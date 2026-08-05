"""A flat's stock identity — two parallel lobes are not one double-D (#1013).

`Flat` recorded `axis` (the letter), `across` and `at`, and nothing that said which piece of
round stock a flat belonged to. From that record, two faces of one double-D and two flats on
two parallel lobes are the same shape of data, so `render_flats` grouped by
`(axis, across)` and collapsed both cases into a single callout — leaving the second lobe
undefined on the sheet with nothing reporting it.

ADR 0013's rule for a record that looks too thin is that the fix is the record.
"""

from build123d import Box, Cylinder, Pos, Rot

from draftwright import build_drawing
from draftwright.model.declare import flat as declare_flat
from draftwright.recognition import recognise_flats


def _lobe():
    """One D-flat on round stock, axis Z, centred on the origin line."""
    return Cylinder(15, 40) - Pos(12.5, 0, 0) * Box(10, 40, 50)


def _double_d():
    """ONE piece of stock, two opposed faces — one A/F definition."""
    return (
        Cylinder(15, 40) - Pos(12.5, 0, 0) * Box(10, 40, 50) - Pos(-12.5, 0, 0) * Box(10, 40, 50)
    )


def _two_parallel_lobes(gap: float = 100.0):
    """TWO pieces of stock, same size flat on each — two independent A/F definitions."""
    return _lobe() + Pos(gap, 0, 0) * _lobe()


def test_the_record_distinguishes_one_stock_from_two():
    """The counterexample the record could not express before.

    Both fixtures produce two `Flat` records with equal `axis` and equal `across`. Only the
    axis line separates them, which is exactly why the letter alone was not enough.
    """
    dd = recognise_flats(_double_d())
    lobes = recognise_flats(_two_parallel_lobes())

    assert len(dd) == 2 and len(lobes) == 2, "both fixtures should yield two flat records"
    assert {f.axis for f in dd} == {"z"} and {f.axis for f in lobes} == {"z"}

    assert len({f.axis_line for f in dd}) == 1, (
        "a double-D's two faces are one piece of stock — they must share an axis line, or the "
        "renderer will draw two A/F callouts for one definition"
    )
    assert len({f.axis_line for f in lobes}) == 2, (
        "two parallel lobes are two pieces of stock — separate axis lines, or the renderer "
        "collapses them and the second lobe goes undefined"
    )


def test_a_double_d_still_gets_exactly_one_callout():
    """The collapse is correct for the case it was written for, and must survive the fix."""
    dwg = build_drawing(_double_d())

    callouts = sorted(n for n in dwg.annotations() if n.startswith("m_flat_"))
    assert len(callouts) == 1, f"a double-D is ONE A/F definition, got {callouts}"
    assert not [i for i in dwg.lint() if i.code == "flat_dropped"]


def test_two_parallel_lobes_are_two_definitions_and_a_lost_one_is_reported():
    """The defect: the sheet used to carry one callout for two independent definitions, with
    nothing saying so.

    The engine now plans both. Placing them both is a corridor-capacity problem and is NOT
    what this fix is about (#1034) — what matters here is that the second definition exists
    and its loss is REPORTED rather than absorbed by a grouping key. A completeness failure
    that is visible is the whole point; the previous behaviour was a clean-looking drawing
    that was wrong.
    """
    dwg = build_drawing(_two_parallel_lobes())

    callouts = sorted(n for n in dwg.annotations() if n.startswith("m_flat_"))
    dropped = [i for i in dwg.lint() if i.code == "flat_dropped"]

    assert len(callouts) + len(dropped) == 2, (
        f"two lobes are two A/F definitions; the drawing accounts for "
        f"{len(callouts)} placed + {len(dropped)} reported. Before #1013 this was one "
        "callout and no report — the second lobe simply vanished."
    )
    assert dropped, (
        "the unplaced definition must be reported. Silently drawing one callout for two "
        "lobes is the exact defect: a sheet that looks complete and is not"
    )


def _coaxial_separated_stocks():
    """TWO pieces of stock stacked on ONE axis with a gap — same axis line, different spans."""
    lobe = Cylinder(15, 30) - Pos(12.5, 0, 0) * Box(10, 40, 50)
    return lobe + Pos(0, 0, 80) * lobe


