"""Shape-export fallback and SVG arc sanitization behavior."""

from pathlib import Path

import pytest
from build123d import Box, Compound, Edge

from draftwright import build_drawing
from draftwright.export import _export_shape


class _FlakyExporter:
    """Stand-in exporter: rejects multi-element shapes (or everything)."""

    def __init__(self, fail_all=False):
        self.added = []
        self.fail_all = fail_all

    def add_shape(self, shape, layer=None):
        if self.fail_all:
            raise AssertionError("Constraint failed")
        elements = shape.faces() or shape.edges()
        if len(elements) > 1:
            raise AssertionError("Constraint failed")
        self.added.append(shape)


class TestExportShapeFallback:
    def test_compound_falls_back_to_edges(self):
        edges = Compound(
            [
                Edge.make_line((0, 0, 0), (1, 0, 0)),
                Edge.make_line((0, 1, 0), (1, 1, 0)),
            ]
        )
        exporter = _FlakyExporter()
        _export_shape(exporter, edges, "hidden", "view 'iso'")
        assert len(exporter.added) == 2

    def test_all_elements_failing_raises_with_context(self):
        edges = Compound([Edge.make_line((0, 0, 0), (1, 0, 0))])
        with pytest.raises(RuntimeError, match=r"view 'iso' \(layer 'hidden'\)"):
            _export_shape(_FlakyExporter(fail_all=True), edges, "hidden", "view 'iso'")

    def test_annotation_falls_back_to_faces(self):
        from build123d import Draft
        from build123d_drafting import Note

        note = Note("AB", (10, 10), Draft(font_size=3.0))  # two glyphs → ≥2 faces
        exporter = _FlakyExporter()
        _export_shape(exporter, note, "dims", "annotation 'AB'")
        assert len(exporter.added) == len(note.faces())

    def test_mixed_faces_and_loose_edges_all_exported(self):
        # A compound mixing text faces with bare stroke edges must not lose
        # the edges in the element-wise path.
        from build123d import Text

        mixed = Compound([*Text("A", 3).faces(), Edge.make_line((5, 5, 0), (9, 5, 0))])
        exporter = _FlakyExporter()
        _export_shape(exporter, mixed, "dims", "annotation 'mixed'")
        assert len(exporter.added) == len(mixed.faces()) + 1

    def test_svg_exporter_failure_raises_when_nothing_exports(self, monkeypatch):
        # Atomic (SVG) path: whole-shape add fails and the shape decomposes
        # to nothing — the original error must surface, not be swallowed.
        from build123d import ExportSVG

        svg = ExportSVG()
        svg.add_layer("part")

        def boom(self, shape, layer="", **kwargs):
            raise AssertionError("Constraint failed")

        monkeypatch.setattr(ExportSVG, "add_shape", boom)
        with pytest.raises(RuntimeError, match="nothing could be exported"):
            _export_shape(svg, Compound([]), "part", "view 'iso'")

    @pytest.mark.timeout(60)
    def test_export_survives_one_bad_compound(self, tmp_path, monkeypatch):
        # Simulate #83: OCCT raises a bare AssertionError for one view
        # compound. export() must degrade element-wise and still write files.
        from build123d import ExportSVG

        dwg = build_drawing(Box(30, 20, 10))
        real = ExportSVG.add_shape
        state = {"tripped": False}

        def flaky(self, shape, layer="default", **kwargs):
            if not state["tripped"] and layer == "part":
                state["tripped"] = True
                raise AssertionError("Constraint failed")
            return real(self, shape, layer=layer, **kwargs)

        monkeypatch.setattr(ExportSVG, "add_shape", flaky)
        _p = dwg.export(str(tmp_path / "f"), formats=("svg", "dxf"))
        svg = _p["svg"]
        dxf = _p["dxf"]
        assert Path(svg).exists() and Path(dxf).exists()


class TestSanitizeSvgArcs:
    """build123d's ExportSVG writes a circle seen edge-on as an elliptical arc
    with a vanishing minor radius (ry ~ 1e-7). Renderers blow that up into a
    spurious full-page line. sanitize_svg_arcs rewrites such arcs as the straight
    line segments they actually are, leaving real-radius arcs untouched."""

    def _write(self, tmp_path, body):
        from pathlib import Path

        p = Path(tmp_path) / "t.svg"
        p.write_text(f'<svg><g id="part">{body}</g></svg>', encoding="utf-8")
        return str(p)

    @pytest.mark.timeout(30)
    def test_degenerate_arc_rewritten_to_line(self, tmp_path):
        from pathlib import Path

        from draftwright.export import sanitize_svg_arcs

        f = self._write(
            tmp_path, '<path d="M 441.547 224.55 A 3.65627 5.88651e-7 90.0 0 0 441.547 222.627" />'
        )
        n = sanitize_svg_arcs(f)
        out = Path(f).read_text(encoding="utf-8")
        assert n == 1
        assert "L 441.547 222.627" in out
        assert " A " not in out  # the degenerate arc command is gone

    @pytest.mark.timeout(30)
    def test_real_radius_arc_preserved(self, tmp_path):
        from pathlib import Path

        from draftwright.export import sanitize_svg_arcs

        arc = '<path d="M 10 10 A 5.0 5.0 0 0 1 20 20" />'
        f = self._write(tmp_path, arc)
        n = sanitize_svg_arcs(f)
        assert n == 0
        assert "A 5.0 5.0 0 0 1 20 20" in Path(f).read_text(encoding="utf-8")
