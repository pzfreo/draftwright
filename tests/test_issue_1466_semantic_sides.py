"""Separate GRM04 hole sizes and location dimensions through authored side hints."""

from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import Sheet, build_drawing
from draftwright.audit import compare_measurements
from draftwright.linting.issues import LintIssue
from draftwright.model import hole
from draftwright.sheet_emit import emit_sheet_script, generate_sheet_script


def _execute(source):
    namespace = {}
    exec(compile(source, "<semantic-sides>", "exec"), namespace)
    return namespace


@pytest.fixture(scope="module")
def grm04_scripts(tmp_path_factory):
    out = tmp_path_factory.mktemp("semantic-sides")
    path = generate_sheet_script(
        str(Path(__file__).parent / "fixtures" / "grm04_drive_plate.step"),
        out=str(out / "grm04"),
        title="GRM04",
        number="1466",
        scale=4,
        formats=(),
    )
    source = Path(path).read_text(encoding="utf-8")
    assert source.count('"bore.diameter")') == 2
    assert source.count('"height.length")') == 1
    edited = source.replace('"bore.diameter")', '"bore.diameter", side="left")')
    edited = edited.replace('"height.length")', '"height.length", side="left")')
    return _execute(source), _execute(edited), out


def _measurements(drawing):
    return Counter(
        (key["feature"], key["parameter_id"])
        for name in drawing.annotations()
        for key in drawing.measurement_keys(name)
    )


def test_side_edit_clears_demonstrated_crossing_without_losing_measurements(grm04_scripts):
    original, edited, _ = grm04_scripts
    before, after = original["drawing"], edited["drawing"]
    before_issues = before.lint()
    assert any(
        issue.code == "annotation_ink_overlap" and "⌀2.4 THRU" in issue.message
        for issue in before_issues
    ), "The fixture must exhibit the hole/location collision before editing"
    assert before.scale == after.scale == 4
    assert not any(
        issue.code in {"annotation_overlap", "annotation_ink_overlap", "placement_unsatisfiable"}
        for issue in after.lint()
    )
    assert _measurements(after) == _measurements(before)
    assert {"dim_step_0", "dim_step_1", "dim_height"} <= after.annotations().keys()
    assert len(after.lint()) < len(before_issues)


def test_discovery_and_emission_preserve_the_supported_sides(grm04_scripts):
    _, edited, out = grm04_scripts
    sheet = edited["sheet"]
    model = sheet.model()
    requested = [request for request in model.authored_dimensions if request.side == "left"]
    assert len(requested) == 3
    for request in requested:
        options = sheet.dimension_options(request.feature, request.role)
        assert {"view": None, "side": "left"} in options["placements"]
        assert sheet.validate_dimension(request.feature, request.role, side="left")["supported"]
    source = emit_sheet_script(
        model,
        "part",
        str(out / "roundtrip"),
        title="GRM04",
        number="1466",
        scale=4,
        formats=(),
    )
    namespace = {"part": edited["part"]}
    exec(compile(source, "<side-roundtrip>", "exec"), namespace)
    repeated = namespace["sheet"].model().authored_dimensions
    assert [(r.role, r.view, r.side) for r in repeated] == [
        (r.role, r.view, r.side) for r in model.authored_dimensions
    ]
    assert _measurements(namespace["drawing"]) == _measurements(edited["drawing"])


def test_grm04_edit_preserves_measurement_meaning_under_shared_declaration(grm04_scripts):
    original, _edited, _out = grm04_scripts
    before = original["drawing"]
    model = before.model()
    requests = tuple(
        replace(request, side="left")
        if request.role in {"bore.diameter", "height.length"}
        else request
        for request in model.authored_dimensions
    )
    assert sum(request.side == "left" for request in requests) == 3
    after = build_drawing(
        original["part"], model=replace(model, authored_dimensions=requests), scale=4
    )
    assert any(issue.code == "annotation_ink_overlap" for issue in before.lint())
    assert not any(
        issue.code in {"annotation_overlap", "annotation_ink_overlap"} for issue in after.lint()
    )
    comparison = compare_measurements(before, after)
    assert comparison["status"] == "preserved", comparison


