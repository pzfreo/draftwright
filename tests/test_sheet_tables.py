"""Sheet corner-block tables — `sheet.table()` / `sheet.notes()` (ADR 4 (was 0011) #488).

The declarative surface over the engine's generic auto-placed `Drawing.add_table` (the same
machinery as the hole table): notes blocks / revision blocks / BOMs / schedules, positioned
clear of the views + title block at build, and lint-checked.
"""

import pytest
from build123d import Box, Cylinder, Location, Pos, Rectangle

from draftwright import Sheet, build_drawing


def _sheet():
    s = Sheet(
        Box(120, 80, 20) - Pos(0, 0, 0) * Cylinder(4, 20), title="Plate", number="DWG-T"
    ).auto_dimensions()
    s.envelope()
    s.hole(Pos(0, 0, 0) * Cylinder(4, 20))
    return s


def test_notes_block_places_lint_clean():
    s = _sheet()
    s.notes(["BREAK ALL EDGES 0.3", "DEBURR", "M3x0.5 TAP"])
    dwg = s.build()
    assert "notes0" in dwg.annotations()
    assert not [
        x for x in dwg.lint() if x.code in ("annotation_overlap", "annotation_out_of_bounds")
    ]


def test_notes_diameter_sign_uses_the_supported_drafting_glyph():
    """#1275: U+2300 must not turn into the font's missing-glyph square.

    Compare the public notes result with the established U+00F8 drafting spelling.
    Equal table geometry proves the source glyph reached the same supported outline,
    while the retained rows prove this render-time compatibility rule did not rewrite
    the user's declaration.
    """
    source = (
        Sheet(Box(20, 12, 4), page="A4")
        .authored_dimensions()
        .notes(["⌀4 x 3.2"], title=None, number=False)
        .build()
    )
    control = (
        Sheet(Box(20, 12, 4), page="A4")
        .authored_dimensions()
        .notes(["ø4 x 3.2"], title=None, number=False)
        .build()
    )

    source_table = source.get_annotation("notes0")
    control_table = control.get_annotation("notes0")
    assert source_table.table_rows == (("⌀4 x 3.2",),)
    assert source_table.table_size == pytest.approx(control_table.table_size)
    assert source_table.bounding_box().size == pytest.approx(control_table.bounding_box().size)


def test_four_row_notes_block_uses_clear_lower_left_a4_space_inside_frame():
    # #1145 real failure seam: the sheet-frame Compound spans the page, but it is
    # a boundary whose inset is already represented by analysis.margin—not a
    # solid obstacle.  Three notes + title are the reported four-row inventory.
    s = Sheet(
        Box(80, 50, 12) - Pos(0, 0, 0) * Cylinder(4, 12),
        title="Plate",
        number="DWG-A4-NOTES",
        page="A4",
        frame=True,
        zones=True,
    ).authored_dimensions()
    s.envelope()
    s.hole(Pos(0, 0, 0) * Cylinder(4, 12))
    s.notes(["BREAK ALL EDGES 0.3", "DEBURR", "M3x0.5 TAP"], prefer="bl")

    drawing = s.build()
    table = drawing.get_annotation("notes0")

    assert table is not None
    assert table.table_size == pytest.approx((43.1950001, 28.0))
    box = table.bounding_box()
    # The frame-enabled public A4 content inset is 16 mm; pin the rendered
    # result without reaching through Drawing's private analysis state.
    assert (box.min.X, box.min.Y) == pytest.approx((16.0, 16.0), abs=1e-6)
    assert not [issue for issue in drawing.lint() if issue.code == "table_dropped"]


def test_default_preference_falls_back_to_the_only_clear_lower_left_region():
    # A real public, zones-enabled A4 inventory whose free notes occupy every
    # candidate above the bottom band and the bottom-right remainder.  ``prefer``
    # is deliberately omitted: TR is rejected and the exact solver must reach
    # the surviving lower-left region rather than restricting itself to corners.
    drawing = build_drawing(Box(30, 20, 8), page="A4", auto_dims=False, frame=True, zones=True)
    for index in range(19):
        drawing.note("M" * 180, (148.5, 47.1 + 8 * index), name=f"top_band_{index}")
    drawing.note("M" * 121, (170, 30), name="bottom_right_band")

    table = drawing.add_table(
        [("NOTES",), ("1  BREAK ALL EDGES 0.3",), ("2  DEBURR",), ("3  M3x0.5 TAP",)],
        name="notes",
    )

    assert table is not None
    box = table.bounding_box()
    assert box.min.X < 20.0 and box.min.Y < 20.0
    assert box.max.X < 62.0 and box.max.Y < 48.0


