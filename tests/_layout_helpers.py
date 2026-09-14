"""Shared layout-test helpers."""


def _sizing_model(part):
    """Return the sizing IR and planned bore-callout width for *part*."""
    from build123d_drafting.helpers import draft_preset

    from draftwright._core import _FONT_SIZE
    from draftwright.annotations.orchestrator import build_model
    from draftwright.compose import _est_planned_bore_callout_width
    from draftwright import build_drawing
    from draftwright.model import plan_dimensions

    model = build_model(build_drawing(part, number="X")._analysis)
    draft = draft_preset(font_size=_FONT_SIZE, decimal_precision=1)
    width = _est_planned_bore_callout_width(
        plan_dimensions(model), draft, font_size=_FONT_SIZE, pad_around_text=draft.pad_around_text
    )
    return model, width
