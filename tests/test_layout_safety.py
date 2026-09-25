"""Independent candidate safety checks retain failures without a baseline."""

from types import SimpleNamespace

import pytest
from build123d import Box, Cylinder

from draftwright import Sheet
from draftwright.layout_safety import candidate_safety_evidence
from draftwright.reporting import ReportUnavailableError


class DrawingStub:
    page_w = 297.0
    page_h = 210.0
    scale = 1.0
    views = {"front": object()}

    def __init__(
        self,
        report,
        *,
        features=1,
        bounds=(0.0, 0.0, 20.0, 20.0),
        items=(),
        model=None,
        registry=None,
    ):
        self._report = report
        self._features = features
        self._bounds = bounds
        self.items = list(items)
        self.box_cache = {}
        self._model = model
        self.registry = registry or SimpleNamespace(names=lambda: ())

    def model(self):
        return self._model or SimpleNamespace(
            features=[object()] * self._features,
            authored_dimensions=None,
            requested_dimensions=(),
            schedules=(),
        )

    def report(self):
        if isinstance(self._report, Exception):
            raise self._report
        return self._report

    def view_bounds(self, _name):
        return self._bounds


def _raw_report(*, requirements=(), total=0, issues=(), occurrences=None):
    ledger = []
    for index, requirement in enumerate(requirements):
        if isinstance(requirement, dict):
            requirement = {
                "id": f"requirement:{index + 1}",
                "occurrence_ids": [f"hole:{index + 1}"] if index < total else [],
                **requirement,
            }
        ledger.append(requirement)
    if occurrences is None:
        occurrences = []
        for index in range(total):
            occurrence_id = f"hole:{index + 1}"
            linked = [
                row["id"]
                for row in ledger
                if isinstance(row, dict) and occurrence_id in row["occurrence_ids"]
            ]
            occurrences.append(
                {
                    "id": occurrence_id,
                    "disposition": "represented",
                    "requirements": {
                        "coverage": "ledger" if linked else "not-applicable",
                        "ids": linked,
                    },
                }
            )
    return {
        "schema_version": 3,
        "recognition": {
            "summary": {"total": total, "unexpectedly_missing": 0},
            "occurrences": occurrences,
            "requirements": ledger,
        },
        "lint": {
            "issues": list(issues),
            "quality": {"completeness": {"missing": 0, "dropped": 0}},
        },
    }


def test_clean_candidate_needs_no_baseline_for_safety_evidence():
    verdict = candidate_safety_evidence(
        DrawingStub(_raw_report(total=1, requirements=({"state": "placed"},)))
    )

    assert verdict["checks_passed"] is True
    assert verdict["admission_ready"] is False
    assert verdict["failed_checks"] == []
    assert verdict["page"] == [297.0, 210.0]


def test_missing_requirement_and_overlap_are_independent_failures():
    verdict = candidate_safety_evidence(
        DrawingStub(
            _raw_report(
                total=1,
                requirements=({"state": "missing"},),
                issues=({"code": "annotation_overlap", "severity": "warning"},),
            )
        )
    )

    assert verdict["checks_passed"] is False
    assert "required_outcomes" in verdict["failed_checks"]
    assert "lint_blockers" in verdict["failed_checks"]


@pytest.mark.parametrize(
    ("state", "accepted"),
    [
        ("placed", True),
        ("satisfied_by_structured_note", True),
        ("inapplicable", True),
        ("suppressed", False),
        ("dropped", False),
        ("missing", False),
        ("unverifiable", False),
        ("unsupported", False),
        ("future_state", False),
        (None, False),
    ],
)
def test_raw_requirement_states_fail_closed(state, accepted):
    report = _raw_report(total=1, requirements=({"state": state},))

    verdict = candidate_safety_evidence(DrawingStub(report))

    assert ("required_outcomes" not in verdict["failed_checks"]) is accepted


@pytest.mark.parametrize("requirement", [None, {}, {"state": []}])
def test_malformed_raw_requirement_fails_closed(requirement):
    report = _raw_report(total=1, requirements=(requirement,))

    verdict = candidate_safety_evidence(DrawingStub(report))

    assert "required_outcomes" in verdict["failed_checks"]


@pytest.mark.parametrize(
    "disposition",
    ["unsupported", "deferred", "evidence_only", "unexpectedly_missing", "future_state", None],
)
def test_recognized_occurrence_disposition_fails_independently(disposition):
    report = _raw_report(total=1, requirements=({"state": "placed"},))
    report["recognition"]["occurrences"][0]["disposition"] = disposition

    verdict = candidate_safety_evidence(DrawingStub(report))

    assert "required_outcomes" not in verdict["failed_checks"]
    assert "recognized_occurrences" in verdict["failed_checks"]


