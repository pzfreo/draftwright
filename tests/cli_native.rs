//! The command line takes its features from Rust quiddity's recognition (`bridge::native`) unless
//! told otherwise: the Analysis names that recogniser, `--features` still reads Python quiddity's
//! JSON, `--no-features` draws without features, a refusal is reported and nothing drawn, and
//! the families the native recognition does not give are named in the plan's problems.

#[path = "common/corpus.rs"]
mod corpus;

#[path = "common/cli.rs"]
mod cli;

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
    let json = serde_json::from_slice(&out.stdout).unwrap_or_else(|e| {
        panic!(
            "{args:?}: {e}\nstdout: {}\nstderr: {}",
            String::from_utf8_lossy(&out.stdout),
            String::from_utf8_lossy(&out.stderr)
        )
    });
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
        let e = cli::failure(&both, 2);
        assert!(e.contains("--no-features is not taken with"), "{e}");
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
    let e = cli::failure(&["review", step, "--analysis", file], 2);
    assert!(
        e.contains(&format!("\n  choose from: {ANALYSIS_SCHEMA}\n")),
        "{e}"
    );
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
    let e = cli::failure(
        &[
            "render",
            fixture("parts/unresolved.step").to_str().unwrap(),
            "-o",
            out.to_str().unwrap(),
        ],
        1,
    );
    assert!(e.contains("unresolved.step"), "{e}");
    assert!(!out.with_extension("svg").exists());
}

#[test]
fn file_errors_are_text_on_stderr_for_one_shot_commands() {
    let missing_step = scratch("missing.step");
    let missing_spec = scratch("missing-spec.json");
    let missing_drawing = scratch("missing.svg");
    let not_step = scratch("not.step");
    std::fs::write(&not_step, "This is not a STEP file.\n").unwrap();
    let output = scratch("invalid-output");
    let step = missing_step.to_str().unwrap();
    let spec = missing_spec.to_str().unwrap();
    let drawing = missing_drawing.to_str().unwrap();
    let not_step = not_step.to_str().unwrap();
    let output = output.to_str().unwrap();
    for (args, name, reason) in [
        (vec![step], step, "cannot read"),
        (vec!["render", step, "-o", output], step, "cannot read"),
        (vec![not_step], not_step, "as STEP: STEP parse error"),
        (vec!["parts", step], step, "cannot read"),
        (vec!["spec", drawing], drawing, "cannot read"),
        (vec!["edit", spec, "unused-ops.json"], spec, "cannot read"),
    ] {
        let e = cli::failure(&args, 2);
        assert!(e.contains(name) && e.contains(reason), "{args:?}: {e}");
        assert_eq!(e.lines().count(), 1, "{e}");
    }
    assert!(!Path::new(output).with_extension("svg").exists());
}

