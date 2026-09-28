"""#1240 — the above strips and the iso view must see each other.

`strip_obstacles` is annotations-only by documented design (views enter
`late_furniture_obstacles`), so strip placement never saw the iso; and `_fit_iso_view`
re-scales the iso AFTER `_auto_annotate` from the compose-time zone, so the fit never saw
annotations. Two blindnesses, one per direction. The right strips have been iso-clamped since
the zone carve; the above strips had only the per-pass `m_locy` special case.

**No natural fixture reaches the collision** — this was hunted hard, in the #1239 review
(forced-fallthrough sweep over six synthetic parts and all ten CTC fixtures) and again when
this was fixed (deep-Y parts, wide-X parts, pinned pages, forced starvation): compose plus
page/scale selection kept the iso zone horizontally separated from every populated above strip
every time. So these tests construct the geometry at the seam instead — a planted iso bbox for
the clamp, a planted obstacle box for the grow cap — and each carries a control proving the
un-fixed behaviour differs, since a sweep that finds nothing cannot (#1216's lesson) be told
apart from a sweep that tests nothing.


Every build here pins ``scale=1.0, scale_policy="permissive"``. The subject is where strip
candidates land under a planted iso bbox, and since #1250 the automatic path answers a
placement drop by searching for a smaller complete scale — which dissolves the fixture rather
than testing it (the clamp assertion failed with "the fixture stopped using it", which its own
message anticipated). Asking for the incomplete drawing explicitly keeps the geometry the test
is about fixed.
"""

from __future__ import annotations

import pytest
from build123d import Box, Cylinder, Pos

from draftwright._core import _iso_bbox
from draftwright.builder import build_drawing
from draftwright.projection import _largest_clear_factor


