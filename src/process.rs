//! Per-product manufacturing defaults. No geometry-based process inference.
use crate::{
    bom::Metadata,
    model::{Analysis, Spec},
    pack::ValueSource,
    pmi_lowering::SheetRequirement,
};
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub enum Process {
    #[default]
    #[serde(rename = "machined")]
    Machined,
    #[serde(rename = "3D printed: FDM")]
    Fdm,
    #[serde(rename = "3D printed: resin")]
    Resin,
    #[serde(rename = "3D printed: powder")]
    Powder,
    #[serde(rename = "3D printed: metal")]
    Metal,
    #[serde(rename = "sheet metal")]
    SheetMetal,
    #[serde(rename = "moulded")]
    Moulded,
    #[serde(rename = "cast")]
    Cast,
}
impl Process {
    pub fn printed(self) -> bool {
        matches!(self, Self::Fdm | Self::Resin | Self::Powder | Self::Metal)
    }
    pub fn label(self) -> String {
        serde_json::to_value(self).unwrap().as_str().unwrap().into()
    }
    fn tolerance(self) -> Option<&'static str> {
        match self {
            Self::Fdm | Self::Powder => Some("±0.3 mm or ±0.3 %, the greater"),
            Self::Resin => Some("±0.15 mm or ±0.15 %, the greater"),
            Self::Metal => Some("±0.1 mm or ±0.2 %, the greater"),
            Self::Moulded => Some("ISO 20457"),
            Self::Cast => Some("ISO 8062-3"),
            _ => None,
        }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Resolved {
    pub process: Process,
    pub source: ValueSource,
    pub basis: String,
}
pub fn resolve(metadata: Option<&Metadata>, spec: &Spec) -> Resolved {
    let (process, source) = if let Some(p) = spec.process {
        (p, ValueSource::Spec)
    } else if let Some(m) = metadata
        && let Some(p) = m.process
    {
        (
            p,
            if m.suggested_fields.contains("process") {
                ValueSource::Suggested
            } else {
                ValueSource::File
            },
        )
    } else {
        let p = metadata.map_or(Process::Machined, |m| m.manufacturing_process());
        (p, ValueSource::Guess)
    };
    Resolved { process, source, basis: "Spec process > PRODUCT_DEFINITION specify process > material class guess (check); override: process; tolerance default: sheet/title/tolerance; finish default (printed: omitted): sheet/default_finish".into() }
}

pub(crate) fn defaults(a: &Analysis, spec: &Spec, requirements: &mut Vec<SheetRequirement>) {
    let resolved = resolve(a.bom.as_ref(), spec);
    let p = resolved.process;
    if let Some(value) = p.tolerance()
        && !spec.sheet.title.contains_key("tolerance")
        && !requirements.iter().any(|r| r.kind == "general_tolerance")
    {
        let value = if spec.units.is_mm() {
            value.to_string()
        } else {
            let (length, rest) = value
                .split_once(" mm")
                .expect("process tolerance has a length");
            let mm: f64 = length
                .trim_start_matches('±')
                .parse()
                .expect("process tolerance");
            format!("±{} in{rest}", spec.units.magnitude(mm))
        };
        requirements.push(SheetRequirement {
            id: "process:general_tolerance".into(),
            kind: "general_tolerance",
            source: "process".into(),
            value: if p.printed() {
                format!(
                    "{} CHECK",
                    value.replace(", the greater", "\nwhichever is greater")
                )
            } else if matches!(resolved.source, ValueSource::Guess | ValueSource::Suggested) {
                format!("{value} CHECK")
            } else {
                value.clone()
            },
            basis: format!(
                "{}: {value}; default to check; override: sheet/title/tolerance",
                p.label()
            ),
            removal: String::new(),
        });
    }
    // Authored PMI (or an explicit Spec finish) wins; process defaults only fill gaps.
    if matches!(p, Process::Moulded | Process::Cast)
        && spec.sheet.default_finish.is_none()
        && !requirements
            .iter()
            .any(|r| r.kind == "default_surface_finish")
    {
        requirements.push(SheetRequirement {
            id: "process:default_surface_finish".into(),
            kind: "default_surface_finish",
            source: "process".into(),
            value: if p == Process::Moulded {
                "as moulded"
            } else {
                "as cast"
            }
            .into(),
            basis: "process default; override: sheet/default_finish".into(),
            removal: "no material removal".into(),
        });
    }
}

/// Authored process answers become independent Analysis facts before lint, like PMI.
pub(crate) fn effective<'a>(
    mut analysis: std::borrow::Cow<'a, Analysis>,
    intent: &Spec,
) -> std::borrow::Cow<'a, Analysis> {
    if let Some(process) = intent.process {
        let metadata = analysis.to_mut().bom.get_or_insert_default();
        metadata.process = Some(process);
        metadata.suggested_fields.remove("process");
    }
    analysis
}
