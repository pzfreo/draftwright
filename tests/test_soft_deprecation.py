"""Soft deprecation: discouraged, supported, and NOT scheduled for removal (#1043).

`Sheet.auto_dimensions()` and `Sheet.add_dimension()` steer callers toward authored
dimensions, which is what `--script` emits and the only form where omission can mean
suppression (ADR 0016). They keep working indefinitely.

The category matters as much as the message. `docs/deprecations.md` carries ADR 0005 §4 —
a compat surface names a removal target, because "a facade with no exit date is a failure
mode" — and `tests/test_deprecation_dates.py` enforces it. Raising `DeprecationWarning` for
something we intend to keep would mean writing a removal date we do not mean.
"""

import warnings

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import Sheet, build_drawing
from draftwright._core import SoftDeprecationWarning


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


@pytest.mark.parametrize("verb", ["auto_dimensions", "add_dimension"])
def test_the_discouraged_verbs_warn_without_promising_removal(verb):
    """Each names the replacement, and explicitly does NOT promise to go away — a user who
    reads the warning should learn that their code will keep working."""
    sheet = Sheet(_part())
    hole = sheet.hole(Pos(0, 0, 0) * Cylinder(5, 20))

    with pytest.warns(SoftDeprecationWarning) as rec:
        if verb == "auto_dimensions":
            sheet.auto_dimensions()
        else:
            # `add_dimension` only means something against an automatic set, so reaching it
            # necessarily warns twice. The last warning is the one under test.
            sheet.auto_dimensions()
            sheet.add_dimension(hole, "bore.diameter")

    msg = str(rec[-1].message)
    assert "authored" in msg.lower(), f"the warning must name the replacement: {msg}"
    assert "not scheduled for removal" in msg.lower(), (
        f"the warning must say it is staying — that is what makes this soft: {msg}"
    )


def test_the_warning_does_not_claim_a_removal_version():
    """`test_deprecation_dates` requires removal LANGUAGE plus a version of every
    `DeprecationWarning`. These must not read like that, or a future reader will believe a
    removal is scheduled when none is.
    """
    import re

    sheet = Sheet(_part())
    with pytest.warns(SoftDeprecationWarning) as rec:
        sheet.auto_dimensions()

    msg = str(rec[0].message)
    assert not re.search(r"(?:removed|removal|expires)\b[^.]{0,60}?\d+\.\d+", msg, re.I), (
        f"the message reads like a scheduled removal, which it is not: {msg}"
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
