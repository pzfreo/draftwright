"""The views a drawing has, and where they sit — ADR 0018's representation slice.

Until now nothing owned the question *which views should exist*. The four orthographic views
were named at one site in `builder` with hardcoded cameras, their page positions came from
`Analysis` fields called `FV_X`/`PV_X`/`SV_X`, and `compose.choose_scale` did its layout
arithmetic from a docstring — "Layout columns: [front(x×z)] [side(y×z)] [iso] [title block]" —
rather than from anything a caller could inspect or change. The topology was real but implicit,
spread across three modules and stated in prose.

This module makes it a value. :class:`ViewSpec` is a semantic request — what a view shows, in
model terms, and nothing about the page. :class:`ResolvedViewPlan` is the immutable result — the
same specs plus the page geometry chosen for them. The split is ADR 0018 decision §1 ("one value
vocabulary, distinct request and result states"), and it is a split rather than one mutable
object because a resolved plan that can be edited in place is indistinguishable from a request,
which is how a layout comes to be silently relaxed.

**This slice changes no behaviour.** It describes the fixed front/plan/side/iso topology the
engine already builds, and the golden placement snapshots are the gate. Semantic view SELECTION
— dropping a view because nothing needs it, which is what the thin rotational plate in
`tests/test_issue_1130_view_planning_evidence.py` is waiting for — comes only once the
lifecycle, projection-convention and requirement-coverage invariants in ADR 0018's evidence list
are guarded. A representation nobody can yet vary is the point of the first slice: everything
above it stops reading the topology out of scattered fields, so the later change has one place
to happen.

Rank 0: this is a leaf. It describes views; it cannot reach the code that draws them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


@dataclass(frozen=True)
class UncoveredViewRequirement:
    """One semantic requirement no selected principal view can carry.

    ``identity`` is deliberately opaque here.  The view-planning leaf must not import the
    model waist, while the planner supplies an ADR 0016 ``DimensionId`` at the boundary.
    ``label`` is only the human-readable rendering of that identity; callers must use
    ``identity`` for correspondence and never parse the label.
    """

    identity: Any
    label: str
    preferred_view: str
    eligible_views: tuple[str, ...]
    reason: str


class ViewPlanIncomplete(ValueError):
    """The selected principal views cannot carry every approved requirement.

    Raised by the dimension planner before projection.  This is a user/actionable planning
    result, unlike ``Drawing.ViewNotPlanned``: the latter remains the internal invariant for
    code that tries to project into a view after this check has succeeded.
    """

    def __init__(self, planned, uncovered, *, source: str = "selected") -> None:
        self.planned = tuple(planned)
        self.uncovered = tuple(uncovered)
        self.source = source
        noun = "dimension" if len(self.uncovered) == 1 else "dimensions"
        lines = [
            f"{len(self.uncovered)} approved {noun} cannot be shown by the {source} "
            f"view set {self.planned!r}:"
        ]
        for requirement in self.uncovered:
            eligible = ", ".join(f"`{view}`" for view in requirement.eligible_views)
            add = (
                f"add {eligible}"
                if len(requirement.eligible_views) == 1
                else f"add one of {eligible}"
            )
            lines.append(f"  {requirement.label}  {requirement.reason} — {add}")
        super().__init__("\n".join(lines))


#: The model axes a principal view projects onto the page, as ``(page_x, page_y)``.
#: Third-angle front shows model x across and z up; the plan shows x across and y up; the side
#: shows y across and z up. Held here rather than inferred from the camera because
#: `compose.choose_scale` needs to know which two model extents a view's block spans before any
#: geometry is projected, and deriving it from a camera vector at that point is how the mapping
#: came to be duplicated in a docstring in the first place.
_PRINCIPAL_PAGE_AXES = {
    "front": ("x", "z"),
    "plan": ("x", "y"),
    "side": ("y", "z"),
}


@dataclass(frozen=True)
class ViewSpec:
    """One view a drawing should contain, in model terms.

    Deliberately says nothing about the page: no position, no size, no scale. A spec is what a
    planner decides and a user may edit; where it lands is the resolver's answer, and mixing the
    two is what ADR 0018 §1 separates. `camera` and `up` are the projection request as
    `Drawing._add_view` already expresses it — a direction from the part and an up vector —
    while `page_axes` states which model extents the view's block spans, which is the fact
    layout arithmetic needs and cameras only imply.
    """

    name: str
    #: What the view is FOR. `principal` participates in projection-convention relationships and
    #: in the page/scale decision; `pictorial` (the iso) is orientation only and is fitted after
    #: the sheet is settled; `section` and `detail` are derived from another view. The kind is
    #: what makes "which views should exist" answerable — a principal view may be dropped only
    #: if nothing requires what it shows, and a pictorial one carries no requirements at all.
    kind: str
    camera: tuple[float, float, float] | None = None
    up: tuple[float, float, float] | None = None
    page_axes: tuple[str, str] | None = None

    def __post_init__(self) -> None:
        if self.kind not in _KINDS:
            raise ValueError(f"unknown view kind {self.kind!r}; expected one of {sorted(_KINDS)}")


_KINDS = frozenset({"principal", "pictorial", "section", "detail"})


@dataclass(frozen=True)
class ViewPlacement:
    """Where a resolved view's block sits on the page: centre and half-extents, in page mm."""

    cx: float
    cy: float
    hw: float
    hh: float

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return (self.cx - self.hw, self.cy - self.hh, self.cx + self.hw, self.cy + self.hh)


