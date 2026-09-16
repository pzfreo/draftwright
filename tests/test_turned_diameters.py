"""Turned-shaft diameter recognition, placement, and replay behavior."""

import math

import pytest
from _drawing_helpers import sheet_script_drawing as _sheet_script_drawing
from _parts import x_stepped_shaft as _x_stepped_shaft
from build123d import Align, Axis, Box, Cylinder, Pos, Rotation

from draftwright import build_drawing
from draftwright.model import DimensionId


@pytest.fixture(scope="module")
def x_shaft_dwg():
    return build_drawing(_x_stepped_shaft())


class TestTurnedDiameters:
    """External turned diameters get ø leader callouts through the IR renderer."""

    @staticmethod
    def _issue_881_y_step_flange():
        """A non-rotational four-lug flange with a coaxial Y-axis stepped stack."""
        part = Cylinder(21, 4)
        for x in (-18, 18):
            for y in (-18, 18):
                part = part + Pos(x, y, 2) * Box(
                    10,
                    10,
                    4,
                    align=(Align.CENTER, Align.CENTER, Align.CENTER),
                )
        # Deliberate overlap keeps this one solid while leaving distinct axial bands.
        part = part + Pos(0, 0, 2) * Cylinder(15.5, 12)
        part = part + Pos(0, 0, 10) * Cylinder(12.5, 12)
        part = part - Cylinder(8, 30)
        for x in (-18, 18):
            for y in (-18, 18):
                part = part - Pos(x, y, 0) * Cylinder(2, 10)
        return part.rotate(Axis.X, 90)

    @staticmethod
    def _issue_890_cardinal_hole_flange():
        """The same stepped stack with bolt holes on end-view cardinal rays."""
        locations = ((18, 0), (-18, 0), (0, 18), (0, -18))
        part = Cylinder(21, 4)
        for x, y in locations:
            part += Pos(x, y, 2) * Box(
                10,
                10,
                4,
                align=(Align.CENTER, Align.CENTER, Align.CENTER),
            )
        part += Pos(0, 0, 2) * Cylinder(15.5, 12)
        part += Pos(0, 0, 10) * Cylinder(12.5, 12)
        part -= Cylinder(8, 30)
        for x, y in locations:
            part -= Pos(x, y, 0) * Cylinder(2, 10)
        return part.rotate(Axis.X, 90)

    @staticmethod
    def _issue_892_y_chain(*, axis_z=0.0, rotation=90):
        b = Align.MIN
        part = Cylinder(22.5, 3, align=(Align.CENTER, Align.CENTER, b))
        part += Pos(0, 0, 3) * Cylinder(17, 5.5, align=(Align.CENTER, Align.CENTER, b))
        part += Pos(0, 0, 8.5) * Cylinder(14, 3.5, align=(Align.CENTER, Align.CENTER, b))
        return Pos(0, 0, axis_z) * part.rotate(Axis.X, rotation)

    @staticmethod
    def _assert_y_diameter_leaders_clear_holes(dwg):
        circles = []
        for feature in dwg.model().features:
            if feature.frame.axis != "y":
                continue
            if feature.kind == "hole":
                diameter = feature.diameter
                locations = feature.members or (feature.frame.origin,)
            elif feature.kind == "pattern":
                diameter = feature.member.diameter
                locations = feature.members or (feature.frame.origin,)
            else:
                continue
            for location in locations:
                x, y, *_ = dwg.at("front", *location)
                circles.append((x, y, diameter / 2 * dwg.scale))

        for name in (n for n in dwg.annotations() if n.startswith("m_dia_y")):
            ann = dwg.get_annotation(name)
            ax, ay = ann.tip[:2]
            bx, by = ann.elbow[:2]
            vx, vy = bx - ax, by - ay
            length2 = vx * vx + vy * vy
            for cx, cy, radius in circles:
                t = max(0.0, min(1.0, ((cx - ax) * vx + (cy - ay) * vy) / length2))
                distance = math.hypot(cx - (ax + t * vx), cy - (ay + t * vy))
                assert distance > radius

    def test_issue_881_y_axis_steps_render_without_half_envelope_locations(self):
        dwg = build_drawing(self._issue_881_y_step_flange())

        steps = [f for f in dwg.model().features if f.kind == "step"]
        assert steps and {f.frame.axis for f in steps} == {"y"}

        y_diameters = {
            dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_dia_y")
        }
        assert y_diameters == {"ø25", "ø31", "ø42"}
        # #890: each queued radial leader retains its own StepFeature provenance;
        # a lazy generator previously captured the final loop iteration's owner.
        diameter_owners = [
            feature for feature in dwg.model().features if feature.kind in {"step", "boss"}
        ]
        # Four since quiddity 0.2.8, which coalesces contiguous equal-diameter bands: the
        # flange's profile is four tiling segments (ø25 -16..-8, ø31 -8..-2, ø42 -2..2,
        # ø31 2..4) where the provider previously reported each split in two. #890's subject
        # is per-leader provenance, which four distinct owners exercise exactly as eight did.
        assert len(diameter_owners) == 4
        for name in (n for n in dwg.annotations() if n.startswith("m_dia_y")):
            ann = dwg.get_annotation(name)
            owner = dwg.registry.feature_of(name)
            diameter = float(ann.label.removeprefix("ø"))
            matches = [feature for feature in diameter_owners if feature.diameter == diameter]
            assert any(owner is match for match in matches)
            (identity,) = dwg.registry.measurement_of(name)
            assert identity.feature is owner and identity.parameter == f"{owner.kind}.diameter"
        assert all(
            dwg.registry.has_measurement(DimensionId(owner, f"{owner.kind}.diameter"))
            for owner in diameter_owners
        )

        self._assert_y_diameter_leaders_clear_holes(dwg)
        step_labels = {dwg.get_annotation(n).label for n in dwg.annotations() if "steplen" in n}
        assert step_labels >= {"8", "4"}
        assert "2" in step_labels or "4× 2" in step_labels
        assert {dwg.view_of(n) for n in dwg.annotations() if n.startswith("m_steplen")} == {"side"}
        # Since quiddity 0.2.8 the chain is 8 | 6 | 4 | 2, and the 2 mm segment is below the
        # legibility floor, so this fixture now routes to an enlarged detail. That splits the
        # provenance in two, exactly as `test_issue_892_short_y_step_chain_moves_to_enlarged_
        # side_detail` documents: the detail carries one claimed dimension per step, and the
        # main view keeps the aggregate block, which claims nothing because it "is not any one
        # approved step measurement". Assert both halves rather than only the surviving one.
        detail_steps = [n for n in dwg.annotations() if n.startswith("dim_detail_a_steplen")]
        assert detail_steps, "the crowded chain must be sized somewhere"
        assert all(len(dwg.measurement_keys(n)) == 1 for n in detail_steps)
        for name in (n for n in dwg.annotations() if n.startswith("m_steplen")):
            label = dwg.get_annotation(name).label
            if label == "4× 2":
                expected = 4
            elif "detail_a" in dwg.views:
                expected = 0  # the aggregate block
            else:
                expected = 1
            assert len(dwg.measurement_keys(name)) == expected

        # The central Y-axis bore shares the detected turned-profile axis. Its
        # centreline locates it; generic minimum-edge offsets would redundantly
        # show half the 46 mm envelope in both X and Z (#881).
        assert dwg.view_of("centerline_side") == "side"
        # RaisedPad v2 no longer misclassifies two rotated flange lugs as pads. With that
        # false requirement gone, ADR 2 (was 0018) omits the redundant plan view and its furniture;
        # the front profile plus side end view still define the Y-axis stack completely.
        assert dwg.view_of("centerline_plan") is None
        assert "plan" not in dwg.views
        assert not any(n.startswith("dim_loc_front_") for n in dwg.annotations())
        assert not any(n.startswith("dim_loc_side_") for n in dwg.annotations())

        codes = {issue.code for issue in dwg.lint()}
        assert "feature_not_dimensioned" not in codes
        assert "axial_length_missing" not in codes

        # #798: the bolt-circle callout spends 27.6 mm of its 49 mm shaft inside the
        # flange body. This assertion used to read `not in codes` and passed only
        # because the check was blind: the outline-crossing form exempted every
        # `covers_diameters` annotation wholesale, and this is a hole callout. Measured
        # against the filled material field the cut is real, and pinning WHICH leader
        # is a stronger guard than the absence it replaces. Front-view hole callouts
        # keep their specialised placer under ADR 2 (was 0014), so routing this one clear is
        # #798's own remaining work.
        silhouette = [i for i in dwg.lint() if i.code == "leader_crosses_silhouette"]
        assert len(silhouette) == 1, [i.message for i in silhouette]
        assert "4× ⌀4 THRU" in silhouette[0].message

        for name in tuple(dwg.annotations()):
            if "steplen" in name:
                dwg.remove(name)
        assert "axial_length_missing" in {issue.code for issue in dwg.lint()}

    @pytest.mark.parametrize(
        ("overall", "step", "expect_height", "expect_rungs"),
        [
            (False, False, False, False),
            (True, False, True, False),
            (False, True, False, True),
            (True, True, True, True),
        ],
        ids=["neither", "overall-only", "step-only", "both"],
    )
    def test_each_ladder_intent_draws_only_what_it_recorded(
        self, overall, step, expect_height, expect_rungs
    ):
        """`render_height_ladder` draws TWO independent things, and the drain passed it the
        whole compiled plan once EITHER intent was present.

        So `overall_height()` alone also rebuilt the step rungs — a dimension nobody recorded,
        and live/deferred divergence in the change that relies on their equivalence (#934
        review). The converse held too: a step-height intent carried the overall height along,
        so commenting out `dwg.overall_height()` in a generated script need not have removed it.

        A model with BOTH approved ladders is required to see this at all. Every earlier
        fixture exposes one, which is why cross-contamination was invisible: with a single
        ladder, "pass everything" and "pass what was asked for" are the same plan.

        Asserted on WHICH ladders appear, not how many marks: with both recorded, the strip
        legitimately drops a rung for room, and pinning counts would make this test fail on
        placement changes that have nothing to do with the property.
        """
        from draftwright.model.ir import Frame, PartModel, StepLevelFeature

        part = Box(80, 60, 30)
        model = PartModel(
            bbox=part.bounding_box(),
            orientation="prismatic",
            features=[
                StepLevelFeature(
                    Frame((0, 0, 0), "z"),
                    base=-15,
                    levels=(-5, 5),
                    shoulders=(("x", 0),),
                    datum=(-40, -30, -15),
                )
            ],
            datums=[],
        )
        dwg = build_drawing(part, model=model, auto_dims=False)
        feature = dwg.model().features[0]
        with dwg.deferred():
            if overall:
                dwg.overall_height()
            if step:
                dwg.dimension(feature, "length", role="step_height")

        drawn = {n for n, _ in dwg.iter_annotations()}
        assert ("dim_height" in drawn) is expect_height
        assert any(n.startswith("dim_step") for n in drawn) is expect_rungs

    def test_overall_height_live_and_deferred_agree_with_a_step_ladder_present(self):
        """The equivalence, on the model that can break it.

        `test_the_overall_height_intent_is_commentable_not_injected` uses a part with no
        step_level, so its live and deferred results agreed even while the drain was drawing
        every ladder it could find. The ladder itself is deferred-only by construction (a
        correlated set, routed at the drain), so the overall height is the half where live and
        deferred are both reachable — and therefore the half that can disagree."""
        from draftwright.model.ir import Frame, PartModel, StepLevelFeature

        part = Box(80, 60, 30)

        def _model():
            return PartModel(
                bbox=part.bounding_box(),
                orientation="prismatic",
                features=[
                    StepLevelFeature(
                        Frame((0, 0, 0), "z"),
                        base=-15,
                        levels=(-5, 5),
                        shoulders=(("x", 0),),
                        datum=(-40, -30, -15),
                    )
                ],
                datums=[],
            )

        live = build_drawing(part, model=_model(), auto_dims=False)
        live.overall_height()
        deferred = build_drawing(part, model=_model(), auto_dims=False)
        with deferred.deferred():
            deferred.overall_height()
        assert deferred.annotations() == live.annotations()

    @pytest.mark.parametrize("deferred", [False, True], ids=["live", "deferred"])
    def test_overall_height_is_refused_when_the_model_declares_an_envelope(self, deferred):
        """One measurement, one verb.

        `overall_height()` exists ONLY for the featureless fallback — a model with no
        `EnvelopeFeature`, whose height comes from the bounding box and has nothing to name.
        It did not enforce that, so on an enveloped model both public spellings were
        available and composing them drew the height twice:

            live,  overall_height() then dimension(env, …, role="height")  →  BOTH
            live,  the reverse order                                       →  one
            deferred, either order                                         →  one

        Order-dependent live AND live ≠ deferred, from two spellings of one measurement —
        the "three spellings of pin" problem (#906) in miniature (#934 review).

        Refused before the deferred/live split, so both routes answer identically. That is
        the shape #925 settled for `callout()`: a check on one side makes the answer depend
        on whether you are inside `deferred()`.
        """
        from draftwright.model.declare import envelope

        part = Box(80, 60, 30)
        dwg = build_drawing(part, model=[envelope(part)], auto_dims=False)
        with pytest.raises(ValueError, match="declares an envelope"):
            if deferred:
                with dwg.deferred():
                    dwg.overall_height()
            else:
                dwg.overall_height()

    @pytest.mark.parametrize("deferred", [False, True], ids=["live", "deferred"])
    def test_an_enveloped_height_is_drawn_once_by_its_feature_verb(self, deferred):
        """The false-positive half: refusing the second spelling must not cost the first.

        The enveloped model's height is still dimensionable — through the feature that owns
        it — and exactly once, on both routes."""
        from draftwright.model.declare import envelope

        part = Box(80, 60, 30)
        dwg = build_drawing(part, model=[envelope(part)], auto_dims=False)
        feature = dwg.model().features[0]
        if deferred:
            with dwg.deferred():
                dwg.dimension(feature, "length", role="height")
        else:
            dwg.dimension(feature, "length", role="height")
        heights = [
            a.label
            for n, a in dwg.iter_annotations()
            if n.startswith(("dim_height", "dim_length"))
        ]
        assert heights == ["30"], f"the height should be drawn once, got {heights}"

    def test_the_overall_height_intent_is_commentable_not_injected(self):
        """The half that the first fix got wrong, and the reason there is a verb at all.

        Making the drain draw the overall height whenever the compiler approved one restored
        #889's parity — and broke record-then-finalize == place-live, because `auto_dims=False`
        means the recorded verbs ARE the drawing and an automatic dimension nobody asked for
        appeared in the deferred result. `test_finalize_replay_equals_live_placement` caught it.

        So it is an INTENT: absent unless recorded, which also makes it commentable, which is
        the property the whole intent-level script rests on (ADR 4 (was 0016), "the script records
        intent").
        """
        # The verb's actual domain: a part with NO `EnvelopeFeature`, so the height comes
        # from the bounding-box fallback and there is nothing to name. (An enveloped model
        # refuses this verb and uses its feature instead — see the test above; this fixture
        # asserted the property on an enveloped plate, which is the overlap itself.)
        part = self._issue_881_y_step_flange()
        assert not any(f.kind == "envelope" for f in build_drawing(part).model().features)

        silent = build_drawing(part, auto_dims=False)
        assert "dim_height" not in silent.annotations(), "not recorded ⇒ not drawn"

        live = build_drawing(part, auto_dims=False)
        assert live.overall_height() == ["dim_height"]

        deferred = build_drawing(part, auto_dims=False)
        with deferred.deferred():
            deferred.overall_height()
        assert deferred.annotations() == live.annotations(), "record-then-finalize == live"

    def test_overall_height_round_trips_through_generated_script(self, tmp_path):
        """#889: the replay dropped the automatic overall height, silently and lint-clean.

        Two different things share `render_height_ladder`, and the drain gated BOTH on the
        step-ladder intent. The step-height LADDER is a `step_level` feature's correlated
        rungs, so one recorded intent meaning "rebuild the whole chain" is right. The OVERALL
        HEIGHT is envelope furniture — and on a part with no `EnvelopeFeature` it comes from
        the compiler's bounding-box fallback, so there is NO feature for a script to record an
        intent against. It could never be replayed, only lost.

        The Y-axis stepped flange is the case that exposes it: `step` features but no
        `step_level`, so no ladder intent exists to carry the overall height along.

        Asserted as full annotation-set parity rather than "dim_height is present", because
        the acceptance is that replay matches the automatic drawing — and the risk on the
        other side is duplicating the Y-step length chain, which a presence check would miss.

        Retargeted onto the Sheet script by #940. This fixture also carries what
        `test_issue_881_generated_script_emits_y_step_intents` used to assert about the
        imperative script's TEXT: that suite's executable half — side/plan centrelines, no
        front/side location dims, the step-length chain on the side view — is folded in
        below, since the source-text half described a file that no longer exists.
        """
        part = self._issue_881_y_step_flange()
        from math import pi

        for x in (-18, 18):
            for z in (-18, 18):
                lug = Pos(x, -2, z) * Box(10, 4, 10)
                assert (part & lug).volume == pytest.approx(400 - pi * 2**2 * 4)
        auto = build_drawing(part)
        assert not any(f.kind == "pocket" for f in auto.model().features)
        assert not any(f.kind == "envelope" for f in auto.model().features), (
            "the fixture must have NO envelope feature — the bbox fallback is the case "
            "with no intent to record"
        )

        _source, replayed = _sheet_script_drawing(part, tmp_path, "flange")

        automatic = {n for n, _ in auto.iter_annotations()}
        replay = {n for n, _ in replayed.iter_annotations()}
        assert "dim_height" in automatic, "the fixture must draw an overall height to lose"
        assert replay == automatic, (
            f"replay differs — missing {sorted(automatic - replay)}, "
            f"extra {sorted(replay - automatic)}"
        )
        assert (
            auto.get_annotation("dim_height").label == replayed.get_annotation("dim_height").label
        )
        # The off-axis four-hole pattern has relative pitch/count but no absolute X/Z
        # location dimensions. The hole-family ledger added by #1143 reports those two
        # physical requirements honestly on both paths; reconstruction must preserve the
        # same critique as well as the same annotation set.
        # The `leader_crosses_silhouette` entry is the #798 bolt-circle cut described in
        # test_issue_881_...; it appears on BOTH paths, which is what this test is
        # actually about — the replay reproduces the same critique, defects included.
        # Candidate prevention (#1334) removes the same-batch step-chain crossings, and
        # measured block clearance now clears the former cross-producer ink crossing. The
        # remaining critique is reproduced on both paths.
        # Quiddity refuses four recess proposals at the solid mounting lugs. The
        # material-volume checks above prohibit reviving the old false pocket claims.
        assert (
            auto.lint_summary()["by_code"]
            == replayed.lint_summary()["by_code"]
            == {
                "hole_requirement_missing": 2,
                "leader_crosses_silhouette": 1,
                "section_recess_recognition_refused": 4,
            }
        )

        # #1512's two crossings no longer occur on this fixture. They were the restored 6 mm
        # and 4 mm boss-height witnesses cutting the step chain's `4× 2` repeat label, and
        # quiddity 0.2.8 leaves neither on this view: the coalesced 8 | 6 | 4 | 2 chain routes
        # to an enlarged detail, which carries all four as claimed dimensions. Nothing was
        # dropped to achieve that — the detail assertions above account for every step.
        #
        # This is the reproduction disappearing, NOT the placement debt being paid: the
        # bounded same-batch ink solver still never considers the combined cross-pass set,
        # which is what #1512 is actually about. Asserted as absence so a reappearance here
        # fails rather than passing quietly under a laxer check.
        for drawing in (auto, replayed):
            assert [i for i in drawing.lint() if i.code == "annotation_ink_overlap"] == []

        # ── from #881: the Y-step furniture lands in the right views on the replay ──
        assert replayed.view_of("centerline_side") == "side"
        assert replayed.view_of("centerline_plan") is None
        assert "plan" not in replayed.views
        assert not any(n.startswith(("dim_loc_front_", "dim_loc_side_")) for n in replay)
        assert {replayed.view_of(n) for n in replay if n.startswith("m_steplen")} == {"side"}

    @pytest.mark.parametrize(("axis_z", "rotation"), [(0.0, 90), (17.0, 90), (-11.0, -90)])
    def test_issue_892_short_y_step_chain_moves_to_enlarged_side_detail(self, axis_z, rotation):
        # P10-base axial profile: 3 | 5.5 | 3.5 mm along Y.  Centred labels crowd at
        # 1:1, but lifting the middle 5.5 onto a far tier makes it read like an
        # overall dimension. Keep only the 12 mm block on the side view and redraw
        # the three shoulder-to-shoulder links in a genuine enlarged side detail.
        dwg = build_drawing(
            self._issue_892_y_chain(axis_z=axis_z, rotation=rotation),
            scale=1.0,
            scale_policy="permissive",
        )

        main = {n: o for n, o in dwg.iter_annotations() if n.startswith("m_steplen")}
        assert {o.label for o in main.values()} == {"12"}
        assert {dwg.view_of(n) for n in main} == {"side"}
        assert "detail_a" in dwg.views

        detail = {n: o for n, o in dwg.iter_annotations() if n.startswith("dim_detail_a_steplen")}
        assert {o.label for o in detail.values()} == {"3", "5.5", "3.5"}
        assert {dwg.view_of(n) for n in detail} == {"detail_a"}
        assert len({round(o._dw_spec.distance, 6) for o in detail.values()}) == 1
        assert all(dwg.measurement_keys(name) == [] for name in main), (
            "the aggregate block is not any one approved step measurement"
        )
        assert all(len(dwg.measurement_keys(name)) == 1 for name in detail)

        labels = sorted((o.label_bbox for o in detail.values()), key=lambda bb: bb[0])
        assert all(
            left[2] + dwg.draft.pad_around_text <= right[0] + 1e-6
            for left, right in zip(labels, labels[1:])
        )
        marker = dwg.get_annotation("detail_marker_A").bounding_box()
        axis_page_y = dwg.at("side", 0, 0, axis_z)[1]
        assert marker.min.Y <= axis_page_y <= marker.max.Y

    def test_issue_892_toleranced_labels_drive_detail_scale_from_rendered_text(self):
        from draftwright import Sheet

        part = self._issue_892_y_chain(axis_z=9.0)
        sheet = Sheet(part, scale=1.0, page="A2", scale_policy="permissive").auto_dimensions()
        sheet.step(diameter=45, length=3, at=(0, -1.5, 9), axis="y").tolerance(0.2)
        sheet.step(diameter=34, length=5.5, at=(0, -5.75, 9), axis="y").tolerance(0.0, 0.3)
        sheet.step(diameter=28, length=3.5, at=(0, -10.25, 9), axis="y")
        dwg = sheet.build()

        detail = [o for n, o in dwg.iter_annotations() if n.startswith("dim_detail_a_steplen")]
        assert len(detail) == 3
        assert {o._dw_scale for o in detail} == {10.0}
        labels = sorted((o.label_bbox for o in detail), key=lambda bb: bb[0])
        assert all(
            left[2] + dwg.draft.pad_around_text <= right[0] + 1e-6
            for left, right in zip(labels, labels[1:])
        )

    def test_issue_892_clear_labels_but_tight_arrows_still_request_detail(self):
        # Single-digit labels clear one another at 1:1, but the 3/5/4 mm spans
        # cannot contain text plus two inside arrowheads. Outside-arrow tails on
        # the principal chain would intrude into neighbouring links.
        b = Align.MIN
        part = Cylinder(12, 3, align=(Align.CENTER, Align.CENTER, b))
        part += Pos(0, 0, 3) * Cylinder(10, 5, align=(Align.CENTER, Align.CENTER, b))
        part += Pos(0, 0, 8) * Cylinder(8, 4, align=(Align.CENTER, Align.CENTER, b))
        dwg = build_drawing(part.rotate(Axis.X, 90), scale=1.0, scale_policy="permissive")

        assert "detail_a" in dwg.views
        detail = {
            o.label for n, o in dwg.iter_annotations() if n.startswith("dim_detail_a_steplen")
        }
        assert detail == {"3", "5", "4"}

    def test_issue_892_no_detail_room_keeps_block_and_reports_uncovered_shoulders(self):
        # Transactional failure: a deliberately undersized sheet has no rectangle
        # for the enlarged profile. Do not leave half a detail or reinstate the
        # ambiguous stagger; retain the overall block and let coverage lint expose
        # that the interior shoulders are not located.
        dwg = build_drawing(
            self._issue_892_y_chain(),
            scale=1.0,
            page="140x100",
            scale_policy="permissive",
        )
        assert "detail_a" not in dwg.views
        main = [o for n, o in dwg.iter_annotations() if n.startswith("m_steplen")]
        assert [o.label for o in main] == ["12"]
        assert dwg.lint_summary()["by_code"].get("axial_length_missing", 0) == 1

    def test_issue_890_rotated_bolt_pattern_selects_clear_diameter_rays(self):
        self._assert_y_diameter_leaders_clear_holes(
            build_drawing(self._issue_890_cardinal_hole_flange())
        )

    def test_issue_881_y_axis_steps_replay_through_deferred_intents(self):
        dwg = build_drawing(self._issue_881_y_step_flange(), auto_dims=False)
        steps = [f for f in dwg.model().features if f.kind == "step"]

        with dwg.deferred():
            for feature in steps:
                dwg.callout(feature)
                dwg.dimension(feature, "length", role="step")

        assert {dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_dia_y")}
        assert {
            dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_steplen")
        }
        assert dwg.view_of("centerline_side") == "side"
        assert dwg.view_of("centerline_plan") == "plan"

    def test_each_external_diameter_gets_a_callout(self, x_shaft_dwg):
        dwg = x_shaft_dwg
        labels = {o.label for n, o in dwg.iter_annotations() if n.startswith("m_dia")}
        assert "ø30" in labels
        assert "ø16" in labels

    def test_no_feature_not_dimensioned_left(self, x_shaft_dwg):
        # The whole point: the external diameters no longer lint as uncovered.
        dwg = x_shaft_dwg
        codes = dwg.lint_summary()["by_code"]
        assert codes.get("feature_not_dimensioned", 0) == 0

    def test_callouts_are_leaders_on_the_constraint_solver(self, x_shaft_dwg):
        # Placed via _solve_strip_ys (ADR 2 (was 0003) layer-2), so two distinct
        # diameters never share an x and never collide: label xs are min_gap
        # apart and inside the front view's page bounds.
        dwg = x_shaft_dwg
        leaders = [o for n, o in dwg.iter_annotations() if n.startswith("m_dia")]
        assert len(leaders) >= 2
        xs = sorted(ldr.elbow[0] for ldr in leaders)
        assert all(b - a > 1.0 for a, b in zip(xs, xs[1:]))  # spread, not stacked

    def test_z_rotational_part_is_untouched(self):
        # A plain Z disc's OD is covered by dim_od (rotational), so render_diameters
        # skips it (already mentioned) — no m_dia callouts appear.
        dwg = build_drawing(Cylinder(15, 40))  # plain Z disc/shaft
        assert not any(n.startswith("m_dia") for n in dwg.annotations())

    def test_horizontal_round_body_od_on_profile(self):
        # A horizontal (X-axis) single-OD cylinder shows its OD as a clean profile-view
        # diameter dim (dim_od) — not an end-on/corner boss leader — with the envelope
        # dims that duplicate the OD suppressed, so no double-dimensioning (#222).
        from build123d import Rot

        for rot, axis in ((Rot(0, 90, 0), "x"), (Rot(90, 0, 0), "y")):
            dwg = build_drawing(rot * Cylinder(25, 40), number="X")
            assert dwg._analysis.od_axis == axis
            assert "dim_od" in dwg.annotations(), f"{axis}: OD not on profile"
            assert not any(n.startswith("m_dia") for n in dwg.annotations()), (
                f"{axis}: end-on leader"
            )
            # the OD (50) appears once (ø50), not also as a bare envelope "50"
            labels = [str(o.label) for _, o in dwg.iter_annotations() if getattr(o, "label", None)]
            assert "50" not in labels, f"{axis}: OD double-dimensioned as envelope"
            assert [i for i in dwg.lint() if i.severity != "info"] == []

    def test_unfittable_row_recovers_diameters_without_crashing(self, monkeypatch):
        # A failed row must reach the shared leader solve without crashing on
        # a None unpack. Both physical diameter requirements remain covered.
        import sys

        # render_diameters looks the strip solvers up in its own module's namespace
        # (annotations.from_model) — patch them there.
        m = sys.modules["draftwright.annotations.from_model"]
        monkeypatch.setattr(m, "_solve_strip_ys", lambda *a, **k: None)
        monkeypatch.setattr(m, "_greedy_strip_ys", lambda *a, **k: None)
        dwg = build_drawing(_x_stepped_shaft())  # must not raise
        marks = [(name, item) for name, item in dwg.iter_annotations() if name.startswith("m_dia")]
        assert {item.label for _, item in marks} == {"ø30", "ø16"}
        assert all(dwg.registry.measurement_of(name) for name, _ in marks)
        assert not [issue for issue in dwg.lint() if issue.code == "feature_not_dimensioned"]

    def test_nested_band_under_silhouette_gets_a_callout(self):
        # #298: a narrow ø6 external band sits under the ø30 flange silhouette, so
        # recognise_turned_steps' local_od max() reads it as ø30 and it never becomes a step
        # diameter. detect.py now emits the missed band as a boss, so it still gets a ø
        # callout (matching the feature_diameters coverage inventory) and the part lints
        # clean. The overall part is large enough for all three callouts to fit the row.
        from build123d import Align

        def cyl(r, h, z):
            return Pos(0, 0, z) * Cylinder(r, h, align=(Align.CENTER, Align.CENTER, Align.MIN))

        part = Rotation(0, 90, 0) * (cyl(3, 0.5, 0.0) + cyl(15, 20, 0.5) + cyl(10, 15, 20.5))
        dwg = build_drawing(part)
        labels = {o.label for n, o in dwg.iter_annotations() if n.startswith("m_dia")}
        assert {"ø6", "ø30", "ø20"} <= labels  # the nested ø6 is now called out
        assert dwg.lint_summary()["by_code"].get("feature_not_dimensioned", 0) == 0

    def test_diameter_row_places_what_fits_not_all_or_nothing(self):
        # #298/#1505: a partial row retains the ODs that fit and sends the
        # remaining band to the shared leader solve. No measurement disappears
        # merely because the first presentation ran out of capacity.
        from build123d import Align

        def cyl(r, h, z):
            return Pos(0, 0, z) * Cylinder(r, h, align=(Align.CENTER, Align.CENTER, Align.MIN))

        # A ~4 mm shaft: ø6 tip, ø10 flange, ø8 body — three callouts won't fit the row.
        part = Rotation(0, 90, 0) * (cyl(3, 0.5, 0.0) + cyl(5, 1.7, 0.5) + cyl(4, 2.0, 2.2))
        dwg = build_drawing(part)
        labels = {o.label for n, o in dwg.iter_annotations() if n.startswith("m_dia")}
        assert {"ø10", "ø8", "ø6"} <= labels
        assert not [issue for issue in dwg.lint() if issue.code == "feature_not_dimensioned"]
        for feature in dwg.model().features:
            if feature.kind not in {"step", "boss"}:
                continue
            matches = [
                name
                for name, _ in dwg.iter_annotations()
                for identity in dwg.registry.measurement_of(name)
                if identity.feature is feature and identity.parameter.endswith(".diameter")
            ]
            assert len(matches) == 1, "each physical diameter has exactly one owning mark"

    def test_leader_tip_on_the_edge_centred_on_the_feature_length(self, x_shaft_dwg):
        # The ø leader lands on the step's silhouette EDGE — a full radius off the
        # turning axis, not on it (an arrow floating on the centre line reads
        # wrong) — and is CENTRED along the feature's length, not at a step
        # corner (a boss/free-end anchor would otherwise put it on an end face).
        dwg = x_shaft_dwg
        fb = dwg.view_bounds("front")
        axis_y = (fb[1] + fb[3]) / 2
        by_dia = {
            f.diameter: f
            for f in dwg.model().features
            if getattr(f, "diameter", None) and getattr(f, "frame", None) and f.frame.axis == "x"
        }
        tips = [
            (o, float(str(o.label)[1:]))
            for n, o in dwg.iter_annotations()
            if n.startswith("m_dia")
        ]
        assert len(tips) >= 2
        for ldr, dia in tips:
            # radial: on the bottom edge (one radius below the axis), NOT on it
            assert abs((axis_y - ldr.tip[1]) - dwg.scale * dia / 2) < 1e-6, (
                f"{ldr.label} off the edge"
            )
            # axial: at the mid-length of the feature(s) sharing this diameter
            ends = [e[0] for e in by_dia[dia].span]
            exp_x = dwg.at("front", (min(ends) + max(ends)) / 2, 0, 0)[0]
            assert abs(ldr.tip[0] - exp_x) < 1e-6, f"{ldr.label} not centred on its length"

    def test_boss_leader_keeps_its_frame_origin_for_script_parity(self):
        # Only STEP diameters are centred on their span. A boss is re-synthesised
        # WITHOUT a declared span in the emitted-Sheet path, so — unlike a step —
        # its ø leader must anchor at the frame origin (which round-trips) rather
        # than a span mid, or the direct and scripted builds diverge (#707).
        # (nested ø6 boss under the ø30 silhouette.)
        from build123d import Align

        def cyl(r, h, z):
            return Pos(0, 0, z) * Cylinder(r, h, align=(Align.CENTER, Align.CENTER, Align.MIN))

        dwg = build_drawing(
            Rotation(0, 90, 0) * (cyl(3, 0.5, 0.0) + cyl(15, 20, 0.5) + cyl(10, 15, 20.5))
        )
        boss = next(
            f
            for f in dwg.model().features
            if getattr(f, "diameter", None) == 6.0 and f.frame.axis == "x"
        )
        origin_x = dwg.at("front", boss.frame.origin[0], 0, 0)[0]
        tip_x = next(
            o.tip[0] for n, o in dwg.iter_annotations() if str(getattr(o, "label", "")) == "ø6"
        )
        assert abs(tip_x - origin_x) < 1e-6, "ø6 boss leader should anchor at its frame origin"

    def test_equal_diameter_leaders_keep_each_disjoint_runs_own_support(self):
        # The same diameter on two disjoint, unequal runs is independently
        # editable. Each arrow must land on its own band's midpoint, never
        # the convex-hull midpoint in the intervening larger-diameter band.
        part = (
            Cylinder(10, 5)
            + Pos(0, 0, 7.5) * Cylinder(15, 10)
            + Pos(0, 0, 22.5) * Cylinder(10, 20)
        )
        dwg = build_drawing(part, number="X")
        marks = [
            (name, item)
            for name, item in dwg.iter_annotations()
            if str(getattr(item, "label", "")) == "ø20"
        ]
        assert len(marks) == 2
        on_long = dwg.at("front", 0, 0, 22.5)[1]
        on_short = dwg.at("front", 0, 0, 0)[1]
        in_gap = dwg.at("front", 0, 0, 7.5)[1]
        assert sorted(item.tip[1] for _, item in marks) == pytest.approx(
            sorted((on_short, on_long))
        )
        for name, item in marks:
            owner = dwg.registry.feature_of(name)
            assert owner is not None
            assert item.tip[1] == pytest.approx(dwg.at("front", *owner.frame.origin)[1])
            assert abs(item.tip[1] - in_gap) > 1e-6

    def test_z_column_leader_lands_on_the_left_edge(self):
        # Cover the Z-turned column placer too (mirror of the X row): its tips sit
        # a radius to the LEFT of the axis, on the silhouette, not on the axis.
        dwg = build_drawing(Cylinder(15, 40) + Pos(0, 0, 35) * Cylinder(10, 30))
        fb = dwg.view_bounds("front")
        axis_x = (fb[0] + fb[2]) / 2
        tips = [
            (o, float(str(o.label)[1:]))
            for n, o in dwg.iter_annotations()
            if n.startswith("m_dia")
        ]
        assert tips, "expected a Z-column ø callout"
        for ldr, dia in tips:
            assert abs((axis_x - ldr.tip[0]) - dwg.scale * dia / 2) < 1e-6, (
                f"{ldr.label} off the left edge"
            )
