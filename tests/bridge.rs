//! The bridge from Python quiddity's recognition JSON into the Analysis's features.
//!
//! The fixtures in `tests/fixtures/recognition/` were written by `tools/recognise.py` (quiddity
//! 0.4.0.dev0) from the parts beside them. `plate.step` is a 60 × 40 × 5 plate with four Ø6 holes
//! on a 40 × 20 grid, made in build123d (`Box(60, 40, 5)`, `GridLocations(40, 20, 2, 2)`,
//! `Hole(3)`).
//!
//! That quiddity's face order (OpenCascade's) is haecceity's is checked on every read (surface
//! type and area), and here over every recognition the repository has, 4,031 faces: every face
//! whose area differs from OpenCascade's by more than 1e-4 (relative, to a face of 1 mm²) is
//! listed, with its verdict and evidence, in `tests/fixtures/known_bridge_faces.json`.

#[path = "common/corpus.rs"]
mod corpus;
#[path = "common/facts.rs"]
mod facts;

use std::io::Read as _;
use std::path::{Path, PathBuf};
use std::process::Command;

use draftwright::bridge;
use draftwright::model::{self, Analysis};
use serde_json::Value;

fn fixture(path: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(path)
}

fn analysed(name: &str) -> Analysis {
    let step = fixture(&format!("parts/{name}.step"));
    let json = fixture(&format!("recognition/{name}.json"));
    model::analyse_with(&step, Some(&json)).unwrap().1
}

fn recognition(name: &str) -> Value {
    serde_json::from_str(
        &std::fs::read_to_string(fixture(&format!("recognition/{name}.json"))).unwrap(),
    )
    .unwrap()
}

/// The features read from *json* (edited) for the part *name*.
fn read(name: &str, json: &Value) -> Result<Vec<bridge::Feature>, String> {
    let (part, a) = model::analyse(&fixture(&format!("parts/{name}.step"))).unwrap();
    bridge::read(&part, &a.binding.source_sha256, &json.to_string()).map(|r| r.1)
}

#[test]
fn holes_carry_their_faces_sizes_axis_position_and_pattern() {
    let a = analysed("plate");
    let ids: Vec<_> = a.features.iter().map(|f| f.id.as_str()).collect();
    assert_eq!(ids, ["f:hole:f6", "f:hole:f7", "f:hole:f8", "f:hole:f9"]);
    let mut at = Vec::new();
    for (f, face) in a.features.iter().zip(6..) {
        assert_eq!(f.kind, "hole");
        assert_eq!(f.faces, [face]);
        let p = &f.fingerprint;
        assert_eq!(p.family, "holes");
        assert_eq!(p.sizes["diameter"], 6.0);
        assert_eq!(p.sizes["depth"], 5.0);
        // quiddity's working frame is not the part's: its axis is x there, z here.
        assert_eq!(f.parameters["axis"], serde_json::json!([-1.0, 0.0, 0.0]));
        assert_eq!(p.axis, Some([0.0, 0.0, 1.0]));
        let pattern = p.pattern.as_ref().unwrap();
        assert_eq!((pattern.count, pattern.pitch), (4, 20.0));
        assert_eq!(p.position[2], 0.0);
        at.push([p.position[0], p.position[1]]);
        assert!(!f.parameters.contains_key("dependents"));
    }
    at.sort_by(|a, b| a.partial_cmp(b).unwrap());
    assert_eq!(
        at,
        [[-20.0, -10.0], [-20.0, 10.0], [20.0, -10.0], [20.0, 10.0]]
    );
    let r = a.recognition.unwrap();
    assert!(r.recogniser.starts_with("quiddity "), "{}", r.recogniser);
}

#[test]
fn turned_features_are_in_the_parts_frame() {
    let a = analysed("turned");
    let summary: Vec<_> = a
        .features
        .iter()
        .map(|f| (f.id.as_str(), f.faces.clone(), f.fingerprint.position[2]))
        .collect();
    assert_eq!(
        summary,
        [
            ("f:fillet:1", vec![1], 0.4),
            ("f:fillet:2", vec![2], 39.6),
            ("f:fillet:3", vec![5], 40.4),
            ("f:fillet:4", vec![7], 69.6),
            ("f:turned_step:f0", vec![0], 20.0),
            ("f:turned_step:f6", vec![6], 55.0),
        ]
    );
    for f in &a.features {
        assert_eq!(f.fingerprint.axis, Some([0.0, 0.0, 1.0]), "{}", f.id);
        assert!(f.fingerprint.pattern.is_none());
    }
    let step = &a.features[4].fingerprint.sizes;
    assert_eq!((step["diameter"], step["axial_length"]), (30.0, 40.0));
    assert_eq!(analysed("prismatic").features.len(), 4);
}

