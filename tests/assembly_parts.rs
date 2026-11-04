//! One chosen part of an assembly (draftwright-rust #8, slice 3), driven through the command line
//! and the service as a caller drives them. The fixture is the editor's `assembly.step`: a plate
//! (part 0) and a pin (part 1) placed twice, at x = ±30. `assembly_pin_pmi.step.gz` is the same
//! file with PMI written onto the pin alone by haecceity (quiddity-rust's
//! `editor_assembly_pin_alone`): datums A and B, a position and a circular runout.

use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use serde_json::{Value, json};

fn assembly() -> String {
    fixture("assembly.step").display().to_string()
}

fn fixture(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/assembly")
        .join(name)
}

fn scratch(name: &str) -> PathBuf {
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR")).join("assembly_parts");
    std::fs::create_dir_all(&dir).unwrap();
    dir.join(name)
}

/// The assembly with the pin's PMI, unpacked.
fn pin_pmi() -> String {
    let mut bytes = Vec::new();
    flate2::read::GzDecoder::new(std::fs::File::open(fixture("assembly_pin_pmi.step.gz")).unwrap())
        .read_to_end(&mut bytes)
        .unwrap();
    let path = scratch("assembly_pin_pmi.step");
    std::fs::write(&path, bytes).unwrap();
    path.display().to_string()
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

fn problems<'a>(plan: &'a Value, code: &str) -> Vec<&'a Value> {
    plan["problems"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|p| p["code"] == code)
        .collect()
}

#[test]
fn parts_lists_the_plate_and_the_pin_placed_twice() {
    let (code, out) = run(&["parts", &assembly()]);
    assert_eq!(code, Some(0), "{out}");
    assert_eq!(
        out,
        json!({ "parts": [
            { "part": 0, "key": "plate", "name": "plate", "product_id": "plate", "faces": 13, "instances": 1 },
            { "part": 1, "key": "pin", "name": "pin", "product_id": "pin", "faces": 7, "instances": 2 },
        ]})
    );
}

#[test]
fn part_one_is_the_pin_alone_in_its_own_frame() {
    let (code, a) = run(&["analyse", &assembly(), "--part", "1", "--no-features"]);
    assert_eq!(code, Some(0), "{a}");
    assert_eq!(a["binding"]["face_count"], 7);
    assert_eq!(a["binding"]["part"], "pin");
    assert_eq!(a["binding"]["part_index"], 1);
    // The pin's own origin, not either placement at x = ±30.
    let e = &a["envelope"];
    for k in 0..2 {
        let (lo, hi) = (e[0][k].as_f64().unwrap(), e[1][k].as_f64().unwrap());
        assert!((lo + hi).abs() < 1e-6, "axis {k} not centred: {e}");
        assert!(
            (hi - lo - 10.0).abs() < 1e-6,
            "axis {k} is not the pin's ø10: {e}"
        );
    }
}

#[test]
fn render_part_one_draws_one_pin() {
    let out = scratch("pin");
    let (code, r) = run(&[
        "render",
        &assembly(),
        "--part",
        "1",
        "-o",
        out.to_str().unwrap(),
        "--formats",
        "svg",
    ]);
    assert_eq!(code, Some(0), "{r}");
    assert!(problems(&r, "assembly_drawn_whole").is_empty(), "{r}");
    let svg = std::fs::read_to_string(out.with_extension("svg")).unwrap();
    // One pin: its overall length and diameter, and the title the part names; nothing of the
    // plate (100 × 60) or of the pins' 60 mm spacing.
    let (_, plan) = run(&["plan", &assembly(), "--part", "1"]);
    let labels: Vec<&str> = plan["dimensions"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|d| d["label"].as_str())
        .collect();
    assert!(labels.contains(&"32"), "{labels:?}");
    assert!(labels.contains(&"ø10"), "{labels:?}");
    for whole in ["100", "60", "42"] {
        assert!(!labels.contains(&whole), "{whole} in {labels:?}");
    }
    assert!(svg.contains(">PIN<"), "the title block names the pin");
}

