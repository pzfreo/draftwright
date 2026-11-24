import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile

import pro_benchmark as bench


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "pro_benchmark"
BINARY = ROOT / "target" / "release" / "draftwright"
CORPUS = Path(os.environ["DRAFTWRIGHT_CORPUS"]) if "DRAFTWRIGHT_CORPUS" in os.environ else None


class WorkbookTest(unittest.TestCase):
    def test_workbook_relationships_shared_and_inline_strings(self):
        main = bench.XML["s"]
        rel = bench.REL[1:-1]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "synthetic.xlsx"
            with ZipFile(path, "w") as book:
                book.writestr("xl/workbook.xml",
                              f'<workbook xmlns="{main}" xmlns:r="{rel}"><sheets>'
                              '<sheet name="Definitions" r:id="rId7"/></sheets></workbook>')
                book.writestr("xl/_rels/workbook.xml.rels",
                              '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                              '<Relationship Id="rId7" Target="/xl/worksheets/constructs.xml"/>'
                              '</Relationships>')
                book.writestr("xl/sharedStrings.xml",
                              f'<sst xmlns="{main}"><si><t>Primary FTC</t></si>'
                              '<si><r><t>Specifi</t></r><r><t>cation</t></r></si></sst>')
                book.writestr("xl/worksheets/constructs.xml",
                              f'<worksheet xmlns="{main}"><sheetData>'
                              '<row r="1"><c r="A1" t="s"><v>0</v></c>'
                              '<c r="E1" t="s"><v>1</v></c></row>'
                              '<row r="2"><c r="A2"><v>6</v></c>'
                              '<c r="D2" t="inlineStr"><is><t>Diameter</t></is></c>'
                              '<c r="E2" t="inlineStr"><is><t>2X ⌀ 1</t></is></c></row>'
                              '<row r="3"><c r="A3"><v>6</v></c></row>'
                              '</sheetData></worksheet>')
            self.assertEqual(list(bench.xlsx_rows(path)), [
                {"A": "Primary FTC", "E": "Specification"},
                {"A": "6", "D": "Diameter", "E": "2X ⌀ 1"},
                {"A": "6"},
            ])
            self.assertEqual(bench.ftc_definitions(path), {"ftc_06": 1})


class MetricsTest(unittest.TestCase):
    def test_absent_is_na_and_observed_zero_is_numeric(self):
        self.assertEqual(bench.display("pmi", bench.na("No input.")), "n/a — No input.")
        self.assertEqual(bench.display("hole_table", bench.metric(0, 1)), "0/1 (0.0%)")
        self.assertEqual(bench.aggregate([], "hole_table")["denominator"], None)

    def test_requirement_coverage_tracks_drawn_listed_and_unaccounted(self):
        review = {"coverage": {"pmi": {"requirements": ["d1", "d2", "d3"],
                                      "drawn_sources": ["d1", "d2", "another"]}}}
        analysis = {"pmi": {"read": {"records": [{"source_id": "d1"}, {"source_id": "d2"}]}}}
        result = bench.pmi_metric(review, analysis, {"d2"})
        self.assertEqual(result, bench.metric(1, 3, read=2, read_not_drawn=1,
                                             listed_not_shown=1, unaccounted=1))

    def test_retired_instructions_reads_only_the_counter(self):
        self.assertEqual(bench.retired_instructions(b"  0.03 real\n  1234 instructions retired\n"), 1234)
        self.assertIsNone(bench.retired_instructions("  32 cycles elapsed\n"))
        self.assertIsNone(bench.cost_total([{"instructions_retired": None}]))
        self.assertEqual(bench.cost_total([{"instructions_retired": 12}, {"instructions_retired": 3}]), 15)

    def test_uncertainty_is_excluded_and_reported(self):
        result = bench.dimension_counts([{"uncertain": True}], {}, {}, {}, set())
        self.assertIsNone(result["denominator"])
        self.assertEqual(result["uncertain"], 1)

    def test_section_match_checks_plane_and_direction(self):
        wanted = {"direction": [1, 0, 0], "origin": [4, 0, 0]}
        view = {"direction": [1, 0, 0], "cut": {"origin": [4, 100, 3]}}
        self.assertTrue(bench.view_match("section", wanted, view))
        self.assertFalse(bench.view_match("section", {**wanted, "origin": [5, 0, 0]}, view))
        self.assertFalse(bench.view_match("section", {**wanted, "direction": [-1, 0, 0]}, view))

    def test_matching_reserves_specific_occurrences(self):
        self.assertEqual(bench.match_count([{1, 2}, {1}, {1}], [1, 2],
                                           lambda want, got: got in want), 2)


