"""draftwright.Part (build123d requirements API, in-memory haecceity PMI).

Needs build123d (optional); skipped without it. Run with the other Python tests:
python -m unittest discover -s tests/python -v
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    import build123d as bd
except ImportError:  # pragma: no cover
    bd = None

import draftwright

FACE = re.compile(r"ADVANCED_FACE\('([^']*)'")


def pdf_text(path):
    if shutil.which("pdftotext"):
        return subprocess.check_output(["pdftotext", str(path), "-"], text=True)
    from pypdf import PdfReader
    return "\n".join(page.extract_text() for page in PdfReader(path).pages)


def plate_parts():
    with bd.BuildPart() as plate:
        bd.Box(120, 80, 20)
        bore = bd.CounterBoreHole(radius=6, counter_bore_radius=10, counter_bore_depth=5)
        with bd.GridLocations(90, 50, 2, 2):
            mount = bd.Hole(radius=3.3)
    return plate, bore, mount


def build_plate():
    plate, bore, mount = plate_parts()
    p = draftwright.Part(plate.part, "plate", number="DWG-002", title="Mounting plate")
    p.process("machined").material("6082-T6").general_tolerance("ISO 2768-mK")
    A = p.datum("A", plate.faces().sort_by(bd.Axis.Z)[0])
    B = p.datum("B", plate.faces().sort_by(bd.Axis.X)[0])
    C = p.datum("C", plate.faces().sort_by(bd.Axis.Y)[0])
    p.hole(bore).fit("H7").finish(1.6)
    p.holes(mount).position(0.1, A, B, C)
    p.face(plate.faces().sort_by(bd.Axis.Z)[-1]).flatness(0.05)
    return plate, p


class OptionalImport(unittest.TestCase):
    def test_import_does_not_import_build123d(self):
        # Runs with or without build123d installed.
        code = "import sys, draftwright; assert 'build123d' not in sys.modules and 'OCP' not in sys.modules"
        subprocess.run([sys.executable, "-I", "-c", code], check=True)

    def test_part_without_build123d_is_not_needed_to_import(self):
        self.assertTrue(callable(draftwright.Part))


@unittest.skipIf(bd is None, "build123d is not installed")
class PartAPI(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def export(self, p, name="plate.step"):
        result = p.export_step(self.directory / name)
        return result, FACE.findall(result["step"].read_text())

    def test_build123d_export_step_does_not_name_faces(self):
        # The checked-before-building question: build123d's own exporter writes
        # labels for nodes only, so sub-shape names need the OCP writer in Part.
        plate, _, _ = plate_parts()
        for face in plate.faces():
            face.label = "f:plate.labelled"
        path = self.directory / "b123d.step"
        bd.export_step(plate.part, path)
        self.assertEqual(set(FACE.findall(path.read_text())), {""})

    def test_plate_step_names_and_in_memory_pmi(self):
        plate, p = build_plate()
        result, names = self.export(p)
        self.assertEqual(len(names), 13)
        named = [n for n in names if n]
        self.assertEqual(len(named), len(set(named)))
        self.assertEqual(sorted(named), sorted([
            "f:plate.datum_A.1", "f:plate.datum_B.1", "f:plate.datum_C.1", "f:plate.face1.1",
            "f:plate.hole1.1", "f:plate.hole1.2", "f:plate.hole1.3",
            "f:plate.hole2.1", "f:plate.hole2.2", "f:plate.hole2.3", "f:plate.hole2.4",
        ]))
        # Names sit on the faces they were asked for: A is the bottom, B the -X end,
        # C the -Y side, the flatness face the top, the fit the radius-6 bore.
        faces = list(plate.part.faces())
        self.assertEqual(len(faces), 13)
        by_name = {n: i for i, n in enumerate(names) if n}
        self.assertEqual(result["faces"], by_name)
        self.assertEqual(sorted(f.name for f in self.directory.iterdir()), ["plate.step"])
        # export_step is intentionally plain geometry. Rendering consumes the native PMI.
        from draftwright import _native
        analysis = json.loads(_native.inspect_step(str(result["step"])))
        self.assertNotIn("H7", json.dumps(analysis.get("pmi")))
        report = json.loads(p._geometry().inspect(p._pmi))
        records = report["records"]
        self.assertEqual({r["label"] for r in records if r["kind"] == "datum"}, {"A", "B", "C"})
        self.assertEqual([r["fit"] for r in records if r["fit"]], ["H7"])
        self.assertEqual([r["value"] for r in records if r["kind"] == "flatness"], [0.05])
        positions = [r for r in records if r["kind"] == "position"]
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["value"], 0.1)
        self.assertEqual(positions[0]["datum_refs"], ["A", "B", "C"])
        self.assertEqual(len(positions[0]["faces"]), 4)
        finish = [r for r in records if r["kind"] == "surface_finish"]
        self.assertEqual(len(finish), 1)
        self.assertIn("Ra 1.6", finish[0]["label"])
        self.assertEqual(len(finish[0]["faces"]), 1)
        fitted = [r for r in records if r["fit"] == "H7"][0]
        self.assertEqual(fitted["faces"], finish[0]["faces"])
        self.assertEqual(report["materials"][0]["designation"], "6082-T6")
        self.assertEqual([r["label"] for r in records if r["kind"] == "general_tolerances"], ["ISO 2768-mK"])
        self.assertIn("PROCESS: machined", [r["label"] for r in records])

    def test_names_are_deterministic(self):
        _, first = build_plate()
        _, second = build_plate()
        self.assertEqual(self.export(first, "one.step")[1], self.export(second, "two.step")[1])

    def test_loud_failures(self):
        plate, bore, mount = plate_parts()
        p = draftwright.Part(plate.part, "plate")
        with self.assertRaisesRegex(ValueError, "none of the faces"):
            p.face(bd.Box(1, 1, 1).moved(bd.Location((500, 0, 0))).faces()[0])
        with self.assertRaisesRegex(ValueError, "none of the faces"):
            p.face([])
        top = plate.faces().sort_by(bd.Axis.Z)[-1]
        stray = bd.Box(1, 1, 1).moved(bd.Location((500, 0, 0))).faces()[0]
        with self.assertRaisesRegex(ValueError, "1 of 2 faces given are not faces of the part"):
            p.face([top, stray])
        self.assertEqual(p._names, {})
        with self.assertRaisesRegex(ValueError, "separate holes; use holes"):
            p.hole(mount)
        with self.assertRaisesRegex(ValueError, "no cylindrical face"):
            p.hole(plate.faces().sort_by(bd.Axis.Z)[0])
        with self.assertRaisesRegex(ValueError, "not an ISO 286"):
            p.hole(bore).fit("loose")
        with self.assertRaisesRegex(ValueError, "shaft fit"):
            p.hole(bore, label="s").fit("g6")
        with self.assertRaisesRegex(ValueError, "fitted or threaded"):
            p.hole(bore, label="again").fit("H7").thread("M6")
        names = dict(p._names)
        with self.assertRaises(ValueError):          # a failed call names nothing
            p.hole(plate.faces().sort_by(bd.Axis.Z)[0], label="nothing")
        self.assertEqual(p._names, names)
        with self.assertRaisesRegex(ValueError, "letters are A to H"):
            p.datum("Z", plate.faces()[0])
        p.datum("A", plate.faces()[0], label="x")
        with self.assertRaisesRegex(ValueError, "already declared"):
            p.datum("A", plate.faces()[1])
        with self.assertRaisesRegex(ValueError, "not been declared"):
            p.hole(bore, label="b2").position(0.1, "B")
        # The same label on two groups would put one name on two faces.
        with self.assertRaisesRegex(ValueError, "already on face"):
            p.datum("B", plate.faces()[1], label="x")

    def test_offset_cutter_does_not_match(self):
        plate, bore, _ = plate_parts()
        p = draftwright.Part(plate.part, "plate")
        with self.assertRaisesRegex(ValueError, "none of the faces|no cylindrical"):
            p.hole(bore.moved(bd.Location((0.5, 0, 0))))
        self.assertEqual(len(p.hole(bore).faces), 3)  # control: the real cutter matches

    def test_boss_is_not_a_hole(self):
        with bd.BuildPart() as b:
            bd.Box(40, 40, 10)
            with bd.Locations((0, 0, 5)):
                boss = bd.Cylinder(5, 10, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
        p = draftwright.Part(b.part, "boss")
        with self.assertRaisesRegex(ValueError, "bosses or shafts"):
            p.hole(boss).fit("H7")

    def test_filleted_box_names_every_face(self):
        # Planes of a filleted box carry a TopLoc location; the XCAF sub-shape route lost their names.
        with bd.BuildPart() as b:
            bd.Box(30, 20, 10)
            bd.fillet(b.edges().filter_by(bd.Axis.Z), 2)
        for i, face in enumerate(b.part.faces()):
            p = draftwright.Part(b.part, "fil")
            p.face(face, label="q").flatness(0.1)
            result, names = self.export(p, f"fil{i}.step")
            self.assertEqual([n for n in names if n], ["f:fil.q.1"], face.geom_type)
            self.assertEqual(result["faces"]["f:fil.q.1"], names.index("f:fil.q.1"))

    def test_reference_keys_and_sections_accept_objects(self):
        plate, bore, _ = plate_parts()
        p = draftwright.Part(plate.part, "plate")
        ref = p.hole(bore)
        feature = p.feature_id(ref)
        self.assertTrue(feature.startswith("f:hole:"), feature)
        bottom = plate.faces().sort_by(bd.Axis.Z)[0]
        self.assertTrue(p.reference_key(bottom).startswith("face:v1:"))
        self.assertEqual(p.reference_key(bottom), p.reference_key(bottom))
        spec = p.drawing().section("A-A", through=ref).spec()
        self.assertEqual(spec["views"]["sections"][0]["through"], feature)
        with self.assertRaisesRegex(ValueError, "one feature"):
            p.feature_id(list(plate.faces()))


    def test_drawing_is_layout_only(self):
        _, p = build_plate()
        spec = (p.drawing(sheet="A3", scale="1:2").views(shaded=True, omit=["side"])
                .section("A-A", through="f:section_recess:f133").spec())
        self.assertEqual(spec, {
            "units": "mm",
            "sheet": {"size": "A3", "scale": "1:2",
                      "title": {"title": "Mounting plate", "number": "DWG-002"}},
            "views": {"iso": {"style": "shaded"}, "side": {"omit": True},
                      "sections": [{"id": "A", "through": "f:section_recess:f133"}]},
        })
        with self.assertRaises((ValueError, AttributeError)):
            p.drawing().section("B", through=object())

    def test_render(self):
        _, p = build_plate()
        svg, dxf = p.drawing(sheet="A3").views(shaded=True).render(self.directory / "plate")
        self.assertEqual(Path(svg), self.directory / "plate.svg")
        self.assertIn("<svg", Path(svg).read_text())
        self.assertTrue(Path(dxf).exists())

    def test_plain_proposal_plate_draws_its_requirements(self):
        _, p = build_plate()
        drawing = p.drawing(sheet="A3")
        svg, pdf = drawing.render(self.directory / "proposal", formats=("svg", "pdf"))
        text = Path(svg).read_text()
        for expected in ("H7", "Ra 1.6", "6082-T6", "ISO 2768-mK"):
            self.assertIn(expected, text)
        self.assertTrue(Path(pdf).read_bytes().startswith(b"%PDF-"))
        delivered = pdf_text(pdf)
        for expected in ("⌖", "⏥", "0.1", "0.05", "H7", "Ra 1.6"):
            self.assertIn(expected, delivered)
        self.assertFalse([problem for problem in drawing.problems if problem["code"] == "pmi_not_lowered"], drawing.problems)
        self.assertFalse((self.directory / "proposal.step").exists())
        self.assertEqual(list(self.directory.glob("*.json")), [])

    def test_layout_update_title_and_requirement_boundary(self):
        _, p = build_plate()
        drawing = p.drawing(sheet="A3").title(drawn_by="PA", date="2026-11-14", revision="B")
        drawing.update({"sheet": {"zones": False}, "views": {"iso": {"style": "shaded"}}})
        before = drawing.spec()
        self.assertEqual(before["sheet"]["title"]["title"], "Mounting plate")
        for patch in ({"process": "machined"}, {"sheet": {"title": {"material": "steel"}}},
                      {"features": {"f:hole:f1": {"fit": "H7"}}},
                      {"features": {"f:hole:f1": {"modelled_thread": {"designation": "M3"}}}},
                      {"dimensions": {"d:a": {"tolerance": "±0.1"}}}, {"notes": ["Test"]}):
            with self.assertRaisesRegex(draftwright.DraftwrightError, r"Part\."):
                drawing.update(patch)
            self.assertEqual(drawing.spec(), before)
        for patch in ({"sheet": {"unknown": True}}, {"sheet": {"title": {"date": 12}}},
                      {"views": {"iso": {"style": "bad"}}}):
            with self.assertRaises(draftwright.DraftwrightError):
                drawing.update(patch)
        copy = drawing.spec()
        copy["sheet"]["title"]["title"] = "Changed"
        self.assertEqual(drawing.spec(), before)

    def test_shank_requirements_reach_svg_pdf_without_specify(self):
        # A turned body with separate axial H7 and radial tapped bores, as #459.
        with bd.BuildPart() as shank:
            bd.Cylinder(10, 40)
            bd.Cylinder(4, 40, mode=bd.Mode.SUBTRACT)
            with bd.Locations((0, 0, 10)):
                bd.Cylinder(1.25, 24, rotation=(90, 0, 0), mode=bd.Mode.SUBTRACT)
        p = draftwright.Part(shank.part, "shank", number="GRM-01", title="SHANK")
        p.material("BRASS H62 (CuZn40)").process("machined").general_tolerance("±0.05")
        p.datum("A", shank.faces().sort_by(bd.Axis.Z)[0])
        bores = [f for f in shank.faces().filter_by(bd.GeomType.CYLINDER)
                 if p._is_hole_wall(f)]
        p.hole([f for f in bores if abs(f.radius - 4) < 1e-4]).fit("H7").finish(1.6)
        p.holes([f for f in bores if abs(f.radius - 1.25) < 1e-4]).thread("M3x0.5-6H")
        exported = p.export_step(self.directory / "GRM-01.step")
        original = exported["step"].read_bytes()
        pmi = json.loads(p._geometry().inspect(p._pmi))
        self.assertEqual([r["label"] for r in pmi["records"] if r["kind"] == "datum"], ["A"])
        self.assertEqual([r["kind"] for r in pmi["records"] if r["kind"] == "internal_thread"], ["internal_thread"])
        self.assertNotIn("specify_core", sys.modules)

        drawing = p.drawing(sheet="A3").title(drawn_by="PA", revision="C")
        outputs = drawing.render(self.directory / "GRM-01", formats=("svg", "pdf"),
                                 spec={"sheet": {"zones": False, "title": {"date": "2026-11-14"}}})
        svg = Path(outputs[0]).read_text()
        for text in ("H7", "M3", "Ra 1.6", "BRASS H62", "±0.05", "PA", "2026-11-14"):
            self.assertIn(text, svg)
        text_nodes = list(ET.fromstring(svg).iter("{http://www.w3.org/2000/svg}text"))
        self.assertIn("A", [node.text for node in text_nodes])
        title_text = " ".join(node.text or "" for node in text_nodes if node.attrib.get("font-family") == "sans-serif")
        self.assertIn("BRASS H62 (CuZn40)", title_text)
        self.assertIn("±0.05", title_text)
        from draftwright import _native
        analysis = json.loads(_native.inspect_step(str(exported["step"])))
        self.assertEqual(analysis["product_name"], "SHANK")
        self.assertEqual(exported["step"].read_bytes(), original)
        self.assertIsInstance(drawing.problems, list)
        self.assertTrue(Path(outputs[1]).read_bytes().startswith(b"%PDF-"))
        # Verify the deliverable through an independent PDF text reader.
        delivered = pdf_text(outputs[1])
        for text in ("H7", "M3", "Ra", "1.6", "BRASS", "±0.05"):
            self.assertIn(text, delivered)
        self.assertIn("A", [line.strip() for line in delivered.splitlines()])
        self.assertFalse([problem for problem in drawing.problems if problem["code"] == "pmi_not_lowered"], drawing.problems)
        # The one-render patch does not mutate the builder.
        self.assertNotIn("date", drawing.spec()["sheet"]["title"])
        self.assertNotIn("material", drawing.spec()["sheet"]["title"])
        self.assertNotIn("process", drawing.spec())

    def test_formats_and_multiple_sheets_one_pdf(self):
        _, p = build_plate()
        drawing = p.drawing(sheet="A3").update({"sheets": {"max": 2}, "views": {"iso": {"sheet": 2}}})
        paths = drawing.render(self.directory / "multi", formats=("svg", "pdf", "png", "dxf"))
        pdfs = [Path(path) for path in paths if path.endswith(".pdf")]
        self.assertEqual(len(pdfs), 1)
        pdf = pdfs[0].read_bytes()
        self.assertIn(b"/Count 2", pdf)
        self.assertEqual(len([p for p in paths if p.endswith(".svg")]), 2)
        self.assertEqual(len([p for p in paths if p.endswith(".png")]), 2)
        self.assertEqual(len([p for p in paths if p.endswith(".dxf")]), 2)
        for formats in (("bad",), (), ("svg", "svg")):
            with self.assertRaises(ValueError):
                drawing.render(self.directory / "bad", formats=formats)
        with self.assertRaises(TypeError):
            drawing.render(self.directory / "bad", formats="pdf")


if __name__ == "__main__":
    unittest.main()
