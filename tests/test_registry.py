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
    assert r.issues == ("first", "second")  # an immutable snapshot, not the live list


class _Issue:
    def __init__(self, code, resolved=False):
        self.code = code
        self.resolved = resolved


def test_drop_issues_by_code_and_reset():
    r = AnnotationRegistry()
    for c in ("a", "b", "c"):
        r.record_issue(_Issue(c))
    r.drop_issues(["b"])
    assert [i.code for i in r.issues] == ["a", "c"]
    r.drop_issues(("a", "c"))  # accepts any iterable of codes
    assert r.issues == ()
    r.record_issue(_Issue("x"))
    r.reset_issues()
    assert r.issues == ()


def test_drop_issues_where_preserves_unresolved_findings_with_the_same_code():
    r = AnnotationRegistry()
    for issue in (_Issue("location", True), _Issue("location"), _Issue("other", True)):
        r.record_issue(issue)

    r.drop_issues_where(("location",), lambda issue: issue.resolved)

    assert [(issue.code, issue.resolved) for issue in r.issues] == [
        ("location", False),
        ("other", True),
    ]


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


def test_identity_of_reapply_round_trips_every_axis():
    # The remove/re-add transactions (callout re-route, hole-table fallback, detail-view
    # retry) restore through this pair. It must carry the measurement id too: a restored
    # dim that has lost it reads as "nothing claims it" to the audit — a false negative in
    # exactly the tool this identity exists to feed (#1002, Codex r2).
    r = AnnotationRegistry()
    obj, feat = object(), object()
    r.add(obj, "d1", "front", feature=feat, measurement=("bore.depth",))
    r.pin("d1")
    ident = r.identity_of("d1")

    removed = r.remove("d1")
    assert r.identity_of("d1") == {  # gone in every axis, not just the object
        "view": None,
        "feature": None,
        "measurement": (),
        "pinned": False,
    }

    r.add(removed, "d1", None)  # the bare re-place the call sites do …
    r.reapply("d1", ident)  # … then the identity, as a unit
    assert r.view_of("d1") == "front"
    assert r.feature_of("d1") is feat
    assert r.measurement_of("d1") == ("bore.depth",)
    assert r.is_pinned("d1")


def test_reapply_clears_axes_the_identity_does_not_carry():
    # Authoritative, not additive: a restore must never leave the name wearing metadata
    # from whatever briefly held the slot.
    r = AnnotationRegistry()
    r.add(object(), "d1", "plan", feature=object(), measurement=("width.length",))
    r.pin("d1")
    r.reapply("d1", {"view": "front", "feature": None, "measurement": (), "pinned": False})
    assert r.view_of("d1") == "front"
    assert r.feature_of("d1") is None
    assert r.measurement_of("d1") == ()
    assert not r.is_pinned("d1")


def test_identity_of_covers_every_per_name_axis():
    """Ratchet: a FOURTH identity axis cannot be added and then silently forgotten.

    That is the defect this pair exists to end — `_anno_feature` (#398) and
    `_anno_measurement` (#1002) were each added without updating every restore site, and
    each loss was found by a reviewer rather than by a test. The keys of `identity_of`
    mirror the `_anno_*` map names so the two can be compared mechanically here.
    """
    r = AnnotationRegistry()
    axes = {n for n in vars(r) if n.startswith("_anno_")}
    ident = r.identity_of("nothing-registered")
    assert {f"_anno_{k}" for k in ident if k != "pinned"} == axes
    assert "pinned" in ident  # the non-`_anno_` axis, asserted explicitly


def test_every_remove_and_restore_site_goes_through_the_identity_pair():
    """Ratchet: the restore SITES, not just the primitive (Codex #1002 r2).

    A registry-level round-trip cannot catch a call site that hand-rolls the axes — which is
    precisely how this bug arrived three times. Any annotation pass that removes a name and
    puts it back must read `identity_of` and write `reapply`; a pass that removes without
    restoring says so here, with a reason.
    """
    import ast
    import pathlib

    # function name -> why it may remove without restoring identity
    EXEMPT = {
        "_clear_section_reservation": "deletes the reserved section marks outright; no restore",
        "_discard_attempt_annotations": (
            "permanently deletes table/balloon geometry from an uncommitted attempt"
        ),
        "_stash_annotations": (
            "captures the identity half of the shared stash/restore transaction; paired below"
        ),
    }

    root = pathlib.Path("src/draftwright/annotations")
    offenders = []
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls = {
                n.func.attr
                for n in ast.walk(fn)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            }
            if "remove" not in calls or fn.name in EXEMPT:
                continue
            if not {"identity_of", "reapply"} <= calls:
                offenders.append(f"{path.name}:{fn.name}")
    assert offenders == [], (
        "these remove a named annotation without round-tripping its identity: "
        + ", ".join(offenders)
    )

    # #1144: a replacement transaction spans table/balloon placement before it knows
    # whether the shared result can commit. Keep its one stash/snapshot seam load-bearing so
    # moving the old hand-rolled identity bug into a helper cannot satisfy this ratchet by
    # exemption alone.
    common_tree = ast.parse((root / "_common.py").read_text(encoding="utf-8"))
    functions = {
        fn.name: fn
        for fn in ast.walk(common_tree)
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    def calls(function_name):
        return {
            node.func.attr
            for node in ast.walk(functions[function_name])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }

    assert {"identity_of", "remove"} <= calls("_stash_annotations")
    assert {"restore", "restore_issues"} <= calls("_restore_annotation_transaction")
