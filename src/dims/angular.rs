//! Angular PMI dimensions: an AP242 angle between two planar faces, drawn where it shows true.
//!
//! - **Its sides** ([`geometry`], read with the part when it is analysed): draftwright's
//!   `_angular_reference` and `_angular_reference_from_planes` (`pmi.py`). Each reference group
//!   is one planar face; the vertex is the point of the planes' line of intersection nearest the
//!   midpoint of the two groups' box centres, and each side's witness is the point of its plane
//!   in the common normal section that its box centre selects (the minor sector between the two
//!   half-rays). The angle so measured must be the record's nominal to 0.01°; otherwise, or when
//!   a group is not one planar face, or the planes are parallel, it is not drawn, with
//!   draftwright's reason.
//! - **Its view**: the one that looks along the normal of the plane of its two sides, x, y or z to
//!   1e-6 (draftwright's `AngularReference.principal_axis` and `_angular_specs`: z the plan, y the
//!   front, x the side), so both sides project edge-on and the angle shows true. An angle whose
//!   sides' plane is oblique to every view is not drawn (draftwright's
//!   `dimension_kind_unsupported`: "supported angular ink requires explicit coplanar rays in a
//!   principal view"); it is never drawn foreshortened.
//! - **Its ink** ([`place`]): draftwright's `AngularGeometry` (`angular_geometry.py`) and
//!   `AngularDimension` (`annotations/angular.py`): two extension lines along the sides, from just
//!   past each witness to just past the arc; an unbroken arc about the vertex across the minor
//!   sector, an arrowhead at each end (ISO 129-1); the label on the bisector, aligned with the
//!   arc's tangent, turned to read from below or the right, and set just outside the arc,
//!   [`LIFT`] clear of it as a linear label is of its line. The label's centre lies where an
//!   inline label stood when the arc was broken around it: the smallest radius that keeps both
//!   arrowheads clear of the label's width (and a pad) about its station; the arc runs inside it,
//!   so the ink reaches no further than before. A profile corner's angle ([`place_profile`])
//!   instead starts at the smallest radius whose arc carries both arrowheads with room between
//!   them and whose label stays within the sector, [`GAP`] clear of both extension lines
//!   ([`Ink::compact`]; #227: the label no longer interrupts the arc, so an acute corner's arc
//!   need not clear its width and stays near its vertex). PMI and notch angles keep the wider
//!   radius: the recess callouts do not yet route around a notch arc brought in, and 10020's
//!   21° notch then sent its sheet from A4 1:1 to A3 2:1. Witness stations select the support
//!   half-rays, without imposing a radius. Both the physical sector and the equal sector on the
//!   two extended supports are tried, in increasing radius, in 1 mm steps, whose ink stays on the
//!   sheet, its label off the views, the title block and the other items, and whose lines cross
//!   no other view, dimension or label. With none, it keeps the smallest radius whose label is
//!   clear of the views, and the collision checks report what it hits: nothing is dropped. The
//!   arc is drawn as chords within 0.005 mm of it (draftwright's sampling).
//!
//! The label is the record's with its unit on every value (`60° ±0.5°`): draftwright's
//! `implicit_degrees` left the unit to the record's kind, which a reader of the sheet cannot see.

use haecceity::Part;
use haecceity::geom::Surface;
use serde::{Deserialize, Serialize};

use super::contacts::clear_box;
use super::placement::{
    Frame, LabelInk, Page, boxes_overlap, crosses, ink_points, ink_through, label_box,
};
use super::{ARROW, GAP, LIFT, OVERSHOOT, P2, P3, Placed, Shape, Want, font, label_ink};
use crate::pmi::{Geometry, Record};

/// An angle's two sides, draftwright's `AngularReference` with its `minor` sector: the vertex
/// and a witness point on each side, world mm (the drawing's frame).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Angle {
    pub vertex: [f64; 3],
    pub first: [f64; 3],
    pub second: [f64; 3],
}

impl From<&crate::lint::profile_angles::Corner> for Angle {
    fn from(c: &crate::lint::profile_angles::Corner) -> Self {
        Self {
            vertex: c.vertex,
            first: c.first,
            second: c.second,
        }
    }
}

fn sub(a: P3, b: P3) -> P3 {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}

fn dot(a: P3, b: P3) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

pub(super) fn cross(a: P3, b: P3) -> P3 {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}

fn norm(a: P3) -> f64 {
    dot(a, a).sqrt()
}

impl Angle {
    /// Its two sides' unit directions from the vertex; none when a witness is at the vertex or
    /// the sides are parallel (draftwright's `AngularReference` validation).
    pub fn rays(&self) -> Option<[P3; 2]> {
        let unit = |p: P3| {
            let d = sub(p, self.vertex);
            let n = norm(d);
            (n.is_finite() && n > 1e-9).then(|| d.map(|c| c / n))
        };
        let rays = [unit(self.first)?, unit(self.second)?];
        (norm(cross(rays[0], rays[1])) > 1e-9).then_some(rays)
    }

    /// The angle between its sides, degrees.
    pub fn degrees(&self) -> Option<f64> {
        let [a, b] = self.rays()?;
        Some(norm(cross(a, b)).atan2(dot(a, b)).to_degrees())
    }

    /// The world axis (0, 1, 2) along the normal of its sides' plane, when that is x, y or z to
    /// 1e-6 (draftwright's `principal_axis`): the view along it shows the angle true.
    pub fn principal(&self) -> Option<usize> {
        let [a, b] = self.rays()?;
        let c = cross(a, b);
        let n = c.map(|x| x / norm(c));
        let k = (0..3).fold(0, |k, i| if n[i].abs() > n[k].abs() { i } else { k });
        (0..3).all(|i| i == k || n[i].abs() <= 1e-6).then_some(k)
    }
}

