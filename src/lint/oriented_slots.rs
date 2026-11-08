//! Oriented slot coverage (Python's `linting/oriented_slot_coverage.py`): a free-direction slot
//! (quiddity's `OrientedSlot`, a rectangular through passage whose width and length need not lie
//! along the part's axes) states its width and its length
//! (LINT-oriented_slot_coverage-width_length, LINT-code-oriented_slot_requirement_state).
//!
//! Each `oriented_slot` feature has two requirements, `oriented_slot_width.length` and
//! `oriented_slot_length.length`, the record's `width` and `length`, judged by
//! [`parameters::coverage`] (the callout prints both: `SLOT 2 WIDE × 4.5 LONG`). A gap open at
//! both ends ([`open`], a retaining ring's) has its width only: its length is the wall it cuts.
//! Python joins each record to exactly one IR feature by every structural fact, and reports both
//! requirements unverifiable when it cannot; here the Analysis feature is the record, so the join
//! fails only when a record does not give a positive width and length, or when two records are
//! the same (Python's two sources with one key), and then both requirements are unverifiable.
//!
//! Not ported: Python excludes the members of oriented slot patterns (`standalone_oriented_slots`:
//! draftwright gives the patterns no drafting grammar, #1430, and its quality report lists them
//! as an undecided family). The Analysis does not record the patterns (the bridge reads their
//! members one by one, as oriented slots), so every oriented slot is judged here, and a pattern's
//! members are reported where Python reports nothing. Not reached either: `_suppressed` and
//! `_dropped` (lint cannot read the author's omissions or placement drops) and the assembly
//! severity.

use serde_json::Value;

use super::parameters::{self, Wanted, fmt, fmt3};
use super::{LintIssue, Mark};
use crate::bridge::Feature;
use crate::bridge::holes::{dir_to_part, frame, to_part};
use crate::model::Analysis;

/// A record's size *name*, if it is a positive finite number.
fn size(f: &Feature, name: &str) -> Result<f64, String> {
    match parameters::size(f, name)? {
        v if v.is_finite() && v > 0.0 => Ok(v),
        v => Err(format!("its {name} {v} is not a positive size")),
    }
}

type P3 = [f64; 3];

/// An oriented slot's passage in the part's frame: its origin, depth direction (`run`), the two
/// directions across it (`u`, `v`), its depth interval, and its outline across its depth as
/// (u, v) points with each edge's bulge, closed or open.
pub(crate) struct Passage {
    pub origin: P3,
    pub run: P3,
    pub u: P3,
    pub v: P3,
    pub interval: [f64; 2],
    pub boundary: Vec<([f64; 2], f64)>,
    pub closed: bool,
}

/// A slot's outline across its depth: (long, width) points with each edge's bulge, its ends
/// semicircles when its end radius is half its *width*, else a rectangle from *ends* about the
/// width centre *c*.
pub(crate) fn outline(
    width: f64,
    [e0, e1]: [f64; 2],
    c: f64,
    end_radius: Option<f64>,
) -> Vec<([f64; 2], f64)> {
    let half = 0.5 * width;
    match end_radius {
        Some(r) if (r - half).abs() <= 1e-3 && e1 - e0 > 2.0 * r => vec![
            ([e0 + r, c - half], 0.0),
            ([e1 - r, c - half], 1.0),
            ([e1 - r, c + half], 0.0),
            ([e0 + r, c + half], 1.0),
        ],
        _ => vec![
            ([e0, c - half], 0.0),
            ([e1, c - half], 0.0),
            ([e1, c + half], 0.0),
            ([e0, c + half], 0.0),
        ],
    }
}

