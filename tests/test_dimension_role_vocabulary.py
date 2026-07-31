"""`DimensionParameterId` must stay in step with the measurements the IR actually carries (#963).

`Sheet.dimension(feature, role)` used to take a bare `str`: no completion, no type checking,
and no way to see the options without running the code. It now takes a `Literal`, which is how
this codebase already spells a closed string vocabulary (`ParamKind`, `Axis`, `pmi=`,
`severity=`).

A hand-maintained `Literal` rots the moment a detector adds a role, and rots *silently* — the
symptom is a caller being told a valid measurement is invalid. So the truth is derived here
from the `DimParameter(...)` construction sites rather than trusted, the same way
`parameter_id` is derived rather than hand-authored.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import get_args

from draftwright.model import DimensionParameterId

_IR = Path(__file__).resolve().parents[1] / "src" / "draftwright" / "model" / "ir.py"

#: Reachable through `dimension(...)` but not a `DimParameter`: synthesised from the planner
#: plus a datum, and only on features `planner.location_datum` deems eligible (#876).
_SYNTHESISED = {"location"}


def _ir_parameters() -> set[tuple[str, str, bool]]:
    """``(role, kind, discriminated)`` for every `DimParameter(kind, role, ...)` in `ir.py`."""
    out: set[tuple[str, str, bool]] = set()
    for node in ast.walk(ast.parse(_IR.read_text(encoding="utf-8"))):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "DimParameter"):
            continue
        if len(node.args) >= 2 and all(isinstance(a, ast.Constant) for a in node.args[:2]):
            disc = any(kw.arg == "discriminator" for kw in node.keywords) or len(node.args) >= 4
            out.add((node.args[1].value, node.args[0].value, disc))
    return out


def _discriminated_ids() -> set[str]:
    """Full ids for discriminated parameters (`grid_pitch.length.row`)."""
    out = set()
    for node in ast.walk(ast.parse(_IR.read_text(encoding="utf-8"))):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "DimParameter"):
            continue
        d = next(
            (
                kw.value.value
                for kw in node.keywords
                if kw.arg == "discriminator" and isinstance(kw.value, ast.Constant)
            ),
            None,
        )
        if d and len(node.args) >= 2:
            out.add(f"{node.args[1].value}.{node.args[0].value}.{d}")
    return out


def _canonical_spellings() -> set[str]:
    """What a caller should write for each parameter: the id, or — where the id carries a
    variant that `axis=` supplies separately — the bare role."""
    # A discriminated parameter's canonical spelling is its FULL id; its `role.kind` prefix
    # names nothing on its own, so it is not a spelling anyone should write.
    return {
        f"{role}.{kind}" for role, kind, disc in _ir_parameters() if not disc
    } | _discriminated_ids()


def test_the_extraction_finds_the_construction_sites():
    """Guard the guard. If the `DimParameter(...)` call shape changes, this walk silently
    finds nothing and every assertion below passes vacuously — which is the failure mode a
    derived-truth test has to rule out first."""
    pairs = _ir_parameters()
    assert len(pairs) > 20, f"only found {len(pairs)} parameters — has DimParameter changed?"
    assert ("bore", "diameter", False) in pairs


def test_every_ir_measurement_is_nameable():
    """Every parameter id must type — that is the canonical spelling (#963), so a caller
    naming one must never get a type error for a measurement that works."""
    allowed = set(get_args(DimensionParameterId))
    missing = _canonical_spellings() - allowed
    assert not missing, f"canonical spellings missing from DimensionParameterId: {sorted(missing)}"


def test_bare_roles_are_deliberately_absent():
    """The bare role is the FAMILY spelling and is deprecated (#963): it selects every
    parameter carrying it, which is how `dimension(step, "step")` declared two dimensions
    silently. It stays accepted at runtime for one release, but it is not what the type
    advertises — listing it would recommend the thing being retired."""
    allowed = set(get_args(DimensionParameterId))
    canonical = _canonical_spellings()
    bare = {role for role, _, disc in _ir_parameters() if not disc}
    assert not (bare & allowed) - canonical, (
        f"deprecated bare roles listed: {sorted((bare & allowed) - canonical)}"
    )


def test_no_invented_members():
    """The other direction: a member that names nothing offers a caller a role the engine
    will reject at runtime, which is worse than no completion at all."""
    allowed = set(get_args(DimensionParameterId))
    real = _canonical_spellings() | _SYNTHESISED
    assert not (allowed - real), (
        f"DimensionParameterId lists {sorted(allowed - real)}, which no DimParameter produces"
    )


def test_the_synthesised_roles_are_stated_not_smuggled():
    """`location` is in the alias but is not a `DimParameter`. It is listed in `_SYNTHESISED`
    so that exemption is visible here rather than being an unexplained gap in the diff above."""
    assert _SYNTHESISED <= set(get_args(DimensionParameterId))
    assert _SYNTHESISED.isdisjoint({role for role, _, _ in _ir_parameters()})


def test_a_real_role_resolves_and_an_invented_one_raises():
    """The alias is an authoring aid, not a second decision site: `_resolve_measurement`
    stays authoritative, so a listed role must actually work end to end."""
    import pytest
    from build123d import Box, Cylinder, Pos

    from draftwright import Sheet

    sheet = Sheet.from_part(
        Box(80, 50, 8) - Pos(-20, 0, 0) * Cylinder(4, 20), title="T", number="N"
    ).authored_dimensions()
    hole = next(f for f in sheet.features if f.kind == "hole")

    sheet.dimension(hole, "bore.diameter")  # listed, and it resolves
    with pytest.raises(ValueError, match="no 'diameter' measurement"):
        sheet.dimension(hole, "diameter")  # type: ignore[call-overload]


class TestTheCanonicalSpellingIsEnforced:
    """The BEHAVIOUR, not just the vocabulary (#965 review).

    The tests above check `DimensionParameterId`'s contents; deleting the refuse/warn/normalise
    branch in `_resolve_measurement` would leave every one of them passing. These fail if it
    goes — which is the point of having them.
    """

    @staticmethod
    def _shaft_sheet():
        from build123d import Cylinder, Pos

        from draftwright import Sheet

        shaft = Cylinder(15, 20) + Pos(0, 0, 17.5) * Cylinder(10, 15)
        sheet = Sheet.from_part(shaft, title="T", number="N").authored_dimensions()
        return sheet, next(f for f in sheet.features if f.kind == "step")

    @staticmethod
    def _hole_sheet(verb="dimension"):
        from build123d import Box, Cylinder, Pos

        from draftwright import Sheet

        part = Box(80, 50, 8) - Pos(-20, 0, 0) * Cylinder(4, 20)
        sheet = Sheet.from_part(part, title="T", number="N")
        sheet.authored_dimensions() if verb == "dimension" else sheet.auto_dimensions()
        return sheet, next(f for f in sheet.features if f.kind == "hole")

    def test_a_family_naming_two_measurements_is_refused(self):
        """The defect this was written for: `dimension(step, "step")` used to declare BOTH
        `step.length` and `step.diameter` and say nothing, inside a set whose semantics is
        that omission means suppression."""
        import pytest

        sheet, step = self._shaft_sheet()
        with pytest.raises(ValueError) as exc:
            sheet.dimension(step, "step")  # type: ignore[call-overload]
        # Names them, so the fix is a choice rather than a guess.
        assert "step.diameter" in str(exc.value) and "step.length" in str(exc.value)

    def test_the_members_remain_individually_nameable(self):
        """Refusing the family must not cost the ability to say which one you meant."""
        sheet, step = self._shaft_sheet()
        sheet.dimension(step, "step.length")
        assert sheet._authored[-1]["role"] == "step.length"

    def test_a_singleton_family_warns_and_is_normalised(self):
        import warnings

        sheet, hole = self._hole_sheet()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            sheet.dimension(hole, "bore")  # type: ignore[call-overload]
        assert sheet._authored[-1]["role"] == "bore.diameter"
        assert any(issubclass(w.category, DeprecationWarning) for w in caught)

    def test_the_warning_points_at_the_caller_not_at_draftwright(self):
        """A deprecation reported against the library's own source tells the reader nothing
        about which of their lines to change. `dimension` reaches the resolver two frames
        deeper than `add_dimension` (through the transitional dispatcher and
        `_authored_dimension`), so one shared stacklevel cannot serve both — and asserting
        only that a warning EXISTS does not catch it (#965 review)."""
        import warnings

        for verb in ("dimension", "add_dimension"):
            sheet, hole = self._hole_sheet(verb)
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                getattr(sheet, verb)(hole, "bore")  # type: ignore[call-overload]
            dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
            assert dep, verb
            assert Path(dep[0].filename).name == Path(__file__).name, (
                f"{verb}: warning blamed {dep[0].filename}, not the calling test"
            )

    def test_the_handles_answer_what_they_can_be_asked_for(self):
        """`dimension_ids()` is the runtime half of the discoverability #963 is about, and it lists
        the CANONICAL spellings — so what it returns is what `dimension()` accepts."""
        from build123d import Box

        from draftwright import Sheet

        sheet = Sheet(Box(80, 50, 8))
        hole = sheet.hole(diameter=8, at=(0, 0, 4), axis="z")
        assert "bore.diameter" in hole.dimension_ids()
        assert (
            "bore" not in hole.dimension_ids()
        )  # the deprecated family spelling is not advertised
        for role in hole.dimension_ids():
            sheet.dimension(hole, role)  # every listed role must actually resolve

    def test_add_dimension_normalises_identically(self):
        """The two verbs address a measurement through one resolver, so normalisation must
        not be something only the authored path gets."""
        import warnings

        sheet, hole = self._hole_sheet("add_dimension")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            sheet.add_dimension(hole, "bore")  # type: ignore[call-overload]
        assert sheet._added_dimensions[-1]["role"] == "bore.diameter"

    def test_the_canonical_spelling_is_silent(self):
        """A deprecation that fires on the recommended spelling trains people to ignore it."""
        import warnings

        sheet, hole = self._hole_sheet()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            sheet.dimension(hole, "bore.diameter")
        assert not [w for w in caught if issubclass(w.category, DeprecationWarning)]

    def test_every_role_a_grid_pattern_advertises_actually_resolves(self):
        """The contract `dimension_ids()` states — what it lists is what `dimension()` wants — held
        for a hole and NOT for a grid pattern, which advertised a bare `"grid_pitch"` that
        `dimension()` then refused as ambiguous (#965 review).

        This calls `dimension_ids()` on the handle an EMITTED SCRIPT binds. An earlier version
        reconstructed the expected answer from `p.parameter_id` instead, so it passed while
        the method it claimed to guard was broken — a guard that bypasses its own subject
        proves only that the author knows what the answer should be.
        """
        import pytest
        from build123d import Box, Cylinder, Pos

        from draftwright.builder import detect_part_model
        from draftwright.sheet_emit import emit_sheet_script

        part = Box(160, 120, 12)
        for x in (-45, 0, 45):
            for y in (-30, 30):
                part -= Pos(x, y, 0) * Cylinder(3, 30)
        model = detect_part_model(part)
        pattern = next((f for f in model.features if f.kind == "pattern"), None)
        if pattern is None or not any(p.discriminator for p in pattern.parameters()):
            pytest.skip("fixture stopped detecting a discriminated grid pattern")

        src = emit_sheet_script(model, "part", "s", title="T", number="N")
        ns: dict = {"part": part}
        exec(compile(src[: src.index("sheet.export(")], "<emit>", "exec"), ns)  # noqa: S102
        handle = ns[next(k for k in ns if k.startswith("pattern"))]

        advertised = handle.dimension_ids()
        assert "grid_pitch.length.row" in advertised, advertised
        assert "grid_pitch" not in advertised, "the ambiguous bare role is still advertised"
        for role in advertised:
            ns["sheet"].dimension(handle, role)  # every advertised spelling must resolve

    def test_a_discriminated_id_and_a_conflicting_axis_is_refused(self):
        """The id already names the variant, so an `axis=` disagreeing with it is a
        contradiction rather than something to silently prefer one side of."""
        import pytest
        from build123d import Box, Cylinder, Pos

        from draftwright import Sheet

        part = Box(160, 120, 12)
        for x in (-45, 0, 45):
            for y in (-30, 30):
                part -= Pos(x, y, 0) * Cylinder(3, 30)
        sheet = Sheet.from_part(part, title="T", number="N").authored_dimensions()
        pattern = next((f for f in sheet.features if f.kind == "pattern"), None)
        if pattern is None or not any(p.discriminator for p in pattern.parameters()):
            pytest.skip("fixture stopped detecting a discriminated grid pattern")
        with pytest.raises(ValueError, match="already names"):
            sheet.dimension(pattern, "grid_pitch.length.row", axis="col")

    def test_the_bare_role_with_an_axis_still_works_for_existing_scripts(self):
        """`grid_pitch`'s id carries the variant (`grid_pitch.length.row`) that `axis=`
        supplies separately, so the bare role IS its canonical spelling. An early cut of the
        refusal counted those variants as two measurements and fired in place of the older,
        more useful `axis=` error (#965 review)."""
        import pytest
        from build123d import Box, Cylinder, Pos

        from draftwright import Sheet

        part = Box(160, 120, 12)
        for x in (-45, 0, 45):
            for y in (-30, 30):
                part -= Pos(x, y, 0) * Cylinder(3, 30)
        sheet = Sheet.from_part(part, title="T", number="N").authored_dimensions()
        pattern = next(f for f in sheet.features if f.kind == "pattern")
        if not any(p.role == "grid_pitch" for p in pattern.parameters()):
            pytest.skip("fixture stopped detecting a grid pattern")

        with pytest.raises(ValueError, match="ambiguous"):
            sheet.dimension(pattern, "grid_pitch")
        sheet.dimension(pattern, "grid_pitch", axis="row")
        assert sheet._authored[-1]["role"] == "grid_pitch"

    def test_a_legacy_spelling_reaches_the_script_canonically(self):
        """Why normalisation is at the facade rather than only in the error message: the
        emitter writes what was stored, so before this an emitted script's dialect depended
        on how its source model was authored."""
        import warnings

        from draftwright.sheet_emit import emit_sheet_script

        sheet, hole = self._hole_sheet()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            sheet.dimension(hole, "bore")  # type: ignore[call-overload]
        src = emit_sheet_script(sheet.model(), "part", "s", title="T", number="N")
        assert '"bore.diameter"' in src
        assert '"bore")' not in src

    def test_the_generated_header_advertises_a_route_that_runs(self):
        """The header first pointed at `feature.parameters()`, which raises on the facade
        handles a generated script actually binds — advertising discoverability that did not
        work, in the artefact the issue is about (#965 review). Executed, not grepped."""
        from build123d import Box, Cylinder, Pos

        from draftwright.builder import detect_part_model
        from draftwright.sheet_emit import emit_sheet_script

        part = Box(80, 50, 16) - Pos(-20, 0, 0) * Cylinder(4, 40)
        src = emit_sheet_script(detect_part_model(part), "part", "s", title="T", number="N")
        assert "<name>.dimension_ids()" in src

        ns: dict = {"part": part}
        body = src[: src.index("sheet.export(")].replace("\npart\n", "\n", 1)
        exec(compile(body, "<emit>", "exec"), ns)  # noqa: S102 — our own generated script
        handle = ns["hole1"]
        assert handle.dimension_ids(), "the advertised route returned nothing"
        assert "bore.diameter" in handle.dimension_ids()


def test_the_package_ships_its_typing_marker():
    """PEP 561: without `py.typed`, an installed draftwright is untyped to consumers and
    every annotation in it — these Literals, `ParamKind`, `pmi=`, `severity=` — resolves to
    `Any` outside this repo. The marker is what makes the typing in this change reach the
    people it was added for (#965 review); a mypy run inside the source tree does not prove
    it, because there the modules are found as source rather than as an installed package."""
    import draftwright

    marker = Path(draftwright.__file__).parent / "py.typed"
    assert marker.exists(), "py.typed is missing — the package is untyped to its consumers"
