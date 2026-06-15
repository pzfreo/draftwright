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
    assert any(isinstance(a, TitleBlock) for a in dwg.annotations), "no title block"
    assert len(dwg.annotations) >= 2, "expected dimensions + title block"

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
# Regression: NIST CTC-02 (AP203 geometry-only) used to render a spurious
# full-page vertical line from circle-edge-on projections exported as
# near-zero-radius SVG arcs.  The export must now leave no degenerate arcs.
# ---------------------------------------------------------------------------

CTC02_AP203 = Path(__file__).parent / "fixtures" / "nist_ctc_02_asme1_ap203.stp"


@pytest.mark.timeout(300)
def test_ctc02_no_degenerate_arcs_in_svg(tmp_path):
    from draftwright.make_drawing import _MIN_ARC_RADIUS, _SVG_ARC_RE

    dwg = build_drawing(str(CTC02_AP203), out=str(tmp_path / "ctc02"))
    svg, _ = dwg.export(str(tmp_path / "ctc02"))
    data = Path(svg).read_text(encoding="utf-8")
    degenerate = [
        m.group(0)
        for m in _SVG_ARC_RE.finditer(data)
        if abs(float(m.group(1))) < _MIN_ARC_RADIUS or abs(float(m.group(2))) < _MIN_ARC_RADIUS
    ]
    assert not degenerate, f"{len(degenerate)} near-zero-radius arcs leaked into the SVG"
