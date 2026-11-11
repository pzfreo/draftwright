//! Outer-profile corners (LINT-angular-profile_angle_requirement), after ISO 129-1 and ASME
//! Y14.5: a corner needs its geometry defined, not necessarily its angle printed.
//!
//! - A corner whose two edges the linear dimensions already locate (both coordinates of each
//!   oblique edge's ends, the offset of a principal edge) is defined: no angle is added. Only
//!   0°, 90° and 180° are implied.
//! - A cut across a corner of principal edges (l-plate-5-holes' 45° cut, a bracket's corner
//!   chamfers) that the linear dimensions do not fix is located by its legs (#217): each missing
//!   station from the nearer side of the part's box, drawn on the side of the view its corner
//!   lies on ([`super::placement`]'s `station_strip`). Its two angles would fix the cut's
//!   direction, not where it lies. Two equal stations from opposite sides of the box (a
//!   symmetric octagon's, a bracket's two chamfers) are stated once, `2× v`, by default.
//! - A 45° chamfer across a right corner (both its corners 135°: equal legs) is called out by its
//!   size instead, `C<leg>` as an edge chamfer is, equal ones of a face counted (`4× C21.075`,
//!   maintainer 2026-10-28); only its principal edges' missing offsets get stations.
//! - Any other corner the linear dimensions do not fix gets its angle, drawn in the view that
//!   shows it true; lint reports it while its edges stay unlocated (`profile_corner_unlocated`).
//!   Completing it with stations is the `linear` profile choice: stations are ordinary
//!   dimensions, and completing every corner automatically moved paper (mfcadpp/1013, 10020,
//!   CTC-04).
//! - A closed all-line profile whose corners are all issued, equal and whose sides are equal (a
//!   regular polygon) gets one counted angle (`6× 120°`), never one per corner. An octagon with
//!   unequal sides (FTC-08's and STC-08's chamfered rectangle) takes its chamfers' callout, or
//!   its `8× 135°` under the `angle` choice.
//! - Two mirror-equal corners of one profile that keep their angles (CTC-04's waist) get one
//!   counted angle, `2× θ°`, drawn at whichever of them has a clear place.
//! - IDs follow the notches': `d:envelope/profile_angle.angle.f<face>_e<i>_e<j>` per corner,
//!   `d:envelope/profile_angle.angle.f<face>` for a polygon's counted angle and
//!   `d:envelope/profile_location_<axis>.length.f<face>_<n>` for a completing station
//!   (`…f<face>_<n>_<m>` for a counted pair); `d:envelope/profile_chamfer.length.f<face>_e<i>` for
//!   a chamfer callout, named by its first cut's oblique edge.
//! - Overrides: `dimensions/<id>` shows (`{}`, as a reference when linearly defined) or hides
//!   (`omit`) a corner's angle; `faces/face:<n>/profile_corners` chooses `angle` (every corner,
//!   reference where linearly defined) or `linear` (stations only, a regular polygon too). Naming
//!   either corner of a chamfer in `dimensions` keeps that cut out of the chamfer callout.
//!
//! The angles are placed one at a time ([`super::angular::place_profile`]), local to their corner
//! (arc and label within twelve label heights of it) and only where they are clear:
//! last of all, or before the callouts route when that states more corners and is no worse. One with no clear
//! place stays undrawn (`angular_dimension_dropped`) and lint still reports its corner missing;
//! it never moves another item, takes a fallback or asks for more paper.
use super::{Measurement, Shape, Want, end_on, fmt, want_id};
use crate::lint::profile_angles::{
    Corner, chamfer, principal_axes, requirements, undefined_stations,
};
use crate::model::{Analysis, Invalid, Spec};

pub const PARAMETER: &str = "profile_angle.angle";
/// A profile chamfer callout's role in its ID (`d:envelope/profile_chamfer.length.f<face>_e<i>`).
const CHAMFER: &str = "profile_chamfer.length";

