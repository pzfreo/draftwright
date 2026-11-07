//! Smallest-first automatic paper and sheet-count preferences.
use std::collections::BTreeSet;

use serde::{Deserialize, Serialize};

use crate::model::{self, Analysis, Invalid, Plan, Spec};
use crate::{layout, sheet_fit};

/// Cheap evidence for one paper/count request; `max` is a bound, not an allocation.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Candidate {
    pub size: String,
    /// Preferred largest paper, including for refused candidates.
    #[serde(default)]
    pub preferred_max_size: String,
    #[serde(default)]
    pub preferred_max_sheets: usize,
    pub max: usize,
    pub outcome: String,
    /// [`RULED_OUT`] when cheap reserved-footprint estimates proved the request cannot fit
    /// at any readable scale, so it was not planned; absent for planned candidates.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub kind: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub scales: Vec<String>,
}

/// The [`Candidate::kind`] of a request ruled out before planning; its outcome starts
/// "ruled out: ".
pub const RULED_OUT: &str = "ruled_out";

// Layout/readability failures only: overlaps and crossings, ink outside its frame/bands,
// unreadable text/scale, and content left undrawn for space. Recognition and engineering
// findings do not choose paper. Compare code + sorted subjects against the automatic baseline
// so findings shared by every paper do not penalise a smaller sheet. Spatial codes count even
// at info severity; only the informational recovery summary is not a spatial/readability failure.
const FAILING_CODES: &[&str] = &[
    "annotation_overlap",
    "annotation_ink_overlap",
    "datum_symbol_overlap",
    "annotation_leader_crossing",
    "leader_on_extension",
    "leader_through_feature",
    "extension_through_feature",
    "annotation_off_sheet",
    "annotation_outside_area",
    "annotation_over_title_block",
    "annotation_over_view",
    "annotation_line_over_view",
    "title_field_overflow",
    "page_fit_uncertain",
    "views_do_not_fit",
    "views_overlap",
    "view_on_title_block",
    "turned_profile_superimposed",
    "turned_chain_no_clear_lane",
    "dimension_line_shared",
    "dimension_text_stray",
    "legibility_floor_breached",
    "layout_recovery_exhausted",
    "sheet_below_smallest_scale",
    "notes_not_placed",
    "table_not_placed",
    "table_dropped",
    "balloon_dropped",
    "callout_dropped",
    "location_ref_dropped",
    "off_axis_location_dropped",
    "section_dropped",
    "detail_unplaceable",
    "pmi_not_drawn",
    "view_plan_incomplete",
    "cross_reference_missing",
    "datum_not_identified",
];

fn problem_key(p: &model::Problem) -> (String, Vec<String>) {
    let mut subjects = p.subjects.clone();
    subjects.sort();
    subjects.dedup();
    (p.code.clone(), subjects)
}

pub(crate) fn layout_problem(problem: &model::Problem) -> bool {
    (FAILING_CODES.contains(&problem.code.as_str())
        || problem.labels().impact == crate::finding::Impact::Readability)
        && (problem.code != "layout_recovery_exhausted" || problem.severity != "info")
}

fn failures(analysis: &Analysis, plan: &Plan, floor: f64) -> BTreeSet<(String, Vec<String>)> {
    let mut failures: BTreeSet<_> = plan
        .problems
        .iter()
        .filter(|p| layout_problem(p))
        .map(problem_key)
        .collect();
    // This quality finding is measured from final ink, rather than emitted as a
    // Problem. Use the placer's corridor predicate, including its 1 mm clearance,
    // and compare code + item pair just like the reported crossing findings.
    for sheet in std::iter::once(&plan.sheet).chain(&plan.sheets) {
        let dimensions: Vec<_> = plan
            .dimensions
            .iter()
            .filter(|d| {
                plan.views
                    .iter()
                    .any(|v| v.id == d.view && v.sheet == sheet.number)
            })
            .cloned()
            .collect();
        failures.extend(
            crate::dims::leader_on_extension(&dimensions)
                .into_iter()
                .map(|subjects| ("leader_on_extension".into(), subjects)),
        );
    }
    failures.extend(crate::dims::sheet_route_limits(analysis, plan));
    // The outline box has no stroke width. At the frame its visible stroke is already
    // outside the drawing area. Use the renderer's line width, and the existing off-sheet
    // code/subject comparison; do not move or recover any candidate's geometry.
    let stroke = crate::output::layer_styles()[0].1.width;
    for (bounds, view) in sheet_fit::footprints(analysis, plan)
        .into_iter()
        .zip(&plan.views)
    {
        let page = plan
            .sheets
            .iter()
            .find(|s| s.number == view.sheet)
            .unwrap_or(&plan.sheet)
            .page;
        if bounds[0] < layout::margin() + stroke
            || bounds[1] < layout::margin() + stroke
            || bounds[2] > page[0] - layout::margin() - stroke
            || bounds[3] > page[1] - layout::margin() - stroke
        {
            failures.insert(("annotation_off_sheet".into(), vec![view.id.clone()]));
        }
    }
    // Internal automatic pairs suppress the explicit-scale warning, but must
    // still meet the automatic planner's existing printed-step minimum.
    if sheet_fit::illegible_steps(analysis, plan) > 0 {
        failures.insert(("legibility_floor_breached".into(), vec!["sheet".into()]));
    }
    if plan.sheet.scale_factor + 1e-9 < floor {
        failures.insert(("legibility_floor_breached".into(), vec!["sheet".into()]));
    }
    for view in plan.views.iter().filter(|v| v.id != "v:iso") {
        if view.scale + 1e-9 < floor {
            failures.insert(("legibility_floor_breached".into(), vec![view.id.clone()]));
        }
    }
    failures
}

/// Baseline outline sizes and reserved paper-mm bands. Outlines change with scale;
/// annotation lanes, captions and other furniture do not. Optional details and the
/// freely resized isometric are omitted, making the necessary fit test optimistic.
#[derive(Clone)]
struct Estimate {
    views: Vec<(String, [f64; 2], [f64; 2])>,
    /// Each block's sizes, in preference order: one for fixed furniture, one per block count
    /// for a hole table that may be split side by side ([`crate::hole_table::split_sizes`]).
    blocks: Vec<Vec<[f64; 2]>>,
    first_blocks: usize,
    scale: f64,
    aligned: bool,
}

impl Estimate {
    fn new(analysis: &Analysis, baseline: &Plan) -> Self {
        let outlines = sheet_fit::footprints(analysis, baseline);
        let bands = crate::view_gutters::bands(analysis, baseline);
        let views = baseline
            .views
            .iter()
            .zip(outlines)
            .zip(bands)
            .filter(|((v, _), _)| v.id != "v:iso" && v.detail.is_none())
            .map(|((v, outline), band)| {
                let size = [outline[2] - outline[0], outline[3] - outline[1]];
                let fixed = std::array::from_fn(|k| {
                    (outline[k] - band[k]).max(0.0) + (band[k + 2] - outline[k + 2]).max(0.0)
                });
                (v.id.clone(), size, fixed)
            })
            .collect();
        let size = |b: layout::Rect| vec![[b[2] - b[0], b[3] - b[1]]];
        let mut blocks: Vec<_> = baseline
            .notes_block
            .iter()
            .chain(&baseline.radius_notes_block)
            .map(|n| size(n.cells))
            .collect();
        blocks.extend(
            baseline
                .radius_table
                .iter()
                .filter(|t| t.table.placed.is_some())
                .map(|t| {
                    crate::tables::forms(
                        &t.table.rows,
                        crate::hole_table::columns(&baseline.spec, &t.table.id),
                    )
                }),
        );
        // R5 moves the rescue hole_table and declared tables, but general
        // notes, radius furniture and principal-view hole schedules stay on 1.
        // A hole table is judged in each form it may take, whole or split.
        let split = |t: &crate::hole_table::HoleTable| {
            (!t.blocks.is_empty() && t.blocks.iter().all(|b| b.placed.is_some()))
                .then(|| crate::hole_table::split_sizes(t, &baseline.spec))
        };
        blocks.extend(baseline.hole_tables.iter().filter_map(split));
        let first_blocks = blocks.len();
        blocks.extend(
            baseline
                .tables
                .iter()
                .filter(|t| t.placed.is_some())
                .map(|t| {
                    crate::tables::forms(&t.rows, crate::hole_table::columns(&baseline.spec, &t.id))
                }),
        );
        blocks.extend(baseline.hole_table.iter().filter_map(split));
        Self {
            views,
            first_blocks,
            blocks,
            scale: baseline.sheet.scale_factor,
            aligned: baseline.spec.views.values().all(|v| v.place.is_none()),
        }
    }

    fn compact_bound(&self, analysis: &Analysis, baseline: &Plan) -> Self {
        let outlines = sheet_fit::footprints(analysis, baseline);
        let mut estimate = self.clone();
        for (id, _, band) in &mut estimate.views {
            if let Some(i) = baseline.views.iter().position(|v| &v.id == id) {
                let saving = compact_savings(baseline, id, outlines[i]);
                for k in 0..2 {
                    band[k] = (band[k] - saving[k]).max(0.);
                }
            }
        }
        estimate
    }

    fn fits(&self, size: &str, max: usize, scale: f64) -> bool {
        self.shortfall(size, max, scale).is_none()
    }

    /// The first necessary condition `max` sheets of `size` paper fail at `scale`, stated
    /// with the reserved paper mm it needs against what the paper offers; `None` if all hold.
    fn shortfall(&self, size: &str, max: usize, scale: f64) -> Option<String> {
        // Invariant: Candidates select paper names from the fixed PAGES table.
        let (_, w, h) = layout::PAGES.iter().find(|p| p.0 == size).unwrap();
        let available = [w - 2.0 * layout::margin(), h - 2.0 * layout::margin()];
        let at = model::scale_name(scale);
        let frame = format!(
            "{size} drawable area is {:.0} × {:.0} mm",
            available[0], available[1]
        );
        let mut principal = std::collections::BTreeMap::new();
        let mut area = 0.0;
        let mut principal_area = 0.0;
        for (id, outline, band) in &self.views {
            let footprint =
                std::array::from_fn::<_, 2, _>(|k| outline[k] * scale / self.scale + band[k]);
            if (0..2).any(|k| footprint[k] > available[k]) {
                return Some(format!(
                    "{}{} needs at least {:.0} × {:.0} mm at {at}; {frame}",
                    crate::wording::view(id),
                    if band.iter().any(|b| *b > 0.0) {
                        " with its annotation bands"
                    } else {
                        ""
                    },
                    footprint[0],
                    footprint[1]
                ));
            }
            area += footprint[0] * footprint[1];
            if ["v:front", "v:plan", "v:side"].contains(&id.as_str()) {
                principal_area += footprint[0] * footprint[1];
                principal.insert(id.as_str(), footprint);
            }
        }
        // R5 keeps the aligned principal projections together on sheet 1.
        // With more sheets allowed it may move the plan to another sheet,
        // so only a single sheet must hold front and plan stacked.
        const GUTTER: f64 = 6.0;
        if self.aligned
            && let Some(front) = principal.get("v:front")
        {
            if let Some(side) = principal.get("v:side") {
                let across =
                    front[0] + side[0] + GUTTER - shared_band(&self.views, "v:front", "v:side", 0);
                if across > available[0] {
                    return Some(format!(
                        "aligned front and side views need at least {across:.0} mm across at {at}; {frame}"
                    ));
                }
            }
            if max == 1
                && let Some(plan) = principal.get("v:plan")
            {
                let high = front[1] + plan[1] + GUTTER;
                if high > available[1] {
                    return Some(format!(
                        "aligned front and plan views need at least {high:.0} mm high at {at}; {frame}"
                    ));
                }
            }
        }
        let mut first_area = 0.0;
        for (i, forms) in self.blocks.iter().enumerate() {
            // The fewest-block form that fits the drawable area, as the table is laid out;
            // the most split form when none does.
            let Some(&block) = forms
                .iter()
                .find(|b| (0..2).all(|k| b[k] <= available[k]))
                .or(forms.last())
            else {
                continue;
            };
            if i < self.first_blocks {
                first_area += block[0] * block[1];
            }
            if (0..2).any(|k| block[k] > available[k]) {
                return Some(format!(
                    "a table needs {:.0} × {:.0} mm; {frame}",
                    block[0], block[1]
                ));
            }
            // A tall block that cannot stack vertically with either aligned
            // principal must fit beside both, allowing their shared corridor.
            // R5 may move declared furniture, but not the principal schedules.
            if self.aligned
                && (max == 1 || i < self.first_blocks)
                && let (Some(front), Some(side)) =
                    (principal.get("v:front"), principal.get("v:side"))
                && block[1] + front[1] > available[1]
                && block[1] + side[1] > available[1]
            {
                let across = block[0] + front[0] + side[0]
                    - shared_band(&self.views, "v:front", "v:side", 0);
                if across > available[0] {
                    return Some(format!(
                        "a {:.0} mm tall table beside the aligned front and side views needs at least {across:.0} mm across at {at}; {frame}",
                        block[1]
                    ));
                }
            }
            area += block[0] * block[1];
        }
        let drawing_area = available[0] * available[1]
            - layout::title_block_width(*w) * crate::title_block::top([0.0; 4]);
        if principal_area + first_area > drawing_area {
            return Some(format!(
                "the principal views and sheet-1 tables need at least {:.0} mm² at {at}; one {size} sheet offers {drawing_area:.0} mm² beside the title block",
                principal_area + first_area
            ));
        }
        if area > drawing_area * max as f64 {
            return Some(format!(
                "views and tables need at least {area:.0} mm² at {at}; {max} {size} sheet(s) offer {:.0} mm² beside the title block",
                drawing_area * max as f64
            ));
        }
        None
    }

    /// Why no readable standard scale fits: the shortfall at the smallest readable scale,
    /// where every reserved outline is smallest, so every larger scale fails too.
    fn ruled_out(&self, size: &str, max: usize, floor: f64) -> String {
        let smallest = layout::SCALES
            .into_iter()
            .rev()
            .find(|s| *s + 1e-9 >= floor);
        match smallest.and_then(|s| Some((s, self.shortfall(size, max, s)?))) {
            Some((s, shortfall)) => format!(
                "ruled out: {shortfall}; {} is the smallest readable scale",
                model::scale_name(s)
            ),
            None => format!(
                "ruled out: no standard scale reaches the readable floor {}",
                model::scale_name(floor)
            ),
        }
    }

    fn scale(&self, size: &str, max: usize, floor: f64) -> Option<f64> {
        layout::SCALES
            .into_iter()
            .filter(|s| *s + 1e-9 >= floor)
            .find(|s| self.fits(size, max, *s))
    }
}

// Opposing annotation bands can share the common base corridor. Keep the
// remaining dimension tiers and callout widths in paper mm.
fn shared_band(views: &[(String, [f64; 2], [f64; 2])], a: &str, b: &str, k: usize) -> f64 {
    let band = |id| views.iter().find(|v| v.0 == id).map_or(0.0, |v| v.2[k]);
    band(a).min(band(b)).min(layout::dim_pad())
}

// With no detail owner, the baseline's printed primary steps must remain
// readable at the candidate's common scale. Do not tighten an inherited
// illegibility finding, or assume an authored/enlarged owner has sheet scale.
fn candidate_floor(analysis: &Analysis, baseline: &Plan, floor: f64) -> f64 {
    // Never newly compress a larger readable reference to the absolute 1:10
    // fallback just to retain preferred paper.
    let floor = if baseline.sheet.scale_factor > sheet_fit::ABSOLUTE_FLOOR {
        floor.max(
            layout::SCALES
                .into_iter()
                .rev()
                .find(|s| *s > sheet_fit::ABSOLUTE_FLOOR)
                .unwrap_or(sheet_fit::ABSOLUTE_FLOOR),
        )
    } else {
        floor
    };
    if baseline.views.iter().any(|v| v.detail.is_some())
        || baseline.spec.views.values().any(|v| v.detail.is_some())
        || sheet_fit::illegible_steps(analysis, baseline) > 0
    {
        return floor;
    }
    let needed = sheet_fit::min_step_scale(analysis, baseline);
    let standard = layout::SCALES
        .into_iter()
        .rev()
        .find(|s| *s >= needed)
        .unwrap_or(needed);
    floor.max(standard)
}

fn readable_floor(analysis: &Analysis, baseline: &Plan, spec: &Spec) -> f64 {
    // The unrestricted finished reference may already be just below a feature
    // target (e.g. a 3.96875 mm opening prints at 0.79375 mm at 1:5).
    // Do not turn that inherited clearance into a demand for the next standard
    // enlargement. Smaller candidates may not make it worse, and the author's
    // explicit smallest_scale remains a hard floor.
    candidate_floor(
        analysis,
        baseline,
        sheet_fit::min_geometry_scale(analysis).min(baseline.sheet.scale_factor),
    )
    .max(crate::sheets::floor(spec).unwrap_or(0.))
}

fn cap(spec: &Spec) -> &str {
    spec.sheet.preferred_max_size.as_deref().unwrap_or("A3")
}

fn preferred_sheets(spec: &Spec) -> usize {
    spec.sheet.preferred_max_sheets.unwrap_or(2) as usize
}

fn requests(spec: &Spec) -> Vec<(&'static str, usize)> {
    let preferred = layout::PAGES
        .iter()
        .position(|p| p.0 == cap(spec))
        .unwrap_or(1);
    let hard = spec.sheets.as_ref().and_then(|s| s.max).unwrap_or(9) as usize;
    let count = preferred_sheets(spec).min(hard);
    let mut out: Vec<_> = layout::PAGES[..=preferred]
        .iter()
        .map(|p| (p.0, 1))
        .collect();
    out.extend((2..=count).map(|n| (layout::PAGES[preferred].0, n)));
    for p in &layout::PAGES[preferred + 1..] {
        out.extend((1..=count).map(|n| (p.0, n)));
    }
    out.extend((count + 1..=hard).map(|n| ("A0", n)));
    out
}

fn rank(plan: &Plan) -> (usize, usize, f64, usize) {
    let f = model::findings(plan);
    (
        f.hard,
        f.dropped + model::undrawn_gdt(plan),
        -plan.sheet.scale_factor,
        1 + plan.sheets.len(),
    )
}

// Ink that cannot be read whatever the reader's patience: overlapping text,
// leader-through-text, ink outside its frame, view area or title block, and
// furniture or views that could not be placed. Other layout warnings (extension,
// feature and hidden-edge crossings, route lengths) are reported but do not choose paper.
const UNREADABLE: &[&str] = &[
    "annotation_overlap",
    "annotation_ink_overlap",
    "datum_symbol_overlap",
    "leader_line_through_text",
    "leader_on_extension",
    "annotation_off_sheet",
    "annotation_outside_area",
    "annotation_over_title_block",
    "title_field_overflow",
    "page_fit_uncertain",
    "views_do_not_fit",
    "views_overlap",
    "view_on_title_block",
    "notes_not_placed",
    "table_not_placed",
];

/// Usable relative to the automatic baseline: never clipped and never below the readable
/// floor; otherwise disqualified only by unreadable, error or loss findings (code + subjects,
/// printed-step breaches, drops) that the automatic baseline does not have. Warnings the
/// baseline shares never make a crowded-but-readable drawing lose to an enlargement.
/// `Err` names the check that refused the candidate, for the sheet-choice basis.
fn actual_space_drops(plan: &Plan) -> usize {
    plan.problems
        .iter()
        .filter(|p| crate::hole_table::DROPS.contains(&p.code.as_str()))
        .count()
}

fn finished_preview(
    analysis: &Analysis,
    spec: &Spec,
    intent: &Spec,
    plan: &Plan,
    reference: Option<&Plan>,
) -> Result<Plan, Invalid> {
    crate::planning::isolated(analysis, || {
        let mut preview = plan.clone();
        preview.spec = intent.clone();
        let mut preview = model::finish_presentation(analysis, spec, preview)?;
        crate::dims::contacts::report(analysis, &mut preview);
        if let Some(reference) = reference {
            let fixed_contacts = crate::dims::contacts::fixed_stroke_contacts(&preview);
            let reference_fixed = crate::dims::contacts::fixed_stroke_contacts(reference);
            let ids = preview
                .problems
                .iter()
                .chain(&fixed_contacts)
                .filter(|p| {
                    matches!(
                        p.code.as_str(),
                        "leader_along_centreline"
                            | "leader_through_feature"
                            | "leader_centerline_overlap"
                    ) && !reference
                        .problems
                        .iter()
                        .chain(&reference_fixed)
                        .any(|r| problem_key(r) == problem_key(p))
                })
                .flat_map(|p| {
                    p.subjects
                        .iter()
                        .filter(|s| preview.dimensions.iter().any(|d| &d.id == *s))
                        .cloned()
                })
                .collect::<BTreeSet<_>>();
            if !ids.is_empty() {
                crate::dims::callout_routes::finish_new_contacts(
                    analysis,
                    spec,
                    &mut preview,
                    &ids,
                    reference,
                );
            }
        }
        if let Some(reference) = reference {
            model::finish_selected(analysis, &mut preview);
            crate::dims::contacts::clear_new_section_contacts(&mut preview, reference);
            crate::dims::contacts::report(analysis, &mut preview);
        }
        Ok(preview)
    })
}

