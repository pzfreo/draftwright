"""Every deprecation names when it goes (#987 / ADR 0005 §4).

§4's rule is that a compat surface carries a tracking issue *and* a removal target, because
"a facade with no exit date is a failure mode, not a success". #720 applied that to seven
private aliases and deleted them. This asserts the same rule for everything else that warns.

The gap this closes is not that dates were never chosen — the v0.3.8 CHANGELOG *did* slate
the #817 plumbing for "~0.5.0". It is that the date lived in a release note while the message
the caller actually sees said nothing, so answering "what is left for the next release" meant
grepping three ADRs and a changelog, and still getting it wrong.

Scans both forms a deprecation takes here: the PEP 702 ``@deprecated`` decorator (which also
gets type-checkers to flag call sites) and a runtime ``warnings.warn(..., DeprecationWarning)``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src" / "draftwright"

#: A removal statement is either a version ("Removed in 0.5.0", "Expires at 0.4.0") or an
#: explicit gate on the work that must land first ("Removal gated on #707"). The second form
#: exists for `place_dim`: ADR 0012 keeps it as the sanctioned raw-coordinate escape hatch
#: until the full recompose lands, so dating it to a release whose replacement does not exist
#: would be a promise the engine cannot keep. A gate names the blocker instead of inventing a
#: version — which is still an answer to "when", and still checkable.
_REMOVAL = re.compile(r"\b\d+\.\d+\.\d+\b|removal gated on #\d+", re.IGNORECASE)


def _message_of(node: ast.AST) -> str | None:
    """The literal message string of a `@deprecated(...)` or `warnings.warn(..., DeprecationWarning)`
    call, or None if this isn't one. Implicitly-concatenated string parts arrive already joined."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name == "deprecated":
        pass
    elif name == "warn":
        kinds = [a for a in node.args[1:]] + [
            k.value for k in node.keywords if k.arg == "category"
        ]
        if not any(getattr(k, "id", None) == "DeprecationWarning" for k in kinds):
            return None
    else:
        return None
    if not node.args:
        return None
    return _literal_text(node.args[0])


def _literal_text(node: ast.AST) -> str | None:
    """The statically-known text of a message argument.

    Handles f-strings by joining their constant parts and dropping the interpolations: the
    bare-role deprecation builds its message with `f"{verb}({role!r}): …"`, so treating only
    `ast.Constant` as a message made the scanner skip it SILENTLY — it says "Expires at 0.4.0"
    and was invisible to the check anyway. A guard that quietly ignores the messages it cannot
    parse is the same failure it is testing for.
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


def test_every_deprecation_names_its_removal() -> None:
    undated: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            msg = _message_of(node)
            if msg is not None and not _REMOVAL.search(msg):
                undated.append(f"{path.relative_to(_SRC)}:{node.lineno}: {msg[:70]}…")
    assert not undated, (
        'deprecation(s) with no removal target — give each a version ("Removed in 0.5.0") or '
        'an explicit gate ("Removal gated on #707") in the message the CALLER sees, and add a '
        "row to docs/deprecations.md (#987 / ADR 0005 §4):\n  " + "\n  ".join(undated)
    )


def test_the_scanner_actually_matches_something() -> None:
    """Guard the guard: if `_message_of` stopped recognising the call shapes — a decorator
    rename, a move to `warnings.warn(category=...)` — the test above would pass by finding
    nothing to check, which is the failure mode it exists to prevent."""
    found = 0
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found += sum(1 for n in ast.walk(tree) if _message_of(n) is not None)
    assert found >= 8, (
        f"the deprecation scanner found only {found} messages — has the shape changed?"
    )
