"""No dimension is placed inside the title block (#1593).

Strip placement ran before the block existed — it is drawn near the end of
``_PASS_SEQUENCE`` — so the block was invisible to every placer except the GD&T one,
which carried its own ``forbid`` box (#481). Latent while the block was two rows tall;
the ISO 7200 layout makes it four, and an overall depth dimension below the side view
then lands on it. ``build_drawing(Box(40, 30, 12))`` ran its ``30`` 2.1 mm into the
block and ``lint()`` reported a clean sheet; the stepped ``Box(80, 60, 30)`` below put
the whole ``60`` label inside it, reported only as an ``annotation_overlap`` between the
labels ``60`` and ``DRAWING``.

The fix reads the block's deterministic footprint (``builder._assemble`` computes it
once) and treats it as a hard keep-out. Each case below asserts its own precondition by
building twice — once with the keep-out neutralised — so a test cannot pass because its
fixture never contained the defect.
"""

import pytest
from build123d import Box, Cylinder, Pos

import draftwright.annotations._common as _common
from draftwright import build_drawing

# name -> (part, whether the pre-fix build put a dimension in the block)
CASES = {
    # Both cases move to the side view's opposite strip and stay on the sheet.
    "plain_box": (Box(40, 30, 12), True),
    "tall_box": (Box(80, 60, 30), True),
    # Both of the side view's strips are full here (the block below, a shoulder dim
    # above), so the overall depth cannot be placed at the auto A4/1:1 at all.
    "stepped": (Box(80, 60, 30) - Pos(0, -20, 7.5) * Box(80, 20, 15), True),
    # The control: this one never came near the block. It must be untouched.
    "bored_plate": (Box(90, 60, 10) - Cylinder(5, 10), False),
}


def _build(name, *, keep_out, monkeypatch):
    if not keep_out:
        monkeypatch.setattr(_common, "pending_title_block_box", lambda _dwg: None)
    return build_drawing(CASES[name][0], number="X")


def _title_block(drawing):
    box = drawing.get_annotation("title_block").bounding_box()
    return (box.min.X, box.min.Y, box.max.X, box.max.Y)


def _inside_the_block(drawing):
    """Names of annotations whose footprint overlaps the drawn title block."""
    x0, y0, x1, y1 = _title_block(drawing)
    hits = []
    for name, annotation in drawing.iter_annotations():
        if name in ("title_block", "sheet_frame"):
            continue
        box = annotation.bounding_box()
        if box.min.X < x1 and box.max.X > x0 and box.min.Y < y1 and box.max.Y > y0:
            hits.append(name)
    return hits


@pytest.mark.parametrize("name", list(CASES))
def test_precondition_the_defect_is_present_without_the_keep_out(name, monkeypatch):
    """Neutralising the keep-out reproduces #1593 — or, for the control, does not.

    Without this, three of the four cases below would pass against completely unfixed
    code: their geometry simply never reaches the block.
    """
    drawing = _build(name, keep_out=False, monkeypatch=monkeypatch)
    assert bool(_inside_the_block(drawing)) is CASES[name][1]


@pytest.mark.parametrize("name", list(CASES))
def test_nothing_is_drawn_inside_the_title_block(name, monkeypatch):
    assert _inside_the_block(_build(name, keep_out=True, monkeypatch=monkeypatch)) == []


@pytest.mark.parametrize("name", ["plain_box", "tall_box"])
def test_a_displaced_dimension_stays_on_the_sheet(name, monkeypatch):
    """Avoiding the block relocates the dimension; it does not discard it.

    The side view's opposite strip is the engine's existing fallthrough for a starved
    overall extent, and it is what catches these. A fix that merely refused to draw
    into the block would trade one silent defect for a quieter one.
    """
    before = _build(name, keep_out=False, monkeypatch=monkeypatch)
    monkeypatch.undo()
    after = _build(name, keep_out=True, monkeypatch=monkeypatch)

    assert before.get_annotation("m_env_depth") is not None
    assert after.get_annotation("m_env_depth") is not None
    assert after.get_annotation("m_env_depth").label == before.get_annotation("m_env_depth").label
    # It moved because it had to, not incidentally: the old position was in the block.
    assert "m_env_depth" in _inside_the_block(before)
    assert not [issue for issue in after.lint() if issue.severity == "error"]


def test_a_dimension_with_nowhere_to_go_is_reported_not_dropped(monkeypatch):
    """The remaining cost, stated rather than hidden.

    On A4 at 1:1 this part's side view has the title block below and a shoulder dim
    above, so the overall depth has no clear strip. Before #1593 it was drawn through
    the block; now it is withheld, as an ERROR that names the block. A larger sheet or a
    smaller scale places it — choosing one without knowing the dimension needed the room
    is #1590.
    """
    drawing = _build("stepped", keep_out=True, monkeypatch=monkeypatch)
    (withheld,) = [i for i in drawing.lint() if i.code == "overall_dim_withheld"]
    assert withheld.severity == "error"
    assert "title_block" in withheld.message

    monkeypatch.undo()
    roomier = build_drawing(CASES["stepped"][0], number="X", page="A3")
    assert roomier.get_annotation("m_env_depth") is not None
    assert _inside_the_block(roomier) == []
    assert not [issue for issue in roomier.lint() if issue.severity == "error"]
