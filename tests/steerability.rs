//! Executable reproducers for #293–#331 (PR #306 excluded). No production policy changes.
//! See fixtures/steerability/README.md for provenance, scope and the expected-failure contract.
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};

use draftwright::dims::PlannedDimension;
use draftwright::haecceity::Part;
use draftwright::lint::{self, LintIssue};
use draftwright::model::{self, Analysis, Plan, Projections, Spec, dimension_geometry::Reference};
use draftwright::output::Drawing;
use draftwright::{api, text::Font};
use serde::Deserialize;
use serde_json::{Value, json};

const BRACKET: &str = "angle-bracket";
const SHAFT: &str = "stepped-shaft-keyway";
const FLANGE: &str = "circular-flange";
const BAR: &str = "guide-bar";
const PLATE: &str = "perforated-plate";
const PIVOT: &str = "pivot-mount-plate";
const CTC: &str = "nist-ctc-01";

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/steerability")
}
fn input(name: &str) -> Value {
    serde_json::from_slice(&std::fs::read(root().join(format!("{name}.spec.json"))).unwrap())
        .unwrap()
}
fn minimal() -> Value {
    json!({"schema":"draftwright.spec/2"})
}
fn bytes(name: &str) -> Vec<u8> {
    let path = if name == CTC {
        root().join("../pmi/step/nist_ctc_01_asme1_ap242-e1.stp.gz")
    } else {
        root().join(format!("{name}.step.gz"))
    };
    std::fs::read(path).unwrap()
}
fn filename(name: &str) -> String {
    if name == CTC {
        "nist_ctc_01_asme1_ap242-e1.stp.gz".into()
    } else {
        format!("{name}.step.gz")
    }
}
struct Fixture {
    part: Part,
    analysis: Analysis,
    plans: Mutex<BTreeMap<String, Arc<Planned>>>,
    projections: Mutex<Projections>,
}
struct Planned {
    plan: Plan,
    rendered: OnceLock<Rendered>,
}
struct Rendered {
    drawings: Vec<Drawing>,
    lint: Vec<LintIssue>,
    structural: Vec<LintIssue>,
}
fn fixture(name: &str) -> &'static Fixture {
    static FIXTURES: [OnceLock<Fixture>; 7] = [const { OnceLock::new() }; 7];
    let index = [BRACKET, SHAFT, FLANGE, BAR, PLATE, PIVOT, CTC]
        .iter()
        .position(|n| *n == name)
        .unwrap();
    FIXTURES[index].get_or_init(|| {
        let (part, analysis) = model::analyse_native_bytes(&filename(name), &bytes(name)).unwrap();
        Fixture {
            part,
            analysis,
            plans: Mutex::default(),
            projections: Mutex::default(),
        }
    })
}
fn plan_result(name: &str, value: &Value) -> Result<Arc<Planned>, String> {
    let spec: Spec = serde_json::from_value(value.clone()).map_err(|e| e.to_string())?;
    let f = fixture(name);
    let key = value.to_string();
    let mut plans = f.plans.lock().unwrap();
    if let Some(p) = plans.get(&key) {
        return Ok(Arc::clone(p));
    }
    let plan = api::plan_analysed(&f.analysis, &spec).map_err(|e| e.to_string())?;
    let p = Arc::new(Planned {
        plan,
        rendered: OnceLock::new(),
    });
    plans.insert(key, Arc::clone(&p));
    Ok(p)
}
fn planned(name: &str, value: &Value) -> Arc<Planned> {
    plan_result(name, value).unwrap_or_else(|e| panic!("fixture {name}: {e}"))
}
fn rendered<'a>(name: &str, p: &'a Planned) -> &'a Rendered {
    p.rendered.get_or_init(|| {
        let f = fixture(name);
        let font = Font::new(api::MONO.to_vec()).unwrap();
        let drawings = if p.plan.sheets.is_empty() {
            vec![
                model::render_with(
                    &f.part,
                    &f.analysis,
                    &p.plan,
                    &font,
                    &mut f.projections.lock().unwrap(),
                )
                .unwrap(),
            ]
        } else {
            model::render_sheets(&f.part, &f.analysis, &p.plan, &font).unwrap()
        };
        let lint = lint::lint_sheets(&f.analysis, &drawings);
        let structural = lint::structural::check_sheets(&drawings);
        Rendered {
            drawings,
            lint,
            structural,
        }
    })
}
fn dim<'a>(p: &'a Plan, id: &str) -> Option<&'a PlannedDimension> {
    p.dimensions
        .iter()
        .find(|d| d.id == id && d.placed.is_some())
}
fn has(p: &Plan, code: &str) -> bool {
    p.problems.iter().any(|f| f.code == code)
}
fn missing(r: &Rendered, code: &str, feature: &str, parameter: &str) -> bool {
    r.lint.iter().any(|f| {
        f.code == code
            && (f.subjects.iter().any(|s| s == feature)
                || f.requirements.iter().any(|q| q.feature == feature))
            && (parameter.is_empty() || f.requirements.iter().any(|q| q.parameter == parameter))
    })
}
fn set_dim(s: &mut Value, id: &str, v: Value) {
    s["dimensions"][id] = v;
}
fn plane(name: &str, axis: usize, coordinate: f64) -> String {
    fixture(name)
        .analysis
        .dimension_references()
        .iter()
        .find_map(|(key, rs)| {
            rs.iter()
                .any(|r| {
                    matches!(r, Reference::Plane { origin, normal, .. }
            if normal[axis].abs() > 1.0 - 1e-8 && (origin[axis] - coordinate).abs() < 1e-5)
                })
                .then(|| key.clone())
        })
        .unwrap_or_else(|| panic!("{name}: no plane {axis}={coordinate}"))
}
fn between(a: String, b: String, along: &str, view: &str) -> Value {
    json!({"between":[a,b],"along":along,"view":view})
}

