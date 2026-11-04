//! Hole callouts, in draftwright's compound grammar (`4× ⌀10 THRU ⌴ ⌀16 ↧ 5`): identical holes
//! grouped and counted, quiddity's hole patterns called out as patterns with the pitches that
//! space them (a grid prints no `(2×2)` arrangement: ISO 129 has none, its pitches show it), and every hole or pattern located from the part's box in the view that looks
//! along it. A hole that owns an AP242 thread ([`crate::pmi_lowering::manufacturing`]) prints it
//! after its terms (`⌀1.6 ↧ 4 M2 x 0.4-6H RH; 2.5 MIN FULL THREAD`, or `SEE MFG n`) on a callout
//! of its own.
//!
//! Coincident locations from every family that locates from the part's box (holes, hole and slot
//! patterns, bosses, slots) are merged here into one dimension per span ([`consolidate`],
//! ARCH0009-6).

use std::collections::{BTreeMap, BTreeSet};

use serde_json::Value;

use super::turned::Turned;
use super::{
    AXES, Context, Measurement, NAMES, Omitted, P3, Planner, Problem, Shape, Want, end_on, fmt,
    key, point, want_id,
};
use crate::arc::ProfileArc;
use crate::bridge::{HolePattern, holes};
use crate::model::Analysis;

pub(super) const PLANNER: Planner = Planner {
    kinds: &["hole"],
    wants,
};

/// The spotface symbol as a callout term: ASME Y14.5-2018's dedicated spotface symbol, the
/// counterbore symbol with SF inside (maintainer decision, 2026-10-08). Unicode has no code
/// point for it, so the term is the counterbore symbol and the letters, as the text layer and
/// lint read it; [`super::label_ink`] draws it as one symbol, the letters inside the box.
pub(super) const SPOTFACE: &str = "⌴SF";

/// Member sites projected into a view, as integer keys.
type Sites = Vec<(i64, i64)>;

/// A recognised hole the callout grammar can state: read by [`holes::Hole::read`], with its
/// depth when it is blind.
#[derive(Clone, Debug)]
struct Hole<'a> {
    read: holes::Hole<'a>,
    /// The world axis it lies along, or, for an inclined hole, the one it lies most nearly along
    /// ([`oblique_axes`]).
    axis: usize,
    /// Whether its axis is inclined to every principal axis, so no standard view sees it end-on.
    inclined: bool,
    /// Whether its counterbore or spotface is cut at both mouths of the through hole
    /// ([`both_mouths`]), so its callout says `BOTH SIDES`.
    both: bool,
}

impl<'a> std::ops::Deref for Hole<'a> {
    type Target = holes::Hole<'a>;
    fn deref(&self) -> &holes::Hole<'a> {
        &self.read
    }
}

impl Hole<'_> {
    /// The recesses the grammar prints, each its role and sizes: the counterbore, then the
    /// spotface, in draftwright's segment order (`callout.py`'s `_CALLOUT_SEGMENTS`).
    ///
    /// draftwright prints one recess slot, the counterbore shadowing the spotface (`_recess`,
    /// `_shadowed`), so a hole quiddity finds both on (`holes.py`'s `_near_side_steps`: a shallow
    /// step is a spotface, a deep one a counterbore) loses its spotface on the sheet while its
    /// own lint still requires it (`_physical_requirement_count`). The port prints both:
    /// rust-correct, since the spotface is a machining requirement the callout would otherwise
    /// leave unstated. Each recess opens with its symbol: `⌴` for the counterbore, [`SPOTFACE`]
    /// for the spotface (draftwright prints a spotface with the counterbore's `⌴`).
    ///
    /// The counterbore-class steps outside the counterbore (quiddity's `outer_steps`,
    /// quiddity-rust#118) follow it, each its own `⌴ ⌀d ↧ depth` segment, innermost first, so the
    /// callout reads from the bore outward with every diameter growing, and each depth is the
    /// step's own mouth-to-floor span as quiddity measures it. Their role is
    /// `outer_steps.{i}`, *i* quiddity's index (outermost first). draftwright has no outer
    /// steps: rust-correct, since otherwise a machined step is left unstated.
    fn recesses(&self) -> Vec<(String, &'static str, holes::Recess)> {
        self.counterbore
            .map(|r| ("counterbore".to_string(), "⌴", r))
            .into_iter()
            .chain(
                self.outer_steps
                    .iter()
                    .enumerate()
                    .rev()
                    .map(|(i, r)| (format!("outer_steps.{i}"), "⌴", *r)),
            )
            .chain(self.spotface.map(|r| ("spotface".to_string(), SPOTFACE, r)))
            .collect()
    }

    /// The terms after the count: `⌀d`, `THRU` or `↧ depth`, the recesses, the countersink.
    ///
    /// A hole with more than one counterbore or spotface step reads one step per line: the bore
    /// on the first, each `⌴`/[`SPOTFACE`] step opening a line of its own, innermost first
    /// (maintainer decision, 2026-10-29). A single recess stays on the bore's line, wrapped only
    /// at the 48 mm width.
    fn terms(&self) -> Vec<String> {
        let mut t = vec![format!("⌀{}", fmt(self.diameter))];
        match self.depth {
            Some(depth) if !self.through => t.extend(["↧".into(), fmt(depth)]),
            _ => t.push("THRU".into()),
        }
        let recesses = self.recesses();
        let stacked = recesses.len() > 1;
        for (_, symbol, r) in recesses {
            if stacked {
                t.push(crate::callouts::BREAK.into());
            }
            t.extend([symbol.into(), format!("⌀{}", fmt(r.diameter))]);
            if let Some(depth) = r.depth {
                t.extend(["↧".into(), fmt(depth)]);
            }
        }
        if self.both {
            t.push(crate::callouts::BOTH_SIDES.into());
        }
        if let Some(c) = self.countersink {
            t.extend(["⌵".into(), format!("⌀{}", fmt(c.diameter))]);
            if let Some(a) = c.angle {
                t.extend(["×".into(), format!("{}°", fmt(a))]);
            }
        }
        t
    }

    fn measurements(&self) -> Vec<Measurement> {
        let id = &self.feature.id;
        let m = |p: &str, v: f64| Measurement {
            feature: id.clone(),
            parameter: p.into(),
            value: v,
        };
        let mut out = vec![m("bore.diameter", self.diameter)];
        if let Some(depth) = self.depth.filter(|_| !self.through) {
            out.push(m("bore.depth", depth));
        }
        for (role, _, r) in self.recesses() {
            out.push(m(&format!("{role}.diameter"), r.diameter));
            if let Some(depth) = r.depth {
                out.push(m(&format!("{role}.depth"), depth));
            }
        }
        if let Some(c) = self.countersink {
            out.push(m("countersink.diameter", c.diameter));
            if let Some(a) = c.angle {
                out.push(m("countersink.angle", a));
            }
        }
        out
    }
}

/// A hole as the callout grammar reads it, or why it cannot state it.
fn hole<'a>(read: Result<holes::Hole<'a>, String>) -> Result<Hole<'a>, String> {
    let read = read?;
    if !read.through && read.depth.is_none() {
        return Err("it is blind and has no depth".into());
    }
    let (axis, inclined) = match read.principal {
        Some((k, _)) => (k, false),
        None => (oblique_axes(read.direction)[0], true),
    };
    Ok(Hole {
        read,
        axis,
        inclined,
        both: false,
    })
}

