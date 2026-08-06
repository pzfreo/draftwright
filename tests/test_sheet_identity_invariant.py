"""The Sheet identity invariant, enforced across every retained object (#912).

The invariant #908/#910 arrived at, stated once:

> Every long-lived reference to a feature is identity-based, never position-based.
> Reordering preserves the target; replacing or deleting has deliberate, documented
> semantics; no retained builder, intent, tolerance, GD&T origin, section request or
> decoration may silently retarget a neighbour.

**Why this file is behavioural and not another AST guard.** Its siblings
(`test_drawing_encapsulation.py`, `test_carve_free_position_callers.py`) walk the AST for a
syntactic marker, because theirs *is* syntactic — an assignment to ``dwg._x``, a call to a named
function. The marker here would be "an attribute assigned from an index into ``_features``", and
*is this value an index* is not decidable from the AST. A name-based proxy (``_src``, ``_index``,
``_i``) is evaded by naming the field something else, which is precisely how #910 happened: the
leak was a field called ``_src`` on a class that derived from none of the handle types the
migration swept. So the check is the observable consequence instead, and it catches a stored
index under any name.

**The oracle.** ``Sheet.model()`` is the whole declared state materialised — features,
tolerances, GD&T origins, section request, dimension requests. Two runs that differ only by an
identity-preserving mutation must produce the same model. Retargeting shows up as a difference
there no matter which layer leaked it.

**The ratchet.** :func:`test_every_handle_returning_verb_is_exercised` fails when a new verb
starts handing out a retained object, and :func:`test_the_handle_class_roster_is_closed` fails
when a new *kind* of retained object appears. Either way the author has to come here and say
what the new thing does across a reorder, which is the question #910 went three rounds without
being asked.

Acceptance for the guard itself: restoring `_Control`'s pre-#910 shape (an index resolved at
append time) must fail :class:`TestRetainedBuilders`. A guard that passes on the buggy commit
proves nothing — see the canary discipline in #737.
"""

from __future__ import annotations

import ast
import copy
import pickle
from pathlib import Path

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import Sheet
from draftwright.model import Frame, HoleFeature

_SRC = Path(__file__).resolve().parent.parent / "src" / "draftwright" / "sheet.py"

#: Every class `Sheet` hands back that outlives the call which made it. A new entry here is a new
#: opportunity for the #910 bug, so each must appear in a scenario below.
#: `_Nameable` is a MIXIN, not a retained object — nothing hands one back, so it needs no
#: scenario; it is listed because this roster is closed on class NAMES in sheet.py, which is
#: what makes a new kind of handle impossible to add unnoticed (#963). If it ever gains state
#: or is returned by a verb, it needs a scenario in `_SCENARIOS` like the rest.
_HANDLE_CLASSES = frozenset(
    {"_Hole", "_Dim", "_Params", "_Control", "DimensionIntent", "_Nameable"}
)


def _part():
    """Two same-⌀-family holes plus a boss. Same-KIND neighbours are the point: a type or role
    check in the wrong place can mask retargeting between features of different kinds, and then
    the guard passes for the wrong reason."""
    part = Box(90, 60, 20)
    part -= Pos(-25, 0, 4) * Cylinder(5, 40)
    part -= Pos(25, 0, 4) * Cylinder(3, 40)
    return part


def _sheet():
    return Sheet(_part(), title="T", number="N").auto_dimensions()


def _canonical(model) -> tuple:
    """A model reduced to something order-insensitive and comparable.

    Sorted, because an identity-preserving reorder legitimately changes feature ORDER — what it
    must not change is which feature carries which aspect. Reprs rather than the objects because
    decoration keys are features, and a set of frozen dataclasses would compare by value and hide
    a swap between two features that happen to be equal.
    """
    features = sorted(repr(f) for f in model.features)
    decorations = sorted((repr(k), repr(v)) for k, v in getattr(model, "decorations", {}).items())
    requested = sorted(repr(r) for r in getattr(model, "requested_dimensions", ()))
    return (features, decorations, requested)


# --------------------------------------------------------------------------------------------
# Scenarios: (declare prerequisites -> retained object), (drive it), for every handle-returning
# verb. Each returns the object under test; the driver is invoked AFTER the mutation.
# --------------------------------------------------------------------------------------------


