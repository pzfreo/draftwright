//! Through slots (quiddity's `Slot`, draftwright's `SlotFeature`): width, length, an obround's
//! end radius, the location of its low end and of its centre line, in the view that looks through
//! it (`annotations/_slots.py`, `compiled.py`'s slot location).
//!
//! Each lone slot states:
//! - its **width** across its width axis, and its **length** along its long axis, as linear
//!   dimensions whose extension lines start at the slot's own edges (draftwright's witnesses);
//!   slots whose widths span the same stretch of a view's horizontal axis (or any axis in the
//!   front) share one width, counted (`2× 6`), as draftwright's shared width does;
//! - for an **obround** (its ends proven arcs: quiddity's `end_radius`), the end radius as a
//!   leader to an end, `2× R3`. A straight slot (no end radius, perhaps a `corner_radius`) states
//!   no radius: its corner blends are the fillets' (draftwright prints none for it either);
//! - its **location**: the distance along its long axis from the part's box to its low end
//!   (draftwright's `location_slot.length`, datum `bbox`), and the distance across its width axis
//!   from the part's box to its centre line (`location_slot_across.length`). draftwright states
//!   no position across; the maintainer's decision of 2026-10-08 is that a slot is located across
//!   its width too. Slots at the same distance across in one view share one dimension, as hole
//!   locations do, and a slot location that a hole's, pattern's or boss's states is conveyed by it
//!   (`dims::holes::consolidate`, ARCH0009-6).
//!
//! Lone obround slots that are **congruent** (the same width, length and full-round ends, the
//! radius half the width, seen through the same axis, whichever way each runs) are dimensioned as
//! slots: one `N× SLOT W × L` callout states them all, in place of their widths, lengths and end
//! radii (the callout implies full-round ends), and each is located by its centre (along its
//! length as well as across it), so the group reads off centre-to-centre. A straight slot (a
//! window with blended corners) or an obround whose ends are not full-round is dimensioned on its
//! own, as are a keyhole (a circular seat opening the slot at its middle, whose axis and callout
//! carry the compound: `lint::slots::keyed`) and a slot the spec splits from the callout
//! (`features/<id>/split`). A full-round obround split that way is dimensioned by its two end
//! centres (`lint::slots::ends_located`): its width, its low end's centre located from the part's
//! box, and the centres' distance apart (`slot_centres.length`), as two arcs located; the width
//! with the located centres fixes its full-round ends, so no length or end radius is stated.
//! `centrelines` draws a centre mark at each end centre ([`located_end_centres`]).
//!
//! A **slot pattern** (quiddity's `SlotArray` or `SlotGrid`, a `slot_pattern` feature naming its
//! member slots) is called out once, as draftwright's `render_slot_patterns` does, in the view that
//! looks through its slots: one `N× SLOT W × L` callout, an obround's `2× R` unless the ends are
//! full-round (the radius half the width, which the callout implies), the pitch that spaces
//! a linear array (`2× 25`) or the column and row pitches of a grid (quiddity's own `cols` and
//! `rows`), and the pattern's centre located from the part's box on both of that view's axes
//! (draftwright's `location_slot_pattern`, from the array's centroid or the grid's centre). Its
//! members are not dimensioned again. A pattern whose members do not hold together as quiddity
//! describes them (not identical, not along the view's axes, not at its pitches) is reported
//! (`feature_not_dimensioned`), and its slots are dimensioned singly.
//!
//! **Blind slots** reach the Analysis as `section_recess` features, a kind shared with pockets,
//! channels and passages, so the planner claims no kind for them: it reads each record's section
//! as draftwright's `section_recess_fields` does, and calls out those it reads as a rectangular
//! or a round-bottom blind slot (an edge-open recess with an open U section), in the view that
//! looks into it: `OPEN SLOT W × L × D DEEP`, `ROUND-BOTTOM OPEN SLOT F BOTTOM FLAT × Rr × L
//! LONG`. Every other section recess stays reported as not dimensioned. A blind slot opens at the
//! part's edge, which fixes its position along its run; across its width it is located by its
//! centre line as a through slot is (beyond draftwright, which does not locate it).
//!
//! quiddity's **semicircular blind slots** (`SemicircularBlindSlot`, #233), open at one end and
//! along their mouth, their floor a half-round between straight sides, are called out as the
//! round-bottom blind slots are, in the view that looks into them: `ROUND-BOTTOM OPEN SLOT W × L
//! × D DEEP` (no flat floor: the width is the floor's diameter). Each is located across its width
//! by its centre line, as a blind slot is; its open end fixes it along its run. One whose opening
//! or width is along no axis of the part is reported (`feature_not_dimensioned`).

use std::collections::{BTreeMap, BTreeSet};

use serde_json::Value;

use super::{
    AXES, Context, Measurement, P3, Planner, Problem, QFrame, Shape, Want, end_on, key, point,
    want_id,
};
use crate::bridge::Feature;
use crate::bridge::holes::principal;

pub(super) const PLANNER: Planner = Planner {
    kinds: &["slot", "slot_pattern", "semicircular_blind_slot"],
    wants,
};

/// A slot in the part's frame.
struct Slot<'a> {
    feature: &'a Feature,
    /// The world axes it runs along, spans across and passes through.
    long: usize,
    wide: usize,
    deep: usize,
    width: f64,
    length: f64,
    end_radius: Option<f64>,
    /// Its ends along `long`, low first.
    ends: [f64; 2],
    /// Its centre line's coordinate on `wide`, and its mid-thickness on `deep`.
    centre: f64,
    mid: f64,
}

impl Slot<'_> {
    fn end_arcs(&self, r: f64, analysis: &crate::model::Analysis) -> Vec<crate::arc::ProfileArc> {
        if let Some(arcs) = analysis.profile_arcs.get(&self.feature.id) {
            return arcs.clone();
        }
        let mut u = [0.0; 3];
        let mut v = [0.0; 3];
        u[self.long] = 1.0;
        v[self.wide] = 1.0;
        [0, 1]
            .map(|end| crate::arc::ProfileArc {
                centre: self.at(
                    if end == 0 {
                        self.ends[0] + r
                    } else {
                        self.ends[1] - r
                    },
                    self.centre,
                ),
                radius: r,
                u,
                v,
                start: if end == 0 {
                    std::f64::consts::FRAC_PI_2
                } else {
                    -std::f64::consts::FRAC_PI_2
                },
                sweep: std::f64::consts::PI,
            })
            .to_vec()
    }

    /// The point at *long* along it and *wide* across it, at mid-thickness.
    fn at(&self, long: f64, wide: f64) -> P3 {
        let mut p = [0.0; 3];
        p[self.long] = long;
        p[self.wide] = wide;
        p[self.deep] = self.mid;
        p
    }

    /// Its edge on *axis* (`long` or `wide`) facing *sign*.
    fn edge(&self, axis: usize, sign: f64) -> f64 {
        if axis == self.long {
            if sign > 0.0 {
                self.ends[1]
            } else {
                self.ends[0]
            }
        } else {
            self.centre + sign * 0.5 * self.width
        }
    }
}