fn crowded_labels(plan: &Plan) -> usize {
    crate::dims::crowded_labels(plan)
}

/// Bounds below the actual base can be rejected before lowering any glyphs.
/// Enlarged detail owners keep their own scale and are checked on the trial.
fn reduction_floor(analysis: &Analysis, base: &Plan) -> f64 {
    let mut needed: f64 = 0.;
    for d in base
        .dimensions
        .iter()
        .filter(|d| d.placed.is_some() || matches!(d.state, crate::items::State::Covered { .. }))
    {
        let Some(view) = base
            .views
            .iter()
            .find(|v| v.id == d.view && v.detail.is_none())
        else {
            continue;
        };
        for m in &d.measurements {
            let Some(f) = analysis.features.iter().find(|f| f.id == m.feature) else {
                continue;
            };
            let key = match f.kind.as_str() {
                "hole" | "boss" | "cylindrical_profile" => "diameter",
                "slot" => "width",
                _ => continue,
            };
            if let Some(size) = f
                .parameters
                .get(key)
                .and_then(|v| v.as_f64())
                .filter(|v| v.is_finite() && *v > 0.)
            {
                needed = needed.max(base.sheet.scale_factor * 1.5 / (size * view.scale));
            }
        }
    }
    needed.min(base.sheet.scale_factor)
}

/// Reductions must retain readable dimensioned openings, even when they inherit
/// the reference's contact codes. Edge radii/chamfers are stated by callouts.
fn minimum_reduction_opening(analysis: &Analysis, plan: &Plan, base: &Plan) -> Result<(), String> {
    if plan.sheet.scale_factor + 1e-9 >= base.sheet.scale_factor {
        return Ok(());
    }
    const MINIMUM: f64 = 1.5;
    for d in plan
        .dimensions
        .iter()
        .filter(|d| d.placed.is_some() || matches!(d.state, crate::items::State::Covered { .. }))
    {
        let scale = plan
            .views
            .iter()
            .find(|v| v.id == d.view)
            .map_or(plan.sheet.scale_factor, |v| v.scale);
        for m in &d.measurements {
            let Some(f) = analysis.features.iter().find(|f| f.id == m.feature) else {
                continue;
            };
            let key = match f.kind.as_str() {
                "hole" | "boss" | "cylindrical_profile" => "diameter",
                "slot" => "width",
                _ => continue,
            };
            if let Some(size) = f
                .parameters
                .get(key)
                .and_then(|v| v.as_f64())
                .filter(|v| v.is_finite() && *v > 0.)
                && size * scale + 1e-6 < MINIMUM
            {
                return Err(format!(
                    "reduction refused: dimensioned {} {} is {:.3} paper mm, below {MINIMUM:.1} mm",
                    f.kind,
                    f.id,
                    size * scale
                ));
            }
        }
    }
    Ok(())
}

fn reduction_readable(analysis: &Analysis, plan: &Plan, base: &Plan) -> Result<(), String> {
    minimum_reduction_opening(analysis, plan, base)?;
    if plan.sheet.scale_factor + 1e-9 < base.sheet.scale_factor
        && crate::dims::converging_terminations(analysis, plan)
    {
        return Err("reduction refused: at least two annotations terminate within 1 mm of a feature smaller than 8 paper mm".into());
    }
    Ok(())
}

fn isolated_contacts_readable(
    analysis: &Analysis,
    plan: &Plan,
    displaced: &Plan,
) -> Result<(), String> {
    let (before, after) = (crowded_labels(displaced), crowded_labels(plan));
    if after > before {
        Err(format!(
            "cosmetic exception refused: label_crowded increases {before} to {after}"
        ))
    } else if crate::dims::converging_terminations(analysis, plan) {
        Err("cosmetic exception refused: at least two annotations terminate within 1 mm of a feature smaller than 8 paper mm".into())
    } else {
        Ok(())
    }
}

fn usable(analysis: &Analysis, plan: &Plan, reference: &Plan, floor: f64) -> Result<(), String> {
    usable_with_base_contacts(analysis, plan, reference, floor, None, false)
}

fn usable_with_base_contacts(
    analysis: &Analysis,
    plan: &Plan,
    reference: &Plan,
    floor: f64,
    base_contacts: Option<&Plan>,
    strict_routes: bool,
) -> Result<(), String> {
    usable_prepared(
        analysis,
        plan,
        reference,
        floor,
        base_contacts,
        strict_routes,
        None,
        None,
    )
}

#[allow(clippy::too_many_arguments)]
fn usable_prepared(
    analysis: &Analysis,
    plan: &Plan,
    reference: &Plan,
    floor: f64,
    base_contacts: Option<&Plan>,
    strict_routes: bool,
    prepared_reference: Option<&BTreeSet<(String, Vec<String>)>>,
    unbroken_routes: Option<&Plan>,
) -> Result<(), String> {
    let drops = |p: &Plan| {
        p.problems
            .iter()
            .filter(|q| crate::hole_table::DROPS.contains(&q.code.as_str()))
            .map(problem_key)
            .collect::<BTreeSet<_>>()
    };
    let refusal = if !sheet_fit::inside_frames(analysis, plan) {
        Some("ink leaves the drawable frame")
    } else if plan.sheet.scale_factor + 1e-9 < floor
        || plan
            .views
            .iter()
            .any(|v| v.id != "v:iso" && v.scale + 1e-9 < floor)
    {
        Some("below the readable scale")
    } else if sheet_fit::illegible_steps(analysis, plan)
        > sheet_fit::illegible_steps(analysis, reference)
    {
        Some("more illegible steps")
    } else if !model::carries_required(reference, plan) {
        Some("required items not carried")
    } else if !drops(plan).is_subset(&drops(reference)) {
        Some("new dropped items")
    } else if model::undrawn_gdt(plan) > model::undrawn_gdt(reference) {
        Some("more undrawn GD&T")
    } else if !unreported_losses(plan).is_subset(&unreported_losses(reference)) {
        Some("unreported lost dimensions")
    } else if strict_routes
        && model::longer_callouts(plan, reference)
        && unbroken_routes.is_none_or(|p| model::longer_callouts(plan, p))
    {
        Some("a callout grows more than 2 paper mm beyond the displaced drawing")
    } else if pictorial_only_sheet(&plan.spec, plan) {
        Some("a sheet carries only the pictorial view")
    } else {
        None
    };
    if let Some(reason) = refusal {
        return Err(reason.into());
    }
    let mut inherited = prepared_reference
        .cloned()
        .unwrap_or_else(|| failures(analysis, reference, floor));
    inherited.extend(reference.problems.iter().map(problem_key));
    if let Some(base) = base_contacts {
        inherited.extend(
            failures(analysis, base, floor)
                .into_iter()
                .filter(|(code, _)| code == "leader_on_extension"),
        );
        inherited.extend(
            base.problems
                .iter()
                .filter(|p| p.code == "leader_on_extension")
                .map(problem_key),
        );
    }
    let unreadable = |code: &str, severity: &str| {
        (severity == "error" && FAILING_CODES.contains(&code))
            || (severity != "info" && UNREADABLE.contains(&code))
    };
    // The inner pass retains the established recovery policy. The preferred-scale
    // pass must clear every added warning against that actual published base.
    let reported = plan
        .problems
        .iter()
        .filter(|p| unreadable(&p.code, &p.severity) || (strict_routes && p.severity == "warning"))
        .map(problem_key);
    // Measured from final ink rather than reported: a leader on an extension line reads as
    // that line. Leader runs beside model ink, hidden-edge crossings and route lengths are
    // reported route quality and stay advisory.
    // A split costs the reader a page turn, so it must not also add route defects: on more
    // than one sheet, new leader runs beside model ink and hidden-edge crossings refuse it.
    let split = !plan.sheets.is_empty();
    let mut measured: Vec<_> = failures(analysis, plan, floor)
        .into_iter()
        .filter(|(code, _)| {
            code == "leader_on_extension"
                || code == "annotation_off_sheet"
                || ((split || strict_routes)
                    && (code == "leader_ink_run" || code == "leader_hidden_crossing"))
        })
        .collect();
    if strict_routes {
        measured.extend(
            crate::dims::contacts::fixed_stroke_contacts(plan)
                .iter()
                .map(problem_key),
        );
        inherited.extend(
            crate::dims::contacts::fixed_stroke_contacts(reference)
                .iter()
                .map(problem_key),
        );
    }
    measured.extend(frame_conflicts(plan));
    inherited.extend(frame_conflicts(reference));
    let new: BTreeSet<_> = reported
        .chain(measured)
        .filter(|key| !inherited.contains(key))
        .map(|(code, _)| code)
        .collect();
    if new.is_empty() {
        Ok(())
    } else {
        Err(format!(
            "unreadable ink, required losses or unreadable scale: new {}",
            new.into_iter().collect::<Vec<_>>().join(", ")
        ))
    }
}

/// View frames (with their annotation bands) on the title block or on each other, page by
/// page, as the quality audit measures them.
fn frame_conflicts(plan: &Plan) -> Vec<(String, Vec<String>)> {
    let overlap =
        |a: [f64; 4], b: [f64; 4]| a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1];
    let mut out = Vec::new();
    for sheet in std::iter::once(&plan.sheet).chain(&plan.sheets) {
        let views: Vec<_> = plan
            .views
            .iter()
            .filter(|v| v.sheet == sheet.number)
            .collect();
        for (i, v) in views.iter().enumerate() {
            if overlap(v.area, sheet.title_block) {
                out.push(("view_on_title_block".into(), vec![v.id.clone()]));
            }
            for w in &views[i + 1..] {
                if overlap(v.area, w.area) {
                    out.push(("views_overlap".into(), vec![v.id.clone(), w.id.clone()]));
                }
            }
        }
    }
    out
}

// A sheet that carries only the freely fitted isometric is not a meaningful split: the
// orientation view belongs beside the views it explains, shrunk if need be, or beside a
// table that went to another sheet. An author's own sheet assignment for the isometric
// (in `spec`) is kept.
pub(crate) fn pictorial_only_sheet(spec: &Spec, plan: &Plan) -> bool {
    let authored = spec.views.get("iso").is_some_and(|v| v.sheet.is_some());
    !authored
        && plan.sheets.iter().any(|sheet| {
            let mut on = plan
                .views
                .iter()
                .filter(|v| v.sheet == sheet.number)
                .peekable();
            let tabled = plan
                .hole_table
                .iter()
                .chain(&plan.hole_tables)
                .any(|t| t.sheet == sheet.number)
                || plan.tables.iter().any(|t| t.sheet == sheet.number);
            on.peek().is_some() && !tabled && on.all(|v| v.id == "v:iso")
        })
}

// A dimension that could not be drawn must be stated by a problem naming it.
fn unreported_losses(plan: &Plan) -> BTreeSet<&str> {
    plan.dimensions
        .iter()
        .filter(|d| {
            matches!(&d.state, crate::items::State::NotDrawable { reason_code }
                if !plan.problems.iter().any(|q| q.code == *reason_code && q.subjects.contains(&d.id)))
        })
        .map(|d| d.id.as_str())
        .collect()
}

// The planner's internal first-estimate trace is not a replan the author asked for:
// `layout_replanned` belongs to kept layouts and local fixes ([`crate::local_layout`]).
fn drop_internal_replan(plan: &mut Plan) {
    plan.problems.retain(|p| p.id != "p:layout_replanned:sheet");
}

fn finish_choice(mut plan: Plan, spec: &Spec, candidates: Vec<Candidate>, reason: &str) -> Plan {
    plan.spec = spec.clone();
    plan.problems
        .retain(|p| p.id != "p:sheet_preference_exceeded:sheet");
    // Forced pairs, including explicit-layout replays and compact trials, are
    // search candidates. Their sheet/scale was not authored in the original Spec.
    if automatic(spec) {
        plan.sheet.basis = plan
            .sheet
            .basis
            .replace("as the spec asks", "from the automatic paper/scale search");
    }
    drop_internal_replan(&mut plan);
    plan.sheet.default = true;
    for s in &mut plan.sheets {
        s.default = true;
    }
    let larger = layout::PAGES.iter().position(|p| p.0 == plan.sheet.size)
        > layout::PAGES.iter().position(|p| p.0 == cap(spec));
    let more = 1 + plan.sheets.len() > preferred_sheets(spec);
    if larger || more {
        let which = match (larger, more) {
            (true, true) => "paper size and sheet count",
            (true, false) => "paper size",
            _ => "sheet count",
        };
        plan.problems.push(model::Problem {
            id: "p:sheet_preference_exceeded:sheet".into(),
            code: "sheet_preference_exceeded".into(), severity: "info".into(),
            message: format!("Preferred {which} exceeded: using {} sheet(s) of {} at {}. No layout within preferred {} paper and {} sheet(s) kept all ink inside the frame at a readable scale without unreadable ink or required losses.",
                1+plan.sheets.len(),plan.sheet.size,plan.sheet.scale,cap(spec),preferred_sheets(spec)),
            subjects: vec!["sheet".into()], ..Default::default()
        });
    }
    plan.status = crate::completeness::status(&plan.problems);
    explain(&mut plan, candidates, reason);
    plan
}

/// A usable preference candidate does not clear a shortfall the automatic search found: when
/// no standard sheet had a legible scale and the candidate is still below the opening
/// legibility floor, it says so, as the baseline did.
fn illegible_shortfall(analysis: &Analysis, spec: &Spec, baseline: &Plan, plan: &mut Plan) {
    let legible =
        sheet_fit::min_legible_scale(analysis).max(crate::sheets::floor(spec).unwrap_or(0.));
    let exhausted = |p: &Plan| {
        p.problems
            .iter()
            .any(|q| q.code == "layout_recovery_exhausted" && q.severity == "warning")
    };
    if !exhausted(baseline) || exhausted(plan) || plan.sheet.scale_factor + 1e-9 >= legible {
        return;
    }
    plan.problems.push(model::Problem {
        id: "p:layout_recovery_exhausted:sheet".into(),
        code: "layout_recovery_exhausted".into(),
        severity: "warning".into(),
        message: format!(
            "{} at {} is below {}, the opening legibility floor; no standard sheet and legible scale yields a valid complete drawing, so this usable drawing and its findings are kept.",
            plan.sheet.size,
            plan.sheet.scale,
            model::scale_name(legible)
        ),
        subjects: vec!["sheet".into()],
        ..Default::default()
    });
    plan.status = crate::completeness::status(&plan.problems);
}

/// [`select_once`], with an idle view left out (#222) only when that keeps the drawing on
/// paper no larger than the drawing with it shown takes at its first choice: otherwise, when
/// keeping the view reaches smaller paper, the view is kept. The plan keeps the caller's spec,
/// so planning it again reaches the same drawing.
pub(crate) fn select(analysis: &Analysis, spec: &Spec) -> Result<Plan, Invalid> {
    // Pin replays derive the same canonical paper inputs for annotation-only edits.
    // Retain that exact selection in session preparation instead of searching again.
    let key = serde_json::to_vec(&(spec, crate::planning::policy_key(analysis)))
        .expect("sheet selection inputs serialize");
    crate::planning::memo(analysis, "selected-paper", key, || {
        let plan = select_uncached(analysis, spec)?;
        if (spec.sheet.size.is_none() || spec.sheet.scale.is_none())
            && let Some(unbroken) = crate::broken::without(spec)
        {
            let mut full = select(analysis, &unbroken)?;
            let rank = |p: &Plan| layout::PAGES.iter().position(|g| g.0 == p.sheet.size);
            if rank(&plan) > rank(&full) || plan.sheet.scale_factor + 1e-9 < full.sheet.scale_factor
            {
                full.spec = spec.clone();
                for (name, _) in spec.views.iter().filter(|(_, v)| v.broken.is_some()) {
                    crate::broken::not_applied(
                        &mut full.problems,
                        name,
                        &format!("views/{name}/break"),
                        "no clean broken arrangement preserves the unbroken paper and scale; automatic policy keeps the unbroken drawing; pin sheet/size and sheet/scale to choose another pair",
                    );
                }
                return Ok(full);
            }
        }
        Ok(plan)
    })
}

fn select_uncached(analysis: &Analysis, spec: &Spec) -> Result<Plan, Invalid> {
    crate::dims::idle_decided(analysis, spec, || {
        let plan = select_once(analysis, spec)?;
        let decision = crate::dims::idle_decision(analysis, spec);
        let paper = |p: &Plan| {
            (
                layout::PAGES
                    .iter()
                    .position(|g| g.0 == p.sheet.size)
                    .unwrap_or(usize::MAX),
                1 + p.sheets.len(),
            )
        };
        let (Some(v), Some(shown)) = (decision.dropped, decision.paper) else {
            return Ok(plan);
        };
        let escalated = |p: &Plan| {
            let (page, count) = paper(p);
            page > shown.0 || count > shown.1
        };
        if !automatic(spec)
            || plan.views.iter().any(|w| w.id == format!("v:{v}"))
            || !escalated(&plan)
        {
            return Ok(plan);
        }
        let mut named = spec.clone();
        named.views.insert(v.into(), Default::default());
        match crate::dims::with_idle_view(None, || select_once(analysis, &named)) {
            Ok(mut kept) if paper(&kept) < paper(&plan) => {
                kept.spec = plan.spec.clone();
                kept.sheet.basis += &format!(
                    "; the {v} view, which no dimension needs, is kept: leaving it out took {} at {}",
                    plan.sheet.size, plan.sheet.scale
                );
                Ok(kept)
            }
            _ => Ok(plan),
        }
    })
}

fn select_once(analysis: &Analysis, spec: &Spec) -> Result<Plan, Invalid> {
    // Historical, unrendered routing templates retain their established scale.
    // Only the live drawing makes the new paper/scale choice.
    select_scales(
        analysis,
        spec,
        !crate::nominal::comparison(),
        &mut Default::default(),
    )
}

#[derive(Default)]
struct ForcedTrials {
    // One selection has one authored intent. Optional automatic detail recipes
    // belong to the presentation being judged, not to a new author request.
    plans: std::collections::BTreeMap<(String, u64, bool, usize), Result<Plan, Invalid>>,
    estimate: Option<Estimate>,
    compact_estimate: Option<Estimate>,
}

fn select_scales(
    analysis: &Analysis,
    spec: &Spec,
    maximise: bool,
    forced_trials: &mut ForcedTrials,
) -> Result<Plan, Invalid> {
    if automatic(spec) && crate::placement_intent::has_pins(spec) {
        let retained = pinned(analysis, spec)?;
        let requested_cut = !spec.views.sections.is_empty()
            || spec.views.get(model::SECTION).is_some_and(|v| !v.omit);
        if !requested_cut || model::finished_sheets_fit(analysis, &retained) {
            return Ok(retained);
        }
        // Adding a cut changes the canonical composition. A pin in that new
        // composition must participate in paper trials if its ink no longer fits;
        // retaining an automatic pair must not publish clipped ink.
        let mut selected = select_scales_full(analysis, spec, maximise, forced_trials)?;
        selected.sheet.basis +=
            "; requested section and pinned ink require a new fitting canonical paper/scale pair";
        return Ok(selected);
    }
    if automatic(spec)
        && !crate::nominal::comparison()
        && let Some(base) = tolerance_reference(spec)
    {
        let canonical = select(analysis, &base)?;
        let compact = canonical.sheet.dimension_spacing == model::DimensionSpacing::Compact;
        let reused = crate::dims::with_compact(compact, || {
            let plan = replay_pins(analysis, spec, &base, canonical.clone())?;
            let mut plan = model::finish_presentation(analysis, spec, plan)?;
            model::finish_selected(analysis, &mut plan);
            Ok::<_, Invalid>(plan)
        });
        let refused = match reused {
            Ok(plan) => {
                let warnings = new_tolerance_warnings(analysis, &plan, &canonical);
                if warnings.is_empty() {
                    return Ok(plan);
                }
                warnings.into_iter().collect::<Vec<_>>().join(", ")
            }
            Err(error) => error.message,
        };
        // The tolerance is part of the full request. Bypass only this shortcut,
        // including in the inner recovery pass, rather than stripping its ink.
        let mut selected = select_scales_full(analysis, spec, maximise, forced_trials)?;
        // Match replay's migrated authored intent rather than publishing a
        // forced trial's automatically generated source binding.
        let migrated = model::migrate(spec, analysis)?;
        selected.spec =
            crate::recognition_corrections::canonical(analysis, &migrated)?.into_owned();
        selected.sheet.basis += &format!(
            "; tolerance-only retained frame refused: {refused}; full paper/scale selection used"
        );
        return Ok(selected);
    }
    select_scales_full(analysis, spec, maximise, forced_trials)
}

