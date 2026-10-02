"""Evidence-based STEP analysis evaluation (#1169).

This module scores recogniser observations against an independently authored oracle.  Its
expectations do not inspect ``RecognitionResult`` or the feature census: adapters supply
observations, while the benchmark case supplies the denominator and tolerances.

Every downstream boundary is OBSERVED through its real seam (#1369), never copied from the
capability declaration: the built ``PartModel`` for ``ir_adapter``; an explicit public
``Sheet.hole`` declaration for ``dsl_declaration``; an executed ``emit_sheet_script`` result
for ``generated_code``; and the placed drawing's ADR 5 (was 0010) measurement provenance for
``drawing_consumer``.  The existing hole-requirement ledger supplies one conservative
recognition-to-IR correspondence implementation for all four observations.  It is a join, not
the benchmark denominator: the independently authored corpus remains the only source of
expected facts.

The hole-pattern slice (#1370) uses the same four boundaries through ``Sheet.pattern`` and the
existing hole-requirement correspondence. Its separate corpus scores one arrangement fact per
aggregate pattern. Member diameter/depth/bottom/location requirements stay solely in the hole
corpus, so the derived N:1 group never becomes a second physical-hole denominator.

The Double-D slice (#1370) counts one profiled through-bore occurrence per full physical frame.
Major diameter, A/F, through-depth and the unoriented flat line are scored independently, while
automatic IR, public ``Sheet.double_d_bore``, executed generated code and exact role-specific
``⌀… DOUBLE-D … A/F`` ink must all retain the same occurrence.  This is a separate profile
fact, not a second ordinary-hole fact: provider aggregate ownership excludes its circular parent
from ``RecognitionResult.holes``.

The flat slice (#1371) scores one physical A/F requirement per stock line and axial span. Two
opposed faces on one Double-D are member evidence for one requirement; equal parallel stock and
disjoint coaxial stock remain separate facts. Across-flats and the face anchors are parameters,
not benchmark identity, so weakening either lowers fidelity instead of hiding as a detection
mismatch.

The lone-pocket slice (#1372) excludes members owned by pocket patterns and counts width, length,
depth, plus two independently observed datum-location axes for an interior recess. Edge-anchored
corner interruptions retain their three explicit sizes while their position is intentionally
implicit. Opening side remains identity so opposed-face pockets cannot collapse at the IR waist.

The pocket-pattern slice (#1372) counts one grouped physical arrangement, never its member
pockets again. Its width, length, depth, count, lattice and centre are observed through the same
four boundaries, including exact count/pitch/location ink backed by compiler provenance.

The groove slice (#1372) counts one annular recess at each turning-axis station. Axis and physical
anchor identify the occurrence; axial width and floor diameter are scored parameters and required
drawing measurements. The drawing observation requires both compiler-approved identities on one
exact semantic ``WIDE × ø`` callout, while the corpus remains independent of recognition output.

The rectangular-pad slice (#1372) counts one bounded protrusion at each signed attachment-plane
centre. Principal axis, material-outward direction, and attachment point identify the occurrence;
footprint width/length and local height are scored parameters. The drawing observation follows all
five physical requirements through compiler measurement identities and structured directional
location facts without treating the older geometric coverage fallback as semantic evidence.

The Plate slice (#1373) counts one body-local thin slab whose thickness is not already owned by
the whole-part envelope. Thin axis, physical slab station and both independently authored
transverse witness coordinates identify the occurrence; thickness is the scored parameter.
Automatic IR, public declaration and executed generated code must preserve the full witness, and
the drawing must carry the exact compiler-owned thickness identity and verified ink.

The polygonal-boss slice (#1372) counts one attached regular prism per principal axis and physical
centre. Side count, A/F, height, ordered flat directions and physical flat centres are scored
parameters. Automatic IR, public declaration and executed generated code must retain the complete
prism record; the drawing must carry the exact A/F and height compiler identities, valid statement
ink and a live A/F leader on one retained physical support face.

The polygonal-stock slice (#1371) counts one complete regular-hexagonal-prism body. Principal axis
and physical centre identify the occurrence; side count, A/F, axial length and the coupled ring of
flat directions/physical centres are scored parameters. Exact cap span and support geometry remain
load-bearing correspondence evidence through automatic IR, public declaration and generated code.
The drawing must carry both compiler identities, exact statement ink, and a live A/F leader on one
retained physical support face. Attached bosses, machined/irregular prisms and compounds remain
outside this whole-stock denominator.

The chamfer slice (#1374) counts one planar or conical bevel per physical anchor. Axis, anchor and
surface form identify the occurrence; both legs and angle are scored parameters. One compiler
identity reaches exact ``C`` or ``leg × angle`` ink and a live leader on the bevel/profile station;
equal specifications may share ink only while retaining every member identity.

The fillet slice (#1374) counts one cylindrical or toroidal round per physical surface anchor.
Axis, anchor and planar/turned form identify the occurrence; radius is the scored parameter. One
compiler identity per round reaches exact ``R`` or grouped ``n× R`` ink and a live leader on the
round/profile station. Aggregate ownership excludes curved walls assigned to CircularBlindStep.

The turned-step slice (#1374) counts one outside-diameter band per body-local axis line and axial
station. Length and diameter are independently scored parameters and drawing requirements. A
band uniquely owned by a correlated groove remains solely in the groove denominator; ambiguous
nested/coaxial groove ownership is refused under the provider contract rather than guessed.

Known limit of the drawing observation: it reads the ADR 5 (was 0010) provenance seam, which
``registry.measurement_of`` carries and which is populated one render pass at a time (the set
of tagged renderers is enumerated by ``tests/test_audit_differential.py``, not by prose here —
that docstring warns the prose version was wrong when first written). An un-tagged render pass
therefore reads as a genuine omission, and this is a CLASS of limitation rather than a single
case. Two instances are known:

* the hole-table escalation, which withdraws the individual callouts and records the
  substitution on the table — admitted here via that ledger;
* a **turned** part, where the bore's diameter reaches the sheet as a ``Leader`` but the hole
  requirement ledger still reports the bore size as missing. The benchmark therefore reports
  a loss for a hole whose size is visibly printed. No corpus fixture is turned today; adding
  one without closing that correspondence gap would make the number wrong.

A new representation route must be admitted here or it registers as a false loss.

The module-level imports of ``evaluation._double_d_evidence``,
``evaluation._turned_step_evidence``, ``evaluation._groove_evidence``,
``evaluation._edge_profile_evidence``,
``evaluation._flat_polygonal_evidence``,
``evaluation._hole_family_evidence``,
``evaluation._pocket_evidence`` and ``evaluation._prismatic_evidence`` load only
standard-library dependencies. **Every engine import remains inside a function body**,
preserving the #313 lazy-load pattern.
(`quiddity` counts: importing it puts build123d in `sys.modules`, so it carries the
same cost.) It is load-bearing: importing this module took ~0.02 s in five fresh local
processes, and hoisting ANY engine import makes it
one to two seconds, because every one pulls build123d transitively. Measured in a single process,
the cost is essentially all build123d and is paid once — the draftwright modules themselves are
free once it is loaded::

    build123d                          (the whole cost)
    draftwright.linting.hole_coverage  ~0.02-0.05 s   (after build123d)
    draftwright.model.compiled         ~0.01-0.03 s
    draftwright.linting.evidence        0.000 s
    draftwright.builder                ~0.01-0.02 s

Absolute seconds are deliberately not quoted for build123d: measurements on two machines gave
1.35 s and 2.26 s. The SHAPE is the point and it reproduces. (An earlier version listed four
figures of 1.4-2.0 s, one per module, from four separate cold processes — the same one-time cost
measured four times and presented as if the modules differed. They do not.)

#1229 filed three of these imports as "unexplained, hoist or justify"; measuring is what showed
the filing was wrong, and this note is the justification it asked for. Keep new engine imports
inside the bodies too.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from hashlib import sha256
from math import isfinite
from pathlib import Path
from typing import Any, Literal, Protocol, TypeAlias

from draftwright.evaluation._double_d_evidence import (
    _DOUBLE_D_CALLOUT_RE as _DOUBLE_D_CALLOUT_RE,
)
from draftwright.evaluation._double_d_evidence import (
    _DOUBLE_D_GEOMETRY_TOLERANCE as _DOUBLE_D_GEOMETRY_TOLERANCE,
)
from draftwright.evaluation._double_d_evidence import (
    _DOUBLE_D_REQUIREMENTS as _DOUBLE_D_REQUIREMENTS,
)
from draftwright.evaluation._double_d_evidence import (
    _DOUBLE_D_SPAN_TOLERANCE as _DOUBLE_D_SPAN_TOLERANCE,
)
from draftwright.evaluation._double_d_evidence import (
    _declared_double_d_model as _declared_double_d_model,
)
from draftwright.evaluation._double_d_evidence import (
    _double_d_axis as _double_d_axis,
)
from draftwright.evaluation._double_d_evidence import (
    _double_d_callout_has_roles as _double_d_callout_has_roles,
)
from draftwright.evaluation._double_d_evidence import (
    _double_d_correspondence as _double_d_correspondence,
)
from draftwright.evaluation._double_d_evidence import (
    _double_d_direction as _double_d_direction,
)
from draftwright.evaluation._double_d_evidence import (
    _double_d_drawing_outcomes as _double_d_drawing_outcomes,
)
from draftwright.evaluation._double_d_evidence import (
    _double_d_exclusive_owners as _double_d_exclusive_owners,
)
from draftwright.evaluation._double_d_evidence import (
    _double_d_feature_key as _double_d_feature_key,
)
from draftwright.evaluation._double_d_evidence import (
    _double_d_model_outcomes as _double_d_model_outcomes,
)
from draftwright.evaluation._double_d_evidence import (
    _double_d_number as _double_d_number,
)
from draftwright.evaluation._double_d_evidence import (
    _double_d_record_key as _double_d_record_key,
)
from draftwright.evaluation._double_d_evidence import (
    _double_d_span as _double_d_span,
)
from draftwright.evaluation._double_d_evidence import (
    _double_d_spans_match as _double_d_spans_match,
)
from draftwright.evaluation._double_d_evidence import (
    _double_d_vector as _double_d_vector,
)
from draftwright.evaluation._edge_profile_evidence import (
    _chamfer_correspondence as _chamfer_correspondence,
)
from draftwright.evaluation._edge_profile_evidence import (
    _chamfer_drawing_outcomes as _chamfer_drawing_outcomes,
)
from draftwright.evaluation._edge_profile_evidence import (
    _chamfer_identity as _chamfer_identity,
)
from draftwright.evaluation._edge_profile_evidence import (
    _chamfer_model_outcomes as _chamfer_model_outcomes,
)
from draftwright.evaluation._edge_profile_evidence import (
    _chamfer_parameters as _chamfer_parameters,
)
from draftwright.evaluation._edge_profile_evidence import (
    _chamfer_point as _chamfer_point,
)
from draftwright.evaluation._edge_profile_evidence import (
    _declared_chamfer_model as _declared_chamfer_model,
)
from draftwright.evaluation._edge_profile_evidence import (
    _declared_fillet_model as _declared_fillet_model,
)
from draftwright.evaluation._edge_profile_evidence import (
    _fillet_correspondence as _fillet_correspondence,
)
from draftwright.evaluation._edge_profile_evidence import (
    _fillet_drawing_outcomes as _fillet_drawing_outcomes,
)
from draftwright.evaluation._edge_profile_evidence import (
    _fillet_identity as _fillet_identity,
)
from draftwright.evaluation._edge_profile_evidence import (
    _fillet_model_outcomes as _fillet_model_outcomes,
)
from draftwright.evaluation._edge_profile_evidence import (
    _fillet_parameters as _fillet_parameters,
)
from draftwright.evaluation._edge_profile_evidence import (
    _fillet_point as _fillet_point,
)
from draftwright.evaluation._flat_polygonal_evidence import (
    _declared_flat_model as _declared_flat_model,
)
from draftwright.evaluation._flat_polygonal_evidence import (
    _declared_polygonal_boss_model as _declared_polygonal_boss_model,
)
from draftwright.evaluation._flat_polygonal_evidence import (
    _declared_polygonal_stock_model as _declared_polygonal_stock_model,
)
from draftwright.evaluation._flat_polygonal_evidence import (
    _flat_correspondence as _flat_correspondence,
)
from draftwright.evaluation._flat_polygonal_evidence import (
    _flat_drawing_outcomes as _flat_drawing_outcomes,
)
from draftwright.evaluation._flat_polygonal_evidence import (
    _flat_groups as _flat_groups,
)
from draftwright.evaluation._flat_polygonal_evidence import (
    _flat_identity as _flat_identity,
)
from draftwright.evaluation._flat_polygonal_evidence import (
    _flat_model_outcomes as _flat_model_outcomes,
)
from draftwright.evaluation._flat_polygonal_evidence import (
    _flat_parameters as _flat_parameters,
)
from draftwright.evaluation._flat_polygonal_evidence import (
    _flat_point as _flat_point,
)
from draftwright.evaluation._flat_polygonal_evidence import (
    _polygonal_boss_center as _polygonal_boss_center,
)
from draftwright.evaluation._flat_polygonal_evidence import (
    _polygonal_boss_correspondence as _polygonal_boss_correspondence,
)
from draftwright.evaluation._flat_polygonal_evidence import (
    _polygonal_boss_drawing_outcomes as _polygonal_boss_drawing_outcomes,
)
from draftwright.evaluation._flat_polygonal_evidence import (
    _polygonal_boss_identity as _polygonal_boss_identity,
)
from draftwright.evaluation._flat_polygonal_evidence import (
    _polygonal_boss_model_outcomes as _polygonal_boss_model_outcomes,
)
from draftwright.evaluation._flat_polygonal_evidence import (
    _polygonal_boss_parameters as _polygonal_boss_parameters,
)
from draftwright.evaluation._flat_polygonal_evidence import (
    _polygonal_stock_center as _polygonal_stock_center,
)
from draftwright.evaluation._flat_polygonal_evidence import (
    _polygonal_stock_correspondence as _polygonal_stock_correspondence,
)
from draftwright.evaluation._flat_polygonal_evidence import (
    _polygonal_stock_drawing_outcomes as _polygonal_stock_drawing_outcomes,
)
from draftwright.evaluation._flat_polygonal_evidence import (
    _polygonal_stock_identity as _polygonal_stock_identity,
)
from draftwright.evaluation._flat_polygonal_evidence import (
    _polygonal_stock_model_outcomes as _polygonal_stock_model_outcomes,
)
from draftwright.evaluation._flat_polygonal_evidence import (
    _polygonal_stock_parameters as _polygonal_stock_parameters,
)
from draftwright.evaluation._groove_evidence import (
    _declared_groove_model as _declared_groove_model,
)
from draftwright.evaluation._groove_evidence import (
    _groove_correspondence as _groove_correspondence,
)
from draftwright.evaluation._groove_evidence import (
    _groove_drawing_outcomes as _groove_drawing_outcomes,
)
from draftwright.evaluation._groove_evidence import (
    _groove_expected_tolerance_suffix as _groove_expected_tolerance_suffix,
)
from draftwright.evaluation._groove_evidence import (
    _groove_identity as _groove_identity,
)
from draftwright.evaluation._groove_evidence import (
    _groove_model_outcomes as _groove_model_outcomes,
)
from draftwright.evaluation._groove_evidence import (
    _groove_parameters as _groove_parameters,
)
from draftwright.evaluation._groove_evidence import (
    _groove_point as _groove_point,
)
from draftwright.evaluation._hole_family_evidence import (
    _COUNTERSINK_REQUIREMENTS as _COUNTERSINK_REQUIREMENTS,
)
from draftwright.evaluation._hole_family_evidence import (
    _COUNTERSINK_TERM_RE as _COUNTERSINK_TERM_RE,
)
from draftwright.evaluation._hole_family_evidence import (
    _SIZE_REQUIREMENT as _SIZE_REQUIREMENT,
)
from draftwright.evaluation._hole_family_evidence import (
    _countersink_claim_has_role_specific_ink as _countersink_claim_has_role_specific_ink,
)
from draftwright.evaluation._hole_family_evidence import (
    _countersink_drawing_outcomes as _countersink_drawing_outcomes,
)
from draftwright.evaluation._hole_family_evidence import (
    _countersink_model_outcomes as _countersink_model_outcomes,
)
from draftwright.evaluation._hole_family_evidence import (
    _countersink_sites as _countersink_sites,
)
from draftwright.evaluation._hole_family_evidence import (
    _declared_hole_model as _declared_hole_model,
)
from draftwright.evaluation._hole_family_evidence import (
    _declared_pattern_model as _declared_pattern_model,
)
from draftwright.evaluation._hole_family_evidence import (
    _drawing_consumer_outcomes as _drawing_consumer_outcomes,
)
from draftwright.evaluation._hole_family_evidence import (
    _hole_model_outcomes as _hole_model_outcomes,
)
from draftwright.evaluation._hole_family_evidence import (
    _pattern_drawing_outcomes as _pattern_drawing_outcomes,
)
from draftwright.evaluation._hole_family_evidence import (
    _pattern_kind as _pattern_kind,
)
from draftwright.evaluation._hole_family_evidence import (
    _pattern_members as _pattern_members,
)
from draftwright.evaluation._hole_family_evidence import (
    _pattern_model_outcomes as _pattern_model_outcomes,
)
from draftwright.evaluation._pocket_evidence import (
    _declared_pocket_model as _declared_pocket_model,
)
from draftwright.evaluation._pocket_evidence import (
    _declared_pocket_pattern_model as _declared_pocket_pattern_model,
)
from draftwright.evaluation._pocket_evidence import (
    _lone_pockets as _lone_pockets,
)
from draftwright.evaluation._pocket_evidence import (
    _pocket_correspondence as _pocket_correspondence,
)
from draftwright.evaluation._pocket_evidence import (
    _pocket_declaration_arguments as _pocket_declaration_arguments,
)
from draftwright.evaluation._pocket_evidence import (
    _pocket_depth_axis as _pocket_depth_axis,
)
from draftwright.evaluation._pocket_evidence import (
    _pocket_drawing_outcomes as _pocket_drawing_outcomes,
)
from draftwright.evaluation._pocket_evidence import (
    _pocket_geometry as _pocket_geometry,
)
from draftwright.evaluation._pocket_evidence import (
    _pocket_identity as _pocket_identity,
)
from draftwright.evaluation._pocket_evidence import (
    _pocket_model_outcomes as _pocket_model_outcomes,
)
from draftwright.evaluation._pocket_evidence import (
    _pocket_parameter_ids as _pocket_parameter_ids,
)
from draftwright.evaluation._pocket_evidence import (
    _pocket_parameters as _pocket_parameters,
)
from draftwright.evaluation._pocket_evidence import (
    _pocket_pattern_angle as _pocket_pattern_angle,
)
from draftwright.evaluation._pocket_evidence import (
    _pocket_pattern_correspondence as _pocket_pattern_correspondence,
)
from draftwright.evaluation._pocket_evidence import (
    _pocket_pattern_drawing_outcomes as _pocket_pattern_drawing_outcomes,
)
from draftwright.evaluation._pocket_evidence import (
    _pocket_pattern_model_outcomes as _pocket_pattern_model_outcomes,
)
from draftwright.evaluation._pocket_evidence import (
    _pocket_pattern_pitch_gaps as _pocket_pattern_pitch_gaps,
)
from draftwright.evaluation._pocket_evidence import (
    _pocket_point as _pocket_point,
)
from draftwright.evaluation._pocket_evidence import (
    _supported_pocket_patterns as _supported_pocket_patterns,
)
from draftwright.evaluation._prismatic_evidence import (
    _PAD_PLANE_AXES as _PAD_PLANE_AXES,
)
from draftwright.evaluation._prismatic_evidence import (
    _declared_pad_model as _declared_pad_model,
)
from draftwright.evaluation._prismatic_evidence import (
    _declared_plate_model as _declared_plate_model,
)
from draftwright.evaluation._prismatic_evidence import (
    _pad_axis as _pad_axis,
)
from draftwright.evaluation._prismatic_evidence import (
    _pad_bounds as _pad_bounds,
)
from draftwright.evaluation._prismatic_evidence import (
    _pad_correspondence as _pad_correspondence,
)
from draftwright.evaluation._prismatic_evidence import (
    _pad_drawing_outcomes as _pad_drawing_outcomes,
)
from draftwright.evaluation._prismatic_evidence import (
    _pad_expected_parameters as _pad_expected_parameters,
)
from draftwright.evaluation._prismatic_evidence import (
    _pad_identity as _pad_identity,
)
from draftwright.evaluation._prismatic_evidence import (
    _pad_model_outcomes as _pad_model_outcomes,
)
from draftwright.evaluation._prismatic_evidence import (
    _pad_pair as _pad_pair,
)
from draftwright.evaluation._prismatic_evidence import (
    _pad_parameters as _pad_parameters,
)
from draftwright.evaluation._prismatic_evidence import (
    _plate_correspondence as _plate_correspondence,
)
from draftwright.evaluation._prismatic_evidence import (
    _plate_drawing_outcomes as _plate_drawing_outcomes,
)
from draftwright.evaluation._prismatic_evidence import (
    _plate_identity as _plate_identity,
)
from draftwright.evaluation._prismatic_evidence import (
    _plate_model_outcomes as _plate_model_outcomes,
)
from draftwright.evaluation._prismatic_evidence import (
    _plate_parameters as _plate_parameters,
)
from draftwright.evaluation._turned_step_evidence import (
    _turned_step_correspondence as _turned_step_correspondence,
)
from draftwright.evaluation._turned_step_evidence import (
    _turned_step_drawing_outcomes as _turned_step_drawing_outcomes,
)
from draftwright.evaluation._turned_step_evidence import (
    _turned_step_model_outcomes as _turned_step_model_outcomes,
)

Scalar: TypeAlias = int | float | str | bool
Value: TypeAlias = Scalar | tuple[float, ...]
Outcome: TypeAlias = Literal["supported", "unknown", "unsupported"]
Observer: TypeAlias = Callable[[object], Sequence["ObservedFact"]]


@dataclass(frozen=True)
class _BuildAttempt:
    part: object
    repair: bool
    drawing: Any | None = None
    error: Exception | None = None


class _PreparedObserver(Protocol):
    def __call__(
        self, part: object, *, build: _BuildAttempt | None = None
    ) -> Sequence[ObservedFact]: ...


def _attempt_build(part: object, *, repair: bool = True) -> _BuildAttempt:
    # The lazy concrete-module import avoids the package-root upward edge and OCC startup
    # when loading only the corpus schema.
    from draftwright.builder import build_drawing

    try:
        if repair:
            return _BuildAttempt(part, repair, drawing=build_drawing(part))  # type: ignore[arg-type]
        return _BuildAttempt(part, repair, drawing=build_drawing(part, repair=False))  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 — each observer owns its existing failure policy
        return _BuildAttempt(part, repair, error=exc)


def _drawing_for_observation(
    part: object, *, build: _BuildAttempt | None, repair: bool = True
) -> Any:
    if build is not None and build.part is not part:
        raise ValueError("observation build belongs to another imported part")
    if build is not None and build.repair is not repair:
        raise ValueError("observation build uses another repair policy")
    attempt = build if build is not None else _attempt_build(part, repair=repair)
    if attempt.error is not None:
        raise attempt.error
    return attempt.drawing


_log = logging.getLogger(__name__)

_CORPUS_FORMAT = "draftwright-step-analysis-corpus"
_CORPUS_FORMAT_VERSION = 1
_METRIC_VERSION = 1
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_DOWNSTREAM_BOUNDARIES = frozenset(
    {"ir_adapter", "dsl_declaration", "generated_code", "drawing_consumer"}
)


class CorpusError(ValueError):
    """The independent benchmark corpus is malformed or its evidence changed."""


class ObservationError(RuntimeError):
    """A family observer could not distinguish an honest empty inventory from failure."""

    def __init__(self, family: str, message: str) -> None:
        super().__init__(message)
        self.family = family


@dataclass(frozen=True)
class ParameterExpectation:
    """An independently authored value and its absolute acceptance tolerance."""

    value: Value
    absolute_tolerance: float = 0.0


@dataclass(frozen=True)
class ExpectedFact:
    """One physical fact in the benchmark denominator."""

    family: str
    identity: Mapping[str, ParameterExpectation]
    parameters: Mapping[str, ParameterExpectation]
    required_downstream: tuple[str, ...] = ()


@dataclass(frozen=True)
class ObservedFact:
    """One normalized fact emitted by the system under evaluation.

    No benchmark identifier is accepted.  Matching is derived from family and independently
    specified physical identity fields, preventing an adapter from copying the oracle's answer.
    """

    family: str
    identity: Mapping[str, Value]
    parameters: Mapping[str, Value]
    downstream: Mapping[str, str]


@dataclass(frozen=True)
class BenchmarkCase:
    """One independently sourced STEP fixture and its expected facts."""

    case_id: str
    classification: str
    expected: tuple[ExpectedFact, ...]
    provenance: Mapping[str, str]
    expected_outcome: Outcome = "supported"


@dataclass(frozen=True)
class BenchmarkCorpus:
    """A validated, versioned collection of independently authored cases."""

    corpus_version: str
    metric_version: int
    scope: tuple[str, ...]
    cases: tuple[BenchmarkCase, ...]


@dataclass(frozen=True)
class DetectionScore:
    recall: float | None
    false_positive_rate: float
    matched: int
    missed: int
    false_positives: int


@dataclass(frozen=True)
class LayerScore:
    score: float | None
    passed: int
    total: int


@dataclass(frozen=True)
class Diagnostic:
    layer: str
    family: str
    message: str
    parameter: str | None = None
    expected: Value | None = None
    observed: Value | None = None


@dataclass(frozen=True)
class CaseEvaluation:
    case_id: str
    expected_outcome: Outcome
    outcome: Outcome
    detection: DetectionScore
    parameter_fidelity: LayerScore
    downstream_usefulness: LayerScore
    diagnostics: tuple[Diagnostic, ...]

    @property
    def conformant(self) -> bool:
        """Whether the observation matches the oracle, including an honest non-answer."""
        return bool(
            self.outcome == self.expected_outcome
            and self.detection.recall in (None, 1.0)
            and self.detection.false_positives == 0
            and self.parameter_fidelity.score in (None, 1.0)
            and self.downstream_usefulness.score in (None, 1.0)
        )

    @property
    def complete(self) -> bool:
        """Whether every independently expected layer is satisfied without false claims."""
        return self.outcome == "supported" and self.conformant


@dataclass(frozen=True)
class CorpusEvaluation:
    """Micro-averaged evidence layers; deliberately no composite scalar."""

    corpus_version: str | None
    metric_version: int
    cases: tuple[CaseEvaluation, ...]
    detection: DetectionScore
    parameter_fidelity: LayerScore
    downstream_usefulness: LayerScore
    conformant_cases: int
    complete_cases: int


def _canonical(value: object) -> str:
    def default(item: object) -> object:
        if isinstance(item, ParameterExpectation):
            return {"value": item.value, "absolute_tolerance": item.absolute_tolerance}
        if hasattr(item, "__dict__"):
            return vars(item)
        raise TypeError(f"cannot canonicalize {type(item).__name__}")

    return json.dumps(value, default=default, sort_keys=True, separators=(",", ":"))


def _within(expectation: ParameterExpectation, observed: Value) -> bool:
    expected = expectation.value
    if isinstance(expected, tuple):
        if not isinstance(observed, tuple) or len(expected) != len(observed):
            return False
        return all(
            abs(float(actual) - wanted) <= expectation.absolute_tolerance
            for wanted, actual in zip(expected, observed, strict=True)
        )
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        if not isinstance(observed, (int, float)) or isinstance(observed, bool):
            return False
        return abs(float(observed) - float(expected)) <= expectation.absolute_tolerance
    return observed == expected


def _identity_matches(expected: ExpectedFact, observed: ObservedFact) -> bool:
    return bool(
        expected.family == observed.family
        and all(
            name in observed.identity and _within(expectation, observed.identity[name])
            for name, expectation in expected.identity.items()
        )
    )


def _expectations_disjoint(first: ParameterExpectation, second: ParameterExpectation) -> bool:
    left = first.value
    right = second.value
    if isinstance(left, tuple) or isinstance(right, tuple):
        if not isinstance(left, tuple) or not isinstance(right, tuple) or len(left) != len(right):
            return True
        return any(
            abs(left_component - right_component)
            > first.absolute_tolerance + second.absolute_tolerance
            for left_component, right_component in zip(left, right, strict=True)
        )
    if (
        isinstance(left, (int, float))
        and not isinstance(left, bool)
        and isinstance(right, (int, float))
        and not isinstance(right, bool)
    ):
        return (
            abs(float(left) - float(right)) > first.absolute_tolerance + second.absolute_tolerance
        )
    return type(left) is not type(right) or left != right


def _facts_are_distinguishable(first: ExpectedFact, second: ExpectedFact) -> bool:
    if first.family != second.family:
        return True
    shared = set(first.identity) & set(second.identity)
    return any(
        _expectations_disjoint(first.identity[name], second.identity[name]) for name in shared
    )


def _maximum_matching(
    expected: Sequence[ExpectedFact], observations: Sequence[ObservedFact]
) -> list[tuple[int, int]]:
    """Return a deterministic maximum bipartite match, independent of topology ordering."""
    ordered_expected = sorted(enumerate(expected), key=lambda item: (_canonical(item[1]), item[0]))
    ordered_observed = sorted(
        enumerate(observations), key=lambda item: (_canonical(item[1]), item[0])
    )
    candidates = [
        [
            index
            for index, (_, observed) in enumerate(ordered_observed)
            if _identity_matches(fact, observed)
        ]
        for _, fact in ordered_expected
    ]
    owner: dict[int, int] = {}

    def assign(expected_index: int, visited: set[int]) -> bool:
        for observed_index in candidates[expected_index]:
            if observed_index in visited:
                continue
            visited.add(observed_index)
            previous = owner.get(observed_index)
            if previous is None or assign(previous, visited):
                owner[observed_index] = expected_index
                return True
        return False

    for expected_index in range(len(ordered_expected)):
        assign(expected_index, set())
    return sorted(
        (
            (ordered_expected[expected_index][0], ordered_observed[observed_index][0])
            for observed_index, expected_index in owner.items()
        ),
        key=lambda pair: (
            _canonical(expected[pair[0]]),
            _canonical(observations[pair[1]]),
            pair,
        ),
    )


def _layer_score(passed: int, total: int) -> LayerScore:
    return LayerScore(score=passed / total if total else None, passed=passed, total=total)


def evaluate_case(
    case: BenchmarkCase,
    *,
    observations: Sequence[ObservedFact],
    outcome: Outcome = "supported",
) -> CaseEvaluation:
    """Score one case without deriving either expectations or tolerances from observations."""
    if outcome not in {"supported", "unknown", "unsupported"}:
        raise ValueError(f"invalid analysis outcome {outcome!r}")
    if outcome != "supported" and observations:
        raise ValueError(f"{outcome} analysis cannot also claim observed facts")

    matches = _maximum_matching(case.expected, observations)
    matched_expected = {expected_index for expected_index, _ in matches}
    matched_observed = {observed_index for _, observed_index in matches}
    missed = len(case.expected) - len(matches)
    false_positives = len(observations) - len(matches)
    diagnostics: list[Diagnostic] = []

    for expected_index, expected in sorted(
        enumerate(case.expected), key=lambda item: (_canonical(item[1]), item[0])
    ):
        if expected_index not in matched_expected:
            diagnostics.append(
                Diagnostic("detection", expected.family, "expected physical fact was not detected")
            )
    for observed_index, observed in sorted(
        enumerate(observations), key=lambda item: (_canonical(item[1]), item[0])
    ):
        if observed_index not in matched_observed:
            diagnostics.append(
                Diagnostic(
                    "detection", observed.family, "observation has no expected physical fact"
                )
            )

    parameter_passed = 0
    parameter_total = 0
    downstream_passed = 0
    downstream_total = 0
    for expected_index, observed_index in matches:
        expected = case.expected[expected_index]
        observed = observations[observed_index]
        for name, expectation in sorted(expected.parameters.items()):
            parameter_total += 1
            actual = observed.parameters.get(name)
            if actual is not None and _within(expectation, actual):
                parameter_passed += 1
            else:
                diagnostics.append(
                    Diagnostic(
                        "parameter_fidelity",
                        expected.family,
                        "parameter is missing or outside its authored tolerance",
                        parameter=name,
                        expected=expectation.value,
                        observed=actual,
                    )
                )
        for boundary in sorted(expected.required_downstream):
            downstream_total += 1
            actual_state = observed.downstream.get(boundary)
            if actual_state == "supported":
                downstream_passed += 1
            else:
                diagnostics.append(
                    Diagnostic(
                        "downstream_usefulness",
                        expected.family,
                        f"required boundary {boundary!r} is {actual_state or 'unknown'}",
                        parameter=boundary,
                        expected="supported",
                        observed=actual_state,
                    )
                )

    detection = DetectionScore(
        recall=len(matches) / len(case.expected) if case.expected else None,
        false_positive_rate=(false_positives / len(observations) if observations else 0.0),
        matched=len(matches),
        missed=missed,
        false_positives=false_positives,
    )
    return CaseEvaluation(
        case_id=case.case_id,
        expected_outcome=case.expected_outcome,
        outcome=outcome,
        detection=detection,
        parameter_fidelity=_layer_score(parameter_passed, parameter_total),
        downstream_usefulness=_layer_score(downstream_passed, downstream_total),
        diagnostics=tuple(diagnostics),
    )


def evaluate_corpus(
    cases: Sequence[CaseEvaluation], *, corpus_version: str | None = None
) -> CorpusEvaluation:
    """Aggregate raw units across cases without averaging away small-case failures."""
    ordered = tuple(sorted(cases, key=lambda case: case.case_id))
    matched = sum(case.detection.matched for case in ordered)
    missed = sum(case.detection.missed for case in ordered)
    false_positives = sum(case.detection.false_positives for case in ordered)
    observations = matched + false_positives
    expected = matched + missed
    parameter_passed = sum(case.parameter_fidelity.passed for case in ordered)
    parameter_total = sum(case.parameter_fidelity.total for case in ordered)
    downstream_passed = sum(case.downstream_usefulness.passed for case in ordered)
    downstream_total = sum(case.downstream_usefulness.total for case in ordered)
    return CorpusEvaluation(
        corpus_version=corpus_version,
        metric_version=_METRIC_VERSION,
        cases=ordered,
        detection=DetectionScore(
            recall=matched / expected if expected else None,
            false_positive_rate=false_positives / observations if observations else 0.0,
            matched=matched,
            missed=missed,
            false_positives=false_positives,
        ),
        parameter_fidelity=_layer_score(parameter_passed, parameter_total),
        downstream_usefulness=_layer_score(downstream_passed, downstream_total),
        conformant_cases=sum(case.conformant for case in ordered),
        complete_cases=sum(case.complete for case in ordered),
    )


def _expect_object(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise CorpusError(f"{context} must be an object with string keys")
    return value


def _require_keys(
    value: dict[str, Any], *, required: set[str], optional: set[str], context: str
) -> None:
    missing = required - set(value)
    extra = set(value) - required - optional
    if missing or extra:
        raise CorpusError(
            f"{context} keys are not format-{_CORPUS_FORMAT_VERSION} compliant; "
            f"missing={sorted(missing)}, extra={sorted(extra)}"
        )


def _expect_string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CorpusError(f"{context} must be a non-empty string")
    return value


def _expect_string_list(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise CorpusError(f"{context} must be an array")
    result = tuple(_expect_string(item, f"{context} entry") for item in value)
    if len(set(result)) != len(result):
        raise CorpusError(f"{context} entries must be unique")
    return result


def _expectation(value: object, context: str) -> ParameterExpectation:
    raw = _expect_object(value, context)
    _require_keys(raw, required={"value"}, optional={"absolute_tolerance"}, context=context)
    expected: Value
    item = raw["value"]
    if isinstance(item, list):
        if not item or not all(
            isinstance(component, (int, float))
            and not isinstance(component, bool)
            and isfinite(component)
            for component in item
        ):
            raise CorpusError(f"{context}.value vector must contain only finite numbers")
        expected = tuple(float(component) for component in item)
    elif isinstance(item, (int, float)) and not isinstance(item, bool):
        if not isfinite(item):
            raise CorpusError(f"{context}.value must be finite")
        expected = item
    elif isinstance(item, (str, bool)):
        expected = item
    else:
        raise CorpusError(f"{context}.value has unsupported type {type(item).__name__}")
    tolerance = raw.get("absolute_tolerance", 0.0)
    if (
        not isinstance(tolerance, (int, float))
        or isinstance(tolerance, bool)
        or not isfinite(tolerance)
        or tolerance < 0
    ):
        raise CorpusError(f"{context}.absolute_tolerance must be a finite non-negative number")
    if isinstance(expected, (str, bool)) and tolerance != 0:
        raise CorpusError(f"{context} cannot apply numeric tolerance to {type(expected).__name__}")
    return ParameterExpectation(expected, float(tolerance))


def _fact(value: object, *, scope: tuple[str, ...], context: str) -> ExpectedFact:
    raw = _expect_object(value, context)
    _require_keys(
        raw,
        required={"family", "identity", "parameters", "required_downstream"},
        optional=set(),
        context=context,
    )
    family = _expect_string(raw["family"], f"{context}.family")
    if family not in scope:
        raise CorpusError(f"{context}.family {family!r} is outside corpus scope {scope!r}")
    identity_raw = _expect_object(raw["identity"], f"{context}.identity")
    if not identity_raw:
        raise CorpusError(f"{context}.identity must independently distinguish the fact")
    parameters_raw = _expect_object(raw["parameters"], f"{context}.parameters")
    downstream = _expect_string_list(raw["required_downstream"], f"{context}.required_downstream")
    unknown_boundaries = set(downstream) - _DOWNSTREAM_BOUNDARIES
    if unknown_boundaries:
        raise CorpusError(
            f"{context} has unknown downstream boundaries {sorted(unknown_boundaries)}"
        )
    return ExpectedFact(
        family=family,
        identity={
            name: _expectation(item, f"{context}.identity.{name}")
            for name, item in sorted(identity_raw.items())
        },
        parameters={
            name: _expectation(item, f"{context}.parameters.{name}")
            for name, item in sorted(parameters_raw.items())
        },
        required_downstream=downstream,
    )


def load_corpus(path: str | Path) -> BenchmarkCorpus:
    """Load and fail-closed validate a corpus, including every fixture hash."""
    corpus_path = Path(path).resolve()
    try:
        raw_value = json.loads(corpus_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CorpusError(f"cannot read corpus {corpus_path}: {error}") from error
    raw = _expect_object(raw_value, "corpus")
    _require_keys(
        raw,
        required={
            "format",
            "format_version",
            "corpus_version",
            "metric_version",
            "scope",
            "cases",
        },
        optional=set(),
        context="corpus",
    )
    if raw["format"] != _CORPUS_FORMAT or raw["format_version"] != _CORPUS_FORMAT_VERSION:
        raise CorpusError(
            f"unsupported corpus format {raw['format']!r} version {raw['format_version']!r}"
        )
    corpus_version = _expect_string(raw["corpus_version"], "corpus.corpus_version")
    if not _SEMVER.fullmatch(corpus_version):
        raise CorpusError("corpus.corpus_version must be a SemVer release")
    if raw["metric_version"] != _METRIC_VERSION:
        raise CorpusError(f"unsupported metric version {raw['metric_version']!r}")
    scope = _expect_string_list(raw["scope"], "corpus.scope")
    if not scope:
        raise CorpusError("corpus.scope must name at least one independently evaluated family")
    cases_raw = raw["cases"]
    if not isinstance(cases_raw, list) or not cases_raw:
        raise CorpusError("corpus.cases must be a non-empty array")
    cases: list[BenchmarkCase] = []
    root = corpus_path.parent
    for index, case_value in enumerate(cases_raw):
        context = f"corpus.cases[{index}]"
        case_raw = _expect_object(case_value, context)
        _require_keys(
            case_raw,
            required={
                "id",
                "tags",
                "fixture",
                "sha256",
                "author",
                "license",
                "source",
                "expected_outcome",
                "facts",
            },
            optional=set(),
            context=context,
        )
        case_id = _expect_string(case_raw["id"], f"{context}.id")
        tags = _expect_string_list(case_raw["tags"], f"{context}.tags")
        if not tags:
            raise CorpusError(f"{context}.tags must classify the case")
        fixture_name = _expect_string(case_raw["fixture"], f"{context}.fixture")
        fixture = (root / fixture_name).resolve()
        if not fixture.is_relative_to(root) or not fixture.is_file():
            raise CorpusError(f"{context}.fixture must resolve to a corpus-local file")
        expected_hash = _expect_string(case_raw["sha256"], f"{context}.sha256")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            raise CorpusError(f"{context}.sha256 must be lowercase SHA-256")
        actual_hash = sha256(fixture.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            raise CorpusError(
                f"{context}.fixture hash mismatch: expected {expected_hash}, got {actual_hash}"
            )
        outcome = case_raw["expected_outcome"]
        if outcome not in {"supported", "unknown", "unsupported"}:
            raise CorpusError(f"{context}.expected_outcome is invalid")
        facts_raw = case_raw["facts"]
        if not isinstance(facts_raw, list):
            raise CorpusError(f"{context}.facts must be an array")
        facts = tuple(
            _fact(fact, scope=scope, context=f"{context}.facts[{fact_index}]")
            for fact_index, fact in enumerate(facts_raw)
        )
        if outcome != "supported" and facts:
            raise CorpusError(
                f"{context} cannot expect facts and a non-supported analysis outcome"
            )
        for first_index, first in enumerate(facts):
            for second_index, second in enumerate(facts[first_index + 1 :], first_index + 1):
                if not _facts_are_distinguishable(first, second):
                    raise CorpusError(
                        f"{context}.facts[{first_index}] and facts[{second_index}] have "
                        "overlapping physical identity tolerances"
                    )
        cases.append(
            BenchmarkCase(
                case_id=case_id,
                classification="+".join(tags),
                expected=facts,
                expected_outcome=outcome,
                provenance={
                    "fixture": str(fixture),
                    "sha256": expected_hash,
                    "author": _expect_string(case_raw["author"], f"{context}.author"),
                    "license": _expect_string(case_raw["license"], f"{context}.license"),
                    "source": _expect_string(case_raw["source"], f"{context}.source"),
                },
            )
        )
    identifiers = [case.case_id for case in cases]
    if len(set(identifiers)) != len(identifiers):
        raise CorpusError("corpus case ids must be unique")
    return BenchmarkCorpus(corpus_version, _METRIC_VERSION, scope, tuple(cases))


def _generated_sheet_model(part, model):
    """Execute generated Sheet code through its public declarations and return its IR."""
    from draftwright.sheet_emit import emit_sheet_script

    source = emit_sheet_script(model, "part", "evaluation", title="EVALUATION", number="EVAL")
    prefix = source.split("drawing = sheet.build()", 1)[0]
    namespace: dict[str, object] = {"part": part}
    exec(compile(prefix, "<draftwright-evaluation>", "exec"), namespace)  # noqa: S102
    # The namespace comes from executed script source, so its values are typed as object.
    return getattr(namespace["sheet"], "model")()  # noqa: B009


def _generated_sheet_drawing(part, model):
    """Execute generated declarations through ``Sheet.build`` without exporting files."""
    from draftwright.sheet_emit import emit_sheet_script

    source = emit_sheet_script(model, "part", "evaluation", title="EVALUATION", number="EVAL")
    marker = "drawing = sheet.build()"
    declarations, separator, _exports = source.partition(marker)
    if not separator:
        raise ValueError("generated Sheet script has no drawing build boundary")
    namespace: dict[str, object] = {"part": part}
    exec(  # noqa: S102 — executing the generated public program is the boundary under test
        compile(f"{declarations}{marker}\n", "<draftwright-evaluation>", "exec"),
        namespace,
    )
    return namespace["drawing"]


def _turned_step_identity(profile, step) -> tuple:
    from draftwright.linting.turned_step_coverage import turned_step_source_geometry

    axis, line, span = turned_step_source_geometry(profile, step)
    return axis, line, round((float(span[0]) + float(span[1])) / 2.0, 6)


def _turned_step_parameters(profile, step) -> dict[str, Value]:
    from draftwright.linting.turned_step_coverage import (
        turned_step_source_geometry,
        turned_step_source_key,
    )

    try:
        _axis, _line, span = turned_step_source_geometry(profile, step)
        length: Value = round(float(span[1]) - float(span[0]), 6)
    except (AttributeError, TypeError, ValueError):
        length = "<invalid>"
    try:
        diameter: Value = turned_step_source_key(profile, step)[3]
    except (AttributeError, TypeError, ValueError):
        diameter = "<invalid>"
    return {
        "length": length,
        "diameter": diameter,
    }


def _turned_step_observed_fact(profile, step, boundary_outcomes, index) -> ObservedFact:
    try:
        identity = _turned_step_identity(profile, step)
    except (AttributeError, TypeError, ValueError):
        identity = ("<invalid>", "<invalid>", "<invalid>")
    return ObservedFact(
        family="turned-steps",
        identity={"axis": identity[0], "axis_line": identity[1], "station": identity[2]},
        parameters=_turned_step_parameters(profile, step),
        downstream={
            boundary: boundary_outcomes[boundary][index] for boundary in _DOWNSTREAM_BOUNDARIES
        },
    )


def _generated_turned_step_outcomes(part, model, sources, recognition) -> list[Outcome]:
    """Run emitted public code through its built Drawing and verify its final evidence."""

    generated = _generated_sheet_drawing(part, model)
    return _turned_step_drawing_outcomes(
        sources,
        generated,
        recognition_override=recognition,
        allow_declared_profile_omission=True,
    )


def _declared_turned_step_model(part, sources):
    """Declare provider bands through the existing public ``Sheet.step`` word."""
    from draftwright.sheet import Sheet

    sheet = Sheet(part)
    sheet.authored_dimensions()
    tokens: dict[int, str] = {}
    for profile, step in sources:
        token = tokens.setdefault(id(profile), f"corpus_profile_{len(tokens)}")
        axis = str(step.axis)
        axis_index = "xyz".index(axis)
        base = list(profile.profile.axis_origin)
        at = list(base)
        at[axis_index] = (float(step.lo) + float(step.hi)) / 2.0
        sheet.step(
            diameter=step.diameter,
            length=step.length,
            at=tuple(at),
            axis=axis,
            profile_group=token,
        )
    return sheet.model()


def _boundary_observer(
    count: int,
    *,
    counted_as: str,
    scored_as: str,
    eligible: Sequence[bool] | None = None,
) -> Callable[[str, Callable[[], list[Outcome]]], list[Outcome]]:
    """Score each downstream boundary once per physical source, retaining unknowns."""
    unknown: list[Outcome] = ["unknown"] * count

    def observed_boundary(name: str, observe: Callable[[], list[Outcome]]) -> list[Outcome]:
        try:
            result = observe()
            if len(result) != count:
                raise ValueError(f"observed {len(result)} outcomes for {count} {counted_as}")
            if eligible is not None:
                return [
                    outcome if eligible[index] else "unknown"
                    for index, outcome in enumerate(result)
                ]
            return result
        except Exception as exc:  # noqa: BLE001 — score a broken boundary, keep corpus
            _log.warning(
                "evaluation: %s observation failed (%s); scoring %s as unknown",
                name,
                exc,
                scored_as,
            )
            return list(unknown)

    return observed_boundary


def _bore_observers() -> Mapping[str, _PreparedObserver]:
    def observe_holes(
        part: object, *, build: _BuildAttempt | None = None
    ) -> Sequence[ObservedFact]:
        # Score the build-owned aggregate so correspondence and rendered features share
        # one recognition run. Only the failed-build fallback below recognises standalone.
        try:
            drawing = _drawing_for_observation(part, build=build)
        except Exception as exc:  # noqa: BLE001 — a non-answer, not an aborted corpus run
            # The SCORE is the same `unknown` a correspondence gap produces — the oracle has
            # three outcomes and no fourth — but the two must not be indistinguishable to a
            # reader. A benchmark whose whole point is that a self-reported number cannot
            # validate itself should not quietly equate "the compiler has no correspondence"
            # with "the engine crashed", so the crash is announced.
            _log.warning("evaluation: drawing build failed (%s); scoring holes as unknown", exc)
            drawing = None
        if drawing is None:
            # The recognition read is guarded too: a fixture that neither builds NOR
            # recognises must still yield a scored non-answer rather than a traceback out
            # of the middle of a corpus run.
            try:
                from quiddity import build_raw_recognition_result

                holes = tuple(build_raw_recognition_result(part).holes)  # type: ignore[arg-type]
            except Exception:  # noqa: BLE001 — an unanalysable fixture observes nothing
                return ()
            boundary_outcomes: dict[str, list[Outcome]] = {
                boundary: ["unknown"] * len(holes) for boundary in _DOWNSTREAM_BOUNDARIES
            }
        else:
            try:
                recognition = drawing.recognition()
                if recognition is None:
                    raise ValueError("detected build has no build-owned recognition result")
                holes = tuple(recognition.holes)
            except Exception as exc:  # noqa: BLE001 — no safe observed numerator remains
                _log.warning("evaluation: recognition access failed (%s); observing no holes", exc)
                return ()
            observed_boundary = _boundary_observer(
                len(holes), counted_as="recognised holes", scored_as="holes"
            )

            boundary_outcomes = {
                "ir_adapter": observed_boundary(
                    "ir_adapter",
                    lambda: _hole_model_outcomes(
                        holes,
                        recognition,
                        drawing.model().features,
                        ownership=drawing.recognition_ownership(),
                    ),
                ),
                "dsl_declaration": observed_boundary(
                    "dsl_declaration",
                    lambda: _hole_model_outcomes(
                        holes,
                        recognition,
                        _declared_hole_model(part, holes).features,
                    ),
                ),
                "generated_code": observed_boundary(
                    "generated_code",
                    lambda: _hole_model_outcomes(
                        holes,
                        recognition,
                        _generated_sheet_model(part, drawing.model()).features,
                    ),
                ),
                "drawing_consumer": observed_boundary(
                    "drawing_consumer", lambda: _drawing_consumer_outcomes(holes, drawing)
                ),
            }
        return tuple(
            ObservedFact(
                family="holes",
                identity={"axis": hole.axis, "location": hole.location},
                parameters={
                    "bottom": hole.bottom,
                    "depth": hole.depth,
                    "diameter": hole.diameter,
                },
                downstream={
                    boundary: boundary_outcomes[boundary][index]
                    for boundary in _DOWNSTREAM_BOUNDARIES
                },
            )
            for index, hole in enumerate(holes)
        )

    def observe_countersinks(
        part: object, *, build: _BuildAttempt | None = None
    ) -> Sequence[ObservedFact]:
        """Observe physical seats without making them a second bore denominator."""

        try:
            drawing = _drawing_for_observation(part, build=build)
        except Exception as exc:  # noqa: BLE001 — a non-answer, not an aborted corpus run
            _log.warning(
                "evaluation: drawing build failed (%s); scoring countersinks as unknown", exc
            )
            return ()
        try:
            recognition = drawing.recognition()
            if recognition is None:
                raise ValueError("detected build has no build-owned recognition result")
            countersinks = tuple(recognition.countersinks)
            for countersink in countersinks:
                vectors = (countersink.axis, countersink.location)
                scalars = (
                    countersink.major_diameter,
                    countersink.drill_diameter,
                    countersink.included_angle,
                    countersink.depth,
                )
                if not all(
                    isinstance(vector, tuple)
                    and len(vector) == 3
                    and all(
                        isinstance(component, (int, float))
                        and not isinstance(component, bool)
                        and isfinite(component)
                        for component in vector
                    )
                    for vector in vectors
                ) or not all(
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and isfinite(value)
                    for value in scalars
                ):
                    raise ValueError("countersink record fields must be finite numeric values")
        except Exception as exc:  # noqa: BLE001 — no safe observed numerator remains
            _log.warning(
                "evaluation: recognition access failed (%s); observing no countersinks", exc
            )
            return ()
        observed_boundary = _boundary_observer(
            len(countersinks), counted_as="countersinks", scored_as="countersinks"
        )

        boundary_outcomes = {
            "ir_adapter": observed_boundary(
                "ir_adapter",
                lambda: _countersink_model_outcomes(
                    countersinks, recognition, drawing.model().features
                ),
            ),
            "dsl_declaration": observed_boundary(
                "dsl_declaration",
                lambda: _countersink_model_outcomes(
                    countersinks,
                    recognition,
                    _declared_hole_model(part, recognition.holes).features,
                ),
            ),
            "generated_code": observed_boundary(
                "generated_code",
                lambda: _countersink_model_outcomes(
                    countersinks,
                    recognition,
                    _generated_sheet_model(part, drawing.model()).features,
                ),
            ),
            "drawing_consumer": observed_boundary(
                "drawing_consumer",
                lambda: _countersink_drawing_outcomes(countersinks, recognition, drawing),
            ),
        }
        return tuple(
            ObservedFact(
                family="countersinks",
                identity={"axis": countersink.axis, "location": countersink.location},
                parameters={
                    "major_diameter": countersink.major_diameter,
                    "drill_diameter": countersink.drill_diameter,
                    "included_angle": countersink.included_angle,
                    "depth": countersink.depth,
                },
                downstream={
                    boundary: boundary_outcomes[boundary][index]
                    for boundary in _DOWNSTREAM_BOUNDARIES
                },
            )
            for index, countersink in enumerate(countersinks)
        )

    return {
        "holes": observe_holes,
        "countersinks": observe_countersinks,
    }


def _bore_variant_observers() -> Mapping[str, _PreparedObserver]:
    def observe_double_d_bores(
        part: object, *, build: _BuildAttempt | None = None
    ) -> Sequence[ObservedFact]:
        """Observe one complete through-profile occurrence per aggregate record."""

        try:
            drawing = _drawing_for_observation(part, build=build)
        except Exception as exc:  # noqa: BLE001 — a non-answer, not an aborted corpus run
            _log.warning(
                "evaluation: drawing build failed (%s); scoring Double-D bores as unknown", exc
            )
            return ()
        try:
            recognition = drawing.recognition()
            if recognition is None:
                raise ValueError("detected build has no build-owned recognition result")
            bores = tuple(recognition.double_d_bores)
            # Validate the complete public schema before publishing any numerator.  One malformed
            # record invalidates the observed inventory; the independent corpus then reports misses.
            for bore in bores:
                _double_d_record_key(bore)
            exclusive_owners = _double_d_exclusive_owners(bores, recognition.holes)
        except Exception as exc:  # noqa: BLE001 — no safe observed numerator remains
            _log.warning(
                "evaluation: recognition access failed (%s); observing no Double-D bores", exc
            )
            return ()
        observed_boundary = _boundary_observer(
            len(bores),
            counted_as="Double-D bores",
            scored_as="Double-D bores",
            eligible=exclusive_owners,
        )

        boundary_outcomes = {
            "ir_adapter": observed_boundary(
                "ir_adapter",
                lambda: _double_d_model_outcomes(bores, drawing.model().features),
            ),
            "dsl_declaration": observed_boundary(
                "dsl_declaration",
                lambda: _double_d_model_outcomes(
                    bores,
                    _declared_double_d_model(part, bores).features,
                ),
            ),
            "generated_code": observed_boundary(
                "generated_code",
                lambda: _double_d_model_outcomes(
                    bores,
                    _generated_sheet_model(part, drawing.model()).features,
                ),
            ),
            "drawing_consumer": observed_boundary(
                "drawing_consumer", lambda: _double_d_drawing_outcomes(bores, drawing)
            ),
        }
        return tuple(
            ObservedFact(
                family="double-d-bores",
                identity={"axis": bore.axis, "location": bore.location},
                parameters={
                    "major_diameter": bore.major_diameter,
                    "across_flats": bore.across_flats,
                    "depth": bore.depth,
                    "through": bore.through,
                    "flat_direction": bore.flat_direction,
                },
                downstream={
                    boundary: boundary_outcomes[boundary][index]
                    for boundary in _DOWNSTREAM_BOUNDARIES
                },
            )
            for index, bore in enumerate(bores)
        )

    def observe_hole_patterns(
        part: object, *, build: _BuildAttempt | None = None
    ) -> Sequence[ObservedFact]:

        try:
            drawing = _drawing_for_observation(part, build=build)
        except Exception as exc:  # noqa: BLE001 — a non-answer, not an aborted corpus run
            _log.warning(
                "evaluation: drawing build failed (%s); scoring hole patterns as unknown", exc
            )
            return ()
        try:
            recognition = drawing.recognition()
            if recognition is None:
                raise ValueError("detected build has no build-owned recognition result")
            patterns = tuple(recognition.hole_patterns)
        except Exception as exc:  # noqa: BLE001 — no safe observed numerator remains
            _log.warning(
                "evaluation: recognition access failed (%s); observing no hole patterns", exc
            )
            return ()
        observed_boundary = _boundary_observer(
            len(patterns), counted_as="recognised patterns", scored_as="patterns"
        )

        boundary_outcomes = {
            "ir_adapter": observed_boundary(
                "ir_adapter",
                lambda: _pattern_model_outcomes(patterns, recognition, drawing.model().features),
            ),
            "dsl_declaration": observed_boundary(
                "dsl_declaration",
                lambda: _pattern_model_outcomes(
                    patterns,
                    recognition,
                    _declared_pattern_model(part, patterns).features,
                ),
            ),
            "generated_code": observed_boundary(
                "generated_code",
                lambda: _pattern_model_outcomes(
                    patterns,
                    recognition,
                    _generated_sheet_model(part, drawing.model()).features,
                ),
            ),
            "drawing_consumer": observed_boundary(
                "drawing_consumer", lambda: _pattern_drawing_outcomes(patterns, drawing)
            ),
        }

        def parameters(pattern) -> dict[str, Value]:
            kind = _pattern_kind(pattern)
            values: dict[str, Value] = {"count": len(pattern.holes)}
            if kind == "bolt_circle":
                values.update(center=pattern.center, diameter=pattern.diameter)
            elif kind == "linear":
                values.update(pitch=pattern.pitch, direction=pattern.direction)
            else:
                values.update(
                    rows=pattern.rows,
                    cols=pattern.cols,
                    row_pitch=pattern.row_pitch,
                    col_pitch=pattern.col_pitch,
                    angle=pattern.angle,
                    center=pattern.center,
                )
            return values

        return tuple(
            ObservedFact(
                family="hole-patterns",
                identity={
                    "kind": _pattern_kind(pattern),
                    "members": tuple(
                        component for point in _pattern_members(pattern) for component in point
                    ),
                },
                parameters=parameters(pattern),
                downstream={
                    boundary: boundary_outcomes[boundary][index]
                    for boundary in _DOWNSTREAM_BOUNDARIES
                },
            )
            for index, pattern in enumerate(patterns)
        )

    return {
        "double-d-bores": observe_double_d_bores,
        "hole-patterns": observe_hole_patterns,
    }


def _stock_observers() -> Mapping[str, _PreparedObserver]:
    def observe_flats(
        part: object, *, build: _BuildAttempt | None = None
    ) -> Sequence[ObservedFact]:

        try:
            drawing = _drawing_for_observation(part, build=build)
        except Exception as exc:  # noqa: BLE001 — a non-answer, not an aborted corpus run
            _log.warning("evaluation: drawing build failed (%s); scoring flats as unknown", exc)
            return ()
        try:
            recognition = drawing.recognition()
            if recognition is None:
                raise ValueError("detected build has no build-owned recognition result")
            flats = tuple(recognition.flats)
        except Exception as exc:  # noqa: BLE001 — no safe observed numerator remains
            _log.warning("evaluation: recognition access failed (%s); observing no flats", exc)
            return ()
        groups = _flat_groups(flats)
        observed_boundary = _boundary_observer(
            len(groups), counted_as="physical flats", scored_as="flats"
        )

        boundary_outcomes = {
            "ir_adapter": observed_boundary(
                "ir_adapter",
                lambda: _flat_model_outcomes(flats, recognition, drawing.model().features),
            ),
            "dsl_declaration": observed_boundary(
                "dsl_declaration",
                lambda: _flat_model_outcomes(
                    flats,
                    recognition,
                    _declared_flat_model(part, flats).features,
                ),
            ),
            "generated_code": observed_boundary(
                "generated_code",
                lambda: _flat_model_outcomes(
                    flats,
                    recognition,
                    _generated_sheet_model(part, drawing.model()).features,
                ),
            ),
            "drawing_consumer": observed_boundary(
                "drawing_consumer", lambda: _flat_drawing_outcomes(flats, drawing)
            ),
        }

        return tuple(
            ObservedFact(
                family="flats",
                identity={
                    "axis": identity[0],
                    "axis_direction": identity[1],
                    "axis_line": identity[2],
                    "stock_span": identity[3],
                },
                parameters=_flat_parameters(members),
                downstream={
                    boundary: boundary_outcomes[boundary][index]
                    for boundary in _DOWNSTREAM_BOUNDARIES
                },
            )
            for index, (identity, members) in enumerate(groups)
        )

    def observe_pads(
        part: object, *, build: _BuildAttempt | None = None
    ) -> Sequence[ObservedFact]:

        try:
            drawing = _drawing_for_observation(part, build=build)
        except Exception as exc:  # noqa: BLE001 — a non-answer, not an aborted corpus run
            _log.warning(
                "evaluation: drawing build failed (%s); scoring rectangular pads as unknown",
                exc,
            )
            raise ObservationError("rectangular-pads", f"drawing build failed: {exc}") from exc
        try:
            recognition = drawing.recognition()
            if recognition is None:
                raise ValueError("detected build has no build-owned recognition result")
            pads = tuple(recognition.pads)
        except Exception as exc:  # noqa: BLE001 — no safe observed numerator remains
            _log.warning(
                "evaluation: recognition access failed (%s); observing no rectangular pads",
                exc,
            )
            raise ObservationError(
                "rectangular-pads", f"recognition access failed: {exc}"
            ) from exc
        observed_boundary = _boundary_observer(
            len(pads), counted_as="physical pads", scored_as="rectangular pads"
        )

        boundary_outcomes = {
            "ir_adapter": observed_boundary(
                "ir_adapter",
                lambda: _pad_model_outcomes(pads, recognition, drawing.model().features),
            ),
            "dsl_declaration": observed_boundary(
                "dsl_declaration",
                lambda: _pad_model_outcomes(
                    pads,
                    recognition,
                    _declared_pad_model(part, pads).features,
                ),
            ),
            "generated_code": observed_boundary(
                "generated_code",
                lambda: _pad_model_outcomes(
                    pads,
                    recognition,
                    _generated_sheet_model(part, drawing.model()).features,
                ),
            ),
            "drawing_consumer": observed_boundary(
                "drawing_consumer", lambda: _pad_drawing_outcomes(pads, drawing)
            ),
        }

        return tuple(
            ObservedFact(
                family="rectangular-pads",
                identity={
                    "axis": identity[0],
                    "direction": identity[1],
                    "attachment": identity[2],
                },
                parameters=_pad_parameters(pad),
                downstream={
                    boundary: boundary_outcomes[boundary][index]
                    for boundary in _DOWNSTREAM_BOUNDARIES
                },
            )
            for index, pad in enumerate(pads)
            for identity in (_pad_identity(pad),)
        )

    def observe_plates(
        part: object, *, build: _BuildAttempt | None = None
    ) -> Sequence[ObservedFact]:

        try:
            drawing = _drawing_for_observation(part, build=build)
        except Exception as exc:  # noqa: BLE001 — a non-answer, not an aborted corpus run
            _log.warning(
                "evaluation: drawing build failed (%s); scoring plates as unknown",
                exc,
            )
            raise ObservationError("plates", f"drawing build failed: {exc}") from exc
        try:
            recognition = drawing.recognition()
            if recognition is None:
                raise ValueError("detected build has no build-owned recognition result")
            plates = tuple(recognition.plates)
        except Exception as exc:  # noqa: BLE001 — no safe observed numerator remains
            _log.warning(
                "evaluation: recognition access failed (%s); observing no plates",
                exc,
            )
            raise ObservationError("plates", f"recognition access failed: {exc}") from exc
        observed_boundary = _boundary_observer(
            len(plates), counted_as="physical plates", scored_as="plates"
        )

        boundary_outcomes = {
            "ir_adapter": observed_boundary(
                "ir_adapter",
                lambda: _plate_model_outcomes(plates, recognition, drawing.model().features),
            ),
            "dsl_declaration": observed_boundary(
                "dsl_declaration",
                lambda: _plate_model_outcomes(
                    plates,
                    recognition,
                    _declared_plate_model(part, plates).features,
                ),
            ),
            "generated_code": observed_boundary(
                "generated_code",
                lambda: _plate_model_outcomes(
                    plates,
                    recognition,
                    _generated_sheet_model(part, drawing.model()).features,
                ),
            ),
            "drawing_consumer": observed_boundary(
                "drawing_consumer", lambda: _plate_drawing_outcomes(plates, drawing)
            ),
        }

        return tuple(
            ObservedFact(
                family="plates",
                identity={
                    "axis": identity[0],
                    "at": identity[1],
                    "u": identity[2],
                    "v": identity[3],
                },
                parameters=_plate_parameters(plate),
                downstream={
                    boundary: boundary_outcomes[boundary][index]
                    for boundary in _DOWNSTREAM_BOUNDARIES
                },
            )
            for index, plate in enumerate(plates)
            for identity in (_plate_identity(plate),)
        )

    return {
        "flats": observe_flats,
        "rectangular-pads": observe_pads,
        "plates": observe_plates,
    }


def _polygonal_observers() -> Mapping[str, _PreparedObserver]:
    def observe_polygonal_bosses(
        part: object, *, build: _BuildAttempt | None = None
    ) -> Sequence[ObservedFact]:

        try:
            drawing = _drawing_for_observation(part, build=build)
        except Exception as exc:  # noqa: BLE001 — a non-answer, not an aborted corpus run
            _log.warning(
                "evaluation: drawing build failed (%s); scoring polygonal bosses as unknown",
                exc,
            )
            raise ObservationError("polygonal-bosses", f"drawing build failed: {exc}") from exc
        try:
            recognition = drawing.recognition()
            if recognition is None:
                raise ValueError("detected build has no build-owned recognition result")
            bosses = tuple(recognition.polygonal_bosses)
        except Exception as exc:  # noqa: BLE001 — no safe observed numerator remains
            _log.warning(
                "evaluation: recognition access failed (%s); observing no polygonal bosses",
                exc,
            )
            raise ObservationError(
                "polygonal-bosses", f"recognition access failed: {exc}"
            ) from exc
        observed_boundary = _boundary_observer(
            len(bosses), counted_as="polygonal bosses", scored_as="polygonal bosses"
        )

        boundary_outcomes = {
            "ir_adapter": observed_boundary(
                "ir_adapter",
                lambda: _polygonal_boss_model_outcomes(
                    bosses, recognition, drawing.model().features
                ),
            ),
            "dsl_declaration": observed_boundary(
                "dsl_declaration",
                lambda: _polygonal_boss_model_outcomes(
                    bosses,
                    recognition,
                    _declared_polygonal_boss_model(part, bosses).features,
                ),
            ),
            "generated_code": observed_boundary(
                "generated_code",
                lambda: _polygonal_boss_model_outcomes(
                    bosses,
                    recognition,
                    _generated_sheet_model(part, drawing.model()).features,
                ),
            ),
            "drawing_consumer": observed_boundary(
                "drawing_consumer",
                lambda: _polygonal_boss_drawing_outcomes(bosses, drawing),
            ),
        }

        return tuple(
            ObservedFact(
                family="polygonal-bosses",
                identity={"axis": identity[0], "center": identity[1]},
                parameters=_polygonal_boss_parameters(boss),
                downstream={
                    boundary: boundary_outcomes[boundary][index]
                    for boundary in _DOWNSTREAM_BOUNDARIES
                },
            )
            for index, boss in enumerate(bosses)
            for identity in (_polygonal_boss_identity(boss),)
        )

    def observe_polygonal_stock(
        part: object, *, build: _BuildAttempt | None = None
    ) -> Sequence[ObservedFact]:

        try:
            drawing = _drawing_for_observation(part, build=build, repair=False)
        except Exception as exc:  # noqa: BLE001 — a non-answer, not an aborted corpus run
            _log.warning(
                "evaluation: drawing build failed (%s); scoring polygonal stock as unknown",
                exc,
            )
            raise ObservationError("polygonal-stock", f"drawing build failed: {exc}") from exc
        try:
            recognition = drawing.recognition()
            if recognition is None:
                raise ValueError("detected build has no build-owned recognition result")
            stocks = tuple(recognition.polygonal_stock)
        except Exception as exc:  # noqa: BLE001 — no safe observed numerator remains
            _log.warning(
                "evaluation: recognition access failed (%s); observing no polygonal stock",
                exc,
            )
            raise ObservationError("polygonal-stock", f"recognition access failed: {exc}") from exc
        observed_boundary = _boundary_observer(
            len(stocks), counted_as="polygonal stocks", scored_as="polygonal stock"
        )

        boundary_outcomes = {
            "ir_adapter": observed_boundary(
                "ir_adapter",
                lambda: _polygonal_stock_model_outcomes(
                    stocks, recognition, drawing.model().features
                ),
            ),
            "dsl_declaration": observed_boundary(
                "dsl_declaration",
                lambda: _polygonal_stock_model_outcomes(
                    stocks,
                    recognition,
                    _declared_polygonal_stock_model(part, stocks).features,
                ),
            ),
            "generated_code": observed_boundary(
                "generated_code",
                lambda: _polygonal_stock_model_outcomes(
                    stocks,
                    recognition,
                    _generated_sheet_model(part, drawing.model()).features,
                ),
            ),
            "drawing_consumer": observed_boundary(
                "drawing_consumer",
                lambda: _polygonal_stock_drawing_outcomes(stocks, drawing),
            ),
        }

        return tuple(
            ObservedFact(
                family="polygonal-stock",
                identity={"axis": identity[0], "center": identity[1]},
                parameters=_polygonal_stock_parameters(stock),
                downstream={
                    boundary: boundary_outcomes[boundary][index]
                    for boundary in _DOWNSTREAM_BOUNDARIES
                },
            )
            for index, stock in enumerate(stocks)
            for identity in (_polygonal_stock_identity(stock),)
        )

    return {
        "polygonal-bosses": observe_polygonal_bosses,
        "polygonal-stock": observe_polygonal_stock,
    }


def _turned_profile_observers() -> Mapping[str, _PreparedObserver]:
    def observe_grooves(
        part: object, *, build: _BuildAttempt | None = None
    ) -> Sequence[ObservedFact]:

        try:
            drawing = _drawing_for_observation(part, build=build)
        except Exception as exc:  # noqa: BLE001 — a non-answer, not an aborted corpus run
            _log.warning("evaluation: drawing build failed (%s); scoring grooves as unknown", exc)
            raise ObservationError("grooves", f"drawing build failed: {exc}") from exc
        try:
            recognition = drawing.recognition()
            if recognition is None:
                raise ValueError("detected build has no build-owned recognition result")
            grooves = tuple(recognition.grooves)
        except Exception as exc:  # noqa: BLE001 — no safe observed numerator remains
            _log.warning("evaluation: recognition access failed (%s); observing no grooves", exc)
            raise ObservationError("grooves", f"recognition access failed: {exc}") from exc
        observed_boundary = _boundary_observer(
            len(grooves), counted_as="physical grooves", scored_as="grooves"
        )

        boundary_outcomes = {
            "ir_adapter": observed_boundary(
                "ir_adapter",
                lambda: _groove_model_outcomes(grooves, recognition, drawing.model().features),
            ),
            "dsl_declaration": observed_boundary(
                "dsl_declaration",
                lambda: _groove_model_outcomes(
                    grooves,
                    recognition,
                    _declared_groove_model(part, grooves).features,
                ),
            ),
            "generated_code": observed_boundary(
                "generated_code",
                lambda: _groove_model_outcomes(
                    grooves,
                    recognition,
                    _generated_sheet_model(part, drawing.model()).features,
                ),
            ),
            "drawing_consumer": observed_boundary(
                "drawing_consumer", lambda: _groove_drawing_outcomes(grooves, drawing)
            ),
        }

        return tuple(
            ObservedFact(
                family="grooves",
                identity={"axis": identity[0], "location": identity[1]},
                parameters=_groove_parameters(groove),
                downstream={
                    boundary: boundary_outcomes[boundary][index]
                    for boundary in _DOWNSTREAM_BOUNDARIES
                },
            )
            for index, groove in enumerate(grooves)
            for identity in (_groove_identity(groove),)
        )

    def observe_turned_steps(
        part: object, *, build: _BuildAttempt | None = None
    ) -> Sequence[ObservedFact]:
        from draftwright.linting.turned_step_coverage import physical_turned_steps

        try:
            drawing = _drawing_for_observation(part, build=build)
        except Exception as exc:  # noqa: BLE001 — a non-answer, not an aborted corpus run
            _log.warning(
                "evaluation: drawing build failed (%s); scoring turned steps as unknown", exc
            )
            raise ObservationError("turned-steps", f"drawing build failed: {exc}") from exc
        try:
            recognition = drawing.recognition()
            if recognition is None:
                raise ValueError("detected build has no build-owned recognition result")
            sources = physical_turned_steps(recognition)
        except Exception as exc:  # noqa: BLE001 — no safe observed numerator remains
            _log.warning(
                "evaluation: recognition access failed (%s); observing no turned steps", exc
            )
            raise ObservationError("turned-steps", f"recognition access failed: {exc}") from exc
        observed_boundary = _boundary_observer(
            len(sources), counted_as="turned-step bands", scored_as="turned steps"
        )

        boundary_outcomes = {
            "ir_adapter": observed_boundary(
                "ir_adapter",
                lambda: _turned_step_model_outcomes(
                    sources, recognition, drawing.model().features
                ),
            ),
            "dsl_declaration": observed_boundary(
                "dsl_declaration",
                lambda: _turned_step_model_outcomes(
                    sources,
                    recognition,
                    _declared_turned_step_model(part, sources).features,
                    allow_declared_profile_omission=True,
                ),
            ),
            "generated_code": observed_boundary(
                "generated_code",
                lambda: _generated_turned_step_outcomes(
                    part,
                    drawing.model(),
                    sources,
                    recognition,
                ),
            ),
            "drawing_consumer": observed_boundary(
                "drawing_consumer", lambda: _turned_step_drawing_outcomes(sources, drawing)
            ),
        }

        return tuple(
            _turned_step_observed_fact(profile, step, boundary_outcomes, index)
            for index, (profile, step) in enumerate(sources)
        )

    return {
        "grooves": observe_grooves,
        "turned-steps": observe_turned_steps,
    }


def _edge_observer(
    family: str,
    identity_of: Callable[..., tuple],
    parameters_of: Callable[..., Mapping[str, Value]],
    model_outcomes: Callable[..., list[Outcome]],
    declared_model: Callable[..., Any],
    drawing_outcomes: Callable[..., list[Outcome]],
) -> _PreparedObserver:
    """Keep the shared edge-profile observation flow and each family's evidence separate."""

    def observe(part: object, *, build: _BuildAttempt | None = None) -> Sequence[ObservedFact]:
        try:
            drawing = _drawing_for_observation(part, build=build)
        except Exception as exc:  # noqa: BLE001 — a non-answer, not an aborted corpus run
            _log.warning(
                "evaluation: drawing build failed (%s); scoring %s as unknown", exc, family
            )
            raise ObservationError(family, f"drawing build failed: {exc}") from exc
        try:
            recognition = drawing.recognition()
            if recognition is None:
                raise ValueError("detected build has no build-owned recognition result")
            sources = tuple(getattr(recognition, family))
        except Exception as exc:  # noqa: BLE001 — no safe observed numerator remains
            _log.warning(
                "evaluation: recognition access failed (%s); observing no %s", exc, family
            )
            raise ObservationError(family, f"recognition access failed: {exc}") from exc
        observed_boundary = _boundary_observer(
            len(sources), counted_as=f"physical {family}", scored_as=family
        )
        boundary_outcomes = {
            "ir_adapter": observed_boundary(
                "ir_adapter",
                lambda: model_outcomes(sources, recognition, drawing.model().features),
            ),
            "dsl_declaration": observed_boundary(
                "dsl_declaration",
                lambda: model_outcomes(
                    sources, recognition, declared_model(part, sources).features
                ),
            ),
            "generated_code": observed_boundary(
                "generated_code",
                lambda: model_outcomes(
                    sources, recognition, _generated_sheet_model(part, drawing.model()).features
                ),
            ),
            "drawing_consumer": observed_boundary(
                "drawing_consumer", lambda: drawing_outcomes(sources, drawing)
            ),
        }
        return tuple(
            ObservedFact(
                family=family,
                identity={"axis": identity[0], "location": identity[1], "turned": identity[2]},
                parameters=parameters_of(source),
                downstream={
                    boundary: boundary_outcomes[boundary][index]
                    for boundary in _DOWNSTREAM_BOUNDARIES
                },
            )
            for index, source in enumerate(sources)
            for identity in (identity_of(source),)
        )

    return observe


