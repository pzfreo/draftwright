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

from draftwright import build_drawing
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


@pytest.mark.parametrize(
    "label",
    [
        pytest.param("25 A/F", id="plain"),
        pytest.param("25 ±0.2 A/F", id="toleranced"),
        pytest.param("A/F 25.0", id="worded-the-other-way-round"),
        pytest.param("25.05 A/F", id="within-tolerance"),
    ],
)
def test_a_label_stating_the_size_counts_however_it_is_worded(flatted_shaft, label):
    """The *value* is matched, not the string. An authored tolerance rides the number
    (``25 ±0.2 A/F``), so a whole-string comparison would report a fully-defined drawing
    as incomplete — the #629 class of bug, one layer up."""
    sheet = SimpleNamespace(items=[SimpleNamespace(label=label)])
    assert lint_flat_coverage(flatted_shaft, sheet, assembly=False) == []


@pytest.mark.parametrize(
    "label",
    [
        pytest.param("", id="nothing-at-all"),
        pytest.param("⌀40", id="the-stock-diameter"),
        pytest.param("12 A/F", id="a-different-size"),
        pytest.param("25", id="the-number-without-the-callout"),
    ],
)
def test_a_label_that_does_not_state_the_size_does_not_count(flatted_shaft, label):
    sheet = SimpleNamespace(items=[SimpleNamespace(label=label)])
    issues = lint_flat_coverage(flatted_shaft, sheet, assembly=False)
    assert [i.code for i in issues] == ["flat_not_dimensioned"]


def _sheet(*labels):
    return SimpleNamespace(items=[SimpleNamespace(label=x) for x in labels])


def test_one_callout_cannot_define_two_flats_on_different_axes():
    """``render_flats`` collapses by ``(axis, across)``, so an X-stock and a Z-stock 25 mm
    flat are TWO callouts on the sheet. A leader points at one flat; it cannot also define a
    differently oriented one. Matching on value alone let a single label cover both
    (Codex #1011 r2)."""
    flats = [Flat("x", 25.0, (0, 0, 0)), Flat("z", 25.0, (0, 0, 0))]
    part = Box(1, 1, 1)
    assert lint_flat_coverage(part, _sheet("25 A/F", "25 A/F"), flats=flats, assembly=False) == []
    issues = lint_flat_coverage(part, _sheet("25 A/F"), flats=flats, assembly=False)
    assert [i.code for i in issues] == ["flat_not_dimensioned"]
    assert "Z stock" in issues[0].message


def test_one_callout_cannot_define_two_flats_whose_sizes_are_within_tolerance():
    """25.0 and 25.1 are two groups and two callouts. A single ``25.05 A/F`` is within the
    match window of both, and independent comparisons therefore accepted it twice."""
    flats = [Flat("z", 25.0, (0, 0, 0)), Flat("z", 25.1, (0, 0, 1))]
    issues = lint_flat_coverage(Box(1, 1, 1), _sheet("25.05 A/F"), flats=flats, assembly=False)
    assert [i.code for i in issues] == ["flat_not_dimensioned"]


def test_a_tolerance_figure_cannot_stand_in_for_a_second_flat():
    """``25 ±12 A/F`` defines the 25 mm flat and says nothing about a 12 mm one. The size
    is the label's FIRST number, so the tolerance figure neither covers the second flat nor
    — the subtler failure — claims the label and leaves the message blaming the 25."""
    flats = [Flat("z", 25.0, (0, 0, 0)), Flat("x", 12.0, (0, 0, 0))]
    issues = lint_flat_coverage(Box(1, 1, 1), _sheet("25 ±12 A/F"), flats=flats, assembly=False)
    assert [i.code for i in issues] == ["flat_not_dimensioned"]
    assert "12 A/F" in issues[0].message, "the UNDEFINED flat must be the one named"


def test_a_part_with_no_flats_is_left_alone():
    """The inventory short-circuits, so a prismatic part pays nothing and is never flagged."""
    assert lint_flat_coverage(Box(50, 50, 30), SimpleNamespace(items=[]), assembly=False) == []


def test_the_two_faces_of_a_double_d_are_one_definition(flatted_shaft):
    """``render_flats`` collapses flats sharing an axis and size into ONE callout, so the
    inventory groups the same way. Two faces, one unsatisfied requirement — not two."""
    assert len(recognise_flats(flatted_shaft)) == 2
    issues = lint_flat_coverage(flatted_shaft, SimpleNamespace(items=[]), assembly=False)
    assert len(issues) == 1
