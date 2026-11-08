//! Plan items as data (the editor thin slice's stream S2): each item's typed [`State`], the
//! edit that fixes a problem ([`Op`]), and one label formatter ([`items`], [`label`]), so a
//! caller (the editor, an MCP client) never reads an item's state from its `basis` sentence or
//! formats its text again. The `basis` stays, for a person (ADR R1 invariant 4).
//!
//! Every plan item carries its state: on the sheet ([`State::Shown`]); left out by the spec
//! ([`State::OmittedByUser`], [`State::NotAuthored`]); stated by another item
//! ([`State::Covered`]); or kept in the plan undrawn, with the code of the problem that says why
//! ([`State::NotDrawable`]: R1 invariant 3). Each is set where the plan decides it.

use serde::{Deserialize, Serialize};

use crate::model::Plan;

/// What became of a plan item.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum State {
    /// Drawn on the sheet.
    #[default]
    Shown,
    /// Left out by the spec (`dimensions/<id>/omit`), or with a member of its correlated set
    /// that the spec omits (ARCH0015-2: the problem `correlated_set_whole` names it).
    OmittedByUser,
    /// No drawing requirement authored, or left out of an authored set
    /// (`dimension_source: authored`) by not being listed.
    NotAuthored,
    /// Not drawn because another item states what it would (one measurement, one owner): `by`
    /// is that item's ID.
    Covered { by: String },
    /// Kept in the plan but not drawn; `reason_code` is the code of the problem on its ID that
    /// says why (`view_plan_incomplete`, `callout_dropped`, `pmi_not_drawn`, …).
    NotDrawable { reason_code: String },
}

impl State {
    /// [`State::NotDrawable`] for the problem *code*.
    pub fn not_drawable(code: &str) -> State {
        State::NotDrawable {
            reason_code: code.into(),
        }
    }

    /// An item drawn when *placed*, else not drawable for the problem *code*.
    pub fn placed_or(placed: bool, code: &str) -> State {
        if placed {
            State::Shown
        } else {
            State::not_drawable(code)
        }
    }
}

/// One edit operation, as [`crate::model::edit`] takes it: a problem's `fix` is a list of them,
/// applied together.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Op {
    /// `set`, `remove` or `add`.
    pub op: String,
    /// The path into the spec, a `/` inside a key written `~1` ([`key`]).
    pub path: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub value: Option<serde_json::Value>,
}

impl Op {
    /// Append an authored declaration, note or table through the common Spec editor.
    pub fn add(path: impl Into<String>, value: serde_json::Value) -> Op {
        Op {
            op: "add".into(),
            path: path.into(),
            value: Some(value),
        }
    }

    pub fn remove(path: impl Into<String>) -> Op {
        Op {
            op: "remove".into(),
            path: path.into(),
            value: None,
        }
    }

    pub fn set(path: impl Into<String>, value: serde_json::Value) -> Op {
        Op {
            op: "set".into(),
            path: path.into(),
            value: Some(value),
        }
    }
}

/// *id* as one key of an edit path: `~` written `~0` and `/` written `~1` (RFC 6901).
pub fn key(id: &str) -> String {
    id.replace('~', "~0").replace('/', "~1")
}

/// A plan item, as a caller lists it.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(from = "ItemWire", into = "ItemWire")]
pub struct Item {
    pub sheet: u32,
    pub(crate) explicit_sheet: bool,
    pub id: String,
    /// `sheet`, `view`, `dimension`, `control_frame`, `datum_feature`, `label`, `requirement`,
    /// `note`, `caption`, `table`, `hole_table`, `hole_table_row`, `balloon` or
    /// `characteristic_balloon`.
    pub kind: String,
    /// The view that carries it, where it has one.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub view: Option<String>,
    pub state: State,
    /// The text draftwright draws for it, where it draws one line of text: a dimension's or
    /// callout's label (an omitted one's as it would print), a frame's compartments joined by
    /// ` | `, a datum's letter, a PMI label's text, a requirement's text, a note's rows (joined by
    /// a line break), a balloon's tag.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub label: Option<String>,
    /// All file-owned characteristics stated by this item, in source order.
    pub characteristic_numbers: Vec<String>,
}

