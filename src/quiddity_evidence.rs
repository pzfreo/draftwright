//! Geometry evidence quiddity no longer exports: face levels and risers, planar outer profiles
//! and inner wires, read on the part as placed.
//!
//! quiddity 0.1.0 made its caller-space helpers private (quiddity-rust #61): its only entry point
//! recognises in the part's inferred frame. Draftwright reads these in the file's axes, so they
//! are carried here, copied from quiddity-rust v0.1.0 (`src/features/levels.rs`,
//! `outer_profile.rs`, `wire_seed.rs`, `policy.rs`, `evidence.rs`; Apache-2.0, Copyright Paul
//! Fremantle) with the parts draftwright does not read left out: body keys (draftwright drops
//! them, and within one scope they are equal), refusal reasons, and the locally degraded run
//! (only quiddity's own recognition enters one, so a direct call was always strict).

use std::cmp::Ordering;

use haecceity::Part;
use haecceity::geom::{Bounds, COORD_FLOOR, Curve, Surface, V3, cross, sub};
use haecceity::py;
use haecceity::sampling::edge_interval;
use serde::Serialize;

// --- policy.rs ---

/// A normal component above this is along that axis (`AXIS_ALIGNED_COS`).
const AXIS_ALIGNED_COS: f64 = 0.99;
/// A normal component at or below this is no component at all (`AXIS_ZERO_COS`).
const AXIS_ZERO_COS: f64 = 0.01;

/// Above the threshold, an exact tie (to 1e-9) counting as below.
fn clears_threshold(magnitude: f64, threshold: f64) -> bool {
    magnitude >= threshold * (1.0 + 1e-9)
}

/// Bounded coordinate clusters: a unique minimum-cardinality partition of each connected
/// component; a component with several minimum partitions is refused (left out).
fn cluster_coordinates(coordinates: &[f64], tol: f64) -> Vec<Vec<usize>> {
    let mut order: Vec<usize> = (0..coordinates.len()).collect();
    order.sort_by(|&a, &b| py::order(coordinates[a], coordinates[b]).then(a.cmp(&b)));
    let mut out = Vec::new();
    let mut start = 0;
    while start < order.len() {
        let mut end = start + 1;
        while end < order.len()
            && coordinates[order[end]] - coordinates[order[end - 1]] <= tol + COORD_FLOOR
        {
            end += 1;
        }
        let component = &order[start..end];
        let n = component.len();
        let mut minimum = vec![n + 1; n + 1];
        let mut ways = vec![0usize; n + 1];
        let mut next = vec![0; n];
        minimum[n] = 0;
        ways[n] = 1;
        for i in (0..n).rev() {
            for j in i..n {
                if coordinates[component[j]] - coordinates[component[i]] > tol + COORD_FLOOR {
                    break;
                }
                let count = 1 + minimum[j + 1];
                if count < minimum[i] {
                    minimum[i] = count;
                    ways[i] = ways[j + 1];
                    next[i] = j + 1;
                } else if count == minimum[i] {
                    ways[i] = (ways[i] + ways[j + 1]).min(2);
                }
            }
        }
        if ways[0] == 1 {
            let mut i = 0;
            while i < n {
                out.push(component[i..next[i]].to_vec());
                i = next[i];
            }
        }
        start = end;
    }
    out
}

// --- levels.rs ---

/// Band within which two horizontal faces are one level (absolute, ADR 0008).
const TOL: f64 = 0.5;
/// A level whose faces cover less of the plan footprint is an incidental face, not a step.
const STEP_MIN_AREA_FRAC: f64 = 0.01;
/// A bounded slanted face is a structural ramp only at this fraction of the part on each axis.
const STRUCTURAL_RAMP_MIN_FRAC: f64 = 0.1;
/// A bounded riser must fill this fraction of its own footprint.
const BOUNDED_RISER_AREA_FRAC: f64 = 0.5;
/// `recognise_risers`' default area floor.
const RISER_MIN_AREA_FRAC: f64 = 0.15;

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct FaceLevel {
    pub(crate) z: f64,
    pub(crate) x_span: (f64, f64),
    pub(crate) y_span: (f64, f64),
}

