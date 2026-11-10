//! ISO 6410 depiction of recognised physical screw threads. The source Part and
//! recognition remain immutable; a drawing-only carrier replaces the claimed flanks.
use std::collections::BTreeSet;
use std::f64::consts::{FRAC_PI_2, TAU};

use haecceity::brep::{Edge, Face, Loop, Part, Solid};
use haecceity::classify::{Classifier, State};
use haecceity::geom::{self, Frame, Surface, V3};
use haecceity::hlr::View;
use serde::{Deserialize, Serialize};

use crate::bridge::Feature;
use crate::model::{Analysis, PlannedView, Spec};
use crate::output::{Curve, Item};

/// Author intent only; measured geometry always remains in the Analysis.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields, default)]
pub struct Settings {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub depiction: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub designation: Option<String>,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct Thread<'a> {
    pub feature: &'a Feature,
    pub on: V3,
    pub axis: V3,
    pub internal: bool,
    pub major: f64,
    pub minor: f64,
    pub pitch: f64,
    pub span: [f64; 2],
    pub left: bool,
    pub starts: u64,
}

impl<'a> Thread<'a> {
    pub(crate) fn read(f: &'a Feature) -> Result<Self, &'static str> {
        let p = &f.parameters;
        let number = |name: &str| {
            p.get(name)
                .and_then(|v| v.as_f64())
                .filter(|x| x.is_finite())
        };
        let point = |v: &serde_json::Value| -> Option<V3> {
            let a = v.as_array()?;
            (a.len() == 3).then_some(())?;
            Some([a[0].as_f64()?, a[1].as_f64()?, a[2].as_f64()?])
        };
        let on = p
            .get("axis")
            .and_then(|a| point(&a["point"]))
            .ok_or("missing axis point")?;
        let axis = p
            .get("axis")
            .and_then(|a| point(&a["direction"]))
            .and_then(geom::unit)
            .ok_or("missing axis direction")?;
        let major = number("major_diameter").ok_or("missing major diameter")?;
        let minor = number("minor_diameter").ok_or("missing minor diameter")?;
        let pitch = number("pitch").ok_or("missing pitch")?;
        let span = [
            number("start").ok_or("missing start")?,
            number("end").ok_or("missing end")?,
        ];
        if !(major > minor && minor > 0. && pitch > 0. && span[1] > span[0]) || f.faces.is_empty() {
            return Err("invalid diameters, pitch, extent or face claims");
        }
        let internal = match p.get("side").and_then(|v| v.as_str()) {
            Some("internal") => true,
            Some("external") => false,
            _ => return Err("invalid material side"),
        };
        let left = match p.get("hand").and_then(|v| v.as_str()) {
            Some("left") => true,
            Some("right") => false,
            _ => return Err("invalid hand"),
        };
        let starts = p
            .get("starts")
            .and_then(|v| v.as_u64())
            .filter(|s| *s > 0)
            .ok_or("invalid starts")?;
        Ok(Self {
            feature: f,
            on,
            axis,
            internal,
            major,
            minor,
            pitch,
            span,
            left,
            starts,
        })
    }
    pub(crate) fn at(&self, t: f64) -> V3 {
        geom::add(self.on, geom::scale(self.axis, t))
    }
    pub(crate) fn crest(&self) -> f64 {
        (if self.internal {
            self.minor
        } else {
            self.major
        }) / 2.
    }
    pub(crate) fn root(&self) -> f64 {
        (if self.internal {
            self.major
        } else {
            self.minor
        }) / 2.
    }
    pub(crate) fn enabled(&self, spec: &Spec) -> bool {
        spec.features
            .get(&self.feature.id)
            .and_then(|f| f.modelled_thread.as_ref())
            .and_then(|t| t.depiction)
            != Some(false)
    }
    pub(crate) fn designation(&self) -> (String, bool) {
        // External crests match nominal directly. An internal model can truncate its
        // roots: allow up to 0.26 P (capped at 0.4 mm) below nominal, never above it.
        // This is an inference policy, not an ISO manufacturing tolerance/class.
        let below = if self.internal {
            (0.26 * self.pitch).clamp(0.02, 0.4)
        } else {
            0.02
        };
        let candidates: Vec<_> = metric_pairs()
            .into_iter()
            .filter(|&(d, p)| {
                (p - self.pitch).abs() <= 0.01 + 1e-10
                    && d - self.major >= -0.02 - 1e-10
                    && d - self.major <= below + 1e-10
            })
            .collect();
        let standard = candidates.len() == 1;
        let mut text = if let [(d, p)] = candidates.as_slice() {
            if self.starts == 1 {
                format!("M{}×{}", crate::dims::fmt(*d), crate::dims::fmt(*p))
            } else {
                format!(
                    "M{}×Ph{}P{}",
                    crate::dims::fmt(*d),
                    crate::dims::fmt(*p * self.starts as f64),
                    crate::dims::fmt(*p)
                )
            }
        } else {
            format!(
                "⌀{} × P{}",
                crate::dims::fmt((self.major * 1000.).round() / 1000.),
                crate::dims::fmt((self.pitch * 1000.).round() / 1000.)
            )
        };
        if !standard && self.starts > 1 {
            text += &format!(" {} STARTS", self.starts);
        }
        if self.left {
            text += if standard { "-LH" } else { " LH" };
        }
        (text, standard)
    }
}

// ISO 261:1998 tables 1/2 (ISO 262 is the selected subset). Coarse entries
// shared with authored callouts; fine pairs are explicit, never a pitch formula.
fn metric_pairs() -> Vec<(f64, f64)> {
    let mut pairs = crate::spec_notes::COARSE.to_vec();
    let fine: &[(f64, &[f64])] = &[
        (1., &[0.2]),
        (1.1, &[0.2]),
        (1.2, &[0.2]),
        (1.4, &[0.2]),
        (1.6, &[0.2]),
        (1.8, &[0.2]),
        (2., &[0.25]),
        (2.2, &[0.25]),
        (2.5, &[0.35]),
        (3., &[0.35]),
        (3.5, &[0.35]),
        (4., &[0.5]),
        (4.5, &[0.5]),
        (5., &[0.5]),
        (5.5, &[0.5]),
        (6., &[0.75, 0.5]),
        (7., &[0.75]),
        (8., &[1., 0.75]),
        (9., &[1., 0.75]),
        (10., &[1.25, 1., 0.75]),
        (11., &[1., 0.75]),
        (12., &[1.5, 1.25, 1.]),
        (14., &[1.5, 1.]),
        (15., &[1.5, 1.]),
        (16., &[1.5, 1.]),
        (17., &[1.5, 1.]),
        (18., &[2., 1.5, 1.]),
        (20., &[2., 1.5, 1.]),
        (22., &[2., 1.5, 1.]),
        (24., &[2., 1.5, 1.]),
        (25., &[2., 1.5, 1.]),
        (26., &[1.5]),
        (27., &[2., 1.5, 1.]),
        (28., &[2., 1.5, 1.]),
        (30., &[3., 2., 1.5, 1.]),
        (32., &[2., 1.5]),
        (33., &[3., 2., 1.5]),
        (35., &[1.5]),
        (36., &[3., 2., 1.5]),
        (38., &[1.5]),
        (39., &[3., 2., 1.5]),
    ];
    for &(d, ps) in fine {
        pairs.extend(ps.iter().map(|p| (d, *p)));
    }
    for d in [
        40, 42, 45, 48, 50, 52, 55, 56, 58, 60, 62, 64, 65, 68, 70, 72, 75, 76, 78, 80, 82, 85, 90,
        95, 100, 105, 110, 115, 120, 125, 130, 135, 140, 145, 150, 155, 160, 165, 170, 175, 180,
        185, 190, 195, 200, 205, 210, 215, 220, 225, 230, 235, 240, 245, 250, 255, 260, 265, 270,
        275, 280, 285, 290, 295, 300,
    ] {
        for p in [1.5, 2., 3., 4., 6., 8.] {
            let listed = match p {
                1.5 => d <= 80,
                2. => d <= 245,
                3. => d <= 250 && d != 78 && d != 82,
                4. => ![40, 50, 78, 82].contains(&d),
                6. => d >= 68 && d != 75 && d != 78 && d != 82,
                8. => [
                    125, 130, 140, 150, 160, 170, 180, 190, 200, 210, 220, 230, 240, 250, 260, 270,
                    280, 290, 300,
                ]
                .contains(&d),
                _ => false,
            };
            if listed {
                pairs.push((d as f64, p));
            }
        }
        // ISO 261 clause 5.4 / table 1 permits finer pitches on listed diameters.
        for (p, max) in [(3., 300), (2., 200), (1.5, 150), (1., 80)] {
            if d <= max
                && pairs
                    .iter()
                    .filter(|(diameter, _)| *diameter == d as f64)
                    .all(|(_, pitch)| p < *pitch)
            {
                pairs.push((d as f64, p));
            }
        }
    }
    pairs.sort_by(|a, b| a.0.total_cmp(&b.0).then(a.1.total_cmp(&b.1)));
    pairs.dedup();
    pairs
}

pub(crate) fn threads(a: &Analysis) -> impl Iterator<Item = Thread<'_>> {
    a.features
        .iter()
        .filter(|f| f.kind == "modelled_thread")
        .filter_map(|f| Thread::read(f).ok())
}

