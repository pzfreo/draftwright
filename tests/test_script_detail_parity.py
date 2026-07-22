"""Characterisation tests for direct/generated-script detail-view parity.

These tests deliberately avoid prescribing the implementation.  The detail-view
escalation gap they were written against is fixed (#661: the finalize drain now
queues and resolves detail requests like the auto pass); the remaining xfails
capture the still-open gaps — side-drilled location dims and rotational furniture.
"""

import runpy
from pathlib import Path

import pytest
from build123d import Align, Box, Cylinder, Pos, Rot, Rotation, export_step

from draftwright import build_drawing, generate_script

# Characterisation with executable acceptance criteria for the known direct/script gaps
# (#707 umbrella; #661 details; #426 reconstruction convergence). The strict xfails below
# ARE the acceptance criteria: a fix that closes a gap surfaces as an XPASS failure and
# the xfail is then removed deliberately, in that fix's PR (#709 un-skipped the module).


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


def _run_generated_script(step, tmp_path, name, *, scale=None, page=None, detail_view=None):
    """Execute an emitted script with the same build settings as its direct peer.

    ``generate_script`` does not yet expose ``detail_view``. Because the generated file is
    explicitly an editable surface, inject that setting into its ``build_drawing`` call when
    a parity fixture needs it. This keeps the comparison inputs equal without changing the
    production emitter merely to enable a characterization test.
    """
    script = Path(
        generate_script(
            str(step),
            out=str(tmp_path / name),
            scale=scale,
            page=page,
        )
    )
    if detail_view is not None:
        source = script.read_text(encoding="utf-8")
        anchor = "    page=PAGE,\n"
        assert source.count(anchor) == 1, "generated build_drawing call changed"
        source = source.replace(anchor, anchor + f"    detail_view={detail_view!r},\n")
        script.write_text(source, encoding="utf-8")
    return runpy.run_path(str(script))["dwg"]


def _scripted_drawing(part, tmp_path, name, **build_settings):
    """Round-trip *part* through the actual generated imperative script."""
    step = tmp_path / f"{name}.step"
    export_step(part, str(step))
    return step, _run_generated_script(step, tmp_path, name, **build_settings)


@pytest.mark.timeout(180)
def test_direct_build_detail_fixture_really_triggers(crowded_step):
    """Guard the fixture: direct automatic output must contain a real detail."""
    direct = build_drawing(str(crowded_step), detail_view=True)

    views, annotations = _detail_signature(direct)
    assert views == ("detail_a",)
    assert "detail_caption_A" in annotations
    assert any(name.startswith("dim_detail_a_step") for name in annotations)


@pytest.mark.timeout(240)
def test_generated_script_matches_direct_detail_view(crowded_step, tmp_path):
    """Target behaviour: executing the script preserves the direct detail output."""
    direct = build_drawing(str(crowded_step), detail_view=True)
    scripted = _run_generated_script(crowded_step, tmp_path, "scripted", detail_view=True)

    assert _detail_signature(scripted) == _detail_signature(direct)


@pytest.mark.timeout(300)
def test_generated_script_matches_two_direct_detail_views(tmp_path):
    """Two separated fine-step runs retain distinct DETAIL A/B output."""
    specs = [(4, 1.5), (6, 2.0), (4, 2.5), (3, 22), (6, 1.5), (4, 2.0), (5, 2.5), (2, 22)]
    part = Rotation(0, 90, 0) * _turned_shaft(specs)
    step, scripted = _scripted_drawing(part, tmp_path, "two_details", page="A2", scale=2.0)
    direct = build_drawing(str(step), page="A2", scale=2.0)

    assert {"detail_a", "detail_b"} <= set(direct.views)  # guard the fixture
    assert _detail_signature(scripted) == _detail_signature(direct)


@pytest.mark.timeout(240)
def test_generated_script_matches_direct_turned_head_detail(tmp_path):
    """The turned-head detail route has parity, independently of prismatic details."""
    specs = [(4, 1.5), (6, 2.0), (4, 2.5), (3, 25.0)]
    part = Rotation(0, 90, 0) * _turned_shaft(specs)
    step, scripted = _scripted_drawing(part, tmp_path, "turned_detail", scale=2.0)
    direct = build_drawing(str(step), scale=2.0)

    assert "detail_a" in direct.views  # guard the fixture
    assert _detail_signature(scripted) == _detail_signature(direct)


@pytest.mark.timeout(240)
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
def test_generated_script_reproduces_nts_iso_note(tmp_path):
    """Furniture parity: the "ISO VIEW (NTS)" note the direct CLI adds whenever it
    rescales the iso view off sheet scale must also appear on the emitted-script drawing.

    The script builds with ``auto_dims=False``, which used to fit the iso with
    ``annotate=False`` and silently drop the note — so the editable script's drawing
    disagreed with the direct CLI on every part whose iso is not to scale. The note is
    sheet furniture (like the title block, always added on both paths), not a dimension.
    """
    part = Box(60, 40, 20) - Pos(0, 0, 10) * Cylinder(4, 20)
    step, scripted = _scripted_drawing(part, tmp_path, "nts_note")
    direct = build_drawing(str(step))

    def note(dwg):
        """(label, rounded bbox centre) of the NTS note, or None if absent."""
        obj = dwg.get_annotation("note_iso_nts")
        if obj is None:
            return None
        bb = obj.bounding_box()
        return obj.label, (
            round((bb.min.X + bb.max.X) / 2, 1),
            round((bb.min.Y + bb.max.Y) / 2, 1),
        )

    assert note(direct) is not None  # guard the fixture: the iso is rescaled off sheet scale
    # Genuine parity, not mere presence (Codex #810): a stale/mislabelled/misplaced scripted
    # note must fail. This part has no machined-callout features, so the two layouts do not
    # diverge — the note's label AND page position match exactly between the paths.
    assert note(scripted) == note(direct)


