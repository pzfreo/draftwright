//! Reproduce the three before/after drawings for #76 item 1.
//! cargo run --release --example authored_dimensions -- target/authored-dimensions
use draftwright::{
    api,
    model::{self, Analysis, Spec, dimension_geometry::Reference},
    text::Font,
};
use serde_json::json;
use std::{
    fs,
    path::{Path, PathBuf},
};

fn planes(a: &Analysis, k: usize) -> Vec<(String, f64)> {
    let mut out: Vec<_> = a
        .dimension_references()
        .iter()
        .filter_map(|(id, rs)| match rs.as_slice() {
            [Reference::Plane { origin, normal, .. }] if normal[k].abs() > 1.0 - 1e-8 => {
                Some((id.clone(), origin[k]))
            }
            _ => None,
        })
        .collect();
    out.sort_by(|p, q| p.1.total_cmp(&q.1));
    out
}
fn save(
    out: &Path,
    name: &str,
    part: &draftwright::haecceity::Part,
    a: &Analysis,
    s: Spec,
) -> Result<(), Box<dyn std::error::Error>> {
    let font = Font::new(api::MONO.to_vec()).ok_or("bundled font is invalid")?;
    for (state, spec) in [("before", Spec::default()), ("after", s)] {
        let p = model::plan_recovered(a, &spec).map_err(|e| format!("{name}: {e:?}"))?;
        let drawing = model::render(part, a, &p, &font)?;
        let stem = out.join(format!("{name}-{state}"));
        fs::write(stem.with_extension("png"), drawing.png(120.0)?)?;
        fs::write(stem.with_extension("svg"), drawing.svg())?;
        fs::write(
            stem.with_extension("spec.json"),
            serde_json::to_vec_pretty(&p.spec)?,
        )?;
        fs::write(
            stem.with_extension("plan.json"),
            serde_json::to_vec_pretty(&p)?,
        )?;
        let mut issues = draftwright::lint::lint(a, &drawing);
        issues.extend(draftwright::lint::structural::check(&drawing));
        fs::write(
            stem.with_extension("lint.json"),
            serde_json::to_vec_pretty(&issues)?,
        )?;
        println!(
            "{}: {} at {}; {} lint findings",
            stem.display(),
            p.sheet.size,
            p.sheet.scale,
            issues.len()
        );
    }
    Ok(())
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let out = std::env::args_os()
        .nth(1)
        .map(PathBuf::from)
        .unwrap_or_else(|| "target/authored-dimensions".into());
    fs::create_dir_all(&out)?;
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts");
    let (part, a) =
        model::analyse_native(&root.join("plate.step")).map_err(|e| format!("{e:?}"))?;
    let vertices: Vec<_> = a
        .dimension_references()
        .iter()
        .filter_map(|(id, rs)| match rs.as_slice() {
            [Reference::Vertex { point, .. }] => Some((id, point)),
            _ => None,
        })
        .collect();
    let (p, q) = vertices
        .iter()
        .flat_map(|p| vertices.iter().map(move |q| (p, q)))
        .find(|(p, q)| {
            (p.1[1] - q.1[1]).abs() < 1e-7
                && (p.1[0] - q.1[0]).abs() > 1e-3
                && (p.1[2] - q.1[2]).abs() > 1e-3
        })
        .unwrap();
    let diagonal: Spec = serde_json::from_value(
        json!({"dimensions":{"d:authored:diagonal":{"between":[p.0,q.0],"along":"true","view":"front","tolerance":"±0.1","place":{"side":"below","lane":1}}}}),
    )?;
    save(&out, "diagonal", &part, &a, diagonal)?;
    let x = planes(&a, 0);
    let z = planes(&a, 2);
    let angle: Spec = serde_json::from_value(json!({"dimensions":{
        "d:authored:corner":{"kind":"angle","between":[x[0].0,z.last().unwrap().0],"view":"front","tolerance":"±0.5"}
    }}))?;
    save(&out, "angle", &part, &a, angle)?;
    // No recognized feature document: the author sizes a real cylinder directly from B-rep.
    let (part, a) =
        model::analyse(&root.join("mounting_plate.step")).map_err(|e| format!("{e:?}"))?;
    let id = a
        .dimension_references()
        .iter()
        .find_map(|(id, rs)| matches!(rs.as_slice(), [Reference::Cylinder { .. }]).then_some(id))
        .unwrap();
    let size: Spec = serde_json::from_value(json!({"dimension_source":"authored","dimensions":{
        "d:envelope/width.length":{},"d:envelope/depth.length":{},"d:envelope/height.length":{},
        "d:authored:bore":{"kind":"diameter","between":[id],"view":"plan","tolerance":"±0.05"}
    }}))?;
    save(&out, "cylinder", &part, &a, size)?;
    Ok(())
}
