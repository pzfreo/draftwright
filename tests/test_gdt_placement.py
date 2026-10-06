"""GD&T aspect side-layer placement (ADR 4 (was 0011 §4) / ADR 2 (was 0009), #61).

Declared feature control frames / datum feature symbols / surface finishes are placed
as first-class ADR 2 (was 0009) corridor candidates — through the SAME collect-then-solve strip
machinery as the auto-dimensions, NOT a leftover first-fit. These tests lock down: the
glyphs render into their target strip, they carry their real footprint so stacked frames
never overlap, a full strip drops honestly (a warning, not a silent vanish), and the
placement stays lint-clean.
"""

import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree

import ezdxf
import pytest
from build123d import Box, Cylinder, Draft, Pos
from build123d_drafting import DatumFeature, FeatureControlFrame, Leader

from draftwright.annotations._gdt import _datum_alternate_view
from draftwright.builder import build_drawing, detect_part_model
from draftwright.linting.structural import lint_drawing
from draftwright.model.ir import ControlFrame, DatumRef, Finish, Frame, Note, PmiFeature
from draftwright.pmi import extract_pmi_report


def _part():
    """A prismatic block with a central through-hole — roomy plan/front views."""
    return Box(80, 50, 20) - Pos(0, 0, 0) * Cylinder(6, 20)


def _build(*extra_features, part=None, trace=None, **kwargs):
    part = part if part is not None else _part()
    m = detect_part_model(part)
    m.features.extend(extra_features)
    return build_drawing(part, model=m, trace=trace, **kwargs)


def _fcf_height():
    """The bare feature-control-frame glyph height — the strip footprint a frame reserves
    (NOT the leader+frame box). Two stacked frames must sit at least this far apart."""
    g = FeatureControlFrame("position", "0.1", datums=("A",), draft=Draft(font_size=3.0))
    return g.bounding_box().size.Y


def _leader_path_length(leader):
    points = (leader.tip, *getattr(leader, "bends", ()), leader.elbow)
    return sum(
        math.dist(left[:2], right[:2]) for left, right in zip(points, points[1:], strict=False)
    )


def test_gdt_retry_reuses_validated_glyph_without_changing_placed_ink(monkeypatch):
    from draftwright._core import Strip
    from draftwright.annotations import _gdt

    draft = Draft(font_size=3.0)
    frame = ControlFrame(
        frame=Frame((0.0, 0.0, 0.0), "z"),
        characteristic="position",
        tolerance="0.1",
        view="plan",
        side="above",
        datums=("A",),
        diameter=True,
    )
    glyph = _gdt._gdt_glyph(frame, draft)
    origin_box = glyph.bounding_box()
    strip = Strip(anchor=20.0, outer_limit=90.0, direction=1.0, gap=8.0, spacing=3.0)
    real_visual_tolerance = _gdt._gdt_visual_tolerance
    validated = []

    def counted_visual_tolerance(*args, **kwargs):
        validated.append(True)
        return real_visual_tolerance(*args, **kwargs)

    monkeypatch.setattr(_gdt, "_gdt_visual_tolerance", counted_visual_tolerance)
    build, _, _, compact, _ = _gdt._gdt_candidate_builders(
        frame,
        draft,
        Leader,
        glyph,
        30.0,
        20.0,
        True,
        strip,
        (origin_box.size.X, origin_box.size.Y),
        5.0,
        None,
    )
    monkeypatch.setattr(
        _gdt, "_gdt_glyph", lambda *_args: pytest.fail("placement rebuilt a validated glyph")
    )
    assert len(validated) == 1
    for tier in (35.0, 45.0, 35.0):
        placed = build(tier)
        fresh = Leader(
            tip=(30.0, 20.0),
            elbow=(30.0, tier),
            label="",
            draft=draft,
            callout=glyph,
        )
        _gdt._attach_gdt_text_evidence(fresh, glyph, frame, draft)
        assert placed.label_bbox == pytest.approx(fresh.label_bbox)
        assert placed.segments == fresh.segments
        assert placed.pdf_text_relative_specs
        assert placed.pdf_text_relative_specs == fresh.pdf_text_relative_specs
    # Only the direct reference attachment above adds validations; the three
    # placement retries must reuse the builder's single checked result.
    assert len(validated) == 4
    proposals = list(compact(build(55.0)))[:3]
    assert proposals
    other = build(55.0)

    class EmptyDrawing:
        @staticmethod
        def iter_annotations():
            return ()

        @staticmethod
        def view_of(_name):
            return "plan"

    for proposal in proposals:
        actual = proposal.materialize()
        assert proposal.label_bbox == pytest.approx(actual.label_bbox)
        assert proposal.segments == tuple(actual.segments)
        assert proposal.tip == actual.tip
        assert proposal.elbow == actual.elbow
        assert _gdt.annotation_ink_clear(
            EmptyDrawing(), proposal, additional=(other,)
        ) == _gdt.annotation_ink_clear(EmptyDrawing(), actual, additional=(other,))
    unchanged = glyph.bounding_box()
    assert (unchanged.min.X, unchanged.min.Y, unchanged.max.X, unchanged.max.Y) == pytest.approx(
        (origin_box.min.X, origin_box.min.Y, origin_box.max.X, origin_box.max.Y)
    )


