//! Proportionate remedies. Local offers constrain paper, scale and sheet count.
use crate::items::Op;
use serde::{Deserialize, Serialize, ser::SerializeStruct};

pub use crate::remedy_scope::Scope;

/// First matching pattern wins; `*` matches one escaped key and `**` its descendants.
/// Keep sheet allocation before view intent. Only known item fields are local: new or
/// unknown paths conservatively re-plan the sheet until deliberately added to this table.
pub fn path_scope(path: &str) -> Scope {
    use Scope::*;
    const PATTERNS: &[(&str, Scope)] = &[
        ("balloons/items/**", Local),
        ("balloons/occurrences", Local),
        ("sheet/balloons", Local),
        ("views/*/sheet", Sheet),
        ("tables/*/sheet", Sheet),
        ("sheets/**", Sheet),
        ("sheet/size", Sheet),
        ("sheet/scale", Sheet),
        ("sheet/dimension_spacing", Sheet),
        ("hole_table", Sheet),
        ("dimension_source", Sheet),
        ("dimension_schemes/**", Sheet),
        ("pmi/**", Sheet),
        ("views/*", View),
        ("views/*/place/**", View),
        ("views/*/scale", View),
        ("views/*/omit", View),
        ("views/*/detail/**", View),
        ("dimensions/*", Local),
        ("dimensions/*/place/**", Local),
        ("dimensions/*/view", Local),
        ("dimensions/*/omit", Local),
        ("dimensions/*/text/**", Local),
        ("dimensions/*/style", Local),
        ("dimensions/*/tolerance/**", Local),
        ("dimensions/*/fit", Local),
        ("annotations/*", Local),
        ("annotations/*/place/**", Local),
        ("annotations/*/omit", Local),
        ("annotations/*/text/**", Local),
        ("features/*/fit", Local),
        ("sheet/title/*", Local),
        ("sheet/zones", Local),
    ];
    let parts: Vec<_> = path.trim_matches('/').split('/').collect();
    PATTERNS
        .iter()
        .find_map(|(pattern, scope)| {
            let pattern: Vec<_> = pattern.split('/').collect();
            let recursive = pattern.last() == Some(&"**");
            let count = pattern.len() - usize::from(recursive);
            ((recursive && parts.len() >= count || parts.len() == pattern.len())
                && pattern[..count]
                    .iter()
                    .zip(&parts)
                    .all(|(a, b)| *a == "*" || a == b))
            .then_some(*scope)
        })
        .unwrap_or(crate::remedy_scope::UNBOUND_SCOPE)
}

// An object-valued set addresses its nested Spec paths too. Keep the root's
// conservative scope: replacing a collection may remove entries absent in its value.
fn value_scope(path: &str, value: &serde_json::Value) -> Scope {
    let mut scope = path_scope(path);
    if scope == Scope::Sheet {
        return scope;
    }
    if let serde_json::Value::Object(fields) = value {
        for (name, value) in fields {
            scope = scope.max(value_scope(
                &format!("{path}/{}", crate::items::key(name)),
                value,
            ));
        }
    }
    scope
}

pub fn scope(ops: &[Op]) -> Scope {
    // Paper/count settings bundled with keep_layout constrain a local edit. Apply
    // compares them with the current drawing; alone they remain sheet scope.
    let retained = ops.iter().any(|op| {
        op.path == "sheet/keep_layout"
            && op.op == "set"
            && op.value == Some(serde_json::json!(true))
    });
    let edits: Vec<_> = ops
        .iter()
        .filter(|op| {
            !(retained
                && op.op == "set"
                && matches!(
                    op.path.as_str(),
                    "sheet/keep_layout"
                        | "sheet/size"
                        | "sheet/scale"
                        | "sheets/max"
                        | "sheet/dimension_spacing"
                ))
        })
        .collect();
    let ops = edits.as_slice();
    let broadest = ops
        .iter()
        .map(|op| {
            op.value.as_ref().map_or_else(
                || path_scope(&op.path),
                |value| value_scope(&op.path, value),
            )
        })
        .max()
        .unwrap_or(crate::remedy_scope::UNBOUND_SCOPE);
    if broadest == Scope::Sheet {
        return broadest;
    }
    // Local/view remedies act on one item/view. A bundle editing several owners is
    // sheet-wide even if each individual path would be a narrower edit.
    let owner = |op: &Op| {
        let count = if op.path.starts_with("sheet/title/") {
            3
        } else {
            2
        };
        op.path
            .trim_matches('/')
            .split('/')
            .take(count)
            .collect::<Vec<_>>()
            .join("/")
    };
    let first = owner(ops[0]);
    if ops.iter().any(|op| owner(op) != first) {
        Scope::Sheet
    } else {
        broadest
    }
}