/// Whether through hole *h*'s counterbore or spotface is cut at both its mouths: the part has a
/// second cylinder of that recess's diameter and depth on its axis at the far mouth, where the
/// bore ends (no nearer than the recess and the bore's depth from the opening, and adjoining
/// that end). quiddity records a recess found identically at both mouths of a hole whose ends
/// it cannot tell apart once, on the end it presents (`HoleRecord`), so the record alone reads
/// as one recess (lm10uu's ø16.4 ↧ 1.4 at both ends, #104). A like recess of another hole
/// farther along the same line (ctc-02's spotfaces at the two ends of its long body) is not.
fn both_mouths(analysis: &Analysis, h: &Hole) -> bool {
    let (Some(ink), Some(r), Some((k, sign)), true) = (
        analysis.edge_ink.as_ref(),
        h.counterbore.or(h.spotface),
        h.principal,
        h.through,
    ) else {
        return false;
    };
    let (Some(sunk), Some(bore)) = (r.depth, h.depth) else {
        return false;
    };
    let (u, v) = in_view(k);
    let tol = 1e-3;
    // The bore's far end lies at least this far from the opening, and at most one recess depth
    // farther (when the bore starts below the recess's floor).
    let end = sunk + bore;
    ink.curved.iter().any(|c| {
        c.cylinder.as_ref().is_some_and(|y| {
            let along = |x: f64| (x - h.at[k]) * f64::from(sign);
            let (a, b) = (along(c.bounds[0][k]), along(c.bounds[1][k]));
            let (near, far) = (a.min(b), a.max(b));
            y.axis[k].abs() > 1.0 - 1e-6
                && (y.origin[u] - h.at[u]).abs() < ON_AXIS
                && (y.origin[v] - h.at[v]).abs() < ON_AXIS
                && (2.0 * y.radius - r.diameter).abs() < tol
                && ((far - near) - sunk).abs() < tol
                && near > sunk + tol
                && near <= end + sunk + tol
                && far >= end - tol
        })
    })
}

/// The world axes an inclined direction *d* is seen across, as an ellipse rather than edge-on,
/// most nearly end-on first: by its components' size, the first axis on an exact tie (a hole at
/// 45° between two views is seen equally well in either; x, then y, then z, so the plan view,
/// which carries most of a part's dimensions, comes last).
pub(super) fn oblique_axes(d: P3) -> Vec<usize> {
    let mut axes: Vec<usize> = (0..3).filter(|&k| d[k].abs() >= 1e-6).collect();
    axes.sort_by(|&i, &j| d[j].abs().total_cmp(&d[i].abs()).then(i.cmp(&j)));
    axes
}

/// The direction toward the viewer of the view along world axis *k*.
pub(super) fn toward(k: usize) -> P3 {
    super::TOWARD
        .iter()
        .find(|(n, _)| *n == end_on(k))
        .map(|(_, t)| *t)
        // Invariant: The fixed principal view table includes one view along each coordinate axis.
        .expect("a view along each axis")
}

/// Where a leader may land on a circle of *radius* about *centre*, square to unit direction *d*,
/// in the view along world axis *k* that sees it as an ellipse: the two ends of the ellipse's
/// major axis, the points that keep the circle's full radius and where a leader pointing at the
/// centre meets the rim square-on. Each is a zero-length arc, so the tip lands exactly on the
/// projected rim (the leaders place tips on circular arcs); both are points of the circle itself,
/// so they lie on its rim in any view.
pub(super) fn rim_tips(centre: P3, d: P3, radius: f64, k: usize) -> [ProfileArc; 2] {
    let t = toward(k);
    let u = [
        d[1] * t[2] - d[2] * t[1],
        d[2] * t[0] - d[0] * t[2],
        d[0] * t[1] - d[1] * t[0],
    ];
    let n = u.iter().map(|c| c * c).sum::<f64>().sqrt();
    let u = u.map(|c| c / n);
    let v = [
        d[1] * u[2] - d[2] * u[1],
        d[2] * u[0] - d[0] * u[2],
        d[0] * u[1] - d[1] * u[0],
    ];
    [0.0, std::f64::consts::PI].map(|start| ProfileArc {
        centre,
        radius,
        u,
        v,
        start,
        sweep: 0.0,
    })
}

/// Where a leader may land on an inclined hole's bore, for each view that sees it as an ellipse
/// ([`oblique_axes`]): [`rim_tips`] on the bore's circle at the end nearer that view's viewer
/// (where its recess, if any, ends; a through hole drilled towards the viewer shows its exit).
fn hole_tips(h: &Hole) -> Vec<ProfileArc> {
    let d = h.direction;
    let along = |t: f64| [0, 1, 2].map(|j| h.at[j] + t * d[j]);
    let sunk = h
        .counterbore
        .or(h.spotface)
        .and_then(|r| r.depth)
        .unwrap_or(0.0);
    let mut out = Vec::new();
    for k in oblique_axes(d) {
        let t = toward(k);
        let centre = match h.depth {
            Some(depth) if h.through && (0..3).map(|j| d[j] * t[j]).sum::<f64>() > 0.0 => {
                along(depth)
            }
            _ => along(sunk),
        };
        out.extend(rim_tips(centre, d, 0.5 * h.diameter, k));
    }
    out
}

