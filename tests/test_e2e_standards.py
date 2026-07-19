"""End-to-end tests: build a drawing from STEP or a build123d object and assert
it meets the mechanically-checkable drawing standards.

These check the parts of "meets standards" a machine can verify:

- no error-severity lint violations (axis swaps, label mismatches, page bounds,
  view overlaps) — warnings are tolerated;
- the SVG declares the chosen ISO A-series page size;
- the SVG contains no native ``<text>`` elements (build123d renders glyphs as
  paths; stray ``<text>`` would not DXF-export and would not scale with the
  drawing);
- the SVG is well-formed XML;
- the standard four views and a title block are present, with at least one
  dimension.

Subjective aspects (ISO 7200 field completeness, ISO 128 line-type judgement) are
out of scope — they are not machine-checkable.
"""

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from build123d import Box, Cylinder, export_step
from build123d_drafting import TitleBlock

from draftwright import build_drawing, make_drawing


def _make_parts():
    """Representative geometry: a turned cylinder, a plate, a stepped block."""
    return {
        "cylinder": Cylinder(radius=15, height=40),
        "plate": Box(80, 50, 8),
        "stepped": Box(40, 40, 10) + Box(20, 20, 10).translate((0, 0, 10)),
    }


def _assert_meets_standards(dwg, svg_path, dxf_path):
    """Assert a built+exported drawing satisfies the checkable standards."""
    # Both files written.
    assert Path(svg_path).exists(), "SVG not written"
    assert Path(dxf_path).exists(), "DXF not written"

    # Structure: four standard views, a title block, at least one dimension.
    assert set(dwg.views) >= {"front", "plan", "side", "iso"}
    assert any(isinstance(a, TitleBlock) for a in dwg.items), "no title block"
    assert len(dwg.items) >= 2, "expected dimensions + title block"

    # Lint: no error-severity issues (warnings tolerated).
    errors = [i for i in dwg.lint() if i.severity == "error"]
    assert not errors, f"lint errors: {[(i.code, i.message) for i in errors]}"

    data = Path(svg_path).read_text(encoding="utf-8")

    # ISO page size declared on the SVG.
    assert f'width="{dwg.page_w:.3f}mm"' in data
    assert f'height="{dwg.page_h:.3f}mm"' in data

    # No native text elements — glyphs must be rendered as paths.
    assert "<text" not in data, "SVG contains native <text> elements"

    # Well-formed XML.
    ET.fromstring(data)


@pytest.mark.smoke  # representative full build → annotate → export → lint → standards
@pytest.mark.timeout(120)
@pytest.mark.parametrize("name", ["cylinder", "plate", "stepped"])
def test_e2e_from_object_meets_standards(tmp_path, name):
    part = _make_parts()[name]
    stem = str(tmp_path / name)
    dwg = build_drawing(part, out=stem, title=name.upper(), number="DWG-1")
    svg, dxf = dwg.export(stem)
    _assert_meets_standards(dwg, svg, dxf)


@pytest.mark.timeout(120)
def test_e2e_from_step_meets_standards(tmp_path):
    step = tmp_path / "plate.step"
    export_step(Box(80, 50, 8), str(step))
    stem = str(tmp_path / "plate")

    # The one-shot wrapper writes the files...
    svg, dxf = make_drawing(str(step), out=stem, title="PLATE", number="DWG-1")
    assert Path(svg).exists() and Path(dxf).exists()

    # ...and build_drawing on the same STEP gives a Drawing to assert against.
    dwg = build_drawing(str(step), out=stem, title="PLATE", number="DWG-1")
    _assert_meets_standards(dwg, svg, dxf)


# ---------------------------------------------------------------------------
# NIST MBE PMI Combined Test Cases (CTC), public-domain models.
# All 10 variants ship as fixtures and now build. Two #20 fixes got them there:
# the fuzzy section cut (_fuzzy_cut) unblocked CTC-04, and the direct
# STEPControl_Reader importer (_import_step) avoids the XCAF/PMI segfault that
# build123d's import_step hit on CTC-02 AP242.
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"
_CTC_AP203_OK = ["01", "02", "03", "04", "05"]
_CTC_AP242_OK = ["01", "02", "03", "04", "05"]


