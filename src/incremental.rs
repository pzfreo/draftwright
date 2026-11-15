//! Retained-layout planning uses the ordinary planner's published pin reference.
use std::cell::{Cell, RefCell};
use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::model::{self, Analysis, Invalid, Plan, Problem, Spec};
use crate::placement_intent::Place;

thread_local! {
    static RESTORED: RefCell<BTreeSet<String>> = const { RefCell::new(BTreeSet::new()) };
    static INTENT: RefCell<Option<Spec>> = const { RefCell::new(None) };
    static ACTIVE: Cell<bool> = const { Cell::new(false) };
}
pub(crate) fn active() -> bool {
    ACTIVE.get()
}

/// A prior content variant, including its shared statement's contributing edits.
/// These are bounded by distinct authored variants, never nested plans.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub(crate) struct RetiredDimension {
    dimension: crate::dims::PlannedDimension,
    edits: Vec<(String, model::DimensionSpec)>,
    order: usize,
    covered: Vec<crate::dims::Omitted>,
}
impl RetiredDimension {
    fn new(before: &Plan, dimension: &crate::dims::PlannedDimension) -> Self {
        let keys = std::iter::once(dimension.id.clone()).chain(before.omitted.iter()
            .filter(|o| matches!(&o.state, crate::items::State::Covered { by } if by == &dimension.id))
            .map(|o| o.id.clone()));
        Self {
            dimension: dimension.clone(),
            covered: before
                .omitted
                .iter()
                .filter(|o| {
                    matches!(&o.state,
                crate::items::State::Covered { by } if by == &dimension.id)
                })
                .cloned()
                .collect(),
            order: before
                .dimensions
                .iter()
                .position(|d| d.id == dimension.id)
                .unwrap_or(before.dimensions.len()),
            edits: keys
                .map(|id| {
                    let edit = before.spec.dimensions.get(&id).cloned().unwrap_or_default();
                    (id, edit)
                })
                .collect(),
        }
    }
    fn matches(&self, spec: &Spec) -> bool {
        self.edits
            .iter()
            .all(|(id, edit)| spec.dimensions.get(id).cloned().unwrap_or_default() == *edit)
    }
}

pub(crate) fn plan(analysis: &Analysis, before: &Plan, spec: &Spec) -> Result<Plan, Invalid> {
    plan_revision(analysis, before, spec, false)
}

