//! Explicit, geometry-referenced longitudinal sections. Unavailable planes warn; they
//! never prevent the remaining drawing from being made.
use std::collections::BTreeMap;
use std::ops::{Deref, DerefMut};

use serde::{Deserialize, Serialize};

use crate::model::{Analysis, Invalid, Problem, Spec, ViewSpec};

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct Views {
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub sections: Vec<SectionSpec>,
    #[serde(flatten)]
    entries: BTreeMap<String, ViewSpec>,
}
impl Views {
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty() && self.sections.is_empty()
    }
}
impl Deref for Views {
    type Target = BTreeMap<String, ViewSpec>;
    fn deref(&self) -> &Self::Target {
        &self.entries
    }
}
impl DerefMut for Views {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.entries
    }
}
impl From<BTreeMap<String, ViewSpec>> for Views {
    fn from(entries: BTreeMap<String, ViewSpec>) -> Self {
        Self {
            entries,
            sections: Vec::new(),
        }
    }
}
impl<'a> IntoIterator for &'a Views {
    type Item = (&'a String, &'a ViewSpec);
    type IntoIter = std::collections::btree_map::Iter<'a, String, ViewSpec>;
    fn into_iter(self) -> Self::IntoIter {
        self.entries.iter()
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SectionSpec {
    pub id: String,
    pub through: String,
}

pub const NAMES: [&str; 8] = [
    "section_aa",
    "section_bb",
    "section_cc",
    "section_dd",
    "section_ee",
    "section_ff",
    "section_gg",
    "section_hh",
];

/// The named pocket's length and depth must be visible in the front camera. Width
/// is stated as a WIDE callout, never as a false in-plane linear measurement.
pub(crate) fn pocket(a: &Analysis, s: &Spec) -> Option<crate::lint::pockets::Placed> {
    let request = s.views.sections.first()?;
    crate::lint::pockets::placed(a)
        .into_iter()
        .find(|(f, _)| f.id == request.through)?
        .1
        .ok()
        .filter(|p| p.depth_axis == 2 && p.long_axis == 0)
        .filter(|p| {
            if !p.mouth {
                return true;
            }
            let Some(f) = a.features.iter().find(|f| f.id == request.through) else {
                return false;
            };
            let Ok(crate::lint::section_recesses::Grammar::Pocket(local)) =
                crate::lint::section_recesses::grammar(f)
            else {
                return false;
            };
            local.mouth.is_some_and(|(_, _, on)| {
                let on = crate::bridge::holes::to_part(&crate::bridge::holes::frame(a), on);
                (p.centre[1] - on[1]).abs() < 1e-6
            })
        })
}

pub(crate) fn resolve(
    a: &Analysis,
    s: &Spec,
    problems: &mut Vec<Problem>,
) -> Result<Option<crate::demand::Section>, Invalid> {
    let mut seen = std::collections::BTreeSet::new();
    for (i, r) in s.views.sections.iter().enumerate() {
        if !r.id.bytes().all(|b| (b'A'..=b'H').contains(&b))
            || r.id.len() != 1
            || !seen.insert(&r.id)
        {
            return Err(
                crate::model::invalid("Section IDs must be distinct letters A–H.", &[])
                    .at("views/sections", &i.to_string()),
            );
        }
        if !a.features.iter().any(|f| f.id == r.through) {
            return Err(crate::model::invalid(
                format!("Section {} names missing feature {}.", r.id, r.through),
                &[],
            )
            .at("views/sections", &i.to_string()));
        }
    }
    let Some(r) = s.views.sections.first() else {
        return Ok(None);
    };
    let y = pocket(a, s).map(|p| p.centre[1]).or_else(|| {
        crate::bridge::holes::read(a)
            .into_iter()
            .filter_map(|(_, h)| h.ok())
            .find(|h| h.feature.id == r.through && h.principal.is_some_and(|(k, _)| k == 2))
            .map(|h| h.at[1])
    });
    for (i, request) in s.views.sections.iter().enumerate() {
        if i > 0 || y.is_none() {
            problems.push(Problem {
                id: format!("p:section_not_shown:sections/{i}"), code: "section_not_shown".into(), severity: "warning".into(),
                message: format!("Rejected views/sections/{i}: section {} through {} is retained but not drawn: {}.", request.id, request.through, if i > 0 { "only one explicit section can currently be packed" } else { "no front-facing cutting plane can be named from this feature; supported geometry is a z-axis hole or a z-depth pocket running along x" }),
                subjects: vec![request.through.clone()], ..Default::default()
            });
        }
    }
    Ok(y.map(|cut_y| crate::demand::Section {
        cut_y,
        holes: 1,
        internal_detail: false,
    }))
}
