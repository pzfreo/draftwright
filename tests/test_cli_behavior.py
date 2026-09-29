"""CLI compatibility and lazy-import behavior."""

import importlib
import json
import os
import subprocess
import sys

import pytest


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
