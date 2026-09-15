"""Drawing pinning, annotation-query, and view-bound behavior."""

import pytest
from build123d import Compound, Edge
from build123d_drafting import Leader


@pytest.fixture
def plain_box_dwg(shared_drawing):
    return shared_drawing("box_60x40x20")


class TestPin:
    """#89: a pinned annotation is never moved by the engine (repair today)."""

    def _two_overlapping(self, fresh_drawing):
        from draftwright._core import _dim

        dwg = fresh_drawing("box_60x40x20")
        p1, p2 = (40.0, 20.0, 0.0), (80.0, 20.0, 0.0)
        dwg._add(_dim(p1, p2, "above", 8, dwg.draft, label="AA"), "a")
        dwg._add(_dim(p1, p2, "above", 8, dwg.draft, label="BB"), "b")
        return dwg

    def test_repair_does_not_move_a_pinned_dim(self, fresh_drawing):
        # Overlaps are not repaired by fixed-step placement anymore, so pinned and
        # unpinned dimensions alike stay put.
        dwg = self._two_overlapping(fresh_drawing)
        dwg.pin("a")
        dwg.repair()
        assert dwg.get_annotation("a")._dw_spec.distance == 8
        assert dwg.get_annotation("b")._dw_spec.distance == 8

    def test_unpin_lets_repair_move_it_again(self, fresh_drawing):
        dwg = self._two_overlapping(fresh_drawing)
        dwg.pin("a").unpin("a")
        dwg.repair()
        assert dwg.get_annotation("a")._dw_spec.distance == 8
        assert dwg.get_annotation("b")._dw_spec.distance == 8
        assert [i for i in dwg.lint() if i.code == "annotation_overlap"]

    def test_pin_unknown_name_raises(self, fresh_drawing):
        dwg = fresh_drawing("box_60x40x20")
        with pytest.raises(KeyError):
            dwg.pin("does_not_exist")

    def test_pin_and_unpin_are_chainable(self, fresh_drawing):
        dwg = self._two_overlapping(fresh_drawing)
        assert dwg.pin("a") is dwg
        assert dwg.unpin("a") is dwg

    def test_pinning_both_overlap_labels_is_a_noop(self, fresh_drawing):
        # Both deliberate → the engine respects both and leaves the overlap.
        dwg = self._two_overlapping(fresh_drawing)
        dwg.pin("a").pin("b")
        dwg.repair()
        assert dwg.get_annotation("a")._dw_spec.distance == 8
        assert dwg.get_annotation("b")._dw_spec.distance == 8

    def test_pinning_a_non_dim_then_repair_does_not_crash(self, fresh_drawing):
        # _find_dim builds an id-set over pinned objects of any type; pinning a
        # Leader (not a re-placeable dim) must not break repair.
        dwg = self._two_overlapping(fresh_drawing)
        dwg._add(Leader((0, 0, 0), (10, 10, 0), "L", dwg.draft), "ldr")
        dwg.pin("ldr")
        dwg.repair()  # must not raise
        assert "ldr" in dwg.annotations()

    def test_removed_then_readded_name_is_not_still_pinned(self, fresh_drawing):
        from draftwright._core import _dim

        dwg = self._two_overlapping(fresh_drawing)
        dwg.pin("a")
        dwg.remove("a")
        # Re-add a fresh "a" at the same overlapping spot; it must NOT inherit
        # the old pin, so repair is free to move it.
        dwg._add(
            _dim((40.0, 20.0, 0.0), (80.0, 20.0, 0.0), "above", 8, dwg.draft, label="AA"), "a"
        )
        assert not dwg.registry.is_pinned("a")
        dwg.repair()
        assert dwg.get_annotation("a")._dw_spec.distance == 8


