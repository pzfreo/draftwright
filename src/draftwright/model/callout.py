"""The compound hole-callout specification — one reading of a plan, two consumers.

`⌀20 THRU ⌴ ⌀32 ↓ 1.5` is assembled twice in this engine: once by the renderer that draws it,
and once by the page/scale estimator that must reserve the right width for it before any
geometry exists (#261's estimator/render agreement). Those were separate implementations, and
they drifted — the estimator still inferred `THRU` from a missing depth parameter after #868
fixed that inference in the renderer, and it ignored `suppressed` entirely after #875 taught
the renderer to honour it. A callout could therefore be reserved 33 mm of strip and rendered
at 14 mm.

So the reading lives here, in the IR waist, below both consumers (`compose` ranks 2,
`annotations` ranks 4, `model` ranks 0). The renderer turns the spec into a `HoleCallout`; the
estimator turns the same spec into token widths. Neither re-derives it.

The governing rule (ADR 0016): **no renderer may infer an engineering fact from the presence or
absence of a dimension parameter.** Parameters carry values for display; facts live on the
feature. `through` is read off the feature for exactly this reason.
"""

from __future__ import annotations

from draftwright._geometry import _fmt
from draftwright.model.ir import HoleFeature, PatternFeature
from draftwright.model.planner import DimensionGroup


def _planned(group: DimensionGroup, kind: str, *roles: str):
    """First PLANNED dimension matching *kind* and any of *roles*, in role order — suppressed
    or not. The lookup suppression is decided against; :func:`_first` is what honours it."""
    for role in roles:
        for pd in group.dims:
            if pd.param.kind == kind and pd.param.role == role:
                return pd
    return None


def _first(group: DimensionGroup, kind: str, *roles: str) -> float | None:
    """First **unsuppressed** parameter value matching *kind* and any of *roles*, in role order.

    Honouring ``suppressed`` here is ADR 0016 / #875. Thirteen render sites already skipped
    suppressed dimensions; the compound-callout path did not, so a suppressed segment still
    printed. Suppression MARKS a dimension rather than removing it (the group keeps its
    engineering data either way) — what changes is whether it reaches the page."""
    pd = _planned(group, kind, *roles)
    return None if pd is None or pd.suppressed else pd.param.value


#: The compound callout's segments, each as the (kind, role) parameters that make it readable.
#: A segment is ATOMIC: `⌴ ⌀32 ↓ 1.5` needs both terms, and half of it is not a shorter callout,
#: it is a different and wrong one. The bore is listed first because it also heads the string.
_CALLOUT_SEGMENTS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("bore", (("diameter", "bore"),)),
    ("counterbore", (("diameter", "counterbore"), ("depth", "counterbore"))),
    ("spotface", (("diameter", "spotface"), ("depth", "spotface"))),
    ("countersink", (("diameter", "countersink"), ("angle", "countersink"))),
)


def _segment_state(group: DimensionGroup, params) -> tuple[list[str], list[str]]:
    """(present-and-printing, present-but-suppressed) for one segment's parameters."""
    printing: list[str] = []
    suppressed: list[str] = []
    for kind, role in params:
        pd = _planned(group, kind, role)
        if pd is None:
            continue
        (suppressed if pd.suppressed else printing).append(f"{role}.{kind}")
    return printing, suppressed


