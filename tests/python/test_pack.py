"""Python packs: executed documentation, native PMI parity and validation."""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import draftwright

try:
    import build123d as bd
except ImportError:
    bd = None

ROOT = Path(__file__).resolve().parents[2]


def example():
    section = (ROOT / "docs/python.md").read_text().split("## Assembly drawing packs", 1)[1]
    return re.search(r"```python\n(.*?)```", section, re.S)[1]


@contextlib.contextmanager
def cwd(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class OptionalPack(unittest.TestCase):
    def test_step_only_does_not_import_build123d(self):
        fixture = ROOT / "tests/fixtures/assembly/pack-small.step.gz"
        with tempfile.TemporaryDirectory() as directory:
            code = ("import sys; from draftwright import Pack; "
                    f"p=Pack({str(fixture)!r}); p.title(drawn_by='PA'); p.part('plate').title(revision='D'); "
                    f"p.render({str(Path(directory) / 'pack')!r},formats=('json',)); "
                    "assert p.rows and 'build123d' not in sys.modules and 'OCP' not in sys.modules")
            subprocess.run([sys.executable, "-I", "-c", code], check=True)

    def test_invalid_arguments_at_call(self):
        for bad in (None, 7, object()):
            with self.assertRaises(TypeError):
                draftwright.Pack(bad)
        with self.assertRaises(ValueError):
            draftwright.Pack([])
        p = draftwright.Pack("missing.step")
        for formats, error in (("pdf", TypeError), ((), ValueError), (("svg",), ValueError), (("pdf", "pdf"), ValueError)):
            with self.assertRaises(error):
                p.render(formats=formats)
        for field in ("title", "number", "sheet"):
            with self.assertRaises(ValueError):
                p.title(**{field: "per sheet"})
        with self.assertRaises(TypeError):
            p.title(date=2026)
        with self.assertRaises(draftwright.DraftwrightError):
            p.title(bogus="field")
        with self.assertRaises(draftwright.DraftwrightError):
            p.render()


@unittest.skipIf(bd is None, "build123d is not installed")
class PackAPI(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def documented(self):
        namespace = {}
        with cwd(self.directory), contextlib.redirect_stdout(io.StringIO()):
            exec(compile(example(), "docs/python.md:Pack", "exec"), namespace)
        return namespace

    def test_documented_example_title_defaults_reports_and_pmi_parity(self):
        from pypdf import PdfReader
        ns = self.documented()
        pack, base = ns["pack"], ns["base"]
        pdf = PdfReader(self.directory / "enclosure.pdf")
        self.assertEqual(len(pdf.pages), 3)
        self.assertEqual(pack.report["pages"], 3)
        self.assertEqual(len(pack.rows), 2)
        self.assertTrue(all(not row.get("error") for row in pack.rows), pack.rows)
        self.assertEqual({row["part_number"] for row in pack.rows}, {"DWG-101", "DWG-102"})
        report = json.loads((self.directory / "enclosure.json").read_text())
        self.assertEqual(pack.rows, report["rows"])
        self.assertEqual(pack.report, report)
        self.assertEqual(pack.findings[:len(report["findings"])], report["findings"])
        self.assertEqual(len(pack.findings), len(report["findings"]) +
                         sum(len(row["problems"]) for row in report["rows"]))
        self.assertEqual(len((self.directory / "enclosure.csv").read_text().splitlines()), 3)
        for i, page in enumerate(pdf.pages):
            text = page.extract_text()
            for value in ("PA", "2026-11-15", "B" if i < 2 else "C"):
                self.assertIn(value, text)
        base_row = next(r for r in pack.rows if r["key"] == "base")
        single = base.drawing().title(drawn_by="PA", date="2026-11-15", revision="B")
        single.render(self.directory / "base", formats=("pdf",))
        self.assertEqual(sorted(p["code"] for p in base_row["problems"]),
                         sorted(p["code"] for p in single.problems))
        packed_text = pdf.pages[base_row["sheets"][0] - 1].extract_text()
        single_text = PdfReader(self.directory / "base.pdf").pages[0].extract_text()
        # Same annotation text, including the fit, datum and position frame.
        for token in ("H7", "A", "0.1", "6082-T6", "ISO 2768-mK"):
            self.assertIn(token, packed_text)
            self.assertEqual(packed_text.count(token), single_text.count(token), token)
        # Part exports and render intermediates are entirely in memory.
        self.assertEqual({p.suffix for p in self.directory.iterdir()}, {".pdf", ".csv", ".json"})
        with patch.object(base, "export_step", side_effect=AssertionError("no STEP files")), \
                patch.object(bd, "export_step", side_effect=AssertionError("assembly geometry is cached")):
            pack.render(self.directory / "again", formats=("json",))
        self.assertEqual(pack.rows, report["rows"])
        with patch.object(bd, "export_step", side_effect=AssertionError("PMI edits do not re-export geometry")):
            base.general_tolerance("±0.07")
            pack.render(self.directory / "live", formats=("pdf", "json"))
        live_page = PdfReader(self.directory / "live.pdf").pages[base_row["sheets"][0] - 1]
        self.assertIn("±0.07", live_page.extract_text())

    def test_list_and_step_path_use_the_same_products(self):
        ns = self.documented()
        parts = [ns["base"], ns["lid"]]
        for assembly in (parts, ns["assembly"]):
            pack = draftwright.Pack(assembly, **({} if isinstance(assembly, list) else {"parts": parts}))
            pack.render(self.directory / "variant", formats=("json",))
            self.assertEqual({r["key"] for r in pack.rows}, {"base", "lid"})
            self.assertFalse([r for r in pack.rows if r.get("error")], pack.rows)
        step = self.directory / "assembly.step"
        bd.export_step(ns["assembly"], step)
        pack = draftwright.Pack(step, parts=parts)
        pack.render(self.directory / "step", formats=("json",))
        self.assertFalse([r for r in pack.rows if r.get("error")], pack.rows)
        self.assertEqual(sorted(p["code"] for p in pack.rows[0]["problems"]),
                         sorted(p["code"] for p in ns["pack"].rows[0]["problems"]))

    def test_located_child_keeps_the_same_face_bound_pmi(self):
        from pypdf import PdfReader
        ns = self.documented()
        child = ns["base_shape"].moved(bd.Location((80, 0, 20), (0, 0, 90)))
        assembly = bd.Compound(children=[child, ns["lid_shape"]])
        pack = draftwright.Pack(assembly, parts=[ns["base"], ns["lid"]])
        pack.render(self.directory / "located", formats=("pdf", "json"))
        base = next(r for r in pack.rows if r["key"] == "base")
        original = next(r for r in ns["pack"].rows if r["key"] == "base")
        self.assertNotIn("error", base)
        self.assertEqual([p["code"] for p in base["problems"]],
                         [p["code"] for p in original["problems"]])
        page = PdfReader(self.directory / "located.pdf").pages[base["sheets"][0] - 1]
        for token in ("H7", "A", "0.1"):
            self.assertIn(token, page.extract_text())

    def test_real_product_names_in_compound_step_and_list_modes(self):
        from pypdf import PdfReader
        parts = []
        children = []
        for name, radius in ((" push rod ", 3), ("GRM-01 shank", 4)):
            shape = bd.Cylinder(radius, 12)
            shape.label = name
            part = draftwright.Part(shape, name)
            part.shaft(shape).fit("h6")
            parts.append(part)
            children.append(shape)
        self.assertEqual([p.name for p in parts], ["push rod", "GRM-01 shank"])
        assembly = bd.Compound(children=children)
        step = self.directory / "named.step"
        bd.export_step(assembly, step)
        for mode, source in (("compound", assembly), ("step", step), ("list", parts)):
            with self.subTest(mode=mode):
                pack = draftwright.Pack(source, **({} if mode == "list" else {"parts": parts}))
                pack.part(" push rod ").title(revision="N")
                out = self.directory / mode
                pack.render(out, formats=("pdf", "json"))
                self.assertEqual({r["key"] for r in pack.rows}, {"push rod", "GRM-01 shank"})
                pdf = PdfReader(out.with_suffix(".pdf"))
                for row in pack.rows:
                    self.assertNotIn("error", row)
                    self.assertEqual(row["qty"], 1)
                    self.assertEqual(len(row["sheets"]), 1)
                    self.assertIn("h6", pdf.pages[row["sheets"][0] - 1].extract_text())

    def test_repeated_product_has_one_pmi_sheet_and_total_quantity(self):
        from pypdf import PdfReader
        shape = bd.Cylinder(3, 12)
        shape.label = "bolt"
        part = draftwright.Part(shape, "bolt")
        part.shaft(shape).fit("h6")
        assembly = bd.Compound(children=[shape.moved(bd.Location((x, 0, 0))) for x in (0, 20, 40)])
        pack = draftwright.Pack(assembly, parts=[part])
        pack.render(self.directory / "repeated", formats=("pdf", "json"))
        self.assertEqual(len(pack.rows), 1, pack.rows)
        row = pack.rows[0]
        self.assertEqual(row["key"], "bolt")
        self.assertEqual(row["qty"], 3)
        self.assertNotIn("error", row)
        self.assertEqual(row["sheets"], [1])
        pdf = PdfReader(self.directory / "repeated.pdf")
        self.assertEqual(len(pdf.pages), 1)
        self.assertIn("h6", pdf.pages[0].extract_text())

    def test_source_pmi_beats_defaults_and_absent_pmi_inherits_them(self):
        from pypdf import PdfReader
        for material in (False, True):
            for tolerance in (False, True):
                with self.subTest(material=material, tolerance=tolerance):
                    shape = bd.Box(30, 20, 4)
                    part = draftwright.Part(shape, "source")
                    if material:
                        part.material("SOURCE STEEL")
                    if tolerance:
                        part.general_tolerance("±0.07")
                    pack = draftwright.Pack([part]).title(material="PACK BRASS", tolerance="ISO 2768-mK")
                    out = self.directory / f"defaults-{material}-{tolerance}"
                    pack.render(out, formats=("pdf", "json"))
                    row = pack.rows[0]
                    self.assertNotIn("error", row)
                    self.assertFalse([p for p in row["problems"] if "disagreement" in p["code"]], row["problems"])
                    text = PdfReader(out.with_suffix(".pdf")).pages[0].extract_text()
                    self.assertIn("SOURCE STEEL" if material else "PACK BRASS", text)
                    self.assertIn("±0.07" if tolerance else "ISO 2768-mK", text)
                    # Supplied semantic requirements must stay out of the layout Spec.
                    self.assertNotIn("material", pack.part("source").spec()["sheet"]["title"])
                    self.assertNotIn("tolerance", pack.part("source").spec()["sheet"]["title"])

    def test_geometry_signature_tracks_placement_children_labels_and_shapes(self):
        one, two = bd.Box(30, 20, 4), bd.Box(12, 10, 3)
        one.label, two.label = "one", "two"
        assembly = bd.Compound(children=[one, two])
        pack = draftwright.Pack(assembly)
        pack.render(self.directory / "before", formats=("pdf", "json"))
        before = pack.report["general_assembly"]["envelope"]
        original_export = bd.export_step
        with patch.object(bd, "export_step", wraps=original_export) as export:
            two.location = bd.Location((80, 0, 0))
            pack.render(self.directory / "moved", formats=("pdf", "json"))
            moved = pack.report["general_assembly"]["envelope"]
            self.assertGreater(moved[0], before[0])
            self.assertEqual(export.call_count, 1)
            pack.render(self.directory / "unchanged", formats=("json",))
            self.assertEqual(export.call_count, 1)
            three = bd.Box(7, 8, 6).moved(bd.Location((150, 0, 0)))
            three.label = "three"
            assembly.children = [one, two, three]
            pack.render(self.directory / "children", formats=("json",))
            self.assertEqual({r["key"] for r in pack.rows}, {"one", "two", "three"})
            self.assertGreater(pack.report["general_assembly"]["envelope"][0], moved[0])
            self.assertEqual(export.call_count, 2)
            three.label = "renamed"
            pack.render(self.directory / "renamed", formats=("json",))
            self.assertIn("renamed", {r["key"] for r in pack.rows})
            self.assertEqual(export.call_count, 3)
            three.wrapped = bd.Box(90, 8, 6).moved(bd.Location((150, 0, 0))).wrapped
            previous = pack.report["general_assembly"]["envelope"]
            pack.render(self.directory / "reshaped", formats=("json",))
            self.assertNotEqual(pack.report["general_assembly"]["envelope"], previous)
            self.assertEqual(export.call_count, 4)

    def test_single_placed_product_pmi_uses_its_product_local_faces(self):
        from pypdf import PdfReader
        ns = self.documented()
        child = ns["base_shape"].moved(bd.Location((80, 30, 20), (0, 0, 90)))
        pack = draftwright.Pack(bd.Compound(children=[child]), parts=[ns["base"]])
        pack.render(self.directory / "single-placed", formats=("pdf", "json"))
        self.assertEqual(pack.report["pages"], 1)
        row = pack.rows[0]
        self.assertNotIn("error", row)
        self.assertEqual([p["code"] for p in row["problems"]],
                         [p["code"] for p in ns["pack"].rows[0]["problems"]])
        page = PdfReader(self.directory / "single-placed.pdf").pages[0]
        text = page.extract_text()
        # The diameter/position controls stay on the off-centre bore; datum A
        # remains the bottom face. Its local offsets are 42 and 26, not world XYZ.
        for token in ("H7", "0.1", "A", "42", "26", "6082-T6"):
            self.assertIn(token, text)

    def test_geometry_only_and_failures_remain_data(self):
        one, two = bd.Box(30, 20, 4), bd.Box(12, 10, 3)
        one.label, two.label = "one", "two"
        assembly = bd.Compound(children=[one, two])
        # Deliberately different Part geometry cannot inherit another face's PMI.
        wrong = draftwright.Part(bd.Cylinder(2, 8), "one")
        wrong.shaft(wrong.shape).fit("h6")
        pack = draftwright.Pack(assembly, parts=[wrong])
        pack.render(self.directory / "failed", formats=("pdf", "json"))
        failed = next(r for r in pack.rows if r["key"] == "one")
        self.assertIn("reference geometry differs", failed["error"]["message"])
        self.assertFalse(failed["sheets"])
        self.assertTrue(any(f.get("part") == "one" and f["severity"] == "error"
                            and f["code"] == failed["error"]["code"]
                            and failed["error"]["message"] in f["message"]
                            for f in pack.findings))
        self.assertEqual(pack.report, json.loads((self.directory / "failed.json").read_text()))
        self.assertTrue(next(r for r in pack.rows if r["key"] == "two")["sheets"])
        geometry = draftwright.Pack(assembly)
        geometry.part("two").title(revision="D")
        geometry.render(self.directory / "geometry", formats=("json",))
        self.assertTrue(all(r["sheets"] for r in geometry.rows))
        with self.assertRaises(ValueError):
            draftwright.Pack(assembly, parts=[wrong, wrong])
        with self.assertRaises(ValueError):
            geometry.part("missing")
        with self.assertRaises(ValueError):
            draftwright.Pack(assembly, parts=[draftwright.Part(one, "missing")]).render(self.directory / "missing")
