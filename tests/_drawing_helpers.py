"""Shared helpers for drawing-level tests."""

from pathlib import Path
from unittest.mock import patch

from build123d import export_step

from draftwright import Drawing
from draftwright.sheet_emit import generate_sheet_script


def sheet_script_drawing(part, tmp_path, name, **emit_kw):
    """Emit and run a Sheet script, returning its source and built drawing."""
    step = tmp_path / f"{name}.step"
    export_step(part, str(step))
    py = generate_sheet_script(str(step), out=str(tmp_path / name), **emit_kw)
    source = Path(py).read_text(encoding="utf-8")
    captured = {}
    with patch.object(Drawing, "export", lambda self, *a, **k: captured.setdefault("dwg", self)):
        exec(compile(source, py, "exec"), {})  # noqa: S102 — our own generated script
    return source, captured["dwg"]