def _side_sheet(kind, side, **options):
    stock = Box(20, 40, 50)
    cutter = Cylinder(2, 20, rotation=(0, 90, 0))
    if kind == "pattern":
        part = stock - Pos(0, 0, -10) * cutter - Pos(0, 0, 10) * cutter
    elif kind == "profile":
        cutter = Cylinder(4, 20, rotation=(0, 90, 0)) & Box(20, 5, 20)
        part = stock - cutter
    else:
        part = stock - cutter
    sheet = Sheet(part, scale=2, **options).authored_dimensions()
    if kind == "pattern":
        target = sheet.pattern(
            hole(diameter=4, depth=20, at=(0, 0, 0), axis="x"),
            kind="linear",
            count=2,
            pitch=20,
            direction=(0, 0, 1),
        )
    elif kind == "profile":
        target = sheet.double_d_bore(
            major_diameter=8,
            depth=20,
            at=(0, 0, 0),
            axis="x",
            across_flats=5,
            profile_direction=(0, 1, 0),
        )
    else:
        target = sheet.hole(diameter=4, depth=20, at=(0, 0, 0), axis="x")
    for parameter_id in target.dimension_ids():
        sheet.dimension(
            target, parameter_id, side=side if parameter_id == "bore.diameter" else None
        )
    envelope = sheet.envelope()
    for parameter_id in envelope.dimension_ids():
        sheet.dimension(
            envelope, parameter_id, side=side if parameter_id == "height.length" else None
        )
    return sheet


@pytest.mark.parametrize("kind", ["single", "pattern", "profile"])
@pytest.mark.parametrize("side", ["left", "right"])
def test_native_hole_routes_and_height_honour_the_requested_side(kind, side):
    drawing = _side_sheet(kind, side).build()
    leaders = [
        annotation
        for name, annotation in drawing.iter_annotations()
        if any(key["parameter_id"] == "bore.diameter" for key in drawing.measurement_keys(name))
    ]
    assert len(leaders) == 1
    left, _, right, _ = drawing.view_bounds("side")
    box = leaders[0].bounding_box()
    if side == "left":
        assert box.max.X < (left + right) / 2
    else:
        assert box.min.X > (left + right) / 2
    height = drawing.get_annotation("dim_height").bounding_box()
    left, _, right, _ = drawing.view_bounds("front")
    assert height.max.X < left if side == "left" else height.min.X > right
    assert not any(issue.code.endswith("_dropped") for issue in drawing.lint())


def test_a_full_authored_side_is_reported_instead_of_using_the_other_side(monkeypatch):
    import draftwright.annotations.holes as holes

    annotate = holes._annotate_holes
    attempts = []

    def narrow_left(drawing, analysis, *args, **kwargs):
        edge_left = analysis.proj.side_x(analysis.bb.min.Y)
        bounded = replace(analysis, margin=edge_left + 1)
        assert bounded.margin > edge_left
        attempts.append(bounded)
        return annotate(drawing, bounded, *args, **kwargs)

    monkeypatch.setattr("draftwright.annotations.orchestrator._annotate_holes", narrow_left)
    control = _side_sheet("single", "right", scale_policy="permissive").build()
    assert any(
        key["parameter_id"] == "bore.diameter"
        for name in control.annotations()
        for key in control.measurement_keys(name)
    ), "The opposite side must still fit, so the authored restriction decides the refusal"
    drawing = _side_sheet("single", "left", scale_policy="permissive").build()
    assert attempts
    assert not any(
        key["parameter_id"] == "bore.diameter"
        for name in drawing.annotations()
        for key in drawing.measurement_keys(name)
    )
    refusals = [issue for issue in drawing.lint() if issue.code == "callout_dropped"]
    assert len(refusals) == 1
    assert "requested left side" in refusals[0].message and "page margin" in refusals[0].message
    assert any(mid.parameter == "bore.diameter" for mid in refusals[0].measurement_ids)


