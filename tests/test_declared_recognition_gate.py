"""A declared build recognises nothing; critique on that path recognises once (#1022).

ADR 0011 says a caller-supplied ``PartModel`` skips detection, and ADR 0017 §6 restates it for
the aggregate. Neither held: ``_analyse`` ran ``build_recognition_result`` before it knew
whether a model had been declared, so a declared build recognised the full inventory and threw
most of it away.

The gate is not one ``if``. Page and scale selection read the turned profile and the step-level
ladder off that recognition, so sizing had to source them from the declaration first; and the
lint→repair loop asked for the full critique on every iteration while acting only on
``dim_inside_part``, which forced the recognition back in through the side door. Both halves
are guarded here, because either one alone leaves the acceptance unmet.

Counted by code object (``conftest.counting_calls``) rather than by patching module bindings —
see that helper for why a binding-level spy cannot be trusted for this claim.
"""

import inspect
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from build123d import Align, Box, Cylinder, Pos, Rot
from conftest import counting_calls

import draftwright.recognition as recognition
from draftwright import Sheet, build_drawing
from draftwright.compose import _est_right_strip_depth, _n_right_strip_boss_heights
from draftwright.linting.coverage import lint_prismatic_coverage
from draftwright.recognition import (
    RecognitionResult,
    build_recognition_result,
    project_step_shoulders,
    recognise_risers,
    step_level_zs,
)
from draftwright.recognition.result import DEFERRED, MIGRATED


@contextmanager
def _counting_every_family():
    with counting_calls(
        {name: getattr(recognition, name) for name in MIGRATED | DEFERRED.keys()}
    ) as counts:
        yield counts


def _issue_keys(issues) -> tuple:
    """A stable, order-insensitive representation of a lint result.

    Sorted because the issue list is assembled from several checks and their relative order is
    not part of the contract; message included because two issues of one code differ only
    there (which hole, which shoulder).
    """
    return tuple(sorted((i.code, i.severity, i.message) for i in issues))


def _prismatic_plate():
    return Box(80, 60, 20) - Pos(0, 0, 0) * Cylinder(5, 20)


def _declared_plate_sheet():
    part = _prismatic_plate()
    sheet = Sheet(part).auto_dimensions()
    sheet.envelope()
    sheet.hole(Pos(0, 0, 0) * Cylinder(5, 20))
    return part, sheet


def _turned_shaft_parts():
    """Three coaxial segments, each longer than it is wide so the axis is unambiguous."""
    a = Pos(-42.5, 0, 0) * Rot(0, 90, 0) * Cylinder(9, 25)
    b = Pos(-10, 0, 0) * Rot(0, 90, 0) * Cylinder(15, 40)
    c = Pos(25, 0, 0) * Rot(0, 90, 0) * Cylinder(11, 30)
    return a, b, c


def test_a_declared_build_recognises_nothing():
    """The headline acceptance. Includes the repair loop, which runs inside ``build_drawing``
    and used to force a full critique — gating ``_analyse`` alone left this at ten families,
    every one reached through ``repair() -> lint()`` rather than through detection.
    """
    _, sheet = _declared_plate_sheet()
    with _counting_every_family() as counts:
        sheet.build()

    assert dict(counts) == {}, (
        f"a declared build recognised {dict(counts)}. ADR 0011 says a caller-supplied model "
        "skips detection — every one of these scanned a solid whose features the caller had "
        "already stated."
    )


def test_exporting_a_declared_drawing_pays_for_one_aggregate_and_no_more(tmp_path):
    """``export`` calls ``_lint_and_log``, whose warnings the user reads — so exporting *is*
    asking for physical critique, and the lazy aggregate is the honest price.

    Deliberately not "export recognises nothing": the alternative was making ``_lint_and_log``
    skip coverage, which would stop every export on BOTH paths from reporting coverage
    problems. That trades a real diagnostic for a benchmark number. What must hold is that the
    price is paid once — a second export reuses the aggregate.
    """
    _, sheet = _declared_plate_sheet()
    drawing = sheet.build()

    with _counting_every_family() as counts:
        drawing.export(str(tmp_path / "first"), formats=("svg",))
        first = dict(counts)
        counts.clear()
        drawing.export(str(tmp_path / "second"), formats=("svg",))
        second = dict(counts)

    assert first.get("recognise_holes") == 1, (
        "the first export's lint-and-log found no inventory to judge against"
    )
    assert dict(second) == {}, f"a second export re-recognised {dict(second)}"


