"""Tests for draftwright.linting.lint_drawing — the duck-typed structural lint.

Vendored from build123d_drafting's test_helpers.py (ADR 0007): the TestLintDrawing
/ TestLintIssueCode / TestAnnotationOverlapLabelBbox / TestLintViewShapes classes.
Page bounds are passed explicitly via page_bbox (draftwright severed the set_page
module-global, ADR 0007); the one set_page/clear_page-based test is adapted to the
omit-page-bbox default, which is the equivalent "no page context" state.
"""

from types import SimpleNamespace

import pytest
from build123d import Box, Circle, Draft, Location, Pos
from build123d_drafting import (
    Centerline,
    DatumFeature,
    DatumTarget,
    Dimension,
    Leader,
    Note,
    SurfaceFinish,
    annotate,
    find_interferences,
    place_dims,
)

from draftwright.linting import lint_drawing


@pytest.fixture
def draft():
    return Draft(font_size=2.5, decimal_precision=1)


class TestLintDrawing:
    def test_empty_list_returns_no_issues(self):
        assert lint_drawing([]) == []

    def test_label_value_matches_length_no_issue(self, draft):
        d = Dimension((-10, 0, 0), (10, 0, 0), "above", 8, draft, label="20")
        issues = [
            i for i in lint_drawing([d]) if "axis swap" in i.message or "differs from" in i.message
        ]
        assert issues == []

    def test_label_value_diverges_from_length(self, draft):
        d = Dimension((-10, 0, 0), (10, 0, 0), "above", 8, draft, label="35")
        issues = lint_drawing([d])
        assert any("35" in i.message or "differs from" in i.message for i in issues)
        assert any(i.severity == "warning" for i in issues)

    def test_dim_overlapping_part_flagged(self, draft):
        d = Dimension((-5, 0, 0), (5, 0, 0), "above", 1, draft, label="10")

        class FakeBBox:
            class _pt:
                pass

            min = _pt()
            min.X = -20
            min.Y = -20
            max = _pt()
            max.X = 20
            max.Y = 20

        issues = lint_drawing([d], part_bbox=FakeBBox())
        assert any("overlap" in i.message.lower() for i in issues)

    def test_leader_elbow_outside_text_no_issue(self, draft):
        ld = Leader((0, 0, 0), (20, 10, 0), "label", draft)
        assert [i for i in lint_drawing([ld]) if "Leader" in i.message] == []

    def test_mixed_items_checked(self, draft):
        d = Dimension((-10, 0, 0), (10, 0, 0), "above", 8, draft, label="20")
        ld = Leader((50, 0, 0), (70, 10, 0), "Ra 1.6", draft)
        assert lint_drawing([d, ld]) == []

    def test_overlapping_dims_flagged(self, draft):
        a = Dimension((-10, 0, 0), (10, 0, 0), "above", 8, draft, label="20")
        b = Dimension((-10, 0, 0), (10, 0, 0), "above", 8, draft, label="20")
        assert any("overlap" in i.message.lower() for i in lint_drawing([a, b]))

    def test_stacked_dims_not_flagged(self, draft):
        inner = Dimension((-10, 0, 0), (10, 0, 0), "above", 8, draft, label="20")
        outer = Dimension((-10, 0, 0), (10, 0, 0), "above", 18, draft, label="20")
        assert [i for i in lint_drawing([inner, outer]) if "overlap" in i.message.lower()] == []

    def test_labelless_items_overlap_via_memoized_box(self):
        # #161: label-less items are compared via bounding_box(), now computed
        # once per item instead of once per pair. Overlap detection is unchanged.

        def _item(x0, y0, x1, y1, label):
            bb = SimpleNamespace(min=SimpleNamespace(X=x0, Y=y0), max=SimpleNamespace(X=x1, Y=y1))
            return SimpleNamespace(label_bbox=None, label=label, bounding_box=lambda b=bb: b)

        a = _item(0, 0, 10, 10, "A")
        b = _item(5, 5, 15, 15, "B")  # overlaps A
        c = _item(100, 100, 110, 110, "C")  # clear of both
        overlaps = [i for i in lint_drawing([a, b, c]) if i.code == "annotation_overlap"]
        assert len(overlaps) == 1
        assert "'A'" in overlaps[0].message and "'B'" in overlaps[0].message

    def test_duck_typed_namespace_dim(self, draft):
        # lint must work on a lightweight SimpleNamespace stand-in (the MCP uses these)

        ns = SimpleNamespace(label="999", measured_length=20.0, label_bbox=None)
        codes = {i.code for i in lint_drawing([ns])}
        assert "label_vs_measured" in codes

    def test_annotate_label_is_read_when_item_has_no_label(self):
        # #146: annotate(label=...) attaches a label that lint_drawing() reads
        # when the object carries none (e.g. a vanilla build123d ExtensionLine).

        ns = SimpleNamespace(measured_length=20.0, label_bbox=None)
        # with no label attached, lint has nothing to compare and stays silent
        assert "label_vs_measured" not in {i.code for i in lint_drawing([ns])}
        # annotate() attaches the label the docstring promises lint will read
        annotate(ns, "width", label="999")
        assert "label_vs_measured" in {i.code for i in lint_drawing([ns])}