def _edge_observers() -> Mapping[str, _PreparedObserver]:
    return {
        "chamfers": _edge_observer(
            "chamfers",
            _chamfer_identity,
            _chamfer_parameters,
            _chamfer_model_outcomes,
            _declared_chamfer_model,
            _chamfer_drawing_outcomes,
        ),
        "fillets": _edge_observer(
            "fillets",
            _fillet_identity,
            _fillet_parameters,
            _fillet_model_outcomes,
            _declared_fillet_model,
            _fillet_drawing_outcomes,
        ),
    }


def _recess_observers() -> Mapping[str, _PreparedObserver]:
    def observe_pockets(
        part: object, *, build: _BuildAttempt | None = None
    ) -> Sequence[ObservedFact]:

        try:
            drawing = _drawing_for_observation(part, build=build)
        except Exception as exc:  # noqa: BLE001 — a non-answer, not an aborted corpus run
            _log.warning("evaluation: drawing build failed (%s); scoring pockets as unknown", exc)
            return ()
        try:
            recognition = drawing.recognition()
            if recognition is None:
                raise ValueError("detected build has no build-owned recognition result")
            pockets = _lone_pockets(recognition)
        except Exception as exc:  # noqa: BLE001 — no safe observed numerator remains
            _log.warning("evaluation: recognition access failed (%s); observing no pockets", exc)
            return ()
        observed_boundary = _boundary_observer(
            len(pockets), counted_as="physical pockets", scored_as="pockets"
        )

        boundary_outcomes = {
            "ir_adapter": observed_boundary(
                "ir_adapter",
                lambda: _pocket_model_outcomes(pockets, recognition, drawing.model().features),
            ),
            "dsl_declaration": observed_boundary(
                "dsl_declaration",
                lambda: _pocket_model_outcomes(
                    pockets,
                    recognition,
                    _declared_pocket_model(part, pockets).features,
                ),
            ),
            "generated_code": observed_boundary(
                "generated_code",
                lambda: _pocket_model_outcomes(
                    pockets,
                    recognition,
                    _generated_sheet_model(part, drawing.model()).features,
                ),
            ),
            "drawing_consumer": observed_boundary(
                "drawing_consumer", lambda: _pocket_drawing_outcomes(pockets, drawing)
            ),
        }

        return tuple(
            ObservedFact(
                family="pockets",
                identity={
                    "width_axis": identity[0],
                    "long_axis": identity[1],
                    "depth_axis": identity[2],
                    "open_sign": identity[3],
                    "location": identity[4],
                },
                parameters=_pocket_parameters(pocket),
                downstream={
                    boundary: boundary_outcomes[boundary][index]
                    for boundary in _DOWNSTREAM_BOUNDARIES
                },
            )
            for index, pocket in enumerate(pockets)
            for identity in (_pocket_identity(pocket),)
        )

    def observe_pocket_patterns(
        part: object, *, build: _BuildAttempt | None = None
    ) -> Sequence[ObservedFact]:
        from draftwright.linting.pocket_pattern_coverage import (
            pocket_pattern_kind,
            pocket_pattern_members,
            pocket_pattern_source_at,
        )

        try:
            drawing = _drawing_for_observation(part, build=build)
        except Exception as exc:  # noqa: BLE001 — a non-answer, not an aborted corpus run
            _log.warning(
                "evaluation: drawing build failed (%s); scoring pocket patterns as unknown",
                exc,
            )
            return ()
        try:
            recognition = drawing.recognition()
            if recognition is None:
                raise ValueError("detected build has no build-owned recognition result")
            patterns = _supported_pocket_patterns(recognition)
        except Exception as exc:  # noqa: BLE001 — no safe observed numerator remains
            _log.warning(
                "evaluation: recognition access failed (%s); observing no pocket patterns",
                exc,
            )
            return ()
        observed_boundary = _boundary_observer(
            len(patterns), counted_as="pocket patterns", scored_as="pocket patterns"
        )

        boundary_outcomes = {
            "ir_adapter": observed_boundary(
                "ir_adapter",
                lambda: _pocket_pattern_model_outcomes(
                    patterns, recognition, drawing.model().features
                ),
            ),
            "dsl_declaration": observed_boundary(
                "dsl_declaration",
                lambda: _pocket_pattern_model_outcomes(
                    patterns,
                    recognition,
                    _declared_pocket_pattern_model(part, patterns, recognition).features,
                ),
            ),
            "generated_code": observed_boundary(
                "generated_code",
                lambda: _pocket_pattern_model_outcomes(
                    patterns,
                    recognition,
                    _generated_sheet_model(part, drawing.model()).features,
                ),
            ),
            "drawing_consumer": observed_boundary(
                "drawing_consumer",
                lambda: _pocket_pattern_drawing_outcomes(patterns, drawing),
            ),
        }

        from draftwright.section_recess_contract import section_recess_pattern_members

        def member_geometry(pattern):
            member = section_recess_pattern_members(pattern, recognition.section_recesses)[0]
            return _pocket_geometry(member)

        def parameters(pattern) -> dict[str, Value]:
            member = member_geometry(pattern)
            values: dict[str, Value] = {
                "count": len(pattern.members),
                "width": round(float(member["width"]), 2),
                "length": round(float(member["length"]), 2),
                "depth": round(float(member["depth"]), 2),
                "edge_anchored": bool(member["edge_anchored"]),
                "center": pocket_pattern_source_at(
                    pattern, inventory=recognition.section_recesses
                ),
            }
            if pocket_pattern_kind(pattern) == "linear":
                values.update(
                    pitch=round(float(pattern.pitch), 2),
                    direction=tuple(round(float(value), 2) for value in pattern.direction),
                )
            else:
                values.update(
                    rows=pattern.rows,
                    cols=pattern.cols,
                    row_pitch=round(float(pattern.row_pitch), 2),
                    col_pitch=round(float(pattern.col_pitch), 2),
                    angle=round(
                        _pocket_pattern_angle(
                            pattern,
                            section_recess_pattern_members(pattern, recognition.section_recesses)[
                                0
                            ],
                        ),
                        2,
                    ),
                )
            return values

        return tuple(
            ObservedFact(
                family="pocket-patterns",
                identity={
                    "kind": pocket_pattern_kind(pattern),
                    "width_axis": member_geometry(pattern)["width_axis"],
                    "long_axis": member_geometry(pattern)["long_axis"],
                    "depth_axis": member_geometry(pattern)["axis"],
                    "open_sign": member_geometry(pattern)["open_sign"],
                    "members": tuple(
                        component
                        for point in pocket_pattern_members(
                            pattern, inventory=recognition.section_recesses
                        )
                        for component in point
                    ),
                },
                parameters=parameters(pattern),
                downstream={
                    boundary: boundary_outcomes[boundary][index]
                    for boundary in _DOWNSTREAM_BOUNDARIES
                },
            )
            for index, pattern in enumerate(patterns)
        )

    return {
        "pockets": observe_pockets,
        "pocket-patterns": observe_pocket_patterns,
    }


