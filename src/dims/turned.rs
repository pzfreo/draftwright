//! Turned parts: the overall diameter and length of a part turned whole, and each turned step's
//! own diameter and length (draftwright's `render_rotational`, `render_diameters` and
//! `render_step_lengths`, over the groups `model/compiled.py` approves).
//!
//! - **Bands** are the Analysis's `turned_step` features that no groove owns (lint's
//!   [`bands`]: draftwright's `groove_owns_turned_step_band`, the groove's callout states that
//!   band), each read in the part's frame. When groove ownership cannot be decided, or a record
//!   cannot be read, the step is reported (`feature_not_dimensioned`), never dropped or guessed.
//! - **The overall diameter** of a part turned whole is `envelope`/`od.diameter`, in a view that
//!   shows the profile. It also states the diameter of the band it is
//!   (`_share_unique_outer_diameter`): the one band of the largest diameter on the part's axis
//!   line, or several such bands with separate spans inside the box (`2× ø130`, two flanges;
//!   `ø15` for three lands between grooves: beyond draftwright, whose three or more each got
//!   their own diameter, the OD stated again per land, #220).
//! - **A step's diameter** is `step.diameter`, a callout `ø6` led to the band's silhouette at
//!   its middle, in the front view for a band along x or z (draftwright's row below and column
//!   left of the front view; the next profile view when the front is omitted), and led to its
//!   circle in the front view, which looks along it, for a band along y (draftwright's radial
//!   leaders). A band along z whose diameter prints as a coaxial hole's bore, counterbore or
//!   spotface does reads `ø70 OD` (`_external_diameter_rider`), and exactly two bands along z
//!   with the same diameter at different stations share one `2× ø6` callout
//!   (`_counted_column_items`). A band that owns an AP242 thread or knurl
//!   ([`crate::pmi_lowering::manufacturing`]) prints it after its diameter (`ø5 SEE MFG 3`), on a
//!   callout of its own, or on the overall diameter when that states the band alone (beyond
//!   draftwright, whose overall diameter prints none and whose lint then finds it not drawn).
//!   A band that flats cut reads first in the profile view where its outline is the cylinder,
//!   and its leader goes to the proven point of its trimmed cylinder on the chosen view's
//!   outline (the edge on the flat, where the flats face the view), or to the arcs between the
//!   flats seen along it ([`route_profile`], ISSUE-1374-d).
//! - **A step's length** is `step.length`, the band's span along its axis, in its first profile
//!   view (front for x and z, side for y), its witnesses at the band's shoulders: a chain, one
//!   item per band, for each turned profile (the bands of one axis line and one recorded
//!   profile, `_profile_groups_from_plan`). Three or more bands of one profile within 10 % of
//!   their mean length are one `N× L` (`_draw_step_chain`'s collapse) when each prints as `L`
//!   (draftwright collapses them whatever they print, so its `3× 23.3` can stand for a 23 and a
//!   23.7); along y, each contiguous run of three or more equal lengths is one `N× L` instead
//!   (`_render_y_profile`).
//! - **A baseline from the datum** replaces the chain of a profile that runs end to end of a part
//!   turned whole by default. PMI keeps first say (#33; [`datum_end`]): a planar
//!   datum feature on that end face (beyond draftwright), or PMI lengths from that end to its
//!   shoulders (draftwright's `add_authored_step_positions`). The band at the datum keeps its
//!   length, each further shoulder is located from the datum (`step_position.length` of the band
//!   it ends), and the overall length is the last rung, so no member is reference. A PMI length
//!   across the same span takes its rung's place (`share_pmi`).
//! - **The overall length** of a part turned whole is stated only when its profile's chain (its
//!   bands' and grooves' spans, adjoining) does not run end to end along the axis: a surviving
//!   chain conveys it (`_step_chain_covers_extent`; ISSUE-955-a's "a surviving chain does not
//!   release a redundant height").
//!
//! Enlarged crowded-head and Y-chain recovery is planned at candidate scale in
//! [`crate::detail`], retaining these exact IDs. Not ported: dropping the smallest diameters on
//! overflow and stagger/dense-chain drops. Every item remains planned, and placement reports
//! what collides. A part of several turned bodies along one axis gives each body's chain the
//! view that separates its axis line and witnesses on its own silhouette ([`own_body`], ARCH0020-2).

use std::collections::{BTreeMap, BTreeSet};

use serde_json::Value;

use super::envelope::{corner, profile_views};
use super::grooves::not_dimensioned;
use super::{AXES, Context, Measurement, P3, Planner, Problem, Shape, Want, fmt, point, want_id};
use crate::bridge::{Feature, holes};
use crate::lint::turned::{Band, band, bands};
use crate::model::Analysis;
use crate::pmi_lowering::Manufacturing;

pub(super) const PLANNER: Planner = Planner {
    kinds: &["turned_step", "turned_body"],
    wants,
};

/// A turned step and its band, read in the part's frame.
type Banded<'a> = (&'a Feature, Band);

/// A part turned whole: its axis (a world axis), a point on it, and its largest diameter, when
/// every turned step shares one principal axis and the largest step spans the part's box across
/// it.
pub(super) struct Turned {
    pub(super) axis: usize,
    pub(super) on: P3,
    pub(super) diameter: f64,
}

