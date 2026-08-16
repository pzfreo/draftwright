"""Independent checks for the cross-repository recogniser capability join."""

from __future__ import annotations

import ast
import copy
import dataclasses
import importlib.metadata
import inspect
import json
import typing
from pathlib import Path

import b123d_recognisers as recognition
import pytest
from build123d import Cylinder

from draftwright.model.detect import (
    _CONVERTERS,
    _DERIVED_CONVERTERS,
    _ORCHESTRATED_RECORDS,
    build_part_model,
)
from draftwright.recogniser_contract import (
    RecogniserCapabilityError,
    consumer_capability_declaration,
    validate_recogniser_capabilities,
)
from draftwright.sheet import Sheet
from draftwright.sheet_emit import _feature_line, emit_sheet_script

ROOT = Path(__file__).parents[1]
PINNED_VERSION = json.loads(
    (ROOT / ".github/recogniser-release.json").read_text(encoding="utf-8")
)["version"]


def _families(declaration: dict) -> dict[str, dict]:
    return {family["id"]: family for family in declaration["families"]}


def _runtime_record_types(annotation: object) -> set[type[object]]:
    args = typing.get_args(annotation)
    if args:
        found: set[type[object]] = set()
        for argument in args:
            found.update(_runtime_record_types(argument))
        return found
    if (
        isinstance(annotation, type)
        and dataclasses.is_dataclass(annotation)
        and annotation.__module__.startswith("b123d_recognisers.")
    ):
        return {annotation}
    return set()


def _runtime_emitted_records() -> set[type[object]]:
    found: set[type[object]] = set()
    for name in dir(recognition):
        if not name.startswith(("recognise_", "project_")):
            continue
        function = getattr(recognition, name)
        hints = typing.get_type_hints(function)
        records = _runtime_record_types(hints.get("return"))
        assert records, f"{name} has no independently discoverable Record return"
        found.update(records)
    return found


def _emitter_literal_kinds() -> set[str]:
    """Derive the emitter inventory from executable branches, not the declaration."""
    tree = ast.parse(inspect.getsource(_feature_line))
    kinds: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or not isinstance(node.left, ast.Name):
            continue
        if node.left.id != "k" or len(node.ops) != 1 or len(node.comparators) != 1:
            continue
        right = node.comparators[0]
        if isinstance(node.ops[0], ast.Eq) and isinstance(right, ast.Constant):
            if isinstance(right.value, str):
                kinds.add(right.value)
        if isinstance(node.ops[0], ast.In) and isinstance(right, (ast.Tuple, ast.Set)):
            kinds.update(
                item.value
                for item in right.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            )
    return kinds


def test_installed_released_package_contract_validates_without_a_sibling_checkout() -> None:
    distribution = importlib.metadata.distribution("b123d-recognisers")
    assert distribution.version == PINNED_VERSION
    assert distribution.read_text("direct_url.json") is None
    package_path = Path(inspect.getfile(recognition)).resolve()
    assert package_path.is_relative_to(ROOT / ".venv")

    validate_recogniser_capabilities()
    package = recognition.capability_manifest(format_version=1)
    declaration = consumer_capability_declaration()
    assert len(package["families"]) == len(declaration["families"]) == 22


def test_runtime_adapter_inventory_is_derived_independently_and_exhaustive() -> None:
    runtime = _runtime_emitted_records()
    tiers = [set(_CONVERTERS), set(_DERIVED_CONVERTERS), set(_ORCHESTRATED_RECORDS)]
    assert runtime == set.union(*tiers)
    assert all(
        not left & right for index, left in enumerate(tiers) for right in tiers[index + 1 :]
    )

    declared_records = {
        name
        for family in consumer_capability_declaration()["families"]
        for name in family["record_schemas"]
    }
    package_records = {
        record["name"]
        for family in recognition.capability_manifest()["families"]
        for record in family["records"]
    }
    assert declared_records == package_records


