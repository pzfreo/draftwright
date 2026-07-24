"""Recognition + emit for the pocket-pattern kind (#841 outcome 1, PR 2/2).

PR 1 landed the declared path (`pocket_pattern`/`Sheet.pocket_pattern`/`render_pocket_patterns`).
This pins the RECOGNITION half: `recognise_pocket_patterns` groups identical pockets into one
`PocketArray`/`PocketGrid`, `build_part_model` emits ONE `PocketPatternFeature` and excludes the
member pockets, and `sheet_emit` round-trips it. The #837 tuner-jig STEP (five blind obround
pockets on one centreline) is the end-to-end regression: it must render ONE grouped
``5× W×L×D DEEP`` callout, not five competing per-pocket size dims.
"""

from pathlib import Path

from build123d import Box, Pos, import_step

from draftwright.make_drawing import build_drawing
from draftwright.model import pocket, pocket_pattern  # noqa: F401  (declared-path symmetry)
from draftwright.model.detect import build_part_model
from draftwright.recognition import (
    PocketArray,
    PocketGrid,
    recognise_pocket_patterns,
    recognise_pockets,
)

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
    pockets = recognise_pockets(_pocket_row(n=4, pitch=30.0))
    assert len(pockets) == 4
    pats = recognise_pocket_patterns(pockets)
    assert len(pats) == 1
    pa = pats[0]
    assert isinstance(pa, PocketArray)
    assert len(pa.pockets) == 4
    assert pa.pitch == 30.0
    assert abs(pa.direction[1]) == 1.0  # runs along Y


def test_recognise_pocket_grid():
    pats = recognise_pocket_patterns(recognise_pockets(_pocket_grid(2, 3)))
    assert len(pats) == 1
    pg = pats[0]
    assert isinstance(pg, PocketGrid)
    assert len(pg.pockets) == 6
    assert {pg.rows, pg.cols} == {2, 3}


def test_two_pockets_are_not_a_pattern():
    # an array needs >=3 members (a pair is just two pockets)
    assert recognise_pocket_patterns(recognise_pockets(_pocket_row(n=2, pitch=30.0))) == []


def test_different_size_pockets_do_not_group():
    # three collinear pockets of DIFFERENT sizes share no spec key, so none form an array
    part = Box(30, 150, 20)
    part -= Pos(0, -45, 7) * Box(10, 12, 6)
    part -= Pos(0, 0, 7) * Box(14, 12, 6)  # wider
    part -= Pos(0, 45, 7) * Box(10, 18, 6)  # longer
    assert recognise_pocket_patterns(recognise_pockets(part)) == []


def test_build_part_model_groups_and_excludes_members():
    pm = build_part_model(_pocket_row(n=4, pitch=30.0))
    kinds = [f.kind for f in pm.features]
    assert kinds.count("pocket_pattern") == 1
    assert kinds.count("pocket") == 0  # members folded into the pattern, not emitted individually
    pat = next(f for f in pm.features if f.kind == "pocket_pattern")
    assert pat.count == 4
    assert pat.member.width == 10.0 and pat.member.length == 12.0 and pat.member.depth == 6.0


def test_sheet_emit_round_trips_the_pattern():
    from draftwright.sheet_emit import generate_sheet_script

    py = generate_sheet_script(_pocket_row(n=4, pitch=30.0), out="/tmp/pp_emit_rt")
    src = Path(py).read_text()
    line = next(ln for ln in src.splitlines() if "sheet.pocket_pattern(" in ln)
    # declare rejects members= — the emit must use at=/pitch=/direction= instead
    assert "members=" not in line
    assert 'kind="linear"' in line and "count=4" in line
    assert "at=" in line and "pitch=" in line and "direction=" in line


def test_tuner_jig_fixture_recognised_as_one_pattern():
    # #837/#841: five blind obround pockets on one centreline collapse to ONE PocketArray.
    part = import_step(str(_FIXTURE))
    pats = recognise_pocket_patterns(recognise_pockets(part))
    assert len(pats) == 1 and isinstance(pats[0], PocketArray)
    assert len(pats[0].pockets) == 5


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