class TestAnnotationsQuery:
    """#27: introspect existing annotations by name and type."""

    def test_annotations_maps_name_to_type(self, fresh_drawing):
        dwg = fresh_drawing("box_60x40x20")
        anns = dwg.annotations()
        # A dict keyed by the names actually registered, valued by class name.
        assert isinstance(anns, dict)
        assert anns  # a box drawing has named annotations
        assert all(isinstance(k, str) and isinstance(v, str) for k, v in anns.items())
        # Every key resolves, and its reported type matches the live object.
        for name, type_name in anns.items():
            assert type(dwg.get_annotation(name)).__name__ == type_name

    def test_annotations_omits_unnamed(self, fresh_drawing):
        from draftwright._core import _dim

        dwg = fresh_drawing("box_60x40x20")
        before = dict(dwg.annotations())
        dwg._add(_dim((0, 0, 0), (40, 0, 0), "above", 8, dwg.draft, label="U"))  # no name
        # Unnamed annotation lands in items but not in the name→type map.
        assert dwg.annotations() == before
        assert len(dwg.items) == len(before) + 1

    def test_annotations_reflects_add_and_membership(self, fresh_drawing):
        from draftwright._core import _dim

        dwg = fresh_drawing("box_60x40x20")
        assert "q_dim" not in dwg.annotations()
        dwg._add(_dim((0, 0, 0), (40, 0, 0), "above", 8, dwg.draft, label="Q"), "q_dim")
        assert dwg.annotations()["q_dim"] == "Dimension"

    def test_get_annotation_returns_object_or_none(self, fresh_drawing):
        from draftwright._core import _dim

        dwg = fresh_drawing("box_60x40x20")
        obj = dwg._add(_dim((0, 0, 0), (40, 0, 0), "above", 8, dwg.draft, label="G"), "g")
        assert dwg.get_annotation("g") is obj
        assert dwg.get_annotation("does_not_exist") is None

    def test_get_annotation_follows_remove(self, fresh_drawing):
        from draftwright._core import _dim

        dwg = fresh_drawing("box_60x40x20")
        dwg._add(_dim((0, 0, 0), (40, 0, 0), "above", 8, dwg.draft, label="R"), "r")
        assert dwg.get_annotation("r") is not None
        dwg.remove("r")
        assert dwg.get_annotation("r") is None
        assert "r" not in dwg.annotations()


class TestViewBounds:
    """#28: page bounding box of a named view's projected geometry."""

    def test_view_bounds_returns_page_bbox(self, plain_box_dwg):
        dwg = plain_box_dwg
        b = dwg.view_bounds("front")
        assert b is not None and len(b) == 4
        x0, y0, x1, y1 = b
        assert x1 > x0 and y1 > y0
        # Front view (looking along Y) shows X=60 wide, Z=20 tall, at sheet scale.
        assert (x1 - x0) == pytest.approx(60 * dwg.scale, rel=1e-3)
        assert (y1 - y0) == pytest.approx(20 * dwg.scale, rel=1e-3)

    def test_view_bounds_contains_projected_centroid(self, plain_box_dwg):
        # The part centroid (world origin for a centred Box) projects inside.
        dwg = plain_box_dwg
        x0, y0, x1, y1 = dwg.view_bounds("front")
        px, py, _ = dwg.at("front", 0, 0, 0)
        assert x0 <= px <= x1
        assert y0 <= py <= y1

    def test_view_bounds_unknown_view_is_none(self, plain_box_dwg):
        assert plain_box_dwg.view_bounds("does_not_exist") is None

    def test_view_bounds_for_each_standard_view(self, plain_box_dwg):
        dwg = plain_box_dwg
        for v in ("front", "plan", "side", "iso"):
            b = dwg.view_bounds(v)
            assert b is not None, v
            x0, y0, x1, y1 = b
            assert x1 > x0 and y1 > y0, v

    def test_view_bounds_includes_hidden_lines(self, fresh_drawing):
        # Bounds union the visible and hidden silhouettes. Replace the front
        # view's hidden compound with one that extends past the visible box and
        # confirm the right edge moves out to it.
        dwg = fresh_drawing("box_60x40x20")
        vis, _ = dwg.views["front"]
        _, _, x1, _ = dwg.view_bounds("front")
        far = Compound(children=[Edge.make_line((x1 + 10, 0, 0), (x1 + 10, 5, 0))])
        dwg.views["front"] = (vis, far)
        assert dwg.view_bounds("front")[2] == pytest.approx(x1 + 10)
