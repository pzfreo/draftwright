`mouth_plain.step`, `mouth_far_side.step`, `mouth_both.step`, `mouth_angled.step`, `mouth_mixed.step`,
`mouth_cbore_under.step`, `mouth_cbore_far.step`, `mouth_cbore_far_only.step` and `mouth_countersink.step` are generated, one plate each (30 x 30 x 5, top face at z = 0) with a
⌀6 through hole (⌀2 for the countersink) whose mouth is chamfered 0.5 x 45° on the top, on the
bottom (`far_side`), on both, 0.4 x 30° on the top (`angled`), or, on the ⌀2 hole, 0.5 x 45° that
quiddity keeps as a ⌀3 x 90° countersink. `cbore_chamfer.step` is quiddity's own fixture
(`tests/fixtures/hole_mouth/cbore_chamfer.step`, its `make.py`). Used by
`tests/dims_mouth_chamfer.rs`. Regenerate with
`uv run --no-project --with build123d python -I gen_mouth.py mouth_<case>.step <case>`
(build123d's `export_step`), from this script:

```python
"""Plate 30 x 30 x 5 (top face at z = 0) with one hole, for the mouth-chamfer fixtures.
usage: gen_mouth.py <out.step> <case>
  plain, far_side, both, angled: a D6 through hole chamfered 0.5 x 45 on top / bottom / both,
      0.4 x 30 on top; mixed: 0.5 x 45 on top and 0.4 x 30 on the bottom
  countersink: a D2 hole chamfered 0.5 x 45 (quiddity keeps it a D3 x 90 countersink)
  cbore_under: a D6 hole with a D10 x 2 counterbore from the bottom face, 0.25 x 45 on its mouth
  cbore_far: a D10 x 2 counterbore from the top, 0.25 x 45 on its mouth and on the bottom bore mouth
  cbore_far_only: the same counterbore, 0.25 x 45 on the bottom bore mouth only"""
import sys
from build123d import Box, Cylinder, Pos, Align, GeomType, chamfer, export_step
out, case = sys.argv[1], sys.argv[2]
below = (Align.CENTER, Align.CENTER, Align.MAX)
above = (Align.CENTER, Align.CENTER, Align.MIN)
part = Box(30, 30, 5, align=below)
d = 2.0 if case == "countersink" else 6.0
part -= Cylinder(d / 2, 7, align=below)
if case == "cbore_under":
    part -= Pos(0, 0, -5) * Cylinder(5, 2, align=above)
elif case.startswith("cbore_"):
    part -= Cylinder(5, 2, align=below)
def mouth(z, r):
    found = [e for e in part.edges() if e.geom_type == GeomType.CIRCLE and abs(e.radius - r) < 1e-6 and abs(e.arc_center.Z - z) < 1e-6]
    assert len(found) == 1, (z, r, len(found))
    return found
# (z, radius, length, length2) of each chamfer
cuts = {
    "plain": [(0, d / 2, 0.5, None)],
    "far_side": [(-5, d / 2, 0.5, None)],
    "both": [(0, d / 2, 0.5, None), (-5, d / 2, 0.5, None)],
    "angled": [(0, d / 2, 0.4, 0.2309)],      # 0.4 x 0.2309 is 30 degrees from the face
    "mixed": [(0, d / 2, 0.5, None), (-5, d / 2, 0.2309, 0.4)],
    "countersink": [(0, d / 2, 0.5, None)],
    "cbore_under": [(-5, 5, 0.25, None)],
    "cbore_far": [(0, 5, 0.25, None), (-5, d / 2, 0.25, None)],
    "cbore_far_only": [(-5, d / 2, 0.25, None)],
}[case]
for z, r, a, b in cuts:
    part = chamfer(mouth(z, r), a, b) if b else chamfer(mouth(z, r), a)
export_step(part, out)
```
