//! Spec selection uses the same identities as `parts`, before recognition or drawing.
use crate::model::{Analysis, FilePart, Invalid, Spec};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum PartChoice {
    Index(usize),
    Key(String),
}
impl PartChoice {
    pub(crate) fn resolve(&self, parts: &[FilePart]) -> Result<usize, Invalid> {
        match self {
            Self::Index(k) => Ok(*k),
            Self::Key(key) => {
                let keys = crate::pack::part_keys(parts);
                let matches: Vec<_> = keys.iter().enumerate().filter(|(_, k)| *k == key).collect();
                match matches.as_slice() {
                    [(i, _)] => Ok(parts[*i].part),
                    [] if parts.iter().filter(|p| p.name.trim() == key).count() > 1 => {
                        Err(invalid(format!(
                            "part key {key:?} is ambiguous; use draftwright parts <step> to list the distinct persistent keys."
                        )))
                    }
                    [] => Err(invalid(format!(
                        "part {key:?} is not a part key; use draftwright parts <step> to list the persistent keys."
                    ))),
                    _ => Err(invalid(format!(
                        "part key {key:?} is ambiguous; use draftwright parts <step> to list the persistent keys."
                    ))),
                }
            }
        }
    }
}
/// CLI text may denote an index or a persistent key. Numeric product keys must
/// not silently select a different indexed product.
pub(crate) fn cli(value: &str, parts: &[FilePart]) -> Result<usize, Invalid> {
    let key = PartChoice::Key(value.into());
    match value.parse::<usize>() {
        Ok(index) => {
            if crate::pack::part_keys(parts).iter().any(|k| k == value) {
                let keyed = key.resolve(parts)?;
                if keyed != index && parts.iter().any(|p| p.part == index) {
                    return Err(invalid(format!(
                        "--part {value:?} is ambiguous: index {index} and persistent key for part {keyed}; use draftwright parts <step> and choose a distinct key, or the Spec part field"
                    )));
                }
                return Ok(keyed);
            }
            Ok(index)
        }
        Err(_)
            if value.starts_with('-')
                && value[1..].parse::<usize>().is_ok()
                && !crate::pack::part_keys(parts).iter().any(|k| k == value) =>
        {
            Err(invalid(format!(
                "--part {value:?} is not a part's index; use draftwright parts <step> to list persistent keys and indices"
            )))
        }
        Err(_) => key.resolve(parts),
    }
}

fn invalid(message: String) -> Invalid {
    Invalid {
        message,
        path: Some("part".into()),
        choices: Vec::new(),
        also: Vec::new(),
    }
}

pub(crate) fn reconcile(
    request: Option<usize>,
    selected: Option<usize>,
) -> Result<Option<usize>, Invalid> {
    if let (Some(a), Some(b)) = (request, selected)
        && a != b
    {
        return Err(invalid(format!(
            "Spec part {b} conflicts with command/request part {a}; choose the same part in both places."
        )));
    }
    Ok(selected.or(request))
}

/// A saved Analysis cannot supply another part's geometry. Selecting its principal part
/// explicitly is safe; selecting another requires analysing the source STEP again.
pub(crate) fn check(analysis: &Analysis, spec: &Spec) -> Result<(), Invalid> {
    let Some(choice) = &spec.part else {
        return Ok(());
    };
    let selected = selected(analysis, choice)?;
    let drawn = analysis
        .binding
        .part_index
        .or_else(|| analysis.parts.is_empty().then_some(0));
    if drawn != Some(selected) {
        return Err(invalid(format!(
            "Spec part {selected} is not the part this Analysis draws ({drawn:?}); analyse that part from the STEP file, or pass the STEP instead of this Analysis."
        )));
    }
    Ok(())
}

pub(crate) fn selected(analysis: &Analysis, choice: &PartChoice) -> Result<usize, Invalid> {
    match choice {
        PartChoice::Key(key) if analysis.part_keys.contains_key(key) => Ok(analysis.part_keys[key]),
        PartChoice::Key(_) if analysis.parts.is_empty() && analysis.product_name.is_some() => {
            // Singleton maps are absent from the public document and its in-memory
            // counterpart. Reconstruct the same key from the existing product metadata.
            choice.resolve(&[FilePart {
                part: analysis.binding.part_index.unwrap_or(0),
                name: analysis.product_name.clone().unwrap_or_default(),
                product_id: analysis
                    .bom
                    .as_ref()
                    .and_then(|b| b.product_ids.first())
                    .cloned()
                    .unwrap_or_default(),
                faces: analysis.binding.face_count,
                instances: 1,
            }])
        }
        _ => choice.resolve(&analysis.parts),
    }
}
