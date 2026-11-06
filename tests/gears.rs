//! Authored gear requirements, fixed ISO table, independent correspondence and table lint.
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use draftwright::bridge::{Recognition, Record};
use draftwright::gears::{self, GearSpec};
use draftwright::haecceity::Part;
use draftwright::lint::{self, Severity};
use draftwright::model::{self, Analysis, Spec};
use draftwright::output::Drawing;
use draftwright::text::Font;
use serde_json::{Value, json};

fn fixture(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts")
        .join(name)
}
fn blank() -> &'static (Part, Analysis) {
    static PART: OnceLock<(Part, Analysis)> = OnceLock::new();
    PART.get_or_init(|| model::analyse_native(&fixture("gear_blank.step")).unwrap())
}
fn declaration() -> Value {
    json!({"on":"f:turned_body:1", "frame":{"origin":[0,0,0],"axis":"z"}, "tooth_count":13,
        "module":1.25, "pressure_angle":20, "profile_shift":0, "face_width":10,
        "tooth_thickness":1.9634954084936207, "tooth_thickness_tolerance":[-0.03,0.01],
        "flank_tolerance_class":7})
}
fn gear_spec() -> Spec {
    serde_json::from_value(json!({"sheet":{"scale":"1:1"},"gears":[declaration()]})).unwrap()
}
fn font() -> Font {
    Font::open(&Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
        .unwrap()
}
fn drawing() -> (Analysis, Drawing) {
    let (part, a) = blank();
    let s = gear_spec();
    let a = draftwright::spec_gdt::effective(a, &s)
        .unwrap()
        .into_owned();
    let p = model::plan_recovered(&a, &s).unwrap();
    (a.clone(), model::render(part, &a, &p, &font()).unwrap())
}
fn codes(a: &Analysis, d: &Drawing) -> Vec<String> {
    lint::gears::check(a, d)
        .into_iter()
        .map(|i| i.code)
        .collect()
}
fn profile(a: &mut Analysis, axis: &str, count: u64) {
    let r = a.recognition.get_or_insert_with(|| Recognition {
        recogniser: "test".into(),
        frame: draftwright::bridge::native::IDENTITY,
        evidence: Vec::new(),
        refusals: Vec::new(),
        patterns: Vec::new(),
    });
    r.evidence.push(Record {family:"repeating_radial_profiles".into(),subtype:"RepeatingRadialProfile".into(),faces:Vec::new(),parameters:json!({"axis":axis,"centre":[0,0,0],"span":[-5,5],"repeat_count":count,"edge_count":52,"sector_signature":[]}).as_object().unwrap().clone()});
}

#[test]
fn gear_table_preserves_exact_python_rows_and_exports() {
    let (a, d) = drawing();
    let expected = json!([
        ["GEAR DATA", "VALUE", "BASIS"],
        ["TYPE", "METRIC EXTERNAL SPUR", "ISO 21771-1:2024"],
        ["BASIC RACK", "STANDARD", "ISO 53:1998"],
        ["TEETH z", "13", "ISO 21771-1:2024"],
        ["MODULE m", "1.25 mm", "ISO 54:1996"],
        ["PRESSURE ANGLE", "20 deg", "ISO 21771-1:2024"],
        ["PROFILE SHIFT x", "0", "ISO 21771-1:2024"],
        ["FACE WIDTH b", "10 mm", "ISO 21771-1:2024"],
        [
            "TOOTH THICKNESS s",
            "1.9634954084936207 +0.01/-0.03 mm",
            "ISO 21771-2:2025"
        ],
        ["FLANK CLASS", "7", "ISO 1328-1:2013"]
    ]);
    let p = model::plan_recovered(&a, &gear_spec()).unwrap();
    let table = p.tables.iter().find(|t| t.id == "gear_data_1").unwrap();
    assert_eq!(json!(table.rows), expected);
    assert!(table.placed.is_some());
    assert!(
        matches!(p.sheet.size.as_str(), "A4" | "A3"),
        "small blank must not need A2: {:?}",
        p.sheet
    );
    assert_eq!(p.sheet.scale, "1:1");
    assert_eq!(codes(&a, &d), ["gear_correspondence_unverifiable"]);
    let findings = lint::structural::check(&d);
    assert!(
        !findings.iter().any(|i| [
            "annotation_ink_overlap",
            "annotation_over_view",
            "annotation_outside_area"
        ]
        .contains(&i.code.as_str())),
        "{findings:?}"
    );
    let svg = d.svg();
    for row in expected.as_array().unwrap() {
        for cell in row.as_array().unwrap() {
            assert!(svg.contains(cell.as_str().unwrap()), "{cell}");
        }
    }
    let mut bound = gear_spec();
    bound.binding = Some(a.binding.clone());
    assert_eq!(
        model::embedded_spec(svg.as_bytes()).unwrap(),
        Some(bound.clone())
    );
    let pdf = d.pdf(&|name| {
        std::fs::read(
            Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("fonts")
                .join(name),
        )
        .ok()
    });
    assert_eq!(model::embedded_spec(&pdf).unwrap(), Some(bound));
    assert!(
        !lint::lint_sheets(&a, &[d])
            .iter()
            .any(|i| i.code == "gear_requirement_mismatch")
    );
}

#[test]
fn invalid_gear_requirements_are_refused_one_per_python_validation_rule() {
    for (key, value, message) in [
        ("tooth_count", json!(4), "tooth_count"),
        ("tooth_count", json!(1001), "tooth_count"),
        ("tooth_count", json!(true), "tooth_count"),
        ("tooth_count", json!(13.5), "tooth_count"),
        ("module", json!(0), "module"),
        ("module", json!(-1), "module"),
        ("module", json!(0.49), "module"),
        ("module", json!(71), "module"),
        ("module", json!(true), "module"),
        ("pressure_angle", json!("20"), "pressure_angle"),
        ("pressure_angle", json!(0), "pressure_angle"),
        ("pressure_angle", json!(90), "pressure_angle"),
        ("profile_shift", json!("0"), "profile_shift"),
        ("profile_shift", json!(true), "profile_shift"),
        ("face_width", json!(0), "face_width"),
        ("face_width", json!(3.9), "face_width"),
        ("face_width", json!(1201), "face_width"),
        ("face_width", json!(true), "face_width"),
        ("tooth_thickness", json!(0), "tooth_thickness"),
        ("tooth_thickness", json!(false), "tooth_thickness"),
        ("tooth_thickness_tolerance", json!([0]), "tolerance"),
        ("tooth_thickness_tolerance", json!([true, 0]), "tolerance"),
        (
            "tooth_thickness_tolerance",
            json!([0.02, -0.01]),
            "lower deviation",
        ),
        (
            "tooth_thickness_tolerance",
            json!([-2, 0]),
            "positive lower limit",
        ),
        (
            "flank_tolerance_class",
            json!(true),
            "flank_tolerance_class",
        ),
        ("flank_tolerance_class", json!(0), "flank_tolerance_class"),
        ("flank_tolerance_class", json!(12), "flank_tolerance_class"),
        ("flank_tolerance_class", json!(7.5), "flank_tolerance_class"),
        ("frame", json!({"origin":[0,0],"axis":"z"}), "frame"),
        ("frame", json!({"origin":[0,true,0],"axis":"z"}), "frame"),
        ("frame", json!({"origin":[0,0,0],"axis":"q"}), "axis"),
    ] {
        let mut g = declaration();
        g[key] = value;
        let err = serde_json::from_value::<Spec>(json!({"gears":[g]})).unwrap_err();
        assert!(err.to_string().contains(message), "{key}: {err}");
    }
    for (teeth, module) in [(5, 0.5), (1000, 70.0)] {
        let mut g = declaration();
        g["tooth_count"] = json!(teeth);
        g["module"] = json!(module);
        assert!(
            serde_json::from_value::<Spec>(json!({"gears":[g]}))
                .unwrap_err()
                .to_string()
                .contains("reference diameter")
        );
    }
    for key in declaration().as_object().unwrap().keys() {
        let mut g = declaration();
        g.as_object_mut().unwrap().remove(key);
        assert!(
            serde_json::from_value::<Spec>(json!({"gears":[g]}))
                .unwrap_err()
                .to_string()
                .contains(key)
        );
    }
    let base: GearSpec = serde_json::from_value(declaration()).unwrap();
    for key in [
        "origin",
        "module",
        "face_width",
        "tooth_thickness",
        "pressure_angle",
        "profile_shift",
        "tolerance",
    ] {
        for value in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            let mut g = base.clone();
            match key {
                "origin" => g.frame.origin[1] = value,
                "module" => g.module = value,
                "face_width" => g.face_width = value,
                "tooth_thickness" => g.tooth_thickness = value,
                "pressure_angle" => g.pressure_angle = value,
                "profile_shift" => g.profile_shift = value,
                _ => g.tooth_thickness_tolerance[0] = value,
            }
            assert!(g.check().unwrap_err().message.contains(key), "{key}");
        }
    }
}