#[test]
fn refusals_print_choices_and_the_spec_entry_on_separate_lines() {
    let spec = scratch("error-spec.json");
    let ops = scratch("error-ops.json");
    std::fs::write(&spec, "{}").unwrap();
    std::fs::write(&ops, r#"[{"op":"bogus","path":"sheet/size","value":"A4"}]"#).unwrap();
    let e = cli::failure(&["edit", spec.to_str().unwrap(), ops.to_str().unwrap()], 2);
    assert_eq!(
        e,
        "error: \"bogus\" is not an edit.\n  choose from: set, remove, add\n"
    );

    std::fs::write(&spec, r#"{"views":{"gone":{"omit":true}}}"#).unwrap();
    let e = cli::failure(
        &[
            "plan",
            fixture("parts/plate.step").to_str().unwrap(),
            "--spec",
            spec.to_str().unwrap(),
        ],
        2,
    );
    assert!(
        e.starts_with("error: There is no view called \"gone\";"),
        "{e}"
    );
    assert_eq!(
        e.lines().skip(1).collect::<Vec<_>>(),
        [
            "  choose from: front, plan, side, iso, section_aa, iso_below",
            "  entry: views/gone",
        ]
    );
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
/// on stderr), while a bad value is also refused as text on stderr.
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
    let e = cli::failure(&["plan", step.to_str().unwrap(), "--part", "-1"], 2);
    assert!(e.contains("is not a part's index"), "{e}");
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
    let error = cli::failure(&["bogus"], 2);
    assert!(error.contains("cannot read bogus:"), "{error}");
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

#[test]
fn pictorial_flags_select_each_view_and_accept_both_hex_spellings() {
    let step = fixture("parts/plate.step");
    let step = step.to_str().unwrap();
    for (flags, top, bottom, colour) in [
        (vec!["--shaded"], Some("shaded"), None, None),
        (vec!["--below"], None, Some("wireframe"), None),
        (
            vec!["--shaded", "--below"],
            Some("shaded"),
            Some("shaded"),
            None,
        ),
        (
            vec!["--shaded", "--colour", "#b8c4cc"],
            Some("shaded"),
            None,
            Some([184, 196, 204]),
        ),
        (
            vec!["--shaded", "--colour", "A1B2C3"],
            Some("shaded"),
            None,
            Some([161, 178, 195]),
        ),
        (
            vec!["--shaded", "--below", "--color", "0123ab"],
            Some("shaded"),
            Some("shaded"),
            Some([1, 35, 171]),
        ),
    ] {
        let mut args = vec!["plan", step, "--no-features"];
        args.extend(flags);
        let (code, plan) = run(&args);
        assert_eq!(code, Some(0), "{args:?}: {plan}");
        assert_eq!(plan["spec"]["views"]["iso"]["style"].as_str(), top);
        assert_eq!(plan["spec"]["views"]["iso_below"]["style"].as_str(), bottom);
        assert_eq!(
            plan["views"]
                .as_array()
                .unwrap()
                .iter()
                .any(|v| v["id"] == "v:iso_below"),
            bottom.is_some()
        );
        if let Some(colour) = colour {
            assert_eq!(plan["spec"]["views"]["iso"]["colour"], json!(colour));
            if bottom.is_some() {
                assert_eq!(plan["spec"]["views"]["iso_below"]["colour"], json!(colour));
            }
        }
    }
}

#[test]
fn pictorial_flags_override_spec_and_are_embedded_by_every_cli_form() {
    let dir = scratch("pictorial-overrides");
    std::fs::create_dir_all(&dir).unwrap();
    let file = dir.join("base.json");
    std::fs::write(&file, json!({
        "sheet":{"title":{"title":"Base title","number":"D-CLI"}},
        "views":{"iso":{"style":"wireframe","colour":[9,8,7],"direction":[2.,-1.,1.]},
                 "iso_below":{"omit":true,"style":"wireframe","colour":[7,8,9],"direction":[-1.,1.,-1.]}}
    }).to_string()).unwrap();
    let step = fixture("parts/plate.step");
    let check = |spec: &Value| {
        assert_eq!(spec["sheet"]["title"]["title"], "Base title");
        assert_eq!(spec["sheet"]["title"]["number"], "D-CLI");
        for name in ["iso", "iso_below"] {
            assert_eq!(spec["views"][name]["style"], "shaded", "{spec}");
            assert_eq!(
                spec["views"][name]["colour"],
                json!([161, 178, 195]),
                "{spec}"
            );
        }
        assert_ne!(spec["views"]["iso_below"]["omit"], json!(true));
        assert_eq!(spec["views"]["iso"]["direction"], json!([2., -1., 1.]));
        assert_eq!(
            spec["views"]["iso_below"]["direction"],
            json!([-1., 1., -1.])
        );
    };
    for form in ["plan", "review", "render", "bare"] {
        let prefix = dir.join(form);
        let mut args = if form == "bare" {
            vec![step.to_str().unwrap(), "--out", prefix.to_str().unwrap()]
        } else {
            vec![form, step.to_str().unwrap(), "--no-features"]
        };
        if form == "render" {
            args.extend([
                "-o",
                prefix.to_str().unwrap(),
                "--formats",
                "svg,pdf,png",
                "--dpi",
                "36",
            ]);
        }
        args.extend([
            "--spec",
            file.to_str().unwrap(),
            "--shaded",
            "--below",
            "--colour",
            "#a1B2c3",
        ]);
        let (code, result) = run(&args);
        assert_eq!(code, Some(0), "{form}: {result}");
        match form {
            "plan" => check(&result["spec"]),
            "review" => {
                let svg = result["svg"].as_str().unwrap();
                assert!(svg.contains("shaded-iso-view") && svg.contains("shaded-iso_below-view"));
                let spec = model::embedded_spec(svg.as_bytes()).unwrap().unwrap();
                check(&serde_json::to_value(spec).unwrap());
            }
            _ => {
                let formats: &[&str] = if form == "bare" {
                    &["svg", "pdf"]
                } else {
                    &["svg", "pdf", "png"]
                };
                for format in formats {
                    let output = prefix.with_extension(format);
                    let (code, spec) = run(&["spec", output.to_str().unwrap()]);
                    assert_eq!(code, Some(0), "{output:?}: {spec}");
                    check(&spec);
                }
            }
        }
    }
}

#[test]
fn below_requests_lines_and_shaded_alone_keeps_other_view_settings() {
    let file = scratch("pictorial-selected-views.json");
    std::fs::write(
        &file,
        json!({"views":{
            "iso":{"style":"wireframe","omit":true,"colour":[9,8,7]},
            "iso_below":{"style":"shaded","omit":true,"colour":[7,8,9]}
        }})
        .to_string(),
    )
    .unwrap();
    let step = fixture("parts/plate.step");
    let base = [
        "plan",
        step.to_str().unwrap(),
        "--no-features",
        "--spec",
        file.to_str().unwrap(),
    ];
    let mut args = base.to_vec();
    args.push("--below");
    let (code, plan) = run(&args);
    assert_eq!(code, Some(0), "{plan}");
    assert_eq!(plan["spec"]["views"]["iso"]["style"], "wireframe");
    assert_eq!(plan["spec"]["views"]["iso"]["omit"], true);
    assert_eq!(plan["spec"]["views"]["iso_below"]["style"], "wireframe");
    assert_ne!(plan["spec"]["views"]["iso_below"]["omit"], true);
    assert!(
        plan["views"]
            .as_array()
            .unwrap()
            .iter()
            .any(|v| v["id"] == "v:iso_below")
    );
    // A style shortcut edits only style/colour, not an authored omission or an unselected view.
    let mut args = base.to_vec();
    args.extend(["--shaded", "--colour", "abcdef"]);
    let (code, plan) = run(&args);
    assert_eq!(code, Some(0), "{plan}");
    assert_eq!(plan["spec"]["views"]["iso"]["style"], "shaded");
    assert_eq!(plan["spec"]["views"]["iso"]["omit"], true);
    assert_eq!(
        plan["spec"]["views"]["iso"]["colour"],
        json!([171, 205, 239])
    );
    assert_eq!(
        plan["spec"]["views"]["iso_below"]["colour"],
        json!([7, 8, 9])
    );
    assert_eq!(plan["spec"]["views"]["iso_below"]["omit"], true);
}

#[test]
fn pictorial_colour_errors_are_clear_and_precede_step_io() {
    for alias in ["--colour", "--color"] {
        let e = cli::failure(&["plan", "does-not-exist.step", alias, "b8c4cc"], 2);
        assert!(e.contains("requires --shaded"), "{e}");
        for value in [
            "123",
            "#12345",
            "1234567",
            "#gggggg",
            "ééé",
            "-1",
            "#12345678",
        ] {
            let e = cli::failure(
                &["plan", "does-not-exist.step", "--shaded", alias, value],
                2,
            );
            assert!(e.contains("six hexadecimal digits"), "{e}");
        }
    }
    let file = scratch("pictorial-already-shaded.json");
    std::fs::write(&file, r#"{"views":{"iso":{"style":"shaded"}}}"#).unwrap();
    let e = cli::failure(
        &[
            "plan",
            "does-not-exist.step",
            "--spec",
            file.to_str().unwrap(),
            "--colour",
            "b8c4cc",
        ],
        2,
    );
    assert!(e.contains("requires --shaded"), "{e}");
}

#[test]
fn pictorial_help_lists_flags_and_color_alias_on_every_form() {
    for args in [
        vec!["--help"],
        vec!["render", "--help"],
        vec!["review", "--help"],
        vec!["plan", "--help"],
        vec!["part.step", "--help"],
    ] {
        let out = Command::new(env!("CARGO_BIN_EXE_draftwright"))
            .args(&args)
            .output()
            .unwrap();
        assert!(
            out.status.success(),
            "{args:?}: {}",
            String::from_utf8_lossy(&out.stderr)
        );
        let help = String::from_utf8(out.stdout).unwrap();
        for flag in ["--shaded", "--below", "--colour", "--color"] {
            assert!(help.contains(flag), "{args:?}: {help}");
        }
        assert!(help.contains("Shade the isometric view"), "{help}");
        assert!(help.contains("Add a view from below"), "{help}");
    }
}

#[test]
fn pictorial_plan_from_analysis_accepts_flags_without_a_step_argument() {
    let analysis = api::analyse(&fixture("parts/plate.step"), None).unwrap();
    let file = scratch("pictorial.analysis.json");
    std::fs::write(&file, serde_json::to_vec(&analysis).unwrap()).unwrap();
    let (code, plan) = run(&[
        "plan",
        "--analysis",
        file.to_str().unwrap(),
        "--shaded",
        "--below",
        "--color",
        "#112233",
    ]);
    assert_eq!(code, Some(0), "{plan}");
    for name in ["iso", "iso_below"] {
        assert_eq!(plan["spec"]["views"][name]["style"], "shaded");
        assert_eq!(plan["spec"]["views"][name]["colour"], json!([17, 34, 51]));
    }
}

#[test]
fn no_pictorial_flags_keep_cli_output_equal_to_the_default_library_spec() {
    let step = fixture("parts/plate.step");
    let prefix = scratch("pictorial-default");
    let (code, result) = run(&[
        "render",
        step.to_str().unwrap(),
        "--no-features",
        "-o",
        prefix.to_str().unwrap(),
        "--formats",
        "svg,pdf,png,dxf",
    ]);
    assert_eq!(code, Some(0), "{result}");
    let (_, files) = api::render(&step, None, &model::Spec::default(), &api::FORMATS).unwrap();
    for (format, bytes) in files {
        assert_eq!(
            std::fs::read(prefix.with_extension(&format)).unwrap(),
            bytes,
            "{format}"
        );
    }
}

#[test]
fn units_and_paper_flags_apply_to_plan_and_review() {
    let path = fixture("parts/plate.step");
    for command in ["plan", "review"] {
        let (code, result) = run(&[
            command,
            path.to_str().unwrap(),
            "--units",
            "in",
            "--paper",
            "arch",
        ]);
        assert_eq!(code, Some(0), "{result}");
        if command == "plan" {
            assert_eq!(result["spec"]["units"], "in");
            assert_eq!(result["spec"]["sheet"]["paper"], "arch");
            assert!(
                result["sheet"]["size"]
                    .as_str()
                    .unwrap()
                    .starts_with("ARCH ")
            );
        }
    }
    for args in [["--units", "cm"], ["--paper", "letter"]] {
        let result = Command::new(env!("CARGO_BIN_EXE_draftwright"))
            .args(["plan", path.to_str().unwrap(), args[0], args[1]])
            .output()
            .unwrap();
        assert!(!result.status.success());
    }
}

#[test]
fn bare_and_render_embed_us_unit_and_paper_overrides() {
    let path = fixture("parts/plate.step");
    for command in ["render", "bare"] {
        let stem = scratch(&format!("units-{command}"));
        let mut args = Vec::new();
        if command == "render" {
            args.push("render");
        }
        args.extend([
            path.to_str().unwrap(),
            "--units",
            "in",
            "--paper",
            "ansi",
            "--out",
            stem.to_str().unwrap(),
        ]);
        let (code, result) = run(&args);
        assert_eq!(code, Some(0), "{result}");
        let (code, spec) = run(&["spec", stem.with_extension("svg").to_str().unwrap()]);
        assert_eq!(code, Some(0), "{spec}");
        assert_eq!(spec["units"], "in");
        assert_eq!(spec["sheet"]["paper"], "ansi");
    }
}

#[test]
fn sheet_overrides_apply_to_every_drawing_cli_form() {
    let step = fixture("parts/plate.step");
    let base = scratch("sheet-flags-base.json");
    std::fs::write(
        &base,
        r#"{"sheet":{"size":"A4","preferred_max_size":"A2"}}"#,
    )
    .unwrap();
    for form in ["render", "bare", "plan", "review"] {
        for (kind, options) in [
            ("named", vec!["--size", "A3", "--max-size", "A3"]),
            (
                "set",
                vec![
                    "--set",
                    "sheet/size=A3",
                    "--set",
                    "sheet/preferred_max_size=A3",
                ],
            ),
        ] {
            let stem = scratch(&format!("sheet-flags-{form}-{kind}"));
            let mut args = Vec::new();
            if form != "bare" {
                args.push(form);
            }
            args.extend([step.to_str().unwrap(), "--spec", base.to_str().unwrap()]);
            if form == "render" {
                args.extend(["-o", stem.to_str().unwrap(), "--formats", "svg"]);
            }
            if form == "bare" {
                args.extend(["--out", stem.to_str().unwrap()]);
            }
            args.extend(options);
            let (code, result) = run(&args);
            assert_eq!(code, Some(0), "{args:?}: {result}");
            let spec = match form {
                "plan" => {
                    assert_eq!(result["sheet"]["size"], "A3", "{result}");
                    result["spec"].clone()
                }
                "review" => {
                    let svg = result["svg"].as_str().unwrap();
                    assert!(svg.contains("width=\"420.000mm\""), "{svg}");
                    serde_json::to_value(model::embedded_spec(svg.as_bytes()).unwrap().unwrap())
                        .unwrap()
                }
                _ => {
                    let svg = std::fs::read_to_string(stem.with_extension("svg")).unwrap();
                    assert!(svg.contains("width=\"420.000mm\""), "{svg}");
                    run(&["spec", stem.with_extension("svg").to_str().unwrap()]).1
                }
            };
            assert_eq!(spec["sheet"]["size"], "A3");
            assert_eq!(spec["sheet"]["preferred_max_size"], "A3");
        }
    }
}

#[test]
fn scale_flags_accept_numbers_and_ratios() {
    let step = fixture("parts/plate.step");
    for (value, expected) in [("5", "5:1"), ("5:1", "5:1"), ("0.5", "1:2"), ("1:2", "1:2")] {
        let stem = scratch(&format!("scale-ratio-{}", value.replace(':', "-")));
        let (code, result) = run(&[
            "render",
            step.to_str().unwrap(),
            "-o",
            stem.to_str().unwrap(),
            "--formats",
            "svg",
            "--size",
            "A0",
            "--scale",
            value,
        ]);
        assert_eq!(code, Some(0), "{value}: {result}");
        let (_, spec) = run(&["spec", result["written"][0].as_str().unwrap()]);
        assert_eq!(spec["sheet"]["scale"], expected);
    }
}

#[test]
fn set_parses_json_text_repeats_and_escaped_keys() {
    let step = fixture("parts/plate.step");
    let (code, result) = run(&[
        "plan",
        step.to_str().unwrap(),
        "--set",
        "sheet/size=A4",
        "--set",
        "sheet/size=\"A3\"",
        "--set",
        "views/iso/omit=true",
        "--set",
        "sheet/title/title=A=B",
        "--set",
        "dimensions/d:f:hole:f6~1bore.diameter/omit=true",
    ]);
    assert_eq!(code, Some(0), "{result}");
    assert_eq!(result["sheet"]["size"], "A3");
    assert_eq!(result["spec"]["views"]["iso"]["omit"], true);
    assert_eq!(result["spec"]["sheet"]["title"]["title"], "A=B");
    assert_eq!(
        result["spec"]["dimensions"]["d:f:hole:f6/bore.diameter"]["omit"],
        true
    );
    for name in ["ANSI B", "ARCH B"] {
        let (code, result) = run(&["plan", step.to_str().unwrap(), "--size", name]);
        assert_eq!(code, Some(0), "{result}");
        assert_eq!(result["sheet"]["size"], name);
    }
}

#[test]
fn sheet_overrides_refuse_bad_paths_values_and_conflicts_readably() {
    let step = fixture("parts/plate.step");
    for form in ["plan", "review", "render", "bare"] {
        let stem = scratch(&format!("sheet-refusal-{form}"));
        let mut base = Vec::new();
        if form != "bare" {
            base.push(form);
        }
        base.push(step.to_str().unwrap());
        if form == "render" {
            base.extend(["-o", stem.to_str().unwrap()]);
        }
        if form == "bare" {
            base.extend(["--out", stem.to_str().unwrap()]);
        }
        for (options, path, reason) in [
            (
                vec!["--set", "sheet/typo=A3"],
                "sheet/typo",
                "unknown field",
            ),
            (
                vec!["--set", "views/iso/omit=perhaps"],
                "views/iso/omit",
                "invalid type",
            ),
            (
                vec!["--set", "sheet={\"size\":\"bad\"}"],
                "sheet",
                "sheet size",
            ),
            (
                vec!["--set", "sheet/scale=bogus"],
                "sheet/scale",
                "standard scale",
            ),
            (vec!["--set", "sheet/size=bad"], "sheet/size", "sheet size"),
            (vec!["--size", "bad"], "sheet/size", "sheet size"),
            (
                vec!["--max-size", "bad"],
                "sheet/preferred_max_size",
                "sheet size",
            ),
            (vec!["--set", "sheet/size"], "sheet/size", "PATH=VALUE"),
            (
                vec!["--set", "sheet/size=A4", "--size", "A3"],
                "sheet/size",
                "conflicts",
            ),
            (
                vec!["--size", "A3", "--set", "sheet/size=A4"],
                "sheet/size",
                "conflicts",
            ),
            (
                vec!["--set", "sheet={}", "--size", "A3"],
                "sheet",
                "conflicts",
            ),
        ] {
            let mut args = base.clone();
            args.extend(options);
            let error = cli::failure(&args, 2);
            assert!(
                error.contains(path) && error.contains(reason),
                "{args:?}: {error}"
            );
        }
    }
}

/// Every example in the reference table is executable CLI input, not a second list
/// maintained here. Each row is independent, including optional pack drawing paths.
#[test]
fn cli_reference_set_examples_are_accepted() {
    let doc = include_str!("../docs/cli.md");
    let table = doc
        .split_once("<!-- tested-set-examples -->")
        .unwrap()
        .1
        .split_once("<!-- /tested-set-examples -->")
        .unwrap()
        .0;
    let rows: Vec<_> = table
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .collect();
    assert_eq!(rows[0], "| Path | Values | Effect | Example |");
    assert_eq!(rows[1], "| --- | --- | --- | --- |");
    let examples = &rows[2..];
    assert!((15..=25).contains(&examples.len()), "common settings table");
    let part = fixture("parts/plate.step");
    let assembly = fixture("assembly/pack-small.step.gz");
    let process = std::process::id();
    for (index, row) in examples.iter().enumerate() {
        assert!(row.starts_with('|') && row.ends_with('|'), "{row}");
        let columns: Vec<_> = row.trim_matches('|').split('|').map(str::trim).collect();
        assert_eq!(columns.len(), 4, "{row}");
        let assignment = columns[3]
            .strip_prefix("`--set '")
            .and_then(|s| s.strip_suffix("'`"))
            .unwrap_or_else(|| panic!("expected one quoted --set argument: {row}"));
        let (path, value) = assignment.split_once('=').unwrap();
        let expected: Value = serde_json::from_str(value).unwrap_or_else(|_| json!(value));
        let part_path = path
            .strip_prefix("parts/")
            .map(|p| p.split_once("/drawing/").unwrap());
        let drawing_path = part_path
            .map(|(_, p)| p)
            .unwrap_or_else(|| path.strip_prefix("ga/drawing/").unwrap_or(path));
        let pack = part_path.is_some() || path.starts_with("ga/");
        let out = scratch(&format!("reference-pack-{process}-{index}"));
        let base_file = scratch(&format!("reference-pack-{process}-{index}.json"));
        let mut args = if pack {
            vec![
                "pack",
                assembly.to_str().unwrap(),
                "-o",
                out.to_str().unwrap(),
                "--formats",
                "pdf,json",
            ]
        } else {
            vec!["plan", part.to_str().unwrap()]
        };
        if let Some((key, _)) = part_path {
            let key = key.replace("~1", "/").replace("~0", "~");
            let (code, listed) = run(&["parts", assembly.to_str().unwrap()]);
            assert_eq!(code, Some(0), "{row}: {listed}");
            let keys: Vec<_> = listed["parts"]
                .as_array()
                .unwrap()
                .iter()
                .map(|p| p["key"].as_str().unwrap().to_owned())
                .collect();
            assert!(keys.contains(&key), "{row}");
            // PDF embeds the first sheet's Spec. Make this target the only made
            // part, separate from congruent products, with no preceding GA.
            let mut base = json!({"ga":{"include":false}, "parts":{}});
            for name in keys {
                base["parts"][&name] = if name == key {
                    json!({"supply":"make", "separate":true})
                } else {
                    json!({"supply":"buy"})
                };
            }
            std::fs::write(&base_file, base.to_string()).unwrap();
            args.extend(["--spec", base_file.to_str().unwrap()]);
        }
        args.extend(["--set", assignment]);
        let (code, result) = run(&args);
        assert_eq!(code, Some(0), "{row}: {result}");
        assert!(result.is_object(), "{row}: {result}");
        let spec = if pack {
            if let Some((key, _)) = part_path {
                let key = key.replace("~1", "/").replace("~0", "~");
                let target = result["report"]["rows"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .find(|r| r["products"].as_array().unwrap().contains(&json!(key)))
                    .unwrap();
                assert_eq!(target["sheets"], json!([1]), "{row}: {result}");
                assert!(
                    result["report"]["general_assembly"].is_null(),
                    "{row}: {result}"
                );
            } else if drawing_path == "sheet/size" || drawing_path == "sheet/scale" {
                let field = drawing_path.strip_prefix("sheet/").unwrap();
                assert_eq!(
                    result["report"]["general_assembly"][field], expected,
                    "{row}: {result}"
                );
            }
            serde_json::to_value(api::spec(&out.join("pack.pdf")).unwrap()).unwrap()
        } else {
            assert_reference_effect(&result, path, &expected);
            result["spec"].clone()
        };
        let actual = if let Some(view) = drawing_path
            .strip_prefix("views/")
            .and_then(|p| p.strip_suffix("/omit"))
        {
            // False is omitted by ViewSpec's serializer. Require the view entry,
            // then check its actual typed value instead of treating absence as false.
            let typed: model::Spec = serde_json::from_value(spec.clone()).unwrap();
            let key = view.replace("~1", "/").replace("~0", "~");
            json!(
                typed
                    .views
                    .get(&key)
                    .expect("the edited view is present")
                    .omit
            )
        } else {
            spec.pointer(&format!("/{drawing_path}"))
                .cloned()
                .unwrap_or(Value::Null)
        };
        assert_reference_value(&actual, &expected, row);
        if pack {
            std::fs::remove_dir_all(out).unwrap();
        }
        if part_path.is_some() {
            std::fs::remove_file(base_file).unwrap();
        }
    }
}

// JSON numbers keep their value when a typed Spec serializes e.g. 3 as 3.0.
fn assert_reference_value(actual: &Value, expected: &Value, row: &str) {
    match (actual, expected) {
        (Value::Number(a), Value::Number(b)) if a.is_f64() || b.is_f64() => {
            assert_eq!(a.as_f64(), b.as_f64(), "{row}");
        }
        (Value::Object(a), Value::Object(b)) => {
            assert_eq!(
                a.keys().collect::<Vec<_>>(),
                b.keys().collect::<Vec<_>>(),
                "{row}"
            );
            for (key, value) in b {
                assert_reference_value(&a[key], value, row);
            }
        }
        (Value::Array(a), Value::Array(b)) => {
            assert_eq!(a.len(), b.len(), "{row}");
            for (a, b) in a.iter().zip(b) {
                assert_reference_value(a, b, row);
            }
        }
        _ => assert_eq!(actual, expected, "{row}"),
    }
}

/// Check effects exposed directly by the Plan as well as the stored Spec.
fn assert_reference_effect(plan: &Value, path: &str, expected: &Value) {
    match path {
        "sheet/size" | "sheet/scale" => {
            assert_eq!(plan.pointer(&format!("/{path}")), Some(expected))
        }
        "units" => assert_eq!(&plan["sheet"]["units"], expected),
        "sheet/paper" => {
            let prefix = match expected.as_str().unwrap() {
                "iso" => "A",
                "ansi" => "ANSI ",
                "arch" => "ARCH ",
                _ => unreachable!(),
            };
            assert!(plan["sheet"]["size"].as_str().unwrap().starts_with(prefix));
        }
        "sheet/projection" => assert_eq!(
            plan["sheet"]["projection_symbol"]["method"],
            format!("{}-angle", expected.as_str().unwrap())
        ),
        "sheet/zones" => assert_eq!(
            !plan["sheet"]["zones"].is_null(),
            expected.as_bool().unwrap()
        ),
        "sheet/title" => {
            for (key, value) in expected.as_object().unwrap() {
                assert_eq!(&plan["sheet"]["title"][key], value);
            }
        }
        "sheet/default_finish" => {
            let finish = plan["requirements"]
                .as_array()
                .unwrap()
                .iter()
                .find(|r| r["kind"] == "default_surface_finish")
                .unwrap();
            assert_eq!(finish["text"], format!("Ra {}", expected["ra"]));
            assert!(!finish["placed"].is_null());
        }
        "sheet/dimension_spacing" => assert_eq!(&plan["sheet"]["dimension_spacing"], expected),
        "sheets/max" => assert!(
            plan["sheets"].as_array().map_or(0, Vec::len) < expected.as_u64().unwrap() as usize
        ),
        _ => {
            if let Some(key) = path.strip_prefix("sheet/title/") {
                assert_eq!(&plan["sheet"]["title"][key], expected);
            }
            if let Some(view) = path
                .strip_prefix("views/")
                .and_then(|p| p.strip_suffix("/omit"))
            {
                let shown = plan["views"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|v| v["id"] == format!("v:{view}"));
                assert_eq!(shown, !expected.as_bool().unwrap(), "{path}: {plan}");
            }
        }
    }
}

#[test]
fn plan_and_review_accept_numeric_and_ratio_scales_and_override_the_spec() {
    let part = fixture("parts/plate.step");
    let base = scratch("review-plan-scale-base.json");
    std::fs::write(&base, r#"{"sheet":{"scale":"1:10"}}"#).unwrap();
    for command in ["plan", "review"] {
        let help = Command::new(env!("CARGO_BIN_EXE_draftwright"))
            .args([command, "--help"])
            .output()
            .unwrap();
        assert!(help.status.success());
        let help = String::from_utf8(help.stdout).unwrap();
        assert!(
            help.contains("--scale") && help.contains("overrides --spec"),
            "{help}"
        );
        for (value, ratio) in [("5", "5:1"), ("0.5", "1:2"), ("5:1", "5:1"), ("1:2", "1:2")] {
            for with_spec in [false, true] {
                let mut args = vec![command, part.to_str().unwrap(), "--scale", value];
                if with_spec {
                    args.extend(["--spec", base.to_str().unwrap()]);
                }
                let (code, result) = run(&args);
                assert_eq!(code, Some(0), "{args:?}: {result}");
                let spec = if command == "plan" {
                    assert_eq!(result["sheet"]["scale"], ratio);
                    result["spec"].clone()
                } else {
                    let svg = result["svg"].as_str().unwrap();
                    assert!(
                        svg.contains(&format!(">SCALE {ratio}<")),
                        "{args:?}: scale caption"
                    );
                    serde_json::to_value(model::embedded_spec(svg.as_bytes()).unwrap().unwrap())
                        .unwrap()
                };
                assert_eq!(spec["sheet"]["scale"], ratio, "{args:?}: {spec}");
            }
        }
        for options in [
            vec!["--scale", "1:2", "--set", "sheet/scale=1:1"],
            vec!["--set", "sheet/scale=1:1", "--scale", "1:2"],
        ] {
            let mut args = vec![command, part.to_str().unwrap()];
            args.extend(options);
            let error = cli::failure(&args, 2);
            assert!(
                error.contains("sheet/scale") && error.contains("conflicts"),
                "{error}"
            );
        }
        for value in ["0", "1:0", "bad"] {
            let error = cli::failure(&[command, part.to_str().unwrap(), "--scale", value], 2);
            assert!(error.contains("--scale"), "{error}");
        }
    }
}

#[test]
fn plan_from_analysis_accepts_scale_without_a_step_or_other_overrides() {
    let (_, analysis) = run(&["analyse", fixture("parts/plate.step").to_str().unwrap()]);
    let file = scratch("plan-scale.analysis.json");
    std::fs::write(&file, analysis.to_string()).unwrap();
    let (code, plan) = run(&[
        "plan",
        "--analysis",
        file.to_str().unwrap(),
        "--scale",
        "1:2",
    ]);
    assert_eq!(code, Some(0), "{plan}");
    assert_eq!(plan["spec"]["sheet"]["scale"], "1:2");
    assert_eq!(plan["sheet"]["scale"], "1:2");
}

#[test]
fn set_paths_are_strict_whole_values_and_errors_prefix_the_path_once() {
    for form in ["plan", "review", "render", "bare", "pack"] {
        let pack = form == "pack";
        let part = fixture(if pack {
            "assembly/pack-small.step.gz"
        } else {
            "parts/plate.step"
        });
        let out = scratch(&format!("strict-set-path-{form}"));
        let mut base = Vec::new();
        if form != "bare" {
            base.push(form);
        }
        base.push(part.to_str().unwrap());
        if matches!(form, "render" | "pack") {
            base.extend(["-o", out.to_str().unwrap()]);
        }
        if form == "bare" {
            base.extend(["--out", out.to_str().unwrap()]);
        }
        for assignment in ["=A3", ""] {
            let mut args = base.clone();
            args.extend(["--set", assignment]);
            assert_eq!(
                cli::failure(&args, 2),
                "error: --set needs PATH=VALUE; the path is empty\n"
            );
        }
        let prefix = if pack { "ga/drawing/" } else { "" };
        for (path, reason) in [
            (format!("/{prefix}sheet/size"), "leading '/'"),
            (format!("{prefix}sheet//size"), "empty segments"),
            (format!("{prefix}sheet/size/"), "trailing '/'"),
            (format!("/{prefix}sheet//size/"), "leading '/'"),
        ] {
            let assignment = format!("{path}=A3");
            let mut args = base.clone();
            args.extend(["--set", &assignment]);
            let error = cli::failure(&args, 2);
            assert!(
                error.contains(reason) && error.contains(&path),
                "{form}: {error}"
            );
        }
        let path = format!("{prefix}sheet/scale");
        let assignment = format!("{path}=bogus");
        let mut args = base.clone();
        args.extend(["--set", &assignment]);
        let error = cli::failure(&args, 2);
        assert_eq!(
            error.lines().next().unwrap(),
            format!("error: {path}: \"bogus\" is not a standard scale")
        );
        assert_eq!(
            error.lines().next().unwrap().matches("sheet/scale").count(),
            1
        );
        assert!(error.ends_with(&format!("  entry: {path}\n")));
        for (whole, element) in [
            ("views/iso/colour=[184,196,204]", "views/iso/colour/0=1"),
            (
                "tables=[{\"id\":\"t:1\",\"rows\":[[\"NOTE\"]]}]",
                "tables/0/sheet=2",
            ),
        ] {
            let first = format!("{prefix}{whole}");
            let second = format!("{prefix}{element}");
            let mut args = base.clone();
            args.extend(["--set", &first, "--set", &second]);
            let error = cli::failure(&args, 2);
            assert!(
                error.contains("whole values only; set an array or object in one go"),
                "{form}: {error}"
            );
            assert!(error.contains(second.split_once('=').unwrap().0), "{error}");
        }
    }
}
