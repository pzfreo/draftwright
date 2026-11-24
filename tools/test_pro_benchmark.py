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
        self.assertEqual(result, bench.metric(1, 3, read=2, read_not_drawn=1, read_without_ink=0,
                                             listed_not_shown=1, unaccounted=1))

    def test_general_tolerance_presentation_note(self):
        payload = (b"#4=DESCRIPTIVE_REPRESENTATION_ITEM('Text.1','APPLIES TO ALL');\n"
                   b"#7=DESCRIPTIVE_REPRESENTATION_ITEM('','/n');\n"
                   b"#5=DESCRIPTIVE_REPRESENTATION_ITEM('Text.1','UNTOLERANCED SURFACES.');")
        self.assertTrue(bench.has_general_tolerance_note(payload))
        self.assertFalse(bench.has_general_tolerance_note(b"ISO 2768-m"))
        self.assertFalse(bench.has_general_tolerance_note(
            b"#6=DESCRIPTIVE_REPRESENTATION_ITEM('Text.1','UNLESS OTHERWISE SPECIFIED');"))

    def test_retired_instructions_reads_only_the_counter(self):
        self.assertEqual(bench.retired_instructions(b"  0.03 real\n  1234 instructions retired\n"), 1234)
        self.assertIsNone(bench.retired_instructions("  32 cycles elapsed\n"))
        self.assertIsNone(bench.cost_total([{"instructions_retired": None}]))
        self.assertEqual(bench.cost_total([{"instructions_retired": 12}, {"instructions_retired": 3}]), 15)

    def test_retired_instructions_from_macos_stderr_samples(self):
        # Successful capture reproduced in the installed macOS time(1) manual.
        sample = b"""real 5.01
user 0.00
sys 0.00
         0  maximum resident set size
         0  average shared memory size
         0  average unshared data size
         0  average unshared stack size
        80  page reclaims
         0  page faults
         0  swaps
         1  block input operations
         0  block output operations
         0  messages sent
         0  messages received
         0  signals received
         3  voluntary context switches
         0  involuntary context switches
   2054316  instructions retired
   2445544  cycles elapsed
    241664  peak memory footprint
"""
        self.assertEqual(bench.retired_instructions(sample), 2054316)
        denied = b"        0.00 real         0.00 user         0.00 sys\n" \
                 b"time: sysctl kern.clockrate: Operation not permitted\n"
        self.assertIsNone(bench.retired_instructions(denied))

    def test_uncertainty_is_excluded_and_reported(self):
        results = bench.dimension_counts([{"uncertain": True}], {}, {}, {}, set())
        for result in results:
            self.assertIsNone(result["denominator"])
            self.assertEqual(result["uncertain"], 1)

    def test_section_match_checks_plane_and_direction(self):
        wanted = {"direction": [1, 0, 0], "origin": [4, 0, 0]}
        view = {"direction": [0, 1, 0], "cut": {"normal": [1, 0, 0], "origin": [4, 100, 3]}}
        self.assertTrue(bench.view_match("section", wanted, view))
        self.assertTrue(bench.view_match("section", {**wanted, "origin": [4.5, 0, 0]}, view))
        self.assertFalse(bench.view_match("section", {**wanted, "origin": [4.51, 0, 0]}, view))
        self.assertTrue(bench.view_match("section", {**wanted, "direction": [-1, 0, 0]}, view))
        self.assertFalse(bench.view_match("section", {**wanted, "direction": [0, 1, 0]}, view))

    def test_views_use_unsigned_axes_and_detail_bounds(self):
        wanted = {"direction": [0, 0, 1], "bounds": [[0, 0, 0], [5, 5, 5]]}
        actual = {"direction": [0, 0, -1],
                  "detail": {"marked_bounds": [[2, 0, 0], [5, 5, 5]]}}
        for kind in ("principal", "auxiliary", "detail"):
            self.assertTrue(bench.view_match(kind, wanted, actual))
        actual["detail"]["marked_bounds"][0][0] = 2.01
        self.assertFalse(bench.view_match("detail", wanted, actual))

    def test_unknown_axes_match_by_kind_and_count(self):
        unknown = {"direction": None, "axis_unknown": True}
        ref = {"principal": [unknown], "section": [unknown, unknown], "detail": [],
               "auxiliary": [unknown, {"direction": [1, 0, 0]}]}
        plan = {"views": [
            {"id": "v:front", "direction": [1, 0, 0]},
            {"id": "v:section", "cut": {"normal": [1, 0, 0], "origin": [0, 0, 0]}},
            {"id": "v:aux1", "direction": [-1, 0, 0], "auxiliary": {"id": "a"}},
            {"id": "v:aux2", "direction": [0, -1, 0], "auxiliary": {"id": "b"}},
        ]}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "synthetic.json"
            path.write_text(json.dumps({"views": ref, "dimensions": None,
                                        "hole_table": None, "general_tolerance": None}))
            actual = bench.view_counts(bench.private_reference(path)["views"], plan)
        self.assertEqual(actual["by_kind"]["principal"], bench.metric(1, 1, uncertain=0))
        self.assertEqual(actual["by_kind"]["section"], bench.metric(1, 2, uncertain=0))
        self.assertEqual(actual["by_kind"]["auxiliary"], bench.metric(2, 2, uncertain=0))
        self.assertEqual((actual["numerator"], actual["denominator"]), (3, 4))

    def test_matching_reserves_specific_occurrences(self):
        self.assertEqual(bench.match_count([{1, 2}, {1}, {1}], [1, 2],
                                           lambda want, got: got in want), 2)


