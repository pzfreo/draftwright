"""#602: feature detection runs once per build (ADR 0008 Amendment 5, #244).

`_analyse` builds the PartModel pre-scale so layout sizes from the same model the
renderers use (#584 WP1 A) — but the builder then called `build_model(a)` again,
re-running every detector `build_part_model` doesn't take by injection (grooves,
plates, step shoulders, chamfers, fillets, flats, pockets). On the NIST CTC-02
fixture the duplicate pass cost ~16 s, `recognise_fillets` alone 22.7 s across the
two runs. The sizing model is now stored on `Analysis.model` and reused.

`recognise_fillets` is the counted sentinel: it is the most expensive detector and
has no injection parameter, so a second call means the duplicate-detection path is
back.
"""

from __future__ import annotations

import pytest
from build123d import Axis, Box, fillet
from conftest import counting_calls, recognition_family_calls

from draftwright import build_drawing


def _filleted():
    plate = Box(90, 60, 20)
    e = plate.edges().filter_by(Axis.Z).sort_by(lambda e: e.center().X + e.center().Y)[-1]
    return fillet(e, 8)


@pytest.fixture
def fillet_counter():
    """Count ``recognise_fillets`` by CODE OBJECT, not by patching a module binding.

    This spied on ``model.detect.recognise_fillets`` until #1028 moved the call into the
    shared aggregate — at which point the binding was never used, the count read 0, and the
    test failed claiming the detector had stopped running when it had merely moved. The claim
    under test is "once per build", which is about the function, not about who imports it.

    Same reasoning as ``cyls_counter`` below and the ADR 0017 manifest guards: a code object
    cannot be re-bound, so the count survives the next migration too.
    """
    with recognition_family_calls({"recognise_fillets"}) as family_counts:
        yield family_counts


def test_detectors_run_once_per_build(fillet_counter):
    dwg = build_drawing(_filleted())

    assert fillet_counter.get("recognise_fillets") == 1, (
        "recognise_fillets ran "
        f"{fillet_counter.get('recognise_fillets', 0)}× in one build — the sizing and "
        f"render paths are re-detecting instead of sharing one inventory (ADR 0008: detected "
        f"once). A prismatic fixture, so the #1028 classification gate does not apply."
    )
    # The drawing's render model IS the stored sizing model — one object, one inventory.
    assert dwg.model() is dwg._analysis.model


def test_generate_script_detects_once(fillet_counter, tmp_path):
    from build123d import export_step

    from draftwright.sheet_emit import generate_sheet_script

    step = str(tmp_path / "filleted.step")
    export_step(_filleted(), step)
    generate_sheet_script(step, out=str(tmp_path / "s"))
    assert fillet_counter.get("recognise_fillets") == 1, (
        "recognise_fillets ran "
        f"{fillet_counter.get('recognise_fillets', 0)}× in generate_sheet_script — the "
        f"emitter must reuse one inventory, not rebuild"
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
    import b123d_recognisers._features as _features

    with counting_calls({"n": _features.analyse_cylinders}) as counts:
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


def test_declared_model_runs_no_detection(fillet_counter):
    # ADR 0011: a caller-declared model skips detection entirely — build_part_model is
    # never invoked (the sizing path uses the declared model; the builder coerces it),
    # so the fillet detector must not run at all.
    from draftwright.model import declare

    part = _filleted()
    dwg = build_drawing(part, model=[declare.envelope(part)])
    assert fillet_counter.get("recognise_fillets", 0) == 0, (
        "recognise_fillets ran "
        f"{fillet_counter.get('recognise_fillets', 0)}× on the declared-model path — "
        f"declaration must skip detection (ADR 0011)"
    )
    assert dwg._analysis.model is None  # declared models are not stored on Analysis
