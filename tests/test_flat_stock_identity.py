"""A flat's stock identity — two parallel lobes are not one double-D (#1013).

`Flat` recorded `axis` (the letter), `across` and `at`, and nothing that said which piece of
round stock a flat belonged to. From that record, two faces of one double-D and two flats on
two parallel lobes are the same shape of data, so `render_flats` grouped by
`(axis, across)` and collapsed both cases into a single callout — leaving the second lobe
undefined on the sheet with nothing reporting it.

ADR 0013's rule for a record that looks too thin is that the fix is the record.
"""

import pytest
from b123d_recognisers import recognise_flats
from build123d import Box, Cylinder, Pos, Rot

from draftwright import build_drawing
from draftwright.model.declare import flat as declare_flat


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
    assert all(f.axis_aligned for f in [*dd, *lobes])

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


def test_two_parallel_lobes_place_two_independent_definitions():
    """Each stock needs its own placed A/F definition (#1034).

    The left lobe's flat is not on the aggregate plan-view boundary.  A single centre-out
    candidate therefore leaves its label intersecting the aggregate view box even though
    there is clear margin above it.  Removing the margin-escape candidates must make this
    test fail with one placed callout plus one ``flat_dropped`` issue.
    """
    dwg = build_drawing(_two_parallel_lobes())

    callouts = sorted(n for n in dwg.annotations() if n.startswith("m_flat_"))
    dropped = [i for i in dwg.lint() if i.code == "flat_dropped"]

    assert len(callouts) == 2, f"two lobes need two placed A/F definitions, got {callouts}"
    assert not dropped, f"available plan-view margin must prevent a placement drop: {dropped}"
    assert not [
        i for i in dwg.lint() if i.code in {"annotation_overlap", "leader_crosses_silhouette"}
    ]


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


def test_slanted_stock_has_canonical_identity_and_a_placed_definition():
    """A slanted flat's A/F callout is safe only after its stock key carries direction.

    The OCP axis point for this fixture is ``(-14.142, 0, -14.142)``. Dropping X from that
    point produced ``(0, -14.142)``, a key that changed with the chosen point along the line.
    The perpendicular foot is the origin, so the canonical in-plane coordinates are
    ``(0, 0)`` and direction distinguishes other lines through that foot (#1036).
    """
    slant = Rot(0, 45, 0) * (Cylinder(10, 40) - Pos(8, 0, 0) * Box(10, 40, 60))

    flats = recognise_flats(slant)
    assert flats, "fixture stopped producing a slanted flat — it no longer pins anything"
    assert flats[0].axis == "z", (
        "an exact (0.707, 0, 0.707) dominant-axis tie is normalised Z-first by the external "
        "package; if that changed, the slanted-identity reasoning in _axis_line needs rechecking"
    )
    assert flats[0].axis_direction == pytest.approx((2**-0.5, 0.0, 2**-0.5), abs=1e-6)
    assert flats[0].axis_line == (0.0, 0.0), (
        "the line key used OCP's arbitrary point rather than the perpendicular foot"
    )
    assert not flats[0].axis_aligned

    dwg = build_drawing(slant)
    model_flat = next(f for f in dwg.model().features if f.kind == "flat")
    assert model_flat.axis == "z", "the external recogniser's semantic axis must be preserved"
    assert model_flat.presentation_axis == "x", (
        "Draftwright keeps the established lint-clean side view for an exact X/Z slant"
    )
    callouts = [n for n in dwg.annotations() if n.startswith("m_flat_")]
    assert callouts == ["m_flat_x0"]
    assert len(dwg.measurement_keys(callouts[0])) == 1
    assert not [
        issue
        for issue in dwg.lint()
        if issue.code in {"flat_dropped", "annotation_overlap", "leader_crosses_silhouette"}
        or issue.code.startswith("flat_requirement_")
    ]

    # Translation perpendicular to the slanted direction changes the canonical line position,
    # so the two physical requirements remain two rendered definitions.
    two_slants = slant + Pos(0, 60, 0) * slant
    multi = build_drawing(two_slants)
    assert len([n for n in multi.annotations() if n.startswith("m_flat_")]) == 2
    assert not [i for i in multi.lint() if i.code == "flat_dropped"]


def test_flat_presentation_override_is_limited_to_the_x_z_tie():
    yz_tie = declare_flat(
        axis="z",
        across=10,
        at=(0, 0, 0),
        axis_direction=(0.0, 2**-0.5, 2**-0.5),
    )

    assert yz_tie.presentation_axis == "z", "the compatibility policy is not a general tie rule"


def test_a_slanted_double_d_is_still_one_physical_definition():
    slanted_double_d = Rot(0, 45, 0) * _double_d()
    flats = recognise_flats(slanted_double_d)
    assert len(flats) == 2 and len({f.axis_direction for f in flats}) == 1
    assert len({(f.axis_line, f.stock_span) for f in flats}) == 1

    dwg = build_drawing(slanted_double_d)
    callouts = [n for n in dwg.annotations() if n.startswith("m_flat_")]
    assert len(callouts) == 1 and len(dwg.measurement_keys(callouts[0])) == 2
    assert not [i for i in dwg.lint() if i.code == "flat_dropped"]


# `test_slanted_line_geometry_is_directional_and_point_invariant` lived here and drove
# `flats._axis_line` / `_same_axis_line` directly. Those privates moved in 0.2.2 and then
# changed signature in 0.2.4 (`_same_axis_line` gained `radius`) — three breaks in three
# releases, none of them a behaviour change on our side. The property it asserted (a
# perpendicular foot alone is not a line; direction must match) is recogniser-internal
# mechanism, and its OBSERVABLE consequence is already pinned above by
# `test_a_slanted_double_d_is_still_one_physical_definition` through the public
# `recognise_flats` + `build_drawing` surface. ADR 0013's contract is that public surface,
# so the mechanism test belongs in the recogniser's own suite rather than being chased
# across package internals from here.
