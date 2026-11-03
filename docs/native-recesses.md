# Native section recesses

The default reader consumes `quiddity-rust/recognition/3`. `section_recesses`
records use Python's `SectionRecess` geometry, classification and evidence. Their
constituent faces anchor drawing IDs; source projection refusals go to
`Recognition.refusals` and remain explicit lint findings. Step levels and risers
are retained as evidence. Source pocket, channel, blind-slot and passage families
are represented by their unified projection and no longer produce duplicate
unrecognised-family warnings.

The native and Python 0.4 readers retain `section_recess_patterns`, resolving their occurrence indices
to member feature IDs. Missing, repeated, multiply-owned, ambiguous and non-pocket
members are refused. Archived Python 0.3.9 documents retain the released individual
pocket callouts and datum locations. The drawing verifies member sizes and the lattice before
crediting a grouped callout, count, pitch and absolute centre locations. Pattern
members retain their source records and share one requirement set. Missing pitch
reopens the member-location finding. Rounded pockets retain four corner-radius
requirements per occurrence, independently of their size/depth annotation. Pattern corners use one counted radius callout carrying their matching blend
claims; the same blends are excluded from blanket radius notes. Lone pockets
reuse four matching vertical blend owners through their note, callout or located
table. Without such records, a counted pocket-radius callout owns all four corners.
Matching uses faces, axis and radius and prevents duplicate physical statements.

Python has no location compiler owner for non-Z pocket patterns; those remain
explicitly unverifiable here too. Lattices outside the drawing's principal axes
also remain unverifiable rather than receiving invented locations. Individual
side-opening pockets get both in-plane locations in the view looking into them.

Five pending catalogue rules are ported: LINT-pocket_pattern_coverage-requirements,
LINT-code-pocket_pattern_requirement_state, ISSUE-885-a, ISSUE-1485-a and
ISSUE-1485-c. The default-path tests also exercise ISSUE-1245-a, ISSUE-1246-a,
ISSUE-917-a and ARCH0015-1. Existing bridge oracle tests stay in place.

Both kernel dependencies now pin the merged recognition/3 implementation from
quiddity-rust PR #14, `0fdf5dcbdf4cf675e1b7f690b349069704e77a82`:

```toml
haecceity = { git = "https://github.com/pzfreo/quiddity", rev = "0fdf5dcbdf4cf675e1b7f690b349069704e77a82" }
quiddity = { git = "https://github.com/pzfreo/quiddity", rev = "0fdf5dcbdf4cf675e1b7f690b349069704e77a82" }
```

The local `.cargo/config.toml` path override has been removed. Cargo.lock changes
only the two kernel Git sources relative to merged draftwright main; registry
packages retain main's resolution.

All required families are published. Families still published but not consumed by
this drawing reader are `circular_face_patterns`,
`thin_wall_bodies`, `interior_voids`, `sheet_metal_bodies`,
and `freeform_surfaces`. Their per-part records remain
reported; this change does not imply drafting grammars for them. Oriented chamfers are now
consumed as bevels alongside bounded profile-notch dimensions.

The initial verification is retained in [native-recesses-qa.md](native-recesses-qa.md).
The fourteen-part attribution, layout repair, final checks and render comparisons
are recorded in [native-recess-layout-review.md](native-recess-layout-review.md).
The merged-kernel verification is recorded in [native-recess-merge-validation.md](native-recess-merge-validation.md).