#[derive(Deserialize)]
struct Expected {
    reason: String,
    checks: BTreeSet<String>,
}
#[derive(Default)]
struct Checks(BTreeSet<String>);
impl Checks {
    fn want(&mut self, name: &str, holds: bool) {
        if !holds {
            self.0.insert(name.into());
        }
    }
    /// Only the issue's known refusal becomes a target failure. Other errors, panics,
    /// broken fixtures and rendering failures fail the test normally, never as xfail.
    fn accepts(
        &mut self,
        name: &str,
        s: &Value,
        check: &str,
        refusal: &str,
    ) -> Option<Arc<Planned>> {
        match plan_result(name, s) {
            Ok(p) => Some(p),
            Err(e) => {
                assert!(e.contains(refusal), "unexpected refusal for {check}: {e}");
                eprintln!("REFUSAL {check}: {e}");
                self.want(check, false);
                None
            }
        }
    }
    fn finish(self, issue: u32) {
        let expected: BTreeMap<u32, Expected> =
            serde_json::from_str(include_str!("fixtures/steerability/expected.json")).unwrap();
        if let Some(e) = expected.get(&issue) {
            assert!(
                !self.0.is_empty(),
                "#{issue} now passes: remove its expected-failure entry ({})",
                e.reason
            );
            assert_eq!(
                self.0, e.checks,
                "#{issue}: failure changed; review the target assertions ({})",
                e.reason
            );
            eprintln!("EXPECTED-FAIL #{issue}: {} {:?}", e.reason, self.0);
        } else {
            assert!(
                self.0.is_empty(),
                "#{issue} target assertions failed: {:?}",
                self.0
            );
        }
    }
}

#[test]
fn steer_293_inclined_hole() {
    let s = input("inline-293");
    let p = planned(BRACKET, &s);
    let r = rendered(BRACKET, &p);
    let mut c = Checks::default();
    for code in ["feature_not_located", "feature_not_dimensioned"] {
        c.want(code, !missing(r, code, "f:hole:2", ""));
    }
    let mut note = s;
    note["features"] = json!({"f:hole:2":{"note":"LOCATED ALONG FLANGE AND ACROSS", "note_satisfies":["location.length.z"]}});
    c.accepts(
        BRACKET,
        &note,
        "lint_role_accepted",
        "not a measurement role",
    );
    c.finish(293);
}

#[test]
fn steer_294_distinct_feature_owner() {
    let mut s = input("inline-293");
    s["dimensions"]["d:authored:hole2_y"]["between"][1] =
        json!("face:v1:5fdfb46ec239923be4a21b4361e93e5ef60fca9ac0dae9162f6174ce64a65b0f");
    let p = planned(BRACKET, &s);
    let d = dim(&p.plan, "d:authored:hole2_y").expect("second hole's dimension prints");
    assert!(
        d.measurements
            .iter()
            .any(|m| m.feature == "f:hole:2" && m.parameter == "location.length.y")
    );
    assert!(!missing(
        rendered(BRACKET, &p),
        "feature_not_located",
        "f:hole:2",
        "location.length.y"
    ));
    s["dimensions"]["d:authored:hole2_y_duplicate"] = s["dimensions"]["d:authored:hole2_y"].clone();
    let e = plan_result(BRACKET, &s)
        .err()
        .expect("same-feature duplicate still refused");
    assert!(e.contains("K6"), "{e}");
    Checks::default().finish(294);
}

#[test]
fn steer_295_automatic_sheet() {
    let mut s = input("inline-293");
    let explicit = planned(BRACKET, &s);
    assert_eq!(explicit.plan.sheet.size, "A4");
    assert_eq!(explicit.plan.sheet.scale, "1:1");
    s["sheet"].as_object_mut().unwrap().remove("size");
    s["sheet"].as_object_mut().unwrap().remove("scale");
    let auto = planned(BRACKET, &s);
    let mut c = Checks::default();
    c.want(
        "a4_one_to_one",
        auto.plan.sheet.size == "A4" && auto.plan.sheet.scale == "1:1",
    );
    c.finish(295);
}

#[test]
fn steer_296_reserve_explicit_lane() {
    let mut s = input("inline-293");
    s["dimensions"]["d:envelope/height.length"]["place"] = json!({"side":"left","lane":1});
    let p = planned(BRACKET, &s);
    let r = rendered(BRACKET, &p);
    let mut c = Checks::default();
    c.want(
        "no_annotation_off_sheet",
        !has(&p.plan, "annotation_off_sheet")
            && !r
                .structural
                .iter()
                .any(|f| f.code == "annotation_off_sheet"),
    );
    c.want("no_page_fit_uncertain", !has(&p.plan, "page_fit_uncertain"));
    c.finish(296);
}

#[test]
fn steer_297_reference_roles() {
    let _p = planned(BRACKET, &input("inline-293"));
    let refs = serde_json::to_value(fixture(BRACKET).analysis.edge_ink.as_ref().unwrap()).unwrap();
    let refs = refs["references"].as_object().unwrap();
    assert!(!refs.is_empty());
    let mut c = Checks::default();
    c.want(
        "reference_membership",
        refs.values().all(|v| {
            v.as_array()
                .unwrap()
                .iter()
                .all(|r| r.get("features").is_some() || r.get("stock_faces").is_some())
        }),
    );
    c.want(
        "face_boundaries",
        refs.iter()
            .filter(|(key, _)| key.starts_with("face:"))
            .all(|(_, v)| {
                v.as_array()
                    .unwrap()
                    .iter()
                    .all(|r| r.get("edges").is_some() && r.get("vertices").is_some())
            }),
    );
    c.finish(297);
}

