"""A machined flat's A/F callout is its only size definition (#914).

The engine already reported ``flat_dropped`` when the leader found no room, but that is a
*placement* report: it names what the layout could not do, not what the drawing no longer
says. ``lint_flat_coverage`` is the completeness half — it reads the finished sheet and
asks whether each recognised flat's across-flats size is stated anywhere at all.

The two are deliberately both emitted, so a test here asserts the pair, not one or the
other.
"""

from types import SimpleNamespace

import pytest
from build123d import Align, Box, Cylinder, Pos
from build123d_drafting.helpers import Draft, Leader

from draftwright import Sheet, build_drawing
from draftwright.drawing import _GEOMETRY_AWARE_CODES
from draftwright.linting import lint_flat_coverage
from draftwright.recognition import Flat, recognise_flats


def _flatted_shaft():
    """Z-axis stepped stock truncated to a 25 mm double-D — one flat group, one A/F."""
    part = Cylinder(20, 40) + Pos(0, 0, 30) * Cylinder(12, 20)
    return part & Box(25, 60, 100, align=(Align.CENTER, Align.CENTER, Align.CENTER))


def _codes(dwg):
    return [i.code for i in dwg.lint()]


@pytest.fixture(scope="module")
def flatted_shaft():
    return _flatted_shaft()


def test_the_fixture_really_has_a_flat_to_lose(flatted_shaft):
    """Precondition for every negative assertion below: a check that fires on nothing
    proves nothing, and an inventory of zero flats short-circuits before any of it."""
    flats = recognise_flats(flatted_shaft)
    assert flats, "no flat recognised — the rest of this module would pass vacuously"
    assert {round(f.across, 3) for f in flats} == {25.0}


def test_a_placed_flat_callout_is_not_reported(flatted_shaft):
    dwg = build_drawing(flatted_shaft)
    assert any(n.startswith("m_flat_") for n, _ in dwg.iter_annotations()), (
        "the callout must be placed here, or 'not reported' is free"
    )
    assert "flat_not_dimensioned" not in _codes(dwg)


@pytest.mark.parametrize(
    "title",
    [
        pytest.param("PART", id="ordinary-title"),
        # A title block's label is the drawing title, not a callout. Titling the part after
        # its own defining dimension must not satisfy the check (Codex #1011 r1).
        pytest.param("25 A/F", id="title-quoting-the-missing-callout"),
    ],
)
def test_a_dropped_flat_callout_is_reported_as_incomplete_not_merely_unplaced(
    flatted_shaft, title
):
    """The #914 case: the leader finds no room, and the drawing silently stops defining
    the flat. Both signals must appear — the cause and the consequence."""
    dwg = build_drawing(flatted_shaft, page="A4", scale=2.0, title=title)
    assert not any(n.startswith("m_flat_") for n, _ in dwg.iter_annotations()), (
        "this fixture is meant to DROP the callout; if it now places, the drop case is untested"
    )
    issues = {i.code: i for i in dwg.lint()}
    assert "flat_dropped" in issues, "the placement signal must survive — this adds to it"
    assert "flat_not_dimensioned" in issues
    assert "25 A/F" in issues["flat_not_dimensioned"].message


def test_the_completeness_failure_counts_as_a_geometry_issue(flatted_shaft):
    """``lint_summary()['geometry_issues']`` is what a non-interactive caller reads to tell
    a wrong drawing from a merely tight one. An undefined flat is wrong, not tight — so it
    belongs in that count, beside its sibling completeness codes (Codex #1011 r1)."""
    summary = build_drawing(flatted_shaft, page="A4", scale=2.0).lint_summary()
    assert summary["by_code"].get("flat_not_dimensioned"), "precondition: the check fired"
    assert summary["geometry_issues"] >= 1


def test_removing_the_callout_from_a_finished_sheet_reports_it(flatted_shaft):
    """Drawing-derived, not a build-time side channel: the same build lints clean, then
    dirty, with nothing changed but what is on the sheet."""
    dwg = build_drawing(flatted_shaft)
    assert "flat_not_dimensioned" not in _codes(dwg)
    for name, _ in list(dwg.iter_annotations()):
        if name.startswith("m_flat_"):
            dwg.remove(name)
    assert "flat_not_dimensioned" in _codes(dwg)


