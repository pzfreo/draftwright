"""Draftwright must decide what to do with every capability the installed package ships.

This is the other half of splitting the recogniser inventory check. ``recogniser_contract``
no longer fails when the package ships a family Draftwright has not declared, because that
direction cannot break anything here and blocking on it made the *provider* unreleasable
until its consumer caught up. The obligation to decide did not go away — it moved here,
where the decision belongs.

Adoption is a Draftwright schedule question, so it is checked in Draftwright's ordinary CI. An
additive package family does not break the runtime compatibility join, but advancing the exact
dependency pin cannot silently leave that family undecided.
"""

from __future__ import annotations

from draftwright.recogniser_contract import pending_family_declarations


def test_every_installed_package_family_has_a_declaration() -> None:
    """The installed package's families are all declared, so nothing is silently ignored.

    When this fails, the installed ``quiddity`` has grown a recogniser family and
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
        f"installed quiddity ships {len(pending)} undeclared "
        f"recogniser {'family' if len(pending) == 1 else 'families'}: {pending}. "
        "Declare each in recogniser_contract._FAMILIES; see this module's docstring."
    )
