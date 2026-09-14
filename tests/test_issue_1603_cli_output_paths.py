"""CLI outputs follow the STEP input unless the caller chooses a destination."""

import os
import runpy
from pathlib import Path

import pytest
from click.utils import strip_ansi
from typer.testing import CliRunner

from draftwright.cli import app


@pytest.mark.parametrize("script", [False, True])
@pytest.mark.parametrize(
    ("source", "options", "expected"),
    [
        ("parts/fixture.step", [], "parts/fixture"),
        ("parts/fixture.step", ["--out-dir", "."], "fixture"),
        (
            "parts/fixture.step",
            ["--out-dir", "new directory/nested"],
            "new directory/nested/fixture",
        ),
        ("parts/fixture.step", ["--out", "revised.pdf"], "revised"),
    ],
)
def test_one_destination_is_forwarded_to_both_routes(
    tmp_path, monkeypatch, script, source, options, expected
):
    import draftwright.builder as builder
    import draftwright.sheet_emit as emitter

    monkeypatch.chdir(tmp_path)
    captured = []

    class DestinationCaptured(Exception):
        pass

    def capture(*args, **kwargs):
        captured.append(kwargs["out"])
        raise DestinationCaptured

    monkeypatch.setattr(builder, "build_drawing", capture)
    monkeypatch.setattr(emitter, "generate_sheet_script", capture)
    result = CliRunner().invoke(app, [source, *options, *(["--script"] if script else [])])
    assert isinstance(result.exception, DestinationCaptured), result.output
    assert captured == [str(tmp_path / expected)]
    if "--out-dir" in options:
        assert (tmp_path / expected).parent.is_dir()


def test_conflicting_destinations_fail_before_loading_the_part(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["missing.step", "--out", "name", "--out-dir", "new"])
    assert result.exit_code == 2
    assert "use either --out or --out-dir" in " ".join(strip_ansi(result.output).split())
    assert not (tmp_path / "new").exists()


@pytest.mark.parametrize("source", ["some_module:part", "model.py:part"])
def test_live_object_script_keeps_a_separate_drawing_basename(tmp_path, monkeypatch, source):
    from types import SimpleNamespace

    import draftwright.sheet_emit as emitter

    monkeypatch.chdir(tmp_path)
    captured = []
    monkeypatch.setattr(
        emitter,
        "_resolve_object_source",
        lambda _: SimpleNamespace(part=object(), seam="part = value", candidates={}),
    )

    def emit(part, **kwargs):
        captured.append(kwargs["out"])
        return f"{kwargs['out']}.py"

    monkeypatch.setattr(emitter, "generate_sheet_script", emit)
    result = CliRunner().invoke(app, [source, "--script"])
    assert result.exit_code == 0, result.output
    assert captured == [str(tmp_path / "drawing")]
    assert result.output.strip() == str(tmp_path / "drawing.py")


@pytest.mark.parametrize("absolute_input", [False, True])
def test_actual_outputs_and_script_replay_stay_beside_the_input(
    tmp_path, monkeypatch, absolute_input
):
    from build123d import Box, export_step

    inputs = tmp_path / "input parts"
    invocation = tmp_path / "invocation"
    replay = tmp_path / "replay"
    for directory in (inputs, invocation, replay):
        directory.mkdir()
    source = inputs / "block.step"
    export_step(Box(30, 20, 10), str(source))
    monkeypatch.chdir(invocation)
    reference = str(source) if absolute_input else os.path.relpath(source, invocation)
    result = CliRunner().invoke(app, [reference, "--format", "all"])
    assert result.exit_code == 0, result.output
    visual = {inputs / f"block.{suffix}" for suffix in ("svg", "dxf", "pdf", "png")}
    report = inputs / "block.draftwright.json"
    assert set(map(Path, result.stdout.splitlines())) == visual | {report}
    assert all(path.is_file() and path.stat().st_size for path in visual | {report})
    assert list(invocation.iterdir()) == []

    generated = CliRunner().invoke(app, [reference, "--script", "--format", "all"])
    assert generated.exit_code == 0, generated.output
    written = set(map(Path, generated.stdout.splitlines()))
    script = inputs / "block.py"
    assert script in written and len(written) == 2
    assert all(path.is_file() for path in written)
    assert list(invocation.iterdir()) == []
    for path in visual:
        path.unlink()
    monkeypatch.chdir(replay)
    runpy.run_path(str(script))
    assert all(path.is_file() and path.stat().st_size for path in visual)
    assert list(replay.iterdir()) == []
