//! What the sheet must make room for, read from the Analysis before the views are placed:
//! draftwright's sheet-demand planning (`analysis._plan_sheet_demand`,
//! `compose._compose_anno_boxes`, `builder._automatic_turned_principals` and
//! `planner.plan_sections`) as far as the bridged features carry it.
//!
//! - **Turned parts** keep their profile and end views and drop the view that repeats the profile
//!   (draftwright's `redundant_radial_view_removed`), unless a feature needs that view end-on.
//! - **Step levels**: a turned part's shoulders along a vertical axis, or the part's horizontal
//!   face levels; they size the height ladder right of the front view.
//! - **Annotation bands** ([`Strips`]): the height ladder, the X-location tiers above the plan,
//!   the balloon ring, and the rows of Y-hole leaders below the front; each band then widened to
//!   hold the lanes the port's own dimensions stack in ([`Demand::lanes`], which the plan sets),
//!   the side view's bands from those lanes alone; and, on a part with AP242 PMI, room for its
//!   feature control frames, datum feature symbols and labels at the scale chosen
//!   ([`Demand::gdt`], [`Strips::reserve_gdt`]), which draftwright does not reserve.
//! - **Section A–A** through the densest row of Z-axis holes whose profile no standard view shows.
//!
//! Not ported here: the hole-callout band (it is measured from the callout labels, which come with
//! the hole callouts), authored dimensions, pads, seat offsets, through-step legs and angular
//! reservations, the IR's ownership rules (a boss absorbed by a groove still counts here), the
//! rotational-OD fallback for a turning axis, and authored section planes.

use serde_json::Value;

use crate::bridge::holes;
use crate::layout::{self, Pick, Request, Scaled, Strips, dim_pad};
use crate::model::Analysis;

/// A bridged hole in the part's frame.
#[derive(Clone, Debug, PartialEq)]
pub struct Hole {
    /// Its location as quiddity reports it, in the part's frame.
    pub at: [f64; 3],
    /// Its axis, when it lies along x, y or z (0, 1, 2).
    pub axis: Option<usize>,
    pub diameter: f64,
    pub through: bool,
    pub cbore: bool,
    pub spotface: bool,
    /// Its machining requirement ([`holes::Hole::requirement`]: signed direction, sizes,
    /// bottom, counterbore, spotface, countersink): holes with the same requirement are one
    /// feature, called out once.
    pub spec: Vec<i64>,
    /// The recognised pattern it is called out with (`Analysis.recognition.patterns`), if that
    /// pattern is established as one requirement ([`holes::established_with`], given the
    /// [`holes::bosses`]).
    pub pattern: Option<String>,
}

/// Where section A–A cuts, and why.
#[derive(Clone, Debug, PartialEq)]
pub struct Section {
    /// The plane y = `cut_y` (part frame), seen from the front.
    pub cut_y: f64,
    /// The holes on that row whose profile it shows (or the internal physical details).
    pub holes: usize,
    /// Several unlike internal details, rather than a qualifying bore row.
    pub internal_detail: bool,
}

/// What the sheet must make room for.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Demand {
    /// The axis (0, 1, 2) of the one turned profile, if the part has exactly one.
    pub turning_axis: Option<usize>,
    /// The number of turned profiles.
    pub profiles: usize,
    /// A whole turned profile uses a common inspection end.
    pub inspection_baseline: bool,
    /// The step levels (z, part frame) the height ladder may dimension.
    pub step_zs: Vec<f64>,
    pub holes: Vec<Hole>,
    /// Bosses along z with a height: each takes a slot on the height ladder.
    pub z_boss_heights: usize,
    /// A boss or turned step along y: its radial fan needs room beside the height slot.
    pub y_radial: bool,
    pub section: Option<Section>,
    /// The lanes the port's own dimensions take beside each view (set from `dims::lanes` by the
    /// plan; none from [`read`]): the bands are widened to hold them ([`Strips::reserve`]).
    pub lanes: layout::Lanes,
    /// Authored side-only callout room, including the final printed label width.
    pub callout_bands: Vec<layout::CalloutBand>,
    /// The part's feature control frames, datum feature symbols and labels as the layout
    /// reserves for them ([`Strips::reserve_gdt`]): with every principal view shown, and with
    /// the side view not shown (where a datum on a y-normal plane moves to the plan).
    pub gdt: [Vec<layout::Gdt>; 2],
    /// Whether the GD&T's room starts beyond its strips' full lane counts
    /// ([`Strips::reserve_gdt`]'s *beyond_lanes*): unset from [`read`]; the plan's second try
    /// sets it (`model::plan_escalated`).
    pub gdt_beyond_lanes: bool,
}

