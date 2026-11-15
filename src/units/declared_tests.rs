use super::{Paper, Units};
use crate::{api, model, pack};
use haecceity::pmi::LengthUnit;
use serde_json::json;
use std::path::Path;

fn inch_records() -> &'static str {
    "#999901=LENGTH_MEASURE_WITH_UNIT(LENGTH_MEASURE(0.0254),#999902);\n\
     #999902=(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT($,.METRE.));\n\
     #999903=DIMENSIONAL_EXPONENTS(1.,0.,0.,0.,0.,0.,0.);\n"
}

fn inch_unit() -> &'static str {
    "(CONVERSION_BASED_UNIT('INCH',#999901)LENGTH_UNIT()NAMED_UNIT(#999903))"
}

fn specimen(inches: bool, declared: bool) -> Vec<u8> {
    let mut text = include_str!("../../tests/fixtures/parts/synthetic_note_plate.step").to_owned();
    if inches {
        text = text.replace(
            "(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.))",
            inch_unit(),
        );
    }
    if !declared {
        text = text.replace(
            "GLOBAL_UNIT_ASSIGNED_CONTEXT((#159,#160,#161))",
            "GLOBAL_UNIT_ASSIGNED_CONTEXT((#160,#161))",
        );
    }
    // A detached INCH entity must never determine the drawing's units.
    text = text.replace(
        "ENDSEC;\nEND-ISO-10303-21;",
        &format!(
            "{}#999904={};\nENDSEC;\nEND-ISO-10303-21;",
            inch_records(),
            inch_unit()
        ),
    );
    text.into_bytes()
}

#[test]
fn declared_inches_default_to_ansi_without_authoring_spec_units() {
    let (part, a) = model::analyse_native_bytes("inch.step", &specimen(true, true)).unwrap();
    assert_eq!(part.declared_length_unit(), Some(LengthUnit::Inch));
    assert_eq!(a.declared_length_unit, Some(LengthUnit::Inch));
    let a: model::Analysis = serde_json::from_value(serde_json::to_value(a).unwrap()).unwrap();
    for planner in [model::plan, model::plan_escalated, model::plan_recovered] {
        let p = planner(&a, &model::Spec::default()).unwrap();
        assert_eq!(p.sheet.units, Units::In);
        assert_eq!(Paper::for_size(&p.sheet.size), Paper::Ansi);
        assert!(p.spec.units.is_auto());
        assert!(
            serde_json::to_value(&p.spec)
                .unwrap()
                .get("units")
                .is_none()
        );
        let notice = p
            .problems
            .iter()
            .find(|p| p.code == "declared_units")
            .unwrap();
        assert_eq!(serde_json::to_value(notice).unwrap()["tier"], "information");
        assert!(!p.problems.iter().any(|p| p.code == "inch_design_detected"));
        let restored: model::Plan =
            serde_json::from_value(serde_json::to_value(&p).unwrap()).unwrap();
        let again = planner(&a, &restored.spec).unwrap();
        assert_eq!(
            (again.sheet.units, again.sheet.size),
            (p.sheet.units, p.sheet.size)
        );
    }
}

#[test]
fn explicit_mm_wins_and_survives_spec_roundtrip() {
    let (_, a) = model::analyse_native_bytes("inch.step", &specimen(true, true)).unwrap();
    let spec: model::Spec = serde_json::from_value(json!({"units":"mm"})).unwrap();
    assert_eq!(serde_json::to_value(&spec).unwrap()["units"], "mm");
    let p = model::plan_recovered(&a, &spec).unwrap();
    assert_eq!(p.sheet.units, Units::Mm);
    assert_eq!(Paper::for_size(&p.sheet.size), Paper::Iso);
    assert_eq!(p.spec.units, Units::Mm);
    assert!(
        !p.problems
            .iter()
            .any(|p| matches!(p.code.as_str(), "declared_units" | "inch_design_detected"))
    );
    let pinned: model::Spec = serde_json::from_value(json!({"sheet":{"size":"A0"}})).unwrap();
    let p = model::plan(&a, &pinned).unwrap();
    assert_eq!(p.sheet.units, Units::In);
    assert_eq!(p.sheet.size, "A0");
}

#[test]
fn metric_and_undeclared_contexts_ignore_detached_inches() {
    for declared in [true, false] {
        let (_, a) =
            model::analyse_native_bytes("metric.step", &specimen(false, declared)).unwrap();
        assert_eq!(
            a.declared_length_unit,
            declared.then_some(LengthUnit::Millimetre)
        );
        let p = model::plan_recovered(&a, &model::Spec::default()).unwrap();
        assert_eq!(p.sheet.units, Units::Mm);
        assert_eq!(Paper::for_size(&p.sheet.size), Paper::Iso);
        assert!(!p.problems.iter().any(|p| p.code == "declared_units"));
    }
}

