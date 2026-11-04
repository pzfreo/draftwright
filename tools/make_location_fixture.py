"""Independent rectangular pocket for #301/#307; Python standard library only.

A 120 x 40 x 10 block, pocket x=40..80, y=17..23, floor z=7.
No repro file is read. Run from the repository root to regenerate.
"""
from pathlib import Path
import math
from make_review_fixtures import Step


class Pocket(Step):
    def box(self, dims, bores=()):
        points = [(0, 0, 0), (120, 0, 0), (120, 40, 0), (0, 40, 0),
                  (0, 0, 10), (120, 0, 10), (120, 40, 10), (0, 40, 10),
                  (40, 17, 10), (80, 17, 10), (80, 23, 10), (40, 23, 10),
                  (40, 17, 7), (80, 17, 7), (80, 23, 7), (40, 23, 7)]
        vertices = [self.add(f"VERTEX_POINT('',{self.point(p)})") for p in points]
        edges = {}

        def loop(indices):
            result = []
            for a, b in zip(indices, indices[1:] + indices[:1]):
                key = tuple(sorted((a, b)))
                if key not in edges:
                    u, v = key
                    delta = tuple(points[v][k] - points[u][k] for k in range(3))
                    length = math.sqrt(sum(c*c for c in delta))
                    vector = self.add(f"VECTOR('',{self.direction(tuple(c/length for c in delta))},1.)")
                    line = self.add(f"LINE('',{self.point(points[u])},{vector})")
                    edges[key] = self.add(f"EDGE_CURVE('',{vertices[u]},{vertices[v]},{line},.T.)")
                result.append((edges[key], ".T." if a < b else ".F."))
            return result

        faces = []
        for indices, normal, x in [
            ([0, 3, 2, 1], (0, 0, -1), (1, 0, 0)),
            ([4, 5, 6, 7], (0, 0, 1), (1, 0, 0)),
            ([0, 1, 5, 4], (0, -1, 0), (1, 0, 0)),
            ([1, 2, 6, 5], (1, 0, 0), (0, 1, 0)),
            ([2, 3, 7, 6], (0, 1, 0), (-1, 0, 0)),
            ([3, 0, 4, 7], (-1, 0, 0), (0, -1, 0)),
            ([8, 9, 13, 12], (0, 1, 0), (1, 0, 0)),
            ([9, 10, 14, 13], (-1, 0, 0), (0, 1, 0)),
            ([10, 11, 15, 14], (0, -1, 0), (-1, 0, 0)),
            ([11, 8, 12, 15], (1, 0, 0), (0, -1, 0)),
            ([12, 13, 14, 15], (0, 0, 1), (1, 0, 0)),
        ]:
            plane = self.add(f"PLANE('',{self.axis(points[indices[0]], normal, x)})")
            inner = [loop([11, 10, 9, 8])] if indices == [4, 5, 6, 7] else []
            faces.append(self.face(loop(indices), plane, inner=inner))
        return self.add("CLOSED_SHELL('',(" + ",".join(faces) + "))")


if __name__ == "__main__":
    out = Path("tests/fixtures/parts/location")
    out.mkdir(exist_ok=True)
    Pocket().write(out / "pocket.step")
