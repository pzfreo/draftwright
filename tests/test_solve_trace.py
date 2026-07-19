"""Solve-trace / explain mode (#736): the opt-in per-build JSON strip-placement trace.

Child 1 of #735 (the #733 post-mortem): diagnosing a "strip full" drop must be a
glance — one JSON file per build plus a lint message that names the occupants —
never a custom script and two slow CTC rebuilds. Tracing is default-OFF and must
never change a placement decision (zero output risk; the golden corpus pins that).
"""

import json
import logging
from types import SimpleNamespace

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import build_drawing
from draftwright._core import Strip
from draftwright.annotations._common import full_strip_message, strip_occupants


def _build_box(tmp_path, **kw):
    return build_drawing(Box(60, 40, 20), out=str(tmp_path / "t"), **kw)


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


class TestTraceRecording:
    def test_trace_kwarg_writes_one_json_per_build(self, tmp_path):
        dwg = _build_box(tmp_path, trace=True)
        path = tmp_path / "t.trace.json"
        assert path.exists()
        data = _load(path)
        assert data["version"] == 2
        assert data["solves"], "at least one strip solve must be recorded"
        # Two distinct record types (schema honesty): corridor solves carry candidates/
        # outcomes; everything else is a pass_event with per-item outcomes.
        assert isinstance(data["pass_events"], list)
        assert all(s["corridor"] for s in data["solves"])
        assert all("items" in e and "label" in e for e in data["pass_events"])
        # seq is ONE global event counter across solves + pass_events (decision order).
        seqs = sorted([s["seq"] for s in data["solves"]] + [e["seq"] for e in data["pass_events"]])
        assert seqs == list(range(len(seqs)))
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
        data = _load(tmp_path / "t.trace.json")
        assert data["version"] == 2 and data["solves"]

    def test_off_by_default_no_file_no_recorder(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DRAFTWRIGHT_TRACE", raising=False)
        dwg = _build_box(tmp_path)
        assert dwg.solve_trace is None, "tracing must be off by default (nil cost)"
        assert not list(tmp_path.glob("**/*.trace.json"))

    def test_trace_is_byte_for_byte_deterministic(self, tmp_path):
        # Strict serialisation (no default=str): same part → byte-identical trace.
        for stem in ("a", "b"):
            build_drawing(Box(60, 40, 20), out=str(tmp_path / stem), trace=True)
        assert (tmp_path / "a.trace.json").read_bytes() == (tmp_path / "b.trace.json").read_bytes()

    def test_unwritable_trace_path_never_aborts_the_build(self, tmp_path, caplog):
        # Recording-only: an unwritable path degrades to a warning — the build (and
        # any export after it) must complete untouched.
        target = tmp_path / "no_such_dir" / "t.trace.json"
        with caplog.at_level(logging.WARNING, logger="draftwright.annotations._common"):
            dwg = _build_box(tmp_path, trace=str(target))
        assert dwg is not None and dwg.solve_trace is not None
        assert not target.exists()
        assert any("trace: could not write" in r.getMessage() for r in caplog.records)


class TestPassEvents:
    """Finding-#733 coverage: the immediate placers — post-drain machined-feature leader
    callouts and the turned diameter/step-length set-solves — must be trace-visible too
    (pre-#734 the callouts WERE the drain-time occupants; their own story may not vanish)."""

    def test_machined_feature_callout_pass_is_traced(self, tmp_path):
        from build123d import Axis, chamfer

        plate = Box(90, 60, 20)
        e = plate.edges().filter_by(Axis.Z).sort_by(lambda e: e.center().X + e.center().Y)[-1]
        build_drawing(chamfer(e, 12), out=str(tmp_path / "c"), trace=True)
        data = _load(tmp_path / "c.trace.json")
        ev = next(e for e in data["pass_events"] if e["label"] == "chamfer_callouts")
        item = next(i for i in ev["items"] if i["name"].startswith("m_chamfer"))
        # The callout's own story: where it leads from/to, what it dodged, how hard it tried.
        assert item["outcome"] == "placed"
        assert item["view"] == "plan" and item["label"] == "C12"
        assert item["candidates_tried"] >= 1 and item["obstacles"] >= 0
        assert len(item["tip"]) == 2 and len(item["elbow"]) == 2

    def test_turned_immediate_placers_are_traced(self, tmp_path):
        shaft = Cylinder(15, 40) + Pos(0, 0, 35) * Cylinder(10, 30)
        build_drawing(shaft, out=str(tmp_path / "s"), trace=True)
        data = _load(tmp_path / "s.trace.json")
        labels = {e["label"] for e in data["pass_events"]}
        assert "diameter_column_left" in labels and "step_length_chain" in labels
        col = next(e for e in data["pass_events"] if e["label"] == "diameter_column_left")
        assert any(
            i["outcome"] == "placed" and i["name"].startswith("m_dia_z") and len(i["pos"]) == 2
            for i in col["items"]
        )
        chain = next(e for e in data["pass_events"] if e["label"] == "step_length_chain")
        assert chain["items"] and all(
            i["outcome"] in ("placed", "dropped") for i in chain["items"]
        )


class TestFinalizeTransaction:
    """Finding-2 coverage: the recorder appends during the finalize drain, and finalize is
    transactional (#647) — a rolled-back drain must leave the trace state AND the on-disk
    file exactly as they were before finalize()."""

    def test_failed_finalize_leaves_trace_state_and_file_unchanged(self, tmp_path, monkeypatch):
        from draftwright.annotations import _common

        part = Box(80, 60, 30) - Pos(0, -20, 7.5) * Box(80, 20, 15)  # step only (cf. #647 tests)
        dwg = build_drawing(part, out=str(tmp_path / "t"), trace=True, auto_dims=False)
        path = tmp_path / "t.trace.json"
        tr = dwg.solve_trace
        before_state = tr.snapshot()
        before_bytes = path.read_bytes()
        step = next(f for f in dwg.model().features if f.kind == "step_level")
        dwg._defer_intents = True
        dwg.dimension(step, "length", role="step_position")

        real = _common.drain_corridors
        calls = {"n": 0}

        def _boom(ctx, d):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("injected drain failure")
            return real(ctx, d)

        monkeypatch.setattr(_common, "drain_corridors", _boom)
        with pytest.raises(RuntimeError):
            dwg.finalize()
        # Rolled back: no trace records for placements that no longer exist, file untouched.
        assert tr.snapshot() == before_state
        assert path.read_bytes() == before_bytes

        dwg.finalize()  # the clean retry records its solves and rewrites the file once
        assert len(tr.solves) > before_state[0]
        assert path.read_bytes() != before_bytes


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