#[test]
fn gear_anchors_and_editor_preserve_authored_intent() {
    let (_, a) = blank();
    let s = gear_spec();
    let edited = model::edit(
        &Spec::default(),
        &json!([{"op":"add","path":"gears","value":declaration()}]),
    )
    .unwrap();
    assert_eq!(edited.gears, s.gears);
    assert!(
        model::edit(
            &s,
            &json!([{"op":"set","path":"gears","value":[{"on":"top"}]}])
        )
        .is_err()
    );
    for anchor in ["face:0", "top", "f:turned_body:1"] {
        let mut s = s.clone();
        s.gears[0].on = anchor.into();
        assert!(model::plan(a, &s).is_ok(), "{anchor}");
    }
    for anchor in ["face:999", "f:boss:999", "unknown"] {
        let mut s = s.clone();
        s.gears[0].on = anchor.into();
        assert!(model::plan(a, &s).is_err(), "{anchor}");
    }
    let mut upper = declaration();
    upper["frame"]["axis"] = json!("Z");
    assert_eq!(
        serde_json::from_value::<GearSpec>(upper)
            .unwrap()
            .frame
            .axis,
        "z"
    );
    assert_eq!(gears::authored(-0.0), "0");
    assert_eq!(gears::authored(1e-7), "1e-07");
    assert_eq!(gears::authored(1e20), "100000000000000000000");
}

