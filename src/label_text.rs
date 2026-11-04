//! Author wording around authoritative generated label terms, before ink is measured.
use serde::{Deserialize, Serialize};

use crate::model::{Invalid, Spec};

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Text {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub prefix: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub suffix: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub template: Option<String>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Style {
    Reference,
    Basic,
}

fn bad(id: &str, why: &str) -> Invalid {
    Invalid {
        path: None,
        also: Vec::new(),
        message: format!(
            "Label text for {id}: {why}. Use prefix/suffix wording or a template such as {{value}} AFTER PLATING; remove the override to restore automatic text."
        ),
        choices: vec!["{value} AFTER PLATING".into()],
    }
}

impl Text {
    fn check(&self, id: &str) -> Result<(), Invalid> {
        if self.template.is_some() && (self.prefix.is_some() || self.suffix.is_some()) {
            return Err(bad(id, "template excludes prefix and suffix"));
        }
        for s in [&self.prefix, &self.suffix, &self.template]
            .into_iter()
            .flatten()
        {
            if s.len() > 240 || s.chars().any(char::is_control) {
                return Err(bad(
                    id,
                    "wording must be a single line of at most 240 UTF-8 bytes",
                ));
            }
        }
        let (before, after) = if let Some(template) = &self.template {
            let Some((before, after)) = template.split_once("{value}") else {
                return Err(bad(
                    id,
                    "template must contain exactly one {value} placeholder, preserving every measured term",
                ));
            };
            (before, after)
        } else {
            (
                self.prefix.as_deref().unwrap_or(""),
                self.suffix.as_deref().unwrap_or(""),
            )
        };
        // Extra numbers could masquerade as the measured nominal, depth, fit or tolerance.
        // The one conventional exception is a leading quantity, independently judged by lint.
        let prose = count_prefix(before).map_or(before, |(_, rest)| rest);
        if [prose, after].iter().any(|s| {
            s.chars()
                .any(|c| c.is_numeric() || "{}⌀øØ↧⌴⌵±°=<>".contains(c))
        }) {
            return Err(bad(
                id,
                "literal wording may not add values, tolerance symbols or placeholders; keep measurements inside {value}",
            ));
        }
        Ok(())
    }

    pub(crate) fn wording(&self, reference: bool) -> crate::output::LabelWording {
        let (before, after) = self
            .template
            .as_deref()
            .and_then(|s| s.split_once("{value}"))
            .unwrap_or((
                self.prefix.as_deref().unwrap_or(""),
                self.suffix.as_deref().unwrap_or(""),
            ));
        crate::output::LabelWording {
            prefix: before.trim().into(),
            suffix: after.trim().into(),
            reference,
            count: count_prefix(before).map(|(n, _)| n),
        }
    }

    fn body(&self, label: &str, reference: bool) -> Option<String> {
        self.wording(reference).body(label)
    }

    pub(crate) fn terms(&self, id: &str, terms: &[String]) -> Result<Vec<String>, Invalid> {
        self.check(id)?;
        let (before, after) = self
            .template
            .as_deref()
            .and_then(|s| s.split_once("{value}"))
            .unwrap_or((
                self.prefix.as_deref().unwrap_or(""),
                self.suffix.as_deref().unwrap_or(""),
            ));
        if count_prefix(before).is_some() && count_prefix(&terms.join(" ")).is_some() {
            return Err(bad(
                id,
                "the generated label already has a quantity; adding another would contradict its count",
            ));
        }
        let mut out = Vec::with_capacity(terms.len() + 2);
        if !before.trim().is_empty() {
            out.push(before.trim().into());
        }
        out.extend_from_slice(terms);
        if !after.trim().is_empty() {
            out.push(after.trim().into());
        }
        Ok(out)
    }
}

fn count_prefix(s: &str) -> Option<(usize, &str)> {
    let s = s.trim_start();
    let end = s.find(|c: char| !c.is_ascii_digit())?;
    let count = s[..end].parse::<usize>().ok().filter(|n| *n > 0)?;
    let rest = s[end..].trim_start().strip_prefix('×')?;
    Some((count, rest))
}

pub(crate) fn check(spec: &Spec) -> Result<(), Invalid> {
    for (id, text) in spec
        .dimensions
        .iter()
        .filter_map(|(id, d)| d.text.as_ref().map(|t| (id, t)))
        .chain(
            spec.annotations
                .iter()
                .filter_map(|(id, a)| a.text.as_ref().map(|t| (id, t))),
        )
    {
        text.check(id)?;
    }
    Ok(())
}

pub(crate) fn basic(d: &crate::dims::PlannedDimension) -> bool {
    d.from_spec.iter().any(|s| s == "style.basic") || crate::dims::is_basic(&d.measurements)
}

/// Keep annotation glyph semantics intact: only unframed prose labels accept wording.
pub(crate) fn annotations(
    items: &mut [crate::pmi_lowering::Lowered],
    spec: &Spec,
) -> Result<(), Invalid> {
    if spec.annotations.is_empty() {
        return Ok(());
    }
    for item in items {
        if let Some(text) = spec.annotations.get(&item.id).and_then(|a| a.text.as_ref()) {
            let (crate::pmi_lowering::Glyph::Label { text: original }
            | crate::pmi_lowering::Glyph::FlagNote { text: original, .. }) = &mut item.glyph
            else {
                return Err(bad(
                    &item.id,
                    "datums, control frames and finish symbols have structured meanings; edit their feature fields instead",
                ));
            };
            *original = text
                .terms(&item.id, std::slice::from_ref(original))?
                .join(" ");
            item.basis += "; from_spec: text; original callout retained";
        }
    }
    Ok(())
}

/// An authored label stays on its stable item when automatic schedules or notes are proposed.
pub(crate) fn overridden(d: &crate::dims::PlannedDimension) -> bool {
    d.from_spec
        .iter()
        .any(|s| s == "text" || s.starts_with("style."))
}

pub(crate) fn note_body<'a>(
    spec: &Spec,
    note: &'a crate::notes::PlannedNote,
) -> std::borrow::Cow<'a, str> {
    spec.annotations
        .get(&note.id)
        .and_then(|a| a.text.as_ref())
        .and_then(|text| text.body(&note.text, false))
        .map_or_else(
            || std::borrow::Cow::Borrowed(note.text.as_str()),
            std::borrow::Cow::Owned,
        )
}