/// One alternative: its operations apply together. Scope is always derived, including
/// when reading old or externally supplied JSON; a caller may widen but cannot understate it.
#[derive(Clone, Debug, Deserialize)]
pub struct Fix {
    pub description: String,
    pub ops: Vec<Op>,
    #[serde(default, rename = "scope")]
    pub minimum_scope: Option<Scope>,
}
impl PartialEq for Fix {
    fn eq(&self, other: &Self) -> bool {
        if self.ops != other.ops {
            return false;
        }
        // Equal operations and scope floors imply equal derived scopes. Most
        // state comparisons also have identical raw descriptions: avoid parsing
        // intent bundles and constructing their descriptions again in that case.
        // Wire descriptions may already include the sheet consequence, so retain
        // the full effective comparison for different raw descriptions or floors.
        if self.description == other.description
            && self.minimum_scope.unwrap_or(Scope::Local)
                == other.minimum_scope.unwrap_or(Scope::Local)
        {
            return true;
        }
        self.scope() == other.scope() && self.description() == other.description()
    }
}
impl Fix {
    pub fn new(description: impl Into<String>, ops: Vec<Op>) -> Self {
        Self {
            description: description.into(),
            ops,
            minimum_scope: None,
        }
    }
    pub fn scope(&self) -> Scope {
        scope(&self.ops).max(self.minimum_scope.unwrap_or(Scope::Local))
    }
    pub(crate) fn from_ops(ops: Vec<Op>) -> Self {
        let phrase = ops
            .iter()
            .map(op_description)
            .collect::<Vec<_>>()
            .join(" and ");
        let mut chars = phrase.chars();
        let description = chars
            .next()
            .map(|c| c.to_uppercase().chain(chars).collect::<String>())
            .unwrap_or_default();
        Self::new(format!("{description}."), ops)
    }
    pub fn description(&self) -> String {
        if self.scope() != Scope::Sheet || self.description.ends_with(SHEET_CONSEQUENCE) {
            return self.description.clone();
        }
        format!("{} {SHEET_CONSEQUENCE}", self.description)
    }
}
/// Sheet edits rebuild the canonical drawing, including the frame used by pins.
pub const SHEET_CONSEQUENCE: &str =
    "The whole sheet is laid out again, so placements you have moved by hand may change.";

