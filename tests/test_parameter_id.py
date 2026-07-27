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

The uniqueness guard needs **no exemption list** (#870). Phrased over
`AddressableDimension`s rather than raw parameters, the ADR's tier-3 *correlated
sets* stop being a special case: a `step_height` ladder is one unit holding three
members, so it cannot collide with itself, while two *ungrouped* parameters sharing
an id are still two units and still fail. Structure replaced the table it needed in
its first form — an exemption can hide a real collision, arity cannot.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest
from build123d import Box, Cylinder, Pos

import draftwright.model.ir as ir
from draftwright.model import DimParameter, PartModel, build_part_model
from draftwright.model.planner import (
    CORRELATED_SETS,
    PlannedDimension,
    _addressable,
    plan_dimensions,
)


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
_BBOX = Box(80, 50, 12).bounding_box()  # some planner rules consult the footprint


def _plan(feature):
    """The feature's planned groups — the layer where addressable units exist."""
    return plan_dimensions(PartModel(bbox=_BBOX, orientation="prismatic", features=[feature]))


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
        pitch=25.0,  # != row pitch: equal values would mask a swap
        bcd=50.0,
    ),
    "EnvelopeFeature": ir.EnvelopeFeature(_F, 80.0, 8.0, 50.0, (-40, -25, 0), (40, 25, 8)),
    "SlotFeature": ir.SlotFeature(_F, "y", "x", 8.0, 30.0, 0.0, -15.0, 15.0),
    "PadFeature": ir.PadFeature(_F, "y", "x", 8.0, 30.0, 0.0, -15.0, 15.0, 12.0, 20.0),
    "PocketFeature": ir.PocketFeature(_F, "y", "x", 8.0, 30.0, 5.0, 0.0, -15.0, 15.0),
    "PocketPatternFeature": ir.PocketPatternFeature(
        _F,
        "grid",
        4,
        ir.PocketFeature(_F, "y", "x", 8.0, 30.0, 5.0, 0.0, -15.0, 15.0),
        grid=(30.0, 40.0),
        rows=2,
        cols=2,
        pitch=25.0,  # != row pitch: equal values would mask a swap
    ),
    "SlotPatternFeature": ir.SlotPatternFeature(
        _F,
        "grid",
        4,
        ir.SlotFeature(_F, "y", "x", 8.0, 30.0, 0.0, -15.0, 15.0),
        grid=(30.0, 40.0),
        rows=2,
        cols=2,
        pitch=25.0,  # != row pitch: equal values would mask a swap
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


# The committed id → measurement binding for every feature type, over the maximal
# `_SAMPLES` above. This is what makes the stability claim general rather than
# five-ids-deep: swap the values behind `slot_width.length` and `slot_length.length`
# in `ir.py` and this fails, where determinism and uniqueness checks both still pass.
#
# Ordered tuples, not a dict, because a correlated set legitimately repeats one id
# (a `step_height` ladder, the rotational bores) — a dict would silently drop members.
# The empty tuples are correct: aspect features (GD&T, finish, notes, imported PMI)
# are placed as corridor candidates and carry no dimension parameters at all.
#
# Re-blessing is a deliberate act — an id moving to a different quantity breaks every
# intent aimed at it, silently.
_SAMPLE_BINDINGS = {
    "HoleFeature": (
        ("bore.diameter", 8.0),
        ("bore.depth", 10.0),
        ("counterbore.diameter", 16.0),
        ("counterbore.depth", 4.0),
        ("spotface.diameter", 20.0),
        ("spotface.depth", 1.0),
        ("countersink.diameter", 12.0),
        ("countersink.angle", 90.0),
    ),
    "StepFeature": (("step.length", 20.0), ("step.diameter", 30.0)),
    "PatternFeature": (
        ("bore.diameter", 5.0),
        ("bolt_circle.diameter", 50.0),
        ("pitch.length", 25.0),
        ("grid_pitch.length.row", 30.0),
        ("grid_pitch.length.col", 40.0),
    ),
    "EnvelopeFeature": (("width.length", 80.0), ("height.length", 8.0), ("depth.length", 50.0)),
    "SlotFeature": (("slot_width.length", 8.0), ("slot_length.length", 30.0)),
    "PadFeature": (("pad_width.length", 8.0), ("pad_length.length", 30.0)),
    "PocketFeature": (
        ("pocket_width.length", 8.0),
        ("pocket_length.length", 30.0),
        ("pocket_depth.length", 5.0),
    ),
    "PocketPatternFeature": (
        ("pocket_width.length", 8.0),
        ("pocket_length.length", 30.0),
        ("pocket_depth.length", 5.0),
        ("pitch.length", 25.0),
        ("grid_pitch.length.row", 30.0),
        ("grid_pitch.length.col", 40.0),
    ),
    "SlotPatternFeature": (
        ("slot_width.length", 8.0),
        ("slot_length.length", 30.0),
        ("pitch.length", 25.0),
        ("grid_pitch.length.row", 30.0),
        ("grid_pitch.length.col", 40.0),
    ),
    "BossFeature": (("boss.diameter", 25.0), ("boss_height.length", 12.0)),
    "ChamferFeature": (("chamfer.length", 2.0),),
    "FilletFeature": (("fillet.radius", 3.0),),
    "FlatFeature": (("flat.length", 18.0),),
    "GrooveFeature": (("groove.length", 3.0), ("groove.diameter", 20.0)),
    "StepLevelFeature": (
        ("step_height.length", 5.0),
        ("step_height.length", 10.0),
        ("step_height.length", 15.0),
    ),
    "PlateFeature": (("thickness.length", 8.0),),
    "RotationalFeature": (("od.diameter", 40.0), ("bore.diameter", 10.0), ("bore.diameter", 16.0)),
    "AuthoredDimension": (),
    "PmiFeature": (),
    "ControlFrame": (),
    "DatumRef": (),
    "Finish": (),
    "Note": (),
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
    def test_no_feature_yields_two_addressable_dimensions_under_one_id(self, name):
        """**No exemptions.** Phrased over addressable units rather than raw
        parameters, the tier-3 problem dissolves: a correlated set is *one* unit
        holding N members, so it cannot collide with itself, while two *ungrouped*
        parameters sharing an id are two units and still fail."""
        for group in _plan(_SAMPLES[name]):
            ids = [u.id for u in group.units]
            dupes = {i for i in ids if ids.count(i) > 1}
            assert not dupes, f"{name} emits colliding addressable ids: {sorted(dupes)}"

    def test_the_guard_has_no_exemption_list_left(self):
        """PR 1 audited raw parameters and needed a `(feature, role)` exemption table
        to tolerate the correlated sets. The unit layer replaces that table with
        structure, which is the point of making the addressable dimension
        first-class — an exemption can hide a real collision; arity cannot."""
        assert "_CORRELATED_SETS" not in globals()

    def test_a_correlated_set_is_one_unit_with_n_members(self):
        """The tier-3 sets, as grouped by the planner rather than tolerated by a
        filter: a three-level ladder is one addressable dimension holding three."""
        (group,) = _plan(_SAMPLES["StepLevelFeature"])
        (unit,) = [u for u in group.units if u.id == "step_height.length"]
        assert len(unit.members) == 3
        assert [m.param.value for m in unit.members] == [5.0, 10.0, 15.0]

    def test_a_singleton_beside_a_correlated_set_stays_addressable(self):
        """`RotationalFeature`'s `od` sits beside its correlated bores and remains a
        unit of its own — the reason grouping is declared per `(feature, role)` pair
        and not per feature."""
        (group,) = _plan(_SAMPLES["RotationalFeature"])
        by_id = {u.id: len(u.members) for u in group.units}
        assert by_id == {"od.diameter": 1, "bore.diameter": 2}

    def test_grouping_is_declared_not_inferred_from_a_collision(self):
        """The load-bearing rule. Two `step_height` params on a feature that has NOT
        declared the set stay two units — so a grid pitch that lost its discriminator
        fails the audit instead of silently becoming a 'correlated set'."""
        assert ("hole", "step_height") not in CORRELATED_SETS
        pds = [
            PlannedDimension(param=DimParameter("length", "step_height", v), convention="linear")
            for v in (5.0, 10.0)
        ]
        units = _addressable(_SAMPLES["HoleFeature"], pds)
        assert len(units) == 2  # not merged — the pair was never declared

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
    """What makes an id safe to write into a version-controlled script.

    Two distinct properties, and the weaker one alone is not enough:

    - the id *string* must not churn between runs; and
    - the id must stay attached to **the same measurement**. An id that silently
      repoints — same string, different physical quantity — is worse than one that
      churns, because nothing fails: the intent quietly starts dimensioning
      something else. So the checks below carry each id's *value* and its feature's
      location, never the id alone.
    """

    # The committed expectation. Two live detections can only prove determinism —
    # they cannot catch a *code change* that repoints an id, because both sides run
    # the changed code and agree. Only a snapshot fails when `width.length` starts
    # measuring the depth. Re-blessing this is a deliberate act: an id moving to a
    # different quantity breaks every intent aimed at it, silently.
    #
    # Keyed on (feature kind, feature origin, id) → value. `span`/`refs` are
    # deliberately excluded — they carry model coordinates that shift with unrelated
    # recognition work, so pinning them would make this churn without adding
    # semantic protection. Note the two holes sharing `bore.diameter`: that is why
    # the key includes the feature, not the parameter id alone.
    _EXPECTED = {
        ("hole", (-30.0, -18.0, 6.0), "bore.diameter"): 5.0,
        ("hole", (0.0, 0.0, 6.0), "bore.diameter"): 10.0,
        ("envelope", (0.0, 0.0, 0.0), "width.length"): 80.0,
        ("envelope", (0.0, 0.0, 0.0), "height.length"): 12.0,
        ("envelope", (0.0, 0.0, 0.0), "depth.length"): 50.0,
    }

    def _part(self):
        # The four ⌀5 corner holes are grouped into ONE count-group feature rather
        # than a grid pattern, so this fixture carries no grid pitches — the row/col
        # discriminator is pinned at unit level above instead.
        return (
            Box(80, 50, 12)
            - Pos(0, 0, 0) * Cylinder(5, 40)
            - Pos(-30, -18, 0) * Cylinder(2.5, 40)
            - Pos(30, -18, 0) * Cylinder(2.5, 40)
            - Pos(-30, 18, 0) * Cylinder(2.5, 40)
            - Pos(30, 18, 0) * Cylinder(2.5, 40)
        )

    def _measurements(self, part):
        """Each id keyed to what it measures — feature kind + location, id, value."""
        return sorted(
            (f.kind, f.frame.origin, p.parameter_id, p.value)
            for f in build_part_model(part).features
            for p in f.parameters()
        )

    def test_redetection_reproduces_the_same_ids_on_the_same_measurements(self):
        """Determinism: the same solid detected twice agrees."""
        assert self._measurements(self._part()) == self._measurements(self._part())

    @pytest.mark.parametrize("name", sorted(_SAMPLES))
    def test_every_feature_type_holds_its_ids_to_the_same_measurements(self, name):
        """The general form: every feature type's full id → value binding is pinned,
        so repointing anywhere on the identity surface fails here. Determinism and
        uniqueness both survive such a swap, which is why neither is sufficient."""
        got = tuple((p.parameter_id, p.value) for p in _SAMPLES[name].parameters())
        assert got == _SAMPLE_BINDINGS[name]

    def test_every_sample_has_a_committed_binding(self):
        """Fail-closed with the universe check: a new feature type must be sampled
        *and* pinned, so it cannot enter with its identity unguarded."""
        assert set(_SAMPLE_BINDINGS) == set(_SAMPLES)

    def test_every_id_still_measures_what_it_measured(self):
        """The same property end-to-end, through real detection rather than
        hand-built features — so a change in the *detectors* is caught too."""
        got = {
            (kind, origin, pid): value
            for kind, origin, pid, value in self._measurements(self._part())
        }
        assert got == self._EXPECTED

    def test_row_and_col_ids_track_the_grid_tuple_order(self):
        """The contract a value-carrying stability check cannot enforce on its own:
        comparing two runs of the *same* code cannot catch a code change that swaps
        which physical pitch is called row. Pin the mapping explicitly, so a swap in
        `ir.py` fails here rather than silently repointing every pitch intent."""
        feat = dataclasses.replace(_SAMPLES["PatternFeature"], grid=(30.0, 45.0))
        by_id = {p.parameter_id: p.value for p in feat.parameters() if p.role == "grid_pitch"}
        assert by_id["grid_pitch.length.row"] == 30.0  # grid = (row_pitch, col_pitch)
        assert by_id["grid_pitch.length.col"] == 45.0

    def test_the_row_col_mapping_survives_a_rotated_lattice(self):
        """Rotation must not reshuffle which pitch owns which id — the whole reason
        the IR keys on row/col rather than x/y."""
        feat = dataclasses.replace(_SAMPLES["PatternFeature"], grid=(30.0, 45.0), angle=30.0)
        by_id = {p.parameter_id: p.value for p in feat.parameters() if p.role == "grid_pitch"}
        assert by_id == {"grid_pitch.length.row": 30.0, "grid_pitch.length.col": 45.0}

    def test_ids_are_readable_semantic_strings(self):
        """Not opaque tokens — they surface in diagnostics and emitted scripts, so a
        UUID or a list position would be unreadable in a diff or unstable across runs
        (both rejected by ADR 0016)."""
        for _, _, pid, _ in self._measurements(self._part()):
            assert pid and not pid[0].isdigit()
            assert pid.replace(".", "").replace("_", "").isalnum()
