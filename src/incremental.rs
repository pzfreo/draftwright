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
    static CONTEXT: Cell<Option<[u8; 32]>> = const { Cell::new(None) };
    static ACTIVE: Cell<bool> = const { Cell::new(false) };
}
pub(crate) fn active() -> bool {
    ACTIVE.get()
}

pub(crate) fn context_digest() -> Option<[u8; 32]> {
    CONTEXT.get()
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
    // Every placement memo includes INTENT, RESTORED and the pin reference.
    // Keep the caller's retained Analysis preparation for preview/commit reuse.
    plan_revision_scoped(analysis, before, spec, source_verified)
}

fn plan_revision_scoped(
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
        layout.schema = model::SPEC_SCHEMA.into();
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
    struct Restore(bool, Option<Spec>, BTreeSet<String>, Option<[u8; 32]>);
    impl Drop for Restore {
        fn drop(&mut self) {
            ACTIVE.set(self.0);
            CONTEXT.set(self.3);
            INTENT.with(|s| *s.borrow_mut() = self.1.take());
            RESTORED.with(|s| *s.borrow_mut() = std::mem::take(&mut self.2));
        }
    }
    let context = Sha256::digest(
        serde_json::to_vec(&(spec, &restored)).expect("incremental intent serializes"),
    )
    .into();
    let _restore = Restore(
        ACTIVE.replace(true),
        INTENT.with(|s| s.replace(Some(spec.clone()))),
        RESTORED.with(|s| s.replace(restored)),
        CONTEXT.replace(Some(context)),
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
    recheck(analysis, &mut after);
    if !crate::engine_pins::loading()
        && before.binding.source_sha256 == analysis.binding.source_sha256
    {
        retain_findings(before, &mut after);
    }
    let mut new: Vec<_> = after
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
    // A source revision re-evaluates ownership/attachment findings rather than carrying
    // diagnoses from old PMI. Previously introduced ink is checked again too.
    if let Some(pins) = &before.spec.engine_pins {
        new.extend(
            pins.annotations
                .iter()
                .filter(|(_, p)| p.introduced)
                .map(|(id, _)| id.clone()),
        );
    }
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
    constrain(&mut after, affected);
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
    crate::engine_pins::persist(analysis, &mut after, Some(before));
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

pub(crate) fn retain(plan: &mut Plan, analysis: &Analysis) {
    crate::engine_pins::apply_loading(plan, analysis);
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
            crate::dims::gdt::retain_region(g, old.basis.contains(crate::dims::interior::BASIS));
            g.view.clone_from(&old.view);
            g.side.clone_from(&old.side);
            g.placed.clone_from(&old.placed);
            if crate::dims::gdt::has_ink(old) {
                g.state.clone_from(&old.state);
            }
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

pub use crate::engine_pins::EnginePins;
#[derive(Clone, Debug, Serialize, Deserialize)]
pub(crate) struct RetiredFindings {
    layout: String,
    problems: Vec<Problem>,
}

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
                crate::items::Op::add("engine_pins/rebase",serde_json::json!(id))
            ])], ..Default::default()
        })
    }).collect()
}