#: Where the module's single-flat fixture projects, under `_sheet`'s stub projection.
_AT_THE_FLAT = (12.5, 0.0)


def _sheet(*labels, tips=None):
    """A stand-in sheet carrying real ``Leader``s tipped at given page points.

    Real leaders, not stubs: the check looks at type AND tip, so a stub with only a label
    would sail past both. The stub ``at`` projects a part point to its own ``(x, y)``, which
    is enough to tell two flats apart — the property every association test here turns on.
    *tips* defaults to every leader pointing at :data:`_AT_THE_FLAT`, the module fixture's
    flat.
    """
    draft = Draft()
    if tips is None:
        tips = [_AT_THE_FLAT] * len(labels)
    return SimpleNamespace(
        items=[
            Leader(tip=(t[0], t[1], 0), elbow=(t[0] + 5, t[1] + 5, 0), label=x, draft=draft)
            for x, t in zip(labels, tips, strict=True)
        ],
        at=lambda view, x, y, z: (x, y, 0.0),
    )


@pytest.mark.parametrize(
    "label",
    [
        pytest.param("25 A/F", id="plain"),
        pytest.param("25 ±0.2 A/F", id="toleranced"),
        pytest.param("A/F 25.0", id="worded-the-other-way-round"),
        pytest.param("25.05 A/F", id="within-tolerance"),
        pytest.param("2× 25 A/F", id="quantity-prefixed"),
    ],
)
def test_a_callout_stating_the_size_counts_however_it_is_worded(flatted_shaft, label):
    """The *value* is matched, not the string. An authored tolerance rides the number
    (``25 ±0.2 A/F``), so a whole-string comparison would report a fully-defined drawing
    as incomplete — the #629 class of bug, one layer up."""
    assert lint_flat_coverage(flatted_shaft, _sheet(label), assembly=False) == []


@pytest.mark.parametrize(
    "label",
    [
        pytest.param("", id="nothing-at-all"),
        pytest.param("⌀40", id="the-stock-diameter"),
        pytest.param("25", id="the-number-without-the-callout"),
        # Prose that happens to contain the size. Anchoring, not the leader-only rule,
        # is what rejects this one — it arrives here already on a Leader (Codex #1011 r4).
        pytest.param("USE 25 A/F SPANNER", id="prose-quoting-the-size"),
        pytest.param("25 A/F; SEE NOTE 12", id="a-callout-with-prose-appended"),
    ],
)
def test_a_label_that_is_not_a_callout_does_not_count(flatted_shaft, label):
    issues = lint_flat_coverage(flatted_shaft, _sheet(label), assembly=False)
    assert [i.code for i in issues] == ["flat_not_dimensioned"]


def test_a_callout_of_the_wrong_size_is_a_mismatch_not_a_gap(flatted_shaft):
    """`12 A/F` on a 25 mm flat IS a callout — the drawing dimensions the feature, just
    wrongly. Reporting that as "no across-flats callout" would describe a sheet that
    plainly has one (Codex #1011 r5)."""
    issues = lint_flat_coverage(flatted_shaft, _sheet("12 A/F"), assembly=False)
    assert [i.code for i in issues] == ["flat_callout_mismatched"]
    assert "12 A/F" in issues[0].message and "25 A/F" in issues[0].message


def test_a_note_stating_the_size_does_not_define_the_flat(flatted_shaft):
    """A size defines a feature only when something POINTS at it. A note is user-positioned
    free text carrying no feature (``Drawing.note``'s own words), so it cannot define the
    flat however it is worded (Codex #1011 r4).

    The note text here is *exactly* the callout on purpose. Codex's repro — "USE 25 A/F
    SPANNER" — is rejected by the anchored pattern before the leader rule is ever reached,
    so a test using it stays green with the leader rule removed and proves nothing about it.
    """
    dwg = build_drawing(flatted_shaft)
    for name, _ in list(dwg.iter_annotations()):
        if name.startswith("m_flat_"):
            dwg.remove(name)
    assert "flat_not_dimensioned" in _codes(dwg), "precondition: the flat is undefined"
    dwg.note("25 A/F", (20, 20), view="front")
    assert "flat_not_dimensioned" in _codes(dwg), "a note is not a callout"


