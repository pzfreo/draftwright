//! Final, bounded route repair against the complete printed inventory (#147).
//! Geometry producers remain in `leaders`; datum triangles never move here.
use std::collections::{BTreeMap, BTreeSet};

use super::{Placed, PlannedDimension, gdt, interior, leaders, placement};
use crate::model::{Analysis, Plan, Problem, Spec};

/// Counts every crossing kind independently, plus every other problem code.
/// Trading a leader/leader crossing for a leader through text is never an improvement.
fn counts(problems: &[Problem]) -> BTreeMap<String, usize> {
    let mut out = BTreeMap::new();
    for p in problems {
        *out.entry(p.code.clone()).or_default() += 1;
        for kind in &p.crossing_kinds {
            *out.entry(format!("crossing:{kind}")).or_default() += 1;
        }
    }
    out
}

fn route_issue(code: &str) -> bool {
    matches!(
        code,
        "annotation_leader_crossing"
            | "annotation_ink_overlap"
            | "annotation_overlap"
            | "annotation_over_view"
            | "annotation_line_over_view"
            | "annotation_leader_long"
    )
}

fn improves(before: &[Problem], after: &[Problem]) -> bool {
    improves_with_contacts(before, after, false)
}

fn improves_with_contacts(before: &[Problem], after: &[Problem], contacts: bool) -> bool {
    let a = counts(before);
    let b = counts(after);
    let defects = |counts: &BTreeMap<String, usize>| {
        counts
            .iter()
            .filter(|(code, _)| {
                route_issue(code)
                    || code.as_str() == "label_crowded"
                    || (contacts
                        && matches!(
                            code.as_str(),
                            "leader_along_centreline" | "annotation_outside_area"
                        ))
            })
            .map(|(_, n)| n)
            .sum::<usize>()
    };
    b.iter().all(|(k, n)| *n <= a.get(k).copied().unwrap_or(0)) && defects(&b) < defects(&a)
}

fn no_regression(before: &[Problem], after: &[Problem]) -> bool {
    let (a, b) = (counts(before), counts(after));
    b.iter().all(|(k, n)| *n <= a.get(k).copied().unwrap_or(0))
}

type Extensions = Vec<(usize, [[f64; 2]; 2])>;
fn extension_sets(plan: &Plan) -> BTreeMap<u32, Extensions> {
    let mut out: BTreeMap<u32, Extensions> = BTreeMap::new();
    for (i, d) in plan.dimensions.iter().enumerate() {
        if let Some(p) = &d.placed
            && p.arrows.len() == 2
            && p.lines.len() >= 3
        {
            let page = plan
                .views
                .iter()
                .find(|v| v.id == d.view)
                .map_or(1, |v| v.sheet);
            out.entry(page)
                .or_default()
                .extend(p.lines[..2].iter().map(|e| (i, *e)));
        }
    }
    out
}

/// The complete ring/tag and shaft of a schedule balloon are fixed annotation ink.
pub(super) fn balloon_obstacle(b: &crate::hole_table::Balloon) -> PlannedDimension {
    let [x, y] = b.at;
    let r = b.radius;
    let ring = [x - r, y - r, x + r, y + r];
    let label = b.label_box.unwrap_or_else(|| {
        let ink = super::label_ink(std::slice::from_ref(&b.tag), super::font());
        let w = 0.5 * (ink.max[0] - ink.min[0]);
        let h = 0.5 * (ink.max[1] - ink.min[1]);
        [x - w, y - h, x + w, y + h]
    });
    let box_ = [
        label[0].min(ring[0]),
        label[1].min(ring[1]),
        label[2].max(ring[2]),
        label[3].max(ring[3]),
    ];
    let mut lines = Vec::new();
    if r > 0.0 {
        let corners = [
            [x - r, y - r],
            [x + r, y - r],
            [x + r, y + r],
            [x - r, y + r],
        ];
        lines.extend((0..4).map(|i| [corners[i], corners[(i + 1) % 4]]));
    }
    let mut arrows = Vec::new();
    if let Some([tip, end]) = b.leader {
        lines.push([tip, end]);
        arrows.push(placement::arrow(tip, end));
    }
    PlannedDimension {
        id: b.id.clone(),
        kind: "ink".into(),
        view: b.view.clone(),
        value: 0.0,
        label: b.tag.clone(),
        terms: vec![b.tag.clone()],
        measurements: Vec::new(),
        default: true,
        from_spec: Vec::new(),
        basis: "balloon".into(),
        state: b.state.clone(),
        region: None,
        reference: false,
        determined_by: Vec::new(),
        placed: Some(Placed {
            label_box: box_,
            label_at: b.at,
            rotation: 0.0,
            lines,
            arrows,
        }),
    }
}

