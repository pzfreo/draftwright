"""Measure professional drawing coverage with a release draftwright binary.

    DRAFTWRIGHT_CORPUS=/path/to/corpus REF=/path/to/nist-ref \
      python3 -I tools/pro_benchmark.py --binary target/release/draftwright [--json OUT] [--parts ID ...]

--nist DIR selects the public AP242 exports (default: REF/ap242).
REF contains the public FTC definitions workbook and optional SFA expected PMI.
The workbook inventories test constructs, which can span several requirements.
SFA also has overlapping coverage categories. Neither is a source-ID crosswalk
to the corpus STEP exports. The NIST denominator is the submitted STEP's semantic
requirement inventory; a geometry-only export has no denominator.

Private reference contract
--------------------------
DRAFTWRIGHT_BENCH_PRIVATE points to a JSON array of
{"id": "P01", "step": "part.step", "reference": "reference.json"} objects.
Paths are absolute or relative to the manifest. Optionally include "spec", a
draftwright settings file to use for that part. References never become settings.
Use opaque unique IDs matching P followed by at least two digits.

Each reference is an object with these fields (null means unavailable):
* "views": an object with an array for each kind: principal, section, auxiliary,
  detail. Each view has "direction" (toward viewer, part-frame vector). A section
  also has "origin" (a point on the cut plane in part-frame mm) and may have
  "half": true with "keep" (direction toward the cut half). A detail also has
  "bounds": [[xmin,ymin,zmin],[xmax,ymax,zmax]] in part-frame mm for its intended
  crop. Empty arrays mean the drawing uses none of that kind. The scored row
  covers derived views; principal view counts are reported for context.
* "dimensions": one entry per toleranced or datum-referenced dimension, e.g.
  {"feature":{"kind":"hole","id":"f:hole:f73"},"kind":"diameter",
   "nominal":20,"unit":"mm","tolerance":{"lower":-0.1,"upper":0.1},"datums":[]}.
  Feature kinds and optional occurrence IDs use draftwright analyse's vocabulary.
  Use "envelope" for overall size. Use analyse's PMI kind (e.g. linear, diameter,
  radius, angular, thickness) or "gdt.<kind>" for a geometric control,
  with its stated magnitude as nominal. Units are mm, in or deg. "resolution"
  optionally states the nominal's display increment (defaults .01 mm, .001 in,
  .01 deg). "tolerance" may be null or contain signed deviations as above, and
  "fit" optionally gives the case-sensitive ISO fit class. "datums" lists the
  referenced letters in order. Omit neither the tolerance field nor the datum
  array. Without an occurrence ID, equal dimensions on the same feature kind
  are matched by multiplicity. One drawn statement can satisfy at most one entry.
  Mark an unresolved dimension or view with "uncertain": true. Remaining fields
  on that entry are optional. The benchmark reports these entries separately
  and excludes them from the matching denominator.
* "hole_table": boolean. "general_tolerance": {"stated": true, "text": "..."}
  or {"stated": false, "text": null}. General tolerance from the engineer's
  drawing is recorded for context. The scored cell uses only STEP or Spec input.

Private subprocess output and temporary caches stay local. The report carries
only validated case IDs, counts, timings and fixed diagnostic reasons.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import posixpath
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

TOOLS = Path(__file__).resolve().parent
METRICS = {
    "pmi": ("Requirements in the file drawn (NIST)", "All drawn or listed as not shown; 95% drawn"),
    "true": ("Features not shown true (both sets)", "0"),
    "views": ("Engineer's views matched (sections, aux, detail)", "90%"),
    "complete": ("Features complete (sized and located)", "95%"),
    "dimensions": ("Engineer's toleranced / datum dimensions present, with the same references", "85%"),
    "redundancy": ("Redundancy", "No worse than today"),
    "tolerance": ("Tolerance box filled when the file or settings give one", "100%"),
    "hole_table": ("Hole table where the engineer used one", "All"),
    "quality": ("30-part quality check", "No worse"),
}
NIST_IDS = tuple([f"ctc_{i:02}" for i in range(1, 6)] +
                 [f"ftc_{i:02}" for i in range(6, 12)])
VIEW_KINDS = ("principal", "section", "auxiliary", "detail")
UNIT_SCALE = {"mm": 1, "in": 25.4, "deg": 1}
RESOLUTION = {"mm": .01, "in": .001, "deg": .01}
REDUNDANCY = {"closed_dimension_chain", "duplicate_measurement",
              "feature_located_twice", "over_dimensioned_pattern"}
FEATURE_GAPS = {
    "angled_step_requirement_unsupported", "axial_length_missing",
    "dimension_not_authored", "dimension_view_not_shown", "dimension_witness_detached",
    "dimensions_not_ported", "feature_not_dimensioned", "feature_not_located",
    "feature_not_sized", "hole_table_entry_invalid", "label_vs_measured",
    "modelled_thread_not_depicted", "pad_footprint_not_defined", "pocket_not_located",
    "profile_corner_unlocated", "profiled_bore_not_dimensioned",
    "section_recess_recognition_refused", "step_level_support_missing",
}
PMI_GAPS = {
    "pmi_not_drawn", "pmi_not_extracted", "pmi_not_lowered", "pmi_not_rendered",
    "pmi_datum_suppressed", "pmi_note_unattached",
    "pmi_value_mismatch", "pmi_source_text_mismatch", "pmi_source_site_mismatch",
    "manufacturing_reference_unresolved", "step_general_tolerance_disagreement",
    "step_general_tolerance_mismatch", "step_material_disagreement", "step_material_mismatch",
    "step_material_ambiguous", "step_material_unavailable",
}
XML = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def read_json(path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def xlsx_rows(path):
    """Yield nonempty cell dictionaries from the workbook's first sheet."""
    def string(element):
        if element is None:
            return ""
        runs = element.findall("s:r/s:t", XML)
        return "".join(node.text or "" for node in runs or element.findall("s:t", XML))

    with ZipFile(path) as book:
        workbook = ET.fromstring(book.read("xl/workbook.xml"))
        sheet = workbook.find("s:sheets/s:sheet", XML)
        rels = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
        target = next(rel.attrib["Target"] for rel in rels if rel.get("Id") == sheet.get(REL + "id"))
        sheet_path = posixpath.normpath(posixpath.join("xl", target)).lstrip("/")
        strings = []
        if "xl/sharedStrings.xml" in book.namelist():
            strings = [string(node) for node in ET.fromstring(book.read("xl/sharedStrings.xml"))]
        for row in ET.fromstring(book.read(sheet_path)).findall("s:sheetData/s:row", XML):
            cells = {}
            for cell in row.findall("s:c", XML):
                col = cell.attrib["r"].rstrip("0123456789")
                value = cell.findtext("s:v", default="", namespaces=XML)
                if cell.get("t") == "s":
                    value = strings[int(value)]
                elif cell.get("t") == "inlineStr":
                    value = string(cell.find("s:is", XML))
                if value.strip():
                    cells[col] = value.strip()
            if cells:
                yield cells