@pytest.mark.slow
@pytest.mark.timeout(600)
@pytest.mark.parametrize("n", _CTC_AP203_OK)
def test_ctc_ap203_meets_standards_no_degenerate_arcs(tmp_path, n):
    from draftwright.export import _MIN_ARC_RADIUS, _SVG_ARC_RE

    step = FIXTURES / f"nist_ctc_{n}_asme1_ap203.stp"
    stem = str(tmp_path / f"ctc{n}")
    dwg = build_drawing(str(step), out=stem)
    svg, dxf = dwg.export(stem)
    _assert_meets_standards(dwg, svg, dxf)
    # The #19 fix: no circle-edge-on degenerate arcs leak into the SVG.
    data = Path(svg).read_text(encoding="utf-8")
    degenerate = [
        m.group(0)
        for m in _SVG_ARC_RE.finditer(data)
        if abs(float(m.group(1))) < _MIN_ARC_RADIUS or abs(float(m.group(2))) < _MIN_ARC_RADIUS
    ]
    assert not degenerate, f"{len(degenerate)} near-zero-radius arcs leaked into the SVG"


@pytest.mark.slow
@pytest.mark.timeout(600)
@pytest.mark.parametrize("n", _CTC_AP242_OK)
def test_ctc_ap242_meets_standards(tmp_path, n):
    step = FIXTURES / f"nist_ctc_{n}_asme1_ap242.stp"
    stem = str(tmp_path / f"ctc{n}_ap242")
    dwg = build_drawing(str(step), out=stem)
    svg, dxf = dwg.export(stem)
    _assert_meets_standards(dwg, svg, dxf)


@pytest.mark.slow
@pytest.mark.timeout(600)
def test_ctc02_top_balloon_ring_hugs_dimensions():
    """#125: the plan-view TOP balloon ring sits just beyond the real dimension
    stack, not over the phantom corridor the deleted X-location dims leave in the
    above-strip cursor. Pre-fix the top ring floated ~150 mm above the highest
    dimension; it should now clear it by only a small standoff."""
    dwg = build_drawing(str(FIXTURES / "nist_ctc_02_asme1_ap203.stp"))
    a = dwg._analysis
    pt = a.PV_Y + a.pv_hh
    pl, pr = a.PV_X - a.fv_hw, a.PV_X + a.fv_hw

    # Highest plan-view dimension spanning the plan width above it — the real
    # obstruction the top ring must clear.
    dim_top = pt
    for name, obj in dwg.iter_annotations():
        if not name.startswith("dim_") or dwg.view_of(name) != "plan":
            continue
        bb = obj.bounding_box()
        if bb.max.X > pl and bb.min.X < pr and bb.max.Y > pt:
            dim_top = max(dim_top, bb.max.Y)

    # No plan balloon should float far above the dimension stack. Balloons are
    # leadered compounds (no label_bbox); the highest point of any of them is a
    # glyph at the end of its leader. Pre-#125 the top ring sat ~150 mm above the
    # highest dim; the fix keeps the whole ring within a small standoff of it.
    balloon_tops = [
        obj.bounding_box().max.Y
        for name, obj in dwg.iter_annotations()
        if name.startswith("balloon_plan")
    ]
    assert balloon_tops, "expected balloons on CTC-02"
    assert max(balloon_tops) > pt + 20, "expected a balloon ring above the plan view"
    gap = max(balloon_tops) - dim_top
    assert gap < 60, (
        f"a plan balloon floats {gap:.0f} mm above the dimension stack — the pre-#125 "
        f"stale-cursor phantom corridor was ~150 mm; expected a small standoff"
    )