fn select_scales_full(
    analysis: &Analysis,
    spec: &Spec,
    maximise: bool,
    forced_trials: &mut ForcedTrials,
) -> Result<Plan, Invalid> {
    // The displaced drawing is the actual paper-policy choice, not an
    // escalated estimate or a fill winner on different paper.
    let baseline = if maximise && automatic(spec) {
        select_scales_full(analysis, spec, false, forced_trials)?
    } else {
        model::plan_automatic(analysis, spec)?
    };
    if !automatic(spec) {
        return Ok(baseline);
    }
    let intent = baseline.spec.clone();
    let mut reference = if maximise {
        finished_preview(analysis, spec, &intent, &baseline, None)?
    } else {
        baseline.clone()
    };
    // A smaller broken first estimate must not impose a tighter route budget
    // than the actual unbroken drawing. Keep it as the drawable reference.
    let unbroken_routes = if maximise && let Some(unbroken) = crate::broken::without(spec) {
        let shown = select(analysis, &unbroken)?;
        Some(finished_preview(
            analysis,
            &unbroken,
            &shown.spec,
            &shown,
            None,
        )?)
    } else {
        None
    };
    // The inner recovery can itself enlarge a valid first estimate before the
    // preferred-scale pass starts. Do not inherit a new finite-chain contact
    // merely by calling that enlargement the base; repair its actual leader ink.
    if maximise && model::findings(&baseline).clean() {
        let ids = crate::dims::contacts::fixed_stroke_contacts(&reference)
            .into_iter()
            .flat_map(|p| {
                p.subjects
                    .into_iter()
                    .filter(|id| reference.dimensions.iter().any(|d| &d.id == id))
            })
            .collect::<BTreeSet<_>>();
        if !ids.is_empty() {
            let first = model::plan_escalated(analysis, spec)?;
            if first.sheet.size == baseline.sheet.size
                && first.sheet.scale_factor < baseline.sheet.scale_factor
                && model::findings(&first).clean()
                && model::undrawn_gdt(&first) == 0
            {
                let mut original = finished_preview(analysis, spec, &intent, &first, None)?;
                model::finish_selected(analysis, &mut original);
                crate::planning::isolated(analysis, || {
                    crate::dims::callout_routes::finish_new_contacts(
                        analysis,
                        spec,
                        &mut reference,
                        &ids,
                        &original,
                    )
                });
                crate::dims::contacts::report(analysis, &mut reference);
            }
        }
    }
    let effective = crate::spec_gdt::effective(analysis, &intent)?;
    let fit = effective.as_ref();
    let radius_reference = model::radius_edit_reference(analysis, &intent, true);
    let floor = readable_floor(fit, &baseline, spec).max(if maximise {
        reduction_floor(fit, &baseline)
    } else {
        0.
    });
    // The displaced finished drawing is immutable throughout this pass. Its
    // geometric readability evidence is independent of the candidate recipe.
    let prepared_reference = maximise.then(|| failures(fit, &reference, floor));
    let estimate = if maximise {
        forced_trials
            .estimate
            .clone()
            .unwrap_or_else(|| Estimate::new(fit, &baseline))
    } else {
        let estimate = Estimate::new(fit, &baseline);
        forced_trials.estimate = Some(estimate.clone());
        forced_trials.compact_estimate = Some(estimate.compact_bound(fit, &baseline));
        estimate
    };
    let compact_estimate = forced_trials
        .compact_estimate
        .clone()
        .unwrap_or_else(|| estimate.compact_bound(fit, &baseline));
    let geometry = if maximise && spec.sheet.dimension_spacing.is_none() {
        compact_estimate
    } else {
        estimate.clone()
    };
    // Unset spacing may fall back to compact lanes; an explicit choice holds for every trial.
    let fallback = spec.sheet.dimension_spacing.is_none();
    let hard = spec.sheets.as_ref().and_then(|s| s.max).unwrap_or(9);
    let mut candidates = Vec::new();
    let mut search_trace = Vec::new();
    let mut best = sheet_fit::inside_frames(fit, &baseline).then(|| baseline.clone());
    // Whole-view allocations are independent of a count bound once they fit.
    let mut separated = std::collections::BTreeMap::<(String, u64), Option<Plan>>::new();
    let mut scale_sources = std::collections::BTreeMap::<u64, Option<Plan>>::new();
    let mut unblocked = std::collections::BTreeMap::<(String, u64, bool), Plan>::new();
    // A2 and larger are for parts that need them at 1:1 (maintainer, 2026-10-15), as in the
    // automatic search: a part whose views fit A3 full size is not offered a large sheet.
    let small = std::cell::OnceCell::new();
    let first_estimate = std::cell::OnceCell::new();
    for (size, max) in requests(spec) {
        let large = layout::PAGES
            .iter()
            .position(|p| p.0 == size)
            .is_some_and(|i| i >= model::LARGE_SHEET);
        if large
            && spec.sheet.size.is_none()
            && *small.get_or_init(|| model::fits_a3_at_full_size(fit, &baseline))
        {
            candidates.push(Candidate {
                size: size.into(),
                preferred_max_size: String::new(),
                preferred_max_sheets: 0,
                max,
                outcome: "not offered: the views fit A3 at 1:1; large sheets are for larger parts"
                    .into(),
                kind: None,
                scales: Vec::new(),
            });
            continue;
        }
        let mut scales = Vec::new();
        // No readable standard scale passes the necessary reserved-footprint test: the
        // request is not planned, and its outcome states the shortfall. Otherwise the first
        // trial below always replaces this outcome.
        // Compact lanes may improve a feasible request, but do not revive paper
        // which the reserved annotation bands rule out at every readable scale.
        let request_fits = estimate.scale(size, max, floor).is_some();
        let first = request_fits
            .then(|| geometry.scale(size, max, floor))
            .flatten();
        let ruled_out = first
            .is_none()
            .then(|| estimate.ruled_out(size, max, floor));
        let mut outcome = ruled_out.clone().unwrap_or_default();
        let inherited_floor = readable_floor(fit, &baseline, spec);
        let trials = std::cell::RefCell::new(
            layout::SCALES
                .into_iter()
                .filter(|s| *s + 1e-9 >= inherited_floor)
                .filter_map(|s| {
                    if s+1e-9<floor {
                        return Some(format!("{size} at {}: not planned: reduction below {:.4} readable scale; dimensioned features require 1.5 paper mm, preserving the inherited base {}",model::scale_name(s),floor,baseline.sheet.scale));
                    }
                    if maximise && baseline.sheet.size==size && baseline.sheets.len()<max && (s-baseline.sheet.scale_factor).abs()<1e-9 { return None; }
                    geometry.shortfall(size, max, s).map(|why| {
                        format!("{size} at {}: not planned: {why}", model::scale_name(s))
                    })
                })
                .collect::<Vec<_>>(),
        );
        // Judge each trial as soon as it exists and stop at the largest clean scale.
        let mut judge = |mut plan: Plan,
                         scales: &mut Vec<String>,
                         outcome: &mut String,
                         reuse_reference: bool| {
            let scale = plan.sheet.scale.clone();
            let spacing = plan.sheet.dimension_spacing;
            let result = (|| {
                crate::dims::finish_pmi_witness_routes(fit, &mut plan);
                if !maximise && plan.sheet.dimension_spacing == model::DimensionSpacing::Compact {
                    let ids = plan
                        .problems
                        .iter()
                        .filter(|p| {
                            p.code == "leader_through_feature"
                                && !reference
                                    .problems
                                    .iter()
                                    .any(|r| problem_key(r) == problem_key(p))
                        })
                        .flat_map(|p| {
                            p.subjects
                                .iter()
                                .filter(|id| plan.dimensions.iter().any(|d| &d.id == *id))
                                .cloned()
                        })
                        .collect::<BTreeSet<_>>();
                    if !ids.is_empty() {
                        crate::dims::callout_routes::finish_new_contacts(
                            fit, spec, &mut plan, &ids, &reference,
                        );
                    }
                }
                if !scales.contains(&plan.sheet.scale) {
                    scales.push(plan.sheet.scale.clone());
                }
                if maximise && let Err(reason) = minimum_reduction_opening(fit, &plan, &reference) {
                    *outcome = reason;
                    return None;
                }
                if !sheet_fit::inside_frames(fit, &plan) {
                    *outcome = format!(
                        "hard rejected: view geometry or annotation ink leaves the drawable frame at {}",
                        plan.sheet.scale
                    );
                    return None;
                }
                if maximise
                    && (actual_space_drops(&plan) > 0
                        || model::undrawn_gdt(&plan) > 0
                        || model::hard_layout_codes(&plan)
                            .keys()
                            .any(|c| *c != "page_fit_uncertain"))
                {
                    *outcome = format!(
                        "rejected before final repair: hard findings {:?}; {} space drops; {} undrawn GD&T",
                        model::hard_layout_codes(&plan),
                        actual_space_drops(&plan),
                        model::undrawn_gdt(&plan)
                    );
                    return None;
                }
                if maximise
                    && !reuse_reference
                    && model::findings(&plan).hard == 0
                    && let Err(reason) = usable_prepared(
                        fit,
                        &plan,
                        &reference,
                        floor,
                        None,
                        true,
                        prepared_reference.as_ref(),
                        unbroken_routes.as_ref(),
                    )
                {
                    let repairable = reason
                        .strip_prefix("unreadable ink, required losses or unreadable scale: new ")
                        .is_some_and(|codes| {
                            codes.split(", ").all(|c| {
                                matches!(
                                    c,
                                    "leader_on_extension"
                                        | "leader_along_centreline"
                                        | "leader_centerline_overlap"
                                        | "leader_through_feature"
                                        | "label_section_furniture_overlap"
                                        | "views_overlap"
                                        | "view_on_title_block"
                                        | "page_fit_uncertain"
                                )
                            })
                        });
                    if !repairable {
                        *outcome = format!(
                            "refused at {}: {reason} ({} hard, {} drops; {})",
                            plan.sheet.scale,
                            model::findings(&plan).hard,
                            model::findings(&plan).dropped,
                            failures(fit, &plan, floor)
                                .into_iter()
                                .map(|(c, _)| c)
                                .collect::<BTreeSet<_>>()
                                .into_iter()
                                .collect::<Vec<_>>()
                                .join(", ")
                        );
                        if let Err(reason) = isolated_contacts_readable(fit, &plan, &reference) {
                            *outcome += &format!("; {reason}");
                        }
                        return None;
                    }
                }
                if maximise {
                    plan = match if reuse_reference {
                        let mut selected = reference.clone();
                        crate::planning::isolated(analysis, || {
                            model::finish_selected(analysis, &mut selected)
                        });
                        Ok(selected)
                    } else {
                        finished_preview(analysis, spec, &intent, &plan, Some(&reference))
                    } {
                        Ok(preview) => preview,
                        Err(error) => {
                            *outcome = format!("final presentation refused: {}", error.message);
                            return None;
                        }
                    };
                }
                let findings = model::findings(&plan);
                let finished_fit = !maximise || model::finished_sheets_fit(fit, &plan);
                if maximise
                    && (findings.hard > 0
                        || actual_space_drops(&plan) > 0
                        || model::undrawn_gdt(&plan) > 0
                        || !finished_fit)
                {
                    *outcome = format!(
                        "rejected: {} hard findings {:?}, {} space drops, {} undrawn GD&T; finished footprints fit {}",
                        findings.hard,
                        model::hard_layout_codes(&plan),
                        actual_space_drops(&plan),
                        model::undrawn_gdt(&plan),
                        finished_fit
                    );
                    return None;
                }
                let usability = if maximise {
                    usable_prepared(
                        fit,
                        &plan,
                        &reference,
                        floor,
                        None,
                        true,
                        prepared_reference.as_ref(),
                        unbroken_routes.as_ref(),
                    )
                } else {
                    usable(fit, &plan, &baseline, floor)
                };
                let reduction = if maximise {
                    reduction_readable(fit, &plan, &reference)
                } else {
                    Ok(())
                };
                let reason = match (usability, &reduction) {
                    (Ok(()), Ok(())) => return Some(plan),
                    (Err(reason), _) => reason,
                    (Ok(()), Err(reason)) => reason.clone(),
                };
                let seed_contacts = !maximise
                    && plan.sheets.is_empty()
                    && model::findings(&plan).clean()
                    && model::undrawn_gdt(&plan) == 0
                    && !plan
                        .problems
                        .iter()
                        .any(|p| p.code != "leader_on_extension" && layout_problem(p))
                    && model::finished_items_fit(fit, &plan)
                    && (crate::nominal::comparison()
                        || !crate::dims::converging_terminations(fit, &plan))
                    && first_estimate
                        .get_or_init(|| model::plan_escalated(analysis, spec).ok())
                        .as_ref()
                        .is_some_and(|base| {
                            base.sheet.size == plan.sheet.size
                                && base.sheet.scale == plan.sheet.scale
                                && model::findings(base).clean()
                                && model::undrawn_gdt(base) == 0
                                && model::finished_items_fit(fit, base)
                                && !base
                                    .problems
                                    .iter()
                                    .any(|p| p.code != "leader_on_extension" && layout_problem(p))
                                && crowded_labels(&plan) <= crowded_labels(base)
                                && usable_prepared(
                                    fit,
                                    &plan,
                                    &baseline,
                                    floor,
                                    Some(base),
                                    false,
                                    prepared_reference.as_ref(),
                                    unbroken_routes.as_ref(),
                                )
                                .is_ok()
                        });
                let readability = if maximise {
                    isolated_contacts_readable(fit, &plan, &reference)
                } else {
                    Ok(())
                };
                let isolated_contacts = maximise
                    && reduction.is_ok()
                    && readability.is_ok()
                    && !plan
                        .problems
                        .iter()
                        .any(|p| p.code != "leader_on_extension" && layout_problem(p))
                    && usable_prepared(
                        fit,
                        &plan,
                        &reference,
                        floor,
                        Some(&plan),
                        true,
                        prepared_reference.as_ref(),
                        unbroken_routes.as_ref(),
                    )
                    .is_ok();
                if seed_contacts || isolated_contacts {
                    plan.sheet.basis +=
                        "; isolated cosmetic leader/extension contacts retain this fitting pair";
                    return Some(plan);
                }
                *outcome = format!(
                    "refused at {}: {reason} ({} hard, {} drops; {})",
                    plan.sheet.scale,
                    model::findings(&plan).hard,
                    model::findings(&plan).dropped,
                    failures(fit, &plan, floor)
                        .into_iter()
                        .map(|(code, _)| code)
                        .collect::<BTreeSet<_>>()
                        .into_iter()
                        .collect::<Vec<_>>()
                        .join(", ")
                );
                if let Err(refusal) = readability {
                    *outcome += &format!("; {refusal}");
                }
                if !maximise && best.as_ref().is_none_or(|b| rank(&plan) < rank(b)) {
                    best = Some(plan);
                }
                None
            })();
            let reported = result.as_ref().map_or_else(
                || outcome.clone(),
                |p| {
                    format!(
                        "valid and complete; usable; {} final annotation conflicts",
                        model::ink_findings(p)
                    )
                },
            );
            trials
                .borrow_mut()
                .push(format!("{size} at {scale} ({spacing:?}): {reported}"));
            result
        };
        let mut chosen = None;
        // Smallest paper first; on it try every geometrically possible preferred
        // scale in descending order. Empty annotation reservations cannot cap scale.
        let mut steps: Vec<_> = first
            .into_iter()
            .flat_map(|largest| {
                layout::SCALES
                    .into_iter()
                    .filter(move |s| *s <= largest && *s + 1e-9 >= floor)
            })
            .collect();
        // A conservative band estimate cannot discard the drawing already
        // proved by the inner planner. Recheck its finished ink at this pair.
        if maximise
            && baseline.sheet.size == size
            && baseline.sheets.len() < max
            && baseline.sheet.scale_factor + 1e-9 >= floor
            && !steps.contains(&baseline.sheet.scale_factor)
        {
            steps.push(baseline.sheet.scale_factor);
            steps.sort_by(|a, b| b.total_cmp(a));
        }
        if !maximise {
            let baseline_fits = baseline.sheet.size == size && baseline.sheets.len() < max;
            if baseline_fits {
                outcome = "automatic baseline reused".into();
                chosen = judge(baseline.clone(), &mut scales, &mut outcome, true);
            } else {
                // The finished baseline's whole annotated views, repacked at its own
                // (roomiest) scale, cost no new plan: try them before any forced trial.
                let split = separated
                    .entry((size.into(), baseline.sheet.scale_factor.to_bits()))
                    .or_insert_with(|| {
                        crate::sheets::separate_views(fit, &baseline, size, hard as usize)
                            .or_else(|| crate::sheets::reflow_tall_tables(fit, &baseline, size))
                    });
                if let Some(plan) = split.as_ref().filter(|p| p.sheets.len() < max) {
                    chosen = judge(plan.clone(), &mut scales, &mut outcome, false);
                }
            }
            // At most one smaller readable standard scale: a crowded estimate may
            // read cleanly one step down on the same paper.
            steps = first
                .into_iter()
                .flat_map(|scale| {
                    std::iter::once(scale).chain(
                        layout::SCALES
                            .into_iter()
                            .find(|s| *s < scale && *s + 1e-9 >= floor),
                    )
                })
                .collect();
            if chosen.is_some() {
                steps.clear();
            } else if baseline_fits {
                // A refused baseline still earns one smaller readable scale on its own paper
                // before a larger sheet is tried.
                steps = layout::SCALES
                    .into_iter()
                    .find(|s| *s < baseline.sheet.scale_factor && *s + 1e-9 >= floor)
                    .into_iter()
                    .collect();
            }
        }
        // The compact fallback keeps a drawing on one sheet of this paper; a split keeps
        // normal lanes (pinned replay across whole-view sheets is not yet reliable).
        let compact = fallback && max == 1;
        'scales: for scale in steps {
            if maximise
                && baseline.sheet.size == size
                && baseline.sheet.scale_factor == scale
                && baseline.sheets.len() < max
            {
                // Maximisation may improve the existing drawing, never disqualify
                // it and then search A0 repeatedly for a stricter replacement.
                search_trace.extend(trials.borrow().iter().map(|t| {
                    if max > 1 {
                        format!("{t} [sheet limit {max}]")
                    } else {
                        t.clone()
                    }
                }));
                return retained_base(
                    analysis,
                    baseline,
                    reference,
                    candidates,
                    scales,
                    search_trace,
                );
            }
            if !scales.contains(&model::scale_name(scale)) {
                scales.push(model::scale_name(scale));
            }
            // Normal spacing first, then a compact presentation at the same scale.
            // The full scale search also retries readable ink: a clean compact
            // candidate must not lose to smaller geometry or larger paper.
            let mut cramped_first = None;
            let mut lanes_may_help = true;
            for spaced in [false, true] {
                if spaced && (!compact || cramped_first == Some(false) || !lanes_may_help) {
                    if compact {
                        trials.borrow_mut().push(format!(
                            "{size} at {} (Compact): not planned: {}",
                            model::scale_name(scale),
                            if cramped_first == Some(false) {
                                "the normal-spacing refusal is not a shortage of lane space"
                            } else {
                                "compact lanes cannot cure the frame shortfall"
                            }
                        ));
                    }
                    continue;
                }
                if maximise
                    && !spaced
                    && let Some(why) = estimate.shortfall(size, max, scale)
                {
                    trials.borrow_mut().push(format!(
                        "{size} at {} (Normal): banded pre-filter: {why}",
                        model::scale_name(scale)
                    ));
                    cramped_first = Some(true);
                    continue;
                }
                let key = (size.to_string(), scale.to_bits(), spaced);
                let cached = (max > 1).then(|| unblocked.get(&key).cloned()).flatten();
                let mut forced = intent.clone();
                forced.binding = spec.binding.clone();
                forced.sheet.size = Some(size.into());
                forced.sheet.scale = Some(model::scale_name(scale));
                forced.sheets.get_or_insert_with(Default::default).max = Some(max as u32);
                if spaced {
                    forced.sheet.dimension_spacing = Some(model::DimensionSpacing::Compact);
                }
                omit_absent_details(fit, &mut forced, &baseline, scale);
                let plan = match cached.map(Ok).unwrap_or_else(|| {
                    let trial_key = (
                        size.to_string(),
                        scale.to_bits(),
                        forced.sheet.dimension_spacing == Some(model::DimensionSpacing::Compact),
                        max,
                    );
                    forced_trials
                        .plans
                        .entry(trial_key)
                        .or_insert_with(|| {
                            crate::planning::fixed_pair_with_radius_reference(
                                radius_reference.as_ref(),
                                || model::plan_automatic(analysis, &forced),
                            )
                        })
                        .clone()
                }) {
                    Ok(plan) => plan,
                    Err(e) => {
                        outcome = format!("cannot plan: {}", e.message);
                        trials
                            .borrow_mut()
                            .push(format!("{size} at {}: {outcome}", model::scale_name(scale)));
                        continue;
                    }
                };
                let plan = if maximise
                    && max == 1
                    && !sheet_fit::inside_frames(fit, &plan)
                    && actual_space_drops(&plan) == 0
                {
                    crate::sheets::reflow_outside_auxiliaries(fit, &plan).unwrap_or(plan)
                } else {
                    plan
                };
                // A complete single sheet is what a larger count permission returns too
                // (the planner's max-only shortcut), so later counts reuse it unplanned.
                let complete = max == 1
                    && model::findings(&plan).clean()
                    && model::undrawn_gdt(&plan) == 0
                    && sheet_fit::inside_frames(fit, &plan);
                if (max > 1 && !plan.sheet.basis.contains("blocked by sheets.max")) || complete {
                    unblocked.insert(key, plan.clone());
                }
                // Only a lack of room (clipping, hard layout findings or space drops)
                // earns compact spacing or the smaller scale; a routing refusal would only
                // repeat itself.
                let frame_conflict = !frame_conflicts(&plan).is_empty();
                let default_frame_conflict = size == "A4" && max == 1 && frame_conflict;
                let findings = model::findings(&plan);
                let cramped = default_frame_conflict
                    || findings.hard > 0
                    || findings.dropped > 0
                    || plan
                        .problems
                        .iter()
                        .any(|p| crate::hole_table::DROPS.contains(&p.code.as_str()))
                    || !sheet_fit::inside_frames(fit, &plan);
                cramped_first = if spaced && !cramped {
                    // Once compact ink fits, a remaining routing refusal does not
                    // earn a smaller scale merely because normal lanes were cramped.
                    Some(false)
                } else {
                    cramped_first.or(Some(cramped))
                };
                if compact && cramped && !spaced {
                    lanes_may_help = default_frame_conflict || compact_may_help(fit, &plan);
                }
                let only_estimate = model::hard_layout_codes(&plan)
                    .keys()
                    .all(|code| *code == "page_fit_uncertain");
                let replay_explicit = size == "A4"
                    && max == 1
                    && (frame_conflict
                        || plan.problems.iter().any(|p| p.code == "page_fit_uncertain"))
                    && only_estimate
                    && findings.dropped == 0
                    && model::undrawn_gdt(&plan) == 0
                    && sheet_fit::inside_frames(fit, &plan);
                chosen = judge(plan, &mut scales, &mut outcome, false);
                if chosen.is_some() {
                    break 'scales;
                }
                // Default A4 earns its clean explicit presentation before
                // spending more paper on an empty reservation-frame conflict.
                // The bounded replay is judged by the same physical fit, loss,
                // readability and crossing gates.
                if replay_explicit
                    && let Ok(Some(explicit)) = model::explicit_fit_candidate(analysis, &forced)
                {
                    chosen = judge(explicit, &mut scales, &mut outcome, false);
                    if chosen.is_some() {
                        break 'scales;
                    }
                }
                // A smaller readable scale may fit individual views even when
                // the unrestricted reference's scale cannot. Plan its ink on
                // roomy paper, then reuse whole-view allocation; never pack a
                // cramped small-paper plan after it has already lost items.
                if !spaced && max > 1 && scale < baseline.sheet.scale_factor {
                    let source = scale_sources.entry(scale.to_bits()).or_insert_with(|| {
                        let mut wide = forced.clone();
                        wide.sheet.size = Some("A0".into());
                        wide.sheets.get_or_insert_with(Default::default).max = Some(hard);
                        crate::planning::fixed_pair_with_radius_reference(
                            radius_reference.as_ref(),
                            || model::plan_automatic(analysis, &wide),
                        )
                        .ok()
                    });
                    let split = separated
                        .entry((size.into(), scale.to_bits()))
                        .or_insert_with(|| {
                            let source = source.as_ref()?;
                            crate::sheets::separate_views(fit, source, size, hard as usize)
                                .or_else(|| crate::sheets::reflow_tall_tables(fit, source, size))
                        });
                    if let Some(plan) = split.as_ref().filter(|p| p.sheets.len() < max) {
                        chosen = judge(plan.clone(), &mut scales, &mut outcome, false);
                        if chosen.is_some() {
                            break 'scales;
                        }
                    }
                }
            }
            if !maximise && cramped_first == Some(false) {
                break;
            }
        }
        search_trace.extend(trials.borrow().iter().map(|t| {
            if max > 1 {
                format!("{t} [sheet limit {max}]")
            } else {
                t.clone()
            }
        }));
        if let Some(mut plan) = chosen {
            illegible_shortfall(fit, spec, &baseline, &mut plan);
            let reason = format!(
                "{size}: usable at {} ({} sheet(s)){}; nothing clipped, readable scale, no unreadable ink or required losses",
                plan.sheet.scale,
                1 + plan.sheets.len(),
                if plan.sheet.dimension_spacing == model::DimensionSpacing::Compact {
                    " with compact dimension spacing"
                } else {
                    ""
                }
            );
            candidates.push(Candidate {
                size: size.into(),
                preferred_max_size: String::new(),
                preferred_max_sheets: 0,
                max,
                outcome: reason.clone(),
                kind: None,
                scales,
            });
            if plan.sheet.basis != baseline.sheet.basis {
                plan.sheet.basis += &format!(
                    "; {} {} at {}: {}",
                    if maximise {
                        "base choice before scale maximisation"
                    } else {
                        "inner planner reference before paper preferences"
                    },
                    baseline.sheet.size,
                    baseline.sheet.scale,
                    baseline.sheet.basis
                );
            }
            plan.sheet.basis += &format!(
                "; {} preferred scale trials: {}",
                if maximise {
                    "largest-clean-scale search"
                } else {
                    "initial paper search"
                },
                search_trace.join("; ")
            );
            if maximise {
                plan.sheet.basis += &format!(
                    "; initial automatic reference {} at {}",
                    baseline.sheet.size, baseline.sheet.scale
                );
                for problem in plan
                    .problems
                    .iter_mut()
                    .filter(|p| p.code == "layout_recovery_exhausted")
                {
                    problem.message += &format!(
                        " Largest-clean-scale search tried: {}.",
                        search_trace.join("; ")
                    );
                }
                selected_trial_basis(&mut plan);
            }
            return Ok(finish_choice(plan, &intent, candidates, &reason));
        }
        let kind = (ruled_out.as_ref() == Some(&outcome)).then(|| RULED_OUT.to_string());
        candidates.push(Candidate {
            size: size.into(),
            preferred_max_size: String::new(),
            preferred_max_sheets: 0,
            max,
            outcome,
            kind,
            scales,
        });
    }
    if maximise {
        return retained_base(
            analysis,
            baseline,
            reference,
            candidates,
            Vec::new(),
            search_trace,
        );
    }
    let mut best = best.ok_or_else(|| Invalid {
        path: None,
        also: Vec::new(),
        message: "No unclipped automatic drawing fits on supported paper within sheets.max; increase sheets/max or state an explicit layout.".into(),
        choices: vec!["sheets/max".into(),"sheet/size".into()],
    })?;
    best.problems.push(model::Problem {
        id:"p:layout_recovery_exhausted:sheet-preferences".into(),
        code:"layout_recovery_exhausted".into(),severity:"warning".into(),
        message:"No usable automatic layout was found through A0 within sheets.max; the least-bad unclipped candidate is retained. Its remaining layout, readability and required-information limitations are reported.".into(),
        subjects:vec!["sheet".into()],..Default::default()
    });
    if maximise {
        best.spec = intent.clone();
        best = finished_preview(analysis, spec, &intent, &best, None)?;
        model::finish_selected(analysis, &mut best);
        selected_trial_basis(&mut best);
    }
    best.sheet.basis += &format!(
        "; {} preferred scale trials: {}",
        if maximise {
            "largest-clean-scale search"
        } else {
            "initial paper search"
        },
        search_trace.join("; ")
    );
    let reason = "least-bad unclipped candidate; supported-paper search exhausted";
    Ok(finish_choice(best, &intent, candidates, reason))
}

