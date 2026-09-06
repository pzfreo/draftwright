"""Reject incompatible provider schemas and mismatched recess correspondence."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from build123d import Box, Pos, Rot, SlotOverall, extrude
from quiddity import SectionRecessArray, build_raw_recognition_result
from test_issue_1471_section_recess_contract import _recess

import draftwright.oriented_slot_contract as slot_contract
from draftwright.linting import (
    channel_coverage,
    pocket_coverage,
    pocket_pattern_coverage,
    rectangular_blind_slot_coverage,
    round_bottom_blind_slot_coverage,
)
from draftwright.linting.section_recess_coverage import (
    lint_section_recess_coverage,
    unsupported_section_recess_outcomes,
)
from draftwright.section_recess_contract import (
    UnsupportedSectionRecess,
    section_recess_fields,
    section_recess_pattern_members,
)


@pytest.mark.parametrize("field", ("source", "ends"))
def test_public_slot_nested_types_reject_structural_lookalikes(field):
    part = Box(120, 90, 10) - Rot(0, 0, 30) * Box(24, 6, 20)
    (slot,) = build_raw_recognition_result(part).oriented_slots
    assert slot_contract.oriented_slot_provider_key(slot)
    altered = deepcopy(slot)
    parent = altered if field == "source" else altered.source
    original = getattr(parent, field)
    lookalike = SimpleNamespace(
        **{name: getattr(original, name) for name in original.__dataclass_fields__}
    )
    object.__setattr__(parent, field, lookalike)
    assert getattr(parent, field) is lookalike
    with pytest.raises(TypeError, match="released passage record schema"):
        slot_contract.oriented_slot_provider_key(altered)


@pytest.fixture(scope="module")
def pocket():
    return _recess("pocket")[1]


@pytest.fixture(scope="module")
def channel():
    return _recess("channel")[1]


@pytest.mark.parametrize("source", (None, {}, SimpleNamespace()))
def test_recess_requires_an_exact_occurrence(source):
    with pytest.raises(TypeError, match="exact SectionRecess"):
        section_recess_fields(source)


@pytest.mark.parametrize("grammar", ("channel", "pocket", "pattern", "rectangular", "round"))
def test_correspondence_rejects_a_different_supported_recess_grammar(pocket, channel, grammar):
    key, source = {
        "channel": (channel_coverage._key, pocket),
        "pocket": (pocket_coverage._key, channel),
        "pattern": (pocket_pattern_coverage._member_spec, channel),
        "rectangular": (rectangular_blind_slot_coverage.rectangular_blind_slot_key, pocket),
        "round": (round_bottom_blind_slot_coverage.round_bottom_blind_slot_key, pocket),
    }[grammar]
    with pytest.raises(ValueError, match="grammar"):
        key(source)


@pytest.fixture(scope="module")
def array_result():
    part = Box(120, 40, 20)
    for x in (-30, 0, 30):
        part -= Pos(x, 0, 8) * Box(20, 10, 10)
    result = build_raw_recognition_result(part, rotational=False)
    assert len(result.section_recesses) == 3
    assert len(result.section_recess_patterns) == 1
    assert type(result.section_recess_patterns[0]) is SectionRecessArray
    return result


@pytest.mark.parametrize(
    "fault", ("pattern_type", "mutable", "foreign", "duplicate", "missing", "pitch", "body")
)
def test_patterns_cannot_adopt_an_inconsistent_inventory(array_result, fault):
    (pattern,) = array_result.section_recess_patterns
    records = array_result.section_recesses
    assert section_recess_pattern_members(pattern, records) == records
    if fault == "pattern_type":
        pattern = SimpleNamespace(members=pattern.members)
        message = "exact public array"
    elif fault == "mutable":
        records = list(records)
        message = "immutable inventory"
    elif fault == "foreign":
        records = (None, *records[1:])
        message = "immutable inventory"
    elif fault == "duplicate":
        records = (*records, records[0])
        message = "repeats an occurrence"
    elif fault == "missing":
        records = records[1:]
        message = "outside the supplied inventory"
    elif fault == "pitch":
        pattern = replace(pattern, pitch=pattern.pitch + 5)
        message = "lattice"
    else:
        records = (replace(records[0], body=100), *records[1:])
        message = "body-local geometry"
    with pytest.raises((TypeError, ValueError), match=message):
        section_recess_pattern_members(pattern, records)


def test_unsupported_recess_observer_requires_the_original_aggregate(array_result):
    assert unsupported_section_recess_outcomes(None) == []
    with pytest.raises(TypeError, match="exact RecognitionResult"):
        unsupported_section_recess_outcomes(SimpleNamespace(section_recesses=()))
    altered = replace(
        array_result, section_recess_refusals=(SimpleNamespace(reason="unsupported"),)
    )
    with pytest.raises(TypeError, match="exact public refusal"):
        unsupported_section_recess_outcomes(altered)


def _profile(source, points, bulges=None):
    old = source.geometry.profile
    if bulges is None:
        bulges = [0.0] * len(points)
    vertices = tuple(
        replace(old.boundary[0], point=point, bulge=bulge)
        for point, bulge in zip(points, bulges, strict=True)
    )
    changes = dict(boundary=vertices)
    if hasattr(old, "opening"):
        changes["opening"] = (points[-1], points[0])
    return replace(source, geometry=replace(source.geometry, profile=replace(old, **changes)))


@pytest.mark.parametrize(
    ("points", "message"),
    [
        (((-10, 7.5), (-10, -7.5), (0, -7.5), (10, -7.5), (10, 7.5)), "four-vertex"),
        (((-10, 7.5), (-10, -7.5), (10, -7.5), (10, 6.5)), "principal direction"),
        (((-10, 7.5), (-10, -7.5), (10, -6.5), (10, 7.5)), "parallel flat floor"),
        (((-10, 7.5), (-9, -7.5), (9, -7.5), (10, 7.5)), "perpendicular walls"),
    ],
)
def test_unsupported_open_profile_cannot_emit_rectangular_measurements(channel, points, message):
    altered = _profile(channel, points)
    with pytest.raises(UnsupportedSectionRecess, match=message):
        section_recess_fields(altered)


def test_curved_channel_has_an_explicit_unsupported_outcome(channel, array_result):
    points = tuple(v.point for v in channel.geometry.profile.boundary)
    altered = _profile(channel, points, (0.1, 0.0, 0.1, 0.0))
    result = replace(array_result, section_recesses=(altered,), section_recess_patterns=())
    (outcome,) = unsupported_section_recess_outcomes(result)
    assert outcome.source_records[0] is altered
    assert outcome.source_at == (0, 0, 7.5)
    assert outcome.state == "unsupported" and outcome.requirement_count == 1
    (issue,) = lint_section_recess_coverage(result)
    assert issue.code == "section_recess_requirement_unsupported"
    assert "curved channel" in issue.message


@pytest.fixture(scope="module")
def obround():
    part = Box(60, 60, 20) - Pos(0, 0, 5) * extrude(SlotOverall(30, 8), 12)
    (source,) = build_raw_recognition_result(part, rotational=False).section_recesses
    assert source.classification.section_shape == "obround"
    return source


def test_obround_sloped_end_requires_explicit_refusal(obround):
    geometry = obround.geometry
    altered = replace(
        obround,
        geometry=replace(
            geometry,
            ends=replace(
                geometry.ends,
                low=replace(
                    geometry.ends.low,
                    surface=replace(geometry.ends.low.surface, gradient=(0.1, 0.0)),
                ),
            ),
        ),
    )
    with pytest.raises(UnsupportedSectionRecess, match="perpendicular run ends"):
        section_recess_fields(altered)


def test_obround_major_arcs_are_not_semicircular_ends(obround):
    vertices = obround.geometry.profile.boundary
    altered = _profile(
        obround, tuple(v.point for v in vertices), tuple(2.0 if v.bulge else 0.0 for v in vertices)
    )
    with pytest.raises(UnsupportedSectionRecess, match="semicircular ends"):
        section_recess_fields(altered)


@pytest.mark.parametrize(
    ("points", "bulges", "message"),
    [
        (((-11, -5), (11, -3), (11, 5), (-11, 3)), (0, 1, 0, 1), "principal long"),
        (((-12, -4), (10, -4), (12, 4), (-10, 4)), (0, 1, 0, 1), "chord must be perpendicular"),
    ],
)
def test_obround_classification_cannot_override_unsupported_boundary(
    obround, points, bulges, message
):
    altered = _profile(obround, points, bulges)
    with pytest.raises(UnsupportedSectionRecess, match=message):
        section_recess_fields(altered)


def test_projected_recess_measurements_cannot_overflow(channel):
    geometry = channel.geometry
    altered = replace(
        channel, geometry=replace(geometry, frame=replace(geometry.frame, origin=(0, 1e308, 0)))
    )
    with pytest.raises(ValueError, match="finite and positive"):
        section_recess_fields(altered)