/// The holes' callouts, the pitches of their patterns and their locations, with the problems met
/// on the way.
fn wants(cx: &Context) -> (Vec<Want>, Vec<Problem>) {
    let analysis = cx.analysis;
    let frame = &cx.frame;
    let turned = &cx.turned;
    let [lo, _] = analysis.envelope;
    let mut out = Vec::new();
    let mut problems = Vec::new();

    let mut holes: Vec<Hole> = Vec::new();
    for (f, read) in holes::read(analysis) {
        match hole(read) {
            Ok(mut h) => {
                h.both = both_mouths(analysis, &h);
                let bottom = h.bottom;
                if !matches!(bottom, Some("through" | "flat" | "drill_point")) {
                    // Called out blind to its recognised depth, as draftwright does, but the
                    // recogniser did not establish that it stops there.
                    problems.push(Problem {
                        id: format!("p:hole_bottom_unknown:{}", f.id),
                        code: "hole_bottom_unknown".into(),
                        severity: "info".into(),
                        message: format!(
                            "{} is called out blind to its recognised depth ({}), but recognition did not establish its bottom ({}); check whether it is through.",
                            crate::wording::capital(&crate::wording::feature(f)),
                            // Invariant: The hole reader refuses blind records without a finite positive depth.
                            fmt(h.depth.expect("a blind hole has its depth")),
                            bottom.unwrap_or("none given")
                        ),
                        subjects: vec![f.id.clone()],
                        ..Default::default()
                    });
                }
                if h.inclined {
                    // As an inclined boss's (super::bosses): its size is called out below.
                    problems.push(Problem {
                        id: format!("p:feature_not_dimensioned:{}", f.id),
                        code: "feature_not_dimensioned".into(),
                        severity: "warning".into(),
                        message: format!(
                            "{}'s location is not dimensioned: its axis is inclined to every principal axis, so it needs a view square to it; its size is called out where it is seen most nearly end-on.",
                            crate::wording::capital(&crate::wording::feature(f))
                        ),
                        subjects: vec![f.id.clone()],
                        ..Default::default()
                    });
                }
                holes.push(h);
            }
            Err(why) => problems.push(Problem {
                id: format!("p:feature_not_dimensioned:{}", f.id),
                code: "feature_not_dimensioned".into(),
                severity: "warning".into(),
                message: format!(
                    "{} is not dimensioned: {why}.",
                    crate::wording::capital(&crate::wording::feature(f))
                ),
                subjects: vec![f.id.clone()],
                ..Default::default()
            }),
        }
    }
    let index: BTreeMap<&str, usize> = holes
        .iter()
        .enumerate()
        .map(|(i, h)| (h.feature.id.as_str(), i))
        .collect();
    let patterns: &[HolePattern] = analysis
        .recognition
        .as_ref()
        .map_or(&[], |r| r.patterns.as_slice());

    // Patterns first: each that holds together takes its members.
    let mut taken: BTreeSet<usize> = BTreeSet::new();
    let mut callouts: Vec<Want> = Vec::new();
    let mut located: Vec<(String, P3, usize, String)> = Vec::new(); // owner, point, view axis…
    let mut pitches: Vec<Want> = Vec::new();
    let mut declined: BTreeMap<usize, String> = BTreeMap::new();
    let mut coincident: Vec<(usize, P3, Sites)> = Vec::new(); // callout index, station
    let reads: Vec<&holes::Hole> = holes.iter().map(|h| &h.read).collect();
    let bosses = holes::bosses(analysis);
    // The part's threads and knurls on their owners: a hole's, or a pattern's when the Spec
    // authors one on it ([`crate::spec_notes`]).
    let manufacturing = crate::pmi_lowering::manufacturing(analysis);
    for p in patterns {
        let members = match holes::established_with(p, &reads, &bosses) {
            Ok(members) => members,
            Err(why) => {
                for m in &p.members {
                    if let Some(&i) = index.get(m.as_str()) {
                        declined.entry(i).or_insert_with(|| why.clone());
                    }
                }
                continue;
            }
        };
        if members.iter().any(|m| taken.contains(m)) {
            continue;
        }
        let first = &holes[members[0]];
        let k = first.axis;
        let view = end_on(k);
        let (u, v) = in_view(k);
        let centres: Vec<P3> = members.iter().map(|&m| holes[m].at).collect();
        let n = members.len();
        let (suffix, bcd, why): (Option<String>, Option<f64>, String) = match p.arrangement.as_str()
        {
            "bolt_circle" => {
                let d = p.parameters.get("diameter").and_then(Value::as_f64);
                let c = point(p.parameters.get("center")).map(|c| frame.point(c));
                match (d, c) {
                    (Some(d), Some(c)) => {
                        located_pattern(&mut located, &p.id, c, k, turned.as_ref());
                        (
                            Some(format!("EQ SP ON ø{} BC", fmt(d))),
                            Some(d),
                            if n >= 5 {
                                "a bolt circle of five or more holes is self-evident (ISSUE-1596-a): it is called out with its circle and located by its centre".to_string()
                            } else {
                                "a bolt circle of fewer than five holes is corroborated by a hole or boss on its centre (ISSUE-1596-a): it is called out with its circle and located by its centre".to_string()
                            },
                        )
                    }
                    _ => {
                        for &m in &members {
                            declined.insert(m, format!("quiddity's bolt circle {} gives no diameter or centre, so its holes are dimensioned singly", p.id));
                        }
                        continue;
                    }
                }
            }
            "grid" => match grid_lattice(&centres, k) {
                Some((cols, rows, centre)) => {
                    located_pattern(&mut located, &p.id, centre, k, turned.as_ref());
                    for (along, values, name) in [(u, &cols, "col"), (v, &rows, "row")] {
                        if values.len() > 1 {
                            pitches.push(pitch_want(
                                &p.id,
                                &format!("grid_pitch.length.{name}"),
                                view,
                                centres[0],
                                along,
                                values,
                            ));
                        }
                    }
                    (
                        None,
                        None,
                        format!(
                            "a grid of {}×{} identical holes is called out once with its size, located by its centre and spaced by its pitches",
                            rows.len(),
                            cols.len()
                        ),
                    )
                }
                _ => {
                    for &m in &members {
                        declined.insert(m, format!("quiddity's grid {} is not a square lattice in the {view} view, so its holes are dimensioned singly", p.id));
                    }
                    continue;
                }
            },
            _ => match line(&centres, u, v) {
                Some((along, values)) => {
                    let mut start = centres[0];
                    start[along] = values[0];
                    if let Some(c) = centres.iter().find(|c| (c[along] - values[0]).abs() < 1e-4) {
                        start = *c;
                    }
                    located_pattern(&mut located, &p.id, start, k, turned.as_ref());
                    pitches.push(pitch_want(
                        &p.id,
                        "pitch.length",
                        view,
                        start,
                        along,
                        &values,
                    ));
                    (
                        None,
                        None,
                        "a linear pattern is called out once, located by its first member and spaced by its pitch".to_string(),
                    )
                }
                None => {
                    for &m in &members {
                        declined.insert(m, format!("quiddity's linear pattern {} is not evenly spaced along one axis of the {view} view, so its holes are dimensioned singly", p.id));
                    }
                    continue;
                }
            },
        };
        taken.extend(members.iter().copied());
        let mut terms = vec![format!("{n}×")];
        terms.extend(first.terms());
        let mut measurements: Vec<Measurement> = members
            .iter()
            .flat_map(|&m| holes[m].measurements())
            .collect();
        // A thread the Spec authors on the pattern leads its pattern suffix, as draftwright's
        // `hole_callout_spec` folds a pattern member's thread (`4× ⌀6 THRU M6 EQ SP ON ø20 BC`).
        let (thread, _) = manufacturing.terms(&p.id);
        terms.extend(thread);
        let mut threaded = String::new();
        for o in manufacturing.on(&p.id) {
            measurements.push(Measurement {
                feature: o.id.clone(),
                parameter: o.aspect.into(),
                value: first.diameter,
            });
            threaded = format!(
                "; it carries {} thread ({}), printed after its holes' size",
                manufacturing.author(&p.id),
                o.source
            );
        }
        terms.extend(suffix);
        if let Some(d) = bcd {
            measurements.push(Measurement {
                feature: p.id.clone(),
                parameter: "bolt_circle.diameter".into(),
                value: d,
            });
        }
        let sites: Vec<(P3, f64)> = members
            .iter()
            .map(|&m| (holes[m].at, 0.5 * holes[m].diameter))
            .collect();
        let mut projected: Vec<(i64, i64)> =
            sites.iter().map(|(c, _)| (key(c[u]), key(c[v]))).collect();
        projected.sort();
        if k == 2 {
            coincident.push((callouts.len(), first.at, projected));
        }
        callouts.push(Want {
            id: want_id(&p.id, "bore.diameter"),
            views: vec![view],
            value: first.diameter,
            terms,
            measurements,
            turning: None,
            basis: format!("{why}; called out where it is seen end-on{threaded}"),
            shape: Shape::Callout {
                sites,
                arcs: Vec::new(),
            },
        });
    }

    // Draftwright names the axial face of z patterns whose members coincide in the plan.
    let mut by_sites: BTreeMap<&Sites, Vec<(usize, f64)>> = BTreeMap::new();
    for (i, c, sites) in &coincident {
        by_sites.entry(sites).or_default().push((*i, c[2]));
    }
    for group in by_sites.values().filter(|g| g.len() > 1) {
        let mut stations: Vec<f64> = group.iter().map(|g| g.1).collect();
        stations.sort_by(f64::total_cmp);
        stations.dedup_by(|a, b| (*a - *b).abs() < 1e-6);
        if stations.len() < 2 {
            continue;
        }
        for (i, z) in group {
            let n = stations
                .iter()
                .position(|s| (s - z).abs() < 1e-6)
                .unwrap_or(0);
            let which = if stations.len() == 2 {
                ["LOWER FACE", "UPPER FACE"][n].to_string()
            } else {
                format!("FACE {} OF {} FROM LOWER END", n + 1, stations.len())
            };
            let w = &mut callouts[*i];
            match w.terms.last_mut() {
                Some(last) if last.starts_with("EQ SP") => *last = format!("{last} {which}"),
                _ => w.terms.push(which),
            }
            w.basis += "; its members project onto another pattern's, so the face it is drilled from is named";
        }
    }

    // Identical holes share a counted callout only when their manufacturing requirements
    // are the same source statements. Independent threads keep independent annotations
    // (ISSUE-1298-a); one requirement applied to several holes is stated once with a count.
    // (geometry, manufacturing sources, recessed at both mouths), members.
    type Group<'a> = ((Vec<i64>, Vec<&'a str>, bool), Vec<usize>);
    let mut groups: Vec<Group> = Vec::new();
    for (i, h) in holes.iter().enumerate() {
        if taken.contains(&i) {
            continue;
        }
        let threaded = manufacturing
            .on(&h.feature.id)
            .iter()
            .map(|o| o.source.as_str())
            .collect();
        let req = (h.requirement(), threaded, h.both);
        match groups.iter_mut().find(|g| g.0 == req) {
            Some(g) => g.1.push(i),
            None => groups.push((req, vec![i])),
        }
    }
    for (_, members) in &groups {
        let first = &holes[members[0]];
        let view = end_on(first.axis);
        let views: Vec<&'static str> = if first.inclined {
            oblique_axes(first.direction)
                .into_iter()
                .map(end_on)
                .collect()
        } else {
            vec![view]
        };
        // The segments of one through hole split by a slit are one hole: counted once.
        let n = holes::drillings(members.iter().map(|&m| &holes[m].read));
        let mut terms = Vec::new();
        if n > 1 {
            terms.push(format!("{n}×"));
        }
        terms.extend(first.terms());
        let mut measurements: Vec<Measurement> = members
            .iter()
            .flat_map(|&m| holes[m].measurements())
            .collect();
        let mut threaded = String::new();
        let (suffix, through) = manufacturing.terms(&first.feature.id);
        if !suffix.is_empty() {
            // draftwright's `hole_callout_spec`: the thread's suffix after the hole's terms (or
            // `SEE MFG n`), a through tap's own `THRU` standing for the bore's.
            if through {
                terms.retain(|t| t != "THRU");
            }
            terms.extend(suffix);
            let on = manufacturing.on(&first.feature.id);
            for o in &on {
                measurements.push(Measurement {
                    feature: o.id.clone(),
                    parameter: o.aspect.into(),
                    value: first.diameter,
                });
            }
            threaded = format!(
                "; it carries {} thread ({}), {}, as draftwright gives a thread to the hole whose bore its cylinder is (lower_ap242_manufacturing_requirements)",
                manufacturing.author(&first.feature.id),
                on.iter()
                    .map(|o| o.source.as_str())
                    .collect::<Vec<_>>()
                    .join(", "),
                if manufacturing.tags.is_empty() {
                    "printed after its size"
                } else {
                    "referred to the manufacturing requirements table"
                }
            );
        }
        let mut note = members
            .iter()
            .find_map(|m| declined.get(m))
            .map_or(String::new(), |d| format!("; {d}"));
        if first.both {
            note += "; its recess is cut alike at both mouths, so the callout says BOTH SIDES";
        }
        let seen = if first.inclined {
            "most nearly end-on"
        } else {
            "end-on"
        };
        let arcs: Vec<ProfileArc> = if first.inclined {
            note += "; its axis is inclined to every principal axis, so it shows there as an ellipse, the leader to its bore's rim, and it is not located (that needs a view square to its axis)";
            members.iter().flat_map(|&m| hole_tips(&holes[m])).collect()
        } else {
            Vec::new()
        };
        callouts.push(Want {
            id: want_id(&first.feature.id, "bore.diameter"),
            views,
            value: first.diameter,
            terms,
            measurements,
            turning: None,
            basis: if n > 1 {
                format!("identical holes drilled from one face are called out once and counted, where they are seen {seen}{note}{threaded}")
            } else if members.len() > 1 {
                format!("a through hole split into coaxial segments (by a slit or gap) is one hole, called out once without a count, where it is seen {seen}{note}{threaded}")
            } else {
                format!("a hole's size is called out where it is seen {seen}{note}{threaded}")
            },
            shape: Shape::Callout {
                arcs,
                sites: members
                    .iter()
                    .map(|&m| (holes[m].at, 0.5 * holes[m].diameter))
                    .collect(),
            },
        });
        for &m in members {
            let h = &holes[m];
            if h.inclined {
                continue; // no standard view measures across its axis
            }
            let (u, v) = in_view(h.axis);
            for k in [u, v] {
                if let Some(t) = &turned
                    && !cx.spec.origin.contains_key(AXES[k])
                    && k != t.axis
                    && (h.at[k] - t.on[k]).abs() < 1e-3
                {
                    // A complete body now establishes this even without an OD boss.
                    // Keep the reason on the surviving size callout, and let an explicit
                    // Spec origin retain the tangent-based location as before (#104).
                    if crate::bridge::turned_body(analysis).is_some() {
                        callouts.last_mut().unwrap().basis += &format!(
                            "; it is on the axis of the part's round outline, which the centre lines locate, so it is not located in {} from that outline's tangent",
                            AXES[k]
                        );
                    }
                    continue; // on the turning axis: the centre line locates it
                }
                located.push((h.feature.id.clone(), h.at, k, view.into()));
            }
        }
    }
    out.extend(callouts);
    out.extend(pitches);

    // Locations from the part's box: one dimension per distinct distance (ARCH0009-6).
    let mut by: BTreeMap<(String, usize, i64), Vec<(String, P3)>> = BTreeMap::new();
    for (owner, p, k, view) in located {
        let d = p[k] - lo[k];
        if d.abs() < 1e-6 {
            problems.push(Problem {
                id: format!("p:location_on_datum:{owner}/{}", AXES[k]),
                code: "location_on_datum".into(),
                severity: "info".into(),
                message: format!(
                    "{} lies on the part's {} face, so its {} location measures nothing and is not dimensioned.",
                    crate::wording::capital(&crate::wording::feature_id(cx.analysis, &owner)),
                    ["left", "front", "bottom"][k],
                    AXES[k]
                ),
                subjects: vec![owner],
                ..Default::default()
            });
            continue;
        }
        by.entry((view, k, key(d))).or_default().push((owner, p));
    }
    let rank = |id: &str| {
        analysis
            .features
            .iter()
            .position(|f| f.id == id)
            .unwrap_or(usize::MAX)
    };
    for ((view, k, _), mut owners) in by {
        owners.sort_by_key(|(o, _)| (rank(o), o.clone()));
        owners.dedup_by(|a, b| a.0 == b.0);
        let (first, p) = &owners[0];
        let d = p[k] - lo[k];
        let parameter = format!("location.length.{}", AXES[k]);
        // Invariant: The selected view comes from the fixed principal view table.
        let view: &'static str = NAMES.iter().find(|n| **n == view).expect("a view");
        out.push(Want {
            id: want_id(first, &parameter),
            views: vec![view],
            value: d,
            terms: vec![fmt(d)],
            measurements: owners
                .iter()
                .map(|(o, _)| Measurement {
                    feature: o.clone(),
                    parameter: parameter.clone(),
                    value: d,
                })
                .collect(),
            turning: None,
            basis: format!(
                "located in {} from the part's {} face, in the view that looks along the hole{}",
                AXES[k],
                ["left", "front", "bottom"][k],
                if owners.len() > 1 {
                    "; one dimension serves every feature at this distance"
                } else {
                    ""
                }
            ),
            shape: Shape::Linear {
                a: {
                    let mut a = *p;
                    a[k] = lo[k];
                    a
                },
                b: *p,
                axis: k,
                a_edge: true,
                b_edge: false,
                order: 1,
            },
        });
    }
    (out, problems)
}

