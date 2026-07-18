"""Sheet corner-block tables — `sheet.table()` / `sheet.notes()` (ADR 0011 #488).

The declarative surface over the engine's generic auto-placed `Drawing.add_table` (the same
machinery as the hole table): notes blocks / revision blocks / BOMs / schedules, positioned
clear of the views + title block at build, and lint-checked.
"""

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import Sheet


def _sheet():
    s = Sheet(Box(120, 80, 20) - Pos(0, 0, 0) * Cylinder(4, 20), title="Plate", number="DWG-T")
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
    # #700: compose's table_fits fitness check (ADR 0004) sizes tables via
    # _est_table_size; the annotation pass renders them via _build_table. Both now
    # draw from the one _core._table_metrics — this pins estimator == rendered, the
    # exact drift ADR 0004 names as the failure mode to guard against.
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