def test_table_keeps_external_clearance_from_existing_annotation():
    drawing = build_drawing(Box(30, 20, 8), page="A4", auto_dims=False, frame=True)
    drawing.note("BLOCKER TEXT", (30, 22), name="blocker")

    table = drawing.add_table([("HEAD",), ("row",)], prefer="bl", name="clearance_table")

    assert table is not None
    table_box = table.bounding_box()
    blocker_box = drawing.get_annotation("blocker").bounding_box()
    # X spans overlap, so the separating Y gap is the load-bearing external
    # clearance (the old strict-overlap solve placed them exactly flush).
    assert table_box.min.X < blocker_box.max.X and blocker_box.min.X < table_box.max.X
    assert table_box.min.Y - blocker_box.max.Y >= drawing.draft.pad_around_text - 1e-6


def test_table_still_avoids_anonymous_drawing_add_compatibility_object():
    drawing = build_drawing(Box(40, 30, 10), page="A4", auto_dims=False, frame=True)
    blocker = Rectangle(60, 40).locate(Location((46, 36, 0)))
    with pytest.warns(DeprecationWarning, match="Drawing.add"):
        drawing.add(blocker)

    table = drawing.add_table([("H",), ("x",)], prefer="bl", name="compat_table")

    assert table is not None
    tb, bb = table.bounding_box(), blocker.bounding_box()
    assert not (
        tb.min.X < bb.max.X and bb.min.X < tb.max.X and tb.min.Y < bb.max.Y and bb.min.Y < tb.max.Y
    )


def test_failed_table_diagnostic_names_size_candidate_regions_and_blockers():
    # Free-form notes are the sanctioned user-positioned text surface.  These
    # intentionally fill the A4 usable region in horizontal bands so the real
    # generic table path has candidates, rejects them, and attributes the exact
    # blocking annotations in its public lint diagnostic.
    drawing = build_drawing(Box(40, 30, 10), page="A4", auto_dims=False, frame=True)
    for index, y in enumerate(range(20, 191, 8)):
        drawing.note("M" * 180, (148.5, y), name=f"block_{index}")

    assert drawing.add_table([("H",), ("x",)], name="blocked") is None
    issue = next(
        issue
        for issue in drawing.registry.issues
        if issue.code == "table_dropped" and "'blocked'" in issue.message
    )
    assert "measured page-space footprint width=7.5 mm, height=14.0 mm" in issue.message
    assert "attempted " in issue.message and "candidate regions; rejected:" in issue.message
    assert "blocked within 2.0 mm clearance by annotation:block_" in issue.message


def test_oversized_table_diagnostic_reports_both_measured_dimensions_and_violation():
    s = _sheet()
    s.table([("H",)] + [(str(i),) for i in range(300)], name="oversized")
    drawing = s.build()
    issue = next(
        issue
        for issue in drawing.registry.issues
        if issue.code == "table_dropped" and "'oversized'" in issue.message
    )
    assert "measured page-space footprint" in issue.message
    assert "height exceeds usable region by" in issue.message


def test_notes_autonumbers_under_a_title():
    s = _sheet()
    s.notes(["A", "B"], title="NOTES")
    rows = s._tables[0]["rows"]
    assert rows[0] == ("NOTES",)  # title header
    assert rows[1] == ("1  A",) and rows[2] == ("2  B",)  # auto-numbered single column


def test_notes_can_omit_number_and_title():
    s = _sheet()
    s.notes(["JUST TEXT"], title=None, number=False)
    assert s._tables[0]["rows"] == [("JUST TEXT",)]


def test_generic_table_places_and_stringifies():
    s = _sheet()
    s.table([("REV", "DATE", "BY"), ("A", "2026-07-06", "PF")], prefer="br")
    dwg = s.build()
    assert "table0" in dwg.annotations()
    # cells are stringified (a non-str is accepted)
    s2 = _sheet()
    s2.table([("QTY",), (3,)])
    assert s2._tables[0]["rows"][1] == ("3",)


