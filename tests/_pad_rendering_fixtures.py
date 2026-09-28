"""Shared signed-pad geometry for rendering and provider-boundary contracts."""

from build123d import Align, Box, Location


def _box(size, position):
    return Box(*size, align=(Align.MIN, Align.MIN, Align.MIN)).moved(Location(position))


def _signed_pad(axis: str, direction: int):
    """One asymmetric pad occurrence with a five-millimetre signed attachment span."""
    if axis == "z":
        body_pos = (0, 0, 0 if direction > 0 else 5)
        pad_pos = (5, 8, 10 if direction > 0 else 0)
        return _box((40, 30, 10), body_pos) + _box((15, 10, 5), pad_pos)
    if axis == "x":
        body_pos = (0 if direction > 0 else 5, 0, 0)
        pad_pos = (10 if direction > 0 else 0, 8, 5)
        return _box((10, 30, 40), body_pos) + _box((5, 10, 15), pad_pos)
    body_pos = (0, 0 if direction > 0 else 5, 0)
    pad_pos = (5, 10 if direction > 0 else 0, 8)
    return _box((40, 10, 30), body_pos) + _box((15, 5, 10), pad_pos)
