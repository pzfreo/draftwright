"""Compat facade: the annotation passes moved to the annotations/ subpackage (#164).

`_auto_annotate` and its companion entry points live in
`annotations.orchestrator`; rendering passes live in `annotations/`. This module
retains the historical import path for callers of those entry points.
"""

import warnings

from draftwright.annotations.orchestrator import (  # noqa: F401
    _auto_annotate,
    _wrap_rows,
    build_model,
    build_rotational_feature,
)
from draftwright.model.compiled import _step_repeat as _detect_step_repeat  # noqa: F401

warnings.warn(
    "draftwright.annotate is deprecated (#1936); import _auto_annotate, build_model, and "
    "build_rotational_feature from draftwright.annotations.orchestrator, _wrap_rows from "
    "draftwright._core, and _step_repeat from draftwright.model.compiled. "
    "Removed in 0.6.0.",
    DeprecationWarning,
    stacklevel=2,
)
