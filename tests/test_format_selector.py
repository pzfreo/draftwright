"""CLI output-format parsing and emission behavior."""

import pytest


class TestFormatSelector:
    def test_parse_default_is_pdf(self):
        from draftwright.cli import _parse_formats

        assert _parse_formats("pdf") == ["pdf"]

    def test_parse_comma_list_keeps_order_and_dedupes(self):
        from draftwright.cli import _parse_formats

        assert _parse_formats("dxf, pdf ,dxf") == ["dxf", "pdf"]

    def test_parse_all_expands_to_all_four(self):
        from draftwright.cli import _parse_formats

        assert _parse_formats("all") == ["pdf", "svg", "dxf", "png"]

    def test_parse_png(self):
        from draftwright.cli import _parse_formats

        assert _parse_formats("png,pdf") == ["png", "pdf"]

    def test_parse_unknown_format_raises(self):
        import typer

        from draftwright.cli import _parse_formats

        with pytest.raises(typer.BadParameter, match="unknown format 'jpg'"):
            _parse_formats("pdf,jpg")

    def test_parse_empty_raises(self):
        import typer

        from draftwright.cli import _parse_formats

        with pytest.raises(typer.BadParameter, match="no output format"):
            _parse_formats(" , ")

    class _FakeDwg:
        """Record export calls without requiring a real render."""

        def __init__(self, tmp):
            self.tmp = tmp
            self.calls = []

        def export(self, *, formats):
            self.calls.append(("export", tuple(formats)))
            paths = {}
            for f in formats:
                p = str(self.tmp / f"o.{f}")
                open(p, "w").close()
                paths[f] = p
            return paths

    def test_emit_delegates_to_export_and_orders_paths(self, tmp_path):
        from draftwright.cli import _emit

        dwg = self._FakeDwg(tmp_path)
        out = _emit(dwg, ["pdf", "png", "dxf"])
        assert dwg.calls == [("export", ("pdf", "png", "dxf"))]
        assert out == [
            str(tmp_path / "o.pdf"),
            str(tmp_path / "o.png"),
            str(tmp_path / "o.dxf"),
        ]

    def test_emit_png_only(self, tmp_path):
        from draftwright.cli import _emit

        dwg = self._FakeDwg(tmp_path)
        assert _emit(dwg, ["png"]) == [str(tmp_path / "o.png")]
        assert dwg.calls == [("export", ("png",))]
