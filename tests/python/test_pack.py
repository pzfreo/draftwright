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
        with self.assertRaises(ValueError):
            p.title(number="ASM")
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
        self.assertEqual(pack.findings, report["findings"])
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
