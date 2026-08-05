"""A flat's stock identity — two parallel lobes are not one double-D (#1013).

`Flat` recorded `axis` (the letter), `across` and `at`, and nothing that said which piece of
round stock a flat belonged to. From that record, two faces of one double-D and two flats on
two parallel lobes are the same shape of data, so `render_flats` grouped by
`(axis, across)` and collapsed both cases into a single callout — leaving the second lobe
undefined on the sheet with nothing reporting it.

ADR 0013's rule for a record that looks too thin is that the fix is the record.
"""

from build123d import Box, Cylinder, Pos

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