def test_dsl_and_generated_code_inventories_are_derived_from_live_code() -> None:
    declaration = consumer_capability_declaration()
    supported = [
        family
        for family in declaration["families"]
        if family["generated_code"]["state"] == "supported"
    ]
    sheet_methods = {
        name
        for name, value in inspect.getmembers(Sheet)
        if callable(value) and not name.startswith("_")
    }
    declared_methods = {
        family["dsl_declaration"]["implementation"].rsplit(".", 1)[-1] for family in supported
    }
    assert declared_methods <= sheet_methods

    family_kinds = {
        "bosses": "boss",
        "chamfers": "chamfer",
        "channels": "channel",
        "countersinks": "hole",
        "double-d-bores": "hole",
        "face-levels": "step_level",
        "fillets": "fillet",
        "flats": "flat",
        "grooves": "groove",
        "hole-patterns": "pattern",
        "holes": "hole",
        "plates": "plate",
        "pocket-patterns": "pocket_pattern",
        "pockets": "pocket",
        "polygonal-bosses": "polygonal_boss",
        "polygonal-stock": "polygonal_stock",
        "rectangular-pads": "pad",
        "risers": "step_level",
        "slot-patterns": "slot_pattern",
        "slots": "slot",
        "turned-steps": "step",
    }
    assert {family["id"] for family in supported} == set(family_kinds)
    assert set(family_kinds.values()) <= _emitter_literal_kinds()


def test_boss_is_fully_consumed_and_generated_sheet_round_trips() -> None:
    boss = _families(consumer_capability_declaration())["bosses"]
    assert boss["disposition"] == "supported"
    assert all(
        boss[boundary]["state"] == "supported"
        for boundary in (
            "ir_adapter",
            "dsl_declaration",
            "generated_code",
            "drawing_consumer",
            "completeness",
            "documentation",
        )
    )

    part = Cylinder(10, 20)
    detected = build_part_model(part)
    source = emit_sheet_script(
        detected,
        "part = Cylinder(10, 20)",
        "contract-roundtrip",
        title="CONTRACT",
        number="BOSS-1",
    )
    source = "\n".join(
        line for line in source.splitlines() if not line.startswith("drawing.export(")
    )
    namespace: dict[str, object] = {"Cylinder": Cylinder}
    exec(compile(source, "<generated-sheet>", "exec"), namespace)
    rebuilt = namespace["drawing"].model()
    original_boss = next(feature for feature in detected.features if feature.kind == "boss")
    rebuilt_boss = next(feature for feature in rebuilt.features if feature.kind == "boss")
    assert rebuilt_boss.parameters() == original_boss.parameters()


def test_repeating_profile_is_explicit_geometry_only_critique_evidence() -> None:
    family = _families(consumer_capability_declaration())["repeating-radial-profiles"]
    assert family["disposition"] == "geometry-only"
    assert all(
        family[boundary]["state"] == "not-applicable"
        for boundary in ("ir_adapter", "dsl_declaration", "generated_code", "drawing_consumer")
    )
    assert family["completeness"]["state"] == "supported"
    assert recognition.RepeatingRadialProfile in _ORCHESTRATED_RECORDS
    assert recognition.RepeatingRadialProfile not in _CONVERTERS | _DERIVED_CONVERTERS


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value.update({"format_version": 2}), "unsupported"),
        (lambda value: value["consumer"].update({"version": "0.4.6"}), "identity/version"),
        (
            lambda value: value["package_compatibility"].update({"version": "==0.1.0"}),
            "must pin",
        ),
        (lambda value: value["families"].pop(), "inventory mismatch"),
        (
            lambda value: value["families"][0]["record_schemas"].update({"BossRecord": 99}),
            "record schema mismatch",
        ),
        (
            lambda value: value["families"][0]["ir_adapter"].update(
                {"implementation": "draftwright.model.detect.no_such_adapter"}
            ),
            "stale implementation",
        ),
        (
            lambda value: value["families"][0]["documentation"].update(
                {"evidence": ["docs/reference/missing.md"]}
            ),
            "evidence .* is missing",
        ),
        (
            lambda value: value["families"][0]["completeness"].update({"state": "maybe"}),
            "unknown state",
        ),
    ],
)
def test_consumer_declaration_fails_closed_on_stale_or_unknown_claims(
    mutate, message: str
) -> None:
    declaration = consumer_capability_declaration()
    mutate(declaration)
    with pytest.raises(RecogniserCapabilityError, match=message):
        validate_recogniser_capabilities(declaration)


