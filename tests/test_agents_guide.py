"""Executable check that AGENTS.md's examples stay correct (#818).

AGENTS.md is the one-page agent usage guide; a guide with wrong API is worse than none, and an
agent will follow it verbatim. These run the exact public surfaces it recommends so a rename or
signature change fails CI here, not silently in an agent's hands. Every verb and front door the
guide shows is exercised (Codex #819: untested claims let a wrong `drop(name)` example and an
un-verified GD&T target slip through round 1).
"""

from build123d import Box, Cylinder, Pos, export_step

from draftwright import Sheet, build_drawing


def _holed_block(hole_x=0.0):
    part = Box(60, 40, 20) - Pos(hole_x, 0, 5) * Cylinder(4, 20)
    return part, Pos(hole_x, 0, 5) * Cylinder(4, 20), part.faces().sort_by()[-1]


def test_declared_sheet_gdt_targets_the_declared_feature():
    """Front door 2 + GD&T: control(handle) must target the HOLE, not the datum/face. An
    off-centre hole makes the frame site distinguishable from the centred top face (Codex #819)."""
    part, hole_solid, top_face = _holed_block(hole_x=18.0)
    s = Sheet(part)
    h = s.hole(hole_solid)
    h.fit("H7")
    s.datum("A", top_face)
    s.control(h).position(0.1, to="A").perpendicularity(0.05, to="A")
    dwg = s.build()

    frames = [n for n in dwg.annotations() if n.startswith("m_gdt")]
    assert len(frames) == 3, f"expected datum + 2 control frames, got {frames}"
    assert not [i for i in dwg.lint() if i.code == "gdt_dropped"], "no GD&T frame should drop"
    # The control frames hang off the HOLE (x≈18), not the centred top face (x≈0).
    cfs = [f for f in s.features if f.kind == "control_frame"]
    assert cfs and all(abs(f.frame.origin[0] - 18.0) < 2.0 for f in cfs), (
        "frames must target the hole"
    )


def test_automatic_and_declared_ir_front_doors(tmp_path):
    """Front door 1 (solid AND path) + front door 3 (model=) + diagnostics + 4-format export."""
    part, _, _ = _holed_block()
    step = tmp_path / "part.step"
    export_step(part, str(step))

    for src in (part, str(step)):  # build_drawing accepts a solid or a path
        dwg = build_drawing(src)
        assert isinstance(dwg.lint(), list)  # the guide says: always check lint()
        assert dwg.model().features  # recognition populated (a non-sparse drawing)

    model = build_drawing(part).model()  # front door 3: reuse a PartModel, skip detection
    assert build_drawing(part, model=model).model().features

    out = build_drawing(part).export(str(tmp_path / "out"), formats=("svg", "dxf", "pdf", "png"))
    assert set(out) == {"svg", "dxf", "pdf", "png"}  # -> {format: path}


def test_editing_verbs_exist_and_route_correctly():
    """Every edit verb the guide shows: deferred callout/dimension(pin=)/locate/furniture/section,
    then pin/unpin (freeze a placed annotation) and the drop(feature) vs remove(name) distinction."""
    part, _, _ = _holed_block()
    dwg = build_drawing(part)
    hole = next(f for f in dwg.model().features if f.kind in ("hole", "pattern"))
    envelope = next(f for f in dwg.model().features if f.kind == "envelope")

    with dwg.deferred():
        dwg.callout(hole)
        dwg.dimension(envelope, "length", role="width", pin=True, priority=2)  # anchored, ranked
        dwg.locate(hole)
        dwg.furniture(hole)  # centre marks
        dwg.section()  # no-op when nothing triggers a section, but must not error
    names = list(dwg.annotations_of(hole))
    assert names  # placed via the solve, not hand-coordinated

    dwg.pin(names[0])  # freeze an already-placed annotation, then release
    dwg.unpin(names[0])
    dwg.remove(names[0])  # remove() takes a NAME
    assert names[0] not in dwg.annotations()

    removed = dwg.drop(hole)  # drop() takes a FEATURE, returns removed names
    assert isinstance(removed, list) and not dwg.annotations_of(hole)

    assert dwg.features("plan") is not None  # dwg.features(view) — the per-view read cited


def test_sheet_slot_declaration_builds():
    """The guide's `s.slot(...)` declaration: a slotted part declares + builds without error."""
    slot_solid = Pos(0, 0, 0) * Box(24, 8, 12)
    part = Box(80, 40, 12) - slot_solid
    s = Sheet(part)
    s.slot(slot_solid)
    dwg = s.build()
    assert dwg.model().features  # the slot is in the declared model
