"""Physical-axis grouping for turned step-length chains."""

from build123d import Align, Cylinder, Pos

from draftwright import Sheet
from draftwright.annotations._step_lengths import _profile_groups_from_plan
from draftwright.model.compiled import compile_dimensions


def test_collinear_groove_bridges_only_its_own_ungrouped_step_gap():
    centre_min = (Align.CENTER, Align.CENTER, Align.MIN)
    part = Cylinder(15, 200, align=centre_min)
    part -= Pos(0, 0, 95) * (
        Cylinder(15, 10, align=centre_min) - Cylinder(12, 10, align=centre_min)
    )

    def grouped_spans(groove_at):
        sheet = Sheet(part, title="GROOVE GROUPING", number="1357-GG")
        first = sheet.step(diameter=30, length=95, at=(0, 0, 47.5), axis="z")
        second = sheet.step(diameter=30, length=95, at=(0, 0, 152.5), axis="z")
        sheet.dimension(first, "step.length")
        sheet.dimension(second, "step.length")
        groove = sheet.groove(axis="z", width=10, diameter=24, at=groove_at)
        sheet.dimension(groove, "groove.length")
        sheet.dimension(groove, "groove.diameter")
        model = sheet.model()
        steps = [feature for feature in model.features if feature.kind == "step"]
        assert len(steps) == 2
        assert steps[0].span[1][2] == 95 and steps[1].span[0][2] == 105
        assert all(step.profile_group is None for step in steps)
        return _profile_groups_from_plan(compile_dimensions(model))

    assert [len(refs) for _key, refs in grouped_spans((0, 0, 100))] == [2]
    assert [len(refs) for _key, refs in grouped_spans((50, 0, 100))] == [1, 1]
