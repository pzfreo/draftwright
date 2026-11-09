//! Surface finish, threads, knurls and notes authored in the Spec ([`draftwright::spec_notes`],
//! draftwright-rust #43 item 1, stream K1b): lowered into the records the AP242 PMI path reads,
//! so each is drawn by the path that draws the file's own (the ISO 1302 symbol, the owner's
//! callout, the leadered label, the default finish beside the title block); the Spec wins over
//! the file's PMI with a warning; what cannot be read or attached is refused or reported.

#[path = "common/corpus.rs"]
mod corpus;

use std::io::Read as _;
use std::path::{Path, PathBuf};

use draftwright::haecceity::Part;
use draftwright::model::{self, Analysis, Plan, Spec};
use serde_json::json;

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn plate() -> (Part, Analysis) {
    model::analyse_native(&root().join("tests/fixtures/parts/plate.step")).unwrap()
}

/// A gzipped sample, from its own scratch file.
fn sample(name: &str) -> (Part, Analysis) {
    sample_marked(name, false)
}

fn sample_marked(name: &str, marked: bool) -> (Part, Analysis) {
    let mut bytes = Vec::new();
    flate2::read::GzDecoder::new(
        std::fs::read(
            root()
                .join("tests/fixtures/samples")
                .join(format!("{name}.step.gz")),
        )
        .unwrap()
        .as_slice(),
    )
    .read_to_end(&mut bytes)
    .unwrap();
    if marked {
        bytes = String::from_utf8(bytes)
            .unwrap()
            .replace("THREAD('','')", "THREAD('','specify-core:written:v1')")
            .replace(
                "TURNED_KNURL('','diamond')",
                "TURNED_KNURL('specify-core:written:v1','diamond')",
            )
            .into_bytes();
    }
    // nextest runs cases in separate processes, each starting at ThreadId(1).
    // Isolate their atomic staging files as well as ordinary harness threads.
    let out = Path::new(env!("CARGO_TARGET_TMPDIR"))
        .join("spec_notes")
        .join(std::process::id().to_string());
    std::fs::create_dir_all(&out).unwrap();
    let path = out.join(format!("{name}.step"));
    let part = out.join(format!("{name}.{:?}.part", std::thread::current().id()));
    std::fs::write(&part, &bytes).unwrap();
    std::fs::rename(&part, &path).unwrap();
    model::analyse_native(&path).unwrap()
}

/// The editor's spindle without PMI: ø10, ø15 and ø24 turned steps, a ⌀4.2 blind hole.
fn spindle() -> (Part, Analysis) {
    sample("specify-sample-spindle")
}

fn spec(v: serde_json::Value) -> Spec {
    serde_json::from_value(v).expect("the spec reads")
}

fn font() -> draftwright::text::Font {
    draftwright::text::Font::open(&root().join("fonts").join(draftwright::dims::FONT)).unwrap()
}

fn gdt<'a>(plan: &'a Plan, kind: &str) -> Vec<&'a draftwright::dims::gdt::PlannedGdt> {
    plan.gdt.iter().filter(|g| g.kind == kind).collect()
}

fn dimension<'a>(plan: &'a Plan, id: &str) -> &'a draftwright::dims::PlannedDimension {
    plan.dimensions
        .iter()
        .find(|d| d.id == id)
        .unwrap_or_else(|| panic!("{id} is planned"))
}

/// The drawing's PMI lint findings, against *a* with the spec it embeds merged in, as
/// `api::review` lints.
fn pmi_lint(a: &Analysis, d: &draftwright::output::Drawing) -> Vec<String> {
    let s: Spec = serde_json::from_str(d.spec.as_deref().expect("the drawing embeds its spec"))
        .expect("the embedded spec reads");
    let merged = draftwright::spec_gdt::effective(a, &s).expect("the plan accepted the spec");
    draftwright::lint::lint(&merged, d)
        .into_iter()
        .filter(|i| i.code.starts_with("pmi_"))
        .map(|i| format!("{}: {}", i.code, i.message))
        .collect()
}

/// The runs of the drawing's annotation *name*.
fn runs<'a>(d: &'a draftwright::output::Drawing, name: &str) -> Vec<&'a str> {
    let a = d
        .annotations
        .iter()
        .find(|a| a.name == name)
        .unwrap_or_else(|| panic!("{name} is drawn"));
    a.runs.iter().map(|&i| d.text[i].text.as_str()).collect()
}

fn problems<'a>(plan: &'a Plan, code: &str) -> Vec<&'a model::Problem> {
    plan.problems.iter().filter(|p| p.code == code).collect()
}

fn no_pmi_problems(plan: &Plan) {
    assert!(
        !plan.problems.iter().any(|p| p.code.starts_with("pmi_")),
        "{:?}",
        plan.problems
    );
}

