"""Pinned real-frame authoring, physical membership and production export canary."""

import hashlib
import json
import runpy
from pathlib import Path

import pytest
from build123d import Plane, import_step, section

from draftwright.model.ir import HoleFeature, PatternFeature

pytestmark = [pytest.mark.slow, pytest.mark.real_part_canary, pytest.mark.timeout(120)]
ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "tests/fixtures/whistle_frame_reference.step"
RECIPE = ROOT / "docs/examples/frame_document.py"


@pytest.fixture(scope="module")
def recipe():
    return runpy.run_path(str(RECIPE))


@pytest.fixture(scope="module")
def frame_document(recipe):
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == (
        "078d0abd52b618a1a9180bb27a162a8fd1b8ae1a74a52571adfdbf3914f23d71"
    )
    package, _sheets = recipe["declare_document"](SOURCE)
    return package, package.build()


def _bores(features):
    return [
        (feature, feature.member if isinstance(feature, PatternFeature) else feature)
        for feature in features
        if isinstance(feature, (HoleFeature, PatternFeature))
    ]


def test_frame_operations_and_actual_claims_keep_complete_physical_identity(frame_document):
    package, result = frame_document
    pairs = _bores(package.features)
    assert len(pairs) == 4
    expected = {"z": (2.4, True, 1.6, 6), "x": (2.4, False, 1.5, 3), "y": (1.1, True, 53.2, 1)}
    for axis, (diameter, through, depth, count) in expected.items():
        group = [(owner, bore) for owner, bore in pairs if owner.frame.axis == axis]
        assert sum(owner.count for owner, _bore in group) == count
        for owner, bore in group:
            assert bore.frame.axis == axis
            assert bore.diameter == pytest.approx(diameter)
            assert bore.depth == pytest.approx(depth)
            assert bore.through is through
            assert len(owner.members) == owner.count
            assert all(
                getattr(bore, field) is None
                for field in (
                    "cbore",
                    "spotface",
                    "csink",
                    "thread",
                    "profile",
                    "across_flats",
                    "profile_direction",
                )
            )
    assert sorted(owner.count for owner, _ in pairs if owner.frame.axis == "z") == [2, 4]
    members = {
        axis: sorted(
            point for owner, _bore in pairs if owner.frame.axis == axis for point in owner.members
        )
        for axis in "xyz"
    }
    assert members["x"] == pytest.approx(
        [(-9.8, 11.0, 13.7), (-9.8, 37.85, 13.7), (-9.8, 53.595, 13.7)]
    )
    assert members["y"] == pytest.approx([(-11.2, 58.895, 11.1)])
    assert members["z"] == pytest.approx(
        [
            (-12.15, 0.0, 3.5),
            (-12.15, 24.375, 3.5),
            (-12.15, 64.595, 3.5),
            (12.15, 0.0, 3.5),
            (12.15, 24.375, 3.5),
            (12.15, 64.595, 3.5),
        ]
    )
    drawing = result.sheets["features"]
    claims = drawing.measurement_snapshot().claims
    for owner, bore in pairs:
        (claim,) = [c for c in claims if c.owner is owner and c.parameter == "bore.diameter"]
        assert claim.meaning[0][0] == pytest.approx(bore.diameter)
        assert ("THRU" in str(claim.rendered)) is bore.through
    (socket_owner,) = [owner for owner, _bore in pairs if owner.frame.axis == "x"]
    (depth,) = [c for c in claims if c.owner is socket_owner and c.parameter == "bore.depth"]
    assert depth.meaning[0][0] == pytest.approx(1.5)
    cells = drawing.registry.cells_of("hole_locations")
    assert len(cells) == 18  # nine member locations, two transverse components each
    cell_claims = [claim for claim in claims if claim.cell is not None]
    assert len(cell_claims) == 18
    assert {(c.owner, c.parameter) for c in cell_claims} == {
        (cell.measurement.feature, cell.measurement.parameter) for cell in cells
    }

    _assert_location_values(result.report())
    general_claims = result.sheets["general"].measurement_snapshot().claims
    expected_envelope = {
        "width.length": (31.3, "31.3"),
        "depth.length": (71.595, "71.6"),
        "height.length": (15.3, "15.3"),
    }
    envelope_claims = [claim for claim in general_claims if claim.owner.kind == "envelope"]
    assert {claim.parameter for claim in envelope_claims} == set(expected_envelope)
    assert len(envelope_claims) == 3
    for claim in envelope_claims:
        value, label = expected_envelope[claim.parameter]
        assert claim.meaning[0][0] == pytest.approx(value)
        assert claim.rendered[0] == label


