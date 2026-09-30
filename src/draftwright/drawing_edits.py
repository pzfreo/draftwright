"""Feature edit decisions for the Drawing facade (ADR 1 / ADR 2 / ADR 4).

Drawing owns mutable state and passes the exact inputs and callbacks for each edit.
This rank-5 module neither constructs BuildState nor reads Drawing private attributes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from draftwright._core import _dim, _fmt, _font_safe_text, _tol_suffix
from draftwright._geometry import _END_ON
from draftwright.annotations._common import PlacementContext
from draftwright.intent_routing import Intent
from draftwright.view_plan import PRINCIPAL_VIEW_NAMES


@dataclass(frozen=True)
class _DimensionBuild:
    """Inputs retained until a deferred dimension's corridor is solved."""

    owner: EditOperations
    p1: tuple[float, float, float]
    p2: tuple[float, float, float]
    side: str
    axis_index: int
    kwargs: dict[str, Any]

    def __call__(self, pos: float) -> Any:
        if self.side in ("right", "above"):
            dist = pos - max(p[self.axis_index] for p in (self.p1, self.p2))
        else:
            dist = min(p[self.axis_index] for p in (self.p1, self.p2)) - pos
        dim = _dim(self.p1, self.p2, self.side, max(dist, 4.0), self.owner.draft, **self.kwargs)
        return dim


