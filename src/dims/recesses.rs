//! Section recesses (quiddity's `SectionRecess`, the Analysis's `section_recess` features) that
//! draftwright reads as pockets, hexagonal pockets, circular seats or open channels
//! (`section_recess_fields`), dimensioned as its sheets state them (`annotations/_pocket_pad.py`,
//! `_polygonal_prisms.py`, `_circular_recesses.py`, `_thin_profiles.py`, and the pocket and seat
//! locations in `compiled.py`). Blind slots, the other recesses with a grammar, are
//! [`super::slots`]'s.
//!
//! Each recess is read once, through lint's port of `section_recess_fields`
//! ([`crate::lint::section_recesses::grammar`] and the readers built on it in
//! [`crate::lint::pockets`] and [`crate::lint::channels`]), so the plan and lint agree on what a
//! recess is. A record no grammar reads stays reported as `dimensions_not_ported` (the planner
//! claims no kind); one read but whose axes lie along no axis of the part is
//! `feature_not_dimensioned`.
//!
//! - **A pocket** (rectangular, edge-open polygonal, obround or rounded rectangle): one callout
//!   `W × L × D DEEP` in the view that looks into it (along its depth), its width and length the
//!   shorter and longer sides of its section, as `_pocket_label` prints it; a pocket whose mouth
//!   is a cylinder states its greatest depth, `MAX DEEP`. The leader goes to a side wall. It is
//!   located from the part's box (draftwright's `datum_xy`) to its centre, or along its length to
//!   its near end when that end is on the box (`pocket_location_reference`), in both directions
//!   across it, in the same view. draftwright draws a z pocket's y location in the side view; the
//!   port keeps both in the view that looks into it (ADR1-6, the maintainer's decision of
//!   2026-10-08 for holes and slot patterns). A direction in which the reference is on the box is
//!   an info `location_on_datum` problem (draftwright's `pocket_location_coincident_with_datum`
//!   omission). An edge-open pocket is not located: its edge fixes it, as in draftwright.
//! - **A hexagonal pocket**: `HEX AF A/F × D DEEP` in the view down its axis, with a leader to its
//!   inscribed circle; identical pockets along one axis opening the same way (away from the box
//!   around their faces) are counted once (`2× HEX …`), as draftwright batches them. draftwright locates no hexagonal pocket, and nor
//!   does the port (a maintainer question in `docs/next-session.md`).
//! - **A circular seat** (a part-cylindrical channel open at both ends): `øD × L LONG × A° ARC`
//!   in the view along it, the leader to its arc's middle or quarter stations; coaxial seats with the same label
//!   project onto one arc and are counted once (`2× ø…`), as draftwright groups them. Its axis is
//!   located from the part's box to the axis's midpoint along x, y and z
//!   (`seat_location.location.<axis>`): across the seat in the view along it, along it in the
//!   first view that shows its profile. draftwright sends x and z to the front and y to the side
//!   whatever the seat's axis; for a seat along y (the only one on the captured sheets) the two
//!   agree. Coaxial seats share their locations across the axis. A direction in which the axis's
//!   midpoint is on the box is an info `location_on_datum` problem.
//! - **An open channel** (a rectangular U section open along its run): its width, wall to wall at
//!   the mouth, as a linear dimension in the view along the channel (`channel_width.length`).
//!   draftwright draws it only in a base-and-walls construction (quiddity's plates along two axes
//!   or more) on a part with no turned profile (here: no `turned_step` feature, since the bridge
//!   does not read quiddity's turned profiles); otherwise its step ladder owns the recess, which
//!   the port does not plan yet, so the channel stays `dimensions_not_ported`. Its depth and run
//!   are the plates' and the overall extents', as in draftwright.
//!
//! Unified pocket patterns share one callout, pitches and centre locations. Rounded pockets
//! retain four corner-radius measurements through existing blend owners or a shared radius callout. A recess location
//! coinciding with a hole's or slot's is not merged (ARCH0009-6).

use super::envelope::profile_views;
use super::{
    AXES, Context, Measurement, P3, Planner, Problem, Shape, Want, end_on, fmt, key, want_id,
};
use crate::bridge::Feature;
use crate::bridge::holes::{dir_to_part, principal, to_part};
use crate::lint::section_recesses::{Grammar, grammar, recesses};
use crate::lint::{channels, pockets};

pub(super) const PLANNER: Planner = Planner {
    kinds: &["pocket_pattern"],
    wants,
};

fn not_dimensioned(f: &Feature, why: &str) -> Problem {
    Problem {
        id: format!("p:feature_not_dimensioned:{}", f.id),
        code: "feature_not_dimensioned".into(),
        severity: "warning".into(),
        message: format!(
            "{} is not dimensioned: {why}.",
            crate::wording::capital(&crate::wording::feature(f))
        ),
        subjects: vec![f.id.clone()],
        ..Default::default()
    }
}

pub(super) fn on_datum(a: &crate::model::Analysis, owner: &str, k: usize) -> Problem {
    Problem {
        id: format!("p:location_on_datum:{owner}/{}", AXES[k]),
        code: "location_on_datum".into(),
        severity: "info".into(),
        message: format!(
            "{} is located on the part's {} face, so its {} location measures nothing and is not dimensioned.",
            crate::wording::capital(&crate::wording::feature_id(a, owner)),
            ["left", "front", "bottom"][k],
            AXES[k]
        ),
        subjects: vec![owner.into()],
        ..Default::default()
    }
}