fn number(f: &Feature, name: &str) -> Result<f64, String> {
    f.parameters
        .get(name)
        .and_then(Value::as_f64)
        .ok_or_else(|| format!("its record gives no {name}"))
}

/// A slot as quiddity records it (`long_axis`, `width_axis` as letters of its frame; `lo`, `hi`
/// along the first, `w_center` on the second, `d_lo`, `d_hi` through it), or why it cannot be
/// dimensioned.
fn read<'a>(f: &'a Feature, frame: &QFrame) -> Result<Slot<'a>, String> {
    let p = &f.parameters;
    let letter = |name: &str| {
        p.get(name)
            .and_then(Value::as_str)
            .and_then(|a| AXES.iter().position(|x| *x == a))
            .ok_or_else(|| format!("its record gives no {name}"))
    };
    let (ql, qw) = (letter("long_axis")?, letter("width_axis")?);
    if ql == qw {
        return Err("its long and width axes are the same".into());
    }
    let qd = 3 - ql - qw;
    let principal_of = |q: usize| {
        // Invariant: AXES contains exactly the three supported coordinate letters.
        principal(frame.letter(&Value::from(AXES[q])).expect("a letter"))
            .ok_or("its axes lie along no axis of the part, so no standard view looks through it")
    };
    let (long, wide) = (principal_of(ql)?, principal_of(qw)?);
    let deep = 3 - long - wide;
    let (width, length) = (number(f, "width")?, number(f, "length")?);
    let (lo, hi, w) = (number(f, "lo")?, number(f, "hi")?, number(f, "w_center")?);
    let mid = 0.5 * (number(f, "d_lo")? + number(f, "d_hi")?);
    let point = |t: f64| {
        let mut q = [0.0; 3];
        q[ql] = t;
        q[qw] = w;
        q[qd] = mid;
        frame.point(q)
    };
    let (a, b) = (point(lo), point(hi));
    // quiddity rounds `lo`, `hi` and `w_center` to 0.01 mm in its own frame, whose origin is not
    // round in the part's, so a slot 55 mm from an edge reads 54.998 there. The box around its
    // faces (the fingerprint's position) is exact; it is taken when it agrees within that
    // rounding, and the record otherwise.
    let c = f.fingerprint.position;
    let exact = |k: usize, recorded: f64| {
        if (c[k] - recorded).abs() <= 0.01 {
            c[k]
        } else {
            recorded
        }
    };
    let along = exact(long, 0.5 * (a[long] + b[long]));
    Ok(Slot {
        feature: f,
        long,
        wide,
        deep,
        width,
        length,
        end_radius: p.get("end_radius").and_then(Value::as_f64),
        ends: [along - 0.5 * length, along + 0.5 * length],
        centre: exact(wide, a[wide]),
        mid: a[deep],
    })
}

/// The world axis an extension line runs along, and its sign, from a dimension measured along
/// *m* in *view* out to the strip placement puts it in (`placement::strip`: horizontal ones above
/// the plan and below the front and side, vertical ones left of the plan and right of the front
/// and side), under the standard views' orientations (`model`: the plan shows x across and y up,
/// the front x and z, the side y and z).
fn outward(view: &str, m: usize) -> (usize, f64) {
    match (view, m) {
        ("plan", 0) => (1, 1.0),
        ("plan", _) => (0, -1.0),
        ("front", 0) | ("side", 1) => (2, -1.0),
        ("front", _) => (0, 1.0),
        _ => (1, 1.0),
    }
}

/// The world axis a view shows across the page.
fn horizontal(view: &str) -> usize {
    if view == "side" { 1 } else { 0 }
}

/// A linear size of *s* along *m*, its extension lines from the slot's edge facing their strip.
fn size(s: &Slot, view: &str, m: usize, lo: f64, hi: f64, order: u8, physical_ends: bool) -> Shape {
    let (e, sign) = outward(view, m);
    // A full-round end reaches its length extremum on the centreline,
    // not at the rectangle's imaginary corner. Its size and end location
    // witness that physical point; the measured stations stay unchanged.
    let witness = if physical_ends
        && m == s.long
        && s.end_radius
            .is_some_and(|r| (2. * r - s.width).abs() < 1e-6)
    {
        s.centre
    } else {
        s.edge(e, sign)
    };
    let point = |t: f64| {
        let mut p = s.at(0.0, 0.0);
        p[m] = t;
        p[e] = witness;
        p
    };
    Shape::Linear {
        a: point(lo),
        b: point(hi),
        axis: m,
        a_edge: false,
        b_edge: false,
        order,
    }
}

