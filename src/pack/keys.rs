use std::collections::{BTreeMap, BTreeSet};

/// Leaf keys in assembly order. Names take precedence over meaningful product IDs;
/// repeated/blank names fall back to occurrence numbers, never STEP entity numbers.
pub fn part_keys(parts: &[crate::model::FilePart]) -> Vec<String> {
    keys(
        parts
            .iter()
            .map(|p| (p.name.as_str(), p.product_id.as_str())),
    )
}

pub(super) fn keys<'a>(parts: impl Iterator<Item = (&'a str, &'a str)>) -> Vec<String> {
    let parts: Vec<_> = parts.map(|(n, p)| (n.trim(), p.trim())).collect();
    let mut names = BTreeMap::new();
    let mut ids = BTreeMap::new();
    for &(name, id) in &parts {
        *names.entry(name).or_insert(0) += 1;
        *ids.entry(id).or_insert(0) += 1;
    }
    let unique_names: BTreeSet<_> = names
        .iter()
        .filter(|(name, count)| !name.is_empty() && **count == 1)
        .map(|(&name, _)| name)
        .collect();
    let preferred: Vec<_> = parts
        .iter()
        .map(|&(name, id)| {
            if !name.is_empty() && names[name] == 1 {
                Some(name)
            } else if meaningful(id) && ids[id] == 1 && !unique_names.contains(id) {
                Some(id)
            } else {
                None
            }
        })
        .collect();
    // Unique names reserve their keys first, including literal names such as
    // ASSEMBLY#1. IDs may never displace a unique name.
    let mut used: BTreeSet<_> = preferred
        .iter()
        .flatten()
        .map(|key| key.to_string())
        .collect();
    let mut occurrences = BTreeMap::new();
    parts
        .iter()
        .zip(preferred)
        .map(|(&(name, _), preferred)| {
            let stem = if name.is_empty() { "unnamed" } else { name };
            let occurrence = occurrences.entry(stem).or_insert(0);
            *occurrence += 1;
            if let Some(key) = preferred {
                return key.to_string();
            }
            let mut key = format!("{stem}#{occurrence}");
            while !used.insert(key.clone()) {
                key.push('#');
            }
            key
        })
        .collect()
}

fn meaningful(id: &str) -> bool {
    !id.is_empty()
        && ![
            "SOLID", "COMPOUND", "ASSEMBLY", "PART", "PRODUCT", "UNNAMED",
        ]
        .contains(&id.to_ascii_uppercase().as_str())
}

/// Only strip a trailing numeric index with a separator, never a drawing number.
pub(super) fn stem(name: &str) -> Option<&str> {
    let stem = name.trim_end_matches(|c: char| c.is_ascii_digit());
    if stem.len() == name.len() {
        return None;
    }
    let stem = if let Some(stem) = stem.strip_suffix(" #") {
        stem
    } else if stem.ends_with(['_', '-', ' ']) {
        stem
    } else {
        return None;
    };
    let stem = stem.trim_end_matches(['_', '-', ' ']);
    (!stem.is_empty()).then_some(stem)
}

#[cfg(test)]
mod tests {
    use super::stem;

    #[test]
    fn instance_suffixes_are_display_stems() {
        for name in [
            "M3×8 hex bolt #2",
            "M3×8 hex bolt_2",
            "M3×8 hex bolt-2",
            "M3×8 hex bolt 2",
        ] {
            assert_eq!(stem(name), Some("M3×8 hex bolt"));
        }
        assert_eq!(stem("M3×8 hex bolt #123"), Some("M3×8 hex bolt"));
    }

    #[test]
    fn other_suffixes_and_empty_stems_are_preserved() {
        for name in [
            "ABC123", "bolt#2", "bolt #", "bolt #2a", " #2", "_2", "-2", " 2", "123", "",
        ] {
            assert_eq!(stem(name), None, "{name:?}");
        }
    }
}

pub(super) fn common_stem<'a>(names: impl Iterator<Item = &'a str>) -> Option<String> {
    let names: Vec<_> = names.collect();
    let first = stem(names.first()?)?;
    (names.len() > 1
        && names.iter().all(|name| stem(name) == Some(first))
        && names.iter().any(|name| *name != names[0]))
    .then(|| first.to_string())
}
