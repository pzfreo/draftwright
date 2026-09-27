"""The profile recommendation uses only pre-render scheme and sheet facts."""

from dataclasses import replace

from draftwright.compose import (
    AnnotationSchemeShadowReport,
    CorridorDepthComparison,
    StripDepths,
)
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

    assert choice["version"] == 5
    assert choice["profile"] == "planned"
    assert choice["page"] == [297.0, 210.0]
    assert choice["scale"] == 1.0


def test_sparse_authored_corridor_keeps_uncapped_strips():
    strips = _strips()
    demand = replace(strips.scheme.demands[0], family="authored_dimension")
    strips.scheme = AnnotationScheme((demand,), ())
    choice = choose_pre_render_profile(
        strips,
        strips.annotation_scheme_shadow_report(1.0),
        page=(297.0, 210.0),
        views=("front",),
        auto_dims=True,
    )

    assert choice["profile"] == "iso-growth"
    assert choice["reason"] == "sparse_authored_corridor"


def test_sparse_authored_corridor_with_unplanned_demand_keeps_legacy_depth():
    strips = _strips(unplanned=True)
    demand = replace(strips.scheme.demands[0], family="authored_dimension")
    strips.scheme = AnnotationScheme((demand,), strips.scheme.unplanned)
    choice = choose_pre_render_profile(
        strips,
        strips.annotation_scheme_shadow_report(1.0),
        page=(297.0, 210.0),
        views=("front",),
        auto_dims=True,
    )

    assert choice["profile"] == "legacy-depth"
    assert choice["reason"] == "unplanned_or_under_reserved_demand"


def test_default_sparse_authored_measurement_keeps_its_requested_corridor():
    from build123d import Box

    from draftwright import Sheet

    sheet = Sheet(Box(40, 20, 10)).authored_dimensions()
    sheet.measured_dimension(
        kind="linear",
        value=10,
        label="10",
        dominant_axis="Z",
        ref_bbox=(-10, -5, 0, 10, 5, 10),
        ref_pts=[(0, 0, 0), (0, 0, 10)],
        view="front",
        side="left",
    )
    drawing = sheet.build()

    assert drawing.annotation_scheme_decision["pre_render_choice"]["profile"] == "iso-growth"
    mark = drawing.get_annotation("pmi_z_0")
    assert mark is not None and mark._dw_spec.side == "left"
    assert not [issue for issue in drawing.lint() if issue.code == "pmi_dropped"]


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


def test_dense_unplanned_demand_chooses_columns():
    strips = _strips(unplanned=True)
    demand = strips.scheme.demands[0]
    strips.scheme = AnnotationScheme((demand,) * 60, (strips.scheme.unplanned[0],) * 25)
    shadow = AnnotationSchemeShadowReport(
        1.0,
        tuple(
            CorridorDepthComparison("front", side, 12.0, 10.0)
            for side in ("left", "right", "above")
        ),
        25,
    )
    choice = choose_pre_render_profile(
        strips,
        shadow,
        page=(594.0, 420.0),
        views=("front",),
        auto_dims=True,
    )

    assert choice["profile"] == "columns"
    assert choice["reason"] == "dense_unplanned_corridors"


def test_unbounded_unplanned_tail_keeps_uncapped_reservations():
    strips = _strips(unplanned=True)
    demand = strips.scheme.demands[0]
    strips.scheme = AnnotationScheme((demand,) * 24, (strips.scheme.unplanned[0],) * 6)
    choice = choose_pre_render_profile(
        strips,
        strips.annotation_scheme_shadow_report(1.0),
        page=(297.0, 210.0),
        views=("front",),
        auto_dims=True,
    )

    assert choice["profile"] == "legacy-depth"
    assert choice["reason"] == "unplanned_or_under_reserved_demand"


def test_sparse_under_reserved_demand_chooses_iso_growth():
    strips = _strips()
    shadow = AnnotationSchemeShadowReport(
        1.0,
        (CorridorDepthComparison("front", "right", 12.0, 10.0),),
        0,
    )
    choice = choose_pre_render_profile(
        strips,
        shadow,
        page=(297.0, 210.0),
        views=("front",),
        auto_dims=True,
    )

    assert choice["profile"] == "iso-growth"
    assert choice["reason"] == "bounded_sparse_demand"


def test_typed_corridor_pressure_chooses_planned_without_large_unplanned_tail():
    strips = _strips(unplanned=True)
    demand = strips.scheme.demands[0]
    strips.scheme = AnnotationScheme((demand,) * 38, (strips.scheme.unplanned[0],) * 5)
    shadow = AnnotationSchemeShadowReport(
        1.0,
        tuple(
            CorridorDepthComparison("front", side, 12.0, 10.0)
            for side in ("left", "right", "above", "below")
        ),
        5,
    )

    choice = choose_pre_render_profile(
        strips, shadow, page=(420.0, 297.0), views=("front",), auto_dims=True
    )

    assert choice["profile"] == "planned"
    assert choice["reason"] == "typed_corridor_pressure"


def test_medium_typed_pressure_allows_a_bounded_unplanned_fraction():
    strips = _strips(unplanned=True)
    demand = strips.scheme.demands[0]
    gap = strips.scheme.unplanned[0]
    strips.scheme = AnnotationScheme((demand,) * 38, (gap,) * 7)
    shadow = AnnotationSchemeShadowReport(
        1.0,
        tuple(
            CorridorDepthComparison("front", side, 12.0, 10.0)
            for side in ("left", "right", "above", "below")
        ),
        7,
    )

    choice = choose_pre_render_profile(
        strips, shadow, page=(420.0, 297.0), views=("front",), auto_dims=True
    )

    assert choice["profile"] == "planned"
    assert choice["reason"] == "typed_corridor_pressure"

    strips.scheme = AnnotationScheme((demand,) * 38, (gap,) * 8)
    above_limit = choose_pre_render_profile(
        strips,
        AnnotationSchemeShadowReport(1.0, shadow.corridors, 8),
        page=(420.0, 297.0),
        views=("front",),
        auto_dims=True,
    )
    assert above_limit["profile"] == "legacy-depth"


def test_large_typed_pressure_retains_absolute_unplanned_cap():
    strips = _strips(unplanned=True)
    demand = strips.scheme.demands[0]
    strips.scheme = AnnotationScheme((demand,) * 62, (strips.scheme.unplanned[0],) * 11)
    shadow = AnnotationSchemeShadowReport(
        1.0,
        tuple(
            CorridorDepthComparison("front", side, 12.0, 10.0)
            for side in ("left", "right", "above", "below")
        ),
        11,
    )

    choice = choose_pre_render_profile(
        strips, shadow, page=(420.0, 297.0), views=("front",), auto_dims=True
    )

    assert choice["profile"] == "legacy-depth"
    assert choice["reason"] == "unplanned_or_under_reserved_demand"


def test_bounded_sparse_demand_keeps_annotation_layout_and_grows_iso():
    strips = _strips(unplanned=True)
    demand = strips.scheme.demands[0]
    strips.scheme = AnnotationScheme((demand,) * 24, (strips.scheme.unplanned[0],) * 2)
    shadow = AnnotationSchemeShadowReport(
        1.0,
        tuple(
            CorridorDepthComparison("front", side, 12.0, 10.0)
            for side in ("left", "right", "above")
        ),
        2,
    )

    choice = choose_pre_render_profile(
        strips, shadow, page=(420.0, 297.0), views=("front",), auto_dims=True
    )

    assert choice["profile"] == "iso-growth"
    assert choice["reason"] == "bounded_sparse_demand"


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