// Ownership is explicit on every multi-sheet item, including sheet 1. The optional wire
// field preserves the old single-sheet bytes and remembers that distinction on replay.
#[derive(Serialize, Deserialize)]
struct ItemWire {
    #[serde(
        default,
        deserialize_with = "read_sheet",
        skip_serializing_if = "Option::is_none"
    )]
    sheet: Option<u32>,
    id: String,
    kind: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    view: Option<String>,
    state: State,
    #[serde(skip_serializing_if = "Option::is_none")]
    label: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    characteristic_numbers: Vec<String>,
}
fn read_sheet<'de, D: serde::Deserializer<'de>>(d: D) -> Result<Option<u32>, D::Error> {
    u32::deserialize(d).map(Some)
}
impl From<Item> for ItemWire {
    fn from(i: Item) -> Self {
        Self {
            sheet: (i.explicit_sheet || i.sheet != 1).then_some(i.sheet),
            id: i.id,
            kind: i.kind,
            view: i.view,
            state: i.state,
            label: i.label,
            characteristic_numbers: i.characteristic_numbers,
        }
    }
}
impl From<ItemWire> for Item {
    fn from(i: ItemWire) -> Self {
        Self {
            sheet: i.sheet.unwrap_or(1),
            explicit_sheet: i.sheet.is_some(),
            id: i.id,
            kind: i.kind,
            view: i.view,
            state: i.state,
            label: i.label,
            characteristic_numbers: i.characteristic_numbers,
        }
    }
}

/// A feature control frame's compartments as one line.
pub fn frame_label(cells: &[String]) -> String {
    cells.join(" | ")
}