/// Only the source API calls this, after exact B-rep identity has been verified.
pub(crate) fn plan_source(
    analysis: &Analysis,
    before: &Plan,
    spec: &Spec,
) -> Result<Plan, Invalid> {
    plan_revision(analysis, before, spec, true)
}
fn plan_revision(
    analysis: &Analysis,
    before: &Plan,
    spec: &Spec,
    source_verified: bool,
) -> Result<Plan, Invalid> {
    let refuse = |message: &str| Invalid {
        message: format!("Incremental apply {message}; use full apply explicitly."),
        path: None,
        choices: Vec::new(),
        also: Vec::new(),
    };
    if (!source_verified && before.binding.source_sha256 != analysis.binding.source_sha256)
        || before.binding.part_index != analysis.binding.part_index
    {
        return Err(refuse("requires the committed plan for the same part"));
    }
    if !before.sheets.is_empty() {
        return Err(refuse("currently requires a single-sheet drawing"));
    }
    // Layout/geometry edits cannot be expressed as additions in the retained frame.
    if before.spec.views != spec.views
        || before.spec.sheet != spec.sheet
        || before.spec.sheets != spec.sheets
        || before.spec.units != spec.units
    {
        return Err(refuse("cannot change sheet or view intent"));
    }
    // The full Spec comparison excludes the annotation edits this entry point accepts.
    // Geometry, recognition, notes and tables need a full apply until they have pin support.
    let mut layout_before = before.spec.clone();
    let mut layout_after = spec.clone();
    for layout in [&mut layout_before, &mut layout_after] {
        layout.engine_pins = None;
        layout.dimensions.clear();
        layout.annotations.clear();
        for f in layout.faces.values_mut() {
            f.datum = None;
            f.controls.clear();
        }
        layout.faces.retain(|_, f| *f != Default::default());
        for f in layout.features.values_mut() {
            f.datum = None;
            f.controls.clear();
        }
        layout.features.retain(|_, f| *f != Default::default());
    }
    if layout_before != layout_after {
        return Err(refuse(
            "supports annotation edits, not geometry or furniture changes",
        ));
    }
    let mut pinned = spec.clone();
    pinned.engine_pins = None;
    pinned.sheet.size = Some(before.sheet.size.clone());
    pinned.sheet.scale = Some(before.sheet.scale.clone());
    pinned.sheet.keep_layout = None;
    pinned.sheets = None;
    let mut reference = before.clone();
    reference.incremental_gdt.clear();
    reference.incremental_dimensions.clear();
    let mut restored = BTreeSet::new();
    for retired in before.incremental_dimensions.iter().rev() {
        let id = &retired.dimension.id;
        if retired.matches(spec)
            && !spec
                .engine_pins
                .as_ref()
                .is_some_and(|p| p.rebase.contains(id))
            && restored.insert(id.clone())
        {
            for omitted in &retired.covered {
                restored.insert(omitted.id.clone());
                reference.dimensions.retain(|d| d.id != omitted.id);
                reference.omitted.retain(|o| o.id != omitted.id);
                reference.omitted.push(omitted.clone());
            }
            if let Some(d) = reference.dimensions.iter_mut().find(|d| &d.id == id) {
                *d = retired.dimension.clone();
            } else {
                reference.dimensions.insert(
                    retired.order.min(reference.dimensions.len()),
                    retired.dimension.clone(),
                );
            }
        }
    }
    for d in &reference.dimensions {
        if d.placed.is_some() && (same_dimension(before, spec, &d.id) || restored.contains(&d.id)) {
            pinned.dimensions.entry(d.id.clone()).or_default().place = Some(Place {
                pinned: true,
                ..Default::default()
            });
        }
    }
    let effective = crate::spec_gdt::effective(analysis, spec)?;
    let (mut lowered, _) = crate::pmi_lowering::lower_in(
        &effective,
        &|name| before.views.iter().any(|v| v.id == format!("v:{name}")),
        spec.units,
    );
    crate::label_text::annotations(&mut lowered, spec)?;
    for old in before.incremental_gdt.iter().rev() {
        if let Some(current) = reference.gdt.iter_mut().find(|g| g.id == old.id) {
            if !lowered
                .iter()
                .any(|item| crate::dims::gdt::same_content(current, item, spec.units))
                && lowered
                    .iter()
                    .any(|item| crate::dims::gdt::same_content(old, item, spec.units))
            {
                *current = old.clone();
            }
        } else {
            reference.gdt.push(old.clone());
        }
    }
    for g in &reference.gdt {
        if g.placed.is_some()
            && spec.annotations.get(&g.id).cloned().unwrap_or_default()
                == before
                    .spec
                    .annotations
                    .get(&g.id)
                    .cloned()
                    .unwrap_or_default()
            && lowered
                .iter()
                .any(|item| crate::dims::gdt::same_content(g, item, spec.units))
        {
            pinned.annotations.entry(g.id.clone()).or_default().place = Some(Place {
                pinned: true,
                ..Default::default()
            });
        }
    }
    struct Restore(bool, Option<Spec>, BTreeSet<String>);
    impl Drop for Restore {
        fn drop(&mut self) {
            ACTIVE.set(self.0);
            INTENT.with(|s| *s.borrow_mut() = self.1.take());
            RESTORED.with(|s| *s.borrow_mut() = std::mem::take(&mut self.2));
        }
    }
    let _restore = Restore(
        ACTIVE.replace(true),
        INTENT.with(|s| s.replace(Some(spec.clone()))),
        RESTORED.with(|s| s.replace(restored)),
    );
    let compact = before.sheet.dimension_spacing == model::DimensionSpacing::Compact;
    let mut after = crate::planning::with_pin_reference(reference.clone(), || {
        crate::dims::with_compact(compact, || model::plan(analysis, &pinned))
    })?;
    // placement_intent::views substitutes the published frames before placement;
    // view_gutters is disabled in this scope. No sheet recovery is invoked.
    retain_from(&mut after, &reference, spec);
    crate::dims::contacts::report(&effective, &mut after);
    // Remove only transaction-generated pin provenance. Authored content/state changes
    // keep their newly planned records; unchanged items keep their published records.
    for d in &mut after.dimensions {
        if let Some(old) = before.dimensions.iter().find(|old| old.id == d.id)
            && old.terms == d.terms
            && old.placed == d.placed
            && old.view == d.view
            && spec.dimensions.get(&d.id) == before.spec.dimensions.get(&d.id)
        {
            *d = old.clone();
        }
    }
    for g in &mut after.gdt {
        if let Some(old) = reference.gdt.iter().find(|old| old.id == g.id)
            && old.cells == g.cells
            && old.placed == g.placed
            && old.view == g.view
            && old.state == g.state
            && old.source == g.source
            && spec.annotations.get(&g.id) == before.spec.annotations.get(&g.id)
        {
            *g = old.clone();
        }
    }
    let same_glyph = |a: &crate::dims::gdt::PlannedGdt, b: &crate::dims::gdt::PlannedGdt| {
        a.id == b.id && a.cells == b.cells && a.faces == b.faces && a.modifiers == b.modifiers
    };
    after.incremental_gdt.clone_from(&before.incremental_gdt);
    for old in &before.gdt {
        if !after.gdt.iter().any(|g| same_glyph(old, g)) {
            after.incremental_gdt.retain(|g| !same_glyph(old, g));
            after.incremental_gdt.push(old.clone());
        }
    }
    after
        .incremental_gdt
        .retain(|old| !after.gdt.iter().any(|g| same_glyph(old, g)));
    after.incremental_dimensions = before.incremental_dimensions.clone();
    for old in &before.dimensions {
        if !same_dimension(before, spec, &old.id) {
            let retired = RetiredDimension::new(before, old);
            if retired.matches(spec) {
                continue;
            }
            after
                .incremental_dimensions
                .retain(|r| r.dimension.id != old.id || r.edits != retired.edits);
            after.incremental_dimensions.push(retired);
        }
    }
    after.incremental_dimensions.retain(|r| {
        !(r.matches(spec)
            && after.dimensions.iter().any(|d| {
                d.id == r.dimension.id
                    && d.placed == r.dimension.placed
                    && d.terms == r.dimension.terms
            }))
    });
    cap_variants(&mut after.incremental_gdt, |g| &g.id);
    cap_variants(&mut after.incremental_dimensions, |r| &r.dimension.id);
    // The published pin record is installed after the solve; authored pins stay intact.
    after.spec = spec.clone();
    after.sheet.size.clone_from(&before.sheet.size);
    after.sheet.scale.clone_from(&before.sheet.scale);
    after.sheet.default = before.sheet.default;
    after.sheet.basis.clone_from(&before.sheet.basis);
    retain_findings(before, &mut after);
    let new: Vec<_> = after
        .gdt
        .iter()
        .filter(|g| {
            !reference
                .gdt
                .iter()
                .any(|b| b.id == g.id && b.cells == g.cells && b.faces == g.faces)
        })
        .map(|g| g.id.clone())
        .chain(
            after
                .dimensions
                .iter()
                .filter(|d| {
                    !reference
                        .dimensions
                        .iter()
                        .any(|b| b.id == d.id && b.terms == d.terms)
                })
                .map(|d| d.id.clone()),
        )
        .collect();
    let affected: Vec<_> = new
        .iter()
        .filter(|id| {
            after.gdt.iter().any(|g| &g.id == *id && g.placed.is_none())
                || after
                    .dimensions
                    .iter()
                    .any(|d| &d.id == *id && d.placed.is_none())
                || after.problems.iter().any(|p| {
                    p.subjects.contains(id)
                        && (p.severity != "info"
                            || p.code.starts_with("annotation_")
                            || p.code == "label_crowded")
                })
        })
        .cloned()
        .collect();
    if !affected.is_empty() {
        after.problems.push(Problem {
            id: format!("p:incremental_placement_constrained:{}", affected.join("|")),
            code: "incremental_placement_constrained".into(), severity: "warning".into(),
            message: "New annotation ink could not be placed cleanly in the retained layout. Existing placements were kept; inspect the placement findings or preview an explicit full apply.".into(),
            subjects: affected, ..Default::default()
        });
    }
    let signature = layout_signature(&after);
    after
        .incremental_findings
        .clone_from(&before.incremental_findings);
    if let Some(retired) = before
        .incremental_findings
        .iter()
        .find(|r| r.layout == signature)
    {
        after.problems.clone_from(&retired.problems);
    }
    let previous = layout_signature(before);
    if previous != signature
        && !after
            .incremental_findings
            .iter()
            .any(|r| r.layout == previous)
    {
        after.incremental_findings.push(RetiredFindings {
            layout: previous,
            problems: before.problems.clone(),
        });
    }
    if after.incremental_findings.len() > 32 {
        after.incremental_findings.remove(1);
    }
    let mut ids = BTreeSet::new();
    after.problems.retain(|p| ids.insert(p.id.clone()));
    after.status = crate::completeness::status(&after.problems);
    persist(&mut after);
    Ok(after)
}

