"""A machined flat's A/F callout is its only size definition (#914).

The engine already reported ``flat_dropped`` when the leader found no room, but that is a
*placement* report: it names what the layout could not do, not what the drawing no longer
says. ``lint_flat_coverage`` is the completeness half — it reads the finished sheet and
asks whether each recognised flat's across-flats size is stated anywhere at all.

The two are deliberately both emitted, so a test here asserts the pair, not one or the
other.
"""

import warnings
from types import SimpleNamespace

import pytest
from build123d import Align, Box, Cylinder, Pos, Rot
from build123d_drafting.helpers import Draft, Leader

from draftwright import Sheet, build_drawing
from draftwright._core import _tol_suffix
from draftwright.annotations.from_model import _flat_label
from draftwright.drawing import _GEOMETRY_AWARE_CODES
from draftwright.fits import fit_deviation
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


def _sheet(*labels, tips=None, scale=1.0, view=None):
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
    leaders = [
        (
            f"m_flat_{i}",
            Leader(tip=(t[0], t[1], 0), elbow=(t[0] + 5, t[1] + 5, 0), label=x, draft=draft),
        )
        for i, (x, t) in enumerate(zip(labels, tips, strict=True))
    ]
    return SimpleNamespace(
        items=[ldr for _, ldr in leaders],
        # Callouts are read per view, so the stub must answer that question: *view* is the one
        # these leaders are owned by, and every other view sees nothing.
        annotations_in_view=lambda asked: leaders if view is None or asked == view else [],
        at=lambda _view, x, y, z: (x * scale, y * scale, 0.0),
    )


