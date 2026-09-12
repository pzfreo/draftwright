"""Repair must clear real ink collisions without trading away drawing content."""

import pytest
from build123d import Box, Pos, Rot

from draftwright import build_drawing
from draftwright.audit import compare_measurements
from draftwright.linting.ink_overlap import segments_of


def _geometry(item):
    box = item.bounding_box()
    label = getattr(item, "label_bbox", None)
    return (
        tuple(box.min),
        tuple(box.max),
        None if label is None else tuple(label),
        segments_of(item),
    )


def test_repair_clears_parallel_dimension_ink_and_preserves_measurements_and_pins():
    # The rotated blind pocket from #916 has two parallel dimensions on the same
    # line: each one's stroke crosses the other's text. Disable build-time repair
    # so this remains a direct public repair regression when the handler lands.
    part = Rot(90, 0, 0) * (Box(50, 50, 30) - Pos(10, 10, 10) * Box(22, 14, 10))
    drawing = build_drawing(part, repair=False, scale=1)
    before = drawing.lint(physical=False)
    assert [issue.code for issue in before] == [
        "annotation_ink_overlap",
        "annotation_ink_overlap",
    ]
    measurements = drawing.measurement_snapshot()
    annotations = dict(drawing.iter_annotations())
    assert "m_env_width" in annotations
    pins = {name: item for name, item in annotations.items() if name != "m_env_width"}
    pin_geometry = {name: _geometry(item) for name, item in pins.items()}
    for name in pins:
        drawing.pin(name)

    assert drawing.repair() is drawing

    assert set(drawing.annotations()) == set(annotations)
    assert all(drawing.get_annotation(name) is item for name, item in pins.items())
    assert drawing.registry.pinned_names() == frozenset(pins)
    assert {name: _geometry(drawing.get_annotation(name)) for name in pins} == pin_geometry
    assert compare_measurements(measurements, drawing)["status"] == "preserved"
    assert drawing.lint(physical=False) == []

    repaired = dict(drawing.iter_annotations())
    drawing.repair()
    assert all(drawing.get_annotation(name) is item for name, item in repaired.items())
    assert drawing.lint(physical=False) == []


def test_pins_keep_an_infeasible_ink_collision_visible_without_moving_geometry():
    part = Rot(90, 0, 0) * (Box(50, 50, 30) - Pos(10, 10, 10) * Box(22, 14, 10))
    drawing = build_drawing(part, repair=False, scale=1)
    before = drawing.lint(physical=False)
    assert [issue.code for issue in before] == [
        "annotation_ink_overlap",
        "annotation_ink_overlap",
    ]
    original = dict(drawing.iter_annotations())
    geometry = {name: _geometry(item) for name, item in original.items()}
    measurements = drawing.measurement_snapshot()
    for name in original:
        drawing.pin(name)

    drawing.repair()

    assert set(drawing.annotations()) == set(original)
    assert all(drawing.get_annotation(name) is item for name, item in original.items())
    assert {name: _geometry(item) for name, item in drawing.iter_annotations()} == geometry
    assert drawing.registry.pinned_names() == frozenset(original)
    assert compare_measurements(measurements, drawing)["status"] == "preserved"
    assert drawing.lint(physical=False) == before


