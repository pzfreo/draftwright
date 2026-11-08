//! Problems in drawing language with subjects that say what they are about, and reference
//! dimensions flagged (draftwright-rust #24, #25, #26):
//! - a message names a dimension by the text it prints and a feature by what a draftsman sees
//!   of it, never by an internal ID; the IDs stay in `subjects`;
//! - unclaimed geometry names its faces, not the sheet;
//! - a dimension drawn in brackets carries `reference: true` and the IDs that determine it.
//!
//! The editor's samples: `parts/mounting_plate.step` is byte for byte the editor's `plate.step`,
//! and `samples/specify-sample-spindle.step.gz` its turned spindle (draftwright-editor
//! `public/samples/specify-sample-spindle.step`).

#[path = "common/support.rs"]
mod support;

#[path = "common/cache.rs"]
mod cache;

use std::io::Read as _;
use std::path::{Path, PathBuf};

use draftwright::model::{self, Analysis, Plan, Spec};
use draftwright::wording::carries_id;
use serde_json::{Value, json};

fn fixture(path: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(path)
}

/// The gzipped fixture *path*, analysed as the command line analyses it, from a scratch file of
/// its own named *name* (a binding names its part by the file's stem).
fn gz(path: &str, name: &str) -> Analysis {
    cache::analysed(name, &std::fs::read(fixture(path)).unwrap(), true, || {
        static N: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
        let n = N.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        let dir = Path::new(env!("CARGO_TARGET_TMPDIR"))
            .join("problem_wording")
            .join(format!("{}-{n}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let mut bytes = Vec::new();
        flate2::read::GzDecoder::new(std::fs::File::open(fixture(path)).unwrap())
            .read_to_end(&mut bytes)
            .unwrap();
        let scratch = dir.join(name);
        std::fs::write(&scratch, bytes).unwrap();
        let result = model::analyse_native(&scratch).unwrap();
        std::fs::remove_dir_all(&dir).unwrap();
        result
    })
    .1
}

fn spindle() -> Analysis {
    gz(
        "samples/specify-sample-spindle.step.gz",
        "specify-sample-spindle.step",
    )
}

fn spec(v: Value) -> Spec {
    serde_json::from_value(v).unwrap()
}

/// #24, as reported on the editor's plate: the grid's column pitch placed above, where its
/// second extension line crosses the ⌀8 hole.
#[test]
fn the_plates_extension_crossing_reads_in_drawing_language() {
    let (_, a) = cache::native(&fixture("parts/mounting_plate.step"));
    let pitch = "d:f:hole_pattern:f6/grid_pitch.length.col";
    let s = spec(json!({ "dimensions": { pitch: { "place": { "side": "above" } } } }));
    let plan = model::plan_recovered(&a, &s).unwrap();
    let p = plan
        .problems
        .iter()
        .find(|p| {
            p.code == "extension_through_feature" && p.subjects.first().is_some_and(|s| s == pitch)
        })
        .unwrap_or_else(|| panic!("no extension crossing for {pitch}: {:#?}", plan.problems));
    assert_eq!(p.subjects, [pitch, "f:hole:f9"]);
    assert_eq!(
        p.message,
        "The second extension line of 2× 30 crosses the ⌀8 hole."
    );
    assert_eq!(carries_id(&p.message), None);
}

/// #25: the spindle's unclaimed faces are the problem's subjects, not the sheet.
#[test]
fn the_spindles_unclaimed_faces_are_its_subjects() {
    let a = spindle();
    let plan = cache::default_plan(&a, true);
    let p = plan
        .problems
        .iter()
        .find(|p| p.code == "unclaimed_geometry")
        .expect("the spindle has unclaimed faces");
    assert_eq!(p.severity, "info");
    assert_eq!(serde_json::to_value(p).unwrap()["impact"], "advisory");
    let unclaimed = plan.completeness.faces - plan.completeness.claimed_faces;
    // #207: claims follow quiddity's face accounting, which accounts no body faces yet; the
    // envelope plane it leaves unexplained is reported with the rest.
    assert_eq!(unclaimed, 4);
    assert_eq!(p.subjects.len(), unclaimed, "{:?}", p.subjects);
    for s in &p.subjects {
        let face: usize = s
            .strip_prefix("face:")
            .unwrap_or_else(|| panic!("{s} is not a face"))
            .parse()
            .unwrap();
        assert!(face < a.binding.face_count);
        assert!(
            !a.features.iter().any(|f| f.faces.contains(&face)),
            "{s} belongs to a feature"
        );
    }
    assert_eq!(carries_id(&p.message), None);
}

/// #25 across the test parts and every AP242 fixture: `sheet` is the subject only of problems about paper, scale, fit
/// or the title block drawn on it.
#[test]
fn only_sheet_problems_have_the_sheet_as_subject() {
    const SHEET: [&str; 17] = [
        "layout_replanned",
        "layout_recovery_exhausted",
        "scale_fallback_applied",
        "legibility_floor_breached",
        "views_do_not_fit",
        "page_fit_uncertain",
        "sheet_below_smallest_scale",
        "sheet_preference_exceeded",
        "hole_table_unavailable",
        "general_tolerance_missing",
        "title_field_overflow",
        "annotation_off_sheet",
        "step_general_tolerance_disagreement",
        "step_material_disagreement",
        "sheet_label_replaced",
        "sheet_zones_kept",
        "spec_entries_inactive",
    ];
    for (name, plan) in plans() {
        for p in &plan.problems {
            if p.subjects.iter().any(|s| s == "sheet") {
                assert!(
                    SHEET.contains(&p.code.as_str()),
                    "{name}: {} ({}) is not about the sheet: {}",
                    p.id,
                    p.code,
                    p.message
                );
            }
        }
    }
}

/// #26: the turned spindle's bracketed (12) is a reference, determined by the overall length and
/// the chain's other links; its controlling dimensions are not.
#[test]
fn the_spindles_bracketed_length_is_a_reference_with_what_determines_it() {
    // Independent profiles retain the reference/closure contract. Connected whole-part
    // profiles now default to baseline ordinates and have no redundant reference link.
    let mut a = spindle();
    for f in a.features.iter_mut().filter(|f| f.kind == "turned_step") {
        f.parameters["profile"]["body_key"] = json!([f.id]);
    }
    let plan = model::plan_recovered(&a, &spec(json!({"sheet":{"references":true}}))).unwrap();
    let d = plan
        .dimensions
        .iter()
        .find(|d| d.label == "(12)")
        .expect("the spindle's (12)");
    assert_eq!(d.id, "d:f:turned_step:3/step.length");
    assert!(d.reference);
    assert_eq!(
        d.determined_by,
        [
            "d:envelope/height.length",
            "d:f:turned_step:1/step.length",
            "d:f:turned_step:2/step.length"
        ]
    );
    for id in &d.determined_by {
        let by = plan.dimensions.iter().find(|o| o.id == *id).unwrap();
        assert!(!by.reference && by.determined_by.is_empty(), "{id}");
    }
    // On the wire: present on the reference, absent on the others.
    let wire = serde_json::to_value(&plan).unwrap();
    let dims = wire["dimensions"].as_array().unwrap();
    for w in dims {
        let bracketed = w["label"].as_str().unwrap().starts_with('(');
        assert_eq!(w.get("reference").is_some(), bracketed, "{w}");
        assert_eq!(w.get("determined_by").is_some(), bracketed, "{w}");
    }
    assert_eq!(
        dims.iter().find(|w| w["label"] == "(12)").unwrap()["reference"],
        true
    );
}

/// The specs a part is planned under, beside the defaults, to raise the spec's own problems:
/// one dimension omitted and toleranced; one given a band of nothing; an authored pair; each
/// principal view omitted; and, on the editor's plate, its grid pitch placed above and a fit on
/// a hole whose callout is omitted.
fn specs(part: &str, base: &Plan) -> Vec<(String, Spec)> {
    let ids: Vec<&str> = base.dimensions.iter().map(|d| d.id.as_str()).collect();
    let mut specs = Vec::new();
    if let Some(first) = ids.first() {
        specs.push((
            "omitted".into(),
            spec(json!({ "dimensions": { *first: { "omit": true, "tolerance": "±0.1" } } })),
        ));
    }
    if let Some(l) = base.dimensions.iter().find(|d| d.kind == "linear") {
        specs.push((
            "±0".into(),
            spec(json!({ "dimensions": { &l.id: { "tolerance": "±0" } } })),
        ));
    }
    if ids.len() >= 2 {
        specs.push((
            "authored".into(),
            spec(json!({ "dimension_source": "authored",
                         "dimensions": { ids[0]: {}, ids[1]: {} } })),
        ));
    }
    for v in ["front", "plan", "side"] {
        specs.push((
            format!("{v} omitted"),
            spec(json!({ "views": { v: { "omit": true } } })),
        ));
    }
    if part == "cnc-review-spindle" {
        specs.push((
            "thread and knurl override".into(),
            spec(json!({"features": {
                "f:hole:f3": {"thread": {"designation": "M5-6H", "length": 8}},
                "f:turned_step:f9": {"knurl": {"pattern": "straight", "pitch": 1.0}}
            }})),
        ));
    }
    if part == "mounting_plate" {
        specs.push((
            "pitch above".into(),
            spec(json!({ "dimensions": {
                "d:f:hole_pattern:f6/grid_pitch.length.col": { "place": { "side": "above" } } } })),
        ));
        specs.push((
            "fit, callout omitted".into(),
            spec(json!({ "features": { "f:hole:f9": { "fit": "H7" } },
                         "dimensions": { "d:f:hole:f9/bore.diameter": { "omit": true } } })),
        ));
    }
    specs
}

/// Every test part's plan under the defaults and [`specs`] (a spec a part refuses is left out),
/// and every AP242 fixture's under the defaults; made once for the tests that read them.
fn plans() -> &'static [(String, Plan)] {
    static PLANS: std::sync::OnceLock<Vec<(String, Plan)>> = std::sync::OnceLock::new();
    PLANS.get_or_init(|| {
        let mut parts: Vec<(String, Analysis)> = vec![
            (
                "cnc-review-spindle".into(),
                gz(
                    "samples/cnc-review-spindle.step.gz",
                    "cnc-review-spindle.step",
                ),
            ),
            (
                "plate (recognition)".into(),
                model::analyse_with(
                    &fixture("parts/plate.step"),
                    Some(&fixture("recognition/plate.json")),
                )
                .unwrap()
                .1,
            ),
            ("specify-sample-spindle".into(), spindle()),
            (
                "manufacturing_pmi".into(),
                gz("parts/manufacturing_pmi.step.gz", "manufacturing_pmi.step"),
            ),
        ];
        for e in std::fs::read_dir(fixture("parts")).unwrap() {
            let path = e.unwrap().path();
            // `unresolved.step` is refused before planning, by design.
            if path.extension().is_some_and(|x| x == "step")
                && let Ok((_, a)) = model::analyse_native(&path)
            {
                let name = path.file_stem().unwrap().to_string_lossy().into_owned();
                parts.push((name, a));
            }
        }
        let mut files: Vec<String> = std::fs::read_dir(fixture("pmi/step"))
            .unwrap()
            .filter_map(|e| {
                let name = e.unwrap().file_name().into_string().unwrap();
                name.ends_with(".gz").then_some(name)
            })
            .collect();
        files.sort();
        parts.extend(support::parallel(&files, |file| {
            (
                file.clone(),
                gz(&format!("pmi/step/{file}"), file.trim_end_matches(".gz")),
            )
        }));
        let bases = support::parallel(&parts, |(_, a)| cache::default_plan(a, true));
        let jobs: Vec<(String, String, &Analysis, Spec)> = parts
            .iter()
            .zip(&bases)
            .filter(|((part, _), _)| !part.ends_with(".gz"))
            .flat_map(|((part, a), base)| {
                specs(part, base)
                    .into_iter()
                    .map(move |(name, s)| (part.clone(), name, a, s))
            })
            .collect();
        let mut out: Vec<(String, Plan)> = parts
            .iter()
            .map(|(part, _)| format!("{part}, defaults"))
            .zip(bases.iter().cloned())
            .collect();
        out.extend(
            support::parallel(&jobs, |(part, name, a, s)| {
                model::plan_recovered(a, s)
                    .ok()
                    .map(|plan| (format!("{part}, {name}"), plan))
            })
            .into_iter()
            .flatten(),
        );
        out
    })
}

fn assert_public_pmi_message(message: &str) {
    assert_eq!(carries_id(message), None, "{message}");
    for internal in [
        "manufacturing_requirement:#",
        "features/",
        "faces/",
        "spec:",
        "sheet/default_finish",
    ] {
        assert!(!message.contains(internal), "{message}");
    }
}

/// #24 across the test parts and every AP242 fixture: no problem message carries an internal
/// ID (`d:`, `f:`, `v:`, `pmi:`, `n:`, `t:`, `face:`).
#[test]
fn no_problem_message_carries_an_internal_id() {
    let mut checked = 0;
    let mut found = Vec::new();
    for (name, plan) in plans() {
        for p in &plan.problems {
            checked += 1;
            if let Some(id) = carries_id(&p.message) {
                found.push(format!("{name}: {} names {id}: {}", p.code, p.message));
            }
        }
    }
    // #102: cover the override that was absent from the default-plan inventory, and
    // lint-only lowering failures (these need not appear among plan problems).
    let mut a = gz(
        "samples/cnc-review-spindle.step.gz",
        "cnc-review-spindle.step",
    );
    let mut overrides = 0;
    for (_, plan) in plans() {
        for p in plan
            .problems
            .iter()
            .filter(|p| p.code == "spec_overrides_pmi")
        {
            overrides += 1;
            assert_public_pmi_message(&p.message);
        }
    }
    assert!(overrides >= 2);
    let Some(draftwright::pmi::Reading::Read(r)) = &mut a.pmi else {
        panic!("PMI fixture")
    };
    let requirement = r
        .records
        .iter_mut()
        .find(|r| r.kind == "internal_thread")
        .unwrap();
    let diagnostic = "unsupported manufacturing_requirement:#925 at features/f:hole:f3/thread";
    requirement.blockers.push(diagnostic.into());
    let lint = draftwright::lint::pmi_coverage::coverage(&a, &[]);
    let lowered: Vec<_> = lint
        .iter()
        .filter(|p| p.code == "pmi_not_lowered")
        .collect();
    assert!(!lowered.is_empty());
    assert!(lowered.iter().any(|p| {
        p.evidence
            .as_deref()
            .is_some_and(|e| e.contains(diagnostic))
    }));
    for p in lowered {
        assert_public_pmi_message(&p.message);
    }
    assert!(checked > 200, "{checked} problems checked");
    assert!(
        found.is_empty(),
        "{} messages carry IDs:\n{}",
        found.len(),
        found.join("\n")
    );
}

#[test]
fn recognition_warning_describes_the_part_and_retains_developer_provenance() {
    let (_, mut a) = cache::native(&fixture("parts/plate.step"));
    a.unrecognised = vec![model::Unrecognised {
        family: "freeform_surfaces".into(),
        records: Some(2),
    }];
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let p = plan
        .problems
        .iter()
        .find(|p| p.code == "features_not_recognised")
        .unwrap();
    assert_eq!(
        p.message,
        "Unrecognised features on this part: 2 freeform surfaces. These features are not dimensioned, sectioned or checked. If a missed feature matches a supported type, declare it in the Spec's feature_declarations using its face fingerprints. Otherwise, add the required dimensions and review it manually."
    );
    assert!(p.refs.contains(&a.recognition.as_ref().unwrap().recogniser));
    assert!(p.refs.iter().any(|r| r.contains("tools/recognise.py")));
    a.unrecognised[0].records = Some(1);
    let single = model::plan(&a, &Spec::default()).unwrap();
    let single = single
        .problems
        .iter()
        .find(|p| p.code == "features_not_recognised")
        .unwrap();
    assert!(
        single
            .message
            .contains("1 freeform surface. This feature is not dimensioned")
    );
    a.unrecognised[0].family = "thin_wall_bodies".into();
    let single = model::plan(&a, &Spec::default()).unwrap();
    let single = single
        .problems
        .iter()
        .find(|p| p.code == "features_not_recognised")
        .unwrap();
    assert!(single.message.contains("1 thin wall body."));
    a.unrecognised[0].records = None;
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let p = plan
        .problems
        .iter()
        .find(|p| p.code == "features_not_recognised")
        .unwrap();
    assert!(p.message.contains("whether this part has any is unknown"));
    assert!(!p.message.contains("on this part:"));
}

#[test]
fn problems_keep_tool_diagnostics_out_of_drawing_messages() {
    for (name, plan) in plans() {
        for p in &plan.problems {
            for internal in [
                "quiddity",
                "haecceity",
                "tools/",
                "not ported",
                "::",
                "lower_ap242_",
                "tolerance_withheld",
            ] {
                assert!(
                    !p.message.contains(internal),
                    "{name}: {}: {}",
                    p.code,
                    p.message
                );
            }
        }
    }
}

#[test]
fn hole_table_refusal_explains_the_datum_requirement() {
    let (_, mut a) = cache::native(&fixture("parts/plate.step"));
    // As in the schedule's datum-evidence regression: holes are kept, but no corner is proven.
    a.principal_profiles.clear();
    let plan = model::plan_escalated(&a, &spec(json!({"hole_table":"always"}))).unwrap();
    let p = plan
        .problems
        .iter()
        .find(|p| p.code == "hole_table_unavailable")
        .unwrap();
    assert_eq!(
        p.message,
        "A hole table needs holes located in X and Y from a datum corner. This part has no holes located that way; use the individual hole dimensions shown instead."
    );
    assert!(!p.subjects.is_empty());
    assert!(
        p.subjects
            .iter()
            .all(|id| plan.dimensions.iter().any(|d| d.id == *id))
    );
}

/// #138: the words shown beside Apply stay in drawing language across the
/// fixture variants and the corpus survey, including fallback operation bundles.
#[test]
fn fix_descriptions_use_drawing_language_across_fixtures_and_corpus() {
    fn check(name: &str, plan: &Plan) -> usize {
        let mut count = 0;
        for problem in &plan.problems {
            for fix in problem.offered_fixes() {
                let description = fix.description();
                let words: Vec<_> = description
                    .split(|c: char| !c.is_alphabetic())
                    .map(str::to_lowercase)
                    .collect();
                for banned in ["preview", "lane", "spec", "automatic", "auto", "corrective"] {
                    assert!(!words.iter().any(|w| w == banned), "{name}: {description}");
                }
                assert_eq!(carries_id(&description), None, "{name}: {description}");
                assert!(description.ends_with('.'), "{name}: {description}");
                let consequence = "The whole sheet is laid out again, so placements you have moved by hand may change.";
                assert_eq!(
                    description.matches(consequence).count(),
                    usize::from(fix.scope() == draftwright::fixes::Scope::Sheet),
                    "{name}: {description}"
                );
                assert_eq!(description, fix.description(), "deterministic wording");
                let reread: draftwright::fixes::Fix =
                    serde_json::from_value(serde_json::to_value(&fix).unwrap()).unwrap();
                assert_eq!(description, reread.description(), "no repeated consequence");
                count += 1;
            }
        }
        count
    }
    let fixture_count: usize = plans().iter().map(|(name, plan)| check(name, plan)).sum();
    assert!(fixture_count > 0);
    let corpus = std::env::var_os("DRAFTWRIGHT_CORPUS")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/Users/paul/repos/quiddity/tests/corpus"));
    if !corpus.is_dir() {
        assert_ne!(
            std::env::var("DRAFTWRIGHT_CORPUS_REQUIRED").as_deref(),
            Ok("1"),
            "corpus required"
        );
        return;
    }
    let files: Vec<_> = include_str!("../tools/repeated_span_parts.txt")
        .lines()
        .collect();
    let counts = support::parallel(&files, |file| {
        let (_, analysis) = cache::native(&corpus.join(file));
        let count = check(file, &cache::default_plan(&analysis, true));
        for issue in draftwright::lint::check(&analysis, &[]) {
            let wire = serde_json::to_value(issue).unwrap();
            if let Some(description) = wire["fix_description"].as_str() {
                let words: Vec<_> = description
                    .split(|c: char| !c.is_alphabetic())
                    .map(str::to_lowercase)
                    .collect();
                for banned in ["preview", "lane", "spec", "automatic", "auto", "corrective"] {
                    assert!(!words.iter().any(|w| w == banned), "{file}: {description}");
                }
                assert_eq!(carries_id(description), None, "{file}: {description}");
            }
        }
        count
    });
    assert!(counts.into_iter().sum::<usize>() > 0);
}