#[test]
fn ra_0_8_on_a_plate_face_draws_its_iso_1302_symbol() {
    let (part, a) = plate();
    assert!(a.pmi.is_none(), "plate.step carries no PMI");
    let s = spec(json!({"faces": {"top": {"finish": {"ra": 0.8}}}}));
    let plan = model::plan_recovered(&a, &s).unwrap();
    let finishes = gdt(&plan, "surface_finish");
    assert_eq!(finishes.len(), 1);
    let f = finishes[0];
    assert_eq!(f.cells, ["Ra 0.8"]);
    assert_eq!(f.source, "spec:faces/top/finish");
    assert_eq!((f.view.as_str(), f.side.as_str()), ("v:front", "above"));
    assert!(f.basis.starts_with("spec-authored"), "{}", f.basis);
    assert!(!f.default);
    assert!(f.placed.is_some());
    assert_eq!(f.faces.len(), 1, "the top face alone");
    no_pmi_problems(&plan);
    let d = model::render(&part, &a, &plan, &font()).unwrap();
    assert_eq!(runs(&d, &f.id), ["Ra 0.8"]);
    assert_eq!(pmi_lint(&a, &d), Vec::<String>::new());
    // On a feature, its cylinders carry it; a removal condition is drawn as ISO 1302's bar.
    let (part, a) = spindle();
    let s = spec(
        json!({"features": {"f:turned_step:f6": {"finish": {"ra": 1.6, "removal": "required"}}}}),
    );
    let plan = model::plan_recovered(&a, &s).unwrap();
    let f = gdt(&plan, "surface_finish")[0];
    assert_eq!(f.cells, ["Ra 1.6"]);
    let step = a
        .features
        .iter()
        .find(|f| f.id == "f:turned_step:f6")
        .unwrap();
    assert_eq!(f.faces, step.faces);
    assert!(f.placed.is_some());
    no_pmi_problems(&plan);
    let d = model::render(&part, &a, &plan, &font()).unwrap();
    assert_eq!(pmi_lint(&a, &d), Vec::<String>::new());
}

#[test]
fn a_default_finish_is_drawn_beside_the_title_block() {
    let (part, a) = plate();
    let s = spec(json!({"sheet": {"default_finish": {"ra": 3.2, "removal": "prohibited"}}}));
    let plan = model::plan_recovered(&a, &s).unwrap();
    assert_eq!(plan.requirements.len(), 1);
    let r = &plan.requirements[0];
    assert_eq!(
        (r.kind.as_str(), r.text.as_str(), r.carrier.as_str()),
        ("default_surface_finish", "Ra 3.2", "sheet")
    );
    assert_eq!(r.source, "spec:sheet/default_finish");
    assert!(r.basis.starts_with("spec-authored"), "{}", r.basis);
    // No material removal: ISO 1302's circle.
    let placed = r.placed.as_ref().expect("the symbol is placed");
    assert!(!placed.arcs.is_empty(), "{placed:?}");
    no_pmi_problems(&plan);
    let d = model::render(&part, &a, &plan, &font()).unwrap();
    assert!(d.svg().contains("Ra 3.2"));
    assert_eq!(pmi_lint(&a, &d), Vec::<String>::new());
}

#[test]
fn m6_on_the_plate_hole_pattern_prints_on_its_callout() {
    let (part, a) = plate();
    let base = model::plan_recovered(&a, &Spec::default()).unwrap();
    let id = "d:f:hole_pattern:f6/bore.diameter";
    assert_eq!(dimension(&base, id).label, "4× ⌀6 THRU");
    let s = spec(json!({"features": {"f:hole_pattern:f6": {"thread": {"designation": "M6"}}}}));
    let plan = model::plan_recovered(&a, &s).unwrap();
    let callout = dimension(&plan, id);
    assert_eq!(callout.label, "4× M6 THRU\n⌀6 THRU");
    let claim = callout
        .measurements
        .iter()
        .find(|m| m.parameter == "thread")
        .expect("the callout claims the thread");
    assert!(
        claim
            .feature
            .starts_with("pmi:manufacturing_requirement.internal_thread:f"),
        "{}",
        claim.feature
    );
    assert!(
        callout.basis.contains("the spec's thread"),
        "{}",
        callout.basis
    );
    no_pmi_problems(&plan);
    let d = model::render(&part, &a, &plan, &font()).unwrap();
    assert!(d.svg().contains("M6"));
    assert_eq!(pmi_lint(&a, &d), Vec::<String>::new());
    // A depth prints as the minimum full thread; a member of the pattern is pointed at it; a tap
    // too small for its hole is reported, not drawn.
    let plan = model::plan_recovered(
        &a,
        &spec(json!({"features": {"f:hole_pattern:f6": {"thread": {"designation": "M6x1-6H", "length": 4}}}})),
    )
    .unwrap();
    assert_eq!(dimension(&plan, id).label, "4× M6-6H ↧ 4\n⌀6 THRU");
    let plan = model::plan_recovered(
        &a,
        &spec(json!({"features": {"f:hole:f6": {"thread": {"designation": "M6"}}}})),
    )
    .unwrap();
    let refused = problems(&plan, "pmi_not_lowered");
    assert_eq!(refused.len(), 1, "{:?}", plan.problems);
    assert_eq!(refused[0].severity, "error");
    assert_eq!(
        refused[0].refs.first().map(String::as_str),
        Some("spec:features/f:hole:f6/thread")
    );
    assert!(
        refused[0].refs.len() > 1,
        "the original refusal diagnostic is retained"
    );
    assert!(
        refused[0]
            .message
            .contains("features/f:hole_pattern:f6/thread"),
        "{}",
        refused[0].message
    );
    assert_eq!(dimension(&plan, id).label, "4× ⌀6 THRU");
    let plan = model::plan_recovered(
        &a,
        &spec(json!({"features": {"f:hole_pattern:f6": {"thread": {"designation": "M3"}}}})),
    )
    .unwrap();
    let refused = problems(&plan, "pmi_not_lowered");
    assert_eq!(refused.len(), 1, "{:?}", plan.problems);
    assert!(
        refused[0].message.contains("cannot be cut"),
        "{}",
        refused[0].message
    );
}

