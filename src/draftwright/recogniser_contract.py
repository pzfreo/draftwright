"""Fail-closed Draftwright policy for the installed recogniser capability manifest.

The geometry package says what it can prove.  This module is the separate consumer-owned
declaration of what Draftwright does with that evidence.  Keeping the declarations here prevents
recognition policy from leaking into ``b123d-recognisers`` and gives CI one exhaustive join point.
"""

from __future__ import annotations

import copy
import importlib
import re
from dataclasses import dataclass
from importlib.metadata import version as distribution_version
from pathlib import Path
from typing import Any

from b123d_recognisers import capability_manifest

CONSUMER_CAPABILITY_FORMAT = "draftwright-recogniser-capabilities"
CONSUMER_CAPABILITY_FORMAT_VERSION = 1
_RECOGNISER_DISTRIBUTION = "b123d-recognisers"
_BOUNDARIES = (
    "ir_adapter",
    "dsl_declaration",
    "generated_code",
    "drawing_consumer",
    "completeness",
    "documentation",
)
_STATES = {"deferred", "not-applicable", "supported", "unsupported"}
_IMPLEMENTATION = re.compile(r"^draftwright(?:\.[A-Za-z_]\w*)+$")
_TRACKING = re.compile(
    r"^https://github\.com/pzfreo/(?:draftwright|b123d-recognisers)/issues/\d+$"
)


class RecogniserCapabilityError(RuntimeError):
    """The installed geometry contract and Draftwright policy cannot be safely joined."""


@dataclass(frozen=True)
class _FamilySpec:
    records: tuple[str, ...]
    ir: str
    dsl: str
    drawing: str


_FAMILIES: dict[str, _FamilySpec] = {
    "bosses": _FamilySpec(("BossRecord",), "_convert_boss", "boss", "render_boss_diameters"),
    "chamfers": _FamilySpec(("Chamfer",), "_convert_chamfer", "chamfer", "render_chamfers"),
    "channels": _FamilySpec(("Channel",), "_convert_channel", "channel", "render_slots"),
    "circular-blind-steps": _FamilySpec(
        ("CircularBlindStep",),
        "_convert_circular_blind_step",
        "circular_blind_step",
        "render_circular_blind_steps",
    ),
    "countersinks": _FamilySpec(("CounterSink",), "build_part_model", "hole", "_annotate_holes"),
    "double-d-bores": _FamilySpec(
        ("DoubleDBore",), "_convert_double_d_bore", "double_d_bore", "_annotate_holes"
    ),
    "face-levels": _FamilySpec(
        ("FaceLevel",), "build_part_model", "step_level", "render_height_ladder"
    ),
    "fillets": _FamilySpec(("Fillet",), "_convert_fillet", "fillet", "render_fillets"),
    "flats": _FamilySpec(("Flat",), "_convert_flat", "flat", "render_flats"),
    "grooves": _FamilySpec(("Groove",), "_convert_groove", "groove", "render_grooves"),
    "hole-patterns": _FamilySpec(
        ("BoltCircle", "LinearArray", "RectGrid"),
        "_pattern_feature",
        "pattern",
        "_annotate_holes",
    ),
    "holes": _FamilySpec(
        ("CounterBore", "HoleRecord", "HoleSpec"), "_member_hole", "hole", "_annotate_holes"
    ),
    "plates": _FamilySpec(("Plate",), "_convert_plate", "plate", "render_plates"),
    "pocket-patterns": _FamilySpec(
        ("PocketArray", "PocketGrid"),
        "_pocket_pattern_feature",
        "pocket_pattern",
        "render_pocket_patterns",
    ),
    "paired-ramp-steps": _FamilySpec(
        ("PairedRampStep",),
        "_convert_paired_ramp_step",
        "paired_ramp_step",
        "render_paired_ramp_steps",
    ),
    "pockets": _FamilySpec(("Pocket",), "_convert_pocket", "pocket", "render_pockets"),
    "polygonal-bosses": _FamilySpec(
        ("PolygonalBoss",), "_convert_polygonal_boss", "polygonal_boss", "render_polygonal_bosses"
    ),
    "polygonal-stock": _FamilySpec(
        ("PolygonalStock",),
        "_convert_polygonal_stock",
        "polygonal_stock",
        "render_polygonal_stock",
    ),
    "rectangular-pads": _FamilySpec(("RaisedPad",), "_convert_pad", "pad", "render_slots"),
    "rectangular-blind-slots": _FamilySpec(
        ("RectangularBlindSlot",),
        "_convert_rectangular_blind_slot",
        "rectangular_blind_slot",
        "render_rectangular_blind_slots",
    ),
    "round-bottom-blind-slots": _FamilySpec(
        ("RoundBottomBlindSlot",),
        "_convert_round_bottom_blind_slot",
        "round_bottom_blind_slot",
        "render_round_bottom_blind_slots",
    ),
    "risers": _FamilySpec(
        ("RiserEvidence", "StepShoulder"),
        "build_part_model",
        "step_level",
        "render_step_positions",
    ),
    "slot-patterns": _FamilySpec(
        ("SlotArray", "SlotGrid"),
        "_slot_pattern_feature",
        "slot_pattern",
        "render_slot_patterns",
    ),
    "slots": _FamilySpec(("Slot",), "_convert_slot", "slot", "render_slots"),
    "through-steps": _FamilySpec(
        ("ThroughStep",), "_convert_through_step", "through_step", "render_through_steps"
    ),
    "turned-steps": _FamilySpec(
        ("TurnedProfile", "TurnedProfileKey", "TurnedStep"),
        "_convert_step",
        "step",
        "render_step_lengths",
    ),
}

