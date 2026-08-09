"""Standards-backed presentation for declared gear requirements (#1086)."""

from __future__ import annotations

from draftwright.model import ExternalSpurGearFeature


def _authored(value: float) -> str:
    value = float(value)
    return str(int(value)) if value.is_integer() else repr(value)


def _signed(value: float) -> str:
    if value == 0:
        return "0"
    return ("+" if value > 0 else "-") + _authored(abs(value))


def gear_table_rows(feature: ExternalSpurGearFeature) -> tuple[tuple[str, str, str], ...]:
    """The fixed data-table vocabulary for the one supported gear class."""
    lower, upper = feature.tooth_thickness_tolerance
    return (
        ("GEAR DATA", "VALUE", "BASIS"),
        ("TYPE", "METRIC EXTERNAL SPUR", feature.GEOMETRY_STANDARD),
        ("BASIC RACK", "STANDARD", feature.BASIC_RACK_STANDARD),
        ("TEETH z", str(feature.tooth_count), feature.GEOMETRY_STANDARD),
        ("MODULE m", f"{_authored(feature.module)} mm", feature.MODULE_STANDARD),
        (
            "PRESSURE ANGLE",
            f"{_authored(feature.pressure_angle)} deg",
            feature.GEOMETRY_STANDARD,
        ),
        ("PROFILE SHIFT x", _authored(feature.profile_shift), feature.GEOMETRY_STANDARD),
        ("FACE WIDTH b", f"{_authored(feature.face_width)} mm", feature.GEOMETRY_STANDARD),
        (
            "TOOTH THICKNESS s",
            f"{_authored(feature.tooth_thickness)} {_signed(upper)}/{_signed(lower)} mm",
            feature.TOOTH_THICKNESS_STANDARD,
        ),
        (
            "FLANK CLASS",
            str(feature.flank_tolerance_class),
            feature.FLANK_TOLERANCE_STANDARD,
        ),
    )


def render_gear_tables(dwg, model) -> int:
    """Place each declared gear table through ``Drawing.add_table``'s free-space solve."""
    features = [f for f in model.features if isinstance(f, ExternalSpurGearFeature)]
    placed = 0
    for index, feature in enumerate(features, 1):
        base = name = f"gear_data_{index}"
        suffix = 1
        while name in dwg.registry:
            name = f"{base}_{suffix}"
            suffix += 1
        rows = gear_table_rows(feature)
        table = dwg.add_table(rows, name=name, prefer="tr")
        if table is None:
            continue
        # Snapshot what the rendered table actually represents.  Physical lint compares
        # this with the current frozen IR feature, so a stale/mutated normative value cannot
        # pass merely because some table-shaped annotation survived.
        table.gear_requirement_values = feature.requirement_values
        table.gear_requirement_rows = rows
        dwg.registry.reapply(name, {"feature": feature})
        placed += 1
    return placed


__all__ = ["gear_table_rows", "render_gear_tables"]
