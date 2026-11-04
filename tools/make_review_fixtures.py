"""Independently authored analytic B-reps for review regressions; Python stdlib only.

No source STEP is read. The plate is 60 x 40 x 8 mm. The void specimen is a
32 x 24 x 20 mm block with four sealed diameter-2.5, length-12 cylinders.
It also writes genuine opposed blind openings and a turned axial blind opening
for placement pins whose old fixtures accidentally contained sealed cavities.
Run from the repository root to regenerate these uncompressed fixtures.
"""
from pathlib import Path
import math


class Step:
    def __init__(self, along_x=False):
        self.entities = []
        self.along_x = along_x

    def add(self, body):
        self.entities.append(body)
        return f"#{len(self.entities)}"

    def point(self, xyz):
        if self.along_x:
            xyz = (xyz[2], xyz[0], xyz[1])
        return self.add("CARTESIAN_POINT('',(" + ",".join(f"{v:.9f}" for v in xyz) + "))")

    def direction(self, xyz):
        if self.along_x:
            xyz = (xyz[2], xyz[0], xyz[1])
        return self.add("DIRECTION('',(" + ",".join(f"{v:.9f}" for v in xyz) + "))")

    def axis(self, at, z=(0, 0, 1), x=(1, 0, 0)):
        return self.add(f"AXIS2_PLACEMENT_3D('',{self.point(at)},{self.direction(z)},{self.direction(x)})")

    def loop(self, edges):
        return self.add("EDGE_LOOP('',(" + ",".join(self.add(f"ORIENTED_EDGE('',*,*,{e},{sense})") for e, sense in edges) + "))")

    def face(self, edges, surface, sense=".T.", inner=()):
        bounds = [self.add(f"FACE_OUTER_BOUND('',{self.loop(edges)},.T.)")]
        bounds += [self.add(f"FACE_BOUND('',{self.loop(loop)},.T.)") for loop in inner]
        return self.add(f"ADVANCED_FACE('',({','.join(bounds)}),{surface},{sense})")

    def circle_edge(self, x, y, z, radius):
        circle = self.add(f"CIRCLE('',{self.axis((x, y, z))},{radius})")
        vertex = self.add(f"VERTEX_POINT('',{self.point((x+radius, y, z))})")
        return self.add(f"EDGE_CURVE('',{vertex},{vertex},{circle},.T.)")

    def blind_bore(self, x, y, mouth, floor, radius):
        low, high = sorted((mouth, floor))
        edges = {z: self.circle_edge(x, y, z, radius) for z in (low, high)}
        cylinder = self.add(f"CYLINDRICAL_SURFACE('',{self.axis((x, y, low))},{radius})")
        bounds = [self.add(f"FACE_BOUND('',{self.loop([(edges[z], sense)])},.T.)") for z, sense in ((low, ".F."), (high, ".T."))]
        side = self.add(f"ADVANCED_FACE('',({','.join(bounds)}),{cylinder},.F.)")
        plane = self.add(f"PLANE('',{self.axis((x, y, floor))})")
        sense = ".T." if mouth > floor else ".F."
        cap = self.face([(edges[floor], sense)], plane, sense)
        return edges[mouth], [side, cap]

    def box(self, dims, bores=()):
        pts = [(0, 0, 0), (dims[0], 0, 0), (dims[0], dims[1], 0), (0, dims[1], 0),
               (0, 0, dims[2]), (dims[0], 0, dims[2]), tuple(dims), (0, dims[1], dims[2])]
        vertices = [self.add(f"VERTEX_POINT('',{self.point(p)})") for p in pts]
        cache = {}

        def edge(a, b):
            key = tuple(sorted((a, b)))
            if key not in cache:
                u, v = key
                delta = tuple(pts[v][k] - pts[u][k] for k in range(3))
                length = math.sqrt(sum(v*v for v in delta))
                vector = self.add(f"VECTOR('',{self.direction(tuple(v/length for v in delta))},1.)")
                line = self.add(f"LINE('',{self.point(pts[u])},{vector})")
                cache[key] = self.add(f"EDGE_CURVE('',{vertices[u]},{vertices[v]},{line},.T.)")
            return cache[key], ".T." if a < b else ".F."

        mouths = {0: [], dims[2]: []}
        bore_faces = []
        for x, y, mouth, floor, radius in bores:
            edge_at_mouth, inside = self.blind_bore(x, y, mouth, floor, radius)
            mouths[mouth].append([(edge_at_mouth, ".T." if mouth == 0 else ".F.")])
            bore_faces.extend(inside)
        faces = []
        for indices, normal, x in [((0, 3, 2, 1), (0, 0, -1), (1, 0, 0)),
                                   ((4, 5, 6, 7), (0, 0, 1), (1, 0, 0)),
                                   ((0, 1, 5, 4), (0, -1, 0), (1, 0, 0)),
                                   ((1, 2, 6, 5), (1, 0, 0), (0, 1, 0)),
                                   ((2, 3, 7, 6), (0, 1, 0), (-1, 0, 0)),
                                   ((3, 0, 4, 7), (-1, 0, 0), (0, -1, 0))]:
            plane = self.add(f"PLANE('',{self.axis(pts[indices[0]], normal, x)})")
            inner = mouths[0] if normal == (0, 0, -1) else mouths[dims[2]] if normal == (0, 0, 1) else []
            faces.append(self.face([edge(indices[i], indices[(i+1) % 4]) for i in range(4)], plane, inner=inner))
        return self.add("CLOSED_SHELL('',(" + ",".join(faces + bore_faces) + "))")

    def cavity(self, x, y):
        # One periodic cylinder and two circular caps, all oriented into the void.
        edges = []
        for z in (4, 16):
            circle = self.add(f"CIRCLE('',{self.axis((x, y, z))},1.25)")
            vertex = self.add(f"VERTEX_POINT('',{self.point((x+1.25, y, z))})")
            edges.append(self.add(f"EDGE_CURVE('',{vertex},{vertex},{circle},.T.)"))
        cylinder = self.add(f"CYLINDRICAL_SURFACE('',{self.axis((x, y, 4))},1.25)")
        bounds = [self.add(f"FACE_BOUND('',{self.loop([(e, sense)])},.T.)") for e, sense in zip(edges, (".F.", ".T."))]
        side = self.add(f"ADVANCED_FACE('',({','.join(bounds)}),{cylinder},.F.)")
        bottom = self.add(f"PLANE('',{self.axis((x, y, 4))})")
        top = self.add(f"PLANE('',{self.axis((x, y, 16))})")
        faces = [side, self.face([(edges[0], ".T.")], bottom), self.face([(edges[1], ".F.")], top, ".F.")]
        shell = self.add("CLOSED_SHELL('',(" + ",".join(faces) + "))")
        return self.add(f"ORIENTED_CLOSED_SHELL('',*,{shell},.T.)")

    def cylinder(self, radius, height, bore_radius, depth):
        bottom = self.circle_edge(0, 0, 0, radius)
        top = self.circle_edge(0, 0, height, radius)
        mouth, inside = self.blind_bore(0, 0, height, height-depth, bore_radius)
        cylinder = self.add(f"CYLINDRICAL_SURFACE('',{self.axis((0, 0, 0))},{radius})")
        bounds = [self.add(f"FACE_BOUND('',{self.loop([(e, sense)])},.T.)") for e, sense in ((bottom, ".F."), (top, ".T."))]
        side = self.add(f"ADVANCED_FACE('',({','.join(bounds)}),{cylinder},.T.)")
        low = self.add(f"PLANE('',{self.axis((0, 0, 0))})")
        high = self.add(f"PLANE('',{self.axis((0, 0, height))})")
        faces = [side, self.face([(bottom, ".F.")], low, ".F."), self.face([(top, ".T.")], high, inner=[[(mouth, ".F.")]]), *inside]
        return self.add("CLOSED_SHELL('',(" + ",".join(faces) + "))")

    def write(self, path, dims=None, voids=False, bores=(), cylinder=None):
        shell = self.cylinder(*cylinder) if cylinder else self.box(dims, bores)
        if voids:
            inner = [self.cavity(x, y) for x, y in ((8, 8), (24, 8), (8, 16), (24, 16))]
            solid = self.add(f"BREP_WITH_VOIDS('',{shell},({','.join(inner)}))")
        else:
            solid = self.add(f"MANIFOLD_SOLID_BREP('',{shell})")
        app = self.add("APPLICATION_CONTEXT('managed model based 3d engineering')")
        self.add(f"APPLICATION_PROTOCOL_DEFINITION('international standard','ap242_managed_model_based_3d_engineering',2014,{app})")
        context = self.add(f"PRODUCT_CONTEXT('',{app},'mechanical')")
        product = self.add(f"PRODUCT('synthetic','Independently authored review specimen','',({context}))")
        formation = self.add(f"PRODUCT_DEFINITION_FORMATION('','',{product})")
        pdcontext = self.add(f"PRODUCT_DEFINITION_CONTEXT('part definition',{app},'design')")
        definition = self.add(f"PRODUCT_DEFINITION('design','',{formation},{pdcontext})")
        shape = self.add(f"PRODUCT_DEFINITION_SHAPE('','',{definition})")
        mm = self.add("(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.))")
        rad = self.add("(NAMED_UNIT(*)PLANE_ANGLE_UNIT()SI_UNIT($,.RADIAN.))")
        sr = self.add("(NAMED_UNIT(*)SI_UNIT($,.STERADIAN.)SOLID_ANGLE_UNIT())")
        unc = self.add(f"UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-7),{mm},'distance_accuracy_value','')")
        repcontext = self.add(f"(GEOMETRIC_REPRESENTATION_CONTEXT(3)GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT(({unc}))GLOBAL_UNIT_ASSIGNED_CONTEXT(({mm},{rad},{sr}))REPRESENTATION_CONTEXT('',''))")
        rep = self.add(f"ADVANCED_BREP_SHAPE_REPRESENTATION('',({solid},{self.axis((0,0,0))}),{repcontext})")
        self.add(f"SHAPE_DEFINITION_REPRESENTATION({shape},{rep})")
        path.write_text("ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('Independent synthetic regression specimen'),'2;1');\nFILE_NAME('synthetic.step','2026-11-04T00:00:00',('draftwright'),(''),'','','');\nFILE_SCHEMA(('AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF'));\nENDSEC;\nDATA;\n" + "\n".join(f"#{i}={e};" for i,e in enumerate(self.entities, 1)) + "\nENDSEC;\nEND-ISO-10303-21;\n")


if __name__ == "__main__":
    directory = Path("tests/fixtures/parts")
    Step().write(directory / "synthetic_note_plate.step", (60, 40, 8))
    Step().write(directory / "synthetic_sealed_voids.step", (32, 24, 20), True)

    Step().write(directory / "synthetic_opposed_taps.step", (40, 40, 20),
                 bores=[(20, 20, 20, 14, 1), (20, 20, 0, 3, 2)])
    # Off-centre holes share y=12, so coverage requires both location witnesses (#294).
    Step().write(directory / "synthetic_coincident_locations.step", (60, 40, 8),
                 bores=[(15, 12, 8, 3, 3), (40, 12, 8, 2, 4)])
    Step(along_x=True).write(directory / "rules-text/synthetic-coaxial-cylinder-x.step",
                            cylinder=(6, 20, 0.8, 8))
