"""Turned-part diameter callouts (#138 / ADR 0005, P5b).

External step-diameter callouts for Z-rotational parts, placed beside the
front view (`_annotate_turned_diameters` + helpers). Below annotate in the DAG.
"""

from __future__ import annotations

from build123d_drafting.features import (
    find_bosses,
)
from build123d_drafting.helpers import (
    Leader,
    TitleBlock,
)

from draftwright._core import (
    _DIAM_RE,
    Analysis,
    _axis_letter,
    _fmt,
    _greedy_strip_ys,
    _log,
    _solve_strip_ys,
)
from draftwright.annotations._common import _anno_box, _box_hits, _occupied_boxes


def _mentioned_diams(annotations):
    """Diameters already called out by an annotation — from ø-labels and from
    structured ``covers_diameters`` metadata (e.g. ``HoleCallout``). Mirrors the
    coverage :func:`lint_feature_coverage` checks, so a diameter in this set will
    not lint as ``feature_not_dimensioned``."""
    diams: set = set()
    for ann in annotations:
        if isinstance(ann, TitleBlock):
            continue
        for m in _DIAM_RE.finditer(getattr(ann, "label", None) or ""):
            diams.add(float(m.group(1)))
        for v in getattr(ann, "covers_diameters", ()):
            diams.add(float(v))
    return diams


def _distinct_bosses(bosses, mentioned):
    """One representative boss per distinct external diameter (tallest wins),
    dropping any diameter another annotation already covers (#77)."""
    by_diam: dict = {}
    for b in bosses:
        key = next((k for k in by_diam if abs(k - b.diameter) <= 0.15), b.diameter)
        if key not in by_diam or b.height > by_diam[key].height:
            by_diam[key] = b
    return [b for d, b in by_diam.items() if not any(abs(d - m) <= 0.15 for m in mentioned)]


def _annotate_turned_diameters(dwg, a: Analysis):
    """Leader ø-callouts for external turned step diameters (#77, #131).

    draftwright dimensions holes and, for a Z-rotational part, the OD; the
    external stepped diameters of a turned part lying along X — a peg body, a
    stepped shaft drawn on its side — are otherwise undimensioned and surface
    only as ``feature_not_dimensioned``. This pass places one ø leader per
    distinct external diameter, the thread/worm patches collapsed by
    :func:`find_bosses` into a single boss, below the front-view profile.
    Diameters another annotation already covers are skipped.

    X-axis turning (a shaft drawn on its side) gets a row of callouts below the
    front view; Z-axis turning (a vertical stepped shaft) gets a column to its
    left (#131). Y-axis turning, gear/thread module notes, and axial-length dims
    are out of scope.
    """
    draft = dwg.draft
    try:
        bosses = find_bosses(a.part)
    except Exception as exc:  # noqa: BLE001 — recognition may fail on odd geometry
        _log.info("turned-diameter annotation skipped (%s)", exc)
        return

    mentioned = _mentioned_diams(dwg.items)
    # Z-axis turning (a vertical stepped shaft) gets a column of ø callouts to the
    # left of the front view (#131); X-axis turning keeps the row below (#77).
    _turned_diameters_beside(
        dwg, a, _distinct_bosses([b for b in bosses if _axis_letter(b) == "z"], mentioned)
    )
    todo = _distinct_bosses([b for b in bosses if _axis_letter(b) == "x"], mentioned)
    if not todo:
        return

    # Each callout's label sits in a row below the front view, pulled toward the
    # page-x of its feature; a shared 1D Cassowary solve spreads any that would
    # overlap. This is ADR 0003's layer-2 primitive (_solve_strip_ys reused on
    # the x axis) standing in for the manual pitch stacking the other leaders
    # still use — the first pass to place on the constraint solver (#77).
    fx0, fy0, fx1, _ = dwg.view_bounds("front")  # page bbox of the profile (#28)
    # Drop the row clear of anything already placed below the profile (hole
    # callouts, envelope dims). This is a coarse single-pass guard against the
    # cross-pass overlap a global solve would handle exactly (ADR 0003 / #80):
    # it deconflicts the whole row vertically, not per-label.
    obstacle_bottom = fy0
    for o in dwg.items:
        try:
            ob = o.bounding_box()
        except Exception:  # noqa: BLE001 — not every annotation bbox-es cleanly
            continue
        if ob.min.Y < fy0 and ob.max.X > fx0 and ob.min.X < fx1:
            obstacle_bottom = min(obstacle_bottom, ob.min.Y)
    label_y = obstacle_bottom - (draft.font_size + 4 * draft.pad_around_text)
    # No room below the profile within the page — skip rather than run the row
    # off the sheet. The diameters then surface as feature_not_dimensioned; the
    # escalation ladder (#82) will tabulate instead of dropping.
    if label_y < a.margin + draft.font_size:
        _log.info("turned-diameter callouts skipped (no room below the front view)")
        return

    specs = []  # (tip_page, label) ordered by feature x
    for b in todo:
        mid_x = b.location[0] - b.axis[0] * (b.height / 2)
        tip = dwg.at("front", mid_x, b.location[1], b.location[2] - b.diameter / 2)
        specs.append((tip, f"ø{_fmt(b.diameter)}"))
    specs.sort(key=lambda s: s[0][0])

    half_w = max(len(label) for _, label in specs) * draft.font_size * 0.62 / 2
    min_gap = 2 * half_w + 2 * draft.pad_around_text
    naturals = [tip[0] for tip, _ in specs]
    x_lo, x_hi = fx0 + half_w, fx1 - half_w
    label_xs = _solve_strip_ys(naturals, min_gap, x_lo, x_hi) or _greedy_strip_ys(
        naturals, min_gap, x_lo, x_hi
    )
    if label_xs is None:
        # The labels do not fit the row even greedily; skip rather than crash on
        # a None unpack. They surface as feature_not_dimensioned (#82 tabulates).
        _log.info("turned-diameter callouts skipped (%d will not fit the row)", len(specs))
        return
    for i, ((tip, label), lx) in enumerate(zip(specs, label_xs, strict=True)):
        dwg.add(
            Leader(
                tip=(tip[0], tip[1], 0),
                elbow=(lx, label_y, 0),
                label=label,
                draft=draft,
            ),
            f"ldr_d{i}",
            view="front",
        )


