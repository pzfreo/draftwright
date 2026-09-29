"""Opt-in annotation solve trace recorder, below Drawing and builder."""

from __future__ import annotations

import functools
import json
import logging
import os
from pathlib import Path

from draftwright.model.compiled import resolve_feature

_log = logging.getLogger("draftwright.annotations._common")


def _never_aborts(method):
    """Recording can never abort a build (#736): any exception inside a
    :class:`SolveTrace` recording method logs ONE warning, disarms the recorder
    (sets ``_broken`` — every later guarded call no-ops), and returns ``None``.
    A recorder bug degrades to "no trace", never a failed drawing/export. The
    guard lives HERE, on the recorder, rather than at every hook site — the hooks
    stay bare ``trace is None`` checks (nil cost off) and no call site can forget
    the try/except."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        if self._broken:
            return None
        try:
            return method(self, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — the whole point: never propagate
            self._broken = True
            _log.warning("trace: recorder failed, tracing disabled: %s", exc)
            return None

    return wrapper


class SolveTrace:
    """The opt-in solve-trace recorder (#736): one JSON file per build explaining every
    strip placement decision — the #733 post-mortem's "why is this strip full" answer
    as a glance instead of a custom script.

    Activated by ``build_drawing(trace=...)`` or the ``DRAFTWRIGHT_TRACE`` env var
    (see :func:`draftwright.builder.build_drawing`); threaded onto each run's
    :class:`PlacementContext` (``ctx.trace``) so both the auto-annotate and finalize
    paths trace. **Default off, and off means nil cost**: every hook site is a plain
    ``trace is None`` check — no dict is ever built.

    JSON shape (``version`` 2): ``{"version", "solves": [...], "pass_events": [...],
    "escalations": [...]}`` — two DISTINCT record types, not one masquerading as the
    other:

    * ``solves`` — the corridor solves. Each entry carries ``seq`` (a global event
      counter shared with ``pass_events``, so the build's decision order is
      reconstructable), ``phase`` (``auto:N``/``finalize:N`` — one per annotate run),
      ``corridor`` (the ``[view, side]`` key), ``view``/``axis``/``tier``, the
      ``strip`` bounds (anchor/outer_limit/direction/gap/spacing), the full candidate
      set (name, order, priority, size, force, anchored, dedup, precedence), the
      placement ``passes`` (per pass: the carved span, the in-band obstacles with
      their owning annotation names, the free segments, placed positions, rejections
      with reasons, unplaced leftovers), and per-candidate ``outcomes`` (placed /
      dropped-with-reason / deduped / promoted / deferred-to-post-drain).
    * ``pass_events`` — everything placed OUTSIDE a corridor solve: the standalone
      strip passes (slot fallthroughs, front hole callouts, off-axis locations,
      PMI fallbacks) and the *immediate* placers — the post-drain machined-feature
      leader callouts (chamfer/fillet/flat/pocket/groove/boss ø) and the turned
      diameter row/column and step-length set-solves. The
      ``interior_dimension_assignment`` event also records viable candidates,
      rejection categories, and final placed or dropped outcomes. Each entry carries
      ``seq``/``phase``, a pass ``label``, and ``items`` — one outcome dict per annotation
      (``placed`` with its position, or ``dropped`` with a reason). The
      ``hole_table_replacement`` event records the transactional choice between
      retained feature annotations and a complete table with keyed balloons;
      ``coverage_authority=false`` leaves the semantic verdict to coverage lint.
      ``gdt_post_drain_fallback`` records each rejected alternate side and the
      final sheet-route or unmet outcome after a full GD&T corridor; it does not
      change placement or claim semantic coverage.
      The #733 gap:
      pre-#734 these callouts were the drain-time occupants; post-drain, their own
      story must still be in the trace.

    The ``jq`` contract: ``.solves[].outcomes[]`` for corridor dims,
    ``.pass_events[].items[]`` for everything else::

        jq '.solves[].outcomes[] | select(.name == "dim_height")' t.trace.json
        jq '.pass_events[] | select(.label == "pocket_callouts") | .items[]' t.trace.json

    Recording-only, so the recorder can never abort a build: every public recording
    method is wrapped by :func:`_never_aborts` — an internal failure logs ONE
    warning (``trace: recorder failed, tracing disabled``), disarms the recorder
    (``_broken``), and every later call no-ops. **Disarm semantics are
    partial-disable**: records made before the failure stay in memory and any file
    already written stays on disk, but nothing further is recorded or written (a
    disarmed :meth:`snapshot` returns the ``None`` sentinel, which :meth:`restore`
    tolerates). Separately, an *unwritable path* degrades to a per-attempt logged
    warning without disarming (:meth:`write`), and :meth:`snapshot`/:meth:`restore`
    let ``finalize()``'s #647 transaction roll a failed drain's records back out of
    the trace.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.solves: list[dict] = []
        self.pass_events: list[dict] = []
        self.escalations: list[dict] = []
        # Opaque semantic provenance for report projection. These object references never enter
        # the standalone trace JSON (which remains version 2 and strict JSON); they let the
        # drawing report join even a dropped candidate back to final IR/declaration authority.
        self._candidate_bindings: list[tuple[int, str, object, object, object]] = []
        self._phase = ""
        self._phase_n = 0
        self._seq = 0
        self._current: dict | None = None
        self._broken = False  # set by _never_aborts on the first internal failure

    def _next_seq(self) -> int:
        """One global event counter across solves + pass_events (decision order)."""
        self._seq += 1
        return self._seq - 1

    @_never_aborts
    def snapshot(self):
        """The trace's rollback point for finalize's #647 transaction: capture the
        record counts + phase/seq counters so :meth:`restore` can truncate a failed
        drain's records — a rolled-back finalize must not leave trace entries
        describing placements that no longer exist."""
        return (
            len(self.solves),
            len(self.pass_events),
            len(self.escalations),
            len(self._candidate_bindings),
            self._seq,
            self._phase,
            self._phase_n,
        )

    @_never_aborts
    def restore(self, snap) -> None:
        """Roll the trace back to *snap* (see :meth:`snapshot`). Tolerates the
        ``None`` sentinel a disarmed/failed :meth:`snapshot` returns (no-op)."""
        if snap is None:
            return
        n_solves, n_events, n_esc, n_bindings, seq, phase, phase_n = snap
        del self.solves[n_solves:]
        del self.pass_events[n_events:]
        del self.escalations[n_esc:]
        del self._candidate_bindings[n_bindings:]
        self._seq = seq
        self._phase = phase
        self._phase_n = phase_n
        self._current = None

    @_never_aborts
    def begin_phase(self, label) -> None:
        """Start a new annotate run (``auto``) / finalize drain (``finalize``); each
        run's solves are labelled ``<label>:<n>`` so a measure-and-repack build keeps
        its passes apart."""
        self._phase_n += 1
        self._phase = f"{label}:{self._phase_n}"

    @staticmethod
    def _strip_rec(strip):
        if strip is None:
            return None
        return {
            "anchor": strip.anchor,
            "outer_limit": strip.outer_limit,
            "direction": strip.direction,
            "gap": strip.gap,
            "spacing": strip.spacing,
        }

    @_never_aborts
    def begin_solve(self, key, view, axis, tier, strip, cands) -> None:
        """Open a corridor-solve record (called by :func:`solve_corridor`)."""
        cands = tuple(cands)
        seq = self._next_seq()
        self._current = {
            "seq": seq,
            "phase": self._phase,
            "corridor": list(key) if key is not None else None,
            "view": view,
            "axis": axis,
            "tier": tier,
            "strip": self._strip_rec(strip),
            "candidates": [
                {
                    "name": c.name,
                    "order": list(c.order),
                    "priority": c.priority,
                    "obligation_class": c.effective_obligation_class,
                    "size": list(c.size) if c.size is not None else None,
                    "force": c.force,
                    "anchored": c.anchored,
                    "dedup": list(c.dedup) if c.dedup is not None else None,
                    "precedence": c.precedence,
                }
                for c in cands
            ],
            "passes": [],
            "outcomes": [],
        }
        self.solves.append(self._current)
        self._candidate_bindings.extend(
            (seq, candidate.name, candidate.feature, candidate.measurement, candidate.declaration)
            for candidate in cands
        )

    def candidate_bindings(self) -> tuple[tuple[int, str, object, object, object], ...]:
        """Opaque solve candidate provenance for the drawing-report projector.

        The returned feature/measurement/declaration objects are build-local authority. They are
        deliberately excluded from :meth:`write`; only the report projector may translate them
        into report-local semantic IDs.
        """

        return tuple(self._candidate_bindings)

    @property
    def recording_complete(self) -> bool:
        """Whether recording remained armed through the latest build/finalize pass."""

        return not self._broken

    @_never_aborts
    def end_solve(self) -> None:
        self._current = None

    @_never_aborts
    def begin_pass(self, *, force, label=None, strip=None, view=None, axis=None) -> dict:
        """Open a placement-pass record (called by :func:`place_strip_candidates`) —
        nested under the open corridor solve, or a standalone ``pass_events`` entry
        (with *label*) for a pass-local strip placement outside any corridor."""
        rec: dict = {
            "force": force,
            # A fallback can use another axis/view while its parent corridor is
            # still open. Every pass therefore owns its coordinate frame.
            "label": label,
            "view": view,
            "axis": axis,
            "strip": self._strip_rec(strip),
            "obstacles": [],
            "free_segments": [],
            "placed": [],
            "rejected": [],
            "unplaced": [],
        }
        if self._current is not None:
            self._current["passes"].append(rec)
        else:
            rec = {
                "seq": self._next_seq(),
                "phase": self._phase,
                **rec,
                "items": [],
            }
            self.pass_events.append(rec)
        return rec

    @_never_aborts
    def end_pass(self, rec) -> None:
        """Close a placement-pass record. For a standalone ``pass_events`` entry the
        per-candidate story is folded into ``items`` (the jq contract:
        ``.pass_events[].items[]``): each placed candidate with its position, each
        leftover as ``dropped`` with its last rejection reason (default
        ``strip_full``). A pass nested under a corridor solve keeps its raw
        placed/unplaced lists — the solve's ``outcomes`` carry the summary there."""
        if "items" not in rec:
            return
        reasons = {e["name"]: e["reason"] for e in rec["rejected"]}
        rec["items"] = [
            {"name": e["name"], "outcome": "placed", "pos": e["pos"]} for e in rec.pop("placed")
        ] + [
            {"name": n, "outcome": "dropped", "reason": reasons.get(n, "strip_full")}
            for n in rec.pop("unplaced")
        ]

    @_never_aborts
    def pass_event(self, label, **fields) -> dict:
        """Open a ``pass_events`` record for an out-of-corridor placement decision.

        This covers immediate placers (the #733 gap) and the joint interior-dimension
        assignment. The caller appends one outcome dict per attempted annotation to
        ``rec["items"]``.
        """
        rec = {
            "seq": self._next_seq(),
            "phase": self._phase,
            "label": label,
            **fields,
            "items": [],
        }
        self.pass_events.append(rec)
        return rec

    @_never_aborts
    def record_hole_table_decision(
        self, *, committed, reason, replaced, table_rows, keyed_rows
    ) -> None:
        """Record one complete table-versus-feature-ink transaction safely.

        Build the event and its item inventory inside the recorder guard. A trace
        failure must never turn a valid drawing into a failed build.
        """
        names = sorted(replaced)
        self.pass_events.append(
            {
                "seq": self._next_seq(),
                "phase": self._phase,
                "label": "hole_table_replacement",
                "view": "plan",
                "candidate": "hole_table_plan",
                "alternatives": ["feature_annotations", "hole_table_with_balloons"],
                "outcome": "committed" if committed else "restored",
                "reason": reason,
                "attempted_replacements": names,
                "table_rows": table_rows,
                "keyed_rows": keyed_rows,
                "coverage_authority": False,
                "items": [
                    {
                        "name": name,
                        "outcome": "replaced" if committed else "retained",
                        "reason": "table_with_keyed_row" if committed else reason,
                    }
                    for name in names
                ],
            }
        )

    @_never_aborts
    def record_outcome(self, name, outcome, **extra) -> None:
        """Record a candidate's solve-level outcome; a ``placed`` outcome is enriched
        with its position and a reason-less ``dropped`` with the last recorded
        rejection reason (default ``strip_full``) from this solve's passes."""
        if self._current is None:
            return
        rec: dict = {"name": name, "outcome": outcome, **extra}
        if outcome == "placed":
            for p in self._current["passes"]:
                for e in p["placed"]:
                    if e["name"] == name:
                        rec["pos"] = e["pos"]
        elif outcome == "dropped" and "reason" not in rec:
            reason = "strip_full"
            for p in self._current["passes"]:
                for e in p["rejected"]:
                    if e["name"] == name:
                        reason = e["reason"]
            rec["reason"] = reason
        self._current["outcomes"].append(rec)

    @_never_aborts
    def record_escalations(self, escalations) -> None:
        """Snapshot the run's :class:`Escalation` list (called once per annotate run)."""
        for e in escalations:
            self.escalations.append(
                {
                    "phase": self._phase,
                    "kind": e.kind,
                    "view": e.view,
                    "reason": e.reason,
                    "feature": type(resolve_feature(e.feature)).__name__
                    if e.feature is not None
                    else None,
                }
            )

    @_never_aborts
    def write(self) -> None:
        """Dump the trace JSON to :attr:`path` (once per build; a successful finalize
        re-writes). **Recording-only, so it must never abort a build** — with the two
        documented degradations kept distinct: an *unwritable path* is
        environmental and possibly transient, so it warns per attempt WITHOUT
        disarming (a later finalize rewrite may succeed); a *serialisation failure*
        is an internal recorder bug, so the strict no-``default=`` ``dumps`` raises
        past this method's ``OSError``-only guard into :func:`_never_aborts`, which
        disarms permanently with one warning. The rewrite is atomic
        (``<path>.tmp`` then :func:`os.replace`) so a reader never sees a torn file."""
        data = {
            "version": 2,
            "solves": self.solves,
            "pass_events": self.pass_events,
            "escalations": self.escalations,
        }
        text = json.dumps(data, indent=1)  # recorder-bug failures → decorator disarm
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            _log.warning("trace: could not write %s: %s", self.path, exc)
