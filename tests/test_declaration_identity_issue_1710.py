"""Build-scoped declaration identity follows intent, never a list position (#1710)."""

import pytest
from build123d import Box

from draftwright import Sheet
from draftwright.model import DeclarationIdentity


def _sheet() -> Sheet:
    sheet = Sheet(Box(40, 30, 10))
    sheet.authored_dimensions()
    return sheet


def test_identity_survives_fluent_replacement_and_feature_reordering() -> None:
    sheet = _sheet()
    first = sheet.hole(diameter=4, at=(-8, 0, 0), axis="z").identify("declaration:1")
    second = sheet.hole(diameter=6, at=(8, 0, 0), axis="z").identify("declaration:2")

    first.depth(5)
    sheet.features.reverse()
    model = sheet.model()

    assert model.decorations[(sheet.features[1], "declaration_identity")] == DeclarationIdentity(
        "declaration:1"
    )
    assert model.decorations[(sheet.features[0], "declaration_identity")] == DeclarationIdentity(
        "declaration:2"
    )
    assert sheet.by_declaration("declaration:1")._token == first._token
    assert sheet.by_declaration("declaration:2")._token == second._token


def test_public_replacement_withdraws_identity_instead_of_retargeting_it() -> None:
    sheet = _sheet()
    sheet.hole(diameter=4, at=(-8, 0, 0), axis="z").identify("declaration:1")
    sheet.hole(diameter=6, at=(8, 0, 0), axis="z")
    sheet.features[0] = sheet.features[1]
    del sheet.features[1]

    model = sheet.model()

    assert not any(key[1:] == ("declaration_identity",) for key in model.decorations)
    with pytest.raises(ValueError, match="found 0"):
        sheet.by_declaration("declaration:1")
    assert sheet.features[0].diameter == 6


def test_live_declaration_ids_are_unique_but_withdrawn_ids_can_be_reused() -> None:
    sheet = _sheet()
    first = sheet.hole(diameter=4, at=(-8, 0, 0), axis="z").identify("declaration:1")
    second = sheet.hole(diameter=6, at=(8, 0, 0), axis="z")

    with pytest.raises(ValueError, match="duplicate declaration_id"):
        second.identify("declaration:1")

    del sheet.features[first._i]
    second.identify("declaration:1")
    assert sheet.by_declaration("declaration:1")._token == second._token


def test_identity_validates_bounded_provenance_and_run_local_occurrences() -> None:
    assert DeclarationIdentity(
        "declaration:1",
        provenance="detected-geometry",
        occurrence_ids=("holes:1",),
    ).occurrence_ids == ("holes:1",)

    with pytest.raises(ValueError, match="non-empty declaration_id"):
        DeclarationIdentity("")
    with pytest.raises(ValueError, match="surrounding whitespace"):
        DeclarationIdentity(" declaration:1")
    with pytest.raises(ValueError, match="unsupported declaration provenance"):
        DeclarationIdentity("declaration:1", provenance="guessed")
    with pytest.raises(ValueError, match="tuple of non-empty strings"):
        DeclarationIdentity("declaration:1", occurrence_ids=["holes:1"])
    with pytest.raises(ValueError, match="tuple of non-empty strings"):
        DeclarationIdentity(
            "declaration:1",
            provenance="detected-geometry",
            occurrence_ids=(" holes:1",),
        )
    with pytest.raises(ValueError, match="must be unique"):
        DeclarationIdentity(
            "declaration:1",
            provenance="detected-geometry",
            occurrence_ids=("holes:1", "holes:1"),
        )
    with pytest.raises(ValueError, match="only detected-geometry or pmi"):
        DeclarationIdentity("declaration:1", occurrence_ids=("holes:1",))


def test_declaration_selector_can_author_a_dimension_without_private_objects() -> None:
    sheet = _sheet()
    sheet.hole(diameter=4, at=(0, 0, 0), axis="z").identify("declaration:1")

    sheet.dimension(sheet.by_declaration("declaration:1"), "bore.diameter")

    (intent,) = sheet.model().authored_dimensions
    assert intent.feature is sheet.features[0]
    assert intent.role == "bore.diameter"