class TestLintIssueCode:
    def test_find_interferences_sets_codes(self, draft):
        dims = place_dims(
            [
                ((-18, -10, 0), (18, -10, 0), "below", "36"),
                ((-18, -10, 0), (0, -10, 0), "below", "18"),
            ],
            draft,
            base_distance=5,
        )
        codes = {i.code for i in find_interferences(dims)}
        assert "line_pierces_label" in codes
        assert "redundant_lines" in codes
        assert "" not in codes

    def test_lint_drawing_sets_codes(self, draft):
        d = Dimension((-10, 0, 0), (10, 0, 0), "above", 8, draft, label="999")
        codes = {i.code for i in lint_drawing([d])}
        assert "label_vs_measured" in codes


class TestAnnotationOverlapLabelBbox:
    """Regression: annotation_overlap must compare label text boxes, not full
    bboxes. Full bboxes include witness lines that legitimately overlap for
    stacked dimensions — that was always a false positive. Issue #149."""

    def test_stacked_dims_from_same_anchor_no_false_positive(self, draft):
        # Four stacked height dims from the same left anchor — all witness lines
        # share the same X and their full bboxes nest inside each other. Only
        # the label text regions should be compared; they don't overlap.
        dims = place_dims(
            [
                ((0, 0, 0), (0, 15, 0), "left", "15"),
                ((0, 0, 0), (0, 30, 0), "left", "30"),
                ((0, 0, 0), (0, 45, 0), "left", "45"),
                ((0, 0, 0), (0, 60, 0), "left", "60"),
            ],
            draft,
            base_distance=8,
        )
        overlaps = [i for i in lint_drawing(dims) if i.code == "annotation_overlap"]
        assert overlaps == [], "stacked dims should not produce annotation_overlap — " + "; ".join(
            i.message for i in overlaps
        )

    def test_truly_overlapping_labels_still_flagged(self, draft):
        # Two dims placed at the same offset with the same span → labels land
        # on top of each other → should still be flagged.
        d1 = Dimension((-10, 0, 0), (10, 0, 0), "above", 8, draft, label="20")
        d2 = Dimension((-10, 0, 0), (10, 0, 0), "above", 8, draft, label="20")
        overlaps = [i for i in lint_drawing([d1, d2]) if i.code == "annotation_overlap"]
        assert overlaps, "identical dims with overlapping labels should fire annotation_overlap"


