"""#1438 — direct CLI rendering emits an explicit machine-readable sidecar."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from draftwright.cli import app


class _Drawing:
    def __init__(self, out: Path) -> None:
        self.out = str(out)
        self.calls: list[tuple[str, object]] = []

    def export(self, *, formats):
        formats = tuple(formats)
        self.calls.append(("export", formats))
        stem = self.out
        for suffix in (".svg", ".dxf", ".pdf", ".png"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        return {name: f"{stem}.{name}" for name in formats}

    def write_report(self, path):
        self.calls.append(("write_report", path))
        return path


@pytest.mark.parametrize(
    ("extra", "expect_report"),
    [([], True), (["--no-report"], False)],
)
def test_direct_cli_report_default_and_explicit_opt_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extra: list[str],
    expect_report: bool,
) -> None:
    import draftwright.builder as builder

    output = tmp_path / "part"
    drawing = _Drawing(output)
    monkeypatch.setattr(builder, "build_drawing", lambda **_kwargs: drawing)

    result = CliRunner().invoke(
        app,
        ["source.step", "--out", str(output), "--format", "pdf,dxf", *extra],
    )

    assert result.exit_code == 0, result.output
    expected_calls: list[tuple[str, object]] = [("export", ("pdf", "dxf"))]
    expected_paths = [f"{output}.pdf", f"{output}.dxf"]
    if expect_report:
        expected_calls.append(("write_report", f"{output}.draftwright.json"))
        expected_paths.append(f"{output}.draftwright.json")
    assert drawing.calls == expected_calls
    assert result.output.splitlines() == expected_paths


def test_report_is_not_attempted_when_visual_export_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import draftwright.builder as builder

    class _FailingDrawing(_Drawing):
        def export(self, *, formats):
            self.calls.append(("export", tuple(formats)))
            raise OSError("visual export failed")

    drawing = _FailingDrawing(tmp_path / "part")
    monkeypatch.setattr(builder, "build_drawing", lambda **_kwargs: drawing)

    result = CliRunner().invoke(app, ["source.step", "--out", drawing.out])

    assert result.exit_code == 1
    assert isinstance(result.exception, OSError)
    assert drawing.calls == [("export", ("pdf",))]


@pytest.mark.parametrize("suffix", ["", ".svg", ".dxf", ".pdf", ".png"])
def test_report_uses_the_visual_exports_normalized_stem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    import draftwright.builder as builder

    requested_output = tmp_path / f"part{suffix}"
    drawing = _Drawing(requested_output)
    monkeypatch.setattr(builder, "build_drawing", lambda **_kwargs: drawing)

    result = CliRunner().invoke(
        app,
        ["source.step", "--out", str(requested_output), "--format", "pdf"],
    )

    expected_stem = tmp_path / "part"
    assert result.exit_code == 0, result.output
    assert drawing.calls == [
        ("export", ("pdf",)),
        ("write_report", f"{expected_stem}.draftwright.json"),
    ]
    assert result.output.splitlines() == [
        f"{expected_stem}.pdf",
        f"{expected_stem}.draftwright.json",
    ]


def test_report_failure_propagates_after_printing_visuals_but_not_a_report_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import draftwright.builder as builder

    class _FailingReportDrawing(_Drawing):
        def write_report(self, path):
            self.calls.append(("write_report", path))
            raise OSError("report write failed")

    drawing = _FailingReportDrawing(tmp_path / "part")
    monkeypatch.setattr(builder, "build_drawing", lambda **_kwargs: drawing)

    result = CliRunner().invoke(app, ["source.step", "--out", drawing.out])

    assert result.exit_code == 1
    assert isinstance(result.exception, OSError)
    assert drawing.calls == [
        ("export", ("pdf",)),
        ("write_report", f"{drawing.out}.draftwright.json"),
    ]
    assert result.output.splitlines() == [f"{drawing.out}.pdf"]