# Record schemas are explicit and family-specific. The exact dependency pin selects the installed
# package version; this table records only the schemas its Draftwright adapters actually consume.
_RECORD_SCHEMA_VERSIONS: dict[tuple[str, str], tuple[int, ...]] = {
    ("chamfers", "Chamfer"): (2,),
    ("fillets", "Fillet"): (2,),
    ("rectangular-pads", "RaisedPad"): (2,),
    ("risers", "RiserEvidence"): (2,),
    ("turned-steps", "TurnedProfile"): (2,),
    ("turned-steps", "TurnedProfileKey"): (1,),
    ("turned-steps", "TurnedStep"): (2,),
}


def _record_schema_versions(family_id: str, names: tuple[str, ...]) -> dict[str, list[int]]:
    return {name: list(_RECORD_SCHEMA_VERSIONS.get((family_id, name), (1,))) for name in names}


def _supported(implementation: str, evidence: str) -> dict[str, Any]:
    return {"state": "supported", "implementation": implementation, "evidence": [evidence]}


_COMPLETENESS_TRACKING = {
    "chamfers": 1374,
    "channels": 1371,
    "countersinks": 1370,
    "double-d-bores": 1370,
    "face-levels": 1373,
    "fillets": 1374,
    "flats": 1371,
    "grooves": 1372,
    "hole-patterns": 1370,
    "holes": 1369,
    "plates": 1373,
    "pocket-patterns": 1372,
    "pockets": 1372,
    "polygonal-bosses": 1372,
    "polygonal-stock": 1371,
    "rectangular-pads": 1372,
    "rectangular-blind-slots": 1421,
    "risers": 1373,
    "slot-patterns": 1371,
    "slots": 1371,
    "turned-steps": 1374,
}


def _deferred_completeness(family_id: str) -> dict[str, Any]:
    return {
        "state": "deferred",
        "rationale": (
            "The feature is consumed for drafting, but evidence-based completeness scoring is "
            "being specified separately rather than inferred from annotation presence."
        ),
        "tracking": (
            f"https://github.com/pzfreo/draftwright/issues/{_COMPLETENESS_TRACKING[family_id]}"
        ),
    }


def _family_declaration(family_id: str, spec: _FamilySpec) -> dict[str, Any]:
    if family_id == "bosses":
        completeness = _supported(
            "draftwright.linting.coverage.lint_boss_height_coverage",
            "tests/test_issue_885_prismatic_coverage.py",
        )
    elif family_id == "holes":
        completeness = _supported(
            "draftwright.evaluation.step_analysis.evaluate_step_corpus",
            "tests/test_issue_1369_hole_completeness_evidence.py",
        )
    elif family_id == "chamfers":
        completeness = _supported(
            "draftwright.evaluation.step_analysis.evaluate_step_corpus",
            "tests/test_issue_1374_chamfer_completeness_evidence.py",
        )
    elif family_id == "countersinks":
        completeness = _supported(
            "draftwright.evaluation.step_analysis.evaluate_step_corpus",
            "tests/test_issue_1370_countersink_completeness_evidence.py",
        )
    elif family_id == "double-d-bores":
        completeness = _supported(
            "draftwright.evaluation.step_analysis.evaluate_step_corpus",
            "tests/test_issue_1370_double_d_completeness_evidence.py",
        )
    elif family_id == "hole-patterns":
        completeness = _supported(
            "draftwright.evaluation.step_analysis.evaluate_step_corpus",
            "tests/test_issue_1370_hole_pattern_completeness_evidence.py",
        )
    elif family_id == "plates":
        completeness = _supported(
            "draftwright.evaluation.step_analysis.evaluate_step_corpus",
            "tests/test_issue_1373_plate_completeness_evidence.py",
        )
    elif family_id == "flats":
        completeness = _supported(
            "draftwright.evaluation.step_analysis.evaluate_step_corpus",
            "tests/test_issue_1371_flat_completeness_evidence.py",
        )
    elif family_id == "fillets":
        completeness = _supported(
            "draftwright.evaluation.step_analysis.evaluate_step_corpus",
            "tests/test_issue_1374_fillet_completeness_evidence.py",
        )
    elif family_id == "turned-steps":
        completeness = _supported(
            "draftwright.evaluation.step_analysis.evaluate_step_corpus",
            "tests/test_issue_1374_turned_step_completeness_evidence.py",
        )
    elif family_id == "grooves":
        completeness = _supported(
            "draftwright.evaluation.step_analysis.evaluate_step_corpus",
            "tests/test_issue_1372_groove_completeness_evidence.py",
        )
    elif family_id == "pockets":
        completeness = _supported(
            "draftwright.evaluation.step_analysis.evaluate_step_corpus",
            "tests/test_issue_1372_pocket_completeness_evidence.py",
        )
    elif family_id == "pocket-patterns":
        completeness = _supported(
            "draftwright.evaluation.step_analysis.evaluate_step_corpus",
            "tests/test_issue_1372_pocket_pattern_completeness_evidence.py",
        )
    elif family_id == "rectangular-pads":
        completeness = _supported(
            "draftwright.evaluation.step_analysis.evaluate_step_corpus",
            "tests/test_issue_1372_pad_completeness_evidence.py",
        )
    elif family_id == "polygonal-bosses":
        completeness = _supported(
            "draftwright.evaluation.step_analysis.evaluate_step_corpus",
            "tests/test_issue_1372_polygonal_boss_completeness_evidence.py",
        )
    elif family_id == "polygonal-stock":
        completeness = _supported(
            "draftwright.evaluation.step_analysis.evaluate_step_corpus",
            "tests/test_issue_1371_polygonal_stock_completeness_evidence.py",
        )
    elif family_id == "paired-ramp-steps":
        completeness = _supported(
            "draftwright.linting.paired_ramp_step_coverage.lint_paired_ramp_step_coverage",
            "tests/test_issue_1382_paired_ramp_semantics.py",
        )
    elif family_id == "circular-blind-steps":
        completeness = _supported(
            "draftwright.linting.circular_blind_step_coverage.lint_circular_blind_step_coverage",
            "tests/test_issue_1382_circular_blind_step_semantics.py",
        )
    elif family_id == "rectangular-blind-slots":
        completeness = _supported(
            "draftwright.linting.rectangular_blind_slot_coverage."
            "lint_rectangular_blind_slot_coverage",
            "tests/test_issue_1421_rectangular_blind_slot_completeness.py",
        )
    elif family_id == "round-bottom-blind-slots":
        completeness = _supported(
            "draftwright.linting.round_bottom_blind_slot_coverage."
            "lint_round_bottom_blind_slot_coverage",
            "tests/test_issue_1421_round_bottom_blind_slot_completeness.py",
        )
    elif family_id == "through-steps":
        completeness = _supported(
            "draftwright.linting.through_step_coverage.lint_through_step_coverage",
            "tests/test_issue_1382_through_step_semantics.py",
        )
    else:
        completeness = _deferred_completeness(family_id)
    return {
        "id": family_id,
        "record_schemas": _record_schema_versions(family_id, spec.records),
        "disposition": "supported",
        "ir_adapter": _supported(
            f"draftwright.model.detect.{spec.ir}", "tests/test_detect_registry.py"
        ),
        "dsl_declaration": _supported(
            f"draftwright.sheet.Sheet.{spec.dsl}", "tests/test_declare.py"
        ),
        "generated_code": _supported(
            "draftwright.sheet_emit._feature_line", "tests/test_sheet_emit.py"
        ),
        "drawing_consumer": _supported(
            (
                f"draftwright.annotations.holes.{spec.drawing}"
                if spec.drawing
                in {"_annotate_holes", "render_pocket_patterns", "render_slot_patterns"}
                else f"draftwright.annotations.from_model.{spec.drawing}"
            ),
            "tests/test_make_drawing.py",
        ),
        "completeness": completeness,
        "documentation": {
            "state": "supported",
            "evidence": [
                "docs/reference/recogniser-capabilities.md",
                "docs/reference/sheet.md",
            ],
        },
    }


