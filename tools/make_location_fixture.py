"""Independent rectangular pocket for #301/#307; Python standard library only.

A 120 x 40 x 10 block, pocket x=40..80, y=17..23, floor z=7.
No repro file is read. Run from the repository root to regenerate.
"""
from pathlib import Path
import math
from make_review_fixtures import Step


class Pocket(Step):
    def __init__(self, pockets=((40, 17, 80, 23, 7),), step=False, open_end=False):
        super().__init__()
        self.pockets = pockets
        self.step = step
        self.open_end = open_end

    def box(self, dims, bores=()):
        points = [(0, 0, 0), (120, 0, 0), (120, 40, 0), (0, 40, 0),
                  (0, 0, 10), (120, 0, 10), (120, 40, 10), (0, 40, 10)]
        for x0, y0, x1, y1, floor in self.pockets:
            points += [(x0, y0, 10), (x1, y0, 10), (x1, y1, 10), (x0, y1, 10),
                       (x0, y0, floor), (x1, y0, floor), (x1, y1, floor), (x0, y1, floor)]
        step_offset = len(points)
        if self.step:
            points[5] = (120, 0, 8)
            points += [(60, 0, 10), (60, 9, 10), (120, 9, 10),
                       (60, 0, 8), (60, 9, 8), (120, 9, 8)]
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

        specs = [
            ([0, 3, 2, 1], (0, 0, -1), (1, 0, 0)),
            ([4, 5, 6, 7], (0, 0, 1), (1, 0, 0)),
            ([0, 1, 5, 4], (0, -1, 0), (1, 0, 0)),
            ([1, 2, 6, 5], (1, 0, 0), (0, 1, 0)),
            ([2, 3, 7, 6], (0, 1, 0), (-1, 0, 0)),
            ([3, 0, 4, 7], (-1, 0, 0), (0, -1, 0)),
        ]
        if self.step:
            a, b, c, d, e, f = range(step_offset, step_offset+6)
            specs[1] = ([4, a, b, c, 6, 7], (0, 0, 1), (1, 0, 0))
            specs[2] = ([0, 1, 5, d, a, 4], (0, -1, 0), (1, 0, 0))
            specs[3] = ([1, 2, 6, c, f, 5], (1, 0, 0), (0, 1, 0))
            specs += [([a, d, e, b], (1, 0, 0), (0, 1, 0)),
                      ([b, e, f, c], (0, -1, 0), (1, 0, 0)),
                      ([d, 5, f, e], (0, 0, 1), (1, 0, 0))]
        if self.open_end:
            assert len(self.pockets) == 1 and self.pockets[0][2] == 120
            specs[1] = ([4, 5, 9, 8, 11, 10, 6, 7], (0, 0, 1), (1, 0, 0))
            specs[3] = ([1, 2, 6, 10, 14, 13, 9, 5], (1, 0, 0), (0, 1, 0))
        top = specs[1][0]
        holes = []
        for i, (_, _, _, _, floor) in enumerate(self.pockets):
            o = 8 + 8*i
            walls = [([o, o+1, o+5, o+4], (0, 1, 0), (1, 0, 0)),
                     ([o+1, o+2, o+6, o+5], (-1, 0, 0), (0, 1, 0)),
                     ([o+2, o+3, o+7, o+6], (0, -1, 0), (-1, 0, 0)),
                     ([o+3, o, o+4, o+7], (1, 0, 0), (0, -1, 0))]
            if self.open_end:
                walls.pop(1)
            for indices, normal, x in walls:
                if floor > 10:
                    indices = list(reversed(indices))
                    normal = tuple(-v for v in normal)
                specs.append((indices, normal, x))
            specs.append(([o+4, o+5, o+6, o+7], (0, 0, 1), (1, 0, 0)))
            if not self.open_end:
                holes.append([o+3, o+2, o+1, o])
        faces = []
        for indices, normal, x in specs:
            plane = self.add(f"PLANE('',{self.axis(points[indices[0]], normal, x)})")
            inner = [loop(indices) for indices in holes] if indices == top else []
            faces.append(self.face(loop(indices), plane, inner=inner))
        return self.add("CLOSED_SHELL('',(" + ",".join(faces) + "))")


if __name__ == "__main__":
    out = Path("tests/fixtures/parts/location")
    out.mkdir(exist_ok=True)
    Pocket().write(out / "pocket.step")
    two = ((40, 17, 80, 23, 7), (50, 27, 70, 33, 7))
    Pocket(two).write(out / "two_pockets.step")
    # An open edge step has a physical wall at the pockets' common x=60.
    Pocket(two, step=True).write(out / "two_pockets_step.step")
    Pocket(((40, 17, 80, 23, 7), (60, 27, 80, 33, 7))).write(out / "two_pockets_sizes.step")
    Pocket(((40, 17, 120, 23, 7),), open_end=True).write(out / "blind_slot.step")
    Pocket(((40.00123, 17, 80.00234, 23, 7),)).write(out / "pocket_rounded.step")
