"""The declarative Sheet-DSL emitter (ADR 0011 Amendment 1, #461).

Generates a `Sheet(...)` script from a detected part — one commentable line per feature.
Detected input only writes numbers (the part-seam form); we never fabricate geometry.
"""

import ast
import math

from build123d import Box, Cylinder, Pos, export_step

from draftwright.builder import detect_part_model
from draftwright.sheet_emit import emit_sheet_script, generate_sheet_script


def _plate():
    return Box(80, 50, 8) - Pos(20, 10, 4) * Cylinder(4, 20) - Pos(-20, 10, 4) * Cylinder(4, 20)


def _script_for(part, part_expr="part = PART", stem="drawing", **kw):
    return emit_sheet_script(detect_part_model(part), part_expr, stem, title="T", number="N", **kw)


class TestEmit:
    def test_emits_one_declarative_line_per_feature(self):
        src = _script_for(_plate())
        assert "sheet = Sheet(part, title='T', number='N')" in src
        assert "sheet.hole(diameter=8" in src  # the ⌀8 holes
        assert "sheet.envelope()" in src
        assert src.rstrip().endswith("sheet.export('drawing')")

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

    def test_counterbore_flags_the_auto_section(self):
        part = Box(60, 60, 16) - Pos(0, 0, 0) * Cylinder(4, 30) - Pos(0, 0, 4) * Cylinder(8, 12)
        src = _script_for(part)
        assert "cbore=(" in src  # the counterbore rides the hole line
        assert "Section A–A auto-triggers" in src

    def test_blind_hole_gets_depth(self):
        part = Box(40, 40, 20) - Pos(0, 0, 6) * Cylinder(4, 16)  # blind ⌀8
        src = _script_for(part)
        assert ".depth(" in src

    def test_pattern_emits_the_pattern_verb(self):
        part = Cylinder(40, 8)
        for i in range(6):
            a = i * math.pi / 3
            part -= Pos(25 * math.cos(a), 25 * math.sin(a), 0) * Cylinder(3, 20)
        src = _script_for(part)
        assert "sheet.pattern(hole(" in src and 'kind="bolt_circle"' in src

    def test_non_declarable_kind_is_flagged_not_dropped(self):
        # a counterbored plate carries a step_level (horizontal face levels) with no Sheet verb —
        # it must surface as an inline comment, never silently vanish
        part = Box(100, 70, 24) - Pos(0, 0, 0) * Cylinder(9, 40) - Pos(0, 0, 8) * Cylinder(15, 20)
        src = _script_for(part)
        assert any(
            ln.startswith("#") and "no declarative verb yet" in ln for ln in src.splitlines()
        )

    def test_needs_hole_import_only_when_a_pattern_is_present(self):
        # `hole` is only imported when a pattern line references it
        assert "from draftwright.model import hole" not in _script_for(Box(20, 20, 5))


class TestGenerate:
    def test_step_input_emits_a_self_contained_import_seam(self, tmp_path):
        step = tmp_path / "plate.step"
        export_step(_plate(), str(step))
        py = generate_sheet_script(str(step), out=str(tmp_path / "gen"))
        src = open(py).read()
        assert "import_step(" in src and "part = ..." not in src

    def test_shape_input_leaves_a_part_seam(self, tmp_path):
        py = generate_sheet_script(_plate(), out=str(tmp_path / "gen"))
        src = open(py).read()
        assert "part = ..." in src and "import_step(" not in src

    def test_generated_step_script_round_trips_to_a_drawing(self, tmp_path):
        # the whole point: the generated script RUNS and produces a real drawing
        step = tmp_path / "plate.step"
        export_step(_plate(), str(step))
        stem = tmp_path / "gen"
        py = generate_sheet_script(str(step), out=str(stem))
        exec(compile(open(py).read(), py, "exec"), {})
        assert (tmp_path / "gen.svg").exists()

    def test_title_from_basename_not_the_out_path(self, tmp_path):
        step = tmp_path / "widget.step"
        export_step(Box(20, 20, 5), str(step))
        py = generate_sheet_script(str(step), out=str(tmp_path / "gen"))
        src = open(py).read()
        assert "title='GEN'" in src  # basename of out, upper — not the full path


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
        assert "sheet.hole(" in open(tmp_path / "g.py").read()

    def test_bad_style_is_rejected(self, tmp_path):
        from typer.testing import CliRunner

        from draftwright.cli import app

        step = tmp_path / "plate.step"
        export_step(_plate(), str(step))
        r = CliRunner().invoke(app, [str(step), "--script", "--style", "bogus"])
        assert r.exit_code != 0