/// Retain the ordinary paper-policy result once maximisation reaches it.
/// No new recovery finding is introduced by an unsuccessful maximisation.
fn retained_base(
    analysis: &Analysis,
    baseline: Plan,
    mut selected: Plan,
    mut candidates: Vec<Candidate>,
    mut scales: Vec<String>,
    mut trace: Vec<String>,
) -> Result<Plan, Invalid> {
    trace.push(format!(
        "{} at {} ({:?}): base retained without a replacement",
        baseline.sheet.size, baseline.sheet.scale, baseline.sheet.dimension_spacing
    ));
    model::finish_selected(analysis, &mut selected);
    let count = 1 + baseline.sheets.len();
    let mut candidate = baseline
        .sheet_candidates
        .last()
        .cloned()
        .unwrap_or(Candidate {
            size: baseline.sheet.size.clone(),
            preferred_max_size: String::new(),
            preferred_max_sheets: 0,
            max: count,
            outcome: "base retained".into(),
            kind: None,
            scales: Vec::new(),
        });
    if !scales.contains(&baseline.sheet.scale) {
        scales.push(baseline.sheet.scale.clone());
    }
    candidate.scales = scales;
    candidate.size = baseline.sheet.size.clone();
    candidate.max = count;
    candidates.push(candidate);
    selected.sheet.basis += &format!(
        "; largest-clean-scale search preferred scale trials: {}; initial automatic reference {} at {}; base retained without changing its findings",
        trace.join("; "),
        baseline.sheet.size,
        baseline.sheet.scale
    );
    for candidate in &mut candidates {
        candidate.preferred_max_size = cap(&selected.spec).to_string();
        candidate.preferred_max_sheets = preferred_sheets(&selected.spec);
    }
    // A genuinely clean retained summary still names the refused larger scales.
    // Existing non-clean recovery diagnoses are preserved byte for byte.
    if model::findings(&selected).clean() {
        for p in selected.problems.iter_mut().filter(|p| {
            p.code == "layout_recovery_exhausted"
                && p.severity == "info"
                && p.message.contains("no alternative")
        }) {
            p.message += &format!(" Largest-clean-scale search tried: {}.", trace.join("; "));
        }
    }
    selected.sheet_candidates = candidates;
    selected_trial_basis(&mut selected);
    Ok(selected)
}

/// Whether compact lanes could cure a cramped normal-spacing trial. Not when a view's own
/// outline is larger than the frame, nor when the annotated views span more than the frame
/// by more than compact lanes could save across all of them; not when its hard findings and
/// drops are not a lack of room for dimension lanes.
fn compact_may_help(analysis: &Analysis, plan: &Plan) -> bool {
    let page = plan.sheet.page;
    let frame = [
        layout::margin(),
        layout::margin(),
        page[0] - layout::margin(),
        page[1] - layout::margin(),
    ];
    // Dense PMI's settled normal bands are better evidence than the seed's
    // empty reservations. If even their compact bound fails, do not lower it again.
    if !plan.gdt.is_empty()
        && Estimate::new(analysis, plan)
            .compact_bound(analysis, plan)
            .shortfall(&plan.sheet.size, 1, plan.sheet.scale_factor)
            .is_some()
    {
        return false;
    }
    if !sheet_fit::inside_frames(analysis, plan) {
        let outlines = sheet_fit::footprints(analysis, plan);
        let bands = crate::view_gutters::bands(analysis, plan);
        let mut ink = [
            f64::INFINITY,
            f64::INFINITY,
            f64::NEG_INFINITY,
            f64::NEG_INFINITY,
        ];
        let mut saving = [0.0; 2];
        for ((view, outline), band) in plan.views.iter().zip(outlines).zip(bands) {
            if view.id == "v:iso" {
                continue;
            }
            // Packing moves views; only a view larger than the frame cannot fit.
            if (0..2).any(|k| outline[k + 2] - outline[k] > frame[k + 2] - frame[k] + 1e-6) {
                return false;
            }
            // Sections and details are movable auxiliary blocks, not an
            // immutable continuation of the principal projection row. Their
            // old off-sheet origins cannot rule out compact principal lanes.
            if view.cut.is_some() || view.detail.is_some() {
                continue;
            }
            for k in 0..2 {
                ink[k] = ink[k].min(band[k]);
                ink[k + 2] = ink[k + 2].max(band[k + 2]);
                saving[k] += compact_savings(plan, &view.id, outline)[k];
            }
        }
        // The annotated views' total extent beyond the frame's span, against the most that
        // compact lanes could save across every view.
        return (0..2).all(|k| ink[k + 2] - ink[k] - (frame[k + 2] - frame[k]) <= saving[k] + 1e-6);
    }
    // Room the sheet lacks: space drops, the page-fit verdicts, and dimension ink pushed off
    // the sheet or onto the title block. Collisions between items (overlaps) are local
    // placement, which tighter lanes do not relieve.
    let dimensioned = |p: &model::Problem| p.subjects.iter().any(|s| s.starts_with("d:"));
    plan.problems
        .iter()
        .filter(|p| p.severity != "info")
        .any(|p| match p.code.as_str() {
            "page_fit_uncertain" | "views_do_not_fit" => true,
            "annotation_off_sheet" | "annotation_over_title_block" => dimensioned(p),
            code => crate::hole_table::DROPS.contains(&code),
        })
}

/// The most paper mm compact spacing can save across each axis of a view: per side, 3 mm on
/// the first line (or the empty corridor) and 1.5 mm per further stacked linear dimension.
fn compact_savings(plan: &Plan, view: &str, o: [f64; 4]) -> [f64; 2] {
    use crate::dims::{FIRST_COMPACT, FIRST_NORMAL, LANE_COMPACT, LANE_NORMAL};
    let side = |k: usize, sign: f64, edge: f64| {
        let mut at: Vec<f64> = plan
            .dimensions
            .iter()
            .filter(|d| d.view == view && d.kind == "linear")
            .filter_map(|d| d.placed.as_ref()?.lines.last().copied())
            .filter(|[a, b]| (a[k] - b[k]).abs() < 1e-6 && (a[1 - k] - b[1 - k]).abs() > 1.0)
            .map(|[a, _]| sign * (a[k] - edge))
            .filter(|d| *d > 0.5)
            .collect();
        at.sort_by(f64::total_cmp);
        at.dedup_by(|a, b| (*a - *b).abs() < 0.01);
        // Every side keeps at least the first-line corridor (layout::dim_pad), 3 mm less
        // at compact spacing.
        (FIRST_NORMAL - FIRST_COMPACT)
            + at.len().saturating_sub(1) as f64 * (LANE_NORMAL - LANE_COMPACT)
    };
    // Vertical lines stack left and right (across x); horizontal ones below and above.
    [
        side(0, -1.0, o[0]) + side(0, 1.0, o[2]),
        side(1, -1.0, o[1]) + side(1, 1.0, o[3]),
    ]
}

/// Live, unpinned automatic choices are already finished and quality-checked.
/// Explicit controls and historical routing templates keep their existing finishing path.
pub(crate) fn finishes_selection(spec: &Spec) -> bool {
    automatic(spec) && !crate::placement_intent::has_pins(spec) && !crate::nominal::comparison()
}

fn selected_trial_basis(plan: &mut Plan) {
    plan.sheet.basis = format!(
        "selected {} {} fill {:.1}% ({} annotation conflicts); {}",
        plan.sheet.size,
        plan.sheet.scale,
        plan.sheet.fill * 100.,
        model::ink_findings(plan),
        plan.sheet.basis
    );
}

// A tolerance edits annotation ink, not the canonical paper/scale inputs.
// Derive the same reference statelessly too, so saved Specs and Session replays agree.
fn annotation_only_tolerance(spec: &Spec) -> bool {
    spec.dimensions.values().any(|d| d.tolerance.is_some())
        // An authored measurement changes the geometric inventory, even when
        // the same transaction also gives it a tolerance.
        && !spec.dimensions.values().any(|d| d.kind.is_some() || d.between.is_some())
}

// Compare the finished ink, including collisions that a forced replay can
// leave unreported until publication. A shared pre-existing warning is inherited.
fn new_tolerance_warnings(analysis: &Analysis, plan: &Plan, reference: &Plan) -> BTreeSet<String> {
    let warnings = |p: &Plan| {
        p.problems
            .iter()
            .cloned()
            .chain(crate::dims::recheck(analysis, p))
            .chain(crate::dims::contacts::fixed_stroke_contacts(p))
            .filter(|p| matches!(p.severity.as_str(), "warning" | "error"))
            .map(|p| (problem_key(&p), p.severity))
            .collect::<BTreeSet<_>>()
    };
    let inherited = warnings(reference);
    let mut new: BTreeSet<_> = warnings(plan)
        .difference(&inherited)
        .map(|((code, _), _)| code.clone())
        .collect();
    if !model::finished_items_fit(analysis, plan) && model::finished_items_fit(analysis, reference)
    {
        new.insert("page_fit_uncertain".into());
    }
    new
}

fn tolerance_reference(spec: &Spec) -> Option<Spec> {
    if !annotation_only_tolerance(spec) {
        return None;
    }
    let mut base = spec.clone();
    base.dimensions.retain(|_, d| {
        if d.tolerance.take().is_some() {
            *d != model::DimensionSpec::default()
        } else {
            true
        }
    });
    Some(base)
}

fn automatic(spec: &Spec) -> bool {
    spec.sheet.size.is_none() && spec.sheet.scale.is_none()
}

fn pinned(analysis: &Analysis, spec: &Spec) -> Result<Plan, Invalid> {
    // Offsets belong to the unpinned drawing's frame. A drag may clear a
    // rejected candidate's crossing, but must not change paper and its origin.
    // Select that canonical frame first, then replay the existing fixed-pair R5
    // machinery; there is no new search or placement repair for pins.
    let mut base = model::pin_base(spec);
    base.sheets = spec.sheets.clone();
    // An already published view is a no-op name in the pin reference. Keeping
    // that name can select a different automatic composition and lose its
    // schedule before the pinned trial even starts (#127's expanded radii).
    let mut canonical = if !base.views.is_empty() {
        let mut unnamed = base.clone();
        unnamed.views.clear();
        unnamed.views.keep_sections_from(&spec.views);
        let published = select(analysis, &unnamed)?;
        if base
            .views
            .keys()
            .all(|name| published.views.iter().any(|v| v.id == format!("v:{name}")))
        {
            base = unnamed;
            published
        } else {
            select(analysis, &base)?
        }
    } else {
        select(analysis, &base)?
    };
    if spec.sheet.references != Some(true)
        && spec.dimensions.iter().any(|(id, d)| {
            d.place.as_ref().is_some_and(|p| p.pinned)
                && canonical.omitted.iter().any(|o| {
                    &o.id == id
                        && o.basis
                            .contains("other stated dimensions determine this interval")
                })
        })
    {
        // A pinned reference retains the drawing it came from, including its
        // paper choice with reference ink enabled (as model's pin replay does).
        base.sheet.references = Some(true);
        canonical = select(analysis, &base)?;
    }
    // Replay with the spacing the canonical drawing was selected with, which may be the
    // automatic compact fallback.
    let compact = canonical.sheet.dimension_spacing == model::DimensionSpacing::Compact;
    crate::dims::with_compact(compact, || replay_pins(analysis, spec, &base, canonical))
}