def test_a_detected_build_still_recognises_each_family_once():
    """The gate must not fire on the automatic path — the counterexample that stops
    :func:`test_a_declared_build_and_export_recognises_nothing` from being satisfied by
    breaking recognition outright."""
    with _counting_every_family() as counts:
        build_drawing(_prismatic_plate())

    assert counts, "a detected build recognised nothing — the gate fired on the wrong path"
    for family in MIGRATED:
        assert counts.get(family) == 1, (
            f"{family} ran {counts.get(family, 0)}× on the detected path, not once"
        )


def test_critique_on_a_declared_drawing_recognises_once_and_then_never_again():
    """A declared drawing has no inventory to judge against, so the first *explicit* physical
    lint builds one — once, and cached in the typed ``BuildState``.

    No exemptions since #1025: the last family that rescanned per lint
    (``recognise_step_shoulders``) split into evidence the aggregate owns plus a pure
    projection, so repeated critique now recognises nothing at all.
    """
    _, sheet = _declared_plate_sheet()
    drawing = sheet.build()

    with _counting_every_family() as counts:
        first_issues = _issue_keys(drawing.lint())
        first = dict(counts)
        counts.clear()
        second_issues = _issue_keys(drawing.lint())
        third_issues = _issue_keys(drawing.lint())
        later = dict(counts)

    assert first.get("recognise_holes") == 1, (
        "the first physical lint of a declared drawing must obtain one aggregate — without it "
        "coverage judges against an empty inventory and reports every real feature as missing"
    )
    assert dict(later) == {}, (
        f"two further lints re-ran {dict(later)} — the aggregate is supposed to be built once "
        "and reused, so a second scan is a cache that is not being hit"
    )
    # Counting alone would pass an implementation whose later lints reuse the aggregate and
    # return LESS — placement issues only, or nothing. "Obtains no new aggregate" and "returns
    # the same answer" are separate claims and the second is the one a user would notice.
    assert second_issues == first_issues, (
        "the second lint of a declared drawing disagreed with the first while recognising "
        f"nothing new: {sorted(set(first_issues) ^ set(second_issues))}"
    )
    assert third_issues == first_issues, "the third lint disagreed with the first"


#: Every way ``export`` can reject a call outright, with the message it rejects it by.
#: Parametrised rather than written once because the first fix covered only the unknown-format
#: path and left the `dpi` check where it was — a second review round found the parallel path
#: still recognising. A rejection reason added here without moving its check ahead of
#: ``_lint_and_log`` fails, which is the property that generalises.
_REJECTED_EXPORTS = [
    ({"formats": ("bogus",)}, "unknown export format"),
    ({"formats": ("png",), "dpi": 0}, "dpi > 0"),
    ({"formats": ("png",), "dpi": -150}, "dpi > 0"),
]


@pytest.mark.parametrize(("kwargs", "message"), _REJECTED_EXPORTS)
def test_a_rejected_export_does_not_recognise_first(kwargs, message):
    """``export`` validates, then lints. It used to lint first, so a call that could not
    possibly succeed scanned the whole solid and only then reported the fault.

    Harmless while the critique was free — on a detected build the inventory already exists —
    but #1022 made it the trigger for the lazy aggregate, so a broken call started paying for
    recognition it could never use. Same reasoning the surrounding code already applies to the
    deprecation warning: report the fault that matters before doing the work.
    """
    _, sheet = _declared_plate_sheet()
    drawing = sheet.build()

    with _counting_every_family() as counts:
        with pytest.raises(ValueError, match=message):
            drawing.export("out", **kwargs)

    assert dict(counts) == {}, (
        f"a rejected export ({kwargs}) recognised {dict(counts)} before raising — the call "
        "was never going to produce a drawing to critique"
    )


