"""Solve-trace / explain mode (#736): the opt-in per-build JSON strip-placement trace.

Child 1 of #735 (the #733 post-mortem): diagnosing a "strip full" drop must be a
glance — one JSON file per build plus a lint message that names the occupants —
never a custom script and two slow CTC rebuilds. Tracing is default-OFF and must
never change a placement decision (zero output risk; the golden corpus pins that).
"""

import json
from types import SimpleNamespace

from build123d import Box

from draftwright import build_drawing
from draftwright._core import Strip
from draftwright.annotations._common import full_strip_message, strip_occupants


def _build_box(tmp_path, **kw):
    return build_drawing(Box(60, 40, 20), out=str(tmp_path / "t"), **kw)


class TestTraceRecording:
    def test_trace_kwarg_writes_one_json_per_build(self, tmp_path):
        dwg = _build_box(tmp_path, trace=True)
        path = tmp_path / "t.trace.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert data["solves"], "at least one strip solve must be recorded"
        # seq is the drain's batch order — the per-strip solve sequence.
        assert [s["seq"] for s in data["solves"]] == list(range(len(data["solves"])))
        by_corridor = {tuple(s["corridor"]): s for s in data["solves"] if s["corridor"]}
        # A plain box registers its height ladder in the shared (front, right) corridor.
        s = by_corridor[("front", "right")]
        assert [c["name"] for c in s["candidates"]] == ["dim_height"]
        cand = s["candidates"][0]
        assert cand["force"] is True and cand["priority"] == 0
        # The strip bounds are the diagnosis frame …
        assert set(s["strip"]) == {"anchor", "outer_limit", "direction", "gap", "spacing"}
        # … and each pass records the carve + per-candidate events.
        p = s["passes"][0]
        assert set(p) >= {"obstacles", "free_segments", "placed", "rejected", "unplaced", "span"}
        assert p["free_segments"], "the carved free segments must be recorded"
        # "Why did X place/drop" is one query over outcomes (the jq contract).
        outcome = next(o for o in s["outcomes"] if o["name"] == "dim_height")
        assert outcome["outcome"] == "placed"
        assert outcome["pos"] == p["placed"][0]["pos"]
        # The recorder rides the drawing's build state, so finalize() traces too.
        assert dwg.solve_trace is not None

    def test_env_var_directory_activates(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DRAFTWRIGHT_TRACE", str(tmp_path))
        _build_box(tmp_path)
        data = json.loads((tmp_path / "t.trace.json").read_text(encoding="utf-8"))
        assert data["version"] == 1 and data["solves"]

    def test_off_by_default_no_file_no_recorder(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DRAFTWRIGHT_TRACE", raising=False)
        dwg = _build_box(tmp_path)
        assert dwg.solve_trace is None, "tracing must be off by default (nil cost)"
        assert not list(tmp_path.glob("**/*.trace.json"))


class _Anno:
    """A duck-typed placed annotation: a hull bbox only (no ``.segments``)."""

    def __init__(self, x0, y0, x1, y1):
        self._box = (x0, y0, x1, y1)

    def bounding_box(self):
        x0, y0, x1, y1 = self._box
        return SimpleNamespace(min=SimpleNamespace(X=x0, Y=y0), max=SimpleNamespace(X=x1, Y=y1))


class _Dwg:
    """A duck-typed drawing exposing just the occupancy surface strip_occupants reads."""

    draft = None

    def __init__(self, annos):
        self._annos = annos

    def iter_annotations(self):
        return list(self._annos.items())

    def view_of(self, name):
        return "front"


class TestFullStripMessage:
    """The #736 enriched placement_unsatisfiable message: name what filled the strip."""

    def test_names_top_occupants_largest_first(self):
        strip = Strip(anchor=90.0, outer_limit=140.0, direction=1.0)  # free span x=[100, 140]
        dwg = _Dwg(
            {
                "ldr_big": _Anno(105, 0, 130, 10),  # covers 25 mm of the span
                "dim_small": _Anno(110, 20, 112, 24),  # covers 2 mm
                "dim_elsewhere": _Anno(0, 0, 50, 5),  # outside the span → not an occupant
            }
        )
        assert strip_occupants(dwg, strip, "front", "x") == ["ldr_big", "dim_small"]
        msg = full_strip_message(
            "overall height dimension dropped (front-view right strip full)",
            dwg,
            strip,
            "front",
            "x",
        )
        assert msg == (
            "overall height dimension dropped "
            "(front-view right strip full; occupied by: ldr_big, dim_small)"
        )

    def test_unknown_occupancy_leaves_message_untouched(self):
        strip = Strip(anchor=90.0, outer_limit=140.0, direction=1.0)
        base = "overall height dimension dropped (front-view right strip full)"
        assert full_strip_message(base, _Dwg({}), strip, "front", "x") == base
        assert full_strip_message(base, _Dwg({}), None, "front", "x") == base

    def test_limit_caps_the_name_list(self):
        strip = Strip(anchor=90.0, outer_limit=140.0, direction=1.0)
        annos = {f"a{i}": _Anno(100 + i, 0, 139, 5) for i in range(5)}
        assert len(strip_occupants(_Dwg(annos), strip, "front", "x")) == 3
