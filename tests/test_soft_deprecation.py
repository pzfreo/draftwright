"""Soft deprecation: discouraged, supported, and NOT scheduled for removal (#1043).

`Sheet.auto_dimensions()` and `Sheet.add_dimension()` steer callers toward authored
dimensions, which is what `--script` emits and the only form where omission can mean
suppression (ADR 0016). They keep working indefinitely.

The category matters as much as the message. `docs/deprecations.md` carries ADR 0005 §4 —
a compat surface names a removal target, because "a facade with no exit date is a failure
mode" — and `tests/test_deprecation_dates.py` enforces it. Raising `DeprecationWarning` for
something we intend to keep would mean writing a removal date we do not mean.
"""

import pathlib
import subprocess
import sys
import warnings

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import Sheet, SoftDeprecationWarning, build_drawing


def _part():
    return Box(80, 60, 20) - Pos(0, 0, 0) * Cylinder(5, 20)


def test_soft_deprecation_is_not_a_deprecation_warning():
    """The load-bearing distinction, asserted directly.

    If this ever becomes a `DeprecationWarning`, it silently acquires an obligation under ADR
    0005 §4 to carry a removal target — and `test_deprecation_dates` would start scanning a
    message that promises no removal, because we do not intend one. Better to fail here, at
    the definition, than there with a confusing message.
    """
    assert issubclass(SoftDeprecationWarning, UserWarning)
    assert not issubclass(SoftDeprecationWarning, DeprecationWarning)


def _warn_auto(sheet):
    """Select the automatic source WITHOUT its warning reaching the caller's capture.

    `add_dimension` is only legal against an automatic set, so reaching it necessarily warns
    twice. The first cut of these tests wrapped both calls in one `pytest.warns` and asserted
    on the last record — which passes even if `add_dimension` stops warning entirely, because
    `auto_dimensions`' warning satisfies every assertion on its own. Verified: deleting
    `add_dimension`'s warning left all seven tests green (Codex #1045 r1).
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SoftDeprecationWarning)
        sheet.auto_dimensions()


def _trigger(verb, sheet, hole):
    """Call exactly ONE discouraged verb, with any prerequisite warning already absorbed."""
    if verb == "auto_dimensions":
        sheet.auto_dimensions()
    else:
        _warn_auto(sheet)
        sheet.add_dimension(hole, "bore.diameter")


@pytest.mark.parametrize("verb", ["auto_dimensions", "add_dimension"])
def test_the_discouraged_verbs_warn_without_promising_removal(verb):
    """Each names the replacement, and explicitly does NOT promise to go away — a user who
    reads the warning should learn that their code will keep working.

    Exactly one warning is captured, so each verb is tested on its own behaviour.
    """
    sheet = Sheet(_part())
    hole = sheet.hole(Pos(0, 0, 0) * Cylinder(5, 20))

    with pytest.warns(SoftDeprecationWarning) as rec:
        _trigger(verb, sheet, hole)

    assert len(rec) == 1, (
        f"expected exactly one warning from {verb}, got {len(rec)} — a prerequisite verb's "
        "warning is leaking into the capture and could satisfy these assertions by itself"
    )
    msg = str(rec[0].message)
    assert verb in msg, f"the warning must name the verb it came from: {msg}"
    assert "authored" in msg.lower(), f"the warning must name the replacement: {msg}"
    assert "not scheduled for removal" in msg.lower(), (
        f"the warning must say it is staying — that is what makes this soft: {msg}"
    )


@pytest.mark.parametrize("verb", ["auto_dimensions", "add_dimension"])
def test_the_warning_does_not_claim_a_removal_version(verb):
    """`test_deprecation_dates` requires removal LANGUAGE plus a version of every
    `DeprecationWarning`. These must not read like that, or a future reader will believe a
    removal is scheduled when none is.

    Parametrised over both verbs: the first cut checked only `auto_dimensions`, so
    `add_dimension` could have started promising a removal without this failing.
    """
    import re

    sheet = Sheet(_part())
    hole = sheet.hole(Pos(0, 0, 0) * Cylinder(5, 20))

    with pytest.warns(SoftDeprecationWarning) as rec:
        _trigger(verb, sheet, hole)

    msg = str(rec[0].message)
    assert not re.search(r"(?:removed|removal|expires)\b[^.]{0,60}?\d+\.\d+", msg, re.I), (
        f"{verb}'s message reads like a scheduled removal, which it is not: {msg}"
    )


def test_the_automatic_detected_path_is_not_discouraged():
    """The boundary most likely to be crossed by accident.

    `build_drawing(part)` IS automatic dimensioning, and that is the product's front door —
    point the CLI at a STEP and get a drawing. Only the `Sheet` declaration surface is
    steered, because an author who is already explicit about features should be equally
    explicit about dimensions.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("error", SoftDeprecationWarning)
        drawing = build_drawing(_part())

    assert drawing.annotations(), "the automatic path should still dimension the part"