def test_the_lazy_aggregate_is_owned_by_the_build_state():
    """ADR 0017 §6: one owner. ``Drawing._cyl_cache`` is the existing violation (#1023) and
    this must not add a second — the aggregate lands in ``BuildState``, which is where a
    finished drawing already keeps its build context.
    """
    _, sheet = _declared_plate_sheet()
    drawing = sheet.build()
    assert drawing.recognition() is None, "a declared build should not have recognised yet"

    drawing.lint()

    first = drawing.recognition()
    assert first is not None, "the aggregate did not land in BuildState"
    # Read through `Drawing.recognition()`, the public accessor over ``BuildState.recognition``
    # — reaching into `_build` here would be the test-side private read #741 ratchets, and the
    # claim is about the aggregate being reachable and stable, which the accessor states.
    drawing.lint()
    assert drawing.recognition() is first, "a second lint replaced the aggregate"


def test_repair_asks_for_the_placement_critique_only():
    """Repair acts on ``dim_inside_part`` and nothing else (ADR 0002), so it has no use for
    the feature-coverage half — and asking for it is what forced recognition back onto the
    declared path after ``_analyse`` was gated.
    """
    seen: list[bool] = []
    drawing = build_drawing(Box(60, 40, 20))
    real_lint = drawing.lint

    def spy(*, physical=True):
        seen.append(physical)
        return real_lint(physical=physical)

    drawing.lint = spy
    drawing.repair()

    assert seen, "repair did not lint at all"
    assert not any(seen), (
        f"repair requested the physical critique ({seen}) — it acts only on placement codes, "
        "so the coverage half is computed and discarded every iteration"
    )


def test_a_declared_turned_part_keeps_axial_critique():
    """``Analysis.prof`` feeds ``lint_axial_coverage``. Gating recognition without a
    declaration-sourced replacement would leave it ``None`` and silently disable axial
    critique for every declared turned part — a gate that quietly removes a check is worse
    than the scan it saves.

    Asserted through what the profile PRODUCES rather than by reading it off ``Analysis``: one
    step-length dimension per declared segment, and the check firing when they are removed. A
    ``prof`` that is present but wrong — one step, or the wrong axis — passes "is not None"
    and fails both of these.
    """
    a, b, c = _turned_shaft_parts()
    sheet = Sheet(a + b + c).auto_dimensions()
    sheet.step(a)
    sheet.step(b)
    sheet.step(c)
    with _counting_every_family() as counts:
        drawing = sheet.build()
    assert dict(counts) == {}, f"the declared turned path recognised {dict(counts)}"

    lengths = sorted(n for n in drawing.annotations() if n.startswith("m_steplen"))
    assert len(lengths) == 3, (
        f"expected one step-length dim per declared segment, got {lengths} — the declared "
        "profile reached the renderer with the wrong step count or axis"
    )
    assert not [i for i in drawing.lint() if i.code == "axial_length_missing"]

    for name in [n for n in drawing.annotations() if n.startswith("m_steplen")][:2]:
        drawing.remove(name)

    assert [i for i in drawing.lint() if i.code == "axial_length_missing"], (
        "removing two step-length dims produced no axial_length_missing — the declared "
        "profile is present but critique is not actually judging against it"
    )