#[test]
fn steer_298_agent_discovery() {
    let p = planned(BRACKET, &input("inline-293"));
    let mut c = Checks::default();
    for flag in ["--help", "--version"] {
        let out = std::process::Command::new(env!("CARGO_BIN_EXE_draftwright"))
            .arg(flag)
            .output()
            .unwrap();
        c.want(flag, out.status.success());
    }
    c.want("schema_operation", api::OPERATIONS.contains(&"spec_schema"));
    c.want(
        "item_options_operation",
        api::OPERATIONS.contains(&"item_options"),
    );
    let d = dim(&p.plan, "d:f:hole:1/bore.diameter").unwrap();
    let record = serde_json::to_value(d).unwrap();
    c.want(
        "callout_offset",
        record.get("callout_offset").is_some() || record.get("offset").is_some(),
    );
    c.finish(298);
}

#[test]
fn steer_299_agent_rough_edges() {
    let p = planned(BRACKET, &minimal());
    let f = p
        .plan
        .problems
        .iter()
        .find(|p| p.code == "general_tolerance_missing")
        .unwrap();
    let mut c = Checks::default();
    c.want(
        "tolerance_fix_local",
        f.offered_fixes().iter().any(|f| {
            !f.ops.is_empty() && f.ops.iter().all(|op| op.path == "sheet/title/tolerance")
        }),
    );
    let s = input("inline-293");
    let p = planned(BRACKET, &s);
    c.want(
        "reference_precision",
        !rendered(BRACKET, &p).lint.iter().any(|f| {
            f.code == "nominal_rounded"
                && f.annotation.as_deref() == Some("d:envelope/height.length")
        }),
    );
    let mut detail = s;
    detail["views"] =
        json!({"detail_a":{"detail":{"around":"f:hole:2","from":"side","scale_factor":2}}});
    match plan_result(BRACKET, &detail) {
        Ok(_) => {}
        Err(e) => {
            assert!(e.contains("needs its source view and room"), "{e}");
            c.want("detail_reason", false);
        }
    }
    c.finish(299);
}

#[test]
fn steer_300_oblique_shoulders() {
    let s = input("inline-293");
    let p = planned(BRACKET, &s);
    let r = rendered(BRACKET, &p);
    let shoulder = |f: &&LintIssue| {
        f.code == "unrecognised_defining_geometry"
            && (f.message.contains("75.381") || f.message.contains("93.072"))
    };
    let mut c = Checks::default();
    c.want(
        "shoulders_defined",
        r.lint.iter().filter(shoulder).count() == 0,
    );
    let angle = p
        .plan
        .dimensions
        .iter()
        .find(|d| d.kind == "angular" && (d.value - 120.).abs() < 1e-5)
        .unwrap();
    let mut without = s;
    set_dim(&mut without, &angle.id, json!({"omit":true}));
    let p = planned(BRACKET, &without);
    c.want(
        "missing_angle_reported",
        rendered(BRACKET, &p).lint.iter().filter(shoulder).count() > 0,
    );
    c.finish(300);
}

#[test]
fn steer_301_authored_pocket() {
    let mut s = input("inline-301");
    let p = planned(SHAFT, &s);
    let mut c = Checks::default();
    c.want(
        "end_plus_length_locates_centre",
        !missing(
            rendered(SHAFT, &p),
            "pocket_requirement_missing",
            "f:section_recess:1",
            "location_pocket.location.x",
        ),
    );
    set_dim(
        &mut s,
        "d:f:section_recess:1/pocket_width.length",
        json!({"omit":true}),
    );
    set_dim(
        &mut s,
        "d:authored:keyway_length",
        between(plane(SHAFT, 0, 40.), plane(SHAFT, 0, 80.), "x", "plan"),
    );
    set_dim(
        &mut s,
        "d:authored:keyway_width",
        between(plane(SHAFT, 1, -3.), plane(SHAFT, 1, 3.), "y", "plan"),
    );
    let p = planned(SHAFT, &s);
    let r = rendered(SHAFT, &p);
    for role in ["pocket_width.length", "pocket_length.length"] {
        c.want(
            role,
            !missing(r, "pocket_requirement_missing", "f:section_recess:1", role),
        );
    }
    assert!(
        r.lint.iter().any(|f| f.code == "pocket_requirement_missing"
            && f.requirements.iter().any(|q| q.parameter.contains("depth"))),
        "the unstated depth must remain required"
    );
    c.finish(301);
}

#[test]
fn steer_302_axis_origin() {
    let mut s = input("inline-301");
    s.as_object_mut().unwrap().remove("features");
    s["dimensions"]
        .as_object_mut()
        .unwrap()
        .remove("d:f:section_recess:1/location_pocket.location.y");
    let p = planned(SHAFT, &s);
    let mut c = Checks::default();
    c.want(
        "no_tangent_location",
        dim(&p.plan, "d:f:section_recess:1/location_pocket.location.y").is_none(),
    );
    s["origin"] = json!({"y":"axis:f:turned_step:2"});
    c.accepts(
        SHAFT,
        &s,
        "axis_origin",
        "must identify min, max or one plane normal to y",
    );
    c.finish(302);
}