#[test]
fn assembly_selection_and_pack_use_each_products_context() {
    let text = include_str!("../../tests/fixtures/assembly/assembly.step");
    let text = text
        .replace(
            "#960 = ( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) );",
            &format!("#960={};", inch_unit()),
        )
        .replace(
            "ENDSEC;\nEND-ISO-10303-21;",
            &format!("{}ENDSEC;\nEND-ISO-10303-21;", inch_records()),
        );
    let file = haecceity::step::StepFile::read(text.as_bytes()).unwrap();
    assert_eq!(file.parts.len(), 2);
    let mut spec = pack::PackSpec::default();
    spec.ga.include = Some(false);
    let mut inch_products = 0;
    for (k, def) in file.parts.iter().enumerate() {
        let (_, a) =
            model::analyse_native_bytes_part("assembly.step", text.as_bytes(), Some(k)).unwrap();
        assert_eq!(a.declared_length_unit, def.declared_length_unit);
        let p = model::plan(&a, &model::Spec::default()).unwrap();
        let inches = def.declared_length_unit == Some(LengthUnit::Inch);
        inch_products += usize::from(inches);
        assert_eq!(p.sheet.units, if inches { Units::In } else { Units::Mm });
        spec.parts.insert(
            def.name.clone(),
            pack::PartSpec {
                supply: Some(pack::Supply::Make),
                ..Default::default()
            },
        );
    }
    assert_eq!(inch_products, 1);
    let pack = pack::build_bytes("assembly.step", text.as_bytes(), &spec).unwrap();
    assert!(pack.report.rows.iter().all(|r| r.error.is_none()));
    assert_eq!(
        pack.drawings
            .iter()
            .filter(|d| d.sheet.units == Units::In)
            .count(),
        1
    );
    assert_eq!(
        pack.drawings
            .iter()
            .filter(|d| d.sheet.units == Units::Mm)
            .count(),
        1
    );
}

#[test]
fn python_part_render_model_retains_the_declared_default() {
    let (part, a) = model::analyse_native_bytes("inch.step", &specimen(true, true)).unwrap();
    // The Python Part binding calls this same caller-owned semantic PMI entry point.
    let (p, files) = api::render_model(
        &part,
        &a,
        &Default::default(),
        &model::Spec::default(),
        &["svg", "dxf"],
        api::DPI,
    )
    .unwrap();
    assert_eq!(p.sheet.units, Units::In);
    for (_, bytes) in files {
        let embedded = model::embedded_spec(&bytes).unwrap().unwrap();
        assert!(embedded.units.is_auto());
    }
}

#[test]
fn editor_and_mcp_session_keep_the_source_default_after_units_edits() {
    let (part, a) = model::analyse_native_bytes("inch.step", &specimen(true, true)).unwrap();
    let mut session = crate::session::Session::new(part, a, model::Spec::default()).unwrap();
    assert_eq!(session.plan().sheet.units, Units::In);
    assert!(session.spec().units.is_auto());
    session
        .apply(&[crate::items::Op::set("units", json!("mm"))], true)
        .unwrap();
    assert_eq!(session.plan().sheet.units, Units::Mm);
    assert_eq!(serde_json::to_value(session.spec()).unwrap()["units"], "mm");
    session
        .apply(&[crate::items::Op::remove("units")], true)
        .unwrap();
    assert_eq!(session.plan().sheet.units, Units::In);
    assert!(session.spec().units.is_auto());
}

#[test]
fn ftc07_decimal_inches_reach_native_dxf_text() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/pmi/step/nist_ftc_07_asme1_ap242-e2.stp.gz");
    let (part, a) = model::analyse_native(&path).unwrap();
    assert_eq!(a.declared_length_unit, Some(LengthUnit::Inch));
    let p = model::plan_recovered(&a, &Default::default()).unwrap();
    assert!(
        p.dimensions
            .iter()
            .any(|d| (d.value - 1.524).abs() < 1e-6 && d.label.contains(".060"))
    );
    assert!(p.dimensions.iter().any(|d| d.label.contains("⌀.875 ±.010")));
    let font = crate::text::Font::new(api::MONO.to_vec()).unwrap();
    let drawing = model::render(&part, &a, &p, &font).unwrap();
    let dxf = String::from_utf8(drawing.dxf()).unwrap();
    assert!(dxf.contains("%%c.875"));
    assert!(dxf.contains(".060"));
    assert!(
        model::embedded_spec(dxf.as_bytes())
            .unwrap()
            .unwrap()
            .units
            .is_auto()
    );
}
