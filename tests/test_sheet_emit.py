"""The declarative Sheet-DSL emitter (ADR 0011 Amendment 1, #461).

Generates a `Sheet(...)` script from a detected part — one commentable line per feature.
Detected input only writes numbers (the part-seam form); we never fabricate geometry.
"""

import ast
import math
import os
from pathlib import Path

import pytest
from build123d import Box, Cylinder, Pos, Shape, export_step

from draftwright.builder import build_drawing, detect_part_model
from draftwright.pmi import _PMI_AVAILABLE
from draftwright.sheet_emit import (
    emit_sheet_script,
    generate_sheet_script,
    resolve_object_spec,
)

# A throwaway source module the object-spec tests import a live part off (#469): an object,
# a zero-arg factory, a non-Shape, and a callable that needs args (the guard-rail case).
_SOURCE_MODULE = (
    "from build123d import Box, Cylinder, Pos\n"
    "bracket = Box(80, 50, 8) - Pos(20, 10, 4) * Cylinder(4, 20)\n"
    "def make_bracket():\n    return Box(30, 20, 5)\n"
    "NOT_A_SHAPE = 42\n"
    "NONE_BOUND = None\n"  # exists but bound to None — the wrong-type, not-missing case
    "def needs_args(x):\n    return x\n"
)

AP242_CTC01 = Path(__file__).parent / "fixtures" / "nist_ctc_01_asme1_ap242.stp"


def _norm(s: str) -> str:
    """Flatten a rich-rendered CLI panel for substring checks: drop ANSI colour and
    box-drawing borders, collapse whitespace — so a line-wrapped phrase reads contiguously."""
    import re

    s = re.sub(r"\x1b\[[0-9;]*m", "", s)  # ANSI colour codes
    s = re.sub(r"[│╭╮╰╯─┌┐└┘|]", " ", s)  # panel borders
    return " ".join(s.split())


def _plate():
    return Box(80, 50, 8) - Pos(20, 10, 4) * Cylinder(4, 20) - Pos(-20, 10, 4) * Cylinder(4, 20)


def _script_for(part, part_expr="part = PART", stem="drawing", **kw):
    return emit_sheet_script(detect_part_model(part), part_expr, stem, title="T", number="N", **kw)