#[test]
fn turned_steps_can_anchor_authored_gear_requirements() {
    let (_, a) = model::analyse_native(&fixture("turned.step")).unwrap();
    let step = a.features.iter().find(|f| f.kind == "turned_step").unwrap();
    let mut s = gear_spec();
    s.gears[0].on = step.id.clone();
    assert!(draftwright::spec_gdt::effective(&a, &s).is_ok());
    let mut wrong = a.clone();
    wrong
        .features
        .iter_mut()
        .find(|f| f.id == step.id)
        .unwrap()
        .kind = "hole".into();
    assert!(draftwright::spec_gdt::effective(&wrong, &s).is_err());
}

#[test]
fn python_gear_limits_are_inclusive_without_extra_tooth_geometry_constraints() {
    let mut g: GearSpec = serde_json::from_value(declaration()).unwrap();
    g.tooth_count = 5;
    g.module = 1.0;
    g.face_width = 4.0;
    g.flank_tolerance_class = 1;
    g.pressure_angle = 0.001;
    g.profile_shift = -100.0;
    assert!(g.check().is_ok());
    g.tooth_count = 1000;
    g.module = 15.0;
    g.face_width = 1200.0;
    g.flank_tolerance_class = 11;
    g.pressure_angle = 89.999;
    g.profile_shift = 100.0;
    assert!(g.check().is_ok());
    g.tooth_count = 10;
    g.module = 0.5;
    assert!(g.check().is_ok());
    g.tooth_count = 100;
    g.module = 70.0;
    assert!(g.check().is_ok());
}