/// The part frame's point for quiddity's local *l*.
fn to_part(frame: &[[f64; 3]; 4], l: [f64; 3]) -> [f64; 3] {
    [0, 1, 2].map(|k| frame[0][k] + (0..3).map(|j| l[j] * frame[j + 1][k]).sum::<f64>())
}

/// The part frame's direction for quiddity's local *d*.
fn dir_to_part(frame: &[[f64; 3]; 4], d: [f64; 3]) -> [f64; 3] {
    [0, 1, 2].map(|k| (0..3).map(|j| d[j] * frame[j + 1][k]).sum::<f64>())
}

fn vec3(v: &Value) -> Option<[f64; 3]> {
    let a = v.as_array()?;
    Some([
        a.first()?.as_f64()?,
        a.get(1)?.as_f64()?,
        a.get(2)?.as_f64()?,
    ])
}

/// A local axis given as a letter or a vector.
fn local_axis(v: &Value) -> Option<[f64; 3]> {
    match v.as_str() {
        Some("x") => Some([1.0, 0.0, 0.0]),
        Some("y") => Some([0.0, 1.0, 0.0]),
        Some("z") => Some([0.0, 0.0, 1.0]),
        Some(_) => None,
        None => vec3(v),
    }
}

/// The end-exclusion of a span (quiddity's `bounded_end_margin`, draftwright's 0.6 mm).
const END_MARGIN: f64 = 0.6;

/// Whether the part is one short disc on z (#220: a bearing ring, a coupling half), which reads
/// as a part turned about z: it has no turned step, and a cylindrical boss along z is as wide as
/// the part's box across z, centred in it, and no taller than its diameter.
fn disc(analysis: &Analysis) -> bool {
    let Some(frame) = analysis.recognition.as_ref().map(|r| r.frame) else {
        return false;
    };
    let [lo, hi] = analysis.envelope;
    !analysis.features.iter().any(|f| f.kind == "turned_step")
        && analysis
            .features
            .iter()
            .filter(|f| f.kind == "boss")
            .any(|f| {
                let p = &f.parameters;
                let (Some(axis), Some(at), Some(d), Some(h)) = (
                    p.get("axis").and_then(local_axis),
                    p.get("location").and_then(vec3),
                    p.get("diameter").and_then(Value::as_f64),
                    p.get("height").and_then(Value::as_f64),
                ) else {
                    return false;
                };
                let at = to_part(&frame, at);
                let tol = 1e-3 * d.max(1.0);
                holes::principal(dir_to_part(&frame, axis)) == Some(2)
                    && 0.0 < h
                    && h <= d
                    && (0..2).all(|j| {
                        ((hi[j] - lo[j]) - d).abs() <= tol
                            && (at[j] - 0.5 * (lo[j] + hi[j])).abs() <= tol
                    })
            })
}

/// Reads what the sheet must make room for from *analysis*.
pub fn read(analysis: &Analysis) -> Demand {
    crate::planning::memo(analysis, "demand", Vec::new(), || read_uncached(analysis))
}