pub(super) fn turned(analysis: &Analysis, frame: &super::QFrame) -> Option<Turned> {
    if let Some((axis, on, diameter)) = crate::bridge::turned_body(analysis) {
        return Some(Turned { axis, on, diameter });
    }
    let steps: Vec<&Feature> = analysis
        .features
        .iter()
        .filter(|f| f.kind == "turned_step")
        .collect();
    let mut found: Option<(usize, P3)> = None;
    let mut diameter: f64 = 0.0;
    for s in &steps {
        let p = &s.parameters;
        let k = holes::principal(frame.letter(p.get("axis")?)?)?;
        let on = frame.point(point(p.get("profile")?.get("axis_origin"))?);
        if let Some((k0, on0)) = found
            && (k0 != k || (0..3).any(|j| j != k && (on[j] - on0[j]).abs() > 1e-3))
        {
            return None;
        }
        found = Some((k, on));
        diameter = diameter.max(p.get("diameter")?.as_f64()?);
    }
    let (axis, on) = found?;
    let [lo, hi] = analysis.envelope;
    let whole = (0..3)
        .filter(|&j| j != axis)
        .all(|j| ((hi[j] - lo[j]) - diameter).abs() < 1e-3 * diameter.max(1.0));
    whole.then_some(Turned { axis, on, diameter })
}

/// Whether two points lie on one line along world axis *k* (within 1e-3 mm across it).
fn same_line(k: usize, a: P3, b: P3) -> bool {
    (0..3).all(|j| j == k || (a[j] - b[j]).abs() <= 1e-3)
}

/// The bands the overall diameter of part *t* also states: the one band of its diameter on its
/// axis line, or several with separate spans inside the box (`_share_unique_outer_diameter`,
/// with `_two_distinct_od_bands`, beyond draftwright for three or more: #220); none when two of
/// them adjoin or overlap.
fn shared<'a>(t: &Turned, read: &'a [Banded<'a>], envelope: [P3; 2]) -> Vec<&'a Banded<'a>> {
    let matches: Vec<&Banded> = read
        .iter()
        .filter(|(_, b)| b.axis == t.axis && same_line(t.axis, b.on, t.on))
        .filter(|(_, b)| (b.diameter - t.diameter).abs() <= 1e-6)
        .collect();
    let k = t.axis;
    let [lo, hi] = envelope;
    let inside = |c: &Band| lo[k] - 1e-6 <= c.lo && c.lo < c.hi && c.hi <= hi[k] + 1e-6;
    let apart = |a: &Band, b: &Band| a.hi < b.lo - 1e-6 || b.hi < a.lo - 1e-6;
    let separate = matches.len() == 1
        || matches
            .iter()
            .enumerate()
            .all(|(i, (_, a))| inside(a) && matches[i + 1..].iter().all(|(_, b)| apart(a, b)));
    if separate { matches } else { Vec::new() }
}

/// Whether the bands and grooves of one turned profile adjoin from one end of the part's box to
/// the other along world axis *k* (draftwright's `_selected_chain_covers_extent`: shared stations
/// within 1e-3 mm, overlapping spans do not join).
fn chain_covers(profiles: &[Vec<(f64, f64)>], envelope: [P3; 2], k: usize) -> bool {
    let tol = 1e-3 + 1e-9;
    let [lo, hi] = envelope;
    profiles.iter().any(|spans| {
        let mut sorted = spans.clone();
        sorted.sort_by(|a, b| a.0.total_cmp(&b.0).then(a.1.total_cmp(&b.1)));
        let mut reachable = vec![lo[k]];
        for (a, b) in sorted {
            if reachable.iter().any(|s| (a - s).abs() <= tol) {
                reachable.push(b);
            }
        }
        reachable.iter().any(|s| (s - hi[k]).abs() <= tol)
    })
}

/// The grooves' spans along their axes, by the profile their record names, in the part's frame:
/// (profile, axis, a point on the axis, low end, high end).
fn groove_spans<'a>(cx: &'a Context) -> Vec<(Option<&'a Value>, usize, P3, f64, f64)> {
    let mut out = Vec::new();
    for g in cx.analysis.features.iter().filter(|f| f.kind == "groove") {
        let p = &g.parameters;
        let (Some(at), Some(axis), Some(width)) = (
            point(p.get("at")),
            p.get("axis").and_then(|a| cx.frame.letter(a)),
            p.get("width").and_then(Value::as_f64),
        ) else {
            continue;
        };
        let Some(k) = holes::principal(axis) else {
            continue;
        };
        let at = cx.frame.point(at);
        out.push((
            p.get("profile"),
            k,
            at,
            at[k] - 0.5 * width,
            at[k] + 0.5 * width,
        ));
    }
    out
}

/// The diameters a hole coaxial with a band along *k* through *on* prints (its bore, counterbore
/// and spotface), as `fmt` prints them.
fn coaxial_bores(analysis: &Analysis, k: usize, on: P3) -> Vec<String> {
    let mut out = Vec::new();
    for (_, h) in holes::read(analysis) {
        let Ok(h) = h else { continue };
        if h.principal.map(|(a, _)| a) != Some(k) || !same_line(k, h.at, on) {
            continue;
        }
        out.push(fmt(h.diameter));
        for r in h.recesses() {
            out.push(fmt(r.diameter));
        }
    }
    out
}

/// The leader sites of a band's diameter: its silhouette at its middle, across each radial axis
/// (in a profile view); or, along y, its circle (in the front view, which looks along it).
fn sites(b: &Band) -> Vec<(P3, f64)> {
    let mut mid = b.on;
    mid[b.axis] = 0.5 * (b.lo + b.hi);
    let r = 0.5 * b.diameter;
    if b.axis == 1 {
        return vec![(mid, r)];
    }
    (0..3)
        .filter(|&j| j != b.axis)
        .flat_map(|j| {
            [-r, r].map(|d| {
                let mut s = mid;
                s[j] += d;
                (s, 0.0)
            })
        })
        .collect()
}

