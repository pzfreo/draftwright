"""Draftwright must decide what to do with every capability the installed package ships.

This is the other half of splitting the recogniser inventory check. ``recogniser_contract``
no longer fails when the package ships a family Draftwright has not declared, because that
direction cannot break anything here and blocking on it made the *provider* unreleasable
until its consumer caught up. The obligation to decide did not go away — it moved here,
where the decision belongs.

**This file is deliberately outside the downstream canary.** ``check_downstream.py`` in
b123d-recognisers runs ``tests/test_recogniser_capabilities.py`` and
``tests/test_import_boundaries.py`` against a candidate wheel; those two are the
*compatibility* contract, and a candidate that adds a family has not broken compatibility.
Adoption is a Draftwright schedule question, so it is checked in Draftwright's own CI and
nowhere else. Adding this module to that list would restore exactly the coupling the split
removed.
"""

from __future__ import annotations

from draftwright.recogniser_contract import pending_family_declarations


def test_every_installed_package_family_has_a_declaration() -> None:
    """The installed package's families are all declared, so nothing is silently ignored.

    When this fails, the installed ``b123d-recognisers`` has grown a recogniser family and
    the choice of what Draftwright does with it is outstanding. Declare it in
    ``recogniser_contract._FAMILIES`` — as ``supported`` with an IR adapter, DSL kind and
    renderer, or as ``geometry-only`` if the evidence informs critique without becoming a
    drafting feature.

    Deciding "not yet" is legitimate, but it is still a decision and still gets written
    down: raise or reference a tracking issue, then declare the family with the disposition
    that says so. What this test refuses to allow is the family going unnoticed.
    """

    pending = pending_family_declarations()

    assert pending == [], (
        f"installed b123d-recognisers ships {len(pending)} undeclared "
        f"recogniser {'family' if len(pending) == 1 else 'families'}: {pending}. "
        "Declare each in recogniser_contract._FAMILIES; see this module's docstring."
    )