fn words(s: &str) -> Vec<String> {
    s.split(' ').map(String::from).collect()
}

/// A location from the part's box to *at* along *k*, stated in *view* for *owners* (the first
/// names it).
pub(super) fn location(
    owners: &[&Feature],
    parameter: &str,
    at: P3,
    k: usize,
    lo: f64,
    view: &'static str,
    basis: String,
) -> Want {
    let d = at[k] - lo;
    let mut from = at;
    from[k] = lo;
    Want {
        id: want_id(&owners[0].id, parameter),
        views: vec![view],
        value: d,
        terms: vec![fmt(d)],
        measurements: owners
            .iter()
            .map(|f| Measurement {
                feature: f.id.clone(),
                parameter: parameter.into(),
                value: d,
            })
            .collect(),
        turning: None,
        basis,
        shape: Shape::Linear {
            a: from,
            b: at,
            axis: k,
            a_edge: true,
            b_edge: false,
            order: 1,
        },
    }
}

fn wants(cx: &Context) -> (Vec<Want>, Vec<Problem>) {
    let mut out = Vec::new();
    let mut problems = Vec::new();
    corner_wants(cx, &mut out);
    pattern_wants(cx, &mut out, &mut problems);
    pocket_wants(cx, &mut out, &mut problems);
    hex_wants(cx, &mut out, &mut problems);
    seat_wants(cx, &mut out, &mut problems);
    channel_wants(cx, &mut out, &mut problems);
    (out, problems)
}

fn pocket_wants(cx: &Context, out: &mut Vec<Want>, problems: &mut Vec<Problem>) {
    let [lo, _] = cx.analysis.envelope;
    for (f, p) in pockets::placed(cx.analysis) {
        if crate::lint::pocket_patterns::member(cx.analysis, &f.id) {
            continue;
        }
        let p = match p {
            Ok(p) => p,
            Err(why) => {
                problems.push(not_dimensioned(f, &why));
                continue;
            }
        };
        let view = end_on(p.depth_axis);
        let (depth, deep) = if p.mouth {
            ("pocket_max_depth.length", "MAX DEEP")
        } else {
            ("pocket_depth.length", "DEEP")
        };
        let m = |parameter: &str, value: f64| Measurement {
            feature: f.id.clone(),
            parameter: parameter.into(),
            value,
        };
        let mut terms = vec![fmt(p.width), "×".into(), fmt(p.length), "×".into()];
        terms.push(fmt(p.depth));
        terms.extend(words(deep));
        let wall = |k: usize, s: f64| {
            let mut c = p.centre;
            c[k] += s * 0.5 * if k == p.width_axis { p.width } else { p.length };
            (c, 0.0)
        };
        // Mid-wall anchors alone can box a small pocket's leader between its
        // own two centre-location witnesses. Quarter stations on the proven
        // straight wall leave the solver a real attachment away from that cross.
        let mut sites = vec![
            wall(p.width_axis, -1.0),
            wall(p.width_axis, 1.0),
            wall(p.long_axis, -1.0),
            wall(p.long_axis, 1.0),
        ];
        // Captured bridge drawings retain the matching-axis midpoint contract.
        // Native recognition supplies the additional wall geometry used by the
        // joint solve for newly drawn recesses.
        if cx
            .analysis
            .recognition
            .as_ref()
            .is_some_and(|r| r.recogniser.starts_with("quiddity-rust "))
        {
            // An obround's ends are semicircles of half its width: its walls are straight only
            // between them, and quarter stations past them would lie in air beyond the arc
            // (ARCH0017-6).
            let obround = f
                .parameters
                .get("classification")
                .and_then(|c| c.get("section_shape"))
                .and_then(serde_json::Value::as_str)
                == Some("obround");
            let rounding = p
                .corner_radius
                .unwrap_or(if obround { 0.5 * p.width } else { 0.0 });
            for (normal, tangent, length) in [
                (p.width_axis, p.long_axis, p.length),
                (p.long_axis, p.width_axis, p.width),
            ] {
                let straight = (length * 0.5 - rounding).max(0.0);
                for side in [-1.0, 1.0] {
                    for station in [-0.5, 0.5] {
                        let (mut at, radius) = wall(normal, side);
                        at[tangent] += station * straight;
                        sites.push((at, radius));
                    }
                }
            }
        }
        out.push(Want {
            id: want_id(&f.id, "pocket_width.length"),
            views: vec![view],
            value: p.width,
            terms,
            measurements: vec![m("pocket_width.length",p.width),m("pocket_length.length",p.length),m(depth,p.depth)],
            turning: None,
            basis: format!(
                "a blind pocket's width, length and {}depth in one callout, in the view that looks into it",
                if p.mouth {
                    "greatest (its mouth is a cylinder) "
                } else {
                    ""
                }
            ),
            shape: Shape::Callout { arcs: Vec::new(),
                sites,
            },
        });
        if p.edge_anchored {
            continue;
        }
        let mut at = p.centre;
        let near = (p.lo - lo[p.long_axis]).abs() <= 1e-6;
        at[p.long_axis] = if near { p.lo } else { 0.5 * (p.lo + p.hi) };
        at[p.width_axis] = p.w_center;
        let mut axes = [p.long_axis, p.width_axis];
        if p.depth_axis == 2 {
            axes = [0, 1];
        }
        for k in axes {
            if cx
                .turned
                .as_ref()
                .is_some_and(|t| k != t.axis && (at[k] - t.on[k]).abs() <= 1e-6)
                && !cx.spec.origin.contains_key(AXES[k])
            {
                problems.push(Problem {
                    id: format!("p:location_on_axis:{}/{}", f.id, AXES[k]),
                    code: "location_on_axis".into(),
                    severity: "info".into(),
                    message: format!(
                        "{} is centred on the turning axis in {}; no tangent location is needed.",
                        crate::wording::capital(&crate::wording::feature(f)),
                        AXES[k]
                    ),
                    subjects: vec![f.id.clone()],
                    ..Default::default()
                });
                continue;
            }
            if (at[k] - lo[k]).abs() <= 1e-6 {
                problems.push(on_datum(cx.analysis, &f.id, k));
                continue;
            }
            let parameter = if p.depth_axis == 2 {
                format!("location_pocket.location.{}", AXES[k])
            } else {
                format!("location_pocket.{}", AXES[k])
            };
            let to = if k == p.long_axis && near {
                "its near end"
            } else {
                "its centre"
            };
            out.push(location(
                &[f],
                &parameter,
                at,
                k,
                lo[k],
                view,
                format!(
                    "a blind pocket located in {} from the part's {} face to {to}, in the view that looks into it",
                    AXES[k],
                    ["left", "front", "bottom"][k]
                ),
            ));
        }
    }
}