def test_a_declared_boss_height_is_reserved_room_in_the_right_strip():
    """A boss height shares the front view's right strip with the step ladder, but the depth
    estimate counted only ladder steps — the strip was sized for one occupant class and solved
    for two.

    On a detected build the ladder's own reservation absorbed the boss height, so the defect
    was invisible. A declared model that states a boss and no ladder reserved one slot, needed
    two, and the height was dropped — silently, because ``render_boss_heights`` sets
    ``on_drop`` to a no-op so the omission is reported once by lint rather than twice.
    """
    wall, length, width, height = 3.0, 90, 64, 38
    pocket = Pos(0, 0, -wall) * Box(length - 2 * wall, width - 2 * wall, height)
    boss = Pos(0, 0, height / 2 + 5) * Cylinder(14, 10)
    bore = Pos(0, 0, 15) * Cylinder(6, 40)
    cover = Box(length, width, height) - pocket + boss - bore

    sheet = Sheet(cover, title="C").auto_dimensions()
    sheet.envelope()
    sheet.boss(boss)
    sheet.hole(bore).through()
    sheet.pocket(pocket)
    drawing = sheet.build()

    # The fixture only exercises the case while it declares NO height ladder: with one, the
    # ladder's own slots would hide the missing boss-height slot exactly as they do on the
    # detected path. Asserted off the declared model rather than `Analysis.step_zs`, which
    # says the same thing one derivation later and is a test-side private read (#741).
    assert not [f for f in drawing.model().features if f.kind == "step_level"], (
        "fixture no longer exercises the case — it needs a declared boss with NO declared "
        "height ladder, so the strip's only reserved slot is the envelope height"
    )
    assert [n for n in drawing.annotations() if n.startswith("m_bossheight_")], (
        "the declared boss height was dropped: the right strip reserved a slot for the "
        "envelope height only, leaving the boss height nowhere to go"
    )
    assert drawing.lint_summary()["by_code"].get("boss_height_missing", 0) == 0


def test_the_right_strip_estimate_counts_boss_heights():
    """The unit behind the case above: an extra occupant is an extra slot."""
    assert _est_right_strip_depth(0, 1) > _est_right_strip_depth(0, 0)
    assert _est_right_strip_depth(2, 1) == _est_right_strip_depth(3, 0)


@pytest.mark.parametrize(
    ("declare", "expected", "why"),
    [
        (lambda s, boss: s.boss(boss), 1, "a Z-axis boss with a height lands in the right strip"),
        (lambda s, boss: None, 0, "no boss declared, no slot"),
    ],
)
def test_only_the_bosses_that_land_right_are_counted(declare, expected, why):
    """Mirrors ``render_boss_heights``' own guards rather than reserving for every boss —
    over-reserving costs page space on every build that has one."""
    boss = Pos(0, 0, 15) * Cylinder(14, 10)
    sheet = Sheet(Box(90, 64, 20) + boss).auto_dimensions()
    sheet.envelope()
    declare(sheet, boss)
    model = sheet.build().model()

    assert _n_right_strip_boss_heights(model) == expected, why


def test_a_turned_model_reserves_no_boss_height_slots():
    """``render_boss_heights`` returns early for a turned part, so reserving for one there
    would be dead page space."""
    a, b, c = _turned_shaft_parts()
    sheet = Sheet(a + b + c).auto_dimensions()
    sheet.step(a)
    sheet.step(b)
    sheet.step(c)

    assert _n_right_strip_boss_heights(sheet.build().model()) == 0


def _rebated_block():
    """A full-span step: one riser, one interior level, one shoulder to locate."""
    return Box(60, 40, 20, align=(Align.MIN,) * 3) - Pos(30, 0, 10) * Box(
        30, 40, 10, align=(Align.MIN,) * 3
    )


def test_lint_projects_the_shared_riser_evidence_and_rescans_nothing():
    """#1025's headline: the last per-lint rescan is gone.

    ``recognise_step_shoulders`` did a full face scan on every critique pass, because its
    answer depended on the caller's level set and ADR 0015 forbids lint taking that from the
    model. Splitting the scan (``recognise_risers``) from the filter
    (``project_step_shoulders``) gives the aggregate something single-valued to own, and each
    consumer projects. This is phase 0's third guard for epic #1018.
    """
    drawing = build_drawing(_rebated_block())

    with _counting_every_family() as counts:
        drawing.lint()
        drawing.lint()

    assert dict(counts) == {}, (
        f"linting a built drawing recognised {dict(counts)} — critique is supposed to judge "
        "against the build's inventory, not scan the solid again"
    )


