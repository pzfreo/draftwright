"""#1116 — AP242 hole tolerances enrich canonical feature dimensions exactly once."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from build123d import Box, import_step

from draftwright import Sheet
from draftwright.builder import detect_part_model
from draftwright.model.ir import (
    AuthoredDimension,
    Frame,
    HoleFeature,
    PartModel,
    PatternFeature,
    ToleranceDecoration,
)
from draftwright.model.pmi_lowering import lower_ap242_hole_tolerances
from draftwright.sheet_emit import emit_sheet_script


def _hole(at=(0.0, 0.0, 0.0), *, diameter=10.0, members=()):
    positions = tuple(members)
    return HoleFeature(
        frame=Frame(at, "z"),
        diameter=diameter,
        depth=8.0,
        through=True,
        count=len(positions) or 1,
        members=positions,
    )


def _dimension(
    *,
    value=10.0,
    lower_tol=None,
    upper_tol=None,
    lower_bound=None,
    upper_bound=None,
    bbox=(-5.1, -5.1, -0.1, 5.1, 5.1, 8.1),
    source_id="dimension:test",
):
    return AuthoredDimension(
        frame=Frame((0.0, 0.0, 4.0), "z"),
        dimension_kind="diameter",
        value=value,
        label=f"ø{value:g}",
        dominant_axis="Z",
        lower_tol=lower_tol,
        upper_tol=upper_tol,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
        ref_bbox=bbox,
        ref_pts=((-5.0, 0.0, 4.0), (5.0, 0.0, 4.0)),
        source_id=source_id,
    )


def _model(*features):
    return PartModel(Box(40, 40, 10).bounding_box(), None, list(features))


@pytest.mark.parametrize(
    ("dimension", "expected"),
    (
        (_dimension(lower_tol=0.1, upper_tol=0.1), 0.1),
        (_dimension(lower_tol=0.05, upper_tol=0.1), (0.05, 0.1)),
        (_dimension(lower_tol=0.1), (0.1, 0.0)),
        (_dimension(upper_tol=0.1), (0.0, 0.1)),
        (_dimension(value=10, lower_bound=9.8, upper_bound=10.15), (0.2, 0.15)),
    ),
    ids=("symmetric", "asymmetric", "lower-only", "upper-only", "limits"),
)
def test_supported_deviation_forms_lower_to_one_bore_decoration(dimension, expected):
    hole = _hole()
    lowered = lower_ap242_hole_tolerances(_model(hole, dimension))

    assert not any(feature.kind == "authored_dimension" for feature in lowered.features)
    owner = next(feature for feature in lowered.features if feature.kind == "hole")
    requirement = lowered.decorations[(owner, "diameter")]
    assert requirement == ToleranceDecoration(expected, "ap242_pmi", ("dimension:test",))


def test_normalized_inch_source_values_are_not_scaled_a_second_time():
    """Extraction's public contract is mm; lowering consumes that unit without reinterpretation."""
    hole = _hole(diameter=25.4)
    dim = _dimension(
        value=25.4,
        lower_tol=0.0254,
        upper_tol=0.0508,
        bbox=(-12.8, -12.8, -0.1, 12.8, 12.8, 8.1),
    )
    lowered = lower_ap242_hole_tolerances(_model(hole, dim))
    owner = next(feature for feature in lowered.features if feature.kind == "hole")
    assert lowered.decorations[(owner, "diameter")].value == (0.0254, 0.0508)


def test_member_specific_requirements_split_a_count_group_without_lying_about_siblings():
    members = ((-20.0, 0.0, 0.0), (0.0, 0.0, 0.0), (20.0, 0.0, 0.0))
    hole = _hole(at=members[0], members=members)
    dim = _dimension(
        lower_tol=0.1,
        upper_tol=0.2,
        bbox=(-5.1, -5.1, -0.1, 5.1, 5.1, 8.1),
    )
    lowered = lower_ap242_hole_tolerances(_model(hole, dim))

    holes = [feature for feature in lowered.features if feature.kind == "hole"]
    assert [(feature.count, feature.members) for feature in holes] == [
        (2, (members[0], members[2])),
        (1, (members[1],)),
    ]
    assert (holes[0], "diameter") not in lowered.decorations
    assert lowered.decorations[(holes[1], "diameter")].value == (0.1, 0.2)


