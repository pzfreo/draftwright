"""Reproduce the #2233 quotation-sheet comparison on a public synthetic part.

Run from the repository root::

    python docs/examples/quotation_blind_hole.py --out-dir /tmp/draftwright-2233 \
        --date 2026-10-07 --sheet 1/1

The date and sheet are sample *supplied* metadata, not inferred from geometry.
No approval, material, or manufacturing-release status is invented.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from build123d import Align, Axis, Cylinder, Pos

from draftwright import Sheet


def turned_blind_hole():
    """A 50 mm turned shaft with a 24 mm deep axial blind bore."""
    align = (Align.CENTER, Align.CENTER, Align.MIN)

    def axial(radius: float, length: float):
        return Cylinder(radius, length, align=align).rotate(Axis.Y, 90)

    return (axial(10, 50) + axial(15, 8)) - Pos(26, 0, 0) * axial(3, 24)


def _sheet(part, *, date: str, sheet_number: str) -> tuple[Sheet, object]:
    sheet = Sheet.from_part(
        part,
        title="QUOTATION SPINDLE",
        number="Q-2233",
        document_type="QUOTATION",
        date=date,
        sheet=sheet_number,
        revision="",
        approved_by="",
    )
    hole = next(feature for feature in sheet.features if feature.kind == "hole")
    sheet.of(hole).fit("H7")  # supplied example requirement, not a geometry inference
    return sheet, hole


def baseline(part, *, date: str = "", sheet_number: str = ""):
    """Detected dimensions and default view planning: no X-axis blind-hole section."""
    sheet, _hole = _sheet(part, date=date, sheet_number=sheet_number)
    return sheet.build()


def section_with_iso(part, *, date: str = "", sheet_number: str = ""):
    """Keep automatic dimensions/views but require the axial section by feature."""
    sheet, hole = _sheet(part, date=date, sheet_number=sheet_number)
    sheet.take_over(dimensions="automatic", principal_views="automatic", derived_views="automatic")
    sheet.add_section_view("A", through=sheet.of(hole))
    return sheet.build()


def section_visible_iso(part, *, date: str = "", sheet_number: str = ""):
    """Keep the orientation view but hide edges already explained by the section."""
    sheet, hole = _sheet(part, date=date, sheet_number=sheet_number)
    sheet.take_over(dimensions="automatic", principal_views="automatic", derived_views="automatic")
    sheet.add_view("iso").hidden_lines(False)
    sheet.add_section_view("A", through=sheet.of(hole))
    return sheet.build()


def measurement_ledger(drawing) -> Counter:
    """Compare measured facts, not annotation names or rendered glyphs."""
    facts: Counter = Counter()
    for name in drawing.registry.names():
        for identity in drawing.registry.measurement_of(name):
            value = next(
                parameter.value
                for parameter in identity.feature.parameters()
                if parameter.parameter_id == identity.parameter
            )
            facts[(identity.feature.kind, identity.parameter, round(value, 6))] += 1
    return facts


def summary(drawing, *, date: str, sheet_number: str) -> dict:
    block = drawing.get_annotation("title_block")
    fields = {name: value for name, value, *_rest in block.title_field_specs}
    ledger = measurement_ledger(drawing)
    report = drawing.report()
    completeness = report["lint"]["quality"]["completeness"]
    iso = drawing.views.get("iso")
    return {
        "views": list(drawing.views),
        "section_decision": drawing.section_decision,
        "iso_hidden_edges": len(iso[1].edges()) if iso and iso[1] else 0,
        "measurements": [
            {"feature": kind, "parameter": parameter, "value": value, "count": count}
            for (kind, parameter, value), count in sorted(ledger.items())
        ],
        "fit_labels": [
            drawing.registry.named(name).label
            for name in sorted(drawing.registry.names())
            if "H7" in getattr(drawing.registry.named(name), "label", "")
        ],
        "title_fields": fields,
        "missing_supplied_metadata": [
            field for field, supplied in (("date", date), ("sheet", sheet_number)) if not supplied
        ],
        "lint_by_code": drawing.lint_summary()["by_code"],
        "audited_completeness": {
            "scope": completeness["scope"],
            "coverage": completeness["coverage"],
            "requirements": completeness["requirements"],
            "placed": completeness["placed"],
            "dropped": completeness["dropped"],
            "missing": completeness["missing"],
        },
        "page": report["layout"]["page"],
    }


def write_comparison(out_dir: Path, *, date: str = "", sheet_number: str = "") -> dict:
    """Write comparable PDFs, full reports and an explicit decision/coverage ledger."""
    out_dir.mkdir(parents=True, exist_ok=True)
    part = turned_blind_hole()
    drawings = {
        "baseline": baseline(part, date=date, sheet_number=sheet_number),
        "section-with-iso": section_with_iso(part, date=date, sheet_number=sheet_number),
        "section-visible-iso": section_visible_iso(part, date=date, sheet_number=sheet_number),
    }
    expected = measurement_ledger(drawings["baseline"])
    for name, drawing in drawings.items():
        if name != "baseline" and (
            drawing.section_decision["status"] != "placed" or "section_aa" not in drawing.views
        ):
            raise RuntimeError(
                f"required axial section was not placed: {drawing.section_decision}"
            )
        if measurement_ledger(drawing) != expected:
            raise RuntimeError(f"{name} changed the measured requirement set")
        if len(summary(drawing, date=date, sheet_number=sheet_number)["fit_labels"]) != 1:
            raise RuntimeError(f"{name} lost the supplied H7 bore requirement")
        drawing.export(str(out_dir / name), formats=("pdf", "png"))
        drawing.write_report(out_dir / f"{name}.draftwright.json")
    comparison = {
        "source": "public programmatic 50 mm turned shaft with axial blind bore",
        "status": "QUOTATION / DFM REVIEW - NOT RELEASED FOR MANUFACTURE",
        "original_quotation_source_available": False,
        "variants": {
            name: summary(drawing, date=date, sheet_number=sheet_number)
            for name, drawing in drawings.items()
        },
        "redundancy_review": (
            "The baseline prints head outside diameter 30 and overall height 30, which repeat "
            "the same radial extent on this turned fixture. It prints 8 and 42 axial lengths "
            "but no 50 overall length, so that chain is not closed by a third printed value. "
            "No dimension was removed automatically; functional priority needs author review (#941)."
        ),
        "isometric_policy": (
            "The section-visible-iso variant retains the optional orientation view but suppresses "
            "its hidden edges. Orthographic and section measurements remain unchanged."
        ),
    }
    (out_dir / "comparison.json").write_text(
        json.dumps(comparison, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    return comparison


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--date", default="", help="Supplied title-block date; blank if unknown")
    parser.add_argument("--sheet", default="", help="Supplied sheet field; blank if unknown")
    args = parser.parse_args()
    result = write_comparison(args.out_dir, date=args.date, sheet_number=args.sheet)
    print(json.dumps({name: item["views"] for name, item in result["variants"].items()}))


if __name__ == "__main__":
    main()
