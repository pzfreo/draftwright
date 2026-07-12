"""Model-neutral geometry primitives — the leaf below both ``_core`` and ``model``.

These four helpers read an axis / coordinate / position off a build123d object
(or the IR) and carry no drawing, layout or page knowledge. They live here, not
in :mod:`draftwright._core`, so the IR waist (:mod:`draftwright.model`) can use
them without importing the stage-level drawing grab-bag (ADR 0008; #584 WP2).
This module imports nothing from ``draftwright`` — it is the bottom of the DAG.
"""

from __future__ import annotations

from dataclasses import dataclass

# Axis letter -> the orthographic view a feature on that axis reads end-on in.
_END_ON = {"x": "side", "y": "front", "z": "plan"}


def _xyz(loc) -> tuple[float, float, float]:
    """A build123d ``Vector`` (has ``.X/.Y/.Z``) or an ``(x, y, z)`` sequence → an
    ``(x, y, z)`` float tuple. Shared by the detectors and the lint coverage checks
    so the Vector-unpacking idiom lives in one place."""
    if hasattr(loc, "X"):
        return (loc.X, loc.Y, loc.Z)
    x, y, z = loc
    return (float(x), float(y), float(z))


@dataclass(frozen=True)
class HoleRef:
    """A position-keyed reference to a hole — the IR-typed value the cover / hole-table
    bookkeeping matches on, so the shared escalation never needs a recogniser ``Hole``
    object (ADR 0008 Amendment 6). Built from any location via :meth:`of` (rounded, so
    two references at the same position compare equal)."""

    x: float
    y: float
    z: float

    @classmethod
    def of(cls, loc) -> HoleRef:
        x, y, z = _xyz(loc)
        return cls(round(x, 3), round(y, 3), round(z, 3))


def _axis_letter(obj) -> str:
    """Letter (``"x"``/``"y"``/``"z"``) of ``obj.axis``'s dominant component.

    ``obj`` is anything carrying an ``.axis`` 3-vector (a hole or a boss).
    """
    return max(zip("xyz", obj.axis, strict=True), key=lambda t: abs(t[1]))[0]
