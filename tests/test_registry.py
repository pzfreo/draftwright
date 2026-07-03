"""Unit tests for AnnotationRegistry — the single owner of annotation identity,
ownership, pins, and build issues (#138 / ADR 0005, Step 2)."""

import pytest

from draftwright.registry import AnnotationRegistry

# Pure unit tests — no OCC builds — so they join the build-light `smoke` set (#153).
pytestmark = pytest.mark.smoke


def test_add_records_name_and_view():
    r = AnnotationRegistry()
    obj = object()
    assert r.add(obj, "d1", "front") is None  # nothing displaced
    assert r.named("d1") is obj
    assert r.view_of("d1") == "front"
    assert "d1" in r
    assert r.annotations() == {"d1": "object"}


def test_add_replace_returns_displaced_and_drops_pin():
    r = AnnotationRegistry()
    old, new = object(), object()
    r.add(old, "d1", "front")
    r.pin("d1")
    assert r.is_pinned("d1")
    displaced = r.add(new, "d1", "plan")
    assert displaced is old  # caller drops it from the render list
    assert r.named("d1") is new
    assert r.view_of("d1") == "plan"  # owner updated
    assert not r.is_pinned("d1")  # a replacement is a fresh object (#89)


def test_readd_viewless_clears_stale_owner():
    r = AnnotationRegistry()
    r.add(object(), "d1", "front")
    r.add(object(), "d1", None)
    assert r.view_of("d1") is None  # ownership map never lags _named (#121)


def test_remove_forgets_object_view_pin():
    r = AnnotationRegistry()
    obj = object()
    r.add(obj, "d1", "side")
    r.pin("d1")
    assert r.remove("d1") is obj
    assert r.named("d1") is None
    assert r.view_of("d1") is None
    assert not r.is_pinned("d1")
    assert r.remove("missing") is None  # unknown name -> None


def test_clear_keeps_only_named_and_prunes_views_pins():
    r = AnnotationRegistry()
    r.add(object(), "title_block", None)
    r.add(object(), "dim", "front")
    r.pin("dim")
    kept = r.clear(keep=("title_block",))
    assert set(kept) == {"title_block"}
    assert "dim" not in r
    assert r.view_of("dim") is None
    assert not r.is_pinned("dim")


def test_pinned_object_ids_only_live_pins():
    r = AnnotationRegistry()
    a, b = object(), object()
    r.add(a, "a", "front")
    r.add(b, "b", "front")
    r.pin("a")
    r.pin("b")
    r.remove("b")  # a pin without a live object must not linger
    assert r.pinned_object_ids() == {id(a)}


def test_unnamed_add_is_a_noop_for_identity():
    r = AnnotationRegistry()
    assert r.add(object(), None, "front") is None
    assert r.annotations() == {}  # nothing named


def test_build_issues_accumulate_in_order():
    r = AnnotationRegistry()
    r.record_issue("first")
    r.record_issue("second")
    assert r._build_issues == ["first", "second"]


class _Issue:
    def __init__(self, code):
        self.code = code


def test_drop_issues_by_code_and_reset():
    r = AnnotationRegistry()
    for c in ("a", "b", "c"):
        r.record_issue(_Issue(c))
    r.drop_issues(["b"])
    assert [i.code for i in r._build_issues] == ["a", "c"]
    r.drop_issues(("a", "c"))  # accepts any iterable of codes
    assert r._build_issues == []
    r.record_issue(_Issue("x"))
    r.reset_issues()
    assert r._build_issues == []


def test_snapshot_restore_round_trips_view_and_pin_metadata():
    # Repair-undo (repair.py) restores a snapshot when a pass net-worsens the sheet.
    # The snapshot must carry the view/pin metadata, not only the name->object map —
    # else a rolled-back pass leaves `_anno_view`/`_pinned` referencing names it added
    # or the wrong view for a re-placed dim (the identity state would be inconsistent
    # with the restored objects).
    r = AnnotationRegistry()
    a, b = object(), object()
    r.add(a, "d1", "front")
    r.add(b, "d2", "plan")
    r.pin("d1")
    snap = r.snapshot()

    # A worsening pass: move d2 to another view, add a new dim, pin it, unpin d1.
    r.add(object(), "d2", "side")  # d2 re-placed onto a different view
    r.add(object(), "d3", "front")  # a brand-new annotation
    r.pin("d3")
    r.unpin("d1")

    r.restore(snap)

    assert r.named("d1") is a and r.named("d2") is b
    assert "d3" not in r  # the added annotation is gone from identity
    assert r.view_of("d2") == "plan"  # NOT the repaired "side"
    assert r.view_of("d3") is None  # its stale view entry is gone
    assert r.is_pinned("d1") and not r.is_pinned("d3")  # pins restored exactly
