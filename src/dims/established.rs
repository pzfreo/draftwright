//! A complete alternative which gives established annotations first claim.
use std::collections::BTreeSet;

use crate::model::{Analysis, Plan, PlannedView, Problem, Spec};
use crate::placement_intent::{move_box, move_point};

fn hard(p: &Problem) -> bool {
    matches!(
        p.code.as_str(),
        "annotation_ink_overlap"
            | "annotation_overlap"
            | "annotation_off_sheet"
            | "annotation_over_view"
            | "annotation_line_over_view"
            | "annotation_over_title_block"
            | "dimension_line_shared"
            | "dimension_text_stray"
            | "views_do_not_fit"
            | "page_fit_uncertain"
    )
}

fn signatures(problems: &[Problem]) -> BTreeSet<(String, Vec<String>)> {
    problems
        .iter()
        .filter(|p| hard(p))
        .map(|p| {
            let mut ids = p.subjects.clone();
            ids.sort();
            (p.code.clone(), ids)
        })
        .collect()
}

fn delta(before: &Plan, views: &[PlannedView], id: &str) -> Option<[f64; 2]> {
    let a = before.views.iter().find(|v| v.id == id)?;
    let b = views
        .iter()
        .find(|v| v.id == id && v.scale == a.scale && v.direction == a.direction && v.up == a.up)?;
    Some([b.at[0] - a.at[0], b.at[1] - a.at[1]])
}

fn claims(dims: &[super::PlannedDimension]) -> BTreeSet<(String, String, u64)> {
    dims.iter()
        .filter(|d| d.placed.is_some())
        .flat_map(|d| &d.measurements)
        .map(|m| (m.feature.clone(), m.parameter.clone(), m.value.to_bits()))
        .collect()
}

/// Existing leaders may not cross a different physical edge merely to clear
/// a displaced label. Compare translated routes at the same projection/scale.
fn added_route_crossings(analysis: &Analysis, before: &Plan, now: &Plan) -> BTreeSet<String> {
    let Some(ink) = analysis.edge_ink.as_ref() else {
        return BTreeSet::new();
    };
    let mut changed = BTreeSet::new();
    for view in &now.views {
        let model = super::leaders::SegmentIndex::for_view(analysis, ink, view).0;
        for d in now
            .dimensions
            .iter()
            .filter(|d| d.kind == "callout" && d.view == view.id)
        {
            let Some(old) = before
                .dimensions
                .iter()
                .find(|o| o.id == d.id && o.view == d.view)
            else {
                continue;
            };
            let Some(shift) = delta(before, &now.views, &d.view) else {
                continue;
            };
            let (Some(p), Some(old)) = (&d.placed, &old.placed) else {
                continue;
            };
            let mut lines = old.lines.clone();
            for q in lines.iter_mut().flatten() {
                move_point(q, shift);
            }
            if model.new_physical_crossing(&p.lines, &lines) {
                changed.insert(d.id.clone());
            }
        }
    }
    changed
}