#[test]
fn m10_and_a_knurl_on_the_spindle_print_after_their_diameters() {
    let (part, a) = spindle();
    assert!(a.pmi.is_none(), "the sample spindle carries no PMI");
    let s = spec(json!({"features": {
        "f:turned_step:f2": {"thread": {"designation": "M10", "length": 15}},
        "f:turned_step:f9": {"knurl": {"pattern": "diamond", "pitch": 0.8}}
    }}));
    let plan = model::plan_recovered(&a, &s).unwrap();
    assert_eq!(
        dimension(&plan, "d:f:turned_step:f2/step.diameter").label,
        "M10; 15 MIN FULL THREAD"
    );
    // The ø24 step's diameter is the overall diameter's, which prints its knurl.
    let od = dimension(&plan, "d:envelope/od.diameter");
    assert_eq!(od.label, "ø24\nDIAMOND KNURL P0.8");
    assert!(od.measurements.iter().any(|m| m.parameter == "knurl"));
    no_pmi_problems(&plan);
    let d = model::render(&part, &a, &plan, &font()).unwrap();
    assert!(d.svg().contains("M10"));
    assert!(d.svg().contains("KNURL"));
    assert_eq!(pmi_lint(&a, &d), Vec::<String>::new());
    // A thread whose nominal size is not its diameter, or longer than its step, is reported.
    let plan = model::plan_recovered(
        &a,
        &spec(json!({"features": {
            "f:turned_step:f6": {"thread": {"designation": "M16"}},
            "f:turned_step:f2": {"thread": {"designation": "M10", "length": 40}},
            "f:hole:f3": {"thread": {"designation": "M5", "length": 12}}
        }})),
    )
    .unwrap();
    let refused = problems(&plan, "pmi_not_lowered");
    assert_eq!(refused.len(), 3, "{:?}", plan.problems);
    // A tap deeper than its blind hole (ISSUE-1360-a).
    assert!(
        refused
            .iter()
            .any(|p| p.message.contains("exceeds its hole's depth 9.2"))
    );
    assert!(
        refused
            .iter()
            .any(|p| p.message.contains("not the nominal size"))
    );
    assert!(
        refused
            .iter()
            .any(|p| p.message.contains("exceeds its length 18"))
    );
}

/// A thread or knurl on a boss prints on the boss's diameter callout, as a turned step's does:
/// nist_ftc_06's 2× ø31.75 and 2× ø22.86 bosses, one of each pair carrying it, keep a callout
/// of their own (one annotation per owner), the thread's designation in place of the diameter.
#[test]
fn a_thread_and_a_knurl_on_bosses_print_on_their_diameter_callouts() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let (_, a) = corpus::analyse(&dir, "nist/nist_ftc_06_asme1_rd.stp", "ftc06");
    let s = spec(json!({"features": {
        "f:boss:f41": {"thread": {"designation": "M31.75x1.5"}},
        "f:boss:f43": {"knurl": {"pattern": "straight", "pitch": 1}}
    }}));
    let plan = model::plan(&a, &s).unwrap();
    let thread = dimension(&plan, "d:f:boss:f41/boss.diameter");
    assert_eq!(thread.label, "M31.75 x 1.5");
    assert!(thread.measurements.iter().any(|m| m.parameter == "thread"));
    assert!(
        thread
            .measurements
            .iter()
            .all(|m| m.feature != "f:boss:f46")
    );
    let knurl = dimension(&plan, "d:f:boss:f43/boss.diameter");
    assert_eq!(knurl.label, "ø22.86\nSTRAIGHT KNURL P1");
    assert!(knurl.measurements.iter().any(|m| m.parameter == "knurl"));
    assert_eq!(
        dimension(&plan, "d:f:boss:f46/boss.diameter").label,
        "ø31.75"
    );
    assert_eq!(
        dimension(&plan, "d:f:boss:f90/boss.diameter").label,
        "ø22.86"
    );
    assert!(
        problems(&plan, "pmi_not_lowered").is_empty(),
        "{:?}",
        plan.problems
    );
}

