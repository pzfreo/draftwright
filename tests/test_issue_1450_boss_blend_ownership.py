"""#1450 — one cylindrical face must not become both a boss diameter and blend radius."""

from __future__ import annotations

from pathlib import Path
from runpy import run_path

from b123d_recognisers.evidence import build_recognition_evidence
from build123d import import_step

from draftwright import build_drawing
from draftwright.linting.coverage import lint_feature_coverage as coverage_lint
from draftwright.recognition_ownership import boss_blend_owner_pairs, boss_spans_envelope
from draftwright.sheet_emit import generate_sheet_script

FIXTURE = Path(__file__).parent / "fixtures" / "grm04_drive_plate.step"


def _occurrences(ownership, family: str):
    return tuple(
        occurrence
        for occurrence in ownership.evidence.features
        if ownership.evidence.family(occurrence) == family
    )


def test_grm04_lower_round_has_one_ir_owner_and_no_detached_diameter() -> None:
    drawing = build_drawing(FIXTURE, title="GRM-04")
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    (boss_occurrence,) = _occurrences(ownership, "bosses")
    binding = ownership.binding_for(boss_occurrence)

    assert binding is not None
    assert binding.disposition == "absorbed"
    assert binding.reason_code == "boss_blend_owner"
    assert binding.feature.kind == "blend"
    assert binding.feature.radius == 4.0
    assert not [feature for feature in drawing.model().features if feature.kind == "boss"]
    assert [feature.radius for feature in drawing.model().features if feature.kind == "blend"] == [
        3.0,
        4.0,
    ]
    assert ownership.unexpectedly_missing == ()
    assert not [issue for issue in drawing.lint() if issue.code == "feature_not_dimensioned"]


def test_generated_grm04_sheet_emits_r4_but_not_a_second_diameter(tmp_path) -> None:
    script_path = Path(
        generate_sheet_script(
            str(FIXTURE),
            out=str(tmp_path / "grm04"),
            title="GRM-04 DRIVE PLATE",
            number="GRM-04",
            page="A3",
            scale=5,
            scale_policy="strict",
            zones=True,
            projection="third",
        )
    )
    script = script_path.read_text()

    assert "radius=4" in script
    assert 'sheet.dimension(blend2, "blend.radius")' in script
    assert "sheet.diameter(diameter=8" not in script
    assert '"boss.diameter"' not in script
    rebuilt = run_path(str(script_path))["drawing"]
    assert not [issue for issue in rebuilt.lint() if issue.code == "feature_not_dimensioned"]


def test_scalar_equality_without_same_run_face_identity_does_not_deduplicate() -> None:
    part = import_step(FIXTURE)
    evidence = build_recognition_evidence(part, rotational=False)
    (boss,) = evidence.result.bosses
    lower, upper = sorted(evidence.result.blends, key=lambda blend: blend.radius, reverse=True)
    bbox = part.bounding_box()

    assert boss_blend_owner_pairs(
        evidence,
        (boss,),
        (lower,),
        bbox=bbox,
        envelope_emittable=True,
    ) == ((boss, lower),)
    assert (
        boss_blend_owner_pairs(
            evidence,
            (boss,),
            (upper,),
            bbox=bbox,
            envelope_emittable=True,
        )
        == ()
    )
    assert (
        boss_blend_owner_pairs(
            evidence,
            (boss,),
            (lower,),
            bbox=bbox,
            envelope_emittable=False,
        )
        == ()
    )


def test_blend_ownership_requires_boss_length_to_be_covered_by_envelope() -> None:
    part = import_step(FIXTURE)
    evidence = build_recognition_evidence(part, rotational=False)
    (boss,) = evidence.result.bosses

    assert boss_spans_envelope(boss, part.bounding_box())

    class ShortBoss:
        axis = boss.axis
        location = boss.location
        height = boss.height / 2.0

    assert not boss_spans_envelope(ShortBoss(), part.bounding_box())