@dataclass(frozen=True)
class ResolvedViewPlan:
    """The views a drawing has, their page geometry, and the sheet they were resolved onto.

    Immutable, and immutable on purpose: ADR 0018 §1 keeps the resolved result distinct from the
    editable request so a caller cannot mutate a snapshot and have it read as an authored
    constraint. Turning one back into constraints is an explicit conversion, not an attribute
    write.
    """

    specs: tuple[ViewSpec, ...]
    placements: Mapping[str, ViewPlacement]
    scale: float
    page: tuple[float, float]

    def __post_init__(self) -> None:
        names = [spec.name for spec in self.specs]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate view names in plan: {names}")
        object.__setattr__(self, "placements", MappingProxyType(dict(self.placements)))

    def spec(self, name: str) -> ViewSpec | None:
        return next((spec for spec in self.specs if spec.name == name), None)

    def of_kind(self, kind: str) -> tuple[ViewSpec, ...]:
        return tuple(spec for spec in self.specs if spec.kind == kind)

    @property
    def principal_names(self) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.specs if spec.kind == "principal")


def third_angle_principals() -> tuple[ViewSpec, ...]:
    """The front/plan/side set the engine has always built, as specs.

    Cameras are `None` here: they depend on the scaled part's centre and the projection
    distance, which only `builder` knows, so they are filled by :func:`resolve_from_analysis`'s
    caller. What this function fixes is the SET and its page-axis mapping — the part that was
    previously a sentence in `choose_scale`'s docstring.
    """
    return tuple(
        ViewSpec(name=name, kind="principal", page_axes=axes)
        for name, axes in _PRINCIPAL_PAGE_AXES.items()
    )


def third_angle_view_names() -> tuple[str, ...]:
    """The principal view names, in the order the layout arranges them.

    One source for "which views a candidate contains", so a candidate generator and the resolver
    cannot disagree about the set while both claiming to describe the same drawing.
    """
    return tuple(_PRINCIPAL_PAGE_AXES)


def principal_placements(analysis) -> dict[str, ViewPlacement]:
    """Where the principal view blocks sit, read from a finished `Analysis`.

    Split out from :func:`resolve_from_analysis` because the layout consumers need exactly this
    and nothing else. Building a whole plan for them would make them depend on `SCALE`,
    `PAGE_W` and `PAGE_H` as well — and it did: routing `compose._view_geom` through the full
    resolver broke a repack test that passes a minimal stub carrying only the position fields.
    The stub was right and the coupling was wrong; a consumer that needs placements should ask
    for placements.
    """
    return {
        "front": ViewPlacement(analysis.FV_X, analysis.FV_Y, analysis.fv_hw, analysis.fv_hh),
        "plan": ViewPlacement(analysis.PV_X, analysis.PV_Y, analysis.fv_hw, analysis.pv_hh),
        "side": ViewPlacement(analysis.SV_X, analysis.SV_Y, analysis.sv_hw, analysis.fv_hh),
    }