fn hex_wants(cx: &Context, out: &mut Vec<Want>, problems: &mut Vec<Problem>) {
    // Callouts by axis, the side the pocket opens to, size across flats and depth, in quiddity's
    // order of their first member.
    type Member<'a> = (&'a Feature, P3, f64, f64);
    type Key = (usize, i64, i64, i64);
    let mut groups: Vec<(Key, Vec<Member>)> = Vec::new();
    for h in pockets::hexes(cx.analysis) {
        let (axis, at) = match h.placed {
            Ok(placed) => placed,
            Err(why) => {
                problems.push(not_dimensioned(h.feature, &why));
                continue;
            }
        };
        // It opens away from its faces' box, which lies below its mouth.
        let opens = (at[axis] - h.feature.fingerprint.position[axis]).signum() as i64;
        let k = (axis, opens, key(h.across_flats), key(h.depth));
        let member = (h.feature, at, h.across_flats, h.depth);
        match groups.iter_mut().find(|(g, _)| *g == k) {
            Some((_, members)) => members.push(member),
            None => groups.push((k, vec![member])),
        }
    }
    for ((axis, ..), members) in groups {
        let (first, _, across, depth) = members[0];
        let view = end_on(axis);
        let n = members.len();
        let mut terms = Vec::new();
        if n > 1 {
            terms.push(format!("{n}×"));
        }
        terms.extend(["HEX".into(), fmt(across), "A/F".into(), "×".into()]);
        terms.extend([fmt(depth), "DEEP".into()]);
        out.push(Want {
            id: want_id(&first.id, "polygon_across_flats.length"),
            views: vec![view],
            value: across,
            terms,
            measurements: members
                .iter()
                .flat_map(|(f, _, a, d)| {
                    [
                        ("polygon_across_flats.length", *a),
                        ("pocket_depth.length", *d),
                    ]
                    .map(|(parameter, value)| Measurement {
                        feature: f.id.clone(),
                        parameter: parameter.into(),
                        value,
                    })
                })
                .collect(),
            turning: None,
            basis: if n > 1 {
                format!(
                    "{n} identical hexagonal pockets along {} are called out once and counted: their size across flats and depth, in the view down them",
                    AXES[axis]
                )
            } else {
                "a hexagonal pocket's size across flats and depth in one callout, in the view down it"
                    .into()
            },
            shape: Shape::Callout { arcs: Vec::new(),
                sites: members
                    .iter()
                    .map(|(_, at, a, _)| (*at, 0.5 * a))
                    .collect(),
            },
        });
    }
}

