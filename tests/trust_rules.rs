//! The trust rules of the catalogue (`docs/rules/adr.yaml`, class `trust`) that the port upholds
//! and no other test checks: determinism of reading, honest failure, provenance and the spec's
//! standing as the source of truth. Each test names the rules it checks; a part (and its
//! recognition) and a spec go in, a property of the plan, its problems or the output comes out.

#[path = "common/corpus.rs"]
mod corpus;
#[path = "common/facts.rs"]
mod facts;

use std::collections::BTreeSet;
use std::io::Read as _;
use std::path::{Path, PathBuf};

use draftwright::api;
use draftwright::dims::PlannedDimension;
use draftwright::model::{self, Analysis, DimensionSource, DimensionSpec, Plan, Spec};
use draftwright::output::{Curve, Item};
use draftwright::text::Font;
use serde_json::{Value, json};

fn fixture(path: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(path)
}

fn font() -> Font {
    Font::open(&Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
        .unwrap()
}

fn recognition(name: &str) -> Value {
    serde_json::from_str(
        &std::fs::read_to_string(fixture(&format!("recognition/{name}.json"))).unwrap(),
    )
    .unwrap()
}

/// The fixture part *name* with *json* as its recognition.
fn analysed_with(name: &str, json: &Value) -> (draftwright::haecceity::Part, Analysis) {
    let (part, mut a) = model::analyse(&fixture(&format!("parts/{name}.step"))).unwrap();
    model::recognise(&part, &mut a, &json.to_string()).unwrap();
    (part, a)
}

/// The fixture parts with their recognition.
fn fixtures() -> Vec<(String, Analysis)> {
    ["plate", "turned", "prismatic", "two_grids"]
        .iter()
        .map(|n| (n.to_string(), analysed_with(n, &recognition(n)).1))
        .collect()
}

/// The sampled sheets' corpus parts, with the features draftwright's own recogniser found for
/// them (`tests/fixtures/recognition/sheets/`); none when the corpus is missing. ctc04 is left
/// out: these tests have not been checked against it since the bridge began reading it.
fn sampled() -> Vec<(String, Analysis)> {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED (corpus part): the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return Vec::new();
    };
    let names = std::fs::read_dir(fixture("recognition/sheets")).unwrap();
    let mut names: Vec<String> = names
        .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
        .filter_map(|f| f.strip_suffix(".json.gz").map(String::from))
        .filter(|n| n != "ctc04")
        .collect();
    names.sort();
    names
        .into_iter()
        .map(|name| {
            let f = facts::load(&name);
            let mut json = String::new();
            flate2::read::GzDecoder::new(
                std::fs::File::open(fixture(&format!("recognition/sheets/{name}.json.gz")))
                    .unwrap(),
            )
            .read_to_string(&mut json)
            .unwrap();
            let (part, mut a) = model::analyse(&dir.join(&f.binding.file)).unwrap();
            model::recognise(&part, &mut a, &json).unwrap_or_else(|e| panic!("{name}: {e}"));
            (name, a)
        })
        .collect()
}

fn scratch(name: &str) -> PathBuf {
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR")).join("trust_rules");
    std::fs::create_dir_all(&dir).unwrap();
    dir.join(name)
}

