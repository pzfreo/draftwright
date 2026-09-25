# Old and new annotation layout, in plain English

Both approaches start with the same recognized part features and use the same
drawing engine. They differ in how they reserve space around the views and choose
where annotations go. The sheet size and drawing scale stay fixed in the
comparison.

| | Established layout | New candidate layout |
| --- | --- | --- |
| Planning | Reserves fairly generous strips around each view before placing dimensions and callouts. | Estimates the space annotations need, then uses selected tighter strips and a different arrangement of the views. After annotations settle, the isometric can grow into space that its real outline and the annotation ink leave clear. |
| Dimensions | Generally gives each dimension its own tier in a strip. Some dimensions may end up inside a view when exterior space is tight. | Prefers exterior dimensions, can route some plan dimensions to the opposite side, and lets dimensions with separate sideways spans share a tier when their actual lines and labels remain clear. |
| Callouts | Places leaders through the established solve. | Uses the same solve, with an extra bounded attempt to recover a leader that would otherwise be dropped. |
| Failure | Reports dropped or conflicting annotations through lint. | Reports the same problems. The public `best` option keeps the established drawing if a candidate loses required content or fails the quality checks. |

The visible result is often more usable space for the views and fewer dimensions
inside the part. On CTC01, the candidate restores a required 180 mm hole-location
dimension, reduces required blockers from three to two, moves the 800 mm and
50 mm dimensions outside the view, and removes two dimension crossings. Its
`55` and `100` dimensions can now share a height because they occupy separate
horizontal spans; the overlapping `75` dimension remains on another height.
CTC01 stays on A3 at 1:5. Its isometric view is smaller, so that change is a
real tradeoff rather than a free gain.

Some front and plan views still sit at the 20 mm minimum geometry gap. There
is annotation padding inside that space, but it can look tight. Adding 5 mm to
every candidate lost required content on crowded parts, so spacing needs a
part-specific clearance decision before it can be increased safely.

Across the fixed-sheet 15-part test set, the new `best` option selects an
improved drawing for 13 parts and keeps two ties. All 15 selected drawings retain
the checked annotation meaning. This is a relative result: some difficult
drawings, especially CTC02 and CTC04, still have many unresolved requirements.
The tests do not say those drawings are ready for manufacture.

Today, `best` first builds the established drawing, tries candidate layouts,
checks the completed results, and returns a candidate only when it is strictly
better and passes the content and safety checks. That makes it slower than one
ordinary build. In one measured CTC05 run, the chosen two-build path took
36.6 seconds versus 13.9 seconds for the established build alone. The default
remains the established layout. A future candidate-first default would need its
own safety decision before it could use the established build as a fallback;
the current 13-of-15 result does not prove that future path yet.
