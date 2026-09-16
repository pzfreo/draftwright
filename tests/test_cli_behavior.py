"""CLI compatibility and lazy-import behavior."""

import os
import subprocess
import sys

import pytest


@pytest.mark.smoke
def test_make_drawing_module_entrypoint_runs_cli_help():
    """The compat facade remains executable as ``python -m draftwright.make_drawing``."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "cp1252"
    result = subprocess.run(
        [
            sys.executable,
            "-W",
            "error::RuntimeWarning",
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
        [sys.executable, "-m", "draftwright.make_drawing", "--version"],
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


def test_lazy_public_api_preserves_make_drawing_identity():
    """The lazy package __init__ (#313) must still expose the public API, and
    `draftwright.make_drawing` must stay the FUNCTION even after the compat
    submodule of the same name is imported and would otherwise shadow it."""
    code = (
        "import types, draftwright as d; "
        "from draftwright import make_drawing, build_drawing, Drawing, choose_scale; "
        "assert callable(make_drawing) and not isinstance(make_drawing, types.ModuleType); "
        "assert d.make_drawing is make_drawing; "
        "import draftwright.make_drawing; "  # provoke the shadowing path
        "assert callable(d.make_drawing) and not isinstance(d.make_drawing, types.ModuleType); "
        "print('ok')"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip() == "ok", (
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
