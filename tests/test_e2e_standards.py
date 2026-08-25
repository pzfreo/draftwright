"""End-to-end tests: build and export drawings, then inspect their honest result.

The small representative parts check the parts of "meets standards" a machine can verify:

- no error-severity lint violations (axis swaps, label mismatches, page bounds,
  view overlaps) — warnings are tolerated;
- the SVG declares the chosen ISO A-series page size;
- the SVG contains no native ``<text>`` elements (build123d renders glyphs as
  paths; stray ``<text>`` would not DXF-export and would not scale with the
  drawing);
- the SVG is well-formed XML;
- the standard four views and a title block are present, with at least one
  dimension.

The larger CTC corpus also contains automatic plans that are currently diagnostic rather than
complete. Those cases must remain exportable while failing the lint/quality gate explicitly;
calling them standards-clean would undo #1250. Subjective aspects (ISO 7200 field completeness,
ISO 128 line-type judgement) are out of scope — they are not machine-checkable.
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


def _assert_export_contract(dwg, svg_path, dxf_path):
    """Assert the structural contract shared by complete and diagnostic drawings."""
    # Both files written.
    assert Path(svg_path).exists(), "SVG not written"
    assert Path(dxf_path).exists(), "DXF not written"

    # Structure: four standard views, a title block, at least one dimension.
    assert set(dwg.views) >= {"front", "plan", "side", "iso"}
    assert any(isinstance(a, TitleBlock) for a in dwg.items), "no title block"
    assert len(dwg.items) >= 2, "expected dimensions + title block"

    data = Path(svg_path).read_text(encoding="utf-8")

    # ISO page size declared on the SVG.
    assert f'width="{dwg.page_w:.3f}mm"' in data
    assert f'height="{dwg.page_h:.3f}mm"' in data

    # No native text elements — glyphs must be rendered as paths.
    assert "<text" not in data, "SVG contains native <text> elements"

    # Well-formed XML.
    ET.fromstring(data)


def _assert_meets_standards(dwg, svg_path, dxf_path):
    """Assert a built+exported drawing satisfies the checkable standards."""
    _assert_export_contract(dwg, svg_path, dxf_path)

    errors = [i for i in dwg.lint() if i.severity == "error"]
    assert not errors, f"lint errors: {[(i.code, i.message) for i in errors]}"


def _assert_ctc_diagnostic_contract(dwg, svg_path, dxf_path, *, expect_incomplete):
    """Keep diagnostic CTC exports usable without ever certifying them as complete."""
    _assert_export_contract(dwg, svg_path, dxf_path)

    errors = [i for i in dwg.lint() if i.severity == "error"]
    if expect_incomplete:
        codes = [i.code for i in errors]
        # ``overall_dim_withheld`` is a measurement-specific explanation of the same
        # incomplete plan, not a second unrelated standards failure. It may precede the
        # aggregate terminal issue when a mandatory extent cannot fit (#1215).
        assert codes in (
            ["plan_incomplete"],
            ["overall_dim_withheld", "plan_incomplete"],
        ), f"expected the known incomplete plan, got {[(i.code, i.message) for i in errors]}"
        assert dwg.scale_decision["status"] == "incomplete"
        assert dwg.lint_summary()["passed"] is False
    else:
        assert not errors, f"lint errors: {[(i.code, i.message) for i in errors]}"
        assert dwg.lint_summary()["passed"] is True


@pytest.mark.smoke  # representative full build → annotate → export → lint → standards
@pytest.mark.timeout(120)
@pytest.mark.parametrize("name", ["cylinder", "plate", "stepped"])
def test_e2e_from_object_meets_standards(tmp_path, name):
    part = _make_parts()[name]
    stem = str(tmp_path / name)
    dwg = build_drawing(part, out=stem, title=name.upper(), number="DWG-1")
    _p = dwg.export(stem, formats=("svg", "dxf"))
    svg = _p["svg"]
    dxf = _p["dxf"]
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
_MIN_BALLOON_RING_EXTENT_MM = 20.0
_MAX_BALLOON_RING_EXTENT_MM = 60.0


@pytest.mark.slow
@pytest.mark.timeout(600)
@pytest.mark.parametrize("n", _CTC_AP203_OK)
def test_ctc_ap203_exports_honest_diagnostic_no_degenerate_arcs(tmp_path, n):
    from draftwright.export import _MIN_ARC_RADIUS, _SVG_ARC_RE

    step = FIXTURES / f"nist_ctc_{n}_asme1_ap203.stp"
    stem = str(tmp_path / f"ctc{n}")
    dwg = build_drawing(str(step), out=stem)
    _p = dwg.export(stem, formats=("svg", "dxf"))
    svg = _p["svg"]
    dxf = _p["dxf"]
    _assert_ctc_diagnostic_contract(dwg, svg, dxf, expect_incomplete=True)
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
@pytest.mark.parametrize(("n", "expect_incomplete"), [(n, n != "01") for n in _CTC_AP242_OK])
def test_ctc_ap242_exports_honest_result(tmp_path, n, expect_incomplete):
    step = FIXTURES / f"nist_ctc_{n}_asme1_ap242.stp"
    stem = str(tmp_path / f"ctc{n}_ap242")
    dwg = build_drawing(str(step), out=stem)
    _p = dwg.export(stem, formats=("svg", "dxf"))
    svg = _p["svg"]
    dxf = _p["dxf"]
    _assert_ctc_diagnostic_contract(dwg, svg, dxf, expect_incomplete=expect_incomplete)


@pytest.mark.slow
@pytest.mark.timeout(600)
def test_ctc02_infeasible_hole_table_restores_feature_callouts():
    """#1144: a real dense model fails its all-row balloon transaction honestly."""
    dwg = build_drawing(str(FIXTURES / "nist_ctc_02_asme1_ap203.stp"))
    annotations = dwg.annotations()
    issue_codes = {issue.code for issue in dwg.registry.issues}

    assert "hole_table_plan" not in annotations
    assert not [name for name in annotations if name.startswith("balloon_plan")]
    assert [name for name in annotations if name.startswith("hc_plan")]
    assert {"table_dropped", "balloon_dropped"} <= issue_codes