def test_control_frame_places_first_class():
    frame = ControlFrame(
        frame=Frame((0.0, 0.0, 0.0), "z"),
        characteristic="position",
        tolerance="0.1",
        view="plan",
        side="above",
        datums=("A",),
        diameter=True,
    )
    dwg = _build(frame)
    assert "m_gdt0" in dwg.annotations()
    assert not [i for i in dwg.registry.issues if i.code == "gdt_dropped"]
    # It rendered the actual frame geometry (a wide box, not an empty leader).
    assert dwg.get_annotation("m_gdt0").bounding_box().size.X > 15


def test_imported_scope_modifier_reaches_the_solver_owned_leader(monkeypatch, tmp_path):
    import draftwright.annotations.from_model as from_model

    seen: list[tuple[bool, bool]] = []
    real_leader = from_model.Leader

    def recording_leader(*args, **kwargs):
        seen.append((kwargs.get("all_around", False), kwargs.get("all_over", False)))
        return real_leader(*args, **kwargs)

    monkeypatch.setattr(from_model, "Leader", recording_leader)
    frame = ControlFrame(
        frame=Frame((0.0, 0.0, 0.0), "z"),
        characteristic="profile_surface",
        tolerance="0.5",
        view="plan",
        side="above",
        all_around=True,
        source_id="geometric_tolerance:fixture",
        part21_id="#27",
    )
    dwg = _build(frame)

    assert (True, False) in seen
    assert "m_gdt0" in dwg.annotations()
    assert dwg.registry.names_for_feature(frame) == ["m_gdt0"]
    paths = dwg.export(str(tmp_path / "all-around"), formats=("svg", "dxf"))
    assert all(Path(path).exists() and Path(path).stat().st_size > 0 for path in paths.values())


@pytest.mark.timeout(60)
def test_all_over_control_frame_exports_two_rings_to_svg_and_dxf(tmp_path):
    frame = ControlFrame(
        frame=Frame((0.0, 0.0, 0.0), "z"),
        characteristic="profile_surface",
        tolerance="0.5",
        view="plan",
        side="above",
        all_over=True,
        source_id="geometric_tolerance:fixture",
        part21_id="#27",
    )
    dwg = _build(frame)

    paths = dwg.export(str(tmp_path / "all-over"), formats=("svg", "dxf"))
    assert all(Path(path).exists() and Path(path).stat().st_size > 0 for path in paths.values())

    svg = ElementTree.parse(paths["svg"])
    dims = next(element for element in svg.iter() if element.attrib.get("id") == "dims")
    annular_paths = [
        element.attrib["d"]
        for element in dims
        if element.tag.endswith("path")
        and element.attrib.get("d", "").count(" A ") == 4
        and element.attrib["d"].count("M ") == 2
        and " L " not in element.attrib["d"]
    ]
    assert len(annular_paths) == 2

    circles_by_center = defaultdict(set)
    for entity in ezdxf.readfile(paths["dxf"]).modelspace():
        if entity.dxf.layer == "dims" and entity.dxftype() == "CIRCLE":
            center = tuple(round(value, 6) for value in entity.dxf.center)
            circles_by_center[center].add(round(entity.dxf.radius, 6))
    assert any(len(radii) == 4 for radii in circles_by_center.values())