/// A drawing carrier: all unclaimed faces and boundaries are retained. Each
/// claimed surface is replaced by a smooth cylinder at the ISO crest diameter.
/// Its span includes indivisible carrier faces beyond the physical thread extent.
/// The kernel still owns projection, visibility and section/hatch intersections.
pub(crate) fn carrier(part: &Part, a: &Analysis, spec: &Spec, enabled: bool) -> Option<Part> {
    let spring_faces = crate::springs::faces(a, spec);
    let ts: Vec<_> = threads(a)
        .filter(|t| {
            enabled && t.enabled(spec) && t.feature.faces.iter().all(|&i| i < part.faces.len())
        })
        .collect();
    if ts.is_empty() && spring_faces.is_empty() {
        return None;
    }
    let mut claimed: BTreeSet<_> = ts
        .iter()
        .flat_map(|t| t.feature.faces.iter().copied())
        .collect();
    claimed.extend(spring_faces);
    let mut faces = Vec::new();
    let mut mapping = vec![None; part.faces.len()];
    for (i, f) in part.faces.iter().enumerate() {
        if !claimed.contains(&i) {
            mapping[i] = Some(faces.len());
            faces.push(f.clone());
        }
    }
    let mut edges = part.edges.clone();
    let mut solids: Vec<_> = part
        .solids
        .iter()
        .map(|s| Solid {
            faces: s.faces.iter().filter_map(|i| mapping[*i]).collect(),
        })
        .collect();
    for t in ts {
        let r = t.crest();
        let seed = if t.axis[0].abs() < 0.9 {
            [1., 0., 0.]
        } else {
            [0., 1., 0.]
        };
        // Invariant: Thread::read normalizes the axis; the chosen seed is not parallel to it.
        let x = geom::unit(geom::cross(seed, t.axis)).unwrap();
        let frame = Frame {
            origin: t.on,
            x,
            y: geom::cross(t.axis, x),
            z: t.axis,
        };
        let (lo, hi) = part.extent_along(&t.feature.faces, t.axis);
        let shift = geom::dot(t.on, t.axis);
        let span = [(lo - shift).min(t.span[0]), (hi - shift).max(t.span[1])];
        // End-face circles are shared with the removed flanks/carrier. Keep
        // their trimming topology, but put the drawing boundary at the crest;
        // a thick full root/chamfer circle would contradict the open root arc.
        for (i, edge) in edges.iter_mut().enumerate().take(part.edges.len()) {
            let owners = &part.edge_faces()[i];
            if !owners.iter().any(|f| t.feature.faces.contains(f))
                || !owners.iter().any(|f| !claimed.contains(f))
            {
                continue;
            }
            // A chamfer or end face meets the flanks along a jagged curve. Its drawing
            // boundary is the arc where that face crosses the crest, as for a circle.
            if !matches!(edge.curve, haecceity::geom::Curve::Circle { .. }) {
                let level = owners
                    .iter()
                    .filter(|f| !claimed.contains(f))
                    .find_map(|&f| crest_level(&part.faces[f].surface, &t, r));
                if let Some(level) = level
                    && !edge.samples.is_empty()
                    && level >= t.span[0] - t.pitch
                    && level <= t.span[1] + t.pitch
                    && edge.samples.iter().all(|&p| {
                        let off = geom::norm(geom::cross(geom::sub(p, t.on), t.axis));
                        off >= t.minor / 2. - 0.01 && off <= t.major / 2. + 0.01
                    })
                {
                    let circle = Frame {
                        origin: t.at(level),
                        ..frame
                    };
                    let angle = |p: V3| {
                        let q = geom::sub(p, circle.origin);
                        geom::dot(q, circle.y).atan2(geom::dot(q, circle.x))
                    };
                    let first = angle(edge.samples[0]);
                    let mut a = first;
                    for &p in &edge.samples[1..] {
                        a = geom::nearest_turn(angle(p), a);
                    }
                    let c = haecceity::geom::Curve::Circle {
                        frame: circle,
                        radius: r,
                    };
                    let n = edge.samples.len().max(9);
                    edge.samples = (0..n)
                        .map(|k| c.value(first + (a - first) * k as f64 / (n - 1) as f64))
                        .collect();
                    edge.start = edge.samples[0];
                    edge.end = edge.samples[n - 1];
                    edge.same_sense = a >= first;
                    edge.curve = c;
                    for face in &mut faces {
                        face.pcurves.retain(|(e, _)| *e != i);
                    }
                }
                continue;
            }
            let haecceity::geom::Curve::Circle {
                frame: circle,
                radius,
            } = edge.curve.clone()
            else {
                continue;
            };
            let z = geom::dot(geom::sub(circle.origin, t.on), t.axis);
            let off = geom::norm(geom::cross(geom::sub(circle.origin, t.on), t.axis));
            if off > 1e-5
                || geom::dot(circle.z, t.axis).abs() < 1. - 1e-8
                || radius < t.minor / 2. - 0.01
                || radius > t.major / 2. + 0.01
                || z < t.span[0] - t.pitch
                || z > t.span[1] + t.pitch
            {
                continue;
            }
            let move_point = |p| {
                geom::add(
                    circle.origin,
                    geom::scale(geom::sub(p, circle.origin), r / radius),
                )
            };
            edge.start = move_point(edge.start);
            edge.end = move_point(edge.end);
            edge.samples = edge.samples.iter().copied().map(move_point).collect();
            edge.curve = haecceity::geom::Curve::Circle {
                frame: circle,
                radius: r,
            };
            for face in &mut faces {
                face.pcurves.retain(|(e, _)| *e != i);
            }
        }
        let base = edges.len();
        let vertex = edges
            .iter()
            .flat_map(|e| [e.vertices.0, e.vertices.1])
            .max()
            .unwrap_or(0)
            + 1;
        for (i, z) in span.into_iter().enumerate() {
            let c = haecceity::geom::Curve::Circle {
                frame: Frame {
                    origin: t.at(z),
                    ..frame
                },
                radius: r,
            };
            let p = c.value(0.);
            edges.push(Edge {
                curve: c.clone(),
                start: p,
                end: p,
                vertices: (vertex + i, vertex + i),
                same_sense: true,
                samples: (0..=64).map(|k| c.value(TAU * k as f64 / 64.)).collect(),
            });
        }
        let p = edges[base].start;
        let q = edges[base + 1].start;
        edges.push(Edge {
            curve: haecceity::geom::Curve::Line {
                origin: p,
                dir: t.axis,
            },
            start: p,
            end: q,
            vertices: (vertex, vertex + 1),
            same_sense: true,
            samples: vec![p, q],
        });
        let solid = t.feature.faces.iter().find_map(|&i| part.faces[i].solid);
        if let Some(s) = solid.and_then(|s| solids.get_mut(s)) {
            s.faces.push(faces.len());
        }
        faces.push(Face {
            surface: Surface::Cylinder { frame, radius: r },
            reversed: t.internal,
            loops: vec![Loop {
                edges: vec![
                    (base, true),
                    (base + 2, true),
                    (base + 1, false),
                    (base + 2, false),
                ],
                vertex: None,
            }],
            solid,
            pcurves: Vec::new(),
        });
    }
    Some(Part::new(faces, edges, solids))
}