/// Re-evaluate the normal placement predicates after replay has installed final ink.
/// Findings about a provisional route must never survive a retained-layout solve.
pub(crate) fn recheck(a: &Analysis, plan: &mut Plan) {
    plan.problems.retain(|p| {
        !matches!(
            p.code.as_str(),
            "annotation_off_sheet"
                | "annotation_outside_area"
                | "annotation_over_title_block"
                | "annotation_over_view"
                | "annotation_line_over_view"
                | "annotation_overlap"
                | "annotation_ink_overlap"
                | "annotation_leader_crossing"
                | "leader_through_feature"
                | "extension_through_feature"
                | "dimension_line_shared"
                | "dimension_text_stray"
                | "dimension_line_crossing"
        )
    });
    let mut ink = plan.dimensions.clone();
    ink.extend(plan.gdt.iter().filter_map(crate::dims::gdt::proxy));
    let cache = crate::dims::footprints::Cache::default();
    plan.problems.extend(crate::dims::label_collisions(
        a,
        &ink,
        &plan.sheet,
        &plan.views,
        &cache,
    ));
    plan.problems.retain(|p| {
        !(["datum_not_drawn", "pmi_not_drawn"].contains(&p.code.as_str())
            && plan
                .gdt
                .iter()
                .any(|g| p.subjects.contains(&g.id) && crate::dims::gdt::has_ink(g)))
    });
    crate::dims::gdt::cited_undrawn(plan);
    // Centre-line obstacle proxies have an empty text box at [0, 0]. Check their
    // actual curves through the ordinary page-bounds audit, not label bounds.
    model::refresh_line_work_problems(plan);
    crate::dims::contacts::report(a, plan);
    let spec = plan.spec.clone();
    crate::dims::pmi_policy::audit(a, &spec, plan);
    let measured = plan
        .views
        .iter()
        .any(|v| v.basis.contains("automatic orientation view, fitted once"));
    plan.problems.retain(|p| {
        !(measured && p.code == "detail_unplaceable")
            && !(p.code == "spec_entries_inactive"
                && p.refs.iter().all(|id| {
                    !spec.dimensions.contains_key(id)
                        && !spec.annotations.contains_key(id)
                        && !spec.features.contains_key(id)
                }))
    });
    if crate::engine_pins::loading() {
        plan.problems
            .retain(|p| p.code != "incremental_placement_constrained");
        let affected = crate::engine_pins::introduced()
            .into_iter()
            .filter(|id| {
                plan.problems.iter().any(|p| {
                    p.subjects.contains(id)
                        && (p.severity != "info"
                            || p.code.starts_with("annotation_")
                            || p.code == "label_crowded")
                })
            })
            .collect();
        constrain(plan, affected);
    }
    let mut ids = BTreeSet::new();
    plan.problems.retain(|p| ids.insert(p.id.clone()));
    plan.status = crate::completeness::status(&plan.problems);
}

pub(crate) fn constrain(plan: &mut Plan, ids: Vec<String>) {
    for id in ids {
        let problem_id = format!("p:incremental_placement_constrained:{id}");
        if plan.problems.iter().any(|p| p.id == problem_id) {
            continue;
        }
        plan.problems.push(Problem {
            id: problem_id,
            code: "incremental_placement_constrained".into(), severity: "warning".into(),
            message: "New annotation ink could not be placed cleanly in the retained layout. Existing placements were kept; inspect the placement findings or preview Re-layout explicitly.".into(),
            subjects: vec![id], ..Default::default()
        });
    }
    plan.status = crate::completeness::status(&plan.problems);
}

#[cfg(test)]
mod memo_tests {
    use super::*;
    #[test]
    fn active_planning_separates_placement_and_shares_pure_memos() {
        let memo = crate::planning::BoundedMemo::new(4);
        let key = [7; 32];
        let base = crate::planning::spaced_digest(&key);
        memo.insert(base, 10);
        struct Restore(bool, Option<[u8; 32]>);
        impl Drop for Restore {
            fn drop(&mut self) {
                ACTIVE.set(self.0);
                CONTEXT.set(self.1);
            }
        }
        {
            let _restore = Restore(ACTIVE.replace(true), CONTEXT.get());
            let active = crate::planning::spaced_digest(&key);
            assert_eq!(memo.get(&active), None);
            memo.insert(active, 20);
            assert_eq!(memo.get(&active), Some(20));
            // Intent and restored variants are part of every placement key.
            let spec = Spec::default();
            for restored in [BTreeSet::new(), BTreeSet::from(["d:restored".to_string()])] {
                CONTEXT.set(Some(
                    Sha256::digest(serde_json::to_vec(&(&spec, restored)).unwrap()).into(),
                ));
                let context = crate::planning::spaced_digest(&key);
                assert_eq!(memo.get(&context), None);
                memo.insert(context, 30);
                assert_eq!(memo.get(&context), Some(30));
            }
            assert_eq!(memo.get(&base), Some(10), "pure immutable keys stay shared");
        }
        assert_eq!(memo.get(&crate::planning::spaced_digest(&key)), Some(10));
    }
}
