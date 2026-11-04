//! Remove each printed geometric dimension without replanning, and retain lint/witness evidence.
use draftwright::{api, lint, model, text};
use serde_json::json;
use std::{fs, path::Path};

fn drawings(
    part: &draftwright::haecceity::Part,
    a: &model::Analysis,
    plan: &model::Plan,
    font: &text::Font,
    projections: &mut model::Projections,
) -> Vec<draftwright::output::Drawing> {
    let mut drawings = vec![model::render_with(part, a, plan, font, projections).unwrap()];
    for sheet in &plan.sheets {
        let mut page = plan.clone();
        page.sheet = sheet.clone();
        drawings.push(model::render_with(part, a, &page, font, projections).unwrap());
    }
    drawings
}

fn main() {
    let args: Vec<_> = std::env::args().collect();
    let corpus = Path::new(&args[1]);
    let plans = Path::new(&args[2]);
    let out = Path::new(&args[3]);
    fs::create_dir_all(out).unwrap();
    let font = text::Font::new(api::MONO.to_vec()).unwrap();
    for rel in include_str!("../tools/quality_parts.txt").lines() {
        let path = out.join(format!(
            "{}.json",
            Path::new(rel).file_stem().unwrap().to_str().unwrap()
        ));
        let (part, a) = model::analyse_native(&corpus.join(rel)).unwrap();
        let plan: model::Plan =
            serde_json::from_slice(&fs::read(plans.join(format!("{rel}.plan.json"))).unwrap())
                .unwrap();
        assert_eq!(plan.binding.source_sha256, a.binding.source_sha256);
        let mut projections = model::Projections::default();
        let base = drawings(&part, &a, &plan, &font, &mut projections);
        let original: Vec<_> = base.iter().flat_map(|d| lint::lint(&a, d)).collect();
        let mut rows = Vec::new();
        for removed in plan.dimensions.iter().filter(|d| {
            !d.reference
                && d.placed.is_some()
                && d.measurements.iter().any(|m| {
                    m.parameter.contains("length")
                        || m.parameter.contains("location")
                        || m.parameter.contains("angle")
                })
        }) {
            let mut mutation = plan.clone();
            mutation.dimensions.retain(|d| d.id != removed.id);
            let drawings = drawings(&part, &a, &mutation, &font, &mut projections);
            let findings: Vec<_> = drawings.iter().flat_map(|d| lint::lint(&a, d)).collect();
            let evidence: Vec<_> = drawings
                .iter()
                .flat_map(|d| {
                    lint::marks(d)
                        .into_iter()
                        .map(|m| {
                            let span =
                                d.sheet
                                    .items
                                    .iter()
                                    .find(|i| i.name == m.name)
                                    .and_then(|i| {
                                        m.view
                                            .as_deref()
                                            .and_then(|v| {
                                                d.sheet.frames.get(v.trim_start_matches("v:"))
                                            })
                                            .and_then(|f| lint::location::ink_span(&a, f, &i.lines))
                                    });
                            json!({"name":m.name,"text":m.text,"claims":m.claims,"span":span})
                        })
                        .collect::<Vec<_>>()
                })
                .collect();
            rows.push(json!({"removed":removed,"findings":findings,"evidence":evidence}));
        }
        fs::write(
            &path,
            serde_json::to_vec(
                &json!({"input":rel,"analysis":a,"original":original,"mutations":rows}),
            )
            .unwrap(),
        )
        .unwrap();
        println!("{rel}: {} removals", rows.len());
    }
}
