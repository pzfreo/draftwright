"""#1438 — report persistence is explicit, deterministic, and atomic."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from build123d import Box, Pos

from draftwright import ReportUnavailableError, build_drawing
from draftwright import reporting as reporting_module


def _through_step_part():
    return Box(40, 30, 20) - Pos(15, 10, 0) * Box(20, 20, 30)


def test_write_report_persists_the_exact_in_memory_document(tmp_path: Path) -> None:
    drawing = build_drawing(_through_step_part(), reproducible=True)
    destination = tmp_path / "part.draftwright.json"

    returned = drawing.write_report(destination)

    assert returned == str(destination)
    assert destination.read_bytes().endswith(b"\n")
    assert json.loads(destination.read_text(encoding="utf-8")) == drawing.report()
    assert list(tmp_path.iterdir()) == [destination]


def test_repeated_writes_are_byte_deterministic(tmp_path: Path) -> None:
    drawing = build_drawing(_through_step_part(), reproducible=True)
    destination = tmp_path / "part.draftwright.json"

    drawing.write_report(destination)
    first = destination.read_bytes()
    drawing.write_report(destination)

    assert destination.read_bytes() == first


def test_valid_near_name_limit_destination_does_not_overflow_temporary_name(
    tmp_path: Path,
) -> None:
    try:
        name_max = os.pathconf(tmp_path, "PC_NAME_MAX")
    except (AttributeError, OSError, ValueError):
        pytest.skip("filesystem component limit is not available")
    suffix = ".json"
    destination = tmp_path / ("r" * (name_max - len(suffix)) + suffix)
    drawing = build_drawing(_through_step_part())

    drawing.write_report(destination)

    assert json.loads(destination.read_text(encoding="utf-8")) == drawing.report()
    assert list(tmp_path.iterdir()) == [destination]


def test_report_refusal_cannot_touch_an_existing_destination(tmp_path: Path) -> None:
    drawing = build_drawing(_through_step_part(), model=[])
    destination = tmp_path / "part.draftwright.json"
    destination.write_text("keep me\n", encoding="utf-8")

    with pytest.raises(ReportUnavailableError):
        drawing.write_report(destination)

    assert destination.read_text(encoding="utf-8") == "keep me\n"
    assert list(tmp_path.iterdir()) == [destination]


def test_non_finite_json_cannot_touch_an_existing_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    drawing = build_drawing(_through_step_part())
    destination = tmp_path / "part.draftwright.json"
    destination.write_text("keep me\n", encoding="utf-8")
    monkeypatch.setattr(drawing, "report", lambda: {"invalid": float("nan")})

    with pytest.raises(ValueError, match="Out of range float values"):
        drawing.write_report(destination)

    assert destination.read_text(encoding="utf-8") == "keep me\n"
    assert list(tmp_path.iterdir()) == [destination]


def test_failed_atomic_replace_keeps_the_old_file_and_cleans_the_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    drawing = build_drawing(_through_step_part())
    destination = tmp_path / "part.draftwright.json"
    destination.write_text("old\n", encoding="utf-8")

    def fail_replace(_source, _destination):
        raise OSError("replace failed")

    monkeypatch.setattr(reporting_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        drawing.write_report(destination)

    assert destination.read_text(encoding="utf-8") == "old\n"
    assert list(tmp_path.iterdir()) == [destination]


def test_cleanup_failure_does_not_mask_the_primary_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    drawing = build_drawing(_through_step_part())
    destination = tmp_path / "part.draftwright.json"
    destination.write_text("old\n", encoding="utf-8")
    original_unlink = Path.unlink

    def fail_replace(_source, _destination):
        raise OSError("replace failed")

    def fail_cleanup(_path, *, missing_ok=False):
        raise PermissionError("cleanup failed")

    monkeypatch.setattr(reporting_module.os, "replace", fail_replace)
    monkeypatch.setattr(Path, "unlink", fail_cleanup)
    with pytest.raises(OSError, match="replace failed"):
        drawing.write_report(destination)

    assert destination.read_text(encoding="utf-8") == "old\n"
    temporary = next(path for path in tmp_path.iterdir() if path != destination)
    original_unlink(temporary)


def test_missing_parent_is_not_created(tmp_path: Path) -> None:
    drawing = build_drawing(_through_step_part())
    destination = tmp_path / "missing" / "part.draftwright.json"

    with pytest.raises(FileNotFoundError):
        drawing.write_report(destination)

    assert not destination.parent.exists()
