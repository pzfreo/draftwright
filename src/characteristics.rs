//! File-owned inspection numbers, their final drawing carriers and inspection rings.
use crate::{
    items::State,
    model::{Analysis, Plan, Problem},
    pmi::{Field, Reading, Record},
};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Mode {
    #[default]
    Off,
    All,
}
impl Mode {
    pub fn is_off(&self) -> bool {
        *self == Self::Off
    }
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Settings {
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub occurrences: bool,
    /// By PMI source or plan item ID; source-specific decisions take precedence.
    #[serde(skip_serializing_if = "BTreeMap::is_empty")]
    pub items: BTreeMap<String, Override>,
}
impl Settings {
    pub fn is_default(&self) -> bool {
        *self == Self::default()
    }
}
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Override {
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub suppress: bool,
    /// Offset from the carrier's ink centre in paper mm (x right, y up).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub position: Option<[f64; 2]>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Characteristic {
    pub id: String,
    pub source: String,
    pub number: String,
    /// Original item, including an omitted item covered by another.
    pub item: Option<String>,
    pub drawn_item: Option<String>,
    pub sheet: Option<u32>,
    pub view: Option<String>,
    /// Expected ring IDs, including suppressed or unplaceable rings.
    pub balloon_ids: Vec<String>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Balloon {
    pub state: State,
    pub default: bool,
    pub basis: String,
    pub id: String,
    pub source: String,
    pub number: String,
    pub item: String,
    pub sheet: u32,
    pub view: Option<String>,
    pub at: [f64; 2],
    pub radius: f64,
    pub lines: Vec<[[f64; 2]; 2]>,
}
impl Balloon {
    pub fn bounds(&self) -> [f64; 4] {
        let [x, y] = self.at;
        let r = self.radius;
        [x - r, y - r, x + r, y + r]
    }
}
/// The number exactly as the STEP states it; no allocation or feature-level inheritance.
pub fn number(record: &Record) -> Option<&str> {
    if text(record, "id ambiguity").is_some() {
        return None;
    }
    text(record, "id")
        .filter(|n| numeric(n))
        .or_else(|| text(record, "characteristic number"))
}
fn text<'a>(record: &'a Record, key: &str) -> Option<&'a str> {
    record.fields.iter().find_map(|(k, v)| match v {
        Field::Text(n) if k == key => Some(n.as_str()),
        _ => None,
    })
}
/// Positive decimal integers only, without machine-integer truncation or respelling.
pub(crate) fn numeric(n: &str) -> bool {
    !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()) && n.bytes().any(|b| b != b'0')
}
fn diagnostic(record: &Record) -> Option<(&'static str, String)> {
    if let Some(why) = text(record, "id ambiguity") {
        let code = "characteristic_id_ambiguous";
        return Some((code, format!("{why}; no inspection number is used.")));
    }
    if let Some(id) = text(record, "id").filter(|n| numeric(n))
        && let Some(uda) = text(record, "characteristic number")
        && id.trim_start_matches('0') != uda.trim_start_matches('0')
    {
        let code = "characteristic_number_conflict";
        return Some((
            code,
            format!(
                "Requirement id {id} disagrees with characteristic number UDA {uda}; the id is used."
            ),
        ));
    }
    None
}
/// Numbered characteristics, including undrawn requirements, with their resolved sheet/item.
pub fn list(plan: &Plan) -> &[Characteristic] {
    &plan.characteristics
}
pub(crate) fn enabled(plan: &Plan) -> bool {
    plan.spec.sheet.balloons == Mode::All
}
fn mentions(basis: &str, source: &str) -> bool {
    basis.match_indices(source).any(|(i, _)| {
        basis[i + source.len()..]
            .chars()
            .next()
            .is_none_or(|c| !c.is_ascii_digit())
    })
}
fn original(plan: &Plan, r: &Record, identity: Option<&str>) -> Option<String> {
    if let Some(g) = plan.gdt.iter().find(|g| g.source == r.source_id) {
        return Some(g.id.clone());
    }
    if let Some(r) = plan.requirements.iter().find(|g| g.source == r.source_id) {
        return Some(r.id.clone());
    }
    if let Some(n) = plan.notes.iter().find(|n| identity == Some(n.id.as_str())) {
        return Some(n.id.clone());
    }
    let matches = |d: &&crate::dims::PlannedDimension| {
        identity.is_some_and(|id| d.measurements.iter().any(|m| m.feature == id))
            || mentions(&d.basis, &r.source_id)
    };
    plan.dimensions
        .iter()
        .filter(matches)
        .min_by_key(|d| d.state != State::Shown)
        .map(|d| d.id.clone())
        .or_else(|| {
            plan.omitted
                .iter()
                .find(|o| mentions(&o.basis, &r.source_id))
                .map(|o| o.id.clone())
        })
}
fn measurements(plan: &Plan, id: &str) -> Vec<crate::dims::Measurement> {
    plan.dimensions
        .iter()
        .find(|d| d.id == id)
        .map(|d| d.measurements.clone())
        .unwrap_or_default()
}
fn carrier(plan: &Plan, id: &str) -> Option<(String, u32, Option<String>)> {
    let inventory = crate::items::items(plan);
    let mut id = id.to_string();
    let mut seen = BTreeSet::new();
    while seen.insert(id.clone()) {
        let i = inventory.iter().find(|i| i.id == id)?;
        match &i.state {
            State::Shown => return Some((i.id.clone(), i.sheet, i.view.clone())),
            State::Covered { by } => id = by.clone(),
            _ => return None,
        }
    }
    None
}
pub(crate) fn finding(plan: &mut Plan, c: &Characteristic, code: &str, why: &str) {
    plan.problems.push(Problem {
        id: format!("p:{code}:{}", c.source),
        code: code.into(),
        severity: "warning".into(),
        message: format!("Inspection characteristic {} {why}.", c.number),
        subjects: vec![c.item.clone().unwrap_or_else(|| "sheet".into())],
        refs: vec![c.source.clone()],
        ..Default::default()
    });
}
/// Runs only at the finished planning boundaries. Empty readings take a constant-time exit.
pub(crate) fn apply(analysis: &Analysis, plan: &mut Plan) {
    if plan.characteristics.is_empty()
        && plan.inspection_balloons.is_empty()
        && !matches!(&analysis.pmi,Some(Reading::Read(r)) if r.records.iter().any(|r|number(r).is_some() || diagnostic(r).is_some()))
    {
        return;
    }
    plan.characteristics.clear();
    plan.inspection_balloons.clear();
    plan.problems.retain(|p| {
        !matches!(
            p.code.as_str(),
            "characteristic_not_drawn"
                | "characteristic_balloon_missing"
                | "characteristic_number_conflict"
                | "characteristic_id_ambiguous"
        )
    });
    let Some(Reading::Read(report)) = &analysis.pmi else {
        return;
    };
    if !report
        .records
        .iter()
        .any(|r| number(r).is_some() || diagnostic(r).is_some())
    {
        return;
    }
    let identities = report.identities();
    for r in &report.records {
        let item = original(plan, r, identities.id(&r.source_id).ok());
        if let Some((code, message)) = diagnostic(r) {
            plan.problems.push(Problem {
                id: format!("p:{code}:{}", r.source_id),
                code: code.into(),
                severity: "warning".into(),
                message,
                subjects: vec![item.clone().unwrap_or_else(|| r.source_id.clone())],
                refs: vec![r.source_id.clone()],
                ..Default::default()
            });
        }
        let Some(n) = number(r) else { continue };
        let carried = item.as_deref().and_then(|id| carrier(plan, id));
        let mut c = Characteristic {
            id: format!("characteristic:{}", r.source_id),
            source: r.source_id.clone(),
            number: n.into(),
            item,
            drawn_item: carried.as_ref().map(|i| i.0.clone()),
            sheet: carried.as_ref().map(|i| i.1),
            view: carried.and_then(|i| i.2),
            balloon_ids: Vec::new(),
        };
        // A table states the characteristic in its matching row, not in its TAG ring.
        if let Some(id) = &c.drawn_item {
            let ms = c
                .item
                .as_deref()
                .map(|i| measurements(plan, i))
                .unwrap_or_default();
            if let Some((table, row)) = plan
                .hole_table
                .iter()
                .chain(&plan.hole_tables)
                .filter(|t| {
                    &t.id == id
                        || ms.iter().any(|m| {
                            m.parameter == "bore.diameter"
                                && t.rows.iter().any(|row| row.feature == m.feature)
                        })
                })
                .find_map(|t| {
                    t.rows
                        .iter()
                        .find(|row| {
                            ms.iter()
                                .any(|m| m.parameter == "bore.diameter" && m.feature == row.feature)
                                || row.measurements.iter().any(|m| {
                                    ms.contains(m)
                                        || identities
                                            .id(&r.source_id)
                                            .is_ok_and(|id| m.feature == id)
                                })
                        })
                        .map(|row| (t, row))
                })
            {
                c.drawn_item = Some(format!("{}/{}", table.id, row.key()));
                c.sheet = Some(table.sheet);
                c.view = table.view.clone();
            }
        }
        plan.characteristics.push(c);
    }
    if enabled(plan) {
        // Give each carrier its first ring before growing groups of numbers on
        // compound callouts: a group must not close a neighbouring label's exit.
        // The source inventory retains its file order.
        let mut characteristics = std::mem::take(&mut plan.characteristics);
        let mut order: Vec<_> = (0..characteristics.len()).collect();
        order.sort_by_key(|&i| {
            characteristics[..i]
                .iter()
                .filter(|c| c.drawn_item == characteristics[i].drawn_item)
                .count()
        });
        for i in order {
            let c = &mut characteristics[i];
            if c.drawn_item.is_none() {
                finding(plan, c, "characteristic_not_drawn", "has no drawn item");
            } else if let Some(r) = report.records.iter().find(|r| r.source_id == c.source) {
                crate::dims::inspection::place(analysis, plan, r, c);
            }
        }
        plan.characteristics = characteristics;
    }
    plan.status = crate::completeness::status(&plan.problems);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{self, Spec};
    use crate::pmi::Category;
    fn numbered() -> (haecceity::Part, Analysis, String) {
        let bytes = include_bytes!("../tests/fixtures/parts/editor_pmi.step");
        let (part, mut a) = model::analyse_native_bytes("numbered.step", bytes).unwrap();
        let Some(Reading::Read(r)) = &mut a.pmi else {
            panic!("PMI")
        };
        let r = r
            .records
            .iter_mut()
            .find(|r| {
                r.category == Category::Dimension
                    && r.geometry.as_ref().is_some_and(|g| g.cylinders.len() > 1)
            })
            .unwrap();
        r.fields
            .push(("characteristic number".into(), Field::Text("407".into())));
        let source = r.source_id.clone();
        (part, a, source)
    }
    #[test]
    fn a_transferred_hole_row_gets_its_ring_only_on_its_own_sheet() {
        let (part, a, source) = numbered();
        let mut s = Spec::default();
        s.sheet.balloons = Mode::All;
        s.hole_table = crate::hole_table::Setting::Always;
        let mut p = model::plan_recovered(&a, &s).unwrap();
        assert!(p.characteristics.iter().any(|c| {
            c.source == source
                && c.drawn_item
                    .as_ref()
                    .is_some_and(|id| id.starts_with("hole_schedule_"))
        }));
        let rows: BTreeSet<_> = p
            .inspection_balloons
            .iter()
            .map(|b| b.item.clone())
            .collect();
        assert_eq!(
            rows.len(),
            6,
            "the numbered six-hole requirement has one ring per row"
        );
        // The allocator's table transfer changes the ownership before the final ring pass.
        for t in p.hole_table.iter_mut().chain(&mut p.hole_tables) {
            t.sheet = 2;
            for block in &mut t.blocks {
                block.sheet = 2;
            }
        }
        let mut second = p.sheet.clone();
        second.number = 2;
        p.sheets.push(second);
        crate::title_block::number_sheets(&mut p);
        apply(&a, &mut p);
        assert_eq!(p.inspection_balloons.len(), 6, "{:?}", p.problems);
        assert_eq!(
            p.inspection_balloons
                .iter()
                .map(|b| b.item.clone())
                .collect::<BTreeSet<_>>(),
            rows
        );
        assert!(
            p.inspection_balloons
                .iter()
                .all(|b| b.sheet == 2 && b.number == "407" && b.source == source)
        );
        let pages = model::render_sheets(&part, &a, &p, crate::dims::font()).unwrap();
        for balloon in &p.inspection_balloons {
            assert!(!pages[0].sheet.items.iter().any(|i| i.name == balloon.id));
            assert!(pages[1].sheet.items.iter().any(|i| i.name == balloon.id));
        }
    }
    #[test]
    fn covered_numbered_dimensions_follow_their_note_line() {
        let (_, a, source) = numbered();
        let base = model::plan_recovered(&a, &Spec::default()).unwrap();
        let c = base
            .characteristics
            .iter()
            .find(|c| c.source == source)
            .unwrap();
        let original = c.item.clone().unwrap();
        let label = base
            .dimensions
            .iter()
            .find(|d| d.id == original)
            .unwrap()
            .label
            .clone();
        let mut s = Spec::default();
        s.sheet.balloons = Mode::All;
        s.notes.push(crate::notes::NoteSpec {
            id: "n:1".into(),
            text: label,
        });
        let mut p = model::plan_recovered(&a, &s).unwrap();
        let d = p.dimensions.iter_mut().find(|d| d.id == original).unwrap();
        d.state = State::Covered { by: "n:1".into() };
        d.placed = None;
        apply(&a, &mut p);
        let c = p
            .characteristics
            .iter()
            .find(|c| c.source == source)
            .unwrap();
        assert_eq!(c.drawn_item.as_deref(), Some("n:1"));
        assert_eq!(c.number, "407");
        assert_eq!(p.inspection_balloons.len(), 1, "{:?}", p.problems);
        assert!(
            crate::items::items(&p)
                .iter()
                .any(|i| i.id == "n:1" && i.characteristic_numbers == ["407"])
        );
    }
}
