"""Evidence-based STEP analysis evaluation (#1169).

This module scores recogniser observations against an independently authored oracle.  Its
expectations do not inspect ``RecognitionResult`` or the feature census: adapters supply
observations, while the benchmark case supplies the denominator and tolerances.

Every downstream boundary is OBSERVED through its real seam (#1369), never copied from the
capability declaration: the built ``PartModel`` for ``ir_adapter``; an explicit public
``Sheet.hole`` declaration for ``dsl_declaration``; an executed ``emit_sheet_script`` result
for ``generated_code``; and the placed drawing's ADR 0010 measurement provenance for
``drawing_consumer``.  The existing hole-requirement ledger supplies one conservative
recognition-to-IR correspondence implementation for all four observations.  It is a join, not
the benchmark denominator: the independently authored corpus remains the only source of
expected facts.

The hole-pattern slice (#1370) uses the same four boundaries through ``Sheet.pattern`` and the
existing hole-requirement correspondence. Its separate corpus scores one arrangement fact per
aggregate pattern. Member diameter/depth/bottom/location requirements stay solely in the hole
corpus, so the derived N:1 group never becomes a second physical-hole denominator.

The flat slice (#1371) scores one physical A/F requirement per stock line and axial span. Two
opposed faces on one Double-D are member evidence for one requirement; equal parallel stock and
disjoint coaxial stock remain separate facts. Across-flats and the face anchors are parameters,
not benchmark identity, so weakening either lowers fidelity instead of hiding as a detection
mismatch.

Known limit of the drawing observation: it reads the ADR 0010 provenance seam, which
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

**Every draftwright import in this module is deliberately inside a function body** — there are
no module-level ones at all — which is the #313 lazy-load pattern rather than an accident.
(`b123d_recognisers` counts: importing it puts build123d in `sys.modules`, so it carries the
same cost.) It is load-bearing: importing this module costs ~0.01 s, and hoisting ANY engine import makes it
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
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from pathlib import Path
from typing import Any, Literal, TypeAlias

Scalar: TypeAlias = int | float | str | bool
Value: TypeAlias = Scalar | tuple[float, ...]
Outcome: TypeAlias = Literal["supported", "unknown", "unsupported"]
Observer: TypeAlias = Callable[[object], Sequence["ObservedFact"]]

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
            for wanted, actual in zip(expected, observed)
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
            for left_component, right_component in zip(left, right)
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


#: The compiled requirement that carries a hole's SIZE. A table row documents several
#: requirements per hole (location, through-ness, …); only this one is the fact
#: ``drawing_consumer`` asks about. Crediting the others would mean a hole with a located
#: row but no diameter counted as consumed.
_SIZE_REQUIREMENT = "bore.diameter"


def _drawing_consumer_outcomes(holes, drawing) -> list[Outcome]:
    """Per recognised hole: did the DRAWING carry its size? — observed, not declared.

    ``supported`` its size reached the sheet **and the annotation carrying it renders that
    value**; ``unsupported`` the engine accounts for the hole and did not place its size;
    ``unknown`` nothing can be joined to the hole without guessing.

    Be precise about ``unknown``: :func:`evaluate_case` credits a unit only when the state is
    ``supported``, so downstream an ``unknown`` scores as a MISS, distinguishable from a
    dropped callout only in the diagnostic text. It is an honest label, not an exemption.

    **One correspondence implementation** (#1206). This used to carry its own — matching
    recognised holes to IR features by axis, diameter and position, then asking whether any
    annotation named ``hc_*`` — beside `linting.hole_coverage.hole_requirement_outcomes`,
    which answers the same question. Two implementations of one question drift, and the copy
    was the more generous of the two: it credited holes the ledger declines to join.

    That generosity is a measurable score change, not a refactor. Measured base against head
    over all 16 STEP fixtures, 26 holes move: ``nist_ctc_03`` ap203 and ap242 lose five each
    (15/15 -> 10 supported + 5 unknown) and ``nist_ctc_04`` ap203 and ap242 lose eight each
    (54/54 -> 46 + 8). Every one is a hole the ledger reports ``unverifiable`` — it knows they
    exist and cannot tie them to a feature without guessing — where the old matcher scored
    them ``supported``. They now score ``unknown``. ``nist_ctc_02`` does not move at all; an
    earlier version of this paragraph named it and omitted CTC-03, which is the fixture that
    actually diverges. Crediting a hole whose
    evidence cannot be located is exactly the self-validation #1206 was opened to remove, so
    the lower number is the more honest one.

    **The ledger is a pointer, not proof** (@pzfreo's decision on #1206). ``placed`` means the
    engine recorded an annotation as carrying the requirement; this then follows that pointer
    through `linting.evidence` and confirms the annotation actually renders the approved
    value. A claim the drawing does not bear out is ``unsupported``, not ``supported``.

    Name prefixes are gone with the duplicate: provenance does not care what a renderer called
    its annotation.

    That does **not** close the turned-part gap, and an earlier draft of this paragraph said
    it did. Measured on ``Cylinder(20,60) - Cylinder(6,70)``, the outcome is ``unsupported``
    before and after, with a byte-identical annotation set.

    The cause was never the name. When this was written no annotation on that sheet carried a
    measurement claim at all — not ``ldr_z0`` (``ø12``), not ``dim_od`` (``ø40``), not
    ``dim_height`` — while the compiled plan did hold ``hole.bore.diameter`` and
    ``rotational.od.diameter``: the rotational render path threaded no ADR 0010 provenance.
    #1225 fixed the threading, so ``ldr_z0`` and ``dim_od`` now claim and both confirm; only
    ``dim_height`` still carries none, and that one is #1230 rather than a tagging gap.

    **The ledger symptom is unchanged by that fix** — measured head against ``main``,
    ``bore.diameter`` is still ``missing`` and this function still returns ``unsupported`` for
    that hole, exactly as the paragraph above says. (A draft of this sentence said "yields no
    outcome", which is false: there is one outcome and it is ``unsupported``. Contradicting a
    correct sentence four lines up, inside the fix for a docstring falsified by its own diff.)
    Tagging the annotation was necessary and is not sufficient, which is worth stating plainly
    here rather than letting "#1227 is fixed" read as "the turned part is covered".

    It is NOT #754, which closed on 2026-07-22 — an earlier draft of this paragraph cited it,
    which is the fourth instance of the stale citation #951 exists to remove, written inside a
    sentence correcting a different false claim.
    """
    from draftwright.linting.evidence import verify_measurement_claims
    from draftwright.linting.hole_coverage import canonical_hole_sites, hole_requirement_outcomes
    from draftwright.model.compiled import compile_dimensions

    model = drawing.model()
    # Recompiled through the public model rather than read off `Drawing._build`: the compiler
    # is deterministic over the model, so this is the same inventory the build used, and the
    # state-bus guard keeps `dwg._*` to `drawing.py` alone.
    plan = compile_dimensions(model)
    ledger = hole_requirement_outcomes(
        drawing.recognition(),
        getattr(model, "features", ()),
        drawing.registry,
        plan.diagnostics,
    )
    # NOT just `value_absent`. `supported` is meant to mean the annotation carrying the size
    # renders it, so anything short of `confirmed` fails that: an `unreadable` annotation
    # draws no text at all, and an `unresolved` one claims a measurement the compiler never
    # approved (the ADR 0016 Amdt 1 violation). Crediting either was the PR body's own
    # sentence — "and the annotation carrying it renders that value" — being false of the
    # code beneath it (#1223 review).
    unconfirmed = {
        claim.measurement
        for claim in verify_measurement_claims(drawing.registry, plan)
        if claim.state != "confirmed" and claim.measurement is not None
    }

    def _borne_out(entry) -> bool:
        """Whether the placement the ledger reports is confirmed by what is drawn."""
        return not any(
            getattr(claim, "feature", None) in entry.features
            and str(getattr(claim, "parameter", "")) == _SIZE_REQUIREMENT
            for claim in unconfirmed
        )

    by_position: dict[tuple, Outcome] = {}
    for entry in ledger:
        if entry.parameter_id == _SIZE_REQUIREMENT:
            state: Outcome = "supported" if entry.state == "placed" else "unsupported"
            if state == "supported" and not _borne_out(entry):
                state = "unsupported"
        else:
            # Non-size parameters contribute no site. An `unverifiable` entry needs no arm
            # of its own: its holes reach `unknown` through the lookup default below, and a
            # branch that cannot change an outcome is one a reader trusts wrongly. The
            # property it looked like it was enforcing — that every `unknown` joins an
            # `unverifiable` entry — is asserted in the tests, where it can fail.
            continue
        for member in entry.members:
            by_position.setdefault(member, state)

    # `canonical_hole_sites`, not `hole.location`: the ledger keys a THROUGH hole with its
    # axis coordinate zeroed, so the same bore keys identically whichever face it was measured
    # from. Keying on the raw location matched nothing for every through hole and reported
    # four otherwise-perfect corpus units as `unknown`.
    outcomes: list[Outcome] = []
    for hole in holes:
        sites = canonical_hole_sites(hole)
        outcomes.append(next((by_position[s] for s in sites if s in by_position), "unknown"))
    return outcomes


def _hole_model_outcomes(holes, recognition, features) -> list[Outcome]:
    """Per recognised hole: does *features* contain one exact IR owner?

    This deliberately reuses :func:`hole_requirement_outcomes` rather than growing a second
    geometry matcher in the evaluation module.  Only its correspondence evidence is read:
    annotation state from the empty registry is necessarily ``missing`` and contributes no
    credit.  The recognised ``holes`` supplied by the build remain the observed numerator;
    the corpus remains the independent denominator.
    """
    from draftwright.linting.hole_coverage import canonical_hole_sites, hole_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    ledger = hole_requirement_outcomes(recognition, features, AnnotationRegistry())
    by_position: dict[tuple, Outcome] = {}
    for entry in ledger:
        if entry.parameter_id != _SIZE_REQUIREMENT:
            continue
        state: Outcome = "supported" if entry.features else "unknown"
        for member in entry.members:
            by_position.setdefault(member, state)
    return [
        next(
            (by_position[site] for site in canonical_hole_sites(hole) if site in by_position),
            "unknown",
        )
        for hole in holes
    ]


def _declared_hole_model(part, holes):
    """Declare observed holes through the public ``Sheet.hole`` seam and return its IR."""
    from b123d_recognisers import HoleSpec

    from draftwright.sheet import Sheet

    sheet = Sheet(part)
    # Feature correspondence is independent of dimension selection.  An explicit empty set
    # keeps this a valid public Sheet without asking the planner to add evidence of its own.
    sheet.authored_dimensions()
    for observed in holes:
        spec = HoleSpec.from_hole(observed)
        axis = max(zip("xyz", spec.axis, strict=True), key=lambda item: abs(item[1]))[0]

        def recess(value):
            return None if value is None else (value.diameter, value.depth)

        sheet.hole(
            diameter=observed.diameter,
            at=observed.location,
            axis=axis,
            through=spec.bottom == "through",
            depth=observed.depth,
            cbore=recess(spec.cbore),
            spotface=recess(spec.spotface),
            csink=spec.csink,
        )
    return sheet.model()


def _generated_sheet_model(part, model):
    """Execute generated Sheet code through its public declarations and return its IR."""
    from draftwright.sheet_emit import emit_sheet_script

    source = emit_sheet_script(model, "part", "evaluation", title="EVALUATION", number="EVAL")
    prefix = source.split("drawing = sheet.build()", 1)[0]
    namespace: dict[str, object] = {"part": part}
    exec(compile(prefix, "<draftwright-evaluation>", "exec"), namespace)  # noqa: S102
    return getattr(namespace["sheet"], "model")()


def _pattern_kind(pattern) -> str:
    if hasattr(pattern, "diameter") and hasattr(pattern, "center"):
        return "bolt_circle"
    if hasattr(pattern, "row_pitch"):
        return "grid"
    return "linear"


def _pattern_members(pattern) -> tuple[tuple[float, float, float], ...]:
    """Recognition-owned member sites in the production ledger's canonical space."""
    from draftwright.linting.hole_coverage import canonical_hole_sites

    return canonical_hole_sites(pattern)


def _pattern_model_outcomes(patterns, recognition, features) -> list[Outcome]:
    """Per recognised pattern: does *features* contain one exact IR owner?"""
    from draftwright.linting.hole_coverage import hole_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    outcomes = hole_requirement_outcomes(recognition, features, AnnotationRegistry())
    by_members: dict[tuple, list] = {}
    for outcome in outcomes:
        if outcome.source_kind == "hole_pattern":
            by_members.setdefault(outcome.members, []).append(outcome)
    result: list[Outcome] = []
    for pattern in patterns:
        matched = by_members.get(_pattern_members(pattern), ())
        result.append(
            "supported"
            if matched and all(len(outcome.features) == 1 for outcome in matched)
            else "unknown"
        )
    return result


def _pattern_drawing_outcomes(patterns, drawing) -> list[Outcome]:
    """Per recognised pattern: did its grouping grammar reach the placed drawing?"""
    from draftwright.linting.evidence import verify_measurement_claims
    from draftwright.linting.hole_coverage import hole_requirement_outcomes
    from draftwright.model.compiled import compile_dimensions

    recognition = drawing.recognition()
    model = drawing.model()
    plan = compile_dimensions(model)
    outcomes = hole_requirement_outcomes(
        recognition,
        model.features,
        drawing.registry,
        plan.diagnostics,
    )
    unconfirmed = {
        claim.measurement
        for claim in verify_measurement_claims(drawing.registry, plan)
        if claim.state != "confirmed" and claim.measurement is not None
    }

    def rendered_group_count(outcome) -> bool:
        """Whether the exact owner's count-bearing callout actually renders ``N×``."""
        for name, annotation in drawing.registry.iter_named():
            measurements = drawing.registry.measurement_of(name)
            owns_pattern_diameter = any(
                getattr(measurement, "feature", None) in outcome.features
                and str(getattr(measurement, "parameter", "")) == "bore.diameter"
                for measurement in measurements
            )
            if not owns_pattern_diameter:
                continue
            if int(getattr(annotation, "covers_count", 1) or 1) != outcome.member_count:
                continue
            label = getattr(annotation, "label", None) or getattr(
                annotation, "_annotate_label", None
            )
            match = re.match(r"^\s*(\d+)\s*[×x]\s", str(label or ""))
            if match is not None and int(match.group(1)) == outcome.member_count:
                return True
        return False

    def rendered_interval_count(outcome, expected: int) -> bool:
        """Whether the exact pitch dimension renders its required interval multiplier."""
        for name, annotation in drawing.registry.iter_named():
            owns_pitch = any(
                getattr(measurement, "feature", None) in outcome.features
                and str(getattr(measurement, "parameter", "")) == outcome.parameter_id
                for measurement in drawing.registry.measurement_of(name)
            )
            if not owns_pitch:
                continue
            label = getattr(annotation, "label", None) or getattr(
                annotation, "_annotate_label", None
            )
            match = re.match(r"^\s*(\d+)\s*[×x]\s", str(label or ""))
            if match is not None and int(match.group(1)) == expected:
                return True
        return False

    def borne_out(outcome, pattern) -> bool:
        """Whether placed dimensional evidence renders its compiler-approved value."""
        if outcome.parameter_id == "grouping.count":
            return rendered_group_count(outcome)
        value_confirmed = not any(
            getattr(claim, "feature", None) in outcome.features
            and str(getattr(claim, "parameter", "")) == outcome.parameter_id
            for claim in unconfirmed
        )
        if not value_confirmed:
            return False
        if outcome.parameter_id == "pitch.length":
            interval_count = len(pattern.holes) - 1
        elif outcome.parameter_id == "grid_pitch.length.row":
            interval_count = pattern.rows - 1
        elif outcome.parameter_id == "grid_pitch.length.col":
            interval_count = pattern.cols - 1
        else:
            interval_count = None
        return interval_count is None or rendered_interval_count(outcome, interval_count)

    by_members: dict[tuple, list] = {}
    for outcome in outcomes:
        if outcome.source_kind == "hole_pattern":
            by_members.setdefault(outcome.members, []).append(outcome)
    expected_parameters = {
        "bolt_circle": {"grouping.count", "bolt_circle.diameter"},
        "linear": {"grouping.count", "pitch.length"},
        "grid": {
            "grouping.count",
            "grid_pitch.length.row",
            "grid_pitch.length.col",
        },
    }
    placed = {"placed", "satisfied_by_structured_note"}
    result: list[Outcome] = []
    for pattern in patterns:
        candidates = by_members.get(_pattern_members(pattern), ())
        relevant = {
            outcome.parameter_id: outcome
            for outcome in candidates
            if outcome.parameter_id in expected_parameters[_pattern_kind(pattern)]
        }
        expected = expected_parameters[_pattern_kind(pattern)]
        if not candidates or any(len(outcome.features) != 1 for outcome in candidates):
            result.append("unknown")
        elif set(relevant) != expected or any(
            outcome.state not in placed or not borne_out(outcome, pattern)
            for outcome in relevant.values()
        ):
            result.append("unsupported")
        else:
            result.append("supported")
    return result


def _declared_pattern_model(part, patterns):
    """Declare observed arrangements through public ``Sheet.pattern`` and return its IR."""
    from b123d_recognisers import HoleSpec

    from draftwright.model import hole
    from draftwright.sheet import Sheet

    sheet = Sheet(part)
    sheet.authored_dimensions()

    def recess(value):
        return None if value is None else (value.diameter, value.depth)

    for observed in patterns:
        member = observed.holes[0]
        spec = HoleSpec.from_hole(member)
        axis = max(zip("xyz", spec.axis, strict=True), key=lambda item: abs(item[1]))[0]
        declared_member = hole(
            diameter=member.diameter,
            at=member.location,
            axis=axis,
            through=spec.bottom == "through",
            depth=member.depth,
            cbore=recess(spec.cbore),
            spotface=recess(spec.spotface),
            csink=spec.csink,
        )
        members = tuple(item.location for item in observed.holes)
        center = getattr(observed, "center", None)
        if center is None:
            center = tuple(
                sum(point[index] for point in members) / len(members) for index in range(3)
            )
        kind = _pattern_kind(observed)
        kwargs: dict[str, object] = {
            "kind": kind,
            "count": len(members),
            "at": center,
            "axis": axis,
            "members": members,
        }
        if kind == "bolt_circle":
            kwargs["bcd"] = observed.diameter
        elif kind == "linear":
            kwargs.update(pitch=observed.pitch, direction=observed.direction)
        else:
            kwargs.update(
                grid=(observed.row_pitch, observed.col_pitch),
                rows=observed.rows,
                cols=observed.cols,
                angle=observed.angle,
            )
        sheet.pattern(declared_member, **kwargs)
    return sheet.model()


def _flat_point(flat) -> tuple[float, float, float]:
    point = getattr(flat, "at", None)
    if point is None:
        point = flat.frame.origin
    return tuple(round(float(component), 3) for component in point)  # type: ignore[return-value]


def _flat_identity(flat) -> tuple:
    """Physical stock identity, deliberately excluding its scored A/F value."""
    from draftwright._geometry import _canonical_axis_direction

    return (
        str(flat.axis),
        _canonical_axis_direction(flat.axis, getattr(flat, "axis_direction", None)),
        tuple(round(float(component), 3) for component in flat.axis_line),
        tuple(round(float(component), 3) for component in flat.stock_span),
    )


def _flat_groups(flats) -> list[tuple[tuple, tuple]]:
    grouped: dict[tuple, list] = {}
    for flat in flats:
        grouped.setdefault(_flat_identity(flat), []).append(flat)
    return [
        (identity, tuple(sorted(members, key=_flat_point)))
        for identity, members in sorted(grouped.items())
    ]


def _flat_parameters(members) -> dict[str, Value]:
    across_values = tuple(sorted({round(float(member.across), 3) for member in members}))
    across: Value = across_values[0] if len(across_values) == 1 else across_values
    anchors = tuple(component for member in members for component in _flat_point(member))
    return {"across": across, "face_count": len(members), "anchors": anchors}


def _flat_correspondence(flats, recognition, features) -> list[tuple[bool, tuple]]:
    """Per physical requirement, retain exact member IR and production-ledger evidence."""
    from draftwright.linting.flat_coverage import flat_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    ledger = flat_requirement_outcomes(recognition, features, AnnotationRegistry())
    ledger_by_identity: dict[tuple, list] = {}
    for outcome in ledger:
        ledger_by_identity.setdefault(_flat_identity(outcome), []).append(outcome)
    feature_by_identity: dict[tuple, list] = {}
    for feature in features:
        if getattr(feature, "kind", None) == "flat":
            feature_by_identity.setdefault(_flat_identity(feature), []).append(feature)

    result = []
    for identity, members in _flat_groups(flats):
        candidate_features = tuple(sorted(feature_by_identity.get(identity, ()), key=_flat_point))
        candidate_outcomes = ledger_by_identity.get(identity, ())
        exact_members = _flat_parameters(candidate_features) == _flat_parameters(
            members
        ) and tuple(_flat_point(feature) for feature in candidate_features) == tuple(
            _flat_point(member) for member in members
        )
        production_join = (
            len(candidate_outcomes) == 1
            and candidate_outcomes[0].state != "unverifiable"
            and round(float(candidate_outcomes[0].across), 3)
            in {round(float(member.across), 3) for member in members}
        )
        result.append((exact_members and production_join, candidate_features))
    return result


def _flat_model_outcomes(flats, recognition, features) -> list[Outcome]:
    return [
        "supported" if exact else "unknown"
        for exact, _ in _flat_correspondence(flats, recognition, features)
    ]


def _flat_drawing_outcomes(flats, drawing) -> list[Outcome]:
    """Per physical A/F requirement, verify placed semantic ownership and rendered value."""
    from draftwright.linting.evidence import verify_measurement_claims
    from draftwright.linting.flat_coverage import flat_requirement_outcomes
    from draftwright.model.compiled import compile_dimensions

    recognition = drawing.recognition()
    model = drawing.model()
    plan = compile_dimensions(model)
    ledger = flat_requirement_outcomes(
        recognition,
        model.features,
        drawing.registry,
        plan.diagnostics,
    )
    ledger_by_identity: dict[tuple, list] = {}
    for outcome in ledger:
        ledger_by_identity.setdefault(_flat_identity(outcome), []).append(outcome)
    confirmed = {
        claim.measurement
        for claim in verify_measurement_claims(drawing.registry, plan)
        if claim.state == "confirmed" and claim.measurement is not None
    }
    placed = {"placed", "satisfied_by_structured_note"}
    result: list[Outcome] = []
    for (identity, _members), (exact, features) in zip(
        _flat_groups(flats),
        _flat_correspondence(flats, recognition, model.features),
        strict=True,
    ):
        outcomes = ledger_by_identity.get(identity, ())
        if not exact or len(outcomes) != 1:
            result.append("unknown")
        elif outcomes[0].state not in placed or any(
            not any(
                getattr(claim, "feature", None) == feature
                and str(getattr(claim, "parameter", "")) == "flat.length"
                for claim in confirmed
            )
            for feature in features
        ):
            result.append("unsupported")
        else:
            result.append("supported")
    return result


def _declared_flat_model(part, flats):
    """Declare observed flat faces through public ``Sheet.flat`` and return its IR."""
    from draftwright.sheet import Sheet

    sheet = Sheet(part)
    sheet.authored_dimensions()
    for observed in flats:
        sheet.flat(
            axis=observed.axis,
            across=observed.across,
            at=observed.at,
            axis_line=observed.axis_line,
            stock_span=observed.stock_span,
            axis_direction=observed.axis_direction,
        )
    return sheet.model()


def _default_observers() -> Mapping[str, Observer]:
    def observe_holes(part: object) -> Sequence[ObservedFact]:
        # Lazy for COST, not for layering: `evaluation` is rank 7 and `builder` rank 6, so
        # a module-level import here is a legal downward edge and passes the DAG guard —
        # an earlier comment claimed otherwise. What it buys is not paying build123d's
        # ~6 s import to load this module. Import the concrete module rather than the
        # package root: `from draftwright import ...` pulls `__init__`, which the guard
        # treats as the TOP module and would make this a genuine upward edge.
        from draftwright.builder import build_drawing

        # ONE drawing per fixture, and ONE recognition: the records scored here come from
        # the build's own aggregate (ADR 0017's single owner per run), not a second
        # `build_recognition_result` call, so the facts being scored and the features they
        # are matched against cannot come from different recognition runs.
        try:
            drawing = build_drawing(part)  # type: ignore[arg-type]
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
                from b123d_recognisers import build_recognition_result

                holes = tuple(build_recognition_result(part).holes)  # type: ignore[arg-type]
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
            unknown: list[Outcome] = ["unknown"] * len(holes)

            def observed_boundary(
                name: str, observe: Callable[[], list[Outcome]]
            ) -> list[Outcome]:
                try:
                    result = observe()
                    if len(result) != len(holes):
                        raise ValueError(
                            f"observed {len(result)} outcomes for {len(holes)} recognised holes"
                        )
                    return result
                except Exception as exc:  # noqa: BLE001 — score a broken boundary, keep corpus
                    _log.warning(
                        "evaluation: %s observation failed (%s); scoring holes as unknown",
                        name,
                        exc,
                    )
                    return list(unknown)

            boundary_outcomes = {
                "ir_adapter": observed_boundary(
                    "ir_adapter",
                    lambda: _hole_model_outcomes(holes, recognition, drawing.model().features),
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

    def observe_hole_patterns(part: object) -> Sequence[ObservedFact]:
        from draftwright.builder import build_drawing

        try:
            drawing = build_drawing(part)  # type: ignore[arg-type]
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
        unknown: list[Outcome] = ["unknown"] * len(patterns)

        def observed_boundary(name: str, observe: Callable[[], list[Outcome]]) -> list[Outcome]:
            try:
                result = observe()
                if len(result) != len(patterns):
                    raise ValueError(
                        f"observed {len(result)} outcomes for {len(patterns)} recognised patterns"
                    )
                return result
            except Exception as exc:  # noqa: BLE001 — score a broken boundary, keep corpus
                _log.warning(
                    "evaluation: %s observation failed (%s); scoring patterns as unknown",
                    name,
                    exc,
                )
                return list(unknown)

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

    def observe_flats(part: object) -> Sequence[ObservedFact]:
        from draftwright.builder import build_drawing

        try:
            drawing = build_drawing(part)  # type: ignore[arg-type]
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
        unknown: list[Outcome] = ["unknown"] * len(groups)

        def observed_boundary(name: str, observe: Callable[[], list[Outcome]]) -> list[Outcome]:
            try:
                result = observe()
                if len(result) != len(groups):
                    raise ValueError(
                        f"observed {len(result)} outcomes for {len(groups)} physical flats"
                    )
                return result
            except Exception as exc:  # noqa: BLE001 — score a broken boundary, keep corpus
                _log.warning(
                    "evaluation: %s observation failed (%s); scoring flats as unknown",
                    name,
                    exc,
                )
                return list(unknown)

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

    return {
        "flats": observe_flats,
        "holes": observe_holes,
        "hole-patterns": observe_hole_patterns,
    }


def evaluate_step_corpus(
    corpus: BenchmarkCorpus,
    *,
    observers: Mapping[str, Observer] | None = None,
    outcomes: Mapping[str, Outcome] | None = None,
) -> CorpusEvaluation:
    """Import every pinned STEP fixture and evaluate normalized family observations."""
    from build123d import import_step

    if observers is None:
        defaults = _default_observers()
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
        observations = tuple(
            observation for family in corpus.scope for observation in registered[family](part)
        )
        evaluations.append(
            evaluate_case(
                case,
                observations=observations,
                outcome=resolved_outcomes.get(case.case_id, "supported"),
            )
        )
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
    "ParameterExpectation",
    "evaluate_case",
    "evaluate_corpus",
    "evaluate_step_corpus",
    "load_corpus",
]