def ftc_definitions(path):
    """Count official construct rows by primary FTC, separately from requirement counts."""
    rows = xlsx_rows(path)
    for row in rows:
        if row.get("A") == "Primary FTC" and row.get("E") == "Specification":
            break
    else:
        raise ValueError("FTC workbook header unavailable")
    return Counter(f"ftc_{int(row['A']):02}" for row in rows
                   if row.get("A", "").isdigit() and row.get("D") and row.get("E"))


def metric(n=None, d=None, reason=None, **counts):
    return {"numerator": n, "denominator": d, **({"reason": reason} if reason else {}), **counts}


def na(reason):
    return metric(reason=reason)


@dataclass(frozen=True)
class Case:
    id: str
    step: Path
    reference: Path | None = None
    spec: Path | None = None


def private_cases(path):
    """Validate IDs before exposing any case ID to the output."""
    rows = read_json(path)
    if not isinstance(rows, list):
        raise ValueError("manifest must be an array")
    cases = []
    seen = set()
    for item in rows:
        case_id = item["id"]
        if not re.fullmatch(r"P[0-9]{2,}", case_id) or case_id in seen:
            raise ValueError("invalid or duplicate opaque case ID")
        seen.add(case_id)
        cases.append(Case(case_id, path.parent / item["step"], path.parent / item["reference"],
                          path.parent / item["spec"] if "spec" in item else None))
    return cases


def finite(value):
    if type(value) not in (float, int) or not math.isfinite(value):
        raise ValueError("expected a finite number")
    return value


def vector(values, length):
    if len(values) != length:
        raise ValueError("invalid vector")
    return tuple(map(finite, values))


def direction(values):
    v = vector(values, 3)
    length = math.hypot(*v)
    if not length:
        raise ValueError("zero direction")
    return tuple(n / length for n in v)