/// Describe the actual values carried by an engine-generated remedy, without exposing
/// edit paths. More specific placement builders can supply the printed label as well.
fn op_description(op: &Op) -> String {
    let parts: Vec<_> = op.path.trim_matches('/').split('/').collect();
    let value = op.value.as_ref();
    let text = value.map(|v| {
        v.as_str()
            .map(String::from)
            .unwrap_or_else(|| v.to_string())
    });
    let choose = |what: &str| format!("let draftwright choose the {what}");
    let view = || crate::wording::view(parts.get(1).copied().unwrap_or("front"));
    let item = || match parts.first().copied() {
        Some("dimensions") => "the dimension",
        Some("annotations") => "the annotation",
        _ => "the feature",
    };
    match op.path.as_str() {
        "sheet/scale" => text.map_or_else(|| choose("scale"), |v| format!("draw at {v}")),
        "sheet/keep_layout" => if value.and_then(serde_json::Value::as_bool) == Some(true) {
            "keep the paper, scale and sheet count; report moved views".into()
        } else {
            "lay out the whole sheet again".into()
        },
        "sheet/size" => text.map_or_else(|| choose("paper size"), |v| format!("use {v} paper")),
        "sheet/zones" => if value.and_then(serde_json::Value::as_bool) == Some(false) {
            "omit the grid zones and centring marks where no reference cites them".into()
        } else {
            "draw the grid zones and centring marks".into()
        },
        "sheet/dimension_spacing" => text.map_or_else(
            || choose("dimension spacing"),
            |v| format!("keep {v} dimension spacing"),
        ),
        "sheet/title/tolerance" => text.map_or_else(
            || "remove the general tolerance from the title block".into(),
            |v| if v == "UNSPECIFIED" {
                "mark the general tolerance as UNSPECIFIED in the title block, without choosing a tolerance".into()
            } else {
                format!("add the general tolerance {v} to the title block")
            },
        ),
        "hole_table" => match text.as_deref() {
            Some("always") => "list the holes in a hole table".into(),
            Some("never") => "draw the hole callouts beside the views".into(),
            _ => "let draftwright choose whether to list the holes in a hole table".into(),
        },
        "notes" => match value.and_then(|v| v.as_array()) {
            Some(notes) if notes.is_empty() => "remove the added notes".into(),
            Some(notes) => format!(
                "keep the added notes {}",
                notes
                    .iter()
                    .filter_map(|n| n["text"].as_str())
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
            None => "remove the added notes".into(),
        },
        _ => match parts.as_slice() {
            ["views", _, "omit"] => {
                if value.and_then(|v| v.as_bool()) == Some(true) {
                    format!("hide {}", view())
                } else {
                    format!("show {}", view())
                }
            }
            ["views", _, "place", ..] => format!("let draftwright position {}", view()),
            ["views", _] if value.is_none() => format!("remove {}", view()),
            ["dimensions" | "annotations", _, "place"] => {
                if let Some(place) = value.and_then(|v| {
                    serde_json::from_value::<crate::placement_intent::Place>(v.clone()).ok()
                }) {
                    placement_description(item(), &place)
                        .trim_end_matches('.')
                        .to_lowercase()
                } else {
                    format!("let draftwright position {}", item())
                }
            }
            ["dimensions", _, "omit"] => format!("show {}", item()),
            ["dimensions", _, "tolerance"] => format!("remove the tolerance from {}", item()),
            ["features", _, "fit"] => "remove the fit from the feature".into(),
            ["dimensions", _] if value.is_some() => "draw the dimension".into(),
            ["dimensions" | "annotations" | "features" | "faces", _] => {
                format!(
                    "remove the {} settings for the previous part revision",
                    parts[0].trim_end_matches('s')
                )
            }
            _ => {
                // External operations are not engine remedies. Still name their value
                // and field in ordinary words rather than emitting an opaque fallback.
                let field = parts
                    .last()
                    .copied()
                    .unwrap_or("drawing settings")
                    .replace('_', " ");
                text.map_or_else(
                    || format!("clear the {field}"),
                    |v| format!("set the {field} to {v}"),
                )
            }
        },
    }
}

pub(crate) fn placement_description(item: &str, place: &crate::placement_intent::Place) -> String {
    let view = crate::wording::view(place.view.as_deref().unwrap_or("front"));
    if let Some([x, y]) = place.offset {
        let components: Vec<_> = [(x, "left", "right"), (y, "down", "up")]
            .into_iter()
            .filter_map(|(value, negative, positive)| {
                let amount = crate::dims::fmt(value.abs());
                (amount != "0").then(|| {
                    format!(
                        "{amount} mm {}",
                        if value < 0.0 { negative } else { positive }
                    )
                })
            })
            .collect();
        let distance = components.join(" and ");
        return if item.ends_with("callout") {
            if components.is_empty() {
                format!("Move the leader bend of {item} to its tip, in {view}.")
            } else {
                format!("Move the leader bend of {item} {distance} from its tip, in {view}.")
            }
        } else if components.is_empty() {
            format!("Keep {item} where draftwright placed it, in {view}.")
        } else {
            let relation = if crate::dims::fmt(y.abs()) == "0" {
                "of"
            } else {
                "from"
            };
            format!("Move {item} {distance} {relation} where draftwright placed it, in {view}.")
        };
    }

    let Some(side) = place.side else {
        return format!("Let draftwright position {item}.");
    };
    let side = match side {
        crate::placement_intent::Side::Above => "above",
        crate::placement_intent::Side::Below => "below",
        crate::placement_intent::Side::Left => "to the left of",
        crate::placement_intent::Side::Right => "to the right of",
    };
    let row = match place.lane.unwrap_or(0) {
        0 => "nearest the view".into(),
        1 => "in the second row of dimensions".into(),
        n => format!("in row {} of dimensions", n + 1),
    };
    format!("Move {item} {side} {view}, {row}.")
}

impl Serialize for Fix {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let mut s = serializer.serialize_struct("Fix", 3)?;
        s.serialize_field("scope", &self.scope())?;
        s.serialize_field("description", &self.description())?;
        s.serialize_field("ops", &self.ops)?;
        s.end()
    }
}