#[test]
fn a_feature_note_and_a_sheet_note_are_drawn() {
    let (part, a) = spindle();
    let s = spec(json!({
        "features": {"f:hole:f3": {"note": "DRILL AND TAP AFTER HARDENING"}},
        "notes": [{"id": "n:1", "text": "BREAK ALL SHARP EDGES"}]
    }));
    let plan = model::plan_recovered(&a, &s).unwrap();
    let labels = gdt(&plan, "label");
    assert_eq!(labels.len(), 1);
    let note = labels[0];
    assert_eq!(note.cells, ["DRILL AND TAP AFTER HARDENING"]);
    assert_eq!(note.source, "spec:features/f:hole:f3/note");
    assert!(note.basis.starts_with("spec-authored"), "{}", note.basis);
    assert!(note.placed.is_some());
    let hole = a.features.iter().find(|f| f.id == "f:hole:f3").unwrap();
    assert_eq!(note.faces, hole.faces);
    assert_eq!(plan.notes.len(), 1);
    assert_eq!(plan.notes[0].rows, ["1  BREAK ALL SHARP EDGES"]);
    no_pmi_problems(&plan);
    let d = model::render(&part, &a, &plan, &font()).unwrap();
    assert_eq!(runs(&d, &note.id), ["DRILL AND TAP AFTER", "HARDENING"]);
    assert!(d.svg().contains("BREAK ALL SHARP EDGES"));
    assert_eq!(pmi_lint(&a, &d), Vec::<String>::new());
    // A face takes a note too.
    let (_, plate) = plate();
    let plan = model::plan_recovered(&plate, &spec(json!({"faces": {"top": {"note": "DEBURR"}}})))
        .unwrap();
    assert_eq!(gdt(&plan, "label")[0].cells, ["DEBURR"]);
}

#[test]
fn spec_finish_threads_and_knurls_win_over_step_pmi_with_a_problem() {
    let (part, a) = sample("cnc-review-spindle");
    let base = model::plan_recovered(&a, &Spec::default()).unwrap();
    assert_eq!(
        dimension(&base, "d:envelope/od.diameter").label,
        "ø24 MAX AFTER KNURL\nDIAMOND KNURL P1"
    );
    assert_eq!(
        base.requirements
            .iter()
            .filter(|r| r.text == "Ra 3.2")
            .count(),
        1
    );
    let s = spec(json!({
        "features": {
            "f:turned_step:f9": {"knurl": {"pattern": "straight", "pitch": 1.0}},
            "f:hole:f3": {"thread": {"designation": "M5x0.8-6H", "length": 8}}
        },
        "sheet": {"default_finish": {"ra": 1.6, "removal": "required"}}
    }));
    let plan = model::plan_recovered(&a, &s).unwrap();
    assert_eq!(
        dimension(&plan, "d:envelope/od.diameter").label,
        "ø24\nSTRAIGHT KNURL P1"
    );
    assert_eq!(
        dimension(&plan, "d:f:hole:f3/bore.diameter").label,
        "M5-6H ↧ 8\n⌀4.2 ↧ 9.2"
    );
    let finishes: Vec<_> = plan
        .requirements
        .iter()
        .filter(|r| r.kind == "default_surface_finish")
        .collect();
    assert_eq!(finishes.len(), 1);
    assert_eq!(finishes[0].text, "Ra 1.6");
    let mut overridden: Vec<(&str, &str)> = problems(&plan, "spec_overrides_pmi")
        .iter()
        .map(|p| (p.refs[0].as_str(), p.refs[1].as_str()))
        .collect();
    overridden.sort();
    assert_eq!(
        overridden,
        [
            (
                "manufacturing_requirement:#1007",
                "spec:sheet/default_finish"
            ),
            (
                "manufacturing_requirement:#925",
                "spec:features/f:hole:f3/thread"
            ),
            (
                "manufacturing_requirement:#947",
                "spec:features/f:turned_step:f9/knurl"
            ),
        ]
    );
    assert!(
        problems(&plan, "spec_overrides_pmi")
            .iter()
            .all(|p| p.severity == "warning")
    );
    assert!(
        problems(&plan, "pmi_not_lowered").is_empty(),
        "{:?}",
        plan.problems
    );
    // Lint judges the drawing against the merged reading: nothing lost, nothing unknown.
    let d = model::render(&part, &a, &plan, &font()).unwrap();
    let base_d = model::render(&part, &a, &base, &font()).unwrap();
    assert_eq!(pmi_lint(&a, &d), pmi_lint(&a, &base_d));
}