/// How far a hole or boss axis may lie from a round face's axis and still stand on it, mm:
/// quiddity's 0.01 mm record rounding.
const ON_AXIS: f64 = 0.01;

/// Where the part's low box side along *k*, seen along world axis *view*, is only the tangent of
/// a round face (no plane lies on it) whose axis carries a recognised hole or boss: that
/// feature's ID and a point on the face's axis (#223). A tangent on a curved outline is no
/// face to measure from; the axis of the round feature it bounds is.
fn round_origin(analysis: &Analysis, view: usize, k: usize) -> Option<(String, P3)> {
    let ink = analysis.edge_ink.as_ref()?;
    let lo = analysis.envelope[0][k];
    let flat = ink.planes.iter().any(|p| {
        p.normal[k].abs() > 1.0 - 1e-6
            && p.edges
                .iter()
                .filter_map(|&e| ink.edges.get(e)?.first())
                .any(|q| (q[k] - lo).abs() < 1e-4)
    });
    if flat {
        return None;
    }
    let (u, v) = in_view(view);
    let frame = holes::frame(analysis);
    let mut found: Option<(String, P3)> = None;
    for c in ink.curved.iter().filter_map(|c| {
        let y = c.cylinder.as_ref()?;
        (y.axis[view].abs() > 1.0 - 1e-6
            && (y.origin[k] - y.radius - lo).abs() < 1e-4
            && (c.bounds[0][k] - lo).abs() < 1e-4)
            .then_some(y)
    }) {
        let on =
            |at: P3| (at[u] - c.origin[u]).abs() < ON_AXIS && (at[v] - c.origin[v]).abs() < ON_AXIS;
        // A hole on the axis carries its centre mark there; else a boss standing on it.
        let owner = holes::read(analysis)
            .into_iter()
            .filter_map(|(f, h)| h.ok().map(|h| (f, h)))
            .find(|(_, h)| h.direction[view].abs() > 1.0 - 1e-6 && on(h.at))
            .map(|(f, _)| f.id.clone())
            .or_else(|| {
                analysis
                    .features
                    .iter()
                    .filter(|f| f.kind == "boss")
                    .find(|f| {
                        let at = point(f.parameters.get("location"));
                        let d = point(f.parameters.get("axis"));
                        at.zip(d).is_some_and(|(at, d)| {
                            holes::dir_to_part(&frame, d)[view].abs() > 1.0 - 1e-6
                                && on(holes::to_part(&frame, at))
                        })
                    })
                    .map(|f| f.id.clone())
            })
            .or_else(|| {
                analysis
                    .features
                    .iter()
                    .filter(|f| f.kind == "turned_body")
                    .find(|f| {
                        crate::lint::parameters::place(&frame, f.parameters.get("axis_point"))
                            .zip(crate::lint::parameters::direction(
                                &frame,
                                f.parameters.get("axis"),
                            ))
                            .is_some_and(|(at, d)| d[view].abs() > 1.0 - 1e-6 && on(at))
                    })
                    .map(|f| f.id.clone())
            });
        let Some(owner) = owner else { continue };
        match &found {
            Some((_, at)) if (at[k] - c.origin[k]).abs() > ON_AXIS => return None, // two round sides
            Some(_) => {}
            None => found = Some((owner, c.origin)),
        }
    }
    found
}

