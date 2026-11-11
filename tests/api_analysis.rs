//! `plan`, `render` and `review` from an Analysis document (`docs/model.md`, "The five
//! operations"): the caller passes what `analyse` returned instead of analysing again. The output
//! is byte-identical to the re-analysing path's, and an Analysis that is not this file's, not of
//! this version, not well formed, or given beside features, is refused as `invalid`.

#[path = "common/corpus.rs"]
mod corpus;
#[path = "common/facts.rs"]
mod facts;

use std::path::{Path, PathBuf};

use draftwright::api::{self, DPI};
use draftwright::model::{ANALYSIS_SCHEMA, Analysis, Spec};
use serde_json::{Value, json};

fn fixture(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(name)
}

fn scratch(name: &str) -> PathBuf {
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR")).join("api_analysis");
    std::fs::create_dir_all(&dir).unwrap();
    dir.join(name)
}

/// A spec that changes the defaults, so the comparison covers more than the default sheet.
fn edited() -> Spec {
    serde_json::from_value(json!({ "views": { "iso": { "omit": true } },
        "sheet": { "title": { "title": "BRACKET" } } }))
    .unwrap()
}

/// Every output of *step* (with *features*) is the same from its Analysis as from re-analysis:
/// through the typed API, and through `api::call` with the Analysis carried inline.
fn same_from_analysis(step: &Path, features: Option<&Path>) {
    let analysis = api::analyse(step, features).unwrap();
    let formats = ["svg", "pdf", "png"];
    for spec in [Spec::default(), edited()] {
        let plan = api::plan(step, features, &spec).unwrap();
        let from = api::plan_analysed(&analysis, &spec).unwrap();
        assert_eq!(
            serde_json::to_string(&plan).unwrap(),
            serde_json::to_string(&from).unwrap()
        );

        let (plan, files) = api::render_at(step, features, &spec, &formats, 40.0).unwrap();
        let (from, from_files) =
            api::render_analysed(step, &analysis, &spec, &formats, 40.0).unwrap();
        assert_eq!(
            serde_json::to_string(&plan).unwrap(),
            serde_json::to_string(&from).unwrap()
        );
        // PDF carries the whole document; SVG and PNG carry every physical page.
        let mut expected = Vec::new();
        for format in formats {
            expected.push(format.to_string());
            if format != "pdf" {
                expected.extend((2..=1 + plan.sheets.len()).map(|n| format!("sheet{n}.{format}")));
            }
        }
        assert_eq!(
            files.iter().map(|(suffix, _)| suffix).collect::<Vec<_>>(),
            expected.iter().collect::<Vec<_>>()
        );
        assert_eq!(files, from_files, "{}", step.display());

        let review = api::review(step, features, &spec).unwrap();
        let from = api::review_analysed(step, &analysis, &spec).unwrap();
        assert_eq!(
            serde_json::to_string(&review).unwrap(),
            serde_json::to_string(&from).unwrap()
        );
    }

    // The same through `api::call`, as the adapters send it.
    let step_arg = step.display().to_string();
    let features_arg = features.map(|f| f.display().to_string());
    let document = api::call(
        "analyse",
        &json!({ "step": step_arg, "features": features_arg }),
    )
    .unwrap();
    let spec = serde_json::to_value(edited()).unwrap();
    let analysed = json!({ "step": step_arg, "features": features_arg, "spec": spec });
    let given = json!({ "step": step_arg, "analysis": document, "spec": spec });
    for op in ["plan", "review"] {
        assert_eq!(
            api::call(op, &analysed).unwrap(),
            api::call(op, &given).unwrap(),
            "{op}"
        );
    }
    let alone = json!({ "analysis": document, "spec": spec });
    assert_eq!(
        api::call("plan", &analysed).unwrap(),
        api::call("plan", &alone).unwrap()
    );
    let stem = step.file_stem().unwrap().to_string_lossy().into_owned();
    let mut written = Vec::new();
    for (args, out) in [(analysed, "analysed"), (given, "given")] {
        let mut args = args;
        let output = scratch(&format!("{stem}.{out}"));
        args["output"] = json!(output.display().to_string());
        let reply = api::call("render", &args).unwrap();
        let files: Vec<Vec<u8>> = reply["written"]
            .as_array()
            .unwrap()
            .iter()
            .map(|f| std::fs::read(f.as_str().unwrap()).unwrap())
            .collect();
        written.push((reply["problems"].clone(), files));
    }
    let edited_plan = api::plan_analysed(&analysis, &edited()).unwrap();
    assert_eq!(
        written[0].1.len(),
        api::DEFAULT_FORMATS.len() + edited_plan.sheets.len()
    );
    assert_eq!(written[0], written[1]);
}