class TestEmit:
    def test_emits_one_declarative_line_per_feature(self):
        src = _script_for(_plate())
        assert "sheet = Sheet(part, title='T', number='N')" in src
        assert "sheet.hole(diameter=8" in src  # the ⌀8 holes
        assert "sheet.add(EnvelopeFeature(" in src
        assert src.rstrip().endswith("sheet.export('drawing')")

    def test_non_default_formats_are_emitted_into_the_export_call(self):
        # #709: --format must survive into the generated script; the default (pdf)
        # keeps the bare call so a plain script matches Sheet.export's own default.
        src = _script_for(_plate(), formats=("svg", "dxf"))
        assert src.rstrip().endswith("sheet.export('drawing', formats=('svg', 'dxf'))")
        assert _script_for(_plate(), formats=("pdf",)).rstrip().endswith("sheet.export('drawing')")

    def test_step_seam_preserves_detected_ctc01_envelope(self, tmp_path):
        # #536: build123d.import_step reports CTC01's raw bbox as 1170 × 650, but the
        # detector's solid-body envelope is 800 × 450. The generated STEP-seam script
        # must preserve the detected EnvelopeFeature literally instead of remeasuring
        # the raw imported object with sheet.envelope().
        step = Path(__file__).parent / "fixtures" / "nist_ctc_01_asme1_ap203.stp"
        py = generate_sheet_script(str(step), out=str(tmp_path / "ctc01"))
        src = Path(py).read_text(encoding="utf-8")
        assert "sheet.envelope()" not in src
        assert "sheet.add(EnvelopeFeature(" in src
        assert "width=800" in src
        assert "depth=450" in src
        assert "1170" not in src

    @pytest.mark.skipif(not _PMI_AVAILABLE, reason="OCP GDT support not available")
    def test_step_seam_emits_ap242_pmi_as_sheet_dimensions(self, tmp_path):
        py = generate_sheet_script(str(AP242_CTC01), out=str(tmp_path / "ctc01"), pmi="annotate")
        src = Path(py).read_text(encoding="utf-8")
        ast.parse(src)
        assert "sheet.dimension(" in src
        assert "sheet.pmi(" not in src
        assert "# authored_dimension" not in src
        assert "source='ap242_pmi'" in src
        assert "sheet.add(PmiFeature(" in src
        assert "sheet.step_level(" in src  # #578: fluent verb, no StepLevelFeature import
        import_line = next(
            ln for ln in src.splitlines() if ln.startswith("from draftwright.model")
        )
        assert "Frame" in import_line and "PmiFeature" in import_line
        assert "StepLevelFeature" not in import_line

    def test_sheet_dimension_declares_renderable_authored_dimension(self, tmp_path):
        from draftwright import Sheet

        sheet = Sheet(Box(40, 20, 10), title="P", out=str(tmp_path / "dim"))
        sheet.dimension(
            kind="linear",
            value=40,
            label="40",
            dominant_axis="X",
            ref_bbox=(-20, -10, -5, 20, 10, 5),
            ref_pts=[(-20, 0, 0), (20, 0, 0)],
            upper_tol=0.1,
            lower_tol=0.0,
        )

        feat = next(f for f in sheet.model().features if f.kind == "authored_dimension")
        assert feat.upper_tol == 0.1
        assert feat.lower_tol == 0.0
        assert feat.source == "sheet"
        assert any(n.startswith("pmi_") for n in sheet.build().annotations())

    def test_sheet_dimension_rejects_unrenderable_kind(self):
        from draftwright import Sheet

        sheet = Sheet(Box(40, 20, 10), title="P")
        with pytest.raises(ValueError, match="kind must be one of"):
            sheet.dimension(
                kind="liner",
                value=40,
                label="40",
                dominant_axis="X",
                ref_pts=[(-20, 0, 0), (20, 0, 0)],
                ref_bbox=(-20, -10, -5, 20, 10, 5),
            )

    def test_sheet_dimension_rejects_unrenderable_axis(self):
        from draftwright import Sheet

        sheet = Sheet(Box(40, 20, 10), title="P")
        with pytest.raises(ValueError, match="dominant_axis must be X, Y, or Z"):
            sheet.dimension(
                kind="linear",
                value=40,
                label="40",
                dominant_axis="XX",
                ref_pts=[(-20, 0, 0), (20, 0, 0)],
                ref_bbox=(-20, -10, -5, 20, 10, 5),
            )

    def test_refpts_only_linear_dim_renders_on_each_axis(self):
        # #562: a linear Sheet.dimension() with two valid ref_pts and NO ref_bbox must
        # render (the witness is derived from the ref points) — for X, Y, and Z. Z needs
        # the front left/right strip reserved for authored height dims (the sizing fix).
        from draftwright import Sheet

        cases = {
            "X": [(-40, -30, 0), (-30, -30, 0)],
            "Y": [(-40, -30, 0), (-40, -20, 0)],
            "Z": [(-38, -30, 5), (-38, -30, 15)],
        }
        for axis, ref_pts in cases.items():
            sheet = Sheet(Box(80, 60, 40), title="P")
            sheet.dimension(
                kind="linear",
                value=10,
                label="10",
                dominant_axis=axis,
                ref_pts=ref_pts,
                at=ref_pts[0],
            )
            dwg = sheet.build()
            placed = [n for n in dwg.annotations() if n.startswith("pmi_")]
            assert placed, f"{axis}: ref_pts-only dim was not placed"
            assert not any(i.code == "pmi_dropped" for i in dwg.registry.issues), axis

    def test_degenerate_refpts_reports_specific_code_not_no_room(self):
        # #562: a genuinely unrenderable ref (two coincident points → no span) reports a
        # specific validation code, NOT the misleading "no room" — the latter is reserved
        # for a real candidate that reached the corridor solver and could not fit.
        from draftwright import Sheet

        sheet = Sheet(Box(80, 60, 40), title="P")
        sheet.dimension(
            kind="linear",
            value=10,
            label="10",
            dominant_axis="Z",
            ref_pts=[(-38, -30, 5), (-38, -30, 5)],
            at=(-38, -30, 5),
        )
        dwg = sheet.build()
        codes = {i.code for i in dwg.registry.issues}
        assert "authored_dim_degenerate" in codes
        assert "pmi_dropped" not in codes

    def test_title_block_and_layout_aspects_emitted_when_set(self):
        # #474: non-default drawn_by/tolerance/scale/page ride the Sheet(...) constructor.
        ctor = next(
            ln
            for ln in _script_for(
                _plate(), drawn_by="PF", tolerance="ISO 2768-f", scale=2.0, page="A3"
            ).splitlines()
            if "Sheet(part" in ln
        )
        assert "drawn_by='PF'" in ctor
        assert "tolerance='ISO 2768-f'" in ctor
        assert "scale=2.0" in ctor
        assert "page='A3'" in ctor

    def test_default_aspects_stay_off_the_constructor(self):
        # unset aspects (and tolerance left at the ISO 2768-m default) never appear.
        ctor = next(ln for ln in _script_for(_plate()).splitlines() if "Sheet(part" in ln)
        assert ctor == "sheet = Sheet(part, title='T', number='N')"

    def test_count_group_hole_carries_its_members(self):
        # a count>1 hole MUST emit members= with every position — without them the render
        # collapses to a single hole at the anchor (fidelity loss). The plate has two ⌀8 holes.
        line = next(
            ln
            for ln in _script_for(_plate()).splitlines()
            if ln.startswith("sheet.hole(diameter=8")
        )
        call = ast.parse(line, mode="eval").body  # the sheet.hole(...) Call node
        kw = {k.arg: k.value for k in call.keywords}
        assert ast.literal_eval(kw["count"]) == 2
        assert len(kw["members"].elts) == 2  # both hole positions spelled out

    def test_output_is_valid_python(self):
        # the whole emitted script must parse — a generated script that doesn't is useless
        ast.parse(_script_for(_plate()))

    def test_countersink_rides_the_hole_line(self):
        # #575: a detected countersunk hole emits csink=(major, angle) on the hole line
        # (was dropped) — the emit surface for #558.
        from build123d import Cone

        part = Box(90, 60, 12)
        for x, y in [(-30, -15), (5, 12), (30, -8)]:
            part -= Pos(x, y, 0) * Cylinder(3, 12)
            part -= Pos(x, y, 4) * Cone(3, 7, 4)
        src = _script_for(part)
        assert "csink=(14" in src
        ast.parse(src)

    def test_countersunk_bolt_circle_keeps_csink_on_members(self):
        # #582 review (BLOCKER): a countersunk PATTERN member must emit csink= (was dropped in
        # _member_hole_str), else the bolt circle loses its ⌵ callout on re-run.
        from build123d import Cone

        part = Cylinder(40, 12)
        for i in range(6):
            a = i * math.pi / 3
            c = Pos(25 * math.cos(a), 25 * math.sin(a), 0)
            part -= c * Cylinder(3, 20)
            part -= c * Pos(0, 0, 4) * Cone(3, 7, 4)
        src = _script_for(part)
        pat = [ln for ln in src.splitlines() if "sheet.pattern(hole(" in ln]
        assert pat and "csink=(14" in pat[0]
        ast.parse(src)

    def test_plate_emits_the_plate_verb(self):
        # #577: a multi-plate part emits sheet.plate(...) per slab (was dropped).
        part = Box(80, 50, 8) + Pos(-36, 0, 29) * Box(8, 50, 50)  # base plate + upright wall
        src = _script_for(part)
        assert src.count("sheet.plate(") == 2
        # Assert the emitted fields, not just the verb count — a swapped axis/lo/hi/u/v
        # would still parse and count 2. The base slab is a Z plate lo=-4 hi=4; the
        # upright wall is an X plate lo=-40 hi=-32.
        assert 'sheet.plate(axis="z", lo=-4, hi=4,' in src
        assert 'sheet.plate(axis="x", lo=-40, hi=-32,' in src
        ast.parse(src)

    def test_chamfer_emits_the_chamfer_verb(self):
        # #576: a detected chamfer emits `sheet.chamfer(...)` (was a "no declarative verb yet"
        # comment) — the emit surface for #560.
        from build123d import Axis
        from build123d import chamfer as bd_chamfer

        part = Box(80, 50, 8)
        e = part.edges().filter_by(Axis.Z).sort_by(lambda x: x.center().X + x.center().Y)[-1]
        src = _script_for(bd_chamfer(e, 4))
        assert "sheet.chamfer(" in src and "leg1=4" in src
        ast.parse(src)

    def test_fillet_emits_the_fillet_verb(self):
        # #561: a detected fillet emits `sheet.fillet(...)` — the emit surface for the R callout.
        from build123d import Axis
        from build123d import fillet as bd_fillet

        part = Box(80, 50, 8)
        e = part.edges().filter_by(Axis.Z).sort_by(lambda x: x.center().X + x.center().Y)[-1]
        src = _script_for(bd_fillet(e, 3))
        assert "sheet.fillet(" in src and "radius=3" in src
        ast.parse(src)

    def test_fillet_round_trips_the_full_feature(self):
        # The emitted line execs back to the same FilletFeature (axis/radius/at).
        from build123d import Axis
        from build123d import fillet as bd_fillet

        from draftwright import build_drawing
        from draftwright.model import fillet as declare_fillet
        from draftwright.sheet_emit import _feature_line

        part = bd_fillet(Box(80, 50, 8).edges().filter_by(Axis.Z).sort_by(Axis.X)[-1], 3)
        det = next(
            f for f in build_drawing(part, number="X").model().features if f.kind == "fillet"
        )
        line = _feature_line(det)  # "sheet.fillet(axis=..., radius=..., at=...)"
        env = {"sheet": type("S", (), {"fillet": staticmethod(declare_fillet)})()}
        redeclared = eval(line, {"__builtins__": {}}, env)  # noqa: S307
        assert redeclared.axis == det.axis and redeclared.radius == det.radius
        assert redeclared.frame.origin == pytest.approx(det.frame.origin)

    def test_flat_emits_the_flat_verb(self):
        # #148b: a detected machined flat emits `sheet.flat(...)` — the emit surface for the
        # across-flats callout.
        part = Cylinder(10, 30) - Pos(10, 0, 0) * Box(10, 40, 40)  # D-shaft, flat at x=5
        src = _script_for(part)
        assert "sheet.flat(" in src and 'axis="z"' in src and "across=15" in src
        ast.parse(src)

    def test_flat_round_trips_the_full_feature(self):
        # The emitted line execs back to the same FlatFeature (axis/across/at).
        from draftwright.model import flat as declare_flat
        from draftwright.sheet_emit import _feature_line

        part = Cylinder(10, 30) - Pos(10, 0, 0) * Box(10, 40, 40)
        det = next(f for f in build_drawing(part, number="X").model().features if f.kind == "flat")
        line = _feature_line(det)  # "sheet.flat(axis=..., across=..., at=...)"
        env = {"sheet": type("S", (), {"flat": staticmethod(declare_flat)})()}
        redeclared = eval(line, {"__builtins__": {}}, env)  # noqa: S307
        assert redeclared.axis == det.axis and redeclared.across == det.across
        assert redeclared.frame.origin == pytest.approx(det.frame.origin)

    def test_groove_emits_the_groove_verb(self):
        # #148c: a detected turned groove emits `sheet.groove(...)` — the emit surface for the
        # width + floor-diameter callout.
        part = Cylinder(10, 40) - (Cylinder(10, 4) - Cylinder(8, 4))  # circlip groove
        src = _script_for(part)
        assert "sheet.groove(" in src and 'axis="z"' in src
        assert "width=4" in src and "diameter=16" in src
        ast.parse(src)

    def test_groove_round_trips_the_full_feature(self):
        # The emitted line execs back to the same GrooveFeature (axis/width/diameter/at).
        from draftwright.model import groove as declare_groove
        from draftwright.sheet_emit import _feature_line

        part = Cylinder(10, 40) - (Cylinder(10, 4) - Cylinder(8, 4))
        det = next(
            f for f in build_drawing(part, number="X").model().features if f.kind == "groove"
        )
        line = _feature_line(det)  # "sheet.groove(axis=..., width=..., diameter=..., at=...)"
        env = {"sheet": type("S", (), {"groove": staticmethod(declare_groove)})()}
        redeclared = eval(line, {"__builtins__": {}}, env)  # noqa: S307
        assert redeclared.axis == det.axis and redeclared.width == det.width
        assert redeclared.diameter == det.diameter
        assert redeclared.frame.origin == pytest.approx(det.frame.origin)

    def test_counterbore_flags_the_auto_section(self):
        part = Box(60, 60, 16) - Pos(0, 0, 0) * Cylinder(4, 30) - Pos(0, 0, 4) * Cylinder(8, 12)
        src = _script_for(part)
        assert "cbore=(" in src  # the counterbore rides the hole line
        assert "Section A–A auto-triggers" in src

    def test_blind_hole_gets_depth(self):
        part = Box(40, 40, 20) - Pos(0, 0, 6) * Cylinder(4, 16)  # blind ⌀8
        src = _script_for(part)
        assert ".depth(" in src

    def _bolt_circle(self, cbore=False):
        part = Cylinder(40, 8)
        for i in range(6):
            a = i * math.pi / 3
            c = Pos(25 * math.cos(a), 25 * math.sin(a), 0)
            part -= c * Cylinder(3, 20)
            if cbore:
                part -= c * Pos(0, 0, 4) * Cylinder(5, 8)
        return part

    def test_pattern_emits_the_pattern_verb(self):
        src = _script_for(self._bolt_circle())
        assert "sheet.pattern(hole(" in src and 'kind="bolt_circle"' in src

    def test_counterbored_pattern_flags_the_auto_section(self):
        # the section trigger lives on the pattern's MEMBER hole, not a top-level hole — a
        # counterbored bolt circle still auto-sections, so the comment must be present (was missed)
        src = _script_for(self._bolt_circle(cbore=True))
        assert "Section A–A auto-triggers" in src

    def test_blind_pattern_flags_the_auto_section(self):
        # #475: a BLIND bolt circle also auto-sections (the trigger is `not member.through`, not
        # just cbore/spotface). The trigger lives on the pattern's member hole, so the generated
        # comment must fire here too — the companion to the counterbored-pattern case.
        part = Cylinder(40, 20)  # 20 mm-thick disc
        for i in range(6):
            a = i * math.pi / 3
            # drill from the top face, blind (does not exit the bottom)
            part -= Pos(25 * math.cos(a), 25 * math.sin(a), 6) * Cylinder(3, 16)
        assert "Section A–A auto-triggers" in _script_for(part)

    def test_plain_pattern_does_not_flag_a_section(self):
        # regression guard: a through-hole bolt circle needs no section — no false-positive comment
        assert "Section A–A auto-triggers" not in _script_for(self._bolt_circle())

    def test_bolt_circle_spells_out_members(self):
        # #461 review r2: the detector records no start ANGLE, so recomputing members at angle 0
        # rotates the holes — the emitter must spell out the real member positions.
        line = next(
            ln
            for ln in _script_for(self._bolt_circle()).splitlines()
            if ln.startswith("sheet.pattern(")
        )
        assert "members=[" in line and line.count("(") >= 7  # member hole + 6 positions

    def test_pattern_member_keeps_its_counterbore(self):
        # #461 review r2: a counterbored bolt circle must keep the member's cbore on re-run
        line = next(
            ln
            for ln in _script_for(self._bolt_circle(cbore=True)).splitlines()
            if ln.startswith("sheet.pattern(")
        )
        assert "cbore=(" in line  # on the member hole(...) template

    def test_step_level_emits_fluent_verb(self):
        # A counterbored plate carries a step_level (horizontal face levels). #578: it
        # round-trips through the fluent sheet.step_level(...) verb (carrying levels +
        # shoulders + datum) — no StepLevelFeature/Frame import — so the generated script
        # preserves the front-right height ladder occupancy other dimensions negotiate against.
        part = Box(100, 70, 24) - Pos(0, 0, 0) * Cylinder(9, 40) - Pos(0, 0, 8) * Cylinder(15, 20)
        src = _script_for(part)
        assert "sheet.step_level(" in src
        assert "sheet.add(StepLevelFeature(" not in src
        assert "# step_level" not in src
        ast.parse(src)

    def test_step_level_emits_shoulder_positions(self):
        # #578: a single-level rebate carries a step-position shoulder; the emit must carry
        # a non-empty shoulders=(...) so the round-trip constrains the step POSITION, not
        # just its heights. The base slab top is Z=5; the raised step rises from x=0.
        part = Box(80, 40, 10) + Pos(-20, 0, 10) * Box(40, 40, 12)
        src = _script_for(part)
        line = next(ln for ln in src.splitlines() if "sheet.step_level(" in ln)
        assert "shoulders=(('x', 0),)" in line  # the riser position rides the emit
        assert "levels=(5,)" in line
        ast.parse(src)

    def test_step_level_round_trips_the_full_feature(self):
        # #578 review: EXECUTE the emitted fluent verb (not just substring-check it) and
        # assert the re-declared StepLevelFeature is identical to the detected one — base,
        # levels, shoulders, datum AND frame.origin (carried via at=), so the round trip is
        # lossless, not merely syntactically valid.
        from draftwright import Sheet

        part = Box(80, 40, 10) + Pos(-20, 0, 10) * Box(40, 40, 12)
        model = detect_part_model(part)
        src = _script_for(part)
        line = next(ln for ln in src.splitlines() if "sheet.step_level(" in ln).split("#")[0]
        sheet = Sheet(part, title="T", number="N")
        exec(compile(line, "<emit>", "exec"), {"sheet": sheet})
        got = sheet._features[-1]
        det = next(f for f in model.features if f.kind == "step_level")
        assert (got.base, got.levels, got.shoulders, got.datum) == (
            det.base,
            det.levels,
            det.shoulders,
            det.datum,
        )
        assert tuple(round(c, 3) for c in got.frame.origin) == tuple(
            round(c, 3) for c in det.frame.origin
        )

    @pytest.mark.skipif(not _PMI_AVAILABLE, reason="OCP GDT support not available")
    def test_ap242_script_keeps_side_hole_z_location_on_side_ladder(self, tmp_path):
        py = generate_sheet_script(str(AP242_CTC01), out=str(tmp_path / "ctc01"), pmi="annotate")
        ns = {}
        exec(compile(Path(py).read_text(encoding="utf-8"), py, "exec"), ns)
        dwg = ns["sheet"].build()
        # (#636) The Y-drilled hole's Z location joins the FRONT right ladder —
        # _locate_along_z's documented primary for a Y-axis hole (its circle shows in
        # the front view), co-solved with dim_step/dim_height on one running ladder.
        # The old side placement was an artifact: the carve-placed ladder crowded the
        # front strip, forcing the fallback; the corridor co-solve fits both.
        assert "dim_loc_front_z7500" in dwg.annotations()
        assert "dim_loc_side_z7500" not in dwg.annotations()
        assert "dim_step_0" in dwg.annotations()

    def test_needs_hole_import_only_when_a_pattern_is_present(self):
        # `hole` is only imported when a pattern line references it
        assert "from draftwright.model import hole" not in _script_for(Box(20, 20, 5))

    def test_slot_line_re_runs_without_the_length_invariant_error(self):
        # #461 review: declare.slot() checks hi - lo == length to 1e-6; the emitter must derive
        # length from the emitted lo/hi so the generated slot line doesn't raise on re-run.
        from draftwright import Sheet

        part = Box(60, 30, 12) - Pos(0, 0, 0) * Box(20.33, 8, 20)  # off-round → stresses rounding
        line = next(ln for ln in _script_for(part).splitlines() if ln.startswith("sheet.slot("))
        eval(line, {"sheet": Sheet(part)})  # declare.slot() must not raise

    def test_pocket_line_re_runs_without_the_length_invariant_error(self):
        # #148a: declare.pocket() checks hi - lo == length to 1e-6; the emitter must derive
        # length from the emitted lo/hi so the generated pocket line doesn't raise on re-run.
        from draftwright import Sheet

        part = Box(80, 60, 20) - Pos(0, 0, 6) * Box(30.33, 20, 8)  # off-round → stresses rounding
        line = next(ln for ln in _script_for(part).splitlines() if ln.startswith("sheet.pocket("))
        eval(line, {"sheet": Sheet(part)})  # declare.pocket() must not raise

    def test_linear_pattern_spells_out_members(self):
        # #461 review: the arrangement can't be recomputed faithfully (no reliable direction/angle),
        # so the emitter spells out the exact member positions for every pattern kind.
        from draftwright.model import Frame, HoleFeature, PatternFeature
        from draftwright.sheet_emit import _feature_line

        member = HoleFeature(Frame((0, 0, 0), "z"), 4.0, depth=None, through=True)
        pat = PatternFeature(
            frame=Frame((0, 0, 0), "z"),
            pattern="linear",
            count=3,
            member=member,
            members=((0, -10, 0), (0, 0, 0), (0, 10, 0)),
            pitch=10,
            direction=(0, 1, 0),
        )
        line = _feature_line(pat)
        assert "members=[(0, -10, 0), (0, 0, 0), (0, 10, 0)]" in line and "pitch=10" in line


