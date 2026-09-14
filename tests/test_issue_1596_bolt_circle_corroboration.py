"""`EQ SP ON ø… BC` must state a datum, not a fitted circle (#1596).

#1595 drew a real printed part whose six ⌀2.4 holes sit in a 2×3 rectangular grid. Four of
them came back as `4× ⌀2.4 THRU EQ SP ON ø34.4 BC`, the remaining two as a separate
`2× ⌀2.4 THRU`. The circle is centred at (0, −20.11) — mid-air, concentric with nothing.

The fit is not a near miss that a tolerance could catch. **Any three non-collinear points are
concyclic, and so are the four corners of any rectangle** — for those arrangements the residual
is zero by construction, so it cannot disconfirm anything. Corroboration has to come from
somewhere other than the fit.

What actually gives it away here is lying in plain sight next to the bad callout: the leftover
`2× ⌀2.4 THRU`. Six identical holes, and the circle claims four. A drawing does not put two of
six bolts on a different plan.
"""

import math
from dataclasses import replace

import pytest
from build123d import Box, BuildPart, Cylinder, Hole, Locations, PolarLocations

from draftwright import build_drawing
from draftwright.builder import detect_part_model
from draftwright.model.ir import PatternFeature

# The #1595 arrangement, reduced to the smallest part that reproduces it. Row spacing is
# UNEVEN, which is what stops the grid recogniser claiming all six and leaves the four
# concyclic ones to the bolt-circle fitter.
_UNEVEN_ROWS = (-32.30, -7.92, 32.30)
_EVEN_ROWS = (-24.0, 0.0, 24.0)
_COLUMNS = (-12.15, 12.15)


def _grid_plate(rows, columns=_COLUMNS, thickness=6.0):
    with BuildPart() as part:
        Box(60, 90, thickness)
        with Locations(*[(x, y, 0) for x in columns for y in rows]):
            Hole(1.2, depth=thickness)
    return part.part


def _flange(holes, *, central_feature=True):
    """A round flange: *holes* on a ⌀60 circle, optionally with a boss and bore at the centre."""
    with BuildPart() as part:
        Cylinder(40, 8)
        if central_feature:
            Cylinder(12, 16)
            Hole(6, depth=16)
        with PolarLocations(30, holes):
            Hole(3, depth=8)
    return part.part


def _patterns(part):
    return [f for f in detect_part_model(part).features if isinstance(f, PatternFeature)]


def _bolt_circles(part):
    return [f for f in _patterns(part) if f.pattern == "bolt_circle"]


# --- the defect ------------------------------------------------------------------------


def test_precondition_the_recogniser_really_does_propose_this_circle():
    """Without this the tests below pass against unfixed code: the whole question is what
    draftwright does with a `BoltCircle` the recogniser legitimately found."""
    from quiddity import analyse_cylinders, recognise_hole_patterns, recognise_holes

    part = _grid_plate(_UNEVEN_ROWS)
    holes = recognise_holes(part, cyls=analyse_cylinders(part))
    (found,) = recognise_hole_patterns(holes)
    assert type(found).__name__ == "BoltCircle"
    assert len(found.holes) == 4
    assert found.diameter == pytest.approx(34.42, abs=0.01)


def test_the_fitted_circle_is_exactly_the_rectangles_circumcircle():
    """Why the fit is not evidence, as arithmetic rather than assertion.

    Four corners at x = ±12.15, y = −32.30 and −7.92 have one circumcircle, and it is the
    one the engine reports. A fit that cannot fail cannot corroborate anything.
    """
    from quiddity import analyse_cylinders, recognise_hole_patterns, recognise_holes

    part = _grid_plate(_UNEVEN_ROWS)
    (found,) = recognise_hole_patterns(recognise_holes(part, cyls=analyse_cylinders(part)))

    centre_y = (_UNEVEN_ROWS[0] + _UNEVEN_ROWS[1]) / 2
    diameter = 2 * math.hypot(_COLUMNS[1], (_UNEVEN_ROWS[1] - _UNEVEN_ROWS[0]) / 2)
    assert found.center[1] == pytest.approx(centre_y, abs=0.01)
    assert found.diameter == pytest.approx(diameter, abs=0.01)