/// Where a coaxial end plane or chamfer cone crosses radius *r*, along the thread's axis.
fn crest_level(surface: &Surface, t: &Thread, r: f64) -> Option<f64> {
    let (frame, at) = match surface {
        Surface::Plane { frame } => (frame, 0.),
        Surface::Cone {
            frame,
            radius,
            semi_angle,
        } if semi_angle.sin().abs() > 1e-6 => {
            (frame, (r - radius) / semi_angle.sin() * semi_angle.cos())
        }
        _ => return None,
    };
    let coaxial = geom::dot(frame.z, t.axis).abs() > 1. - 1e-8
        && (matches!(surface, Surface::Plane { .. })
            || geom::norm(geom::cross(geom::sub(frame.origin, t.on), t.axis)) < 1e-5);
    coaxial.then(|| {
        geom::dot(
            geom::sub(geom::add(frame.origin, geom::scale(frame.z, at)), t.on),
            t.axis,
        )
    })
}

/// Extra ISO root and termination curves. Witnesses lie on the carrier crests,
/// so hidden-line tests do not mistake an internal root line inside material for
/// a visible surface. Kept-half clipping and crop clipping apply to these too.
pub(crate) fn ink(
    a: &Analysis,
    spec: &Spec,
    v: &PlannedView,
    part: &Part,
) -> (Vec<Item>, Vec<Item>, Vec<Item>) {
    let (mut thick, mut thin, mut hidden) = (Vec::new(), Vec::new(), Vec::new());
    let ts: Vec<_> = threads(a).filter(|t| t.enabled(spec)).collect();
    if ts.is_empty() || v.line_style.thread_depiction == Some(false) {
        return (thick, thin, hidden);
    }
    // Invariant: Planned cameras come from the fixed views or checked section/detail cameras, with nonparallel axes.
    let view = View::new(v.direction, v.up).expect("planned view");
    let centre = crate::detail::crop_extent(a, v).1;
    let page = |p: V3| {
        let q = view.map(p);
        [0, 1].map(|k| v.at[k] + v.scale * (q[k] - centre[k]))
    };
    let faces: Vec<_> = part
        .solids
        .iter()
        .flat_map(|s| s.faces.iter().copied())
        .collect();
    let faces = if faces.is_empty() {
        (0..part.faces.len()).collect()
    } else {
        faces
    };
    let rays = haecceity::rays::RayCaster::for_faces(part, faces.clone());
    let reach = part.bounds().diagonal() * 2. + 1.;
    let epsilon = 1e-5 * reach;
    let cut = v
        .cut
        .as_ref()
        .or_else(|| v.detail.as_ref().and_then(|d| d.source_cut.as_ref()));
    let material = cut.map(|_| Classifier::for_faces(part, faces));
    // Adjacent equal verdicts remain one exact curve; the open quarter and line
    // widths survive SVG/PDF/PNG export without tessellating the conventional ink.
    let mut draw = |curve: Curve,
                    witness: &dyn Fn(f64) -> V3,
                    bold: bool,
                    force_hidden: bool,
                    material_only: bool| {
        let verdict = |u: f64| -> Option<bool> {
            let p = witness(u);
            let mut end = reach;
            if let Some(c) = cut {
                // An external limit in section ends at the cut material. A
                // hollow shaft's white bore must not acquire a diameter bar.
                if material_only
                    && let Some(classifier) = &material
                    && [-1., 1.].into_iter().any(|side| {
                        classifier.classify(geom::add(p, geom::scale(c.normal, side * 1e-5)))
                            == State::Out
                    })
                {
                    return None;
                }
                let d = geom::dot(geom::sub(p, c.origin), c.normal);
                if d < -epsilon {
                    return None;
                }
                let rate = -geom::dot(view.toward, c.normal);
                if rate > 1e-8 {
                    end = (d / rate).max(0.);
                }
            }
            Some(
                force_hidden
                    || (end > epsilon && rays.any_hit(p, view.toward, epsilon, end) == Some(true)),
            )
        };
        let count = if matches!(curve, Curve::Conic { .. }) {
            72
        } else {
            24
        };
        let mut start = 0.;
        let mut previous = verdict(0.5 / count as f64);
        for i in 1..=count {
            let next = if i == count {
                None
            } else {
                verdict((i as f64 + 0.5) / count as f64)
            };
            if next != previous || i == count {
                let boundary = if i == count {
                    1.
                } else {
                    let mut lo = (i as f64 - 0.5) / count as f64;
                    let mut hi = (i as f64 + 0.5) / count as f64;
                    for _ in 0..30 {
                        let mid = (lo + hi) * 0.5;
                        if verdict(mid) == previous {
                            lo = mid;
                        } else {
                            hi = mid;
                        }
                    }
                    (lo + hi) * 0.5
                };
                if let Some(h) = previous {
                    let item = Item::Edge(curve.trim(start, boundary));
                    if h {
                        if v.line_style.hidden.unwrap_or(v.id != "v:iso") && cut.is_none() {
                            hidden.push(item);
                        }
                    } else if bold {
                        thick.push(item);
                    } else {
                        thin.push(item);
                    }
                }
                start = boundary;
                previous = next;
            }
        }
    };
    for t in ts {
        let along = geom::dot(t.axis, view.toward);
        let section = cut.is_some_and(|c| {
            geom::dot(t.axis, c.normal).abs() < 1e-6
                && geom::dot(geom::sub(t.on, c.origin), c.normal).abs() < t.crest()
        });
        let concealed = t.internal && along.abs() < 1e-6 && !section;
        let gap = ((t.root() - t.crest()).abs() * v.scale).max(1.0);
        let root = if t.internal {
            t.crest() + gap / v.scale
        } else {
            (t.crest() - gap / v.scale).max(t.crest() * 0.1)
        };
        if along.abs() > 1. - 1e-6 {
            let z = if along > 0. { t.span[1] } else { t.span[0] };
            let radial = |u: f64, r: f64| {
                let theta = FRAC_PI_2 + u * (TAU - FRAC_PI_2);
                geom::add(
                    t.at(z),
                    geom::add(
                        geom::scale(view.x, r * theta.cos()),
                        geom::scale(view.y, r * theta.sin()),
                    ),
                )
            };
            let witness = |u| radial(u, t.crest() + if t.internal { -epsilon } else { epsilon });
            draw(
                Curve::Conic {
                    centre: page(t.at(z)),
                    radii: [root * v.scale; 2],
                    x_axis: [1., 0.],
                    y_axis: [0., 1.],
                    t0: FRAC_PI_2,
                    t1: TAU,
                },
                &witness,
                false,
                false,
                false,
            );
        } else {
            // Invariant: The end-on branch was excluded, so axis and view direction have a nonzero cross product.
            let u = geom::unit(geom::cross(t.axis, view.toward)).unwrap();
            let mut offset = [0.; 3];
            let mut crest = t.crest();
            let mut root_chord = root;
            if section && let Some(c) = cut {
                let d = geom::dot(geom::sub(t.on, c.origin), c.normal);
                offset = geom::scale(c.normal, -d);
                crest = (crest * crest - d * d).sqrt();
                root_chord = (root * root - d * d).max(0.).sqrt();
            }
            let at = |z, r| geom::add(geom::add(t.at(z), offset), geom::scale(u, r));
            for side in [-1., 1.] {
                let witness = |f| {
                    at(
                        t.span[0] + f * (t.span[1] - t.span[0]),
                        side * (crest + if t.internal { -epsilon } else { epsilon }),
                    )
                };
                draw(
                    Curve::Line(
                        page(at(t.span[0], side * root_chord)),
                        page(at(t.span[1], side * root_chord)),
                    ),
                    &witness,
                    false,
                    concealed,
                    false,
                );
            }
            for z in t.span {
                if along.abs() > 1e-6 && !section {
                    let w = geom::cross(t.axis, u);
                    let ux = view.map(u);
                    let wy = view.map(w);
                    let length = wy[0].hypot(wy[1]);
                    let witness = |fraction: f64| {
                        let theta = fraction * TAU;
                        geom::add(
                            t.at(z),
                            geom::add(
                                geom::scale(
                                    u,
                                    (crest + if t.internal { -epsilon } else { epsilon })
                                        * theta.cos(),
                                ),
                                geom::scale(
                                    w,
                                    (crest + if t.internal { -epsilon } else { epsilon })
                                        * theta.sin(),
                                ),
                            ),
                        )
                    };
                    draw(
                        Curve::Conic {
                            centre: page(t.at(z)),
                            radii: [crest * v.scale, crest * v.scale * length],
                            x_axis: ux,
                            y_axis: [wy[0] / length, wy[1] / length],
                            t0: 0.,
                            t1: TAU,
                        },
                        &witness,
                        true,
                        false,
                        false,
                    );
                    continue;
                }
                let toward =
                    // Invariant: The end-on branch was excluded, so the transverse viewing component is nonzero.
                    geom::unit(geom::sub(view.toward, geom::scale(t.axis, along))).unwrap();
                let witness = |f| {
                    let radial = (2. * f - 1.) * crest;
                    if section || t.internal {
                        at(z, radial)
                    } else {
                        geom::add(
                            at(z, radial),
                            geom::scale(
                                toward,
                                (crest * crest - radial * radial).max(0.).sqrt() + epsilon,
                            ),
                        )
                    }
                };
                draw(
                    Curve::Line(page(at(z, -crest)), page(at(z, crest))),
                    &witness,
                    true,
                    concealed,
                    section && !t.internal,
                );
            }
        }
    }
    if v.detail.is_some() {
        let size = crate::detail::crop_extent(a, v).0;
        let rect = [
            v.at[0] - size[0] * v.scale / 2.,
            v.at[1] - size[1] * v.scale / 2.,
            v.at[0] + size[0] * v.scale / 2.,
            v.at[1] + size[1] * v.scale / 2.,
        ];
        for list in [&mut thick, &mut thin, &mut hidden] {
            *list = std::mem::take(list)
                .into_iter()
                .flat_map(|i| match i {
                    Item::Edge(c) | Item::TextStroke(c, _) => crate::detail::clip(c, rect)
                        .into_iter()
                        .map(Item::Edge)
                        .collect(),
                    Item::Face(_) | Item::Glyph(_, _) => Vec::new(),
                })
                .collect();
        }
    }
    (thick, thin, hidden)
}

