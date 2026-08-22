"""Independent checks for the cross-repository recogniser capability join."""

from __future__ import annotations

import ast
import copy
import dataclasses
import importlib.metadata
import inspect
import json
import os
import typing
from pathlib import Path

import b123d_recognisers as recognition
import pytest
from build123d import Cylinder

import draftwright.recogniser_contract as contract_module
from draftwright.model.detect import (
    _CONVERTERS,
    _DERIVED_CONVERTERS,
    _ORCHESTRATED_RECORDS,
    _UNCONSUMED_RECORDS,
    build_part_model,
)
from draftwright.recogniser_contract import (
    RecogniserCapabilityError,
    consumer_capability_declaration,
    pending_family_declarations,
    validate_recogniser_capabilities,
)
from draftwright.sheet import Sheet
from draftwright.sheet_emit import _feature_line, emit_sheet_script

ROOT = Path(__file__).parents[1]
PINNED_VERSION = json.loads(
    (ROOT / ".github/recogniser-release.json").read_text(encoding="utf-8")
)["version"]
CANDIDATE_VERSION = os.environ.get("DRAFTWRIGHT_RECOGNISER_CANDIDATE_VERSION")
EXPECTED_PACKAGE_VERSION = CANDIDATE_VERSION or PINNED_VERSION


def _validate(*args, **kwargs) -> None:
    """Run the same contract under the explicitly selected two-checkout candidate, if any."""
    validate_recogniser_capabilities(
        *args,
        **kwargs,
        candidate_version=CANDIDATE_VERSION,
    )


def test_installed_wheel_layout_validates_portable_contract_without_source_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    installed_module = tmp_path / "lib/python/site-packages/draftwright/recogniser_contract.py"
    monkeypatch.setattr(contract_module, "__file__", str(installed_module))

    _validate()


@pytest.mark.parametrize("reference", [None, "", "/tmp/evidence.py", "../evidence.py"])
def test_installed_wheel_rejects_nonportable_evidence_references(reference: object) -> None:
    assert not contract_module._evidence_reference_is_valid(reference, None)


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


def test_installed_package_contract_validates_without_a_sibling_checkout() -> None:
    distribution = importlib.metadata.distribution("b123d-recognisers")
    assert distribution.version == EXPECTED_PACKAGE_VERSION
    if CANDIDATE_VERSION is None:
        assert distribution.read_text("direct_url.json") is None
    else:
        assert distribution.read_text("direct_url.json") is not None
    package_path = Path(inspect.getfile(recognition)).resolve()
    assert package_path.is_relative_to(ROOT / ".venv")

    _validate()
    package = recognition.capability_manifest(format_version=1)
    declaration = consumer_capability_declaration()
    # 25 since 0.2.6 added angled-steps, passages and prismatic-pockets (#1244). A literal,
    # not a length comparison against the package: the point is that BOTH sides changed
    # together, so an upgrade that declared nothing would leave this at 22 and fail.
    assert len(package["families"]) == len(declaration["families"]) == 25


def test_runtime_adapter_inventory_is_derived_independently_and_exhaustive() -> None:
    runtime = _runtime_emitted_records()
    tiers = [
        set(_CONVERTERS),
        set(_DERIVED_CONVERTERS),
        set(_ORCHESTRATED_RECORDS),
        set(_UNCONSUMED_RECORDS),
    ]
    # Subset again, and the third place the same coupling was written. A converter for a
    # record the installed package no longer emits is dead code pointing at a type that is
    # gone, so that direction still fails. A record with no converter yet is the additive
    # direction: the family carrying it is reported by `pending_family_declarations`, and
    # declaring it `supported` forces an `ir_adapter` that `_resolve_implementation` must
    # import -- so converter coverage is still compelled, at the point the decision is made.
    assert set.union(*tiers) <= runtime
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
    # Subset, not equality, and for the same reason the family inventory is: a record we
    # declare that the package does not ship is a break, while one it ships and we have not
    # declared yet is a Draftwright to-do. `tests/test_recogniser_adoption.py` owns the
    # second direction; asserting equality here would put the coupling straight back.
    assert declared_records <= package_records

    # Restores the half the subset above gave up. `runtime == union(tiers)` used to assert two
    # things at once: no converter for a record that does not exist, and no record without a
    # converter. Relaxing it to a subset kept the first and dropped the second, which would let
    # a family be declared `supported` while nothing converts what it emits.
    #
    # Scoped to records this repository has actually declared, so it cannot recouple: a record
    # the package emits and we have not adopted is exactly the case that must stay free, and
    # `tests/test_recogniser_adoption.py` owns it.
    converted = {record.__name__ for record in set.union(*tiers)}
    emitted = {record.__name__ for record in runtime}
    assert emitted & declared_records <= converted


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