#[test]
fn ids_are_unique_stable_and_independent_of_discovery_order() {
    for name in ["plate", "turned", "prismatic"] {
        let a = serde_json::to_string(&analysed(name)).unwrap();
        assert_eq!(a, serde_json::to_string(&analysed(name)).unwrap(), "{name}");
        let features = analysed(name).features;
        let mut ids: Vec<_> = features.iter().map(|f| &f.id).collect();
        ids.sort();
        ids.dedup();
        assert_eq!(ids.len(), features.len(), "{name}");

        let mut json = recognition(name);
        json["quiddity"]["features"]
            .as_array_mut()
            .unwrap()
            .reverse();
        assert_eq!(read(name, &json).unwrap(), features, "{name}");
    }
}

#[test]
fn quiddity_face_indices_are_mapped_to_the_parts() {
    // Relabel quiddity's working faces 6 and 7 as each other's file faces: the hole quiddity
    // found on working face 6 is on the part's face 7.
    let mut json = recognition("plate");
    let first = json["quiddity"]["features"][0].clone();
    assert_eq!(first["constituent_faces"], serde_json::json!([6]));
    let faces = json["quiddity"]["faces"].as_array_mut().unwrap();
    faces[6]["caller_index"] = 7.into();
    faces[7]["caller_index"] = 6.into();
    let features = read("plate", &json).unwrap();
    let moved = features
        .iter()
        .find(|f| Value::from(f.parameters.clone())["location"] == first["record"]["location"])
        .unwrap();
    assert_eq!(moved.faces, [7]);
}

#[test]
fn recognition_of_another_part_or_shape_is_refused() {
    let plate = fixture("parts/plate.step");
    let turned = fixture("recognition/turned.json");
    let e = model::analyse_with(&plate, Some(&turned)).unwrap_err();
    assert!(e.contains("different file"), "{e}");

    let mut json = recognition("plate");
    json["faces"][3]["area"] = 301.0.into();
    let e = read("plate", &json).unwrap_err();
    assert!(e.contains("face 3 has area"), "{e}");

    let mut json = recognition("plate");
    json["faces"][6]["surface"] = "plane".into();
    let e = read("plate", &json).unwrap_err();
    assert!(e.contains("face 6 is a cylinder here"), "{e}");

    let mut json = recognition("plate");
    json["faces"].as_array_mut().unwrap().pop();
    assert!(
        read("plate", &json)
            .unwrap_err()
            .contains("9 faces and the part 10")
    );

    let mut json = recognition("plate");
    let twin = json["quiddity"]["features"][1].clone();
    json["quiddity"]["features"][0]["constituent_faces"] = twin["constituent_faces"].clone();
    let e = read("plate", &json).unwrap_err();
    assert!(e.contains("cannot be told apart"), "{e}");

    let quiddity_alone = recognition("plate")["quiddity"].clone();
    assert!(
        read("plate", &quiddity_alone)
            .unwrap_err()
            .contains("tools/recognise.py")
    );
}

/// The analysis of plate.json (edited).
fn read_all(json: &Value) -> Result<(bridge::Recognition, Vec<bridge::Feature>), String> {
    let (part, a) = model::analyse(&fixture("parts/plate.step")).unwrap();
    bridge::read(&part, &a.binding.source_sha256, &json.to_string())
}