fn seat_wants(cx: &Context, out: &mut Vec<Want>, problems: &mut Vec<Problem>) {
    let [lo, _] = cx.analysis.envelope;
    // Coaxial seats with one label, by axis, the axis's position across it and the label's
    // values, in quiddity's order of their first member.
    type Key = (usize, [i64; 3], [i64; 3]);
    let mut groups: Vec<(Key, Vec<channels::Seat>)> = Vec::new();
    for (f, s) in channels::seats(cx.analysis) {
        let s = match s {
            Ok(s) => s,
            Err(why) => {
                problems.push(not_dimensioned(f, &why));
                continue;
            }
        };
        let k = s.run_axis;
        let across = [0, 1, 2].map(|j| if j == k { 0 } else { key(s.centre[j]) });
        let sizes = [key(2.0 * s.radius), key(s.length), key(s.sweep)];
        match groups.iter_mut().find(|(g, _)| *g == (k, across, sizes)) {
            Some((_, members)) => members.push(s),
            None => groups.push(((k, across, sizes), vec![s])),
        }
    }
    for ((axis, ..), members) in groups {
        let first = &members[0];
        let view = end_on(axis);
        let n = members.len();
        let mut terms = Vec::new();
        if n > 1 {
            terms.push(format!("{n}×"));
        }
        let diameter = 2.0 * first.radius;
        terms.extend([format!("ø{}", fmt(diameter)), "×".into(), fmt(first.length)]);
        terms.extend(["LONG".into(), "×".into(), format!("{}°", fmt(first.sweep))]);
        terms.push("ARC".into());
        out.push(Want {
            id: want_id(&first.feature.id, "seat_diameter.diameter"),
            views: vec![view],
            value: diameter,
            terms,
            measurements: members
                .iter()
                .flat_map(|s| {
                    [
                        ("seat_diameter.diameter", 2.0 * s.radius),
                        ("seat_run.length", s.length),
                        ("seat_sweep.angle", s.sweep),
                    ]
                    .map(|(parameter, value)| Measurement {
                        feature: s.feature.id.clone(),
                        parameter: parameter.into(),
                        value,
                    })
                })
                .collect(),
            turning: None,
            basis: if n > 1 {
                format!(
                    "{n} coaxial circular seats with one label project onto one arc: called out once and counted, their diameter, run and swept angle, in the view along {}",
                    AXES[axis]
                )
            } else {
                "a circular seat's diameter, run and swept angle in one callout, in the view along it"
                    .into()
            },
            shape: Shape::Callout { arcs: Vec::new(),
                sites: members.iter().flat_map(|s| {
                    // All stations are on the measured arc, away from its open
                    // ends. A fixed midpoint can force a new seat leader across
                    // an existing datum tower even when a quarter is clear.
                    let axes: Vec<_> = (0..3).filter(|k| *k != s.run_axis).collect();
                    let (u, v) = (axes[0], axes[1]);
                    let radial = [s.anchor[u] - s.centre[u], s.anchor[v] - s.centre[v]];
                    [0.0_f64, -0.25, 0.25].map(|fraction| {
                        let (sin, cos) = (fraction * s.sweep.to_radians()).sin_cos();
                        let mut point = s.anchor;
                        point[u] = s.centre[u] + cos * radial[0] - sin * radial[1];
                        point[v] = s.centre[v] + sin * radial[0] + cos * radial[1];
                        (point, 0.0)
                    })
                }).collect(),
            },
        });
        let owners: Vec<&Feature> = members.iter().map(|s| s.feature).collect();
        let face = |k: usize| ["left", "front", "bottom"][k];
        for k in (0..3).filter(|&k| k != axis) {
            if (first.centre[k] - lo[k]).abs() <= 1e-9 {
                problems.push(on_datum(cx.analysis, &first.feature.id, k));
                continue;
            }
            let mut located = location(
                &owners,
                &format!("seat_location.location.{}", AXES[k]),
                first.centre,
                k,
                lo[k],
                view,
                format!(
                    "a circular seat's axis located in {} from the part's {} face, in the view along it{}",
                    AXES[k],
                    face(k),
                    if n > 1 {
                        "; one dimension serves every coaxial seat"
                    } else {
                        ""
                    }
                ),
            );
            if cx
                .analysis
                .recognition
                .as_ref()
                .is_some_and(|r| r.recogniser.starts_with("quiddity-rust "))
            {
                // The axis coordinate is also visible in the other orthogonal
                // profile. Prefer the opening view, but let a joint held-ink
                // solve use that profile when its existing bands are clearer.
                for candidate in profile_views(k) {
                    if !located.views.contains(&candidate) {
                        located.views.push(candidate);
                    }
                }
            }
            out.push(located);
        }
        let along = profile_views(axis)[0];
        for s in &members {
            if (s.centre[axis] - lo[axis]).abs() <= 1e-9 {
                problems.push(on_datum(cx.analysis, &s.feature.id, axis));
                continue;
            }
            let mut located = location(
                &[s.feature],
                &format!("seat_location.location.{}", AXES[axis]),
                s.centre,
                axis,
                lo[axis],
                along,
                format!(
                    "a circular seat located along its axis ({}) from the part's {} face to the axis's midpoint, in a view that shows its profile",
                    AXES[axis],
                    face(axis)
                ),
            );
            if cx
                .analysis
                .recognition
                .as_ref()
                .is_some_and(|r| r.recogniser.starts_with("quiddity-rust "))
            {
                located.views = profile_views(axis).to_vec();
            }
            out.push(located);
        }
    }
}

/// A channel in the part's frame: the axes it runs along and spans across, its width, and its
/// walls' coordinates across it with the middle of its mouth.
struct Channel<'a> {
    feature: &'a Feature,
    run: usize,
    wide: usize,
    width: f64,
    walls: [f64; 2],
    mouth: P3,
}

/// *f*'s channel (`section_recess_fields`' channel), or why it cannot be dimensioned.
fn channel<'a>(
    f: &'a Feature,
    c: &crate::lint::section_recesses::Channel,
    frame: &[[f64; 3]; 4],
) -> Result<Channel<'a>, String> {
    let world = |k: usize| {
        let mut e = [0.0; 3];
        e[k] = 1.0;
        principal(dir_to_part(frame, e))
    };
    let deep = 3 - c.axis - c.width_axis;
    let (Some(run), Some(wide)) = (world(c.axis), world(c.width_axis)) else {
        return Err("its axes lie along no axes of the part".into());
    };
    let mut mouth = c.origin;
    mouth[deep] = if c.open_sign > 0.0 { c.d_hi } else { c.d_lo };
    let mouth = to_part(frame, mouth);
    let half = 0.5 * c.width;
    Ok(Channel {
        feature: f,
        run,
        wide,
        width: c.width,
        walls: [mouth[wide] - half, mouth[wide] + half],
        mouth,
    })
}

