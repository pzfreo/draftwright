"""Draftwright's single framed-recognition selection boundary (#1357).

The provider owns frame inference and topology-preserving normalization. Draftwright owns
classification and the decision to compile a framed or caller-coordinate unit.  This module
keeps the selected shape, aggregate, classification, and frame inseparable so no downstream
consumer can accidentally combine local records with caller-space geometry.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, Literal, TypeVar

from b123d_recognisers import (
    FramedPreparation,
    PartFrame,
    RecognitionResult,
    RefusedPartFrame,
    analyse_cylinders,
    build_raw_recognition_result,
    prepare_framed_part,
)
from build123d import Shape

ClassificationT = TypeVar("ClassificationT")
RecognitionFrameStatus = Literal["framed", "raw_fallback", "raw"]


@dataclass(frozen=True)
class Classification(Generic[ClassificationT]):
    """A caller-owned classification plus the aggregate's rotational gate."""

    value: ClassificationT
    rotational: bool


@dataclass(frozen=True)
class AdaptedRecognition(Generic[ClassificationT]):
    """One coherent working-shape/result/classification unit plus source provenance."""

    source_part: Shape
    working_part: Shape
    result: RecognitionResult
    classification: ClassificationT
    frame: PartFrame | None
    status: RecognitionFrameStatus
    refusal_reason: str | None = None

    @property
    def decision(self) -> dict[str, object]:
        """Return the stable, JSON-friendly rollout decision."""

        return {
            "status": self.status,
            "gauge": self.frame.gauge.value if self.frame is not None else None,
            "refusal_reason": self.refusal_reason,
        }


def adapt_recognition(
    part: Shape,
    *,
    framed: bool,
    classify: Callable[[Shape, object], Classification[ClassificationT]],
    prepare: Callable[[Shape], FramedPreparation] | None = None,
    raw_builder: Callable[..., RecognitionResult] | None = None,
) -> AdaptedRecognition[ClassificationT]:
    """Select and recognise one coordinate-coherent automatic compiler unit.

    A successful framed route classifies the provider's exact normalized solid from the
    provider's frozen cylinder substrate *before* its only aggregate run.  A typed refusal
    takes one explicit caller-coordinate run.  The raw rollout route does the same
    classification/aggregate sequence in caller coordinates.  Declared builds never call this
    function.
    """

    prepare = prepare_framed_part if prepare is None else prepare
    raw_builder = build_raw_recognition_result if raw_builder is None else raw_builder

    if framed:
        prepared = prepare(part)
        if not isinstance(prepared, RefusedPartFrame):
            classified = classify(prepared.part, prepared.cylinders)
            outcome = prepared.recognise(rotational=classified.rotational)
            return AdaptedRecognition(
                source_part=part,
                working_part=outcome.part,
                result=outcome.result,
                classification=classified.value,
                frame=outcome.frame,
                status="framed",
            )
        refusal_reason = prepared.reason.value
        status: RecognitionFrameStatus = "raw_fallback"
    else:
        refusal_reason = None
        status = "raw"

    cylinders = analyse_cylinders(part)
    classified = classify(part, cylinders)
    result = raw_builder(
        part,
        cylinders=cylinders,
        rotational=classified.rotational,
    )
    return AdaptedRecognition(
        source_part=part,
        working_part=part,
        result=result,
        classification=classified.value,
        frame=None,
        status=status,
        refusal_reason=refusal_reason,
    )