/// The views a band's diameter reads in, preferred first. A band that flats cut reads first
/// where its outline is the cylinder itself, when one profile view shows it uncut.
fn diameter_views(analysis: &Analysis, b: &Band) -> Vec<&'static str> {
    if b.axis == 1 {
        return vec![super::end_on(1)];
    }
    let mut views = profile_views(b.axis);
    let cuts = crate::lint::turned::cuts(analysis, b);
    if !cuts.is_empty() {
        let mid = 0.5 * (b.lo + b.hi);
        let r = 0.5 * b.diameter;
        let whole = |v: &str| {
            across(b.axis, v).is_some_and(|d| {
                [d, d.map(|x| -x)].iter().all(|&d| {
                    crate::lint::turned::extreme(b, &cuts, mid, d).is_some_and(|p| {
                        (0..3).map(|i| (p[i] - b.on[i]) * d[i]).sum::<f64>() > r - 1e-6
                    })
                })
            })
        };
        if !whole(views[0]) && whole(views[1]) {
            views.swap(0, 1);
        }
    }
    views
}

/// The unit direction square to axis *k* that view *v* shows across it (`toward × axis`), when
/// *v* is a principal view that does not look along *k*.
fn across(k: usize, v: &str) -> Option<P3> {
    let (_, t) = super::TOWARD.iter().find(|(n, _)| *n == v)?;
    if t[k] != 0.0 {
        return None;
    }
    let mut a = [0.0; 3];
    a[k] = 1.0;
    Some([
        t[1] * a[2] - t[2] * a[1],
        t[2] * a[0] - t[0] * a[2],
        t[0] * a[1] - t[1] * a[0],
    ])
}

/// The leader sites of a turned step's diameter in view *view* once it is chosen, for a band that
/// flats cut: the proven points of its trimmed cylinder on the view's outline (each side of its
/// axis), or its trimmed circle's arcs in a view looking along it. A band no flat cuts keeps
/// [`sites`], which are those points already.
pub(super) fn route_profile(analysis: &Analysis, w: &mut Want, view: &str) {
    let Shape::Callout { sites, arcs } = &mut w.shape else {
        return;
    };
    if w.turning.is_none() || !super::TOWARD.iter().any(|(n, _)| *n == view) {
        return;
    }
    let frame = holes::frame(analysis);
    let (mut points, mut circles, mut cut) = (Vec::new(), Vec::new(), false);
    for m in w
        .measurements
        .iter()
        .filter(|m| m.parameter == "step.diameter")
    {
        let Some(Ok(b)) = analysis
            .features
            .iter()
            .find(|f| f.id == m.feature)
            .map(|f| band(&frame, f))
        else {
            return;
        };
        let cuts = crate::lint::turned::cuts(analysis, &b);
        cut |= !cuts.is_empty();
        let mid = 0.5 * (b.lo + b.hi);
        match across(b.axis, view) {
            Some(d) => points.extend(
                [d, d.map(|x| -x)]
                    .into_iter()
                    .filter_map(|d| crate::lint::turned::extreme(&b, &cuts, mid, d))
                    .map(|p| (p, 0.0)),
            ),
            None => circles.extend(crate::lint::turned::arcs(&b, &cuts, mid)),
        }
    }
    if !cut {
        return;
    }
    if circles.is_empty() {
        *sites = points;
        arcs.clear();
    } else {
        *arcs = circles;
    }
}

/// The steps' diameters, those the overall diameter does not state.
fn diameters(
    cx: &Context,
    read: &[Banded],
    carried: &[&str],
    manufacturing: &Manufacturing,
) -> Vec<Want> {
    let fmt = |v| crate::dims::fmt_in(v, cx.spec.units);
    let mut items: Vec<(Vec<&Banded>, Vec<String>)> = Vec::new();
    for fb in read {
        let (f, b) = fb;
        if carried.contains(&f.id.as_str()) {
            continue;
        }
        let text = fmt(b.diameter);
        let mut terms = vec![format!("ø{text}")];
        // An external thread's designation stands in place of the diameter (ISO 6410).
        if manufacturing.replaces_diameter(&f.id) {
            terms.clear();
        }
        // A thread or knurl the band carries follows its diameter (`_manufacturing_suffix`),
        // before the `OD` rider (`_external_diameter_rider`).
        terms.extend(manufacturing.terms(&f.id).0);
        if b.axis == 2 && coaxial_bores(cx.analysis, b.axis, b.on).contains(&text) {
            terms.push("OD".into());
        }
        items.push((vec![fb], terms));
    }
    // Along z, exactly two bands of one printed diameter at different stations share a callout.
    let mut by_text: BTreeMap<(i64, Vec<String>), Vec<usize>> = BTreeMap::new();
    for (i, (members, terms)) in items.iter().enumerate() {
        let (f, b) = members[0];
        // A band carrying a thread or knurl keeps its own callout (one annotation per owner).
        if b.axis == 2 && manufacturing.on(&f.id).is_empty() {
            by_text
                .entry((super::key(b.diameter), terms.clone()))
                .or_default()
                .push(i);
        }
    }
    let mut gone = Vec::new();
    for pair in by_text.values() {
        if let [i, j] = pair[..] {
            let (a, b) = (&items[i].0[0].1, &items[j].0[0].1);
            if (a.lo + a.hi - b.lo - b.hi).abs() > 1e-9 {
                let second = items[j].0[0];
                items[i].0.push(second);
                items[i].1.insert(0, "2×".into());
                gone.push(j);
            }
        }
    }
    items
        .into_iter()
        .enumerate()
        .filter(|(i, _)| !gone.contains(i))
        .map(|(_, (members, terms))| {
            let (f, b) = members[0];
            let views = diameter_views(cx.analysis, b);
            let n = members.len();
            Want { units: cx.spec.units,
                id: want_id(&f.id, "step.diameter"),
                views,
                value: b.diameter,
                measurements: members
                    .iter()
                    .map(|(g, c)| Measurement {
                        feature: g.id.clone(),
                        parameter: "step.diameter".into(),
                        value: c.diameter,
                    })
                    .chain(claims(manufacturing, ids(&members), b.diameter))
                    .collect(),
                basis: format!("{}{}", if n == 2 {
                    format!(
                        "two turned steps about {} have one diameter at different stations, so one callout states both (draftwright's counted column), in a view that shows the profile",
                        AXES[b.axis]
                    )
                } else if b.axis == 1 {
                    "a turned step's diameter, led to its circle in the front view, which looks along y (draftwright's radial leaders)".into()
                } else {
                    format!(
                        "a turned step's diameter, led to its silhouette in a view that shows the profile about {}{}",
                        AXES[b.axis],
                        if terms.last().is_some_and(|t| t == "OD") {
                            "; OD, as a coaxial bore prints the same size"
                        } else {
                            ""
                        }
                    )
                }, carries(manufacturing, &ids(&members), "turned step")),
                terms,
                shape: Shape::Callout { arcs: Vec::new(),
                    sites: members.iter().flat_map(|(_, c)| sites(c)).collect(),
                },
                turning: Some(b.axis),
            }
        })
        .collect()
}

