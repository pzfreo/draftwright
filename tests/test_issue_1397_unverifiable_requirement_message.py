"""#1397 — an `unverifiable` finding must not print `?` where a requirement id belongs.

The message read

    hole at (12.0, 7.0, 0.0) requirement ? cannot be joined to measurement provenance
    without guessing

which is a diagnostic about guessing that requires one. The issue guessed the cause was a
formatter reached with an unresolved id. It is not: `unverifiable` is recorded exactly when
a recognised source matched **no** IR feature, so `_parameter_ids` was never called and no
per-requirement id exists to print. `requirement_count` already carried the truth — how many
physical requirements the recognised source has, all of them unattributable — and only the
message threw it away.

So the fix names the count instead of inventing ids: `all 4 physical requirements, which no
IR feature claimed, cannot be joined ...`. Nine coverage modules printed the same sentinel;
`turned_step_coverage` records it in its ledger but builds no message from it, so it is
deliberately untouched.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import build_drawing
from draftwright.builder import detect_part_model
from draftwright.linting.issues import UNJOINED_PARAMETER_ID, requirement_subject

_XYZ_MIN = (None, None, None)


def _uncertifiable_hole_drawing():
    """A circular hole declared as a `double_d` profile: recognised, but joinable to nothing.

    This is the fixture `test_declared_structural_profile_cannot_certify_a_circular_hole`
    already relies on, reused because it is the narrowest way to reach the state — exactly
    one recognised source, exactly one `unverifiable` outcome, four physical requirements.
    """

    from build123d import Align

    align = (Align.MIN, Align.MIN, Align.MIN)
    part = Box(80, 50, 10, align=align) - Pos(12, 7, 0) * Cylinder(4, 10, align=align)
    detected = detect_part_model(part)
    circular = next(feature for feature in detected.features if feature.kind == "hole")
    profiled = replace(
        circular, profile="double_d", across_flats=3.0, profile_direction=(1.0, 0.0, 0.0)
    )
    declared = replace(
        detected,
        features=[profiled if feature is circular else feature for feature in detected.features],
    )
    return build_drawing(part, model=declared)


@pytest.fixture(scope="module")
def unverifiable_drawing():
    return _uncertifiable_hole_drawing()


def test_the_defect_state_is_actually_reached(unverifiable_drawing):
    """Precondition: the ledger really does hold the sentinel, with a count above one.

    Without this the message assertions below could pass on a drawing that never produced an
    unverifiable outcome at all — the failure mode CLAUDE.md names, and the reason a message
    test that only greps for absence of `?` would be worthless.
    """

    from draftwright.linting.hole_coverage import hole_requirement_outcomes
    from draftwright.model.compiled import compile_dimensions

    unverifiable_drawing.lint()
    outcomes = hole_requirement_outcomes(
        unverifiable_drawing.recognition(),
        unverifiable_drawing.model().features,
        unverifiable_drawing.registry,
        compile_dimensions(unverifiable_drawing.model()).diagnostics,
    )
    unverifiable = [outcome for outcome in outcomes if outcome.state == "unverifiable"]
    assert [(outcome.parameter_id, outcome.requirement_count) for outcome in unverifiable] == [
        (UNJOINED_PARAMETER_ID, 4)
    ]


def test_the_finding_names_the_requirements_instead_of_printing_a_question_mark(
    unverifiable_drawing,
):
    findings = [
        issue for issue in unverifiable_drawing.lint() if issue.code.endswith("_unverifiable")
    ]
    assert findings, "fixture produced no unverifiable finding"
    for issue in findings:
        assert "requirement ?" not in issue.message, issue.message
        assert "measurement ?" not in issue.message, issue.message
    assert any("all 4 physical requirements" in issue.message for issue in findings), [
        issue.message for issue in findings
    ]
    assert any("no IR feature claimed" in issue.message for issue in findings)


def test_a_joined_requirement_still_prints_its_own_id(unverifiable_drawing):
    """The converse. A message naming nothing specific would also pass the test above.

    The first version of this test built a second drawing and looped over its findings. That
    drawing lints CLEAN — `named` was empty, so the loop body ran zero times and the whole
    OCC build bought nothing. It is replaced by an assertion over the ledger the fixture
    already has, where a joined outcome is guaranteed to exist.
    """

    from draftwright.linting.hole_coverage import hole_requirement_outcomes
    from draftwright.model.compiled import compile_dimensions

    unverifiable_drawing.lint()
    outcomes = hole_requirement_outcomes(
        unverifiable_drawing.recognition(),
        unverifiable_drawing.model().features,
        unverifiable_drawing.registry,
        compile_dimensions(unverifiable_drawing.model()).diagnostics,
    )
    joined = [o for o in outcomes if o.parameter_id != UNJOINED_PARAMETER_ID]
    # Not a precondition this fixture satisfies — it is wholly unverifiable — so build the
    # one case directly rather than pretend a corpus supplies it.
    assert not joined, "fixture changed: it is meant to produce only unjoined outcomes"

    class _Joined:
        parameter_id = "bore.diameter"
        requirement_count = 4

    assert requirement_subject(_Joined(), noun="requirement") == "requirement bore.diameter"
    assert requirement_subject(_Joined(), noun="measurement") == "measurement bore.diameter"


class _Outcome:
    def __init__(self, parameter_id, requirement_count, known=True):
        self.parameter_id = parameter_id
        self.requirement_count = requirement_count
        self.requirement_count_known = known


@pytest.mark.parametrize(
    ("outcome", "noun", "expected"),
    [
        (_Outcome("bore.depth", 1), "requirement", "requirement bore.depth"),
        (_Outcome("bore.depth", 9), "measurement", "measurement bore.depth"),
        (
            _Outcome("?", 1),
            "measurement",
            "its sole physical measurement, which no IR feature claimed,",
        ),
        (
            _Outcome("?", 6),
            "requirement",
            "all 6 physical requirements, which no IR feature claimed,",
        ),
        # Cardinality that cannot be known must not be printed as a number. `groove_coverage`'s
        # corrupt-inventory path stores `requirement_count=1` to keep the denominator honest,
        # and this branch stops the message reading "its sole physical measurement" for an
        # inventory whose size is exactly what could not be established.
        (
            _Outcome("?", 1, known=False),
            "measurement",
            "its physical measurements, of unknown number, which no IR feature claimed,",
        ),
    ],
)
def test_requirement_subject_table(outcome, noun, expected):
    assert requirement_subject(outcome, noun=noun) == expected


def test_the_groove_corrupt_inventory_path_does_not_invent_a_count():
    """#1469 review: the first version of this fix printed a false number here.

    `groove_coverage` returns ONE aggregate outcome when `recognition.grooves` cannot be
    iterated, with `requirement_count=1` meaning "one aggregate", not "one measurement" — a
    groove carries two (`groove.length`, `groove.diameter`), which is why the module's three
    sibling sentinel sites pass 2. Naming "1" was inventing the very cardinality the path
    exists to refuse to invent.
    """

    from dataclasses import fields

    from b123d_recognisers import RecognitionResult

    from draftwright.linting.groove_coverage import groove_requirement_outcomes

    class _Uniterable:
        """Not a tuple, and iterating it raises — the corrupt-inventory contract path."""

        def __iter__(self):
            raise RuntimeError("inventory is not iterable")

    empty = {
        field.name: False if field.name == "rotational" else ()
        for field in fields(RecognitionResult)
    }
    corrupt = RecognitionResult(**{**empty, "grooves": _Uniterable()})
    outcomes = groove_requirement_outcomes(corrupt, (), None, ())
    assert [
        (o.parameter_id, o.requirement_count, o.requirement_count_known) for o in outcomes
    ] == [(UNJOINED_PARAMETER_ID, 1, False)]
    subject = requirement_subject(outcomes[0], noun="measurement")
    assert subject == "its physical measurements, of unknown number, which no IR feature claimed,"
    assert "sole" not in subject and " 1 " not in subject


def test_no_module_can_both_record_the_sentinel_and_print_it_raw():
    """The pairing that IS the defect: a module that mints the sentinel must not print it.

    Nine copies of one sentence produced nine copies of one defect, so the guard has to see a
    tenth. It is deliberately not "no module may interpolate `parameter_id`": six coverage
    modules do that and are correct, because none of them ever records the sentinel — their
    outcomes always carry a real id. Converting those would be churn against no defect.

    The predicate keys on the **imported constant**, not on the `"?"` literal. Keying on the
    literal — which is what the first version did — meant a module that correctly imported
    `UNJOINED_PARAMETER_ID` dropped out of the minting set and could then interpolate
    `parameter_id` raw undetected. The guard rewarded the worse spelling.
    """

    from pathlib import Path

    linting = Path(__file__).resolve().parent.parent / "src" / "draftwright" / "linting"
    sources = {
        path.name: path.read_text(encoding="utf-8") for path in linting.glob("*_coverage.py")
    }
    mints = sorted(
        name
        for name, text in sources.items()
        if "UNJOINED_PARAMETER_ID" in text or '"' + UNJOINED_PARAMETER_ID + '"' in text
    )
    prints_raw = sorted(name for name, text in sources.items() if "{outcome.parameter_id}" in text)
    assert not set(mints) & set(prints_raw), sorted(set(mints) & set(prints_raw))

    # Both halves must be able to fire, or the intersection above is empty for the wrong
    # reason. These are exact counts, not floors: a floor of ">= 9" stayed green when one
    # module stopped minting, and ">= 6" would have forbidden legitimately converting one of
    # the six. If either number changes, this test should be read, not bumped.
    assert len(mints) == 10, mints
    assert len(prints_raw) == 6, prints_raw
    assert "turned_step_coverage.py" in mints, "the one minting module that builds no message"
    assert "hole_coverage.py" in mints and "hole_coverage.py" not in prints_raw

    # And that the nine really were rewired, rather than the sentinel having vanished.
    assert "requirement_subject(outcome" in sources["hole_coverage.py"]
