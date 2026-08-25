"""#1336 — the GRM-03 AP242+PMI fixture, and the preconditions every test on it assumes.

Until this fixture landed, ``GRM03`` in #1296/#1298/#1299 pointed at a file on one
developer's disk and every assertion on it skipped everywhere else.  These checks are the
fixture's own preconditions: that it still carries the PMI those modules read, and that it
is *not* interchangeable with the PMI-free ``grm03_thumbwheel_drive_screw.step`` (different
geometry, not the same solid with annotations bolted on).
"""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path

from draftwright.analysis import _import_step
from draftwright.pmi import extract_pmi_report

FIXTURES = Path(__file__).parent / "fixtures"
PMI_FIXTURE = FIXTURES / "grm03_thumbwheel_drive_screw_ap242_pmi.step"
PLAIN_FIXTURE = FIXTURES / "grm03_thumbwheel_drive_screw.step"
PMI_FIXTURE_SHA256 = "4b6462b9cc9f0d419250933bd77fb305f9cfebb7ec2b3f377008732876010a21"

# The manufacturing requirement whose 1.6 mm tapping drill becomes the hole callout that
# drives the scale/page escalation reported in #1336.
INTERNAL_THREAD_ID = "#2004"


def test_fixture_is_the_exact_file_the_pmi_acceptance_tests_pin():
    assert hashlib.sha256(PMI_FIXTURE.read_bytes()).hexdigest() == PMI_FIXTURE_SHA256


def test_fixture_carries_the_ap242_pmi_those_tests_read():
    report = extract_pmi_report(PMI_FIXTURE)

    assert report.error is None
    assert len(report.records) == 18
    assert Counter(record.source_category for record in report.records) == {
        "dimension": 10,
        "manufacturing_requirement": 8,
    }
    assert sorted(record.part21_id for record in report.records if record.part21_id) == [
        "#2000",
        "#2004",
        "#2008",
        "#2012",
        "#2016",
        "#2020",
        "#2024",
        "#2028",
    ]


def test_fixture_carries_the_internal_thread_behind_the_escalating_callout():
    report = extract_pmi_report(PMI_FIXTURE)

    threads = [record for record in report.records if record.part21_id == INTERNAL_THREAD_ID]
    assert [record.kind for record in threads] == ["internal_thread"]
    assert "DIA 1.6 tapping drill" in threads[0].label

    assert [
        record.label
        for record in report.records
        if record.source_category == "dimension" and record.value == 1.6
    ] == ["ø1.6"]


def test_pmi_fixture_is_a_different_solid_from_the_plain_grm03_fixture():
    # Stated so no test treats the pair as a PMI on/off comparison of one part: the PMI
    # fixture is a later revision, 5.2 mm longer with two more faces.
    assert extract_pmi_report(PLAIN_FIXTURE).records == ()

    with_pmi = _import_step(str(PMI_FIXTURE))
    without_pmi = _import_step(str(PLAIN_FIXTURE))

    assert (round(with_pmi.bounding_box().size.X, 4), len(with_pmi.faces())) == (28.7, 16)
    assert (round(without_pmi.bounding_box().size.X, 4), len(without_pmi.faces())) == (23.5, 14)
