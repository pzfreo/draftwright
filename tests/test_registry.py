"""Unit tests for AnnotationRegistry — the single owner of annotation identity,
ownership, pins, and build issues (#138 / ADR 1 (was 0005), Step 2)."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from draftwright.registry import (
    AnnotationRegistry,
    CandidateRegion,
    DimensionPlacementSpec,
    PlacedDimension,
    RegisteredDimensionSpec,
    SectionMark,
)

# Pure unit tests — no OCC builds — so they join the build-light `smoke` set (#153).
pytestmark = pytest.mark.smoke


def _foreign_helper_metadata_sites(root: Path) -> list[str]:
    marker = "_" + "dw_"
    return sorted(
        str(path) for path in root.rglob("*.py") if marker in path.read_text(encoding="utf-8")
    )


def test_no_foreign_helper_metadata_side_channel_issue_1931(tmp_path):
    source = Path(__file__).resolve().parents[1] / "src"
    assert _foreign_helper_metadata_sites(source) == []
    # The guard must reject a newly introduced attribute string.
    (tmp_path / "foreign.py").write_text(
        'setattr(annotation, "_' + 'dw_next", value)', encoding="utf-8"
    )
    assert _foreign_helper_metadata_sites(tmp_path) == [str(tmp_path / "foreign.py")]


def test_measurement_presence_requires_the_live_exact_owner_and_parameter():
    owner = SimpleNamespace(diameter=40)
    identity = SimpleNamespace(feature=owner, parameter="step.diameter")
    registry = AnnotationRegistry()
    registry.add(object(), "diameter", "front", measurement=identity)
    assert registry.has_measurement(identity)
    assert not registry.has_measurement(
        SimpleNamespace(feature=SimpleNamespace(diameter=40), parameter="step.diameter")
    )
    assert not registry.has_measurement(SimpleNamespace(feature=owner, parameter="step.length"))
    registry.remove("diameter")
    assert not registry.has_measurement(identity)


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


def test_candidate_region_is_typed_and_follows_live_annotation_identity():
    r = AnnotationRegistry()
    original = SimpleNamespace()
    r.add(original, "d1", "front", candidate_region=CandidateRegion.INTERIOR)
    assert r.candidate_region_of("d1") is CandidateRegion.INTERIOR
    assert not hasattr(original, "_dw_candidate_region")

    snap = r.snapshot()
    identity = r.identity_of("d1")
    r.remove("d1")
    assert r.candidate_region_of("d1") is None
    r.add(original, "d1", "front")
    r.reapply("d1", identity)
    assert r.candidate_region_of("d1") is CandidateRegion.INTERIOR

    r.add(object(), "d1", "front", candidate_region=CandidateRegion.EXTERIOR)
    assert r.candidate_region_of("d1") is CandidateRegion.EXTERIOR
    r.restore(snap)
    assert r.named("d1") is original
    assert r.candidate_region_of("d1") is CandidateRegion.INTERIOR

    replacement = object()
    r.replace_object(original, replacement)
    assert r.named("d1") is replacement
    assert r.candidate_region_of("d1") is None

    r.add(object(), "d2", "plan", candidate_region=CandidateRegion.EXTERIOR)
    r.clear(())
    assert r.candidate_region_of("d1") is None
    assert r.candidate_region_of("d2") is None


def test_candidate_region_rejects_unknown_provenance():
    r = AnnotationRegistry()
    with pytest.raises(ValueError, match="not a valid CandidateRegion"):
        r.add(object(), "d1", "front", candidate_region="guessed")
    assert r.named("d1") is None


def test_detail_scale_follows_live_annotation_identity():
    r = AnnotationRegistry()
    original = SimpleNamespace()
    r.add(original, "detail", "section_aa", scale=4.0)
    assert r.scale_of("detail") == 4.0
    assert not hasattr(original, "_dw_scale")

    snap = r.snapshot()
    identity = r.identity_of("detail")
    removed = r.remove("detail")
    assert r.scale_of("detail") is None
    r.add(removed, "detail", None)
    r.reapply("detail", identity)
    assert r.scale_of("detail") == 4.0

    replacement = SimpleNamespace()
    r.replace_object(original, replacement)
    assert r.scale_of("detail") == 4.0  # repair keeps this name's scale
    r.add(SimpleNamespace(), "detail", "front")
    assert r.scale_of("detail") is None  # a new mark inherits nothing
    r.restore(snap)
    assert r.scale_of("detail") == 4.0
    r.clear(())
    assert r.scale_of("detail") is None


@pytest.mark.parametrize(
    "attribute", ["_dw_candidate_region", "_dw_scale", "_dw_measurement_span"]
)
def test_registry_metadata_never_returns_to_helper_object_attributes(attribute):
    source = Path(__file__).parents[1] / "src" / "draftwright"
    offenders = [
        path.relative_to(source)
        for path in source.rglob("*.py")
        if attribute in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_measurement_span_follows_name_across_replacement_and_rollback():
    registry = AnnotationRegistry()
    original = SimpleNamespace()
    span = ((0, 0, 0), (4, 0, 0))
    registry.add(original, "dimension", "front", measurement_span=span)
    assert registry.measurement_span_of("dimension") == span
    assert not hasattr(original, "_dw_measurement_span")

    snapshot = registry.snapshot()
    identity = registry.identity_of("dimension")
    registry.replace_object(original, object())
    assert registry.measurement_span_of("dimension") == span
    registry.remove("dimension")
    assert registry.measurement_span_of("dimension") is None
    with pytest.raises(KeyError):
        registry.mark_measurement_span("dimension", span)

    registry.add(original, "dimension", "plan")
    registry.reapply("dimension", identity)
    assert registry.measurement_span_of("dimension") == span
    registry.add(object(), "dimension", "front")
    assert registry.measurement_span_of("dimension") is None
    registry.restore(snapshot)
    assert registry.named("dimension") is original
    assert registry.measurement_span_of("dimension") == span

    registry.add(object(), "other", "plan", measurement_span=span)
    registry.clear(("dimension",))
    assert registry.measurement_span_of("dimension") == span
    assert registry.measurement_span_of("other") is None
    registry.reapply("dimension", {"measurement_span": None})
    assert registry.measurement_span_of("dimension") is None


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
    obj, feat, declaration = object(), object(), object()
    r.add(
        obj,
        "d1",
        "front",
        feature=feat,
        measurement=("bore.depth",),
        satisfaction=("counterbore.depth",),
        declaration=declaration,
    )
    r.pin("d1")
    mark = SectionMark(2.0, "section_aa")
    r.mark_section("d1", mark)
    ident = r.identity_of("d1")

    removed = r.remove("d1")
    assert r.identity_of("d1") == {  # gone in every axis, not just the object
        "view": None,
        "feature": None,
        "declaration": None,
        "measurement": (),
        "cells": (),
        "satisfaction": (),
        "section": None,
        "dimension_spec": None,
        "candidate_region": None,
        "scale": None,
        "measurement_span": None,
        "pinned": False,
    }

    r.add(removed, "d1", None)  # the bare re-place the call sites do …
    r.reapply("d1", ident)  # … then the identity, as a unit
    assert r.view_of("d1") == "front"
    assert r.feature_of("d1") is feat
    assert r.declaration_of("d1") is declaration
    assert r.measurement_of("d1") == ("bore.depth",)
    assert r.satisfaction_of("d1") == ("counterbore.depth",)
    assert r.section_of("d1") is mark
    assert r.is_pinned("d1")


def test_reapply_clears_axes_the_identity_does_not_carry():
    # Authoritative, not additive: a restore must never leave the name wearing metadata
    # from whatever briefly held the slot.
    r = AnnotationRegistry()
    r.add(
        object(),
        "d1",
        "plan",
        feature=object(),
        measurement=("width.length",),
        satisfaction=("height.length",),
        declaration=object(),
    )
    r.pin("d1")
    r.reapply(
        "d1",
        {
            "view": "front",
            "feature": None,
            "declaration": None,
            "measurement": (),
            "pinned": False,
        },
    )
    assert r.view_of("d1") == "front"
    assert r.feature_of("d1") is None
    assert r.declaration_of("d1") is None
    assert r.measurement_of("d1") == ()
    assert r.satisfaction_of("d1") == ()
    assert r.section_of("d1") is None
    assert not r.is_pinned("d1")


def test_section_mark_lifecycle_issue_1931():
    r = AnnotationRegistry()
    line, other = object(), object()
    mark = SectionMark(2.0, "section_aa")
    assert not hasattr(line, "_dw_section_cut_y")
    with pytest.raises(KeyError):
        r.mark_section("line", mark)
    r.add(line, "line", None)
    r.mark_section("line", mark)
    assert r.has_section(2.0, {"section_aa"})
    assert not r.has_section(3.0, {"section_aa"})
    assert not r.has_section(2.0, set())
    with pytest.raises(AttributeError):
        mark.cut_y = 3.0
    assert r.section_of("line") is mark

    snap = r.snapshot()
    r.add(other, "line", None)
    assert r.section_of("line") is None  # same-name unmarked replacement
    assert not r.has_section(2.0, {"section_aa"})
    r.restore(snap)
    assert r.named("line") is line and r.section_of("line") is mark

    r.replace_object(line, other)
    assert r.section_of("line") is None  # object replacement also loses the old mark
    assert not r.has_section(2.0, {"section_aa"})
    r.mark_section("line", mark)
    assert r.remove("line") is other
    assert r.section_of("line") is None

    r.add(line, "line", None)
    r.mark_section("line", mark)
    r.add(other, "keep", None)
    r.clear(("keep",))
    assert r.section_of("line") is None
    r.add(line, "line", None)
    r.mark_section("line", mark)
    r.clear(("line",))
    assert r.section_of("line") is mark


def test_registered_dimension_spec_uses_live_identity_and_survives_transactions_issue_1931():
    class EqualShape(PlacedDimension):
        def __init__(self, side):
            self.placement_spec = DimensionPlacementSpec(
                [0, 0, 0],
                [10, 0, 0],
                side,
                8.0,
                SimpleNamespace(
                    font_size=3.0,
                    font="Arial",
                    font_style=SimpleNamespace(name="REGULAR"),
                ),
                {"label": "10"},
            )

        def __eq__(self, other):
            return isinstance(other, EqualShape)

        def __hash__(self):
            return 1

    r = AnnotationRegistry()
    original, replacement, equal_peer = (
        EqualShape("above"),
        EqualShape("below"),
        EqualShape("left"),
    )
    r.add(original, "dim", "front")
    r.add(equal_peer, "peer", "front")
    spec = r.dimension_spec_of("dim")
    assert isinstance(spec, RegisteredDimensionSpec)
    assert spec.side == "above"
    assert isinstance(original.placement_spec.p1, list)
    assert spec.p1 == (0.0, 0.0, 0.0)
    assert spec.p2 == (10.0, 0.0, 0.0)
    assert spec.live_draft is original.placement_spec.draft
    assert spec.live_draft.font_size == 3.0
    original.placement_spec.p1[0] = 7
    original.placement_spec.p2[0] = 20
    original.placement_spec.draft.font_size = 8.0
    assert spec.p1 == (0.0, 0.0, 0.0)
    assert spec.p2 == (10.0, 0.0, 0.0)
    assert spec.live_draft.font_size == 8.0
    original.placement_spec.side = "right"
    original.placement_spec.kwargs["label"] = "changed"
    assert spec.side == "above" and spec.kwargs["label"] == "10"
    with pytest.raises(TypeError):
        spec.kwargs["label"] = "changed"

    snapshot, identity = r.snapshot(), r.identity_of("dim")
    r.replace_object(original, replacement)
    assert r.dimension_spec_of("dim").side == "below"
    assert r.dimension_spec_of("peer").side == "left"
    r.restore(snapshot)
    assert r.named("dim") is original and r.dimension_spec_of("dim") is spec
    r.remove("dim")
    assert r.dimension_spec_of("dim") is None
    r.add(original, "dim", "front")
    r.reapply("dim", identity)
    assert r.dimension_spec_of("dim") is spec
    r.add(object(), "dim", "front")
    assert r.dimension_spec_of("dim") is None
    r.add(equal_peer, "peer", "front")
    r.clear(("dim",))
    assert r.dimension_spec_of("peer") is None


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
        "_clear_derived_view_reservation": (
            "deletes only an engine-owned planning placeholder; no restore"
        ),
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