pub(crate) fn attach(analysis: &crate::model::Analysis, plan: &mut crate::model::Plan) {
    use sha2::{Digest, Sha256};
    // Preview and commit revisit identical settled ink. Reuse only exact input
    // results, in the planner's existing bounded cache, without another search.
    static MEMO: crate::planning::BoundedMemo<Vec<crate::model::Problem>> =
        crate::planning::BoundedMemo::new(64);
    let key: [u8; 32] = Sha256::digest(
        serde_json::to_vec(&(&*plan, crate::planning::policy_key(analysis)))
            // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
            .expect("fix inputs serialize"),
    )
    .into();
    if let Some(problems) = MEMO.get(&key) {
        plan.problems = problems;
        return;
    }
    crate::planning::with(analysis, || attach_inner(analysis, plan));
    MEMO.insert(key, plan.problems.clone());
}

/// Marks a local fix that adds findings of its own, as `dims::local_placement_fix` words it.
pub(crate) const TRADE_OFF: &str = " Trade-off: adds these findings: ";

/// Whether applying `ops` keeps the settled sheet, so the fix can be offered as local. The
/// sheet must be a single one at the size and scale the spec asked for (or left automatic),
/// and the edit must not touch a radius table's carrier.
pub(crate) fn keeps_layout(plan: &crate::model::Plan, ops: &[Op]) -> bool {
    let retainable = plan.sheets.is_empty()
        && plan
            .spec
            .sheet
            .size
            .as_ref()
            .is_none_or(|s| s == &plan.sheet.size)
        && plan
            .spec
            .sheet
            .scale
            .as_ref()
            .is_none_or(|s| s == &plan.sheet.scale);
    if !retainable {
        return false;
    }
    // An automatic radius table's centre coordinates are extra information.
    // A settled-ink trial cannot reconstruct the withdrawn leaders and prove
    // that moving another item on their carrier will keep that table. Widen
    // conservatively rather than advertise an unverified local promise.
    let table_carrier = plan.radius_table.as_ref().is_some_and(|table| {
        ops.iter().any(|op| {
            if op.path.starts_with("annotations/") {
                return true;
            }
            let Some(rest) = op.path.strip_prefix("dimensions/") else {
                return false;
            };
            let id = rest
                .split('/')
                .next()
                .unwrap_or("")
                .replace("~1", "/")
                .replace("~0", "~");
            let affects = |view: &str| table.rows.iter().any(|r| r.view == view);
            let old = plan
                .dimensions
                .iter()
                .find(|d| d.id == id)
                .is_some_and(|d| affects(&d.view));
            let new = op
                .value
                .as_ref()
                .and_then(|v| v.get("view"))
                .and_then(serde_json::Value::as_str)
                .is_some_and(|v| affects(&format!("v:{v}")));
            old || new
        })
    });
    !table_carrier
}