def test_an_uncorroborated_circle_is_not_carried_into_the_ir():
    assert _bolt_circles(_grid_plate(_UNEVEN_ROWS)) == []


def test_the_members_are_still_drawn_counted_and_located(refused_pattern_drawing):
    """Refusing the datum must not lose the holes.

    They fall through to the un-patterned grouping, which also repairs the second half of
    #1595's A2: the six stop being split into an unrelated 4 and 2.
    """
    drawing = refused_pattern_drawing
    labels = [str(o.label) for _n, o in drawing.iter_annotations() if getattr(o, "label", None)]
    assert not [label for label in labels if "BC" in label]
    assert f"{len(drawing.recognition().holes)}× ⌀2.4 THRU" in labels

    from draftwright.sheet_emit import emit_sheet_script

    source = emit_sheet_script(
        drawing.model(), "part = supplied_part", "drawing", title="T", number="N"
    )
    count = len(drawing.recognition().holes)
    part = _grid_plate(_UNEVEN_ROWS) if count == 6 else _grid_plate((-10, 10), columns=(-10, 10))
    namespace = {"supplied_part": part}
    exec(
        compile(source[: source.index("drawing = sheet.build()")], "<refused-pattern>", "exec"),
        namespace,
    )  # noqa: S102
    replay = namespace["sheet"].model()
    assert not [feature for feature in replay.features if isinstance(feature, PatternFeature)]
    assert [feature for feature in replay.features if feature.kind == "hole"] == [
        feature for feature in drawing.model().features if feature.kind == "hole"
    ]
    replay_drawing = namespace["sheet"].build()
    assert not [
        issue for issue in replay_drawing.lint() if issue.code.startswith("hole_requirement")
    ]
    assert replay_drawing.recognition_ownership() is None


def test_even_spacing_was_never_this_defect():
    """The contrast that explains it: with even rows all six ARE a grid, and the bolt-circle
    fitter never sees four holes on their own. The defect needs the uneven case."""
    (grid,) = _patterns(_grid_plate(_EVEN_ROWS))
    assert grid.pattern == "grid"
    assert grid.count == 6


def test_no_leftover_holes_do_not_corroborate_a_fitted_circle():
    assert _bolt_circles(_grid_plate(_UNEVEN_ROWS)) == []
    assert _bolt_circles(_grid_plate(_UNEVEN_ROWS[:2])) == []


@pytest.mark.parametrize("rotation", [(0, 0, 0), (0, 0, 23), (0, 0, 45), (90, 0, 0), (0, 90, 0)])
def test_the_rule_does_not_depend_on_the_angle_to_the_axes(rotation):
    """A circular body corroborates; a bare square plate does not, in every principal plane."""
    from build123d import Rotation
    from quiddity import BoltCircle
    from quiddity.evidence import build_recognition_evidence

    from draftwright.model.detect import _build_part_model_from_recognition

    transform = Rotation(*rotation)
    for part, accepted in (
        (_flange(4, central_feature=False), True),
        (_grid_plate((-10, 10), columns=(-10, 10)), False),
    ):
        part = transform * part
        evidence = build_recognition_evidence(part)
        assert len(evidence.result.hole_patterns) == 1
        assert isinstance(evidence.result.hole_patterns[0], BoltCircle)
        assert len(evidence.result.hole_patterns[0].holes) == 4
        model = _build_part_model_from_recognition(part, evidence.result)
        patterns = [f for f in model.features if isinstance(f, PatternFeature)]
        assert bool(patterns) is accepted
        assert sum(f.count for f in model.features if f.kind in ("hole", "pattern")) == 4


# --- what must keep working ------------------------------------------------------------


def test_four_holes_around_a_real_centre_keep_their_bolt_circle():
    """A four-bolt round flange is real and common, so member count alone would refuse as
    much good work as bad. What separates it from the grid is a physical feature at the
    centre that a machinist can indicate off."""
    (pattern,) = _bolt_circles(_flange(4))
    assert pattern.count == 4
    assert pattern.bcd == pytest.approx(60.0)