/// One step-length item: from *first* (its ID's band) over *members*' spans, printing *terms*.
fn length(first: &Feature, members: &[&Banded], terms: Vec<String>, basis: String) -> Want {
    let b0 = &members[0].1;
    let k = b0.axis;
    let lo = members
        .iter()
        .map(|(_, b)| b.lo)
        .fold(f64::INFINITY, f64::min);
    let hi = members
        .iter()
        .map(|(_, b)| b.hi)
        .fold(f64::NEG_INFINITY, f64::max);
    let views = profile_views(k);
    Want {
        units: Default::default(),
        id: want_id(&first.id, "step.length"),
        views,
        value: if members.len() == 1 {
            hi - lo
        } else {
            mean(members)
        },
        terms,
        measurements: members
            .iter()
            .map(|(g, b)| Measurement {
                feature: g.id.clone(),
                parameter: "step.length".into(),
                value: b.hi - b.lo,
            })
            .collect(),
        basis,
        shape: Shape::Linear {
            a: corner(k, lo, b0.on),
            b: corner(k, hi, b0.on),
            axis: k,
            a_edge: true,
            b_edge: true,
            order: 1,
        },
        turning: None,
    }
}

/// The claims a diameter *value* across makes on the AP242 threads and knurls the features
/// *ids* carry: one measurement of feature `pmi:<source>` each.
pub(super) fn claims<'a>(
    manufacturing: &'a Manufacturing,
    ids: Vec<&'a str>,
    value: f64,
) -> impl Iterator<Item = Measurement> + 'a {
    ids.into_iter().flat_map(move |id| {
        manufacturing.on(id).into_iter().map(move |o| Measurement {
            feature: o.id.clone(),
            parameter: o.aspect.into(),
            value,
        })
    })
}

/// The clause a diameter's basis gives the threads and knurls the features *ids* carry, each
/// a *noun* (a turned step, a boss), or none.
pub(super) fn carries(manufacturing: &Manufacturing, ids: &[&str], noun: &str) -> String {
    let sources: Vec<&str> = ids
        .iter()
        .flat_map(|id| manufacturing.on(id))
        .map(|o| o.source.as_str())
        .collect();
    if sources.is_empty() {
        return String::new();
    }
    // The spec's, when the Spec authors one ([`crate::spec_notes`]).
    let author = if sources
        .iter()
        .any(|s| s.starts_with(crate::spec_gdt::SOURCE_PREFIX))
    {
        "the spec's"
    } else {
        "the part's AP242"
    };
    format!(
        "; it carries {author} thread or knurl ({}), {}, as draftwright gives one to the {noun} whose cylinder it names (lower_ap242_manufacturing_requirements)",
        sources.join(", "),
        if manufacturing.tags.is_empty() {
            "printed after its diameter"
        } else {
            "referred to the manufacturing requirements table"
        }
    )
}

/// The IDs of *members*' features.
fn ids<'a>(members: &[&'a Banded]) -> Vec<&'a str> {
    members.iter().map(|(f, _)| f.id.as_str()).collect()
}

fn mean(members: &[&Banded]) -> f64 {
    members.iter().map(|(_, b)| b.hi - b.lo).sum::<f64>() / members.len() as f64
}

/// One profile's step lengths, its bands in order along the axis.
fn chain(bands: &[&Banded], units: crate::units::Units) -> Vec<Want> {
    let fmt = |v| crate::dims::fmt_in(v, units);
    let k = bands[0].1.axis;
    let one = |m: &Banded| {
        length(
            m.0,
            &[m],
            vec![fmt(m.1.hi - m.1.lo)],
            format!(
                "a turned step's length along {}, one link of its profile's chain, in a view that shows the profile",
                AXES[k]
            ),
        )
    };
    let repeated = |run: &[&Banded], value: f64, why: &str| {
        length(
            run[0].0,
            run,
            vec![format!("{}×", run.len()), fmt(value)],
            format!(
                "{} turned steps along {} {why}, so one length states them all ({}× {}), in a view that shows the profile",
                run.len(),
                AXES[k],
                run.len(),
                fmt(value)
            ),
        )
    };
    if k == 1 {
        // Along y: each contiguous run of three or more equal lengths is one `N× L`.
        let mut out = Vec::new();
        let mut collapsed = false;
        let mut i = 0;
        while i < bands.len() {
            let mut j = i + 1;
            while j < bands.len()
                && (bands[j - 1].1.hi - bands[j].1.lo).abs() <= 1e-4
                && fmt(bands[j].1.hi - bands[j].1.lo) == fmt(bands[i].1.hi - bands[i].1.lo)
            {
                j += 1;
            }
            if j - i >= 3 {
                let run = &bands[i..j];
                out.push(repeated(run, mean(run), "adjoin with one length"));
                collapsed = true;
            } else {
                out.extend(bands[i..j].iter().map(|m| one(m)));
            }
            i = j;
        }
        if collapsed {
            return out;
        }
    }
    let lengths: Vec<f64> = bands.iter().map(|(_, b)| b.hi - b.lo).collect();
    let m = mean(bands);
    let spread = lengths.iter().copied().fold(f64::NEG_INFINITY, f64::max)
        - lengths.iter().copied().fold(f64::INFINITY, f64::min);
    // draftwright collapses lengths within 10 % of their mean whatever they print; the port also
    // asks that each prints as the one length stated, so `3× 23.3` never stands for a 23 or a 23.7.
    if bands.len() >= 3 && spread <= 0.10 * m && lengths.iter().all(|l| fmt(*l) == fmt(m)) {
        return vec![repeated(bands, m, "have one length")];
    }
    bands.iter().map(|m| one(m)).collect()
}