def _scn_hole(s):
    h = s.hole(diameter=10, at=(-25, 0, 20), axis="z").depth(12)
    s.hole(diameter=6, at=(25, 0, 20), axis="z").depth(8)  # same-kind neighbour
    return h, lambda h: h.tolerance(0.05).note("DEBURR")


def _scn_of(s):
    s.hole(diameter=10, at=(-25, 0, 20), axis="z").depth(12)
    s.hole(diameter=6, at=(25, 0, 20), axis="z").depth(8)
    return s.of(0), lambda h: h.fit("H7")


def _scn_diameter(s):
    d = s.diameter(diameter=30.0, height=8.0, at=(0, 0, 20), axis="z")
    s.diameter(diameter=18.0, height=8.0, at=(20, 0, 20), axis="z")  # same-kind neighbour
    return d, lambda d: d.tolerance(0.02)


def _scn_step(s):
    d = s.step(diameter=30.0, length=12.0, at=(0, 0, 0), axis="x")
    s.step(diameter=18.0, length=6.0, at=(20, 0, 0), axis="x")  # same-kind neighbour
    return d, lambda d: d.tolerance(0.03)


def _scn_envelope(s):
    p = s.envelope()
    s.hole(diameter=6, at=(25, 0, 20), axis="z").depth(8)
    return p, lambda p: p.tolerance(0.1)


def _scn_control(s):
    a = s.hole(diameter=10, at=(-25, 0, 20), axis="z").depth(12)
    s.hole(diameter=6, at=(25, 0, 20), axis="z").depth(8)
    s.datum("A", 1)
    return s.control(a), lambda c: c.position(0.1, to="A").flatness(0.02)


def _scn_section(s):
    """`section(feature=…)` returns `Sheet`, not a handle — which is why the first draft of this
    file missed it entirely, exactly as #910's enumeration missed `_Control`. It stores a
    long-lived reference in `_section` all the same, and cutting through the wrong hole is a
    silent, plausible-looking wrong drawing."""
    a = s.hole(diameter=10, at=(-25, -18, 20), axis="z").depth(12)
    # DIFFERENT Y, and that is load-bearing: the section cut materialises as a Y coordinate, so
    # two holes sharing a Y make the oracle blind — a positional implementation picking the wrong
    # hole would produce the same cut and the scenario would pass while the bug was live.
    s.hole(diameter=6, at=(25, 18, 20), axis="z").depth(8)
    return a, lambda a: s.section(feature=a)


def _scn_datum(s):
    a = s.hole(diameter=10, at=(-25, 0, 20), axis="z").depth(12)
    s.hole(diameter=6, at=(25, 0, 20), axis="z").depth(8)
    return a, lambda a: s.datum("A", a)


def _scn_note(s):
    a = s.hole(diameter=10, at=(-25, 0, 20), axis="z").depth(12)
    s.hole(diameter=6, at=(25, 0, 20), axis="z").depth(8)
    return a, lambda a: s.note("M10x1.5 TAP", a)


def _scn_dimension(s):
    """The #874 authored set. Like `section`/`datum`/`note` it returns `Sheet`, so only the
    state-field ratchet makes it mandatory here."""
    # `_sheet()` states the automatic source; the two are mutually exclusive (#874), so this
    # scenario switches to the authored one. Reaching for the private flag rather than adding
    # a public "unset the source" verb: nothing outside a test wants to change its mind.
    s._auto_dimensions = None
    a = s.hole(diameter=10, at=(-25, 0, 20), axis="z").depth(12)
    s.hole(diameter=6, at=(25, 0, 20), axis="z").depth(8)
    return a, lambda a: s.dimension(a, "bore.diameter")


def _scn_add_dimension(s):
    """The verb runs in the DRIVER, not in setup — so the matrix exercises the resolver on an
    already-reordered sheet, which is a different claim from "a recorded intent survives a
    reorder" and the one an earlier draft silently skipped.

    `DimensionIntent` itself exposes no verb that consumes its stored reference (ADR 0012's
    `.pin()`/`.priority()` were removed from #905 pending #906), so there is nothing further to
    drive on the returned object. `test_dimension_intent_still_has_no_verbs` fails the moment
    there is."""
    a = s.hole(diameter=10, at=(-25, 0, 20), axis="z").depth(12)
    s.hole(diameter=6, at=(25, 0, 20), axis="z").depth(8)
    return a, lambda a: s.add_dimension(a, "bore.diameter")