class TestGenerate:
    def test_step_input_emits_a_self_contained_import_seam(self, tmp_path):
        step = tmp_path / "plate.step"
        export_step(_plate(), str(step))
        py = generate_sheet_script(str(step), out=str(tmp_path / "gen"))
        src = open(py, encoding="utf-8").read()
        assert "import_step(" in src and "part = ..." not in src

    def test_shape_input_leaves_a_part_seam(self, tmp_path):
        py = generate_sheet_script(_plate(), out=str(tmp_path / "gen"))
        src = open(py, encoding="utf-8").read()
        assert "part = ..." in src and "import_step(" not in src

    def test_generated_step_script_round_trips_to_a_drawing(self, tmp_path):
        # the whole point: the generated script RUNS and produces a real drawing
        step = tmp_path / "plate.step"
        export_step(_plate(), str(step))
        stem = tmp_path / "gen"
        py = generate_sheet_script(str(step), out=str(stem))
        exec(compile(open(py, encoding="utf-8").read(), py, "exec"), {})
        assert (tmp_path / "gen.pdf").exists()  # #702: Sheet.export defaults to PDF

    def test_title_from_basename_not_the_out_path(self, tmp_path):
        step = tmp_path / "widget.step"
        export_step(Box(20, 20, 5), str(step))
        py = generate_sheet_script(str(step), out=str(tmp_path / "gen"))
        src = open(py, encoding="utf-8").read()
        assert "title='GEN'" in src  # basename of out, upper — not the full path

    def test_step_path_is_absolute_for_cwd_independence(self, tmp_path, monkeypatch):
        export_step(_plate(), str(tmp_path / "plate.step"))
        monkeypatch.chdir(tmp_path)
        py = generate_sheet_script("plate.step", out="gen")  # relative input
        # the emitted import_step path must be absolute so the script runs from any CWD
        import_line = next(
            ln for ln in open(py, encoding="utf-8").read().splitlines() if "import_step(" in ln
        )
        path = ast.literal_eval(import_line.split("import_step(", 1)[1].rsplit(")", 1)[0])
        assert os.path.isabs(path)  # platform-agnostic (C:\… on Windows, /… on POSIX)