def resolve_from_analysis(analysis) -> ResolvedViewPlan:
    """Read the plan the engine has already decided out of a finished `Analysis`.

    The bridge for this slice, and the reason it changes no behaviour: `Analysis` already
    carries the resolved answer, spread over `FV_X`/`PV_X`/`SV_X`, the matching `Y` fields, and
    the `fv_hw`/`sv_hw`/`fv_hh`/`pv_hh` half-extents. Reading it into one value lets every
    consumer stop reaching for those fields individually, without changing what any of them
    compute. When view selection becomes a real decision, this function is replaced by a
    resolver that *chooses*, and its callers do not move.

    The iso is included as a `pictorial` spec with no placement: it is fitted after the sheet is
    settled (`projection._fit_iso_view`), so at resolve time it genuinely has no page geometry,
    and recording a placeholder would be a claim the engine cannot honour.
    """
    placements = principal_placements(analysis)
    # The resolver now CHOOSES, which is what this function's docstring said would happen when
    # view selection became a real decision: `planned_views` is the set the layout reserved
    # space for, so the builder must create exactly those or the two disagree about the sheet.
    # None keeps the third-angle three, so every existing path is unchanged (ADR 0018, #1130).
    principals = third_angle_principals()
    wanted = getattr(analysis, "planned_views", None)
    if wanted is not None:
        principals = tuple(spec for spec in principals if spec.name in set(wanted))
    specs = principals + (ViewSpec(name="iso", kind="pictorial"),)
    return ResolvedViewPlan(
        specs=specs,
        placements=placements,
        scale=analysis.SCALE,
        page=(analysis.PAGE_W, analysis.PAGE_H),
    )


@dataclass(frozen=True)
class ViewCoverage:
    """What one view carries, and what would be lost with it.

    `carries` is every measurement drawn in this view; `exclusive` is the subset no other view
    draws. The distinction is the whole point: a view whose measurements all appear elsewhere
    costs the sheet its area and contributes nothing a reader could not get from another view.
    """

    view: str
    carries: frozenset
    exclusive: frozenset
    #: Measurements this view claims that MORE THAN ONE annotation claims somewhere on the
    #: drawing. For those, an id cannot tell whether two views draw the same mark or different
    #: marks of one parameter — so exclusivity by id is not an answer, and this records where
    #: the question was unanswerable rather than letting `exclusive` imply it was answered.
    indeterminate: frozenset = frozenset()

    @property
    def carries_nothing_exclusively(self) -> bool:
        """No measurement would be lost if this view were removed.

        **Necessary, not sufficient**, and the name says so rather than saying "redundant". A
        view can carry no exclusive DIMENSION and still be required: it may be the only view
        showing a feature's shape, the projection convention may demand it, or a reader may need
        it to relate two others. ADR 0018 makes exactly this the trap to avoid — "removing a
        visually similar but semantically necessary view is rejected by an asymmetric
        counterexample" — so this answers the measurable half and refuses to imply the rest.

        **Fails closed on indeterminate coverage**, which is the difference between right and
        wrong on a real case rather than a refinement. A `DimensionId` names a parameter, not a
        mark (ADR 0016 Amdt 3), so every rung of a step ladder shares one id. An enlarged DETAIL
        view exists precisely to redraw the rungs the main view could not fit — three of them,
        on `_crowded_staircase` — and all three claim the id the main view already claims. By id
        alone that detail carries nothing exclusively and reads as droppable, while dropping it
        loses three dimensions from the sheet: the exact trap above, reached by arithmetic
        rather than by judgement. Per-mark identity (ADR 0019 §3) is what makes the case
        answerable; until then it is reported as unanswered.
        """
        return not self.exclusive and not self.indeterminate