/// Whether one round face about *axis*, seen along world axis *view*, is the part's outline on
/// both box sides along *k*: its tangents are the box's low and high sides (its faces together,
/// when the cylinder is split).
fn round_whole(analysis: &Analysis, view: usize, k: usize, axis: P3) -> bool {
    let Some(ink) = analysis.edge_ink.as_ref() else {
        return false;
    };
    let [lo, hi] = analysis.envelope;
    let (u, v) = in_view(view);
    let reach = ink
        .curved
        .iter()
        .filter(|c| {
            c.cylinder.as_ref().is_some_and(|y| {
                y.axis[view].abs() > 1.0 - 1e-6
                    && (y.origin[u] - axis[u]).abs() < ON_AXIS
                    && (y.origin[v] - axis[v]).abs() < ON_AXIS
                    && (y.origin[k] - y.radius - lo[k]).abs() < 1e-4
                    && (y.origin[k] + y.radius - hi[k]).abs() < 1e-4
            })
        })
        .fold([f64::INFINITY, f64::NEG_INFINITY], |r, c| {
            [r[0].min(c.bounds[0][k]), r[1].max(c.bounds[1][k])]
        });
    (reach[0] - lo[k]).abs() < 1e-4 && (reach[1] - hi[k]).abs() < 1e-4
}