impl FaceLevel {
    fn order(&self, other: &Self) -> Ordering {
        py::order(self.z, other.z)
            .then_with(|| {
                py::tuple_order(
                    &[self.x_span.0, self.x_span.1],
                    &[other.x_span.0, other.x_span.1],
                )
            })
            .then_with(|| {
                py::tuple_order(
                    &[self.y_span.0, self.y_span.1],
                    &[other.y_span.0, other.y_span.1],
                )
            })
    }
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct RiserEvidence {
    pub(crate) faces: Vec<usize>,
    pub(crate) vertical: bool,
    pub(crate) axis: char,
    pub(crate) positions: Vec<f64>,
    pub(crate) other_axis: char,
    pub(crate) other_positions: Vec<f64>,
    pub(crate) z_lo: f64,
    pub(crate) z_hi: f64,
    pub(crate) lo_at_envelope: bool,
    pub(crate) hi_at_envelope: bool,
    pub(crate) tol: f64,
    pub(crate) body_levels: Vec<FaceLevel>,
}

impl RiserEvidence {
    fn order(&self, other: &Self) -> Ordering {
        self.vertical
            .cmp(&other.vertical)
            .then(self.axis.cmp(&other.axis))
            .then_with(|| py::tuple_order(&self.positions, &other.positions))
            .then(self.other_axis.cmp(&other.other_axis))
            .then_with(|| py::tuple_order(&self.other_positions, &other.other_positions))
            .then(py::order(self.z_lo, other.z_lo))
            .then(py::order(self.z_hi, other.z_hi))
            .then(self.lo_at_envelope.cmp(&other.lo_at_envelope))
            .then(self.hi_at_envelope.cmp(&other.hi_at_envelope))
            .then(py::order(self.tol, other.tol))
            .then_with(|| {
                self.body_levels
                    .iter()
                    .zip(&other.body_levels)
                    .map(|(a, b)| a.order(b))
                    .find(|o| o.is_ne())
                    .unwrap_or(self.body_levels.len().cmp(&other.body_levels.len()))
            })
    }
}

/// The faces of the part that are drawn: every solid's, or every face of a part with no solid,
/// as haecceity's hidden-line removal draws them. Surface models read beside a solid (a CATIA
/// fill bounded by sketch curves, say) are construction geometry, not the part (#215).
pub(crate) fn drawn_faces(part: &Part) -> Vec<usize> {
    let mut faces: Vec<usize> = part
        .solids
        .iter()
        .flat_map(|s| s.faces.iter().copied())
        .collect();
    if faces.is_empty() {
        faces = (0..part.faces.len()).collect();
    }
    faces.sort_unstable();
    faces.dedup();
    faces
}

/// The box of *faces*, the part's [`drawn_faces`]: its envelope.
pub(crate) fn drawn_bounds(part: &Part, faces: &[usize]) -> Bounds {
    let mut b = Bounds::empty();
    for &f in faces {
        b.merge(&part.face_bounds(f));
    }
    b
}

/// One body the levels are read within: a solid, or the whole part when it has none.
struct Scope {
    faces: Vec<usize>,
    bounds: Bounds,
}

fn scopes(part: &Part) -> Vec<Scope> {
    if part.solids.is_empty() {
        return vec![Scope {
            faces: (0..part.faces.len()).collect(),
            bounds: part.bounds(),
        }];
    }
    part.solids
        .iter()
        .enumerate()
        .map(|(s, solid)| Scope {
            faces: solid.faces.clone(),
            bounds: part.solid_bounds(s),
        })
        .collect()
}

/// The levels of one scope with the faces supporting each (`_face_level_proposals_one`).
fn level_proposals(
    part: &Part,
    scope: &Scope,
    tol: f64,
    min_area_frac: f64,
) -> Vec<(FaceLevel, Vec<usize>)> {
    let mut zs = Vec::new();
    let mut faces = Vec::new();
    for &f in &scope.faces {
        if let Surface::Plane { frame } = part.faces[f].surface
            && frame.z[2].abs() > AXIS_ALIGNED_COS
        {
            zs.push(frame.origin[2]);
            faces.push(f);
        }
    }
    let b = &scope.bounds;
    let threshold = min_area_frac * (b.max[0] - b.min[0]) * (b.max[1] - b.min[1]);
    let mut out = Vec::new();
    for cluster in cluster_coordinates(&zs, tol) {
        // A level whose area cannot be integrated is not proposed (not read as area 0).
        if min_area_frac > 0.0 {
            let areas: Option<Vec<f64>> = cluster
                .iter()
                .map(|&i| part.face_mass(faces[i]).map(|m| m[0]))
                .collect();
            if !areas.is_some_and(|a| clears_threshold(py::sum(a), threshold)) {
                continue;
            }
        }
        let spans: Vec<Bounds> = cluster
            .iter()
            .map(|&i| part.face_bounds(faces[i]))
            .collect();
        // Python's `min`/`max`: the first of equals wins, which decides a zero's sign.
        let fold = |pick: fn(&Bounds) -> f64, max: bool| {
            spans.iter().map(pick).reduce(|a, x| {
                let better = if max { x > a } else { x < a };
                if better { x } else { a }
            })
        };
        // Invariant: a cluster holds at least one face, so every fold has a first value.
        let span = |lo, hi| {
            fold(lo, false)
                .zip(fold(hi, true))
                .expect("non-empty cluster")
        };
        out.push((
            FaceLevel {
                z: zs[cluster[py::first_min(&cluster, |&i| zs[i])]],
                x_span: span(|b| b.min[0], |b| b.max[0]),
                y_span: span(|b| b.min[1], |b| b.max[1]),
            },
            cluster.iter().map(|&i| faces[i]).collect(),
        ));
    }
    out
}

/// `recognise_face_levels`' levels at the default band, each with the faces it was read from.
pub(crate) fn face_levels_with_faces(
    part: &Part,
    min_area_frac: f64,
) -> Vec<(FaceLevel, Vec<usize>)> {
    let mut out: Vec<(FaceLevel, Vec<usize>)> = scopes(part)
        .iter()
        .flat_map(|scope| level_proposals(part, scope, TOL, min_area_frac))
        .collect();
    out.sort_by(|a, b| a.0.order(&b.0));
    out
}

/// The scope's interior step levels: area-filtered, strictly inside its height by *tol*.
fn body_levels(part: &Part, scope: &Scope, tol: f64) -> Vec<FaceLevel> {
    let (lo, hi) = (scope.bounds.min[2], scope.bounds.max[2]);
    level_proposals(part, scope, TOL, STEP_MIN_AREA_FRAC)
        .into_iter()
        .filter(|(level, _)| lo + tol < level.z && level.z < hi - tol)
        .map(|(level, _)| level)
        .collect()
}

/// `recognise_risers` with its default options.
pub(crate) fn recognise_risers(part: &Part) -> Vec<RiserEvidence> {
    let mut out: Vec<RiserEvidence> = scopes(part)
        .iter()
        .flat_map(|scope| {
            let levels = body_levels(part, scope, TOL);
            riser_proposals(part, scope, RISER_MIN_AREA_FRAC, TOL, levels)
        })
        .collect();
    out.sort_by(|a, b| a.order(b));
    out
}

/// A riser's facing: `(vertical, axis)` when the normal lies along exactly one in-plane axis.
fn riser_orientation(n: V3) -> Option<(bool, usize)> {
    let on_x = n[0].abs() > AXIS_ZERO_COS && n[1].abs() <= AXIS_ZERO_COS;
    let on_y = n[1].abs() > AXIS_ZERO_COS && n[0].abs() <= AXIS_ZERO_COS;
    if !(on_x || on_y) {
        return None;
    }
    Some((n[2].abs() <= AXIS_ZERO_COS, if on_x { 0 } else { 1 }))
}

/// The risers of one scope, equal records merged (`_riser_proposals_one`).
fn riser_proposals(
    part: &Part,
    scope: &Scope,
    min_area_frac: f64,
    tol: f64,
    body_levels: Vec<FaceLevel>,
) -> Vec<RiserEvidence> {
    let bb = &scope.bounds;
    let ext = |i: usize| bb.max[i] - bb.min[i];
    let mut found: Vec<RiserEvidence> = Vec::new();
    for &f in &scope.faces {
        let Surface::Plane { frame } = part.faces[f].surface else {
            continue;
        };
        let Some(normal) = part.face_normal(f, 0.0, 0.0) else {
            continue;
        };
        let Some((vertical, axis)) = riser_orientation(normal) else {
            continue;
        };
        let fb = part.face_bounds(f);
        let other = 1 - axis;
        let (flo, fhi) = (fb.min[other], fb.max[other]);
        // A step crosses its whole body on the other in-plane axis; a pad's or a blind
        // pocket's walls are bounded.
        let full_span = flo <= bb.min[other] + tol && fhi >= bb.max[other] - tol;
        let interior = |i: usize, p: f64| {
            p - bb.min[i] > tol + COORD_FLOOR && bb.max[i] - p > tol + COORD_FLOOR
        };
        let height = fb.max[2] - fb.min[2];
        let (positions, other_positions) = if vertical {
            let pos = frame.origin[axis];
            if !full_span || !interior(axis, pos) {
                continue;
            }
            (vec![pos], vec![])
        } else {
            if tol >= height {
                continue;
            }
            let along = vec![fb.min[axis], fb.max[axis]];
            if full_span {
                (along, vec![])
            } else if fhi - flo < STRUCTURAL_RAMP_MIN_FRAC * ext(other)
                || along[1] - along[0] < STRUCTURAL_RAMP_MIN_FRAC * ext(axis)
                || STRUCTURAL_RAMP_MIN_FRAC * ext(2) > height
            {
                continue; // an edge-break chamfer, not a structural ramp
            } else {
                (along, vec![flo, fhi])
            }
        };
        let cross = ext(other) * ext(2);
        let area_floor = if full_span {
            min_area_frac * cross
        } else {
            BOUNDED_RISER_AREA_FRAC * ((fhi - flo) * height)
        };
        let Some([area, _]) = part.face_mass(f) else {
            continue;
        };
        if cross <= 0.0 || area < area_floor {
            continue;
        }
        let at_envelope = |z: f64| (z - bb.min[2]).abs() < tol || (z - bb.max[2]).abs() < tol;
        let record = RiserEvidence {
            faces: vec![f],
            vertical,
            axis: ['x', 'y'][axis],
            positions: positions
                .into_iter()
                .filter(|&p| interior(axis, p))
                .map(py::round_to3)
                .collect(),
            other_axis: ['x', 'y'][other],
            other_positions: other_positions
                .into_iter()
                .filter(|&p| interior(other, p))
                .map(py::round_to3)
                .collect(),
            z_lo: fb.min[2],
            z_hi: fb.max[2],
            lo_at_envelope: at_envelope(fb.min[2]),
            hi_at_envelope: at_envelope(fb.max[2]),
            tol,
            body_levels: body_levels.clone(),
        };
        if let Some(existing) = found.iter_mut().find(|r| r.order(&record).is_eq()) {
            existing.faces.push(f);
        } else {
            found.push(record);
        }
    }
    found.sort_by(|a, b| a.order(b));
    found
}

// --- wire_seed.rs ---

/// The edges of each of the face's inner loops: every loop but the outer one. A face whose
/// outer loop cannot be told has none read.
pub(crate) fn inner_wires(part: &Part, face: usize) -> Vec<Vec<usize>> {
    let loops = &part.faces[face].loops;
    if loops.len() < 2 {
        return Vec::new();
    }
    let Some(outer) = part.outer_loop(face) else {
        return Vec::new();
    };
    loops
        .iter()
        .enumerate()
        .filter(|&(l, lp)| l != outer && !lp.edges.is_empty())
        .map(|(_, lp)| lp.edges.iter().map(|&(e, _)| e).collect())
        .collect()
}

// --- outer_profile.rs ---

/// Analytic support coincidence (mm), consistent with ADR0008's 1e-6 source endpoint bound.
const POSITION_TOL: f64 = 1e-6;
const DIRECTION_TOL: f64 = 2e-8;

/// An oriented finite straight support, traversed from start to end (`ProfileLine`).
#[derive(Clone, Debug, PartialEq, Serialize)]
pub(crate) struct ProfileLine {
    pub(crate) start: V3,
    pub(crate) end: V3,
}

impl ProfileLine {
    fn direction(&self) -> V3 {
        let length = py::dist(&self.start, &self.end);
        [0, 1, 2].map(|i| (self.end[i] - self.start[i]) / length)
    }
}

/// A finite circular arc; its signed sweep is radians about the profile normal (`ProfileArc`).
#[derive(Clone, Debug, PartialEq, Serialize)]
pub(crate) struct ProfileArc {
    pub(crate) start: V3,
    pub(crate) end: V3,
    pub(crate) center: V3,
    pub(crate) radius: f64,
    pub(crate) sweep: f64,
}

/// One support of an outer profile; serialises with its `kind`.
#[derive(Clone, Debug, PartialEq, Serialize)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub(crate) enum ProfileSupport {
    Line(ProfileLine),
    Arc(ProfileArc),
}