// Keep the original variant and the 31 most recent alternatives per item.
fn cap_variants<T>(variants: &mut Vec<T>, id: impl Fn(&T) -> &str) {
    let mut counts = BTreeMap::<String, usize>::new();
    for v in variants.iter() {
        *counts.entry(id(v).into()).or_default() += 1;
    }
    let mut first = BTreeSet::new();
    variants.retain(|v| {
        let key = id(v);
        if first.insert(key.to_string()) {
            return true;
        }
        let count = counts.get_mut(key).expect("variant was counted");
        if *count > 32 {
            *count -= 1;
            false
        } else {
            true
        }
    });
}

fn same_dimension(before: &Plan, spec: &Spec, id: &str) -> bool {
    let same = |id: &str| {
        before.spec.dimensions.get(id).cloned().unwrap_or_default()
            == spec.dimensions.get(id).cloned().unwrap_or_default()
    };
    !spec
        .engine_pins
        .as_ref()
        .is_some_and(|p| p.rebase.iter().any(|d| d == id))
        && same(id)
        && before
            .omitted
            .iter()
            .filter(|o| matches!(&o.state, crate::items::State::Covered { by } if by == id))
            .all(|o| same(&o.id))
}
fn intent() -> Option<Spec> {
    INTENT.with(|s| s.borrow().clone())
}

