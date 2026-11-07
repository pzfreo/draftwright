//! Physical pocket-pattern requirements: one count, W/L/D, pitches and absolute location.
//! Member pockets belong to this requirement, rather than N lone-pocket requirements.
use super::{
    LintIssue, Mark,
    parameters::{self, Wanted},
    pockets::{self, Placed},
};
use crate::bridge::Feature;
use crate::bridge::holes::{dir_to_part, frame, point, principal};
use crate::model::Analysis;

pub struct Pattern<'a> {
    pub feature: &'a Feature,
    pub members: Vec<&'a Feature>,
    pub pocket: Placed,
    pub centre: [f64; 3],
    /// Parameter, direction, pitch, number of intervals, first member centre.
    pub pitches: Vec<(String, usize, f64, usize, [f64; 3])>,
}

pub fn member(analysis: &Analysis, id: &str) -> bool {
    analysis
        .features
        .iter()
        .filter(|f| f.kind == "pocket_pattern")
        .any(|f| {
            f.parameters
                .get("members")
                .and_then(serde_json::Value::as_array)
                .is_some_and(|m| m.iter().any(|m| m.as_str() == Some(id)))
        })
}

/// Validate the retained members against the declared lattice before certifying measurements.
pub fn read<'a>(a: &'a Analysis, f: &'a Feature) -> Result<Pattern<'a>, String> {
    let all = pockets::placed(a);
    let ids = f
        .parameters
        .get("members")
        .and_then(serde_json::Value::as_array)
        .filter(|m| m.len() >= 2)
        .ok_or("pattern has fewer than two pockets")?;
    let mut members = Vec::new();
    let mut placed = Vec::new();
    for id in ids {
        let id = id.as_str().ok_or("pattern member has no feature ID")?;
        let (feature, p) = all
            .iter()
            .find(|(g, _)| g.id == id)
            .ok_or("pattern lost a pocket")?;
        members.push(*feature);
        placed.push(p.clone()?);
    }
    let p = placed[0].clone();
    if p.depth_axis != 2 {
        return Err("non-Z pocket patterns have no location compiler owner".into());
    }
    let same = |x: f64, y: f64| (x - y).abs() <= 1e-3;
    if !placed.iter().all(|q| {
        q.depth_axis == p.depth_axis
            && q.long_axis == p.long_axis
            && same(q.width, p.width)
            && same(q.length, p.length)
            && same(q.depth, p.depth)
            && q.mouth == p.mouth
            && q.corner_radius == p.corner_radius
    }) {
        return Err("pattern pockets do not have one specification".into());
    }
    if placed.iter().enumerate().any(|(i, q)| {
        !same(q.centre[p.depth_axis], p.centre[p.depth_axis])
            || placed[..i]
                .iter()
                .any(|r| (0..3).all(|k| same(q.centre[k], r.centre[k])))
    }) {
        return Err("pattern members repeat a position or do not share one depth station".into());
    }
    let centre = std::array::from_fn(|k| {
        placed.iter().map(|p| p.centre[k]).sum::<f64>() / placed.len() as f64
    });
    let mut pitches = Vec::new();
    let mut add = |name: &str, dir: &str, size: &str, count: usize| -> Result<(), String> {
        let d = point(f.parameters.get(dir)).ok_or("pattern has no direction")?;
        let k = principal(dir_to_part(&frame(a), d))
            .filter(|k| *k != p.depth_axis)
            .ok_or("pattern direction lies along no in-plane part axis")?;
        let pitch = f
            .parameters
            .get(size)
            .and_then(serde_json::Value::as_f64)
            .filter(|p| *p > 0.0)
            .ok_or("pattern has no positive pitch")?;
        let mut values: Vec<_> = placed.iter().map(|p| p.centre[k]).collect();
        values.sort_by(f64::total_cmp);
        values.dedup_by(|a, b| same(*a, *b));
        if values.len() != count || !values.windows(2).all(|v| same(v[1] - v[0], pitch)) {
            return Err("pattern members do not reconstruct its pitch".into());
        }
        let mut at = placed[0].centre;
        at[k] = values[0];
        pitches.push((name.into(), k, pitch, count - 1, at));
        Ok(())
    };
    if let Some(rows) = f.parameters.get("rows").and_then(serde_json::Value::as_u64) {
        let cols = f
            .parameters
            .get("cols")
            .and_then(serde_json::Value::as_u64)
            .ok_or("pattern has no columns")?;
        if rows < 2 || cols < 2 {
            return Err("pattern grid requires two rows and columns".into());
        }
        if rows.checked_mul(cols) != Some(ids.len() as u64) {
            return Err("pattern grid count disagrees".into());
        }
        add(
            "grid_pitch.length.row",
            "row_direction",
            "row_pitch",
            rows as usize,
        )?;
        add(
            "grid_pitch.length.col",
            "col_direction",
            "col_pitch",
            cols as usize,
        )?;
        if pitches[0].1 == pitches[1].1 {
            return Err("pattern grid directions coincide".into());
        }
    } else {
        add("pitch.length", "direction", "pitch", ids.len())?;
        let k = pitches[0].1;
        if placed
            .iter()
            .any(|q| (0..3).any(|j| j != k && !same(q.centre[j], p.centre[j])))
        {
            return Err("pattern members do not lie on one line".into());
        }
    }
    Ok(Pattern {
        feature: f,
        members,
        pocket: p,
        centre,
        pitches,
    })
}