/// A turned part's dimensions: the overall diameter and length of a part turned whole, and each
/// band's diameter and length.
fn wants(cx: &Context) -> (Vec<Want>, Vec<Problem>) {
    let mut out = Vec::new();
    let mut problems = Vec::new();
    let steps: Vec<&Feature> = cx
        .analysis
        .features
        .iter()
        .filter(|f| f.kind == "turned_step")
        .collect();
    let mut read: Vec<Banded> = Vec::new();
    match bands(cx.analysis) {
        Ok(own) => {
            for f in own {
                match band(&cx.frame.0, f) {
                    Ok(b) => read.push((f, b)),
                    Err(why) => problems.push(not_dimensioned(f, &why)),
                }
            }
        }
        Err(why) => {
            for f in &steps {
                problems.push(not_dimensioned(
                    f,
                    &format!("groove ownership of its band cannot be decided ({why})"),
                ));
            }
        }
    }
    // The bands of each turned profile, in order along the axis: one axis line, one record.
    let mut profiles: Vec<Vec<&Banded>> = Vec::new();
    for fb in &read {
        let (f, b) = fb;
        let key = f.parameters.get("profile");
        match profiles.iter_mut().find(|p| {
            let (g, c) = p[0];
            c.axis == b.axis && same_line(b.axis, c.on, b.on) && g.parameters.get("profile") == key
        }) {
            Some(p) => p.push(fb),
            None => profiles.push(vec![fb]),
        }
    }
    for p in &mut profiles {
        p.sort_by(|x, y| x.1.lo.total_cmp(&y.1.lo).then(x.1.hi.total_cmp(&y.1.hi)));
    }

    let manufacturing = crate::pmi_lowering::manufacturing(cx.analysis);
    let mut carried: Vec<&str> = Vec::new();
    if let Some(t) = &cx.turned {
        let mut share = shared(t, &read, cx.analysis.envelope);
        // A band that carries a thread or knurl keeps a diameter of its own when another band
        // shares the overall diameter (ISSUE-1298-a: one annotation per owner).
        if share.len() > 1
            && share
                .iter()
                .any(|(f, _)| !manufacturing.on(&f.id).is_empty())
        {
            share.clear();
        }
        carried = share.iter().map(|(f, _)| f.id.as_str()).collect();
        let grooves = groove_spans(cx);
        let spans: Vec<Vec<(f64, f64)>> = profiles
            .iter()
            .filter(|p| p[0].1.axis == t.axis)
            .map(|p| {
                let (f, b) = p[0];
                let key = f.parameters.get("profile");
                p.iter()
                    .map(|(_, c)| (c.lo, c.hi))
                    .chain(
                        grooves
                            .iter()
                            .filter(|(g, k, at, ..)| {
                                *k == b.axis && *g == key && same_line(b.axis, *at, b.on)
                            })
                            .map(|(.., lo, hi)| (*lo, *hi)),
                    )
                    .collect()
            })
            .collect();
        let covered = chain_covers(&spans, cx.analysis.envelope, t.axis);
        out.extend(overall(cx, t, &share, covered, &manufacturing));
    }
    out.extend(diameters(cx, &read, &carried, &manufacturing));
    for p in &profiles {
        let [lo, hi] = cx.analysis.envelope;
        let k = p[0].1.axis;
        let full = cx.turned.as_ref().is_some_and(|t| t.axis == k)
            && (p[0].1.lo - lo[k]).abs() <= 1e-3
            && (p[p.len() - 1].1.hi - hi[k]).abs() <= 1e-3
            && p.windows(2).all(|w| (w[0].1.hi - w[1].1.lo).abs() <= 1e-3);
        // A whole multi-step profile uses one inspection end (PMI first, then the larger flat
        // end); separate bodies keep their own chains and silhouette witnesses below.
        if full
            && p.len() >= 2
            && let Some((high, from)) = profile_datum(cx.analysis, p)
        {
            let (shoulders, last) = baseline(p, high, &from, cx.spec.units);
            out.extend(shoulders);
            let overall = want_id("envelope", &format!("{}.length", super::envelope::NAMES[k]));
            if let Some(w) = out.iter_mut().find(|w| w.id == overall) {
                w.measurements.push(last);
                w.basis = format!(
                    "the overall length along the turning axis, the last rung of the profile's baseline from {from}"
                );
            }
            continue;
        }
        let first = out.len();
        if full {
            // Keep individual links so exactly one, rather than an entire counted
            // chain, can become reference beside the controlling overall length.
            for b in p {
                out.extend(chain(std::slice::from_ref(b), cx.spec.units));
            }
        } else {
            out.extend(chain(p, cx.spec.units));
        }
        own_body(&mut out[first..], p, &profiles, cx.analysis);
    }
    (out, problems)
}

/// The radius of profile *p*'s silhouette at station *at* along its axis: the largest band
/// ending there, so a witness starts at the outer corner of the shoulder it locates.
fn radius_at(p: &[&Banded], at: f64) -> f64 {
    p.iter()
        .filter(|(_, b)| (b.lo - at).abs() <= 1e-3 || (b.hi - at).abs() <= 1e-3)
        .map(|(_, b)| 0.5 * b.diameter)
        .fold(0.0, f64::max)
}

