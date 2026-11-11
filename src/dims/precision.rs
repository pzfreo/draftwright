//! Display policy by semantic measurement ID. Nominals and tolerances remain independent.
use super::{Want, want_id};
use crate::model::{Invalid, Spec};
use serde::de::{Error, MapAccess, Visitor};
use std::collections::{BTreeMap, BTreeSet};

pub(crate) fn read<'de, D: serde::Deserializer<'de>>(
    d: D,
) -> Result<BTreeMap<String, u8>, D::Error> {
    struct Policies;
    impl<'de> Visitor<'de> for Policies {
        type Value = BTreeMap<String, u8>;
        fn expecting(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            f.write_str("measurement IDs with integer precision from 0 to 15")
        }
        fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Self::Value, A::Error> {
            let mut out = BTreeMap::new();
            while let Some((id, places)) = map.next_entry::<String, u8>()? {
                if places > 15 {
                    return Err(A::Error::custom(
                        "precision must be an integer from 0 to 15",
                    ));
                }
                if out
                    .insert(id.clone(), places)
                    .is_some_and(|old| old != places)
                {
                    return Err(A::Error::custom(format!("conflicting precision for {id}")));
                }
            }
            Ok(out)
        }
    }
    d.deserialize_map(Policies)
}

/// Explicit precision retains trailing zeroes, including at zero; default formatting is intact.
pub fn format(value: f64, places: u8) -> String {
    let text = format!("{value:.p$}", p = usize::from(places));
    if text.starts_with('-') && text.parse::<f64>() == Ok(0.0) {
        text[1..].into()
    } else {
        text
    }
}

pub(super) fn apply(spec: &Spec, wants: &mut [Want]) -> Result<(), Invalid> {
    if spec.precision.is_empty() {
        return Ok(());
    }
    let bad = |message| Invalid {
        path: None,
        also: Vec::new(),
        message,
        choices: Vec::new(),
    };
    if spec.precision.values().any(|p| *p > 15) {
        return Err(bad("precision must be an integer from 0 to 15".into()));
    }
    labels(spec, wants)
}

pub(super) fn unapplied(spec: &Spec, wants: &[Want]) -> Vec<crate::model::Problem> {
    let known: BTreeSet<_> = wants
        .iter()
        .flat_map(|w| {
            std::iter::once(w.id.clone()).chain(
                w.measurements
                    .iter()
                    .map(|m| want_id(&m.feature, &m.parameter)),
            )
        })
        .collect();
    spec.precision
        .keys()
        .filter(|id| !known.contains(*id))
        .map(|id| crate::model::Problem {
            id: format!("p:dimension_edit_unapplied:precision/{id}"),
            code: "dimension_edit_unapplied".into(),
            severity: "warning".into(),
            subjects: vec![id.clone()],
            message: "A saved precision edit has no current measurement; it is retained but cannot be applied.".into(),
            ..Default::default()
        })
        .collect()
}