/// Oriented slot *f*'s [`Passage`] in *analysis*'s part frame. quiddity 0.2.0 gives a slot in a
/// turned frame by its `origin`, unit long, width and depth axes and its extents along them
/// (`lo`/`hi`, `w_center`, `d_lo`/`d_hi`), so its outline is its width about its width centre
/// over its long extent. A record without an origin (Python's oriented slot) gives its proved
/// passage: `source.frame`, `source.run_interval` and `source.section`.
pub(crate) fn passage(analysis: &Analysis, f: &Feature) -> Result<Passage, String> {
    let p = &f.parameters;
    let q = frame(analysis);
    let point = |v: Option<&Value>| -> Option<P3> {
        let a = v?.as_array().filter(|a| a.len() == 3)?;
        Some([a[0].as_f64()?, a[1].as_f64()?, a[2].as_f64()?])
    };
    let unit = |d: P3, what: &str| -> Result<P3, String> {
        let n = d.iter().map(|x| x * x).sum::<f64>().sqrt();
        if n.is_nan() || n <= 1e-9 {
            return Err(format!("its {what} is a zero vector"));
        }
        Ok(dir_to_part(&q, d.map(|x| x / n)))
    };
    let Some(origin) = point(p.get("origin")) else {
        let source = p.get("source").ok_or("its record gives no passage")?;
        let fr = source
            .get("frame")
            .ok_or("its record gives no passage frame")?;
        let origin = point(fr.get("origin")).ok_or("its passage frame has no origin")?;
        let [run, u, v] = ["run", "u", "v"].map(|k| point(fr.get(k)));
        let (Some(run), Some(u), Some(v)) = (run, u, v) else {
            return Err("its passage frame has no run, u or v direction".into());
        };
        let interval: Vec<f64> = source
            .get("run_interval")
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(Value::as_f64).collect())
            .unwrap_or_default();
        let interval =
            <[f64; 2]>::try_from(interval).map_err(|_| "its passage has no run interval")?;
        let section = source.get("section");
        let boundary: Vec<([f64; 2], f64)> = section
            .and_then(|s| s.get("boundary"))
            .and_then(Value::as_array)
            .map(|b| {
                b.iter()
                    .filter_map(|e| {
                        let c = e.get("point")?.as_array()?;
                        let bulge = e.get("bulge").and_then(Value::as_f64).unwrap_or(0.0);
                        Some(([c.first()?.as_f64()?, c.get(1)?.as_f64()?], bulge))
                    })
                    .collect()
            })
            .unwrap_or_default();
        if boundary.len() < 3 {
            return Err("its passage section has no boundary".into());
        }
        return Ok(Passage {
            origin: to_part(&q, origin),
            run: unit(run, "passage run")?,
            u: unit(u, "passage u")?,
            v: unit(v, "passage v")?,
            interval,
            boundary,
            closed: section
                .and_then(|s| s.get("closure"))
                .and_then(Value::as_str)
                != Some("open"),
        });
    };
    let number = |k: &str| {
        p.get(k)
            .and_then(Value::as_f64)
            .filter(|v| v.is_finite())
            .ok_or_else(|| format!("its record gives no {k}"))
    };
    let axis = |k: &str| -> Result<P3, String> {
        let v = p.get(k).ok_or_else(|| format!("its record gives no {k}"))?;
        let d = match v.as_str() {
            Some(l) => {
                let i = ["x", "y", "z"]
                    .iter()
                    .position(|x| *x == l)
                    .ok_or_else(|| format!("its {k} is no axis"))?;
                [0, 1, 2].map(|j| if j == i { 1.0 } else { 0.0 })
            }
            None => point(Some(v)).ok_or_else(|| format!("its {k} is no direction"))?,
        };
        unit(d, k)
    };
    let width = size(f, "width")?;
    Ok(Passage {
        origin: to_part(&q, origin),
        run: axis("depth_axis")?,
        u: axis("long_axis")?,
        v: axis("width_axis")?,
        interval: [number("d_lo")?, number("d_hi")?],
        boundary: outline(
            width,
            [number("lo")?, number("hi")?],
            number("w_center")?,
            p.get("end_radius").and_then(Value::as_f64),
        ),
        closed: true,
    })
}

/// Whether slot *f* is a gap open at both ends: its walls meet every neighbour convexly, so no end
/// wall closes it (a retaining ring's gap, #225), and it has no length of its own.
pub(crate) fn open(f: &Feature) -> bool {
    let n = &f.fingerprint.neighbourhood;
    !n.is_empty() && n.iter().all(|s| s.ends_with(":convex"))
}

/// The oriented slots' requirements: each slot's width and length (an [`open`] gap's width only),
/// or, when its record cannot be read or another record is the same, both unverifiable.
pub fn wanted(analysis: &Analysis) -> Vec<Wanted<'_>> {
    let slots: Vec<&Feature> = analysis
        .features
        .iter()
        .filter(|f| f.kind == "oriented_slot")
        .collect();
    let mut out = Vec::new();
    for f in &slots {
        let twin = slots
            .iter()
            .any(|g| g.id != f.id && g.parameters == f.parameters);
        let sizes = [
            ("oriented_slot_width.length", "width", "wide"),
            ("oriented_slot_length.length", "length", "long"),
        ];
        for &(parameter, name, word) in &sizes[..if open(f) { 1 } else { 2 }] {
            let values = if twin {
                Err("another oriented slot's record is the same, so neither is one slot".into())
            } else {
                size(f, name).map(|v| vec![v])
            };
            let callout = match &values {
                Ok(v) => format!("{} {word}", fmt(v[0])),
                Err(_) => format!("its {name}"),
            };
            out.push(Wanted {
                feature: f,
                parameter: parameter.into(),
                values,
                callout,
            });
        }
    }
    out
}

/// The oriented slot coverage findings.
pub fn coverage(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let frame = frame(analysis);
    let noun = |f: &Feature| {
        let mut s = format!("oriented slot {}", f.id);
        if let (Ok(w), Ok(l)) = (size(f, "width"), size(f, "length")) {
            s += &format!(" {} wide × {} long", fmt(w), fmt(l));
        }
        if let Some(at) = parameters::place(&frame, f.parameters.get("center")) {
            s += &format!(", at {}", fmt3(at));
        }
        s
    };
    parameters::coverage(
        "oriented_slot",
        &wanted(analysis),
        marks,
        noun,
        "the slot's callout (SLOT W WIDE × L LONG, or GAP W WIDE), in a view that looks along its run",
    )
}

