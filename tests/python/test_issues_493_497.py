"""Maintainer regressions from ISSUES.md, including the original tester geometry."""
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

try:
    import build123d as bd
except ModuleNotFoundError as error:
    if error.name != "build123d":
        raise
    bd = None
from draftwright import Part, Pack, DraftwrightError, make_drawing


@unittest.skipIf(bd is None, "build123d is optional")
class PythonIssues(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def records(self, part):
        return json.loads(part._geometry().inspect(part._pmi))["records"]

    def render(self, part, name="drawing"):
        drawing = part.drawing()
        paths = drawing.render(self.directory / name, formats=("svg", "pdf"))
        svg = ET.parse(paths[0])
        text = " ".join(svg.getroot().itertext())
        from pypdf import PdfReader
        pdf = " ".join(p.extract_text() for p in PdfReader(paths[1]).pages)
        return drawing, text, pdf

    def test_493_tester_updates_name_the_new_calls(self):
        p = Part(bd.Box(30, 20, 12), "block").material("Brass")
        d = p.drawing()
        for spec, call in (({"notes": [{"id": "n:1", "text": "DEBURR"}]}, "Part.note()"),
                           ({"sheet": {"default_finish": {"ra": 3.2}}}, "Part.default_finish()")):
            with self.assertRaises(DraftwrightError) as error:
                d.update(spec)
            self.assertIn(call, str(error.exception))

    def test_493_notes_order_default_finish_and_native_records(self):
        p = Part(bd.Box(30, 20, 12), "block").material("Brass")
        self.assertIs(p.note("Visible surfaces: clean satin finish, no lacquer."), p)
        p.note("Harden and temper to 60 HRC approx.").note("DEBURR")
        p.default_finish(6.3).default_finish(3.2)
        records = self.records(p)
        notes = [r for r in records if r["kind"] == "general_note"]
        self.assertEqual([r["label"] for r in notes], [
            "Visible surfaces: clean satin finish, no lacquer.",
            "Harden and temper to 60 HRC approx.", "DEBURR"])
        textures = [r for r in records if r["kind"] == "surface_texture"]
        self.assertEqual(len(textures), 1)
        self.assertFalse(textures[0]["faces"])
        self.assertIn("Ra 3.2", textures[0]["label"])
        _, svg, pdf = self.render(p)
        for text in (svg, pdf):
            self.assertLess(text.index("Visible surfaces"), text.index("Harden and temper"))
            self.assertLess(text.index("Harden and temper"), text.index("DEBURR"))
            self.assertIn("3.2", text)
        for invalid in ("", "  ", None, 42):
            with self.assertRaises(ValueError):
                p.note(invalid)

    def test_493_angle_uses_existing_pmi_angular_renderer(self):
        # A triangular blade section with a 25 degree bevel, extruded along Y.
        profile = bd.Plane.XZ * bd.Polygon((0, 0), (30, 0), (0, 30 * math.tan(math.radians(25))), align=None)
        shape = bd.extrude(profile, amount=8)
        p = Part(shape, "blade")
        faces = list(shape.faces())
        bottom = min(faces, key=lambda f: f.center().Z)
        bevel = max((f for f in faces if abs(f.normal_at().Z) > 0.5), key=lambda f: f.center().Z)
        self.assertIs(p.angle(bottom, bevel), p)
        record = next(r for r in self.records(p) if r["kind"] == "angular")
        self.assertAlmostEqual(record["value"], 25, places=5)
        self.assertEqual(len(record["face_groups"]), 2)
        drawing, svg, pdf = self.render(p, "angle")
        self.assertIn("25", svg)
        self.assertIn("25", pdf)
        self.assertFalse([p for p in drawing.problems if p["code"] in ("pmi_not_lowered", "pmi_not_rendered")], drawing.problems)
        with self.assertRaises(DraftwrightError):
            p.angle(bottom, bottom)
        other_shape = bd.Box(1, 2, 3)
        other = Part(other_shape)
        with self.assertRaises(ValueError):
            p.angle(bottom, other.face(other_shape.faces()[0]))

    def test_494_tester_blind_hole_thread_depth_and_drill_line(self):
        tap = bd.Pos(0, 0, 2) * bd.Cylinder(2.5, 8)
        p = Part(bd.Box(30, 20, 12) - tap, "block")
        p.hole(tap).thread("M6", depth=6)
        record = next(r for r in self.records(p) if r["kind"] == "internal_thread")
        self.assertIn(["minimum full thread", 6.0], record["fields"])
        _, svg, pdf = self.render(p, "thread")
        for text in (svg, pdf):
            self.assertIn("M6-6H ↧ 6", text)
            self.assertIn("⌀5 ↧ 8", text)

    def test_494_counterbored_66mm_bore_is_not_tapped_through(self):
        tap = bd.Cylinder(2.5, 66)
        counterbore = bd.Pos(0, 0, 30.25) * bd.Cylinder(3.175, 5.5)
        p = Part(bd.Box(30, 20, 66) - tap - counterbore, "long bore")
        p.hole(tap + counterbore).thread("M6", depth=12)
        _, svg, pdf = self.render(p, "counterbore")
        for text in (svg, pdf):
            self.assertIn("M6-6H ↧ 12", text)
            self.assertNotIn("M6-6H THRU", text)
            self.assertIn("6.35", text)
            self.assertIn("5.5", text)

    def test_494_depth_refusals_are_atomic_and_omitted_keeps_full_depth(self):
        tap = bd.Pos(0, 0, 2) * bd.Cylinder(2.5, 8)
        p = Part(bd.Box(30, 20, 12) - tap)
        hole = p.hole(tap).thread("M6")
        before = self.records(p)
        for bad in (9, 0, -1, math.inf, math.nan, "min 6", True):
            with self.subTest(depth=bad), self.assertRaises(ValueError):
                hole.thread("M6", depth=bad)
            self.assertEqual(self.records(p), before)
        _, svg, _ = self.render(p)
        self.assertIn("M6-6H", svg)
        self.assertNotIn("M6-6H ↧", svg)
        shaft = bd.Cylinder(3, 8)
        with self.assertRaisesRegex(ValueError, "external thread takes no depth"):
            Part(shaft).shaft(shaft).thread("M6", depth=6)

    def test_495_tester_knob_omitted_teeth_and_mismatch_finding(self):
        knob = bd.Pos(0, 0, 12) * bd.Cylinder(5, 12)
        p = Part(bd.Box(30, 20, 12) + knob, "block")
        ref = p.shaft(knob).knurl(0.8)
        drawing, svg, _ = self.render(p, "derived")
        self.assertIn("STRAIGHT KNURL P0.8", svg)
        self.assertIn("MAX AFTER KNURL", svg)
        self.assertFalse([f for f in drawing.problems if f["code"] == "knurl_tooth_count"])
        ref.knurl(0.8, teeth=39)
        _, explicit, _ = self.render(p, "explicit")
        self.assertEqual(svg, explicit)
        for teeth, mismatch in ((40, True), (2, True), (39, False)):
            ref.knurl(0.8, teeth=teeth)
            drawing, text, _ = self.render(p, f"teeth-{teeth}")
            findings = [f for f in drawing.problems if f["code"] == "knurl_tooth_count"]
            self.assertEqual(bool(findings), mismatch)
            self.assertTrue(all(f["severity"] != "error" for f in findings))
            self.assertIn("STRAIGHT KNURL P0.8", text)
        for invalid in (0, -1, 3.2, True):
            with self.assertRaises(ValueError):
                ref.knurl(0.8, teeth=invalid)

    def test_496_orientation_findings_and_supplied_parts_are_stated_make(self):
        # Two made parts, including an undimensioned slot which has sheet problems.
        slot = bd.Pos(7, 0, 0) * bd.Box(4.75, 12, 10)
        first = Part(bd.Box(30, 20, 6) - slot, "GRM-01 shank")
        original_faces = list(first.shape.faces())
        second = Part(bd.Pos(0, 0, 20) * bd.Box(15, 12, 3), "lid")
        pack = Pack([first, second], horizontal_axis="x")
        pack.render(self.directory / "pack", formats=("pdf", "json"))
        self.assertTrue(all(a.wrapped.IsSame(b.wrapped)
                            for a, b in zip(original_faces, first.shape.faces())))
        self.assertFalse([f for f in pack.findings if f["code"] == "pack_make_guess"])
        self.assertEqual(pack.report["general_assembly"]["frame"][0][:3], [1, 0, 0])
        count = 0
        for row in pack.rows:
            self.assertNotIn("error", row)
            for problem in row["problems"]:
                count += 1
                self.assertTrue(any(f.get("part") == row["name"] and f["code"] == problem["code"]
                                    and f["severity"] == problem["severity"] and problem["message"] in f["message"]
                                    for f in pack.findings), problem)
        self.assertGreater(count, 0)
        self.assertEqual(pack.findings, json.loads((self.directory / "pack.json").read_text())["findings"])
        for axis in ("y", "z", None):
            Pack([first, second], horizontal_axis=axis)
        for axis in ("X", "xy", 1, []):
            with self.assertRaises(ValueError):
                Pack([first], horizontal_axis=axis)

    def test_497_pack_exports_to_a_removed_temporary_file(self):
        shape = bd.Box(30, 20, 12)
        shape.label = "block"
        assembly = bd.Compound(children=[shape])
        pack = Pack(assembly, parts=[Part(shape, "block")])
        original = bd.export_step
        paths = []
        def export(shape, path):
            self.assertIsInstance(path, Path)
            paths.append(path)
            self.assertTrue(path.parent.is_dir())
            return original(shape, path)
        with patch.object(bd, "export_step", side_effect=export):
            pack.render(self.directory / "compat", formats=("pdf", "json"))
        self.assertEqual(len(paths), 1)
        self.assertFalse(paths[0].exists())
        self.assertFalse(paths[0].parent.exists())
        self.assertNotIn("error", pack.rows[0])
        self.assertEqual(assembly.label, "")

    def test_497_temporary_file_removed_on_export_failure(self):
        shape = bd.Box(3, 2, 1)
        shape.label = "block"
        assembly = bd.Compound(children=[shape])
        paths = []
        def fail(shape, path):
            paths.append(path)
            path.write_bytes(b"partial")
            raise RuntimeError("export failed")
        with patch.object(bd, "export_step", side_effect=fail):
            with self.assertRaisesRegex(DraftwrightError, "export failed"):
                Pack(assembly)._step()
        self.assertFalse(paths[0].parent.exists())
        self.assertEqual(assembly.label, "")

    def test_497_old_version_names_required_and_found_before_use(self):
        with patch("draftwright._build123d.version", return_value="0.9.1"):
            shape = bd.Box(3, 2, 1)
            for use in (lambda: Part(shape), lambda: Pack(bd.Compound(children=[shape])),
                        lambda: make_drawing(shape, out=self.directory / "old")):
                with self.assertRaisesRegex(DraftwrightError, r"build123d>=0\.10.*found build123d 0\.9\.1"):
                    use()

    def test_497_build123d_extra_declares_the_minimum(self):
        from importlib.metadata import requires
        requirements = requires("draftwright") or []
        self.assertTrue(any("build123d>=0.10" in requirement.replace(" ", "")
                            and "extra" in requirement for requirement in requirements), requirements)