def view_coverage(drawing) -> Mapping[str, ViewCoverage]:
    """Per-view measurement coverage of a FINISHED drawing.

    The evidence view selection needs and does not yet have: before a view can be dropped,
    something has to be able to say what goes with it. Read through the ADR 0010 provenance
    seam — an annotation claiming a `DimensionId` is asserting it draws that measurement — and
    the view each annotation was tagged with at placement, so this is what the sheet actually
    carries rather than what the compiler hoped for.

    Duck-typed on `drawing` (`registry.names()`, `view_of`, `registry.measurement_of`) so this
    stays a rank-0 leaf, the way `audit` reads finished drawings without the engine depending on
    it. Annotations owned by no orthographic view — the title block, iso furniture — are grouped
    under `None` and take part in the exclusivity arithmetic, because a measurement stated only
    on the iso is still stated.
    """
    # SEEDED from the views the drawing has, not discovered from the annotations. A view that
    # carries nothing at all has no annotations to discover it by, so building the map from
    # annotations alone drops exactly the most redundant views: on an X-turned stepped shaft the
    # `side` view carries not one measurement, and the first version of this function reported
    # only `plan` as a candidate and never mentioned it (#1130).
    per_view: dict[Any, set] = {name: set() for name in getattr(drawing, "views", ())}
    for name in drawing.registry.names():
        view = drawing.view_of(name)
        claimed = drawing.registry.measurement_of(name) or ()
        per_view.setdefault(view, set()).update(claimed)

    # How many annotations claim each measurement, drawing-wide. An id claimed more than once
    # cannot distinguish "two views draw the same mark" from "two views draw different marks of
    # one parameter", and the second is exactly what a detail view is for.
    claim_counts: dict[Any, int] = {}
    for name in drawing.registry.names():
        for mid in drawing.registry.measurement_of(name) or ():
            claim_counts[mid] = claim_counts.get(mid, 0) + 1

    coverage = {}
    for view, carries in per_view.items():
        elsewhere: set = set()
        for other, theirs in per_view.items():
            if other != view:
                elsewhere |= theirs
        coverage[view] = ViewCoverage(
            view=view,
            carries=frozenset(carries),
            exclusive=frozenset(carries - elsewhere),
            indeterminate=frozenset(m for m in carries if claim_counts.get(m, 0) > 1),
        )
    return MappingProxyType(coverage)


def views_carrying_nothing_exclusively(drawing) -> tuple[str, ...]:
    """Principal views whose every measurement is also drawn elsewhere, sorted.

    A CANDIDATE list for removal, not a verdict — see
    :attr:`ViewCoverage.carries_nothing_exclusively`. Restricted to principal views because the
    pictorial view is orientation and carries no requirements by design, so reporting it here
    every time would be noise that trains a reader to ignore the answer.
    """
    coverage = view_coverage(drawing)
    plan = getattr(drawing, "view_plan", None)
    principals = set(plan.principal_names) if plan is not None else {"front", "plan", "side"}
    return tuple(
        sorted(
            name
            for name, cover in coverage.items()
            if name in principals and cover.carries_nothing_exclusively
        )
    )


