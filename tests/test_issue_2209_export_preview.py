"""An explicitly disposable export need not repeat the drawing's lint (#2209)."""

import pytest
from build123d import Box

from draftwright import build_drawing


def test_preview_export_skips_lint_but_normal_export_keeps_it(tmp_path, monkeypatch):
    drawing = build_drawing(Box(20, 10, 5), page="A4", scale=1)

    def fail_if_linted():
        raise RuntimeError("export lint ran")

    monkeypatch.setattr(drawing, "_lint_and_log", fail_if_linted)
    paths = drawing.export(str(tmp_path / "preview"), formats=("svg",), lint=False)
    assert paths["svg"] == str(tmp_path / "preview.svg")
    assert (tmp_path / "preview.svg").is_file()

    with pytest.raises(RuntimeError, match="export lint ran"):
        drawing.export(str(tmp_path / "final"), formats=("svg",))
    assert not (tmp_path / "final.svg").exists()