/// An empty directory of its own under the scratch directory.
fn fresh(name: &str) -> PathBuf {
    let dir = scratch(name);
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

fn listing(dir: &Path) -> Vec<String> {
    let mut names: Vec<String> = std::fs::read_dir(dir)
        .unwrap()
        .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
        .collect();
    names.sort();
    names
}

fn codes(plan: &Plan) -> Vec<&str> {
    plan.problems.iter().map(|p| p.code.as_str()).collect()
}

fn ids(plan: &Plan) -> Vec<&str> {
    plan.dimensions.iter().map(|d| d.id.as_str()).collect()
}

/// A spec that omits the dimensions *omit* (in `auto`).
fn omitting(omit: &[&str]) -> Spec {
    let mut spec = Spec::default();
    for id in omit {
        spec.dimensions.insert(
            id.to_string(),
            DimensionSpec {
                omit: true,
                ..Default::default()
            },
        );
    }
    spec
}

/// ADR3-4: a document of a version this build does not know is refused, never read as the
/// nearest it does: an Analysis or a Spec of another schema, and recognition JSON of another
/// format. (Record types without a home are refused too: `tests/bridge.rs`.)
#[test]
fn a_document_of_an_unknown_version_is_refused() {
    let (_, analysis) = analysed_with("plate", &recognition("plate"));
    assert!(model::plan(&analysis, &Spec::default()).is_ok());

    let mut future = analysis.clone();
    future.schema = "draftwright.analysis/9".into();
    let e = model::plan(&future, &Spec::default()).unwrap_err();
    assert!(e.message.contains("draftwright.analysis/9"), "{e}");
    assert!(
        e.choices.contains(&model::ANALYSIS_SCHEMA.to_string()),
        "{e}"
    );
    // Version 2 is read (with no levels).
    let mut older = analysis.clone();
    older.schema = "draftwright.analysis/2".into();
    assert!(model::plan(&older, &Spec::default()).is_ok());

    let spec: Spec = serde_json::from_value(json!({ "schema": "draftwright.spec/3" })).unwrap();
    let e = model::plan(&analysis, &spec).unwrap_err();
    assert!(e.message.contains("draftwright.spec/3"), "{e}");
    assert_eq!(e.choices, model::SPEC_SCHEMAS);
    // Version 1 is read, through the migration.
    let spec: Spec = serde_json::from_value(json!({ "schema": "draftwright.spec/1" })).unwrap();
    assert_eq!(
        model::plan(&analysis, &spec).unwrap().spec.schema,
        model::SPEC_SCHEMA
    );
    // Through the adapters' one entry point, it is the caller's to fix.
    let e = api::call(
        "plan",
        &json!({ "step": fixture("parts/plate.step"), "spec": { "schema": "draftwright.spec/3" } }),
    )
    .unwrap_err();
    assert_eq!(e.code, "invalid", "{e}");

    let mut json = recognition("plate");
    json["format"] = "draftwright.recognition/2".into();
    let path = scratch("plate-recognition-2.json");
    std::fs::write(&path, json.to_string()).unwrap();
    let e = model::analyse_with(&fixture("parts/plate.step"), Some(&path)).unwrap_err();
    assert!(e.contains("draftwright.recognition/1"), "{e}");
}

/// ADR5-10: every file the port writes is staged beside its place and moved in, so a failure
/// leaves the destination as it was (and no staged copy behind), and rendering writes the
/// drawings asked for and nothing else: no report beside them, no parent directory made.
#[test]
fn a_failed_write_leaves_the_destination_as_it_was() {
    let dir = fresh("atomic");
    let out = dir.join("part");
    let files = |format: &str| vec![(format.to_string(), b"new".to_vec())];

    // A foreign directory at the old staging path is bypassed and kept.
    std::fs::write(dir.join("part.svg"), "old").unwrap();
    std::fs::create_dir(dir.join("part.svg.part")).unwrap();
    assert_eq!(
        api::write(&out, &files("svg")).unwrap(),
        [dir.join("part.svg").display().to_string()]
    );
    assert_eq!(
        std::fs::read_to_string(dir.join("part.svg")).unwrap(),
        "new"
    );
    assert!(dir.join("part.svg.part").is_dir());

    // It cannot be moved in (a directory is in its place): what was there is kept, and the
    // staged copy is removed.
    std::fs::create_dir(dir.join("part.dxf")).unwrap();
    std::fs::write(dir.join("part.dxf/keep"), "kept").unwrap();
    let e = api::write(&out, &files("dxf")).unwrap_err();
    assert_eq!(e.code, "failed", "{e}");
    assert_eq!(
        std::fs::read_to_string(dir.join("part.dxf/keep")).unwrap(),
        "kept"
    );
    assert_eq!(listing(&dir), ["part.dxf", "part.svg", "part.svg.part"]);

    // A missing directory is not made.
    let e = api::write(&dir.join("missing/part"), &files("pdf")).unwrap_err();
    assert_eq!(e.code, "failed", "{e}");
    assert!(!dir.join("missing").exists());

    // A render writes exactly the formats asked for.
    let dir = fresh("render-only");
    let reply = api::call(
        "render",
        &json!({ "step": fixture("parts/plate.step"), "features": fixture("recognition/plate.json"),
            "output": dir.join("plate").display().to_string(), "formats": ["pdf", "svg"] }),
    )
    .unwrap();
    assert_eq!(reply["written"].as_array().unwrap().len(), 2);
    assert_eq!(listing(&dir), ["plate.pdf", "plate.svg"]);
}

/// ADR5-12: the Analysis's hash and its geometry come from one read of the file. The file here
/// is a pipe that gives turned.step's bytes to its first reader and, should the file be opened
/// again while the analysis is under way, prismatic.step's: an analysis that read it twice would
/// bind one file's hash to the other's faces. Only the file's name and hash are recorded, not
/// where it was.
#[cfg(unix)]
#[test]
fn the_hash_and_the_geometry_come_from_one_read() {
    use sha2::{Digest, Sha256};
    use std::io::Write as _;
    use std::sync::mpsc;
    use std::time::Duration;

    // Publish a different FIFO before closing the first writer. EOF on the original
    // descriptor must precede any second open: a delayed reader can never concatenate
    // the two documents, even if the writer's fallback timeout expires under load.
    let first = std::fs::read(fixture("parts/turned.step")).unwrap();
    let second = std::fs::read(fixture("parts/prismatic.step")).unwrap();
    let changing_file = |name: &str| {
        let dir = fresh(&format!("snapshot-{}-{name}", std::process::id()));
        let pipe = dir.join("snapshot.step");
        let next = dir.join("next.step");
        for path in [&pipe, &next] {
            assert!(
                std::process::Command::new("mkfifo")
                    .arg(path)
                    .status()
                    .unwrap()
                    .success()
            );
        }
        let (done, analysed) = mpsc::channel::<()>();
        let (offered, offer) = mpsc::channel();
        let to = pipe.clone();
        let bytes = first.clone();
        let replacement = second.clone();
        let writer = std::thread::spawn(move || {
            let mut f = std::fs::OpenOptions::new().write(true).open(&to).unwrap();
            f.write_all(&bytes).unwrap();
            std::fs::rename(next, &to).unwrap();
            drop(f);
            // A second open while the analysis is under way gets the other file.
            let again = analysed.recv_timeout(Duration::from_secs(5)).is_err();
            offered.send(again).unwrap();
            if again {
                let mut f = std::fs::OpenOptions::new().write(true).open(&to).unwrap();
                f.write_all(&replacement).unwrap();
            }
        });
        (pipe, done, offer, writer)
    };
    let (pipe, done, offer, writer) = changing_file("analysis");
    let (_, analysis) = model::analyse(&pipe).unwrap();
    let _ = done.send(());
    if offer.recv().unwrap() {
        assert_eq!(std::fs::read(&pipe).unwrap(), second);
    }
    writer.join().unwrap();

    let (_, direct) = model::analyse(&fixture("parts/turned.step")).unwrap();
    let hash: String = Sha256::digest(&first)
        .iter()
        .map(|x| format!("{x:02x}"))
        .collect();
    assert_eq!(analysis.binding.source_sha256, hash);
    assert_eq!(analysis.binding.part, "snapshot");
    assert_eq!(analysis.binding.face_count, direct.binding.face_count);
    assert_eq!(analysis.envelope, direct.envelope);
    assert_eq!(analysis.iso_bounds, direct.iso_bounds);
    let text = serde_json::to_string(&analysis).unwrap();
    let dir = pipe.parent().unwrap().display().to_string();
    assert!(
        !text.contains(&dir),
        "the analysis records where the file was"
    );

    // Negative control: code that opens twice really receives two different documents.
    // The complete first stream also proves the replacement cannot reach its reader.
    let (pipe, done, offer, writer) = changing_file("two-reads");
    let mut original = std::fs::File::open(&pipe).unwrap();
    let mut bytes = vec![0; first.len()];
    original.read_exact(&mut bytes).unwrap();
    assert_eq!(bytes, first);
    // Deliberately leave EOF unread until the second writer is offered. Reusing the
    // original FIFO would append the replacement here and fail this assertion.
    assert!(offer.recv().unwrap());
    let mut trailing = Vec::new();
    original.read_to_end(&mut trailing).unwrap();
    assert!(trailing.is_empty());
    drop(original);
    assert_eq!(std::fs::read(&pipe).unwrap(), second);
    let _ = done.send(());
    writer.join().unwrap();
}

/// ADR5-D2: no document infers manufacturing intent. With the default spec, every plan's title
/// says only the part's name and a drawing number (no material), and every printed term is a
/// count, a size, a symbol or one of the callout grammar's words: no fit, tolerance, thread or
/// finish the spec did not give.
#[test]
fn no_document_infers_manufacturing_intent() {
    // The callout grammar's words (which face of a turned part a bolt circle is on included),
    // a radius's, a diameter's and an equal-leg chamfer's prefix, a polygonal boss's (`HEX`,
    // `N-SIDED`, `A/F`), a blind slot's words, a groove's (`WIDE`), and a pocket's and a circular
    // seat's (`MAX DEEP`, `LONG`, `ARC`), and a turned step's outside diameter where a coaxial
    // bore prints the same size (`OD`), and the letters inside the spotface symbol (`⌴SF`,
    // maintainer decision 2026-10-08).
    const WORDS: [&str; 29] = [
        "THRU",
        "EQ",
        "SP",
        "ON",
        "BC",
        "UPPER",
        "LOWER",
        "FACE",
        "OF",
        "FROM",
        "END",
        "R",
        "ø",
        "C",
        "HEX",
        "SIDED",
        "AF",
        "OPEN",
        "SLOT",
        "DEEP",
        "ROUNDBOTTOM",
        "BOTTOM",
        "FLAT",
        "LONG",
        "WIDE",
        "MAX",
        "ARC",
        "OD",
        "SF",
    ];
    let mut parts = fixtures();
    parts.extend(sampled());
    for (name, analysis) in &parts {
        let plan = model::plan(analysis, &Spec::default()).unwrap();
        assert_eq!(
            plan.sheet.title.keys().collect::<Vec<_>>(),
            ["number", "title"],
            "{name}"
        );
        for d in &plan.dimensions {
            for term in &d.terms {
                for word in term.split_whitespace() {
                    let letters: String = word.chars().filter(|c| c.is_alphabetic()).collect();
                    assert!(
                        letters.is_empty() || WORDS.contains(&letters.as_str()),
                        "{name}: {} prints {term:?}",
                        d.id
                    );
                }
                assert!(
                    !term.contains(['±', '+']),
                    "{name}: {} prints {term:?}",
                    d.id
                );
            }
        }
    }
}

/// ADR4-D2: the spec records intent, never outcomes. A plan whose items do not all fit (a view
/// omitted, a sheet too small at the scale asked) carries its spec back unchanged but for the
/// binding, and planning again from that spec gives the same plan.
#[test]
fn planning_never_writes_its_outcomes_into_the_spec() {
    let (_, analysis) = analysed_with("plate", &recognition("plate"));
    let given: Spec = serde_json::from_value(json!({
        "sheet": { "size": "A4", "scale": "10:1", "title": { "material": "6082-T6" } },
        "views": { "plan": { "omit": true } },
        "dimensions": { "d:envelope/height.length": { "omit": true } },
    }))
    .unwrap();
    let plan = model::plan(&analysis, &given).unwrap();
    assert!(
        codes(&plan).contains(&"view_plan_incomplete"),
        "{:?}",
        codes(&plan)
    );
    assert!(plan.dimensions.iter().any(|d| d.placed.is_none()));
    let mut expected = given.clone();
    expected.binding = Some(analysis.binding.clone());
    assert_eq!(plan.spec, expected);
    let again = model::plan(&analysis, &plan.spec).unwrap();
    assert_eq!(
        serde_json::to_string(&again).unwrap(),
        serde_json::to_string(&plan).unwrap()
    );
}

/// ADR2-D3: an authored constraint is never relaxed to make the sheet work. A sheet and scale
/// that cannot hold the views are kept, and the plan says so; a scale alone is kept on the
/// largest sheet, which is said not to fit; an omitted view stays omitted and what it carried is
/// reported.
#[test]
fn an_authored_constraint_is_kept_and_its_infeasibility_reported() {
    let (_, analysis) = analysed_with("plate", &recognition("plate"));
    let sheet = |v: Value| -> Plan {
        let spec: Spec = serde_json::from_value(json!({ "sheet": v })).unwrap();
        model::plan(&analysis, &spec).unwrap()
    };
    let both = sheet(json!({ "size": "A4", "scale": "10:1" }));
    assert_eq!(
        (both.sheet.size.as_str(), both.sheet.scale.as_str()),
        ("A4", "10:1")
    );
    assert!(
        codes(&both)
            .iter()
            .any(|c| ["page_fit_uncertain", "views_do_not_fit"].contains(c)),
        "{:?}",
        codes(&both)
    );
    // two_grids is 140 mm long: at 10:1 no sheet holds it.
    let (_, long) = analysed_with("two_grids", &recognition("two_grids"));
    let spec: Spec = serde_json::from_value(json!({ "sheet": { "scale": "10:1" } })).unwrap();
    let scale = model::plan(&long, &spec).unwrap();
    assert_eq!(
        (scale.sheet.size.as_str(), scale.sheet.scale.as_str()),
        ("A0", "10:1")
    );
    assert!(
        codes(&scale).contains(&"views_do_not_fit"),
        "{:?}",
        codes(&scale)
    );
    let size = sheet(json!({ "size": "A4" }));
    assert_eq!(size.sheet.size, "A4");

    let spec: Spec =
        serde_json::from_value(json!({ "views": { "plan": { "omit": true } } })).unwrap();
    let plan = model::plan(&analysis, &spec).unwrap();
    assert!(plan.views.iter().all(|v| v.id != "v:plan"));
    assert!(codes(&plan).contains(&"view_plan_incomplete"));
}

/// The parts of a dimension's ID: its feature and its parameter. It never names the view that
/// carries it (`draftwright.spec/2`).
fn id_parts(id: &str) -> (&str, &str) {
    let rest = id.strip_prefix("d:").unwrap_or_else(|| panic!("{id}"));
    assert!(!rest.contains('@'), "an ID never names its view: {id}");
    rest.split_once('/').unwrap_or_else(|| panic!("{id}"))
}

/// ADR4-6 (R1 invariant 5): a dimension's ID is derived, never positional: its feature (the
/// envelope, the height ladder `step_level`, a recognised feature or pattern), its parameter in
/// the role vocabulary (`role.kind[.discriminator]`, the kind always present), never its view
/// (a field of the item, a principal view's); IDs are unique and do not change with the order recognition reports features in. A bare role is refused, with the IDs
/// it could mean.
#[test]
fn dimension_ids_are_derived_and_a_bare_role_warns_without_applying() {
    let mut parts = fixtures();
    parts.extend(sampled());
    for (name, analysis) in &parts {
        let plan = model::plan(analysis, &Spec::default()).unwrap();
        let mut features: BTreeSet<&str> =
            analysis.features.iter().map(|f| f.id.as_str()).collect();
        if let Some(r) = &analysis.recognition {
            features.extend(r.patterns.iter().map(|p| p.id.as_str()));
        }
        // The overall extents and the height ladder are the part's, not a recognised feature's.
        features.insert("envelope");
        features.insert("step_level");
        let mut seen = BTreeSet::new();
        for d in &plan.dimensions {
            let (feature, parameter) = id_parts(&d.id);
            assert!(features.contains(feature), "{name}: {}", d.id);
            let words: Vec<&str> = parameter.split('.').collect();
            assert!(
                (2..=3).contains(&words.len())
                    && words.iter().all(|w| {
                        !w.is_empty()
                            && w.chars()
                                .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_')
                    }),
                "{name}: {}",
                d.id
            );
            assert!(
                ["v:front", "v:plan", "v:side"].contains(&d.view.as_str())
                    || plan
                        .views
                        .iter()
                        .any(|v| v.id == d.view && v.detail.is_some()),
                "{name}: {} in {}",
                d.id,
                d.view
            );
            assert!(seen.insert(d.id.clone()), "{name}: {} twice", d.id);
        }
    }

    // The same records in the opposite order give the same items.
    let json = recognition("plate");
    let mut reversed = json.clone();
    reversed["quiddity"]["features"]
        .as_array_mut()
        .unwrap()
        .reverse();
    let plan = model::plan(&analysed_with("plate", &json).1, &Spec::default()).unwrap();
    let again = model::plan(&analysed_with("plate", &reversed).1, &Spec::default()).unwrap();
    assert_eq!(
        serde_json::to_string(&plan.dimensions).unwrap(),
        serde_json::to_string(&again.dimensions).unwrap()
    );

    let (_, analysis) = analysed_with("plate", &json);
    let spec = omitting(&["d:f:hole_pattern:1/bore"]);
    let plan = model::plan(&analysis, &spec).unwrap();
    assert!(plan.problems.iter().any(|p| {
        p.code == "dimension_edit_unapplied"
            && p.subjects
                .contains(&"dimensions/d:f:hole_pattern:1~1bore".into())
    }));
    assert_eq!(plan.spec.dimensions, spec.dimensions);
    assert!(
        plan.dimensions
            .iter()
            .any(|d| { d.id == "d:f:hole_pattern:1/bore.diameter" && d.placed.is_some() })
    );
}

/// ARCH0016-1: an omission is not a drop. What the spec leaves out is listed as omitted, with
/// its basis, and is no problem's subject; what the plan approves and cannot draw stays among its
/// dimensions, unplaced, and is reported; nothing is both.
#[test]
fn an_omission_is_never_reported_as_a_drop() {
    let (_, analysis) = analysed_with("plate", &recognition("plate"));
    let width = "d:envelope/width.length";
    let mut spec = omitting(&[width]);
    spec.views.entry("plan".into()).or_default().omit = true;
    let plan = model::plan(&analysis, &spec).unwrap();

    assert_eq!(plan.omitted.len(), 1);
    assert_eq!(plan.omitted[0].id, width);
    assert!(plan.omitted[0].basis.starts_with("authored"));
    assert!(!ids(&plan).contains(&width));
    assert!(
        plan.problems
            .iter()
            .all(|p| !p.subjects.iter().any(|s| s == width))
    );

    let dropped: Vec<&PlannedDimension> = plan
        .dimensions
        .iter()
        .filter(|d| d.placed.is_none())
        .collect();
    assert!(!dropped.is_empty());
    for d in dropped {
        assert!(
            plan.problems
                .iter()
                .any(|p| p.code == "view_plan_incomplete" && p.subjects.contains(&d.id)),
            "{} is not reported",
            d.id
        );
        assert!(plan.omitted.iter().all(|o| o.id != d.id));
    }
}

/// ARCH0016-2: every dimension the plan states can be addressed by its ID. Omitting it (in
/// `auto`) takes it, and only it, off, except that an item it conveyed (ARCH0009-6: a dropped
/// winner promotes its loser), or a dependent radius default conveyed, comes back; listing it
/// alone (in `authored`) gives a plan of it
/// alone, so any dimension dropped can be added back.
#[test]
fn every_planned_dimension_can_be_addressed() {
    let mut parts = fixtures();
    parts.extend(
        sampled()
            .into_iter()
            .filter(|(n, _)| ["cgb245", "ftc06", "m10127"].contains(&n.as_str())),
    );
    for (name, analysis) in &parts {
        let plan = model::plan(analysis, &Spec::default()).unwrap();
        let all = ids(&plan);
        assert!(!all.is_empty(), "{name}");
        for id in &all {
            let without = model::plan(analysis, &omitting(&[id])).unwrap();
            // ARCH0015-2 (stream K6): omitting a ladder rung omits its whole set (the height
            // ladder, or the shoulders along one axis), each recorded as omitted with it.
            let set = |i: &str| {
                let heights = "d:step_level/step_height.length";
                if i.starts_with(heights) {
                    return Some(heights.to_string());
                }
                i.strip_prefix("d:step_level/step_position.length.")
                    .and_then(|r| r.get(..1))
                    .map(|axis| format!("d:step_level/step_position.length.{axis}"))
            };
            let with: Vec<&str> = all
                .iter()
                .copied()
                .filter(|i| i != id && set(i).is_some() && set(i) == set(id))
                .collect();
            for i in &with {
                assert!(
                    without
                        .omitted
                        .iter()
                        .any(|o| o.id == *i && o.state == draftwright::items::State::OmittedByUser),
                    "{name}: omitting {id} omits {i} with its set"
                );
            }
            let expected: Vec<&str> = all
                .iter()
                .copied()
                .filter(|i| i != id && !with.contains(i))
                .collect();
            let mut promoted: Vec<&str> = plan
                .omitted
                .iter()
                .filter(|o| {
                    o.basis.starts_with(&format!("conveyed by {id}: "))
                        || o.basis
                            .split_once("; dependencies: ")
                            .is_some_and(|(_, owners)| owners.split(", ").any(|owner| owner == *id))
                })
                .map(|o| o.id.as_str())
                .collect();
            for note in plan.notes.iter().filter(|n| {
                n.placed.is_some()
                    && !n.measurements.is_empty()
                    && without.notes.iter().any(|changed| {
                        changed.id == n.id
                            && matches!(
                                changed.state,
                                draftwright::items::State::NotDrawable { .. }
                            )
                    })
            }) {
                assert!(note.text.starts_with("ALL UNSPECIFIED RADII R"));
                let exception = plan.dimensions.iter().find(|d| d.id == *id).unwrap();
                assert!(exception.measurements.iter().any(|m| {
                    matches!(m.parameter.as_str(), "blend.radius" | "fillet.radius")
                        && draftwright::dims::fmt(m.value)
                            != draftwright::dims::fmt(note.measurements[0].value)
                }));
                assert!(
                    without
                        .problems
                        .iter()
                        .any(|p| { p.code == "notes_not_placed" && p.subjects.contains(&note.id) })
                );
                let hidden = model::edit(&Spec::default(), &json!([{
                    "op":"set", "path":format!("annotations/{}/omit", draftwright::items::key(&note.id)), "value":true
                }])).unwrap();
                let hidden = model::plan(analysis, &hidden).unwrap();
                for item in plan.omitted.iter().filter(|o| {
                    matches!(&o.state, draftwright::items::State::Covered { by } if by == &note.id)
                }) {
                    let restored = without.dimensions.iter().find(|d| d.id == item.id).unwrap();
                    assert!(restored.placed.is_some());
                    assert_eq!(restored.label, item.label);
                    assert_eq!(
                        restored.placed,
                        hidden.dimensions.iter().find(|d| d.id == item.id).unwrap().placed,
                        "a dependent radius uses its established geometry"
                    );
                    promoted.push(item.id.as_str());
                }
            }
            let kept: Vec<&str> = ids(&without)
                .into_iter()
                .filter(|i| !promoted.contains(i))
                .collect();
            assert_eq!(kept, expected, "{name}: omitting {id}");
            // What it conveyed comes back: each item drawn again, or conveyed by one that is
            // (a size it shared among three or more is shared again by the rest, src/dims/mod.rs
            // ::share).
            let back: Vec<&str> = ids(&without)
                .into_iter()
                .filter(|i| promoted.contains(i))
                .collect();
            for p in promoted.iter().filter(|p| !back.contains(p)) {
                assert!(
                    without.omitted.iter().any(|o| o.id == *p
                        && back
                            .iter()
                            .any(|b| o.basis.starts_with(&format!("conveyed by {b}: ")))),
                    "{name}: omitting {id} brings back {p}, or one that conveys it"
                );
            }
            assert!(
                promoted.is_empty() || !back.is_empty(),
                "{name}: omitting {id} brings back what it conveyed"
            );
            assert_eq!(
                without.omitted.len(),
                1 + with.len() + plan.omitted.len() - back.len(),
                "{name}: {id}"
            );

            let mut alone = Spec {
                dimension_source: DimensionSource::Authored,
                ..Spec::default()
            };
            alone
                .dimensions
                .insert(id.to_string(), DimensionSpec::default());
            let alone = model::plan(analysis, &alone).unwrap();
            // An authored set listing one rung approves its whole set (ARCH0015-2): every
            // member is drawn, those the default plan covers by another item included, since
            // that owner is not in this set.
            let drawn = ids(&alone);
            assert!(drawn.contains(id), "{name}: {id}");
            for i in &with {
                assert!(drawn.contains(i), "{name}: listing {id} approves {i}");
            }
            for d in &drawn {
                assert!(
                    d == id || (set(d).is_some() && set(d) == set(id)),
                    "{name}: listing {id} draws {d}"
                );
            }
        }
    }
}

/// ADR5-4: provenance is recorded once, where the plan is drawn: each annotation on the drawing
/// is one placed plan item, named by its ID and claiming exactly the measurements the item
/// states, and the annotations of a feature are exactly what omitting its dimensions takes off.
#[test]
fn annotations_carry_their_plan_items_provenance() {
    let mut parts: Vec<(String, draftwright::haecceity::Part, Analysis)> = ["plate", "two_grids"]
        .iter()
        .map(|n| {
            let (p, a) = analysed_with(n, &recognition(n));
            (n.to_string(), p, a)
        })
        .collect();
    if let Some(dir) = corpus::dir() {
        let (p, a) = corpus::analyse(&dir, "nist/nist_ftc_06_asme1_rd.stp", "ftc06");
        parts.push(("ftc06".into(), p, a));
    }
    let font = font();
    for (name, part, analysis) in &parts {
        let plan = model::plan(analysis, &Spec::default()).unwrap();
        let drawing = model::render(part, analysis, &plan, &font).unwrap();
        let placed: Vec<&PlannedDimension> = plan
            .dimensions
            .iter()
            .filter(|d| d.placed.is_some())
            .collect();
        let notes: Vec<_> = plan.notes.iter().filter(|n| n.placed.is_some()).collect();
        assert_eq!(
            drawing.annotations.len(),
            placed.len() + notes.len(),
            "{name}"
        );
        for n in notes {
            let a = drawing.annotations.iter().find(|a| a.name == n.id).unwrap();
            assert!(a.claims.is_empty());
            assert_eq!(a.runs.len(), n.rows.len());
            if n.measurements.is_empty() {
                assert_eq!(n.id, "n:f:blend:relief/profiles");
                assert_eq!(n.text, "SHALLOW RELIEF PROFILES PER MODEL");
                assert!(!analysis.radius_reliefs.is_empty());
                assert!(a.view.is_none() && a.satisfies.is_empty());
                assert_eq!(
                    a.runs
                        .iter()
                        .map(|&i| drawing.text[i].text.as_str())
                        .collect::<Vec<_>>()
                        .join(" "),
                    n.text,
                    "{name}: profile requirement is actually printed"
                );
            }
            let satisfies: Vec<_> = a
                .satisfies
                .iter()
                .map(|c| (&c.feature, &c.parameter))
                .collect();
            let expected: Vec<_> = n
                .measurements
                .iter()
                .map(|m| (&m.feature, &m.parameter))
                .collect();
            assert_eq!(satisfies, expected, "{name}: {}", n.id);
        }
        for (a, d) in drawing
            .annotations
            .iter()
            .filter(|a| a.satisfies.is_empty() && a.name != "n:f:blend:relief/profiles")
            .zip(&placed)
        {
            assert_eq!(a.name, d.id, "{name}");
            assert_eq!(a.view.as_deref(), Some(d.view.as_str()), "{name}");
            let claims: Vec<(&str, &str)> = a
                .claims
                .iter()
                .map(|c| (c.feature.as_str(), c.parameter.as_str()))
                .collect();
            let states: Vec<(&str, &str)> = d
                .measurements
                .iter()
                .map(|m| (m.feature.as_str(), m.parameter.as_str()))
                .collect();
            assert_eq!(claims, states, "{name}: {}", d.id);
        }

        // Drop the feature with the most dimensions: exactly its annotations go.
        let mut by_feature = std::collections::BTreeMap::<&str, Vec<&str>>::new();
        for d in &plan.dimensions {
            for m in &d.measurements {
                by_feature
                    .entry(m.feature.as_str())
                    .or_default()
                    .push(&d.id);
            }
        }
        let (feature, its) = by_feature.iter().max_by_key(|(_, v)| v.len()).unwrap();
        // Its own items another item conveys (one span, one dimension: ARCH0009-6) are its too:
        // omitting the item that states them would promote them.
        let mut its = its.clone();
        let own = format!("d:{feature}/");
        its.extend(
            plan.omitted
                .iter()
                .filter(|o| o.id.starts_with(&own) && o.basis.starts_with("conveyed by "))
                .map(|o| o.id.as_str()),
        );
        let dropped = model::plan(analysis, &omitting(&its)).unwrap();
        let after = model::render(part, analysis, &dropped, &font).unwrap();
        let names = |d: &draftwright::output::Drawing| -> BTreeSet<String> {
            d.annotations.iter().map(|a| a.name.clone()).collect()
        };
        let removed: BTreeSet<String> = names(&drawing)
            .difference(&names(&after))
            .cloned()
            .collect();
        let claiming: BTreeSet<String> = drawing
            .annotations
            .iter()
            .filter(|a| a.claims.iter().any(|c| c.feature == *feature))
            .map(|a| a.name.clone())
            .collect();
        assert_eq!(removed, claiming, "{name}: dropping {feature}");
        let still: Vec<&str> = after
            .annotations
            .iter()
            .filter(|a| a.claims.iter().any(|c| c.feature == *feature))
            .map(|a| a.name.as_str())
            .collect();
        assert!(
            still.is_empty(),
            "{name}: dropping {feature} ({its:?}) leaves {still:?}"
        );
    }
}

/// The page box of *items*' ink: line ends, and Bézier curves sampled finely.
fn ink_box<'a>(items: impl Iterator<Item = &'a Item>) -> [f64; 4] {
    let mut b = [
        f64::INFINITY,
        f64::INFINITY,
        f64::NEG_INFINITY,
        f64::NEG_INFINITY,
    ];
    let mut add = |p: [f64; 2]| {
        b = [
            b[0].min(p[0]),
            b[1].min(p[1]),
            b[2].max(p[0]),
            b[3].max(p[1]),
        ];
    };
    let mut curve = |c: &Curve| match c {
        Curve::Line(a, z) => {
            add(*a);
            add(*z);
        }
        Curve::Beziers(list) => {
            for poles in list {
                for i in 0..=256 {
                    let t = i as f64 / 256.0;
                    let mut q = poles.clone();
                    while q.len() > 1 {
                        q = q
                            .windows(2)
                            .map(|w| {
                                [
                                    w[0][0] + t * (w[1][0] - w[0][0]),
                                    w[0][1] + t * (w[1][1] - w[0][1]),
                                ]
                            })
                            .collect();
                    }
                    add(q[0]);
                }
            }
        }
        _ => panic!("a glyph is lines and Béziers"),
    };
    for item in items {
        match item {
            Item::Face(wires) => wires.iter().flatten().for_each(&mut curve),
            Item::Edge(c) => curve(c),
        }
    }
    b
}