def _assert_location_values(report):
    # Independent input facts: datum planes are X=-15.65, Y=-3.5, Z=0.
    expected = {}
    for x, x_value in ((-12.15, (3.5, "3.5")), (12.15, (27.8, "27.8"))):
        for y, y_value in (
            (0.0, (3.5, "3.5")),
            (24.375, (27.875, "27.9")),
            (64.595, (68.095, "68.1")),
        ):
            point = (x, y, 3.5)
            expected[(point, "x")] = x_value
            expected[(point, "y")] = y_value
    for y, value, label in (
        (11.0, 14.5, "14.5"),
        (37.85, 41.35, "41.4"),
        (53.595, 57.095, "57.1"),
    ):
        point = (-9.8, y, 13.7)
        expected[(point, "y")] = (value, label)
        expected[(point, "z")] = (13.7, "13.7")
    observed = set()
    for claim in report["claims"]:
        if claim["cell"] is None:
            continue
        meaning = claim["meaning"]
        component = "xyz".index(meaning["discriminator"])
        assert meaning["span"][0][component] == pytest.approx((-15.65, -3.5, 0)[component])
        key = (tuple(meaning["span"][1]), meaning["discriminator"])
        assert key in expected and key not in observed
        observed.add(key)
        value, label = expected[key]
        assert meaning["value"] == pytest.approx(value)
        assert claim["rendered"][0] == label
        assert claim["rendered"][1] == f"{meaning['discriminator'].upper()} distance (mm)"
    assert observed == set(expected)


def _membership(report):
    return tuple(
        (
            row["id"],
            row["family"],
            tuple(row["occurrence_ids"]),
            tuple(row["owner_ids"]),
            row["parameter_id"],
            row["requirement_count"],
        )
        for row in report["recognition"]["requirements"]
    )


def test_frame_preserves_unsupported_pockets_and_uncredited_segment_note(frame_document, recipe):
    package, result = frame_document
    report = result.report()
    rows = report["recognition"]["requirements"]
    assert len(rows) == 58
    pockets = [row for row in rows if row["family"] == "section_recesses"]
    assert len(pockets) == 6
    assert len({tuple(row["occurrence_ids"]) for row in pockets}) == 6
    assert all(row["requirement_count"] == 1 and row["coverage_credit"] == 0 for row in pockets)
    assert all(
        all(local["state"] == "unsupported" for local in row["local_outcomes"]) for row in pockets
    )
    drawing = result.sheets["features"]
    (hinge,) = [owner for owner, _bore in _bores(package.features) if owner.frame.axis == "y"]
    notes = [
        feature
        for feature in drawing.model().features
        if feature.kind == "note" and feature.text == recipe["SEGMENT_NOTE"]
    ]
    assert len(notes) == 1 and notes[0].origin is hinge and not notes[0].satisfies
    assert hinge.count == 1
    placed_notes = [
        annotation
        for annotation in drawing.annotations_of(hinge).values()
        if getattr(annotation, "pdf_text", None) == recipe["SEGMENT_NOTE"]
    ]
    assert len(placed_notes) == 1
    assert not any(
        c.owner is hinge and c.parameter.startswith("location")
        for c in drawing.measurement_snapshot().claims
    )
    assert report["assessment"]["manufacturing"]["readiness"] == "not-certified"
    assert report["assessment"]["fidelity"]["unknown_claims"]


def test_frame_measurement_moves_between_sheets_without_changing_requirements(recipe):
    package, sheets = recipe["declare_document"](SOURCE)
    (hinge,) = [owner for owner, _bore in _bores(package.features) if owner.frame.axis == "y"]
    # Both source declarations permit this measurement; live edits choose its current sheet.
    sheets["general"].dimension(hinge, "bore.diameter")
    result = package.build()
    general, features = result.sheets["general"], result.sheets["features"]

    def diameter_names(drawing):
        return {
            claim.annotation
            for claim in drawing.measurement_snapshot().claims
            if claim.owner is hinge and claim.parameter == "bore.diameter"
        }

    for name in diameter_names(general):
        general.remove(name)
    baseline = result.report()
    (claim,) = [
        c
        for c in baseline["claims"]
        if c["parameter_id"] == "bore.diameter" and c["meaning"]["value"] == pytest.approx(1.1)
    ]
    owner_id = claim["owner_id"]

    def target(report):
        return next(
            row
            for row in report["recognition"]["requirements"]
            if row["parameter_id"] == "bore.diameter" and owner_id in row["owner_ids"]
        )

    assert target(baseline)["coverage_credit"] == 1
    general.callout(hinge)
    assert diameter_names(general)
    for name in diameter_names(features):
        features.remove(name)
    moved = result.report()
    assert _membership(moved) == _membership(baseline)
    assert target(moved)["coverage_credit"] == 1
    assert {ref["sheet_id"] for ref in target(moved)["carrying_annotations"]} == {"sheet:1"}
    for name in diameter_names(general):
        general.remove(name)
    removed = result.report()
    assert _membership(removed) == _membership(baseline)
    assert target(removed)["coverage_credit"] == 0
    assert not target(removed)["carrying_annotations"]


