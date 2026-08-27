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
from draftwright.model.ir import HoleFeature, PatternFeature, ThreadRequirement
from draftwright.model.planner import DimensionGroup, DimensionId


def _planned(group: DimensionGroup, kind: str, *roles: str):
    """First PLANNED dimension matching *kind* and any of *roles*, in role order — **suppressed
    or not**. This is the lookup suppression is decided *against*; :func:`_first` is what
    honours it. Keeping the two separate is what makes each call site's intent legible."""
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
    engineering data either way) — what changes is whether it reaches the page.

    Suppression is applied per role, BEFORE the precedence between them. The roles are ordered
    as a fallback chain (counterbore, then spotface), so a suppressed counterbore must fall
    through to an unsuppressed spotface rather than swallowing it — the first draft resolved
    precedence first and then nulled the winner, which silently dropped the spotface (#920
    review)."""
    for role in roles:
        pd = _planned(group, kind, role)
        if pd is not None and not pd.suppressed:
            return None if pd.param.value is None else float(pd.param.value)
    return None


def _display_decimals(group: DimensionGroup, kind: str, *roles: str) -> int | None:
    """Display policy for the same surviving term :func:`_first` selects (#1349)."""
    for role in roles:
        planned = _planned(group, kind, role)
        if planned is not None and not planned.suppressed:
            decimals = planned.display_decimals
            return int(decimals) if decimals is not None else None
    return None


#: The compound callout's segments: ``(name, head, dependents)``.
#:
#: Each segment has a HEAD term — always its ⌀ — and terms that only mean something beside it.
#: The relation is ASYMMETRIC, which two review rounds were needed to pin down:
#:
#: - ``⌴ ⌀32`` is a readable counterbore; its depth is optional detail. Suppressing the depth
#:   while the ⌀ survives is a legitimate authoring choice, not an error.
#: - ``⌴ ↓ 1.5`` is not a counterbore at all. Suppressing the ⌀ while the depth survives leaves
#:   a number with nothing to be the depth OF, and the renderer would silently drop both.
#:
#: The bore behaves identically — ``⌀12`` is fine, ``↓ 8`` alone is not — so its depth is a
#: dependent rather than, as the first version had it, no part of the segment.
_CALLOUT_SEGMENTS: tuple[tuple[str, tuple[str, str], tuple[tuple[str, str], ...]], ...] = (
    (
        "bore",
        ("diameter", "bore"),
        (("depth", "bore"), ("length", "profile_across_flats")),
    ),
    ("counterbore", ("diameter", "counterbore"), (("depth", "counterbore"),)),
    ("spotface", ("diameter", "spotface"), (("depth", "spotface"),)),
    ("countersink", ("diameter", "countersink"), (("angle", "countersink"),)),
)


def _printing(group: DimensionGroup, *params) -> list[str]:
    """Labels for the given (kind, role) parameters that are present and not suppressed."""
    out = []
    for kind, role in params:
        pd = _planned(group, kind, role)
        if pd is not None and not pd.suppressed:
            out.append(f"{role}.{kind}")
    return out


def _is_suppressed(group: DimensionGroup, kind: str, role: str) -> bool:
    pd = _planned(group, kind, role)
    return pd is not None and pd.suppressed


def _pattern_suffix(group: DimensionGroup) -> str | None:
    """The pattern's trailing term — ``EQ SP ON ø50 BC`` or ``(3×3)`` — or ``None``.

    Shared by the spec and the dependency rule, because the rule has to know exactly what WOULD
    print. Listing the multiplier and thread as head-dependents but not this was invisible while
    every fixture used ``count > 1``: with ``count=1`` the multiplier check does not fire, and a
    one-member bolt circle lost its ``EQ SP ON ø50 BC`` in silence (#920 review).

    The BCD is a planned, addressable dimension (``bolt_circle.diameter``), so suppressing it
    stops the suffix printing — the value is a fact off the feature, but WHETHER it prints is
    the parameter's business. The grid's ``(3×3)`` is a count, not a dimension, and has no
    parameter to suppress.
    """
    feat = group.feature
    if not isinstance(feat, PatternFeature):
        return None
    if feat.pattern == "bolt_circle":
        bcd = _first(group, "diameter", "bolt_circle")
        return (
            None
            if bcd is None
            else f"EQ SP ON ø{_fmt(bcd, _display_decimals(group, 'diameter', 'bolt_circle'))} BC"
        )
    if feat.pattern == "grid" and feat.rows and feat.cols:
        return f"({feat.rows}×{feat.cols})"
    return None


def _shadowed(group: DimensionGroup, name: str) -> bool:
    """Is this segment invisible because another one takes precedence over it?

    Only the spotface can be, and only while the counterbore's ⌀ prints — the two share the one
    recess slot in the callout string. A shadowed segment is not rendered before OR after a
    suppression, so suppressing part of it orphans nothing and must not raise: the first version
    validated every role and refused `spotface.diameter` on a hole whose counterbore was intact,
    which is a spurious error about a term the drawing never carried (#920 review).
    """
    if name != "spotface":
        return False
    cbore = _planned(group, "diameter", "counterbore")
    return cbore is not None and not cbore.suppressed


def _refuse_headless_callout(group: DimensionGroup) -> None:
    """Raise if suppression would leave part of a compound callout orphaned (ADR 0016 / #875).

    Two scales of the same rule:

    - **Within a segment.** Suppressing a segment's ⌀ while its depth or angle survives leaves
      a number with nothing to qualify.
    - **Across segments.** The bore ⌀ heads the whole string, so suppressing it while a
      counterbore or countersink segment prints leaves those with nothing to attach to.

    Raising is the only option that keeps the author in control. Lint-and-drop silently discards
    intent they expressed; implicitly restoring the missing term makes the drawing say something
    the script does not. Suppressing a whole segment — or the whole callout — is coherent and
    stays silent, because nothing is orphaned.
    """
    for name, head, dependents in _CALLOUT_SEGMENTS:
        if _shadowed(group, name):
            continue
        if not _is_suppressed(group, *head):
            continue
        orphans = _printing(group, *dependents)
        if orphans:
            raise ValueError(
                f"suppressing {head[1]}.{head[0]} would leave {', '.join(orphans)} with nothing "
                f"to attach to — a {name} reads as one term, led by its ⌀. Suppress the whole "
                "segment, or keep its diameter."
            )

    if not _is_suppressed(group, "diameter", "bore"):
        return

    # Everything the bore ⌀ heads splits in two, and the split is the whole rule.
    #
    # A **dependent** has a `DimParameter` and the drawing prints its value: a counterbore ⌀,
    # a countersink angle, and — the case that made the distinction necessary — a bolt
    # circle's `bolt_circle.diameter`, which renders only as the `EQ SP ON ø50 BC` suffix.
    # Appearing in the suffix is what made the BCD look like a rider; where a term sits in
    # the string is not what classifies it, having a parameter is.
    #
    # A **rider** lives on the FEATURE with no parameter to suppress — the thread spec, the
    # `n×` multiplier, a grid's `(3×3)` — so it survives any amount of parameter suppression
    # and has no existence outside the string (#920 review).
    feat = group.feature
    suffix = _pattern_suffix(group)
    bcd_suffix = (
        suffix if isinstance(feat, PatternFeature) and feat.pattern == "bolt_circle" else None
    )
    # `dependent_labels`, not `dependents`: the segment loop above binds that name at
    # function scope, and reusing it silently retyped the list.
    dependent_labels = [
        label
        for name, head, deps in _CALLOUT_SEGMENTS[1:]
        if not _shadowed(group, name)
        for label in _printing(group, head, *deps)
    ]
    if bcd_suffix:
        dependent_labels.append(f"the pattern suffix {bcd_suffix!r}")

    # Riders are waived for an AUTHORED omission, and only for one. Whether losing a rider in
    # silence is acceptable turns on WHO decided, which is the distinction `Omission.authored`
    # carries: a planner rule dropping a thread spec is the engine quietly discarding
    # manufacturing intent, and #920's refusal stands; an author who omits the bore is
    # declining the string, not orphaning its prefix. Refusing there made a pattern the one
    # feature whose callout could not be omitted at all, so `dimension(pattern, "pitch")`
    # raised instead of drawing a pitch dim (#925 review).
    #
    # A DEPENDENT is never waived. Doing so let an authored set naming `bolt_circle.diameter`
    # and omitting `bore.diameter` produce neither the 50 mm BCD nor a diagnostic — the
    # requested dimension vanished (#925 review).
    riders: list[str] = []
    if not authored_omission_in(group):
        hole = feat.member if isinstance(feat, PatternFeature) else feat
        thread = getattr(hole, "thread", None)
        if thread:
            riders.append(f"the thread spec {thread}")
        # A plain `HoleFeature` may also carry a count — `4× ⌀6 THRU` — so the multiplier is a
        # dependent of the head for both feature kinds, not just for patterns (#920 review).
        multiplier = getattr(feat, "count", 0) or 0
        if multiplier > 1:
            riders.append(f"the {multiplier}× multiplier")
        if suffix and not bcd_suffix:
            riders.append(f"the pattern suffix {suffix!r}")

    # One raise naming EVERYTHING orphaned. Checking dependents first and returning early
    # produced a message listing the BCD while silently omitting the `4×` multiplier beside
    # it — accurate but incomplete, and the author fixes both with the same edit.
    orphans = dependent_labels + riders
    if not orphans:
        return  # the whole callout is suppressed — coherent, and nothing to print
    raise ValueError(
        f"suppressing the bore diameter would leave {', '.join(orphans)} with no callout to "
        "head. A compound callout reads as one string, so its leading ⌀ is a dependency: "
        "suppress those segments too, or keep the bore ⌀."
    )


def _recess_plan(group: DimensionGroup):
    """The winning recess head/depth pair, preserving its planned identities."""
    for role in ("counterbore", "spotface"):
        dia = _planned(group, "diameter", role)
        if dia is not None and not dia.suppressed:
            depth = _planned(group, "depth", role)
            return dia, None if depth is None or depth.suppressed else depth
    return None, None


def _recess(group: DimensionGroup) -> tuple[float | None, float | None]:
    """The counterbore-or-spotface recess as ONE segment: ``(diameter, depth)``.

    ``counterbore`` takes precedence and ``spotface`` is the fallback — but the choice is made
    once, for the segment, and both terms then come from the role that won. Resolving the two
    terms through independent fallbacks (as the first version did) let a suppressed
    ``counterbore.depth`` pair the counterbore's ⌀32 with the spotface's 0.5 depth: a recess
    present on neither feature, and a wrong drawing rather than a missing one.

    A role wins on its HEAD being unsuppressed, matching the asymmetric segment rule — its
    depth may legitimately be suppressed, which yields a ⌀ with no stated depth.
    """
    dia, depth = _recess_plan(group)
    return (
        None if dia is None else float(dia.param.value),
        None if depth is None else float(depth.param.value),
    )


def _callout_measurements(group: DimensionGroup) -> tuple[DimensionId, ...]:
    """Exact planned measurements whose values the compound callout prints.

    This is semantic provenance, not coverage inferred from formatted text. The same
    suppression and recess-precedence decisions used to build the visible specification
    select the identities, so the renderer cannot certify a hidden or shadowed term.
    """

    planned = []

    def add(kind: str, role: str) -> None:
        pd = _planned(group, kind, role)
        if pd is not None and not pd.suppressed:
            planned.append(pd)

    add("diameter", "bore")
    add("depth", "bore")
    add("length", "profile_across_flats")
    recess_dia, recess_depth = _recess_plan(group)
    planned.extend(pd for pd in (recess_dia, recess_depth) if pd is not None)
    add("diameter", "countersink")
    add("angle", "countersink")
    add("diameter", "bolt_circle")
    return tuple(DimensionId(group.feature, pd.param.parameter_id) for pd in planned)


_AUTHORED_OMISSION = "not in the authored dimension set"

# What a `callout()` PRINTS, by feature kind — the parameter kinds whose values make up
# the callout text. A dimension outside this set belongs to some other mark: a pattern's
# `pitch` is a linear dim drawn between members, not a term in the callout, so a pattern
# whose pocket size is omitted has an undrawable callout even though its pitch survives
# (#921 review round 7). Kinds absent here fall back to "any un-suppressed dim will do".
_AUTHORED_OMISSION = "not in the authored dimension set"


def authored_omission_in(group) -> bool:
    """Does *group* have any measurement the AUTHOR left out (ADR 0016 / #876)?

    Only that — deliberately NOT "would the callout draw?". Three attempts at predicting
    the second from a hand-written per-kind table were each wrong for some feature: a
    hole's callout survives losing an optional segment, a pocket's does not survive losing
    a required component, and a pattern's pitch is not a callout term at all (#921 rounds
    6–8). Each renderer owns that rule, and a copy of it here is a parallel representation
    that drifts.

    Since the compiled-plan boundary landed there is no need to predict at all: a renderer
    receives approved entries, so "draws nothing" is observable rather than forecast. What
    the plan still has to say is WHY it drew nothing — an author's omission is recoverable
    by adding a `dimension(...)` line, a planner rule's suppression is not.
    """
    if group is None:
        return False
    dims = getattr(group, "dims", ())
    return any(
        getattr(d, "suppressed", False) and getattr(d, "reason", None) == _AUTHORED_OMISSION
        for d in dims
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
    cbore_dia, cbore_depth = _recess(group)
    # The recess and countersink terms carry their own authored tolerances. Only the bore's
    # was threaded, so `s.hole(...).cbore(...).tolerance(0.05)` — which `_decorated` folds onto
    # EVERY parameter of that kind — printed `⌀8 ±0.1 THRU ⌴ ⌀14`: one ± shown, one silently
    # gone, and a machinist reads the bare ⌀14 as falling under the general block
    # (#1215, #1234 review r7).
    #
    # Through `_recess_plan` rather than a second lookup, so the tolerance comes from the SAME
    # segment that won the counterbore/spotface precedence — resolving them independently is
    # the #920 defect that paired one role's ⌀ with the other's depth.
    recess_dia_pd, recess_depth_pd = _recess_plan(group)
    depth_pd = _planned(group, "depth", "bore")
    csink_dia_pd = _planned(group, "diameter", "countersink")
    csink_angle_pd = _planned(group, "angle", "countersink")

    def _tol_of(planned):
        return planned.param.tolerance if planned is not None else None

    bore_pd = _planned(group, "diameter", "bore")
    bore = _first(group, "diameter", "bore")
    if bore is None:
        return None
    bore_tol = bore_pd.param.tolerance if bore_pd is not None else None
    depth = _first(group, "depth", "bore")
    count = feat.count
    suffix = _pattern_suffix(group)
    # A thread spec (#764) folds onto the compound callout — it lives on the bore hole
    # (the pattern's member for a threaded array). Lead with it (the tap/thread is the
    # defining call), then any pattern suffix: e.g. "M3x0.5" or "M3x0.5 EQ SP ON ø50 BC".
    hole = feat.member if isinstance(feat, PatternFeature) else feat
    thread = getattr(hole, "thread", None)
    thread_source_ids = thread.source_ids if isinstance(thread, ThreadRequirement) else ()
    if isinstance(thread, ThreadRequirement):
        thread = thread.callout_suffix
    profile_suffix = None
    across = None
    if getattr(hole, "profile", None) == "double_d":
        across = _first(group, "length", "profile_across_flats")
        profile_suffix = "DOUBLE-D" + (
            f" {_fmt(across, _display_decimals(group, 'length', 'profile_across_flats'))} A/F"
            if across is not None
            else ""
        )
    suffix = " ".join(p for p in (profile_suffix, thread, suffix) if p) or None
    return {
        "diameter": bore,
        "diameter_decimals": _display_decimals(group, "diameter", "bore"),
        "count": count if count and count > 1 else None,
        "through": hole.through,  # the feature's fact, not the param list's shape (#868)
        "depth": depth,
        "depth_decimals": _display_decimals(group, "depth", "bore"),
        # counterbore precedence, spotface fallback — the engine's mapping
        # ONE role, both terms. Reading ⌀ and depth through independent fallbacks let a
        # drawing pair the counterbore's ⌀32 with the spotface's 0.5 depth — a recess that
        # exists on neither feature (#920 review). The chain picks a segment, not a value.
        "cbore_dia": cbore_dia,
        "cbore_depth": cbore_depth,
        "cbore_dia_decimals": getattr(recess_dia_pd, "display_decimals", None),
        "cbore_depth_decimals": getattr(recess_depth_pd, "display_decimals", None),
        "csink_dia": _first(group, "diameter", "countersink"),
        "csink_angle": _first(group, "angle", "countersink"),
        "csink_dia_decimals": getattr(csink_dia_pd, "display_decimals", None),
        "csink_angle_decimals": getattr(csink_angle_pd, "display_decimals", None),
        "suffix": suffix,
        "tolerance": bore_tol,  # P2a: ± on the bore ⌀, baked into the callout string below
        # ...and one per remaining term, baked in the same way (#1234 review r7).
        # A BLIND hole's own depth tolerance. `callout_from_spec` and `compose.py` were both
        # given readers for this key and the spec never wrote it, so the reader always resolved
        # to None — dead code shipped alongside the fix it belonged to (#1234 review r8).
        "depth_tol": _tol_of(depth_pd),
        "cbore_dia_tol": _tol_of(recess_dia_pd),
        "cbore_depth_tol": _tol_of(recess_depth_pd),
        "csink_dia_tol": _tol_of(csink_dia_pd),
        "csink_angle_tol": _tol_of(csink_angle_pd),
        # Exact compiler identities printed by this compound callout. Count and THRU are
        # non-dimensional facts carried separately as structured callout coverage; neither
        # rendered text nor an invented dimensional identity certifies them.
        "measurements": _callout_measurements(group),
        # Exact imported source(s) whose typed rider is printed by this compound callout.
        # For a pattern the rider lives on ``member`` above, while its measurements belong
        # to the pattern owner; carrying the source here preserves that intentional split.
        "source_ids": tuple(thread_source_ids),
        # Structured coverage for physical critique. This is deliberately absent when the
        # A/F parameter was suppressed: ``DOUBLE-D`` without its defining A/F is incomplete.
        "profile_coverage": (
            (
                hole.profile,
                hole.frame.axis,
                hole.through,
                bore,
                across,
                getattr(hole, "profile_direction", None),
            )
            if getattr(hole, "profile", None) == "double_d" and across is not None
            else None
        ),
        # Natural leader-anchor geometry, independent of whether A/F was approved for
        # display. An authored omission makes the callout incomplete, but must not move its
        # arrow into solid material.
        "profile_boundary": (
            (hole.across_flats, hole.profile_direction)
            if getattr(hole, "profile", None) == "double_d"
            else None
        ),
    }