def test_automatically_imported_frame_stays_diagnostic_in_report_mode():
    from types import SimpleNamespace

    from draftwright.annotations.from_model import render_gdt

    frame = ControlFrame(
        frame=Frame((0.0, 0.0, 0.0), "z"),
        characteristic="profile_surface",
        tolerance="0.5",
        view="plan",
        side="above",
        source_id="geometric_tolerance:fixture",
    )

    registered = render_gdt(
        None,
        SimpleNamespace(features=[frame]),
        SimpleNamespace(pmi_mode="report"),
        ctx=SimpleNamespace(model_declared=False),
    )

    assert registered == 0


def test_datum_and_finish_place():
    datum = DatumRef(frame=Frame((30.0, 0.0, 0.0), "z"), letter="A", view="plan", side="above")
    finish = Finish(frame=Frame((0.0, 25.0, 0.0), "z"), ra="3.2", view="front", side="above")
    dwg = _build(datum, finish)
    placed = {n for n in dwg.annotations() if n.startswith("m_gdt")}
    assert placed == {"m_gdt0", "m_gdt1"}


def test_imported_planar_datum_leader_stays_normal_to_end_face():
    part = Box(80, 50, 20)
    site = (-40.0, 0.0, 5.0)
    origin = PmiFeature(
        frame=Frame(site, "x"),
        pmi_kind="datum",
        value=0.0,
        label="A",
        dominant_axis="X",
        ref_bbox=(-40.0, -25.0, -10.0, -40.0, 25.0, 10.0),
        source_category="datum",
        reference_axis="X",
    )
    datum = DatumRef(
        frame=Frame(site, "x"),
        letter="A",
        view="front",
        side="left",
        origin=origin,
        reference_surface_kind="plane",
    )

    dwg = _build(datum, part=part, page="A3", scale=1.0, scale_policy="permissive")

    leader = dwg.get_annotation("m_gdt0")
    assert leader.tip[1] == pytest.approx(leader.elbow[1])
    assert leader.tip[0] > leader.elbow[0]


def test_grm03_imported_datums_try_local_normal_symbols_issue_2177():
    fixture = Path(__file__).parent / "fixtures" / "grm03_thumbwheel_drive_screw_ap242_pmi.step"
    assert hashlib.sha256(fixture.read_bytes()).hexdigest() == (
        "4b6462b9cc9f0d419250933bd77fb305f9cfebb7ec2b3f377008732876010a21"
    )
    records = extract_pmi_report(fixture).records
    assert {(r.label, r.source_id) for r in records if r.kind == "datum"} == {
        ("A", "datum_definition:#777"),
        ("B", "datum_definition:#810"),
    }

    dwg = build_drawing(fixture, pmi="annotate", page="A4", scale=2.0, scale_policy="permissive")
    assert (dwg.page_w, dwg.page_h, dwg.scale) == (297.0, 210.0, 2.0)
    assert set(dwg.views) == {"front", "side", "iso", "detail_a"}
    a, b = (dwg.get_annotation(name) for name in ("m_gdt0", "m_gdt1"))
    assert a.tip[0] == pytest.approx(a.elbow[0])
    assert a.elbow[1] < a.tip[1]
    assert b.tip[1] == pytest.approx(b.elbow[1])
    assert b.elbow[0] > b.tip[0]
    # A's external symbol fits near the source face. B's normal path is still
    # obstructed by the existing front-view dimension ink, so it stays placed
    # and critique reports the long shaft rather than silently declaring it tidy.
    assert _leader_path_length(a) < 20.0
    assert "m_gdt0" not in {
        issue.annotation_name for issue in dwg.lint() if issue.code == "datum_leader_remote"
    }
    assert "m_gdt1" in {
        issue.annotation_name for issue in dwg.lint() if issue.code == "datum_leader_remote"
    }


def test_grm03_a4_five_to_one_datums_use_projected_whitespace_issue_2177():
    fixture = Path(__file__).parent / "fixtures" / "grm03_thumbwheel_drive_screw_ap242_pmi.step"
    # This public explicit-scale build exercises the same geometry as the A4 5:1
    # automatic candidate; scale completeness is a separate #2177 fix.
    dwg = build_drawing(fixture, pmi="annotate", page="A4", scale=5.0, scale_policy="permissive")
    assert (dwg.page_w, dwg.page_h, dwg.scale) == (297.0, 210.0, 5.0)
    a, b = (dwg.get_annotation(name) for name in ("m_gdt0", "m_gdt1"))
    assert a.tip[0] == pytest.approx(a.elbow[0])
    assert a.elbow[1] < a.tip[1]
    assert b.tip[1] == pytest.approx(b.elbow[1])
    assert b.elbow[0] > b.tip[0]
    assert _leader_path_length(a) < 20.0
    assert _leader_path_length(b) < 20.0
    assert not [issue for issue in dwg.lint() if issue.code == "datum_leader_remote"]


