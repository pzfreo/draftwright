"""Build-scoped declaration identity follows intent, never a list position (#1710)."""

from types import SimpleNamespace

import pytest
from build123d import Box, Cylinder

from draftwright import Sheet
from draftwright.drawing import Drawing
from draftwright.model import DeclarationIdentity
from draftwright.registry import AnnotationRegistry


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

    assert model.declaration_identities == (
        DeclarationIdentity("declaration:2"),
        DeclarationIdentity("declaration:1"),
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

    assert model.declaration_identities == ()
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


def test_equal_valued_independent_features_retain_distinct_identities() -> None:
    sheet = _sheet()
    sheet.hole(diameter=4, at=(0, 0, 0), axis="z").identify("declaration:1")
    sheet.hole(diameter=4, at=(0, 0, 0), axis="z").identify("declaration:2")

    model = sheet.model()

    assert model.features[0] == model.features[1]
    assert model.declaration_identities == (
        DeclarationIdentity("declaration:1"),
        DeclarationIdentity("declaration:2"),
    )


def test_part_model_rejects_a_misaligned_identity_inventory() -> None:
    sheet = _sheet()
    sheet.hole(diameter=4, at=(0, 0, 0), axis="z").identify("declaration:1")
    model = sheet.model()

    with pytest.raises(ValueError, match="align one-for-one"):
        type(model)(
            bbox=model.bbox,
            orientation=model.orientation,
            features=model.features,
            declaration_identities=(DeclarationIdentity("declaration:1"), None),
        )


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


def test_identity_reaches_the_built_drawing_model() -> None:
    sheet = _sheet()
    hole = sheet.hole(diameter=4, at=(0, 0, 0), axis="z").identify("declaration:1")
    sheet.dimension(hole, "bore.diameter")

    drawing = sheet.build()

    assert drawing.model().declaration_identities == (DeclarationIdentity("declaration:1"),)
    marks = drawing.annotations_of(drawing.model().features[0])
    assert marks
    assert all(drawing.declaration_id_of(name) == "declaration:1" for name in marks)
    assert any(drawing.label_box(name) is not None for name in marks)
    assert drawing.declaration_id_of("title_block") is None
    with pytest.raises(KeyError):
        drawing.declaration_id_of("no-such-annotation")


def test_public_pick_identity_handles_explicit_measurement_and_ambiguous_owners() -> None:
    first, second, explicit = object(), object(), object()
    model = SimpleNamespace(
        features=(first, second, explicit),
        declaration_identities=tuple(
            DeclarationIdentity(f"declaration:{index}") for index in (1, 2, 3)
        ),
    )
    registry = AnnotationRegistry()
    label = SimpleNamespace(label_bbox=(1, 2, 3, 4))
    registry.add(label, "feature", "front", feature=first)
    registry.add(
        label,
        "measurement",
        "front",
        measurement=SimpleNamespace(feature=second, parameter="step.diameter"),
    )
    registry.add(label, "explicit", "front", declaration=explicit)
    registry.add(
        label,
        "shared",
        "front",
        feature=first,
        measurement=SimpleNamespace(feature=second, parameter="step.diameter"),
    )
    drawing = SimpleNamespace(
        _registry=registry, model=lambda: model, get_annotation=registry.named
    )

    assert Drawing.declaration_id_of(drawing, "feature") == "declaration:1"
    assert Drawing.declaration_id_of(drawing, "measurement") == "declaration:2"
    assert Drawing.declaration_id_of(drawing, "explicit") == "declaration:3"
    assert Drawing.declaration_id_of(drawing, "shared") is None
    assert Drawing.label_box(drawing, "measurement") == (1.0, 2.0, 3.0, 4.0)
    registry.add(SimpleNamespace(label_bbox=None), "furniture", "front")
    assert Drawing.declaration_id_of(drawing, "furniture") is None
    assert Drawing.label_box(drawing, "furniture") is None
    with pytest.raises(KeyError):
        Drawing.label_box(drawing, "missing")

    equal_a, equal_b, copy = (SimpleNamespace(value=1) for _ in range(3))
    equal_model = SimpleNamespace(
        features=(equal_a, equal_b),
        declaration_identities=(DeclarationIdentity("equal:a"), DeclarationIdentity("equal:b")),
    )
    registry.add(label, "equal-ambiguous", "front", feature=copy)
    drawing.model = lambda: equal_model
    assert Drawing.declaration_id_of(drawing, "equal-ambiguous") is None
    drawing.model = lambda: None
    assert Drawing.declaration_id_of(drawing, "feature") is None


def test_synthetic_rotational_feature_preserves_declared_identity_alignment() -> None:
    part = Cylinder(12, 30)
    sheet = Sheet(part)
    sheet.authored_dimensions()
    sheet.boss(part).identify("declaration:1")

    drawing = sheet.build()

    assert drawing.model().declaration_identities[0] == DeclarationIdentity("declaration:1")
    assert drawing.model().declaration_identities[-1] is None
    assert len(drawing.model().declaration_identities) == len(drawing.model().features)
