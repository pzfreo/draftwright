//! Geometry-backed ladder identity, spans and drawing order. No saved-Spec migration state.
use crate::model::Analysis;
use std::collections::BTreeMap;

const AXES: [&str; 3] = ["x", "y", "z"];
fn thousandths(v: f64) -> i64 {
    (v * 1000.).round() as i64
}

/// Coordinate spelling retained for genuinely faceless evidence and captured drawing claims.
/// This grammar is independent of the spec/1–2 reader's compatibility window.
pub(crate) fn coordinate_word(v: f64) -> String {
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

/// Principal plane identities from either immutable Analysis representation.
fn plane_sites(a: &Analysis) -> Vec<(usize, usize, f64)> {
    let mut out: Vec<_> = a
        .face_attachments
        .iter()
        .enumerate()
        .filter_map(|(face, f)| {
            (f.surface.as_deref() == Some("plane")).then_some(())?;
            Some((face, f.axis?, *f.support.first()?))
        })
        .collect();
    if let Some(ink) = &a.edge_ink {
        use crate::model::dimension_geometry::Reference;
        for r in ink.references.values().flatten() {
            if let Reference::Plane {
                membership,
                origin,
                normal,
                ..
            } = r
                && let Some(axis) = normal.iter().position(|n| n.abs() > 1. - 1e-8)
            {
                out.extend(membership.faces.iter().map(|&f| (f, axis, origin[axis])));
            }
        }
    }
    out
}

/// Face identity need not also be an eligible witness support.
fn plane_face(a: &Analysis, axis: usize, at: f64) -> Option<usize> {
    plane_sites(a)
        .into_iter()
        .filter(|(_, k, v)| *k == axis && thousandths(*v) == thousandths(at))
        .map(|(f, _, _)| f)
        .min()
}

pub(crate) fn height_parameter(a: &Analysis, z: f64) -> String {
    let face = a
        .level_supports
        .iter()
        .filter(|s| thousandths(s.z) == thousandths(z))
        .flat_map(|s| &s.faces)
        .min()
        .copied()
        .or_else(|| plane_face(a, 2, z));
    face.map_or_else(
        || {
            format!(
                "step_height.length.h{}",
                coordinate_word(z - a.envelope[0][2])
            )
        },
        |f| format!("step_height.length.f{f}"),
    )
}

/// Riser stations, including records from older/faceless analyses. A shared face is numbered
/// locally in axis/station order; unrelated face planes cannot renumber these stations.
fn positions(a: &Analysis) -> BTreeMap<(usize, i64), (f64, String)> {
    let mut sites: BTreeMap<(usize, i64), (f64, Option<usize>)> = BTreeMap::new();
    for r in &a.risers {
        for (axis, values) in [(&r.axis, &r.positions), (&r.other_axis, &r.other_positions)] {
            let Some(k) = AXES.iter().position(|x| x == axis) else {
                continue;
            };
            for &at in values {
                let face = r
                    .faces
                    .iter()
                    .min()
                    .copied()
                    .or_else(|| plane_face(a, k, at));
                let key = (k, thousandths((at - a.envelope[0][k]).abs()));
                sites
                    .entry(key)
                    .and_modify(|(_, f)| {
                        *f = f.iter().chain(face.iter()).min().copied();
                    })
                    .or_insert((at, face));
            }
        }
    }
    let mut groups: BTreeMap<usize, Vec<(usize, i64)>> = BTreeMap::new();
    for (&site, (_, face)) in &sites {
        if let Some(face) = face {
            groups.entry(*face).or_default().push(site);
        }
    }
    let mut names = BTreeMap::new();
    for (face, group) in groups {
        let shared = group.len() > 1;
        for (i, site) in group.into_iter().enumerate() {
            names.insert(
                site,
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
    sites
        .into_iter()
        .map(|(site @ (k, v), (at, _))| {
            let name = names.remove(&site).unwrap_or_else(|| {
                format!(
                    "step_position.length.{}{}",
                    AXES[k],
                    coordinate_word(v as f64 / 1000.)
                )
            });
            (site, (at, name))
        })
        .collect()
}

pub(crate) fn position_parameter(a: &Analysis, k: usize, at: f64) -> String {
    let key = (k, thousandths((at - a.envelope[0][k]).abs()));
    positions(a)
        .get(&key)
        .map(|(_, name)| name.clone())
        .unwrap_or_else(|| {
            plane_face(a, k, at).map_or_else(
                || {
                    format!(
                        "step_position.length.{}{}",
                        AXES[k],
                        coordinate_word((at - a.envelope[0][k]).abs())
                    )
                },
                |f| format!("step_position.length.f{f}"),
            )
        })
}

pub(crate) fn heights(a: &Analysis) -> Vec<f64> {
    let mut zs: Vec<_> = a
        .levels
        .iter()
        .copied()
        .chain(a.level_supports.iter().map(|s| s.z))
        .collect();
    zs.sort_by(f64::total_cmp);
    zs.dedup_by_key(|z| thousandths(*z));
    zs
}

/// The current item's geometric interval, without reverse-looking-up a migration alias.
pub(crate) fn parameter_span(a: &Analysis, parameter: &str) -> Option<(usize, f64, f64)> {
    if parameter.starts_with("step_height.length.") {
        return heights(a)
            .into_iter()
            .find(|&z| height_parameter(a, z) == parameter)
            .map(|z| (2, a.envelope[0][2], z));
    }
    positions(a)
        .into_iter()
        .find(|(_, (_, name))| name == parameter)
        .map(|((k, _), (at, _))| (k, a.envelope[0][k], at))
        .or_else(|| {
            plane_sites(a).into_iter().find_map(|(_, k, at)| {
                (k < 2 && position_parameter(a, k, at) == parameter).then_some((
                    k,
                    a.envelope[0][k],
                    at,
                ))
            })
        })
}
impl Analysis {
    /// The part axis of a live ladder item, determined from its face/riser geometry.
    pub fn ladder_axis(&self, parameter: &str) -> Option<usize> {
        parameter_span(self, parameter).map(|(k, _, _)| k)
    }
}

/// Historical drawing claims have their own coordinate grammar. Reading these claims is
/// independent of reading legacy Specs; internal current-item spans never use this parser.
pub(crate) fn coordinate_claim(a: &Analysis, parameter: &str) -> Option<(usize, f64, f64)> {
    let number = |word: &str| {
        let word = word
            .strip_prefix('m')
            .map_or_else(|| word.to_string(), |s| format!("-{s}"));
        word.replace('_', ".").parse::<f64>().ok()
    };
    if let Some(word) = parameter.strip_prefix("step_height.length.h") {
        return number(word).map(|v| (2, a.envelope[0][2], a.envelope[0][2] + v));
    }
    let word = parameter.strip_prefix("step_position.length.")?;
    let k = AXES.iter().position(|x| word.starts_with(x))?;
    number(&word[1..]).map(|v| (k, a.envelope[0][k], a.envelope[0][k] + v))
}
pub(crate) fn claim_parameter(a: &Analysis, parameter: &str) -> Option<String> {
    let (k, _, at) = coordinate_claim(a, parameter)?;
    Some(if k == 2 && parameter.starts_with("step_height.") {
        height_parameter(a, at)
    } else {
        position_parameter(a, k, at)
    })
}

/// Keep the geometric enumeration before cavity reconciliation removes contradictory records.
/// These ordinals serve drawing order, not saved identity or migration.
pub(crate) fn assign_presentation_order(
    features: &mut [crate::bridge::Feature],
    patterns: &mut [crate::bridge::HolePattern],
) {
    let mut indices: Vec<_> = (0..features.len()).collect();
    indices.sort_by(|&i, &j| {
        (&features[i].kind, &features[i].faces).cmp(&(&features[j].kind, &features[j].faces))
    });
    let mut previous = None;
    let mut n = 0;
    for i in indices {
        if previous.as_ref() == Some(&features[i].kind) {
            n += 1;
        } else {
            n = 1;
        }
        previous = Some(features[i].kind.clone());
        features[i].presentation_order = Some(n);
    }
    let mut indices: Vec<_> = (0..patterns.len()).collect();
    indices.sort_by(|&i, &j| patterns[i].faces.cmp(&patterns[j].faces));
    for (n, i) in indices.into_iter().enumerate() {
        patterns[i].presentation_order = Some(n + 1);
    }
}

/// Decimal enumeration order preserves established drawing ties (10 precedes 2), without
/// consulting legacy_id. Authored and opaque synthetic identities retain their own ordering.
pub(crate) fn presentation_key<'a>(a: &'a Analysis, id: &'a str) -> std::borrow::Cow<'a, str> {
    use std::borrow::Cow;
    let Some((prefix, anchor)) = id.rsplit_once(':') else {
        return Cow::Borrowed(id);
    };
    let automatic = anchor.strip_prefix('f').is_some_and(|s| {
        !s.is_empty()
            && s.split('_')
                .all(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()))
    });
    if !automatic || prefix == "f:declared" {
        return Cow::Borrowed(id);
    }
    let order = if prefix == "f:hole_pattern" {
        a.recognition
            .as_ref()
            .and_then(|r| r.patterns.iter().find(|f| f.id == id))
            .and_then(|f| f.presentation_order)
            .or_else(|| {
                a.recognition.as_ref().and_then(|r| {
                    let mut fs: Vec<_> = r.patterns.iter().collect();
                    fs.sort_by(|a, b| a.faces.cmp(&b.faces));
                    fs.iter().position(|f| f.id == id).map(|n| n + 1)
                })
            })
    } else {
        a.features
            .iter()
            .find(|f| f.id == id)
            .and_then(|f| f.presentation_order)
            .or_else(|| {
                let mut fs: Vec<_> = a
                    .features
                    .iter()
                    .filter(|f| f.id.rsplit_once(':').is_some_and(|(p, _)| p == prefix))
                    .collect();
                fs.sort_by(|a, b| a.faces.cmp(&b.faces));
                fs.iter().position(|f| f.id == id).map(|n| n + 1)
            })
    };
    order.map_or(Cow::Borrowed(id), |n| Cow::Owned(format!("{prefix}:{n}")))
}
