"""Optional section scale recovery and a shared authored/generated datum ladder."""

from types import SimpleNamespace

from draftwright._core import Strip
from draftwright.annotations._axial_render import _draw_step_chain, _StepChainSegment
from draftwright.annotations._common import PlacementContext
from draftwright.annotations._pmi_dimensions import _pmi_dim_spec
from draftwright.builder import _AutomaticResolution


def test_vertical_baseline_steps_and_imported_pmi_share_span_order():
    draft = SimpleNamespace(font_size=3.0, pad_around_text=1.0)
    strip = Strip(anchor=105.0, outer_limit=160.0)
    drawing = SimpleNamespace(
        draft=draft,
        _analysis=SimpleNamespace(fv_zones=SimpleNamespace(right=strip)),
        view_bounds=lambda _view: (80.0, 50.0, 103.0, 160.0),
    )
    ctx = PlacementContext()
    segments = [
        _StepChainSegment((100.0, 50.0, 0), (100.0, 74.0, 0), 12.0),
        _StepChainSegment((100.0, 50.0, 0), (100.0, 154.0, 0), 52.0),
    ]

    assert (
        _draw_step_chain(drawing, "front", segments, "m_steplen", ctx=ctx, baseline_positions=True)
        == 2
    )
    pmi = _pmi_dim_spec(
        (100.0, 50.0, 0),
        (100.0, 118.0, 0),
        strip,
        "34 ±0.05",
        "pmi_z_0",
        "front",
        "right",
        draft,
    )
    assert pmi is not None
    queued = ctx.corridor_batch[("front", "right")]["cands"]
    orders = [(candidate.name, candidate.order) for candidate in queued]
    orders.append((pmi["name"], pmi["order"]))
    assert [name for name, _order in sorted(orders, key=lambda item: item[1])] == [
        "m_steplen0",
        "pmi_z_0",
        "m_steplen1",
    ]


def test_dropped_optional_section_can_recover_next_scale_without_larger_sheet(monkeypatch):
    import draftwright.builder as builder

    analysis = SimpleNamespace(
        layout_section=1,
        x_size=24.0,
        y_size=24.0,
        z_size=52.0,
        TB_W=150.0,
        layout_strips=None,
        layout_n_steps=2,
        layout_table_sizes=(),
        layout_required_tables=(),
        title_block_margins=None,
        margin=5.0,
        planned_iso=True,
        planned_iso_scale=None,
        projection_convention="third",
    )
    context = SimpleNamespace(
        latest_analysis=analysis,
        placement_issues=lambda _drawing: (SimpleNamespace(code="section_dropped"),),
    )
    recovery = _AutomaticResolution(context=context, views_are_automatic=True)
    recovery.dimensions_are_automatic = True
    recovery.drawing = SimpleNamespace(scale=1.0)
    recovery.original_page = (420.0, 297.0)
    recovery.settled_principal_views = ("front", "plan", "side")
    calls = []
    winner = SimpleNamespace(scale=2.0)
    recovery.trials = SimpleNamespace(
        try_scales_on_selected_page=lambda scales, **kwargs: (
            calls.append((scales, kwargs)) or winner,
            (),
        )
    )
    monkeypatch.setattr(
        builder, "_layout_geometry", lambda *args, **kwargs: SimpleNamespace(auto_fits=True)
    )

    recovery.recover_scale_from_dropped_section()

    assert recovery.drawing is winner
    assert recovery.replanned
    assert calls == [
        (
            (2.0,),
            {
                "reason": "optional_section_scale_recovery",
                "require_axial_coverage": False,
            },
        )
    ]

    # An optional section is not a license to try an enlarged principal layout
    # that still cannot fit after its reservation is removed.
    recovery.drawing = SimpleNamespace(scale=1.0)
    recovery.replanned = False
    monkeypatch.setattr(
        builder,
        "_layout_geometry",
        lambda *args, **kwargs: SimpleNamespace(auto_fits=False),
    )
    recovery.recover_scale_from_dropped_section()
    assert recovery.drawing.scale == 1.0
    assert not recovery.replanned
    assert len(calls) == 1