/// The world axis a profile view of axis *k* shows across it (neither *k* nor the axis *view*
/// looks along).
fn shown_across(k: usize, view: &str) -> usize {
    let look = super::TOWARD
        .iter()
        .find(|(n, _)| *n == view)
        .map(|(_, t)| {
            t.iter()
                .position(|c| c.abs() == 1.0)
                // Invariant: Routing selected a principal from the fixed view table.
                .expect("a principal view")
        })
        // Invariant: The selected principal is an entry in the fixed view table.
        .expect("a named view");
    // Invariant: The iterator is over the fixed three coordinate axes.
    (0..3).find(|&j| j != k && j != look).expect("three axes")
}

/// ARCH0020-2 and ISSUE-1357-c: when the part has several turned profiles along one axis on
/// distinct axis lines (separate bodies), each profile's chain *chain* is its own: it goes in the
/// first profile view that separates its axis line from every other's (one of the two always
/// does: distinct parallel lines differ across one of them), and its witnesses start at its own
/// silhouette (the outer corner of each shoulder) on the side of the view its body is nearer, so
/// a chain never stands on a sibling's outline (`dims::placement::lanes` puts such a chain in
/// that side's strip). Placement then checks both body-local corridors, including middle
/// bodies, and reports a superimposed profile with a view-restoration fix when the spec omits
/// its separating view. Policy B retains and reports a chain with no clear corridor.
fn own_body(chain: &mut [Want], p: &[&Banded], profiles: &[Vec<&Banded>], analysis: &Analysis) {
    let b0 = &p[0].1;
    let k = b0.axis;
    let siblings: Vec<P3> = profiles
        .iter()
        .map(|q| &q[0].1)
        .filter(|c| c.axis == k && !same_line(k, c.on, b0.on))
        .map(|c| c.on)
        .collect();
    if siblings.is_empty() {
        return;
    }
    let views = profile_views(k);
    let view = views
        .iter()
        .copied()
        .find(|v| {
            let j = shown_across(k, v);
            siblings.iter().all(|s| (s[j] - b0.on[j]).abs() > 1e-3)
        })
        .unwrap_or(views[0]);
    let j = shown_across(k, view);
    let [lo, hi] = analysis.envelope;
    let sign = if b0.on[j] - lo[j] <= hi[j] - b0.on[j] {
        -1.0
    } else {
        1.0
    };
    for w in chain.iter_mut() {
        let mut order = vec![view];
        order.extend(w.views.iter().copied().filter(|v| *v != view));
        w.views = order;
        if let Shape::Linear {
            a,
            b,
            a_edge,
            b_edge,
            ..
        } = &mut w.shape
        {
            a[j] = b0.on[j] + sign * radius_at(p, a[k]);
            b[j] = b0.on[j] + sign * radius_at(p, b[k]);
            *a_edge = false;
            *b_edge = false;
        }
    }
}

/// The end of turned profile *p* (its bands in order along their axis, adjoining from end to end
/// of the part) that the part's PMI measures it from, `true` for the high end, and what names it
/// (`datum A (datum:#847)`), or none when the PMI names neither end or both alike (#33):
///
/// - **a datum feature** on the end: a planar datum whose outward normal runs along the axis and
///   whose every site lies on that end face (beyond draftwright, which takes no datum's say-so);
/// - else **its PMI lengths** along the axis from one end to a shoulder of the profile, as
///   draftwright's `_authored_step_baseline` reads them: the end most of them run from, when at
///   least one of them is more than a single band's own length (a band's length alone establishes
///   no scheme). Equal support at both ends leaves the chain.
fn datum_end(analysis: &Analysis, p: &[&Banded]) -> Option<(bool, String)> {
    let Some(crate::pmi::Reading::Read(report)) = &analysis.pmi else {
        return None;
    };
    let k = p[0].1.axis;
    let on = p[0].1.on;
    let (lo, hi) = (p[0].1.lo, p[p.len() - 1].1.hi);
    let radius = p.iter().map(|(_, b)| b.diameter).fold(0.0, f64::max) / 2.0;
    let near = |a: f64, b: f64| (a - b).abs() <= 0.01;
    let on_body = |q: &P3| {
        (0..3)
            .filter(|&j| j != k)
            .map(|j| (q[j] - on[j]).powi(2))
            .sum::<f64>()
            .sqrt()
            <= radius + 0.01
    };
    let usable = |r: &&crate::pmi::Record| {
        r.blockers.is_empty() && r.geometry.as_ref().is_some_and(|g| g.reasons.is_empty())
    };
    // Datum features on an end face.
    let mut datums: Vec<(bool, String)> = Vec::new();
    for r in report
        .records
        .iter()
        .filter(|r| r.category == crate::pmi::Category::Datum)
        .filter(usable)
    {
        // Invariant: The usable record predicate requires geometry to be Some.
        let g = r.geometry.as_ref().expect("usable");
        let Some(n) = g.normal else { continue };
        if g.surface.as_deref() != Some("plane")
            || g.sites.is_empty()
            || (0..3).any(|j| j != k && n[j].abs() > 1e-6)
        {
            continue;
        }
        let high = n[k] > 0.0;
        let end = if high { hi } else { lo };
        if g.sites.iter().all(|s| near(s[k], end) && on_body(s)) {
            datums.push((high, format!("datum {} ({})", r.label, r.source_id)));
        }
    }
    match datums
        .iter()
        .map(|(h, _)| *h)
        .collect::<BTreeSet<bool>>()
        .len()
    {
        1 => return datums.into_iter().next(),
        2 => return None,
        _ => {}
    }
    // PMI lengths from an end to a shoulder.
    let shoulders: Vec<f64> = p.iter().flat_map(|(_, b)| [b.lo, b.hi]).collect();
    let mut targets: [BTreeSet<i64>; 2] = [BTreeSet::new(), BTreeSet::new()];
    let mut established = [false; 2];
    let mut sources: [Vec<&str>; 2] = [Vec::new(), Vec::new()];
    for r in report
        .records
        .iter()
        .filter(|r| {
            r.category == crate::pmi::Category::Dimension
                && matches!(r.kind.as_str(), "linear" | "thickness")
        })
        .filter(usable)
    {
        // Invariant: The usable record predicate requires geometry to be Some.
        let g = r.geometry.as_ref().expect("usable");
        if g.axis.as_deref() != Some(["X", "Y", "Z"][k])
            || g.stations.len() != 2
            || !g.stations.iter().all(on_body)
        {
            continue;
        }
        let (s0, s1) = (g.stations[0][k], g.stations[1][k]);
        for (base, target) in [(s0, s1), (s1, s0)] {
            let Some(end) = [lo, hi].iter().position(|e| near(base, *e)) else {
                continue;
            };
            if near(base, target) || !shoulders.iter().any(|s| near(*s, target)) {
                continue;
            }
            targets[end].insert((target * 1e3).round() as i64);
            sources[end].push(&r.source_id);
            let (a, b) = (base.min(target), base.max(target));
            if !p.iter().any(|(_, c)| near(c.lo, a) && near(c.hi, b)) {
                established[end] = true;
            }
        }
    }
    let end = match (established, targets[0].len().cmp(&targets[1].len())) {
        ([false, false], _) => return None,
        ([true, false], _) => 0,
        ([false, true], _) => 1,
        (_, std::cmp::Ordering::Greater) => 0,
        (_, std::cmp::Ordering::Less) => 1,
        (_, std::cmp::Ordering::Equal) => return None,
    };
    sources[end].dedup();
    Some((
        end == 1,
        format!(
            "the {} end, which the part's AP242 lengths ({}) run from",
            if end == 1 { "high" } else { "low" },
            sources[end].join(", ")
        ),
    ))
}

