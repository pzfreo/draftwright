"""Deferred placement intents — the low IR of the layout backend (#426).

The add verbs (`callout`/`locate`/`furniture`/`dimension`/`section`) place *live* by
default. When a :class:`~draftwright.drawing.Drawing` is in **deferred** mode
(``_defer_intents``), each verb records an :class:`Intent` instead of placing, and
``Drawing.finalize()`` drains the recorded list.

This is the explicit low IR the layout optimizer is missing (ADR-0009 collect-then-solve
is per-strip; #426 lifts it to the whole drawing). Phase 1 records intents and replays
them through the existing live helpers — byte-identical to placing live. Later phases
drain the recorded set through the shared ``_auto_annotate`` orchestration so a
reconstruction reaches auto-pass quality (crossing-free, optimally packed) while staying
editable — commenting a verb line simply omits its recorded intent.

A leaf module with no draftwright imports, so `drawing` can import it without a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The five add verbs that record intents (`section` is part-level → feature is None).
IntentKind = str  # "callout" | "locate" | "furniture" | "dimension" | "section"


@dataclass
class Intent:
    """One deferred add-verb call: *what* to place and with *which* args, not *where*.

    ``kind`` names the verb; ``feature`` is the IR feature it targets (``None`` for a
    part-level ``section``); ``kwargs`` carries the verb's placement-relevant arguments
    verbatim (e.g. ``{"role": "width"}`` for a dimension, ``{"axes": ("x",)}`` for a
    locate) so a replay/solve reproduces the call exactly. Ordering is list order — the
    sequence the script's verb calls ran in.
    """

    kind: IntentKind
    feature: object | None
    kwargs: dict = field(default_factory=dict)