fn read_uncached(analysis: &Analysis) -> Demand {
    let mut d = Demand::default();
    let [lo, hi] = analysis.envelope;
    let frame = analysis.recognition.as_ref().map(|r| r.frame);

    // Turned profiles: their steps' shoulders, and the profile's axis in the part's frame.
    let mut profiles: Vec<(String, Option<usize>, Vec<f64>)> = Vec::new();
    for f in analysis.features.iter().filter(|f| f.kind == "turned_step") {
        let (Some(frame), Some(axis), Some(lo_t), Some(hi_t)) = (
            frame,
            f.parameters.get("axis").and_then(local_axis),
            f.parameters.get("lo").and_then(Value::as_f64),
            f.parameters.get("hi").and_then(Value::as_f64),
        ) else {
            continue;
        };
        let profile = f.parameters.get("profile");
        let key = profile
            .and_then(|p| p.get("body_key"))
            .map_or_else(String::new, Value::to_string);
        let origin = profile
            .and_then(|p| p.get("axis_origin"))
            .and_then(vec3)
            .unwrap_or([0.0; 3]);
        let k = (0..3).find(|&k| axis[k] != 0.0).unwrap_or(0);
        let world = |t: f64| {
            let mut l = origin;
            l[k] = t;
            to_part(&frame, l)
        };
        let axis = holes::principal(dir_to_part(&frame, axis));
        let zs = [world(lo_t)[2], world(hi_t)[2]];
        match profiles.iter_mut().find(|p| p.0 == key) {
            Some(p) => p.2.extend(zs),
            None => profiles.push((key, axis, zs.to_vec())),
        }
    }
    if let Some((axis, _, _)) = crate::bridge::turned_body(analysis)
        && profiles.is_empty()
    {
        profiles.push(("turned_body".into(), Some(axis), Vec::new()));
    }
    d.inspection_baseline = crate::dims::inspection_datum(analysis).is_some();
    d.profiles = profiles.len();
    if profiles.len() == 1 {
        d.turning_axis = profiles[0].1;
    } else if profiles.is_empty() && disc(analysis) {
        d.turning_axis = Some(2);
    }
    let inside = |z: f64| lo[2] + END_MARGIN < z && z < hi[2] - END_MARGIN;
    d.step_zs = if !profiles.is_empty() && profiles.iter().all(|p| p.1 == Some(2)) {
        profiles.iter().flat_map(|p| p.2.iter().copied()).collect()
    } else {
        analysis.levels.clone()
    };
    d.step_zs.retain(|z| inside(*z));
    d.step_zs.sort_by(f64::total_cmp);
    d.step_zs.dedup_by(|a, b| (*a - *b).abs() < 1e-9);

    // The holes that can be read (the dimensions report the others), each in the recognised
    // pattern that is established as one requirement ([`holes::established_with`], a boss on a
    // small bolt circle's centre counting, as the dimensions and lint group them; the first
    // pattern to claim a hole keeps it), else loose.
    let read = holes::read(analysis);
    let bosses = holes::bosses(analysis);
    let readable: Vec<&holes::Hole> = read.iter().filter_map(|(_, h)| h.as_ref().ok()).collect();
    let mut pattern: Vec<Option<String>> = vec![None; readable.len()];
    let patterns = analysis.recognition.as_ref().map(|r| r.patterns.as_slice());
    for p in patterns.unwrap_or_default() {
        if let Ok(members) = holes::established_with(p, &readable, &bosses)
            && members.iter().all(|&m| pattern[m].is_none())
        {
            for m in members {
                pattern[m] = Some(p.id.clone());
            }
        }
    }
    d.holes = readable
        .iter()
        .zip(pattern)
        .map(|(h, p)| hole(h, p))
        .collect();
    // Polygonal stock's length takes the overall height's slot; other axial heights add one.
    let mut stock = false;
    for f in &analysis.features {
        match f.kind.as_str() {
            "boss" | "polygonal_boss" | "polygonal_stock" => {
                let axis = f.fingerprint.axis.and_then(holes::principal);
                let tall = ["height", "length", "axial_length"]
                    .iter()
                    .any(|k| f.fingerprint.sizes.contains_key(*k));
                if axis == Some(2) && tall {
                    d.z_boss_heights += 1;
                    stock |= f.kind == "polygonal_stock";
                }
                if axis == Some(1) && f.kind == "boss" {
                    d.y_radial = true;
                }
            }
            "turned_step" if f.fingerprint.axis.and_then(holes::principal) == Some(1) => {
                d.y_radial = true;
            }
            _ => {}
        }
    }
    d.z_boss_heights -= usize::from(stock);
    // A turned part's concentric through bores are dimensioned by its centre-line, so they drive
    // no section (draftwright's `feature_hole_keys`).
    let centre = [0.5 * (lo[0] + hi[0]), 0.5 * (lo[1] + hi[1])];
    let keyed: Vec<Hole> = d
        .holes
        .iter()
        .filter(|h| {
            !(d.turning_axis == Some(2)
                && h.axis == Some(2)
                && h.through
                && (h.at[0] - centre[0]).hypot(h.at[1] - centre[1]) <= 0.5)
        })
        .cloned()
        .collect();
    d.section =
        section(&keyed, centre[1]).or_else(|| internal_section(analysis, &d.holes, centre[1]));
    d.gdt = [
        crate::dims::gdt::demand(analysis, &|_| true),
        crate::dims::gdt::demand(analysis, &|v| v != "side"),
    ];
    d
}

fn hole(h: &holes::Hole, pattern: Option<String>) -> Hole {
    Hole {
        at: h.at,
        axis: h.principal.map(|(k, _)| k),
        diameter: h.diameter,
        through: h.through,
        cbore: h.counterbore.is_some(),
        spotface: h.spotface.is_some(),
        spec: h.requirement(),
        pattern,
    }
}

/// Draftwright's section rule (`plan_sections`): a Z-axis hole with a counterbore, a spotface or
/// a bottom has a profile no standard view shows; the plane cuts the densest row of them
/// (within 0.5 mm), the tie going to the row nearest the part's centre.
fn section(holes: &[Hole], centre_y: f64) -> Option<Section> {
    section_rows(holes, centre_y, false)
}