#[test]
fn refusals_and_evidence_are_not_features() {
    // quiddity's records as the corpus has them: a section recess it refused, and a face level.
    let mut json = recognition("plate");
    let features = json["quiddity"]["features"].as_array_mut().unwrap();
    features[0]["family"] = "section_recesses".into();
    features[0]["record_type"] = "SectionRecessRefusal".into();
    features[0]["record"] = serde_json::json!({"reason": "unsupported_support_geometry"});
    features[1]["family"] = "step_levels".into();
    features[1]["record_type"] = "FaceLevel".into();
    features[1]["record"] = serde_json::json!({"axis": "x", "level": 2.5});
    // The plate's hole pattern named those two holes; with them gone it no longer describes
    // the document (a pattern naming a hole that is not there is refused, below).
    json["quiddity"]["derived"]["hole_patterns"] = serde_json::json!([]);
    let (r, features) = read_all(&json).unwrap();
    let ids: Vec<_> = features.iter().map(|f| f.id.as_str()).collect();
    assert_eq!(ids, ["f:hole:f6", "f:hole:f7"]);
    assert_eq!(features[0].faces, [8]);
    assert_eq!(r.refusals.len(), 1);
    assert_eq!(r.refusals[0].subtype, "SectionRecessRefusal");
    assert_eq!(r.refusals[0].faces, [6]);
    assert_eq!(
        r.refusals[0].parameters["reason"],
        "unsupported_support_geometry"
    );
    assert_eq!(r.evidence.len(), 1);
    assert_eq!(
        (r.evidence[0].family.as_str(), &r.evidence[0].faces),
        ("step_levels", &vec![7])
    );

    // A hole pattern naming a member that is not a recognised hole is refused.
    let mut json = recognition("plate");
    json["quiddity"]["features"][0]["record_type"] = "FaceLevel".into();
    json["quiddity"]["features"][0]["family"] = "step_levels".into();
    let e = read_all(&json).unwrap_err();
    assert!(e.contains("not a recognised hole"), "{e}");

    // A record type with no home is refused, not guessed at.
    let mut json = recognition("plate");
    json["quiddity"]["features"][0]["record_type"] = "WarpDrive".into();
    let e = read_all(&json).unwrap_err();
    assert!(
        e.contains("WarpDrive (holes), which the bridge has no home for"),
        "{e}"
    );
}

#[test]
fn circular_face_patterns_count_and_pitch_their_instances() {
    // A circular face pattern whose instances are the plate's four holes, one face each.
    let mut json = recognition("plate");
    let instances: Vec<_> = (6..10)
        .map(|f| serde_json::json!({"face_indices": [f]}))
        .collect();
    json["quiddity"]["features"]
        .as_array_mut()
        .unwrap()
        .push(serde_json::json!({
            "index": 4,
            "family": "circular_face_patterns",
            "record_type": "CircularFacePattern",
            "record": {"count": 4},
            "constituent_faces": [6, 7, 8, 9],
            "instances": instances,
        }));
    let (_, features) = read_all(&json).unwrap();
    let p = features
        .iter()
        .find(|f| f.kind == "circular_face_pattern")
        .unwrap();
    assert_eq!(p.id, "f:circular_face_pattern:1");
    assert_eq!(p.faces, [6, 7, 8, 9]);
    let pattern = p.fingerprint.pattern.as_ref().unwrap();
    assert_eq!((pattern.count, pattern.pitch), (4, 20.0));
}

#[test]
fn unreadable_fingerprint_parts_are_refused() {
    let mut json = recognition("plate");
    json["quiddity"]["features"][0]["record"]["axis"] = "w".into();
    let e = read_all(&json).unwrap_err();
    assert!(e.contains("axis \"w\" is not an axis"), "{e}");

    let mut json = recognition("plate");
    let holes = &mut json["quiddity"]["derived"]["hole_patterns"][0]["holes"];
    holes[3]["location"] = Value::Null;
    let e = read_all(&json).unwrap_err();
    assert!(e.contains("member with no location"), "{e}");
}

#[test]
fn the_command_line_takes_features() {
    let run = |args: &[&str]| {
        let out = Command::new(env!("CARGO_BIN_EXE_draftwright"))
            .args(args)
            .output()
            .unwrap();
        let json: Value = serde_json::from_slice(&out.stdout).unwrap();
        (out.status.code(), json)
    };
    let step = fixture("parts/plate.step");
    let features = fixture("recognition/plate.json");
    let (step, features) = (step.to_str().unwrap(), features.to_str().unwrap());
    let (code, a) = run(&["analyse", step, "--features", features]);
    assert_eq!(code, Some(0));
    assert_eq!(a["features"].as_array().unwrap().len(), 4);
    let (code, _) = run(&["plan", step, "--features", features]);
    assert_eq!(code, Some(0));
    let wrong = fixture("recognition/turned.json");
    let (code, e) = run(&["analyse", step, "--features", wrong.to_str().unwrap()]);
    assert_eq!(code, Some(2));
    assert_eq!(e["error"]["code"], "invalid");
}