def test_a_concentric_feature_alone_can_carry_it():
    """The physical-witness branch with the member-count route closed.

    #1595's plate, plus a boss concentric with the fitted circle. Four members cannot use
    the five-member route, regardless of the two identical holes left off the circle.
    Only the boss is left — and a centre a machinist can indicate off is exactly what makes
    the circle real. Without the boss this same part is refused (above).
    """
    with BuildPart() as part:
        Box(60, 90, 6)
        with Locations(*[(x, y, 0) for x in _COLUMNS for y in _UNEVEN_ROWS]):
            Hole(1.2, depth=6)
        with Locations((0.0, -20.11, 3.0)):
            Cylinder(6, 6)

    (pattern,) = _bolt_circles(part.part)
    assert pattern.count == 4


def test_a_round_body_concentric_with_the_holes_is_itself_corroboration():
    """A plain disc with four bolt holes and nothing in the middle keeps its bolt circle.

    Its ⌀80 body is a boss on the pattern axis at the pattern centre. Written expecting a
    refusal; the code was right and the expectation was wrong, and it is kept because "the
    part itself is the concentric feature" is the commonest flange there is.
    """
    (pattern,) = _bolt_circles(_flange(4, central_feature=False))
    assert pattern.count == 4


def test_four_polar_holes_on_a_square_plate_do_not_establish_a_circular_datum():
    with BuildPart() as part:
        Box(90, 90, 8)
        with PolarLocations(30, 4):
            Hole(3, depth=8)

    assert _bolt_circles(part.part) == []


def test_five_holes_are_self_evident():
    """Five concyclic holes could have failed to fit and did not, so the fit IS evidence —
    no corroborating feature needed."""
    (pattern,) = _bolt_circles(_flange(5, central_feature=False))
    assert pattern.count == 5
    assert pattern.bcd == pytest.approx(60.0)


class TestTheConcentricityPredicate:
    """`_corroborates_bolt_circle` directly, because two of its three conditions cannot be
    reached by building a part.

    A cross-axis boss placed so its in-plane projection lands on the pattern centre is
    awkward to construct in build123d and would prove nothing about real geometry anyway —
    the recogniser records such a boss at one end, far from the centre in projection. The
    predicate is pure, so it is exercised as one.
    """

    @staticmethod
    def _candidate(axis, location):
        from types import SimpleNamespace

        return SimpleNamespace(axis=axis, location=location)

    def test_a_feature_on_the_pattern_axis_at_the_centre_corroborates(self):
        from draftwright.recognition_ownership import _corroborates_bolt_circle

        assert _corroborates_bolt_circle(
            self._candidate((0.0, 0.0, 1.0), (0.0, 0.0, 25.0)), "z", (0.0, 0.0, 4.0)
        )

    def test_the_axis_coordinate_is_ignored(self):
        """A concentric boss is concentric whether it sits above or below the holes."""
        from draftwright.recognition_ownership import _corroborates_bolt_circle

        assert _corroborates_bolt_circle(
            self._candidate((0.0, 0.0, -1.0), (0.0, 0.0, -900.0)), "z", (0.0, 0.0, 4.0)
        )

    def test_a_feature_on_a_DIFFERENT_axis_does_not(self):
        """The check that no fixture reaches: a boss pointing along X says nothing about a
        bolt circle drilled along Z, however its centre happens to project."""
        from draftwright.recognition_ownership import _corroborates_bolt_circle

        assert not _corroborates_bolt_circle(
            self._candidate((1.0, 0.0, 0.0), (0.0, 0.0, 4.0)), "z", (0.0, 0.0, 4.0)
        )

    def test_a_feature_merely_nearby_does_not(self):
        """The claim is that a machinist may work from this circle. 2 mm out is not that."""
        from draftwright.recognition_ownership import _corroborates_bolt_circle

        assert not _corroborates_bolt_circle(
            self._candidate((0.0, 0.0, 1.0), (2.0, 0.0, 4.0)), "z", (0.0, 0.0, 4.0)
        )