impl ProfileSupport {
    fn start(&self) -> V3 {
        match self {
            ProfileSupport::Line(l) => l.start,
            ProfileSupport::Arc(a) => a.start,
        }
    }

    fn end(&self) -> V3 {
        match self {
            ProfileSupport::Line(l) => l.end,
            ProfileSupport::Arc(a) => a.end,
        }
    }

    fn reversed(&self) -> Self {
        match self {
            ProfileSupport::Line(l) => ProfileSupport::Line(ProfileLine {
                start: l.end,
                end: l.start,
            }),
            ProfileSupport::Arc(a) => ProfileSupport::Arc(ProfileArc {
                start: a.end,
                end: a.start,
                sweep: -a.sweep,
                ..a.clone()
            }),
        }
    }
}

/// `PlanarOuterProfile`: one outer wire, oriented with material left of traversal about the
/// face's outward normal, starting at its least vertex. Schema 2 marks a concave turn or fewer
/// than two lines.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub(crate) struct PlanarOuterProfile {
    pub(crate) origin: V3,
    pub(crate) normal: V3,
    pub(crate) supports: Vec<ProfileSupport>,
    pub(crate) inner_loop_count: usize,
    pub(crate) schema_version: u8,
    pub(crate) boundary_kind: &'static str,
}

