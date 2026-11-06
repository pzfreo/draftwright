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
            Self::Key(key) => crate::pack::part_keys(parts)
                .iter()
                .position(|k| k == key)
                .map(|i| parts[i].part)
                .ok_or_else(|| invalid(format!("Spec part {key:?} is not a part key; use draftwright parts to list the persistent keys."))),
        }
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
        PartChoice::Key(key)
            if analysis.parts.is_empty() && analysis.product_name.as_ref() == Some(key) =>
        {
            Ok(0)
        }
        _ => choice.resolve(&analysis.parts),
    }
}
