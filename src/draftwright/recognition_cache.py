"""Draftwright-owned lifecycle state for immutable recognition results.

The recognition algorithms and values live in :mod:`b123d_recognisers`.  This cache does not:
it implements Draftwright's build-versus-lazy-critique policy (ADRs 0015/0017), so it remains
consumer state at the bottom of Draftwright's dependency graph.
"""

from __future__ import annotations

from dataclasses import dataclass

from b123d_recognisers import RecognitionResult, build_recognition_result


@dataclass
class RecognitionCache:
    """One drawing's optional recognition result, built at most once on demand."""

    result: RecognitionResult | None = None

    def ensure(self, part) -> RecognitionResult:
        """Return this drawing's result, recognising *part* only when still empty."""

        if self.result is None:
            self.result = build_recognition_result(part)
        return self.result
