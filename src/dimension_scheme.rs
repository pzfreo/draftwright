//! Authored scheme intent; independent model-space station evidence lives on Analysis.
use crate::model::{Analysis, Invalid, Spec};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Scheme {
    pub scheme: String,
    pub axis: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub targets: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub view: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub from: Option<String>,
}

/// A scheme station is identified by its stable reference, never its measured position.
pub(crate) fn item_id(name: &str, station: &str) -> String {
    use sha2::{Digest, Sha256};
    let hash = Sha256::digest(station.as_bytes());
    let token: String = hash[..8].iter().map(|b| format!("{b:02x}")).collect();
    format!("d:scheme:{name}/{token}")
}

pub(crate) fn invalid(message: impl Into<String>) -> Invalid {
    Invalid {
        path: None,
        also: Vec::new(),
        message: message.into(),
        choices: vec![],
    }
}

pub(crate) fn check(s: &Spec) -> Result<(), Invalid> {
    for (name, d) in &s.dimension_schemes {
        let fail = |why| {
            invalid(format!(
                "dimension_schemes/{name}: {why}; correct this entry or remove it."
            ))
        };
        if name.is_empty()
            || !name
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || b"_-".contains(&c))
        {
            return Err(fail("use a stable name containing letters, digits, _ or -"));
        }
        if !["chain", "baseline", "ordinate"].contains(&d.scheme.as_str()) {
            return Err(fail("scheme must be chain, baseline or ordinate"));
        }
        if !["x", "y", "z"].contains(&d.axis.as_str()) {
            return Err(fail("axis must be x, y or z in the part frame"));
        }
        if d.view
            .as_deref()
            .is_some_and(|v| !["front", "plan", "side"].contains(&v))
        {
            return Err(fail("view must be front, plan or side"));
        }
        if d.targets.is_empty() && d.view.is_none() {
            return Err(fail("name targets, or a view for its whole axis"));
        }
        if d.targets.iter().any(String::is_empty) || d.from.as_deref() == Some("") {
            return Err(fail(
                "references cannot be empty; use feature IDs or fingerprint keys from Analysis.edge_ink.references",
            ));
        }
    }
    Ok(())
}

/// Check revision-sensitive scheme references before recognition binding can refresh binding.
/// Fingerprint-only stations remain safe; feature ranks and whole-axis generated edits do not.
pub(crate) fn check_binding(a: &Analysis, s: &Spec) -> Result<(), Invalid> {
    if s.binding
        .as_ref()
        .is_none_or(|b| b.source_sha256 == a.binding.source_sha256)
    {
        return Ok(());
    }
    for (name, d) in &s.dimension_schemes {
        let prefix = format!("d:scheme:{name}/");
        let generated_edits = d.targets.is_empty()
            && s.dimensions
                .keys()
                .chain(s.precision.keys())
                .any(|id| id.starts_with(&prefix));
        if generated_edits || d.targets.iter().chain(&d.from).any(|r| r.contains("f:")) {
            return Err(invalid(format!(
                "dimension_schemes/{name}: Feature IDs belong to another STEP revision; replace them with current fingerprint references or explicitly rebind the Spec. Correct the reference or remove dimension_schemes/{name}."
            )));
        }
    }
    Ok(())
}
