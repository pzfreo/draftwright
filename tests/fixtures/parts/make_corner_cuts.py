"""#520: 40 x 20 x 10 blocks whose right-hand corner cut starts 8 mm up the side and leans 20, 25
and 30 degrees from the vertical; build123d 0.13.0. Writes block-cut-<degrees>.step.gz here."""
import gzip, math, tempfile
from pathlib import Path
from build123d import Polygon, export_step, extrude

out = Path(__file__).resolve().parent
for degrees in (20, 25, 30):
    far = 40 - 12 * math.tan(math.radians(degrees))
    part = extrude(Polygon((0, 0), (40, 0), (40, 8), (far, 20), (0, 20), align=None), 10)
    with tempfile.TemporaryDirectory() as t:
        export_step(part, Path(t) / "p.step")
        data = (Path(t) / "p.step").read_bytes()
    (out / f"block-cut-{degrees}.step.gz").write_bytes(gzip.compress(data, mtime=0))
