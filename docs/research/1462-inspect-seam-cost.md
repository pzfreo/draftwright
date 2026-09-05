# What the shared detect seam costs an inspection (#1462)

`inspect_step` builds no drawing, but it shares the engine's one detect seam, which sizes the
part while detecting. Some scale selection and dimension planning therefore runs and is
thrown away. #1462 asked whether that justifies an `inspect`-only detect path.

## What #1462 measured, and why it was the wrong measure

The issue sized the discarded work by **call count**, via `sys.setprofile`:

```
528  draftwright.compose            (choose_scale, _build_zones, _compose_anno_boxes)
334  draftwright.model.planner      (plan_dimensions, _suppression, _group_view)
160  draftwright.model.callout      (hole_callout_spec, _tol_of, _is_suppressed)
 37  draftwright.view_plan          (arrangement_of, candidate_is_feasible)
```

A call count says nothing about cost. These are cheap pure-Python calls on a path dominated
by STEP parsing and OCC.

## What it costs

`cProfile` `tottime` over one warm inspection per fixture, attributing each frame by its
defining file. **The attribution is the part that is easy to get wrong**: the repository path
itself contains the string `draftwright`, so `b123d_recognisers` lives at
`…/repos/draftwright/.venv/…` and a naive substring test credits the provider's frames to us.
Match on the resolved package directory (`/src/draftwright/`), not on the string.

| fixture | wall | `compose`+`planner`+`callout`+`view_plan` | all draftwright frames |
| --- | --- | --- | --- |
| `grm04_drive_plate.step` | 0.03 s | 0.000 s | 0.002 s |
| `nist_ctc_04_asme1_ap242.stp` | 6.63 s | 0.002 s | 0.142 s |
| `nist_ctc_02_asme1_ap242.stp` | 13.54 s | 0.003 s | 0.171 s |

Two idle-machine runs agreed to the millisecond on the discarded column. A third, run while
the full test suite had every core busy, quadrupled every wall time and left the discarded
share between 0.01% and 0.02%. An independent reviewer reproduced the table on a different
load with the same magnitudes (CTC-02: 0.181 s of 15.75 s).

## Conclusion

The discarded work is **0.003 s of CTC-02's 13.5 s**. Inspection cost is recognition, STEP
parsing and OCC. An `inspect`-only detect variant would save single-digit milliseconds and
would be a second seam whose divergence from the drawing path could not be checked, so the
shared seam stays.

`test_the_set_of_engine_modules_an_inspection_executes_is_pinned` fixes the exact module set
an inspection runs, so the discarded set cannot grow silently while this holds.

Whether ADR 3 should record this in its `## Open` section is the maintainer's call; this
document is the evidence for that decision, not the decision.
