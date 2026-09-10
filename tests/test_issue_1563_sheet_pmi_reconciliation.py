"""A Sheet-built drawing reconciles its source PMI, or says it could not (#1563).

`Sheet` holds an in-memory solid, and `analysis` only extracted an AP242 census from a STEP
*path*, so no script-built drawing ran PMI reconciliation at all — not even to report that it
had not. The emitter writes unlowered records as `sheet.add(PmiFeature(..))  # raw AP242 PMI
fallback`, and from there they were invisible: their presence, their deletion and a fabricated
`source_id` all produced byte-identical diagnostics.
"""

import pytest
from build123d import import_step

from draftwright import Sheet
from draftwright.model import Frame, PmiFeature

SOURCE = "tests/fixtures/grm03_thumbwheel_drive_screw_ap242_pmi.step"

#: One of the five records the emitter cannot lower on this part — a surface finish, which is a
#: manufacturing requirement a shop must see.
RAW_SOURCE_ID = "manufacturing_requirement:#2012"
#: An AP242 dimension the part really does carry, used as the honest provenance claim.
REAL_SOURCE_ID = "dimension:0:1:4:5"


@pytest.fixture(scope="module")
def part():
    return import_step(SOURCE)


def _sheet(part, *, source=None, pmi=None, raw=True, claim=REAL_SOURCE_ID):
    options = {}
    if source is not None:
        options["source"] = source
    if pmi is not None:
        options["pmi"] = pmi
    sheet = Sheet(part, title="GRM03", **options)
    sheet.hole(diameter=1.6, at=(-3.2, 0, 0), axis="x").depth(8).requirement(
        1.6, source="ap242_pmi", source_ids=(claim,)
    )
    if raw:
        sheet.add(
            PmiFeature(
                frame=Frame((11.15, 0, 0), "z"),
                pmi_kind="surface_texture",
                value=0,
                label="Ra 3.2 um unless otherwise specified",
                dominant_axis="?",
                ref_bbox=None,
                ref_pts=(),
                source_id=RAW_SOURCE_ID,
                source_category="manufacturing_requirement",
            )
        )
    sheet.authored_dimensions()
    return sheet


def _codes(drawing) -> dict[str, int]:
    return {
        code: count
        for code, count in drawing.lint_summary()["by_code"].items()
        if code.startswith("pmi_")
    }


@pytest.fixture(scope="module")
def unsourced(part):
    return _sheet(part).build()


@pytest.fixture(scope="module")
def reconciled(part):
    return _sheet(part, source=SOURCE, pmi="annotate").build()


def test_the_fixture_really_carries_ap242_records(part):
    """Precondition. Every assertion below is about what happens to source PMI, so a run
    against a file with none would pass while proving nothing."""
    from draftwright.pmi import extract_pmi_report

    report = extract_pmi_report(SOURCE)
    assert len(report.sources) == 26
    assert RAW_SOURCE_ID in {entity.source_id for entity in report.sources}
    assert REAL_SOURCE_ID in {
        source_id for record in report.records for source_id in record.source_ids or ()
    } | {record.source_id for record in report.records}


def test_a_sheet_can_name_the_document_its_solid_came_from(reconciled):
    summary = reconciled.lint_summary()["pmi"]
    assert summary is not None, "a Sheet given its source must reconcile, not stay silent"
    assert summary["mode"] == "annotate"
    assert summary["sources"] == 26
    # The link is the caller's claim, so the report states which document it checked.
    assert summary["source"]["name"] == "grm03_thumbwheel_drive_screw_ap242_pmi.step"
    assert len(summary["source"]["sha256"]) == 64


def test_unlowered_records_are_reported_on_the_sheet_path(reconciled):
    assert _codes(reconciled)["pmi_not_lowered"] >= 1
    assert any(
        RAW_SOURCE_ID in issue.message
        for issue in reconciled.lint()
        if issue.code == "pmi_not_lowered"
    )


def test_deleting_a_raw_record_changes_the_diagnostics(part):
    """io#623's exact complaint: "an agent that deletes the fallback line gets no signal"."""
    kept = _sheet(part, source=SOURCE, pmi="annotate").build()
    dropped = _sheet(part, source=SOURCE, pmi="annotate", raw=False).build()

    def reason(drawing):
        return next(
            issue.message
            for issue in drawing.lint()
            if issue.code == "pmi_not_lowered" and RAW_SOURCE_ID in issue.message
        )

    assert "raw" in reason(kept)
    assert "did not produce a typed IR feature" in reason(dropped)
    assert reason(kept) != reason(dropped)


def test_a_fabricated_source_id_is_reported_not_accepted(part):
    drawing = _sheet(part, source=SOURCE, pmi="annotate", claim="dimension:NO-SUCH-RECORD").build()
    unknown = [issue for issue in drawing.lint() if issue.code == "pmi_source_unknown"]
    assert len(unknown) == 1
    assert unknown[0].severity == "error"
    assert "dimension:NO-SUCH-RECORD" in unknown[0].message
    # And the honest claim on the same build is not swept up with it.
    assert REAL_SOURCE_ID not in unknown[0].message


def test_an_honest_claim_is_not_reported_as_fabricated(reconciled):
    assert "pmi_source_unknown" not in _codes(reconciled)


def test_content_with_no_census_is_unverified_rather_than_silent(unsourced):
    """No source named: the claims cannot be checked, and that is itself the finding. It must
    not read as "checked and fine" — which is what it read as before."""
    codes = _codes(unsourced)
    assert codes.get("pmi_unreconciled") == 1
    issue = next(i for i in unsourced.lint() if i.code == "pmi_unreconciled")
    assert "unverified, not satisfied" in issue.message
    assert RAW_SOURCE_ID in issue.source_ids
    assert unsourced.lint_summary()["geometry_issues"] > 0


def test_a_drawing_with_no_ap242_claims_stays_quiet():
    """The check is scoped to content claiming an AP242 origin, so an ordinary part with none
    must not acquire a warning it cannot act on."""
    from build123d import Box

    sheet = Sheet(Box(30, 20, 10), title="BLOCK")
    sheet.authored_dimensions()
    assert "pmi_unreconciled" not in _codes(sheet.build())


@pytest.mark.slow
def test_a_generated_script_names_its_own_source(tmp_path):
    """The emitted script opens the STEP in its `part =` line; it must hand the same path to
    `Sheet` or the re-run reconciles nothing."""
    import runpy

    from draftwright.sheet_emit import generate_sheet_script

    script = generate_sheet_script(
        SOURCE,
        out=str(tmp_path / "grm03"),
        title="GRM03",
        pmi="annotate",
        formats=("svg",),
        inspect=False,
    )
    text = open(script).read()
    ctor = next(line for line in text.splitlines() if line.startswith("sheet = Sheet("))
    assert "source=" in ctor and "pmi='annotate'" in ctor
    imported = next(line for line in text.splitlines() if line.startswith("part = import_step("))
    assert ctor.split("source=")[1].split(",")[0].strip(" )") in imported

    drawing = runpy.run_path(script)["drawing"]
    codes = _codes(drawing)
    assert codes["pmi_not_lowered"] == 5
    assert "pmi_unreconciled" not in codes