class TestObjectSpec:
    """`module:attr` / `file.py:attr` → a live build123d object (#469, mode 3b from a
    separate codebase). The seam re-binds `part` to the real source, not a frozen STEP."""

    def _mod(self, tmp_path, name="srcmod"):
        p = tmp_path / f"{name}.py"
        p.write_text(_SOURCE_MODULE, encoding="utf-8")
        return p

    def test_file_attr_resolves_object_with_self_contained_seam(self, tmp_path):
        p = self._mod(tmp_path)
        obj, seam = resolve_object_spec(f"{p}:bracket")
        assert isinstance(obj, Shape)
        # the file seam bakes the absolute path as a repr'd literal (so it's valid Python and
        # runs from any CWD) — compare against repr, not the bare string, so Windows backslash
        # escaping (C:\\Users\\… in the seam vs C:\Users\… in str(p)) doesn't false-fail.
        assert "spec_from_file_location(" in seam and repr(str(p.resolve())) in seam
        assert "part = _mod.bracket" in seam

    def test_object_input_emits_object_reference_tip(self, tmp_path):
        # #771: a live-source (Shape) input gets a discoverability tip pointing at the
        # object-reference idiom; a STEP-sourced script does NOT — the numbers ARE the honest
        # source and there are no objects to reference (keeps STEP scripts byte-stable).
        py = generate_sheet_script(Box(60, 40, 12), out=str(tmp_path / "shape"))
        assert "Object-reference tip" in Path(py).read_text()
        step = tmp_path / "p.step"
        export_step(Box(60, 40, 12), str(step))
        py2 = generate_sheet_script(str(step), out=str(tmp_path / "step"))
        assert "Object-reference tip" not in Path(py2).read_text()

    def test_zero_arg_factory_is_called(self, tmp_path):
        p = self._mod(tmp_path)
        obj, seam = resolve_object_spec(f"{p}:make_bracket")
        assert isinstance(obj, Shape)
        assert seam.rstrip().endswith("()")  # part = _mod.make_bracket()

    def test_dotted_module_seam_bakes_the_cwd(self, tmp_path, monkeypatch):
        # Python puts only the *script's* dir on sys.path, not the cwd — so the seam must
        # bake the resolve-time cwd or `from mod import …` fails when the script is re-run.
        self._mod(tmp_path, "dottedmod")
        monkeypatch.chdir(tmp_path)
        obj, seam = resolve_object_spec("dottedmod:bracket")
        assert isinstance(obj, Shape)
        assert "from dottedmod import bracket as _obj" in seam

    def test_file_spec_helper_sibling_import_wins_and_seam_is_cwd_independent(
        self, tmp_path, monkeypatch
    ):
        # #488 + #491 review: a subdir helper importing a sibling must (1) resolve to the sibling
        # next to it — NOT a same-named module in cwd (match `python file.py`); and (2) the baked
        # seam must re-build the SAME object from any CWD (bake cwd as a resolve-time literal, not
        # a runtime getcwd, and preserve insert order so the initial build == the re-run).
        sub = tmp_path / "sub"
        sub.mkdir()
        (tmp_path / "sib491.py").write_text(  # a COLLIDING module in cwd
            "from build123d import Box\ndef base():\n    return Box(9, 9, 9)\n", encoding="utf-8"
        )
        (sub / "sib491.py").write_text(  # the true sibling next to the helper
            "from build123d import Box\ndef base():\n    return Box(3, 3, 3)\n", encoding="utf-8"
        )
        (sub / "helper491.py").write_text(
            "from sib491 import base\ndef make():\n    return base()\n", encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)
        obj, seam = resolve_object_spec("sub/helper491.py:make")
        assert round(obj.bounding_box().size.X) == 3  # helper's own dir wins the clash

        # the seam bakes cwd as an absolute literal (no runtime getcwd), so re-run is cwd-stable
        assert "getcwd" not in seam
        assert repr(str(tmp_path)) in seam

        # exec the seam from a DIFFERENT cwd with the modules purged -> must build the SAME object
        import sys as _sys

        for _m in ("sib491", "helper491"):
            _sys.modules.pop(_m, None)
        for _p in (str(tmp_path), str(sub)):
            while _p in _sys.path:
                _sys.path.remove(_p)
        monkeypatch.chdir(tmp_path.parent)
        ns: dict = {}
        exec(seam, ns)  # noqa: S102 — exercising the generated re-run seam
        assert round(ns["_mod"].make().bounding_box().size.X) == 3  # build == re-run

    def test_file_spec_helper_dir_wins_even_when_preloaded_on_syspath(self, tmp_path, monkeypatch):
        # #491 review: a `not in sys.path` guard can't reorder an ALREADY-present file_dir — a
        # driver run as `python tools/driver.py` preloads the helper dir, so cwd could win a clash
        # (opposite of script semantics) and the build diverge from the re-run. Force-front fixes it.
        sub = tmp_path / "sub"
        sub.mkdir()
        (tmp_path / "sibZ.py").write_text(
            "from build123d import Box\ndef base():\n    return Box(9, 9, 9)\n", encoding="utf-8"
        )
        (sub / "sibZ.py").write_text(
            "from build123d import Box\ndef base():\n    return Box(3, 3, 3)\n", encoding="utf-8"
        )
        (sub / "helperZ.py").write_text(
            "from sibZ import base\ndef make():\n    return base()\n", encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)
        import sys as _sys

        saved = list(_sys.path)
        try:
            _sys.path.insert(0, str(sub))  # file_dir ALREADY present (driver-on-path)
            while str(tmp_path) in _sys.path:  # cwd absent
                _sys.path.remove(str(tmp_path))
            obj, _seam = resolve_object_spec("sub/helperZ.py:make")
            assert round(obj.bounding_box().size.X) == 3  # helper dir wins despite being preloaded
        finally:
            _sys.path[:] = saved
            for _m in ("sibZ", "helperZ"):
                _sys.modules.pop(_m, None)

    def test_missing_attr_raises(self, tmp_path):
        p = self._mod(tmp_path)
        with pytest.raises(ValueError, match="not found"):
            resolve_object_spec(f"{p}:nope")

    def test_non_shape_raises(self, tmp_path):
        p = self._mod(tmp_path)
        with pytest.raises(ValueError, match="not a build123d Shape"):
            resolve_object_spec(f"{p}:NOT_A_SHAPE")

    def test_none_bound_attr_reports_wrong_type_not_missing(self, tmp_path):
        # `bracket = None` exists but is None — must report the honest "not a Shape", not "not
        # found" (a None sentinel on getattr would conflate the two, #469 review).
        p = self._mod(tmp_path)
        with pytest.raises(ValueError, match="not a build123d Shape"):
            resolve_object_spec(f"{p}:NONE_BOUND")

    def test_unimportable_module_raises_a_clean_error(self):
        # a missing/malformed module surfaces the friendly ValueError, not a raw ImportError
        with pytest.raises(ValueError, match="cannot import module"):
            resolve_object_spec("no_such_module_zzz:bracket")

    def test_self_referential_file_module_loads(self, tmp_path):
        # a target that resolves its own forward-ref annotations via typing.get_type_hints needs
        # sys.modules registration BEFORE exec — the .py branch must register it (#469 review).
        src = (
            "from dataclasses import dataclass\n"
            "from typing import Optional, get_type_hints\n"
            "from build123d import Box\n"
            "@dataclass\n"
            "class Node:\n    nxt: 'Optional[Node]' = None\n"
            "get_type_hints(Node)  # NameError unless this module is in sys.modules\n"
            "part = Box(10, 10, 10)\n"
        )
        p = tmp_path / "selfref.py"
        p.write_text(src, encoding="utf-8")
        obj, _seam = resolve_object_spec(f"{p}:part")
        assert isinstance(obj, Shape)

    def test_callable_needing_args_raises(self, tmp_path):
        p = self._mod(tmp_path)
        with pytest.raises(ValueError, match="needs arguments"):
            resolve_object_spec(f"{p}:needs_args")

    def test_malformed_spec_raises(self):
        with pytest.raises(ValueError, match="module:attr"):
            resolve_object_spec("no_colon_here")