@pytest.fixture(scope="module", params=["uneven-six", "square-four"])
def refused_pattern_drawing(request):
    part = (
        _grid_plate(_UNEVEN_ROWS)
        if request.param == "uneven-six"
        else _grid_plate((-10, 10), columns=(-10, 10))
    )
    return build_drawing(part, title="T", number="N")


def test_a_refused_pattern_does_not_leave_the_hole_ledger_believing_in_it(refused_pattern_drawing):
    """Refused pattern members remain ordinary, independently audited physical holes."""
    drawing = refused_pattern_drawing

    assert _bolt_circles(_grid_plate(_UNEVEN_ROWS)) == [], "precondition: the pattern is refused"
    unverifiable = [
        issue.code for issue in drawing.lint() if issue.code.startswith("hole_requirement")
    ]
    assert unverifiable == [], (
        f"the ledger reported holes the drawing actually states: {unverifiable}"
    )
    labels = [str(o.label) for _n, o in drawing.iter_annotations() if getattr(o, "label", None)]
    assert f"{len(drawing.recognition().holes)}× ⌀2.4 THRU" in labels


def test_refusal_retains_exact_physical_members_and_evaluation_credit(refused_pattern_drawing):
    from draftwright.evaluation.step_analysis import (
        _drawing_consumer_outcomes,
        _hole_model_outcomes,
    )
    from draftwright.linting.hole_coverage import hole_requirement_outcomes

    drawing = refused_pattern_drawing
    recognition = drawing.recognition()
    ownership = drawing.recognition_ownership()
    (refusal,) = ownership.hole_pattern_refusals
    assert refusal.pattern is recognition.hole_patterns[0]
    assert refusal.reason_code == "uncorroborated_bolt_circle"
    count = len(recognition.holes)
    assert count in (4, 6)
    outcomes = hole_requirement_outcomes(
        recognition, drawing.model().features, drawing.registry, ownership=ownership
    )
    sizes = [row for row in outcomes if row.parameter_id == "bore.diameter"]
    assert sum(row.member_count for row in sizes) == count
    assert {id(record) for row in sizes for record in row.source_records} == {
        id(record) for record in recognition.holes
    }
    assert all(row.state == "placed" for row in sizes)
    locations = [row for row in outcomes if row.parameter_id.startswith("location.location.")]
    assert {row.parameter_id for row in locations} == {
        "location.location.x",
        "location.location.y",
    }
    assert all(row.state == "placed" for row in locations)
    assert _drawing_consumer_outcomes(recognition.holes, drawing) == ["supported"] * count
    assert (
        _hole_model_outcomes(
            recognition.holes, recognition, drawing.model().features, ownership=ownership
        )
        == ["supported"] * count
    )


def test_declared_replay_uses_the_same_physical_policy_without_fabricated_ownership(
    refused_pattern_drawing,
):
    from draftwright.linting.hole_coverage import hole_requirement_outcomes

    drawing = refused_pattern_drawing
    recognition = drawing.recognition()
    outcomes = hole_requirement_outcomes(recognition, drawing.model().features, drawing.registry)
    assert outcomes
    assert all(row.state == "placed" for row in outcomes)
    with pytest.raises(ValueError, match="same run"):
        hole_requirement_outcomes(
            replace(recognition),
            drawing.model().features,
            drawing.registry,
            ownership=drawing.recognition_ownership(),
        )


