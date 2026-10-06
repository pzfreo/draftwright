"""Regression coverage for #367's rendered leader-ink collision model."""

import math

import pytest
from build123d import Box, Cylinder, Face, HeadType, Vector
from build123d_drafting.helpers import ArrowHead, Draft, Leader

from draftwright import Sheet
from draftwright._geometry import (
    _convex_polygon_overlaps_box,
    _leader_ink_crosses_box,
    _segment_crosses_box,
)
from draftwright.annotations._leader_fixed_ink import _validated_face_mesh
from draftwright.annotations.holes import _leader_hits
from draftwright.fixed_ink_cache import FixedInkMeshCache, _using_fixed_ink_mesh_cache


def _leader(draft, elbow=(20.0, 0.0)):
    tip = (0.0, 0.0)
    side = "right" if elbow[0] > 0 else "left"
    leader = Leader(tip=tip, elbow=elbow, label="X", draft=draft, text_side=side)
    return leader, tip, elbow, side


def test_exact_face_cache_reuses_identical_ink_but_not_moved_ink(monkeypatch):
    draft = Draft()
    first = tuple(_leader(draft)[0].faces())[0]
    identical = tuple(_leader(draft)[0].faces())[0]
    moved = tuple(_leader(draft, elbow=(21.0, 0.0))[0].faces())[0]
    calls = 0
    tessellate = Face.tessellate

    def counted(face, tolerance):
        nonlocal calls
        calls += 1
        return tessellate(face, tolerance)

    monkeypatch.setattr(Face, "tessellate", counted)
    cache = FixedInkMeshCache(max_entries=2)
    with _using_fixed_ink_mesh_cache(cache):
        original = _validated_face_mesh(first, 0.01)
        assert original is not None
        assert _validated_face_mesh(identical, 0.01) == original
        assert _validated_face_mesh(moved, 0.01) is not None
        assert _validated_face_mesh(identical, 0.02) is not None
    assert calls == 3
    assert cache.hits == 1
    assert cache.misses == 3
    cache.clear()
    assert cache.hits == cache.misses == 0


def test_face_cache_key_failure_falls_back_to_uncached_validation():
    class UnserializableFace:
        def tessellate(self, _tolerance):
            return (
                [Vector(0, 0), Vector(1, 0), Vector(0, 1)],
                [(0, 1, 2)],
            )

        def edges(self):
            return ()

    cache = FixedInkMeshCache()
    with _using_fixed_ink_mesh_cache(cache):
        assert _validated_face_mesh(UnserializableFace(), 0.01) is not None
    assert cache.hits == cache.misses == 0


def test_face_cache_write_failure_keeps_validated_mesh(monkeypatch):
    face = tuple(_leader(Draft())[0].faces())[0]
    cache = FixedInkMeshCache()

    def unavailable(_key, _mesh):
        raise RuntimeError("cache unavailable")

    monkeypatch.setattr(cache, "put", unavailable)
    with _using_fixed_ink_mesh_cache(cache):
        assert _validated_face_mesh(face, 0.01) is not None


def test_sheet_replay_reuses_exact_ink_without_changing_drawing() -> None:
    part = Box(20, 40, 50) - Cylinder(2, 20, rotation=(0, 90, 0))
    sheet = Sheet(part, scale=2).authored_dimensions()
    bore = sheet.hole(diameter=4, depth=20, at=(0, 0, 0), axis="x")
    sheet.dimension(bore, "bore.diameter")
    with pytest.raises(TypeError, match="FixedInkMeshCache"):
        sheet.build(mesh_cache=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="positive integer"):
        FixedInkMeshCache(max_entries=0)
    cache = FixedInkMeshCache()

    first = sheet.build(mesh_cache=cache)
    first_hits, first_misses = cache.hits, cache.misses
    second = sheet.build(mesh_cache=cache)

    assert first_misses > 0
    assert cache.hits > first_hits
    assert cache.misses == first_misses
    assert first.annotations().keys() == second.annotations().keys()
    assert first.lint_summary() == second.lint_summary()