/// An angular dimension's sides from its faces, into *g*: its [`Angle`] and the axis its view
/// looks along (`axis`, when principal), or the reasons it has none (draftwright's
/// `_angular_reference`; see the module's notes).
pub(crate) fn geometry(part: &Part, r: &Record, g: &mut Geometry) {
    let groups = &r.face_groups;
    let edges = |i: usize| r.edge_groups.get(i).map_or(0, Vec::len);
    if groups.len() != 2 || (0..2).any(|i| groups[i].len() + edges(i) != 1) {
        g.reasons
            .push("angular dimension needs one planar face in each reference group".into());
        return;
    }
    if (0..2).any(|i| groups[i].is_empty()) {
        g.reasons.push("one angular reference is not a face".into());
        return;
    }
    let mut planes = Vec::new();
    let mut stations = Vec::new();
    for group in groups {
        let f = group[0];
        let Surface::Plane { frame } = &part.faces[f].surface else {
            g.reasons
                .push("one angular reference face is not planar".into());
            return;
        };
        let n = norm(frame.z);
        if n.is_nan() || n <= 1e-12 {
            g.reasons.push(
                "one angular reference plane could not be measured (degenerate normal)".into(),
            );
            return;
        }
        planes.push((frame.origin, frame.z.map(|c| c / n)));
        let b = part.face_bounds(f);
        stations.push([0, 1, 2].map(|i| 0.5 * (b.min[i] + b.max[i])));
    }
    match from_planes([planes[0], planes[1]], [stations[0], stations[1]]) {
        Ok(a) => {
            match a.degrees() {
                Some(d) if r.value.is_none_or(|value| (d - value).abs() <= 0.01) => {}
                Some(d) => g.reasons.push(format!(
                    "angular reference angle {} deg differs from nominal {} deg",
                    sig6(d),
                    sig6(r.value.unwrap_or(f64::NAN))
                )),
                None => g.reasons.push(
                    "angular reference is invalid (angular reference rays must not be parallel or collinear)"
                        .into(),
                ),
            }
            g.axis = a.principal().map(|k| ["X", "Y", "Z"][k].to_string());
            g.angle = Some(a);
        }
        Err(why) => g.reasons.push(why.into()),
    }
}

/// *v* as Python's `{:.6g}`, enough for a reason's text.
fn sig6(v: f64) -> String {
    if !v.is_finite() {
        return format!("{v}");
    }
    let digits = 6 - 1 - v.abs().log10().floor().max(-300.0) as i32;
    let s = format!("{:.*}", digits.max(0) as usize, v);
    if s.contains('.') {
        s.trim_end_matches('0').trim_end_matches('.').to_string()
    } else {
        s
    }
}

/// draftwright's `_angular_reference_from_planes`: the common normal-section angle of two planes
/// (each a point and a unit normal), its half-rays selected by the stations.
pub(crate) fn from_planes(planes: [(P3, P3); 2], stations: [P3; 2]) -> Result<Angle, &'static str> {
    let target = [0, 1, 2].map(|i| 0.5 * (stations[0][i] + stations[1][i]));
    let [(pa, na), (pb, nb)] = planes;
    let coupling = dot(na, nb);
    let determinant = 1.0 - coupling * coupling;
    if determinant <= 1e-12 {
        return Err("angular reference planes are parallel or coincident");
    }
    let ra = dot(na, sub(pa, target));
    let rb = dot(nb, sub(pb, target));
    let wa = (ra - coupling * rb) / determinant;
    let wb = (rb - coupling * ra) / determinant;
    let vertex = [0, 1, 2].map(|i| target[i] + wa * na[i] + wb * nb[i]);
    let line = cross(na, nb);
    let length = norm(line);
    if length <= 1e-12 {
        return Err("angular reference planes are parallel or coincident");
    }
    let line = line.map(|c| c / length);
    let mut witnesses = [[0.0; 3]; 2];
    for (w, (station, normal)) in witnesses
        .iter_mut()
        .zip([(stations[0], na), (stations[1], nb)])
    {
        let support = cross(line, normal);
        let s = dot(sub(station, vertex), support);
        if s.abs() <= 1e-9 {
            return Err("one angular reference face does not select a support half-ray");
        }
        *w = [0, 1, 2].map(|i| vertex[i] + s * support[i]);
    }
    let a = Angle {
        vertex,
        first: witnesses[0],
        second: witnesses[1],
    };
    if a.rays().is_none() {
        return Err(
            "angular reference is invalid (angular reference rays must not be parallel or collinear)",
        );
    }
    Ok(a)
}

/// The pad either side of the label's width that the arrowheads keep clear of, page mm
/// (draftwright's `pad_around_text`, from when the arc was broken around the label).
const PAD: f64 = 2.0;
/// How far the arc's radius grows between the places tried, mm, and the most it tries.
const STEP: f64 = 1.0;
const TRIES: usize = 400;
/// The largest chord error of the arc's chords, mm (draftwright's `0.04` sampling constant gives
/// 0.005 mm).
const SAMPLING: f64 = 0.04;

/// One angle's ink on the page, its radius free: [`AngularGeometry`] in page mm.
#[derive(Clone)]
struct Ink {
    vertex: P2,
    start: f64,
    sweep: f64,
    middle: f64,
    lengths: [f64; 2],
    /// The label's direction, radians, and its half-extents along and across the bisector.
    text_angle: f64,
    radial_half: f64,
    tangent_half: f64,
    minimum: f64,
    /// Whether its radius is bounded by [`Ink::fits`] (a profile corner's) rather than by
    /// [`Ink::arrows_clear`].
    compact: bool,
}

impl Ink {
    /// The ink of *a* as *frame* sees it, for a label of ink *size* (width, height); none when
    /// its rays project to nothing or to a degenerate sector (the view does not look along its
    /// normal).
    fn new(frame: &Frame, a: &Angle, size: P2) -> Option<Ink> {
        Ink::on_page(
            frame.page(a.vertex),
            [frame.page(a.first), frame.page(a.second)],
            size,
        )
    }

