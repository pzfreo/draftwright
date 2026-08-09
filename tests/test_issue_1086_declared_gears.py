"""#1086: declared gear semantics stay authored, typed, and auditable."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from build123d import Cylinder

from draftwright import Sheet
from draftwright.annotations.gears import gear_table_rows, render_gear_tables
from draftwright.linting.gear_coverage import lint_declared_gear_coverage
from draftwright.model import ExternalSpurGearFeature, Frame
from draftwright.sheet_emit import emit_sheet_script


def _gear(**changes) -> ExternalSpurGearFeature:
    values = {
        "frame": Frame((0.0, 0.0, 5.0), "z"),
        "tooth_count": 13,
        "module": 1.25,
        "pressure_angle": 20.0,
        "profile_shift": 0.0,
        "face_width": 10.0,
        "tooth_thickness": 1.9634954084936207,
        "tooth_thickness_tolerance": (-0.03, 0.01),
        "flank_tolerance_class": 7,
    }
    values.update(changes)
    return ExternalSpurGearFeature(**values)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"tooth_count": 4}, "tooth_count"),
        ({"tooth_count": 1001}, "tooth_count"),
        ({"tooth_count": True}, "tooth_count"),
        ({"module": 0}, "module"),
        ({"module": 0.49}, "module"),
        ({"module": 71}, "module"),
        ({"module": True}, "module"),
        ({"pressure_angle": "20"}, "pressure_angle"),
        ({"profile_shift": "0"}, "profile_shift"),
        ({"profile_shift": float("nan")}, "profile_shift"),
        ({"frame": Frame((0, float("nan"), 5), "z")}, "origin"),
        ({"pressure_angle": 90}, "pressure_angle"),
        ({"face_width": 3.9}, "face_width"),
        ({"face_width": 1201}, "face_width"),
        ({"face_width": float("nan")}, "face_width"),
        ({"tooth_thickness": 0}, "tooth_thickness"),
        ({"tooth_thickness_tolerance": (0,)}, "tolerance"),
        ({"tooth_thickness_tolerance": (float("nan"), 0)}, "tolerance"),
        ({"tooth_thickness_tolerance": (0.02, -0.01)}, "tolerance"),
        ({"tooth_thickness_tolerance": (-2, 0)}, "positive lower limit"),
        ({"flank_tolerance_class": True}, "flank_tolerance_class"),
        ({"flank_tolerance_class": 12}, "flank_tolerance_class"),
        ({"tooth_count": 5, "module": 0.5}, "reference diameter"),
        ({"tooth_count": 1000, "module": 70}, "reference diameter"),
    ],
)
def test_external_spur_gear_ir_rejects_incomplete_or_invalid_requirements(changes, message):
    with pytest.raises(ValueError, match=message):
        _gear(**changes)


def test_sheet_declaration_places_one_solver_owned_standard_table():
    sheet = Sheet(Cylinder(8, 10))
    sheet.external_spur_gear(
        at=(0, 0, 5),
        axis="z",
        tooth_count=13,
        module=1.25,
        pressure_angle=20,
        profile_shift=0,
        face_width=10,
        tooth_thickness=1.9634954084936207,
        tooth_thickness_tolerance=(-0.03, 0.01),
        flank_tolerance_class=7,
    )
    sheet.authored_dimensions()

    drawing = sheet.build()
    (feature,) = [f for f in drawing.model().features if f.kind == "external_spur_gear"]
    names = drawing.registry.names_for_feature(feature)

    assert sheet.features[0] == feature
    assert names == ["gear_data_1"]
    assert drawing.registry.named(names[0]).gear_requirement_values == feature.requirement_values
    assert drawing.registry.named(names[0]).gear_requirement_rows == gear_table_rows(feature)
    assert ("MODULE m", "1.25 mm", "ISO 54:1996") in gear_table_rows(feature)
    assert (
        "TOOTH THICKNESS s",
        "1.9634954084936207 +0.01/-0.03 mm",
        "ISO 21771-2:2025",
    ) in gear_table_rows(feature)
    placement_codes = [issue.code for issue in drawing.lint(physical=False)]
    assert not {
        "table_dropped",
        "annotation_overlap",
        "view_annotation_overlap",
        "annotation_out_of_bounds",
    } & set(placement_codes)
    physical_codes = [issue.code for issue in drawing.lint()]
    assert "gear_requirement_mismatch" not in physical_codes
    assert "gear_correspondence_unverifiable" in physical_codes


def test_standard_table_survives_vector_export(tmp_path):
    sheet = Sheet(Cylinder(8, 10))
    sheet.external_spur_gear(
        at=(0, 0, 5),
        axis="z",
        tooth_count=13,
        module=1.25,
        pressure_angle=20,
        profile_shift=0,
        face_width=10,
        tooth_thickness=1.9634954084936207,
        tooth_thickness_tolerance=(-0.03, 0.01),
        flank_tolerance_class=7,
    )
    sheet.authored_dimensions()

    paths = sheet.build().export(str(tmp_path / "declared-gear"), formats=("svg", "dxf"))

    assert set(paths) == {"svg", "dxf"}
    assert all(Path(path).exists() and Path(path).stat().st_size > 0 for path in paths.values())


def test_gear_table_uses_a_free_name_and_dropped_table_never_acquires_provenance():
    class _Registry:
        def __init__(self):
            self.names = {"gear_data_1"}
            self.reapplied = []

        def __contains__(self, name):
            return name in self.names

        def reapply(self, name, identity):
            self.reapplied.append((name, identity))

    class _Drawing:
        def __init__(self, *, drop=False):
            self.registry = _Registry()
            self.drop = drop
            self.requested_names = []

        def add_table(self, _rows, *, name, prefer):
            self.requested_names.append((name, prefer))
            if self.drop:
                return None
            return type("_Table", (), {})()

    model = type("_Model", (), {"features": [_gear()]})()
    placed = _Drawing()
    dropped = _Drawing(drop=True)

    assert render_gear_tables(placed, model) == 1
    assert placed.requested_names == [("gear_data_1_1", "tr")]
    assert placed.registry.reapplied[0][0] == "gear_data_1_1"
    assert render_gear_tables(dropped, model) == 0
    assert dropped.registry.reapplied == []


def test_removed_gear_table_is_a_semantic_coverage_failure():
    sheet = Sheet(Cylinder(8, 10))
    sheet.external_spur_gear(
        at=(0, 0, 5),
        axis="z",
        tooth_count=13,
        module=1.25,
        pressure_angle=20,
        profile_shift=0,
        face_width=10,
        tooth_thickness=1.9634954084936207,
        tooth_thickness_tolerance=(-0.03, 0.01),
        flank_tolerance_class=7,
    )
    sheet.authored_dimensions()
    drawing = sheet.build()

    drawing.remove("gear_data_1")

    assert "gear_requirement_missing" in [issue.code for issue in drawing.lint()]


def test_sheet_script_preserves_every_source_authored_gear_value_exactly():
    feature = _gear()
    sheet = Sheet(Cylinder(8, 10))
    sheet.add(feature)
    sheet.authored_dimensions()
    model = sheet.model()

    source = emit_sheet_script(model, "part = PART", "drawing", title="T", number="N")

    assert (
        'sheet.external_spur_gear(at=(0, 0, 5), axis="z", tooth_count=13, '
        "module=1.25, pressure_angle=20, profile_shift=0, face_width=10, "
        "tooth_thickness=1.9634954084936207, "
        "tooth_thickness_tolerance=(-0.03, 0.01), flank_tolerance_class=7)"
    ) in source

    emitted_line = next(
        line for line in source.splitlines() if " = sheet.external_spur_gear(" in line
    ).split("#", 1)[0]
    regenerated = Sheet(Cylinder(8, 10))
    exec(emitted_line, {"sheet": regenerated})  # noqa: S102 — generated Python is the product
    regenerated.authored_dimensions()

    assert regenerated.model().features == [feature]


@dataclass(frozen=True)
class _Profile:
    axis: str
    centre: tuple[float, float, float]
    span: tuple[float, float]
    repeat_count: int


def _coverage(*, features=(), profiles=()):
    return lint_declared_gear_coverage(
        features=features,
        registry=None,
        profiles=profiles,
        assembly=False,
    )


def test_repeating_profile_without_declared_semantics_is_incomplete():
    issues = _coverage(profiles=(_Profile("z", (0, 0, 5), (0, 10), 13),))

    assert [issue.code for issue in issues] == ["gear_semantics_missing"]


def test_declared_repeat_count_mismatch_is_not_lint_clean():
    issues = _coverage(
        features=(_gear(),),
        profiles=(_Profile("z", (0, 0, 5), (0, 10), 12),),
    )

    assert "gear_repeat_count_mismatch" in [issue.code for issue in issues]


def test_declared_axis_mismatch_is_not_lint_clean():
    issues = _coverage(
        features=(_gear(),),
        profiles=(_Profile("x", (0, 0, 5), (0, 10), 13),),
    )

    assert "gear_axis_mismatch" in [issue.code for issue in issues]


@pytest.mark.parametrize(
    "profiles",
    [
        (),
        (
            _Profile("z", (0, 0, 5), (0, 10), 13),
            _Profile("z", (0, 0, 5), (0, 10), 13),
        ),
    ],
)
def test_absent_or_ambiguous_correspondence_is_not_lint_clean(profiles):
    issues = _coverage(features=(_gear(),), profiles=profiles)

    assert [issue.code for issue in issues] == ["gear_correspondence_unverifiable"]


def test_normative_parameter_mutation_cannot_leave_a_stale_table_clean():
    feature = _gear()

    class _Table:
        gear_requirement_values = feature.requirement_values
        gear_requirement_rows = gear_table_rows(feature)

    class _Registry:
        def names_for_feature(self, _feature):
            return ["gear_data_1"]

        def named(self, _name):
            return _Table()

    mutated = replace(feature, pressure_angle=25.0)
    issues = lint_declared_gear_coverage(
        features=(mutated,), registry=_Registry(), profiles=None, assembly=False
    )

    assert "gear_requirement_mismatch" in [issue.code for issue in issues]