# ---------------------------------------------------------------------------
# ADR 0018 §5 — page, scale, views and arrangement as ONE constrained choice
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LayoutCandidate:
    """One complete answer to "what does this drawing look like", before it is judged.

    ADR 0018 §5 says the planner evaluates

        candidate semantic view sets
        x preferred ISO 5455 scales
        x standard sheets
        x plausible relational arrangements

    `compose.choose_scale` has always been that loop for two of the four: it builds a list of
    `(scale, page_w, page_h, tb_w)` tuples and returns the first that fits. What it could not do
    is carry the other two, because an anonymous tuple has nowhere to put them — so the view set
    stayed fixed in three modules and the arrangement stayed a sentence in a docstring.

    This is that tuple as a value, with all four dimensions present. Today `views` is always
    the third-angle three; `arrangement` varies over :data:`ARRANGEMENTS`, and the one that
    wins is carried to placement in a :class:`ScalePick` rather than re-derived there.
    """

    views: tuple[str, ...]
    scale: float
    page: tuple[float, float]
    title_block_width: float
    #: How the view blocks are related on the sheet. `"columns"` is the arrangement the engine
    #: has always used — front and side side by side with the plan stacked above the front, iso
    #: and title block to the right. Named rather than assumed so a second one can be proposed
    #: without the first becoming a special case.
    arrangement: str = "columns"

    def __post_init__(self) -> None:
        if self.arrangement not in ARRANGEMENTS:
            raise ValueError(
                f"unknown arrangement {self.arrangement!r}; expected one of {ARRANGEMENTS}"
            )

    @property
    def legacy_tuple(self) -> tuple[float, float, float, float]:
        """`(scale, page_w, page_h, tb_w)` — the shape `choose_scale` returns to its callers.

        A migration seam, and a deliberately narrow one: callers keep taking the tuple until
        they have a reason to want the candidate, so this slice does not ripple.
        """
        return (self.scale, self.page[0], self.page[1], self.title_block_width)


@dataclass(frozen=True)
class Infeasible:
    """Why a candidate was rejected, in terms a diagnostic can print.

    ADR 0018 §6: "Infeasibility is a first-class result, not a silent relaxation." Today the
    only rejection reason the engine can give is that the geometry did not fit, and when every
    candidate is rejected `choose_scale` logs a warning and returns the last one anyway. That
    fallback is not this type's doing and this slice does not change it — but the reason a
    candidate lost is now a value rather than a `False`, which is the half that has to exist
    before the terminal behaviour can be anything but a shrug.
    """

    candidate: LayoutCandidate
    reason: str
    detail: str = ""


def candidate_is_feasible(candidate: LayoutCandidate, fits) -> Infeasible | None:
    """`None` when *candidate* survives every gate, else why it did not.

    *fits* is the caller's geometric predicate — passed in rather than imported, because this is
    a rank-0 leaf and the fit maths lives in `compose` with the strip estimates it needs.

    ADR 0018 §5 lists four hard gates; only the second ("keep all view blocks and required
    annotations in bounds and conflict-free") is evaluated here, and only in its cheap estimated
    form. The first — "preserve every supported requirement or reject the candidate" — is not
    evaluated by anything today, which is #1250: the automatic path emits sheets it would refuse
    if asked for them explicitly, because the requirement gate runs only on the explicit-scale
    path. Naming the gates in one predicate is what makes that gap a missing branch here rather
    than a difference between two call sites.
    """
    if not fits(candidate):
        return Infeasible(
            candidate=candidate,
            reason="layout_does_not_fit",
            detail=(
                f"{candidate.arrangement} arrangement of {len(candidate.views)} views at "
                f"{candidate.scale:g} does not fit {candidate.page[0]:.0f}x{candidate.page[1]:.0f}"
            ),
        )
    return None


#: The relational arrangements the layout may be composed under — ADR 0018 §5's fourth
#: dimension, ordered by preference. `columns` gives the isometric a column of its own;
#: `stacked-iso` puts it in the title block's column instead, which wins back that column's
#: width at the cost of the height the title block does not use. Preference order matters:
#: the candidate loop returns the FIRST feasible candidate, so `columns` — the arrangement
#: every existing drawing is composed under — is only departed from when it does not fit.
ARRANGEMENTS: tuple[str, ...] = ("columns", "stacked-iso")