pub(super) fn furniture(plan: &Plan) -> Vec<PlannedDimension> {
    let mut fixed = crate::centrelines::obstacles(&plan.views);
    fixed.extend(plan.tables.iter().filter_map(crate::tables::proxy));
    fixed.extend(
        plan.hole_table
            .iter()
            .chain(&plan.hole_tables)
            .flat_map(|t| &t.blocks)
            .filter_map(crate::tables::proxy),
    );
    fixed.extend(plan.balloons.iter().map(balloon_obstacle));
    for block in [&plan.notes_block, &plan.radius_notes_block]
        .into_iter()
        .flatten()
    {
        fixed.push(crate::notes::proxy(block));
    }
    if let Some(table) = &plan.radius_table {
        fixed.extend(crate::tables::proxy(&table.table));
    }
    fixed.extend(plan.requirements.iter().filter_map(gdt::finish_proxy));
    fixed.extend(plan.captions.iter().map(crate::iso_caption::proxy));
    // Triangles stay fixed; all-around marks travel with their frame's bend.
    // Reserve both independently of the frame's label box.
    for g in &plan.gdt {
        if let Some(p) = &g.placed {
            let mut shapes: Vec<_> = p
                .fills
                .iter()
                .enumerate()
                .map(|(i, fill)| (format!("{}:triangle:{i}", g.id), fill.clone()))
                .collect();
            let count = g
                .modifiers
                .iter()
                .map(|m| if m == "all_over" { 2 } else { 1 })
                .sum();
            for (i, arc) in p.arcs.iter().rev().take(count).enumerate() {
                let [x, y, r, _, _] = *arc;
                shapes.push((
                    format!("{}:modifier:{i}", g.id),
                    vec![
                        [x - r, y - r],
                        [x + r, y - r],
                        [x + r, y + r],
                        [x - r, y + r],
                    ],
                ));
            }
            for (id, fill) in shapes {
                let mut d = gdt::proxy(g).expect("a placed glyph has a proxy");
                d.id = id;
                d.kind = "ink".into();
                let b = fill.iter().fold(
                    [
                        f64::INFINITY,
                        f64::INFINITY,
                        f64::NEG_INFINITY,
                        f64::NEG_INFINITY,
                    ],
                    |b, p| {
                        [
                            b[0].min(p[0]),
                            b[1].min(p[1]),
                            b[2].max(p[0]),
                            b[3].max(p[1]),
                        ]
                    },
                );
                let p = d.placed.as_mut().expect("the proxy retains its placement");
                p.label_box = b;
                p.lines = (0..fill.len())
                    .map(|i| [fill[i], fill[(i + 1) % fill.len()]])
                    .collect();
                p.arrows.clear();
                fixed.push(d);
            }
        }
    }
    fixed
}

fn rehang(g: &mut gdt::PlannedGdt, next: &Placed) {
    let p = g.placed.as_mut().expect("placed glyph");
    let delta = [
        next.label_box[0] - p.label_box[0],
        next.label_box[1] - p.label_box[1],
    ];
    let shift = |q: &mut [f64; 2]| {
        q[0] += delta[0];
        q[1] += delta[1];
    };
    // The glyph and text move as one rigid object: all compartment widths and 3 mm text stay.
    for line in &mut p.strokes {
        for q in line {
            shift(q);
        }
    }
    for arc in &mut p.arcs {
        arc[0] += delta[0];
        arc[1] += delta[1];
    }
    for text in p.letters.iter_mut().chain(&mut p.runs) {
        shift(&mut text.at);
    }
    if g.kind != "datum_feature" {
        let old_elbow = p.lines.first().map(|s| s[1]);
        let new_elbow = next.lines.first().map(|s| s[1]);
        // All-around/all-over rings belong at the bend, rather than on the frame.
        if let (Some(a), Some(b)) = (old_elbow, new_elbow) {
            let count = g
                .modifiers
                .iter()
                .map(|m| if m == "all_over" { 2 } else { 1 })
                .sum::<usize>();
            for arc in p.arcs.iter_mut().rev().take(count) {
                arc[0] += b[0] - a[0] - delta[0];
                arc[1] += b[1] - a[1] - delta[1];
            }
        }
        p.tip = next.lines[0][0];
    }
    p.lines.clone_from(&next.lines);
    p.arrows.clone_from(&next.arrows);
    p.label_box = next.label_box;
}