/// Re-solve the full inventory around unchanged existing ink. This is a bounded
/// candidate, not an authored pin or a replacement recognition document. It must
/// retain all claims and improve hard findings or established physical routes
/// under the normal complete-drawing checks.
pub(crate) fn retain(analysis: &Analysis, spec: &Spec, current: &Plan) -> Option<Plan> {
    if !current.dimensions.iter().any(|d| {
        d.measurements
            .iter()
            .any(|m| m.feature.starts_with("f:section_recess:"))
    }) || !spec.dimensions.is_empty()
        || !spec.annotations.is_empty()
        || !spec.features.is_empty()
        || !spec.faces.is_empty()
        || spec.views.iter().any(|(name, view)| {
            !crate::detail::NAMES.contains(&name.as_str())
                || *view
                    != crate::model::ViewSpec {
                        omit: true,
                        ..Default::default()
                    }
        })
        || current.views.iter().any(|view| view.detail.is_some())
        || current.hole_table.is_some()
        || !current.hole_tables.is_empty()
        || !current.sheets.is_empty()
    {
        return None;
    }

    let key = crate::model::established_key(analysis, spec);
    let before = crate::planning::lookup::<Result<Plan, crate::model::Invalid>>(
        analysis,
        "established-drawing",
        &key,
    )?
    .ok()?;
    let old_hard = signatures(&before.problems);
    let current_hard = signatures(&current.problems);
    let current_routes = added_route_crossings(analysis, &before, current);
    if current_hard.is_subset(&old_hard) && current_routes.is_empty() {
        return None;
    }
    let route_only = current.gdt.is_empty() && !current_routes.is_empty();
    let mut restore = current_routes.clone();
    if route_only {
        // Restore a displaced label together with the old route it blocks.
        // Every unrelated established item keeps its current clear geometry.
        for id in &current_routes {
            let old = before.dimensions.iter().find(|d| &d.id == id)?;
            let shift = delta(&before, &current.views, &old.view)?;
            let mut p = old.placed.clone()?;
            for q in p.lines.iter_mut().flatten() {
                move_point(q, shift);
            }
            move_box(&mut p.label_box, shift);
            for d in current.dimensions.iter().filter(|d| d.kind == "linear") {
                if d.placed.as_ref().is_some_and(|q| {
                    p.lines
                        .iter()
                        .any(|s| super::leaders::touches_box(*s, q.label_box))
                        || q.lines
                            .iter()
                            .any(|s| super::leaders::touches_box(*s, p.label_box))
                }) {
                    restore.insert(d.id.clone());
                }
            }
        }
    }
    let mut trial = current.clone();
    let mut held = Vec::new();
    let mut held_spec = spec.clone();
    let wants = super::wants(analysis).0;
    for d in &current.dimensions {
        let Some(old) = before.dimensions.iter().find(|o| {
            o.id == d.id
                && o.view == d.view
                && o.measurements.iter().all(|m| d.measurements.contains(m))
        }) else {
            continue;
        };
        let shift = delta(&before, &trial.views, &old.view)?;
        let mut d = d.clone();
        let restored = !route_only || restore.contains(&d.id);
        let mut p = if restored {
            old.placed.clone()?
        } else {
            d.placed.clone()?
        };
        if restored {
            let mut w = wants.iter().find(|w| w.id == d.id)?.clone();
            w.terms.clone_from(&d.terms);
            p = super::placement::relabel(&p, &old.terms, &w);
            // A longer label may outgrow an old inside-arrow span. Leave
            // that item free for the accurate solve instead of pinning its
            // text across its own witnesses or arrowheads.
            if old.terms != d.terms
                && matches!(w.shape, super::Shape::Linear { .. })
                && (p
                    .lines
                    .iter()
                    .take(2)
                    .any(|line| super::placement::crosses(*line, p.label_box))
                    || p.arrows
                        .iter()
                        .flat_map(|a| super::leaders::head(*a))
                        .any(|line| super::placement::crosses(line, p.label_box)))
            {
                continue;
            }
            for q in p.lines.iter_mut().chain(&mut p.arrows).flatten() {
                move_point(q, shift);
            }
            move_point(&mut p.label_at, shift);
            move_box(&mut p.label_box, shift);
            d.region = old.region;
        }
        d.placed = Some(p);
        held_spec.dimensions.entry(d.id.clone()).or_default().place =
            Some(crate::placement_intent::Place {
                pinned: true,
                ..Default::default()
            });
        held.push(d);
    }
    if held.is_empty() {
        return None;
    }
    // A seat's axis coordinates are visible in either orthogonal view that
    // shows the measured axis. New locations may use the less occupied profile;
    // established dimensions and frames keep their views and exact geometry.
    for w in &wants {
        if !route_only
            && w.views.len() > 1
            && w.measurements
                .iter()
                .all(|m| m.feature.starts_with("f:section_recess:"))
            && !held.iter().any(|d| d.id == w.id)
            && current.dimensions.iter().any(|d| d.id == w.id)
            && let Some(view) = w
                .views
                .iter()
                .filter(|name| trial.views.iter().any(|v| v.id == format!("v:{name}")))
                .min_by_key(|name| {
                    let id = format!("v:{name}");
                    held.iter().filter(|d| d.view == id).count()
                        + 2 * before.gdt.iter().filter(|g| g.view == id).count()
                })
        {
            let carrier = current.dimensions.iter().find(|d| d.id == w.id)?;
            for alias in wants.iter().filter(|a| {
                !a.measurements.is_empty()
                    && a.measurements
                        .iter()
                        .all(|m| carrier.measurements.contains(m))
                    && a.views.contains(view)
            }) {
                held_spec
                    .dimensions
                    .entry(alias.id.clone())
                    .or_default()
                    .view = Some((*view).into());
            }
        }
    }
    for g in &mut trial.gdt {
        let old = before
            .gdt
            .iter()
            .find(|o| o.id == g.id && o.view == g.view)?;
        if old.cells != g.cells || old.label != g.label {
            return None;
        }
        let shift = delta(&before, &trial.views, &old.view)?;
        let mut p = old.placed.clone()?;
        move_point(&mut p.tip, shift);
        move_box(&mut p.label_box, shift);
        for q in p
            .lines
            .iter_mut()
            .chain(&mut p.arrows)
            .chain(&mut p.strokes)
            .flatten()
        {
            move_point(q, shift);
        }
        for q in p.fills.iter_mut().flatten() {
            move_point(q, shift);
        }
        for arc in &mut p.arcs {
            arc[0] += shift[0];
            arc[1] += shift[1];
        }
        for t in p.letters.iter_mut().chain(&mut p.runs) {
            move_point(&mut t.at, shift);
        }
        *g = old.clone();
        g.placed = Some(p);
    }
    // Retain the established band boundaries too. Nominal lanes reserved for
    // new items may be unused after their shared solve; only their actual ink
    // can enlarge these bands. Existing fallback control frames keep their
    // honest outside-band provenance rather than acquiring an unused ladder.
    let areas: Option<Vec<_>> = trial
        .views
        .iter()
        .map(|v| {
            if route_only || v.id == "v:iso" {
                return Some(v.area);
            }
            let mut area = before.views.iter().find(|o| o.id == v.id)?.area;
            move_box(&mut area, delta(&before, &trial.views, &v.id)?);
            Some(area)
        })
        .collect();
    for (view, area) in trial.views.iter_mut().zip(areas?) {
        view.area = area;
    }
    let mut fixed: Vec<_> = trial.gdt.iter().filter_map(super::gdt::proxy).collect();
    fixed.extend(crate::centrelines::obstacles(&trial.views));
    fixed.extend(
        trial
            .notes_block
            .iter()
            .chain(&trial.radius_notes_block)
            .map(crate::notes::proxy),
    );
    fixed.extend(trial.tables.iter().filter_map(crate::tables::proxy));
    fixed.extend(
        trial
            .radius_table
            .iter()
            .filter_map(|t| crate::tables::proxy(&t.table)),
    );
    fixed.extend(trial.captions.iter().map(crate::iso_caption::proxy));
    fixed.extend(
        trial
            .requirements
            .iter()
            .filter_map(super::gdt::finish_proxy),
    );
    let cache = super::footprints::Cache::default();
    let ((mut dims, _, _), mut inventory) = super::plan_inventory(
        analysis,
        &held_spec,
        &trial.sheet,
        &trial.views,
        &fixed,
        &held,
        &cache,
        &BTreeSet::new(),
    )
    .ok()?;
    // The finished presentation may carry a radius in a note or table. Its
    // discarded inline proposal is not extra ink for the new dimensions to
    // avoid; the actual note/table already participates through fixed proxies.
    let displayed: BTreeSet<_> = current.dimensions.iter().map(|d| d.id.as_str()).collect();
    dims.retain(|d| displayed.contains(d.id.as_str()));
    inventory.retain(|d| displayed.contains(d.id.as_str()));
    super::settle_inventory(
        analysis,
        &held_spec,
        &trial.sheet,
        &trial.views,
        &mut inventory,
        &fixed,
    );
    super::refine_inventory(
        analysis,
        &held_spec,
        &trial.sheet,
        &trial.views,
        &mut inventory,
        &fixed,
        &cache,
    );
    let growth = crate::nominal::geometry_eligible(analysis) && !crate::nominal::comparison();
    if growth {
        // Retention pins are a solve recipe, not author constraints. Repair the
        // final live leaders against the original Spec after applying that recipe.
        super::settle_after_furniture(
            analysis,
            spec,
            &trial.sheet,
            &trial.views,
            &mut inventory,
            &fixed,
            &cache,
            false,
        );
    }
    for d in &mut dims {
        if let Some(settled) = inventory.iter().find(|s| s.id == d.id) {
            d.placed = settled.placed.clone();
        }
        if let Some(original) = current.dimensions.iter().find(|o| o.id == d.id) {
            d.reference = original.reference;
            d.determined_by = original.determined_by.clone();
            d.default = original.default;
            d.from_spec = original.from_spec.clone();
            d.basis = original.basis.clone();
            // A retention pin changes placement, while the unchanged statement keeps the
            // physically proven aliases acquired during precedence/consolidation.
            if d.kind == original.kind && d.value == original.value && d.terms == original.terms {
                d.measurements.clone_from(&original.measurements);
            }
        }
    }
    if !claims(&current.dimensions).is_subset(&claims(&dims)) {
        return None;
    }
    trial.dimensions = dims;
    for d in trial.dimensions.iter().filter(|d| d.kind == "callout") {
        if let Some(p) = &d.placed
            && let Some(line) = p.lines.first()
            && let Some(v) = trial.views.iter().find(|v| v.id == d.view)
        {
            trial.callout_tips.insert(
                d.id.clone(),
                (d.view.clone(), [line[0][0] - v.at[0], line[0][1] - v.at[1]]),
            );
        }
    }
    let added: Vec<_> = trial
        .dimensions
        .iter()
        .filter(|d| {
            (!d.measurements.is_empty()
                && d.measurements
                    .iter()
                    .all(|m| m.feature.starts_with("f:section_recess:")))
                || (route_only && restore.contains(&d.id))
        })
        .cloned()
        .collect();
    crate::sheet_fit::reserve_annotation_bounds(
        analysis,
        &trial.sheet,
        &mut trial.views,
        &added,
        &trial.gdt,
    );
    if growth {
        crate::sheet_fit::reserve_nominal_bounds(
            analysis,
            &trial.sheet,
            &mut trial.views,
            &trial.dimensions,
            &trial.gdt,
        );
    }
    let ids: BTreeSet<_> = trial
        .dimensions
        .iter()
        .map(|d| d.id.clone())
        .chain(trial.gdt.iter().map(|g| g.id.clone()))
        .collect();
    trial.problems.retain(|p| {
        !(p.subjects.iter().any(|id| ids.contains(id))
            && (p.code.starts_with("annotation_")
                || matches!(
                    p.code.as_str(),
                    "leader_through_feature"
                        | "extension_through_feature"
                        | "dimension_line_shared"
                        | "dimension_text_stray"
                )))
    });
    let mut ink = trial.dimensions.clone();
    ink.extend(fixed.iter().filter(|d| d.kind != "ink").cloned());
    trial.problems.extend(super::label_collisions(
        analysis,
        &ink,
        &trial.sheet,
        &trial.views,
        &cache,
    ));
    let new_hard = signatures(&trial.problems);
    let rank = |h: &BTreeSet<_>| (h.difference(&old_hard).count(), h.len());
    let new_routes = added_route_crossings(analysis, &before, &trial);
    if (rank(&new_hard), new_routes.len()) >= (rank(&current_hard), current_routes.len())
        || (route_only && !new_routes.is_subset(&current_routes))
    {
        return None;
    }
    if current.gdt.is_empty() {
        // A route-only repair must improve the complete drawing, not trade
        // material crossings for annotation contacts or lost span association.
        let contacts = |p: &Plan| super::extension_contacts(&p.dimensions);
        let findings = |p: &Plan, codes: &[&str]| {
            p.problems
                .iter()
                .filter(|q| codes.contains(&q.code.as_str()))
                .count()
        };
        if contacts(&trial) > contacts(current)
            || findings(
                &trial,
                &["annotation_leader_crossing", "annotation_outside_area"],
            ) > findings(
                current,
                &["annotation_leader_crossing", "annotation_outside_area"],
            )
            || findings(
                &trial,
                &["leader_through_feature", "extension_through_feature"],
            ) > findings(
                current,
                &["leader_through_feature", "extension_through_feature"],
            )
        {
            return None;
        }
    }
    trial.sheet.basis +=
        "; existing dimension and GD&T corridors retained while solving the new recess ink";
    Some(trial)
}
