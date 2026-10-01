"""Unmodified sheet presentation inputs passed through the build pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SheetMetadata:
    """One immutable carrier for authored title, display, and border options.

    The public front doors own defaults and validation. This record retains the
    caller's values so analysis can pass them to their established owners.
    """

    title: Any
    number: Any
    tolerance: Any
    drawn_by: Any
    material: Any
    date: Any
    revision: Any
    company: Any
    approved_by: Any
    document_type: Any
    sheet: Any
    frame: bool
    projection: str | None
    projection_symbol: bool
    text_position: str
    text_orientation: str
    leader_region: str
    zones: bool
    margin_left: float | None
    margin_right: float | None
    margin_top: float | None
    margin_bottom: float | None
    title_block_width: float | None
