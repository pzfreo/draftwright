"""Recognition + emit for the pocket-pattern kind (#841 outcome 1, PR 2/2).

PR 1 landed the declared path (`pocket_pattern`/`Sheet.pocket_pattern`/`render_pocket_patterns`).
This pins the RECOGNITION half: the unified aggregate groups identical pockets into one
`SectionRecessArray`/`SectionRecessGrid`, `build_part_model` emits ONE `PocketPatternFeature` and excludes the
member pockets, and `sheet_emit` round-trips it. The #837 tuner-jig STEP (five blind obround
pockets on one centreline) is the end-to-end regression: it must render ONE grouped
``5× W×L×D DEEP`` callout, not five competing per-pocket size dims.
"""

from pathlib import Path

import pytest
from build123d import Align, Box, Pos, import_step
from quiddity import SectionRecessArray, SectionRecessGrid, build_raw_recognition_result

from draftwright.make_drawing import build_drawing
from draftwright.model import pocket, pocket_pattern  # noqa: F401  (declared-path symmetry)
from draftwright.model.detect import build_part_model

_FIXTURE = Path(__file__).parent / "fixtures" / "tuner_jig_blind_obround_pockets.step"


def _pocket_row(n=4, pitch=30.0):
    part = Box(30, pitch * (n + 1), 20)
    for i in range(n):  # n identical blind pockets on one Y centreline
        part -= Pos(0, (i - (n - 1) / 2) * pitch, 7) * Box(10, 12, 6)
    return part


def _pocket_grid(nx=2, ny=3, px=40.0, py=30.0):
    part = Box(px * (nx + 2), py * (ny + 1), 20)
    for i in range(nx):
        for j in range(ny):
            part -= Pos((i - (nx - 1) / 2) * px, (j - (ny - 1) / 2) * py, 7) * Box(8, 10, 6)
    return part


def test_recognise_linear_pocket_array():
    result = build_raw_recognition_result(_pocket_row(n=4, pitch=30.0))
    assert len(result.section_recesses) == 4
    (pattern,) = result.section_recess_patterns
    assert type(pattern) is SectionRecessArray
    assert len(pattern.members) == 4
    assert pattern.pitch == 30.0
    assert abs(pattern.direction[1]) == 1.0


def test_recognise_pocket_grid():
    (pattern,) = build_raw_recognition_result(_pocket_grid(2, 3)).section_recess_patterns
    assert type(pattern) is SectionRecessGrid
    assert len(pattern.members) == 6
    assert {pattern.rows, pattern.cols} == {2, 3}


def test_two_pockets_are_not_a_pattern():
    result = build_raw_recognition_result(_pocket_row(n=2, pitch=30.0))
    assert len(result.section_recesses) == 2
    assert result.section_recess_patterns == ()


def test_different_size_pockets_do_not_group():
    part = Box(30, 150, 20)
    part -= Pos(0, -45, 7) * Box(10, 12, 6)
    part -= Pos(0, 0, 7) * Box(14, 12, 6)
    part -= Pos(0, 45, 7) * Box(10, 18, 6)
    result = build_raw_recognition_result(part)
    assert len(result.section_recesses) == 3
    assert result.section_recess_patterns == ()


