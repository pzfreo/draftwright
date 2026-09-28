"""Measured page blocks shared by sheet selection and late furniture placement.

The preferred gutter is discretionary: failure at that spacing always retries the
existing hard clearance. Neither pass may discard a required block to gain space.
"""

from __future__ import annotations

import textwrap

from draftwright._core import _font_safe_text, _largest_empty_rect
from draftwright.layout import _boxes_overlap, fit_box

PREFERRED_BLOCK_GUTTER = 6.0  # page millimetres, beyond the hard ink clearance


def section_slot_x(usable, side_right, half_w, *, shares_title_row, tb_left):
    """Choose a gracious section gutter only when the original slot still fits."""
    spacious = [
        (lo, hi)
        for lo, hi in usable
        if hi - max(lo, side_right + 16) >= 2 * half_w
        and (not shares_title_row or max(lo, side_right + 16) + 2 * half_w <= tb_left - 4)
    ]
    return (max(spacious[0][0], side_right + 16) if spacious else usable[0][0]) + half_w


def document_note_rows(notes) -> tuple[tuple[str], ...]:
    """One measured text shape for both page planning and rendering."""
    rows = [("GENERAL NOTES",)]
    for index, note in enumerate(notes, 1):
        lines = textwrap.wrap(
            _font_safe_text(note.text),
            width=48,
            break_long_words=False,
            break_on_hyphens=False,
        )
        rows.extend(
            (f"{index}  {line}" if line_index == 0 else f"   {line}",)
            for line_index, line in enumerate(lines or [""])
        )
    return tuple(rows)


def fit_auxiliary_box(size, region, obstacles, prefer, *, clearance, trace=None):
    """Prefer breathing room, then use the exact existing hard fit rule."""
    preferred = max(clearance, PREFERRED_BLOCK_GUTTER)
    if preferred > clearance:
        pos = fit_box(size, region, obstacles, prefer, clearance=preferred)
        if pos is not None:
            return pos
    return fit_box(size, region, obstacles, prefer, clearance=clearance, trace=trace)


def detail_space(drawable, obstacles, *, minimum_size, desired_size):
    """Choose a spacious detail region only if it retains the requested scale.

    The existing empty-rectangle choice is the hard fallback. Inflating obstacles
    only for the first attempt means a cosmetic gutter cannot make a detail drop
    or reduce its otherwise achievable scale.
    """
    gap = PREFERRED_BLOCK_GUTTER
    padded = [(x0 - gap, y0 - gap, x1 + gap, y1 + gap) for x0, y0, x1, y1 in obstacles]
    candidate = _largest_empty_rect(drawable, padded, target_size=minimum_size, warn=False)
    if (
        not any(_boxes_overlap(candidate, obstacle) for obstacle in padded)
        and candidate[2] - candidate[0] >= desired_size[0]
        and candidate[3] - candidate[1] >= desired_size[1]
    ):
        return candidate
    return _largest_empty_rect(drawable, obstacles, target_size=minimum_size, warn=False)