def _supported(value: dict) -> dict:
    """The first family declared `supported`, by disposition rather than by position.

    These mutations need a family that HAS an ir_adapter implementation, documentation evidence
    and a `BossRecord` schema — i.e. a supported one. They indexed `families[0]` and got it
    because "bosses" sorted first; 0.2.6 added `angled-steps`, which sorts ahead of it and is
    declared unsupported, so every one of them began mutating the wrong shape and asserting the
    wrong error (#1244). Selecting by what the mutation needs cannot drift with the alphabet.
    """
    return next(family for family in value["families"] if family.get("disposition") == "supported")


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value.update({"format_version": 2}), "unsupported"),
        (lambda value: value["consumer"].update({"version": "0.4.6"}), "identity/version"),
        (
            lambda value: value["package_compatibility"].update({"version": "==0.1.0"}),
            "must pin",
        ),
        (
            # The stale direction, and the one that really breaks: an id the package does not
            # ship would have a declared adapter calling a recogniser that is gone. The
            # opposite -- not declaring a family it *does* ship -- is deliberately no longer a
            # failure here; see tests/test_recogniser_adoption.py. Renaming the *last* family
            # so the change cannot disturb sorted-id ordering and trip that check first.
            lambda value: value["families"][-1].update({"id": "zz-retired-family"}),
            "stale=",
        ),
        (
            lambda value: _supported(value)["record_schemas"].update({"BossRecord": [99]}),
            "record schema mismatch",
        ),
        (
            lambda value: _supported(value)["ir_adapter"].update(
                {"implementation": "draftwright.model.detect.no_such_adapter"}
            ),
            "stale implementation",
        ),
        (
            lambda value: _supported(value)["documentation"].update(
                {"evidence": ["docs/reference/missing.md"]}
            ),
            "evidence .* is missing",
        ),
        (
            lambda value: _supported(value)["completeness"].update({"state": "maybe"}),
            "unknown state",
        ),
    ],
)
def test_consumer_declaration_fails_closed_on_stale_or_malformed_claims(
    mutate, message: str
) -> None:
    declaration = consumer_capability_declaration()
    mutate(declaration)
    with pytest.raises(RecogniserCapabilityError, match=message):
        _validate(declaration)


def test_a_package_family_we_have_not_declared_yet_does_not_fail_the_join() -> None:
    """The additive direction is a Draftwright to-do, not a compatibility failure.

    A family the package ships and this repository has not declared cannot reach any
    Draftwright code path: nothing here constructs ``RecognitionResult`` or indexes the
    feature census by key. Failing the join on it made every new recogniser a two-repo
    lockstep release, with the provider blocked on its own consumer.

    It is still tracked -- ``pending_family_declarations`` reports it and
    ``tests/test_recogniser_adoption.py`` fails Draftwright's build until it is declared.
    """
    package = recognition.capability_manifest()
    package["families"].append(copy.deepcopy(package["families"][0]))
    package["families"][-1]["id"] = "future-thread"

    _validate(package=package)

    # `in`, not equality: any family the installed package has genuinely grown since the
    # last declaration is legitimately pending too, and this test is not about those.
    assert "future-thread" in pending_family_declarations(package=package)


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda families: families.reverse(), id="unsorted"),
        pytest.param(
            lambda families: families[0].update({"id": families[-1]["id"]}), id="duplicated"
        ),
    ],
)
def test_family_declarations_must_be_unique_and_sorted(mutate) -> None:
    """Both halves of that condition, because one expression checks two different mistakes.

    Ordering is now checked separately from membership, so it cannot be masked by it: while
    the inventory check was a single condition, unsorted ids and a stale family raised the
    same error. Reversing the list gives ordering without duplication; copying one id over
    another gives duplication without any id the package does not ship. An earlier version of
    this test did only the second while claiming to do the first.
    """
    declaration = consumer_capability_declaration()
    mutate(declaration["families"])

    with pytest.raises(RecogniserCapabilityError, match="unique and sorted"):
        _validate(declaration)


