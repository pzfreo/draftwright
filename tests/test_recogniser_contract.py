"""ADR 0013 recogniser-contract tests.

Enforces the uniform contract mechanically (epic #584 WP3):

- **Immutable records** — every recogniser returns frozen dataclasses.
- **Uniform serialization** — each public record has ``.to_dict()`` that yields a
  *JSON-serializable* nested dict. This is the invariant with
  teeth: a leaked build123d / OCP object would make ``json.dumps`` raise, so the
  test proves the "geometry-only records, no build123d type leaks out" rule.
- **Signature shape** — a part-based recogniser takes ``part`` then keyword-only
  args; a derived recogniser takes a single positional inventory.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
from pathlib import Path

import pytest
from _recogniser_public_contract import (
    public_recogniser_member,
    public_recogniser_names,
    public_record_universe,
)
from b123d_recognisers import (
    Blend,
    OrientedSlot,
    OrientedSlotArray,
    OrientedSlotGrid,
    analyse_cylinders,
    build_raw_recognition_result,
    project_step_shoulders,
    recognise_angled_steps,
    recognise_blends,
    recognise_bosses,
    recognise_chamfers,
    recognise_channels,
    recognise_circular_blind_steps,
    recognise_countersinks,
    recognise_double_d_bores,
    recognise_face_levels,
    recognise_fillets,
    recognise_flats,
    recognise_grooves,
    recognise_hole_patterns,
    recognise_holes,
    recognise_oriented_slot_patterns,
    recognise_oriented_slots,
    recognise_paired_ramp_steps,
    recognise_passages,
    recognise_plates,
    recognise_pocket_patterns,
    recognise_pockets,
    recognise_polygonal_bosses,
    recognise_polygonal_stock,
    recognise_prismatic_pockets,
    recognise_rectangular_blind_slots,
    recognise_rectangular_pads,
    recognise_repeating_radial_profiles,
    recognise_risers,
    recognise_round_bottom_blind_slots,
    recognise_section_passages,
    recognise_slot_patterns,
    recognise_slots,
    recognise_through_steps,
    recognise_turned_steps,
    step_level_records,
)
from build123d import (
    Align,
    Axis,
    Box,
    BuildLine,
    BuildPart,
    BuildSketch,
    Cylinder,
    Line,
    Plane,
    Polygon,
    Pos,
    RadiusArc,
    RegularPolygon,
    Rot,
    Vector,
    chamfer,
    extrude,
    fillet,
    import_step,
    make_face,
)

from draftwright import build_drawing


def _csk_plate():
    from build123d import Cone

    plate = Box(90, 60, 12)
    for x, y in [(-30, -15), (5, 12), (30, -8)]:
        plate -= Pos(x, y, 0) * Cylinder(3, 12)
        plate -= Pos(x, y, 4) * Cone(3, 7, 4)
    return plate


def _stepped():
    return Box(80, 40, 10) + Pos(-20, 0, 10) * Box(40, 40, 12)


def _double_d_plate():
    centre = (Align.CENTER, Align.CENTER, Align.CENTER)
    cutter = Cylinder(5, 20, align=centre) & Box(7.2, 20, 30, align=centre)
    return Box(30, 30, 10, align=centre) - cutter


def _turned_shaft():
    return Rot(0, 90, 0) * (Cylinder(10, 30) + Pos(0, 0, 20) * Cylinder(6, 10))


def _linear_array_plate():
    part = Box(120, 40, 10)
    for i in range(5):
        part -= Pos(-40 + i * 20, 0, 0) * Cylinder(3, 10)
    return part


def _grid_plate(nx=3, ny=3, px=25, py=25):
    part = Box(px * (nx + 1), py * (ny + 1), 10)
    for i in range(nx):
        for j in range(ny):
            part -= Pos((i - (nx - 1) / 2) * px, (j - (ny - 1) / 2) * py, 0) * Cylinder(3, 10)
    return part


def _pocket_array_plate():
    part = Box(30, 150, 20)
    for cy in (-45, -15, 15, 45):  # four identical blind pockets on one Y centreline, pitch 30
        part -= Pos(0, cy, 7) * Box(10, 12, 6)  # floored (opening +Z) → a Pocket, not a Slot
    return part


def _pocket_grid_plate():
    part = Box(140, 110, 20)
    for i in range(2):  # 2×3 lattice of identical blind pockets (rect_grid needs n>=6)
        for j in range(3):
            part -= Pos((i - 0.5) * 40, (j - 1) * 30, 7) * Box(8, 10, 6)
    return part


def _slot_array_plate():
    part = Box(60, 200, 20)
    for cy in (-45, -15, 15, 45):  # four identical THROUGH slots on one Y centreline, pitch 30
        part -= Pos(0, cy, 0) * Box(30, 8, 20)  # cutter spans the full Z → a Slot, not a Pocket
    return part


def _slot_grid_plate():
    part = Box(180, 130, 20)
    for i in range(2):  # 2×3 lattice of identical through slots (rect_grid needs n>=6)
        for j in range(3):
            part -= Pos((i - 0.5) * 44, (j - 1) * 34, 0) * Box(24, 8, 20)
    return part


def _rectangular_blind_slot_part():
    stock = Box(30, 20, 40, align=(Align.CENTER, Align.CENTER, Align.MIN))
    tool = Pos(0, 5, 0) * Box(
        10,
        5,
        20,
        align=(Align.CENTER, Align.MIN, Align.MIN),
    )
    return stock - tool


def _round_bottom_blind_slot_part():
    width, radius, length = 10.0, 3.0, 20.0
    half_width = width / 2
    half_flat = (width - 2 * radius) / 2
    with BuildLine() as boundary:
        Line((-half_width, 0), (half_width, 0))
        RadiusArc((half_width, 0), (half_flat, -radius), radius)
        Line((half_flat, -radius), (-half_flat, -radius))
        RadiusArc((-half_flat, -radius), (-half_width, 0), radius)
    with BuildSketch() as sketch:
        make_face(boundary.line)
    stock = Pos(0, -5, 0) * Box(30, 10, 40)
    tool = extrude(sketch.sketch, amount=length, dir=Vector(0, 0, 1))
    return stock - tool


def _bolt_circle_plate(n=6, r=30):
    from math import cos, radians, sin

    part = Box(100, 100, 12)
    for i in range(n):
        a = radians(360 / n * i + 15.0)
        part -= Pos(r * cos(a), r * sin(a), 0) * Cylinder(4, 12)
    return part


def _chamfered_box():
    box = Box(30, 30, 30)
    edge = box.edges().filter_by(Axis.Z).sort_by(Axis.X)[-1]
    return chamfer(edge, 3)


def _filleted_box():
    box = Box(30, 30, 30)
    edge = box.edges().filter_by(Axis.Z).sort_by(Axis.X)[-1]
    return fillet(edge, 3)


def _small_blended_box():
    box = Box(40, 30, 20)
    return fillet(list(box.edges().filter_by(Axis.Z)), 0.2)


def _oriented_slot_pattern(points, *, angle=30.0):
    part = Box(120, 90, 10)
    for x, y in points:
        part -= (
            Pos(x, y, 0)
            * Rot(0, 0, angle)
            * Box(
                24,
                6,
                20,
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
            )
        )
    return part


def _l_bracket():
    return Box(80, 40, 8) + Pos(-36, 0, 24) * Box(8, 40, 40)


def _polygonal_boss_plate():
    from build123d import RegularPolygon, extrude

    return Box(100, 80, 10) + Pos(0, 0, 5) * extrude(RegularPolygon(20, 6), 30)


def _angled_step_part():
    return import_step(
        str(Path(__file__).parent / "fixtures" / "issue_1247_angled_blind_step.step")
    )


def _circular_blind_step_part():
    return Box(40, 30, 20) - Pos(7.5, 15, 10) * Rot(0, 90, 0) * Cylinder(4, 25)


def _paired_ramp_step_part():
    profile = Polygon((0, -8), (0, 8), (-10, 0))
    cutter = Pos(20, 20, 0) * extrude(Plane.XZ * profile, 25)
    return Box(40, 40, 30) - cutter


def _through_step_part():
    return Box(40, 30, 20) - Pos(15, 10, 0) * Box(20, 20, 30)


def _hexagonal_passage_plate():
    plate = Box(120, 80, 20)
    with BuildPart() as tool:
        with BuildSketch(Plane.XY.offset(-20)):
            RegularPolygon(12, 6)
        extrude(amount=60)
    return plate - Pos(-30, 0, 0) * tool.part


def _hexagonal_pocket_plate():
    plate = Box(120, 80, 20)
    with BuildPart() as tool:
        with BuildSketch(Plane.XY.offset(4)):
            RegularPolygon(12, 6)
        extrude(amount=20)
    return plate - Pos(-30, 0, 0) * tool.part


def _polygonal_stock():
    return extrude(RegularPolygon(20, 6), 30)


def _raised_pad_plate():
    return Box(120, 90, 16) + Pos(0, -30, 10) * Box(30, 20, 4)


def _records_from_recognisers():
    """(name, record) pairs across every recogniser, on parts that actually trigger them."""
    csk = _csk_plate()
    stepped = _stepped()
    holes = recognise_holes(csk, csinks=recognise_countersinks(csk))
    slotted = Box(60, 40, 20) - Pos(0, 0, 0) * Box(30, 8, 20)
    pocketed = Box(60, 40, 20) - Pos(0, 0, 7) * Box(30, 18, 6)
    channel = (
        Box(50, 50, 12)
        + Pos(0, -18.75, 15) * Box(50, 12.5, 18)
        + Pos(0, 18.75, 15) * Box(50, 12.5, 18)
    )
    dshaft = Cylinder(10, 30) - Pos(10, 0, 0) * Box(10, 40, 40)  # round stock with one flat
    grooved = Cylinder(10, 40) - (Cylinder(10, 4) - Cylinder(8, 4))  # round stock with one groove
    levels = [f.z for f in recognise_face_levels(stepped)]
    oriented_array = recognise_oriented_slots(_oriented_slot_pattern(((-30, 0), (0, 0), (30, 0))))
    oriented_grid = recognise_oriented_slots(
        _oriented_slot_pattern(((-30, -20), (0, -20), (30, -20), (-30, 20), (0, 20), (30, 20)))
    )

    out: list[tuple[str, object]] = []
    for name, recs in [
        ("recognise_holes", holes),
        ("recognise_countersinks", recognise_countersinks(csk)),
        ("recognise_double_d_bores", recognise_double_d_bores(_double_d_plate())),
        ("recognise_bosses", recognise_bosses(Cylinder(10, 20))),
        ("recognise_polygonal_bosses", recognise_polygonal_bosses(_polygonal_boss_plate())),
        ("hole_patterns:bolt", recognise_hole_patterns(recognise_holes(_bolt_circle_plate()))),
        ("hole_patterns:linear", recognise_hole_patterns(recognise_holes(_linear_array_plate()))),
        ("hole_patterns:grid", recognise_hole_patterns(recognise_holes(_grid_plate()))),
        ("recognise_chamfers", recognise_chamfers(_chamfered_box())),
        ("recognise_blends", recognise_blends(_small_blended_box())),
        ("recognise_channels", recognise_channels(channel)),
        ("recognise_fillets", recognise_fillets(_filleted_box())),
        ("recognise_slots", recognise_slots(slotted)),
        ("recognise_oriented_slots", oriented_array),
        (
            "oriented_slot_patterns:linear",
            recognise_oriented_slot_patterns(oriented_array),
        ),
        (
            "oriented_slot_patterns:grid",
            recognise_oriented_slot_patterns(oriented_grid),
        ),
        (
            "recognise_rectangular_blind_slots",
            recognise_rectangular_blind_slots(_rectangular_blind_slot_part()),
        ),
        (
            "recognise_round_bottom_blind_slots",
            recognise_round_bottom_blind_slots(_round_bottom_blind_slot_part()),
        ),
        ("recognise_pockets", recognise_pockets(pocketed)),
        (
            "pocket_patterns:linear",
            recognise_pocket_patterns(recognise_pockets(_pocket_array_plate())),
        ),
        (
            "pocket_patterns:grid",
            recognise_pocket_patterns(recognise_pockets(_pocket_grid_plate())),
        ),
        (
            "slot_patterns:linear",
            recognise_slot_patterns(recognise_slots(_slot_array_plate())),
        ),
        (
            "slot_patterns:grid",
            recognise_slot_patterns(recognise_slots(_slot_grid_plate())),
        ),
        ("recognise_flats", recognise_flats(dshaft)),
        ("recognise_grooves", recognise_grooves(grooved)),
        ("recognise_plates", recognise_plates(_l_bracket())),
        ("recognise_face_levels", recognise_face_levels(stepped)),
        ("recognise_risers", recognise_risers(stepped)),
        ("step_level_records", step_level_records(stepped)),
        ("recognise_angled_steps", recognise_angled_steps(_angled_step_part())),
        (
            "recognise_circular_blind_steps",
            recognise_circular_blind_steps(_circular_blind_step_part()),
        ),
        (
            "recognise_paired_ramp_steps",
            recognise_paired_ramp_steps(_paired_ramp_step_part()),
        ),
        ("recognise_through_steps", recognise_through_steps(_through_step_part())),
        ("recognise_passages", recognise_passages(_hexagonal_passage_plate())),
        (
            "recognise_section_passages",
            recognise_section_passages(_hexagonal_passage_plate()),
        ),
        (
            "recognise_prismatic_pockets",
            recognise_prismatic_pockets(_hexagonal_pocket_plate()),
        ),
        ("recognise_polygonal_stock", recognise_polygonal_stock(_polygonal_stock())),
        ("recognise_rectangular_pads", recognise_rectangular_pads(_raised_pad_plate())),
        (
            "recognise_repeating_radial_profiles",
            recognise_repeating_radial_profiles(
                import_step(str(Path(__file__).parent / "fixtures" / "issue_1058_wheel_rh.step"))
            ),
        ),
        # `StepShoulder` stopped being a recogniser return in #1025 — it is now what
        # `project_step_shoulders` derives from riser evidence. It stays in this roster
        # because the record contract (frozen, JSON-serialisable, no leaked build123d object)
        # binds a projection's output exactly as it binds a recogniser's; dropping it would
        # retire the only coverage of that type on the grounds that it moved.
        (
            "project_step_shoulders",
            project_step_shoulders(recognise_risers(stepped), levels=levels),
        ),
        ("recognise_turned_steps", recognise_turned_steps(_turned_shaft())),
    ]:
        for r in recs:
            out.append((name, r))
    return out


def test_records_are_frozen_and_json_serializable():
    """Every result is a public, frozen, JSON-serializable record."""
    records = _records_from_recognisers()

    for name, rec in records:
        record_type = type(rec)
        type_name = record_type.__name__
        assert type_name in public_recogniser_names(), (
            f"{name}: {type_name} is not published in b123d_recognisers.__all__"
        )
        assert public_recogniser_member(type_name) is record_type, (
            f"{name}: root export {type_name} is not the returned record class"
        )
        assert dataclasses.is_dataclass(rec) and rec.__dataclass_params__.frozen, (
            f"{name}: {type_name} must be a frozen dataclass"
        )
        assert callable(getattr(rec, "to_dict", None)), f"{name}: {type_name} has no to_dict()"
        d = rec.to_dict()
        assert isinstance(d, dict)
        # The teeth: a leaked build123d/OCP object makes this raise.
        json.dumps(d)


def test_every_record_type_is_actually_exercised():
    """The drive-parts must emit *each* recogniser record type — no silent under-coverage.

    Guards against the count-only trap: a record type whose drive-part stops producing it
    (or a new record added without coverage) fails here instead of passing on a bare tally.
    """
    expected = public_record_universe()
    seen = {type(rec) for _, rec in _records_from_recognisers()}
    assert seen == expected, (
        "runtime contract roster disagrees with the mechanically derived public universe: "
        f"missing={sorted(t.__name__ for t in expected - seen)}, "
        f"unexpected={sorted(t.__name__ for t in seen - expected)}"
    )


def test_0410_round_bottom_slot_crosses_the_dedicated_consumer_path():
    drawing = build_drawing(_round_bottom_blind_slot_part())
    recognition = drawing.recognition()

    assert len(recognition.round_bottom_blind_slots) == 1
    assert not recognition.slots
    assert not recognition.pockets
    assert not {
        "slot",
        "slot_pattern",
        "pocket",
        "pocket_pattern",
    } & {feature.kind for feature in drawing.model().features}
    assert not [name for name in drawing.annotations() if name.startswith(("m_slot", "m_pocket"))]
    assert (
        len(
            [
                feature
                for feature in drawing.model().features
                if feature.kind == "round_bottom_blind_slot"
            ]
        )
        == 1
    )
    assert any(name.startswith("m_round_bottom_blind_slot") for name in drawing.annotations())

    completeness = drawing.lint_summary()["quality"]["completeness"]
    assert completeness["unscored_recognized_families"] == []
    assert completeness["requirements"] == 3
    assert completeness["audited_score"] == 1.0
    assert completeness["by_family"]["round_bottom_blind_slots"] == 3


@pytest.mark.parametrize(
    ("part", "inventory", "record_type", "count", "pattern_type", "unscored"),
    [
        (
            _small_blended_box(),
            "blends",
            Blend,
            4,
            None,
            ["blends"],
        ),
        (
            _oriented_slot_pattern(((-30, 0), (0, 0), (30, 0))),
            "oriented_slots",
            OrientedSlot,
            3,
            OrientedSlotArray,
            ["oriented_slot_patterns"],
        ),
        (
            _oriented_slot_pattern(
                ((-30, -20), (0, -20), (30, -20), (-30, 20), (0, 20), (30, 20))
            ),
            "oriented_slots",
            OrientedSlot,
            6,
            OrientedSlotGrid,
            ["oriented_slot_patterns"],
        ),
    ],
    ids=("blend", "oriented-linear", "oriented-grid"),
)
def test_0412_deferred_families_cross_the_aggregate_without_duplicate_ownership(
    part,
    inventory,
    record_type,
    count,
    pattern_type,
    unscored,
):
    """The provider aggregate and Drawing keep deferred occurrences explicit and unscored."""
    raw = build_raw_recognition_result(part)
    drawing = build_drawing(part)
    recognition = drawing.recognition()

    for result in (raw, recognition):
        records = getattr(result, inventory)
        assert len(records) == count
        assert {type(record) for record in records} == {record_type}
        if pattern_type is None:
            assert not result.oriented_slot_patterns
        else:
            assert len(result.oriented_slot_patterns) == 1
            assert type(result.oriented_slot_patterns[0]) is pattern_type

        # 0.4.12 assigns these physical owners exclusively to the new inventories.
        assert not result.fillets
        assert not result.slots
        assert not result.slot_patterns

    completeness = drawing.lint_summary()["quality"]["completeness"]
    assert completeness["unscored_recognized_families"] == unscored
    assert completeness["requirements"] == 0
    assert completeness["audited_score"] is None


def test_frozen_records_reject_mutation():
    """A record is immutable — assigning a field raises (frozen dataclass)."""
    hole = recognise_holes(_csk_plate())[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        hole.diameter = 99.0  # type: ignore[misc]


def test_part_based_recognisers_are_keyword_only_after_part():
    """Part-based recognisers take ``part`` then keyword-only args (ADR 0013)."""
    for fn in (
        recognise_holes,
        recognise_bosses,
        recognise_polygonal_bosses,
        recognise_countersinks,
        recognise_double_d_bores,
        recognise_chamfers,
        recognise_channels,
        recognise_fillets,
        recognise_slots,
        recognise_pockets,
        recognise_flats,
        recognise_grooves,
        recognise_plates,
        recognise_face_levels,
        recognise_risers,
        recognise_repeating_radial_profiles,
        recognise_turned_steps,
    ):
        params = list(inspect.signature(fn).parameters.values())
        assert params[0].name == "part"
        assert params[0].kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
        for p in params[1:]:
            assert p.kind == inspect.Parameter.KEYWORD_ONLY, (
                f"{fn.__name__}: '{p.name}' must be keyword-only (injected dep / tuning)"
            )


def test_cylinder_substrate_is_injectable():
    """#703: the three turned-stock recognisers accept a precomputed
    ``analyse_cylinders`` result (``cyls=``) and return identical records to a
    self-scan — the caller owns the one scan (ADR 0013 §2 / ADR 0008 Am5),
    mirroring ``recognise_holes``/``recognise_bosses``."""
    dshaft = Cylinder(10, 30) - Pos(10, 0, 0) * Box(10, 40, 40)
    grooved = Cylinder(10, 40) - (Cylinder(10, 4) - Cylinder(8, 4))
    for fn, part in (
        (recognise_turned_steps, _turned_shaft()),
        (recognise_grooves, grooved),
        (recognise_flats, dshaft),
    ):
        records = fn(part)
        assert records, f"{fn.__name__}: fixture no longer triggers the recogniser"
        assert fn(part, cyls=analyse_cylinders(part)) == records


def test_derived_recogniser_takes_single_positional_inventory():
    """A derived recogniser (``recognise_hole_patterns``) takes one positional arg."""
    params = list(inspect.signature(recognise_hole_patterns).parameters.values())
    assert params[0].name == "holes"
    assert params[0].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
