"""The grid-lattice convention shared by recognition and declaration (#969).

`recognition._features` and `model.declare` are two halves of one convention: which world
directions span a pattern's plane, which lattice direction is a "column", and what `angle`
names. Disagree and every declared grid array comes back transposed — the #969 defect, invisible
on the hole path (which emits explicit ``members=``) and exposed by pocket/slot arrays, which
recompute their members from ``grid=/rows=/cols=/angle=``.

These tests exercise the seam DIRECTLY — declare a lattice, project and detect it exactly as the
pattern recognisers do, redeclare the detection — with no solid build, so the whole cross-product
of planes, pitch orders, unequal counts and rotations is affordable. That cross-product is the
point. Two branches of it were unexecuted by every fixture in the corpus:

* the angle was reduced modulo 90°, which round-trips only when the shortest lattice basis is
  the column direction (so `row_pitch < col_pitch` failed); and
* recognition projected onto its own cross-product basis, a quarter turn round from the one
  declaration lays out along, so the two errors cancelled on a z-plane grid and on nothing else.

Projecting through `_plane_uv` here rather than just taking ``(x, y)`` is what makes the second
one visible: a test that assumes the basis cannot detect that the basis is wrong.

The basis-level tests at the bottom are the third round's lesson: sharing one basis is only
right if the shared basis is still perpendicular to the axis it was asked for, which a
near-axis shortcut quietly stopped being.
"""

import math

import pytest

from draftwright._geometry import plane_axes
from draftwright.model.declare import _pattern_members
from draftwright.recognition._features import _plane_uv, _rect_grid

_AXIS_UNIT = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}


def _dot(a, b):
    return sum(p * q for p, q in zip(a, b, strict=True))


class _Member:
    """The only thing `_rect_grid` asks of a member is where it is."""

    def __init__(self, location):
        self.location = location


def _declare(axis, rows, cols, row_pitch, col_pitch, angle, center=None):
    return _pattern_members(
        "grid",
        center or (0.0, 0.0, 0.0),
        axis,
        rows * cols,
        bcd=None,
        pitch=None,
        direction=None,
        grid=(row_pitch, col_pitch),
        rows=rows,
        cols=cols,
        angle=angle,
    )


def _detect(axis, points):
    """`(rows, cols, row_pitch, col_pitch, angle, center)`, or None if it is not a grid.

    Projects onto the plane basis the way `recognise_hole_patterns` /
    `recognise_pocket_patterns` / `recognise_slot_patterns` all do, so the basis itself is
    under test and not assumed.
    """
    u, v = _plane_uv(_AXIS_UNIT[axis])
    pts = [
        (
            sum(a * b for a, b in zip(p, u, strict=True)),
            sum(a * b for a, b in zip(p, v, strict=True)),
        )
        for p in points
    ]
    return _rect_grid(
        [_Member(p) for p in points],
        pts,
        lambda _members, rows, cols, row_pitch, col_pitch, angle, center: (
            rows,
            cols,
            row_pitch,
            col_pitch,
            angle,
            center,
        ),
    )


def _lattice(points):
    return sorted(tuple(round(c, 3) for c in p) for p in points)


#: Lattices in BOTH pitch orders, because the detector's first basis is the SHORTEST pairwise
#: vector — so `row_pitch < col_pitch` and `row_pitch > col_pitch` take different branches.
#: Counts are unequal so a rows↔cols transpose cannot hide behind a square array.
_LATTICES = [
    (2, 5, 10.0, 45.0),  # shortest basis is a ROW step — the branch #970 r1 got wrong
    (5, 2, 45.0, 10.0),  # ...and its mirror, where the shortest basis is a COLUMN step
    (3, 4, 17.0, 31.0),
    (4, 3, 31.0, 17.0),
    (2, 3, 45.0, 40.0),  # the shape of the `pocket grid` / `slot grid` emit fixtures
    (3, 3, 25.0, 25.0),  # square: the two bases tie in length, so the sort order is arbitrary
]