#: verb name -> (prepare, drive). The ratchet below asserts this covers every handle-returning
#: verb, so a new one cannot ship without an author deciding what it does across a reorder.
_SCENARIOS = {
    "hole": _scn_hole,
    "of": _scn_of,
    "diameter": _scn_diameter,
    "step": _scn_step,
    "envelope": _scn_envelope,
    "control": _scn_control,
    "add_dimension": _scn_add_dimension,
    # Verbs that return `Sheet` but retain a feature reference. Not handle-returning, so the
    # verb ratchet cannot see them — the state-field ratchet below is what makes them mandatory.
    "section": _scn_section,
    "datum": _scn_datum,
    "note": _scn_note,
    "dimension": _scn_dimension,
}

#: Verbs that hand back a `_Params` by exactly the same route `envelope` does — declare the
#: feature, return `_Params(self, len(features) - 1)`, no identity handling of their own. Listed
#: rather than scenario'd because a scenario each would test the same three lines many times.
#: `boss` is absent because it delegates to `diameter` and constructs nothing.
#:
#: The nine below joined in #922, which made every feature verb nameable — previously they
#: returned `Sheet`, so their features could not be referenced at all and
#: `sheet.dimension(chamfer, ...)` was impossible. Their bodies are the same two lines as
#: `envelope`'s, and `test_the_same_path_verbs_really_share_the_route` checks that claim
#: against the source rather than taking it on trust: an exemption list nobody verifies is
#: how a guard stops guarding.
_SAME_PATH_AS_ENVELOPE = {
    "add",
    "measured_dimension",
    "slot",
    "pocket",
    "channel",
    "pad",
    "chamfer",
    "fillet",
    "flat",
    "groove",
    "plate",
    "step_level",
    "pattern",
    "pocket_pattern",
    "slot_pattern",
    "rotational",  # #945 — same two lines; keyword-only, but identity handling is unaffected
}


def _mutations():
    """Mutations, each as (applied to BOTH runs, applied only to the mutated one, does it move?).

    Three properties this table has to have, the last two learned the hard way:

    - the additive part is shared, so a mutation that also ADDS a feature does not make the two
      models differ for a reason that has nothing to do with identity;
    - the reordering part must ACTUALLY reorder, which `sort(key=repr)` did not for most
      scenarios — the entries happened to already be in repr order, so nineteen matrix cases
      asserted nothing while looking like coverage;
    - whether it reorders is declared here and CHECKED at runtime, so a scenario whose features
      resist a given mutation fails loudly instead of passing vacuously.

    The sorts therefore key off each feature's CURRENT position, which reverses the list by
    construction. That still exercises the real `sort(key=…)` and `sort(…, reverse=True)` paths
    on `_FeatureView` — the point is that `sort` moves whole entries and so preserves identity,
    not that any particular ordering is interesting.
    """

    def extra(s):
        s.features.append(HoleFeature(Frame((0, 25, 20), "z"), 3.0, depth=4.0, through=False))

    def positions(s):
        return {id(f): i for i, f in enumerate(s.features)}

    def sort_by_key(s):
        order = positions(s)
        s.features.sort(key=lambda f: -order[id(f)])

    def sort_by_flag(s):
        order = positions(s)
        s.features.sort(key=lambda f: order[id(f)], reverse=True)

    nothing = lambda s: None  # noqa: E731 — a table of one-liners reads better inline
    return {
        "reverse": (nothing, lambda s: s.features.reverse(), True),
        "sort_by_key": (nothing, sort_by_key, True),
        "sort_by_reverse_flag": (nothing, sort_by_flag, True),
        # A deliberate net no-op: a round trip must be harmless, which is a real claim and a
        # different one. Declared as NOT moving, so it cannot be mistaken for coverage.
        "double_reverse": (nothing, lambda s: (s.features.reverse(), s.features.reverse()), False),
        "reverse_with_a_neighbour_added": (extra, lambda s: s.features.reverse(), True),
        # Moves the added feature in FRONT of the target, shifting every later position by one.
        "move_a_neighbour_to_the_front": (
            extra,
            lambda s: s.features.insert(0, s.features.pop()),
            True,
        ),
    }