def test_unknown_package_family_and_schema_format_fail_closed() -> None:
    package = recognition.capability_manifest()
    package["families"].append(copy.deepcopy(package["families"][0]))
    package["families"][-1]["id"] = "future-thread"
    with pytest.raises(RecogniserCapabilityError, match="inventory mismatch"):
        validate_recogniser_capabilities(package=package)

    package = recognition.capability_manifest()
    package["format_version"] = 2
    with pytest.raises(RecogniserCapabilityError, match="manifest format"):
        validate_recogniser_capabilities(package=package)


def test_geometry_only_cannot_acquire_invented_drafting_semantics() -> None:
    declaration = consumer_capability_declaration()
    family = _families(declaration)["repeating-radial-profiles"]
    family["ir_adapter"] = copy.deepcopy(_families(declaration)["bosses"]["ir_adapter"])
    with pytest.raises(RecogniserCapabilityError, match="invents ir_adapter semantics"):
        validate_recogniser_capabilities(declaration)


def test_state_transition_requires_version_release_notes_and_compatibility_evidence() -> None:
    declaration = consumer_capability_declaration()
    declaration["transitions"] = [{"family": "bosses", "from": "deferred", "to": "supported"}]
    with pytest.raises(RecogniserCapabilityError, match="transition lacks"):
        validate_recogniser_capabilities(declaration)

    declaration["transitions"] = [
        {
            "boundary": "completeness",
            "compatibility_evidence": ["tests/test_recogniser_capabilities.py"],
            "family": "chamfers",
            "from": "supported",
            "release_notes": "CHANGELOG.md",
            "to": "deferred",
            "version": "0.4.7",
        }
    ]
    validate_recogniser_capabilities(declaration)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda declaration, _package: declaration["families"][0]["ir_adapter"].update(
                {"implementation": "not-a-reference"}
            ),
            "invalid Draftwright implementation reference",
        ),
        (
            lambda declaration, _package: declaration["families"][0].update({"ir_adapter": []}),
            "unknown or invalid fields",
        ),
        (
            lambda declaration, _package: declaration["families"][0]["ir_adapter"].pop(
                "implementation"
            ),
            "supported claim lacks required evidence",
        ),
        (
            lambda declaration, _package: declaration["families"][0]["ir_adapter"].update(
                {"evidence": []}
            ),
            "evidence must be non-empty",
        ),
        (
            lambda declaration, _package: declaration["families"][0]["ir_adapter"].update(
                {"implementation": 7}
            ),
            "implementation must be a reference",
        ),
        (
            lambda declaration, _package: declaration["families"][1]["completeness"].pop(
                "tracking"
            ),
            "deferred state needs rationale and tracking",
        ),
        (
            lambda declaration, _package: _families(declaration)["repeating-radial-profiles"][
                "ir_adapter"
            ].update({"tracking": "https://github.com/pzfreo/draftwright/issues/1"}),
            "state needs only a rationale",
        ),
        (
            lambda declaration, _package: _families(declaration)["repeating-radial-profiles"][
                "ir_adapter"
            ].update({"rationale": ""}),
            "needs a non-empty rationale",
        ),
        (
            lambda declaration, _package: declaration.pop("transitions"),
            "unknown or missing top-level fields",
        ),
        (
            lambda _declaration, package: package["package"].update({"version": "99.99.99"}),
            f"does not satisfy =={PINNED_VERSION}",
        ),
        (
            lambda _declaration, package: package.update({"families": {}}),
            "families must be arrays",
        ),
        (
            lambda _declaration, package: package["families"].append(None),
            "duplicate or malformed families",
        ),
        (
            lambda _declaration, package: package["families"].append(
                copy.deepcopy(package["families"][0])
            ),
            "duplicate or malformed families",
        ),
        (
            lambda declaration, _package: declaration["families"].append(None),
            "consumer family declarations",
        ),
        (
            lambda declaration, _package: declaration["families"][0].update({"surprise": 1}),
            "unknown or missing fields",
        ),
        (
            lambda declaration, _package: declaration["families"][0].update(
                {"disposition": "maybe"}
            ),
            "invalid disposition",
        ),
        (
            lambda declaration, _package: _families(declaration)[
                "repeating-radial-profiles"
            ].update({"rationale": ""}),
            "needs rationale",
        ),
        (
            lambda declaration, _package: _families(declaration)[
                "repeating-radial-profiles"
            ].update({"package_evidence": ["not-package-evidence.json"]}),
            "needs package-owned evidence",
        ),
        (
            lambda declaration, _package: declaration["families"][0].update(
                {"rationale": "not allowed on supported"}
            ),
            "has geometry-only/reserved fields",
        ),
        (
            lambda declaration, _package: declaration["families"][0].update(
                {"disposition": "deferred", "rationale": "later"}
            ),
            "reserved family .* needs rationale and tracking",
        ),
        (
            lambda declaration, _package: declaration.update({"transitions": {}}),
            "transitions must be an array",
        ),
    ],
)
def test_every_malformed_consumer_contract_fails_at_its_own_boundary(mutate, message: str) -> None:
    declaration = consumer_capability_declaration()
    package = recognition.capability_manifest()
    mutate(declaration, package)
    with pytest.raises(RecogniserCapabilityError, match=message):
        validate_recogniser_capabilities(declaration, package=package)