/// One profile bound to its source in this part.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct PlanarOuterProfileEvidence {
    pub(crate) profile: PlanarOuterProfile,
    /// Every face of the valid solid the face belongs to, ascending.
    pub(crate) body_faces: Vec<usize>,
    /// Per support, in order, its source edge.
    pub(crate) edges: Vec<usize>,
}

/// The one geometrically valid solid every face belongs to (`common_valid_solid`, strict).
fn common_valid_solid(part: &Part, faces: &[usize]) -> Option<usize> {
    let owner = part.faces.get(*faces.first()?)?.solid?;
    (part.solid_is_geometrically_valid(owner)
        && faces.iter().all(|&f| {
            part.faces
                .get(f)
                .is_some_and(|face| face.solid == Some(owner))
        }))
    .then_some(owner)
}

/// The support's unit tangent at its end (or start) about *normal*.
fn tangent(support: &ProfileSupport, normal: V3, end: bool) -> Option<V3> {
    match support {
        ProfileSupport::Line(l) => Some(l.direction()),
        ProfileSupport::Arc(a) => {
            let radius = sub(if end { a.end } else { a.start }, a.center);
            let t = cross(normal, radius);
            let length = py::hypot(&t);
            if length == 0.0 {
                return None;
            }
            let sign = 1.0f64.copysign(a.sweep);
            Some(t.map(|v| v * sign / length))
        }
    }
}