#[test]
fn steer_303_keyway_fit_and_view() {
    let mut c = Checks::default();
    for fit in ["N9", "P9", "JS9"] {
        let mut s = input("inline-301");
        set_dim(
            &mut s,
            "d:f:section_recess:1/pocket_width.length",
            json!({"fit":fit}),
        );
        if let Some(p) = c.accepts(SHAFT, &s, fit, "not in draftwright's table") {
            c.want(
                fit,
                dim(&p.plan, "d:f:section_recess:1/pocket_width.length")
                    .is_some_and(|d| d.label.contains(fit)),
            );
        }
    }
    let mut s = input("inline-301");
    s.as_object_mut().unwrap().remove("views");
    set_dim(
        &mut s,
        "d:f:section_recess:1/pocket_width.length",
        json!({"omit":true}),
    );
    set_dim(
        &mut s,
        "d:authored:keyway_width",
        between(plane(SHAFT, 1, -3.), plane(SHAFT, 1, 3.), "y", "plan"),
    );
    let p = planned(SHAFT, &s);
    c.want(
        "authored_view_kept",
        p.plan.views.iter().any(|v| v.id == "plan") && !has(&p.plan, "dimension_view_not_shown"),
    );
    c.finish(303);
}

#[test]
fn steer_304_keyway_section() {
    let mut s = input("inline-301");
    s["views"]["sections"] = json!([{"id":"A", "through":"f:section_recess:1"}]);
    let mut c = Checks::default();
    if let Some(p) = c.accepts(SHAFT, &s, "named_section", "invalid type") {
        c.want(
            "named_section",
            p.plan.views.iter().any(|v| v.cut.is_some()),
        );
        for role in ["pocket_width.length", "pocket_depth.length"] {
            c.want(
                role,
                p.plan.dimensions.iter().any(|d| {
                    d.placed.is_some()
                        && p.plan
                            .views
                            .iter()
                            .any(|v| v.id == d.view && v.cut.is_some())
                        && d.measurements
                            .iter()
                            .any(|m| m.feature == "f:section_recess:1" && m.parameter == role)
                }),
            );
        }
    }
    c.finish(304);
}

#[test]
fn steer_305_keyway_depth_wording() {
    let p = planned(SHAFT, &input("inline-301"));
    let d = dim(&p.plan, "d:f:section_recess:1/pocket_width.length").unwrap();
    assert!(
        d.measurements
            .iter()
            .any(|m| m.parameter.contains("depth") && (m.value - 3.).abs() < 1e-5)
    );
    let mut c = Checks::default();
    c.want("depth_is_not_a_limit", !d.label.contains("MAX"));
    c.finish(305);
}

/// Step 1's mutation guard reads the actual rendered marks, with no auto-planner filling
/// the removed statement back in. Step 2's implied oblique relations remain an xfail.
#[test]
fn steer_307_completeness_mutations() {
    let mut c = Checks::default();
    for (name, s, authored) in [
        (
            BRACKET,
            input("inline-293"),
            vec![
                "d:authored:base_length",
                "d:authored:flange_length",
                "d:authored:flange_thickness",
            ],
        ),
        (
            SHAFT,
            input("inline-301"),
            vec![
                "d:authored:keyway_start",
                "d:f:section_recess:1/pocket_width.length",
            ],
        ),
    ] {
        let p = planned(name, &s);
        let r = rendered(name, &p);
        let relevant = |f: &&LintIssue| {
            [
                "unrecognised_defining_geometry",
                "profile_corner_unlocated",
                "pocket_requirement_missing",
                "feature_not_located",
                "feature_not_dimensioned",
                "duplicate_measurement",
            ]
            .contains(&f.code.as_str())
        };
        c.want(
            &format!("{name}_defined"),
            r.lint.iter().filter(relevant).count() == 0,
        );
        let mut ids: Vec<String> = authored.into_iter().map(String::from).collect();
        if name == BRACKET {
            ids.extend(
                p.plan
                    .dimensions
                    .iter()
                    .filter(|d| d.kind == "angular" || d.id == "d:step_level/step_height.length.h8")
                    .map(|d| d.id.clone()),
            );
        }
        let marks: Vec<_> = r.drawings.iter().flat_map(lint::marks).collect();
        for id in ids {
            assert!(
                marks.iter().any(|m| m.name == id),
                "mutation must remove an actually drawn dimension: {id}"
            );
            let reduced: Vec<_> = marks.iter().filter(|m| m.name != id).cloned().collect();
            let before: BTreeSet<_> = r.lint.iter().map(|f| f.id.as_str()).collect();
            let findings = lint::check(&fixture(name).analysis, &reduced);
            let fresh: Vec<_> = findings
                .iter()
                .filter(|f| !before.contains(f.id.as_str()))
                .collect();
            c.want(
                &format!("remove_{id}"),
                fresh.len() == 1
                    && !fresh[0].subjects.is_empty()
                    && !fresh[0].requirements.is_empty(),
            );
        }
    }
    c.finish(307);
}

#[test]
fn steer_308_refusal_boundary() {
    let mut c = Checks::default();
    let mut s = input("inline-301");
    set_dim(
        &mut s,
        "d:f:section_recess:1/pocket_width.length",
        json!({"fit":"N9"}),
    );
    c.accepts(
        SHAFT,
        &s,
        "conventional_fit_draws",
        "not in draftwright's table",
    );
    let mut s = input("inline-293");
    s["features"] =
        json!({"f:hole:2":{"note":"LOCATED ALONG FLANGE", "note_satisfies":["location.length.z"]}});
    c.accepts(
        BRACKET,
        &s,
        "requested_role_draws",
        "not a measurement role",
    );
    // False/unmeasurable input must continue to be refused, independently of xfail.
    let mut bad = input("inline-301");
    set_dim(
        &mut bad,
        "d:f:section_recess:1/pocket_width.length",
        json!({"text":{"template":"6 × 40 × 3 DEEP"}}),
    );
    assert!(plan_result(SHAFT, &bad).err().unwrap().contains("{value}"));
    let mut bad = input("inline-293");
    bad["dimensions"]["d:authored:hole2_along_flange"]["along"] = json!("z");
    assert!(
        plan_result(BRACKET, &bad)
            .err()
            .unwrap()
            .contains("no unique z station")
    );
    c.finish(308);
}