/// The AAG neighbourhood: repeats of one feature in like surroundings share it (position tells
/// them apart), and features in different surroundings do not.
#[test]
fn neighbourhoods_describe_how_a_feature_meets_the_part() {
    let plate = analysed("plate");
    let through = ["cylinder>out:plane:convex", "cylinder>out:plane:convex"];
    for f in &plate.features {
        assert_eq!(f.fingerprint.neighbourhood, through, "{}", f.id);
    }
    let turned = analysed("turned");
    let of = |id: &str| {
        &turned
            .features
            .iter()
            .find(|f| f.id == id)
            .unwrap()
            .fingerprint
            .neighbourhood
    };
    assert_eq!(
        of("f:fillet:f1"),
        &["torus>out:cylinder:smooth", "torus>out:plane:smooth"]
    );
    assert_eq!(
        of("f:turned_step:f0"),
        &["cylinder>out:torus:smooth", "cylinder>out:torus:smooth"]
    );
    assert_ne!(of("f:fillet:f1"), of("f:turned_step:f0"));
}

/// quiddity reports a rectangle of four as width and height, and an N×M lattice (RectGrid) as
/// rows, columns and their pitches; both are a grid (nist_ftc_10 has a RectGrid).
#[test]
fn a_rect_grid_is_a_grid() {
    let mut json = recognition("plate");
    let p = json["quiddity"]["derived"]["hole_patterns"][0]
        .as_object_mut()
        .unwrap();
    let (w, h) = (p.remove("width").unwrap(), p.remove("height").unwrap());
    for (k, v) in [
        ("rows", 2.into()),
        ("cols", 2.into()),
        ("row_pitch", h),
        ("col_pitch", w),
    ] {
        p.insert(k.into(), v);
    }
    let (part, a) = model::analyse(&fixture("parts/plate.step")).unwrap();
    let (recognition, _) =
        bridge::read(&part, &a.binding.source_sha256, &json.to_string()).unwrap();
    assert_eq!(recognition.patterns.len(), 1);
    assert_eq!(recognition.patterns[0].arrangement, "grid");
    assert_eq!(recognition.patterns[0].members.len(), 4);
}

/// A slot-pattern fixture part (`tests/fixtures/recognition/slot_patterns/`: plates with a
/// linear array and a grid of obround through slots, built by `make.py` there and recognised by
/// quiddity 0.3.9) and its recognition JSON.
fn slot_fixture(name: &str) -> (draftwright::haecceity::Part, Analysis, Value) {
    use std::io::Read as _;
    let dir = fixture("recognition/slot_patterns");
    let (part, a) = model::analyse(&dir.join(format!("{name}.step"))).unwrap();
    let mut text = String::new();
    flate2::read::GzDecoder::new(std::fs::File::open(dir.join(format!("{name}.json.gz"))).unwrap())
        .read_to_string(&mut text)
        .unwrap();
    (part, a, serde_json::from_str(&text).unwrap())
}

fn read_slots(
    name: &str,
    json: &Value,
) -> Result<(bridge::Recognition, Vec<bridge::Feature>), String> {
    let (part, a, _) = slot_fixture(name);
    bridge::read(&part, &a.binding.source_sha256, &json.to_string())
}

#[test]
fn slot_patterns_are_features_naming_their_member_slots() {
    let (_, _, json) = slot_fixture("slot_array");
    let (_, features) = read_slots("slot_array", &json).unwrap();
    let ids: Vec<_> = features.iter().map(|f| f.id.as_str()).collect();
    assert_eq!(
        ids,
        ["f:slot:f6", "f:slot:f10", "f:slot:f14", "f:slot_pattern:f6"]
    );
    let p = &features[3];
    assert_eq!(p.kind, "slot_pattern");
    assert_eq!(p.faces, (6..18).collect::<Vec<_>>());
    // quiddity's order of its members, by their IDs: its record's `slots` were their records.
    assert_eq!(
        p.parameters["slots"],
        serde_json::json!(["f:slot:f14", "f:slot:f10", "f:slot:f6"])
    );
    assert_eq!(p.parameters["pitch"], 25.0);
    assert_eq!(
        (
            p.fingerprint.family.as_str(),
            p.fingerprint.subtype.as_str()
        ),
        ("slot_patterns", "SlotArray")
    );
    // Each member's fingerprint carries the pattern's count and pitch, as a hole's in a hole
    // pattern does, and the pattern's own does too.
    for f in &features {
        let pattern = f.fingerprint.pattern.as_ref().unwrap();
        assert_eq!((pattern.count, pattern.pitch), (3, 25.0), "{}", f.id);
        assert!(!f.parameters.contains_key("dependents"), "{}", f.id);
    }

    let (_, _, json) = slot_fixture("slot_grid");
    let (_, features) = read_slots("slot_grid", &json).unwrap();
    let p = features.iter().find(|f| f.kind == "slot_pattern").unwrap();
    assert_eq!(p.id, "f:slot_pattern:f6");
    assert_eq!(p.fingerprint.subtype, "SlotGrid");
    assert_eq!(p.parameters["slots"].as_array().unwrap().len(), 6);
    assert_eq!(
        (p.parameters["rows"].as_u64(), p.parameters["cols"].as_u64()),
        (Some(3), Some(2))
    );
    let pattern = p.fingerprint.pattern.as_ref().unwrap();
    assert_eq!((pattern.count, pattern.pitch), (6, 30.0));
}