pub(super) fn labels<'a>(
    spec: &Spec,
    wants: impl IntoIterator<Item = &'a mut Want>,
) -> Result<(), Invalid> {
    if spec.precision.is_empty() {
        return Ok(());
    }
    let bad = |message| Invalid {
        path: None,
        also: Vec::new(),
        message,
        choices: Vec::new(),
    };
    if spec.precision.values().any(|p| *p > 15) {
        return Err(bad("precision must be an integer from 0 to 15".into()));
    }
    for w in wants {
        if w.id.starts_with("d:scheme:") {
            if let Some(&places) = spec.precision.get(&w.id)
                && let Some(term) = w.terms.first_mut()
                && let Some(nominal) = term.split_whitespace().next()
                && nominal.parse::<f64>().is_ok()
            {
                *term = format!(
                    "{}{}",
                    spec.units.format_places(w.value, Some(places)),
                    &term[nominal.len()..]
                );
            }
            continue;
        }
        // ISO thread designations replace the numeric diameter. Their lengths and table
        // references are different quantities, even when they happen to equal that diameter.
        if super::tolerance::thread_designated(w) {
            continue;
        }
        // A compound callout's scalar terms are in measurement order. Repeat that mapping
        // for each physical member; all members sharing one printed term must agree.
        let mut replacements: BTreeMap<(usize, usize, usize), String> = BTreeMap::new();
        let mut cursor: BTreeMap<&str, usize> = BTreeMap::new();
        let mut slots = Vec::new();
        for (i, term) in w.terms.iter().enumerate() {
            let mut start = None;
            for (j, c) in term
                .char_indices()
                .chain(std::iter::once((term.len(), ' ')))
            {
                if c.is_ascii_digit() || c == '.' {
                    start.get_or_insert(j);
                } else if let Some(a) = start.take() {
                    let prefix = &term[..a];
                    let suffix = &term[j..];
                    if !suffix.starts_with('×')
                        && !prefix.ends_with(['+', '-', 'M', 'P'])
                        && !prefix.contains('(')
                        && !prefix.contains("SEE MFG ")
                        && (a == 0 || prefix.ends_with(['⌀', 'ø', ' ', 'R']))
                        && let Ok(value) = term[a..j].parse::<f64>()
                    {
                        slots.push((i, a, j, value));
                    }
                }
            }
        }
        let mut seen = BTreeSet::new();
        for m in &w.measurements {
            let units = if m.parameter.contains("angle") {
                crate::units::Units::Mm
            } else {
                spec.units
            };
            if m.feature.starts_with("pmi:") && !w.id.starts_with("d:pmi:") {
                continue;
            }
            if !seen.insert((&m.feature, &m.parameter)) {
                continue;
            }
            let at = cursor.entry(&m.feature).or_default();
            let found = slots
                .iter()
                .enumerate()
                .skip(*at)
                .find(|(_, (_, _, _, v))| {
                    (*v - m.value).abs() < 1e-9
                        || units
                            .format(m.value)
                            .parse::<f64>()
                            .is_ok_and(|n| (n - v).abs() < 1e-9)
                });
            let Some((j, &(i, a, b, _))) = found else {
                continue;
            };
            *at = j + 1;
            let policy = spec
                .precision
                .get(&want_id(&m.feature, &m.parameter))
                .or_else(|| {
                    (m.parameter == w.id.rsplit_once('/').map_or("", |(_, p)| p)
                        || (w.id.starts_with("d:authored:") && m.feature.starts_with("geometry:")))
                    .then(|| spec.precision.get(&w.id))
                    .flatten()
                });
            if let Some(&p) = policy {
                let text = units.format_places(m.value, Some(p));
                if let Some(old) = replacements.insert((i, a, b), text.clone())
                    && old != text
                {
                    return Err(bad(format!(
                        "conflicting precision on the shared term of {}: {old} and {text}; its measurements require separate dimension owners",
                        w.id
                    )));
                }
            }
        }
        for ((i, a, b), text) in replacements.into_iter().rev() {
            w.terms[i].replace_range(a..b, &text);
        }
    }
    Ok(())
}

/// A scalar dimension's leading display policy, including its grouped measurement identity.
pub(super) fn leading(spec: &Spec, w: &Want) -> Option<u8> {
    spec.precision.get(&w.id).copied().or_else(|| {
        let role = w.id.rsplit_once('/')?.1;
        w.measurements
            .iter()
            .filter(|m| m.parameter == role)
            .find_map(|m| {
                spec.precision
                    .get(&want_id(&m.feature, &m.parameter))
                    .copied()
            })
    })
}

#[cfg(test)]
mod tests {
    #[test]
    fn symbolic_threads_and_manufacturing_references_are_not_nominal_terms() {
        use crate::dims::{Measurement, Shape, Want};
        use crate::model::Spec;
        for (terms, expected) in [
            (
                vec!["M10", "10 MIN FULL THREAD"],
                vec!["M10", "10 MIN FULL THREAD"],
            ),
            (vec!["SEE MFG 10"], vec!["SEE MFG 10"]),
            (vec!["10", "SEE MFG 10"], vec!["10.000", "SEE MFG 10"]),
            (vec!["⌀10", "SEE MFG 10"], vec!["⌀10.000", "SEE MFG 10"]),
        ] {
            let mut want = Want {
                units: Default::default(),
                id: "d:f:turned_step:1/step.diameter".into(),
                views: vec!["front"],
                value: 10.0,
                terms: terms.into_iter().map(String::from).collect(),
                measurements: vec![Measurement {
                    feature: "f:turned_step:1".into(),
                    parameter: "step.diameter".into(),
                    value: 10.0,
                }],
                basis: String::new(),
                shape: Shape::Callout {
                    arcs: Vec::new(),
                    sites: vec![],
                },
                turning: None,
            };
            let mut spec = Spec::default();
            spec.precision.insert(want.id.clone(), 3);
            super::apply(&spec, std::slice::from_mut(&mut want)).unwrap();
            assert_eq!(want.terms, expected);
            assert_eq!(want.measurements[0].value, 10.0);
        }
    }

    #[test]
    fn precision_boundaries_and_negative_zero_are_stable() {
        assert_eq!(super::format(1.25, 0), "1");
        assert_eq!(super::format(1.0, 15), "1.000000000000000");
        assert_eq!(super::format(-0.0001, 2), "0.00");
    }
}
