"""A report must refuse broken references rather than serialize a plausible smaller graph."""

from dataclasses import replace

import pytest
from test_issue_1542_document_report import hole_document

from draftwright.reporting import ReportUnavailableError, document_report


@pytest.fixture(scope="module")
def report_inputs(tmp_path_factory):
    import draftwright.document as document_module

    document, _path, _raw = hole_document(tmp_path_factory.mktemp("report-authority"))
    result = document.build()
    captured = {}

    def capture(**kwargs):
        captured.update(kwargs)
        return document_report(**kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(document_module, "document_report", capture)
        report = result.report()
    assert report["claims"] and report["assessment"]["fidelity"]["conflicts"]
    assert len(captured["evaluation"].members) == 2
    return captured


@pytest.mark.parametrize("change", ["missing", "equal_clone"])
def test_report_requires_its_exact_complete_catalog(report_inputs, change):
    evaluation = report_inputs["evaluation"]
    first, *rest = evaluation.requirements
    rows = (
        tuple(rest)
        if change == "missing"
        else (replace(first, requirement=replace(first.requirement)), *rest)
    )
    with pytest.raises(ReportUnavailableError, match="exact catalog"):
        document_report(**{**report_inputs, "evaluation": replace(evaluation, requirements=rows)})


@pytest.mark.parametrize("change", ["foreign_owner", "absent_sheet", "absent_annotation"])
def test_claim_references_must_resolve_within_the_document(report_inputs, change):
    evaluation = report_inputs["evaluation"]
    snapshot, *others = evaluation.claims
    claim, *rest = snapshot.claims
    if change == "foreign_owner":
        changed = replace(claim, owner=replace(claim.owner))
    elif change == "absent_sheet":
        changed = replace(claim, sheet="absent")
    else:
        changed = replace(claim, annotation="absent")
    altered = replace(evaluation, claims=(replace(snapshot, claims=(changed, *rest)), *others))
    with pytest.raises(
        ReportUnavailableError, match="exact common owner|absent member annotation"
    ):
        document_report(**{**report_inputs, "evaluation": altered})


@pytest.mark.parametrize("members", ["empty", "duplicate"])
def test_report_cannot_lose_or_duplicate_member_identity(report_inputs, members):
    evaluation = report_inputs["evaluation"]
    changed = () if members == "empty" else (evaluation.members[0], evaluation.members[0])
    with pytest.raises(ReportUnavailableError, match="member identities"):
        document_report(**{**report_inputs, "evaluation": replace(evaluation, members=changed)})


def test_conflict_cannot_refer_to_an_unpublished_equal_claim(report_inputs):
    evaluation = report_inputs["evaluation"]
    conflict, *rest = evaluation.conflicts
    first, *others = conflict.claims
    changed = replace(conflict, claims=(replace(first), *others))
    with pytest.raises(ReportUnavailableError, match="absent confirmed claim"):
        document_report(
            **{**report_inputs, "evaluation": replace(evaluation, conflicts=(changed, *rest))}
        )


def test_producer_carrier_cannot_name_absent_ink(report_inputs):
    evaluation = report_inputs["evaluation"]
    index, row = next(
        (i, row)
        for i, row in enumerate(evaluation.requirements)
        if getattr(row.combined.outcome, "carriers", ()) and row.state == "placed"
    )
    outcome = row.combined.outcome
    carrier, *rest = outcome.carriers
    outcome = replace(outcome, carriers=(replace(carrier, annotation="absent"), *rest))
    changed = replace(row, combined=replace(row.combined, outcome=outcome))
    rows = list(evaluation.requirements)
    rows[index] = changed
    with pytest.raises(ReportUnavailableError, match="carrier names an absent"):
        document_report(
            **{**report_inputs, "evaluation": replace(evaluation, requirements=tuple(rows))}
        )


@pytest.mark.parametrize("change", ["invalid_state", "unattributed_placed"])
def test_report_distinguishes_invalid_state_from_missing_carrier_attribution(
    report_inputs, change
):
    evaluation = report_inputs["evaluation"]
    index, row = next(
        (i, row) for i, row in enumerate(evaluation.requirements) if row.state == "placed"
    )
    rows = list(evaluation.requirements)
    if change == "invalid_state":
        rows[index] = replace(row, state="complete")
        with pytest.raises(ReportUnavailableError, match="invalid evaluation state"):
            document_report(
                **{**report_inputs, "evaluation": replace(evaluation, requirements=tuple(rows))}
            )
    else:
        rows[index] = replace(
            row, combined=replace(row.combined, outcome=replace(row.combined.outcome, carriers=()))
        )
        report = document_report(
            **{**report_inputs, "evaluation": replace(evaluation, requirements=tuple(rows))}
        )
        emitted = report["recognition"]["requirements"][index]
        assert emitted["carrier_attribution"] == "unavailable"
        assert emitted["carrying_annotations"] == []
        assert report["status"] == "needs-attention"


@pytest.mark.parametrize("missing", ["lint", "resolved", "recipe"])
def test_report_requires_member_lint_and_replay_inputs(report_inputs, missing):
    kwargs = dict(report_inputs)
    evaluation = kwargs["evaluation"]
    name, snapshot, rows = evaluation.members[0]
    if missing == "lint":
        kwargs["evaluation"] = replace(
            evaluation,
            members=((name, replace(snapshot, lint=None), rows), *evaluation.members[1:]),
        )
    else:
        key = "resolved" if missing == "resolved" else "member_recipes"
        kwargs[key] = {k: v for k, v in kwargs[key].items() if k != name}
    with pytest.raises(ReportUnavailableError, match="lacks lint or run options"):
        document_report(**kwargs)