/// An explicit request may show a plain through bore too. The existing row ranking
/// still chooses a deterministic plane, without changing automatic section policy.
pub(crate) fn explicit_section(holes: &[Hole], centre_y: f64) -> Option<Section> {
    section_rows(holes, centre_y, true)
}

fn section_rows(holes: &[Hole], centre_y: f64, explicit: bool) -> Option<Section> {
    let ys: Vec<f64> = holes
        .iter()
        .filter(|h| h.axis == Some(2) && (explicit || h.cbore || h.spotface || !h.through))
        .map(|h| h.at[1])
        .collect();
    let mut rows: Vec<f64> = ys.iter().map(|y| (y * 10.0).round() / 10.0).collect();
    rows.sort_by(f64::total_cmp);
    rows.dedup();
    let count = |v: f64| ys.iter().filter(|y| (*y - v).abs() <= 0.5).count();
    rows.into_iter()
        .map(|v| (count(v), v))
        .max_by(|a, b| {
            a.0.cmp(&b.0)
                .then((b.1 - centre_y).abs().total_cmp(&(a.1 - centre_y).abs()))
                .then(b.1.total_cmp(&a.1))
        })
        .map(|(holes, cut_y)| Section {
            cut_y,
            holes,
            internal_detail: false,
        })
}

/// Python's internal_section_rows: physical members, at least three of two kinds,
/// along a Y-normal station; the bore-row rule has priority.
fn internal_section(analysis: &Analysis, holes: &[Hole], centre_y: f64) -> Option<Section> {
    let mut sites = Vec::new();
    for hex in crate::lint::pockets::hexes(analysis) {
        if let Ok((2, at)) = hex.placed {
            sites.push((at[1], "hex_pocket"));
        }
    }
    for (_, seat) in crate::lint::channels::seats(analysis) {
        if let Ok(seat) = seat
            && seat.run_axis == 1
        {
            sites.push((seat.centre[1], "circular_channel"));
        }
    }
    sites.extend(
        holes
            .iter()
            .filter(|h| h.axis == Some(0) && !h.through)
            .map(|h| (h.at[1], "transverse_blind_hole")),
    );
    internal_station(&sites, centre_y)
}

fn internal_station(sites: &[(f64, &str)], centre_y: f64) -> Option<Section> {
    let mut rows: Vec<f64> = sites
        .iter()
        // Decimal formatting rounds the actual binary value with ties to even,
        // like Python round(y, 1), without first rounding y * 10.
        .map(|(y, _)| {
            format!("{y:.1}")
                .parse::<f64>()
                // Invariant: The string was formatted from an f64 locally and is a Rust float literal.
                .expect("a formatted coordinate parses")
        })
        .collect();
    rows.sort_by(f64::total_cmp);
    rows.dedup();
    rows.into_iter()
        .filter_map(|cut_y| {
            let kinds: Vec<_> = sites
                .iter()
                .filter(|(y, _)| (y - cut_y).abs() <= 0.5)
                .map(|(_, kind)| *kind)
                .collect();
            (kinds.len() >= 3 && kinds.iter().any(|k| *k != kinds[0]))
                .then_some((kinds.len(), cut_y))
        })
        .max_by(|a, b| {
            a.0.cmp(&b.0)
                .then((b.1 - centre_y).abs().total_cmp(&(a.1 - centre_y).abs()))
                .then(b.1.total_cmp(&a.1))
        })
        .map(|(holes, cut_y)| Section {
            cut_y,
            holes,
            internal_detail: true,
        })
}