def test_datum_locality_lint_reads_placed_ink_independently_issue_2177():
    draft = Draft(font_size=3.0)
    glyph = DatumFeature("A", draft=draft)
    remote = Leader(tip=(40.0, 60.0), elbow=(40.0, 10.0), label="", draft=draft, callout=glyph)
    issues = lint_drawing(
        [remote],
        annotation_datums={id(remote)},
        annotation_names={id(remote): "datum_A"},
        annotation_views={id(remote): "front"},
    )
    assert [(issue.code, issue.annotation_name, issue.view) for issue in issues] == [
        ("datum_leader_remote", "datum_A", "front")
    ]
    assert not [issue for issue in lint_drawing([remote]) if issue.code == "datum_leader_remote"]


def test_grm03_remote_datum_producer_mutation_is_visible_to_lint_issue_2177(monkeypatch):
    import draftwright.annotations._gdt as gdt

    original = gdt._gdt_candidate_builders

    def without_local_candidates(*args):
        build, build_at, build_routed, _compact, repair = original(*args)
        return build, build_at, build_routed, lambda _placed: (), repair

    monkeypatch.setattr(gdt, "_gdt_candidate_builders", without_local_candidates)
    fixture = Path(__file__).parent / "fixtures" / "grm03_thumbwheel_drive_screw_ap242_pmi.step"
    dwg = build_drawing(fixture, pmi="annotate", page="A4", scale=5.0, scale_policy="permissive")
    remote = [issue for issue in dwg.lint() if issue.code == "datum_leader_remote"]
    assert {issue.annotation_name for issue in remote} == {"m_gdt0", "m_gdt1"}


def test_imported_datum_refuses_a_wrong_side_fallback(monkeypatch, tmp_path):
    import draftwright.annotations._gdt as gdt
    import draftwright.annotations.from_model as from_model

    def reject_primary(*args):
        candidate = args[-1]
        candidate.on_drop(candidate.name)

    monkeypatch.setattr(gdt, "register_corridor", reject_primary)
    monkeypatch.setattr(from_model, "carve_free_position", lambda *_args, **_kwargs: None)

    def forbidden_sheet_route(*_args, **_kwargs):
        raise AssertionError("an imported datum cannot switch to a diagonal sheet route")

    monkeypatch.setattr(from_model, "_sheet_leader_fallback", forbidden_sheet_route)
    site = (-40.0, 0.0, 5.0)
    origin = PmiFeature(
        frame=Frame(site, "x"),
        pmi_kind="datum",
        value=0.0,
        label="A",
        dominant_axis="X",
        ref_bbox=(-40.0, -25.0, -10.0, -40.0, 25.0, 10.0),
        source_category="datum",
        reference_axis="X",
    )
    datum = DatumRef(
        frame=Frame(site, "x"),
        letter="A",
        view="front",
        side="left",
        origin=origin,
        source_id="datum:fixture",
        reference_surface_kind="plane",
    )
    trace_path = tmp_path / "datum-normal-fallback.json"

    dwg = _build(datum, part=Box(80, 50, 20), trace=trace_path)

    assert "m_gdt0" not in dwg.annotations()
    assert any(issue.code == "pmi_dropped" for issue in dwg.registry.issues)
    events = [
        event
        for event in json.loads(trace_path.read_text())["pass_events"]
        if event["label"] == "gdt_post_drain_fallback"
    ]
    assert [attempt["side"] for attempt in events[0]["items"][0]["attempts"]] == ["left"]


def test_imported_datum_on_absent_side_strip_tries_normal_plan_edge_issue_2182(tmp_path):
    datum = DatumRef(
        frame=Frame((0.0, -25.0, 0.0), "y"),
        letter="A",
        view="side",
        side="left",
        source_id="datum:missing-side-strip",
        reference_surface_kind="plane",
    )
    surviving_frame = ControlFrame(
        frame=Frame((0.0, 0.0, 10.0), "z"),
        characteristic="flatness",
        tolerance="0.05",
        view="plan",
        side="above",
        source_id="geometric_tolerance:survivor",
    )
    trace_path = tmp_path / "missing-side-strip.json"

    dwg = _build(datum, surviving_frame, pmi="annotate", trace=trace_path)

    assert "m_gdt0" not in dwg.annotations()
    assert "m_gdt1" in dwg.annotations()
    assert any(
        issue.code == "pmi_dropped"
        and "m_gdt0" in issue.message
        and "surface-normal below strip" in issue.message
        for issue in dwg.registry.issues
    )
    solves = json.loads(trace_path.read_text())["solves"]
    assert any(
        solve["corridor"] == ["plan", "below"]
        and solve["strip"] is not None
        and "m_gdt0" in {candidate["name"] for candidate in solve["candidates"]}
        and any(outcome["name"] == "m_gdt0" for outcome in solve["outcomes"])
        for solve in solves
    )


