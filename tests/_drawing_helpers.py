"""Shared helpers for drawing-level tests."""

from pathlib import Path
from unittest.mock import patch

from build123d import export_step

from draftwright import Drawing
from draftwright.sheet_emit import generate_sheet_script


def execute_sheet_script_without_export(source, filename="<generated sheet>"):
    """Execute generated Sheet code and return its drawing without serializing files."""
    captured = []

    def capture(drawing, *_args, **_kwargs):
        captured.append(drawing)
        return {}

    with patch.object(Drawing, "export", capture):
        exec(compile(source, filename, "exec"), {})  # noqa: S102 — our own generated script
    assert len(captured) == 1
    return captured[0]


def sheet_script_drawing(part, tmp_path, name, **emit_kw):
    """Emit and run a Sheet script, returning its source and built drawing."""
    step = tmp_path / f"{name}.step"
    export_step(part, str(step))
    py = generate_sheet_script(str(step), out=str(tmp_path / name), **emit_kw)
    source = Path(py).read_text(encoding="utf-8")
    return source, execute_sheet_script_without_export(source, py)


def ink_crossings_named(dwg, expected):
    """Assert the sheet's ink crossings are exactly *expected*, then return other lint.

    Filtering the whole ``annotation_ink_overlap`` code would let a sheet gain new
    crossings unnoticed. Naming the expected ``(crosser, crossed)`` pairs keeps these
    assertions sharp while allowing explicitly documented crossings.
    """
    crossings = [i for i in dwg.lint() if i.code == "annotation_ink_overlap"]
    expected = list(expected)
    unmatched = []
    seen = set()
    for issue in crossings:
        for pair in expected:
            if (
                f"'{pair[0]}' draws" in issue.message
                and f"through the label '{pair[1]}'" in issue.message
            ):
                seen.add(pair)
                break
        else:
            unmatched.append(issue.message)
    assert not unmatched, f"unexpected ink crossings: {unmatched}"
    assert seen == set(expected), (
        f"ink crossings changed: expected {sorted(expected)}, matched {sorted(seen)}"
    )
    assert len(crossings) == len(expected), (
        f"expected {len(expected)} crossings, got {len(crossings)}"
    )
    return [i for i in dwg.lint() if i.code != "annotation_ink_overlap"]
