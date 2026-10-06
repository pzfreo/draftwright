"""Optional section scale recovery and a shared authored/generated datum ladder."""

from types import SimpleNamespace

from draftwright._core import Strip
from draftwright.annotations._axial_render import _draw_step_chain, _StepChainSegment
from draftwright.annotations._baseline_step_ladder import register_vertical_baseline_steps
from draftwright.annotations._common import PlacementContext
from draftwright.annotations._pmi_dimensions import _pmi_dim_spec
from draftwright.builder import _AutomaticResolution, _AutomaticScaleTrials
from draftwright.section_scale_recovery import (
    preserves_recognized_requirements,
    recover_dropped_section_scale,
)


def test_vertical_baseline_steps_and_imported_pmi_share_span_order():
    draft = SimpleNamespace(font_size=3.0, pad_around_text=1.0)
    strip = Strip(anchor=105.0, outer_limit=160.0)
    drawing = SimpleNamespace(
        draft=draft,
        view_bounds=lambda _view: (80.0, 50.0, 103.0, 160.0),
    )
    ctx = PlacementContext(analysis=SimpleNamespace(fv_zones=SimpleNamespace(right=strip)))
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


def test_vertical_baseline_callbacks_keep_witnesses_and_report_a_drop(monkeypatch):
    import draftwright.annotations._baseline_step_ladder as ladder

    draft = SimpleNamespace(font_size=3.0, pad_around_text=1.0)
    strip = Strip(anchor=105.0, outer_limit=160.0)
    ctx = PlacementContext(analysis=SimpleNamespace(fv_zones=SimpleNamespace(right=strip)))
    segment = _StepChainSegment((100.0, 74.0, 0), (100.0, 50.0, 0), 12.0, measurements=("step:1",))
    calls = []
    monkeypatch.setattr(ladder, "_dim", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(ladder, "dim_footprint", lambda *args: calls.append((args, {})))
    drawing = SimpleNamespace(draft=draft)
    assert (
        register_vertical_baseline_steps(
            drawing,
            "front",
            [segment],
            ["12"],
            "step",
            0,
            (80.0, 50.0, 103.0, 160.0),
            ctx,
            lambda ids: calls.append(ids),
        )
        == 1
    )
    candidate = ctx.corridor_batch[("front", "right")]["cands"][0]
    candidate.build(110.0)
    candidate.footprint(110.0)
    candidate.on_drop(candidate.name)
    assert calls[0][0][0:4] == ((105.0, 50.0, 0), (105.0, 74.0, 0), "right", 5.0)
    assert calls[1][0][0:4] == calls[0][0][0:4]
    assert calls[2] == ("step:1",)

    no_analysis = PlacementContext()
    assert (
        register_vertical_baseline_steps(
            drawing,
            "front",
            [segment],
            ["12"],
            "step",
            0,
            (80.0, 50.0, 103.0, 160.0),
            no_analysis,
            lambda _ids: None,
        )
        is None
    )


def test_overcrowded_horizontal_steps_drop_with_a_trace_event():
    draft = SimpleNamespace(font_size=3.0, pad_around_text=1.0)
    drawing = SimpleNamespace(draft=draft, view_bounds=lambda _view: (0.0, 0.0, 5.0, 10.0))
    segments = [_StepChainSegment((x, 0.0, 0), (x + 1.0, 0.0, 0), 1.0) for x in (0.0, 1.0, 2.0)]
    event = {"items": []}
    issues = []
    ctx = SimpleNamespace(
        trace=SimpleNamespace(pass_event=lambda *_args, **_kwargs: event),
        record_issue=lambda *args, **kwargs: issues.append((args, kwargs)),
    )
    assert (
        _draw_step_chain(drawing, "front", segments, "dense", ctx=ctx, allow_collapse=False) == 0
    )
    assert event["items"] == [{"name": "dense", "outcome": "dropped", "reason": "too_dense"}]
    assert issues[0][0][1] == "step_dim_dropped"


def test_dropped_optional_section_can_recover_next_scale_without_larger_sheet(monkeypatch):
    import draftwright.section_scale_recovery as recovery_module

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
        recovery_module,
        "_layout_geometry",
        lambda *args, **kwargs: SimpleNamespace(auto_fits=True),
    )

    original = recovery.drawing
    recover_dropped_section_scale(recovery)

    assert recovery.drawing is winner
    assert recovery.replanned
    assert calls == [
        (
            (2.0,),
            {
                "reason": "optional_section_scale_recovery",
                "require_axial_coverage": False,
                "requirement_floor": original,
            },
        )
    ]

    # An optional section is not a license to try an enlarged principal layout
    # that still cannot fit after its reservation is removed.
    recovery.drawing = SimpleNamespace(scale=1.0)
    recovery.replanned = False
    monkeypatch.setattr(
        recovery_module,
        "_layout_geometry",
        lambda *args, **kwargs: SimpleNamespace(auto_fits=False),
    )
    recover_dropped_section_scale(recovery)
    assert recovery.drawing.scale == 1.0
    assert not recovery.replanned
    assert len(calls) == 1