def _order(sheet) -> list[int]:
    return [id(f) for f in sheet.features]


@pytest.mark.parametrize("verb", sorted(_SCENARIOS))
@pytest.mark.parametrize("mutation", sorted(_mutations()))
class TestRetainedBuilders:
    """Retain the object → mutate `features` → drive the object. Must match the un-mutated run.

    This is the matrix `_Control` failed: `control()` resolved its target and `.position(0.1)`
    consumed the resolution arbitrarily later, so anything that moved the target in between
    silently rebound the frame to a neighbour.
    """

    def test_driving_after_a_mutation_matches_driving_without_one(self, verb, mutation):
        shared, reorder, moves = _mutations()[mutation]

        baseline = _sheet()
        obj, drive = _SCENARIOS[verb](baseline)
        shared(baseline)
        drive(obj)

        mutated = _sheet()
        obj2, drive2 = _SCENARIOS[verb](mutated)
        shared(mutated)
        before = _order(mutated)
        reorder(mutated)
        assert (_order(mutated) != before) is moves, (
            f"`{mutation}` did not reorder {verb}'s features as declared — a case that does not "
            "move the target asserts nothing, however green it looks"
        )
        drive2(obj2)

        assert _canonical(mutated.model()) == _canonical(baseline.model()), (
            f"{verb}() retained an object that did not survive `{mutation}` — the mutation "
            "moved its target and the object followed the position instead of the feature"
        )


@pytest.mark.parametrize("verb", sorted(_SCENARIOS))
def test_a_mutation_between_declaration_and_build_is_invisible(verb):
    """The same invariant for objects driven BEFORE the mutation: a tolerance / GD&T origin /
    section request / dimension intent already recorded must still resolve to its own feature."""
    baseline = _sheet()
    obj, drive = _SCENARIOS[verb](baseline)
    drive(obj)

    mutated = _sheet()
    obj2, drive2 = _SCENARIOS[verb](mutated)
    drive2(obj2)
    before = _order(mutated)
    mutated.features.reverse()
    assert _order(mutated) != before, "the mutation must actually reorder, or this asserts nothing"

    assert _canonical(mutated.model()) == _canonical(baseline.model())


@pytest.mark.parametrize("verb", sorted(_SCENARIOS))
def test_materialisation_is_idempotent(verb):
    """`model()` runs `_prepare()`, which materialises GD&T provenance in place. Calling it twice
    must not append a second copy or re-resolve against a half-materialised list."""
    s = _sheet()
    obj, drive = _SCENARIOS[verb](s)
    drive(obj)
    first = _canonical(s.model())
    assert _canonical(s.model()) == first
    assert _canonical(s.model()) == first


class TestDeliberateInvalidation:
    """Removing or replacing a referenced feature is not a reorder, and must not be treated as
    one. The documented semantics: the reference fails loudly rather than sliding onto whatever
    now occupies the position."""

    @staticmethod
    def _two_holes():
        s = _sheet()
        a = s.hole(diameter=10, at=(-25, 0, 20), axis="z").depth(12)
        s.hole(diameter=6, at=(25, 0, 20), axis="z").depth(8)
        a.tolerance(0.05)
        return s, a

    def test_deleting_the_referenced_feature_raises(self):
        s, _a = self._two_holes()
        del s.features[0]
        with pytest.raises(ValueError, match="no longer on the sheet"):
            s.model()

    def test_popping_the_referenced_feature_raises(self):
        s, _a = self._two_holes()
        s.features.pop(0)
        with pytest.raises(ValueError, match="no longer on the sheet"):
            s.model()

    def test_clearing_raises(self):
        s, _a = self._two_holes()
        s.features.clear()
        with pytest.raises(ValueError, match="no longer on the sheet"):
            s.model()

    def test_replacing_through_the_public_view_raises(self):
        """Assignment cannot tell "move this feature here" from "put a different feature here",
        so it mints — and the old reference must fail rather than transfer to the newcomer."""
        s, _a = self._two_holes()
        s.features[0] = HoleFeature(Frame((9, 9, 9), "z"), 1.0, depth=None, through=True)
        with pytest.raises(ValueError, match="no longer on the sheet"):
            s.model()

    def test_a_tuple_swap_raises(self):
        s, _a = self._two_holes()
        s.features[0], s.features[1] = s.features[1], s.features[0]
        with pytest.raises(ValueError, match="no longer on the sheet"):
            s.model()

    def test_a_slice_permutation_raises(self):
        s, _a = self._two_holes()
        s.features[:] = s.features[::-1]
        with pytest.raises(ValueError, match="no longer on the sheet"):
            s.model()

    def test_a_sanctioned_size_verb_does_not_invalidate(self):
        """The contrast case: `.depth()` replaces the frozen dataclass with an updated copy of
        the SAME feature, so it must preserve identity where public assignment mints."""
        s, a = self._two_holes()
        a.thread("M10")
        toleranced = [k[0].diameter for k in s.model().decorations if hasattr(k[0], "diameter")]
        assert toleranced == [10.0]


