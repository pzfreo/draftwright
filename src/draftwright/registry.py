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

    def add(self, obj, name, view):
        """Register *obj* under *name* and record its owning *view*.

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
        return displaced

    def remove(self, name):
        """Forget *name* (object, view, pin); returns the object or ``None``."""
        obj = self._named.pop(name, None)
        if obj is not None:
            self._pinned.discard(name)  # a removed name carries no pin (#89)
            self._anno_view.pop(name, None)
        return obj

    def clear(self, keep) -> dict:
        """Drop every name except those in *keep*; returns the kept ``{name: obj}``
        so the caller can prune the render list to match."""
        keep_set = set(keep)
        kept_named = {n: o for n, o in self._named.items() if n in keep_set}
        self._named = kept_named
        self._pinned &= keep_set  # drop pins for cleared names (#89)
        self._anno_view = {n: v for n, v in self._anno_view.items() if n in keep_set}
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