fn replay_pins(
    analysis: &Analysis,
    spec: &Spec,
    base: &Spec,
    canonical: Plan,
) -> Result<Plan, Invalid> {
    let count = 1 + canonical.sheets.len();
    let migrated = model::migrate(spec, analysis)?;
    let intent = crate::recognition_corrections::canonical(analysis, &migrated)?.into_owned();
    // The unpinned automatic baseline is already memoised by canonical selection.
    // Keep its ordinary pin replay when no forced paper was selected; a fixed-pair
    // replay can have a different nominal presentation even at the same scale.
    let raw = model::plan_automatic(analysis, base)?;
    // The drawing the unpinned request finally shows, after the selected drawing's own
    // route repair (plan_recovered's callout_routes::finish). Reported callout offsets
    // are measured on it, so an offset at its automatic bend is a bare pin.
    let mut held = canonical.clone();
    let areas = if finishes_selection(base) {
        // Automatic selection already publishes its finished ink. Repeating the
        // nominal and attachment passes changes the anchor a bare pin holds.
        held.views.iter().map(|v| v.area).collect::<Vec<_>>()
    } else {
        model::finish_nominal_plan(analysis, base, &mut held);
        let effective = crate::spec_gdt::effective(analysis, &held.spec)?;
        crate::dims::callout_routes::prefer_interior(effective.as_ref(), base, &mut held, None);
        crate::dims::finish_openings(effective.as_ref(), base, &mut held);
        crate::dims::callout_routes::finish(effective.as_ref(), base, &mut held);
        let areas: Vec<_> = held.views.iter().map(|v| v.area).collect();
        crate::dims::hole_marks::attach(effective.as_ref(), base, &mut held);
        crate::dims::size_datums::attach(effective.as_ref(), base, &mut held);
        if crate::dims::openings_need_settling(&held) {
            crate::dims::finish_attached_openings(effective.as_ref(), base, &mut held);
            crate::dims::hole_marks::attach(effective.as_ref(), base, &mut held);
            crate::dims::size_datums::attach(effective.as_ref(), base, &mut held);
            crate::dims::finish_attached_openings(effective.as_ref(), base, &mut held);
            crate::dims::callout_routes::finish_frames(effective.as_ref(), base, &mut held);
        }
        crate::dims::gdt::cited_undrawn(&mut held);
        areas
    };
    // A replay lowers and attaches its glyphs again. Reserve their original
    // areas, rather than feeding attachment-grown strips back into lowering.
    let mut reference = held.clone();
    for (view, area) in reference.views.iter_mut().zip(areas) {
        view.area = area;
    }
    crate::planning::with_pin_reference(reference, || {
        let mut plan = if only_bare_pins(&intent, base, &held) {
            // A bare pin holds the selected finished ink, including peers on every
            // physical page. Re-solving sheet 1 would create a different pin origin.
            held.spec = intent.clone();
            for d in &mut held.dimensions {
                if intent
                    .dimensions
                    .get(&d.id)
                    .is_some_and(|d| d.place.is_some())
                {
                    d.default = false;
                    if !d.from_spec.iter().any(|s| s == "place") {
                        d.from_spec.push("place".into());
                    }
                }
            }
            for g in &mut held.gdt {
                if intent
                    .annotations
                    .get(&g.id)
                    .is_some_and(|a| a.place.is_some())
                {
                    g.default = false;
                    if !g.from_spec.iter().any(|s| s == "place") {
                        g.from_spec.push("place".into());
                    }
                }
            }
            crate::placement_intent::finish(&mut held, analysis);
            held
        } else if canonical
            .views
            .iter()
            .any(|v| ["v:front", "v:plan", "v:side"].contains(&v.id.as_str()) && v.sheet > 1)
            || canonical
                .sheet
                .basis
                .contains(crate::sheets::WHOLE_VIEW_ALLOCATION)
        {
            // A whole-view allocation transported finished ink into its frames; pins
            // replay into the same frames rather than re-solving a forced layout.
            let source = if canonical.sheet.scale == raw.sheet.scale {
                model::plan_automatic(analysis, spec)?
            } else {
                let mut wide = spec.clone();
                wide.sheet.size = Some("A0".into());
                wide.sheet.scale = Some(canonical.sheet.scale.clone());
                wide.sheets.get_or_insert_with(Default::default).max =
                    Some(spec.sheets.as_ref().and_then(|s| s.max).unwrap_or(9));
                crate::planning::fixed_pair(|| model::plan_automatic(analysis, &wide))?
            };
            let mut plan =
            crate::sheets::replay_views(analysis, &source, &canonical).ok_or_else(|| Invalid {
                path: None,
                also: Vec::new(),
                message: "Pinned ink cannot fit in the retained annotated view frames; change its placement or remove the pin.".into(),
                choices: vec!["dimensions/<id>/place".into()],
            })?;
            reanchor_offsets(&mut plan, &canonical, &intent);
            plan
        } else if tolerance_reference(spec).is_none()
            && raw.sheet.size == canonical.sheet.size
            && raw.sheet.scale == canonical.sheet.scale
            && raw.sheets.len() == canonical.sheets.len()
            && raw.views.len() == canonical.views.len()
            && raw
                .views
                .iter()
                .zip(&canonical.views)
                .all(|(a, b)| a.id == b.id && (0..2).all(|k| (a.at[k] - b.at[k]).abs() < 1e-6))
        {
            model::plan_automatic(analysis, spec)?
        } else {
            let mut forced = intent.clone();
            forced.binding = spec.binding.clone();
            forced.sheet.size = Some(canonical.sheet.size.clone());
            forced.sheet.scale = Some(canonical.sheet.scale.clone());
            if count > 1 {
                forced.sheets.get_or_insert_with(Default::default).max = Some(count as u32);
            }
            // The canonical trial's own request (paper, scale and count) usually
            // reproduces its R5 allocation; keep it when every view keeps its page.
            let mut same = forced.clone();
            omit_absent_details(
                analysis,
                &mut same,
                &canonical,
                canonical.sheet.scale_factor,
            );
            let same = crate::planning::fixed_pair(|| model::plan_automatic(analysis, &same))?;
            // The same views on the same pages: pins may move frames within a page.
            let frames = |p: &Plan| {
                p.views
                    .iter()
                    .map(|v| (v.id.clone(), v.sheet))
                    .collect::<Vec<_>>()
            };
            if count > 1 && frames(&same) != frames(&canonical) {
                for view in canonical.views.iter().filter(|v| v.sheet > 1) {
                    forced
                        .views
                        .entry(view.id.trim_start_matches("v:").into())
                        .or_default()
                        .sheet = Some(view.sheet);
                }
                if let Some(table) = canonical.hole_table.as_ref().filter(|t| t.sheet > 1) {
                    // Invariant: The forced multi-sheet Spec was initialized with Some sheets before this branch.
                    forced.sheets.as_mut().unwrap().hole_table = Some(table.sheet);
                }
                for table in &mut forced.tables {
                    if let Some(placed) = canonical
                        .tables
                        .iter()
                        .find(|t| t.id == table.id && t.sheet > 1)
                    {
                        table.sheet = Some(placed.sheet);
                    }
                }
                omit_absent_details(
                    analysis,
                    &mut forced,
                    &canonical,
                    canonical.sheet.scale_factor,
                );
                crate::planning::fixed_pair(|| model::plan_automatic(analysis, &forced))?
            } else {
                same
            }
        };
        if annotation_only_tolerance(spec) && !crate::placement_intent::has_pins(spec) {
            // Tolerances change annotation ink, not view geometry. Transport the
            // edited ink back into the published frames before the final audit.
            if let Some(replayed) = crate::sheets::replay_views(analysis, &plan, &canonical) {
                plan = replayed;
            }
        }
        plan.spec = intent;
        // Replaying a cut into its canonical frame can leave the initial packing
        // estimate's warning behind. Judge its finished ink just as paper trials do.
        if (!spec.views.sections.is_empty()
            || spec.views.get(model::SECTION).is_some_and(|v| !v.omit))
            && plan.problems.iter().any(|p| p.code == "page_fit_uncertain")
            && model::finished_items_fit(analysis, &plan)
        {
            plan.problems.retain(|p| p.code != "page_fit_uncertain");
        }
        drop_internal_replan(&mut plan);
        for problem in canonical
            .problems
            .iter()
            .filter(|p| p.code == "sheet_preference_exceeded")
        {
            if !plan.problems.iter().any(|p| p.code == problem.code) {
                plan.problems.push(problem.clone());
            }
        }
        plan.status = crate::completeness::status(&plan.problems);
        plan.sheet.default = canonical.sheet.default;
        for sheet in &mut plan.sheets {
            sheet.default = canonical.sheet.default;
        }
        let reason = format!(
            "{}: canonical {count} sheet(s) at {} retained for {}",
            canonical.sheet.size,
            canonical.sheet.scale,
            if crate::placement_intent::has_pins(spec) {
                "placement pins"
            } else {
                "annotation edits"
            }
        );
        let reason = if annotation_only_tolerance(spec) && !crate::placement_intent::has_pins(spec)
        {
            plan.sheet.basis.push_str(&format!(
                "; retained annotation reference: {}",
                canonical.sheet.basis
            ));
            canonical
                .sheet_candidates
                .last()
                .map_or(reason, |c| c.outcome.clone())
        } else {
            reason
        };
        explain(&mut plan, canonical.sheet_candidates, &reason);
        Ok(plan)
    })
}

/// A callout offset is measured from the automatic leader tip of the drawing the author
/// saw. Ink transported from another layout keeps its own tip; move each offset callout
/// rigidly so its bend is that canonical tip plus the offset, and report that tip.
fn reanchor_offsets(plan: &mut Plan, canonical: &Plan, intent: &Spec) {
    for d in plan.dimensions.iter_mut().filter(|d| d.kind == "callout") {
        let Some(offset) = intent
            .dimensions
            .get(&d.id)
            .and_then(|s| s.place.as_ref())
            .and_then(|p| p.offset)
        else {
            continue;
        };
        let (Some((_, anchor)), Some(placed)) =
            (canonical.automatic_callout_tip(&d.id), d.placed.as_mut())
        else {
            continue;
        };
        let Some(bend) = placed.lines.first().map(|line| line[1]) else {
            continue;
        };
        let delta = [
            anchor[0] + offset[0] - bend[0],
            anchor[1] + offset[1] - bend[1],
        ];
        for q in placed.lines.iter_mut().chain(&mut placed.arrows).flatten() {
            crate::placement_intent::move_point(q, delta);
        }
        crate::placement_intent::move_point(&mut placed.label_at, delta);
        crate::placement_intent::move_box(&mut placed.label_box, delta);
        if let Some(tip) = canonical.callout_tips.get(&d.id) {
            plan.callout_tips.insert(d.id.clone(), tip.clone());
        }
    }
}

fn only_bare_pins(spec: &Spec, base: &Spec, canonical: &Plan) -> bool {
    let mut unpinned = spec.clone();
    for (id, dimension) in &mut unpinned.dimensions {
        if let Some(place) = &mut dimension.place
            && place.offset.is_some_and(|offset| {
                crate::placement_intent::automatic_offset(canonical, id, offset)
            })
        {
            place.offset = None;
        }
    }
    let mut pins = 0;
    for (id, place) in unpinned
        .dimensions
        .iter_mut()
        .map(|(id, d)| (id, &mut d.place))
        .chain(
            unpinned
                .annotations
                .iter_mut()
                .map(|(id, a)| (id, &mut a.place)),
        )
    {
        let Some(p) = place.as_ref() else { continue };
        // An empty editor placement carries no layout decision. Keep the
        // published peers just as for an absent placement, even beside a pin.
        if *p == Default::default() {
            *place = None;
            continue;
        }
        if !crate::placement_intent::automatic_pin(p) {
            return false;
        }
        // A removed revision item must pass through ordinary lowering so its
        // inactive-entry diagnostic and removal remedy are retained.
        let Some(carrier) = crate::placement_intent::carrier(canonical, id) else {
            return false;
        };
        if let Some(view) = p.view.as_deref() {
            // A matching carrier is an anchor hint. A different or invalid
            // carrier must go through normal placement and validation.
            if carrier.strip_prefix("v:") != Some(view) {
                return false;
            }
        }
        pins += 1;
        *place = None;
    }
    if pins == 0 {
        return false;
    }
    unpinned.dimensions.retain(|_, d| *d != Default::default());
    unpinned.annotations.retain(|_, a| *a != Default::default());
    // Binding and schema migration are metadata, not additional author decisions.
    unpinned.binding.clone_from(&base.binding);
    unpinned.schema.clone_from(&base.schema);
    unpinned == *base
}

fn omit_absent_details(analysis: &Analysis, forced: &mut Spec, baseline: &Plan, scale: f64) {
    for (name, detail) in crate::detail::requests(analysis, forced, scale) {
        if !detail.authored
            && !baseline
                .views
                .iter()
                .any(|v| v.id == format!("v:{name}") && v.detail.is_some())
        {
            forced.views.entry(name).or_default().omit = true;
        }
    }
}

fn explain(plan: &mut Plan, mut candidates: Vec<Candidate>, reason: &str) {
    let maximum = cap(&plan.spec).to_string();
    for candidate in &mut candidates {
        candidate.preferred_max_size = maximum.clone();
        candidate.preferred_max_sheets = preferred_sheets(&plan.spec);
    }
    let attempts = candidates
        .iter()
        .map(|c| {
            format!(
                "{} up to {} sheet(s), scales [{}]: {}",
                c.size,
                c.max,
                c.scales.join(", "),
                c.outcome
            )
        })
        .collect::<Vec<_>>()
        .join("; ");
    plan.sheet.basis = format!(
        "{}; sheet/preferred_max_size {maximum}, sheet/preferred_max_sheets {}; sheet choice: {reason}; {attempts}",
        plan.sheet.basis,
        preferred_sheets(&plan.spec)
    );
    plan.sheet_candidates = candidates;
}

#[cfg(test)]
mod tests {
    #[test]
    fn a_fitting_auxiliary_repack_does_not_earn_an_unreadable_reduction() {
        let Some(a) = corpus("mfcadpp/10224.step") else {
            return;
        };
        let spec:Spec=serde_json::from_value(json!({"sheet":{"size":"A4","scale":"1:1","dimension_spacing":"compact"},"sheets":{"max":1}})).unwrap();
        crate::planning::with(&a, || {
            let before = crate::planning::fixed_pair(|| model::plan_automatic(&a, &spec)).unwrap();
            assert!(!model::finished_items_fit(&a, &before));
            let after = crate::sheets::reflow_outside_auxiliaries(&a, &before)
                .expect("A4 1:1 has room when its off-sheet section is packed into vacant paper");
            assert!(model::finished_items_fit(&a, &after));
            assert!(model::carries_required(&before, &after));
            assert_eq!(after.sheet.scale, "1:1");
            assert!(after.sheets.is_empty());
            assert_eq!(after.views.len(), before.views.len());
            let base = model::plan_recovered(&a, &Spec::default()).unwrap();
            assert_eq!(
                (base.sheet.size.as_str(), base.sheet.scale.as_str()),
                ("A3", "2:1")
            );
            let refusal = minimum_reduction_opening(&a, &after, &base).unwrap_err();
            assert_eq!(
                refusal,
                "reduction refused: dimensioned boss f:boss:1 is 1.296 paper mm, below 1.5 mm"
            );
        });
    }

    use super::*;
    use serde_json::json;
    use std::path::Path;

    fn preferred_scales_descend(c: &Candidate) -> bool {
        let indices: Option<Vec<_>> = c
            .scales
            .iter()
            .map(|name| {
                layout::SCALES
                    .iter()
                    .position(|s| model::scale_name(*s) == *name)
            })
            .collect();
        indices.is_some_and(|indices| indices.windows(2).all(|pair| pair[0] < pair[1]))
    }

    fn fixture() -> Analysis {
        model::analyse_native(
            &Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/prismatic.step"),
        )
        .unwrap()
        .1
    }

    fn corpus(file: &str) -> Option<Analysis> {
        let root = std::env::var_os("DRAFTWRIGHT_CORPUS")
            .map(std::path::PathBuf::from)
            .unwrap_or_else(|| {
                std::path::PathBuf::from(std::env::var_os("HOME").unwrap())
                    .join("repos/quiddity/tests/corpus")
            });
        if !root.is_dir() {
            assert_ne!(
                std::env::var("DRAFTWRIGHT_CORPUS_REQUIRED").as_deref(),
                Ok("1")
            );
            return None;
        }
        Some(model::analyse_native(&root.join(file)).unwrap().1)
    }

    /// Linear dimension lines beyond each view's outline, per view and side: the first line's
    /// distance from the outline, the gaps between neighbouring parallel lines (paper mm,
    /// rounded to 0.01), and the least clearance from a label's box to another parallel line
    /// it spans on that side.
    fn lane_gaps(analysis: &Analysis, plan: &Plan) -> (Vec<f64>, Vec<f64>, f64) {
        let round = |v: f64| (v * 100.0).round() / 100.0;
        let (mut firsts, mut pitches, mut clear) = (Vec::new(), Vec::new(), f64::INFINITY);
        for (view, o) in plan.views.iter().zip(sheet_fit::footprints(analysis, plan)) {
            // The line's constant coordinate, the outward sign and the outline's edge.
            for (k, sign, edge) in [
                (1, -1.0, o[1]),
                (1, 1.0, o[3]),
                (0, -1.0, o[0]),
                (0, 1.0, o[2]),
            ] {
                let lines: Vec<(f64, [f64; 2], [f64; 4])> = plan
                    .dimensions
                    .iter()
                    .filter(|d| d.view == view.id && d.kind == "linear")
                    .filter_map(|d| {
                        let p = d.placed.as_ref()?;
                        let [a, b] = *p.lines.last()?;
                        ((a[k] - b[k]).abs() < 1e-6 && (a[1 - k] - b[1 - k]).abs() > 1.0).then(
                            || {
                                let span = [a[1 - k].min(b[1 - k]), a[1 - k].max(b[1 - k])];
                                (a[k], span, p.label_box)
                            },
                        )
                    })
                    .filter(|(at, _, _)| sign * (at - edge) > 0.5)
                    .collect();
                let mut at: Vec<f64> = lines.iter().map(|l| sign * (l.0 - edge)).collect();
                at.sort_by(f64::total_cmp);
                at.dedup_by(|a, b| (*a - *b).abs() < 0.01);
                if let Some(first) = at.first() {
                    firsts.push(round(*first));
                }
                pitches.extend(at.windows(2).map(|w| round(w[1] - w[0])));
                for (own, _, label) in &lines {
                    let (low, high) = (label[k], label[k + 2]);
                    for (other, span, _) in &lines {
                        if (other - own).abs() < 0.01
                            || span[1] < label[1 - k]
                            || span[0] > label[3 - k]
                        {
                            continue;
                        }
                        clear = clear.min((low - other).max(other - high).max(0.0));
                    }
                }
            }
        }
        (firsts, pitches, clear)
    }

    fn spaced(spacing: Option<&str>, sheet: serde_json::Value) -> Spec {
        let mut sheet = sheet;
        if let Some(spacing) = spacing {
            sheet["dimension_spacing"] = json!(spacing);
        }
        serde_json::from_value(json!({ "sheet": sheet })).unwrap()
    }

    #[test]
    fn compact_lanes_keep_their_pitch_and_label_clearance() {
        let Some(a) = corpus("mfcadpp/1013.step") else {
            return;
        };
        let fixed = json!({"size":"A3","scale":"1:1"});
        for (spacing, first, pitch) in [("normal", 10.0, 7.0), ("compact", 7.0, 5.5)] {
            let p = model::plan_automatic(&a, &spaced(Some(spacing), fixed.clone())).unwrap();
            let (firsts, pitches, clear) = lane_gaps(&a, &p);
            assert_eq!(
                p.sheet.dimension_spacing == model::DimensionSpacing::Compact,
                spacing == "compact"
            );
            assert_eq!(
                firsts.iter().copied().reduce(f64::min),
                Some(first),
                "{spacing}"
            );
            assert!(pitches.contains(&pitch), "{spacing}: {pitches:?}");
            for gap in &pitches {
                let lanes = gap / pitch;
                assert!(
                    (lanes - lanes.round()).abs() < 0.01 && lanes >= 1.0,
                    "{spacing}: {pitches:?}"
                );
            }
            // A label's top stays 1.5 mm clear of the next line.
            assert!(clear >= 1.5, "{spacing}: {clear}");
        }
    }

    #[test]
    fn compact_spacing_is_tried_before_larger_paper() {
        let Some(crowded) = corpus("mfcadpp_holdout/119.step") else {
            return;
        };
        let paper = |p: &Plan| layout::PAGES.iter().position(|q| q.0 == p.sheet.size);
        let compact = |p: &Plan| p.sheet.dimension_spacing == model::DimensionSpacing::Compact;
        // Normal lanes need A3 for this part; compact lanes keep it on A4. (1013 was the witness
        // until its semicircular blind slot was drawn (#233); normal lanes now fit it on A4.)
        let auto = select(&crowded, &Spec::default()).unwrap();
        let normal = select(&crowded, &spaced(Some("normal"), json!({}))).unwrap();
        assert!(compact(&auto) && !compact(&normal));
        assert!(
            paper(&auto) < paper(&normal),
            "{} vs {}",
            auto.sheet.size,
            normal.sheet.size
        );
        assert!(auto.sheet.basis.contains("with compact dimension spacing"));
        assert!(!normal.sheet.basis.contains("compact"));
        // The Spec keeps the author's intent; only the plan records the fallback.
        assert_eq!(auto.spec.sheet.dimension_spacing, None);
    }

    #[test]
    fn a_local_fix_retains_the_compact_fallback() {
        // A part whose finished drawing takes the automatic compact fallback. (mfcadpp/1013 no
        // longer does once its obround pocket callout aims at a straight wall, nor the pocket_z
        // fixture with the 3 mm frame gutter, 2026-10-25, nor mfcadpp_holdout/119 once quiddity
        // recognises its paired-ramp step, 2026-10-26.)
        let Some(a) = corpus("mfcadpp_holdout/275.step") else {
            return;
        };
        let before = model::plan_recovered(&a, &Spec::default()).unwrap();
        assert_eq!(
            before.sheet.dimension_spacing,
            model::DimensionSpacing::Compact
        );
        let fix = before
            .problems
            .iter()
            .flat_map(|p| p.offered_fixes())
            .find(|f| f.ops.iter().any(|op| op.path == "sheet/keep_layout"))
            .expect("a retained local fix");
        assert!(
            fix.ops.iter().any(
                |op| op.path == "sheet/dimension_spacing" && op.value == Some(json!("compact"))
            )
        );
        let ops = serde_json::to_value(&fix.ops).unwrap();
        let after = model::plan_recovered(&a, &model::edit(&before.spec, &ops).unwrap()).unwrap();
        assert_eq!(
            after.sheet.dimension_spacing,
            model::DimensionSpacing::Compact
        );
        assert_eq!(
            (&after.sheet.size, &after.sheet.scale),
            (&before.sheet.size, &before.sheet.scale)
        );
    }

