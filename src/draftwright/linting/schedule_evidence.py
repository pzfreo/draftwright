"""Verified cell evidence for the existing physical requirement producers.

This is a read-local registry projection, not a requirement inventory or a renderer.
Only successfully registered cells whose actual content agrees with their compiler
approval can expose measurements or physical riders. Original drawing state is untouched.
"""

from __future__ import annotations

from draftwright.linting.evidence import _verify_cell_claim
from draftwright.registry import AnnotationRegistry


class _ScheduleAnnotationEvidence:
    def __init__(self, annotation, approvals):
        self._annotation = annotation
        requirements = {}
        owners = {}
        locations: list = []
        for reference, cell in approvals:
            feature = reference.measurement.feature
            owners[id(feature)] = feature
            for parameter, count in cell.hole_requirements:
                requirements[(id(feature), parameter)] = (feature, parameter, count)
            locations.extend(
                (feature, component, point) for component, point in cell.hole_locations
            )
        self.source_features = tuple(owners.values())
        self.covers_hole_requirements_by_feature = tuple(requirements.values())
        self.covers_hole_locations = tuple(locations)
        self.covers_count = sum(
            count
            for _feature, parameter, count in requirements.values()
            if parameter == "grouping.count"
        )
        # An unverified stale rider cannot restore a fact withdrawn with its cell.
        self.covers_hole_requirements = ()
        self.covers_hole_centers = ()
        self.covers_hole_representations_by_requirement = ()
        self.hole_representation = None
        self.hole_representation_reason = None

    def __getattr__(self, name):
        return getattr(self._annotation, name)


class _VerifiedScheduleRegistry:
    def __init__(self, registry, plan):
        self._registry = AnnotationRegistry()
        self._registry.restore(registry.snapshot())
        self._registry.restore_issues(registry.issues)
        self._approvals = {}
        schedules = {schedule.name: schedule for schedule in getattr(plan, "schedules", ())}
        for name in sorted(registry.names()):
            cells = registry.cells_of(name)
            annotation = registry.named(name)
            if (
                not cells
                and name not in schedules
                and getattr(annotation, "measurement_schedule", None) is None
            ):
                continue
            approvals = tuple(
                (reference, schedules[reference.schedule].rows[reference.row][reference.column])
                for reference in cells
                if _verify_cell_claim(registry, name, reference, schedules).state == "confirmed"
            )
            self._approvals[name] = approvals
            identity = registry.identity_of(name)
            identity["measurement"] = ()
            identity["cells"] = tuple(reference for reference, _cell in approvals)
            self._registry.add(
                _ScheduleAnnotationEvidence(annotation, approvals), name, registry.view_of(name)
            )
            self._registry.reapply(name, identity)

    def cell_approvals_of(self, name):
        return self._approvals.get(name, ())

    def __getattr__(self, name):
        return getattr(self._registry, name)


def verified_schedule_registry(registry, plan):
    """Filter only typed schedule ink; all other registry evidence keeps its contract."""
    if registry is None or isinstance(registry, _VerifiedScheduleRegistry):
        return registry
    cells_of = getattr(registry, "cells_of", None)
    if not callable(cells_of):
        return registry
    if not getattr(plan, "schedules", ()) and not any(
        cells_of(name) or getattr(registry.named(name), "measurement_schedule", None) is not None
        for name in registry.names()
    ):
        return registry
    return _VerifiedScheduleRegistry(registry, plan)
