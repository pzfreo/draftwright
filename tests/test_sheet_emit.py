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
from build123d import chamfer as bd_chamfer
from build123d import fillet as bd_fillet

from draftwright.builder import build_drawing, detect_part_model
from draftwright.model.declare import hole as _declare_hole
from draftwright.pmi import _PMI_AVAILABLE
from draftwright.sheet_emit import (
    _hole_line,
    _member_hole_str,
    emit_sheet_script,
    generate_sheet_script,
    resolve_object_spec,
    unmirrored_dimensions,
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


def _feature_line_for(src: str, call: str) -> str:
    """The emitted DECLARATION line containing *call*, e.g. ``"sheet.hole("``.

    Matching by prefix stopped working when #922 made every feature line bind a name
    (`hole1 = sheet.hole(...)`), and matching by bare substring is too loose — the module
    docstring mentions `sheet.hole(my_bore)`. A declaration is an assignment whose value is
    that call, so that is what this looks for."""
    for line in src.splitlines():
        stripped = line.split("#")[0].rstrip()
        if call in stripped and " = " in stripped and not stripped.lstrip().startswith("#"):
            return line
    raise AssertionError(f"no emitted declaration line contains {call!r}")


def _call_expr(line: str) -> str:
    """The call on the right of an emitted declaration, without its binding or comment —
    for the tests that `eval()` a line, which cannot take an assignment."""
    return line.split("#")[0].split(" = ", 1)[-1].strip()


class TestEmit:
    @pytest.mark.parametrize(
        "part", [Box(40, 20, 10), _plate(), Box(80, 60, 40)], ids=["bare", "plate", "block"]
    )
    def test_every_emitted_script_states_its_dimension_source(self, part):
        """#874's emitter gate. A generated script must SAY where its dimensions come from,
        not rely on a default — that is what makes omission mean something. Round trips cover
        this only implicitly (the build would raise), so assert the line directly: a part with
        no features must still emit one.

        EITHER verb satisfies it. This asserted `auto_dimensions()` specifically until #938
        made the script mirror the planner's set as `dimension(...)` lines, which is the
        authored source — a different answer to the same question, not a missing one."""
        src = _script_for(part)
        assert "sheet.auto_dimensions()" in src or "sheet.authored_dimensions()" in src

    def test_an_authored_set_emits_its_declarations_through_the_facade(self):
        """The emitter used to write `sheet.auto_dimensions()` for ANY model, so a script
        generated from an authored model restored every dimension that model had omitted —
        "width only" became the full automatic set (#921 review round 5). A generated script
        that draws something other than its source model is the #707 class of divergence.

        It then REFUSED an authored model, because the only way to name a feature in the
        script was by position and the documented workflow is to comment a feature line out
        and re-run — which shifts the indices and retargets the declarations onto their
        neighbours. #931/#932 gave every feature a handle and a bound name, so #922 writes
        the declarations instead. This test kept its property and changed its expectation:
        the authored set must never become the planner's, whether by conversion or by
        refusal-then-workaround.

        Round-tripped end to end in `TestAuthoredSetRoundTrips`; here it is the `Sheet`
        façade path specifically, which reaches the emitter through `sheet.model()`.
        """
        from draftwright import Sheet
        from draftwright.sheet_emit import emit_sheet_script

        sheet = Sheet(Box(90, 60, 20), title="T", number="N")
        sheet.dimension(sheet.envelope(), "width")
        src = emit_sheet_script(
            sheet.model(), "part = Box(90, 60, 20)", "s", title="T", number="N"
        )
        assert "sheet.auto_dimensions()" not in src
        assert 'sheet.dimension(envelope1, "width")' in src

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
        # `measured_dimension` since #873: a generated script must not emit the
        # transitional overload, or every regenerated AP242 script arrives deprecated.
        assert "sheet.measured_dimension(" in src
        # The transitional MEASURED overload (`dimension(kind=…, value=…)`), not the
        # referential verb: a regenerated AP242 script must not arrive pre-deprecated (#873).
        # Bare `sheet.dimension(` stopped meaning that when #938 made every script mirror the
        # planner's set with referential `dimension(feature, role)` lines.
        assert "sheet.dimension(kind=" not in src
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

    def test_measured_dimension_declares_renderable_authored_dimension(self, tmp_path):
        from draftwright import Sheet

        sheet = Sheet(Box(40, 20, 10), title="P", out=str(tmp_path / "dim")).auto_dimensions()
        sheet.measured_dimension(
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

    def test_measured_dimension_rejects_unrenderable_kind(self):
        from draftwright import Sheet

        sheet = Sheet(Box(40, 20, 10), title="P").auto_dimensions()
        with pytest.raises(ValueError, match="kind must be one of"):
            sheet.measured_dimension(
                kind="liner",
                value=40,
                label="40",
                dominant_axis="X",
                ref_pts=[(-20, 0, 0), (20, 0, 0)],
                ref_bbox=(-20, -10, -5, 20, 10, 5),
            )

    def test_measured_dimension_rejects_unrenderable_axis(self):
        from draftwright import Sheet

        sheet = Sheet(Box(40, 20, 10), title="P").auto_dimensions()
        with pytest.raises(ValueError, match="dominant_axis must be X, Y, or Z"):
            sheet.measured_dimension(
                kind="linear",
                value=40,
                label="40",
                dominant_axis="XX",
                ref_pts=[(-20, 0, 0), (20, 0, 0)],
                ref_bbox=(-20, -10, -5, 20, 10, 5),
            )

    def test_refpts_only_linear_dim_renders_on_each_axis(self):
        # #562: a linear Sheet.measured_dimension() with two valid ref_pts and NO ref_bbox must
        # render (the witness is derived from the ref points) — for X, Y, and Z. Z needs
        # the front left/right strip reserved for authored height dims (the sizing fix).
        from draftwright import Sheet

        cases = {
            "X": [(-40, -30, 0), (-30, -30, 0)],
            "Y": [(-40, -30, 0), (-40, -20, 0)],
            "Z": [(-38, -30, 5), (-38, -30, 15)],
        }
        for axis, ref_pts in cases.items():
            sheet = Sheet(Box(80, 60, 40), title="P").auto_dimensions()
            sheet.measured_dimension(
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

        sheet = Sheet(Box(80, 60, 40), title="P").auto_dimensions()
        sheet.measured_dimension(
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
        line = _feature_line_for(_script_for(_plate()), "sheet.hole(diameter=8")
        # `mode="eval"` cannot parse an assignment, and the line binds a name since #922.
        call = ast.parse(_call_expr(line), mode="eval").body  # the sheet.hole(...) Call node
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

    def _four_fillets(self):
        from build123d import Axis
        from build123d import fillet as bd_fillet

        return bd_fillet(Box(80, 50, 8).edges().filter_by(Axis.Z), 3)  # 4 equal R3 corners

    def test_feature_lines_carry_a_describing_comment(self):
        # Each verb line ends in a plain-language comment so the body is readable without
        # decoding the coordinates — a hole reads "⌀8 THRU ×N", a fillet "R3".
        src = _script_for(_plate())
        hole = _feature_line_for(src, "sheet.hole(")
        assert "   # ⌀8 THRU" in hole

    def test_body_is_grouped_under_section_sub_headers(self):
        # Consecutive same-section features sit under a "#   Edges …" sub-header, so a long
        # script is navigable. (Holes/Edges are distinct sections.)
        src = _script_for(self._four_fillets())
        assert "#   Edges" in src
        assert "#   Holes" not in src  # this part has no holes

    def test_repeat_run_shows_a_tally_in_its_header(self):
        # A run of near-identical features tallies in the header (4 equal R3 fillets → "4× R3"),
        # signalling the repetition below is expected, not noise.
        edges = next(
            ln
            for ln in _script_for(self._four_fillets()).splitlines()
            if ln.startswith("#   Edges")
        )
        assert "4× R3" in edges

    def test_header_lists_a_feature_manifest(self):
        # The Features banner is a one-line census so the editor has a map before scrolling.
        banner = next(
            ln for ln in _script_for(_plate()).splitlines() if ln.startswith("# ── Features (")
        )
        assert "hole" in banner and "envelope" in banner

    def test_section_headers_never_name_step_level_internally(self):
        # The step_level section header is human-worded ("Prismatic steps"); the internal kind
        # string must not leak (guards the same contract as the existing "# step_level" check).
        part = Box(100, 70, 24) - Pos(0, 0, 0) * Cylinder(9, 40) - Pos(0, 0, 8) * Cylinder(15, 20)
        src = _script_for(part)
        assert "Prismatic steps" in src
        assert "# step_level" not in src

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

    def test_a_blind_hole_with_no_measured_depth_stays_blind(self):
        """Blindness is a FACT on the feature, so it must round-trip whether or not a depth
        was measured.

        Both emitters guarded `through=False` on `depth is not None`, so a
        `HoleFeature(through=False, depth=None)` emitted neither — and `declare.hole`
        defaults `through=True`, so the regenerated script declared a THROUGH hole and the
        blindness vanished. This is #868's rule (a fact is not inferred from a dimension's
        presence) applied to the emit side; after #868 fixed the render side, the two
        disagreed about the same hole (#878).

        The assertion is on the REBUILT feature, not on the emitted text: the point is that
        the round trip preserves the fact, and an emitted-substring check would pass on any
        spelling that happened to contain the token.
        """
        from draftwright import Sheet
        from draftwright.model.ir import Frame, HoleFeature

        blind = HoleFeature(Frame((0, 0, 0), "z"), 6.0, depth=None, through=False)
        part = Box(40, 40, 20)
        for line, prefix in (
            (_hole_line(blind), "sheet.hole"),
            # The pattern member goes through a second, independent emitter with the same
            # bug — one fix per site, so one assertion per site.
            (
                f"sheet.pattern({_member_hole_str(blind)}, kind='linear', count=2, pitch=10)",
                "sheet.pattern",
            ),
        ):
            assert line.startswith(prefix)
            sheet = Sheet(part, title="T", number="N").auto_dimensions()
            exec(compile(line, "<emit>", "exec"), {"sheet": sheet, "hole": _declare_hole})  # noqa: S102
            feature = sheet._features[-1]
            rebuilt = getattr(feature, "member", feature)
            assert rebuilt.through is False, f"{line} rebuilt a THROUGH hole"
            assert rebuilt.depth is None

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
            if "sheet.pattern(" in ln and " = " in ln
        )
        assert "members=[" in line and line.count("(") >= 7  # member hole + 6 positions

    def test_pattern_member_keeps_its_counterbore(self):
        # #461 review r2: a counterbored bolt circle must keep the member's cbore on re-run
        line = next(
            ln
            for ln in _script_for(self._bolt_circle(cbore=True)).splitlines()
            if "sheet.pattern(" in ln and " = " in ln
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
        sheet = Sheet(part, title="T", number="N").auto_dimensions()
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
        line = _call_expr(_feature_line_for(_script_for(part), "sheet.slot("))
        eval(line, {"sheet": Sheet(part).auto_dimensions()})  # declare.slot() must not raise

    def test_pocket_line_re_runs_without_the_length_invariant_error(self):
        # #148a: declare.pocket() checks hi - lo == length to 1e-6; the emitter must derive
        # length from the emitted lo/hi so the generated pocket line doesn't raise on re-run.
        from draftwright import Sheet

        part = Box(80, 60, 20) - Pos(0, 0, 6) * Box(30.33, 20, 8)  # off-round → stresses rounding
        line = _call_expr(_feature_line_for(_script_for(part), "sheet.pocket("))
        eval(line, {"sheet": Sheet(part).auto_dimensions()})  # declare.pocket() must not raise

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
        assert "Object-reference tip" in Path(py).read_text(encoding="utf-8")
        step = tmp_path / "p.step"
        export_step(Box(60, 40, 12), str(step))
        py2 = generate_sheet_script(str(step), out=str(tmp_path / "step"))
        assert "Object-reference tip" not in Path(py2).read_text(encoding="utf-8")

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

    def test_bad_style_is_rejected(self, tmp_path):
        from typer.testing import CliRunner

        from draftwright.cli import app

        step = tmp_path / "plate.step"
        export_step(_plate(), str(step))
        r = CliRunner().invoke(app, [str(step), "--script", "--style", "bogus"])
        assert r.exit_code != 0

    def test_the_retired_imperative_style_says_so(self, tmp_path):
        """#940: `--style imperative` was a working flag last release, so it gets its own
        message rather than falling into the generic bad-value branch. The flag survives with
        one value; a script passing `--style sheet` keeps working, which is why it wasn't
        deleted outright."""
        from typer.testing import CliRunner

        from draftwright.cli import app

        step = tmp_path / "plate.step"
        export_step(_plate(), str(step))
        r = CliRunner().invoke(app, [str(step), "--script", "--style", "imperative"])
        assert r.exit_code != 0
        out = _norm(r.output)
        assert "retired" in out and "--style sheet" in out
        # ...and it is a distinct message, not the generic one a typo gets.
        generic = CliRunner().invoke(app, [str(step), "--script", "--style", "bogus"])
        assert _norm(generic.output) != out

    def test_the_retired_imperative_emitter_explains_itself(self):
        """The Python-level counterpart: `generate_script` is in `draftwright.__all__`, so a
        caller who upgrades hits a stub with the replacement in it rather than an
        AttributeError on a name that was public last release (#940; the stub exits at 0.4.0
        with #720)."""
        import draftwright

        with pytest.raises(NotImplementedError, match="generate_sheet_script"):
            draftwright.generate_script("part.step")

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


def _chamfered_corner(op, size):
    """A plate with ONE vertical corner edge chamfered or filleted — `op` is build123d's
    `chamfer` or `fillet`. Same edge selection the per-kind emit tests use, so the corpus
    and those tests describe the same geometry."""
    from build123d import Axis, Box

    part = Box(80, 50, 8)
    e = part.edges().filter_by(Axis.Z).sort_by(lambda x: x.center().X + x.center().Y)[-1]
    return op(e, size)


def _linear_recess_row(cutter, *, z, n=4, pitch=30.0, width=30.0):
    """A block with *n* identical recesses in a row — a pocket array when the cutter is
    blind, a slot array when it breaks through. Shaped after the recognisers' own fixtures
    (`test_pocket_pattern_recognition` / `test_slot_pattern_recognition`), because a
    pattern fixture that detects as N separate features tests nothing it claims to."""
    from build123d import Box, Pos

    part = Box(width, pitch * (n + 2), 20)
    for i in range(n):
        part -= Pos(0, (i - (n - 1) / 2) * pitch, z) * cutter
    return part


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
        return scripted, direct

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

    def test_grooved_shaft_parity(self, tmp_path, monkeypatch):
        """A groove between two steps (#953). The emitted script did not merely diverge here —
        it RAISED: the #631 span guard walks step spans only, so the groove's 4 mm read as an
        unmeasured interior gap and the declared model was refused. `build_drawing(shaft)`
        drew the same model happily, so the engine was rejecting its own detector's output the
        moment that output was declared rather than detected.

        Signature parity alone would also be satisfied by two identically BLANK drawings, so
        this pins the content on both sides as well — including the part that is still wrong.
        This shaft's shoulders sit 4 mm apart, which fails the legibility gate, which drops the
        whole step chain; the overall height was already suppressed at compile time on the
        premise that chain would convey it, so nothing states the 60 mm. That is #955, it
        predates this fix and affects the detected path identically, and it is asserted here
        rather than glossed: when #955 lands this test must fail and be updated."""
        from build123d import Align

        shaft = Cylinder(12, 60, align=(Align.CENTER, Align.CENTER, Align.MIN))
        shaft -= Pos(0, 0, 30) * (
            Cylinder(12, 4, align=(Align.CENTER, Align.CENTER, Align.MIN))
            - Cylinder(9, 4, align=(Align.CENTER, Align.CENTER, Align.MIN))
        )
        scripted, direct = self._parity(shaft, tmp_path, monkeypatch)

        for dwg, which in ((scripted, "scripted"), (direct, "direct")):
            names = {n for n, _ in dwg.iter_annotations()}
            labels = {lab for _, o in dwg.iter_annotations() if (lab := getattr(o, "label", None))}
            # Non-degenerate: the groove and the OD are drawn, so parity is over real content.
            assert any(n.startswith("m_groove") for n in names), (which, names)
            assert "4 WIDE × ø18" in labels, (which, labels)
            assert "ø24" in labels, (which, labels)
            # The #955 gap, stated: no height, and lint says so on both paths. These four
            # assertions are expected to fail when #955 lands — that is deliberate. Replace
            # them with the height's presence then, and check parity still holds; do not
            # relax them, or the fixture stops describing the drawing it produces. (Asserting
            # only the lint codes would not decouple this: they change when #955 lands too.)
            gap = f"{which}: #955 fixed? see the note above — update, don't relax."
            assert "dim_height" not in names, (gap, names)
            assert "60" not in labels, (gap, labels)
            codes = dwg.lint_summary()["by_code"]
            assert codes.get("axial_length_missing") == 1, (gap, codes)
            assert codes.get("step_dim_dropped") == 1, (gap, codes)

    def test_pocket_pattern_parity(self, tmp_path, monkeypatch):
        """#957 review: the emitted script named `pocket` without importing it, so a
        four-pocket array produced a file that raised NameError on its first feature line —
        a capability the imperative emitter had and #940 would have deleted without
        replacing. Full parity, executed, not a source-text check."""
        self._parity(_linear_recess_row(Box(10, 12, 6), z=7), tmp_path, monkeypatch)

    def test_slot_pattern_parity(self, tmp_path, monkeypatch):
        """The same defect on the slot verb (#957 review)."""
        self._parity(_linear_recess_row(Box(30, 8, 20), z=0, width=60), tmp_path, monkeypatch)

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

    def test_frame_zones_projection_round_trip(self, tmp_path, monkeypatch):
        # Script parity: --frame/--zones/--projection on the CLI must round-trip into the
        # emitted Sheet so the regenerated drawing matches the direct build (the furniture
        # was added in #767/#768/#769 but not wired into the emitter until now).
        from draftwright import Sheet

        flags = dict(frame=True, zones=True, projection="third")
        step = tmp_path / "part.step"
        export_step(_plate(), str(step))
        direct = build_drawing(step_file=str(step), title="PART", **flags)

        captured = {}
        monkeypatch.setattr(
            Sheet, "export", lambda self, stem=None: captured.setdefault("dwg", self.build())
        )
        py = generate_sheet_script(str(step), out=str(tmp_path / "gen"), title="PART", **flags)
        ctor = next(
            line
            for line in open(py, encoding="utf-8").read().splitlines()
            if line.startswith("sheet = Sheet(")
        )
        assert "frame=True" in ctor and "zones=True" in ctor and "projection='third'" in ctor
        exec(compile(open(py, encoding="utf-8").read(), py, "exec"), {})
        scripted = captured["dwg"]

        def furniture(dwg):
            names = set(dwg.annotations())
            return (
                "sheet_frame" in names,
                "zone_grid" in names,
                "projection_symbol" in names,
            )

        assert furniture(scripted) == furniture(direct) == (True, True, True)
        # a plain script carries none of them
        plain_py = generate_sheet_script(str(step), out=str(tmp_path / "plain"), title="PART")
        plain_ctor = next(
            line
            for line in open(plain_py, encoding="utf-8").read().splitlines()
            if line.startswith("sheet = Sheet(")
        )
        assert "frame" not in plain_ctor and "zones" not in plain_ctor

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


class TestEmittedFeaturesAreNamed:
    """Every emitted feature line binds a name, and the names are usable (#922).

    Not primarily a dimension feature: it removes POSITIONAL addressing from the artefact.
    The documented workflow is to comment a feature line out and re-run (ADR 0011 Amdt 1),
    which shifts every later index — so `sheet.of(2)` silently retargets onto a neighbour,
    while `sheet.of(hole1)` raises `NameError` at the line you edited.

    Uniform rather than only-where-referenced, because the alternative keys a FORMATTING
    decision on the dimension source, which has nothing to do with formatting.
    """

    @staticmethod
    def _part():
        from build123d import Box, Cylinder, Pos

        return Box(80, 50, 8) - Pos(-20, 0, 0) * Cylinder(4, 20) - Pos(20, 10, 0) * Cylinder(3, 20)

    def _src(self):
        return emit_sheet_script(
            detect_part_model(self._part()), "part", "bracket", title="T", number="N"
        )

    def test_every_statement_line_binds_a_name(self):
        """The property over the whole block, not a sample: any feature line that is a
        STATEMENT must bind. Sampling one kind is how nine verbs stayed unnameable."""
        import ast

        src = self._src()
        # The FEATURE block only. Since #938 the script also contains `dimension(...)` lines,
        # which are statements that correctly bind nothing — they name features rather than
        # declaring them. Slicing the block is what keeps this about feature declarations.
        lines = src.splitlines()
        start = next(i for i, ln in enumerate(lines) if ln.startswith("# ── Features"))
        end = next(i for i, ln in enumerate(lines[start:], start) if ln.startswith("# ── Dim"))
        declarations = [
            ln.split("#")[0].rstrip()
            for ln in lines[start:end]
            if "sheet." in ln and not ln.lstrip().startswith("#")
        ]
        assert declarations, "the fixture must emit feature lines"
        unbound = [ln for ln in declarations if not isinstance(ast.parse(ln).body[0], ast.Assign)]
        assert not unbound, f"emitted feature lines with no binding: {unbound}"

    def test_the_names_are_distinct_and_valid_identifiers(self):
        import ast

        src = self._src()
        names = [
            node.targets[0].id
            for node in ast.parse(src).body
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
        ]
        feature_names = [n for n in names if n not in ("part", "sheet")]
        assert feature_names, "the fixture must emit at least one feature"
        assert len(set(feature_names)) == len(feature_names), (
            f"duplicate bindings: {feature_names}"
        )
        assert all(n.isidentifier() for n in feature_names)
        # A binding must not shadow the script's own imports — `hole` is imported for pattern
        # members, so an unsuffixed `hole = ...` would break every later pattern line.
        imported = {
            alias.asname or alias.name
            for node in ast.parse(src).body
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert not (set(feature_names) & imported), "a binding shadows an import"

    def test_a_name_is_usable_by_the_verbs_it_exists_for(self):
        """The point of the name, executed rather than asserted about. Runs the emitted
        script with an extra line that references a binding — if the name did not resolve, or
        did not name that feature, this raises."""
        src = self._src()
        ns: dict = {"part": self._part()}
        body = src.replace("\npart\n", "\n", 1).replace("sheet.export('bracket')", "")
        exec(compile(body, "<emit>", "exec"), ns)  # noqa: S102 — exercising the generated script
        sheet = ns["sheet"]
        assert ns["hole1"] is not None
        assert sheet.features[ns["hole1"]._i].kind == "hole"
        assert sheet.features[ns["envelope1"]._i].kind == "envelope"

    def test_a_kind_with_no_declarative_verb_binds_nothing(self):
        """Its line is a COMMENT, and `rotational1 = # …` does not parse. Checked because the
        binding is applied in the emit loop, where a comment line looks like any other."""
        from draftwright.sheet_emit import _binding

        counts: dict = {}
        commented = "# rotational @ (0, 0, 0) — no declarative verb yet; drawn by the auto-pass"
        assert _binding(_Sentinel(), commented, counts) is None
        assert counts == {}, "a comment line must not consume a name"


class _Sentinel:
    kind = "rotational"


def test_of_rejects_a_wrong_argument_clearly():
    """The path Python's own suggester points at, after #922 named the features.

    Comment out `hole1` and re-run: Python raises `NameError ... Did you mean: 'hole'?`,
    because the script imports `hole` for pattern members. A user who follows that
    suggestion reaches `of()` with the constructor FUNCTION, and used to get a leaked
    `AttributeError: 'function' object has no attribute 'bounding_box'` from the
    match-by-object path, which assumed anything not a handle/index/Feature was a solid.
    """
    from build123d import Box

    from draftwright import Sheet
    from draftwright.model import hole as hole_ctor

    sheet = Sheet(Box(80, 50, 8))
    sheet.hole(diameter=6, at=(0, 0, 0), axis="z")
    for wrong in (hole_ctor, object(), "hole1"):
        with pytest.raises(ValueError, match="of\\(\\): expected a handle"):
            sheet.of(wrong)
    assert sheet.of(0) is not None  # the real routes still work


class TestAuthoredSetRoundTrips:
    """A generated script draws what the model it came from draws — including its omissions.

    `emit_sheet_script` refused an authored model until #922, and refusing was right: the
    alternative at the time was emitting `sheet.auto_dimensions()`, which silently turns
    "draw exactly these" back into "draw everything" — a script producing a different drawing
    from its source model (#707 class). Writing the declarations needed features to have
    names first (#931, #932).

    The assertion is on the DRAWN ANNOTATIONS of both drawings, not on the emitted text. A
    text check passes on any script that mentions the right roles, including one that names
    the wrong feature — which is the failure mode positional addressing would have produced.
    """

    @staticmethod
    def _authored_model():
        import dataclasses

        from build123d import Box, Cylinder, Pos

        from draftwright.model.ir import RequestedDimension

        part = Box(80, 50, 8) - Pos(-20, 0, 0) * Cylinder(4, 20)
        model = detect_part_model(part)
        hole = next(f for f in model.features if f.kind == "hole")
        env = next(f for f in model.features if f.kind == "envelope")
        authored = (RequestedDimension(hole, "bore.diameter"), RequestedDimension(env, "width"))
        return part, dataclasses.replace(model, authored_dimensions=authored)

    def _run(self, src, part):
        ns: dict = {"part": part}
        body = src.replace("\npart\n", "\n", 1)
        body = body[: body.index("sheet.export(")]
        exec(compile(body, "<emit>", "exec"), ns)  # noqa: S102 — exercising the generated script
        return ns

    def test_the_emitted_script_draws_the_same_dimensions_as_its_source_model(self):
        from draftwright import build_drawing

        part, model = self._authored_model()
        direct = build_drawing(part, model=model, authored=model.authored_dimensions, number="N")
        src = emit_sheet_script(model, "part", "bracket", title="T", number="N")
        regenerated = self._run(src, part)["sheet"].build()

        def dims(dwg):
            return {n for n, _ in dwg.iter_annotations() if not n.startswith(("note_", "title"))}

        assert dims(regenerated) == dims(direct), (
            "the regenerated script draws a different set from the model it came from"
        )

    def test_a_measured_dimension_round_trips_through_the_emitted_script(self):
        """The `authored_dimension` kind, executed (#957 review round 2).

        It is the one IR kind the detection corpus cannot reach — nothing detects an
        imported AP242 measurement or a hand-written `measured_dimension`, so it has to
        round-trip through the DECLARED route instead. It also has no referential form:
        it carries its own value, label, tolerances and reference points, so what must
        survive emit-and-re-run is that materialised content, not a role name.
        """
        from draftwright import Sheet, build_drawing

        def authored(part):
            sheet = Sheet(part, title="T", number="N").auto_dimensions()
            sheet.measured_dimension(
                kind="linear",
                value=40,
                label="40",
                dominant_axis="X",
                ref_bbox=(-20, -10, -5, 20, 10, 5),
                ref_pts=[(-20, 0, 0), (20, 0, 0)],
                upper_tol=0.1,
                lower_tol=0.0,
            )
            return sheet.model()

        part = Box(40, 20, 10)
        model = authored(part)
        direct = build_drawing(part, model=model, number="N")
        src = emit_sheet_script(model, "part", "authored", title="T", number="N")
        assert "sheet.measured_dimension(" in src
        regenerated = self._run(src, part)["sheet"].build()

        assert {n for n, _ in regenerated.iter_annotations()} == {
            n for n, _ in direct.iter_annotations()
        }
        # The materialised content, not just the annotation name: a re-run that dropped the
        # tolerance would still match on names alone.
        feat = next(
            f
            for f in self._run(src, part)["sheet"].model().features
            if f.kind == "authored_dimension"
        )
        assert (feat.value, feat.label, feat.upper_tol, feat.lower_tol) == (40, "40", 0.1, 0.0)

    def test_the_authored_set_is_not_silently_widened_to_the_planner_set(self):
        """The specific regression the old refusal existed to prevent: emitting
        `auto_dimensions()` for an authored model restores every omitted dimension."""
        _part, model = self._authored_model()
        src = emit_sheet_script(model, "part", "bracket", title="T", number="N")
        assert "sheet.auto_dimensions()" not in src
        assert 'sheet.dimension(hole1, "bore.diameter")' in src
        assert 'sheet.dimension(envelope1, "width")' in src

    def test_a_detected_model_still_states_the_planner_source(self):
        """The other half of the mandatory-source rule — narrowing the refusal must not cost
        the auto path its explicit `auto_dimensions()` line."""
        from build123d import Box

        # Since #938 a detected model MIRRORS the planner's set as `dimension(...)` lines
        # rather than deferring to `auto_dimensions()`. The property this guards is unchanged
        # — the script states its source explicitly — and the source is now the authored one.
        src = _script_for(Box(40, 20, 10))
        assert "sheet.authored_dimensions()" in src
        assert "sheet.dimension(" in src

    def test_an_EMPTY_authored_set_round_trips(self):
        """`authored_dimensions=()` is a valid model: "the author chose no dimensions".

        ADR 0016 distinguishes it from `None` ("the planner chooses"), and `build_drawing`
        honours both — but the emitted script could not express it. The authored source was
        entered IMPLICITLY, by calling `dimension(...)` at least once, so a set with no lines
        stated its source in a comment and then failed the mandatory-source check at build:
        a generated script that cannot run, from a model that draws fine (#933 review).

        This is the #707 class the rest of this PR closes, at the source boundary rather than
        the dimension boundary — so it is checked the same way, by running the script.
        """
        import dataclasses

        from build123d import Box

        part = Box(40, 20, 10)
        model = dataclasses.replace(detect_part_model(part), authored_dimensions=())
        direct = build_drawing(part, model=model, authored=model.authored_dimensions, number="N")

        src = emit_sheet_script(model, "part", "empty", title="T", number="N")
        assert "sheet.auto_dimensions()" not in src, "an empty authored set is not the planner's"
        regenerated = self._run(src, part)["sheet"].build()

        def dims(dwg):
            return {n for n, _ in dwg.iter_annotations() if not n.startswith(("note_", "title"))}

        assert dims(regenerated) == dims(direct) == set(), (
            "an empty authored set draws no generated dimensions, on both paths"
        )

    def test_every_authored_script_states_its_source_with_a_verb(self):
        """Not with a comment. The source is machine-checkable on the automatic path
        (`auto_dimensions()`), and it should be on this one too — a reader should not have to
        trust prose to know whether an absent dimension means anything."""
        import dataclasses

        from build123d import Box

        _part, model = self._authored_model()
        empty = dataclasses.replace(detect_part_model(Box(40, 20, 10)), authored_dimensions=())
        for m in (model, empty):
            src = emit_sheet_script(m, "part", "s", title="T", number="N")
            assert "sheet.authored_dimensions()" in src

    def test_an_authored_dimension_on_an_unnameable_feature_refuses_the_script(self):
        """Narrowed, not removed. A kind with no declarative verb emits a COMMENT, so it binds
        no name and the declaration has nothing to reference. Emitting the rest would produce
        a script that draws a different set — exactly what the blanket refusal prevented.

        Driven SYNTHETICALLY since #945. This used `rotational`, which was then the one kind
        carrying planned dimensions without a declarative verb; giving it that verb left no
        real model able to reach the branch — the remaining comment-only kinds are aspects
        (`control_frame`, `datum_ref`, `finish`, `note`), which carry no `DimParameter`, so
        `_check_authored_targets` rejects an authored dimension on them first.

        The branch is kept and tested rather than deleted: it is defensive against a FUTURE
        kind arriving with parameters and no verb, which is exactly how `rotational` looked
        until this release. Constructing the situation directly is the honest way to keep
        proving it, instead of waiting for a fixture that no longer qualifies.
        """
        import dataclasses

        from build123d import Box

        from draftwright import sheet_emit
        from draftwright.model.ir import RequestedDimension

        model = detect_part_model(Box(80, 50, 8))
        env = next(f for f in model.features if f.kind == "envelope")
        model = dataclasses.replace(
            model, authored_dimensions=(RequestedDimension(env, "width.length"),)
        )
        # Make the envelope unnameable the way a verb-less kind is: its line becomes a comment.
        real = sheet_emit._feature_line
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                sheet_emit,
                "_feature_line",
                lambda f: "# envelope — no declarative verb" if f is env else real(f),
            )
            with pytest.raises(ValueError, match="has no declarative verb"):
                emit_sheet_script(model, "part", "bracket", title="T", number="N")


class TestTheDimensionMirror:
    """A generated script declares the planner's dimensions as commentable lines (#938).

    Before this the script said `sheet.auto_dimensions()`, so the editable-drawing promise —
    comment a line out to drop it — held for FEATURES and not for DIMENSIONS. The only way to
    drop one dimension was to comment out its whole feature, losing that feature's callout,
    centre marks and location with it. #922 built the capability across three PRs; nothing
    used it, because `--script` emits from `detect_part_model`, which never carries an
    authored set.
    """

    @staticmethod
    def _corpus():
        from build123d import Align, Axis, Box, Cylinder, Pos, Rot

        def flange():
            part = Cylinder(21, 4)
            for x in (-18, 18):
                for y in (-18, 18):
                    part += Pos(x, y, 2) * Box(10, 10, 4, align=(Align.CENTER,) * 3)
            part = part + Pos(0, 0, 2) * Cylinder(15.5, 12) + Pos(0, 0, 10) * Cylinder(12.5, 12)
            part -= Cylinder(8, 30)
            for x in (-18, 18):
                for y in (-18, 18):
                    part -= Pos(x, y, 0) * Cylinder(2, 10)
            return part.rotate(Axis.X, 90)

        return {
            "plate+hole": Box(80, 50, 8) - Pos(-20, 0, 0) * Cylinder(4, 20),
            "flange (no envelope feature)": flange(),
            "stepped": Box(40, 12, 40) - Pos(10, 0, 20) * Box(20, 12, 20),
            "slot": Box(80, 60, 12) - Pos(10, 0, 6) * Box(30, 8, 10),
            "pocket": Box(80, 60, 20) - Pos(0, 0, 14) * Box(30, 20, 14),
            "two holes": Box(80, 50, 8)
            - Pos(-20, 0, 0) * Cylinder(4, 20)
            - Pos(20, 10, 0) * Cylinder(3, 20),
            "pad": Box(80, 60, 10) + Pos(0, 0, 10) * Box(30, 20, 4),
            "bare block": Box(60, 40, 20),
            # A real X-axis bore. The first version used a rotated cylinder through a block
            # and detected NOTHING but an envelope, so the off-axis location path this corpus
            # claimed to cover was untested (#947 review). `_the_corpus_detects_what_it_claims`
            # now makes that failure loud instead of invisible.
            "side-drilled": Box(12, 40, 30) - Pos(0, 8, 6) * Rot(0, 90, 0) * Cylinder(3, 12),
            "boss": Box(80, 60, 12) + Pos(0, 0, 12) * Cylinder(10, 8),
            # Turned parts joined the corpus with #945: `rotational` gained a declarative
            # verb, so they mirror their dimensions instead of falling back. Contiguous on
            # purpose — a disconnected profile is a separate, unrelated defect (#943).
            "turned shaft": Cylinder(15, 20) + Pos(0, 0, 17.5) * Cylinder(10, 15),
            "bored flange": Cylinder(40, 8) - Cylinder(8, 20),
            # The two PATTERN kinds whose members are nested constructor calls
            # (`pocket_pattern(pocket(...), …)`). They were classified "untested" under
            # #948 and were in fact BROKEN — the emitted script named `pocket`/`slot`
            # without importing them, so it raised NameError on its first feature line
            # (#957 review). This corpus executes what it emits, so carrying them here is
            # what makes that class of defect impossible to reintroduce quietly.
            "pocket pattern": _linear_recess_row(Box(10, 12, 6), z=7),
            "slot pattern": _linear_recess_row(Box(30, 8, 20), z=0, width=60),
            # The machined kinds (#957 review round 2). Each already had a fixture proving
            # it EMITS its verb; none had one proving the emitted script RUNS and draws the
            # same drawing. That is the gap round 1 turned into a NameError, so the answer
            # is the same: the fixtures move to the corpus that executes, and the coverage
            # roster stops saying "untested".
            "chamfer": _chamfered_corner(bd_chamfer, 4),
            "fillet": _chamfered_corner(bd_fillet, 3),
            "flat": Cylinder(10, 30) - Pos(10, 0, 0) * Box(10, 40, 40),  # D-shaft
            "groove": Cylinder(10, 40) - (Cylinder(10, 4) - Cylinder(8, 4)),  # circlip groove
            "plate": Box(80, 50, 8) + Pos(-36, 0, 29) * Box(8, 50, 50),  # base + upright
        }

    @staticmethod
    def _run(src, part):
        ns: dict = {"part": part}
        body = src.replace("\npart\n", "\n", 1)
        exec(compile(body[: body.index("sheet.export(")], "<emit>", "exec"), ns)  # noqa: S102
        return ns

    #: What each fixture is FOR. A fixture that stops detecting its kind stops testing the
    #: path it was added for, silently — which is what happened to "side-drilled" (detected
    #: only an envelope) and "slot" (detected a pocket) until #947's review counted them.
    _EXPECTED_KINDS = {
        "plate+hole": {"hole"},
        "flange (no envelope feature)": {"hole", "pattern", "pad", "step"},
        "stepped": {"step_level"},
        "slot": {"pocket"},
        "pocket": {"pocket"},
        "two holes": {"hole"},
        "pad": {"pad", "slot"},
        "bare block": {"envelope"},
        "side-drilled": {"hole"},
        "boss": {"boss"},
        "turned shaft": {"rotational", "step"},
        "bored flange": {"rotational"},
        "pocket pattern": {"pocket_pattern"},
        "slot pattern": {"slot_pattern"},
        "chamfer": {"chamfer"},
        "fillet": {"fillet"},
        "flat": {"flat"},
        "groove": {"groove"},
        "plate": {"plate"},
    }

    @pytest.mark.parametrize("name", sorted(_corpus()))
    def test_the_corpus_detects_what_it_claims(self, name):
        """A fixture named for a kind it does not produce is coverage theatre."""
        kinds = {f.kind for f in detect_part_model(self._corpus()[name]).features}
        expected = self._EXPECTED_KINDS[name]
        assert expected <= kinds, (
            f"{name} detects {sorted(kinds)}, missing {sorted(expected - kinds)}"
        )

    def test_the_corpus_names_every_fixture_it_carries(self):
        """The two tables cannot drift: a fixture added without an expectation is a fixture
        nobody has said what it is for."""
        assert set(self._corpus()) == set(self._EXPECTED_KINDS)

    def test_the_side_drilled_fixture_reaches_the_off_axis_location_path(self):
        """Named because it is the path that was silently uncovered. An X-axis bore's
        position is compiled by `_compile_off_axis_hole_locations`, a different code path
        from the Z-normal ladder (#925)."""
        model = detect_part_model(self._corpus()["side-drilled"])
        from draftwright.model.compiled import compile_dimensions

        roles = {loc.role for loc in compile_dimensions(model).locations}
        assert "location_off_axis" in roles, f"got {sorted(roles)}"

    @pytest.mark.parametrize("name", sorted(_corpus()))
    def test_the_mirrored_script_draws_what_the_automatic_drawing_draws(self, name):
        """The acceptance, per fixture: annotation-set EQUALITY.

        On drawn annotations, not emitted text — a text assertion passes on a script that
        names the wrong feature, which is exactly what positional addressing produced before
        #932. A corpus rather than one part, because single-fixture guards are how whole
        paths stayed uncovered twice in this series (#925 had no holes, #934 had one ladder).
        """
        part = self._corpus()[name]
        model = detect_part_model(part)
        # Same title/number on both sides — the signature includes the title block, and a
        # mismatch there would read as a dimension difference.
        automatic = build_drawing(part, model=model, title="T", number="N")
        src = emit_sheet_script(model, "part", "s", title="T", number="N")
        regenerated = self._run(src, part)["sheet"].build()

        names = {n for n, _ in automatic.iter_annotations()}
        got = {n for n, _ in regenerated.iter_annotations()}
        assert got == names, f"{name}: missing {sorted(names - got)}, extra {sorted(got - names)}"
        # Names alone are too weak: a request aimed at the right feature kind but rebuilt
        # with the wrong VALUE or span keeps exactly the same registry name (#944 review).
        # `_annotation_signature` compares labels, dimension specs, leader coverage and boxes.
        assert _annotation_signature(regenerated) == _annotation_signature(automatic), (
            f"{name}: same annotation names but different content — a mirrored dimension "
            "reproduced the wrong value, span, side or view"
        )

    def test_every_planner_dimension_gets_a_line(self):
        from build123d import Box, Cylinder, Pos

        part = Box(80, 50, 8) - Pos(-20, 0, 0) * Cylinder(4, 20)
        src = emit_sheet_script(detect_part_model(part), "part", "s", title="T", number="N")
        assert "sheet.auto_dimensions()" not in src
        assert 'sheet.dimension(hole1, "bore.diameter")' in src
        assert 'sheet.dimension(hole1, "location")' in src
        assert 'sheet.dimension(envelope1, "width.length")' in src

    def test_commenting_one_line_drops_exactly_that_dimension(self):
        """The point of the whole change, executed rather than asserted about."""
        from build123d import Box, Cylinder, Pos

        part = Box(80, 50, 8) - Pos(-20, 0, 0) * Cylinder(4, 20)
        src = emit_sheet_script(detect_part_model(part), "part", "s", title="T", number="N")
        full = {n for n, _ in self._run(src, part)["sheet"].build().iter_annotations()}

        target = 'sheet.dimension(envelope1, "width.length")'
        assert target in src
        edited = src.replace(target, "# " + target)
        reduced = {n for n, _ in self._run(edited, part)["sheet"].build().iter_annotations()}
        assert full - reduced == {"m_env_width"}, (
            f"commenting one line changed {sorted(full - reduced)} — it must drop exactly one"
        )

    def test_a_correlated_set_emits_ONE_line(self):
        """A `step_height` ladder is one `AddressableDimension` holding N rungs, so there is
        one line and no member line to mislead (ADR 0016 identity tier 3)."""
        from build123d import Box, Pos

        part = Box(40, 12, 40) - Pos(10, 0, 20) * Box(20, 12, 20)
        src = emit_sheet_script(detect_part_model(part), "part", "s", title="T", number="N")
        assert src.count("step_height") == 1

    def test_a_model_the_emitter_cannot_fully_name_keeps_the_planner_source(self):
        """The fallback still exists, and is still reachable — just not by `rotational`.

        This asserted a turned part fell back, which #945 fixed by giving `rotational` a
        declarative verb. The PROPERTY is unchanged and still worth guarding: a model with any
        dimension no line can name keeps `auto_dimensions()`, because declaring only the
        others would claim a completeness it does not have. Driven synthetically now, since
        no detected part currently reaches it — which is itself the point, and why the message
        no longer blames #945 unconditionally.
        """
        from build123d import Box

        from draftwright import sheet_emit

        model = detect_part_model(Box(80, 50, 8))
        # Starve the request builder: every compiled dimension becomes unmirrorable.
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(sheet_emit, "_mirrored_requests", lambda *_a, **_kw: [])
            src = emit_sheet_script(model, "part", "s", title="T", number="N")
        assert "sheet.auto_dimensions()" in src
        assert "the emitter has no line for" in src, "the fallback must NAME what escaped"

    def test_the_synthesised_envelope_names_only_its_height(self):
        """A part with no `EnvelopeFeature` gets one declared, because an authored set must
        be able to name every measurement in it and `_compile_overall_height` refuses the
        bounding-box fallback under one (#925). Naming only `height` keeps the drawing
        identical — width and depth stay omitted exactly as the planner left them."""
        part = TestTheDimensionMirror._corpus()["flange (no envelope feature)"]
        model = detect_part_model(part)
        assert not any(f.kind == "envelope" for f in model.features)
        src = emit_sheet_script(model, "part", "s", title="T", number="N")
        assert 'sheet.dimension(envelope1, "height.length")' in src
        assert 'sheet.dimension(envelope1, "width.length")' not in src
        assert 'sheet.dimension(envelope1, "depth.length")' not in src


#: Annotations a generated script legitimately does NOT declare, keyed by EXACT name prefix
#: where the family is closed, and each carrying the argument that it can never be a
#: dimension line (ADR 0016's permanent-exception category, #937).
#:
#: Deliberately NOT subsystem-wide prefixes: `"section_"` would admit every annotation that
#: subsystem ever grows, including a future `section_depth` measurement — the exact blind
#: spot the behavioural test below claims to supersede (#947 review).
#:
#: And deliberately only what the corpus OBSERVES. An earlier version listed a section/detail
#: family from memory; the names were wrong (`section_label` does not exist — it is
#: `section_caption`), so the list simultaneously carried dead entries and would have flagged
#: a real sectioned drawing. A drawing feature this corpus does not reach is absent on
#: purpose: adding one fails loudly, and whoever adds it names the annotation with the real
#: name in front of them.
_SCRIPT_FURNITURE = {
    "m_cm": "centre marks — sized off the hole they mark, not a printed value (#875)",
    "centerline_front": "shows where the front view's axis is; no measurement",
    "centerline_plan": "shows where the plan view's axis is; no measurement",
    "centerline_side": "shows where the side view's axis is; no measurement",
    "bc_": "a bolt circle's centreline — geometry, like a centre mark",
    "note_iso_nts": "the ISO NTS caption — a sheet-level statement, not a feature's",
    "title_block": "sheet metadata; edited through Sheet(...) kwargs, not a dimension line",
}


def _is_value_bearing(annotation) -> bool:
    """Does this annotation PRINT a measurement?

    The classification that matters, and the one a name cannot give. An allowlist entry is
    only defensible if the thing it names carries no number — otherwise "furniture" is just
    a word for "we decided not to look" (#947 review).
    """
    label = getattr(annotation, "label", None)
    return bool(label) and any(ch.isdigit() for ch in str(label))


#: Every DIMENSIONAL collection on `RenderableDimensionPlan`, and how `unmirrored_dimensions`
#: accounts for it. A field added without an entry fails `test_the_plan_surface_is_ratcheted`.
#:
#: Python gives no exhaustiveness check, so a new collection — `plan.ordinate_dimensions`,
#: say — would be rendered and silently unmirrored, and every corpus test would still pass
#: (#947 review). This is the ratchet that makes adding one a decision rather than an
#: oversight.
_PLAN_DIMENSIONAL_FIELDS = {
    "groups": "walked per approved dim, matched on parameter_id or bare role",
    "locations": "walked per approved location, matched on the coarse 'location' role",
    "ladders": "walked per ladder, via _LADDER_PARAMETER where the spelling differs",
    "diagnostics": "NOT dimensional — the omissions channel; nothing to mirror by definition",
}


def test_the_plan_surface_is_ratcheted():
    """`unmirrored_dimensions` walks the compiled plan field by field, so the plan growing a
    field is the one way it can silently stop being complete."""
    from draftwright.model.compiled import RenderableDimensionPlan

    assert set(RenderableDimensionPlan.__dataclass_fields__) == set(_PLAN_DIMENSIONAL_FIELDS), (
        "RenderableDimensionPlan changed shape — classify the new field here and, if it "
        "carries dimensions, walk it in sheet_emit.unmirrored_dimensions"
    )


#: Every IR feature kind, mapped to how the dimension mirror is proven to handle it.
#:
#: `"corpus"` — a fixture in `TestTheDimensionMirror._corpus()` detects it, so the round trip
#: is exercised end to end (emit → run → compare annotation signatures).
#: A string starting with `"unnameable"` — no declarative verb, so the emitter falls back and
#: says so; the kind cannot be mirrored by design until that verb exists.
#: `"untested"` — neither. **This is debt**, and naming it is the point: the review found two
#: fixtures detecting something other than their name, and an unlisted kind is the same hole
#: with no label on it.
_KIND_MIRROR_COVERAGE = {
    "hole": "corpus",
    "pattern": "corpus",
    "boss": "corpus",
    "step": "corpus",
    "step_level": "corpus",
    "slot": "corpus",
    "pocket": "corpus",
    "pad": "corpus",
    "envelope": "corpus",
    "rotational": "corpus",
    "pmi": "unnameable — raw AP242, emitted as sheet.add(PmiFeature(...)) (ADR 0016)",
    "chamfer": "corpus",
    "fillet": "corpus",
    "flat": "corpus",
    "groove": "corpus",
    "plate": "corpus",
    "pocket_pattern": "corpus",
    "slot_pattern": "corpus",
    "authored_dimension": "corpus (declared route — nothing detects one)",
    "control_frame": "aspect — carries no DimParameter, so nothing to mirror",
    "datum_ref": "aspect — carries no DimParameter, so nothing to mirror",
    "finish": "aspect — carries no DimParameter, so nothing to mirror",
    "note": "aspect — carries no DimParameter, so nothing to mirror",
}


def test_every_ir_kind_is_classified_for_mirror_coverage():
    """Every feature kind is either exercised, argued unnameable, or NAMED as untested.

    An earlier version of this test was called
    `test_every_ir_kind_is_reachable_by_the_mirror_or_named_as_unnameable` and compared the
    IR kinds to `_FACTS` — it constructed nothing and proved nothing about the mirror, while
    its name and docstring claimed otherwise (#947 review). A test whose name overstates it
    is worse than no test: it answers the question a reader came to ask.

    This does not prove the untested kinds work. It makes the fact that they are untested a
    thing you have to look at, and fails the moment a kind is added without a decision.
    """
    from draftwright.model import ir as _ir

    kinds = {
        value.kind
        for value in vars(_ir).values()
        if isinstance(value, type) and isinstance(getattr(value, "kind", None), str)
    }
    assert kinds == set(_KIND_MIRROR_COVERAGE), (
        "an IR kind was added or removed — classify it: exercised by the corpus, argued "
        "unnameable, or explicitly untested"
    )


def test_the_corpus_really_covers_the_kinds_it_claims():
    """`"corpus"` is a claim about fixtures, so check it against them. Two fixtures were
    detecting something other than their name until the review counted them (#947)."""
    detected: set[str] = set()
    for part in TestTheDimensionMirror._corpus().values():
        detected |= {f.kind for f in detect_part_model(part).features}
    claimed = {k for k, v in _KIND_MIRROR_COVERAGE.items() if v == "corpus"}
    assert claimed <= detected, (
        f"{sorted(claimed - detected)} are marked 'corpus' but no fixture detects them"
    )


class TestTheMirrorCoversTheCompiledSet:
    """The structural half, and the durable one: every dimension the COMPILER approved has an
    emitted line, checked against the compiler rather than against a drawing.

    The behavioural test below can only see what its corpus draws. This one is complete for
    whatever model it is given, so a new compiled dimension kind fails it the moment any
    fixture carries the feature — instead of waiting for someone to notice the drawing
    changed (#947 review).
    """

    @pytest.mark.parametrize("name", sorted(TestTheDimensionMirror._corpus()))
    def test_no_corpus_fixture_falls_back_to_auto_dimensions(self, name):
        """The corpus is the load-bearing proof of the #940 per-kind gate, so it must not be
        able to go green while a kind stops being mirrored.

        `auto_dimensions()` is the emitter's fallback for a model it cannot express as
        declarations. A drawing built from it reproduces the automatic one BY CONSTRUCTION,
        so the parity test below passes either way, and the completeness guard used to SKIP
        exactly when the fallback kicked in — the two tests covering for each other rather
        than catching it (#957 review round 3, found by mutating `_mirrored_requests` to
        drop chamfer groups: parity stayed green, the guard skipped).

        Every fixture here mirrors today. If one legitimately cannot, it does not get to
        fail quietly: give it its own named exemption with the reason, the way
        `_KIND_MIRROR_COVERAGE` does for unnameable kinds."""
        from draftwright.sheet_emit import _is_mirrorable

        part = TestTheDimensionMirror._corpus()[name]
        model = detect_part_model(part)
        assert _is_mirrorable(model), f"{name}: the emitter cannot mirror this model"
        src = emit_sheet_script(model, "part", "s", title="T", number="N")
        assert "sheet.auto_dimensions()" not in src, (
            f"{name}: the emitted script fell back to auto_dimensions(), so it declares no "
            "editable dimension set — the corpus would still pass parity, which is the hole"
        )
        assert "sheet.dimension(" in src, f"{name}: no dimension declarations emitted"

    @pytest.mark.parametrize("name", sorted(TestTheDimensionMirror._corpus()))
    def test_every_approved_dimension_has_a_line(self, name):
        model = detect_part_model(TestTheDimensionMirror._corpus()[name])
        # No mirrorability skip: `test_no_corpus_fixture_falls_back_to_auto_dimensions`
        # makes a non-mirrorable corpus fixture a failure, so reaching here unmirrorable
        # would be that test's job to report, not this one's to tolerate (#957 r3).
        missing = unmirrored_dimensions(model)
        assert not missing, (
            f"{name}: the compiler approved {missing} and the script declares no line for "
            "them, so 'THIS IS THE COMPLETE SET' is false"
        )

    def test_the_guard_reports_each_kind_of_gap_it_can_find(self, monkeypatch):
        """The guard's FAILURE branches, which nothing else reaches.

        Every other test drives `unmirrored_dimensions` on models where it correctly finds
        nothing, so the code that reports a gap — one branch per compiled collection — was
        never executed. A guard whose reporting path is untested is a guard that might not
        fire, which is the whole thing it exists to prevent.

        Starves the emitter of requests and asserts each collection is accounted for
        separately, so a branch that silently stopped reporting shows up as a missing
        category rather than as a slightly shorter list.
        """
        from draftwright import sheet_emit

        # A part carrying all three: group dims (hole/envelope), a location, and the
        # bounding-box overall-height ladder via the synthesised envelope.
        part = TestTheDimensionMirror._corpus()["flange (no envelope feature)"]
        model = detect_part_model(part)
        monkeypatch.setattr(sheet_emit, "_mirrored_requests", lambda *_a, **_kw: [])

        missing = unmirrored_dimensions(model)
        assert missing, "starved of every request, the guard must report gaps"
        assert any("." in m and "location" not in m for m in missing), "no group dim reported"
        assert any(m.endswith(".location") for m in missing), "no location reported"
        assert "(bounding box).overall_height" in missing, "no bbox overall height reported"

    def test_the_guard_reports_a_ladder_attached_to_a_feature(self, monkeypatch):
        """The fourth branch: an `overall_height` ladder whose ref IS a feature, which the
        bounding-box case above cannot reach."""
        from draftwright import sheet_emit

        model = detect_part_model(TestTheDimensionMirror._corpus()["stepped"])  # has envelope
        monkeypatch.setattr(sheet_emit, "_mirrored_requests", lambda *_a, **_kw: [])
        missing = unmirrored_dimensions(model)
        assert any(m.startswith("envelope.") for m in missing), f"got {missing}"

    def test_the_fallback_is_the_ONLY_way_a_dimension_escapes(self):
        """A model the emitter says it can mirror must have no unmirrored dimension. That is
        what makes `_is_mirrorable` a real capability check rather than a guess: if it returns
        True while something is unexpressible, this fails."""
        from draftwright.sheet_emit import _is_mirrorable

        for name, part in TestTheDimensionMirror._corpus().items():
            model = detect_part_model(part)
            if _is_mirrorable(model):
                assert not unmirrored_dimensions(model), (
                    f"{name}: _is_mirrorable said yes but dimensions are unexpressible"
                )


class TestTheScriptAccountsForEveryAnnotation:
    """Absence of a `dimension(...)` line means SUPPRESSED — a claim that is only safe over
    the set the emitter can actually express (#939).

    So every annotation the drawing carries must be one of two things: produced by a line the
    script contains, or named here as furniture. **No third state.** A dimensional mark the
    script cannot express would otherwise sit in the drawing with nothing to comment out and
    nothing saying why — the reader would conclude the engine chose it, when in fact the
    script could not say anything about it.

    Checked BEHAVIOURALLY, by commenting every line out and seeing what survives, rather than
    by classifying annotation names. A prefix taxonomy is a proxy: it asserts what the author
    believed about the names, and the same shortcut has twice been the bug (`_VALUE_FREE`
    absorbing the pitch dim, `_PENDING_VALUE_CARRYING` read as "furniture").
    """

    def _corpus(self):
        return TestTheDimensionMirror._corpus()

    @pytest.mark.parametrize("name", sorted(TestTheDimensionMirror._corpus()))
    def test_commenting_every_dimension_line_leaves_only_named_furniture(self, name):
        part = self._corpus()[name]
        src = emit_sheet_script(detect_part_model(part), "part", "s", title="T", number="N")
        if "sheet.dimension(" not in src:
            pytest.skip(f"{name} falls back to auto_dimensions() — #945")

        blanked = "\n".join(
            "# " + line if line.startswith("sheet.dimension(") else line
            for line in src.splitlines()
        )
        ns: dict = {"part": part}
        body = blanked.replace("\npart\n", "\n", 1)
        exec(compile(body[: body.index("sheet.export(")], "<emit>", "exec"), ns)  # noqa: S102
        survivors = {n for n, _ in ns["sheet"].build().iter_annotations()}

        # Two independent checks, because a name is not a classification. First: does the
        # thing print a measurement — the claim each furniture entry's ARGUMENT makes.
        # `_is_value_bearing` was introduced for exactly this and then never called; the list
        # was validated only by the length of its prose (#947 review).
        annotations = dict(ns["sheet"].build().iter_annotations())
        smuggled = sorted(
            n
            for n in survivors
            if n.startswith(tuple(_SCRIPT_FURNITURE)) and _is_value_bearing(annotations[n])
        )
        assert not smuggled, (
            f"{name}: {smuggled} are allowed as furniture but PRINT a measurement — the "
            "entry's argument is false, so it is an unexpressible dimension in disguise"
        )
        unaccounted = sorted(n for n in survivors if not n.startswith(tuple(_SCRIPT_FURNITURE)))
        assert not unaccounted, (
            f"{name}: {unaccounted} survive with every dimension line commented out — the "
            "script cannot express them, so 'absence of a line means suppressed' is false "
            "for them. Either emit a line, or add them to _SCRIPT_FURNITURE with a reason."
        )

    def test_the_furniture_list_is_argued_not_just_listed(self):
        """A permanent exception carries its argument (ADR 0016, #937). Without one a reader
        cannot tell it from unfinished work, and the list decays into a suppression."""
        for prefix, why in _SCRIPT_FURNITURE.items():
            assert len(why) > 20, f"{prefix} needs a reason it can never be a dimension line"

    def test_every_furniture_entry_is_OBSERVED_not_asserted(self):
        """Every entry must be produced by this corpus. **No exemptions.**

        The first version listed a `section_` family from memory — `section_label`,
        `section_line` — and the renderer actually emits `section_caption`,
        `section_arrow_*`, `section_wing_*`. So the list carried three dead entries AND would
        have reported a real sectioned drawing's caption as unaccounted (#947 review). It had
        been "validated" only by the length of its prose.

        An entry nobody observes is a guess, and a guess in an allowlist only ever widens
        what the accounting forgives. So the list is exactly what this corpus draws, and a
        drawing feature it does not reach — sections, detail views, balloons, hole tables —
        is deliberately ABSENT. Adding such a fixture then fails loudly, and whoever adds it
        classifies the annotation with its real name in front of them rather than from memory.
        """
        drawn: set[str] = set()
        for part in self._corpus().values():
            dwg = build_drawing(part, model=detect_part_model(part), title="T", number="N")
            drawn |= {n for n, _ in dwg.iter_annotations()}
        unobserved = sorted(
            prefix for prefix in _SCRIPT_FURNITURE if not any(n.startswith(prefix) for n in drawn)
        )
        assert not unobserved, (
            f"{unobserved} are exempted as furniture but this corpus never draws them — "
            "remove them, or add a fixture that produces them so the name is observed"
        )