    #[test]
    fn page_fit_repair_keeps_a_clean_a4_at_full_size() {
        let Some(a) = corpus("mfcadpp/10247.step") else {
            return;
        };
        let plan = select(&a, &Spec::default()).unwrap();
        assert_eq!(
            (plan.sheet.size.as_str(), plan.sheet.scale.as_str()),
            ("A4", "1:1"),
            "{}",
            plan.sheet.basis
        );
        assert!(frame_conflicts(&plan).is_empty());
        assert!(sheet_fit::inside_frames(&a, &plan));
        assert!(model::findings(&plan).clean());
    }

    #[test]
    fn routing_contacts_do_not_shrink_a_readable_plate() {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/revisions/plate_thickened.new.step.gz");
        let a = model::analyse_native(&path).unwrap().1;
        let plan = select(&a, &Spec::default()).unwrap();
        assert_eq!(
            (plan.sheet.size.as_str(), plan.sheet.scale.as_str()),
            ("A4", "1:1"),
            "{}",
            plan.sheet.basis
        );
        let a4 = &plan.sheet_candidates[0];
        assert_eq!(a4.size, "A4");
        assert_eq!(a4.scales, ["1:1"], "{}", plan.sheet.basis);
        assert!(a4.outcome.contains("usable"), "{}", a4.outcome);
        assert_eq!(crate::dims::leader_on_extension(&plan.dimensions).len(), 4);
    }

    #[test]
    fn converging_small_feature_heads_and_additional_crowding_are_not_isolated_contacts() {
        let fixture = |name: &str| {
            model::analyse_native(
                &Path::new(env!("CARGO_MANIFEST_DIR"))
                    .join("tests/fixtures")
                    .join(name),
            )
            .unwrap()
            .1
        };
        let a = fixture("rule_pins/grm04_drive_plate.step.gz");
        let small = model::plan_recovered(
            &a,
            &serde_json::from_value(json!({"sheet":{"size":"A4","scale":"2:1"}})).unwrap(),
        )
        .unwrap();
        let large = model::plan_recovered(
            &a,
            &serde_json::from_value(
                json!({"sheet":{"size":"A4","scale":"5:1","dimension_spacing":"compact"}}),
            )
            .unwrap(),
        )
        .unwrap();
        // The 2.4 mm opening prints at 4.8 mm, below the 8 mm threshold.
        // At 5:1 it prints at 12 mm; its annotations can be read independently.
        assert!(crate::dims::converging_terminations(&a, &small));
        assert!(!crate::dims::converging_terminations(&a, &large));
        assert!(isolated_contacts_readable(&a, &small, &large).is_err());
        assert!(
            reduction_readable(&a, &small, &large).is_err(),
            "the convergence backstop applies even without a cosmetic exception"
        );

        let a = fixture("parts/k5/pad-x-positive.step");
        let plan = |scale| {
            model::plan_recovered(
                &a,
                &serde_json::from_value(json!({"sheet":{"size":"A4","scale":scale}})).unwrap(),
            )
            .unwrap()
        };
        let (before, after) = (plan("1:1"), plan("2:1"));
        assert!(crowded_labels(&after) > crowded_labels(&before));
        assert!(isolated_contacts_readable(&a, &after, &before).is_err());
    }

    #[test]
    fn automatic_trials_do_not_claim_an_authored_sheet_request() {
        for file in ["u_channel.step", "u_channel_asymmetric.step"] {
            let path = Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/recognition/issues")
                .join(file);
            let a = model::analyse_native(&path).unwrap().1;
            let plan = select(&a, &Spec::default()).unwrap();
            assert_eq!(
                (plan.sheet.size.as_str(), plan.sheet.scale.as_str()),
                ("A4", "1:1"),
                "{file}: {}",
                plan.sheet.basis
            );
            assert!(plan.sheet.default);
            assert!(
                plan.problems
                    .iter()
                    .all(|p| p.severity != "warning" || p.code == "general_tolerance_missing"),
                "{file}: {:?}",
                plan.problems
            );
            assert!(
                !crate::dims::recheck(&a, &plan)
                    .iter()
                    .any(|p| p.code == "leader_through_feature"),
                "{file}: the published leader must clear the hole ink"
            );
            assert!(
                !plan.sheet.basis.contains("as the spec asks"),
                "{}",
                plan.sheet.basis
            );
        }
    }

    #[test]
    fn retained_clean_layout_does_not_claim_no_valid_layout_exists() {
        let Some(a) = corpus("mfcadpp/10046.step") else {
            return;
        };
        let plan = select(&a, &Spec::default()).unwrap();
        assert!(model::findings(&plan).clean());
        assert!(
            plan.sheet.basis.contains("A4 at 2:1 (Normal): refused"),
            "{}",
            plan.sheet.basis
        );
        for problem in plan
            .problems
            .iter()
            .filter(|p| p.code == "layout_recovery_exhausted")
        {
            assert!(
                problem.message.contains("A4 at 2:1 (Normal): refused"),
                "{}",
                problem.message
            );
            assert!(
                problem.message.contains("no alternative"),
                "{}",
                problem.message
            );
            assert!(
                !problem.message.contains("has valid and complete"),
                "{}",
                problem.message
            );
            assert!(
                !problem
                    .message
                    .contains("no standard sheet and legible scale yields"),
                "{}",
                problem.message
            );
        }
    }

    #[test]
    fn compact_is_retried_only_for_a_lack_of_lane_room() {
        let a = fixture();
        let plan = model::plan_automatic(&a, &Spec::default()).unwrap();
        assert!(sheet_fit::inside_frames(&a, &plan));
        let with = |code: &str, subjects: &[&str]| {
            let mut p = plan.clone();
            p.problems.push(model::Problem {
                code: code.into(),
                severity: "warning".into(),
                subjects: subjects.iter().map(|s| s.to_string()).collect(),
                ..Default::default()
            });
            compact_may_help(&a, &p)
        };
        assert!(with("page_fit_uncertain", &["sheet"]));
        assert!(with("callout_dropped", &["d:x"]));
        assert!(with("annotation_off_sheet", &["d:x"]));
        // Collisions and unrelated furniture are not cured by tighter lanes.
        assert!(!with("annotation_ink_overlap", &["d:x", "d:y"]));
        assert!(!with("title_field_overflow", &["sheet"]));
        assert!(!with("annotation_off_sheet", &["v:iso"]));
        // Clipped ink far wider than every view's lane saving: no compact retry.
        let mut wide = plan.clone();
        wide.views[0].at[0] += 1000.0;
        wide.views[0].area[0] += 1000.0;
        wide.views[0].area[2] += 1000.0;
        assert!(!sheet_fit::inside_frames(&a, &wide));
        assert!(!compact_may_help(&a, &wide));
    }

    #[test]
    fn plain_parts_keep_normal_spacing_and_explicit_compact_holds() {
        let a = fixture();
        let auto = select(&a, &Spec::default()).unwrap();
        assert_eq!(
            auto.sheet.dimension_spacing,
            model::DimensionSpacing::Normal
        );
        let (firsts, _, _) = lane_gaps(&a, &auto);
        assert!(
            !firsts.is_empty() && firsts.iter().all(|f| *f >= 10.0),
            "{firsts:?}"
        );
        let compact = select(&a, &spaced(Some("compact"), json!({}))).unwrap();
        assert_eq!(
            compact.sheet.dimension_spacing,
            model::DimensionSpacing::Compact
        );
        let (firsts, _, _) = lane_gaps(&a, &compact);
        assert_eq!(firsts.iter().copied().reduce(f64::min), Some(7.0));
        assert!(
            serde_json::from_value::<Spec>(json!({"sheet":{"dimension_spacing":"tight"}})).is_err()
        );
    }

    #[test]
    fn extension_clearance_uses_padding_and_baseline_item_pairs() {
        let a = fixture();
        let mut p = model::plan_automatic(&a, &Spec::default()).unwrap();
        let mut linear = p.dimensions[0].clone();
        linear.id = "linear".into();
        linear.kind = "linear".into();
        let ink = linear.placed.as_mut().unwrap();
        ink.lines = vec![
            [[20., 20.], [20., 40.]],
            [[20., 20.], [20., 40.]],
            [[20., 40.], [30., 40.]],
        ];
        ink.arrows = vec![[[20., 40.], [22., 40.]], [[30., 40.], [28., 40.]]];
        let mut callout = linear.clone();
        callout.id = "callout".into();
        callout.kind = "callout".into();
        let ink = callout.placed.as_mut().unwrap();
        ink.lines = vec![[[21., 30.], [25., 30.]]];
        ink.arrows.truncate(1);
        ink.label_box = [22., 10., 25., 12.];
        p.dimensions = vec![linear, callout];
        let contacts = |p: &Plan| {
            failures(&a, p, 0.)
                .into_iter()
                .filter(|(code, _)| code == "leader_on_extension")
                .collect::<BTreeSet<_>>()
        };
        let inherited = contacts(&p);
        assert_eq!(
            inherited.len(),
            1,
            "inclusive 1 mm corridor; one pair per owner"
        );
        assert!(contacts(&p).difference(&inherited).next().is_none());
        p.dimensions[1].id = "another-callout".into();
        assert_eq!(contacts(&p).difference(&inherited).count(), 1);
        p.dimensions[1].placed.as_mut().unwrap().lines[0][0][0] = 21.001;
        assert!(contacts(&p).is_empty());
        p.dimensions[1].placed.as_mut().unwrap().label_box = [21., 25., 24., 27.];
        assert_eq!(contacts(&p).len(), 1, "padded text also counts");
        p.dimensions[1].placed = None;
        assert!(contacts(&p).is_empty());
    }

    #[test]
    fn smaller_sheets_do_not_gain_extension_contacts() {
        for file in [
            "gramel/GRM-03_thumbwheel_drive_screw.step",
            "ttt_inputs/sm-hanger.step",
        ] {
            let Some(a) = corpus(file) else { continue };
            let p = model::plan_recovered(&a, &Spec::default()).unwrap();
            assert!(
                crate::dims::leader_on_extension(&p.dimensions).is_empty(),
                "{file}"
            );
            // #221: GRM-03's A4 trial is finished free of its contact, so A4 is kept. #163: the
            // hanger's A4 trial, judged by its ink rather than an empty band past the border,
            // is free of contacts too (asserted above for every sheet), so A4 is kept.
            assert_eq!(p.sheet.size, "A4", "{file}");
        }
    }

    #[test]
    fn retaining_bare_pin_ink_does_not_ignore_other_author_decisions() {
        let a = fixture();
        let canonical = model::plan_automatic(&a, &Spec::default()).unwrap();
        let dimension = canonical
            .dimensions
            .iter()
            .find(|d| d.placed.is_some() && d.kind == "linear")
            .unwrap();
        let id = &dimension.id;
        let mut spec: Spec = serde_json::from_value(json!({
            "dimensions":{id:{"place":{"pinned":true}}}
        }))
        .unwrap();
        let base = model::pin_base(&spec);
        assert!(only_bare_pins(&spec, &base, &canonical));
        spec.dimensions
            .get_mut(id)
            .unwrap()
            .place
            .as_mut()
            .unwrap()
            .offset = Some([0., 0.]);
        assert!(only_bare_pins(&spec, &base, &canonical));
        spec.dimensions
            .get_mut(id)
            .unwrap()
            .place
            .as_mut()
            .unwrap()
            .offset = Some([1., 0.]);
        assert!(!only_bare_pins(&spec, &base, &canonical));
        spec.dimensions
            .get_mut(id)
            .unwrap()
            .place
            .as_mut()
            .unwrap()
            .offset = None;
        spec.dimensions
            .get_mut(id)
            .unwrap()
            .place
            .as_mut()
            .unwrap()
            .view = Some("not-a-view".into());
        assert!(!only_bare_pins(&spec, &base, &canonical));
        assert!(model::plan_recovered(&a, &spec).is_err());
        spec.dimensions
            .get_mut(id)
            .unwrap()
            .place
            .as_mut()
            .unwrap()
            .view = Some(dimension.view.trim_start_matches("v:").into());
        assert!(only_bare_pins(&spec, &base, &canonical));
        spec.dimensions.get_mut(id).unwrap().omit = true;
        assert!(!only_bare_pins(&spec, &base, &canonical));
        spec.dimensions.get_mut(id).unwrap().omit = false;
        spec.views.entry("iso".into()).or_default().omit = true;
        assert!(!only_bare_pins(&spec, &base, &canonical));
    }

    #[test]
    fn automatic_pins_keep_canonical_paper_scale_and_sheet_count() {
        let Some(a) = corpus("gramel/GRM-03_thumbwheel_drive_screw.step") else {
            return;
        };
        let tapped = model::analyse_native(
            &Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/pmi/step/string_post_tapped.step.gz"),
        )
        .unwrap()
        .1;
        let ftc08 = corpus("nist/nist_ftc_08_asme1_rc.stp");
        for a in [&tapped, &a].into_iter().chain(ftc08.as_ref()) {
            let canonical = model::plan_recovered(a, &Spec::default()).unwrap();
            let id = &canonical
                .dimensions
                .iter()
                .find(|d| d.kind == "callout" && d.placed.is_some())
                .unwrap()
                .id;
            let s: Spec = serde_json::from_value(json!({
                "dimensions": {id: {"place": {"pinned": true}}}
            }))
            .unwrap();
            let pinned = model::plan_recovered(a, &s).unwrap();
            let choice = |p: &Plan| (p.sheet.size.clone(), p.sheet.scale.clone(), p.sheets.len());
            assert_eq!(choice(&pinned), choice(&canonical));
            assert!(pinned.spec.sheet.size.is_none());
            assert!(pinned.spec.sheet.scale.is_none());
            assert!(pinned.spec.sheets.as_ref().and_then(|s| s.max).is_none());
            for d in &canonical.dimensions {
                let replayed = pinned.dimensions.iter().find(|p| p.id == d.id).unwrap();
                assert_eq!(replayed.placed, d.placed, "bare pin moved {}", d.id);
            }
        }
    }

    /// Opt-in survey of the *unrestricted* explicit-paper planner. Normal tests
    /// exercise the bound below; this audit is deliberately separate from timing.
    #[test]
    #[ignore = "set DRAFTWRIGHT_SHEET_BOUND_SURVEY to a JSON array of analysis/baseline/selected paths"]
    fn corpus_bound_survey() {
        let path = std::env::var("DRAFTWRIGHT_SHEET_BOUND_SURVEY").unwrap();
        let rows: Vec<serde_json::Value> =
            serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
        let mut results = Vec::new();
        let mut counterexamples = Vec::new();
        for row in rows {
            let read = |key: &str| std::fs::read(row[key].as_str().unwrap()).unwrap();
            let analysis: Analysis = serde_json::from_slice(&read("analysis")).unwrap();
            let baseline: Plan = serde_json::from_slice(&read("baseline")).unwrap();
            let selected: Plan = serde_json::from_slice(&read("selected")).unwrap();
            if !selected
                .sheet_candidates
                .iter()
                .any(|r| r.kind.as_deref() == Some(RULED_OUT))
            {
                continue;
            }
            // Match production's baseline preparation scope, including its
            // existing canonical and established-route policies.
            crate::planning::with(&analysis, || {
                let baseline = model::plan_automatic(&analysis, &baseline.spec).unwrap();
                let floor = readable_floor(&analysis, &baseline, &baseline.spec);
                let bound_floor = floor;
                std::fs::write(
                    format!("{path}.{}.baseline.json", row["name"].as_str().unwrap()),
                    serde_json::to_vec(&baseline).unwrap(),
                )
                .unwrap();
                let estimate = Estimate::new(&analysis, &baseline);
                let mut inherited = failures(&analysis, &baseline, floor);
                inherited.extend(baseline.problems.iter().map(problem_key));
                for request in &selected.sheet_candidates {
                    if request.kind.as_deref() != Some(RULED_OUT) {
                        continue;
                    }
                    assert!(
                        estimate
                            .scale(&request.size, request.max, bound_floor)
                            .is_none()
                    );
                    let mut forced = baseline.spec.clone();
                    forced.sheet.size = Some(request.size.clone());
                    forced.sheet.scale = None;
                    if request.max > 1 {
                        forced.sheets.get_or_insert_with(Default::default).max =
                            Some(request.max as u32);
                    }
                    let full_path = format!(
                        "{path}.{}-{}-{}.full.json",
                        row["name"].as_str().unwrap(),
                        request.size,
                        request.max
                    );
                    let full = if let Ok(bytes) = std::fs::read(&full_path) {
                        Ok(serde_json::from_slice::<Plan>(&bytes).unwrap())
                    } else {
                        let result = model::plan_automatic(&analysis, &forced);
                        if let Ok(plan) = &result {
                            std::fs::write(&full_path, serde_json::to_vec(plan).unwrap()).unwrap();
                        }
                        result
                    };
                    let codes: BTreeSet<_> = match &full {
                        Ok(plan) => failures(&analysis, plan, floor)
                            .difference(&inherited)
                            .map(|(c, _)| c.clone())
                            .collect(),
                        Err(error) => BTreeSet::from([error.message.clone()]),
                    };
                    if codes.is_empty() {
                        counterexamples.push(format!(
                            "{} {} max={}",
                            row["name"], request.size, request.max
                        ));
                    }
                    let result = json!({"name":row["name"], "size":request.size, "max":request.max, "codes":codes,
                        "full_choice":full.as_ref().ok().map(|p| (&p.sheet.size, &p.sheet.scale, 1+p.sheets.len()))});
                    eprintln!("{}", result);
                    results.push(result);
                }
            });
        }
        std::fs::write(
            format!("{path}.results.json"),
            serde_json::to_vec_pretty(&results).unwrap(),
        )
        .unwrap();
        assert!(
            counterexamples.is_empty(),
            "false refusals: {counterexamples:?}"
        );
    }

    #[test]
    fn paper_bands_and_furniture_do_not_shrink_with_the_outline() {
        let estimate = Estimate {
            views: vec![("v:front".into(), [100.0, 80.0], [280.0, 15.0])],
            blocks: Vec::new(),
            first_blocks: 0,
            scale: 1.0,
            aligned: true,
        };
        // An outline-only test would accept A4 at 1:10. A fixed-width
        // annotation reservation exceeds it even with additional sheets.
        assert_eq!(estimate.scale("A4", 1, 0.1), None);
        assert_eq!(estimate.scale("A4", 2, 0.1), None);
        assert_eq!(estimate.scale("A3", 1, 0.1), Some(1.0));
        let with_table = Estimate {
            blocks: vec![vec![[401.0, 20.0]]],
            ..estimate
        };
        assert_eq!(with_table.scale("A3", 9, 0.1), None);
    }

    #[test]
    fn a_tall_hole_table_is_judged_in_its_split_form() {
        // CTC-02's plan schedule: 115 × 441 mm whole, too tall for A2's 394 mm; split into
        // two blocks it is 236 × 231 mm and does not rule A2 out.
        let whole = [115.0, 441.0];
        let split = [236.0, 231.0];
        let estimate = |blocks| Estimate {
            views: vec![("v:front".into(), [200.0, 100.0], [0.0; 2])],
            blocks,
            first_blocks: 1,
            scale: 1.0,
            aligned: true,
        };
        assert!(!estimate(vec![vec![whole]]).fits("A2", 1, 1.0));
        assert!(estimate(vec![vec![whole, split]]).fits("A2", 1, 1.0));
        // A table that fits whole is judged whole, as it is laid out.
        assert!(estimate(vec![vec![whole, split]]).fits("A1", 1, 1.0));
        assert!(
            estimate(vec![vec![whole]])
                .shortfall("A2", 1, 1.0)
                .is_some_and(|s| s.starts_with("a table needs 115 × 441 mm"))
        );
    }

    #[test]
    fn tall_furniture_must_fit_beside_both_aligned_principals() {
        let estimate = Estimate {
            views: vec![
                ("v:front".into(), [200.0, 100.0], [0.0; 2]),
                ("v:side".into(), [70.0, 100.0], [0.0; 2]),
            ],
            blocks: vec![vec![[150.0, 238.0]]],
            first_blocks: 1,
            scale: 1.0,
            aligned: true,
        };
        // Total area and every individual side fit A3. The tall schedule
        // forces both aligned projections beside it; their widths do not.
        assert!(!estimate.fits("A3", 1, 1.0));
        assert!(!estimate.fits("A3", 2, 1.0));
        assert!(estimate.fits("A3", 1, 0.5));
        let movable = Estimate {
            first_blocks: 0,
            ..estimate
        };
        assert!(!movable.fits("A3", 1, 1.0));
        assert!(movable.fits("A3", 2, 1.0));
    }

