"""`DimensionRole` must stay in step with the measurements the IR actually carries (#963).

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

from draftwright.model import DimensionRole

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


def _canonical_spellings() -> set[str]:
    """What a caller should write for each parameter: the id, or — where the id carries a
    variant that `axis=` supplies separately — the bare role."""
    return {role if disc else f"{role}.{kind}" for role, kind, disc in _ir_parameters()}


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
    allowed = set(get_args(DimensionRole))
    missing = _canonical_spellings() - allowed
    assert not missing, f"canonical spellings missing from DimensionRole: {sorted(missing)}"


def test_bare_roles_are_deliberately_absent():
    """The bare role is the FAMILY spelling and is deprecated (#963): it selects every
    parameter carrying it, which is how `dimension(step, "step")` declared two dimensions
    silently. It stays accepted at runtime for one release, but it is not what the type
    advertises — listing it would recommend the thing being retired."""
    allowed = set(get_args(DimensionRole))
    canonical = _canonical_spellings()
    bare = {role for role, _, disc in _ir_parameters() if not disc}
    assert not (bare & allowed) - canonical, (
        f"deprecated bare roles listed: {sorted((bare & allowed) - canonical)}"
    )


def test_no_invented_members():
    """The other direction: a member that names nothing offers a caller a role the engine
    will reject at runtime, which is worse than no completion at all."""
    allowed = set(get_args(DimensionRole))
    real = _canonical_spellings() | _SYNTHESISED
    assert not (allowed - real), (
        f"DimensionRole lists {sorted(allowed - real)}, which no DimParameter produces"
    )


def test_the_synthesised_roles_are_stated_not_smuggled():
    """`location` is in the alias but is not a `DimParameter`. It is listed in `_SYNTHESISED`
    so that exemption is visible here rather than being an unexplained gap in the diff above."""
    assert _SYNTHESISED <= set(get_args(DimensionRole))
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
