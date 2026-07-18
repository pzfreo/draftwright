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

from draftwright import build_drawing


def _filleted():
    plate = Box(90, 60, 20)
    e = plate.edges().filter_by(Axis.Z).sort_by(lambda e: e.center().X + e.center().Y)[-1]
    return fillet(e, 8)


@pytest.fixture
def fillet_counter(monkeypatch):
    import draftwright.model.detect as detect

    calls = {"n": 0}
    real = detect.recognise_fillets

    def counting(part, *args, **kwargs):
        calls["n"] += 1
        return real(part, *args, **kwargs)

    monkeypatch.setattr(detect, "recognise_fillets", counting)
    return calls


def test_detectors_run_once_per_build(fillet_counter):
    dwg = build_drawing(_filleted())

    assert fillet_counter["n"] == 1, (
        f"recognise_fillets ran {fillet_counter['n']}× in one build — the sizing and render "
        f"paths are re-detecting instead of sharing Analysis.model (ADR 0008: detected once)"
    )
    # The drawing's render model IS the stored sizing model — one object, one inventory.
    assert dwg.model() is dwg._analysis.model


def test_generate_script_detects_once(fillet_counter, tmp_path):
    from build123d import export_step

    from draftwright import generate_script

    step = str(tmp_path / "filleted.step")
    export_step(_filleted(), step)
    generate_script(step, out=str(tmp_path / "s"))
    assert fillet_counter["n"] == 1, (
        f"recognise_fillets ran {fillet_counter['n']}× in generate_script — the emitter "
        f"must reuse Analysis.model, not rebuild"
    )


@pytest.fixture
def cyls_counter(monkeypatch):
    """Count ``analyse_cylinders`` scans everywhere the name is import-bound —
    patching only ``_features`` would miss the recognisers' own bindings."""
    import draftwright.analysis as analysis
    import draftwright.drawing as drawing
    import draftwright.recognition._features as _features
    import draftwright.recognition.flats as flats
    import draftwright.recognition.grooves as grooves
    import draftwright.recognition.turned as turned

    calls = {"n": 0}
    real = _features.analyse_cylinders

    def counting(part):
        calls["n"] += 1
        return real(part)

    for mod in (_features, analysis, drawing, flats, grooves, turned):
        monkeypatch.setattr(mod, "analyse_cylinders", counting)
    return calls


def test_cylinder_scan_runs_once_per_build(cyls_counter):
    # #703: one analyse_cylinders scan per build, threaded to every substrate
    # recogniser (holes/bosses/turned/grooves/flats) via ``cyls=``. Injection
    # alone can't pin this — a recogniser that ignores ``cyls`` and self-scans,
    # or a dropped call-site threading, returns identical records; only the
    # scan count regresses.
    dwg = build_drawing(_filleted())
    dwg.lint()
    assert cyls_counter["n"] == 1, (
        f"analyse_cylinders ran {cyls_counter['n']}× in one build+lint — a recogniser "
        f"or lint path is re-scanning instead of sharing the one Analysis scan (#703)"
    )


def test_declared_model_runs_no_detection(fillet_counter):
    # ADR 0011: a caller-declared model skips detection entirely — build_part_model is
    # never invoked (the sizing path uses the declared model; the builder coerces it),
    # so the fillet detector must not run at all.
    from draftwright.model import declare

    part = _filleted()
    dwg = build_drawing(part, model=[declare.envelope(part)])
    assert fillet_counter["n"] == 0, (
        f"recognise_fillets ran {fillet_counter['n']}× on the declared-model path — "
        f"declaration must skip detection (ADR 0011)"
    )
    assert dwg._analysis.model is None  # declared models are not stored on Analysis