/// Re-roots hole and hole-pattern locations measured from a box side that is only a round
/// face's tangent ([`round_origin`]) on that round feature's axis (#223: linkrods' holes were
/// located 0.75 and 4.625 from a ø1.5 boss's tangent, and the 3.875 between their centres was
/// never stated). A feature on that axis is not located again: the dimensions from its centre
/// state it, as draftwright's location backstop reads a witness on a hole's centre. When nothing
/// stands off the round axis, its features are not located at all if the round face is the
/// part's whole outline along that axis ([`round_whole`], #104), and are left from the box
/// otherwise. An axis is left from the box when the Spec names its
/// `origin/<axis>` (`min` keeps the box side), or when the part carries PMI, whose datums own
/// its origins.
pub(super) fn round_origins(analysis: &Analysis, spec: &crate::model::Spec, wants: &mut Vec<Want>) {
    if super::pmi_policy::carries(analysis) {
        return;
    }
    let lo = analysis.envelope[0];
    let feature = |m: &Measurement, kinds: &[&str]| {
        analysis
            .features
            .iter()
            .any(|f| f.id == m.feature && kinds.contains(&f.kind.as_str()))
            || (kinds.contains(&"hole") && m.feature.starts_with("f:hole_pattern:"))
    };
    // A location from the low box side: its view's axis, the axis it measures, its station.
    let from_box = |w: &Want, kinds: &[&str]| match w.shape {
        Shape::Linear {
            a,
            b,
            axis: k,
            a_edge: true,
            order: 1,
            ..
        } if (a[k] - lo[k]).abs() < 1e-9
            && !w.measurements.is_empty()
            && w.measurements.iter().all(|m| {
                m.parameter == format!("location.length.{}", AXES[k]) && feature(m, kinds)
            }) =>
        {
            let view = (0..3).find(|&j| w.views.first() == Some(&end_on(j)))?;
            Some((view, k, b[k]))
        }
        _ => None,
    };
    let mut axes: BTreeSet<(usize, usize)> = BTreeSet::new();
    for w in wants.iter() {
        if let Some((view, k, _)) = from_box(w, &["hole"]) {
            axes.insert((view, k));
        }
    }
    for (view, k) in axes {
        if spec.origin.contains_key(AXES[k]) {
            continue;
        }
        let Some((owner, axis)) = round_origin(analysis, view, k) else {
            continue;
        };
        let c = axis[k];
        let on: Vec<usize> = (0..wants.len())
            .filter(|&i| {
                from_box(&wants[i], &["hole", "boss"])
                    .is_some_and(|(j, kk, at)| j == view && kk == k && (at - c).abs() < ON_AXIS)
            })
            .collect();
        let off: Vec<usize> = (0..wants.len())
            .filter(|&i| {
                from_box(&wants[i], &["hole"])
                    .is_some_and(|(j, kk, at)| j == view && kk == k && (at - c).abs() >= ON_AXIS)
            })
            .collect();
        if off.is_empty() {
            // The round face is the part's whole outline along *k* (a disc or ring seen end-on,
            // 608zz's bore in its ø22): what stands on its axis is concentric with the outline,
            // which the centre lines show, and a location from its tangent only repeats half the
            // outline's diameter (#104).
            if round_whole(analysis, view, k, axis) {
                let concentric: BTreeSet<String> = on
                    .iter()
                    .flat_map(|&i| wants[i].measurements.iter().map(|m| m.feature.clone()))
                    .collect();
                let mut i = 0;
                wants.retain(|_| {
                    i += 1;
                    !on.contains(&(i - 1))
                });
                let note = format!(
                    "; it is on the axis of the part's round outline, which the centre lines locate, so it is not located in {} from that outline's tangent",
                    AXES[k]
                );
                for w in wants.iter_mut().filter(|w| {
                    matches!(w.shape, Shape::Callout { .. })
                        && w.measurements
                            .iter()
                            .any(|m| concentric.contains(&m.feature))
                }) {
                    if !w.basis.ends_with(&note) {
                        w.basis += &note;
                    }
                }
            }
            continue;
        }
        let stated: Vec<Measurement> = on
            .iter()
            .flat_map(|&i| wants[i].measurements.clone())
            .collect();
        let named = crate::wording::feature_id(analysis, &owner);
        let (u, v) = in_view(view);
        let across = if u == k { v } else { u };
        // The nearest dimension from the axis also carries what stands on it.
        let nearest = off.iter().copied().min_by(|&x, &y| {
            let d = |i: usize| {
                from_box(&wants[i], &["hole"]).map_or(f64::MAX, |(_, _, at)| (at - c).abs())
            };
            d(x).total_cmp(&d(y))
        });
        for &i in &off {
            let w = &mut wants[i];
            let Shape::Linear { a, b, a_edge, .. } = &mut w.shape else {
                continue;
            };
            *a = *b;
            a[k] = c;
            a[across] = axis[across];
            *a_edge = false;
            let d = (b[k] - c).abs();
            w.value = d;
            w.terms = vec![fmt(d)];
            for m in &mut w.measurements {
                m.value = d;
            }
            w.basis = format!(
                "located in {} from the axis of {named}, in the view that looks along the hole: the part's {} side is only the tangent of its round face, which is no face to measure from",
                AXES[k],
                ["left", "front", "bottom"][k],
            );
            if Some(i) == nearest && !stated.is_empty() {
                let mut ids: Vec<String> = Vec::new();
                for m in &stated {
                    w.measurements.push(Measurement {
                        value: d,
                        ..m.clone()
                    });
                    let id = crate::wording::feature_id(analysis, &m.feature);
                    if !ids.contains(&id) {
                        ids.push(id);
                    }
                }
                w.basis += &format!(
                    "; {} on that axis is located by this dimension's witness on its centre",
                    ids.join(", ")
                );
            }
        }
        let mut i = 0;
        wants.retain(|_| {
            i += 1;
            !on.contains(&(i - 1))
        });
    }
}

/// The world axes a view looking along *k* shows across and up the page.
fn in_view(k: usize) -> (usize, usize) {
    match k {
        0 => (1, 2),
        1 => (0, 2),
        _ => (0, 1),
    }
}

fn distinct(values: impl Iterator<Item = f64>) -> Vec<f64> {
    let mut v: Vec<f64> = values.collect();
    v.sort_by(f64::total_cmp);
    v.dedup_by(|a, b| (*a - *b).abs() < 1e-4);
    v
}

fn even(values: &[f64]) -> bool {
    values.len() < 3
        || values
            .windows(2)
            .all(|w| ((w[1] - w[0]) - (values[1] - values[0])).abs() < 1e-4)
}

/// A grid's distinct column and row positions, when its members are an evenly spaced lattice
/// along the view's axes.
fn lattice(centres: &[P3], u: usize, v: usize) -> Option<(Vec<f64>, Vec<f64>)> {
    let cols = distinct(centres.iter().map(|c| c[u]));
    let rows = distinct(centres.iter().map(|c| c[v]));
    (even(&cols) && even(&rows)).then_some((cols, rows))
}

/// A grid's column and row positions and its centre, when its members, seen along world axis
/// *k*, are a full evenly spaced lattice: the grid is then located by that centre.
pub(crate) fn grid_lattice(centres: &[P3], k: usize) -> Option<(Vec<f64>, Vec<f64>, P3)> {
    let (u, v) = in_view(k);
    let (cols, rows) = lattice(centres, u, v)?;
    (cols.len() * rows.len() == centres.len()).then(|| {
        let mut c = centres[0];
        c[u] = 0.5 * (cols[0] + cols[cols.len() - 1]);
        c[v] = 0.5 * (rows[0] + rows[rows.len() - 1]);
        (cols, rows, c)
    })
}

/// A linear pattern's axis and its members' positions along it, when they lie evenly along one
/// of the view's axes.
fn line(centres: &[P3], u: usize, v: usize) -> Option<(usize, Vec<f64>)> {
    for (along, across) in [(u, v), (v, u)] {
        let a = distinct(centres.iter().map(|c| c[along]));
        let b = distinct(centres.iter().map(|c| c[across]));
        if b.len() == 1 && a.len() == centres.len() && even(&a) {
            return Some((along, a));
        }
    }
    None
}

fn located_pattern(
    located: &mut Vec<(String, P3, usize, String)>,
    id: &str,
    centre: P3,
    k: usize,
    turned: Option<&Turned>,
) {
    let (u, v) = in_view(k);
    for a in [u, v] {
        if let Some(t) = turned
            && a != t.axis
            && (centre[a] - t.on[a]).abs() < 1e-3
        {
            continue;
        }
        located.push((id.into(), centre, a, end_on(k).into()));
    }
}

fn pitch_want(
    pattern: &str,
    parameter: &str,
    view: &'static str,
    at: P3,
    along: usize,
    values: &[f64],
) -> Want {
    let p = values[1] - values[0];
    let intervals = values.len() - 1;
    let mut terms = Vec::new();
    let text = if intervals > 1 {
        format!("{intervals}× {}", fmt(p))
    } else {
        fmt(p)
    };
    terms.push(text);
    let (mut a, mut b) = (at, at);
    a[along] = values[0];
    b[along] = values[1];
    Want {
        id: want_id(pattern, parameter),
        views: vec![view],
        value: p,
        terms,
        measurements: vec![Measurement {
            feature: pattern.into(),
            parameter: parameter.into(),
            value: p,
        }],
        turning: None,
        basis: format!(
            "the pattern's spacing in {}, between its first two members, counted across its {intervals} interval{}",
            AXES[along],
            if intervals == 1 { "" } else { "s" }
        ),
        shape: Shape::Linear {
            a,
            b,
            axis: along,
            a_edge: false,
            b_edge: false,
            order: 0,
        },
    }
}

