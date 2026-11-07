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
        "Frame changes require reanalysis (#309)",
    ),
    (
        "view selection",
        "views/sections; views/<view> keys [omit, half_section, detail, break]",
        "Named and explicit sections use front-facing planes; cameras and half-section side unavailable (#309)",
    ),
    (
        "paper and arrangement",
        "sheet keys [size, scale, preferred_max_size, preferred_max_sheets, view_gutter, dimension_spacing, projection]; sheets; views/<view> keys [sheet, place]; iso_scale_factor",
        "Captions state actual scale; caption placement unavailable (#309)",
    ),
    (
        "dimension selection, origins and local presentation",
        "dimensions/<dimension> keys [omit, between, along, view, place, text, style, presentation]; dimension_source; origin; dimension_schemes; precision; faces/<face>/profile_corners",
        "Keep measured values; force-show, named supports and wrap width unavailable (#309)",
    ),
    (
        "recognition, grouping and attachment",
        "feature_declarations; features/<feature> keys [reclassify, suppress, split, pattern]; annotations/<annotation>/place",
        "Named support overrides unavailable (#309)",
    ),
    (
        "wording and precision",
        "precision; features/<feature>/callouts/style; sheet/callouts/style; annotations/<annotation>/text",
        "Keep measured values; wrap width unavailable (#309)",
    ),
    (
        "tables, tags, notes and furniture",
        "hole_table; sheet keys [table_columns, title]; notes; tables; annotations/<annotation> keys [omit, text, place]",
        "Automatic tag order and block placement unavailable (#309)",
    ),
    (
        "linework, symbols and inspection rings",
        "sheet keys [hidden, centre_marks, centre_lines, uncited_datums, balloons]; views/<view> keys [hidden, centre_marks, centre_lines, thread_depiction, knurl_depiction]; features/<feature> keys [modelled_thread, spring]; annotations/<annotation>/omit; balloons",
        "Pens/fonts/hatching unavailable; sections omit hidden ink (#309)",
    ),
    (
        "requirements and checker conclusions",
        "",
        "No presentation override: PMI owns requirements; lint independently checks the drawing",
    ),
];