#[test]
fn a_slot_pattern_that_does_not_resolve_to_its_slots_is_refused() {
    let (_, _, json) = slot_fixture("slot_array");

    // A member record that is no recognised slot.
    let mut edited = json.clone();
    edited["quiddity"]["derived"]["slot_patterns"][0]["slots"][1]["w_center"] = 7.5.into();
    let e = read_slots("slot_array", &edited).unwrap_err();
    assert!(
        e.contains("a slot pattern has a member that is not a recognised slot"),
        "{e}"
    );

    // A member's own pattern links that disagree with the pattern.
    let mut edited = json.clone();
    let slot = edited["quiddity"]["features"]
        .as_array_mut()
        .unwrap()
        .iter_mut()
        .find(|f| f["record_type"] == "Slot")
        .unwrap();
    slot["record"]["dependents"].as_array_mut().unwrap().pop();
    let e = read_slots("slot_array", &edited).unwrap_err();
    assert!(
        e.contains("as its pattern siblings, but its slot pattern has"),
        "{e}"
    );

    // A pattern with neither a linear array's nor a grid's sizes.
    let mut edited = json.clone();
    edited["quiddity"]["derived"]["slot_patterns"][0]
        .as_object_mut()
        .unwrap()
        .remove("pitch");
    let e = read_slots("slot_array", &edited).unwrap_err();
    assert!(
        e.contains("neither a grid's nor a linear array's sizes"),
        "{e}"
    );
}

/// ADR3-4 on the bridge path: quiddity's document is read only in the format, version, release,
/// coordinate space, families and derived collections the bridge knows; anything else is refused
/// with the mismatch named.
#[test]
fn a_document_the_bridge_does_not_know_is_refused() {
    let refused = |edit: &dyn Fn(&mut Value)| {
        let mut json = recognition("plate");
        edit(&mut json);
        read_all(&json).unwrap_err()
    };
    let e = refused(&|j| j["quiddity"]["format_version"] = 4.into());
    assert!(
        e.contains("version 4; the bridge reads \"quiddity-recognition\" version 3 only"),
        "{e}"
    );
    let e = refused(&|j| j["quiddity"]["format"] = "quiddity-edit".into());
    assert!(e.contains("\"quiddity-edit\" version 3"), "{e}");
    let e = refused(&|j| {
        j["quiddity"]["package"]["version"] = "0.5.0".into();
        j["recogniser"] = "quiddity 0.5.0".into();
    });
    assert!(
        e.contains("written by quiddity 0.5.0; the bridge is checked against quiddity 0.3.9 and 0.4.0.dev0 only"),
        "{e}"
    );
    let e = refused(&|j| j["recogniser"] = "quiddity 0.3.9".into());
    assert!(
        e.contains("says it was written by \"quiddity 0.3.9\", but quiddity's document by quiddity 0.4.0.dev0"),
        "{e}"
    );
    let e = refused(&|j| j["quiddity"]["coordinate_space"] = "caller".into());
    assert!(e.contains("in \"caller\" coordinates"), "{e}");
    let e = refused(&|j| j["quiddity"]["features"][0]["family"] = "bores".into());
    assert!(
        e.contains("a family \"bores\" (a HoleRecord), which the bridge does not know"),
        "{e}"
    );
    let e = refused(&|j| j["quiddity"]["features"][0]["family"] = "slots".into());
    assert!(
        e.contains("a HoleRecord in slots; the bridge knows it only in holes"),
        "{e}"
    );
    let e = refused(&|j| j["quiddity"]["derived"]["pocket_patterns"] = serde_json::json!([]));
    assert!(
        e.contains("derives \"pocket_patterns\", which the bridge does not know"),
        "{e}"
    );
    // The fixtures' own documents, from both releases, are read.
    assert!(read_all(&recognition("plate")).is_ok());
    let (_, _, json) = slot_fixture("slot_array");
    assert_eq!(json["quiddity"]["package"]["version"], "0.3.9");
    assert!(read_slots("slot_array", &json).is_ok());
}