class TestTokensDivergedFromIndices:
    """Every test above runs on a sheet where token N happens to sit at index N, because nothing
    was removed. That coincidence hides an entire class of bug: converting a token back to an
    index, or vice versa, is a no-op on such a sheet and observable on no other.

    Deleting features first forces the two apart — the survivor's token is 2 while its index is
    0 — so a token/index confusion shows up here without needing a reorder at all.
    """

    @staticmethod
    def _offset():
        s = _sheet()
        s.hole(diameter=99, at=(-40, 0, 20), axis="z").depth(2)  # tokens 0 and 1, both discarded
        s.hole(diameter=98, at=(-35, 0, 20), axis="z").depth(2)
        a = s.hole(diameter=10, at=(-25, 0, 20), axis="z").depth(12)
        b = s.hole(diameter=6, at=(25, 0, 20), axis="z").depth(8)
        del s.features[0:2]
        assert s._token_at(0) == 2, "the fixture must actually diverge tokens from indices"
        return s, a, b

    def test_a_handle_resolves_by_token_not_position(self):
        s, a, _b = self._offset()
        assert s.features[a._i].diameter == 10

    def test_add_dimension_records_the_named_feature(self):
        s, a, _b = self._offset()
        s.add_dimension(a, "bore.diameter")
        assert s._added_dimensions[0]["token"] == s._token_at(0)

    def test_gdt_provenance_binds_to_the_named_feature(self):
        s, a, _b = self._offset()
        s.control(a).flatness(0.02)
        s._prepare()
        frame = next(f for f in s.features if f.kind == "control_frame")
        assert frame.origin.diameter == 10

    def test_a_tolerance_binds_to_the_named_feature(self):
        s, a, _b = self._offset()
        a.tolerance(0.05)
        assert [k[0].diameter for k in s.model().decorations] == [10.0]

    def test_a_section_cuts_through_the_named_feature(self):
        s, _a, b = self._offset()
        s.section(feature=b)
        assert s.model().decorations["section"] == 0.0

    def test_an_integer_ref_still_means_a_position(self):
        """The contrast that makes the rest meaningful: a raw index is POSITIONAL by definition,
        so `of(0)` names whatever sits at 0 — it is not a stale token."""
        s, a, _b = self._offset()
        assert s.of(0)._token == a._token
        s.features.reverse()
        assert s.of(0)._token != a._token


class TestIntegerRefs:
    """A raw integer is the positional addressing mode this epic is moving away from, but it
    remains a documented `of()` ref kind. #912 made negatives uniform across the three resolvers
    rather than adding a per-caller knob that would recreate the divergence it removed — so
    `add_dimension(-1, …)` now resolves where it previously raised. Pinned here as a decision."""

    @staticmethod
    def _two():
        s = _sheet()
        s.hole(diameter=10, at=(-25, 0, 20), axis="z").depth(12)
        s.hole(diameter=6, at=(25, 0, 20), axis="z").depth(8)
        return s

    def test_negative_indices_resolve_from_the_end_everywhere(self):
        s = self._two()
        assert s.of(-1)._i == 1
        s.add_dimension(-1, "bore.diameter")
        assert s._added_dimensions[0]["token"] == s._token_at(1)

    def test_an_out_of_range_index_raises_on_every_verb(self):
        s = self._two()
        for call in (
            lambda: s.of(2),
            lambda: s.of(-3),
            lambda: s.add_dimension(-3, "bore.diameter"),
        ):
            with pytest.raises(IndexError, match="out of range"):
                call()

    def test_a_bool_is_not_an_index(self):
        """`True` is an `int` subclass; accepting it as index 1 would make a mistyped flag
        silently dimension the second feature."""
        s = self._two()
        with pytest.raises(TypeError, match="must be a handle"):
            s.add_dimension(True, "bore.diameter")


