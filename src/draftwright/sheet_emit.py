"""sheet_emit — the ``Sheet``-script emitter (ADR 4 (was 0011 Amendment 1), #461).

Mode 3 of the three authoring modes: *generate an editable beautiful-Python script*. Walk a
**detected** :class:`PartModel` and print a :class:`~draftwright.Sheet` script — one commentable
line per feature — that the user edits / comments-out / extends, then re-runs.

**Detected STEP input only writes numbers (the part-seam form, ADR 4 (was 0011 Amdt 1) decision).** For a
STEP file or a recovered solid the number *is* the ground truth, so a detected value is honest. We
never fabricate a build123d part to chase a number-free layer — a synthesised solid silently drops
what detection didn't model (a misread band, a thread's true form, an unrecognised relief) yet reads
as authoritative. A caller who *has* the objects (mode 3b) can expose a features dataclass with a
Shape-valued ``body``; the emitter references a named object only where source polarity, geometry,
and mutual one-to-one correspondence independently establish it (#1041). Every unavailable or
ambiguous correspondence retains the complete detected numeric declaration.

Kinds with no declarative verb are flagged inline — never silently dropped — and left to the
auto-pass that runs over the declared model on re-run. Every *geometric* kind now has one
(``rotational`` was the last, #945, keyword-only — see :func:`draftwright.model.rotational`);
an unsupported kind stays an explicit comment, so the branch guards against a new kind
arriving unemitted. Imported authored
dimensions, including AP242 dimensional PMI, emit as Sheet ``measured_dimension(...)``
declarations (#873 — never the transitional ``dimension`` overload, so a regenerated script is
not born deprecated).
Fidelity: the script reproduces a lint-clean drawing of the same features. The generated script is
validated against the direct build for prismatic, slot/pattern, section, and turned/rotational
fixtures (#472).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from numbers import Real
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, cast

from build123d import Shape

from draftwright._core import _dimension_draft
from draftwright.build_options import BuildOptions
from draftwright.builder import (
    _detect_part_model_analysis,
    _is_expected_candidate_build_failure,
    build_drawing,
)
from draftwright.fits import FitClass
from draftwright.inspection import (
    INSPECTION_SCHEMA,
    InspectionUnavailableError,
)
from draftwright.inspection import (
    _document as _inspection_document,
)
from draftwright.model.ir import (
    KnurlRequirement,
    NominalRequirement,
    ThreadOperation,
    ThreadRequirement,
    ToleranceDecoration,
)
from draftwright.replay_assessment import (
    assessment_sidecar_path,
    invalidate_replay_assessment,
)
from draftwright.reporting import (
    ReportUnavailableError,
    project_feature_occurrence_ids,
    project_occurrences,
    write_json_document,
)
from draftwright.sheet_feature_lines import (
    _angular_refs_arg as _angular_refs_arg,
)
from draftwright.sheet_feature_lines import (
    _authored_n as _authored_n,
)
from draftwright.sheet_feature_lines import (
    _authored_pt as _authored_pt,
)
from draftwright.sheet_feature_lines import (
    _bbox_arg as _bbox_arg,
)
from draftwright.sheet_feature_lines import (
    _circular_refs_arg as _circular_refs_arg,
)
from draftwright.sheet_feature_lines import (
    _control_frame_line as _control_frame_line,
)
from draftwright.sheet_feature_lines import (
    _cylindrical_refs_arg as _cylindrical_refs_arg,
)
from draftwright.sheet_feature_lines import (
    _cylindrical_refs_expr as _cylindrical_refs_expr,
)
from draftwright.sheet_feature_lines import (
    _datum_ref_line as _datum_ref_line,
)
from draftwright.sheet_feature_lines import (
    _direction as _direction,
)
from draftwright.sheet_feature_lines import (
    _finish_line as _finish_line,
)
from draftwright.sheet_feature_lines import (
    _hole_group_args as _hole_group_args,
)
from draftwright.sheet_feature_lines import (
    _hole_line as _hole_line,
)
from draftwright.sheet_feature_lines import (
    _knurl_arg as _knurl_arg,
)
from draftwright.sheet_feature_lines import (
    _knurl_requirement_expr as _knurl_requirement_expr,
)
from draftwright.sheet_feature_lines import (
    _machined_feature_line as _machined_feature_line,
)
from draftwright.sheet_feature_lines import (
    _measured_dimension_line as _measured_dimension_line,
)
from draftwright.sheet_feature_lines import (
    _member_hole_str as _member_hole_str,
)
from draftwright.sheet_feature_lines import (
    _member_pocket_str as _member_pocket_str,
)
from draftwright.sheet_feature_lines import (
    _member_slot_str as _member_slot_str,
)
from draftwright.sheet_feature_lines import (
    _n as _n,
)
from draftwright.sheet_feature_lines import (
    _note_line as _note_line,
)
from draftwright.sheet_feature_lines import (
    _parameter_n as _parameter_n,
)
from draftwright.sheet_feature_lines import (
    _profile_feature_line as _profile_feature_line,
)
from draftwright.sheet_feature_lines import (
    _pt as _pt,
)
from draftwright.sheet_feature_lines import (
    _pts_arg as _pts_arg,
)
from draftwright.sheet_feature_lines import (
    _raw_pmi_expr as _raw_pmi_expr,
)
from draftwright.sheet_feature_lines import (
    _raw_pmi_line as _raw_pmi_line,
)
from draftwright.sheet_feature_lines import (
    _stock_feature_line as _stock_feature_line,
)
from draftwright.sheet_feature_lines import (
    _thread_arg as _thread_arg,
)
from draftwright.sheet_feature_lines import (
    _thread_requirement_expr as _thread_requirement_expr,
)
from draftwright.sheet_feature_lines import (
    _tuple_arg as _tuple_arg,
)
from draftwright.sheet_object_source import (
    _REFERENCE_DIA_TOL as _REFERENCE_DIA_TOL,
)
from draftwright.sheet_object_source import (
    _REFERENCE_EXTERNAL as _REFERENCE_EXTERNAL,
)
from draftwright.sheet_object_source import (
    _REFERENCE_POS_TOL as _REFERENCE_POS_TOL,
)
from draftwright.sheet_object_source import (
    _candidate_external as _candidate_external,
)
from draftwright.sheet_object_source import (
    _object_references as _object_references,
)
from draftwright.sheet_object_source import (
    _ObjectSource as _ObjectSource,
)
from draftwright.sheet_object_source import (
    _reference_geometry_matches as _reference_geometry_matches,
)
from draftwright.sheet_object_source import (
    _resolve_object_source as _resolve_object_source,
)
from draftwright.view_plan import (
    PRINCIPAL_VIEW_NAMES,
    ViewConstraints,
    ViewSpec,
    validate_projection,
)

_log = logging.getLogger(__name__)

# The evidence document written beside a generated script. Derived from the returned
# script path so the writer and every caller name the same file from one place.
_INSPECTION_SUFFIX = ".draftwright-inspection.json"


def _is_inspection_document(path: str) -> bool:
    """Is the file at *path* one of ours — a readable inspection document of this schema?"""

    try:
        with open(path, encoding="utf-8") as handle:
            return bool(json.load(handle).get("schema") == INSPECTION_SCHEMA)
    except (OSError, ValueError, AttributeError):
        return False


def inspection_sidecar_path(py_path: str) -> str:
    """Return the evidence document that accompanies the generated script *py_path*."""

    return f"{py_path.removesuffix('.py')}{_INSPECTION_SUFFIX}"


def _general_tolerance_line(feature) -> str:
    kwargs = [f"statement={feature.statement!r}"] if feature.statement else []
    if feature.source_id:
        kwargs.append(f"source_id={feature.source_id!r}")
    if feature.part21_id:
        kwargs.append(f"part21_id={feature.part21_id!r}")
    if feature.source_ids:
        kwargs.append(f"source_ids={feature.source_ids!r}")
    suffix = f", {', '.join(kwargs)}" if kwargs else ""
    return f"sheet.general_tolerance({feature.designation!r}{suffix})"


_SOURCE_NOTE_LINES = {"finish": _finish_line, "note": _note_line}


def _feature_line(
    f,
    part_envelope=None,
    *,
    origin_ref: str | None = None,
    object_ref: str | None = None,
    exact_parameter: str | None = None,
    exact_step_length: bool = False,
    profile_group: str | None = None,
) -> str:
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
    if k == "general_tolerance":
        return _general_tolerance_line(f)
    if k == "default_surface_finish":
        kwargs = [f"statement={f.statement!r}"] if f.statement else []
        if f.source_id:
            kwargs.append(f"source_id={f.source_id!r}")
        if f.part21_id:
            kwargs.append(f"part21_id={f.part21_id!r}")
        suffix = f", {', '.join(kwargs)}" if kwargs else ""
        return f"sheet.default_surface_finish({f.ra!r}{suffix})"
    if k == "document_note":
        kwargs = [f"kind={f.note_kind!r}"]
        if f.source_id:
            kwargs.append(f"source_id={f.source_id!r}")
        if f.part21_id:
            kwargs.append(f"part21_id={f.part21_id!r}")
        if not f.on_drawing:
            kwargs.append("on_drawing=False")
        if f.represented_by_source_ids:
            kwargs.append(f"represented_by_source_ids={f.represented_by_source_ids!r}")
        return f"sheet.document_note({f.text!r}, {', '.join(kwargs)})"
    if k == "control_frame":
        return _control_frame_line(f, origin_ref)
    if k == "datum_ref":
        return _datum_ref_line(f, origin_ref)
    if k in _SOURCE_NOTE_LINES:
        return _SOURCE_NOTE_LINES[k](f, origin_ref)
    if k in {
        "envelope",
        "step_level",
        "rotational",
        "hole",
        "boss",
        "polygonal_boss",
        "polygonal_stock",
        "external_spur_gear",
        "step",
    }:
        return _stock_feature_line(
            f,
            part_envelope,
            object_ref=object_ref,
            exact_parameter=exact_parameter,
            exact_step_length=exact_step_length,
            profile_group=profile_group,
        )
    if k in {
        "slot",
        "blend",
        "oriented_slot",
        "rectangular_blind_slot",
        "round_bottom_blind_slot",
        "pocket",
        "channel",
        "pad",
        "pattern",
        "pocket_pattern",
        "slot_pattern",
    }:
        return _machined_feature_line(f, exact_parameter=exact_parameter)
    if k in {
        "chamfer",
        "fillet",
        "angle",
        "paired_ramp_step",
        "gusset_rib",
        "hex_pocket",
        "circular_channel",
        "circular_blind_step",
        "through_step",
        "flat",
        "groove",
        "plate",
    }:
        return _profile_feature_line(f, profile_group=profile_group)
    # Kinds with no declarative verb are flagged inline so they are not silently lost;
    # this includes bare-face aspects and any new kind lacking an emit line.
    return f"# {k} @ {_pt(f.frame.origin)} — no declarative verb yet; drawn by the auto-pass"


def _needs_section(model) -> bool:
    """Mirror the automatic section triggers in the emitted explanation.

    A dense internal station qualifies, as does a Z-axis hole/pattern with a counterbore,
    spotface, or blind bottom. Patterns carry their bore on the ``member`` hole.
    """
    from draftwright.model.planner import internal_section_rows

    if internal_section_rows(model):
        return True
    for f in model.features:
        if f.kind not in ("hole", "pattern") or f.frame.axis != "z":
            continue
        bore = f.member if f.kind == "pattern" else f
        if bore.cbore or bore.spotface or not bore.through:
            return True
    return False


# The section a feature line is grouped under, and the singular noun the header manifest tallies
# it by. Kinds sharing a section (hole+pattern, chamfer+fillet) are emitted under one header —
# grouping is on CONSECUTIVE runs of the existing feature order (never a reorder: ADR 2 (was 0014) makes
# the corridor solve order-sensitive), so a kind that recurs in two runs earns two headers.
_SECTION = {
    "hole": "Holes",
    "pattern": "Holes",
    "boss": "Diameters",
    "polygonal_boss": "Bosses",
    "polygonal_stock": "Stock",
    "external_spur_gear": "Gear requirements",
    "rectangular_blind_slot": "Blind slots",
    "round_bottom_blind_slot": "Blind slots",
    "step": "Turned steps",
    "groove": "Grooves",
    "slot": "Slots",
    "oriented_slot": "Slots",
    "pocket": "Pockets",
    "channel": "Channels",
    "chamfer": "Edges",
    "fillet": "Edges",
    "blend": "Edges",
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
    "polygonal_boss": "polygonal boss",
    "polygonal_stock": "polygonal stock",
    "external_spur_gear": "external spur gear",
    "rectangular_blind_slot": "rectangular blind slot",
    "round_bottom_blind_slot": "round-bottom blind slot",
    "step": "step",
    "groove": "groove",
    "slot": "slot",
    "oriented_slot": "oriented slot",
    "pocket": "pocket",
    "channel": "channel",
    "chamfer": "chamfer",
    "fillet": "fillet",
    "blend": "blend",
    "flat": "flat",
    "plate": "plate",
    "envelope": "envelope",
    "step_level": "step-ladder",
    "authored_dimension": "measured dimension",
    "pmi": "PMI record",
    "datum_ref": "datum feature",
}
# Kinds whose _feature_line carries NO inline comment of its own — the emit loop appends a
# describing comment for these. envelope/step_level/pmi and the no-verb fallback already end in a
# `# …`, so they stay out (double-commenting, and _feature_line is called bare in a test).
_DESCRIBED = frozenset(
    (
        "hole",
        "boss",
        "polygonal_boss",
        "polygonal_stock",
        "external_spur_gear",
        "rectangular_blind_slot",
        "round_bottom_blind_slot",
        "step",
        "slot",
        "oriented_slot",
        "pocket",
        "channel",
        "pattern",
        "chamfer",
        "fillet",
        "blend",
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
        if getattr(f, "profile", None) == "double_d":
            s += f" double-D {_n(f.across_flats)} A/F"
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
    if k == "polygonal_boss":
        prefix = "HEX" if f.side_count == 6 else f"{f.side_count}-sided"
        return f"{prefix} {_n(f.across_flats)} A/F × {_n(f.height)} high"
    if k == "polygonal_stock":
        prefix = "HEX" if f.side_count == 6 else f"{f.side_count}-sided"
        return f"{prefix} {_n(f.across_flats)} A/F × {_n(f.length)} long"
    if k == "external_spur_gear":
        return f"external spur gear · {f.tooth_count} teeth · module {_n(f.module)}"
    if k == "step":
        return f"⌀{_n(f.diameter)} × {_n(f.length)} step"
    if k in ("slot", "pocket"):
        s = f"{k} {_n(f.width)} × {_n(f.length)}"
        return s + (f" × {_n(f.depth)} deep" if k == "pocket" else "")
    if k == "oriented_slot":
        return f"oriented slot {_n(f.width)} × {_n(f.length)}"
    if k == "rectangular_blind_slot":
        return f"open slot {_n(f.width)} × {_n(f.length)} × {_n(f.depth)} deep"
    if k == "round_bottom_blind_slot":
        return (
            f"round-bottom open slot {_n(f.flat_width)} flat × "
            f"R{_n(f.radius)} × {_n(f.length)} long"
        )
    if k == "channel":
        return f"channel {_n(f.width)} wide"
    if k == "pattern":
        return f"{f.count}× ⌀{_n(f.member.diameter)} {f.pattern.replace('_', ' ')}"
    if k == "chamfer":
        equal = f.leg1 == f.leg2 and f.angle == 45
        return f"C{_n(f.leg1)}" if equal else f"chamfer {_n(f.leg1)} × {_n(f.leg2)}"
    if k == "fillet":
        return f"R{_n(f.radius)}"
    if k == "blend":
        return f"R{_n(f.radius)} {f.side} {f.path_kind} blend"
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
    commented out, which is the documented editing workflow (ADR 4 (was 0011 Amdt 1)); `sheet.of(bore1)`
    raises `NameError` at the line you edited. That benefit has nothing to do with dimensions.

    ``None`` for a kind with no declarative verb: its "line" is a comment, and
    ``rotational1 = # …`` does not parse.
    """
    # Fluent ``handle.note(...)`` returns the origin handle, not the appended Note. The
    # generated ``sheet.structured_note(...)`` spelling returns the note's own handle and is
    # therefore safe to bind.
    if (
        f.kind == "note" and not line.startswith("sheet.structured_note(")
    ) or line.lstrip().startswith("#"):
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

    plan = compile_dimensions(model)
    if plan.ladder("overall_height") is None and plan.contingency("step_length") is None:
        return model, None  # the compiler withholds it (Z-turned, rotational OD)
    from dataclasses import replace

    from draftwright.model.declare import _envelope_from_bbox

    # The SAME construction `sheet.envelope()` uses, not a copy. Hand-rolling it here hardcoded
    # the frame origin to (0, 0, 0), which differs from the bbox centre for an off-centre part;
    # then the synthesised envelope would disagree with detection and declaration.
    env = _envelope_from_bbox(model.bbox)
    # Returned ALONGSIDE the model rather than stamped onto the frozen `EnvelopeFeature`:
    # emitter bookkeeping is not public model state.
    # Which feature the emitter synthesised is the emitter's own fact; it travels out-of-band.
    identities = (*model.declaration_identities, None) if model.declaration_identities else ()
    return replace(
        model,
        features=[*model.features, env],
        declaration_identities=identities,
    ), env


