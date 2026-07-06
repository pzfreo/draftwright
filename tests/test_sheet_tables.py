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
    assert "notes0" in dwg._named
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
    assert "table0" in dwg._named
    # cells are stringified (a non-str is accepted)
    s2 = _sheet()
    s2.table([("QTY",), (3,)])
    assert s2._tables[0]["rows"][1] == ("3",)


def test_multiple_tables_get_unique_names():
    s = _sheet()
    s.notes(["x"])
    s.table([("a",), ("b",)])
    dwg = s.build()
    assert {"notes0", "table1"} <= set(dwg._named)


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


def test_model_inspection_ignores_tables():
    # model() is the cheap no-render inspection path — tables are render-time, so declaring one
    # must not affect the IR model or raise.
    s = _sheet()
    s.notes(["x"])
    m = s.model()
    assert not any(getattr(f, "kind", None) in ("table", "notes") for f in m.features)