def _refuse_headless_callout(group: DimensionGroup, bore_pd=None) -> None:
    """Raise if suppression would leave part of a compound callout orphaned (ADR 0016 / #875).

    Two ways that happens, and they are the same rule at two scales:

    - **Within a segment.** Suppressing `counterbore.diameter` while its depth survives leaves
      a depth with nothing to be the depth OF, and the renderer would quietly drop both.
    - **Across segments.** The bore ⌀ heads the string, so suppressing it while a counterbore or
      countersink segment prints leaves those with no callout to attach to.

    Raising is the only option that keeps the author in control. Lint-and-drop silently discards
    intent they expressed; implicitly restoring the missing term makes the drawing say something
    the script does not. Suppressing a whole segment — or the whole callout — is coherent and
    stays silent, because nothing is orphaned.

    The first version of this check only guarded the bore head, which left the within-segment
    case doing exactly the silent discard the rule exists to forbid (PR #920 review).
    """
    for name, params in _CALLOUT_SEGMENTS:
        printing, suppressed = _segment_state(group, params)
        if suppressed and printing:
            raise ValueError(
                f"suppressing {', '.join(suppressed)} would leave {', '.join(printing)} with "
                f"nothing to attach to — a {name} reads as one term. Suppress the whole segment, "
                "or keep it whole."
            )

    bore_printing, bore_suppressed = _segment_state(group, (("diameter", "bore"),))
    if not bore_suppressed or bore_printing:
        return
    orphans = [
        label
        for _name, params in _CALLOUT_SEGMENTS[1:]
        for label in _segment_state(group, params)[0]
    ]
    if not orphans:
        return  # the whole callout is suppressed — coherent, and nothing to print
    raise ValueError(
        f"suppressing the bore diameter would leave {', '.join(orphans)} with no callout to "
        "head. A compound callout reads as one string, so its leading ⌀ is a dependency: "
        "suppress those segments too, or keep the bore ⌀."
    )


def hole_callout_spec(group: DimensionGroup) -> dict | None:
    """A hole/pattern group's plan → `HoleCallout` kwargs, mirroring the engine's
    convention. ``None`` if not a hole-bearing callout.

    From the plan: bore from `DimParameter` roles; the cbore/spotface *step* with
    counterbore precedence (``step = cbore or spotface``, as the engine does);
    ``count`` and the pattern *suffix* (``EQ SP ON ø50 BC`` / ``(3×3)``) from the
    source feature.

    ``through`` is read off the FEATURE, never inferred from a missing bore-depth
    param (#868). `HoleFeature.parameters()` only emits the depth for a blind hole,
    so absence-as-signal would make any consumer that filters the parameter list —
    ADR 0016 suppression above all — silently render a blind hole as ``THRU``. The
    rule that follows (ADR 0016): a renderer may not infer an engineering *fact*
    from the presence or absence of a dimension parameter — parameters carry values
    for display, facts live on the feature."""
    feat = group.feature
    if not isinstance(feat, HoleFeature | PatternFeature):
        return None
    _refuse_headless_callout(group)  # every segment, not just the head — see the docstring
    bore_pd = _planned(group, "diameter", "bore")
    bore = _first(group, "diameter", "bore")
    if bore is None:
        return None
    bore_tol = bore_pd.param.tolerance if bore_pd is not None else None
    depth = _first(group, "depth", "bore")
    count = feat.count
    suffix = None
    if isinstance(feat, PatternFeature):
        if feat.pattern == "bolt_circle" and feat.bcd is not None:
            suffix = f"EQ SP ON ø{_fmt(feat.bcd)} BC"
        elif feat.pattern == "grid" and feat.rows and feat.cols:
            suffix = f"({feat.rows}×{feat.cols})"
    # A thread spec (#764) folds onto the compound callout — it lives on the bore hole
    # (the pattern's member for a threaded array). Lead with it (the tap/thread is the
    # defining call), then any pattern suffix: e.g. "M3x0.5" or "M3x0.5 EQ SP ON ø50 BC".
    hole = feat.member if isinstance(feat, PatternFeature) else feat
    thread = getattr(hole, "thread", None)
    suffix = " ".join(p for p in (thread, suffix) if p) or None
    return {
        "diameter": bore,
        "count": count if count and count > 1 else None,
        "through": hole.through,  # the feature's fact, not the param list's shape (#868)
        "depth": depth,
        # counterbore precedence, spotface fallback — the engine's mapping
        "cbore_dia": _first(group, "diameter", "counterbore", "spotface"),
        "cbore_depth": _first(group, "depth", "counterbore", "spotface"),
        "csink_dia": _first(group, "diameter", "countersink"),
        "csink_angle": _first(group, "angle", "countersink"),
        "suffix": suffix,
        "tolerance": bore_tol,  # P2a: ± on the bore ⌀, baked into the callout string below
    }
