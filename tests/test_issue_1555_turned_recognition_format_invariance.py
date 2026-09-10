"""The same solid must recognise the same turned profile in either STEP encoding (#1555).

quiddity 0.2.6 elected a turned profile's axis from a vote that unsupported bands could join.
On NIST CTC-05 a ⌀2.833 blend was among the voters, and the two encodings of the *same part*
disagreed about the answer:

    ap203  3 steps on x, including ⌀2.833      ap242  9 steps on x

0.2.7 filters bands to those with more than half a turn of circumferential support *before* the
vote, and both encodings now report the same five steps on z. That agreement is the observable
consequence of the fix, and nothing else in the suite pins it: CTC-05's other guard
(`test_issue_798_silhouette_lint`) asserts a callout FLOOR of 18, which a wrong-but-plentiful
inventory would satisfy.

Recognition only, not a drawing build — `inspect_step` is ~2-3 s per fixture against ~60 s for a
dense CTC build. Still slow-tier and one pair per test, because the fast tier is measured on
ubuntu at 5-7x macOS and #656's note records what putting a CTC fixture in it cost.
"""

from __future__ import annotations

import pytest

from draftwright.inspection import inspect_step

#: The five NIST parts that ship in both encodings. Four legitimately recognise no turned
#: profile at all; they are here because the property is "the encodings agree", and an encoding
#: that invented a profile on a prismatic part would be exactly the #1555 defect returning.
PAIRS = ("01", "02", "03", "04", "05")

#: CTC-05's profile, pinned exactly. This is the non-vacuous half: the four cases above agree by
#: both being empty, so without this the module could pass while recognising nothing anywhere.
#: A provider change that alters these values should fail here and be updated deliberately.
CTC05_STEPS = (
    ("z", 63.5, 279.4, 482.6),
    ("z", 304.8, 25.4, 45.72),
    ("z", 304.8, 45.72, 54.61),
    ("z", 304.8, 54.61, 127.0),
    ("z", 558.8, 0.0, 25.4),
)


def _turned_steps(stem: str) -> list[tuple[str, float, float, float]]:
    document = inspect_step(f"tests/fixtures/{stem}.stp")
    return sorted(
        (
            found["feature"]["axis"],
            round(found["feature"]["diameter"], 3),
            round(found["feature"]["lo"], 3),
            round(found["feature"]["hi"], 3),
        )
        for found in document["found"]
        if found["family"] == "turned_steps"
    )


@pytest.mark.slow
@pytest.mark.parametrize("part", PAIRS)
def test_both_encodings_recognise_the_same_turned_profile(part):
    ap203 = _turned_steps(f"nist_ctc_{part}_asme1_ap203")
    ap242 = _turned_steps(f"nist_ctc_{part}_asme1_ap242")
    assert ap203 == ap242, (
        f"CTC-{part} recognises a different turned profile in each encoding of the same solid; "
        "that is the #1555 axis-election defect returning"
    )


@pytest.mark.slow
@pytest.mark.parametrize("encoding", ["ap203", "ap242"])
def test_ctc05_recognises_its_actual_turned_profile(encoding):
    """The agreement above is only worth something if what both agree on is right.

    ⌀2.833 is the specific artefact 0.2.6 drew on the ap203 sheet: a blend read as a turned
    diameter. Asserting the exact set covers it without inventing a size threshold.
    """
    steps = _turned_steps(f"nist_ctc_05_asme1_{encoding}")
    assert tuple(steps) == CTC05_STEPS
    assert not [s for s in steps if s[1] < 10.0], "a blend-sized diameter is back in the profile"