fn channel_wants(cx: &Context, out: &mut Vec<Want>, problems: &mut Vec<Problem>) {
    let analysis = cx.analysis;
    // draftwright's `_append_primary_feature_families`: channels are drawn only for a
    // base-and-walls construction with no turned profile.
    if !channels::multi_axis(analysis) || analysis.features.iter().any(|f| f.kind == "turned_step")
    {
        return;
    }
    let frame = crate::bridge::holes::frame(analysis);
    for f in recesses(analysis) {
        let Ok(Grammar::Channel(c)) = grammar(f) else {
            continue;
        };
        let c = match channel(f, &c, &frame) {
            Ok(c) => c,
            Err(why) => {
                problems.push(not_dimensioned(f, &why));
                continue;
            }
        };
        let view = end_on(c.run);
        let wall = |w: f64| {
            let mut p = c.mouth;
            p[c.wide] = w;
            p
        };
        out.push(Want {
            id: want_id(&c.feature.id, "channel_width.length"),
            views: vec![view],
            value: c.width,
            terms: vec![fmt(c.width)],
            measurements: vec![Measurement {
                feature: c.feature.id.clone(),
                parameter: "channel_width.length".into(),
                value: c.width,
            }],
            turning: None,
            basis: "an open channel's width, wall to wall at its mouth, in the view along it (its depth and run are the plates' and the overall extents')".into(),
            shape: Shape::Linear {
                a: wall(c.walls[0]),
                b: wall(c.walls[1]),
                axis: c.wide,
                a_edge: false,
                b_edge: false,
                order: 0,
            },
        });
    }
}

fn pattern_wants(cx: &Context, out: &mut Vec<Want>, problems: &mut Vec<Problem>) {
    for f in cx
        .analysis
        .features
        .iter()
        .filter(|f| f.kind == "pocket_pattern")
    {
        let group = match crate::lint::pocket_patterns::read(cx.analysis, f) {
            Ok(p) => p,
            Err(why) => {
                problems.push(not_dimensioned(f, &why));
                continue;
            }
        };
        let p = &group.pocket;
        let view = end_on(p.depth_axis);
        let count = group.members.len();
        let depth = if p.mouth {
            "pocket_max_depth.length"
        } else {
            "pocket_depth.length"
        };
        let mut terms = vec![
            format!("{count}×"),
            fmt(p.width),
            "×".into(),
            fmt(p.length),
            "×".into(),
            fmt(p.depth),
        ];
        terms.extend(words(if p.mouth { "MAX DEEP" } else { "DEEP" }));
        let mut measurements = Vec::new();
        let mut sites = Vec::new();
        let placed = pockets::placed(cx.analysis);
        for owner in std::iter::once(f).chain(group.members.iter().copied()) {
            for (parameter, value) in [
                ("pocket_width.length", p.width),
                ("pocket_length.length", p.length),
                (depth, p.depth),
            ] {
                measurements.push(Measurement {
                    feature: owner.id.clone(),
                    parameter: parameter.into(),
                    value,
                });
            }
            if let Some((_, Ok(q))) = placed.iter().find(|(g, _)| g.id == owner.id) {
                for sign in [-1.0, 1.0] {
                    let mut c = q.centre;
                    c[q.width_axis] += sign * q.width / 2.0;
                    sites.push((c, 0.0));
                }
            }
        }
        measurements.push(Measurement {
            feature: f.id.clone(),
            parameter: "grouping.count".into(),
            value: count as f64,
        });
        out.push(Want {
            id: want_id(&f.id, "pocket_width.length"),
            views: vec![view],
            value: p.width,
            terms,
            measurements,
            turning: None,
            basis:
                "one count and size/depth callout for identical recognised pocket-pattern members"
                    .into(),
            shape: Shape::Callout {
                sites,
                arcs: Vec::new(),
            },
        });
        for (parameter, axis, pitch, intervals, at) in group.pitches {
            let mut b = at;
            b[axis] += pitch;
            out.push(Want {
                id: want_id(&f.id, &parameter),
                views: vec![view],
                value: pitch,
                terms: vec![format!("{intervals}× {}", fmt(pitch))],
                measurements: vec![Measurement {
                    feature: f.id.clone(),
                    parameter,
                    value: pitch,
                }],
                turning: None,
                basis: "the recognised pocket pattern's pitch, counted across its intervals".into(),
                shape: Shape::Linear {
                    a: at,
                    b,
                    axis,
                    a_edge: false,
                    b_edge: false,
                    order: 0,
                },
            });
        }
        for k in [p.long_axis, p.width_axis] {
            if (group.centre[k] - cx.analysis.envelope[0][k]).abs() <= 1e-6 {
                problems.push(on_datum(cx.analysis, &f.id, k));
                continue;
            }
            out.push(location(
                &[f],
                &format!("location_pocket_pattern.location.{}", AXES[k]),
                group.centre,
                k,
                cx.analysis.envelope[0][k],
                view,
                "the pocket pattern's centre located from the part's datum".into(),
            ));
        }
    }
}