/// The signed turn from each support into the next about *normal*; a cusp takes its sign from
/// the supports' curvatures (see quiddity's `outer_profile::turns`).
fn turns(supports: &[ProfileSupport], normal: V3) -> Option<Vec<f64>> {
    let n = supports.len();
    let curvature = |s: &ProfileSupport| match s {
        ProfileSupport::Line(_) => 0.0,
        ProfileSupport::Arc(a) => 1.0f64.copysign(a.sweep) / a.radius,
    };
    (0..n)
        .map(|at| {
            let (first, second) = (&supports[at], &supports[(at + 1) % n]);
            let before = tangent(first, normal, true)?;
            let after = tangent(second, normal, false)?;
            let turn = py::dot(&cross(before, after), &normal).atan2(py::dot(&before, &after));
            let (k1, k2) = (curvature(first), curvature(second));
            let sum = k1 + k2;
            Some(
                if turn.abs() > std::f64::consts::PI - DIRECTION_TOL
                    && sum.abs() > 1e-9 * (k1.abs() + k2.abs())
                {
                    -std::f64::consts::PI.copysign(sum)
                } else {
                    turn
                },
            )
        })
        .collect()
}

/// The wire's total turning: its corners' turns plus its arcs' sweeps, each summed exactly.
fn winding(turns: &[f64], supports: &[ProfileSupport]) -> f64 {
    py::fsum(turns.iter().copied())
        + py::fsum(supports.iter().filter_map(|s| match s {
            ProfileSupport::Arc(a) => Some(a.sweep),
            ProfileSupport::Line(_) => None,
        }))
}