class TestLintViewShapes:
    """Tests for view_shapes parameter — #159 (view vs annotation) and #160 (view vs view)."""

    def _make_box_shape(self, x, y, w, h):
        """Return a build123d Box located at (x+w/2, y+h/2) — stands in for a projected view."""

        return Pos(x + w / 2, y + h / 2, 0) * Box(w, h, 0.01)

    # --- no view_shapes: existing behaviour unchanged ---

    def test_no_view_shapes_no_new_codes(self, draft):
        d = Dimension((-10, 0, 0), (10, 0, 0), "above", 8, draft, label="20")
        codes = {i.code for i in lint_drawing([d])}
        assert "view_annotation_overlap" not in codes
        assert "view_overlap" not in codes

    # --- #159: view vs annotation ---

    def test_view_overlapping_annotation_flagged(self, draft):
        # View at x=0–40, y=0–30; dim label straddles the view's right outline
        view = self._make_box_shape(0, 0, 40, 30)
        d = Dimension((35, 10, 0), (45, 10, 0), "above", 4, draft, label="10")
        codes = {i.code for i in lint_drawing([d], view_shapes=[view])}
        assert "view_annotation_overlap" in codes

    def test_view_not_overlapping_annotation_not_flagged(self, draft):
        # View at x=0–40, y=0–30; dim far away at x=100
        view = self._make_box_shape(0, 0, 40, 30)
        d = Dimension((100, 10, 0), (120, 10, 0), "above", 4, draft, label="20")
        codes = {i.code for i in lint_drawing([d], view_shapes=[view])}
        assert "view_annotation_overlap" not in codes

    def test_view_annotation_overlap_severity_warning(self, draft):
        view = self._make_box_shape(0, 0, 40, 30)
        d = Dimension((35, 10, 0), (45, 10, 0), "above", 4, draft, label="10")
        issues = [
            i for i in lint_drawing([d], view_shapes=[view]) if i.code == "view_annotation_overlap"
        ]
        assert issues and issues[0].severity == "warning"

    # --- #76: label over a blank region inside the view bbox is only a notice ---

    # --- #143: persisted per-edge bbox cache across repeated lints ---

    def test_view_edge_cache_gives_same_result(self, draft):
        view = self._make_box_shape(0, 0, 40, 30)
        d = Dimension((35, 10, 0), (45, 10, 0), "above", 4, draft, label="10")
        without = {i.code for i in lint_drawing([d], view_shapes=[view])}
        with_cache = {i.code for i in lint_drawing([d], view_shapes=[view], view_edge_cache={})}
        assert without == with_cache
        assert "view_annotation_overlap" in with_cache

    def test_view_edge_cache_reused_not_rebuilt(self, draft):
        view = self._make_box_shape(0, 0, 40, 30)
        d = Dimension((35, 10, 0), (45, 10, 0), "above", 4, draft, label="10")
        cache: dict = {}
        lint_drawing([d], view_shapes=[view], view_edge_cache=cache)
        assert cache  # populated with the view's per-edge entries
        key = next(iter(cache))
        first = cache[key]
        # second lint of the same view must reuse the cached entry object, not
        # rebuild it (rebuilding would reassign cache[key] to a new tuple)
        lint_drawing([d], view_shapes=[view], view_edge_cache=cache)
        assert cache[key] is first

    def test_label_over_blank_interior_is_info_not_warning(self, draft):
        # The view bbox is mostly blank face here; a label deliberately placed
        # over an empty region (hole-callout convention on big parts) must not
        # warn — it gets an info-level notice instead.
        view = self._make_box_shape(0, 0, 40, 30)
        d = Dimension((5, 10, 0), (15, 10, 0), "above", 4, draft, label="10")
        issues = lint_drawing([d], view_shapes=[view])
        codes = {i.code for i in issues}
        assert "view_annotation_overlap" not in codes
        notices = [i for i in issues if i.code == "view_annotation_inside_extents"]
        assert notices and notices[0].severity == "info"

    def test_label_crossing_curved_edge_flagged(self, draft):
        # Curved edges are sampled, not bbox-tested — a label on the rim of a
        # circular outline fires, one at the blank centre does not.

        view = Pos(20, 15, 0) * Circle(10)
        on_rim = Note("X", (10.5, 15), draft)
        codes = {i.code for i in lint_drawing([on_rim], view_shapes=[view])}
        assert "view_annotation_overlap" in codes

    def test_label_inside_curved_outline_is_info(self, draft):

        view = Pos(20, 15, 0) * Circle(10)
        centre = Note("X", (20, 15), draft)
        issues = lint_drawing([centre], view_shapes=[view])
        assert not any(i.code == "view_annotation_overlap" for i in issues)
        assert any(i.code == "view_annotation_inside_extents" for i in issues)

    # --- #65: line-work that legitimately touches the view must not fire ---

    def test_centerline_crossing_view_not_flagged(self):
        # A centreline must cross the feature it marks — never a finding.
        view = self._make_box_shape(0, 0, 40, 30)
        cl = Centerline((20, -5, 0), (20, 35, 0))
        codes = {i.code for i in lint_drawing([cl], view_shapes=[view])}
        assert "view_annotation_overlap" not in codes

    def test_dim_witness_lines_into_view_not_flagged(self, draft):
        # Witness lines run from the feature (inside the view) out to the dim
        # line; full bbox overlaps the view but the label sits outside it.
        view = self._make_box_shape(0, 0, 40, 30)
        d = Dimension((5, 25, 0), (15, 25, 0), "above", 10, draft, label="10")
        codes = {i.code for i in lint_drawing([d], view_shapes=[view])}
        assert "view_annotation_overlap" not in codes

    def test_leader_tip_in_view_label_outside_not_flagged(self, draft):
        # Leader tips touch the part outline by definition.
        view = self._make_box_shape(0, 0, 40, 30)
        ld = Leader(tip=(20, 15, 0), elbow=(50, 15, 0), label="ø4", draft=draft)
        codes = {i.code for i in lint_drawing([ld], view_shapes=[view])}
        assert "view_annotation_overlap" not in codes

    def test_leader_label_on_view_outline_still_flagged(self, draft):
        # The real failure mode — label text sitting on the part's line-work —
        # must still fire (#76: only edge crossings warn, not blank regions).
        view = self._make_box_shape(0, 0, 40, 30)
        ld = Leader(tip=(20, 15, 0), elbow=(36, 15, 0), label="ø4", draft=draft)
        codes = {i.code for i in lint_drawing([ld], view_shapes=[view])}
        assert "view_annotation_overlap" in codes

    def test_datum_triangle_in_view_not_flagged(self, draft):
        # #69 — the datum triangle attaches to a part edge by design; only the
        # letter frame matters. Tip on the view's top edge, frame above it.

        view = self._make_box_shape(0, 0, 40, 30)
        df = DatumFeature("A", draft).moved(Location((20, 27, 0)))
        codes = {i.code for i in lint_drawing([df], view_shapes=[view])}
        assert "view_annotation_overlap" not in codes

    def test_datum_letter_frame_on_view_outline_still_flagged(self, draft):

        view = self._make_box_shape(0, 0, 40, 30)
        df = DatumFeature("A", draft).moved(Location((40, 15, 0)))  # frame straddles x=40
        codes = {i.code for i in lint_drawing([df], view_shapes=[view])}
        assert "view_annotation_overlap" in codes

    def test_surface_finish_mark_in_view_not_flagged(self, draft):
        # #69 — the check-mark sits on the surface line; the Ra text is
        # outside the view here, so nothing should fire.
        view = self._make_box_shape(0, 0, 40, 30)
        sf = SurfaceFinish("Ra 1.6", (38, 15), draft=draft)
        codes = {i.code for i in lint_drawing([sf], view_shapes=[view])}
        assert "view_annotation_overlap" not in codes

    def test_surface_finish_text_on_view_outline_still_flagged(self, draft):
        view = self._make_box_shape(0, 0, 40, 30)
        sf = SurfaceFinish("Ra 1.6", (35, 15), draft=draft)  # Ra text straddles x=40
        codes = {i.code for i in lint_drawing([sf], view_shapes=[view])}
        assert "view_annotation_overlap" in codes

    def test_datum_target_in_view_not_flagged(self, draft):
        # #71 — a datum target sits on the part face by definition (ISO 5459);
        # fully inside the view is its only correct placement.

        view = self._make_box_shape(0, 0, 40, 30)
        dt = DatumTarget("A1", draft=draft).moved(Location((20, 15, 0)))
        codes = {i.code for i in lint_drawing([dt], view_shapes=[view])}
        assert "view_annotation_overlap" not in codes

    def test_datum_target_exemption_does_not_leak_to_page_bounds(self, draft):
        # The exemption is view-overlap only — a datum target off the page
        # must still fire annotation_out_of_bounds.

        dt = DatumTarget("A1", draft=draft).moved(Location((-50, -50, 0)))
        issues = lint_drawing([dt], page_bbox=(0, 0, 100, 100))
        assert any(i.code == "annotation_out_of_bounds" for i in issues)

    # --- #160: view vs view ---

    def test_overlapping_views_flagged(self):
        v1 = self._make_box_shape(0, 0, 60, 40)
        v2 = self._make_box_shape(50, 0, 60, 40)  # overlaps v1 by 10mm in X
        codes = {i.code for i in lint_drawing([], view_shapes=[v1, v2])}
        assert "view_overlap" in codes

    def test_non_overlapping_views_not_flagged(self):
        v1 = self._make_box_shape(0, 0, 60, 40)
        v2 = self._make_box_shape(70, 0, 60, 40)  # 10mm gap
        codes = {i.code for i in lint_drawing([], view_shapes=[v1, v2])}
        assert "view_overlap" not in codes

    def test_view_overlap_severity_warning(self):
        v1 = self._make_box_shape(0, 0, 60, 40)
        v2 = self._make_box_shape(50, 0, 60, 40)
        issues = [i for i in lint_drawing([], view_shapes=[v1, v2]) if i.code == "view_overlap"]
        assert issues and issues[0].severity == "warning"

    def test_three_views_only_adjacent_pairs_flagged(self):
        # v1 overlaps v2; v2 overlaps v3; v1 and v3 do not overlap each other
        v1 = self._make_box_shape(0, 0, 60, 40)
        v2 = self._make_box_shape(50, 0, 60, 40)  # overlaps v1
        v3 = self._make_box_shape(100, 0, 60, 40)  # overlaps v2, clear of v1
        issues = [
            i for i in lint_drawing([], view_shapes=[v1, v2, v3]) if i.code == "view_overlap"
        ]
        assert len(issues) == 2

    def test_empty_view_shapes_list_no_error(self, draft):
        d = Dimension((-10, 0, 0), (10, 0, 0), "above", 8, draft, label="20")
        issues = lint_drawing([d], view_shapes=[])
        assert not any(i.code in ("view_overlap", "view_annotation_overlap") for i in issues)

    def test_invalid_view_shape_skipped_gracefully(self, draft):
        # A non-shape object with no bounding_box should be silently skipped

        bad = SimpleNamespace()  # no bounding_box attribute
        d = Dimension((-10, 0, 0), (10, 0, 0), "above", 8, draft, label="20")
        issues = lint_drawing([d], view_shapes=[bad])  # must not raise
        assert not any(i.code == "view_annotation_overlap" for i in issues)

    def test_same_shape_in_items_and_view_shapes_no_self_overlap(self):
        # A shape passed in both lists must not generate a spurious self-overlap warning
        view = self._make_box_shape(0, 0, 40, 30)
        issues = lint_drawing([view], view_shapes=[view])
        assert not any(i.code == "view_annotation_overlap" for i in issues)

    # --- #75: view vs drawable area ---

    def test_view_past_page_edge_is_error(self):
        view = self._make_box_shape(80, 10, 40, 30)  # spans x=80–120 on a 100-wide page
        issues = [
            i
            for i in lint_drawing([], page_bbox=(0, 0, 100, 100), view_shapes=[view])
            if i.code == "view_out_of_bounds"
        ]
        assert issues and issues[0].severity == "error"
        assert "right by 20.0 mm" in issues[0].message

    def test_view_inside_page_not_flagged(self):
        view = self._make_box_shape(10, 10, 40, 30)
        codes = {i.code for i in lint_drawing([], page_bbox=(0, 0, 100, 100), view_shapes=[view])}
        assert "view_out_of_bounds" not in codes

    def test_view_bounds_not_checked_without_page(self):
        # no page context (omit page_bbox) → views cannot be bounds-checked
        view = self._make_box_shape(80, 10, 40, 30)
        codes = {i.code for i in lint_drawing([], view_shapes=[view])}
        assert "view_out_of_bounds" not in codes