pub(crate) fn cache_key(a: &Analysis, s: &Spec, v: &PlannedView) -> Vec<u64> {
    let ts: Vec<_> = threads(a).collect();
    if ts.is_empty() {
        return crate::springs::cache_key(a, s);
    }
    let mut key = vec![
        0x6410,
        u64::from(v.line_style.thread_depiction != Some(false)),
    ];
    key.extend(ts.iter().map(|t| u64::from(t.enabled(s))));
    key.extend(crate::springs::cache_key(a, s));
    key
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Evidence {
    pub edges: Vec<usize>,
    pub curved: Vec<usize>,
    pub span: [f64; 2],
    /// (solid scope, first chord, exclusive end): only exclusively claimed edges.
    pub profile_ranges: Vec<[usize; 3]>,
}
pub(crate) fn evidence(part: &Part, a: &Analysis) -> std::collections::BTreeMap<String, Evidence> {
    let mut curved = vec![None; part.faces.len()];
    let mut n = 0;
    for (i, f) in part.faces.iter().enumerate() {
        if !matches!(f.surface, Surface::Plane { .. }) {
            curved[i] = Some(n);
            n += 1;
        }
    }
    threads(a)
        .map(|t| {
            let edges: Vec<usize> = (0..part.edges.len())
                .filter(|&e| {
                    !part.edge_faces()[e].is_empty()
                        && part.edge_faces()[e]
                            .iter()
                            .all(|i| t.feature.faces.contains(i))
                })
                .collect();
            let curved = t
                .feature
                .faces
                .iter()
                .filter_map(|&i| curved.get(i).copied().flatten())
                .collect();
            let scopes: Vec<Vec<usize>> = if part.solids.len() > 1 {
                part.solids.iter().map(|s| s.faces.clone()).collect()
            } else {
                vec![(0..part.faces.len()).collect()]
            };
            let mut profile_ranges = Vec::new();
            for (scope, faces) in scopes.into_iter().enumerate() {
                let source_edges: BTreeSet<_> =
                    faces.iter().flat_map(|&f| part.face_edges(f)).collect();
                let mut first = 0;
                for e in source_edges {
                    let end = first + part.edges[e].samples.len().saturating_sub(1).max(1);
                    if edges.contains(&e) {
                        profile_ranges.push([scope, first, end]);
                    }
                    first = end;
                }
            }
            let (lo, hi) = part.extent_along(&t.feature.faces, t.axis);
            let shift = geom::dot(t.on, t.axis);
            (
                t.feature.id.clone(),
                Evidence {
                    edges,
                    curved,
                    profile_ranges,
                    span: [(lo - shift).min(t.span[0]), (hi - shift).max(t.span[1])],
                },
            )
        })
        .collect()
}

pub(crate) fn planning<'a>(a: &'a Analysis, s: &Spec) -> std::borrow::Cow<'a, Analysis> {
    if a.modelled_thread_ink.is_empty()
        || s.sheet.thread_depiction == Some(false)
        || s.views.values().any(|v| v.thread_depiction == Some(false))
    {
        return std::borrow::Cow::Borrowed(a);
    }
    let ts: Vec<_> = threads(a).filter(|t| t.enabled(s)).collect();
    if ts.is_empty() {
        return std::borrow::Cow::Borrowed(a);
    }
    let mut prepared = a.clone();
    if let Some(ink) = &mut prepared.edge_ink {
        let mut omitted = BTreeSet::new();
        for t in ts {
            if let Some(e) = a.modelled_thread_ink.get(&t.feature.id) {
                for &i in &e.edges {
                    if let Some(edge) = ink.edges.get_mut(i) {
                        edge.clear();
                    }
                }
                omitted.extend(e.curved.iter().copied());
                let r = t.crest();
                let ends = e.span.map(|z| t.at(z));
                let lo = [0, 1, 2]
                    .map(|k| ends[0][k].min(ends[1][k]) - r * (1. - t.axis[k] * t.axis[k]).sqrt());
                let hi = [0, 1, 2]
                    .map(|k| ends[0][k].max(ends[1][k]) + r * (1. - t.axis[k] * t.axis[k]).sqrt());
                ink.curved.push(crate::model::projected_ink::CurvedFace {
                    bounds: [lo, hi],
                    cylinder: Some(crate::model::projected_ink::Cylinder {
                        origin: t.on,
                        axis: t.axis,
                        radius: r,
                    }),
                });
            }
        }
        ink.curved = std::mem::take(&mut ink.curved)
            .into_iter()
            .enumerate()
            .filter_map(|(i, c)| (!omitted.contains(&i)).then_some(c))
            .collect();
        ink.resolved.clear();
    }
    // The principal-profile evidence contains dense source chords too. Retain
    // all unclaimed chords by their source indices, replacing only thread edges
    // with the same smooth carrier boundaries used for annotation clearance.
    for (scope, source) in prepared.principal_profiles.iter_mut().enumerate() {
        let enabled: Vec<_> = threads(a).filter(|t| t.enabled(s)).collect();
        let mut ranges: Vec<_> = enabled
            .iter()
            .filter_map(|t| a.modelled_thread_ink.get(&t.feature.id))
            .flat_map(|e| e.profile_ranges.iter().copied())
            .filter(|r| r[0] == scope)
            .collect();
        ranges.sort_unstable_by_key(|r| r[1]);
        let mut range = 0;
        let mut index = 0;
        source.boundary_segments.retain(|_| {
            while range < ranges.len() && ranges[range][2] <= index {
                range += 1;
            }
            let keep = range == ranges.len() || index < ranges[range][1];
            index += 1;
            keep
        });
        for t in enabled {
            let Some(e) = a.modelled_thread_ink.get(&t.feature.id) else {
                continue;
            };
            if !e.profile_ranges.iter().any(|r| r[0] == scope) {
                continue;
            }
            let seed = if t.axis[0].abs() < 0.9 {
                [1., 0., 0.]
            } else {
                [0., 1., 0.]
            };
            // Invariant: Thread::read normalizes the axis; the chosen seed is not parallel to it.
            let x = geom::unit(geom::cross(t.axis, seed)).unwrap();
            let y = geom::cross(t.axis, x);
            let at = |z, theta: f64| {
                geom::add(
                    t.at(z),
                    geom::add(
                        geom::scale(x, t.crest() * theta.cos()),
                        geom::scale(y, t.crest() * theta.sin()),
                    ),
                )
            };
            for z in e.span {
                for i in 0..128 {
                    source.boundary_segments.push([
                        at(z, TAU * i as f64 / 128.),
                        at(z, TAU * (i + 1) as f64 / 128.),
                    ]);
                }
            }
            for theta in [0., FRAC_PI_2, std::f64::consts::PI, 3. * FRAC_PI_2] {
                source
                    .boundary_segments
                    .push([at(e.span[0], theta), at(e.span[1], theta)]);
            }
        }
    }
    // Preparing an already prepared analysis is unnecessary during recovery.
    prepared.modelled_thread_ink.clear();
    std::borrow::Cow::Owned(prepared)
}

