//! Stable identity (`draftwright.spec/2`; ADR R1 invariants 5 and 9), the editor thin slice's
//! stream S1, as checks:
//! - a dimension's ID never names its view, so moving a dimension to another view, omitting a
//!   view, and changing the sheet or the scale keep every surviving item's ID;
//! - a PMI item's ID is its record's semantic identity, so the same intent written again by
//!   specify-core-rust (new Part 21 numbers) keeps every PMI item's ID;
//! - a `draftwright.spec/1` spec migrates and plans as the 0.1.0-alpha.1 build planned it, and
//!   an ambiguous key is refused with its candidates.
//!
//! The parts are the slice's (the editor's thin-slice plan, section 3.1): the editor's mounting
//! plate (`tests/fixtures/parts/mounting_plate.step`, `tests/fixtures/identity/README.md`), NIST
//! CTC-01 with its PMI (`tests/fixtures/pmi/step/nist_ctc_01_asme1_ap242-e1.stp.gz`) and, from
//! the corpus, mfcadpp 10127 and GRM-03; and the test plate with its Python recognition.

#[path = "common/corpus.rs"]
mod corpus;

use std::collections::{BTreeMap, BTreeSet};
use std::io::Read as _;
use std::path::{Path, PathBuf};

use draftwright::api;
use draftwright::model::{self, Analysis, DimensionSpec, Plan, Spec};
use draftwright::pmi::Reading;
use serde_json::{Value, json};

fn fixture(path: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(path)
}

fn gunzip(path: &Path) -> Vec<u8> {
    let mut out = Vec::new();
    flate2::read::GzDecoder::new(std::fs::File::open(path).unwrap())
        .read_to_end(&mut out)
        .unwrap();
    out
}

