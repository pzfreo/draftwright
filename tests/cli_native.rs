//! The command line takes its features from Rust quiddity's recognition (`bridge::native`) unless
//! told otherwise: the Analysis names that recogniser, `--features` still reads Python quiddity's
//! JSON, `--no-features` draws without features, a refusal is reported and nothing drawn, and
//! the families the native recognition does not give are named in the plan's problems.

#[path = "common/corpus.rs"]
mod corpus;

use std::path::{Path, PathBuf};
use std::process::Command;

use draftwright::api::{self, Features};
use draftwright::bridge::native;
use draftwright::model::{self, ANALYSIS_SCHEMA, NOT_IN_NATIVE_DOCUMENT};
use serde_json::{Value, json};

const FIXTURES: [&str; 5] = ["boss_circle", "plate", "prismatic", "turned", "two_grids"];

fn fixture(path: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(path)
}

fn scratch(name: &str) -> PathBuf {
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR")).join("cli_native");
    std::fs::create_dir_all(&dir).unwrap();
    dir.join(name)
}

/// The command line's exit code and the JSON it printed.
fn run(args: &[&str]) -> (Option<i32>, Value) {
    let out = Command::new(env!("CARGO_BIN_EXE_draftwright"))
        .args(args)
        .output()
        .unwrap();
    let json = serde_json::from_slice(&out.stdout)
        .unwrap_or_else(|e| panic!("{args:?}: {e}: {}", String::from_utf8_lossy(&out.stdout)));
    (out.status.code(), json)
}

fn recogniser(analysis: &Value) -> &str {
    analysis["recognition"]["recogniser"].as_str().unwrap()
}

/// The families an Analysis or a problem names as not recognised.
fn unrecognised(analysis: &Value) -> Vec<String> {
    analysis["unrecognised"].as_array().map_or(Vec::new(), |u| {
        u.iter()
            .map(|u| u["family"].as_str().unwrap().to_string())
            .collect()
    })
}

fn not_recognised(problems: &Value) -> Option<&Value> {
    problems
        .as_array()
        .unwrap()
        .iter()
        .find(|p| p["code"] == "features_not_recognised")
}

/// What the native recognition cannot give on any part: the families its document does not
/// carry and the native reader does not read.
fn never_native() -> Vec<String> {
    NOT_IN_NATIVE_DOCUMENT
        .iter()
        .filter(|f| !native::READ.contains(f))
        .map(|f| f.to_string())
        .collect()
}

#[test]
fn without_features_the_command_line_recognises_with_rust_quiddity() {
    for name in FIXTURES {
        let step = fixture(&format!("parts/{name}.step"));
        let step = step.to_str().unwrap();
        let (code, a) = run(&["analyse", step]);
        assert_eq!(code, Some(0), "{name}: {a}");
        assert_eq!(a["schema"], ANALYSIS_SCHEMA, "{name}");
        assert!(
            recogniser(&a).starts_with("quiddity-rust "),
            "{name}: {}",
            recogniser(&a)
        );
        assert_eq!(
            a["recognition"]["frame"],
            serde_json::to_value(native::IDENTITY).unwrap(),
            "{name}"
        );
        // The very Analysis the library's native entry gives.
        let typed = api::analyse_from(Path::new(step), Features::Native).unwrap();
        assert_eq!(a, serde_json::to_value(&typed).unwrap(), "{name}");
        for family in never_native() {
            assert!(unrecognised(&a).contains(&family), "{name}: {family}");
        }
        assert!(
            unrecognised(&a)
                .iter()
                .all(|f| !native::READ.contains(&f.as_str())),
            "{name}"
        );

        let (code, plan) = run(&["plan", step]);
        assert_eq!(code, Some(0), "{name}: {plan}");
        let warning = not_recognised(&plan["problems"]);
        assert_eq!(warning.is_some(), !unrecognised(&a).is_empty(), "{name}");
        if let Some(p) = warning {
            assert_eq!(p["severity"], "warning");
            let refs = p["refs"].as_array().unwrap();
            for family in unrecognised(&a) {
                assert!(refs.contains(&json!(family)), "{name}: {p}");
            }
            assert!(refs.contains(&json!(recogniser(&a))), "{name}: {p}");
            assert!(
                !p["message"].as_str().unwrap().contains(recogniser(&a)),
                "{name}: {p}"
            );
            assert!(
                p["message"]
                    .as_str()
                    .unwrap()
                    .contains("feature_declarations")
            );
        }
    }
    // The plate's four holes and their grid, as Python's recognition has them.
    let (_, a) = run(&["analyse", fixture("parts/plate.step").to_str().unwrap()]);
    let holes: Vec<&str> = a["features"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|f| f["kind"] == "hole")
        .map(|f| f["id"].as_str().unwrap())
        .collect();
    assert_eq!(holes, ["f:hole:f6", "f:hole:f7", "f:hole:f8", "f:hole:f9"]);
    assert_eq!(a["recognition"]["patterns"][0]["arrangement"], "grid");
}

