//! Face-anchored ladder names and their exact legacy spellings, independent of planning and lint.
use crate::model::Analysis;

const AXES: [&str; 3] = ["x", "y", "z"];
fn thousandths(v: f64) -> i64 {
    (v * 1000.).round() as i64
}

/// The legacy value word used only by the exact migration map: 16 → `16`, 12.5 → `12_5`, -3.25 → `m3_25`.
fn word(v: f64) -> String {
    let t = thousandths(v);
    let (sign, t) = if t < 0 { ("m", -t) } else { ("", t) };
    let frac = format!("{:03}", t % 1000);
    let frac = frac.trim_end_matches('0');
    if frac.is_empty() {
        format!("{sign}{}", t / 1000)
    } else {
        format!("{sign}{}_{frac}", t / 1000)
    }
}

/// The exact old-parameter → face-parameter map, shared by migration, planning and lint.
/// Old analyses without faces keep their old spelling; current evidence always has faces.
pub(crate) fn legacy_ids(a: &Analysis) -> std::collections::BTreeMap<String, String> {
    use std::collections::BTreeMap;
    let mut heights: BTreeMap<i64, usize> = BTreeMap::new();
    for s in &a.level_supports {
        if let Some(&face) = s.faces.iter().min() {
            heights
                .entry(thousandths(s.z - a.envelope[0][2]))
                .and_modify(|f| *f = (*f).min(face))
                .or_insert(face);
        }
    }
    let mut out = BTreeMap::new();
    for (height, face) in heights {
        out.insert(
            format!("step_height.length.h{}", word(height as f64 / 1000.)),
            format!("step_height.length.f{face}"),
        );
    }
    let mut sites: BTreeMap<(usize, i64), usize> = BTreeMap::new();
    for r in &a.risers {
        let Some(&face) = r.faces.iter().min() else {
            continue;
        };
        for (axis, positions) in [(&r.axis, &r.positions), (&r.other_axis, &r.other_positions)] {
            let Some(k) = AXES.iter().position(|a| a == axis) else {
                continue;
            };
            for at in positions {
                let site = (k, thousandths((at - a.envelope[0][k]).abs()));
                sites
                    .entry(site)
                    .and_modify(|f| *f = (*f).min(face))
                    .or_insert(face);
            }
        }
    }
    let mut groups: BTreeMap<usize, Vec<(usize, i64)>> = BTreeMap::new();
    for (site, face) in sites {
        groups.entry(face).or_default().push(site);
    }
    for (face, sites) in groups {
        let shared = sites.len() > 1;
        for (i, (k, value)) in sites.into_iter().enumerate() {
            out.insert(
                format!(
                    "step_position.length.{}{}",
                    AXES[k],
                    word(value as f64 / 1000.)
                ),
                format!(
                    "step_position.length.f{face}{}",
                    if shared {
                        format!("_{i}")
                    } else {
                        String::new()
                    }
                ),
            );
        }
    }
    out
}

pub(crate) fn height_parameter(a: &Analysis, z: f64) -> String {
    let old = format!("step_height.length.h{}", word(z - a.envelope[0][2]));
    legacy_ids(a).get(&old).cloned().unwrap_or(old)
}
pub(crate) fn position_parameter(a: &Analysis, k: usize, at: f64) -> String {
    let old = format!(
        "step_position.length.{}{}",
        AXES[k],
        word((at - a.envelope[0][k]).abs())
    );
    legacy_ids(a).get(&old).cloned().unwrap_or(old)
}