def _inject_repair_attempt(monkeypatch, drawing, name):
    original = drawing.get_annotation(name)

    def diagnosis(**kwargs):
        if drawing.get_annotation(name) is original:
            return [
                LintIssue(
                    "warning",
                    f"dimension '{original.label}' is inside the part",
                    code="dim_inside_part",
                )
            ]
        return []

    monkeypatch.setattr(drawing, "lint", diagnosis)
    assert drawing.lint()[0].code == "dim_inside_part"
    return original


def test_pin_blocks_a_repair_attempt_beside_the_authored_left_height(monkeypatch):
    drawing = _side_sheet("single", "left").build()

    # Inject a repairable diagnosis to exercise the pin, rather than letting clean lint
    # make both pinned and unpinned calls pass without attempting a change.
    original = _inject_repair_attempt(monkeypatch, drawing, "m_env_depth")
    drawing.pin("m_env_depth").repair()
    assert drawing.get_annotation("m_env_depth") is original
    drawing.unpin("m_env_depth").repair()
    assert drawing.get_annotation("m_env_depth") is not original


@pytest.mark.parametrize("side", ["left", "right"])
def test_repair_does_not_relax_an_authored_side(monkeypatch, side):
    automatic = _side_sheet("single", None).build()
    auto_height = _inject_repair_attempt(monkeypatch, automatic, "dim_height")
    automatic.repair()
    assert automatic.get_annotation("dim_height") is not auto_height

    drawing = _side_sheet("single", side).build()
    original = _inject_repair_attempt(monkeypatch, drawing, "dim_height")
    assert not drawing.registry.is_pinned("dim_height")
    drawing.repair()
    assert drawing.get_annotation("dim_height") is original


def test_same_side_label_rebuild_preserves_the_authored_constraint(monkeypatch):
    from draftwright._core import _dim
    from draftwright.repair import _replace_dim

    drawing = _side_sheet("single", "left").build()
    before = _measurements(drawing)
    original = drawing.get_annotation("dim_height")
    spec = original._dw_spec
    rebuilt = _dim(
        spec.p1,
        spec.p2,
        spec.side,
        spec.distance,
        spec.draft,
        **{**spec.kwargs, "label_offset_x": 1},
    )
    # This is the replacement seam used by witness-label reconciliation. Exercise
    # subsequent repair too: merely retaining the original side string is insufficient.
    _replace_dim(drawing, original, rebuilt)
    assert drawing.get_annotation("dim_height") is rebuilt
    _inject_repair_attempt(monkeypatch, drawing, "dim_height")
    drawing.repair()
    assert drawing.get_annotation("dim_height") is rebuilt
    assert _measurements(drawing) == before


def test_height_retry_predicts_the_geometry_it_builds_after_a_predecessor_is_placed(monkeypatch):
    import draftwright.annotations.from_model as renderer

    captured = {}
    register = renderer.register_corridor

    def observe(ctx, key, strip, view, axis, tier, candidate):
        if candidate.name == "dim_height":
            captured.update(strip=strip, candidate=candidate)
        return register(ctx, key, strip, view, axis, tier, candidate)

    monkeypatch.setattr(renderer, "register_corridor", observe)
    part = Pos(0, 0, -5) * Box(40, 30, 10) + Pos(10, 0, 5) * Box(20, 30, 10)
    drawing = build_drawing(part, scale=2)
    strip, candidate = captured["strip"], captured["candidate"]
    inner = strip.anchor + strip.direction * strip.gap
    predecessor = drawing.get_annotation("dim_step_0")._dw_spec
    assert predecessor.p1[0] + predecessor.distance == pytest.approx(inner)
    # The force pass probes the strip's inner position again after earlier candidates
    # have been built. A valid predicted footprint must not become a zero-offset build.
    predicted = candidate.footprint(inner)
    actual = candidate.build(inner).bounding_box()
    assert predicted == pytest.approx(
        (actual.min.X, actual.min.Y, actual.max.X, actual.max.Y), abs=0.06
    )