/// ARCH0006-1, in part: text is measured with the font file it is drawn with. Each label's box
/// in the plan, measured when it was placed, is the box of the glyphs the drawing draws for it,
/// to a thousandth of a millimetre. (Labels whose symbols are drawn as strokes, `⌴ ↧ ⌵`, are left
/// out: their strokes cannot be told from the dimension's lines.)
#[test]
fn labels_are_measured_with_the_font_they_are_drawn_with() {
    let (part, analysis) = analysed_with("plate", &recognition("plate"));
    let font = font();
    let plan = model::plan(&analysis, &Spec::default()).unwrap();
    let mut checked = 0;
    let mut captions_checked = 0;
    for d in &plan.dimensions {
        if d.placed.is_none() || d.terms.iter().any(|t| t.contains(['⌴', '↧', '⌵'])) {
            continue;
        }
        let mut alone = Spec {
            dimension_source: DimensionSource::Authored,
            ..Spec::default()
        };
        alone
            .dimensions
            .insert(d.id.clone(), DimensionSpec::default());
        let one = model::plan(&analysis, &alone).unwrap();
        let mine = one.dimensions[0].placed.as_ref().unwrap();
        let drawing = model::render(&part, &analysis, &one, &font).unwrap();
        // Automatic furniture remains on an otherwise isolated dimension's sheet. Verify
        // its own font measurement before separating its glyphs from the dimension's.
        let caption_boxes: Vec<_> = one
            .captions
            .iter()
            .map(|caption| {
                let drawn: Vec<_> = drawing
                    .sheet
                    .items
                    .iter()
                    .filter(|d| d.name == caption.id)
                    .collect();
                assert_eq!(drawn.len(), 1, "{} is drawn once", caption.id);
                assert_eq!(drawn[0].kind, draftwright::output::Kind::Caption);
                for k in 0..4 {
                    assert!(
                        (drawn[0].ink[k] - caption.ink[k]).abs() < 1e-3,
                        "{}: drawn {:?}, measured {:?}",
                        caption.id,
                        drawn[0].ink,
                        caption.ink
                    );
                }
                captions_checked += 1;
                drawn[0].ink
            })
            .collect();
        let [tx0, ty0, tx1, ty1] = one.sheet.title_block;
        let tips: Vec<[f64; 2]> = mine.arrows.iter().map(|a| a[0]).collect();
        let glyphs = drawing.layers[2].items.iter().filter(|i| match i {
            Item::Face(wires) => {
                let first = match &wires[0][0] {
                    Curve::Line(a, _) => *a,
                    Curve::Beziers(b) => b[0][0],
                    _ => return false,
                };
                let in_title = (tx0..=tx1).contains(&first[0]) && (ty0..=ty1).contains(&first[1]);
                let in_caption = caption_boxes.iter().any(|b| {
                    first[0] >= b[0] - 1e-8
                        && first[0] <= b[2] + 1e-8
                        && first[1] >= b[1] - 1e-8
                        && first[1] <= b[3] + 1e-8
                });
                // ISO 5457 zone labels sit in the margin, outside the frame.
                let [w, h] = one.sheet.page;
                let in_margin = first[0] < 10.0
                    || first[1] < 10.0
                    || first[0] > w - 10.0
                    || first[1] > h - 10.0;
                // An arrowhead is a triangle from a tip.
                !in_title && !in_caption && !in_margin && !tips.contains(&first)
            }
            _ => false,
        });
        let ink = ink_box(glyphs);
        for k in 0..4 {
            assert!(
                (ink[k] - mine.label_box[k]).abs() < 1e-3,
                "{}: drawn {ink:?}, measured {:?}",
                d.id,
                mine.label_box
            );
        }
        checked += 1;
    }
    assert!(checked >= 3, "{checked} labels checked");
    assert!(
        captions_checked > 0,
        "the fixture must exercise caption measurements"
    );
}