def _dense_scattered_plate():
    """20 unpatterned Z-holes of distinct diameters — dense enough that even the
    auto-grown sheet (#121) can't fit their callouts, so the plan view escalates to a
    hole table + balloon ring (#93). The naturally-occurring escalation trigger in the
    fast-buildable range (CTC-02 is the STEP-fixture equivalent)."""
    import itertools

    from build123d import Box, Cylinder, Pos

    cols = [-40 + i * 20 for i in range(5)]  # 5 columns × 4 rows = 20 holes
    part = Box(90, 60, 12)
    for i, (c, y) in enumerate(itertools.product(range(5), [-18, -6, 6, 18])):
        part -= Pos(cols[c], y, 0) * Cylinder(1.0 + i * 0.2, 20)  # distinct radii → unpatterned
    return part


@pytest.mark.slow
@pytest.mark.timeout(600)
def test_dense_scattered_reconstruction_rebuilds_the_hole_table():
    """#426 Phase 4c: a dense scattered plan view escalates to a hole TABLE + balloon ring.
    A detect-only record→finalize reconstruction (mirroring what the --script emitter writes
    per hole: callout + locate + furniture) must reproduce the SAME escalation the auto-pass
    produces — the table, the balloon tag set, and NO orphaned hc_plan* callouts — and stay
    lint-clean. Before the Phase 4c fix (coverage recorded under place_furniture=False + leg D
    running _maybe_tabulate_holes), finalize left every plan callout on the sheet alongside the
    table (duplicate documentation), because the coverage the resolver removes was never
    registered."""
    part = _dense_scattered_plate()

    def snap(dwg):
        ann = dwg.annotations()
        return (
            "hole_table_plan" in ann,
            frozenset(n.split("_")[2] for n in ann if n.startswith("balloon_plan_")),
            frozenset(n for n in ann if n.startswith("hc_plan")),
        )

    auto = build_drawing(part)
    assert "hole_table_plan" in auto.annotations(), "fixture must escalate in the auto-pass"
    auto_codes = {i.code for i in auto.lint() if i.severity in ("warning", "error")}
    # #440/#639: the escalating build must not leak its consumed escalations into a later
    # `with dwg.deferred(): …` edit (which would re-fire leg D and relocate the table). Since
    # #639 the escalations live on a per-run PlacementContext, discarded when the build returns
    # — the leak is now impossible by construction (no drawing attribute to assert against).

    dwg = build_drawing(part, auto_dims=False)
    with dwg.deferred():
        for f in dwg.model().features:
            if getattr(f, "kind", None) in ("hole", "pattern"):
                dwg.callout(f)
                dwg.locate(f)
                dwg.furniture(f)

    assert snap(dwg) == snap(auto)  # same table + balloon tags, no orphaned callouts
    assert not [n for n in dwg.annotations() if n.startswith("hc_plan")]  # no duplicate callouts
    assert dwg._intents == []  # drained
    # (#639) Escalations live on the per-run PlacementContext — no cross-run leak to assert.
    # Lint no worse than the auto-pass (the epic's soft-acceptance bar): the reconstruction
    # introduces NO warning/error code the auto-pass doesn't already have — no new
    # callout_dropped / location_ref_dropped / table_dropped. (It is a strict subset here:
    # the table covers the tabulated holes identically, but the finalize routing happens to
    # avoid an incidental view_annotation_overlap the auto-pass leaves.)
    fin_codes = {i.code for i in dwg.lint() if i.severity in ("warning", "error")}
    assert fin_codes <= auto_codes


def _rt_prismatic_holes():
    from build123d import Cylinder, Pos

    part = Box(80, 60, 20)  # a row of Z-holes → callouts + location dims + centre marks
    for x in (-30, -10, 10, 30):
        part -= Pos(x, 20, 0) * Cylinder(3, 30)
    return part


def _rt_turned_shaft():
    from build123d import Cylinder, Pos

    return Cylinder(15, 30) + Pos(0, 0, 30) * Cylinder(8, 30)  # Z-turned ladder: ø + length


def _rt_bolt_circle():
    import math

    from build123d import Cylinder, Pos

    part = Box(60, 60, 15)  # 6-hole bolt circle → pattern furniture (centre-cross + pitch)
    for i in range(6):
        ang = i * math.pi / 3
        part -= Pos(20 * math.cos(ang), 20 * math.sin(ang), 0) * Cylinder(2.5, 30)
    return part