/// Every item of *plan*, once each by its ID, in the plan's order: the sheet (ID `sheet`), the
/// views, the dimensions (drawn, then omitted), the GD&T items, the drawing-wide requirements
/// (one carried by a general note is listed as that note), the notes, the tables, the hole table
/// and the balloons.
pub fn items(plan: &Plan) -> Vec<Item> {
    let item =
        |id: &str, kind: &str, view: Option<&str>, state: &State, label: Option<String>| Item {
            explicit_sheet: !plan.sheets.is_empty(),
            sheet: view
                .and_then(|id| plan.views.iter().find(|v| v.id == id))
                .map_or(1, |v| v.sheet),
            id: id.into(),
            kind: kind.into(),
            view: view.map(String::from),
            state: state.clone(),
            label,
            characteristic_numbers: plan
                .characteristics
                .iter()
                .filter(|c| {
                    c.item.as_deref() == Some(id)
                        || c.drawn_item.as_deref() == Some(id)
                        || plan
                            .inspection_balloons
                            .iter()
                            .any(|b| b.source == c.source && b.item == id)
                })
                .map(|c| c.number.clone())
                .collect(),
        };
    let mut out = vec![item("sheet", "sheet", None, &plan.sheet.state, None)];
    for sheet in &plan.sheets {
        let mut i = item(
            &format!("sheet:{}", sheet.number),
            "sheet",
            None,
            &sheet.state,
            None,
        );
        i.sheet = sheet.number;
        out.push(i);
    }
    for v in &plan.views {
        let mut i = item(&v.id, "view", None, &v.state, None);
        i.sheet = v.sheet;
        out.push(i);
    }
    for d in &plan.dimensions {
        let label = Some(d.label.clone());
        out.push(item(&d.id, "dimension", Some(&d.view), &d.state, label));
    }
    for o in &plan.omitted {
        let label = Some(o.label.clone());
        out.push(item(&o.id, "dimension", None, &o.state, label));
    }
    // A feature note has two physical carriers (block text and flag), but one
    // editable requirement ID. Its note inventory entry represents both.
    for g in plan
        .gdt
        .iter()
        .filter(|g| !plan.notes.iter().any(|n| n.id == g.id))
    {
        let label = Some(g.label.clone());
        out.push(item(&g.id, &g.kind, Some(&g.view), &g.state, label));
    }
    for r in plan.requirements.iter().filter(|r| r.carrier != "notes") {
        let label = Some(r.text.clone());
        out.push(item(&r.id, "requirement", None, &r.state, label));
    }
    for c in &plan.captions {
        let mut i = item(
            &c.id,
            "caption",
            Some(&c.view),
            &c.state,
            Some(c.text.clone()),
        );
        i.sheet = c.sheet;
        out.push(i);
    }
    for n in &plan.notes {
        let label = Some(n.rows.join("\n"));
        let view = plan
            .gdt
            .iter()
            .find(|g| g.id == n.id && g.placed.is_some())
            .map(|g| g.view.as_str());
        out.push(item(&n.id, "note", view, &n.state, label));
    }
    for t in &plan.tables {
        let mut i = item(&t.id, "table", None, &t.state, None);
        i.sheet = t.sheet;
        out.push(i);
    }
    if let Some(t) = &plan.radius_table {
        out.push(item(&t.table.id, "table", None, &t.table.state, None));
    }
    for t in plan.hole_table.iter().chain(&plan.hole_tables) {
        let mut i = item(
            &t.id,
            "hole_table",
            Some(t.view.as_deref().unwrap_or("v:plan")),
            &t.state,
            None,
        );
        i.sheet = t.sheet;
        out.push(i);
    }
    for b in &plan.balloons {
        let label = Some(b.tag.clone());
        out.push(item(&b.id, "balloon", Some(&b.view), &b.state, label));
    }
    for id in plan
        .characteristics
        .iter()
        .filter_map(|c| c.drawn_item.as_ref())
        .chain(plan.inspection_balloons.iter().map(|b| &b.item))
    {
        if !out.iter().any(|i| &i.id == id)
            && let Some((t, r)) = plan
                .hole_table
                .iter()
                .chain(&plan.hole_tables)
                .find_map(|t| {
                    t.rows
                        .iter()
                        .find(|r| *id == format!("{}/{}", t.id, r.key()))
                        .map(|r| (t, r))
                })
        {
            let mut i = item(
                id,
                "hole_table_row",
                t.view.as_deref(),
                &State::Shown,
                Some(r.cells.join(" | ")),
            );
            i.sheet = t.sheet;
            out.push(i);
        }
    }
    for b in &plan.inspection_balloons {
        let mut i = item(
            &b.id,
            "characteristic_balloon",
            b.view.as_deref(),
            &State::Shown,
            Some(b.number.clone()),
        );
        i.sheet = b.sheet;
        out.push(i);
    }
    for c in &plan.characteristics {
        for id in &c.balloon_ids {
            if out.iter().any(|i| &i.id == id) {
                continue;
            }
            let suppressed = plan
                .spec
                .balloons
                .items
                .get(&c.source)
                .or_else(|| {
                    c.item
                        .as_ref()
                        .and_then(|i| plan.spec.balloons.items.get(i))
                })
                .is_some_and(|s| s.suppress);
            let state = if suppressed {
                State::OmittedByUser
            } else {
                State::not_drawable("characteristic_balloon_missing")
            };
            let mut i = item(
                id,
                "characteristic_balloon",
                c.view.as_deref(),
                &state,
                Some(c.number.clone()),
            );
            i.sheet = c.sheet.unwrap_or(1);
            out.push(i);
        }
    }
    out
}

/// The text draftwright draws for the item *id* of *plan* ([`Item::label`]); none for an item
/// that draws no one line of text, or an ID the plan does not have.
pub fn label(plan: &Plan, id: &str) -> Option<String> {
    items(plan).into_iter().find(|i| i.id == id)?.label
}
