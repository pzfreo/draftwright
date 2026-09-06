"""Published recess test values and deliberately damaged consumer-boundary inputs."""

from copy import deepcopy

from draftwright.section_recess_contract import section_recess_fields


def declaration_fields(source, expected_kind):
    kind, values = section_recess_fields(source)
    assert kind == expected_kind
    values["at"] = values.pop("origin")
    return values


def corrupt_recess(source, field, value):
    """Bypass frozen constructor checks so the consumer must catch the exact defect.

    Preserve the original record and every unrelated nested fact; this prevents a provider
    constructor error from masquerading as successful consumer validation.
    """
    damaged = deepcopy(source)
    geometry = damaged.geometry
    if field == "boundary_coordinate":
        vertex = geometry.profile.boundary[0]
        object.__setattr__(vertex, "point", (value, vertex.point[1]))
    elif field == "run_interval":
        object.__setattr__(geometry, "run_interval", value)
    elif field == "origin":
        object.__setattr__(geometry.frame, "origin", value)
    elif field == "u":
        object.__setattr__(geometry.frame, "u", value)
    elif field == "condition":
        object.__setattr__(geometry.ends.low, "condition", value)
    else:
        raise AssertionError(f"unknown test corruption {field}")
    return damaged


def unsupported_roof_recess():
    """An open recess with suspended material that fails the constant-section proof."""
    from build123d import Box, Pos

    part = Box(60, 40, 12) - Pos(25, 15, 4) * Box(20, 20, 8)
    part += Pos(-20, -10, 9) * Box(5, 5, 6)
    part += Pos(0, 0, 13) * Box(60, 40, 2)
    part += Pos(20, 10, 8) * Box(3, 3, 8)
    return part