def test_a_non_leader_carrying_a_tip_does_not_define_a_flat(flatted_shaft):
    """The leader rule and the tip rule are independent, and each needs its own guard.

    Today's `Note` and `TitleBlock` have no tip, so the tip rule alone would keep them out —
    but that is incidental to those two classes, not the intent. "A callout is a leader" is
    the rule; an annotation type that grows a `tip` must not silently begin defining flats.
    The stub here is deliberately not a real annotation: it exists to be exactly the thing
    the type check, and nothing else, excludes.
    """
    sheet = SimpleNamespace(
        items=[SimpleNamespace(tip=_AT_THE_FLAT, label="25 A/F")],
        at=lambda view, x, y, z: (x, y, 0.0),
    )
    issues = lint_flat_coverage(flatted_shaft, sheet, assembly=False)
    assert [i.code for i in issues] == ["flat_not_dimensioned"]


def test_two_leaders_on_one_flat_do_not_define_a_second_one():
    """The r6 case, and the reason association is positional at all. Two callouts reading
    ``25 A/F`` both pointing at the X flat say nothing about a 25 mm Z flat elsewhere on the
    part — but under value matching they were simply two labels of the right value, and the
    undefined flat went unreported (Codex #1011 r6)."""
    flats = [Flat("x", 25.0, (0, 0, 0)), Flat("z", 25.0, (100, 0, 0))]
    sheet = _sheet("25 A/F", "25 A/F", tips=[(0, 0), (0, 0)])
    issues = lint_flat_coverage(Box(1, 1, 1), sheet, flats=flats, assembly=False)
    assert [i.code for i in issues] == ["flat_not_dimensioned"]
    assert "Z stock" in issues[0].message


def test_one_callout_cannot_define_two_flats_on_different_axes():
    """``render_flats`` collapses by ``(axis, across)``, so an X-stock and a Z-stock 25 mm
    flat are TWO callouts. A leader points at one flat; it cannot also define a differently
    oriented one somewhere else (Codex #1011 r2)."""
    flats = [Flat("x", 25.0, (0, 0, 0)), Flat("z", 25.0, (100, 0, 0))]
    part = Box(1, 1, 1)
    both = _sheet("25 A/F", "25 A/F", tips=[(0, 0), (100, 0)])
    assert lint_flat_coverage(part, both, flats=flats, assembly=False) == []
    issues = lint_flat_coverage(part, _sheet("25 A/F", tips=[(0, 0)]), flats=flats, assembly=False)
    assert [i.code for i in issues] == ["flat_not_dimensioned"]
    assert "Z stock" in issues[0].message


def test_one_callout_cannot_define_two_flats_whose_sizes_are_within_tolerance():
    """25.0 and 25.1 are two groups and two callouts. A single ``25.05 A/F`` is within the
    size window of both, so under value matching it was accepted twice; it is at one flat."""
    flats = [Flat("z", 25.0, (0, 0, 0)), Flat("z", 25.1, (100, 0, 0))]
    sheet = _sheet("25.05 A/F", tips=[(0, 0)])
    issues = lint_flat_coverage(Box(1, 1, 1), sheet, flats=flats, assembly=False)
    assert [i.code for i in issues] == ["flat_not_dimensioned"]


def test_a_tolerance_figure_cannot_stand_in_for_a_second_flat():
    """``25 ±12 A/F`` at the 25 mm flat defines that flat and says nothing about a 12 mm one
    elsewhere. The size is the label's first number, so the tolerance figure is not a size."""
    flats = [Flat("z", 25.0, (0, 0, 0)), Flat("x", 12.0, (100, 0, 0))]
    sheet = _sheet("25 ±12 A/F", tips=[(0, 0)])
    issues = lint_flat_coverage(Box(1, 1, 1), sheet, flats=flats, assembly=False)
    assert [i.code for i in issues] == ["flat_not_dimensioned"]
    assert "12 A/F" in issues[0].message, "the UNDEFINED flat must be the one named"


