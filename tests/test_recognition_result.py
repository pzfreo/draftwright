"""ADR 0017 phase 1: one explicit recognition result."""

from dataclasses import FrozenInstanceError

import pytest
from b123d_recognisers import (
    RecognitionResult,
    build_recognition_result,
    recognise_plates,
)
from build123d import Align, Axis, Box, Cylinder, Pos, chamfer, fillet
from conftest import counting_calls

from draftwright import build_drawing
from draftwright.model import build_part_model


def _plate_with_holes():
    plate = Box(60, 40, 8)
    return plate - Pos(-15, 0, 0) * Cylinder(3, 8) - Pos(15, 0, 0) * Cylinder(3, 8)


def test_recognition_result_is_frozen_and_owns_tuple_inventories():
    result = build_recognition_result(_plate_with_holes())

    assert isinstance(result, RecognitionResult)
    assert len(result.holes) == 2
    assert isinstance(result.holes, tuple)
    with pytest.raises(FrozenInstanceError):
        result.holes = ()


def test_built_drawing_exposes_its_recognition_result_without_private_state(monkeypatch):
    """The accessor hands back THE build's result, not an equal-looking rebuild — every
    value assertion below is satisfied by a second orchestration nobody asked for.

    Asserted through the public surface: comparing against ``drawing._analysis.recognition``
    would be exactly the test-side reach-through ``test_private_test_attr_reads`` exists to
    stop. Stable identity rules out a rebuild-per-call; the poisoned orchestration rules out
    a rebuild-then-cache, which stable identity alone would accept.
    """
    import b123d_recognisers.result as result_module

    import draftwright.analysis as analysis_module

    drawing = build_drawing(_plate_with_holes())

    result = drawing.recognition()
    assert isinstance(result, RecognitionResult)
    assert len(result.holes) == 2
    assert result.cylinders

    def forbidden(*args, **kwargs):
        raise AssertionError("Drawing.recognition() re-ran recognition instead of handing back")

    monkeypatch.setattr(result_module, "build_recognition_result", forbidden)
    monkeypatch.setattr(analysis_module, "build_raw_recognition_result", forbidden)
    assert drawing.recognition() is result


# `test_orchestrator_injects_each_shared_dependency_once` was here until #1244.
#
# It replaced every recogniser in `b123d_recognisers.result` with a fake to assert the aggregate
# injects the cylinder substrate once and hands the same objects on. Every symbol it asserted
# belonged to the package — it stated nothing about draftwright — and 0.2.6 restructured that
# orchestration (`analyse_cylinders` gave way to `CylinderInventory`), so its patch targets no
# longer exist.
#
# Not retargeted: the property draftwright depends on is "each family runs exactly once per
# build, and nothing rescans", and that is asserted from OUR side, against the running engine and
# the public family names, by `test_recognition_manifest.py::
# test_an_automatic_build_runs_each_family_exactly_once_and_lint_runs_no_migrated_one`. A second
# copy phrased in the dependency's private vocabulary bought a private reach and no coverage.


def _grooved_flatted_shaft():
    """A turned shaft carrying both a groove and a machined flat."""
    part = Cylinder(20, 30) + Pos(0, 0, 30) * Cylinder(14, 30)
    part -= Pos(0, 0, 10) * (Cylinder(20, 5) - Cylinder(16, 5))  # groove
    part -= Pos(20, 0, 45) * Box(20, 40, 30)  # flat
    return part


def _chamfered_filleted_block():
    """One chamfered edge and one filleted edge — both edge-treatment inventories, with
    content (#1028/#1254)."""
    box = Box(60, 40, 30)
    box = chamfer(box.edges().filter_by(Axis.Z).sort_by(Axis.X)[-1], 4)
    return fillet(box.edges().filter_by(Axis.Z).sort_by(Axis.X)[0], 5)


def _l_bracket():
    """A base slab plus an upright wall — two plates on different axes."""
    return Box(80, 40, 8) + Pos(-36, 0, 24) * Box(8, 40, 40)


def _slot_lattice_plate():
    """A 2x3 slot lattice — `rect_grid` needs n >= 6 to form a slot pattern."""
    part = Box(180, 130, 20)
    for i in range(2):
        for j in range(3):
            part -= Pos((i - 0.5) * 44, (j - 1) * 34, 0) * Box(24, 8, 20)
    return part


def _double_d_plate():
    centre = (Align.CENTER, Align.CENTER, Align.CENTER)
    cutter = Cylinder(5, 20, align=centre) & Box(7.2, 20, 30, align=centre)
    return Box(30, 30, 10, align=centre) - cutter


