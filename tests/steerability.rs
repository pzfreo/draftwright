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
    let mut value: Value =
        serde_json::from_slice(&std::fs::read(root().join(format!("{name}.spec.json"))).unwrap())
            .unwrap();
    if name == CTC {
        // Main now recognises the two notches. Keep the issue's authored statements,
        // explicitly omitting only their newly generated coincident owners (K6).
        for id in [
            "d:f:rectangular_through_notch:1/notch_depth.length",
            "d:f:rectangular_through_notch:2/notch_depth.length",
            "d:f:rectangular_through_notch:2/location.length.x",
            "d:f:rectangular_through_notch:1/notch_width.length",
            "d:f:rectangular_through_notch:2/notch_width.length",
        ] {
            value["dimensions"][id] = json!({"omit":true});
        }
    }
    value
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
    case: String,
    group: String,
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
        for (id, e) in &expected {
            assert!(
                (293..=331).contains(id)
                    && *id != 306
                    && !e.reason.is_empty()
                    && !e.checks.is_empty(),
                "invalid expected-failure entry #{id}"
            );
        }
        if let Some(e) = expected.get(&issue) {
            assert_eq!(
                Some(e.case.as_str()),
                std::thread::current().name(),
                "expected entry names the case it governs"
            );
            assert!(["A", "B", "C", "D", "E", "F"].contains(&e.group.as_str()));
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
    let p = planned(BRACKET, &s);
    assert!(has(&p.plan, "duplicate_measurement"));
    assert!(dim(&p.plan, "d:authored:hole2_y_duplicate").is_some());
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
    for p in [&explicit, &auto] {
        assert!(annotations_in_bounds(rendered(BRACKET, p)));
        assert!(!has(&p.plan, "page_fit_uncertain"));
        assert!(
            !rendered(BRACKET, p)
                .structural
                .iter()
                .any(|f| f.code == "annotation_overlap")
        );
    }
    let rejected: Vec<_> = auto
        .plan
        .sheet
        .basis
        .split(';')
        .filter(|entry| entry.contains("hard layout finding"))
        .collect();
    assert!(
        !rejected.is_empty(),
        "oversized candidates must be rejected"
    );
    for entry in rejected {
        assert!(
            entry.contains(" [") && entry.contains(": ") && entry.contains(']'),
            "hard candidate findings must be named: {entry}"
        );
    }
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
    let a = &fixture(BRACKET).analysis;
    // Independently locate the issue's foot and far stock face by fixture geometry;
    // discovering them by their role is precisely the agent-facing behavior under test.
    let bottom = a
        .dimension_reference(&plane(BRACKET, 2, a.envelope[0][2]))
        .unwrap();
    let far_side = a
        .dimension_reference(&plane(BRACKET, 0, a.envelope[0][0]))
        .unwrap();
    let foot_edges: Vec<_> = bottom
        .membership()
        .edges
        .as_ref()
        .unwrap()
        .iter()
        .filter_map(|edge| edge.reference.as_ref())
        .map(|key| a.dimension_reference(key).unwrap())
        .collect();
    let plate = "f:plate:1";
    c.want(
        "reference_membership",
        bottom.membership().features.iter().any(|id| id == plate)
            && bottom
                .membership()
                .stock_sides
                .iter()
                .any(|side| side == "bottom")
            && far_side
                .membership()
                .stock_sides
                .iter()
                .any(|side| side == "left")
            && !foot_edges.is_empty()
            && foot_edges.iter().all(|r| {
                r.membership().features.iter().any(|id| id == plate)
                    && r.membership()
                        .stock_sides
                        .iter()
                        .any(|side| side == "bottom")
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
    let f = fixture(BRACKET);
    for (key, records) in f.analysis.dimension_references() {
        for r in records {
            let m = r.membership();
            assert!(!m.faces.is_empty(), "{key} needs incident topology");
            let expected: BTreeSet<_> = f
                .analysis
                .features
                .iter()
                .filter(|feature| feature.faces.iter().any(|i| m.faces.contains(i)))
                .map(|feature| feature.id.clone())
                .collect();
            assert_eq!(
                m.features.iter().cloned().collect::<BTreeSet<_>>(),
                expected
            );
            assert_eq!(
                m.stock_faces,
                m.faces
                    .iter()
                    .filter(|i| f.analysis.stock_faces.contains(i))
                    .copied()
                    .collect::<Vec<_>>()
            );
            for side in &m.stock_sides {
                assert!(
                    ["bottom", "top", "left", "right", "front", "back"].contains(&side.as_str())
                );
                assert!(!m.stock_faces.is_empty());
            }
            if key.starts_with("face:") {
                let face = m.faces[0];
                let expected: BTreeSet<_> = f.part.faces[face]
                    .loops
                    .iter()
                    .flat_map(|l| &l.edges)
                    .map(|(e, _)| *e)
                    .collect();
                let edges = m.edges.as_ref().unwrap();
                assert_eq!(
                    edges.iter().map(|e| e.index).collect::<BTreeSet<_>>(),
                    expected
                );
                for e in edges {
                    if let Some(key) = &e.reference {
                        assert!(f.analysis.dimension_references().contains_key(key));
                    }
                    for key in &e.vertices {
                        assert!(f.analysis.dimension_references().contains_key(key));
                    }
                }
                assert!(
                    m.vertices
                        .as_ref()
                        .unwrap()
                        .iter()
                        .all(|key| f.analysis.dimension_references().contains_key(key))
                );
            }
        }
    }
    let restored: Analysis =
        serde_json::from_value(serde_json::to_value(&f.analysis).unwrap()).unwrap();
    assert_eq!(
        restored.dimension_references(),
        f.analysis.dimension_references()
    );
    c.finish(297);
}

/// Exercise the release workflow's actual package recipe, locally, without any upload step.
/// Inspecting archive member names checks the public deliverable rather than workflow text.
fn release_members() -> BTreeSet<String> {
    struct Scratch(PathBuf);
    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }
    let repo = Path::new(env!("CARGO_MANIFEST_DIR"));
    let dir =
        Scratch(std::env::temp_dir().join(format!("dw-steer-release-{}", std::process::id())));
    std::fs::create_dir(&dir.0).unwrap();
    fn copy_tree(from: &Path, to: &Path) {
        if from.is_dir() {
            std::fs::create_dir(to).unwrap();
            for entry in std::fs::read_dir(from).unwrap() {
                let entry = entry.unwrap();
                copy_tree(&entry.path(), &to.join(entry.file_name()));
            }
        } else {
            std::fs::copy(from, to).unwrap();
        }
    }
    for name in ["LICENSE", "README.md", "schemas", "docs"] {
        copy_tree(&repo.join(name), &dir.0.join(name));
    }
    let bin = dir.0.join("target/fixture-native/release");
    std::fs::create_dir_all(&bin).unwrap();
    let suffix = std::env::consts::EXE_SUFFIX;
    for (name, path) in [
        ("draftwright", env!("CARGO_BIN_EXE_draftwright")),
        ("draftwright-mcp", env!("CARGO_BIN_EXE_draftwright-mcp")),
    ] {
        std::fs::copy(path, bin.join(format!("{name}{suffix}"))).unwrap();
    }
    let workflow = std::fs::read_to_string(repo.join(".github/workflows/cli.yml")).unwrap();
    let block = workflow
        .split("      - name: Package and checksum\n")
        .nth(1)
        .expect("release packaging step must still be exercised");
    let script = block
        .split("        run: |\n")
        .nth(1)
        .unwrap()
        .lines()
        .take_while(|line| line.starts_with("          ") || line.is_empty())
        .map(|line| line.strip_prefix("          ").unwrap_or(line))
        .collect::<Vec<_>>()
        .join("\n")
        .replace("${{ matrix.target }}", "fixture-native")
        .replace("${{ matrix.exe }}", suffix)
        // Exercise the portable tar payload on each host; the Windows zip has the same staging directory.
        .replace("${{ runner.os }}", "Linux");
    let output = std::process::Command::new("bash")
        .args(["-euc", &script])
        .env("RELEASE_TAG", "fixture")
        .current_dir(&dir.0)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "package recipe: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let archive = dir.0.join("dist/draftwright-fixture-fixture-native.tar.gz");
    let output = std::process::Command::new("tar")
        .arg("-tzf")
        .arg(archive)
        .output()
        .unwrap();
    assert!(output.status.success(), "archive must be readable");
    String::from_utf8(output.stdout)
        .unwrap()
        .lines()
        .map(String::from)
        .collect()
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
    if let Some(offset) = d.callout_offset {
        let (view, tip) = p.plan.automatic_callout_tip(&d.id).unwrap();
        assert_eq!(view, d.view);
        let bend = d.placed.as_ref().unwrap().lines[0][1];
        assert_eq!(offset, [bend[0] - tip[0], bend[1] - tip[1]]);
        let mut pinned = input("inline-293");
        pinned["dimensions"][&d.id]["place"] = json!({"offset":offset,"pinned":true});
        let replay = planned(BRACKET, &pinned);
        let replay = dim(&replay.plan, &d.id).unwrap();
        assert_eq!(replay.placed.as_ref().unwrap().lines[0][1], bend);
        assert_eq!(replay.callout_offset, Some(offset));
    }
    let schema = api::call("spec_schema", &json!({})).unwrap();
    assert_eq!(
        schema["spec"]["$schema"],
        "https://json-schema.org/draft/2020-12/schema"
    );
    let mut service = api::Service::default();
    assert_eq!(
        service
            .call(
                "spec_schema",
                &json!({"id":1,"op":"spec_schema","arguments":{}})
            )
            .unwrap(),
        schema
    );
    let cli = std::process::Command::new(env!("CARGO_BIN_EXE_draftwright"))
        .arg("spec_schema")
        .output()
        .unwrap();
    assert!(cli.status.success());
    assert_eq!(
        serde_json::from_slice::<Value>(&cli.stdout).unwrap(),
        schema
    );
    let members = release_members();
    for path in [
        "draftwright-mcp",
        "schemas/spec.json",
        "docs/model.md",
        "docs/mcp.md",
        "docs/rules/README.md",
    ] {
        let path = if path == "draftwright-mcp" {
            format!("{path}{}", std::env::consts::EXE_SUFFIX)
        } else {
            path.into()
        };
        c.want(
            &format!("archive_{path}"),
            members
                .iter()
                .any(|name| name.ends_with(&format!("/{path}"))),
        );
    }
    // Check the actual archive payload, not just the positive staging recipe.
    let docs: BTreeSet<_> = members
        .iter()
        .filter_map(|name| name.split_once("/docs/"))
        .map(|(_, path)| path)
        .filter(|path| !path.is_empty() && !path.ends_with('/'))
        .collect();
    assert!(
        !docs.contains("next-session.md"),
        "internal working notes must not ship"
    );
    assert!(
        docs.iter()
            .all(|path| matches!(*path, "model.md" | "mcp.md") || path.starts_with("rules/")),
        "release documentation must be allowlisted: {docs:?}"
    );
    assert!(
        include_str!("../docs/model.md").contains("](rules/README.md)"),
        "the rules catalogue ships because the agent guide links to it"
    );
    c.finish(298);
}

fn detail_refusal_names_condition(message: &str) -> bool {
    let text = message.to_lowercase();
    let source = text.contains("source")
        && [
            "missing",
            "absent",
            "omitted",
            "not shown",
            "not drawn",
            "does not exist",
            "doesn't exist",
            "unavailable",
            "no source",
        ]
        .iter()
        .any(|word| text.contains(word));
    let space = ["crop", "room", "space", "fit", "layout", "sheet"]
        .iter()
        .any(|word| text.contains(word))
        && [
            "too large",
            "too big",
            "no room",
            "not enough",
            "insufficient",
            "does not fit",
            "doesn't fit",
            "cannot fit",
            "can't fit",
            "won't fit",
            "no reserved",
            "no clear",
            "no space",
            "cannot be placed",
        ]
        .iter()
        .any(|word| text.contains(word));
    // A minimal fix may simply say "needs room for its crop" or "requires its
    // source view". Those name one condition; the old conjunction names neither.
    let demand = text.contains("needs") || text.contains("requires");
    let mentions_source = text.contains("source");
    let mentions_space = ["crop", "room", "space", "fit", "layout", "sheet"]
        .iter()
        .any(|word| text.contains(word));
    source || space || (demand && mentions_source != mentions_space)
}

fn check_detail(name: &str, spec: &Value, c: &mut Checks, check: &str) {
    match plan_result(name, spec) {
        Err(message) => {
            assert!(message.contains("detail_a"), "unrelated refusal: {message}");
            if message.contains("needs its source view and room for its crop") {
                c.want(check, false);
            } else {
                assert!(
                    detail_refusal_names_condition(&message),
                    "detail refusal must name the failed condition: {message}"
                );
            }
        }
        Ok(p) => {
            let drawn = p
                .plan
                .views
                .iter()
                .any(|v| v.id == "v:detail_a" && v.detail.is_some());
            let reported = p.plan.problems.iter().any(|f| {
                (f.code.contains("detail") || f.code.contains("layout"))
                    && f.subjects
                        .iter()
                        .chain(&f.refs)
                        .any(|id| id == "v:detail_a" || id == "detail_a")
            });
            c.want(check, drawn || reported);
        }
    }
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
        !rendered(BRACKET, &p).structural.iter().any(|f| {
            f.code == "nominal_rounded"
                && (f.message.contains("38.6")
                    || f.annotation.as_deref() == Some("d:envelope/height.length"))
        }),
    );
    let mut detail = s;
    detail["views"] =
        json!({"detail_a":{"detail":{"around":"f:hole:2","from":"side","scale_factor":2}}});
    check_detail(BRACKET, &detail, &mut c, "detail_reason");
    // Exercise a real, already-specific source refusal and an accepted roomier crop.
    // These are controls for the acceptance criterion, not more expected failures.
    let mut controls = Checks::default();
    let mut source_missing = detail.clone();
    source_missing["views"]["detail_a"]["detail"]["from"] = json!("section_aa");
    check_detail(
        BRACKET,
        &source_missing,
        &mut controls,
        "specific_source_refusal",
    );
    detail["sheet"]["size"] = json!("A2");
    check_detail(BRACKET, &detail, &mut controls, "accepted_detail");
    assert!(
        controls.0.is_empty(),
        "valid detail outcomes must pass: {:?}",
        controls.0
    );
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
    let mut unlocated = input("inline-301");
    unlocated["dimensions"]
        .as_object_mut()
        .unwrap()
        .remove("d:authored:keyway_start");
    let unlocated = planned(SHAFT, &unlocated);
    assert!(
        missing(
            rendered(SHAFT, &unlocated),
            "pocket_requirement_missing",
            "f:section_recess:1",
            "location_pocket.location.x"
        ),
        "an unstated pocket location must still be reported"
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
    c.want(
        "centred_keyway_located",
        !missing(
            rendered(SHAFT, &p),
            "pocket_requirement_missing",
            "f:section_recess:1",
            "location_pocket.location.y",
        ),
    );
    s["origin"] = json!({"y":"axis:f:turned_step:2"});
    if let Some(p) = c.accepts(
        SHAFT,
        &s,
        "axis_origin",
        "must identify min, max or one plane normal to y",
    ) {
        c.want(
            "centred_keyway_located",
            !missing(
                rendered(SHAFT, &p),
                "pocket_requirement_missing",
                "f:section_recess:1",
                "location_pocket.location.y",
            ),
        );
    }
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
        p.plan.views.iter().any(|v| v.id == "v:plan") && !has(&p.plan, "dimension_view_not_shown"),
    );
    c.finish(303);
}

#[test]
fn steer_304_keyway_section() {
    let mut s = input("inline-301");
    let _baseline = planned(SHAFT, &s);
    let schema: Value = serde_json::from_str(include_str!("../schemas/spec.json")).unwrap();
    draftwright::mcp::validate(&schema, &s).expect("the unmodified Spec is valid");
    s["views"]["sections"] = json!([{"id":"A", "through":"f:section_recess:1"}]);
    let mut c = Checks::default();
    let p = match serde_json::from_value::<Spec>(s.clone()) {
        Ok(_) => Some(planned(SHAFT, &s)),
        Err(error) => {
            let path = draftwright::mcp::validate(&schema, &s).unwrap_err();
            assert!(
                path.starts_with("input/views/sections:"),
                "unexpected field refused: {path}; {error}"
            );
            eprintln!("REFUSAL views/sections: {error}; {path}");
            c.want("named_section", false);
            None
        }
    };
    if let Some(p) = p {
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
    for (name, s) in [(BRACKET, input(BRACKET)), (SHAFT, input(SHAFT))] {
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
        let ids: Vec<_> = p
            .plan
            .dimensions
            .iter()
            .filter(|d| !d.reference && d.placed.is_some())
            .map(|d| d.id.clone())
            .collect();
        assert!(!ids.is_empty());
        let marks: Vec<_> = r.drawings.iter().flat_map(lint::marks).collect();
        for id in ids {
            assert!(
                marks.iter().any(|m| m.name == id),
                "mutation must remove an actually drawn dimension: {id}"
            );
            let reduced: Vec<_> = marks.iter().filter(|m| m.name != id).cloned().collect();
            let baseline = lint::check(&fixture(name).analysis, &marks);
            let before: BTreeSet<_> = baseline.iter().map(|f| f.id.as_str()).collect();
            let findings = lint::check(&fixture(name).analysis, &reduced);
            let fresh: Vec<_> = findings
                .iter()
                .filter(|f| !before.contains(f.id.as_str()))
                .collect();
            c.want(
                &format!("remove_{id}"),
                fresh.len() == 1
                    && marks
                        .iter()
                        .filter(|m| m.name == id)
                        .flat_map(|m| &m.claims)
                        .any(|claim| {
                            fresh[0].subjects.contains(&claim.feature)
                                || fresh[0]
                                    .requirements
                                    .iter()
                                    .any(|q| q.feature == claim.feature)
                        }),
            );
        }
    }
    let mut redundant = input(BRACKET);
    set_dim(&mut redundant, "d:envelope/width.length", json!({}));
    let p = planned(BRACKET, &redundant);
    c.want(
        "controlling_overall_is_redundant",
        rendered(BRACKET, &p).lint.iter().any(|f| {
            ["duplicate_measurement", "closed_dimension_chain"].contains(&f.code.as_str())
                && (!f.related.is_empty() || f.subjects.len() > 1)
        }),
    );
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
    let mut axis = input("inline-301");
    axis["origin"] = json!({"y":"axis:f:turned_step:2"});
    c.accepts(
        SHAFT,
        &axis,
        "axis_datum_draws",
        "must identify min, max or one plane normal to y",
    );
    let mut detail = input("inline-301");
    detail["views"]["detail_a"] =
        json!({"detail":{"around":"f:section_recess:1","from":"side","scale_factor":2}});
    check_detail(SHAFT, &detail, &mut c, "small_detail_reports_layout");
    let mut unknown = input("inline-301");
    unknown["dimensions"]["d:authored:keyway_start"]["between"][0] = json!("face:v1:missing");
    assert!(
        plan_result(SHAFT, &unknown)
            .err()
            .unwrap()
            .contains("Unknown reference")
    );
    let mut typed = input("inline-301");
    typed["dimensions"]["d:authored:keyway_start"]["value"] = json!(40);
    assert!(
        plan_result(SHAFT, &typed)
            .err()
            .unwrap()
            .contains("unknown field `value`")
    );
    let mut zero = input("inline-293");
    zero["dimensions"]["d:authored:hole2_along_flange"]["between"][1] =
        json!(plane(BRACKET, 2, 0.));
    assert!(
        plan_result(BRACKET, &zero)
            .err()
            .unwrap()
            .contains("no nonzero true distance")
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
            .chain(p.plan.views.iter().map(|v| {
                (
                    format!("views/{}", v.id.trim_start_matches("v:")),
                    v.basis.as_str(),
                )
            }))
            .chain(p.plan.dimensions.iter().map(|d| {
                (
                    format!("dimensions/{}", draftwright::items::key(&d.id)),
                    d.basis.as_str(),
                )
            }));
        for (path, basis) in choices {
            c.want(
                &format!("{name}_basis_names_override"),
                basis.contains(&path)
                    || [
                        "sheet/",
                        "views/",
                        "dimensions/",
                        "features/",
                        "origin/",
                        "dimension_schemes/",
                    ]
                    .iter()
                    .any(|prefix| basis.contains(prefix))
                    || basis.contains("cannot override")
                    || basis.contains('#'),
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
    // An unannotated rectangular CTC grid has no round-outline axis to locate it.
    // The same location-missing code must survive this negative control.
    let unlocated = lint::check(&fixture(CTC).analysis, &[]);
    for role in ["location.length.x", "location.length.y"] {
        assert!(
            unlocated
                .iter()
                .any(|f| f.code == "hole_pattern_requirement_missing"
                    && f.requirements
                        .iter()
                        .any(|q| q.feature == "f:hole_pattern:1" && q.parameter == role)),
            "an unstated off-axis grid location must still be reported: {role}"
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
                .any(|g| &g.id == id && g.view == "v:plan" && g.placed.is_some()),
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
                    let font = Font::new(data).unwrap();
                    t.text
                        .chars()
                        .all(|ch| ch.is_whitespace() || font.supports(ch))
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
            .any(|v| v.id == "v:section_aa" && v.cut.is_some())
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
        // Compare automatic choices: the issue's explicit 1:2 is still author intent.
        let mut automatic = s.clone();
        automatic["sheet"].as_object_mut().unwrap().remove("size");
        automatic["sheet"].as_object_mut().unwrap().remove("scale");
        let broken = planned(BAR, &automatic);
        automatic["views"]["front"]
            .as_object_mut()
            .unwrap()
            .remove("break");
        let whole = planned(BAR, &automatic);
        c.want(
            "larger_scale",
            broken.plan.sheet.scale_factor > whole.plan.sheet.scale_factor,
        );
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
        !missing(
            rendered(BAR, &p),
            "feature_not_located",
            "f:hole:2",
            "location.length.x",
        ),
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
        missing(
            rendered(BAR, &p),
            "feature_not_located",
            "f:hole:2",
            "location.length.x",
        ),
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
                    && d.kind == "linear"
                    && (d.value - value).abs() < 1e-5
                    && d.reference == (value == 630.)
                    && (value != 30. || d.id == "d:f:turned_step:2/step.length")
                    && (value != 630. || d.id == "d:envelope/width.length")
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
    let stated = p.plan.dimensions.iter().any(|d| {
        d.kind == "linear"
            && d.placed.is_some()
            && p.plan.views.iter().any(|v| v.id == d.view)
            && d.measurements
                .iter()
                .any(|m| m.feature == "f:slot:1" && m.parameter == "location_slot_across.length")
    });
    let reported = r.lint.iter().any(|f| {
        ["feature_not_located", "slot_requirement_missing"].contains(&f.code.as_str())
            && f.requirements.iter().any(|q| {
                q.feature == "f:slot:1"
                    && ["location_slot_across.length", "location.length.y"]
                        .contains(&q.parameter.as_str())
            })
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
    // #320 gives a slot a unique centre. An actual compound of two holes still
    // has several axes and must retain the same copyable guidance.
    let mut analysis = fixture(PLATE).analysis.clone();
    let mut holes = analysis.features.iter().filter(|f| f.kind == "hole");
    let mut compound = holes.next().unwrap().clone();
    compound
        .faces
        .extend_from_slice(&holes.next().unwrap().faces);
    compound.id = "f:compound:guidance".into();
    analysis.features.push(compound);
    s["dimensions"]["d:authored:slot_y"]["between"][1] = json!("axis:f:compound:guidance");
    let e = api::plan_analysed(&analysis, &serde_json::from_value(s).unwrap())
        .err()
        .unwrap()
        .to_string();
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
    let b = relative_label(&before.plan, id);
    let a = relative_label(&after.plan, id);
    let unchanged = ((a[0] + a[2]) - (b[0] + b[2])).abs() < 1e-6
        && (a[1] + a[3]).signum() == (b[1] + b[3]).signum();
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

fn extension_crossings(p: &Plan, view: &str) -> usize {
    let dims: Vec<_> = p
        .dimensions
        .iter()
        .filter(|d| {
            d.view == view
                && d.kind == "linear"
                && d.placed.as_ref().is_some_and(|q| q.lines.len() >= 3)
        })
        .collect();
    let side = |a: [f64; 2], b: [f64; 2], c: [f64; 2]| {
        (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    };
    let mut count = 0;
    for a in &dims {
        for b in &dims {
            if a.id == b.id {
                continue;
            }
            for [p, q] in &a.placed.as_ref().unwrap().lines[..2] {
                let [r, s] = b.placed.as_ref().unwrap().lines[2];
                if side(*p, *q, r) * side(*p, *q, s) < -1e-8
                    && side(r, s, *p) * side(r, s, *q) < -1e-8
                {
                    count += 1;
                }
            }
        }
    }
    count
}

struct StackDimension<'a> {
    dimension: &'a PlannedDimension,
    axis: usize,
    span: [f64; 2],
    offset: f64,
}

fn linear_stack<'a>(p: &'a Plan, view: &str) -> Vec<StackDimension<'a>> {
    let v = p.views.iter().find(|v| v.id == view).unwrap();
    p.dimensions
        .iter()
        .filter_map(|d| {
            if d.kind != "linear" || d.view != view {
                return None;
            }
            let placed = d.placed.as_ref()?;
            if placed.lines.len() < 3 {
                return None;
            }
            let [a, b] = placed.lines[2];
            let axis = if (a[1] - b[1]).abs() < 1e-6 && (a[0] - b[0]).abs() > 1e-6 {
                0
            } else if (a[0] - b[0]).abs() < 1e-6 && (a[1] - b[1]).abs() > 1e-6 {
                1
            } else {
                return None;
            };
            let x = placed.lines[0][0][axis];
            let y = placed.lines[1][0][axis];
            Some(StackDimension {
                dimension: d,
                axis,
                span: [x.min(y), x.max(y)],
                offset: a[1 - axis] - v.at[1 - axis],
            })
        })
        .collect()
}

#[test]
fn steer_326_short_dimensions_inside() {
    let p = planned(CTC, &input(CTC));
    let stack = linear_stack(&p.plan, "v:plan");
    assert!(
        stack
            .iter()
            .any(|d| d.dimension.id.starts_with("d:authored:"))
    );
    assert!(
        stack
            .iter()
            .any(|d| !d.dimension.id.starts_with("d:authored:"))
    );
    let mut c = Checks::default();
    for (axis, check) in [(0, "horizontal_nesting"), (1, "vertical_nesting")] {
        let mut compared = 0;
        let mut violations = Vec::new();
        for (i, a) in stack.iter().enumerate().filter(|(_, d)| d.axis == axis) {
            for b in stack.iter().skip(i + 1).filter(|d| d.axis == axis) {
                let alen = a.span[1] - a.span[0];
                let blen = b.span[1] - b.span[0];
                if a.offset * b.offset <= 0.
                    || a.span[1].min(b.span[1]) - a.span[0].max(b.span[0]) <= 1e-6
                    || (alen - blen).abs() <= 1e-6
                {
                    continue;
                }
                let (short, long) = if alen < blen { (a, b) } else { (b, a) };
                compared += 1;
                if short.offset.abs() > long.offset.abs() + 1e-6 {
                    violations.push(format!(
                        "{} ({}) outside {} ({})",
                        short.dimension.id,
                        short.dimension.value,
                        long.dimension.id,
                        long.dimension.value
                    ));
                }
            }
        }
        assert!(
            compared > 0,
            "must compare overlapping dimensions on both axes"
        );
        eprintln!("{check}: {} violations: {violations:?}", violations.len());
        c.want(check, violations.is_empty());
    }
    let mut baseline = input(CTC);
    baseline.as_object_mut().unwrap().remove("dimensions");
    let baseline = planned(CTC, &baseline);
    let (before, after) = (
        extension_crossings(&baseline.plan, "v:plan"),
        extension_crossings(&p.plan, "v:plan"),
    );
    eprintln!("extension crossings: baseline {before}, authored {after}");
    c.want("extension_crossings_do_not_increase", after <= before);
    c.finish(326);
}

#[test]
fn steer_327_unstated_outline_faces() {
    // Main recognises/dimensions the notches; the schema-only drawing still
    // neither states nor reports the inner ear faces at x = +/-192.5.
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
    // Unlike the issue, main credits both slot y locations despite its extra
    // notch dimensions. The authored variant must also print the 205 and 200.
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
    // The two straight slot walls are 205 and 200 from the front face.
    // Together with the printed widths they fix the common centre line.
    for (slot, coordinate) in [("f:slot:1", -20.), ("f:slot:2", -25.)] {
        set_dim(
            &mut authored,
            &format!("d:authored:{}_y", slot.replace(':', "_")),
            between(
                plane(CTC, 1, fixture(CTC).analysis.envelope[0][1]),
                plane(CTC, 1, coordinate),
                "y",
                "plan",
            ),
        );
    }
    let p = planned(CTC, &authored);
    for (slot, value) in [("f:slot:1", 205.), ("f:slot:2", 200.)] {
        let id = format!("d:authored:{}_y", slot.replace(':', "_"));
        c.want(
            &format!("placed_{slot}"),
            dim(&p.plan, &id).is_some_and(|d| (d.value - value).abs() < 1e-5),
        );
    }
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
        let attached = !record.faces.is_empty();
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

fn annotations_in_bounds(r: &Rendered) -> bool {
    !r.structural
        .iter()
        .any(|f| f.code.contains("off_sheet") || f.code.contains("out_of_bounds"))
}

#[test]
fn steer_331_finished_sheet_fits() {
    let mut s = input(CTC);
    let original = planned(CTC, &s);
    s["dimensions"]
        .as_object_mut()
        .unwrap()
        .remove("d:envelope/notch_location_x.length.f97_e104_e108");
    let original_baseline = planned(CTC, &s);
    let mut c = Checks::default();
    // Main's additional generated dimensions have changed the issue's premise:
    // the 650 really overflows by 3.1 mm with the 510 placed, 0.1 mm without it.
    c.want(
        "annotations_in_bounds",
        annotations_in_bounds(rendered(CTC, &original))
            && annotations_in_bounds(rendered(CTC, &original_baseline)),
    );
    for p in [&original, &original_baseline] {
        assert!(
            has(&p.plan, "page_fit_uncertain"),
            "the original 650 overflow must retain its page-fit warning"
        );
    }

    // Keep every dimension and the original A3 1:5. Moving only the 650 below
    // restores the false-warning premise, paired with/without the 510 placement.
    let mut fitting = input(CTC);
    fitting["dimensions"]["d:f:hole_pattern:2/grid_pitch.length.col"] =
        json!({"place":{"side":"below","lane":0}});
    let after = planned(CTC, &fitting);
    fitting["dimensions"]
        .as_object_mut()
        .unwrap()
        .remove("d:envelope/notch_location_x.length.f97_e104_e108");
    let before = planned(CTC, &fitting);
    for p in [&before, &after] {
        assert!(
            annotations_in_bounds(rendered(CTC, p)),
            "the false-warning control must actually fit"
        );
        for (id, value) in [
            ("d:f:hole_pattern:2/grid_pitch.length.col", 650.),
            ("d:envelope/notch_location_x.length.f97_e104_e108", 510.),
        ] {
            assert!(
                dim(&p.plan, id).is_some_and(|d| (d.value - value).abs() < 1e-5),
                "the fitting variant must retain {value}"
            );
        }
    }
    c.want(
        "fitting_baseline_no_warning",
        !has(&before.plan, "page_fit_uncertain"),
    );
    c.want(
        "fitting_variant_no_warning",
        !has(&after.plan, "page_fit_uncertain"),
    );
    c.finish(331);
}

#[test]
fn concentric_pattern_requires_live_round_outline_pitch_circle_and_centre() {
    let a = &fixture(FLANGE).analysis;
    let p = planned(FLANGE, &input(FLANGE));
    let original = &rendered(FLANGE, &p).drawings[0];
    let reported = |drawing: &Drawing| {
        lint::lint(a, drawing).iter().any(|i| {
            i.code == "hole_pattern_requirement_missing"
                && i.requirements.iter().any(|r| {
                    r.feature == "f:hole_pattern:1" && r.parameter.starts_with("location.length.")
                })
        })
    };
    assert!(!reported(original));
    let mut drawing = original.clone();
    drawing.sheet.centre_marks.clear();
    assert!(
        reported(&drawing),
        "model concentricity alone does not locate"
    );
    let mut drawing = original.clone();
    drawing
        .sheet
        .items
        .retain(|i| !i.name.ends_with(":f:hole_pattern:1"));
    assert!(
        reported(&drawing),
        "a centre mark must identify this pattern"
    );
    let mut drawing = original.clone();
    for view in &mut drawing.sheet.views {
        view.edges.clear();
    }
    assert!(reported(&drawing), "the round outline must be visible");
    let mut offset = a.clone();
    let pattern = offset
        .recognition
        .as_mut()
        .unwrap()
        .patterns
        .iter_mut()
        .find(|p| p.id == "f:hole_pattern:1")
        .unwrap();
    pattern
        .parameters
        .insert("center".into(), json!([1., 0., 0.]));
    assert!(
        lint::lint(&offset, original).iter().any(|i| {
            i.code == "hole_pattern_requirement_missing"
                && i.requirements
                    .iter()
                    .any(|r| r.parameter.starts_with("location.length."))
        }),
        "nearby axes do not share a centre"
    );
}

#[test]
fn authored_slot_across_from_far_face_is_credited_without_borrowing_table_cells() {
    let p = planned(PLATE, &input(PLATE));
    assert!(dim(&p.plan, "d:authored:slot_y").is_some());
    let r = rendered(PLATE, &p);
    let across_missing = |issues: &[LintIssue]| {
        issues.iter().any(|i| {
            i.code == "slot_requirement_missing"
                && i.requirements.iter().any(|q| {
                    q.feature == "f:slot:1" && q.parameter == "location_slot_across.length"
                })
        })
    };
    let a = &fixture(PLATE).analysis;
    let marks: Vec<_> = r.drawings.iter().flat_map(lint::marks).collect();
    let refs: Vec<String> = input(PLATE)["dimensions"]["d:authored:slot_y"]["between"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| r.as_str().unwrap().into())
        .collect();
    let face = a.dimension_reference_axis_face(&refs[1]).unwrap();
    assert!(
        a.features
            .iter()
            .find(|f| f.id == "f:slot:1")
            .unwrap()
            .faces
            .contains(&face)
    );
    assert!(
        !across_missing(&r.lint),
        "the far-edge ordinate locates the slot; chain {:?}",
        lint::location::printed_chain(a, &marks, "f:slot:1", "location_slot_across.length"),
    );
    assert!(!across_missing(&lint::check(a, &marks)));
    let reduced: Vec<_> = marks
        .into_iter()
        .filter(|m| m.name != "d:authored:slot_y")
        .collect();
    assert!(
        across_missing(&lint::check(a, &reduced)),
        "other holes' table cells locate no slot"
    );
}

#[test]
fn spec_part_identity_and_conflicts() {
    let f = fixture(PIVOT);
    let keys = draftwright::pack::part_keys(&f.analysis.parts);
    let key = &keys[0];
    let spec: Spec = serde_json::from_value(json!({"part": key})).unwrap();
    let step = root().join(format!("{PIVOT}.step.gz"));
    let p = api::plan_part(&step, Some(0), api::Features::Native, &spec).unwrap();
    assert_eq!(p.binding.part_index, Some(0));
    assert!(!has(&p, "assembly_principal_drawn"));
    let error = api::plan_part(&step, Some(1), api::Features::Native, &spec).unwrap_err();
    assert!(error.message.contains("conflicts"), "{error}");
    let error = api::call(
        "plan",
        &json!({"analysis": f.analysis, "part": 1, "spec": {"part": key}}),
    )
    .unwrap_err();
    assert!(error.message.contains("conflicts"), "{error}");
    let wrong: Spec = serde_json::from_value(json!({"part":1})).unwrap();
    assert!(
        api::plan_analysed(&f.analysis, &wrong)
            .unwrap_err()
            .message
            .contains("analyse that part")
    );
    let unknown: Spec =
        serde_json::from_value(json!({"part":"missing persistent identity"})).unwrap();
    assert!(
        api::plan_part(&step, None, api::Features::Native, &unknown)
            .unwrap_err()
            .message
            .contains("part key")
    );
}

#[test]
fn spec_depth_presentation_keeps_measured_terms() {
    let id = "d:f:section_recess:1/pocket_width.length";
    for (choice, value, role, words) in [
        (
            "tangent_depth",
            3.,
            "pocket_depth_from_tangent.length",
            "DEEP FROM TANGENT",
        ),
        (
            "far_extreme",
            27.,
            "pocket_floor_from_far_extreme.length",
            "FLOOR FROM FAR TANGENT",
        ),
    ] {
        let mut s = input("inline-301");
        s["dimensions"][id]["presentation"] = json!(choice);
        let p = planned(SHAFT, &s);
        let d = dim(&p.plan, id).unwrap();
        let label = d.label.split_whitespace().collect::<Vec<_>>().join(" ");
        assert!(label.contains(words), "{}", d.label);
        assert!(!d.label.contains("MAX"));
        assert!(
            d.measurements
                .iter()
                .any(|m| m.parameter == role && (m.value - value).abs() < 1e-6)
        );
        assert!(d.from_spec.iter().any(|f| f == "presentation"));
        if choice == "far_extreme" {
            assert!(
                !d.measurements
                    .iter()
                    .any(|m| m.parameter == "pocket_max_depth.length")
            );
        }
    }
    for id in [id, "d:envelope/width.length"] {
        let mut s = input("inline-301");
        s["dimensions"][id] = json!({"presentation":"far_extreme", "omit":true});
        let p = planned(SHAFT, &s);
        assert!(has(&p.plan, "dimension_edit_unapplied"));
        assert!(p.plan.spec.dimensions[id].presentation.is_some());
    }
}

#[test]
fn named_section_public_schema_and_geometry() {
    let mut s = input("inline-301");
    s["views"]["sections"] = json!([{"id":"B", "through":"f:section_recess:1"}]);
    let schema: Value = serde_json::from_str(include_str!("../schemas/spec.json")).unwrap();
    draftwright::mcp::validate(&schema, &s).unwrap();
    let inputs: Value =
        serde_json::from_str(include_str!("../schemas/mcp-validation.json")).unwrap();
    for tool in ["inspect_part", "render", "get_spec"] {
        draftwright::mcp::validate(&inputs[tool], &json!({"part_ref":"part", "spec":s})).unwrap();
    }
    let p = planned(SHAFT, &s);
    let v = p
        .plan
        .views
        .iter()
        .find(|v| v.id == "v:section_bb")
        .unwrap();
    let cut = v.cut.as_ref().unwrap();
    assert_eq!(cut.normal, [0., 1., 0.]);
    assert!(cut.origin[1].abs() < 1e-8);
    assert_eq!(cut.label, "B");
    assert!(v.from_spec.iter().any(|f| f == "views/sections/0"));
    for (role, value) in [("pocket_width.length", 6.), ("pocket_depth.length", 3.)] {
        let d = p
            .plan
            .dimensions
            .iter()
            .find(|d| {
                d.view == v.id
                    && d.placed.is_some()
                    && d.measurements
                        .iter()
                        .any(|m| m.parameter == role && (m.value - value).abs() < 1e-8)
            })
            .unwrap();
        assert!(d.from_spec.iter().any(|f| f == "views/sections/0"));
    }
    let rendered = rendered(SHAFT, &p);
    assert!(
        !rendered
            .drawings
            .iter()
            .flat_map(|d| &d.problems)
            .any(|p| p.code == "section_dropped")
    );
    s["views"]["sections"] = json!([{"id":"A", "through":"f:turned_step:2"}]);
    let p = planned(SHAFT, &s);
    assert!(
        p.plan.problems.iter().any(|p| p.code == "section_not_shown"
            && p.message.contains("no front-facing cutting plane"))
    );
    assert!(p.plan.views.iter().any(|v| v.id == "v:front"));
}

#[test]
fn explicit_section_reports_its_plane_or_missing_geometry() {
    let mut s = minimal();
    s["views"] = json!({"section_aa":{}});
    let p = planned(FLANGE, &s);
    let v = p
        .plan
        .views
        .iter()
        .find(|v| v.id == "v:section_aa")
        .unwrap();
    let cut = v.cut.as_ref().unwrap();
    assert!(v.from_spec.iter().any(|f| f == "views/section_aa"));
    assert!(p.plan.views.iter().any(|v| v.id == cut.marked_in && v.line_work.iter().any(|w| w.kind == "cutting_plane")));
    assert!(
        !rendered(FLANGE, &p)
            .drawings
            .iter()
            .flat_map(|d| &d.problems)
            .any(|p| p.code == "section_dropped")
    );
    let p = planned(SHAFT, &s);
    assert!(p.plan.views.iter().any(|v| v.id == "v:front"));
    assert!(p.plan.problems.iter().any(|p| p.code == "section_not_shown"
        && p.message.contains("no front-facing cutting plane")
        && p.message.contains("retained")));
}