def test_the_recommended_migration_does_not_nag():
    """`from_part()` seeds detected features and picks the automatic source *implicitly*;
    adding `dimension(...)` lines overrides it (#921). That is the recommended way to take
    over a detected drawing, so it must not warn — a migration path that scolds you for
    taking it is worse than no advice.
    """
    part = _part()
    with warnings.catch_warnings():
        warnings.simplefilter("error", SoftDeprecationWarning)
        sheet = Sheet.from_part(part)
        feature = next(f for f in sheet.features if f.kind == "hole")
        sheet.dimension(feature, "bore.diameter")
        sheet.build()


def test_authored_dimensions_is_silent():
    """The recommended surface itself, obviously, must not warn."""
    part = _part()
    with warnings.catch_warnings():
        warnings.simplefilter("error", SoftDeprecationWarning)
        sheet = Sheet(part)
        hole = sheet.hole(Pos(0, 0, 0) * Cylinder(5, 20))
        sheet.envelope()
        sheet.authored_dimensions()
        sheet.dimension(hole, "bore.diameter")
        sheet.build()


@pytest.mark.parametrize("verb", ["auto_dimensions", "add_dimension"])
def test_the_warning_blames_the_caller_not_the_library(verb):
    """`stacklevel=2`, guarded.

    A warning attributed to `sheet.py` tells the user nothing: they cannot see which of their
    lines caused it, which for a "stop using this" message is the entire value. The
    implementation was already correct, but nothing failed if it stopped being — mutating
    either site to `stacklevel=1` left every other guard green (Codex #1045 r2).
    """
    sheet = Sheet(_part())
    hole = sheet.hole(Pos(0, 0, 0) * Cylinder(5, 20))

    with pytest.warns(SoftDeprecationWarning) as rec:
        _trigger(verb, sheet, hole)

    assert rec[0].filename == __file__, (
        f"{verb}'s warning is attributed to {rec[0].filename}, not the caller. A user cannot "
        "act on a warning that points at library internals."
    )


def test_reaching_the_warning_category_does_not_import_the_cad_kernel():
    """A warning class must not drag build123d behind it.

    It lived in `_core`, which imports build123d — so `from draftwright import
    SoftDeprecationWarning` cost ~6 s, and the pytest `filterwarnings` entry naming it paid
    that on EVERY invocation, including `--collect-only` and the smoke tier.

    Run in a subprocess because this test module has already imported the kernel; checking
    `sys.modules` in-process would pass regardless of where the class lives, which is why the
    original regression was invisible to the suite (Codex #1045 r2).
    """
    probe = (
        "import sys; from draftwright import SoftDeprecationWarning; "
        "print('build123d' in sys.modules or 'OCP' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)

    assert out.stdout.strip() == "False", (
        "importing SoftDeprecationWarning pulled in the CAD kernel — it belongs in a "
        "dependency-free leaf, not beside code that imports build123d"
    )


def test_the_category_is_defined_exactly_once():
    """One class, one place.

    `_core` kept a second, independent definition after the class was moved to the
    dependency-free leaf — a `git checkout` used to undo a mutation clobbered the deletion,
    and it was committed back (Codex #1045 r3). Two classes with one name is worse than
    either alone: `Sheet` emits the public one, so anyone filtering or catching the other
    silently matches nothing, and the kernel-dragging definition the move existed to remove
    is still there.

    Scans the source rather than comparing imports, because the two-class state is invisible
    to every behavioural test — `pytest.warns(SoftDeprecationWarning)` passes either way.
    """
    root = pathlib.Path(__file__).resolve().parent.parent / "src" / "draftwright"
    definitions = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "class SoftDeprecationWarning" in path.read_text(encoding="utf-8")
    ]

    assert definitions == ["_warnings.py"], (
        f"SoftDeprecationWarning is defined in {definitions} — it belongs only in the "
        "dependency-free leaf. A second definition matches nothing that the first catches."
    )