class TestLooksLikeSpec:
    """The CLI's STEP-path-vs-object-spec discriminator (`_looks_like_object_spec`, #469)."""

    def test_dotted_and_file_specs_are_specs(self):
        from draftwright.cli import _looks_like_object_spec

        assert _looks_like_object_spec("mypkg.mymod:bracket")
        assert _looks_like_object_spec("model.py:make_part")

    def test_step_paths_are_not_specs(self):
        from draftwright.cli import _looks_like_object_spec

        assert not _looks_like_object_spec("/tmp/part.step")
        assert not _looks_like_object_spec("part.stp")
        assert not _looks_like_object_spec("dir/sub/part.step")  # a colonless path

    def test_windows_step_path_is_not_a_spec(self):
        from draftwright.cli import _looks_like_object_spec

        assert not _looks_like_object_spec(r"C:\models\part.step")

    def test_windows_absolute_file_spec_is_a_spec(self):
        # the drive-path guard was removed (#469 review): C:\…\model.py:bracket is a real file
        # spec and must route to resolve_object_spec, not the STEP path.
        from draftwright.cli import _looks_like_object_spec

        assert _looks_like_object_spec(r"C:\proj\model.py:bracket")

    def test_existing_file_is_never_a_spec(self, tmp_path):
        # a real STEP file that happens to parse spec-like still isn't a spec
        from draftwright.cli import _looks_like_object_spec

        f = tmp_path / "weird.step"
        f.write_text("x")
        assert not _looks_like_object_spec(str(f))


