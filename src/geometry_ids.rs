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

#[derive(Default)]
struct Index {
    planes: BTreeMap<(usize, i64), usize>,
    heights: BTreeMap<i64, String>,
    zs: Vec<f64>,
    positions: BTreeMap<(usize, i64), String>,
    spans: BTreeMap<String, (usize, f64, f64)>,
    presentation: BTreeMap<String, String>,
    stations: BTreeMap<String, String>,
}

impl Index {
    fn new(a: &Analysis) -> Self {
        let sites = plane_sites(a);
        let mut out = Self::default();
        for &(face, k, at) in &sites {
            out.planes
                .entry((k, thousandths(at)))
                .and_modify(|f| *f = (*f).min(face))
                .or_insert(face);
        }
        let mut supports = BTreeMap::<i64, usize>::new();
        for support in &a.level_supports {
            if let Some(&face) = support.faces.iter().min() {
                supports
                    .entry(thousandths(support.z))
                    .and_modify(|f| *f = (*f).min(face))
                    .or_insert(face);
            }
        }
        out.zs = raw_heights(a);
        for &z in &out.zs {
            let face = supports
                .get(&thousandths(z))
                .or_else(|| out.planes.get(&(2, thousandths(z))));
            let parameter = face.map_or_else(
                || {
                    format!(
                        "step_height.length.h{}",
                        coordinate_word(z - a.envelope[0][2])
                    )
                },
                |f| format!("step_height.length.f{f}"),
            );
            out.heights.insert(thousandths(z), parameter.clone());
            out.spans
                .entry(parameter)
                .or_insert((2, a.envelope[0][2], z));
        }
        let mut stations = BTreeMap::<(usize, i64), (f64, Option<usize>)>::new();
        for riser in &a.risers {
            for (axis, values) in [
                (&riser.axis, &riser.positions),
                (&riser.other_axis, &riser.other_positions),
            ] {
                let Some(k) = AXES.iter().position(|x| x == axis) else {
                    continue;
                };
                for &at in values {
                    let face = riser
                        .faces
                        .iter()
                        .min()
                        .copied()
                        .or_else(|| out.planes.get(&(k, thousandths(at))).copied());
                    stations
                        .entry((k, thousandths((at - a.envelope[0][k]).abs())))
                        .and_modify(|(_, f)| *f = f.iter().chain(face.iter()).min().copied())
                        .or_insert((at, face));
                }
            }
        }
        let mut groups = BTreeMap::<usize, Vec<(usize, i64)>>::new();
        for (&site, (_, face)) in &stations {
            if let Some(face) = face {
                groups.entry(*face).or_default().push(site);
            }
        }
        for (face, group) in groups {
            let shared = group.len() > 1;
            for (i, site) in group.into_iter().enumerate() {
                out.positions.insert(
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
        for (&site @ (k, v), &(at, _)) in &stations {
            let parameter = out
                .positions
                .entry(site)
                .or_insert_with(|| {
                    format!(
                        "step_position.length.{}{}",
                        AXES[k],
                        coordinate_word(v as f64 / 1000.)
                    )
                })
                .clone();
            out.spans
                .entry(parameter)
                .or_insert((k, a.envelope[0][k], at));
        }
        // Keep the previous principal-plane fallback order and riser precedence.
        for &(_, k, at) in &sites {
            if k < 2 {
                let parameter = out.position(a, k, at);
                out.spans
                    .entry(parameter)
                    .or_insert((k, a.envelope[0][k], at));
            }
        }
        let mut first_orders = BTreeMap::new();
        for f in &a.features {
            first_orders
                .entry(f.id.as_str())
                .or_insert(f.presentation_order);
        }
        let mut families = BTreeMap::<String, Vec<&crate::bridge::Feature>>::new();
        for f in &a.features {
            if let Some((prefix, _)) = f.id.rsplit_once(':') {
                families.entry(prefix.into()).or_default().push(f);
            }
        }
        for (prefix, mut fs) in families {
            fs.sort_by(|a, b| a.faces.cmp(&b.faces));
            for (n, f) in fs.into_iter().enumerate() {
                if automatic(&f.id) {
                    out.presentation.entry(f.id.clone()).or_insert_with(|| {
                        format!("{prefix}:{}", first_orders[&f.id.as_str()].unwrap_or(n + 1))
                    });
                }
            }
        }
        if let Some(r) = &a.recognition {
            let mut first_orders = BTreeMap::new();
            for p in &r.patterns {
                first_orders
                    .entry(p.id.as_str())
                    .or_insert(p.presentation_order);
            }
            let mut ps: Vec<_> = r.patterns.iter().collect();
            ps.sort_by(|a, b| a.faces.cmp(&b.faces));
            for (n, p) in ps.into_iter().enumerate() {
                if automatic(&p.id) {
                    out.presentation.entry(p.id.clone()).or_insert_with(|| {
                        format!(
                            "f:hole_pattern:{}",
                            first_orders[&p.id.as_str()].unwrap_or(n + 1)
                        )
                    });
                }
            }
        }
        let patterns = a
            .recognition
            .as_ref()
            .map_or(&[][..], |r| r.patterns.as_slice());
        // Canonical face identity survives spelling changes, independently of migration aliases.
        let features: Vec<_> = a.features.iter().filter(|f| !f.faces.is_empty()).collect();
        let patterns: Vec<_> = patterns.iter().filter(|p| !p.faces.is_empty()).collect();
        if let Ok(names) = face_names(
            features
                .iter()
                .map(|f| (f.kind.as_str(), f.faces.as_slice()))
                .chain(
                    patterns
                        .iter()
                        .map(|p| ("hole_pattern", p.faces.as_slice())),
                )
                .collect(),
        ) {
            out.stations.extend(
                features
                    .iter()
                    .map(|f| f.id.clone())
                    .chain(patterns.iter().map(|p| p.id.clone()))
                    .zip(names),
            );
        }
        out
    }
    fn position(&self, a: &Analysis, k: usize, at: f64) -> String {
        self.positions
            .get(&(k, thousandths((at - a.envelope[0][k]).abs())))
            .cloned()
            .unwrap_or_else(|| {
                self.planes.get(&(k, thousandths(at))).map_or_else(
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
}

fn automatic(id: &str) -> bool {
    id.rsplit_once(':').is_some_and(|(prefix, anchor)| {
        prefix != "f:declared"
            && anchor.strip_prefix('f').is_some_and(|s| {
                !s.is_empty()
                    && s.split('_')
                        .all(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()))
            })
    })
}

// Lexically scoped immutable preparation. No persistent address cache: mutating an
// Analysis between calls cannot reuse stale evidence, and nested inputs restore on exit.
type ActiveIndex = Option<(usize, Option<std::rc::Rc<Index>>)>;
thread_local! {
    static ACTIVE: std::cell::RefCell<ActiveIndex> = const {std::cell::RefCell::new(None)};
}
pub(crate) fn with<T>(a: &Analysis, run: impl FnOnce() -> T) -> T {
    let identity = a as *const Analysis as usize;
    if ACTIVE.with(|v| v.borrow().as_ref().is_some_and(|(p, _)| *p == identity)) {
        return run();
    }
    struct Restore(ActiveIndex);
    impl Drop for Restore {
        fn drop(&mut self) {
            ACTIVE.with(|v| *v.borrow_mut() = self.0.take());
        }
    }
    let _restore = Restore(ACTIVE.with(|v| v.replace(Some((identity, None)))));
    run()
}
fn index(a: &Analysis) -> std::rc::Rc<Index> {
    let identity = a as *const Analysis as usize;
    if let Some(found) = ACTIVE.with(|v| {
        v.borrow()
            .as_ref()
            .filter(|(p, _)| *p == identity)
            .and_then(|(_, i)| i.clone())
    }) {
        return found;
    }
    let found = std::rc::Rc::new(Index::new(a));
    ACTIVE.with(|v| {
        if let Some((p, i)) = &mut *v.borrow_mut()
            && *p == identity
        {
            *i = Some(found.clone());
        }
    });
    found
}

pub(crate) fn height_parameter(a: &Analysis, z: f64) -> String {
    let i = index(a);
    i.heights.get(&thousandths(z)).cloned().unwrap_or_else(|| {
        i.planes.get(&(2, thousandths(z))).map_or_else(
            || {
                format!(
                    "step_height.length.h{}",
                    coordinate_word(z - a.envelope[0][2])
                )
            },
            |f| format!("step_height.length.f{f}"),
        )
    })
}
pub(crate) fn position_parameter(a: &Analysis, k: usize, at: f64) -> String {
    index(a).position(a, k, at)
}
pub(crate) fn heights(a: &Analysis) -> Vec<f64> {
    index(a).zs.clone()
}
pub(crate) fn parameter_span(a: &Analysis, parameter: &str) -> Option<(usize, f64, f64)> {
    if !parameter.starts_with("step_height.length.")
        && !parameter.starts_with("step_position.length.")
    {
        return None;
    }
    index(a).spans.get(parameter).copied()
}
impl Analysis {
    /// The part axis of a live ladder item, determined from its face/riser geometry.
    pub fn ladder_axis(&self, parameter: &str) -> Option<usize> {
        parameter_span(self, parameter).map(|(k, _, _)| k)
    }
}
fn raw_heights(a: &Analysis) -> Vec<f64> {
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
pub(crate) fn presentation_key<'a>(a: &Analysis, id: &'a str) -> std::borrow::Cow<'a, str> {
    index(a)
        .presentation
        .get(id)
        .map_or(std::borrow::Cow::Borrowed(id), |key| {
            std::borrow::Cow::Owned(key.clone())
        })
}

/// Unsigned key with exactly f64::total_cmp ordering (including signed zero).
pub(crate) fn coordinate_order(value: f64) -> u64 {
    let bits = value.to_bits();
    bits ^ if bits >> 63 == 0 {
        1_u64 << 63
    } else {
        u64::MAX
    }
}

pub(crate) fn feature_names(
    features: &[crate::bridge::Feature],
    patterns: &[crate::bridge::HolePattern],
) -> Result<Vec<String>, String> {
    let records: Vec<_> = features
        .iter()
        .map(|f| (f.kind.as_str(), f.faces.as_slice()))
        .chain(
            patterns
                .iter()
                .map(|p| ("hole_pattern", p.faces.as_slice())),
        )
        .collect();
    face_names(records)
}

fn face_names(records: Vec<(&str, &[usize])>) -> Result<Vec<String>, String> {
    type Anchors<'a> = BTreeMap<(&'a str, usize), Vec<(usize, &'a [usize])>>;
    let mut groups: Anchors<'_> = BTreeMap::new();
    for (i, (kind, faces)) in records.iter().enumerate() {
        let face = faces
            .iter()
            .min()
            .ok_or_else(|| format!("{kind} has no faces to anchor its ID"))?;
        groups.entry((kind, *face)).or_default().push((i, faces));
    }
    let mut names = vec![String::new(); records.len()];
    for ((kind, face), mut group) in groups {
        group.sort_by(|a, b| a.1.cmp(b.1));
        let shared = group.len() > 1;
        for (n, (i, _)) in group.into_iter().enumerate() {
            names[i] = format!(
                "f:{kind}:f{face}{}",
                if shared {
                    format!("_{n}")
                } else {
                    String::new()
                }
            );
        }
    }
    Ok(names)
}

/// A scheme's geometric station identity, independent of its current reference spelling.
pub(crate) fn station_key(a: &Analysis, reference: &str) -> String {
    let (role, id) = ["start:", "end:", "axis:"]
        .into_iter()
        .find_map(|role| reference.strip_prefix(role).map(|id| (role, id)))
        .unwrap_or(("", reference));
    let index = index(a);
    format!(
        "{role}{}",
        index.stations.get(id).map_or(id, String::as_str)
    )
}

#[cfg(test)]
mod tests {
    #[test]
    fn index_is_shared_within_a_scope_and_rebuilt_after_mutation() {
        let mut a: crate::model::Analysis = serde_json::from_str(include_str!(
            "../tests/fixtures/identity/m10020.analysis7.json"
        ))
        .unwrap();
        let prepared = super::with(&a, || {
            let first = super::index(&a);
            super::with(&a, || {
                assert!(std::rc::Rc::ptr_eq(&first, &super::index(&a)))
            });
            first
        });
        a.levels.push(12345.);
        super::with(&a, || {
            let next = super::index(&a);
            assert!(!std::rc::Rc::ptr_eq(&prepared, &next));
            assert!(next.zs.contains(&12345.));
            assert!(!prepared.zs.contains(&12345.));
        });
    }
    #[test]
    fn coordinate_keys_preserve_total_float_order() {
        let mut values = [f64::NAN, -0., f64::NEG_INFINITY, 4., 0., -4., f64::INFINITY];
        let mut expected = values;
        values.sort_by_key(|&v| super::coordinate_order(v));
        expected.sort_by(f64::total_cmp);
        assert_eq!(values.map(f64::to_bits), expected.map(f64::to_bits));
    }
}