#[test]
fn features_still_takes_python_quidditys_json_and_no_features_none() {
    let step = fixture("parts/plate.step");
    let features = fixture("recognition/plate.json");
    let (step, features) = (step.to_str().unwrap(), features.to_str().unwrap());
    let (code, a) = run(&["analyse", step, "--features", features]);
    assert_eq!(code, Some(0));
    assert!(
        recogniser(&a).ends_with(" via tools/recognise.py"),
        "{}",
        recogniser(&a)
    );
    assert!(a.get("unrecognised").is_none(), "{a}");
    assert_eq!(
        a,
        serde_json::to_value(api::analyse(Path::new(step), Some(Path::new(features))).unwrap())
            .unwrap()
    );
    let (code, plan) = run(&["plan", step, "--features", features]);
    assert_eq!(code, Some(0));
    assert!(not_recognised(&plan["problems"]).is_none());

    // No features: what the library gives when it is given none.
    let (code, a) = run(&["analyse", step, "--no-features"]);
    assert_eq!(code, Some(0));
    assert!(a.get("recognition").is_none(), "{a}");
    assert_eq!(a["features"], Value::Array(Vec::new()));
    assert_eq!(
        a,
        serde_json::to_value(api::analyse(Path::new(step), None).unwrap()).unwrap()
    );
    for both in [
        ["analyse", step, "--no-features", "--features", features],
        ["plan", step, "--no-features", "--analysis", features],
    ] {
        let (code, e) = run(&both);
        assert_eq!(code, Some(2), "{both:?}: {e}");
        assert_eq!(e["error"]["code"], "invalid");
    }
}

/// A native Analysis passed back as `--analysis` draws what analysing again draws; one of the
/// previous schema is planned (read with none of what it lacks) but not drawn from.
#[test]
fn a_native_analysis_is_taken_back_and_an_older_one_refused_for_drawing() {
    let step = fixture("parts/two_grids.step");
    let step = step.to_str().unwrap();
    let (_, analysis) = run(&["analyse", step]);
    let file = scratch("two_grids.analysis.json");
    std::fs::write(&file, analysis.to_string()).unwrap();
    let file = file.to_str().unwrap();
    let (code, plan) = run(&["plan", step]);
    assert_eq!(code, Some(0));
    assert_eq!(run(&["plan", step, "--analysis", file]), (Some(0), plan));
    let (_, review) = run(&["review", step]);
    assert_eq!(
        run(&["review", step, "--analysis", file]),
        (Some(0), review)
    );

    let mut old = analysis.clone();
    old["schema"] = "draftwright.analysis/4".into();
    old.as_object_mut().unwrap().remove("unrecognised");
    let file = scratch("two_grids.analysis4.json");
    std::fs::write(&file, old.to_string()).unwrap();
    let file = file.to_str().unwrap();
    let (code, _) = run(&["plan", "--analysis", file]);
    assert_eq!(code, Some(0));
    let (code, e) = run(&["review", step, "--analysis", file]);
    assert_eq!(code, Some(2), "{e}");
    assert_eq!(e["error"]["choices"][0], ANALYSIS_SCHEMA);
}

