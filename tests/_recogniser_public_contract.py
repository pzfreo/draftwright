"""Test helpers for the released public b123d-recognisers record contract."""

from __future__ import annotations

import dataclasses
import inspect
import types
import typing
from collections.abc import Callable, Iterable

import b123d_recognisers as recognition


class _PublicRecogniserRoot:
    """Read-only live access restricted to the root names published at construction."""

    __slots__ = ("_names", "_root")

    def __init__(self, module: types.ModuleType) -> None:
        object.__setattr__(self, "_names", frozenset(module.__all__))
        object.__setattr__(self, "_root", types.MappingProxyType(module.__dict__))

    def __getattribute__(self, name: str) -> object:
        if name in {"_names", "_root"}:
            raise AttributeError(name)
        return object.__getattribute__(self, name)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("public recogniser access is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("public recogniser access is immutable")

    def names(self) -> frozenset[str]:
        return object.__getattribute__(self, "_names")

    def __call__(self, name: str) -> object:
        if name not in object.__getattribute__(self, "_names"):
            raise KeyError(name)
        return object.__getattribute__(self, "_root")[name]


_public_recogniser_root = _PublicRecogniserRoot(recognition)
public_recogniser_names: Callable[[], frozenset[str]] = _public_recogniser_root.names
public_recogniser_member: Callable[[str], object] = _public_recogniser_root
del _public_recogniser_root
del _PublicRecogniserRoot
del recognition


def _is_record_like_class(value: object) -> bool:
    """Whether *value* has the released record shape, independent of publication."""

    if not isinstance(value, type):
        return False
    params = getattr(value, "__dataclass_params__", None)
    return bool(
        dataclasses.is_dataclass(value)
        and params is not None
        and params.frozen
        and callable(getattr(value, "to_dict", None))
    )


def is_public_record_class(value: object) -> bool:
    """Whether *value* is a record class published at the package root."""

    if not _is_record_like_class(value):
        return False
    name = getattr(value, "__name__", "")
    return name in public_recogniser_names() and public_recogniser_member(name) is value


def _nested_record_like_classes(annotation: object) -> set[type]:
    """Find structurally record-like classes anywhere in an annotation."""

    if _is_record_like_class(annotation):
        assert isinstance(annotation, type)
        return {annotation}
    found: set[type] = set()
    for member in typing.get_args(annotation):
        found |= _nested_record_like_classes(member)
    return found


def public_record_return_types(annotation: object, *, source: str) -> set[type]:
    """Validate and return the records in one public emitter's return annotation.

    Non-record returns are outside this contract and contribute nothing. Once a public record
    occurs anywhere in the annotation, however, ADR 3 (was 0013)'s emitter grammar is strict: the
    top-level shape is ``list[...]`` and every list member is a public record class.
    """

    nested = _nested_record_like_classes(annotation)
    if not nested:
        return set()

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    assert origin is list and len(args) == 1, (
        f"{source} returns public record(s) in {annotation!r}; expected list[PublicRecord]"
    )
    element = args[0]
    element_origin = typing.get_origin(element)
    members = (
        typing.get_args(element)
        if element_origin in (typing.Union, types.UnionType)
        else (element,)
    )
    invalid = [member for member in members if not is_public_record_class(member)]
    assert not invalid, (
        f"{source} has non-public-record list member(s) {invalid!r} in {annotation!r}"
    )
    return set(members)


def public_record_universe(
    *,
    names: Iterable[str] | None = None,
    member: Callable[[str], object] | None = None,
) -> set[type]:
    """Every record type returned by every released public package function."""

    universe: set[type] = set()
    published_names = public_recogniser_names() if names is None else names
    get_member = public_recogniser_member if member is None else member
    for name in published_names:
        fn = get_member(name)
        # Python 3.10 reports parameterised ``typing.Union`` aliases as callable.
        # Root publication includes contract aliases such as ``FramedEvidence``;
        # they are not emitters and therefore have no return hints to inventory.
        # Exclude typing aliases specifically: a callable-object decorator can
        # still be a real public emitter and must remain inside the fail-closed
        # return-grammar census.
        if inspect.isclass(fn) or not callable(fn) or typing.get_origin(fn) is not None:
            continue
        try:
            hints = typing.get_type_hints(fn)
        except Exception as exc:
            raise AssertionError(
                f"could not resolve return hints for recognition.{name}: {exc!r}"
            ) from exc
        found = public_record_return_types(hints.get("return"), source=f"recognition.{name}")
        if name.startswith(("recognise_", "project_")) or name == "step_level_records":
            assert found, (
                f"recognition.{name} has no public-record return annotation "
                f"(got {hints.get('return')!r})"
            )
        universe |= found
    return universe
