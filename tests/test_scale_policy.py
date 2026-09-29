"""Explicit and automatic drawing scale policy behavior."""

import pytest
from build123d import Box
from test_issue_1146_scale_completeness import _short_step_ladder

import draftwright.builder as builder
from draftwright import build_drawing
from draftwright._core import _MIN_VIEW_MM
from draftwright._warnings import ScaleCompletenessWarning
from draftwright.layout_selection import drawing_record


def test_fallback_registry_warning_invalidates_build_lint_snapshot_issue_1945(monkeypatch):
    original_lint = builder.Drawing.lint
    original_safety = builder.candidate_safety_evidence
    physical_calls = []
    manifest_warnings = []

    def counted_lint(self, *, physical=True):
        issues = original_lint(self, physical=physical)
        if physical:
            codes = tuple(issue.code for issue in issues)
            physical_calls.append((self, codes, bool(self.registry.issues)))
        return issues

    def safety_with_manifest(drawing):
        assert drawing.scale_decision["status"] == "fallback"
        assert any(issue.code == "scale_fallback_applied" for issue in drawing.registry.issues)
        manifest_warnings.append(drawing_record(drawing)["manifest"]["lint"]["warnings"])
        return original_safety(drawing)

    monkeypatch.setattr(builder.Drawing, "lint", counted_lint)
    monkeypatch.setattr(builder, "candidate_safety_evidence", safety_with_manifest)
    with pytest.warns(ScaleCompletenessWarning, match="complete fallback scale 0.5"):
        drawing = build_drawing(
            _short_step_ladder(), page="A4", detail_view=False, pmi="off", scale=1.0
        )

    assert any(
        "step_dim_dropped" in codes for owner, codes, _ in physical_calls if owner is not drawing
    )
    assert manifest_warnings == [1]
    fallback_calls = [
        (codes, had_issue) for owner, codes, had_issue in physical_calls if owner is drawing
    ]
    assert len(fallback_calls) == 2  # before and after the registry mutation
    assert "scale_fallback_applied" not in fallback_calls[0][0]
    assert "scale_fallback_applied" in fallback_calls[1][0]
    assert drawing_record(drawing)["manifest"]["lint"]["warnings"] == 1


class TestScaleMinimum:
    """An explicit scale below the legibility floor is warned about and rendered."""

    def test_explicit_illegible_scale_warns_and_renders(self):
        part = Box(680, 860, 80)
        with pytest.warns(UserWarning, match="legibility floor"):
            result = build_drawing(part, scale=0.1, scale_policy="permissive")
        assert result is not None

    def test_warning_suggests_safe_scale(self):
        import re

        part = Box(680, 860, 80)
        with pytest.warns(UserWarning) as record:
            build_drawing(part, scale=0.1, scale_policy="permissive")
        msg = str(record[0].message)
        assert "scale" in msg.lower()
        nums = re.findall(r"\d+\.?\d*", msg)
        safe_scales = [float(n) for n in nums if 0.1 < float(n) < 1.0]
        assert any(s >= _MIN_VIEW_MM / 80 for s in safe_scales)

    def test_degenerate_scale_raises(self):
        part = Box(680, 860, 80)
        with pytest.raises(ValueError, match="geometry degenerates"):
            build_drawing(part, scale=0.001)

    def test_safe_scale_does_not_warn(self):
        import warnings

        part = Box(680, 860, 80)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            result = build_drawing(part, scale=0.2)
        assert result is not None

    def test_auto_scale_thin_part_does_not_raise(self):
        part = Box(80, 50, 8)
        result = build_drawing(part)
        assert result is not None

    def test_inherently_subfloor_part_does_not_warn(self):
        import warnings

        part = Box(3000, 3000, 15)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = build_drawing(part, scale=0.05)
        assert result is not None
        assert not [w for w in caught if "legibility floor" in str(w.message)]

    def test_sheet_explicit_scale_below_floor_is_honoured(self, tmp_path):
        from draftwright import Sheet

        with pytest.warns(UserWarning, match="legibility floor"):
            sheet = Sheet(
                Box(680, 860, 80), scale="1:10", scale_policy="permissive"
            ).auto_dimensions()
            sheet.export(str(tmp_path / "s"))
        assert (tmp_path / "s.pdf").exists()