/// Feature callouts over blank face (maintainer 2026-10-25: more labels over the interior). Each
/// automatic feature callout drawn outside its view takes the best interior route the
/// settled-route sweep offers, when its whole label is on proven blank face, its leader is no
/// longer and none of its findings grows, code by code. Linear dimensions stay outside.
///
/// *natural* is the same request finished without the Spec's omissions. When it shares this
/// drawing's paper and view positions, its moves are copied for the items both draw and no other
/// item moves, so omitting one item moves no other.
pub(crate) fn prefer_interior(
    analysis: &Analysis,
    spec: &Spec,
    plan: &mut Plan,
    natural: Option<&Plan>,
) {
    if crate::nominal::comparison() {
        return;
    }
    let cache = super::footprints::Cache::default();
    if let Some(n) = natural.filter(|n| {
        n.sheet.size == plan.sheet.size
            && n.sheet.scale == plan.sheet.scale
            && n.views.len() == plan.views.len()
            && n.views
                .iter()
                .zip(&plan.views)
                .all(|(a, b)| a.id == b.id && a.at == b.at)
    }) {
        let mut all = plan.dimensions.clone();
        all.extend(plan.gdt.iter().filter_map(gdt::proxy));
        let mut changed = BTreeSet::new();
        let mut rechecked_codes = BTreeSet::new();
        for i in 0..plan.dimensions.len() {
            let d = &plan.dimensions[i];
            let Some(q) = n.dimensions.iter().find(|q| {
                q.id == d.id
                    && q.region == Some(super::CandidateRegion::Interior)
                    && q.basis.contains(interior::BASIS)
            }) else {
                continue;
            };
            if d.kind != "callout"
                || d.region == Some(super::CandidateRegion::Interior)
                || d.placed == q.placed
                || spec
                    .dimensions
                    .get(&d.id)
                    .is_some_and(|s| s.place.is_some())
            {
                continue;
            }
            let Some(sheet) = plan.views.iter().find(|v| v.id == d.view).and_then(|v| {
                std::iter::once(&plan.sheet)
                    .chain(&plan.sheets)
                    .find(|s| s.number == v.sheet)
            }) else {
                continue;
            };
            rechecked_codes.extend(
                placement::collisions_for(analysis, &all, sheet, &plan.views, &cache, &d.id)
                    .into_iter()
                    .map(|p| p.code),
            );
            all[i].placed.clone_from(&q.placed);
            all[i].region = q.region;
            let d = &mut plan.dimensions[i];
            d.placed.clone_from(&q.placed);
            d.region = q.region;
            d.basis.clone_from(&q.basis);
            if let Some(t) = n.callout_tips.get(&d.id) {
                plan.callout_tips.insert(d.id.clone(), t.clone());
            }
            changed.insert(d.id.clone());
        }
        refresh(analysis, plan, &all, &cache, &changed, &rechecked_codes);
        return;
    }
    let proofs = interior::callout_proofs(analysis, &plan.views);
    let mut wants = super::wants(analysis).0;
    let frames: BTreeMap<_, _> = plan
        .views
        .iter()
        .filter_map(|v| {
            let name = v.id.strip_prefix("v:")?;
            super::NAMES
                .contains(&name)
                .then(|| (name, placement::Frame::new(analysis, v)))
        })
        .collect();
    super::detail_routes(analysis, &mut wants, &plan.views);
    super::route(&mut wants, &frames, analysis);
    let fixed = furniture(plan);
    let mut all = plan.dimensions.clone();
    all.extend(plan.gdt.iter().filter_map(gdt::proxy));
    let mut changed = BTreeSet::new();
    let mut rechecked_codes = BTreeSet::new();
    let length = |p: &Placed| {
        p.lines
            .iter()
            .map(|[a, b]| (b[0] - a[0]).hypot(b[1] - a[1]))
            .sum::<f64>()
    };
    // Repeat until nothing moves, so finishing a finished drawing changes nothing: a callout
    // moved inside can free the room another one needs.
    for _ in 0..4 {
        let moved = changed.len();
        for i in 0..plan.dimensions.len() {
            let d = &all[i];
            let Some(old) = d.placed.clone() else {
                continue;
            };
            if d.kind != "callout"
                || d.region == Some(super::CandidateRegion::Interior)
                || spec
                    .dimensions
                    .get(&d.id)
                    .is_some_and(|s| s.place.is_some())
            {
                continue;
            }
            let Some(view) = plan.views.iter().find(|v| v.id == d.view) else {
                continue;
            };
            let (Some(frame), Some(proof)) = (
                frames.get(view.id.trim_start_matches("v:")),
                proofs.get(&view.id),
            ) else {
                continue;
            };
            if placement::boxes_overlap(old.label_box, frame.outline()) {
                continue;
            }
            let Some(sheet) = std::iter::once(&plan.sheet)
                .chain(&plan.sheets)
                .find(|s| s.number == view.sheet)
            else {
                continue;
            };
            let Some(want) = wants.iter().find(|w| w.id == d.id) else {
                continue;
            };
            let peers: Vec<_> = all
                .iter()
                .enumerate()
                .filter(|(j, q)| {
                    *j != i
                        && plan
                            .views
                            .iter()
                            .find(|v| v.id == q.view)
                            .is_none_or(|v| v.sheet == sheet.number)
                })
                .map(|(_, q)| q.clone())
                .chain(fixed.iter().cloned())
                .collect();
            let candidates = leaders::settled_routes(
                analysis,
                sheet,
                &plan.views,
                d,
                Some(want),
                &peers,
                Some(proof.as_ref()),
            );
            let id = d.id.clone();
            let found = placement::collisions_for(analysis, &all, sheet, &plan.views, &cache, &id);
            let before = counts(&found);
            for next in candidates.into_iter().take(40) {
                if !placement::boxes_overlap(next.label_box, frame.outline())
                    || !proof.annotation_clear(view, &next, true)
                    || length(&next) > length(&old) + 1e-6
                {
                    continue;
                }
                all[i].placed = Some(next.clone());
                let after = counts(&placement::collisions_for(
                    analysis,
                    &all,
                    sheet,
                    &plan.views,
                    &cache,
                    &id,
                ));
                if after
                    .iter()
                    .all(|(code, n)| before.get(code).is_some_and(|m| n <= m))
                {
                    rechecked_codes.extend(found.iter().map(|p| p.code.clone()));
                    changed.insert(id.clone());
                    let d = &mut plan.dimensions[i];
                    d.placed = Some(next.clone());
                    d.region = Some(super::CandidateRegion::Interior);
                    if !d.basis.contains(interior::BASIS) {
                        d.basis += interior::BASIS;
                    }
                    all[i].region = d.region;
                    plan.callout_tips.insert(
                        id.clone(),
                        (
                            d.view.clone(),
                            [
                                next.lines[0][0][0] - view.at[0],
                                next.lines[0][0][1] - view.at[1],
                            ],
                        ),
                    );
                    break;
                }
                all[i].placed = Some(old.clone());
            }
        }
        if changed.len() == moved {
            break;
        }
    }
    refresh(analysis, plan, &all, &cache, &changed, &rechecked_codes);
}

