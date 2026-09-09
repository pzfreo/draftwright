"""Supported, deliberately preliminary two-sheet frame recipe for #1544.

Run from the repository root with the pinned fixture and an output directory.
Keep this recipe, its source STEP and the generated manifest together for replay.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from importlib.metadata import version
from math import isclose
from pathlib import Path

from draftwright import Document
from draftwright.model.ir import HoleFeature, PatternFeature

SOURCE_SHA256 = "078d0abd52b618a1a9180bb27a162a8fd1b8ae1a74a52571adfdbf3914f23d71"
SOURCE_REVISION = "pzfreo/whistle-key@76ccdc2"
STATUS = "QUOTATION / DFM REVIEW - NOT RELEASED"
SEGMENT_NOTE = "SIX BEARING SEGMENTS - FIT / PIN RETENTION TBD"
OPTIONS = {
    "page": "A2",
    "scale": 2,
    "projection": "third",
    "detail_view": False,
    "title": "THREE-KEY TITANIUM FRAME - DFM REVIEW",
    "material": "TI - TBD",
    "tolerance": "REVIEW ONLY",
    "revision": "P1",
    "date": "2026-09-09",
}


def operations(package):
    """Resolve current exact owners; never reuse a report's local IDs on replay."""
    groups = {"bolts": [], "sockets": [], "hinge": []}
    expected = {
        "bolts": ("z", 2.4, True, 1.6, 6),
        "sockets": ("x", 2.4, False, 1.5, 3),
        "hinge": ("y", 1.1, True, 53.2, 1),
    }
    for feature in package.features:
        bore = feature.member if isinstance(feature, PatternFeature) else feature
        if not isinstance(bore, HoleFeature):
            continue
        if any(
            getattr(bore, field) is not None
            for field in (
                "cbore",
                "spotface",
                "csink",
                "thread",
                "profile",
                "across_flats",
                "profile_direction",
            )
        ):
            raise ValueError("the pinned frame requires plain circular bore operations")
        matches = [
            name
            for name, (axis, diameter, through, depth, _count) in expected.items()
            if feature.frame.axis == bore.frame.axis == axis
            and isclose(bore.diameter, diameter, abs_tol=1e-6)
            and bore.through is through
            and bore.depth is not None
            and isclose(bore.depth, depth, abs_tol=1e-6)
        ]
        if len(matches) != 1:
            raise ValueError("an observed frame bore no longer matches its complete operation")
        groups[matches[0]].append(feature)
    for name, (_axis, _diameter, _through, _depth, count) in expected.items():
        if sum(feature.count for feature in groups[name]) != count:
            raise ValueError(f"the pinned frame requires {count} {name}")
    if len(groups["hinge"]) != 1:
        raise ValueError("the hinge has one recognized path, not six independent owners")
    return {name: tuple(features) for name, features in groups.items()}


def declare_document(source):
    source = Path(source)
    if hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError("this recipe requires the pinned datum-aligned frame STEP")
    package = Document.from_part(source)
    selected = operations(package)
    general = package.sheet("general", number="WK-TI-001", **OPTIONS)
    features = package.sheet("features", number="WK-TI-002", **OPTIONS)
    for sheet in (general, features):
        sheet.authored_dimensions().authored_views()
        for view in ("front", "plan", "side"):
            sheet.view(view)
    for feature in package.features:
        if feature.kind == "envelope":
            for parameter in feature.parameters():
                general.dimension(feature, parameter.parameter_id)
    general.section_view("A", at=0)
    for feature in selected["bolts"] + selected["hinge"] + selected["sockets"]:
        features.dimension(feature, "bore.diameter")
    for feature in selected["sockets"]:
        features.dimension(feature, "bore.depth")
    features.of(selected["hinge"][0]).note(SEGMENT_NOTE)
    features.schedule(
        [(feature, ("location",)) for feature in selected["bolts"] + selected["sockets"]],
        name="hole_locations",
        prefer="br",
    )
    features.notes(
        [
            "COORDINATES FROM THE MODEL MINIMUM X / Y / Z DATUM PLANES",
            "HINGE FIT / PIN RETENTION: ENGINEERING DECISION REQUIRED",
            "TITANIUM GRADE: ENGINEERING DECISION REQUIRED",
            STATUS,
        ]
    )
    general.table(
        [
            ["Operation", "Quantity", "Dimensions", "Status"],
            ["Underside hex pockets", "6", "SEE ENGINEERING INPUT", "UNSUPPORTED GRAMMAR"],
        ],
        name="pocket_schedule",
    )
    general.notes(["TITANIUM GRADE: ENGINEERING DECISION REQUIRED", STATUS])
    return package, {"general": general, "features": features}


def export_package(result, source, output):
    """Export drawing and diagnostic artifacts; never equate export success with release."""
    report = result.report()
    captured_source = report["source"]
    if captured_source["sha256"] != SOURCE_SHA256:
        raise ValueError("the retained document source does not match the pinned frame")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    paths = {}
    for name, drawing in result.sheets.items():
        paths[name] = {
            format: str(path)
            for format, path in drawing.export(
                str(output / name), formats=("pdf", "svg", "png"), reproducible=True
            ).items()
        }
    result.write_report(output / "document.json")
    manifest = {
        "source_revision": SOURCE_REVISION,
        "source_path": str(Path(source).resolve()),
        "source_sha256": captured_source["sha256"],
        "source": captured_source,
        "recipe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "versions": {
            name: version(name)
            for name in (
                "draftwright",
                "quiddity",
                "build123d",
                "build123d-drafting-helpers",
            )
        },
        "python": platform.python_version(),
        "platform": platform.platform(),
        "sheet_options": OPTIONS,
        "export_options": {"formats": ["pdf", "svg", "png"], "reproducible": True},
        "status": STATUS,
        "exports": paths,
        "report": "document.json",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    package, _sheets = declare_document(args.source)
    result = package.build()
    export_package(result, args.source, args.output)
    print(json.dumps(result.report()["assessment"], indent=2))


if __name__ == "__main__":
    main()