@unittest.skipUnless(BINARY.is_file() and CORPUS and (CORPUS / "nist").is_dir(),
                     "release binary and DRAFTWRIGHT_CORPUS required for CLI integration")
class SyntheticManifestTest(unittest.TestCase):
    def run_benchmark(self, manifest, output, *parts):
        env = {**os.environ, "DRAFTWRIGHT_BENCH_PRIVATE": str(manifest)}
        return subprocess.run(
            [sys.executable, "-I", str(bench.TOOLS / "pro_benchmark.py"),
             "--binary", str(BINARY), "--json", str(output), "--parts", *parts],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=120,
        )

    def test_synthetic_references_and_counts_only_output(self):
        fixture = bench.read_json(FIXTURES / "manifest.json")
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            manifest = directory / "synthetic-benchmark-manifest.json"
            output = directory / "result.json"
            for row in fixture:
                row["step"] = str(CORPUS.resolve() / Path(row["step"]).relative_to("corpus"))
                for field in ("reference", "spec"):
                    if field in row:
                        row[field] = str(FIXTURES / row[field])
            manifest.write_text(json.dumps(fixture), encoding="utf-8")
            run = self.run_benchmark(manifest, output, "P01", "P02")
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            raw_json = output.read_text()
            report = json.loads(raw_json)
            self.assertNotIn("wall_seconds", raw_json)
            self.assertIn("instructions_retired", report)
            rows = {r["id"]: r for r in report["sets"]["Private"]["parts"]}
            self.assertEqual(set(rows), {"P01", "P02"})
            self.assertEqual(rows["P01"]["metrics"]["dimensions"], bench.metric(1, 2))
            self.assertEqual(rows["P01"]["metrics"]["tolerance"], bench.metric(1, 1))
            self.assertEqual(rows["P02"]["metrics"]["hole_table"], bench.metric(1, 1))
            self.assertEqual(rows["P02"]["metrics"]["views"]["numerator"], 0)
            self.assertIsNone(rows["P02"]["metrics"]["dimensions"]["numerator"])
            self.assertIsNone(rows["P02"]["metrics"]["tolerance"]["numerator"])
            self.assertIn("| P01 | n/a |", run.stdout)
            self.assertEqual(rows["P02"]["metrics"]["dimensions"]["reason"], "Reference data unavailable.")
            for row in fixture:
                for key in ("step", "reference", "spec"):
                    if key in row:
                        self.assertNotIn(Path(row[key]).name, run.stdout + run.stderr + raw_json)
                        self.assertNotIn(Path(row[key]).stem, run.stdout + run.stderr + raw_json)
                text = bench.read_json(Path(row["reference"]))["general_tolerance"]["text"]
                self.assertNotIn(text, run.stdout + run.stderr + raw_json)

    def test_invalid_private_reference_is_redacted(self):
        marker = "synthetic-error-marker"
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            manifest = directory / "manifest.json"
            output = directory / "out.json"
            manifest.write_text(json.dumps([{"id": "P01", "step": f"{marker}.stp",
                                             "reference": f"{marker}.json"}]))
            run = self.run_benchmark(manifest, output, "P01")
            self.assertEqual(run.returncode, 1)
            self.assertIn("| P01 | n/a |", run.stdout)
            self.assertNotIn(marker, run.stdout + run.stderr + output.read_text())


if __name__ == "__main__":
    unittest.main()