#[test]
fn unknown_ids_and_unreadable_entries_are_refused() {
    let (_, a) = plate();
    let e = model::plan_recovered(
        &a,
        &spec(json!({"features": {"f:hole_patern:1": {"thread": {"designation": "M6"}}}})),
    )
    .unwrap_err();
    assert!(
        e.message
            .starts_with("There is no feature \"f:hole_patern:1\" on this part"),
        "{}",
        e.message
    );
    assert_eq!(e.choices[0], "f:hole_pattern:f6");
    let e = model::plan_recovered(
        &a,
        &spec(json!({"faces": {"underside": {"finish": {"ra": 0.8}}}})),
    )
    .unwrap_err();
    assert!(
        e.message.contains("\"underside\" names no face"),
        "{}",
        e.message
    );
    let e = model::plan_recovered(
        &a,
        &spec(json!({"features": {"f:hole:f6": {"knurl": {"pattern": "straight", "pitch": 0.8}}}})),
    )
    .unwrap_err();
    assert!(e.message.contains("takes no knurl"), "{}", e.message);
    let refused = |ops: serde_json::Value| model::edit(&Spec::default(), &ops).unwrap_err();
    let e = refused(json!([{"op": "set", "path": "faces/top/finish", "value": {"ra": 0}}]));
    assert!(e.message.contains("positive"), "{}", e.message);
    let e = refused(
        json!([{"op": "set", "path": "faces/top/finish", "value": {"ra": 0.8, "removal": "maybe"}}]),
    );
    assert_eq!(e.choices, ["any", "required", "prohibited"]);
    let e = refused(
        json!([{"op": "set", "path": "features/f:hole:f6/thread", "value": {"designation": "1/4-20 UNC"}}]),
    );
    assert!(
        e.message.contains("not an ISO metric thread"),
        "{}",
        e.message
    );
    let e = refused(
        json!([{"op": "set", "path": "features/f:hole:f6/thread", "value": {"designation": "M13"}}]),
    );
    assert!(
        e.message.contains("no ISO 261 coarse pitch"),
        "{}",
        e.message
    );
    let e = refused(
        json!([{"op": "set", "path": "features/f:turned_step:1/knurl", "value": {"pattern": "spiral", "pitch": 1}}]),
    );
    assert_eq!(e.choices, ["straight", "diamond"]);
    let e = refused(json!([{"op": "set", "path": "features/f:hole:f6/note", "value": "  "}]));
    assert!(e.message.contains("empty"), "{}", e.message);
    // What reads is kept, as written.
    let s = model::edit(
        &Spec::default(),
        &json!([
            {"op": "set", "path": "faces/top/finish", "value": {"ra": 0.8}},
            {"op": "set", "path": "features/f:hole:f6/thread", "value": {"designation": "M5 LH", "length": 8}},
            {"op": "set", "path": "sheet/default_finish", "value": {"ra": 3.2}}
        ]),
    )
    .unwrap();
    let v = serde_json::to_value(&s).unwrap();
    assert_eq!(v["faces"]["top"]["finish"]["ra"], 0.8);
    assert_eq!(v["features"]["f:hole:f6"]["thread"]["designation"], "M5 LH");
    assert_eq!(v["sheet"]["default_finish"]["ra"], 3.2);
}

#[test]
fn the_session_offers_finish_thread_knurl_and_note_paths() {
    let (part, a) = spindle();
    let session = draftwright::Session::new(part, a, Spec::default()).unwrap();
    let options =
        serde_json::to_value(session.options("d:f:turned_step:f2/step.diameter").unwrap()).unwrap();
    let settable = &options["settable"];
    let base = "features/f:turned_step:f2";
    for path in ["finish", "thread", "knurl", "note"] {
        assert!(
            settable.get(format!("{base}/{path}")).is_some(),
            "{path}: {settable}"
        );
    }
    let options =
        serde_json::to_value(session.options("d:f:hole:f3/bore.diameter").unwrap()).unwrap();
    assert!(
        options["settable"]
            .get("features/f:hole:f3/thread")
            .is_some()
    );
    assert!(
        options["settable"]
            .get("features/f:hole:f3/knurl")
            .is_none()
    );
}

