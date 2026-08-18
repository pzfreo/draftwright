# Meta-guard audit (#1222)

The verdict table required by #1222: every structural guard either names the failure
it demonstrably prevents, or it goes. A guard is **load-bearing** when its failure
mode has actually occurred in this repository's history (the issue is cited), or when
it carries its own anti-tautology self-test proving the detector detects. "Keep"
verdicts are not permanent: a guard whose allowlist stops shrinking or whose failure
mode becomes unreachable should be re-audited, and the moratorium from #1221 applies
to all growth — no new ratchet/allowlist/meta-test lands without the mutation that
breaks its claimed contract (#1018 rule).

## A. Structural meta-tests — audited 2026-08-18

| Guard | Verdict | Why |
|---|---|---|
| `test_import_boundaries.py` | **keep** | `_LAYERS` is the authoritative DAG map (CLAUDE.md defers to it); upward imports were the pre-#640 norm, not a hypothetical. |
| `test_drawing_encapsulation.py` | **keep** | Single-owner build state (ADR 0005). The stay-deleted halves guard against reintroduction of removed surfaces — a failure mode LLM sessions are *specifically* prone to (regenerating deleted code from stale context). |
| `test_compiled_plan_boundary.py` | **keep** | ADR 0016 Amdt 1: suppression-by-omission is unenforceable without it. |
| `test_label_provenance.py` | **keep** | Active drawdown ratchet (#927, 26 sites). Re-audit when the budget reaches zero — at zero it becomes a simple boundary test. |
| `test_recognition_manifest.py` | **keep** | Fail-closed classification is the ADR 0017 contract itself. |
| `test_recogniser_contract.py` | **keep** | Cross-repository capability join; fail-closed by design. |
| `test_detect_registry.py` | **keep** | Record→Feature completeness; a silently unadapted record family is invisible in output. |
| `test_carve_free_position_callers.py` | **keep** | Two features (#555, #559) regressed onto the solver-invisible path before #636 — the failure mode recurred twice. Carries anti-tautology self-tests. |
| `test_declared_recognition_gate.py` | **keep** | ADR 0017: declared builds must not pay recognition cost; regression is silent (slow, not wrong). |
| `test_external_recognition_boundary.py` | **keep** | Pins the published recogniser release by hash; the fail-closed join depends on it. |
| `test_private_test_imports.py` | **keep** | Shrink-only ratchet with per-entry rationale (#641). The stale-entry assertion is the shrink mechanism, not churn. |
| `test_private_test_attr_reads.py` | **keep** | Cardinality ratchet closing the last reach-through quadrant (#741); deliberately line-number-churn-free. |
| `test_architecture_docs.py` | **trimmed** | Three of four assertions policed phrase absences from battles won weeks ago (stale ADR 0008/0009 references); only the live no-line-anchors rule survives. |

Audit finding: the structural guards individually earn their keep — most cite the
concrete regression they exist to prevent, and the two AST detectors prove their own
detection. The guard *tax* identified by #1221 is the aggregate context cost, which
is addressed by CLAUDE.md pointing here instead of restating each guard, not by
deleting guards.

## B. Runtime budgets/caps — pending

Still open under #1222: each budget below must bound *measured* work via a live
counter, emit a named diagnostic when it fires (per ADR 0014 Amdt 4), and have a
fixture demonstrating the fire path.

`_FEATURE_LEADER_MAX_{CANDIDATES,FIXED_PROBES,PAIR_PROBES}`,
`_LEADER_ASSIGN_MAX_{JOBS,CANDIDATES,PAIR_PROBES,STATES}`,
`_GUARDED_TOP_LANE_MAX_OBSTACLE_PROBES`, `_GUARDED_CARVE_MAX_LABEL_PROBES`,
`_GUARDED_STRIP_MAX_STATES`, `_GUARDED_STRIP_MAX_INTERVAL_PROBES`,
`_REPACK_MAX_ITER`, `_ISO_WIDTH_BUDGET`, `_MATERIAL_MAX_GRID`.