def _geometry_only_declaration() -> dict[str, Any]:
    rationale = (
        "Independent repeating-profile evidence critiques a separately authored gear; geometry "
        "alone must not create inferred gear intent."
    )
    no_inferred = {
        "state": "not-applicable",
        "rationale": "No inferred drafting feature exists for geometry-only critique evidence.",
    }
    return {
        "id": "repeating-radial-profiles",
        "record_schemas": _record_schema_versions(
            "repeating-radial-profiles", ("RepeatingRadialProfile",)
        ),
        "disposition": "geometry-only",
        "rationale": rationale,
        "package_evidence": ["tests/golden/repeating_radial_profile/expected.json"],
        "ir_adapter": copy.deepcopy(no_inferred),
        "dsl_declaration": copy.deepcopy(no_inferred),
        "generated_code": copy.deepcopy(no_inferred),
        "drawing_consumer": copy.deepcopy(no_inferred),
        "completeness": _supported(
            "draftwright.linting.gear_coverage.lint_declared_gear_coverage",
            "tests/test_issue_1086_declared_gears.py",
        ),
        "documentation": {
            "state": "supported",
            "evidence": [
                "docs/reference/recogniser-capabilities.md",
                "docs/research/1062-repeating-radial-profile-evidence.md",
            ],
        },
    }


#: Families the installed package proves but Draftwright does not fully support, each with the
#: issue recording its consumer disposition. Not a parking bay: an undecided family must still
#: have a live decision issue, while a settled unsupported boundary must carry an explicit
#: outcome such as Passage completeness below. ``pending_family_declarations`` reports anything
#: absent from BOTH this map and ``_FAMILIES``, so the next new family fails closed exactly as
#: the first three did (#1244), and the 0.4.6 step families do now (#1382).
_UNSUPPORTED: dict[str, tuple[tuple[str, ...], str, str]] = {
    "passages": (
        (
            "Passage",
            "PassageEnds",
            "PassageFrame",
            "PassageSection",
            "PassageSectionVertex",
            "SectionPassage",
        ),
        "https://github.com/pzfreo/draftwright/issues/1245",
        "A prismatic through-opening — the internal counterpart to polygonal stock. The rich "
        "record permits arbitrary line/arc sections, so treating its regular-polygon subset as "
        "a supported IR feature or HEX callout would overstate the drawing contract. Draftwright "
        "therefore reports every occurrence as an unsupported completeness requirement.",
    ),
    "prismatic-pockets": (
        ("PrismaticPocket",),
        "https://github.com/pzfreo/draftwright/issues/1246",
        "The aggregate removes candidates also owned by the supported `pockets` family, so each "
        "remaining PrismaticPocket is a distinct recess not superseded by Pocket. Its section may "
        "be any planar polygon: width/length is false for triangles and a regular-polygon A/F "
        "callout is incomplete in the general case. Draftwright therefore reports every "
        "surviving occurrence as an unsupported completeness requirement.",
    ),
    "angled-steps": (
        ("AngledStep",),
        "https://github.com/pzfreo/draftwright/issues/1247",
        "Introduced by 0.2.5 to stop `recognise_chamfers` reporting step slants as chamfers "
        "(precision 44% -> 78%). The aggregate reconciles the shared slanted face in favour of "
        "AngledStep, but its angle, legs and run length do not themselves decide which drawing "
        "requirements or section/detail view are required. Draftwright therefore reports every "
        "occurrence as an unsupported completeness requirement.",
    ),
}