def _turned_diameters_beside(dwg, a: Analysis, todo):
    """ø-callout column to the LEFT of the front view for Z-axis turned (vertical
    stepped) diameters — the page-Y mirror of the #77 row-below (#131)."""
    if not todo:
        return
    draft = dwg.draft
    fx0, fy0, fx1, fy1 = dwg.view_bounds("front")
    # Anchor the ø column just LEFT of the profile — not left of every obstacle.
    # The concentric bore leaders (ldr_z) already own a column further left; the
    # old "left of the leftmost left-obstacle" anchor pushed this column past
    # them and off the page, so a BORED stepped shaft lost ALL its step-diameter
    # callouts (#144). A step sits at its own height, normally clear of the bore
    # leader (which is at the bore's mid-height); the per-label occupancy gate
    # below drops only a step that genuinely collides — place-what-fits, never
    # all-or-nothing. For a non-bored shaft there is no left obstacle, so this is
    # identical to the prior behaviour.
    label_w = max(len(f"ø{_fmt(b.diameter)}") for b in todo) * draft.font_size * 0.62
    elbow_x = fx0 - (draft.font_size + 2 * draft.pad_around_text)
    # No room left of the profile within the page (the view itself abuts the left
    # margin) — skip rather than run off the sheet; the diameters then surface as
    # feature_not_dimensioned.
    if elbow_x - label_w < a.margin:
        _log.info("turned-diameter callouts skipped (no room left of the front view)")
        return
    specs = []  # (tip_page, label) — tip on the step's left silhouette at mid-height
    for b in todo:
        mid_z = b.location[2] - b.axis[2] * (b.height / 2)
        tip = dwg.at("front", b.location[0] - b.diameter / 2, b.location[1], mid_z)
        specs.append((tip, f"ø{_fmt(b.diameter)}"))
    specs.sort(key=lambda s: s[0][1])
    half_h = draft.font_size / 2 + draft.pad_around_text
    min_gap = 2 * half_h
    naturals = [tip[1] for tip, _ in specs]
    y_lo, y_hi = fy0 + half_h, fy1 - half_h
    label_ys = _solve_strip_ys(naturals, min_gap, y_lo, y_hi) or _greedy_strip_ys(
        naturals, min_gap, y_lo, y_hi
    )
    if label_ys is None:
        _log.info("turned-diameter callouts skipped (%d will not fit the column)", len(specs))
        return
    occupied = _occupied_boxes(dwg)  # bore leaders + other left-column callouts
    for i, ((tip, label), ly) in enumerate(zip(specs, label_ys, strict=True)):
        ldr = Leader(tip=(tip[0], tip[1], 0), elbow=(elbow_x, ly, 0), label=label, draft=draft)
        if _box_hits(_anno_box(ldr), occupied):
            # This step's label would overprint a bore leader / existing callout
            # sharing the left region — drop just this one (it surfaces as
            # feature_not_dimensioned), not the whole column.
            continue
        dwg.add(ldr, f"ldr_dz{i}", view="front")
        occupied.append(_anno_box(ldr))