/// ARCH0014-7: per-part counts of placed dimensions are one-way regression floors. A change may
/// place more (raise the floor then); one that places fewer fails here, and lowering a floor is
/// a decision to record. The floors are the port's own counts (at 7a966a9), not draftwright's:
/// families it does not dimension yet would fail draftwright's. An item counts as placed when it
/// has ink on the page, whether or not a problem is reported against it, or when a placed item
/// conveys it (one span, one dimension: ARCH0009-6), since that item states it.
#[test]
fn placed_counts_do_not_fall_below_their_floors() {
    const FLOORS: [(&str, usize); 16] = [
        ("plate", 8),
        ("turned", 2),
        ("prismatic", 3),
        ("two_grids", 15),
        ("cgb245", 37),
        ("connector109", 11),
        ("ctc01", 22),
        ("ftc06", 34),
        ("grm03", 3),
        ("grm05", 2),
        ("hanger", 6),
        ("m1000", 3),
        ("m10046", 3),
        ("m10127", 11),
        ("spool132", 6),
        ("string_post", 5),
    ];
    let mut parts = fixtures();
    parts.extend(sampled());
    let mut below = Vec::new();
    for (name, analysis) in &parts {
        let floor = FLOORS
            .iter()
            .find(|(n, _)| n == name)
            .unwrap_or_else(|| panic!("{name} has no floor"))
            .1;
        let plan = model::plan(analysis, &Spec::default()).unwrap();
        let inked: Vec<&str> = plan
            .dimensions
            .iter()
            .filter(|d| d.placed.is_some())
            .map(|d| d.id.as_str())
            .collect();
        let conveyed = plan
            .omitted
            .iter()
            .filter(|o| {
                inked
                    .iter()
                    .any(|i| o.basis.starts_with(&format!("conveyed by {i}: ")))
            })
            .count();
        let placed = inked.len() + conveyed;
        if placed < floor {
            below.push(format!("{name}: {placed} placed, floor {floor}"));
        }
    }
    assert!(below.is_empty(), "{}", below.join("\n"));
}