def test_pending_declarations_reject_a_malformed_manifest() -> None:
    """The pending query fails closed too, rather than reporting an empty to-do list.

    It is the control that replaced a hard failure, so a malformed manifest must not make it
    quietly answer "nothing pending" -- that would read as adoption being complete.

    ``None`` is deliberately absent from these: it is the "use the installed manifest"
    sentinel, not a malformed value.
    """
    for broken in ("not-a-dict", {}, {"families": "not-an-array"}):
        with pytest.raises(RecogniserCapabilityError, match="manifest format is unsupported"):
            pending_family_declarations(package=broken)


def test_schema_format_fails_closed() -> None:
    package = recognition.capability_manifest()
    package["format_version"] = 2
    with pytest.raises(RecogniserCapabilityError, match="manifest format"):
        _validate(package=package)


def test_only_chamfer_and_fillet_use_the_declared_additive_schema_2_state() -> None:
    """The 0.3.0 transition is dual-readable before cutover and schema-2-only afterward.

    Package PR #151 adds one optional ``turned`` field to these two records. The existing
    converters consume only the unchanged schema-1 fields, so accepting schema 2 before the
    dependency cutover is compatible. The dependency updater closes the window to schema 2;
    schema 3 remains unknown in both states and must fail closed.
    """
    declaration = consumer_capability_declaration()
    families = _families(declaration)
    transition_open = contract_module._CANDIDATE_PACKAGE_VERSION is not None
    expected_transition_schemas = [1, 2] if transition_open else [2]
    assert families["chamfers"]["record_schemas"] == {"Chamfer": expected_transition_schemas}
    assert families["fillets"]["record_schemas"] == {"Fillet": expected_transition_schemas}
    assert all(
        versions == [1]
        for family_id, family in families.items()
        if family_id not in {"chamfers", "fillets"}
        for versions in family["record_schemas"].values()
    )

    package = recognition.capability_manifest()
    package_families = _families(package)
    package_families["chamfers"]["records"][0]["schema_version"] = 2
    package_families["fillets"]["records"][0]["schema_version"] = 2
    _validate(declaration, package=package)

    package_families["chamfers"]["records"][0]["schema_version"] = 3
    with pytest.raises(RecogniserCapabilityError, match="record schema mismatch"):
        _validate(declaration, package=package)


def test_candidate_validation_changes_only_the_explicit_reviewed_package_identity() -> None:
    package = recognition.capability_manifest()
    transition = contract_module._CANDIDATE_PACKAGE_VERSION
    if transition is not None:
        candidate = f"{transition}.dev0"
        package["package"]["version"] = candidate
        package_families = _families(package)
        package_families["chamfers"]["records"][0]["schema_version"] = 2
        package_families["fillets"]["records"][0]["schema_version"] = 2
        validate_recogniser_capabilities(package=package, candidate_version=candidate)

        with pytest.raises(
            RecogniserCapabilityError,
            match=rf"b123d-recognisers=={PINNED_VERSION}",
        ):
            validate_recogniser_capabilities(package=package)

    with pytest.raises(RecogniserCapabilityError, match="outside the reviewed"):
        validate_recogniser_capabilities(package=package, candidate_version="99.99.99")

    package = recognition.capability_manifest()
    package["package"]["name"] = "lookalike"
    with pytest.raises(RecogniserCapabilityError, match="package identity"):
        _validate(package=package)


def test_removing_schema_2_from_the_transition_guard_rejects_the_candidate() -> None:
    """Mutation guard: schema-2 compatibility depends on the explicit declaration."""
    declaration = consumer_capability_declaration()
    _families(declaration)["chamfers"]["record_schemas"]["Chamfer"] = [1]
    package = recognition.capability_manifest()
    _families(package)["chamfers"]["records"][0]["schema_version"] = 2

    with pytest.raises(RecogniserCapabilityError, match="record schema mismatch"):
        _validate(declaration, package=package)