/// Equal corners share ink while retaining four independent requirements per occurrence.
type CornerGroup<'a> = (usize, f64, Vec<(&'a Feature, &'a pockets::Placed)>);

fn corner_wants(cx: &Context, out: &mut Vec<Want>) {
    let placed = pockets::placed(cx.analysis);
    let mut groups: Vec<CornerGroup> = Vec::new();
    for (f, p) in &placed {
        if let Ok(p) = p
            && let Some(r) = p.corner_radius
        {
            if !crate::lint::pocket_patterns::member(cx.analysis, &f.id)
                && !pockets::corner_blends(cx.analysis, f, p).is_empty()
            {
                continue;
            }
            if let Some((_, _, members)) = groups
                .iter_mut()
                .find(|(axis, radius, _)| *axis == p.depth_axis && (*radius - r).abs() < 1e-6)
            {
                members.push((f, p));
            } else {
                groups.push((p.depth_axis, r, vec![(f, p)]));
            }
        }
    }
    for (axis, r, members) in groups {
        let count = members.len() * 4;
        let mut measurements = Vec::new();
        let mut sites = Vec::new();
        for (f, p) in &members {
            measurements.extend(
                pockets::corner_blends(cx.analysis, f, p)
                    .into_iter()
                    .map(|f| Measurement {
                        feature: f.id.clone(),
                        parameter: "blend.radius".into(),
                        value: r,
                    }),
            );
            for (corner, (u, v)) in [(-1.0, -1.0), (-1.0, 1.0), (1.0, -1.0), (1.0, 1.0)]
                .into_iter()
                .enumerate()
            {
                measurements.push(Measurement {
                    feature: f.id.clone(),
                    parameter: format!("pocket_corner_radius.radius.{corner}"),
                    value: r,
                });
                let mut at = p.centre;
                at[p.width_axis] += u * (p.width / 2.0 - r + r / std::f64::consts::SQRT_2);
                at[p.long_axis] += v * (p.length / 2.0 - r + r / std::f64::consts::SQRT_2);
                sites.push((at, 0.0));
            }
        }
        out.push(Want {
            id: want_id(&members[0].0.id, "pocket_corner_radius.radius"),
            views: vec![end_on(axis)],
            value: r,
            terms: vec![format!("{count}×"), format!("R{}", fmt(r))],
            measurements,
            turning: None,
            basis: "each rounded pocket retains four equal tangent corner-radius requirements"
                .into(),
            shape: Shape::Callout {
                sites,
                arcs: Vec::new(),
            },
        });
    }
}

type Kept = (Want, bool, Option<&'static str>, Vec<&'static str>);

