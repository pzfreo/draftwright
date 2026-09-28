"""Stable import path for shared pocket/pad location applicability.

The implementations live in measurement_support; this module remains a supported
import path for callers that imported the original location contract.
"""

from __future__ import annotations

from math import isfinite as isfinite

from draftwright.measurement_support import (
    RequirementExclusion as RequirementExclusion,
)
from draftwright.measurement_support import (
    coincident_location_axes as coincident_location_axes,
)
from draftwright.measurement_support import (
    datum_location_exclusion as datum_location_exclusion,
)
from draftwright.measurement_support import (
    pocket_location_reference as pocket_location_reference,
)
