"""Drawing mutation, coordinate, custom-view, and generated-script behavior."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from build123d import Box, Cylinder, Pos, export_step
from build123d_drafting import Leader, ViewCoordinates

from draftwright import build_drawing


@pytest.fixture(scope="module")
def small_box_dwg():
    return build_drawing(Box(30, 20, 10))


@pytest.mark.timeout(60)
def test_drawing_add_and_remove():
    dwg = build_drawing(Box(30, 20, 10))
    n0 = len(dwg.items)
    ldr = Leader(tip=dwg.at("front", 0, 0, 0), elbow=(5, 5, 0), label="X", draft=dwg.draft)
    dwg._add(ldr, "ldr_test")
    assert len(dwg.items) == n0 + 1
    removed = dwg.remove("ldr_test")
    assert removed is ldr
    assert len(dwg.items) == n0
    with pytest.raises(KeyError):
        dwg.remove("does_not_exist")


@pytest.mark.timeout(60)
def test_drawing_add_replaces_reused_name():
    dwg = build_drawing(Box(30, 20, 10))
    n0 = len(dwg.items)
    first = Leader(tip=dwg.at("front", 0, 0, 0), elbow=(5, 5, 0), label="A", draft=dwg.draft)
    second = Leader(tip=dwg.at("front", 0, 0, 0), elbow=(6, 6, 0), label="B", draft=dwg.draft)
    dwg._add(first, "ldr")
    dwg._add(second, "ldr")  # same name → replaces, no orphan left behind
    assert len(dwg.items) == n0 + 1
    assert first not in dwg.items
    assert dwg.remove("ldr") is second


@pytest.mark.timeout(60)
def test_drawing_at_maps_world_to_page(small_box_dwg):
    dwg = small_box_dwg
    cx, cy, cz = dwg.centroid
    base = dwg.at("front", cx, cy, cz)
    # Front view: world +X → page +X, world +Z → page +Y.
    dx = dwg.at("front", cx + 10, cy, cz)
    dz = dwg.at("front", cx, cy, cz + 10)
    assert dx[0] > base[0] and dx[1] == pytest.approx(base[1])
    assert dz[1] > base[1] and dz[0] == pytest.approx(base[0])


@pytest.mark.timeout(60)
def test_drawing_add_view(tmp_path):
    dwg = build_drawing(Box(30, 20, 10))
    look = dwg.look_at
    bottom_cam = (look[0], look[1], look[2] - dwg.dist)
    vc = dwg._add_view("bottom", Box(30, 20, 10), bottom_cam, (0, 1, 0), (260.0, 60.0))
    assert "bottom" in dwg.views
    assert isinstance(vc, ViewCoordinates)
    # The custom view exports alongside the standard ones.
    _p = dwg.export(str(tmp_path / "b"), formats=("svg", "dxf"))
    svg = _p["svg"]
    assert Path(svg).exists()


def test_generate_script_defers_invalid_scale_page(tmp_path):
    # #388/#401: an out-of-range scale/page must NOT crash generation — the script is
    # written with the value embedded and validation deferred to run time (consistent
    # with a large unfittable scale, which already defers). Retargeted onto the Sheet
    # emitter by #940; the value rides the emitted Sheet(...) call rather than a cog field.
    from draftwright.sheet_emit import generate_sheet_script

    step = tmp_path / "p.step"
    export_step(Box(30, 20, 10), str(step))
    py = generate_sheet_script(str(step), out=str(tmp_path / "p"), scale=0.001, page="A9")
    content = Path(py).read_text(encoding="utf-8")
    assert "scale=0.001" in content and "page='A9'" in content


@pytest.mark.timeout(180)
def test_generated_script_runs_and_preserves_pmi(tmp_path):
    # #388 acceptance: a generated --pmi annotate script preserves pmi when RUN — execute
    # it in a subprocess and assert it builds output without error. Retargeted onto the
    # Sheet emitter by #940. PMI survives differently there and better: it is threaded
    # through detection and emitted as explicit dimension lines, rather than a flag the
    # script re-applies on every run.
    import os
    import subprocess
    import sys

    from draftwright.sheet_emit import generate_sheet_script

    step = tmp_path / "p.step"
    # A hole so the #400 listing carries a non-ASCII ø in a comment — proves the utf-8
    # source runs even under an ASCII stdout (source encoding is independent of stdout).
    export_step(Box(80, 50, 8) - Pos(0, 0, 0) * Cylinder(4, 40), str(step))
    py = generate_sheet_script(str(step), out=str(tmp_path / "p"), pmi="annotate")
    # Force an ASCII stdout so a non-ASCII char in the script's own print() (e.g. a
    # Unicode arrow) fails HERE on every platform, not only on a Windows cp1252 console.
    env = {**os.environ, "PYTHONIOENCODING": "ascii"}
    r = subprocess.run(
        [sys.executable, py],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        timeout=150,
        env=env,
    )
    assert r.returncode == 0, f"generated script failed:\n{r.stderr[-1500:]}"
    # #709: the emitted export defaults to PDF (the CLI / sheet-flavour default).
    assert (tmp_path / "p.pdf").exists(), "generated script did not write the PDF"