class ScalePick(tuple):
    """`(scale, page_w, page_h, tb_w)` — plus the arrangement it was chosen under.

    ADR 0018 §5 makes scale, sheet, view set and arrangement ONE choice. Returning only the
    first three of those leaves the fourth to be re-derived downstream, and #1130 measured
    what that costs: `_layout_geometry` is a single shared authority, but scale selection
    calls it with ESTIMATED strip depths and placement with MEASURED ones, so resolving the
    arrangement inside it lets the two stages reach different answers for the same sheet —
    and the drawing silently loses dimensions to the mismatch.

    So the arrangement rides with the rest of the decision. This is a 4-tuple by
    construction, which is what keeps that possible: every existing
    `scale, page_w, page_h, tb_w = choose_scale(...)` unpack, every `pick[0]`, and every
    comparison against a plain tuple keeps working unchanged, while the stages that need
    the fourth dimension read it off the attribute.
    """

    # No `__slots__`: CPython rejects a nonempty one on a tuple subtype, so the attribute
    # lives in the instance dict. Picks are made a handful of times per build.
    arrangement: str

    def __new__(
        cls,
        scale: float,
        page_w: float,
        page_h: float,
        tb_w: float,
        arrangement: str = "columns",
    ) -> ScalePick:
        if arrangement not in ARRANGEMENTS:
            raise ValueError(
                f"unknown arrangement {arrangement!r}; expected one of {ARRANGEMENTS}"
            )
        pick = super().__new__(cls, (scale, page_w, page_h, tb_w))
        pick.arrangement = arrangement
        return pick

    def __repr__(self) -> str:
        return f"ScalePick({tuple(self)!r}, arrangement={self.arrangement!r})"


def arrangement_of(pick) -> str:
    """The arrangement `pick` was chosen under, defaulting for a plain tuple.

    Callers may hand back a bare 4-tuple — `_repack_candidates` builds its own alternatives,
    and tests construct picks by hand. Those mean "the arrangement the engine has always
    used", which is the first of :data:`ARRANGEMENTS`, not "unknown".
    """
    return getattr(pick, "arrangement", ARRANGEMENTS[0])


#: Principal view -> the model axes it lays out as (horizontal, vertical) on the page.
#: The primitive everything below derives from, so the derivations cannot drift from each
#: other or be quietly mis-stated: `front` is the x-z elevation, `plan` looks down at x-y,
#: `side` is the y-z elevation.
VIEW_AXES: dict[str, tuple[str, str]] = {
    "front": ("x", "z"),
    "plan": ("x", "y"),
    "side": ("y", "z"),
}

#: Axis letter -> the principal views that can carry a requirement about it, preference
#: ordered. `_geometry._END_ON` answers "which single view does this feature read face-on
#: in"; this answers the question view-set selection actually needs — "which views COULD
#: carry this", because an overall width reads in plan and equally well in front.
#:
#: That difference is why droppability was uncomputable. Every principal view carries some
#: requirement exclusively even on a featureless box, because the three envelope extents are
#: distributed one per view — so "carries nothing exclusively" is never true and would drop
#: nothing, ever. The real criterion is whether what a view carries can be carried by a view
#: that REMAINS (#1130).
#:
#: The first entry of each is the view that extent has always been placed in, so consulting
#: this changes nothing while all three principals are planned.
VIEWS_SHOWING: dict[str, tuple[str, ...]] = {
    "x": ("plan", "front"),
    "y": ("side", "plan"),
    "z": ("front", "side"),
}


def views_showing(axis: str, planned, *, horizontal: bool = False) -> str | None:
    """The preferred view in *planned* that can carry a requirement about *axis*.

    ``horizontal=True`` restricts to views where the axis runs ACROSS the page. A below-strip
    extent dimension is drawn horizontally, so it needs more than a view containing its axis:
    the overall depth reads in plan, but VERTICALLY, and dimensioning it there horizontally
    collapses the span to zero length. Measured as a degenerate-border `ValueError` the first
    time the plan view was offered as a fallback for it (#1130).

    ``None`` when the sheet has no such view — a caller must report that rather than place a
    requirement where it cannot be read (ADR 0016 Amdt 6).
    """
    planned = set(planned)
    return next(
        (
            view
            for view in VIEWS_SHOWING.get(axis, ())
            if view in planned and (not horizontal or VIEW_AXES[view][0] == axis)
        ),
        None,
    )