#: Unrotated plus three rotations that are not multiples of 45°, so a lost quarter-turn cannot
#: land back on the original lattice by symmetry.
_ANGLES = [0.0, 25.0, 37.0, 73.0]


@pytest.mark.parametrize("axis", ["x", "y", "z"])
@pytest.mark.parametrize("angle", _ANGLES)
@pytest.mark.parametrize(("rows", "cols", "row_pitch", "col_pitch"), _LATTICES)
def test_a_detected_grid_redeclares_to_the_same_point_set(
    axis, rows, cols, row_pitch, col_pitch, angle
):
    original = _declare(axis, rows, cols, row_pitch, col_pitch, angle)
    detected = _detect(axis, original)
    assert detected is not None, "the declared lattice was not recognised as a grid at all"
    rebuilt = _declare(axis, *detected[:5], center=detected[5])
    assert _lattice(rebuilt) == _lattice(original), (
        "redeclaring the detection moved the members: recognition and declaration disagree "
        "about the lattice convention"
    )


@pytest.mark.parametrize("axis", ["x", "y", "z"])
@pytest.mark.parametrize("angle", _ANGLES)
@pytest.mark.parametrize(("rows", "cols", "row_pitch", "col_pitch"), _LATTICES)
def test_each_count_keeps_its_own_pitch(axis, rows, cols, row_pitch, col_pitch, angle):
    # The detector is free to call the original rows "columns" — the convention is local to the
    # lattice (first basis = columns), not tied to world axes, so a rotated grid legitimately
    # comes back relabelled. What it may NOT do is separate a count from its pitch: 5 members
    # spaced 45 mm must stay 5-and-45, whichever name they end up under. A point-set check alone
    # would pass on a self-cancelling mislabel; this fails on it.
    d_rows, d_cols, d_rp, d_cp, _angle, _center = _detect(
        axis, _declare(axis, rows, cols, row_pitch, col_pitch, angle)
    )
    assert {(d_rows, d_rp), (d_cols, d_cp)} == {(rows, row_pitch), (cols, col_pitch)}


@pytest.mark.parametrize("axis", ["x", "y", "z"])
def test_recognition_and_declaration_span_the_plane_the_same_way(axis):
    # The basis itself, stated once, and by exact equality: `_plane_uv` takes a vector and
    # `declare._plane_axes` a letter, and both must land on the shared pair, or `angle` is
    # measured in one frame and applied in another — which is how a quarter-turn error hid
    # behind a mod-90 fold for so long. Exact rather than approximate because the
    # orthogonalisation inside `_plane_uv` has to be a genuine no-op on a principal axis.
    from draftwright.model.declare import _plane_axes

    assert _plane_uv(_AXIS_UNIT[axis]) == plane_axes(axis)
    assert _plane_axes(axis) == plane_axes(axis)


#: Axes that are awkward for the shared-basis seed: several a hair off principal, some properly
#: oblique, one antiparallel. The near-axis cases are the point — a first cut treated anything
#: within ~2.6° of an axis as axis-aligned and returned the exact principal basis, which is not
#: perpendicular to the normal it was given, so it silently foreshortened every projected pitch
#: (Codex review of #970, round 2).
_TRICKY_AXES = [
    (0.04, 0.0, 1.0),  # 2.3° off +Z — inside the cutoff the first cut used
    (0.0, 1.0, 0.001),  # 0.06° off +Y
    (1.0, -0.005, 0.002),  # a hair off +X, and off in BOTH other components
    (0.0, 0.6, 0.8),  # properly oblique
    (0.6, -0.48, 0.64),  # oblique with no zero component
    (0.0, 0.0, -1.0),  # exactly antiparallel to +Z
]


