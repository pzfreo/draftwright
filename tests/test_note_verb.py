"""The public `Drawing.note()` free-text verb (#817).

`note()` is the sanctioned door for free-form text — a note is user-positioned (it carries no
feature and is not solve-placed), so it takes a page position. It replaces the raw
`dwg.add(Note(...))` pattern as the low-level placement API is privatised.
"""

from build123d import Box, Cylinder, Pos

from draftwright import build_drawing


def _dwg():
    return build_drawing(Box(60, 40, 20) - Pos(0, 0, 5) * Cylinder(4, 20))


def test_note_places_text_at_the_page_position():
    dwg = _dwg()
    x0, y0, x1, y1 = dwg.view_bounds("front")
    name = dwg.note("SEE NOTE 1", (x1 + 5, (y0 + y1) / 2))

    obj = dwg.get_annotation(name)
    assert obj is not None and type(obj).__name__ == "Note"
    assert obj.label == "SEE NOTE 1"
    # placed at the requested page position (centred on it by default).
    bb = obj.bounding_box()
    assert abs((bb.min.X + bb.max.X) / 2 - (x1 + 5)) < 2.0


def test_note_autonames_and_can_be_named():
    dwg = _dwg()
    assert dwg.note("A", (10, 10)) == "note0"
    assert dwg.note("B", (10, 20)) == "note1"
    assert dwg.note("C", (10, 30), name="my_note") == "my_note"
    assert {"note0", "note1", "my_note"} <= set(dwg.annotations())


def test_note_tagged_to_a_view_is_owned_by_it():
    dwg = _dwg()
    name = dwg.note("PLAN NOTE", (10, 10), view="plan")
    assert dwg.view_of(name) == "plan"


def test_note_exports(tmp_path):
    dwg = _dwg()
    dwg.note("SEE NOTE 1", (150, 100))
    out = dwg.export(str(tmp_path / "out"), formats=("svg",))
    assert set(out) == {"svg"}
