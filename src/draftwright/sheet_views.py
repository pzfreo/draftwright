"""Private Sheet view declarations and constraint materialisation (ADR 2)."""

from __future__ import annotations

import inspect
import math
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from draftwright._warnings import SoftDeprecationWarning
from draftwright.sheet_features import _FeatureView
from draftwright.view_plan import (
    PRINCIPAL_VIEW_NAMES,
    ConstraintSource,
    ViewConstraint,
    ViewConstraints,
    ViewPin,
    ViewRelation,
    ViewSpec,
    derived_view_identifier,
    third_angle_view_names,
)

if TYPE_CHECKING:
    from draftwright.sheet import Sheet

_SOURCE_CHOICES = ("automatic", "authored")
_FACADE_FILES = {__file__, str(Path(__file__).with_name("sheet.py"))}


def _constraint_source() -> ConstraintSource:
    """Return the first caller frame outside the Sheet facade and view owner."""

    frame = inspect.currentframe()
    try:
        caller = frame.f_back if frame is not None else None
        while caller is not None and caller.f_code.co_filename in _FACADE_FILES:
            caller = caller.f_back
        if caller is None:
            return ConstraintSource("<unknown>", 0)
        return ConstraintSource(caller.f_code.co_filename, caller.f_lineno)
    finally:
        del frame


class _View:
    """A fluent handle for one semantic whole-view constraint.

    Its layout verbs relate or pin the complete view block.  They never address a feature
    annotation, so ADR 2 (was 0014) remains the sole owner of dimension/callout/GD&T coordinates.
    """

    def __init__(self, sheet: _SheetViewMethods, bucket: str, index: int) -> None:
        self._sheet = sheet
        self._bucket = bucket
        self._index = index

    @property
    def _record(self) -> dict:
        return cast(dict, getattr(self._sheet, self._bucket)[self._index])

    @property
    def name(self) -> str:
        return cast(str, self._record["name"])

    def _relation(self, relation: str, other, gap=None) -> _View:
        other_name = other.name if isinstance(other, _View) else str(other)
        self._sheet._view_relations.append(
            ViewRelation(
                self.name,
                relation,
                other_name,
                None if gap is None else float(gap),
                _constraint_source(),
            )
        )
        return self

    def left_of(self, other, *, gap=None) -> _View:
        """Keep this whole view block left of *other*."""
        return self._relation("left_of", other, gap)

    def right_of(self, other, *, gap=None) -> _View:
        """Keep this whole view block right of *other*."""
        return self._relation("right_of", other, gap)

    def above(self, other, *, gap=None) -> _View:
        """Keep this whole view block above *other*."""
        return self._relation("above", other, gap)

    def below(self, other, *, gap=None) -> _View:
        """Keep this whole view block below *other*."""
        return self._relation("below", other, gap)

    def align_x(self, other) -> _View:
        """Align this view's projection origin horizontally with *other*."""
        return self._relation("align_x", other)

    def align_y(self, other) -> _View:
        """Align this view's projection origin vertically with *other*."""
        return self._relation("align_y", other)

    def pin(self, at) -> _View:
        """Pin a principal view's projection origin at ``(x, y)`` page millimetres."""
        if self._record["kind"] != "principal":
            raise ValueError(
                "projection-origin pins currently support principal front/plan/side views only; "
                f"cannot pin {self.name!r}"
            )
        values = tuple(float(value) for value in at)
        if len(values) != 2:
            raise ValueError(f"view pin needs two page coordinates, got {at!r}")
        point = (values[0], values[1])
        self._sheet._view_pins = [pin for pin in self._sheet._view_pins if pin.view != self.name]
        self._sheet._view_pins.append(ViewPin(self.name, point, _constraint_source()))
        return self

    def scale(self, factor) -> _View:
        """Set an independent detail/orientation scale; principal views reject it."""
        record = self._record
        if record["kind"] == "principal":
            raise ValueError(
                f"principal view {self.name!r} cannot have an independent scale; "
                "front/plan/side share the drawing scale"
            )
        factor = float(factor)
        if not math.isfinite(factor) or factor <= 0:
            raise ValueError(f"view scale factor must be finite and positive, got {factor!r}")
        record["scale_factor"] = factor
        return self