def test_absorption_fails_closed_when_the_boss_has_no_usable_face_evidence() -> None:
    """The two fail-closed arms the PR describes but never exercised.

    `boss_blend_owner_pairs` refuses a pair when the boss has no accepted occurrence, and
    again when that occurrence carries no defining faces — "an evidence-less build, an
    ambiguous face claim ... retains the existing boss path and fails closed". Both arms were
    uncovered, so nothing established that a missing join refuses rather than absorbs, which
    is the whole safety property: absorbing on absent evidence would silently delete a boss
    diameter from the sheet.
    """

    part = import_step(FIXTURE)
    evidence = build_recognition_evidence(part, rotational=False)
    (boss,) = evidence.result.bosses
    lower = max(evidence.result.blends, key=lambda blend: blend.radius)
    bbox = part.bounding_box()

    # Precondition: with real evidence this pair IS absorbed, so a later refusal is caused by
    # the evidence being withdrawn and not by the pair having been ineligible all along.
    assert boss_blend_owner_pairs(
        evidence, (boss,), (lower,), bbox=bbox, envelope_emittable=True
    ) == ((boss, lower),)

    class _NoOccurrence:
        """Evidence that accepts nothing: every `occurrence_for` lookup misses."""

        result = evidence.result
        features = ()

        def family(self, occurrence):
            raise AssertionError("no occurrence should be reachable")

        def record(self, occurrence):
            raise AssertionError("no occurrence should be reachable")

        def defining_faces(self, occurrence):
            raise AssertionError("no occurrence should be reachable")

    assert (
        boss_blend_owner_pairs(
            _NoOccurrence(), (boss,), (lower,), bbox=bbox, envelope_emittable=True
        )
        == ()
    )

    class _NoFaces:
        """Occurrences exist, but none of them names a defining face."""

        result = evidence.result
        features = evidence.features

        def family(self, occurrence):
            return evidence.family(occurrence)

        def record(self, occurrence):
            return evidence.record(occurrence)

        def defining_faces(self, occurrence):
            return ()

    assert (
        boss_blend_owner_pairs(_NoFaces(), (boss,), (lower,), bbox=bbox, envelope_emittable=True)
        == ()
    )