@pytest.mark.parametrize(
    ("tips", "expected"),
    [
        pytest.param([(0, 0), (100, 0)], [], id="one-leader-at-each-flat"),
        pytest.param([(100, 0), (0, 0)], [], id="and-in-the-other-order"),
        pytest.param([(0, 0), (0, 0)], ["flat_not_dimensioned"], id="both-at-the-first-flat"),
        pytest.param([(50, 0), (60, 0)], ["flat_not_dimensioned"] * 2, id="neither-at-a-flat"),
    ],
)
def test_coverage_follows_where_the_leaders_point(tips, expected):
    """Two same-sized flats and two identical labels: only the tips distinguish the cases, so
    every value-based scheme gives the same answer for all four and at least two of them are
    wrong. This replaces the maximum-matching guard from r3 — with association by position
    there is no pool to mis-assign, so that whole class of defect is structural, not tested
    for."""
    # Different AXES, so two groups: same axis and size would collapse to one group that a
    # leader at either face covers, which is the double-D case tested separately.
    flats = [Flat("z", 25.0, (0, 0, 0)), Flat("x", 25.0, (100, 0, 0))]
    sheet = _sheet("25 A/F", "25 A/F", tips=tips)
    issues = lint_flat_coverage(Box(1, 1, 1), sheet, flats=flats, assembly=False)
    assert [i.code for i in issues] == expected


def test_a_part_with_no_flats_is_left_alone():
    """The inventory short-circuits, so a prismatic part pays nothing and is never flagged."""
    assert lint_flat_coverage(Box(50, 50, 30), SimpleNamespace(items=[]), assembly=False) == []


def test_the_two_faces_of_a_double_d_are_one_definition(flatted_shaft):
    """``render_flats`` collapses flats sharing an axis and size into ONE callout, so the
    inventory groups the same way: two faces, one requirement, and a leader at EITHER face
    satisfies it — which is what the renderer does, trying each corner until one is clear."""
    faces = recognise_flats(flatted_shaft)
    assert len(faces) == 2
    assert len(lint_flat_coverage(flatted_shaft, _sheet(), assembly=False)) == 1
    for face in faces:
        at = (face.at[0], face.at[1])
        sheet = _sheet("25 A/F", tips=[at])
        assert lint_flat_coverage(flatted_shaft, sheet, assembly=False) == [], (
            f"a callout at the face at {at} must satisfy the group"
        )


def test_a_correctly_declared_flat_lints_clean():
    """The declared-IR path (ADR 0011) renders the caller's value. When it agrees with the
    geometry the drawing is complete, and coverage must say so."""
    sheet = Sheet(_flatted_shaft())
    sheet.flat(axis="z", across=25, at=(12.5, 0, 20))
    sheet.auto_dimensions()
    dwg = sheet.build()
    assert not [i for i in dwg.lint() if i.code.startswith("flat_")]


def test_a_declared_value_the_geometry_contradicts_is_a_mismatch_not_a_gap():
    """Declaring ``across=24`` on stock that measures 25 renders `24 A/F`. The drawing is
    wrong, and coverage still reports it — ground truth is the geometry, because a stale or
    mistaken declaration is exactly what this must catch (ADR 0015). But calling it "no
    across-flats callout" pointed at the wrong problem and read as plainly false to anyone
    looking at the sheet, which had one (Codex #1011 r5)."""
    sheet = Sheet(_flatted_shaft())
    sheet.flat(axis="z", across=24, at=(12.5, 0, 20))
    sheet.auto_dimensions()
    dwg = sheet.build()
    flat_issues = [i for i in dwg.lint() if i.code.startswith("flat_")]
    assert [i.code for i in flat_issues] == ["flat_callout_mismatched"]
    assert "24 A/F" in flat_issues[0].message and "25 A/F" in flat_issues[0].message
    # A wrong dimension is wrong, not tight — so it belongs in the geometry count beside
    # `flat_not_dimensioned`. Asserted against the register rather than the count: this
    # drawing also raises `feature_not_dimensioned`, so a `>= 1` on the count stayed green
    # with the new code unregistered and proved nothing.
    assert "flat_callout_mismatched" in _GEOMETRY_AWARE_CODES
    assert dwg.lint_summary()["geometry_issues"] == len(dwg.lint())


def test_a_missing_callout_is_still_a_gap_not_a_mismatch(flatted_shaft):
    """With no leftover callout to blame, the diagnosis stays 'nothing defines this flat' —
    the mismatch arm must not swallow the case the check exists for."""
    issues = lint_flat_coverage(flatted_shaft, _sheet(), assembly=False)
    assert [i.code for i in issues] == ["flat_not_dimensioned"]