#[test]
fn structured_feature_notes_cover_only_explicit_roles_of_their_placed_owner() {
    let (part, a) = plate();
    let owner = a
        .features
        .iter()
        .find(|f| f.kind == "hole")
        .unwrap()
        .id
        .clone();
    let make = |claims: bool| {
        spec(json!({
            "dimension_source":"authored",
            "features":{&owner:{"note":"DIAMETER PER PROCESS SHEET", "note_satisfies":if claims {vec!["bore.diameter"]} else {vec![]}}}
        }))
    };
    let draw = |s: &Spec| {
        let plan = model::plan(&a, s).unwrap();
        assert!(plan.dimensions.is_empty());
        let merged = draftwright::spec_gdt::effective(&a, s)
            .unwrap()
            .into_owned();
        let drawing = model::render(&part, &a, &plan, &font()).unwrap();
        (merged, drawing)
    };
    let missing = |a: &Analysis, d: &draftwright::output::Drawing| {
        draftwright::lint::lint(a, d)
            .into_iter()
            .filter(|i| {
                i.code == "hole_requirement_missing"
                    && i.requirements
                        .iter()
                        .any(|r| r.parameter == "bore.diameter")
            })
            .collect::<Vec<_>>()
    };
    let (merged, prose) = draw(&make(false));
    let before = missing(&merged, &prose);
    assert!(!before.is_empty());
    let (merged, mut structured) = draw(&make(true));
    let annotation = structured
        .annotations
        .iter()
        .find(|a| !a.satisfies.is_empty())
        .unwrap()
        .clone();
    assert!(annotation.claims.is_empty());
    assert_eq!(annotation.satisfies.len(), 1);
    // The plate has identical siblings. Its grouped missing requirement must remain until
    // every physical identity has authority; one note must not close the group.
    let after = missing(&merged, &structured);
    assert!(!after.is_empty());
    assert!(
        after
            .iter()
            .all(|i| i.requirements.iter().any(|r| r.feature != owner))
    );
    assert!(
        after
            .iter()
            .flat_map(|i| &i.requirements)
            .all(|r| r.feature != owner)
    );
    structured.annotations.push(annotation.clone());
    assert_eq!(missing(&merged, &structured), after);
    structured.annotations.retain(|a| a.name != annotation.name);
    assert_eq!(missing(&merged, &structured), before);

    // With an explicit note for every physical hole, the diameter requirement closes,
    // independently of through/count roles. Omitting the note's view grants no authority.
    let mut all = make(true);
    for f in a.features.iter().filter(|f| f.kind == "hole") {
        all.features.insert(
            f.id.clone(),
            serde_json::from_value(
                json!({"note":"SIZE PER PROCESS", "note_satisfies":["bore.diameter"]}),
            )
            .unwrap(),
        );
    }
    let (merged, mut complete) = draw(&all);
    assert!(missing(&merged, &complete).is_empty());
    assert!(
        draftwright::lint::lint(&merged, &complete)
            .iter()
            .any(|i| i.requirements.iter().any(|r| r.parameter == "bore.through"))
    );
    for a in &mut complete.annotations {
        a.view = None;
    }
    assert!(!missing(&merged, &complete).is_empty());
    let bad = spec(json!({"features":{&owner:{"note":"TEST", "note_satisfies":["made.up"]}}}));
    assert!(model::plan(&a, &bad).is_err());
}

#[test]
fn specify_thread_designation_refines_its_marked_requirement_and_retains_length() {
    let (_, a) = sample_marked("cnc-review-spindle", true);
    // Authorship must survive the same Analysis serialization used by editor/MCP clients.
    let a: Analysis = serde_json::from_value(serde_json::to_value(a).unwrap()).unwrap();
    let Some(draftwright::pmi::Reading::Read(report)) = &a.pmi else {
        panic!("PMI fixture")
    };
    assert!(
        report
            .sources
            .iter()
            .any(|s| s.id == "manufacturing_requirement:#925"
                && s.reason.contains("specify-core:written:v1"))
    );
    // An unmarked requirement in the same file does not inherit Specify authorship.
    assert!(
        !report
            .sources
            .iter()
            .find(|s| s.id == "manufacturing_requirement:#1007")
            .unwrap()
            .reason
            .contains("specify-core:written:v1")
    );
    let s = spec(json!({"features": {
        "f:hole:f3": {"thread": {"designation": "M5-6H"}},
        "f:turned_step:f9": {"knurl": {"pattern": "straight", "pitch": 1.0}}
    }}));
    let plan = model::plan_recovered(&a, &s).unwrap();
    let refined = problems(&plan, "spec_refines_pmi");
    assert_eq!(refined.len(), 1, "{:?}", plan.problems);
    assert_eq!(refined[0].severity, "info");
    assert_eq!(
        refined[0].message,
        "The Spec's M5-6H refines Specify's thread designation."
    );
    assert_eq!(
        dimension(&plan, "d:f:hole:f3/bore.diameter").label,
        "M5-6H ↧ 6.5\n⌀4.2 ↧ 9.2"
    );
    let overrides = problems(&plan, "spec_overrides_pmi");
    assert_eq!(overrides.len(), 1);
    assert_eq!(
        overrides[0].message,
        "The Spec's straight knurl, 1 mm pitch replaces Specify's knurl annotation, which is not drawn. Remove the corresponding setting to draw the original requirement."
    );
    // An explicit length still takes precedence over the inherited minimum full thread.
    let s =
        spec(json!({"features": {"f:hole:f3": {"thread": {"designation": "M5-6H", "length": 6}}}}));
    let plan = model::plan_recovered(&a, &s).unwrap();
    assert!(
        dimension(&plan, "d:f:hole:f3/bore.diameter")
            .label
            .starts_with("M5-6H ↧ 6")
    );
}

#[test]
fn string_post_finishes_do_not_add_location_requirements() {
    let Some(corpus) = std::env::var_os("DRAFTWRIGHT_CORPUS")
        .map(PathBuf::from)
        .or_else(|| {
            std::env::var_os("HOME").map(|p| PathBuf::from(p).join("repos/quiddity/tests/corpus"))
        })
        .filter(|p| p.is_dir())
    else {
        assert_ne!(
            std::env::var("DRAFTWRIGHT_CORPUS_REQUIRED").as_deref(),
            Ok("1")
        );
        return;
    };
    let (part, a) = model::analyse_native(&corpus.join("gramel/string_post.step")).unwrap();
    assert!(a.pmi.is_none());
    let plain = model::plan_recovered(&a, &Spec::default()).unwrap();
    let mut features = serde_json::Map::new();
    for f in a.features.iter().filter(|f| f.kind == "turned_step") {
        features.insert(f.id.clone(), json!({"finish": {"ra": 0.8}}));
    }
    assert!(!features.is_empty());
    let s = spec(json!({"features": features}));
    let finished = model::plan_recovered(&a, &s).unwrap();
    assert!(!gdt(&finished, "surface_finish").is_empty());
    for p in [&plain, &finished] {
        assert!(
            problems(p, "feature_not_located").is_empty(),
            "{:?}",
            p.problems
        );
    }
    let drawing = model::render(&part, &a, &finished, &font()).unwrap();
    let effective = draftwright::spec_gdt::effective(&a, &s).unwrap();
    assert!(
        !draftwright::lint::lint(&effective, &drawing)
            .iter()
            .any(|p| p.code == "feature_not_located")
    );
}