/// A moved callout's collision findings, measured again where it now stands.
pub(super) fn refresh(
    analysis: &Analysis,
    plan: &mut Plan,
    all: &[PlannedDimension],
    cache: &super::footprints::Cache,
    changed: &BTreeSet<String>,
    rechecked_codes: &BTreeSet<String>,
) {
    if changed.is_empty() {
        return;
    }
    let checked: Vec<_> = std::iter::once(&plan.sheet)
        .chain(&plan.sheets)
        .flat_map(|s| placement::collisions_cached(analysis, all, s, &plan.views, cache))
        .collect();
    plan.problems.retain(|p| {
        !p.subjects.iter().any(|s| changed.contains(s)) || !rechecked_codes.contains(&p.code)
    });
    plan.problems.extend(checked.into_iter().filter(|p| {
        p.subjects.iter().any(|s| changed.contains(s)) && rechecked_codes.contains(&p.code)
    }));
    plan.sheet.fill = crate::sheet_fit::fill(analysis, plan);
    plan.status = crate::completeness::status(&plan.problems);
}

/// Final text clearance is inspected on the same filled arrowheads and
/// labels as the reported contacts, after local opening dimensions are added.
fn crowding(
    analysis: &Analysis,
    plan: &Plan,
    all: &[PlannedDimension],
    dimension_count: usize,
    id: &str,
) -> Vec<Problem> {
    let mut candidate = plan.clone();
    candidate.dimensions = all[..dimension_count].to_vec();
    super::contacts::report(analysis, &mut candidate);
    candidate
        .problems
        .into_iter()
        .filter(|p| {
            matches!(
                p.code.as_str(),
                "label_crowded"
                    | "leader_across_view"
                    | "leader_to_hidden_edge"
                    | "leader_along_centreline"
            ) && p.subjects.iter().any(|s| s == id)
        })
        .collect()
}

pub(crate) fn finish(analysis: &Analysis, spec: &Spec, plan: &mut Plan) {
    finish_with(analysis, spec, plan, false, None);
}

/// Repair independent frames after their neighbours have attached as units.
/// Size labels and attached hole frames remain fixed together.
pub(crate) fn finish_frames(analysis: &Analysis, spec: &Spec, plan: &mut Plan) {
    finish_with(analysis, spec, plan, true, None);
}

/// Repair only contacts newly introduced by a paper/scale candidate. Existing
/// contacts in the displaced drawing keep the established routing policy.
pub(crate) fn finish_new_contacts(
    analysis: &Analysis,
    spec: &Spec,
    plan: &mut Plan,
    ids: &BTreeSet<String>,
) {
    finish_with(analysis, spec, plan, false, Some(ids));
}