fn gunzip(path: &Path) -> String {
    let mut text = String::new();
    flate2::read::GzDecoder::new(std::fs::File::open(path).unwrap())
        .read_to_string(&mut text)
        .unwrap();
    text
}

/// Every recognition the repository has, as (set, part, STEP file, JSON): the fixtures beside
/// their parts, and the corpus parts' only when the corpus is found.
fn recognitions() -> Vec<(String, String, PathBuf, String)> {
    let mut out = Vec::new();
    for part in ["boss_circle", "plate", "prismatic", "turned", "two_grids"] {
        let json = std::fs::read_to_string(fixture(&format!("recognition/{part}.json"))).unwrap();
        out.push((
            "parts".into(),
            part.into(),
            fixture(&format!("parts/{part}.step")),
            json,
        ));
    }
    let listed = |set: &str, suffix: &str| {
        let mut names: Vec<String> = std::fs::read_dir(fixture(&format!("recognition/{set}")))
            .unwrap()
            .filter_map(|e| {
                let name = e.unwrap().file_name().to_string_lossy().into_owned();
                name.strip_suffix(suffix).map(String::from)
            })
            .collect();
        names.sort();
        names
    };
    for set in ["polygonal_stock", "slot_patterns", "steps"] {
        for part in listed(set, ".step") {
            let json = gunzip(&fixture(&format!("recognition/{set}/{part}.json.gz")));
            let step = fixture(&format!("recognition/{set}/{part}.step"));
            out.push((set.into(), part, step, json));
        }
    }
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED (corpus parts): the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return out;
    };
    for set in ["corpus", "sheets"] {
        for part in listed(set, ".json.gz") {
            let json = gunzip(&fixture(&format!("recognition/{set}/{part}.json.gz")));
            let step = dir.join(facts::load(&part).binding.file);
            out.push((set.into(), part, step, json));
        }
    }
    for (set, part, file) in [
        ("dims", "ftc07", "nist/nist_ftc_07_asme1_rd.stp"),
        ("dims", "m10047", "mfcadpp/10047.step"),
        ("dims", "m10245", "mfcadpp/10245.step"),
        ("dims", "m11512", "mfcadpp_regressions/11512.step"),
        ("steps", "h363", "mfcadpp_holdout/363.step"),
        ("steps", "m10000", "mfcadpp/10000.step"),
        ("steps", "m10060", "mfcadpp/10060.step"),
        ("steps", "m1007", "mfcadpp/1007.step"),
    ] {
        let json = gunzip(&fixture(&format!("recognition/{set}/{part}.json.gz")));
        out.push((set.into(), part.into(), dir.join(file), json));
    }
    out
}

#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct KnownFace {
    set: String,
    part: String,
    face: usize,
    surface: String,
    python: f64,
    rust: f64,
    verdict: String,
    evidence: String,
}