def test_the_critique_rejects_an_assembled_shoulder_inventory():
    """A completeness check must not accept an argument that can silence it.

    ``lint_prismatic_coverage`` used to take ``step_zs=``, and it *fully determined* the
    shoulder answer: ``step_zs=[]`` produced no shoulders, so ``missing_transitions`` was
    structurally zero and ``unrecognised_defining_geometry`` could never fire. The engine
    never passed that — ``Analysis.step_zs`` is a pure function of the solid — but a
    false-negative door in a completeness check is the one place a clean absence is
    indistinguishable from a clean part.

    The signature half is necessary but NOT sufficient, which the first cut of this test got
    wrong: asserting that a `recognition=` parameter exists says nothing about whether a
    caller can narrow it. A duck-typed `SimpleNamespace(risers=(), step_levels=())` silenced
    the check exactly as `step_zs=[]` had — the same door wearing a new parameter name (Codex
    #1031 r1). So the behavioural half is the one with teeth: an assembled stand-in is
    REJECTED.

    **What this does NOT establish**, deliberately named rather than implied: the type check
    proves neither provenance nor correspondence with *part*. A caller can still pass
    ``build_recognition_result(some_other_solid)``, or construct a valid frozen result with
    empty inventories, and this function will use it (Codex #1031 r2). That is true of every
    injected inventory in the engine — `holes=`, `pockets=`, `pads=` — and is the accepted
    cost of ADR 0008 Amdt 5 dependency injection, not something specific to this parameter.
    Closing it needs an unforgeable association between an aggregate and its solid, which is
    tracked separately (#1032); singling out this one parameter would be inconsistent and
    would still not make the check fail-closed.

    So the test is named for what it does: it rejects an ASSEMBLED inventory, which is the
    accidental form. It does not claim to reject a mismatched genuine one.
    """
    params = inspect.signature(lint_prismatic_coverage).parameters
    assert "step_zs" not in params, (
        "lint_prismatic_coverage grew back a caller-supplied level set — that argument "
        "decides the shoulder answer, so a caller passing [] silences the check entirely"
    )
    assert "step_shoulders" not in params, (
        "the critique must recognise its own shoulder inventory rather than accept one"
    )

    part = _rebated_block()
    empty_stand_in = SimpleNamespace(risers=(), step_levels=())
    with pytest.raises(TypeError, match="RecognitionResult"):
        lint_prismatic_coverage(part, [], features=(), recognition=empty_stand_in)

    # And the real thing still works — the guard rejects impostors, not the parameter.
    assert isinstance(
        lint_prismatic_coverage(part, [], features=(), recognition=build_recognition_result(part)),
        list,
    )


def test_the_two_consumers_project_the_same_evidence_differently():
    """The split's whole point: one scan, two answers.

    Model construction filters levels by plate and pocket ownership; critique must not (ADR
    0015 — lint's inventory cannot come from the model). If the projection ignored its
    ``levels`` argument, both would agree and the deferral this replaced would have been
    pointless. A projection that discriminates is what makes one shared scan sufficient.
    """
    part = _rebated_block()
    risers = recognise_risers(part)
    assert risers, "fixture stopped producing riser evidence"

    everything = project_step_shoulders(risers, levels=step_level_zs(part))
    nothing = project_step_shoulders(risers, levels=[])
    unrelated = project_step_shoulders(risers, levels=[999.0])

    assert everything, "the real level set produced no shoulders"
    assert nothing == [], "an empty level set must locate no shoulder"
    assert unrelated == [], "a level no riser sits on must locate no shoulder"