def private_reference(path):
    ref = read_json(path)
    if ref["views"] is not None:
        for kind in VIEW_KINDS:
            if not isinstance(ref["views"][kind], list):
                raise ValueError("expected a view list")
            for view in ref["views"][kind]:
                if view.get("uncertain") is True:
                    continue
                direction(view["direction"])
                if kind == "section":
                    vector(view["origin"], 3)
                    if view.get("half"):
                        direction(view["keep"])
                if kind == "detail":
                    low, high = (vector(point, 3) for point in view["bounds"])
                    if any(a > b for a, b in zip(low, high)):
                        raise ValueError("inverted bounds")
    if ref["dimensions"] is not None:
        if not isinstance(ref["dimensions"], list):
            raise ValueError("expected a dimension list")
        for dim in ref["dimensions"]:
            if dim.get("uncertain") is True:
                continue
            if not isinstance(dim["feature"]["kind"], str) or not isinstance(dim["kind"], str):
                raise ValueError("expected dimension and feature kinds")
            if "id" in dim["feature"] and not isinstance(dim["feature"]["id"], str):
                raise ValueError("invalid feature identity")
            finite(dim["nominal"])
            if finite(dim.get("resolution", RESOLUTION[dim["unit"]])) <= 0:
                raise ValueError("expected positive resolution")
            if not isinstance(dim["datums"], list) or any(
                    not re.fullmatch(r"[A-Z]+[0-9]*", datum) for datum in dim["datums"]):
                raise ValueError("invalid datum letters")
            if dim["tolerance"] is not None:
                if finite(dim["tolerance"]["lower"]) > 0 or finite(dim["tolerance"]["upper"]) < 0:
                    raise ValueError("expected signed deviations")
            if "fit" in dim and not re.fullmatch(r"[A-Za-z]{1,2}[0-9]+", dim["fit"]):
                raise ValueError("invalid fit")
            if (dim["tolerance"] is None and not dim["datums"] and "fit" not in dim
                    and not dim["kind"].startswith("gdt.")):
                raise ValueError("dimension has no tolerance, fit or datum reference")
    if ref["hole_table"] is not None and type(ref["hole_table"]) is not bool:
        raise ValueError("invalid hole-table flag")
    general = ref["general_tolerance"]
    if general is not None and (
        type(general["stated"]) is not bool
        or (general["stated"] and not (isinstance(general["text"], str) and general["text"].strip()))
        or (not general["stated"] and general["text"] is not None)
    ):
        raise ValueError("invalid general tolerance")
    return ref


def match_count(wanted, actual, matches):
    """Maximum one-to-one match; a broad reference may yield to a more specific one."""
    candidates = [[i for i, got in enumerate(actual) if matches(want, got)] for want in wanted]
    used = {}

    def place(want, seen):
        for i in candidates[want]:
            if i in seen:
                continue
            seen.add(i)
            if i not in used or place(used[i], seen):
                used[i] = want
                return True
        return False

    return sum(place(i, set()) for i in range(len(wanted)))


def same_direction(a, b):
    return math.dist(direction(a), direction(b)) <= 1e-3


def view_match(kind, wanted, actual):
    if not same_direction(wanted["direction"], actual["direction"]):
        return False
    if kind == "section":
        half = actual.get("half_section")
        cut = half or actual["cut"]
        if bool(wanted.get("half")) != bool(half):
            return False
        if half and not same_direction(wanted["keep"], half["keep"]):
            return False
        normal = direction(wanted["direction"])
        return abs(sum((a - b) * n for a, b, n in
                       zip(wanted["origin"], cut["origin"], normal))) <= .05
    if kind == "detail":
        return all(math.dist(a, b) <= .05
                   for a, b in zip(wanted["bounds"], actual["detail"]["marked_bounds"]))
    return True


def view_counts(reference, plan):
    kinds = {key: [] for key in VIEW_KINDS}
    for view in plan["views"]:
        if view.get("detail"):
            kinds["detail"].append(view)
        elif view.get("auxiliary"):
            kinds["auxiliary"].append(view)
        elif view.get("cut") or view.get("half_section"):
            kinds["section"].append(view)
        elif view["id"] in {"v:front", "v:rear", "v:side", "v:left", "v:plan", "v:bottom", "v:end"}:
            kinds["principal"].append(view)
    certain = {kind: [view for view in reference[kind] if view.get("uncertain") is not True]
               for kind in VIEW_KINDS}
    counts = {kind: (match_count(certain[kind], kinds[kind],
                                 lambda a, b: view_match(kind, a, b)), len(certain[kind]))
              for kind in VIEW_KINDS}
    uncertainty = {kind: len(reference[kind]) - len(certain[kind]) for kind in VIEW_KINDS}
    n, d = (sum(counts[kind][i] for kind in VIEW_KINDS[1:]) for i in (0, 1))
    extra = {"by_kind": {k: metric(*counts[k], uncertain=uncertainty[k]) for k in counts},
             "uncertain": sum(uncertainty[k] for k in VIEW_KINDS[1:])}
    if d:
        return metric(n, d, **extra)
    return metric(reason="No scorable derived views in the reference.", **extra)


def drawing_findings(review):
    return [item for key in ("problems", "lint", "structural") for item in review[key]]


def failed_pmi(findings):
    return {source for item in findings if item["code"] in PMI_GAPS
            for key in ("sources", "refs") for source in item.get(key, ())}


def pmi_metric(review, analysis, bad):
    evidence = review["coverage"]["pmi"]
    if not evidence or not evidence["requirements"]:
        if analysis.get("pmi", {}).get("refused"):
            return na("STEP PMI inventory could not be read.")
        return na("Submitted STEP has no inventoried semantic PMI requirements.")
    required = set(evidence["requirements"])
    carried = set(evidence["drawn_sources"]) & required
    drawn = carried - bad
    listed = (required - drawn) & bad
    records = analysis.get("pmi", {}).get("read", {}).get("records", ())
    read = required & {r["source_id"] for r in records}
    return metric(len(drawn), len(required), read=len(read),
                  read_without_ink=len(read - carried),
                  read_not_drawn=len(read - drawn), listed_not_shown=len(listed),
                  unaccounted=len(required - drawn - listed))


