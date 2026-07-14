"""Tests for the feature-completeness metric (#148f / #608)."""

from __future__ import annotations

import pytest
from build123d import Box, Cylinder, Pos, Rotation

import draftwright.score as score
from draftwright.score import FeatureScore, feature_census, feature_completeness


def _grooved_shaft():
    # ø20 turned shaft with one circlip groove (floor ø16).
    return Cylinder(10, 40) - (Cylinder(10, 4) - Cylinder(8, 4))


def _bored_plate():
    return Box(60, 40, 20) - Cylinder(5, 40)  # single bore ø10


class TestCensus:
    def test_plain_box_recognises_no_features(self):
        census = feature_census(Box(40, 20, 10))
        assert sum(census.values()) == 0
        # every recogniser kind is still a key (a stable, complete census shape).
        assert "groove" in census and "slot" in census and "pocket" in census

    def test_each_machined_kind_appears_in_the_census(self):
        # The census is the coverage-progress signal: each #148 feature kind shows up on a part
        # that has it (grooves #606, flats #605, slots #135, pockets #148a, arc-slots #607).
        assert feature_census(_grooved_shaft())["groove"] == 1
        assert feature_census(Box(60, 30, 12) - Box(20, 8, 20))["slot"] == 1
        assert feature_census(Cylinder(10, 40) - Pos(0, 12, 0) * Box(40, 10, 40))["flat"] == 1
        arc = (Rotation(0, 90, 0) * Cylinder(20, 80)) - Pos(0, 0, 14) * Box(6, 24, 12)
        assert feature_census(arc)["pocket"] == 1

    def test_feature_census_matches_the_score_census(self):
        part = _grooved_shaft()
        assert feature_census(part) == feature_completeness(part).census


class TestCompleteness:
    def test_prismatic_part_scores_one_vacuously(self):
        # No feature diameter → nothing on the cylinder side to miss → 1.0.
        s = feature_completeness(Box(40, 20, 10))
        assert s.diameters == ()
        assert s.completeness == 1.0

    def test_recognised_diameters_are_fully_covered(self):
        for part in (_grooved_shaft(), _bored_plate()):
            s = feature_completeness(part)
            assert s.diameters  # the part has feature diameters
            assert s.completeness == 1.0
            assert set(s.covered) == set(s.diameters)

    def test_ratio_is_a_regression_guard(self):
        # The ratio drops below 1.0 exactly when NO cylindrical recogniser accounts for a
        # feature diameter — i.e. a base-recogniser regression. Simulate the hole recogniser
        # failing on a bored plate: its ø10 bore becomes uncovered.
        part = _bored_plate()
        assert feature_completeness(part).completeness == 1.0
        original = score.recognise_holes
        score.recognise_holes = lambda p, **kw: []
        try:
            s = feature_completeness(part)
        finally:
            score.recognise_holes = original
        assert s.diameters == (10.0,)
        assert s.covered == ()
        assert s.completeness == 0.0

    def test_score_invariants(self):
        for part in (Box(40, 20, 10), _grooved_shaft(), _bored_plate()):
            s = feature_completeness(part)
            assert isinstance(s, FeatureScore)
            assert 0.0 <= s.completeness <= 1.0
            assert set(s.covered) <= set(s.diameters)
            assert s.total == sum(s.census.values())
            expected = len(s.covered) / len(s.diameters) if s.diameters else 1.0
            assert s.completeness == pytest.approx(expected)