fn finite(p: V3) -> bool {
    p.iter().all(|v| v.is_finite())
}

fn line(start: V3, end: V3) -> Option<ProfileLine> {
    let length = py::dist(&start, &end);
    (finite(start) && finite(end) && length.is_finite() && length != 0.0)
        .then_some(ProfileLine { start, end })
}

fn arc(start: V3, end: V3, center: V3, radius: f64, sweep: f64) -> Option<ProfileArc> {
    (finite(start)
        && finite(end)
        && finite(center)
        && radius.is_finite()
        && radius > 0.0
        && sweep.is_finite()
        && 0.0 < sweep.abs()
        && sweep.abs() < std::f64::consts::TAU)
        .then_some(ProfileArc {
            start,
            end,
            center,
            radius,
            sweep,
        })
}

/// `PlanarOuterProfile.__post_init__`: the profile, or `None` where it is incoherent.
fn profile(
    origin: V3,
    normal: V3,
    supports: Vec<ProfileSupport>,
    inner_loop_count: usize,
) -> Option<PlanarOuterProfile> {
    if !finite(origin) || !finite(normal) || (py::hypot(&normal) - 1.0).abs() > 1e-8 {
        return None;
    }
    let n = supports.len();
    if n < 2 {
        return None;
    }
    let off_plane = |p: V3| py::dot(&sub(p, origin), &normal).abs() > 1e-6;
    for (at, support) in supports.iter().enumerate() {
        if support.end() != supports[(at + 1) % n].start() {
            return None;
        }
        if off_plane(support.start()) || off_plane(support.end()) {
            return None;
        }
        if let ProfileSupport::Arc(a) = support {
            if off_plane(a.center)
                || [a.start, a.end]
                    .iter()
                    .any(|p| (py::dist(p, &a.center) - a.radius).abs() > 1e-6)
            {
                return None;
            }
            let radial = sub(a.start, a.center);
            let crossed = cross(normal, radial);
            let reconstructed = [0, 1, 2]
                .map(|i| a.center[i] + a.sweep.cos() * radial[i] + a.sweep.sin() * crossed[i]);
            if py::dist(&reconstructed, &a.end) > 1e-6 {
                return None;
            }
        }
    }
    let turns = turns(&supports, normal)?;
    if (winding(&turns, &supports) - std::f64::consts::TAU).abs() > 2e-8 {
        return None;
    }
    let lines = supports
        .iter()
        .filter(|s| matches!(s, ProfileSupport::Line(_)))
        .count();
    let schema_version = if turns.iter().any(|&t| t < -2e-8) || lines < 2 {
        2
    } else {
        1
    };
    Some(PlanarOuterProfile {
        origin,
        normal,
        supports,
        inner_loop_count,
        schema_version,
        boundary_kind: "outer",
    })
}