def test_multiple_tables_get_unique_names():
    s = _sheet()
    s.notes(["x"])
    s.table([("a",), ("b",)])
    dwg = s.build()
    assert {"notes0", "table1"} <= set(dwg.annotations())


def test_chains_and_returns_sheet():
    s = _sheet()
    assert s.notes(["x"]) is s and s.table([("a",), ("b",)]) is s


def test_validation():
    s = _sheet()
    with pytest.raises(ValueError, match="at least one row"):
        s.table([])
    with pytest.raises(ValueError, match="same .* number of columns"):
        s.table([("a", "b"), ("c",)])
    with pytest.raises(ValueError, match="at least one line"):
        s.notes([])


def test_flat_string_list_is_rejected_with_a_notes_hint():
    # #493 review: a str row iterates char-by-char into columns — a silent-garbage trap. Since
    # notes() takes a flat list of strings, table(["REV","DATE","BY"]) is an easy mistake; reject it.
    s = _sheet()
    with pytest.raises(ValueError, match="notes"):
        s.table(["REV", "DATE", "BY"])


def test_table_never_overwrites_a_feature_annotation():
    # #493 review: an explicit name colliding with an existing annotation used to silently delete
    # it via dwg.add. It must be uniquified (both survive) and warned, never overwrite.
    s = _sheet()
    s.table([("R",), ("A",)], name="m_env_width")  # a real feature-annotation name
    with pytest.warns(UserWarning, match="already taken"):
        dwg = s.build()
    assert "m_env_width" in dwg.annotations()  # the original width dimension survives
    assert any(
        n.startswith("m_env_width_") for n in dwg.annotations()
    )  # table placed under a fresh name


def test_auto_name_does_not_collide_with_an_explicit_one():
    # #493 review: the auto name f"table{len}" could equal a user's explicit "table1"; both must survive.
    s = _sheet()
    s.table([("x",), ("y",)], name="table1")
    s.table([("p",), ("q",)])  # auto-names table1 -> collision
    dwg = s.build()
    assert "table1" in dwg.annotations() and any(
        n.startswith("table1_") for n in dwg.annotations()
    )


def test_a_dropped_table_frees_its_name():
    # #493 review r2: a table that doesn't fit is dropped (table_dropped lint) — its name must be
    # freed, so a later same-named table that DOES fit gets the clean name, not a misleading rename.
    s = _sheet()
    s.table([("H",)] + [(str(i),) for i in range(300)], name="rev")  # too tall → dropped
    s.table([("A",), ("B",)], name="rev")  # small, same name → should get the freed "rev"
    dwg = s.build()
    assert "rev" in dwg.annotations()  # the fitting table took the freed name
    assert any(
        i.code == "table_dropped" for i in dwg.registry.issues
    )  # the drop is still recorded


def test_estimated_table_size_matches_rendered():
    # #700: compose's table_fits fitness check (ADR 2 (was 0004)) sizes tables via
    # _est_table_size; the annotation pass renders them via _build_table. Both now
    # draw from the one _core._table_metrics — this pins estimator == rendered, the
    # exact drift ADR 2 (was 0004) names as the failure mode to guard against.
    from draftwright._core import _FONT_SIZE, _build_table, _wrap_rows, draft_preset
    from draftwright.compose import _est_table_size

    draft = draft_preset(font_size=_FONT_SIZE, decimal_precision=1)
    header = ("TAG", "ø", "X", "Y")
    data = [(f"A{i}", f"ø{3 + i}.5", str(10 * i), str(5 * i)) for i in range(5)]
    for ncols in (1, 2, 3):
        rows = _wrap_rows(header, data, ncols)
        table = _build_table(rows, draft, block_cols=len(header))
        est = _est_table_size(rows, draft.font_size, draft.pad_around_text, len(header))
        assert est == pytest.approx(table.table_size), (
            f"ncols={ncols}: estimated footprint {est} != rendered {table.table_size}"
        )


def test_model_inspection_ignores_tables():
    # model() is the cheap no-render inspection path — tables are render-time, so declaring one
    # must not affect the IR model or raise.
    s = _sheet()
    s.notes(["x"])
    m = s.model()
    assert not any(getattr(f, "kind", None) in ("table", "notes") for f in m.features)