class EditOperations:
    """One short-lived edit port over explicit Drawing-owned state."""

    def __init__(
        self,
        drawing,
        *,
        analysis,
        model,
        build,
        registry,
        coverage,
        intents,
        defer_intents,
        document_member,
        document_source_annotation_ids,
        record_build_issue,
        place_dim,
        machined_callout_kinds: tuple[str, ...],
    ) -> None:
        self.drawing = drawing
        self.analysis = analysis
        self.part_model = model
        self.build = build
        self.registry = registry
        self.coverage = coverage
        self.intents = intents
        self.defer_intents = defer_intents
        self.document_member = document_member
        self.document_source_annotation_ids = document_source_annotation_ids
        self.record_build_issue = record_build_issue
        self.place_dim = place_dim
        self.machined_callout_kinds = machined_callout_kinds
        self.items = drawing.items
        self.views = drawing.views
        self.draft = drawing.draft
        self.scale = drawing.scale
        self.at = drawing.at
        self.model = drawing.model
        self.pin = drawing.pin
        self.deferred = drawing.deferred
        self.iter_annotations = drawing.iter_annotations
        self.annotations = drawing.annotations

    @staticmethod
    def _derive_span(feature, param):
        """Model-space ``(lo, hi)`` endpoints for a value-only *linear* param whose geometry
        the feature carries (#411), or ``None`` for a callout param with no linear span.

        Slots and pads: the width dim spans ``width_axis`` across
        ``w_center ± width/2`` (at the length midpoint); the length dim spans
        ``long_axis`` ``lo → hi`` (at the centre line) — the same endpoints
        ``render_slots`` measures."""
        feature_kind = getattr(feature, "kind", None)
        if feature_kind in ("slot", "pad"):
            ax = {"x": 0, "y": 1, "z": 2}
            li, wi = ax[feature.long_axis], ax[feature.width_axis]
            a = list(feature.frame.origin)
            b = list(feature.frame.origin)
            if param.role == f"{feature_kind}_length":
                a[li], b[li] = feature.lo, feature.hi
                a[wi] = b[wi] = feature.w_center
            elif param.role == f"{feature_kind}_width":
                mid = (feature.lo + feature.hi) / 2
                half = feature.width / 2
                a[wi], b[wi] = feature.w_center - half, feature.w_center + half
                a[li] = b[li] = mid
            else:
                return None
            return tuple(a), tuple(b)
        return None

    def _resolve_dimension_span(self, feature, param, *, role=None, view=None):
        """Return ``(param_record, view, p1, p2)`` for a feature linear dimension."""
        _ortho = PRINCIPAL_VIEW_NAMES
        if view is not None and view not in _ortho:
            raise ValueError(
                f"view must be one of {_ortho}, not {view!r} (it foreshortens the span)"
            )
        parameters = feature.parameters()
        exact = [q for q in parameters if param in (q.parameter_id, q.discriminator)]
        matches = (
            [q for q in exact if role is None or q.role == role]
            if exact
            else [q for q in parameters if q.kind == param and (role is None or q.role == role)]
        )
        if not matches:
            r = f"/{role!r}" if role else ""
            raise ValueError(
                f"{type(feature).__name__} has no '{param}'{r} parameter to dimension"
            )
        if len(matches) > 1:
            ids = sorted(q.parameter_id for q in matches)
            raise ValueError(
                f"{type(feature).__name__} has {len(matches)} '{param}' params {ids} — pass "
                "role= or an exact parameter id/discriminator to choose one"
            )
        # A span-carrying param (a step length, a location) gives its endpoints directly;
        # a value-only linear param (a slot's dims) derives them from the feature geometry
        # (#411). A callout param (a hole's diameter/depth) has no linear span at all.
        span = matches[0].span or self._derive_span(feature, matches[0])
        if span is None:
            raise ValueError(
                f"'{param}' (role {matches[0].role!r}) is a leader-callout parameter, not a "
                f"linear dimension — dimension() draws linear dims only (a callout add verb "
                f"is tracked separately)"
            )
        (lo, hi) = span
        p1 = p2 = None
        chosen = view
        automatic_views = tuple(name for name in _ortho if name in self.views)
        if view is None and getattr(feature, "kind", None) == "through_step":
            automatic_views = (_END_ON[feature.axis],)
        for v in [view] if view else automatic_views:
            q1, q2 = self.at(v, *lo), self.at(v, *hi)
            if math.hypot(q2[0] - q1[0], q2[1] - q1[1]) > 1e-6:
                chosen, p1, p2 = v, q1, q2
                break
        if p1 is None:
            raise ValueError(
                f"'{param}' span projects to a point in "
                f"{'the requested view' if view else 'every orthographic view'} — nothing to dimension"
            )
        return matches[0], chosen, p1, p2

    def _resolve_dimension_side(self, feature, param, view, p1, p2, side):
        """Choose a feature's natural corridor when the caller leaves ``side`` implicit."""
        if side is not None:
            return side
        if getattr(feature, "kind", None) != "through_step":
            return "above"
        changed_axis = param.discriminator
        perpendicular = next(axis for axis in "xyz" if axis not in (feature.axis, changed_axis))
        outside = dict(feature.outside_directions)
        probe_world = [(a + b) / 2 for a, b in zip(param.span[0], param.span[1], strict=True)]
        probe_world["xyz".index(perpendicular)] += outside[perpendicular]
        exterior = self.at(view, *probe_world)
        if abs(p2[0] - p1[0]) >= abs(p2[1] - p1[1]):
            return "above" if exterior[1] > (p1[1] + p2[1]) / 2 else "below"
        return "right" if exterior[0] > (p1[0] + p2[0]) / 2 else "left"

    def _angular_dimension_plan(self, feature, options):
        """Compile a referential angle edit with the model's existing decorations."""
        from dataclasses import replace

        from draftwright.model.compiled import compile_dimensions
        from draftwright.model.ir import RequestedDimension

        parameters = feature.parameters()
        matches = [
            parameter
            for parameter in parameters
            if options["param"] == parameter.parameter_id
            or (options["param"] == "angle" and len(parameters) == 1)
        ]
        if len(matches) != 1 or options.get("role") not in (None, "included"):
            raise ValueError(
                "angle edit needs one included-angle measurement: "
                + ", ".join(parameter.parameter_id for parameter in parameters)
            )
        extra = set(options) - {"param", "role", "view", "side", "name", "pin", "priority"}
        if extra:
            raise ValueError(
                f"unsupported angular edit controls: {sorted(extra)}; declare content on Sheet"
            )
        model = self.model()
        if model is None or not any(owner is feature for owner in model.features):
            raise ValueError("angular edit must name an exact feature in the drawing model")
        request = RequestedDimension(
            feature,
            matches[0].parameter_id,
            view=options.get("view"),
            side=options.get("side"),
        )
        return compile_dimensions(
            replace(model, authored_dimensions=(request,), requested_dimensions=())
        )

    def _queue_dimension_intent(self, it, a, *, ctx, used_names=None) -> bool:
        """Queue a pinned/prioritized feature dimension into a shared corridor."""
        from draftwright.annotations._common import CorridorCandidate, register_corridor

        if getattr(it.feature, "kind", None) == "angle":
            from draftwright.annotations.from_model import render_angular_dimensions
            from draftwright.model.compiled import FeatureRef

            plan = self._angular_dimension_plan(it.feature, it.kwargs)
            name = it.kwargs.get("name")
            used_names = used_names if used_names is not None else set()
            if name is None:
                index = 0
                while (name := f"dim_angle{index}") in self.registry or name in used_names:
                    index += 1
            used_names.add(name)
            render_angular_dimensions(
                self.drawing,
                plan,
                a,
                ctx=ctx,
                only={FeatureRef(it.feature)},
                name=name,
                pin=bool(it.kwargs.get("pin")),
                priority=float(it.kwargs.get("priority") or 0.0),
            )
            return True

        side = it.kwargs.get("side")
        view = it.kwargs.get("view")
        zones_name = {
            "front": "fv_zones",
            "plan": "pv_zones",
            "side": "sv_zones",
            "rear": "rv_zones",
        }
        rec, view, p1, p2 = self._resolve_dimension_span(
            it.feature,
            it.kwargs["param"],
            role=it.kwargs.get("role"),
            view=view,
        )
        side = self._resolve_dimension_side(it.feature, rec, view, p1, p2, side)
        if side not in ("above", "below", "left", "right"):
            return False
        from draftwright.model.compiled import DimensionId

        measurement = DimensionId(it.feature, rec.parameter_id)
        measurement_span = rec.span or self._derive_span(it.feature, rec)
        zones = getattr(a, zones_name.get(view, ""), None)
        strip = getattr(zones, side, None) if zones is not None else None
        if strip is None:
            return False

        name = it.kwargs.get("name")
        if name is None:
            used_names = used_names if used_names is not None else set()
            i = 0
            while (name := f"dim_{it.kwargs['param']}{i}") in self.registry or name in used_names:
                i += 1
            used_names.add(name)

        dim_kwargs = {
            k: v
            for k, v in it.kwargs.items()
            if k not in {"param", "role", "side", "view", "name", "pin", "priority", "slot"}
        }
        # Match `_place_dim`: the deferred corridor path must keep authored tolerance in its
        # label even when `pin=True` or `priority=` selects this route.
        tolerance = dim_kwargs.pop("tolerance", None)
        if dim_kwargs.get("label") is None:  # `None` is "auto"; see `_place_dim`.
            page_len = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            dim_kwargs["label"] = _fmt(page_len / self.scale)
        dim_kwargs["label"] = _font_safe_text(
            f"{dim_kwargs['label']}{_tol_suffix(tolerance, self.draft)}"
        )
        slot = it.kwargs.get("slot", 8.0)
        axis = "y" if side in ("above", "below") else "x"
        ax = 1 if axis == "y" else 0
        if side in ("right", "above"):
            natural = max(p[ax] for p in (p1, p2)) + slot
        else:
            natural = min(p[ax] for p in (p1, p2)) - slot
        tier = self.draft.font_size + 2 * self.draft.pad_around_text
        p_lo, p_hi = sorted((p1[1 - ax], p2[1 - ax]))

        def _placed(nm, _pin=it.kwargs.get("pin", False)):
            if _pin:
                self.pin(nm)

        def _drop(nm):
            self.record_build_issue(
                "warning",
                "dimension_dropped",
                f"{nm} not placed (no room on the {view} {side} strip)",
                measurement=measurement,
                measurement_span=measurement_span,
            )

        priority = float(it.kwargs.get("priority", 0.0) or 0.0)
        if it.kwargs.get("pin"):
            priority = max(priority, 100.0)
        register_corridor(
            ctx,
            (view, side),
            strip,
            view,
            axis,
            tier,
            CorridorCandidate(
                name=name,
                build=_DimensionBuild(self, p1, p2, side, ax, dim_kwargs),
                order=(0, natural, name),
                on_place=_placed,
                on_drop=_drop,
                dedup=(view, side, round(p_lo, 6), round(p_hi, 6), rec.role),
                precedence=4,
                priority=priority,
                anchored=bool(it.kwargs.get("pin")),
                natural=natural,
                feature=it.feature,
                measurement=measurement,
                measurement_span=measurement_span,
            ),
        )
        return True

    def dimension(
        self,
        feature,
        param,
        *,
        role=None,
        side=None,
        view=None,
        name=None,
        pin=False,
        priority=0.0,
        **kwargs,
    ):
        """Add a dimension for *feature*'s *param*, attributed to the feature (#398e).

        The feature-referenced **add** verb: pair to :meth:`drop`. *feature* is an IR
        feature from :meth:`model`; *param* is a **linear** parameter kind, exact parameter
        id, or discriminator it exposes — a turned step's ``"length"`` or a through step's
        ``"through_step_leg.length.x"``/``"x"`` (value-only slot geometry is derived here
        via :meth:`_derive_span`).
        The dimension is placed into free strip space and tagged with *feature*, so
        :meth:`drop` / :meth:`annotations_of` find it. Returns the annotation name.

        A feature may expose several params of one kind (an envelope's width/height/depth,
        or a slot's ``slot_width``/``slot_length``, are all ``"length"``); pass ``role=`` or
        an exact parameter id/discriminator to pick one — an ambiguous kind raises rather
        than guessing.

        ``view`` is chosen from the selected principal views (``"front"``/``"plan"``/
        ``"side"``/``"rear"``) where the span projects non-degenerate — a length along the turning
        axis vanishes in its end-on view, so the view follows the geometry. Through-step legs
        share their semantic axis end view and natural outside-corner sides. Pass ``view=``
        to select a principal explicitly (a non-orthographic view foreshortens the span and is
        rejected). An implicit ``side`` is ``"above"`` except for through-step legs, whose
        missing corner selects the natural outside corridor. ``kwargs`` forward to the dimension
        — except ``tolerance=``, which is folded into the label (see :meth:`place_dim`),
        because helpers discard a forwarded tolerance whenever a label is present.
        In deferred mode, ``pin=True`` anchors the dimension at its natural slot coordinate
        inside the shared corridor solve, and ``priority=`` controls over-capacity survival.
        Live placement still uses the single-position escape hatch and pins only the placed
        annotation name.

        Raises ``ValueError`` if the feature has no such param, the kind is ambiguous, or
        *view* is not orthographic. A hole's ``"diameter"``/``"depth"`` are **leader
        callouts**, not linear dimensions, so they raise here — a callout add verb is a
        separate mechanism, tracked apart from this one.
        """
        if self.defer_intents:  # #426: record, don't place — finalize() drains it
            self.intents.append(
                Intent(
                    "dimension",
                    feature,
                    {
                        "param": param,
                        "role": role,
                        "side": side,
                        "view": view,
                        "name": name,
                        "pin": pin,
                        "priority": priority,
                        **kwargs,
                    },
                )
            )
            return ""
        if getattr(feature, "kind", None) == "angle":
            options = dict(
                param=param,
                role=role,
                side=side,
                view=view,
                name=name,
                pin=pin,
                priority=priority,
                **kwargs,
            )
            self._angular_dimension_plan(feature, options)
            if name is None:
                index = 0
                while (name := f"dim_angle{index}") in self.registry:
                    index += 1
            with self.deferred():
                self.drawing.dimension(
                    feature,
                    param,
                    role=role,
                    side=side,
                    view=view,
                    name=name,
                    pin=pin,
                    priority=priority,
                    **kwargs,
                )
            return name
        rec, view, p1, p2 = self._resolve_dimension_span(feature, param, role=role, view=view)
        side = self._resolve_dimension_side(feature, rec, view, p1, p2, side)
        from draftwright.model.compiled import DimensionId

        measurement = DimensionId(feature, rec.parameter_id)
        if name is None:
            i = 0
            while (name := f"dim_{param}{i}") in self.registry:
                i += 1
        # Correlated ladder members can share one public identity; keep the exact
        # compiler-owned world span on this placement for occurrence coverage.
        self.place_dim(
            p1,
            p2,
            side,
            view,
            self.draft,
            name=name,
            feature=feature,
            measurement=measurement,
            measurement_span=rec.span or self._derive_span(feature, rec),
            **kwargs,
        )
        if pin:
            self.pin(name)
        return name

    def callout(self, feature, *, view=None, name=None) -> str:
        """Add a **ø leader callout** for *feature* (#414/#419) — the callout half of the
        feature-referenced **add** surface, symmetric with :meth:`drop`.

        Where :meth:`dimension` draws a linear dim, ``callout`` draws a leader: for a
        **hole/pattern**, the ø / ``n×`` / through-or-depth / counterbore callout (the same
        text the auto-pass builds), placed beside the feature's end-on view (``view``
        defaults to it); for a turned **step/boss**, the ``ø…`` diameter leader in the row
        below (X-turned) or column left of (Z-turned) the front view. Tagged with *feature*
        so :meth:`drop` / :meth:`annotations_of` find it. Returns the annotation name.

        Raises ``ValueError`` if *feature* exposes no callout (use :meth:`dimension` for a
        linear param). A machined-feature callout
        (pocket/pad-height/circular-blind-step/fillet/blend/paired-ramp/flat/chamfer/groove) is
        auto-named and placed in its characteristic view by the kind's renderer, so
        ``view=``/``name=`` are unsupported for those kinds and raise ``ValueError`` rather
        than being silently ignored. Placed reasonably, not via the auto-pass's
        whole-set solve (byte-identity is not a goal, #400 Ph2) — :meth:`repair` tidies the
        rest. A step/boss diameter that finds no room returns ``""`` (a warning-level drop,
        like the auto-pass), rather than raising, so a reconstruction script never aborts.
        """
        kind = getattr(feature, "kind", None)
        if (
            kind in self.machined_callout_kinds or kind in ("pocket_pattern", "slot_pattern")
        ) and (view is not None or name is not None):
            raise ValueError(
                f"callout(): a {kind} is auto-named and placed in its characteristic view; "
                "view=/name= are unsupported for machined-feature callouts"
            )
        # There is deliberately NO authored-omission pre-check here.
        #
        # A pre-check for ANY approved dimension cannot prove this callout has approved
        # content: a turned step can have its length authored and diameter omitted. The
        # renderer must decide from the compiled plan what it can draw.
        #
        # The renderers below now consume approved content, so "draws nothing" is what they
        # DO rather than something to forecast — and both paths reach it the same way: the
        # live call returns "" with an `authored_omission` build issue, and the deferred
        # intent drains through the same migrated renderers to the same nothing.
        if self.defer_intents:  # #426: record, don't place — finalize() drains it
            self.intents.append(Intent("callout", feature, {"view": view, "name": name}))
            return ""
        from draftwright.annotations._common import PlacementContext
        from draftwright.annotations.holes import add_feature_callout, add_feature_diameter

        ctx = PlacementContext(
            registry=self.registry,
            coverage=self.coverage,
            items=self.items,
            document_member=self.document_member,
            document_source_annotation_ids=self.document_source_annotation_ids,
        )
        if kind in ("step", "boss"):
            return add_feature_diameter(self.drawing, feature, self.part_model, ctx=ctx)
        if kind in self.machined_callout_kinds:
            return self._machined_callout(feature, kind, ctx)
        if kind == "pocket_pattern":
            # A pocket pattern renders through its own auto-pass renderer (grouped size/depth
            # callout + pitch dim(s)), restricted to THIS feature (#841 outcome 3). Unlike the
            # lone machined callouts it places furniture too, so several names change — return
            # the grouped-callout name (m_pocketpat*), the handle pin()/drop() address.
            if self.part_model is None or self.analysis is None:
                raise ValueError(
                    "callout(): a pocket-pattern callout needs the part model and analysis; "
                    "add it to a drawing built by build_drawing(), not a bare Drawing"
                )
            from draftwright.annotations.holes import render_pocket_patterns
            from draftwright.model.compiled import FeatureRef, compile_dimensions

            before = {n: id(o) for n, o in self.iter_annotations()}
            render_pocket_patterns(
                self.drawing,
                compile_dimensions(self.part_model),
                self.analysis,
                ctx=ctx,
                only={FeatureRef(feature)},
            )
            placed = [n for n, o in self.iter_annotations() if before.get(n) != id(o)]
            return next((n for n in placed if n.startswith("m_pocketpat")), "")
        if kind == "slot_pattern":
            # A slot pattern renders through its own auto-pass renderer (grouped SLOT W × L
            # callout + pitch dim(s)), restricted to THIS feature (#841). Like the pocket pattern
            # it places furniture too, so several names change — return the grouped-callout name
            # (m_slotpat*), the handle pin()/drop() address.
            if self.part_model is None or self.analysis is None:
                raise ValueError(
                    "callout(): a slot-pattern callout needs the part model and analysis; "
                    "add it to a drawing built by build_drawing(), not a bare Drawing"
                )
            from draftwright.annotations.holes import render_slot_patterns
            from draftwright.model.compiled import FeatureRef, compile_dimensions

            before = {n: id(o) for n, o in self.iter_annotations()}
            render_slot_patterns(
                self.drawing,
                compile_dimensions(self.part_model),
                self.analysis,
                ctx=ctx,
                only={FeatureRef(feature)},
            )
            placed = [n for n, o in self.iter_annotations() if before.get(n) != id(o)]
            return next((n for n in placed if n.startswith("m_slotpat")), "")
        return add_feature_callout(
            self.drawing, feature, self.part_model, self.analysis, view=view, name=name, ctx=ctx
        )

    def _machined_callout(self, feature, kind, ctx) -> str:
        """Render one approved machined callout through its canonical family pass."""
        # Machined callouts render through their auto-pass renderer, restricted to THIS
        # feature (only={feature}) so a live call draws exactly one callout — the per-feature
        # `only=` subset the finalize stages also use. The deferred path above routes the
        # recorded intent to the matching per-kind finalize stage instead.
        if self.part_model is None or self.analysis is None:
            raise ValueError(
                f"callout(): a {kind} callout needs the part model and analysis; "
                "add it to a drawing built by build_drawing(), not a bare Drawing"
            )
        from draftwright.annotations.from_model import (
            render_blends,
            render_chamfers,
            render_circular_blind_steps,
            render_circular_channels,
            render_fillets,
            render_flats,
            render_grooves,
            render_hex_pockets,
            render_oriented_slots,
            render_pad_heights,
            render_paired_ramp_steps,
            render_pockets,
            render_rectangular_blind_slots,
            render_round_bottom_blind_slots,
        )

        renderers = {
            "blend": render_blends,
            "chamfer": render_chamfers,
            "circular_blind_step": render_circular_blind_steps,
            "circular_channel": render_circular_channels,
            "hex_pocket": render_hex_pockets,
            "fillet": render_fillets,
            "paired_ramp_step": render_paired_ramp_steps,
            "flat": render_flats,
            "pocket": render_pockets,
            "rectangular_blind_slot": render_rectangular_blind_slots,
            "round_bottom_blind_slot": render_round_bottom_blind_slots,
            "oriented_slot": render_oriented_slots,
            "pad": render_pad_heights,
            "groove": render_grooves,
        }
        # Return the placed annotation's name so pin()/drop() can address it.
        # only={feature} places exactly one callout, so at most one name changes. Diff by
        # object IDENTITY, not just the name set, so re-placing over an existing canonical
        # name (or a grouped callout collapsing to an already-present name) is still detected
        # as the placed name. A drop (no clear room) changes nothing and
        # returns "" — the same empty-string drop signal the step/boss diameter branch gives.
        before = {n: id(o) for n, o in self.iter_annotations()}
        # Migrated renderers consume the compiled plan and select by opaque reference.
        from draftwright.model.compiled import FeatureRef as _FR
        from draftwright.model.compiled import compile_dimensions as _cd3

        renderers[kind](
            self.drawing,
            _cd3(self.part_model),
            self.analysis,
            ctx=ctx,
            only={_FR(feature)},
        )
        changed = [n for n, o in self.iter_annotations() if before.get(n) != id(o)]
        return changed[0] if len(changed) == 1 else ""

    def overall_height(self) -> list[str]:
        """Add the part's **overall height** — the one dimension with no feature to name.

        Every other add verb takes a feature, because every other dimension belongs to one.
        The overall height usually does too: a model with an `EnvelopeFeature` carries a
        `height` parameter, and `dimension(env, "length", role="height")` is the verb for it.

        A model WITHOUT one still gets an overall height — the compiler falls back to the
        bounding box, which is a decision only the compiler may make (`_compile_overall_height`).
        There is then no feature to record an intent against, so an intent-level script had no
        way to say "and the 46 mm overall height", and a generated script replayed without it,
        silently and lint-clean (#889).

        This verb is that line. It is deliberately NOT "draw it whenever the compiler approves
        one": `auto_dims=False` means the verbs are the whole drawing, so a dimension nobody
        recorded must not appear — record-then-finalize has to equal placing live.

        Returns the placed names (empty when the compiler withholds the height — a Z-turned
        part whose step chain already tiles it, or an X/Y rotational OD that conveys it).
        """
        model, a = self.part_model, self.analysis
        # BEFORE the deferred/live split, so both routes refuse identically — the shape #925
        # settled for `callout()`: a check on one side of that split makes the answer depend
        # on whether you are inside `deferred()`.
        if model is not None and any(f.kind == "envelope" for f in model.features):
            # This verb exists ONLY for the featureless fallback. On an enveloped model the
            # measurement already has a feature to name, and supporting both spellings gave
            # two: live, `overall_height()` then `dimension(env, …, role="height")` drew the
            # 30 mm height TWICE, while the reverse order and the deferred route drew it once
            # (`explicit_envelope_height` removes the overall ladder from the compile). Order-
            # dependent live and live ≠ deferred, from composing two public spellings of one
            # measurement. One measurement, one verb.
            raise ValueError(
                "overall_height(): this model declares an envelope, so its height has a "
                'feature to name — use dimension(envelope, "length", role="height"). This '
                "verb is for a model with NO envelope feature, where the height comes from "
                "the bounding box and there is nothing to name."
            )
        if self.defer_intents:  # #426: record, don't place — finalize() drains it
            self.intents.append(Intent("overall_height", None, {}))
            return []
        if model is None or a is None:
            raise ValueError("overall_height(): no detected model — build the drawing first")
        from draftwright._core import layout_frame
        from draftwright.annotations._common import drain_corridors
        from draftwright.annotations.from_model import ladder_plan_for, render_height_ladder
        from draftwright.model.compiled import compile_dimensions

        before = set(self.annotations())
        ctx = PlacementContext(
            registry=self.registry,
            coverage=self.coverage,
            items=self.items,
            document_member=self.document_member,
            document_source_annotation_ids=self.document_source_annotation_ids,
        )
        # ONLY the overall height: the renderer also draws the step ladder, which is a
        # different intent with its own verb. The drain projects the plan with the same
        # helper, so the two routes cannot disagree about what was asked for.
        plan = ladder_plan_for(compile_dimensions(model), step_height=False, overall=True)
        if plan.ladder("overall_height") is not None:
            render_height_ladder(
                self.drawing, plan, layout_frame(a), ctx=ctx, detail_view=self.build.detail_view
            )
            drain_corridors(ctx, self.drawing)
        return sorted(set(self.annotations()) - before)

    def furniture(self, feature, *, view=None) -> list[str]:
        """Add a hole/pattern's non-dimensional **sheet furniture** (#419) — centre marks
        (every member) plus a pattern's centre-cross (bolt circle) or pitch/grid dims.

        The geometric marks a feature carries that no other verb emits: where
        :meth:`callout` draws the ø leader and :meth:`locate` the position dims, ``furniture``
        draws the centre marks and pattern furniture. *feature* is a hole/pattern from
        :meth:`model`; ``view`` defaults to its end-on view. Each mark is tagged with
        *feature* so :meth:`drop` / :meth:`annotations_of` find it. Returns the placed names
        (varies by pattern kind — a bolt circle emits a centre-cross, a linear/grid array a
        pitch dim).

        Raises ``ValueError`` if *feature* is not a hole/pattern (use :meth:`dimension`).
        """
        if self.defer_intents:  # #426: record, don't place — finalize() drains it
            self.intents.append(Intent("furniture", feature, {"view": view}))
            return []
        from draftwright.annotations._common import PlacementContext
        from draftwright.annotations.holes import add_feature_furniture

        ctx = PlacementContext(
            registry=self.registry,
            coverage=self.coverage,
            items=self.items,
            document_member=self.document_member,
            document_source_annotation_ids=self.document_source_annotation_ids,
        )
        return add_feature_furniture(
            self.drawing, feature, self.part_model, self.analysis, view=view, ctx=ctx
        )

    def rotational(self, feature) -> list[str]:
        """Add a rotational part's **turned furniture** (#424/#426) — the overall OD
        dimension, the axis centrelines, and any concentric-bore leaders.

        The editable handle for the whole-model rotational renderer: where the
        per-feature verbs place callouts/locations, ``rotational`` draws the furniture
        the auto-pass synthesises for a part's ``RotationalFeature`` (a turned /
        cylindrical body). *feature* is the rotational feature from :meth:`model`.
        Placed by the shared :func:`render_rotational` — the same whole-model renderer
        the auto-pass runs, so a script-reconstructed drawing is byte-identical to the
        direct build (no ``only=`` subset, no positional-naming seam: the renderer
        names its own outputs ``dim_od`` / ``centerline_*`` / ``ldr_*``). Returns ``[]``.
        """
        if self.defer_intents:  # #426: record, don't place — finalize() drains it
            self.intents.append(Intent("rotational", feature, {}))
            return []
        from draftwright.annotations._common import PlacementContext
        from draftwright.annotations.from_model import render_rotational
        from draftwright.model.compiled import compile_dimensions

        ctx = PlacementContext(
            registry=self.registry,
            coverage=self.coverage,
            items=self.items,
            document_member=self.document_member,
            document_source_annotation_ids=self.document_source_annotation_ids,
        )
        render_rotational(
            self.drawing, compile_dimensions(self.part_model), self.analysis, ctx=ctx
        )
        return []

    def section(self) -> list[str]:
        """Add the automatic full **section A–A** (#420) — the section half of the
        editable surface.

        Part-level, unlike the per-feature verbs: a section fires when a Z-axis
        hole/pattern has a counterbore, spotface, or blind bottom (its internal
        profile is hidden-line-only in every ortho view), cutting through the densest
        qualifying row. Takes no argument (the auto A–A) and is **not** feature-tagged
        or :meth:`drop`-compatible — a section is atomic, so it is dropped by commenting
        the call. Returns the placed annotation names, or ``[]`` when no section is
        warranted or there is no room. Call it *after* the per-feature verbs — the room
        check carves the view row around whatever is already placed and takes the
        leftmost gap that fits, so it needs the occupancy to be complete. The outcome
        is recorded on :attr:`section_decision` either way (#1190).
        """
        if self.defer_intents:  # #426: record, don't place — finalize() drains it
            self.intents.append(Intent("section", None, {}))
            return []
        from draftwright.annotations.sections import add_section

        ctx = PlacementContext(
            registry=self.registry,
            coverage=self.coverage,
            items=self.items,
            document_member=self.document_member,
            document_source_annotation_ids=self.document_source_annotation_ids,
        )
        return add_section(self.drawing, self.part_model, self.analysis, ctx=ctx)

    def locate(self, feature, *, axes=None, pin=False) -> list[str]:
        """Add datum-referenced **X/Y position dimensions** for a Z-axis hole/pattern
        (#418) — the location half of the feature-referenced **add** surface.

        Distinct from :meth:`dimension` (a feature's own intrinsic linear params): a
        location dim measures the *datum → feature-centre* offset, which no feature
        exposes as a parameter. *feature* is a hole/pattern from :meth:`model`; ``axes``
        selects the in-plane axes (default both — ``"x"`` above the plan view, ``"y"``
        above the side view). ``pin=True`` marks the placed dimensions as deliberate user
        edits: in deferred mode they still flow through the shared corridor solve, but
        survive/dedup as high-priority candidates and pin themselves once placed (#511).
        Each dim is tagged with *feature* so :meth:`drop` / :meth:`annotations_of` find it.
        In live mode, returns one placed name per distinct requested in-plane
        ordinate with a real offset. In deferred mode, records the intent and
        returns ``[]``; the names are created when the context finalizes.

        Circular channels also accept this verb: their X/Y/Z offsets locate the seat
        axis from the stock bounding-box minimum, and ``axes`` may select any subset
        of those three coordinates. They use the shared profile corridor solve.

        Raises ``ValueError`` for an unsupported feature (side-drilled
        bores are placed by the auto-pass). A feature with no datum-referenced ref (a
        datum-less model or a concentric/on-datum bore) returns ``[]``. Live placement
        handles this feature alone; automatic/deferred rendering may coalesce truly
        coincident ordinates while retaining every semantic owner. Placed reasonably, not
        via the auto-pass's corridor solve (byte-identity is not a goal, #400 Ph2).
        """
        if self.defer_intents:  # #426: record, don't place — finalize() drains it
            self.intents.append(Intent("locate", feature, {"axes": axes, "pin": pin}))
            return []
        from draftwright.annotations._common import PlacementContext
        from draftwright.annotations.holes import add_feature_location

        ctx = PlacementContext(
            registry=self.registry,
            coverage=self.coverage,
            items=self.items,
            document_member=self.document_member,
            document_source_annotation_ids=self.document_source_annotation_ids,
        )
        if getattr(feature, "kind", None) == "circular_channel":
            from draftwright.annotations._common import drain_corridors
            from draftwright.annotations.from_model import render_circular_channel_locations
            from draftwright.model.compiled import compile_dimensions

            if self.part_model is None or not any(
                item is feature for item in self.part_model.features
            ):
                raise ValueError("locate(): feature is not from this drawing's model")
            before = set(self.annotations())
            render_circular_channel_locations(
                self.drawing,
                compile_dimensions(self.part_model, planned_views=tuple(self.views)),
                self.analysis,
                ctx=ctx,
                only={feature},
                pinned={feature} if pin else None,
                axes=axes,
            )
            drain_corridors(ctx, self.drawing)
            return [
                name
                for name in self.annotations()
                if name not in before and name.startswith("m_seatloc_")
            ]
        return add_feature_location(
            self.drawing, feature, self.part_model, self.analysis, axes=axes, pin=pin, ctx=ctx
        )
