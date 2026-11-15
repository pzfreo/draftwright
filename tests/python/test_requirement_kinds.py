"""Caller-owned PMI requirements: real build123d geometry and structural SVG assertions."""
import json
import math
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

import draftwright

try:
    import build123d as bd
except ImportError:
    bd = None


@unittest.skipIf(bd is None, "build123d is not installed")
class RequirementKinds(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def rod(self):
        shape = bd.Cylinder(5, 30)
        p = draftwright.Part(shape, "rod")
        wall = shape.faces().filter_by(bd.GeomType.CYLINDER)[0]
        ends = shape.faces().filter_by(bd.GeomType.PLANE).sort_by(bd.Axis.Z)
        return p, wall, ends

    def stepped_rod(self):
        align = (bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)
        shape = bd.Cylinder(5, 30, align=align) + bd.Pos(0, 0, -6) * bd.Cylinder(7, 6, align=align)
        p = draftwright.Part(shape, "stepped_rod")
        cylinders = shape.faces().filter_by(bd.GeomType.CYLINDER)
        wall = [f for f in cylinders if abs(f.radius - 5) < 1e-6][0]
        datum_wall = [f for f in cylinders if abs(f.radius - 7) < 1e-6][0]
        ends = shape.faces().filter_by(bd.GeomType.PLANE).sort_by(bd.Axis.Z)
        return p, wall, datum_wall, ends

    def records(self, part):
        return json.loads(part._geometry().inspect(part._pmi))["records"]

    def rendered(self, part, name, *, style=None):
        drawing = part.drawing(sheet="A3")
        if style is not None:
            drawing.update({"sheet": {"callouts": {"style": style}}})
        svg, = drawing.render(self.directory / name, formats=("svg",))
        root = ET.parse(svg).getroot()
        text = " ".join("".join(n.itertext()) for n in root.iter("{http://www.w3.org/2000/svg}text"))
        self.assertTrue(text)
        self.assertFalse([p for p in drawing.problems
                          if p["code"] in ("pmi_not_lowered", "pmi_dimensions_not_ported")], drawing.problems)
        return text

    def test_shaft_fit_and_boss_fit(self):
        p, wall, _ = self.rod()
        self.assertIsNotNone(p.shaft(wall).fit("g6").fit("h6"))
        text = self.rendered(p, "shaft")
        self.assertIn("h6", text)
        self.assertNotIn("g6", text)
        # A boss shape expands to its external cylinder, rather than its end planes.
        with bd.BuildPart() as body:
            bd.Box(30, 30, 8)
            with bd.Locations((0, 0, 4)):
                boss = bd.Cylinder(5, 12, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
        p = draftwright.Part(body.part, "boss")
        p.cylinder(boss).fit("js6")
        self.assertIn("js6", self.rendered(p, "boss"))

    def test_external_threads_default_explicit_and_left_hand(self):
        for designation, expected in (("M10", ("M10", "1.5", "6g")),
                                      ("M10x1.5-6g", ("M10", "1.5", "6g")),
                                      ("M10x1.5-6h LH", ("M10", "1.5", "6h", "LH")),
                                      ("M10x1.5-5g6g", ("M10", "1.5", "5g6g"))):
            with self.subTest(designation=designation):
                p, wall, _ = self.rod()
                p.shaft(wall).thread(designation)
                threads = [r for r in self.records(p) if r["kind"] == "external_thread"]
                self.assertEqual(len(threads), 1)
                compact = self.rendered(p, "thread_compact")
                self.assertIn("M10", compact)
                self.assertIn(expected[2], compact)
                # The engine's ISO compact notation omits the conventional coarse pitch.
                # Full callout presentation must still print that stated pitch, as well as
                # the class and hand; test both presentations without relaxing any label check.
                text = self.rendered(p, "thread", style="full")
                for label in expected:
                    self.assertIn(label, text)

    def test_toleranced_diameters_and_distances(self):
        for target in ("diameter", "distance"):
            for tolerance in ({"plus_minus": 0.023}, {"limits": (9.98, 10.04)}):
                with self.subTest(target=target, tolerance=tolerance):
                    p, wall, ends = self.rod()
                    if target == "diameter":
                        p.cylinder(wall).diameter(**tolerance)
                    else:
                        if "limits" in tolerance:
                            tolerance = {"limits": (29.98, 30.04)}
                        p.distance(ends[0], ends[-1]).tolerance(**tolerance)
                    text = self.rendered(p, target)
                    if "plus_minus" in tolerance:
                        self.assertIn("±0.023", text)
                    else:
                        for bound in tolerance["limits"]:
                            self.assertIn(str(bound), text)
        # A bore and a generic cylinder face use the same diameter API.
        shape = bd.Cylinder(8, 30) - bd.Cylinder(5, 30)
        p = draftwright.Part(shape, "bore")
        wall = [f for f in shape.faces().filter_by(bd.GeomType.CYLINDER)
                if p._is_hole_wall(f)][0]
        with self.assertRaisesRegex(ValueError, "external cylinder"):
            p.shaft(wall)
        with self.assertRaisesRegex(ValueError, "external cylinder"):
            p.cylinder(wall).knurl(0.8, teeth=39)
        with self.assertRaisesRegex(ValueError, "shaft fit"):
            p.cylinder(wall).fit("h6")
        p.hole(wall).diameter(plus_minus=0.017)
        self.assertIn("±0.017", self.rendered(p, "bore"))
        p, wall, _ = self.rod()
        p.face(wall).diameter(limits=(9.98, 10.04))
        self.assertIn("10.04", self.rendered(p, "face_diameter"))

    def test_geometric_controls(self):
        controls = (("perpendicularity", "⟂", True, False),
                    ("parallelism", "∥", True, False),
                    ("cylindricity", "⌭", False, True),
                    ("circular_runout", "↗", True, True),
                    ("total_runout", "⌰", True, True),
                    ("profile_surface", "⌓", True, False),
                    ("concentricity", "◎", True, True))
        for kind, symbol, needs_datum, cylinder in controls:
            with self.subTest(kind=kind):
                p, wall, datum_wall, ends = self.stepped_rod()
                datum_face = datum_wall if kind in ("perpendicularity", "circular_runout", "total_runout", "concentricity") else ends[0]
                A = p.datum("A", datum_face)
                ref = p.shaft(wall) if cylinder else p.face(ends[-1])
                args = (0.041, A) if needs_datum else (0.041,)
                self.assertIs(getattr(ref, kind)(*args), ref)
                record = [r for r in self.records(p) if r["kind"] == kind]
                self.assertEqual(len(record), 1)
                self.assertEqual(record[0]["datum_refs"], ["A"] if needs_datum else [])
                text = self.rendered(p, kind)
                self.assertIn(symbol, text)
                self.assertIn("0.041", text)
                if needs_datum:
                    self.assertIn("A", text)
        # A datum-free profile is valid too; different control kinds coexist and edits replace.
        p, wall, _ = self.rod()
        ref = p.shaft(wall).cylindricity(0.087).profile_surface(0.062)
        ref.cylindricity(0.031)
        text = self.rendered(p, "edited_controls")
        self.assertIn("0.031", text)
        self.assertIn("0.062", text)
        self.assertNotIn("0.087", text)

    def test_orientation_zone_follows_target_in_svg_frame(self):
        for kind, symbol in (("perpendicularity", "⟂"), ("parallelism", "∥")):
            for cylinder in (True, False):
                with self.subTest(kind=kind, cylinder=cylinder):
                    p, wall, datum_wall, ends = self.stepped_rod()
                    datum_face = (ends[0] if cylinder else datum_wall) if kind == "perpendicularity" else (datum_wall if cylinder else ends[0])
                    A = p.datum("A", datum_face)
                    ref = p.shaft(wall) if cylinder else p.face(ends[-1])
                    getattr(ref, kind)(0.041, A)
                    text = self.rendered(p, kind + str(cylinder))
                    # Match the characteristic, zone, magnitude and datum together;
                    # an unrelated diameter elsewhere on the sheet cannot satisfy this.
                    zone = r"⌀\s*" if cylinder else ""
                    self.assertRegex(text, symbol + r"\s+" + zone + r"0\.041\s+A")
                    if not cylinder:
                        self.assertNotRegex(text, symbol + r"\s+⌀")

    def test_controls_refuse_their_own_datum_at_the_call(self):
        for kind in ("perpendicularity", "parallelism", "circular_runout", "total_runout",
                     "concentricity", "profile_surface"):
            for datum_object in (True, False):
                with self.subTest(kind=kind, datum_object=datum_object):
                    p, wall, ends = self.rod()
                    p.datum("A", ends[0])
                    B = p.datum("B", wall)
                    ref = p.shaft(wall)
                    before = p._geometry().inspect(p._pmi)
                    with self.assertRaisesRegex(ValueError, "datum B shares faces"):
                        getattr(ref, kind)(0.1, "A", B if datum_object else "B")
                    self.assertEqual(p._geometry().inspect(p._pmi), before)
        p, _, ends = self.rod()
        A = p.datum("A", [ends[0], ends[-1]])
        with self.assertRaisesRegex(ValueError, "shares faces"):
            p.face([ends[-1], ends[0]]).profile_surface(0.1, A)
        p, wall, _ = self.rod()
        with self.assertRaisesRegex(ValueError, "shares faces"):
            p.cylinder(wall).parallelism(0.1, p.datum("A", wall))

        shape = bd.Cylinder(8, 30) - bd.Cylinder(5, 30)
        p = draftwright.Part(shape, "self_datum_bore")
        bore = [f for f in shape.faces().filter_by(bd.GeomType.CYLINDER) if p._is_hole_wall(f)][0]
        A = p.datum("A", bore)
        with self.assertRaisesRegex(ValueError, "shares faces"):
            p.hole(bore).position(0.1, A)
        p, wall, _, ends = self.stepped_rod()
        A = p.datum("A", ends[0])
        for kind in ("perpendicularity", "parallelism"):
            with self.assertRaisesRegex(ValueError, "only planes or only cylinders"):
                getattr(p.face([wall, ends[-1]]), kind)(0.1, A)

    def test_controls_refuse_partial_datum_overlap_without_changing_pmi(self):
        for datum_has_extra_face in (True, False):
            with self.subTest(datum_has_extra_face=datum_has_extra_face):
                p, _, ends = self.rod()
                both = [ends[0], ends[-1]]
                datum_faces = both if datum_has_extra_face else ends[0]
                target_faces = ends[-1] if datum_has_extra_face else both
                A = p.datum("A", datum_faces)
                ref = p.face(target_faces)
                before = p._geometry().inspect(p._pmi)
                with self.assertRaisesRegex(ValueError, "datum A shares faces"):
                    ref.profile_surface(0.1, A)
                self.assertEqual(p._geometry().inspect(p._pmi), before)

    def test_threads_validate_every_measured_diameter_at_the_call(self):
        p, wall, _ = self.rod()
        ref = p.shaft(wall)
        before = p._geometry().inspect(p._pmi)
        for size in ("M12", "M8"):
            with self.assertRaisesRegex(ValueError, "measured external cylinder diameter 10"):
                ref.thread(size)
            self.assertEqual(p._geometry().inspect(p._pmi), before)
        for diameter, accepted in ((10.009, True), (10.011, False)):
            shape = bd.Cylinder(diameter / 2, 30)
            p = draftwright.Part(shape, "external_boundary")
            ref = p.shaft(shape.faces().filter_by(bd.GeomType.CYLINDER)[0])
            if accepted:
                ref.thread("M10")
                self.assertIn("M10-6g", self.rendered(p, "external_boundary"))
            else:
                with self.assertRaisesRegex(ValueError, "measured external"):
                    ref.thread("M10")
        # Same tapping-drill range as the engine: M6x1 accepts 4.5..6 mm,
        # with 0.01 mm slack at each boundary. Check both sides of each boundary.
        for diameter, accepted in ((4.495, True), (6.005, True), (4.48, False), (6.02, False)):
            with self.subTest(internal_diameter=diameter):
                shape = bd.Cylinder(8, 30) - bd.Cylinder(diameter / 2, 30)
                p = draftwright.Part(shape, "internal_boundary")
                wall = [f for f in shape.faces().filter_by(bd.GeomType.CYLINDER) if p._is_hole_wall(f)][0]
                ref = p.hole(wall)
                if accepted:
                    ref.thread("M6")
                    self.assertIn("M6-6H", self.rendered(p, "internal_boundary"))
                else:
                    before = p._geometry().inspect(p._pmi)
                    with self.assertRaisesRegex(ValueError, "measured internal"):
                        ref.thread("M6")
                    self.assertEqual(p._geometry().inspect(p._pmi), before)

    def test_fit_and_numeric_diameter_replace_each_other(self):
        p, wall, _ = self.rod()
        ref = p.shaft(wall).fit("h6")
        p.face(wall).diameter(plus_minus=0.023)
        text = self.rendered(p, "fit_to_size")
        self.assertIn("±0.023", text)
        self.assertNotIn("h6", text)
        ref.fit("g6")
        text = self.rendered(p, "size_to_fit")
        self.assertIn("g6", text)
        self.assertNotIn("0.023", text)
        self.assertEqual(len([r for r in self.records(p) if r["kind"] == "diameter"]), 1)

    def test_straight_knurl_refuses_any_helix_angle(self):
        p, wall, _ = self.rod()
        ref = p.shaft(wall)
        before = p._geometry().inspect(p._pmi)
        for angle in (0, 30, 90, -1):
            with self.assertRaisesRegex(ValueError, "straight knurl takes no helix_angle"):
                ref.knurl(0.8, "straight", teeth=39, helix_angle=angle)
            self.assertEqual(p._geometry().inspect(p._pmi), before)

    def test_knurls(self):
        for pattern in ("straight", "diamond"):
            with self.subTest(pattern=pattern):
                p, wall, _ = self.rod()
                ref = p.shaft(wall)
                ref.knurl(0.8, pattern, teeth=39, **({"helix_angle": 30} if pattern == "diamond" else {}))
                self.assertEqual(len([r for r in self.records(p) if r["kind"] == "knurl"]), 1)
                text = self.rendered(p, pattern)
                self.assertIn(f"{pattern.upper()} KNURL P0.8", text)
                self.assertIn("MAX AFTER KNURL", text)

    def test_edits_replace_sizes_threads_knurls_and_reversed_distances(self):
        for kind in ("diameter", "thread", "knurl", "distance"):
            with self.subTest(kind=kind):
                p, wall, ends = self.rod()
                ref = p.shaft(wall)
                if kind == "diameter":
                    ref.diameter(plus_minus=0.087)
                elif kind == "thread":
                    ref.thread("M10x1.5-6h LH")
                elif kind == "knurl":
                    ref.knurl(0.8, teeth=39)
                else:
                    p.distance(ends[0], ends[-1]).tolerance(plus_minus=0.065)
                geometry = p._geometry()
                if kind == "diameter":
                    p.cylinder(wall).diameter(limits=(9.98, 10.04))
                    new, old, record_kind = "10.04", "0.087", "diameter"
                elif kind == "thread":
                    p.shaft(wall).thread("M10x1.5-6g")
                    new, old, record_kind = "6g", "6h", "external_thread"
                elif kind == "knurl":
                    p.shaft(wall).knurl(1, "diamond", teeth=31, helix_angle=30)
                    new, old, record_kind = "DIAMOND KNURL P1", "STRAIGHT KNURL", "knurl"
                else:
                    p.distance(ends[-1], ends[0]).tolerance(plus_minus=0.019)
                    new, old, record_kind = "±0.019", "0.065", "linear"
                self.assertIs(p._geometry(), geometry)
                self.assertEqual(len([r for r in self.records(p) if r["kind"] == record_kind]), 1)
                text = self.rendered(p, "edited_" + kind)
                self.assertIn(new, text)
                self.assertNotIn(old, text)

    def test_free_text_general_tolerance(self):
        p, _, _ = self.rod()
        p.general_tolerance("±0.05")
        self.assertIn("±0.05", self.rendered(p, "general"))

    def test_invalid_calls_fail_without_changing_pmi(self):
        p, wall, ends = self.rod()
        ref = p.shaft(wall)
        before = p._geometry().inspect(p._pmi)
        invalid = [lambda: ref.fit("h99"), lambda: ref.fit("zz6"), lambda: ref.fit("H7"),
                   lambda: ref.thread("M10-6H"), lambda: ref.thread("M10-99g"),
                   lambda: ref.thread("M10-6z"), lambda: ref.thread("not a thread"),
                   lambda: p.shaft(ends[0]), lambda: p.face(ends[0]).diameter(plus_minus=0.1),
                   lambda: p.face(ends[0]).cylindricity(0.1),
                   lambda: p.face(ends[0]).concentricity(0.1, "A"),
                   lambda: ref.perpendicularity(0.1), lambda: ref.parallelism(0.1),
                   lambda: ref.circular_runout(0.1), lambda: ref.total_runout(0.1),
                   lambda: ref.concentricity(0.1), lambda: ref.profile_surface(0.1, "A"),
                   lambda: ref.parallelism(0.1, "A"), lambda: ref.cylindricity(-0.1),
                   lambda: ref.knurl(0.8, "diagonal", teeth=39),
                   lambda: ref.knurl(0.8, "diamond", teeth=39),
                   lambda: ref.knurl(0.8, teeth=2), lambda: ref.knurl(0, teeth=39),
                   lambda: ref.knurl(0.8, "diamond", teeth=39, helix_angle=100),
                   lambda: ref.diameter(), lambda: ref.diameter(plus_minus=-0.1),
                   lambda: ref.diameter(plus_minus=math.inf),
                   lambda: ref.diameter(limits=(10.1, 10.2)),
                   lambda: ref.diameter(limits=(10.1, 9.9)),
                   lambda: ref.diameter(plus_minus=0.1, limits=(9.9, 10.1)),
                   lambda: p.distance(wall, ends[0]), lambda: p.distance(ends[0], ends[0])]
        for call in invalid:
            with self.subTest(call=call):
                with self.assertRaises((ValueError, draftwright.DraftwrightError)) as error:
                    call()
                self.assertTrue(str(error.exception))
                self.assertEqual(p._geometry().inspect(p._pmi), before)
        foreign, _, foreign_ends = self.rod()
        A = foreign.datum("A", foreign_ends[0])
        with self.assertRaisesRegex(ValueError, "another Part"):
            ref.perpendicularity(0.1, A)
        p.datum("A", ends[0])
        for kind in ("perpendicularity", "parallelism", "circular_runout", "total_runout",
                     "profile_surface", "concentricity"):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "not been declared"):
                getattr(ref, kind)(0.1, "B")
        ref.thread("M10")
        before = p._geometry().inspect(p._pmi)
        for call in (lambda: p.shaft(wall).fit("h6"),
                     lambda: p.face(wall).diameter(plus_minus=0.1),
                     lambda: ref.knurl(0.8, teeth=39)):
            with self.assertRaises((ValueError, draftwright.DraftwrightError)):
                call()
            self.assertEqual(p._geometry().inspect(p._pmi), before)
        # A rejected thread does not poison the fluent handle.
        p, wall, _ = self.rod()
        ref = p.shaft(wall)
        with self.assertRaises(draftwright.DraftwrightError):
            ref.thread("M10-6z")
        ref.fit("h6")
        self.assertIn("h6", self.rendered(p, "after_error"))


if __name__ == "__main__":
    unittest.main()