class TestCrossSheetHandles:
    """A handle names a feature on the sheet that issued it. Accepting a foreign one would
    address by position in the worst possible way — a different list entirely."""

    @staticmethod
    def _pair():
        s1, s2 = _sheet(), _sheet()
        h1 = s1.hole(diameter=10, at=(-25, 0, 20), axis="z").depth(12)
        s2.hole(diameter=6, at=(25, 0, 20), axis="z").depth(8)
        return s1, s2, h1

    def test_add_dimension_rejects_a_foreign_handle(self):
        _s1, s2, h1 = self._pair()
        with pytest.raises(ValueError, match="different Sheet"):
            s2.add_dimension(h1, "bore.diameter")

    def test_control_rejects_a_foreign_handle(self):
        _s1, s2, h1 = self._pair()
        with pytest.raises(ValueError, match="different Sheet"):
            s2.control(h1)

    def test_of_rejects_a_foreign_handle(self):
        _s1, s2, h1 = self._pair()
        with pytest.raises(ValueError, match="different Sheet"):
            s2.of(h1)

    def test_datum_rejects_a_foreign_handle(self):
        _s1, s2, h1 = self._pair()
        with pytest.raises(ValueError, match="different Sheet"):
            s2.datum("A", h1)


class TestTokenAllocation:
    """Tokens are per-sheet and monotonic. Determinism matters because a script that emits and
    re-runs must allocate the same ones, and because a reused token would alias two features."""

    def test_tokens_are_never_reused_after_removal(self):
        s = _sheet()
        s.hole(diameter=10, at=(-25, 0, 20), axis="z")
        first = s._token_at(0)
        del s.features[0]
        s.hole(diameter=6, at=(25, 0, 20), axis="z")
        assert s._token_at(0) != first

    def test_two_sheets_allocate_independently(self):
        a, b = _sheet(), _sheet()
        a.hole(diameter=10, at=(-25, 0, 20), axis="z")
        b.hole(diameter=10, at=(-25, 0, 20), axis="z")
        assert a._token_at(0) == b._token_at(0), "per-sheet counters, so the same script is stable"

    def test_declaring_the_same_script_twice_allocates_the_same_tokens(self):
        def tokens():
            s = _sheet()
            _scn_hole(s)
            return [s._token_at(i) for i in range(len(s.features))]

        assert tokens() == tokens()


class TestCopySemantics:
    """`_Params.__getattr__` guards against copy/pickle rebuilding a handle without `__init__`,
    so both are supported contracts and the token must survive them."""

    def test_deepcopy_preserves_declared_state(self):
        s = _sheet()
        h, drive = _scn_hole(s)
        drive(h)
        assert _canonical(copy.deepcopy(s).model()) == _canonical(s.model())

    def test_pickle_preserves_declared_state(self):
        s = _sheet()
        h, drive = _scn_hole(s)
        drive(h)
        assert _canonical(pickle.loads(pickle.dumps(s)).model()) == _canonical(s.model())

    def test_a_deepcopied_sheet_allocates_without_colliding(self):
        """The counter must come across too — otherwise the copy re-mints a token the original
        already issued, and two features in the copy alias one reference."""
        s = _sheet()
        s.hole(diameter=10, at=(-25, 0, 20), axis="z")
        clone = copy.deepcopy(s)
        clone.hole(diameter=6, at=(25, 0, 20), axis="z")
        assert len({clone._token_at(i) for i in range(len(clone.features))}) == 2


# --------------------------------------------------------------------------------------------
# The ratchet
# --------------------------------------------------------------------------------------------