def _machined_callouts(dwg):
    """(kind, label) of every machined-feature leader callout on the drawing."""
    prefixes = ("m_pocket", "m_fillet", "m_flat", "m_chamfer", "m_groove", "m_plate")
    return sorted(
        (name.split("_")[1], getattr(dwg.get_annotation(name), "label", None))
        for name in dwg.annotations()
        if name.startswith(prefixes)
    )


@pytest.mark.timeout(240)
def test_generated_script_reproduces_machined_callouts(tmp_path):
    """Machined-feature callout parity (#148): a pocket/fillet/flat/chamfer/groove/plate is a
    Leader callout, not a linear Dimension (its IR params carry no span), so the emitted script
    could not route it through ``dimension()``. The reconstruction had NO callout verb for these
    kinds, so ``_feature_listing`` emitted nothing and every machined callout was silently
    dropped from the script's drawing — contradicting its own "never silently dropped" contract.

    A single floored pocket on a roomy block places identically on both paths (no crowding, so
    no layout-driven drop divergence), giving an exact-parity check.
    """
    part = Box(120, 80, 30) - Pos(0, 0, 12) * Box(40, 25, 8)
    step, scripted = _scripted_drawing(part, tmp_path, "machined")
    direct = build_drawing(str(step))

    assert _machined_callouts(direct)  # guard the fixture: the direct build draws the pocket
    assert _machined_callouts(scripted) == _machined_callouts(direct)


@pytest.mark.timeout(240)
def test_generated_script_machined_callout_is_per_feature(tmp_path):
    """Editable-script contract (Codex #811): commenting ONE machined callout line drops
    exactly that feature, not the whole kind. Two separated pockets on a roomy block emit two
    ``dwg.callout(f)`` lines; removing the first must leave exactly the second pocket's callout.
    The pre-#811 whole-kind renderer redrew BOTH pockets from the single surviving intent, so
    this fails on that approach — the ``only=`` per-feature subset is what makes it pass.
    """
    part = Box(160, 90, 30) - Pos(-40, 0, 12) * Box(24, 20, 8) - Pos(40, 0, 12) * Box(24, 20, 8)
    step = tmp_path / "two_pockets.step"
    export_step(part, str(step))

    direct = build_drawing(str(step))
    assert len(_machined_callouts(direct)) == 2  # guard: both pockets drawn on the direct path

    script = Path(generate_script(str(step), out=str(tmp_path / "two_pockets")))
    baseline = runpy.run_path(str(script))["dwg"]
    assert len(_machined_callouts(baseline)) == 2  # the unedited script reproduces both

    # Comment out the FIRST pocket callout line only, then re-run the edited script.
    lines = script.read_text(encoding="utf-8").splitlines()
    for idx, ln in enumerate(lines):
        if ln.strip() == "dwg.callout(f)":
            indent = ln[: len(ln) - len(ln.lstrip())]
            lines[idx] = f"{indent}# {ln.strip()}"
            break
    else:
        raise AssertionError("generated script has no dwg.callout(f) line to comment out")
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    edited = runpy.run_path(str(script))["dwg"]

    # Exactly one pocket survives — per-feature only=, not a whole-kind redraw.
    assert len(_machined_callouts(edited)) == 1


@pytest.mark.timeout(240)
def test_callout_rejects_name_view_for_machined_feature(tmp_path):
    """A machined callout is auto-named/placed by its whole-kind renderer, so ``name=``/``view=``
    are unsupported and raise rather than being silently discarded (Codex #811, F2)."""
    part = Box(120, 80, 30) - Pos(0, 0, 12) * Box(40, 25, 8)
    step = tmp_path / "pocket.step"
    export_step(part, str(step))
    dwg = build_drawing(str(step))
    pocket = next(f for f in dwg.model().features if f.kind == "pocket")

    with pytest.raises(ValueError, match="machined"):
        dwg.callout(pocket, name="critical_depth")
    with pytest.raises(ValueError, match="machined"):
        dwg.callout(pocket, view="plan")


@pytest.mark.timeout(240)
def test_generated_script_matches_direct_y_axis_turned_diameter_policy(tmp_path):
    """Y-axis turned output runs and follows the direct no-diameter policy."""
    part = Rotation(90, 0, 0) * _turned_shaft([(20, 20), (14, 15)])
    step, scripted = _scripted_drawing(part, tmp_path, "y_turned")
    direct = build_drawing(str(step))

    def diameter_callouts(dwg):
        return tuple(sorted(n for n in dwg.annotations() if n.startswith("m_dia")))

    assert diameter_callouts(scripted) == diameter_callouts(direct) == ()