#[test]
fn the_pins_pmi_is_drawn_and_the_plates_drawing_drops_it() {
    let step = pin_pmi();
    let (code, plan) = run(&["plan", &step, "--part", "1"]);
    assert_eq!(code, Some(0), "{plan}");
    // Whole, each of these is refused as drawn in two placements; alone, each is drawn once.
    assert!(
        !plan["problems"]
            .to_string()
            .contains("placements of the part"),
        "{}",
        plan["problems"]
    );
    let shown: Vec<String> = plan["gdt"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|a| a["state"]["kind"] == "shown")
        .filter_map(|a| a["id"].as_str().map(String::from))
        .collect();
    for kind in [
        "geometric_tolerance.position",
        "geometric_tolerance.circular_runout",
    ] {
        assert!(
            shown
                .iter()
                .any(|id| id.starts_with(&format!("pmi:{kind}"))),
            "{kind} not shown: {shown:?}"
        );
    }
    for letter in ["A", "B"] {
        assert!(
            shown
                .iter()
                .any(|id| id.starts_with("pmi:datum") && id.ends_with(&format!(":t={letter}"))),
            "datum {letter} not shown: {shown:?}"
        );
    }
    // The pin's material is its own drawing's.
    assert_eq!(
        plan["sheet"]["title"]["material"], "Steel C45",
        "{}",
        plan["sheet"]
    );

    // The plate alone: the pin's records are dropped, not refused.
    let (code, a) = run(&["analyse", &step, "--part", "0", "--no-features"]);
    assert_eq!(code, Some(0), "{a}");
    assert_eq!(a["binding"]["face_count"], 13);
    let report = &a["pmi"]["read"];
    assert!(report.is_object(), "the plate's PMI reading: {}", a["pmi"]);
    assert_eq!(report["drawn"], 0);
    assert_eq!(report["records"], json!([]));
    assert!(report.get("materials").is_none(), "{report}");
}

#[test]
fn no_part_draws_the_whole_assembly_and_warns_naming_the_parts() {
    let (code, a) = run(&["analyse", &assembly(), "--no-features"]);
    assert_eq!(code, Some(0), "{a}");
    assert_eq!(a["binding"]["face_count"], 27);
    assert!(a["binding"].get("part_index").is_none());
    assert_eq!(a["parts"].as_array().unwrap().len(), 2);
    let (code, plan) = run(&["plan", &assembly(), "--no-features"]);
    assert_eq!(code, Some(0), "{plan}");
    let warned = problems(&plan, "assembly_drawn_whole");
    assert_eq!(warned.len(), 1, "{}", plan["problems"]);
    let message = warned[0]["message"].as_str().unwrap();
    assert_eq!(warned[0]["severity"], "warning");
    assert!(message.contains("0: plate, 1: pin"), "{message}");
    assert!(message.contains("--part"), "{message}");
    // The pins are placed apart, so nothing coincides (#212).
    assert!(a.get("coincident").is_none(), "{a}");
    assert!(problems(&plan, "coincident_bodies").is_empty());
    // They stand out of the plate's box, so no part is drawn alone as its principal (#219).
    assert!(a.get("principal").is_none(), "{a}");
    assert!(problems(&plan, "assembly_principal_drawn").is_empty());
}

/// The V-slot profile with three coincident copies (#212), unpacked.
fn vslot() -> String {
    let mut bytes = Vec::new();
    flate2::read::GzDecoder::new(std::fs::File::open(fixture("vslot-20x20-100.step.gz")).unwrap())
        .read_to_end(&mut bytes)
        .unwrap();
    let path = scratch("vslot-20x20-100.step");
    std::fs::write(&path, bytes).unwrap();
    path.display().to_string()
}

/// The quantity a label states (`8× C2.661` is 8, `⌀4.2 THRU` is 1), by the label's text after
/// it.
fn quantities(plan: &Value) -> std::collections::BTreeMap<String, u32> {
    plan["dimensions"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|d| d["label"].as_str())
        .filter(|l| l.contains('⌀') || l.contains(" C"))
        .map(|l| match l.split_once("× ") {
            Some((n, rest)) => (rest.to_string(), n.parse().unwrap()),
            None => (l.to_string(), 1),
        })
        .collect()
}