/// O(operations per offer), with no recovered-plan invocation per candidate.
/// Existing bounded trials check the settled inventory and ink. Intent fields bypass
/// automatic sheet selection and refitting on apply. Never advertise unconstrained allocation
/// as local. Scope read from old JSON may widen, but cannot understate path scope.
pub(crate) fn retain_local_layout(plan: &mut crate::model::Plan) {
    // Taken out so the rest of the plan can be read while the offers are rewritten.
    let mut problems = std::mem::take(&mut plan.problems);
    for problem in &mut problems {
        let mut fixes = problem.offered_fixes();
        for fix in &mut fixes {
            // Choosing the title-block tolerance changes no view placement. Keep
            // this offer local without turning automatic paper selection into a pin.
            if !fix.ops.is_empty() && fix.ops.iter().all(|op| op.path == "sheet/title/tolerance") {
                continue;
            }
            if fix.scope() != Scope::Local {
                if plan.spec.sheet.keep_layout == Some(true)
                    && fix.scope() == Scope::Sheet
                    && !fix.ops.iter().any(|op| op.path == "sheet/keep_layout")
                {
                    fix.ops.push(Op::remove("sheet/keep_layout"));
                }
                continue;
            }
            if keeps_layout(plan, &fix.ops) {
                fix.ops.retain(|op| {
                    !matches!(
                        op.path.as_str(),
                        "sheet/keep_layout"
                            | "sheet/size"
                            | "sheet/scale"
                            | "sheets/max"
                            | "sheet/dimension_spacing"
                    )
                });
                fix.ops
                    .push(Op::set("sheet/keep_layout", serde_json::json!(true)));
                fix.ops
                    .push(Op::set("sheet/size", serde_json::json!(plan.sheet.size)));
                fix.ops
                    .push(Op::set("sheet/scale", serde_json::json!(plan.sheet.scale)));
                fix.ops.push(Op::set("sheets/max", serde_json::json!(1)));
                // An automatic compact fallback is part of the retained layout.
                if plan.spec.sheet.dimension_spacing.is_none()
                    && plan.sheet.dimension_spacing == crate::model::DimensionSpacing::Compact
                {
                    fix.ops.push(Op::set(
                        "sheet/dimension_spacing",
                        serde_json::json!("compact"),
                    ));
                }
            } else {
                // The settled-ink trial assumed the rest of the sheet stays put, and a
                // whole-sheet layout does not promise that. The fix is still offered, at sheet
                // scope: its real outcome is for a preview to show (`Session::apply`).
                fix.minimum_scope = Some(Scope::Sheet);
                fix.ops.retain(|op| {
                    !matches!(
                        op.path.as_str(),
                        "sheet/keep_layout"
                            | "sheet/size"
                            | "sheet/scale"
                            | "sheets/max"
                            | "sheet/dimension_spacing"
                    )
                });
                if plan.spec.sheet.keep_layout == Some(true) {
                    fix.ops.push(Op::remove("sheet/keep_layout"));
                }
            }
        }
        fixes.sort_by_key(Fix::scope);
        problem.fix = fixes.first().map(|f| f.ops.clone()).unwrap_or_default();
        problem.fixes = fixes;
    }
    plan.problems = problems;
}