def _rt_counterbored_section():
    from build123d import Cylinder, Pos

    part = Box(60, 40, 20)  # a counterbore → the emitter records dwg.section()
    part -= Cylinder(4, 30)
    part -= Pos(0, 0, 2) * Cylinder(7, 20)
    return part


def _rt_rotational_boss():
    from build123d import Cylinder

    # A plain cylinder: a rotational boss (ø via callout()) plus a flagged "rotational"
    # gap comment for the OD/centrelines the verbs don't reach (#419). Exercises the
    # boss-callout path + a gap-comment line, distinct from the turned-shaft step chain.
    return Cylinder(15, 40)


# One fixture per emitter path: hole verbs (callout/locate/furniture), turned step
# diameter + length chain, pattern furniture, section, and a rotational boss + gap comment.
# The all-gaps flat (no-`with`) emit and the side-drilled flagged gate are covered by unit
# tests, not here (no simple build123d part reaches all-verb-gaps — even a cylinder is a boss).
_ROUNDTRIP_FAMILIES = [
    ("prismatic_holes", _rt_prismatic_holes),
    ("turned_shaft", _rt_turned_shaft),
    ("bolt_circle", _rt_bolt_circle),
    ("counterbored_section", _rt_counterbored_section),
    ("rotational_boss", _rt_rotational_boss),
]


@pytest.mark.slow
@pytest.mark.timeout(600)
@pytest.mark.parametrize(
    "name,factory", _ROUNDTRIP_FAMILIES, ids=[n for n, _ in _ROUNDTRIP_FAMILIES]
)
def test_generated_script_roundtrip_is_lint_error_free(tmp_path, name, factory):
    """#436: the STEP → generate_script → run-the-.py → drawing round-trip, exercised
    end-to-end across part families. The emitted script text is the thing under test (not
    the in-process `_reconstruct` mirror): it wraps the intent verbs in `with dwg.deferred():`
    and relies on `finalize()` running on block exit — a behavior only the *executed* script
    exercises. We append a lint epilogue so the script reports ITS OWN drawing's lint to
    stdout (no rebuild in this process), then assert exit 0 + the exported file written
    (PDF — the #709 default, aligned with the direct CLI) + no error-severity lint
    (warnings tolerated, matching _assert_meets_standards)."""
    import json
    import subprocess
    import sys

    from draftwright.make_drawing import generate_script

    step = tmp_path / f"{name}.step"
    export_step(factory(), str(step))
    py = generate_script(str(step), out=str(tmp_path / name))

    # Make the executed script print its own drawing's error-severity lint codes.
    src = Path(py).read_text(encoding="utf-8")
    src += (
        "\nimport json as _dwj\n"
        "_dwerrs = sorted({i.code for i in dwg.lint() if i.severity == 'error'})\n"
        "print('LINT_ERRORS=' + _dwj.dumps(_dwerrs))\n"
    )
    Path(py).write_text(src, encoding="utf-8")

    r = subprocess.run(
        [sys.executable, py], capture_output=True, text=True, cwd=str(tmp_path), timeout=300
    )
    assert r.returncode == 0, f"{name}: generated script failed:\n{r.stderr[-2000:]}"
    # #709: generated scripts default to PDF like the direct CLI (the old SVG+DXF
    # legacy-tuple default is gone; the slow tier caught this assertion post-merge
    # because the PR gate excludes it, #153).
    assert (tmp_path / f"{name}.pdf").exists(), f"{name}: no PDF written"
    marker = [ln for ln in r.stdout.splitlines() if ln.startswith("LINT_ERRORS=")]
    assert marker, f"{name}: no LINT_ERRORS line in stdout:\n{r.stdout[-1000:]}"
    errs = json.loads(marker[-1].split("=", 1)[1])
    assert errs == [], f"{name}: executed script produced lint errors {errs}"