#[test]
fn coincident_bodies_are_drawn_once_as_the_part_alone() {
    let step = vslot();
    let (code, whole) = run(&["plan", &step]);
    assert_eq!(code, Some(0), "{whole}");
    let (code, alone) = run(&["plan", &step, "--part", "0"]);
    assert_eq!(code, Some(0), "{alone}");
    // One hole and eight of each chamfer, as part 0 alone has them, not once per copy.
    let counted = quantities(&whole);
    assert_eq!(counted.get("⌀4.2 THRU"), Some(&1), "{counted:?}");
    assert_eq!(counted.get("C2.661"), Some(&8), "{counted:?}");
    assert_eq!(counted, quantities(&alone));
    assert_eq!(whole["sheet"]["size"], alone["sheet"]["size"]);
    assert_eq!(whole["sheet"]["scale"], alone["sheet"]["scale"]);
    // The copies are reported, not warned as an assembly drawn whole.
    assert!(problems(&whole, "assembly_drawn_whole").is_empty());
    let reported = problems(&whole, "coincident_bodies");
    assert_eq!(reported.len(), 1, "{}", whole["problems"]);
    assert_eq!(reported[0]["severity"], "info");
    let message = reported[0]["message"].as_str().unwrap();
    assert!(
        message.contains("0: V-Slot-20x20-100mm (placement 1), 1: Pad"),
        "{message}"
    );
    assert!(message.contains("--part"), "{message}");
    assert!(problems(&alone, "coincident_bodies").is_empty());

    // The Analysis is part 0's, and names the copies.
    let (code, a) = run(&["analyse", &step]);
    assert_eq!(code, Some(0), "{a}");
    assert_eq!(a["binding"]["part_index"], 0);
    assert_eq!(a["binding"]["face_count"], 55);
    assert_eq!(
        a["coincident"],
        json!([
            { "part": 0, "name": "V-Slot-20x20-100mm", "placement": 1, "of": 0 },
            { "part": 1, "name": "Pad", "placement": 0, "of": 0 },
        ])
    );
    // Passed back, it is read from part 0 again.
    let analysis = scratch("vslot_analysis.json");
    std::fs::write(&analysis, a.to_string()).unwrap();
    let (code, plan) = run(&["plan", &step, "--analysis", analysis.to_str().unwrap()]);
    assert_eq!(code, Some(0), "{plan}");
    assert_eq!(quantities(&plan), counted);

    // An Analysis drawn whole with its copies (as one with distinct parts beside them would be)
    // warns that their features are counted again.
    let mut drawn_whole = a.clone();
    drawn_whole["binding"]
        .as_object_mut()
        .unwrap()
        .remove("part_index");
    std::fs::write(&analysis, drawn_whole.to_string()).unwrap();
    let (code, plan) = run(&["plan", "--analysis", analysis.to_str().unwrap()]);
    assert_eq!(code, Some(0), "{plan}");
    let warned = problems(&plan, "coincident_bodies");
    assert_eq!(warned.len(), 1, "{}", plan["problems"]);
    assert_eq!(warned[0]["severity"], "warning");
    let message = warned[0]["message"].as_str().unwrap();
    assert!(message.contains("counted once per copy"), "{message}");
}

/// The LM8UU bearing (#219): a metal sleeve holding an inner sleeve, unpacked.
fn lm8uu() -> String {
    let mut bytes = Vec::new();
    flate2::read::GzDecoder::new(std::fs::File::open(fixture("lm8uu.step.gz")).unwrap())
        .read_to_end(&mut bytes)
        .unwrap();
    let path = scratch("lm8uu.step");
    std::fs::write(&path, bytes).unwrap();
    path.display().to_string()
}

fn labels(plan: &Value) -> Vec<String> {
    plan["dimensions"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|d| d["label"].as_str().map(String::from))
        .collect()
}