/// `_read_profile`: the face's outer wire as a profile with each support's edge.
fn read_profile(part: &Part, face: usize) -> Option<(PlanarOuterProfile, Vec<usize>)> {
    let f = &part.faces[face];
    if !matches!(f.surface, Surface::Plane { .. }) {
        return None;
    }
    let outer = part.outer_loop(face)?;
    let inner_loop_count = f.loops.len() - 1;
    let wire = &f.loops[outer].edges;
    // Each edge once, its vertices joined in order (a seam run twice refuses).
    if wire.is_empty() || (1..wire.len()).any(|i| wire[..i].iter().any(|w| w.0 == wire[i].0)) {
        return None;
    }
    // An unresolved edge carries its chord in its place: not the edge's support.
    if wire.iter().any(|&(e, _)| {
        part.unresolved_edges().contains(&e)
            || !matches!(
                part.edges[e].curve,
                Curve::Line { .. } | Curve::Circle { .. }
            )
    }) {
        return None;
    }
    if wire.len() < 2 {
        return None;
    }
    let normal = part.face_normal(face, 0.0, 0.0)?;
    let first = |&(e, forward): &(usize, bool)| {
        let edge = &part.edges[e];
        if forward {
            (edge.vertices.0, edge.start)
        } else {
            (edge.vertices.1, edge.end)
        }
    };
    let last = |&(e, forward): &(usize, bool)| {
        let edge = &part.edges[e];
        if forward {
            edge.vertices.1
        } else {
            edge.vertices.0
        }
    };
    let points: Vec<V3> = wire.iter().map(|w| first(w).1).collect();
    let origin = points[0];
    let mut supports = Vec::with_capacity(wire.len());
    for (at, w) in wire.iter().enumerate() {
        let following = (at + 1) % wire.len();
        if last(w) != first(&wire[following]).0 {
            return None;
        }
        let (start, end) = (points[at], points[following]);
        if py::dist(&start, &end) == 0.0 {
            return None;
        }
        let edge = &part.edges[w.0];
        // The trimmed curve's own ends: a vertex displaced from them within a larger imported
        // tolerance would invent a different finite support.
        let (a, b) = edge_interval(
            &edge.curve,
            edge.start,
            edge.end,
            edge.same_sense,
            edge.is_closed(),
        );
        let curve_ends = (edge.curve.value(a), edge.curve.value(b));
        let apart = |(p, q): (V3, V3)| py::dist(&start, &p).max(py::dist(&end, &q));
        if apart(curve_ends).min(apart((curve_ends.1, curve_ends.0))) > POSITION_TOL {
            return None;
        }
        let midpoint = edge.curve.value(0.5 * (a + b));
        if [start, end, midpoint]
            .iter()
            .any(|p| py::dot(&sub(*p, origin), &normal).abs() > POSITION_TOL)
        {
            return None;
        }
        match &edge.curve {
            Curve::Line { .. } => {
                let support = line(start, end)?;
                let source = line(curve_ends.0, curve_ends.1)?.direction();
                let d = support.direction();
                if py::dist(&d, &source).min(py::dist(&d, &source.map(|v| -v))) > DIRECTION_TOL {
                    return None;
                }
                supports.push(ProfileSupport::Line(support));
            }
            Curve::Circle { frame, radius } => {
                let center = frame.origin;
                let sense = py::dot(&cross(sub(start, center), sub(midpoint, center)), &normal);
                if sense == 0.0 {
                    return None;
                }
                let length = radius * (b - a).abs();
                let sweep = (length / radius).copysign(sense);
                supports.push(ProfileSupport::Arc(arc(
                    start, end, center, *radius, sweep,
                )?));
            }
            _ => return None,
        }
    }
    let mut edges: Vec<usize> = wire.iter().map(|w| w.0).collect();
    // The wire's own orientation can differ from the face's outward one: choose the whole
    // boundary's winding, not an individual edge's parameter direction.
    let turns = turns(&supports, normal)?;
    let mut winding = winding(&turns, &supports);
    if winding < 0.0 {
        supports = supports
            .iter()
            .rev()
            .map(ProfileSupport::reversed)
            .collect();
        edges.reverse();
        winding = -winding;
    }
    if (winding - std::f64::consts::TAU).abs() > DIRECTION_TOL {
        return None;
    }
    let starts: Vec<V3> = supports.iter().map(ProfileSupport::start).collect();
    let mut least = 0;
    for at in 1..starts.len() {
        if py::tuple_order(&starts[at], &starts[least]).is_lt() {
            least = at;
        }
    }
    supports.rotate_left(least);
    edges.rotate_left(least);
    let profile = profile(supports[0].start(), normal, supports, inner_loop_count)?;
    Some((profile, edges))
}

/// One face's line/arc outer wire with its body's faces and each support's edge, or `None`
/// where quiddity refuses it (not a native plane, another curve, no proved body, incoherent).
pub(crate) fn planar_outer_profile(part: &Part, face: usize) -> Option<PlanarOuterProfileEvidence> {
    let owner = common_valid_solid(part, &[face])?;
    // Exact ownership: equal geometry or touching bodies cannot join.
    let mut body_faces = part.solids[owner].faces.clone();
    if common_valid_solid(part, &body_faces) != Some(owner) {
        return None;
    }
    body_faces.sort_unstable();
    body_faces.dedup();
    let (profile, edges) = read_profile(part, face)?;
    Some(PlanarOuterProfileEvidence {
        profile,
        body_faces,
        edges,
    })
}
