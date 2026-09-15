"""Exact deterministic cover selection for the real-geometry corpus."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields
from itertools import combinations
from typing import Any


@dataclass(frozen=True)
class CoverageSignature:
    """The independent evidence dimensions a real-geometry case protects."""

    recognizer_families: frozenset[str] = frozenset()
    topology_variants: frozenset[str] = frozenset()
    requirement_outcomes: frozenset[str] = frozenset()
    compiler_paths: frozenset[str] = frozenset()
    lint_codes: frozenset[str] = frozenset()
    mutation_kills: frozenset[str] = frozenset()

    def tokens(self) -> frozenset[str]:
        """Namespace every value so equal spelling in two dimensions cannot collapse."""

        return frozenset(
            f"{field.name}:{value}"
            for field in fields(self)
            for value in getattr(self, field.name)
        )


def case_coverage_signature(
    case: Any,
    *,
    scope: Iterable[str],
    topology_variants: Iterable[str] = (),
    lint_codes: Iterable[str],
    mutation_kills: Iterable[str],
) -> CoverageSignature:
    """Project one benchmark case into explicit, reviewable coverage obligations.

    The corpus supplies recognition families, classifications, parameter paths and required
    downstream outcomes. Lint and mutation evidence are deliberately mandatory: neither can
    be inferred from an oracle document, and silently leaving either empty would approve a
    deletion without the quality evidence the reduction plan requires.
    """

    facts = tuple(case.expected)
    families = frozenset(scope) | frozenset(fact.family for fact in facts)
    topology = {
        *(f"classification:{tag}" for tag in case.classification.split("+") if tag),
        *(f"declared:{variant}" for variant in topology_variants),
        f"fact-cardinality:{len(facts)}",
    }
    outcomes = {f"case:{case.expected_outcome}"}
    compiler = set()
    for fact in facts:
        identity_fields = ",".join(sorted(fact.identity))
        topology.add(f"{fact.family}:identity-fields:{identity_fields}")
        for parameter in fact.parameters:
            compiler.add(f"{fact.family}:{parameter}")
        for boundary in fact.required_downstream:
            outcomes.add(f"{fact.family}:{boundary}:supported")

    return CoverageSignature(
        recognizer_families=families,
        topology_variants=frozenset(topology),
        requirement_outcomes=frozenset(outcomes),
        compiler_paths=frozenset(compiler),
        lint_codes=frozenset(lint_codes),
        mutation_kills=frozenset(mutation_kills),
    )


def minimum_coverage_cases(
    signatures: Mapping[str, frozenset[str]], *, required: Iterable[str] = ()
) -> tuple[str, ...]:
    """Return the smallest lexicographic case set preserving every signature token.

    Corpora currently contain at most 13 cases, so exhaustive selection is both easier to
    review and guaranteed minimal. ``required`` retains named regressions even when another
    case happens to cover their current signature.
    """

    names = tuple(sorted(signatures))
    required_set = frozenset(required)
    unknown = required_set.difference(names)
    if unknown:
        raise KeyError(f"required cases are absent from the signature map: {sorted(unknown)}")
    if not names:
        return ()

    target = frozenset().union(*(signatures[name] for name in names))
    for size in range(len(required_set), len(names) + 1):
        for candidate in combinations(names, size):
            if not required_set.issubset(candidate):
                continue
            covered = frozenset().union(*(signatures[name] for name in candidate))
            if covered == target:
                return candidate
    raise AssertionError("the complete case set did not cover its own signature union")