    /// The ink of an angle whose vertex and witnesses are *vertex* and *witnesses* on the page.
    fn on_page(vertex: P2, witnesses: [P2; 2], size: P2) -> Option<Ink> {
        let mut rays = Vec::new();
        let mut lengths = [0.0; 2];
        for (i, q) in witnesses.into_iter().enumerate() {
            let d = [q[0] - vertex[0], q[1] - vertex[1]];
            let n = d[0].hypot(d[1]);
            if n.is_nan() || n <= 0.0 {
                return None;
            }
            lengths[i] = n;
            rays.push([d[0] / n, d[1] / n]);
        }
        let (mut r0, r1) = (rays[0], rays[1]);
        let signed = (r0[0] * r1[1] - r0[1] * r1[0]).atan2(r0[0] * r1[0] + r0[1] * r1[1]);
        let sweep = signed.abs();
        if !(1e-9 < sweep && sweep < std::f64::consts::PI - 1e-9) {
            return None;
        }
        if signed < 0.0 {
            r0 = r1;
            lengths.swap(0, 1);
        }
        let start = r0[1].atan2(r0[0]);
        let middle = start + 0.5 * sweep;
        let [width, height] = size;
        // The label always lies along the tangent: its width along the arc, its height across.
        let ink = Ink {
            vertex,
            start,
            sweep,
            middle,
            lengths,
            text_angle: 0.0,
            radial_half: height / 2.0,
            tangent_half: width / 2.0,
            minimum: 0.0,
            compact: false,
        };
        Some(ink.along(0.5).least())
    }

    /// The same ink with its least radius found, by bisection, for its rule ([`Ink::room`]).
    fn least(mut self) -> Ink {
        let mut lo = self.radial_half + PAD;
        let mut hi = (2.0 * (self.tangent_half + self.radial_half + PAD) + 4.0 * ARROW)
            / self.sweep
            + self.radial_half
            + PAD;
        // A sector that never leaves room (none of a profile corner's is a half turn or more)
        // keeps a radius too far out to place.
        for _ in 0..32 {
            if self.room(hi) {
                break;
            }
            hi *= 2.0;
        }
        for _ in 0..40 {
            let radius = 0.5 * (lo + hi);
            if self.room(radius) {
                hi = radius;
            } else {
                lo = radius;
            }
        }
        self.minimum = hi + 1e-6;
        self
    }

    /// The same ink bounded by [`Ink::fits`]: its arc as near the vertex as its arrowheads and
    /// label allow (a profile corner's, #227).
    fn compact(mut self) -> Ink {
        self.compact = true;
        self.least()
    }

    /// Whether a label centred at *radius* leaves its arc room, by this ink's rule.
    fn room(&self, radius: f64) -> bool {
        if self.compact {
            self.fits(radius)
        } else {
            self.arrows_clear(radius)
        }
    }

    /// Whether a label centred at *radius* on its station leaves its arc ([`LIFT`] inside the
    /// label) room for an arrowhead at each end with a [`GAP`] between them, and lies within the
    /// sector, [`GAP`] clear of both extension lines, which overshoot the arc.
    fn fits(&self, radius: f64) -> bool {
        let arc = radius - LIFT - self.radial_half;
        if arc < ARROW || self.sweep * arc < 2.0 * ARROW + GAP {
            return false;
        }
        let (s, c) = self.middle.sin_cos();
        let [(s0, c0), (s1, c1)] = [self.start, self.start + self.sweep].map(f64::sin_cos);
        [-1.0, 1.0].into_iter().all(|u| {
            [-1.0, 1.0].into_iter().all(|v| {
                let along = radius + u * self.radial_half;
                let across = v * self.tangent_half;
                let q = [along * c - across * s, along * s + across * c];
                // Each corner's distance inside each side.
                c0 * q[1] - s0 * q[0] >= GAP && s1 * q[0] - c1 * q[1] >= GAP
            })
        })
    }

    /// Whether the arc at *radius* leaves room for an arrowhead either side of the label's
    /// width, padded, about its station: the label's angular extent as if it sat on the arc.
    fn arrows_clear(&self, radius: f64) -> bool {
        let gap = (self.tangent_half + PAD).atan2(radius - self.radial_half - PAD);
        let near = (self.middle - self.start).min(self.start + self.sweep - self.middle);
        (near - gap) * radius > ARROW
    }

    /// How far the label's farthest corner lies from the vertex with its centre at *radius*.
    fn label_reach(&self, radius: f64) -> f64 {
        (radius + self.radial_half).hypot(self.tangent_half)
    }

    /// The same ink with its label at *fraction* of the sweep from its start (0.5 is the
    /// bisector), turned to read from below or the right.
    fn along(&self, fraction: f64) -> Ink {
        let mut g = self.clone();
        g.middle = g.start + g.sweep * fraction;
        let tangent = (g.middle + std::f64::consts::FRAC_PI_2).rem_euclid(std::f64::consts::TAU);
        g.text_angle = if std::f64::consts::FRAC_PI_2 < tangent
            && tangent <= 3.0 * std::f64::consts::FRAC_PI_2
        {
            tangent - std::f64::consts::PI
        } else {
            tangent
        };
        g
    }

    fn point(&self, radius: f64, angle: f64) -> P2 {
        [
            self.vertex[0] + radius * angle.cos(),
            self.vertex[1] + radius * angle.sin(),
        ]
    }

    /// The ink for a label of *ink* centred at *radius*: the label on its station, the unbroken
    /// arc's chords [`LIFT`] inside it, an arrowhead at each end, and extension lines; none when
    /// the arc leaves no room by its rule ([`Ink::room`]).
    fn placed(&self, radius: f64, ink: &LabelInk) -> Option<Placed> {
        self.placed_at(radius, ink, false)
    }