def unmirrored_dimensions(model) -> list[str]:
    """Dimensions the COMPILER approved that no emitted line would name.

    The one production answer to "can this script claim to be complete?", and the reason it
    is here rather than in a test: the first cut asked a weaker question — whether an
    unsuppressed `plan_dimensions()` group belonged to a feature with no declarative verb —
    which misses every dimension created OUTSIDE `plan_dimensions`. Locations already prove
    such paths exist, so a compiled location or ladder on an otherwise nameable feature left
    the script printing "THIS IS THE COMPLETE SET" while silently omitting it: a real
    user-facing third state, not merely a missing diagnostic (#947).

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
        (id(feature), role, discriminator, member)
        for feature, role, discriminator, member in _mirrored_requests(declared, synthesised)
        if not _feature_line(feature).lstrip().startswith("#")
    }

    def _asked(feature, parameter_id: str, discriminator=None, member=None) -> bool:
        # A line names either the full parameter id ("bore.diameter") or the bare role
        # ("bore"); a correlated set emits one line covering N members.
        return (id(feature), parameter_id, discriminator, member) in requested or (
            id(feature),
            parameter_id.split(".")[0],
            discriminator,
            member,
        ) in requested

    # ONE interpreter, the same one the emitter serialises. This used to walk
    # `plan.groups`, `plan.locations` and `plan.ladders` itself, with its own copy of the
    # ladder→parameter mapping — so the completeness GATE re-derived the compiler's answer
    # even after the emitter stopped. A category the compiler grew would have been mirrored
    # by `_mirrored_requests` and still reported missing here, or worse, the reverse.
    #
    # `model`, not `declared`: the synthesised envelope adds width and depth parameters the
    # emitter deliberately omits, so compiling the mirror model would report them missing.
    # A measurement the MIRROR consolidates onto another dimension is named by that
    # dimension's line, so the script is complete without a line of its own. This
    # only ever differs from the source model's answer because `mirror_model` synthesises an
    # envelope for a part detected without one — a round body's boss height and its overall
    # height run between the same two faces, so the mirror consolidates where the source had
    # no overall extent to consolidate onto. Requiring a line for it would demand the script
    # name a dimension re-running the script does not draw.
    consolidated = {
        (id(omission.feature), omission.parameter_id)
        for omission in compile_dimensions(declared).diagnostics
        if omission.conveyed_by is not None
        and _asked(omission.conveyed_by.feature, omission.conveyed_by.parameter)
    }

    missing: list[str] = []
    for intent in compile_dimensions(model).addressable():
        feature = resolve_feature(intent.ref)
        if feature is None:
            # Model-level — the overall height of a part with no envelope feature. Only the
            # synthesised envelope can name it until it has a declarative target.
            if (
                synthesised is None
                or (id(synthesised), "height.length", None, None) not in requested
            ):
                missing.append("(bounding box).overall_height")
        elif (
            not _asked(feature, intent.role, intent.discriminator, intent.member)
            and (id(feature), intent.role) not in consolidated
        ):
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


def _mirrors_dimensions(model) -> bool:
    """Whether the emitted script declares its own dimension set, or keeps the planner's.

    False means the script keeps ``sheet.auto_dimensions()`` — a feature the emitter has no
    line for would make a mirrored set silently incomplete (#938/#945), so the whole set
    stays automatic. That is also what makes it load-bearing OUTSIDE the dimension block:
    ``Sheet`` refuses ``authored_views()`` beside ``auto_dimensions()`` (ADR 2: requirements
    determine views), so a script in that state cannot be handed a pinned view set either.
    """
    return model.authored_dimensions is not None or _is_mirrorable(model)


def _mirrored_requests(declared, declared_envelope=None):
    """The compiler's feature/role/axis/member selectors, serialized for the mirror.

    A step-height ladder and rotational bores remain correlated units. Hole locations carry
    separate member/axis selectors so an emitted line can omit exactly one component.

    From the planner's INTENT, never from placed annotations. Walking the drawing is the
    obvious way to build a mirror and it is wrong: a dimension the solver dropped would
    vanish from the regenerated script and could never be recovered by re-running it, and an
    unrelated layout change would silently rewrite version-controlled source.
    """
    from draftwright.model.compiled import compile_dimensions, resolve_feature

    # ONE compiler result, serialised — not three sources reassembled. This used to
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
            out.append((declared_envelope, "height.length", None, None))
            continue
        if declared_envelope is not None and feature is declared_envelope:
            # The synthesised envelope exists ONLY to make the overall height nameable, so
            # name its height and nothing else — width and depth stay omitted exactly as the
            # planner left them on a model that declared no envelope (`mirror_model`). The
            # compiler reaches that height twice, as the ladder and as the parameter, and
            # `addressable()` has already collapsed them to one intent.
            if intent.role != "height.length":
                continue
        out.append((feature, intent.role, intent.discriminator, intent.member))
    return out


def _requested_display_decimals(model, feature, role, discriminator) -> int | None:
    """Precision attached to an augmenting intent mirrored as an authored line (#1349)."""
    return _requested_intent_policy(model, feature, role, discriminator)[0]


def _requested_intent_policy(
    model, feature, role, discriminator, member=None
) -> tuple[int | None, str | None, str | None, int | None]:
    """Display and placement policy attached to an augmenting referential intent."""
    for request in model.requested_dimensions:
        if request.feature is not feature or request.member != member:
            continue
        matches = (
            request.role == role if "." in request.role else role.startswith(f"{request.role}.")
        )
        if not matches:
            continue
        if request.discriminator is not None and not (
            request.discriminator == discriminator or role.endswith(f".{request.discriminator}")
        ):
            continue
        return (
            cast(int | None, request.display_decimals),
            cast(str | None, request.view),
            cast(str | None, request.side),
            cast(int | None, request.lane),
        )
    return None, None, None, None


def _dimension_block(model, names: dict[int, str], synthesised_envelope=None) -> list[str]:
    """The authored set as `sheet.dimension(...)` declarations, or the `auto_dimensions()` line.

    Emitted AFTER the features, because each line names a feature by the variable that
    feature's line binds — which is the whole reason #922 needed nameable identity first. A
    positional spelling (`sheet.dimension(3, "width")`) would break the moment a user
    comments a feature out, which is the documented editing workflow.

    A generated script must state its source either way (ADR 4 (was 0016) / #874): a dimension the
    script does not name only means "omitted" inside a set that says it is complete.
    """
    if not _mirrors_dimensions(model):
        # A feature with no declarative verb carries planned dimensions, so a mirrored set
        # would silently omit them and claim completeness it does not have.
        # WHY, specifically. `_is_mirrorable` now fails for any dimension the compiler
        # approved and no line can name — not only the no-declarative-verb case — so blaming
        # blaming the missing verb unconditionally would misdirect a reader when the cause differs,
        # and could print an empty kind list.
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
        [
            (
                a.feature,
                a.role,
                a.discriminator,
                a.display_decimals,
                a.view,
                a.side,
                a.lane,
                a.member,
            )
            for a in model.authored_dimensions
        ]
        if model.authored_dimensions is not None
        # A detected model has no authored set, so the script MIRRORS the planner's choice as
        # explicit lines instead of `auto_dimensions()`. That is what makes an
        # automatic dimension commentable: before this, the promise "comment a line out to
        # drop it" held for features and not for dimensions, so the only way to drop one
        # dimension was to drop its whole feature — losing the callout, the centre marks and
        # the location with it.
        else [
            (
                *request[:3],
                *_requested_intent_policy(model, *request),
                request[3],
            )
            for request in _mirrored_requests(model, synthesised_envelope)
        ]
    )
    out = [
        "# ── Dimensions ────────────────────────────────────────────────────────────────",
        "# THIS IS THE COMPLETE SET (ADR 4 (was 0016)), including feature schedules below.",
        "# A measurement with no dimension or schedule declaration is omitted",
        "# deliberately — comment a line out to drop that dimension, add one to declare it.",
        # The role vocabulary was undiscoverable from the artefact: an editor had to guess a
        # string or read the source. Typing narrows it now, but a generated file is
        # read by people and agents who may have neither, so it says where the answer is.
        '# To add one: sheet.dimension(<name>, "<id>") — a feature\'s ids are listed by',
        "# <name>.dimension_ids(), and naming one it lacks reports the ones it has.",
        # The VERB, not just the comment above it. `dimension(...)` lines imply this source
        # on their own, so writing it was optional for a non-empty set — but an EMPTY
        # authored set has no line to imply it from, and the script then said its source in a
        # comment only and failed the mandatory-source check at build. Emitting
        # it unconditionally also means an authored script states its source the same way an
        # automatic one does, rather than in prose a reader has to trust.
        "sheet.authored_dimensions()",
    ]
    for feature, role, discriminator, display_decimals, view, side, lane, member in requests:
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
        # of one thing appear side by side.
        axis = (
            f', axis="{discriminator}"'
            if discriminator and "." not in role[role.find(".") + 1 :]
            else ""
        )
        placement = "" if member is None else f", member={member!r}"
        if view is not None:
            placement += f', view="{view}"'
        if side is not None:
            placement += f', side="{side}"'
        line = f'sheet.dimension({name}, "{role}"{axis}{placement})'
        if display_decimals is not None:
            line += f".format(decimals={display_decimals})"
        if lane is not None:
            line += f".place(lane={lane})"
        out.append(line)
    return out


def _schedule_block(model, names: dict[int, str]) -> list[str]:
    """Retain authored table representations using the same emitted owner bindings."""
    if not model.schedules:
        return []
    model._validate_schedule_origins()
    out = ["", "# Feature schedules select measurements; the compiler supplies cell content."]
    for schedule in model.schedules:
        out.append("sheet.schedule([")
        for row in schedule.rows:
            name = names.get(id(row.feature))
            if name is None:
                raise ValueError(
                    f"emit_sheet_script(): cannot name the {row.feature.kind} carrying "
                    f"schedule {schedule.name!r}; it has no emitted feature binding"
                )
            out.append(f"    ({name}, {row.parameters!r}),")
        out.append(f"], name={schedule.name!r}, prefer={schedule.prefer!r})")
    return out


def _layout_override_block(model) -> list[str]:
    """Emit append-only layout policy after every declaration has its identity."""

    if not model.layout_overrides:
        return []
    lines = []
    for override in model.layout_overrides:
        selector = (
            f"axis={json.dumps(override.axis)}, member={json.dumps(override.member)}, "
            if override.parameter_id == "location"
            else ""
        )
        if override.side is not None:
            parameter = (
                f"parameter={json.dumps(override.parameter_id)}, "
                if override.parameter_id is not None
                else ""
            )
            lines.append(
                "sheet.layout_override("
                f"{json.dumps(override.declaration_id)}, {parameter}{selector}"
                f"side={json.dumps(override.side)})"
            )
        else:
            lines.append(
                "sheet.layout_override("
                f"{json.dumps(override.declaration_id)}, "
                f"parameter={json.dumps(override.parameter_id)}, {selector}lane={override.lane})"
            )
    return [
        "# ── Layout-only declaration overrides ──────────────────────────────────────────",
        "# Relative corridor/lane policy only; the solve owns coordinates and feasibility.",
        *lines,
    ]


def _profile_groups(source_features) -> dict[int, str]:
    """Keep detected profile tokens distinct from authored groups."""
    detected_profile_groups: dict[object, str] = {}
    profile_group_by_feature: dict[int, str] = {}
    reserved_profile_groups = {
        group
        for feature in source_features
        if feature.kind in {"step", "groove"}
        for group in (getattr(feature, "profile_group", None),)
        if group is not None
    }

    def detected_profile_token() -> str:
        """Mint a stable generated token without entering the caller's namespace."""
        index = len(detected_profile_groups) + 1
        token = f"detected-profile-{index}"
        while token in reserved_profile_groups:
            index += 1
            token = f"detected-profile-{index}"
        reserved_profile_groups.add(token)
        return token

    for feature in source_features:
        if feature.kind not in {"step", "groove"}:
            continue
        declared_group = getattr(feature, "profile_group", None)
        if declared_group is not None:
            profile_group_by_feature[id(feature)] = declared_group
            continue
        provider_group = getattr(feature, "profile", None)
        if provider_group is None:
            continue
        token = detected_profile_groups.get(provider_group)
        if token is None:
            token = detected_profile_token()
            detected_profile_groups[provider_group] = token
        profile_group_by_feature[id(feature)] = token
    return profile_group_by_feature


def _nominal_requirement_calls(
    nominal: object,
    step_length_nominal: object,
    nominal_parameter: str | None,
    kind: str,
) -> str:
    calls = ""
    if isinstance(nominal, NominalRequirement):
        provenance = f"source={nominal.source!r}, source_ids={nominal.source_ids!r}"
        if nominal.label is not None:
            provenance += f", label={nominal.label!r}"
        on = f", on={nominal_parameter!r}" if kind in ("step", "pattern", "rotational") else ""
        calls += f".requirement({_authored_n(nominal.value)}{on}, {provenance})"
    if isinstance(step_length_nominal, NominalRequirement):
        label = (
            f", label={step_length_nominal.label!r}"
            if step_length_nominal.label is not None
            else ""
        )
        calls += (
            f".requirement({_authored_n(step_length_nominal.value)}, "
            f"on='step.length', source={step_length_nominal.source!r}, "
            f"source_ids={step_length_nominal.source_ids!r}{label})"
        )
    return calls


def _feature_block(
    features,
    part_envelope=None,
    object_refs: Mapping[int, str] | None = None,
    decorations: Mapping | None = None,
    declaration_metadata: Mapping[int, tuple[str, str, tuple[str, ...]]] | None = None,
) -> tuple[list[str], dict[int, str]]:
    """The emitted feature lines plus ``{id(feature): binding}`` for the names they bind.

    The map is RETURNED rather than recomputed by the dimension block, which needs the same
    names. Recomputing would be a second derivation of one fact — and this one would be
    silently wrong rather than loudly: a mismatch emits `sheet.dimension(hole2, …)` naming
    the wrong hole, which still runs.

    Lines are grouped under section sub-headers (with a repeat tally) and each carries a
    trailing describing comment. Geometric features retain their consecutive order. Notes
    are dependent aspect statements, so they are emitted after the independently bindable
    features; this lets an identity-preserving public reorder put a note before its origin
    without making the generated relationship impossible to spell (#1351).
    """
    if not features:
        return ["# ── Features: none detected ──"], {}
    source_features = tuple(features)
    profile_group_by_feature = _profile_groups(source_features)
    out = [f"# ── Features ({len(source_features)}): {_manifest(source_features)} ──"]
    features = tuple(f for f in source_features if f.kind != "note") + tuple(
        f for f in source_features if f.kind == "note"
    )
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
            profile_group = profile_group_by_feature.get(id(f))
            profile_kw = {} if profile_group is None else {"profile_group": profile_group}
            step_length_nominal = (
                (decorations or {}).get((f, "nominal_requirement", "step.length"))
                if f.kind == "step"
                else None
            )
            exact_step_length = isinstance(step_length_nominal, NominalRequirement)
            gdt_with_origin = f.kind in ("control_frame", "datum_ref", "finish", "note")
            origin_ref = names.get(id(f.origin)) if gdt_with_origin else None
            if (
                gdt_with_origin
                and (f.kind != "note" or bool(f.satisfies))
                and f.origin is not None
                and getattr(f.origin, "kind", None) != "pmi"
                and origin_ref is None
            ):
                raise ValueError(
                    f"emit_sheet_script(): cannot preserve a {f.kind} whose origin has "
                    "no emitted binding"
                )
            if f.kind == "note" and f.satisfies and origin_ref is None:
                raise ValueError(
                    "emit_sheet_script(): cannot preserve structured note satisfaction "
                    "without its feature binding"
                )
            nominal_parameter = {
                "step": "step.diameter",
                "boss": "boss.diameter",
                "hole": "bore.diameter",
                "pattern": "bore.diameter",
                "rotational": "od.diameter",
            }.get(f.kind)
            nominal = (
                (decorations or {}).get((f, "nominal_requirement", nominal_parameter))
                if nominal_parameter is not None
                else None
            )
            exact_parameter = (
                nominal_parameter if isinstance(nominal, NominalRequirement) else None
            )
            # An imported nominal owner must rebuild the same exact geometry that its typed
            # requirement validates. Object-reference matching intentionally admits the
            # generated-script rounding quantum, so a close source cylinder can be a valid
            # ordinary convenience reference yet disagree with the lossless imported value.
            # Keep the numeric declaration for exact-owned parameters.
            object_ref = (
                None
                if exact_parameter is not None
                or exact_step_length
                or (f.kind == "step" and f.position_span is not None)
                else (object_refs or {}).get(id(f))
            )
            line = _feature_line(
                f,
                part_envelope,
                origin_ref=origin_ref,
                object_ref=None if gdt_with_origin else object_ref,
                exact_parameter=exact_parameter,
                exact_step_length=exact_step_length,
                **profile_kw,
            )
            diameter_role = {
                "hole": "bore",
                "pattern": "bore",
                "rotational": "od",
            }.get(f.kind)
            role_tolerance = (
                (decorations or {}).get((f, "diameter", diameter_role))
                if diameter_role is not None
                else None
            )
            broad_tolerance = (decorations or {}).get((f, "diameter"))
            tolerances: tuple[object, ...] = (
                role_tolerance if role_tolerance is not None else broad_tolerance,
            )
            if (
                f.kind == "hole"
                and isinstance(role_tolerance, FitClass)
                and broad_tolerance is not None
            ):
                # The broad tolerance still belongs on recess diameters; the role-specific fit
                # then wins only on the bore. Preserve that public fluent ordering exactly.
                tolerances = (broad_tolerance, role_tolerance)
            for tolerance in () if f.kind in ("step", "boss") else tolerances:
                if isinstance(tolerance, ToleranceDecoration | int | float | tuple):
                    value = (
                        tolerance.value
                        if isinstance(tolerance, ToleranceDecoration)
                        else tolerance
                    )
                    args = (
                        f"{_authored_n(value[0])}, {_authored_n(value[1])}"
                        if isinstance(value, tuple)
                        else _authored_n(value)
                    )
                    provenance = ""
                    if isinstance(tolerance, ToleranceDecoration):
                        provenance = f", source={tolerance.source!r}"
                        if tolerance.source_ids:
                            provenance += f", source_ids={tolerance.source_ids!r}"
                        if tolerance.limit_bounds is not None:
                            provenance += f", limit_bounds={tolerance.limit_bounds!r}"
                    on_target = "diameter" if f.kind == "step" else diameter_role
                    on = (
                        f", on={on_target!r}"
                        if f.kind in ("step", "pattern", "rotational")
                        else ""
                    )
                    line += f".tolerance({args}{on}{provenance})"
                elif isinstance(tolerance, FitClass) and f.kind == "hole":
                    show = "" if tolerance.show == "class" else f", show={tolerance.show!r}"
                    line += f".fit({tolerance.code!r}{show})"

            if f.kind in (
                "step",
                "boss",
                "angle",
                "through_step",
                "pad",
                "rectangular_blind_slot",
                "round_bottom_blind_slot",
                "oriented_slot",
            ):
                # Preserve the EFFECTIVE decoration of each independently addressable
                # through-step leg / pad extent.  Pad height is a new independent public
                # parameter; replay must not lose its tolerance merely because all three
                # extents share the generic ``length`` kind.
                # Serialising each as a canonical full id is deliberately lossless even when
                # the source used one family-wide call: replay compiles to the same effective
                # tolerances without depending on fluent call order.
                for parameter in f.parameters():
                    tolerance = (decorations or {}).get(
                        (f, parameter.kind, parameter.role, parameter.discriminator)
                    )
                    if tolerance is None:
                        tolerance = (decorations or {}).get((f, parameter.kind, parameter.role))
                    if tolerance is None:
                        tolerance = (decorations or {}).get((f, parameter.kind))
                    if isinstance(tolerance, FitClass) and f.kind in ("step", "boss"):
                        show = "" if tolerance.show == "class" else f", show={tolerance.show!r}"
                        line += f".fit({tolerance.code!r}{show})"
                        continue
                    if not isinstance(tolerance, ToleranceDecoration | int | float | tuple):
                        continue
                    value = (
                        tolerance.value
                        if isinstance(tolerance, ToleranceDecoration)
                        else tolerance
                    )
                    args = (
                        f"{_authored_n(value[0])}, {_authored_n(value[1])}"
                        if isinstance(value, tuple)
                        else _authored_n(value)
                    )
                    provenance = ""
                    if isinstance(tolerance, ToleranceDecoration):
                        provenance = f", source={tolerance.source!r}"
                        if tolerance.source_ids:
                            provenance += f", source_ids={tolerance.source_ids!r}"
                        if tolerance.limit_bounds is not None:
                            provenance += f", limit_bounds={tolerance.limit_bounds!r}"
                    line += f".tolerance({args}, on={parameter.parameter_id!r}{provenance})"

            line += _nominal_requirement_calls(
                nominal, step_length_nominal, nominal_parameter, f.kind
            )
            name = _binding(f, line, counts)
            if name is not None:
                metadata = (declaration_metadata or {}).get(id(f))
                if metadata is not None:
                    declaration_id, provenance, occurrence_ids = metadata
                    identity_call = (
                        f".identify({declaration_id!r}, provenance={provenance!r}, "
                        f"occurrence_ids={occurrence_ids!r})"
                    )
                    code, separator, comment = line.partition("   #")
                    line = code + identity_call + (separator + comment if separator else "")
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

The numeric values are DETECTED off the geometry (honest for a STEP / recovered solid).
With a named features container, uniquely proven source objects are referenced directly;
unavailable or ambiguous matches remain complete numeric declarations and fail closed.
"""'''


def _validate_scale_policy(scale, scale_policy) -> None:
    """Reject incoherent script options before detection or emission work begins."""
    if scale_policy not in {"strict", "fallback", "permissive"}:
        raise ValueError("scale_policy must be 'strict', 'fallback', or 'permissive'")
    if scale is None and scale_policy != "fallback":
        raise ValueError("scale_policy applies only when an explicit scale is supplied")


def _derived_label(name: str, kind: str) -> str:
    """Recover the public one-character label from a canonical derived-view name."""
    if kind == "section" and re.fullmatch(r"section_([a-z0-9])\1", name):
        return name[-1].upper()
    if kind == "detail" and re.fullmatch(r"detail_[a-z0-9]", name):
        return name[-1].upper()
    raise ValueError(f"cannot emit {kind} view with non-canonical name {name!r}")


def _principal_view_suffix(spec: ViewSpec) -> str:
    """Express independent scale and hidden-edge visibility through view verbs."""
    scale = "" if spec.scale_factor is None else f".scale({spec.scale_factor!r})"
    hidden = "" if spec.hidden_lines else ".hidden_lines(False)"
    return scale + hidden


def _adopted_view_block(constraints: ViewConstraints, names: Mapping[int, str]) -> list[str]:
    """Emit a semantic Sheet request for an adopted view-source state (#1350)."""
    principal_source = constraints.principal_source or "automatic"
    derived_source = constraints.derived_source or "automatic"
    for records, actual_source, expected_source, label in (
        (constraints.principals, constraints.principal_source, "authored", "principal"),
        (
            constraints.added_principals,
            constraints.principal_source,
            "automatic",
            "added principal",
        ),
        (constraints.derived, constraints.derived_source, "authored", "derived"),
        (constraints.added_derived, constraints.derived_source, "automatic", "added derived"),
    ):
        if records and actual_source != expected_source:
            raise ValueError(
                f"cannot emit {label} views with source {actual_source!r}; "
                f"expected {expected_source!r}"
            )
    lines = [
        "# Adopt the detected baseline with independent dimension/principal/derived sources.",
        "sheet.take_over(",
        '    dimensions="authored",',
        f'    principal_views="{principal_source}",',
        f'    derived_views="{derived_source}",',
        ")",
    ]
    handles: dict[str, str] = {}

    def reject_unexpressed_spec_fields(spec) -> None:
        unsupported = [
            field for field in ("camera", "up", "page_axes") if getattr(spec, field) is not None
        ]
        if unsupported:
            raise ValueError(
                f"cannot emit {spec.name!r}: Sheet view verbs do not express "
                f"{', '.join(unsupported)}"
            )

    def emit_principal(item, verb: str) -> None:
        spec = item.spec
        reject_unexpressed_spec_fields(spec)
        expected_kind = "pictorial" if spec.name == "iso" else "principal"
        if spec.name not in (*PRINCIPAL_VIEW_NAMES, "iso") or spec.kind != expected_kind:
            raise ValueError(f"cannot emit principal view {spec.name!r} with kind {spec.kind!r}")
        if spec.target is not None:
            raise ValueError(f"cannot emit principal view {spec.name!r} with a target")
        handle = f"{spec.name}_view"
        handles[spec.name] = handle
        suffix = _principal_view_suffix(spec)
        lines.append(f'{handle} = sheet.{verb}("{spec.name}"){suffix}')

    def emit_derived(item, verb: str) -> None:
        spec = item.spec
        reject_unexpressed_spec_fields(spec)
        if spec.kind not in {"section", "detail"}:
            raise ValueError(f"cannot emit derived view {spec.name!r} with kind {spec.kind!r}")
        label = _derived_label(spec.name, spec.kind)
        target = spec.target
        if not isinstance(target, tuple) or len(target) != 2:
            raise ValueError(f"cannot emit {spec.name!r}: it has no semantic target")
        target_kind, target_value = target
        if target_kind == "feature":
            feature_name = names.get(id(target_value))
            if feature_name is None:
                raise ValueError(
                    f"cannot emit {spec.name!r}: its target feature has no script binding"
                )
            keyword = "through" if spec.kind == "section" else "around"
            args = f'"{label}", {keyword}={feature_name}'
        elif target_kind == "at" and spec.kind == "section":
            if (
                isinstance(target_value, bool)
                or not isinstance(target_value, Real)
                or not math.isfinite(float(target_value))
            ):
                raise ValueError(
                    f"cannot emit {spec.name!r}: an at= target must be a finite numeric value"
                )
            args = f'"{label}", at={float(target_value)!r}'
        else:
            raise ValueError(f"cannot emit {spec.name!r}: unsupported target {target!r}")
        handle = f"{spec.name}_view"
        handles[spec.name] = handle
        suffix = "" if spec.scale_factor is None else f".scale({spec.scale_factor!r})"
        lines.append(f"{handle} = sheet.{verb}({args}){suffix}")

    for item in constraints.principals:
        emit_principal(item, "view")
    for item in constraints.added_principals:
        emit_principal(item, "add_view")
    for item in constraints.derived:
        emit_derived(item, f"{item.spec.kind}_view")
    for item in constraints.added_derived:
        emit_derived(item, f"add_{item.spec.kind}_view")

    for relation in constraints.relations:
        gap = "" if relation.gap is None else f", gap={relation.gap!r}"
        handle = handles.get(relation.subject)
        if handle is not None:
            if relation.relation in {"align_x", "align_y"} and relation.gap is not None:
                raise ValueError(
                    f"cannot emit {relation.relation} relation with gap={relation.gap!r}; "
                    "the public alignment verbs do not accept a gap"
                )
            lines.append(f'{handle}.{relation.relation}("{relation.reference}"{gap})')
            continue
        if relation.relation in {"left_of", "right_of"}:
            left, right = (
                (relation.subject, relation.reference)
                if relation.relation == "left_of"
                else (relation.reference, relation.subject)
            )
            lines.append(f'sheet.row("{left}", "{right}"{gap})')
            continue
        if relation.relation in {"above", "below"}:
            below, above = (
                (relation.reference, relation.subject)
                if relation.relation == "above"
                else (relation.subject, relation.reference)
            )
            lines.append(f'sheet.column("{below}", "{above}"{gap})')
            continue
        raise ValueError(
            f"cannot emit {relation.relation} relation for automatic view "
            f"{relation.subject!r}; declare or add the subject view to obtain its public handle"
        )
    for pin in constraints.pins:
        handle = handles.get(pin.view)
        if handle is None:
            raise ValueError(
                f"cannot emit pin for automatic view {pin.view!r}; "
                "declare or add the view to obtain its public handle"
            )
        lines.append(f"{handle}.pin({pin.at!r})")
    return lines


def _settled_reference_build(*args, **kwargs):
    """One reference build, or ``None`` if this source cannot be DRAWN.

    Its only job is to reveal an automatic replan for :func:`settled_layout_for` to pin, so an
    undrawable source must not be the reason a script is not written: a STEP file holding a
    bare curve projects no side view, and `generate_sheet_script` still owes the caller a
    script — the standard the inspection sidecar beside it already meets.

    Narrow on purpose. It reuses `builder._is_expected_candidate_build_failure`, the
    predicate the recovery ladder already uses to decide whether a speculative build may
    reject quietly. A blanket `except ValueError` would also swallow
    `ScaleIncompatibilityError`, `ViewPlanIncomplete`, `MultipleTurnedProfilesError` and an
    unknown page size — deliberate refusals under ADR 5, which must reach the caller now and
    not be demoted to a log line plus a script that fails when someone runs it.
    """
    try:
        return build_drawing(*args, **kwargs)
    except Exception as error:  # noqa: BLE001 — re-raised below unless expected
        if not _is_expected_candidate_build_failure(error):
            raise
        _log.warning("No settled-layout reference build for this source: %s", error)
        return None


def settled_layout_for(drawing) -> dict | None:
    """A measured automatic layout that a declared script cannot safely re-derive.

    ``None`` when the first plan was accepted, which is the common case and needs no
    pinning. A scale/page replan or a reduced-view candidate rejected after measuring ink
    is not a decision a declared script can reproduce from its model alone: the latter may
    otherwise re-select the very view set the automatic build rejected. The resolved page
    and view set have to be written down. The
    automatically selected scale is replayed through a private constraint: spelling its numeric
    result as an authored ``scale=`` request changes the compose policy even when the number
    agrees, while discarding it can select another standard scale on the settled page.

    One function because two callers must agree on what "the settled layout" is:
    :func:`generate_sheet_script`, and the round-trip parity tests that assert a generated
    script draws and lints exactly what the automatic build did.
    """
    if (
        drawing.scale_decision.get("status") != "automatic_replanned"
        and drawing.view_decision.get("status") != "retained_after_rejection"
    ):
        return None
    return {
        "scale": drawing.scale,
        "page": (drawing.page_w, drawing.page_h),
        "views": tuple(drawing.views),
        "pin_views": drawing.view_decision.get("status") == "retained_after_rejection",
    }


def _declaration_metadata(model, source_feature_ids, source_detected, declaration_occurrences):
    """Preserve declaration identities while assigning names to mirrored features."""
    declaration_metadata = {}
    reserved_declaration_ids = {
        identity.declaration_id
        for identity in model.declaration_identities
        if identity is not None
    }
    generated_declaration_ids: set[str] = set()
    for index, feature in enumerate(model.features, start=1):
        identity = (
            model.declaration_identities[index - 1] if model.declaration_identities else None
        )
        if identity is not None:
            declaration_id = identity.declaration_id
            provenance = identity.provenance
            occurrence_ids = identity.occurrence_ids
        else:
            declaration_id = f"declaration:{index}"
            suffix = 1
            while declaration_id in reserved_declaration_ids | generated_declaration_ids:
                declaration_id = f"declaration:{index}:generated-{suffix}"
                suffix += 1
            generated_declaration_ids.add(declaration_id)
            if id(feature) not in source_feature_ids:
                provenance = "derived"
            elif feature.kind in {
                "pmi",
                "control_frame",
                "datum_ref",
                "general_tolerance",
                "default_surface_finish",
                "document_note",
            }:
                provenance = "pmi"
            elif feature.kind == "finish" and (
                feature.source_id
                or getattr(getattr(feature, "origin", None), "kind", None) == "pmi"
            ):
                provenance = "pmi"
            elif feature.kind == "note":
                provenance = "structured-note"
            elif source_detected:
                provenance = "detected-geometry"
            else:
                provenance = "authored"
            occurrence_ids = ()
        if declaration_occurrences is not None and id(feature) in declaration_occurrences:
            occurrence_ids = tuple(declaration_occurrences[id(feature)])
        declaration_metadata[id(feature)] = (
            declaration_id,
            provenance,
            occurrence_ids,
        )
    return declaration_metadata


def _pattern_requirement_imports(model) -> set[str]:
    return {
        type(requirement).__name__
        for feature in model.features
        for requirement in getattr(feature, "member_size_requirements", ())
        if isinstance(requirement, ToleranceDecoration | NominalRequirement)
    }


def _finish_constructor_imports(model) -> set[str]:
    imports: set[str] = set()
    for feature in model.features:
        if feature.kind != "finish":
            continue
        imports.update(("Finish", "Frame"))
        if getattr(getattr(feature, "origin", None), "cylindrical_refs", ()):
            imports.add("CylindricalReference")
    return imports


def _model_constructor_imports(model):
    """Find constructor names potentially used by the emitted feature declarations."""
    # Every constructor a member template can name has to be listed here. The pattern verbs
    # take their member as a nested `hole(...)` / `pocket(...)` / `slot(...)` call — declare
    # rejects `members=` and recomputes the layout — so the member constructor is a name the
    # generated file uses, and a missing entry is a NameError on the first line that runs
    # (including nested pocket and slot pattern members).
    model_imports = set()
    if any(f.kind == "angle" and getattr(f, "members", ()) for f in model.features):
        model_imports.add("AngularReference")
    if any(f.kind in ("hole", "pattern") for f in model.features):
        model_imports.add("hole")
    model_imports.update(_pattern_requirement_imports(model))
    if any(
        f.kind == "pattern" and getattr(f.member, "profile", None) == "double_d"
        for f in model.features
    ):
        model_imports.add("double_d_bore")
    if any(f.kind == "pocket_pattern" for f in model.features):
        model_imports.add("pocket")
    if any(f.kind == "slot_pattern" for f in model.features):
        model_imports.add("slot")
    if any(f.kind == "envelope" for f in model.features):
        model_imports.update(["EnvelopeFeature", "Frame"])
    if any(f.kind == "pmi" for f in model.features):
        model_imports.update(["Frame", "PmiFeature"])
    if any(f.kind == "pmi" and getattr(f, "cylindrical_refs", ()) for f in model.features):
        model_imports.add("CylindricalReference")
    if any(f.kind == "control_frame" for f in model.features):
        model_imports.update(["ControlFrame", "Frame"])
    if any(f.kind == "datum_ref" for f in model.features):
        model_imports.update(["DatumRef", "Frame"])
    model_imports.update(_finish_constructor_imports(model))
    if any(
        f.kind == "note"
        and (
            getattr(getattr(f, "origin", None), "kind", None) == "pmi"
            or getattr(f, "source_id", "")
            or getattr(f, "source_ids", ())
            or getattr(f, "part21_id", "")
        )
        for f in model.features
    ):
        model_imports.update(["Frame", "Note", "PmiFeature"])
    typed_aspects = [
        aspect
        for feature in model.features
        for target in (getattr(feature, "member", feature),)
        for aspect in (getattr(target, "thread", None), getattr(target, "knurl", None))
        if isinstance(aspect, (ThreadOperation, ThreadRequirement, KnurlRequirement))
    ]
    if any(isinstance(aspect, ThreadOperation) for aspect in typed_aspects):
        model_imports.add("ThreadOperation")
    if any(isinstance(aspect, ThreadRequirement) for aspect in typed_aspects):
        model_imports.update(["CylindricalReference", "ThreadRequirement"])
    if any(isinstance(aspect, KnurlRequirement) for aspect in typed_aspects):
        model_imports.update(["CylindricalReference", "KnurlRequirement"])
    if any(
        f.kind in ("control_frame", "datum_ref", "finish", "note")
        and getattr(getattr(f, "origin", None), "kind", None) == "pmi"
        for f in model.features
    ):
        model_imports.add("PmiFeature")
    return model_imports


def _script_constructor_args(
    script_options, model, settled_layout, pmi_source, assessment, replayed_recognition
):
    """Spell replay-dependent Sheet options at their declared constructor positions."""
    # Ordinary aspects, ordering and defaults come from BuildOptions. Only replay-
    # dependent spellings are supplied here, at their field's declared position.
    from draftwright._core import _sheet_option_margins, _validated_title_block_width

    _sheet_option_margins(
        margin_left=script_options.margin_left,
        margin_right=script_options.margin_right,
        margin_top=script_options.margin_top,
        margin_bottom=script_options.margin_bottom,
    )
    validated_width = _validated_title_block_width(script_options.title_block_width)
    special: dict[str, list[str]] = {}
    for key in (
        "margin_left",
        "margin_right",
        "margin_top",
        "margin_bottom",
        "title_block_width",
    ):
        value = validated_width if key == "title_block_width" else getattr(script_options, key)
        special[key] = [] if value is None else [f"{key}={float(value)!r}"]
    emitted_scale = script_options.scale
    if emitted_scale is None and settled_layout is not None and not _mirrors_dimensions(model):
        # An unmirrorable model keeps auto_dimensions(), so its requirement planner must stay
        # in charge of the view topology. Replay the settled numeric scale through the public
        # explicit-scale path; the private authored-mirror constraint relies on fixed views.
        emitted_scale = settled_layout["scale"]
    if emitted_scale is not None:
        special["scale"] = [f"scale={emitted_scale!r}"]
    elif settled_layout is not None:
        special["scale"] = [f"_replayed_scale={settled_layout['scale']!r}"]
    else:
        special["scale"] = []
    emitted_page = script_options.page
    if emitted_page is None and settled_layout is not None:
        emitted_page = settled_layout["page"]
    special["page"] = [] if emitted_page is None else [f"page={emitted_page!r}"]
    if settled_layout is not None and settled_layout.get("pin_views", False):
        replayed_views = tuple(
            name for name in settled_layout["views"] if name in {"front", "plan", "side", "iso"}
        )
        special["page"].append(f"_replayed_views={replayed_views!r}")
    # The AP242 seam: the generated script builds from a solid, so it must retain
    # the document path separately for PMI correspondence.
    special["source"] = [] if pmi_source is None else [f"source={pmi_source!r}"]
    if assessment:
        special["pmi"] = ['pmi=_replay_options["pmi_mode"]']
    elif script_options.pmi != "off":
        special["pmi"] = [f"pmi={script_options.pmi!r}"]
    else:
        special["pmi"] = []
    ctor = script_options.script_constructor_args(special)
    if replayed_recognition:
        ctor.insert(2, "_replayed_recognition=True")
    return ctor


def emit_sheet_script(
    model,
    part_expr: str,
    stem: str,
    *,
    title: str,
    number: str,
    drawn_by: str = "",
    tolerance: str | None = None,
    scale=None,
    scale_policy="fallback",
    page=None,
    material: str | None = None,
    date: str = "",
    revision: str = "A",
    company: str = "",
    approved_by: str = "",
    document_type: str = "",
    sheet: str = "",
    frame: bool = False,
    zones: bool = False,
    projection: str | None = None,
    projection_symbol: bool = True,
    text_position: str = "inline",
    text_orientation: str = "aligned",
    leader_region: str = "auto",
    annotation_layout: str = "demand-guided",
    object_ref: bool = False,
    object_candidates: Mapping[str, Shape] | None = None,
    source_part: Shape | None = None,
    formats: Sequence[str] = ("pdf",),
    settled_layout: Mapping | None = None,
    view_constraints: ViewConstraints | None = None,
    pmi: str = "off",
    pmi_source: str | None = None,
    margin_left: float | None = None,
    margin_right: float | None = None,
    margin_top: float | None = None,
    margin_bottom: float | None = None,
    title_block_width: float | None = None,
    declaration_occurrences: Mapping[int, tuple[str, ...]] | None = None,
    assessment: bool = False,
    assessment_source_name: str | None = None,
) -> str:
    """The generated declarative ``Sheet`` script text for a detected *model*.

    *part_expr* is the Python that binds ``part`` (a STEP ``import_step`` or a ``part = …``
    seam); *stem* is the output basename the script exports to. The title-block / layout aspects
    (``drawn_by``/``tolerance``/``scale``/``page``, #474) are emitted into the ``Sheet(...)``
    constructor only when non-default. The layout algorithm is always explicit so a saved
    script keeps its chosen mode across later default changes.
    The script ends with the explicit lifecycle ``drawing = sheet.build()`` then
    ``drawing.export(...)`` (#968), so the finalized :class:`~draftwright.drawing.Drawing` has a
    name an editor can lint or inspect without rewriting the tail or building twice. *formats*
    (the CLI's ``--format``, #709) is always spelled out on that call, so a re-run reproduces the
    requested outputs.

    AP242 PMI cannot be re-extracted from the ``import_step`` seam, so detected dimensional PMI
    is emitted as declared Sheet dimensions and supported geometric tolerances/datums as
    declared ``ControlFrame`` / ``DatumRef`` objects; unsupported raw PMI records stay explicit
    ``sheet.add(PmiFeature(...))`` fallbacks (#503 / #422 / #1095).

    A model carrying an **authored** dimension set emits `sheet.dimension(feature, role)`
    declarations after the features (#922). That was refused until every feature had a name to
    reference: the only way to name one was its position, and the documented workflow is to
    comment a feature line out and re-run — which shifts every later index and silently
    retargets the declarations onto their neighbours. #931 and #932 removed positional
    addressing from the artefact, so the declarations can now be written honestly.

    ``settled_layout`` is supplied by :func:`generate_sheet_script` only when an automatic
    semantic correction changed the initially composed scale or view set. The generated
    script mirrors dimensions as an authored set, so that correction is deliberately gated
    off on re-run; spelling the already-resolved scale/page/views preserves round-trip parity
    without pretending an edited authored set is still automatic. ``view_constraints`` lets a
    caller emitting an adopted :class:`~draftwright.Sheet` preserve its independent principal
    and derived source choices plus semantic view declarations; targets remain stable feature
    bindings rather than raw page coordinates.

    Recognition evidence is deliberately NOT embedded here. The generated script is a drawing
    declaration a person edits; evidence about the run that produced it belongs in the sidecar
    document beside it, where a reader can diff or re-read it without parsing Python (#1460)."""
    _validate_scale_policy(scale, scale_policy)
    validate_projection(projection, projection_symbol=projection_symbol)
    _dimension_draft(text_position, text_orientation)
    from draftwright.annotation_layout_profile import annotation_layout_policy
    from draftwright.leader_policy import leader_region_policy

    leader_region = leader_region_policy(leader_region).value
    annotation_layout = annotation_layout_policy(annotation_layout)
    script_options = BuildOptions.from_mapping(locals())
    # The script declares this model — `model` plus an envelope when the overall height would
    # otherwise be unnameable under the mirrored (authored) set. BEFORE the import scan, since
    # a synthesised envelope needs `EnvelopeFeature` imported like a detected one.
    from draftwright.model.compiled import compile_dimensions

    source_feature_ids = {id(feature) for feature in model.features}
    source_detected = bool(model.detected)
    replayed_recognition = (
        model.detected
        and not any(feature.kind == "envelope" for feature in model.features)
        and any(
            omission.code == "overall_dim_withheld"
            for omission in compile_dimensions(model).diagnostics
        )
    )
    model, _synth_env = mirror_model(model)
    declaration_metadata = _declaration_metadata(
        model, source_feature_ids, source_detected, declaration_occurrences
    )
    model_imports = _model_constructor_imports(model)
    ctor = _script_constructor_args(
        script_options, model, settled_layout, pmi_source, assessment, replayed_recognition
    )
    from draftwright.model.declare import _envelope_from_bbox

    object_refs = _object_references(model.features, source_part, object_candidates)
    feature_lines, _names = _feature_block(
        model.features,
        _envelope_from_bbox(model.bbox),
        object_refs,
        model.decorations,
        declaration_metadata,
    )
    # Narrowed to what the BODY actually names. The set above is derived from feature kinds,
    # which over-imports when a kind stops emitting a constructor: a
    # whole-part envelope emits `sheet.envelope()`, so `EnvelopeFeature` and `Frame` were
    # imported and never used, and a user linting their own generated script got F401 on line
    # three. Deriving from the emitted text cannot over-import by construction, and cannot
    # under-import either — a name absent from the body is a name the body does not need.
    _body = "\n".join(feature_lines)
    # Called as a NAME, not merely present as a substring: `hole` occurs inside `sheet.hole(`,
    # so a substring test keeps the import for every script that uses the fluent verb and needs
    # no constructor — which is four of the nineteen corpus fixtures.
    model_imports = {n for n in model_imports if re.search(rf"(?<![.\w]){n}\s*\(", _body)}
    fmts = tuple(formats)
    assessment_imports = []
    assessment_lines = []
    if assessment:
        assessment_path = assessment_sidecar_path(f"{stem}.py")
        assessment_imports = [
            "from draftwright.replay_assessment import prepare_replay_assessment"
        ]
        assessment_lines = [
            "_replay_options = {",
            f'    "pmi_mode": {pmi!r},',
            f'    "formats": {fmts!r},',
            '    "reproducible": True,',
            "}",
            "_replay_assessment = (",
            "    prepare_replay_assessment(",
            f"        {assessment_path!r},",
            "        script_path=__file__,",
            f"        source_path={pmi_source!r},",
            f"        source_name={assessment_source_name!r},",
            '        pmi_mode=_replay_options["pmi_mode"],',
            '        formats=_replay_options["formats"],',
            '        reproducible=_replay_options["reproducible"],',
            "    )",
            "    # In-memory exec has no exact script file to identify, so it builds without",
            "    # pretending to persist a replay assessment.",
            '    if "__file__" in globals()',
            "    else None",
            ")",
            "",
        ]
    lines = [
        _HEADER,
        "from draftwright import Sheet",
        *(
            ["from draftwright.model import " + ", ".join(sorted(model_imports))]
            if model_imports
            else []
        ),
        *assessment_imports,
        "",
        *assessment_lines,
        part_expr,
        "",
        f"sheet = Sheet(part, {', '.join(ctor)})",
        "",
        # The script must SAY where its dimensions come from (ADR 4 (was 0016)): an omitted
        # dimension only means something inside a set that says it is complete. The planner's
        # set is stated here; an AUTHORED set is stated after the features instead, because
        # each of its lines names a feature by the variable that feature's line binds.
        # Every generated script now declares its dimensions below the features, so
        # the source is stated here as a pointer rather than inline: the lines name features
        # by the variables those features' lines bind, and cannot precede them.
        "# The dimension source is DECLARED below the features (ADR 4 (was 0016)).",
        "",
        # For a live-source part, the values below were read off YOUR objects — point
        # each line back at the object to keep it a single source of truth (a STEP-sourced
        # script has no such objects, so this note is emitted only for object inputs).
        *(
            [
                "# Named-source correspondence: unique cylindrical matches below reference",
                "# `features.<name>` directly. Numeric lines are deliberate fail-closed fallbacks",
                "# where source polarity, geometry, or one-to-one identity was not established.",
                "",
            ]
            if object_candidates
            else [
                "# Object-reference tip: you built these objects, so swap a numbered arg for the",
                "# object itself to read the size off it — e.g.  sheet.step(journal)  /",
                "#  sheet.hole(m3_bore).thread('M3x0.5')  — no numbers restated (ADR 4 (was 0011) declare).",
                "",
            ]
            if object_ref
            else []
        ),
        # One commentable line per feature, grouped under section sub-headers with a describing
        # comment on each (ADR 4 (was 0011 Amdt 1): still one declared feature per line — comment out, edit
        # a value, re-run). Runs are consecutive in feature order; never reordered (ADR 2 (was 0014)).
        *feature_lines,
        "",
        *_dimension_block(model, _names, _synth_env),
        *_schedule_block(model, _names),
        "",
        *_layout_override_block(model),
        *([""] if model.layout_overrides else []),
        "# ── Views ─────────────────────────────────────────────────────────────────────",
    ]
    principal_views = tuple(
        name
        for name in (() if settled_layout is None else settled_layout["views"])
        if name in {"front", "plan", "side", "iso"}
    )
    if view_constraints is not None:
        lines += _adopted_view_block(view_constraints, _names)
    elif (
        principal_views
        and principal_views != ("front", "plan", "side", "iso")
        and not (settled_layout is not None and settled_layout.get("pin_views", False))
        and _mirrors_dimensions(model)
    ):
        lines += [
            "# The automatic build settled on this complete view set; it is declared so",
            "# the editable authored-dimension mirror reproduces that resolved layout.",
            "sheet.authored_views()",
            *(f'sheet.view("{name}")' for name in principal_views),
        ]
    elif principal_views and principal_views != ("front", "plan", "side", "iso"):
        # The settled set is known and deliberately NOT declared. This script keeps
        # `sheet.auto_dimensions()` (see the dimension block above for why), and `Sheet`
        # refuses `authored_views()` beside it — emitting both would write a script that
        # raises the moment anyone runs it. Requirement-driven view selection may well reach
        # this same set; it is not guaranteed to, and saying so beats a script that cannot
        # run.
        lines += [
            "# The automatic build settled on: " + ", ".join(principal_views) + ".",
            "# Not declared here: this script keeps auto_dimensions(), and a sheet cannot",
            "# author its views and its dimensions separately (ADR 2 — requirements",
            "# determine views). The view set is re-derived on each run and may differ.",
        ]
    else:
        lines.append("# front / plan / side / iso are produced automatically.")
    if view_constraints is None and _needs_section(model):
        lines.append("# Section A–A auto-triggers from qualifying hidden internal detail above.")
    # Build and export as two statements, so the finalized Drawing has a name. That is
    # the lifecycle the architecture already has — Sheet declares intent, `build()` compiles and
    # solves placement, `Drawing` is the artefact that gets critiqued and serialised — and an
    # editor wanting to lint, inspect `drawing.model()` or export twice can now do it without
    # rewriting the tail or paying for a second build. `Sheet.export` stays as shorthand for
    # handwritten programs; it is this GENERATED tail that has an editor to serve.
    #
    # `formats` is always spelled out because `Drawing.export` requires it and
    # generated scripts should state their requested output explicitly.
    lines += [
        "",
        "# ── Build ─────────────────────────────────────────────────────────────────────",
        "# `build()` returns the finalized Drawing: critique it with `drawing.lint()` or read",
        "# `drawing.model()` here, then export the same object — no second build.",
        "drawing = sheet.build()",
        *(
            [
                "outputs = drawing.export(",
                f"    {stem!r},",
                '    formats=_replay_options["formats"],',
                '    reproducible=_replay_options["reproducible"],',
                ")",
                "if _replay_assessment is not None:",
                "    print(_replay_assessment.write(drawing, outputs))",
            ]
            if assessment
            else [f"drawing.export({stem!r}, formats={fmts!r})"]
        ),
    ]
    return "\n".join(lines) + "\n"


def resolve_object_spec(spec: str) -> tuple[Shape, str]:
    """Resolve a Shape-valued object spec into ``(object, import seam)`` (#469).

    Kept as the compatibility surface for callers that only need the body. The CLI uses the
    richer private resolver so a #1041 features container can also carry named candidates.
    """
    source = _resolve_object_source(spec)
    return source.part, source.seam


def generate_sheet_script(
    step_file: str | Shape,
    out: str | None = None,
    *,
    title: str | None = None,
    number: str = "DWG-001",
    tolerance: str | None = None,
    drawn_by: str = "",
    scale=None,
    scale_policy="fallback",
    page=None,
    material: str | None = None,
    date: str = "",
    revision: str = "A",
    company: str = "",
    approved_by: str = "",
    document_type: str = "",
    sheet: str = "",
    frame: bool = False,
    zones: bool = False,
    projection: str | None = None,
    projection_symbol: bool = True,
    text_position: str = "inline",
    text_orientation: str = "aligned",
    leader_region: str = "auto",
    annotation_layout: str = "demand-guided",
    pmi: Literal["off", "report", "annotate"] = "off",
    part_expr: str | None = None,
    object_candidates: Mapping[str, Shape] | None = None,
    formats: Sequence[str] = ("pdf",),
    inspect: bool = True,
    assessment: bool | None = None,
    margin_left: float | None = None,
    margin_right: float | None = None,
    margin_top: float | None = None,
    margin_bottom: float | None = None,
    title_block_width: float | None = None,
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
    validate_projection(projection, projection_symbol=projection_symbol)
    _validate_scale_policy(scale, scale_policy)
    _dimension_draft(text_position, text_orientation)
    from draftwright.annotation_layout_profile import annotation_layout_policy
    from draftwright.leader_policy import leader_region_policy

    leader_region = leader_region_policy(leader_region).value
    annotation_layout = annotation_layout_policy(annotation_layout)
    is_shape = isinstance(step_file, Shape)
    assessment = inspect if assessment is None else assessment
    stem = out or ("drawing" if is_shape else Path(step_file).stem)
    for _ext in (".py", ".svg", ".dxf"):
        if stem.endswith(_ext):
            stem = stem[: -len(_ext)]
            break
    title = title or (Path(stem).name.replace("_", " ").upper() if not is_shape else "DRAWING")
    script_options = BuildOptions.from_mapping(locals())

    # A STEP path is mutable and may be a retargetable symlink. Resolve its replay seam once,
    # then read one immutable byte snapshot. Recognition, PMI, and any semantic-correction build
    # all consume a private copy of those exact hashed bytes; no endpoint re-hash can be fooled by
    # an A→B→A replacement during recognition (ADR 3 (was 0017 Amendment 24)).
    source_display = None if is_shape else Path(step_file)
    source_resolved = None if source_display is None else source_display.resolve()
    source_bytes = None if source_resolved is None else source_resolved.read_bytes()
    source_sha256 = None if source_bytes is None else hashlib.sha256(source_bytes).hexdigest()

    if part_expr is not None:
        pass  # caller-supplied seam (e.g. an import of a live module)
    elif is_shape:
        part_expr = "part = ...   # ← wire in your build123d object (built above)"
    else:
        # absolute so the generated script runs from any working directory
        assert source_resolved is not None
        abspath = str(source_resolved)
        part_expr = f"from build123d import import_step\npart = import_step({abspath!r})"

    with ExitStack() as source_stack:
        detection_source: str | Shape | Path = step_file
        if source_bytes is not None:
            snapshot_dir = Path(
                source_stack.enter_context(TemporaryDirectory(prefix="draftwright-source-"))
            )
            snapshot_name = source_resolved.name if source_resolved is not None else "source.step"
            detection_source = snapshot_dir / snapshot_name
            detection_source.write_bytes(source_bytes)

        model, analysis = _detect_part_model_analysis(detection_source, pmi=pmi)
        # The evidence document is written beside the script, never into it. It is projected
        # from THIS run — no second aggregate. A build123d object source has no STEP
        # bytes and therefore no document.
        inspection = None
        declaration_occurrences: dict[int, tuple[str, ...]] = {}
        if inspect and source_bytes is not None:
            assert source_display is not None
            try:
                occurrences, _requirements, _summary = project_occurrences(
                    analysis.recognition_evidence,
                    analysis.recognition_ownership,
                    model,
                )
                inspection = _inspection_document(
                    model,
                    analysis,
                    source_display.name,
                    source_bytes,
                    _occurrences=occurrences,
                )
                aligned_occurrences = project_feature_occurrence_ids(model, occurrences)
                declaration_occurrences = {
                    id(feature): occurrence_ids
                    for feature, occurrence_ids in zip(
                        model.features, aligned_occurrences, strict=True
                    )
                }
            except (InspectionUnavailableError, ReportUnavailableError) as error:
                # Never fail script generation over its sidecar, and never drop it in silence.
                _log.warning(
                    "No inspection sidecar written for %s: %s", source_display.name, error
                )
        settled_layout = None
        # Generated scripts declare their dimensions and do not enter automatic layout
        # recovery. Capture the measured layout decision from the same STEP snapshot so
        # the script reproduces the direct drawing. Pass through the same automatic front
        # door: supplying a detected model here changes view selection and ownership.
        # Whether a required dimension fits is known only after building the sheet, so
        # this reference build runs even when the feature model predicts no replan.
        if scale is None:
            settled = _settled_reference_build(
                detection_source,
                _analysis_base=analysis,
                **script_options.script_front_door_kwargs(),
            )
            settled_layout = None if settled is None else settled_layout_for(settled)
        script = emit_sheet_script(
            model,
            part_expr,
            stem,
            **script_options.script_front_door_kwargs(),
            object_ref=is_shape,
            object_candidates=object_candidates,
            source_part=step_file if isinstance(step_file, Shape) else None,
            formats=formats,
            settled_layout=settled_layout,
            pmi_source=None if source_resolved is None else str(source_resolved),
            declaration_occurrences=declaration_occurrences,
            assessment=assessment,
            assessment_source_name=None if source_display is None else source_display.name,
        )
    if source_resolved is not None:
        try:
            replay_sha256 = hashlib.sha256(source_resolved.read_bytes()).hexdigest()
        except OSError as error:
            raise RuntimeError(
                "STEP replay source became unavailable while generating its recognition snapshot"
            ) from error
        if replay_sha256 != source_sha256:
            raise RuntimeError(
                "STEP replay source changed while generating its recognition snapshot"
            )
    py_path = f"{stem}.py"
    # Any previous assessment describes the script we are about to replace.  Clear only a
    # document carrying Draftwright's assessment schema; unrelated data at the derived path is
    # preserved and a real replay will refuse to overwrite it.
    invalidate_replay_assessment(assessment_sidecar_path(py_path))
    Path(py_path).write_text(script, encoding="utf-8")  # the script has box-drawing / × / ← glyphs
    sidecar = inspection_sidecar_path(py_path)
    if inspection is not None:
        write_json_document(inspection, sidecar)
    elif _is_inspection_document(sidecar):
        # An earlier run's document left beside a freshly generated script is evidence about a
        # different part, and nothing in it would say so. Only a document this tool wrote is
        # removed: the path is derived from the caller's stem, so an unrelated file can sit
        # there, and deleting it would destroy data we were never asked to own.
        Path(sidecar).unlink(missing_ok=True)
    return py_path