/// Every recognition is read, and every face whose area differs from OpenCascade's (the JSON's)
/// by more than 1e-4 is listed, with the same areas, a verdict and evidence; a listed face that
/// stops differing must come off. The largest listed difference is 2.5e-3, a quarter of the
/// bridge's 1e-2 for spheres and B-splines (`bridge::check_faces`).
#[test]
fn face_areas_differ_from_opencascades_only_as_listed() {
    let known: Vec<KnownFace> =
        serde_json::from_str(&std::fs::read_to_string(fixture("known_bridge_faces.json")).unwrap())
            .unwrap();
    let verdicts = [
        "rust-correct",
        "rust-wrong",
        "equivalent",
        "undetermined",
        "not-applicable",
    ];
    let close = |a: f64, b: f64| (a - b).abs() <= 1e-9 * a.abs().max(1.0);
    let mut seen = vec![false; known.len()];
    let mut unlisted = Vec::new();
    for (set, name, step, json) in recognitions() {
        let (part, a) = model::analyse(&step).unwrap();
        if let Err(e) = bridge::read(&part, &a.binding.source_sha256, &json) {
            panic!("the bridge refuses {set}/{name}: {e}");
        }
        let json: Value = serde_json::from_str(&json).unwrap();
        for (i, f) in json["faces"].as_array().unwrap().iter().enumerate() {
            let python = f["area"].as_f64().unwrap();
            let rust = part.face_mass(i).unwrap()[0];
            if (rust - python).abs() <= 1e-4 * python.max(1.0) {
                continue;
            }
            match known
                .iter()
                .position(|k| k.set == set && k.part == name && k.face == i)
            {
                Some(k) => {
                    let listed = &known[k];
                    assert!(
                        close(listed.python, python) && close(listed.rust, rust),
                        "{set}/{name} face {i}: listed python {} rust {}, now python {python} rust {rust}",
                        listed.python,
                        listed.rust
                    );
                    assert_eq!(listed.surface, f["surface"].as_str().unwrap());
                    seen[k] = true;
                }
                None => unlisted.push(format!(
                    "{set}/{name} face {i} ({}): python {python:?} rust {rust:?}",
                    f["surface"]
                )),
            }
        }
    }
    assert!(
        unlisted.is_empty(),
        "unlisted face areas:\n{}",
        unlisted.join("\n")
    );
    for (k, s) in known.iter().zip(&seen) {
        assert!(verdicts.contains(&k.verdict.as_str()), "{}", k.verdict);
        assert!(!k.evidence.is_empty());
        // Every entry is a corpus part's, seen only when the corpus is found.
        if corpus::dir().is_some() {
            assert!(
                s,
                "{}/{} face {} no longer differs: take it off",
                k.set, k.part, k.face
            );
        }
    }
}

/// nist_ctc_04 is read: its face 367 (13.080630 here, 13.100238 in the JSON) is the same face,
/// OpenCascade's default precision being off its own converged area (see
/// `known_bridge_faces.json`). A different face in its place is still refused: a sphere of
/// another size, or a face of another type.
#[test]
fn nist_ctc_04_is_read_and_a_face_out_of_place_is_refused() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let (part, a) = model::analyse(&dir.join(facts::load("ctc04").binding.file)).unwrap();
    let text = gunzip(&fixture("recognition/sheets/ctc04.json.gz"));
    bridge::read(&part, &a.binding.source_sha256, &text).unwrap();

    let json: Value = serde_json::from_str(&text).unwrap();
    let faces = json["faces"].as_array().unwrap();
    let swapped = |j: usize| {
        let mut edited = json.clone();
        edited["faces"][367] = faces[j].clone();
        edited["faces"][j] = faces[367].clone();
        bridge::read(&part, &a.binding.source_sha256, &edited.to_string()).unwrap_err()
    };
    // The nearest-sized other sphere differing by more than 1e-2.
    let area = faces[367]["area"].as_f64().unwrap();
    let other = (0..faces.len())
        .filter(|&j| faces[j]["surface"] == "sphere")
        .filter(|&j| (faces[j]["area"].as_f64().unwrap() - area).abs() > 1e-2 * area)
        .min_by(|&x, &y| {
            let d = |j: usize| (faces[j]["area"].as_f64().unwrap() - area).abs();
            d(x).total_cmp(&d(y))
        })
        .unwrap();
    let e = swapped(other);
    assert!(
        e.contains(&format!("face {} has area", other.min(367))),
        "{e}"
    );
    let plane = (0..faces.len())
        .find(|&j| faces[j]["surface"] == "plane")
        .unwrap();
    let e = swapped(plane);
    assert!(e.contains(&format!("face {plane} is a plane here")), "{e}");
}

/// Two faces of the plate swapped in the JSON are refused, though the file is the same.
#[test]
fn swapped_faces_are_refused() {
    let mut json = recognition("plate");
    let (a, b) = (json["faces"][0].clone(), json["faces"][1].clone());
    json["faces"][0] = b;
    json["faces"][1] = a;
    let e = read("plate", &json).unwrap_err();
    assert!(e.contains("face 0 has area"), "{e}");
}
