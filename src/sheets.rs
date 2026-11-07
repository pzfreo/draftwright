//! R5's allocation: recover paper first, then move cumulative groups of auxiliary content.

use std::cell::Cell;
use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::items::{Op, State};
use crate::model::{self, Analysis, Invalid, Plan, PlannedSheet, PlannedView, Problem, Spec};
use crate::{hole_table, layout};

pub(crate) fn one() -> u32 {
    1
}
pub(crate) fn is_one(n: &u32) -> bool {
    *n == 1
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct SheetsSpec {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub smallest_scale: Option<String>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub keep_together: Vec<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub hole_table: Option<u32>,
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub gdt_on_sheet_1: bool,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Zones {
    pub columns: u32,
    pub rows: u32,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct CrossRef {
    pub sheet: u32,
    pub zone: String,
    pub at: [f64; 2],
}

pub fn zones(page: [f64; 2]) -> Zones {
    let (columns, rows) = layout::PAGES
        .iter()
        .position(|(_, w, h)| [*w, *h] == page)
        .map(|i| [(6, 4), (8, 6), (12, 8), (16, 12), (24, 16)][i])
        .unwrap_or_else(|| {
            (
                ((page[0] / 100.0).round() as u32 * 2).max(2),
                ((page[1] / 100.0).round() as u32 * 2).max(2),
            )
        });
    Zones { columns, rows }
}

/// ISO 5457 sheet furniture: grid zones (and the centring marks drawn with them) on every
/// sheet unless `sheet/zones` is false. Zones a section cross-reference cites stay, reported
/// once as `sheet_zones_kept`.
pub(crate) fn furnish(plan: &mut Plan) {
    let cited = plan
        .views
        .iter()
        .any(|v| v.xref.is_some() || v.cut.as_ref().is_some_and(|c| c.xref.is_some()));
    let off = plan.spec.sheet.zones == Some(false);
    let draw = !off || cited;
    plan.sheet.zones = draw.then(|| zones(plan.sheet.page));
    for sheet in &mut plan.sheets {
        sheet.zones = draw.then(|| zones(sheet.page));
    }
    if off && cited && !plan.problems.iter().any(|p| p.code == "sheet_zones_kept") {
        plan.problems.push(problem(
            "sheet_zones_kept",
            "info",
            "sheet",
            "Grid zones are kept despite sheet/zones false: section cross-references cite them."
                .into(),
            vec![Op::remove("sheet/zones")],
        ));
    }
}

/// ISO 5457: numbers left to right, letters top to bottom, omitting I and O.
pub fn zone(page: [f64; 2], point: [f64; 2]) -> String {
    let grid = zones(page);
    let col = ((point[0] / page[0] * f64::from(grid.columns)).floor() as u32).min(grid.columns - 1);
    let row = (((page[1] - point[1]) / page[1] * f64::from(grid.rows)).floor() as usize)
        .min(grid.rows as usize - 1);
    let letter = b"ABCDEFGHJKLMNPQRSTUVWXYZ"[row.min(23)] as char;
    format!("{letter}{}", col + 1)
}

pub(crate) fn floor(spec: &Spec) -> Option<f64> {
    spec.sheets
        .as_ref()?
        .smallest_scale
        .as_ref()
        .and_then(|name| {
            layout::SCALES
                .into_iter()
                .find(|s| model::scale_name(*s) == *name)
        })
}

/// Only permission to allocate more sheets; no authored allocation constraints.
pub(crate) fn max_only(spec: &Spec) -> bool {
    spec.sheets.as_ref().is_none_or(|s| {
        (SheetsSpec {
            max: None,
            ..s.clone()
        }) == Default::default()
    }) && spec.views.values().all(|v| v.sheet.is_none())
        && spec.tables.iter().all(|t| t.sheet.is_none())
}

fn maximum(spec: &Spec) -> u32 {
    spec.sheets.as_ref().and_then(|s| s.max).unwrap_or(1)
}

fn assignments(spec: &Spec) -> BTreeMap<String, u32> {
    let mut out: BTreeMap<_, _> = spec
        .views
        .iter()
        .filter_map(|(name, v)| v.sheet.map(|s| (name.clone(), s)))
        .collect();
    if let Some(n) = spec.sheets.as_ref().and_then(|s| s.hole_table) {
        out.insert("hole_table".into(), n);
    }
    out
}

fn groups(spec: &Spec) -> Vec<Vec<String>> {
    let mut out: Vec<Vec<String>> = ["iso", "section_aa", "hole_table", "front", "plan", "side"]
        .into_iter()
        .map(|s| vec![s.into()])
        .collect();
    let mut links = spec
        .sheets
        .as_ref()
        .map_or_else(Vec::new, |s| s.keep_together.clone());
    links.extend(spec.views.iter().filter_map(|(name, v)| {
        v.place
            .as_ref()
            .map(|p| vec![name.clone(), p.relative_to.clone()])
    }));
    for link in links {
        let mut combined = BTreeSet::new();
        out.retain(|g| {
            if g.iter().any(|s| link.contains(s)) {
                combined.extend(g.iter().cloned());
                false
            } else {
                true
            }
        });
        combined.extend(link);
        out.push(combined.into_iter().collect());
    }
    out
}

pub(crate) fn validate(spec: &Spec) -> Result<(), Invalid> {
    if spec
        .sheet
        .frame_gutter
        .is_some_and(|g| !(g.is_finite() && (0.0..=20.0).contains(&g)))
    {
        return Err(Invalid {
            message: "sheet.frame_gutter must be a number of millimetres from 0 to 20.".into(),
            choices: vec!["0".into(), "5".into()],
            path: Some("sheet/frame_gutter".into()),
            also: Vec::new(),
        });
    }
    for (id, &n) in &spec.sheet.table_columns {
        if !hole_table::SPLIT_TABLES.contains(&id.as_str()) && !crate::tables::split_id(id, spec) {
            return Err(Invalid {
                message: format!(
                    "sheet.table_columns names {id:?}; it sets a table's side-by-side blocks: a declared table ID, t:radius-centres, manufacturing_requirements, gear_data_<owner>, hole_table_plan or hole_schedule_<view> (front, plan or side)."
                ),
                choices: hole_table::SPLIT_TABLES.map(String::from).to_vec(),
                path: Some(format!("sheet/table_columns/{id}")),
                also: Vec::new(),
            });
        }
        if !(1..=hole_table::MAX_COLUMNS as u32).contains(&n) {
            return Err(Invalid {
                message: format!(
                    "sheet.table_columns/{id} is {n}; a table is drawn in 1 to {} side-by-side blocks (1 never splits it).",
                    hole_table::MAX_COLUMNS
                ),
                choices: (1..=hole_table::MAX_COLUMNS)
                    .map(|n| n.to_string())
                    .collect(),
                path: Some(format!("sheet/table_columns/{id}")),
                also: Vec::new(),
            });
        }
    }
    if spec.sheets.is_none()
        && spec.views.values().all(|v| v.sheet.is_none())
        && spec.tables.iter().all(|t| t.sheet.is_none())
    {
        return Ok(());
    }
    let max = maximum(spec);
    let refuse = |message, choices| Invalid {
        message,
        choices,
        path: None,
        also: Vec::new(),
    };
    if !(1..=9).contains(&max) {
        return Err(refuse(
            "sheets.max must be from 1 to 9.".into(),
            (1..=9).map(|n| n.to_string()).collect(),
        ));
    }
    for (name, n) in assignments(spec).into_iter().chain(
        spec.tables
            .iter()
            .map(|t| (t.id.clone(), t.sheet.unwrap_or(1))),
    ) {
        if n == 0 || n > max {
            let mut choices: Vec<_> = (1..=max).map(|n| n.to_string()).collect();
            if (2..=9).contains(&n) {
                choices.push(format!("set sheets/max {n}"));
            }
            return Err(refuse(
                format!(
                    "{name}'s sheet {n} is outside 1..{max}; set sheets/max to allow more sheets."
                ),
                choices,
            ));
        }
        if ["front", "plan", "side"].contains(&name.as_str()) && n != 1 {
            return Err(refuse(
                format!("Principal view {name} must stay on sheet 1."),
                vec!["1".into()],
            ));
        }
    }
    if let Some(s) = &spec.sheets {
        if let Some(name) = &s.smallest_scale {
            let Some(f) = floor(spec) else {
                return Err(refuse(
                    format!("{name:?} is not a standard smallest scale."),
                    layout::SCALES.map(model::scale_name).to_vec(),
                ));
            };
            if spec
                .sheet
                .scale
                .as_ref()
                .and_then(|name| {
                    layout::SCALES
                        .into_iter()
                        .find(|v| model::scale_name(*v) == *name)
                })
                .is_some_and(|scale| scale < f)
            {
                return Err(refuse(
                    "sheet.scale is below sheets.smallest_scale; the intents contradict.".into(),
                    vec![
                        "remove sheet/scale".into(),
                        format!(
                            "set sheets/smallest_scale {}",
                            // Invariant: The preceding is_some_and succeeded on this same optional scale.
                            spec.sheet.scale.as_ref().unwrap()
                        ),
                    ],
                ));
            }
        }
        for group in &s.keep_together {
            if group.len() < 2
                || group.iter().collect::<BTreeSet<_>>().len() != group.len()
                || group.iter().any(|n| {
                    !["front", "plan", "side", "iso", "section_aa", "hole_table"]
                        .contains(&n.as_str())
                })
            {
                return Err(refuse(
                    format!(
                        "Invalid keep-together group {group:?}; use distinct view names or hole_table."
                    ),
                    vec![
                        "front".into(),
                        "plan".into(),
                        "side".into(),
                        "iso".into(),
                        "section_aa".into(),
                        "hole_table".into(),
                    ],
                ));
            }
        }
    }
    let assigned = assignments(spec);
    for group in groups(spec) {
        let sheets: BTreeSet<_> = group
            .iter()
            .filter_map(|n| {
                assigned
                    .get(n)
                    .copied()
                    .or_else(|| ["front", "plan", "side"].contains(&n.as_str()).then_some(1))
            })
            .collect();
        if sheets.len() > 1 {
            let mut choices: Vec<_> = sheets.iter().map(|n| format!("assign sheet {n}")).collect();
            choices.extend(
                group
                    .iter()
                    .filter(|n| spec.views.get(*n).is_some_and(|v| v.place.is_some()))
                    .map(|n| format!("remove views/{n}/place")),
            );
            return Err(refuse(
                format!(
                    "Assignments split keep-together group {group:?}, including relative_to placement dependencies."
                ),
                choices,
            ));
        }
    }
    Ok(())
}

fn shown(spec: &Spec, name: &str) -> bool {
    !spec.views.get(name).is_some_and(|v| v.omit)
}

fn available(analysis: &Analysis, spec: &Spec, name: &str, long: bool) -> bool {
    match name {
        "iso" => shown(spec, name) && analysis.drawing_iso_bounds() != [0.; 4],
        "section_aa" => {
            shown(spec, name)
                && shown(spec, "plan")
                && (shown(spec, "front") || shown(spec, "side"))
                && crate::demand::read(analysis).section.is_some()
        }
        "hole_table" => long,
        _ => false,
    }
}

fn candidates(analysis: &Analysis, spec: &Spec, long: bool) -> Vec<Vec<String>> {
    let assigned = assignments(spec);
    let mut out = Vec::new();
    for name in ["iso", "section_aa", "hole_table"] {
        let group = groups(spec)
            .into_iter()
            .find(|g| g.iter().any(|n| n == name))
            // Invariant: groups inserts singleton groups for every allocatable view/table name.
            .unwrap();
        if out.contains(&group)
            || !group.iter().any(|n| available(analysis, spec, n, long))
            || group.iter().any(|n| {
                ["front", "plan", "side"].contains(&n.as_str())
                    || assigned.contains_key(n)
                    || spec
                        .views
                        .get(n)
                        .and_then(|v| v.place.as_ref())
                        .is_some_and(|p| p.pinned)
            })
        {
            continue;
        }
        out.push(group);
    }
    out
}

fn working(analysis: &Analysis, spec: &Spec, moved: &BTreeMap<String, u32>) -> Spec {
    let mut copy = spec.clone();
    for (name, sheet) in moved {
        if name == "hole_table" {
            copy.sheets.get_or_insert_with(Default::default).hole_table =
                Some(if *sheet == 0 { 2 } else { *sheet });
        } else if available(analysis, spec, name, false) {
            let view = copy.views.entry(name.clone()).or_default();
            view.omit = true;
            view.sheet = Some(if *sheet == 0 { 2 } else { *sheet });
        }
    }
    copy.tables.retain(|t| t.sheet.unwrap_or(1) == 1);
    copy
}

thread_local! {
    // Monotonic per-thread count lets an outer allocation include its nested small-paper tries.
    static RUNS: Cell<usize> = const { Cell::new(0) };
}

/// Include recovered runs performed while preparing and finishing a public plan,
/// so sessions use the same planner as the CLI without losing their accounting.
pub(crate) fn count_runs<T>(steps: &mut usize, run: impl FnOnce() -> T) -> T {
    let before = RUNS.with(Cell::get);
    let result = run();
    *steps += RUNS.with(Cell::get) - before;
    result
}

fn run(analysis: &Analysis, spec: &Spec) -> Result<Plan, Invalid> {
    if crate::planning::is_fixed_pair()
        && (max_only(spec) || (spec.sheet.size.is_some() && spec.sheet.scale.is_some()))
    {
        // Paper/scale and the moved prefix determine recovered sheet-1 ink.
        // Increasing the sheet-count permission only changes the packer's capacity;
        // reuse this existing run cache across 2..N instead of rerouting each prefix.
        // allocate_inner validates the real bound before running any prefix.
        let mut single = spec.clone();
        if max_only(spec) {
            single.sheets = None;
        } else if let Some(sheets) = &mut single.sheets {
            sheets.max = Some(9);
        }
        let key = serde_json::to_vec(&(
            &single,
            crate::planning::policy_key(analysis),
            crate::planning::nominals_deferred(),
        ))
        // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
        .expect("fixed sheet inputs serialize");
        let mut plan = crate::planning::memo(analysis, "fixed-sheet-run", key, || {
            run_uncached(analysis, &single)
        })?;
        plan.spec = spec.clone();
        return Ok(plan);
    }
    run_uncached(analysis, spec)
}

fn run_uncached(analysis: &Analysis, spec: &Spec) -> Result<Plan, Invalid> {
    RUNS.with(|runs| runs.set(runs.get() + 1));
    crate::planning::with(analysis, || {
        model::recover(
            analysis,
            spec,
            model::plan_escalated_layout(analysis, spec)?,
        )
    })
}

/// Single-sheet recovery may invoke the small-part overflow allocation before large paper.
pub(crate) fn allocate(analysis: &Analysis, spec: &Spec) -> Result<Plan, Invalid> {
    allocate_counted(analysis, spec, &mut 0)
}

/// Count actual recovered sheet-1 runs, including prefixes whose packing later fails.
pub(crate) fn allocate_counted(
    analysis: &Analysis,
    spec: &Spec,
    steps: &mut usize,
) -> Result<Plan, Invalid> {
    let before = RUNS.with(Cell::get);
    let result = (|| {
        let mut plan = allocate_inner(analysis, spec, None)?;
        // Finish forced allocations and table presentation before checking paths that
        // bypassed ordinary recovery (fixed scale, pins, or a rebuilt hole schedule).
        if spec.sheet.size.is_none()
            && spec.sheets.as_ref().and_then(|s| s.max) != Some(1)
            && ["A2", "A1", "A0"].contains(&plan.sheet.size.as_str())
            && model::fits_a3_at_full_size(analysis, &plan)
            && (spec.sheet.scale.is_some()
                || crate::placement_intent::has_pins(spec)
                || !plan.hole_tables.is_empty()
                || assignments(spec).values().any(|n| *n > 1)
                || spec.tables.iter().any(|t| t.sheet.is_some_and(|n| n > 1)))
        {
            let mut tried = Vec::new();
            if let Some(small) = small_fallback(analysis, spec, &plan, &mut tried) {
                plan = small;
            } else if !tried.is_empty() {
                plan.sheet.basis += &format!("; {}", tried.join("; "));
            }
        }
        crate::iso_caption::refresh(analysis, &mut plan);
        crate::view_gutters::apply(analysis, &mut plan);
        // Gutters translate the isometric while schedules stay fixed. Resolve
        // its caption against that finished furniture, not the earlier frame.
        crate::iso_caption::refresh(analysis, &mut plan);
        Ok(plan)
    })();
    *steps += RUNS.with(Cell::get) - before;
    result
}

fn allocate_inner(
    analysis: &Analysis,
    spec: &Spec,
    required: Option<&Plan>,
) -> Result<Plan, Invalid> {
    if spec.sheets.is_none()
        && spec.views.values().all(|v| v.sheet.is_none())
        && spec.tables.iter().all(|t| t.sheet.is_none())
    {
        return run(analysis, spec);
    }
    let migrated = model::migrate(spec, analysis)?;
    let spec = &migrated;
    validate(spec)?;
    let assigned = assignments(spec);
    let mut moved = BTreeMap::new();
    for group in groups(spec) {
        if let Some(n) = group
            .iter()
            .filter_map(|s| assigned.get(s))
            .copied()
            .find(|n| *n > 1)
        {
            for name in group {
                moved.insert(name, n);
            }
        }
    }
    if moved.is_empty() && crate::placement_intent::has_pins(spec) {
        // Pins are measured on the unpinned drawing. Start from the views its own
        // allocation moved, so the pinned sheet 1 keeps that drawing's frames.
        for (name, n) in pinned_allocation(analysis, spec)? {
            moved.insert(name, n);
        }
    }
    let mut first = run(analysis, &working(analysis, spec, &moved))?;
    if !first.sheets.is_empty() && moved.is_empty() {
        return Ok(first);
    }
    let long = first.table_outcome == hole_table::TableOutcome::NoPlace;
    let possible = candidates(analysis, spec, long);
    let mut tried = Vec::new();
    let mut blocked = false;
    let mut best = None;
    let mut previous = first.clone();
    for step in 0..=possible.len() {
        if step > 0 {
            if maximum(spec) == 1 {
                blocked = true;
                break;
            }
            for name in &possible[step - 1] {
                moved.insert(name.clone(), 0);
            }
            // Preflight the cumulative prefix before paying for another recovered sheet 1.
            let mut trial = previous.clone();
            if !pack(analysis, spec, &moved, &mut trial) {
                tried.push(format!("step {step}: blocked by sheets.max"));
                blocked = true;
                continue;
            }
            let mut copy = working(analysis, spec, &moved);
            if moved.contains_key("hole_table") {
                // Invariant: Allocation initialized the multi-sheet Spec before copying it.
                copy.sheets.as_mut().unwrap().hole_table = Some(2);
            }
            first = run(analysis, &copy)?;
        }
        let mut p = first.clone();
        if !pack_with_overflow(analysis, spec, &moved, &mut p, step == 0) {
            tried.push(format!("step {step}: blocked by sheets.max"));
            blocked = true;
            continue;
        }
        previous = p.clone();
        // A sheet of nothing but the isometric is a last resort: the isometric goes back
        // beside the views it explains when the finished sheet 1 has room for it, and
        // otherwise a later prefix may give it a table to share its sheet with.
        let rehomed = rehome_isometric(analysis, spec, &mut p);
        let (mut f, dropped) = score(analysis, spec, &p);
        // A practical-paper rescue must pass the whole-document gate before an early
        // clean prefix can stop the search. Later prefixes may restore a seed measurement
        // or relieve a page whose packed estimate passed but whose actual ink did not.
        if required.is_some_and(|seed| !small_content_fits(analysis, seed, &p)) {
            f.hard += 1;
        }
        let hard = f.hard;
        let pictorial = crate::sheet_policy::pictorial_only_sheet(spec, &p);
        tried.push(format!(
            "step {step}: {} hard findings, {dropped} required drops, scale {}, moved {}{}",
            hard,
            p.sheet.scale,
            moved.keys().cloned().collect::<Vec<_>>().join(", "),
            if pictorial {
                ", a sheet with only the isometric"
            } else if rehomed {
                ", then the isometric returned to sheet 1"
            } else {
                ""
            }
        ));
        let rank = (
            hard,
            dropped,
            pictorial,
            -p.sheet.scale_factor,
            p.sheets.len(),
            step,
        );
        if best.as_ref().is_none_or(|(r, _)| rank < *r) {
            best = Some((rank, p));
        }
        if hard == 0 && dropped == 0 && !pictorial {
            break;
        }
    }
    // Invariant: Step zero is inserted as the seed candidate before trying reallocations.
    let (_, mut p) = best.expect("step zero was packed");
    if maximum(spec) > 1 || !moved.is_empty() {
        p.sheet.basis += &format!("; sheet allocation: {}", tried.join("; "));
    }
    // Working omissions and table assignments are allocation mechanics, never author intent.
    let binding = p.spec.binding.clone();
    p.spec = spec.clone();
    p.spec.schema = model::SPEC_SCHEMA.into();
    p.spec.binding = binding;
    for table in &mut p.tables {
        if let Some((i, authored)) = spec
            .tables
            .iter()
            .enumerate()
            .find(|(_, t)| t.id == table.id)
        {
            table.basis = format!(
                "authored: the spec's table {} (tables/{i}), towards the sheet's {} corner as draftwright places a declared table",
                table.id,
                authored.prefer.as_deref().unwrap_or("tr")
            );
        }
    }
    if spec.sheets.as_ref().and_then(|s| s.hole_table).is_some() && p.hole_table.is_none() {
        p.problems.push(problem(
            "sheet_assignment_unused",
            "info",
            "sheets/hole_table",
            "No rescue hole table was made; its sheet assignment is unused.".into(),
            vec![Op::remove("sheets/hole_table")],
        ));
    }
    crate::title_block::number_sheets(&mut p);
    p.problems.extend(check_with_search(
        &p,
        blocked || (maximum(spec) == 1 && !possible.is_empty()),
    ));
    debug_assert!(
        p.dimensions
            .iter()
            .map(|d| &d.view)
            .chain(p.gdt.iter().map(|g| &g.view))
            .all(|id| p
                .views
                .iter()
                .find(|v| &v.id == id)
                .is_none_or(|v| v.sheet == 1)),
        "Stage I carriers must stay on sheet 1"
    );
    p.table_restore = None;
    p.status = crate::completeness::status(&p.problems);
    Ok(p)
}

/// A pinned request's unpinned reference views. Omitted views stay omitted. On a
/// multi-sheet request a view assigned to a later sheet (an allocation step's moved
/// view) is left to the reference's own allocation, so the reference is the whole
/// unpinned drawing with every frame where it finally sits, not one step's sheet 1.
pub(crate) fn reference_views(spec: &Spec) -> crate::sections::Views {
    let multi = maximum(spec) > 1;
    let mut views: crate::sections::Views = spec
        .views
        .iter()
        .filter(|(_, view)| view.omit && !(multi && view.sheet.is_some_and(|n| n > 1)))
        .map(|(name, _)| {
            let view = model::ViewSpec {
                omit: true,
                ..Default::default()
            };
            (name.clone(), view)
        })
        .collect::<BTreeMap<_, _>>()
        .into();
    views.keep_sections_from(&spec.views);
    views.keep_breaks_from(&spec.views);
    views
}

/// The views (and rescue hole table) the unpinned request's own allocation puts after
/// sheet 1. Pins never move frames, so the pinned replay starts from the same allocation.
fn pinned_allocation(analysis: &Analysis, spec: &Spec) -> Result<BTreeMap<String, u32>, Invalid> {
    let mut base = model::pin_base(spec);
    base.sheets = spec.sheets.clone();
    base.views = reference_views(spec);
    let canonical = model::plan_automatic(analysis, &base)?;
    let mut moved: BTreeMap<String, u32> = canonical
        .views
        .iter()
        .filter(|v| v.sheet > 1)
        .map(|v| (v.id.trim_start_matches("v:").to_string(), v.sheet))
        .collect();
    if let Some(table) = canonical.hole_table.as_ref().filter(|t| t.sheet > 1) {
        moved.insert("hole_table".into(), table.sheet);
    }
    Ok(moved)
}

/// Relieve a small part on practical paper before opening the large-sheet ladder.
/// Named paper prevents re-entry into this fallback; allocation uses R5's normal prefixes,
/// keep-together groups, packing and cross-references. Working controls never become intent.
pub(crate) fn small_fallback(
    analysis: &Analysis,
    spec: &Spec,
    required: &Plan,
    tried: &mut Vec<String>,
) -> Option<Plan> {
    if spec.sheet.size.is_some() {
        return None;
    }
    if spec.sheets.as_ref().and_then(|s| s.max) == Some(1) {
        tried.push("small-sheet allocation blocked by explicit sheets.max = 1".into());
        return None;
    }
    for (name, _, _) in layout::PAGES.iter().take(2) {
        let mut working = spec.clone();
        working.sheet.size = Some((*name).into());
        working.sheets.get_or_insert_with(Default::default).max =
            Some(spec.sheets.as_ref().and_then(|s| s.max).unwrap_or(9));
        let mut p = match allocate_inner(analysis, &working, Some(required)) {
            Ok(plan) => plan,
            Err(error) => {
                tried.push(format!("{name} multi-sheet: invalid ({})", error.message));
                continue;
            }
        };
        crate::iso_caption::refresh(analysis, &mut p);
        let (mut f, dropped) = score(analysis, &working, &p);
        f.hard = f.hard.max(model::findings(&p).hard);
        let complete = model::carries_required(required, &p);
        let fits = model::drawn_sheets_fit(analysis, &p);
        let over = model::over_views(&p) > model::over_views(required);
        let clean = f.hard == 0 && dropped == 0 && small_content_fits(analysis, required, &p);
        let blockers: BTreeSet<_> = p
            .problems
            .iter()
            .filter(|q| blocks_small_plan(q))
            .map(|q| q.code.as_str())
            .collect();
        tried.push(format!(
            "{name} multi-sheet: {} sheets at {}, {} hard findings, {dropped} required drops, required measurements retained {complete}, drawn bounds fit {fits}, additional view conflicts {over}, blocking problems [{}]",
            1 + p.sheets.len(), p.sheet.scale, f.hard, blockers.into_iter().collect::<Vec<_>>().join(", ")
        ));
        if clean {
            let binding = p.spec.binding.clone();
            p.spec = spec.clone();
            p.spec.schema = model::SPEC_SCHEMA.into();
            p.spec.binding = binding;
            p.sheet.default = true;
            for sheet in &mut p.sheets {
                sheet.default = true;
            }
            p.sheet.basis = format!(
                "small part: practical-paper R5 allocation before A2 and larger; {}",
                tried.join("; ")
            );
            return Some(p);
        }
    }
    None
}

/// The supplement search uses the recovery's actual-ink and measurement-retention gates.
fn small_content_fits(analysis: &Analysis, required: &Plan, plan: &Plan) -> bool {
    model::findings(plan).clean()
        // An authored small scale may already have under-floor steps. A rescue
        // must not add any, but preserving those steps cannot force larger paper.
        && crate::sheet_fit::illegible_steps(analysis, plan)
            <= crate::sheet_fit::illegible_steps(analysis, required)
        && model::carries_required(required, plan)
        && model::drawn_sheets_fit(analysis, plan)
        && model::over_views(plan) <= model::over_views(required)
        && !plan.problems.iter().any(blocks_small_plan)
}

fn blocks_small_plan(problem: &Problem) -> bool {
    problem.severity == "error"
        || matches!(
            problem.code.as_str(),
            "cross_reference_missing" | "sheet_below_smallest_scale"
        )
}

fn score(analysis: &Analysis, spec: &Spec, plan: &Plan) -> (layout::Findings, usize) {
    // The search relieves sheet 1. An unplaceable assigned corner table on another sheet
    // cannot justify moving unrelated content off an otherwise clean first sheet.
    let mut first = plan.clone();
    let off: BTreeSet<_> = crate::items::items(plan)
        .into_iter()
        .filter(|i| i.sheet > 1)
        .map(|i| i.id)
        .collect();
    first
        .problems
        .retain(|p| p.subjects.is_empty() || p.subjects.iter().any(|id| !off.contains(id)));
    let mut f = model::findings(&first);
    if spec.sheet.scale.is_none() {
        f.hard += crate::sheet_fit::illegible_steps(analysis, plan);
    }
    let requested = floor(spec);
    let legible = crate::sheet_fit::min_legible_scale(analysis);
    let low_parent = spec.sheet.scale.is_none()
        && plan.sheet.scale_factor < requested.unwrap_or(0.).max(legible);
    let low_view =
        requested.is_some_and(|floor| plan.views.iter().any(|v| v.sheet == 1 && v.scale < floor));
    if (low_parent || low_view) && !requested.is_some_and(|floor| plan.sheet.scale_factor < floor) {
        f.hard += 1;
    }
    let dropped = f.dropped + model::undrawn_gdt(plan);
    (f, dropped)
}

/// A moved section still marks its parent during sheet-1 placement. A temporary carrier
/// supplies the cutting plane to the existing furniture planner without reserving view room.
pub(crate) fn line_work(analysis: &Analysis, spec: &Spec, views: &mut Vec<PlannedView>) {
    let moved = spec
        .views
        .get("section_aa")
        .is_some_and(|v| v.omit && v.sheet.is_some_and(|n| n > 1));
    let marker = moved
        .then(|| auxiliary(analysis, "section_aa", 1., 2, 1.))
        .flatten();
    if let Some(marker) = marker.as_ref() {
        views.push(marker.clone());
    }
    crate::linework::configure(spec, views);
    crate::centrelines::plan(analysis, views);
    if marker.is_some() {
        views.pop();
    }
}

fn auxiliary(
    analysis: &Analysis,
    name: &str,
    scale: f64,
    sheet: u32,
    parent_scale: f64,
) -> Option<PlannedView> {
    let section = if name == "section_aa" {
        Some(crate::demand::read(analysis).section?)
    } else {
        None
    };
    let direction = if name == "iso" {
        [1., -1., 1.]
    } else {
        [0., -1., 0.]
    };
    let up = [0., 0., 1.];
    // Invariant: Planned cameras come from the fixed views or checked section/detail cameras, with nonparallel axes.
    let view = crate::haecceity::hlr::View::new(direction, up).expect("auxiliary view");
    let (extent, _) = model::extent(analysis, &view);
    let bounds = if name == "iso" {
        if analysis.drawing_iso_bounds() == [0.; 4] {
            return None;
        }
        analysis.drawing_iso_bounds()
    } else {
        [
            -extent[0] / 2.,
            -extent[1] / 2.,
            extent[0] / 2.,
            extent[1] / 2.,
        ]
    };
    let size = [bounds[2] - bounds[0], bounds[3] - bounds[1]];
    let label = if section.is_some() { 19. } else { 7. };
    let mut planned = PlannedView {
        detail: None,
        id: format!("v:{name}"),
        sheet,
        scale_note: (name != "iso" && scale != parent_scale)
            .then(|| format!("SCALE {}", model::scale_name(scale))),
        xref: None,
        direction,
        up,
        at: [-bounds[0] * scale, -bounds[1] * scale + label],
        scale,
        area: [0., 0., size[0] * scale, size[1] * scale + label],
        default: true,
        basis: "auxiliary view moved as a whole after sheet-1 paper recovery".into(),
        state: State::Shown,
        line_work: Vec::new(),
        line_style: Default::default(),
        from_spec: Vec::new(),
        half_section: None,
        broken: None,
        cut: section.map(|s| {
            let [lo, hi] = analysis.envelope;
            model::Cut {
                label: "A".into(),
                origin: [(lo[0] + hi[0]) / 2., s.cut_y, (lo[2] + hi[2]) / 2.],
                normal: [0., 1., 0.],
                marked_in: "v:plan".into(),
                xref: None,
            }
        }),
    };
    crate::centrelines::plan(analysis, std::slice::from_mut(&mut planned));
    for curve in planned.line_work.iter().flat_map(|w| &w.curves) {
        let b = curve.bbox();
        planned.area = [
            planned.area[0].min(b[0]),
            planned.area[1].min(b[1]),
            planned.area[2].max(b[2]),
            planned.area[3].max(b[3]),
        ];
    }
    // A narrow model does not narrow its caption/reference text. Reserve the longest
    // zone address on the supported paper grid; I2 draws it at this same caption centre.
    let text = if planned.cut.is_some() {
        "FROM SHEET 1, ZONE R24"
    } else if name == "iso" && scale != parent_scale {
        model::NTS_CAPTION
    } else {
        planned.scale_note.as_deref().unwrap_or("")
    };
    if !text.is_empty() {
        let half = crate::dims::font().advance(text, crate::dims::TEXT) / 2.;
        planned.area[0] = planned.area[0].min(planned.at[0] - half);
        planned.area[2] = planned.area[2].max(planned.at[0] + half);
    }
    planned.area[0] -= crate::tables::PAD;
    planned.area[1] -= crate::tables::PAD;
    planned.area[2] += crate::tables::PAD;
    planned.area[3] += crate::tables::PAD;
    Some(planned)
}

fn extra_sheet(first: &PlannedSheet, number: u32) -> PlannedSheet {
    let mut sheet = first.clone();
    sheet.number = number;
    sheet.fill = 0.;
    sheet.arrangement = "rows".into();
    sheet.basis = "same paper as sheet 1; assigned tables then auxiliary view blocks".into();
    sheet.projection_symbol = None;
    sheet
}

/// A freely fitted isometric alone on the last sheet goes back to sheet 1 when that sheet's
/// finished ink leaves room for it: at its parent scale, shrunk no further than the
/// single-sheet layout allows ([`layout::ISO_MIN_FIT_FRAC`]), centred in the largest clear
/// rectangle as the single-sheet layout centres it. The emptied sheet is removed.
fn rehome_isometric(analysis: &Analysis, spec: &Spec, plan: &mut Plan) -> bool {
    let Some(last) = plan.sheets.last().map(|s| s.number) else {
        return false;
    };
    let on_last: Vec<_> = plan.views.iter().filter(|v| v.sheet == last).collect();
    if !crate::sheet_policy::pictorial_only_sheet(spec, plan)
        || on_last.len() != 1
        || on_last[0].id != "v:iso"
        || plan
            .hole_table
            .iter()
            .chain(&plan.hole_tables)
            .any(|t| t.sheet == last)
        || plan.tables.iter().any(|t| t.sheet == last)
    {
        return false;
    }
    let mut first = plan.clone();
    first.sheets.clear();
    first.views.retain(|v| v.sheet == 1);
    let carried: BTreeSet<_> = first.views.iter().map(|v| v.id.clone()).collect();
    first.dimensions.retain(|d| carried.contains(&d.view));
    first.gdt.retain(|g| carried.contains(&g.view));
    first.balloons.retain(|b| carried.contains(&b.view));
    first.captions.retain(|c| carried.contains(&c.view));
    first.tables.retain(|t| t.sheet == 1);
    first.hole_table = first.hole_table.filter(|t| t.sheet == 1);
    first.hole_tables.retain(|t| t.sheet == 1);
    let mut obstacles = crate::sheet_fit::footprints(analysis, &first);
    obstacles.extend(crate::sheet_fit::view_ink(analysis, &first));
    obstacles.extend(crate::sheet_fit::framed_footprints(analysis, &first));
    obstacles.extend(first.views.iter().map(|v| v.area));
    obstacles.push(first.sheet.title_block);
    let gutter = layout::VIEW_GUTTER;
    for b in &mut obstacles {
        *b = [b[0] - gutter, b[1] - gutter, b[2] + gutter, b[3] + gutter];
    }
    let page = first.sheet.page;
    let region = [
        layout::margin() + 2.,
        layout::margin() + 2.,
        page[0] - layout::margin() - 2.,
        page[1] - layout::margin() - 2.,
    ];
    let room = layout::largest_empty_rect(region, &obstacles, [1., 1.]);
    if obstacles.iter().any(|o| overlap(room, *o)) {
        return false;
    }
    let parent = plan.sheet.scale_factor;
    let fractions: Vec<f64> = if let Some(f) = spec.iso_scale_factor {
        vec![f]
    } else {
        (0..)
            .map(|k| 1. - 0.1 * f64::from(k))
            .take_while(|f| *f + 1e-9 >= layout::ISO_MIN_FIT_FRAC)
            .collect()
    };
    for fraction in fractions {
        let Some(mut iso) = auxiliary(analysis, "iso", fraction * parent, 1, parent) else {
            return false;
        };
        let size = [iso.area[2] - iso.area[0], iso.area[3] - iso.area[1]];
        if size[0] > room[2] - room[0] || size[1] > room[3] - room[1] {
            continue;
        }
        let delta = [
            (room[0] + room[2] - size[0]) / 2. - iso.area[0],
            (room[1] + room[3] - size[1]) / 2. - iso.area[1],
        ];
        shift(&mut iso, delta);
        iso.basis = "isometric returned beside the views it explains, centred in the largest clear rectangle of sheet 1".into();
        iso.default = !spec.views.contains_key("iso");
        for v in plan.views.iter_mut().filter(|v| v.id == "v:iso") {
            *v = iso.clone();
        }
        plan.captions.retain(|c| c.view != "v:iso");
        plan.sheets.pop();
        return true;
    }
    false
}

/// Pack assigned groups first, then fill the remaining sheets from 2 upwards. A group is atomic.
fn pack(analysis: &Analysis, spec: &Spec, moved: &BTreeMap<String, u32>, plan: &mut Plan) -> bool {
    pack_with_overflow(analysis, spec, moved, plan, false)
}

fn pack_with_overflow(
    analysis: &Analysis,
    spec: &Spec,
    moved: &BTreeMap<String, u32>,
    plan: &mut Plan,
    allow_overflow: bool,
) -> bool {
    plan.sheets.clear();
    plan.views
        .retain(|v| v.sheet == 1 && !moved.contains_key(v.id.trim_start_matches("v:")));
    plan.tables.retain(|t| t.sheet == 1);
    let max = maximum(spec);
    let mut sheets: BTreeMap<u32, PlannedSheet> = BTreeMap::new();
    let mut blocks: BTreeMap<u32, Vec<PlannedView>> = BTreeMap::new();
    let mut exclusive = BTreeSet::new();
    let mut problems = Vec::new();
    for n in 2..=max {
        let tables: Vec<_> = spec
            .tables
            .iter()
            .filter(|t| t.sheet == Some(n))
            .cloned()
            .collect();
        if tables.is_empty() {
            continue;
        }
        let sheet = extra_sheet(&plan.sheet, n);
        let (items, findings) =
            crate::tables::plan(&tables, analysis, &sheet, &[], &[], None, spec);
        plan.tables.extend(items);
        problems.extend(findings);
        sheets.insert(n, sheet);
    }
    let mut reserved_table = None;
    let table_sheet = |sheets: &BTreeMap<u32, PlannedSheet>| {
        spec.sheets
            .as_ref()
            .and_then(|s| s.hole_table)
            .filter(|n| *n > 1)
            .or_else(|| moved.get("hole_table").copied().filter(|n| *n > 1))
            .or_else(|| {
                (2..=max).find(|n| !sheets.contains_key(n) && !moved.values().any(|s| s == n))
            })
    };
    // A preflight still has the prior NoPlace outcome, with the table withdrawn.
    // Reserve its exclusive sheet before testing the views; the real run escalates Some(n).
    if moved.contains_key("hole_table")
        && plan.table_outcome == hole_table::TableOutcome::NoPlace
        && plan.hole_table.is_none()
    {
        let Some(n) = table_sheet(&sheets) else {
            return false;
        };
        exclusive.insert(n);
        reserved_table = Some(n);
        sheets.insert(n, extra_sheet(&plan.sheet, n));
    }
    if let Some(mut table) = plan.hole_table.clone().filter(|t| t.sheet > 1) {
        let Some(n) = table_sheet(&sheets) else {
            return false;
        };
        let sheet = extra_sheet(&plan.sheet, n);
        let parts = hole_table::place_on_sheet(&table, &sheet, spec);
        if let Some(parts) = parts {
            table.blocks = parts;
            table.sheet = n;
            table.basis = format!(
                "drop and escalate: every row is tagged on sheet 1; the table is on sheet {n}, towards the top right in {} blocks",
                table.blocks.len()
            );
            plan.hole_table = Some(table);
            exclusive.insert(n);
            sheets.insert(n, sheet);
        } else {
            hole_table::withdraw_no_place(analysis, plan);
        }
    }
    let mut bundles: Vec<_> = groups(spec)
        .into_iter()
        .filter(|g| g.iter().any(|n| moved.contains_key(n) && n != "hole_table"))
        .collect();
    bundles.sort_by_key(|g| {
        let assigned = g
            .iter()
            .filter_map(|n| moved.get(n))
            .copied()
            .find(|n| *n > 1);
        (
            assigned.is_none(),
            assigned.unwrap_or(0),
            if g.iter().any(|n| n == "iso") { 0 } else { 1 },
        )
    });
    for group in bundles {
        let names: Vec<_> = group
            .iter()
            .filter(|n| *n != "hole_table" && moved.contains_key(*n) && shown(spec, n))
            .collect();
        let with_table = group.iter().any(|n| n == "hole_table")
            && (plan.hole_table.as_ref().is_some_and(|t| t.sheet > 1) || reserved_table.is_some());
        let assigned = if with_table {
            plan.hole_table.as_ref().map(|t| t.sheet).or(reserved_table)
        } else {
            names
                .iter()
                .filter_map(|n| moved.get(*n))
                .copied()
                .find(|n| *n > 1)
        };
        if names.is_empty() {
            continue;
        }
        let targets: Vec<_> = assigned.map_or_else(
            || (2..=max).filter(|n| !exclusive.contains(n)).collect(),
            |n| vec![n],
        );
        // Full parent scale on occupied sheets, then a fresh sheet, then shrink on a fresh sheet.
        let mut found = None;
        for shrink in [false, true] {
            for &n in &targets {
                if (exclusive.contains(&n) && !with_table)
                    || (shrink && blocks.get(&n).is_some_and(|b| !b.is_empty()))
                {
                    continue;
                }
                let sheet = sheets
                    .get(&n)
                    .cloned()
                    .unwrap_or_else(|| extra_sheet(&plan.sheet, n));
                let existing = blocks.get(&n).cloned().unwrap_or_default();
                let mut obstacles: Vec<_> = plan
                    .tables
                    .iter()
                    .filter(|t| t.sheet == n)
                    .filter_map(|t| t.placed.as_ref().map(|p| p.cells))
                    .collect();
                obstacles.extend(
                    plan.hole_table
                        .iter()
                        .filter(|t| t.sheet == n)
                        .flat_map(|t| {
                            t.blocks
                                .iter()
                                .filter_map(|b| b.placed.as_ref().map(|p| p.cells))
                        }),
                );
                let scales: Vec<_> = if shrink {
                    layout::SCALES
                        .into_iter()
                        .filter(|s| *s < plan.sheet.scale_factor)
                        .collect()
                } else {
                    vec![plan.sheet.scale_factor]
                };
                for scale in scales {
                    let mut trial = existing.clone();
                    trial.extend(names.iter().filter_map(|name| {
                        auxiliary(
                            analysis,
                            name,
                            if *name == "iso" {
                                spec.iso_scale_factor
                                    .map_or(scale, |f| f * plan.sheet.scale_factor)
                            } else {
                                scale
                            },
                            n,
                            plan.sheet.scale_factor,
                        )
                    }));
                    if trial.len() == existing.len() {
                        found = Some((n, sheet.clone(), trial));
                        break;
                    }
                    let relative = names
                        .iter()
                        .any(|name| spec.views.get(*name).is_some_and(|v| v.place.is_some()));
                    let fits = if relative {
                        let mut next = trial.split_off(existing.len());
                        relate(spec, &mut next);
                        let mut carrier = next[0].clone();
                        carrier.area = next.iter().fold(
                            [
                                f64::INFINITY,
                                f64::INFINITY,
                                f64::NEG_INFINITY,
                                f64::NEG_INFINITY,
                            ],
                            |b, v| {
                                [
                                    b[0].min(v.area[0]),
                                    b[1].min(v.area[1]),
                                    b[2].max(v.area[2]),
                                    b[3].max(v.area[3]),
                                ]
                            },
                        );
                        let old = carrier.area;
                        let mut fixed = obstacles.clone();
                        fixed.extend(existing.iter().map(|v| v.area));
                        let fits =
                            layout::plan_sheet(std::slice::from_mut(&mut carrier), &fixed, &sheet);
                        for v in &mut next {
                            shift(v, [carrier.area[0] - old[0], carrier.area[1] - old[1]]);
                        }
                        trial.extend(next);
                        fits
                    } else {
                        layout::plan_sheet(&mut trial, &obstacles, &sheet)
                    };
                    if fits {
                        for v in trial.iter_mut().skip(existing.len()) {
                            v.default = !spec.views.contains_key(v.id.trim_start_matches("v:"));
                        }
                        found = Some((n, sheet.clone(), trial));
                        break;
                    }
                }
                if found.is_some() {
                    break;
                }
            }
            if found.is_some() {
                break;
            }
        }
        if found.is_none()
            && allow_overflow
            && let Some(n) = assigned
        {
            let sheet = sheets
                .get(&n)
                .cloned()
                .unwrap_or_else(|| extra_sheet(&plan.sheet, n));
            let mut views = blocks.get(&n).cloned().unwrap_or_default();
            for name in &names {
                if let Some(mut view) = auxiliary(
                    analysis,
                    name,
                    if *name == "iso" {
                        spec.iso_scale_factor
                            .map_or(layout::SCALES[15], |f| f * plan.sheet.scale_factor)
                    } else {
                        layout::SCALES[15]
                    },
                    n,
                    plan.sheet.scale_factor,
                ) {
                    let top = views
                        .last()
                        .map_or(sheet.page[1] - layout::margin(), |v| v.area[1] - 10.);
                    let delta = [layout::margin() - view.area[0], top - view.area[3]];
                    shift(&mut view, delta);
                    views.push(view);
                }
            }
            // Keep assigned ink even when no standard scale fits. The finished-sheet audit
            // reports overflow; the floor check offers a corrective intent.
            found = Some((n, sheet, views));
        }
        let Some((n, sheet, views)) = found else {
            return false;
        };
        if !views.is_empty() {
            sheets.insert(n, sheet);
            blocks.insert(n, views);
        }
    }
    let mapping: BTreeMap<_, _> = sheets
        .keys()
        .enumerate()
        .map(|(i, &n)| (n, i as u32 + 2))
        .collect();
    for (old, mut sheet) in sheets {
        let n = mapping[&old];
        sheet.number = n;
        plan.sheets.push(sheet);
        for mut view in blocks.remove(&old).unwrap_or_default() {
            view.sheet = n;
            if spec
                .views
                .get(view.id.trim_start_matches("v:"))
                .and_then(|v| v.sheet)
                .is_some_and(|s| s != n)
            {
                problems.push(problem(
                    "sheet_renumbered",
                    "info",
                    &view.id,
                    format!(
                        "Empty sheets closed: {} is on sheet {n}.",
                        crate::wording::view(&view.id)
                    ),
                    vec![Op::set(
                        format!("views/{}/sheet", view.id.trim_start_matches("v:")),
                        json!(n),
                    )],
                ));
            }
            plan.views.push(view);
        }
        for table in plan.tables.iter_mut().filter(|t| t.sheet == old) {
            table.sheet = n;
            if old != n {
                // Invariant: Planned authored tables were emitted from this same spec.tables list.
                let i = spec.tables.iter().position(|t| t.id == table.id).unwrap();
                problems.push(problem(
                    "sheet_renumbered",
                    "info",
                    &table.id,
                    format!(
                        "Empty sheets closed: {} is on sheet {n}.",
                        crate::wording::block(&table.id)
                    ),
                    vec![Op::set(format!("tables/{i}/sheet"), json!(n))],
                ));
            }
        }
        if let Some(t) = plan.hole_table.as_mut().filter(|t| t.sheet == old) {
            t.sheet = n;
            for b in &mut t.blocks {
                b.sheet = n;
            }
            if old != n {
                problems.push(problem(
                    "sheet_renumbered",
                    "info",
                    &t.id,
                    format!("Empty sheets closed: the hole table is on sheet {n}."),
                    vec![Op::set("sheets/hole_table", json!(n))],
                ));
            }
        }
    }
    plan.problems.extend(problems);
    if !plan.sheets.is_empty() {
        plan.sheet.zones = Some(zones(plan.sheet.page));
        for sheet in &mut plan.sheets {
            sheet.zones = Some(zones(sheet.page));
        }
        crate::linework::configure(&plan.spec, &mut plan.views);
        let mut generated = plan.views.clone();
        crate::centrelines::plan(analysis, &mut generated);
        crate::centrelines::unused_origins(&mut generated, &plan.dimensions);
        for view in plan.views.iter_mut().filter(|v| v.sheet > 1) {
            view.line_work = generated
                .iter()
                .find(|v| v.id == view.id)
                // Invariant: The selected moved table is present in the immutable source Spec.
                .unwrap()
                .line_work
                .clone();
        }
        cross_references(analysis, plan);
    }
    true
}

/// The sheet-basis marker of a [`separate_views`] allocation.
pub(crate) const WHOLE_VIEW_ALLOCATION: &str =
    "principal views and auxiliary views allocated as whole annotated blocks";

/// Allocate settled view blocks, including principal projections, without changing
/// their scale, annotation ownership or required-item inventory. The same paper,
/// fit-box allocator, sheet numbering and cross-reference machinery serve R5.
pub(crate) fn separate_views(
    analysis: &Analysis,
    source: &Plan,
    size: &str,
    max: usize,
) -> Option<Plan> {
    separate_views_inner(analysis, source, size, max, None, None)
}

/// A section initially composed to the right of the principal row may be off
/// paper even when its whole annotated block fits an empty corner. Repack owned
/// ink before mistaking that composition failure for a shortage of paper.
pub(crate) fn reflow_outside_auxiliaries(analysis: &Analysis, source: &Plan) -> Option<Plan> {
    if !source.sheets.is_empty() || crate::placement_intent::has_pins(&source.spec) {
        return None;
    }
    let bounds = crate::sheet_fit::footprints(analysis, source);
    if !source.views.iter().zip(&bounds).any(|(v, b)| {
        v.cut.is_some()
            && (b[0] < layout::margin()
                || b[1] < layout::margin()
                || b[2] > source.sheet.page[0] - layout::margin()
                || b[3] > source.sheet.page[1] - layout::margin())
    }) {
        return None;
    }
    let mut source = source.clone();
    // Recover complete line-work before transport; an off-sheet trial's line-work
    // has already been clipped to its old frame.
    crate::centrelines::plan(analysis, &mut source.views);
    crate::centrelines::unused_origins(&mut source.views, &source.dimensions);
    source.views.sort_by_key(|v| v.id == "v:iso");
    // Try moving only auxiliaries first. Their estimated right-hand row is
    // not a required projection relation; principal alignments remain exact.
    let mut relocated = source.clone();
    let movable: Vec<_> = relocated
        .views
        .iter()
        .filter(|v| v.cut.is_some() || v.id == "v:iso")
        .map(|v| v.id.clone())
        .collect();
    let owned = |plan: &Plan, keep: &dyn Fn(&str) -> bool| {
        let mut p = plan.clone();
        p.views.retain(|v| keep(&v.id));
        p.dimensions.retain(|d| keep(&d.view));
        p.gdt.retain(|g| keep(&g.view));
        p.captions.retain(|c| keep(&c.view));
        p.balloons.retain(|b| keep(&b.view));
        p.hole_tables
            .retain(|t| keep(t.view.as_deref().unwrap_or("v:plan")));
        p.tables.clear();
        p.radius_table = None;
        p.requirements.clear();
        p
    };
    let mut fixed = source.clone();
    fixed.views.clear();
    fixed.dimensions.clear();
    fixed.gdt.clear();
    fixed.captions.clear();
    fixed.balloons.clear();
    fixed.hole_tables.clear();
    let mut fixed_ink = crate::sheet_fit::view_ink(analysis, &fixed);
    fixed_ink.extend(crate::sheet_fit::furniture_footprints(analysis, &fixed));
    let region = [
        layout::margin(),
        layout::margin(),
        source.sheet.page[0] - layout::margin(),
        source.sheet.page[1] - layout::margin(),
    ];
    let mut placed = Vec::new();
    let mut complete = true;
    for id in &movable {
        // Per-view schedules need the allocator's coupled transport below.
        if source
            .hole_tables
            .iter()
            .any(|t| t.view.as_deref() == Some(id))
        {
            complete = false;
            break;
        }
        let retained = owned(&relocated, &|v| {
            !movable.iter().any(|m| m == v) || placed.iter().any(|m| m == v)
        });
        let mut obstacles = crate::sheet_fit::view_ink(analysis, &retained);
        obstacles.extend(fixed_ink.iter().copied());
        let own = owned(&relocated, &|v| v == id);
        let Some(bounds) = crate::sheet_fit::view_ink(analysis, &own)
            .into_iter()
            .reduce(|a, b| {
                [
                    a[0].min(b[0]),
                    a[1].min(b[1]),
                    a[2].max(b[2]),
                    a[3].max(b[3]),
                ]
            })
        else {
            complete = false;
            break;
        };
        let Some(at) = layout::fit_box(
            [bounds[2] - bounds[0], bounds[3] - bounds[1]],
            region,
            &obstacles,
            layout::Corner::TopRight,
            2.,
        ) else {
            complete = false;
            break;
        };
        let mut areas: Vec<_> = relocated.views.iter().map(|v| v.area).collect();
        let mut deltas = vec![[0., 0.]; areas.len()];
        let i = relocated.views.iter().position(|v| &v.id == id)?;
        relocated.views[i].area = bounds;
        areas[i] = bounds;
        deltas[i] = [at[0] - bounds[0], at[1] - bounds[1]];
        crate::view_gutters::transport(analysis, &mut relocated, areas, deltas);
        placed.push(id.clone());
    }
    if complete
        && model::finished_items_fit(analysis, &relocated)
        && model::carries_required(&source, &relocated)
    {
        relocated.problems.retain(|p| {
            !matches!(
                p.code.as_str(),
                "page_fit_uncertain" | "annotation_off_sheet" | "annotation_outside_area"
            )
        });
        relocated
            .problems
            .extend(crate::dims::recheck(analysis, &relocated));
        relocated.sheet.basis += "; complete auxiliary ink relocated into vacant same-sheet space";
        relocated.status = crate::completeness::status(&relocated.problems);
        return Some(relocated);
    }
    for tier in 0..=8 {
        if let Some(mut plan) = separate_views_inner(
            analysis,
            &source,
            &source.sheet.size,
            1,
            None,
            Some(TableReflow {
                row_lift: 6. * tier as f64,
                late_tables: false,
            }),
        ) && model::finished_items_fit(analysis, &plan)
            && model::carries_required(&source, &plan)
        {
            plan.problems.retain(|p| p.code != "page_fit_uncertain");
            plan.sheet.basis += "; off-sheet auxiliary row repacked as complete annotated blocks on the same paper and scale";
            plan.status = crate::completeness::status(&plan.problems);
            return Some(plan);
        }
    }
    None
}

/// Pin replay transports the same owned ink into the already selected view
/// frames. Expanded pin ink is audited there, never used to relocate peers.
pub(crate) fn replay_views(analysis: &Analysis, source: &Plan, frame: &Plan) -> Option<Plan> {
    let mut fixed = frame.clone();
    let mut findings = Vec::new();
    // A retained physical page is its own coordinate frame. Settling all
    // pages together can refit an isometric around a principal on another
    // page, changing a bare annotation pin into a view/scale edit.
    if source.spec.views.values().any(|v| v.place.is_some()) {
        for sheet in std::iter::once(&frame.sheet).chain(&frame.sheets) {
            let mut local: Vec<_> = fixed
                .views
                .iter()
                .filter(|v| v.sheet == sheet.number)
                .cloned()
                .collect();
            let mut spec = source.spec.clone();
            spec.views
                .retain(|name, _| local.iter().any(|v| v.id == format!("v:{name}")));
            crate::placement_intent::views(
                &spec,
                &mut local,
                Some(frame),
                sheet,
                &[],
                &mut findings,
            );
            for view in local {
                if let Some(old) = fixed.views.iter_mut().find(|v| v.id == view.id) {
                    *old = view;
                }
            }
        }
    }
    let mut source = source.clone();
    source.problems.retain(|p| {
        !matches!(
            p.code.as_str(),
            "projection_relation_infeasible" | "pinned_view_off_sheet"
        )
    });
    source.problems.extend(findings);
    separate_views_inner(
        analysis,
        &source,
        &frame.sheet.size,
        1 + frame.sheets.len(),
        Some(&fixed),
        None,
    )
}

/// Repack only when existing ruled furniture is taller than the requested page. The
/// settled dimensions, datum symbols, tags and required rows all survive as owned ink.
pub(crate) fn reflow_tall_tables(analysis: &Analysis, source: &Plan, size: &str) -> Option<Plan> {
    let (_, _, height) = layout::PAGES.iter().find(|p| p.0 == size)?;
    let drawable = height - 2.0 * layout::margin();
    let tall = source
        .hole_tables
        .iter()
        .chain(&source.hole_table)
        .any(|t| {
            crate::tables::ROW * (t.rows.len() + if t.view.is_some() { 2 } else { 1 }) as f64
                > drawable
        })
        || source
            .tables
            .iter()
            .chain(source.radius_table.iter().map(|t| &t.table))
            .any(|t| crate::tables::ROW * t.rows.len() as f64 > drawable);
    if !tall
        || !source.sheets.is_empty()
        || source.spec.sheet.projection.as_deref() == Some("first")
        || source.notes.iter().any(|n| {
            source
                .spec
                .annotations
                .get(&n.id)
                .is_some_and(|a| a.place.is_some())
        })
    {
        return None;
    }
    // Paper-policy baselines precede the final attachment pass. Pack the same
    // finished marks a published drawing carries, rather than their provisional
    // long leaders and strips (also how canonical pin replay settles its marks).
    let mut settled = source.clone();
    crate::dims::callout_routes::prefer_interior(analysis, &source.spec, &mut settled, None);
    crate::dims::callout_routes::finish(analysis, &source.spec, &mut settled);
    crate::dims::hole_marks::attach(analysis, &source.spec, &mut settled);
    crate::dims::size_datums::attach(analysis, &source.spec, &mut settled);
    crate::dims::gdt::cited_undrawn(&mut settled);
    let source = &settled;
    let mut forms = vec![source.spec.sheet.table_columns.clone()];
    for table in source.hole_tables.iter().chain(&source.hole_table) {
        if source.spec.sheet.table_columns.contains_key(&table.id) {
            continue;
        }
        forms = forms
            .into_iter()
            .flat_map(|form| {
                (1..=hole_table::MAX_COLUMNS as u32).map(move |n| {
                    let mut form = form.clone();
                    form.insert(table.id.clone(), n);
                    form
                })
            })
            .collect();
    }
    forms.sort_by_key(|c| c.values().sum::<u32>());
    for table_columns in forms {
        let mut trial = source.clone();
        trial.spec.sheet.table_columns = table_columns;
        trial.views.sort_by_key(|v| v.id == "v:iso");
        if trial.hole_tables.iter().chain(&trial.hole_table).any(|t| {
            hole_table::split_sizes(t, &trial.spec)
                .first()
                .is_some_and(|s| s[1] > drawable)
        }) {
            continue;
        }
        let free_iso = source.spec.iso_scale_factor.is_none()
            && !source.dimensions.iter().any(|d| d.view == "v:iso")
            && !source.gdt.iter().any(|g| g.view == "v:iso");
        for late_tables in [false, true] {
            let fractions = if late_tables && free_iso {
                vec![1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4]
            } else {
                vec![1.0]
            };
            for fraction in fractions {
                let mut trial = trial.clone();
                if late_tables {
                    trial.views.sort_by_key(|v| {
                        if ["v:front", "v:plan", "v:side"].contains(&v.id.as_str()) {
                            0
                        } else if v.id == "v:iso" {
                            1
                        } else {
                            2
                        }
                    });
                    if free_iso && let Some(i) = trial.views.iter().position(|v| v.id == "v:iso") {
                        trial.views[i] = auxiliary(
                            analysis,
                            "iso",
                            source.sheet.scale_factor * fraction,
                            1,
                            source.sheet.scale_factor,
                        )?;
                    }
                }
                for tier in 0..=8 {
                    let reflow = TableReflow {
                        row_lift: 6.0 * tier as f64,
                        late_tables,
                    };
                    if let Some(mut p) =
                        separate_views_inner(analysis, &trial, size, 1, None, Some(reflow))
                    {
                        p.spec = source.spec.clone();
                        return Some(p);
                    }
                }
            }
        }
    }
    None
}

fn reflow_table_furniture(plan: &mut Plan, obstacles: &mut Vec<[f64; 4]>) -> Option<()> {
    for t in plan.hole_tables.iter_mut().chain(&mut plan.hole_table) {
        t.blocks = hole_table::reflow_blocks(
            t,
            &plan.sheet,
            obstacles,
            hole_table::columns(&plan.spec, &t.id),
        )?;
        t.sheet = 1;
        obstacles.extend(
            t.blocks
                .iter()
                .filter_map(|b| b.placed.as_ref().map(|p| p.cells)),
        );
    }
    for t in plan
        .tables
        .iter_mut()
        .chain(plan.radius_table.iter_mut().map(|t| &mut t.table))
    {
        let corner = plan
            .spec
            .tables
            .iter()
            .find(|given| given.id == t.id)
            .map_or(layout::Corner::TopRight, |given| given.corner().1);
        let placed = crate::tables::place_split(
            &t.rows,
            &plan.sheet,
            obstacles,
            corner,
            hole_table::columns(&plan.spec, &t.id),
        )?;
        obstacles.push(placed.cells);
        t.placed = Some(placed);
    }
    // Notes remain on this page, but give required table rows their first clear place.
    for block in plan
        .notes_block
        .iter_mut()
        .chain(&mut plan.radius_notes_block)
    {
        let old = block.cells;
        let size = [old[2] - old[0], old[3] - old[1]];
        let at = crate::tables::place(size, &plan.sheet, obstacles, layout::Corner::TopRight)?;
        let delta = [at[0] - old[0], at[1] - old[1]];
        crate::placement_intent::move_box(&mut block.cells, delta);
        crate::placement_intent::move_point(&mut block.title_at, delta);
        for note in &mut plan.notes {
            if let Some(p) = &mut note.placed
                && p.cells[0] >= old[0] - 1e-9
                && p.cells[1] >= old[1] - 1e-9
                && p.cells[2] <= old[2] + 1e-9
                && p.cells[3] <= old[3] + 1e-9
            {
                crate::placement_intent::move_box(&mut p.cells, delta);
                for at in &mut p.rows_at {
                    crate::placement_intent::move_point(at, delta);
                }
            }
        }
        obstacles.push(block.cells);
    }
    Some(())
}

#[derive(Clone, Copy)]
struct TableReflow {
    row_lift: f64,
    late_tables: bool,
}

fn separate_views_inner(
    analysis: &Analysis,
    source: &Plan,
    size: &str,
    max: usize,
    frame: Option<&Plan>,
    table_reflow: Option<TableReflow>,
) -> Option<Plan> {
    let reflow = table_reflow.is_some();
    let row_lift = table_reflow.map_or(0.0, |r| r.row_lift);
    let late_tables = table_reflow.is_some_and(|r| r.late_tables);
    use crate::placement_intent::{move_box, move_point};
    if (frame.is_none() && source.spec.views.values().any(|v| v.place.is_some()))
        || source.spec.sheets.as_ref().is_some_and(|s| {
            !s.keep_together.is_empty() || s.gdt_on_sheet_1 || s.hole_table.is_some()
        })
        || source.spec.views.values().any(|v| v.sheet.is_some())
        || source.spec.tables.iter().any(|t| t.sheet.is_some())
        || source.views.iter().any(|v| v.detail.is_some())
        || source.views.len() < 2
    {
        return None;
    }
    // R5 already treats the orientation view as freely fitted. Its normal
    // parent-scale auxiliary avoids carrying a large-paper 1.3x enlargement
    // into a small-paper allocation; dimensioned principal views keep scale.
    let mut adjusted = source.clone();
    if source.spec.iso_scale_factor.is_none()
        && !source.dimensions.iter().any(|d| d.view == "v:iso")
        && !source.gdt.iter().any(|g| g.view == "v:iso")
        && let Some(i) = source
            .views
            .iter()
            .position(|v| v.id == "v:iso" && v.scale > source.sheet.scale_factor)
        && let Some(iso) = auxiliary(
            analysis,
            "iso",
            source.sheet.scale_factor,
            1,
            source.sheet.scale_factor,
        )
    {
        adjusted.views[i] = iso;
        adjusted.captions.retain(|c| c.view != "v:iso");
    }
    if let Some(target) = frame.and_then(|p| p.views.iter().find(|v| v.id == "v:iso"))
        && source.spec.iso_scale_factor.is_none()
        && !source.dimensions.iter().any(|d| d.view == "v:iso")
        && !source.gdt.iter().any(|g| g.view == "v:iso")
        && let Some(i) = adjusted.views.iter().position(|v| v.id == "v:iso")
    {
        adjusted.views[i] = auxiliary(analysis, "iso", target.scale, 1, source.sheet.scale_factor)?;
        adjusted.captions.retain(|c| c.view != "v:iso");
    }
    let source = &adjusted;
    let (_, w, h) = layout::PAGES.iter().find(|p| p.0 == size)?;
    let mut plan = source.clone();
    let page_delta = [w - source.sheet.page[0], h - source.sheet.page[1]];
    plan.sheet.size = size.into();
    plan.sheet.page = [*w, *h];
    let width = layout::title_block_width(*w);
    plan.sheet.title_block[0] = w - layout::TB_CLEAR - width;
    plan.sheet.title_block[2] = w - layout::TB_CLEAR;
    let title_delta = [
        plan.sheet.title_block[0] - source.sheet.title_block[0],
        plan.sheet.title_block[1] - source.sheet.title_block[1],
    ];
    plan.sheet.title_cells =
        crate::title_block::cells(plan.sheet.title_block, &plan.sheet.title, size);
    plan.sheet.projection_symbol = Some(crate::title_block::projection(
        plan.sheet.title_block,
        plan.spec.sheet.projection.as_deref() == Some("first"),
    ));
    plan.sheet.zones = Some(zones(plan.sheet.page));
    plan.sheets.clear();
    // Free document furniture stays on sheet 1, anchored to its original corner.
    for n in plan
        .notes_block
        .iter_mut()
        .chain(&mut plan.radius_notes_block)
    {
        move_box(&mut n.cells, page_delta);
        move_point(&mut n.title_at, page_delta);
    }
    for n in &mut plan.notes {
        if let Some(p) = &mut n.placed {
            move_box(&mut p.cells, page_delta);
            for at in &mut p.rows_at {
                move_point(at, page_delta);
            }
        }
    }
    for r in &mut plan.requirements {
        if let Some(p) = &mut r.placed {
            move_box(&mut p.glyph, title_delta);
            move_box(&mut p.label_box, title_delta);
            move_point(&mut p.text_at, title_delta);
            for q in p.strokes.iter_mut().flatten() {
                move_point(q, title_delta);
            }
            for a in &mut p.arcs {
                a[0] += title_delta[0];
                a[1] += title_delta[1];
            }
        }
    }
    fn move_table(t: &mut crate::tables::PlannedTable, delta: [f64; 2]) {
        use crate::placement_intent::{move_box, move_point};
        t.sheet = 1;
        if let Some(p) = &mut t.placed {
            fn shift(p: &mut crate::tables::PlacedTable, delta: [f64; 2]) {
                move_box(&mut p.cells, delta);
                for x in &mut p.columns {
                    *x += delta[0];
                }
                for at in p.cells_at.iter_mut().flatten().flatten() {
                    move_point(at, delta);
                }
                for block in &mut p.blocks {
                    shift(block, delta);
                }
            }
            shift(p, delta);
        }
    }
    for t in &mut plan.tables {
        move_table(t, page_delta);
    }
    if let Some(t) = &mut plan.radius_table {
        move_table(&mut t.table, page_delta);
    }
    // First try to share the document page. If its furniture needs the whole
    // page, principal views start on 2; no note or table is silently withdrawn.
    let mut furniture = plan.clone();
    furniture.views.clear();
    furniture.dimensions.clear();
    furniture.gdt.clear();
    furniture.hole_table = None;
    furniture.hole_tables.clear();
    if reflow {
        furniture.tables.clear();
        furniture.radius_table = None;
        furniture.notes_block = None;
        furniture.radius_notes_block = None;
        furniture.notes.clear();
    }
    furniture.balloons.clear();
    furniture.captions.clear();
    if !crate::sheet_fit::inside_frames(analysis, &furniture) {
        return None;
    }
    let mut occupied = crate::sheet_fit::view_ink(analysis, &furniture);
    occupied.extend(
        furniture
            .notes_block
            .iter()
            .chain(&furniture.radius_notes_block)
            .map(|n| n.cells),
    );
    occupied.extend(
        furniture
            .radius_table
            .iter()
            .filter_map(|t| t.table.placed.as_ref().map(|p| p.cells)),
    );
    occupied.extend(crate::sheet_fit::furniture_footprints(analysis, &furniture));
    occupied.push(plan.sheet.title_block);
    let region = [
        layout::margin() + 2.,
        layout::margin() + 2.,
        w - layout::margin() - 2.,
        h - layout::margin() - 2.,
    ];
    let mut pages = vec![occupied];
    let principal = |id: &str| ["v:front", "v:plan", "v:side"].contains(&id);
    let mut primary_frames = BTreeMap::<usize, ([f64; 2], Vec<[f64; 4]>)>::new();
    let owned = |id: &str| {
        let mut own = source.clone();
        own.views.retain(|v| v.id == id);
        own.dimensions.retain(|d| d.view == id);
        own.gdt.retain(|g| g.view == id);
        own.balloons.retain(|b| b.view == id);
        own.captions.retain(|c| c.view == id);
        own.tables.clear();
        own.hole_table = own
            .hole_table
            .filter(|t| t.view.as_deref().unwrap_or("v:plan") == id);
        own.hole_tables
            .retain(|t| t.view.as_deref().unwrap_or("v:plan") == id);
        own.requirements.clear();
        if reflow {
            own.hole_table = None;
            own.hole_tables.clear();
            if id == "v:iso" {
                own.captions.clear();
            }
        }
        own
    };
    let column_left = if reflow {
        ["v:front", "v:plan"]
            .iter()
            .filter(|id| source.views.iter().any(|v| v.id == **id))
            .flat_map(|id| crate::sheet_fit::view_ink(analysis, &owned(id)))
            .map(|b| b[0])
            .fold(f64::INFINITY, f64::min)
    } else {
        0.0
    };
    let mut tables_placed = !reflow;
    let mut refined = !reflow;
    for i in 0..plan.views.len() {
        let id = plan.views[i].id.clone();
        if !refined && !principal(&id) {
            // Empty corners of annotation bands are usable paper. Reserve each settled
            // component, including arrowheads and centre lines, rather than its view's
            // whole enclosing rectangle when fitting the remaining furniture.
            let areas: Vec<_> = plan
                .views
                .iter()
                .filter(|v| principal(&v.id))
                .map(|v| v.area)
                .collect();
            pages[0].retain(|b| !areas.contains(b));
            let mut settled = plan.clone();
            settled.dimensions =
                crate::placement_intent::held_dimensions(Some(source), &plan.views);
            settled.views.retain(|v| principal(&v.id));
            settled.dimensions.retain(|d| principal(&d.view));
            settled.gdt.retain(|g| principal(&g.view));
            settled.balloons.retain(|b| principal(&b.view));
            settled.captions.retain(|c| principal(&c.view));
            settled.tables.clear();
            settled.hole_tables.clear();
            settled.hole_table = None;
            settled.radius_table = None;
            settled.requirements.clear();
            pages[0].extend(crate::sheet_fit::view_ink(analysis, &settled));
            refined = true;
            if !late_tables {
                reflow_table_furniture(&mut plan, &mut pages[0])?;
                tables_placed = true;
            }
        }
        let own = owned(&id);
        let mut ink = crate::sheet_fit::view_ink(analysis, &own);
        ink.extend(
            crate::sheet_fit::furniture_footprints(analysis, &own)
                .into_iter()
                .skip(1 + usize::from(own.sheet.projection_symbol.is_some())),
        );
        let mut bounds = ink.into_iter().reduce(|a, b| {
            [
                a[0].min(b[0]),
                a[1].min(b[1]),
                a[2].max(b[2]),
                a[3].max(b[3]),
            ]
        })?;
        if reflow && matches!(id.as_str(), "v:front" | "v:plan") {
            bounds[0] = bounds[0].min(column_left);
        }
        if reflow
            && id == "v:iso"
            && (plan.views[i].scale / plan.sheet.scale_factor - 1.0).abs() >= 1e-6
        {
            // Keep the scale disclaimer with its view. A whole clear caption band is
            // reserved before the neighbouring section and tables claim the paper.
            bounds[1] -= 3.0 * crate::section::FONT_SIZE + crate::tables::PAD;
        }
        if late_tables && id == "v:iso" {
            // Stack the orientation view and sections in one clear column, leaving
            // continuous room beside them for a long schedule.
            let width = source
                .views
                .iter()
                .filter(|v| !principal(&v.id) && v.id != "v:iso")
                .filter_map(|v| {
                    crate::sheet_fit::view_ink(analysis, &owned(&v.id))
                        .into_iter()
                        .reduce(|a, b| {
                            [
                                a[0].min(b[0]),
                                a[1].min(b[1]),
                                a[2].max(b[2]),
                                a[3].max(b[3]),
                            ]
                        })
                })
                .map(|b| b[2] - b[0])
                .fold(bounds[2] - bounds[0], f64::max);
            let extra = (width - (bounds[2] - bounds[0])) / 2.0;
            bounds[0] -= extra;
            bounds[2] += extra;
        }
        let mut block = [bounds[2] - bounds[0], bounds[3] - bounds[1]];
        let mut allocation = if let Some(frame) = frame {
            let target = frame.views.iter().find(|v| v.id == id)?;
            if (target.scale - plan.views[i].scale).abs() > 1e-9 {
                return None;
            }
            let delta = [
                target.at[0] - plan.views[i].at[0],
                target.at[1] - plan.views[i].at[1],
            ];
            while pages.len() < target.sheet as usize {
                let number = pages.len() + 1;
                plan.sheets.push(extra_sheet(&plan.sheet, number as u32));
                let mut obstacles =
                    crate::sheet_fit::furniture_bounds(analysis, plan.sheets.last()?, &[]);
                obstacles.push(plan.sheet.title_block);
                pages.push(obstacles);
            }
            Some((
                target.sheet as usize,
                [bounds[0] + delta[0], bounds[1] + delta[1]],
            ))
        } else {
            pages.iter().enumerate().find_map(|(n, obstacles)| {
                if principal(&id)
                    && let Some((delta, peers)) = primary_frames.get(&(n + 1))
                {
                    // Projections sharing a page retain their established alignment
                    // and relative spacing. A different page is a separate frame.
                    let mut delta = *delta;
                    if reflow && id == "v:plan" {
                        delta[1] = peers.first()?[3] + 6.0 - bounds[1];
                    } else if reflow && id == "v:side" {
                        delta[0] = peers.iter().map(|b| b[2]).fold(f64::NEG_INFINITY, f64::max)
                            + 6.0
                            - bounds[0];
                    }
                    if reflow && id == "v:side" {
                        let mut fixed: Vec<_> = obstacles
                            .iter()
                            .filter(|b| !peers.contains(b))
                            .copied()
                            .collect();
                        for peer in plan
                            .views
                            .iter()
                            .filter(|v| matches!(v.id.as_str(), "v:front" | "v:plan"))
                        {
                            let old = source.views.iter().find(|v| v.id == peer.id)?;
                            let d = [peer.at[0] - old.at[0], peer.at[1] - old.at[1]];
                            fixed.extend(
                                crate::sheet_fit::view_ink(analysis, &owned(&peer.id))
                                    .into_iter()
                                    .map(|b| [b[0] + d[0], b[1] + d[1], b[2] + d[0], b[3] + d[1]]),
                            );
                        }
                        let ink = crate::sheet_fit::view_ink(analysis, &own);
                        for inset in (0..=24).rev() {
                            let d = [delta[0] - 2.0 * inset as f64, delta[1]];
                            let clear = ink.iter().all(|b| {
                                let b = [
                                    b[0] + d[0] - 2.0,
                                    b[1] + d[1] - 2.0,
                                    b[2] + d[0] + 2.0,
                                    b[3] + d[1] + 2.0,
                                ];
                                b[0] >= layout::margin()
                                    && b[1] >= layout::margin()
                                    && b[2] <= w - layout::margin()
                                    && b[3] <= h - layout::margin()
                                    && fixed.iter().all(|f| !overlap(b, *f))
                            });
                            if clear {
                                return Some((n + 1, [bounds[0] + d[0], bounds[1] + d[1]]));
                            }
                        }
                        return None;
                    }
                    let at = [bounds[0] + delta[0], bounds[1] + delta[1]];
                    let rect = [at[0], at[1], at[0] + block[0], at[1] + block[1]];
                    if rect[0] < region[0]
                        || rect[1] < region[1]
                        || rect[2] > region[2]
                        || rect[3] > region[3]
                    {
                        return None;
                    }
                    let fixed: Vec<_> = obstacles
                        .iter()
                        .filter(|b| !peers.contains(b))
                        .copied()
                        .collect();
                    if reflow
                        && crate::sheet_fit::view_ink(analysis, &own)
                            .into_iter()
                            .all(|b| {
                                let b = [
                                    b[0] + delta[0] - 2.0,
                                    b[1] + delta[1] - 2.0,
                                    b[2] + delta[0] + 2.0,
                                    b[3] + delta[1] + 2.0,
                                ];
                                fixed.iter().all(|f| !overlap(b, *f))
                            })
                    {
                        return Some((n + 1, at));
                    }
                    return layout::fit_box(block, rect, &fixed, layout::Corner::BottomLeft, 2.)
                        .map(|at| (n + 1, at));
                }
                let mut region = region;
                if reflow && principal(&id) {
                    region[1] += row_lift;
                }
                let corner = if late_tables && !principal(&id) {
                    layout::Corner::TopRight
                } else {
                    layout::Corner::BottomLeft
                };
                layout::fit_box(block, region, obstacles, corner, 2.).map(|at| (n + 1, at))
            })
        };
        if allocation.is_none()
            && reflow
            && id == "v:iso"
            && source.spec.iso_scale_factor.is_none()
            && !source.dimensions.iter().any(|d| d.view == id)
            && !source.gdt.iter().any(|g| g.view == id)
        {
            // The orientation view already fits freely in ordinary sheet layout. Fit it
            // after the tables and dimensioned views, with room for its NTS caption.
            let world = [
                analysis.drawing_iso_bounds()[2] - analysis.drawing_iso_bounds()[0],
                analysis.drawing_iso_bounds()[3] - analysis.drawing_iso_bounds()[1],
            ];
            let gap = layout::largest_empty_rect(region, &pages[0], world);
            let scale = ((gap[2] - gap[0] - 4.0) / world[0])
                .min((gap[3] - gap[1] - 11.0) / world[1])
                .min(source.sheet.scale_factor);
            if scale > 0.0 && world[0].max(world[1]) * scale >= 20.0 {
                let iso = auxiliary(analysis, "iso", scale, 1, source.sheet.scale_factor)?;
                bounds = [
                    iso.at[0] + scale * analysis.drawing_iso_bounds()[0],
                    iso.at[1] + scale * analysis.drawing_iso_bounds()[1]
                        - 3.0 * crate::section::FONT_SIZE
                        - crate::tables::PAD,
                    iso.at[0] + scale * analysis.drawing_iso_bounds()[2],
                    iso.at[1] + scale * analysis.drawing_iso_bounds()[3],
                ];
                block = [bounds[2] - bounds[0], bounds[3] - bounds[1]];
                plan.views[i] = iso;
                allocation =
                    layout::fit_box(block, region, &pages[0], layout::Corner::BottomLeft, 2.0)
                        .map(|at| (1, at));
            }
        }
        if allocation.is_none() {
            let n = pages.len() + 1;
            if n > max {
                return None;
            }
            plan.sheets.push(extra_sheet(&plan.sheet, n as u32));
            let mut obstacles =
                crate::sheet_fit::furniture_bounds(analysis, plan.sheets.last()?, &[]);
            obstacles.push(plan.sheet.title_block);
            let at = layout::fit_box(block, region, &obstacles, layout::Corner::BottomLeft, 2.)?;
            pages.push(obstacles);
            allocation = Some((n, at));
        }
        let (next, at) = allocation?;
        let delta = [at[0] - bounds[0], at[1] - bounds[1]];
        plan.views[i].sheet = next as u32;
        shift(&mut plan.views[i], delta);
        plan.views[i].area = [at[0], at[1], at[0] + block[0], at[1] + block[1]];
        plan.views[i].basis += if reflow {
            "; whole view and its annotations repacked beside split tables on the same sheet"
        } else {
            "; whole view and its annotations allocated to a separate sheet"
        };
        for g in plan.gdt.iter_mut().filter(|g| g.view == id) {
            if let Some(p) = &mut g.placed {
                move_point(&mut p.tip, delta);
                move_box(&mut p.label_box, delta);
                for q in p
                    .lines
                    .iter_mut()
                    .chain(&mut p.arrows)
                    .chain(&mut p.strokes)
                    .flatten()
                {
                    move_point(q, delta);
                }
                for a in &mut p.arcs {
                    a[0] += delta[0];
                    a[1] += delta[1];
                }
                for q in p.fills.iter_mut().flatten() {
                    move_point(q, delta);
                }
                for t in p.letters.iter_mut().chain(&mut p.runs) {
                    move_point(&mut t.at, delta);
                }
            }
        }
        for b in plan.balloons.iter_mut().filter(|b| b.view == id) {
            move_point(&mut b.at, delta);
            if let Some(r) = &mut b.label_box {
                move_box(r, delta);
            }
            if let Some(line) = &mut b.leader {
                for q in line {
                    move_point(q, delta);
                }
            }
        }
        for t in plan
            .hole_table
            .iter_mut()
            .chain(&mut plan.hole_tables)
            .filter(|t| t.view.as_deref().unwrap_or("v:plan") == id)
        {
            t.sheet = next as u32;
            for b in &mut t.blocks {
                move_table(b, delta);
                b.sheet = next as u32;
            }
        }
        for c in plan.captions.iter_mut().filter(|c| c.view == id) {
            c.sheet = next as u32;
            move_point(&mut c.at, delta);
            move_box(&mut c.ink, delta);
        }
        if principal(&id) {
            primary_frames
                .entry(next)
                .or_insert((delta, Vec::new()))
                .1
                .push(plan.views[i].area);
        }
        pages[next - 1].push(plan.views[i].area);
    }
    if !tables_placed {
        reflow_table_furniture(&mut plan, &mut pages[0])?;
    }
    plan.dimensions = crate::placement_intent::held_dimensions(Some(source), &plan.views);
    crate::centrelines::trim_to_frame(&mut plan.views, plan.sheet.page);
    cross_references(analysis, &mut plan);
    crate::iso_caption::refresh(analysis, &mut plan);
    // Re-evaluate spatial findings after transport. Required losses and every
    // unsupported requirement remain in the inventory and problem list.
    plan.problems.retain(|p| {
        !matches!(
            p.code.as_str(),
            "annotation_outside_area"
                | "annotation_off_sheet"
                | "annotation_over_title_block"
                | "annotation_over_view"
                | "annotation_line_over_view"
                | "annotation_overlap"
                | "annotation_ink_overlap"
                | "annotation_leader_crossing"
                | "leader_through_feature"
                | "extension_through_feature"
                | "views_do_not_fit"
                | "views_overlap"
                | "view_on_title_block"
        )
    });
    plan.problems.extend(crate::dims::recheck(analysis, &plan));
    // Reflow never changes a datum's sheet: it is the same single-sheet inventory. Keep
    // its existing source completeness findings rather than adding allocation diagnostics
    // for symbols that were already unavailable before any table moved.
    if !reflow {
        plan.problems.extend(check(&plan));
    }
    // A frame replay is an author's explicit pin edit, not an automatic
    // candidate. Preserve its requested ink and the off-sheet findings above,
    // as on one sheet. Every automatic allocation still rejects clipping.
    if frame.is_none() && !crate::sheet_fit::inside_frames(analysis, &plan) {
        return None;
    }
    plan.sheet.basis += "; ";
    plan.sheet.basis += WHOLE_VIEW_ALLOCATION;
    plan.sheet.fill = crate::sheet_fit::fill(analysis, &plan);
    plan.status = crate::completeness::status(&plan.problems);
    Some(plan)
}

fn shift(view: &mut PlannedView, delta: [f64; 2]) {
    view.at[0] += delta[0];
    view.at[1] += delta[1];
    for k in 0..4 {
        view.area[k] += delta[k % 2];
    }
    crate::centrelines::shift(view, delta);
}

fn relate(spec: &Spec, views: &mut [PlannedView]) {
    use crate::placement_intent::{Side, ViewAlign};
    let mut done = BTreeSet::new();
    for _ in 0..views.len() {
        for i in 0..views.len() {
            let name = views[i].id.trim_start_matches("v:").to_string();
            let Some(place) = spec.views.get(&name).and_then(|v| v.place.as_ref()) else {
                done.insert(name);
                continue;
            };
            if done.contains(&name)
                || (spec
                    .views
                    .get(&place.relative_to)
                    .is_some_and(|v| v.place.is_some())
                    && !done.contains(&place.relative_to))
            {
                continue;
            }
            let Some(parent) = views
                .iter()
                .find(|v| v.id == format!("v:{}", place.relative_to))
            else {
                continue;
            };
            let b = parent.area;
            let a = views[i].area;
            let horizontal = matches!(place.side, Side::Left | Side::Right);
            let k = usize::from(horizontal);
            let along = match place.align {
                ViewAlign::Start => b[k] - a[k],
                ViewAlign::Middle => (b[k] + b[k + 2] - a[k] - a[k + 2]) / 2.,
                ViewAlign::End | ViewAlign::Outside => b[k + 2] - a[k + 2],
                ViewAlign::Projection => parent.at[k] - views[i].at[k],
            };
            let across = match place.side {
                Side::Above => b[3] + place.gap - a[1],
                Side::Below => b[1] - place.gap - a[3],
                Side::Left => b[0] - place.gap - a[2],
                Side::Right => b[2] + place.gap - a[0],
            };
            shift(
                &mut views[i],
                if horizontal {
                    [across, along]
                } else {
                    [along, across]
                },
            );
            done.insert(name);
        }
    }
}

fn cross_references(analysis: &Analysis, plan: &mut Plan) {
    let refs: Vec<_> = plan
        .views
        .iter()
        .filter_map(|v| {
            let cut = v.cut.as_ref()?;
            let parent = plan.views.iter().find(|p| p.id == cut.marked_in)?;
            if v.sheet == parent.sheet {
                return None;
            }
            let letters = parent
                .line_work
                .iter()
                .find(|w| w.kind == "cutting_plane")
                .and_then(|w| w.curves.first())
                .map(|c| {
                    let b = c.bbox();
                    crate::section::cutting_plane(b[0], b[2], b[1]).1
                })
                .unwrap_or([parent.at; 2]);
            let projection =
                // Invariant: Planned cameras come from the fixed views or checked section/detail cameras, with nonparallel axes.
                crate::haecceity::hlr::View::new(v.direction, v.up).expect("section view");
            let caption = [
                v.at[0],
                v.at[1] - model::extent(analysis, &projection).0[1] * v.scale / 2. - 7.,
            ];
            let page = |number| {
                std::iter::once(&plan.sheet)
                    .chain(&plan.sheets)
                    .find(|s| s.number == number)
                    // Invariant: Sheet assignments are validated before cross references are generated.
                    .expect("reference sheet")
                    .page
            };
            // A caption may lie in the next zone below the view. Address the view itself.
            let destination = zone(page(v.sheet), v.at);
            let source = zone(page(parent.sheet), letters[0]);
            let from = reference_position(
                analysis,
                plan,
                parent.sheet,
                &format!(
                    "SECTION {}–{}, SHEET {}, ZONE {}",
                    cut.label,
                    cut.label,
                    v.sheet,
                    destination.clone()
                ),
                &letters.map(|p| [p[0], p[1] + 5.]),
            );
            let at = reference_position(
                analysis,
                plan,
                v.sheet,
                &format!("FROM SHEET {}, ZONE {}", parent.sheet, source.clone()),
                &[[caption[0], caption[1] - 5.]],
            );
            Some((
                v.id.clone(),
                CrossRef {
                    sheet: parent.sheet,
                    zone: source.clone(),
                    at,
                },
                CrossRef {
                    sheet: v.sheet,
                    zone: destination.clone(),
                    at: from,
                },
            ))
        })
        .collect();
    for (id, from, to) in refs {
        // Invariant: refs was collected from these same plan views with no intervening mutation.
        let v = plan.views.iter_mut().find(|v| v.id == id).unwrap();
        v.xref = Some(from);
        // Invariant: refs includes only views whose cut is Some.
        v.cut.as_mut().unwrap().xref = Some(to);
    }
}

/// Keep the whole address clear of existing ink, using obstacle boundaries as deterministic
/// candidates. This changes only the new furniture; the allocation's view bands stay intact.
fn reference_position(
    analysis: &Analysis,
    plan: &Plan,
    number: u32,
    text: &str,
    anchors: &[[f64; 2]],
) -> [f64; 2] {
    use std::sync::OnceLock;
    static FONT: OnceLock<crate::text::Font> = OnceLock::new();
    let font = FONT.get_or_init(|| {
        crate::text::Font::new(include_bytes!("../fonts/IBMPlexMono-Regular.ttf").to_vec())
            // Invariant: Font bytes are bundled at build time and parsed unchanged, independently of user input.
            .expect("bundled section font")
    });
    let laid = font.text(text, crate::section::FONT_SIZE);
    let mut local = plan.clone();
    local.sheet = std::iter::once(&plan.sheet)
        .chain(&plan.sheets)
        .find(|s| s.number == number)
        // Invariant: The validated owning sheet number came from this same plan.
        .unwrap()
        .clone();
    local.views.retain(|v| v.sheet == number);
    let carries = |id: &str| local.views.iter().any(|v| v.id == id);
    local.dimensions.retain(|d| carries(&d.view));
    local.gdt.retain(|g| carries(&g.view));
    local.balloons.retain(|b| carries(&b.view));
    local.tables.retain(|t| t.sheet == number);
    local.hole_table = local.hole_table.filter(|t| t.sheet == number);
    local.hole_tables.retain(|t| t.sheet == number);
    if number > 1 {
        local.notes_block = None;
        local.radius_notes_block = None;
        local.radius_table = None;
    }
    let mut obstacles = crate::sheet_fit::footprints(analysis, &local);
    obstacles.extend(crate::sheet_fit::furniture_footprints(analysis, &local));
    obstacles.push(local.sheet.title_block);
    for v in &plan.views {
        if let Some(cut) = &v.cut
            && let Some(parent) = local.views.iter().find(|p| p.id == cut.marked_in)
            && let Some(curve) = parent
                .line_work
                .iter()
                .find(|w| w.kind == "cutting_plane")
                .and_then(|w| w.curves.first())
        {
            let b = curve.bbox();
            let (ink, letters) = crate::section::cutting_plane(b[0], b[2], b[1]);
            obstacles.extend(crate::output::items_bbox(&ink));
            let label = font.text(&cut.label, crate::section::FONT_SIZE);
            obstacles.extend(letters.map(|at| {
                [
                    at[0] + label.min[0],
                    at[1] + label.min[1],
                    at[0] + label.max[0],
                    at[1] + label.max[1],
                ]
            }));
        }
    }
    let [w, h] = local.sheet.page;
    let limits = [
        layout::margin() + 1. - laid.min[0],
        layout::margin() + 1. - laid.min[1],
        w - layout::margin() - 1. - laid.max[0],
        h - layout::margin() - 1. - laid.max[1],
    ];
    let mut xs: Vec<_> = anchors
        .iter()
        .map(|p| p[0].clamp(limits[0], limits[2]))
        .collect();
    let mut ys: Vec<_> = anchors
        .iter()
        .map(|p| p[1].clamp(limits[1], limits[3]))
        .collect();
    // Beside other ink on its line the address keeps a word's gap more than the 1 mm above or
    // below: 1 mm after a callout's text reads as one line with it (#104).
    const BESIDE: f64 = 3.;
    for b in &obstacles {
        xs.extend([b[0] - BESIDE - laid.max[0], b[2] + BESIDE - laid.min[0]]);
        ys.extend([b[1] - 1. - laid.max[1], b[3] + 1. - laid.min[1]]);
    }
    xs.retain(|x| *x >= limits[0] && *x <= limits[2]);
    ys.retain(|y| *y >= limits[1] && *y <= limits[3]);
    xs.sort_by(f64::total_cmp);
    xs.dedup();
    ys.sort_by(f64::total_cmp);
    ys.dedup();
    xs.into_iter()
        .flat_map(|x| ys.iter().map(move |y| [x, *y]))
        .filter(|at| {
            let b = [
                at[0] + laid.min[0] - BESIDE + 0.5,
                at[1] + laid.min[1] - 0.5,
                at[0] + laid.max[0] + BESIDE - 0.5,
                at[1] + laid.max[1] + 0.5,
            ];
            obstacles.iter().all(|other| !overlap(b, *other))
        })
        .min_by(|a, b| {
            let distance = |p: [f64; 2]| {
                anchors
                    .iter()
                    .map(|anchor| {
                        let near = if anchors.len() > 1 {
                            [
                                anchor[0].clamp(p[0] + laid.min[0], p[0] + laid.max[0]),
                                anchor[1].clamp(p[1] + laid.min[1], p[1] + laid.max[1]),
                            ]
                        } else {
                            p
                        };
                        (near[0] - anchor[0]).powi(2) + (near[1] - anchor[1]).powi(2)
                    })
                    .fold(f64::INFINITY, f64::min)
            };
            distance(*a)
                .total_cmp(&distance(*b))
                .then(a[1].total_cmp(&b[1]))
                .then(a[0].total_cmp(&b[0]))
        })
        .unwrap_or([
            anchors[0][0].clamp(limits[0], limits[2]),
            anchors[0][1].clamp(limits[1], limits[3]),
        ])
}

fn problem(code: &str, severity: &str, subject: &str, message: String, fix: Vec<Op>) -> Problem {
    Problem {
        id: format!("p:{code}:{subject}"),
        code: code.into(),
        severity: severity.into(),
        subjects: vec![subject.into()],
        message,
        fix,
        ..Default::default()
    }
}

pub fn check(plan: &Plan) -> Vec<Problem> {
    check_with_search(plan, false)
}

fn check_with_search(plan: &Plan, blocked: bool) -> Vec<Problem> {
    let mut out = Vec::new();
    if !plan.sheets.is_empty() && plan.spec.sheet.title.contains_key("sheet") {
        let mut p = problem(
            "sheet_label_replaced",
            "info",
            "sheet",
            "Multi-sheet SHEET labels use n/m in place of the spec's sheet/title/sheet.".into(),
            vec![Op::remove("sheet/title/sheet")],
        );
        p.refs.push("title_block/sheet".into());
        out.push(p);
    }
    let sheet_of = |id: &str| {
        plan.views
            .iter()
            .find(|v| v.id == id)
            .map_or(1, |v| v.sheet)
    };
    for frame in plan
        .gdt
        .iter()
        .filter(|g| g.kind == "control_frame" && g.placed.is_some())
    {
        let n = sheet_of(&frame.view);
        for cell in frame.cells.iter().skip(2) {
            for letter in cell
                .split(|c: char| !c.is_ascii_alphabetic())
                .filter(|s| s.len() == 1)
            {
                if !plan.gdt.iter().any(|g| {
                    g.kind == "datum_feature"
                        && g.cells.first().is_some_and(|s| s == letter)
                        && g.placed.is_some()
                        && [1, n].contains(&sheet_of(&g.view))
                }) {
                    let mut p = problem(
                        "datum_not_identified",
                        "error",
                        &frame.id,
                        format!(
                            "Datum {letter} is not identified on sheet 1 or the frame's sheet {n}."
                        ),
                        vec![],
                    );
                    p.id += &format!(":{letter}");
                    p.refs.push(letter.into());
                    out.push(p);
                }
            }
        }
    }
    for view in &plan.views {
        if let Some(cut) = &view.cut {
            let parent = plan.views.iter().find(|v| v.id == cut.marked_in);
            if parent.is_none_or(|p| {
                p.sheet != view.sheet && (view.xref.is_none() || cut.xref.is_none())
            }) {
                let mut p = problem(
                    "cross_reference_missing",
                    "error",
                    &view.id,
                    "A section on another sheet needs cross-references both ways.".into(),
                    vec![],
                );
                p.refs.push(cut.marked_in.clone());
                out.push(p);
            }
        }
    }
    for sheet in std::iter::once(&plan.sheet).chain(&plan.sheets) {
        let views: Vec<_> = plan
            .views
            .iter()
            .filter(|v| v.sheet == sheet.number)
            .collect();
        if let Some(floor) = floor(&plan.spec) {
            let below: Vec<_> = views
                .iter()
                .filter(|v| v.scale < floor)
                .map(|v| v.id.clone())
                .collect();
            if sheet.scale_factor < floor || !below.is_empty() {
                let subject = if sheet.number == 1 {
                    "sheet".into()
                } else {
                    format!("sheet:{}", sheet.number)
                };
                let index = layout::PAGES
                    .iter()
                    .position(|(n, ..)| *n == sheet.size)
                    .unwrap_or(4);
                let scale = views
                    .iter()
                    .map(|v| v.scale)
                    .fold(sheet.scale_factor, f64::min);
                let fix = if sheet.number == 1 && blocked && maximum(&plan.spec) < 9 {
                    Op::set("sheets/max", json!(maximum(&plan.spec) + 1))
                } else if plan.spec.sheet.size.is_some() && index < 4 {
                    Op::set("sheet/size", json!(layout::PAGES[index + 1].0))
                } else {
                    Op::set(
                        "sheets/smallest_scale",
                        json!(model::scale_name(
                            layout::SCALES
                                .into_iter()
                                .find(|s| *s <= scale)
                                .unwrap_or(layout::SCALES[15])
                        )),
                    )
                };
                let mut p = problem(
                    "sheet_below_smallest_scale",
                    "warning",
                    &subject,
                    format!(
                        "Sheet {} has views below the smallest scale {}.",
                        sheet.number,
                        model::scale_name(floor)
                    ),
                    vec![fix],
                );
                p.refs = below;
                out.push(p);
            }
        }
        if sheet.number == 1 {
            continue;
        }
        let mut bounds: Vec<(String, [f64; 4])> =
            views.iter().map(|v| (v.id.clone(), v.area)).collect();
        bounds.extend(
            plan.tables
                .iter()
                .filter(|t| t.sheet == sheet.number)
                .filter_map(|t| t.placed.as_ref().map(|p| (t.id.clone(), p.cells))),
        );
        bounds.extend(
            plan.hole_table
                .iter()
                .chain(&plan.hole_tables)
                .filter(|t| t.sheet == sheet.number)
                .flat_map(|t| {
                    t.blocks
                        .iter()
                        .filter_map(|b| b.placed.as_ref().map(|p| (b.id.clone(), p.cells)))
                }),
        );
        for (i, (id, b)) in bounds.iter().enumerate() {
            // Views keep the frame gutter; tables stand against the frame.
            let m = if i < views.len() {
                layout::margin()
            } else {
                layout::FRAME
            };
            if b[0] < m || b[1] < m || b[2] > sheet.page[0] - m || b[3] > sheet.page[1] - m {
                out.push(problem(
                    "annotation_off_sheet",
                    "error",
                    id,
                    format!(
                        "{} is outside sheet {}.",
                        crate::wording::capital(&crate::wording::block(id)),
                        sheet.number
                    ),
                    vec![],
                ));
            }
            if overlap(*b, sheet.title_block) {
                out.push(problem(
                    "view_on_title_block",
                    "error",
                    id,
                    format!(
                        "{} overlaps sheet {}'s title block.",
                        crate::wording::capital(&crate::wording::block(id)),
                        sheet.number
                    ),
                    vec![],
                ));
            }
            for (other, c) in &bounds[i + 1..] {
                if overlap(*b, *c) {
                    let mut p = problem(
                        "views_overlap",
                        "error",
                        id,
                        format!(
                            "{} overlaps {} on sheet {}.",
                            crate::wording::capital(&crate::wording::block(id)),
                            crate::wording::block(other),
                            sheet.number
                        ),
                        vec![],
                    );
                    p.refs.push(other.clone());
                    out.push(p);
                }
            }
        }
    }
    out
}

pub(crate) fn overlap(a: [f64; 4], b: [f64; 4]) -> bool {
    a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn issue109_sheet_relations_distinguish_projection_from_legacy_area_alignment() {
        // Captured issue areas reproduce the editor's shifts exactly in the area resolver.
        for (align, shift_x) in [
            ("start", 0.95),
            ("middle", -40.9005),
            ("end", -82.751),
            ("outside", -82.751),
            ("projection", 0.),
        ] {
            for gap in [0., 5., 15., 30.] {
                let mut pair: Vec<PlannedView> = serde_json::from_str(include_str!(
                    "../tests/fixtures/projection_alignment/issue109.views.json"
                ))
                .unwrap();
                let spec: Spec = serde_json::from_value(json!({"views":{"plan":{"place":{
                    "relative_to":"front", "side":"above", "align":align, "gap":gap
                }}}}))
                .unwrap();
                relate(&spec, &mut pair);
                assert!(
                    (pair[1].at[0] - pair[0].at[0] - shift_x).abs() < 1e-6,
                    "{align}"
                );
                assert!((pair[1].area[1] - pair[0].area[3] - gap).abs() < 1e-6);
            }
        }
    }

    #[test]
    fn explicit_under_floor_steps_do_not_block_small_sheet_rescue() {
        let (_, a) = model::analyse_native(
            &std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/parts/k5/turned-step-repeated-lengths.step"),
        )
        .unwrap();
        let mut spec = Spec::default();
        spec.sheet.scale = Some("1:10".into());
        let mut seed_spec = spec.clone();
        seed_spec.sheet.size = Some("A2".into());
        let seed = model::plan_escalated(&a, &seed_spec).unwrap();
        assert!(crate::sheet_fit::illegible_steps(&a, &seed) > 0);
        assert!(model::fits_a3_at_full_size(&a, &seed));
        // The A3 candidate already meets every gate except inherited step legibility.
        let mut small_spec = spec.clone();
        small_spec.sheet.size = Some("A3".into());
        let small = allocate_inner(&a, &small_spec, Some(&seed)).unwrap();
        assert!(model::findings(&small).clean());
        assert!(model::carries_required(&seed, &small));
        assert!(model::drawn_sheets_fit(&a, &small));
        assert!(model::over_views(&small) <= model::over_views(&seed));
        assert!(!small.problems.iter().any(blocks_small_plan));
        assert!(crate::sheet_fit::illegible_steps(&a, &small) > 0);
        let mut tried = Vec::new();
        let rescued =
            small_fallback(&a, &spec, &seed, &mut tried).unwrap_or_else(|| panic!("{tried:?}"));
        assert!(["A4", "A3"].contains(&rescued.sheet.size.as_str()));
        assert_eq!(rescued.sheet.scale, "1:10");
        assert!(model::carries_required(&seed, &rescued));
        assert!(model::drawn_sheets_fit(&a, &rescued));
        assert!(
            crate::sheet_fit::illegible_steps(&a, &rescued)
                <= crate::sheet_fit::illegible_steps(&a, &seed)
        );
    }

    #[test]
    fn long_table_preflight_reserves_an_exclusive_sheet() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/parts/prismatic.step");
        let (_, analysis) = model::analyse_native(&path).unwrap();
        let mut spec: Spec = serde_json::from_value(json!({
            "sheets": {"max": 2}, "views": {"iso": {"sheet": 2}}
        }))
        .unwrap();
        let mut prior = model::plan(&analysis, &spec).unwrap();
        // Typed evidence from the prior escalation; no table ink survives NoPlace.
        prior.table_outcome = hole_table::TableOutcome::NoPlace;
        assert!(prior.hole_table.is_none());
        let moved = BTreeMap::from([("iso".into(), 2), ("hole_table".into(), 0)]);
        assert!(!pack(&analysis, &spec, &moved, &mut prior.clone()));
        spec.sheets.as_mut().unwrap().max = Some(3);
        assert!(pack(&analysis, &spec, &moved, &mut prior));
        assert_eq!(prior.sheets.len(), 2);
        assert_eq!(
            prior.views.iter().find(|v| v.id == "v:iso").unwrap().sheet,
            2
        );
    }

    #[test]
    fn a_forced_group_assigns_the_rescue_table_to_its_views_sheet() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/parts/scattered_holes.step");
        let (_, analysis) = model::analyse_native(&path).unwrap();
        let spec: Spec = serde_json::from_value(json!({
            "sheet": {"preferred_max_size": "A0"},
            "sheets": {"max": 2, "keep_together": [["iso", "hole_table"]]},
            "views": {"iso": {"sheet": 2}}
        }))
        .unwrap();
        let mut p = model::plan(&analysis, &spec).unwrap();
        let id = p
            .dimensions
            .iter()
            .find(|d| {
                d.view == "v:plan"
                    && d.placed.is_some()
                    && d.kind == "linear"
                    && d.measurements.iter().any(|m| {
                        m.feature.starts_with("f:hole:")
                            && m.parameter.starts_with("location.length")
                    })
            })
            .unwrap()
            .id
            .clone();
        p.problems.push(Problem {
            id: format!("stand-in:{id}"),
            code: "annotation_off_sheet".into(),
            severity: "warning".into(),
            subjects: vec![id],
            ..Default::default()
        });
        assert_eq!(
            hole_table::escalate(&analysis, &mut p, Some(2)),
            hole_table::TableOutcome::Kept
        );
        let moved = BTreeMap::from([("iso".into(), 2), ("hole_table".into(), 2)]);
        assert!(pack(&analysis, &spec, &moved, &mut p));
        assert_eq!(p.sheets.len(), 1);
        assert_eq!(p.hole_table.as_ref().unwrap().sheet, 2);
        assert_eq!(p.hole_table.as_ref().unwrap().rows.len(), 18);
        assert_eq!(p.balloons.len(), 18);
        assert_eq!(p.views.iter().find(|v| v.id == "v:iso").unwrap().sheet, 2);
        assert!(!check(&p).iter().any(|p| p.code == "views_overlap"));
    }

    #[test]
    fn a_lone_isometric_returns_to_sheet_one_when_it_has_room() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/parts/prismatic.step");
        let (_, analysis) = model::analyse_native(&path).unwrap();
        let spec: Spec = serde_json::from_value(json!({"sheets": {"max": 2}})).unwrap();
        let mut p = model::plan(&analysis, &spec).unwrap();
        let moved = BTreeMap::from([("iso".into(), 0)]);
        assert!(pack(&analysis, &spec, &moved, &mut p));
        assert_eq!(p.sheets.len(), 1);
        assert!(crate::sheet_policy::pictorial_only_sheet(&spec, &p));
        // The author's own assignment keeps the isometric on its sheet.
        let authored: Spec =
            serde_json::from_value(json!({"sheets": {"max": 2}, "views": {"iso": {"sheet": 2}}}))
                .unwrap();
        assert!(!rehome_isometric(&analysis, &authored, &mut p.clone()));
        assert!(rehome_isometric(&analysis, &spec, &mut p));
        assert!(p.sheets.is_empty());
        let iso = p.views.iter().find(|v| v.id == "v:iso").unwrap();
        assert_eq!(iso.sheet, 1);
        assert!(iso.scale + 1e-9 >= layout::ISO_MIN_FIT_FRAC * p.sheet.scale_factor);
        assert!(!overlap(iso.area, p.sheet.title_block));
        assert!(
            p.views
                .iter()
                .filter(|v| v.id != "v:iso")
                .all(|v| !overlap(iso.area, v.area))
        );
        assert!(!crate::sheet_policy::pictorial_only_sheet(&spec, &p));
    }
}