@pytest.mark.parametrize("axis", _TRICKY_AXES)
def test_every_axis_gets_an_orthonormal_basis_of_its_own_plane(axis):
    # NOT a claim that an oblique pattern round-trips — it does not, and cannot: the IR records
    # a pattern's axis as a LETTER, so declaration flattens an oblique lattice into its dominant
    # plane whatever frame it was found in (#971, pre-dating this branch). The claim here is
    # narrower and is the one the near-axis regression broke: whatever axis it is given, the
    # PROJECTION is geometrically honest, so pitches are not foreshortened and the lattice is
    # recognised at its true shape.
    u, v = _plane_uv(axis)
    n = math.hypot(*axis)
    unit = tuple(c / n for c in axis)
    for w in (u, v):
        assert math.isclose(math.hypot(*w), 1.0, abs_tol=1e-12), "not a unit vector"
        assert math.isclose(_dot(w, unit), 0.0, abs_tol=1e-12), (
            "the basis is not perpendicular to the axis it was asked for, so every pitch "
            "projected onto it is foreshortened"
        )
    assert math.isclose(_dot(u, v), 0.0, abs_tol=1e-12), "the two basis vectors are not orthogonal"


@pytest.mark.parametrize(
    "axis",
    [
        *_TRICKY_AXES,
        (1.0, 1.0 - 1e-9, 0.0),  # either side of a dominant-component tie, where the frame
        (1.0 - 1e-9, 1.0, 0.0),  # ...turns a quarter turn between one call and the next
    ],
)
def test_the_frame_and_the_ir_axis_letter_agree(axis):
    # The frame is seeded from the dominant component, so it DOES jump across a tie. That is
    # only a defect if it can disagree with the axis letter `detect` stamps on the feature —
    # then the angle would be measured in one plane and applied in another, which is #969 all
    # over again. It cannot: both are chosen by the same dominant-component rule. Pinned by
    # calling `_axis_letter` — the `_geometry` helper `detect._pattern_feature` itself uses —
    # rather than by restating the rule, so a change to it breaks this. That is white-box: it
    # locks the shared convention, not `detect`'s call site, which stays covered by the
    # round-trip tests above (Codex review of #970, rounds 3 and 4).
    from draftwright._geometry import _axis_letter

    letter = _axis_letter(type("F", (), {"axis": axis})())
    u, v = _plane_uv(axis)
    cu, cv = plane_axes(letter)
    # Aligned with the letter's canonical basis rather than equal to it: the orthogonalisation
    # tilts the frame by however far the axis is off principal. A quarter-turn disagreement —
    # the failure this guards — would read ~0 here. The floor is 1/√2, approached only AT a
    # tie, so 0.7 is the tightest bound that holds for every axis rather than a round number.
    assert _dot(u, cu) >= 0.7, "the frame is a quarter turn from the basis the IR letter names"
    assert _dot(v, cv) >= 0.7, "the frame is a quarter turn from the basis the IR letter names"


@pytest.mark.parametrize("axis", ["x", "y", "z"])
def test_a_reversed_axis_keeps_the_same_frame(axis):
    # The IR records a pattern's axis as a LETTER, so declaration cannot tell +Z from -Z. A
    # recognition frame that flipped with the sign would mirror the angle back through the
    # waist. Pinned because the natural right-handed construction does exactly that.
    forward = _AXIS_UNIT[axis]
    assert _plane_uv(forward) == _plane_uv(tuple(-c for c in forward))


def _oblique_and_principal_lattices():
    """The same 2×3 lattice laid in an oblique plane and in the Z plane."""
    from draftwright.recognition._features import HoleRecord

    axis = (0.6, -0.48, 0.64)
    n = math.hypot(*axis)
    a = tuple(c / n for c in axis)
    u, v = _plane_uv(a)
    cells = [(c * 31.0, r * 17.0) for r in range(2) for c in range(3)]
    oblique = [tuple(c1 * u[k] + c2 * v[k] for k in range(3)) for c1, c2 in cells]
    flat = [(c1, c2, 0.0) for c1, c2 in cells]

    def holes(locs, ax):
        return [
            HoleRecord(axis=ax, location=loc, diameter=6.0, depth=10.0, bottom="through")
            for loc in locs
        ]

    return holes(oblique, a), holes(flat, (0.0, 0.0, 1.0))


