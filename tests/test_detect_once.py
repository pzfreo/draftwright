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

from build123d import Axis, Box, fillet

from draftwright import build_drawing


def _filleted():
    plate = Box(90, 60, 20)
    e = plate.edges().filter_by(Axis.Z).sort_by(lambda e: e.center().X + e.center().Y)[-1]
    return fillet(e, 8)


def test_detectors_run_once_per_build(monkeypatch):
    import draftwright.model.detect as detect

    calls = 0
    real = detect.recognise_fillets

    def counting(part, *args, **kwargs):
        nonlocal calls
        calls += 1
        return real(part, *args, **kwargs)

    monkeypatch.setattr(detect, "recognise_fillets", counting)
    dwg = build_drawing(_filleted())

    assert calls == 1, (
        f"recognise_fillets ran {calls}× in one build — the sizing and render paths "
        f"are re-detecting instead of sharing Analysis.model (ADR 0008: detected once)"
    )
    # The drawing's render model IS the stored sizing model — one object, one inventory.
    assert dwg.model() is dwg._analysis.model