def test_refusals_require_exact_run_identity_and_one_decision(refused_pattern_drawing):
    from draftwright.recognition_ownership import RecognitionOwnershipBuilder

    ownership = refused_pattern_drawing.recognition_ownership()
    builder = RecognitionOwnershipBuilder(ownership.evidence)
    (refusal,) = ownership.hole_pattern_refusals
    pattern = refusal.pattern
    with pytest.raises(ValueError, match="belong to this recognition run"):
        builder.refuse_hole_pattern(replace(pattern), reason_code=refusal.reason_code)
    assert builder.snapshot().hole_pattern_refusals == ()
    builder.refuse_hole_pattern(pattern, reason_code=refusal.reason_code)
    snapshot = builder.snapshot()
    assert snapshot.hole_pattern_refusals[0].pattern is pattern
    with pytest.raises(ValueError, match="already recorded"):
        builder.refuse_hole_pattern(pattern, reason_code=refusal.reason_code)
    assert builder.snapshot().hole_pattern_refusals == snapshot.hole_pattern_refusals
    with pytest.raises(ValueError, match="unknown hole-pattern refusal reason"):
        builder.refuse_hole_pattern(pattern, reason_code="unknown")

    from draftwright.linting.hole_coverage import hole_requirement_outcomes

    forged = replace(
        ownership, hole_pattern_refusals=(replace(refusal, pattern=replace(pattern)),)
    )
    with pytest.raises(ValueError, match="does not belong to this recognition run"):
        hole_requirement_outcomes(
            ownership.evidence.result, (), refused_pattern_drawing.registry, ownership=forged
        )


def test_oblique_pattern_refusal_is_recorded_at_the_adapter():
    from build123d import Axis
    from quiddity.evidence import build_recognition_evidence

    from draftwright.model.detect import _build_part_model_from_recognition
    from draftwright.recognition_ownership import RecognitionOwnershipBuilder

    part = _grid_plate(_EVEN_ROWS).rotate(Axis.X, 25)
    evidence = build_recognition_evidence(part)
    assert len(evidence.result.holes) == 6
    assert len(evidence.result.hole_patterns) == 1
    assert all(abs(hole.axis[1]) > 0.1 for hole in evidence.result.holes)
    builder = RecognitionOwnershipBuilder(evidence)
    model = _build_part_model_from_recognition(part, evidence.result, ownership=builder)
    ownership = builder.snapshot()
    (refusal,) = ownership.hole_pattern_refusals
    assert refusal.pattern is evidence.result.hole_patterns[0]
    assert refusal.reason_code == "oblique_pattern_plane"
    assert sum(feature.count for feature in model.features if feature.kind == "hole") == 6
    occurrences = [ref for ref in evidence.features if evidence.family(ref) == "holes"]
    assert len(occurrences) == 6
    assert all(ownership.status(ref) == "absorbed" for ref in occurrences)


@pytest.mark.parametrize("corroborated", [False, True])
def test_declared_patterns_remain_exact_and_corroborated_patterns_cannot_disappear(corroborated):
    from quiddity import BoltCircle
    from quiddity.evidence import build_recognition_evidence

    from draftwright.linting.hole_coverage import hole_requirement_outcomes
    from draftwright.model.detect import _pattern_feature
    from draftwright.registry import AnnotationRegistry

    part = (
        _flange(4, central_feature=False)
        if corroborated
        else _grid_plate((-10, 10), columns=(-10, 10))
    )
    result = build_recognition_evidence(part).result
    (source,) = result.hole_patterns
    assert isinstance(source, BoltCircle) and len(source.holes) == 4
    feature = _pattern_feature(source, source.holes)
    registry = AnnotationRegistry()
    exact = hole_requirement_outcomes(result, [feature], registry)
    assert exact and all(row.state == "missing" for row in exact)
    assert any(row.parameter_id == "bolt_circle.diameter" for row in exact)
    wrong = replace(feature, bcd=feature.bcd + 1)
    mismatch = hole_requirement_outcomes(result, [wrong], registry)
    assert any(row.state == "unverifiable" for row in mismatch)

    ordinary = replace(feature.member, count=4, members=feature.members)
    fallback = hole_requirement_outcomes(result, [ordinary], registry)
    assert fallback
    assert any(row.state == "unverifiable" for row in fallback) is corroborated
    if not corroborated:
        assert all(row.state == "missing" for row in fallback)
        assert (
            sum(row.member_count for row in fallback if row.parameter_id == "bore.diameter") == 4
        )