def is_feature_gap(code):
    return code in FEATURE_GAPS or code.endswith(
        ("_requirement_missing", "_requirement_unverifiable", "_requirement_unsupported",
         "_requirement_mismatch", "_requirement_ambiguous")
    )


def feature_metrics(plan, review, findings):
    inventory = set(review["coverage"]["dimensionable_features"])
    aliases = review["coverage"]["inclined_location_gaps"]
    missing = set(aliases)
    dimensions = {item["id"]: {m["feature"] for m in item["measurements"]}
                  for item in plan["dimensions"]}
    for item in findings:
        if is_feature_gap(item["code"]):
            missing.update(item["subjects"])
            missing.update(req["feature"] for req in item.get("requirements", ()))
            for item_id in [*item["subjects"], item.get("annotation")]:
                missing.update(dimensions.get(item_id, ()))
    if not inventory:
        reason = "No recognized dimension-bearing features."
        return na(reason), na(reason)
    return metric(len(set(aliases) & inventory), len(inventory)), metric(
        len(inventory - missing), len(inventory)
    )


STEP_DESCRIPTIVE_TEXT = re.compile(
    rb"DESCRIPTIVE_REPRESENTATION_ITEM\s*\(\s*'(?:[^']|'')*'\s*,\s*'((?:[^']|'')*)'",
    re.IGNORECASE,
)
GLOBAL_TOLERANCE_NOTE = re.compile(
    rb"\b(?:APPL(?:IES|Y)\s+TO\s+ALL\s+UNTOLERANCED\s+(?:SURFACES|FEATURES)|"
    rb"GENERAL\s+TOLERANCES?\s*(?:[:=]|(?:ACCORDING\s+TO|PER)\s+)|"
    rb"ISO\s*2768\s*[- ]\s*[FMCHKV]\b)",
    re.IGNORECASE,
)


def has_general_tolerance_note(step):
    # AP242 presentation notes may split a global-profile clause over consecutive text items.
    text = b" ".join(STEP_DESCRIPTIVE_TEXT.findall(step))
    text = re.sub(rb"\\X\\0[AD]|\\{1,2}n", b" ", text, flags=re.IGNORECASE)
    return GLOBAL_TOLERANCE_NOTE.search(text) is not None


def filled_tolerance(plan, analysis, review, step):
    records = analysis.get("pmi", {}).get("read", {}).get("records", ())
    given = plan["spec"]["sheet"].get("title", {}).get("tolerance", "").strip()
    has_pmi = any(r["kind"] == "general_tolerances" for r in records)
    if not (given or has_pmi or has_general_tolerance_note(step.read_bytes())):
        if analysis.get("pmi", {}).get("refused"):
            return na("STEP PMI inventory could not be read.")
        return na("No general tolerance in the STEP file or settings.")
    return metric(int(review["coverage"]["tolerance_box_filled"]), 1)


def numeric_band(record):
    nominal = record["value"]
    def deviation(side):
        if record.get(f"{side}_tol") is not None:
            return record[f"{side}_tol"]
        bound = record.get(f"{side}_bound")
        return bound - nominal if bound is not None else None

    lower, upper = deviation("lower"), deviation("upper")
    if lower is not None or upper is not None:
        return (lower or 0, upper or 0)
    return None


def datum_letters(record):
    return tuple(token.strip() for token in record["datum_refs"] or
                 [value for key, value in record.get("fields", ()) if key == "reference datum"])


def feature_kind(features, fid):
    return features.get(fid, {}).get("kind", fid if fid == "envelope" else None)


def pmi_dimensions(analysis, proven, features):
    records = analysis.get("pmi", {}).get("read", {}).get("records", ())
    for record in records:
        category, kind, value = record["category"], record["kind"], record["value"]
        if (category not in {"dimension", "geometric_tolerance"} or value is None
                or record.get("reference") or record["source_id"] not in proven):
            continue
        faces = set(record["faces"])
        owners = {fid for fid, f in features.items() if faces.intersection(f["faces"])}
        datums = datum_letters(record)
        band = numeric_band(record)
        if category == "geometric_tolerance":
            kind = "gdt." + kind
        yield (kind, value, "deg" if kind == "angular" else "mm", band,
               record.get("fit"), datums, owners)


