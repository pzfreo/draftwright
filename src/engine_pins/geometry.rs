//! Read-side identity for saved layout pins. No STEP authoring, sampling or query caches.
//! The field set follows `Part::same_brep`: defining geometry and ordered topology.
use crate::haecceity::{
    Part,
    brep::Pcurve,
    geom::{Curve, Frame, Surface},
};
use sha2::{Digest, Sha256};

struct Identity(Sha256);
impl Identity {
    fn index(&mut self, v: usize) {
        self.0.update((v as u64).to_le_bytes());
    }
    fn flag(&mut self, v: bool) {
        self.0.update([u8::from(v)]);
    }
    fn scalar(&mut self, v: f64) {
        // Kernel equality treats both signs of zero as equal. No coordinate rounding.
        self.0
            .update(if v == 0. { 0_u64 } else { v.to_bits() }.to_le_bytes());
    }
    fn point(&mut self, v: &[f64; 3]) {
        for &x in v {
            self.scalar(x);
        }
    }
    fn scalars(&mut self, vs: &[f64]) {
        self.index(vs.len());
        for &v in vs {
            self.scalar(v);
        }
    }
    fn points(&mut self, vs: &[[f64; 3]]) {
        self.index(vs.len());
        for v in vs {
            self.point(v);
        }
    }
    fn frame(&mut self, f: &Frame) {
        for v in [&f.origin, &f.x, &f.y, &f.z] {
            self.point(v);
        }
    }
    fn surface(&mut self, s: &Surface) {
        match s {
            Surface::Plane { frame } => {
                self.index(0);
                self.frame(frame);
            }
            Surface::Cylinder { frame, radius } => {
                self.index(1);
                self.frame(frame);
                self.scalar(*radius);
            }
            Surface::Cone {
                frame,
                radius,
                semi_angle,
            } => {
                self.index(2);
                self.frame(frame);
                self.scalar(*radius);
                self.scalar(*semi_angle);
            }
            Surface::Sphere { frame, radius } => {
                self.index(3);
                self.frame(frame);
                self.scalar(*radius);
            }
            Surface::Torus {
                frame,
                major,
                minor,
            } => {
                self.index(4);
                self.frame(frame);
                self.scalar(*major);
                self.scalar(*minor);
            }
            Surface::Freeform { kind, surface: s } => {
                self.index(5);
                self.index(kind.len());
                self.0.update(kind.as_bytes());
                self.index(s.degree_u);
                self.index(s.degree_v);
                self.index(s.control_points.len());
                for row in &s.control_points {
                    self.points(row);
                }
                self.index(s.weights.len());
                for row in &s.weights {
                    self.scalars(row);
                }
                self.scalars(&s.knots_u);
                self.scalars(&s.knots_v);
            }
            Surface::Other { .. } => unreachable!("same_brep rejects unresolved surfaces"),
        }
    }
    fn curve(&mut self, c: &Curve) {
        match c {
            Curve::Line { origin, dir } => {
                self.index(0);
                self.point(origin);
                self.point(dir);
            }
            Curve::Circle { frame, radius } => {
                self.index(1);
                self.frame(frame);
                self.scalar(*radius);
            }
            Curve::Ellipse {
                frame,
                major,
                minor,
            } => {
                self.index(2);
                self.frame(frame);
                self.scalar(*major);
                self.scalar(*minor);
            }
            Curve::Nurbs(c) => {
                self.index(3);
                self.index(c.degree());
                self.points(c.control_points());
                self.scalars(c.weights());
                self.scalars(c.knots());
            }
        }
    }
    fn indices(&mut self, vs: &[usize]) {
        self.index(vs.len());
        for &v in vs {
            self.index(v);
        }
    }
    fn optional_point(&mut self, p: Option<&[f64; 3]>) {
        self.flag(p.is_some());
        if let Some(p) = p {
            self.point(p);
        }
    }
}