    /// As [`Ink::placed`]; *literal* (an authored placement) keeps a radius without that room.
    fn placed_at(&self, radius: f64, ink: &LabelInk, literal: bool) -> Option<Placed> {
        if !literal && !self.room(radius) {
            return None;
        }
        // The label's centre stays where the inline label stood; the arc runs LIFT inside it.
        // An authored radius too small for an arc there moves the label out, not into the arc.
        let label = radius.max(ARROW + LIFT + self.radial_half);
        let radius = label - LIFT - self.radial_half;
        let end = self.start + self.sweep;
        let mut lines = Vec::new();
        for (length, angle) in self.lengths.iter().zip([self.start, end]) {
            lines.push([
                self.point(length.min(radius - GAP - OVERSHOOT) + GAP, angle),
                self.point(radius + OVERSHOOT, angle),
            ]);
        }
        let count = (self.sweep * (radius / SAMPLING).sqrt()).ceil().max(1.0) as usize;
        let at = |i: usize| self.point(radius, self.start + self.sweep * i as f64 / count as f64);
        lines.extend((0..count).map(|i| [at(i), at(i + 1)]));
        let turn = ARROW / radius;
        let arrows = vec![
            [
                self.point(radius, self.start),
                self.point(radius, self.start + turn),
            ],
            [self.point(radius, end), self.point(radius, end - turn)],
        ];
        let rotation = self.text_angle.to_degrees();
        let centre = self.point(label, self.middle);
        let mid = [
            0.5 * (ink.min[0] + ink.max[0]),
            0.5 * (ink.min[1] + ink.max[1]),
        ];
        let (s, c) = self.text_angle.sin_cos();
        let label_at = [
            centre[0] - (c * mid[0] - s * mid[1]),
            centre[1] - (s * mid[0] + c * mid[1]),
        ];
        Some(Placed {
            lines,
            arrows,
            label_at,
            rotation,
            label_box: label_box(ink, label_at, rotation),
        })
    }
}

pub(super) fn intent(
    frame: &Frame,
    angle: &Angle,
    terms: &[String],
    p: &crate::placement_intent::Place,
) -> Option<Placed> {
    let ink = label_ink(terms, font());
    let mut geometry = Ink::new(
        frame,
        angle,
        [ink.max[0] - ink.min[0], ink.max[1] - ink.min[1]],
    )?;
    if let Some(a) = p.along {
        geometry = geometry.along(a.fraction());
    }
    geometry.placed_at(
        geometry.minimum + p.lane.unwrap_or(0) as f64 * STEP,
        &ink,
        true,
    )
}

/// Reserve only the bounded notch grammar's minimum exterior arc. General profile
/// corners remain disabled; ordinary linear lane estimates cannot size acute arcs.
pub(crate) fn notch_reservations(
    a: &crate::model::Analysis,
    spec: &crate::model::Spec,
    scale: f64,
) -> Vec<(String, crate::layout::Rect)> {
    let mut out = Vec::new();
    for n in crate::notches::requirements(a) {
        let id = super::notches::id(&n.id, "notch.angle");
        if spec.dimensions.get(&id).is_some_and(|d| d.omit) {
            continue;
        }
        let (view, direction, up) = match n.axis {
            0 => ("side", [1., 0., 0.], [0., 0., 1.]),
            1 => ("front", [0., -1., 0.], [0., 0., 1.]),
            _ => ("plan", [0., 0., 1.], [0., 1., 0.]),
        };
        let frame = Frame::looking(a, direction, up, [0.; 2], scale);
        let terms = vec![format!("{}°", super::fmt(n.angle.degrees()))];
        let ink = label_ink(&terms, font());
        let size = [ink.max[0] - ink.min[0], ink.max[1] - ink.min[1]];
        // Invariant: The notch reader proves nonzero rays and angle in this principal plane.
        let physical = Ink::new(&frame, &(&n.angle).into(), size).unwrap();
        let mut extended = Ink::new(
            &frame,
            &Angle {
                vertex: n.angle.vertex,
                first: [0, 1, 2].map(|k| 2. * n.angle.vertex[k] - n.angle.first[k]),
                second: [0, 1, 2].map(|k| 2. * n.angle.vertex[k] - n.angle.second[k]),
            },
            size,
        )
        // Invariant: Reversing both proven notch rays preserves their nonzero angle.
        .unwrap();
        extended.lengths = [0.; 2];
        let ink_ref = &ink;
        if let Some(p) = (0..TRIES)
            .flat_map(|i| {
                [&physical, &extended]
                    .into_iter()
                    .filter_map(move |g| g.placed(g.minimum + i as f64 * STEP, ink_ref))
            })
            .find(|p| !boxes_overlap(p.label_box, frame.outline()))
        {
            let mut bounds = [
                f64::INFINITY,
                f64::INFINITY,
                f64::NEG_INFINITY,
                f64::NEG_INFINITY,
            ];
            for q in ink_points(&p) {
                for k in 0..2 {
                    bounds[k] = bounds[k].min(q[k] - 1.);
                    bounds[k + 2] = bounds[k + 2].max(q[k] + 1.);
                }
            }
            out.push((view.into(), bounds));
        }
    }
    out
}

