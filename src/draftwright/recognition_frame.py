"""Owned boundary for provider frames and body-local occurrence joins (#1357).

The provider owns frame inference, topology-preserving normalisation, and the immutable
recognition aggregate.  Draftwright owns the classification decision that selects the
aggregate's rotational policy and the rule that the exact local working solid, cylinders,
records, and frame travel as one unit. It also owns fail-closed joins between public provider
records when an occurrence key is absent. This module deliberately provides no raw fallback.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from b123d_recognisers import (
    FramedRecognitionResult,
    FrameGauge,
    FrameRefusalReason,
    PartFrame,
    PreparedFramedPart,
    RecognitionResult,
    RefusedPartFrame,
    TurnedProfile,
    prepare_framed_part,
)
from build123d import Shape

from draftwright._geometry import _classify_rotational_cylinders


class FramedRecognitionContractError(RuntimeError):
    """The provider returned a framed unit whose paired values do not correspond."""


class MultipleTurnedProfilesError(ValueError):
    """A caller requested the compatible singular view of a plural profile inventory."""


class AmbiguousTurnedOwnershipError(RuntimeError):
    """A released recognition record cannot identify one physical turned profile."""


def profiles_owning_axial_band(
    profiles: Iterable[Any],
    *,
    axis: str,
    centre: tuple[float, float, float],
    width: float,
    axis_tol: float = 1e-6,
    span_tol: float = 1e-3 + 1e-9,
) -> tuple[Any, ...]:
    """Profiles whose published axis line and axial span contain one record band."""

    axis_index = "xyz".index(axis)
    band_lo = float(centre[axis_index]) - float(width) / 2.0
    band_hi = float(centre[axis_index]) + float(width) / 2.0
    owners = []
    for profile in profiles:
        if profile.axis != axis:
            continue
        key = getattr(profile, "profile", None)
        if key is not None and any(
            abs(float(centre[index]) - float(key.axis_origin[index])) > axis_tol
            for index in range(3)
            if index != axis_index
        ):
            continue
        shoulders = tuple(float(value) for value in profile.shoulders)
        if (
            shoulders
            and min(shoulders) - span_tol <= band_lo
            and band_hi <= max(shoulders) + span_tol
        ):
            owners.append(profile)
    return tuple(owners)


def require_unambiguous_groove_owner(groove: Any, profiles: Iterable[Any]) -> tuple[Any, ...]:
    """Return the zero/one groove owner or refuse the missing provider contract."""

    centre = getattr(groove, "at", None)
    if centre is None:
        centre = groove.frame.origin
    owners = profiles_owning_axial_band(
        profiles,
        axis=groove.axis if hasattr(groove, "axis") else groove.frame.axis,
        centre=centre,
        width=groove.width,
    )
    if len(owners) > 1:
        raise AmbiguousTurnedOwnershipError(
            "a groove matches multiple body-local turned profiles, but "
            "b123d-recognisers 0.4.9 Groove records carry no profile identity; "
            "refusing to guess (https://github.com/pzfreo/b123d-recognisers/issues/354)"
        )
    return owners


@dataclass(frozen=True, slots=True)
class GeometryClassification:
    """One cylinder inventory and Draftwright's classification derived from that inventory."""

    z_cyls: tuple[Any, ...]
    cross_cyls: tuple[Any, ...]
    z_diams: tuple[float, ...]
    cross_diams: tuple[float, ...]
    od_diam: float | None
    od_axis: str
    is_rotational: bool


@dataclass(frozen=True, slots=True)
class FramePolicy:
    """What semantic direction claims a frame gauge establishes.

    Local principal-axis geometry is usable for every successful frame.  The other flags are
    intentionally conservative: an ORTHOGONAL frame leaves sign or interchange unobservable,
    while an AXIAL frame additionally leaves roll unobservable.
    """

    gauge: FrameGauge
    directed_ordered_basis: bool
    material_axis_identity: bool
    roll_observable: bool


_FRAME_POLICIES = {
    FrameGauge.FULL: FramePolicy(FrameGauge.FULL, True, True, True),
    FrameGauge.ORTHOGONAL: FramePolicy(FrameGauge.ORTHOGONAL, False, False, True),
    FrameGauge.AXIAL: FramePolicy(FrameGauge.AXIAL, False, False, False),
}


