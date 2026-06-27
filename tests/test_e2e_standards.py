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
    for name, obj in dwg._named.items():
        if not name.startswith("dim_") or dwg._anno_view.get(name) != "plan":
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
        for name, obj in dwg._named.items()
        if name.startswith("balloon_plan")
    ]
    assert balloon_tops, "expected balloons on CTC-02"
    assert max(balloon_tops) > pt + 20, "expected a balloon ring above the plan view"
    gap = max(balloon_tops) - dim_top
    assert gap < 60, (
        f"a plan balloon floats {gap:.0f} mm above the dimension stack — the pre-#125 "
        f"stale-cursor phantom corridor was ~150 mm; expected a small standoff"
    )
