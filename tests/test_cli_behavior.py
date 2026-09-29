"""CLI compatibility, lazy imports, and output destinations."""

import importlib
import json
import os
import re
import runpy
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from draftwright.cli import app


@pytest.mark.smoke
def test_make_drawing_module_entrypoint_runs_cli_help():
    """The compat facade remains executable as ``python -m draftwright.make_drawing``."""
    from draftwright import builder
    from draftwright import make_drawing as public_make_drawing

    with pytest.warns(DeprecationWarning) as caught:
        facade = (
            importlib.reload(sys.modules["draftwright.make_drawing"])
            if "draftwright.make_drawing" in sys.modules
            else importlib.import_module("draftwright.make_drawing")
        )
    assert len(caught) == 1
    assert "draftwright.builder" in str(caught[0].message)
    assert "0.6.0" in str(caught[0].message)
    assert facade.build_drawing is builder.build_drawing
    assert "generate_script" not in vars(facade)
    assert public_make_drawing is facade.make_drawing
    assert importlib.import_module("draftwright").make_drawing is public_make_drawing

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "cp1252"
    result = subprocess.run(
        [
            sys.executable,
            "-W",
            "error::RuntimeWarning",
            "-W",
            "always::DeprecationWarning",
            "-m",
            "draftwright.make_drawing",
            "--help",
        ],
        capture_output=True,
        env=env,
        text=True,
        encoding="cp1252",  # match the child's explicit PYTHONIOENCODING
    )

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "draftwright.make_drawing is deprecated" in result.stderr
    assert "Removed in 0.6.0" in result.stderr
    assert "Usage:" in result.stdout  # Typer/rich help capitalises (was argparse "usage:")
    assert "step_file" in result.stdout
    # Rich degrades unsupported box drawing on cp1252 streams. Long flag names may
    # use a representable ellipsis, so safe cp1252 output need not be entirely ASCII.
    assert not any(glyph in result.stdout for glyph in "╭╮╰╯│─")