/// How a profile's corners are defined (`faces/<face>/profile_corners`).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum CornerMode {
    /// No angle where the linear dimensions already fix a corner; else a cut's missing
    /// stations, another corner's angle, a regular polygon's one counted angle.
    #[default]
    Auto,
    /// Every corner's angle; a reference where linear dimensions already fix it.
    Angle,
    /// Locate the missing stations with linear dimensions; no angles, not even a polygon's.
    Linear,
}

impl CornerMode {
    pub fn is_auto(&self) -> bool {
        *self == CornerMode::Auto
    }
}

/// A corner's support stamp (`f37_e124_e276`): its face and edges, as the notch ids name theirs.
fn support(c: &Corner) -> String {
    c.feature
        .strip_prefix("outer_profile:")
        .unwrap_or(&c.feature)
        .replace(':', "_")
}

/// Profile requirements belong to the part envelope, as the notches' do: `d:envelope/<role>.<support>`.
pub(crate) fn id(support: &str, role: &str) -> String {
    want_id("envelope", &format!("{role}.{support}"))
}

fn face_of(c: &Corner) -> usize {
    c.feature
        .strip_prefix("outer_profile:f")
        .and_then(|s| s.split(':').next())
        .and_then(|s| s.parse().ok())
        // Invariant: requirements() names every corner outer_profile:f<face>:e<i>:e<j>.
        .expect("outer-profile corner names its face")
}

/// The profile corner modes the spec authors, by face index.
fn modes(
    analysis: &Analysis,
    spec: &Spec,
) -> Result<std::collections::BTreeMap<usize, CornerMode>, Invalid> {
    let mut out = std::collections::BTreeMap::new();
    for (key, f) in &spec.faces {
        if f.profile_corners.is_auto() {
            continue;
        }
        for face in crate::spec_gdt::named_faces(analysis, key)? {
            out.insert(face, f.profile_corners);
        }
    }
    Ok(out)
}

/// Every station a linear dimension in *wants* reaches, rooted at the part's box and datums.
fn located(analysis: &Analysis, wants: &[Want]) -> impl Fn(usize, f64) -> bool + use<> {
    let spans: Vec<(usize, f64, f64)> = wants
        .iter()
        .filter(|w| !w.terms.first().is_some_and(|t| t.starts_with('(')))
        .filter_map(|w| match w.shape {
            Shape::Linear { a, b, axis, .. } => Some((axis, a[axis], b[axis])),
            _ => None,
        })
        .collect();
    let roots = crate::lint::location::roots(analysis);
    move |k, v| crate::lint::profile_angles::rooted_at(&roots[k], &spans, k, v)
}

fn corner_angle(c: &Corner) -> super::angular::Angle {
    super::angular::Angle {
        vertex: c.vertex,
        first: c.ends[0],
        second: c.ends[1],
    }
}

/// Whether corners *a* and *b* of one profile are mirror images across a line parallel to one
/// of the view's axes: equal angles, mirrored vertices and mirrored edge directions.
fn mirrored(a: &Corner, b: &Corner) -> bool {
    if a.axis != b.axis || (a.degrees() - b.degrees()).abs() > 1e-6 {
        return false;
    }
    let plane: Vec<usize> = (0..3).filter(|&k| k != a.axis).collect();
    let directions = |c: &Corner| {
        c.ends.map(|e| {
            let d = [0, 1, 2].map(|k| e[k] - c.vertex[k]);
            let n = (d[0] * d[0] + d[1] * d[1] + d[2] * d[2]).sqrt();
            d.map(|x| x / n)
        })
    };
    let (da, db) = (directions(a), directions(b));
    plane.iter().any(|&k| {
        let other = plane[usize::from(plane[0] == k)];
        let flip = |d: [f64; 3]| {
            let mut d = d;
            d[k] = -d[k];
            d
        };
        let same = |p: [f64; 3], q: [f64; 3]| (0..3).all(|j| (p[j] - q[j]).abs() < 1e-6);
        (a.vertex[other] - b.vertex[other]).abs() < 1e-4
            && (a.vertex[k] - b.vertex[k]).abs() > 1e-3
            && ((same(flip(da[0]), db[0]) && same(flip(da[1]), db[1]))
                || (same(flip(da[0]), db[1]) && same(flip(da[1]), db[0])))
    })
}