class _SheetViewMethods:
    # Sheet initializes this state. These declarations let the private view owner
    # type-check its methods without manufacturing a second state owner.
    _principal_views: list[dict]
    _added_principal_views: list[dict]
    _derived_views: list[dict]
    _added_derived_views: list[dict]
    _view_relations: list[ViewRelation]
    _view_pins: list[ViewPin]
    _principal_view_source: str | None
    _derived_view_source: str | None
    _principal_view_source_at: ConstraintSource | None
    _derived_view_source_at: ConstraintSource | None
    _authored: list[dict]
    _authored_source: bool
    _auto_dimensions: str | None
    _added_dimensions: list[dict]
    _section: tuple[str, Any] | None
    _opts: dict
    _replayed_views: tuple[str, ...] | None
    _features: _FeatureView

    if TYPE_CHECKING:

        def _gdt_ref(self, ref: Any) -> tuple[Any, int | None]: ...
        def _index_of_token(self, token: int) -> int: ...
        def _section_cut_y(self, request: Any = None) -> float: ...

    # -- view declaration (ADR 2 (was 0018)) ---------------------------------------

    @staticmethod
    def _principal_view_name(name) -> tuple[str, str]:
        name = str(name).strip().lower()
        kinds = {**dict.fromkeys(PRINCIPAL_VIEW_NAMES, "principal"), "iso": "pictorial"}
        if name not in kinds:
            raise ValueError(
                f"unknown view {name!r}; expected one of {tuple(kinds)}. "
                "Use section_view()/detail_view() for derived views."
            )
        return name, kinds[name]

    @staticmethod
    def _derived_view_name(kind: str, label) -> str:
        label = str(label).strip()
        if len(label) != 1 or not label.isalnum():
            raise ValueError(f"{kind}_view() needs one alphanumeric drawing label, got {label!r}")
        slug = label.lower()
        return f"section_{slug}{slug}" if kind == "section" else f"detail_{slug}"

    def _all_view_names(self) -> set[str]:
        return {
            record["name"]
            for bucket in (
                self._principal_views,
                self._added_principal_views,
                self._derived_views,
                self._added_derived_views,
            )
            for record in bucket
        }

    def _append_view_record(
        self, bucket: str, *, name: str, kind: str, target=None, source: ConstraintSource
    ) -> _View:
        if name in self._all_view_names():
            raise ValueError(f"view {name!r} is declared more than once")
        identifier = derived_view_identifier(kind, name)
        if identifier is not None:
            for records in (self._derived_views, self._added_derived_views):
                for record in records:
                    if derived_view_identifier(record["kind"], record["name"]) == identifier:
                        raise ValueError(
                            f"derived-view identifier {identifier!r} is already used by "
                            f"{record['name']!r}; sections and details share one drawing-wide "
                            "identifier sequence"
                        )
        records = getattr(self, bucket)
        records.append(
            {
                "name": name,
                "kind": kind,
                "target": target,
                "scale_factor": None,
                "source": source,
            }
        )
        return _View(self, bucket, len(records) - 1)

    def _reject_authored_view_auto_dimensions(self, verb: str) -> None:
        if self._auto_dimensions == "explicit":
            raise ValueError(
                f"{verb} authors the view set, but this sheet already called "
                "auto_dimensions(). ADR 2 (was 0018) makes requirements determine views, not the "
                "reverse: use authored_dimensions() with explicit dimension(...) lines, or "
                "keep auto_views() and use add_view()/add_section_view()/add_detail_view()."
            )

    def take_over(
        self,
        *,
        dimensions: Literal["automatic", "authored"],
        principal_views: Literal["automatic", "authored"],
        derived_views: Literal["automatic", "authored"],
    ) -> Sheet:
        """Adopt a detected baseline with explicit, independently chosen sources.

        This is the public transition from :meth:`from_part`'s detected features and implicit
        automatic dimensions to an editable Sheet request.  Principal and derived views are
        separate sources (ADR 2 (was 0018)): keeping automatic principal planning while authoring the
        complete derived set replaces an inferred section instead of augmenting it; an empty
        authored derived set explicitly suppresses every inferred section/detail.

        The declaration is atomic and order-independent.  Matching declarations made before
        this call are accepted, while an explicit contradictory source raises without changing
        any source.  The incoherent ADR 2 (was 0018) combination -- authored views with automatic
        dimensions -- is always refused.
        """
        choices = {
            "dimensions": dimensions,
            "principal_views": principal_views,
            "derived_views": derived_views,
        }
        invalid = {name: value for name, value in choices.items() if value not in _SOURCE_CHOICES}
        if invalid:
            name, value = next(iter(invalid.items()))
            raise ValueError(f"{name} must be 'automatic' or 'authored', got {value!r}")
        if dimensions == "automatic" and (
            principal_views == "authored" or derived_views == "authored"
        ):
            raise ValueError(
                "take_over() cannot combine automatic dimensions with authored views. "
                "ADR 2 (was 0018) makes requirements determine views, not views determine requirements"
            )

        authored_dimensions = bool(self._authored) or self._authored_source
        if dimensions == "automatic" and authored_dimensions:
            raise ValueError(
                "take_over(dimensions='automatic') conflicts with this sheet's authored "
                "dimension source"
            )
        if dimensions == "authored" and self._auto_dimensions == "explicit":
            raise ValueError(
                "take_over(dimensions='authored') conflicts with an explicit "
                "auto_dimensions() source"
            )
        if dimensions == "authored" and self._added_dimensions:
            raise ValueError(
                "take_over(dimensions='authored') conflicts with add_dimension() automatic-set "
                "additions"
            )
        if derived_views == "authored" and (
            self._section is not None or self._opts.get("detail_view") is True
        ):
            raise ValueError(
                "take_over(derived_views='authored') conflicts with a legacy section()/detail() "
                "automatic-set augmentation; migrate it to section_view()/detail_view()"
            )

        source_requests = (
            (
                "principal_views",
                principal_views,
                self._principal_view_source,
                self._added_principal_views,
            ),
            (
                "derived_views",
                derived_views,
                self._derived_view_source,
                self._added_derived_views,
            ),
        )
        for name, requested, current, added_records in source_requests:
            if current is not None and current != requested:
                raise ValueError(
                    f"take_over({name}={requested!r}) conflicts with the existing "
                    f"{current!r} {name} source"
                )
            if requested == "authored" and added_records:
                raise ValueError(
                    f"take_over({name}='authored') conflicts with automatic-set additions"
                )

        # All checks precede mutation: a failed takeover never leaves a partly changed Sheet.
        source = _constraint_source()
        if dimensions == "authored":
            self._authored_source = True
            self._auto_dimensions = None
        else:
            self._auto_dimensions = "explicit"
        self._principal_view_source = principal_views
        self._derived_view_source = derived_views
        self._principal_view_source_at = self._principal_view_source_at or source
        self._derived_view_source_at = self._derived_view_source_at or source
        return cast("Sheet", self)

    def authored_views(self) -> Sheet:
        """Declare that subsequent :meth:`view` lines are the complete principal set.

        Calling this with no ``view(...)`` lines makes the empty authored set explicit.  It
        remains a request even when a later build finds that no projectable drawing can satisfy
        it; absence of the verb retains the behaviourally-compatible automatic default.
        """
        self._reject_authored_view_auto_dimensions("authored_views()")
        if self._principal_view_source == "automatic":
            raise ValueError(
                "a sheet has one principal-view source: authored_views()/view(...) or "
                "auto_views()/add_view(), not both"
            )
        self._principal_view_source = "authored"
        self._principal_view_source_at = self._principal_view_source_at or _constraint_source()
        return cast("Sheet", self)

    def auto_views(self) -> Sheet:
        """Select automatic principal and derived views, optionally augmented by add verbs."""
        if self._principal_view_source == "authored" or self._derived_view_source == "authored":
            raise ValueError(
                "auto_views() cannot be combined with an authored principal or derived view "
                "set; use add_view()/add_section_view()/add_detail_view() to augment automatic views"
            )
        warnings.warn(
            "Sheet.auto_views() is soft deprecated: still supported and NOT scheduled for "
            "removal, but authored_views() plus view(...) lines is the editable surface.",
            SoftDeprecationWarning,
            stacklevel=2,
        )
        self._principal_view_source = "automatic"
        self._derived_view_source = "automatic"
        source = _constraint_source()
        self._principal_view_source_at = self._principal_view_source_at or source
        self._derived_view_source_at = self._derived_view_source_at or source
        return cast("Sheet", self)

    def view(self, name) -> _View:
        """Add one view to the complete authored principal/orientation set."""
        self._reject_authored_view_auto_dimensions("view()")
        if self._principal_view_source == "automatic":
            raise ValueError(
                "view() defines the complete authored set and cannot follow auto_views(); "
                "use add_view() to augment the automatic set"
            )
        name, kind = self._principal_view_name(name)
        self._principal_view_source = "authored"
        self._principal_view_source_at = self._principal_view_source_at or _constraint_source()
        return self._append_view_record(
            "_principal_views", name=name, kind=kind, source=_constraint_source()
        )

    def add_view(self, name) -> _View:
        """Require one additional principal/orientation view in an automatic set."""
        if self._replayed_views is not None:
            raise ValueError(
                "add_view() conflicts with the generated script's settled _replayed_views; "
                "remove _replayed_views from Sheet(...) to replan after editing the view set"
            )
        if self._principal_view_source == "authored":
            raise ValueError("add_view() augments auto_views(); use view() inside an authored set")
        name, kind = self._principal_view_name(name)
        return self._append_view_record(
            "_added_principal_views", name=name, kind=kind, source=_constraint_source()
        )

    def _derived_target(self, verb: str, *, feature=None, at=None):
        if (feature is None) == (at is None):
            raise ValueError(f"{verb} needs exactly one of its feature target or at=")
        if at is not None:
            value = float(at)
            if not math.isfinite(value):
                raise ValueError(f"{verb}(at=…) needs a finite Y, got {at!r}")
            return ("at", value)
        _target, token = self._gdt_ref(feature)
        if token is None:
            raise ValueError(
                f"{verb} needs a declared feature handle/index/Feature; use at= for a bare cut"
            )
        return ("feature", token)

    def section_view(self, label, through=None, *, at=None) -> _View:
        """Author a named section view through a declared feature or explicit Y cut plane."""
        self._reject_authored_view_auto_dimensions("section_view()")
        if self._derived_view_source == "automatic":
            raise ValueError(
                "section_view() defines the authored derived set and cannot follow auto_views(); "
                "use add_section_view() to augment automatic derived views"
            )
        name = self._derived_view_name("section", label)
        target = self._derived_target("section_view()", feature=through, at=at)
        self._derived_view_source = "authored"
        self._derived_view_source_at = self._derived_view_source_at or _constraint_source()
        return self._append_view_record(
            "_derived_views",
            name=name,
            kind="section",
            target=target,
            source=_constraint_source(),
        )

    def add_section_view(self, label, through=None, *, at=None) -> _View:
        """Augment automatic derived views with one named section."""
        if self._derived_view_source == "authored":
            raise ValueError(
                "add_section_view() augments auto_views(); use section_view() in an authored set"
            )
        return self._append_view_record(
            "_added_derived_views",
            name=self._derived_view_name("section", label),
            kind="section",
            target=self._derived_target("add_section_view()", feature=through, at=at),
            source=_constraint_source(),
        )

    def detail_view(self, label, around) -> _View:
        """Author a named detail view around a declared feature."""
        self._reject_authored_view_auto_dimensions("detail_view()")
        if self._derived_view_source == "automatic":
            raise ValueError(
                "detail_view() defines the authored derived set and cannot follow auto_views(); "
                "use add_detail_view() to augment automatic derived views"
            )
        name = self._derived_view_name("detail", label)
        target = self._derived_target("detail_view()", feature=around, at=None)
        self._derived_view_source = "authored"
        self._derived_view_source_at = self._derived_view_source_at or _constraint_source()
        return self._append_view_record(
            "_derived_views",
            name=name,
            kind="detail",
            target=target,
            source=_constraint_source(),
        )

    def add_detail_view(self, label, around) -> _View:
        """Augment automatic derived views with one named detail around a declared feature."""
        if self._derived_view_source == "authored":
            raise ValueError(
                "add_detail_view() augments auto_views(); use detail_view() in an authored set"
            )
        return self._append_view_record(
            "_added_derived_views",
            name=self._derived_view_name("detail", label),
            kind="detail",
            target=self._derived_target("add_detail_view()", feature=around, at=None),
            source=_constraint_source(),
        )

    def section(self, feature=None, *, at=None) -> Sheet:
        """Request a full **section A–A** (#841) — the part-level verb behind the auto section.

        A section fires automatically only when a Z-axis hole/pattern has a counterbore,
        spotface, or blind bottom; a blind pocket has no such driving hole, so its floor
        and depth stay hidden-line-only. This forces a cut so that internal profile reads.

        The cut plane is normal to Y. *feature* — a fluent handle / :class:`Feature` /
        index — cuts through that feature's centre (the natural "section through this
        pocket"); ``at=<y>`` cuts at an explicit Y; bare ``section()`` cuts through the
        part centre. The section renders last (its room check clears the right-of-side-view
        band), so declare it after the per-feature verbs. Chainable."""
        warnings.warn(
            "Sheet.section() is deprecated; for authored derived views with authored dimensions use "
            "section_view('A', through=...) or section_view('A', at=...). "
            "To augment automatic views, select auto_views() and use add_section_view(...). "
            "Removal target 0.6.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        if self._derived_view_source == "authored":
            raise ValueError(
                "section() augments automatic derived views and cannot follow an authored "
                "derived-view source; use section_view()"
            )
        if at is not None:
            if not math.isfinite(at):
                raise ValueError(f"section(at=…) needs a finite Y, got {at!r}")
            self._section = ("at", float(at))
        elif feature is not None:
            _target, src = self._gdt_ref(feature)
            if src is None:
                raise ValueError(
                    "section(feature=…) needs a declared feature (a handle/index/Feature "
                    "on this sheet) — pass at=<y> for a bare cut-plane position"
                )
            self._section = ("feature", src)
        else:
            self._section = ("auto", None)
        return cast("Sheet", self)

    def detail(self) -> Sheet:
        """Ensure enlarged **detail-view** recovery is enabled (#42/#307/#841).

        Automatic builds enable it by default; this verb is useful after constructing a
        ``Sheet(..., detail_view=False)`` or when an emitted declaration should state the
        choice explicitly. Adds a magnified crop of the step-height region when warranted
        (a no-op otherwise). Chainable. Not feature-targeted — for a blind pocket's
        floor/depth prefer :meth:`section`."""
        warnings.warn(
            "Sheet.detail() is deprecated; use add_detail_view('A', around=feature). "
            "Removal target 0.6.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        if self._derived_view_source == "authored":
            raise ValueError(
                "detail() augments automatic derived views and cannot follow an authored "
                "derived-view source; use detail_view()"
            )
        self._opts["detail_view"] = True
        return cast("Sheet", self)

    # -- inspection / output --------------------------------------------------

    def _materialized_view_constraint(self, record: dict) -> ViewConstraint:
        target = record["target"]
        if target is not None and target[0] == "feature":
            target = ("feature", self._features[self._index_of_token(target[1])])
        return ViewConstraint(
            ViewSpec(
                name=record["name"],
                kind=record["kind"],
                target=target,
                scale_factor=record["scale_factor"],
            ),
            record["source"],
        )

    @property
    def view_constraints(self) -> ViewConstraints:
        """The immutable pre-projection view request authored on this sheet."""

        materialize = self._materialized_view_constraint
        return ViewConstraints(
            principal_source=self._principal_view_source,
            principal_source_location=self._principal_view_source_at,
            principals=tuple(map(materialize, self._principal_views)),
            added_principals=tuple(map(materialize, self._added_principal_views)),
            derived_source=self._derived_view_source,
            derived_source_location=self._derived_view_source_at,
            derived=tuple(map(materialize, self._derived_views)),
            added_derived=tuple(map(materialize, self._added_derived_views)),
            relations=tuple(self._view_relations),
            pins=tuple(self._view_pins),
        )

    def row(self, *views, gap=None) -> Sheet:
        """Constrain complete view blocks into a left-to-right row."""
        names = [view.name if isinstance(view, _View) else str(view) for view in views]
        if len(names) < 2:
            raise ValueError("row() needs at least two views")
        source = _constraint_source()
        for left, right in zip(names, names[1:]):
            self._view_relations.append(
                ViewRelation(right, "right_of", left, None if gap is None else float(gap), source)
            )
        return cast("Sheet", self)

    def column(self, *views, gap=None) -> Sheet:
        """Constrain complete view blocks into a bottom-to-top column."""
        names = [view.name if isinstance(view, _View) else str(view) for view in views]
        if len(names) < 2:
            raise ValueError("column() needs at least two views")
        source = _constraint_source()
        for below, above in zip(names, names[1:]):
            self._view_relations.append(
                ViewRelation(above, "above", below, None if gap is None else float(gap), source)
            )
        return cast("Sheet", self)

    def _view_build_request(self) -> tuple[tuple[str, ...] | None, bool]:
        """Validate source coherence and lower the principal request to the engine seam."""

        if self._added_principal_views and self._principal_view_source != "automatic":
            source = self._added_principal_views[0]["source"]
            raise ValueError(
                f"add_view() at {source} augments the automatic set; call auto_views() first"
            )
        if self._added_derived_views and self._derived_view_source != "automatic":
            source = self._added_derived_views[0]["source"]
            guidance = (
                "use section_view()/detail_view() for the authored view set"
                if "authored" in (self._principal_view_source, self._derived_view_source)
                else "call auto_views() first"
            )
            raise ValueError(
                f"add_section_view()/add_detail_view() at {source} augment automatic derived "
                f"views; {guidance}"
            )
        if self._principal_view_source != "authored":
            additions = tuple(
                record["name"]
                for record in self._added_principal_views
                if record["kind"] == "principal"
            )
            if additions:
                return tuple(dict.fromkeys((*third_angle_view_names(), *additions))), True
            return None, True
        names = tuple(record["name"] for record in self._principal_views)
        principals = tuple(name for name in names if name in PRINCIPAL_VIEW_NAMES)
        if not principals:
            source = (
                self._principal_views[0]["source"]
                if self._principal_views
                else self._principal_view_source_at or "the authored_views() declaration"
            )
            raise ValueError(
                f"the authored view set from {source} has no principal orthographic view; "
                "add view('front'), view('plan'), view('side'), or view('rear')"
            )
        return principals, "iso" in names

    def _view_source_description(self) -> str:
        sources = [record["source"] for record in self._principal_views]
        if not sources:
            return f"authored at {self._principal_view_source_at}"
        return "authored at " + ", ".join(str(source) for source in sources)

    def _derived_build_request(self) -> tuple[tuple | None, bool]:
        """Validate derived targets and return legacy decoration/detail compatibility state."""

        records = [*self._derived_views, *self._added_derived_views]
        sections = [record for record in records if record["kind"] == "section"]
        if self._section is not None and records:
            raise ValueError(
                "deprecated section() cannot be combined with section_view()/detail_view() "
                "constraints; remove the legacy call and keep section_view() for authored "
                "derived views or add_section_view() for automatic derived views"
            )
        for record in sections:
            target = record["target"]
            if target[0] == "at":
                self._section_cut_y(target)
        detail_auto = self._derived_view_source != "authored"
        return None, detail_auto
