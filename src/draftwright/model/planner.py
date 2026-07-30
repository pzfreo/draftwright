"""planner — the dimensioning back-end over the IR (ADR 0008).

One rule set over `DimParameter`s, regardless of which feature produced them. The
contract was tightened twice under adversarial review of the counterbore work:

- **Grouping with an anchor and one view.** A feature's parameters form one
  `DimensionGroup` carrying the feature's `anchor` (so it can be placed) and a
  single `view` (so a compound callout — a hole's bore + counterbore + depth —
  renders as one callout in one place, not split across views/kinds).
- **No value-blind collapse.** The planner does *not* de-duplicate parameters by
  value: a `counterbore` ø16 and a `boss` ø16 are distinct, and a 10×10 pocket's
  two orthogonal 10 mm lengths are distinct. Features own their parameters; they
  do not emit spurious duplicates. Genuine redundancy/count of *repeated identical
  features* ("3× ø8") is upstream (pattern detection), not here.

Current scope: convention + group view selection. Richer render intents
(suppression / datum / grouping) are the next planned increment (#250); the full
ISO/ASME rule set grows here as real features demand it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from draftwright._geometry import _END_ON, HoleRef
from draftwright.model.ir import (
    Datum,
    DimParameter,
    Feature,
    HoleFeature,
    PadFeature,
    ParameterId,
    PartModel,
    PatternFeature,
    PocketFeature,
    PocketPatternFeature,
    Point,
    SlotFeature,
)

#: The `reason` marking a measurement the AUTHORED set left out — the author's own
#: omission, as opposed to a rule-set suppression. Defined here, beside the code that
#: sets it, and re-exported by `model/compiled.py` so the two cannot drift.
_AUTHORED_OMISSION = "not in the authored dimension set"

# How each (role, kind) is drawn. Defaults keep the table small.
_CONVENTION = {
    ("step", "length"): "chain",
    ("step", "diameter"): "leader",
    ("bore", "diameter"): "leader",
    ("bore", "depth"): "leader",
    ("counterbore", "diameter"): "leader",
    ("counterbore", "depth"): "leader",
    ("spotface", "diameter"): "leader",
    ("spotface", "depth"): "leader",
    ("boss", "diameter"): "leader",
    ("boss_height", "length"): "linear",
    ("bolt_circle", "diameter"): "leader",  # BCD (a pitch-circle diameter)
    ("pitch", "length"): "pitch",  # linear-array pitch — distinct from a plain linear dim
    ("grid_pitch", "length"): "pitch",
    ("chamfer", "length"): "leader",  # C{leg} / {leg}×{angle}° leader callout (#724)
    ("fillet", "radius"): "leader",  # R{radius} (grouped n× R) leader callout (#725)
    ("flat", "length"): "leader",  # {across} A/F across-flats leader callout (#726)
    # One groove callout carries BOTH params: {width} WIDE × ø{diameter} (#727)
    ("groove", "length"): "leader",
    ("groove", "diameter"): "leader",
    # One pocket callout carries all THREE params: W × L × D DEEP (#728)
    ("pocket_width", "length"): "leader",
    ("pocket_length", "length"): "leader",
    ("pocket_depth", "length"): "leader",
    # A plate thickness is a linear Dimension. That IS the table default, but the
    # entry is explicit anyway (#744 review): this table is the one convention
    # registry, and a planner-fed kind relying on the implicit default would erode
    # that — unknown pairs should eventually fail loudly, not silently go linear.
    ("thickness", "length"): "linear",
    # A slot's width + length are linear Dimensions with witness lines (#730) —
    # again the table default, entered explicitly per the #744 review rule above.
    ("slot_width", "length"): "linear",
    ("slot_length", "length"): "linear",
    ("pad_width", "length"): "linear",
    ("pad_length", "length"): "linear",
}


@dataclass(frozen=True)
class PlannedDimension:
    """A parameter plus its render *intent* — the planner's decision about how/whether
    it is drawn, leaving the layout (zones/sides/coordinates) to the renderer
    (ADR 0008 Amendment 4). `suppressed`/`reason` carry *model-level* suppression
    (the planner sees the model, not the drawing-in-progress, so render-state
    suppression like "diameter already mentioned" stays in the renderer). `datum` is
    the reference a positional dim measures from, resolved from `param.refs` against
    `model.datums` — reserved for the location-dimension work (#238); ``None`` until
    a feature emits datum-referenced params."""

    param: DimParameter
    convention: str  # "chain" | "ordinate" | "leader" | "linear" | "pitch"
    suppressed: bool = False
    reason: str | None = None
    datum: Datum | None = None
    # The source IR feature this dim locates — carried so the renderer can record
    # provenance (ADR 0010). ``None`` for dims not tied to a single feature.
    feature: Feature | None = None


# Correlated sets (ADR 0016 identity, tier 3): exact ``(feature kind, role)`` pairs
# whose parameters are ONE addressable dimension holding N members — a `step_height`
# ladder, a rotational body's concentric bores. `ir.py` states the rule at the source:
# they are "correlated SETS routed as a whole … a single `role=` intent rebuilds the
# whole ladder", so their members are deliberately NOT separately addressable.
#
# **Declared, never inferred.** Grouping must not fall out of two parameters happening
# to share a derived id: without this table there is no way to tell an intentional set
# from an accidental collision, and a grid pitch that forgot its discriminator would
# silently become a "correlated set" instead of failing the uniqueness audit.
#
# Scoped to the pair, never a bare role or a whole feature — a future feature reusing
# `step_height` must not inherit the exemption, and `rotational`'s `od` is a plain
# singleton that stays individually addressable.
CORRELATED_SETS: frozenset[tuple[str, str]] = frozenset(
    {
        ("step_level", "step_height"),
        ("step_level", "step_position"),
        # Provisional (ADR 0016 tier 3): whether a rotational body's concentric bores
        # stay one identity or split into addressable members follows the #754
        # planner-routing migration; one identity until something says otherwise.
        ("rotational", "bore"),
    }
)


@dataclass(frozen=True)
class AddressableDimension:
    """One **addressable** measurement (ADR 0016) — the unit a `dimension(...)` line
    names, and the thing suppression, provenance and de-duplication key on.

    Usually one member. A correlated set holds N, and those members are *not*
    separately addressable: commenting out the line drops the whole ladder, which is
    what the grouped auto-passes already do.

    Members are `PlannedDimension`s rather than raw `DimParameter`s so the planner's
    decisions — convention, model-level suppression and its reason, the datum,
    provenance — survive the grouping instead of needing a second parallel structure
    alongside it.

    **Scope: the feature parameters that flow through `plan_dimensions`.** A location has
    no entry here — `plan_dimensions` skips every `location`-kind parameter and
    `plan_locations` returns a flat cross-feature list of bare `PlannedDimension`s, deduped
    by ref point, that never enters a `DimensionGroup`.

    That no longer means a location is unaddressable. :data:`_LOCATION_ROLE` gives each
    locatable kind a role, which is what `sheet.dimension(bore, "location")` names and what
    an authored set omits (#925) — one unit per feature. The finer question **#883** asks
    (is a grouped hole's location one unit or one per member? does a deduped coincident ref
    belong to which feature?) is about NAMING, and omission is well-formed either way, so
    it no longer blocks the `"location"` role."""

    id: ParameterId
    members: tuple[PlannedDimension, ...]


@dataclass(frozen=True)
class DimensionId:
    """The identity of one addressable dimension: **which feature**, and **which of its
    measurements** (ADR 0016).

    `(feature, role)` is how a caller *addresses* a dimension; this is the key underneath
    it — `parameter` is an `AddressableDimension`'s `ParameterId`, which already carries
    the `kind` and any discriminator the role alone cannot supply.

    `feature` is the IR feature itself, compared **structurally** — the frozen-dataclass
    equality every `Feature` already has. Not `is`: re-running a script builds new feature
    objects, so an identity that resolved only against the original instance would break on
    every re-plan, destroying the stability the whole identity model exists for. Structural
    equality also keeps the law dedup depends on — *equal ids are interchangeable in
    lookup* — which `is`-based lookup silently violated, since two structurally identical
    features yield `==` (and hash-colliding) ids.

    A *durable* `FeatureId` is only needed where an intent must survive re-**detection**
    (where the feature values themselves may shift), and ADR 0016 deliberately leaves that
    open — so this type reads "whatever identifies a feature" rather than minting one."""

    feature: Feature
    parameter: ParameterId


def _addressable(feature: Feature, planned: list[PlannedDimension]):
    """Group planned dimensions into addressable units — by **declaration**
    (`CORRELATED_SETS`), never by two ids happening to collide."""
    units: list[AddressableDimension] = []
    open_sets: dict[ParameterId, int] = {}
    for pd in planned:
        pid = pd.param.parameter_id
        if (feature.kind, pd.param.role) in CORRELATED_SETS:
            at = open_sets.get(pid)
            if at is not None:
                unit = units[at]
                units[at] = replace(unit, members=(*unit.members, pd))
                continue
            open_sets[pid] = len(units)
        units.append(AddressableDimension(id=pid, members=(pd,)))
    return tuple(units)


@dataclass(frozen=True)
class DimensionGroup:
    """A feature's planned dimensions, kept together with the source feature and a
    single view so a compound callout renders as one callout in one place.

    Carrying the source `feature` (rather than copying selected fields) keeps the
    plan Open/Closed: a grouped renderer reads whatever metadata it needs —
    `count`/`pattern` for a pattern, a thread spec later — without the plan
    contract growing a field per feature type.

    ``units`` is the ADR 0016 identity layer; ``dims`` flattens it back to the plain
    sequence of planned dimensions. Most renderers want the flat view — a compound
    hole callout reads bore ⌀, depth and counterbore without caring how they group —
    so `dims` stays the reading surface and only code that addresses a *dimension by
    identity* touches `units`."""

    feature: Feature
    view: str  # one view for the whole group
    units: tuple[AddressableDimension, ...]

    @property
    def dims(self) -> tuple[PlannedDimension, ...]:
        """Every planned dimension in the group, ignoring identity grouping."""
        return tuple(m for u in self.units for m in u.members)

    @property
    def dimension_ids(self) -> tuple[DimensionId, ...]:
        """This group's addressable dimensions, by identity — one per unit, so an
        N-member correlated set yields **one** id (ADR 0016)."""
        return tuple(DimensionId(self.feature, u.id) for u in self.units)

    def unit_for(self, dim_id: DimensionId) -> AddressableDimension | None:
        """The addressable dimension *dim_id* names, or `None` if it names another
        feature or a measurement this group does not carry.

        Structural comparison, deliberately — see `DimensionId`. Using `is` here made
        equal ids non-interchangeable in lookup, and broke resolution across re-planning."""
        if dim_id.feature != self.feature:
            return None
        return next((u for u in self.units if u.id == dim_id.parameter), None)

    @property
    def feature_kind(self) -> str:
        return self.feature.kind

    @property
    def anchor(self) -> Point:
        return self.feature.frame.origin


# The PROFILE view per axis — the orthographic view whose page plane CONTAINS the
# axis, so an axial length along it is measurable there (in its `_END_ON` view the
# span is degenerate — a point). The front view is the x–z plane, so X- and Z-turned
# axes both derive to "front" by that one containment rule (X/Z parity); a Y axis
# lies in the side (y–z) plane — the same map the groove profile callouts use.
_PROFILE = {"x": "front", "z": "front", "y": "side"}


def _group_view(feature: Feature) -> str:
    """The single view a feature's callout lands on — derived from the feature's
    axis, never hardcoded, so X and Z are handled by the same rule (parity). A
    diameter callout (hole / boss) reads on the view where the cylinder is end-on
    (z→plan, x→side, y→front). A turned step is the opposite case (#731): its
    length + OD read where the turning axis lies IN-PLANE — `_PROFILE`, not
    `_END_ON` — which derives to "front" for both X- and Z-turned steps because
    the front view is the x–z plane. That matches the renderers per axis:
    `render_step_lengths` draws the chain on the front view (X → horizontal
    above, Z → vertical right) and `render_diameters` hangs its ø row/column off
    the front view (X → row below, Z → column left). A Y-axis step derives to
    its geometric profile ("side"); no step render pipeline consumes Y today
    (`render_diameters` buckets x/z only)."""
    if feature.kind == "step":
        return _PROFILE.get(feature.frame.axis, "front")
    return _END_ON.get(feature.frame.axis, "plan")


def _square_footprint(model: PartModel) -> bool:
    """In-plane width ≈ depth (within 5%) — a single overall dim suffices."""
    size = model.bbox.size  # type: ignore[attr-defined]  # build123d BoundBox
    w, d = float(size.X), float(size.Y)
    return abs(w - d) <= max(w, d) * 0.05


def _suppression(model: PartModel, feature: Feature, param: DimParameter):
    """Model-level suppression intent → ``(suppressed, reason)``. Decisions the
    planner can make from the model alone (ISO 129 no-double-dimensioning):
    a square footprint needs one overall dim, not width+depth; a turned part's
    step-length chain already conveys the length (X) / height (Z), so the envelope
    dim along the turning axis is redundant."""
    if feature.kind != "envelope":
        return False, None
    # A rotational part's OD already conveys its cross-axis extent(s); the envelope
    # dim(s) perpendicular to the turning axis would double-dimension it (#222). The
    # axis-aligned envelope dim (the length) is kept. (The overall *height* dim is the
    # height-ladder renderer's call — it skips it for an X/Y rotational part likewise.)
    rot = next((f for f in model.features if f.kind == "rotational"), None)
    if rot is not None:
        od_perp = {"x": {"depth"}, "y": {"width"}, "z": {"width", "depth"}}[rot.frame.axis]
        if param.role in od_perp:
            return True, f"rotational OD ({rot.frame.axis}-axis) conveys this extent"
    # Keep width as the single representative planar extent. Suppressing both
    # width and depth made square parts lose their plan size entirely (#897).
    if param.role in ("width", "depth") and _square_footprint(model):
        has_profile = any(
            f.kind == "step_level" and getattr(f, "shoulders", ()) for f in model.features
        )
        if not has_profile or param.role == "depth":
            return True, "square footprint (single overall dim suffices)"
    if param.role == "width" and model.orientation == "x":
        return True, "X-turned (step-length chain conveys the length)"
    if param.role == "height" and model.orientation == "z":
        return True, "Z-turned (step-length chain conveys the height)"
    return False, None


def _datum_for(model: PartModel, param: DimParameter) -> Datum | None:
    """The datum a positional param measures from — resolved from ``param.refs``
    against ``model.datums``. ``None`` until a feature emits datum-referenced
    params (the location-dimension work, #238)."""
    if not param.refs:
        return None
    return next((d for d in model.datums if d.id in param.refs), None)


#: Which feature kinds get a datum-referenced position, and what that position is called.
#:
#: The ONE statement of "this feature is locatable". :func:`plan_locations` builds from it,
#: and the authored-set vocabulary derives from it (:func:`location_role`), so a script can
#: name `dimension(hole, "location")` exactly when the planner would emit one. Before #925
#: a location was unnameable — it had no `DimParameter`, so an authored set could neither
#: include nor exclude it, and every location was drawn regardless of what the script
#: declared. A dimension the author cannot address is a dimension the author cannot omit.
_LOCATION_ROLE: dict[type, str] = {
    HoleFeature: "location",
    PatternFeature: "location_pattern",
    PocketFeature: "location_pocket",
    PocketPatternFeature: "location_pocket_pattern",
    PadFeature: "location_pad",
    # A slot's position is datum-referenced too, but from the BOUNDING BOX along its long
    # axis rather than from `datum_xy`, and it is drawn in the slot's own view. So it is
    # addressable here (an authored set can name or omit it) while `plan_locations` skips
    # it — the span is compiled in `model/compiled._compile_slot_positions`, which has the
    # bbox. Listing it in one table with the rest is what keeps "which features have a
    # position" a single answer.
    SlotFeature: "location_slot",
}


#: Which DATUM each kind's position is measured from, per orientation — and therefore
#: whether it has one at all.
#:
#: ``"datum_xy"`` is the Z-normal ladder `plan_locations` owns. ``"bbox"`` is a position
#: measured from the bounding box in the feature's own view, compiled in `model/compiled`
#: (a slot's near-end offset, a side-drilled hole's offset + height). ``None`` means the
#: engine draws no position for that feature at all.
#:
#: **The eligibility question is answered once, here, and read three times** — by
#: `plan_locations`, by the bbox compilers, and by the authored vocabulary. Re-deriving it
#: is what broke twice: `location_role` said a hole is locatable while `plan_locations`
#: said only a Z-normal one is (side-drilled positions drawn outside the plan), and then
#: it said a PATTERN is locatable while neither compiler emits one off-axis — so
#: `dimension(x_pattern, "location")` was ACCEPTED and silently produced nothing, the exact
#: failure `_check_authored_targets` exists to prevent (#925 review).
#:
#: An off-axis pattern is ``None`` because the engine has never drawn one: the off-axis
#: pass excluded patterns by construction. Compiling one would be new output with its own
#: layout consequences, not a boundary fix — so the vocabulary tells the truth about
#: today's engine and the author gets an error instead of a blank drawing.
def location_datum(feature) -> str | None:
    """``"datum_xy"``, ``"bbox"``, or ``None`` — where *feature*'s position is measured
    from, or that it has none. See :data:`_LOCATION_ROLE`."""
    if type(feature) not in _LOCATION_ROLE:
        return None
    if isinstance(feature, SlotFeature):
        return "bbox"  # near-end offset along its long axis, in its own view
    if isinstance(feature, PocketFeature):
        return "datum_xy"  # every orientation: its two in-plane coordinates
    if isinstance(feature, HoleFeature):
        return "datum_xy" if feature.frame.axis == "z" else "bbox"
    # Patterns and pads: the plan-X / side-Y ladder only, so Z-normal only.
    return "datum_xy" if feature.frame.axis == "z" else None


#: The role every authored entry uses to name a location, whatever the per-kind role above.
#:
#: One coarse unit per feature, deliberately: #883 asks whether a patterned hole's location
#: is one addressable thing or one per member, and that is a question about NAMING. Omission
#: does not need the answer — "this feature's position is not in the set" is well-formed at
#: either granularity, and a finer id (`location.member.3`) refines this one later without
#: contradicting it. So the completeness contract does not wait on #883.
LOCATION_ROLE = "location"


def location_role(feature) -> str | None:
    """The role *feature*'s position is planned under, or ``None`` if it has none.

    Derived from :func:`location_datum` rather than from the kind table alone, so the
    authored vocabulary cannot accept a target no compiler will produce."""
    if location_datum(feature) is None:
        return None
    return _LOCATION_ROLE.get(type(feature))


def plan_locations(model: PartModel) -> list[PlannedDimension]:
    """Plan hole **location** dimensions — the *intent*: which features get located
    and from which datum. The renderer owns the tier/legibility/zone layout
    (Amendment 4). One ref per un-patterned Z-hole + one per Z-pattern (bolt-circle
    centre, else the array member nearest the datum); coincident refs deduped. Each
    returned `PlannedDimension` carries the datum and a `span` of datum → ref; the
    renderer derives the X (plan) and Y (side) distances from it (#238).

    Under an AUTHORED set (#876) a feature whose position the script did not name is
    marked suppressed here, exactly as `plan_dimensions` marks an unnamed parameter — a
    location is a dimension and obeys the same completeness rule. Two consequences worth
    stating:

    - The coincident-ref DEDUP runs over the surviving refs only. Deduping first would let
      an omitted feature's ref absorb an approved sibling's and take the drawing's position
      dim down with it — the author would have named a measurement and got nothing.
    - Everything else is unchanged when no set is authored: every ref survives, so the
      dedup sees the same input it always did.
    """
    datum = next((d for d in model.datums if d.id == "datum_xy"), None)
    if datum is None:
        return []
    dx, dy, dz = datum.at
    # (ref_point, role): role distinguishes a hole ref from a pattern ref — the
    # renderer's concentric-bore exclusion applies to holes only (a bolt circle on
    # the axis is still located by its centre), matching the engine.
    # (ref_point, role, source feature): the feature is carried so the renderer can
    # record provenance on the placed location dim (ADR 0010).
    refs: list[tuple[Point, str, Feature]] = []
    for f in model.features:
        # Which features get a `datum_xy` position — including the orientation rule (the
        # hole/pattern/pad ladder is Z-normal; a pocket's two in-plane coordinates belong
        # in the view normal to its opening, for every orientation) — is `location_datum`'s
        # single answer. This loop used to restate the orientation half inline, which is
        # how it came to disagree with the kind table (#925 review).
        role = location_role(f)
        if role is None or location_datum(f) != "datum_xy":
            continue
        if isinstance(f, HoleFeature):
            # un-patterned holes — a HoleFeature may group identical holes
            for m in f.members or (f.frame.origin,):
                refs.append((m, role, f))
        elif isinstance(f, PatternFeature):
            if f.pattern == "bolt_circle":
                refs.append((f.frame.origin, role, f))
            elif f.members:
                near = min(f.members, key=lambda m: (m[0] - dx) ** 2 + (m[1] - dy) ** 2)
                refs.append((near, role, f))
        elif isinstance(f, PocketFeature):
            if f.edge_anchored:
                continue
            # A recognised pocket frame may be anchored at an opening corner;
            # location furniture defines the in-plane centre, which is expressed
            # explicitly by lo/hi and w_center for every principal orientation.
            centre = list(f.frame.origin)
            centre["xyz".index(f.long_axis)] = (f.lo + f.hi) / 2
            centre["xyz".index(f.width_axis)] = f.w_center
            ref = (centre[0], centre[1], centre[2])
            refs.append((ref, role, f))
        else:
            refs.append((f.frame.origin, role, f))

    def _plan(r, role, feat, *, suppressed=False, reason=None) -> PlannedDimension:
        return PlannedDimension(
            param=DimParameter(
                kind="location",
                role=role,
                value=0.0,  # a location is a 2-D offset; the renderer reads the span
                span=((dx, dy, dz), (r[0], r[1], r[2])),
                refs=(datum.id,),
            ),
            convention="location",
            suppressed=suppressed,
            reason=reason,
            datum=datum,
            feature=feat,
        )

    omitted: list[PlannedDimension] = []
    if model.authored_dimensions is not None:
        kept: list[tuple[Point, str, Feature]] = []
        for r, role, feat in refs:
            if _authored_location_for(model, feat) is None:
                omitted.append(_plan(r, role, feat, suppressed=True, reason=_AUTHORED_OMISSION))
            else:
                kept.append((r, role, feat))
        refs = kept
    unique: list[tuple[Point, str, Feature]] = []
    for r, role, feat in refs:
        if not any(abs(r[0] - u[0]) < 0.5 and abs(r[1] - u[1]) < 0.5 for u, _, _ in unique):
            unique.append((r, role, feat))
    return [_plan(r, role, feat) for r, role, feat in unique] + omitted


def _request_for(model, feature, param):
    """The caller's `add_dimension(...)` for this parameter, or ``None``.

    Matched on ``(feature, role)`` plus the discriminator where the role alone is
    ambiguous — a grid pattern carries two ``grid_pitch`` params and a request naming
    neither would be a coin toss, so it must name one (the facade raises before we get
    here; this stays strict so a hand-built `PartModel` cannot slip past)."""
    for req in model.requested_dimensions:
        # Identity, NOT structural equality. Two equal-valued features are two distinct
        # targets — a part with two identical envelopes or holes must be able to request
        # a dimension on one of them. (`DimensionId` deliberately compares structurally,
        # #871, because an id must survive a re-plan that rebuilds the objects; a request
        # targets one declared instance within a single build, so the rules differ.)
        if req.feature is not feature:
            continue
        # A request names either a full `ParameterId` ("bore.depth" — one measurement)
        # or a bare role ("bore" — every measurement under it). Both are useful: the
        # dotted form is the exact identity #871 built, the short form is what a caller
        # reaches for when the role is unambiguous. ADR 0016 leaves this vocabulary open.
        if "." in req.role:
            if req.role != param.parameter_id:
                continue
        elif req.role != param.role:
            continue
        if req.discriminator is not None and req.discriminator != param.discriminator:
            continue
        return req
    return None


def _authored_addresses(authored, feature, param) -> bool:
    """Does this ONE authored entry name *param* on *feature*?

    Split out from :func:`_authored_for` so `_check_authored_targets` can ask about a single
    entry. Asking "did this FEATURE match anything" let one valid entry vouch for every
    invalid one beside it: `dimension(pattern, "bore.diameter")` plus
    `dimension(pattern, "location")` passed the check because the bore matched, and the
    unmatched location then silently produced nothing (#925 review)."""
    if authored.feature is not feature:
        return False
    if "." in authored.role:
        if authored.role != param.parameter_id:
            return False
    elif authored.role != param.role:
        return False
    return authored.discriminator is None or authored.discriminator == param.discriminator


def _authored_for(model, feature, param):
    """The caller's `dimension(...)` naming this parameter, or ``None`` if it is omitted.

    Same matching as :func:`_request_for` — the two verbs address a measurement identically;
    what differs is what the answer means. `add_dimension` ADDS to the planner's set, so a
    miss leaves the rule set's own decision standing. `dimension` DECLARES the set, so a miss
    is an omission and the measurement is suppressed."""
    for authored in model.authored_dimensions or ():
        if _authored_addresses(authored, feature, param):
            return authored
    return None


def authored_location_omitted(model, feature) -> bool:
    """Did an AUTHORED set leave *feature*'s position out?

    ``False`` when no set is authored (the rule set draws every position it plans) and when
    the set names this feature's location. Exported so the compiler can gate the positions
    it derives itself — a slot's, which measures from the bounding box — against the same
    authored decision `plan_locations` applies to its own."""
    if model.authored_dimensions is None:
        return False
    return _authored_location_for(model, feature) is None


def _authored_location_for(model, feature):
    """The caller's ``dimension(feature, "location")``, or ``None`` if it is omitted.

    Separate from :func:`_authored_for` because a location has no `DimParameter` to match
    against: it is synthesized from the feature and the datum, so the entry names the
    feature and the coarse role and nothing else (see :data:`LOCATION_ROLE`)."""
    for authored in model.authored_dimensions or ():
        if authored.feature is feature and authored.role == LOCATION_ROLE:
            return authored
    return None


def _check_authored_targets(model: PartModel) -> None:
    """Every authored entry must address a measurement this model actually has.

    An authored entry that hits nothing is not a no-op the way a stray `add_dimension`
    is: `dimension(...)` DECLARES the set, so an entry matching no parameter leaves that
    feature — potentially the whole drawing — silently blank, and the caller sees a clean
    build with no dimensions rather than an error (#921 review round 4). This is the
    failure mode #630/#631/#632 rank as worse than a visible raise.

    The `Sheet` façade cannot reach this: it resolves the role when the verb is called
    and materialises entries against the FINAL features at build. It is reachable through
    the ADR 0011 public input — `build_drawing(part, model=…, authored=…)` with a
    hand-built `PartModel` — where a feature object may be an equal-but-distinct CLONE of
    the one in `model.features`. Matching stays identity-based on purpose (see
    `_request_for`: two equal-valued features are two distinct targets, so structural
    equality would make an authored dimension on one of a pair of identical holes
    ambiguous). What changes is that a miss now says so."""
    for authored in model.authored_dimensions or ():
        if authored.role == LOCATION_ROLE and any(
            f is authored.feature and location_role(f) is not None for f in model.features
        ):
            continue
        if not any(
            _authored_addresses(authored, feature, p)
            for feature in model.features
            if authored.feature is feature
            for p in feature.parameters()
        ):
            in_model = any(f is authored.feature for f in model.features)
            if in_model and authored.role == LOCATION_ROLE:
                # Distinguish "this kind has no position" from "this kind has one, but not
                # in this orientation" — the second reads as an engine limit, which it is,
                # and a bare "no location measurement" would send the author looking for a
                # typo instead.
                raise ValueError(
                    f"authored dimension 'location' matches nothing: the engine draws no "
                    f"position for a {authored.feature.frame.axis}-axis "
                    f"{authored.feature.kind}. An authored set declares the complete "
                    "dimensioning, so an entry that addresses nothing would silently leave "
                    "the drawing blank."
                )
            why = (
                f"no {authored.role!r} measurement on that {authored.feature.kind}"
                if in_model
                else "that feature object is not in model.features (an equal-valued COPY "
                "is a different target — pass the instance the model holds)"
            )
            raise ValueError(
                f"authored dimension {authored.role!r} matches nothing: {why}. An authored "
                "set declares the complete dimensioning, so an entry that addresses nothing "
                "would silently leave the drawing blank."
            )


def plan_dimensions(model: PartModel) -> list[DimensionGroup]:
    """Plan each feature's parameters into one `DimensionGroup` (anchor + single
    view + planned dims, each carrying its render intent — convention, model-level
    suppression, datum). No cross- or within-feature value de-duplication."""
    if model.authored_dimensions is not None:
        _check_authored_targets(model)
    groups: list[DimensionGroup] = []
    for feature in model.features:
        dims = []
        for p in feature.parameters():
            if p.kind == "location":
                continue
            # An authored ± tolerance (ADR 0011 §4 / P2a) rides on the decorations side-
            # layer; fold it onto the param so every renderer sees one carrier. A
            # ROLE-keyed (feature, kind, role) decoration wins — it tolerances ONE param
            # of a multi-param kind (#746, e.g. a pocket's depth, or a rotational OD vs
            # its bores). A KIND-keyed (feature, kind) decoration is the back-compat
            # fallback that folds onto EVERY param of that kind. `kind` stays in the key —
            # a step's length and diameter share role="step", so role alone can't tell
            # them apart.
            tol = model.decorations.get((feature, p.kind, p.role))
            if tol is None:
                tol = model.decorations.get((feature, p.kind))
            if tol is not None:
                p = replace(p, tolerance=tol)
            suppressed, reason = _suppression(model, feature, p)
            # A caller's `add_dimension(...)` overrides the rule set's suppression for
            # exactly the measurement it names (ADR 0016 / #872). It changes SELECTION
            # only — the value still comes from the geometry, so a request can never
            # introduce a number the part does not carry. Requesting something the
            # planner already emits is a deliberate no-op (idempotence gate): a script
            # must be able to ask without first knowing the rule set's mind.
            request = _request_for(model, feature, p)
            if request is not None:
                suppressed, reason = False, None
            if model.authored_dimensions is not None:
                # An authored set REPLACES the rule set rather than adding to it: what the
                # script lists is what the drawing carries, and everything else is omitted.
                # Marked, not filtered (#875) — the value survives on the group so the
                # omission stays inspectable, and the compound-callout dependency rules
                # still refuse to orphan half a term.
                if _authored_for(model, feature, p) is None:
                    suppressed, reason = True, _AUTHORED_OMISSION
                else:
                    suppressed, reason = False, None
            dims.append(
                PlannedDimension(
                    param=p,
                    convention=_CONVENTION.get((p.role, p.kind), "linear"),
                    suppressed=suppressed,
                    reason=reason,
                    datum=_datum_for(model, p),
                )
            )
        if dims:
            groups.append(
                DimensionGroup(
                    feature=feature,
                    view=_group_view(feature),
                    units=_addressable(feature, dims),
                )
            )
    return groups


@dataclass(frozen=True)
class SectionPlan:
    """A planned full section A–A: the plane is normal to Y at ``cut_y``, parallel to
    the front view. The *intent* (does the part need a section, and where the plane
    sits) — the rendering machinery (cut / hatch / cutting-plane arrows) stays shared
    infrastructure that consumes this (ADR 0008 Amendment 4 / #207)."""

    cut_y: float


def plan_sections(model: PartModel, feature_keys: set[HoleRef]) -> SectionPlan | None:
    """Decide whether a part needs a full section A–A, and where the plane cuts.

    Trigger: any Z-axis hole/pattern whose bore has a counterbore, spotface, or a
    non-through bottom — its internal profile is hidden-line-only in every standard
    view. The cut plane passes through the **densest row** of qualifying hole axes
    (ISO practice), tie-broken toward the part centre. Only holes whose positions are
    in *feature_keys* count (so a rotational part's concentric bores, dimensioned by
    the centreline leaders, don't drive a section). ``None`` when no section is
    warranted.

    An **explicit** request (``decorations["section"]``, the ADR 0011 ``Sheet.section``
    verb, #841) forces a cut at that Y regardless of the auto trigger — so a blind
    pocket, which no hole gate recognises, still gets its floor/depth section."""
    requested = model.decorations.get("section")
    if requested is not None:
        return SectionPlan(cut_y=float(requested))
    qual_ys: list[float] = []
    for f in model.features:
        if not isinstance(f, HoleFeature | PatternFeature) or f.frame.axis != "z":
            continue
        bore = f.member if isinstance(f, PatternFeature) else f  # the bore-carrying HoleFeature
        if bore.cbore is None and bore.spotface is None and bore.through:
            continue
        for m in f.members or (f.frame.origin,):
            if HoleRef.of(m) in feature_keys:
                qual_ys.append(m[1])
    if not qual_ys:
        return None
    cy = model.bbox.center().Y  # type: ignore[attr-defined]  # build123d BoundBox
    cut_y = max(
        {round(y, 1) for y in qual_ys},
        key=lambda v: (sum(1 for y in qual_ys if abs(y - v) <= 0.5), -abs(v - cy)),
    )
    return SectionPlan(cut_y=cut_y)
