"""Same-sheet scale recovery when a planned optional section did not render."""

from draftwright._core import _SCALES, _analysis_margins
from draftwright.compose import _layout_geometry
from draftwright.reporting import ReportUnavailableError

_PLACED_STATES = frozenset({"placed", "satisfied_by_structured_note"})


def next_scale_without_section(analysis, current_scale, page, arrangement, views):
    larger = sorted(value for value in _SCALES if value > current_scale)
    if not larger:
        return None
    candidate_scale = larger[0]
    page_width, page_height = page
    geometry = _layout_geometry(
        analysis.x_size,
        analysis.y_size,
        analysis.z_size,
        candidate_scale,
        page_width,
        page_height,
        analysis.TB_W,
        analysis.layout_strips,
        analysis.layout_n_steps,
        section=False,
        table_sizes=analysis.layout_table_sizes,
        required_tables=analysis.layout_required_tables,
        warn_no_iso=False,
        title_block_margins=analysis.title_block_margins,
        margin=_analysis_margins(analysis),
        arrangement=arrangement,
        views=views,
        include_iso=analysis.planned_iso,
        iso_scale_factor=analysis.planned_iso_scale,
        convention=analysis.projection_convention,
    )
    return candidate_scale if geometry.auto_fits else None


def preserves_recognized_requirements(original, candidate):
    """Keep every recognized requirement satisfied by the smaller drawing."""
    try:
        original_rows = original.report()["recognition"]["requirements"]
        candidate_rows = candidate.report()["recognition"]["requirements"]
    except (ReportUnavailableError, KeyError, TypeError):
        return False
    if not isinstance(original_rows, list) or not isinstance(candidate_rows, list):
        return False

    def identity(row):
        return (
            row["id"],
            row["family"],
            row["parameter_id"],
            tuple(row["occurrence_ids"]),
            tuple(row["owner_ids"]),
        )

    try:
        previous = {identity(row): row["state"] for row in original_rows}
        proposed = {identity(row): row["state"] for row in candidate_rows}
    except (KeyError, TypeError):
        return False
    if (
        len(previous) != len(original_rows)
        or len(proposed) != len(candidate_rows)
        or previous.keys() != proposed.keys()
    ):
        return False
    return all(
        (state not in _PLACED_STATES or proposed[key] in _PLACED_STATES)
        and (state != "inapplicable" or proposed[key] == "inapplicable")
        for key, state in previous.items()
    )


def recover_dropped_section_scale(resolution):
    """Try one larger same-sheet scale after an optional section actually drops."""
    if not (resolution.dimensions_are_automatic and resolution.views_are_automatic):
        return
    if not any(
        issue.code == "section_dropped"
        for issue in resolution.context.placement_issues(resolution.drawing)
    ):
        return
    analysis = resolution.context.latest_analysis
    if analysis is None or not analysis.layout_section:
        return
    candidate_scale = next_scale_without_section(
        analysis,
        resolution.drawing.scale,
        resolution.original_page,
        resolution.settled_arrangement,
        resolution.settled_principal_views,
    )
    if candidate_scale is None:
        return
    recovered, recovered_issues = resolution.trials.try_scales_on_selected_page(
        (candidate_scale,),
        reason="optional_section_scale_recovery",
        require_axial_coverage=False,
        requirement_floor=resolution.drawing,
    )
    if recovered is not None:
        resolution.drawing = recovered
        resolution.settled_issues = recovered_issues
        resolution.replanned = True