#[test]
fn steer_309_decision_overrides() {
    let mut c = Checks::default();
    for (name, s) in [(BRACKET, input("inline-293")), (SHAFT, input("inline-301"))] {
        let p = planned(name, &s);
        // Inventory comes from public plan decisions, not a guessed list of internals.
        let choices = std::iter::once(("sheet".to_owned(), p.plan.sheet.basis.as_str()))
            .chain(
                p.plan
                    .views
                    .iter()
                    .map(|v| (format!("views/{}", v.id), v.basis.as_str())),
            )
            .chain(p.plan.dimensions.iter().map(|d| {
                (
                    format!("dimensions/{}", draftwright::items::key(&d.id)),
                    d.basis.as_str(),
                )
            }));
        for (path, basis) in choices {
            c.want(
                &format!("{name}_basis_names_override"),
                basis.contains(&path) || basis.contains("cannot override") || basis.contains("#"),
            );
        }
    }
    c.finish(309);
}

#[test]
fn steer_310_concentric_pattern() {
    let p = planned(FLANGE, &input(FLANGE));
    let r = rendered(FLANGE, &p);
    let mut c = Checks::default();
    for role in ["location.length.x", "location.length.y"] {
        c.want(
            role,
            !missing(
                r,
                "hole_pattern_requirement_missing",
                "f:hole_pattern:1",
                role,
            ),
        );
    }
    c.finish(310);
}

#[test]
fn steer_311_note_is_local() {
    let mut s = input(FLANGE);
    let before = planned(FLANGE, &s);
    s["features"] = json!({"f:hole_pattern:1":{"note":"X"}});
    let after = planned(FLANGE, &s);
    let mut c = Checks::default();
    for role in ["x", "y"] {
        let id = format!("d:f:hole:3/location.length.{role}");
        assert!(dim(&before.plan, &id).is_none());
        c.want(&id, dim(&after.plan, &id).is_none());
    }
    c.finish(311);
}

#[test]
fn steer_312_pattern_note_in_plan() {
    let mut s = input(FLANGE);
    s["features"] = json!({"f:hole_pattern:1":{"note":"X","note_satisfies":["location.length.x","location.length.y"]}});
    let before = planned(FLANGE, &s);
    let id = &before
        .plan
        .gdt
        .iter()
        .find(|g| g.kind == "label" && g.cells.iter().any(|t| t == "X"))
        .unwrap()
        .id;
    s["annotations"] = json!({id:{"place":{"view":"plan"}}});
    let mut c = Checks::default();
    if let Some(p) = c.accepts(FLANGE, &s, "note_in_plan", "cannot attach in the plan view") {
        c.want(
            "note_in_plan",
            p.plan
                .gdt
                .iter()
                .any(|g| &g.id == id && g.view == "plan" && g.placed.is_some()),
        );
        let r = rendered(FLANGE, &p);
        c.want(
            "note_satisfies_in_plan",
            !missing(
                r,
                "hole_pattern_requirement_missing",
                "f:hole_pattern:1",
                "",
            ),
        );
    }
    c.finish(312);
}

#[test]
fn steer_313_authored_diameter_sign() {
    let mut s = input(FLANGE);
    s["features"] = json!({"f:hole_pattern:1":{"note":"CONCENTRIC WITH ⌀80"}});
    let mut c = Checks::default();
    // Refusal of unsupported text is also allowed by the issue. Any other refusal fails.
    match plan_result(FLANGE, &s) {
        Err(e) => assert!(
            e.contains("⌀") && (e.contains("character") || e.contains("glyph")),
            "{e}"
        ),
        Ok(p) => {
            let r = rendered(FLANGE, &p);
            let runs: Vec<_> = r
                .drawings
                .iter()
                .flat_map(|d| &d.text)
                .filter(|t| t.text.contains("CONCENTRIC"))
                .collect();
            assert!(
                !runs.is_empty(),
                "the authored note must actually reach the drawing"
            );
            c.want(
                "note_has_supported_glyphs",
                runs.iter().all(|t| {
                    let data = std::fs::read(
                        Path::new(env!("CARGO_MANIFEST_DIR"))
                            .join("fonts")
                            .join(&t.font),
                    )
                    .unwrap();
                    let face = ttf_parser::Face::parse(&data, 0).unwrap();
                    t.text
                        .chars()
                        .all(|ch| ch.is_whitespace() || face.glyph_index(ch).is_some())
                }),
            );
        }
    }
    c.finish(313);
}

#[test]
fn steer_314_plain_hole_section() {
    let mut s = input(FLANGE);
    s["views"] = json!({"section_aa":{}});
    let p = planned(FLANGE, &s);
    let mut c = Checks::default();
    c.want(
        "explicit_section_drawn",
        p.plan
            .views
            .iter()
            .any(|v| v.id == "section_aa" && v.cut.is_some())
            && !has(&p.plan, "section_not_shown"),
    );
    c.finish(314);
}

#[test]
fn steer_315_broken_front() {
    let mut s = input(BAR);
    s["views"]["front"] = json!({"break":{}});
    let mut c = Checks::default();
    if let Some(p) = c.accepts(BAR, &s, "broken_view_accepted", "unknown field `break`") {
        c.want(
            "true_length",
            p.plan
                .dimensions
                .iter()
                .any(|d| (d.value - 600.).abs() < 1e-5 && d.placed.is_some()),
        );
        c.want("larger_scale", p.plan.sheet.scale_factor > 0.5);
        c.want(
            "broken_view_recorded",
            serde_json::to_value(&p.plan.views)
                .unwrap()
                .as_array()
                .unwrap()
                .iter()
                .any(|v| v.get("break").is_some()),
        );
    }
    c.finish(315);
}