def spec_dimensions(plan):
    """Use placed, Spec-authored statements and their measurement role, never label equality."""
    spec = plan["spec"]
    authored = spec.get("dimensions", {})
    withheld = {sub for p in plan["problems"] if p["code"] == "tolerance_withheld"
                for sub in p["subjects"]}
    for dim in plan["dimensions"]:
        if not dim.get("placed") or dim.get("reference") or dim["id"] in withheld:
            continue
        mode = set(dim.get("from_spec", ())) & {"fit", "tolerance"}
        if not mode:
            continue
        owner, _, primary = dim["id"].partition("/")
        measurements = [m for m in dim["measurements"] if m["parameter"] == primary]
        if not measurements:
            continue
        owners = {m["feature"] for m in measurements}
        lead = measurements[0]["parameter"]
        unit = "deg" if dim["kind"] == "angular" else "mm"
        kind = ("angular" if unit == "deg" else "diameter" if lead.endswith(".diameter") else
                "radius" if lead.endswith(".radius") else "thickness" if lead == "thickness.length"
                else "linear")
        entry = authored.get(dim["id"], {})
        band, fit = None, None
        if "fit" in mode:
            fit = entry.get("fit") or spec.get("features", {}).get(measurements[0]["feature"], {}).get("fit")
        if "tolerance" in mode:
            value = entry.get("tolerance") or (spec.get("envelope_tolerance") if owner == "d:envelope" else None)
            if value:
                value = value.strip()
                scale = 25.4 if unit == "mm" and plan["sheet"].get("units") == "in" else 1
                if value.startswith("±"):
                    upper = float(value[1:]) * scale
                    band = (-upper, upper)
                else:
                    upper, lower = map(float, value.split())
                    band = (lower * scale, upper * scale)
        yield (kind, dim["value"], unit, band, fit, (), owners)


def dimension_counts(reference, analysis, plan, review, bad):
    certain = [dim for dim in reference if dim.get("uncertain") is not True]
    uncertain = len(reference) - len(certain)
    if not certain:
        return metric(reason="No scorable toleranced or datum-reference dimensions.", uncertain=uncertain)
    features = {f["id"]: f for f in analysis["features"]}
    pmi = review["coverage"]["pmi"] or {}
    proven = set(pmi.get("drawn_sources", ())) - bad
    actual = [*pmi_dimensions(analysis, proven, features), *spec_dimensions(plan)]

    def matches(want, got):
        kind, nominal, unit, band, fit, datums, owners = got
        target = want["feature"]
        if (want["kind"] != kind or (want["unit"] == "deg") != (unit == "deg")
                or tuple(want["datums"]) != datums):
            return False
        if not any(target["kind"] == feature_kind(features, fid) and (
                "id" not in target or target["id"] == fid or
                target["id"] == features.get(fid, {}).get("legacy_id")) for fid in owners):
            return False
        scale = UNIT_SCALE[want["unit"]]
        delta = want.get("resolution", RESOLUTION[want["unit"]]) * scale / 2
        if abs(want["nominal"] * scale - nominal) > delta + 1e-9:
            return False
        given = want["tolerance"]
        if given is not None and (band is None or any(
                abs(given[key] * scale - n) > 1e-6 for key, n in zip(("lower", "upper"), band))):
            return False
        return "fit" not in want or want["fit"] == fit

    result = metric(match_count(certain, actual, matches), len(certain))
    if uncertain:
        result["uncertain"] = uncertain
    return result


def retired_instructions(stderr):
    if isinstance(stderr, bytes):
        stderr = stderr.decode(errors="replace")
    found = re.search(r"^\s*([0-9]+)\s+instructions retired\s*$", stderr, re.MULTILINE)
    return int(found[1]) if found else None