/// Add remedies only after selecting the drawing. As with the existing placement
/// fixes, compatibility and Spec edits are checked; layout effects need preview/apply.
/// No planner or full preview is invoked to generate a suggestion.
fn attach_inner(analysis: &crate::model::Analysis, plan: &mut crate::model::Plan) {
    let relevant = |p: &crate::model::Problem| {
        matches!(
            p.code.as_str(),
            "annotation_leader_crossing"
                | "extension_through_feature"
                | "leader_through_feature"
                | "annotation_overlap"
                | "annotation_ink_overlap"
                | "label_centerline_overlap"
        )
    };
    if !plan.problems.iter().any(relevant) {
        return;
    }
    let options = crate::dims::placement_options_with_spec(analysis, &plan.views, &plan.spec);
    // Reuse schedule eligibility only, without installing or laying out a table.
    // Always + no pins avoids select's canonical-plan branches.
    let sheet_remedy = if !crate::placement_intent::has_pins(&plan.spec)
        && plan.spec.hole_table == crate::hole_table::Setting::Auto
        && plan.hole_tables.is_empty()
        && plan.hole_table.is_none()
        && plan.spec.dimension_source == crate::model::DimensionSource::Auto
    {
        let mut table = plan.clone();
        table.spec.hole_table = crate::hole_table::Setting::Always;
        let selection = crate::hole_table::select(analysis, &table);
        plan.spec
            .scales()
            .iter()
            .copied()
            .find(|scale| {
                *scale < plan.sheet.scale_factor
                    && *scale >= crate::sheet_fit::min_legible_scale_in(analysis, plan.spec.units)
                    && *scale >= crate::sheets::floor(&plan.spec).unwrap_or(0.0)
            })
            .filter(|_| !selection.tables.is_empty())
            .map(|scale| {
                let scale = crate::model::scale_name(scale);
                Fix::new(
                    format!("List the holes in a hole table and draw at {scale}."),
                    vec![
                        Op::set("hole_table", serde_json::json!("always")),
                        Op::set("sheet/scale", serde_json::json!(scale)),
                    ],
                )
            })
    } else {
        None
    };
    let snapshot = plan.clone();
    let trials = crate::dims::LocalFixTrials::default();
    for problem in plan.problems.iter_mut().filter(|p| relevant(p)) {
        // finish() can run more than once; don't duplicate earlier remedies.
        if problem
            .fixes
            .iter()
            .any(|f| f.description.starts_with("Move ") || f.description.starts_with("Place "))
        {
            continue;
        }
        let old = problem.offered_fixes();
        let mut local = Vec::new();
        // Keep existing annotation/pin remedies; otherwise validate compatible item
        // placements with the existing ink checks, never a full sheet preview.
        if (problem.fix.is_empty()
            || problem
                .subjects
                .first()
                .is_some_and(|id| plan.dimensions.iter().any(|d| &d.id == id)))
            && let Some(fix) =
                crate::dims::local_placement_fix(analysis, &snapshot, problem, &options, &trials)
        {
            local.push(fix);
        }
        if local.is_empty() {
            local.extend(old);
        } else {
            local.extend(old.into_iter().filter(|f| f.scope() != Scope::Local));
        }
        if let Some(fix) = &sheet_remedy {
            local.push(fix.clone());
        }
        let mut unique = Vec::<Fix>::new();
        for fix in local {
            if !unique.iter().any(|f| f.ops == fix.ops) {
                unique.push(fix);
            }
        }
        unique.sort_by_key(Fix::scope);
        if let Some(fix) = unique.first() {
            problem.fix = fix.ops.clone();
        }
        problem.fixes = unique;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn equal_fix_operations_compare_effective_scope_floors() {
        for (path, groups) in [
            (
                "dimensions/d/place/lane",
                vec![
                    vec![None, Some(Scope::Local)],
                    vec![Some(Scope::View)],
                    vec![Some(Scope::Sheet)],
                ],
            ),
            (
                "views/front/place/gap",
                vec![
                    vec![None, Some(Scope::Local), Some(Scope::View)],
                    vec![Some(Scope::Sheet)],
                ],
            ),
            (
                "sheet/scale",
                vec![vec![
                    None,
                    Some(Scope::Local),
                    Some(Scope::View),
                    Some(Scope::Sheet),
                ]],
            ),
        ] {
            let fixes: Vec<_> = groups
                .iter()
                .enumerate()
                .flat_map(|(group, floors)| {
                    floors.iter().map(move |floor| {
                        let mut fix = Fix::new("Edit", vec![Op::set(path, serde_json::json!(1))]);
                        fix.minimum_scope = *floor;
                        (group, fix)
                    })
                })
                .collect();
            for (group, fix) in &fixes {
                for (other_group, other) in &fixes {
                    assert_eq!(
                        fix == other,
                        group == other_group,
                        "{path}: {fix:?}, {other:?}"
                    );
                }
            }
        }
    }
    #[test]
    fn alternatives_sort_bundles_without_reordering_operations() {
        use crate::model::Problem;
        let sheet = vec![
            Op::set("hole_table", "always".into()),
            Op::set("sheet/scale", "1:10".into()),
        ];
        let p = Problem {
            fix: sheet.clone(),
            fixes: vec![
                Fix::new("Move view", vec![Op::remove("views/iso/place")]),
                Fix::new(
                    "Move label",
                    vec![Op::set("dimensions/d:a~1b/place/lane", 1.into())],
                ),
            ],
            ..Default::default()
        };
        let wire = serde_json::to_value(&p).unwrap();
        assert_eq!(wire["fixes"][0]["scope"], "local");
        assert_eq!(wire["fixes"][1]["scope"], "view");
        assert_eq!(wire["fixes"][2]["scope"], "sheet");
        assert_eq!(
            wire["fixes"][2]["ops"],
            serde_json::to_value(sheet).unwrap()
        );
        let description = wire["fixes"][2]["description"].as_str().unwrap();
        assert_eq!(
            description,
            format!("List the holes in a hole table and draw at 1:10. {SHEET_CONSEQUENCE}")
        );
        let reread: Problem = serde_json::from_value(wire.clone()).unwrap();
        assert_eq!(p, reread);
        assert_eq!(serde_json::to_value(reread).unwrap(), wire);
        let understated: Fix = serde_json::from_value(serde_json::json!({
            "scope":"local", "description":"Change paper", "ops":[{"op":"set","path":"sheet/size","value":"A3"}]
        })).unwrap();
        assert_eq!(understated.scope(), Scope::Sheet);
    }
    #[test]
    fn descriptions_name_operation_values_once() {
        for (ops, expected) in [
            (
                vec![Op::set("sheet/title/tolerance", "ISO 2768-mK".into())],
                "Add the general tolerance ISO 2768-mK to the title block.".to_string(),
            ),
            (
                vec![Op::set("sheet/title/tolerance", "UNSPECIFIED".into())],
                "Mark the general tolerance as UNSPECIFIED in the title block, without choosing a tolerance.".to_string(),
            ),
            (
                vec![Op::remove("sheet/scale")],
                format!("Let draftwright choose the scale. {SHEET_CONSEQUENCE}"),
            ),
            (
                vec![
                    Op::set("sheet/size", "A3".into()),
                    Op::set("sheet/scale", "1:5".into()),
                ],
                format!("Use A3 paper and draw at 1:5. {SHEET_CONSEQUENCE}"),
            ),
            (
                vec![Op::set("views/iso/omit", true.into())],
                "Hide the isometric view.".to_string(),
            ),
            (
                vec![Op::remove("views/plan/omit")],
                "Show the plan view.".to_string(),
            ),
            (
                vec![Op::remove("dimensions/d/place")],
                "Let draftwright position the dimension.".to_string(),
            ),
        ] {
            let fix = Fix::from_ops(ops.clone());
            assert_eq!(fix.description(), expected);
            assert_eq!(fix.ops, ops);
        }
    }

    #[test]
    fn placement_words_follow_the_position_and_offset_origin() {
        let place =
            serde_json::from_value(serde_json::json!({"view":"plan", "side":"above", "lane":0}))
                .unwrap();
        assert_eq!(
            placement_description("the 16 × 30 × 4 DEEP callout", &place),
            "Move the 16 × 30 × 4 DEEP callout above the plan view, nearest the view."
        );
        let place = serde_json::from_value(
            serde_json::json!({"view":"front", "offset":[-4.0, 6.0], "pinned":true}),
        )
        .unwrap();
        assert_eq!(
            placement_description("the Ø8 callout", &place),
            "Move the leader bend of the Ø8 callout 4 mm left and 6 mm up from its tip, in the front view."
        );
    }

    #[test]
    fn offsets_use_drawing_numbers_and_omit_zero_components() {
        for (item, offset, expected) in [
            (
                "the 404.441 dimension",
                [-14.0, 0.0],
                "Move the 404.441 dimension 14 mm left of where draftwright placed it, in the plan view.",
            ),
            (
                "the C1.134 callout",
                [-3.9, -0.3],
                "Move the leader bend of the C1.134 callout 3.9 mm left and 0.3 mm down from its tip, in the plan view.",
            ),
            (
                "the 20 dimension",
                [-0.0, 2.375],
                "Move the 20 dimension 2.375 mm up from where draftwright placed it, in the plan view.",
            ),
            (
                "the Ø8 callout",
                [0.0, 5.0],
                "Move the leader bend of the Ø8 callout 5 mm up from its tip, in the plan view.",
            ),
            (
                "the 20 dimension",
                [0.0, 0.0],
                "Keep the 20 dimension where draftwright placed it, in the plan view.",
            ),
            (
                "the Ø8 callout",
                [0.0, 0.0],
                "Move the leader bend of the Ø8 callout to its tip, in the plan view.",
            ),
        ] {
            let place = serde_json::from_value(
                serde_json::json!({"view":"plan", "offset":offset,"pinned":true}),
            )
            .unwrap();
            assert_eq!(placement_description(item, &place), expected);
        }
    }

    #[test]
    fn classification_table() {
        use Scope::*;
        for (path, expected) in [
            ("dimensions/d:a~1b/place", Local),
            ("dimensions/d/place/side", Local),
            ("dimensions/d/place/lane", Local),
            ("dimensions/d/place/along", Local),
            ("dimensions/d/place/offset", Local),
            ("dimensions/d/text/template", Local),
            ("dimensions/d/style", Local),
            ("dimensions/d/omit", Local),
            ("dimensions/d/tolerance", Local),
            ("annotations/g/place", Local),
            ("features/f/fit", Local),
            ("views/iso/place/gap", View),
            ("views/iso/scale", View),
            ("views/plan/omit", View),
            ("views/detail_a", View),
            ("views/section_aa", View),
            ("sheet/size", Sheet),
            ("sheet/scale", Sheet),
            ("sheets/max", Sheet),
            ("views/iso/sheet", Sheet),
            ("tables/0/sheet", Sheet),
            ("hole_table", Sheet),
            ("dimension_source", Sheet),
            ("dimension_schemes/xy", Sheet),
            ("pmi/mode", Sheet),
            ("unknown", Sheet),
            ("dimensions/d/new_field", Sheet),
            ("views/iso/new_field", Sheet),
            ("dimensions", Sheet),
            ("", Sheet),
        ] {
            assert_eq!(path_scope(path), expected, "{path}");
        }
        assert_eq!(
            scope(&[
                Op::set("dimensions/d/omit", true.into()),
                Op::remove("sheet/scale")
            ]),
            Sheet
        );
        assert_eq!(
            scope(&[
                Op::set("dimensions/d/place/lane", 1.into()),
                Op::set("dimensions/d/omit", false.into())
            ]),
            Local
        );
        assert_eq!(
            scope(&[
                Op::remove("dimensions/a/tolerance"),
                Op::remove("dimensions/b/tolerance")
            ]),
            Sheet
        );
        assert_eq!(
            scope(&[
                Op::remove("views/front/omit"),
                Op::remove("views/side/omit")
            ]),
            Sheet
        );
        assert_eq!(
            scope(&[Op::set("views/iso", serde_json::json!({"sheet":2}))]),
            Sheet
        );
        assert_eq!(
            scope(&[Op::set(
                "views/detail_a",
                serde_json::json!({"detail":{"around":"f:hole:1","scale_factor":2}})
            )]),
            View
        );
        assert_eq!(
            scope(&[Op::set(
                "dimensions/d",
                serde_json::json!({"place":{"lane":1},"omit":false})
            )]),
            Local
        );
        assert_eq!(
            scope(&[Op::set(
                "dimensions/d",
                serde_json::json!({"new_field":true})
            )]),
            Sheet
        );
        assert_eq!(
            scope(&[Op::set("dimensions", serde_json::json!({"d":{}}))]),
            Sheet
        );
        assert_eq!(scope(&[]), Sheet);
    }
}