@pytest.mark.parametrize(
    "label",
    [
        pytest.param("25 A/F", id="plain"),
        pytest.param("25 ±0.2 A/F", id="toleranced"),
        pytest.param("A/F 25.0", id="worded-the-other-way-round"),
        pytest.param("25.05 A/F", id="within-tolerance"),
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
        # The tolerance region between the size and "A/F" is any run of NON-LETTERS, wide
        # enough for every `_tol_suffix` form without having to enumerate them. Excluding
        # letters is what stops that width swallowing prose (Codex #1011 r8).
        pytest.param("25 THREADED A/F", id="prose-between-the-size-and-the-token"),
        # The tolerance region needs a SIGN and at least one digit. Without that it was any
        # run of non-letters, so these read as definitions (Codex #1011 r9).
        pytest.param("25 123 A/F", id="an-unsigned-number-is-not-a-tolerance"),
        pytest.param("25 + A/F", id="a-sign-with-no-value"),
        pytest.param("25 --- A/F", id="punctuation-only"),
        # No quantity prefix. `_flat_label` never writes a count, so accepting `n×` was
        # support for a form nothing produces — and it let `0× 25 A/F`, which asserts the
        # flat is not there, certify it as defined (Codex #1011 r16).
        pytest.param("2× 25 A/F", id="a-quantity-the-engine-never-writes"),
        pytest.param("0× 25 A/F", id="a-quantity-that-denies-the-flat"),
        pytest.param("999x 25 A/F", id="an-absurd-quantity"),
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


def _two_lobes():
    """One solid with TWO parallel Z lobes, each truncated to the same 25 mm A/F — the case
    an axis-letter grouping conflates with a single double-D."""

    def lobe(x):
        return Pos(x, 0, 0) * (
            Cylinder(20, 40) & Box(25, 60, 40, align=(Align.CENTER, Align.CENTER, Align.CENTER))
        )

    return Pos(0, 0, -24) * Box(160, 40, 8) + lobe(-50) + lobe(50)


def _two_coaxial_regions():
    """ONE shaft with two 25 A/F machined regions at different Z stations, joined by a plain
    waist — two definitions that share an axis line and a size."""
    C = (Align.CENTER, Align.CENTER, Align.CENTER)

    def region(z):
        return Pos(0, 0, z) * (Cylinder(20, 40) & Box(25, 60, 40, align=C))

    return region(0) + Pos(0, 0, 45) * Cylinder(6, 50) + region(90)


def _sheet_at(*tips, scale=1.0, view=None):
    """A sheet carrying one ``25 A/F`` leader per tip, tips given in PART space and projected
    by *scale* — so a test can put two flats close together on the page without moving them
    on the part."""
    return _sheet(
        *["25 A/F"] * len(tips),
        tips=[(t[0] * scale, t[1] * scale) for t in tips],
        scale=scale,
        view=view,
    )


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
        annotations_in_view=lambda _asked: [
            ("n0", SimpleNamespace(tip=_AT_THE_FLAT, label="25 A/F"))
        ],
        at=lambda _view, x, y, z: (x, y, 0.0),
    )
    issues = lint_flat_coverage(flatted_shaft, sheet, assembly=False)
    assert [i.code for i in issues] == ["flat_not_dimensioned"]


def test_two_leaders_on_one_flat_do_not_define_a_second_one():
    """The r6 case, and the reason association is positional at all. Two callouts reading
    ``25 A/F`` both pointing at the X flat say nothing about a 25 mm Z flat elsewhere on the
    part — but under value matching they were simply two labels of the right value, and the
    undefined flat went unreported (Codex #1011 r6)."""
    flats = [Flat("x", 25.0, (0, 0, 0), (0, 0, 0)), Flat("z", 25.0, (100, 0, 0), (100, 0, 0))]
    sheet = _sheet("25 A/F", "25 A/F", tips=[(0, 0), (0, 0)])
    issues = lint_flat_coverage(Box(1, 1, 1), sheet, flats=flats, assembly=False)
    assert [i.code for i in issues] == ["flat_not_dimensioned"]
    assert "Z stock" in issues[0].message


def test_one_callout_cannot_define_two_flats_on_different_axes():
    """``render_flats`` collapses by ``(axis, across)``, so an X-stock and a Z-stock 25 mm
    flat are TWO callouts. A leader points at one flat; it cannot also define a differently
    oriented one somewhere else (Codex #1011 r2)."""
    flats = [Flat("x", 25.0, (0, 0, 0), (0, 0, 0)), Flat("z", 25.0, (100, 0, 0), (100, 0, 0))]
    part = Box(1, 1, 1)
    both = _sheet("25 A/F", "25 A/F", tips=[(0, 0), (100, 0)])
    assert lint_flat_coverage(part, both, flats=flats, assembly=False) == []
    issues = lint_flat_coverage(part, _sheet("25 A/F", tips=[(0, 0)]), flats=flats, assembly=False)
    assert [i.code for i in issues] == ["flat_not_dimensioned"]
    assert "Z stock" in issues[0].message


def test_one_callout_cannot_define_two_flats_whose_sizes_are_within_tolerance():
    """25.0 and 25.1 are two groups and two callouts. A single ``25.05 A/F`` is within the
    size window of both, so under value matching it was accepted twice; it is at one flat."""
    flats = [Flat("z", 25.0, (0, 0, 0), (0, 0, 0)), Flat("z", 25.1, (100, 0, 0), (100, 0, 0))]
    sheet = _sheet("25.05 A/F", tips=[(0, 0)])
    issues = lint_flat_coverage(Box(1, 1, 1), sheet, flats=flats, assembly=False)
    assert [i.code for i in issues] == ["flat_not_dimensioned"]


def test_a_tolerance_figure_cannot_stand_in_for_a_second_flat():
    """``25 ±12 A/F`` at the 25 mm flat defines that flat and says nothing about a 12 mm one
    elsewhere. The size is the label's first number, so the tolerance figure is not a size."""
    flats = [Flat("z", 25.0, (0, 0, 0), (0, 0, 0)), Flat("x", 12.0, (100, 0, 0), (100, 0, 0))]
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
    flats = [Flat("z", 25.0, (0, 0, 0), (0, 0, 0)), Flat("x", 25.0, (100, 0, 0), (100, 0, 0))]
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


@pytest.mark.parametrize(
    ("tips", "expected"),
    [
        pytest.param([(-62.5, 0), (62.5, 0)], 0, id="one-callout-on-each-lobe"),
        pytest.param([(-62.5, 0)], 1, id="only-the-left-lobe-defined"),
        pytest.param([(-62.5, 0), (-37.5, 0)], 1, id="both-callouts-on-the-left-lobe"),
        pytest.param([], 2, id="neither-lobe-defined"),
    ],
)
def test_same_sized_flats_on_separate_stock_are_separate_definitions(tips, expected):
    """Two parallel lobes each machined to 25 A/F are TWO definitions; the two faces of one
    double-D are one. ``Flat`` cannot tell those apart — its ``axis`` is a letter — so the
    stock's axis line is re-derived from the cylinder inventory (#1013).

    Grouping the way ``render_flats`` groups would have been the easy choice, and wrong for
    the reason coverage exists: a check that mirrors the pipeline cannot see the pipeline get
    it wrong (ADR 0015). The renderer still collapses these into one callout, so on a real
    two-lobe build this check now reports the undefined lobe — a true finding about a drawing
    that really is incomplete.
    """
    codes = [i.code for i in lint_flat_coverage(_two_lobes(), _sheet_at(*tips), assembly=False)]
    assert codes == ["flat_not_dimensioned"] * expected


def test_a_callout_defines_the_flat_it_is_nearest_not_every_flat_it_is_near():
    """At a small scale two separate lobes project close enough that one leader falls inside
    both acceptance windows, and accepting it for each independently certified a lobe with no
    callout at all (Codex #1011 r10).

    1:100 here: lobes 100 mm apart on the part are 1 mm apart on the page, inside the 1 mm
    window from either. The identity projection every other test uses cannot reach this — the
    collision is a property of the SCALE, not of the geometry.
    """
    part = _two_lobes()
    codes = [
        i.code for i in lint_flat_coverage(part, _sheet_at((-37.5, 0), scale=0.01), assembly=False)
    ]
    assert codes == ["flat_not_dimensioned"], "the far lobe has no callout and must be reported"


def test_the_two_faces_of_one_lobe_remain_a_single_definition():
    """The other half of the same rule, and the thing axis-line grouping must not break: two
    faces at ±12.5 from ONE axis are one requirement, satisfied by a callout at either."""
    part = _two_lobes()
    for face in (-62.5, -37.5):
        codes = [i.code for i in lint_flat_coverage(part, _sheet_at((face, 0)), assembly=False)]
        assert codes == ["flat_not_dimensioned"], (
            f"a callout at {face} must satisfy its own lobe, leaving only the other reported"
        )


@pytest.mark.parametrize(
    "tolerance",
    [
        pytest.param(None, id="none"),
        pytest.param(0.2, id="symmetric"),
        pytest.param((0.1, 0.2), id="asymmetric-limits"),
        pytest.param(fit_deviation("H7", 25), id="resolved-fit"),
    ],
)
def test_the_parser_reads_every_label_the_renderer_can_write(flatted_shaft, tolerance):
    """`_AF_RE` and `_flat_label` are two halves of one contract with nothing holding them
    together, and they drifted: `_tol_suffix` writes an asymmetric limit as TWO
    space-separated tokens (`25 +0.20 -0.10 A/F`), which a one-token pattern could not read,
    so the check called our own correctly dimensioned drawing incomplete (Codex #1011 r8).

    The input is BUILT from `_tol_suffix` rather than restating what it emits, so a new
    suffix form fails here instead of in the field. That is the structural half of the fix;
    widening the pattern was only the local half.
    """
    label = _flat_label("25", _tol_suffix(tolerance, Draft()))
    assert lint_flat_coverage(flatted_shaft, _sheet(label), assembly=False) == [], (
        f"the renderer writes {label!r} and coverage could not read it"
    )


def test_an_asymmetric_tolerance_on_the_declared_path_lints_clean():
    """End to end through the public surface, since the drift was invisible until a real
    build produced the label (Codex #1011 r8)."""
    sheet = Sheet(_flatted_shaft())
    flat = sheet.flat(axis="z", across=25, at=(12.5, 0, 20))
    flat.tolerance(0.1, 0.2)
    sheet.auto_dimensions()
    dwg = sheet.build()
    drawn = [
        a.label for _, a in dwg.iter_annotations() if "A/F" in (getattr(a, "label", "") or "")
    ]
    # Expected label derived from the formatter, not spelled out: `_tol_suffix` rounds to the
    # draft's own decimal precision, so a literal here would assert this test's idea of the
    # format rather than the renderer's.
    assert drawn == [_flat_label("25", _tol_suffix((0.1, 0.2), dwg.draft))], (
        "precondition: the asymmetric label is on the sheet"
    )
    assert not [i for i in dwg.lint() if i.code.startswith("flat_")]


def test_a_correct_callout_does_not_excuse_a_contradictory_one(flatted_shaft):
    """Two leaders on one flat reading `25 A/F` and `12 A/F` are a contradiction someone has
    to resolve at the bench. A right answer beside a wrong one does not make the wrong one
    right, and nothing else on the sheet checks leader text — `label_vs_measured` reads
    Dimensions (Codex #1011 r11)."""
    sheet = _sheet("25 A/F", "12 A/F", tips=[_AT_THE_FLAT, _AT_THE_FLAT])
    issues = lint_flat_coverage(flatted_shaft, sheet, assembly=False)
    assert [i.code for i in issues] == ["flat_callout_mismatched"]
    assert "12 A/F" in issues[0].message


def test_every_contradictory_callout_is_named_not_just_the_first(flatted_shaft):
    """One issue per wrong callout: a reader fixing the sheet needs to find all of them."""
    sheet = _sheet("12 A/F", "40 A/F", tips=[_AT_THE_FLAT, _AT_THE_FLAT])
    issues = lint_flat_coverage(flatted_shaft, sheet, assembly=False)
    assert [i.code for i in issues] == ["flat_callout_mismatched"] * 2
    assert "12 A/F" in issues[0].message and "40 A/F" in issues[1].message


def test_a_callout_in_the_wrong_view_does_not_define_the_flat(flatted_shaft):
    """A Z flat reads in the plan. A front-view leader whose tip happens to land on the same
    page coordinates points at unrelated geometry, and page position alone cannot tell the two
    apart — so callouts are read from the flat's own view (Codex #1011 r12)."""
    right = _sheet("25 A/F", tips=[_AT_THE_FLAT], view="plan")
    assert lint_flat_coverage(flatted_shaft, right, assembly=False) == []
    wrong = _sheet("25 A/F", tips=[_AT_THE_FLAT], view="front")
    issues = lint_flat_coverage(flatted_shaft, wrong, assembly=False)
    assert [i.code for i in issues] == ["flat_not_dimensioned"]


def test_the_stock_axis_comes_from_the_recogniser_not_from_proximity(flatted_shaft):
    """`Flat.axis_at` is the OD the recogniser matched by edge adjacency, not the nearest
    parallel axis. Deriving it from proximity split a double-D whenever another cylinder sat
    closer to one face than its own axis did — a false positive on a correct drawing, which is
    the worst thing a completeness check can do (Codex #1011 r12)."""
    faces = recognise_flats(flatted_shaft)
    assert len({f.axis_at for f in faces}) == 1, "both faces of a double-D share one stock axis"
    # The faces sit 12.5 mm either side of the axis, so a cylinder nearer than that to one of
    # them would win a proximity contest while being the wrong stock.
    for face in faces:
        assert abs(face.at[0] - face.axis_at[0]) == 12.5


@pytest.mark.parametrize(
    "labels",
    [
        pytest.param(("18 A/F", "28 A/F"), id="smaller-first"),
        pytest.param(("28 A/F", "18 A/F"), id="larger-first"),
    ],
)
def test_coaxial_flats_projecting_to_one_point_take_their_own_callouts(labels):
    """Two sections of ONE shaft at different Z stations, flats in the same x plane but
    different stock radii. The plan view is end-on, so both project to the same page point and
    position alone cannot separate them — every leader went to the first group, reporting a
    mismatch and a gap on a sheet that had both callouts, correct (Codex #1011 r13).

    Order-parametrised: the fix must not depend on which leader is read first.
    """
    flats = [Flat("z", 18.0, (8, 0, 0), (0, 0, 0)), Flat("z", 28.0, (8, 0, 20), (0, 0, 0))]
    sheet = _sheet(*labels, tips=[(8, 0), (8, 0)])
    assert lint_flat_coverage(Box(1, 1, 1), sheet, flats=flats, assembly=False) == []


def test_value_ranks_second_so_it_cannot_override_position():
    """Value breaks a positional TIE; it must not outrank distance.

    Both groups sit inside the 1 mm window, 0.5 mm apart, with their callouts swapped — each
    leader is nearest one flat and states the OTHER's size. Distance-first calls both
    mismatched, which is the truth: the sheet dimensions each flat with its neighbour's size.
    Value-first would hand each leader to whichever flat its number happened to match and the
    sheet would lint clean while every callout pointed at the wrong feature.

    The 0.5 mm separation is a unit test of the ranking rule, not a plausible part; two flats
    further apart than the window never compete, so the rule would be untestable through
    realistic geometry.
    """
    flats = [Flat("z", 18.0, (0, 0, 0), (0, 0, 0)), Flat("z", 28.0, (0.5, 0, 0), (0.5, 0, 0))]
    sheet = _sheet("28 A/F", "18 A/F", tips=[(0, 0), (0.5, 0)])
    codes = [i.code for i in lint_flat_coverage(Box(1, 1, 1), sheet, flats=flats, assembly=False)]
    assert codes == ["flat_callout_mismatched", "flat_callout_mismatched"]


@pytest.mark.parametrize(
    ("tips", "expected"),
    [
        pytest.param([], 2, id="neither-region-called-out"),
        pytest.param([(-12.5, 0)], 1, id="one-callout-covers-one-region"),
        pytest.param([(-12.5, 0), (-12.5, 0)], 1, id="two-callouts-still-cover-only-one"),
    ],
)
def test_two_machined_regions_on_one_shaft_are_two_definitions(tips, expected):
    """Same axis line, same size, different stations. Grouping on the axis line alone merged
    them into ONE requirement, so a callout on the first certified the second — and since
    `render_flats` collapses them too, the check mirrored that rather than caught it
    (Codex #1011 r15). Two regions are now two requirements, and an uncovered one is reported.

    The plan view is end-on, so the two regions project onto each other: a leader there is
    positionally tied between them and nearest-wins gives it to one. Hence one callout leaves
    one region reported, and a second callout at the same point — indistinguishable from the
    first — does not help. That is the drawing's problem, not the check's: these two regions
    cannot be told apart in the plan view alone, which is the renderer half of #1013.

    The counts here were verified against the code, not predicted from that narrative; my
    first version asserted two issues in every case and was simply wrong about the tie.
    """
    part = _two_coaxial_regions()
    codes = [i.code for i in lint_flat_coverage(part, _sheet_at(*tips), assembly=False)]
    assert codes == ["flat_not_dimensioned"] * expected


def test_every_face_of_one_machined_region_shares_its_stock_identity():
    """The property the grouping key rests on: `axis_at` is the matched cylinder's placement,
    so all faces of one region carry the same one. Checked on a four-flat section, where a
    per-face identity would split one definition into four."""
    C = (Align.CENTER, Align.CENTER, Align.CENTER)
    bar = Cylinder(20, 40) & Box(30, 60, 60, align=C) & (Rot(0, 0, 90) * Box(30, 60, 60, align=C))
    faces = recognise_flats(bar)
    assert len(faces) == 4
    assert len({f.axis_at for f in faces}) == 1
    assert len(lint_flat_coverage(bar, _sheet(), assembly=False)) == 1


def test_a_view_with_no_coordinate_mapping_does_not_break_lint(flatted_shaft):
    """`drop_view_coordinates` leaves a view holding annotations that `at` can no longer
    place. Coverage projected unconditionally and `lint()` raised `KeyError` instead of
    returning issues — a lint that crashes is worse than one that is wrong (Codex #1011 r17).

    The flat is then reported as undimensioned, which is true: a view the drawing cannot map
    cannot carry its definition.
    """
    dwg = build_drawing(flatted_shaft)
    assert "flat_not_dimensioned" not in _codes(dwg), "precondition: covered before the drop"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        dwg.drop_view_coordinates("plan")
    assert "flat_not_dimensioned" in _codes(dwg)