#[test]
fn a_body_within_the_principal_part_is_not_dimensioned_as_its_feature() {
    let step = lm8uu();
    // The sleeve, whose box holds the inner sleeve's, is read alone as --part 1 reads it.
    let (code, a) = run(&["analyse", &step, "--no-features"]);
    assert_eq!(code, Some(0), "{a}");
    assert_eq!(a["binding"]["part_index"], 1);
    assert_eq!(a["binding"]["part"], "Metal");
    assert_eq!(a["binding"]["face_count"], 14);
    assert_eq!(a["principal"], true);
    let (code, alone) = run(&["analyse", &step, "--part", "1", "--no-features"]);
    assert_eq!(code, Some(0), "{alone}");
    assert!(alone.get("principal").is_none(), "{alone}");
    let (code, plan) = run(&["plan", &step]);
    assert_eq!(code, Some(0), "{plan}");
    let (code, chosen) = run(&["plan", &step, "--part", "1"]);
    assert_eq!(code, Some(0), "{chosen}");
    // The sleeve's own bore, not the inner sleeve's ⌀8, which is 22.4 long, as a THRU hole.
    let drawn = labels(&plan);
    assert!(drawn.iter().any(|l| l == "⌀12.4 THRU"), "{drawn:?}");
    assert!(!drawn.iter().any(|l| l.contains("⌀8")), "{drawn:?}");
    assert_eq!(drawn, labels(&chosen));
    assert_eq!(plan["sheet"]["size"], chosen["sheet"]["size"]);
    assert!(problems(&plan, "assembly_drawn_whole").is_empty());
    let warned = problems(&plan, "assembly_principal_drawn");
    assert_eq!(warned.len(), 1, "{}", plan["problems"]);
    assert_eq!(warned[0]["severity"], "warning");
    let message = warned[0]["message"].as_str().unwrap();
    assert!(message.contains("part 1 (Metal)"), "{message}");
    assert!(message.contains("they (0: Center)"), "{message}");
    assert!(message.contains("--part"), "{message}");
    assert!(problems(&chosen, "assembly_principal_drawn").is_empty());
    // The sheet says so: one automatic general note names the part drawn and the one left out,
    // and a part chosen by its index carries none.
    let note = |plan: &Value| {
        plan["notes"].as_array().map_or(Vec::new(), |n| {
            n.iter()
                .filter(|n| n["id"] == "n:assembly")
                .cloned()
                .collect::<Vec<_>>()
        })
    };
    let notes = note(&plan);
    assert_eq!(notes.len(), 1, "{}", plan["notes"]);
    assert_eq!(
        notes[0]["text"],
        "ASSEMBLY OF 2 PARTS: ONLY PART METAL SHOWN; PART CENTER NOT SHOWN"
    );
    assert_eq!(notes[0]["default"], true);
    assert_eq!(notes[0]["state"]["kind"], "shown");
    assert!(notes[0]["placed"].is_object(), "{}", notes[0]);
    assert!(note(&chosen).is_empty(), "{}", chosen["notes"]);
    // The Spec leaves it out, kept in the plan undrawn.
    let spec = scratch("lm8uu_spec.json");
    std::fs::write(
        &spec,
        json!({"schema": "draftwright.spec/2", "annotations": {"n:assembly": {"omit": true}}})
            .to_string(),
    )
    .unwrap();
    let (code, omitted) = run(&["plan", &step, "--spec", spec.to_str().unwrap()]);
    assert_eq!(code, Some(0), "{omitted}");
    let notes = note(&omitted);
    assert_eq!(notes.len(), 1, "{}", omitted["notes"]);
    assert_eq!(notes[0]["state"]["kind"], "omitted_by_user");
    assert!(notes[0]["placed"].is_null());
    assert_eq!(labels(&omitted), drawn);
    // The inner sleeve can still be drawn alone.
    let (code, inner) = run(&["plan", &step, "--part", "0"]);
    assert_eq!(code, Some(0), "{inner}");
    assert!(labels(&inner).iter().any(|l| l == "⌀8 THRU"), "{inner}");

    // Passed back, the Analysis is read from the sleeve again and still warns.
    let analysis = scratch("lm8uu_analysis.json");
    std::fs::write(&analysis, a.to_string()).unwrap();
    let (code, again) = run(&["plan", &step, "--analysis", analysis.to_str().unwrap()]);
    assert_eq!(code, Some(0), "{again}");
    assert_eq!(problems(&again, "assembly_principal_drawn").len(), 1);
}

#[test]
fn coincident_bodies_are_read_alone_again_from_the_caches() {
    let step = vslot();
    // The plan cache's Analysis, read on a hit with the part it was made from: drawn as when
    // it was made, not with the copies on top.
    let cache = scratch("vslot_plan_cache");
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
        render("vslot_made") == render("vslot_hit"),
        "a plan cache hit draws another part"
    );
    // A registered part_ref's cached Analysis, by a second service: the same PDF.
    let cache = scratch("vslot_service_cache");
    let _ = std::fs::remove_dir_all(&cache);
    let registered = serve_in(
        &cache,
        &[json!({ "id": 1, "op": "mcp_inspect_part", "arguments": { "step": step } })],
    );
    assert_eq!(
        registered[0]["result"]["face_count"], 55,
        "{}",
        registered[0]
    );
    let part_ref = registered[0]["result"]["part_ref"].clone();
    let pdf = |reply: &Value| reply["result"]["bytes"].as_u64().unwrap();
    let render_pdf =
        json!({ "id": 2, "op": "mcp_render_pdf", "arguments": { "part_ref": part_ref } });
    // Its projections dropped, so the part is projected again from the file.
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