/// ADR5-16: a STEP binding alone cannot identify a run with caller-supplied recognition.
#[test]
fn plan_provenance_binds_the_complete_recognition_input() {
    use sha2::{Digest, Sha256};
    let (_, mut a) = analysed_with("plate", &recognition("plate"));
    let s = Spec::default();
    let original = model::plan(&a, &s).unwrap();
    let digest = |a: &Analysis| -> String {
        Sha256::digest(serde_json::to_vec(a).unwrap())
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect()
    };
    assert_eq!(
        original.analysis_sha256.as_deref(),
        Some(digest(&a).as_str())
    );
    assert_eq!(
        serde_json::to_value(&original).unwrap(),
        serde_json::to_value(model::plan(&a, &s).unwrap()).unwrap()
    );
    // Same STEP, producer and Spec; a different valid recognition run changes the drawing.
    a.features.retain(|f| f.kind != "hole");
    let changed = model::plan(&a, &s).unwrap();
    assert_eq!(original.binding, changed.binding);
    assert_eq!(original.spec, changed.spec);
    assert_ne!(original.dimensions.len(), changed.dimensions.len());
    assert_ne!(original.analysis_sha256, changed.analysis_sha256);
    assert_eq!(
        changed.analysis_sha256.as_deref(),
        Some(digest(&a).as_str())
    );
    let saved = serde_json::to_value(&changed).unwrap();
    let replay: Plan = serde_json::from_value(saved.clone()).unwrap();
    assert_eq!(replay.analysis_sha256, changed.analysis_sha256);
    let mut legacy = saved;
    legacy.as_object_mut().unwrap().remove("analysis_sha256");
    assert!(
        serde_json::from_value::<Plan>(legacy)
            .unwrap()
            .analysis_sha256
            .is_none()
    );
}