/// A recognition document the native reader refuses is reported, never read as a part without
/// features; and the command line reports a part it cannot analyse and writes nothing.
#[test]
fn a_refusal_is_reported_not_drawn_without_features() {
    let (part, mut analysis) = model::analyse(&fixture("parts/plate.step")).unwrap();
    let recognition = quiddity::correspondence::recognise(&part);
    let mut doc = quiddity::recognition::document(&recognition, part.faces.len());
    doc.schema_version = "quiddity-rust/recognition/0".into();
    let e = model::recognise_document(&part, &mut analysis, &doc).unwrap_err();
    assert!(e.contains("quiddity-rust/recognition/0"), "{e}");
    assert!(analysis.recognition.is_none() && analysis.features.is_empty());
    assert!(analysis.unrecognised.is_empty());

    let out = scratch("unresolved");
    let (code, e) = run(&[
        "render",
        fixture("parts/unresolved.step").to_str().unwrap(),
        "-o",
        out.to_str().unwrap(),
    ]);
    assert_eq!(code, Some(1), "{e}");
    assert_eq!(e["ok"], false);
    assert!(
        e["error"]["message"]
            .as_str()
            .unwrap()
            .contains("unresolved.step"),
        "{e}"
    );
    assert!(!out.with_extension("svg").exists());
}

/// Two corpus parts rendered as the command line renders them by default: drawn, with every
/// family the native document has and the reader does not read named, with its count.
#[test]
fn corpus_parts_render_natively_naming_what_is_not_recognised() {
    let Some(dir) = corpus::dir() else {
        eprintln!("skipped: corpus not found");
        return;
    };
    for (name, file) in [
        ("cgb245", "cadgenbench_inputs/cgb245.step"),
        ("grm05", "gramel/GRM-05_depth_lock_bolt.step"),
    ] {
        let step = dir.join(file);
        let (part, _) = model::analyse(&step).unwrap();
        let recognition = quiddity::correspondence::recognise(&part);
        let doc = quiddity::recognition::document(&recognition, part.faces.len());
        let mut expected = never_native();
        let mut counted = std::collections::BTreeMap::<String, usize>::new();
        for r in doc
            .records
            .iter()
            .filter(|r| !native::represented(&r.family))
        {
            *counted.entry(r.family.clone()).or_default() += 1;
        }
        expected.retain(|f| !counted.contains_key(f));
        expected.extend(counted.keys().cloned());

        let step = step.to_str().unwrap();
        let (code, a) = run(&["analyse", step]);
        assert_eq!(code, Some(0), "{name}: {a}");
        assert!(recogniser(&a).starts_with("quiddity-rust "), "{name}");
        assert!(!a["features"].as_array().unwrap().is_empty(), "{name}");
        assert_eq!(unrecognised(&a), expected, "{name}");
        if expected.is_empty() {
            assert!(a.get("unrecognised").is_none(), "{name}: {a}");
        }
        for u in a["unrecognised"].as_array().into_iter().flatten() {
            let family = u["family"].as_str().unwrap();
            assert_eq!(
                u.get("records").and_then(Value::as_u64),
                counted.get(family).map(|&n| n as u64),
                "{name}: {family}"
            );
        }

        let out = scratch(name);
        let (code, r) = run(&["render", step, "-o", out.to_str().unwrap()]);
        assert_eq!(code, Some(0), "{name}: {r}");
        assert_eq!(r["written"].as_array().unwrap().len(), 2, "{name}");
        let warning = not_recognised(&r["problems"]);
        assert_eq!(warning.is_some(), !expected.is_empty(), "{name}");
        if let Some(p) = warning {
            let refs = p["refs"].as_array().unwrap();
            for family in &expected {
                assert!(refs.contains(&json!(family)), "{name}: {p}");
            }
            assert!(refs.contains(&a["recognition"]["recogniser"]));
        }

        // With Python's JSON, its own features and no such problem.
        if let Some(json) = sheet_recognition(name) {
            let (code, r) = run(&[
                "render",
                step,
                "--features",
                json.to_str().unwrap(),
                "-o",
                out.to_str().unwrap(),
            ]);
            assert_eq!(code, Some(0), "{name}: {r}");
            assert!(not_recognised(&r["problems"]).is_none(), "{name}");
        }
    }
}