@pytest.mark.parametrize(
    ("name", "build"),
    [
        ("grooved+flatted shaft", _grooved_flatted_shaft),
        ("slot lattice", _slot_lattice_plate),
        # The #1028 families. Without these the equivalence guard could not see a defect where
        # an injected non-empty inventory is consumed as empty — `None` rescans, `()` must
        # suppress, and only a fixture that actually produces the feature tells them apart
        # (Codex #1033 r2).
        ("chamfered+filleted block", _chamfered_filleted_block),
        ("L-bracket plates", _l_bracket),
        ("double-D bore", _double_d_plate),
    ],
)
def test_injecting_the_aggregate_builds_the_same_model_as_detecting(name, build):
    """The migration's actual claim: feeding ``build_part_model`` the aggregate's inventories
    produces the model it would have detected for itself (#1026).

    The manifest's value oracle cannot check this. It computes the expected inventory the same
    way the orchestration does, so if the *inputs* were wrong — a recogniser needing a filtered
    or reconciled set rather than the aggregate's raw accepted records — production and oracle
    would agree on the same wrong answer and the guard would pass (Codex #1030 r1).

    This compares the two paths end to end instead: detect-for-yourself against
    inject-from-the-aggregate. It is the equivalence every hoist in ADR 0017 assumes and the
    one that silently stops holding when a recogniser's contract drifts.
    """
    part = build()
    rec = build_recognition_result(part)
    bb = part.bounding_box()

    detected = build_part_model(part)
    injected = build_part_model(
        part,
        holes=list(rec.holes),
        double_d_bores=list(rec.double_d_bores),
        patterns=list(rec.hole_patterns),
        bosses=list(rec.bosses),
        polygonal_bosses=list(rec.polygonal_bosses),
        polygonal_stock=list(rec.polygonal_stock),
        channels=list(rec.channels),
        slots=list(rec.slots),
        slot_patterns=list(rec.slot_patterns),
        grooves=list(rec.grooves),
        flats=list(rec.flats),
        pockets=list(rec.pockets),
        pocket_patterns=list(rec.pocket_patterns),
        pads=list(rec.pads),
        step_zs=rec.step_ladder_for_z_span(bb.min.Z, bb.max.Z),
        face_levels=list(rec.step_levels),
        risers=list(rec.risers),
        chamfers=list(rec.chamfers),
        fillets=list(rec.fillets),
        circular_blind_steps=list(rec.circular_blind_steps),
        paired_ramp_steps=list(rec.paired_ramp_steps),
        through_steps=list(rec.through_steps),
        plates=list(rec.plates),
        cyls=rec.cylinders,
    )

    assert [f.kind for f in injected.features] == [f.kind for f in detected.features], (
        f"{name}: injecting the aggregate changed WHICH features the model carries"
    )
    assert injected.features == detected.features, (
        f"{name}: injecting the aggregate changed the features' values"
    )


def test_the_gate_is_the_orchestrations_not_the_call_sites():
    """#1028's actual claim: a family can be MIGRATED *and* not always run.

    Plates remain classification-gated because hoisting them unconditionally would scan every
    turned build for a prismatic-only result `build_part_model` discards. Chamfers and fillets
    deliberately stopped sharing this gate in b123d-recognisers 0.2.9, which recognises their
    conical/toroidal turned forms (#1254/#1281).
    """
    # An L-bracket: a base slab plus an upright wall, which is what `recognise_plates` looks
    # for. A plain box has no plates, so it would make the gate assertion below vacuous.
    prismatic = Box(80, 40, 8) + Pos(-36, 0, 24) * Box(8, 40, 40)
    turned = Cylinder(20, 60)

    assert build_recognition_result(prismatic, rotational=False).rotational is False
    assert build_recognition_result(turned, rotational=True).rotational is True

    # Same solid, both classifications: only the gate differs, so the plate inventory change
    # is the gate's doing and not the geometry's.
    ungated = build_recognition_result(prismatic, rotational=False)
    gated = build_recognition_result(prismatic, rotational=True)

    assert gated.plates == (), "a rotational classification must gate prismatic plates away"
    assert ungated.plates, "fixture stopped producing plates, so the gate proves nothing here"
    # And nothing else moved: the gate is narrow, not a blanket suppression.
    assert ungated.holes == gated.holes
    assert ungated.risers == gated.risers
    assert ungated.step_levels == gated.step_levels


def test_the_plates_gate_needs_both_halves_not_just_the_rotational_one():
    """`plates` gates on a CONJUNCTION — not rotational AND no turned profile — and the
    profile half needs its own counterexample.

    The manifest's exclusion test drives both its turned fixtures through `build_drawing`,
    where they classify rotational, so weakening the gate to `if prismatic` alone still
    skipped plates on both and that guard stayed green (Codex #1033 r1). The half that was
    untested is exactly the documented "caller has no classification" path: a stepped shaft
    with `rotational=False`, where only the profile keeps the scan away.

    Asserted by COUNTING the call, not by checking the inventory came back empty:
    `recognise_plates` naturally finds nothing on a shaft, so an empty result proves the scan
    was skipped only by coincidence. The cost this gate exists to avoid is the scan itself.
    """
    shaft = Cylinder(20, 30) + Pos(0, 0, 30) * Cylinder(14, 30)

    with counting_calls({"plates": recognise_plates}) as counts:
        rec = build_recognition_result(shaft, rotational=False)

    assert rec.turned_steps, "fixture stopped producing a turned profile — the gate's other half"
    assert counts.get("plates", 0) == 0, (
        "recognise_plates ran for a part with a turned profile even though the caller said "
        "not-rotational — the conjunction has collapsed to its rotational half, and every "
        "unclassified stepped-shaft aggregate now pays for a scan the model discards"
    )
    assert rec.plates == ()