    #[test]
    fn stacked_front_and_plan_rule_out_only_a_single_sheet() {
        // Each view fits A2 alone, but stacked they exceed its 400 mm drawable height.
        // With a second sheet R5 may give the plan its own page, so the estimate
        // must not rule that request out.
        let estimate = Estimate {
            views: vec![
                ("v:front".into(), [400.0, 100.0], [0.0; 2]),
                ("v:plan".into(), [400.0, 320.0], [0.0; 2]),
            ],
            blocks: Vec::new(),
            first_blocks: 0,
            scale: 1.0,
            aligned: true,
        };
        assert!(
            estimate
                .shortfall("A2", 1, 1.0)
                .is_some_and(|s| s.starts_with("aligned front and plan views need at least"))
        );
        assert_eq!(estimate.shortfall("A2", 2, 1.0), None);
        // NIST CTC-04 is the corpus case: a full plan on two A2 sheets precedes one A1.
        let Some(a) = corpus("nist/nist_ctc_04_asme1_rd.stp") else {
            return;
        };
        let p = model::plan_recovered(&a, &Spec::default()).unwrap();
        assert_eq!(
            (
                p.sheet.size.as_str(),
                p.sheet.scale.as_str(),
                1 + p.sheets.len()
            ),
            ("A2", "1:5", 2)
        );
        let two = p
            .sheet_candidates
            .iter()
            .find(|c| c.size == "A2" && c.max == 2)
            .unwrap();
        assert_eq!(two.kind, None);
        // Section A-A moves to sheet 2, but its cutting line stays drawn on the plan:
        // no hole-schedule tag there may sit on it.
        assert!(p.views.iter().any(|v| v.cut.is_some() && v.sheet == 2));
        let plan = p.views.iter().find(|v| v.id == "v:plan").unwrap();
        let cut = plan
            .line_work
            .iter()
            .find(|w| w.kind == "cutting_plane")
            .unwrap();
        let crate::output::Curve::Line(a, b) = cut.curves[0] else {
            panic!("cutting-plane line work is a line");
        };
        let tags: Vec<_> = p
            .balloons
            .iter()
            .filter(|t| t.view == "v:plan")
            .filter_map(|t| t.label_box.map(|r| (&t.id, r)))
            .collect();
        assert!(!tags.is_empty());
        for (id, r) in tags {
            let across = r[0] < a[0].max(b[0]) && r[2] > a[0].min(b[0]);
            assert!(
                !(across && r[1] < a[1] && r[3] > a[1]),
                "{id} sits on the cutting line"
            );
        }
    }

    #[test]
    fn a_ruled_out_request_states_its_shortfall_and_paper() {
        let estimate = Estimate {
            views: vec![("v:front".into(), [100.0, 80.0], [280.0, 15.0])],
            blocks: Vec::new(),
            first_blocks: 0,
            scale: 1.0,
            aligned: true,
        };
        // Even at the smallest readable scale the fixed band exceeds A4's 271 mm (277 mm inside
        // the frame, less the 3 mm frame gutter on each side).
        assert_eq!(estimate.scale("A4", 1, 0.5), None);
        assert_eq!(
            estimate.ruled_out("A4", 1, 0.5),
            "ruled out: the front view with its annotation bands needs at least 330 × 55 mm at 1:2; \
             A4 drawable area is 271 × 184 mm; 1:2 is the smallest readable scale"
        );
        // These trials also appear in public recovery messages, including
        // when a principal view was omitted. Name views as the drawing does.
        for (id, name) in [
            ("v:front", "the front view"),
            ("v:plan", "the plan view"),
            ("v:side", "the side view"),
            ("v:section_aa", "section A–A"),
        ] {
            let mut named = estimate.clone();
            named.views[0].0 = id.into();
            let message = named.ruled_out("A4", 1, 0.5);
            assert!(message.starts_with(&format!("ruled out: {name} with its annotation bands")));
            assert_eq!(crate::wording::carries_id(&message), None, "{message}");
        }
        // A floor above every standard scale has no smallest readable scale to cite.
        assert!(
            estimate
                .ruled_out("A3", 1, 20.0)
                .starts_with("ruled out: no standard scale reaches the readable floor")
        );
    }

    #[test]
    fn an_estimate_at_the_limit_is_not_ruled_out() {
        // The necessary test refuses only a strict excess: a footprint exactly filling the
        // drawable width is planned in full.
        let at_limit = |width: f64| Estimate {
            views: vec![("v:front".into(), [width - 20.0, 100.0], [20.0, 10.0])],
            blocks: Vec::new(),
            first_blocks: 0,
            scale: 1.0,
            aligned: true,
        };
        // A4's drawable width inside the 3 mm frame gutter.
        assert_eq!(at_limit(271.0).scale("A4", 1, 1.0), Some(1.0));
        assert_eq!(at_limit(271.01).scale("A4", 1, 1.0), None);
        // mfcadpp holdout 317's aligned front and side estimate is within 5% of A4's drawable
        // width at 1:2, and that full trial is the winner. (mfcadpp 10163 was this case until
        // quiddity-rust 0.2.0 read one fewer fillet and it moved off the limit.) Its plan view,
        // which no dimension needs, is named so that it is kept (#222 would leave it out and
        // reach 1:1 off the limit).
        let Some(a) = corpus("mfcadpp_holdout/317.step") else {
            return;
        };
        let spec: Spec = serde_json::from_value(json!({"views": {"plan": {}}})).unwrap();
        let p = model::plan_recovered(&a, &spec).unwrap();
        assert_eq!(
            (
                p.sheet.size.as_str(),
                p.sheet.scale.as_str(),
                p.sheets.len()
            ),
            ("A4", "1:2", 0)
        );
        assert_eq!(p.sheet_candidates.len(), 1);
        let trial = p.sheet_candidates.last().unwrap();
        assert_eq!((trial.size.as_str(), trial.max), ("A4", 1));
        assert_eq!(trial.kind, None);
        assert_eq!(trial.scales, ["1:1", "1:2"]);
        assert_eq!(trial.scales.last(), Some(&p.sheet.scale));
        assert_eq!(
            trial.outcome,
            "A4: usable at 1:2 (1 sheet(s)); nothing clipped, readable scale, no unreadable ink or required losses"
        );
        crate::planning::with(&a, || {
            let baseline = model::plan_automatic(&a, &spec).unwrap();
            let estimate = Estimate::new(&a, &baseline);
            assert!(estimate.fits("A4", 1, 0.5));
            let wider = Estimate {
                views: estimate
                    .views
                    .iter()
                    .map(|(id, o, b)| (id.clone(), [o[0] * 1.05, o[1]], [b[0] * 1.05, b[1]]))
                    .collect(),
                ..estimate
            };
            assert!(!wider.fits("A4", 1, 0.5));
        });
    }

    #[test]
    fn primary_step_readability_bounds_both_a4_requests() {
        let Some(a) = corpus("cadgenbench/threaded_connector_109.step") else {
            return;
        };
        let p = model::plan_recovered(&a, &Spec::default()).unwrap();
        assert_eq!(
            (p.sheet.size.as_str(), p.sheet.scale.as_str()),
            ("A3", "1:5")
        );
        assert_eq!(p.sheet_candidates.len(), 2);
        for candidate in &p.sheet_candidates[..1] {
            assert_eq!(candidate.size, "A4");
            assert!(candidate.scales.is_empty());
            assert_eq!(candidate.kind.as_deref(), Some(RULED_OUT));
            assert!(candidate.outcome.starts_with("ruled out: "));
        }
    }

    #[test]
    fn all_preferred_scales_are_considered_before_spending_more_paper_for_route_quality() {
        // GRM-02 has route defects beyond an isolated extension contact. The new
        // scale policy tries every readable A4 scale before retaining clean A3.
        let a = model::analyse_native(
            &Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/grm02.step"),
        )
        .unwrap()
        .1;
        let p = model::plan_recovered(&a, &Spec::default()).unwrap();
        let a4 = &p.sheet_candidates[0];
        assert_eq!((a4.size.as_str(), a4.max), ("A4", 1));
        // The banded pre-filter now proves 5:1 wider than A4 before lowering
        // its ink. Retain explicit coverage of that refused preferred scale.
        assert_eq!(a4.scales, ["2:1", "1:1"], "{}", p.sheet.basis);
        assert!(p.sheet.basis.contains("A4 at 5:1: not planned: aligned front and side views need at least 282 mm across at 5:1; A4 drawable area is 271 × 184 mm"),"{}",p.sheet.basis);
        assert_eq!(
            a4.outcome,
            "refused at 1:1: a callout grows more than 2 paper mm beyond the displaced drawing (0 hard, 0 drops; annotation_leader_crossing, extension_through_feature, leader_ink_run, leader_on_extension, leader_through_feature); cosmetic exception refused: at least two annotations terminate within 1 mm of a feature smaller than 8 paper mm"
        );
        for scale in &a4.scales {
            assert!(p.sheet.basis.contains(&format!("A4 at {scale}")));
        }
        assert_eq!(
            (p.sheet.size.as_str(), p.sheet.scale.as_str()),
            ("A3", "5:1")
        );
        // The gear blank's A4 trial is usable at its first scale (#163).
        let a = model::analyse_native(
            &Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/gear_blank.step"),
        )
        .unwrap()
        .1;
        let p = model::plan_recovered(&a, &Spec::default()).unwrap();
        let a4 = &p.sheet_candidates[0];
        assert_eq!((a4.size.as_str(), a4.max), ("A4", 1));
        assert!(a4.outcome.contains("usable"), "{}", a4.outcome);
        assert_eq!(a4.scales.len(), 1, "{}", p.sheet.basis);
        // GRM-03's A4 trial gained only a contact its finishing pass clears (#221): usable at
        // its first scale.
        let Some(a) = corpus("gramel/GRM-03_thumbwheel_drive_screw.step") else {
            return;
        };
        let p = model::plan_recovered(&a, &Spec::default()).unwrap();
        let a4 = &p.sheet_candidates[0];
        assert_eq!((a4.size.as_str(), a4.max), ("A4", 1));
        assert!(a4.outcome.contains("usable"), "{}", a4.outcome);
        assert_eq!(a4.scales.len(), 1, "{}", p.sheet.basis);
        // 10255's A4 trial runs off the sheet: lack of room earns one smaller scale.
        let Some(a) = corpus("mfcadpp/10255.step") else {
            return;
        };
        let p = model::plan_recovered(&a, &Spec::default()).unwrap();
        assert_eq!(
            (p.sheet.size.as_str(), p.sheet.scale.as_str()),
            ("A3", "2:1")
        );
        assert!(p.sheet_candidates.iter().all(preferred_scales_descend));
    }

    #[test]
    fn tapped_posts_keep_readable_scale_and_prefer_complete_allocation() {
        for (file, maximum, size, scale, extra_sheets) in [
            ("string_post_tapped", "A4", "A4", "5:1", 1),
            ("string_post_tapped", "A3", "A3", "5:1", 0),
        ] {
            let path = Path::new(env!("CARGO_MANIFEST_DIR"))
                .join(format!("tests/fixtures/pmi/step/{file}.step.gz"));
            let a = model::analyse_native(&path).unwrap().1;
            let s: Spec =
                serde_json::from_value(json!({"sheet":{"preferred_max_size":maximum}})).unwrap();
            let p = model::plan_recovered(&a, &s).unwrap();
            assert_eq!(
                (
                    p.sheet.size.as_str(),
                    p.sheet.scale.as_str(),
                    p.sheets.len()
                ),
                (size, scale, extra_sheets),
                "{file}: {}",
                p.sheet.basis
            );
            assert!(model::findings(&p).clean(), "{}", p.sheet.basis);
            assert!(p.sheet_candidates.iter().all(preferred_scales_descend));
        }
    }

    #[test]
    fn descending_trials_keep_clean_reductions_and_reject_new_ink() {
        for (file, size, scale) in [
            // #222: its plan view, which no dimension needs, is left out, and 2:1 fits.
            ("mfcadpp/10020.step", "A4", "2:1"),
            // Its paired-ramp step's angle, recognised since quiddity main after 0.2.0-alpha.2,
            // conflicts with an annotation on A4 at 1:1; A3 at 2:1 is clean.
            ("mfcadpp/10033.step", "A3", "2:1"),
            ("mfcadpp/10163.step", "A4", "1:1"),
            // Usable on preferred A3, as on main: nothing clipped and nothing lost.
            ("cadgenbench_inputs/cgb245.step", "A3", "1:5"),
            // The close-ordinate estimate is not an actual drop: the final
            // schedule and dimensions carry all required locations on A3.
            ("nist/nist_ftc_07_asme1_rd.stp", "A3", "1:5"),
        ] {
            let Some(a) = corpus(file) else {
                return;
            };
            let p = model::plan_recovered(&a, &Spec::default()).unwrap();
            assert_eq!(
                (
                    p.sheet.size.as_str(),
                    p.sheet.scale.as_str(),
                    p.sheets.len()
                ),
                (size, scale, 0),
                "{file}: {}",
                p.sheet.basis
            );
            assert!(p.sheet_candidates.iter().all(preferred_scales_descend));
        }
    }

    #[test]
    fn fixed_pair_scope_does_not_leak_into_explicit_requests() {
        assert!(!crate::planning::is_fixed_pair());
        crate::planning::fixed_pair(|| {
            assert!(crate::planning::is_fixed_pair());
            crate::nominal::with_comparison(|| assert!(!crate::planning::is_fixed_pair()));
            assert!(crate::planning::is_fixed_pair());
            crate::planning::explicit_pair(|| {
                assert!(!crate::planning::is_fixed_pair());
                assert!(crate::planning::bounded_pair());
            });
            assert!(crate::planning::is_fixed_pair());
            let explicit_panicked = std::panic::catch_unwind(|| {
                crate::planning::explicit_pair(|| panic!("explicit trial refused"))
            });
            assert!(explicit_panicked.is_err());
            assert!(crate::planning::is_fixed_pair());
            let panicked = std::panic::catch_unwind(|| {
                crate::planning::fixed_pair(|| panic!("trial refused"))
            });
            assert!(panicked.is_err());
            assert!(crate::planning::is_fixed_pair());
        });
        assert!(!crate::planning::is_fixed_pair());
        assert!(!crate::planning::bounded_pair());
    }

    #[test]
    fn fits_a4_reuses_the_only_baseline() {
        let a = fixture();
        let p = model::plan_recovered(&a, &Spec::default()).unwrap();
        assert_eq!(p.sheet.size, "A4");
        assert_eq!(p.sheet_candidates.len(), 1);
        assert!(p.sheet_candidates[0].outcome.contains("usable at"));
    }

    fn inspection_spec(printer: &str) -> Spec {
        let mut rows = vec![json!(["INSPECTION SCHEDULE", "CHECKED"])];
        rows.extend((0..12).map(|i| json!([format!("FORM AND SIZE {}", "X".repeat(60)), i])));
        serde_json::from_value(json!({"sheet":{"preferred_max_size":printer}, "sheets":{"smallest_scale":"5:1"}, "hole_table":"never", "tables":[{"id":"t:1","rows":rows,"prefer":"tr"}]})).unwrap()
    }

    #[test]
    fn a4_printer_uses_two_a4_sheets() {
        let Some(a) = corpus("gramel/string_post.step") else {
            return;
        };
        let s = inspection_spec("A4");
        let p = model::plan_recovered(&a, &s).unwrap();
        assert_eq!(
            (p.sheet.size.as_str(), p.sheets.len()),
            ("A4", 1),
            "{}",
            p.sheet.basis
        );
        assert_eq!(p.sheet.scale, "5:1");
        assert_eq!(p.sheet_candidates.len(), 2);
        assert_eq!(p.spec.sheets.as_ref().unwrap().max, None);
        let mut forced = s.clone();
        forced.sheet.size = Some("A4".into());
        forced.sheets.as_mut().unwrap().max = Some(2);
        let q = model::plan_automatic(&a, &forced).unwrap();
        assert!(model::carries_required(&q, &p));
        assert!(sheet_fit::inside_frames(&a, &p));
        assert!(model::findings(&p).clean());
    }

    #[test]
    fn a3_printer_uses_one_a3_for_the_same_drawing() {
        let Some(a) = corpus("gramel/string_post.step") else {
            return;
        };
        let p = model::plan_recovered(&a, &inspection_spec("A3")).unwrap();
        assert_eq!(
            (p.sheet.size.as_str(), p.sheets.len()),
            ("A3", 0),
            "{}",
            p.sheet.basis
        );
        assert_eq!(p.sheet.scale, "5:1");
        assert_eq!(p.sheet_candidates.len(), 2);
    }

    #[test]
    fn needs_a3_and_stops_before_replaying_the_baseline() {
        let Some(a) = corpus("cadgenbench/threaded_connector_109.step") else {
            return;
        };
        let s: Spec = serde_json::from_value(json!({"sheet":{"preferred_max_size":"A3"}})).unwrap();
        let p = model::plan_recovered(&a, &s).unwrap();
        assert_eq!(p.sheet.size, "A3", "{}", p.sheet.basis);
        assert_eq!(p.sheet_candidates.len(), 2);
        assert!(p.sheet_candidates[1].outcome.contains("usable at"));
    }

    #[test]
    fn default_preferences_never_keep_a_clipped_large_part() {
        let a = model::analyse_native(
            &Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/pmi/step/nist_stc_09_asme1_ap242-e4.stp.gz"),
        )
        .unwrap()
        .1;
        let p = model::plan_recovered(&a, &Spec::default()).unwrap();
        assert!(sheet_fit::inside_frames(&a, &p), "{}", p.sheet.basis);
        assert!(p.sheet.scale_factor >= 0.2, "{}", p.sheet.basis);
        // The value-less `2X R` and `4X R` common labels are refused (#214).
        assert_eq!(p.gdt.len(), 26);
        let slot = p
            .dimensions
            .iter()
            .find(|d| d.id == "d:f:slot:3/slot_length.length")
            .unwrap()
            .placed
            .as_ref()
            .unwrap();
        assert!(
            slot.lines[..2]
                .iter()
                .all(|[a, b]| (a[1] - b[1]).abs() < 60.)
        );
        let exceeded = !["A4", "A3"].contains(&p.sheet.size.as_str()) || p.sheets.len() > 1;
        let notices: Vec<_> = p
            .problems
            .iter()
            .filter(|p| p.code == "sheet_preference_exceeded")
            .collect();
        assert_eq!(notices.len(), usize::from(exceeded));
        if let Some(n) = notices.first() {
            assert_eq!(n.severity, "info");
        }
        assert!(
            p.sheet_candidates
                .iter()
                .all(|c| c.preferred_max_size == "A3" && c.preferred_max_sheets == 2)
        );
        for d in &p.dimensions {
            if let crate::items::State::NotDrawable { reason_code } = &d.state {
                assert!(
                    p.problems
                        .iter()
                        .any(|q| q.code == *reason_code && q.subjects.contains(&d.id)),
                    "unreported {}",
                    d.id
                );
            }
        }
    }

    #[test]
    fn one_advisory_identifies_both_exceeded_preferences() {
        let a = fixture();
        let spec = Spec::default();
        let mut p = model::plan_automatic(&a, &spec).unwrap();
        p.sheet.size = "A2".into();
        p.sheets = vec![p.sheet.clone(), p.sheet.clone()];
        let p = finish_choice(p, &spec, Vec::new(), "test");
        let notices: Vec<_> = p
            .problems
            .iter()
            .filter(|p| p.code == "sheet_preference_exceeded")
            .collect();
        assert_eq!(notices.len(), 1);
        assert_eq!(notices[0].severity, "info");
        assert_eq!(notices[0].labels().impact, crate::finding::Impact::Advisory);
        assert!(notices[0].message.contains("paper size and sheet count"));
        assert!(notices[0].message.contains("3 sheet(s) of A2"));
        assert!(notices[0].fix.is_empty());
    }

    #[test]
    fn principal_blocks_keep_their_annotations_and_scale_on_separate_sheets() {
        let a = fixture();
        let spec: Spec = serde_json::from_value(
            json!({"sheet":{"size":"A1","scale":"5:1"},"views":{"iso":{"omit":true}}}),
        )
        .unwrap();
        let source = model::plan_automatic(&a, &spec).unwrap();
        let split = crate::sheets::separate_views(&a, &source, "A3", 9)
            .expect("individual annotated views fit A3");
        assert!(sheet_fit::inside_frames(&a, &split));
        assert!(!split.sheets.is_empty());
        assert!(split.views.iter().all(|v| v.scale == 5.));
        assert!(
            split.views.iter().any(|v| v.id == "v:front" && v.sheet > 1)
                || split.views.iter().any(|v| v.id == "v:plan" && v.sheet > 1)
        );
        assert_eq!(source.dimensions.len(), split.dimensions.len());
        for old in &source.dimensions {
            let new = split.dimensions.iter().find(|d| d.id == old.id).unwrap();
            assert_eq!(new.view, old.view);
            assert_eq!(new.state, old.state);
            assert_eq!(new.measurements, old.measurements);
            if let (Some(a), Some(b)) = (&old.placed, &new.placed) {
                let delta = crate::placement_intent::anchor_delta(&source, &split.views, &old.view)
                    .unwrap();
                for (k, shift) in delta.iter().enumerate() {
                    assert!((a.label_at[k] + shift - b.label_at[k]).abs() < 1e-9);
                }
                for (a, b) in a.lines.iter().zip(&b.lines) {
                    for j in 0..2 {
                        for k in 0..2 {
                            assert!((a[j][k] + delta[k] - b[j][k]).abs() < 1e-9);
                        }
                    }
                }
            } else {
                assert_eq!(old.placed.is_some(), new.placed.is_some());
            }
        }
    }

