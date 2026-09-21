"""Public feature-leader region policy across CLI, APIs, and the Sheet DSL."""

from __future__ import annotations

from pathlib import Path

import pytest
from build123d import Box, Cylinder, Pos
from typer.testing import CliRunner

from draftwright import LeaderRegionPolicy, Sheet, build_drawing
from draftwright.cli import app
from draftwright.leader_policy import effective_leader_region_policy, leader_region_policy
from draftwright.model import HoleFeature
from draftwright.sheet_emit import generate_sheet_script


def _one_hole():
    return Box(80, 60, 10) - Pos(25, 15, 0) * Cylinder(4, 10)


def test_public_policy_preserves_family_eligibility_and_authored_side_constraints():
    assert leader_region_policy("auto") is LeaderRegionPolicy.AUTO
    assert (
        effective_leader_region_policy(LeaderRegionPolicy.AUTO, "interior")
        is LeaderRegionPolicy.INTERIOR
    )
    assert (
        effective_leader_region_policy(LeaderRegionPolicy.AUTO, "exterior")
        is LeaderRegionPolicy.EXTERIOR
    )
    # An exterior-only producer includes families with no interior proof and a
    # feature carrying an authored side. A document policy cannot manufacture
    # interior authority for either case.
    assert (
        effective_leader_region_policy(LeaderRegionPolicy.EXTERIOR, "interior")
        is LeaderRegionPolicy.EXTERIOR
    )


@pytest.mark.parametrize("entry", [build_drawing, Sheet, generate_sheet_script])
def test_every_public_front_door_rejects_an_unknown_policy(entry, tmp_path):
    with pytest.raises(ValueError, match="leader_region must be"):
        entry(tmp_path / "missing.step", leader_region="near-the-middle")


@pytest.mark.timeout(120)
@pytest.mark.parametrize(
    ("policy", "expected_region"),
    [("auto", "interior"), ("interior", "interior"), ("exterior", "exterior")],
)
def test_build_policy_selects_only_the_requested_proved_region(policy, expected_region):
    drawing = build_drawing(
        _one_hole(),
        leader_region=policy,
        scale=1,
        page="A3",
        scale_policy="permissive",
    )

    callout = drawing.get_annotation("hc_plan0")
    assert callout is not None
    assert callout._dw_candidate_region == expected_region
    assert drawing.leader_region == policy
    assert not [issue for issue in drawing.lint() if issue.code == "callout_dropped"]


@pytest.mark.timeout(120)
def test_sheet_dsl_forwards_exterior_compatibility_policy_to_the_shared_solve():
    drawing = Sheet.from_part(
        _one_hole(),
        leader_region="exterior",
        scale=1,
        page="A3",
        scale_policy="permissive",
    ).build()

    callout = drawing.get_annotation("hc_plan0")
    assert callout is not None
    assert callout._dw_candidate_region == "exterior"
    assert drawing.leader_region == "exterior"


@pytest.mark.timeout(120)
def test_authored_side_remains_exterior_under_an_interior_document_policy():
    sheet = Sheet.from_part(
        _one_hole(),
        leader_region="interior",
        scale=1,
        page="A3",
        scale_policy="permissive",
    )
    hole = next(feature for feature in sheet.features if isinstance(feature, HoleFeature))
    sheet.dimension(hole, "bore.diameter", side="left")

    callout = sheet.build().get_annotation("hc_plan0")
    assert callout is not None
    assert callout._dw_candidate_region == "exterior"


def test_cli_forwards_the_region_policy_to_a_rendered_build(monkeypatch):
    import draftwright.builder as builder

    forwarded = []

    class _Drawing:
        out = "out"

        def export(self, *, formats):
            return {name: f"out.{name}" for name in formats}

        def write_report(self, path):
            return path

    def capture(*_args, **kwargs):
        forwarded.append(kwargs)
        return _Drawing()

    monkeypatch.setattr(builder, "build_drawing", capture)
    result = CliRunner().invoke(
        app,
        ["part.step", "--leader-region", "exterior", "--format", "svg", "--no-report"],
    )

    assert result.exit_code == 0, result.output
    assert forwarded[0]["leader_region"] == "exterior"


@pytest.mark.timeout(120)
def test_generated_script_retains_nondefault_policy_and_omits_default(tmp_path):
    exterior = Path(
        generate_sheet_script(
            _one_hole(),
            out=str(tmp_path / "exterior"),
            leader_region="exterior",
            scale=1,
            page="A3",
            formats=(),
            inspect=False,
        )
    ).read_text(encoding="utf-8")
    automatic = Path(
        generate_sheet_script(
            _one_hole(),
            out=str(tmp_path / "auto"),
            scale=1,
            page="A3",
            formats=(),
            inspect=False,
        )
    ).read_text(encoding="utf-8")

    exterior_ctor = next(
        line for line in exterior.splitlines() if line.startswith("sheet = Sheet(")
    )
    automatic_ctor = next(
        line for line in automatic.splitlines() if line.startswith("sheet = Sheet(")
    )
    assert "leader_region='exterior'" in exterior_ctor
    assert "leader_region=" not in automatic_ctor
