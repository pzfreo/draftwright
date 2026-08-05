"""sheet_emit — the ``Sheet``-script emitter (ADR 0011 Amendment 1, #461).

Mode 3 of the three authoring modes: *generate an editable beautiful-Python script*. Walk a
**detected** :class:`PartModel` and print a :class:`~draftwright.Sheet` script — one commentable
line per feature — that the user edits / comments-out / extends, then re-runs.

**Detected input only writes numbers (the part-seam form, ADR 0011 Amdt 1 decision).** For a STEP
file or a recovered solid the number *is* the ground truth, so a detected value is honest. We never
fabricate a build123d part to chase a number-free layer — a synthesised solid silently drops what
detection didn't model (a misread band, a thread's true form, an unrecognised relief) yet reads as
authoritative. A caller who
*has* the objects (mode 3b) wires their real part into the seam and swaps the number lines for
``sheet.hole(obj)`` references — the emitter's numbers are a starting point, not a ceiling.

Kinds with no declarative verb are flagged inline — never silently dropped — and left to the
auto-pass that runs over the declared model on re-run. Every *geometric* kind now has one
(``rotational`` was the last, #945, keyword-only — see :func:`draftwright.model.rotational`);
what remains on the comment floor is the aspect kinds a detector never emits (``finish``,
``note``, ``control_frame``, ``datum_ref``), so the branch is a live guard against a NEW kind
arriving unemitted rather than a standing gap. Imported authored
dimensions, including AP242 dimensional PMI, emit as Sheet ``measured_dimension(...)``
declarations (#873 — never the transitional ``dimension`` overload, so a regenerated script is
not born deprecated).
Fidelity: the script reproduces a lint-clean drawing of the same features. The generated script is
validated against the direct build for prismatic, slot/pattern, section, and turned/rotational
fixtures (#472).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from build123d import Shape

from draftwright.builder import detect_part_model


def _n(v) -> float | int:
    """A tidy number: int when integral, else rounded to 3 dp."""
    v = round(float(v), 3)
    return int(v) if v == int(v) else v


def _pt(p) -> str:
    return "(" + ", ".join(str(_n(c)) for c in p) + ")"


def _pts_arg(points) -> str:
    return "[" + ", ".join(_pt(p) for p in points) + "]"


def _bbox_arg(bbox) -> str:
    return "None" if bbox is None else _pt(bbox)


def _tuple_arg(values) -> str:
    vals = [str(_n(v)) for v in values]
    if len(vals) == 1:
        return f"({vals[0]},)"
    return "(" + ", ".join(vals) + ")"


def _hole_line(f) -> str:
    kw = [f"diameter={_n(f.diameter)}", f"at={_pt(f.frame.origin)}", f'axis="{f.frame.axis}"']
    if f.count and f.count > 1:
        kw.append(f"count={f.count}")
        # A count-group carries its member positions; without them the render collapses to a
        # single hole at the anchor (fidelity loss). Patterns recompute members from the
        # arrangement, so only a plain count-group needs them spelled out.
        if f.members:
            kw.append("members=[" + ", ".join(_pt(m) for m in f.members) + "]")
    if f.cbore:
        kw.append(f"cbore=({_n(f.cbore[0])}, {_n(f.cbore[1])})")
    if f.spotface:
        kw.append(f"spotface=({_n(f.spotface[0])}, {_n(f.spotface[1])})")
    if f.csink:
        kw.append(f"csink=({_n(f.csink[0])}, {_n(f.csink[1])})")
    if getattr(f, "thread", None):  # internal thread round-trips too (#859, symmetric with steps)
        kw.append(f"thread={f.thread!r}")
    # Blindness is a FACT on the feature, so it round-trips whether or not a depth was
    # measured. Guarding it on `depth is not None` emitted neither `through=False` nor a
    # depth for a `HoleFeature(through=False, depth=None)`, and `declare.hole` defaults
    # `through=True` — so the script rebuilt a THROUGH hole and the blindness vanished on
    # the emit path (#878). Same rule as #868, which fixed the render side: a renderer (or
    # an emitter) may not infer an engineering fact from a dimension's presence.
    if not f.through and f.depth is None:
        kw.append("through=False")
    elif f.through and f.depth is not None:
        # A THROUGH hole's measured depth is a fact too — it is the bore length through the
        # material — and dropping it left the declared feature with `depth=None` where
        # detection had 8.0 (#962, epic #964's model-fidelity oracle). It changes no
        # annotation: the callout reads THRU either way, checked. `.depth()` is not usable
        # here because it also sets `through=False`, which would invert the very fact above.
        kw.append(f"depth={_n(f.depth)}")
    line = f"sheet.hole({', '.join(kw)})"
    if not f.through and f.depth is not None:
        line += f".depth({_n(f.depth)})"  # sets through=False too
    return line


def _member_hole_str(m) -> str:
    """The ``hole(...)`` template for a pattern member — carries its ⌀ AND its
    counterbore / spotface / countersink / blind-depth so a counterbored or countersunk
    bolt circle keeps those callouts on re-run (declare.hole takes depth=/through=/cbore=/
    spotface=/csink= kwargs)."""
    kw = [f"diameter={_n(m.diameter)}", f"at={_pt(m.frame.origin)}", f'axis="{m.frame.axis}"']
    if m.cbore:
        kw.append(f"cbore=({_n(m.cbore[0])}, {_n(m.cbore[1])})")
    if m.spotface:
        kw.append(f"spotface=({_n(m.spotface[0])}, {_n(m.spotface[1])})")
    if m.csink:
        kw.append(f"csink=({_n(m.csink[0])}, {_n(m.csink[1])})")
    if getattr(m, "thread", None):  # a patterned threaded bore keeps its thread on re-run (#859)
        kw.append(f"thread={m.thread!r}")
    # `through=False` independent of the depth — see `_hole_line` (#878).
    if not m.through:
        if m.depth is not None:
            kw.append(f"depth={_n(m.depth)}")
        kw.append("through=False")
    elif m.depth is not None:
        # A THROUGH member's measured depth is a fact too, exactly as on the standalone verb
        # — and this template was the sibling that still dropped it after `_hole_line` was
        # fixed, which the model-fidelity oracle missed because its corpus carried no hole
        # pattern (#967 review). The two templates diverging is the recurring shape here.
        kw.append(f"depth={_n(m.depth)}")
    return f"hole({', '.join(kw)})"


def _member_pocket_str(m) -> str:
    """The ``pocket(...)`` template for a pocket-pattern member — carries its width × length ×
    depth, orientation and position. Length is derived from the emitted lo/hi so
    ``hi - lo == length`` exactly (declare.pocket rejects a 1e-6 mismatch).

    ``at=`` is carried for the same reason as the standalone verb (#962): without it
    `declare.pocket` synthesises an origin whose DEPTH-axis component is zero, so a member
    recessed into a face came back at 0 and the declared model diverged from the detected one.
    It cannot move the array — `declare.pocket_pattern` takes its centre from the pattern's own
    ``at=`` and falls back to the member's only when that is absent."""
    lo, hi = _n(m.lo), _n(m.hi)
    length = _n(round(float(hi) - float(lo), 3))
    return (
        f"pocket(width={_n(m.width)}, length={length}, depth={_n(m.depth)}, "
        f'long_axis="{m.long_axis}", width_axis="{m.width_axis}", '
        f"lo={lo}, hi={hi}, w_center={_n(m.w_center)}, at={_pt(m.frame.origin)})"
    )


def _member_slot_str(m) -> str:
    """The ``slot(...)`` template for a slot-pattern member — carries its width × length,
    orientation and position (a slot has no depth). Length is derived from the emitted lo/hi so
    ``hi - lo == length`` exactly (declare.slot rejects a 1e-6 mismatch).

    ``at=`` rides along for the same reason as the pocket member above (#962). A through-slot's
    depth-axis coordinate happens to be zero either way today, so this changes nothing now —
    it is here so the two member templates cannot drift apart, which is how the pocket one
    came to be the only place still dropping a position."""
    lo, hi = _n(m.lo), _n(m.hi)
    length = _n(round(float(hi) - float(lo), 3))
    return (
        f"slot(width={_n(m.width)}, length={length}, "
        f'long_axis="{m.long_axis}", width_axis="{m.width_axis}", '
        f"lo={lo}, hi={hi}, w_center={_n(m.w_center)}, at={_pt(m.frame.origin)})"
    )


def _measured_dimension_line(f) -> str:
    kw = [
        f"kind={f.dimension_kind!r}",
        f"value={_n(f.value)}",
        f"label={f.label!r}",
        f"dominant_axis={f.dominant_axis!r}",
        f"ref_pts={_pts_arg(f.ref_pts)}",
        f"ref_bbox={_bbox_arg(f.ref_bbox)}",
        f"at={_pt(f.frame.origin)}",
        f"axis={f.frame.axis!r}",
    ]
    if f.upper_tol is not None:
        kw.append(f"upper_tol={_n(f.upper_tol)}")
    if f.lower_tol is not None:
        kw.append(f"lower_tol={_n(f.lower_tol)}")
    if f.source != "sheet":
        kw.append(f"source={f.source!r}")
    if f.source_kind is not None and f.source_kind != f.dimension_kind:
        kw.append(f"source_kind={f.source_kind!r}")
    # `measured_dimension` since #873 — a generated script must not emit the transitional
    # overload, or every regenerated AP242 script would arrive pre-deprecated.
    return "sheet.measured_dimension(" + ", ".join(kw) + ")"


def _raw_pmi_line(f) -> str:
    return (
        "sheet.add(PmiFeature("
        f"frame=Frame({_pt(f.frame.origin)}, {f.frame.axis!r}), "
        f"pmi_kind={f.pmi_kind!r}, value={_n(f.value)}, label={f.label!r}, "
        f"dominant_axis={f.dominant_axis!r}, ref_bbox={_bbox_arg(f.ref_bbox)}, "
        f"ref_pts=tuple({_pts_arg(f.ref_pts)})"
        "))   # raw AP242 PMI fallback; not yet lowered to a drafting concept"
    )


def _feature_line(f, part_envelope=None) -> str:
    """The declaration for one feature.

    *part_envelope* is the whole-part `EnvelopeFeature` when the caller knows it, so an
    envelope that IS that one can emit the verb instead of six baked numbers (#976). Optional
    because two callers only ask whether the line is a comment, and passing it there would be
    noise; omitting it simply keeps the explicit form.
    """
    k = f.kind
    if k == "authored_dimension":
        return _measured_dimension_line(f)
    if k == "pmi":
        return _raw_pmi_line(f)
    if k == "envelope":
        if part_envelope is not None and f == part_envelope:
            # `sheet.envelope()` defaults to the whole part and now measures its SOLIDS with a
            # centred frame (#977/#976), so for the whole-part envelope it reconstructs exactly
            # this feature — verified on the CTC-01 STEP seam, where the raw import is
            # 1170 × 650 and the part is 800 × 450. Restating six numbers the object already
            # carries is what ADR 0011 exists to avoid.
            #
            # Guarded by equality rather than assumed: an envelope declared on a SUB-OBJECT is
            # not the part, and the bare verb would silently measure something else — the exact
            # shape of the four defects #977 closed.
            return "sheet.envelope()   # envelope " + (
                f"{_n(f.width)} × {_n(f.height)} × {_n(f.depth)}"
            )
        return (
            "sheet.add(EnvelopeFeature("
            f"frame=Frame({_pt(f.frame.origin)}, {f.frame.axis!r}), "
            f"width={_n(f.width)}, height={_n(f.height)}, depth={_n(f.depth)}, "
            f"bbox_min={_pt(f.bbox_min)}, bbox_max={_pt(f.bbox_max)}"
            f"))   # envelope {_n(f.width)} × {_n(f.height)} × {_n(f.depth)}"
        )
    if k == "step_level":
        # Carry shoulders + datum (#555/#578) so the declared model still constrains the step
        # POSITION, not just its heights. The fluent verb rebuilds the frame from base+datum.
        _items = [f"({a!r}, {_n(p)})" for a, p in f.shoulders]
        _sh = "(" + ", ".join(_items) + ("," if len(_items) == 1 else "") + ")"
        return (
            f"sheet.step_level(base={_n(f.base)}, levels={_tuple_arg(f.levels)}, "
            f"shoulders={_sh}, datum={_pt(f.datum)}, at={_pt(f.frame.origin)})"
            "   # prismatic height ladder + shoulder position(s)"
        )
    if k == "rotational":
        bores = f", bores=({', '.join(str(_n(b)) for b in f.bores)},)" if f.bores else ""
        return (
            f"sheet.rotational(od={_n(f.od)}{bores}, at={_pt(f.frame.origin)}, "
            f'axis="{f.frame.axis}")'
        )
    if k == "hole":
        return _hole_line(f)
    if k == "boss":
        thr = (
            f", thread={f.thread!r}" if getattr(f, "thread", None) else ""
        )  # external thread (#859)
        # The HEIGHT round-trips too (#938). Dropping it made the declared boss carry only
        # `boss.diameter` while the detected one also carries `boss_height.length`, so the
        # regenerated model could not express a dimension its source had — silently before
        # the mirror named it, and as a raise afterwards. ADR 0011's round-trip rule is that
        # recognise, emit and declare agree about a feature's parameters.
        height = f", height={_n(f.height)}" if getattr(f, "height", None) else ""
        # The SPAN too, not just the height. Detection reports `frame.origin` as the boss TOP
        # while `declare.boss` reads `at` as its CENTRE, so round-tripping the origin alone
        # shifted the height dimension by half the boss — same value, wrong witness lines
        # (#947 review, found by a boss fixture the corpus previously lacked). Emitting the
        # span states the geometry outright instead of relying on the two ends agreeing about
        # a coordinate convention.
        span = f", span=({_pt(f.span[0])}, {_pt(f.span[1])})" if getattr(f, "span", None) else ""
        return (
            f"sheet.diameter(diameter={_n(f.diameter)}{height}{span}, "
            f'at={_pt(f.frame.origin)}, axis="{f.frame.axis}"{thr})'
        )
    if k == "step":
        thr = (
            f", thread={f.thread!r}" if getattr(f, "thread", None) else ""
        )  # external thread (#859)
        return (
            f"sheet.step(diameter={_n(f.diameter)}, length={_n(f.length)}, "
            f'at={_pt(f.frame.origin)}, axis="{f.frame.axis}"{thr})'
        )
    if k == "slot":
        lo, hi = _n(f.lo), _n(f.hi)
        # Derive length from the EMITTED lo/hi so hi - lo == length exactly — declare.slot()
        # rejects the recogniser's independently-rounded (lo, hi, length) with a 1e-6 tolerance.
        length = _n(round(float(hi) - float(lo), 3))
        return (
            f"sheet.slot(width={_n(f.width)}, length={length}, "
            f'long_axis="{f.long_axis}", width_axis="{f.width_axis}", '
            f"lo={lo}, hi={hi}, w_center={_n(f.w_center)}, at={_pt(f.frame.origin)})"
        )
    if k == "pocket":
        lo, hi = _n(f.lo), _n(f.hi)
        # Derive length from the EMITTED lo/hi so hi - lo == length exactly — declare.pocket()
        # rejects an independently-rounded (lo, hi, length) with a 1e-6 tolerance.
        length = _n(round(float(hi) - float(lo), 3))
        return (
            f"sheet.pocket(width={_n(f.width)}, length={length}, depth={_n(f.depth)}, "
            f'long_axis="{f.long_axis}", width_axis="{f.width_axis}", '
            # `at=` is NOT redundant with lo/hi/w_center: those fix two axes, and with `at`
            # omitted `declare.pocket` synthesises an origin whose DEPTH-axis component is
            # zero. So a recess in an underside came back at z=0, the declared model diverged
            # from the detected one, and annotation parity still passed because the callout is
            # placed from the projected geometry (#962). Same reasoning on `slot` above.
            f"lo={lo}, hi={hi}, w_center={_n(f.w_center)}, at={_pt(f.frame.origin)}"
            + (", edge_anchored=True" if f.edge_anchored else "")
            + ")"
        )
    if k == "pad":
        half = f.width / 2
        return (
            f"sheet.pad(x0={_n(f.lo)}, x1={_n(f.hi)}, "
            f"y0={_n(f.w_center - half)}, y1={_n(f.w_center + half)}, "
            f"z0={_n(f.z0)}, z1={_n(f.z1)})"
        )
    if k == "pattern":
        # Defining dims for the furniture (BCD centreline / pitch / grid dims) PLUS the exact
        # member positions. The arrangement alone can't be recomputed faithfully — the
        # detector records no bolt-circle START ANGLE (nor a linear direction reliably) — so
        # spelling out members= is the only fidelity-safe form (declare uses them as-is).
        parts = [f'kind="{f.pattern}"', f"count={f.count}"]
        if f.pattern == "bolt_circle" and f.bcd:
            parts.append(f"bcd={_n(f.bcd)}")
        elif f.pattern == "linear" and f.pitch:
            parts.append(f"pitch={_n(f.pitch)}")
            if f.direction:
                # Redundant for the LAYOUT, since `members=` below is spelled out — but it is
                # a field on the feature, and leaving it None made the declared pattern differ
                # from the detected one (#967 review). The emitted script is a representation
                # of the model, not only a program that reproduces its positions.
                parts.append(f"direction={_pt(f.direction)}")
        elif f.pattern == "grid" and f.grid:
            parts.append(f"grid=({_n(f.grid[0])}, {_n(f.grid[1])}), rows={f.rows}, cols={f.cols}")
            # `is not None`, not truthiness: 0.0 is a MEANINGFUL angle (an
            # unrotated grid) and `if f.angle:` silently dropped it, so a grid
            # pattern came back with angle=None (#967 r2).
            if f.angle is not None:
                parts.append(f"angle={_n(f.angle)}")
        if f.members:
            parts.append("members=[" + ", ".join(_pt(p) for p in f.members) + "]")
        return f"sheet.pattern({_member_hole_str(f.member)}, " + ", ".join(parts) + ")"
    if k == "pocket_pattern":
        # declare.pocket_pattern() REJECTS members= (it recomputes the layout from
        # count + pitch/grid), so emit the array CENTRE as at= and the arrangement params —
        # the computed layout then lands where detected. direction= is required for a linear
        # array (else declare defaults to the first in-plane axis, mis-orienting the row).
        parts = [f'kind="{f.pattern}"', f"count={f.count}", f"at={_pt(f.frame.origin)}"]
        if f.pattern == "linear":
            parts.append(f"pitch={_n(f.pitch)}")
            if f.direction:
                parts.append(f"direction={_pt(f.direction)}")
        elif f.pattern == "grid" and f.grid:
            parts.append(f"grid=({_n(f.grid[0])}, {_n(f.grid[1])}), rows={f.rows}, cols={f.cols}")
            if f.angle is not None:
                parts.append(f"angle={_n(f.angle)}")
        return f"sheet.pocket_pattern({_member_pocket_str(f.member)}, " + ", ".join(parts) + ")"
    if k == "slot_pattern":
        # Like pocket_pattern: declare rejects members=, so emit the array centre + arrangement
        # params and let the computed layout land where detected. direction= carries orientation.
        parts = [f'kind="{f.pattern}"', f"count={f.count}", f"at={_pt(f.frame.origin)}"]
        if f.pattern == "linear":
            parts.append(f"pitch={_n(f.pitch)}")
            if f.direction:
                parts.append(f"direction={_pt(f.direction)}")
        elif f.pattern == "grid" and f.grid:
            parts.append(f"grid=({_n(f.grid[0])}, {_n(f.grid[1])}), rows={f.rows}, cols={f.cols}")
            if f.angle is not None:
                parts.append(f"angle={_n(f.angle)}")
        return f"sheet.slot_pattern({_member_slot_str(f.member)}, " + ", ".join(parts) + ")"
    if k == "chamfer":
        return (
            f'sheet.chamfer(axis="{f.axis}", leg1={_n(f.leg1)}, leg2={_n(f.leg2)}, '
            f"angle={_n(f.angle)}, at={_pt(f.frame.origin)})"
        )
    if k == "fillet":
        return f'sheet.fillet(axis="{f.axis}", radius={_n(f.radius)}, at={_pt(f.frame.origin)})'
    if k == "flat":
        # `axis_line`/`stock_span` are the stock identity (#1013). Emitted ALWAYS, not only
        # when non-default: they are what stops two same-sized flats on separate stock
        # collapsing into one callout, and a script that omits them regenerates the very
        # drawing the detection was fixing (Codex #1035 r1). The declared defaults reproduce
        # pre-#1013 grouping, so silence here is not neutral — it is the old bug.
        alignment = "" if f.axis_aligned else ", axis_aligned=False"
        return (
            f'sheet.flat(axis="{f.axis}", across={_n(f.across)}, at={_pt(f.frame.origin)}, '
            f"axis_line={_pt(f.axis_line)}, stock_span={_pt(f.stock_span)}{alignment})"
        )
    if k == "groove":
        return (
            f'sheet.groove(axis="{f.axis}", width={_n(f.width)}, '
            f"diameter={_n(f.diameter)}, at={_pt(f.frame.origin)})"
        )
    if k == "plate":
        return (
            f'sheet.plate(axis="{f.axis}", lo={_n(f.lo)}, hi={_n(f.hi)}, u={_n(f.u)}, v={_n(f.v)})'
        )
    # Kinds with no declarative verb: flag inline so they aren't silently lost. Since #945 every
    # geometric kind has a verb, so this catches the aspect kinds (finish/note/control_frame/
    # datum_ref) and, more usefully, a newly added kind whose emit line nobody wrote.
    return f"# {k} @ {_pt(f.frame.origin)} — no declarative verb yet; drawn by the auto-pass"


def _needs_section(model) -> bool:
    """Mirror planner.plan_sections' trigger so the emitted comment matches what the drawing
    actually does: any Z-axis hole/pattern whose bore has a counterbore, spotface, or blind
    bottom. A pattern carries the bore on its ``member`` hole, so a counterbored bolt circle
    counts too (checking only top-level holes missed it)."""
    for f in model.features:
        if f.kind not in ("hole", "pattern") or f.frame.axis != "z":
            continue
        bore = f.member if f.kind == "pattern" else f
        if bore.cbore or bore.spotface or not bore.through:
            return True
    return False


# The section a feature line is grouped under, and the singular noun the header manifest tallies
# it by. Kinds sharing a section (hole+pattern, chamfer+fillet) are emitted under one header —
# grouping is on CONSECUTIVE runs of the existing feature order (never a reorder: ADR 0014 makes
# the corridor solve order-sensitive), so a kind that recurs in two runs earns two headers.
_SECTION = {
    "hole": "Holes",
    "pattern": "Holes",
    "boss": "Diameters",
    "step": "Turned steps",
    "groove": "Grooves",
    "slot": "Slots",
    "pocket": "Pockets",
    "chamfer": "Edges",
    "fillet": "Edges",
    "flat": "Flats",
    "plate": "Plates",
    "envelope": "Envelope",
    "step_level": "Prismatic steps",
    "authored_dimension": "Dimensions",
    "pmi": "Dimensions",
}
_NOUN = {
    "hole": "hole",
    "pattern": "pattern",
    "boss": "diameter",
    "step": "step",
    "groove": "groove",
    "slot": "slot",
    "pocket": "pocket",
    "chamfer": "chamfer",
    "fillet": "fillet",
    "flat": "flat",
    "plate": "plate",
    "envelope": "envelope",
    "step_level": "step-ladder",
    "authored_dimension": "measured dimension",
    "pmi": "PMI record",
}
# Kinds whose _feature_line carries NO inline comment of its own — the emit loop appends a
# describing comment for these. envelope/step_level/pmi and the no-verb fallback already end in a
# `# …`, so they stay out (double-commenting, and _feature_line is called bare in a test).
_DESCRIBED = frozenset(
    (
        "hole",
        "boss",
        "step",
        "slot",
        "pocket",
        "pattern",
        "chamfer",
        "fillet",
        "flat",
        "plate",
        "groove",
    )
)


def _short_label(f) -> str:
    """A compact human descriptor of *f* for the trailing per-line comment and the section-header
    tally — ``⌀8 THRU ×4`` / ``slot 40 × 120`` / ``6× ⌀3 bolt circle`` / ``R50``. Empty for the
    kinds that already describe themselves inline (envelope/step_level/pmi) or have no verb yet."""
    k = f.kind
    if k == "hole":
        s = f"⌀{_n(f.diameter)}"
        if f.cbore:
            s += " c'bore"
        if f.spotface:
            s += " spotface"
        if f.csink:
            s += " csink"
        s += (
            " THRU"
            if f.through
            else (f" blind {_n(f.depth)}" if f.depth is not None else " blind")
        )
        if f.count and f.count > 1:
            s += f" ×{f.count}"
        return s
    if k == "boss":
        return f"⌀{_n(f.diameter)}"
    if k == "step":
        return f"⌀{_n(f.diameter)} × {_n(f.length)} step"
    if k in ("slot", "pocket"):
        s = f"{k} {_n(f.width)} × {_n(f.length)}"
        return s + (f" × {_n(f.depth)} deep" if k == "pocket" else "")
    if k == "pattern":
        return f"{f.count}× ⌀{_n(f.member.diameter)} {f.pattern.replace('_', ' ')}"
    if k == "chamfer":
        equal = f.leg1 == f.leg2 and f.angle == 45
        return f"C{_n(f.leg1)}" if equal else f"chamfer {_n(f.leg1)} × {_n(f.leg2)}"
    if k == "fillet":
        return f"R{_n(f.radius)}"
    if k == "flat":
        return f"{_n(f.across)} A/F flat"
    if k == "groove":
        return f"groove {_n(f.width)} × ⌀{_n(f.diameter)}"
    if k == "plate":
        return f"plate t{_n(round(float(f.hi) - float(f.lo), 3))}"
    if k == "authored_dimension":
        return str(f.label)
    return ""


def _run_summary(run) -> str:
    """The header tally for a consecutive run of one section: ``8× R50`` when uniform, a short
    ``3× ⌀14 linear, ⌀6 blind`` list otherwise, or just the count when it would run long. Empty
    for a singleton (its own line already reads clearly)."""
    if len(run) <= 1:
        return ""
    counts: dict[str, int] = {}
    for f in run:
        lbl = _short_label(f)
        counts[lbl] = counts.get(lbl, 0) + 1
    counts.pop("", None)
    if not counts:
        return f"{len(run)}×"
    if len(counts) == 1:
        ((lbl, n),) = counts.items()
        return f"{n}× {lbl}"
    if len(counts) > 3:
        return f"{len(run)} total"
    return ", ".join(f"{n}× {lbl}" if n > 1 else lbl for lbl, n in counts.items())


def _manifest(features) -> str:
    """A one-line feature census for the Features banner — ``3 holes · 2 slots · 1 envelope``,
    kinds in first-appearance order, so the editor has a map before scrolling."""
    tally: dict[str, int] = {}
    for f in features:
        tally[f.kind] = tally.get(f.kind, 0) + 1
    parts = []
    for kind, n in tally.items():
        noun = _NOUN.get(kind, kind)
        parts.append(f"{n} {noun}" + ("s" if n != 1 else ""))
    return " · ".join(parts)


def _binding(f, line: str, counts: dict[str, int]) -> str | None:
    """The variable name this feature's line binds, or ``None`` when it emits no statement.

    ``<kind><n>`` in emit order — ``hole1``, ``envelope1``, ``step_level2``. Derived from
    `Feature.kind`, which is already a valid identifier, rather than from a second display
    table: two spellings of one fact is the drift this codebase keeps paying for. The numeric
    suffix is unconditional, so a binding can never shadow the script's own imports (`hole`
    is imported for pattern members; `hole1` is not it).

    **Why every feature is bound, not only the referenced ones.** The alternative — bind only
    where a `dimension(...)` line needs a name — makes the emitter's output depend on the
    dimension source, which is a formatting decision keyed on something unrelated to
    formatting. It is also less useful: a name removes POSITIONAL addressing from the
    artefact generally. `sheet.of(2)` silently retargets the moment a feature line is
    commented out, which is the documented editing workflow (ADR 0011 Amdt 1); `sheet.of(bore1)`
    raises `NameError` at the line you edited. That benefit has nothing to do with dimensions.

    ``None`` for a kind with no declarative verb: its "line" is a comment, and
    ``rotational1 = # …`` does not parse.
    """
    if line.lstrip().startswith("#"):
        return None
    counts[f.kind] = counts.get(f.kind, 0) + 1
    return f"{f.kind}{counts[f.kind]}"


def mirror_model(model):
    """The model the emitted script DECLARES — *model*, plus an envelope when the part's
    overall height would otherwise be unnameable.

    A generated script mirrors the planner's chosen set as explicit `dimension(...)` lines
    (#938), which makes it an AUTHORED set — and an authored set is the complete
    dimensioning, so every measurement in it must be nameable. The overall height usually is:
    an `EnvelopeFeature` carries a `height` parameter. On a part with no envelope feature the
    compiler falls back to the bounding box, and `_compile_overall_height` deliberately
    refuses that fallback under an authored set — a script "must not acquire a measurement
    its author had no way to ask for" (#925). Mirroring such a part would silently drop its
    overall height, which is #889's bug at the authored level.

    So the script declares the envelope and names only its height. That is honest rather than
    a workaround: the engine was already dimensioning the bounding box, and the declaration
    writes down what it was doing implicitly. Naming only `height` keeps the drawing
    identical — the width and depth stay omitted exactly as the planner left them.
    """
    if any(f.kind == "envelope" for f in model.features):
        return model, None
    from draftwright.model.compiled import compile_dimensions

    if compile_dimensions(model).ladder("overall_height") is None:
        return model, None  # the compiler withholds it (Z-turned, rotational OD)
    from dataclasses import replace

    from draftwright.model.declare import _envelope_from_bbox

    # The SAME construction `sheet.envelope()` uses, not a copy. Hand-rolling it here hardcoded
    # the frame origin to (0, 0, 0) — the bbox centre only for a part centred on the origin, and
    # 6 mm out in Y on the corpus flange — so the synthesised envelope disagreed with both the
    # detector and the declared verb. That is the fourth instance of #977's signature: a
    # constructed envelope that does not match what detection would produce (#976).
    env = _envelope_from_bbox(model.bbox)
    # Returned ALONGSIDE the model rather than stamped onto it. The first cut set a private
    # marker on the frozen `EnvelopeFeature` and rediscovered it later by position and size —
    # emitter bookkeeping masquerading as model state, on a public IR type (#944 review).
    # Which feature the emitter synthesised is the emitter's own fact; it travels out-of-band.
    return replace(model, features=[*model.features, env]), env


def unmirrored_dimensions(model) -> list[str]:
    """Dimensions the COMPILER approved that no emitted line would name.

    The one production answer to "can this script claim to be complete?", and the reason it
    is here rather than in a test: the first cut asked a weaker question — whether an
    unsuppressed `plan_dimensions()` group belonged to a feature with no declarative verb —
    which misses every dimension created OUTSIDE `plan_dimensions`. Locations already prove
    such paths exist, so a compiled location or ladder on an otherwise nameable feature left
    the script printing "THIS IS THE COMPLETE SET" while silently omitting it: a real
    user-facing third state, not merely a missing diagnostic (#947 review).

    Compares the compiled approved set against the requests the emitter would actually write,
    so a new compiler-owned dimension is caught by construction rather than by someone
    remembering to extend the check. The test calls this same function — a test-only
    approximation of a production rule is two spellings of one fact, which is the defect this
    codebase keeps paying for.
    """
    from draftwright.model.compiled import compile_dimensions, resolve_feature

    declared, synthesised = mirror_model(model)
    # A request only counts if the script can WRITE it. A feature whose line is a comment
    # binds no variable, so `dimension(<nothing>, role)` cannot be emitted — the request
    # exists in the walk and would never reach the file. Checking the request alone reported
    # a turned part as fully mirrorable while its rotational dimensions had no name.
    requested = {
        (id(feature), role)
        for feature, role, _disc in _mirrored_requests(declared, synthesised)
        if not _feature_line(feature).lstrip().startswith("#")
    }

    def _asked(feature, parameter_id: str) -> bool:
        # A line names either the full parameter id ("bore.diameter") or the bare role
        # ("bore"); a correlated set emits one line covering N members.
        return (id(feature), parameter_id) in requested or (
            id(feature),
            parameter_id.split(".")[0],
        ) in requested

    # ONE interpreter, the same one the emitter serialises (#946). This used to walk
    # `plan.groups`, `plan.locations` and `plan.ladders` itself, with its own copy of the
    # ladder→parameter mapping — so the completeness GATE re-derived the compiler's answer
    # even after the emitter stopped. A category the compiler grew would have been mirrored
    # by `_mirrored_requests` and still reported missing here, or worse, the reverse.
    #
    # `model`, not `declared`: the synthesised envelope adds width and depth parameters the
    # emitter deliberately omits, so compiling the mirror model would report them missing.
    missing: list[str] = []
    for intent in compile_dimensions(model).addressable():
        feature = resolve_feature(intent.ref)
        if feature is None:
            # Model-level — the overall height of a part with no envelope feature. Only the
            # synthesised envelope can name it, until #976 gives it a declarative target.
            if synthesised is None or (id(synthesised), "height.length") not in requested:
                missing.append("(bounding box).overall_height")
        elif not _asked(feature, intent.role):
            missing.append(f"{feature.kind}.{intent.role}")
    return sorted(set(missing))


def _is_mirrorable(model) -> bool:
    """Can every dimension the compiler approved be named by an emitted line?

    Derived from :func:`unmirrored_dimensions`, so "the emitter says it can mirror" and "the
    emitter actually can" are the same computation rather than two that agree until they do
    not. A model that fails keeps `auto_dimensions()` and says why — a mirrored set that
    silently omitted a dimension would claim a completeness it does not have, which is worse
    than not mirroring.
    """
    return not unmirrored_dimensions(model)


def _mirrored_requests(declared, declared_envelope=None):
    """One `(feature, role)` per dimension the planner CHOSE — the mirror's content.

    Emitted per addressable UNIT, never per member: a `step_height` ladder and a rotational
    body's bores are one `AddressableDimension` holding N, so one line drops the set and
    there is no member line to mislead (ADR 0016 identity tier 3).

    From the planner's INTENT, never from placed annotations. Walking the drawing is the
    obvious way to build a mirror and it is wrong: a dimension the solver dropped would
    vanish from the regenerated script and could never be recovered by re-running it, and an
    unrelated layout change would silently rewrite version-controlled source.
    """
    from draftwright.model.compiled import compile_dimensions, resolve_feature

    # ONE compiler result, serialised — not three sources reassembled (#946). This used to
    # walk `plan_dimensions()` for parameters, `compile_dimensions().locations` for positions
    # and a synthesised envelope for the overall height, with comments explaining which
    # compiler fact each reconstructed. A category the compiler grew rendered correctly and
    # vanished from generated scripts until someone extended this function;
    # `RenderableDimensionPlan.addressable()` is now the single answer, and its `_ADDRESSABLE`
    # roster is guarded so a new collection cannot skip it.
    out: list[tuple] = []
    for intent in compile_dimensions(declared).addressable():
        feature = resolve_feature(intent.ref)
        if feature is None:
            # A model-level intent — the part's overall height, which belongs to no feature.
            # The synthesised envelope is what makes it nameable; `mirror_model` supplies it.
            if declared_envelope is None:
                continue
            out.append((declared_envelope, "height.length", None))
            continue
        if declared_envelope is not None and feature is declared_envelope:
            # The synthesised envelope exists ONLY to make the overall height nameable, so
            # name its height and nothing else — width and depth stay omitted exactly as the
            # planner left them on a model that declared no envelope (`mirror_model`). The
            # compiler reaches that height twice, as the ladder and as the parameter, and
            # `addressable()` has already collapsed them to one intent.
            if intent.role != "height.length":
                continue
        out.append((feature, intent.role, None))
    return out


def _dimension_block(model, names: dict[int, str], synthesised_envelope=None) -> list[str]:
    """The authored set as `sheet.dimension(...)` declarations, or the `auto_dimensions()` line.

    Emitted AFTER the features, because each line names a feature by the variable that
    feature's line binds — which is the whole reason #922 needed nameable identity first. A
    positional spelling (`sheet.dimension(3, "width")`) would break the moment a user
    comments a feature out, which is the documented editing workflow.

    A generated script must state its source either way (ADR 0016 / #874): a dimension the
    script does not name only means "omitted" inside a set that says it is complete.
    """
    if model.authored_dimensions is None and not _is_mirrorable(model):
        # A feature with no declarative verb carries planned dimensions, so a mirrored set
        # would silently omit them and claim completeness it does not have (#938).
        # WHY, specifically. `_is_mirrorable` now fails for any dimension the compiler
        # approved and no line can name — not only the no-declarative-verb case — so blaming
        # #945 unconditionally would misdirect a reader whenever the cause is something else,
        # and could print an empty kind list (#947 review).
        missing = unmirrored_dimensions(model)
        unnameable = sorted(
            {f.kind for f in model.features if _feature_line(f).lstrip().startswith("#")}
        )
        why = (
            [
                f"# {', '.join(unnameable)} has no declarative verb (flagged in the features",
                "# above), so it binds no name and its dimensions cannot be declared here.",
                "# Tracked as draftwright#945; once that lands this part declares its",
                "# dimensions line by line like every other.",
            ]
            if unnameable
            else [
                "# the emitter has no line for: " + ", ".join(missing),
                "# — a dimension the compiler approved that no declaration can name.",
            ]
        )
        return [
            "# The planner selects the dimensions for this part.",
            "# WHY, and it is a gap rather than a choice:",
            *why,
            "# Declaring only the OTHERS would produce a set that claims to be complete and",
            "# is not, so the whole set stays automatic.",
            "sheet.auto_dimensions()",
        ]
    requests = (
        [(a.feature, a.role, a.discriminator) for a in model.authored_dimensions]
        if model.authored_dimensions is not None
        # A detected model has no authored set, so the script MIRRORS the planner's choice as
        # explicit lines instead of `auto_dimensions()` (#938). That is what makes an
        # automatic dimension commentable: before this, the promise "comment a line out to
        # drop it" held for features and not for dimensions, so the only way to drop one
        # dimension was to drop its whole feature — losing the callout, the centre marks and
        # the location with it.
        else _mirrored_requests(model, synthesised_envelope)
    )
    out = [
        "# ── Dimensions ────────────────────────────────────────────────────────────────",
        "# THIS IS THE COMPLETE SET (ADR 0016). A measurement with no line here is omitted",
        "# deliberately — comment a line out to drop that dimension, add one to declare it.",
        # The role vocabulary was undiscoverable from the artefact: an editor had to guess a
        # string or read the source (#963). Typing narrows it now, but a generated file is
        # read by people and agents who may have neither, so it says where the answer is.
        '# To add one: sheet.dimension(<name>, "<id>") — a feature\'s ids are listed by',
        "# <name>.dimension_ids(), and naming one it lacks reports the ones it has.",
        # The VERB, not just the comment above it. `dimension(...)` lines imply this source
        # on their own, so writing it was optional for a non-empty set — but an EMPTY
        # authored set has no line to imply it from, and the script then said its source in a
        # comment only and failed the mandatory-source check at build (#933 review). Emitting
        # it unconditionally also means an authored script states its source the same way an
        # automatic one does, rather than in prose a reader has to trust.
        "sheet.authored_dimensions()",
    ]
    for feature, role, discriminator in requests:
        name = names.get(id(feature))
        if name is None:
            # Reachable for a kind with no declarative verb (its line is a comment, so it
            # binds nothing). Refusing the SCRIPT is right: emitting the rest would produce
            # one that draws a different set from the model it came from, which is the
            # divergence the blanket refusal existed to prevent — just narrowed from "any
            # authored model" to "an authored dimension on an unemittable feature".
            raise ValueError(
                f"emit_sheet_script(): cannot name the {feature.kind} carrying the "
                f"dimension {role!r} — that kind has no declarative verb, so the generated "
                "script has no variable to reference it by"
            )
        # Double quotes, matching the house style of every other emitted string argument
        # (`axis="z"`); `!r` would render single and make the file read as two dialects.
        # A full discriminated id already names the variant, so restating it as `axis=`
        # would be redundant — and would make the emitted line the only place two spellings
        # of one thing appear side by side (#965 review).
        axis = (
            f', axis="{discriminator}"'
            if discriminator and "." not in role[role.find(".") + 1 :]
            else ""
        )
        out.append(f'sheet.dimension({name}, "{role}"{axis})')
    return out


def _feature_block(features, part_envelope=None) -> tuple[list[str], dict[int, str]]:
    """The emitted feature lines plus ``{id(feature): binding}`` for the names they bind.

    The map is RETURNED rather than recomputed by the dimension block, which needs the same
    names. Recomputing would be a second derivation of one fact — and this one would be
    silently wrong rather than loudly: a mismatch emits `sheet.dimension(hole2, …)` naming
    the wrong hole, which still runs.

    Lines are grouped under section sub-headers (with a repeat tally) and each carries a
    trailing describing comment. Runs are CONSECUTIVE in the given order — no reorder.
    """
    if not features:
        return ["# ── Features: none detected ──"], {}
    out = [f"# ── Features ({len(features)}): {_manifest(features)} ──"]
    counts: dict[str, int] = {}
    names: dict[int, str] = {}
    i = 0
    while i < len(features):
        section = _SECTION.get(features[i].kind, "Other")
        j = i
        while j < len(features) and _SECTION.get(features[j].kind, "Other") == section:
            j += 1
        run = features[i:j]
        summary = _run_summary(run)
        out.append(f"#   {section}" + (f" · {summary}" if summary else "") + " ─────")
        for f in run:
            line = _feature_line(f, part_envelope)
            name = _binding(f, line, counts)
            if name is not None:
                names[id(f)] = name
                line = f"{name} = {line}"
            if f.kind in _DESCRIBED:
                lbl = _short_label(f)
                if lbl:
                    line += f"   # {lbl}"
            out.append(line)
        i = j
    return out, names


_HEADER = '''"""Editable drawing — generated by draftwright (declarative Sheet script).

Each line below declares one feature and binds a name for it. Comment a line out to drop
that feature; edit a value freely; chain .tolerance(lo, hi) / .fit("H7") onto any diameter.
Then re-run this file.

The names are how you refer to a feature elsewhere in the script — sheet.of(hole1) to
decorate one after the fact. They are bound rather than addressed by position on purpose:
comment out a feature and any line naming it fails loudly, instead of silently retargeting
onto its neighbour.

The values are DETECTED off the geometry (honest for a STEP / recovered solid). If you
built the part yourself, wire your object into the `part = …` seam and swap a numbered
line for a reference — e.g.  sheet.hole(my_bore)  — to read the size off the object.
"""'''


def emit_sheet_script(
    model,
    part_expr: str,
    stem: str,
    *,
    title: str,
    number: str,
    drawn_by: str = "",
    tolerance: str = "ISO 2768-m",
    scale=None,
    page=None,
    material: str = "",
    date: str = "",
    revision: str = "A",
    company: str = "",
    frame: bool = False,
    zones: bool = False,
    projection: str | None = None,
    object_ref: bool = False,
    formats: Sequence[str] = ("pdf",),
) -> str:
    """The generated declarative ``Sheet`` script text for a detected *model*.

    *part_expr* is the Python that binds ``part`` (a STEP ``import_step`` or a ``part = …``
    seam); *stem* is the output basename the script exports to. The title-block / layout aspects
    (``drawn_by``/``tolerance``/``scale``/``page``, #474) are emitted into the ``Sheet(...)``
    constructor only when non-default, so a plain drawing keeps a clean one-line constructor.
    The script ends with the explicit lifecycle ``drawing = sheet.build()`` then
    ``drawing.export(...)`` (#968), so the finalized :class:`~draftwright.drawing.Drawing` has a
    name an editor can lint or inspect without rewriting the tail or building twice. *formats*
    (the CLI's ``--format``, #709) is always spelled out on that call, so a re-run reproduces the
    requested outputs.

    AP242 PMI cannot be re-extracted from the ``import_step`` seam, so detected dimensional PMI is
    emitted as declared Sheet dimensions; unsupported raw PMI records are kept as explicit
    ``sheet.add(PmiFeature(...))`` fallbacks (#503 / #422).

    A model carrying an **authored** dimension set emits `sheet.dimension(feature, role)`
    declarations after the features (#922). That was refused until every feature had a name to
    reference: the only way to name one was its position, and the documented workflow is to
    comment a feature line out and re-run — which shifts every later index and silently
    retargets the declarations onto their neighbours. #931 and #932 removed positional
    addressing from the artefact, so the declarations can now be written honestly."""
    # The script declares this model — `model` plus an envelope when the overall height would
    # otherwise be unnameable under the mirrored (authored) set. BEFORE the import scan, since
    # a synthesised envelope needs `EnvelopeFeature` imported like a detected one.
    model, _synth_env = mirror_model(model)
    # Every constructor a member template can name has to be listed here. The pattern verbs
    # take their member as a nested `hole(...)` / `pocket(...)` / `slot(...)` call — declare
    # rejects `members=` and recomputes the layout — so the member constructor is a name the
    # generated file uses, and a missing entry is a NameError on the first line that runs
    # (#957 review; pocket/slot patterns were emitting unrunnable scripts).
    model_imports = set()
    if any(f.kind in ("hole", "pattern") for f in model.features):
        model_imports.add("hole")
    if any(f.kind == "pocket_pattern" for f in model.features):
        model_imports.add("pocket")
    if any(f.kind == "slot_pattern" for f in model.features):
        model_imports.add("slot")
    if any(f.kind == "envelope" for f in model.features):
        model_imports.update(["EnvelopeFeature", "Frame"])
    if any(f.kind == "pmi" for f in model.features):
        model_imports.update(["Frame", "PmiFeature"])
    # Only carry an aspect into the emitted constructor when it differs from build_drawing's
    # default (mirrors the CLI's inert-flag test) — an unset aspect stays off the script.
    ctor = [f"title={title!r}", f"number={number!r}"]
    if drawn_by:
        ctor.append(f"drawn_by={drawn_by!r}")
    if tolerance != "ISO 2768-m":
        ctor.append(f"tolerance={tolerance!r}")
    if scale is not None:
        ctor.append(f"scale={scale!r}")
    if page is not None:
        ctor.append(f"page={page!r}")
    if material:
        ctor.append(f"material={material!r}")
    if date:
        ctor.append(f"date={date!r}")
    if revision != "A":  # "A" is build_drawing's own default
        ctor.append(f"revision={revision!r}")
    if company:
        ctor.append(f"company={company!r}")
    if frame:
        ctor.append("frame=True")
    if zones:
        ctor.append("zones=True")
    if projection:
        ctor.append(f"projection={projection!r}")
    from draftwright.model.declare import _envelope_from_bbox

    feature_lines, _names = _feature_block(model.features, _envelope_from_bbox(model.bbox))
    # Narrowed to what the BODY actually names. The set above is derived from feature kinds,
    # which over-imports the moment a kind stops emitting a constructor: since #976 a
    # whole-part envelope emits `sheet.envelope()`, so `EnvelopeFeature` and `Frame` were
    # imported and never used, and a user linting their own generated script got F401 on line
    # three. Deriving from the emitted text cannot over-import by construction, and cannot
    # under-import either — a name absent from the body is a name the body does not need.
    _body = "\n".join(feature_lines)
    # Called as a NAME, not merely present as a substring: `hole` occurs inside `sheet.hole(`,
    # so a substring test keeps the import for every script that uses the fluent verb and needs
    # no constructor — which is four of the nineteen corpus fixtures.
    model_imports = {n for n in model_imports if re.search(rf"(?<![.\w]){n}\s*\(", _body)}
    lines = [
        _HEADER,
        "from draftwright import Sheet",
        *(
            ["from draftwright.model import " + ", ".join(sorted(model_imports))]
            if model_imports
            else []
        ),
        "",
        part_expr,
        "",
        f"sheet = Sheet(part, {', '.join(ctor)})",
        "",
        # The script must SAY where its dimensions come from (ADR 0016 / #874): an omitted
        # dimension only means something inside a set that says it is complete. The planner's
        # set is stated here; an AUTHORED set is stated after the features instead, because
        # each of its lines names a feature by the variable that feature's line binds.
        # Every generated script now declares its dimensions below the features (#938), so
        # the source is stated here as a pointer rather than inline: the lines name features
        # by the variables those features' lines bind, and cannot precede them.
        "# The dimension source is DECLARED below the features (ADR 0016).",
        "",
        # For a live-source part (#771), the values below were read off YOUR objects — point
        # each line back at the object to keep it a single source of truth (a STEP-sourced
        # script has no such objects, so this note is emitted only for object inputs).
        *(
            [
                "# Object-reference tip: you built these objects, so swap a numbered arg for the",
                "# object itself to read the size off it — e.g.  sheet.step(journal)  /",
                "#  sheet.hole(m3_bore).thread('M3x0.5')  — no numbers restated (ADR 0011 declare).",
                "",
            ]
            if object_ref
            else []
        ),
        # One commentable line per feature, grouped under section sub-headers with a describing
        # comment on each (ADR 0011 Amdt 1: still one declared feature per line — comment out, edit
        # a value, re-run). Runs are consecutive in feature order; never reordered (ADR 0014).
        *feature_lines,
        "",
        *_dimension_block(model, _names, _synth_env),
        "",
        "# ── Views ─────────────────────────────────────────────────────────────────────",
        "# front / plan / side / iso are always produced.",
    ]
    if _needs_section(model):
        lines.append("# Section A–A auto-triggers from the counterbore/blind bore above.")
    # Build and export as two statements, so the finalized Drawing has a name (#968). That is
    # the lifecycle the architecture already has — Sheet declares intent, `build()` compiles and
    # solves placement, `Drawing` is the artefact that gets critiqued and serialised — and an
    # editor wanting to lint, inspect `drawing.model()` or export twice can now do it without
    # rewriting the tail or paying for a second build. `Sheet.export` stays as shorthand for
    # handwritten programs; it is this GENERATED tail that has an editor to serve.
    #
    # `formats` is always spelled out, unlike the constructor's non-default-only aspects (#709).
    # `Drawing.export` treats a missing `formats` as the legacy svg=/dxf= call and writes SVG +
    # DXF, where `Sheet.export` defaults to PDF — so the bare call is not the default, it is a
    # different one, and suppressing the argument here would quietly turn every generated
    # script's PDF into a pair of vector files.
    fmts = tuple(formats)
    lines += [
        "",
        "# ── Build ─────────────────────────────────────────────────────────────────────",
        "# `build()` returns the finalized Drawing: critique it with `drawing.lint()` or read",
        "# `drawing.model()` here, then export the same object — no second build.",
        "drawing = sheet.build()",
        f"drawing.export({stem!r}, formats={fmts!r})",
    ]
    return "\n".join(lines) + "\n"


def resolve_object_spec(spec: str) -> tuple[Shape, str]:
    """Resolve a ``module:attr`` (or ``path/to/file.py:attr``) spec into ``(build123d object,
    import seam)`` (ADR 0011, #469). The attribute is imported; a zero-argument callable (a
    ``make_part()`` factory) is called. The returned seam is the Python that re-binds ``part``
    in the generated script, so the drawing references your **live parametric source**, not a
    frozen STEP.

    SECURITY: importing the target executes its module-level code — the same trust as running
    the file yourself."""
    import importlib
    import importlib.util
    import inspect
    import os
    import sys

    mod_ref, sep, name = spec.rpartition(":")
    if not sep or not name.isidentifier() or not mod_ref:
        raise ValueError(f"object spec must be 'module:attr' or 'file.py:attr' (got {spec!r})")

    if mod_ref.endswith(".py"):
        path = Path(mod_ref).resolve()
        ispec = importlib.util.spec_from_file_location(path.stem, path)
        if ispec is None or ispec.loader is None:
            raise ValueError(f"cannot load module from {mod_ref!r}")
        module = importlib.util.module_from_spec(ispec)
        # Force the helper file's OWN directory to the FRONT of sys.path, with the invocation cwd
        # just behind it, so its repo-relative / sibling imports resolve like `python file.py` (the
        # script dir wins a name clash) — spec_from_file_location adds NEITHER (#488). Remove any
        # existing occurrence first, then re-insert in a fixed order: a plain `not in sys.path`
        # guard can't reorder a dir that's ALREADY on the path (e.g. a driver run as
        # `python tools/driver.py` puts the helper dir on sys.path), so cwd could otherwise land
        # ahead of it and win the clash, AND the in-process build would diverge from the standalone
        # re-run seam (#491 review). Removing then front-inserting makes the order deterministic and
        # identical between build and re-run; the seam bakes both as resolve-time absolute literals.
        file_dir = str(path.parent)
        cwd = os.getcwd()
        for _p in (cwd, file_dir):
            while _p in sys.path:
                sys.path.remove(_p)
        for _p in (cwd, file_dir):  # cwd first, file_dir last -> file_dir at index 0 (wins)
            sys.path.insert(0, _p)
        # Register before exec so a self-referential target resolves (a dataclass whose
        # forward-ref annotations get typing.get_type_hints'd, a module reading
        # sys.modules[__name__], import-time pickling). The seam does the same on re-run.
        sys.modules[ispec.name] = module
        try:
            ispec.loader.exec_module(module)
        except Exception as e:
            raise ValueError(f"{spec!r}: importing {mod_ref!r} failed: {e}") from e
        seam = (
            "import importlib.util as _ilu, sys as _sys\n"
            f"for _p in ({cwd!r}, {file_dir!r}):\n"
            "    while _p in _sys.path:\n        _sys.path.remove(_p)\n"
            f"for _p in ({cwd!r}, {file_dir!r}):\n"
            "    _sys.path.insert(0, _p)\n"
            f"_spec = _ilu.spec_from_file_location({path.stem!r}, {str(path)!r})\n"
            "_mod = _ilu.module_from_spec(_spec)\n_sys.modules[_spec.name] = _mod\n"
            "_spec.loader.exec_module(_mod)"
        )
        ref = f"_mod.{name}"
    else:
        cwd = os.getcwd()
        if cwd not in sys.path:
            sys.path.insert(0, cwd)  # allow a cwd-relative import
        try:
            module = importlib.import_module(mod_ref)
        except ImportError as e:
            raise ValueError(f"{spec!r}: cannot import module {mod_ref!r}: {e}") from e
        # Record the invocation cwd (where the module resolved) on the generated script's path,
        # so `from mod import …` works from any working directory — Python puts only the
        # *script's* dir on sys.path, not the cwd, so a bare import would otherwise fail.
        # (For an installed module the insert is harmless; the import works regardless.)
        seam = (
            f"import sys as _sys\nif {cwd!r} not in _sys.path:\n    _sys.path.insert(0, {cwd!r})\n"
            f"from {mod_ref} import {name} as _obj"
        )
        ref = "_obj"

    if not hasattr(module, name):  # `hasattr`, not a None sentinel: a name bound to None exists
        raise ValueError(f"{spec!r}: {name!r} not found in {mod_ref!r}")
    obj = getattr(module, name)

    called = False
    if callable(obj) and not isinstance(obj, Shape):
        required = [
            p
            for p in inspect.signature(obj).parameters.values()
            if p.default is p.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        if required:
            raise ValueError(
                f"{spec!r}: {name} needs arguments — reference a built object instead"
            )
        obj, called = obj(), True

    if not isinstance(obj, Shape):
        raise ValueError(f"{spec!r}: resolved to {type(obj).__name__}, not a build123d Shape")

    return obj, f"{seam}\npart = {ref}{'()' if called else ''}"


def generate_sheet_script(
    step_file: str | Shape,
    out: str | None = None,
    *,
    title: str | None = None,
    number: str = "DWG-001",
    tolerance: str = "ISO 2768-m",
    drawn_by: str = "",
    scale=None,
    page=None,
    material: str = "",
    date: str = "",
    revision: str = "A",
    company: str = "",
    frame: bool = False,
    zones: bool = False,
    projection: str | None = None,
    pmi: str = "off",
    part_expr: str | None = None,
    formats: Sequence[str] = ("pdf",),
) -> str:
    """Write a declarative ``Sheet``-DSL script for *step_file* (a STEP path or a build123d
    object). Returns the path to the generated ``.py``. **The** script emitter, since #940
    retired the imperative one it used to sit alongside.

    ``tolerance``/``drawn_by``/``scale``/``page`` are the title-block / layout aspects (#474):
    when non-default they are emitted into the generated ``Sheet(...)`` so a re-run reproduces
    them. The script ends ``drawing = sheet.build()`` then ``drawing.export(...)`` (#968), with
    ``formats`` (the CLI's ``--format``, #709) always spelled out on that call so a re-run
    reproduces the requested outputs. ``pmi`` is threaded to detection so AP242 PMI features
    surface (flagged inline).
    *part_expr*, when given, overrides the ``part = …`` seam — e.g. the import seam from
    :func:`resolve_object_spec` so the script references a live module (#469)."""
    is_shape = isinstance(step_file, Shape)
    stem = out or ("drawing" if is_shape else Path(step_file).stem)
    for _ext in (".py", ".svg", ".dxf"):
        if stem.endswith(_ext):
            stem = stem[: -len(_ext)]
            break
    title = title or (Path(stem).name.replace("_", " ").upper() if not is_shape else "DRAWING")

    if part_expr is not None:
        pass  # caller-supplied seam (e.g. an import of a live module, #469)
    elif is_shape:
        part_expr = "part = ...   # ← wire in your build123d object (built above)"
    else:
        # absolute so the generated script runs from any working directory
        abspath = str(Path(step_file).resolve())
        part_expr = f"from build123d import import_step\npart = import_step({abspath!r})"

    model = detect_part_model(step_file, pmi=pmi)
    script = emit_sheet_script(
        model,
        part_expr,
        stem,
        title=title,
        number=number,
        drawn_by=drawn_by,
        tolerance=tolerance,
        scale=scale,
        page=page,
        material=material,
        date=date,
        revision=revision,
        company=company,
        frame=frame,
        zones=zones,
        projection=projection,
        object_ref=is_shape,  # a live Shape / resolved object-spec has objects to reference (#771)
        formats=formats,
    )
    py_path = f"{stem}.py"
    Path(py_path).write_text(script, encoding="utf-8")  # the script has box-drawing / × / ← glyphs
    return py_path
