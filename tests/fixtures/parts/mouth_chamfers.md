`mouth_plain.step`, `mouth_far_side.step`, `mouth_both.step`, `mouth_angled.step` and
`mouth_countersink.step` are generated, one plate each (30 x 30 x 5, top face at z = 0) with a
⌀6 through hole (⌀2 for the countersink) whose mouth is chamfered 0.5 x 45° on the top, on the
bottom (`far_side`), on both, 0.4 x 30° on the top (`angled`), or, on the ⌀2 hole, 0.5 x 45° that
quiddity keeps as a ⌀3 x 90° countersink. `cbore_chamfer.step` is quiddity's own fixture
(`tests/fixtures/hole_mouth/cbore_chamfer.step`, its `make.py`). Used by
`tests/dims_mouth_chamfer.rs`. Regenerate with
`uv run --no-project --with build123d python -I gen_mouth.py mouth_<case>.step <case>`
(build123d's `export_step`), from this script:

```python
"""Plate 30 x 30 x 5 (top face at z = 0) with one hole, for the mouth-chamfer fixtures.
usage: gen_mouth.py <out.step> <plain|far_side|both|angled|countersink>"""
import sys
from build123d import Box, Cylinder, Pos, Align, GeomType, chamfer, export_step
out, case = sys.argv[1], sys.argv[2]
below = (Align.CENTER, Align.CENTER, Align.MAX)
part = Box(30, 30, 5, align=below)
d = 2.0 if case == "countersink" else 6.0
part -= Cylinder(d / 2, 7, align=below)
def mouth(z):
    found = [e for e in part.edges() if e.geom_type == GeomType.CIRCLE and abs(e.radius - d / 2) < 1e-6 and abs(e.arc_center.Z - z) < 1e-6]
    assert len(found) == 1
    return found
for z in {"far_side": [-5.0], "both": [0.0, -5.0]}.get(case, [0.0]):
    # a 0.4 x 0.2309 cut is 30 degrees from the face; every other case is 0.5 x 45
    part = chamfer(mouth(z), 0.4, 0.2309) if case == "angled" else chamfer(mouth(z), 0.5)
export_step(part, out)
```
