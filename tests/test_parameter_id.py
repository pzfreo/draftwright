"""ADR 0016 identity — `DimParameter.parameter_id`, the derived semantic key (#869).

Three guards, matching the three tiers the ADR's identity section names:

- **derivation** — the id is ``role.kind`` (+ discriminator), derived and never
  hand-authored, so the ~40 construction sites cannot drift a literal away from
  the ``role=`` beside them;
- **uniqueness, fail-closed** — no feature yields two *independently addressable*
  parameters under one id. The feature universe is enumerated MECHANICALLY from
  `model.ir`, so a new `Feature` type fails this suite until it is sampled;
- **stability** — re-detecting the same solid yields the same ids, which is what
  makes an id safe to write into a version-controlled script.

The uniqueness guard carries an explicit exemption for the ADR's tier 3
*correlated sets* (a `step_height` ladder, a rotational body's concentric bores),
where sharing one id is the design rather than a collision. That exemption is
temporary scaffolding: once `AddressableDimension` lands (#870) the guard is
rephrased over addressable units and the exemption disappears — a correlated set
becomes one unit holding N members, so it cannot collide with anything.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest
from build123d import Box, Cylinder, Pos

import draftwright.model.ir as ir
from draftwright.model import DimParameter, build_part_model

# ── The tier-3 exemption ──────────────────────────────────────────────────────
# Roles whose parameters are a correlated SET routed as a whole (ir.py says so at
# the source: "a single `role=` intent rebuilds the whole ladder"). Their members
# are deliberately NOT separately addressable, so they share one id by design.
# Replaced by `AddressableDimension` in #870 — see the module docstring.
_CORRELATED_SET_ROLES = {"step_height", "step_position"}
# `RotationalFeature` emits one `bore` diameter per concentric bore. Whether these
# stay one identity or split into addressable members is settled when the units are
# built (#870); provisional until then, per ADR 0016 identity tier 3.
_CORRELATED_SET_FEATURES = {"rotational"}


def _feature_classes() -> dict[str, type]:
    """Every `Feature` implementation in `model.ir`, found mechanically — a frozen
    dataclass carrying a `kind` and a `parameters()`. Deriving this rather than
    listing it is what makes the completeness check below fail-closed."""
    return {
        name: obj
        for name, obj in vars(ir).items()
        if inspect.isclass(obj)
        and dataclasses.is_dataclass(obj)
        and hasattr(obj, "kind")
        and hasattr(obj, "parameters")
    }


_F = ir.Frame((0.0, 0.0, 0.0), "z")

# One MAXIMAL instance per feature type — every optional parameter populated, since
# uniqueness is only stressed by a feature emitting its full parameter set.
_SAMPLES: dict[str, ir.Feature] = {
    "HoleFeature": ir.HoleFeature(
        _F,
        8.0,
        depth=10.0,
        through=False,
        cbore=(16.0, 4.0),
        spotface=(20.0, 1.0),
        csink=(12.0, 90.0),
        thread="M8x1.25",
    ),
    "StepFeature": ir.StepFeature(_F, 20.0, 30.0, ((0, 0, 0), (0, 0, 20))),
    "PatternFeature": ir.PatternFeature(
        _F,
        "grid",
        4,
        ir.HoleFeature(_F, 5.0, depth=None, through=True),
        grid=(30.0, 40.0),
        rows=2,
        cols=2,
        pitch=30.0,
        bcd=50.0,
    ),
    "EnvelopeFeature": ir.EnvelopeFeature(_F, 80.0, 8.0, 50.0, (-40, -25, 0), (40, 25, 8)),
    "SlotFeature": ir.SlotFeature(_F, "y", "x", 8.0, 30.0, 0.0, -15.0, 15.0),
    "PocketFeature": ir.PocketFeature(_F, "y", "x", 8.0, 30.0, 5.0, 0.0, -15.0, 15.0),
    "PocketPatternFeature": ir.PocketPatternFeature(
        _F,
        "grid",
        4,
        ir.PocketFeature(_F, "y", "x", 8.0, 30.0, 5.0, 0.0, -15.0, 15.0),
        grid=(30.0, 40.0),
        rows=2,
        cols=2,
        pitch=30.0,
    ),
    "SlotPatternFeature": ir.SlotPatternFeature(
        _F,
        "grid",
        4,
        ir.SlotFeature(_F, "y", "x", 8.0, 30.0, 0.0, -15.0, 15.0),
        grid=(30.0, 40.0),
        rows=2,
        cols=2,
        pitch=30.0,
    ),
    "BossFeature": ir.BossFeature(_F, 25.0, height=12.0, thread="M25x1.5"),
    "ChamferFeature": ir.ChamferFeature(_F, "z", 2.0, 2.0, 45.0),
    "FilletFeature": ir.FilletFeature(_F, "z", 3.0),
    "FlatFeature": ir.FlatFeature(_F, "z", 18.0),
    "GrooveFeature": ir.GrooveFeature(_F, "z", 3.0, 20.0),
    "StepLevelFeature": ir.StepLevelFeature(_F, 0.0, (5.0, 10.0, 15.0)),
    "PlateFeature": ir.PlateFeature(_F, "z", 0.0, 8.0, 80.0, 50.0),
    "RotationalFeature": ir.RotationalFeature(_F, 40.0, bores=(10.0, 16.0)),
    "AuthoredDimension": ir.AuthoredDimension(_F, "linear", 40.0, "40", "X"),
    "PmiFeature": ir.PmiFeature(_F, "position", 0.1, "position 0.1", "X"),
    "ControlFrame": ir.ControlFrame(_F, "position", 0.1, "front", "below"),
    "DatumRef": ir.DatumRef(_F, "A", "front", "below"),
    "Finish": ir.Finish(_F, 1.6, "front", "below"),
    "Note": ir.Note(_F, "NOTE", "front", "below"),
}


class TestDerivation:
    def test_id_is_role_dot_kind(self):
        assert DimParameter("diameter", "bore", 8.0).parameter_id == "bore.diameter"
        assert DimParameter("depth", "bore", 10.0).parameter_id == "bore.depth"

    def test_discriminator_is_appended_only_when_present(self):
        plain = DimParameter("length", "grid_pitch", 30.0)
        row = DimParameter("length", "grid_pitch", 30.0, discriminator="row")
        assert plain.parameter_id == "grid_pitch.length"
        assert row.parameter_id == "grid_pitch.length.row"

    def test_kind_separates_a_role_that_carries_two_measurements(self):
        """Tier 1: a role alone is not enough — counterbore has a ⌀ AND a depth."""
        cb = ir.HoleFeature(_F, 8.0, depth=None, through=True, cbore=(16.0, 4.0))
        ids = [p.parameter_id for p in cb.parameters()]
        assert "counterbore.diameter" in ids and "counterbore.depth" in ids

    def test_the_id_does_not_depend_on_sibling_parameters(self):
        """The reason `kind` is always included. A parameter's id must not change
        because some *other* parameter was added to its feature — otherwise adding a
        feature field would silently repoint every intent aimed at an existing dim."""
        bare = ir.HoleFeature(_F, 8.0, depth=None, through=True)
        enriched = ir.HoleFeature(
            _F, 8.0, depth=None, through=True, cbore=(16.0, 4.0), csink=(12.0, 90.0)
        )
        bore_id = next(p.parameter_id for p in bare.parameters() if p.role == "bore")
        still = next(p.parameter_id for p in enriched.parameters() if p.role == "bore")
        assert bore_id == still == "bore.diameter"


class TestGridPitchDiscriminator:
    """Tier 2 — the case that forces a third key component. A grid emits two
    `("length", "grid_pitch")` parameters that no combination of kind and role
    tells apart."""

    @pytest.mark.parametrize(
        "name", ["PatternFeature", "PocketPatternFeature", "SlotPatternFeature"]
    )
    def test_a_grid_pattern_yields_two_distinct_pitch_ids(self, name):
        pitches = [p for p in _SAMPLES[name].parameters() if p.role == "grid_pitch"]
        assert len(pitches) == 2
        assert {p.parameter_id for p in pitches} == {
            "grid_pitch.length.row",
            "grid_pitch.length.col",
        }

    def test_row_and_col_not_x_and_y(self):
        """`angle` may rotate the lattice, so a row pitch is not an X pitch in
        general. The IR keys on what it knows; mapping a user-facing `axis=` onto
        these belongs to the facade (#872)."""
        rotated = dataclasses.replace(_SAMPLES["PatternFeature"], angle=30.0)
        ids = {p.parameter_id for p in rotated.parameters() if p.role == "grid_pitch"}
        assert ids == {"grid_pitch.length.row", "grid_pitch.length.col"}


class TestUniquenessAudit:
    def test_every_feature_type_is_sampled(self):
        """Fail-closed: add a `Feature` to `model.ir` and this fails until it is
        sampled below, so no feature type escapes the uniqueness guard."""
        assert set(_feature_classes()) == set(_SAMPLES), (
            "feature universe and _SAMPLES disagree — add the missing sample(s): "
            f"{set(_feature_classes()) ^ set(_SAMPLES)}"
        )

    @pytest.mark.parametrize("name", sorted(_SAMPLES))
    def test_no_feature_yields_two_parameters_under_one_id(self, name):
        feat = _SAMPLES[name]
        if feat.kind in _CORRELATED_SET_FEATURES:
            pytest.skip(f"{feat.kind}: correlated set, provisional until #870")
        ids = [p.parameter_id for p in feat.parameters() if p.role not in _CORRELATED_SET_ROLES]
        dupes = {i for i in ids if ids.count(i) > 1}
        assert not dupes, f"{name} emits colliding parameter ids: {sorted(dupes)}"

    def test_the_guard_would_catch_the_grid_pitch_collision(self):
        """The audit earns its keep: strip the discriminator and the tier-2 case
        collides. This is what fails if a future grid forgets to discriminate."""
        undiscriminated = [
            dataclasses.replace(p, discriminator=None)
            for p in _SAMPLES["PatternFeature"].parameters()
        ]
        ids = [p.parameter_id for p in undiscriminated]
        assert any(ids.count(i) > 1 for i in ids)

    def test_correlated_sets_share_one_id_by_design(self):
        """Tier 3, pinned so the exemption above is a decision rather than an
        oversight: a ladder's members are one addressable thing (#870)."""
        ladder = _SAMPLES["StepLevelFeature"].parameters()
        heights = [p for p in ladder if p.role == "step_height"]
        assert len(heights) == 3
        assert len({p.parameter_id for p in heights}) == 1


class TestStability:
    """What makes an id safe to write into a version-controlled script: it must not
    churn between runs, and re-detecting the same solid must reproduce it."""

    def _part(self):
        return (
            Box(80, 50, 12)
            - Pos(0, 0, 0) * Cylinder(5, 40)
            - Pos(-30, -18, 0) * Cylinder(2.5, 40)
            - Pos(30, -18, 0) * Cylinder(2.5, 40)
            - Pos(-30, 18, 0) * Cylinder(2.5, 40)
            - Pos(30, 18, 0) * Cylinder(2.5, 40)
        )

    def _ids(self, part):
        return [
            (f.kind, p.parameter_id)
            for f in build_part_model(part).features
            for p in f.parameters()
        ]

    def test_redetecting_the_same_part_yields_the_same_ids(self):
        assert self._ids(self._part()) == self._ids(self._part())

    def test_ids_are_readable_semantic_strings(self):
        """Not opaque tokens — they surface in diagnostics and emitted scripts, so a
        UUID or a list position would be unreadable in a diff or unstable across runs
        (both rejected by ADR 0016)."""
        for _, pid in self._ids(self._part()):
            assert pid and not pid[0].isdigit()
            assert pid.replace(".", "").replace("_", "").isalnum()
