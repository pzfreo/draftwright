"""A fixed semantic denominator for the restored five-pocket tuner STEP fixture.

Selected once in PR CI, and retained in the post-merge integration tier. The
expected geometry and measurements are fixture facts, not a count of whatever
the current detector happened to return. Export success alone cannot pass this.
"""

import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from time import monotonic

import ezdxf
import pytest

from draftwright import build_drawing

pytestmark = [pytest.mark.slow, pytest.mark.real_part_canary, pytest.mark.timeout(120)]
FIXTURE = Path(__file__).parent / "fixtures/tuner_jig_blind_obround_pockets.step"
CENTRES_Y = (21.2, 48.4, 75.6, 102.8, 130.0)


def test_tuner_retains_occurrences_and_measurements_through_export(tmp_path):
    start = monotonic()
    drawing = build_drawing(FIXTURE)
    (pattern,) = [f for f in drawing.model().features if f.kind == "pocket_pattern"]
    assert pattern.count == 5 and len(pattern.members) == 5
    assert sorted(point[1] for point in pattern.members) == pytest.approx(CENTRES_Y)
    assert all((point[0], point[2]) == pytest.approx((-5, 8.5)) for point in pattern.members)
    assert (
        pattern.member.width,
        pattern.member.length,
        pattern.member.depth,
        pattern.member.corner_radius,
        pattern.pitch,
    ) == pytest.approx((7.9, 13.6, 19, 3.94, 27.2))

    sources = drawing.recognition().section_recesses
    assert len(sources) == 5 and drawing.recognition().section_recess_refusals == ()
    ownership = drawing.recognition_ownership()
    refs = [
        ref
        for ref in ownership.evidence.features
        if any(ownership.evidence.record(ref) is source for source in sources)
    ]
    assert len(refs) == 5
    bindings = [ownership.binding_for(ref) for ref in refs]
    assert all(binding is not None and binding.feature is pattern for binding in bindings)
    assert {binding.member_index for binding in bindings} == set(range(5))
    for ref, binding in zip(refs, bindings, strict=True):
        source = ownership.evidence.record(ref)
        assert pattern.members[binding.member_index][1] == pytest.approx(
            source.geometry.frame.origin[1]
        )

    snapshot = drawing.measurement_snapshot()
    assert not snapshot.unknown
    for parameter, value in [
        ("pocket_width.length", 7.9),
        ("pocket_length.length", 13.6),
        ("pocket_depth.length", 19),
        ("pitch.length", 27.2),
    ]:
        claims = [c for c in snapshot.claims if c.owner is pattern and c.parameter == parameter]
        assert len(claims) == 1, ("missing critical pattern measurement", parameter)
        assert len(claims[0].meaning) == 1 and claims[0].meaning[0][0] == pytest.approx(value)
        expected_text = "4× 27.2" if parameter == "pitch.length" else "5× 7.9 × 13.6 × 19 DEEP"
        assert claims[0].rendered[0] == expected_text

    locations = [
        c
        for c in snapshot.claims
        if c.owner is pattern and c.parameter == "location_pocket_pattern.location"
    ]
    assert len(locations) == 2
    expected = {
        "location_pocket_pattern.location.x": 10.0,
        "location_pocket_pattern.location.y": 83.6,
    }
    observed = set()
    for claim in locations:
        _, components = claim.witnesses
        assert len(components) == 1
        component, at = components[0]
        assert component in expected and component not in observed
        observed.add(component)
        assert at == pytest.approx((-5, 75.6, 8.5))
        assert float(claim.rendered[0]) == pytest.approx(expected[component]), (
            "location value substituted",
            component,
        )
        assert claim.rendered[2] == pytest.approx(expected[component]), (
            "location path substituted",
            component,
        )
    assert observed == expected.keys()

    # Every rounded corner also needs its radius; losing the whole family must
    # fail even if recognition-relative completeness and lint both look clean.
    blends = [f for f in drawing.model().features if f.kind == "blend"]
    assert len(blends) == 20 and all(f.radius == pytest.approx(3.94) for f in blends)
    for blend in blends:
        (claim,) = [
            c for c in snapshot.claims if c.owner is blend and c.parameter == "blend.radius"
        ]
        assert claim.meaning[0][0] == pytest.approx(3.94)
        assert claim.rendered[0] == "20× R3.9"

    report = drawing.report()["recognition"]
    occurrences = [r for r in report["occurrences"] if r["family"] == "section_recesses"]
    assert len(occurrences) == 5
    assert sorted(
        r["record"]["geometry"]["frame"]["origin"][1] for r in occurrences
    ) == pytest.approx(CENTRES_Y)
    ids = {r["id"] for r in occurrences}
    assert len(ids) == 5
    assert all(
        r["disposition"] == "absorbed" and r["reason_code"] == "pocket_pattern_member"
        for r in occurrences
    )
    requirements = [r for r in report["requirements"] if r["family"] == "pocket_patterns"]
    assert len(requirements) == 7
    assert {r["parameter_id"] for r in requirements} == {
        "grouping.count",
        "pocket_width.length",
        "pocket_length.length",
        "pocket_depth.length",
        "pitch.length",
        "location_pocket_pattern.location.x",
        "location_pocket_pattern.location.y",
    }
    assert all(r["state"] == "placed" and set(r["occurrence_ids"]) == ids for r in requirements)
    assert Counter(issue.code for issue in drawing.lint()) == {"step_dim_withheld": 1}

    paths = drawing.export(str(tmp_path / "tuner"), formats=("svg", "dxf"))
    svg = ET.parse(paths["svg"]).getroot()
    assert svg.tag == "{http://www.w3.org/2000/svg}svg"
    assert svg.findall(".//{http://www.w3.org/2000/svg}path")
    dxf = ezdxf.readfile(paths["dxf"])
    assert not dxf.audit().has_errors and len(dxf.modelspace()) > 0
    print(
        f"Tuner real-part canary: {monotonic() - start:.2f}s for build, semantic audit and exports"
    )