def _overlap(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _built_with_analysis(part, **kwargs):
    """A built drawing and its `Analysis`, captured at the fit seam.

    NOT `drawing._analysis`: `test_private_test_attr_reads` ratchets test-side reads of
    `Drawing` privates strictly downward, and adding three would have grown the ceiling.
    `_fit_iso_view` is handed the Analysis by the builder anyway, so a spy there gets the same
    object without reaching through the drawing.
    """
    from draftwright import builder as builder_mod

    # These seam tests spy on the historical fit stage specifically. The
    # demand-guided default settles ISO through its pre-render profile instead.
    kwargs.setdefault("annotation_layout", "estimated-strips")
    captured: dict = {}
    real = builder_mod._fit_iso_view

    def spy(dwg, analysis, obstacles=()):
        captured["analysis"] = analysis
        return real(dwg, analysis, obstacles=obstacles)

    builder_mod._fit_iso_view = spy
    try:
        drawing = build_drawing(part, **kwargs)
    finally:
        builder_mod._fit_iso_view = real
    return drawing, captured["analysis"]


def _plate_with_locations():
    """Holes at distinct x/y so location dims populate the plan/side above strips."""
    part = Box(120, 70, 12)
    for x, y in ((-40, 20), (-10, -15), (25, 5), (45, -25)):
        part -= Pos(x, y, 0) * Cylinder(3.5, 40)
    return part


def test_the_clear_factor_search_uses_measured_box_and_actual_scale_issue_1946(monkeypatch):
    """#1946: the origin and the *actual* starting scale govern the box transform.

    The box is measured at factor 0.6, although the growth corridor starts at 0.65. This
    happened on CTC-01 after an earlier zone-fit shrink. A ten-mm half-extent reaches the
    planted obstacle at factor 0.9; using ``lo`` as the denominator reaches it at 0.975.
    A page-centre transform also changes the result because the iso origin is off-centre.
    """
    from draftwright import projection as projection_mod

    drawing, analysis = _built_with_analysis(Box(40, 30, 8), title="T", number="N")
    ox, oy = analysis.ISO_X, analysis.ISO_Y
    assert abs(ox - drawing.page_w / 2) > 10  # wrong page origin is distinguishable
    base = (ox - 10, oy - 10, ox + 10, oy + 10)
    obstacle = (ox - 5, oy + 15, ox + 5, oy + 18)
    assert not _overlap(base, obstacle)
    assert _overlap((ox - 20, oy - 20, ox + 20, oy + 20), obstacle)
    drawing.set_iso_projection_scale(analysis.SCALE * 0.6)
    monkeypatch.setattr(
        projection_mod,
        "_project_iso",
        lambda *_args, **_kwargs: pytest.fail("clearance search reprojected the solid"),
    )

    factor = _largest_clear_factor(drawing, analysis, 1.3, [obstacle], base, lo=0.65)
    assert factor == pytest.approx(0.89375)
    half = 10 * factor / 0.6
    assert not _overlap((ox - half, oy - half, ox + half, oy + half), obstacle)


def test_the_returned_factor_clears_the_obstacle_on_a_real_projection():
    """The same contract end-to-end, with no dependence on how much a part drifts.

    Whatever the platform makes of the projection, the factor the search returns must leave the
    REAL re-projected bbox clear of the obstacle. That is true with or without drift, so unlike
    the synthetic test above it carries no precondition that can go platform-specific.
    """
    from draftwright._core import _iso_bbox as core_iso_bbox
    from draftwright.projection import _project_iso

    drawing, analysis = _built_with_analysis(Cylinder(20, 60), title="T", number="N")
    _project_iso(drawing, analysis, analysis.SCALE)
    base = core_iso_bbox(drawing)
    _project_iso(drawing, analysis, analysis.SCALE * 1.3)
    grown = core_iso_bbox(drawing)
    assert grown[3] > base[3], "precondition: this part's iso does not grow upward at all"

    obstacle = (base[0], grown[3] - 6.0, base[2], grown[3] - 2.0)
    _project_iso(drawing, analysis, analysis.SCALE)  # the search's documented entry state
    factor = _largest_clear_factor(drawing, analysis, 1.3, [obstacle], base)

    _project_iso(drawing, analysis, analysis.SCALE * factor)
    assert not _overlap(core_iso_bbox(drawing), obstacle), (
        f"at the returned factor {factor:.4f} the real bbox {core_iso_bbox(drawing)} still "
        f"hits {obstacle}"
    )


def test_the_clear_factor_search_degenerate_inputs():
    """No obstacles and a non-growing ceiling are returned untouched, without projecting."""
    from draftwright._core import _iso_bbox as core_iso_bbox

    drawing, analysis = _built_with_analysis(Box(40, 30, 8), title="T", number="N")
    base = core_iso_bbox(drawing)
    assert _largest_clear_factor(drawing, analysis, 1.3, [], base) == 1.3
    assert _largest_clear_factor(drawing, analysis, 1.0, [(0.0, 0.0, 1e4, 1e4)], base) == 1.0


def test_iso_growth_is_capped_by_a_planted_annotation_box(monkeypatch):
    """The fit must not grow onto ink the caller reports — and must still grow up to it.

    The box is planted from MEASURED geometry, not guessed: a spy on the fit captures the
    pre-fit bbox, the control build gives the grown one, and the box goes at the midpoint of
    the growth corridor with its y-range straddling the iso centre (so the x onset alone
    governs). The first version of this test planted "just inside the grown corner", which
    OVERLAPPED the pre-fit bbox — the cap correctly refused all growth and the test read
    that as a failure.
    """
    from draftwright import builder as builder_mod
    from draftwright import projection as projection_mod

    part = Box(40, 30, 8) - Pos(-10, 5, 0) * Cylinder(3, 20) - Pos(10, -5, 0) * Cylinder(3, 20)

    prefit: list = []
    real_fit = projection_mod._fit_iso_view

    def spy_fit(dwg, a, obstacles=()):
        prefit.append(_iso_bbox(dwg))
        return real_fit(dwg, a, obstacles=obstacles)

    monkeypatch.setattr(builder_mod, "_fit_iso_view", spy_fit)

    control = build_drawing(part, title="T", number="N", scale=1.0, scale_policy="permissive")
    grown = _iso_bbox(control)
    pre = prefit[0]
    cx, cy = (grown[0] + grown[2]) / 2, (grown[1] + grown[3]) / 2  # scaling preserves centre
    control_factor = (cx - grown[0]) / (cx - pre[0])
    assert control_factor > 1.15, (
        f"precondition: the control iso grew only {control_factor:.2f}x, leaving no corridor "
        "to plant an obstacle in"
    )
    # Midpoint of the growth corridor in factor space, thin, y-straddling the centre.
    fx_target = (1 + control_factor) / 2
    planted_right = cx - fx_target * (cx - pre[0])
    planted = (planted_right - 3.0, cy - 2.0, planted_right, cy + 2.0)
    assert _overlap(planted, grown) and not _overlap(planted, pre), (
        f"precondition: the box {planted} is not strictly inside the growth corridor "
        f"(pre {pre}, grown {grown})"
    )

    real = builder_mod.annotation_ink_obstacles

    def with_planted(dwg, *args, **kwargs):
        return list(real(dwg, *args, **kwargs)) + [planted]

    monkeypatch.setattr(builder_mod, "annotation_ink_obstacles", with_planted)
    prefit.clear()
    capped = build_drawing(part, title="T", number="N", scale=1.0, scale_policy="permissive")
    capped_bb = _iso_bbox(capped)
    assert not _overlap(planted, capped_bb), (
        f"the fit grew onto a reported obstacle: iso={capped_bb} obstacle={planted}"
    )
    assert capped_bb[0] >= planted[2] - 1e-6, (
        f"the iso's left edge {capped_bb[0]:.2f} crossed the obstacle's right edge "
        f"{planted[2]:.2f}"
    )
    # And the cap allowed growth rather than giving up: strictly wider than pre-fit, strictly
    # narrower than the unobstructed control.
    assert capped_bb[0] < prefit[0][0] - 0.25, (
        f"no growth at all (left edge {capped_bb[0]:.2f} vs pre-fit {prefit[0][0]:.2f}) — the "
        "cap is behaving as a veto, or the margins swallowed the corridor"
    )
    assert capped_bb[0] > grown[0], "the cap did not bind — the obstacle was not the constraint"


def test_the_above_strip_is_clamped_below_an_overlapping_iso(monkeypatch):
    """Strip placement must not stack under an iso that horizontally overlaps its view.

    A fake iso bbox is planted directly over the plan view, low enough that the location
    dims' natural stack would enter it (the control asserts they DO without the clamp —
    the defect, reproduced). With the clamp, nothing may be placed inside it.
    """
    from draftwright.annotations import orchestrator as orch
    from draftwright.annotations._common import annotation_obstacle_boxes

    part = _plate_with_locations()

    # Geometry first: where does the plan's above stack sit unclamped?
    base = build_drawing(part, title="T", number="N", scale=1.0, scale_policy="permissive")
    pv = base.view_bounds("plan")
    assert pv is not None
    # The fake iso spans the plan's x-range with its bottom between the natural stack's
    # second and third tiers (measured: tier tops sit ~11/21/30/40 mm above the view top).
    # The clamp (bottom - 4) then admits tier 1 and refuses tier 3+ — so the un-clamped
    # control MUST enter the box (precondition) while a clamped build keeps a live,
    # non-empty strip below it (the last assertion, which a bottom set too low turns into
    # "the clamp emptied the strip", the first version's failure).
    fake = (pv[0] + 5, pv[3] + 24, pv[2] - 5, pv[3] + 70)

    real = orch._iso_bbox

    def fake_iso(dwg):
        return fake

    monkeypatch.setattr(orch, "_iso_bbox", fake_iso)
    clamped = build_drawing(part, title="T", number="N", scale=1.0, scale_policy="permissive")
    monkeypatch.setattr(orch, "_iso_bbox", real)

    def boxes_in(drawing, region):
        hits = []
        for name, obj in drawing.iter_annotations():
            if getattr(obj, "is_sheet_frame", False) or getattr(obj, "is_zone_grid", False):
                continue
            for box in annotation_obstacle_boxes(drawing, obj):
                if _overlap(box, region):
                    hits.append(name)
        return sorted(set(hits))

    # The precondition, on the UN-clamped build: the natural stack enters the fake region —
    # otherwise the clamp assertion below is vacuous.
    assert boxes_in(base, fake), (
        f"precondition: no annotation enters {fake} even without the clamp, so this fixture "
        "does not reach the defect"
    )
    assert not boxes_in(clamped, fake), (
        f"annotations placed under the (planted) iso despite the clamp: {boxes_in(clamped, fake)}"
    )
    # The clamp must not have simply emptied the strip: something still placed below it.
    band = (pv[0], pv[3], pv[2], fake[1])
    assert boxes_in(clamped, band), (
        "nothing placed in the above strip at all — the clamp killed the strip rather than "
        "bounding it, or the fixture stopped using it"
    )


@pytest.mark.slow  # CTC fixture build (#153)
def test_the_iso_no_longer_grows_over_ctc_01s_pocket_position_dim():
    """The one NATURAL case in the corpus, found only by the #1240 review.

    Both hunts for a reproducing fixture reported none, and the PR said so — but they searched
    for *strip* collisions and this is the other direction: on `main`, CTC-01 AP203's iso grows
    over `m_pocket0_pos_long`'s witness lines. It escaped every sweep because
    `view_annotation_overlap` compares projected EDGES, not bboxes, so the drawing linted clean
    while the boxes genuinely overlapped (#1240 review F2).

    Asserted against the whole fixture rather than that one name: any annotation ink inside the
    final iso bbox is the defect, whichever annotation it belongs to.
    """
    from draftwright._geometry import _boxes_overlap
    from draftwright.annotations._common import annotation_obstacle_boxes

    drawing = build_drawing("tests/fixtures/nist_ctc_01_asme1_ap203.stp")
    assert "iso" in drawing.views, "precondition: the fixture has no iso view"
    iso = _iso_bbox(drawing)
    intruders = sorted(
        {
            name
            for name, obj in drawing.iter_annotations()
            if not getattr(obj, "is_sheet_frame", False)
            and not getattr(obj, "is_zone_grid", False)
            for box in annotation_obstacle_boxes(drawing, obj)
            if _boxes_overlap(box, iso)
        }
    )
    assert not intruders, f"the iso grew over placed annotation ink: {intruders}"


def test_the_post_fit_recap_only_ever_tightens(monkeypatch):
    """The builder's re-cap must not hand back space another pass took.

    It mirrors the right-strip re-cap, which restores from a pre-`_auto_annotate` snapshot to
    give back space taken against a transient iso. Transposed literally, that would DISCARD the
    `m_locy` approach-buffer clamp, which is a different constraint (#1240 review F4). The
    above-strip re-cap therefore tightens only.

    The re-cap branch is guarded on the iso x-overlapping the view, which no natural layout
    does — the first version of this test asserted tightening on a fixture where the branch
    never ran, and a mutation replacing `min(current, limit)` with `limit` PASSED. The iso bbox
    is planted at the builder's own binding so the branch executes.
    """
    from draftwright import builder as builder_mod

    part = _plate_with_locations()
    baseline = build_drawing(part, title="T", number="N", scale=1.0, scale_policy="permissive")
    pv = baseline.view_bounds("plan")
    # An iso spanning the plan's x-range, well above it: the re-cap branch's guards both pass.
    fake = (pv[0] + 5, pv[3] + 40, pv[2] - 5, pv[3] + 90)
    planted = pv[3] + 20.0  # tighter than the fake iso's limit (fake[1] - 4)
    assert planted < fake[1] - 4, "precondition: the planted limit is not the tighter one"

    monkeypatch.setattr(builder_mod, "_iso_bbox", lambda dwg: fake)
    real_annotate = builder_mod._auto_annotate

    def clamp_after(dwg, a, **kwargs):
        result = real_annotate(dwg, a, **kwargs)
        a.pv_zones.above.outer_limit = min(a.pv_zones.above.outer_limit, planted)
        return result

    monkeypatch.setattr(builder_mod, "_auto_annotate", clamp_after)
    _drawing, analysis = _built_with_analysis(part, title="T", number="N")
    final = analysis.pv_zones.above.outer_limit
    assert final <= planted, (
        f"the re-cap loosened a limit set during annotation: {final} > {planted} — it is "
        "restoring from the snapshot instead of tightening"
    )


@pytest.mark.parametrize(
    "options",
    [{}, {"frame": True}, {"frame": True, "zones": True}, {"frame": True, "auto_dims": False}],
    ids=["plain", "frame", "frame+zones", "frame+script-path"],
)
def test_the_page_spanning_riders_do_not_neutralise_the_iso_fit(options):
    """`--frame` must not disable the fit (#1240 review r2, F1).

    The sheet frame and zone grid are registered annotations whose Compound bbox spans the
    page — they carry no `.segments`, so the obstacle decomposition falls back to the full
    geometry box. Handing the raw `strip_obstacles` set to the fit therefore made the iso
    overlap an "obstacle" at every factor: `--frame` silently disabled growth and the NTS
    caption with it, on 17 of 20 corpus fixtures, with the whole fast tier green. It also broke
    script/CLI parity, because the `auto_dims=False` path computes its obstacles BEFORE the
    frame is added and so kept growing — the one thing that branch's comment exists to protect.

    `tests/test_iso_nts_caption_placement.py` makes the same point about the same riders for
    the caption; this is the fit's copy of it, and the reason there is now one predicate
    (`is_page_spanning_rider`) rather than a sixth hand-rolled filter.

    All four configurations must agree: the riders are furniture, not obstacles.
    """
    part = Box(40, 30, 8) - Pos(-10, 5, 0) * Cylinder(3, 20) - Pos(10, -5, 0) * Cylinder(3, 20)
    # Keep the candidate covered for automatic annotations. The generated-script
    # path has no automatic demand/profile and must be compared with that same
    # historical layout on both sides of the rider comparison.
    layout = "estimated-strips" if options.get("auto_dims") is False else "demand-guided"
    plain = build_drawing(
        part,
        title="T",
        number="N",
        scale=1.0,
        scale_policy="permissive",
        annotation_layout=layout,
    )
    plain_iso = _iso_bbox(plain)
    # The precondition: this part's iso GROWS, so a veto is observable at all.
    assert "note_iso_nts" in plain.registry.names(), (
        "precondition: the fixture's iso no longer grows, so no configuration can lose the fit"
    )

    drawing = build_drawing(part, title="T", number="N", annotation_layout=layout, **options)
    iso = _iso_bbox(drawing)
    # SIZE, not position: a frame legitimately shifts the layout (measured: the framed iso sits
    # 3 mm lower). What the riders must not do is change how far the fit GROWS.
    plain_size = (plain_iso[2] - plain_iso[0], plain_iso[3] - plain_iso[1])
    size = (iso[2] - iso[0], iso[3] - iso[1])
    assert size == pytest.approx(plain_size, abs=1e-6), (
        f"{options} changed the fitted iso size: {size} vs {plain_size} — a page-spanning "
        "rider is being treated as an obstacle, so the fit is vetoed"
    )
    assert "note_iso_nts" in drawing.registry.names(), f"{options} lost the NTS caption"