def test_an_oblique_lattice_does_not_become_a_pattern_feature():
    """The IR records a pattern's axis as a LETTER, so an oblique plane has no faithful
    `PatternFeature` — building one anyway produces a silently wrong drawing (#971).

    Measured before the guard: a lattice spanning 39.96 mm in Z was recognised correctly and
    redeclared to a Z spread of 0.00, flat in the letter's canonical plane.

    Checked through `build_part_model`, not against the recogniser. `recognise_hole_patterns`
    still FINDS this lattice — ADR 0013 says a recogniser reports the geometry it finds, and the
    limitation is draftwright's IR, so the refusal lives at the adapter. Asserting the
    recogniser returned nothing would codify the layering violation the first cut had
    (#983 review).
    """
    from build123d import Box

    from draftwright.model.detect import build_part_model
    from draftwright.recognition._features import recognise_hole_patterns

    oblique, principal = _oblique_and_principal_lattices()
    block = Box(200, 200, 200)

    zs = [h.location[2] for h in oblique]
    assert max(zs) - min(zs) > 1.0, "the fixture is not actually out of plane"
    assert recognise_hole_patterns(oblique), (
        "recognition should still FIND the oblique lattice — the refusal belongs at the IR "
        "adapter, and a recogniser that stopped reporting it would be weaker for every consumer"
    )

    model = build_part_model(block, holes=oblique)
    assert "pattern" not in [f.kind for f in model.features], (
        "an oblique lattice became a PatternFeature, and `Frame.axis` cannot express its "
        "plane — declaring it flattens the lattice into the dominant axis's plane"
    )
    # ...and nothing is LOST. Identical unpatterned holes regroup into one count-bearing
    # HoleFeature, so count MEMBERS rather than features.
    holes = [f for f in model.features if f.kind == "hole"]
    survived = sum(len(f.members) for f in holes)
    assert survived == len(oblique), (
        f"{survived} of {len(oblique)} members survived as holes — refusing the pattern must "
        "not drop the holes themselves"
    )

    # The guard is not simply refusing everything.
    principal_model = build_part_model(block, holes=principal)
    assert "pattern" in [f.kind for f in principal_model.features], (
        "the same lattice in a principal plane stopped becoming a pattern — the guard is too wide"
    )


@pytest.mark.parametrize(
    ("axis", "principal", "why"),
    [
        ((0.0, 0.0, 1.0), True, "exactly Z"),
        ((0.0, 0.0, -1.0), True, "exactly -Z"),
        ((1e-7, 0.0, 1.0), True, "noise below the HoleSpec snap"),
        ((1.4e-3, 0.0, 1.0), False, "0.08° off — a cosine 1e-6 test WRONGLY accepts this"),
        ((0.02, 0.0, 1.0), False, "1.1° off"),
        ((0.6, -0.48, 0.64), False, "properly oblique"),
    ],
)
def test_the_principal_axis_predicate_is_exact_not_a_cosine_tolerance(axis, principal, why):
    """The boundary, because the first cut's tolerance was three orders looser than it read.

    It tested `1 - |component| <= 1e-6`, which is a COSINE tolerance: it looks like 1e-6 and
    admits ~0.081°, an off-axis component near 1.4e-3 — about 1.4 mm of flattening over a metre
    (#983 review). The far-oblique fixture above cannot catch that, since both predicates reject
    it; only a NEAR-principal case can, which is why this exists.

    Exact after the snap `HoleSpec.from_hole` already applies, so analytic STEP noise is
    absorbed and nothing else is.
    """
    from draftwright.model.detect import _is_principal_axis

    assert _is_principal_axis(axis) is principal, why
