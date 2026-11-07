//! Explicit, geometry-referenced longitudinal sections. Unavailable planes warn; they
//! never prevent the remaining drawing from being made.
use std::collections::BTreeMap;
use std::ops::{Deref, DerefMut};

use serde::{Deserialize, Serialize, Serializer, ser::SerializeMap};

use crate::model::{Analysis, Invalid, Problem, Spec, ViewSpec};

#[derive(Clone, Debug, Default, PartialEq, Deserialize)]
pub struct Views {
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub sections: Vec<SectionSpec>,
    #[serde(flatten)]
    entries: BTreeMap<String, ViewSpec>,
}
// Preserve the ordinary map's fast serialization path in planning memo keys.
impl Serialize for Views {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        if self.sections.is_empty() {
            return self.entries.serialize(serializer);
        }
        let mut map = serializer.serialize_map(Some(self.entries.len() + 1))?;
        map.serialize_entry("sections", &self.sections)?;
        for (name, view) in &self.entries {
            map.serialize_entry(name, view)?;
        }
        map.end()
    }
}
impl Views {
    pub(crate) fn keep_sections_from(&mut self, source: &Self) {
        self.sections = source.sections.clone();
        for (name, view) in source.entries.iter().filter(|(name, view)| {
            // A moved view's omission is allocator bookkeeping, not an
            // authored section request in the whole-drawing reference.
            name.starts_with("section_") && !(view.omit && view.sheet.is_some_and(|n| n > 1))
        }) {
            let mut view = view.clone();
            view.place = None;
            view.sheet = None;
            self.entries.insert(name.clone(), view);
        }
    }
    pub(crate) fn keep_breaks_from(&mut self, source: &Self) {
        for (name, view) in source.entries.iter().filter(|(_, v)| v.broken.is_some()) {
            let mut view = view.clone();
            view.place = None;
            self.entries.insert(name.clone(), view);
        }
    }
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

impl SectionSpec {
    pub(crate) fn view_name(&self) -> Option<&'static str> {
        view_name(&self.id)
    }
}

pub(crate) fn view_name(label: &str) -> Option<&'static str> {
    let [letter] = label.as_bytes() else {
        return None;
    };
    NAMES.get(letter.checked_sub(b'A')? as usize).copied()
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

pub(crate) fn floor(a: &Analysis, s: &Spec) -> Option<[f64; 3]> {
    let r = s.views.sections.first()?;
    let f = a.features.iter().find(|f| f.id == r.through)?;
    let crate::lint::section_recesses::Grammar::Pocket(local) =
        crate::lint::section_recesses::grammar(f).ok()?
    else {
        return None;
    };
    let mut floor = local.origin;
    floor[local.axis] -= local.open_sign * local.depth / 2.;
    Some(crate::bridge::holes::to_part(
        &crate::bridge::holes::frame(a),
        floor,
    ))
}

/// A source-specific attachment check, independent of the placer's shortening
/// choices. Generic route checks inspect callouts/collisions, not witness ownership.
pub(crate) fn attachment_problems(a: &Analysis, plan: &crate::model::Plan) -> Vec<Problem> {
    let (Some(p), Some(floor), Some(r)) = (
        pocket(a, &plan.spec),
        floor(a, &plan.spec),
        plan.spec.views.sections.first(),
    ) else {
        return Vec::new();
    };
    let Some(v) = plan
        .views
        .iter()
        .find(|v| Some(v.id.trim_start_matches("v:")) == r.view_name() && v.cut.is_some())
    else {
        return Vec::new();
    };
    let camera = haecceity::hlr::View::new(v.direction, v.up).expect("section camera");
    let centre = crate::model::extent(a, &camera).1;
    let page = |p| {
        let q = camera.map(p);
        [
            v.at[0] + v.scale * (q[0] - centre[0]),
            v.at[1] + v.scale * (q[1] - centre[1]),
        ]
    };
    let segment = |z| {
        let mut lo = floor;
        lo[p.long_axis] = p.lo;
        lo[2] = z;
        let mut hi = lo;
        hi[p.long_axis] = p.hi;
        [page(lo), page(hi)]
    };
    let floor_line = segment(floor[2]);
    let mouth_line = segment(2. * p.centre[2] - floor[2]);
    let distance = |q: [f64; 2], [a, b]: [[f64; 2]; 2]| {
        let d = [b[0] - a[0], b[1] - a[1]];
        let t = (((q[0] - a[0]) * d[0] + (q[1] - a[1]) * d[1])
            / (d[0] * d[0] + d[1] * d[1]).max(1e-12))
        .clamp(0., 1.);
        (q[0] - a[0] - t * d[0]).hypot(q[1] - a[1] - t * d[1])
    };
    plan.dimensions
        .iter()
        .filter(|d| {
            d.view == v.id && d.measurements.iter().any(|m| {
                m.feature == r.through && m.parameter == "pocket_depth.length"
            })
        })
        .filter_map(|d| {
            let placed = d.placed.as_ref()?;
            let attached = if d.kind == "callout" {
                placed.arrows.first().is_some_and(|a| distance(a[0], floor_line) < 0.8)
            } else {
                placed.lines.len() >= 2 && [floor_line, mouth_line].into_iter().all(|s| {
                    placed.lines.iter().take(2).any(|l| distance(l[0], s) < 0.8)
                })
            };
            (!attached).then(|| Problem {
                id: format!("p:dimension_witness_detached:{}", d.id),
                code: "dimension_witness_detached".into(),
                severity: "error".into(),
                message: "The section depth is not attached to its pocket floor and mouth; the displayed value alone does not establish that measurement.".into(),
                subjects: vec![d.id.clone(), r.through.clone()],
                ..Default::default()
            })
        })
        .collect()
}

pub(crate) fn resolve(
    a: &Analysis,
    s: &Spec,
    problems: &mut Vec<Problem>,
) -> Result<Option<crate::demand::Section>, Invalid> {
    let mut seen = std::collections::BTreeSet::new();
    for (i, r) in s.views.sections.iter().enumerate() {
        if r.view_name().is_none() || !seen.insert(&r.id) {
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