def test_pattern_wide_requirement_preserves_pattern_identity_and_membership():
    members = ((-10.0, 0.0, 0.0), (10.0, 0.0, 0.0))
    member = _hole(at=members[0])
    pattern = PatternFeature(
        frame=Frame((0.0, 0.0, 0.0), "z"),
        pattern="linear",
        count=2,
        member=member,
        members=members,
        pitch=20.0,
        direction=(1.0, 0.0, 0.0),
    )
    dim = _dimension(
        lower_tol=0.05,
        upper_tol=0.1,
        bbox=(-15.1, -5.1, -0.1, 15.1, 5.1, 8.1),
    )
    lowered = lower_ap242_hole_tolerances(_model(pattern, dim))

    assert lowered.features == [pattern]
    assert lowered.decorations[(pattern, "diameter", "bore")].value == (0.05, 0.1)

    source = emit_sheet_script(lowered, "part", "pattern", title="P", number="N")
    namespace = {"part": Box(40, 40, 10)}
    exec(  # noqa: S102
        compile(source[: source.index("drawing = sheet.build()")], "<pattern-emit>", "exec"),
        namespace,
    )
    rebuilt = namespace["sheet"].model()
    rebuilt_pattern = next(
        feature for feature in rebuilt.features if isinstance(feature, PatternFeature)
    )
    assert rebuilt_pattern.members == pattern.members
    assert rebuilt_pattern.member.diameter == pattern.member.diameter
    assert rebuilt.decorations[(rebuilt_pattern, "diameter", "bore")] == ToleranceDecoration(
        (0.05, 0.1), "ap242_pmi", ("dimension:test",)
    )


def test_partial_pattern_and_ambiguous_matches_fall_back_with_explicit_reasons():
    members = ((-10.0, 0.0, 0.0), (10.0, 0.0, 0.0))
    pattern = PatternFeature(
        frame=Frame((0.0, 0.0, 0.0), "z"),
        pattern="linear",
        count=2,
        member=_hole(at=members[0]),
        members=members,
        pitch=20.0,
        direction=(1.0, 0.0, 0.0),
    )
    partial = _dimension(
        lower_tol=0.1,
        upper_tol=0.1,
        bbox=(-15.1, -5.1, -0.1, -4.9, 5.1, 8.1),
    )
    partial_model = lower_ap242_hole_tolerances(_model(pattern, partial))
    fallback = next(f for f in partial_model.features if f.kind == "authored_dimension")
    assert fallback.lowering_blockers == (
        "unsupported hole correlation: AP242 requirement covers only part of a canonical hole pattern",
    )
    source = emit_sheet_script(partial_model, "part", "fallback", title="P", number="N")
    assert "lowering_blockers=('unsupported hole correlation:" in source
    namespace = {"part": Box(40, 40, 10)}
    exec(  # noqa: S102
        compile(source[: source.index("drawing = sheet.build()")], "<fallback-emit>", "exec"),
        namespace,
    )
    restored = next(
        feature
        for feature in namespace["sheet"].model().features
        if feature.kind == "authored_dimension"
    )
    assert restored.lowering_blockers == fallback.lowering_blockers

    duplicate = replace(_hole(), frame=Frame((0.0, 0.0, 0.0), "z"))
    ambiguous_model = lower_ap242_hole_tolerances(
        _model(_hole(), duplicate, _dimension(lower_tol=0.1, upper_tol=0.1))
    )
    fallback = next(f for f in ambiguous_model.features if f.kind == "authored_dimension")
    assert fallback.lowering_blockers[0].startswith("ambiguous hole correlation:")


