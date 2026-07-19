"""Characterisation tests for direct/generated-script detail-view parity.

These tests deliberately avoid prescribing the implementation.  They capture the
smallest known mismatch: the direct automatic build can escalate crowded shoulders
into a detail view, while the imperative script reconstruction currently cannot.
"""

import runpy
from pathlib import Path

import pytest
from build123d import Align, Box, Cylinder, Pos, Rot, Rotation, export_step

from draftwright import build_drawing, generate_script

# Characterisation is checked in now so the known direct/script gaps have executable
# acceptance criteria, but the group is intentionally dormant until the equality work is
# scheduled. Keep the strict xfails below: removing this module skip should immediately show
# which individual gaps remain (#707 umbrella; #661 details; #426 reconstruction convergence).
pytestmark = pytest.mark.skip(
    reason="temporarily disabled pending generated-script equality work (#707, #661, #426)"
)


def _crowded_shoulders():
    """A narrow tiered block whose 3 mm shoulders require an enlarged view."""
    part = Pos(0, 0, 3) * Box(20, 16, 6)
    z = 6
    for width in (16, 13, 10, 7, 5):
        part += Pos(0, 0, z + 1.5) * Box(width, 12, 3)
        z += 3
    return part


def _turned_shaft(specs):
    """Build a Z-axis shaft from ``(diameter, length)`` segments."""
    shaft = None
    z = 0.0
    for diameter, length in specs:
        segment = Pos(0, 0, z) * Cylinder(
            diameter / 2,
            length,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )
        shaft = segment if shaft is None else shaft + segment
        z += length
    return shaft


def _detail_signature(dwg) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Only the semantic detail-view surface; independent of SVG formatting."""
    views = tuple(sorted(name for name in dwg.views if name.startswith("detail_")))
    annotations = tuple(
        sorted(
            name
            for name in dwg.annotations()
            if name.startswith(("detail_caption_", "dim_detail_"))
        )
    )
    return views, annotations


@pytest.fixture
def crowded_step(tmp_path):
    path = tmp_path / "crowded.step"
    export_step(_crowded_shoulders(), str(path))
    return path


def _scripted_drawing(part, tmp_path, name):
    """Round-trip *part* through the actual generated imperative script."""
    step = tmp_path / f"{name}.step"
    export_step(part, str(step))
    script = generate_script(str(step), out=str(tmp_path / name))
    return step, runpy.run_path(str(Path(script)))["dwg"]


@pytest.mark.timeout(180)
def test_direct_build_detail_fixture_really_triggers(crowded_step):
    """Guard the fixture: direct automatic output must contain a real detail."""
    direct = build_drawing(str(crowded_step), detail_view=True)

    views, annotations = _detail_signature(direct)
    assert views == ("detail_a",)
    assert "detail_caption_A" in annotations
    assert any(name.startswith("dim_detail_a_step") for name in annotations)


@pytest.mark.timeout(240)
@pytest.mark.xfail(
    strict=True,
    reason=(
        "generated imperative scripts use auto_dims=False and do not yet carry "
        "detail-view escalation into their intent reconstruction"
    ),
)
def test_generated_script_matches_direct_detail_view(crowded_step, tmp_path):
    """Target behaviour: executing the script preserves the direct detail output."""
    direct = build_drawing(str(crowded_step), detail_view=True)

    script = generate_script(str(crowded_step), out=str(tmp_path / "scripted"))
    scripted = runpy.run_path(str(Path(script)))["dwg"]

    assert _detail_signature(scripted) == _detail_signature(direct)


@pytest.mark.timeout(300)
@pytest.mark.xfail(
    strict=True,
    reason="generated scripts do not yet reconstruct multiple automatic detail views",
)
def test_generated_script_matches_two_direct_detail_views(tmp_path):
    """Two separated fine-step runs retain distinct DETAIL A/B output."""
    specs = [(4, 1.5), (6, 2.0), (4, 2.5), (3, 22), (6, 1.5), (4, 2.0), (5, 2.5), (2, 22)]
    part = Rotation(0, 90, 0) * _turned_shaft(specs)
    step, scripted = _scripted_drawing(part, tmp_path, "two_details")
    direct = build_drawing(str(step), page="A2", scale=2.0)

    assert {"detail_a", "detail_b"} <= set(direct.views)  # guard the fixture
    assert _detail_signature(scripted) == _detail_signature(direct)


@pytest.mark.timeout(240)
@pytest.mark.xfail(
    strict=True,
    reason="generated scripts do not yet reconstruct turned-head detail escalation",
)
def test_generated_script_matches_direct_turned_head_detail(tmp_path):
    """The turned-head detail route has parity, independently of prismatic details."""
    specs = [(4, 1.5), (6, 2.0), (4, 2.5), (3, 25.0)]
    part = Rotation(0, 90, 0) * _turned_shaft(specs)
    step, scripted = _scripted_drawing(part, tmp_path, "turned_detail")
    direct = build_drawing(str(step), scale=2.0)

    assert "detail_a" in direct.views  # guard the fixture
    assert _detail_signature(scripted) == _detail_signature(direct)


@pytest.mark.timeout(240)
@pytest.mark.xfail(
    strict=True,
    reason="non-Z hole location dimensions are still auto-pass-only in generated scripts",
)
def test_generated_script_matches_direct_side_drilled_locations(tmp_path):
    """Compare actual location annotations, not only the emitter's gap comment."""
    part = (
        Box(120, 90, 40)
        - Pos(0, 0, 5) * Rot(0, 90, 0) * Cylinder(5, 120)
        - Pos(0, 0, -8) * Rot(90, 0, 0) * Cylinder(5, 90)
    )
    step, scripted = _scripted_drawing(part, tmp_path, "side_drilled")
    direct = build_drawing(str(step))

    def locations(dwg):
        return tuple(sorted(n for n in dwg.annotations() if n.startswith("dim_loc_")))

    assert locations(direct)  # guard the fixture
    assert locations(scripted) == locations(direct)


@pytest.mark.timeout(240)
@pytest.mark.xfail(
    strict=True,
    reason="rotational OD and centreline furniture are not represented by script intents",
)
def test_generated_script_matches_direct_rotational_furniture(tmp_path):
    """A plain cylinder characterises the smallest rotational reconstruction gap."""
    step, scripted = _scripted_drawing(Cylinder(15, 40), tmp_path, "rotational")
    direct = build_drawing(str(step))

    names = {"dim_od", "centerline_front", "centerline_side"}
    direct_furniture = names & set(direct.annotations())
    scripted_furniture = names & set(scripted.annotations())
    assert direct_furniture == names  # guard the fixture
    assert scripted_furniture == direct_furniture


@pytest.mark.timeout(240)
@pytest.mark.xfail(
    strict=True,
    reason=(
        "the generated reconstruction attempts an end-on Y-axis step-length dimension "
        "and raises because its projected endpoints coincide"
    ),
)
def test_generated_script_matches_direct_y_axis_turned_diameter_policy(tmp_path):
    """Y-axis turned output runs and follows the direct no-diameter policy."""
    part = Rotation(90, 0, 0) * _turned_shaft([(20, 20), (14, 15)])
    step, scripted = _scripted_drawing(part, tmp_path, "y_turned")
    direct = build_drawing(str(step))

    def diameter_callouts(dwg):
        return tuple(sorted(n for n in dwg.annotations() if n.startswith("m_dia")))

    assert diameter_callouts(scripted) == diameter_callouts(direct) == ()
