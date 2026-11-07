# Section depth witness attachment

A section depth must attach to the geometry whose measurement it states. A linear
form needs witnesses at the pocket floor and mouth (the cylinder extreme for a
central keyway cut). The standard depth callout leads to the pocket floor.
A correct numeric value without that attachment does not establish where it applies.

`dimension_witness_detached` is an **error** with **incomplete** impact, grouped by
its code. It misstates the measurement's application, rather than merely making
correctly attached ink harder to read. The drawing is retained for inspection;
the finding does not refuse generation or invent another measurement.

`src/sections.rs::attachment_problems` independently checks rendered placements
against the named pocket's geometric floor and mouth. The mutation regression
`tests/steerability.rs::render_detects_detached_section_depth_witnesses` covers
both a displaced callout tip and floating linear witnesses, including their
severity, impact and group. The source-wide finding audit and catalogue unit test
require the registration and its reason in `docs/finding-impact.md`.
