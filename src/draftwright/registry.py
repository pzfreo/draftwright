"""The annotation registry (#138 / ADR 0005, Step 2).

`Drawing` was both the public editable object and the internal state bus for
every subsystem. This module gives one of those concerns — **annotation identity
and build-time metadata** — a single owner:

- ``_named`` — name → annotation object (a name maps to exactly one object).
- ``_anno_view`` — name → owning orthographic view ("front"/"plan"/"side"),
  captured at creation by the annotation passes (#121). Authoritative source for
  composing each view's footprint block, so the layout never recovers ownership
  from page coordinates. Drawing-level marks (title block, iso/section notes)
  carry no view.
- ``_pinned`` — names whose position the caller fixed; the engine must not move
  them (repair now, the global solve later — ADR 0003 #89).
- ``_build_issues`` — lint issues found while building (e.g. annotations the
  layout had to drop), so :meth:`Drawing.lint` can surface them — a dropped
  feature must never be silent.

`Drawing` delegates its annotation add/remove/pin/ownership/build-issue
operations here and keeps ``items`` (the ordered render list); the four field
names remain reachable as ``Drawing`` properties during the migration because
tests and helpers still read through them (ADR 0005 §4).

This module sits at the bottom of the import DAG — it depends on nothing in
draftwright and carries no behaviour beyond the bookkeeping moved out of
`Drawing` unchanged.
"""

from __future__ import annotations


class AnnotationRegistry:
    """Single owner of annotation identity, ownership, pins, and build issues."""

    def __init__(self) -> None:
        self._named: dict = {}
        self._anno_view: dict = {}
        # name -> the source IR Feature this annotation was rendered for (#398). Peer to
        # _anno_view: another ownership axis of annotation identity, set at add time by
        # the render layer (which knows the feature) and snapshot/restored with the rest,
        # so a repack/repair preserves provenance. Absent for part-level marks (title
        # block, section arrows) that belong to no single feature.
        self._anno_feature: dict = {}
        self._pinned: set = set()
        self._build_issues: list = []

    # -- identity / ownership -------------------------------------------------

    def __contains__(self, name) -> bool:
        return name in self._named

    def named(self, name):
        """The object registered under *name*, or ``None``."""
        return self._named.get(name)

    def annotations(self) -> dict:
        """``{name: type_name}`` for every *named* annotation (#27)."""
        return {name: type(obj).__name__ for name, obj in self._named.items()}

    def view_of(self, name):
        """The owning view for *name* ("front"/"plan"/"side"), or ``None``."""
        return self._anno_view.get(name)

    def feature_of(self, name):
        """The source IR feature *name* was rendered for, or ``None`` (#398)."""
        return self._anno_feature.get(name)

    def names_for_feature(self, feature) -> list:
        """Every annotation name owned by *feature* (matched by value equality, so a
        feature from ``dwg.model()`` finds the annotations rendered for it) (#398).

        Value equality is safe because IR features are location-distinct — every
        ``Feature`` is a frozen dataclass whose ``frame.origin`` participates in ``==``,
        so two genuinely different features never compare equal. If a location-less
        feature type is ever added, switch this to identity (``f is feature``)."""
        return [n for n, f in self._anno_feature.items() if f == feature]

    def iter_named(self):
        """Iterate ``(name, annotation object)`` for every named annotation — the
        encapsulated read path (callers no longer touch ``_named`` directly, #241)."""
        return self._named.items()

    def replace_object(self, old, new) -> None:
        """Swap annotation object *old* for *new* wherever it is named, keeping the
        name → view binding (the repair loop's re-placement; #241)."""
        for name, obj in self._named.items():
            if obj is old:
                self._named[name] = new

    def snapshot(self) -> dict:
        """An opaque snapshot of the annotation identity state — the name → object map
        AND its per-name view/pin metadata — for restore() (repair undo). Snapshotting
        only ``_named`` would let a rolled-back pass leave ``_anno_view``/``_pinned``
        referencing names it added (or the wrong view for a re-placed dim)."""
        return {
            "named": dict(self._named),
            "anno_view": dict(self._anno_view),
            "anno_feature": dict(self._anno_feature),
            "pinned": set(self._pinned),
        }

    def restore(self, snap: dict) -> None:
        """Restore a :meth:`snapshot` (repair undo of a net-worsening pass)."""
        self._named.clear()
        self._named.update(snap["named"])
        self._anno_view.clear()
        self._anno_view.update(snap["anno_view"])
        self._anno_feature.clear()
        self._anno_feature.update(snap.get("anno_feature", {}))
        self._pinned.clear()
        self._pinned.update(snap["pinned"])

    def add(self, obj, name, view, feature=None):
        """Register *obj* under *name* and record its owning *view* (and source *feature*).

        Returns the object previously registered under *name* (so the caller can
        drop it from the render list), or ``None``. A replacement under the same
        name is a fresh, deliberate object — it does not inherit the old object's
        pin (#89) — and re-adding view-less clears any stale ownership tag so the
        view map never lags ``_named`` (#121).
        """
        displaced = None
        if name is not None and name in self._named:
            displaced = self._named[name]
            self._pinned.discard(name)
        if name is not None:
            self._named[name] = obj
            if view is not None:
                self._anno_view[name] = view
            else:
                self._anno_view.pop(name, None)
            # Feature provenance mirrors the view tag: a replacement re-asserts (or
            # clears, when re-added feature-less) the source feature so it never lags.
            if feature is not None:
                self._anno_feature[name] = feature
            else:
                self._anno_feature.pop(name, None)
        return displaced

    def remove(self, name):
        """Forget *name* (object, view, pin); returns the object or ``None``."""
        obj = self._named.pop(name, None)
        if obj is not None:
            self._pinned.discard(name)  # a removed name carries no pin (#89)
            self._anno_view.pop(name, None)
            self._anno_feature.pop(name, None)
        return obj

    def clear(self, keep) -> dict:
        """Drop every name except those in *keep*; returns the kept ``{name: obj}``
        so the caller can prune the render list to match."""
        keep_set = set(keep)
        kept_named = {n: o for n, o in self._named.items() if n in keep_set}
        self._named = kept_named
        self._pinned &= keep_set  # drop pins for cleared names (#89)
        self._anno_view = {n: v for n, v in self._anno_view.items() if n in keep_set}
        self._anno_feature = {n: f for n, f in self._anno_feature.items() if n in keep_set}
        return kept_named

    # -- pins -----------------------------------------------------------------

    def pin(self, name) -> None:
        self._pinned.add(name)

    def unpin(self, name) -> None:
        self._pinned.discard(name)

    def is_pinned(self, name) -> bool:
        return name in self._pinned

    def pinned_object_ids(self) -> set:
        """``id()`` of every currently-pinned object still on the drawing."""
        return {id(self._named[n]) for n in self._pinned if n in self._named}

    # -- build issues ---------------------------------------------------------

    def record_issue(self, issue) -> None:
        """Record a build-time :class:`LintIssue` (already constructed)."""
        self._build_issues.append(issue)

    def reset_issues(self) -> None:
        """Drop all build issues (re-annotation starts from a clean slate)."""
        self._build_issues = []

    def drop_issues(self, codes) -> None:
        """Drop recorded build issues whose ``code`` is in *codes* — e.g. when a
        fallback restores annotations the layout had tentatively dropped."""
        drop = set(codes)
        self._build_issues = [i for i in self._build_issues if i.code not in drop]