#[test]
fn steer_316_authored_not_reference() {
    let mut s = input(BAR);
    let p = planned(BAR, &s);
    let mut c = Checks::default();
    c.want(
        "authored_locates",
        !missing(rendered(BAR, &p), "feature_not_located", "f:hole:2", ""),
    );
    s["dimensions"]
        .as_object_mut()
        .unwrap()
        .remove("d:authored:hole2_from_shoulder");
    set_dim(
        &mut s,
        "d:f:hole:2/location.length.x",
        json!({"style":"reference"}),
    );
    let p = planned(BAR, &s);
    c.want(
        "reference_does_not_locate",
        missing(rendered(BAR, &p), "feature_not_located", "f:hole:2", ""),
    );
    c.finish(316);
}

#[test]
fn steer_317_reference_covers_nothing() {
    let mut s = input(BAR);
    set_dim(
        &mut s,
        "d:envelope/width.length",
        json!({"style":"reference"}),
    );
    let p = planned(BAR, &s);
    let mut c = Checks::default();
    for value in [600., 30., 630.] {
        c.want(
            &format!("length_{value}"),
            p.plan.dimensions.iter().any(|d| {
                d.placed.is_some()
                    && (d.value - value).abs() < 1e-5
                    && d.reference == (value == 630.)
            }),
        );
    }
    c.finish(317);
}

#[test]
fn steer_318_smaller_isometric_fits() {
    let mut s = input(BAR);
    let before = planned(BAR, &s);
    assert!(!has(&before.plan, "page_fit_uncertain"));
    s["iso_scale_factor"] = json!(0.5);
    let p = planned(BAR, &s);
    let mut c = Checks::default();
    c.want("no_page_fit_uncertain", !has(&p.plan, "page_fit_uncertain"));
    c.want(
        "finished_items_on_sheet",
        !rendered(BAR, &p)
            .structural
            .iter()
            .any(|f| f.code.contains("off_sheet") || f.code.contains("out_of_bounds")),
    );
    c.finish(318);
}

#[test]
fn steer_319_table_does_not_locate_slot() {
    let mut s = input(PLATE);
    s.as_object_mut().unwrap().remove("dimensions");
    let p = planned(PLATE, &s);
    assert!(!p.plan.hole_tables.is_empty(), "reproducer needs its table");
    let r = rendered(PLATE, &p);
    let stated = dim(&p.plan, "d:f:slot:1/location_slot_across.length").is_some();
    let reported = r.lint.iter().any(|f| {
        f.requirements
            .iter()
            .any(|q| q.feature == "f:slot:1" && q.parameter == "location_slot_across.length")
    });
    let mut c = Checks::default();
    c.want("slot_across_stated_or_reported", stated || reported);
    c.finish(319);
}

#[test]
fn steer_320_slot_centre_reference() {
    let mut s = input(PLATE);
    set_dim(
        &mut s,
        "d:authored:c",
        json!({"between":["face:v1:e1167925cb17836ed722c27cd42791984c3f76ceed318488b1bf3052247a3c2e","axis:f:slot:1"],"along":"x","view":"plan"}),
    );
    let mut c = Checks::default();
    if let Some(p) = c.accepts(PLATE, &s, "slot_centre_accepted", "several cylinder axes") {
        c.want(
            "centre_is_110",
            dim(&p.plan, "d:authored:c").is_some_and(|d| (d.value - 110.).abs() < 1e-5),
        );
        c.want(
            "centre_credited",
            !missing(
                rendered(PLATE, &p),
                "slot_requirement_missing",
                "f:slot:1",
                "location_slot.length",
            ),
        );
    }
    c.finish(320);
}

#[test]
fn steer_321_explicit_table_fits() {
    let mut s = input(PLATE);
    s.as_object_mut().unwrap().remove("dimensions");
    s.as_object_mut().unwrap().remove("views");
    let p = planned(PLATE, &s);
    let mut c = Checks::default();
    let blockers_named = p
        .plan
        .problems
        .iter()
        .filter(|f| f.code == "hole_table_unavailable")
        .any(|f| {
            f.subjects.iter().chain(&f.refs).any(|id| {
                p.plan
                    .views
                    .iter()
                    .any(|v| id == &v.id || id == &format!("v:{}", v.id))
                    || p.plan.dimensions.iter().any(|d| &d.id == id)
            })
        });
    c.want(
        "table_or_named_blocker",
        !p.plan.hole_tables.is_empty() || blockers_named,
    );
    c.finish(321);
}

#[test]
fn steer_322_spatial_table_tags() {
    let p = planned(PLATE, &input(PLATE));
    assert!(!p.plan.hole_tables.is_empty());
    let mut families: BTreeMap<String, Vec<(usize, f64, f64)>> = BTreeMap::new();
    for row in p.plan.hole_tables.iter().flat_map(|t| &t.rows) {
        let split = row.tag.find(|ch: char| ch.is_ascii_digit()).unwrap();
        let (prefix, n) = row.tag.split_at(split);
        let x = row
            .measurements
            .iter()
            .find(|m| m.parameter == "location.length.x")
            .unwrap()
            .value;
        let y = row
            .measurements
            .iter()
            .find(|m| m.parameter == "location.length.y")
            .unwrap()
            .value;
        families
            .entry(prefix.into())
            .or_default()
            .push((n.parse().unwrap(), x, y));
    }
    assert!(families.values().any(|v| v.len() >= 10));
    let mut c = Checks::default();
    for rows in families.values_mut() {
        rows.sort_by(|a, b| a.2.total_cmp(&b.2).then_with(|| a.1.total_cmp(&b.1)));
        c.want(
            "tags_follow_rows_then_columns",
            rows.windows(2).all(|w| w[0].0 < w[1].0),
        );
    }
    c.finish(322);
}

