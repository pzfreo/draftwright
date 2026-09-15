"""Exact deterministic cover selection for the real-geometry corpus."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from itertools import combinations


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