def test_unmatched_requirement_without_any_hole_owner_keeps_an_explicit_reason():
    lowered = lower_ap242_hole_tolerances(_model(_dimension(lower_tol=0.1, upper_tol=0.1)))
    (fallback,) = lowered.features
    assert fallback.lowering_blockers[0].startswith("unmatched hole correlation:")


@pytest.mark.parametrize(
    ("dimension", "reason"),
    (
        (
            replace(_dimension(lower_tol=0.1), ref_bbox=None),
            "unmatched hole correlation: source diameter has no referenced geometry",
        ),
        (
            replace(_dimension(lower_tol=0.1), dominant_axis="?"),
            "unsupported hole correlation: source diameter has no principal bore axis",
        ),
        (
            _dimension(lower_bound=9.9),
            "unsupported hole tolerance: a limit requirement needs both lower and upper bounds",
        ),
        (
            _dimension(lower_tol=-0.1),
            "unsupported hole tolerance: negative deviation magnitude",
        ),
    ),
)
def test_unsupported_source_requirements_remain_materialized_with_their_reason(dimension, reason):
    lowered = lower_ap242_hole_tolerances(_model(_hole(), dimension))
    fallback = next(
        feature for feature in lowered.features if feature.kind == "authored_dimension"
    )
    assert fallback.lowering_blockers == (reason,)


def test_extractor_blockers_and_existing_authored_ownership_win_without_duplication():
    blocked = replace(_dimension(lower_tol=0.1), lowering_blockers=("source geometry incomplete",))
    lowered = lower_ap242_hole_tolerances(_model(_hole(), blocked))
    fallback = next(
        feature for feature in lowered.features if feature.kind == "authored_dimension"
    )
    assert fallback.lowering_blockers == ("source geometry incomplete",)

    hole = _hole()
    dimension = _dimension(lower_tol=0.1)
    already_owned = replace(_model(hole, dimension), decorations={(hole, "diameter"): 0.2})
    lowered = lower_ap242_hole_tolerances(already_owned)
    fallback = next(
        feature for feature in lowered.features if feature.kind == "authored_dimension"
    )
    assert fallback.lowering_blockers == (
        "ambiguous hole tolerance ownership: bore already has a tolerance",
    )


def test_conflicting_sources_fall_back_and_other_aspects_follow_a_split_group():
    members = ((-10.0, 0.0, 0.0), (10.0, 0.0, 0.0))
    hole = _hole(at=members[0], members=members)
    first = _dimension(
        lower_tol=0.1,
        bbox=(-15.1, -5.1, -0.1, -4.9, 5.1, 8.1),
        source_id="dimension:first",
    )
    second = replace(first, lower_tol=0.2, source_id="dimension:second")
    conflicted = lower_ap242_hole_tolerances(_model(hole, first, second))
    fallbacks = [
        feature for feature in conflicted.features if feature.kind == "authored_dimension"
    ]
    assert len(fallbacks) == 2
    assert all(
        "conflicting AP242 requirements" in feature.lowering_blockers[0] for feature in fallbacks
    )

    split = lower_ap242_hole_tolerances(
        replace(_model(hole, first), decorations={(hole, "depth"): 0.25})
    )
    owners = [feature for feature in split.features if feature.kind == "hole"]
    assert len(owners) == 2
    assert all(split.decorations[(owner, "depth")] == 0.25 for owner in owners)


def test_imported_tolerance_provenance_arguments_fail_loudly_when_incoherent():
    hole = Sheet(Box(20, 20, 10)).hole(diameter=5, at=(0, 0, 0), axis="z")
    with pytest.raises(ValueError, match="source_ids require source"):
        hole.tolerance(0.1, source_ids=("dimension:test",))
    with pytest.raises(ValueError, match="source must be a non-empty string"):
        hole.tolerance(0.1, source="  ")