pub(crate) struct Dimensions {
    pub ids: BTreeSet<String>,
    pub held: BTreeMap<String, crate::dims::PlannedDimension>,
}
/// Retain the published recipes too: a synthetic pin must not split a shared
/// quantity, rebase an existing measurement onto a new datum, or invent dimensions.
pub(crate) fn dimensions() -> Option<Dimensions> {
    if !active() {
        return None;
    }
    let before = crate::planning::pin_reference_shared()?;
    let spec = intent()?;
    let restored = RESTORED.with(|s| s.borrow().clone());
    Some(Dimensions {
        ids: before
            .dimensions
            .iter()
            .map(|d| d.id.clone())
            .chain(before.omitted.iter().map(|o| o.id.clone()))
            .chain(spec.dimensions.keys().cloned())
            .collect(),
        held: before
            .dimensions
            .iter()
            .filter(|d| {
                d.placed.is_some()
                    && (same_dimension(&before, &spec, &d.id) || restored.contains(&d.id))
            })
            .map(|d| (d.id.clone(), d.clone()))
            .collect(),
    })
}

/// Covered/omitted ink is a retained presentation too: pinning an undrawn callout
/// as visible would promote it out of its existing table or dimension chain.
pub(crate) fn excluded(spec: &Spec) -> std::collections::BTreeSet<String> {
    if !active() {
        return Default::default();
    }
    let Some(before) = crate::planning::pin_reference_shared() else {
        return Default::default();
    };
    let intended = intent().unwrap_or_else(|| spec.clone());
    let restored = RESTORED.with(|s| s.borrow().clone());
    let spec = &intended;
    before
        .dimensions
        .iter()
        .filter(|d| d.placed.is_none() && same_dimension(&before, spec, &d.id))
        .map(|d| d.id.clone())
        .chain(
            before
                .omitted
                .iter()
                .filter(|o| same_dimension(&before, spec, &o.id) || restored.contains(&o.id))
                .map(|o| o.id.clone()),
        )
        .collect()
}