def frame_policy(frame: PartFrame) -> FramePolicy:
    """Return the fail-closed semantic policy for *frame*'s published gauge."""

    return _FRAME_POLICIES[frame.gauge]


@dataclass(frozen=True, slots=True)
class FramedDetection:
    """One source solid and one exact, coherent local recognition unit."""

    source_part: Shape
    prepared: PreparedFramedPart
    classification: GeometryClassification
    framed: FramedRecognitionResult

    @property
    def frame(self) -> PartFrame:
        return self.framed.frame

    @property
    def part(self) -> Shape:
        return self.framed.part

    @property
    def result(self) -> RecognitionResult:
        return self.framed.result

    @property
    def policy(self) -> FramePolicy:
        return frame_policy(self.frame)


@dataclass(frozen=True, slots=True)
class FramedDetectionRefusal:
    """A typed frame refusal with no guessed result and no implicit raw retry."""

    source_part: Shape
    reason: FrameRefusalReason


def classify_geometry(
    part: Shape,
    *,
    cylinders: tuple[tuple[Any, ...], tuple[Any, ...]],
) -> GeometryClassification:
    """Classify a prepared local *part* from its exact supplied cylinder inventory.

    The provider already scanned the exact local working solid, so Draftwright must reuse those
    records rather than inspect the topology a second time. There is deliberately no default
    caller-space scan: raw production retains its separate historical classifier.
    """

    z_cyls, cross_cyls = cylinders
    bbox = part.bounding_box()
    bbox_centre = bbox.center()
    classification = _classify_rotational_cylinders(
        (z_cyls, cross_cyls),
        sizes=(bbox.size.X, bbox.size.Y, bbox.size.Z),
        centre=(bbox_centre.X, bbox_centre.Y, bbox_centre.Z),
        allow_stepped_cross_axis=True,
    )
    return GeometryClassification(
        z_cyls=z_cyls,
        cross_cyls=cross_cyls,
        z_diams=classification.z_diams,
        cross_diams=classification.cross_diams,
        od_diam=classification.od_diam,
        od_axis=classification.od_axis,
        is_rotational=classification.is_rotational,
    )


def _same_cylinder_objects(prepared: PreparedFramedPart, framed: FramedRecognitionResult) -> bool:
    if len(prepared.cylinders) != len(framed.result.cylinders):
        return False
    return all(
        len(source) == len(recognised)
        and all(left is right for left, right in zip(source, recognised, strict=True))
        for source, recognised in zip(prepared.cylinders, framed.result.cylinders, strict=True)
    )


def prepare_framed_detection(part: Shape) -> FramedDetection | FramedDetectionRefusal:
    """Prepare, classify, and recognise *part* once in the provider-owned local frame.

    Classification happens after normalisation and reuses ``PreparedFramedPart.cylinders``.
    A refusal remains a refusal; callers must make any raw-path policy choice explicitly above
    this boundary.
    """

    prepared = prepare_framed_part(part)
    if isinstance(prepared, RefusedPartFrame):
        return FramedDetectionRefusal(part, prepared.reason)

    classification = classify_geometry(prepared.part, cylinders=prepared.cylinders)
    framed = prepared.recognise(rotational=classification.is_rotational)
    if (
        framed.frame is not prepared.frame
        or framed.part is not prepared.part
        or not _same_cylinder_objects(prepared, framed)
    ):
        raise FramedRecognitionContractError(
            "framed recognition did not preserve its prepared frame, part, and cylinders"
        )
    return FramedDetection(part, prepared, classification, framed)


def single_turned_profile(result: RecognitionResult) -> TurnedProfile | None:
    """Return the zero/one compatible projection, refusing a plural inventory."""

    profiles = result.turned_profiles
    if len(profiles) > 1:
        raise MultipleTurnedProfilesError(
            "single_turned_profile() requires zero or one physical profile, but the result "
            f"contains {len(profiles)}; consume result.turned_profiles or Analysis.profiles"
        )
    return profiles[0] if profiles else None


__all__ = [
    "AmbiguousTurnedOwnershipError",
    "FramePolicy",
    "FramedDetection",
    "FramedDetectionRefusal",
    "FramedRecognitionContractError",
    "GeometryClassification",
    "MultipleTurnedProfilesError",
    "classify_geometry",
    "frame_policy",
    "prepare_framed_detection",
    "profiles_owning_axial_band",
    "require_unambiguous_groove_owner",
    "single_turned_profile",
]
