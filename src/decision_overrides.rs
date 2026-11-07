//! The approved #309 inventory. Only existing Spec fields belong in this table.
//! Keep reporting outside the layout solve: bases must not change automatic geometry.
use crate::model::{Invalid, Plan, Problem};
use std::cell::Cell;

#[derive(Clone, Copy)]
enum Family {
    Part,
    Views,
    Sheet,
    Dimensions,
    Attachment,
    Wording,
    Furniture,
    Linework,
    Requirements,
}

// Decision family -> fields that change it -> current exceptions. Missing capabilities
// are reported honestly, not advertised as fields that the Spec cannot yet accept.
const OVERRIDES: [(&str, &str, &str); 9] = [
    (
        "part and orientation",
        "part",
        "cannot override the Analysis frame in Spec yet; orientation requires reanalysis (#309)",
    ),
    (
        "view selection",
        "views/<id> keys [omit, half_section, detail]",
        "cannot override section-through geometry, camera direction/up, half-section side or view breaks yet (#304, #314, #315, #309)",
    ),
    (
        "paper and arrangement",
        "sheet keys [size, scale, preferred_max_size, preferred_max_sheets, view_gutter, dimension_spacing, projection]; sheets; views/<id> keys [sheet, place]; iso_scale_factor",
        "Captions state actual scales; caption placement unsupported (#309)",
    ),
    (
        "dimension selection and origins",
        "dimension_source; dimensions/<id> keys [omit, between, along, view]; origin; dimension_schemes; faces/<face>/profile_corners",
        "PMI remains required; some redundant dimensions lack force-show (#309)",
    ),
    (
        "recognition, grouping and attachment",
        "feature_declarations; features/<feature> keys [reclassify, suppress, split, pattern]; dimensions/<id>/place; annotations/<id>/place",
        "Named support overrides are not implemented (#309)",
    ),
    (
        "wording and precision",
        "precision; dimensions/<id> keys [text, style, presentation]; features/<feature>/callouts/style; sheet/callouts/style",
        "Keep measured values; wrap width not implemented (#309)",
    ),
    (
        "tables, tags, notes and furniture",
        "hole_table; sheet keys [table_columns, title]; notes; tables",
        "cannot override automatic tag order or automatic block placement yet (#309)",
    ),
    (
        "linework, symbols and inspection rings",
        "sheet keys [hidden, centre_marks, centre_lines, uncited_datums, balloons]; views/<id> keys [hidden, centre_marks, centre_lines, thread_depiction, knurl_depiction]; features/<feature> keys [modelled_thread, spring]; annotations/<id>/omit; balloons",
        "Pens/fonts/hatching unsupported; sections omit hidden ink (#309)",
    ),
    (
        "requirements and checker conclusions",
        "",
        "cannot override source requirements or independent checker conclusions through presentation: PMI owns requirements; lint judges the resulting drawing",
    ),
];

fn add(basis: &mut String, family: Family, id: &str) {
    let (_, fields, exception) = OVERRIDES[family as usize];
    let guidance = format!(
        "; Spec: {}; {exception}",
        fields.replace("<id>", &crate::items::key(id))
    );
    if !basis.contains(&guidance) {
        // Preserve semantic suffixes: consumers read dependencies through the end,
        // and routing may add/remove the interior proof after a public plan is made.
        let at = ["; dependencies: ", crate::dims::interior::BASIS]
            .iter()
            .filter_map(|suffix| basis.find(suffix))
            .min()
            .unwrap_or(basis.len());
        basis.insert_str(at, &guidance);
    }
}

thread_local! { static DEPTH: Cell<usize> = const { Cell::new(0) }; }
struct Boundary;
impl Drop for Boundary {
    fn drop(&mut self) {
        DEPTH.set(DEPTH.get() - 1);
    }
}
/// Nested canonical and recovery plans keep their original bases during the solve.
pub(crate) fn boundary(run: impl FnOnce() -> Result<Plan, Invalid>) -> Result<Plan, Invalid> {
    let outer = DEPTH.get() == 0;
    DEPTH.set(DEPTH.get() + 1);
    let _guard = Boundary;
    let mut plan = run()?;
    if outer {
        report(&mut plan);
    }
    Ok(plan)
}

fn report(plan: &mut Plan) {
    add(&mut plan.sheet.basis, Family::Part, "");
    if let Some(part) = &plan.spec.part
        && !plan.sheet.basis.contains("; applied part:")
    {
        plan.sheet.basis += &format!(
            "; applied part: {part:?}; selected geometry is part {:?}",
            plan.binding.part_index
        );
    }
    for sheet in std::iter::once(&mut plan.sheet).chain(&mut plan.sheets) {
        add(&mut sheet.basis, Family::Sheet, "<view>");
        add(&mut sheet.basis, Family::Furniture, "");
    }
    for v in &mut plan.views {
        let id = v.id.trim_start_matches("v:");
        add(&mut v.basis, Family::Views, id);
        add(&mut v.basis, Family::Linework, id);
    }
    for d in &mut plan.dimensions {
        add(&mut d.basis, Family::Dimensions, &d.id);
        add(&mut d.basis, Family::Wording, &d.id);
        add(&mut d.basis, Family::Attachment, &d.id);
    }
    for d in &mut plan.omitted {
        add(&mut d.basis, Family::Dimensions, &d.id);
    }
    for c in &mut plan.recognition_corrections {
        add(&mut c.basis, Family::Attachment, &c.id);
    }
    for g in &mut plan.gdt {
        add(&mut g.basis, Family::Attachment, &g.id);
        add(&mut g.basis, Family::Requirements, &g.id);
    }
    for r in &mut plan.requirements {
        add(&mut r.basis, Family::Requirements, &r.id);
    }
    for n in &mut plan.notes {
        add(&mut n.basis, Family::Furniture, &n.id);
    }
    for t in &mut plan.tables {
        add(&mut t.basis, Family::Furniture, &t.id);
    }
    if let Some(t) = &mut plan.radius_table {
        add(&mut t.table.basis, Family::Furniture, &t.table.id);
    }
    for t in plan.hole_table.iter_mut().chain(&mut plan.hole_tables) {
        add(&mut t.basis, Family::Furniture, &t.id);
        for block in &mut t.blocks {
            add(&mut block.basis, Family::Furniture, &block.id);
        }
    }
    for ring in &mut plan.inspection_balloons {
        add(&mut ring.basis, Family::Linework, "<view>");
        add(&mut ring.basis, Family::Requirements, &ring.id);
    }
    for c in &mut plan.captions {
        add(&mut c.basis, Family::Sheet, "iso");
    }
    for (id, entry) in &plan.spec.dimensions {
        if entry.presentation.is_some()
            && !plan.dimensions.iter().any(|d| {
                &d.id == id && d.placed.is_some() && d.from_spec.iter().any(|f| f == "presentation")
            })
        {
            let problem_id = format!("p:dimension_edit_unapplied:presentation/{id}");
            if plan.problems.iter().any(|p| p.id == problem_id) {
                continue;
            }
            plan.problems.push(Problem {
                id: problem_id, code: "dimension_edit_unapplied".into(), severity: "warning".into(), subjects: vec![id.clone()],
                message: format!("dimensions/{}/presentation was not applied: it needs a separately drawn cylindrical-mouth pocket depth callout; the request is retained.", crate::items::key(id)),
                ..Default::default()
            });
            plan.status = crate::completeness::status(&plan.problems);
        }
    }
}