def test_datum_alternate_view_requires_a_selected_plan_view():
    datum = DatumRef(
        frame=Frame((0.0, -25.0, 0.0), "y"),
        letter="B",
        view="side",
        side="left",
        reference_surface_kind="plane",
    )
    zones = {
        "side": (SimpleNamespace(left=None),),
        "plan": (SimpleNamespace(below=object()),),
    }
    assert _datum_alternate_view(datum, zones, {"side": object()}) is datum
    alternate = _datum_alternate_view(datum, zones, {"side": object(), "plan": object()})
    assert (alternate.view, alternate.side, alternate.letter) == ("plan", "below", "B")


def test_projected_datum_stem_keeps_both_near_and_far_datums_issue_2128():
    part = Box(80, 50, 20)
    near = DatumRef(frame=Frame((0, 0, -10), "z"), letter="A", view="front", side="below")
    far = DatumRef(frame=Frame((0, 0, 10), "z"), letter="B", view="front", side="below")
    # These different physical faces project onto the same front-view shaft.
    assert near.frame.origin[0] == far.frame.origin[0]
    assert near.frame.origin[2] != far.frame.origin[2]

    dwg = _build(near, far, part=part, page="A3", scale=1.0, scale_policy="permissive")

    assert {"m_gdt0", "m_gdt1"} <= set(dwg.annotations())
    assert not [issue for issue in dwg.registry.issues if issue.code == "gdt_dropped"]
    assert not [issue for issue in dwg.lint() if issue.code == "annotation_ink_overlap"]


def test_datum_compacts_through_empty_part_of_conservative_obstacle(monkeypatch):
    """A broad obstacle box must not force remote GD&T ink when its real ink is clear."""
    from draftwright.annotations import _common

    real_obstacles = _common.strip_obstacles

    def with_empty_hull(drawing, view=None, *, crossable=(), named=False):
        obstacles = real_obstacles(drawing, view=view, crossable=crossable, named=named)
        if view != "front":
            return obstacles
        hull = (30.0, 42.0, 115.0, 58.0)
        return [*obstacles, ("dimension:empty-hull", hull) if named else hull]

    monkeypatch.setattr(_common, "strip_obstacles", with_empty_hull)
    datum = DatumRef(frame=Frame((30.0, 0.0, 0.0), "z"), letter="A", view="front", side="below")
    dwg = _build(datum)
    placed = dwg.get_annotation("m_gdt0")

    # The conservative solve has to clear the injected 16 mm-deep hull. The
    # exact-ink contraction can reclaim that empty space and restores the first
    # legal below-view tier, while retaining the declaration's registry identity.
    assert placed.tip[1] - placed.elbow[1] < 30.0
    assert dwg.registry.names_for_feature(datum) == ["m_gdt0"]


def test_cosited_gdt_leaders_compact_as_a_stack(monkeypatch):
    """A sibling's shared shaft must not pin both labels at their old remote tiers (#1756)."""
    from draftwright.annotations import _common

    real_obstacles = _common.strip_obstacles

    def with_empty_hull(drawing, view=None, *, crossable=(), named=False):
        obstacles = real_obstacles(drawing, view=view, crossable=crossable, named=named)
        if view != "front":
            return obstacles
        hull = (30.0, 42.0, 115.0, 78.0)
        return [*obstacles, ("dimension:empty-hull", hull) if named else hull]

    monkeypatch.setattr(_common, "strip_obstacles", with_empty_hull)
    origin = Frame((30.0, 0.0, 0.0), "z")
    datum = DatumRef(frame=origin, letter="B", view="front", side="below")
    control = ControlFrame(
        frame=origin,
        characteristic="perpendicularity",
        tolerance="0.1",
        view="front",
        side="below",
        datums=("B",),
    )
    dwg = _build(datum, control)

    leaders = [dwg.get_annotation(name) for name in ("m_gdt0", "m_gdt1")]
    lengths = [_leader_path_length(leader) for leader in leaders]
    assert max(lengths) < 45.0, lengths
    assert not [issue for issue in dwg.lint() if issue.code == "annotation_ink_overlap"]