@pytest.mark.parametrize("head_type", tuple(HeadType))
@pytest.mark.parametrize("angle", (0.0, 35.0, -35.0, 145.0))
def test_arrowhead_flare_is_collision_evidence_beyond_the_idealised_segment(angle, head_type):
    draft = Draft(head_type=head_type)
    radians = math.radians(angle)
    unit = (math.cos(radians), math.sin(radians))
    normal = (-unit[1], unit[0])
    elbow = (20.0 * unit[0], 20.0 * unit[1])
    tip = (0.0, 0.0)
    sample = (
        unit[0] * draft.arrow_length * 0.83 + normal[0] * draft.arrow_length * 0.23,
        unit[1] * draft.arrow_length * 0.83 + normal[1] * draft.arrow_length * 0.23,
    )
    obstacle = (sample[0] - 0.05, sample[1] - 0.05, sample[0] + 0.05, sample[1] + 0.05)

    assert not _segment_crosses_box(tip, elbow, obstacle), (
        "fixture must miss the old zero-width centreline test"
    )
    arrow = ArrowHead(
        size=draft.arrow_length,
        head_type=head_type,
        rotation=angle + 180.0,
    )
    assert any(face.is_inside(Vector(sample[0], sample[1], 0.0)) for face in arrow.faces()), (
        f"fixture must intersect the real rendered {head_type.name.lower()} arrowhead"
    )
    assert _leader_ink_crosses_box(
        tip,
        elbow,
        obstacle,
        arrow_length=draft.arrow_length,
        line_width=draft.line_width,
    )


def test_annotation_side_leader_check_uses_the_rendered_arrow_ink():
    draft = Draft()
    leader, tip, elbow, side = _leader(draft)
    obstacle = (2.44, 0.64, 2.54, 0.74)

    assert not _segment_crosses_box(tip, elbow, obstacle)
    assert _leader_hits(leader, tip, elbow, side, (obstacle,), draft)


def test_arrowhead_clearance_is_local_to_the_tip_not_the_whole_shaft():
    draft = Draft()
    leader, tip, elbow, side = _leader(draft)
    obstacle = (9.9, 0.6, 10.1, 0.8)

    assert not any(face.is_inside(Vector(10.0, 0.7, 0.0)) for face in leader.faces())
    assert not _leader_hits(leader, tip, elbow, side, (obstacle,), draft)


def test_heavy_shaft_width_is_collision_evidence_beyond_the_centreline():
    draft = Draft(line_width=4.0, arrow_length=1.0)
    leader, tip, elbow, side = _leader(draft)
    obstacle = (9.9, 1.4, 10.1, 1.6)

    assert not _segment_crosses_box(tip, elbow, obstacle), (
        "fixture must miss the old zero-width centreline test"
    )
    assert any(face.is_inside(Vector(10.0, 1.5, 0.0)) for face in leader.faces()), (
        "fixture must intersect the real rendered shaft"
    )
    assert _leader_hits(leader, tip, elbow, side, (obstacle,), draft)


def test_zero_length_leader_uses_its_local_ink_extent():
    assert _leader_ink_crosses_box(
        (5.0, 5.0),
        (5.0, 5.0),
        (5.5, 4.9, 5.6, 5.1),
        arrow_length=3.0,
        line_width=0.5,
    )
    assert not _leader_ink_crosses_box(
        (5.0, 5.0),
        (5.0, 5.0),
        (6.1, 4.9, 6.2, 5.1),
        arrow_length=3.0,
        line_width=0.5,
    )


def test_arrow_and_shaft_components_can_be_disabled_independently():
    # Keep the two footprint branches independently load-bearing.  A zero style value is
    # a supported boundary for the pure geometry helper even though ordinary Draft values
    # render both components.
    assert _leader_ink_crosses_box(
        (0.0, 0.0),
        (20.0, 0.0),
        (2.4, 0.6, 2.6, 0.8),
        arrow_length=3.0,
        line_width=0.0,
    )
    assert _leader_ink_crosses_box(
        (0.0, 0.0),
        (20.0, 0.0),
        (9.9, 0.4, 10.1, 0.6),
        arrow_length=0.0,
        line_width=2.0,
    )
    assert not _leader_ink_crosses_box(
        (0.0, 0.0),
        (20.0, 0.0),
        (9.9, -0.1, 10.1, 0.1),
        arrow_length=0.0,
        line_width=0.0,
    )


def test_convex_overlap_ignores_a_repeated_polygon_vertex():
    polygon = ((0.0, 0.0), (2.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0))
    assert _convex_polygon_overlaps_box(polygon, (1.0, 1.0, 3.0, 3.0))
