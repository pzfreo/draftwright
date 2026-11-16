"""Real-pack copy precision, angular handles and current-render diagnostics."""
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import draftwright

try:
    import build123d as bd
except ModuleNotFoundError as error:
    if error.name != "build123d":
        raise
    bd = None


@unittest.skipIf(bd is None, "build123d is optional")
class ReviewRound4(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def wedge(self, degrees=25):
        profile = bd.Plane.XZ * bd.Polygon(
            (0, 0), (30, 0), (0, 30 * math.tan(math.radians(degrees))), align=None)
        shape = bd.extrude(profile, amount=8)
        faces = list(shape.faces())
        bottom = min(faces, key=lambda f: f.center().Z)
        bevel = max((f for f in faces if abs(f.normal_at().Z) > 0.5), key=lambda f: f.center().Z)
        part = draftwright.Part(shape, "wedge").general_tolerance("ISO 2768-m")
        return part, bottom, bevel

    def render(self, drawing, name):
        paths = drawing.render(self.directory / name, formats=("svg", "pdf"))
        from pypdf import PdfReader
        svg = " ".join(ET.parse(paths[0]).getroot().itertext())
        pdf = " ".join(page.extract_text() for page in PdfReader(paths[1]).pages)
        return svg, pdf

    def assert_clear(self, drawing):
        for problem in drawing.problems:
            self.assertFalse(any(word in problem["code"] for word in
                                 ("overlap", "crossing", "through", "off_sheet", "not_lowered", "not_rendered"))
                             or problem["code"] in ("annotation_over_view", "label_crowded"), drawing.problems)

    def test_translated_end_origin_x_parts_keep_exact_reference_binding(self):
        blank = bd.Pos(15, 0, 0) * bd.Rot(0, 90, 0) * bd.Cylinder(4, 30)
        # The flat produces end vertices about 7e-16 mm from X=0. Copying the
        # placed body through world coordinates rounds those to zero.
        shaft = (bd.Part() + blank) - bd.Pos(15, 0, -5.4) * bd.Box(40, 20, 4)
        disc = bd.Pos(1, 0, 0) * bd.Rot(0, 90, 0) * bd.Cylinder(6, 2)
        stem = bd.Pos(5, 0, 0) * bd.Rot(0, 90, 0) * bd.Cylinder(2, 8)
        wheel = (disc + stem) - bd.Pos(1, 0, 0) * bd.Rot(0, 90, 0) * bd.Cylinder(1, 4)
        parts = [draftwright.Part(shape, name, number=name)
                 for shape, name in ((shaft, "shaft"), (wheel, "wheel"))]
        leaves = [bd.Pos(-18.8, 0, 66 + i * 12.5) * part.shape
                  for i, part in enumerate(parts)]
        for leaf, part in zip(leaves, parts):
            leaf.label = part.name
        originals = [list(part.shape.faces()) for part in parts]
        pack = draftwright.Pack(bd.Compound(children=leaves), parts=parts)
        pack.render(self.directory / "blank", formats=("pdf", "json"))
        self.assertEqual(pack.report["pages"], 3)
        self.assertFalse([r for r in pack.rows if r.get("error")], pack.rows)
        self.assertTrue(all(r["sheets"] for r in pack.rows))
        # Live PMI uses the same exact frame; no loose geometric comparison.
        parts[0].material("Brass").shaft(blank).fit("g6")
        parts[1].material("Steel").shaft(stem).thread("M4")
        pack.render(self.directory / "authored", formats=("pdf", "json"))
        self.assertEqual(pack.report["pages"], 3)
        self.assertFalse([r for r in pack.rows if r.get("error")], pack.rows)
        self.assertEqual({r["material"] for r in pack.rows}, {"Brass", "Steel"})
        for part, before in zip(parts, originals):
            self.assertTrue(all(a.wrapped.IsSame(b.wrapped)
                                for a, b in zip(before, part.shape.faces())))

    def test_angle_handles_print_degrees_and_keep_text_clear(self):
        for degrees in (25, 25.5):
            with self.subTest(degrees=degrees):
                part, bottom, bevel = self.wedge(degrees)
                handle = part.angle(bottom, bevel)
                self.assertEqual(handle.nominal, degrees)
                drawing = part.drawing()
                svg, pdf = self.render(drawing, str(degrees))
                for text in (svg, pdf):
                    self.assertIn(f"{degrees}°", text)
                    self.assertNotIn(f"{degrees:.3f}°", text)
                self.assert_clear(drawing)
                self.assertIs(handle.tolerance(plus_minus=0.5), handle)
                svg, pdf = self.render(drawing, f"{degrees}-deviations")
                self.assert_clear(drawing)
                for text in (svg, pdf):
                    self.assertIn(f"{degrees}°", text)
                    self.assertIn("±0.5°", text)
                self.assertIs(handle.tolerance(limits=(degrees - 0.5, degrees + 0.5)), handle)
                svg, pdf = self.render(drawing, f"{degrees}-limits")
                self.assert_clear(drawing)
                for text in (svg, pdf):
                    self.assertIn(f"{degrees - 0.5:g}°", text)
                    self.assertIn(f"{degrees + 0.5:g}°", text)
                before = part._pmi.snapshot()
                for kwargs in ({}, {"plus_minus": 0}, {"plus_minus": -1}, {"plus_minus": math.inf},
                               {"limits": (degrees + 1, degrees - 1)}, {"limits": (0, degrees - 1)},
                               {"plus_minus": 0.5, "limits": (degrees - 1, degrees + 1)}):
                    with self.assertRaises(ValueError):
                        handle.tolerance(**kwargs)
                    self.assertTrue(part._pmi.same(before))

    def test_findings_require_a_current_successful_render(self):
        part, bottom, bevel = self.wedge()
        handle = part.angle(bottom, bevel)
        drawing = part.drawing()
        with self.assertRaisesRegex(draftwright.DraftwrightError, "render"):
            _ = drawing.problems
        self.render(drawing, "first")
        self.assertIs(drawing.problems, drawing.problems)
        for edit in (lambda: part.note("DEBURR"),
                     lambda: handle.tolerance(plus_minus=0.5),
                     lambda: drawing.title(revision="B")):
            edit()
            with self.assertRaisesRegex(draftwright.DraftwrightError, "render again"):
                _ = drawing.problems
            self.render(drawing, "edited")
            self.assertIsInstance(drawing.problems, list)
        with self.assertRaises(ValueError):
            handle.tolerance(plus_minus=-1)
        self.assertIsInstance(drawing.problems, list)  # refused edits are atomic

    def test_findings_snapshot_is_the_model_actually_rendered(self):
        for kind, token in (("pmi", "LATE NOTE"), ("layout", "LATE AUTHOR")):
            with self.subTest(kind=kind):
                part, bottom, bevel = self.wedge()
                part.angle(bottom, bevel)
                geometry = part._geometry()
                drawing = part.drawing()

                class RenderThenEdit:
                    def render(self, *args):
                        result = geometry.render(*args)
                        if kind == "pmi":
                            part.note(token)
                        else:
                            drawing.title(drawn_by=token)
                        return result

                with patch.object(part, "_geometry", return_value=RenderThenEdit()):
                    svg, _ = self.render(drawing, "before-edit")
                self.assertNotIn(token, svg)
                with self.assertRaisesRegex(draftwright.DraftwrightError, "render again"):
                    _ = drawing.problems
                svg, _ = self.render(drawing, "after-edit")
                self.assertIn(token, svg)
                self.assertIsInstance(drawing.problems, list)


if __name__ == "__main__":
    unittest.main()
