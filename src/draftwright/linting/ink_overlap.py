"""Where two annotations put ink in the same place, rather than boxes.

`annotation_overlap` compares label boxes. That is a deliberate choice, and the
comment beside it gives the reason: full bounding boxes include witness lines,
which legitimately overlap for stacked dimensions, so comparing them cries wolf.

The cost of the choice is that anything drawn over a label which is not itself a
label goes unreported — a dimension line, an arrowhead, a leader. On GRM-03,
`annotation_overlap` fires zero times while ink a reader can see is shared
anyway (#1321).

On GRM-03 that gap is real but smaller than it first looked: the largest place
where ink lands on a label is 0.2755 mm² across 1.31 x 1.96 mm — an arrowhead
driven through the `5` of `0.5`. A 9.0 x 5.0 mm, 0.94 mm² collision reported for
this part elsewhere belongs to a different drawing of it (an AI-authored sheet in
the sibling application), not to the drawing this engine builds; it is recorded
here because it was briefly used to calibrate the floor below.

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

#: Enough shared ink in one place to matter. The floor `b123d-drafting-helpers`'
#: sibling application ships, kept rather than raised, because every candidate it
#: admits on this repository's fixtures was rendered and judged a real defect.
#:
#: An earlier revision of this module set 0.3 — "a 0.1 mm stroke crossing a label
#: for three millimetres" — on three measured cases. That figure does not survive
#: measurement:
#:
#: * At 0.3 the check reports **nothing** on any of the 23 STEP fixtures in
#:   ``tests/fixtures`` (11 ``*.step`` + 12 ``*.stp``, all built through
#:   ``build_drawing``). Its only positive anywhere was one programmatic fixture.
#: * One of the three anchors — "0.94 mm² for a dimension line through a label,
#:   GRM-03" — does not exist on the drawing this engine produces for GRM-03,
#:   whose largest on-label place is 0.2755 mm² across 1.31 x 1.96 mm. That
#:   measurement came from a sibling application's AI-authored sheet of the same
#:   part, which is a different drawing.
#: * The remaining "graze" anchor was the argument for the raise. Rendered at
#:   600 dpi it is an arrowhead sitting on the `x` of `50 x 120 x 5 DEEP` — the
#:   same defect the blatant cases show, smaller, not a different kind.
#:
#: Every place at or above this floor on the corpus, each one rendered and looked
#: at rather than inferred:
#:
#: =========  ==========================  ================================
#: area mm²   fixture / pair              what the render shows
#: =========  ==========================  ================================
#: 0.2755     grm03 `0.5` x `2`           arrowhead driven through the `5`
#: 0.2124     ctc-02 ap203 hole table     dimension line struck through a
#:                                        table row, arrowhead on the `1`
#: 0.1828     ctc-02 ap242 hole table     line through the row, arrowhead
#:                                        covering the `⌀`
#: 0.1312     issue_915 pocket callout    arrowhead on the `x` of the
#:                                        callout
#: 0.0944     ctc-02 ap203 hole table     arrowhead on the `⌀`, extension
#:                                        line down through `22` below
#: =========  ==========================  ================================
#:
#: So this is not a tuned number: it is the sibling's value, retained because
#: nothing between it and 1.45 mm² was found that a draughtsman would accept.
#: Raising it again needs a rendered case it wrongly reports, not an estimate.
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


def _lands_on_a_label(box: tuple[float, float, float, float], keep_clear) -> bool:
    """Whether a place lands on either annotation's label.

    Touching, not covering. Requiring the part inside the label to clear
    ``MIN_REGION_MM`` as well was tried and measured identically on every
    fixture here — a place that reaches a label at all is generally well inside
    it — so the stricter rule bought nothing and cost a second thing to reason
    about. What separates a graze from a collision is how much ink, not how the
    two rectangles meet.
    """
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
    and is ordinary, correct drafting. What is not ordinary is ink *covering* a
    label, which is the gap ``annotation_overlap`` leaves by comparing label
    boxes to each other and to nothing else.
    """
    places = places_where_ink_meets(item_a.intersect(item_b))
    if not places or sum(place.area for place in places) < MIN_INK_MM2:
        return None
    collisions = [
        place
        for place in places
        if place.is_a_collision() and (not keep_clear or _lands_on_a_label(place.box, keep_clear))
    ]
    if not collisions:
        return None
    return max(collisions, key=lambda place: place.area)