/// *gz* decompressed to a scratch file of its own named *name* (a binding names its part by the
/// file's stem), for the readers that take a path.
fn scratch(gz: &Path, name: &str) -> PathBuf {
    static N: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
    let n = N.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR"))
        .join("identity")
        .join(format!("{}-{n}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join(name);
    std::fs::write(&path, gunzip(gz)).unwrap();
    path
}

/// The mounting plate with its recognition, as the command line reads it with `--features`.
fn plate() -> Analysis {
    let features = fixture("recognition/plate.json");
    model::analyse_with(&fixture("parts/plate.step"), Some(&features))
        .unwrap()
        .1
}

/// An AP242 fixture (`tests/fixtures/pmi/step/<file>.gz`), analysed as the command line does.
fn ap242(file: &str) -> Analysis {
    let path = scratch(&fixture(&format!("pmi/step/{file}.gz")), file);
    model::analyse_native(&path).unwrap().1
}

/// The slice's parts this checkout has: the plate and CTC-01 always; 10127 and GRM-03 from the
/// corpus (skipped without it, unless it is required).
fn slice() -> Vec<(String, Analysis)> {
    let mut out = vec![
        ("plate".to_string(), plate()),
        (
            "mounting_plate".to_string(),
            model::analyse_native(&fixture("parts/mounting_plate.step"))
                .unwrap()
                .1,
        ),
        (
            "nist_ctc_01_asme1_ap242-e1".to_string(),
            ap242("nist_ctc_01_asme1_ap242-e1.stp"),
        ),
    ];
    if let Some(dir) = corpus::dir() {
        for file in [
            "mfcadpp/10127.step",
            "gramel/GRM-03_thumbwheel_drive_screw.step",
        ] {
            out.push((
                file.to_string(),
                model::analyse_native(&dir.join(file)).unwrap().1,
            ));
        }
    } else {
        eprintln!("SKIPPED: the corpus parts (the corpus is not found)");
    }
    out
}

/// Every item ID the plan names: its dimensions (drawn and omitted), GD&T items, drawing-wide
/// requirements, notes and tables.
fn names(plan: &Plan) -> BTreeSet<String> {
    let mut out = BTreeSet::new();
    out.extend(plan.dimensions.iter().map(|d| d.id.clone()));
    out.extend(plan.omitted.iter().map(|d| d.id.clone()));
    out.extend(plan.gdt.iter().map(|g| g.id.clone()));
    out.extend(plan.requirements.iter().map(|r| r.id.clone()));
    out.extend(plan.notes.iter().map(|n| n.id.clone()));
    out.extend(plan.tables.iter().map(|t| t.id.clone()));
    out
}

/// Each item's ID by what it is, independent of its ID: a dimension by its own measurement (its
/// first: the feature and parameter it states), a GD&T item or requirement by its record's
/// source. A meaning two items share is left out (it would not tell them apart).
fn meanings(plan: &Plan) -> BTreeMap<String, String> {
    let mut all: Vec<(String, String)> = Vec::new();
    for d in &plan.dimensions {
        if let Some(m) = d.measurements.first() {
            all.push((format!("{}|{}", m.feature, m.parameter), d.id.clone()));
        }
    }
    for g in &plan.gdt {
        all.push((format!("source {}", g.source), g.id.clone()));
    }
    for r in &plan.requirements {
        all.push((format!("source {}", r.source), r.id.clone()));
    }
    let mut count: BTreeMap<&str, usize> = BTreeMap::new();
    for (k, _) in &all {
        *count.entry(k).or_default() += 1;
    }
    all.iter()
        .filter(|(k, _)| count[k.as_str()] == 1)
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect()
}

/// Every item *after* names is one *before* named, and each item both plans have (by what it
/// is) has one ID in both.
fn survives(part: &str, change: &str, before: &Plan, after: &Plan) {
    let (b, a) = (names(before), names(after));
    let new: Vec<&String> = a.difference(&b).collect();
    assert!(new.is_empty(), "{part}, {change}: new IDs {new:?}");
    let (b, a) = (meanings(before), meanings(after));
    let mut both = 0;
    for (k, id) in &a {
        if let Some(was) = b.get(k) {
            assert_eq!(id, was, "{part}, {change}: {k} was {was}, is {id}");
            both += 1;
        }
    }
    assert!(both > 0, "{part}, {change}: no item in both plans");
}

fn principal(view: &str) -> &str {
    view.trim_start_matches("v:")
}

/// A spec moving one of *base*'s dimensions to another shown view that can carry it
/// (`dimensions/<id>/view`): the first dimension, in plan order, for which there is one; none
/// when no dimension has one, with the refusals met.
fn moved(a: &Analysis, base: &Plan) -> Result<(Spec, String, String), Vec<String>> {
    let mut errors = Vec::new();
    for d in &base.dimensions {
        for v in ["front", "plan", "side"] {
            if v == principal(&d.view) || !base.views.iter().any(|x| x.id == format!("v:{v}")) {
                continue;
            }
            let mut spec = Spec::default();
            spec.dimensions.insert(
                d.id.clone(),
                DimensionSpec {
                    view: Some(v.into()),
                    ..Default::default()
                },
            );
            match model::plan(a, &spec) {
                Ok(_) => return Ok((spec, d.id.clone(), v.to_string())),
                Err(e) => errors.push(e.to_string()),
            }
        }
    }
    Err(errors)
}

/// Moving a dimension to another view, omitting a view, and changing the sheet or the scale keep
/// every surviving item's ID: an ID names what an item is, never where it is drawn.
#[test]
fn every_surviving_item_keeps_its_id_across_a_moved_dimension_an_omitted_view_and_the_sheet_and_scale()
 {
    for (part, a) in slice() {
        let base = model::plan(&a, &Spec::default()).unwrap();

        // GRM-03 is turned: its sheet shows the front and side, and what one carries the other
        // looks along (the plan, which could, is not drawn).
        let id = match moved(&a, &base) {
            Ok((spec, id, view)) => {
                let after = model::plan(&a, &spec).unwrap();
                let d = after.dimensions.iter().find(|d| d.id == id).unwrap();
                assert_eq!(d.view, format!("v:{view}"), "{part}: {id}");
                assert!(d.from_spec.iter().any(|f| f == "view"), "{part}: {d:?}");
                survives(
                    &part,
                    &format!("{id} moved to the {view} view"),
                    &base,
                    &after,
                );
                id
            }
            Err(errors) => {
                assert!(
                    part.contains("GRM-03"),
                    "{part}: nothing moves: {errors:#?}"
                );
                base.dimensions[0].id.clone()
            }
        };

        // The view that carried it omitted: it and the others there move, under their IDs.
        let was = base.dimensions.iter().find(|d| d.id == id).unwrap();
        let mut spec = Spec::default();
        spec.views
            .entry(principal(&was.view).to_string())
            .or_default()
            .omit = true;
        let after = model::plan(&a, &spec).unwrap();
        let d = after.dimensions.iter().find(|d| d.id == id).unwrap();
        assert_ne!(d.view, was.view, "{part}: {id}");
        survives(
            &part,
            &format!("the {} view omitted", was.view),
            &base,
            &after,
        );

        let mut spec = Spec::default();
        spec.sheet.size = Some(if base.sheet.size == "A0" { "A1" } else { "A0" }.into());
        let after = model::plan(&a, &spec).unwrap();
        assert_ne!(after.sheet.size, base.sheet.size);
        survives(&part, "another sheet", &base, &after);

        let mut spec = Spec::default();
        spec.sheet.scale = Some(
            if base.sheet.scale == "1:2" {
                "1:1"
            } else {
                "1:2"
            }
            .into(),
        );
        let after = model::plan(&a, &spec).unwrap();
        assert_ne!(after.sheet.scale, base.sheet.scale);
        survives(&part, "another scale", &base, &after);
    }
}

/// The spec's view for a dimension must be one that can carry it (refused with those that can);
/// one the spec omits leaves the dimension in its default view, said with a warning; a view that
/// is not a principal view is refused by `edit` too.
#[test]
fn a_dimensions_view_is_checked_and_an_omitted_one_is_reported() {
    let a = plate();
    // The plate's height is drawn in the front view, and can be in the side view; the plan view
    // looks along it.
    let height = "d:envelope/height.length";
    let spec: Spec = serde_json::from_value(json!({
        "dimensions": { height: { "view": "plan" } }
    }))
    .unwrap();
    let e = model::plan(&a, &spec).unwrap_err();
    assert!(
        e.message.contains("cannot be carried by the plan view"),
        "{e}"
    );
    assert!(
        !e.choices.is_empty() && !e.choices.contains(&"plan".to_string()),
        "{e}"
    );

    let spec: Spec = serde_json::from_value(json!({
        "views": { "side": { "omit": true } },
        "dimensions": { height: { "view": "side" } }
    }))
    .unwrap();
    let plan = model::plan(&a, &spec).unwrap();
    let d = plan.dimensions.iter().find(|d| d.id == height).unwrap();
    assert_eq!(d.view, "v:front");
    assert!(!d.from_spec.iter().any(|f| f == "view"), "{d:?}");
    let p = plan
        .problems
        .iter()
        .find(|p| p.code == "dimension_view_not_shown")
        .unwrap();
    assert_eq!(p.subjects, [height]);

    let e = model::edit(
        &Spec::default(),
        &json!([{ "op": "set", "path": "dimensions/d:envelope~1height.length/view", "value": "top" }]),
    )
    .unwrap_err();
    assert_eq!(e.choices, ["front", "plan", "side"], "{e}");
}

/// The PMI item IDs of *a*: every record's identity, by its source.
fn identities(a: &Analysis) -> BTreeMap<String, String> {
    match &a.pmi {
        Some(Reading::Read(r)) => r.identities().ids,
        _ => panic!("no PMI"),
    }
}

/// The same intent written again through specify-core-rust (its `write`, in Replace mode, onto
/// the file it wrote, on the mounting plate: `tests/fixtures/identity/README.md`) gives every record new Part 21
/// numbers, and every PMI item the same ID: the identities, and every PMI item's ID and what it
/// states in the plan.
#[test]
fn the_same_intent_written_again_keeps_every_pmi_items_id() {
    let first = model::analyse_native(&scratch(
        &fixture("identity/plate_intent.step.gz"),
        "mounting_plate.step",
    ))
    .unwrap()
    .1;
    let again = model::analyse_native(&scratch(
        &fixture("identity/plate_intent_rewritten.step.gz"),
        "mounting_plate.step",
    ))
    .unwrap()
    .1;
    let (one, two) = (identities(&first), identities(&again));
    assert!(one.len() >= 5, "{one:?}");
    // New Part 21 numbers: no record keeps its source.
    let sources: BTreeSet<&String> = one.keys().collect();
    assert!(two.keys().all(|s| !sources.contains(s)), "{one:?}\n{two:?}");
    let ids = |m: &BTreeMap<String, String>| m.values().cloned().collect::<BTreeSet<_>>();
    assert_eq!(ids(&one), ids(&two));

    let spec = Spec::default();
    let (p1, p2) = (
        model::plan(&first, &spec).unwrap(),
        model::plan(&again, &spec).unwrap(),
    );
    let pmi = |p: &Plan| -> BTreeMap<String, String> {
        let mut out: BTreeMap<String, String> = p
            .gdt
            .iter()
            .map(|g| (g.id.clone(), g.cells.join(" | ")))
            .collect();
        out.extend(
            p.requirements
                .iter()
                .map(|r| (r.id.clone(), r.text.clone())),
        );
        for d in &p.dimensions {
            for m in d
                .measurements
                .iter()
                .filter(|m| m.feature.starts_with("pmi:"))
            {
                out.insert(m.feature.clone(), d.label.clone());
            }
        }
        out
    };
    let (a, b) = (pmi(&p1), pmi(&p2));
    assert!(!a.is_empty());
    assert_eq!(a, b);
    assert_eq!(names(&p1), names(&p2));
}

/// Records that share an identity are told apart by their labels; records whose labels are the
/// same too get no ID, and are not drawn, naming each other (ADR R1 invariant 5: refused, never
/// guessed).
#[test]
fn records_that_cannot_be_told_apart_are_refused_not_guessed() {
    let a = ap242("bracket_positions.step");
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let frame = plan
        .gdt
        .iter()
        .find(|g| g.kind == "control_frame")
        .unwrap()
        .clone();
    let Some(Reading::Read(report)) = &a.pmi else {
        panic!("no PMI")
    };
    let r = report
        .records
        .iter()
        .find(|r| r.source_id == frame.source)
        .unwrap()
        .clone();

    // A second segment with another magnitude: both drawn, told apart by their labels.
    let mut b = a.clone();
    let Some(Reading::Read(report)) = &mut b.pmi else {
        unreachable!()
    };
    let mut twin = r.clone();
    twin.source_id = "geometric_tolerance:#999999".into();
    twin.part21_id = 999_999;
    twin.label = format!("{} (twin)", r.label);
    report.records.push(twin.clone());
    let ids = report.identities();
    let (x, y) = (&ids.ids[&r.source_id], &ids.ids[&twin.source_id]);
    assert_ne!(x, y);
    assert!(x.starts_with(&format!("{}:t=", frame.id)), "{x}");
    let plan = model::plan(&b, &Spec::default()).unwrap();
    for id in [x, y] {
        assert!(plan.gdt.iter().any(|g| g.id == *id), "{id}");
    }

    // The same label too: neither has an ID, and neither is drawn.
    let Some(Reading::Read(report)) = &mut b.pmi else {
        unreachable!()
    };
    report.records.last_mut().unwrap().label = r.label.clone();
    assert!(!report.identities().ids.contains_key(&r.source_id));
    let plan = model::plan(&b, &Spec::default()).unwrap();
    assert!(
        !plan
            .gdt
            .iter()
            .any(|g| g.source == r.source_id || g.source == twin.source_id)
    );
    for s in [&r.source_id, &twin.source_id] {
        let p = plan
            .problems
            .iter()
            .find(|p| p.code == "pmi_not_lowered" && p.refs.first() == Some(s))
            .unwrap_or_else(|| panic!("{s}: {:#?}", plan.problems));
        assert!(
            p.message.contains("could not be told from"),
            "{}",
            p.message
        );
    }
}

/// Every Part 21 instance the PMI items of the slice's PMI part could carry is absent from its
/// item IDs (they are never positional).
#[test]
fn no_pmi_item_is_named_by_a_part_21_instance() {
    let a = ap242("nist_ctc_01_asme1_ap242-e1.stp");
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let ids: Vec<String> = names(&plan)
        .into_iter()
        .chain(
            plan.dimensions
                .iter()
                .flat_map(|d| d.measurements.iter().map(|m| m.feature.clone())),
        )
        .filter(|id| id.contains("pmi:"))
        .collect();
    assert!(!ids.is_empty());
    for id in ids {
        assert!(!id.contains('#'), "{id}");
    }
}

/// A version 1 spec and the plan the 0.1.0-alpha.1 build made of it
/// (`tests/fixtures/identity/<part>.spec1.json`, `<part>.plan1.json.gz`): the spec migrates
/// (`draftwright.spec/2`) and plans identically, every ID read as it now is (its `@<view>`
/// dropped, a PMI source named by its record's identity) and the GD&T items in the order of
/// their new IDs. What the thin slice's stream S2 added is set aside ([`without_typed_state`]).
/// Parts whose plan the planner has changed since 0.1.0-alpha.1: their oracle is the version 2
/// spec renamed by hand ([`renamed`]), planned now, in place of the alpha 1 plan.
/// plate: H6's sheet rule (2026-10-12) draws its open sheet at the smallest fit's largest
/// scale, A4 at 2:1 where alpha 1 kept A4 at 1:1, so its alpha geometry is no longer the
/// oracle; the independently renamed version 2 spec, planned now, is.
const REPLANNED: [&str; 2] = ["bracket_positions", "plate"];

#[test]
fn version_1_specs_migrate_and_plan_identically() {
    // As the command line planned them: the plate with its recognition (`--features`), the AP242
    // part with Rust quiddity's.
    let recognition = fixture("recognition/plate.json");
    let cases = [
        (
            "plate",
            fixture("parts/plate.step"),
            Some(recognition.as_path()),
        ),
        (
            "bracket_positions",
            scratch(
                &fixture("pmi/step/bracket_positions.step.gz"),
                "bracket_positions.step",
            ),
            None,
        ),
    ];
    for (part, step, python) in cases {
        let mut spec: Spec = serde_json::from_str(
            &std::fs::read_to_string(fixture(&format!("identity/{part}.spec1.json"))).unwrap(),
        )
        .unwrap();
        assert_eq!(spec.schema, "draftwright.spec/1");
        let old: Value =
            serde_json::from_slice(&gunzip(&fixture(&format!("identity/{part}.plan1.json.gz"))))
                .unwrap();
        let features = python.map_or(api::Features::Native, api::Features::Python);
        if part == "bracket_positions" {
            // v0.2.3 (#127) inserts concave fillets and changes the grouped presenter.
            // Keep this historical schema-migration scenario on its same physical R1.5
            // round (face 23), independently of both ordinal IDs and the migration code.
            let (_, a) = model::analyse_native(&step).unwrap();
            let f = a
                .features
                .iter()
                .find(|f| f.kind == "fillet" && f.faces == [23])
                .unwrap();
            assert_eq!(f.parameters["radius"], 1.5);
            let current = model::plan(&a, &Spec::default()).unwrap();
            let d = current
                .dimensions
                .iter()
                .find(|d| d.measurements.iter().any(|m| m.feature == f.id))
                .unwrap();
            let entry = spec
                .dimensions
                .remove("d:f:fillet:f3/fillet.radius@front")
                .unwrap();
            spec.dimensions.insert(format!("{}@front", d.id), entry);
        }
        let plan = api::plan_from(&step, features, &spec).unwrap();
        assert_eq!(plan.spec.schema, model::SPEC_SCHEMA);
        let (_, analysis) = match python {
            Some(json) => model::analyse_with(&step, Some(json)).unwrap(),
            None => model::analyse_native(&step).unwrap(),
        };
        let ids = match &analysis.pmi {
            Some(Reading::Read(r)) => r.identities().ids,
            _ => BTreeMap::new(),
        };
        let mut expected = if REPLANNED.contains(&part) {
            // The planner has changed this part's plan since 0.1.0-alpha.1 (one span, one
            // dimension for PMI: `share_pmi`; ISO 128 furniture clearance), so the oracle is
            // the plan of the spec renamed by hand, independently of the migration, as a
            // version 2 spec.
            let mut v2 = renamed(&serde_json::to_value(&spec).unwrap(), &ids);
            v2["schema"] = model::SPEC_SCHEMA.into();
            let v2: Spec = serde_json::from_value(v2).unwrap();
            serde_json::to_value(api::plan_from(&step, features, &v2).unwrap()).unwrap()
        } else {
            renamed(&old, &ids)
        };
        if !REPLANNED.contains(&part) {
            // The historical geometry oracle is intact; G1's new bounded report has its own
            // literal expectation, rather than being dropped from the comparison.
            let report: Value = serde_json::from_str(
                &std::fs::read_to_string(fixture(&format!("identity/{part}.reporting.json")))
                    .unwrap(),
            )
            .unwrap();
            expected["status"] = report["status"].clone();
            expected["completeness"] = report["completeness"].clone();
            expected["problems"]
                .as_array_mut()
                .unwrap()
                .extend(report["problems"].as_array().unwrap().iter().cloned());
        }
        expected["spec"]["schema"] = model::SPEC_SCHEMA.into();
        if part == "plate" && !REPLANNED.contains(&part) {
            // The G5 title-furniture additions are golden data beside the legacy capture;
            // the current migration oracle checks these against independent golden data.
            // Their geometry/reporting verdicts are in known_title_block.json.
            let furniture: Value = serde_json::from_str(
                &std::fs::read_to_string(fixture("identity/plate.title_furniture.json")).unwrap(),
            )
            .unwrap();
            let problems =
                without_typed_state(json!({ "sheet": {}, "problems": furniture["problems"] }));
            if REPLANNED.contains(&part) {
                // The current oracle already carries G5 furniture: verify the independent
                // golden data once rather than adding its warning a second time.
                assert_eq!(
                    expected["sheet"]["projection_symbol"],
                    furniture["projection_symbol"]
                );
                let current = without_typed_state(expected.clone());
                for problem in problems["problems"].as_array().unwrap() {
                    assert_eq!(
                        current["problems"]
                            .as_array()
                            .unwrap()
                            .iter()
                            .filter(|p| *p == problem)
                            .count(),
                        1,
                        "{part}: title-furniture problem must occur exactly once"
                    );
                }
            } else {
                expected["sheet"]["projection_symbol"] = furniture["projection_symbol"].clone();
                expected["problems"]
                    .as_array_mut()
                    .unwrap()
                    .extend(problems["problems"].as_array().unwrap().iter().cloned());
            }
        }
        if part == "plate" && !REPLANNED.contains(&part) {
            // H7 moves this callout into proven blank face. A replanned part's oracle is
            // planned now, so it already carries the interior placement on its own sheet.
            // The literal basis delta has a verdict in known_interior.json.
            let interior: Value = serde_json::from_str(
                &std::fs::read_to_string(fixture("identity/plate.interior_basis.json")).unwrap(),
            )
            .unwrap();
            let d = expected["dimensions"]
                .as_array_mut()
                .unwrap()
                .iter_mut()
                .find(|d| d["id"] == interior["id"])
                .unwrap();
            d["basis"] = interior["basis"].clone();
            d["placed"] = interior["placed"].clone();
            d["region"] = interior["region"].clone();
        }
        if REPLANNED.contains(&part) {
            expected = without_typed_state(expected);
        }
        let mut got = without_typed_state(serde_json::to_value(&plan).unwrap());
        for p in [&mut expected, &mut got] {
            // A problem's subjects and its references (S2) are what 0.1.0-alpha.1's subjects
            // were, in some order.
            for q in p["problems"].as_array_mut().unwrap() {
                q["subjects"]
                    .as_array_mut()
                    .unwrap()
                    .sort_by(|a, b| a.as_str().cmp(&b.as_str()));
            }
            // The GD&T items, and so the problems about them, follow their IDs' order.
            for list in ["gdt", "requirements", "problems"] {
                if let Some(items) = p[list].as_array_mut() {
                    items.sort_by(|a, b| a["id"].as_str().cmp(&b["id"].as_str()));
                }
            }
        }
        assert!(
            expected == got,
            "{part}: the migrated plan differs from 0.1.0-alpha.1's:\n{}",
            first_difference(&expected, &got, "")
        );
    }
}

/// *plan* as 0.1.0-alpha.1 wrote it, without what the thin slice's stream S2 added: each item's
/// `state`, an omitted dimension's and a GD&T item's `label`, a problem's `fix`, and a problem's
/// `refs`, put back among its subjects (a title-block cell's or the general tolerance's problem
/// names the sheet as its subject and the cell as a reference).
fn without_typed_state(mut plan: Value) -> Value {
    plan["sheet"].as_object_mut().unwrap().remove("state");
    for list in [
        "views",
        "dimensions",
        "omitted",
        "notes",
        "tables",
        "gdt",
        "requirements",
        "balloons",
    ] {
        let items = plan.get_mut(list).and_then(Value::as_array_mut);
        for item in items.into_iter().flatten() {
            let item = item.as_object_mut().unwrap();
            item.remove("state");
            if list == "omitted" || list == "gdt" {
                item.remove("label");
            }
        }
    }
    if let Some(t) = plan.get_mut("hole_table").and_then(Value::as_object_mut) {
        t.remove("state");
    }
    for p in plan["problems"].as_array_mut().unwrap() {
        let p = p.as_object_mut().unwrap();
        p.remove("fix");
        p.remove("fixes");
        // Additive finding labels do not change the frozen alpha.1 plan contract.
        p.remove("impact");
        p.remove("group");
        let refs = p.remove("refs").unwrap_or(json!([]));
        let refs = refs.as_array().unwrap();
        let subjects = p["subjects"].as_array_mut().unwrap();
        if refs.iter().any(|r| {
            r.as_str()
                .is_some_and(|r| r.starts_with("title_block/") || r == "sheet/title/tolerance")
        }) {
            subjects.retain(|s| s != "sheet");
        }
        subjects.extend(refs.iter().cloned());
    }
    plan
}

/// The 0.1.0-alpha.1 IDs in *v* as they read now: `d:…@<view>` without its view, `pmi:<source>`
/// as its record's ID (from *ids*), everywhere they appear (keys, IDs, subjects, messages).
fn renamed(v: &Value, ids: &BTreeMap<String, String>) -> Value {
    let text = serde_json::to_string(v).unwrap();
    let view = without_views(&text);
    let mut out = String::new();
    let mut rest = view.as_str();
    while let Some(i) = rest.find("pmi:") {
        out.push_str(&rest[..i]);
        let tail = &rest[i + 4..];
        let end = tail
            .find(|c: char| !(c.is_ascii_alphanumeric() || c == '_' || c == ':' || c == '#'))
            .unwrap_or(tail.len());
        let mut source = &tail[..end];
        // A source ends at its instance's digits.
        if let Some(h) = source.find('#') {
            let digits = source[h + 1..]
                .find(|c: char| !c.is_ascii_digit())
                .map_or(source.len(), |j| h + 1 + j);
            source = &source[..digits];
        }
        match ids.get(source) {
            Some(id) if source.contains('#') => out.push_str(id),
            _ => {
                out.push_str("pmi:");
                out.push_str(source);
            }
        }
        rest = &tail[source.len()..];
    }
    out.push_str(rest);
    serde_json::from_str(&out).unwrap()
}

/// *text* with every `@front`, `@plan` and `@side` after a dimension ID removed.
fn without_views(text: &str) -> String {
    let mut out = String::new();
    let mut rest = text;
    while let Some(i) = rest.find('@') {
        let before = &rest[..i];
        let after = &rest[i + 1..];
        let view = ["front", "plan", "side"].into_iter().find(|v| {
            after.starts_with(v) && !after[v.len()..].starts_with(|c: char| c.is_alphanumeric())
        });
        // Within an ID: from its `d:` with no space or quote in between.
        let in_id = before
            .rfind(|c: char| c == '"' || c.is_whitespace())
            .map_or(before, |j| &before[j + 1..])
            .contains("d:");
        out.push_str(before);
        match view {
            Some(v) if in_id => rest = &after[v.len()..],
            _ => {
                out.push('@');
                rest = after;
            }
        }
    }
    out.push_str(rest);
    out
}

/// Where *a* and *b* first differ, as a path and both values.
fn first_difference(a: &Value, b: &Value, at: &str) -> String {
    match (a, b) {
        (Value::Object(x), Value::Object(y)) => {
            for k in x.keys().chain(y.keys()) {
                match (x.get(k), y.get(k)) {
                    (Some(p), Some(q)) if p != q => {
                        return first_difference(p, q, &format!("{at}/{k}"));
                    }
                    (Some(_), None) | (None, Some(_)) => return format!("{at}/{k}: in one only"),
                    _ => {}
                }
            }
            format!("{at}: equal")
        }
        (Value::Array(x), Value::Array(y)) if x.len() == y.len() => {
            for (i, (p, q)) in x.iter().zip(y).enumerate() {
                if p != q {
                    return first_difference(p, q, &format!("{at}[{i}]"));
                }
            }
            format!("{at}: equal")
        }
        _ => format!("{at}: {a} != {b}"),
    }
}

/// Two version 1 keys that read as one version 2 key are ambiguous, and refused with both as the
/// candidates; a version 2 spec keyed the version 1 way is refused, reading as the new key.
#[test]
fn an_ambiguous_version_1_key_is_refused_with_its_candidates() {
    let a = plate();
    let spec: Spec = serde_json::from_value(json!({
        "schema": "draftwright.spec/1",
        "dimensions": {
            "d:envelope/width.length@front": { "omit": true },
            "d:envelope/width.length@plan": { "tolerance": "±0.1" }
        }
    }))
    .unwrap();
    let e = model::plan(&a, &spec).unwrap_err();
    assert_eq!(
        e.choices,
        [
            "d:envelope/width.length@front",
            "d:envelope/width.length@plan"
        ],
        "{e}"
    );
    assert!(
        e.message
            .contains("both read as \"d:envelope/width.length\""),
        "{e}"
    );
    let e = model::migrate(&spec, &a).unwrap_err();
    assert_eq!(e.choices.len(), 2);

    // Migrated alone, a key reads as its new form, and the spec is version 2.
    let spec: Spec = serde_json::from_value(json!({
        "schema": "draftwright.spec/1",
        "dimensions": { "d:envelope/width.length@plan": { "tolerance": "±0.1" } }
    }))
    .unwrap();
    let m = model::migrate(&spec, &a).unwrap();
    assert_eq!(m.schema, model::SPEC_SCHEMA);
    assert_eq!(
        m.dimensions.keys().collect::<Vec<_>>(),
        ["d:envelope/width.length"]
    );
    assert_eq!(model::migrate(&m, &a).unwrap(), m);
}