def instruction_counter_available():
    if sys.platform != "darwin":
        return False
    try:
        probe = subprocess.run(["/usr/bin/time", "-l", "/usr/bin/true"],
                               capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0 and retired_instructions(probe.stderr) is not None


class InstructionCounter:
    def __init__(self, available):
        self.counts = [] if available else None

    def run(self, args, **kwargs):
        if self.counts is None:
            return subprocess.run(args, **kwargs)
        result = subprocess.run(["/usr/bin/time", "-l", *args], **kwargs)
        self.counts.append(retired_instructions(result.stderr))
        return result

    @property
    def total(self):
        return sum(self.counts) if self.counts and None not in self.counts else None


def cost_total(rows):
    costs = [row["instructions_retired"] for row in rows]
    return sum(costs) if costs and None not in costs else None


def run_cli(binary, command, case, cache, counter):
    args = [binary, command, case.step]
    if command != "analyse" and case.spec is not None:
        args.extend(("--spec", case.spec))
    env = {**os.environ, "DRAFTWRIGHT_PLAN_CACHE": str(cache)}
    proc = counter.run(args, env=env, capture_output=True, check=True, timeout=600)
    return json.loads(proc.stdout)


def measure(case, private, binary, cache, count_instructions):
    counter = InstructionCounter(count_instructions)
    error = None
    reference = None
    if private:
        try:
            reference = private_reference(case.reference)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
            error = "Reference unavailable or invalid."
    try:
        analysis = run_cli(binary, "analyse", case, cache, counter)
        plan = run_cli(binary, "plan", case, cache, counter)
        review = run_cli(binary, "review", case, cache, counter)
        findings = drawing_findings(review)
        bad = failed_pmi(findings)
        shown_true, complete = feature_metrics(plan, review, findings)
        checks = {
            "pmi": na("NIST-only metric.") if private else pmi_metric(review, analysis, bad),
            "true": shown_true,
            "complete": complete,
            "redundancy": metric(sum(item["code"] in REDUNDANCY for item in findings), 1),
            "tolerance": filled_tolerance(plan, analysis, review, case.step),
            "quality": na("Measured across the separate quality corpus."),
        }
        for key in ("views", "dimensions", "hole_table"):
            if not private:
                checks[key] = na("No engineer's 2D reference for NIST.")
            elif reference is None or reference[key] is None:
                checks[key] = na("Reference data unavailable.")
        if reference is not None:
            if reference["views"] is not None:
                checks["views"] = view_counts(reference["views"], plan)
            if reference["dimensions"] is not None:
                checks["dimensions"] = dimension_counts(reference["dimensions"], analysis, plan, review, bad)
            if reference["hole_table"] is not None:
                tables = plan.get("hole_tables") or [plan.get("hole_table")]
                has_table = any(t and t["rows"] and t["blocks"] for t in tables)
                checks["hole_table"] = metric(int(has_table), 1) if reference["hole_table"] else na(
                    "Engineer did not use a hole table.")
    except (subprocess.SubprocessError, OSError, ValueError, TypeError, KeyError, AttributeError,
            OverflowError):
        error = "Drawing command or coverage evidence unavailable."
        checks = {key: na(error) for key in METRICS}
    row = {"id": case.id, "metrics": checks, "instructions_retired": counter.total}
    if error:
        row["error"] = error
    if reference and reference["general_tolerance"] is not None:
        row["engineer_general_tolerance"] = metric(int(reference["general_tolerance"]["stated"]), 1)
    return row


def nist_reference(case, root, definitions):
    """Reference counts and provenance, apart from the actual STEP requirement audit."""
    info = {}
    if case.id.startswith("ftc"):
        if case.id in definitions:
            info["workbook_construct_rows"] = definitions[case.id]
        folder = root / "ftc-definitions"
        info["index_pdf_available"] = any(folder.glob(f"nist_{case.id}_*_fsi.pdf"))
    expected = root / f"nist_{case.id}.expected.json"
    if expected.is_file():
        sfa = read_json(expected)
        info["sfa_pmi_rows"] = sum(bool(row["cells"].get("A") and row["cells"].get("B"))
                                   for row in sfa["rows"])
        info["sfa_same_step"] = sfa["step_sha256"] == hashlib.sha256(case.step.read_bytes()).hexdigest()
    return info


def load_ci():
    spec = importlib.util.spec_from_file_location("ci_acceptance", TOOLS / "ci_acceptance.py")
    ci = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ci)
    return ci


def quality_check(corpus, binary, cache, out, count_instructions):
    ci = load_ci()
    baseline = read_json(TOOLS / "ci-quality-baseline.json")
    manifest = ci.manifest("quality_parts.txt")
    if len(manifest) != 30 or set(baseline["parts"]) != ci.names(manifest) or baseline["metrics"] != ci.KEYS:
        return {"metric": na("Quality manifest or baseline is incomplete."), "parts": [], "passed": False}
    def one(path):
        counter = InstructionCounter(count_instructions)
        name, actual = ci.quality.one(str(corpus / path), str(binary), str(out), run=counter.run)
        return name, actual, counter.total

    # quality.one inherits the public plan cache and writes into this scratch directory.
    previous = os.environ.get("DRAFTWRIGHT_PLAN_CACHE")
    os.environ["DRAFTWRIGHT_PLAN_CACHE"] = str(cache)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(one, manifest))
    finally:
        if previous is None:
            del os.environ["DRAFTWRIGHT_PLAN_CACHE"]
        else:
            os.environ["DRAFTWRIGHT_PLAN_CACHE"] = previous
    rows = []
    regressions = []
    for name, actual, instructions in results:
        try:
            worse = []
            for key in ci.KEYS:
                before, after = baseline["parts"][name][key], ci.count(actual, key, name)
                if after > before:
                    worse.append({"id": name, "metric": key, "before": before, "after": after})
            count = metric(len(ci.KEYS) - len(worse), len(ci.KEYS))
            regressions.extend(worse)
        except ValueError:
            count = na("Quality drawing or metric unavailable.")
        rows.append({"id": name, "metric": count, "instructions_retired": instructions})
    if any(row["metric"]["denominator"] is None for row in rows):
        return {"metric": na("Quality evidence missing for one or more parts."),
                "parts": rows, "passed": False}
    try:
        summary = ci.validate("quality", {name: result for name, result, _ in results}, baseline)
    except ValueError:
        summary = {"parts": len(rows), "cells": len(rows) * len(ci.KEYS)}
        summary["before"] = {k: sum(row[k] for row in baseline["parts"].values()) for k in ci.KEYS}
        summary["after"] = {k: sum(row[k] for _, row, _ in results) for k in ci.KEYS}
    n = sum(row["metric"]["numerator"] for row in rows)
    d = summary["cells"]
    return {"metric": metric(n, d), "parts": rows, "summary": summary, "passed": n == d,
            "regressions": regressions}


