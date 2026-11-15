//! Retained-layout planning uses the ordinary planner's published pin reference.
use std::cell::{Cell, RefCell};
use std::collections::{BTreeMap, BTreeSet};

use crate::model::{self, Analysis, Invalid, Plan, Problem, Spec};
use crate::placement_intent::Place;

thread_local! {
    static INTENT: RefCell<Option<Spec>> = const { RefCell::new(None) };
    static ACTIVE: Cell<bool> = const { Cell::new(false) };
}
pub(crate) fn active() -> bool {
    ACTIVE.get()
}

pub(crate) fn plan(analysis: &Analysis, before: &Plan, spec: &Spec) -> Result<Plan, Invalid> {
    let refuse = |message: &str| Invalid {
        message: format!("Incremental apply {message}; use full apply explicitly."),
        path: None,
        choices: Vec::new(),
        also: Vec::new(),
    };
    if before.binding.source_sha256 != analysis.binding.source_sha256
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
    pinned.sheet.size = Some(before.sheet.size.clone());
    pinned.sheet.scale = Some(before.sheet.scale.clone());
    pinned.sheet.keep_layout = None;
    pinned.sheets = None;
    for d in &before.dimensions {
        // Changing content releases the pin; the normal solver then measures new ink.
        if d.placed.is_some() && same_dimension(before, spec, &d.id) {
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
    let mut reference = before.clone();
    reference.incremental_gdt.clear();
    for g in &before.incremental_gdt {
        if !reference.gdt.iter().any(|b| b.id == g.id) {
            reference.gdt.push(g.clone());
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
    struct Restore(bool, Option<Spec>);
    impl Drop for Restore {
        fn drop(&mut self) {
            ACTIVE.set(self.0);
            INTENT.with(|s| *s.borrow_mut() = self.1.take());
        }
    }
    let _restore = Restore(
        ACTIVE.replace(true),
        INTENT.with(|s| s.replace(Some(spec.clone()))),
    );
    let compact = before.sheet.dimension_spacing == model::DimensionSpacing::Compact;
    let mut after = crate::planning::with_pin_reference(reference.clone(), || {
        crate::dims::with_compact(compact, || model::plan(analysis, &pinned))
    })?;
    if after.views.len() != before.views.len()
        || after.views.iter().any(|v| {
            before.views.iter().find(|b| b.id == v.id).is_none_or(|b| {
                b.at != v.at
                    || b.scale != v.scale
                    || b.direction != v.direction
                    || b.up != v.up
                    || b.sheet != v.sheet
            })
        })
    {
        return Err(refuse("cannot preserve the published view composition"));
    }
    retain_from(&mut after, before, spec);
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
    after.incremental_gdt = reference
        .gdt
        .iter()
        .filter(|old| !after.gdt.iter().any(|g| g.id == old.id))
        .cloned()
        .collect();
    // Pins are transaction-local constraints, never authored Spec edits.
    after.spec = spec.clone();
    after.sheet.size.clone_from(&before.sheet.size);
    after.sheet.scale.clone_from(&before.sheet.scale);
    after.sheet.default = before.sheet.default;
    after.sheet.basis.clone_from(&before.sheet.basis);
    let new: Vec<_> = after
        .gdt
        .iter()
        .filter(|g| {
            !before
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
                    !before
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
            id: "p:incremental_placement_constrained".into(),
            code: "incremental_placement_constrained".into(), severity: "warning".into(),
            message: "New annotation ink could not be placed cleanly in the retained layout. Existing placements were kept; inspect the placement findings or preview an explicit full apply.".into(),
            subjects: affected, ..Default::default()
        });
    }
    after.status = crate::completeness::status(&after.problems);
    Ok(after)
}

fn same_dimension(before: &Plan, spec: &Spec, id: &str) -> bool {
    let same = |id: &str| {
        before.spec.dimensions.get(id).cloned().unwrap_or_default()
            == spec.dimensions.get(id).cloned().unwrap_or_default()
    };
    same(id)
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
    let before = crate::planning::pin_reference()?;
    let spec = intent()?;
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
            .filter(|d| d.placed.is_some() && same_dimension(&before, &spec, &d.id))
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
    let Some(before) = crate::planning::pin_reference() else {
        return Default::default();
    };
    let intended = intent().unwrap_or_else(|| spec.clone());
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
                .filter(|o| same_dimension(&before, spec, &o.id))
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
    let Some(before) = crate::planning::pin_reference() else {
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
    let Some(before) = crate::planning::pin_reference() else {
        return;
    };
    let Some(spec) = intent() else {
        return;
    };
    retain_from(plan, &before, &spec);
}
fn retain_from(plan: &mut Plan, before: &Plan, spec: &Spec) {
    for d in before
        .dimensions
        .iter()
        .filter(|d| same_dimension(before, spec, &d.id))
    {
        plan.dimensions.retain(|a| a.id != d.id);
        plan.dimensions.push(d.clone());
        plan.omitted.retain(|a| a.id != d.id);
    }
    for o in before
        .omitted
        .iter()
        .filter(|o| same_dimension(before, spec, &o.id))
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