/// Whether *c* is one end of a cut across a corner of principal edges (l-plate-5-holes' 45°
/// cut, a bracket's corner chamfers, a chamfered rectangle's octagon): one of its edges is
/// principal, and the corner of *corners* at the other end of its oblique edge also meets a
/// principal edge.
fn cut(c: &Corner, corners: &[&Corner]) -> bool {
    let principal = c.principal_edges();
    if principal[0] == principal[1] {
        return false;
    }
    let oblique = c.edge_ids()[usize::from(principal[0])];
    corners.iter().any(|d| {
        d.feature != c.feature && {
            let (ids, p) = (d.edge_ids(), d.principal_edges());
            (ids[0] == oblique && p[1]) || (ids[1] == oblique && p[0])
        }
    })
}

fn angle_want(id: String, corners: &[&Corner], reference: bool, basis: &str) -> Want {
    let c = corners[0];
    // atan2 differs in its last bits between native and WASM libm; the plan's value must not.
    let value = (c.degrees() * 1e9).round() / 1e9;
    let mut text = format!("{}°", fmt(value));
    if corners.len() > 1 {
        text = format!("{}× {text}", corners.len());
    }
    if reference {
        text = format!("({text})");
    }
    Want {
        units: Default::default(),
        id,
        views: vec![end_on(c.axis)],
        value,
        terms: vec![text],
        measurements: corners
            .iter()
            .map(|c| Measurement {
                feature: c.feature.clone(),
                parameter: PARAMETER.into(),
                value,
            })
            .collect(),
        // Witnesses at the far ends of the corner's straight edges: their lengths bound how far
        // the arc may reach (a rounded corner's tangent points would bound it too tightly).
        shape: Shape::Angular {
            angle: corner_angle(c),
            alternates: corners[1..].iter().map(|c| corner_angle(c)).collect(),
        },
        turning: None,
        basis: basis.into(),
    }
}

/// The equal corners of a closed all-line profile when every corner is issued: one counted angle.
/// By default its sides must be equal too (a regular polygon); under the `angle` choice
/// (*equal_sides* false) equal corners suffice.
fn polygon<'a>(
    analysis: &Analysis,
    face: usize,
    corners: &[&'a Corner],
    equal_sides: bool,
) -> Option<Vec<&'a Corner>> {
    let source = analysis.outer_profiles.iter().find(|s| s.face == face)?;
    let n = source.profile.supports.len();
    let lines = source
        .profile
        .supports
        .iter()
        .all(|s| matches!(s, crate::lint::profile_angles::Support::Line { .. }));
    let first = corners.first()?.degrees();
    let side = |s: &crate::lint::profile_angles::Support| match s {
        crate::lint::profile_angles::Support::Line { start, end } => (0..3)
            .map(|k| (end[k] - start[k]).powi(2))
            .sum::<f64>()
            .sqrt(),
        crate::lint::profile_angles::Support::Arc { .. } => 0.,
    };
    let length = source.profile.supports.first().map(side)?;
    (lines
        && (!equal_sides
            || source
                .profile
                .supports
                .iter()
                .all(|s| (side(s) - length).abs() < 1e-4))
        && n >= 3
        && corners.len() == n
        && corners
            .iter()
            .all(|c| !c.virtual_vertex && (c.degrees() - first).abs() < 1e-6))
    .then(|| corners.to_vec())
}

/// A completing station: its axis, its witness on the box side, its witness on the corner, its
/// value and the axis of the view that shows it.
type Leg = (usize, [f64; 3], [f64; 3], f64, usize);