#[test]
fn a_part_with_recognition_draws_the_same_from_its_analysis() {
    let features = fixture("recognition/plate.json");
    let analysis = api::analyse(&fixture("parts/plate.step"), Some(&features)).unwrap();
    assert!(!analysis.features.is_empty());
    same_from_analysis(&fixture("parts/plate.step"), Some(&features));
}

#[test]
fn a_part_without_recognition_draws_the_same_from_its_analysis() {
    same_from_analysis(&fixture("parts/prismatic.step"), None);
}

/// A sampled corpus part with its recognition (GRM-03, a turned part with holes and chamfers).
#[test]
fn a_corpus_part_draws_the_same_from_its_analysis() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let step = dir.join(facts::load("grm03").binding.file);
    let features = scratch("grm03.recognition.json");
    std::fs::write(&features, corpus::recognition("grm03").unwrap()).unwrap();
    let analysis = api::analyse(&step, Some(&features)).unwrap();
    assert!(!analysis.features.is_empty());
    same_from_analysis(&step, Some(&features));
}

#[test]
fn an_analysis_of_another_file_is_refused() {
    let step = fixture("parts/plate.step");
    let other = fixture("parts/prismatic.step");
    let analysis = api::analyse(&other, None).unwrap();
    let theirs = analysis.binding.source_sha256.clone();
    let ours = api::analyse(&step, None).unwrap().binding.source_sha256;
    let refusals = [
        api::render_analysed(&step, &analysis, &Spec::default(), &["svg"], DPI).map(|_| ()),
        api::review_analysed(&step, &analysis, &Spec::default()).map(|_| ()),
        api::call(
            "plan",
            &json!({ "step": step, "analysis": serde_json::to_value(&analysis).unwrap() }),
        )
        .map(|_| ()),
        api::call(
            "review",
            &json!({ "step": step, "analysis": serde_json::to_value(&analysis).unwrap() }),
        )
        .map(|_| ()),
    ];
    for refusal in refusals {
        let e = refusal.unwrap_err();
        assert_eq!(e.code, "invalid", "{e}");
        assert!(e.message.contains(&theirs), "{e}");
        assert!(e.message.contains(&ours), "{e}");
    }

    // The same file read to a different face count is another reading, also refused.
    let mut miscounted = api::analyse(&step, None).unwrap();
    miscounted.binding.face_count += 1;
    let e = api::review_analysed(&step, &miscounted, &Spec::default()).unwrap_err();
    assert_eq!(e.code, "invalid", "{e}");
    assert!(e.message.contains("faces"), "{e}");
}

#[test]
fn an_analysis_of_another_version_is_refused() {
    let step = fixture("parts/prismatic.step");
    let mut analysis: Analysis = api::analyse(&step, None).unwrap();
    analysis.schema = "draftwright.analysis/99".into();
    for e in [
        api::render_analysed(&step, &analysis, &Spec::default(), &["svg"], DPI).unwrap_err(),
        api::review_analysed(&step, &analysis, &Spec::default()).unwrap_err(),
        api::plan_analysed(&analysis, &Spec::default()).unwrap_err(),
    ] {
        assert_eq!(e.code, "invalid", "{e}");
        assert!(e.message.contains("draftwright.analysis/99"), "{e}");
        assert!(e.choices.contains(&ANALYSIS_SCHEMA.to_string()), "{e:?}");
    }
    // Drawing combines the Analysis with a fresh reading of the part, so it takes only the
    // version this build writes (plan alone also reads version 2, as `model::plan` does).
    analysis.schema = "draftwright.analysis/2".into();
    let e = api::review_analysed(&step, &analysis, &Spec::default()).unwrap_err();
    assert_eq!(e.choices, [ANALYSIS_SCHEMA]);
}

