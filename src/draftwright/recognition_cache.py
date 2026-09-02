"""Draftwright-owned lifecycle state for immutable recognition results.

The recognition algorithms and values live in :mod:`b123d_recognisers`.  This cache does not:
it implements Draftwright's build-versus-lazy-critique policy (ADRs 0015/0017), so it remains
consumer state at the bottom of Draftwright's dependency graph.
"""

from __future__ import annotations

from dataclasses import dataclass

from b123d_recognisers import RecognitionResult
from b123d_recognisers.evidence import RecognitionEvidence, build_recognition_evidence


def _result_from_evidence(evidence: RecognitionEvidence) -> RecognitionResult:
    """Project the established aggregate from one evidence acquisition.

    Keeping this as one tiny seam lets semantic mutation tests replace the provider result
    without fabricating provider-issued references.  Callers discard the now-unpaired evidence
    when that happens, preserving the same-run invariant instead of attaching stale authority to
    a deliberately altered aggregate.
    """

    return evidence.result


@dataclass
class RecognitionCache:
    """One drawing's optional recognition run, built at most once on demand.

    ``result`` remains the established geometry inventory consumed by Draftwright.  When the
    run entered through the provider's raw evidence API, ``evidence`` retains that same run's
    accepted-occurrence/face authority for later reporting.  A cache may still be seeded with a
    bare result (notably framed recognition); it must not rerun recognition merely to backfill
    evidence from a different authority universe.
    """

    result: RecognitionResult | None = None
    evidence: RecognitionEvidence | None = None

    def __post_init__(self) -> None:
        if self.evidence is None:
            return
        if self.result is None:
            self.result = self.evidence.result
        elif self.result is not self.evidence.result:
            raise ValueError("recognition evidence and result must come from the same run")

    def seed(
        self,
        result: RecognitionResult | None,
        *,
        evidence: RecognitionEvidence | None = None,
    ) -> None:
        """Replace the cache atomically with one coherent recognition acquisition."""

        if evidence is not None:
            if result is None:
                result = evidence.result
            elif result is not evidence.result:
                raise ValueError("recognition evidence and result must come from the same run")
        self.result = result
        self.evidence = evidence

    def ensure(self, part, *, cylinders=None, rotational: bool = False) -> RecognitionResult:
        """Return this drawing's result, recognising *part* only when still empty."""

        if self.result is None:
            evidence = build_recognition_evidence(
                part,
                cylinders=cylinders,
                rotational=rotational,
            )
            result = _result_from_evidence(evidence)
            self.seed(result, evidence=evidence if result is evidence.result else None)
        assert self.result is not None
        return self.result