@pytest.mark.parametrize(
    "versions",
    [1, [], [1, 1], [True], [1, "2"], [[1]], [1, None]],
)
def test_record_schema_acceptance_lists_fail_closed_when_malformed(versions: object) -> None:
    declaration = consumer_capability_declaration()
    _families(declaration)["bosses"]["record_schemas"]["BossRecord"] = versions

    with pytest.raises(RecogniserCapabilityError, match="record schema mismatch"):
        _validate(declaration)


@pytest.mark.parametrize("version", [0, True, 2.0, "2"])
def test_provider_record_schema_versions_must_be_positive_integers(version: object) -> None:
    package = recognition.capability_manifest()
    _families(package)["chamfers"]["records"][0]["schema_version"] = version

    with pytest.raises(RecogniserCapabilityError, match="record schema mismatch"):
        _validate(package=package)


def test_geometry_only_cannot_acquire_invented_drafting_semantics() -> None:
    declaration = consumer_capability_declaration()
    family = _families(declaration)["repeating-radial-profiles"]
    family["ir_adapter"] = copy.deepcopy(_families(declaration)["bosses"]["ir_adapter"])
    with pytest.raises(RecogniserCapabilityError, match="invents ir_adapter semantics"):
        _validate(declaration)


def test_state_transition_requires_version_release_notes_and_compatibility_evidence() -> None:
    declaration = consumer_capability_declaration()
    declaration["transitions"] = [{"family": "bosses", "from": "deferred", "to": "supported"}]
    with pytest.raises(RecogniserCapabilityError, match="transition lacks"):
        _validate(declaration)

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
    _validate(declaration)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda declaration, _package: _families(declaration)["bosses"]["ir_adapter"].update(
                {"implementation": "not-a-reference"}
            ),
            "invalid Draftwright implementation reference",
        ),
        (
            lambda declaration, _package: _families(declaration)["bosses"].update(
                {"ir_adapter": []}
            ),
            "unknown or invalid fields",
        ),
        (
            lambda declaration, _package: _families(declaration)["bosses"]["ir_adapter"].pop(
                "implementation"
            ),
            "supported claim lacks required evidence",
        ),
        (
            lambda declaration, _package: _families(declaration)["bosses"]["ir_adapter"].update(
                {"evidence": []}
            ),
            "evidence must be non-empty",
        ),
        (
            lambda declaration, _package: _families(declaration)["bosses"]["ir_adapter"].update(
                {"implementation": 7}
            ),
            "implementation must be a reference",
        ),
        (
            # A family whose `completeness` is genuinely deferred — "bosses" declares it
            # supported, and index 1 stopped being a deferred one when 0.2.6's three new
            # families changed the sort order (#1244).
            lambda declaration, _package: _families(declaration)["channels"]["completeness"].pop(
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
            f"does not satisfy b123d-recognisers=={EXPECTED_PACKAGE_VERSION}",
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
            lambda declaration, _package: _families(declaration)["bosses"].update({"surprise": 1}),
            "unknown or missing fields",
        ),
        (
            lambda declaration, _package: _families(declaration)["bosses"].update(
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
            lambda declaration, _package: _families(declaration)["bosses"].update(
                {"rationale": "not allowed on supported"}
            ),
            "has geometry-only/reserved fields",
        ),
        (
            lambda declaration, _package: _families(declaration)["bosses"].update(
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
        _validate(declaration, package=package)


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
        _validate(declaration)


def test_transition_keys_must_be_unique() -> None:
    declaration = consumer_capability_declaration()
    declaration["transitions"] = [_transition(), _transition()]
    with pytest.raises(RecogniserCapabilityError, match="unique and sorted"):
        _validate(declaration)


def test_deferred_family_cannot_claim_supported_downstream_semantics() -> None:
    declaration = consumer_capability_declaration()
    # A family with supported boundaries to demote — selected by id, since 0.2.6's new
    # families sort ahead of "bosses" and are already unsupported (#1244).
    family = _families(declaration)["bosses"]
    family.update(
        {
            "disposition": "deferred",
            "rationale": "Consumer support is deliberately scheduled later.",
            "tracking": "https://github.com/pzfreo/draftwright/issues/1169",
        }
    )
    with pytest.raises(RecogniserCapabilityError, match="claims supported downstream semantics"):
        _validate(declaration)

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
    _validate(declaration)


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
