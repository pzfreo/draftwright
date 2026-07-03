"""Compat facade: the annotation passes moved to the annotations/ subpackage (#164).

`_auto_annotate` (the orchestrator) now lives in `annotations.orchestrator`; the
individual passes in `annotations.{sections,turned,pmi,holes}`. This module re-exports
the orchestrator entry point and the two helpers still referenced by name elsewhere,
so `from draftwright.annotate import _auto_annotate` keeps working.
"""

from draftwright.annotations.from_model import _detect_step_repeat  # noqa: F401
from draftwright.annotations.orchestrator import (  # noqa: F401
    _auto_annotate,
    _wrap_rows,
    build_model,
)
