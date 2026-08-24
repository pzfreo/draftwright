"""Where two annotations put ink in the same place, rather than boxes.

`annotation_overlap` compares label boxes. That is a deliberate choice, and the
comment beside it gives the reason: full bounding boxes include witness lines,
which legitimately overlap for stacked dimensions, so comparing them cries wolf.

The cost of the choice is that anything drawn over a label which is not itself a
label goes unreported — a dimension line, an arrowhead, a leader. On GRM-03,
`annotation_overlap` fires zero times while three pairs share ink a reader can
see (#1321).

Annotations are build123d sketches, so the ink itself is available. Measuring it
takes care, and both obvious approaches are wrong:

**Area alone reports ordinary drafting as a defect.** Strokes are filled faces
about 0.1 mm wide, so everything crosses everything. On that sheet the *largest*
shared area was two collinear extension lines — 1.1004 mm² in a region
0.100 x 11.000 mm — which is exactly the stacked-dimension case full boxes were
rejected for, reached from the other side. Seven such pairs.

**The union of everywhere a pair meets is not the region to test.** Two stacked
dimensions sharing left *and* right extension lines give two thin places forty
millimetres apart; unioned they are 40 x 11 mm and look like the worst collision
on the drawing.

So the shared ink is grouped into **places** by proximity, and a place counts
only if it has width *and* height. Collinear line-work is thin in one axis
however long it runs; a single crossing is thin in both; ink shared across a
region with both is something a reader sees.

Two cases from that sheet fix the shape of the rule, and any change here should
keep both:

- **excluded** — stacked dimensions sharing left and right extension lines: two
  places of 0.1 x 11 mm, forty apart. Baseline dimensioning.
- **reported** — a place made of faces none of which is itself square:
  0.10 x 0.10 and 0.10 x 0.60 two millimetres apart, one place of 2.10 x 0.60.

Per-face tests break the second. Union tests break the first.

What this cannot see, and does not try to: a line drawn *through* text shares
ink only as tall as the line, so no test on the shape of that region will call
it a collision however unreadable it looks. `leader_line_through_text` covers
that case directly; the two checks are complementary.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Below this the boolean found nothing — its own rounding, not an overlap.
MIN_INK_MM2 = 0.001

#: A place must reach this in *both* axes to be ink a reader can separate.
#: The figure is ``annotation_overlap``'s own ``ox > 0.5 and oy > 0.5``, applied
#: to ink instead of to label boxes.
MIN_REGION_MM = 0.5

#: Enough shared ink in one place to see it. A place can have width and height
#: and still hold almost nothing — a few strokes clipping a corner.
MIN_COLLISION_MM2 = 0.05

#: How close two pieces of shared ink have to be to count as one place. The
#: crossings of a line through text sit about a millimetre apart; two collinear
#: extension-line overlaps on stacked dimensions do not.
CLUSTER_GAP_MM = 2.0


@dataclass(frozen=True)
class Place:
    """One place two annotations share ink, and how much."""

    area: float
    box: tuple[float, float, float, float]

    @property
    def width(self) -> float:
        return self.box[2] - self.box[0]

    @property
    def height(self) -> float:
        return self.box[3] - self.box[1]

    def is_a_collision(self) -> bool:
        """Ink with width, height, and enough of it to see."""
        return (
            self.width >= MIN_REGION_MM
            and self.height >= MIN_REGION_MM
            and self.area >= MIN_COLLISION_MM2
        )


def _faces_of(result) -> list[tuple[float, tuple[float, float, float, float]]]:
    """Every face of an intersection, as ``(area, box)``.

    Two shapes of the build123d API matter here, and both are easy to get wrong:
    ``Shape.intersect`` returns a ``ShapeList``, not a shape — so the natural
    ``getattr(result, "area", 0.0)`` answers ``0.0`` for every pair and reports
    every sheet clean — and it returns ``None``, not an empty list, when the two
    are disjoint.
    """
    if result is None:
        return []
    shapes = [result] if hasattr(result, "faces") else list(result)
    faces = []
    for shape in shapes:
        if shape is None or not hasattr(shape, "faces"):
            continue
        for face in shape.faces():
            area = float(face.area)
            if area <= 0.0:
                continue
            bounds = face.bounding_box()
            faces.append((area, (bounds.min.X, bounds.min.Y, bounds.max.X, bounds.max.Y)))
    return faces


def _near(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    """Whether two boxes are close enough to be one place."""
    return (
        a[0] - CLUSTER_GAP_MM <= b[2]
        and b[0] - CLUSTER_GAP_MM <= a[2]
        and a[1] - CLUSTER_GAP_MM <= b[3]
        and b[1] - CLUSTER_GAP_MM <= a[3]
    )


def places_where_ink_meets(result) -> list[Place]:
    """Group an intersection into the places the two annotations actually meet.

    Single-linkage, merged to a fixpoint rather than in one pass: absorbing a
    group grows its box, and a group already passed over may be adjacent to the
    grown one — three faces in an L are the smallest case. Face counts are
    single digits, so running to a fixpoint costs nothing worth saving, and the
    input is sorted so the answer does not depend on the order faces arrive in.
    """
    groups = sorted(_faces_of(result), key=lambda face: face[1])
    changed = True
    while changed:
        changed = False
        settled: list[tuple[float, tuple[float, float, float, float]]] = []
        for area, box in groups:
            for index, (other_area, other_box) in enumerate(settled):
                if _near(box, other_box):
                    settled[index] = (
                        other_area + area,
                        (
                            min(box[0], other_box[0]),
                            min(box[1], other_box[1]),
                            max(box[2], other_box[2]),
                            max(box[3], other_box[3]),
                        ),
                    )
                    changed = True
                    break
            else:
                settled.append((area, box))
        groups = settled
    return [Place(area=area, box=box) for area, box in groups]


def _touches(box: tuple[float, float, float, float], keep_clear) -> bool:
    """Whether a place lands on either annotation's label."""
    for label in keep_clear:
        if label is None:
            continue
        if min(box[2], label[2]) > max(box[0], label[0]) and min(box[3], label[3]) > max(
            box[1], label[1]
        ):
            return True
    return False


def worst_shared_place(item_a, item_b, *, keep_clear=()) -> Place | None:
    """The worst place two annotations put ink over a label, if any.

    ``None`` when they share no ink, share it only where lines meet, or share it
    somewhere no label is. Callers should reject on bounding boxes first: the
    boolean costs far more than the comparison and almost every pair is
    disjoint.

    ``keep_clear`` is the two label extents. Shape alone is not enough: an
    arrowhead meeting another dimension's witness line shares a region with real
    width and height — measured at 13.9 x 4.0 mm on the #916 pocket fixture —
    and is ordinary, correct drafting. What is not ordinary is ink landing on a
    label, which is the gap ``annotation_overlap`` leaves by comparing label
    boxes to each other and to nothing else.
    """
    places = places_where_ink_meets(item_a.intersect(item_b))
    if not places or sum(place.area for place in places) < MIN_INK_MM2:
        return None
    collisions = [
        place
        for place in places
        if place.is_a_collision() and (not keep_clear or _touches(place.box, keep_clear))
    ]
    if not collisions:
        return None
    return max(collisions, key=lambda place: place.area)
