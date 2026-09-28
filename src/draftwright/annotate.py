"""Compat facade: the annotation passes moved to the annotations/ subpackage (#164).

`_auto_annotate` (the orchestrator) now lives in `annotations.orchestrator`; the
individual passes in `annotations.{sections,turned,pmi,holes}`. This module re-exports
the orchestrator entry point and the two helpers still referenced by name elsewhere,
so `from draftwright.annotate import _auto_annotate` keeps working.
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
    "draftwright.annotate is deprecated (#1936); import _auto_annotate from "
    "draftwright.annotations.orchestrator, _wrap_rows from draftwright._core, and "
    "_step_repeat from draftwright.model.compiled. "
    "Removed in 0.6.0.",
    DeprecationWarning,
    stacklevel=2,
)