def test_stacked_frames_reserve_real_footprint():
    # Two frames on the same above strip. If placement reserved only one label-height
    # (the pre-#61 (tier, tier) hardcode) the ~6 mm-tall glyphs would overlap; the real
    # footprint keeps their centres >= one glyph-height apart. Different sites so the
    # leader shafts are not collinear and each frame's glyph edge is the strip-far bbox
    # edge (an above strip stacks the glyph at the TOP: centre = max.Y - h/2).
    f0 = ControlFrame(
        frame=Frame((-20.0, 0.0, 0.0), "z"),
        characteristic="position",
        tolerance="0.1",
        view="plan",
        side="above",
    )
    f1 = ControlFrame(
        frame=Frame((20.0, 0.0, 0.0), "z"),
        characteristic="flatness",
        tolerance="0.05",
        view="plan",
        side="above",
    )
    dwg = _build(f0, f1)
    assert {"m_gdt0", "m_gdt1"} <= set(dwg.annotations())
    h = _fcf_height()
    c0 = dwg.get_annotation("m_gdt0").bounding_box().max.Y - h / 2
    c1 = dwg.get_annotation("m_gdt1").bounding_box().max.Y - h / 2
    assert abs(c0 - c1) >= h - 1e-6  # glyphs do not overlap in the stack


def test_congested_side_falls_through_to_opposite():
    # #481: the plan-below strip carries the overall-width envelope dim, so a frame declared
    # there has no free tier — but rather than drop, render_gdt falls through to the OPPOSITE
    # side (plan-above) and places it there. No gdt_dropped warning; the frame survives.
    frame = ControlFrame(
        frame=Frame((0.0, 0.0, 0.0), "z"),
        characteristic="position",
        tolerance="0.1",
        view="plan",
        side="below",
    )
    dwg = _build(frame)
    assert "m_gdt0" in dwg.annotations()  # recovered on the opposite side
    assert not [i for i in dwg.registry.issues if i.code == "gdt_dropped"]
    assert not [x for x in dwg.lint() if x.code == "annotation_out_of_bounds"]
    # It landed ABOVE the plan view (the fallthrough side): the frame (leader's far end) sits
    # above the view centre, whereas a below placement would keep the whole box at/under it.
    assert (
        dwg.get_annotation("m_gdt0").bounding_box().max.Y > dwg.at("plan", *dwg.centroid)[1] + 10
    )


@pytest.mark.parametrize("side", ["above", "below"])
def test_gdt_never_overlaps_the_title_block(side):
    # #481 review (CONFIRMED, both paths): the side/below strip runs down into the title-block
    # region, which is added AFTER the corridor drain — so neither the PRIMARY corridor solve
    # (force-kept) nor the fallthrough's carve can see it. Stacking frames on the side view (the
    # bottom-right one) must REJECT any spot over the title block (drop/relocate) rather than
    # overlap 'DRAWING'. side="below" exercises the primary path, side="above" the fallthrough.
    frames = [
        ControlFrame(
            frame=Frame((x, 0.0, 0.0), "z"),
            characteristic="position",
            tolerance="0.1",
            view="side",
            side=side,
        )
        for x in (-20.0, 0.0, 20.0)
    ]
    dwg = _build(*frames)
    assert not [x for x in dwg.lint() if x.code == "annotation_overlap"]


def test_bad_target_drops_without_crashing():
    frame = ControlFrame(
        frame=Frame((0.0, 0.0, 0.0), "z"),
        characteristic="position",
        tolerance="0.1",
        view="nope",
        side="above",
    )
    dwg = _build(frame)
    dropped = [i for i in dwg.registry.issues if i.code == "gdt_dropped"]
    assert dropped and dropped[0].outcome_stage == "validation"
    summary = dwg.lint_summary()
    assert "gdt_dropped" not in summary["quality"]["legibility"]["by_code"]
    serialized = next(issue for issue in summary["issues"] if issue["code"] == "gdt_dropped")
    assert serialized["outcome_stage"] == "validation"
    assert "m_gdt0" not in dwg.annotations()