pub(super) fn digest(part: &Part) -> Option<String> {
    // Fail closed for empty/unresolved/NaN geometry, including unsupported surfaces.
    if !part.same_brep(part) {
        return None;
    }
    let mut h = Identity(Sha256::new());
    // Domain separation makes old export-based identities safely release pins on a
    // changed source revision. The same-source reload path requires no migration.
    h.0.update(b"draftwright.read-brep/1\0");
    h.index(part.solids.len());
    for s in &part.solids {
        h.indices(&s.faces);
    }
    h.index(part.edges.len());
    for e in &part.edges {
        h.curve(&e.curve);
        h.point(&e.start);
        h.point(&e.end);
        h.index(e.vertices.0);
        h.index(e.vertices.1);
        h.flag(e.same_sense);
    }
    h.index(part.faces.len());
    for f in &part.faces {
        h.surface(&f.surface);
        h.flag(f.reversed);
        h.flag(f.solid.is_some());
        if let Some(s) = f.solid {
            h.index(s);
        }
        h.index(f.loops.len());
        for l in &f.loops {
            h.index(l.edges.len());
            for &(i, sense) in &l.edges {
                h.index(i);
                h.flag(sense);
            }
            h.optional_point(l.vertex.as_ref());
        }
        h.index(f.pcurves.len());
        for (i, p) in &f.pcurves {
            h.index(*i);
            match p {
                Pcurve::Poles(ps) => {
                    h.index(0);
                    h.index(ps.len());
                    for &(u, v) in ps {
                        h.scalar(u);
                        h.scalar(v);
                    }
                }
                Pcurve::Line { point, dir } => {
                    h.index(1);
                    for &(u, v) in [point, dir] {
                        h.scalar(u);
                        h.scalar(v);
                    }
                }
            }
        }
    }
    Some(h.0.finalize().iter().map(|b| format!("{b:02x}")).collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::haecceity::nurbs::NurbsCurve;

    fn plate() -> Part {
        crate::haecceity::read_step(include_bytes!("../../tests/fixtures/parts/plate.step"))
            .unwrap()
    }

    fn spline(mid: f64) -> Curve {
        Curve::Nurbs(
            NurbsCurve::new(
                2,
                vec![[0., 0., 0.], [1., mid, 0.], [2., 1., 0.]],
                vec![1.; 3],
                vec![0., 0., 0., 1., 1., 1.],
            )
            .unwrap(),
        )
    }

    #[test]
    fn spline_queries_and_edge_samples_do_not_change_identity() {
        let mut part = plate();
        part.edges[0].curve = spline(0.);
        let before = digest(&part).unwrap();
        if let Curve::Nurbs(c) = &part.edges[0].curve {
            c.invert([0.5, 0.1, 0.]);
        }
        part.edges[0].samples.clear();
        part.face_bounds(0);
        assert_eq!(digest(&part).unwrap(), before);
    }

    #[test]
    fn defining_geometry_and_trim_changes_are_not_rounded_away() {
        let mut part = plate();
        part.edges[0].curve = spline(0.);
        let before = digest(&part).unwrap();
        part.edges[0].curve = spline(1e-10);
        assert_ne!(digest(&part).unwrap(), before);
        part.edges[0].curve = spline(0.);
        part.faces[0].loops[0].edges[0].1 ^= true;
        assert_ne!(digest(&part).unwrap(), before);
        part.faces[0].loops[0].edges[0].1 ^= true;
        assert_eq!(digest(&part).unwrap(), before);
        part.faces[0].pcurves.push((
            0,
            Pcurve::Line {
                point: (0., 0.),
                dir: (1., 0.),
            },
        ));
        let before = digest(&part).unwrap();
        if let Pcurve::Line { point, .. } = &mut part.faces[0].pcurves.last_mut().unwrap().1 {
            point.0 = 1e-10;
        }
        assert_ne!(digest(&part).unwrap(), before);
    }

    #[test]
    fn unresolved_geometry_is_not_an_identity_proof() {
        let mut part = plate();
        part.faces[0].surface = Surface::Other { kind: "unresolved" };
        assert!(digest(&part).is_none());
        assert!(digest(&Part::new(vec![], vec![], vec![])).is_none());
    }
}