def test_section_scale_trial_rejects_a_newly_missing_recognized_requirement():
    def drawing(scale, states):
        rows = [
            {
                "id": f"requirement:{index}",
                "family": "turned_steps",
                "parameter_id": "step.length",
                "occurrence_ids": [f"turned_steps:{index}"],
                "owner_ids": [f"step:{index}"],
                "state": state,
            }
            for index, state in enumerate(states, start=1)
        ]
        return SimpleNamespace(
            scale=scale,
            page_w=420.0,
            page_h=297.0,
            views={"front": object()},
            report=lambda: {"recognition": {"requirements": rows}},
        )

    original = drawing(1.0, ("placed", "missing"))
    worse = drawing(2.0, ("missing", "missing"))
    equal = drawing(2.0, ("placed", "missing"))
    better = drawing(2.0, ("placed", "placed"))
    assert not preserves_recognized_requirements(original, worse)
    assert preserves_recognized_requirements(original, equal)
    assert preserves_recognized_requirements(original, better)

    attempts = []
    trial = _AutomaticScaleTrials(
        build=lambda *_args, **_kwargs: worse,
        record_attempt=lambda *args, **kwargs: attempts.append((args, kwargs)),
        qualify=lambda *_args, **_kwargs: ((), (), None),
        retain_arrangement=lambda candidate: candidate,
        current_drawing=lambda: original,
        latest_analysis=lambda: None,
        settled_arrangement="columns",
        settled_principal_views=("front",),
        original_page=(420.0, 297.0),
    )
    result, _issues = trial.try_scales_on_selected_page(
        (2.0,),
        reason="optional_section_scale_recovery",
        require_axial_coverage=False,
        requirement_floor=original,
    )
    assert result is None
    assert attempts[-1][1]["rejection"] == "recognized_requirement_regression"


def test_section_scale_recovery_rejects_unavailable_or_changed_source_evidence(monkeypatch):
    import draftwright.section_scale_recovery as recovery_module

    def drawing(rows):
        return SimpleNamespace(report=lambda: {"recognition": {"requirements": rows}})

    valid = {
        "id": "step:1",
        "family": "turned_steps",
        "parameter_id": "step.length",
        "occurrence_ids": ["step:1"],
        "owner_ids": ["shaft"],
        "state": "placed",
    }
    assert not preserves_recognized_requirements(
        SimpleNamespace(report=lambda: {}), drawing([valid])
    )
    assert not preserves_recognized_requirements(drawing({}), drawing([valid]))
    assert not preserves_recognized_requirements(drawing([{}]), drawing([valid]))
    assert not preserves_recognized_requirements(
        drawing([valid]), drawing([{**valid, "id": "step:2"}])
    )

    monkeypatch.setattr(recovery_module, "_SCALES", (1.0, 2.0))
    assert (
        recovery_module.next_scale_without_section(
            SimpleNamespace(), 2.0, (420.0, 297.0), "columns", ("front",)
        )
        is None
    )
    resolution = SimpleNamespace(
        dimensions_are_automatic=True,
        views_are_automatic=True,
        drawing=object(),
        context=SimpleNamespace(
            latest_analysis=None,
            placement_issues=lambda _drawing: [SimpleNamespace(code="section_dropped")],
        ),
    )
    assert recover_dropped_section_scale(resolution) is None