def test_invalid_glyph_spec_drops_not_crashes():
    # The IR is public input (ADR 4 (was 0011)): a mistyped characteristic must drop the one item
    # with a gdt_dropped warning, NOT raise ValueError and take down the whole drawing.
    frame = ControlFrame(
        frame=Frame((0.0, 0.0, 0.0), "z"),
        characteristic="postion",  # codespell:ignore postion — deliberate typo; helper raises "Unknown characteristic"
        tolerance="0.1",
        view="plan",
        side="above",
    )
    dwg = _build(frame)  # must not raise
    dropped = [i for i in dwg.registry.issues if i.code == "gdt_dropped"]
    assert dropped and "m_gdt0" in dropped[0].message
    assert dropped[0].outcome_stage == "validation"
    assert "m_gdt0" not in dwg.annotations()


def test_wide_frame_in_narrow_strip_relaxes_not_overshoots(tmp_path):
    # Adversarial-review finding (CONFIRMED): a wide GD&T glyph (multi-datum FCF ~33 mm) on a
    # left/right strip narrower than the glyph must never render off the drawable area
    # (annotation_out_of_bounds — pre-fix it placed at min.X=-7.17, 17 mm past outer_limit).
    # The requested left/right strips are too narrow, so under the #841 side auto-relax the
    # frame moves to a wider above/below strip where it fits IN BOUNDS — placed (with a
    # gdt_side_relaxed info notice), not overshooting and not vanishing.
    frame = ControlFrame(
        frame=Frame((0.0, 0.0, 0.0), "z"),
        characteristic="position",
        tolerance="0.1",
        view="plan",
        side="left",
        datums=("A", "B"),
    )
    trace_path = tmp_path / "gdt-relaxed.json"
    dwg = _build(frame, trace=trace_path)
    assert "m_gdt0" in dwg.annotations()  # placed on a relaxed side, not dropped
    assert [i for i in dwg.registry.issues if i.code == "gdt_side_relaxed"]
    assert not [i for i in dwg.registry.issues if i.code == "gdt_dropped"]
    assert not [x for x in dwg.lint() if x.code == "annotation_out_of_bounds"]  # never overshoots
    events = [
        event
        for event in json.loads(trace_path.read_text())["pass_events"]
        if event["label"] == "gdt_post_drain_fallback"
    ]
    assert len(events) == 1
    assert events[0]["items"][0]["outcome"] == "placed"
    assert events[0]["items"][0]["side"] in {"above", "below"}


def test_full_adjacent_strips_fall_back_to_clear_sheet_space(monkeypatch, tmp_path):
    """A required frame may use the sheet after every adjacent strip is exhausted."""
    import draftwright.annotations.from_model as from_model

    monkeypatch.setattr(from_model, "carve_free_position", lambda *_args, **_kwargs: None)
    frame = ControlFrame(
        frame=Frame((0.0, 0.0, 0.0), "z"),
        characteristic="position",
        tolerance="0.1",
        view="plan",
        side="left",
        datums=("A", "B"),
    )
    trace_path = tmp_path / "gdt-fallback.json"
    dwg = _build(frame, trace=trace_path)

    assert "m_gdt0" in dwg.annotations()
    assert [i for i in dwg.registry.issues if i.code == "gdt_sheet_fallback"]
    assert dwg.registry.declaration_of("m_gdt0") is frame
    assert not [i for i in dwg.registry.issues if i.code == "gdt_dropped"]
    assert not [i for i in dwg.lint() if i.code == "annotation_out_of_bounds"]
    events = [
        event
        for event in json.loads(trace_path.read_text())["pass_events"]
        if event["label"] == "gdt_post_drain_fallback"
    ]
    assert [event["items"][0]["outcome"] for event in events] == ["placed"]
    assert events[0]["items"][0]["side"] == "sheet"
    assert [attempt["outcome"] for attempt in events[0]["items"][0]["attempts"]] == [
        "no_free_position",
        "no_free_position",
        "no_free_position",
        "placed",
    ]