def _stepped_pocket_row(floors):
    part = Pos(0, 0, -10) * Box(30, 120, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
    for cy, floor in zip((-30, 0, 30), floors):
        part += Pos(0, cy, 0) * Box(
            30, 30, floor + 6, align=(Align.CENTER, Align.CENTER, Align.MIN)
        )
    for cy, floor in zip((-30, 0, 30), floors):
        part -= Pos(0, cy, floor + 3) * Box(10, 12, 6)
    assert len(part.solids()) == 1
    return part


def test_non_coplanar_aligned_pockets_do_not_merge():
    staggered = build_raw_recognition_result(_stepped_pocket_row((0, 5, 10)))
    coplanar = build_raw_recognition_result(_stepped_pocket_row((0, 0, 0)))
    assert len(staggered.section_recesses) == len(coplanar.section_recesses) == 3
    assert {recess.geometry.run_interval for recess in staggered.section_recesses} == {
        (0, 6),
        (5, 11),
        (10, 16),
    }
    assert staggered.section_recess_patterns == ()
    assert len(coplanar.section_recess_patterns) == 1


def _opposed_pocket_row(signs):
    pieces = [
        Pos(0, cy, 3 if sign > 0 else 13) * Box(30, 30, 16)
        for cy, sign in zip((-30, 0, 30), signs)
    ]
    part = pieces[0] + pieces[1] + pieces[2]
    for cy in (-30, 0, 30):
        part -= Pos(0, cy, 8) * Box(10, 12, 6)
    assert len(part.solids()) == 1
    return part


def test_opposite_facing_pockets_do_not_merge():
    mixed = build_raw_recognition_result(_opposed_pocket_row((1, -1, 1)))
    same = build_raw_recognition_result(_opposed_pocket_row((1, 1, 1)))
    assert len(mixed.section_recesses) == len(same.section_recesses) == 3
    assert {recess.geometry.run_interval for recess in mixed.section_recesses} == {(5, 11)}
    assert {recess.geometry.ends.low.condition for recess in mixed.section_recesses} == {
        "open",
        "capped",
    }
    assert mixed.section_recess_patterns == ()
    assert len(same.section_recess_patterns) == 1


def test_edge_anchored_and_interior_pockets_do_not_form_one_pattern():
    # Three equal 15 x 15 x 6 recesses on a diagonal lattice. The last crosses
    # the stock corner; its open profile must not join the two closed profiles.
    part = Box(100, 100, 20)
    for xy in (-17.5, 12.5):
        part -= Pos(xy, xy, 7) * Box(15, 15, 6)
    part -= Pos(45, 45, 7) * Box(20, 20, 6)
    assert len(part.solids()) == 1
    result = build_raw_recognition_result(part)
    assert len(result.section_recesses) == 3
    assert sorted(source.classification.feature_kind for source in result.section_recesses) == [
        "edge_open_recess",
        "pocket",
        "pocket",
    ]
    assert result.section_recess_patterns == ()


def test_injected_value_equal_inventory_resolves_its_own_pattern_members():
    import dataclasses

    part = _pocket_row(n=4, pitch=30.0)
    result = build_raw_recognition_result(part)
    copied = tuple(dataclasses.replace(source) for source in result.section_recesses)
    patterns = tuple(dataclasses.replace(pattern) for pattern in result.section_recess_patterns)
    assert all(a == b and a is not b for a, b in zip(copied, result.section_recesses))
    model = build_part_model(part, section_recesses=copied, section_recess_patterns=patterns)
    kinds = [feature.kind for feature in model.features]
    assert kinds.count("pocket_pattern") == 1
    assert kinds.count("pocket") == 0


def test_injected_recesses_require_their_explicit_pattern_inventory():
    result = build_raw_recognition_result(_pocket_row(n=4, pitch=30.0))
    with pytest.raises(
        ValueError, match="injected section recesses require explicit section_recess_patterns"
    ):
        build_part_model(Box(200, 200, 20), section_recesses=result.section_recesses)


def test_build_part_model_groups_and_excludes_members():
    part = _pocket_row(n=4, pitch=30.0)
    pm = build_part_model(part)
    kinds = [f.kind for f in pm.features]
    assert kinds.count("pocket_pattern") == 1
    assert kinds.count("pocket") == 0  # members folded into the pattern, not emitted individually
    pat = next(f for f in pm.features if f.kind == "pocket_pattern")
    assert pat.count == 4
    assert pat.member.width == 10.0 and pat.member.length == 12.0 and pat.member.depth == 6.0
    dwg = build_drawing(part)
    assert not [i for i in dwg.lint() if i.code == "unrecognised_defining_geometry"]


def test_sheet_emit_round_trips_the_pattern(tmp_path):
    from draftwright.sheet_emit import generate_sheet_script

    py = generate_sheet_script(_pocket_row(n=4, pitch=30.0), out=str(tmp_path / "pp_emit_rt"))
    src = Path(py).read_text(encoding="utf-8")  # the script carries non-ASCII (ø/×); Windows
    line = next(ln for ln in src.splitlines() if "sheet.pocket_pattern(" in ln)
    # declare rejects members= — the emit must use at=/pitch=/direction= instead
    assert "members=" not in line
    assert 'kind="linear"' in line and "count=4" in line
    assert "at=" in line and "pitch=" in line and "direction=" in line


@pytest.mark.xfail(
    strict=True,
    reason="Quiddity 0.2.2 known limitation: https://github.com/pzfreo/quiddity/issues/536",
)
def test_tuner_jig_fixture_recognised_as_one_pattern():
    # #837/#841: five blind obround pockets on one centreline collapse to ONE SectionRecessArray.
    part = import_step(str(_FIXTURE))
    pats = build_raw_recognition_result(part).section_recess_patterns
    assert len(pats) == 1 and isinstance(pats[0], SectionRecessArray)
    assert len(pats[0].members) == 5


@pytest.mark.xfail(
    strict=True,
    reason="Quiddity 0.2.2 known limitation: https://github.com/pzfreo/quiddity/issues/536",
)
def test_tuner_jig_renders_one_grouped_callout_not_five():
    # the payoff: the imported STEP renders ONE `5× 7.9 × 13.6 × 19 DEEP` callout + a pitch dim,
    # not five competing per-pocket size dims (#841 outcome 1).
    part = import_step(str(_FIXTURE))
    dwg = build_drawing(part)
    names = dwg.annotations()
    callouts = [n for n in names if n.startswith("m_pocketpat")]
    assert len(callouts) == 1
    assert dwg.get_annotation(callouts[0]).label == "5× 7.9 × 13.6 × 19 DEEP"
    # the individual member pockets are NOT separately called out
    assert not [n for n in names if n.startswith("m_pocket_")]
    assert not [x for x in dwg.lint() if x.code == "annotation_out_of_bounds"]