def aggregate(rows, key, skipped=None):
    cells = [row["metrics"][key] for row in rows]
    known = [cell for cell in cells if cell["denominator"] is not None]
    extra = {}
    if key in {"views", "dimensions"}:
        extra["uncertain"] = sum(cell.get("uncertain", 0) for cell in cells)
    if not known:
        reason = "; ".join(sorted({cell["reason"] for cell in cells}))
        return metric(reason=skipped or reason or "No selected cases.", **extra)
    if key == "pmi":
        extra = {count: sum(cell[count] for cell in known)
                 for count in ("read", "read_without_ink", "read_not_drawn",
                               "listed_not_shown", "unaccounted")}
    if key == "views":
        extra["by_kind"] = {
            kind: metric(sum(c["by_kind"][kind]["numerator"] for c in cells if "by_kind" in c),
                         sum(c["by_kind"][kind]["denominator"] for c in cells if "by_kind" in c))
            for kind in VIEW_KINDS
        }
    return metric(sum(cell["numerator"] for cell in known), sum(cell["denominator"] for cell in known),
                  measured_parts=len(known), unavailable_parts=len(cells) - len(known), **extra)


def display(key, cell, compact=False):
    n, d = cell["numerator"], cell["denominator"]
    uncertainty = f"; {cell['uncertain']} uncertain" if cell.get("uncertain") and not compact else ""
    if n is None:
        return "n/a" if compact else f"n/a — {cell['reason']}{uncertainty}"
    counts = f"{n}/{d}"
    if compact:
        return counts
    if key in {"pmi", "views", "complete", "dimensions", "tolerance", "hole_table"}:
        counts += f" ({100*n/d:.1f}%)"
    if key == "pmi":
        counts += (f"; {cell['read']} read; {cell['listed_not_shown']} listed as not shown; "
                   f"{cell['unaccounted']} unaccounted")
    if key in {"true", "redundancy"}:
        counts += " findings/parts" if key == "redundancy" else " affected/features"
    if cell.get("unavailable_parts") and key in {"pmi", "true", "complete", "redundancy"}:
        counts += f"; {cell['unavailable_parts']} cases unavailable"
    return counts + uncertainty


def show_cost(count):
    return "n/a" if count is None else str(count)


