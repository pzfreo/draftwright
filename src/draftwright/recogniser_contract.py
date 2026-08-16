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
_PACKAGE_VERSION = "0.2.1"
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
    "rectangular-pads": _FamilySpec(("RaisedPad",), "_convert_pad", "pad", "render_diameters"),
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
    "turned-steps": _FamilySpec(
        ("TurnedProfile", "TurnedStep"), "_convert_step", "step", "render_step_lengths"
    ),
}


def _supported(implementation: str, evidence: str) -> dict[str, Any]:
    return {"state": "supported", "implementation": implementation, "evidence": [evidence]}


def _deferred_completeness() -> dict[str, Any]:
    return {
        "state": "deferred",
        "rationale": (
            "The feature is consumed for drafting, but evidence-based completeness scoring is "
            "being specified separately rather than inferred from annotation presence."
        ),
        "tracking": "https://github.com/pzfreo/draftwright/issues/1169",
    }


def _family_declaration(family_id: str, spec: _FamilySpec) -> dict[str, Any]:
    completeness = _deferred_completeness()
    if family_id == "bosses":
        completeness = _supported(
            "draftwright.linting.coverage.lint_boss_height_coverage",
            "tests/test_issue_885_prismatic_coverage.py",
        )
    return {
        "id": family_id,
        "record_schemas": {name: 1 for name in spec.records},
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
        "record_schemas": {"RepeatingRadialProfile": 1},
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


def consumer_capability_declaration() -> dict[str, Any]:
    """Return an isolated format-1 declaration for the released package contract."""
    families = [_family_declaration(key, value) for key, value in sorted(_FAMILIES.items())]
    families.append(_geometry_only_declaration())
    families.sort(key=lambda family: family["id"])
    return {
        "format": CONSUMER_CAPABILITY_FORMAT,
        "format_version": CONSUMER_CAPABILITY_FORMAT_VERSION,
        "consumer": {"name": "draftwright", "version": "0.4.7.dev0"},
        "package_compatibility": {
            "distribution": "b123d-recognisers",
            "version": f"=={_PACKAGE_VERSION}",
            "manifest_format": 1,
        },
        "families": families,
        "transitions": [],
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


def validate_recogniser_capabilities(
    declaration: object | None = None,
    *,
    package: object | None = None,
    source_root: Path | None = None,
) -> None:
    """Fail closed when installed package truth and Draftwright policy do not exactly join."""
    current = consumer_capability_declaration() if declaration is None else declaration
    manifest = capability_manifest(format_version=1) if package is None else package
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
            "consumer identity/version is stale; update it with the Draftwright release"
        )
    compatibility = current["package_compatibility"]
    if not isinstance(compatibility, dict) or compatibility != {
        "distribution": "b123d-recognisers",
        "version": f"=={_PACKAGE_VERSION}",
        "manifest_format": 1,
    }:
        raise RecogniserCapabilityError(
            f"package compatibility must pin b123d-recognisers {_PACKAGE_VERSION} format 1"
        )
    if (
        not isinstance(manifest, dict)
        or manifest.get("format") != "b123d-recognisers-capabilities"
        or type(manifest.get("format_version")) is not int
        or manifest.get("format_version") != 1
    ):
        raise RecogniserCapabilityError("installed recogniser manifest format is unsupported")
    package_info = manifest.get("package")
    if not isinstance(package_info, dict) or package_info.get("version") != _PACKAGE_VERSION:
        raise RecogniserCapabilityError(
            f"installed package version {getattr(package_info, 'get', lambda _key: None)('version')!r} "
            f"does not satisfy =={_PACKAGE_VERSION}; update the pin and declaration together"
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
    if ids != sorted(set(ids)) or set(ids) != set(package_by_id):
        missing = sorted(set(package_by_id) - set(ids))
        stale = sorted(set(ids) - set(package_by_id))
        raise RecogniserCapabilityError(
            f"family inventory mismatch for installed {_PACKAGE_VERSION}; "
            f"unknown={missing}, stale={stale}; "
            "declare every package family explicitly"
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
        if family["record_schemas"] != actual_schemas:
            raise RecogniserCapabilityError(
                f"family {family_id!r} record schema mismatch; expected {actual_schemas!r}, "
                f"declared {family['record_schemas']!r}; update the adapter and pin deliberately"
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
