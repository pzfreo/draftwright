"""OCCT type-code vocabulary for AP242 PMI extraction."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Type-code tables
# ---------------------------------------------------------------------------

# int → human tag for XCAFDimTolObjects_DimensionType enum
_DIM_TYPE: dict[int, str] = {
    0: "location",  # Location_None
    1: "curved_dist",  # Location_CurvedDistance
    2: "linear",  # Location_LinearDistance (outer-to-outer, generic)
    3: "linear",  # FromCenterToOuter
    4: "linear",  # FromCenterToInner
    5: "linear",  # FromOuterToCenter
    6: "linear",  # FromOuterToOuter
    7: "linear",  # FromOuterToInner
    8: "linear",  # FromInnerToCenter
    9: "linear",  # FromInnerToOuter
    10: "linear",  # FromInnerToInner
    11: "angular",  # Location_Angular (incl. curved centre-to-centre)
    12: "oriented",  # Location_Oriented
    14: "curve_length",  # Size_CurveLength
    15: "diameter",  # Size_Diameter  ← add ø prefix
    16: "diameter",  # Size_SphericalDiameter
    17: "radius",  # Size_Radius    ← add R prefix
    18: "radius",  # Size_SphericalRadius
    27: "thickness",  # Size_Thickness
    28: "angular",  # Size_Angular
    30: "label",  # CommonLabel     ← no numeric value, skip
    31: "presentation",  # DimensionPresentation ← graphical only, skip
}

# Type 31 is graphical presentation only. Type 30 (CommonLabel) can carry authored meaning
# despite having no numeric GetValue, so it must fail visibly until supported rather than be
# discarded with presentation geometry (#623).
_PRESENTATION_TYPES = {31}

_LENGTH_DIMENSION_KINDS = frozenset(
    (
        "location",
        "curved_dist",
        "linear",
        "oriented",
        "curve_length",
        "diameter",
        "radius",
        "thickness",
    )
)

# prefix character for the label
_DIM_PREFIX: dict[str, str] = {
    "diameter": "ø",
    "radius": "R",
}

# int → short tag for XCAFDimTolObjects_GeomToleranceType
_GTOL_TYPE: dict[int, str] = {
    1: "angularity",
    2: "circular_runout",
    3: "circularity",
    4: "coaxiality",
    5: "concentricity",
    6: "cylindricity",
    7: "flatness",
    8: "parallelism",
    9: "perpendicularity",
    10: "position",
    11: "profile_line",
    12: "profile_surface",
    13: "straightness",
    14: "symmetry",
    15: "total_runout",
}

# int → stable semantic name for XCAFDimTolObjects_GeomToleranceModif.  The whole OCCT
# vocabulary is inventoried even though only the leader-scope symbols have faithful,
# export-safe drafting representations today; every other known value remains explicit and
# fail-closed.
_GTOL_MODIFIER: dict[int, str] = {
    0: "any_cross_section",
    1: "common_zone",
    2: "each_radial_element",
    3: "free_state",
    4: "least_material_requirement",
    5: "line_element",
    6: "major_diameter",
    7: "maximum_material_requirement",
    8: "minor_diameter",
    9: "not_convex",
    10: "pitch_diameter",
    11: "reciprocity_requirement",
    12: "separate_requirement",
    13: "statistical_tolerance",
    14: "tangent_plane",
    15: "all_around",
    16: "all_over",
}
_SUPPORTED_GTOL_SCOPE_MODIFIERS = frozenset(("all_around", "all_over"))
_GTOL_TYPE_OF_VALUE = {
    1: "diameter_zone",
    2: "spherical_diameter_zone",
}
_GTOL_MATERIAL_REQUIREMENT = {
    1: "maximum_material_requirement",
    2: "least_material_requirement",
}