#[test]
fn an_analysis_beside_features_is_refused() {
    let step = fixture("parts/plate.step");
    let features = fixture("recognition/plate.json");
    let analysis = api::call("analyse", &json!({ "step": step, "features": features })).unwrap();
    for op in ["plan", "render", "review"] {
        let e = api::call(
            op,
            &json!({ "step": step, "features": features, "analysis": analysis,
                "output": scratch("both").display().to_string() }),
        )
        .unwrap_err();
        assert_eq!(e.code, "invalid", "{op}: {e}");
        assert!(e.message.contains("not both"), "{op}: {e}");
    }
}

#[test]
fn a_malformed_analysis_is_refused() {
    let step = fixture("parts/prismatic.step");
    let mut document = api::call("analyse", &json!({ "step": step })).unwrap();
    document["unexpected"] = json!(1);
    let mut missing = document.clone();
    missing.as_object_mut().unwrap().remove("unexpected");
    missing.as_object_mut().unwrap().remove("binding");
    for bad in [document, missing, json!("analysis.json"), json!([1, 2])] {
        for op in ["plan", "render", "review"] {
            let e = api::call(
                op,
                &json!({ "step": step, "analysis": bad,
                    "output": scratch("malformed").display().to_string() }),
            )
            .unwrap_err();
            assert_eq!(e.code, "invalid", "{op}: {e}");
            assert!(
                e.message.starts_with("the analysis is not valid"),
                "{op}: {e}"
            );
        }
    }
    // Without an analysis or a step, plan says what it needs.
    let e = api::call("plan", &json!({})).unwrap_err();
    assert!(e.message.contains("step"), "{e}");
}

/// The plan of an Analysis with no STEP named reads no file, so it is the Analysis's alone.
#[test]
fn plan_from_an_analysis_needs_no_step() {
    let analysis = api::call("analyse", &json!({ "step": fixture("parts/turned.step") })).unwrap();
    let plan: Value = api::call("plan", &json!({ "analysis": analysis })).unwrap();
    assert_eq!(plan["schema"], "draftwright.plan/1");
    assert_eq!(plan["spec"]["binding"]["part"], "turned");
}

fn run(args: &[&str]) -> (Option<i32>, Value) {
    let out = std::process::Command::new(env!("CARGO_BIN_EXE_draftwright"))
        .args(args)
        .output()
        .unwrap();
    (
        out.status.code(),
        serde_json::from_slice(&out.stdout).unwrap(),
    )
}

/// The command line's `--analysis` names an Analysis file that `draftwright analyse` printed.
#[test]
fn the_command_line_takes_an_analysis_file() {
    let step = fixture("parts/plate.step").display().to_string();
    let features = fixture("recognition/plate.json").display().to_string();
    let (code, analysis) = run(&["analyse", &step, "--features", &features]);
    assert_eq!(code, Some(0));
    let file = scratch("plate.analysis.json");
    std::fs::write(&file, analysis.to_string()).unwrap();
    let file = file.display().to_string();
    let (_, plan) = run(&["plan", &step, "--features", &features]);
    assert_eq!(run(&["plan", "--analysis", &file]), (Some(0), plan.clone()));
    assert_eq!(run(&["plan", &step, "--analysis", &file]), (Some(0), plan));
    let (_, review) = run(&["review", &step, "--features", &features]);
    assert_eq!(
        run(&["review", &step, "--analysis", &file]),
        (Some(0), review)
    );
    let (code, both) = run(&[
        "review",
        &step,
        "--features",
        &features,
        "--analysis",
        &file,
    ]);
    assert_eq!(code, Some(2), "{both}");
    let other = fixture("parts/prismatic.step").display().to_string();
    let (code, refused) = run(&["review", &other, "--analysis", &file]);
    assert_eq!(code, Some(2), "{refused}");
    assert_eq!(refused["error"]["code"], "invalid");
}

