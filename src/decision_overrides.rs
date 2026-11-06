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
        "views/<id>/omit, views/<id>/half_section, views/<id>/detail",
        "cannot override section-through geometry, camera direction/up, half-section side or view breaks yet (#304, #314, #315, #309)",
    ),
    (
        "paper and arrangement",
        "sheet/size, sheet/scale, sheets, views/<id>/sheet, views/<id>/place, sheet/preferred_max_size, sheet/preferred_max_sheets, sheet/view_gutter, sheet/dimension_spacing, sheet/projection, iso_scale_factor",
        "scale captions must state actual scales; caption placement is not yet Spec-addressable (#309)",
    ),
    (
        "dimension selection and origins",
        "dimension_source, dimensions/<id>/omit, dimensions/<id>/between, dimensions/<id>/along, dimensions/<id>/view, origin, dimension_schemes, faces/<face>/profile_corners",
        "independent PMI requirements remain required; redundant generated dimensions are not all force-showable yet (#309)",
    ),
    (
        "recognition, grouping and attachment",
        "feature_declarations, features/<feature>/reclassify, features/<feature>/suppress, features/<feature>/split, features/<feature>/pattern, dimensions/<id>/place, annotations/<id>/place",
        "cannot override a leader's geometric support by name yet; attachment must lie on proven geometry (#309)",
    ),
    (
        "wording and precision",
        "precision, dimensions/<id>/text, dimensions/<id>/style, dimensions/<id>/presentation, features/<feature>/callouts/style, sheet/callouts/style",
        "measured values must remain truthful; arbitrary wrap width is not yet Spec-addressable (#309)",
    ),
    (
        "tables, tags, notes and furniture",
        "hole_table, sheet/table_columns, notes, tables, sheet/title",
        "cannot override automatic tag order or automatic block placement yet (#309)",
    ),
    (
        "linework, symbols and inspection rings",
        "sheet/hidden, sheet/centre_marks, sheet/centre_lines, views/<id>/hidden, views/<id>/centre_marks, views/<id>/centre_lines, views/<id>/thread_depiction, views/<id>/knurl_depiction, features/<feature>/modelled_thread, features/<feature>/spring, sheet/uncited_datums, annotations/<id>/omit, sheet/balloons, balloons",
        "cannot override pens, fonts or hatching yet; sections suppress hidden ink (#309)",
    ),
    (
        "requirements and checker conclusions",
        "",
        "cannot override source requirements or independent checker conclusions through presentation: PMI owns requirements; lint judges the resulting drawing",
    ),
];

fn add(basis: &mut String, family: Family, id: &str) {
    let (name, fields, exception) = OVERRIDES[family as usize];
    let marker = format!("; overrides ({name}): ");
    if !basis.contains(&marker) {
        let guidance = format!(
            "{marker}{}; {exception}",
            fields.replace("<id>", &crate::items::key(id))
        );
        // Existing consumers read the dependency list through the end of the basis.
        let at = basis.find("; dependencies: ").unwrap_or(basis.len());
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
    for t in plan.hole_table.iter_mut().chain(&mut plan.hole_tables) {
        add(&mut t.basis, Family::Furniture, &t.id);
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