def test_coaxial_separated_stock_is_two_definitions_not_one():
    """The axis line alone is NOT stock identity, and this code already knew it.

    #1015 taught the opposition test that "the same infinite axis is not the same piece of
    stock"; grouping needed the same lesson. Two D-shafts stacked coaxially with a gap share
    an axis line, so the first cut of #1013 merged them into one callout with no report —
    the very defect it set out to fix, one arrangement over (Codex #1035 r1).

    The axial span separates them. Neither half suffices alone: parallel lobes share a span
    but not a line; coaxial stock shares a line but not a span.
    """
    flats = recognise_flats(_coaxial_separated_stocks())
    assert len(flats) == 2, "fixture should yield one flat per stock"
    assert len({f.axis_line for f in flats}) == 1, (
        "fixture no longer exercises the case — these must SHARE an axis line, or the "
        "parallel-lobe fix would separate them and this proves nothing"
    )
    assert len({f.stock_span for f in flats}) == 2, (
        "coaxial stock must be told apart by its axial span"
    )

    dwg = build_drawing(_coaxial_separated_stocks())
    callouts = [n for n in dwg.annotations() if n.startswith("m_flat_")]
    dropped = [i for i in dwg.lint() if i.code == "flat_dropped"]
    assert len(callouts) + len(dropped) == 2, (
        f"two coaxial stocks are two A/F definitions; got {len(callouts)} placed + "
        f"{len(dropped)} reported. One callout and no report is the silent defect."
    )


def test_the_emitted_script_carries_the_stock_identity(tmp_path):
    """A detected two-lobe part must not round-trip into a collapsed one-lobe declaration.

    `sheet.flat(...)` omitted the identity, and the declared defaults reproduce pre-#1013
    grouping — so an emitted script regenerated exactly the drawing the detection had just
    fixed. Silence is not neutral here; it is the old bug (Codex #1035 r1).

    CLAUDE.md requires a feature to round-trip recognise + emit + declare, and this is the
    emit leg.
    """
    from build123d import export_step

    from draftwright.sheet_emit import generate_sheet_script

    step = str(tmp_path / "lobes.step")
    export_step(_two_parallel_lobes(), step)
    generate_sheet_script(step, out=str(tmp_path / "lobes"))
    script = (tmp_path / "lobes.py").read_text()

    flat_lines = [ln for ln in script.splitlines() if "sheet.flat(" in ln]
    assert len(flat_lines) == 2, f"expected one declaration per lobe, got {flat_lines}"
    for ln in flat_lines:
        assert "axis_line=" in ln and "stock_span=" in ln, (
            f"emitted flat drops its stock identity, so the script regenerates the collapse: {ln}"
        )
    # And the two lobes are declared as DIFFERENT stock, not merely annotated.
    assert "axis_line=(0, 0)" in script and "axis_line=(100, 0)" in script, (
        "both lobes emitted the same axis line — the script would collapse them again"
    )


def test_a_declared_flat_defaults_to_one_stock():
    """`axis_line` cannot be derived from `at`: a double-D's two faces have different face
    centres but one axis line, so deriving it would split every declared double-D in two.

    The default is the origin line — all declared flats on an axis group together, which is
    the pre-#1013 behaviour, so a single-stock declaration is unaffected. A declarer with
    parallel lobes states them, because nothing in a flat's own geometry says it.
    """
    a = declare_flat(axis="z", across=25, at=(-12.5, 0, 0))
    b = declare_flat(axis="z", across=25, at=(12.5, 0, 0))
    assert a.axis_line == b.axis_line, "two declared faces of one double-D must not split"

    far = declare_flat(axis="z", across=25, at=(100, 0, 0), axis_line=(100.0, 0.0))
    assert far.axis_line != a.axis_line, "an explicit axis line must separate declared lobes"


def test_slanted_stock_is_an_acknowledged_limitation_not_a_silent_wrong_answer():
    """`axis_line` identifies AXIS-ALIGNED stock. For a slanted axis it is neither canonical
    nor sufficient — see `_axis_line`'s docstring and #1036.

    This pins the reason that gap is tolerable rather than urgent: a slanted flat produces no
    callout at all, so the identity never gets to be wrong in a drawing. The test exists so
    that if slanted rendering is ever fixed, this fails and the identity gap surfaces with
    it — rather than the two being fixed months apart with a wrong callout in between.
    """
    slant = Rot(0, 45, 0) * (Cylinder(10, 40) - Pos(8, 0, 0) * Box(10, 40, 60))

    flats = recognise_flats(slant)
    assert flats, "fixture stopped producing a slanted flat — it no longer pins anything"
    assert flats[0].axis == "x", (
        "a (0.707, 0, 0.707) axis is classified by its dominant component; if that changed, "
        "the slanted-identity reasoning in _axis_line needs rechecking"
    )

    dwg = build_drawing(slant)
    assert not [n for n in dwg.annotations() if n.startswith("m_flat_")], (
        "a slanted flat now RENDERS. The identity key it is grouped by is not canonical for "
        "slanted axes (#1036) — fix that before shipping slanted A/F callouts, or two "
        "patches of one shaft may draw two callouts and two shafts may draw one."
    )
    assert [i for i in dwg.lint() if i.code == "flat_dropped"], (
        "the slanted flat is neither drawn nor reported — that would be a silent omission"
    )