def _default_observers() -> Mapping[str, _PreparedObserver]:
    """Register each physical family in the established corpus order."""
    bore = _bore_observers()
    variant = _bore_variant_observers()
    stock = _stock_observers()
    polygonal = _polygonal_observers()
    turned = _turned_profile_observers()
    edge = _edge_observers()
    recess = _recess_observers()
    return {
        "chamfers": edge["chamfers"],
        "countersinks": bore["countersinks"],
        "double-d-bores": variant["double-d-bores"],
        "fillets": edge["fillets"],
        "flats": stock["flats"],
        "grooves": turned["grooves"],
        "holes": bore["holes"],
        "hole-patterns": variant["hole-patterns"],
        "pocket-patterns": recess["pocket-patterns"],
        "pockets": recess["pockets"],
        "plates": stock["plates"],
        "polygonal-bosses": polygonal["polygonal-bosses"],
        "polygonal-stock": polygonal["polygonal-stock"],
        "rectangular-pads": stock["rectangular-pads"],
        "turned-steps": turned["turned-steps"],
    }


def evaluate_step_corpus(
    corpus: BenchmarkCorpus,
    *,
    observers: Mapping[str, Observer] | None = None,
    outcomes: Mapping[str, Outcome] | None = None,
) -> CorpusEvaluation:
    """Import every pinned STEP fixture and evaluate normalized family observations."""
    from build123d import import_step

    defaults = _default_observers() if observers is None else {}
    if observers is None:
        registered: Mapping[str, Observer] = {
            family: defaults[family] for family in corpus.scope if family in defaults
        }
    else:
        registered = observers
    missing = set(corpus.scope) - set(registered)
    extra = set(registered) - set(corpus.scope)
    if missing or extra:
        raise CorpusError(
            f"observer registry must exactly cover corpus scope; missing={sorted(missing)}, "
            f"extra={sorted(extra)}"
        )
    resolved_outcomes = outcomes or {}
    unknown_cases = set(resolved_outcomes) - {case.case_id for case in corpus.cases}
    if unknown_cases:
        raise CorpusError(f"outcomes name unknown corpus cases {sorted(unknown_cases)}")
    evaluations = []
    for case in corpus.cases:
        part = import_step(case.provenance["fixture"])
        failure: ObservationError | None = None
        try:
            if (
                observers is None
                and corpus.scope
                and ("polygonal-stock" not in corpus.scope or len(corpus.scope) == 1)
            ):
                # A case shares its primary detected build. Polygonal stock alone keeps its
                # established repair=False policy. A mixed stock scope retains separate builds
                # until one repair policy can be shown equivalent for all its families.
                build = _attempt_build(part, repair="polygonal-stock" not in corpus.scope)
                observations = tuple(
                    observation
                    for family in corpus.scope
                    for observation in defaults[family](part, build=build)
                )
            else:
                observations = tuple(
                    observation
                    for family in corpus.scope
                    for observation in registered[family](part)
                )
        except ObservationError as exc:
            failure = exc
            observations = ()
        evaluation = evaluate_case(
            case,
            observations=observations,
            outcome=(
                "unknown"
                if failure is not None
                else resolved_outcomes.get(case.case_id, "supported")
            ),
        )
        if failure is not None:
            evaluation = replace(
                evaluation,
                diagnostics=(
                    Diagnostic(
                        "analysis",
                        failure.family,
                        f"observer failed: {failure}",
                    ),
                    *evaluation.diagnostics,
                ),
            )
        evaluations.append(evaluation)
    return evaluate_corpus(evaluations, corpus_version=corpus.corpus_version)


__all__ = [
    "BenchmarkCase",
    "BenchmarkCorpus",
    "CaseEvaluation",
    "CorpusEvaluation",
    "CorpusError",
    "DetectionScore",
    "Diagnostic",
    "ExpectedFact",
    "LayerScore",
    "ObservedFact",
    "ObservationError",
    "ParameterExpectation",
    "evaluate_case",
    "evaluate_corpus",
    "evaluate_step_corpus",
    "load_corpus",
]