/// Python quiddity's recognition of sampled sheet part *name*, unzipped to a scratch file.
fn sheet_recognition(name: &str) -> Option<PathBuf> {
    use std::io::Read as _;
    let file = std::fs::File::open(fixture(&format!("recognition/sheets/{name}.json.gz"))).ok()?;
    let mut text = String::new();
    flate2::read::GzDecoder::new(file)
        .read_to_string(&mut text)
        .unwrap();
    let path = scratch(&format!("{name}.features.json"));
    std::fs::write(&path, text).unwrap();
    Some(path)
}

/// The command line is clap's (#345): `completions` prints a script for each shell, every
/// subcommand bash selects has its case, and a bad command line is clap's usage error (exit 2,
/// on stderr), while a bad value is still refused as JSON.
#[test]
fn the_command_line_completes_and_refuses_bad_usage() {
    let raw = |args: &[&str]| {
        let out = Command::new(env!("CARGO_BIN_EXE_draftwright"))
            .args(args)
            .output()
            .unwrap();
        (
            out.status.code(),
            String::from_utf8(out.stdout).unwrap(),
            String::from_utf8(out.stderr).unwrap(),
        )
    };
    let (code, help, _) = raw(&["--help"]);
    assert_eq!(code, Some(0));
    for command in [
        "analyse",
        "plan",
        "render",
        "review",
        "edit",
        "spec",
        "parts",
        "pack",
        "diff",
        "spec_schema",
        "item_options",
        "serve",
        "mcp-stdio",
        "mcp-http",
        "completions",
    ] {
        assert!(help.contains(&format!("  {command} ")), "{command}: {help}");
    }
    for shell in ["bash", "zsh", "fish", "powershell", "elvish"] {
        let (code, script, stderr) = raw(&["completions", shell]);
        assert_eq!((code, stderr.as_str()), (Some(0), ""), "{shell}");
        assert!(script.contains("draftwright"), "{shell}");
        assert!(script.contains("no-features"), "{shell}");
    }
    let (_, bash, _) = raw(&["completions", "bash"]);
    let selected: Vec<&str> = bash
        .lines()
        .filter_map(|l| l.trim().strip_prefix("cmd=\""))
        .filter_map(|l| l.strip_suffix('"'))
        .filter(|cmd| !cmd.is_empty())
        .collect();
    assert!(selected.len() > 10, "{selected:?}");
    for cmd in selected {
        assert!(
            bash.contains(&format!("\n        {cmd})\n")),
            "no case for {cmd}"
        );
    }
    for usage in [
        &["completions", "tcsh"][..],
        &["--bogus"],
        &[],
        &["render", "part.step"],
        &["plan"],
        &["analyse", "part.step", "--unknown"],
    ] {
        let (code, stdout, stderr) = raw(usage);
        assert_eq!(code, Some(2), "{usage:?}");
        assert!(stdout.is_empty(), "{usage:?}: {stdout}");
        assert!(
            stderr.starts_with("error: ") || stderr.contains("Usage: draftwright"),
            "{usage:?}: {stderr}"
        );
    }
    // Values that look like negative numbers reach the command's own check.
    let step = fixture("parts/plate.step");
    let (code, e) = run(&["plan", step.to_str().unwrap(), "--part", "-1"]);
    assert_eq!(code, Some(2));
    assert!(
        e["error"]["message"]
            .as_str()
            .unwrap()
            .contains("is not a part's index"),
        "{e}"
    );
}