/// Compile wording evidence independently of the live searchable runs.
pub(crate) fn capture(plan: &crate::model::Plan, sheet: &mut crate::output::Sheet) {
    if !plan
        .spec
        .dimensions
        .values()
        .any(|d| d.text.is_some() || d.style.is_some())
        && !plan.spec.annotations.values().any(|a| a.text.is_some())
    {
        return;
    }
    for (id, entry) in &plan.spec.dimensions {
        if let Some(text) = &entry.text
            && let Some(d) = plan.dimensions.iter().find(|d| &d.id == id)
            && d.from_spec.iter().any(|field| field == "text")
        {
            sheet
                .label_text
                .insert(id.clone(), text.wording(d.reference));
        }
    }
    for d in plan
        .dimensions
        .iter()
        .filter(|d| d.from_spec.iter().any(|field| field.starts_with("style.")))
    {
        sheet
            .label_text
            .entry(d.id.clone())
            .or_insert_with(|| Text::default().wording(d.reference));
    }
    for note in plan
        .notes
        .iter()
        .filter(|note| note.from_spec.iter().any(|field| field == "text"))
    {
        if let Some(text) = plan
            .spec
            .annotations
            .get(&note.id)
            .and_then(|a| a.text.as_ref())
        {
            let (before, after) = text
                .template
                .as_deref()
                .and_then(|t| t.split_once("{value}"))
                .unwrap_or((
                    text.prefix.as_deref().unwrap_or(""),
                    text.suffix.as_deref().unwrap_or(""),
                ));
            let numbered = note
                .rows
                .first()
                .is_some_and(|r| r.starts_with(&format!("{} ", note.number)));
            let prefix = if numbered {
                format!("{}  {}", note.number, before.trim())
                    .trim_end()
                    .to_string()
            } else {
                before.trim().into()
            };
            sheet.label_text.insert(
                note.id.clone(),
                Text {
                    prefix: Some(prefix),
                    suffix: Some(after.trim().into()),
                    template: None,
                }
                .wording(false),
            );
        }
    }
}