#[test]
fn missing_and_duplicate_gear_tables_fail_coverage() {
    let (a, mut d) = drawing();
    let table = d
        .annotations
        .iter()
        .find(|an| an.name == "gear_data_1")
        .unwrap()
        .clone();
    d.annotations.push(table);
    assert!(codes(&a, &d).contains(&"gear_requirement_ambiguous".into()));
    d.annotations.retain(|an| an.name != "gear_data_1");
    assert!(codes(&a, &d).contains(&"gear_requirement_missing".into()));
    let (_, mut d) = drawing();
    d.sheet.items.retain(|item| item.name != "gear_data_1");
    assert!(codes(&a, &d).contains(&"gear_requirement_missing".into()));
}

#[test]
fn stale_or_damaged_gear_table_is_a_requirement_mismatch() {
    let (mut a, mut d) = drawing();
    let mut changed = a.clone();
    changed
        .recognition
        .as_mut()
        .unwrap()
        .evidence
        .iter_mut()
        .find(|r| r.family == "authored_gears")
        .unwrap()
        .parameters
        .insert("pressure_angle".into(), json!(25));
    assert!(codes(&changed, &d).contains(&"gear_requirement_mismatch".into()));
    profile(&mut a, "z", 13);
    assert!(codes(&a, &d).is_empty());
    let run = d
        .annotations
        .iter()
        .find(|an| an.name == "gear_data_1")
        .unwrap()
        .runs[10];
    d.text[run].text = "2 mm".into();
    assert_eq!(codes(&a, &d), ["gear_requirement_mismatch"]);
}

#[test]
fn declared_gear_axis_mismatch_uses_correspondence_before_axis() {
    let (mut a, d) = drawing();
    profile(&mut a, "x", 13);
    assert_eq!(codes(&a, &d), ["gear_axis_mismatch"]);
    a.solid_count = Some(2);
    assert!(
        lint::gears::check(&a, &d)
            .iter()
            .all(|i| i.severity == Severity::Info)
    );
}
#[test]
fn declared_gear_repeat_count_must_match_full_wire_evidence() {
    let (mut a, d) = drawing();
    profile(&mut a, "z", 12);
    assert_eq!(codes(&a, &d), ["gear_repeat_count_mismatch"]);
}
#[test]
fn gear_correspondence_fails_closed_when_absent_ambiguous_or_displaced() {
    let (mut a, d) = drawing();
    assert_eq!(codes(&a, &d), ["gear_correspondence_unverifiable"]);
    profile(&mut a, "z", 13);
    profile(&mut a, "z", 13);
    assert_eq!(codes(&a, &d), ["gear_correspondence_unverifiable"]);
    a.recognition.as_mut().unwrap().evidence.pop();
    a.recognition
        .as_mut()
        .unwrap()
        .evidence
        .last_mut()
        .unwrap()
        .parameters
        .insert("centre".into(), json!([0, 0, 1]));
    assert_eq!(codes(&a, &d), ["gear_correspondence_unverifiable"]);
}
#[test]
fn repeating_profile_never_invents_gear_semantics() {
    let (mut a, mut d) = drawing();
    a.recognition
        .as_mut()
        .unwrap()
        .evidence
        .retain(|r| r.family != "authored_gears");
    d.annotations.retain(|an| an.name != "gear_data_1");
    profile(&mut a, "z", 13);
    assert_eq!(codes(&a, &d), ["gear_semantics_missing"]);
    assert!(gears::tables(&a).is_empty());
}

