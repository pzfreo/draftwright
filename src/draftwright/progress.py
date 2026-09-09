"""Headless activity and cooperative cancellation at existing pipeline seams."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from threading import Event
from time import monotonic
from typing import Any, Literal, ParamSpec, TypeVar

_P = ParamSpec("_P")
_R = TypeVar("_R")


@dataclass(frozen=True)
class BuildEvent:
    """An activity observation, not a placement decision or percentage estimate."""

    phase: Literal["started", "finished", "resumed", "retry", "budget", "cancelled", "failed"]
    stage: tuple[str, ...]
    elapsed_seconds: float
    stage_seconds: float
    details: tuple[tuple[str, str | float | int | None], ...] = ()

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "stage": list(self.stage),
            "elapsed_seconds": self.elapsed_seconds,
            "stage_seconds": self.stage_seconds,
            "details": dict(self.details),
        }


class BuildCancelled(KeyboardInterrupt):
    """Cooperative cancellation with a diagnostic and, at publication, a finished result.

    Like KeyboardInterrupt, this must escape optional-geometry ``except Exception``
    fallbacks. ``completed_result`` is supplied only if the public build returned before
    cancellation was observed at publication. Internal search candidates are never exposed.
    It is a normal mutable Drawing, not a frozen snapshot or manufacturing certificate.
    """

    def __init__(self, diagnostic: dict):
        self.diagnostic = diagnostic
        self.completed_result: Any = None
        super().__init__(f"drawing operation cancelled at {' / '.join(diagnostic['stage'])}")


class BuildProgress:
    """Controller yielded by :func:`observe_build`; ``cancel`` is safe from another thread."""

    def __init__(self, observer: Callable[[BuildEvent], None]):
        self._observer = observer
        self._cancelled = Event()
        self._reason = "requested"
        self._started = monotonic()
        self._stack: list[tuple[str, float]] = []
        self._observer_failed = False
        self.latest: BuildEvent | None = None

    def cancel(self, reason: str = "requested") -> None:
        self._reason = reason
        self._cancelled.set()

    def _event(self, phase, **details) -> BuildEvent:
        now = monotonic()
        return BuildEvent(
            phase,
            tuple(name for name, _ in self._stack),
            now - self._started,
            now - self._stack[-1][1] if self._stack else now - self._started,
            tuple(details.items()),
        )

    def _send(self, event: BuildEvent) -> None:
        self.latest = event
        if not self._observer_failed:
            try:
                self._observer(event)
            except Exception:  # noqa: BLE001 — observing must not change the drawing
                self._observer_failed = True
                logging.getLogger(__name__).warning(
                    "progress observer failed; notifications disabled"
                )

    def _cancellation(self, reason: str) -> BuildCancelled:
        event = self._event("cancelled", reason=reason)
        try:
            self._send(event)
        except KeyboardInterrupt:
            pass  # Preserve the original cancellation if its observer is interrupted too.
        return BuildCancelled(event.to_dict())

    def _check(self) -> None:
        if self._cancelled.is_set():
            raise self._cancellation(self._reason)


_CURRENT: ContextVar[BuildProgress | None] = ContextVar("draftwright_progress", default=None)


@contextmanager
def observe_build(observer: Callable[[BuildEvent], None]) -> Iterator[BuildProgress]:
    """Observe build/edit/export activity without changing the drawing policy.

    The callback receives immutable events. Call the yielded controller's ``cancel()`` to
    stop at the next checkpoint, including within repeated leader work. A native CAD call
    must return before a Python checkpoint can run; this is not a hard kernel timeout.
    KeyboardInterrupt inside the context produces the same structured BuildCancelled outcome.
    Results from an earlier build are never attached to a later cancellation.
    """
    state = BuildProgress(observer)
    token = _CURRENT.set(state)
    try:
        yield state
    except BuildCancelled:
        raise
    except KeyboardInterrupt as exc:
        raise state._cancellation("keyboard_interrupt") from exc
    finally:
        _CURRENT.reset(token)


def checkpoint() -> None:
    """Cheap cancellation point inside bounded work; no event allocation when continuing."""
    state = _CURRENT.get()
    if state is not None:
        state._check()


def activity(phase: Literal["retry", "budget"], **details) -> None:
    """Publish an existing retry/budget reason without owning its decision."""
    state = _CURRENT.get()
    if state is not None:
        state._check()
        state._send(state._event(phase, **details))
        state._check()


@contextmanager
def stage(name: str, **details):
    """Observe one existing stage, preserving exceptions and transaction ownership."""
    state = _CURRENT.get()
    if state is None:
        yield
        return
    state._stack.append((name, monotonic()))
    completed = False
    try:
        state._check()
        state._send(state._event("started", **details))
        state._check()
        yield
        state._check()
        state._send(state._event("finished", **details))
        state._check()
        completed = True
    except BuildCancelled:
        raise
    except KeyboardInterrupt as exc:
        raise state._cancellation("keyboard_interrupt") from exc
    except BaseException:
        state._send(state._event("failed", **details))
        raise
    finally:
        state._stack.pop()
        if completed and state._stack:
            state._send(state._event("resumed"))
            state._check()


def build_operation(function: Callable[_P, _R]) -> Callable[_P, _R]:
    """Wrap the public build boundary; retain a result only after its policy returns."""

    @wraps(function)
    def observed(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        if _CURRENT.get() is None:
            return function(*args, **kwargs)
        result = None
        try:
            with stage("build"):
                result = function(*args, **kwargs)
            return result
        except BuildCancelled as exc:
            exc.completed_result = result
            raise

    return observed


def observed_stage(name: str) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Instrument an existing callable without changing its public signature."""

    def decorate(function: Callable[_P, _R]) -> Callable[_P, _R]:
        @wraps(function)
        def observed(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            if _CURRENT.get() is None:
                return function(*args, **kwargs)
            with stage(name):
                return function(*args, **kwargs)

        return observed

    return decorate