def print_report(report):
    print("| Metric | Target | NIST | Private | Overall |")
    print("|---|---|---|---|---|")
    for key, (label, target) in METRICS.items():
        values = [display(key, report["sets"][name]["metrics"][key])
                  for name in ("NIST", "Private", "Overall")]
        print(f"| {label} | {target} | " + " | ".join(values) + " |")
    for name in ("NIST", "Private"):
        group = report["sets"][name]
        print(f"\n{name} per part")
        if group.get("status"):
            print(group["status"])
        if name == "NIST" and report["missing_nist"]:
            print("Missing NIST cases: " + ", ".join(report["missing_nist"]))
        if not group["parts"]:
            continue
        print("| Case | " + " | ".join(METRICS) + " | Instructions retired |")
        print("|---|" + "---|" * (len(METRICS) + 1))
        for row in group["parts"]:
            numbers = [display(k, row["metrics"][k], compact=True) for k in METRICS]
            print(f"| {row['id']} | " + " | ".join(numbers) +
                  f" | {show_cost(row['instructions_retired'])} |")
        for row in group["parts"]:
            if row.get("error"):
                print(f"  {row['id']} {row['error']}")
            pmi = row["metrics"]["pmi"]
            if pmi["denominator"] is not None:
                print(f"  {row['id']} PMI: {pmi['read']} read, {pmi['read_without_ink']} without ink, "
                      f"{pmi['read_not_drawn']} without verified coverage, "
                      f"{pmi['listed_not_shown']} listed as not shown, "
                      f"{pmi['unaccounted']} unaccounted")
            if row.get("reference"):
                print(f"  {row['id']} public reference counts: " + ", ".join(
                    f"{key}={value}" for key, value in row["reference"].items()))
            by_kind = row["metrics"]["views"].get("by_kind")
            if by_kind:
                print(f"  {row['id']} views matched/expected: " + ", ".join(
                    f"{key}={display('views', value, compact=True)} ({value['uncertain']} uncertain)"
                    for key, value in by_kind.items()))
            uncertain = row["metrics"]["dimensions"].get("uncertain")
            if uncertain:
                print(f"  {row['id']} dimensions uncertain: {uncertain}")
            if row.get("engineer_general_tolerance"):
                cell = row["engineer_general_tolerance"]
                print(f"  {row['id']} general tolerance in reference: {cell['numerator']}/{cell['denominator']}")
    quality = report["quality"]
    if quality["parts"]:
        verdict = "PASS" if quality["passed"] else "FAIL"
        print(f"\n30-part quality gate: {verdict} ({display('quality', quality['metric'])} cells)")
        if quality.get("summary", {}).get("after"):
            print("Quality totals: " + ", ".join(f"{k}={v}" for k, v in quality["summary"]["after"].items()))
        for row in quality.get("regressions", []):
            print(f"  {row['id']} {row['metric']} {row['before']} -> {row['after']}")
        print("Quality per part (cells no worse/checked): " + ", ".join(
            f"{p['id']} {display('quality', p['metric'], compact=True)} "
            f"({show_cost(p['instructions_retired'])} instructions retired)" for p in quality["parts"]))
    print("Instructions retired: " + ", ".join(
        f"{key}={show_cost(value)}" for key, value in report["instructions_retired"].items()))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--nist", type=Path, metavar="DIR")
    parser.add_argument("--parts", nargs="+", metavar="ID")
    parser.add_argument("--json", type=Path, metavar="OUT")
    args = parser.parse_args()
    count_instructions = instruction_counter_available()
    if not os.environ.get("DRAFTWRIGHT_CORPUS"):
        parser.error("Set DRAFTWRIGHT_CORPUS to the public corpus root.")
    corpus = Path(os.environ["DRAFTWRIGHT_CORPUS"]).resolve()
    if not args.binary.is_file():
        parser.error("The draftwright binary is required.")
    binary = args.binary.resolve()
    nist, missing = [], []
    selected = set(args.parts or ())
    ref_root = Path(os.environ.get("REF", "nist-ref"))
    nist_dir = args.nist or ref_root / "ap242"
    for case_id in NIST_IDS:
        if selected and not any(
                key == case_id or key.startswith(f"nist_{case_id}_") for key in selected):
            continue
        if not nist_dir.is_dir():
            parser.error("Supply the NIST AP242 directory with --nist or REF/ap242.")
        matches = sorted(p for p in nist_dir.glob(f"nist_{case_id}_*")
                         if p.suffix.lower() in {".stp", ".step"} and "ap242" in p.stem.lower())
        if len(matches) > 1:
            parser.error(f"Ambiguous corpus exports for {case_id}.")
        if not matches:
            if not selected or case_id in selected:
                missing.append(case_id)
            continue
        if not selected or case_id in selected or matches[0].stem in selected:
            nist.append(Case(case_id, matches[0]))

    workbook = ref_root / "ftc-definitions" / "NIST-FTC-PMI-Definitions.xlsx"
    try:
        definitions = ftc_definitions(workbook) if workbook.is_file() else {}
    except (OSError, ValueError, KeyError, ET.ParseError, BadZipFile, StopIteration, AttributeError):
        parser.error("FTC definitions workbook could not be read.")
    manifest = os.environ.get("DRAFTWRIGHT_BENCH_PRIVATE")
    private, private_status, manifest_error = [], None, False
    if manifest:
        try:
            private = private_cases(Path(manifest).resolve())
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            manifest_error = True
            private_status = "Private manifest unavailable or invalid."
        private = [case for case in private if not selected or case.id in selected]
    else:
        private_status = "Skipped because DRAFTWRIGHT_BENCH_PRIVATE is unset."
    recognized = set(NIST_IDS) | {case.step.stem for case in nist} | {case.id for case in private}
    if selected - recognized and not manifest_error:
        parser.error("Unknown benchmark case ID in --parts.")
    with tempfile.TemporaryDirectory(prefix="draftwright-pro-bench-") as scratch:
        scratch = Path(scratch)
        public_cache = Path(os.environ.get("DRAFTWRIGHT_PLAN_CACHE", scratch / "public-cache")).resolve()
        private_cache = scratch / "private-cache"
        with ThreadPoolExecutor(max_workers=4) as pool:
            public_tasks = [pool.submit(measure, case, False, binary, public_cache, count_instructions)
                            for case in nist]
            private_tasks = [pool.submit(measure, case, True, binary, private_cache, count_instructions)
                             for case in private]
            nist_rows = [future.result() for future in public_tasks]
            private_rows = [future.result() for future in private_tasks]
        for case, row in zip(nist, nist_rows):
            row["reference"] = nist_reference(case, ref_root, definitions)
        if selected:
            quality = {"metric": na("Quality corpus skipped with --parts."), "parts": [], "passed": None}
        else:
            quality = quality_check(corpus, binary, public_cache, scratch / "quality", count_instructions)
    groups = {"NIST": nist_rows, "Private": private_rows, "Overall": nist_rows + private_rows}
    sets = {}
    for name, rows in groups.items():
        status = private_status if name == "Private" else None
        metrics = {key: aggregate(rows, key, status) for key in METRICS}
        metrics["quality"] = (quality["metric"] if name == "Overall" else
                              na("Measured across the separate quality corpus."))
        sets[name] = {"metrics": metrics, **({"parts": rows} if name != "Overall" else {})}
        if status:
            sets[name]["status"] = status
    if not workbook.is_file():
        sets["NIST"]["status"] = "FTC definitions workbook unavailable through REF."
    cost_sets = {"NIST": nist_rows, "Private": private_rows, "Quality": quality["parts"]}
    all_rows = groups["Overall"] + quality["parts"]
    report = {
        "sets": sets, "quality": quality, "missing_nist": missing,
        "instructions_retired": {**{name: cost_total(rows) for name, rows in cost_sets.items()},
                                 "Total": cost_total(all_rows)},
    }
    print_report(report)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return int(manifest_error or any("error" in row for row in groups["Overall"])
               or quality["passed"] is False)


if __name__ == "__main__":
    raise SystemExit(main())
