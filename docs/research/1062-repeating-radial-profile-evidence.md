# Repeating Radial Profile Evidence (#1062)

> **Status:** productionised by #1087, 2026-08-09. The executable mutations remain in
> `tests/test_issue_1058_wheel_profile.py`; the reusable evidence is owned by
> `recognition/repeating_profiles.py`. No automatic gear semantics are approved by this note.

## Decision

The wheel fixture proves a **closed 13-fold repeating radial outer profile** about its Z
axis. It does not prove that the profile is a gear, nor does it establish module, pressure
angle, helix angle, backlash, tooth thickness, quality grade, or manufacturing process.

Keep `unrecognised_defining_geometry` for this part. Do not add an automatic `GearFeature`,
emit a gear callout, or treat outside/root diameters and repeat count as a complete
manufacturing definition. The useful next boundary is:

1. reusable geometry-only correspondence for a repeating radial profile; and
2. a separately designed, explicit declaration of gear requirements and their drawing
   presentation.

The second may consume the first to check that declared tooth count and axis correspond to
the solid. Recognition must not manufacture the declaration.

## Pinned Evidence

| Item | Observed fact |
|---|---|
| Fixture | `tests/fixtures/issue_1058_wheel_rh.step` |
| SHA-256 | `4911c06426f0ceeedc198416e058aabc1c1a6a65e9e766eca7efed5484a27cda` |
| STEP form | AP214, one solid, generic product name `SOLID` |
| Overall bounds | X -3.8000001..3.7733322; Y -3.8000001..3.8000001; Z -3.8500001..3.8500001 mm |
| Principal outer boundaries | two, at the opposed Z limits |
| Boundary topology | one closed 52-edge outer wire on each end face |
| Edge inventory | 13 circular edges and 39 B-splines |
| Circular edges | radius 3.8 mm, common XY centre (0, 0) |
| Rotational correspondence | four complete 13-edge orbits under `2*pi/13` rotation |
| Orbit roles | one circular-tip orbit and three B-spline orbits |

Both end faces prove the same correspondence despite different edge start and traversal.
The STEP header and entities contain no gear name, PMI, standard, or parameter metadata.

## What The Proof Establishes

The test samples every curve in each outer wire, then requires a one-to-one mapping of every
curve under one sector rotation. Curve type, length, and sampled geometry must agree within
the fixed tolerance. The mapping must be bijective, each orbit must contain exactly 13
curves, and every endpoint must belong to one connected degree-two cycle.

This is stronger than the existing lint hint. `_radial_outer_arc_count` reports 13 equal,
equally spaced arcs on a common circle, but explicitly does not claim that the 39 intervening
B-splines repeat. The complete-wire proof closes that geometric gap without widening the
semantic claim.

The following mutations fail:

- retaining only the 13 common-circle arcs;
- altering one intervening B-spline sample while leaving all arcs unchanged;
- breaking one endpoint so the outer wire is open;
- changing one sector boundary while preserving a closed cycle; and
- relying on the wire's original start edge or traversal direction.

The changed-intervening-curve mutation is the decisive guard: an arc-count implementation
would still pass it.

## Why This Is Not Gear Recognition

Many manufactured profiles can be cyclic without being gears. Even if a human recognises
the fixture's purpose, the B-rep alone does not uniquely identify the generating standard or
the requirements needed to reproduce and inspect it. Fitting a plausible standard from 13
tips and a diameter would turn a guess into normative drawing output.

A truthful technical drawing therefore needs explicit requirements supplied by the author,
usually including at least the gear system/type, tooth count, module or diametral pitch,
pressure angle, reference geometry, and the applicable accuracy or inspection requirement.
The exact public surface and table/callout form need a standards-backed design slice; this
discovery does not invent them.

## ADR Fit

- **ADR 0013:** the proven repeat is lower-tier, geometry-only evidence. `gear` is an
  application semantic and must not be smuggled into the recognition record.
- **ADR 0015:** any future automatic record must enter through the compiler's existing
  recognition waist. A declared gear requirement belongs on the declared path.
- **ADR 0017:** if productionised, one orchestration owns the correspondence evidence and
  consumers project from it. The mutation precedes trust in the guard, and a new identity
  scheme is not justified by this single case.
- **ADR 0011:** explicit author intent is the appropriate source for requirements that the
  B-rep cannot establish.

No ADR amendment is required. No placement decision is made, so ADR 0014 is unaffected.

## Follow-up Slices

1. **Productionised in #1087.** The pure full-wire correspondence is owned by the one
   recognition orchestration and consumed by physical critique for declared-profile
   correspondence. It does not clear completeness merely because the repeat exists.
2. **Design declared gear requirements.** Start from the applicable drawing/gear standards
   and define the minimum honest declaration, validation, IR, and table/callout output. Include
   a mismatch diagnostic when declared count or axis disagrees with geometric evidence.
3. **Only then assess automatic assistance.** Recognition may prefill proven geometric facts
   such as axis and repeat count for user confirmation. It may not infer the normative gear
   specification.
