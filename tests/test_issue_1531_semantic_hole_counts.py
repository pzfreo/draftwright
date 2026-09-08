"""Count diagnostics and edits must preserve recognised drilling operations (#1531)."""

from dataclasses import replace

import pytest
from build123d import Align, Box, Cylinder, Pos, Rot

from draftwright import Sheet, build_drawing
from draftwright.linting import LintIssue, _suggest_fix, lint_feature_coverage
from draftwright.linting.hole_coverage import hole_requirement_outcomes


@pytest.fixture(scope="module")
def mixed_part():
    part = Box(60, 45, 12)
    for x in (-20, 0, 20):
        for y in (-7, 9):
            part -= Pos(x, y, 0) * Cylinder(1.2, 20)
    for y in (-15, 0, 15):
        part -= (
            Pos(28.5, y, 0)
            * Rot(0, 90, 0)
            * Cylinder(1.2, 1.5, align=(Align.CENTER, Align.CENTER, Align.MIN))
        )
    return part


def _groups(drawing):
    groups = {
        feature.frame.axis: feature
        for feature in drawing.model().features
        if feature.kind == "pattern"
    }
    assert set(groups) == {"x", "z"}
    assert groups["z"].count == 6 and groups["z"].member.through
    assert groups["x"].count == 3 and not groups["x"].member.through
    assert groups["x"].member.depth == pytest.approx(1.5)
    assert groups["x"].member.diameter == groups["z"].member.diameter == 2.4
    return groups


def _bore_issue(drawing, feature):
    return next(
        issue
        for issue in drawing.lint()
        if issue.code == "hole_requirement_missing"
        and (feature, "bore.diameter") in issue.hole_requirement_ids
    )


def _counts(drawing):
    return {
        feature.frame.axis: sorted(
            getattr(drawing.get_annotation(name), "covers_count")
            for name in drawing.annotations_of(feature)
            if hasattr(drawing.get_annotation(name), "covers_count")
        )
        for feature in drawing.model().features
        if feature.kind == "pattern"
    }


def test_suggestion_restores_three_blind_sockets_without_changing_six_through(mixed_part):
    drawing = build_drawing(mixed_part, auto_dims=False)
    groups = _groups(drawing)
    drawing.callout(groups["z"])
    assert _counts(drawing) == {"x": [], "z": [6]}
    # This is the original false arithmetic, independently of the new Drawing path.
    legacy = lint_feature_coverage(
        mixed_part,
        drawing.items,
        holes=drawing.recognition().holes,
        bosses=drawing.recognition().bosses,
        registry=drawing.registry,
    )
    mismatch = next(issue for issue in legacy if issue.code == "feature_count_mismatch")
    assert "9 ø2.4" in mismatch.message and "account for 6" in mismatch.message

    issues = drawing.lint()
    assert not any(issue.code == "feature_count_mismatch" for issue in issues)
    missing = _bore_issue(drawing, groups["x"])
    assert "3× ø2.4 blind depth 1.5" in missing.message
    assert "axis (-1.0, 0.0, 0.0)" in missing.message
    assert missing.hole_requirement_ids == ((groups["x"], "bore.diameter"),)
    assert "count=" not in missing.suggestion
    exec(missing.suggestion, {"dwg": drawing})
    assert _counts(drawing) == {"x": [3], "z": [6]}
    outcomes = hole_requirement_outcomes(
        drawing.recognition(), drawing.model().features, drawing.registry
    )
    assert {o.state for o in outcomes if o.parameter_id == "grouping.count"} == {"placed"}
    assert {o.member_count for o in outcomes if o.parameter_id == "grouping.count"} == {3, 6}
    assert not any(i.code == "feature_count_mismatch" for i in drawing.lint())


def test_undeclared_sockets_are_identified_without_inventing_an_owner(mixed_part):
    sheet = Sheet.from_part(mixed_part).take_over(
        dimensions="authored", principal_views="automatic", derived_views="authored"
    )
    socket = next(f for f in sheet.features if f.kind == "pattern" and f.frame.axis == "x")
    sheet.features.remove(socket)
    bolts = next(f for f in sheet.features if f.kind == "pattern")
    sheet.dimension(bolts, "bore.diameter")
    drawing = sheet.build()
    issues = drawing.lint()
    missing = next(i for i in issues if i.code == "hole_requirement_unverifiable")
    assert "3× ø2.4 blind depth 1.5" in missing.message
    assert missing.hole_requirement_ids == ()
    assert "separate hole/group" in missing.suggestion
    assert "dwg.callout(" not in missing.suggestion
    assert not any(i.code == "feature_count_mismatch" for i in issues)
    assert _counts(drawing) == {"z": [6]}


def test_stale_equal_feature_is_not_an_executable_target(mixed_part):
    drawing = build_drawing(mixed_part, auto_dims=False)
    feature = _groups(drawing)["x"]
    missing = _bore_issue(drawing, feature)
    assert "dwg.callout(" in missing.suggestion
    stale = replace(feature)
    assert stale is not feature
    missing.hole_requirement_ids = ((stale, "bore.diameter"),)
    assert _suggest_fix(missing, drawing) is None