#[derive(Clone, Copy)]
enum Target<'a> {
    Document,
    View(&'a str),
    Dimension(&'a str),
    Feature(&'a str),
    Annotation(&'a str),
}

fn add(basis: &mut String, family: Family, target: Target<'_>) {
    let (_, fields, exception) = OVERRIDES[family as usize];
    let fields = fields
        .split("; ")
        .filter_map(|field| {
            let mut field = field.to_string();
            for (placeholder, value) in [
                (
                    "<view>",
                    match target {
                        Target::View(id) => Some(id),
                        _ if matches!(family, Family::Sheet) => Some("<view>"),
                        _ => None,
                    },
                ),
                (
                    "<dimension>",
                    match target {
                        Target::Dimension(id) => Some(id),
                        _ => None,
                    },
                ),
                (
                    "<annotation>",
                    match target {
                        Target::Annotation(id) => Some(id),
                        _ => None,
                    },
                ),
                (
                    "<feature>",
                    Some(match target {
                        Target::Feature(id) => id,
                        _ => "<feature>",
                    }),
                ),
            ] {
                if field.contains(placeholder) {
                    field = field.replace(placeholder, &crate::items::key(value?));
                }
            }
            (!field.is_empty()).then_some(field)
        })
        .collect::<Vec<_>>()
        .join("; ");
    let guidance = if fields.is_empty() {
        format!("; {exception}")
    } else {
        format!("; Spec: {fields}; {exception}")
    };
    if !basis.contains(&guidance) {
        // Preserve dependency tails and routing proofs across late recovery.
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
        crate::model::refresh_line_work_problems(&mut plan);
        report(&mut plan);
    }
    Ok(plan)
}

fn report(plan: &mut Plan) {
    crate::broken::check_anchors(plan);
    add(&mut plan.sheet.basis, Family::Part, Target::Document);
    if let Some(part) = &plan.spec.part
        && !plan.sheet.basis.contains("; applied part:")
    {
        plan.sheet.basis += &format!(
            "; applied part: {part:?}; selected geometry is part {:?}",
            plan.binding.part_index
        );
    }
    for sheet in std::iter::once(&mut plan.sheet).chain(&mut plan.sheets) {
        add(&mut sheet.basis, Family::Sheet, Target::Document);
        add(&mut sheet.basis, Family::Furniture, Target::Document);
        add(&mut sheet.basis, Family::Wording, Target::Document);
        add(&mut sheet.basis, Family::Attachment, Target::Document);
    }
    for v in &mut plan.views {
        let id = v.id.trim_start_matches("v:");
        add(&mut v.basis, Family::Views, Target::View(id));
        add(&mut v.basis, Family::Linework, Target::View(id));
        if let Some(b) = &v.broken
            && !v.basis.contains(&format!("; applied {}:", b.source))
        {
            v.basis += &format!(
                "; applied {}: uniform projected interval {:?}, retained gap {} paper mm; displaced full-length depiction; axis/remove/gap/aligned override automatic break choices",
                b.source, b.remove, b.paper_gap
            );
        }
    }
    for d in &mut plan.dimensions {
        add(&mut d.basis, Family::Dimensions, Target::Dimension(&d.id));
    }
    for d in &mut plan.omitted {
        add(&mut d.basis, Family::Dimensions, Target::Dimension(&d.id));
    }
    for c in &mut plan.recognition_corrections {
        add(&mut c.basis, Family::Attachment, Target::Feature(&c.id));
    }
    for g in &mut plan.gdt {
        let target = if g.kind == "label" {
            Target::Annotation(&g.id)
        } else {
            Target::Document
        };
        add(&mut g.basis, Family::Attachment, target);
        if matches!(target, Target::Annotation(_)) {
            add(&mut g.basis, Family::Wording, target);
            add(&mut g.basis, Family::Linework, target);
        }
        add(&mut g.basis, Family::Requirements, Target::Document);
    }
    for r in &mut plan.requirements {
        add(&mut r.basis, Family::Requirements, Target::Document);
    }
    for n in &mut plan.notes {
        let target = if n.default {
            Target::Annotation(&n.id)
        } else {
            Target::Document
        };
        add(&mut n.basis, Family::Furniture, target);
    }
    for t in &mut plan.tables {
        add(&mut t.basis, Family::Furniture, Target::Document);
    }
    if let Some(t) = &mut plan.radius_table {
        add(&mut t.table.basis, Family::Furniture, Target::Document);
    }
    for t in plan.hole_table.iter_mut().chain(&mut plan.hole_tables) {
        add(&mut t.basis, Family::Furniture, Target::Document);
        for block in &mut t.blocks {
            add(&mut block.basis, Family::Furniture, Target::Document);
        }
    }
    for ring in &mut plan.inspection_balloons {
        add(&mut ring.basis, Family::Linework, Target::Document);
        add(&mut ring.basis, Family::Requirements, Target::Document);
    }
    for c in &mut plan.captions {
        add(&mut c.basis, Family::Sheet, Target::View("iso"));
    }
    for (name, entry) in &plan.spec.views {
        if entry.broken.is_some()
            && !plan
                .views
                .iter()
                .any(|v| v.id == format!("v:{name}") && v.broken.is_some())
        {
            crate::broken::not_applied(
                &mut plan.problems,
                name,
                &format!("views/{name}/break"),
                "the carrying view is omitted or displaced",
            );
        }
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

#[cfg(all(test, not(target_arch = "wasm32")))]
mod tests {
    #[test]
    fn final_ink_recheck_keeps_real_overflow_and_unrelated_findings() {
        use crate::{model, output::Curve};
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/parts/prismatic.step");
        let (_, a) = model::analyse(&path).unwrap();
        let mut p = model::plan(&a, &model::Spec::default()).unwrap();
        for id in ["ink:stale", "d:other"] {
            p.problems.push(model::Problem {
                id: format!("p:annotation_off_sheet:{id}"),
                code: "annotation_off_sheet".into(),
                severity: "error".into(),
                ..Default::default()
            });
        }
        p.views[0].line_work.push(crate::centrelines::LineWork {
            id: "ink:actual".into(),
            kind: "cross".into(),
            curves: vec![Curve::Line([-1., 20.], [20., 20.])],
            centre: None,
            radial: false,
            global_axis: false,
            overrun: 0.,
        });
        model::refresh_line_work_problems(&mut p);
        assert!(!p.problems.iter().any(|q| q.id.ends_with("ink:stale")));
        assert!(p.problems.iter().any(|q| q.id.ends_with("ink:actual")));
        assert!(p.problems.iter().any(|q| q.id.ends_with("d:other")));
        p.views[0].line_work.last_mut().unwrap().curves = vec![Curve::Line([20., 20.], [30., 20.])];
        model::refresh_line_work_problems(&mut p);
        assert!(!p.problems.iter().any(|q| q.id.ends_with("ink:actual")));
        assert!(p.problems.iter().any(|q| q.id.ends_with("d:other")));
        p.spec.sheet.frame_gutter = Some(15.);
        model::refresh_line_work_problems(&mut p);
        assert!(
            p.problems.iter().any(|q| q.id.ends_with("ink:actual")),
            "20 mm is outside the requested 25 mm drawable inset"
        );
    }
}
