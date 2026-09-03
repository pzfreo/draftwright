"""#602: feature detection runs once per build (ADR 0008 Amendment 5, #244).

`_analyse` builds the PartModel pre-scale so layout sizes from the same model the
renderers use (#584 WP1 A) — but the builder then called `build_model(a)` again,
re-running every detector `build_part_model` doesn't take by injection (grooves,
plates, step shoulders, chamfers, fillets, flats, pockets). On the NIST CTC-02
fixture the duplicate pass cost ~16 s, `recognise_fillets` alone 22.7 s across the
two runs. The sizing model is now stored on `Analysis.model` and reused.

The public aggregate is the counted sentinel, while any public physical recogniser invoked
outside it is reported as a consumer bypass. A second aggregate or bypass means the
duplicate-detection path is back.
"""

from __future__ import annotations

import pytest
from build123d import Axis, Box, Cylinder, Pos, fillet
from conftest import counting_calls, recognition_consumer_calls

from draftwright import build_drawing


def _filleted():
    plate = Box(90, 60, 20)
    e = plate.edges().filter_by(Axis.Z).sort_by(lambda e: e.center().X + e.center().Y)[-1]
    return fillet(e, 8)


def _turned():
    return Cylinder(20, 60) - Pos(0, 0, 30) * Cylinder(6, 20)


@pytest.fixture
def aggregate_counter():
    """Count the released aggregate without inspecting its provider-owned registry."""

    with recognition_consumer_calls() as counts:
        yield counts


@pytest.mark.parametrize("framed_recognition", (False, True))
def test_detectors_run_once_per_build(aggregate_counter, framed_recognition):
    dwg = build_drawing(_filleted(), framed_recognition=framed_recognition)

    assert dwg.recognition_frame_decision["status"] == ("framed" if framed_recognition else "raw")
    expected = (
        {"recognise_framed_evidence": 1}
        if framed_recognition
        else {"build_recognition_evidence": 1}
    )
    assert aggregate_counter == expected, (
        f"unexpected recognition activity in one "
        f"{'framed' if framed_recognition else 'raw'} build: {aggregate_counter} — the "
        "sizing and render paths must share one inventory with no consumer bypass (ADR 0008)"
    )
    # The drawing's render model IS the stored sizing model — one object, one inventory.
    assert dwg.model() is dwg._analysis.model
    evidence = dwg.recognition_evidence()
    assert evidence is not None
    assert evidence.result is dwg.recognition()
    fillets = tuple(ref for ref in evidence.features if evidence.family(ref) == "fillets")
    assert len(fillets) == 1
    assert evidence.record(fillets[0]) in dwg.recognition().fillets


def test_generate_script_detects_once(aggregate_counter, tmp_path):
    from build123d import export_step

    from draftwright.sheet_emit import generate_sheet_script

    step = str(tmp_path / "filleted.step")
    export_step(_filleted(), step)
    generate_sheet_script(step, out=str(tmp_path / "s"))
    assert aggregate_counter == {"build_recognition_evidence": 1}, (
        f"unexpected recognition activity in generate_sheet_script: {aggregate_counter} — "
        "the emitter must reuse one inventory without a consumer bypass"
    )


@pytest.fixture
def cyls_counter():
    """Count ``analyse_cylinders`` scans by CODE OBJECT (see ``conftest.counting_calls``).

    Not by patching the modules that bind the name. #1019 spent four review rounds on that
    approach and each round found one more binding form it could not see — modules nobody
    had listed (``recognition.result`` and ``linting.coverage``, both on live paths), then
    aliased imports, and next would have been a function held in a container or a closure.
    Every miss is silent: the rescan happens and the count does not move.

    Yields a live dict, so the count is read AFTER the work — matching the previous
    fixture's ``calls["n"]`` shape.
    """
    from b123d_recognisers import analyse_cylinders

    with counting_calls({"n": analyse_cylinders}) as counts:
        yield counts


def test_cylinder_scan_runs_once_per_build(cyls_counter):
    # #703: one analyse_cylinders scan per build, threaded to every substrate
    # recogniser (holes/bosses/turned/grooves/flats) via ``cyls=``. Injection
    # alone can't pin this — a recogniser that ignores ``cyls`` and self-scans,
    # or a dropped call-site threading, returns identical records; only the
    # scan count regresses.
    dwg = build_drawing(_filleted())
    dwg.lint()
    scans = cyls_counter.get("n", 0)
    assert scans == 1, (
        f"analyse_cylinders ran {scans}× in one build+lint — a recogniser "
        f"or lint path is re-scanning instead of sharing the one Analysis scan (#703)"
    )


def test_declared_model_runs_no_detection(aggregate_counter):
    # ADR 0011: a caller-declared model skips detection entirely — build_part_model is
    # never invoked (the sizing path uses the declared model; the builder coerces it),
    # so the recognition aggregate must not run at all.
    from draftwright.model import declare

    part = _filleted()
    dwg = build_drawing(part, model=[declare.envelope(part)])
    assert aggregate_counter == {}, (
        f"declared-model recognition activity: {aggregate_counter} — declaration must skip "
        "detection and consumer recogniser bypasses (ADR 0011)"
    )
    assert dwg._analysis.model is None  # declared models are not stored on Analysis


@pytest.mark.parametrize("build", (_filleted, _turned))
def test_direct_model_construction_reuses_the_analysis_aggregate(build, tmp_path):
    """The second build_part_model seam must receive every aggregate inventory."""

    from draftwright.analysis import _analyse
    from draftwright.annotations.orchestrator import build_model

    analysis = _analyse(build(), "t", "1", "±0.1", "t", tmp_path / "unused.svg")
    with recognition_consumer_calls() as recognition_calls:
        model = build_model(analysis)

    assert model.features
    assert recognition_calls == {}, (
        f"direct model construction bypassed the analysis aggregate: {recognition_calls}"
    )