def test_conflicting_count_cannot_be_repaired_by_adding_another_callout(mixed_part):
    drawing = build_drawing(mixed_part, auto_dims=False)
    feature = _groups(drawing)["z"]
    drawing.callout(feature)
    callout = next(
        drawing.get_annotation(name)
        for name in drawing.annotations_of(feature)
        if hasattr(drawing.get_annotation(name), "covers_count")
    )
    assert callout.covers_count == 6
    callout.covers_count = 9
    # The physical ledger's explicit feature allocation must not mask a contradictory total.
    issue = next(
        i
        for i in drawing.lint()
        if i.code == "hole_requirement_missing"
        and (feature, "grouping.count") in i.hole_requirement_ids
    )
    assert "dwg.callout(" not in issue.suggestion
    assert "conflicting existing callout" in issue.suggestion


def test_authored_partial_bundle_never_gets_an_automatic_callout_suggestion(mixed_part):
    sheet = Sheet.from_part(mixed_part).take_over(
        dimensions="authored", principal_views="automatic", derived_views="authored"
    )
    socket = next(f for f in sheet.features if f.kind == "pattern" and f.frame.axis == "x")
    sheet.dimension(socket, "bore.diameter")
    drawing = sheet.build()
    feature = _groups(drawing)["x"]
    issue = LintIssue(
        "warning",
        "the bore annotation was lost",
        code="hole_requirement_missing",
        hole_requirement_ids=((feature, "bore.diameter"),),
    )
    suggestion = _suggest_fix(issue, drawing)
    assert "dwg.callout(" not in suggestion
    assert "Preserve authored" in suggestion
    assert any(
        i.code == "hole_requirement_suppressed"
        and (feature, "bore.depth") in i.hole_requirement_ids
        for i in drawing.lint()
    )


@pytest.mark.parametrize("difference", ["axis", "depth", "through"])
def test_each_geometric_operation_distinction_survives_count_reconciliation(difference):
    part = Box(60, 40, 20)
    part -= Pos(-15, 0, 7.5) * Cylinder(3, 5)
    if difference == "axis":
        part -= Pos(27.5, 0, 0) * Rot(0, 90, 0) * Cylinder(3, 5)
    elif difference == "depth":
        part -= Pos(15, 0, 6) * Cylinder(3, 8)
    else:
        part -= Pos(15, 0, 0) * Cylinder(3, 30)
    drawing = build_drawing(part, auto_dims=False)
    records = drawing.recognition().holes
    assert len(records) == 2 and {h.diameter for h in records} == {6}
    outcomes = hole_requirement_outcomes(
        drawing.recognition(), drawing.model().features, drawing.registry
    )
    # Two singleton sources, each with one verified owner, not one diameter-total group.
    bores = [o for o in outcomes if o.parameter_id == "bore.diameter"]
    assert len(bores) == 2
    assert all(o.member_count == 1 and len(o.features) == 1 for o in bores)
    assert all(len(o.source_records) == 1 for o in bores)
    first, second = [o.features[0] for o in bores]
    assert first is not second
    drawing.callout(first)
    missing = _bore_issue(drawing, second)
    assert missing.hole_requirement_ids == ((second, "bore.diameter"),)
    assert not any(i.code == "feature_count_mismatch" for i in drawing.lint())


def test_distinct_thread_and_fit_owners_cannot_borrow_each_others_count():
    part = Box(60, 40, 20)
    for x in (-15, 15):
        part -= Pos(x, 0, 0) * Cylinder(1.2, 30)
    sheet = Sheet(part)
    tapped = sheet.hole(diameter=2.4, at=(-15, 0, 0), axis="z", thread="M3x0.5")
    fitted = sheet.hole(diameter=2.4, at=(15, 0, 0), axis="z").fit("H7")
    sheet.dimension(tapped, "bore.diameter")
    drawing = sheet.build()
    holes = [f for f in drawing.model().features if f.kind == "hole"]
    assert len(holes) == 2 and {f.count for f in holes} == {1}
    count_issue = next(
        i
        for i in drawing.lint()
        if i.code == "hole_requirement_missing"
        and any(parameter == "grouping.count" for _, parameter in i.hole_requirement_ids)
    )
    assert len(count_issue.hole_requirement_ids) == 2
    assert "dwg.callout(" not in count_issue.suggestion
    assert "count=" not in count_issue.suggestion
    # Explicitly author the other operation; quantity stays attached to each owner.
    sheet.dimension(fitted, "bore.diameter")
    complete = sheet.build()
    for feature in (f for f in complete.model().features if f.kind == "hole"):
        callouts = [
            complete.get_annotation(n)
            for n in complete.annotations_of(feature)
            if hasattr(complete.get_annotation(n), "covers_count")
        ]
        assert len(callouts) == 1 and callouts[0].covers_count == 1
        assert ("M3" if feature.thread else "H7") in callouts[0].label
    assert not any(
        i.code == "hole_requirement_missing"
        and any(p == "grouping.count" for _, p in i.hole_requirement_ids)
        for i in complete.lint()
    )


def test_old_missing_finding_does_not_add_duplicate_ink_after_owner_is_restored(mixed_part):
    drawing = build_drawing(mixed_part, auto_dims=False)
    groups = _groups(drawing)
    issue = _bore_issue(drawing, groups["x"])
    assert "dwg.callout(" in issue.suggestion
    drawing.callout(groups["x"])
    assert _suggest_fix(issue, drawing) is None
    drawing.callout(groups["x"])
    # Duplicate evidence on the sockets cannot provide any bolt quantity.
    assert _counts(drawing) == {"x": [3, 3], "z": []}
    missing_bolts = _bore_issue(drawing, groups["z"])
    assert missing_bolts.hole_requirement_ids == ((groups["z"], "bore.diameter"),)
    assert any(
        i.code == "hole_requirement_missing"
        and (groups["z"], "grouping.count") in i.hole_requirement_ids
        for i in drawing.lint()
    )
