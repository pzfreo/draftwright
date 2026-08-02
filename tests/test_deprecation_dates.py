"""Every deprecation names when it goes (#987 / ADR 0005 §4).

§4's rule is that a compat surface carries a tracking issue *and* a removal target, because
"a facade with no exit date is a failure mode, not a success". #720 applied that to seven
private aliases and deleted them. This asserts the same rule for everything else that warns.

The gap this closes is not that dates were never chosen — the v0.3.8 CHANGELOG *did* slate
the #817 plumbing for "~0.5.0". It is that the date lived in a release note while the message
the caller actually sees said nothing, so answering "what is left for the next release" meant
grepping three ADRs and a changelog, and still getting it wrong.

**Known blind spot.** This scans code that *warns*. A surface documented as deprecated but
emitting no warning is invisible here, by construction. `Drawing.export(svg=, dxf=)` used to be
exactly that — declared deprecated under v0.3.1's "Deprecated" heading and silent at runtime
for four minor releases — until #987 gave it a warning, which is what brought it into this
check's scope at all. `docs/deprecations.md` is the backstop for anything still in that state;
this file cannot be.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src" / "draftwright"

#: A removal STATEMENT — removal language followed by a version — not merely a version
#: anywhere in the message. The first cut accepted any `X.Y.Z`, so "Deprecated since 0.3.8"
#: or a Python-version note would have satisfied a test whose name promises it checks removal
#: (Codex #987 r1). Accepts "Removed in 0.5.0", "Expires at 0.4.0", and the gated form
#: "Removal gated on #707; target 0.6.0".
_REMOVAL = re.compile(r"(?:removed|removal|expires|expiry)\b[^.]{0,60}?\d+\.\d+(?:\.\d+)?", re.I)


def _is_deprecation(node: ast.AST) -> bool:
    """True if this call IS a deprecation announcement — the `@deprecated(...)` decorator or
    `warnings.warn(..., DeprecationWarning)`.

    Deliberately separate from reading the message: "is this a deprecation" and "can I read
    its text" are different questions, and conflating them let an unreadable message be
    silently treated as "not a deprecation" (Codex #987 r1).
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name) and func.id == "deprecated":
        return True
    if isinstance(func, ast.Attribute) and func.attr == "deprecated":
        return True
    # `warnings.warn(...)` specifically — not any object's `.warn()`, which would sweep in
    # unrelated logger calls that happen to take a second argument.
    is_warn = (
        isinstance(func, ast.Attribute)
        and func.attr == "warn"
        and isinstance(func.value, ast.Name)
        and func.value.id == "warnings"
    )
    if not is_warn:
        return False
    cats = list(node.args[1:]) + [k.value for k in node.keywords if k.arg == "category"]
    return any(isinstance(c, ast.Name) and c.id == "DeprecationWarning" for c in cats)


def _literal_text(node: ast.AST) -> str | None:
    """The statically-known text of a message argument, or None if it cannot be read.

    Handles f-strings by joining their constant parts and dropping the interpolations: the
    bare-role deprecation builds its message with `f"{verb}({role!r}): …"`, so treating only
    `ast.Constant` as a message made the scanner skip it SILENTLY — it says "Expires at 0.4.0"
    and was invisible to the check anyway.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        parts = [
            v.value
            for v in node.values
            if isinstance(v, ast.Constant) and isinstance(v.value, str)
        ]
        return "".join(parts) if parts else None
    return None


def _scan() -> list[tuple[str, str | None]]:
    """``(location, message-or-None)`` for every deprecation announcement under src/."""
    out: list[tuple[str, str | None]] = []
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if _is_deprecation(node):
                assert isinstance(node, ast.Call)
                msg = _literal_text(node.args[0]) if node.args else None
                out.append((f"{path.relative_to(_SRC)}:{node.lineno}", msg))
    return out


def test_every_deprecation_names_its_removal() -> None:
    bad: list[str] = []
    for where, msg in _scan():
        if msg is None:
            # An unreadable message is a FAILURE, not a skip: a deprecation built from a module
            # constant or a helper would otherwise vanish from the check entirely (Codex r1).
            bad.append(f"{where}: message is not a literal — the guard cannot read it")
        elif not _REMOVAL.search(msg):
            bad.append(f"{where}: {msg[:70]}…")
    assert not bad, (
        'deprecation(s) with no removal target — give each a version ("Removed in 0.5.0") or '
        'a gated target ("Removal gated on #707; target 0.6.0") in the message the CALLER sees, '
        "and add a row to docs/deprecations.md (#987 / ADR 0005 §4):\n  " + "\n  ".join(bad)
    )


#: The number of deprecation announcements the scanner must still find. Lower it ONLY when a
#: deprecation was genuinely removed, and say which — the point is that a silent drop means the
#: scanner broke, not that the codebase got cleaner.
#:
#: 11 → 9 when #720 removed the two #963 warnings (the bare dimension-role spelling and the
#: `dimension(kind=…, value=…)` call shape) at 0.4.0; those are raises now, which carry no
#: removal date to check. 9 → 11 when #987 gave the two legacy `Drawing.export` shapes the
#: warnings they had lacked since 0.3.1 — which is also what brought them into this check's
#: scope, since it can only see things that warn.
_EXPECTED_DEPRECATIONS = 11


def test_the_scanner_actually_matches_something() -> None:
    """Guard the guard: if `_is_deprecation` stopped recognising the call shapes — a decorator
    rename, a move to `warnings.warn(category=...)` — the test above would pass by finding
    nothing to check, which is the failure mode it exists to prevent."""
    found = len(_scan())
    assert found >= _EXPECTED_DEPRECATIONS, (
        f"the deprecation scanner found only {found}, expected at least "
        f"{_EXPECTED_DEPRECATIONS} — either a deprecation was removed (lower the constant and "
        "note which) or the scanner stopped recognising a call shape"
    )


def test_the_removal_pattern_rejects_a_bare_version() -> None:
    """The regex must require removal LANGUAGE, not just a version — negative fixtures, because
    the first cut passed on any `X.Y.Z` and so would have accepted every string below."""
    for text in (
        "Foo() is deprecated since 0.3.8; use bar().",
        "Foo() is deprecated (#817): use bar().",
        "Foo() requires Python 3.12.0.",
        "Foo() is deprecated. Removed in a future release.",
    ):
        assert not _REMOVAL.search(text), f"should NOT count as a removal statement: {text!r}"
    for text in (
        "Foo() is deprecated. Removed in 0.5.0.",
        "Foo('x'): use the id. Expires at 0.4.0.",
        "Foo() is deprecated. Removal gated on #707; target 0.6.0.",
    ):
        assert _REMOVAL.search(text), f"SHOULD count as a removal statement: {text!r}"
