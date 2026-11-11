//! Non-right convex corners on the foremost camera-facing outer profile of each body (and of
//! each plate), notch flanks excepted.
//! Python's profile_angles.py: supplied ordered supports establish adjacency; inner wires,
//! back faces, concave and right corners never issue automatic requirements.
use super::{LintIssue, Mark, holes};
use crate::model::Analysis;
use serde::{Deserialize, Serialize};

type P3 = [f64; 3];
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum Support {
    Line {
        start: P3,
        end: P3,
    },
    Arc {
        start: P3,
        end: P3,
        center: P3,
        radius: f64,
        sweep: f64,
    },
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Profile {
    pub origin: P3,
    pub normal: P3,
    pub supports: Vec<Support>,
    pub inner_loop_count: usize,
    pub schema_version: u8,
    pub boundary_kind: String,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Source {
    pub face: usize,
    pub body_faces: Vec<usize>,
    pub edges: Vec<usize>,
    pub profile: Profile,
}
/// Keep the provider's evidence as geometry, independent of recognition feature conversion.
pub fn evidence(part: &haecceity::Part) -> Vec<Source> {
    (0..part.faces.len())
        .filter_map(|face| {
            let source = crate::quiddity_evidence::planar_outer_profile(part, face)?;
            Some(Source {
                face,
                body_faces: source.body_faces,
                edges: source.edges,
                profile: serde_json::from_value(
                    // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
                    serde_json::to_value(source.profile).expect("profile serializes"),
                )
                // Invariant: The provider Profile type is serialized and read using the matching consumer schema.
                .expect("provider profile schema"),
            })
        })
        .collect()
}
fn sub(a: P3, b: P3) -> P3 {
    [0, 1, 2].map(|i| a[i] - b[i])
}
fn dot(a: P3, b: P3) -> f64 {
    (0..3).map(|i| a[i] * b[i]).sum()
}
fn cross(a: P3, b: P3) -> P3 {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}
fn norm(a: P3) -> f64 {
    dot(a, a).sqrt()
}
fn unit(a: P3) -> P3 {
    let n = norm(a);
    a.map(|x| x / n)
}
#[derive(Clone, Debug)]
pub struct Corner {
    pub feature: String,
    pub axis: usize,
    pub vertex: P3,
    pub first: P3,
    pub second: P3,
    pub virtual_vertex: bool,
    /// The far ends of its two edges (each the theoretical sharp vertex when that end is
    /// rounded): with the vertex, the points whose stations fix the corner linearly.
    pub ends: [P3; 2],
}
impl Corner {
    /// Whether each of its two edges (towards `ends[0]`, then `ends[1]`) runs along a principal
    /// direction of its view.
    pub fn principal_edges(&self) -> [bool; 2] {
        [
            principal(self.axis, (self.ends[0], self.vertex)),
            principal(self.axis, (self.vertex, self.ends[1])),
        ]
    }

    /// Its two edges' IDs, in the order of [`Corner::principal_edges`].
    pub fn edge_ids(&self) -> [&str; 2] {
        let mut parts = self.feature.rsplitn(3, ':');
        let second = parts.next().unwrap_or_default();
        let first = parts.next().unwrap_or_default();
        [first, second]
    }

    pub fn degrees(&self) -> f64 {
        let (u, v) = (
            unit(sub(self.first, self.vertex)),
            unit(sub(self.second, self.vertex)),
        );
        norm(cross(u, v)).atan2(dot(u, v)).to_degrees()
    }
}
/// Same-body feature definitions use exact source edge/face identity, never matching values.
fn bevel_edges(a: &Analysis, s: &Source) -> (bool, Vec<usize>) {
    let mut bevels = Vec::new();
    for f in &a.features {
        let polygon = matches!(f.kind.as_str(), "polygonal_stock" | "polygonal_boss");
        if !(polygon || f.kind == "chamfer")
            || f.faces.is_empty()
            || !f.faces.iter().all(|i| s.body_faces.contains(i))
        {
            continue;
        }
        if f.parameters.get("turned").and_then(|v| v.as_bool()) == Some(true) {
            continue;
        }
        let edges: Vec<_> = a
            .outer_profiles
            .iter()
            .filter(|p| f.faces.contains(&p.face))
            .flat_map(|p| p.edges.iter().copied())
            .collect();
        let all_lines = s
            .profile
            .supports
            .iter()
            .all(|s| matches!(s, Support::Line { .. }));
        let count = f.parameters.get("side_count").and_then(|v| v.as_u64());
        if polygon
            && all_lines
            && count == Some(s.edges.len() as u64)
            && (f.faces.contains(&s.face)
                || s.edges.iter().all(|e| edges.contains(e))
                || parallel_to_flats(a, f, s))
        {
            return (true, Vec::new());
        }
        if !polygon {
            bevels.extend(edges);
        }
    }
    (false, bevels)
}
/// A chamfered or offset cap of a regular polygon (CTC-01's hex boss top) keeps every side
/// parallel to one of the polygon's flats, so the polygon callout states its corner angles.
fn parallel_to_flats(a: &Analysis, f: &crate::bridge::Feature, s: &Source) -> bool {
    let frame = crate::bridge::holes::frame(a);
    let axis = super::parameters::direction(&frame, f.parameters.get("axis"));
    let flats: Option<Vec<P3>> = f
        .parameters
        .get("flat_directions")
        .and_then(|v| v.as_array())
        .and_then(|ds| {
            ds.iter()
                .map(|d| super::parameters::direction(&frame, Some(d)))
                .collect()
        });
    let (Some(axis), Some(flats)) = (axis, flats) else {
        return false;
    };
    norm(cross(unit(axis), s.profile.normal)) <= 1e-6
        && flats.len() == s.edges.len()
        && s.profile.supports.iter().all(|l| {
            let Support::Line { start, end } = l else {
                return false;
            };
            let along = unit(sub(*end, *start));
            flats.iter().any(|d| dot(along, unit(*d)).abs() <= 1e-3)
        })
}
fn flat(p: P3, axis: usize) -> P3 {
    let mut q = p;
    q[axis] = 0.;
    q
}
/// A line along a principal direction needs no stated angle.
fn principal(axis: usize, line: (P3, P3)) -> bool {
    unit(sub(flat(line.1, axis), flat(line.0, axis)))
        .iter()
        .any(|v| v.abs() >= 1. - 1e-9)
}
/// A line collinear (in the view) with a common tangent of two arcs centred on located hole or
/// boss axes of the same body is fixed by those circles (GRM-04's taper between R3 and R4):
/// their own size and location requirements determine its direction.
fn common_tangent(a: &Analysis, s: &Source, axis: usize, line: (P3, P3)) -> bool {
    let flat = |p: P3| flat(p, axis);
    let (p0, p1) = (flat(line.0), flat(line.1));
    let d = unit(sub(p1, p0));
    let frame = crate::bridge::holes::frame(a);
    let centres: Vec<P3> = a
        .features
        .iter()
        .filter(|f| {
            matches!(f.kind.as_str(), "hole" | "boss")
                && !f.faces.is_empty()
                && f.faces.iter().all(|i| s.body_faces.contains(i))
                && super::parameters::direction(&frame, f.parameters.get("axis"))
                    .is_some_and(|v| unit(v)[axis].abs() >= 1. - 1e-6)
        })
        .filter_map(|f| super::parameters::place(&frame, f.parameters.get("location")))
        .map(flat)
        .collect();
    let located = |c: P3| centres.iter().any(|h| norm(sub(*h, flat(c))) <= 1e-4);
    a.outer_profiles.iter().any(|o| {
        let q = &o.profile;
        if o.body_faces != s.body_faces
            || q.boundary_kind != "outer"
            || q.normal[axis].abs() < 1. - 1e-8
        {
            return false;
        }
        let n = q.supports.len();
        (0..n).any(|k| {
            let Support::Line { start, end } = q.supports[k] else {
                return false;
            };
            let (m0, m1) = (flat(start), flat(end));
            let e = unit(sub(m1, m0));
            if norm(cross(d, e)) > 1e-6 || norm(cross(sub(m0, p0), d)) > 1e-4 {
                return false;
            }
            let ends = [
                (&q.supports[(k + n - 1) % n], m0),
                (&q.supports[(k + 1) % n], m1),
            ];
            ends.iter().all(|(arc, at)| {
                let Support::Arc { center, .. } = arc else {
                    return false;
                };
                let r = sub(*at, flat(*center));
                dot(unit(r), e).abs() <= 1e-6 && located(*center)
            })
        })
    })
}
/// The far end of line support *i*: its start (or its end, *forward*), or the theoretical
/// sharp vertex with the next line when a single arc rounds that end.
fn far_end(p: &Profile, i: usize, forward: bool) -> P3 {
    let n = p.supports.len();
    let Support::Line { start, end } = p.supports[i] else {
        unreachable!("corner edges are lines")
    };
    let (at, step) = if forward { (end, 1) } else { (start, n - 1) };
    let (arc, next) = ((i + step) % n, (i + 2 * step) % n);
    if let (Support::Arc { .. }, Support::Line { start: c0, end: c1 }) =
        (&p.supports[arc], &p.supports[next])
    {
        let (u, v) = (unit(sub(end, start)), unit(sub(*c1, *c0)));
        let turn = dot(cross(u, v), p.normal);
        if turn.abs() > 1e-8 {
            let t = dot(cross(sub(*c0, start), v), p.normal) / turn;
            return [0, 1, 2].map(|k| start[k] + t * u[k]);
        }
    }
    at
}

/// The stations (axis, coordinate) a corner's linear definition needs that *located* does
/// not give, deduplicated: empty when its two edges are fixed by linear dimensions alone.
/// A principal edge needs its one offset; an oblique edge needs both coordinates of both of
/// its ends (ISO 129-1: no angle is implied except 0°, 90° and 180°).
pub fn undefined_stations(c: &Corner, located: &dyn Fn(usize, f64) -> bool) -> Vec<(usize, f64)> {
    let plane: Vec<usize> = (0..3).filter(|&k| k != c.axis).collect();
    let mut need = Vec::new();
    for (p, q) in [(c.ends[0], c.vertex), (c.vertex, c.ends[1])] {
        let d = unit(sub(flat(q, c.axis), flat(p, c.axis)));
        if let Some(&k) = plane.iter().find(|&&k| d[k].abs() >= 1. - 1e-9) {
            // Along axis k: its offset on the other plane axis fixes it.
            let m = plane[usize::from(plane[0] == k)];
            need.push((m, p[m]));
        } else {
            for r in [p, q] {
                need.extend(plane.iter().map(|&k| (k, r[k])));
            }
        }
    }
    let mut out: Vec<(usize, f64)> = Vec::new();
    for (k, v) in need {
        if !located(k, v) && !out.iter().any(|&(j, w)| j == k && (w - v).abs() < 1e-4) {
            out.push((k, v));
        }
    }
    out
}

/// Every non-reference linear dimension drawn, re-measured from its two witnesses through its
/// view's recorded transform ([`super::location::ink_span`]): the spans that locate stations.
/// A counted profile station (`2× v`, [`crate::dims::profile_angles`]) from one side of the
/// part's box states its mirror from the other side too; no other counted dimension does (a
/// pattern pitch or slot width touching a box side states no mirrored station).
pub fn drawn_spans(a: &Analysis, drawing: &crate::output::Drawing) -> Vec<(usize, f64, f64)> {
    let text = |name: &str| {
        drawing
            .annotations
            .iter()
            .find(|x| x.name == name)
            .map(|x| crate::output::drawn_body(drawing, x).unwrap_or_default())
    };
    drawing
        .sheet
        .items
        .iter()
        .filter(|d| d.kind == crate::output::Kind::Dimension && d.elbow.is_none())
        .filter_map(|d| {
            let body = text(&d.name).filter(|t| !t.trim_start().starts_with('('))?;
            let view = drawing
                .annotations
                .iter()
                .find(|x| x.name == d.name)?
                .view
                .clone()?;
            let frame = drawing.sheet.frames.get(view.trim_start_matches("v:"))?;
            let span = super::location::ink_span(a, frame, &d.lines)?;
            let counted =
                body.trim_start().starts_with("2×") && d.name.contains("/profile_location_");
            Some((span, counted))
        })
        .flat_map(|((k, p, q), counted)| {
            let [lo, hi] = [a.envelope[0][k], a.envelope[1][k]];
            let side = |v: f64| (v - lo).abs() < 1e-3 || (v - hi).abs() < 1e-3;
            let mirror = (counted && (side(p) || side(q))).then_some((k, lo + hi - p, lo + hi - q));
            std::iter::once((k, p, q)).chain(mirror)
        })
        .collect()
}

/// Whether station *at* on *axis* is reached from the part's roots (its box and datum planes)
/// through a chain of *spans* (axis, a, b). Stations match to 1e-3 mm: recognition sites and
/// re-measured witnesses are both rounded finer than any printed dimension.
pub fn rooted(a: &Analysis, spans: &[(usize, f64, f64)], axis: usize, at: f64) -> bool {
    rooted_at(&super::location::roots(a)[axis], spans, axis, at)
}

/// [`rooted`] from the given *roots* on *axis*.
pub fn rooted_at(roots: &[f64], spans: &[(usize, f64, f64)], axis: usize, at: f64) -> bool {
    const TOL: f64 = 1e-3;
    let mut reached = vec![at];
    let mut cursor = 0;
    while cursor < reached.len() {
        let p = reached[cursor];
        cursor += 1;
        if roots.iter().any(|r| (r - p).abs() < TOL) {
            return true;
        }
        for &(k, a, b) in spans {
            for (from, to) in [(a, b), (b, a)] {
                if k == axis
                    && (from - p).abs() < TOL
                    && !reached.iter().any(|q| (q - to).abs() < TOL)
                {
                    reached.push(to);
                }
            }
        }
    }
    false
}

/// The corners the drawing's linear dimensions already fix (see [`undefined_stations`]).
pub fn defined_by_drawing(
    a: &Analysis,
    drawing: &crate::output::Drawing,
) -> std::collections::BTreeSet<String> {
    let corners = requirements(a);
    if corners.is_empty() {
        return Default::default();
    }
    let spans = drawn_spans(a, drawing);
    let roots = super::location::roots(a);
    corners
        .into_iter()
        .filter(|c| undefined_stations(c, &|k, v| rooted_at(&roots[k], &spans, k, v)).is_empty())
        .map(|c| c.feature)
        .collect()
}

/// Whether *c*'s edges are located once its angle is stated: a principal edge by its offset, an
/// oblique edge by both coordinates of at least one of its ends. Two angles alone fix an edge's
/// direction, never where it lies (#217).
pub fn located_with_angle(c: &Corner, located: &dyn Fn(usize, f64) -> bool) -> bool {
    let plane: Vec<usize> = (0..3).filter(|&k| k != c.axis).collect();
    [(c.ends[0], c.vertex), (c.vertex, c.ends[1])]
        .into_iter()
        .all(|(p, q)| {
            let d = unit(sub(flat(q, c.axis), flat(p, c.axis)));
            match plane.iter().find(|&&k| d[k].abs() >= 1. - 1e-9) {
                Some(&k) => {
                    let m = plane[usize::from(plane[0] == k)];
                    located(m, p[m])
                }
                None => [p, q]
                    .iter()
                    .any(|r| plane.iter().all(|&k| located(k, r[k]))),
            }
        })
}

/// The other end and the leg of the 45° chamfer *c* is one end of: a cut across a right corner of
/// principal edges whose two corners are both 135° (equal legs), as the profile's *corners* give
/// them. A chamfer is called out by its size (`C<leg>`, the edge chamfers' house form), which
/// states both its corners' angles.
pub fn chamfer<'a>(
    c: &Corner,
    corners: impl IntoIterator<Item = &'a Corner>,
) -> Option<(&'a Corner, f64)> {
    let chamfered = |c: &Corner| (c.degrees() - 135.).abs() < 1e-3;
    let principal = c.principal_edges();
    if principal[0] == principal[1] || !chamfered(c) {
        return None;
    }
    let oblique = c.edge_ids()[usize::from(principal[0])];
    let other = corners.into_iter().find(|d| {
        d.feature != c.feature && {
            let (ids, p) = (d.edge_ids(), d.principal_edges());
            (ids[0] == oblique && !p[0] && p[1]) || (ids[1] == oblique && p[0] && !p[1])
        }
    })?;
    chamfered(other).then(|| (other, norm(sub(other.vertex, c.vertex)) / 2f64.sqrt()))
}

/// Whether chamfer corner *c* (the other end *other*) lies where its size puts it: its principal
/// edge's offset and the other end's principal edge's offset are located, so the cut runs a leg
/// from the corner they meet at.
pub fn chamfer_located(c: &Corner, other: &Corner, located: &dyn Fn(usize, f64) -> bool) -> bool {
    principal_axes(c).is_some_and(|(k, m)| located(m, c.vertex[m]) && located(k, other.vertex[k]))
}

/// The axis corner *c*'s principal edge runs along and the axis its offset is measured on.
pub fn principal_axes(c: &Corner) -> Option<(usize, usize)> {
    let plane: Vec<usize> = (0..3).filter(|&k| k != c.axis).collect();
    let edge = if c.principal_edges()[0] {
        (c.ends[0], c.vertex)
    } else {
        (c.vertex, c.ends[1])
    };
    let d = unit(sub(flat(edge.1, c.axis), flat(edge.0, c.axis)));
    let &k = plane.iter().find(|&&k| d[k].abs() >= 1. - 1e-9)?;
    Some((k, plane[usize::from(plane[0] == k)]))
}

/// Whether *text* is a chamfer callout of size *leg*: `C<leg>`, optionally counted (`4× C21.075`).
pub fn states_chamfer(text: &str, leg: f64) -> bool {
    let t = text.trim();
    let t = t
        .split_once("× ")
        .filter(|(n, _)| n.parse::<usize>().is_ok())
        .map_or(t, |(_, rest)| rest);
    t.strip_prefix('C')
        .filter(|v| v.parse::<f64>().is_ok())
        .is_some_and(|v| holes::states(v, leg))
}

/// Whether some mark claims corner *c*'s angle with a chamfer callout of size *leg*.
fn chamfer_called(c: &Corner, leg: f64, marks: &[Mark]) -> bool {
    marks.iter().any(|m| {
        m.claims
            .iter()
            .any(|cl| cl.feature == c.feature && cl.parameter == "profile_angle.angle")
            && states_chamfer(&m.text, m.units.from_mm(leg))
    })
}

/// The corners the drawing's linear dimensions neither fix nor locate for an angle (see
/// [`located_with_angle`]): even a stated angle leaves their edges unplaced. A chamfer called
/// out by its size is located by its principal edges' offsets ([`chamfer_located`]).
pub fn unlocated_by_drawing(a: &Analysis, drawing: &crate::output::Drawing) -> Vec<Corner> {
    let corners = requirements(a);
    if corners.is_empty() {
        return corners;
    }
    let spans = drawn_spans(a, drawing);
    let roots = super::location::roots(a);
    let located = |k: usize, v: f64| rooted_at(&roots[k], &spans, k, v);
    let marks = super::marks(drawing);
    corners
        .iter()
        .filter(|c| match chamfer(c, &corners) {
            Some((other, leg)) if chamfer_called(c, leg, &marks) => {
                !chamfer_located(c, other, &located)
            }
            _ => !located_with_angle(c, &located),
        })
        .cloned()
        .collect()
}

/// A corner whose angle is drawn but whose edges no linear dimension places (#217).
pub fn unlocated(a: &Analysis, c: &Corner) -> LintIssue {
    let mut finding = super::uncovered(
        a,
        "profile_corner_unlocated",
        &c.feature,
        "profile_angle.location",
        format!(
            "Outer-profile corner {} is not located: its angle fixes the edge's direction, not \
             where the edge lies.",
            c.feature
        ),
    );
    finding.fix = Some(
        "Locate the corner's stations with linear dimensions (the profile's `linear` choice)."
            .into(),
    );
    finding
}

pub fn requirements(a: &Analysis) -> Vec<Corner> {
    let candidates: Vec<_> = a
        .outer_profiles
        .iter()
        .filter_map(|s| {
            let p = &s.profile;
            if ![1, 2].contains(&p.schema_version) || p.boundary_kind != "outer" {
                return None;
            }
            let axis = (0..3).max_by(|&i, &j| p.normal[i].abs().total_cmp(&p.normal[j].abs()))?;
            let side = if axis == 1 { -1. } else { 1. };
            (side * p.normal[axis] >= 1. - 1e-8
                && (0..3).all(|i| i == axis || p.normal[i].abs() <= 1e-8))
            .then_some((s, axis, side))
        })
        .collect();
    // A plate's face shows its own true shape (#217: a bent bracket's base plate behind the
    // motor plate's end face): only a nearer face of the same plate hides it.
    let plate = |face: usize| {
        a.features
            .iter()
            .find(|f| f.kind == "plate" && f.faces.contains(&face))
    };
    let mut out = Vec::new();
    for &(s, axis, side) in &candidates {
        let own = plate(s.face);
        if candidates.iter().any(|&(other, k, _)| {
            k == axis
                && other.body_faces == s.body_faces
                && own.is_none_or(|p| p.faces.contains(&other.face))
                && side * (other.profile.origin[axis] - s.profile.origin[axis]) > 1e-6
        }) {
            continue;
        }
        let (polygon, bevels) = bevel_edges(a, s);
        if polygon {
            continue;
        }
        // A notch's flanks are stated by the notch's own angle and location (CTC-01's V-slots).
        let notched: Vec<usize> = super::notch_evidence::from_profile(a, s)
            .iter()
            .flat_map(|n| n.id.split(":e").skip(1).filter_map(|e| e.parse().ok()))
            .collect();
        let p = &s.profile;
        let n = p.supports.len();
        for (i, first) in p.supports.iter().enumerate() {
            let Support::Line { start: a0, end: a1 } = first else {
                continue;
            };
            let mut j = (i + 1) % n;
            let rounded = matches!(p.supports[j], Support::Arc { .. });
            let transition = &p.supports[j];
            if rounded {
                j = (j + 1) % n;
            }
            let Support::Line { start: b0, end: b1 } = p.supports[j] else {
                continue;
            };
            let (u, v) = (unit(sub(*a1, *a0)), unit(sub(b1, b0)));
            let turn = dot(cross(u, v), p.normal);
            if turn <= 1e-8 || dot(u, v).abs() <= 1e-8 {
                continue;
            }
            let distance = dot(cross(sub(b0, *a1), v), p.normal) / turn;
            let vertex = [0, 1, 2].map(|k| a1[k] + distance * u[k]);
            let along = dot(sub(vertex, b0), v);
            let rebuilt = [0, 1, 2].map(|k| b0[k] + along * v[k]);
            if norm(sub(vertex, rebuilt)) > 1e-6 {
                continue;
            }
            let (first, second) = if let Support::Arc { center, .. } = transition {
                if distance <= 1e-6
                    || along >= -1e-6
                    || dot(u, sub(*a1, *center)).abs() > 1e-6
                    || dot(v, sub(b0, *center)).abs() > 1e-6
                {
                    continue;
                }
                (*a1, b0)
            } else {
                if norm(sub(vertex, *a1)) > 1e-6 || *a1 != b0 {
                    continue;
                }
                if [i, j]
                    .iter()
                    .any(|&i| s.edges.get(i).is_some_and(|e| bevels.contains(e)))
                {
                    continue;
                }
                (*a0, b1)
            };
            if [i, j]
                .iter()
                .any(|&k| s.edges.get(k).is_some_and(|e| notched.contains(e)))
            {
                continue;
            }
            let lines = [(*a0, *a1), (b0, b1)];
            let tangent = lines.map(|l| common_tangent(a, s, axis, l));
            if tangent.iter().any(|&t| t) && (0..2).all(|k| tangent[k] || principal(axis, lines[k]))
            {
                continue;
            }
            out.push(Corner {
                feature: format!("outer_profile:f{}:e{}:e{}", s.face, s.edges[i], s.edges[j]),
                axis,
                vertex,
                first,
                second,
                virtual_vertex: rounded,
                ends: [far_end(p, i, false), far_end(p, j, true)],
            });
        }
    }
    out
}
/// Require live, role-bound, degree-bearing ink for every issued corner.
pub fn coverage(a: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let corners = requirements(a);
    corners
        .iter()
        .filter_map(|c| {
            // A chamfer's size callout states both its corners' 135°.
            if chamfer(c, &corners).is_some_and(|(_, leg)| chamfer_called(c, leg, marks)) {
                return None;
            }
            let claimed: Vec<_> = marks
                .iter()
                .filter(|m| {
                    m.claims
                        .iter()
                        .any(|cl| cl.feature == c.feature && cl.parameter == "profile_angle.angle")
                })
                .collect();
            let degrees = c.degrees();
            if claimed.iter().any(|m| {
                m.text
                    .strip_suffix('°')
                    .is_some_and(|t| holes::states(t, degrees))
            }) {
                return None;
            }
            let code = if claimed.is_empty() {
                "angular_requirement_missing"
            } else {
                "angular_requirement_unverifiable"
            };
            let mut finding = super::uncovered(
                a,
                code,
                &c.feature,
                "profile_angle.angle",
                format!(
                    "Outer-profile corner {} requires {:.1}° on the drawing.",
                    c.feature, degrees
                ),
            );
            finding.annotation = claimed.first().map(|m| m.name.clone());
            Some(finding)
        })
        .collect()
}