@pytest.mark.slow
@pytest.mark.timeout(600)
def test_ctc04_infeasible_hole_table_restores_complete_fallback():
    """#1144: real fallback remains complete when every row cannot be keyed."""
    dwg = build_drawing(str(FIXTURES / "nist_ctc_04_asme1_ap203.stp"))
    annotations = dwg.annotations()
    issue_codes = {issue.code for issue in dwg.registry.issues}

    assert "hole_table_plan" not in annotations
    assert not [name for name in annotations if name.startswith("balloon_plan")]
    assert [name for name in annotations if name.startswith("hc_plan")]
    assert {"table_dropped", "balloon_dropped"} <= issue_codes
    assert "hole_requirement_missing" not in {issue.code for issue in dwg.lint()}


def _dense_scattered_plate():
    """16 irregular distinct-spec bores with a crossing-free table inventory."""

    from build123d import Box, Cylinder, Pos

    positions = (
        (-42.7, -27.4),
        (-15.1, -31.9),
        (11.0, -28.7),
        (44.8, -27.9),
        (-46.0, 32.2),
        (-16.8, 32.4),
        (16.8, 29.3),
        (45.3, 31.5),
        (-56.5, -17.6),
        (-51.6, -7.9),
        (-51.6, 7.5),
        (-51.2, 16.7),
        (50.7, -15.6),
        (56.4, -6.4),
        (50.8, 3.6),
        (55.1, 16.3),
    )
    part = Box(120, 80, 12)
    for index, (x, y) in enumerate(positions):
        part -= Pos(x, y, 0) * Cylinder(1.0 + index * 0.15, 20)
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
    plan_top = auto.view_bounds("plan")[3]
    balloon_top = max(
        obj.bounding_box().max.Y
        for name, obj in auto.iter_annotations()
        if name.startswith("balloon_plan")
    )
    assert balloon_top > plan_top + _MIN_BALLOON_RING_EXTENT_MM
    assert balloon_top - plan_top < _MAX_BALLOON_RING_EXTENT_MM
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
    """#436: the STEP → emit → run-the-.py → drawing round-trip, exercised end-to-end across
    part families. The emitted script TEXT is the thing under test, not an in-process mirror
    of it: it runs in a subprocess, on a clean interpreter, exactly as a user would run it.
    Since #968 the script already ends with ``drawing = sheet.build()`` then
    ``drawing.export(...)``, so we only APPEND a lint report — the drawing it critiques is the
    one the script itself built and exported, which is precisely the property #968 exists to
    give an editor. (Before #968 this test had to rewrite the tail to get a handle on the
    Drawing at all; that it no longer does is the change working.) Then assert exit 0 + the
    exported file written (PDF — the #709 default, aligned with the direct CLI) + no
    error-severity lint (warnings tolerated, matching _assert_meets_standards).

    Migrated from the imperative emitter to the Sheet one when #940 retired it. The original
    turned on `with dwg.deferred():` and `finalize()` firing on block exit; the Sheet script
    has no such block, so what remains under test is the plainer and more important claim —
    the generated file runs and draws a lint-clean drawing."""
    import json
    import re
    import subprocess
    import sys

    from draftwright.sheet_emit import generate_sheet_script

    step = tmp_path / f"{name}.step"
    export_step(factory(), str(step))
    py = generate_sheet_script(str(step), out=str(tmp_path / name))

    src = Path(py).read_text(encoding="utf-8")
    # The script binds `drawing` itself now, so nothing is rewritten — the epilogue reads the
    # same object the export wrote. Pinned rather than assumed: if the emitted tail ever stops
    # naming the drawing, or builds twice, this test would otherwise quietly go back to
    # critiquing a different Drawing from the one it checks on disk.
    assert re.search(r"^drawing = sheet\.build\(\)$", src, flags=re.M), (
        f"{name}: the generated script no longer names its Drawing — the lint below would "
        "critique something other than what it exported"
    )
    assert src.count("sheet.build()") == 1, f"{name}: the generated script builds more than once"
    exports = re.findall(r"^drawing\.export\((.*)\)$", src, flags=re.M)
    assert len(exports) == 1, f"{name}: expected one drawing.export(...) line, got {exports}"
    # PDF is what this test then looks for on disk, so a fixture that starts requesting
    # something else must fail here rather than silently pass an existence check for a file
    # nothing wrote (#957 review made the same point about a vacuous assertion).
    assert "formats=('pdf',)" in exports[0], (
        f"{name}: the emitted export requests {exports[0]}, not PDF; the assertion below "
        "would be checking for a file the script never writes"
    )
    src += (
        "\nimport json as _dwj\n"
        "_dwerrs = sorted({i.code for i in drawing.lint() if i.severity == 'error'})\n"
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