@pytest.mark.parametrize("coverage", ["not-projected", "deferred", "unavailable", None])
def test_recognized_occurrence_coverage_fails_independently(coverage):
    report = _raw_report(total=1, requirements=({"state": "placed"},))
    report["recognition"]["occurrences"][0]["requirements"]["coverage"] = coverage

    verdict = candidate_safety_evidence(DrawingStub(report))

    assert "recognized_occurrences" in verdict["failed_checks"]


def test_recognized_occurrence_inventory_and_ledger_ids_fail_closed():
    report = _raw_report(total=1, requirements=({"state": "placed"},))
    report["recognition"].pop("occurrences")
    assert (
        "recognized_occurrences" in candidate_safety_evidence(DrawingStub(report))["failed_checks"]
    )

    report["recognition"]["occurrences"] = []
    assert (
        "recognized_occurrences" in candidate_safety_evidence(DrawingStub(report))["failed_checks"]
    )

    report["recognition"]["occurrences"] = [None]
    assert (
        "recognized_occurrences" in candidate_safety_evidence(DrawingStub(report))["failed_checks"]
    )

    report["recognition"]["occurrences"] = [
        {
            "id": "hole:1",
            "disposition": "represented",
            "requirements": {"coverage": "ledger", "ids": []},
        }
    ]
    assert (
        "recognized_occurrences" in candidate_safety_evidence(DrawingStub(report))["failed_checks"]
    )


def test_absorbed_occurrence_with_a_requirement_ledger_is_not_adverse():
    report = _raw_report(total=1, requirements=({"state": "placed"},))
    report["recognition"]["occurrences"][0]["disposition"] = "absorbed"

    verdict = candidate_safety_evidence(DrawingStub(report))

    assert "recognized_occurrences" not in verdict["failed_checks"]


def test_occurrence_with_unknown_requirement_id_fails_even_when_aggregate_is_placed():
    report = _raw_report(
        total=1,
        requirements=({"id": "requirement:1", "state": "placed", "occurrence_ids": ["hole:1"]},),
    )
    report["recognition"]["occurrences"][0]["requirements"]["ids"] = ["requirement:missing"]

    verdict = candidate_safety_evidence(DrawingStub(report))

    assert "required_outcomes" not in verdict["failed_checks"]
    assert "recognized_occurrences" in verdict["failed_checks"]


def test_occurrence_link_requires_a_unique_bidirectional_requirement():
    report = _raw_report(total=1, requirements=({"state": "placed"},))
    requirement = report["recognition"]["requirements"][0]
    requirement["occurrence_ids"] = []
    assert (
        "recognized_occurrences" in candidate_safety_evidence(DrawingStub(report))["failed_checks"]
    )

    requirement["occurrence_ids"] = ["hole:1"]
    report["recognition"]["requirements"].append(dict(requirement))
    verdict = candidate_safety_evidence(DrawingStub(report))
    assert "recognized_occurrences" in verdict["failed_checks"]
    assert any(
        item["detail"]
        and any(gap.get("reason") == "duplicate_requirement_id" for gap in item["detail"])
        for item in verdict["checks"]
        if item["name"] == "recognized_occurrences"
    )


def test_occurrence_link_requires_a_requirement_ledger_inventory():
    report = _raw_report(total=1, requirements=({"state": "placed"},))
    report["recognition"]["requirements"] = None

    assert (
        "recognized_occurrences" in candidate_safety_evidence(DrawingStub(report))["failed_checks"]
    )


@pytest.mark.parametrize("identities", [[None], ["hole:1", "hole:1"]])
def test_occurrence_link_requires_unique_nonempty_occurrence_ids(identities):
    report = _raw_report(
        total=len(identities),
        requirements=({"state": "placed", "occurrence_ids": ["hole:1"]},),
    )
    for occurrence, identity in zip(report["recognition"]["occurrences"], identities):
        occurrence["id"] = identity

    assert (
        "recognized_occurrences" in candidate_safety_evidence(DrawingStub(report))["failed_checks"]
    )


def test_accepted_geometry_without_model_or_requirements_is_not_clean():
    verdict = candidate_safety_evidence(DrawingStub(_raw_report(total=1), features=0))

    assert "recognized_inventory" in verdict["failed_checks"]


def test_empty_raw_recognition_and_empty_model_cannot_claim_a_clean_inventory():
    verdict = candidate_safety_evidence(DrawingStub(_raw_report(), features=0))

    assert "recognized_inventory" in verdict["failed_checks"]


def test_synthetic_model_feature_does_not_replace_raw_recognition_evidence():
    verdict = candidate_safety_evidence(DrawingStub(_raw_report(), features=1))

    assert "recognized_inventory" in verdict["failed_checks"]


def test_unavailable_report_and_tiny_view_fail_closed():
    verdict = candidate_safety_evidence(
        DrawingStub(ReportUnavailableError("ownership unavailable"), bounds=(0, 0, 5, 5))
    )

    assert "report_available" in verdict["failed_checks"]
    assert "minimum_view_area" in verdict["failed_checks"]


