"""Turned-shaft axial length dimension behavior."""

import pytest
from _parts import x_stepped_shaft as _x_stepped_shaft
from build123d import Box, Cylinder, Pos, Rotation

from draftwright import build_drawing


@pytest.fixture(scope="module")
def x_shaft_dwg():
    return build_drawing(_x_stepped_shaft())


def _compiled_step_length_ids(dwg):
    from draftwright.model.compiled import compile_dimensions

    ids = {
        dimension.id
        for group in compile_dimensions(dwg.model()).of_kind("step")
        if (dimension := group.dim(kind="length")) is not None
    }
    assert None not in ids
    return ids


class TestTurnedLengths:
    """Axial step-length chain for X-axis turned parts (the drive-screw gap:
    every diameter dimensioned, no shoulder locatable)."""

    def test_each_step_length_is_dimensioned(self, x_shaft_dwg):
        dwg = x_shaft_dwg  # ø30 l40 then ø16 l30
        labels = {o.label for n, o in dwg.iter_annotations() if n.startswith("m_steplen")}
        assert labels == {"40", "30"}

    @pytest.mark.parametrize(
        "shaft",
        [
            pytest.param(_x_stepped_shaft(), id="x-turned"),
            pytest.param(Cylinder(15, 30) + Pos(0, 0, 30) * Cylinder(8, 30), id="z-turned"),
        ],
    )
    def test_each_step_length_records_its_measurement_identity(self, shaft):
        dwg = build_drawing(shaft)
        names = [name for name in dwg.annotations() if name.startswith("m_steplen")]
        assert names
        for name in names:
            keys = dwg.measurement_keys(name)
            assert len(keys) == 1, f"{name} must identify exactly the step length it draws"
            assert keys[0]["feature"].startswith("step@")
            assert keys[0]["parameter_id"] == "step.length"
        recorded = {mid for name in names for mid in dwg.registry.measurement_of(name)}
        assert recorded == _compiled_step_length_ids(dwg)

    def test_overall_width_suppressed_for_turned_part(self, x_shaft_dwg):
        # The complete chain conveys the overall length, so the envelope width dim
        # is dropped — no double dimensioning (ISO 129).
        dwg = x_shaft_dwg
        assert "m_env_width" not in dwg.annotations()

    def test_turned_part_lints_clean(self, x_shaft_dwg):
        dwg = x_shaft_dwg
        codes = dwg.lint_summary()["by_code"]
        assert codes.get("axial_length_missing", 0) == 0
        assert codes.get("annotation_overlap", 0) == 0

    def test_three_step_shaft_dimensions_all_steps(self):
        # Non-uniform step lengths (10/8/12), base-stacked so they sit flush → each
        # segment dimensioned individually (the uniform-run collapse, #230, is
        # exercised separately below).
        from build123d import Align, Cylinder, Pos, Rotation

        b = Align.MIN
        stack = Cylinder(10, 10, align=(Align.CENTER, Align.CENTER, b))
        stack += Pos(0, 0, 10) * Cylinder(7, 8, align=(Align.CENTER, Align.CENTER, b))
        stack += Pos(0, 0, 18) * Cylinder(4, 12, align=(Align.CENTER, Align.CENTER, b))
        dwg = build_drawing(Rotation(0, 90, 0) * stack)
        assert len([n for n in dwg.annotations() if n.startswith("m_steplen")]) == 3

    def test_uniform_staircase_collapses_to_n_times(self):
        # A uniform run (4 equal-length steps) collapses to one "N× length" dim
        # instead of four identical segment dims (#230) — and the collapsed dim must
        # still satisfy axial coverage (lint clean, every shoulder located).
        from build123d import Align, Cylinder, Pos

        b = Align.MIN
        shaft = Cylinder(30, 10, align=(Align.CENTER, Align.CENTER, b))
        for i, r in enumerate([25, 20, 15], start=1):
            shaft += Pos(0, 0, 10 * i) * Cylinder(r, 10, align=(Align.CENTER, Align.CENTER, b))
        dwg = build_drawing(shaft)
        steplen = {n: o.label for n, o in dwg.iter_annotations() if n.startswith("m_steplen")}
        assert steplen == {"m_steplen_typ": "4× 10"}, steplen
        assert "axial_length_missing" not in {i.code for i in dwg.lint()}

        keys = dwg.measurement_keys("m_steplen_typ")
        assert len(keys) == 4, "the collapsed annotation draws all four step lengths"
        assert len({key["feature"] for key in keys}) == 4
        assert {key["parameter_id"] for key in keys} == {"step.length"}
        assert set(dwg.registry.measurement_of("m_steplen_typ")) == _compiled_step_length_ids(dwg)

    def test_prismatic_part_has_no_step_lengths(self):
        dwg = build_drawing(Box(80, 60, 20))
        assert not any(n.startswith("m_steplen") for n in dwg.annotations())

    def test_grooved_shaft_step_chain_not_flagged_axial_missing(self):
        # A groove band is excluded from the step-length chain (#606) and dimensioned by its
        # WIDTH callout instead. The axial-coverage lint counts prof.steps (which still includes
        # the groove band), so it must credit the rendered groove-width callout as covering that
        # band — else an otherwise fully-dimensioned grooved shaft false-fires axial_length_missing
        # (#628, a regression from the #606 groove exclusion).
        from build123d import Cylinder, Pos

        shaft = (
            Pos(0, 0, 7.5) * Cylinder(30, 15)
            + Pos(0, 0, 32) * Cylinder(20, 34)
            + Pos(0, 0, 53) * Cylinder(13, 8)  # ø26 local-minimum band → recognised as a groove
            + Pos(0, 0, 74) * Cylinder(20, 34)
            + Pos(0, 0, 107) * Cylinder(14, 32)
        ) - Pos(0, 0, 61.5) * Cylinder(8, 123)
        dwg = build_drawing(shaft, number="X")
        assert any(n.startswith("m_groove") for n in dwg.annotations())  # the ø26 band IS a groove
        assert dwg.lint_summary()["by_code"].get("axial_length_missing", 0) == 0

    def test_chain_skips_gracefully_when_no_room(self):
        # Forced onto a too-small page, the chain must SKIP rather than run off the
        # page edge (the parity guard the diameter row has). Lint then reports the
        # gap instead of the engine emitting off-page dims.
        from build123d import Cylinder, Pos, Rotation

        z = 0.0
        part = None
        for i in range(10):
            seg = Pos(0, 0, z + 1.0) * Cylinder((12 - 0.6 * i) / 2, 2.0)
            part = seg if part is None else part + seg
            z += 2.0
        dwg = build_drawing(
            Rotation(0, 90, 0) * part,
            page="90x70",
            scale=4.0,
            scale_policy="permissive",
        )
        assert not any(
            n.startswith("m_steplen") for n in dwg.annotations()
        )  # skipped, not off-page
        assert dwg.lint_summary()["by_code"].get("axial_length_missing", 0) >= 1

    def test_dense_chain_skips_instead_of_cramming(self):
        # A genuinely dense turned shaft (many fine non-uniform steps) whose labels
        # cannot be spaced legibly must SKIP the chain, not overprint a wall of
        # overlapping dims (#293). Any placed step-length dims must not overlap.
        from build123d import Align, Cylinder, Pos, Rotation

        from draftwright.annotations._common import _anno_box

        b = Align.MIN
        shaft = None
        z = 0.0
        for i in range(16):
            d = 20 if i % 2 == 0 else 16  # alternating ø → truly stepped, fine pitch
            ln = 3.0 + (i % 3) * 0.4  # non-uniform (no N× collapse)
            seg = Pos(0, 0, z) * Cylinder(d / 2, ln, align=(Align.CENTER, Align.CENTER, b))
            shaft = seg if shaft is None else shaft + seg
            z += ln
        dwg = build_drawing(Rotation(0, 90, 0) * shaft)
        boxes = [_anno_box(o) for n, o in dwg.iter_annotations() if n.startswith("m_steplen")]

        def overlap(a, c):
            return a and c and not (a[2] <= c[0] or a[0] >= c[2] or a[3] <= c[1] or a[1] >= c[3])

        assert not any(
            overlap(boxes[i], boxes[j])
            for i in range(len(boxes))
            for j in range(i + 1, len(boxes))
        ), "step-length dims overprint — chain crammed instead of skipping"

    def test_crowded_chain_staggers_into_two_tiers_at_current_scale(self):
        # A *moderately* crowded chain — steps just ABOVE the arrowhead floor (so no
        # detail view is triggered), but with labels that would collide on one tier.
        # Rather than cram, the chain staggers successive dims between a near and a far
        # tier (ISO 129-1) so every step length stays legible at the drawing's own
        # scale (#293). Scale pinned so the crowding regime is deterministic.
        from build123d import Align, Cylinder, Pos, Rotation

        b = Align.MIN
        specs = [(8, 3.1), (12, 2.9), (8, 3.2), (12, 2.8), (6, 3.0)]  # ~3 mm, > floor
        shaft = None
        z = 0.0
        for d, ln in specs:
            seg = Pos(0, 0, z) * Cylinder(d / 2, ln, align=(Align.CENTER, Align.CENTER, b))
            shaft = seg if shaft is None else shaft + seg
            z += ln
        dwg = build_drawing(Rotation(0, 90, 0) * shaft, scale=2.0)
        assert "detail_a" not in dwg.views  # above floor → no detail, staggered in place
        steps = {n: o for n, o in dwg.iter_annotations() if n.startswith("m_steplen")}
        assert len(steps) == 5  # every segment dimensioned, none dropped
        assert dwg.lint_summary()["by_code"].get("axial_length_missing", 0) == 0
        # Two tiers: the dims sit at (at least) two distinct offset rows.
        rows = {round(o._dw_spec.distance, 6) for o in steps.values()}
        assert len(rows) >= 2, "chain did not stagger into multiple tiers"

        # Labels don't overprint each other.
        boxes = [o.label_bbox for o in steps.values()]

        def overlap(a, c):
            return a and c and not (a[2] <= c[0] or a[0] >= c[2] or a[3] <= c[1] or a[1] >= c[3])

        assert not any(
            overlap(boxes[i], boxes[j])
            for i in range(len(boxes))
            for j in range(i + 1, len(boxes))
        ), "staggered step-length labels overprint"

    def test_subfloor_head_gets_detail_view(self):
        # A fine head (sub-floor steps) + a long shaft (the GRM-03 pattern). The head
        # can't be dimensioned legibly in line, so the unified detail pipeline (#307)
        # locates it as one block on the main view + breaks it down in DETAIL A, with
        # axial coverage satisfied across the two views (no double-dimensioning).
        from build123d import Align, Cylinder, Pos, Rotation

        b = Align.MIN
        specs = [(4, 1.5), (6, 2.0), (4, 2.5), (3, 25.0)]  # non-uniform sub-floor head
        shaft = None
        z = 0.0
        for d, ln in specs:
            seg = Pos(0, 0, z) * Cylinder(d / 2, ln, align=(Align.CENTER, Align.CENTER, b))
            shaft = seg if shaft is None else shaft + seg
            z += ln
        dwg = build_drawing(Rotation(0, 90, 0) * shaft, scale=2.0)
        assert "detail_a" in dwg.views  # crowded head → enlarged detail
        main = {n: o for n, o in dwg.iter_annotations() if n.startswith("m_steplen")}
        assert "25" in {o.label for o in main.values()}
        block = next(name for name, obj in main.items() if obj.label != "25")
        assert dwg.measurement_keys(block) == [], "the synthetic head extent is not one step"
        detail = [n for n in dwg.annotations() if n.startswith("dim_detail_a_steplen")]
        assert len(detail) >= 3
        assert all(len(dwg.measurement_keys(name)) == 1 for name in detail)
        assert dwg.lint_summary()["by_code"].get("axial_length_missing", 0) == 0

    def test_two_sub_floor_runs_get_separate_non_colliding_details(self):
        # Two separated fine-step clusters → two detail views (A, B). Their dims use
        # view-scoped names, so detail B's dims don't evict detail A's (the #307-review
        # name-collision regression) and axial coverage holds across all views.
        from build123d import Align, Cylinder, Pos, Rotation

        b = Align.MIN
        specs = [(4, 1.5), (6, 2.0), (4, 2.5), (3, 22), (6, 1.5), (4, 2.0), (5, 2.5), (2, 22)]
        shaft = None
        z = 0.0
        for d, ln in specs:
            seg = Pos(0, 0, z) * Cylinder(d / 2, ln, align=(Align.CENTER, Align.CENTER, b))
            shaft = seg if shaft is None else shaft + seg
            z += ln
        dwg = build_drawing(Rotation(0, 90, 0) * shaft, page="A2", scale=2.0)
        assert {"detail_a", "detail_b"} <= set(dwg.views)
        names = [n for n in dwg.annotations() if "steplen" in n and "detail" in n]
        assert len(names) == len(set(names))  # no eviction — all detail dims survive
        assert any(n.startswith("dim_detail_a_") for n in names)
        assert any(n.startswith("dim_detail_b_") for n in names)
        assert dwg.lint_summary()["by_code"].get("axial_length_missing", 0) == 0

    def test_head_block_does_not_collapse_main_chain_to_n_times(self):
        # When the head-block extent happens to match the legible step lengths, the main
        # chain (block + steps) must NOT collapse to a uniform "N× v" — the block is a
        # compound region, not a repeated step, and "N× v" would be a false claim of N
        # equal steps (#307 review).
        from build123d import Align, Cylinder, Pos, Rotation

        b = Align.MIN
        # head 1.5/2.0/2.5 (sub-floor, sums to 6) + two legible 6 mm steps
        specs = [(4, 1.5), (6, 2.0), (4, 2.5), (7, 6.0), (5, 6.0)]
        shaft = None
        z = 0.0
        for d, ln in specs:
            seg = Pos(0, 0, z) * Cylinder(d / 2, ln, align=(Align.CENTER, Align.CENTER, b))
            shaft = seg if shaft is None else shaft + seg
            z += ln
        dwg = build_drawing(Rotation(0, 90, 0) * shaft, scale=2.0)
        main = {o.label for n, o in dwg.iter_annotations() if n.startswith("m_steplen")}
        assert not any("×" in v for v in main)  # no false uniform-staircase collapse
        assert dwg.lint_summary()["by_code"].get("axial_length_missing", 0) == 0

    def test_disjoint_coaxial_bodies_do_not_form_a_non_contiguous_turned_profile(self):
        # Recognisers 0.4.9 makes turned-profile membership body-local. Two coaxial discs with
        # an axial air gap are therefore two single-diameter bosses, not one invented stepped
        # shaft. The old cross-body profile exercised #797's shoulder lookup; the truthful
        # body-local projection has no axial step chain and must still lint without crashing.
        from build123d import Align

        b = Align.MIN
        part = Rotation(0, 90, 0) * (
            Cylinder(15, 10, align=(Align.CENTER, Align.CENTER, b))
            + Pos(0, 0, 20) * Cylinder(10, 10, align=(Align.CENTER, Align.CENTER, b))
        )
        dwg = build_drawing(part, number="X")
        codes = dwg.lint_summary()["by_code"]  # must not raise KeyError
        assert not [n for n in dwg.annotations() if n.startswith("m_steplen")]
        assert {feature.kind for feature in dwg.model().features} == {"boss"}
        assert codes.get("axial_length_missing", 0) == 0