/// A kept item, as `dims::plan` holds it: the want, whether it is a default, the view that
/// carries it, and the views that look along it.
type Kept = (Want, bool, Option<&'static str>, Vec<&'static str>);

/// One span's tolerance, shared with lint's boss-location audit (lint may not read dims).
pub(crate) use crate::lint::location::SAME_SPAN;

/// What each [`rank`] locates, for the bases.
const NAMED: [&str; 3] = ["slot position", "boss location", "hole or pattern location"];

/// A location's rank among coincident ones (ARCH0009-6), higher surviving, or `None` for an item
/// that is no location from the part's box. draftwright's corridor dedup ranks a hole or pattern
/// location (`_locations.py`'s `_location_candidate`, `precedence=2`, which also carries a slot
/// pattern's `location_slot_pattern`, as `render_locations` takes every z location but
/// `location_slot`) above a slot position line (`_slots.py`, `precedence=1 if is_pos`). It
/// locates no boss; the port's boss location ranks between them: a hole down the same point
/// states it as well, and it is a located feature rather than a slot's position line.
fn rank(analysis: &Analysis, w: &Want) -> Option<u8> {
    let lead = w.measurements.first()?;
    // These requirements retain every role through share_statements, including shared
    // mouth datums. The historical hole-location survivor keeps only its own claim.
    if lead.feature.starts_with("notch:") || lead.feature.starts_with("f:oriented_chamfer:") {
        return None;
    }
    let p = lead.parameter.as_str();
    if p.starts_with("location.length.") {
        let boss = analysis
            .features
            .iter()
            .any(|f| f.id == lead.feature && matches!(f.kind.as_str(), "boss" | "polygonal_boss"));
        Some(if boss { 1 } else { 2 })
    } else if p.starts_with("location_slot_pattern.length.") {
        Some(2)
    } else if matches!(p, "location_slot.length" | "location_slot_across.length") {
        Some(0)
    } else {
        None
    }
}

/// One span, one dimension (ARCH0009-6, draftwright's `_ordered_corridor_candidates`): kept
/// locations that measure the same span (the same view and axis, the same raw origin and
/// endpoint, and the same label, its tolerance included, so different stated precision never
/// collapses; two defaults' nominals may differ in printed places only) are drawn once. The
/// survivor is the highest [`rank`]ed, then one the spec lists over a default (draftwright's
/// pinned location, `precedence=3`), then the smaller ID; each displaced one moves to
/// *omitted*, `conveyed by` the survivor, and is not reported as a drop.
/// The survivor's measurements stay its own: draftwright's survivor also claims the displaced
/// ones' (`_group_measurements`), but a location dimension here states one feature's axis
/// (ISSUE-883-a), and the omitted entry records what it conveys. Coincidence is decided among the
/// kept items, so omitting the survivor promotes its top loser. *given* (what the spec gave each
/// kept item) stays aligned with *kept*.
pub(super) fn consolidate(
    analysis: &Analysis,
    kept: &mut Vec<Kept>,
    given: &mut Vec<Vec<String>>,
    omitted: &mut Vec<Omitted>,
) {
    merge(kept, given, omitted, |w| rank(analysis, w));
}