#[test]
fn real_wheel_full_wire_evidence_reconciles_authored_gear() {
    use std::io::Read;
    let mut bytes = Vec::new();
    flate2::read::GzDecoder::new(std::fs::File::open(fixture("gear_wheel.step.gz")).unwrap())
        .read_to_end(&mut bytes)
        .unwrap();
    let (part, a) = model::analyse_native_step(Path::new("gear_wheel.step"), &bytes).unwrap();
    assert!(
        !a.unrecognised
            .iter()
            .any(|u| u.family == "repeating_radial_profiles")
    );
    // Taking geometry evidence is still a fail-closed schema read.
    let recognition = quiddity::correspondence::recognise(&part);
    let mut doc = quiddity::recognition::document(&recognition, part.faces.len());
    let index = doc
        .records
        .iter()
        .position(|r| r.family == "repeating_radial_profiles")
        .unwrap();
    let count = doc.records[index]
        .parameters
        .remove("repeat_count")
        .unwrap();
    assert!(
        draftwright::bridge::native::read(&part, &doc)
            .unwrap_err()
            .contains("repeat_count")
    );
    doc.records[index]
        .parameters
        .insert("repeat_count".into(), count);
    // A profile quiddity measures from its own origin is left out, not misread.
    doc.records[index]
        .parameters
        .insert("origin".into(), serde_json::json!([1.0, 2.0, 3.0]));
    assert!(
        !draftwright::bridge::native::read(&part, &doc)
            .unwrap()
            .evidence
            .iter()
            .any(|e| e.family == "repeating_radial_profiles")
    );
    let profiles: Vec<_> = a
        .recognition
        .as_ref()
        .unwrap()
        .evidence
        .iter()
        .filter(|r| r.family == "repeating_radial_profiles")
        .collect();
    assert_eq!(profiles.len(), 1, "{profiles:?}");
    let p = &profiles[0].parameters;
    assert_eq!(p["repeat_count"], 13);
    let mut declaration = declaration();
    declaration["on"] = json!("face:0");
    declaration["frame"] = json!({"origin":[0,0,0],"axis":"z"});
    declaration["module"] = json!(0.5);
    declaration["tooth_thickness"] = json!(0.78);
    declaration["face_width"] = json!(7.7000002);
    let s: Spec = serde_json::from_value(json!({"gears":[declaration]})).unwrap();
    let a = draftwright::spec_gdt::effective(&a, &s).unwrap();
    let p = model::plan_recovered(&a, &s).unwrap();
    let d = model::render(&part, &a, &p, &font()).unwrap();
    assert!(codes(&a, &d).is_empty(), "{:?}", lint::gears::check(&a, &d));
    let mut wrong = a.into_owned();
    wrong
        .recognition
        .as_mut()
        .unwrap()
        .evidence
        .iter_mut()
        .find(|r| r.family == "authored_gears")
        .unwrap()
        .parameters
        .insert("tooth_count".into(), json!(12));
    assert!(
        lint::lint_sheets(&wrong, &[d])
            .iter()
            .any(|i| i.code == "gear_repeat_count_mismatch")
    );
}

#[test]
fn printed_profile_semantics_are_warning_only() {
    let (mut a, mut d) = drawing();
    a.recognition
        .as_mut()
        .unwrap()
        .evidence
        .retain(|r| r.family != "authored_gears");
    profile(&mut a, "z", 13);
    a.solid_count = Some(2); // Printed semantics stay warnings even on a multi-solid product.
    let intent: Spec = serde_json::from_value(json!({"process":"3D printed: FDM"})).unwrap();
    let a = draftwright::spec_gdt::effective(&a, &intent).unwrap();
    d.spec = Some(serde_json::to_string(&intent).unwrap());
    let issues = lint::gears::check(&a, &d);
    assert!(
        issues
            .iter()
            .any(|i| i.code == "gear_semantics_missing" && i.severity == Severity::Warning)
    );
    // A new answer overrides the effective process; lint still reads only Analysis.
    let machining: Spec = serde_json::from_value(json!({"process":"machined"})).unwrap();
    let a = draftwright::spec_gdt::effective(&a, &machining).unwrap();
    assert!(
        lint::gears::check(&a, &d)
            .iter()
            .any(|i| i.code == "gear_semantics_missing" && i.severity == Severity::Info)
    );
}
