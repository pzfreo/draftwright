"""Common source authority for explicitly related drawing builds (ADR 1/3/5)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from draftwright._core import Analysis
from draftwright.model.ir import (
    AuthoredDimension,
    ControlFrame,
    DatumRef,
    Finish,
    Note,
    PartModel,
    PmiFeature,
)

_ANNOTATION_FEATURES = (AuthoredDimension, ControlFrame, DatumRef, Finish, Note, PmiFeature)


def physical_features(features):
    """Retain physical owners; unknown feature kinds are physical, never exemptions."""
    return tuple(feature for feature in features if not isinstance(feature, _ANNOTATION_FEATURES))


@dataclass(frozen=True)
class DocumentInput:
    """An owned intake; references establish authority only within its lifetime."""

    analysis: Analysis
    source_name: str
    source_bytes: bytes = field(repr=False)
    features: tuple = field(init=False)
    _model: PartModel = field(init=False, repr=False)

    def __post_init__(self):
        analysis = self.analysis
        evidence, ownership = analysis.recognition_evidence, analysis.recognition_ownership
        if (
            (analysis.recognition_frame_decision or {}).get("status") != "raw"
            or evidence is None
            or ownership is None
            or evidence.result is not analysis.recognition
            or ownership.evidence is not evidence
            or not isinstance(analysis.model, PartModel)
        ):
            raise ValueError("a document requires exact raw recognition and conversion ownership")
        model = analysis.model
        features = tuple(model.features)
        physical = physical_features(features)
        if len({id(feature) for feature in physical}) != len(physical):
            raise ValueError("the document physical inventory repeats an owner")
        object.__setattr__(self, "features", physical)
        object.__setattr__(
            self,
            "_model",
            replace(
                model,
                features=list(features),
                datums=list(model.datums),
                decorations=dict(model.decorations),
            ),
        )

    def validate_features(self, features):
        current = physical_features(features)
        if len(current) != len(self.features) or any(
            sum(candidate is feature for candidate in current) != 1 for feature in self.features
        ):
            raise ValueError(
                "document physical features are sealed; use exact common features and "
                "per-sheet dimension intents or decorations"
            )

    def validate(self, part, features):
        if part is not self.analysis.part:
            raise ValueError("document members must use the exact common working solid")
        self.validate_features(features)

    def model(self, features):
        """Copy mutable authoring containers while preserving exact source owners."""
        self.validate_features(features)
        return replace(
            self._model,
            features=list(features),
            datums=list(self._model.datums),
            decorations=dict(self._model.decorations),
        )

    def initial_features(self):
        return tuple(self._model.features)
