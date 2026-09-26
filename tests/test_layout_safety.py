"""Independent candidate safety checks retain failures without a baseline."""

from types import SimpleNamespace

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
        model=None,
        registry=None,
    ):
        self._report = report
        self._features = features
        self._bounds = bounds
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


def _raw_report(*, requirements=(), total=0, issues=()):
    return {
        "schema_version": 3,
        "recognition": {
            "summary": {"total": total, "unexpectedly_missing": 0},
            "requirements": list(requirements),
        },
        "lint": {
            "issues": list(issues),
            "quality": {"completeness": {"missing": 0, "dropped": 0}},
        },
    }


def test_clean_candidate_needs_no_baseline_for_safety_evidence():
    verdict = candidate_safety_evidence(DrawingStub(_raw_report()))

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


def test_accepted_geometry_without_model_or_requirements_is_not_clean():
    verdict = candidate_safety_evidence(DrawingStub(_raw_report(total=1), features=0))

    assert "recognized_inventory" in verdict["failed_checks"]


def test_empty_raw_recognition_and_empty_model_cannot_claim_a_clean_inventory():
    verdict = candidate_safety_evidence(DrawingStub(_raw_report(), features=0))

    assert "recognized_inventory" in verdict["failed_checks"]


def test_unavailable_report_and_tiny_view_fail_closed():
    verdict = candidate_safety_evidence(
        DrawingStub(ReportUnavailableError("ownership unavailable"), bounds=(0, 0, 5, 5))
    )

    assert "report_available" in verdict["failed_checks"]
    assert "minimum_view_area" in verdict["failed_checks"]


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