class TestLintSelfSilencing:
    """#701: the check bodies run unguarded — only the fragile duck-typed reads
    are caught, and every swallowed failure is logged, so a broken check fails
    loudly instead of silently reporting nothing forever."""

    def test_raising_label_bbox_is_logged_not_swallowed(self, caplog):
        import logging

        class BadLabelBbox:
            elbow = (1.0, 1.0)  # leader-like → dispatched to _lint_leader

            @property
            def label_bbox(self):
                raise RuntimeError("boom")

        with caplog.at_level(logging.WARNING, logger="draftwright.linting.structural"):
            issues = lint_drawing([BadLabelBbox()])
        # several checks read the same item's label_bbox — warned exactly once (#711 review)
        assert caplog.text.count("unreadable label_bbox") == 1
        assert isinstance(issues, list)  # lint completed; the bad item was skipped

    def test_broken_elbow_is_logged_not_swallowed(self, caplog):
        import logging

        item = SimpleNamespace(elbow=object(), label_bbox=(0.0, 0.0, 10.0, 5.0), label="L")
        with caplog.at_level(logging.WARNING, logger="draftwright.linting.structural"):
            issues = lint_drawing([item])
        assert "unreadable elbow" in caplog.text
        assert not any(i.code == "leader_line_through_text" for i in issues)

    def test_leader_elbow_inside_label_still_flagged(self):
        # the check itself still fires once the reads succeed
        item = SimpleNamespace(elbow=(5.0, 2.0), label_bbox=(0.0, 0.0, 10.0, 5.0), label="L")
        issues = lint_drawing([item])
        assert any(i.code == "leader_line_through_text" for i in issues)

    def test_internal_bug_fails_loudly_not_silently(self):
        # a malformed 2-tuple label_bbox breaks the pairwise overlap arithmetic;
        # pre-#701 the whole-body `except Exception: pass` hid it forever.
        a = SimpleNamespace(label_bbox=(0.0, 0.0), label="A")
        b = SimpleNamespace(label_bbox=(0.0, 0.0, 4.0, 4.0), label="B")
        with pytest.raises(IndexError):
            lint_drawing([a, b])
