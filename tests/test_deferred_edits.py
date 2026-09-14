"""Deferred edit recording, replay, rollback, and finalization."""

import pytest
from _parts import holed_plate as _holed_plate
from build123d import Box, Cylinder, Pos

from draftwright import build_drawing


class TestDeferredEdits:
    """Deferred edit recording and transactional replay."""

    @staticmethod
    def _reconstruct(dwg):
        # The per-feature verb dispatch the imperative emitter used to write. That emitter is
        # gone (#940), so this is no longer a mirror of anything generated — it exercises the
        # `Drawing` edit verbs in-process, which remain a hand-use API (ADR 4 (was 0016)).
        for f in dwg.model().features:
            if f.kind in ("hole", "pattern"):
                dwg.callout(f)
                if f.frame.axis == "z":  # locate() is Z-axis only (side-drilled → auto-pass)
                    dwg.locate(f)
                dwg.furniture(f)
            elif f.kind in ("step", "boss"):
                if f.frame.axis in ("x", "z"):  # callout() places X/Z-turned diameters only
                    dwg.callout(f)
            elif f.kind in ("pocket_pattern", "slot_pattern"):
                dwg.callout(f)  # grouped callout + pitch (no locate/furniture); #841
                continue
            elif f.kind == "step_level":
                dwg.dimension(f, "length", role="step_height")
                continue
            for p in f.parameters():
                if p.span is not None or f.kind == "slot":
                    dwg.dimension(f, p.kind, role=p.role)
        dwg.section()

    def test_deferred_reconstruction_avoids_duplicate_prismatic_step_height(self):
        # The envelope owns overall height when the generated reconstruction records it
        # explicitly; the step-level ladder must then emit only the internal rungs.
        from build123d import Box, Pos

        part = Box(60, 12, 40) - Pos(10, 0, 20) * Box(20, 12, 20)
        dwg = build_drawing(part, auto_dims=False)

        with dwg.deferred():
            self._reconstruct(dwg)

        dims = {
            n: getattr(ann, "label", None)
            for n, ann in dwg.iter_annotations()
            if n.startswith(("dim_height", "dim_length", "dim_step"))
        }
        assert "dim_height" not in dims
        assert [name for name, label in dims.items() if label == "40"] == ["dim_length1"]
        assert any(name.startswith("dim_step") for name in dims)

    def test_intent_reconstruction_is_error_free(self):
        # #400 Ph2 soft acceptance: a fully reconstructed prismatic part, after repair(),
        # has no lint ERRORS. Placement WARNINGS from the corridor-free verbs are the
        # documented #424 fidelity gap, not a failure.
        part = (
            Box(80, 60, 12) - Pos(20, 10, 0) * Cylinder(4, 40) - Pos(-20, -10, 0) * Cylinder(4, 40)
        )
        dwg = build_drawing(part, auto_dims=False)
        self._reconstruct(dwg)
        dwg.repair()
        assert dwg.lint_summary()["errors"] == 0, dwg.lint_summary()["by_code"]

    def test_intent_reconstruction_runs_on_a_side_drilled_part(self):
        # #427 review F1: a side-drilled (non-Z) bore is kind="hole" axis!="z". locate()
        # rejects it by contract (#133), so the emitter must NOT emit locate() for it —
        # else the reconstruction crashes. Exercise the (fixed) dispatch: it must not raise.
        from build123d import Box, Cylinder, Pos, Rot

        part = Box(120, 90, 40) - Pos(0, 0, 5) * Rot(0, 90, 0) * Cylinder(5, 120)
        dwg = build_drawing(part, auto_dims=False)
        assert any(f.kind == "hole" and f.frame.axis != "z" for f in dwg.model().features)
        self._reconstruct(dwg)  # must not raise on the side-drilled bore
        dwg.repair()
        assert dwg.lint_summary()["errors"] == 0, dwg.lint_summary()["by_code"]

    def test_intent_reconstruction_runs_on_a_crowded_turned_shaft(self):
        # #427 review F2: callout() on a step/boss must DEGRADE (drop the overflow leader
        # like the auto-pass), not raise "no room" — else a multi-step turned shaft's
        # reconstruction (one callout() per step) aborts. Must run to completion.
        from build123d import Cylinder

        shaft = Cylinder(24, 12)
        for k in range(1, 10):  # 10 stacked, decreasing-radius steps along Z
            shaft += Cylinder(24 - 2 * k, 12).translate((0, 0, 12 * k))
        dwg = build_drawing(shaft, auto_dims=False)
        assert sum(f.kind == "step" for f in dwg.model().features) >= 5
        self._reconstruct(dwg)  # many callout(step) calls — none may raise "no room"
        dwg.repair()  # the script must reach repair(), not abort before it

    def test_intent_reconstruction_comment_drops_exactly_that(self):
        # #400 Ph2 soft acceptance: commenting one verb line drops exactly that annotation.
        # With auto_dims=False nothing is auto-drawn, so omitting callout(f) removes exactly
        # the callout — no double-dimension, no collateral on locate/furniture.
        part = Box(80, 60, 12) - Pos(20, 10, 0) * Cylinder(4, 40)
        full = build_drawing(part, auto_dims=False)
        hole = next(f for f in full.model().features if f.kind == "hole")
        full.callout(hole)
        full.locate(hole)
        full.furniture(hole)
        before = set(full.annotations())

        partial = build_drawing(part, auto_dims=False)
        h2 = next(f for f in partial.model().features if f.kind == "hole")
        partial.locate(h2)  # callout(h2) "commented out"
        partial.furniture(h2)
        dropped = before - set(partial.annotations())
        assert dropped == {n for n in before if n.startswith("hc_")}
        assert dropped, "commenting callout() should drop the hole's leader"

    def test_deferred_verbs_record_intents_without_placing(self):
        # #426 Phase 1: in deferred mode a verb records an Intent and places nothing.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        base = set(dwg.annotations())  # the detect-only build's title block, no dims
        dwg._defer_intents = True
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        assert dwg.callout(hole) == ""  # nothing placed
        assert dwg.locate(hole) == []
        assert dwg.furniture(hole) == []
        assert len(dwg._intents) == 3
        assert [i.kind for i in dwg._intents] == ["callout", "locate", "furniture"]
        assert set(dwg.annotations()) == base  # recorded, nothing new drawn

    def test_deferred_context_manager_records_then_finalizes(self):
        # #426 Phase 5: `with dwg.deferred()` records inside the block (nothing placed) and
        # runs finalize() on block exit — the record-then-finalize surface the emitter uses.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        base = set(dwg.annotations())
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        with dwg.deferred():
            dwg.callout(hole)
            dwg.locate(hole)
            dwg.furniture(hole)
            assert set(dwg.annotations()) == base  # still recording inside the block
            assert len(dwg._intents) == 3
        assert dwg._intents == []  # finalize drained them on exit
        assert dwg._defer_intents is False  # mode restored
        assert set(dwg.annotations()) - base  # annotations placed by the batch solve

    def test_deferred_block_keeps_intents_and_skips_finalize_on_raise(self):
        # #426 Phase 5: if the block raises, the recorded intents are left intact and
        # finalize() is NOT run — the error surfaces cleanly and a retry can re-drain.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        base = set(dwg.annotations())
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        with pytest.raises(RuntimeError, match="boom"):
            with dwg.deferred():
                dwg.callout(hole)
                raise RuntimeError("boom")
        assert [i.kind for i in dwg._intents] == ["callout"]  # kept, not drained
        assert set(dwg.annotations()) == base  # finalize skipped — nothing placed
        assert dwg._defer_intents is False  # mode still restored (finally)

    def test_deferred_reconstruction_is_error_free_and_drained(self):
        # #426 Phase 5 soft acceptance: a full reconstruction through the deferred CM
        # batch-solves to a lint-error-free sheet and drains every recorded intent.
        part = (
            Box(80, 60, 12) - Pos(20, 10, 0) * Cylinder(4, 40) - Pos(-20, -10, 0) * Cylinder(4, 40)
        )
        dwg = build_drawing(part, auto_dims=False)
        with dwg.deferred():
            self._reconstruct(dwg)  # records every intent inside the block
            assert dwg._intents, "verbs must record inside the deferred block"
        assert dwg._intents == []  # finalize drained on exit
        dwg.repair()
        assert dwg.lint_summary()["errors"] == 0, dwg.lint_summary()["by_code"]

    def test_deferred_reconstruction_lint_no_worse_than_auto_pass(self):
        # #426 acceptance: a deferred reconstruction is no worse than the auto-pass — the
        # recorded intents route through the same batch solvers, so lint score is >= the
        # auto drawing's (faithful, not merely runnable).
        part = (
            Box(80, 60, 12) - Pos(20, 10, 0) * Cylinder(4, 40) - Pos(-20, -10, 0) * Cylinder(4, 40)
        )
        auto = build_drawing(part).lint_summary()
        recon = build_drawing(part, auto_dims=False)
        with recon.deferred():
            self._reconstruct(recon)
        recon.repair()
        got = recon.lint_summary()
        assert got["errors"] == 0
        assert got["score"] >= auto["score"], (got["by_code"], auto["by_code"])

    def test_finalize_replay_equals_live_placement(self):
        # #426 Phase 1: record-then-finalize == placing live (identical annotations).
        part = Box(80, 60, 12) - Pos(20, 10, 0) * Cylinder(4, 40)

        live = build_drawing(part, auto_dims=False)
        h = next(f for f in live.model().features if f.kind == "hole")
        live.callout(h)
        live.locate(h)
        live.furniture(h)

        deferred = build_drawing(part, auto_dims=False)
        base = set(deferred.annotations())
        deferred._defer_intents = True
        h2 = next(f for f in deferred.model().features if f.kind == "hole")
        deferred.callout(h2)
        deferred.locate(h2)
        deferred.furniture(h2)
        assert set(deferred.annotations()) == base  # nothing placed yet
        deferred.finalize()
        assert deferred.annotations() == live.annotations()

    def test_finalize_is_idempotent(self):
        # #426 Phase 1: finalize() twice == once — idempotent via the empty-list early-out.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        dwg._defer_intents = True
        h = next(f for f in dwg.model().features if f.kind == "hole")
        dwg.callout(h)
        dwg.furniture(h)
        dwg.finalize()
        once = set(dwg.annotations())
        dwg.finalize()
        assert set(dwg.annotations()) == once

    def test_finalize_is_a_noop_on_the_live_path(self):
        # #426 Phase 1: the default (non-deferred) build records nothing → finalize no-ops,
        # so the auto-pass / live-verb path is unchanged.
        dwg = build_drawing(_holed_plate())  # auto_dims=True
        assert dwg._defer_intents is False and dwg._intents == []
        before = set(dwg.annotations())
        dwg.finalize()
        assert set(dwg.annotations()) == before

    def test_export_triggers_finalize(self):
        # #426 Phase 1: export() drains recorded intents (calls finalize) before writing.
        import tempfile
        from pathlib import Path

        dwg = build_drawing(_holed_plate(), auto_dims=False)
        base = set(dwg.annotations())
        dwg._defer_intents = True
        h = next(f for f in dwg.model().features if f.kind == "hole")
        dwg.callout(h)
        assert set(dwg.annotations()) == base  # deferred — nothing placed
        with tempfile.TemporaryDirectory() as d:
            dwg.export(str(Path(d) / "x"), formats=("svg", "dxf"))
        assert dwg.annotations()  # finalize ran during export → the callout got placed

    def test_finalize_drains_a_second_batch(self):
        # #428 review: record → finalize → record-more → finalize drains each batch —
        # idempotency is list-draining only, so a second batch is not blocked.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        dwg._defer_intents = True
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        dwg.callout(hole)
        dwg.finalize()
        after_first = set(dwg.annotations())
        dwg.furniture(hole)  # a second batch recorded after the first finalize
        dwg.finalize()
        assert set(dwg.annotations()) > after_first  # the second batch placed too

    def test_finalize_is_resilient_to_a_raising_intent(self):
        # #428 review: an intent that raises at replay surfaces the error and leaves the
        # remaining intents recorded (not silently dropped), and does not brick the drawing.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        dwg._defer_intents = True
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        dwg.callout(hole)  # ok
        dwg.dimension(hole, "diameter")  # a hole ø is a callout → raises at replay
        dwg.furniture(hole)  # ok — must survive the raise
        with pytest.raises(ValueError, match="callout"):
            dwg.finalize()
        # the raise leaves everything not-yet-placed recorded — nothing silently dropped
        kinds = [i.kind for i in dwg._intents]
        assert "dimension" in kinds and "furniture" in kinds

    def test_finalize_routes_locations_through_the_corridor_dedup(self):
        # #426 Phase 2a: two DISTINCT holes sharing an X. The live path places a duplicate
        # m_locx (each locate() is independent); finalize routes them through the real
        # ADR 2 (was 0009) corridor solve, which dedups the coincident X span to ONE dim — matching
        # the auto-pass. The crossing-free / dedup win.
        part = (
            Box(100, 80, 20) - Pos(20, 25, 0) * Cylinder(4, 30) - Pos(20, -25, 0) * Cylinder(6, 30)
        )

        live = build_drawing(part, auto_dims=False)
        for h in (f for f in live.model().features if f.kind == "hole"):
            live.locate(h)
        live_locx = [n for n in live.annotations() if n.startswith("m_locx")]

        deferred = build_drawing(part, auto_dims=False)
        deferred._defer_intents = True
        for h in (f for f in deferred.model().features if f.kind == "hole"):
            deferred.locate(h)
        deferred.finalize()
        fin_locx = [n for n in deferred.annotations() if n.startswith("m_locx")]

        assert len(fin_locx) < len(live_locx)  # corridor deduped the coincident X=20 span
        auto = build_drawing(part)  # auto_dims=True — the reference the corridor matches
        assert len(fin_locx) == len([n for n in auto.annotations() if n.startswith("m_locx")])

    def test_finalize_routes_pinned_locate_as_corridor_candidate(self):
        # #511 slice 1: a deferred user locate(pin=True) is not hand-added after layout.
        # It routes through render_locations' corridor candidates and pins the resulting
        # names after the shared solve chooses legal positions.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        hole = next(f for f in dwg.model().features if f.kind == "hole" and len(f.members) == 1)
        with dwg.deferred():
            dwg.locate(hole, pin=True)
        locs = {n for n in dwg.annotations_of(hole) if n.startswith("m_loc")}
        assert locs
        assert locs <= dwg.registry.pinned_names()
        assert dwg._intents == []

    def test_finalize_pins_shared_location_when_later_ref_requested_pin(self):
        # #511 review: render_locations dedups same-coordinate refs before candidate
        # creation. The pin bit must survive that dedup even when the pinned feature is
        # not the first representative chosen for the shared dimension.
        part = (
            Box(100, 80, 20) - Pos(20, 25, 0) * Cylinder(4, 30) - Pos(20, -25, 0) * Cylinder(6, 30)
        )
        dwg = build_drawing(part, auto_dims=False)
        holes = [f for f in dwg.model().features if f.kind == "hole"]
        dwg._defer_intents = True
        dwg.locate(holes[0])
        dwg.locate(holes[1], pin=True)
        dwg.finalize()
        shared_x = {
            n for n in dwg.annotations() if n.startswith("m_locx") and dwg.get_annotation(n).label
        }
        assert shared_x
        assert shared_x <= dwg.registry.pinned_names()

    def test_finalize_routes_pinned_dimension_as_corridor_candidate(self):
        # #511: a deferred user dimension(pin=True) is a feature intent, not a raw
        # page-coordinate placement after layout. It joins the shared strip solve, remains
        # feature-owned, and pins the placed name only after legal placement.
        dwg = build_drawing(Box(80, 50, 20), auto_dims=False)
        env = next(f for f in dwg.model().features if f.kind == "envelope")
        with dwg.deferred():
            dwg.dimension(
                env,
                "length",
                role="width",
                side="below",
                name="user_width",
                slot=12,
                pin=True,
                priority=25,
            )

        assert "user_width" in dwg.annotations_of(env)
        assert dwg.registry.is_pinned("user_width")
        assert dwg.get_annotation("user_width")._dw_spec.side == "below"
        assert dwg.get_annotation("user_width")._dw_spec.distance == 12
        assert dwg._intents == []

    def test_finalize_resolves_an_implicit_side_before_corridor_routing(self, tmp_path):
        # #1382 review: `dimension()` now leaves the natural side unresolved as None. The
        # routing predicate must accept that state so pin/priority reach the shared solve;
        # `_queue_dimension_intent` resolves the side from the exact parameter span.
        dwg = build_drawing(
            Box(80, 50, 20),
            auto_dims=False,
            out=str(tmp_path / "implicit-side"),
            trace=True,
        )
        env = next(f for f in dwg.model().features if f.kind == "envelope")
        with dwg.deferred():
            dwg.dimension(
                env,
                "length",
                role="width",
                name="implicit_side_width",
                pin=True,
                priority=25,
            )

        assert dwg.registry.is_pinned("implicit_side_width")
        assert dwg.get_annotation("implicit_side_width")._dw_spec.side == "above"
        candidate = next(
            candidate
            for solve in dwg.solve_trace.solves
            for candidate in solve["candidates"]
            if candidate["name"] == "implicit_side_width"
        )
        assert candidate["priority"] == 100
        assert candidate["anchored"] is True

    def test_finalize_mixed_corridor_batch_places_each_exactly_once(self):
        # #699 slice b (Codex review): the drain stages now run in the auto-pass's
        # canonical _PASS_SEQUENCE order, which moved the register-only height-ladder /
        # step-position stages AFTER locations. Registration order decides corridor
        # KEY-creation order (= drain order) and same-priority tie-breaks, so the reorder
        # is observable — pin the mixed-batch outcome: locations, a height rung, a
        # shoulder position and a pinned user dimension all competing in one deferred
        # batch must each place exactly once, with no error-severity lint and nothing
        # left recorded.
        part = (
            Box(80, 60, 30) - Pos(0, -20, 7.5) * Box(80, 20, 15) - Pos(20, 15, 0) * Cylinder(3, 30)
        )
        dwg = build_drawing(part, auto_dims=False)
        step = next(f for f in dwg.model().features if f.kind == "step_level")
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        env = next(f for f in dwg.model().features if f.kind == "envelope")
        with dwg.deferred():
            dwg.locate(hole)  # the locations corridor (plan/side above)
            dwg.dimension(step, "length", role="step_height")  # the front-right ladder
            dwg.dimension(step, "length", role="step_position")  # the shoulder corridor
            dwg.dimension(  # a pinned user dim joins the same drain (ADR 2 (was 0012))
                env, "length", role="width", side="below", name="user_width", pin=True
            )
        assert dwg._intents == []  # every routed intent drained
        assert len([n for n in dwg.annotations() if n.startswith("dim_shoulder")]) == 1
        assert [n for n in dwg.annotations() if n.startswith("dim_step")]  # rung placed
        assert {n for n in dwg.annotations_of(hole) if n.startswith("m_loc")}
        assert "user_width" in dwg.annotations() and dwg.registry.is_pinned("user_width")
        assert [i for i in dwg.lint() if i.severity == "error"] == []