/// A recess location coincident with an existing dimension of the same datum span
/// needs one carrier. Keep every exact measurement claim and an explicit covered ledger row.
pub(super) fn consolidate(
    kept: &mut Vec<Kept>,
    given: &mut Vec<Vec<String>>,
    omitted: &mut Vec<super::Omitted>,
) {
    // Identical, independently located seats in one projection use one counted
    // form callout. Keep every diameter/run/sweep claim and all real attachment
    // stations; their individual axis locations are consolidated separately.
    let seat_form = |k: &Kept| {
        let w = &k.0;
        let first = w.measurements.first()?;
        if first.parameter != "seat_diameter.diameter" {
            return None;
        }
        let form = [
            "seat_diameter.diameter",
            "seat_run.length",
            "seat_sweep.angle",
        ]
        .map(|parameter| {
            w.measurements
                .iter()
                .find(|m| m.parameter == parameter)
                .map(|m| m.value)
        });
        Some((k.2.unwrap_or(w.views[0]), form))
    };
    let mut seat_gone = std::collections::BTreeSet::new();
    for i in 0..kept.len() {
        let Some(form) = seat_form(&kept[i]) else {
            continue;
        };
        if seat_gone.contains(&i) {
            continue;
        }
        let peers: Vec<_> = (i + 1..kept.len())
            .filter(|&j| {
                !seat_gone.contains(&j)
                    && seat_form(&kept[j]).is_some_and(|other| {
                        other.0 == form.0
                            && other.1.iter().zip(form.1).all(|(a, b)| {
                                a.zip(b).is_some_and(|(a, b)| {
                                    (a - b).abs() <= crate::lint::redundancy::TOL
                                })
                            })
                    })
                    && kept[i].0.terms == kept[j].0.terms
                    && given[i] == given[j]
            })
            .collect();
        for j in peers {
            let claims = kept[j].0.measurements.clone();
            kept[i].0.measurements.extend(claims);
            let more = match &kept[j].0.shape {
                Shape::Callout { sites, .. } => sites.clone(),
                _ => Vec::new(),
            };
            if let Shape::Callout { sites, .. } = &mut kept[i].0.shape {
                sites.extend(more);
            }
            let by = kept[i].0.id.clone();
            omitted.push(super::Omitted {
                id: kept[j].0.id.clone(), label: kept[j].0.terms.join(" "),
                basis: format!("conveyed by {by}: identical located seats in one projection, explicitly counted"),
                state: crate::items::State::Covered { by },
            });
            seat_gone.insert(j);
        }
        let count = kept[i]
            .0
            .measurements
            .iter()
            .map(|m| &m.feature)
            .collect::<std::collections::BTreeSet<_>>()
            .len();
        if count > 1 {
            if kept[i].0.terms.first().is_some_and(|t| t.ends_with('×')) {
                kept[i].0.terms.remove(0);
            }
            kept[i].0.terms.insert(0, format!("{count}×"));
            kept[i].0.basis += "; identical seats share one counted form statement, retaining every diameter, run, sweep and axis location";
        }
    }
    let mut index = 0;
    kept.retain(|_| {
        let keep = !seat_gone.contains(&index);
        index += 1;
        keep
    });
    let mut index = 0;
    given.retain(|_| {
        let keep = !seat_gone.contains(&index);
        index += 1;
        keep
    });
    // Equal channel widths in one projection can share a counted size statement.
    // Their stations remain fixed by the wall scheme; retain every independent
    // width claim and the covered IDs for edits and completeness.
    let channel = |k: &Kept| {
        let m = k.0.measurements.first()?;
        if m.parameter != "channel_width.length" {
            return None;
        }
        match k.0.shape {
            Shape::Linear { axis, .. } => Some((k.2.unwrap_or(k.0.views[0]), axis)),
            _ => None,
        }
    };
    let mut channel_gone = std::collections::BTreeSet::new();
    for i in 0..kept.len() {
        let Some(key) = channel(&kept[i]) else {
            continue;
        };
        if channel_gone.contains(&i) {
            continue;
        }
        let peers: Vec<_> = (i + 1..kept.len())
            .filter(|&j| {
                channel(&kept[j]) == Some(key)
                    && !channel_gone.contains(&j)
                    && (kept[i].0.value - kept[j].0.value).abs() <= crate::lint::redundancy::TOL
                    && kept[i].0.terms == kept[j].0.terms
                    && given[i] == given[j]
            })
            .collect();
        let count = peers.len() + 1;
        for j in peers {
            let claims = kept[j].0.measurements.clone();
            kept[i].0.measurements.extend(claims);
            let by = kept[i].0.id.clone();
            omitted.push(super::Omitted {
                id: kept[j].0.id.clone(), label: kept[j].0.terms.join(" "),
                basis: format!("conveyed by {by}: equal channel widths in the same projection, explicitly counted"),
                state: crate::items::State::Covered { by },
            });
            channel_gone.insert(j);
        }
        if count > 1 {
            kept[i].0.terms.insert(0, format!("{count}×"));
            kept[i].0.basis += "; equal channel widths share one counted statement, retaining each width measurement";
        }
    }
    let mut index = 0;
    kept.retain(|_| {
        let keep = !channel_gone.contains(&index);
        index += 1;
        keep
    });
    let mut index = 0;
    given.retain(|_| {
        let keep = !channel_gone.contains(&index);
        index += 1;
        keep
    });
    let recess_location = |w: &Want| {
        w.measurements.first().is_some_and(|m| {
            m.parameter.starts_with("seat_location.")
                || m.parameter.starts_with("location_pocket.")
                || m.parameter.starts_with("location_pocket_pattern.")
        })
    };
    let span = |k: &Kept| match k.0.shape {
        Shape::Linear { a, b, axis, .. } => {
            Some((k.2.unwrap_or(k.0.views[0]), axis, a[axis], b[axis]))
        }
        _ => None,
    };
    let mut gone = std::collections::BTreeSet::new();
    for i in 0..kept.len() {
        if gone.contains(&i) || !recess_location(&kept[i].0) {
            continue;
        }
        let Some(s) = span(&kept[i]) else { continue };
        let winner = (0..kept.len())
            .filter(|&j| j != i && !gone.contains(&j))
            // Hole and slot locations have independent point identities and table cells.
            // A coincident distance must not turn their pure location carrier into a
            // mixed recess statement (ARCH0009-6).
            .filter(|&j| {
                !kept[j].0.measurements.iter().any(|m| {
                    m.feature.starts_with("f:hole:")
                        || m.feature.starts_with("f:hole_pattern:")
                        || m.feature.starts_with("f:slot:")
                        || m.feature.starts_with("f:oriented_slot:")
                })
            })
            .filter(|&j| !recess_location(&kept[j].0) || kept[j].0.id < kept[i].0.id)
            .filter(|&j| {
                span(&kept[j]).is_some_and(|t| {
                    s.0 == t.0
                        && s.1 == t.1
                        && (s.2 - t.2).abs() <= crate::lint::redundancy::TOL
                        && (s.3 - t.3).abs() <= crate::lint::redundancy::TOL
                }) && carries(&kept[j].0.terms, &kept[i].0)
                    && given[i] == given[j]
            })
            .min_by_key(|&j| {
                (
                    !kept[j].0.id.starts_with("d:envelope/"),
                    kept[j].0.id.clone(),
                )
            });
        if let Some(j) = winner {
            let claims = kept[i].0.measurements.clone();
            for m in claims {
                if !kept[j].0.measurements.contains(&m) {
                    kept[j].0.measurements.push(m);
                }
            }
            let by = kept[j].0.id.clone();
            omitted.push(super::Omitted {id:kept[i].0.id.clone(),label:kept[i].0.terms.join(" "),
                basis:format!("conveyed by {by}: the same recess datum span in the same view, with the same approved label"),
                state:crate::items::State::Covered {by}});
            gone.insert(i);
        }
    }
    let mut i = 0;
    kept.retain(|_| {
        let keep = !gone.contains(&i);
        i += 1;
        keep
    });
    let mut i = 0;
    given.retain(|_| {
        let keep = !gone.contains(&i);
        i += 1;
        keep
    });
}

