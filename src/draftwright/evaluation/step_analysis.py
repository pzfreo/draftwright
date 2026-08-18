"""Evidence-based STEP analysis evaluation (#1169).

This module scores recogniser observations against an independently authored oracle.  It does
not inspect ``RecognitionResult`` or the feature census: adapters supply observations, while the
benchmark case supplies the denominator and tolerances.

The ``drawing_consumer`` boundary is OBSERVED from a real build (#1202) rather than read from
a capability declaration. Known limit of that approach: it reads the ADR 0010 provenance seam,
which ``Drawing.annotations_of`` documents as growing "as passes are migrated". An un-tagged
render pass therefore reads as a genuine omission, and this is a CLASS of limitation rather
than a single case. Two instances are known:

* the hole-table escalation, which withdraws the individual callouts and records the
  substitution on the table — admitted here via that ledger;
* a **turned** part, where the bore's diameter reaches the sheet as a ``Leader`` (``ldr_z0``)
  whose ``registry.feature_of`` is ``None``, so it is neither a callout nor a table row. That
  is inherited ADR 0015 / #754 debt (rotational OD/bore groups are not planner-routed) and
  the engine's own ledger reports it as missing too, but this benchmark will report a loss
  for a hole whose size is visibly printed. No corpus fixture is turned today; adding one
  without addressing #754 would make the number wrong.

A new representation route must be admitted here or it registers as a false loss.
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


#: Position agreement required to call a recognised hole and an IR feature the same hole.
#: Tiny on purpose: the IR carries the recogniser's own ``hole.location`` verbatim
#: (``model/detect.py`` builds ``Frame(origin=_xyz(rep.location))``), so agreement is
#: EXACT on every corpus fixture — worst residual measured at 0.0, including the
#: side-drilled case whose z is 6.66e-15. An earlier version compared only the in-plane
#: coordinates, on a guess that the compiler might mint the origin at the far end of the
#: bore. It does not, and dropping the axis component made two opposed coaxial holes of
#: equal diameter alias onto one feature, hiding a dropped callout on the second.
_CORRESPONDENCE_TOLERANCE = 1e-6


@dataclass(frozen=True)
class _Candidate:
    """One IR feature and the hole positions it can account for."""

    feature: object
    axis: str
    diameter: float
    positions: tuple[tuple[float, ...], ...]


def _hole_candidates(drawing) -> list[_Candidate]:
    """Every IR feature that can account for a recognised hole.

    Patterned holes lower to a ``PatternFeature`` carrying a representative
    ``HoleFeature`` and the member centres — they are NOT ``HoleFeature`` instances, so
    matching on that type alone made recognising a pattern (a capability) *lower* the
    score: four identical holes scored ``unknown`` where the same four at the same
    positions with distinct diameters scored ``supported``, both fully drawn.

    Dispatch is on the IR's own ``kind`` discriminator, which the engine itself uses,
    rather than on ``type(f).__name__``.
    """
    candidates: list[_Candidate] = []
    for feature in drawing.model().features:
        kind = getattr(feature, "kind", None)
        if kind == "hole":
            # A grouped ``count×`` callout covers several holes through one feature.
            positions = feature.members or (feature.frame.origin,)
            candidates.append(
                _Candidate(feature, feature.frame.axis, feature.diameter, tuple(positions))
            )
        elif kind == "pattern" and getattr(feature.member, "kind", None) == "hole":
            candidates.append(
                _Candidate(
                    feature, feature.frame.axis, feature.member.diameter, tuple(feature.members)
                )
            )
    return candidates


#: The compiled requirement that carries a hole's SIZE. A table row documents several
#: requirements per hole (location, through-ness, …); only this one is the fact
#: ``drawing_consumer`` asks about.
_SIZE_REQUIREMENT = "bore.diameter"


def _table_represented(drawing) -> list:
    """Features the engine recorded as carried by a hole TABLE rather than a callout.

    Above ~16 scattered holes the engine withdraws the individual ``hc_*`` callouts and
    places one table plus balloons, recording the substitution on the table object. Reading
    only ``hc_`` scored every hole on such a sheet as lost — measured 16 of 16 on a dense
    plate with no lint issues at all — which inverted the metric: a correct sheet scored
    worse than the same part with the table forced to fail.

    Read ``covers_hole_representations_by_requirement``. An earlier cut read a
    ``…_by_feature`` sibling that no caller ever populated — its ``representation_features``
    argument appeared in the whole tree only at its own definition and its own use — so the
    read returned nothing on every real drawing while a hand-built stub in the tests made it
    look correct. The dead field was removed in #1217; this note stays because the lesson is
    about the stub, not the field.

    Filtered to the SIZE requirement: a table row also documents location and through-ness,
    and crediting those would mean a hole with a located row but no diameter counted as
    consumed.

    Safe against a dropped table: the ledger is written only after the table is placed and
    the balloon-completeness gate passes, and the annotation transaction rolls the table
    back before that point, so a `table_dropped` sheet carries no entries (verified).
    """
    covered = []
    for _name, annotation in drawing.iter_annotations():
        for entry in getattr(annotation, "covers_hole_representations_by_requirement", ()):
            feature, parameter = entry[0], entry[1]
            if parameter == _SIZE_REQUIREMENT:
                covered.append(feature)
    return covered


def _consumed(feature, drawing, represented) -> bool:
    """Did this feature's SIZE reach the sheet, by either sanctioned route?

    Only the callout or the table counts. A hole whose callout was dropped still keeps its
    centre mark and location dims (measured: ``m_cm0``, ``m_locx0``), so counting any
    annotation would score a dropped callout as consumed.
    """
    if any(name.startswith("hc_") for name in drawing.annotations_of(feature)):
        return True
    return any(represented_feature == feature for represented_feature in represented)


def _drawing_consumer_outcomes(holes, drawing) -> list[Outcome]:
    """Per recognised hole: did the DRAWING carry it? — observed, not declared.

    ``supported`` its size reached the sheet; ``unsupported`` a feature accounts for the
    hole and carries neither callout nor table row; ``unknown`` no feature accounts for
    it, which is a gap in the recognition-record-to-IR correspondence ADR 0017 explicitly
    does not yet provide.

    Be precise about ``unknown``: :func:`evaluate_case` credits a unit only when the state
    is ``supported``, so downstream an ``unknown`` scores as a MISS, distinguishable from
    a dropped callout only in the diagnostic text. It is an honest label, not an exemption
    — an earlier version of this docstring claimed it was "never scored as either success
    or failure", which was wrong about the pipeline it feeds.

    Matching is injective per POSITION: a matched position is consumed, so two opposed
    coaxial holes of equal diameter cannot both resolve to the first candidate. Grouped
    features legitimately account for several holes, which is why the position is consumed
    and not the whole candidate.
    """
    # Imported here, not at module level: `_geometry` imports build123d, so a top-level
    # import would pull the CAD kernel into every consumer of this module — measured, it
    # took import cost from 0.01 s to 1.27 s and made the lazy `builder` import below buy
    # nothing at all. An earlier comment claimed that laziness saved the kernel import
    # while this line was already paying it.
    from draftwright._geometry import _axis_letter_of

    remaining = [(candidate, list(candidate.positions)) for candidate in _hole_candidates(drawing)]
    represented = _table_represented(drawing)
    outcomes: list[Outcome] = []
    for hole in holes:
        axis = _axis_letter_of(hole.axis)
        target = tuple(float(value) for value in hole.location)
        for candidate, positions in remaining:
            if candidate.axis != axis or abs(candidate.diameter - hole.diameter) > 1e-9:
                continue
            hit = next(
                (
                    position
                    for position in positions
                    if all(
                        abs(a - b) <= _CORRESPONDENCE_TOLERANCE
                        for a, b in zip(tuple(float(v) for v in position), target, strict=True)
                    )
                ),
                None,
            )
            if hit is None:
                continue
            positions.remove(hit)
            outcomes.append(
                "supported"
                if _consumed(candidate.feature, drawing, represented)
                else "unsupported"
            )
            break
        else:
            outcomes.append("unknown")
    return outcomes


def _default_observers() -> Mapping[str, Observer]:
    from draftwright.recogniser_contract import (
        consumer_capability_declaration,
        validate_recogniser_capabilities,
    )

    consumer = consumer_capability_declaration()
    validate_recogniser_capabilities(consumer)
    declaration = next(family for family in consumer["families"] if family["id"] == "holes")
    # The other three boundaries remain DECLARED states — "this code path exists" — which
    # is the same self-validating shape, one layer along, and is tracked separately. Only
    # `drawing_consumer` is observed here, because that is the boundary #1176 is about:
    # a drawing scoring 1.0 while omitting the geometry it owes.
    declared = {
        boundary: declaration[boundary]["state"]
        for boundary in _DOWNSTREAM_BOUNDARIES
        if boundary != "drawing_consumer"
    }

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
            outcomes: list[Outcome] = ["unknown"] * len(holes)
        else:
            recognition = drawing.recognition()
            if recognition is None:  # pragma: no cover — the detected path always fills it
                from b123d_recognisers import build_recognition_result

                recognition = build_recognition_result(part)  # type: ignore[arg-type]
            holes = tuple(recognition.holes)
            outcomes = _drawing_consumer_outcomes(holes, drawing)
        return tuple(
            ObservedFact(
                family="holes",
                identity={"axis": hole.axis, "location": hole.location},
                parameters={
                    "bottom": hole.bottom,
                    "depth": hole.depth,
                    "diameter": hole.diameter,
                },
                downstream={**declared, "drawing_consumer": outcome},
            )
            for hole, outcome in zip(holes, outcomes, strict=True)
        )

    return {"holes": observe_holes}


def evaluate_step_corpus(
    corpus: BenchmarkCorpus,
    *,
    observers: Mapping[str, Observer] | None = None,
    outcomes: Mapping[str, Outcome] | None = None,
) -> CorpusEvaluation:
    """Import every pinned STEP fixture and evaluate normalized family observations."""
    from build123d import import_step

    registered: Mapping[str, Observer] = _default_observers() if observers is None else observers
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