pub fn coverage(a: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let mut out = Vec::new();
    for f in a.features.iter().filter(|f| f.kind == "pocket_pattern") {
        let mut wanted = Vec::new();
        match read(a, f) {
            Err(why) => wanted.push(Wanted {
                feature: f,
                parameter: "pattern.?".into(),
                values: Err(why),
                callout: "its physical requirements".into(),
            }),
            Ok(p) => {
                let depth = if p.pocket.mouth {
                    "pocket_max_depth.length"
                } else {
                    "pocket_depth.length"
                };
                for (name, v) in [
                    ("grouping.count", p.members.len() as f64),
                    ("pocket_width.length", p.pocket.width),
                    ("pocket_length.length", p.pocket.length),
                    (depth, p.pocket.depth),
                ] {
                    if name == "pocket_max_depth.length"
                        && p.members
                            .iter()
                            .all(|member| pockets::alternative_depth(a, member, &f.id, marks))
                    {
                        continue;
                    }
                    wanted.push(Wanted {
                        feature: f,
                        parameter: name.into(),
                        values: Ok(vec![v]),
                        callout: parameters::fmt(v),
                    });
                }
                for (name, _, v, _, _) in p.pitches {
                    wanted.push(Wanted {
                        feature: f,
                        parameter: name,
                        values: Ok(vec![v]),
                        callout: parameters::fmt(v),
                    });
                }
                for k in [p.pocket.long_axis, p.pocket.width_axis] {
                    let v = p.centre[k] - a.envelope[0][k];
                    if v.abs() > 1e-6 {
                        wanted.push(Wanted {
                            feature: f,
                            parameter: format!(
                                "location_pocket_pattern.location.{}",
                                ["x", "y", "z"][k]
                            ),
                            values: Ok(vec![v]),
                            callout: parameters::fmt(v),
                        });
                    }
                }
            }
        }
        let mut shown = Vec::new();
        for m in marks.iter().filter(|m| !m.name.starts_with("suppressed:")) {
            let mut ordinary = m.clone();
            let count_claims: Vec<_> = ordinary
                .claims
                .iter()
                .filter(|c| c.feature == f.id && c.parameter == "grouping.count")
                .cloned()
                .collect();
            if !count_claims.is_empty() {
                // Quantity is the explicit leading N× token, never an equal width or
                // length numeral elsewhere in the size/depth callout.
                let mut count = m.clone();
                count.claims = count_claims;
                count.text = m
                    .text
                    .split_whitespace()
                    .next()
                    .and_then(|s| s.strip_suffix('×'))
                    .and_then(|s| s.parse::<usize>().ok())
                    .map(|n| n.to_string())
                    .unwrap_or_default();
                ordinary
                    .claims
                    .retain(|c| c.feature != f.id || c.parameter != "grouping.count");
                shown.push(count);
            }
            shown.push(ordinary);
        }
        let mut issues = parameters::coverage(
            "pocket_pattern",
            &wanted,
            &shown,
            |f| format!("pocket pattern {}", f.id),
            "one grouped callout, pitch dimensions and the pattern centre's absolute location",
        );
        for i in &mut issues {
            if marks.iter().any(|m| {
                m.name.starts_with("suppressed:")
                    && m.claims.iter().any(|c| {
                        c.feature == f.id
                            && (i.requirements.iter().any(|r| r.parameter == c.parameter)
                                || i.requirements
                                    .iter()
                                    .any(|r| r.parameter == "grouping.count")
                                    && c.parameter.starts_with("pocket_"))
                    })
            }) {
                i.code = "pocket_pattern_requirement_suppressed".into();
                i.id = format!("l:{}:{}/{}", i.code, f.id, i.requirements[0].parameter);
                i.message = format!(
                    "pocket pattern {}: {} was deliberately omitted by the authored dimension set",
                    f.id, i.requirements[0].parameter
                );
            }
        }
        out.extend(issues);
    }
    out
}