/// Furniture has no authored bare-pin syntax. Feed its published proxies to the
/// very same obstacle solver as the pinned annotation ink, then retain the records.
pub(crate) fn obstacles() -> Vec<crate::dims::PlannedDimension> {
    if !active() {
        return Vec::new();
    }
    let Some(before) = crate::planning::pin_reference_shared() else {
        return Vec::new();
    };
    let mut out: Vec<_> = before
        .tables
        .iter()
        .filter_map(crate::tables::proxy)
        .collect();
    if let Some(t) = &before.radius_table {
        out.extend(crate::tables::proxy(&t.table));
    }
    for table in before.hole_table.iter().chain(&before.hole_tables) {
        out.extend(table.blocks.iter().filter_map(crate::tables::proxy));
    }
    out.extend(
        before
            .notes_block
            .iter()
            .chain(&before.radius_notes_block)
            .map(crate::notes::proxy),
    );
    out.extend(before.captions.iter().map(crate::iso_caption::proxy));
    for b in &before.balloons {
        out.push(ink_proxy(
            &b.id,
            &b.view,
            b.label_box.unwrap_or([
                b.at[0] - b.radius,
                b.at[1] - b.radius,
                b.at[0] + b.radius,
                b.at[1] + b.radius,
            ]),
            b.leader.into_iter().collect(),
        ));
    }
    for b in before
        .inspection_balloons
        .iter()
        .filter(|b| b.state == crate::items::State::Shown)
    {
        out.push(ink_proxy(
            &b.id,
            b.view.as_deref().unwrap_or(""),
            b.bounds(),
            b.lines.clone(),
        ));
    }
    out.extend(
        before
            .requirements
            .iter()
            .filter_map(crate::dims::gdt::finish_proxy),
    );
    for p in &mut out {
        p.kind = "ink".into();
    }
    out
}

pub(crate) fn retain(plan: &mut Plan) {
    if !active() {
        return;
    }
    let Some(before) = crate::planning::pin_reference_shared() else {
        return;
    };
    let Some(spec) = intent() else {
        return;
    };
    retain_from(plan, &before, &spec);
}
fn retain_from(plan: &mut Plan, before: &Plan, spec: &Spec) {
    let restored = RESTORED.with(|s| s.borrow().clone());
    for d in before
        .dimensions
        .iter()
        .filter(|d| same_dimension(before, spec, &d.id) || restored.contains(&d.id))
    {
        plan.dimensions.retain(|a| a.id != d.id);
        plan.dimensions.push(d.clone());
        plan.omitted.retain(|a| a.id != d.id);
    }
    for o in before
        .omitted
        .iter()
        .filter(|o| same_dimension(before, spec, &o.id) || restored.contains(&o.id))
    {
        plan.omitted.retain(|a| a.id != o.id);
        plan.omitted.push(o.clone());
    }
    plan.dimensions.sort_by_key(|d| {
        before
            .dimensions
            .iter()
            .position(|b| b.id == d.id)
            .unwrap_or(usize::MAX)
    });
    for g in &mut plan.gdt {
        if let Some(old) = before.gdt.iter().find(|old| {
            old.id == g.id
                && old.placed.is_some()
                && old.cells == g.cells
                && old.faces == g.faces
                && old.modifiers == g.modifiers
                && spec.annotations.get(&g.id).cloned().unwrap_or_default()
                    == before
                        .spec
                        .annotations
                        .get(&g.id)
                        .cloned()
                        .unwrap_or_default()
        }) {
            g.view.clone_from(&old.view);
            g.side.clone_from(&old.side);
            g.placed.clone_from(&old.placed);
        }
    }
    plan.views.clone_from(&before.views);
    plan.captions.clone_from(&before.captions);
    plan.notes.clone_from(&before.notes);
    plan.notes_block.clone_from(&before.notes_block);
    plan.radius_notes_block
        .clone_from(&before.radius_notes_block);
    plan.radius_table.clone_from(&before.radius_table);
    plan.tables.clone_from(&before.tables);
    plan.hole_table.clone_from(&before.hole_table);
    plan.hole_tables.clone_from(&before.hole_tables);
    plan.balloons.clone_from(&before.balloons);
    plan.requirements.clone_from(&before.requirements);
    plan.inspection_balloons
        .clone_from(&before.inspection_balloons);
    plan.characteristics.clone_from(&before.characteristics);
}

