"""First Quiddity migration slice: released geometry against authored pocket facts.

The two providers run separately for comparison. Only primitive JSON enters the new adapter;
the declared-model drawing still uses the pinned production provider for physical lint. This
proves the new pocket lowering and existing back end, not completion of provider adoption.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
import quiddity
from build123d import Box, Pos, import_step

from draftwright import Sheet, build_drawing
from draftwright.audit import diff_builds
from draftwright.model.detect import (
    _convert_section_recess_pocket,
)
from draftwright.section_recess_contract import UnsupportedSectionRecess
from draftwright.sheet_emit import emit_sheet_script

_CORPUS = Path(__file__).parent / "fixtures/evaluation/corpus-pockets-v1.json"
_CASES = json.loads(_CORPUS.read_text())["cases"]


def _parameters(feature):
    return {
        "width": feature.width,
        "length": feature.length,
        "depth": feature.depth,
        "edge_anchored": feature.edge_anchored,
    }


@pytest.fixture(scope="module", params=_CASES, ids=lambda case: case["id"])
def pocket_case(request):
    case = request.param
    path = _CORPUS.parent / case["fixture"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == case["sha256"]
    part = import_step(path)
    result = quiddity.build_section_recess_document(part).to_dict()
    converted = []
    refused = []
    for source in result["occurrences"]:
        try:
            converted.append(
                _convert_section_recess_pocket(source, schema_version=result["schema_version"])
            )
        except UnsupportedSectionRecess:
            refused.append(source)
    assert not result["refusals"]
    return case, path, part, result, converted, refused


def test_released_pockets_match_the_independently_authored_corpus(pocket_case):
    case, _path, _part, _result, converted, refused = pocket_case
    assert len(converted) == len(case["facts"])
    assert len(refused) == (1 if case["id"] == "pocket-prismatic-owner-negative" else 0)
    remaining = list(converted)
    for fact in case["facts"]:
        expected = fact["identity"]
        matches = [
            feature
            for feature in remaining
            if feature.frame.origin == pytest.approx(expected["location"]["value"], abs=1e-6)
        ]
        assert len(matches) == 1
        feature = matches[0]
        remaining.remove(feature)
        assert feature.frame.axis == expected["depth_axis"]["value"]
        assert feature.long_axis == expected["long_axis"]["value"]
        assert feature.width_axis == expected["width_axis"]["value"]
        assert feature.open_sign == expected["open_sign"]["value"]
        for name, value in _parameters(feature).items():
            assert value == fact["parameters"][name]["value"]
    assert remaining == []


def test_pocket_lowering_preserves_drawing_and_generated_sheet(pocket_case, tmp_path):
    case, path, _part, _result, converted, _refused = pocket_case
    before = build_drawing(path)
    model = before.model()
    old_pockets = [feature for feature in model.features if feature.kind == "pocket"]
    # Equality covers the entire consumer geometry, not merely parameter values. Ordering is
    # not a cross-provider promise; establish one-to-one equality before replacing each owner.
    assert len(old_pockets) == len(converted)
    remaining = list(converted)
    features = []
    for feature in model.features:
        if feature.kind == "pocket":
            match = next(candidate for candidate in remaining if candidate == feature)
            remaining.remove(match)
            features.append(match)
        else:
            features.append(feature)
    assert not remaining
    candidate = replace(model, features=features)
    after = build_drawing(path, model=candidate)
    delta = diff_builds(before, after)
    assert delta["dimensions_lost"] == {}
    assert delta["dimensions_gained"] == {}
    assert delta["dimensions_changed"] == {}
    assert delta["measurements_substituted"] == {}
    assert [(i.severity, i.code) for i in after.lint()] == [
        (i.severity, i.code) for i in before.lint()
    ]

    source = emit_sheet_script(
        candidate,
        f"from build123d import import_step\npart = import_step({str(path)!r})",
        str(tmp_path / case["id"]),
        title=case["id"],
        number="1471",
        formats=(),
    )
    namespace = {"__name__": "__migration_test__"}
    exec(compile(source, "<generated migration sheet>", "exec"), namespace)
    rebuilt = namespace["drawing"]
    generated = [feature for feature in rebuilt.model().features if feature.kind == "pocket"]
    assert sorted(generated, key=lambda feature: feature.frame.origin) == sorted(
        converted, key=lambda feature: feature.frame.origin
    )
    assert not [issue for issue in rebuilt.lint() if issue.code == "pocket_requirement_missing"]


def _record():
    """Independently specified 30 x 12 x 6 pocket opening toward +Z."""
    return {
        "index": 0,
        "body": 0,
        "geometry": {
            "type": "section_recess",
            "frame": {
                "origin": [22.0, -11.0, 0.0],
                "run": [0.0, 0.0, 1.0],
                "u": [1.0, 0.0, 0.0],
                "v": [0.0, 1.0, 0.0],
            },
            "run_interval": [4.0, 10.0],
            "profile": {
                "closure": "closed",
                "boundary": [
                    {"point": [-15.0, -6.0], "bulge": 0.0},
                    {"point": [15.0, -6.0], "bulge": 0.0},
                    {"point": [15.0, 6.0], "bulge": 0.0},
                    {"point": [-15.0, 6.0], "bulge": 0.0},
                ],
            },
            "ends": {
                "low": {"condition": "capped", "gradient": [0.0, 0.0]},
                "high": {"condition": "open", "gradient": [0.0, 0.0]},
            },
        },
        "classification": {"feature_kind": "pocket", "section_shape": "rectangular"},
        "evidence": {"defining_faces": [0, 1, 2, 3], "constituent_faces": [0, 1, 2, 3, 4]},
    }


def test_migration_corpus_keeps_its_independent_denominator():
    assert len(_CASES) == 10
    assert sum(len(case["facts"]) for case in _CASES) == 13


def _change(record, path, value):
    target = record
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("index",), True),
        (("body",), -1),
        (("evidence", "defining_faces"), []),
        (("evidence", "defining_faces"), [99]),
        (("evidence", "constituent_faces"), [1, 0]),
        (("evidence", "constituent_faces"), [False, 1, 2, 3]),
        (("geometry", "type"), "pocket"),
        (("geometry", "frame", "origin"), [0, 0]),
        (("geometry", "frame", "origin", 0), True),
        (("geometry", "frame", "origin", 0), float("nan")),
        (("geometry", "frame", "origin", 0), 10**1000),
        (("geometry", "frame", "u"), [0, 0, 1]),
        (("geometry", "frame", "v"), [0, -1, 0]),
        (("geometry", "run_interval"), [10, 4]),
        (("geometry", "ends", "high", "condition"), "capped"),
        (("geometry", "profile", "closure"), "open"),
        (("geometry", "profile", "boundary", 0), {"point": [0, 0]}),
        (("geometry", "profile", "boundary", 0, "bulge"), "0"),
        (("geometry", "profile", "boundary", 0, "point"), [0, float("inf")]),
    ],
)
def test_malformed_geometry_cannot_enter_the_ir(path, value):
    record = _record()
    assert _convert_section_recess_pocket(record, schema_version=2).width == 12
    _change(record, path, value)
    with pytest.raises(ValueError):
        _convert_section_recess_pocket(record, schema_version=2)


@pytest.mark.parametrize("version", [1, 3, True, 2.0, "2"])
def test_only_the_released_schema_is_admitted(version):
    with pytest.raises(ValueError, match="schema"):
        _convert_section_recess_pocket(_record(), schema_version=version)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("classification", "section_shape"), "obround"),
        (("geometry", "frame", "u"), [0.8, 0.6, 0]),
        (("geometry", "ends", "low", "gradient"), [0.1, 0]),
        (("geometry", "profile", "boundary", 0, "bulge"), 1.0),
        (("geometry", "profile", "boundary", 0, "point"), [-10, 0]),
        (("geometry", "profile", "boundary"), []),
    ],
)
def test_unsupported_sections_are_not_replaced_by_bounding_rectangles(path, value):
    record = _record()
    _change(record, path, value)
    with pytest.raises(UnsupportedSectionRecess):
        _convert_section_recess_pocket(record, schema_version=2)


def test_negative_run_and_reversed_profile_keep_the_physical_pocket():
    record = _record()
    expected = _convert_section_recess_pocket(record, schema_version=2)
    frame = record["geometry"]["frame"]
    frame["run"] = [0, 0, -1]
    frame["v"] = [0, -1, 0]
    record["geometry"]["run_interval"] = [-10, -4]
    record["geometry"]["ends"] = {
        "low": {"condition": "open", "gradient": [0, 0]},
        "high": {"condition": "capped", "gradient": [0, 0]},
    }
    for vertex in record["geometry"]["profile"]["boundary"]:
        vertex["point"][1] *= -1
    assert _convert_section_recess_pocket(record, schema_version=2) == expected


def test_crossing_vertex_order_is_refused():
    record = _record()
    boundary = record["geometry"]["profile"]["boundary"]
    boundary[1], boundary[2] = boundary[2], boundary[1]
    with pytest.raises(UnsupportedSectionRecess, match="diagonal"):
        _convert_section_recess_pocket(record, schema_version=2)


def test_adapter_does_not_mutate_or_retain_provider_json():
    record = _record()
    original = copy.deepcopy(record)
    feature = _convert_section_recess_pocket(record, schema_version=2)
    assert record == original
    record["geometry"]["run_interval"][1] = 100
    assert feature.depth == 6
    assert feature.frame.origin == (22, -11, 7)


def _open_record(*, corner=True):
    record = _record()
    record["classification"] = {"feature_kind": "edge_open_recess", "section_shape": "polygonal"}
    profile = record["geometry"]["profile"]
    profile["closure"] = "open"
    if corner:
        profile["boundary"].pop()
    profile["opening"] = [profile["boundary"][-1]["point"], profile["boundary"][0]["point"]]
    return record


@pytest.mark.parametrize("corner", [True, False])
def test_open_corner_and_edge_keep_only_the_existing_pocket_measurements(corner):
    feature = _convert_section_recess_pocket(_open_record(corner=corner), schema_version=2)
    assert _parameters(feature) == {"width": 12, "length": 30, "depth": 6, "edge_anchored": True}
    assert feature.frame.origin == (22, -11, 7)
    assert feature.open_sign == 1


@pytest.mark.parametrize("opening", [[], [[0, 0], [1, 1]]])
def test_opening_must_reference_the_observed_loose_endpoints(opening):
    record = _open_record()
    record["geometry"]["profile"]["opening"] = opening
    with pytest.raises(ValueError, match="loose endpoints"):
        _convert_section_recess_pocket(record, schema_version=2)


def test_zero_section_and_unrepresentable_world_extents_are_refused():
    record = _record()
    for vertex in record["geometry"]["profile"]["boundary"]:
        vertex["point"][0] = 0
    with pytest.raises(ValueError, match="positive extents"):
        _convert_section_recess_pocket(record, schema_version=2)
    record = _record()
    record["geometry"]["frame"]["origin"][0] = 1e308
    with pytest.raises(ValueError, match="remain positive"):
        _convert_section_recess_pocket(record, schema_version=2)
    record = _record()
    record["geometry"]["run_interval"] = [-1e308, 1e308]
    with pytest.raises(ValueError, match="must be finite"):
        _convert_section_recess_pocket(record, schema_version=2)


def test_lowering_an_existing_document_does_not_recognise_again(monkeypatch):
    import quiddity

    import draftwright.model.detect as detect

    def forbidden(*args, **kwargs):
        pytest.fail("the adapter must only project its supplied document")

    monkeypatch.setattr(quiddity, "build_section_recess_document", forbidden)
    monkeypatch.setattr(quiddity, "build_raw_recognition_result", forbidden)
    monkeypatch.setattr(quiddity, "build_raw_recognition_result", forbidden)
    monkeypatch.setattr(detect, "build_recognition_evidence", forbidden)
    assert _convert_section_recess_pocket(_record(), schema_version=2).depth == 6


def test_declared_omission_still_withholds_the_whole_pocket_callout():
    part = Box(100, 60, 20) - Pos(22, -11, 7) * Box(30, 12, 6)
    feature = _convert_section_recess_pocket(_record(), schema_version=2)

    def callouts(parameters):
        sheet = Sheet(part)
        sheet.authored_dimensions()
        handle = sheet.pocket(
            **_parameters(feature),
            long_axis=feature.long_axis,
            width_axis=feature.width_axis,
            depth_axis=feature.frame.axis,
            at=feature.frame.origin,
            w_center=feature.w_center,
            lo=feature.lo,
            hi=feature.hi,
            open_sign=feature.open_sign,
        )
        for parameter in parameters:
            sheet.dimension(handle, parameter)
        drawing = sheet.build()
        return [name for name in drawing.annotations() if name.startswith("m_pocket_")]

    assert (
        len(callouts(("pocket_width.length", "pocket_length.length", "pocket_depth.length"))) == 1
    )
    assert callouts(("pocket_width.length",)) == []


@pytest.mark.parametrize("axis", [0, 1, 2])
def test_translation_must_not_collapse_any_world_extent(axis):
    record = _record()
    record["geometry"]["frame"]["origin"][axis] = 1e308
    # Adding the finite local extent cannot change this floating-point coordinate.
    assert 1e308 + 30 == 1e308
    with pytest.raises(ValueError, match="remain positive"):
        _convert_section_recess_pocket(record, schema_version=2)