/// The principal views a turned part keeps (draftwright's `_automatic_turned_principals`): the
/// profile and the end view for its axis; a short disc on z is one too ([`disc`]). The spec
/// keeps the dropped view by naming it (`views/<name>`). None when the part is not turned about
/// one axis, or when a hole needs the dropped view end-on (it would have nowhere to be called
/// out: the veto draftwright reaches by planning the reduced set). Returns the kept views, the
/// dropped one and the reason.
pub fn turned_views(d: &Demand) -> Option<([&'static str; 2], &'static str, String)> {
    let axis = d.turning_axis?;
    let (kept, dropped, end_on) = match axis {
        0 | 1 => (["front", "side"], "plan", 2),
        _ => (["front", "plan"], "side", 0),
    };
    let letter = ["x", "y", "z"][axis];
    if d.holes.iter().any(|h| h.axis == Some(end_on)) {
        return None;
    }
    Some((
        kept,
        dropped,
        format!(
            "the part is turned about {letter} (one profile), so it keeps its profile and end views and leaves out the {dropped} view, which would repeat the profile (no feature needs it end-on)"
        ),
    ))
}

/// The holes as draftwright's hole features: an established pattern is one feature, and loose
/// holes of one machining requirement another (draftwright's machining-spec groups). Returns,
/// per feature, its axis, its members' locations and whether it is a pattern.
fn hole_features(holes: &[Hole]) -> Vec<(Option<usize>, Vec<[f64; 3]>, bool)> {
    type Key = (Option<usize>, Vec<i64>, Option<String>);
    let mut grouped: Vec<(Key, Vec<[f64; 3]>)> = Vec::new();
    for h in holes {
        let key = (h.axis, h.spec.clone(), h.pattern.clone());
        match grouped.iter_mut().find(|g| g.0 == key) {
            Some(g) => g.1.push(h.at),
            None => grouped.push((key, vec![h.at])),
        }
    }
    grouped
        .into_iter()
        .map(|(k, m)| (k.0, m, k.2.is_some()))
        .collect()
}

/// Whether a pattern's members lie on a circle about their centroid, and are not the corners
/// of a rectangle (four holes on a rectangle are a grid to quiddity, read here the same way).
fn bolt_circle(members: &[[f64; 3]], c: [f64; 2]) -> bool {
    let r: Vec<f64> = members
        .iter()
        .map(|m| (m[0] - c[0]).hypot(m[1] - c[1]))
        .collect();
    let concyclic = members.len() >= 3 && r.iter().all(|x| (x - r[0]).abs() < 1e-3 && *x > 1e-3);
    // Four concyclic points are a rectangle's corners when the diagonals both pass the centre.
    let rectangle = members.len() == 4
        && members.iter().all(|a| {
            members.iter().any(|b| {
                ((a[0] + b[0]) * 0.5 - c[0]).abs() < 1e-3
                    && ((a[1] + b[1]) * 0.5 - c[1]).abs() < 1e-3
                    && (a[0] - b[0]).hypot(a[1] - b[1]) > 1e-3
            })
        });
    concyclic && !rectangle
}

/// The X reference a Z-axis hole feature's location dimension tiers off: a bolt circle's centre,
/// a pattern's first member (here its lowest x: quiddity's member order is not bridged), or each
/// loose hole.
fn x_refs(holes: &[Hole]) -> Vec<f64> {
    let mut xs = Vec::new();
    for (axis, members, pattern) in hole_features(holes) {
        if axis != Some(2) {
            continue;
        }
        if !pattern {
            xs.extend(members.iter().map(|m| m[0]));
            continue;
        }
        let n = members.len() as f64;
        let c = [0, 1].map(|k| members.iter().map(|m| m[k]).sum::<f64>() / n);
        let circle = bolt_circle(&members, c);
        let first = members
            .iter()
            .min_by(|a, b| a[0].total_cmp(&b[0]).then(a[1].total_cmp(&b[1])))
            // Invariant: The pattern reader refuses empty member lists.
            .expect("a pattern has members");
        xs.push(if circle { c[0] } else { first[0] });
    }
    xs
}

/// The bands the features need with *n_steps* steps on the height ladder (draftwright's
/// `_compose_anno_boxes` reduced to `StripDepths`), for the principal views *views*.
pub fn strips(d: &Demand, n_steps: usize, plan_shown: bool) -> Strips {
    strips_on(d, n_steps, plan_shown, layout::right_strip_depth)
}

/// [`strips`] with the height ladder's band measured by *ladder*.
fn strips_on(
    d: &Demand,
    n_steps: usize,
    plan_shown: bool,
    ladder: fn(usize, usize) -> f64,
) -> Strips {
    let mut right = ladder(n_steps, d.z_boss_heights);
    if d.y_radial {
        right = right.max(ladder(0, 0) + layout::RADIAL_REACH);
    }
    let mut xs = x_refs(&d.holes);
    xs.sort_by(f64::total_cmp);
    let mut distinct: Vec<f64> = Vec::new();
    for x in xs {
        if distinct.last().is_none_or(|l| (x - l).abs() > 0.5) {
            distinct.push(x);
        }
    }
    let features = hole_features(&d.holes);
    let front_rows = features.iter().filter(|f| f.0 == Some(1)).count();
    // Draftwright tabulates and balloons a plan of at least 16 holes not mostly in patterns.
    let loose: usize = features
        .iter()
        .filter(|f| f.0 == Some(2) && !f.2)
        .map(|f| f.1.len())
        .sum();
    let covered: usize = features
        .iter()
        .filter(|f| f.0 == Some(2) && f.2)
        .map(|f| f.1.len())
        .sum();
    let total = loose + covered;
    let balloon = total >= 16 && (covered as f64) < 0.8 * total as f64;
    let plan_work = d.holes.iter().any(|h| h.axis == Some(2)) || d.turning_axis != Some(2);
    let mut strips = Strips {
        right,
        left: dim_pad(),
        top: layout::plan_above_depth(distinct.len()),
        pv_halo: if balloon { layout::PLAN_HALO } else { 0.0 },
        front_hole_below: layout::front_hole_rows_depth(front_rows),
        plan_demand: plan_shown && plan_work,
        ..Strips::without_features()
    };
    strips.reserve(&d.lanes);
    if d.turning_axis == Some(2) && d.inspection_baseline {
        // A short baseline ordinate can keep its label inside using outside arrows.
        // Its datum-end arrow must still fit above the profile, before the plan band.
        strips.fv_top = strips.fv_top.max(crate::dims::ARROW + 1.0);
        // The inspection baseline occupies the right column. Keep a usable exterior
        // column for diameter and cross-hole leaders on the other silhouette.
        strips.left = strips.left.max(30.0);
    }
    strips.reserve_callouts(&d.callout_bands);
    strips
}

/// The steps legible at *scale* above the part's base *base*.
pub fn legible(d: &Demand, base: f64, scale: f64) -> usize {
    layout::legible_steps(&d.step_zs, base, scale).len()
}

/// The part's horizontal face levels (quiddity's `step_level_records`, read in the part's frame):
/// per solid, planar faces facing ±z clustered within 0.5 mm, each cluster with at least 1 % of
/// the solid's footprint in area, strictly inside the solid's height less its end margin.
pub fn face_levels(part: &haecceity::Part) -> Vec<f64> {
    use haecceity::geom::{Bounds, Surface};
    let scopes: Vec<Vec<usize>> = if part.solids.is_empty() {
        vec![(0..part.faces.len()).collect()]
    } else {
        part.solids.iter().map(|s| s.faces.clone()).collect()
    };
    let mut out = Vec::new();
    for faces in scopes {
        let mut b = Bounds::empty();
        for &f in &faces {
            b.merge(&part.face_bounds(f));
        }
        let span = b.max[2] - b.min[2];
        let margin = END_MARGIN.min(span.max(0.0) * 0.25);
        let threshold = 0.01 * (b.max[0] - b.min[0]) * (b.max[1] - b.min[1]);
        let mut levels: Vec<(f64, f64)> = faces
            .iter()
            .filter_map(|&f| match &part.faces[f].surface {
                Surface::Plane { frame } if frame.z[2].abs() > 0.99 => {
                    Some((frame.origin[2], part.face_mass(f).map_or(0.0, |m| m[0])))
                }
                _ => None,
            })
            .collect();
        levels.sort_by(|a, b| a.0.total_cmp(&b.0));
        let mut i = 0;
        while i < levels.len() {
            let z0 = levels[i].0;
            let mut area = 0.0;
            let mut j = i;
            while j < levels.len() && levels[j].0 - z0 <= 0.5 {
                area += levels[j].1;
                j += 1;
            }
            if area >= threshold * (1.0 + 1e-9) && b.min[2] + margin < z0 && z0 < b.max[2] - margin
            {
                out.push(z0);
            }
            i = j;
        }
    }
    out.sort_by(f64::total_cmp);
    out
}

/// Chooses the scale, sheet and arrangement for *request* with the bands *d* needs, the height
/// ladder sized for the steps legible at the scale chosen ([`layout::converge_steps`]), then the
/// GD&T's bands at the scale chosen, re-chosen until they hold. *base* is the part's lowest z. Returns the choice, the bands, and the number of steps they hold.
pub fn size(
    d: &Demand,
    request: &Request,
    scale: Option<f64>,
    page: Option<[f64; 2]>,
    base: f64,
) -> (Pick, Strips, usize) {
    size_with_details(d, request, scale, page, base, true, &|_| Scaled::default())
}

pub(crate) fn size_with_details(
    d: &Demand,
    request: &Request,
    scale: Option<f64>,
    page: Option<[f64; 2]>,
    base: f64,
    allow_larger: bool,
    details: &impl Fn(f64) -> Scaled,
) -> (Pick, Strips, usize) {
    // The height ladder is reserved in draftwright's slots, as every drawing so far is composed.
    // Only on a sheet trial's second attempt ([`crate::planning::lane_ladder`]: the first
    // drawing at this sheet and scale did not fit), and only where the slots do not fit the
    // given pair, is the ladder reserved at the port's lane pitch instead, when that fits.
    let sized = |ladder| size_on(d, request, scale, page, base, allow_larger, details, ladder);
    let slots = sized(layout::right_strip_depth);
    if slots.0.fallback.is_none()
        || scale.is_none()
        || page.is_none()
        || !crate::planning::is_lane_ladder()
    {
        return slots;
    }
    let lanes = sized(layout::lane_strip_depth);
    if lanes.0.fallback.is_none() {
        lanes
    } else {
        let mut slots = slots;
        slots.0.lanes_short = true;
        slots
    }
}

#[allow(clippy::too_many_arguments)]
fn size_on(
    d: &Demand,
    request: &Request,
    scale: Option<f64>,
    page: Option<[f64; 2]>,
    base: f64,
    allow_larger: bool,
    details: &impl Fn(f64) -> Scaled,
    ladder: fn(usize, usize) -> f64,
) -> (Pick, Strips, usize) {
    let measure = |n: usize| strips_on(d, n, request.views.plan, ladder);
    let choose = |strips: &Strips| {
        let req = Request {
            strips: strips.clone(),
            ..request.clone()
        };
        let mut bare = layout::choose(
            &req,
            &|_| Scaled::default(),
            scale,
            page,
            &layout::AUTOMATIC_ARRANGEMENTS,
            None,
        );
        if scale.is_none() && page.is_none() && bare.scale < d.lanes.recess_chain_scale {
            let readable = crate::units::scales(request.units)
                .iter()
                .copied()
                .rev()
                .find(|s| *s >= d.lanes.recess_chain_scale)
                .unwrap_or(crate::units::scales(request.units)[0]);
            bare = layout::choose(
                &req,
                &|_| Scaled::default(),
                Some(readable),
                None,
                &layout::AUTOMATIC_ARRANGEMENTS,
                None,
            );
        }
        if !allow_larger && details(bare.scale).derived.is_empty() {
            return bare;
        }
        let detailed = layout::choose(
            &req,
            details,
            if allow_larger
                && !(scale.is_none() && page.is_none() && d.lanes.recess_chain_scale > 0.0)
            {
                scale
            } else {
                Some(bare.scale)
            },
            if allow_larger { page } else { Some(bare.page) },
            &layout::AUTOMATIC_ARRANGEMENTS,
            None,
        );
        if detailed.fallback.is_some() && !allow_larger {
            bare
        } else {
            detailed
        }
    };
    let count = |s: f64| legible(d, base, s);
    let (mut pick, mut strips, n) =
        layout::converge_steps(d.step_zs.len(), &measure, &choose, &count);
    // Room for the GD&T at the chosen scale; the bands only widen, so the choice moves one way
    // along the ladder and settles (a pinned scale at once).
    let views = request.views;
    let shown = |v: &str| match v {
        "front" => views.front,
        "plan" => views.plan,
        _ => views.side,
    };
    let gdt: Vec<layout::Gdt> = d.gdt[usize::from(!views.side && views.plan)]
        .iter()
        .filter(|g| shown(g.view))
        .cloned()
        .collect();
    if !gdt.is_empty() {
        let base = strips.clone();
        for _ in 0..=crate::units::scales(request.units).len() * request.paper.pages().len() {
            let mut wider = strips.clone();
            wider.reserve_gdt(&base, &d.lanes, d.gdt_beyond_lanes, &gdt, pick.scale);
            if wider == strips {
                break;
            }
            strips = wider;
            pick = choose(&strips);
        }
    }
    (pick, strips, n)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hole(axis: usize) -> Hole {
        Hole {
            at: [0.0; 3],
            axis: Some(axis),
            diameter: 5.0,
            through: true,
            cbore: false,
            spotface: false,
            spec: Vec::new(),
            pattern: None,
        }
    }

    /// A part turned about z keeps front and plan; about x or y, front and side. A hole that
    /// needs the dropped view end-on vetoes the reduction.
    #[test]
    fn a_turned_part_drops_the_repeated_view_unless_a_feature_needs_it() {
        let mut d = Demand {
            turning_axis: Some(2),
            profiles: 1,
            ..Demand::default()
        };
        let (kept, dropped, _) = turned_views(&d).unwrap();
        assert_eq!((kept, dropped), (["front", "plan"], "side"));
        d.holes.push(hole(2));
        assert!(
            turned_views(&d).is_some(),
            "an axial hole is seen end-on in the plan"
        );
        d.holes.push(hole(0));
        assert!(
            turned_views(&d).is_none(),
            "a cross hole along x needs the side view"
        );
        d.holes.clear();
        d.turning_axis = Some(0);
        assert_eq!(turned_views(&d).unwrap().1, "plan");
        d.holes.push(hole(2));
        assert!(turned_views(&d).is_none());
        d.turning_axis = None;
        assert!(turned_views(&Demand::default()).is_none());
    }

    /// The section cuts the densest row of z-axis holes with a hidden profile; through holes
    /// with nothing hidden do not count, and a tie goes to the row nearest the centre.
    #[test]
    fn the_section_cuts_the_densest_row_of_hidden_profiles() {
        let at = |y: f64, blind: bool, cbore: bool| Hole {
            at: [0.0, y, 0.0],
            through: !blind,
            cbore,
            ..hole(2)
        };
        assert_eq!(section(&[at(5.0, false, false)], 0.0), None);
        let holes = [
            at(10.0, true, false),
            at(10.3, false, true),
            at(-4.0, true, false),
            at(30.0, false, false),
            at(30.0, false, false),
            at(30.0, false, false),
        ];
        let s = section(&holes, 0.0).unwrap();
        assert_eq!((s.cut_y, s.holes), (10.0, 2));
        let tie = [at(10.0, true, false), at(-4.0, true, false)];
        assert_eq!(section(&tie, 0.0).unwrap().cut_y, -4.0);
    }

    /// The height ladder is reserved in draftwright's slots. Only a sheet trial's second
    /// attempt ([`crate::planning::lane_ladder`]) reserves it at the lane pitch, and only at a
    /// given sheet and scale the slots do not fit.
    #[test]
    fn the_ladder_takes_the_lane_pitch_only_on_a_second_attempt_the_slots_do_not_fit() {
        let d = Demand {
            step_zs: vec![14.0, 21.0, 28.0, 35.0],
            ..Default::default()
        };
        let a4 = Some([297.0, 210.0]);
        let sized = |width: f64, scale: Option<f64>, page: Option<[f64; 2]>| {
            let request = Request::new([width, 30.0, 50.0], layout::Principals::ALL, true);
            let (pick, strips, _) = size(&d, &request, scale, page, 0.0);
            (pick.scale, pick.page, pick.fallback, strips.right)
        };
        // Four steps take 86 mm in slots and 42 mm in lanes. A part 120 mm wide does not fit
        // A4 at 1:1 beside the slots; one 100 mm wide does.
        let slots = sized(120.0, Some(1.0), a4);
        assert_eq!(
            (slots.2, slots.3),
            (Some(layout::Fallback::Uncertain), 86.0)
        );
        let fits = sized(100.0, Some(1.0), a4);
        assert_eq!((fits.2, fits.3), (None, 86.0));
        let automatic = [sized(120.0, None, a4), sized(120.0, Some(1.0), None)];
        crate::planning::lane_ladder(|| {
            let lanes = sized(120.0, Some(1.0), a4);
            assert_eq!((lanes.2, lanes.3), (None, 42.0));
            // The slots stay where they fit, and wherever the scale or the sheet is chosen.
            assert_eq!(sized(100.0, Some(1.0), a4), fits);
            assert_eq!(
                [sized(120.0, None, a4), sized(120.0, Some(1.0), None)],
                automatic
            );
        });
        assert_eq!(sized(120.0, Some(1.0), a4), slots);
    }

    #[test]
    fn steps_are_legible_by_page_height_and_separation() {
        // 12.4 mm above the base on the page, then 7 mm apart.
        let zs = [5.0, 7.0, 9.0, 13.0];
        assert_eq!(layout::legible_steps(&zs, 0.0, 1.0), vec![13.0]);
        assert_eq!(layout::legible_steps(&zs, 0.0, 2.0), vec![7.0, 13.0]);
        assert_eq!(
            layout::legible_steps(&zs, 0.0, 5.0),
            vec![5.0, 7.0, 9.0, 13.0]
        );
    }
}

#[cfg(test)]
mod internal_station_tests {
    use super::internal_station;

    #[test]
    fn internal_station_rows_use_python_rounding() {
        // Python round(y, 1) rounds the actual binary value, with ties to even.
        // Scaling first also introduces a second rounding for +/-1.15.
        for (y, expected) in [(1.25, 1.2), (-1.25, -1.2), (1.15, 1.1), (-1.15, -1.1)] {
            let section = internal_station(
                &[
                    (y, "hex_pocket"),
                    (y, "hex_pocket"),
                    (y, "circular_channel"),
                ],
                y,
            )
            .expect("three unlike internal details qualify the station");
            assert_eq!(section.cut_y, expected, "station at {y}");
            assert_eq!(section.holes, 3);
            assert!(section.internal_detail);
        }
    }
}