/// Python's span channel disambiguates correlated rungs; Rust uses occurrence-specific IDs.
#[test]
fn lint_issue_serialization_preserves_every_inspection_channel() {
    use draftwright::lint::{LintIssue, Requirement, Severity, Stage};
    let issue = LintIssue {
        id: "l:example:f:hole:2/location.length.x".into(),
        sheet: Some(2),
        code: "example".into(),
        severity: Severity::Warning,
        message: "The X cell is absent".into(),
        at: Some([12.0, 34.0]),
        view: Some("v:plan".into()),
        subjects: vec!["f:hole:2".into()],
        requirements: vec![Requirement {
            feature: "f:hole:2".into(),
            parameter: "location.length.x".into(),
        }],
        measurements: vec!["f:hole:2/location.length.x".into()],
        sources: vec!["dimension:#42".into()],
        stage: Some(Stage::Placement),
        annotation: Some("hole_schedule_plan/A2/x".into()),
        related: vec!["hole_schedule_plan/A1/x".into()],
        evidence: Some("empty cell".into()),
        fix: Some("Restore the X coordinate cell.".into()),
    };
    assert_eq!(
        serde_json::to_value(issue).unwrap(),
        json!({
            "id":"l:example:f:hole:2/location.length.x", "sheet":2, "code":"example", "severity":"warning",
            "impact":"advisory", "group":"example",
            "message":"The X cell is absent", "at":[12.0,34.0], "view":"v:plan", "subjects":["f:hole:2"],
            "requirements":[{"feature":"f:hole:2","parameter":"location.length.x"}],
            "measurements":["f:hole:2/location.length.x"], "sources":["dimension:#42"], "stage":"placement",
            "annotation":"hole_schedule_plan/A2/x", "related":["hole_schedule_plan/A1/x"],
            "evidence":"empty cell", "fix":"Restore the X coordinate cell.",
            "scope":"sheet", "fix_description":"Restore the X coordinate cell."
        })
    );
}

