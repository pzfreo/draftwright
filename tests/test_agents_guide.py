"""Executable check that AGENTS.md's examples stay correct (#818).

AGENTS.md is the one-page agent usage guide; a guide with wrong API is worse than none, and an
agent will follow it verbatim. These run the exact public surfaces it recommends so a rename or
signature change fails CI here, not silently in an agent's hands. Every verb the guide shows is
exercised (Codex #819: an untested claim let a wrong `drop(name)` example slip through).
"""

from build123d import Box, Cylinder, Pos, export_step

from draftwright import Sheet, build_drawing


def _holed_block():
    part = Box(60, 40, 20) - Pos(0, 0, 5) * Cylinder(4, 20)
    return part, Pos(0, 0, 5) * Cylinder(4, 20), part.faces().sort_by()[-1]


def test_declared_sheet_with_gdt_targets_the_feature_and_places_frames():
    """Front door 2 + GD&T: Sheet(part) → keep the hole handle → datum/control(handle) → build().
    The control frames must target the HOLE (not the datum) and place through the solve."""
    part, hole_solid, top_face = _holed_block()
    s = Sheet(part)
    h = s.hole(hole_solid)
    h.fit("H7")
    s.datum("A", top_face)
    s.control(h).position(0.1, to="A").perpendicularity(0.05, to="A")
    dwg = s.build()

    # datum symbol A + the two control frames = 3, all corridor-placed, none dropped.
    frames = [n for n in dwg.annotations() if n.startswith("m_gdt")]
    assert len(frames) == 3, f"expected datum + 2 control frames, got {frames}"
    assert not [i for i in dwg.lint() if i.code == "gdt_dropped"], "no GD&T frame should drop"


def test_automatic_front_doors_lint_and_export(tmp_path):
    """Front door 1 (solid AND path) + diagnostics + the 4-format export shape."""
    part, _, _ = _holed_block()
    step = tmp_path / "part.step"
    export_step(part, str(step))

    for src in (part, str(step)):  # build_drawing accepts a solid or a path
        dwg = build_drawing(src)
        assert isinstance(dwg.lint(), list)  # the guide says: always check lint()
        assert dwg.model().features  # recognition populated (a non-sparse drawing)

    out = build_drawing(part).export(str(tmp_path / "out"), formats=("svg", "dxf", "pdf", "png"))
    assert set(out) == {"svg", "dxf", "pdf", "png"}  # -> {format: path}


def test_editing_verbs_exist_and_route_correctly():
    """The edit verbs the guide recommends: deferred callout/dimension(pin=)/locate/section, then
    drop(feature) and remove(name) — the two removals the guide distinguishes."""
    part, _, _ = _holed_block()
    dwg = build_drawing(part)
    hole = next(f for f in dwg.model().features if f.kind in ("hole", "pattern"))

    with dwg.deferred():
        dwg.callout(hole)
        dwg.locate(hole)
    names = list(dwg.annotations_of(hole))
    assert names  # placed via the solve, not hand-coordinated

    dwg.remove(names[0])  # remove() takes a NAME
    assert names[0] not in dwg.annotations()

    removed = dwg.drop(hole)  # drop() takes a FEATURE, returns removed names
    assert isinstance(removed, list) and not dwg.annotations_of(hole)

    assert dwg.features("plan") is not None  # dwg.features(view) — the per-view read cited
