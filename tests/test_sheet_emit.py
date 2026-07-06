"""The declarative Sheet-DSL emitter (ADR 0011 Amendment 1, #461).

Generates a `Sheet(...)` script from a detected part — one commentable line per feature.
Detected input only writes numbers (the part-seam form); we never fabricate geometry.
"""

import ast
import math
import os
from collections import Counter

import pytest
from build123d import Box, Cylinder, Pos, Shape, export_step

from draftwright.builder import build_drawing, detect_part_model
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

    def test_slot_line_re_runs_without_the_length_invariant_error(self):
        # #461 review: declare.slot() checks hi - lo == length to 1e-6; the emitter must derive
        # length from the emitted lo/hi so the generated slot line doesn't raise on re-run.
        from draftwright import Sheet

        part = Box(60, 30, 12) - Pos(0, 0, 0) * Box(20.33, 8, 20)  # off-round → stresses rounding
        line = next(ln for ln in _script_for(part).splitlines() if ln.startswith("sheet.slot("))
        eval(line, {"sheet": Sheet(part)})  # declare.slot() must not raise

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
        assert (tmp_path / "gen.svg").exists()

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

    def test_sheet_style_warns_on_unsupported_flags(self, tmp_path):
        # the Sheet DSL can't embed --drawn-by/--tolerance/--scale/--page yet; the default
        # sheet path must WARN rather than silently drop them (else the flags are a no-op)
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
                "--scale",
                "2",
                "--out",
                str(tmp_path / "g"),
            ],
        )
        assert r.exit_code == 0, r.output
        assert "--drawn-by" in r.output and "--scale" in r.output
        assert "--style imperative" in r.output

    def test_sheet_style_silent_when_no_unsupported_flags(self, tmp_path):
        # no spurious warning when only supported flags are given
        from typer.testing import CliRunner

        from draftwright.cli import app

        step = tmp_path / "plate.step"
        export_step(_plate(), str(step))
        r = CliRunner().invoke(app, [str(step), "--script", "--out", str(tmp_path / "g")])
        assert r.exit_code == 0, r.output
        assert "warning:" not in r.output

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
        assert (tmp_path / "g.svg").exists()


def _named_signature(dwg):
    """The multiset of annotation TYPES on a drawing — the 'annotation signature' #472 uses
    to compare a generated-script drawing against a direct build (catches a dropped Centerline
    or an OD that fell from Dimension to Leader)."""
    return Counter(type(v).__name__ for v in dwg._named.values())


def _drawing_from_generated_script(step_path, tmp_path, monkeypatch):
    """Run the ACTUAL generated sheet script (STEP-seam form, self-runnable) and capture the
    Drawing it builds, by intercepting Sheet.export — the true end-to-end sheet-script path."""
    from draftwright import Sheet

    captured = {}
    monkeypatch.setattr(
        Sheet, "export", lambda self, stem=None: captured.setdefault("dwg", self.build())
    )
    py = generate_sheet_script(str(step_path), out=str(tmp_path / "gen"))
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
        assert _named_signature(scripted) == _named_signature(direct)

    def test_prismatic_plate_parity(self, tmp_path, monkeypatch):
        self._parity(_plate(), tmp_path, monkeypatch)

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