def _handle_returning_verbs() -> set[str]:
    """Public `Sheet` methods that construct and return one of `_HANDLE_CLASSES`."""
    tree = ast.parse(_SRC.read_text())
    (sheet,) = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Sheet"]
    found = set()
    for fn in [n for n in sheet.body if isinstance(n, ast.FunctionDef)]:
        if fn.name.startswith("_"):
            continue
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Return)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id in _HANDLE_CLASSES
            ):
                found.add(fn.name)
    return found


#: `Sheet` state that stores a reference to a declared feature. Each must be reached by at least
#: one scenario, proven at runtime below rather than asserted by eye.
_STATE_CARRYING_FEATURE_REFS = frozenset(
    {"_tolerances", "_gdt_src", "_section", "_added_dimensions", "_authored"}
)

#: `Sheet` state that stores no feature reference, so a reorder cannot affect it.
_STATE_WITHOUT_FEATURE_REFS = frozenset(
    {
        "_part",
        "_features",
        "_entries",
        "_opts",
        "_tables",
        "_auto_dimensions",
        # A bool: "the dimension(...) lines ARE the set". It carries no feature reference by
        # construction — it is the other half of `_auto_dimensions`, and classified beside it
        # for the same reason (#933). The set's MEMBERS live in `_authored`, which is
        # classified as reference-carrying and has its own scenario.
        "_authored_source",
    }
)


def _sheet_state_fields() -> set[str]:
    """Every ``self._x`` a `Sheet` method assigns — not just the constructor's.

    The first draft walked ``__init__`` alone, which a lazily created field evades entirely::

        def later(self, ref):
            self._new_ref = ref     # invisible to a constructor-only walk

    A field that only ever appears in the method that fills it is exactly the shape a new
    reference store would take, so the walk covers the whole class. Two evasions remain and are
    deliberate acts rather than oversights: ``setattr``/``__dict__`` writes, and burying a
    reference inside a field classified here as reference-free (``_opts``).
    """
    tree = ast.parse(_SRC.read_text())
    (sheet,) = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Sheet"]
    fields = set()
    for fn in [n for n in sheet.body if isinstance(n, ast.FunctionDef)]:
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign):
                fields |= {
                    t.attr
                    for t in node.targets
                    if isinstance(t, ast.Attribute)
                    and isinstance(t.value, ast.Name)
                    and t.value.id == "self"
                }
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Attribute):
                fields.add(node.target.attr)
    return fields


def test_every_sheet_state_field_is_classified():
    """The ratchet the first draft of this file lacked, and the review found the hole in.

    Enumerating handle-returning VERBS is not enough: `section(feature=…)` returns `Sheet` and
    still retains a feature reference, so restoring its positional bug passed all 76 tests. The
    generalisation is to enumerate the STATE — every field a reference can live in — because
    that is what the invariant is actually about. A new field forces a classification, and
    classifying it as reference-carrying forces a scenario via the test below.
    """
    assert _sheet_state_fields() == _STATE_CARRYING_FEATURE_REFS | _STATE_WITHOUT_FEATURE_REFS, (
        "Sheet grew or lost state — classify each new field as carrying feature references "
        "(and give it a scenario in _SCENARIOS) or not carrying them"
    )


def test_every_reference_carrying_state_field_is_exercised():
    """Runtime proof that the scenarios reach every field a reference can live in — the check
    that would have caught `_section` being unexercised while the docstring claimed otherwise."""
    reached = set()
    for scenario in _SCENARIOS.values():
        s = _sheet()
        obj, drive = scenario(s)
        drive(obj)
        reached |= {f for f in _STATE_CARRYING_FEATURE_REFS if getattr(s, f)}
    assert reached == _STATE_CARRYING_FEATURE_REFS, (
        f"no scenario populates {sorted(_STATE_CARRYING_FEATURE_REFS - reached)} — a reference "
        "stored there would not be covered by the reorder matrix"
    )


def test_dimension_intent_still_has_no_verbs():
    """`_scn_add_dimension`'s driver is a no-op, which is honest only while `DimensionIntent`
    has nothing to drive. #906 will restore `.pin()`/`.priority()`; this fails then, so the
    scenario gets a real driver rather than staying quietly vacuous."""
    tree = ast.parse(_SRC.read_text())
    (intent,) = [
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "DimensionIntent"
    ]
    verbs = {
        f.name
        for f in intent.body
        if isinstance(f, ast.FunctionDef) and not f.name.startswith("_")
    }
    assert verbs == set(), (
        f"DimensionIntent gained {sorted(verbs)} — give _scn_add_dimension a driver that calls "
        "it after the mutation, or the retained-object matrix does not cover this class"
    )