fn ink_proxy(
    id: &str,
    view: &str,
    label_box: [f64; 4],
    lines: Vec<[[f64; 2]; 2]>,
) -> crate::dims::PlannedDimension {
    crate::dims::PlannedDimension {
        callout_offset: None,
        reference: false,
        determined_by: Vec::new(),
        region: None,
        id: id.into(),
        kind: "ink".into(),
        view: view.into(),
        value: 0.,
        label: String::new(),
        terms: Vec::new(),
        measurements: Vec::new(),
        default: true,
        from_spec: Vec::new(),
        basis: String::new(),
        state: crate::items::State::Shown,
        placed: Some(crate::dims::Placed {
            lines,
            arrows: Vec::new(),
            label_at: [label_box[0], label_box[1]],
            rotation: 0.,
            label_box,
        }),
    }
}

/// Pin replay can bypass the pass that originally diagnosed retained ink (notably
/// a dropped location). An unchanged defect is not a resolved finding. Semantic
/// dependency findings are deliberately re-evaluated by the normal planner.
fn retain_findings(before: &Plan, after: &mut Plan) {
    let geometric = |p: &Problem| {
        p.labels().impact == crate::finding::Impact::Readability
            || p.code.starts_with("annotation_")
            || p.code.starts_with("leader_")
            || p.code.starts_with("extension_")
            || p.code == "label_crowded"
            || p.code == "location_ref_dropped"
    };
    if !before.problems.iter().any(geometric) {
        return;
    }
    let next = crate::items::items(after);
    let unchanged: BTreeSet<_> = crate::items::items(before)
        .into_iter()
        .filter(|old| {
            if !next.iter().any(|new| new == old) {
                return false;
            }
            let id = &old.id;
            if let Some(d) = before.dimensions.iter().find(|d| &d.id == id) {
                return after.dimensions.iter().any(|n| {
                    n.id == d.id
                        && n.view == d.view
                        && n.terms == d.terms
                        && n.measurements == d.measurements
                        && n.state == d.state
                        && n.placed == d.placed
                });
            }
            if let Some(g) = before.gdt.iter().find(|g| &g.id == id) {
                return after.gdt.iter().any(|n| {
                    n.id == g.id
                        && n.view == g.view
                        && n.cells == g.cells
                        && n.faces == g.faces
                        && n.modifiers == g.modifiers
                        && n.state == g.state
                        && n.placed == g.placed
                });
            }
            if let Some(v) = before.views.iter().find(|v| &v.id == id) {
                return after.views.iter().any(|n| {
                    n.id == v.id
                        && n.at == v.at
                        && n.area == v.area
                        && n.scale == v.scale
                        && n.direction == v.direction
                        && n.up == v.up
                        && n.sheet == v.sheet
                });
            }
            // Omitted records have no ink. All remaining furniture is frozen by retain_from.
            true
        })
        .map(|item| item.id)
        .collect();
    for p in before.problems.iter().filter(|p| geometric(p)) {
        if p.subjects.iter().any(|id| unchanged.contains(id))
            && p.subjects
                .iter()
                .all(|id| unchanged.contains(id) || id.starts_with("f:") || id.starts_with("face:"))
            && !after
                .problems
                .iter()
                .any(|n| n.id == p.id || (n.code == p.code && n.subjects == p.subjects))
        {
            after.problems.push(p.clone());
        }
    }
}