#[test]
fn bare_form_renders_a_fixture_and_preserves_the_common_spec_fields() {
    let dir = scratch("bare-form");
    let (code, result) = run(&[
        fixture("parts/plate.step").to_str().unwrap(),
        "--out-dir",
        dir.to_str().unwrap(),
        "--title",
        "Mounting plate",
        "--number",
        "D-42",
        "--material",
        "Steel",
        "--drawn-by",
        "Paul",
        "--revision",
        "B",
        "--date",
        "2026-11-09",
        "--company",
        "Example",
        "--approved-by",
        "Reviewer",
        "--tolerance",
        "ISO 2768-m",
        "--scale",
        "0.5",
        "--frame",
    ]);
    assert_eq!(code, Some(0), "{result}");
    assert_eq!(result["written"].as_array().unwrap().len(), 2);
    assert!(
        std::fs::read(dir.join("plate.pdf"))
            .unwrap()
            .starts_with(b"%PDF-")
    );
    let svg = dir.join("plate.svg");
    assert!(std::fs::read_to_string(&svg).unwrap().contains("<svg"));
    let (code, spec) = run(&["spec", svg.to_str().unwrap()]);
    assert_eq!(code, Some(0), "{spec}");
    for (key, value) in [
        ("title", "Mounting plate"),
        ("number", "D-42"),
        ("material", "Steel"),
        ("drawn_by", "Paul"),
        ("revision", "B"),
        ("date", "2026-11-09"),
        ("company", "Example"),
        ("approved_by", "Reviewer"),
        ("tolerance", "ISO 2768-m"),
    ] {
        assert_eq!(spec["sheet"]["title"][key], value, "{spec}");
    }
    assert_eq!(spec["sheet"]["scale"], "1:2", "{spec}");
}

#[test]
fn bare_form_refuses_old_options_with_migration_guidance() {
    for (option, hint) in [
        ("--document-type", "sheet.title.document_type"),
        ("--sheet", "sheet.title.sheet"),
        ("--margin-left", "no Rust equivalent"),
        ("--margin-right", "no Rust equivalent"),
        ("--margin-top", "no Rust equivalent"),
        ("--margin-bottom", "no Rust equivalent"),
        ("--title-block-width", "no Rust equivalent"),
        ("--projection", "sheet.projection"),
        ("--projection-symbol", "no Rust equivalent"),
        ("--no-projection-symbol", "no Rust equivalent"),
        ("--text-position", "no Rust equivalent"),
        ("--text-orientation", "no Rust equivalent"),
        ("--leader-region", "no Rust equivalent"),
        ("--annotation-layout", "no Rust equivalent"),
        ("--zones", "sheet.zones"),
        ("--scale-policy", "no Rust equivalent"),
        ("--page", "sheet.size"),
        ("--script", "no Rust equivalent"),
        ("--style", "no Rust equivalent"),
        ("--pmi", "pmi"),
        ("--format", "render --formats"),
        ("-f", "render --formats"),
        ("--no-report", "no Rust equivalent"),
        ("--no-progress", "no Rust equivalent"),
        ("--verbose", "no Rust equivalent"),
        ("-v", "no Rust equivalent"),
        ("--install-completion", "completions <shell>"),
        ("--show-completion", "completions <shell>"),
    ] {
        let out = Command::new(env!("CARGO_BIN_EXE_draftwright"))
            .args(["part.step", option])
            .output()
            .unwrap();
        assert_eq!(out.status.code(), Some(2), "{option}");
        let stderr = String::from_utf8(out.stderr).unwrap();
        assert!(stderr.contains(option) && stderr.contains(hint), "{stderr}");
        assert!(out.stdout.is_empty());
    }
    // A missing bare path is accepted syntactically, then refused by the engine.
    let (code, error) = run(&["bogus"]);
    assert_eq!(code, Some(2), "{error}");
    assert!(!error["error"]["message"].as_str().unwrap().is_empty());
}

