# The layout algorithm, in plain English

**Collect, then solve, once.** Draftwright never places an annotation the moment it
decides to draw one. Every render pass registers what it wants — a dimension with its
anchor geometry, a callout with its viable routes — and positions are computed later by
a few shared solvers. The passes run in a fixed order, and if each committed geometry as
it went, a later pass would find the good space already taken: ladders would interleave,
the same span would get measured twice by two passes, and nothing could tell which of two
coincident dims was the more useful one. So passes queue into corridors, and one drain
stage solves each corridor in a single shot, which is what lets it dedup coincident
spans, sort the set into one monotonic ISO ladder, and drop by importance rather than by
arrival order.

**Dimensions: a 1D strip solve inside a carved corridor.** A corridor is the band above,
below, left or right of a view where dim lines stack. Placement there is
one-dimensional — the position along the stacking axis is the only free variable, since
the other end is pinned to the feature. The engine first carves the band: collect the
obstacles already on the page, discard the ones outside the perpendicular band this batch
occupies (otherwise a width dim below the view would falsely block one on the right
strip), and split what remains into free segments. Then, per segment, order the dims by
where their features sit along the strip (which keeps witness lines from crossing), set
each adjacent pair's gap to the larger of the two labels' extents floored at the minimum
spacing, and solve. The solve is Pool Adjacent Violators with weighted medians: it finds
the placement minimising total witness-line length subject to order, gap and bounds. That
replaced a Cassowary solve and then a scipy LP, both of which merely found *some* optimal
vertex; on the very common ties different platforms picked different vertices and the
drawing changed between machines. PAVA with a lower weighted median is optimal *and*
deterministic by construction. Anchoring — keeping a central hole's callout on the
centre-line — is a dominating weight rather than a hard pin, so an anchored dim still
yields when the strip is genuinely full. If the set won't fit, the lowest-priority
candidate is dropped and the solve retries, so an authored GD&T frame or a mandatory
envelope dim outranks an ordinary auto dim instead of losing by alphabetical accident.

**Leaders: a bounded discrete assignment.** Leaders don't stack, so they get a different
solver. Each callout is a job offering many alternative tip/elbow routes, and the problem
is choosing one route per job with no two colliding — combinatorial, not an ordering. The
annotation layer does the geometry: measure each route (analytically where possible; a
real OCC probe is priced at 150x the analytical one against a fixed work budget), find
which candidate pairs conflict, and price each route by length plus a penalty for
crossing committed ink or ploughing back through the part body. That penalty is charged
per visible stroke width, so a 0.3 mm graze costs about one unit and a 63 mm cut costs
254 — a real cut can't be bought with a shorter route. Those numbers go to a leaf that
knows only indices and costs, optimising lexicographically: most jobs placed, then
highest summed priority, then least penalty, then least total length, then stable input
order. The search is bounded — it first builds the old first-clear greedy answer as an
incumbent, and if the budget runs out it returns that, flagged non-optimal, so resource
pressure degrades routing quality but can never place fewer callouts than the naive
algorithm. Two pieces round it out: 2D furniture like hole tables is placed by a solver
exploiting the fact that an optimal box always sits flush against a region or obstacle
edge; and leader decoration runs *after* the corridor drain, so a best-effort chamfer
callout can never steal strip space from a principal dimension that registered early but
places late.