_DEFERRED_FAMILIES: frozenset[str] = frozenset()


def _unsupported_declaration(family_id: str) -> dict[str, Any]:
    """A family the package proves and this consumer does not fully support.

    An undecided family stays ``deferred`` at every semantic boundary. Once review settles on an
    unsupported consumer boundary, drafting stages become ``unsupported`` and completeness either
    stays deferred or carries an explicit unsupported outcome. That distinction keeps the inventory
    visible without inventing drafting semantics.
    """
    records, tracking, rationale = _UNSUPPORTED[family_id]
    if family_id in _DEFERRED_FAMILIES:
        deferred = {
            "state": "deferred",
            "rationale": rationale,
            "tracking": tracking,
        }
        return {
            "id": family_id,
            "record_schemas": _record_schema_versions(family_id, records),
            "disposition": "deferred",
            "rationale": rationale,
            "tracking": tracking,
            "ir_adapter": copy.deepcopy(deferred),
            "dsl_declaration": copy.deepcopy(deferred),
            "generated_code": copy.deepcopy(deferred),
            "drawing_consumer": copy.deepcopy(deferred),
            "completeness": copy.deepcopy(deferred),
            "documentation": {
                "state": "supported",
                "evidence": ["docs/reference/recogniser-capabilities.md"],
            },
        }
    unsupported = {"state": "unsupported", "rationale": rationale}
    unsupported_completeness = {
        "angled-steps": (
            "Every aggregate-reconciled AngledStep occurrence produces a warning and an "
            "unsupported completeness outcome; no angle/run drafting grammar is invented."
        ),
        "passages": (
            "Every authoritative SectionPassage occurrence produces a warning and an "
            "unsupported completeness outcome; no drafting requirement is invented."
        ),
        "prismatic-pockets": (
            "Every aggregate-reconciled PrismaticPocket occurrence produces a warning and an "
            "unsupported completeness outcome; no polygonal drafting grammar is invented."
        ),
    }
    completeness = (
        {
            "state": "unsupported",
            "rationale": unsupported_completeness[family_id],
        }
        if family_id in unsupported_completeness
        else {
            "state": "deferred",
            "rationale": rationale,
            "tracking": tracking,
        }
    )
    return {
        "id": family_id,
        "record_schemas": _record_schema_versions(family_id, records),
        "disposition": "unsupported",
        "rationale": rationale,
        "tracking": tracking,
        "ir_adapter": copy.deepcopy(unsupported),
        "dsl_declaration": copy.deepcopy(unsupported),
        "generated_code": copy.deepcopy(unsupported),
        "drawing_consumer": copy.deepcopy(unsupported),
        "completeness": completeness,
        "documentation": {
            "state": "supported",
            "evidence": ["docs/reference/recogniser-capabilities.md"],
        },
    }