def test_frame_section_material_and_arrows_match_the_real_cut(frame_document):
    _package, result = frame_document
    drawing = result.sheets["general"]
    assert set(drawing.views) == {"front", "plan", "side", "section_aa"}
    assert drawing.section_decision["status"] == "placed"
    expected = section(import_step(SOURCE), section_by=Plane.XZ)
    assert len(expected.faces()) == 3
    assert expected.area == pytest.approx(101.67622552458516, abs=1e-7)
    origin = drawing.at("section_aa", 0, 0, 0)
    px = drawing.at("section_aa", 1, 0, 0)
    pz = drawing.at("section_aa", 0, 0, 1)
    scale_x, scale_z = px[0] - origin[0], pz[1] - origin[1]
    points = [
        ((point.X - origin[0]) / scale_x, 0, (point.Y - origin[1]) / scale_z)
        for edge in drawing.get_annotation("section_hatch").edges()
        for point in (edge.position_at(0.25), edge.position_at(0.5), edge.position_at(0.75))
    ]
    assert points
    assert all(any(face.is_inside(point) for face in expected.faces()) for point in points)
    assert all(any(face.is_inside(point) for point in points) for face in expected.faces())
    line_y = drawing.get_annotation("section_line").bounding_box().center().Y
    for side in ("left", "right"):
        arrow = drawing.get_annotation(f"section_arrow_{side}")
        wing = drawing.get_annotation(f"section_wing_{side}")
        assert arrow.bounding_box().max.Y == pytest.approx(line_y)
        assert wing.bounding_box().max.Y < line_y
        ys = [vertex.Y for vertex in arrow.vertices()]
        assert sum(abs(y - max(ys)) < 1e-7 for y in ys) == 1


def test_frame_production_exports_retain_verified_cells_and_visible_diagnostics(
    frame_document, recipe, tmp_path
):
    _package, result = frame_document
    before = result.report()
    source_alias = tmp_path / SOURCE.name
    source_alias.write_text("SOURCE PATH REPLACED AFTER THE DOCUMENT SNAPSHOT")
    manifest = recipe["export_package"](result, source_alias, tmp_path / "package")
    assert manifest["source_sha256"] == before["source"]["sha256"]
    assert manifest["source"] == before["source"]
    assert hashlib.sha256(source_alias.read_bytes()).hexdigest() != manifest["source_sha256"]
    after = result.report()
    assert _membership(before) == _membership(after)
    assert before["claims"] == after["claims"]
    for name, paths in manifest["exports"].items():
        assert set(paths) == {"pdf", "svg", "png"}
        assert all(Path(path).stat().st_size > 1000 for path in paths.values())
        issues = result.sheets[name].lint()
        assert not any(issue.severity == "error" for issue in issues)
        assert not any(
            issue.code in {"title_field_overflow", "table_dropped", "section_dropped"}
            for issue in issues
        )
        assert (
            sum(issue.code == "prismatic_pocket_requirement_unsupported" for issue in issues) == 6
        )
    saved = json.loads((tmp_path / "package/document.json").read_text())
    assert _membership(saved) == _membership(after)
    assert saved["claims"] == after["claims"]


def test_numeric_descriptive_pocket_table_cannot_erase_unsupported_obligations(frame_document):
    _package, result = frame_document
    general = result.sheets["general"]
    before = result.report()
    original = general.get_annotation("pocket_schedule").table_rows
    general.remove("pocket_schedule")
    try:
        table = general.add_table(
            [
                ["Pocket", "Qty", "Unverified test input"],
                ["Hex", "6", "AF 4.8 / DEPTH 2.4 - NOT ENGINEERING APPROVAL"],
            ],
            name="pocket_schedule",
        )
        assert table is not None
        after = result.report()
        assert _membership(after) == _membership(before)
        pockets = [
            row
            for row in after["recognition"]["requirements"]
            if row["family"] == "section_recesses"
        ]
        assert len(pockets) == 6 and all(row["coverage_credit"] == 0 for row in pockets)
        assert all(
            all(local["state"] == "unsupported" for local in row["local_outcomes"])
            for row in pockets
        )
        assert not any(claim["annotation"] == "pocket_schedule" for claim in after["claims"])
        assert not general.registry.measurement_of("pocket_schedule")
    finally:
        if "pocket_schedule" in general.registry:
            general.remove("pocket_schedule")
        general.add_table(original, name="pocket_schedule")


def test_export_refuses_a_document_with_different_captured_source(recipe, tmp_path):
    from types import SimpleNamespace

    changed = SimpleNamespace(report=lambda: {"source": {"sha256": "different"}})
    output = tmp_path / "must-not-be-published"
    with pytest.raises(ValueError, match="retained document source"):
        recipe["export_package"](changed, SOURCE, output)
    assert not output.exists()