def test_a_two_stage_call_matches_the_old_one_stage_one_at_any_tolerance():
    """The split must not change what a NON-default tolerance means.

    The old `recognise_step_shoulders(part, levels=..., tol=t)` used one `t` for the geometric
    gates and the level ties. Split naively, `recognise_risers(part, tol=0.1)` followed by a
    bare `project_step_shoulders(...)` mixed 0.1 with the projection's own default — so a
    riser 0.3 mm off a level was rejected before and accepted after (Codex #1031 r1). The
    evidence carries the tolerance it was scanned with, so the natural two-stage call stays
    equivalent.
    """
    part = _rebated_block()

    for tol in (0.1, 0.5, 2.0):
        risers = recognise_risers(part, tol=tol)
        assert all(r.tol == tol for r in risers), "evidence lost the tolerance it was scanned with"
        # The bare projection must behave as if it had been told `tol` explicitly.
        assert project_step_shoulders(risers, levels=[10.4]) == project_step_shoulders(
            risers, levels=[10.4], tol=tol
        ), f"tol={tol}: the bare projection did not inherit the scan's tolerance"

    # And the inheritance is load-bearing, not incidental: a level 0.3mm off is inside the
    # loose scan's tolerance and outside the tight one's.
    loose = project_step_shoulders(recognise_risers(part, tol=0.5), levels=[10.3])
    tight = project_step_shoulders(recognise_risers(part, tol=0.1), levels=[10.3])
    assert loose != tight, "fixture no longer distinguishes the two tolerances"


def _turned_shaft_with_blind_bore():
    """A Z-turned shaft whose blind-bore floor is a horizontal face level but NOT an OD
    shoulder — so the two candidate ladders genuinely differ."""
    part = Cylinder(20, 30) + Pos(0, 0, 30) * Cylinder(14, 30)
    return part - Pos(0, 0, 45) * Cylinder(6, 30)


def test_one_ladder_rule_serves_sizing_and_critique():
    """Sizing and critique must converge on the SAME step ladder.

    For a Z-turned part the ladder is the turned profile's shoulders, not the raw horizontal
    face levels — that distinction exists because a blind bore's flat floor is a face level
    and is emphatically not an OD shoulder. `_analyse` had that rule; critique derived its own
    level set separately, so lint could project risers over a different ladder than the model
    was sized from (Codex #1031 r3). `RecognitionResult.step_ladder` is now the one rule both
    call.

    The fixture is chosen so the two candidate sets actually differ; on an ordinary prismatic
    part they coincide and this would assert nothing.
    """
    part = _turned_shaft_with_blind_bore()
    rec = build_recognition_result(part)
    bb = part.bounding_box()

    ladder = rec.step_ladder(bb)
    raw_level_zs = [level.z for level in rec.step_levels]
    assert ladder != raw_level_zs, (
        "fixture stopped exercising the divergence — the turned ladder and the raw face "
        "levels coincide here, so this test would pass with the rule removed"
    )
    assert 30.0 not in ladder, "the blind-bore floor leaked into the turned ladder"
    assert 30.0 in raw_level_zs, "fixture no longer produces the bore floor as a face level"

    # And sizing goes through that same rule rather than re-deriving it. Asserted by counting
    # the call, because on a turned part `step_zs` feeds only the strip reservation — there is
    # no rendered rung to read the difference off, so a behavioural assertion here would be
    # vacuous and would pass with the consolidation removed (verified: it did).
    with counting_calls({"ladder": RecognitionResult.step_ladder}) as calls:
        drawing = build_drawing(part)
        sizing = dict(calls)
        calls.clear()
        drawing.lint()
        critique = dict(calls)

    assert sizing.get("ladder"), (
        "the build never called RecognitionResult.step_ladder — sizing is deriving its own "
        "ladder again, which is the drift this consolidation removes"
    )
    # Both consumers, asserted separately. Counting across the whole build satisfied the
    # assertion from `_analyse`'s call alone, so critique could revert to the raw face levels
    # and this guard would stay green — the regression it names, undetected (Codex #1031 r2).
    assert critique.get("ladder"), (
        "lint never called RecognitionResult.step_ladder — critique is projecting risers over "
        "a different ladder than the model was sized from"
    )
    lengths = sorted(n for n in drawing.annotations() if n.startswith("m_steplen"))
    assert len(lengths) == 2, f"expected one length per turned segment, got {lengths}"