/// Engine-owned exact pin reference. Authored `place` pins remain in their usual
/// fields; removing this single field is the editor's Re-layout operation.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EnginePins {
    pub written_by: EngineWriter,
    pub source_sha256: String,
    /// The drawing intent at the time these pins were written, without engine pins.
    pub intent: Box<serde_json::value::RawValue>,
    /// Published sheet, view frames, retained recipes and ink. No B-rep or nested Plan.
    pub layout: Box<serde_json::value::RawValue>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub rebase: Vec<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    retired_gdt: Vec<Box<serde_json::value::RawValue>>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    retired_dimensions: Vec<Box<serde_json::value::RawValue>>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    retired_findings: Vec<Box<serde_json::value::RawValue>>,
}
impl PartialEq for EnginePins {
    fn eq(&self, other: &Self) -> bool {
        serde_json::to_value(self).expect("pins serialize")
            == serde_json::to_value(other).expect("pins serialize")
    }
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum EngineWriter {
    Engine,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub(crate) struct RetiredFindings {
    layout: String,
    problems: Vec<Problem>,
}

const LAYOUT_FIELDS: &[&str] = &[
    "sheet",
    "views",
    "dimensions",
    "omitted",
    "gdt",
    "captions",
    "notes",
    "notes_block",
    "radius_notes_block",
    "radius_table",
    "tables",
    "hole_table",
    "hole_tables",
    "balloons",
    "requirements",
    "characteristics",
    "inspection_balloons",
    "problems",
];
fn layout_signature(plan: &Plan) -> String {
    let dimensions: BTreeMap<_, _> = plan
        .dimensions
        .iter()
        .map(|d| (&d.id, (&d.view, &d.placed, &d.terms, &d.measurements)))
        .collect();
    let annotations: BTreeMap<_, _> = plan
        .gdt
        .iter()
        .map(|g| (&g.id, (&g.view, &g.placed, &g.cells, &g.faces)))
        .collect();
    let omitted: BTreeMap<_, _> = plan.omitted.iter().map(|o| (&o.id, &o.state)).collect();
    Sha256::digest(
        serde_json::to_vec(&(
            &plan.binding.source_sha256,
            dimensions,
            annotations,
            omitted,
        ))
        .expect("layout serializes"),
    )
    .iter()
    .map(|b| format!("{b:02x}"))
    .collect()
}
fn persist(plan: &mut Plan) {
    let mut intent = plan.spec.clone();
    intent.engine_pins = None;
    let value = serde_json::to_value(&*plan).expect("plan serializes");
    plan.spec.engine_pins = Some(Box::new(EnginePins {
        written_by: EngineWriter::Engine,
        source_sha256: plan.binding.source_sha256.clone(),
        intent: serde_json::value::to_raw_value(&intent).expect("Spec serializes"),
        layout: serde_json::value::to_raw_value(
            &LAYOUT_FIELDS
                .iter()
                .filter_map(|k| value.get(*k).map(|v| ((*k).to_string(), v.clone())))
                .collect::<BTreeMap<_, _>>(),
        )
        .expect("pins serialize"),
        rebase: Vec::new(),
        retired_gdt: plan
            .incremental_gdt
            .iter()
            .map(|v| serde_json::value::to_raw_value(v).expect("ink serializes"))
            .collect(),
        retired_dimensions: plan
            .incremental_dimensions
            .iter()
            .map(|v| serde_json::value::to_raw_value(v).expect("recipe serializes"))
            .collect(),
        retired_findings: plan
            .incremental_findings
            .iter()
            .map(|v| serde_json::value::to_raw_value(v).expect("findings serialize"))
            .collect(),
    }));
}

pub(crate) fn replay(analysis: &Analysis, spec: &Spec) -> Result<Plan, Invalid> {
    let pins = spec
        .engine_pins
        .as_ref()
        .expect("replay requires engine pins");
    let invalid = |message: String| Invalid {
        message,
        path: Some("engine_pins".into()),
        choices: Vec::new(),
        also: Vec::new(),
    };
    if pins.source_sha256 != analysis.binding.source_sha256 {
        return Err(invalid("Engine pins belong to another STEP revision; update the source incrementally in its Session, or remove engine_pins for a full re-layout.".into()));
    }
    let layout: BTreeMap<String, serde_json::Value> = serde_json::from_str(pins.layout.get())
        .map_err(|e| invalid(format!("Invalid engine layout: {e}")))?;
    if layout.keys().any(|k| !LAYOUT_FIELDS.contains(&k.as_str())) {
        return Err(invalid("Unknown engine pin collection.".into()));
    }
    let mut reference = serde_json::json!({"schema": model::PLAN_SCHEMA, "binding":analysis.binding,
        "spec": serde_json::from_str::<serde_json::Value>(pins.intent.get()).map_err(|e| invalid(e.to_string()))?, "dimensions":[], "problems":[]});
    for (key, value) in &layout {
        reference[key] = value.clone();
    }
    let mut reference: Plan = serde_json::from_value(reference)
        .map_err(|e| invalid(format!("Invalid engine pins: {e}")))?;
    reference.incremental_gdt = pins
        .retired_gdt
        .iter()
        .map(|v| serde_json::from_str(v.get()))
        .collect::<Result<_, _>>()
        .map_err(|e| invalid(e.to_string()))?;
    reference.incremental_dimensions = pins
        .retired_dimensions
        .iter()
        .map(|v| serde_json::from_str(v.get()))
        .collect::<Result<_, _>>()
        .map_err(|e| invalid(e.to_string()))?;
    reference.incremental_findings = pins
        .retired_findings
        .iter()
        .map(|v| serde_json::from_str(v.get()))
        .collect::<Result<_, _>>()
        .map_err(|e| invalid(e.to_string()))?;
    let unchanged = serde_json::to_value({
        let mut s = spec.clone();
        s.engine_pins = None;
        s
    })
    .expect("Spec serializes")
        == serde_json::from_str::<serde_json::Value>(pins.intent.get())
            .map_err(|e| invalid(e.to_string()))?
        && pins.rebase.is_empty();
    let mut result = plan(analysis, &reference, spec)?;
    if unchanged {
        result.problems = reference.problems;
        result.status = crate::completeness::status(&result.problems);
        persist(&mut result);
    }
    Ok(result)
}

pub(crate) fn basis_offers(
    analysis: &Analysis,
    id: &str,
    measurements: &[crate::dims::Measurement],
) -> Vec<Problem> {
    let Some(crate::pmi::Reading::Read(reading)) = &analysis.pmi else {
        return Vec::new();
    };
    reading.records.iter().filter(|r| r.category == crate::pmi::Category::Datum).filter_map(|datum| {
        let axis = datum.geometry.as_ref().filter(|g| g.surface.as_deref() == Some("plane")).and_then(|g| g.axis.as_deref())?;
        let suffix = format!(".{}", axis.to_ascii_lowercase());
        if !measurements.iter().any(|m| m.parameter.contains("location.") && m.parameter.ends_with(&suffix)) { return None; }
        Some(Problem {
            id: format!("p:incremental_basis_kept:{id}:{}",datum.label),
            code:"incremental_basis_kept".into(), severity:"info".into(),
            message:format!("The existing dimension basis was kept. Dimension from datum {} is available for preview.",datum.label),
            subjects:vec![id.into()],
            fixes:vec![crate::fixes::Fix::new(format!("Dimension from datum {}",datum.label),vec![
                crate::items::Op::set("engine_pins/rebase",serde_json::json!([id]))
            ])], ..Default::default()
        })
    }).collect()
}
