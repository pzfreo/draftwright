"""The compiled dimension plan — the only thing a dimensional renderer may draw from.

ADR 0016's boundary rule: **renderers may emit dimensional content only from the compiled
plan.** A renderer receives approved entries and decides *where* and *how* to draw them; it
does not decide *what*, and it is not given the feature inventory or the bounding box it
would need to decide differently.

The rule exists because the previous arrangement made honouring suppression a convention.
`plan_dimensions` marked a `PlannedDimension` suppressed, handed the whole group to a
renderer that also held the `PartModel` and the `Analysis`, and trusted it to check. Eight
adversarial review rounds on #921 found eight renderers that did not — the height ladder and
step positions rebuilding their marks from the feature and `a.bb`, the entire turned family
selecting parameters with no suppression check at all. Each was a real omission reaching a
real drawing, and each was fixed locally, which is how the fourth mechanism for saying the
same thing got added.

The structural answer is that **suppression is not a flag renderers check, it is content
they never receive**:

- :class:`ApprovedDimension` has no ``suppressed`` field. There is nothing to forget.
- What was *not* approved leaves through :attr:`RenderableDimensionPlan.diagnostics`.
  Omission stays inspectable — ADR 0016's "marked, not filtered" is preserved — but it is
  not on the path a renderer walks.

  The first consumer is `add_feature_diameter`, which asks WHY a callout has nothing to
  draw so it can say "the author left this out" rather than "this feature exposes none".
  Coverage lint is the next owner (`linting/coverage.py`).
- Correlated sets (a step-height ladder, a shoulder chain) arrive as explicit
  :class:`ApprovedLadder` groups, so a renderer never reconstructs one from a feature.
- **Positions are dimensions.** A datum-referenced location prints a number, so it is
  compiled like any other (:attr:`RenderableDimensionPlan.locations`) rather than being a
  parallel pipeline the authored set could not reach. That was the #925 gap: locations had
  no `DimParameter`, so `dimension(hole, "location")` raised and every location was drawn
  regardless of what the script declared.

Where the boundary does NOT reach, stated so the exceptions cannot be mistaken for
completeness:

- **Hole callouts** still take legacy `DimensionGroup`s. They honour `suppressed` at every
  term (`model/callout.py`), so this is a structural gap, not a behavioural one — but
  "the renderer checks" is precisely the guarantee this boundary replaces, so it is listed
  rather than assumed safe.
- **Author-supplied text is not a generated measurement.** Raw AP242 PMI, GD&T control
  frames and surface finishes carry values the script or the STEP file wrote; their
  `parameters()` are empty by design and the record is rendered verbatim. There is no
  compiled content for them to come from, and an authored dimension set does not govern
  them because they are not the engine's choice to make.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from draftwright._geometry import _fmt
from draftwright.model.ir import (
    EnvelopeFeature,
    Feature,
    HoleFeature,
    PartModel,
    PatternFeature,
    PocketFeature,
    Point,
    RotationalFeature,
    SlotFeature,
    SlotPatternFeature,
    StepLevelFeature,
)
from draftwright.model.planner import (
    _AUTHORED_OMISSION,
    DimensionId,
    _decorated,
    _is_zero_step_position,
    authored_location_omitted,
    location_datum,
    plan_dimensions,
    plan_locations,
    polygonal_stock_conveys_height,
    rotational_od_conveys_height,
)


class FeatureRef:
    """An **opaque** provenance handle for the feature a dimension came from.

    Carrying the `Feature` itself on an approved entry would have left the whole bypass
    one attribute access away: a renderer could read `.levels`, `.base` or `.shoulders`
    off it and rebuild exactly the content the compiler withheld (#923 review). The AST
    guard proved only that today's renderer does not; the type still permitted it, and a
    boundary that relies on renderers not doing the thing they can trivially do is the
    convention this work exists to replace.

    So the handle exposes identity and category — enough for provenance tagging
    (ADR 0010), escalation grouping, and equality — and no measurement at all. The two
    consumers that legitimately need the object (the corridor provenance seam and the
    escalation resolver) call :func:`resolve_feature` where the object is the point.

    Python cannot make this airtight, and it is not pretending to: ``ref._feature`` still
    exists. What changes is that reading content now requires an obviously-wrong private
    access that the boundary guard greps for, rather than an innocuous ``.feature.levels``
    nobody would flag in review.
    """

    __slots__ = ("_feature",)

    _feature: Feature

    def __init__(self, feature: Feature) -> None:
        object.__setattr__(self, "_feature", feature)

    @property
    def kind(self) -> str:
        """The feature's category — ``"step_level"``, ``"envelope"``. Not a measurement."""
        return getattr(self._feature, "kind", "?")

    def __eq__(self, other) -> bool:
        # STRUCTURAL, like `DimensionId` and for the same reason: a re-plan or a finalize
        # drain rebuilds the feature objects, so identity-based equality would make a
        # reference stop matching the thing it names the moment anything re-derived the
        # model. That is exactly what `only=` selection needs — it used to compare features
        # with `in`, i.e. frozen-dataclass equality, and matching that keeps the drain's
        # per-feature subsets working (#923).
        #
        # Deliberately UNLIKE `_request_for`, which matches by `is` so two identical holes
        # stay two distinct targets. The two answer different questions: a request addresses
        # one declared instance within a build; a reference names which feature a mark came
        # from, and two structurally identical features produce interchangeable marks.
        return isinstance(other, FeatureRef) and other._feature == self._feature

    def __hash__(self) -> int:
        return hash(self._feature)

    def __repr__(self) -> str:
        return f"FeatureRef({self.kind})"


def resolve_feature(ref):
    """The `Feature` behind a :class:`FeatureRef` — for provenance and escalation ONLY.

    Called at the seams where the feature object itself is the point: the corridor's
    ADR 0010 provenance map, and the escalation resolver's grouping. A dimensional
    renderer calling this is a boundary violation, and the guard says so."""
    if ref is None or not isinstance(ref, FeatureRef):
        return ref
    return ref._feature


@dataclass(frozen=True)
class ApprovedDimension:
    """One measurement the compiler approved for drawing.

    Deliberately has **no** ``suppressed`` field: this type exists only for dimensions that
    are drawn. A renderer holding one has nothing to decide about whether to draw it.

    ``span`` is in PART space; the renderer projects it. That is the split — the compiler
    says "this measurement, this value, between these two points"; the renderer says which
    view, which strip, which side, and what happens when it does not fit.
    """

    id: DimensionId | None
    #: The formatted numeric value only (for example ``"12"``). Group renderers add
    #: semantic syntax such as ``ø``, ``THRU`` and tolerance text.
    value_text: str
    value: float
    span: tuple[Point, Point] | None
    ref: FeatureRef | None = None
    #: The parameter's semantic coordinates, flattened off `DimParameter` so a renderer
    #: selects by meaning without holding the planned object its `suppressed` flag lives on.
    kind: str = ""
    role: str = ""
    discriminator: str | None = None
    tolerance: object | None = None
    #: Structural direction needed when a span cannot encode it. In particular, a
    #: shoulder rung retains X/Y explicitly rather than asking a renderer to infer semantic
    #: direction from projected coordinates.
    axis: str | None = None
    #: A complete compiler-owned label for correlated marks whose wording is itself a
    #: content decision (for example ``"4× 10"``). Ordinary group dimensions leave this
    #: ``None`` and consume :attr:`value_text`; the two contracts are deliberately named
    #: apart so a renderer cannot mistake a numeric fragment for finished callout text.
    rendered_label: str | None = None
    #: Model-space bounds of the geometry that established ``span``'s witness, when retained.
    #: Step details use presence of this fact to crop around correspondent stations rather
    #: than treating a fallback envelope-edge span as physical evidence (#915).
    support_bounds: tuple[float, float, float, float] | None = None

    @property
    def parameter_id(self) -> str:
        base = f"{self.role}.{self.kind}"
        return f"{base}.{self.discriminator}" if self.discriminator else base

    @property
    def final_label(self) -> str:
        """The complete compiler-owned label for a correlated mark.

        Raises when called for an ordinary group dimension, whose renderer must compose
        semantic syntax around :attr:`value_text` instead.
        """
        if self.rendered_label is None:
            raise AttributeError(
                f"{self.parameter_id or 'dimension'} has numeric value_text only, "
                "not a complete rendered label"
            )
        return self.rendered_label


#: The NON-dimensional facts each feature kind may hand a renderer, by kind.
#:
#: This table is where "is this a measurement?" gets decided, once and in writing, for all
#: nineteen IR feature kinds. A renderer forming a callout legitimately needs structure — a
#: pocket's `width_axis` to know which way it runs, a pattern's `count` and `rows`, a hole's
#: `through` — and denying it that would only push the renderer back to the feature.
#:
#: What must NOT appear here is any quantity the drawing prints. `PocketFeature.width_axis`
#: is structure; `PocketFeature.width` is a measurement and travels as an approved dimension
#: or not at all. That distinction is the whole boundary, so it is stated per kind rather
#: than left to whoever is editing a renderer — the arrangement that let eight renderers
#: read `pd.param.value` past a suppression flag they never checked.
#:
#: The dividing rule, which the plate case settled: **positions that define what a
#: dimension measures travel in its SPAN; facts carry only what a span cannot express.** A
#: plate's `lo`/`hi` are not facts — they are the two ends of its thickness, and they arrive
#: as `span`. Its `axis` is a fact, because no span says which way the slab is thin. Apply
#: that test to each new entry and the table stays small.
#:
#: The compiler refuses anything absent from this table, so a new feature kind cannot leak
#: measurements by default: it arrives with no facts at all until someone lists them.
_FACTS: dict[str, tuple[str, ...]] = {
    "hole": (
        "frame",
        "through",
        "count",
        "members",
        "thread",
        "profile",
    ),
    "channel": ("frame", "width_axis", "long_axis", "depth_axis", "open_sign"),
    "pattern": ("frame", "pattern", "count", "members", "direction", "rows", "cols"),
    "pocket": ("frame", "width_axis", "long_axis", "depth_axis", "edge_anchored"),
    "pocket_pattern": (
        "frame",
        "pattern",
        "count",
        "members",
        "direction",
        "rows",
        "cols",
    ),
    "slot": ("frame", "width_axis", "long_axis"),
    "slot_pattern": (
        "frame",
        "pattern",
        "count",
        "members",
        "direction",
        "rows",
        "cols",
    ),
    "pad": ("frame", "width_axis", "long_axis"),
    "boss": ("frame", "thread"),
    "polygonal_boss": ("frame", "side_count", "flat_directions", "flat_centres"),
    "polygonal_stock": ("frame", "side_count", "flat_directions", "flat_centres"),
    "step": ("frame", "thread"),
    "step_level": ("frame",),
    "envelope": ("frame",),
    "rotational": ("frame",),
    # `leg2`/`angle` are FORM discriminators: they decide whether the label reads `C3` (an
    # equal-leg 45°) or `3 × 30°`. The angle is printed, and it is NOT a planned parameter —
    # `ChamferFeature.parameters()` emits only the leg — so it cannot be suppressed or
    # toleranced today. That is an IR gap, recorded in the ADR inventory rather than closed
    # here: making the angle addressable adds a parameter to every chamfer's plan, which
    # changes output, and a migration that claims byte-identity is the wrong place for it.
    "chamfer": ("frame", "axis", "leg2", "angle"),
    "fillet": ("frame", "axis"),
    # These are STRUCTURE, not measurements: together they say which piece of stock a flat
    # belongs to, so the renderer can tell one double-D's two faces from independent aligned
    # or slanted regions (#1013/#1036). The drawing prints none of them.
    "flat": (
        "frame",
        "axis",
        "presentation_axis",
        "axis_line",
        "stock_span",
        "axis_direction",
    ),
    "groove": ("frame", "axis"),
    "plate": ("frame", "axis"),
    # Raw AP242 PMI is the documented non-generated exception: its source-authored label
    # is rendered verbatim rather than planned or suppressible. It is intentionally the
    # sole printed value in this structural allowlist (ADR 0016, "Scope").
    "pmi": ("frame", "pmi_kind", "dominant_axis", "ref_bbox", "ref_pts", "label"),
    "authored_dimension": ("frame", "dimension_kind", "dominant_axis", "ref_pts", "ref_bbox"),
    # The gear-data renderer consumes the complete correlated IR record directly and never
    # receives a FeatureFacts projection. Classifying it empty keeps a future dimensional
    # renderer from acquiring normative values through the compiled-plan side door.
    "external_spur_gear": (),
    # Existing ADR 0011 aspect kinds are deliberately classified as exposing no
    # renderer facts yet. Listing them distinguishes "known, reviewed, empty" from a new
    # kind that has never crossed this boundary.
    "control_frame": (),
    "datum_ref": (),
    "finish": (),
    "note": (),
}


class FeatureFacts:
    """The structural, non-dimensional facts of one feature — never its measurements.

    A frozen view over the subset of attributes :data:`_FACTS` allows for the kind. Reading
    anything else raises rather than falling back to the feature, so a renderer that needs a
    new fact has to add it to the table, in a diff someone reviews, next to the sentence
    explaining what belongs there.
    """

    __slots__ = ("_kind", "_values")

    def __init__(self, feature) -> None:
        kind = getattr(feature, "kind", "?")
        allowed = _FACTS.get(kind, ())
        values = {name: getattr(feature, name) for name in allowed if hasattr(feature, name)}
        # Pattern renderers need the representative member's ORIENTATION, never its
        # printable sizes. Flatten those structural facts here instead of exposing the
        # member object (which would put width/length/depth back one attribute away).
        member = getattr(feature, "member", None)
        if kind in ("pocket_pattern", "slot_pattern") and member is not None:
            values.update(
                {
                    "member_frame": member.frame,
                    "member_width_axis": member.width_axis,
                    "member_long_axis": member.long_axis,
                }
            )
            if kind == "pocket_pattern":
                values["member_depth_axis"] = member.depth_axis
        object.__setattr__(self, "_kind", kind)
        object.__setattr__(self, "_values", values)

    def __getattr__(self, name: str):
        # deepcopy/pickle probe special methods on an uninitialised `__new__` object.
        # Touching `_values` through __getattr__ in that state recursively re-entered this
        # method. Backing slots and special names are never public facts.
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            values = object.__getattribute__(self, "_values")
            return values[name]
        except KeyError:
            raise AttributeError(
                f"{self._kind!r} exposes no fact {name!r} to renderers. If it is structure "
                f"rather than a measurement, add it to _FACTS[{self._kind!r}]; if it is a "
                "quantity the drawing prints, it must travel as an approved dimension."
            ) from None

    def get(self, name: str, default=None):
        return self._values.get(name, default)

    def __repr__(self) -> str:
        return f"FeatureFacts({self._kind}, {sorted(self._values)})"


@dataclass(frozen=True)
class ApprovedGroup:
    """One feature's approved dimensions, plus the structure a renderer needs to draw them.

    The general replacement for `DimensionGroup` at the renderer boundary. Same shape —
    `dims`, `view`, `anchor`, `feature_kind` — with two differences that are the point:
    `dims` holds only what the compiler approved and has no `suppressed` field, and the
    feature is reachable only as :class:`FeatureFacts` (structure) or an opaque
    :class:`FeatureRef` (identity), never as the object whose measurements a renderer could
    read past the plan.
    """

    feature_kind: str
    view: str
    anchor: Point
    ref: FeatureRef
    facts: FeatureFacts
    dims: tuple[ApprovedDimension, ...]

    def dim(self, *, kind: str | None = None, role: str | None = None):
        """The first approved dimension matching *kind* and/or *role*, or ``None``.

        ``None`` means "not approved" — there is no second state to check. A renderer that
        used to write ``if pd is None or pd.suppressed`` writes ``if pd is None``."""
        for d in self.dims:
            if (kind is None or d.kind == kind) and (role is None or d.role == role):
                return d
        return None


@dataclass(frozen=True)
class ApprovedLadder:
    """A correlated set approved as a whole (ADR 0016 identity tier 3).

    A step-height ladder or shoulder chain is ONE addressable dimension holding N members,
    so it is approved or omitted whole — never half a staircase. Arriving as an explicit
    group is what stops a renderer rebuilding the members from `StepLevelFeature.levels`
    and the bounding box, which is exactly what `render_height_ladder` did.
    """

    kind: str  # "step_height" | "step_position" | "overall_height"
    rungs: tuple[ApprovedDimension, ...]
    ref: FeatureRef | None = None
    #: A uniform staircase collapsed to a single ``n× rise`` mark. The COLLAPSE is a
    #: content decision and is made here; the renderer only needs to know so it can name
    #: the mark (``dim_step_typ`` rather than ``dim_step_0``) and skip the per-rung
    #: legibility gate, which has nothing to filter when there is one representative rung.
    representative: bool = False


@dataclass(frozen=True)
class Omission:
    """A measurement the compiler did not approve, and why.

    ``authored`` separates the author's own omission from a planner rule's suppression (an
    X-turned extent, a rotational OD's cross-axis extent — the square-footprint rule that used
    to be the stock example was deleted in #997). Only the first makes an empty result the script's
    doing, and only the first is recoverable by adding a `dimension(...)` line — a
    distinction three attempts at predicting renderer behaviour in #921 kept blurring.
    """

    feature: Feature | None
    parameter_id: str
    value: float | None
    reason: str
    #: The dimension that states this fact instead, when the omission was a
    #: consolidation rather than a withholding (#1154). ``None`` wherever nothing takes the
    #: fact over — but NOT a synonym for "the rule set did it": an authored omission carries
    #: it too whenever the author's set keeps the owner, because the author chooses which
    #: dimensions are drawn and not where the geometry states a fact. Read ``authored`` for
    #: whose decision it was and this for where the measurement went.
    #: Completeness lint requires this owner to have actually landed; a consolidation
    #: onto a dimension the placer then drops is a missing measurement, not a covered one.
    conveyed_by: DimensionId | None = None
    #: Stable lint code when this compiler decision should also be visible through
    #: ``Drawing.lint()``. Most ordinary suppression rules remain audit-only and leave this
    #: unset; a datum-coincident shoulder sets it because otherwise the validation decision
    #: that prevented invalid border geometry would be invisible to the drawing caller.
    code: str | None = None

    @property
    def authored(self) -> bool:
        return self.reason == _AUTHORED_OMISSION


@dataclass(frozen=True)
class ApprovedContingency:
    """Compiler-approved content released only when its primary representation places none.

    The fallback is dimensional content, not a renderer recipe: it is the same
    :class:`ApprovedLadder` the renderer would receive on the ordinary path. ``inactive`` is
    the planner diagnostic that remains truthful while the primary survives and is removed
    when the fallback is released. Placement decides only whether the primary survived; it
    never derives the fallback measurement.
    """

    primary: str
    fallback: ApprovedLadder
    inactive: Omission


#: The role a script names for each ladder kind. The step kinds are absent deliberately:
#: their members are the step feature's own parameters, already addressed through the group
#: traversal, so naming them again would ask a script to declare one measurement twice.
_LADDER_ROLE = {"overall_height": "height.length"}


def _deduplicated(intents: list) -> list:
    """*intents* with repeated ``(feature, role)`` targets removed, first occurrence kept."""
    seen: set = set()
    out = []
    for i in intents:
        key = (id(resolve_feature(i.ref)) if i.ref is not None else None, i.role)
        if key in seen:
            continue
        seen.add(key)
        out.append(i)
    return out


def _addressable_units(group) -> list[ApprovedDimension]:
    """One representative dimension per addressable UNIT of *group*, in order.

    A correlated set — a pattern's members, a ladder's rungs — shares one `DimensionId` and is
    addressed once, so a script drops the whole set with one line rather than emitting member
    lines that cannot individually be honoured (ADR 0016 identity tier 3).
    """
    seen: set = set()
    out: list[ApprovedDimension] = []
    for d in group.dims:
        key = d.id if d.id is not None else (id(group.ref), d.parameter_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


@dataclass(frozen=True)
class AddressableIntent:
    """One approved dimensional intent, and how a script would NAME it (#946).

    The compiler already decides what the drawing carries; this is the same decision said in
    the vocabulary a generated script speaks, so emission consumes the compiler's answer
    instead of re-deriving it. `sheet_emit._mirrored_requests` used to assemble that answer
    from three sources — a synthesised envelope, `plan_dimensions()`, and
    `compile_dimensions().locations` — with comments explaining which compiler facts each one
    was reconstructing. A category the compiler grew could render correctly and vanish from
    generated scripts until someone remembered to extend it.

    ``ref`` is the feature a script names, or ``None`` for a model-level intent (the overall
    height, which belongs to the part rather than to any feature). ``role`` is the parameter id
    or role that would be written.

    Deliberately carries no "not representable" state yet. The first cut had a `reason` field
    and a `representable` property that every construction site left unset — machinery
    documenting a capability that did not exist, while unrepresentability was still discovered
    downstream from `_feature_line`. #939's comment floor is what would populate it; it can
    arrive with that work rather than ahead of it (Codex review of #975).
    """

    ref: FeatureRef | None
    role: str


@dataclass(frozen=True)
class RenderableDimensionPlan:
    """Everything approved for drawing, plus what was not and why.

    Grows one renderer at a time: :attr:`ladders` covers the height ladder and shoulder
    chain (the first migrated slice). Renderers not yet migrated keep consuming
    `plan_dimensions` directly, and the migration is finished when none do.

    :attr:`diagnostics` is retained by the builder as the drawing's suppression ledger;
    coverage consumes its family outcomes, and selected validation omissions also surface
    through lint. It stays separate from approved content so no renderer can see it.
    """

    groups: tuple[ApprovedGroup, ...] = ()
    ladders: tuple[ApprovedLadder, ...] = ()
    #: Approved datum-referenced positions, in planner order. A location is a dimension
    #: and lives inside the boundary like any other; it arrives as its own collection
    #: because the planner synthesizes it across features (datum → ref) rather than off a
    #: single feature's parameters, so it has no `DimensionGroup` to belong to.
    #: `span` is ``(datum, located point)`` and `axis` is the feature's frame axis — the
    #: two things the renderer needs and cannot derive.
    locations: tuple[ApprovedDimension, ...] = ()
    #: Approved alternatives that are not drawn with their primary representation. They are
    #: addressable because a generated authored script must carry the fallback intent even
    #: when the automatic build did not need to release it.
    contingencies: tuple[ApprovedContingency, ...] = ()
    diagnostics: tuple[Omission, ...] = field(default=())

    def of_kind(self, *kinds: str) -> tuple[ApprovedGroup, ...]:
        """Every approved group whose feature is one of *kinds*, in model order."""
        return tuple(g for g in self.groups if g.feature_kind in kinds)

    def group_for(self, ref) -> ApprovedGroup | None:
        """The approved group for a feature reference, or ``None`` if nothing survived."""
        return next((g for g in self.groups if g.ref == ref), None)

    def ladder(self, kind: str) -> ApprovedLadder | None:
        """The approved ladder of *kind*, or ``None`` if it was not approved."""
        return next((lad for lad in self.ladders if lad.kind == kind), None)

    def contingency(self, primary: str) -> ApprovedContingency | None:
        """The approved fallback for *primary*, or ``None`` when none was compiled."""
        return next((item for item in self.contingencies if item.primary == primary), None)

    def release_contingency(self, primary: str) -> RenderableDimensionPlan:
        """Return a plan in which *primary*'s already-approved fallback is active.

        Releasing removes the inactive planner diagnostic. A fallback that later fails to fit
        is a placement drop and is reported by the renderer, not as a stale suppression.
        """
        selected = tuple(item for item in self.contingencies if item.primary == primary)
        if not selected:
            return self
        inactive_ids = {id(item.inactive) for item in selected}
        return replace(
            self,
            ladders=(*self.ladders, *(item.fallback for item in selected)),
            contingencies=tuple(item for item in self.contingencies if item.primary != primary),
            diagnostics=tuple(item for item in self.diagnostics if id(item) not in inactive_ids),
        )

    #: The fields carrying approved dimensional content, each with how it names itself.
    #: `addressable()` iterates exactly this, and
    #: `test_compiled_plan_boundary.py::test_every_approved_collection_is_addressable` requires
    #: it to cover every such field — so a NEW compiler-owned category cannot be added without
    #: either flowing into generated scripts or being explicitly, visibly excluded (#946).
    #: `diagnostics` is not here: it is what was NOT approved, and has its own contract.
    _ADDRESSABLE = ("groups", "ladders", "locations", "contingencies")

    def addressable(self) -> tuple[AddressableIntent, ...]:
        """Every approved intent, in plan order, as the target a script would name.

        One dimension per addressable UNIT, never per member: a step ladder and a rotational
        body's bores are each ONE intent holding N, so a script drops the set with one line and
        no member line misleads (ADR 0016 identity tier 3).
        """
        out: list[AddressableIntent] = []
        # Dispatched THROUGH the roster, not merely documented by it: the first cut hard-coded
        # three loops and listed the field names beside them, so adding a field and adding its
        # name passed the guard while the method never traversed it — confidence without
        # enforcement (Codex review of #975).
        for name in self._ADDRESSABLE:
            out += getattr(self, f"_addressable_{name}")()
        return tuple(_deduplicated(out))

    def _addressable_groups(self) -> list[AddressableIntent]:
        out: list[AddressableIntent] = []
        for group in self.groups:
            out += [
                AddressableIntent(group.ref, d.parameter_id) for d in _addressable_units(group)
            ]
        return out

    def _addressable_ladders(self) -> list[AddressableIntent]:
        out: list[AddressableIntent] = []
        for lad in self.ladders:
            # A ladder's rungs carry no parameter id — they are a correlated set, not one
            # feature parameter — so the role a script would name is stated here, by the
            # compiler that built the ladder, rather than inferred by a consumer. That is the
            # boundary #946 is about: this is the right side of it.
            #
            # `ref is None` is the model-level case: the overall height of a part with no
            # envelope feature belongs to the part, not to any feature.
            #
            # Note the deliberate asymmetry with `id`, since the two now disagree. #1230 gives
            # that rung a `DimensionId` whose feature IS a bounding-box envelope, minted for
            # identity so the annotation can claim something; `ref` stays None, because a
            # FeatureRef is what a script addresses and there is no such feature to address.
            # Identity and addressability are different questions here, and this is the one
            # place where the answers differ — worth stating rather than leaving the reader to
            # find a comment that says no feature is ever invented, eight lines from where one
            # is (#1233 review).
            if lad.kind not in _LADDER_ROLE:
                continue
            out.append(AddressableIntent(lad.ref, _LADDER_ROLE[lad.kind]))

        return out

    def _addressable_locations(self) -> list[AddressableIntent]:
        out: list[AddressableIntent] = []
        named: set[int] = set()
        for approved in self.locations:
            # One intent per FEATURE, not per ref. The compiler preserves coincident
            # feature-owned identities; rendering may share one visible ordinate while
            # accumulating every owner. `dimension(f, "location")` remains a per-feature
            # unit (#883 is the per-member question, deliberately not answered here).
            feature = resolve_feature(approved.ref)
            if feature is None or id(feature) in named:
                continue
            named.add(id(feature))
            out.append(AddressableIntent(approved.ref, "location"))
        return out

    def _addressable_contingencies(self) -> list[AddressableIntent]:
        out: list[AddressableIntent] = []
        for item in self.contingencies:
            ladder = item.fallback
            # Contingencies are approved dimensional content, so silently ignoring an
            # unregistered kind would let generated scripts lose it. Indexing is deliberately
            # fail-closed, matching the addressability ratchet on the containing plan.
            out.append(AddressableIntent(ladder.ref, _LADDER_ROLE[ladder.kind]))
        return out

    def omitted(self, kind: str) -> tuple[Omission, ...]:
        """Diagnostics whose parameter belongs to *kind* — what a lint pass reports."""
        return tuple(o for o in self.diagnostics if o.parameter_id.startswith(f"{kind}."))


def _step_repeat(levels, base: float, top: float, tol_frac: float = 0.10):
    """``(n, rise)`` if *levels* form a uniform staircase, else ``None``.

    A uniform staircase has all inter-step rises (including from *base* to the first step)
    within *tol_frac* of their mean; ``n`` counts the top gap too when it matches. Requires
    ≥3 interior steps to avoid false positives.

    Lives in the compiler because collapsing five rungs into one ``5× 10`` mark is a
    decision about WHAT the drawing says, not where it goes. It was in the renderer beside
    the legibility gate, which reads similarly and is genuinely a placement decision (it
    depends on the scale) — the two moved apart here on purpose.
    """
    if len(levels) < 3:
        return None
    ordered = sorted(levels)
    rises = [ordered[0] - base] + [ordered[i + 1] - ordered[i] for i in range(len(ordered) - 1)]
    mean = sum(rises) / len(rises)
    if mean <= 0 or not all(abs(r - mean) / mean <= tol_frac for r in rises):
        return None
    return len(rises) + (1 if abs((top - ordered[-1]) - mean) / mean <= tol_frac else 0), mean


def _suppressed_dims(model: PartModel, groups=None):
    """``{(feature, parameter_id): (value, reason)}`` for every dimension the planner
    marked — the compiler's input for what NOT to approve.

    *groups* lets a caller that has already planned pass the result in. The engine plans
    once per build (ADR 0008 Amdt 5); a compiler that re-planned would both cost a second
    pass and create two products that can drift while the migration is partial (#923
    review)."""
    out = {}
    for group in groups if groups is not None else plan_dimensions(model):
        for pd in group.dims:
            if pd.suppressed:
                out[(id(group.feature), pd.param.parameter_id)] = (
                    pd.param.value,
                    pd.reason or "suppressed",
                )
    return out


def _dim_id(feature, parameter_id: str) -> DimensionId | None:
    """The ADR 0016 identity for an approved entry.

    Minted here rather than left ``None``: `DimensionId` is already the stable addressable
    identity the ADR defines, and a renderer-facing result that discards it would create
    identity debt on the very boundary meant to remove it — provenance, edits, diagnostics
    and later de-duplication all key on it (#923 review)."""
    if feature is None:
        return None
    return DimensionId(feature, parameter_id)


def _compile_step_ladders(model: PartModel, marked) -> tuple[list[ApprovedLadder], list[Omission]]:
    """The prismatic height rungs and shoulder chain, approved as whole sets."""
    step = next((f for f in model.features if isinstance(f, StepLevelFeature)), None)
    if step is None:
        return [], []
    approved: list[ApprovedLadder] = []
    omissions: list[Omission] = []
    bb: Any = model.bbox  # build123d BoundBox
    fallback_x, fallback_y = float(bb.max.X), float(bb.min.Y)
    support_by_level = {support.level: support for support in step.level_supports}
    step_ref = FeatureRef(step)

    def _height_evidence(z: float):
        support = support_by_level.get(z)
        x = support.x_span[1] if support is not None else fallback_x
        y = support.y_span[0] if support is not None else fallback_y
        bounds = (
            (support.x_span[0], support.y_span[0], support.x_span[1], support.y_span[1])
            if support is not None
            else None
        )
        return ((x, y, step.base), (x, y, z)), bounds

    # An authored `.tolerance(...)` on a step level, through the one owner. The PLAIN ladder
    # rungs carry it; the `N× rise` representative below deliberately does not, because a ±
    # on a collapsed representative would claim it of every level (#28 / P2a).
    #
    # The seventh site of #1215's discard, and the last: a bare `.tolerance()` on a step level
    # reached `model.decorations` and was dropped here, so `dim_step_0` printed `14` where the
    # author wrote `14 ±0.05`. `sheet.py` promises a bare tolerance "folds onto every parameter
    # of that kind" (#1234 review r5).
    step_height_param = next((pm for pm in step.parameters() if pm.role == "step_height"), None)
    step_tol = (
        _decorated(model, step, step_height_param).tolerance
        if step_height_param is not None
        else None
    )
    heights = []
    for z in sorted(step.levels):
        span, support_bounds = _height_evidence(z)
        heights.append(
            ApprovedDimension(
                id=_dim_id(step, "step_height.length"),
                value_text=_fmt(z - step.base),
                value=z - step.base,
                tolerance=step_tol,
                span=span,
                ref=step_ref,
                rendered_label=_fmt(z - step.base),
                support_bounds=support_bounds,
            )
        )
    height_marks = [
        (pid, v, why)
        for (fid, pid), (v, why) in marked.items()
        if fid == id(step) and pid.startswith("step_height.")
    ]
    if heights and not height_marks:
        rep = _step_repeat(list(step.levels), step.base, float(bb.max.Z))
        if rep is not None:
            n, rise = rep
            first = sorted(step.levels)[0]
            span, support_bounds = _height_evidence(first)
            heights = [
                ApprovedDimension(
                    id=_dim_id(step, "step_height.length"),
                    value_text=_fmt(rise),
                    value=rise,
                    span=span,
                    ref=step_ref,
                    rendered_label=f"{n}× {_fmt(rise)}",
                    support_bounds=support_bounds,
                )
            ]
        approved.append(
            ApprovedLadder(
                "step_height", tuple(heights), ref=step_ref, representative=rep is not None
            )
        )
    else:
        omissions += [Omission(step, pid, v, why) for pid, v, why in height_marks]

    _di = {"x": 0, "y": 1, "z": 2}

    def _shoulder_span(axis: str, pos: float):
        """A shoulder's span, from its datum to its position along its own axis.

        Carried rather than left ``None`` so consumers never go back to the feature for the
        station: the detail crop needs the X positions to frame the crowded band, and
        `render_step_positions` needs both ends. The approved entry also carries the explicit
        axis because direction is semantic content, not something projection should infer."""
        lo = list(step.datum)
        hi = list(step.datum)
        hi[_di[axis]] = pos
        return (tuple(lo), tuple(hi))

    # Shoulders carry their authored tolerance too. Without this the SAME `DimensionId` sat in
    # `plan.groups` with a tolerance (via `_compile_groups`) and in `plan.ladders` without one,
    # and `evidence.compiled_values` merges both into one multimap — a one-owner violation of
    # the same shape as #1154, five lines below where #1215 added the height rungs'
    # (#1234 review r6).
    shoulder_param = next((pm for pm in step.parameters() if pm.role == "step_position"), None)
    shoulder_tol = (
        _decorated(model, step, shoulder_param).tolerance if shoulder_param is not None else None
    )
    shoulders: list[ApprovedDimension] = []
    degenerate_shoulders: list[Omission] = []
    for axis, pos in sorted(step.shoulders):
        datum = step.datum[_di[axis]]
        value = abs(pos - datum)
        if _is_zero_step_position(value):
            degenerate_shoulders.append(
                Omission(
                    step,
                    "step_position.length",
                    0.0,
                    f"{axis.upper()} shoulder at {_fmt(pos)} is coincident with its datum "
                    f"{_fmt(datum)}; zero-length step positions are omitted before rendering",
                    code="step_position_coincident_with_datum",
                )
            )
            continue
        shoulders.append(
            ApprovedDimension(
                id=_dim_id(step, "step_position.length"),
                value_text=_fmt(value),
                value=value,
                span=_shoulder_span(axis, pos),
                ref=step_ref,
                axis=axis,
                tolerance=shoulder_tol,
                rendered_label=_fmt(value),
            )
        )
    pos_marks = [
        (pid, v, why)
        for (fid, pid), (v, why) in marked.items()
        if fid == id(step) and pid.startswith("step_position.")
    ]
    if shoulders and not pos_marks:
        approved.append(ApprovedLadder("step_position", tuple(shoulders), ref=step_ref))
    else:
        omissions += [Omission(step, pid, v, why) for pid, v, why in pos_marks]
    if not pos_marks:
        omissions += degenerate_shoulders
    return approved, omissions


def _compile_overall_height(
    model: PartModel, marked, *, include_overall: bool, step_chain_approved: bool
) -> tuple[ApprovedLadder | None, ApprovedContingency | None, list[Omission]]:
    """The part's overall height — the envelope's ``height`` parameter, drawn in the
    front-view right strip rather than below a view, which is why it rides the ladder.

    Every reason it might not be drawn is settled here — as conditions named in `planner`
    (`polygonal_stock_conveys_height`, `rotational_od_conveys_height`) and applied here, so
    that #1154's consolidation can ask the same questions without re-deriving them. They used
    to be split in the way that actually hurts:
    the planner suppressed it for a Z-turned part while the renderer independently suppressed
    it for that AND an X/Y rotational OD AND `include_overall`, and neither knew about the
    other. ``include_overall`` is drawing state (the finalize drain's explicit-envelope-height
    request), so it arrives as an argument rather than being read off the model.

    A model with no `EnvelopeFeature` — a round body, or a `Sheet` that never called
    `.envelope()` — has no parameter naming its height, and the value falls back to the
    bounding box. The COMPILER may do that; a renderer may not, which is the whole point.
    """
    # Whole polygonal stock already owns the same axial extent as `stock_length.length`.
    # Letting the bbox fallback add `dim_height` would state one physical requirement twice,
    # and would make authored suppression ineffective through an unrelated synthetic value.
    #
    # Asked through the planner's predicates rather than re-tested here: its consolidation
    # needs the same answers (#1154), and the paragraph above about two owners of this
    # decision applies to a second owner in either direction. Each CONDITION is consulted
    # where it belongs — collapsing both into one early return here deleted the rotational
    # case's `Omission` for a part with no envelope feature (#1154 review r2).
    if polygonal_stock_conveys_height(model):
        return None, None, []
    env = next((f for f in model.features if isinstance(f, EnvelopeFeature)), None)
    bb: Any = model.bbox  # build123d BoundBox
    rot = next((f for f in model.features if isinstance(f, RotationalFeature)), None)
    if not include_overall:
        return None, None, []
    mark = marked.get((id(env), "height.length")) if env is not None else None
    if env is not None:
        if mark is not None and mark[1] == _AUTHORED_OMISSION:
            return None, None, [Omission(env, "height.length", mark[0], mark[1])]
    elif model.authored_dimensions is not None:
        # No `EnvelopeFeature`, so the height falls back to the bounding box and no
        # parameter anywhere names it. An AUTHORED set is the drawing's complete
        # dimensioning, so it must not acquire a measurement its author had no way to ask
        # for: declaring `.envelope()` is how the overall height becomes nameable (#876).
        # This is the one place the fallback lives, so refusing it is one branch rather
        # than a rule every renderer has to remember.
        # `feature=None` here, while the APPROVED rung below now carries a minted envelope
        # identity (#1230). The two halves of a documented-joinable pair therefore disagree for
        # this one measurement: `Drawing.measurement_keys` says it is "deliberately the SAME row
        # shape … so a drawn measurement and a suppressed one are directly comparable", and
        # `audit._correspondence` keys on the feature string — so it attributes a lost `dim_od`
        # and `ldr_z0` but not a lost `dim_height` (#1233 review, F3/R4).
        #
        # Not closed here, and the reason is measured rather than chosen. Passing the same
        # identity to both omissions is three lines and does work — but `height.length` is the
        # ONLY producer of `feature=None` rows in the public ledger (zero featureless rows across
        # the whole detected corpus; on `test_real_mixed_featureless_and_feature_suppressions_
        # have_a_total_order`'s own fixture it is the single one). Giving it a feature deletes
        # the last real source of the mixed `None`/`str` case that #1077's guard exists to sort,
        # leaving that test asserting something no build can produce. Whether `feature=None`
        # should stay representable in the public ledger is a question about the ledger's shape,
        # not about this rung. Tracked on #1230.
        return (
            None,
            None,
            [
                Omission(
                    None, "height.length", float(bb.size.Z), "not in the authored dimension set"
                )
            ],
        )
    value = float(env.height) if env is not None else float(bb.size.Z)
    # A model with no `EnvelopeFeature` still gets an identity for its overall height, from a
    # bounding-box envelope minted here. Without it `_dim_id` returns None, the rung carries no
    # id, and `render_height_ladder` — which already threads `measurement=mid` correctly — has
    # nothing to thread, so `dim_height` reached the sheet claiming nothing and the verifier was
    # blind to it. A PLAN-content gap, not closable at the renderer (#1230).
    #
    # Be exact about which ADR: this is **ADR 0010 provenance / ADR 0016 identity**, NOT an
    # ADR 0016 Amdt 1 breach, which an earlier version of this comment asserted three times.
    # Amendment 1 is about renderers emitting content the plan does not contain. The plan DID
    # contain this: the rung existed with the right value, and `from_model` builds the label
    # from the compiler's own `rendered_label`. Nothing was renderer-derived. What was missing
    # was the identity, so nothing could claim it (#1233 review).
    #
    # The discriminator is having no envelope feature, NOT being rotational: `grm03` is neither
    # rotational nor enveloped and escaped. (`if_step_flat_across_cylinder` was cited as the
    # other half of that contrast and does not earn it — it is rotational and enveloped, but its
    # overall-height ladder is None and it draws no `dim_height` at all, so it discriminates
    # nothing. The mechanism is the evidence: `_dim_id` returns None iff the feature is None.)
    #
    # Minted from the bbox rather than added to `model.features`: this feature names the
    # measurement, it is not a new recognised feature, and `DimensionId` compares features
    # STRUCTURALLY — so re-planning the same part yields an equal id, which is the stability the
    # identity model needs. The value is unchanged; only its identity is new.
    identity = env
    if identity is None:
        # Through `_envelope_from_bbox`, the one shared constructor — NOT hand-rolled here.
        # The first version of this hardcoded `Frame(origin=(0, 0, 0))`, which is the bbox
        # centre only for a part centred on the origin. That is #976's signature, and this
        # would have been its FIFTH instance: `sheet_emit` already paid for it once, and its
        # own comment calls the hand-rolled version "the fourth instance of #977's signature".
        # Measured, the literal made the direct and mirrored paths mint UNEQUAL ids for the
        # same measurement on an off-centre part — breaking exactly the ADR 0011 round trip
        # `mirror_model` exists for (#1233 review).
        from draftwright.model.declare import _envelope_from_bbox

        identity = _envelope_from_bbox(bb)
    env_ref = FeatureRef(env) if env is not None else None
    x, y = float(bb.max.X), float(bb.min.Y)
    # An authored `envelope().tolerance(...)` on the height, through the one owner
    # (`_decorated`) rather than read off `model.decorations` here — a second reader is how
    # a decorated parameter and an undecorated one came to look interchangeable (#1154).
    # A part with no `EnvelopeFeature` has nothing to key a decoration on, and #876 already
    # says declaring `.envelope()` is how the overall height becomes nameable, so `None` here
    # is the right answer rather than a gap (#1215).
    # `next()` with no default, deliberately: `EnvelopeFeature.parameters()` always returns a
    # `height` role, so a guard for its absence is a branch no test can take — and one that
    # would silently drop an authored tolerance if the contract ever changed. Raising is the
    # honest failure.
    height_tol = None
    if env is not None:
        height_param = next(pm for pm in env.parameters() if pm.role == "height")
        height_tol = _decorated(model, env, height_param).tolerance
    ladder = ApprovedLadder(
        "overall_height",
        (
            ApprovedDimension(
                id=_dim_id(identity, "height.length"),
                value_text=_fmt(value),
                value=value,
                span=((x, y, float(bb.min.Z)), (x, y, float(bb.max.Z))),
                ref=env_ref,
                tolerance=height_tol,
                # `rendered_label` is the BARE value while `tolerance` is set beside it, so for
                # a toleranced rung this field is not the "complete compiler-owned label" its
                # own docstring promises — the renderer composes the suffix. It has to:
                # `_tol_suffix` lives in `_core` at rank 1 and this module is rank 0, so the
                # compiler cannot build the string. Today the overall-height rung has exactly
                # one consumer (`render_height_ladder`), which does compose it; a second
                # consumer trusting the docstring would silently drop the tolerance
                # (#1234 review, F8/finding 6).
                rendered_label=_fmt(value),
            ),
        ),
        ref=env_ref,
    )
    if model.orientation == "z":
        if step_chain_approved:
            inactive = Omission(
                env,
                "height.length",
                value,
                "Z-turned (the step chain tiles the height)",
            )
            return None, ApprovedContingency("step_length", ladder, inactive), [inactive]
        return ladder, None, []
    if rot is not None and rotational_od_conveys_height(model):
        # `rot is not None` is implied by the predicate and stated anyway, so the message
        # below can read the axis off it without a second narrowing.
        return (
            None,
            None,
            [
                Omission(
                    env,
                    "height.length",
                    value,
                    f"rotational OD ({rot.frame.axis}-axis) conveys the height",
                )
            ],
        )
    if mark is not None:
        return None, None, [Omission(env, "height.length", mark[0], mark[1])]
    return ladder, None, []


def _compile_locations(model: PartModel) -> tuple[list[ApprovedDimension], list[Omission]]:
    """The datum-referenced positions, approved or omitted.

    A location is a dimension: it prints a number, so an authored set that does not name it
    must not get one. That decision is made in `plan_locations` (which owns the datum and
    authored suppression) and read off `suppressed` here. Coincident feature identities
    remain distinct through this compiler boundary; the renderer may combine their truly
    coincident marks while retaining all semantic owners. This stays the single place a
    renderer's location content comes from.

    The renderer keeps its own filters — the concentric-bore exclusion, the legibility gate,
    the sub-millimetre offset test. Those only ever REMOVE an approved entry, which is a
    drop, not a leak; the rule this boundary enforces is that nothing reaches the page that
    the compiler did not approve, not that everything approved reaches it.
    """
    approved: list[ApprovedDimension] = []
    omissions: list[Omission] = []
    for pd in plan_locations(model):
        feature = pd.feature
        span = pd.param.span
        directional_location = (
            isinstance(feature, HoleFeature | PatternFeature | SlotPatternFeature)
            and feature.frame.axis == "z"
        )
        directional_slot_pattern = (
            isinstance(feature, SlotPatternFeature) and feature.frame.axis == "z"
        )
        if pd.suppressed:
            # Before the span assert, deliberately: a suppressed entry records WHY a position
            # is absent and needs no geometry. When the model has no `datum_xy` there is no
            # datum → ref span to build one from, so requiring it here turned that diagnostic
            # into an AssertionError — a silent hole replaced by a crash, which is worse for
            # the caller it was meant to help (#996).
            parameter_ids = (
                tuple(f"{pd.param.parameter_id}.{axis}" for axis in ("x", "y"))
                if directional_slot_pattern
                else (pd.param.parameter_id,)
            )
            omissions.extend(
                Omission(feature, parameter_id, None, pd.reason or "suppressed")
                for parameter_id in parameter_ids
            )
            continue
        assert span is not None  # an APPROVED location always carries its datum → ref span
        axis = feature.frame.axis if feature is not None else None
        if directional_location:
            assert feature is not None
            # One authored `location` intent, two independently observable page dimensions.
            # Hole/pattern X/Y facts remain rendering members of the ONE feature-level
            # addressable DimensionId (ADR 0016 / #883); critique carries their directional
            # physical evidence separately. Slot patterns retain their existing directional
            # identity contract.
            for measured_axis in ("x", "y"):
                index = "xyz".index(measured_axis)
                value = abs(span[1][index] - span[0][index])
                parameter_id = (
                    f"{pd.param.parameter_id}.{measured_axis}"
                    if directional_slot_pattern
                    else pd.param.parameter_id
                )
                approved.append(
                    ApprovedDimension(
                        id=_dim_id(feature, parameter_id),
                        value_text=_fmt(value),
                        value=value,
                        # `render_locations` groups both axes from this full datum→ref
                        # relationship; narrowing one copy would erase the other datum
                        # coordinate before the renderer separates X from Y.
                        span=span,
                        ref=FeatureRef(feature),
                        kind="location",
                        role=pd.param.role,
                        discriminator=measured_axis,
                        axis=axis,
                    )
                )
            continue
        if isinstance(feature, PocketFeature) and axis != "z":
            # A non-Z pocket's two in-plane coordinates are drawn as TWO dims in its end-on
            # view (`render_slots`), so they are approved as two entries carrying their own
            # values — the same shape `_compile_off_axis_hole_locations` uses.
            #
            # One entry with `value_text=""` made the renderer subtract the span's endpoints
            # itself to get each axis's number, which is the compiler's job done twice; the
            # site that consumed it would have printed an empty label the moment it read
            # `value_text` (#925). The Z-normal ladder below is the remaining exception and
            # is listed as such.
            for meas in (feature.long_axis, feature.width_axis):
                index = "xyz".index(meas)
                value = abs(span[1][index] - span[0][index])
                start = list(span[1])
                start[index] = span[0][index]
                approved.append(
                    ApprovedDimension(
                        id=_dim_id(feature, f"{pd.param.role}.{meas}"),
                        value_text=_fmt(value),
                        value=value,
                        span=((start[0], start[1], start[2]), span[1]),
                        ref=FeatureRef(feature),
                        kind="location",
                        role=pd.param.role,
                        discriminator=meas,
                        axis=axis,
                    )
                )
            continue
        approved.append(
            ApprovedDimension(
                id=_dim_id(feature, pd.param.parameter_id),
                #: Pocket/pad Z-normal ladders remain one location entry with no per-axis value:
                #: `render_locations` groups refs ACROSS features and dedups per axis before
                #: it knows which dims exist, so an entry per axis would be approving a mark
                #: whose existence the renderer decides. Splitting it needs that grouping to
                #: move into the compiler — tracked as follow-up work, and listed in the
                #: label-provenance ratchet so it cannot be forgotten.
                value_text="",
                value=0.0,
                span=span,
                ref=FeatureRef(feature) if feature is not None else None,
                kind="location",
                role=pd.param.role,
                axis=axis,
            )
        )
    return approved, omissions


def _compile_off_axis_hole_locations(
    model: PartModel,
) -> tuple[list[ApprovedDimension], list[Omission]]:
    """A side-drilled hole's positions — its in-plane offset and its height.

    `plan_locations` handles the Z-normal ladder, which measures from `datum_xy`. These
    measure from the BOUNDING BOX in the hole's end-on view, so they are compiled here for
    the same reason a slot's position is: same authored decision, different datum.

    The gap this closes: `location_role` said a hole is locatable, `plan_locations` said
    only a Z-normal one is, and `_locate_off_axis_holes` drew the X/Y ones anyway from the
    raw IR. Three statements of one fact, so an authored set naming only a side-drilled
    bore's ⌀ still produced its 35 mm offset and 12 mm height (#925 review).

    **Two entries per member, not one.** `dim_loc_side_y3500` and `dim_loc_front_z1200` are
    separate dimensions on the page; collapsing them into a single "this hole is located"
    approval would leave the renderer deciding which of the two an approval covered, which
    is the content decision this boundary exists to remove. `discriminator` is the MEASURED
    axis and `axis` is the hole's own — the renderer needs both, and neither is derivable
    from the other.
    """
    approved: list[ApprovedDimension] = []
    omissions: list[Omission] = []
    bb: Any = model.bbox
    for f in model.features:
        # Eligibility comes from `location_datum`, not a second orientation rule here —
        # that is the duplication this whole class of defect keeps coming from (#925).
        if not isinstance(f, HoleFeature) or location_datum(f) != "bbox":
            continue
        # An X-drilled hole is located across Y; a Y-drilled one across X. Both carry a
        # height. (Pattern members are their own `PatternFeature`, so patterned holes are
        # excluded by construction — as `_ir_off_axis_holes` documents.)
        measured = ("y" if f.frame.axis == "x" else "x", "z")
        omitted = authored_location_omitted(model, f)
        for member in f.members or (f.frame.origin,):
            for meas in measured:
                index = "xyz".index(meas)
                datum = float(getattr(bb.min, meas.upper()))
                value = abs(member[index] - datum)
                if omitted:
                    omissions.append(
                        Omission(
                            f, f"{f.LOCATION_OFF_AXIS_STEM}.{meas}", value, _AUTHORED_OMISSION
                        )
                    )
                    continue
                start = list(member)
                start[index] = datum
                approved.append(
                    ApprovedDimension(
                        id=_dim_id(f, f"{f.LOCATION_OFF_AXIS_STEM}.{meas}"),
                        value_text=_fmt(value),
                        value=value,
                        span=((start[0], start[1], start[2]), member),
                        ref=FeatureRef(f),
                        kind="length",
                        role=f.LOCATION_OFF_AXIS_STEM,  # the feature owns its name (#966)
                        discriminator=meas,
                        axis=f.frame.axis,
                    )
                )
    return approved, omissions


def _compile_slot_positions(model: PartModel) -> tuple[list[ApprovedDimension], list[Omission]]:
    """A slot's datum→near-end position, along its long axis.

    Compiled here rather than in `plan_locations` because it measures from the BOUNDING BOX
    rather than from `datum_xy`, and is drawn in the slot's own view — but it is a position
    dimension like any other, so it obeys the authored set through the same
    :func:`~draftwright.model.planner.location_role` table. Before this it obeyed nothing:
    `render_slots` computed `s.lo - a.bb.min` and printed it, so an authored set naming only
    a slot's width still produced its position dim.

    `SlotFeature.parameters()` has no position param — the datum is drawing state, not a
    feature property — which is exactly why the compiler is the right place for it.
    """
    approved: list[ApprovedDimension] = []
    omissions: list[Omission] = []
    bb: Any = model.bbox
    for f in model.features:
        # Eligibility through `location_datum`, matching `_compile_off_axis_hole_locations`
        # — a bare `isinstance` was a second, laxer answer to "is this locatable": it
        # accepted a SlotFeature SUBCLASS, which inherits `LOCATION_STEM`, so the subclass
        # minted its position under the parent's name while the planner (exact type)
        # refused to plan one. The collision the declaration exists to prevent, reached by
        # the one path that did not ask (Codex #1010 r4).
        if not isinstance(f, SlotFeature) or location_datum(f) != "bbox":
            continue
        datum = float(getattr(bb.min, f.long_axis.upper()))
        value = f.lo - datum
        if authored_location_omitted(model, f):
            omissions.append(Omission(f, f"{f.LOCATION_STEM}.length", value, _AUTHORED_OMISSION))
            continue
        start = list(f.frame.origin)
        end = list(f.frame.origin)
        start["xyz".index(f.long_axis)] = datum
        end["xyz".index(f.long_axis)] = f.lo
        approved.append(
            ApprovedDimension(
                id=_dim_id(f, f"{f.LOCATION_STEM}.length"),
                value_text=_fmt(value),
                value=value,
                span=((start[0], start[1], start[2]), (end[0], end[1], end[2])),
                ref=FeatureRef(f),
                kind="length",
                role=f.LOCATION_STEM,  # the feature owns its name (#966)
                axis=f.long_axis,
                # The direction the measurement RUNS, which this entry's DIRECTIONAL siblings
                # state and it did not: `_compile_off_axis_hole_locations` passes
                # `discriminator=meas`, a non-Z pocket the same. (Not "every sibling" — the
                # Z-normal pocket ladder above deliberately passes none, and its comment says
                # why.) That is the stronger framing: `discriminator=None` on a location entry
                # MEANS "feeds both plan ladders", which is exactly what `render_locations`
                # acted on — so on a Z-LONG slot (only nist_ctc_02 in the corpus) both ladders
                # took it, and the Y one minted a dim claiming `location_slot.length`, a Z
                # extent of 94.1, while drawing 430 (#1219).
                #
                # `axis` stays the long axis. For a SlotFeature `frame.axis` IS the long axis,
                # and structurally so: all three construction sites — `detect.py:344`,
                # `detect.py:367`, `declare.py:1158` — pass `axis=long_axis` unconditionally,
                # so passing `f.frame.axis` here to "match the siblings" would be a no-op
                # dressed up as a fix. (A draft justified this by counting slots in the corpus,
                # which is both weaker and, at "25", wrong: there are 24.) The field genuinely
                # means the feature normal elsewhere and the long axis here; that inconsistency
                # is real, and naming it beats papering over it.
                discriminator=f.long_axis,
            )
        )
    return approved, omissions


def _compile_groups(planned) -> tuple[list[ApprovedGroup], list[Omission]]:
    """Every planned group, reduced to what the compiler approved, plus what it withheld.

    The general path all remaining renderers migrate onto: same per-feature shape they
    already consume, with the suppressed entries REMOVED rather than marked. A renderer
    receiving one of these cannot draw a withheld measurement, because it was never
    handed it — which is the whole rule, applied to every feature kind rather than the
    three that had bespoke migrations.

    The withheld entries leave through `diagnostics`. They did not before: only the two
    bespoke compilers reported, so `plan.diagnostics` was empty for every ordinary feature
    and a caller asking "was this the author's doing?" got no for an authored omission
    (#925). An output channel that is right for three kinds and silent for sixteen is worse
    than no channel, because it reads as an answer."""
    out: list[ApprovedGroup] = []
    omissions: list[Omission] = []
    for g in planned:
        omissions.extend(
            Omission(
                g.feature,
                pd.param.parameter_id,
                float(pd.param.value),
                pd.reason or "suppressed",
                conveyed_by=pd.conveyed_by,
            )
            for pd in g.dims
            if pd.suppressed
        )
        approved = tuple(
            ApprovedDimension(
                id=DimensionId(g.feature, pd.param.parameter_id),
                # DimParameter.value is a required float. Keep that invariant explicit at
                # the boundary instead of implying a nullable state renderers cannot handle.
                value_text=_fmt(pd.param.value),
                value=float(pd.param.value),
                span=pd.param.span,
                ref=FeatureRef(g.feature),
                kind=pd.param.kind,
                role=pd.param.role,
                discriminator=pd.param.discriminator,
                tolerance=pd.param.tolerance,
            )
            for pd in g.dims
            if not pd.suppressed
            # A datum-coincident shoulder is not a zero-valued dimension the general group
            # may keep after the dedicated ladder rejected its identical endpoints. It is
            # validation-pruned before either compiled representation forms; the ladder
            # compiler owns the axis-specific omission diagnostic.
            and not (
                isinstance(g.feature, StepLevelFeature)
                and pd.param.role == "step_position"
                and _is_zero_step_position(pd.param.value)
            )
        )
        out.append(
            ApprovedGroup(
                feature_kind=g.feature_kind,
                view=g.view,
                anchor=g.anchor,
                ref=FeatureRef(g.feature),
                facts=FeatureFacts(g.feature),
                dims=approved,
            )
        )
    return out, omissions


def compile_dimensions(
    model: PartModel, *, include_overall: bool = True, groups=None, planned_views=None
) -> RenderableDimensionPlan:
    """Compile *model* into the dimensions that will be drawn, and the ones that will not.

    One pass, one policy. Everything a renderer needs to know about WHAT to draw is decided
    here; everything about WHERE stays in the renderer.

    *groups* accepts a `plan_dimensions` result the caller already has, so the engine's
    plan-once invariant holds through the migration instead of the compiler quietly
    re-planning behind it (#923 review).

    *planned_views* is the ADR 0018 pre-projection constraint used only when the compiler
    must plan for itself.  Callers supplying *groups* have already resolved that constraint;
    the exact model/groups identity check below prevents substituting another plan.
    """
    planned = (
        tuple(groups)
        if groups is not None
        else tuple(plan_dimensions(model, planned_views=planned_views))
    )
    if groups is not None:
        model_feature_ids = {id(feature) for feature in model.features}
        foreign = [g.feature for g in planned if id(g.feature) not in model_feature_ids]
        if foreign:
            raise ValueError(
                "compile_dimensions(groups=...): every planned group must come from "
                "this exact PartModel; refusing a mismatched model/groups pair"
            )
    marked = _suppressed_dims(model, planned)
    ladders, omissions = _compile_step_ladders(model, marked)
    groups_out, group_omissions = _compile_groups(planned)
    step_chain_approved = any(
        group.feature_kind == "step"
        and group.facts.frame.axis == "z"
        and group.dim(kind="length") is not None
        for group in groups_out
    )
    overall, contingency, height_omissions = _compile_overall_height(
        model,
        marked,
        include_overall=include_overall,
        step_chain_approved=step_chain_approved,
    )
    height_ladder = overall or (contingency.fallback if contingency is not None else None)
    if height_ladder is not None:
        # The bespoke compiler can deliberately override the planner's old Z-turn
        # suppression, either as direct content or as a contingency. Its diagnostic is the
        # canonical one, so do not retain the general traversal's duplicate (whose legacy
        # wording differs and would survive value/reason deduplication).
        overall_feature = resolve_feature(height_ladder.ref)
        group_omissions = [
            omission
            for omission in group_omissions
            if not (
                omission.feature is overall_feature and omission.parameter_id == "height.length"
            )
        ]
    if overall is not None:
        ladders.append(overall)
    locations, location_omissions = _compile_locations(model)
    slot_positions, slot_omissions = _compile_slot_positions(model)
    locations.extend(slot_positions)
    location_omissions.extend(slot_omissions)
    off_axis, off_axis_omissions = _compile_off_axis_hole_locations(model)
    locations.extend(off_axis)
    location_omissions.extend(off_axis_omissions)
    return RenderableDimensionPlan(
        groups=tuple(groups_out),
        ladders=tuple(ladders),
        locations=tuple(locations),
        contingencies=(contingency,) if contingency is not None else (),
        diagnostics=_dedupe_omissions(
            omissions, height_omissions, location_omissions, group_omissions
        ),
    )


def _dedupe_omissions(*sources: list[Omission]) -> tuple[Omission, ...]:
    """Drop only the omissions two DIFFERENT compilers reported about one fact.

    An authored set records the overall height twice — `_compile_overall_height`'s bespoke
    branch and the general group traversal both notice it, since the envelope's `height`
    parameter is in its group too. That duplication makes a consumer over-count suppressions
    and a build-diff show churn that did not happen.

    But a repetition *within* one source is not a duplicate. `_compile_off_axis_hole_locations`
    deliberately emits one omission per member, and every member of a grouped hole shares the
    same `HoleFeature` — so a naive key of (feature, parameter, reason) collapses four real
    member facts into one and silently loses positions. Losing a real row is worse than the
    duplicate it was meant to fix (Codex #996 r6).

    Hence: cross-source only. Each source keeps its own repetitions; a key already seen in an
    EARLIER source is dropped from a later one.
    """

    def key(o: Omission) -> tuple[int, str, object, str]:
        # `value` is in the key deliberately. Two sources reporting one parameter with
        # DIFFERENT values are not one fact reported twice — they are two compilers
        # disagreeing, and an audit should surface that rather than silently keep whichever
        # source happens to run first.
        #
        # ROUNDED, because these values carry real float jitter: an X-turned envelope reports
        # its height as 20.000000000000007 by one route and 20.0 by another. Keying on the raw
        # float would split a genuine duplicate back into two rows on noise, re-creating the
        # over-reporting this function exists to remove. A disagreement that matters differs by
        # far more than a micron.
        v = round(o.value, 6) if isinstance(o.value, float) else o.value
        return (id(o.feature), o.parameter_id, v, o.reason)

    seen: set[tuple[int, str, object, str]] = set()
    out: list[Omission] = []
    for source in sources:
        out += [o for o in source if key(o) not in seen]
        seen |= {key(o) for o in source}
    return tuple(out)