/// Whether a carrier labelled *terms* states recess location *w* too: the same text, or one plain
/// size that prints *w*'s value as lint reads it (ftc09's ladder rung prints its modelled
/// 128.100003 as `128.100`, which states the seat's recorded 128.1: one station, one owner).
fn carries(terms: &[String], w: &Want) -> bool {
    terms == w.terms.as_slice()
        || matches!((terms, w.terms.as_slice()), ([t], [own])
            if t.parse::<f64>().is_ok() && own.parse::<f64>().is_ok()
                && crate::lint::holes::states(t, w.value))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::lint::paired_ramp_steps::tests::part;
    use crate::lint::section_recesses::tests::{Z, recess};

    fn planned(a: &crate::model::Analysis) -> (Vec<Want>, Vec<Problem>) {
        let frame = super::super::QFrame(a.recognition.as_ref().unwrap().frame);
        let cx = Context {
            spec: &Default::default(),
            analysis: a,
            turned: None,
            frame,
        };
        wants(&cx)
    }

    /// A rectangular pocket 10 × 6 along z whose mouth is a cylinder along x (radius 10, its
    /// crest 10 above the floor): its depth is its greatest, `MAX DEEP`.
    #[test]
    fn a_pocket_with_a_cylindrical_mouth_states_its_greatest_depth() {
        let rect = [
            ([-5.0, -3.0], 0.0),
            ([5.0, -3.0], 0.0),
            ([5.0, 3.0], 0.0),
            ([-5.0, 3.0], 0.0),
        ];
        let mut r = recess(
            "pocket",
            "rectangular",
            [[50.0, 50.0, 0.0], Z[1], Z[2], Z[3]],
            [0.0, 10.0],
            [false, true],
            true,
            &rect,
        );
        r["geometry"]["ends"]["high"]["surface"] = serde_json::json!({
            "type": "cylinder", "axis_point": [0.0, 0.0, 0.0], "axis_direction": [1.0, 0.0],
            "radius": 10.0, "branch": "positive",
        });
        let a = part("section_recess", r);
        let (w, problems) = planned(&a);
        assert!(problems.is_empty(), "{problems:?}");
        assert_eq!(w[0].terms.join(" "), "6 × 10 × 10 MAX DEEP");
        assert_eq!(w[0].measurements[2].parameter, "pocket_max_depth.length");
        let located: Vec<_> = w[1..].iter().map(|w| (w.id.clone(), w.value)).collect();
        assert_eq!(
            located,
            [
                (
                    "d:f:section_recess:1/location_pocket.location.x".to_string(),
                    50.0
                ),
                (
                    "d:f:section_recess:1/location_pocket.location.y".to_string(),
                    50.0
                ),
            ]
        );
    }

    /// An edge-open polygonal recess is a pocket fixed by its edge: called out, not located.
    #[test]
    fn an_edge_open_pocket_is_not_located() {
        let chain = [
            ([-5.0, 3.0], 0.0),
            ([-5.0, -3.0], 0.0),
            ([5.0, -3.0], 0.0),
            ([5.0, 3.0], 0.0),
        ];
        let r = recess(
            "edge_open_recess",
            "polygonal",
            [[50.0, 97.0, 0.0], Z[1], Z[2], Z[3]],
            [4.0, 10.0],
            [false, true],
            false,
            &chain,
        );
        let a = part("section_recess", r);
        let (w, problems) = planned(&a);
        assert!(problems.is_empty(), "{problems:?}");
        assert_eq!(w.len(), 1, "{w:?}");
        assert_eq!(w[0].terms.join(" "), "6 × 10 × 6 DEEP");
        assert_eq!(w[0].views, ["plan"]);
    }

    /// A pocket whose long side starts on the part's box is located along it by that end, which
    /// measures nothing: an info problem, not a dimension; across it, by its centre.
    #[test]
    fn a_pocket_on_the_box_is_located_by_its_near_end() {
        let rect = [
            ([0.0, -3.0], 0.0),
            ([20.0, -3.0], 0.0),
            ([20.0, 3.0], 0.0),
            ([0.0, 3.0], 0.0),
        ];
        let r = recess(
            "pocket",
            "rectangular",
            [[0.0, 40.0, 4.0], Z[1], Z[2], Z[3]],
            [0.0, 6.0],
            [false, true],
            true,
            &rect,
        );
        let a = part("section_recess", r);
        let (w, problems) = planned(&a);
        assert_eq!(
            problems.iter().map(|p| p.id.as_str()).collect::<Vec<_>>(),
            ["p:location_on_datum:f:section_recess:1/x"]
        );
        assert_eq!(w.len(), 2, "{w:?}");
        assert_eq!(
            (w[1].id.as_str(), w[1].value),
            ("d:f:section_recess:1/location_pocket.location.y", 40.0)
        );
    }
}