@pytest.mark.parametrize(
    "fault",
    [
        "span",
        "pin",
        "membership",
        "equal_lint",
        "lint_error",
        "unknown",
        "authored_side",
        "witness_points",
    ],
)
def test_bad_candidates_cannot_bypass_preservation_and_rollback(monkeypatch, fault):
    from draftwright.annotations import _common

    part = Rot(90, 0, 0) * (Box(50, 50, 30) - Pos(10, 10, 10) * Box(22, 14, 10))
    drawing = build_drawing(part, repair=False, scale=1)
    original = dict(drawing.iter_annotations())
    for name in original:
        if name != "m_env_width":
            drawing.pin(name)
    before = drawing.lint(physical=False)
    measurements = drawing.measurement_snapshot()
    items = list(drawing.items)
    registry = drawing.registry.snapshot()
    candidates = _common.prevent_dimension_label_ink(
        list(original.items()),
        page=(0, 0, drawing.page_w, drawing.page_h),
        immutable=drawing.registry.pinned_names(),
        perpendicular_step=drawing.draft.font_size + 2 * drawing.draft.pad_around_text,
    )
    changed = [(name, item) for name, item in candidates if item is not original[name]]
    assert [name for name, _ in changed] == ["m_env_width"]
    new = changed[0][1]
    # First prove this is a useful candidate, independently of repair's decision.
    try:
        drawing.items[
            next(i for i, item in enumerate(items) if item is original["m_env_width"])
        ] = new
        drawing.registry.replace_object(original["m_env_width"], new)
        assert drawing.lint(physical=False) == []
        assert compare_measurements(measurements, drawing)["status"] == "preserved"
        if fault == "span":
            new._dw_measurement_span = ((1, 2, 3), (51, 2, 3))
            assert drawing.lint(physical=False) == []
            assert compare_measurements(measurements, drawing)["status"] == "changed"
    finally:
        drawing.items[:] = items
        drawing.registry.restore(registry)

    if fault == "pin":
        drawing.pin("m_env_width")  # the candidate was computed before this pin
    elif fault == "membership":
        candidates = [(name, item) for name, item in candidates if name != "m_env_width"]
    elif fault == "authored_side":
        new._dw_authored_side = "left"
    elif fault == "witness_points":
        new._dw_spec.p1 = tuple(value + 1 for value in new._dw_spec.p1)
    elif fault == "unknown":
        identity = drawing.registry.identity_of("m_env_width")
        drawing.registry.reapply("m_env_width", dict(identity, measurement=()))
        assert drawing.measurement_snapshot().unknown
    registry = drawing.registry.snapshot()
    attempts = []

    def choose(*_args, **_kwargs):
        attempts.append(True)
        return candidates

    monkeypatch.setattr(_common, "prevent_dimension_label_ink", choose)
    lint = drawing.lint
    if fault in {"equal_lint", "lint_error"}:

        def judge(**kwargs):
            if drawing.get_annotation("m_env_width") is new:
                if fault == "lint_error":
                    raise RuntimeError("critique unavailable")
                return before  # moving without strict improvement is not a repair
            return lint(**kwargs)

        monkeypatch.setattr(drawing, "lint", judge)
    if fault == "lint_error":
        with pytest.raises(RuntimeError, match="critique unavailable"):
            drawing.repair()
    else:
        drawing.repair()
    assert bool(attempts) == (fault != "unknown")
    assert all(a is b for a, b in zip(drawing.items, items, strict=True))
    assert drawing.registry.snapshot() == registry
    assert drawing.lint(physical=False) == before


def test_automatic_and_declared_builds_use_the_same_recognition_free_repair():
    from conftest import recognition_consumer_calls

    part = Rot(90, 0, 0) * (Box(50, 50, 30) - Pos(10, 10, 10) * Box(22, 14, 10))
    # Unpinned on both sides, unlike the tests above: this one compares the
    # automatic and declared paths to each other, so they must land on the same
    # sheet. Passing `scale=` to the declared build would force a recognition
    # call that `recognition_consumer_calls` exists to prove does not happen.
    raw = build_drawing(part, repair=False)
    assert len(raw.lint(physical=False)) == 2
    automatic = build_drawing(part)
    automatic_left = [i.code for i in automatic.lint(physical=False)]
    # Both halves: repair clears everything on the automatic path, AND the declared
    # path ends up in the same place. Comparing only the two would pass a regression
    # that left both equally dirty.
    assert automatic_left == []
    with recognition_consumer_calls() as counts:
        declared = build_drawing(part, model=raw.model(), repair=False)
        before = declared.measurement_snapshot()
        assert len(declared.lint(physical=False)) == 2
        declared.repair()
        assert [i.code for i in declared.lint(physical=False)] == automatic_left
        assert compare_measurements(before, declared)["status"] == "preserved"
    assert dict(counts) == {}