#[test]
fn a_file_named_render_requires_dot_slash_and_renders_with_it() {
    let dir = scratch("subcommand-path");
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::copy(fixture("parts/plate.step"), dir.join("render")).unwrap();
    let invoke = |args: &[&str]| {
        Command::new(env!("CARGO_BIN_EXE_draftwright"))
            .current_dir(&dir)
            .args(args)
            .output()
            .unwrap()
    };
    let subcommand = invoke(&["render"]);
    assert_eq!(subcommand.status.code(), Some(2));
    let stderr = String::from_utf8(subcommand.stderr).unwrap();
    assert!(stderr.contains("<STEP>"), "{stderr}");
    let bare = invoke(&["./render", "--out", "drawing"]);
    assert!(
        bare.status.success(),
        "{}",
        String::from_utf8_lossy(&bare.stdout)
    );
    assert!(dir.join("drawing.svg").is_file());
    assert!(dir.join("drawing.pdf").is_file());
}

#[test]
fn both_commands_report_engine_and_linked_quiddity_and_mcp_has_help() {
    for bin in [
        env!("CARGO_BIN_EXE_draftwright"),
        env!("CARGO_BIN_EXE_draftwright-mcp"),
    ] {
        let out = Command::new(bin).arg("--version").output().unwrap();
        assert!(out.status.success());
        let version = String::from_utf8(out.stdout).unwrap();
        assert!(version.contains(env!("CARGO_PKG_VERSION")), "{version}");
        let capabilities: Value =
            serde_json::from_str(&quiddity::file_api::capabilities()).unwrap();
        let quiddity = capabilities["package"]["version"].as_str().unwrap();
        assert!(
            version.contains(&format!("quiddity-rust {quiddity}")),
            "{version}"
        );
    }
    let out = Command::new(env!("CARGO_BIN_EXE_draftwright-mcp"))
        .arg("--help")
        .output()
        .unwrap();
    assert!(out.status.success());
    assert!(
        String::from_utf8(out.stdout)
            .unwrap()
            .contains("Usage: draftwright-mcp")
    );
}

#[test]
fn help_is_a_subcommand_and_extra_paths_do_not_get_python_option_hints() {
    let out = Command::new(env!("CARGO_BIN_EXE_draftwright"))
        .arg("help")
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(
        String::from_utf8(out.stdout)
            .unwrap()
            .contains("Usage: draftwright")
    );
    let out = Command::new(env!("CARGO_BIN_EXE_draftwright"))
        .args(["rendr", "x.step"])
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(2));
    let stderr = String::from_utf8(out.stderr).unwrap();
    assert!(stderr.contains("unexpected argument 'x.step'"), "{stderr}");
    assert!(!stderr.contains("Python option"), "{stderr}");
}

#[test]
fn mcp_client_arguments_are_ignored_while_stdio_remains_live() {
    use std::io::Write;
    use std::process::Stdio;
    for args in [
        vec!["--unknown"],
        vec!["--transport", "stdio", "ignored-value"],
    ] {
        let mut child = Command::new(env!("CARGO_BIN_EXE_draftwright-mcp"))
            .args(args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        writeln!(
            child.stdin.take().unwrap(),
            "{}",
            json!({
                "jsonrpc": "2.0", "id": 42, "method": "tools/list"
            })
        )
        .unwrap();
        let out = child.wait_with_output().unwrap();
        assert!(
            out.status.success(),
            "{}",
            String::from_utf8_lossy(&out.stderr)
        );
        assert!(out.stderr.is_empty());
        let reply: Value = serde_json::from_slice(&out.stdout).unwrap();
        assert_eq!(reply["id"], 42);
        assert!(
            reply["result"]["tools"].as_array().unwrap().len() > 5,
            "{reply}"
        );
    }
}