    #[test]
    fn clipping_rejects_a_candidate_even_without_any_reported_problems() {
        let a = fixture();
        let p = model::plan_automatic(&a, &Spec::default()).unwrap();
        let mut clipped = p.clone();
        clipped.problems.clear();
        clipped.views[0].at[0] += clipped.sheet.page[0];
        assert!(!sheet_fit::inside_frames(&a, &clipped));
        assert!(usable(&a, &clipped, &p, 0.).is_err());
        let mut clipped = p.clone();
        clipped.problems.clear();
        let dimension = clipped
            .dimensions
            .iter_mut()
            .find_map(|d| d.placed.as_mut())
            .unwrap();
        dimension.label_box[2] += clipped.sheet.page[0];
        assert!(!sheet_fit::inside_frames(&a, &clipped));
        assert!(usable(&a, &clipped, &p, 0.).is_err());
    }

    #[test]
    fn a_renderer_trimmed_detail_marker_does_not_pass_the_frame_gate() {
        let a = fixture();
        let mut p = model::plan_automatic(&a, &Spec::default()).unwrap();
        assert!(sheet_fit::inside_frames(&a, &p));
        let mut detail = p.views.iter().find(|v| v.id == "v:front").unwrap().clone();
        detail.id = "v:detail_a".into();
        detail.detail = Some(crate::detail::Detail {
            label: "A".into(),
            axis: 0,
            source_view: "v:front".into(),
            source_cut: None,
            kind: "feature".into(),
            bounds: a.envelope,
            marked_bounds: [[-1000.; 3], [1000.; 3]],
            dimensions: vec![],
            scale: 1.,
            authored: false,
            partial_profile: false,
        });
        p.views.push(detail);
        p.problems.clear();
        assert!(!sheet_fit::inside_frames(&a, &p));
    }

    #[test]
    fn route_quality_measures_runs_on_their_carrying_sheet_only() {
        let mut a = fixture();
        let mut p = model::plan_automatic(&a, &Spec::default()).unwrap();
        a.edge_ink = None;
        let mut linear = p
            .dimensions
            .iter()
            .find(|d| d.placed.is_some())
            .unwrap()
            .clone();
        linear.id = "linear".into();
        linear.view = "v:front".into();
        linear.kind = "linear".into();
        let ink = linear.placed.as_mut().unwrap();
        ink.lines = vec![
            [[20., 20.], [20., 50.]],
            [[30., 20.], [30., 50.]],
            [[20., 40.], [30., 40.]],
        ];
        ink.arrows = vec![[[20., 40.], [23., 40.]], [[30., 40.], [27., 40.]]];
        let mut callout = linear.clone();
        callout.id = "callout".into();
        callout.kind = "callout".into();
        callout.view = "v:plan".into();
        let ink = callout.placed.as_mut().unwrap();
        ink.lines = vec![[[20.1, 22.], [20.1, 35.]], [[20.1, 35.], [35., 35.]]];
        ink.arrows.clear();
        p.dimensions = vec![linear, callout];
        p.views.retain(|v| v.id == "v:front" || v.id == "v:plan");
        p.views.iter_mut().find(|v| v.id == "v:plan").unwrap().sheet = 2;
        assert!(crate::dims::sheet_route_limits(&a, &p).is_empty());
        p.views.iter_mut().find(|v| v.id == "v:plan").unwrap().sheet = 1;
        let measured = crate::dims::sheet_route_limits(&a, &p);
        assert!(measured.contains(&("leader_ink_run".into(), vec!["callout".into()])));
        p.dimensions[1].placed.as_mut().unwrap().lines =
            vec![[[20.1, 22.], [20.1, 23.]], [[20.1, 23.], [35., 23.]]];
        assert!(crate::dims::sheet_route_limits(&a, &p).is_empty());
    }

    #[test]
    fn an_inherited_feature_clearance_does_not_force_a_new_enlargement() {
        let mut a = fixture();
        let mut p = model::plan_automatic(&a, &Spec::default()).unwrap();
        p.dimensions.clear();
        p.sheet.scale_factor = 0.2;
        a.features.clear();
        a.envelope = [[0., 0., 0.], [25., 25., 25.]];
        assert!(sheet_fit::min_geometry_scale(&a) > p.sheet.scale_factor);
        assert_eq!(readable_floor(&a, &p, &Spec::default()), 0.2);
        let spec: Spec =
            serde_json::from_value(json!({"sheets":{"smallest_scale":"1:1"}})).unwrap();
        assert_eq!(readable_floor(&a, &p, &spec), 1.);
    }

    #[test]
    fn a_larger_readable_reference_is_not_newly_reduced_to_the_absolute_floor() {
        let a = fixture();
        let mut p = model::plan_automatic(&a, &Spec::default()).unwrap();
        p.dimensions.clear();
        p.sheet.scale_factor = 0.5;
        assert_eq!(candidate_floor(&a, &p, sheet_fit::ABSOLUTE_FLOOR), 0.2);
        p.sheet.scale_factor = sheet_fit::ABSOLUTE_FLOOR;
        assert_eq!(
            candidate_floor(&a, &p, sheet_fit::ABSOLUTE_FLOOR),
            sheet_fit::ABSOLUTE_FLOOR
        );
    }

    #[test]
    fn requests_follow_both_soft_preferences_then_the_hard_limit() {
        assert_eq!(
            requests(&Spec::default()),
            vec![
                ("A4", 1),
                ("A3", 1),
                ("A3", 2),
                ("A2", 1),
                ("A2", 2),
                ("A1", 1),
                ("A1", 2),
                ("A0", 1),
                ("A0", 2),
                ("A0", 3),
                ("A0", 4),
                ("A0", 5),
                ("A0", 6),
                ("A0", 7),
                ("A0", 8),
                ("A0", 9)
            ]
        );
        let spec: Spec = serde_json::from_value(json!({"sheet":{"preferred_max_size":"A4","preferred_max_sheets":3},"sheets":{"max":2}})).unwrap();
        assert_eq!(
            requests(&spec),
            vec![
                ("A4", 1),
                ("A4", 2),
                ("A3", 1),
                ("A3", 2),
                ("A2", 1),
                ("A2", 2),
                ("A1", 1),
                ("A1", 2),
                ("A0", 1),
                ("A0", 2)
            ]
        );
        let spec: Spec = serde_json::from_value(json!({"sheet":{"preferred_max_size":"A2","preferred_max_sheets":1},"sheets":{"max":3}})).unwrap();
        assert_eq!(
            requests(&spec),
            vec![
                ("A4", 1),
                ("A3", 1),
                ("A2", 1),
                ("A1", 1),
                ("A0", 1),
                ("A0", 2),
                ("A0", 3)
            ]
        );
    }

    #[test]
    fn unreleased_cap_is_unknown_and_preferred_count_must_be_positive() {
        assert!(
            serde_json::from_value::<Spec>(json!({"sheet":{"max_size":"A3"}}))
                .unwrap_err()
                .to_string()
                .contains("unknown field")
        );
        assert!(
            serde_json::from_value::<Spec>(json!({"sheet":{"preferred_max_sheets":0}})).is_err()
        );
        assert!(
            serde_json::from_value::<Spec>(json!({"sheet":{"preferred_max_sheets":-1}})).is_err()
        );
        assert!(
            serde_json::from_value::<Spec>(json!({"sheet":{"preferred_max_sheets":100}})).is_ok()
        );
    }

    #[test]
    fn removed_printer_is_refused_with_migration_guidance() {
        for value in ["A4", "A3", "A2"] {
            let error =
                serde_json::from_value::<Spec>(json!({"sheet":{"preferred_printer":value}}))
                    .unwrap_err()
                    .to_string();
            for message in [
                "unknown field",
                "preferred_printer",
                "sheet/preferred_max_size",
                "A4 printer",
                "A3 printer",
            ] {
                assert!(error.contains(message), "{error}");
            }
        }
        let error = model::edit(
            &Spec::default(),
            &json!([{"op":"set", "path":"sheet/preferred_printer", "value":"A4"}]),
        )
        .unwrap_err();
        assert!(error.message.contains("sheet/preferred_max_size"));
    }

    fn assert_passthrough(value: serde_json::Value) {
        let a = fixture();
        let s: Spec = serde_json::from_value(value).unwrap();
        let mut b = model::plan_automatic(&a, &s).unwrap();
        // Public planning attaches sheet furniture and retained-layout constraints after paper
        // selection.
        // Compare the complete plans after the same offer finalization, preserving
        // the passthrough check for every drawing field and all remedy metadata.
        crate::sheets::furnish(&mut b);
        crate::fixes::retain_local_layout(&mut b);
        crate::dims::callout_routes::prefer_interior(&a, &s, &mut b, None);
        crate::dims::callout_routes::finish(&a, &s, &mut b);
        crate::dims::contacts::report(&a, &mut b);
        model::note_final_conflicts(&mut b);
        b.record_callout_offsets();
        let p = model::plan_recovered(&a, &s).unwrap();
        assert!(p.sheet_candidates.is_empty());
        assert_eq!(
            serde_json::to_value(p).unwrap(),
            serde_json::to_value(b).unwrap()
        );
    }

    #[test]
    fn explicit_size_keeps_the_original_path() {
        assert_passthrough(json!({"sheet":{"size":"A2"}}));
    }

    #[test]
    fn explicit_scale_is_passed_through() {
        assert_passthrough(json!({"sheet":{"scale":"1:2"}}));
    }

    #[test]
    fn kept_layout_wins_over_both_automatic_preferences_and_their_edits() {
        let a = fixture();
        let spec: Spec = serde_json::from_value(json!({
            "sheet":{"size":"A2","scale":"1:1","keep_layout":true},
            "sheets":{"max":1}
        }))
        .unwrap();
        for spec in [
            spec.clone(),
            model::edit(
                &spec,
                &json!([
                    {"op":"set","path":"sheet/preferred_max_size","value":"A4"},
                    {"op":"set","path":"sheet/preferred_max_sheets","value":1}
                ]),
            )
            .unwrap(),
        ] {
            assert_eq!(spec.sheet.keep_layout, Some(true));
            let plan = model::plan_recovered(&a, &spec).unwrap();
            assert_eq!(
                (plan.sheet.size.as_str(), plan.sheet.scale.as_str()),
                ("A2", "1:1")
            );
            assert!(plan.sheets.is_empty());
            assert!(plan.sheet_candidates.is_empty());
            assert!(
                !plan
                    .problems
                    .iter()
                    .any(|p| p.code == "sheet_preference_exceeded")
            );
        }
    }

    #[test]
    fn explicit_sheet_limit_stays_hard_with_soft_paper_preferences() {
        for max in [1, 2] {
            let spec: Spec = serde_json::from_value(
                json!({"sheet":{"preferred_max_size":"A4"}, "sheets":{"max":max}}),
            )
            .unwrap();
            let p = model::plan_recovered(&fixture(), &spec).unwrap();
            assert_eq!(p.sheet.size, "A4");
            assert!(p.sheets.len() < max as usize);
            assert!(!p.sheet_candidates.is_empty());
        }
    }

    #[test]
    fn retained_candidates_respect_a_lowered_sheet_limit() {
        let Some(a) = corpus("gramel/string_post.step") else {
            return;
        };
        let mut spec = inspection_spec("A4");
        spec.sheets.as_mut().unwrap().max = Some(3);
        crate::planning::with_retained(&a, || model::plan_recovered(&a, &spec)).unwrap();
        for max in [2, 1] {
            spec.sheets.as_mut().unwrap().max = Some(max);
            let warm =
                crate::planning::with_retained(&a, || model::plan_recovered(&a, &spec)).unwrap();
            let cold = crate::planning::isolated(&a, || model::plan_recovered(&a, &spec)).unwrap();
            assert!(warm.sheets.len() < max as usize);
            assert_eq!(
                serde_json::to_value(warm).unwrap(),
                serde_json::to_value(cold).unwrap()
            );
        }
    }

    #[test]
    fn largest_live_scale_keeps_the_actual_base_choice_in_its_basis() {
        let Some(a) = corpus("mfcadpp/10146.step") else {
            return;
        };
        let p = model::plan_recovered(&a, &Spec::default()).unwrap();
        assert_eq!(
            (
                p.sheet.size.as_str(),
                p.sheet.scale.as_str(),
                p.sheets.len()
            ),
            ("A4", "2:1", 0)
        );
        assert!(
            p.sheet
                .basis
                .contains("base choice before scale maximisation A4 at 1:1")
        );
        assert_eq!(p.sheet_candidates.len(), 1);
        assert!(p.sheet_candidates[0].outcome.contains("usable at"));
        assert!(
            !p.problems
                .iter()
                .any(|p| p.code == "label_section_furniture_overlap")
        );
        let mut audited = p.clone();
        crate::dims::contacts::report(&a, &mut audited);
        assert!(
            !audited
                .problems
                .iter()
                .any(|p| p.code == "label_section_furniture_overlap"),
            "the moved section arrow must be physically clear"
        );
    }

    #[test]
    fn validates_preferred_max_size_in_documents_and_typed_specs() {
        assert!(
            serde_json::from_value::<Spec>(json!({"sheet":{"preferred_max_size":"A5"}})).is_err()
        );
        let mut s = Spec::default();
        s.sheet.preferred_max_size = Some("A5".into());
        assert!(
            model::plan_recovered(&fixture(), &s)
                .unwrap_err()
                .message
                .contains("preferred_max_size")
        );
    }

    #[test]
    fn enlarged_nested_pack_has_no_drawn_leader_centreline_warning() {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/assembly/pack-nested.step.gz");
        let (part, analysis) = model::analyse_native(&path).unwrap();
        let plan = model::plan_recovered(&analysis, &Spec::default()).unwrap();
        assert_eq!(
            (plan.sheet.size.as_str(), plan.sheet.scale.as_str()),
            ("A4", "2:1")
        );
        let font = crate::text::Font::new(crate::api::MONO.to_vec()).unwrap();
        let drawing = model::render(&part, &analysis, &plan, &font).unwrap();
        assert!(
            !crate::lint::structural::check(&drawing)
                .iter()
                .any(|p| p.code == "leader_centerline_overlap")
        );
    }

    #[test]
    fn isolated_extension_exception_does_not_excuse_other_new_warnings() {
        let a = fixture();
        let reference = model::plan_recovered(&a, &Spec::default()).unwrap();
        for code in [
            "leader_through_feature",
            "extension_through_feature",
            "label_section_furniture_overlap",
        ] {
            let mut candidate = reference.clone();
            candidate.problems.push(model::Problem {
                code: code.into(),
                severity: "warning".into(),
                subjects: vec!["new:annotation".into(), "new:feature".into()],
                ..Default::default()
            });
            let refused =
                usable_with_base_contacts(&a, &candidate, &reference, 0.1, Some(&candidate), true)
                    .unwrap_err();
            assert!(refused.contains(code), "{refused}");
        }
    }

    #[test]
    fn retaining_the_base_does_not_add_or_duplicate_recovery_findings() {
        let a = fixture();
        let baseline = model::plan_recovered(&a, &Spec::default()).unwrap();
        for existing in [false, true] {
            let mut reference = baseline.clone();
            if existing {
                reference.problems.push(model::Problem {
                    id: "p:old-recovery".into(),
                    code: "layout_recovery_exhausted".into(),
                    severity: "warning".into(),
                    message: "An existing diagnosis must be preserved exactly.".into(),
                    subjects: vec!["sheet".into()],
                    ..Default::default()
                });
            }
            let expected = reference.problems.clone();
            let selected = retained_base(
                &a,
                baseline.clone(),
                reference,
                Vec::new(),
                Vec::new(),
                vec!["larger preferred scale refused".into()],
            )
            .unwrap();
            assert_eq!(selected.problems, expected);
            assert_eq!(
                (selected.sheet.size, selected.sheet.scale),
                (baseline.sheet.size.clone(), baseline.sheet.scale.clone())
            );
        }
    }

    #[test]
    fn tolerance_labels_that_add_warnings_use_full_selection() {
        let a = fixture();
        let baseline = model::plan_recovered(&a, &Spec::default()).unwrap();
        let mut spec = Spec::default();
        for d in baseline
            .dimensions
            .iter()
            .filter(|d| d.kind == "linear" && d.placed.is_some())
        {
            spec.dimensions.entry(d.id.clone()).or_default().tolerance =
                Some("±0.123456789123456789".into());
        }
        let retained = crate::planning::with(&a, || {
            let plan = replay_pins(&a, &spec, &Spec::default(), baseline.clone()).unwrap();
            let mut plan = model::finish_presentation(&a, &spec, plan).unwrap();
            model::finish_selected(&a, &mut plan);
            plan
        });
        let warnings = new_tolerance_warnings(&a, &retained, &baseline);
        assert!(
            !warnings.is_empty(),
            "the enlarged labels must fail the retained frame"
        );
        let selected = model::plan_recovered(&a, &spec).unwrap();
        assert!(
            selected
                .sheet
                .basis
                .contains("full paper/scale selection used"),
            "{}",
            selected.sheet.basis
        );
        assert_eq!(warnings, BTreeSet::from(["page_fit_uncertain".into()]));
        assert_eq!(
            (retained.sheet.size.as_str(), retained.sheet.scale.as_str()),
            ("A4", "2:1")
        );
        assert_eq!(
            (selected.sheet.size.as_str(), selected.sheet.scale.as_str()),
            ("A4", "1:1")
        );
        assert!(model::finished_items_fit(&a, &selected));
        assert!(new_tolerance_warnings(&a, &selected, &baseline).is_empty());
        assert!(model::carries_required(&baseline, &selected));
        assert_eq!(selected.spec, spec);
    }

    #[test]
    fn tolerance_only_edits_retain_the_canonical_paper_and_scale() {
        let a = fixture();
        crate::planning::with_retained(&a, || {
            let baseline = model::plan_recovered(&a, &Spec::default()).unwrap();
            let id = baseline
                .dimensions
                .iter()
                .find(|d| d.kind == "linear" && d.placed.is_some())
                .unwrap()
                .id
                .clone();
            let mut spec = Spec::default();
            spec.dimensions.entry(id.clone()).or_default().tolerance = Some("±0.1".into());
            let edited = model::plan_recovered(&a, &spec).unwrap();
            assert_eq!(
                (
                    edited.sheet.size.as_str(),
                    edited.sheet.scale.as_str(),
                    edited.sheets.len()
                ),
                (
                    baseline.sheet.size.as_str(),
                    baseline.sheet.scale.as_str(),
                    baseline.sheets.len()
                )
            );
            assert!(
                edited
                    .sheet
                    .basis
                    .contains("retained annotation reference:")
            );
            assert!(
                !edited
                    .sheet
                    .basis
                    .contains("full paper/scale selection used")
            );
            assert!(new_tolerance_warnings(&a, &edited, &baseline).is_empty());
            assert_eq!(edited.spec, spec);
            assert!(
                edited
                    .dimensions
                    .iter()
                    .find(|d| d.id == id)
                    .unwrap()
                    .label
                    .contains("±0.1")
            );
            assert!(model::carries_required(&baseline, &edited));
        });
    }

    #[test]
    fn only_new_code_and_subject_pairs_fail() {
        let a = fixture();
        let mut p = model::plan_automatic(&a, &Spec::default()).unwrap();
        p.problems = vec![model::Problem {
            code: "annotation_overlap".into(),
            subjects: vec!["b".into(), "a".into()],
            ..Default::default()
        }];
        let b = failures(&a, &p, 0.1);
        p.problems[0].subjects.reverse();
        assert!(failures(&a, &p, 0.1).difference(&b).next().is_none());
        p.problems[0].subjects.push("c".into());
        assert_eq!(failures(&a, &p, 0.1).difference(&b).count(), 1);
        p.problems[0].code = "features_not_recognised".into();
        assert!(failures(&a, &p, 0.1).is_empty());
        p.problems[0].code = "layout_recovery_exhausted".into();
        p.problems[0].severity = "info".into();
        assert!(failures(&a, &p, 0.1).is_empty());
        p.problems[0].severity = "warning".into();
        assert!(!failures(&a, &p, 0.1).is_empty());

        assert!(
            failures(&a, &p, p.sheet.scale_factor * 2.0)
                .iter()
                .any(|(code, _)| code == "legibility_floor_breached")
        );
    }
    #[test]
    fn view_strokes_must_clear_the_frame() {
        let a = fixture();
        let mut p = model::plan_automatic(&a, &Spec::default()).unwrap();
        let original = failures(&a, &p, 0.1);
        let first = sheet_fit::footprints(&a, &p)[0];
        p.views[0].at[0] += layout::margin() - first[0];
        let changed = failures(&a, &p, 0.1);
        assert!(
            changed
                .difference(&original)
                .any(|(code, subjects)| code == "annotation_off_sheet"
                    && subjects == std::slice::from_ref(&p.views[0].id))
        );
    }
}
