"""Original ISSUES.md repros; build123d 0.13.0. Run from any directory.

Geometry shared under this repository's licence. STEP fixtures only; Python
requirements are authored by each regression, so they exercise the public API.
"""
from pathlib import Path
from build123d import Box, Cylinder, Part, Polygon, Pos, export_step, extrude

out = Path(__file__).resolve().parent
body = Part() + Box(13.55, 11, 80)
body &= Pos(13.55 / 2 - 8.11, 0, 0) * Cylinder(8.11, 100)
export_step(body, out / "issue461-whole.step")
body -= Pos(13.55 / 2, 0, -7) * Box(2, 2, 66)
export_step(body, out / "issue461-grooved.step")

# #464's authored M3x0.5-6H on a diameter-2.5 through bore.
export_step(Box(20, 16, 8) - Cylinder(1.25, 8), out / "issue464-tap.step")

# #505's rounded model dimension, rather than an exact tan(25 degrees).
wedge = extrude(Polygon((0, 0), (10, 0), (10, 4.663), align=None), 5)
export_step(wedge, out / "issue505-wedge.step")