/// #98: two finishes on string_post, one on the plain ø6 and one on the ø3.4 that the flats trim
/// to two half cylinders with no silhouette, keep the A4 sheet main chooses without them, draw
/// both, and leave no leader through text or leader crossing. The trimmed cylinder is reached
/// head-on: its leader ends in a dot inside the shaft's outline.
#[test]
fn string_post_face_finishes_stay_on_a4_and_reach_a_trimmed_cylinder_head_on() {
    let Some(corpus) = std::env::var_os("DRAFTWRIGHT_CORPUS")
        .map(PathBuf::from)
        .or_else(|| {
            std::env::var_os("HOME").map(|p| PathBuf::from(p).join("repos/quiddity/tests/corpus"))
        })
        .filter(|p| p.is_dir())
    else {
        assert_ne!(
            std::env::var("DRAFTWRIGHT_CORPUS_REQUIRED").as_deref(),
            Ok("1")
        );
        return;
    };
    let (part, a) = model::analyse_native(&corpus.join("gramel/string_post.step")).unwrap();
    let plain = model::plan_recovered(&a, &Spec::default()).unwrap();
    let s = spec(json!({"features": {
        "f:turned_step:f0": {"finish": {"ra": 0.8}},
        "f:turned_step:f10": {"finish": {"ra": 1.6}}
    }}));
    let finished = model::plan_recovered(&a, &s).unwrap();
    assert_eq!(finished.sheet.size, plain.sheet.size);
    assert_eq!(finished.sheet.size, "A4");
    assert_eq!(finished.sheets.len(), plain.sheets.len());
    let finishes = gdt(&finished, "surface_finish");
    assert_eq!(finishes.len(), 2, "{:?}", finished.problems);
    assert!(problems(&finished, "pmi_not_lowered").is_empty());
    let head_on: Vec<_> = finishes
        .iter()
        .filter_map(|g| g.placed.as_ref())
        .filter(|p| p.arrows.is_empty())
        .collect();
    assert_eq!(head_on.len(), 1, "one leader ends in a dot");
    assert_eq!(head_on[0].fills.len(), 1);
    // The dot is inside the front view's outline of the shaft, not on its silhouette.
    let front = finished.views.iter().find(|v| v.id == "v:front").unwrap();
    let tip = head_on[0].tip;
    assert!(tip[0] > front.area[0] && tip[0] < front.area[2]);
    for g in &finishes {
        let p = g.placed.as_ref().unwrap();
        let [_, elbow] = p.lines[0];
        let b = p.label_box;
        assert!(
            !(b[0] <= elbow[0] && elbow[0] <= b[2] && b[1] <= elbow[1] && elbow[1] <= b[3]),
            "{}: the bend stays outside the symbol's box",
            g.id
        );
    }
    assert!(
        finished
            .problems
            .iter()
            .all(|p| p.code != "annotation_leader_crossing"),
        "{:?}",
        finished.problems
    );
    let drawing = model::render(&part, &a, &finished, &font()).unwrap();
    let effective = draftwright::spec_gdt::effective(&a, &s).unwrap();
    let lint = draftwright::lint::lint(&effective, &drawing);
    assert!(
        lint.iter().all(|p| !matches!(
            p.code.as_str(),
            "leader_line_through_text" | "pmi_source_site_mismatch"
        )),
        "{:?}",
        lint.iter().map(|p| &p.code).collect::<Vec<_>>()
    );
}

#[test]
fn a_thread_length_whose_callout_is_omitted_is_reported_not_lost() {
    let (_part, a) = plate();
    let id = "d:f:hole_pattern:f6/bore.diameter";
    let s = spec(json!({
        "features": {"f:hole_pattern:f6": {"thread": {"designation": "M6x1-6H", "length": 4}}},
        "dimensions": {id: {"omit": true}}
    }));
    let plan = model::plan_recovered(&a, &s).unwrap();
    assert!(plan.dimensions.iter().all(|d| d.id != id));
    // The omission keeps the label it would have printed, thread length included.
    let omitted = plan.omitted.iter().find(|o| o.id == id).expect("omitted");
    assert_eq!(omitted.label, "4× M6-6H ↧ 4\n⌀6 THRU");
    // And the thread the Spec authored is reported as not drawn.
    let lost = problems(&plan, "pmi_not_drawn");
    assert_eq!(lost.len(), 1, "{:?}", plan.problems);
    assert_eq!(lost[0].refs, ["spec:features/f:hole_pattern:f6/thread"]);
}

