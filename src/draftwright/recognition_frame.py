"""Draftwright's owned framed-recognition boundary (#1357).

The external package owns frame inference and topology-preserving normalisation. Draftwright
owns the consumer decision: automatic detection compiles the returned local working solid and
its aggregate together, while retaining the caller-space solid and frame as provenance. A
typed frame refusal takes the documented legacy path; no downstream stage is allowed to combine
local records with caller-space geometry or independently recreate the provider's transform.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from b123d_recognisers import (
    FramedRecognitionResult,
    PartFrame,
    RecognitionResult,
    RefusedPartFrame,
    build_framed_recognition_result,
    build_recognition_result,
)
from build123d import Shape

RecognitionFrameStatus = Literal["framed", "legacy_fallback", "legacy"]


@dataclass(frozen=True)
class AdaptedRecognition:
    """One coherent shape/result pair plus caller-space provenance.

    ``part`` and ``result`` are always expressed in the same coordinate system. ``source_part``
    is never transformed. ``frame`` maps source coordinates to ``part`` coordinates when the
    framed route succeeds and is ``None`` on either legacy route.
    """

    source_part: Shape
    part: Shape
    result: RecognitionResult
    frame: PartFrame | None
    status: RecognitionFrameStatus
    refusal_reason: str | None = None

    @property
    def decision(self) -> dict[str, object]:
        """JSON-friendly account of which coordinate contract was selected."""

        return {
            "status": self.status,
            "gauge": self.frame.gauge.value if self.frame is not None else None,
            "refusal_reason": self.refusal_reason,
        }


def adapt_recognition(
    part: Shape,
    *,
    rotational: bool = False,
    framed: bool = True,
    cylinders=None,
    framed_builder: Callable[..., FramedRecognitionResult | RefusedPartFrame] = (
        build_framed_recognition_result
    ),
    legacy_builder: Callable[..., RecognitionResult] = build_recognition_result,
) -> AdaptedRecognition:
    """Recognise *part* through the one sanctioned frame/IR boundary.

    ``framed=False`` deliberately retains the old route for rollout comparison. A provider
    refusal also falls back to that route, but records the closed refusal reason rather than
    silently treating the caller axes as an inferred part frame.
    """

    legacy_kwargs = {"rotational": rotational}
    if cylinders is not None:
        legacy_kwargs["cylinders"] = cylinders

    if not framed:
        return AdaptedRecognition(
            source_part=part,
            part=part,
            result=legacy_builder(part, **legacy_kwargs),
            frame=None,
            status="legacy",
        )

    outcome = framed_builder(part, rotational=rotational)
    if isinstance(outcome, RefusedPartFrame):
        return AdaptedRecognition(
            source_part=part,
            part=part,
            result=legacy_builder(part, **legacy_kwargs),
            frame=None,
            status="legacy_fallback",
            refusal_reason=outcome.reason.value,
        )
    return AdaptedRecognition(
        source_part=part,
        part=outcome.part,
        result=outcome.result,
        frame=outcome.frame,
        status="framed",
    )