def test_cli_version_reports_installed_version():
    """``--version`` prints the installed distribution version (the PyPI version
    once pip-installed) and exits cleanly, without needing a STEP file."""
    from importlib.metadata import version as _pkg_version

    result = subprocess.run(
        [sys.executable, "-c", "from draftwright.cli import app; app()", "--version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert result.stdout.strip() == f"draftwright {_pkg_version('draftwright')}"


def test_cli_import_does_not_load_the_engine():
    """Importing the CLI must not pull in build123d/OCP (#313). Shell completion,
    --help and --version import this module on every invocation; loading the
    ~5 s CAD kernel there made each TAB press take ~6 s. Guard the lazy boundary:
    the engine is imported only on the actual build path, in a fresh process so
    other tests' imports can't mask a regression."""
    code = (
        "import sys, draftwright.cli; "
        "heavy = [m for m in ('build123d', 'OCP') if m in sys.modules]; "
        "print(','.join(heavy)); sys.exit(1 if heavy else 0)"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, (
        f"`import draftwright.cli` eagerly loaded: {result.stdout.strip()}"
    )


def test_cli_inherits_automatic_detail_default(monkeypatch):
    """The CLI must not restore the historical opt-out while delegating to the engine."""
    from typer.testing import CliRunner

    import draftwright.builder as builder
    from draftwright.cli import app

    forwarded = []

    class _Drawing:
        out = "out"
        annotation_scheme_decision = {"pre_render_choice": {}}

        def export(self, *, formats):
            return {name: f"out.{name}" for name in formats}

        def write_report(self, path):
            return path

    def _build(*args, **kwargs):
        forwarded.append(kwargs)
        return _Drawing()

    monkeypatch.setattr(builder, "build_drawing", _build)
    result = CliRunner().invoke(app, ["part.step", "--format", "svg"])

    assert result.exit_code == 0, result.output
    assert "detail_view" not in forwarded[0], "omission deliberately inherits the True default"


def test_cli_script_and_render_forward_the_same_drawing_options(tmp_path, monkeypatch):
    """The two front doors agree on authored drawing choices before source-specific controls."""
    from typer.testing import CliRunner

    import draftwright.builder as builder
    import draftwright.sheet_emit as emitter
    from draftwright.cli import app

    forwarded = {}

    class _Drawing:
        annotation_scheme_decision = {}

        def export(self, *, formats):
            return {name: str(tmp_path / f"part.{name}") for name in formats}

    def build_drawing(**kwargs):
        forwarded["render"] = kwargs
        return _Drawing()

    def generate_sheet_script(_source, **kwargs):
        forwarded["script"] = kwargs
        return str(tmp_path / "part.py")

    monkeypatch.setattr(builder, "build_drawing", build_drawing)
    monkeypatch.setattr(emitter, "generate_sheet_script", generate_sheet_script)
    options = [
        "source.step",
        "--out",
        str(tmp_path / "part"),
        "--no-report",
        "--annotation-layout",
        "estimated-strips",
        "--scale",
        "2",
        "--scale-policy",
        "strict",
        "--projection",
        "first",
        "--frame",
        "--margin-left",
        "15",
        "--leader-region",
        "exterior",
        "--pmi",
        "annotate",
        "--format",
        "svg,pdf",
    ]
    render = CliRunner().invoke(app, options)
    script = CliRunner().invoke(app, [*options, "--script"])

    assert render.exit_code == script.exit_code == 0, (render.output, script.output)
    render_args = forwarded["render"]
    script_args = forwarded["script"]
    assert render_args.pop("step_file") == "source.step"
    assert render_args.pop("pmi") == "annotate"
    assert script_args.pop("pmi") == "annotate"
    assert script_args.pop("formats") == ("svg", "pdf")
    assert script_args.pop("inspect") is False
    assert script_args["scale"] == 2
    assert script_args["scale_policy"] == "strict"
    assert script_args["projection"] == "first"
    assert script_args["frame"] is True
    assert script_args["margin_left"] == 15
    assert script_args["leader_region"] == "exterior"
    assert script_args == render_args
    assert render.output.splitlines() == [str(tmp_path / "part.svg"), str(tmp_path / "part.pdf")]
    assert script.output.splitlines() == [str(tmp_path / "part.py")]


def test_cancelled_build_keeps_diagnostic_and_exit_code(monkeypatch):
    from typer.testing import CliRunner

    import draftwright.builder as builder
    from draftwright.cli import app
    from draftwright.progress import BuildCancelled

    diagnostic = {"stage": "views", "reason": "cancelled"}

    def cancel(**_kwargs):
        raise BuildCancelled(diagnostic)

    monkeypatch.setattr(builder, "build_drawing", cancel)
    result = CliRunner().invoke(app, ["source.step", "--no-progress"])

    assert result.exit_code == 130
    assert result.stdout == ""
    assert json.loads(result.stderr) == diagnostic


def test_lazy_public_api_preserves_make_drawing_identity():
    """The lazy package __init__ (#313) exposes the public function."""
    code = (
        "import types, draftwright as d; "
        "from draftwright import make_drawing, build_drawing, Drawing, choose_scale; "
        "assert callable(make_drawing) and not isinstance(make_drawing, types.ModuleType); "
        "assert d.make_drawing is make_drawing; "
        "print('ok')"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip() == "ok", (
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


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
def test_one_destination_is_forwarded_to_both_routes_issue_1603(
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


def test_conflicting_destinations_fail_before_loading_the_part_issue_1603(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["missing.step", "--out", "name", "--out-dir", "new"])
    assert result.exit_code == 2
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    assert "use either --out or --out-dir" in " ".join(plain.split())
    assert not (tmp_path / "new").exists()


@pytest.mark.parametrize("source", ["some_module:part", "model.py:part"])
def test_live_object_script_keeps_a_separate_drawing_basename_issue_1603(
    tmp_path, monkeypatch, source
):
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
def test_actual_outputs_and_script_replay_stay_beside_the_input_issue_1603(
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