/// Adds the profile-corner items to *wants* (see the module's notes).
pub(super) fn apply(
    analysis: &Analysis,
    spec: &Spec,
    wants: &mut Vec<Want>,
) -> Result<(), Invalid> {
    let fmt = |v| crate::dims::fmt_in(v, spec.units);
    let corners = requirements(analysis);
    if corners.is_empty() {
        return Ok(());
    }
    let modes = modes(analysis, spec)?;
    let located = located(analysis, wants);
    let shown = |id: &str| spec.dimensions.get(id).is_some_and(|d| !d.omit);
    let mut faces: Vec<usize> = corners.iter().map(face_of).collect();
    faces.dedup();
    let mut added = Vec::new();
    for face in faces {
        let mode = modes.get(&face).copied().unwrap_or_default();
        let mine: Vec<&Corner> = corners.iter().filter(|c| face_of(c) == face).collect();
        let missing: Vec<Vec<(usize, f64)>> = mine
            .iter()
            .map(|c| undefined_stations(c, &located))
            .collect();
        if mode != CornerMode::Linear
            && let Some(group) = polygon(analysis, face, &mine, mode == CornerMode::Auto)
        {
            let id = id(&format!("f{face}"), PARAMETER);
            let defined = missing.iter().all(Vec::is_empty);
            if !defined || mode == CornerMode::Angle || shown(&id) {
                added.push(angle_want(
                    id,
                    &group,
                    defined,
                    "the equal corners of a closed polygonal outer profile, one counted angle in the view that shows them true",
                ));
            }
            continue;
        }
        // Two mirror-equal corners that keep their angles (CTC-04's waist) take one counted
        // angle, `2× θ°`, drawn at whichever of them has a clear place. Only an unambiguous
        // pair: no third corner of that angle on the profile.
        let mut paired = vec![false; mine.len()];
        let angled: Vec<bool> = mine.iter().map(|c| !cut(c, &mine)).collect();
        if mode == CornerMode::Auto {
            for x in 0..mine.len() {
                for y in x + 1..mine.len() {
                    let alone = mine.iter().enumerate().all(|(z, c)| {
                        z == x || z == y || (c.degrees() - mine[x].degrees()).abs() > 1e-6
                    });
                    if paired[x]
                        || paired[y]
                        || !angled[x]
                        || !angled[y]
                        || !alone
                        || !mirrored(mine[x], mine[y])
                        || missing[x].is_empty()
                        || missing[y].is_empty()
                    {
                        continue;
                    }
                    let singles = [mine[x], mine[y]].map(|c| id(&support(c), PARAMETER));
                    if singles.iter().any(|i| spec.dimensions.contains_key(i)) {
                        continue;
                    }
                    paired[x] = true;
                    paired[y] = true;
                    let support_b = support(mine[y]);
                    let pair = format!(
                        "{}_{}",
                        support(mine[x]),
                        support_b
                            .split_once('_')
                            .map_or(support_b.as_str(), |(_, e)| e)
                    );
                    added.push(angle_want(
                        id(&pair, PARAMETER),
                        &[mine[x], mine[y]],
                        false,
                        "two mirror-equal outer-profile corners its linear dimensions do not fix, one counted angle in the view that shows them true",
                    ));
                }
            }
        }
        let mut stations: Vec<(usize, f64, [f64; 3], usize)> = Vec::new();
        let mut station = |k: usize, v: f64, at: [f64; 3], axis: usize| {
            if !stations.iter().any(|s| s.0 == k && (s.1 - v).abs() < 1e-4) {
                stations.push((k, v, at, axis));
            }
        };
        // A 45° chamfer across a right corner (#217, maintainer 2026-10-28) is called out by its
        // size, `C<leg>` as an edge chamfer is, counted when equal (`4× C21.075`), and its
        // principal edges' offsets are located; its legs need no stations. Default only, and not
        // where the Spec names either corner's angle.
        if mode == CornerMode::Auto {
            let mut cuts: Vec<(f64, Vec<[usize; 2]>)> = Vec::new();
            for (n, c) in mine.iter().enumerate() {
                let Some((other, leg)) = chamfer(c, mine.iter().copied()) else {
                    continue;
                };
                let Some(o) = mine.iter().position(|d| d.feature == other.feature) else {
                    continue;
                };
                if paired[n]
                    || paired[o]
                    || (missing[n].is_empty() && missing[o].is_empty())
                    || [c, other]
                        .iter()
                        .any(|d| spec.dimensions.contains_key(&id(&support(d), PARAMETER)))
                {
                    continue;
                }
                paired[n] = true;
                paired[o] = true;
                match cuts.iter_mut().find(|(l, _)| (l - leg).abs() < 1e-4) {
                    Some((_, members)) => members.push([n, o]),
                    None => cuts.push((leg, vec![[n, o]])),
                }
            }
            for (leg, members) in cuts {
                let first = mine[members[0][0]];
                let oblique = first.edge_ids()[usize::from(first.principal_edges()[0])];
                for &n in members.iter().flatten() {
                    let c = mine[n];
                    if let Some((_, m)) = principal_axes(c)
                        && !located(m, c.vertex[m])
                    {
                        station(m, c.vertex[m], c.vertex, c.axis);
                    }
                }
                let mut terms = Vec::new();
                if members.len() > 1 {
                    terms.push(format!("{}×", members.len()));
                }
                terms.push(format!("C{}", fmt(leg)));
                // atan2 differs in its last bits between native and WASM libm; the plan's value
                // must not.
                let angle = |c: &Corner| (c.degrees() * 1e9).round() / 1e9;
                added.push(Want { units: spec.units,
                    id: id(&format!("f{face}_{oblique}"), CHAMFER),
                    views: vec![end_on(first.axis)],
                    value: leg,
                    terms,
                    measurements: members
                        .iter()
                        .flat_map(|&[n, o]| {
                            [mine[n], mine[o]].map(|c| Measurement {
                                feature: c.feature.clone(),
                                parameter: PARAMETER.into(),
                                value: angle(c),
                            })
                        })
                        .collect(),
                    basis: if members.len() > 1 {
                        "equal 45° chamfers across outer-profile corners, called out once by their size and counted, as edge chamfers are, in the view that shows them true"
                    } else {
                        "a 45° chamfer across an outer-profile corner, called out by its size as an edge chamfer is, in the view that shows it true"
                    }
                    .into(),
                    shape: Shape::Callout {
                        sites: members
                            .iter()
                            .map(|&[n, o]| {
                                let (p, q) = (mine[n].vertex, mine[o].vertex);
                                ([0, 1, 2].map(|k| (p[k] + q[k]) / 2.), 0.)
                            })
                            .collect(),
                        arcs: Vec::new(),
                    },
                    turning: None,
                });
            }
        }
        for (n, (c, miss)) in mine.iter().zip(&missing).enumerate() {
            if paired[n] {
                continue;
            }
            let id = id(&support(c), PARAMETER);
            // A cut corner is located by its legs by default (#217): its angle alone fixes the
            // cut's direction, not where it lies. Any other corner keeps its angle (lint reports
            // it while its edges stay unlocated).
            let linear = mode == CornerMode::Linear || (mode == CornerMode::Auto && cut(c, &mine));
            if linear && !miss.is_empty() && !shown(&id) {
                for &(k, v) in miss {
                    let at = [c.vertex, c.ends[0], c.ends[1]]
                        .into_iter()
                        .find(|p| (p[k] - v).abs() < 1e-4)
                        .unwrap_or(c.vertex);
                    station(k, v, at, c.axis);
                }
                // An angle the Spec omits stays a known item, omitted, beside the stations.
                if spec.dimensions.contains_key(&id) {
                    added.push(angle_want(
                        id,
                        &[c],
                        false,
                        "a non-right convex outer-profile corner's angle the Spec asks for, in the view that shows it true",
                    ));
                }
                continue;
            }
            let defined = miss.is_empty();
            if !defined || mode == CornerMode::Angle || shown(&id) {
                added.push(angle_want(
                    id,
                    &[c],
                    defined,
                    "a non-right convex outer-profile corner its linear dimensions do not fix, in the view that shows it true",
                ));
            }
        }
        stations.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.total_cmp(&b.1)));
        // Each station from the nearer side of the part's box: the shorter leg, whose extension
        // line crosses less of the part.
        let legs: Vec<Leg> = stations
            .into_iter()
            .map(|(k, v, at, axis)| {
                let mut a = at;
                let [lo, hi] = [analysis.envelope[0][k], analysis.envelope[1][k]];
                a[k] = if v - lo <= hi - v { lo } else { hi };
                (k, a, at, (v - a[k]).abs(), axis)
            })
            .collect();
        let single = |n: usize, k: usize| {
            id(
                &format!("f{face}_{n}"),
                &format!("profile_location_{}.length", super::AXES[k]),
            )
        };
        // Two equal stations from opposite sides of the box (a symmetric octagon's legs, a
        // bracket's two corner chamfers) are stated once, `2× v`, as the mirror-equal angles
        // are. Default only, and not where the Spec names either station.
        let mut mirror: Vec<Option<usize>> = vec![None; legs.len()];
        if mode == CornerMode::Auto {
            for x in 0..legs.len() {
                for y in x + 1..legs.len() {
                    let (p, q) = (&legs[x], &legs[y]);
                    if mirror[x].is_some()
                        || mirror[y].is_some()
                        || p.0 != q.0
                        || p.4 != q.4
                        || (p.1[p.0] - q.1[q.0]).abs() < 1e-6
                        || (p.3 - q.3).abs() > 1e-4
                        || [single(x, p.0), single(y, q.0)]
                            .iter()
                            .any(|i| spec.dimensions.contains_key(i))
                    {
                        continue;
                    }
                    mirror[x] = Some(y);
                    mirror[y] = Some(x);
                }
            }
        }
        for (n, &(k, a, at, value, axis)) in legs.iter().enumerate() {
            if value < 1e-6 || mirror[n].is_some_and(|m| m < n) {
                continue;
            }
            // Not `location.length.*`: that names hole locations, whose legibility the sheet
            // choice counts.
            let parameter = format!("profile_location_{}.length", super::AXES[k]);
            let mut measurements = vec![Measurement {
                feature: format!("outer_profile:f{face}:s{n}"),
                parameter: parameter.clone(),
                value,
            }];
            let (mut want_id, mut text) = (single(n, k), fmt(value));
            if let Some(m) = mirror[n] {
                measurements.push(Measurement {
                    feature: format!("outer_profile:f{face}:s{m}"),
                    parameter: parameter.clone(),
                    value,
                });
                want_id = id(&format!("f{face}_{n}_{m}"), &parameter);
                text = format!("2× {text}");
            }
            // Witnesses in ascending order along the axis, the box side flagged as an edge.
            let ([a, b], [a_edge, b_edge]) = if a[k] <= at[k] {
                ([a, at], [true, false])
            } else {
                ([at, a], [false, true])
            };
            added.push(Want { units: spec.units,
                id: want_id,
                views: vec![end_on(axis)],
                value,
                terms: vec![text],
                measurements,
                basis: if mirror[n].is_some() {
                    "two equal outer-profile corner stations from opposite sides of the part's box, one counted dimension completing corners their linear dimensions do not fix"
                } else {
                    "an outer-profile corner station located from the nearer side of the part's box, completing a corner its linear dimensions do not fix"
                }
                .into(),
                shape: Shape::Linear {
                    a,
                    b,
                    axis: k,
                    a_edge,
                    b_edge,
                    order: 1,
                },
                turning: None,
            });
        }
    }
    wants.extend(added);
    Ok(())
}

/// Whether *w* is a profile-corner angle, placed last and dropped when it cannot be clear.
pub(super) fn is_profile_angle(w: &Want) -> bool {
    matches!(w.shape, Shape::Angular { .. })
        && w.measurements.iter().any(|m| m.parameter == PARAMETER)
}