def test_feature_coverage_skips_the_reconciliation_when_either_inventory_is_empty() -> None:
    """A part with no boss or no blend must not pay a second solid `bounding_box()`.

    `bosses`/`blends` are inventories, so an empty one is not `None`; testing `is not None`
    measured the solid again on every lint of every part. That is what
    `test_relint_recomputes_no_annotation_boxes` caught, and this pins the cheaper predicate
    directly rather than relying on that guard's fixture happening to have no bosses.
    """

    from build123d import Box, Cylinder, Pos

    from draftwright.linting import coverage as coverage_module

    part = Box(60, 40, 20) - Pos(10, 5, 0) * Cylinder(4, 20)
    evidence = build_recognition_evidence(part, rotational=False)
    assert not evidence.result.bosses or not evidence.result.blends, (
        "fixture must have an empty side for this to be the skipped path"
    )

    calls = 0
    real = type(part).bounding_box

    def counting(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        return real(self, *args, **kwargs)

    drawing = build_drawing(part)
    original = type(part).bounding_box
    try:
        type(part).bounding_box = counting
        coverage_module.lint_feature_coverage(
            part,
            drawing.items,
            recognition_evidence=evidence,
            registry=drawing.registry,
        )
    finally:
        type(part).bounding_box = original

    assert calls == 0, f"the empty-inventory path measured the solid {calls} time(s)"


def test_detect_and_lint_decide_absorption_from_one_shared_predicate() -> None:
    """Both sides must answer "is the envelope emittable?" identically, or the sheet lies.

    They did not. `detect` called `_is_round`; `linting.coverage` inlined that function's body
    with its `0.5` copied in, and — the part that matters — read `turned_profiles` off the
    recognition result while detect uses the BUILD's set, which on a declared build is the
    caller's (`profiles = () if prof is None else (prof,)`). Disagreement means the IR absorbs
    a boss whose diameter lint still requires, reporting `feature_not_dimensioned` against a
    diameter the plan deliberately removed.

    Pinned structurally rather than by one fixture: a fixture only demonstrates the pair it
    happens to contain, and the defect is that two formulas exist at all.
    """

    import inspect

    from draftwright.linting import coverage as coverage_module
    from draftwright.model import detect as detect_module
    from draftwright.recognition_ownership import (
        BOSS_BLEND_DIAMETER_TOL,
        envelope_is_emittable,
    )

    # One formula: neither module may re-derive the roundness test locally. The check is the
    # inlined comparison itself, not `bbox.size.X`, which both modules use for other things.
    inlined = "abs(boss.diameter - bbox.size.X)"
    for module in (coverage_module, detect_module):
        source = inspect.getsource(module)
        assert inlined not in source, f"{module.__name__} re-inlines the roundness test"
        assert "envelope_is_emittable" in source, module.__name__
    # ...and the check can fire: this IS the string that was in coverage.py before #1451.
    assert inlined in "        abs(boss.diameter - bbox.size.X) <= 0.5"

    # One tolerance: two independent `0.15` literals agreed only by luck.
    detect_call = inspect.getsource(detect_module)
    coverage_call = inspect.getsource(coverage_module)
    for source in (detect_call, coverage_call):
        assert "diameter_tolerance=BOSS_BLEND_DIAMETER_TOL" in source
    assert BOSS_BLEND_DIAMETER_TOL == 0.15

    # One input: lint takes the build's profile set, so a declared profile reaches both.
    # Asserted BEHAVIOURALLY — that the argument changes the answer — because asserting the
    # parameter merely exists left the original defect (lint re-deriving the set from the
    # recognition result and ignoring what it was given) passing untouched under mutation.
    signature = inspect.signature(coverage_module.lint_feature_coverage)
    assert "turned_profiles" in signature.parameters
    # Read the module file, not `Drawing._lint`: `test_private_test_attr_reads` ratchets
    # private-attribute reads from tests downward, and this needs no such read.
    drawing_source = (
        Path(__file__).resolve().parent.parent / "src" / "draftwright" / "drawing.py"
    ).read_text(encoding="utf-8")
    assert "turned_profiles" in drawing_source, (
        "drawing.py must hand lint the profile set it gave detect"
    )

    # And the predicate itself refuses on any one of the three grounds.
    class _Bbox:
        size = type("S", (), {"X": 40.0, "Y": 40.0})()

    class _RoundBoss:
        diameter = 40.0

    assert envelope_is_emittable(bbox=_Bbox(), bosses=(), turned_profiles=(), polygonal_stock=())
    assert not envelope_is_emittable(
        bbox=_Bbox(), bosses=(_RoundBoss(),), turned_profiles=(), polygonal_stock=()
    )
    assert not envelope_is_emittable(
        bbox=_Bbox(), bosses=(), turned_profiles=("a profile",), polygonal_stock=()
    )
    assert not envelope_is_emittable(
        bbox=_Bbox(), bosses=(), turned_profiles=(), polygonal_stock=("stock",)
    )


def test_lint_honours_the_build_profile_set_rather_than_re_deriving_it() -> None:
    """The behavioural half of the shared decision: the argument must change the answer.

    GRM-04 has no turned profile, so recognition reports none and the boss is absorbed. A
    build that DECLARED a profile makes the envelope non-emittable, and detect would then keep
    the boss — so lint must keep its diameter in the physical inventory too. If lint re-derives
    the profile set from the recognition result it cannot see the declaration, and the two
    sides disagree. That is the defect this fixture exercises: it is not detectable by checking
    that a parameter is present, only by checking that passing it changes the outcome.
    """

    part = import_step(FIXTURE)
    evidence = build_recognition_evidence(part, rotational=False)
    drawing = build_drawing(FIXTURE, title="GRM-04")

    assert evidence.result.turned_profiles == (), "fixture must have no recognised profile"
    assert evidence.result.bosses and evidence.result.blends, "fixture must have both to pair"

    def _codes(**kwargs):
        return sorted(
            issue.code
            for issue in coverage_lint(
                part,
                drawing.items,
                recognition_evidence=evidence,
                registry=drawing.registry,
                **kwargs,
            )
        )

    absorbed = _codes()
    declared_profile = _codes(turned_profiles=("a declared turned profile",))

    assert absorbed != declared_profile, (
        "lint ignored the build's profile set and re-derived it from the recognition result"
    )
    # And in the direction that matters: declaring a profile stops the absorption, so the
    # boss diameter returns to the inventory and its missing callout is reported again.
    assert len(declared_profile) > len(absorbed), (absorbed, declared_profile)