@pytest.mark.parametrize(
    "bounds",
    [
        (0.0, 205.0, 20.0, 225.0),
        (-5.0, 0.0, 20.0, 20.0),
        (280.0, 0.0, 310.0, 20.0),
        (float("nan"), 0.0, 20.0, 20.0),
    ],
)
def test_off_page_view_fails_independently_of_clean_lint(bounds):
    drawing = DrawingStub(_raw_report(total=1, requirements=({"state": "placed"},)), bounds=bounds)

    verdict = candidate_safety_evidence(drawing)

    assert "lint_blockers" not in verdict["failed_checks"]
    assert "view_page_containment" in verdict["failed_checks"]
    detail = next(
        check["detail"] for check in verdict["checks"] if check["name"] == "view_page_containment"
    )
    assert "front" in detail


def test_view_page_containment_tolerates_submicron_rounding_at_edge():
    drawing = DrawingStub(
        _raw_report(total=1, requirements=({"state": "placed"},)),
        bounds=(0.0, 0.0, 297.0000005, 20.0),
    )

    assert "view_page_containment" not in candidate_safety_evidence(drawing)["failed_checks"]


@pytest.mark.parametrize("bounds", [None, (), (0.0, 0.0, 20.0), (0.0, 0.0, "bad", 20.0)])
def test_unavailable_view_bounds_fail_closed(bounds):
    drawing = DrawingStub(_raw_report(total=1, requirements=({"state": "placed"},)), bounds=bounds)

    verdict = candidate_safety_evidence(drawing)

    assert "view_page_containment" in verdict["failed_checks"]
    assert "minimum_view_area" in verdict["failed_checks"]
    detail = next(
        check["detail"] for check in verdict["checks"] if check["name"] == "view_page_containment"
    )
    assert detail["front"] == {"reason": "view_bounds_unavailable"}


class InkStub:
    def __init__(self, bounds=None):
        self.bounds = bounds
        self.calls = 0

    def bounding_box(self):
        self.calls += 1
        if self.bounds is None:
            raise ValueError("ink is unmeasurable")
        x0, y0, x1, y1 = self.bounds
        return SimpleNamespace(min=SimpleNamespace(X=x0, Y=y0), max=SimpleNamespace(X=x1, Y=y1))


@pytest.mark.parametrize(
    ("bounds", "reason"),
    [
        ((290.0, 10.0, 305.0, 20.0), "off_page_ink"),
        ((float("nan"), 10.0, 20.0, 20.0), "invalid_ink_bounds"),
        (None, "ink_bounds_unavailable"),
    ],
)
def test_annotation_page_containment_fails_with_clean_lint(bounds, reason):
    drawing = DrawingStub(
        _raw_report(total=1, requirements=({"state": "placed"},)), items=(InkStub(bounds),)
    )

    verdict = candidate_safety_evidence(drawing)

    assert "lint_blockers" not in verdict["failed_checks"]
    assert "annotation_page_containment" in verdict["failed_checks"]
    detail = next(
        check["detail"]
        for check in verdict["checks"]
        if check["name"] == "annotation_page_containment"
    )
    assert detail[0]["annotation"] == "anonymous[0]"
    assert detail[0]["reason"] == reason


def test_annotation_page_containment_uses_shared_box_cache():
    ink = InkStub((10.0, 10.0, 20.0, 20.0))
    drawing = DrawingStub(_raw_report(total=1, requirements=({"state": "placed"},)), items=(ink,))
    drawing.box_cache[id(ink)] = (ink, None, (10.0, 10.0, 20.0, 20.0))

    verdict = candidate_safety_evidence(drawing)

    assert "annotation_page_containment" not in verdict["failed_checks"]
    assert ink.calls == 0


def test_declared_inventory_mismatch_is_recorded():
    report = _raw_report()
    report["schema_version"] = 8
    report["declarations"] = {"feature_count": 2}

    verdict = candidate_safety_evidence(DrawingStub(report))

    assert "declared_inventory" in verdict["failed_checks"]


def test_missing_lint_evidence_is_a_failed_check():
    report = _raw_report()
    report.pop("lint")

    verdict = candidate_safety_evidence(DrawingStub(report))

    assert "lint_evidence" in verdict["failed_checks"]