/// Places each angular item of *items* in its carrying view (*views_of*) about its vertex (see
/// the module's notes), clear of what *placed* already holds where it can be; *placed* takes
/// its ink.
#[allow(clippy::too_many_arguments)]
pub(super) fn place(
    frames: &std::collections::BTreeMap<&str, Frame>,
    items: &[Want],
    views_of: &[Option<&'static str>],
    sides: &[Option<super::placement::Side>],
    fixed: &[bool],
    obstacles: &[super::PlannedDimension],
    placed: &mut [Option<Placed>],
    page: &Page,
    notch_void: &dyn Fn(&str, &Placed) -> bool,
) {
    for i in 0..items.len() {
        if placed[i].is_some() {
            continue;
        }
        let (Shape::Angular { angle, .. }, Some(view)) = (&items[i].shape, views_of[i]) else {
            continue;
        };
        // Profile-corner angles come last, around everything else ([`place_profile`]).
        if super::profile_angles::is_profile_angle(&items[i]) {
            continue;
        }
        let terms = &items[i].terms;
        let ink = label_ink(terms, font());
        let size = [ink.max[0] - ink.min[0], ink.max[1] - ink.min[1]];
        let Some(geometry) = Ink::new(&frames[view], angle, size) else {
            continue;
        };
        // Extending both supports reverses both rays and preserves the measured
        // included angle (also for obtuse angles). Search radius first, so a compact
        // exterior sector wins over an enormous arc around the original witnesses.
        let mut extended = Ink::new(
            &frames[view],
            &Angle {
                vertex: angle.vertex,
                first: [0, 1, 2].map(|k| 2.0 * angle.vertex[k] - angle.first[k]),
                second: [0, 1, 2].map(|k| 2.0 * angle.vertex[k] - angle.second[k]),
            },
            size,
        )
        // Invariant: The original rays passed the same Ink::new test before reversal.
        .expect("reversing both rays preserves the angle");
        extended.lengths = [0.0; 2];
        let ink = &ink;
        let mut candidates = (0..TRIES).flat_map(|n| {
            [&geometry, &extended]
                .into_iter()
                .filter_map(move |g| g.placed(g.minimum + n as f64 * STEP, ink))
        });
        let others: Vec<&Placed> = placed
            .iter()
            .enumerate()
            .filter(|(j, _)| *j != i)
            .filter_map(|(_, q)| q.as_ref())
            .chain(obstacles.iter().filter_map(|d| d.placed.as_ref()))
            .collect();
        let own = format!("v:{view}");
        let exterior = items[i]
            .measurements
            .iter()
            .any(|m| m.feature.starts_with("geometry:"));
        let in_void = |c: &Placed| {
            items[i]
                .measurements
                .iter()
                .any(|m| m.parameter == "notch.angle")
                && notch_void(view, c)
        };
        let furniture = |c: &Placed| {
            !boxes_overlap(c.label_box, page.title_block())
                && page
                    .views()
                    .iter()
                    .all(|(id, b)| (*id == own && in_void(c)) || !boxes_overlap(c.label_box, *b))
                && !c.lines.iter().any(|l| crosses(*l, page.title_block()))
                && (!exterior
                    || c.lines
                        .iter()
                        .skip(2)
                        .all(|l| !crosses(*l, frames[view].outline())))
        };
        let on_sheet = |c: &Placed| {
            let d = page.drawable();
            ink_points(c).all(|q| q[0] >= d[0] && q[0] <= d[2] && q[1] >= d[1] && q[1] <= d[3])
        };
        let clear = |c: &Placed| {
            page.views()
                .iter()
                .filter(|(id, _)| *id != own)
                .all(|(_, b)| !c.lines.iter().any(|l| crosses(*l, *b)))
                && others.iter().all(|q| {
                    !boxes_overlap(c.label_box, q.label_box)
                        && !q.lines.iter().any(|l| crosses(*l, c.label_box))
                        && !c.lines.iter().any(|l| crosses(*l, q.label_box))
                        && super::placement::dimension_lines_clear(c, q)
                })
        };
        let mut first = None;
        let mut fallback = None;
        let mut moves = Vec::new();
        let chosen = candidates.find(|c| {
            first.get_or_insert_with(|| c.clone());
            if !furniture(c) {
                return false;
            }
            fallback.get_or_insert_with(|| c.clone());
            if !on_sheet(c) || !(page.fits(c, view) || (in_void(c) && page.fits_interior(c, view)))
            {
                return false;
            }
            if clear(c) {
                return true;
            }
            // A newly supplied notch must fit around established dimensions. It cannot
            // evict a previously readable datum or short leader to acquire its arc.
            if !items[i]
                .measurements
                .iter()
                .any(|m| m.parameter == "notch.angle")
                && let Some(room) = super::placement::make_room(
                    i, c, frames, items, views_of, sides, fixed, placed, obstacles, page,
                )
            {
                moves = room;
                return true;
            }
            false
        });
        for (j, p) in moves {
            placed[j] = Some(p);
        }
        placed[i] = chosen.or(fallback).or(first);
    }
}

/// Withdraws each placed profile-corner angle whose witness lines a callout routed after it
/// now meets (see [`place_profile`]); the caller places them again, last.
pub(super) fn withdraw_profile_contacts(items: &[Want], placed: &mut [Option<Placed>]) {
    let callouts: Vec<Placed> = placed
        .iter()
        .zip(items)
        .filter(|(_, w)| matches!(w.shape, Shape::Callout { .. }))
        .filter_map(|(q, _)| q.clone())
        .collect();
    for (q, w) in placed.iter_mut().zip(items) {
        if super::profile_angles::is_profile_angle(w)
            && q.as_ref().is_some_and(|c| {
                callouts
                    .iter()
                    .any(|l| super::leaders::meets_extensions(&c.lines[..2], &l.lines, l.label_box))
            })
        {
            *q = None;
        }
    }
}

/// How far a profile angle's arc and label may reach from its corner, in label heights: a
/// reader must see which corner it dimensions (about 26 mm for a 2.2 mm label).
const LOCAL_REACH: f64 = 12.0;

/// The largest radius a profile angle's arc may take for a label *height* high:
/// [`LOCAL_REACH`] label heights. A short edge does not bound it: its witness line extends the
/// edge outward past the part, as a short chamfer's angle is drawn.
fn local_radius(height: f64) -> f64 {
    LOCAL_REACH * height
}

/// How far beyond another item's ink a candidate's ink may still meet it, page mm: past the
/// distance at which parallel lines read as one ([`super::leaders::lines_meet`], 1 mm).
const NEAR: f64 = 1.5;

/// The bounds of *p*'s ink ([`ink_points`]), grown by *margin*; an empty label box adds none.
fn reach(p: &Placed, margin: f64) -> [f64; 4] {
    let b = p.label_box;
    let label = if b[2] > b[0] && b[3] > b[1] { 4 } else { 0 };
    // `ink_points` lists the label box's four corners last.
    let [x0, y0, x1, y1] = ink_points(p)
        .take(2 * p.lines.len() + 3 * p.arrows.len() + label)
        .fold(
            [
                f64::INFINITY,
                f64::INFINITY,
                f64::NEG_INFINITY,
                f64::NEG_INFINITY,
            ],
            |r, q| {
                [
                    r[0].min(q[0]),
                    r[1].min(q[1]),
                    r[2].max(q[0]),
                    r[3].max(q[1]),
                ]
            },
        );
    [x0 - margin, y0 - margin, x1 + margin, y1 + margin]
}

/// Places each profile-corner angle of *items* (see [`super::profile_angles`]) one at a time in
/// its true-shape view, at its corner or (a counted angle) at any one of its equal corners:
/// inside the corner's material (an arc whose sides are the corner's own edges, its label on
/// proven blank face) or in the equal sector between the extended edges, outside the material.
/// The arc starts as near the corner as its arrowheads and label allow ([`Ink::compact`]),
/// stays local to it ([`local_radius`]) and wholly within the view's reserved area; it crosses
/// no projected edge of the part, no other item's lines, and touches no label, preferring a
/// label clear of other ink by reading distance; its arrowheads lie across no other item's
/// line, its ink keeps out of the span an off-span linear label settles back into, and no
/// callout's leader or padded label meets its witness lines. With no such place the angle stays
/// unplaced (dropped): it never takes a fallback, moves another item or asks for more paper.
/// One already placed by its authored placement intent is kept.
#[allow(clippy::too_many_arguments)]
pub(super) fn place_profile(
    frames: &std::collections::BTreeMap<&str, Frame>,
    items: &[Want],
    views_of: &[Option<&'static str>],
    obstacles: &[super::PlannedDimension],
    placed: &mut [Option<Placed>],
    page: &Page,
    views: &[crate::model::PlannedView],
    proofs: &std::collections::BTreeMap<String, std::rc::Rc<super::interior::Proof>>,
) {
    for i in 0..items.len() {
        // An authored placement (`dimensions/<id>/place`) has already been honoured.
        if !super::profile_angles::is_profile_angle(&items[i]) || placed[i].is_some() {
            continue;
        }
        let (Shape::Angular { angle, alternates }, Some(view)) = (&items[i].shape, views_of[i])
        else {
            continue;
        };
        let own = format!("v:{view}");
        let (Some(frame), Some(v)) = (frames.get(view), views.iter().find(|v| v.id == own)) else {
            continue;
        };
        let Some(proof) = proofs.get(&own) else {
            continue;
        };
        let terms = &items[i].terms;
        let ink = label_ink(terms, font());
        let size = [ink.max[0] - ink.min[0], ink.max[1] - ink.min[1]];
        // Every other item, and which of its lines the angle's lines may cross transversely:
        // a linear dimension's extension lines, and a centre line or cutting-plane chain line
        // (a corner on a section line cannot avoid it). Never a dimension line, leader, arrow,
        // letter, symbol or label.
        let others: Vec<(&Placed, usize)> = placed
            .iter()
            .enumerate()
            .filter(|(j, _)| *j != i)
            .filter_map(|(j, q)| {
                let witnesses = if matches!(items[j].shape, Shape::Linear { .. }) {
                    2
                } else {
                    0
                };
                q.as_ref().map(|q| (q, witnesses))
            })
            .chain(obstacles.iter().filter_map(|d| {
                let crossable = if d.id.starts_with("ink:")
                    && !d.id.contains(":arrow:")
                    && !d.id.contains(":letter:")
                {
                    usize::MAX
                } else if d.kind == "linear" {
                    2
                } else {
                    0
                };
                d.placed.as_ref().map(|q| (q, crossable))
            }))
            .collect();
        // Each one's ink bounds, to pass over those far from a candidate.
        let reaches: Vec<[f64; 4]> = others.iter().map(|&(q, _)| reach(q, NEAR)).collect();
        // A placed callout whose leader or padded label meets the angle's witness lines reads
        // as touching an extension line (the maintainer's 2026-10-12 contact rule).
        let callouts: Vec<&Placed> = placed
            .iter()
            .zip(items)
            .enumerate()
            .filter(|(j, (_, w))| *j != i && matches!(w.shape, Shape::Callout { .. }))
            .filter_map(|(_, (q, _))| q.as_ref())
            .chain(
                obstacles
                    .iter()
                    .filter(|d| d.kind == "callout")
                    .filter_map(|d| d.placed.as_ref()),
            )
            .collect();
        // A linear label still beside its span is brought back between its arrows by label
        // settlement ([`super::label_lanes`]); the strip it returns to stays free of the angle.
        let homes: Vec<[f64; 4]> = others
            .iter()
            .filter(|&&(q, witnesses)| witnesses == 2 && q.arrows.len() == 2)
            .filter_map(|&(q, _)| {
                let k = usize::from(q.rotation != 0.0);
                let (a, b) = (q.arrows[0][0][k], q.arrows[1][0][k]);
                let (lo, hi) = (a.min(b), a.max(b));
                let mut strip = q.label_box;
                (strip[k] < lo - 1e-6 || strip[k + 2] > hi + 1e-6).then(|| {
                    (strip[k], strip[k + 2]) = (lo, hi);
                    strip
                })
            })
            .collect();
        let area = page.area(view);
        let clear = |c: &Placed, stubs: usize, straight_ok: bool, vertex: P2| {
            let ink = reach(c, 0.0);
            // The label within the same reach of its corner as the arc.
            let b = c.label_box;
            let far = [b[0], b[2]]
                .into_iter()
                .flat_map(|x| [b[1], b[3]].map(|y| (x - vertex[0]).hypot(y - vertex[1])))
                .fold(0.0, f64::max);
            far <= local_radius(size[1]) &&
            // Within the view's reserved area: never paper the layout did not give the view.
            area.is_none_or(|b| {
                ink_points(c).all(|q| q[0] >= b[0] && q[0] <= b[2] && q[1] >= b[1] && q[1] <= b[3])
            }) && page.fits_interior(c, view)
                && callouts
                    .iter()
                    .all(|q| !super::leaders::meets_extensions(&c.lines[..2], &q.lines, q.label_box))
                && others.iter().zip(&reaches).all(|(&(q, crossable), &r)| {
                    // Ink farther apart than [`NEAR`] cannot meet.
                    !boxes_overlap(ink, r)
                        || (super::placement::annotations_clear(c, q)
                            && super::placement::lines_clear_but(c, q, crossable)
                            // An arrowhead never lies across another item's line, even one the
                            // arc may cross.
                            && c.arrows.iter().flat_map(|h| super::leaders::head(*h)).all(|s| {
                                q.lines.iter().all(|t| !super::leaders::lines_meet(s, *t))
                            }))
                })
                && homes
                    .iter()
                    .all(|&h| !boxes_overlap(c.label_box, h) && !ink_through(c, h))
                // The label on proven blank paper: a blank face inside the corner, or exterior
                // paper clear of every projected edge.
                && (proof.blank(v, c.label_box) || proof.exterior_clear(v, c.label_box, &[], &[]))
                // The arc and its arrows cross no edge of the part; inside the corner its
                // witness stubs lie on the corner's own edges.
                && proof.lines_clear(v, &c.lines[stubs..], &c.arrows, 2 - stubs, straight_ok)
        };
        // A clear place whose label is also clear of other ink by reading distance
        // (`label_crowded`).
        let roomy = |c: &Placed| {
            let grown = clear_box(c.label_box);
            others.iter().all(|&(q, _)| {
                !boxes_overlap(grown, q.label_box)
                    && !ink_through(q, grown)
                    && !ink_through(c, clear_box(q.label_box))
            })
        };
        let chosen = std::iter::once(angle).chain(alternates).find_map(|angle| {
            let physical = Ink::new(frame, angle, size)?.compact();
            let mut extended = Ink::new(
                frame,
                &Angle {
                    vertex: angle.vertex,
                    first: [0, 1, 2].map(|k| 2.0 * angle.vertex[k] - angle.first[k]),
                    second: [0, 1, 2].map(|k| 2.0 * angle.vertex[k] - angle.second[k]),
                },
                size,
            )?
            .compact();
            extended.lengths = [0.0; 2];
            let reach = local_radius(size[1]);
            // The label may slide off the bisector; its arc pieces must still carry arrows.
            let inks: Vec<(Ink, usize)> = [0.5, 0.35, 0.65, 0.25, 0.75]
                .iter()
                .flat_map(|&f| [(physical.along(f), 2), (extended.along(f), 0)])
                .collect();
            // First an arc that crosses nothing; then one whose arc and witnesses may cross the
            // part's edges, blend silhouettes included, but never a hole or boss seen end-on.
            // Each the nearest roomy place, else the nearest crowded one (still stated).
            [false, true].into_iter().find_map(|straight_ok| {
                let mut crowded = None;
                let roomy = (0..TRIES)
                    .map(|n| n as f64 * STEP)
                    .take_while(|d| {
                        inks.iter()
                            .any(|(g, _)| g.label_reach(g.minimum + d) <= reach)
                    })
                    .find_map(|d| {
                        inks.iter().find_map(|(g, stubs)| {
                            let radius = g.minimum + d;
                            (g.label_reach(radius) <= reach)
                                .then(|| g.placed(radius, &ink))
                                .flatten()
                                .filter(|c| {
                                    // Once a crowded place is held, only a roomy one is worth
                                    // the full check.
                                    if crowded.is_some() {
                                        return roomy(c) && clear(c, *stubs, straight_ok, g.vertex);
                                    }
                                    clear(c, *stubs, straight_ok, g.vertex)
                                        && (roomy(c) || {
                                            crowded = Some(c.clone());
                                            false
                                        })
                                })
                        })
                    });
                roomy.or(crowded)
            })
        });
        placed[i] = chosen;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn two_planes_give_their_normal_section_angle() {
        // ctc_01's 60° V: planes through (110, -75) at ±30° from -y, normals in the xy plane.
        let (s, c) = 30f64.to_radians().sin_cos();
        let a = from_planes(
            [
                ([110.0, -75.0, 0.0], [c, s, 0.0]),
                ([110.0, -75.0, 0.0], [-c, s, 0.0]),
            ],
            [[128.76, -107.5, -25.0], [91.24, -107.5, -25.0]],
        )
        .unwrap();
        assert!((a.vertex[0] - 110.0).abs() < 1e-9 && (a.vertex[1] + 75.0).abs() < 1e-9);
        assert!((a.vertex[2] + 25.0).abs() < 1e-9, "{:?}", a.vertex);
        assert!((a.degrees().unwrap() - 60.0).abs() < 1e-9);
        assert_eq!(a.principal(), Some(2));
        assert!(a.first[0] > 110.0 && a.second[0] < 110.0);
    }

    /// Two planes whose line of intersection is oblique to x, y and z: an angle no view shows true,
    /// so `principal` (and with it the record's `axis`) is none and the plan refuses it.
    #[test]
    fn an_oblique_pair_of_planes_has_no_principal_view() {
        let h = 0.5f64.sqrt();
        let a = from_planes(
            [([0.0; 3], [1.0, 0.0, 0.0]), ([0.0; 3], [0.0, h, h])],
            [[0.0, 5.0, 5.0], [5.0, 0.0, 0.0]],
        )
        .unwrap();
        assert!((a.degrees().unwrap() - 90.0).abs() < 1e-9, "{a:?}");
        assert_eq!(a.principal(), None);
    }

    #[test]
    fn parallel_planes_have_no_angle() {
        let z = [0.0, 0.0, 1.0];
        assert_eq!(
            from_planes([([0.0; 3], z), ([0.0, 0.0, 5.0], z)], [[1.0; 3], [2.0; 3]]).unwrap_err(),
            "angular reference planes are parallel or coincident"
        );
    }

    /// The page ink of an angle at *vertex* between rays at *a* and *b* degrees, witnesses 10 mm
    /// out, its label *terms*.
    fn ink(vertex: P2, a: f64, b: f64, terms: &[String]) -> Ink {
        let at = |d: f64| {
            let (s, c) = d.to_radians().sin_cos();
            [vertex[0] + 10.0 * c, vertex[1] + 10.0 * s]
        };
        let l = label_ink(terms, font());
        Ink::on_page(
            vertex,
            [at(a), at(b)],
            [l.max[0] - l.min[0], l.max[1] - l.min[1]],
        )
        .unwrap()
    }

    /// ISO 129-1 (#227): one unbroken arc about the vertex from one extension line to the other,
    /// an arrowhead at each end on it, and the label outside the arc, clear of it, centred on the
    /// bisector and within the sector, for a wide counted label (the hex's `6× 120°`), a narrow
    /// acute one (the V-slot's `45°`) and labels that read from below.
    #[test]
    fn an_angle_is_one_unbroken_arc_with_its_label_outside() {
        let cases = [
            ("6× 120°", 210.0, 330.0),
            ("45°", 180.0, 225.0),
            ("45°", 0.0, 45.0),
            ("135°", 100.0, 235.0),
            ("60°", 60.0, 120.0),
        ];
        for (label, a, b) in cases {
            let terms = vec![label.to_string()];
            let vertex = [100.0, 100.0];
            let g = ink(vertex, a, b, &terms);
            let text = &label_ink(&terms, font());
            let p = g.placed(g.minimum, text).unwrap();
            let from = |q: P2| (q[0] - vertex[0]).hypot(q[1] - vertex[1]);
            let bearing = |q: P2| (q[1] - vertex[1]).atan2(q[0] - vertex[0]).to_degrees();
            // How far *q* lies inside the sector from its nearer side, mm (negative outside).
            let inside = |q: P2| {
                let side = |d: f64, sign: f64| {
                    let (s, c) = d.to_radians().sin_cos();
                    sign * (c * (q[1] - vertex[1]) - s * (q[0] - vertex[0]))
                };
                side(a, 1.0).min(side(b, -1.0))
            };
            let arc = &p.lines[2..];
            let radius = from(arc[0][0]);
            for pair in arc.windows(2) {
                let [x, y] = [pair[0][1], pair[1][0]];
                assert!((x[0] - y[0]).hypot(x[1] - y[1]) < 1e-9, "{label}: unbroken");
            }
            for q in arc.iter().flat_map(|l| [l[0], l[1]]) {
                assert!((from(q) - radius).abs() < 1e-9, "{label}: on the arc");
            }
            for (q, d) in [(arc[0][0], a), (arc[arc.len() - 1][1], b)] {
                assert!(
                    (bearing(q) - d)
                        .rem_euclid(360.0)
                        .min((d - bearing(q)).rem_euclid(360.0))
                        < 1e-6,
                    "{label}: the arc runs from one extension line to the other"
                );
            }
            for [tip, _] in &p.arrows {
                assert!(
                    (from(*tip) - radius).abs() < 1e-9,
                    "{label}: arrow on the arc"
                );
                assert!(inside(*tip).abs() < 1e-9, "{label}: arrow tip on a side");
            }
            // The label's own (turned) rectangle: outside the arc by LIFT, within the sector.
            let bx = p.label_box;
            let centre = [0.5 * (bx[0] + bx[2]), 0.5 * (bx[1] + bx[3])];
            assert!(
                (bearing(centre) - 0.5 * (a + b)).rem_euclid(360.0) < 1e-6
                    || (0.5 * (a + b) - bearing(centre)).rem_euclid(360.0) < 1e-6,
                "{label}: on the bisector"
            );
            let (s, c) = g.text_angle.sin_cos();
            let half = [g.tangent_half, g.radial_half];
            for (u, v) in [(-1.0, -1.0), (-1.0, 1.0), (1.0, -1.0), (1.0, 1.0)] {
                let q = [
                    centre[0] + u * half[0] * c - v * half[1] * s,
                    centre[1] + u * half[0] * s + v * half[1] * c,
                ];
                assert!(from(q) >= radius + LIFT - 1e-6, "{label}: outside the arc");
                assert!(inside(q) > 0.0, "{label}: within the sector");
            }
            // Below the least radius the arrowheads would reach the label's width.
            assert!(g.placed(g.minimum - 0.5, text).is_none(), "{label}");
        }
    }

    /// #227: a profile corner's compact arc needs room only for its two arrowheads and a label
    /// within the sector, not for the label's width between the arrowheads, so the V-slot's
    /// acute `45°` comes far nearer its vertex; at its least radius the arc still carries both
    /// arrowheads and the label stays outside it, [`GAP`] inside both extension lines.
    #[test]
    fn a_compact_arc_keeps_arrowheads_and_label_in_its_sector() {
        let cases = [
            ("45°", 180.0, 225.0, 0.6),
            ("6× 120°", 210.0, 330.0, 1.0),
            ("135°", 100.0, 235.0, 1.0),
            ("21.227°", 10.0, 31.227, 1.0),
        ];
        for (label, a, b, most) in cases {
            let terms = vec![label.to_string()];
            let vertex = [100.0, 100.0];
            let wide = ink(vertex, a, b, &terms);
            let g = wide.clone().compact();
            assert!(
                g.minimum <= most * wide.minimum + 1e-9,
                "{label}: {} against {}",
                g.minimum,
                wide.minimum
            );
            let text = &label_ink(&terms, font());
            for f in [0.5, 0.35, 0.65] {
                let g = g.along(f);
                let Some(p) = g.placed(g.minimum, text) else {
                    // Off the bisector a narrow sector may need more radius.
                    assert!(f != 0.5, "{label}: placeable at its least radius");
                    continue;
                };
                let from = |q: P2| (q[0] - vertex[0]).hypot(q[1] - vertex[1]);
                let radius = from(p.lines[2][0]);
                let sweep = (b - a).to_radians();
                assert!(
                    sweep * radius >= 2.0 * ARROW + GAP - 1e-6,
                    "{label}: two arrowheads"
                );
                let (s, c) = g.text_angle.sin_cos();
                let bx = p.label_box;
                let centre = [0.5 * (bx[0] + bx[2]), 0.5 * (bx[1] + bx[3])];
                for (u, v) in [(-1.0, -1.0), (-1.0, 1.0), (1.0, -1.0), (1.0, 1.0)] {
                    let q = [
                        centre[0] + u * g.tangent_half * c - v * g.radial_half * s,
                        centre[1] + u * g.tangent_half * s + v * g.radial_half * c,
                    ];
                    assert!(from(q) >= radius + LIFT - 1e-6, "{label}: outside the arc");
                    for (d, sign) in [(a, 1.0), (b, -1.0)] {
                        let (sd, cd) = f64::to_radians(d).sin_cos();
                        let inside = sign * (cd * (q[1] - vertex[1]) - sd * (q[0] - vertex[0]));
                        assert!(
                            inside >= GAP - 1e-6,
                            "{label}: clear of the extension lines"
                        );
                    }
                }
            }
            assert!(g.placed(g.minimum - 0.5, text).is_none(), "{label}");
        }
    }

    #[test]
    fn reasons_print_six_figures() {
        assert_eq!(sig6(59.123456789), "59.1235");
        assert_eq!(sig6(60.0), "60");
        assert_eq!(sig6(0.5), "0.5");
    }
}
