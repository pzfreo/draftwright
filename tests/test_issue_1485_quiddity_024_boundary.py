"""Retain 0.2.4's observed two-plane ends without inventing uniform passage depth."""

import json
from copy import deepcopy

import pytest
from build123d import Box, Keep, Plane, Pos, RegularPolygon, Rot, extrude
from quiddity import PlanarEndTerm

from draftwright import build_drawing
from draftwright.section_recess_contract import UnsupportedSectionRecess, section_recess_fields


@pytest.fixture(scope="module", params=[False, True], ids=["high-roof", "reversed-roof"])
def roof_drawing(request):
    stock = Box(40, 40, 40).split(Plane(origin=(0, 0, 20), z_dir=(0.2, 0, 1)), Keep.BOTTOM)
    part = stock - Pos(0, 0, -25) * extrude(RegularPolygon(3, 4), 50)
    assert stock.volume - part.volume == pytest.approx(718.2)
    drawing = build_drawing(Rot(180, 0, 0) * part if request.param else part)
    (source,) = drawing.recognition().section_recesses
    assert source.classification.feature_kind == "passage"
    ends = (source.geometry.ends.low, source.geometry.ends.high)
    (roof,) = [end.surface for end in ends if end.surface.type == "plane_envelope"]
    assert len(roof.terms) == 2 and roof.terms[0].gradient != roof.terms[1].gradient
    assert not drawing.recognition().section_recess_refusals
    return drawing, source


def test_two_plane_passage_retains_its_source_and_explicit_unsupported_outcome(roof_drawing):
    drawing, source = roof_drawing
    with pytest.raises(UnsupportedSectionRecess, match="no supported drafting grammar"):
        section_recess_fields(source)
    ownership = drawing.recognition_ownership()
    evidence = ownership.evidence
    (ref,) = [ref for ref in evidence.features if evidence.family(ref) == "section_recesses"]
    assert evidence.record(ref) is source
    assert ownership.binding_for(ref) is None and ownership.status(ref) == "unsupported"
    report = drawing.report()["recognition"]
    (row,) = [row for row in report["occurrences"] if row["family"] == "section_recesses"]
    assert row["record"] == json.loads(json.dumps(source.to_dict()))
    assert row["disposition"] == "unsupported" and row["owners"] == []
    (requirement,) = [r for r in report["requirements"] if r["family"] == "section_recesses"]
    assert requirement["occurrence_ids"] == [row["id"]]
    assert requirement["state"] == "unsupported" and requirement["annotations"] == []
    assert any(issue.code == "passage_requirement_unsupported" for issue in drawing.lint())


@pytest.mark.parametrize("fault", ["nonfinite", "foreign_term"])
def test_invalid_roof_terms_cannot_hide_behind_an_unsupported_grammar(roof_drawing, fault):
    _, source = roof_drawing
    altered = deepcopy(source)
    (roof,) = [
        end.surface
        for end in (altered.geometry.ends.low, altered.geometry.ends.high)
        if end.surface.type == "plane_envelope"
    ]
    # Simulate a corrupted aggregate after construction. Validation must precede the
    # unsupported-grammar outcome, so invalid source evidence is never certified.
    if fault == "nonfinite":
        object.__setattr__(roof.terms[0], "height", float("nan"))
        error, match = ValueError, "finite"
    else:

        class ForeignTerm(PlanarEndTerm):
            pass

        term = roof.terms[0]
        object.__setattr__(roof, "terms", (ForeignTerm(term.height, term.gradient), roof.terms[1]))
        error, match = TypeError, "exact public"
    with pytest.raises(error, match=match):
        section_recess_fields(altered)