/// What makes two lone slots identical on a drawing: the view that looks through them, the axes
/// they run along and across, their width and length, and their end radius.
type Alike = (&'static str, usize, usize, i64, i64, Option<i64>);

fn alike(s: &Slot) -> Alike {
    (
        end_on(s.deep),
        s.long,
        s.wide,
        key(s.width),
        key(s.length),
        s.end_radius.map(key),
    )
}

/// What makes lone slots congruent, so one slot callout states them: the view that looks through
/// them, their width and length, and their end radius (not the way each runs). `lint::slots`
/// groups lone slots by the same rule.
type Congruent = (&'static str, i64, i64, Option<i64>);

fn congruence(s: &Slot) -> Congruent {
    (
        end_on(s.deep),
        key(s.width),
        key(s.length),
        s.end_radius.map(key),
    )
}

/// Whether an obround's ends are full-round: their radius half its width (to quiddity's rounding,
/// `lint::slots::FULL_ROUND`), which a slot callout implies.
fn full_round(s: &Slot) -> bool {
    s.end_radius
        .is_some_and(|r| (2.0 * r - s.width).abs() <= crate::lint::slots::FULL_ROUND)
}

/// The `N× SLOT W × L` callout for *members*, its leader to an obround end of any of them (as an
/// end radius's leader lands), or to either long side of a straight slot.
fn slot_callout(
    id: String,
    members: &[&Slot],
    measurements: Vec<Measurement>,
    basis: String,
    analysis: &crate::model::Analysis,
    units: crate::units::Units,
) -> Want {
    let fmt = |v| crate::dims::fmt_in(v, units);
    let s = members[0];
    let arcs = match s.end_radius {
        Some(r) => members
            .iter()
            .flat_map(|t| t.end_arcs(r, analysis))
            .collect(),
        None => Vec::new(),
    };
    Want {
        units: units,
        id,
        views: vec![end_on(s.deep)],
        value: s.width,
        terms: vec![
            format!("{}×", members.len()),
            "SLOT".into(),
            fmt(s.width),
            "×".into(),
            fmt(s.length),
        ],
        measurements,
        turning: None,
        basis,
        shape: Shape::Callout {
            arcs,
            sites: members
                .iter()
                .flat_map(|t| {
                    let mid = 0.5 * (t.ends[0] + t.ends[1]);
                    [-1.0, 1.0].map(|sign| (t.at(mid, t.edge(t.wide, sign)), 0.0))
                })
                .collect(),
        },
    }
}

/// The end radius of *members*' obround ends, called out once with a leader to an end of the
/// first: *measurements* name what it states.
fn end_radius_want(
    id: String,
    s: &Slot,
    r: f64,
    measurements: Vec<Measurement>,
    basis: &str,
    analysis: &crate::model::Analysis,
    units: crate::units::Units,
) -> Want {
    let fmt = |v| crate::dims::fmt_in(v, units);
    Want {
        units: units,
        id,
        views: vec![end_on(s.deep)],
        value: r,
        terms: vec!["2×".into(), format!("R{}", fmt(r))],
        measurements,
        turning: None,
        basis: basis.into(),
        shape: Shape::Callout {
            arcs: s.end_arcs(r, analysis),
            sites: vec![
                (s.at(s.ends[0] + r, s.centre), r),
                (s.at(s.ends[1] - r, s.centre), r),
            ],
        },
    }
}

/// Other physical witness stations for a slot width. Both end references state
/// the same straight-side separation; layout must prove the chosen ink clear.
pub(super) fn width_references(w: &Want, analysis: &crate::model::Analysis) -> Vec<Shape> {
    let [m] = w.measurements.as_slice() else {
        return Vec::new();
    };
    if m.parameter != "slot_width.length" {
        return Vec::new();
    }
    let frame = QFrame(crate::bridge::holes::frame(analysis));
    let Some(s) = analysis
        .features
        .iter()
        .find(|f| f.id == m.feature)
        .and_then(|f| read(f, &frame).ok())
    else {
        return Vec::new();
    };
    let Shape::Linear { axis, order, .. } = w.shape else {
        return Vec::new();
    };
    let inset = s.end_radius.unwrap_or(0.);
    [s.ends[0] + inset, s.ends[1] - inset]
        .into_iter()
        .map(|end| Shape::Linear {
            a: s.at(end, s.edge(s.wide, -1.)),
            b: s.at(end, s.edge(s.wide, 1.)),
            axis,
            order,
            a_edge: false,
            b_edge: false,
        })
        .collect()
}

/// A slot location placed as *p* that also conveys coincident slots' locations (one span, one
/// dimension: their centres on the same line, *others*) has its extension line start at whichever
/// of those centres lies nearest its dimension line, on the line the centres share: congruent
/// slots in a row are located centre to centre, and the witness from the far slot would cross the
/// near ones to reach the dimension (nema17-bracket's rows). The dimension states each of them.
pub(super) fn nearest_witness<'a>(
    w: &Want,
    mut p: super::Placed,
    others: impl Iterator<Item = &'a Shape>,
    frame: &super::placement::Frame,
) -> super::Placed {
    if !w.measurements.iter().all(|m| {
        matches!(
            m.parameter.as_str(),
            "location_slot.length" | "location_slot_across.length"
        )
    }) {
        return p;
    }
    let (Shape::Linear { b_edge: false, .. }, Some(line)) = (&w.shape, p.lines.get_mut(1)) else {
        return p;
    };
    // The page coordinate the witness runs along (k), and which way it runs to the dimension.
    let k = usize::from((line[1][1] - line[0][1]).abs() > (line[1][0] - line[0][0]).abs());
    let out = (line[1][k] - line[0][k]).signum();
    let end = line[1][k];
    let start = others
        .filter_map(|o| match o {
            Shape::Linear { b, .. } => Some(frame.page(*b)),
            _ => None,
        })
        .filter(|q| (q[1 - k] - line[0][1 - k]).abs() <= 1e-6)
        .map(|q| q[k] + out * super::GAP)
        .filter(|&from| out * (from - line[0][k]) > 0.0 && out * (end - from) > 0.0)
        .max_by(|x, y| (out * x).total_cmp(&(out * y)));
    if let Some(from) = start {
        line[0][k] = from;
    }
    p
}

fn wants(cx: &Context) -> (Vec<Want>, Vec<Problem>) {
    let fmt = |v| crate::dims::fmt_in(v, cx.spec.units);
    let analysis = cx.analysis;
    let [lo, _] = analysis.envelope;
    let size = |s: &Slot, view: &str, m, lo, hi, order| {
        size(
            s,
            view,
            m,
            lo,
            hi,
            order,
            super::placement::physical_witness_retry(analysis),
        )
    };
    let mut problems = Vec::new();
    let mut out = Vec::new();
    // Patterns first: each that holds together takes its members.
    let mut taken: BTreeSet<&str> = BTreeSet::new();
    for p in analysis
        .features
        .iter()
        .filter(|f| f.kind == "slot_pattern")
    {
        match pattern(cx, p) {
            Ok((wants, more, members)) => {
                out.extend(wants);
                problems.extend(more);
                taken.extend(members);
            }
            Err(why) => problems.push(Problem {
                id: format!("p:feature_not_dimensioned:{}", p.id),
                code: "feature_not_dimensioned".into(),
                severity: "warning".into(),
                message: format!(
                    "{} is not dimensioned as a pattern: {why}; its slots are dimensioned singly.",
                    crate::wording::capital(&crate::wording::feature_id(analysis, &p.id))
                ),
                subjects: vec![p.id.clone()],
                ..Default::default()
            }),
        }
    }
    let mut slots = Vec::new();
    for f in analysis.features.iter().filter(|f| f.kind == "slot") {
        if taken.contains(f.id.as_str()) {
            continue; // the pattern's
        }
        match read(f, &cx.frame) {
            Ok(s) => slots.push(s),
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

    // Congruent full-round slots are called out together, in the order their first member is
    // recognised. A straight slot (a window with blended corners) is no `SLOT` to a reader.
    let mut congruent: Vec<(Congruent, Vec<usize>)> = Vec::new();
    for (i, s) in slots.iter().enumerate() {
        if !full_round(s)
            || crate::lint::slots::alone(s.feature)
            || crate::lint::slots::keyed(analysis, s.feature)
        {
            continue; // not an obround, split from the callout by the spec, or a keyhole
        }
        let k = congruence(s);
        match congruent.iter_mut().find(|(c, _)| *c == k) {
            Some((_, members)) => members.push(i),
            None => congruent.push((k, vec![i])),
        }
    }
    let mut grouped = vec![false; slots.len()];
    for (_, members) in congruent.iter().filter(|(_, m)| m.len() > 1) {
        let group: Vec<&Slot> = members.iter().map(|&i| &slots[i]).collect();
        let s = group[0];
        let n = group.len();
        let each = |parameter: &'static str, value: fn(&Slot) -> f64| {
            group.iter().map(move |t| Measurement {
                feature: t.feature.id.clone(),
                parameter: parameter.into(),
                value: value(t),
            })
        };
        out.push(slot_callout(
            want_id(&s.feature.id, "slot_width.length"),
            &group,
            each("slot_width.length", |t| t.width)
                .chain(each("slot_length.length", |t| t.length))
                .collect(),
            format!(
                "{n} congruent obround slots (the same width, length and end radius) are dimensioned as slots: one slot callout states their width and length, in the view that looks through them; their ends are full-round, half the width, which the callout implies"
            ),
            cx.analysis,
         cx.spec.units));
        for &i in members {
            grouped[i] = true;
        }
    }

    // Widths spanning the same stretch of one axis share one dimension where draftwright solves
    // them together: across the view, or in the front (same-stretch widths elsewhere are merged
    // later, as one span, one dimension). Identical slots (the same width, length and end radius,
    // run the same way) seen in one view whose widths lie on different stretches also share one
    // width, counted: the same size is not stated twice. Such a slot joins the group of the first
    // identical slot before its own stretch is compared.
    type WidthKey<'k> = (&'k str, usize, i64, i64, i64);
    let mut widths: BTreeMap<WidthKey, Vec<usize>> = BTreeMap::new();
    let mut identical: BTreeMap<Alike, (WidthKey, [i64; 2])> = BTreeMap::new();
    let mut order = Vec::new();
    for (i, s) in slots.iter().enumerate().filter(|(i, _)| !grouped[*i]) {
        let view = end_on(s.deep);
        let same = alike(s);
        let stretch = [key(s.edge(s.wide, -1.0)), key(s.edge(s.wide, 1.0))];
        let k = match identical.get(&same) {
            Some((k, first)) if *first != stretch => *k,
            _ if view == "front" || s.wide == horizontal(view) => {
                (view, s.wide, stretch[0], stretch[1], key(s.width))
            }
            _ => (view, s.wide, i64::MIN, i as i64, 0),
        };
        identical.entry(same).or_insert((k, stretch));
        let members = widths.entry(k).or_default();
        if members.is_empty() {
            order.push(k);
        }
        members.push(i);
    }

    for k in order {
        let members = &widths[&k];
        let s = &slots[members[0]];
        let view = k.0;
        let n = members.len();
        let mut terms = Vec::new();
        if n > 1 {
            terms.push(format!("{n}×"));
        }
        terms.push(fmt(s.width));
        out.push(Want { units: cx.spec.units,
            id: want_id(&s.feature.id, "slot_width.length"),
            views: vec![view],
            value: s.width,
            terms,
            measurements: members
                .iter()
                .map(|&m| Measurement {
                    feature: slots[m].feature.id.clone(),
                    parameter: "slot_width.length".into(),
                    value: slots[m].width,
                })
                .collect(),
            turning: None,
            basis: if n > 1
                && members.iter().all(|&m| {
                    key(slots[m].edge(s.wide, -1.0)) == key(s.edge(s.wide, -1.0))
                        && key(slots[m].edge(s.wide, 1.0)) == key(s.edge(s.wide, 1.0))
                })
            {
                format!(
                    "{n} slots whose widths span the same stretch in {} share one width, counted, in the view that looks through them",
                    AXES[s.wide]
                )
            } else if n > 1 {
                format!(
                    "{n} identical slots share one width, counted, in the view that looks through them"
                )
            } else {
                "a slot's width, across it, in the view that looks through it".into()
            },
            shape: size(
                s,
                view,
                s.wide,
                s.edge(s.wide, -1.0),
                s.edge(s.wide, 1.0),
                0,
            ),
        });
    }

    // Each slot's centre line across its width: (owner, the point located, its axis, view).
    let mut across: Vec<(String, P3, usize, &'static str)> = Vec::new();
    for (s, &together) in slots.iter().zip(&grouped) {
        let view = end_on(s.deep);
        let id = &s.feature.id;
        let m = |parameter: &str, value: f64| Measurement {
            feature: id.clone(),
            parameter: parameter.into(),
            value,
        };
        let Shape::Linear { b, .. } = size(s, view, s.wide, s.centre, s.centre, 1) else {
            // Invariant: size constructs Shape::Linear unconditionally.
            unreachable!("a size is linear")
        };
        across.push((id.clone(), b, s.wide, view));
        if together {
            // Called out with its congruent slots: located along its length by its centre.
            let c = 0.5 * (s.ends[0] + s.ends[1]);
            let Shape::Linear { mut a, b, .. } = size(s, view, s.long, c, c, 1) else {
                // Invariant: size constructs Shape::Linear unconditionally.
                unreachable!("a size is linear")
            };
            a[s.long] = lo[s.long];
            let d = c - lo[s.long];
            out.push(Want { units: cx.spec.units,
                id: want_id(id, "location_slot.length"),
                views: vec![view],
                value: d,
                terms: vec![fmt(d)],
                measurements: vec![m("location_slot.length", d)],
                turning: None,
                basis: format!(
                    "a slot called out with its congruent slots is located along its length ({}) from the part's {} face to its centre, so the group reads centre to centre, in the view that looks through it",
                    AXES[s.long],
                    ["left", "front", "bottom"][s.long]
                ),
                shape: Shape::Linear {
                    a,
                    b,
                    axis: s.long,
                    a_edge: true,
                    b_edge: false,
                    order: 1,
                },
            });
            continue;
        }
        if let (Some(r), true) = (
            s.end_radius,
            crate::lint::slots::ends_located(analysis, s.feature),
        ) {
            // Split from its congruent slots' callout: dimensioned by its two end centres, as two
            // arcs located, and its width (above), which with the centres fixes the full-round
            // ends. The low end's centre is located from the part's box, the other by the
            // centres' distance apart.
            let c = [s.ends[0] + r, s.ends[1] - r];
            let apart = c[1] - c[0];
            out.push(Want { units: cx.spec.units,
                id: want_id(id, "slot_centres.length"),
                views: vec![view],
                value: apart,
                terms: vec![fmt(apart)],
                measurements: vec![m("slot_centres.length", apart)],
                turning: None,
                basis: "a slot split from its congruent slots' callout (features/<id>/split) is dimensioned by its two end centres: their distance apart along it, in the view that looks through it".into(),
                shape: size(s, view, s.long, c[0], c[1], 0),
            });
            let Shape::Linear { mut a, b, .. } = size(s, view, s.long, c[0], c[0], 1) else {
                // Invariant: size constructs Shape::Linear unconditionally.
                unreachable!("a size is linear")
            };
            a[s.long] = lo[s.long];
            let d = c[0] - lo[s.long];
            out.push(Want { units: cx.spec.units,
                id: want_id(id, "location_slot.length"),
                views: vec![view],
                value: d,
                terms: vec![fmt(d)],
                measurements: vec![m("location_slot.length", d)],
                turning: None,
                basis: format!(
                    "a slot dimensioned by its end centres is located along its length ({}) from the part's {} face to its near end's centre, in the view that looks through it",
                    AXES[s.long],
                    ["left", "front", "bottom"][s.long]
                ),
                shape: Shape::Linear {
                    a,
                    b,
                    axis: s.long,
                    a_edge: true,
                    b_edge: false,
                    order: 1,
                },
            });
            continue;
        }
        out.push(Want {
            units: cx.spec.units,
            id: want_id(id, "slot_length.length"),
            views: vec![view],
            value: s.length,
            terms: vec![fmt(s.length)],
            measurements: vec![m("slot_length.length", s.length)],
            turning: None,
            basis: "a slot's length, end to end along it, in the view that looks through it".into(),
            shape: size(s, view, s.long, s.ends[0], s.ends[1], 0),
        });
        if let Some(r) = s.end_radius {
            out.push(end_radius_want(
                want_id(id, "slot_end_radius.radius"),
                s,
                r,
                vec![m("slot_end_radius.radius", r)],
                "an obround slot's ends are proven arcs: their radius is called out once for both, with a leader to an end",
                cx.analysis,
             cx.spec.units));
        }
        let d = s.ends[0] - lo[s.long];
        if d.abs() < 1e-6 {
            problems.push(Problem {
                id: format!("p:location_on_datum:{id}/{}", AXES[s.long]),
                code: "location_on_datum".into(),
                severity: "info".into(),
                message: format!(
                    "{} starts at the part's {} face, so its location along {} measures nothing and is not dimensioned.",
                    crate::wording::capital(&crate::wording::feature_id(cx.analysis, id)),
                    ["left", "front", "bottom"][s.long],
                    AXES[s.long]
                ),
                subjects: vec![id.clone()],
                ..Default::default()
            });
            continue;
        }
        let Shape::Linear { mut a, b, .. } = size(s, view, s.long, s.ends[0], s.ends[0], 1) else {
            // Invariant: size constructs Shape::Linear unconditionally.
            unreachable!("a size is linear")
        };
        a[s.long] = lo[s.long];
        out.push(Want { units: cx.spec.units,
            id: want_id(id, "location_slot.length"),
            views: vec![view],
            value: d,
            terms: vec![fmt(d)],
            measurements: vec![m("location_slot.length", d)],
            turning: None,
            basis: format!(
                "located along its length ({}) from the part's {} face to its near end, in the view that looks through it",
                AXES[s.long],
                ["left", "front", "bottom"][s.long]
            ),
            shape: Shape::Linear {
                a,
                b,
                axis: s.long,
                a_edge: true,
                b_edge: false,
                order: 1,
            },
        });
    }
    for b in analysis
        .features
        .iter()
        .filter(|f| f.kind == "section_recess")
        .filter_map(|f| blind(f, &cx.frame))
    {
        out.push(b.want(cx.spec.units));
        across.push((b.feature.id.clone(), b.centre, b.wide, end_on(b.deep)));
    }
    for f in analysis
        .features
        .iter()
        .filter(|f| f.kind == "semicircular_blind_slot")
    {
        match semicircular(&cx.frame, f, cx.spec.units) {
            Ok((want, centre, wide, view)) => {
                out.push(want);
                across.push((f.id.clone(), centre, wide, view));
            }
            Err(why) => problems.push(super::grooves::not_dimensioned(f, &why)),
        }
    }
    located_across(cx, across, &mut out, &mut problems);
    (out, problems)
}

/// The end centres of each lone slot dimensioned by them ([`crate::lint::slots::ends_located`]),
/// in the part's frame, with the axis it passes through and its end radius: a dimension to a
/// centre ends on a drawn centre mark (ISO 129-1), which `centrelines` draws there.
pub(crate) fn located_end_centres(
    analysis: &crate::model::Analysis,
) -> Vec<(&Feature, usize, [P3; 2], f64)> {
    let frame = QFrame(crate::bridge::holes::frame(analysis));
    let patterned: BTreeSet<&str> = analysis
        .features
        .iter()
        .filter(|p| p.kind == "slot_pattern")
        .filter_map(|p| p.parameters.get("slots").and_then(Value::as_array))
        .flatten()
        .filter_map(Value::as_str)
        .collect();
    analysis
        .features
        .iter()
        .filter(|f| f.kind == "slot" && !patterned.contains(f.id.as_str()))
        .filter(|f| crate::lint::slots::ends_located(analysis, f))
        .filter_map(|f| read(f, &frame).ok())
        .filter_map(|s| {
            let r = s.end_radius?;
            Some((
                s.feature,
                s.deep,
                [s.at(s.ends[0] + r, s.centre), s.at(s.ends[1] - r, s.centre)],
                r,
            ))
        })
        .collect()
}

/// The slots' locations across their widths, from the part's box to each centre line: one
/// dimension per distinct distance in a view, serving every slot at it (as `dims::holes` shares
/// its locations, ARCH0009-6), or an info problem for one centred on the box's face.
fn located_across(
    cx: &Context,
    across: Vec<(String, P3, usize, &'static str)>,
    out: &mut Vec<Want>,
    problems: &mut Vec<Problem>,
) {
    let fmt = |v| crate::dims::fmt_in(v, cx.spec.units);
    let analysis = cx.analysis;
    let [lo, _] = analysis.envelope;
    type Owners = Vec<(String, P3)>;
    let mut by: BTreeMap<(&str, usize, i64), Owners> = BTreeMap::new();
    for (owner, p, k, view) in across {
        let d = p[k] - lo[k];
        if d.abs() < 1e-6 {
            problems.push(Problem {
                id: format!("p:location_on_datum:{owner}/{}", AXES[k]),
                code: "location_on_datum".into(),
                severity: "info".into(),
                message: format!(
                    "{} is centred on the part's {} face, so its {} location measures nothing and is not dimensioned.",
                    crate::wording::capital(&crate::wording::feature_id(analysis, &owner)),
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
        let (first, p) = &owners[0];
        let d = p[k] - lo[k];
        let parameter = "location_slot_across.length";
        let mut a = *p;
        a[k] = lo[k];
        out.push(Want { units: cx.spec.units,
            id: want_id(first, parameter),
            views: vec![view],
            value: d,
            terms: vec![fmt(d)],
            measurements: owners
                .iter()
                .map(|(o, _)| Measurement {
                    feature: o.clone(),
                    parameter: parameter.into(),
                    value: d,
                })
                .collect(),
            turning: None,
            basis: format!(
                "located across its width ({}) from the part's {} face to its centre line, in the view that sees it end-on (beyond draftwright: the maintainer's decision of 2026-10-08){}",
                AXES[k],
                ["left", "front", "bottom"][k],
                if owners.len() > 1 {
                    "; one dimension serves every slot at this distance"
                } else {
                    ""
                }
            ),
            shape: Shape::Linear {
                a,
                b: *p,
                axis: k,
                a_edge: true,
                b_edge: false,
                order: 1,
            },
        });
    }
}

/// A slot pattern's items, the problems met, and its member slots' IDs.
type Grouped<'a> = (Vec<Want>, Vec<Problem>, Vec<&'a str>);

/// A slot pattern's items (callout, end radius, pitches, location), the problems met, and its
/// member slots' IDs; or why it does not hold together as quiddity describes it.
fn pattern<'a>(cx: &Context<'a>, p: &'a Feature) -> Result<Grouped<'a>, String> {
    let fmt = |v| crate::dims::fmt_in(v, cx.spec.units);
    let analysis = cx.analysis;
    let ids: Vec<&str> = p
        .parameters
        .get("slots")
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(Value::as_str).collect())
        .unwrap_or_default();
    if ids.len() < 2 {
        return Err("it names fewer than two member slots".into());
    }
    let mut slots = Vec::new();
    for id in &ids {
        let f = analysis
            .features
            .iter()
            .find(|f| f.id == *id && f.kind == "slot")
            .ok_or_else(|| format!("its member {id} is not a slot of the part"))?;
        slots.push(read(f, &cx.frame).map_err(|why| format!("its member {id}: {why}"))?);
    }
    let s = &slots[0];
    let same = |a: f64, b: f64| (a - b).abs() < 1e-6;
    if !slots.iter().all(|t| {
        (t.long, t.wide, t.deep) == (s.long, s.wide, s.deep)
            && same(t.width, s.width)
            && same(t.length, s.length)
            && t.end_radius.is_some() == s.end_radius.is_some()
            && t.end_radius
                .zip(s.end_radius)
                .is_none_or(|(a, b)| same(a, b))
    }) {
        return Err("its slots are not identical".into());
    }
    let view = end_on(s.deep);
    let in_view = [s.long, s.wide];
    let centres: Vec<P3> = slots
        .iter()
        .map(|t| t.at(0.5 * (t.ends[0] + t.ends[1]), t.centre))
        .collect();
    let number = |k: &str| {
        p.parameters
            .get(k)
            .and_then(Value::as_f64)
            .ok_or_else(|| format!("its record gives no {k}"))
    };
    // A direction of quiddity's frame, as the world axis it lies along.
    let world = |local: P3| {
        let d = (0..3).fold([0.0; 3], |d, j| {
            [0, 1, 2].map(|k| d[k] + local[j] * cx.frame.0[j + 1][k])
        });
        principal(d)
            .filter(|k| in_view.contains(k))
            .ok_or("its arrangement lies along no axis of the view that looks through it")
    };
    let n = slots.len();
    let mut out = Vec::new();
    let (centre, why) = if p.fingerprint.subtype == "SlotGrid" {
        // quiddity's lattice: columns along `angle` from the first axis of its plane basis
        // (`_plane_uv`: x → (y, z), y → (z, x), z → (x, y)), rows across them.
        let qd = AXES
            .iter()
            .position(|a| {
                ![
                    &s.feature.parameters["long_axis"],
                    &s.feature.parameters["width_axis"],
                ]
                .contains(&&Value::from(*a))
            })
            .ok_or("its slots' axes are not two of quiddity's")?;
        let (u, v) = ((qd + 1) % 3, (qd + 2) % 3);
        let a = number("angle")?.to_radians();
        let mut col = [0.0; 3];
        col[u] = a.cos();
        col[v] = a.sin();
        let col = world(col)?;
        let row = s.long + s.wide - col;
        let count = |k: &str| {
            p.parameters
                .get(k)
                .and_then(Value::as_u64)
                .ok_or_else(|| format!("its record gives no {k}"))
        };
        let (cols, rows) = (count("cols")?, count("rows")?);
        for (along, m, pitch, name) in [
            (col, cols, number("col_pitch")?, "col"),
            (row, rows, number("row_pitch")?, "row"),
        ] {
            let values = spaced(&centres, along, m as usize, pitch).ok_or_else(|| {
                format!(
                    "its {name}s are not {m} at {} along {}",
                    fmt(pitch),
                    AXES[along]
                )
            })?;
            if values.len() > 1 {
                out.push(pitch_want(
                    &p.id,
                    &format!("grid_pitch.length.{name}"),
                    view,
                    centres[0],
                    along,
                    &values,
                    cx.spec.units,
                ));
            }
        }
        if (rows * cols) as usize != n {
            return Err(format!("its {n} slots are not {rows}×{cols}"));
        }
        let c = point(p.parameters.get("center")).ok_or("its record gives no centre")?;
        (
            cx.frame.point(c),
            format!("a grid of {rows}×{cols} identical slots"),
        )
    } else {
        let along =
            world(point(p.parameters.get("direction")).ok_or("its record gives no direction")?)?;
        let across = s.long + s.wide - along;
        let values = spaced(&centres, along, n, number("pitch")?)
            .filter(|_| spaced(&centres, across, 1, 0.0).is_some())
            .ok_or_else(|| {
                format!(
                    "its slots are not in one line along {} at its pitch",
                    AXES[along]
                )
            })?;
        out.push(pitch_want(
            &p.id,
            "pitch.length",
            view,
            centres[0],
            along,
            &values,
            cx.spec.units,
        ));
        let c = centres
            .iter()
            .fold([0.0; 3], |c, m| [0, 1, 2].map(|k| c[k] + m[k] / n as f64));
        (c, format!("a linear array of {n} identical slots"))
    };

    let m = |parameter: &str, value: f64| Measurement {
        feature: p.id.clone(),
        parameter: parameter.into(),
        value,
    };
    let members: Vec<&Slot> = slots.iter().collect();
    let mut items = vec![slot_callout(
        want_id(&p.id, "slot_width.length"),
        &members,
        vec![
            m("slot_width.length", s.width),
            m("slot_length.length", s.length),
        ],
        format!(
            "{why} is called out once with its width and length, in the view that looks through them, as draftwright's grouped slot callout{}",
            if full_round(s) {
                "; its ends are full-round, half the width, which the callout implies"
            } else {
                ""
            }
        ),
        analysis,
        cx.spec.units,
    )];
    if let Some(r) = s.end_radius.filter(|_| !full_round(s)) {
        items.push(end_radius_want(
            want_id(&p.id, "slot_end_radius.radius"),
            s,
            r,
            vec![m("slot_end_radius.radius", r)],
            "the pattern's obround ends are proven arcs, not full-round: their radius is called out once, with a leader to an end of one slot",
            analysis,
         cx.spec.units));
    }
    items.extend(out);
    let [lo, _] = analysis.envelope;
    let mut problems = Vec::new();
    for k in [s.wide.min(s.long), s.wide.max(s.long)] {
        let d = centre[k] - lo[k];
        if d.abs() < 1e-6 {
            problems.push(Problem {
                id: format!("p:location_on_datum:{}/{}", p.id, AXES[k]),
                code: "location_on_datum".into(),
                severity: "info".into(),
                message: format!(
                    "{} is centred on the part's {} face, so its {} location measures nothing and is not dimensioned.",
                    crate::wording::capital(&crate::wording::feature_id(cx.analysis, &p.id)),
                    ["left", "front", "bottom"][k],
                    AXES[k]
                ),
                subjects: vec![p.id.clone()],
                ..Default::default()
            });
            continue;
        }
        let parameter = format!("location_slot_pattern.length.{}", AXES[k]);
        let mut a = centre;
        a[k] = lo[k];
        items.push(Want { units: cx.spec.units,
            id: want_id(&p.id, &parameter),
            views: vec![view],
            value: d,
            terms: vec![fmt(d)],
            measurements: vec![m(&parameter, d)],
            turning: None,
            basis: format!(
                "the pattern is located by its centre, in {} from the part's {} face, in the view that looks through its slots",
                AXES[k],
                ["left", "front", "bottom"][k]
            ),
            shape: Shape::Linear {
                a,
                b: centre,
                axis: k,
                a_edge: true,
                b_edge: false,
                order: 1,
            },
        });
    }
    Ok((items, problems, ids))
}

/// The centres' distinct positions along *axis*, when there are *count* of them *pitch* apart.
fn spaced(centres: &[P3], axis: usize, count: usize, pitch: f64) -> Option<Vec<f64>> {
    let mut v: Vec<f64> = centres.iter().map(|c| c[axis]).collect();
    v.sort_by(f64::total_cmp);
    v.dedup_by(|a, b| (*a - *b).abs() < 1e-3);
    (v.len() == count && v.windows(2).all(|w| (w[1] - w[0] - pitch).abs() < 1e-3)).then_some(v)
}

/// The pitch between a pattern's first two positions *values* along *along*, counted across its
/// intervals (`2× 25`).
fn pitch_want(
    pattern: &str,
    parameter: &str,
    view: &'static str,
    at: P3,
    along: usize,
    values: &[f64],
    units: crate::units::Units,
) -> Want {
    let fmt = |v| crate::dims::fmt_in(v, units);
    let p = values[1] - values[0];
    let intervals = values.len() - 1;
    let text = if intervals > 1 {
        format!("{intervals}× {}", fmt(p))
    } else {
        fmt(p)
    };
    let (mut a, mut b) = (at, at);
    a[along] = values[0];
    b[along] = values[1];
    Want {
        units: units,
        id: want_id(pattern, parameter),
        views: vec![view],
        value: p,
        terms: vec![text],
        measurements: vec![Measurement {
            feature: pattern.into(),
            parameter: parameter.into(),
            value: p,
        }],
        turning: None,
        basis: format!(
            "the slot pattern's spacing in {}, between its first two members, counted across its {intervals} interval{}",
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

/// A blind slot: a section recess draftwright reads as a rectangular or a round-bottom open
/// slot (`section_recess_fields`), in the part's frame.
struct Blind<'a> {
    feature: &'a Feature,
    /// Round-bottom (two equal quarter arcs and a flat floor) rather than rectangular.
    round: bool,
    /// The world axes it spans across and is cut into (the view looks along the second).
    wide: usize,
    deep: usize,
    centre: P3,
    width: f64,
    length: f64,
    /// Its depth; a round-bottom slot's is its arcs' radius.
    depth: f64,
}

/// A direction of quiddity's frame that lies along one of its axes: that axis and the sign.
fn along(v: P3) -> Option<(usize, f64)> {
    let i = (0..3).max_by(|&a, &b| v[a].abs().total_cmp(&v[b].abs()))?;
    let sign = v[i].signum();
    (0..3)
        .all(|k| (v[k] - if k == i { sign } else { 0.0 }).abs() <= 1e-9)
        .then_some((i, sign))
}

/// *f* as a blind slot, when draftwright's `section_recess_fields` reads it as one: an
/// edge-open recess with defining-face evidence, planar run ends normal to its run, a principal
/// frame, and an open four-vertex U section, either straight with two perpendicular walls (`rectangular`) or two
/// equal quarter arcs joined by a flat floor (`general`). Anything else (pockets, channels,
/// passages, other profiles) is not a slot here, and stays reported as not dimensioned.
fn blind<'a>(f: &'a Feature, frame: &QFrame) -> Option<Blind<'a>> {
    let p = &f.parameters;
    // draftwright refuses a record without defining-face evidence.
    if p.get("evidence")?
        .get("defining_faces")?
        .as_array()?
        .is_empty()
    {
        return None;
    }
    let class = p.get("classification")?;
    let shape = class.get("section_shape")?.as_str()?;
    if class.get("feature_kind")?.as_str()? != "edge_open_recess"
        || !matches!(shape, "rectangular" | "general")
    {
        return None;
    }
    let g = p.get("geometry")?;
    for end in ["low", "high"] {
        let s = g.get("ends")?.get(end)?.get("surface")?;
        let flat = s
            .get("gradient")?
            .as_array()?
            .iter()
            .all(|x| x.as_f64() == Some(0.0));
        if s.get("type")?.as_str()? != "plane" || !flat {
            return None;
        }
    }
    let fr = g.get("frame")?;
    let origin = point(fr.get("origin"))?;
    let (ri, rs) = along(point(fr.get("run"))?)?;
    let (ui, us) = along(point(fr.get("u"))?)?;
    let (vi, vs) = along(point(fr.get("v"))?)?;
    if ri == ui || ri == vi || ui == vi {
        return None;
    }
    let profile = g.get("profile")?;
    if profile.get("closure")?.as_str()? != "open" {
        return None;
    }
    let mut pts = Vec::new();
    let mut bulges = Vec::new();
    for v in profile.get("boundary")?.as_array()? {
        let xy = v.get("point")?.as_array()?;
        pts.push([xy.first()?.as_f64()?, xy.get(1)?.as_f64()?]);
        bulges.push(v.get("bulge")?.as_f64()?);
    }
    if pts.len() != 4 {
        return None;
    }
    // The opening joins the first and last vertices, across the width.
    let across: Vec<usize> = (0..2).filter(|&i| pts[0][i] != pts[3][i]).collect();
    let [wl] = across[..] else { return None };
    let dl = 1 - wl;
    let (open, floor) = (pts[0][dl], pts[1][dl]);
    if pts[2][dl] != floor || open == floor {
        return None;
    }
    let [(wi, ws), (di, ds)] = [[(ui, us), (vi, vs)][wl], [(ui, us), (vi, vs)][dl]];
    let width = (pts[3][wl] - pts[0][wl]).abs();
    let depth = (open - floor).abs();
    let run = g.get("run_interval")?.as_array()?;
    let (low, high) = (run.first()?.as_f64()?, run.get(1)?.as_f64()?);
    let length = high - low;
    let mid = |i: usize, s: f64, a: f64, b: f64| origin[i] + s * 0.5 * (a + b);
    let mut centre = [0.0; 3];
    centre[ri] = mid(ri, rs, low, high);
    centre[wi] = mid(wi, ws, pts[0][wl], pts[3][wl]);
    centre[di] = mid(di, ds, floor, open);
    if !(width > 0.0 && depth > 0.0 && length > 0.0) {
        return None;
    }
    let round = if bulges.iter().all(|b| *b == 0.0) {
        if shape != "rectangular" || pts[0][wl] != pts[1][wl] || pts[2][wl] != pts[3][wl] {
            return None;
        }
        false
    } else {
        // Two equal quarter arcs (bulge tan(22.5°), signed by the section's winding) and a
        // straight floor between them.
        let winding = (pts[1][0] - pts[0][0]) * (pts[2][1] - pts[1][1])
            - (pts[1][1] - pts[0][1]) * (pts[2][0] - pts[1][0]);
        let quarter = 0.414213562373 * if winding > 0.0 { 1.0 } else { -1.0 };
        let inset = if pts[3][wl] > pts[0][wl] { 1.0 } else { -1.0 };
        if shape != "general"
            || bulges != [quarter, 0.0, quarter, 0.0]
            || (pts[1][wl] - pts[0][wl] - inset * depth).abs() > 1e-9
            || (pts[3][wl] - pts[2][wl] - inset * depth).abs() > 1e-9
            || width <= 2.0 * depth
        {
            return None;
        }
        true
    };
    // Invariant: AXES contains exactly the three supported coordinate letters.
    let world = |i: usize| principal(frame.letter(&Value::from(AXES[i])).expect("a letter"));
    Some(Blind {
        feature: f,
        round,
        wide: world(wi)?,
        deep: world(di)?,
        centre: frame.point(centre),
        width,
        length,
        depth,
    })
}

impl Blind<'_> {
    /// Its callout, in the view that looks into it, as draftwright prints it: `OPEN SLOT W × L
    /// × D DEEP`, or `ROUND-BOTTOM OPEN SLOT F BOTTOM FLAT × Rr × L LONG` (the floor's flat
    /// width, the arcs' radius and the length), with a leader to a side wall.
    fn want(&self, units: crate::units::Units) -> Want {
        let fmt = |v| crate::dims::fmt_in(v, units);
        let id = &self.feature.id;
        let view = end_on(self.deep);
        let m = |parameter: &str, value: f64| Measurement {
            feature: id.clone(),
            parameter: parameter.into(),
            value,
        };
        let (terms, measurements, basis) = if self.round {
            let flat = self.width - 2.0 * self.depth;
            let mut t = words("ROUND-BOTTOM OPEN SLOT");
            t.extend([fmt(flat), "BOTTOM".into(), "FLAT".into(), "×".into()]);
            t.extend([
                format!("R{}", fmt(self.depth)),
                "×".into(),
                fmt(self.length),
            ]);
            t.push("LONG".into());
            (
                t,
                vec![
                    m("round_bottom_blind_slot_flat_width.length", flat),
                    m("round_bottom_blind_slot_radius.radius", self.depth),
                    m("round_bottom_blind_slot_length.length", self.length),
                ],
                "a round-bottom blind slot open at an edge: its floor's flat width, its arcs' radius and its length, in one callout in the view that looks into it",
            )
        } else {
            let mut t = words("OPEN SLOT");
            t.extend([fmt(self.width), "×".into(), fmt(self.length), "×".into()]);
            t.extend([fmt(self.depth), "DEEP".into()]);
            (
                t,
                vec![
                    m("rectangular_blind_slot_width.length", self.width),
                    m("rectangular_blind_slot_length.length", self.length),
                    m("rectangular_blind_slot_depth.length", self.depth),
                ],
                "a rectangular blind slot open at an edge: its width, length and depth, in one callout in the view that looks into it",
            )
        };
        let wall = |s: f64| {
            let mut c = self.centre;
            c[self.wide] += s * 0.5 * self.width;
            (c, 0.0)
        };
        Want {
            units: units,
            id: want_id(id, &measurements[0].parameter),
            views: vec![view],
            value: measurements[0].value,
            terms,
            measurements,
            turning: None,
            basis: basis.into(),
            shape: Shape::Callout {
                arcs: Vec::new(),
                sites: vec![wall(-1.0), wall(1.0)],
            },
        }
    }
}

/// A callout's words, as its terms.
fn words(s: &str) -> Vec<String> {
    s.split(' ').map(String::from).collect()
}

/// A semicircular blind slot's callout, with the point its centre line is located by, the axis
/// across it and the view that carries them, or why it cannot be stated.
fn semicircular(
    frame: &QFrame,
    f: &Feature,
    units: crate::units::Units,
) -> Result<(Want, P3, usize, &'static str), String> {
    let fmt = |v| crate::dims::fmt_in(v, units);
    let p = &f.parameters;
    let number = |name: &str| super::grooves::size(f, name);
    let dir = |name: &str| -> Result<P3, String> {
        let d = point(p.get(name)).ok_or_else(|| format!("its record gives no {name}"))?;
        Ok(crate::bridge::holes::dir_to_part(&frame.0, d))
    };
    let (axis, opening) = (dir("axis")?, dir("opening")?);
    let across = super::angular::cross(axis, opening);
    let (Some(deep), Some(wide)) = (principal(opening), principal(across)) else {
        return Err(
            "its opening or width is not along x, y or z, so no standard view shows it".into(),
        );
    };
    let at = frame.point(point(p.get("at")).ok_or("its record gives no at")?);
    let id = &f.id;
    let m = |parameter: &str, value: f64| Measurement {
        feature: id.clone(),
        parameter: parameter.into(),
        value,
    };
    let radius = number("radius")?;
    let length = number("length")?;
    let depth = radius
        + p.get("straight_height")
            .and_then(Value::as_f64)
            .filter(|h| *h >= 0.0)
            .ok_or("its record gives no straight height")?;
    let width = 2.0 * radius;
    let view = end_on(deep);
    let mut terms = words("ROUND-BOTTOM OPEN SLOT");
    terms.extend([
        fmt(width),
        "×".into(),
        fmt(length),
        "×".into(),
        fmt(depth),
        "DEEP".into(),
    ]);
    let wall = |s: f64| {
        let mut c = at;
        c[wide] += s * radius;
        (c, 0.0)
    };
    let want = Want { units: units,
        id: want_id(id, "semicircular_blind_slot_width.length"),
        views: vec![view],
        value: width,
        terms,
        measurements: vec![
            m("semicircular_blind_slot_width.length", width),
            m("semicircular_blind_slot_length.length", length),
            m("semicircular_blind_slot_depth.length", depth),
        ],
        turning: None,
        basis: "a semicircular blind slot open at an edge: its width (its half-round floor's diameter), length and depth, in one callout in the view that looks into it".into(),
        shape: Shape::Callout {
            arcs: Vec::new(),
            sites: vec![wall(-1.0), wall(1.0)],
        },
    };
    Ok((want, at, wide, view))
}