fn finish_with(
    analysis: &Analysis,
    spec: &Spec,
    plan: &mut Plan,
    only_frames: bool,
    new_contacts: Option<&BTreeSet<String>>,
) {
    let pmi_retry = placement::pmi_witness_retry(analysis);
    let openings = plan.dimensions.iter().any(|d| {
        d.measurements
            .iter()
            .any(|m| m.feature.starts_with("f:rectangular_through_notch:"))
    });
    let repair_issue = |code: &str| {
        route_issue(code)
            || (openings && code == "label_crowded")
            || (new_contacts.is_some()
                && matches!(code, "leader_along_centreline" | "annotation_outside_area"))
    };
    // Added lanes must not leave an otherwise unreported leader alongside
    // an extension. Use the placer's existing one-mm contact predicate.
    let extension_sets = if openings || pmi_retry {
        extension_sets(plan)
    } else {
        BTreeMap::new()
    };
    // Attached marks move with their authored unit. Before attachment, PMI
    // callouts may retry their changed witness corridors; the ordinary hole
    // attachment pass then proves a place for each frame beside the new label.
    let coupled: BTreeSet<_> = if (openings || pmi_retry)
        && !plan.gdt.is_empty()
        && (!pmi_retry || plan.gdt.iter().any(super::hole_marks::attached))
    {
        super::hole_marks::attachments(analysis, plan)
            .into_iter()
            .filter(|(id, _)| {
                !pmi_retry
                    || plan
                        .gdt
                        .iter()
                        .any(|g| &g.id == id && super::hole_marks::attached(g))
            })
            .filter_map(|(_, callout)| callout)
            .collect()
    } else {
        BTreeSet::new()
    };
    let extension_bad: BTreeSet<_> = plan
        .dimensions
        .iter()
        .filter(|d| {
            d.kind == "callout"
                && !coupled.contains(&d.id)
                && (pmi_retry || !d.measurements.iter().any(|m| m.feature.starts_with("pmi:")))
        })
        .filter_map(|d| {
            let p = d.placed.as_ref()?;
            let page = plan.views.iter().find(|v| v.id == d.view)?.sheet;
            let extensions = extension_sets.get(&page)?;
            (leaders::extension_contacts(extensions, &p.lines, p.label_box).0 > 0)
                .then(|| d.id.clone())
        })
        .collect();
    let initial_contacts = if openings || new_contacts.is_some() {
        super::contacts::contacts(plan)
    } else {
        Vec::new()
    };
    if crate::nominal::comparison()
        || crate::planning::lookup::<bool>(analysis, "canonical-circular-recipes", &[])
            .unwrap_or(false)
        || (!plan
            .problems
            .iter()
            .chain(&initial_contacts)
            .any(|p| repair_issue(&p.code))
            && extension_bad.is_empty())
    {
        return;
    }
    let mut affected_ids: BTreeSet<_> = plan
        .problems
        .iter()
        .chain(&initial_contacts)
        .filter(|p| repair_issue(&p.code))
        .flat_map(|p| p.subjects.iter().cloned())
        .collect();
    affected_ids.extend(extension_bad.iter().cloned());
    if let Some(ids) = new_contacts {
        affected_ids.retain(|id| ids.contains(id));
    }
    if only_frames
        && !plan.gdt.iter().any(|g| {
            g.placed.is_some()
                && affected_ids.contains(&g.id)
                && (!super::hole_marks::attached(g) || g.side == "leader")
        })
    {
        return;
    }
    if !plan
        .dimensions
        .iter()
        .any(|d| d.kind == "callout" && d.placed.is_some() && affected_ids.contains(&d.id))
        && !plan.gdt.iter().any(|g| {
            g.kind != "surface_finish" && g.placed.is_some() && affected_ids.contains(&g.id)
        })
    {
        return;
    }
    let cache = super::footprints::Cache::default();
    let proofs = interior::proofs(analysis, &plan.views);
    let exterior = if plan.gdt.iter().any(|g| g.kind == "datum_feature") {
        interior::exterior_proofs(analysis, &plan.views)
    } else {
        BTreeMap::new()
    };
    let mut wants = super::wants(analysis).0;
    let frames = plan
        .views
        .iter()
        .filter_map(|v| {
            let name = super::NAMES
                .iter()
                .chain(crate::detail::NAMES.iter())
                .copied()
                .find(|name| v.id.strip_prefix("v:") == Some(*name))?;
            Some((name, placement::Frame::new(analysis, v)))
        })
        .collect();
    super::detail_routes(analysis, &mut wants, &plan.views);
    super::route(&mut wants, &frames, analysis);
    let mut fixed = furniture(plan);
    let mut all = plan.dimensions.clone();
    all.extend(plan.gdt.iter().filter_map(gdt::proxy));
    let dimension_count = plan.dimensions.len();
    let mut changed = BTreeSet::new();
    let mut rechecked_codes = BTreeSet::new();
    // Settle the established straight-normal/frame/callout inventory first.
    // Bends may then improve datums against those fixed routes, without taking
    // a corridor a later frame needed to escape a worse initial arrangement.
    for bends in [false, true] {
        if bends && !plan.gdt.iter().any(|g| g.kind == "datum_feature") {
            continue;
        }
        for _ in 0..2 {
            let mut moved = false;
            for i in 0..all.len() {
                let d = &all[i];
                if (bends && !d.id.starts_with("pmi:datum."))
                    || (only_frames
                        && (i < dimension_count
                            || plan.gdt.iter().find(|g| g.id == d.id).is_some_and(|g| {
                                super::hole_marks::attached(g) && g.side != "leader"
                            })))
                    || !affected_ids.contains(&d.id)
                    || d.kind != "callout"
                    || d.placed.is_none()
                    || spec
                        .dimensions
                        .get(&d.id)
                        .is_some_and(|s| s.place.is_some())
                    || spec
                        .annotations
                        .get(&d.id)
                        .is_some_and(|s| s.place.is_some())
                {
                    continue;
                }
                let Some(view) = plan.views.iter().find(|v| v.id == d.view) else {
                    continue;
                };
                let Some(sheet) = std::iter::once(&plan.sheet)
                    .chain(&plan.sheets)
                    .find(|s| s.number == view.sheet)
                else {
                    continue;
                };
                let mut before =
                    placement::collisions_for(analysis, &all, sheet, &plan.views, &cache, &d.id);
                if (openings || new_contacts.is_some()) && i < dimension_count {
                    before.extend(crowding(analysis, plan, &all, dimension_count, &d.id));
                }
                let extension_retry = only_frames || extension_bad.contains(&d.id);
                let extension_budget = |p: &Placed| {
                    if !extension_retry {
                        return (0, 0);
                    }
                    extension_sets.get(&sheet.number).map_or((0, 0), |e| {
                        leaders::extension_contacts(e, &p.lines, p.label_box)
                    })
                };
                let before_extension = extension_budget(d.placed.as_ref().expect("placed callout"));
                if !before.iter().any(|p| repair_issue(&p.code)) && before_extension.0 == 0 {
                    continue;
                }
                let glyph = plan.gdt.iter().find(|g| g.id == d.id).cloned();
                let want = wants.iter().find(|w| w.id == d.id);
                if glyph.is_none() && want.is_none() {
                    continue;
                }
                // Finishes have a prescribed outward normal, independent of leadered labels.
                if glyph.as_ref().is_some_and(|g| g.kind == "surface_finish") {
                    continue;
                }
                let triangle_prefix = format!("{}:triangle:", d.id);
                let modifier_prefix = format!("{}:modifier:", d.id);
                let peers: Vec<_> = all
                    .iter()
                    .enumerate()
                    .filter(|(j, q)| {
                        *j != i
                            && plan
                                .views
                                .iter()
                                .find(|v| v.id == q.view)
                                .is_none_or(|v| v.sheet == sheet.number)
                    })
                    .map(|(_, q)| q.clone())
                    .chain(
                        fixed
                            .iter()
                            .filter(|q| {
                                !q.id.starts_with(&triangle_prefix)
                                    && !q.id.starts_with(&modifier_prefix)
                            })
                            .cloned(),
                    )
                    .collect();
                let mut candidates = if glyph.as_ref().is_some_and(|g| g.kind == "datum_feature") {
                    gdt::normal_routes(glyph.as_ref().expect("datum"), bends)
                } else {
                    leaders::settled_routes(
                        analysis,
                        sheet,
                        &plan.views,
                        d,
                        want,
                        &peers,
                        proofs.get(&view.id).map(|p| p.as_ref()),
                    )
                };
                if before_extension.0 > 0 {
                    candidates.sort_by_cached_key(&extension_budget);
                }
                let id = d.id.clone();
                let old = d.placed.clone();
                let mut grown_area = None;
                let limit = if glyph.as_ref().is_some_and(|g| g.kind == "datum_feature") {
                    candidates.len()
                } else {
                    40
                };
                for next in candidates.into_iter().take(limit) {
                    // The all-around/all-over marks travel with the bend. Reserve
                    // their actual extent as well as the proxy's shaft and frame.
                    if let Some(g) = glyph.as_ref().filter(|g| !g.modifiers.is_empty()) {
                        let ring = if g.modifiers.iter().any(|m| m == "all_over") {
                            1.7 * 0.42 * super::TEXT
                        } else {
                            0.42 * super::TEXT
                        } + 0.5;
                        let bend = next.lines[0][1];
                        let b = [
                            bend[0] - ring,
                            bend[1] - ring,
                            bend[0] + ring,
                            bend[1] + ring,
                        ];
                        let ring_clear = proofs.get(&view.id).map_or_else(
                            || {
                                frames
                                    .get(view.id.trim_start_matches("v:"))
                                    .is_none_or(|f| !placement::boxes_overlap(b, f.outline()))
                            },
                            |p| p.blank(view, b) || p.exterior_clear(view, b, &[], &[]),
                        );
                        if !ring_clear
                            || peers.iter().filter_map(|d| d.placed.as_ref()).any(|p| {
                                placement::boxes_overlap(b, p.label_box)
                                    || p.lines
                                        .iter()
                                        .chain(&p.arrows)
                                        .any(|s| leaders::touches_box(*s, b))
                            })
                            || (proofs
                                .get(&view.id)
                                .is_some_and(|p| p.blank(view, next.label_box))
                                && !proofs[&view.id].blank(view, b))
                        {
                            continue;
                        }
                    }
                    // Full sheet furniture is fixed even when collision reporting only
                    // enumerates dimensions and PMI. Keep the existing 1 mm text gap.
                    let pad = [
                        next.label_box[0] - 1.,
                        next.label_box[1] - 1.,
                        next.label_box[2] + 1.,
                        next.label_box[3] + 1.,
                    ];
                    if peers.iter().filter_map(|q| q.placed.as_ref()).any(|q| {
                        placement::boxes_overlap(pad, q.label_box)
                            || q.lines
                                .iter()
                                .chain(&q.arrows)
                                .any(|s| placement::crosses(*s, pad))
                            || next
                                .lines
                                .iter()
                                .any(|s| placement::crosses(*s, q.label_box))
                    }) {
                        continue;
                    }

                    all[i].placed = Some(next.clone());
                    // A free source callout can need a little more of its view's
                    // annotation band after witness lanes move. Reuse the hole
                    // attachment proof: grow only into vacant drawable paper.
                    let area = if pmi_retry && extension_retry && i < dimension_count {
                        super::ink_box(&all[i]).and_then(|ink| {
                            super::hole_marks::grown(view, ink, sheet, &plan.views, &peers)
                        })
                    } else {
                        None
                    };
                    let trial_views = area.map(|area| {
                        let mut views = plan.views.clone();
                        views.iter_mut().find(|v| v.id == view.id).unwrap().area = area;
                        views
                    });
                    let mut after = placement::collisions_for(
                        analysis,
                        &all,
                        sheet,
                        trial_views.as_deref().unwrap_or(&plan.views),
                        &cache,
                        &id,
                    );
                    if (openings || new_contacts.is_some()) && i < dimension_count {
                        after.extend(crowding(analysis, plan, &all, dimension_count, &id));
                    }
                    let next_extension = extension_budget(&next);
                    if next_extension.0 <= before_extension.0
                        && next_extension.1 <= before_extension.1
                        && ((if new_contacts.is_some() {
                            improves_with_contacts(&before, &after, true)
                        } else {
                            improves(&before, &after)
                        }) || (next_extension.0 < before_extension.0
                            && no_regression(&before, &after)))
                    {
                        rechecked_codes.extend(before.iter().chain(&after).map(|p| p.code.clone()));
                        if i < dimension_count {
                            let d = &mut plan.dimensions[i];
                            let inside = proofs
                                .get(&view.id)
                                .is_some_and(|p| p.annotation_clear(view, &next, true));
                            d.region = inside.then_some(super::CandidateRegion::Interior);
                            if inside {
                                if !d.basis.contains(interior::BASIS) {
                                    d.basis += interior::BASIS;
                                }
                            } else {
                                d.basis = d.basis.replace(interior::BASIS, "");
                            }
                            d.placed = Some(next.clone());
                            plan.callout_tips.insert(
                                id.clone(),
                                (
                                    d.view.clone(),
                                    [
                                        next.lines[0][0][0] - view.at[0],
                                        next.lines[0][0][1] - view.at[1],
                                    ],
                                ),
                            );
                        } else if let Some(g) = plan.gdt.iter_mut().find(|g| g.id == id) {
                            rehang(g, &next);
                            let inside = proofs
                                .get(&view.id)
                                .is_some_and(|p| p.blank(view, next.label_box))
                                || (g.kind == "datum_feature"
                                    && exterior
                                        .get(&view.id)
                                        .is_some_and(|p| p.exterior_annotation_clear(view, &next)));
                            let inside =
                                inside
                                    && frames.get(view.id.trim_start_matches("v:")).is_some_and(
                                        |f| placement::boxes_overlap(next.label_box, f.outline()),
                                    );
                            if inside {
                                if !g.basis.contains(interior::BASIS) {
                                    g.basis += interior::BASIS;
                                }
                            } else {
                                g.basis = g.basis.replace(interior::BASIS, "");
                            }
                            if g.kind != "datum_feature"
                                && !inside
                                && let Some(frame) = frames.get(view.id.trim_start_matches("v:"))
                            {
                                let o = frame.outline();
                                let b = next.label_box;
                                g.side = if b[2] <= o[0] {
                                    "left"
                                } else if b[0] >= o[2] {
                                    "right"
                                } else if b[1] >= o[3] {
                                    "above"
                                } else {
                                    "below"
                                }
                                .into();
                            }
                        }
                        if glyph.as_ref().is_some_and(|g| !g.modifiers.is_empty()) {
                            fixed = furniture(plan);
                        }
                        changed.insert(id.clone());
                        grown_area = area.map(|area| (view.id.clone(), area));
                        moved = true;
                        break;
                    }
                    all[i].placed.clone_from(&old);
                }
                if let Some((id, area)) = grown_area {
                    plan.views.iter_mut().find(|v| v.id == id).unwrap().area = area;
                }
            }
            if !moved {
                break;
            }
        }
    }
    if changed.is_empty() {
        return;
    }
    let checked: Vec<_> = std::iter::once(&plan.sheet)
        .chain(&plan.sheets)
        .flat_map(|s| placement::collisions_cached(analysis, &all, s, &plan.views, &cache))
        .collect();
    // Replace only findings involving changed ink; untouched diagnostics retain their identity.
    plan.problems.retain(|p| {
        !p.subjects.iter().any(|s| changed.contains(s))
            || !(rechecked_codes.contains(&p.code)
                || matches!(
                    p.code.as_str(),
                    "annotation_leader_long" | "annotation_line_over_view"
                ))
    });
    plan.problems.extend(
        checked
            .into_iter()
            .filter(|p| p.subjects.iter().any(|s| changed.contains(s))),
    );
    plan.sheet.fill = crate::sheet_fit::fill(analysis, plan);
    plan.status = crate::completeness::status(&plan.problems);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn crossing(kinds: &[&str]) -> Problem {
        Problem {
            code: "annotation_leader_crossing".into(),
            crossing_kinds: kinds.iter().map(|s| (*s).into()).collect(),
            ..Default::default()
        }
    }

    #[test]
    fn a_route_must_improve_crossings_without_trading_kinds_or_text() {
        let before = vec![crossing(&["leader"]), crossing(&["extension_line"])];
        assert!(improves(&before, &[crossing(&["extension_line"])]));
        assert!(!improves(&before, &[crossing(&["arrow"])]));
        assert!(improves(
            &[Problem {
                code: "annotation_ink_overlap".into(),
                ..Default::default()
            }],
            &[]
        ));
        assert!(
            !improves(&before, &before),
            "ties retain the existing exterior route"
        );
        assert!(!improves(
            &before,
            &[Problem {
                code: "annotation_ink_overlap".into(),
                ..Default::default()
            }]
        ));
    }

    #[test]
    fn a_final_frame_cannot_cross_a_schedule_balloon_label() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/parts/mounting_plate.step");
        let a = crate::model::analyse_native(&path).unwrap().1;
        let mut plan = crate::model::plan(&a, &Spec::default()).unwrap();
        plan.balloons.push(crate::hole_table::Balloon {
            id: "schedule/tag/B8".into(),
            tag: "B8".into(),
            view: "v:plan".into(),
            features: Vec::new(),
            certifying: true,
            interior: false,
            at: [140., 170.],
            radius: 0.,
            label_box: Some([138., 168.5, 142., 171.5]),
            leader: None,
            state: crate::items::State::Shown,
        });
        let fixed = furniture(&plan);
        let label = fixed
            .iter()
            .find(|d| d.id == "schedule/tag/B8")
            .expect("the printed balloon is a final obstacle")
            .placed
            .as_ref()
            .unwrap();
        let frame = Placed {
            lines: vec![[[120., 170.], [160., 170.]]],
            arrows: Vec::new(),
            label_box: [160., 167., 180., 173.],
            label_at: [160., 167.],
            rotation: 0.,
        };
        assert!(
            !placement::annotations_clear(&frame, label),
            "the frame shaft crosses tag B8"
        );
    }

    #[test]
    fn a_proven_interior_gdt_flag_preserves_the_complete_glyph_and_text() {
        let mut g: gdt::PlannedGdt = serde_json::from_value(serde_json::json!({
            "id":"pmi:test", "kind":"control_frame", "view":"v:plan", "side":"right",
            "source":"test", "faces":[0], "cells":["A"], "label":"A", "default":true,
            "basis":"test", "state":{"kind":"shown"}, "placed": {
                "tip":[-10.,3.], "lines":[[[-10.,3.],[-2.,3.]],[[-2.,3.],[0.,3.]]],
                "arrows":[[[-10.,3.],[-7.3,3.]]], "label_box":[0.,0.,8.,6.],
                "strokes":[[[0.,0.],[8.,0.]],[[8.,0.],[8.,6.]],[[8.,6.],[0.,6.]],[[0.,6.],[0.,0.]]],
                "arcs":[], "fills":[], "letters":[{"text":"A","at":[4.,3.],"size":3.}],
                "runs":[{"text":"A","at":[4.,3.],"size":3.}] }
        }))
        .unwrap();
        let old = crate::model::render_item(crate::model::AnnotationItem::Gdt(&g));
        assert!(!old.drawn[0].interior);
        g.basis += interior::BASIS;
        let new = crate::model::render_item(crate::model::AnnotationItem::Gdt(&g));
        assert!(new.drawn[0].interior);
        assert_eq!(new.drawn[0].ink, old.drawn[0].ink);
        assert_eq!(new.drawn[0].label, old.drawn[0].label);
        assert_eq!(new.text.len(), old.text.len());
        assert!(new.text.iter().all(|t| t.size == 3.0));
    }
}