class DimensionMatcherTest(unittest.TestCase):
    features = {"features": [
        {"id": "hole-1", "kind": "hole", "faces": [1], "legacy_id": "old-hole-1"},
        {"id": "hole-2", "kind": "hole", "faces": [2]},
    ]}
    review = {"coverage": {"pmi": None}}

    def wanted(self, kind="diameter", feature="hole-1", nominal=10, datums=()):
        return {"feature": {"kind": "hole", "id": feature}, "nominal": nominal, "unit": "mm",
                "kind": kind, "tolerance": {"lower": -0.1, "upper": 0.1}, "datums": list(datums)}

    def callout(self, *measurements, state="shown", terms=(), from_spec=()):
        return {"id": "d:hole-1/bore.diameter", "value": 10, "kind": "callout",
                "state": {"kind": state}, "placed": {"label_at": [1, 2]}, "terms": terms,
                "from_spec": from_spec, "measurements": [
                    {"feature": feature, "parameter": role, "value": value}
                    for feature, role, value in measurements]}

    def score(self, wanted, dimensions, *, gdt=(), hole_tables=(), analysis=None, review=None):
        plan = {"sheet": {}, "dimensions": dimensions, "gdt": gdt, "hole_tables": hole_tables}
        present, toleranced = bench.dimension_counts(
            wanted, analysis or self.features, plan, review or self.review, set())
        return present["numerator"], toleranced["numerator"]

    def test_automatic_measurements_have_presence_without_tolerance(self):
        shown = self.callout(("hole-1", "bore.diameter", 10), ("hole-1", "counterbore.depth", 4))
        covered = self.callout(("hole-2", "bore.diameter", 10), state="covered")
        refs = [self.wanted(feature="old-hole-1", nominal=10.004),
                self.wanted(kind="linear", nominal=4),
                self.wanted(kind="radius"), self.wanted(feature="hole-2"),
                self.wanted(nominal=10.006)]
        self.assertEqual(self.score(refs, [shown, covered]), (2, 0))

    def test_model_reference_diameter_retains_its_feature_kind(self):
        claimed = self.callout(("geometry:face-key", "diameter", 10))
        analysis = {**self.features, "edge_ink": {
            "references": {"face-key": [{"features": ["hole-1"]}]}}}
        self.assertEqual(self.score([self.wanted(), self.wanted("radius")], [claimed],
                                    analysis=analysis), (1, 0))

    def test_automatic_location_uses_its_drawn_datum(self):
        location = dict(self.callout(("hole-1", "location.length.x", 10)),
                        kind="linear", basis="a location; measured from datum A (datum:#1)")
        datum = {"id": "pmi:datum", "source": "datum:#1", "kind": "datum_feature",
                 "state": {"kind": "shown"}, "placed": {"tip": [1, 2]}, "cells": ["A"]}
        self.assertEqual(self.score([self.wanted("linear", datums=["A"])],
                                    [location], gdt=[datum]), (1, 0))
        self.assertEqual(self.score([self.wanted("linear", datums=["B"])],
                                    [location], gdt=[datum]), (0, 0))

    def test_printed_fit_and_tolerance_only_attach_to_the_qualified_quantity(self):
        m = (("hole-1", "bore.diameter", 10), ("hole-1", "counterbore.depth", 4))
        band = self.callout(*m, terms=["⌀10", "±0.1", "↧4"], from_spec=["tolerance"])
        self.assertEqual(self.score([self.wanted(), self.wanted(kind="linear", nominal=4)],
                                    [band]), (2, 1))
        fit = self.callout(*m, terms=["⌀10", "H7", "↧4"], from_spec=["fit"])
        wanted = dict(self.wanted(), tolerance=None, fit="H7")
        self.assertEqual(self.score([wanted], [fit]), (1, 1))
        self.assertEqual(self.score([dict(wanted, fit="h7")], [fit]), (1, 0))

    def test_hole_schedule_carries_a_band_only_when_its_cell_prints_it(self):
        covered = self.callout(("hole-1", "bore.diameter", 10), state="covered",
                               terms=["⌀10", "±0.1"], from_spec=["tolerance"])
        row = {"feature": "hole-1", "cells": ["A", "ø10 ±0.1"],
               "measurements": covered["measurements"]}
        table = {"state": {"kind": "shown"}, "rows": [row]}
        self.assertEqual(self.score([self.wanted(), self.wanted()], [covered],
                                    hole_tables=[table]), (1, 1))
        row["cells"][1] = "ø10"
        self.assertEqual(self.score([self.wanted()], [covered], hole_tables=[table]), (1, 0))

    def test_pmi_claim_adds_attributes_to_the_same_printed_statement(self):
        d = self.callout(("hole-1", "bore.diameter", 10), ("pmi:key", "diameter", 10))
        source = {"source_id": "diameter:#1", "category": "dimension", "kind": "diameter",
                  "value": 10, "faces": [1], "upper_tol": 0.1, "lower_tol": -0.1,
                  "datum_refs": [], "fields": [["reference datum", "A"]]}
        analysis = {**self.features, "pmi": {"read": {"records": [source]}}}
        review = {"coverage": {"pmi": {"drawn_items": {"pmi:key": "diameter:#1"}}}}
        wanted = self.wanted(datums=["A"])
        self.assertEqual(self.score([wanted, wanted, self.wanted()], [d],
                                    analysis=analysis, review=review), (1, 1))

    def test_gdt_requires_a_drawn_frame_with_magnitude_and_ordered_datums(self):
        item = "pmi:geometric_tolerance.position:f1"
        frame = {"id": item, "source": "spec:control", "state": {"kind": "shown"},
                 "placed": {"tip": [1, 2]}, "kind": "control_frame", "faces": [1],
                 "cells": ["⌖", "⌀0.2", "A", "B"]}
        review = {"coverage": {"pmi": {"drawn_items": {item: "spec:control"}}}}
        good = dict(self.wanted("gdt.position", nominal=0.2, datums=["A", "B"]), tolerance=None)
        refs = [good, dict(good, kind="gdt.flatness"), dict(good, datums=["B", "A"]),
                dict(good, nominal=0.3), self.wanted("gdt.position", "hole-2", 0.2, ["A", "B"])]
        self.assertEqual(self.score(refs, [], gdt=[frame], review=review), (1, 1))
        self.assertEqual(self.score([good], [], gdt=[dict(frame, state={"kind": "not_drawable"})],
                                    review=review), (0, 0))


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
            self.assertEqual(rows["P01"]["metrics"]["dimension_tolerance"], bench.metric(1, 2))
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
            fixture[0].pop("spec")
            manifest.write_text(json.dumps(fixture), encoding="utf-8")
            automatic = self.run_benchmark(manifest, output, "P01")
            self.assertEqual(automatic.returncode, 0, automatic.stdout + automatic.stderr)
            measures = bench.read_json(output)["sets"]["Private"]["parts"][0]["metrics"]
            self.assertEqual(measures["dimensions"], bench.metric(1, 2))
            self.assertEqual(measures["dimension_tolerance"], bench.metric(0, 2))

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
