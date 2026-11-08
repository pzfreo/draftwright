//! A part a fraction of a degree off the file's axes is drawn square, in its own frame (#216).
//! The fixture is the FreeCAD library's DIN 471 M10 retaining ring
//! (`tests/fixtures/parts/retaining-ring-din471-m10.md`): 1 thick, 12.298 × 14.537 in its own
//! frame, about 1.09° off the file's axes.

use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use serde_json::{Value, json};

fn scratch(name: &str) -> PathBuf {
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR")).join("off_axis");
    std::fs::create_dir_all(&dir).unwrap();
    dir.join(name)
}

/// The ring, unpacked.
fn ring() -> String {
    let fixture = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts/retaining-ring-din471-m10.step.gz");
    let mut bytes = Vec::new();
    flate2::read::GzDecoder::new(std::fs::File::open(fixture).unwrap())
        .read_to_end(&mut bytes)
        .unwrap();
    let path = scratch("retaining-ring-din471-m10.step");
    std::fs::write(&path, bytes).unwrap();
    path.display().to_string()
}

fn run(args: &[&str]) -> Value {
    let out = Command::new(env!("CARGO_BIN_EXE_draftwright"))
        .args(args)
        .output()
        .unwrap();
    assert!(out.status.success(), "{args:?}");
    serde_json::from_slice(&out.stdout).unwrap()
}

/// The value of the plan's dimension *id*.
fn value(plan: &Value, id: &str) -> f64 {
    plan["dimensions"]
        .as_array()
        .unwrap()
        .iter()
        .find(|d| d["id"] == id)
        .and_then(|d| d["value"].as_f64())
        .unwrap_or_else(|| panic!("no {id}: {}", plan["dimensions"]))
}

#[test]
fn a_part_slightly_off_the_axes_is_drawn_square_in_its_own_frame() {
    let step = ring();
    let a = run(&["analyse", &step]);
    let angle = a["squared"]["angle"].as_f64().unwrap();
    assert!((1.0..1.2).contains(&angle), "{}", a["squared"]);
    // The envelope is the ring's own box, not a tilted one's (1.147 thick in the file's axes).
    let [lo, hi] = [0, 1].map(|i| a["envelope"][i].as_array().unwrap().clone());
    let size: Vec<f64> = (0..3)
        .map(|k| hi[k].as_f64().unwrap() - lo[k].as_f64().unwrap())
        .collect();
    for (got, want) in size.iter().zip([12.298, 1.0, 14.537]) {
        assert!((got - want).abs() < 1e-3, "{size:?}");
    }

    let plan = run(&["plan", &step]);
    assert!((value(&plan, "d:envelope/depth.length") - 1.0).abs() < 1e-3);
    assert!((value(&plan, "d:envelope/width.length") - 12.298).abs() < 1e-3);
    assert!((value(&plan, "d:envelope/height.length") - 14.537).abs() < 1e-3);
    let problems = plan["problems"].as_array().unwrap();
    let squared: Vec<_> = problems
        .iter()
        .filter(|p| p["code"] == "part_squared_to_axes")
        .collect();
    assert_eq!(squared.len(), 1, "{}", plan["problems"]);
    assert_eq!(squared[0]["severity"], "info");
    // Every hole and the boss lie along a principal axis, so each is located.
    assert!(
        !problems.iter().any(|p| p["message"]
            .as_str()
            .is_some_and(|m| m.contains("inclined to every principal axis"))),
        "{}",
        plan["problems"]
    );
    for feature in ["f:hole:1", "f:hole:2", "f:boss:f3"] {
        assert!(
            plan["dimensions"]
                .as_array()
                .unwrap()
                .iter()
                .any(|d| d["id"]
                    .as_str()
                    .is_some_and(|id| id.starts_with(&format!("d:{feature}/location")))),
            "{feature} is not located: {}",
            plan["dimensions"]
        );
    }
}

#[test]
fn a_part_on_the_axes_is_not_moved() {
    let plate = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/plate.step");
    let plate = plate.to_str().unwrap();
    assert!(run(&["analyse", plate]).get("squared").is_none());
    let plan = run(&["plan", plate]);
    assert!(
        !plan["problems"]
            .as_array()
            .unwrap()
            .iter()
            .any(|p| p["code"] == "part_squared_to_axes")
    );
}

fn serve_in(cache: &Path, lines: &[Value]) -> Vec<Value> {
    let mut child = Command::new(env!("CARGO_BIN_EXE_draftwright"))
        .arg("serve")
        .env("DRAFTWRIGHT_CACHE", cache)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let mut stdin = child.stdin.take().unwrap();
    for line in lines {
        writeln!(stdin, "{line}").unwrap();
    }
    drop(stdin);
    let out = child.wait_with_output().unwrap();
    assert!(out.status.success());
    String::from_utf8(out.stdout)
        .unwrap()
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect()
}

#[test]
fn a_squared_part_is_squared_again_when_read_from_the_caches() {
    let step = ring();
    // The plan cache's Analysis, read on a hit with the part re-read from the file: drawn as
    // when it was made, square, not tilted.
    let cache = scratch("ring_plan_cache");
    let _ = std::fs::remove_dir_all(&cache);
    let render = |name: &str| {
        let out = scratch(name);
        let status = Command::new(env!("CARGO_BIN_EXE_draftwright"))
            .args([
                "render",
                &step,
                "-o",
                out.to_str().unwrap(),
                "--formats",
                "svg",
            ])
            .env("DRAFTWRIGHT_PLAN_CACHE", &cache)
            .output()
            .unwrap()
            .status;
        assert!(status.success());
        std::fs::read(out.with_extension("svg")).unwrap()
    };
    assert!(
        render("ring_made") == render("ring_hit"),
        "a plan cache hit draws the part in the file's axes"
    );
    // A registered part_ref's cached Analysis, its projections dropped, so the part is
    // projected again from the file: the same PDF as a fresh registration's.
    let cache = scratch("ring_service_cache");
    let _ = std::fs::remove_dir_all(&cache);
    let registered = serve_in(
        &cache,
        &[json!({ "id": 1, "op": "mcp_inspect_part", "arguments": { "step": step } })],
    );
    let part_ref = registered[0]["result"]["part_ref"].clone();
    let pdf = |reply: &Value| reply["result"]["bytes"].as_u64().unwrap();
    let render_pdf =
        json!({ "id": 2, "op": "mcp_render_pdf", "arguments": { "part_ref": part_ref } });
    for entry in std::fs::read_dir(cache.join("parts")).unwrap() {
        std::fs::remove_file(entry.unwrap().path().join("projections.json")).ok();
    }
    let made = serve_in(&cache, std::slice::from_ref(&render_pdf));
    std::fs::remove_dir_all(&cache).ok();
    let fresh = serve_in(
        &cache,
        &[
            json!({ "id": 1, "op": "mcp_inspect_part", "arguments": { "step": step } }),
            render_pdf.clone(),
        ],
    );
    assert_eq!(pdf(&made[0]), pdf(&fresh[1]), "{} {}", made[0], fresh[1]);
}