def test_emitted_sheet_rebuilds_the_same_owner_value_membership_and_provenance():
    members = ((-10.0, 0.0, 0.0), (10.0, 0.0, 0.0))
    direct = lower_ap242_hole_tolerances(
        _model(
            _hole(at=members[0], members=members),
            _dimension(
                lower_tol=0.05,
                upper_tol=0.1,
                bbox=(4.9, -5.1, -0.1, 15.1, 5.1, 8.1),
            ),
        )
    )
    source = emit_sheet_script(direct, "part", "roundtrip", title="P", number="N")
    namespace = {"part": Box(40, 40, 10)}
    body = source[: source.index("drawing = sheet.build()")]
    exec(compile(body, "<issue-1116-emit>", "exec"), namespace)  # noqa: S102
    rebuilt = namespace["sheet"].model()

    def requirements(model):
        output = []
        for key, value in model.decorations.items():
            if not isinstance(value, ToleranceDecoration) or key[1:] not in (
                ("diameter",),
                ("diameter", "bore"),
            ):
                continue
            owner = key[0]
            output.append(
                (
                    round(float(owner.diameter), 6),
                    tuple(
                        tuple(round(c, 6) for c in point)
                        for point in (owner.members or (owner.frame.origin,))
                    ),
                    value.value,
                    value.source,
                    value.source_ids,
                )
            )
        return sorted(output)

    assert requirements(rebuilt) == requirements(direct)


@pytest.mark.slow
def test_ctc01_consumes_all_hole_tolerances_once_and_emits_provenance():
    path = Path(__file__).parent / "fixtures" / "nist_ctc_01_asme1_ap242.stp"
    model = detect_part_model(path, pmi="annotate")
    authored = [feature for feature in model.features if feature.kind == "authored_dimension"]
    requirements = [
        value for value in model.decorations.values() if isinstance(value, ToleranceDecoration)
    ]

    assert [(feature.dimension_kind, feature.source_id) for feature in authored] == [
        ("angular", "dimension:0:1:4:17")
    ]
    assert {source_id for requirement in requirements for source_id in requirement.source_ids} == {
        "dimension:0:1:4:21",
        "dimension:0:1:4:22",
        "dimension:0:1:4:23",
        "dimension:0:1:4:24",
        "dimension:0:1:4:25",
        "dimension:0:1:4:26",
        "dimension:0:1:4:29",
    }
    source = emit_sheet_script(model, "part", "ctc01", title="CTC01", number="N")
    assert source.count("sheet.measured_dimension(") == 1
    assert source.count("source='ap242_pmi'") == 7
    assert (
        sum(".tolerance(" in line for line in source.splitlines() if " = sheet.hole(" in line) == 6
    )
    assert 'on="bore"' not in source  # CTC uses count-groups, not a recognised pattern.

    namespace = {"part": import_step(str(path))}
    exec(  # noqa: S102
        compile(source[: source.index("drawing = sheet.build()")], "<ctc01-emit>", "exec"),
        namespace,
    )
    rebuilt = namespace["sheet"].model()

    def requirement_signature(part_model):
        return sorted(
            (
                round(key[0].diameter, 6),
                key[0].frame.axis,
                tuple(
                    sorted(
                        tuple(round(coordinate, 6) for coordinate in member)
                        for member in (key[0].members or (key[0].frame.origin,))
                    )
                ),
                value.value,
                value.source_ids,
            )
            for key, value in part_model.decorations.items()
            if isinstance(value, ToleranceDecoration)
        )

    assert requirement_signature(rebuilt) == requirement_signature(model)
    assert [
        (feature.dimension_kind, feature.source_id)
        for feature in rebuilt.features
        if feature.kind == "authored_dimension"
    ] == [("angular", "dimension:0:1:4:17")]
