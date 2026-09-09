"""Execute the section examples users and agents actually read (#1532)."""

import re
import warnings
from pathlib import Path

import pytest
from build123d import Box

from draftwright import Sheet, SoftDeprecationWarning

_ROOT = Path(__file__).parents[1]
_DOCUMENTS = ("skills/SKILL.md", "docs/reference/sheet.md")


def _example(document, name):
    text = (_ROOT / document).read_text(encoding="utf-8")
    matches = re.findall(
        rf"<!-- example: {re.escape(name)} -->\n```python\n(.*?)\n```", text, re.DOTALL
    )
    assert len(matches) == 1, f"expected one executable {name} example in {document}"
    return matches[0]


def _run(document, name):
    namespace = {}
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        exec(compile(_example(document, name), document, "exec"), namespace)
    return namespace, recorded


@pytest.mark.parametrize("document", _DOCUMENTS)
def test_documented_authored_section_preserves_intent_and_reports_omissions(document):
    result, recorded = _run(document, "authored-section")
    assert not recorded, [(type(w.message).__name__, str(w.message)) for w in recorded]
    sheet, drawing = result["sheet"], result["drawing"]
    assert sheet.view_constraints.principal_source == "authored"
    assert sheet.view_constraints.derived_source == "authored"
    assert set(drawing.views) == {"front", "plan", "side", "section_aa"}
    assert drawing.section_decision["status"] == "placed"
    assert "section_hatch" in drawing.annotations()
    assert "bore.diameter" in result["bore"].dimension_ids()
    dimensions = drawing.model().authored_dimensions
    assert dimensions is not None
    assert {dimension.role for dimension in dimensions} == {
        "width.length",
        "height.length",
        "depth.length",
        "bore.diameter",
    }
    bore = next(f for f in drawing.model().features if f.kind == "hole")
    assert bore.diameter == 6 and bore.through
    assert any(
        measurement.feature is bore and measurement.parameter == "bore.diameter"
        for name in drawing.annotations_of(bore)
        for measurement in drawing.registry.measurement_of(name)
    )
    issues = result["issues"]
    assert {i.code for i in issues} == {"feature_not_located", "hole_requirement_suppressed"}
    assert {
        parameter
        for i in issues
        for feature, parameter in i.hole_requirement_ids
        if feature is bore
    } == {"location.location.x", "location.location.y"}
    assert all(i.severity == "warning" for i in issues)


def test_documented_automatic_augmentation_keeps_automatic_views_and_supported_notice():
    result, recorded = _run("docs/reference/sheet.md", "automatic-section")
    assert len(recorded) == 1
    assert recorded[0].category is SoftDeprecationWarning
    assert "NOT scheduled for removal" in str(recorded[0].message)
    sheet, drawing = result["sheet"], result["drawing"]
    assert sheet.view_constraints.principal_source == "automatic"
    assert sheet.view_constraints.derived_source == "automatic"
    assert set(drawing.views) == {"front", "plan", "side", "iso", "section_aa"}
    assert drawing.section_decision["status"] == "placed"
    assert "section_hatch" in drawing.annotations()
    assert drawing.model().authored_dimensions is not None
    assert result["issues"] == []


@pytest.mark.parametrize("verb", ["section", "detail"])
@pytest.mark.parametrize("source", ["principal", "derived"])
def test_augmentation_on_authored_views_points_back_to_authored_verb(verb, source):
    sheet = Sheet(Box(60, 40, 12)).authored_dimensions()
    if source == "principal":
        sheet.authored_views().view("front")
    if verb == "section":
        sheet.add_section_view("A", at=0)
    else:
        envelope = sheet.envelope()
        sheet.add_detail_view("A", around=envelope)
    if source == "derived":
        # An addition may precede the authored declaration: preserve that ordering
        # so the diagnostic must inspect the derived source as well as principals.
        sheet.section_view("B", at=1)
    with pytest.raises(ValueError, match=r"use section_view\(\)/detail_view\(\)") as caught:
        sheet.build()
    assert "call auto_views() first" not in str(caught.value)


def test_legacy_section_warning_names_both_migrations():
    sheet = Sheet(Box(60, 40, 12)).authored_dimensions()
    with pytest.warns(DeprecationWarning) as recorded:
        sheet.section(at=0)
    assert len(recorded) == 1
    message = str(recorded[0].message)
    assert "for authored derived views with authored dimensions use section_view" in message
    assert "augment automatic views" in message and "add_section_view" in message
    assert "Removal target 0.6.0" in message


def test_legacy_and_authored_section_conflict_does_not_send_user_to_automatic_views():
    sheet = Sheet(Box(60, 40, 12)).authored_dimensions()
    with pytest.warns(DeprecationWarning):
        sheet.section(at=0)
    sheet.section_view("A", at=0)
    with pytest.raises(ValueError, match="remove the legacy call") as caught:
        sheet.build()
    assert "keep section_view() for authored" in str(caught.value)