def test_every_handle_returning_verb_is_exercised():
    """Fail-closed. A new verb handing back a retained object must be given a scenario above —
    which forces its author to answer "what does this do across a reorder?", the question #910
    went three rounds without being asked."""
    assert _handle_returning_verbs() <= set(_SCENARIOS) | _SAME_PATH_AS_ENVELOPE, (
        "handle-returning Sheet verbs changed — add a scenario to _SCENARIOS (or, if the new "
        "verb reaches a handle by an already-covered route, to _SAME_PATH_AS_ENVELOPE)"
    )


def test_the_handle_class_roster_is_closed():
    """The second level of the ratchet: a new KIND of retained object. `_Control` was missed in
    #910 precisely because it derives from none of the classes the migration swept, so a roster
    keyed on the verb alone would not have caught it either."""
    tree = ast.parse(_SRC.read_text())
    classes = {
        n.name
        for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name not in ("Sheet", "_FeatureView")
    }
    assert classes == _HANDLE_CLASSES, (
        "a class in sheet.py is neither Sheet, the feature view, nor a known handle — if it is "
        "retained by a caller it needs a scenario in _SCENARIOS and an entry in _HANDLE_CLASSES"
    )


def test_only_one_resolver_bears_identity():
    """#912's structural half: the three ref-resolution paths that grew independently are now
    one. `_gdt_ref`'s was the one that leaked in #910; a fourth would reopen the class.

    Checked by AST rather than by searching the text — the first draft of this test did the
    latter and was vacuous, because each resolver's own docstring names `_declared_token` and
    so the substring was present whether or not the call was.
    """
    tree = ast.parse(_SRC.read_text())
    (sheet,) = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Sheet"]
    fns = {n.name: n for n in sheet.body if isinstance(n, ast.FunctionDef)}

    assert "_declared_token" in fns, "the single identity resolver is gone"
    for caller in ("_index_of", "_gdt_ref", "_feature_token"):
        calls = {
            node.func.attr
            for node in ast.walk(fns[caller])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert "_declared_token" in calls, (
            f"{caller} no longer calls the single resolver — identity resolution must not fork "
            "again; that fork is what let #910 through"
        )


def test_the_same_path_verbs_really_share_the_route():
    """`_SAME_PATH_AS_ENVELOPE` is a claim about code, so check it against the code.

    The list exists so twelve verbs do not each get a scenario testing the same two lines.
    That is only sound while the lines ARE the same — a verb that grows identity handling of
    its own, or appends more than one feature, silently loses its coverage and the ratchet
    reports success. So the shape is asserted rather than assumed (#922).
    """
    import ast
    import inspect
    import textwrap

    from draftwright.sheet import Sheet

    for name in sorted(_SAME_PATH_AS_ENVELOPE | {"envelope"}):
        src = textwrap.dedent(inspect.getsource(getattr(Sheet, name)))
        body = ast.parse(src).body[0].body
        # Drop the DOCSTRING only. Filtering every `ast.Expr` also drops
        # `self._features.append(...)`, which is an expression statement — the first draft
        # did that and then reported "appends 0 features" for every verb.
        statements = body[1:] if isinstance(body[0], ast.Expr) else body
        appends = [
            n
            for n in ast.walk(ast.Module(body=statements, type_ignores=[]))
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "append"
        ]
        assert len(appends) == 1, (
            f"Sheet.{name} appends {len(appends)} features; the envelope route declares "
            "exactly one, and a multi-feature verb needs its own scenario"
        )
        returned = statements[-1]
        assert isinstance(returned, ast.Return), f"Sheet.{name} does not end in a return"
        assert ast.unparse(returned.value) == "_Params(self, len(self._features) - 1)", (
            f"Sheet.{name} no longer returns the envelope handle verbatim "
            f"({ast.unparse(returned.value)}) — give it a scenario or restore the route"
        )