#[test]
fn steer_323_axis_reference_guidance() {
    let mut s = input(PLATE);
    let key = s["dimensions"]["d:authored:slot_y"]["between"][1]
        .as_str()
        .unwrap()
        .strip_prefix("axis:")
        .unwrap()
        .to_string();
    s["dimensions"]["d:authored:slot_y"]["between"][1] = json!(format!("axis:face:{key}"));
    let e = plan_result(PLATE, &s).err().unwrap();
    assert!(e.contains("Unknown reference"), "{e}");
    let mut c = Checks::default();
    c.want(
        "copyable_axis_example",
        e.contains(&format!("axis:{key}"))
            || e.contains("axis:<key from Analysis.edge_ink.references>"),
    );
    s["dimensions"]["d:authored:slot_y"]["between"][1] = json!("axis:f:slot:1");
    let e = plan_result(PLATE, &s).err().unwrap();
    assert!(e.contains("several cylinder axes"), "{e}");
    c.want(
        "unambiguous_axis_instruction",
        e.contains("axis:<key from Analysis.edge_ink.references>") || e.contains("axis:face:v1:"),
    );
    c.finish(323);
}

#[test]
fn steer_324_spec_selects_part() {
    let mut s = input(PIVOT);
    let baseline = planned(PIVOT, &s);
    assert!(has(&baseline.plan, "assembly_principal_drawn"));
    let (_, a) =
        model::analyse_native_bytes_part(&filename(PIVOT), &bytes(PIVOT), Some(0)).unwrap();
    let explicit = api::plan_analysed(&a, &serde_json::from_value(s.clone()).unwrap()).unwrap();
    assert!(!has(&explicit, "assembly_principal_drawn"));
    s["part"] = json!(0);
    let mut c = Checks::default();
    if let Some(p) = c.accepts(PIVOT, &s, "part_field_accepted", "unknown field `part`") {
        c.want(
            "selection_acknowledged",
            !has(&p.plan, "assembly_principal_drawn"),
        );
        c.want(
            "selected_geometry",
            p.plan
                .dimensions
                .iter()
                .map(|d| (&d.id, d.value))
                .collect::<Vec<_>>()
                == explicit
                    .dimensions
                    .iter()
                    .map(|d| (&d.id, d.value))
                    .collect::<Vec<_>>(),
        );
    }
    c.finish(324);
}

fn relative_label(p: &Plan, id: &str) -> [f64; 4] {
    let d = dim(p, id).unwrap();
    let v = p.views.iter().find(|v| v.id == d.view).unwrap();
    let b = d.placed.as_ref().unwrap().label_box;
    [
        b[0] - v.at[0],
        b[1] - v.at[1],
        b[2] - v.at[0],
        b[3] - v.at[1],
    ]
}
fn close(a: [f64; 4], b: [f64; 4]) -> bool {
    a.iter().zip(b).all(|(x, y)| (x - y).abs() < 1e-6)
}
#[test]
fn steer_325_pin_respects_other_placement() {
    let mut s = input(PIVOT);
    let before = planned(PIVOT, &s);
    s["dimensions"]["d:f:hole:4/bore.diameter"]["place"]["offset"] = json!([-20, -14]);
    let after = planned(PIVOT, &s);
    let id = "d:f:hole_pattern:1/grid_pitch.length.col";
    let unchanged = close(
        relative_label(&before.plan, id),
        relative_label(&after.plan, id),
    );
    let reported = after.plan.problems.iter().any(|f| {
        f.subjects.iter().any(|s| s == id)
            && (f.message.contains("place") || f.message.contains("position"))
    });
    let mut c = Checks::default();
    c.want("placement_kept_or_explained", unchanged || reported);
    c.want(
        "basis_truthful",
        unchanged || !dim(&after.plan, id).unwrap().basis.contains("spec places"),
    );
    c.finish(325);
}

#[test]
fn steer_326_short_dimensions_inside() {
    let p = planned(CTC, &input(CTC));
    let ids = [
        "d:authored:ear_left",
        "d:f:slot:1/slot_length.length",
        "d:f:slot:2/location_slot.length",
        "d:f:hole_pattern:1/location.length.x",
    ];
    let ds: Vec<_> = ids.iter().map(|id| dim(&p.plan, id).unwrap()).collect();
    let mut c = Checks::default();
    let mut compared = 0;
    for (i, a) in ds.iter().enumerate() {
        for b in ds.iter().skip(i + 1) {
            let (a, b) = if a.value < b.value { (a, b) } else { (b, a) };
            if a.view != b.view {
                continue;
            }
            let pa = a.placed.as_ref().unwrap();
            let pb = b.placed.as_ref().unwrap();
            assert!(pa.lines.len() >= 3 && pb.lines.len() >= 3);
            let range = |d: &draftwright::dims::Placed| {
                let a = d.lines[0][0][0];
                let b = d.lines[1][0][0];
                [a.min(b), a.max(b)]
            };
            let (ra, rb) = (range(pa), range(pb));
            let v = p.plan.views.iter().find(|v| v.id == a.view).unwrap();
            let ay = (pa.label_box[1] + pa.label_box[3]) / 2. - v.at[1];
            let by = (pb.label_box[1] + pb.label_box[3]) / 2. - v.at[1];
            if ra[0] < rb[1] && rb[0] < ra[1] && ay.signum() == by.signum() {
                compared += 1;
                c.want("shorter_nearer_view", ay.abs() <= by.abs() + 1e-6);
            }
        }
    }
    assert!(
        compared > 0,
        "must compare overlapping dimensions on the same side"
    );
    c.finish(326);
}