class TestCli:
    def test_style_sheet_routes_to_the_declarative_emitter(self, tmp_path):
        from typer.testing import CliRunner

        from draftwright.cli import app

        step = tmp_path / "plate.step"
        export_step(_plate(), str(step))
        r = CliRunner().invoke(
            app, [str(step), "--script", "--style", "sheet", "--out", str(tmp_path / "g")]
        )
        assert r.exit_code == 0, r.output
        assert "sheet.hole(" in open(tmp_path / "g.py", encoding="utf-8").read()

    def test_script_defaults_to_sheet_style(self, tmp_path):
        # --script with NO --style now emits the declarative Sheet DSL (sheet is the default)
        from typer.testing import CliRunner

        from draftwright.cli import app

        step = tmp_path / "plate.step"
        export_step(_plate(), str(step))
        r = CliRunner().invoke(app, [str(step), "--script", "--out", str(tmp_path / "g")])
        assert r.exit_code == 0, r.output
        src = open(tmp_path / "g.py", encoding="utf-8").read()
        assert "from draftwright import Sheet" in src and "sheet.hole(" in src

    def test_imperative_style_still_available(self, tmp_path):
        # the imperative reconstruction is still reachable via an explicit --style imperative
        from typer.testing import CliRunner

        from draftwright.cli import app

        step = tmp_path / "plate.step"
        export_step(_plate(), str(step))
        r = CliRunner().invoke(
            app, [str(step), "--script", "--style", "imperative", "--out", str(tmp_path / "g")]
        )
        assert r.exit_code == 0, r.output
        assert (
            "from draftwright import Sheet" not in open(tmp_path / "g.py", encoding="utf-8").read()
        )

    def test_imperative_with_object_spec_is_rejected(self, tmp_path, monkeypatch):
        # imperative reads a STEP file, not a module:attr object → a clear error, not import_step noise
        from typer.testing import CliRunner

        from draftwright.cli import app

        (tmp_path / "climod.py").write_text(_SOURCE_MODULE, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        r = CliRunner().invoke(app, ["climod:bracket", "--script", "--style", "imperative"])
        assert r.exit_code != 0
        # rich wraps the error panel at the (CI-narrow) console width, so the phrase can straddle
        # a bordered line — normalise ANSI + box borders + whitespace before the substring check
        assert "--style sheet" in _norm(r.output)

    def test_sheet_style_embeds_title_block_and_layout_flags(self, tmp_path):
        # #474: the Sheet DSL now carries --drawn-by/--tolerance/--scale/--page, so the sheet path
        # forwards them into the generated Sheet(...) constructor (no more inert-flag warning).
        from typer.testing import CliRunner

        from draftwright.cli import app

        step = tmp_path / "plate.step"
        export_step(_plate(), str(step))
        r = CliRunner().invoke(
            app,
            [
                str(step),
                "--script",
                "--drawn-by",
                "Paul",
                "--tolerance",
                "ISO 2768-f",
                "--scale",
                "2",
                "--page",
                "A3",
                "--out",
                str(tmp_path / "g"),
            ],
        )
        assert r.exit_code == 0, r.output
        assert "warning:" not in r.output  # the flags are honoured, not dropped
        src = (tmp_path / "g.py").read_text(encoding="utf-8")
        ctor = next(line for line in src.splitlines() if "Sheet(part" in line)
        assert "drawn_by='Paul'" in ctor
        assert "tolerance='ISO 2768-f'" in ctor
        assert "scale=2.0" in ctor
        assert "page='A3'" in ctor

    def test_sheet_style_omits_default_flags(self, tmp_path):
        # A plain invocation keeps a clean one-line constructor — unset aspects stay off the script.
        from typer.testing import CliRunner

        from draftwright.cli import app

        step = tmp_path / "plate.step"
        export_step(_plate(), str(step))
        r = CliRunner().invoke(app, [str(step), "--script", "--out", str(tmp_path / "g")])
        assert r.exit_code == 0, r.output
        assert "warning:" not in r.output
        ctor = next(
            line
            for line in (tmp_path / "g.py").read_text(encoding="utf-8").splitlines()
            if "Sheet(part" in line
        )
        # only title + number; no title-block / layout aspect kwargs when unset
        assert "number='DWG-001'" in ctor
        for kw in ("drawn_by=", "tolerance=", "scale=", "page="):
            assert kw not in ctor

    def test_format_is_forwarded_into_the_sheet_script(self, tmp_path):
        # #709: `--script -f svg` used to silently emit a PDF-producing script.
        from typer.testing import CliRunner

        from draftwright.cli import app

        step = tmp_path / "plate.step"
        export_step(_plate(), str(step))
        r = CliRunner().invoke(
            app, [str(step), "--script", "-f", "svg", "--out", str(tmp_path / "g")]
        )
        assert r.exit_code == 0, r.output
        src = (tmp_path / "g.py").read_text(encoding="utf-8")
        assert f"sheet.export({str(tmp_path / 'g')!r}, formats=('svg',))" in src

    def test_default_format_keeps_the_bare_sheet_export_call(self, tmp_path):
        # No --format → the emitted call stays bare (Sheet.export defaults to PDF).
        from typer.testing import CliRunner

        from draftwright.cli import app

        step = tmp_path / "plate.step"
        export_step(_plate(), str(step))
        r = CliRunner().invoke(app, [str(step), "--script", "--out", str(tmp_path / "g")])
        assert r.exit_code == 0, r.output
        src = (tmp_path / "g.py").read_text(encoding="utf-8")
        assert f"sheet.export({str(tmp_path / 'g')!r})" in src
        assert "formats=" not in src

    def test_format_is_forwarded_into_the_imperative_script(self, tmp_path):
        # #709: the imperative flavour honours --format too, via the modern dict export.
        from typer.testing import CliRunner

        from draftwright.cli import app

        step = tmp_path / "plate.step"
        export_step(_plate(), str(step))
        r = CliRunner().invoke(
            app,
            [
                str(step),
                "--script",
                "--style",
                "imperative",
                "-f",
                "svg,dxf",
                "--out",
                str(tmp_path / "g"),
            ],
        )
        assert r.exit_code == 0, r.output
        src = (tmp_path / "g.py").read_text(encoding="utf-8")
        assert "_formats = ('svg', 'dxf')" in src
        assert "paths = dwg.export(_stem, formats=_formats)" in src

    def test_bad_style_is_rejected(self, tmp_path):
        from typer.testing import CliRunner

        from draftwright.cli import app

        step = tmp_path / "plate.step"
        export_step(_plate(), str(step))
        r = CliRunner().invoke(app, [str(step), "--script", "--style", "bogus"])
        assert r.exit_code != 0

    def test_module_spec_routes_to_the_live_object(self, tmp_path, monkeypatch):
        # `draftwright climod:bracket --script --style sheet` → detect off the imported object
        from typer.testing import CliRunner

        from draftwright.cli import app

        (tmp_path / "climod.py").write_text(_SOURCE_MODULE, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        r = CliRunner().invoke(
            app, ["climod:bracket", "--script", "--style", "sheet", "--out", "g"]
        )
        assert r.exit_code == 0, r.output
        src = open(tmp_path / "g.py", encoding="utf-8").read()
        assert "from climod import bracket as _obj" in src  # the live-source seam
        assert "sheet.hole(" in src  # features detected off the object, not a STEP

    def test_generated_module_spec_script_round_trips(self, tmp_path, monkeypatch):
        # the whole point: the emitted script RUNS (through the baked-cwd seam) and draws
        from typer.testing import CliRunner

        from draftwright.cli import app

        (tmp_path / "rtmod.py").write_text(_SOURCE_MODULE, encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        r = CliRunner().invoke(
            app, ["rtmod:bracket", "--script", "--style", "sheet", "--out", "g"]
        )
        assert r.exit_code == 0, r.output
        py = tmp_path / "g.py"
        exec(compile(open(py, encoding="utf-8").read(), str(py), "exec"), {})
        assert (tmp_path / "g.pdf").exists()  # #702: Sheet.export defaults to PDF


def _annotation_signature(dwg):
    """Value-aware annotation signature for #472 round-trip parity.

    Names and types catch dropped/replaced annotations; dimension specs catch the old
    OD-as-Leader gap plus side/distance drift; leader coverage catches callout text regressions
    where the drafting helper does not expose a label string; boxes catch non-dimension
    furniture moving or vanishing. This intentionally stays below SVG byte identity, which is
    too brittle for a semantic script invariant.
    """

    def _r(v):
        return round(float(v), 3)

    def _box(obj):
        try:
            b = obj.bounding_box()
        except Exception:
            return None
        return (_r(b.min.X), _r(b.min.Y), _r(b.max.X), _r(b.max.Y))

    rows = []
    for name, obj in dwg.iter_annotations():
        spec = getattr(obj, "_dw_spec", None)
        if spec is not None:
            detail = (
                tuple(_r(x) for x in spec.p1[:2]),
                tuple(_r(x) for x in spec.p2[:2]),
                spec.side,
                _r(spec.distance),
                getattr(obj, "label", ""),
            )
        else:
            detail = (
                getattr(obj, "label", ""),
                tuple(_r(x) for x in getattr(obj, "covers_diameters", ())),
                getattr(obj, "covers_count", None),
                _box(obj),
            )
        rows.append((name, type(obj).__name__, detail))
    return sorted(rows)


def _drawing_from_generated_script(step_path, tmp_path, monkeypatch):
    """Run the ACTUAL generated sheet script (STEP-seam form, self-runnable) and capture the
    Drawing it builds, by intercepting Sheet.export — the true end-to-end sheet-script path."""
    from draftwright import Sheet

    captured = {}
    monkeypatch.setattr(
        Sheet, "export", lambda self, stem=None: captured.setdefault("dwg", self.build())
    )
    py = generate_sheet_script(str(step_path), out=str(tmp_path / "gen"), title="PART")
    exec(compile(open(py, encoding="utf-8").read(), py, "exec"), {})
    return captured["dwg"]


class TestRoundTripParity:
    """#472: the generated sheet script must reproduce the direct build's annotation set — the
    invariant that makes the default `--script` (sheet) trustworthy. Turned/rotational parts were
    the known gap (dropped centrelines + OD-as-leader) because the declared model carried no
    RotationalFeature; the builder now synthesises it from the analysis."""

    def _parity(self, part, tmp_path, monkeypatch):
        step = tmp_path / "part.step"
        export_step(part, str(step))
        direct = build_drawing(step_file=str(step), title="PART")
        scripted = _drawing_from_generated_script(step, tmp_path, monkeypatch)
        assert _annotation_signature(scripted) == _annotation_signature(direct)

    def test_prismatic_plate_parity(self, tmp_path, monkeypatch):
        self._parity(_plate(), tmp_path, monkeypatch)

    def test_slot_parity(self, tmp_path, monkeypatch):
        self._parity(Box(50, 30, 20) - Box(20, 8, 30), tmp_path, monkeypatch)

    def test_pocket_parity(self, tmp_path, monkeypatch):
        # A blind pocket (#148a): the generated script's PocketFeature must reproduce the
        # direct build's W × L × D DEEP leader callout.
        self._parity(Box(80, 60, 20) - Pos(0, 0, 6) * Box(30, 20, 8), tmp_path, monkeypatch)

    def test_pattern_parity(self, tmp_path, monkeypatch):
        part = (
            Box(100, 80, 20)
            - Pos(35, 25, 0) * Cylinder(4, 30)
            - Pos(-35, 25, 0) * Cylinder(4, 30)
            - Pos(35, -25, 0) * Cylinder(4, 30)
            - Pos(-35, -25, 0) * Cylinder(4, 30)
        )
        self._parity(part, tmp_path, monkeypatch)

    def test_counterbore_section_parity(self, tmp_path, monkeypatch):
        part = Box(80, 60, 20) - Pos(0, 0, 0) * Cylinder(8, 40) - Pos(0, 0, 8) * Cylinder(14, 20)
        self._parity(part, tmp_path, monkeypatch)

    def test_title_block_and_layout_aspects_round_trip(self, tmp_path, monkeypatch):
        # #474: a generated sheet script carrying drawn_by/tolerance/scale/page must reproduce the
        # same title-block + scale + page as a direct build with the same flags. Compare the
        # Analysis the drawing was built from (title-block text is path-vectorised, not greppable).
        from draftwright import Sheet

        flags = dict(drawn_by="PF", tolerance="ISO 2768-f", scale=2.0, page="A3")
        step = tmp_path / "part.step"
        export_step(_plate(), str(step))

        direct = build_drawing(step_file=str(step), title="PART", **flags)

        captured = {}
        monkeypatch.setattr(
            Sheet, "export", lambda self, stem=None: captured.setdefault("dwg", self.build())
        )
        py = generate_sheet_script(str(step), out=str(tmp_path / "gen"), title="PART", **flags)
        exec(compile(open(py, encoding="utf-8").read(), py, "exec"), {})
        scripted = captured["dwg"]

        def aspects(dwg):
            a = dwg._analysis
            return (a.title, a.tolerance, a.drawn_by, round(a.SCALE, 4), a.PAGE_W, a.PAGE_H)

        assert aspects(scripted) == aspects(direct)

    def test_standing_title_block_fields_round_trip(self, tmp_path, monkeypatch):
        # #766: material/date/revision/company thread through build_drawing and the emitted
        # Sheet script reproduces them (like #474's drawn_by/tolerance). Defaults preserve
        # the prior output: revision "A", the rest blank.
        from draftwright import Sheet

        flags = dict(material="STEEL", date="2026-07-20", revision="B", company="ACME")
        step = tmp_path / "part.step"
        export_step(_plate(), str(step))
        direct = build_drawing(step_file=str(step), title="PART", **flags)
        assert (direct._analysis.material, direct._analysis.revision) == ("STEEL", "B")
        # defaults unchanged on a plain build (revision "A", the rest blank)
        plain = build_drawing(step_file=str(step), title="PART")
        assert (plain._analysis.material, plain._analysis.revision, plain._analysis.company) == (
            "",
            "A",
            "",
        )

        captured = {}
        monkeypatch.setattr(
            Sheet, "export", lambda self, stem=None: captured.setdefault("dwg", self.build())
        )
        py = generate_sheet_script(str(step), out=str(tmp_path / "gen"), title="PART", **flags)
        # the emitted ctor carries the non-default fields
        ctor = next(
            line
            for line in open(py, encoding="utf-8").read().splitlines()
            if line.startswith("sheet = Sheet(")
        )
        assert "material='STEEL'" in ctor and "revision='B'" in ctor and "company='ACME'" in ctor
        exec(compile(open(py, encoding="utf-8").read(), py, "exec"), {})
        scripted = captured["dwg"]

        def fields(dwg):
            a = dwg._analysis
            return (a.material, a.date, a.revision, a.company)

        assert fields(scripted) == fields(direct) == ("STEEL", "2026-07-20", "B", "ACME")

    def test_grm03_vendored_fixture_full_parity(self, tmp_path, monkeypatch):
        # #707: GRM-03 (the Maquetto thumbwheel drive screw) is the real STEP that
        # surfaced "emitted Sheet != direct CLI drawing" against 0.3.3. #709 (format
        # forwarding) + #661 (finalize detail drain) closed the divergence; this
        # vendored-fixture regression LOCKS the full invariant #707 names — same
        # views, annotation inventory, page, scale AND lint — not just the annotation
        # signature the synthetic parity cases above check. A turned stepped part
        # (1 hole + 4 steps + 1 boss), so it also guards the rotational-furniture
        # path end to end through the emitted Sheet.
        fixture = Path(__file__).parent / "fixtures" / "grm03_thumbwheel_drive_screw.step"
        direct = build_drawing(step_file=str(fixture), title="PART")
        scripted = _drawing_from_generated_script(fixture, tmp_path, monkeypatch)

        assert _annotation_signature(scripted) == _annotation_signature(direct)
        assert sorted(scripted.views) == sorted(direct.views)
        da, sa = direct._analysis, scripted._analysis
        assert round(sa.SCALE, 4) == round(da.SCALE, 4)
        assert (sa.PAGE_W, sa.PAGE_H) == (da.PAGE_W, da.PAGE_H)
        assert scripted.lint_summary()["by_code"] == direct.lint_summary()["by_code"]

    def test_turned_x_shaft_parity(self, tmp_path, monkeypatch):
        # a horizontal turned shaft (X axis) — genuinely rotational: is_rotational + od_axis='x',
        # driving the non-Z branch of build_rotational_feature (bores=(), Frame axis='x'). A
        # two-diameter cross body would trip the #222 fallback and classify prismatic instead,
        # exercising no rotational furniture — so keep this a single-diameter cylinder.
        from build123d import Rotation

        self._parity(Rotation(0, 90, 0) * Cylinder(15, 80), tmp_path, monkeypatch)

    def test_rotational_bored_shaft_parity(self, tmp_path, monkeypatch):
        # the #472 fixture: a Z-axis stepped cylinder with a concentric bore — the case that
        # dropped both centrelines and the OD dimension before the RotationalFeature synthesis
        shaft = Pos(0, 0, 20) * Cylinder(15, 40) + Pos(0, 0, 55) * Cylinder(8, 30)
        self._parity(shaft - Pos(0, 0, 0) * Cylinder(2.5, 200), tmp_path, monkeypatch)

    def test_declared_rotational_wins_no_double_add(self):
        # The synthesis gate (builder.py) must not fire when the caller already declared a
        # rotational feature: an explicit choice wins, and the furniture is never double-added.
        # Use a genuinely rotational part (single-diam horizontal cylinder ⇒ synthesis WOULD
        # otherwise add od=30) but declare od=99 — assert exactly one rotational feature survives
        # and it is the declared one.
        from build123d import Rotation

        from draftwright.model.ir import Frame, PartModel, RotationalFeature

        shaft = Rotation(0, 90, 0) * Cylinder(15, 80)
        declared = RotationalFeature(frame=Frame((0.0, 0.0, 0.0), "x"), od=99.0)
        m = PartModel(bbox=shaft.bounding_box(), orientation=None, features=[declared], datums=[])
        rot = [f for f in build_drawing(shaft, model=m).model().features if f.kind == "rotational"]
        assert len(rot) == 1 and rot[0].od == 99.0
