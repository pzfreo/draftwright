"""Shared annotation-placement helpers (#138 / ADR 0005, P5).

Page-box geometry the passes share: an annotation's bbox (`_anno_box`), the
complete strip occupancy (`strip_obstacles`), and an AABB overlap test
(`_box_hits`). Bottom of the annotations DAG.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from build123d_drafting.helpers import DEFAULT_FONT_PATH, Dimension, SafeDimension

from draftwright._core import _anno_box, _text_size  # noqa: F401 — _anno_box re-exported (#700)
from draftwright._geometry import _boxes_overlap, _segment_crosses_box  # noqa: F401
from draftwright.layout import StripCandidate, plan_strip
from draftwright.linting.issues import LintIssue
from draftwright.linting.structural import _centerline_extent

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Escalation:
    """A first-class "could not place this here" signal (ADR 0009 Amendment 1, P5-strand-2).

    Placers *collect* one of these into the run's ``PlacementContext.escalations`` at the point
    of failure —
    instead of recording a stringly-typed ``*_dropped`` lint code and letting the escalators
    grep for it — and one later resolver pass groups them by ``(view, feature-or-pattern)``,
    picks a remedy per group (ISO pattern-grouped balloon / table / detail / drop), and emits
    the ``*_dropped`` lint codes only for what stays unresolved (so coverage lint + the
    cleanliness ratchet keep working). See the ADR / epic #351.

    The hole callout/location placers emit these (#351 PR-2); the resolver in
    ``annotations/orchestrator.py`` (``_maybe_tabulate_holes``) consumes them, including
    the ISO pattern-grouped balloon fallback for a dropped pattern callout (#351 PR-3).

    Attributes:
        kind:     what could not be placed — ``"callout" | "location" | "slot" | "step" | "pmi"``.
        view:     the owning orthographic view (``None`` for drawing-level).
        feature:  reference to the IR feature / ``HoleRef`` / key it belongs to — carries the
                  pattern membership the resolver groups on (a ``"callout"`` escalation's
                  feature is the dropped group's ``PatternFeature`` when it is a
                  fully-surviving recognised pattern, else ``None``). Left untyped to keep
                  this module a leaf (no dependency on ``model.ir``).
        reason:   why placement failed — ``"strip_full" | "illegible" | "corridor_blocked" | "no_room"``.
        remedies: ranked candidate remedies the resolver may pick, e.g.
                  ``("group_balloon", "table", "detail", "drop")``. Empty = resolver's default ladder.
    """

    kind: str
    view: str | None
    feature: object
    reason: str
    remedies: tuple[str, ...] = field(default_factory=tuple)


class SolveTrace:
    """The opt-in solve-trace recorder (#736): one JSON file per build explaining every
    strip placement decision — the #733 post-mortem's "why is this strip full" answer
    as a glance instead of a custom script.

    Activated by ``build_drawing(trace=...)`` or the ``DRAFTWRIGHT_TRACE`` env var
    (see :func:`draftwright.builder.build_drawing`); threaded onto each run's
    :class:`PlacementContext` (``ctx.trace``) so both the auto-annotate and finalize
    paths trace. **Default off, and off means nil cost**: every hook site is a plain
    ``trace is None`` check — no dict is ever built.

    JSON shape (``version`` 2): ``{"version", "solves": [...], "pass_events": [...],
    "escalations": [...]}`` — two DISTINCT record types, not one masquerading as the
    other:

    * ``solves`` — the corridor solves. Each entry carries ``seq`` (a global event
      counter shared with ``pass_events``, so the build's decision order is
      reconstructable), ``phase`` (``auto:N``/``finalize:N`` — one per annotate run),
      ``corridor`` (the ``[view, side]`` key), ``view``/``axis``/``tier``, the
      ``strip`` bounds (anchor/outer_limit/direction/gap/spacing), the full candidate
      set (name, order, priority, size, force, anchored, dedup, precedence), the
      placement ``passes`` (per pass: the carved span, the in-band obstacles with
      their owning annotation names, the free segments, placed positions, rejections
      with reasons, unplaced leftovers), and per-candidate ``outcomes`` (placed /
      dropped-with-reason / deduped / promoted / deferred-to-post-drain).
    * ``pass_events`` — everything placed OUTSIDE a corridor solve: the standalone
      strip passes (slot fallthroughs, front hole callouts) and the *immediate*
      placers — the post-drain machined-feature leader callouts
      (chamfer/fillet/flat/pocket/groove/boss ø) and the turned diameter row/column
      and step-length set-solves. Each entry carries ``seq``/``phase``, a pass
      ``label``, and ``items`` — one outcome dict per attempted annotation
      (``placed`` with its position, or ``dropped`` with a reason). The #733 gap:
      pre-#734 these callouts were the drain-time occupants; post-drain, their own
      story must still be in the trace.

    The ``jq`` contract: ``.solves[].outcomes[]`` for corridor dims,
    ``.pass_events[].items[]`` for everything else::

        jq '.solves[].outcomes[] | select(.name == "dim_height")' t.trace.json
        jq '.pass_events[] | select(.label == "pocket_callouts") | .items[]' t.trace.json

    Recording-only: an unwritable path degrades to a logged warning (:meth:`write`
    never aborts a build), and :meth:`snapshot`/:meth:`restore` let ``finalize()``'s
    #647 transaction roll a failed drain's records back out of the trace.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.solves: list[dict] = []
        self.pass_events: list[dict] = []
        self.escalations: list[dict] = []
        self._phase = ""
        self._phase_n = 0
        self._seq = 0
        self._current: dict | None = None

    def _next_seq(self) -> int:
        """One global event counter across solves + pass_events (decision order)."""
        self._seq += 1
        return self._seq - 1

    def snapshot(self):
        """The trace's rollback point for finalize's #647 transaction: capture the
        record counts + phase/seq counters so :meth:`restore` can truncate a failed
        drain's records — a rolled-back finalize must not leave trace entries
        describing placements that no longer exist."""
        return (
            len(self.solves),
            len(self.pass_events),
            len(self.escalations),
            self._seq,
            self._phase,
            self._phase_n,
        )

    def restore(self, snap) -> None:
        """Roll the trace back to *snap* (see :meth:`snapshot`)."""
        n_solves, n_events, n_esc, seq, phase, phase_n = snap
        del self.solves[n_solves:]
        del self.pass_events[n_events:]
        del self.escalations[n_esc:]
        self._seq = seq
        self._phase = phase
        self._phase_n = phase_n
        self._current = None

    def begin_phase(self, label) -> None:
        """Start a new annotate run (``auto``) / finalize drain (``finalize``); each
        run's solves are labelled ``<label>:<n>`` so a measure-and-repack build keeps
        its passes apart."""
        self._phase_n += 1
        self._phase = f"{label}:{self._phase_n}"

    @staticmethod
    def _strip_rec(strip):
        if strip is None:
            return None
        return {
            "anchor": strip.anchor,
            "outer_limit": strip.outer_limit,
            "direction": strip.direction,
            "gap": strip.gap,
            "spacing": strip.spacing,
        }

    def begin_solve(self, key, view, axis, tier, strip, cands) -> None:
        """Open a corridor-solve record (called by :func:`solve_corridor`)."""
        self._current = {
            "seq": self._next_seq(),
            "phase": self._phase,
            "corridor": list(key) if key is not None else None,
            "view": view,
            "axis": axis,
            "tier": tier,
            "strip": self._strip_rec(strip),
            "candidates": [
                {
                    "name": c.name,
                    "order": list(c.order),
                    "priority": c.priority,
                    "size": list(c.size) if c.size is not None else None,
                    "force": c.force,
                    "anchored": c.anchored,
                    "dedup": list(c.dedup) if c.dedup is not None else None,
                    "precedence": c.precedence,
                }
                for c in cands
            ],
            "passes": [],
            "outcomes": [],
        }
        self.solves.append(self._current)

    def end_solve(self) -> None:
        self._current = None

    def begin_pass(self, *, force, label=None, strip=None, view=None, axis=None) -> dict:
        """Open a placement-pass record (called by :func:`place_strip_candidates`) —
        nested under the open corridor solve, or a standalone ``pass_events`` entry
        (with *label*) for a pass-local strip placement outside any corridor."""
        rec: dict = {
            "force": force,
            "obstacles": [],
            "free_segments": [],
            "placed": [],
            "rejected": [],
            "unplaced": [],
        }
        if self._current is not None:
            self._current["passes"].append(rec)
        else:
            rec = {
                "seq": self._next_seq(),
                "phase": self._phase,
                "label": label,
                "view": view,
                "axis": axis,
                "strip": self._strip_rec(strip),
                **rec,
                "items": [],
            }
            self.pass_events.append(rec)
        return rec

    def end_pass(self, rec) -> None:
        """Close a placement-pass record. For a standalone ``pass_events`` entry the
        per-candidate story is folded into ``items`` (the jq contract:
        ``.pass_events[].items[]``): each placed candidate with its position, each
        leftover as ``dropped`` with its last rejection reason (default
        ``strip_full``). A pass nested under a corridor solve keeps its raw
        placed/unplaced lists — the solve's ``outcomes`` carry the summary there."""
        if "items" not in rec:
            return
        reasons = {e["name"]: e["reason"] for e in rec["rejected"]}
        rec["items"] = [
            {"name": e["name"], "outcome": "placed", "pos": e["pos"]} for e in rec.pop("placed")
        ] + [
            {"name": n, "outcome": "dropped", "reason": reasons.get(n, "strip_full")}
            for n in rec.pop("unplaced")
        ]

    def pass_event(self, label, **fields) -> dict:
        """Open a ``pass_events`` record for an *immediate* placer (the #733 gap: the
        post-drain machined-feature callouts and the turned diameter/step-length
        set-solves place outside any strip solve, but their story must be in the
        trace too). Returns the record; the caller appends one outcome dict per
        attempted annotation to ``rec["items"]``."""
        rec = {
            "seq": self._next_seq(),
            "phase": self._phase,
            "label": label,
            **fields,
            "items": [],
        }
        self.pass_events.append(rec)
        return rec

    def record_outcome(self, name, outcome, **extra) -> None:
        """Record a candidate's solve-level outcome; a ``placed`` outcome is enriched
        with its position and a reason-less ``dropped`` with the last recorded
        rejection reason (default ``strip_full``) from this solve's passes."""
        if self._current is None:
            return
        rec: dict = {"name": name, "outcome": outcome, **extra}
        if outcome == "placed":
            for p in self._current["passes"]:
                for e in p["placed"]:
                    if e["name"] == name:
                        rec["pos"] = e["pos"]
        elif outcome == "dropped" and "reason" not in rec:
            reason = "strip_full"
            for p in self._current["passes"]:
                for e in p["rejected"]:
                    if e["name"] == name:
                        reason = e["reason"]
            rec["reason"] = reason
        self._current["outcomes"].append(rec)

    def record_escalations(self, escalations) -> None:
        """Snapshot the run's :class:`Escalation` list (called once per annotate run)."""
        for e in escalations:
            self.escalations.append(
                {
                    "phase": self._phase,
                    "kind": e.kind,
                    "view": e.view,
                    "reason": e.reason,
                    "feature": type(e.feature).__name__ if e.feature is not None else None,
                }
            )

    def write(self) -> None:
        """Dump the trace JSON to :attr:`path` (once per build; a successful finalize
        re-writes). **Recording-only, so it must never abort a build**: an unwritable
        path degrades to a logged warning, and the rewrite is atomic
        (``<path>.tmp`` then :func:`os.replace`) so a reader never sees a torn file.
        Serialisation is strict (no ``default=``): a non-JSON-native field is a
        recorder bug and should fail tests visibly, not be papered over."""
        data = {
            "version": 2,
            "solves": self.solves,
            "pass_events": self.pass_events,
            "escalations": self.escalations,
        }
        text = json.dumps(data, indent=1)
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            _log.warning("trace: could not write %s: %s", self.path, exc)


def _geom_box(o):
    """Full rendered-geometry bbox ``(x0, y0, x1, y1)`` of an annotation — leader
    shafts and arrow tips, dimension witness/extension lines, centrelines, hatch —
    *not* just its label box. ``None`` if it does not bbox cleanly (logged at
    debug: a silently dropped occupant is the wrong failure mode for an occupancy
    model, so the omission is at least observable)."""
    try:
        b = o.bounding_box()
        return (b.min.X, b.min.Y, b.max.X, b.max.Y)
    except Exception as exc:  # noqa: BLE001 — not every annotation bbox-es cleanly
        _log.debug("strip occupancy: %s did not bbox (%s); omitted", type(o).__name__, exc)
        return None


def dim_footprint(p1, p2, side, distance, draft, label):
    """Analytical page-mm AABB ``(x0, y0, x1, y1)`` of the :class:`Dimension` that
    ``_dim(p1, p2, side, distance, draft, label=label)`` would build — WITHOUT
    constructing any OCC geometry (#602: a rejected candidate must not pay the
    rendering cost).

    Mirrors ``helpers.Dimension`` / build123d ``ExtensionLine``: each extension line
    is the object→dimension-line segment *translated* ``extension_gap`` along itself,
    so it spans ``p + side·gap`` → ``p + side·(distance + gap)`` — starting ``gap``
    clear of the object and overshooting the dimension line by the same ``gap``. The
    measured label is centred on the line's midpoint with its extents swapped for a
    vertical measured segment (the label is rotated); everything strokes at
    ``line_width``. With inside arrows the heads lie within the extension-line
    overshoot (at preset sizes), so they add nothing to the hull.

    Tight spans mirror helpers ≥0.14's outside-arrows flip (``_dim_line_ink``):
    when the label and both heads don't fit — ``w + 2·al ≥ length`` or the shaft
    piece beside a head would vanish — the ink extends ``2·arrow_length`` past
    each end along the line. The label itself stays centred regardless of width
    (``Dimension`` always passes an explicit ``label_t``, so the hang branch
    never runs), and when its keep-clear reaches a witness end the witness's
    overshoot past the dimension line is cut away with it. Without this model
    the estimate under-covers exactly the dims v0.14 widens, and the
    accept-time rebuild fails validation until the candidate is dropped (the
    declared-plate 8 mm thickness dim was the first casualty). Assumes the
    centred label (``label_offset_x=0``), like the rest of the estimate.
    Callers accepting a candidate off this footprint must still build the real
    geometry once and re-validate its box (the #602 validation fallback) so any
    residual mismatch degrades to a wasted probe, never a collision.
    """
    if isinstance(side, str):  # mirror helpers._SIDE_VECTORS ("above"/"below"/"left"/"right")
        side = {
            "above": (0.0, 1.0, 0.0),
            "below": (0.0, -1.0, 0.0),
            "left": (-1.0, 0.0, 0.0),
            "right": (1.0, 0.0, 0.0),
        }[side]
    sx, sy = side[0], side[1]
    off = abs(distance)
    gap = draft.extension_gap
    dxp, dyp = p2[0] - p1[0], p2[1] - p1[1]
    # Mirror the renderer's font resolution exactly (helpers._font_path): the pinned
    # font *file* when set, else the helpers default; an explicit font_path=None means
    # name-based resolution of draft.font — so pass the name through too.
    w, h = _text_size(
        label,
        draft.font_size,
        getattr(draft, "font_path", DEFAULT_FONT_PATH),
        getattr(draft, "font", "Arial"),
    )
    hx, hy = (h / 2.0, w / 2.0) if abs(dyp) > abs(dxp) else (w / 2.0, h / 2.0)
    lcx = (p1[0] + p2[0]) / 2.0 + sx * off
    lcy = (p1[1] + p2[1]) / 2.0 + sy * off
    far = off + gap
    xs = [p1[0] + sx * gap, p2[0] + sx * gap, lcx - hx, lcx + hx]
    ys = [p1[1] + sy * gap, p2[1] + sy * gap, lcy - hy, lcy + hy]
    # Helpers ≥0.14 tight-span behaviour. Along the line: the label's half-extent
    # is w/2 regardless of orientation (the text rotates with the line); outside
    # arrows extend the ink 2·al past each end when label+heads don't fit. Along
    # the witness: the label keep-clear (h/2 + pad either side of the line) is
    # cut out of a witness the centred label reaches, removing its overshoot
    # unless a stub past the keep-clear survives (gap > h/2 + pad).
    length = math.hypot(dxp, dyp)
    al = getattr(draft, "arrow_length", 0.9 * draft.font_size)
    tpad = getattr(draft, "pad_around_text", 0.0)
    fits = length > 0 and (w + 2.0 * al < length) and (length / 2.0 - w / 2.0 - tpad > al / 2.0)
    if not fits and length > 0:
        ux, uy = dxp / length, dyp / length
        xs += [p1[0] + sx * off - ux * 2.0 * al, p2[0] + sx * off + ux * 2.0 * al]
        ys += [p1[1] + sy * off - uy * 2.0 * al, p2[1] + sy * off + uy * 2.0 * al]
    label_covers_witness = length > 0 and length / 2.0 < w / 2.0 + tpad
    if not label_covers_witness or gap > h / 2.0 + tpad:
        xs += [p1[0] + sx * far, p2[0] + sx * far]
        ys += [p1[1] + sy * far, p2[1] + sy * far]
    pad = draft.line_width / 2.0
    return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


CROSSABLE_TYPES = frozenset({"Centerline", "CenterlineCircle", "CenterMark"})
"""Annotation types a *dimension* may legitimately cross (ISO 128): centre lines
and centre marks. A **leader**, by contrast, must avoid them (#305) — so this is a
per-consumer choice, passed as ``crossable`` to :func:`strip_obstacles`."""


def clear_label_of_centerlines(label_bbox, centerlines, gap):
    """``label_offset_x`` so *label_bbox* clears every crossing centre-line-family
    annotation in *centerlines* (#129) — both a turned part's thin vertical/
    horizontal axis :class:`Centerline` and a bolt-circle's wide
    :class:`CenterlineCircle`.

    Two steps. First, decide whether the label's UNSHIFTED position is already
    fine: a crossing only counts past 0.5 mm of depth (a thin line, off its
    midpoint) or overlap (a wide bbox) — the same threshold
    :func:`draftwright.linting.structural.lint_drawing`'s
    ``label_centerline_overlap`` flags — so a marginal graze the lint would not
    flag leaves the label untouched. If nothing crosses by that measure, return
    0.0 without moving anything.

    Otherwise, every centre line the label's row (Y-extent) genuinely reaches —
    *regardless of its own individual X depth* — becomes one forbidden interval
    for the label's LEFT edge (`x`: the label occupies `[x, x+width]`, so it
    clears a crossing's `[c0, c1]` by *gap* exactly when `x` is outside
    `(c0-gap-width, c1+gap)`), carved from a generous span via
    :func:`carve_free_segments` — the same occupancy-carve primitive this
    module's other placers use for the dimension *line* itself. Including every
    reachable centre line here, not just the ones individually past the 0.5 mm
    threshold, matters: once the label is going to move at all, a centre line
    it barely grazed at the ORIGINAL position can end up squarely inside the
    NEW one — this joint carve accounts for all of them in one pass, so moving
    to clear one can never expose a violation against another (the bug class
    an earlier per-centre-line local-search design had, #129 second review). A
    thin **horizontal** line can't be cleared by an X shift at all, so it is
    excluded and left to the lint/repair safety net."""
    if label_bbox is None:
        return 0.0
    lmin_x, lmin_y, lmax_x, lmax_y = label_bbox
    label_w = lmax_x - lmin_x
    extents = []
    natural_violation = False
    for cl in centerlines:
        if not getattr(cl, "is_centerline", False):
            continue
        try:
            cl_min_x, cl_min_y, cl_max_x, cl_max_y = _centerline_extent(cl)
        except Exception:
            continue
        if cl_max_y - cl_min_y < 0.1:
            continue  # a horizontal line's clash can't be fixed by an X shift
        oy = min(lmax_y, cl_max_y) - max(lmin_y, cl_min_y)
        if oy <= 0.5:
            continue  # no real vertical overlap — matches the lint's own oy>0.5 gate
        extents.append((cl_min_x, cl_max_x))
        if cl_max_x - cl_min_x < 0.1:
            cl_x = (cl_min_x + cl_max_x) / 2.0
            ox = min(cl_x - lmin_x, lmax_x - cl_x) if lmin_x < cl_x < lmax_x else 0.0
        else:
            ox = min(lmax_x, cl_max_x) - max(lmin_x, cl_min_x)
        natural_violation = natural_violation or ox > 0.5
    if not natural_violation:
        return 0.0  # already clear enough that the lint would not flag it
    forbidden = [(cl_min_x - gap - label_w, cl_max_x + gap) for cl_min_x, cl_max_x in extents]
    # A span this wide beyond every forbidden edge is free even if every interval
    # merged into one contiguous run (their combined width can never exceed the
    # sum of the individual widths) — carve_free_segments is therefore guaranteed
    # a non-empty result; no defensive empty-result fallback needed.
    total_w = sum(f1 - f0 for f0, f1 in forbidden) + label_w + 10.0
    lo = min([lmin_x, *(f0 for f0, _ in forbidden)]) - total_w
    hi = max([lmax_x, *(f1 for _, f1 in forbidden)]) + total_w
    segs = carve_free_segments(lo, hi, forbidden, 0.0)
    target_x = min((min(max(lmin_x, s0), s1) for s0, s1 in segs), key=lambda x: abs(x - lmin_x))
    return target_x - lmin_x


def occupancy_boxes(o, stroke_pad=None):
    """*o*'s occupancy as a list of AABBs — decomposed, not one hull (#685).

    An annotation's rendered hull includes large EMPTY corner regions (a dimension's
    ink is L-shaped: witness lines + a dim-line band), and helpers ≥0.14's honest
    tight-span rendering made those hulls big enough to collide structurally at view
    corners while the inks stay disjoint. For an annotation exposing ``.segments``
    (helpers ≥0.14 reports the drawn line pieces: witness lines, shafts, box
    strokes), return one box per stroke — inflated by ``_STROKE_PAD`` to cover line
    width plus the arrowheads at stroke junctions — plus its ``label_bbox``.
    Anything else (leaders, hatch, title block) keeps its single hull box.

    Consumers treat the result exactly like a list of hull boxes; the
    perpendicular-band filter in the carve then does the rest — a witness sliver
    carves only its own sliver, and an out-of-band dim-line band stops blocking a
    sibling strip's corner entirely.
    """
    segs = getattr(o, "segments", None)
    if not segs:
        b = _geom_box(o)
        return [b] if b is not None else []
    pad = _STROKE_PAD if stroke_pad is None else stroke_pad
    out = []
    for (x0, y0), (x1, y1) in segs:
        out.append(
            (
                min(x0, x1) - pad,
                min(y0, y1) - pad,
                max(x0, x1) + pad,
                max(y0, y1) + pad,
            )
        )
    lb = getattr(o, "label_bbox", None)
    if lb is not None:
        out.append((lb[0], lb[1], lb[2], lb[3]))
    return out


# Fallback stroke inflation for decomposed occupancy: line_width/2 (~0.08) plus the
# arrowhead half-width at stroke junctions at DEFAULT presets. Arrow geometry scales
# with font_size (#688 review), so callers that know the draft derive the pad as
# max(_STROKE_PAD, draft.arrow_length / 2) — arrow half-LENGTH bounds the head's
# half-width (aspect < 1) AND its protrusion past an inside-arrow shaft trim (al/2).
_STROKE_PAD = 1.2


def strip_obstacles(dwg, view=None, *, crossable=(), named=False):
    """The COMPLETE occupancy for strip placement (ADR 0009): every placed
    annotation's full rendered footprint, optionally restricted to *view*, minus
    any annotation whose type name is in *crossable* (things this particular
    consumer may legitimately overlap — e.g. a location dim crosses a centre line
    but a leader does not; see :data:`CROSSABLE_TYPES`).

    With *named* (the #736 trace/diagnosis flavour) each box comes back as an
    ``(owner-name, box)`` pair — the same boxes, tagged with the annotation name
    they decompose from — so a trace or a "what filled this strip" message can
    attribute the occupancy. Default off: the hot placement path carries bare
    boxes, unchanged.

    Unlike the retired label-box-only ``_occupied_boxes`` (which excluded bare
    centrelines), this captures the geometry a label box hides — leader shafts and
    arrow tips, dimension witness/extension lines, centrelines, and the section
    hatch. That hidden geometry is the 'invisible occupant' class behind the
    recurring strip overlaps (#133/#225/#305): a placer that consults only label
    boxes commits a callout into space a leader or extension line already crosses.

    *view* scoping keeps this view's own annotations **and** drawing-level obstacles
    that no orthographic view owns (the section hatch, title block, …) — those a
    strip placer must still avoid — and drops only the *other* ortho views' blocks
    (which compose-then-pack keeps disjoint, ADR 0004). The section hatch
    (``view_of`` ``None``) is therefore present in every per-view query, the way
    ``_occupied_boxes`` special-cased it; restricting it to ``view=None`` would
    re-open the very blind spot this closes.

    Boxes are AABBs ``(x0, y0, x1, y1)`` (use with :func:`_box_hits`) — intentionally
    conservative: a diagonal leader's box over-claims its empty triangle (ADR 0009
    notes angled leaders weaken the bound), which only ever over-avoids, never
    under-avoids.

    The occupancy source for the collect-then-solve carve — every migrated renderer's
    ``place_strip_candidates`` call wires this in (#321/#150/P3)."""
    # Preset-aware stroke pad (#688 review): arrowheads scale with font_size.
    al = getattr(getattr(dwg, "draft", None), "arrow_length", None)
    pad = max(_STROKE_PAD, al / 2) if al else _STROKE_PAD
    boxes: list = []
    for name, o in dwg.iter_annotations():
        if view is not None:
            owner = dwg.view_of(name)
            if owner is not None and owner != view:
                continue  # owned by a different ortho view → its own (disjoint) block
        if type(o).__name__ in crossable:
            continue  # this consumer may cross it (centre lines/marks for a dim)
        occ = occupancy_boxes(o, stroke_pad=pad)  # decomposed, not one hull (#685)
        boxes.extend(((name, b) for b in occ) if named else occ)
    return boxes


def strip_occupants(dwg, strip, view, axis, limit=3):
    """The names of the annotations whose footprints occupy *strip*'s free span,
    ranked by covered stacking-axis extent (largest first; ties by name) — the
    "what filled this strip" answer the #736 enriched drop message and the solve
    trace share. ``[]`` when the strip is absent or nothing overlaps it."""
    if strip is None:
        return []
    lo, hi, _inner = strip_free_span(strip)
    idx = 1 if axis == "y" else 0
    cover: dict[str, float] = {}
    for name, box in strip_obstacles(dwg, view=view, crossable=CROSSABLE_TYPES, named=True):
        ov = min(hi, box[idx + 2]) - max(lo, box[idx])
        if ov > 0:
            cover[name] = cover.get(name, 0.0) + ov
    return sorted(cover, key=lambda n: (-cover[n], n))[:limit]


def full_strip_message(base, dwg, strip, view, axis):
    """Extend a ``"…strip full)"`` drop message with the top occupant names (#736):
    ``"…strip full; occupied by: dim_a, ldr_b)"`` — so a placement_unsatisfiable
    drop names what filled the strip instead of demanding a custom-script rebuild
    (the #733 diagnosis). Returns *base* unchanged when no occupant is known."""
    occ = strip_occupants(dwg, strip, view, axis)
    if not occ:
        return base
    who = ", ".join(occ)
    if base.endswith(")"):
        return f"{base[:-1]}; occupied by: {who})"
    return f"{base} (occupied by: {who})"


def strip_free_span(strip):
    """``(lo, hi, inner)`` page coords of *strip* along its stacking axis, where
    *inner* is the end nearest the view edge (the first tier a dim fills). Reads the
    live ``outer_limit`` so an orchestrator reservation (#133) stays honoured. The
    cursor-free counterpart of :meth:`Strip.allocate` — a collect-then-solve pass
    (ADR 0009) reads these bounds and carves, rather than advancing a mutable cursor."""
    near = strip.anchor + strip.direction * strip.gap
    if strip.direction == 1:
        return near, strip.outer_limit, near  # lo, hi, inner (=lo)
    return strip.outer_limit, near, near  # lo, hi, inner (=hi)


def carve_free_segments(lo, hi, intervals, pad):
    """``[lo, hi]`` minus every obstacle interval inflated by *pad*, merged and
    complemented — the option-(c) occupancy carve (ADR 0009 / #321). A dim is then
    spaced only WITHIN a clear segment, so it can never overprint a placed occupant
    (a leader shaft, the section hatch, a location-dim tier): the old per-tier
    ``allocate`` + post-hoc ``_box_hits`` retry becomes structural. *intervals* are
    ``(a, b)`` pairs along the strip's stacking axis (e.g. ``(box_y0, box_y1)`` for a
    below strip). Returns a list of ``(seg_lo, seg_hi)`` free segments, lo→hi."""
    blocked = []
    for a0, b0 in intervals:
        a1, b1 = max(lo, a0 - pad), min(hi, b0 + pad)
        if b1 > a1:
            blocked.append((a1, b1))
    blocked.sort()
    merged: list[list[float]] = []
    for a0, b0 in blocked:
        if merged and a0 <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b0)
        else:
            merged.append([a0, b0])
    free, cur = [], lo
    for a0, b0 in merged:
        if a0 > cur:
            free.append((cur, a0))
        cur = b0
    if cur < hi:
        free.append((cur, hi))
    return free


def corridor_blockers(dwg, view):
    """Boxes of annotations a dimension's *witness corridor* (the span from the view
    edge out to its dim line) must not cross — leaders/callouts, the section hatch, the
    title block: everything that is neither a datum-chained ``Dimension`` nor a
    crossable centre line/mark (:data:`CROSSABLE_TYPES`).

    :func:`strip_obstacles` carves the 1-D strip so a dim *line* clears every occupant,
    but a right/below dim also occupies the 2-D corridor back to the view — and a bore
    callout's leader sitting in that corridor is crossed however far out the line is
    placed (the #133/#225/#305 leader class, in its witness-corridor form). A dim whose
    full footprint hits one of these must route to another view, not overprint it (ISO
    128). Sibling location/envelope dims are excluded: they chain off the shared datum
    and legitimately share the corridor. View scoping mirrors :func:`strip_obstacles`
    (this view's own annotations + drawing-level occupants that no ortho view owns)."""
    boxes = []
    for name, o in dwg.iter_annotations():
        if view is not None:
            owner = dwg.view_of(name)
            if owner is not None and owner != view:
                continue
        if isinstance(o, (Dimension, SafeDimension)) or type(o).__name__ in CROSSABLE_TYPES:
            continue  # datum-chained dims share the corridor; centre lines are crossable
        bb = _geom_box(o)
        if bb is not None:
            boxes.append(bb)
    return boxes


def _box_hits(bb, boxes):
    """True when ``bb`` overlaps any box in ``boxes`` (strict AABB test,
    :func:`draftwright._geometry._boxes_overlap`). Slightly more conservative
    than the within-view label lint (which tolerates a 0.5 mm sliver): a touch
    does not count as an overlap, so a candidate never overprints — at worst it
    is dropped a hair early."""
    return bb is not None and any(_boxes_overlap(bb, c) for c in boxes)


def box_within_page_and_clear(bb, page_box, obstacles) -> bool:
    """True when ``bb`` is fully inside *page_box* and hits none of *obstacles*
    (:func:`_box_hits`) — the safety check a shifted label must pass before a
    caller accepts it over an unshifted fallback (#129 review: this was inline
    in ``holes.py``'s ``_clear_and_validate`` and untestable in isolation)."""
    return (
        bb is not None
        and bb[0] >= page_box[0]
        and bb[1] >= page_box[1]
        and bb[2] <= page_box[2]
        and bb[3] <= page_box[3]
        and not _box_hits(bb, obstacles)
    )


@dataclass
class CorridorCandidate:
    """One datum-referenced linear dim collected for a shared corridor's single solve
    (ADR 0009 end state, #345/#346). Multiple render passes (`render_locations`,
    `render_slots`) feed the SAME above-view strip; committing per-pass interleaves the
    dims and cannot dedup coincident spans. Each pass instead registers a candidate here;
    one :func:`solve_corridor` per strip dedups, orders, and places the whole set.

    Attributes:
        name/build: the ``(name, pos->Dimension)`` pair :func:`place_strip_candidates`
            consumes — unchanged.
        order:      sort key placing the candidate in the corridor ladder. Location dims
            key on datum distance (the monotonic ISO ladder); size dims form a separate
            contiguous run so a slot length never lands mid-ladder (#346).
        dedup:      coincidence key ``(view, meas-origin, meas-endpoint)`` on the MEASURED
            axis, or ``None`` to never dedup (size dims). Two candidates with equal keys are
            the same physical dimension; the higher-``precedence`` one survives (#345).
        precedence: dedup survivor rank — a hole *location* dim (feeds coverage/table
            escalation) outranks a coincident slot *position* line.
        priority:   over-capacity survival rank (#357). When a strip cannot hold every
            candidate, :func:`plan_strip` drops the lowest ``(priority, key)`` — so a higher
            ``priority`` is kept. An authored GD&T frame sets this above the auto dims so it
            is not dropped in favour of a lower-value auto dim purely by stacking-key order.
            Default 0 (every auto dim) → key order, unchanged.
        anchored/natural: when ``anchored`` is true, the strip solve keeps this candidate
            near its own natural stacking-axis page coordinate instead of the segment edge.
            This is how user-authored pinned dimension intents join the shared solve
            without being invalidated by a later first-fit pass.
        on_place/on_drop: the pass's own post-placement bookkeeping — coverage
            registration / drop lint + `Escalation`, or a slot's below-side fallthrough.
        force:      policy-B force-keep after the corridor-respecting pass (locations have
            no alternate view); size/position slot dims fall through instead (``on_drop``).
    """

    name: str
    build: object
    order: tuple
    on_place: object
    on_drop: object
    dedup: tuple | None = None
    precedence: int = 0
    priority: float = 0
    anchored: bool = False
    natural: float | None = None
    force: bool = False
    # The source IR feature this dim was rendered for — recorded as provenance when the
    # dim is placed at drain (ADR 0010). ``None`` leaves the annotation feature-less.
    feature: object | None = None
    # Real stacking-axis + perpendicular footprint ``(w, h)`` in page-mm, or ``None`` to
    # use the dimension default ``(tier, tier)``. Wide/tall occupants (a GD&T feature
    # control frame is ~24×6 mm) set this so the strip solve reserves their true extent
    # instead of one label-height (ADR 0009 real-footprint plumbing, #61). A dim leaves
    # it ``None`` — byte-identical to the pre-plumbing placement.
    size: tuple | None = None
    # An ``(x0, y0, x1, y1)`` page-box this candidate must NOT overlap even when force-kept —
    # the title block, which is placed after the corridor drain so the strip carve can't see
    # it (#481). ``None`` (every dim) skips the check → byte-identical.
    forbid: object | None = None
    # Analytical ``pos -> (x0, y0, x1, y1)`` footprint of the geometry ``build(pos)``
    # would produce (#602): lets the strip solve measure and evaluate this candidate
    # without constructing OCC geometry at all (see :func:`dim_footprint`). ``None``
    # falls back to one probe build at the strip edge + the box-shift model. CONTRACT:
    # the footprint must be accurate — its PERPENDICULAR extent feeds the obstacle
    # band filter, so an underestimate hides obstacles from the carve. The solve
    # re-validates each built survivor against the blockers, the forbid box and the
    # band-filtered-out obstacles, so a miss costs a wasted build and a retry, but
    # keeping footprints truthful (``dim_footprint``, ±0.05 mm) is what keeps that
    # fallback rare and the placement identical to the probe path.
    footprint: object | None = None


def solve_corridor(dwg, strip, view, axis, cands, tier, corner_reserves=(), *, key=None, ctx=None):
    """One collect-then-solve over every :class:`CorridorCandidate` a shared strip
    accumulated across passes (ADR 0009 end state). Dedup → order → one non-force
    :func:`place_strip_candidates` pass → a force pass for the force-eligible leftovers →
    dispatch each candidate's ``on_place``/``on_drop``. This is what removes the duplicate
    span (#345) and the interleaved ladder (#346) by construction: a single solve sees the
    full set, so coincident spans collapse and the order is one monotonic chain.

    *key* (the corridor's ``(view, side)``) and *ctx* are threaded by
    :func:`drain_corridors` for the opt-in solve trace (#736, ``ctx.trace``) — when
    tracing is off (``ctx`` is ``None`` or carries no trace) both are inert."""
    if not cands:
        return
    trace = None if ctx is None else ctx.trace
    if trace is not None:
        trace.begin_solve(key, view, axis, tier, strip, cands)
    # Dedup: keep the highest-precedence candidate per coincidence key (tie-break on name,
    # deterministic — ADR 0001). A displaced duplicate is a *loser*: while its winner is
    # drawn it is silently dropped (never starved, so firing its pass's drop lint would be a
    # false report) — but if the winner itself fails to place, the top loser is promoted so
    # the measurement still gets its pass's fallthrough/drop handling (no silent vanish).
    winners: dict = {}
    for c in cands:
        if c.dedup is None:
            continue
        prev = winners.get(c.dedup)
        # Winner: highest precedence, ties broken by the lexicographically smaller name.
        if (
            prev is None
            or c.precedence > prev.precedence
            or (c.precedence == prev.precedence and c.name < prev.name)
        ):
            winners[c.dedup] = c
    kept = [c for c in cands if c.dedup is None or winners.get(c.dedup) is c]
    losers: dict = {}  # dedup key → its displaced candidates (highest precedence first)
    for c in cands:
        if c.dedup is not None and winners.get(c.dedup) is not c:
            losers.setdefault(c.dedup, []).append(c)
    for group in losers.values():
        group.sort(key=lambda c: (-c.precedence, c.name))
    kept.sort(key=lambda c: c.order)
    if trace is not None:  # record who lost each dedup group (a loser never starves)
        for dk, group in losers.items():
            for loser in group:
                trace.record_outcome(loser.name, "deduped", winner=winners[dk].name)

    def _promote_losers(dropped_winner):
        # The winner did not place → hand its measurement to the best surviving loser
        # (e.g. the slot position's below-strip fallthrough), then stop.
        for loser in losers.get(dropped_winner.dedup, ()):
            loser.on_drop(loser.name)
            if trace is not None:
                trace.record_outcome(loser.name, "promoted")
            break

    if strip is None:  # no such strip on this drawing — every candidate drops
        for c in kept:
            c.on_drop(c.name)
            if trace is not None:
                trace.record_outcome(c.name, "dropped", reason="no_strip")
            if c.dedup is not None:
                _promote_losers(c)
        if trace is not None:
            trace.end_solve()
        return
    pairs = [(c.name, c.build) for c in kept]
    feats = {c.name: c.feature for c in kept if c.feature is not None}  # provenance (ADR 0010)
    sizes = {c.name: c.size for c in kept if c.size is not None}  # real footprint (#61)
    forbid = {c.name: c.forbid for c in kept if c.forbid is not None}  # title-block box (#481)
    prio = {c.name: c.priority for c in kept if c.priority}  # over-capacity survival rank (#357)
    anchored = {c.name: c.anchored for c in kept if c.anchored}
    naturals = {c.name: c.natural for c in kept if c.natural is not None}
    foots = {c.name: c.footprint for c in kept if c.footprint is not None}  # analytical (#602)
    left = {
        n
        for n, _ in place_strip_candidates(
            dwg,
            strip,
            view,
            axis,
            pairs,
            tier,
            features=feats,
            sizes=sizes,
            forbid=forbid,
            priorities=prio,
            anchored=anchored,
            naturals=naturals,
            footprints=foots,
            corner_reserves=corner_reserves,
            trace=trace,
        )
    }
    force_pairs = [(c.name, c.build) for c in kept if c.name in left and c.force]
    still = (
        {
            n
            for n, _ in place_strip_candidates(
                dwg,
                strip,
                view,
                axis,
                force_pairs,
                tier,
                force=True,
                footprints=foots,
                corner_reserves=corner_reserves,
                features=feats,
                sizes=sizes,
                forbid=forbid,
                priorities=prio,
                anchored=anchored,
                naturals=naturals,
                trace=trace,
            )
        }
        if force_pairs
        else set()
    )
    for c in kept:
        placed = c.name not in left or (c.force and c.name not in still)
        if placed:
            c.on_place(c.name)  # placed in the corridor-respecting pass or the force pass
            if trace is not None:
                trace.record_outcome(c.name, "placed")
        else:
            n_deferred = len(ctx.post_drain) if trace is not None else 0
            c.on_drop(c.name)  # dropped / not force-kept — the pass's drop handler runs
            if trace is not None:  # did on_drop queue a post-drain fallthrough?
                trace.record_outcome(
                    c.name, "dropped", deferred_post_drain=len(ctx.post_drain) > n_deferred
                )
            if c.dedup is not None:  # a deduped winner failed → promote its top loser
                _promote_losers(c)
    if trace is not None:
        trace.end_solve()


@dataclass
class PlacementContext:
    """The per-run placement scratch a build's passes share — plus references to the drawing's
    build-state stores (the ``registry`` build-issue sink + ``coverage`` bookkeeping) — threaded
    to the passes explicitly instead of hung on the ``Drawing`` result object (ADR 0005 §2, #639):
    the corridor batch
    (:func:`register_corridor`/:func:`drain_corridors`), the escalation list (ADR 0009 Amdt 1,
    #351), and the enlarged-detail request list (#307).

    All three are per-run — both entry paths (:func:`_auto_annotate` and ``Drawing.finalize``)
    make a fresh one each build and discard it after draining/consuming. The corridor batch is a
    pure function of the still-present intents; escalations/detail-requests need no cross-retry
    persistence either, because finalize is transactional (#647): a raised drain rolls the drawing
    back and the retry re-runs from a clean slate, re-generating them."""

    corridor_batch: dict = field(default_factory=dict)
    escalations: list = field(default_factory=list)
    detail_requests: list = field(default_factory=list)
    # Fallthrough callbacks a pass's on_drop queues to run AFTER every corridor has
    # drained (#684 review): a mid-drain carve could occupy space a later sibling
    # corridor's force candidate needs; deferral makes "post-drain" literally true.
    post_drain: list = field(default_factory=list)
    # The opt-in solve-trace recorder (#736) — a :class:`SolveTrace` threaded off the
    # drawing's build state by both entry paths, or ``None`` (the default: tracing off,
    # every hook a bare None check). Ctx state, not a module global, so the finalize
    # path traces exactly like the auto pass.
    trace: Any = None
    # The drawing's build-state stores, referenced (not owned) by the run's passes (#639).
    # Duck-typed as ``Any`` — matching the untyped ``Drawing._record_build_issue`` they replace —
    # so mypy does not reject the delegating calls below.
    registry: Any = None  # the drawing's AnnotationRegistry: build-issue sink + names
    coverage: Any = None  # the drawing's CoverageState
    # The ensured PartModel (ADR 0008 IR) the run's passes read, threaded off the drawing so
    # they no longer reach into ``dwg._part_model`` (#639). Both entry paths set it from the
    # PUBLIC ``dwg.model()`` after the model is ensured/attached.
    part_model: Any = None
    # Whether the model was DECLARED (vs detected) — the ADR 0011 gate the orchestrator reads,
    # threaded off ``getattr(dwg, "_model_declared")`` (#639).
    model_declared: bool = False
    # Per-run cache for :meth:`feature_of_hole_at` — the model is fixed after build, so a
    # per-ctx (per-run) index is correct (mirrors the old ``Drawing._hole_feature_index``).
    _hole_feature_index: Any = field(default=None, repr=False)

    def feature_of_hole_at(self, location):
        """The IR hole/pattern feature whose member sits at model-space *location*, or ``None``
        (#408/#639). Attributes a balloon (which carries a recognition hole, not the IR feature)
        to its feature so :meth:`Drawing.drop` clears it. Cached on the run's ctx — the model is
        fixed after build."""
        m = self.part_model
        if m is None:
            return None
        if self._hole_feature_index is None:
            idx: dict = {}
            for f in getattr(m, "features", []):
                if getattr(f, "kind", None) in ("hole", "pattern"):
                    for loc in getattr(f, "members", None) or (f.frame.origin,):
                        idx[tuple(round(c, 3) for c in loc)] = f
            self._hole_feature_index = idx
        return self._hole_feature_index.get(tuple(round(c, 3) for c in location))

    def record_issue(self, severity, code, message) -> None:
        """Record a build-time lint issue on the run's registry (#639). Replaces the passes'
        old `dwg._record_build_issue`."""
        self.registry.record_issue(LintIssue(severity=severity, code=code, message=message))

    def reset_issues(self) -> None:
        self.registry.reset_issues()

    def drop_issues(self, *codes) -> None:
        self.registry.drop_issues(codes)


def register_corridor(ctx, key, strip, view, axis, tier, cand):
    """Queue a :class:`CorridorCandidate` under a shared corridor *key* so one
    :func:`drain_corridors` places the whole cross-pass set together (ADR 0009 end state).
    The first registration for a key fixes its ``(strip, view, axis)``; mixed producers on
    the same corridor use the largest requested tier so spacing is not registration-order
    dependent."""
    b = ctx.corridor_batch.setdefault(
        key, {"strip": strip, "view": view, "axis": axis, "tier": tier, "cands": []}
    )
    b["tier"] = max(b["tier"], tier)
    b["cands"].append(cand)


def drain_corridors(ctx, dwg):
    """Solve every registered corridor (one :func:`solve_corridor` per strip), then clear
    the batch. Called once, after all corridor-feeding passes have registered. Takes both the
    scratch *ctx* (the batch) and *dwg* (the drawing :func:`solve_corridor` places onto).

    Corner coordination (helpers ≥0.14): perpendicular strips of one view contest the
    view corners — a tight-span dim's outside-arrow tails overhang past the view edge
    into the sibling strip's band (an 8 mm plate-thickness dim on the left strip dips
    below the view bottom; the below strip's own tight dim pokes left of the view edge).
    Solved sequentially and blind, the first drain fills the corner and the second's
    force-kept candidate hard-drops. So each solve receives the *innermost-tier
    footprint boxes* of every not-yet-drained same-view sibling's **force** candidates
    as extra obstacles: the earlier drain places clear of the corner the later one
    provably needs. Reservation is exact (the candidate's own analytical footprint, at
    the innermost position it would take), restricted to force candidates — principal
    dims that would otherwise drop rather than relocate — so best-effort occupants
    never lose capacity to it. Already-drained siblings need nothing: their dims are
    real obstacles via :func:`strip_obstacles`."""
    batches = list(ctx.corridor_batch.items())
    for i, (key, b) in enumerate(batches):
        reserves = []
        for _sk, sib in batches[i + 1 :]:
            if sib["view"] != b["view"] or sib["strip"] is None:
                continue
            s = sib["strip"]
            inner_pos = s.anchor + s.direction * s.gap
            for c in sib["cands"]:
                if c.force and c.footprint is not None:
                    box = c.footprint(inner_pos)
                    if box is not None:
                        reserves.append(box)
        solve_corridor(
            dwg,
            b["strip"],
            b["view"],
            b["axis"],
            b["cands"],
            b["tier"],
            corner_reserves=reserves,
            key=key,  # corridor identity + trace threading (#736)
            ctx=ctx,
        )
    ctx.corridor_batch = {}
    # Deferred fallthroughs (opposite-strip retries) run once every strip has drained,
    # so a retry can never preempt a corner a later sibling's force candidate needs.
    pending, ctx.post_drain = ctx.post_drain, []
    for cb in pending:
        cb()


def place_strip_candidates(
    dwg,
    strip,
    view,
    axis,
    cands,
    tier,
    *,
    force=False,
    features=None,
    sizes=None,
    forbid=None,
    priorities=None,
    anchored=None,
    naturals=None,
    footprints=None,
    corner_reserves=(),
    trace=None,
    trace_label=None,
):
    """Collect-then-solve placement of location/feature dims on one strip (ADR 0009).
    The single shared strip placer that retires the ``Strip.allocate`` cursor (#150,
    P3): each candidate in *cands* — an ``(name, build(pos)->dim)`` pair — is spaced by
    one :func:`plan_strip` solve per free segment of the CARVED strip (`strip` carved
    around :func:`strip_obstacles`), replacing the per-dim ``allocate`` + ``_box_hits``
    tier-retry. *tier* is the label height (sets the inter-dim gap ``tier + spacing``).

    Occupancy is THIS view's own placed annotations plus the drawing-level obstacles no
    ortho view owns (the section hatch), recomputed per call so a dim placed earlier in
    the pass is avoided; other ortho views are disjoint (ADR 0004) and excluded so their
    rows never over-carve this strip. This makes the old post-hoc collision retry
    structural: a dim can never land on a bore-callout leader shaft the label-only
    occupancy missed (#133/#225/#305).

    A right/below dim also occupies the 2-D corridor back to the view edge, which the
    1-D strip carve cannot represent: a leader in that corridor is crossed no matter how
    far out the dim line lands. By default such a placement is rejected so the caller can
    route the dim to the other view (its disjoint block cannot cross this leader).

    *sizes* maps a candidate's name to its real page-mm footprint ``(w, h)``; absent
    names use the dimension default ``(tier, tier)``. A wide/tall occupant (a GD&T
    frame, #61) sets it so :func:`plan_strip` enforces its true stacking gap — over
    capacity it is relocated to the next segment or dropped, never overlapped.

    *priorities* maps a candidate's name to its over-capacity survival rank (#357);
    absent names default to 0. When a segment is over capacity :func:`plan_strip` drops
    the lowest ``(priority, key)``, so a higher priority is kept — an authored GD&T frame
    is not dropped for a lower-value auto dim purely by stacking-key order.

    *anchored* and *naturals* opt individual candidates into the weighted anchoring
    mode in :func:`plan_strip`. This preserves the old segment-edge natural for every
    caller that does not pass them, while letting authored pinned candidates express the
    page coordinate they asked for inside the same shared solve.

    ``force=True`` skips that corridor check — the caller's last resort when no view took
    the dim cleanly: keep it on its natural view and accept the (same-feature) leader
    crossing rather than drop a real dimension (policy B). Candidates that find no strip
    tier AT ALL are still returned (a physically full strip — the caller records the
    genuine drop).

    *trace* is the opt-in :class:`SolveTrace` recorder (#736), ``None`` (default) = off
    with nil cost; :func:`solve_corridor` threads it for corridor solves, and a
    standalone caller may pass ``trace=ctx.trace`` with a *trace_label* naming its
    pass. The recorded pass carries the carved span, the in-band obstacles with their
    owning annotation names, the free segments, per-candidate placements/rejections
    (with reasons), and the unplaced leftovers."""
    if strip is None or not cands:
        return list(cands)
    tp = (
        trace.begin_pass(force=force, label=trace_label, strip=strip, view=view, axis=axis)
        if trace is not None
        else None
    )
    lo, hi, inner = strip_free_span(strip)
    idx = 1 if axis == "y" else 0

    # Reserve the outermost label's OUTWARD extent at the strip boundary. plan_strip bounds
    # the dim-LINE position, but the label extends outward from it — so without this the last
    # tier's label overshoots outer_limit (into the iso view / page margin), unlike the old
    # Strip.allocate which checked `start + tier <= outer_limit` (#338 review). A plain dim's
    # label extends one `tier` outward (one-sided). A GD&T glyph (#61) hangs off a Leader that
    # CENTRES it on the elbow for an above/below strip (real outward extent = height/2) but
    # places it one-sided for a left/right strip (extent = full width). Reserve the MAX real
    # outward extent among these candidates — else a glyph wider than `tier` renders off the
    # sheet (annotation_out_of_bounds) instead of dropping when the strip is too narrow (ADR
    # 0009 Amdt 7 fixed inter-candidate gaps but not this edge). With no `sizes` (every dim)
    # this is `tier`, byte-identical. The strip edge is not an obstacle (obstacles carry their
    # own footprint + pad), so only the boundary needs it.
    def _outward(name):
        sz = (sizes or {}).get(name)
        if sz is None:
            return tier  # a dim: one-sided tier reservation (unchanged)
        return sz[idx] if axis == "x" else sz[idx] / 2  # GD&T: one-sided (L/R) vs centred (A/B)

    reserve = max([tier, *(_outward(n) for n, _ in cands)])
    if inner == lo:
        hi -= reserve
    else:
        lo += reserve
    perp = 0 if axis == "y" else 1  # the axis the dims do NOT stack along
    pad = tier + strip.spacing  # min separation between stacked dim lines
    # Perpendicular band of these candidates. The 1-D carve projects obstacles onto the
    # stacking axis only, so an obstacle on ANOTHER strip of this view — disjoint in the
    # perpendicular axis, never actually touching — would falsely block (e.g. the overall
    # width dim below the view blocking a slot-width dim on the right strip). Filter such
    # obstacles out first. The perpendicular extent is independent of the tier position,
    # so a single probe build per candidate suffices; the corridor check below already
    # uses the full 2-D box, so it needs no such filter.
    #
    # That one probe build is also this call's entire MEASUREMENT step (#602): every
    # candidate is a fixed feature-side anchor (witness origin / leader shaft end)
    # plus a dim line that translates with the tier position, so its box at position
    # ``pos`` is the probe box with the OUTWARD stacking-axis edge shifted by
    # ``pos - lo`` — no further geometry is built to evaluate a position. The
    # segment loop below re-solves and re-checks on these predicted boxes only;
    # each finally-accepted candidate is built once and its real box re-validated
    # (a prediction miss degrades to a later-segment retry, never a collision).
    # A candidate with an analytical *footprints* entry (#602) needs no probe build at
    # all — its box at any position is computed, not measured.
    probe_boxes = {
        name: (footprints[name](lo) if name in (footprints or {}) else _geom_box(build(lo)))
        for name, build in cands
    }
    pbands = [(b[perp], b[perp + 2]) for b in probe_boxes.values() if b is not None]

    def _predicted_box(name, pos):
        fp = (footprints or {}).get(name)
        if fp is not None:
            return fp(pos)
        pb = probe_boxes.get(name)
        if pb is None:
            return None
        box = list(pb)
        # The moving edge is the one AWAY from the view (`inner`); the feature-side
        # edge is anchored geometry and stays put.
        box[idx + 2 if inner == lo else idx] += pos - lo
        return tuple(box)

    if tp is None:
        occupied = strip_obstacles(dwg, view=view, crossable=CROSSABLE_TYPES)
        owners = {}
    else:  # tracing: same boxes, tagged with their owning annotation names (#736)
        named = strip_obstacles(dwg, view=view, crossable=CROSSABLE_TYPES, named=True)
        occupied = [b for _, b in named]
        owners = {id(b): n for n, b in named}
    # Obstacles OUTSIDE the batch's predicted perpendicular band are invisible to the
    # carve below by design — but that makes the band prediction itself load-bearing: a
    # candidate whose real geometry exceeds its predicted band could land on one with no
    # check ever seeing it (review #679). Keep the filtered-out set: the post-build
    # validation re-checks each survivor's REAL box against it. In-band overlaps are NOT
    # validated — witness lines legitimately cross the boxes of dims stacked further in
    # (ISO 129-1), which is exactly why the carve projects onto the stacking axis only.
    out_of_band: list = []
    if pbands:
        band_lo, band_hi = min(p[0] for p in pbands), max(p[1] for p in pbands)
        in_band = [b for b in occupied if b[perp] < band_hi and b[perp + 2] > band_lo]
        out_of_band = [b for b in occupied if not (b[perp] < band_hi and b[perp + 2] > band_lo)]
        occupied = in_band
        # Corner reserves (drain_corridors): projected boxes of not-yet-drained sibling
        # corridors' force candidates. Same band relevance filter as real obstacles, but
        # NEVER in out_of_band — a reserve is a projection, not geometry, so a survivor's
        # real box must not be failed against it.
        occupied += [
            r
            for r in corner_reserves
            if r is not None and r[perp] < band_hi and r[perp + 2] > band_lo
        ]
    blockers = () if force else corridor_blockers(dwg, view)
    segs = carve_free_segments(lo, hi, [(b[idx], b[idx + 2]) for b in occupied], pad)
    # Fill innermost-first (nearest the view), matching the old cursor's stack order.
    segs.sort(key=lambda s: abs((s[0] if inner == lo else s[1]) - inner))
    if tp is not None:  # the diagnosis payload: what carved this strip, and what's left
        tp["span"] = [lo, hi]
        tp["obstacles"] = [
            # owner None = a corner reserve (a projection, not placed geometry)
            {"owner": owners.get(id(b)), "box": list(b)}
            for b in occupied
        ]
        tp["out_of_band"] = len(out_of_band)
        tp["free_segments"] = [list(s) for s in segs]
    todo = list(cands)

    def _take_for_segment(items, n):
        if len(items) <= n:
            return items, []
        # Do not let segment-cap slicing preempt the ranked selection step (#357/#393).
        # `plan_strip` drops the lowest (priority, generated-key), but a narrow segment
        # can only see the candidates we hand it. Preselect the highest-priority members
        # for this segment, preserving their original order for crossing-free placement;
        # ties mirror the generated key below (inner=lo keeps later candidates, inner=hi
        # keeps earlier candidates).
        ranked = sorted(
            enumerate(items),
            key=lambda item: (
                (priorities or {}).get(item[1][0], 0.0),
                item[0] if inner == lo else -item[0],
            ),
            reverse=True,
        )
        chosen = {i for i, _ in ranked[:n]}
        take = [nb for i, nb in enumerate(items) if i in chosen]
        rest = [nb for i, nb in enumerate(items) if i not in chosen]
        return take, rest

    def _evaluate_segment(take, seg_lo, seg_hi):
        nat = seg_lo if inner == lo else seg_hi
        # Keys order the tiers so the FIRST candidate lands on the inner tier: for an
        # inner=lo strip that is the lowest position (ascending keys); for a below strip
        # (inner=hi) it is the highest, so the keys reverse.
        triples = [
            (
                StripCandidate(
                    f"{(k if inner == lo else len(take) - 1 - k):04d}",
                    (
                        (0.0, (naturals or {}).get(nb[0], nat))
                        if axis == "y"
                        else ((naturals or {}).get(nb[0], nat), 0.0)
                    ),
                    (sizes or {}).get(nb[0], (tier, tier)),
                    priority=(priorities or {}).get(nb[0], 0.0),
                    anchored=(anchored or {}).get(nb[0], False),
                ),
                nb,
            )
            for k, nb in enumerate(take)
        ]
        res = plan_strip([sc for sc, _ in triples], seg_lo, seg_hi, pad, axis=axis)
        accepted = []
        rejected = []

        def _reject(name, reason):  # trace-only (#736): why this candidate left this segment
            if tp is not None:
                tp["rejected"].append(
                    {"name": name, "reason": reason, "segment": [seg_lo, seg_hi]}
                )

        for sc, (name, build) in triples:
            pos = res.placed.get(sc.key)
            if pos is None:  # segment over its estimated capacity (shouldn't occur)
                _reject(name, "over_capacity")
                rejected.append((name, build))
                continue
            # Predicted box, not built geometry (#602): the refill loop re-evaluates
            # every already-accepted candidate each iteration, so building here made
            # the drain quadratic in OCC builds.
            box = _predicted_box(name, pos)
            if box is None:  # probe didn't bbox — measure the old way, once per check
                box = _geom_box(build(pos))
            if (
                not force and box is not None and _box_hits(box, blockers)
            ):  # corridor crosses a leader
                _reject(name, "corridor_blocked")
                rejected.append((name, build))
                continue
            # A forbidden box (the title block, #481) is rejected even under force — it is
            # placed after the drain, so the strip carve can't see it; a force-kept GD&T frame
            # must still not stack onto it. `forbid` maps names to their box (only GD&T sets it,
            # so dims are byte-identical). Returned unplaced → the caller's on_drop fallthrough.
            fb = (forbid or {}).get(name)
            if fb is not None and box is not None and _box_hits(box, (fb,)):
                _reject(name, "forbid_box")
                rejected.append((name, build))
                continue
            accepted.append(((name, build), pos))
        return accepted, rejected

    for seg_lo, seg_hi in segs:
        if not todo:
            break
        cap = int((seg_hi - seg_lo) / pad) + 1
        take, todo = _take_for_segment(todo, cap)
        rejected_total = []
        while take:
            accepted, rejected = _evaluate_segment(take, seg_lo, seg_hi)
            rejected_total.extend(rejected)
            vacancies = cap - len(accepted)
            if vacancies <= 0 or not todo:
                break
            fill, todo = _take_for_segment(todo, vacancies)
            take = [nb for nb, _pos in accepted] + fill
        # Build each survivor ONCE at its solved position and re-validate the real box
        # (the #602 validation fallback): a prediction miss is returned to the pool for
        # the next segment — exactly where a same-segment rejection would have sent it.
        placed = []
        for (name, build), pos in accepted:
            dim = build(pos)
            real = _geom_box(dim)
            if real is not None:
                if not force and _box_hits(real, blockers):
                    if tp is not None:
                        tp["rejected"].append(
                            {"name": name, "reason": "real_box_corridor_blocked"}
                        )
                    rejected_total.append((name, build))
                    continue
                fb = (forbid or {}).get(name)
                if fb is not None and _box_hits(real, (fb,)):
                    if tp is not None:
                        tp["rejected"].append({"name": name, "reason": "real_box_forbid"})
                    rejected_total.append((name, build))
                    continue
                # A survivor whose real geometry escaped the batch's predicted
                # perpendicular band could overlap an obstacle the carve was never
                # shown (review #679) — the one collision class the stacking-axis
                # model cannot tolerate. Never fires while predictions are accurate.
                if _box_hits(real, out_of_band):
                    if tp is not None:
                        tp["rejected"].append({"name": name, "reason": "real_box_out_of_band"})
                    rejected_total.append((name, build))
                    continue
            placed.append((name, dim))
            if tp is not None:
                tp["placed"].append({"name": name, "pos": pos})
        todo = todo + rejected_total
        for name, dim in placed:
            # Record feature provenance (ADR 0010): the drain-time seam for corridor-placed
            # dims — `features` maps this batch's names to their source IR feature.
            dwg.add(dim, name, view=view, feature=(features or {}).get(name))
    if tp is not None:
        tp["unplaced"] = [n for n, _ in todo]
        trace.end_pass(tp)  # folds a standalone pass's items; no-op when corridor-nested
    return todo


def carve_free_position(dwg, strip, view, axis, tier, perp_span, *, outermost=False):
    """The single free tier POSITION on *strip* at which a dim of height *tier* spanning
    *perp_span* ``(lo, hi)`` on the perpendicular axis clears every placed obstacle in
    *view* — the innermost (nearest the view) tier by default, or the outermost fitting
    one when *outermost*. Returns the dim-line page coord, or None if the strip is full.

    The position-returning counterpart of :func:`place_strip_candidates` (which batches,
    builds and adds): a caller that needs a dim's assigned position BEFORE building the
    next — the height-ladder leapfrog chain, where each step dim's witness base is the
    previous dim's line — uses this. Same carve: outer-label tier reservation, the
    perpendicular-band filter (*perp_span* drops obstacles disjoint from this dim's own
    perpendicular extent), and innermost-first fill.

    **No corridor check, by construction — not just omission.** This avoids obstacle
    *tiers* on the strip but does not reject a position whose witness *corridor* (feature
    → dim line, across *perp_span*) crosses a leader/callout. Crucially, a single-position
    return *cannot* fix a corridor crossing by choosing a different tier: every tier on
    one side shares that corridor, and a farther tier's corridor is a **superset** of a
    nearer one's, so the innermost free tier this already returns has the shortest
    corridor and the fewest crossings — moving outward only adds crossings. Corridor
    avoidance is therefore inherently a **relocation** problem (reject this position →
    place on another view/side), which is :func:`place_strip_candidates`' job and out of
    scope for a position return. Per caller: the height-ladder chain has no alternate
    view (correct to omit); public ``Drawing.place_dim`` takes the view AND side from the
    caller, so it cannot relocate; the PMI dim helpers already fall through sides
    (``_try_above(...) or _try_below(...)``) and are where a corridor-reject would go if
    ever wanted. Left as a documented known-limitation — the crossing is unobserved on
    the corpus (the cleanliness ratchet would catch it)."""
    if strip is None:
        return None
    lo, hi, inner = strip_free_span(strip)
    idx = 1 if axis == "y" else 0
    perp = 0 if axis == "y" else 1
    pad = tier + strip.spacing
    band_lo, band_hi = perp_span
    occ = [
        b
        for b in strip_obstacles(dwg, view=view, crossable=CROSSABLE_TYPES)
        if b[perp] < band_hi and b[perp + 2] > band_lo
    ]
    segs = carve_free_segments(lo, hi, [(b[idx], b[idx + 2]) for b in occ], pad)
    # A segment holds the dim iff it is at least `tier` wide (the label height). This IS
    # the outer-label reservation — inclusive at the boundary (a strip exactly `gap+tier`
    # wide fits one dim, as the old `allocate` did) — so it must NOT be combined with a
    # separate `hi -= tier` pull-in, which would double-reserve and drop that dim.
    fitting = [s for s in segs if s[1] - s[0] >= tier - 1e-9]
    if not fitting:
        return None
    if inner == lo:  # inner edge = seg lo; outermost = the segment reaching furthest out
        seg = max(fitting, key=lambda s: s[1]) if outermost else min(fitting, key=lambda s: s[0])
        return seg[0]
    seg = min(fitting, key=lambda s: s[0]) if outermost else max(fitting, key=lambda s: s[1])
    return seg[1]
