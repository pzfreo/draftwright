"""The profile recommendation uses only pre-render scheme and sheet facts."""

from draftwright.compose import StripDepths
from draftwright.layout_scheme import AnnotationDemand, AnnotationScheme, UnplannedAnnotation
from draftwright.layout_selection import choose_pre_render_profile


def _strips(*, unplanned=False):
    demand = AnnotationDemand(
        identity="width",
        family="dimension",
        view="front",
        side="right",
        feature_index=0,
        model_site=(0.0, 0.0, 0.0),
    )
    gaps = (UnplannedAnnotation("pmi", "pmi", 1, "raw PMI"),) if unplanned else ()
    return StripDepths(
        right=100.0,
        left=100.0,
        scheme=AnnotationScheme((demand,), gaps),
    )


def test_fully_planned_demand_chooses_capped_profile():
    strips = _strips()
    choice = choose_pre_render_profile(
        strips,
        strips.annotation_scheme_shadow_report(1.0),
        page=(297.0, 210.0),
        views=("front",),
        auto_dims=True,
    )

    assert choice["version"] == 1
    assert choice["profile"] == "planned"
    assert choice["page"] == [297.0, 210.0]
    assert choice["scale"] == 1.0


def test_unplanned_demand_keeps_legacy_depth():
    strips = _strips(unplanned=True)
    choice = choose_pre_render_profile(
        strips,
        strips.annotation_scheme_shadow_report(1.0),
        page=(297.0, 210.0),
        views=("front",),
        auto_dims=True,
    )

    assert choice["profile"] == "legacy-depth"
    assert choice["unplanned_count"] == 1


def test_absent_view_cannot_be_solved_by_a_layout_profile():
    strips = _strips()
    choice = choose_pre_render_profile(
        strips,
        strips.annotation_scheme_shadow_report(1.0),
        page=(297.0, 210.0),
        views=("plan",),
        auto_dims=True,
    )

    assert choice["profile"] is None
    assert choice["missing_views"] == ["front"]