/// A complete authored chain between derived planes owns the profile's axial sizes.
/// Replacing it with automatic cumulative shoulder locations would hide its source claims.
fn derived_chain(analysis: &Analysis, p: &[&Banded]) -> bool {
    let Some(crate::pmi::Reading::Read(report)) = &analysis.pmi else {
        return false;
    };
    p.len() >= 2
        && p.iter().all(|(_, band)| {
            report.records.iter().any(|r| {
                if r.category != crate::pmi::Category::Dimension
                    || r.kind != "linear"
                    || !r.blockers.is_empty()
                {
                    return false;
                }
                let Some(g) = &r.geometry else {
                    return false;
                };
                if !g.reasons.is_empty()
                    || g.axis.as_deref() != Some(["X", "Y", "Z"][band.axis])
                    || !matches!(g.targets.as_slice(), [Some(a),Some(b)]
                    if a.kind == crate::pmi::DerivedKind::MedianPlane
                    && b.kind == crate::pmi::DerivedKind::MedianPlane)
                {
                    return false;
                }
                let [a, b] = g.stations.as_slice() else {
                    return false;
                };
                (a[band.axis].min(b[band.axis]) - band.lo).abs() <= 1e-6
                    && (a[band.axis].max(b[band.axis]) - band.hi).abs() <= 1e-6
                    && [a, b].iter().all(|point| {
                        (0..3)
                            .filter(|&i| i != band.axis)
                            .map(|i| (point[i] - band.on[i]).powi(2))
                            .sum::<f64>()
                            .sqrt()
                            <= band.diameter / 2.0 + 1e-6
                    })
            })
        })
}

/// PMI has first say; otherwise measure from the larger flat end. Equal ends use the low
/// station, giving a deterministic inspection origin independent of feature numbering.
fn profile_datum(analysis: &Analysis, p: &[&Banded]) -> Option<(bool, String)> {
    if derived_chain(analysis, p) {
        return None;
    }
    datum_end(analysis, p).or_else(|| {
        let high = p.last()?.1.diameter > p.first()?.1.diameter + 1e-6;
        Some((
            high,
            format!("the {} flat end", if high { "high" } else { "low" }),
        ))
    })
}

/// The common inspection origin for axial locations on a whole turned part.
pub(super) fn axial_datum(analysis: &Analysis) -> Option<(usize, f64)> {
    crate::planning::memo(analysis, "inspection-datum", Vec::new(), || {
        axial_datum_uncached(analysis)
    })
}

fn axial_datum_uncached(analysis: &Analysis) -> Option<(usize, f64)> {
    let frame = super::QFrame(crate::bridge::holes::frame(analysis));
    let t = turned(analysis, &frame)?;
    let mut read: Vec<_> = analysis
        .features
        .iter()
        .filter(|f| f.kind == "turned_step")
        .filter_map(|f| band(&frame.0, f).ok().map(|b| (f, b)))
        .collect();
    read.sort_by(|a, b| a.1.lo.total_cmp(&b.1.lo));
    let p: Vec<_> = read.iter().collect();
    if p.len() < 2 {
        return None;
    }
    let (high, _) = profile_datum(analysis, &p)?;
    Some((t.axis, analysis.envelope[usize::from(high)][t.axis]))
}