def consumer_capability_declaration() -> dict[str, Any]:
    """Return an isolated format-1 declaration for the installed package contract."""
    families = [_family_declaration(key, value) for key, value in sorted(_FAMILIES.items())]
    families.extend(_unsupported_declaration(key) for key in sorted(_UNSUPPORTED))
    families.append(_geometry_only_declaration())
    families.sort(key=lambda family: family["id"])
    return {
        "format": CONSUMER_CAPABILITY_FORMAT,
        "format_version": CONSUMER_CAPABILITY_FORMAT_VERSION,
        "consumer": {"name": "draftwright", "version": distribution_version("draftwright")},
        "package_compatibility": {
            "distribution": _RECOGNISER_DISTRIBUTION,
            "version": f"=={distribution_version(_RECOGNISER_DISTRIBUTION)}",
            "manifest_format": 2,
        },
        "families": families,
        "transitions": [
            {
                "boundary": "completeness",
                "compatibility_evidence": [
                    "tests/test_issue_1247_angled_step_disposition.py",
                    "tests/test_recogniser_capabilities.py",
                ],
                "family": "angled-steps",
                "from": "deferred",
                "release_notes": "CHANGELOG.md",
                "to": "unsupported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "completeness",
                "compatibility_evidence": [
                    "tests/test_issue_1374_chamfer_completeness_evidence.py",
                    "tests/test_recogniser_capabilities.py",
                    "tests/test_step_analysis_evaluation.py",
                ],
                "family": "chamfers",
                "from": "deferred",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "completeness",
                "compatibility_evidence": [
                    "tests/test_issue_1382_circular_blind_step_semantics.py",
                    "tests/test_recogniser_capabilities.py",
                ],
                "family": "circular-blind-steps",
                "from": "deferred",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "drawing_consumer",
                "compatibility_evidence": [
                    "tests/test_issue_1382_circular_blind_step_semantics.py",
                    "tests/test_recogniser_capabilities.py",
                ],
                "family": "circular-blind-steps",
                "from": "unsupported",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "dsl_declaration",
                "compatibility_evidence": [
                    "tests/test_issue_1382_circular_blind_step_semantics.py",
                    "tests/test_recogniser_capabilities.py",
                ],
                "family": "circular-blind-steps",
                "from": "unsupported",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "generated_code",
                "compatibility_evidence": [
                    "tests/test_issue_1382_circular_blind_step_semantics.py",
                    "tests/test_recogniser_capabilities.py",
                ],
                "family": "circular-blind-steps",
                "from": "unsupported",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "ir_adapter",
                "compatibility_evidence": [
                    "tests/test_issue_1382_circular_blind_step_semantics.py",
                    "tests/test_recogniser_capabilities.py",
                ],
                "family": "circular-blind-steps",
                "from": "unsupported",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "completeness",
                "compatibility_evidence": [
                    "tests/test_issue_1374_fillet_completeness_evidence.py",
                    "tests/test_recogniser_capabilities.py",
                    "tests/test_step_analysis_evaluation.py",
                ],
                "family": "fillets",
                "from": "deferred",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "completeness",
                "compatibility_evidence": [
                    "tests/test_flat_completeness.py",
                    "tests/test_flat_stock_identity.py",
                    "tests/test_issue_1371_flat_completeness_evidence.py",
                    "tests/test_recogniser_capabilities.py",
                    "tests/test_step_analysis_evaluation.py",
                ],
                "family": "flats",
                "from": "deferred",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "completeness",
                "compatibility_evidence": [
                    "tests/test_issue_1372_groove_completeness_evidence.py",
                    "tests/test_recogniser_capabilities.py",
                    "tests/test_step_analysis_evaluation.py",
                ],
                "family": "grooves",
                "from": "deferred",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "completeness",
                "compatibility_evidence": [
                    "tests/test_issue_1370_hole_pattern_completeness_evidence.py",
                    "tests/test_recogniser_capabilities.py",
                    "tests/test_step_analysis_evaluation.py",
                ],
                "family": "hole-patterns",
                "from": "deferred",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "completeness",
                "compatibility_evidence": [
                    "tests/test_issue_1369_hole_completeness_evidence.py",
                    "tests/test_recogniser_capabilities.py",
                    "tests/test_step_analysis_evaluation.py",
                ],
                "family": "holes",
                "from": "deferred",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "completeness",
                "compatibility_evidence": [
                    "tests/test_issue_1382_paired_ramp_semantics.py",
                    "tests/test_recogniser_capabilities.py",
                ],
                "family": "paired-ramp-steps",
                "from": "deferred",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "completeness",
                "compatibility_evidence": [
                    "tests/test_issue_1245_passage_disposition.py",
                    "tests/test_recogniser_capabilities.py",
                ],
                "family": "passages",
                "from": "deferred",
                "release_notes": "CHANGELOG.md",
                "to": "unsupported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "completeness",
                "compatibility_evidence": [
                    "tests/test_issue_1373_plate_completeness_evidence.py",
                    "tests/test_recogniser_capabilities.py",
                    "tests/test_step_analysis_evaluation.py",
                ],
                "family": "plates",
                "from": "deferred",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "completeness",
                "compatibility_evidence": [
                    "tests/test_issue_1372_pocket_pattern_completeness_evidence.py",
                    "tests/test_recogniser_capabilities.py",
                    "tests/test_step_analysis_evaluation.py",
                ],
                "family": "pocket-patterns",
                "from": "deferred",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "completeness",
                "compatibility_evidence": [
                    "tests/test_issue_1372_pocket_completeness_evidence.py",
                    "tests/test_recogniser_capabilities.py",
                    "tests/test_step_analysis_evaluation.py",
                ],
                "family": "pockets",
                "from": "deferred",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "completeness",
                "compatibility_evidence": [
                    "tests/test_issue_1372_polygonal_boss_completeness_evidence.py",
                    "tests/test_recogniser_capabilities.py",
                    "tests/test_step_analysis_evaluation.py",
                ],
                "family": "polygonal-bosses",
                "from": "deferred",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "completeness",
                "compatibility_evidence": [
                    "tests/test_issue_1371_polygonal_stock_completeness_evidence.py",
                    "tests/test_recogniser_capabilities.py",
                    "tests/test_step_analysis_evaluation.py",
                ],
                "family": "polygonal-stock",
                "from": "deferred",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "completeness",
                "compatibility_evidence": [
                    "tests/test_issue_1246_prismatic_pocket_disposition.py",
                    "tests/test_recogniser_capabilities.py",
                ],
                "family": "prismatic-pockets",
                "from": "deferred",
                "release_notes": "CHANGELOG.md",
                "to": "unsupported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "completeness",
                "compatibility_evidence": [
                    "tests/test_issue_1421_rectangular_blind_slot_completeness.py",
                    "tests/test_recogniser_capabilities.py",
                ],
                "family": "rectangular-blind-slots",
                "from": "deferred",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "drawing_consumer",
                "compatibility_evidence": [
                    "tests/test_issue_1421_rectangular_blind_slot_semantics.py",
                    "tests/test_recogniser_capabilities.py",
                ],
                "family": "rectangular-blind-slots",
                "from": "deferred",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "dsl_declaration",
                "compatibility_evidence": [
                    "tests/test_issue_1421_rectangular_blind_slot_semantics.py",
                    "tests/test_recogniser_capabilities.py",
                ],
                "family": "rectangular-blind-slots",
                "from": "deferred",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "generated_code",
                "compatibility_evidence": [
                    "tests/test_issue_1421_rectangular_blind_slot_semantics.py",
                    "tests/test_recogniser_capabilities.py",
                ],
                "family": "rectangular-blind-slots",
                "from": "deferred",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "ir_adapter",
                "compatibility_evidence": [
                    "tests/test_issue_1421_rectangular_blind_slot_semantics.py",
                    "tests/test_recogniser_capabilities.py",
                ],
                "family": "rectangular-blind-slots",
                "from": "deferred",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "completeness",
                "compatibility_evidence": [
                    "tests/test_issue_1372_pad_completeness_evidence.py",
                    "tests/test_recogniser_capabilities.py",
                    "tests/test_step_analysis_evaluation.py",
                ],
                "family": "rectangular-pads",
                "from": "deferred",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "completeness",
                "compatibility_evidence": [
                    "tests/test_issue_1421_round_bottom_blind_slot_completeness.py",
                    "tests/test_recogniser_capabilities.py",
                ],
                "family": "round-bottom-blind-slots",
                "from": "deferred",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "drawing_consumer",
                "compatibility_evidence": [
                    "tests/test_issue_1421_round_bottom_blind_slot_semantics.py",
                    "tests/test_recogniser_capabilities.py",
                ],
                "family": "round-bottom-blind-slots",
                "from": "deferred",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "dsl_declaration",
                "compatibility_evidence": [
                    "tests/test_issue_1421_round_bottom_blind_slot_semantics.py",
                    "tests/test_recogniser_capabilities.py",
                ],
                "family": "round-bottom-blind-slots",
                "from": "deferred",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "generated_code",
                "compatibility_evidence": [
                    "tests/test_issue_1421_round_bottom_blind_slot_semantics.py",
                    "tests/test_recogniser_capabilities.py",
                ],
                "family": "round-bottom-blind-slots",
                "from": "deferred",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "ir_adapter",
                "compatibility_evidence": [
                    "tests/test_issue_1421_round_bottom_blind_slot_semantics.py",
                    "tests/test_recogniser_capabilities.py",
                ],
                "family": "round-bottom-blind-slots",
                "from": "deferred",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "completeness",
                "compatibility_evidence": [
                    "tests/test_issue_1382_through_step_semantics.py",
                    "tests/test_recogniser_capabilities.py",
                ],
                "family": "through-steps",
                "from": "deferred",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "drawing_consumer",
                "compatibility_evidence": [
                    "tests/test_issue_1382_through_step_semantics.py",
                    "tests/test_recogniser_capabilities.py",
                ],
                "family": "through-steps",
                "from": "unsupported",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "dsl_declaration",
                "compatibility_evidence": [
                    "tests/test_issue_1382_through_step_semantics.py",
                    "tests/test_recogniser_capabilities.py",
                ],
                "family": "through-steps",
                "from": "unsupported",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "generated_code",
                "compatibility_evidence": [
                    "tests/test_issue_1382_through_step_semantics.py",
                    "tests/test_recogniser_capabilities.py",
                ],
                "family": "through-steps",
                "from": "unsupported",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "ir_adapter",
                "compatibility_evidence": [
                    "tests/test_issue_1382_through_step_semantics.py",
                    "tests/test_recogniser_capabilities.py",
                ],
                "family": "through-steps",
                "from": "unsupported",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
            {
                "boundary": "completeness",
                "compatibility_evidence": [
                    "tests/test_issue_1374_turned_step_completeness_evidence.py",
                    "tests/test_recogniser_capabilities.py",
                    "tests/test_step_analysis_evaluation.py",
                ],
                "family": "turned-steps",
                "from": "deferred",
                "release_notes": "CHANGELOG.md",
                "to": "supported",
                "version": distribution_version("draftwright"),
            },
        ],
    }


def _resolve_implementation(reference: str) -> object:
    if not _IMPLEMENTATION.fullmatch(reference):
        raise RecogniserCapabilityError(
            f"invalid Draftwright implementation reference {reference!r}"
        )
    parts = reference.split(".")
    for split in range(len(parts), 0, -1):
        try:
            value: object = importlib.import_module(".".join(parts[:split]))
        except ModuleNotFoundError:
            continue
        for attribute in parts[split:]:
            if not hasattr(value, attribute):
                raise RecogniserCapabilityError(
                    f"stale implementation {reference!r}; repair the declaration for this boundary"
                )
            value = getattr(value, attribute)
        return value
    raise RecogniserCapabilityError(f"stale implementation module in {reference!r}")


def _evidence_reference_is_valid(path: object, root: Path | None) -> bool:
    if not isinstance(path, str) or not path:
        return False
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        return False
    return root is None or (root / candidate).is_file()


def _validate_stage(stage: object, family_id: str, boundary: str, root: Path | None) -> None:
    context = f"family {family_id!r} boundary {boundary!r}"
    if not isinstance(stage, dict) or set(stage) - {
        "evidence",
        "implementation",
        "rationale",
        "state",
        "tracking",
    }:
        raise RecogniserCapabilityError(f"{context} has unknown or invalid fields")
    state = stage.get("state")
    if state not in _STATES:
        raise RecogniserCapabilityError(f"{context} has unknown state {state!r}")
    if state == "supported":
        expected = (
            {"evidence", "state"}
            if boundary == "documentation"
            else {
                "evidence",
                "implementation",
                "state",
            }
        )
        if set(stage) != expected:
            raise RecogniserCapabilityError(f"{context} supported claim lacks required evidence")
        evidence = stage["evidence"]
        if not isinstance(evidence, list) or not evidence or evidence != sorted(set(evidence)):
            raise RecogniserCapabilityError(
                f"{context} evidence must be non-empty, unique and sorted"
            )
        for path in evidence:
            if not _evidence_reference_is_valid(path, root):
                raise RecogniserCapabilityError(
                    f"{context} evidence {path!r} is missing; add an independent behavior test"
                )
        if boundary != "documentation":
            implementation = stage["implementation"]
            if not isinstance(implementation, str):
                raise RecogniserCapabilityError(f"{context} implementation must be a reference")
            _resolve_implementation(implementation)
        return
    if state == "deferred":
        if set(stage) != {"rationale", "state", "tracking"} or not _TRACKING.fullmatch(
            str(stage.get("tracking", ""))
        ):
            raise RecogniserCapabilityError(
                f"{context} deferred state needs rationale and tracking"
            )
    elif set(stage) != {"rationale", "state"}:
        raise RecogniserCapabilityError(f"{context} {state} state needs only a rationale")
    if not isinstance(stage.get("rationale"), str) or not stage["rationale"].strip():
        raise RecogniserCapabilityError(f"{context} needs a non-empty rationale")


def pending_family_declarations(*, package: object | None = None) -> list[str]:
    """Package families this consumer has not declared yet, sorted.

    Empty is the healthy state. A non-empty list means the installed package grew a
    capability Draftwright has not yet decided what to do with -- which is a job for this
    repository, not a reason to block the package's release. See
    ``tests/test_recogniser_adoption.py``, which is what makes that job visible.
    """
    manifest = capability_manifest(format_version=2) if package is None else package
    if not isinstance(manifest, dict) or not isinstance(manifest.get("families"), list):
        raise RecogniserCapabilityError("installed recogniser manifest format is unsupported")
    package_ids = {
        family["id"]
        for family in manifest["families"]
        if isinstance(family, dict) and isinstance(family.get("id"), str)
    }
    declared = {family["id"] for family in consumer_capability_declaration()["families"]}
    return sorted(package_ids - declared)


def validate_recogniser_capabilities(
    declaration: object | None = None,
    *,
    package: object | None = None,
    source_root: Path | None = None,
) -> None:
    """Fail closed when installed package truth and Draftwright policy do not exactly join."""
    current = consumer_capability_declaration() if declaration is None else declaration
    manifest = capability_manifest(format_version=2) if package is None else package
    if not isinstance(current, dict) or set(current) != {
        "consumer",
        "families",
        "format",
        "format_version",
        "package_compatibility",
        "transitions",
    }:
        raise RecogniserCapabilityError(
            "consumer declaration has unknown or missing top-level fields"
        )
    if (
        current["format"] != CONSUMER_CAPABILITY_FORMAT
        or type(current["format_version"]) is not int
        or current["format_version"] != 1
    ):
        raise RecogniserCapabilityError("unsupported Draftwright recogniser declaration format")
    consumer = current["consumer"]
    if not isinstance(consumer, dict) or consumer != {
        "name": "draftwright",
        "version": distribution_version("draftwright"),
    }:
        raise RecogniserCapabilityError(
            "consumer identity/version does not match installed Draftwright metadata"
        )
    installed_package_version = distribution_version(_RECOGNISER_DISTRIBUTION)
    compatibility = current["package_compatibility"]
    if not isinstance(compatibility, dict) or compatibility != {
        "distribution": _RECOGNISER_DISTRIBUTION,
        "version": f"=={installed_package_version}",
        "manifest_format": 2,
    }:
        raise RecogniserCapabilityError(
            "package compatibility does not match installed b123d-recognisers metadata"
        )
    if (
        not isinstance(manifest, dict)
        or manifest.get("format") != "b123d-recognisers-capabilities"
        or type(manifest.get("format_version")) is not int
        or manifest.get("format_version") != 2
    ):
        raise RecogniserCapabilityError("installed recogniser manifest format is unsupported")
    package_info = manifest.get("package")
    if (
        not isinstance(package_info, dict)
        or package_info.get("name") != "b123d-recognisers"
        or package_info.get("version") != installed_package_version
    ):
        raise RecogniserCapabilityError(
            f"installed package identity {package_info!r} does not satisfy "
            f"installed b123d-recognisers metadata {installed_package_version!r}"
        )
    package_families = manifest.get("families")
    families = current["families"]
    if not isinstance(package_families, list) or not isinstance(families, list):
        raise RecogniserCapabilityError("package and consumer families must be arrays")
    package_by_id: dict[str, dict[str, Any]] = {}
    for family in package_families:
        if not isinstance(family, dict) or not isinstance(family.get("id"), str):
            raise RecogniserCapabilityError(
                "installed package has duplicate or malformed families"
            )
        package_by_id[family["id"]] = family
    if len(package_by_id) != len(package_families):
        raise RecogniserCapabilityError("installed package has duplicate or malformed families")
    if not all(
        isinstance(family, dict) and isinstance(family.get("id"), str) for family in families
    ):
        raise RecogniserCapabilityError("consumer family declarations must be objects with IDs")
    ids = [family["id"] for family in families]
    if ids != sorted(set(ids)):
        raise RecogniserCapabilityError(
            "consumer family declarations must be unique and sorted by id"
        )
    # Only the *stale* direction is a compatibility failure. A family we declare that the
    # package no longer ships means this code would call a recogniser that is gone, so it
    # stays fatal. A family the package ships that we have not declared cannot reach us at
    # all -- nothing here constructs `RecognitionResult` or indexes the feature census, so a
    # new field or key is inert -- and failing on it made the *provider* unreleasable until
    # its consumer caught up, which inverts the dependency.
    #
    # Adoption is still required, and still enforced; it is enforced in Draftwright's own CI
    # against `pending_family_declarations`, which is where the decision actually lives.
    stale = sorted(set(ids) - set(package_by_id))
    if stale:
        raise RecogniserCapabilityError(
            f"family inventory mismatch for installed {installed_package_version}; "
            f"stale={stale}; a declared family is no longer in the package"
        )
    checkout_root = Path(__file__).resolve().parents[2]
    root = source_root
    if root is None and (checkout_root / "pyproject.toml").is_file():
        root = checkout_root
    for family in families:
        family_id = family["id"]
        allowed = {
            "completeness",
            "disposition",
            "documentation",
            "drawing_consumer",
            "dsl_declaration",
            "generated_code",
            "id",
            "ir_adapter",
            "package_evidence",
            "rationale",
            "record_schemas",
            "tracking",
        }
        if set(family) - allowed or not {
            "id",
            "record_schemas",
            "disposition",
            *_BOUNDARIES,
        } <= set(family):
            raise RecogniserCapabilityError(f"family {family_id!r} has unknown or missing fields")
        records = package_by_id[family_id].get("records")
        actual_schemas = (
            {
                record.get("name"): record.get("schema_version")
                for record in records
                if isinstance(record, dict)
            }
            if isinstance(records, list)
            else {}
        )
        accepted_schemas = family["record_schemas"]
        valid_schema_declaration = (
            isinstance(accepted_schemas, dict)
            and set(accepted_schemas) == set(actual_schemas)
            and all(
                isinstance(versions, list)
                and versions
                and all(type(version) is int and version > 0 for version in versions)
                and versions == sorted(set(versions))
                for versions in accepted_schemas.values()
            )
        )
        valid_actual_schemas = all(
            type(version) is int and version > 0 for version in actual_schemas.values()
        )
        incompatible = (
            not valid_schema_declaration
            or not valid_actual_schemas
            or any(actual_schemas[name] not in accepted_schemas[name] for name in actual_schemas)
        )
        if incompatible:
            raise RecogniserCapabilityError(
                f"family {family_id!r} record schema mismatch; expected {actual_schemas!r}, "
                f"accepted {accepted_schemas!r}; update the adapter and pin deliberately"
            )
        disposition = family["disposition"]
        if disposition not in {"deferred", "geometry-only", "supported", "unsupported"}:
            raise RecogniserCapabilityError(f"family {family_id!r} has invalid disposition")
        if disposition == "geometry-only":
            if not isinstance(family.get("rationale"), str) or not family["rationale"].strip():
                raise RecogniserCapabilityError(
                    f"geometry-only family {family_id!r} needs rationale"
                )
            package_evidence = family.get("package_evidence")
            available = package_by_id[family_id].get("golden_evidence")
            if not isinstance(package_evidence, list) or not set(package_evidence) <= set(
                available or []
            ):
                raise RecogniserCapabilityError(
                    f"geometry-only family {family_id!r} needs package-owned evidence"
                )
            for boundary in (
                "ir_adapter",
                "dsl_declaration",
                "generated_code",
                "drawing_consumer",
            ):
                stage = family[boundary]
                if isinstance(stage, dict) and stage.get("state") == "supported":
                    raise RecogniserCapabilityError(
                        f"geometry-only family {family_id!r} invents {boundary} semantics"
                    )
        elif disposition == "supported":
            if "rationale" in family or "package_evidence" in family or "tracking" in family:
                raise RecogniserCapabilityError(
                    f"supported family {family_id!r} has geometry-only/reserved fields"
                )
        else:
            if (
                not isinstance(family.get("rationale"), str)
                or not family["rationale"].strip()
                or not _TRACKING.fullmatch(str(family.get("tracking", "")))
            ):
                raise RecogniserCapabilityError(
                    f"reserved family {family_id!r} needs rationale and tracking"
                )
            supported = [
                boundary
                for boundary in _BOUNDARIES[:-1]
                if isinstance(family[boundary], dict)
                and family[boundary].get("state") == "supported"
            ]
            if supported:
                raise RecogniserCapabilityError(
                    f"reserved family {family_id!r} claims supported downstream semantics: "
                    f"{supported!r}"
                )
        for boundary in _BOUNDARIES:
            _validate_stage(family[boundary], family_id, boundary, root)
    transitions = current["transitions"]
    if not isinstance(transitions, list):
        raise RecogniserCapabilityError("transitions must be an array")
    transition_keys: list[tuple[str, str]] = []
    family_by_id = {family["id"]: family for family in families}
    for transition in transitions:
        if not isinstance(transition, dict) or set(transition) != {
            "boundary",
            "compatibility_evidence",
            "family",
            "from",
            "release_notes",
            "to",
            "version",
        }:
            raise RecogniserCapabilityError(
                "state transition lacks version and compatibility evidence"
            )
        transition_family = transition["family"]
        transition_boundary = transition["boundary"]
        if not isinstance(transition_family, str) or not isinstance(transition_boundary, str):
            raise RecogniserCapabilityError(
                "state transition lacks version and compatibility evidence"
            )
        evidence = transition["compatibility_evidence"]
        notes = transition["release_notes"]
        transition_keys.append((transition_family, transition_boundary))
        target = family_by_id.get(transition_family, {}).get(transition_boundary, {}).get("state")
        if (
            transition["from"] == transition["to"]
            or transition["from"] not in _STATES
            or transition["to"] not in _STATES
            or transition["to"] != target
            or transition_family not in ids
            or transition_boundary not in _BOUNDARIES
            or not isinstance(evidence, list)
            or not evidence
            or evidence != sorted(set(evidence))
            or any(not _evidence_reference_is_valid(path, root) for path in evidence)
            or not isinstance(notes, str)
            or not _evidence_reference_is_valid(notes, root)
            or not re.fullmatch(r"\d+\.\d+\.\d+(?:\.dev\d+)?", str(transition["version"]))
        ):
            raise RecogniserCapabilityError(
                "state transition lacks version and compatibility evidence"
            )
    if transition_keys != sorted(set(transition_keys)):
        raise RecogniserCapabilityError("state transitions must be unique and sorted")