#[cfg(test)]
mod tests {
    use super::super::paired_ramp_steps::tests::part;
    use super::*;
    use crate::output::Claim;
    use serde_json::json;

    fn mark(text: &str, parameters: &[&str]) -> Mark {
        Mark {
            name: "m_oriented_slot_x_0".into(),
            view: Some("v:side".into()),
            text: text.into(),
            claims: parameters
                .iter()
                .map(|p| Claim {
                    feature: "f:oriented_slot:1".into(),
                    parameter: p.to_string(),
                })
                .collect(),
        }
    }

    const BOTH: [&str; 2] = ["oriented_slot_width.length", "oriented_slot_length.length"];

    /// mfcadpp_holdout/467's oriented slot (quiddity 0.3.9: 2 wide, 4.543 long): the callout
    /// `SLOT 2 WIDE × 4.5 LONG` states both; one stating the width alone leaves
    /// the length missing; a misprinted length is unverifiable; a record without a length, or
    /// two records the same, cannot be checked.
    #[test]
    fn an_oriented_slot_states_its_width_and_length() {
        let record = json!({ "center": [4.675, 0.369, 0.703], "width": 2.0, "length": 4.543,
                             "width_direction": [0.0, 0.6, 0.8],
                             "long_direction": [0.0, -0.8, 0.6] });
        let a = part("oriented_slot", record.clone());
        let got: Vec<(String, f64)> = wanted(&a)
            .into_iter()
            .map(|w| (w.parameter, w.values.unwrap()[0]))
            .collect();
        assert_eq!(
            got,
            [(BOTH[0].to_string(), 2.0), (BOTH[1].to_string(), 4.543)]
        );
        assert!(coverage(&a, &[mark("SLOT 2 WIDE × 4.5 LONG", &BOTH)]).is_empty());
        let codes = |a: &Analysis, marks: &[Mark]| -> Vec<String> {
            coverage(a, marks)
                .into_iter()
                .map(|i| format!("{} {}", i.code, i.requirements[0].parameter))
                .collect()
        };
        assert_eq!(
            codes(&a, &[mark("2", &BOTH[..1])]),
            ["oriented_slot_requirement_missing oriented_slot_length.length"]
        );
        assert_eq!(
            codes(&a, &[mark("SLOT 2 WIDE × 4.6 LONG", &BOTH)]),
            ["oriented_slot_requirement_unverifiable oriented_slot_length.length"]
        );
        assert_eq!(
            coverage(&a, &[])[0].message,
            "oriented slot f:oriented_slot:1 2 wide × 4.543 long, at (4.675, 0.369, 0.703): \
             requirement oriented_slot_width.length (2 wide) is not stated on the sheet"
        );

        let mut r = record.clone();
        r.as_object_mut().unwrap().remove("length");
        let short = part("oriented_slot", r);
        assert_eq!(
            codes(&short, &[mark("SLOT 2 WIDE × 4.5 LONG", &BOTH)]),
            ["oriented_slot_requirement_unverifiable oriented_slot_length.length"]
        );

        let mut twins = part("oriented_slot", record);
        let mut second = twins.features[0].clone();
        second.id = "f:oriented_slot:2".into();
        twins.features.push(second);
        let found = coverage(&twins, &[mark("SLOT 2 WIDE × 4.5 LONG", &BOTH)]);
        assert_eq!(found.len(), 4);
        assert!(
            found
                .iter()
                .all(|i| i.code == "oriented_slot_requirement_unverifiable"),
            "{found:#?}"
        );
    }

    /// #225: a gap whose walls meet every neighbour convexly (the retaining ring's, open at both
    /// ends) wants its width alone, which `GAP 1.64 WIDE` states; one end wall meeting a wall
    /// concavely closes it, and its length is wanted again.
    #[test]
    fn a_gap_open_at_both_ends_wants_its_width_alone() {
        let record = json!({ "center": [0.0, 0.5, -6.0], "width": 1.64, "length": 3.38 });
        let mut a = part("oriented_slot", record);
        a.features[0].fingerprint.neighbourhood = vec![
            "plane>out:cylinder:convex".into(),
            "plane>out:plane:convex".into(),
        ];
        assert!(open(&a.features[0]));
        let got: Vec<String> = wanted(&a).into_iter().map(|w| w.parameter).collect();
        assert_eq!(got, [BOTH[0]]);
        assert!(coverage(&a, &[mark("GAP 1.64 WIDE", &BOTH[..1])]).is_empty());

        a.features[0]
            .fingerprint
            .neighbourhood
            .push("plane>out:plane:concave".into());
        assert!(!open(&a.features[0]));
        assert_eq!(wanted(&a).len(), 2);
        // A hand-built record with no neighbourhood is no gap.
        a.features[0].fingerprint.neighbourhood.clear();
        assert!(!open(&a.features[0]));
    }
}