fn merge(
    kept: &mut Vec<Kept>,
    given: &mut Vec<Vec<String>>,
    omitted: &mut Vec<Omitted>,
    rank: impl Fn(&Want) -> Option<u8>,
) {
    // The span each location measures: its view, axis, raw ends on that axis, label, and whether
    // it is a default (its label then the planner's own, not a precision the spec states).
    type Span = (&'static str, usize, f64, f64, String, bool);
    let span = |(w, default, view, _): &Kept| match w.shape {
        Shape::Linear { a, b, axis, .. } => Some((
            view.unwrap_or(w.views[0]),
            axis,
            a[axis],
            b[axis],
            w.terms.join(" "),
            *default,
        )),
        Shape::Callout { .. } | Shape::Angular { .. } | Shape::Aligned { .. } => None,
    };
    // Two defaults' labels also agree when they differ only in a nominal's printed places: the
    // three-place limit prints 224.999973 as `225.000`, the same distance as `225`.
    let same_label = |s: &Span, t: &Span| {
        s.4 == t.4
            || (s.5 && t.5 && {
                let (x, y): (Vec<&str>, Vec<&str>) =
                    (s.4.split(' ').collect(), t.4.split(' ').collect());
                x.len() == y.len()
                    && x.iter().zip(&y).all(|(p, q)| {
                        p == q
                            || matches!((p.parse::<f64>(), q.parse::<f64>()),
                                (Ok(m), Ok(n)) if (m - n).abs() <= SAME_SPAN)
                    })
            })
    };
    let same = |s: &Span, t: &Span| {
        s.0 == t.0
            && s.1 == t.1
            && (s.2 - t.2).abs() <= SAME_SPAN
            && (s.3 - t.3).abs() <= SAME_SPAN
            && same_label(s, t)
    };
    let mut groups: Vec<(Span, Vec<usize>)> = Vec::new();
    for (i, k) in kept.iter().enumerate() {
        let (Some(_), Some(s)) = (rank(&k.0), span(k)) else {
            continue;
        };
        match groups.iter_mut().find(|(t, _)| same(&s, t)) {
            Some((_, members)) => members.push(i),
            None => groups.push((s, vec![i])),
        }
    }
    let mut gone = BTreeSet::new();
    for (s, mut members) in groups.into_iter().filter(|(_, m)| m.len() > 1) {
        members.sort_by(|&x, &y| {
            let order = |i: usize| (rank(&kept[i].0), !kept[i].1);
            order(y)
                .cmp(&order(x))
                .then_with(|| kept[x].0.id.cmp(&kept[y].0.id))
        });
        // Invariant: The group was filtered to at least two members before split_first.
        let (winner, losers) = members.split_first().expect("a group of two or more");
        let owner = kept[*winner].0.id.clone();
        let top = rank(&kept[*winner].0);
        let mut conveyed = Vec::new();
        for &i in losers {
            let w = &kept[i].0;
            let why = if top > rank(w) {
                format!(
                    "a {} outranks a {}",
                    NAMED[top.map_or(0, usize::from)],
                    NAMED[rank(w).map_or(0, usize::from)]
                )
            } else if kept[*winner].1 != kept[i].1 {
                "the spec lists it".into()
            } else {
                "of equal rank, its ID is the first".into()
            };
            omitted.push(Omitted {
                id: w.id.clone(),
                label: w.terms.join(" "),
                basis: format!(
                    "conveyed by {owner}: it locates the same span in {} ({}) from the part's box, in the same view, and {why} (one span, one dimension: ARCH0009-6)",
                    AXES[s.1], s.4
                ),
                state: crate::items::State::Covered {
                    by: owner.clone(),
                },
            });
            conveyed.push(w.id.clone());
            gone.insert(i);
        }
        kept[*winner].0.basis += &format!(
            "; one span, one dimension: it also conveys the coincident {} (ARCH0009-6)",
            conveyed.join(", ")
        );
    }
    let mut i = 0;
    kept.retain(|_| {
        i += 1;
        !gone.contains(&(i - 1))
    });
    let mut i = 0;
    given.retain(|_| {
        i += 1;
        !gone.contains(&(i - 1))
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A default location of *feature* in the plan, from x = 0 to *at*, labelled *label*.
    fn location(feature: &str, parameter: &str, at: f64, label: &str) -> Kept {
        let want = Want {
            id: want_id(feature, parameter),
            views: vec!["plan"],
            value: at,
            terms: vec![label.into()],
            measurements: vec![Measurement {
                feature: feature.into(),
                parameter: parameter.into(),
                value: at,
            }],
            basis: String::new(),
            shape: Shape::Linear {
                a: [0.0, 5.0, 0.0],
                b: [at, 5.0, 0.0],
                axis: 0,
                a_edge: true,
                b_edge: false,
                order: 1,
            },
            turning: None,
        };
        (want, true, Some("plan"), vec![])
    }

    /// The kept IDs and the omitted ones with their owners, after merging under a rank by
    /// parameter as `rank` gives it (bosses told by their ID).
    fn merged(mut kept: Vec<Kept>) -> (Vec<String>, Vec<(String, String)>) {
        let mut given = vec![Vec::new(); kept.len()];
        let mut omitted = Vec::new();
        merge(&mut kept, &mut given, &mut omitted, |w| {
            let m = &w.measurements[0];
            match m.parameter.as_str() {
                p if p.starts_with("location.length.") && m.feature.contains("boss") => Some(1),
                p if p.starts_with("location.length.") => Some(2),
                "location_slot_across.length" => Some(0),
                _ => None,
            }
        });
        assert_eq!(given.len(), kept.len());
        let owner = |b: &str| b["conveyed by ".len()..b.find(": ").unwrap()].to_string();
        (
            kept.into_iter().map(|k| k.0.id).collect(),
            omitted
                .into_iter()
                .map(|o| (o.id, owner(&o.basis)))
                .collect(),
        )
    }

    #[test]
    fn a_hole_location_outranks_a_coincident_boss_and_slot_location() {
        let (kept, gone) = merged(vec![
            location("f:slot:1", "location_slot_across.length", 40.0, "40"),
            location("f:boss:1", "location.length.x", 40.0, "40"),
            location("f:hole:1", "location.length.x", 40.0, "40"),
        ]);
        let hole = "d:f:hole:1/location.length.x";
        assert_eq!(kept, [hole]);
        assert_eq!(
            gone,
            [
                ("d:f:boss:1/location.length.x".into(), hole.into()),
                ("d:f:slot:1/location_slot_across.length".into(), hole.into()),
            ]
        );
        // Omitting the hole's (it is then not kept) promotes the boss's, which conveys the slot's.
        let (kept, gone) = merged(vec![
            location("f:slot:1", "location_slot_across.length", 40.0, "40"),
            location("f:boss:1", "location.length.x", 40.0, "40"),
        ]);
        assert_eq!(kept, ["d:f:boss:1/location.length.x"]);
        assert_eq!(gone.len(), 1);
    }

    #[test]
    fn coincidence_is_decided_on_raw_ends_and_the_label() {
        // 40.02 and 40 both print 40 when rounded, but are different spans: both are kept.
        let (kept, gone) = merged(vec![
            location("f:hole:1", "location.length.x", 40.02, "40"),
            location("f:boss:1", "location.length.x", 40.0, "40"),
        ]);
        assert_eq!(kept.len(), 2, "{gone:?}");
        // The same span with a different stated precision (a tolerance) is not collapsed either.
        let (kept, _) = merged(vec![
            location("f:hole:1", "location.length.x", 40.0, "40 ±0.1"),
            location("f:boss:1", "location.length.x", 40.0, "40"),
        ]);
        assert_eq!(kept.len(), 2);
        // Round-off below a micrometre is the same span.
        let (kept, _) = merged(vec![
            location("f:hole:1", "location.length.x", 40.0 + 1e-9, "40"),
            location("f:boss:1", "location.length.x", 40.0, "40"),
        ]);
        assert_eq!(kept, ["d:f:hole:1/location.length.x"]);
        // ctc01: a boss centre averaged from its corners, 2.7e-5 short, prints `225.000` beside
        // the holes' `225`; it is the same span, stated once, by the hole location.
        let (kept, gone) = merged(vec![
            location(
                "f:polygonal_boss:1",
                "location.length.x",
                224.999973,
                "225.000",
            ),
            location("f:hole:1", "location.length.x", 225.0, "225"),
        ]);
        assert_eq!(kept, ["d:f:hole:1/location.length.x"]);
        assert_eq!(gone.len(), 1);
        // A precision the spec states is never collapsed by that rule.
        let mut listed = location("f:boss:1", "location.length.x", 225.0, "225.00");
        listed.1 = false;
        let (kept, _) = merged(vec![
            listed,
            location("f:hole:1", "location.length.x", 225.0, "225"),
        ]);
        assert_eq!(kept.len(), 2);
    }

    #[test]
    fn the_spec_lists_then_the_smaller_id_survives_among_equals() {
        let (kept, _) = merged(vec![
            location("f:hole:2", "location.length.x", 40.0, "40"),
            location("f:hole:1", "location.length.x", 40.0, "40"),
        ]);
        assert_eq!(kept, ["d:f:hole:1/location.length.x"]);
        let mut listed = location("f:hole:2", "location.length.x", 40.0, "40");
        listed.1 = false;
        let (kept, _) = merged(vec![
            listed,
            location("f:hole:1", "location.length.x", 40.0, "40"),
        ]);
        assert_eq!(kept, ["d:f:hole:2/location.length.x"]);
    }

    #[test]
    fn an_inclined_axis_is_seen_most_nearly_end_on_first_and_never_edge_on() {
        // ctc04's counterbored holes: 22° off z, in the y–z plane, so never seen in the side view.
        assert_eq!(oblique_axes([0.0, -0.375, 0.927]), [2, 1]);
        // cgb217's ⌀10.2: 13° off x.
        assert_eq!(oblique_axes([-0.974, -0.226, 0.0]), [0, 1]);
        // ctc03's holes at 45° between x and z: a tie, the first axis taken.
        let h = std::f64::consts::FRAC_1_SQRT_2;
        assert_eq!(oblique_axes([h, 0.0, -h]), [0, 2]);
    }

    #[test]
    fn rim_tips_lie_on_the_circle_at_its_full_radius_across_the_view() {
        let d = [0.0, -0.375f64, 0.927];
        let n = (d[1] * d[1] + d[2] * d[2]).sqrt();
        let d = [0.0, d[1] / n, d[2] / n];
        let c = [10.0, 20.0, 30.0];
        for k in oblique_axes(d) {
            let tips = rim_tips(c, d, 6.0, k);
            for t in &tips {
                let p = t.point(t.start);
                let r: Vec<f64> = (0..3).map(|j| p[j] - c[j]).collect();
                // On the circle: at its radius from the centre, square to the axis.
                assert!((r.iter().map(|x| x * x).sum::<f64>().sqrt() - 6.0).abs() < 1e-9);
                assert!((0..3).map(|j| r[j] * d[j]).sum::<f64>().abs() < 1e-9);
                // Square to the view's axis, so the view sees the full radius there.
                assert!(r[k].abs() < 1e-9);
                assert_eq!(t.sweep, 0.0);
            }
            let [a, b] = tips.map(|t| t.point(t.start));
            assert!((0..3).all(|j| (a[j] + b[j] - 2.0 * c[j]).abs() < 1e-9));
        }
    }
}