/// Profile *p*'s shoulders located from its datum end (the high end when *high*), named *from*:
/// the band at the datum keeps its own length (`step.length`, which is its shoulder's distance from
/// the datum), each further shoulder but the far end is `step_position.length` of the band it
/// ends, from the datum (the other bands' lengths are planned too, for consolidation to cover),
/// and the far end's claim (the last band's position), which the overall
/// length states, is returned for it to carry. The shortest is innermost (placement's lane
/// order), so the rungs stack from the datum.
fn baseline(
    p: &[&Banded],
    high: bool,
    from: &str,
    units: crate::units::Units,
) -> (Vec<Want>, Measurement) {
    let fmt = |v| crate::dims::fmt_in(v, units);
    let k = p[0].1.axis;
    let mut ordered: Vec<&Banded> = p.to_vec();
    if high {
        ordered.reverse();
    }
    let datum = if high { p[p.len() - 1].1.hi } else { p[0].1.lo };
    let far = |b: &Band| if high { b.lo } else { b.hi };
    let mut out = Vec::new();
    let first = ordered[0];
    out.push(length(
        first.0,
        &[first],
        vec![fmt(first.1.hi - first.1.lo)],
        format!(
            "a turned step's length along {}, its shoulder's distance from {from}: the first rung of the profile's baseline, in a view that shows the profile",
            AXES[k]
        ),
    ));
    // The other bands' own lengths stay planned, so a spec that names one still reads; the plan
    // shows each as conveyed by the rung that locates its band (`redundancy::consolidate`).
    for b in &ordered[1..] {
        out.extend(chain(std::slice::from_ref(b), units));
    }
    for (f, b) in &ordered[1..ordered.len() - 1] {
        let value = (far(b) - datum).abs();
        out.push(Want { units: units,
            id: want_id(&f.id, "step_position.length"),
            views: profile_views(k),
            value,
            terms: vec![fmt(value)],
            measurements: vec![Measurement {
                feature: f.id.clone(),
                parameter: "step_position.length".into(),
                value,
            }],
            basis: format!(
                "a turned step's far shoulder along {}, located from {from}: one rung of the profile's baseline, in a view that shows the profile",
                AXES[k]
            ),
            shape: Shape::Linear {
                a: corner(k, datum, b.on),
                b: corner(k, far(b), b.on),
                axis: k,
                a_edge: true,
                b_edge: true,
                order: 1,
            },
            turning: None,
        });
    }
    let (f, b) = ordered[ordered.len() - 1];
    let last = Measurement {
        feature: f.id.clone(),
        parameter: "step_position.length".into(),
        value: (far(b) - datum).abs(),
    };
    (out, last)
}

/// The overall diameter and, unless the step chain conveys it, the overall length of part *t*,
/// turned whole. The diameter also states the bands in *share*.
fn overall(
    cx: &Context,
    t: &Turned,
    share: &[&Banded],
    covered: bool,
    manufacturing: &Manufacturing,
) -> Vec<Want> {
    let fmt = |v| crate::dims::fmt_in(v, cx.spec.units);
    let [lo, hi] = cx.analysis.envelope;
    let mut out = Vec::new();
    let views = profile_views(t.axis);
    // Across the axis, in the first of its profile views: the radial axis that view shows.
    let radial = (0..3)
        .find(|&j| j != t.axis && profile_views(j).contains(&views[0]))
        .unwrap_or((t.axis + 1) % 3);
    let mut terms = vec![format!("ø{}", fmt(t.diameter))];
    // Two flanges are counted, as draftwright counts them; three or more lands of one cylinder
    // (beyond draftwright) read as the one diameter they are, which a coaxial boss of that size
    // shares too (`share_statements`).
    if share.len() == 2 {
        terms.insert(0, "2×".into());
    }
    // A thread or knurl the bands it states carry follows the diameter (beyond draftwright,
    // whose overall diameter prints none and whose lint then finds the requirement not drawn).
    if let Some((f, _)) = share.first() {
        // An external thread's designation stands in place of the diameter (ISO 6410).
        if share.len() == 1 && manufacturing.replaces_diameter(&f.id) {
            terms.clear();
        }
        terms.extend(manufacturing.terms(&f.id).0);
    }
    let mut measurements = vec![Measurement {
        feature: "envelope".into(),
        parameter: "od.diameter".into(),
        value: t.diameter,
    }];
    measurements.extend(
        cx.analysis
            .features
            .iter()
            .filter(|f| f.kind == "turned_body")
            .map(|f| Measurement {
                feature: f.id.clone(),
                parameter: "turned_body.diameter".into(),
                value: t.diameter,
            }),
    );
    measurements.extend(share.iter().map(|(f, b)| Measurement {
        feature: f.id.clone(),
        parameter: "step.diameter".into(),
        value: b.diameter,
    }));
    measurements.extend(claims(manufacturing, ids(share), t.diameter));
    let stands = match share.len() {
        0 => String::new(),
        1 => "; it is also the diameter of the one band that size".into(),
        2 => "; it is also the diameter of the two separate bands that size".into(),
        n => format!("; it is also the diameter of the {n} separate bands that size"),
    };
    out.push(Want { units: cx.spec.units,
        id: want_id("envelope", "od.diameter"),
        views: views.clone(),
        value: t.diameter,
        terms,
        measurements,
        turning: Some(t.axis),
        basis: format!(
            "the part is turned whole about {}: its largest diameter is its overall size across the axis, in a view that shows the profile{stands}{}",
            AXES[t.axis],
            carries(manufacturing, &ids(share), "turned step")
        ),
        shape: Shape::Linear {
            a: corner(radial, lo[radial], lo),
            b: corner(radial, hi[radial], lo),
            axis: radial,
            a_edge: true,
            b_edge: true,
            order: 2,
        },
    });
    let k = t.axis;
    let name = super::envelope::NAMES[k];
    out.push(Want { units: cx.spec.units,
        id: want_id("envelope", &format!("{name}.length")),
        views,
        value: hi[k] - lo[k],
        terms: vec![fmt(hi[k] - lo[k])],
        measurements: vec![Measurement {
            feature: "envelope".into(),
            parameter: format!("{name}.length"),
            value: hi[k] - lo[k],
        }],
        turning: None,
        basis: if covered {"the overall length along the turning axis; the chain reaches both ends, so one member is reference"} else {"the overall length along the turning axis: the step chain does not run end to end"}.into(),
        shape: Shape::Linear {
            a: corner(k, lo[k], lo),
            b: corner(k, hi[k], lo),
            axis: k,
            a_edge: true,
            b_edge: true,
            order: 2,
        },
    });
    out
}
