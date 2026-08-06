import pytest
from build123d import Box, Pos, Rot

from draftwright import build_drawing
from draftwright.model import PocketFeature


def _blind_pocket(x_size=20, y_size=20):
    """A real 20 x 20 x 10 blind recess, unlike #916's original non-cutting tool."""
    return Box(50, 50, 30) - Pos(10, 10, 10) * Box(x_size, y_size, 10)


def _tip_is_on_rim(dwg, view, pocket, tip):
    origin = list(pocket.frame.origin)
    centre = dwg.at(view, *origin)
    long_end = origin.copy()
    long_end["xyz".index(pocket.long_axis)] += pocket.length / 2
    width_end = origin.copy()
    width_end["xyz".index(pocket.width_axis)] += pocket.width / 2
    long_page = dwg.at(view, *long_end)
    width_page = dwg.at(view, *width_end)
    long_v = (long_page[0] - centre[0], long_page[1] - centre[1])
    width_v = (width_page[0] - centre[0], width_page[1] - centre[1])
    tip_v = (tip[0] - centre[0], tip[1] - centre[1])
    long_fraction = sum(a * b for a, b in zip(tip_v, long_v)) / sum(v * v for v in long_v)
    width_fraction = sum(a * b for a, b in zip(tip_v, width_v)) / sum(v * v for v in width_v)
    return (abs(abs(long_fraction) - 1) < 1e-6 and abs(width_fraction) <= 1 + 1e-6) or (
        abs(abs(width_fraction) - 1) < 1e-6 and abs(long_fraction) <= 1 + 1e-6
    )


def test_issue_916_pocket_callout_starts_on_its_rim_and_exits_cleanly():
    dwg = build_drawing(_blind_pocket())
    pockets = [feature for feature in dwg.model().features if isinstance(feature, PocketFeature)]
    assert len(pockets) == 1

    callouts = [
        annotation for name, annotation in dwg.iter_annotations() if name.startswith("m_pocket_")
    ]
    assert len(callouts) == 1
    callout = callouts[0]
    assert callout.label == "20 × 20 × 10 DEEP"
    assert dwg.get_annotation("m_locx0").label == "35"
    assert dwg.get_annotation("m_locy0").label == "35"

    assert _tip_is_on_rim(dwg, "plan", pockets[0], callout.tip), (
        "the pocket leader identifies its centre, not its physical rim"
    )
    assert dwg.lint() == []


@pytest.mark.parametrize(
    "rotation",
    [(0, 0, 0), (0, 90, 0), (90, 0, 0)],
    ids=["z-depth", "x-depth", "y-depth"],
)
def test_pocket_rim_tip_is_axis_independent(rotation):
    # Non-square exercises distinct rim proportions in every orthogonal opening view.
    dwg = build_drawing(Rot(*rotation) * _blind_pocket(22, 14))
    pocket = next(
        feature for feature in dwg.model().features if isinstance(feature, PocketFeature)
    )
    view = {"x": "side", "y": "front", "z": "plan"}[pocket.depth_axis]
    callout = next(
        annotation for name, annotation in dwg.iter_annotations() if name.startswith("m_pocket_")
    )

    assert _tip_is_on_rim(dwg, view, pocket, callout.tip)
    assert dwg.lint() == []


@pytest.mark.parametrize("axis", ["long", "width"], ids=["long-axis", "width-axis"])
def test_approved_pocket_sizes_map_to_the_matching_rim_axis(axis):
    dwg = build_drawing(_blind_pocket(22, 14))
    pocket = next(
        feature for feature in dwg.model().features if isinstance(feature, PocketFeature)
    )
    callout_name = next(name for name in dwg.annotations() if name.startswith("m_pocket_"))
    dwg.remove(callout_name)

    # Exercise the public edit seam. Free-text notes occupy the two viable diagonal
    # corridors; the third also occupies the long-axis corridor, making callout() select
    # the width-axis exit. This proves both non-square dimensions reach the matching
    # physical rim without coupling the test to the renderer's private candidate helper.
    _x0, y0, x1, y1 = dwg.view_bounds("plan")
    centre = dwg.at("plan", *pocket.frame.origin)
    blockers = [(x1 + 25, y1 + 10), (x1 + 25, y0 + 9)]
    if axis == "width":
        blockers.append((x1 + 25, centre[1]))
    for i, position in enumerate(blockers):
        dwg.note("BLOCKER", position, view="plan", name=f"blocker_{i}")

    placed_name = dwg.callout(pocket)
    assert placed_name == callout_name
    callout = dwg.get_annotation(placed_name)

    assert _tip_is_on_rim(dwg, "plan", pocket, callout.tip)
    if axis == "long":
        assert callout.tip[1] == pytest.approx(centre[1])
    else:
        assert callout.tip[0] == pytest.approx(centre[0])
    assert dwg.lint() == []
