"""Executable check that AGENTS.md's examples stay correct (#818).

AGENTS.md is the one-page agent usage guide; a guide with wrong API is worse than none.
These run the exact surfaces it recommends so a rename/signature change fails CI here, not
silently in an agent's hands. Kept to the guide's public claims, not internal behaviour.
"""

from build123d import Box, Cylinder, Pos

from draftwright import Sheet, build_drawing


def _holed_block():
    part = Box(60, 40, 20) - Pos(0, 0, 5) * Cylinder(4, 20)
    return part, Pos(0, 0, 5) * Cylinder(4, 20), part.faces().sort_by()[-1]


def test_declared_sheet_with_gdt_builds_and_places_frames():
    """Front door 2 + GD&T section: Sheet(part) → aspects → datum/control → build(), and the
    declared GD&T frames are placed through the solve (the guide's 'right way')."""
    part, hole_solid, top_face = _holed_block()
    s = Sheet(part)
    s.hole(hole_solid).fit("H7")
    s.datum("A", top_face)
    s.control(0).position(0.1, to="A").perpendicularity(0.05, to="A")
    dwg = s.build()

    frames = [n for n in dwg.annotations() if n.startswith("m_gdt")]
    # datum symbol A + the two control frames (position, perpendicularity), all corridor-placed.
    assert len(frames) >= 2, "declared GD&T frames should place via the corridor solve"


def test_automatic_front_door_lint_and_export(tmp_path):
    """Front door 1 + diagnostics + export: build_drawing(part) → lint() → export(formats=…)."""
    part, _, _ = _holed_block()
    dwg = build_drawing(part)

    issues = dwg.lint()  # the guide tells agents to always check this
    assert isinstance(issues, list)
    out = dwg.export(str(tmp_path / "out"), formats=("pdf",))
    assert set(out) == {"pdf"}


def test_editing_verbs_route_through_the_solve():
    """The edit verbs the guide recommends exist and record through deferred()/finalize()."""
    part, _, _ = _holed_block()
    dwg = build_drawing(part)
    hole = next(f for f in dwg.model().features if f.kind in ("hole", "pattern"))
    with dwg.deferred():
        dwg.callout(hole)  # records; block exit finalizes through the solve
    # a callout for the hole now exists (placed, not hand-coordinated)
    assert dwg.annotations_of(hole)