#[test]
fn an_explicit_cramped_scale_keeps_every_requirement_or_reports_its_loss() {
    let (part, analysis) = analysed_with("plate", &recognition("plate"));
    let baseline = model::plan(&analysis, &Spec::default()).unwrap();
    let spec: Spec = serde_json::from_value(
        json!({"sheet":{"size":"A4", "scale":"10:1"}, "hole_table":"never"}),
    )
    .unwrap();
    let cramped = model::plan_escalated(&analysis, &spec).unwrap();
    assert_eq!(cramped.sheet.scale, "10:1");
    assert!(cramped.problems.iter().any(|p| p.code == "views_do_not_fit"
        || p.code == "page_fit_uncertain"
        || p.code == "layout_infeasible"));
    let expected: BTreeSet<_> = baseline
        .dimensions
        .iter()
        .map(|d| &d.id)
        .chain(baseline.omitted.iter().map(|o| &o.id))
        .collect();
    let accounted: BTreeSet<_> = cramped
        .dimensions
        .iter()
        .map(|d| &d.id)
        .chain(cramped.omitted.iter().map(|o| &o.id))
        .collect();
    assert!(
        expected.is_subset(&accounted),
        "lost: {:?}",
        expected.difference(&accounted).collect::<Vec<_>>()
    );
    let drawing = model::render(&part, &analysis, &cramped, &font()).unwrap();
    for d in &cramped.dimensions {
        assert!(
            drawing.annotations.iter().any(|a| a.name == d.id)
                || drawing
                    .problems
                    .iter()
                    .chain(&cramped.problems)
                    .any(|p| p.subjects.contains(&d.id)),
            "{} disappeared",
            d.id
        );
    }
}