#[test]
fn steer_327_unstated_outline_faces() {
    let p = planned(CTC, &minimal());
    let r = rendered(CTC, &p);
    let mut c = Checks::default();
    for (label, coordinate) in [("left_ear", -192.5), ("right_ear", 192.5)] {
        let key = plane(CTC, 0, coordinate);
        c.want(
            label,
            r.lint.iter().any(|f| {
                (f.subjects.iter().any(|s| s == &key)
                    || f.message.contains(&coordinate.to_string()))
                    && (f.code.contains("locat") || f.code.contains("defin"))
            }),
        );
    }
    c.finish(327);
}

#[test]
fn steer_328_slot_location_credit() {
    let s = input(CTC);
    let p = planned(CTC, &s);
    let mut c = Checks::default();
    for slot in ["f:slot:1", "f:slot:2"] {
        c.want(
            slot,
            !missing(
                rendered(CTC, &p),
                "feature_not_located",
                slot,
                "location.length.y",
            ),
        );
    }
    let mut authored = s;
    for (slot, coordinate) in [("f:slot:1", -25.), ("f:slot:2", -20.)] {
        let a = &fixture(CTC).analysis;
        // End-arc axes have a unique y station even though a slot has two x axes.
        let f = a.features.iter().find(|f| f.id == slot).unwrap();
        let _ = coordinate;
        let face = *f.faces.first().unwrap();
        let refs = a.dimension_references();
        // Find a real cylindrical reference on the slot's common y station.
        let key = refs.iter().find_map(|(key,rs)| rs.iter().any(|r| matches!(r,Reference::Cylinder { origin,.. } if (origin[1]-coordinate).abs()<1e-5)).then(||key.clone())).unwrap_or_else(||panic!("slot {slot}, face {face}: no cylinder"));
        set_dim(
            &mut authored,
            &format!("d:authored:{}_y", slot.replace(':', "_")),
            between(
                plane(CTC, 1, fixture(CTC).analysis.envelope[0][1]),
                format!("axis:{key}"),
                "y",
                "plan",
            ),
        );
    }
    let p = planned(CTC, &authored);
    for slot in ["f:slot:1", "f:slot:2"] {
        c.want(
            &format!("authored_{slot}"),
            !missing(
                rendered(CTC, &p),
                "feature_not_located",
                slot,
                "location.length.y",
            ),
        );
    }
    c.finish(328);
}

#[test]
fn steer_329_valueless_pmi() {
    let p = planned(CTC, &minimal());
    let r = rendered(CTC, &p);
    let a = &fixture(CTC).analysis;
    let draftwright::pmi::Reading::Read(report) = a.pmi.as_ref().unwrap() else {
        panic!("fixture PMI must read")
    };
    let records = report.records.as_slice();
    let mut c = Checks::default();
    for (id, value) in [(24, 85.), (25, 50.), (126, 35.), (127, 35.)] {
        let record = records.iter().find(|r| r.part21_id == id).unwrap();
        let unresolved = p
            .plan
            .problems
            .iter()
            .any(|f| f.code == "pmi_not_lowered" && f.refs.contains(&record.source_id))
            || r.lint
                .iter()
                .any(|f| f.code == "pmi_not_extracted" && f.sources.contains(&record.source_id));
        let attached = record
            .geometry
            .as_ref()
            .is_some_and(|g| !g.faces.is_empty());
        c.want(
            &format!("pmi_{id}"),
            !unresolved && attached && record.value == Some(value),
        );
    }
    c.finish(329);
}

#[test]
fn steer_330_report_gdt_moves() {
    let mut s = input(CTC);
    let after = planned(CTC, &s);
    s["dimensions"]
        .as_object_mut()
        .unwrap()
        .remove("d:envelope/notch_location_x.length.f97_e104_e108");
    let before = planned(CTC, &s);
    let mut c = Checks::default();
    assert!(!before.plan.gdt.is_empty());
    for g in &before.plan.gdt {
        let b = g.placed.as_ref().unwrap().label_box;
        let other = after
            .plan
            .gdt
            .iter()
            .find(|h| h.id == g.id)
            .unwrap()
            .placed
            .as_ref()
            .unwrap()
            .label_box;
        let reported = after.plan.problems.iter().any(|f| {
            f.subjects.contains(&g.id)
                && (f.message.contains("moved") || f.message.contains("position"))
        });
        c.want(
            "gdt_unchanged_or_move_reported",
            close(b, other) || reported,
        );
    }
    c.finish(330);
}

#[test]
fn steer_331_finished_sheet_fits() {
    let mut s = input(CTC);
    let after = planned(CTC, &s);
    s["dimensions"]
        .as_object_mut()
        .unwrap()
        .remove("d:envelope/notch_location_x.length.f97_e104_e108");
    let before = planned(CTC, &s);
    let mut c = Checks::default();
    c.want("baseline_fits", !has(&before.plan, "page_fit_uncertain"));
    c.want("placed_510_fits", !has(&after.plan, "page_fit_uncertain"));
    c.want(
        "finished_items_on_sheet",
        !rendered(CTC, &after)
            .structural
            .iter()
            .any(|f| f.code.contains("off_sheet") || f.code.contains("out_of_bounds")),
    );
    c.finish(331);
}