/// Reproduce edge-mouth variants through the public --analysis path. The
/// committed NIST fixture supplies the Analysis; only its notch records change,
/// as in the reviewer's edited CTC-01 analysis (not a replacement STEP solid).
#[test]
fn edge_mouth_notches_need_no_opening_direction_location() {
    let path = fixture("pmi/step/nist_ctc_01_asme1_ap242-e1.stp.gz");
    let (_, a) = draftwright::model::analyse_native(&path).unwrap();
    for sign in [-1., 1.] {
        let mut edited = a.clone();
        let edge = edited.envelope[usize::from(sign > 0.)][1];
        let mut ids = Vec::new();
        for f in edited
            .features
            .iter_mut()
            .filter(|f| f.kind == "rectangular_through_notch")
        {
            f.parameters.insert("opening".into(), json!([0., sign, 0.]));
            let at = f.parameters.get_mut("at").unwrap().as_array_mut().unwrap();
            at[1] = json!(edge - sign * 25.);
            ids.push(f.id.clone());
        }
        assert_eq!(ids.len(), 2);
        let file = scratch(&format!("ctc01-edge-{sign}.analysis.json"));
        std::fs::write(&file, serde_json::to_vec(&edited).unwrap()).unwrap();
        let (code, plan) = run(&["plan", "--analysis", file.to_str().unwrap()]);
        assert_eq!(code, Some(0), "{plan}");
        for id in &ids {
            let measurements: Vec<_> = plan["dimensions"]
                .as_array()
                .unwrap()
                .iter()
                .flat_map(|d| d["measurements"].as_array().unwrap())
                .filter(|m| m["feature"] == *id)
                .collect();
            for role in [
                "notch_width.length",
                "notch_depth.length",
                "location.length.x",
            ] {
                assert!(
                    measurements.iter().any(|m| m["parameter"] == role),
                    "{sign}: {id} {role}"
                );
            }
            assert!(
                !measurements
                    .iter()
                    .any(|m| m["parameter"] == "location.length.y")
            );
        }
        // The independent requirement inventory must agree: the outer face
        // fixes y, while width, depth and the transverse location still matter.
        let marks: Vec<draftwright::lint::Mark> = plan["dimensions"]
            .as_array()
            .unwrap()
            .iter()
            .map(|d| draftwright::lint::Mark {
                units: Default::default(),
                name: d["id"].as_str().unwrap().into(),
                view: d["view"].as_str().map(str::to_string),
                text: d["terms"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .filter_map(Value::as_str)
                    .collect::<Vec<_>>()
                    .join(" "),
                claims: d["measurements"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|m| draftwright::output::Claim {
                        feature: m["feature"].as_str().unwrap().into(),
                        parameter: m["parameter"].as_str().unwrap().into(),
                    })
                    .collect(),
            })
            .collect();
        assert!(
            !draftwright::lint::check(&edited, &marks)
                .iter()
                .any(|issue| issue.code.starts_with("notch_requirement_")
                    && issue
                        .requirements
                        .iter()
                        .any(|r| r.parameter == "location.length.y"))
        );
    }
}

#[test]
fn analysis_references_reject_unknown_fields_with_flat_membership() {
    let analysis = api::analyse(&fixture("parts/plate.step"), None).unwrap();
    let base = serde_json::to_value(analysis).unwrap();
    // Cover each variant, including legacy documents without membership metadata.
    for reference in [
        json!({"kind":"plane","origin":[0,0,0],"normal":[0,0,1],"bounds":[[0,0,0],[1,1,0]]}),
        json!({"kind":"cylinder","origin":[0,0,0],"axis":[0,0,1],"radius":1,"bounds":[[-1,-1,0],[1,1,2]],"sites":[[1,0,0]]}),
        json!({"kind":"sphere","centre":[0,0,0],"radius":1,"bounds":[[-1,-1,-1],[1,1,1]],"sites":[[1,0,0]]}),
        json!({"kind":"edge","ends":[[0,0,0],[1,0,0]]}),
        json!({"kind":"vertex","point":[0,0,0]}),
    ] {
        let mut value = base.clone();
        value["edge_ink"]["references"] = json!({"face:v1:read-contract":[reference]});
        let loaded: Analysis = serde_json::from_value(value).unwrap();
        let mut value = serde_json::to_value(loaded).unwrap();
        let record = &mut value["edge_ink"]["references"]["face:v1:read-contract"][0];
        assert!(
            record.get("membership").is_none(),
            "membership remains flat"
        );
        for field in ["features", "stock_faces", "stock_sides", "faces"] {
            assert!(record[field].is_array());
        }
        record["unexpected_reference_field"] = json!(true);
        let error = serde_json::from_value::<Analysis>(value)
            .unwrap_err()
            .to_string();
        assert!(
            error.contains("unknown field `unexpected_reference_field`"),
            "{error}"
        );
    }
}