def test_partial_repair_keeps_infeasibility_visible_and_is_idempotent():
    part = Rot(90, 0, 0) * (Box(50, 50, 30) - Pos(10, 10, 10) * Box(22, 14, 10))
    drawing = build_drawing(part, scale=2, repair=False)
    assert [issue.code for issue in drawing.lint(physical=False)] == ["annotation_ink_overlap"] * 3
    before = drawing.measurement_snapshot()
    drawing.repair()
    assert [issue.code for issue in drawing.lint(physical=False)] == ["annotation_ink_overlap"]
    assert compare_measurements(before, drawing)["status"] == "preserved"
    repaired = list(drawing.items)
    drawing.repair()
    assert all(a is b for a, b in zip(drawing.items, repaired, strict=True))
    assert [issue.code for issue in drawing.lint(physical=False)] == ["annotation_ink_overlap"]


def test_zero_repair_budget_keeps_the_actual_collision_untouched(monkeypatch):
    from draftwright.annotations import _common

    part = Rot(90, 0, 0) * (Box(50, 50, 30) - Pos(10, 10, 10) * Box(22, 14, 10))
    drawing = build_drawing(part, repair=False, scale=1)
    before = drawing.lint(physical=False)
    assert [issue.code for issue in before] == ["annotation_ink_overlap"] * 2
    items = list(drawing.items)

    def unexpected(*args, **kwargs):
        pytest.fail("zero budget must not construct repair candidates")

    monkeypatch.setattr(_common, "prevent_dimension_label_ink", unexpected)
    drawing.repair(max_iter=0)
    assert all(a is b for a, b in zip(drawing.items, items, strict=True))
    assert drawing.lint(physical=False) == before


def test_repair_rejects_a_lower_issue_count_that_introduces_label_overlap(monkeypatch):
    from draftwright.annotations import _common

    part = Rot(90, 0, 0) * (Box(50, 50, 30) - Pos(10, 10, 10) * Box(22, 14, 10))
    drawing = build_drawing(part, repair=False, scale=1)
    before = drawing.lint(physical=False)
    assert [issue.code for issue in before] == [
        "annotation_ink_overlap",
        "annotation_ink_overlap",
    ]
    original = dict(drawing.iter_annotations())
    for name in original:
        if name != "m_env_width":
            drawing.pin(name)
    items = list(drawing.items)
    registry = drawing.registry.snapshot()
    geometry = {name: _geometry(item) for name, item in original.items()}
    measurements = drawing.measurement_snapshot()

    # Establish the adverse candidate from the actual shared solver. Along-span
    # choices clear both ink crossings but create a different kind of collision.
    candidate = dict(
        _common.prevent_dimension_label_ink(
            list(original.items()),
            page=(0, 0, drawing.page_w, drawing.page_h),
            immutable=drawing.registry.pinned_names(),
        )
    )
    changed = {name for name in original if original[name] is not candidate[name]}
    assert changed == {"m_env_width"}
    try:
        for name in changed:
            old, new = original[name], candidate[name]
            drawing.items[next(i for i, item in enumerate(drawing.items) if item is old)] = new
            drawing.registry.replace_object(old, new)
        adverse = drawing.lint(physical=False)
        assert [issue.code for issue in adverse] == ["annotation_overlap"]
        assert len(adverse) < len(before)
        assert compare_measurements(measurements, drawing)["status"] == "preserved"
    finally:
        drawing.items[:] = items
        drawing.registry.restore(registry)

    attempts = []

    def adverse_choices(dimensions, **_options):
        dimensions = list(dimensions)
        assert "m_env_width" in {name for name, _ in dimensions}
        attempts.append(True)
        return [(name, candidate.get(name, item)) for name, item in dimensions]

    monkeypatch.setattr(_common, "prevent_dimension_label_ink", adverse_choices)
    drawing.repair()

    # The candidate must actually be attempted: the old no-op repair cannot pass
    # this rejection test merely by leaving the original drawing untouched.
    assert attempts, "The bounded repair must evaluate the adverse shared-solver candidate"
    assert len(drawing.items) == len(items)
    assert all(a is b for a, b in zip(drawing.items, items, strict=True))
    assert set(drawing.annotations()) == set(original)
    assert all(drawing.get_annotation(name) is item for name, item in original.items())
    assert drawing.registry.snapshot() == registry
    assert {name: _geometry(item) for name, item in drawing.iter_annotations()} == geometry
    assert compare_measurements(measurements, drawing)["status"] == "preserved"
    assert drawing.lint(physical=False) == before