def test_gdt_fallback_trace_names_unmet_after_every_route_is_exhausted(monkeypatch, tmp_path):
    import draftwright.annotations.from_model as from_model

    # A deliberately wide frame cannot fit the requested left strip on this fixed A4 page.
    # Permissive mode returns the incomplete drawing for inspection rather than raising.
    monkeypatch.setattr(from_model, "carve_free_position", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(from_model, "_sheet_leader_fallback", lambda *_args, **_kwargs: None)
    frame = ControlFrame(
        frame=Frame((0.0, 0.0, 0.0), "z"),
        characteristic="position",
        tolerance="0.1",
        view="plan",
        side="left",
        datums=("A", "B", "C", "D", "E", "F"),
    )
    trace_path = tmp_path / "gdt-unmet.json"
    dwg = _build(frame, trace=trace_path, page="A4", scale=1.0, scale_policy="permissive")

    assert "m_gdt0" not in dwg.annotations()
    assert [issue for issue in dwg.registry.issues if issue.code == "gdt_dropped"]
    events = [
        event
        for event in json.loads(trace_path.read_text())["pass_events"]
        if event["label"] == "gdt_post_drain_fallback"
    ]
    assert len(events) == 1
    assert events[0]["items"] == [
        {
            "name": "m_gdt0",
            "outcome": "unmet",
            "attempts": [
                {"side": "right", "outcome": "no_free_position"},
                {"side": "above", "outcome": "no_free_position"},
                {"side": "below", "outcome": "no_free_position"},
                {"side": "sheet", "outcome": "no_clear_route"},
            ],
        }
    ]


def test_note_relaxes_side_when_requested_strip_full():
    # #841 confirmed-behaviour #2 / outcome C: an anchored note whose requested view/side strip
    # has no room must NOT silently drop — it auto-relaxes to a strip that fits (with a
    # gdt_side_relaxed info notice), so a requested annotation always appears somewhere legible.
    # A wide note text on the narrow left strip forces the relaxation to above/below.
    note = Note(
        frame=Frame((0.0, 0.0, 0.0), "z"),
        text="5X OBROUND SLOT 13.60 X 7.88",
        view="plan",
        side="left",
    )
    dwg = _build(note)
    assert "m_gdt0" in dwg.annotations()  # placed, not dropped
    assert [i for i in dwg.registry.issues if i.code == "gdt_side_relaxed"]
    assert not [i for i in dwg.registry.issues if i.code == "gdt_dropped"]
    assert not [x for x in dwg.lint() if x.code == "annotation_out_of_bounds"]


def test_note_honors_requested_side_when_it_fits():
    # The relax fires ONLY when the requested strip is full: a note that fits its requested side
    # is placed there with NO gdt_side_relaxed notice (no spurious relaxation).
    note = Note(frame=Frame((0.0, 0.0, 0.0), "z"), text="DEBURR", view="plan", side="above")
    dwg = _build(note)
    assert "m_gdt0" in dwg.annotations()
    assert not [i for i in dwg.registry.issues if i.code == "gdt_side_relaxed"]


def test_degenerate_leader_site_does_not_crash():
    # Focused-review finding (CONFIRMED): a declared site that projects ONTO the solved strip
    # tier (pos == py) makes the leader shaft zero-length, which raised in OCC and crashed the
    # whole build (public IR). _build now guarantees a minimum shaft, so it places instead.
    # oy=44.5 is the reviewer's reproduced coincidence for this part/part-of-strip geometry.
    frame = ControlFrame(
        frame=Frame((0.0, 44.5, 0.0), "z"),
        characteristic="position",
        tolerance="0.1",
        view="plan",
        side="above",
    )
    dwg = _build(frame)  # must not raise
    assert "m_gdt0" in dwg.annotations()


def test_placement_is_lint_clean():
    # The Tier-1 claim: a frame placed through the strip solve does not overlap the dims
    # it annotates (the failure mode that motivated routing GD&T through the solver).
    frame = ControlFrame(
        frame=Frame((0.0, 0.0, 0.0), "z"),
        characteristic="position",
        tolerance="0.1",
        view="plan",
        side="above",
        datums=("A",),
        diameter=True,
    )
    dwg = _build(frame)
    overlaps = [x for x in dwg.lint() if x.code == "annotation_overlap"]
    assert not overlaps


def test_provenance_back_link():
    # A frame decorating a detected hole records that hole as its annotation's feature
    # (ADR 5 (was 0010) provenance) so the read/edit surface can find it.
    m = detect_part_model(_part())
    hole = next(f for f in m.features if f.kind == "hole")
    m.features.append(
        ControlFrame(
            frame=Frame((0.0, 0.0, 0.0), "z"),
            characteristic="position",
            tolerance="0.1",
            view="plan",
            side="above",
            origin=hole,
        )
    )
    dwg = build_drawing(_part(), model=m)
    assert "m_gdt0" in dwg.annotations_of(hole)