def test_authored_dimension_requires_a_matching_rendered_claim():
    parameter = SimpleNamespace(parameter_id="bore.diameter", role="bore", discriminator=None)
    feature = SimpleNamespace(parameters=lambda: (parameter,))
    request = SimpleNamespace(feature=feature, role="bore.diameter", discriminator=None)
    model = SimpleNamespace(
        features=[feature],
        authored_dimensions=(request,),
        requested_dimensions=(),
        schedules=(),
    )
    registry = SimpleNamespace(
        names=lambda: {"diameter"},
        measurement_of=lambda _name: (),
        satisfaction_of=lambda _name: (),
    )
    drawing = DrawingStub(_raw_report(), model=model, registry=registry)

    missing = candidate_safety_evidence(drawing)
    assert "authored_dimensions" in missing["failed_checks"]

    registry.measurement_of = lambda _name: (
        SimpleNamespace(feature=feature, parameter="bore.diameter"),
    )
    present = candidate_safety_evidence(drawing)
    assert "authored_dimensions" not in present["failed_checks"]


def test_removing_an_authored_dimension_is_detected_on_a_real_sheet():
    part = Box(30, 20, 5) - Cylinder(2, 10)
    sheet = Sheet(part)
    hole = sheet.hole(diameter=4, at=(0, 0, 2.5), axis="z", depth=5)
    sheet.authored_dimensions()
    sheet.dimension(hole, "bore.diameter")
    drawing = sheet.build()

    assert "authored_dimensions" not in candidate_safety_evidence(drawing)["failed_checks"]
    name = next(name for name in drawing.registry.names() if drawing.registry.measurement_of(name))
    drawing.remove(name)

    assert "authored_dimensions" in candidate_safety_evidence(drawing)["failed_checks"]


def test_removing_a_declared_datum_is_detected_on_a_real_sheet():
    part = Box(30, 20, 5)
    sheet = Sheet(part).auto_dimensions()
    sheet.datum("A", part.faces().sort_by()[-1])
    drawing = sheet.build()

    evidence = candidate_safety_evidence(drawing)
    assert "declared_aspects" not in evidence["failed_checks"]
    name = next(name for name in drawing.registry.names() if drawing.registry.declaration_of(name))
    drawing.remove(name)

    assert "declared_aspects" in candidate_safety_evidence(drawing)["failed_checks"]


def test_document_wide_declarations_require_their_live_carriers():
    sheet = Sheet(Box(30, 20, 5))
    sheet.authored_dimensions()
    sheet.general_tolerance("ISO 2768-m")
    sheet.default_surface_finish("3.2")
    sheet.document_note("DATUM SCHEME A", kind="datum_scheme")
    drawing = sheet.build()

    def missing_kinds():
        evidence = candidate_safety_evidence(drawing)
        gaps = next(
            check["detail"] for check in evidence["checks"] if check["name"] == "declared_aspects"
        )
        return {gap["kind"] for gap in gaps}

    assert missing_kinds() == set()
    drawing.remove("title_block")
    assert missing_kinds() == {"general_tolerance"}
    drawing.remove("default_surface_finish")
    assert missing_kinds() == {"general_tolerance", "default_surface_finish"}
    drawing.remove("general_notes")
    assert missing_kinds() == {"general_tolerance", "default_surface_finish", "document_note"}


def test_equal_but_distinct_declaration_does_not_satisfy_an_authored_aspect():
    first = SimpleNamespace(kind="finish")
    second = SimpleNamespace(kind="finish")
    model = SimpleNamespace(
        features=[first], authored_dimensions=None, requested_dimensions=(), schedules=()
    )
    report = _raw_report()
    report["schema_version"] = 8
    report["declarations"] = {"feature_count": 1}
    registry = SimpleNamespace(
        names=lambda: {"finish"},
        declaration_of=lambda _name: second,
        feature_of=lambda _name: None,
        named=lambda _name: object(),
    )

    evidence = candidate_safety_evidence(DrawingStub(report, model=model, registry=registry))

    assert "declared_aspects" in evidence["failed_checks"]
    gap = next(
        check["detail"] for check in evidence["checks"] if check["name"] == "declared_aspects"
    )
    assert gap == [{"feature_index": 0, "kind": "finish", "reason": "representation_missing"}]


def test_equal_but_distinct_table_owner_does_not_satisfy_a_document_note():
    first = SimpleNamespace(kind="document_note")
    second = SimpleNamespace(kind="document_note")
    model = SimpleNamespace(
        features=[first], authored_dimensions=None, requested_dimensions=(), schedules=()
    )
    report = _raw_report()
    report["schema_version"] = 8
    report["declarations"] = {"feature_count": 1}
    registry = SimpleNamespace(
        names=lambda: {"general_notes"},
        declaration_of=lambda _name: None,
        feature_of=lambda _name: None,
        named=lambda _name: SimpleNamespace(source_features=(second,)),
    )

    evidence = candidate_safety_evidence(DrawingStub(report, model=model, registry=registry))

    gap = next(
        check["detail"] for check in evidence["checks"] if check["name"] == "declared_aspects"
    )
    assert gap == [
        {"feature_index": 0, "kind": "document_note", "reason": "representation_missing"}
    ]
