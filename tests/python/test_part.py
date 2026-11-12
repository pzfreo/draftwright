"""draftwright.Part (build123d requirements API, Python half).

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
from pathlib import Path

try:
    import build123d as bd
except ImportError:  # pragma: no cover
    bd = None

import draftwright

FACE = re.compile(r"ADVANCED_FACE\('([^']*)'")


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

    def test_plate_step_names_and_answers(self):
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
        answers = json.loads(result["answers"].read_text())
        by_name = {n: i for i, n in enumerate(names) if n}
        self.assertEqual(answers["datum.A"], [by_name["f:plate.datum_A.1"]])
        self.assertEqual(answers["datum.B"], [by_name["f:plate.datum_B.1"]])
        self.assertEqual(answers["datum.C"], [by_name["f:plate.datum_C.1"]])
        self.assertEqual(answers["extra.face1.flatness"],
                         {"faces": [by_name["f:plate.face1.1"]], "kind": "flatness", "value": "0.05"})
        bore = answers["extra.hole1.fit"]
        self.assertEqual(bore, {"faces": bore["faces"], "kind": "fit", "value": "H7"})
        self.assertEqual(len(bore["faces"]), 1)  # the Ø12 bore, not the Ø20 counterbore
        self.assertEqual(answers["extra.hole1.finish"],
                         {"faces": bore["faces"], "kind": "finish", "value": "Ra 1.6"})
        self.assertEqual({k: v for k, v in answers.items() if k.startswith("part.")},
                         {"part.material": "6082-T6", "part.process": "machined",
                          "part.general_tolerance": "ISO 2768-mK"})
        self.assertEqual(sorted(answers), sorted([
            "datum.A", "datum.B", "datum.C", "part.material", "part.process",
            "part.general_tolerance", "extra.face1.flatness", "extra.hole1.fit",
            "extra.hole1.finish"]))
        self.assertEqual(result["answers"].name, "plate.answers.json")
        # Position intent waits for Specify's feature ids (Rust half): written by name.
        pending = json.loads(result["pending"].read_text())["requirements"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["answer"], "hole.position")
        self.assertEqual(pending[0]["datums"], ["A", "B", "C"])
        self.assertEqual(len(pending[0]["faces"]), 4)

    def test_names_are_deterministic(self):
        _, first = build_plate()
        _, second = build_plate()
        self.assertEqual(self.export(first, "one.step")[1], self.export(second, "two.step")[1])

    def test_specify_cli_reads_the_answers_on_the_named_faces(self):
        cli = shutil.which("specify-core")
        if cli is None:
            self.skipTest("specify-core is not installed")
        _, p = build_plate()
        result, names = self.export(p)
        analysis = self.directory / "a.json"
        subprocess.run([cli, "analyse", str(result["step"]), "-o", str(analysis)],
                       check=True, capture_output=True)
        faces = json.loads(analysis.read_text())["faces"]
        self.assertEqual([f.get("name") for f in faces], [n or None for n in names])
        answers = json.loads(result["answers"].read_text())
        out = subprocess.run(
            [cli, "write", str(result["step"]), str(analysis), str(result["answers"]),
             "-o", str(self.directory / "pmi.step"), "--accept-defaults"],
            check=True, capture_output=True, text=True)
        written = json.loads(out.stdout)["written"]
        self.assertIn("flatness 0.05 on added", written)
        self.assertIn("Ø12 H7 on holes:8.11", written)
        self.assertIn("material 6082-T6", written)

    def test_loud_failures(self):
        plate, bore, mount = plate_parts()
        p = draftwright.Part(plate.part, "plate")
        with self.assertRaisesRegex(ValueError, "none of the faces"):
            p.face(bd.Box(1, 1, 1).moved(bd.Location((500, 0, 0))).faces()[0])
        with self.assertRaisesRegex(ValueError, "none of the faces"):
            p.face([])
        with self.assertRaisesRegex(ValueError, "separate holes; use holes"):
            p.hole(mount)
        with self.assertRaisesRegex(ValueError, "no cylindrical face"):
            p.hole(plate.faces().sort_by(bd.Axis.Z)[0])
        with self.assertRaisesRegex(ValueError, "not an ISO 286"):
            p.hole(bore).fit("loose")
        with self.assertRaisesRegex(ValueError, "fitted or threaded"):
            p.hole(bore, label="again").fit("H7").thread("M6")
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

    def test_check_and_answer_wait_for_the_rust_half(self):
        _, p = build_plate()
        for call in (p.check, lambda: p.answer("part.material", "x")):
            with self.assertRaisesRegex(NotImplementedError, "0.6.0-alpha.2"):
                call()

    def test_import_does_not_import_build123d(self):
        code = "import sys, draftwright; assert 'build123d' not in sys.modules and 'OCP' not in sys.modules"
        subprocess.run([sys.executable, "-I", "-c", code], check=True)

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
        with self.assertRaises(NotImplementedError):
            p.drawing().section("B", through=object())

    def test_render(self):
        _, p = build_plate()
        svg, dxf = p.drawing(sheet="A3").views(shaded=True).render(self.directory / "plate")
        self.assertEqual(Path(svg), self.directory / "plate.svg")
        self.assertIn("<svg", Path(svg).read_text())
        self.assertTrue(Path(dxf).exists())


if __name__ == "__main__":
    unittest.main()