#[test]
fn a_part_the_file_does_not_have_is_refused_naming_the_parts() {
    let (code, out) = run(&["analyse", &assembly(), "--part", "2", "--no-features"]);
    assert_eq!(code, Some(2), "{out}");
    let message = out["error"]["message"].as_str().unwrap();
    assert!(message.contains("0: plate, 1: pin"), "{message}");
}

#[test]
fn a_file_of_one_part_carries_no_part_fields() {
    let plate = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/plate.step");
    let (code, a) = run(&["analyse", plate.to_str().unwrap(), "--no-features"]);
    assert_eq!(code, Some(0), "{a}");
    assert!(a.get("parts").is_none());
    assert!(a["binding"].get("part_index").is_none());
}

/// The replies to *lines*, sent to one `draftwright serve` until its stdin closes.
fn serve(lines: &[Value]) -> Vec<Value> {
    serve_in(&scratch("cache"), lines)
}

/// [`serve`], with its cache in *cache*.
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
fn the_service_and_mcp_list_parts_and_take_a_part() {
    let step = assembly();
    let replies = serve(&[
        json!({ "id": 1, "op": "parts", "step": step }),
        json!({ "id": 2, "op": "analyse", "step": step, "part": 1 }),
        json!({ "id": 3, "op": "plan", "step": step }),
        json!({ "id": 4, "op": "mcp_parts", "arguments": { "step": step } }),
        json!({ "id": 5, "op": "mcp_inspect_part", "arguments": { "step": step, "part": 1 } }),
        json!({ "id": 6, "op": "analyse", "step": step, "part": "pin" }),
    ]);
    assert_eq!(
        replies[0]["result"]["parts"][1]["name"], "pin",
        "{}",
        replies[0]
    );
    assert_eq!(
        replies[1]["result"]["binding"]["face_count"], 7,
        "{}",
        replies[1]
    );
    assert_eq!(replies[1]["result"]["binding"]["part_index"], 1);
    assert_eq!(
        problems(&replies[2]["result"], "assembly_drawn_whole").len(),
        1,
        "{}",
        replies[2]
    );
    assert_eq!(replies[3]["result"]["parts"], replies[0]["result"]["parts"]);
    let summary = &replies[4]["result"];
    assert_eq!(summary["face_count"], 7, "{}", replies[4]);
    assert_eq!(summary["part"], "pin");
    assert!(
        summary["part_ref"].as_str().unwrap().contains("_p1-"),
        "{summary}"
    );
    assert_eq!(replies[5]["ok"], false);
    assert_eq!(replies[5]["error"]["code"], "invalid");
}

/// A file of one part: choosing it (`--part 0`) draws what the file draws without `--part`, byte
/// for byte, with the title its file stem gives (not the STEP product's name, `COMPOUND` here).
#[test]
fn part_zero_of_a_single_part_file_draws_as_without_part() {
    let plate = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts/plate.step")
        .display()
        .to_string();
    let render = |name: &str, part: &[&str]| {
        let out = scratch(name);
        let mut args = vec!["render", &plate];
        args.extend_from_slice(part);
        args.extend(["-o", out.to_str().unwrap(), "--formats", "svg,pdf"]);
        let (code, r) = run(&args);
        assert_eq!(code, Some(0), "{r}");
        (
            std::fs::read(out.with_extension("svg")).unwrap(),
            std::fs::read(out.with_extension("pdf")).unwrap(),
        )
    };
    let (whole_svg, whole_pdf) = render("plate_whole", &[]);
    let (part_svg, part_pdf) = render("plate_part0", &["--part", "0"]);
    assert!(
        whole_svg == part_svg,
        "--part 0 of a single part changes the SVG"
    );
    assert!(
        whole_pdf == part_pdf,
        "--part 0 of a single part changes the PDF"
    );
    assert!(!String::from_utf8_lossy(&part_svg).contains(">COMPOUND<"));
    // An analysis of the whole file is one of its part 0.
    let (code, a) = run(&["analyse", &plate, "--no-features"]);
    assert_eq!(code, Some(0), "{a}");
    let analysis = scratch("plate_analysis.json");
    std::fs::write(&analysis, a.to_string()).unwrap();
    let (code, plan) = run(&[
        "plan",
        &plate,
        "--analysis",
        analysis.to_str().unwrap(),
        "--part",
        "0",
    ]);
    assert_eq!(code, Some(0), "{plan}");
}