pub(crate) fn source_hash(original: &Analysis, prepared: bool, plan: &mut crate::model::Plan) {
    if prepared {
        use sha2::{Digest, Sha256};
        plan.analysis_sha256 = Some(
            // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
            Sha256::digest(serde_json::to_vec(original).expect("analysis serializes"))
                .iter()
                .map(|b| format!("{b:02x}"))
                .collect(),
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    fn feature(d: f64, p: f64, internal: bool) -> Feature {
        serde_json::from_value(json!({"id":"f:modelled_thread:1","kind":"modelled_thread","faces":[0],
            "fingerprint":{"version":"test","family":"modelled_threads","subtype":"ModelledThread","sizes":{},"axis":[0,0,1],"position":[0,0,4],"neighbourhood":[]},
            "parameters":{"axis":{"point":[0,0,0],"direction":[0,0,1]},"side":if internal {"internal"}else{"external"},"major_diameter":d,"minor_diameter":d-p,"pitch":p,"hand":"right","starts":1,"start":0,"end":8,"length":8}
        })).unwrap()
    }
    fn analysis(f: Feature) -> Analysis {
        serde_json::from_value(json!({"schema":crate::model::ANALYSIS_SCHEMA,"binding":{"source_sha256":"fixture","part":"thread","face_count":1,"loader":"test"},"envelope":[[-10,-10,0],[10,10,8]],"iso_bounds":[-10,-10,10,10],"features":[f]})).unwrap()
    }
    fn view(end: bool) -> PlannedView {
        serde_json::from_value(json!({"id":if end {"v:plan"}else{"v:front"},"direction":if end {[0,0,1]}else{[0,-1,0]},"up":if end {[0,1,0]}else{[0,0,1]},"at":[50,50],"scale":1.,"area":[20,20,80,80],"default":true,"basis":"fixture","state":{"kind":"shown"}})).unwrap()
    }
    fn plain_carrier(a: &Analysis) -> Part {
        // A single full cylindrical face, with circular end trims. A fixture of
        // the line grammar need not import a helix just to measure its replacement.
        let frame = Frame {
            origin: [0.; 3],
            x: [1., 0., 0.],
            y: [0., 1., 0.],
            z: [0., 0., 1.],
        };
        let mut edges = Vec::new();
        for (i, z) in [0., 8.].into_iter().enumerate() {
            let c = haecceity::geom::Curve::Circle {
                frame: Frame {
                    origin: [0., 0., z],
                    ..frame
                },
                radius: 10.,
            };
            let p = c.value(0.);
            edges.push(Edge {
                curve: c.clone(),
                start: p,
                end: p,
                vertices: (i, i),
                same_sense: true,
                samples: (0..=64).map(|k| c.value(k as f64 * TAU / 64.)).collect(),
            });
        }
        let source = Part::new(
            vec![Face {
                surface: Surface::Cylinder { frame, radius: 10. },
                reversed: false,
                loops: vec![
                    Loop {
                        edges: vec![(0, true)],
                        vertex: None,
                    },
                    Loop {
                        edges: vec![(1, false)],
                        vertex: None,
                    },
                ],
                solid: None,
                pcurves: vec![],
            }],
            edges,
            vec![],
        );
        carrier(&source, a, &Spec::default(), true).unwrap()
    }
    #[test]
    fn coarse_fine_and_corpus_designations() {
        for (d, p, internal, expected) in [
            (8., 1.25, false, "M8×1.25"),
            (14., 1.5, false, "M14×1.5"),
            (13.623798, 1.5, true, "M14×1.5"),
            (61.623798, 1.5, true, "M62×1.5"),
            (75., 1.5, false, "M75×1.5"),
            (20., 2.5, false, "M20×2.5"),
        ] {
            assert_eq!(
                Thread::read(&feature(d, p, internal))
                    .unwrap()
                    .designation(),
                (expected.into(), true)
            );
        }
    }
    #[test]
    fn oblique_axes_keep_their_callout_and_conventional_depiction() {
        let mut f = feature(20., 1., false);
        f.parameters
            .insert("axis".into(), json!({"point":[0,0,0],"direction":[1,1,1]}));
        let a = analysis(f);
        let plan = crate::model::plan(&a, &Spec::default()).unwrap();
        assert!(plan.dimensions.iter().any(|d| d.label.contains("M20×1")));
        assert!(
            !plan
                .problems
                .iter()
                .any(|p| p.code == "modelled_thread_not_depicted")
        );
    }
    #[test]
    fn callouts_state_only_the_designation_and_retain_extent_as_evidence() {
        for (diameter, pitch, internal, expected) in [
            (75., 1.5, false, "M75×1.5"),
            (61.623798, 1.5, true, "M62×1.5"),
            (13.623798, 1.5, true, "M14×1.5"),
            (109.999797, 6.35, false, "⌀110 × P6.35"),
        ] {
            let a = analysis(feature(diameter, pitch, internal));
            let plan = crate::model::plan(&a, &Spec::default()).unwrap();
            let callout = plan
                .dimensions
                .iter()
                .find(|d| d.id == "d:f:modelled_thread:1/thread")
                .unwrap();
            assert_eq!(callout.label, expected);
            assert!(!callout.label.contains('\n'));
            assert!(
                callout
                    .measurements
                    .iter()
                    .any(|m| m.parameter == "thread.length" && m.value == 8.)
            );
            assert!(
                !plan
                    .dimensions
                    .iter()
                    .any(|d| d.id.ends_with("/thread.length"))
            );
            if diameter > 100. {
                assert!(
                    plan.problems
                        .iter()
                        .any(|p| p.code == "thread_designation_measured")
                );
                // Compact numeric spelling must not hide the rounding finding.
                assert!(plan.problems.iter().any(|p| p.code == "nominal_rounded"));
            }
        }
    }
    #[test]
    fn matching_tolerance_edges_and_unlisted_pairs() {
        for (d, p, inside) in [
            (8.02, 1.26, true),
            (8.020001, 1.25, false),
            (8., 1.260001, false),
            (7.98, 1.25, true),
            (7.979999, 1.25, false),
            (13.61, 1.5, true),
            (13.609999, 1.5, false),
            (110., 6.35, false),
            (13., 1.5, false),
        ] {
            assert_eq!(
                Thread::read(&feature(d, p, d > 13. && d < 14.))
                    .unwrap()
                    .designation()
                    .1,
                inside,
                "{d}/{p}"
            );
        }
        assert_eq!(
            Thread::read(&feature(110., 6.35, false))
                .unwrap()
                .designation(),
            ("⌀110 × P6.35".into(), false)
        );
    }
    #[test]
    fn left_hand_and_multiple_starts_keep_pitch_and_lead_distinct() {
        let mut f = feature(20., 1., false);
        f.parameters.insert("hand".into(), json!("left"));
        f.parameters.insert("starts".into(), json!(2));
        assert_eq!(Thread::read(&f).unwrap().designation().0, "M20×Ph2P1-LH");
        f.parameters.insert("major_diameter".into(), json!(110.));
        f.parameters.insert("pitch".into(), json!(6.35));
        assert_eq!(
            Thread::read(&f).unwrap().designation().0,
            "⌀110 × P6.35 2 STARTS LH"
        );
    }
    #[test]
    fn external_profile_and_end_arc_use_iso_weights_and_open_quarter() {
        let a = analysis(feature(20., 1., false));
        let source = plain_carrier(&a);
        let s = Spec::default();
        let (bold, thin, hidden) = ink(&a, &s, &view(false), &source);
        assert!(!bold.is_empty());
        assert!(hidden.is_empty());
        assert_eq!(thin.len(), 2);
        for i in &thin {
            let Item::Edge(Curve::Line(p, q)) = i else {
                panic!("{i:?}")
            };
            assert!((p[0] - q[0]).abs() < 1e-9);
            assert!(((p[1] - q[1]).abs() - 8.).abs() < 1e-9);
            assert!(((p[0] - 50.).abs() - 9.0).abs() < 1e-9);
        }
        let (_, thin, hidden) = ink(&a, &s, &view(true), &source);
        assert!(hidden.is_empty());
        assert_eq!(thin.len(), 1);
        let Item::Edge(Curve::Conic { radii, t0, t1, .. }) = &thin[0] else {
            panic!()
        };
        assert_eq!(*radii, [9.0; 2]);
        assert!((t1 - t0 - TAU * 0.75).abs() < 1e-9);
        assert_eq!(*t0, FRAC_PI_2);
        let styles = crate::output::layer_styles();
        assert!(styles[0].1.width > crate::output::thin_style().width);
        assert!(crate::output::thin_style().dash.is_none());
    }
    #[test]
    fn a_shared_root_circle_becomes_a_crest_boundary_without_changing_source() {
        let a = analysis(feature(20., 1., false));
        let original = plain_carrier(&a);
        let mut edges = original.edges.clone();
        for edge in &mut edges[2..4] {
            let haecceity::geom::Curve::Circle { frame, .. } = edge.curve.clone() else {
                panic!()
            };
            let shrink = |p| geom::add(frame.origin, geom::scale(geom::sub(p, frame.origin), 0.95));
            edge.start = shrink(edge.start);
            edge.end = shrink(edge.end);
            edge.samples = edge.samples.iter().copied().map(shrink).collect();
            edge.curve = haecceity::geom::Curve::Circle { frame, radius: 9.5 };
        }
        let mut faces = original.faces.clone();
        let Surface::Cylinder { frame, .. } = faces[0].surface else {
            panic!()
        };
        faces[0].surface = Surface::Cylinder { frame, radius: 9.5 };
        faces.push(Face {
            surface: Surface::Plane { frame },
            reversed: true,
            loops: vec![Loop {
                edges: vec![(2, false)],
                vertex: None,
            }],
            solid: None,
            pcurves: vec![],
        });
        let source = Part::new(faces, edges, vec![]);
        let proxy = carrier(&source, &a, &Spec::default(), true).unwrap();
        assert!(
            matches!(proxy.edges[2].curve,haecceity::geom::Curve::Circle {radius,..} if radius==10.)
        );
        assert!(
            matches!(source.edges[2].curve,haecceity::geom::Curve::Circle {radius,..} if radius==9.5)
        );
        assert!((proxy.face_bounds(0).max[0] - 10.).abs() < 1e-6);
    }
    #[test]
    fn oblique_terminations_follow_projected_circles_instead_of_crossing_openings() {
        let a = analysis(feature(20., 1., false));
        let source = plain_carrier(&a);
        let mut v = view(false);
        v.direction = [1., -1., 1.];
        let (thick, thin, _) = ink(&a, &Spec::default(), &v, &source);
        assert!(!thick.is_empty());
        assert_eq!(thin.len(), 2);
        assert!(
            thick
                .iter()
                .all(|i| matches!(i, Item::Edge(Curve::Conic { .. })))
        );
    }
    #[test]
    fn internal_hidden_and_section_roots_follow_bore_and_hatching_boundary() {
        let a = analysis(feature(20., 1.5, true));
        let source = plain_carrier(&a);
        let s = Spec::default();
        let (_, thin, hidden) = ink(&a, &s, &view(false), &source);
        assert!(thin.is_empty());
        assert!(!hidden.is_empty());
        let mut v = view(false);
        v.cut = Some(
            serde_json::from_value(
                json!({"origin":[0,0,4],"normal":[0,1,0],"label":"A","marked_in":"v:plan"}),
            )
            .unwrap(),
        );
        let (_, thin, hidden) = ink(&a, &s, &v, &source);
        assert_eq!(thin.len(), 2);
        assert!(hidden.is_empty());
        assert!(
            matches!(source.faces[0].surface,Surface::Cylinder {radius,..} if (radius-9.25).abs()<1e-9)
        );
        let section = haecceity::hlr::section(
            &source,
            &haecceity::hlr::Plane::new([0., 0., 4.], [0., 1., 0.]).unwrap(),
        )
        .unwrap();
        assert!(
            section
                .iter()
                .flatten()
                .all(|p| (p[0].abs() - 9.25).abs() < 1e-5
                    || p[2].abs() < 1e-5
                    || (p[2] - 8.).abs() < 1e-5)
        );
    }
    #[test]
    fn external_section_limits_stop_at_a_hollow_shafts_bore() {
        let a = analysis(feature(20., 1., false));
        let outer = plain_carrier(&a);
        let mut edges = outer.edges.clone();
        for edge in &outer.edges[2..5] {
            let mut inner = edge.clone();
            let shrink = |p: V3| [p[0] * 0.6, p[1] * 0.6, p[2]];
            inner.start = shrink(inner.start);
            inner.end = shrink(inner.end);
            inner.samples = inner.samples.into_iter().map(shrink).collect();
            inner.vertices = (inner.vertices.0 + 2, inner.vertices.1 + 2);
            inner.curve = match inner.curve {
                haecceity::geom::Curve::Circle { frame, .. } => {
                    haecceity::geom::Curve::Circle { frame, radius: 6. }
                }
                haecceity::geom::Curve::Line { origin, dir } => haecceity::geom::Curve::Line {
                    origin: shrink(origin),
                    dir,
                },
                _ => unreachable!(),
            };
            edges.push(inner);
        }
        let mut faces = outer.faces.clone();
        faces[0].solid = Some(0);
        let Surface::Cylinder { frame, .. } = faces[0].surface else {
            panic!()
        };
        faces.push(Face {
            surface: Surface::Cylinder { frame, radius: 6. },
            reversed: true,
            loops: vec![Loop {
                edges: vec![(5, true), (7, true), (6, false), (7, false)],
                vertex: None,
            }],
            solid: Some(0),
            pcurves: vec![],
        });
        for (z, outside, inside, reversed) in [(0., 2, 5, true), (8., 3, 6, false)] {
            faces.push(Face {
                surface: Surface::Plane {
                    frame: Frame {
                        origin: [0., 0., z],
                        ..frame
                    },
                },
                reversed,
                loops: vec![
                    Loop {
                        edges: vec![(outside, !reversed)],
                        vertex: None,
                    },
                    Loop {
                        edges: vec![(inside, reversed)],
                        vertex: None,
                    },
                ],
                solid: Some(0),
                pcurves: vec![],
            });
        }
        let part = Part::new(
            faces,
            edges,
            vec![Solid {
                faces: vec![0, 1, 2, 3],
            }],
        );
        let mut v = view(false);
        v.cut = Some(
            serde_json::from_value(json!({
                "origin":[0,0,0],"normal":[0,1,0],"label":"A","marked_in":"v:plan"
            }))
            .unwrap(),
        );
        let (bold, _, _) = ink(&a, &Spec::default(), &v, &part);
        assert_eq!(bold.len(), 4, "two wall strips at each thread limit");
        for item in bold {
            let Item::Edge(Curve::Line(p, q)) = item else {
                panic!()
            };
            assert!((p[1] - q[1]).abs() < 1e-8);
            for point in [p, q] {
                assert!((point[0] - 50.).abs() >= 6. - 1e-5, "{point:?}");
            }
            assert!(((p[0] - q[0]).abs() - 4.).abs() < 1e-5, "{p:?} {q:?}");
        }
    }
    #[test]
    fn spec_off_restores_source_and_designation_override_has_no_geometry_effect() {
        let a = analysis(feature(110., 6.35, false));
        let source = plain_carrier(&a);
        let s:Spec=serde_json::from_value(json!({"features":{"f:modelled_thread:1":{"modelled_thread":{"depiction":false,"designation":"SPECIAL THREAD"}}}})).unwrap();
        assert!(carrier(&source, &a, &s, true).is_none());
        assert_eq!(ink(&a, &s, &view(false), &source), (vec![], vec![], vec![]));
        let on:Spec=serde_json::from_value(json!({"features":{"f:modelled_thread:1":{"modelled_thread":{"designation":"SPECIAL THREAD"}}}})).unwrap();
        assert_eq!(
            cache_key(&a, &Spec::default(), &view(false)),
            cache_key(&a, &on, &view(false))
        );
        let plan = crate::model::plan(&a, &s).unwrap();
        assert!(plan.dimensions.iter().any(|d| d.label == "SPECIAL THREAD"));
        assert!(
            plan.problems
                .iter()
                .any(|p| p.code == "modelled_thread_not_depicted")
        );
        assert!(
            !plan
                .problems
                .iter()
                .any(|p| p.code == "thread_designation_measured")
        );
    }
    #[test]
    fn planning_replaces_only_claimed_profile_chords_and_binds_the_original_analysis() {
        use sha2::{Digest, Sha256};
        let mut a = analysis(feature(20., 1., false));
        let chords = vec![
            [[0., 0., 3.], [1., 0., 3.]],
            [[3., 2., 4.], [4., 2., 4.]],
            [[3., 2., 5.], [4., 2., 5.]],
            [[0., 0., 5.], [1., 0., 5.]],
        ];
        a.principal_profiles = serde_json::from_value(
            json!([{"bounds":a.envelope,"faces":[],"boundary_segments":chords}]),
        )
        .unwrap();
        a.modelled_thread_ink.insert(
            "f:modelled_thread:1".into(),
            Evidence {
                edges: vec![],
                curved: vec![],
                span: [0., 8.],
                profile_ranges: vec![[0, 1, 3]],
            },
        );
        let prepared = planning(&a, &Spec::default());
        assert_eq!(
            &prepared.principal_profiles[0].boundary_segments[..2],
            &[chords[0], chords[3]]
        );
        assert!(
            !prepared.principal_profiles[0]
                .boundary_segments
                .contains(&chords[1])
        );
        assert_eq!(a.principal_profiles[0].boundary_segments, chords);
        let off: Spec = serde_json::from_value(
            json!({"features":{"f:modelled_thread:1":{"modelled_thread":{"depiction":false}}}}),
        )
        .unwrap();
        assert_eq!(
            planning(&a, &off).principal_profiles[0].boundary_segments,
            chords
        );
        let p = crate::model::plan(&a, &Spec::default()).unwrap();
        let hash: String = Sha256::digest(serde_json::to_vec(&a).unwrap())
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        assert_eq!(p.analysis_sha256, Some(hash));
    }
    #[test]
    fn pmi_thread_callout_precedes_inference_in_first_and_strict_modes() {
        use std::io::Read;
        let mut bytes = Vec::new();
        flate2::read::GzDecoder::new(
            &include_bytes!("../tests/fixtures/pmi/step/bolt_thread_knurl.step.gz")[..],
        )
        .read_to_end(&mut bytes)
        .unwrap();
        let (_, mut a) =
            crate::model::analyse_native_step(std::path::Path::new("bolt.step"), &bytes).unwrap();
        let manufacturing = crate::pmi_lowering::manufacturing(&a);
        let owner = manufacturing
            .owned
            .iter()
            .find(|o| o.aspect == "thread")
            .unwrap();
        let original = a.features.iter().find(|f| f.id == owner.owner).unwrap();
        let mut f = feature(6., 0.5, false);
        f.faces = original.faces.clone();
        let crate::pmi::Reading::Read(report) = a.pmi.as_ref().unwrap() else {
            panic!()
        };
        let c = report
            .records
            .iter()
            .find(|r| r.source_id == owner.source)
            .unwrap()
            .geometry
            .as_ref()
            .unwrap()
            .cylinders[0]
            .clone();
        f.parameters.insert(
            "axis".into(),
            json!({"point":c.origin,"direction":c.direction}),
        );
        f.parameters.insert("start".into(), json!(c.interval[0]));
        f.parameters.insert("end".into(), json!(c.interval[1]));
        a.features.push(f);
        for mode in [crate::model::PmiMode::First, crate::model::PmiMode::Strict] {
            let s = Spec {
                pmi: mode,
                ..Spec::default()
            };
            let p = crate::model::plan(&a, &s).unwrap();
            assert!(
                !p.dimensions
                    .iter()
                    .any(|d| d.id == "d:f:modelled_thread:1/thread")
            );
            assert_eq!(
                p.dimensions
                    .iter()
                    .filter(|d| d
                        .measurements
                        .iter()
                        .any(|m| m.feature == owner.id && m.parameter == "thread"))
                    .count(),
                1
            );
        }
        let p = crate::model::plan(
            &a,
            &Spec {
                pmi: crate::model::PmiMode::Ignore,
                ..Spec::default()
            },
        )
        .unwrap();
        assert!(
            p.dimensions
                .iter()
                .any(|d| d.id == "d:f:modelled_thread:1/thread")
        );
    }
}
