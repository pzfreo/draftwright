"""Tests for the feature-completeness metric (#148f / #608)."""

from __future__ import annotations

from build123d import Box, Cylinder, Pos, Rotation

from draftwright.score import feature_census


def _grooved_shaft():
    # ø20 turned shaft with one circlip groove (floor ø16).
    return Cylinder(10, 40) - (Cylinder(10, 4) - Cylinder(8, 4))


class TestCensus:
    def test_plain_box_recognises_no_features(self):
        census = feature_census(Box(40, 20, 10))
        assert sum(census.values()) == 0
        # every recogniser kind is still a key (a stable, complete census shape).
        assert census["groove"] == 0 and census["slot"] == 0 and census["pocket"] == 0

    def test_each_machined_kind_appears_in_the_census(self):
        # The census is the coverage-progress signal: each #148 feature kind shows up on a part
        # that has it (grooves #606, flats #605, slots #135, pockets #148a, arc-slots #607).
        assert feature_census(_grooved_shaft())["groove"] == 1
        assert feature_census(Box(60, 30, 12) - Box(20, 8, 20))["slot"] == 1
        assert feature_census(Cylinder(10, 40) - Pos(0, 12, 0) * Box(40, 10, 40))["flat"] == 1
        arc = (Rotation(0, 90, 0) * Cylinder(20, 80)) - Pos(0, 0, 14) * Box(6, 24, 12)
        assert feature_census(arc)["pocket"] == 1

    def test_bored_plate_counts_its_hole(self):
        assert feature_census(Box(60, 40, 20) - Cylinder(5, 40))["hole"] == 1

    def test_census_has_a_stable_complete_key_set(self):
        # Same keys for any part, so a corpus can be summed key-by-key without gaps.
        keys = set(feature_census(Box(40, 20, 10)))
        assert set(feature_census(_grooved_shaft())) == keys
        assert "groove" in keys and "slot" in keys and "flat" in keys and "pocket" in keys
