"""#798 — the leader floor must never lose a callout to its own route preference.

The floor holds an acceptable-but-cutting candidate while it looks a bounded distance
for a clear one. That lookahead is a *preference*: if it expires and every held
candidate then fails to render, the floor resumes the producer stream in first-clear
order rather than dropping the job.

Adversarial review of #1189 found that resume missing — the job was dropped, which is
the one way a callout could be lost for a routing reason. No fixture reaches it: the
floor consumes the UN-expanded producer stream (three candidates for a chamfer, eight
for a pocket), so the lookahead never expires in practice and phase 2 always answers.
It is defensive code for a producer that offers more alternatives than the lookahead,
so it is driven directly here rather than left as the reviewer found it — unreachable
today, wrong the moment it is reached.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from build123d import Box
from build123d_drafting import Leader

from draftwright import build_drawing
from draftwright.annotations import leaders as leaders_module
from draftwright.annotations._common import PlacementContext
from draftwright.annotations.leaders import (
    FeatureLeaderJob,
    collect_feature_leader,
    drain_feature_leaders,
)


def _harness(candidate_count, *, accept=None):
    """A drawing plus a single leader job offering *candidate_count* clear alternatives.

    Every alternative is placed well clear of the view in open margin, so the only thing
    that can stop one landing is the floor's own selection logic — which is what these
    tests are about.
    """
    drawing = build_drawing(Box(40, 30, 8), page="A4", auto_dims=False)
    bounds = drawing.view_bounds("front")
    tip = (bounds[2], (bounds[1] + bounds[3]) / 2.0)
    owner = object()
    candidates = tuple(
        (tip, (tip[0] + 14.0 + 0.35 * step, tip[1], 0.0), owner) for step in range(candidate_count)
    )

    def build(tip_point, elbow, _feature):
        return Leader(tip=(*tip_point, 0), elbow=elbow, label="R1", draft=drawing.draft)

    fallback_accept = (
        None
        if accept is None
        else (lambda candidate, _obstacles, _page, _test=accept: _test(candidate))
    )

    ctx = PlacementContext(
        registry=drawing.registry,
        coverage=drawing.coverage,
        items=drawing.items,
        part_model=drawing.model(),
        feature_leaders=[],
    )
    collect_feature_leader(
        ctx,
        FeatureLeaderJob(
            name="m_fillet0",
            view="front",
            silhouette=bounds,
            label="R1",
            candidates=candidates,
            build=build,
            measurement=(),
            noun="fillet",
            drop_code="fillet_dropped",
            allow_policy_b_fixed=True,
            fallback_accept=fallback_accept,
        ),
    )
    analysis = SimpleNamespace(
        margin=10.0,
        PAGE_W=drawing.page_w,
        PAGE_H=drawing.page_h,
        TB_W=drawing.get_annotation("title_block").bounding_box().size.X,
    )
    return drawing, analysis, ctx


@pytest.fixture
def forced_floor(monkeypatch):
    """Force the resource-cap floor — the path that actually runs on dense parts."""
    monkeypatch.setattr(leaders_module, "_LEADER_ASSIGN_MAX_JOBS", 0)


class TestTheFloorResumesRatherThanDropping:
    def test_a_callout_survives_when_every_held_candidate_fails_to_render(
        self, monkeypatch, forced_floor
    ):
        # Every route is priced as cutting, so phase 1 holds them all and the lookahead
        # expires; every candidate inside that lookahead then fails to render, so phase 2
        # finds nothing. Only the phase-3 resume can still place this. Mutation: delete
        # the resume block and the callout is dropped.
        lookahead = leaders_module._GREEDY_MATERIAL_LOOKAHEAD
        monkeypatch.setattr(leaders_module, "_material_units", lambda *_a, **_k: 1)
        real_materialize = leaders_module._materialize
        cutoff = lookahead + 4

        def flaky(dwg, job, candidate):
            return None if candidate.raw_index < cutoff else real_materialize(dwg, job, candidate)

        monkeypatch.setattr(leaders_module, "_materialize", flaky)
        drawing, analysis, ctx = _harness(cutoff + 6)
        assert drain_feature_leaders(drawing, analysis, ctx) == 1, (
            "the callout was dropped: the floor stopped scanning because it had been "
            "searching for a clear route, which is the one way a dimension may not be lost"
        )
        assert drawing.get_annotation("m_fillet0") is not None

    def test_the_held_fallback_places_the_least_cutting_candidate(self, monkeypatch, forced_floor):
        # Phase 2: nothing clear exists, so the shallowest held cut is taken rather than
        # the job being dropped. Policy B keeps a required callout at a logged cost.
        depths = {}

        def by_index(candidate, _field):
            depths[candidate.raw_index] = 9 - candidate.raw_index
            return 9 - candidate.raw_index

        monkeypatch.setattr(leaders_module, "_material_units", by_index)
        drawing, analysis, ctx = _harness(6)
        assert drain_feature_leaders(drawing, analysis, ctx) == 1
        placed = drawing.get_annotation("m_fillet0")
        # The last candidate is the shallowest cut, so it wins despite being last.
        assert placed.segments[0][1][0] == pytest.approx(
            drawing.view_bounds("front")[2] + 14.0 + 0.35 * 5
        )

    def test_a_clear_route_still_wins_immediately(self, forced_floor):
        # The common case is untouched: with real material pricing every alternative here
        # is clear, so the first is taken exactly as the pre-#798 floor took it.
        drawing, analysis, ctx = _harness(6)
        assert drain_feature_leaders(drawing, analysis, ctx) == 1
        assert drawing.get_annotation("m_fillet0").segments[0][1][0] == pytest.approx(
            drawing.view_bounds("front")[2] + 14.0
        )


class TestMaterialIsNeverAnAcceptanceTest:
    def test_pricing_every_route_as_cutting_does_not_drop_the_callout(
        self, monkeypatch, forced_floor
    ):
        # Cardinality is identical whether routes are priced as clear or as cutting. If
        # material ever became an acceptance test rather than a preference, this drops.
        monkeypatch.setattr(leaders_module, "_material_units", lambda *_a, **_k: 0)
        drawing, analysis, ctx = _harness(6)
        assert drain_feature_leaders(drawing, analysis, ctx) == 1
        monkeypatch.setattr(leaders_module, "_material_units", lambda *_a, **_k: 40)
        drawing, analysis, ctx = _harness(6)
        assert drain_feature_leaders(drawing, analysis, ctx) == 1


class TestTheResumeScanAppliesTheSameConstraints:
    """The resume is a continuation of the same scan, not a bypass of it. It must still
    reject a candidate the floor would not accept, and still skip one that fails to
    render — otherwise "never drop a callout" would quietly become "place anything"."""

    def test_the_resume_still_rejects_unacceptable_candidates(self, monkeypatch, forced_floor):
        # Reaching the resume's own reject branch takes a precise shape, so it is spelled
        # out: candidates up to the lookahead are ACCEPTED (so they are held) but fail to
        # render (so phase 2 is exhausted), and the candidates the resume then meets are
        # refused before it finds one it can take.
        lookahead = leaders_module._GREEDY_MATERIAL_LOOKAHEAD
        refused = range(lookahead + 2, lookahead + 8)
        takeable = lookahead + 8
        monkeypatch.setattr(leaders_module, "_material_units", lambda *_a, **_k: 1)
        real_materialize = leaders_module._materialize

        def flaky(dwg, job, candidate):
            if candidate.raw_index <= lookahead + 1:
                return None  # every held candidate fails, so phase 2 finds nothing
            return real_materialize(dwg, job, candidate)

        monkeypatch.setattr(leaders_module, "_materialize", flaky)
        drawing, analysis, ctx = _harness(
            takeable + 4, accept=lambda candidate: candidate.raw_index not in refused
        )
        assert drain_feature_leaders(drawing, analysis, ctx) == 1
        placed = drawing.get_annotation("m_fillet0")
        assert placed is not None
        # It skipped the refused ones and took the first the accept test allowed.
        assert placed.segments[0][1][0] == pytest.approx(
            drawing.view_bounds("front")[2] + 14.0 + 0.35 * takeable
        )

    def test_a_job_with_no_acceptable_candidate_at_all_is_dropped(self, forced_floor):
        # The other side of the contract: the floor is not obliged to invent a placement.
        # A job whose every candidate is refused drops, with its drop code recorded.
        drawing, analysis, ctx = _harness(6, accept=lambda _candidate: False)
        assert drain_feature_leaders(drawing, analysis, ctx) == 0
        assert drawing.get_annotation("m_fillet0") is None
        assert any(issue.code == "fillet_dropped" for issue in drawing.registry.issues)