/// LINT-hole_coverage-thread_and_profile, the authored half (draftwright's `thread.depth`, a
/// `ThreadOperation`'s depth): a Spec thread's length on a hole prints as its depth after the
/// designation (`4× M6-6H ↧ 4`), and lint requires that depth on the sheet. A callout that drops
/// it, or states another, is a `pmi_source_text_mismatch` error naming the Spec thread.
#[test]
fn a_spec_threads_depth_on_its_holes_is_required_by_lint() {
    let (part, a) = plate();
    let id = "d:f:hole_pattern:f6/bore.diameter";
    let plan = model::plan_recovered(
        &a,
        &spec(json!({"features": {"f:hole_pattern:f6": {"thread": {"designation": "M6x1-6H", "length": 4}}}})),
    )
    .unwrap();
    assert_eq!(dimension(&plan, id).label, "4× M6-6H ↧ 4\n⌀6 THRU");
    let d = model::render(&part, &a, &plan, &font()).unwrap();
    assert_eq!(pmi_lint(&a, &d), Vec::<String>::new());
    let thread = d.annotations.iter().find(|m| m.name == id).unwrap().clone();
    for (old, new) in [("↧ 4", "↧ 5"), (" ↧ 4", "")] {
        let mut damaged = d.clone();
        for &run in &thread.runs {
            damaged.text[run].text = damaged.text[run].text.replace(old, new);
        }
        assert_ne!(runs(&damaged, id), runs(&d, id), "{old:?}");
        let found = pmi_lint(&a, &damaged);
        assert!(
            found
                .iter()
                .any(|f| f.starts_with("pmi_source_text_mismatch")
                    && f.contains("spec:features/f:hole_pattern:f6/thread")),
            "{old:?}: {found:?}"
        );
    }
}

#[test]
fn location_notes_accept_only_the_features_actual_lint_roles() {
    let (_, a) = plate();
    let roles: std::collections::BTreeSet<_> = draftwright::lint::check(&a, &[])
        .into_iter()
        .flat_map(|i| i.requirements)
        .filter(|r| r.parameter.starts_with("location.length."))
        .map(|r| (r.feature, r.parameter))
        .collect();
    assert!(!roles.is_empty());
    let (owner, role) = roles.iter().next().unwrap();
    let p = model::plan_recovered(
        &a,
        &spec(json!({"features":{owner:{"note":"LOCATION PER PROCESS", "note_satisfies":[role]}}})),
    )
    .unwrap();
    assert!(!p.views.is_empty());
    let hole = a
        .features
        .iter()
        .find(|f| f.kind == "hole" && f.fingerprint.axis.is_some())
        .expect("the plate has an axial hole");
    let owner = &hole.id;
    let axis = hole.fingerprint.axis.unwrap();
    let invalid = format!(
        "location.length.{}",
        ["x", "y", "z"][(0..3)
            .max_by(|&i, &j| axis[i].abs().total_cmp(&axis[j].abs()))
            .unwrap()]
    );
    assert!(!roles.contains(&(owner.clone(), invalid.clone())));
    let e = model::plan_recovered(
        &a,
        &spec(
            json!({"features":{owner:{"note":"LOCATION PER PROCESS", "note_satisfies":[invalid]}}}),
        ),
    )
    .unwrap_err();
    assert!(e.message.contains("not a measurement role"));
    // A centred keyway still has a measurable transverse location, even though
    // the carrying axis already conveys it and lint asks for no extra dimension.
    let bytes =
        std::fs::read(root().join("tests/fixtures/steerability/stepped-shaft-keyway.step.gz"))
            .unwrap();
    let mut step = Vec::new();
    flate2::read::GzDecoder::new(bytes.as_slice())
        .read_to_end(&mut step)
        .unwrap();
    let path = Path::new(env!("CARGO_TARGET_TMPDIR"))
        .join(format!("note-role-{}.step", std::process::id()));
    std::fs::write(&path, step).unwrap();
    let (_, shaft) = model::analyse_native(&path).unwrap();
    let (f, placed) = draftwright::lint::pockets::placed(&shaft)
        .into_iter()
        .find(|(_, p)| p.as_ref().is_ok_and(|p| p.mouth))
        .unwrap();
    let p = placed.unwrap();
    let role = draftwright::lint::pockets::locations(&p, shaft.envelope[0])
        .into_iter()
        .find(|(r, _)| r.ends_with(["x", "y", "z"][p.width_axis]))
        .unwrap()
        .0;
    assert!(
        !draftwright::lint::pockets::wanted(&shaft)
            .iter()
            .any(|w| w.feature.id == f.id && w.parameter == role)
    );
    let plan = model::plan_recovered(
        &shaft,
        &spec(json!({"features":{&f.id:{"note":"CENTRED ON AXIS", "note_satisfies":[role]}}})),
    )
    .unwrap();
    assert!(!plan.views.is_empty());
}
