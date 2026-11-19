"""#520: 40 x 20 x 10 blocks whose right-hand corner cut starts 8 mm up the side and leans 20, 25
and 30 degrees from the vertical, plus R3 variants of the 25 degree cut; build123d 0.13.0.
Writes block-cut-*.step.gz here."""
import gzip, math, tempfile
from pathlib import Path
from build123d import Polygon, export_step, extrude, fillet

out = Path(__file__).resolve().parent


def write(part, name):
    with tempfile.TemporaryDirectory() as t:
        export_step(part, Path(t) / "p.step")
        data = (Path(t) / "p.step").read_bytes()
    (out / f"{name}.step.gz").write_bytes(gzip.compress(data, mtime=0))


for degrees in (20, 25, 30):
    far = 40 - 12 * math.tan(math.radians(degrees))
    part = extrude(Polygon((0, 0), (40, 0), (40, 8), (far, 20), (0, 20), align=None), 10)
    write(part, f"block-cut-{degrees}")
    if degrees == 25:
        for end, x, y in [("start", 40, 8), ("far", far, 20)]:
            (edge,) = (
                e for e in part.edges()
                if abs(e.center().X - x) < 1e-6 and abs(e.center().Y - y) < 1e-6
            )
            write(fillet(edge, 3), f"block-cut-25-{end}-r3")