def _transition() -> dict[str, object]:
    return {
        "boundary": "completeness",
        "compatibility_evidence": ["tests/test_recogniser_capabilities.py"],
        "family": "chamfers",
        "from": "supported",
        "release_notes": "CHANGELOG.md",
        "to": "deferred",
        "version": "0.4.7",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("family", 7),
        ("boundary", 7),
        ("from", "deferred"),
        ("from", "future-state"),
        ("to", "future-state"),
        ("to", "supported"),
        ("family", "future-family"),
        ("boundary", "future-boundary"),
        ("compatibility_evidence", "not-a-list"),
        ("compatibility_evidence", []),
        (
            "compatibility_evidence",
            ["tests/test_recogniser_capabilities.py", "tests/test_recogniser_capabilities.py"],
        ),
        ("compatibility_evidence", ["tests/missing-contract-evidence.py"]),
        ("release_notes", 7),
        ("release_notes", "docs/missing-release-notes.md"),
        ("version", "next"),
    ],
)
def test_each_unevidenced_transition_shape_fails_closed(field: str, value: object) -> None:
    declaration = consumer_capability_declaration()
    transition = _transition()
    transition[field] = value
    declaration["transitions"] = [transition]
    with pytest.raises(RecogniserCapabilityError, match="transition lacks"):
        validate_recogniser_capabilities(declaration)


def test_transition_keys_must_be_unique() -> None:
    declaration = consumer_capability_declaration()
    declaration["transitions"] = [_transition(), _transition()]
    with pytest.raises(RecogniserCapabilityError, match="unique and sorted"):
        validate_recogniser_capabilities(declaration)


def test_deferred_family_cannot_claim_supported_downstream_semantics() -> None:
    declaration = consumer_capability_declaration()
    family = declaration["families"][0]
    family.update(
        {
            "disposition": "deferred",
            "rationale": "Consumer support is deliberately scheduled later.",
            "tracking": "https://github.com/pzfreo/draftwright/issues/1169",
        }
    )
    with pytest.raises(RecogniserCapabilityError, match="claims supported downstream semantics"):
        validate_recogniser_capabilities(declaration)

    for boundary in (
        "ir_adapter",
        "dsl_declaration",
        "generated_code",
        "drawing_consumer",
        "completeness",
    ):
        family[boundary] = {
            "state": "deferred",
            "rationale": "Consumer support is deliberately scheduled later.",
            "tracking": "https://github.com/pzfreo/draftwright/issues/1169",
        }
    validate_recogniser_capabilities(declaration)


def test_documentation_names_each_failure_and_contributor_repair_action() -> None:
    documentation = (ROOT / "docs/reference/recogniser-capabilities.md").read_text(
        encoding="utf-8"
    )
    for message in (
        "family inventory mismatch",
        "record schema mismatch",
        "stale implementation",
        "evidence is missing",
        "geometry-only family invents semantics",
        "reserved family claims supported downstream semantics",
        "state transition lacks evidence",
    ):
        assert message in documentation
    assert "Adding or changing a recogniser" in documentation
