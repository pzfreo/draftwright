//! Typed state and problems (the editor thin slice's stream S2), as checks:
//! - every plan item, drawn or left out, has a typed state, consistent with what the plan did
//!   with it (drawn when shown; a problem naming it when it is not drawable);
//! - every problem's subjects are live items (the plan's items, the sheet, the Analysis's
//!   features, the part's faces), and a problem's fix is an edit that resolves it;
//! - the editor's drawing review (`src/model/drawing-review.ts` at the editor's thin-slice
//!   branch, which reads an item's state from its basis sentence) can be written from the typed
//!   states alone, with no string matching, and classifies every item as it does;
//! - one label formatter: the text drawn for each item, an omitted one's included.
//!
//! The parts are the slice's (the editor's thin-slice plan, section 3.1): the test plate with its
//! Python recognition, the editor's mounting plate, NIST CTC-01 with its PMI and, from the
//! corpus, mfcadpp 10127 and GRM-03; and, for the PMI problems, every AP242 fixture.

#[path = "common/corpus.rs"]
mod corpus;
#[macro_use]
#[path = "common/support.rs"]
mod support;

#[path = "common/cache.rs"]
mod cache;
#[path = "common/sheet_rule.rs"]
mod sheet_rule;

use std::collections::{BTreeMap, BTreeSet};
use std::io::Read as _;
use std::path::{Path, PathBuf};

use draftwright::items::{self, Item, State};
use draftwright::model::{self, Analysis, Plan, Spec};
use serde_json::{Value, json};

fn fixture(path: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(path)
}

/// *gz* decompressed to a scratch file of its own named *name* (a binding names its part by the
/// file's stem), for the readers that take a path.
fn scratch(gz: &Path, name: &str) -> PathBuf {
    static N: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
    let n = N.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR"))
        .join("items")
        .join(format!("{}-{n}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join(name);
    let mut bytes = Vec::new();
    flate2::read::GzDecoder::new(std::fs::File::open(gz).unwrap())
        .read_to_end(&mut bytes)
        .unwrap();
    std::fs::write(&path, bytes).unwrap();
    path
}

/// An AP242 fixture (`tests/fixtures/pmi/step/<file>.gz`), analysed as the command line does.
fn ap242(file: &str) -> Analysis {
    let source = fixture(&format!("pmi/step/{file}.gz"));
    cache::analysed(file, &std::fs::read(&source).unwrap(), true, || {
        let path = scratch(&source, file);
        model::analyse_native(&path).unwrap()
    })
    .1
}

/// The slice's parts this checkout has: the plates and CTC-01 always; 10127 and GRM-03 from the
/// corpus (skipped without it, unless it is required).
fn slice() -> Vec<(String, Analysis)> {
    let features = fixture("recognition/plate.json");
    let mut out = vec![
        (
            "plate".to_string(),
            model::analyse_with(&fixture("parts/plate.step"), Some(&features))
                .unwrap()
                .1,
        ),
        (
            "mounting_plate".to_string(),
            cache::native(&fixture("parts/mounting_plate.step")).1,
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
            out.push((file.to_string(), cache::native(&dir.join(file)).1));
        }
    } else {
        eprintln!("SKIPPED: the corpus parts (the corpus is not found)");
    }
    out
}

fn spec(v: Value) -> Spec {
    serde_json::from_value(v).unwrap()
}

/// The specs each slice part is planned under: the defaults; one dimension omitted, and one
/// given a band of nothing; an authored set of two; each principal view omitted in turn. A spec
/// a part refuses is left out.
fn specs(a: &Analysis) -> Vec<(String, Spec)> {
    let base = cache::default_plan(a, true);
    let ids: Vec<&str> = base.dimensions.iter().map(|d| d.id.as_str()).collect();
    let linear = base
        .dimensions
        .iter()
        .find(|d| d.kind == "linear")
        .map(|d| d.id.as_str());
    let mut out = vec![("defaults".to_string(), Spec::default())];
    if let Some(first) = ids.first() {
        out.push((
            format!("{first} omitted"),
            spec(json!({ "dimensions": { *first: { "omit": true, "tolerance": "±0.1" } } })),
        ));
    }
    if let Some(l) = linear {
        out.push((
            format!("{l} ±0"),
            spec(json!({ "dimensions": { l: { "tolerance": "±0" } } })),
        ));
    }
    if ids.len() >= 2 {
        out.push((
            "authored".into(),
            spec(json!({ "dimension_source": "authored",
                         "dimensions": { ids[0]: {}, ids[1]: {} } })),
        ));
    }
    for v in ["front", "plan", "side"] {
        out.push((
            format!("{v} omitted"),
            spec(json!({ "views": { v: { "omit": true } } })),
        ));
    }
    out.into_iter()
        .filter(|(_, s)| model::plan(a, s).is_ok())
        .collect()
}

/// The IDs a problem may name: the plan's items (the sheet among them), the Analysis's
/// features and hole patterns, and the part's faces (`face:<index>`, for geometry no feature
/// claims).
fn live(plan: &Plan, a: &Analysis) -> BTreeSet<String> {
    let mut out: BTreeSet<String> = items::items(plan).into_iter().map(|i| i.id).collect();
    out.extend(a.features.iter().map(|f| f.id.clone()));
    out.extend((0..a.binding.face_count).map(|i| format!("face:{i}")));
    if let Some(r) = &a.recognition {
        out.extend(r.patterns.iter().map(|p| p.id.clone()));
    }
    out
}

/// Every item of *plan* has one ID and a state consistent with the plan: drawn exactly when
/// shown; not drawable only with a problem of its reason's code naming it; covered by a live
/// item; left out only in `omitted`. Every problem's subjects are live, and its fix edits *spec*.
/// Returns how many items it checked.
fn consistent(what: &str, plan: &Plan, a: &Analysis, spec: &Spec) -> usize {
    let all = items::items(plan);
    let mut seen = BTreeSet::new();
    for i in &all {
        assert!(seen.insert(&i.id), "{what}: {} is listed twice", i.id);
    }
    let live = live(plan, a);
    let named = |id: &str, code: &str| {
        plan.problems
            .iter()
            .any(|p| p.code == code && p.subjects.iter().any(|s| s == id))
    };
    let check = |id: &str, state: &State, placed: bool| match state {
        State::Shown => assert!(placed, "{what}: {id} is shown but not placed"),
        State::NotDrawable { reason_code } => {
            assert!(!placed, "{what}: {id} is placed but not drawable");
            assert!(
                named(id, reason_code),
                "{what}: {id} is not drawable for {reason_code}, which no problem on it says"
            );
        }
        State::Covered { by } => {
            assert!(!placed, "{what}: {id} is placed but covered");
            assert!(
                live.contains(by),
                "{what}: {id} is covered by {by}, no item"
            );
        }
        State::OmittedByUser | State::NotAuthored => {
            panic!("{what}: {id} is left out but listed as planned")
        }
    };
    for d in &plan.dimensions {
        check(&d.id, &d.state, d.placed.is_some());
    }
    for g in &plan.gdt {
        check(&g.id, &g.state, g.placed.is_some());
    }
    for n in &plan.notes {
        check(&n.id, &n.state, n.placed.is_some());
    }
    for t in &plan.tables {
        check(&t.id, &t.state, t.placed.is_some());
    }
    for t in &plan.hole_tables {
        check(
            &t.id,
            &t.state,
            !t.blocks.is_empty() && t.blocks.iter().all(|b| b.placed.is_some()),
        );
        assert!(
            t.rows
                .iter()
                .all(|r| plan.balloons.iter().any(|b| b.certifying
                    && b.tag.split('/').any(|tag| tag == r.tag)
                    && b.features.contains(&r.feature)
                    && Some(&b.view) == t.view.as_ref())),
            "{what}: every scheduled row owns a shown tag"
        );
    }
    for r in &plan.requirements {
        let drawn = match r.carrier.as_str() {
            "sheet" => r.placed.is_some(),
            "notes" => plan
                .notes
                .iter()
                .any(|n| n.id == r.id && n.placed.is_some()),
            "none" => false,
            _ => true,
        };
        if r.kind == "no_requirement" {
            assert_eq!(r.carrier, "none");
            assert_eq!(r.state, State::NotAuthored);
            assert!(!drawn);
        } else {
            check(&r.id, &r.state, drawn);
        }
    }
    for o in &plan.omitted {
        match &o.state {
            State::OmittedByUser => assert!(
                spec.dimensions.get(&o.id).is_some_and(|e| e.omit),
                "{what}: {} is omitted by the user, who did not omit it",
                o.id
            ),
            State::NotAuthored => assert!(
                !spec.dimensions.contains_key(&o.id),
                "{what}: {} is not authored, but listed",
                o.id
            ),
            State::Covered { by } => assert!(
                all.iter()
                    .any(|i| i.id == *by && matches!(i.kind.as_str(), "dimension" | "hole_table"))
                    || plan
                        .notes
                        .iter()
                        .any(|n| n.id == *by && !n.measurements.is_empty())
                    || plan.radius_table.as_ref().is_some_and(|t| t.table.id == *by
                        && t.table.placed.is_some()
                        && t.rows.iter().any(|r| r.dimension == o.id)),
                "{what}: {} is covered by {by}, no planned measurement owner, placed radius table or structured note",
                o.id
            ),
            s => panic!("{what}: {} is omitted as {s:?}", o.id),
        }
    }
    for p in &plan.problems {
        for s in &p.subjects {
            assert!(
                live.contains(s),
                "{what}: {} names {s}, which is no item of the plan or feature of the analysis",
                p.id
            );
        }
        if !p.fix.is_empty() {
            let ops = serde_json::to_value(&p.fix).unwrap();
            if let Err(e) = model::edit(spec, &ops) {
                panic!("{what}: {}'s fix {ops} is refused: {e}", p.id);
            }
        }
    }
    all.len()
}

/// Every plan item has a typed state and every problem subject is a live item ID, on the slice
/// parts under each of their specs and on every AP242 fixture.
#[test]
fn every_plan_item_has_a_state_and_every_problem_subject_is_a_live_item() {
    let results = support::parallel(&slice(), |(part, a)| {
        let mut checked = 0;
        let mut states = BTreeSet::new();
        for (name, spec) in specs(a) {
            let plan = if spec == Spec::default() {
                cache::default_plan(a, true)
            } else {
                model::plan_recovered(a, &spec).unwrap()
            };
            checked += consistent(&format!("{part}, {name}"), &plan, a, &spec);
            states.extend(items::items(&plan).into_iter().map(|i| match i.state {
                State::Covered { .. } => "covered".to_string(),
                State::NotDrawable { reason_code } => format!("not_drawable {reason_code}"),
                s => format!("{s:?}"),
            }));
        }
        (checked, states)
    });
    let mut checked = 0;
    let mut states = BTreeSet::new();
    for (n, found) in results {
        checked += n;
        states.extend(found);
    }
    eprintln!("states met: {states:?}");
    for s in [
        "Shown",
        "OmittedByUser",
        "NotAuthored",
        "covered",
        "not_drawable view_plan_incomplete",
    ] {
        assert!(states.contains(s), "no item was {s}: {states:?}");
    }
    let files: Vec<_> = std::fs::read_dir(fixture("pmi/step"))
        .unwrap()
        .map(|entry| entry.unwrap().file_name().into_string().unwrap())
        .filter_map(|name| name.strip_suffix(".gz").map(str::to_owned))
        .collect();
    checked += support::parallel(&files, |file| {
        let a = ap242(file);
        let plan = cache::default_plan(&a, true);
        consistent(file, &plan, &a, &Spec::default())
    })
    .into_iter()
    .sum::<usize>();
    assert!(checked > 500, "{checked} items checked");
}

/// The same on the quality parts (`tools/quality_parts.txt`, from the corpus), planned as the
/// command line plans them, one case per part. Automatic preferences retain every item, with
/// and without hole tables; explicitly overcrowded author choices retain the same loss-state/reporting contract.
fn on_the_quality_part(file: &str) {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found");
        return;
    };
    let a = cache::native(&dir.join(file)).1;
    let plan = cache::default_plan(&a, true);
    consistent(file, &plan, &a, &Spec::default());
    sheet_rule::assert_preferences(&plan);
    assert_eq!(
        not_drawable(&plan),
        0,
        "{file}: the default quality drawings retain every item"
    );
    // Exercise both forms; auto tables should not erase loss/state-machine coverage.
    let ladder = Spec {
        hole_table: draftwright::hole_table::Setting::Never,
        ..Spec::default()
    };
    let plan = model::plan_recovered(&a, &ladder).unwrap();
    consistent(file, &plan, &a, &ladder);
    sheet_rule::assert_preferences(&plan);
    assert_eq!(
        not_drawable(&plan),
        0,
        "{file}: the ladder (no hole table) quality drawings retain every item"
    );
    let forced = overcrowded();
    let plan = model::plan_recovered(&a, &forced).unwrap();
    consistent(file, &plan, &a, &forced);
}
quality_cases!(on_the_quality_part);

/// Items kept undrawn. An automatic profile-corner angle with no clear place is dropped by
/// design (LINT-angular-profile_angle_requirement): it stays visible as
/// `angular_dimension_dropped` and lint reports its corner, so it is not a lost item here.
fn not_drawable(plan: &Plan) -> usize {
    plan.dimensions
        .iter()
        .filter(|d| {
            matches!(&d.state, State::NotDrawable { reason_code } if reason_code != "angular_dimension_dropped")
        })
        .count()
}

fn overcrowded() -> Spec {
    serde_json::from_value(json!({
        "sheet": {"size": "A4", "scale": "10:1"}, "hole_table": "never"
    }))
    .unwrap()
}

/// The quality-part cases cover the manifest, an explicitly starved sheet exercises
/// not-drawable states, and the overcrowded author choices of `on_the_quality_part` drop an
/// item on at least one part.
#[test]
fn on_the_quality_parts_too() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found");
        return;
    };
    let list = std::fs::read_to_string(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tools/quality_parts.txt"),
    )
    .unwrap();
    let files: Vec<_> = list.lines().filter(|l| !l.trim().is_empty()).collect();
    assert_eq!(files, support::QUALITY_FILES, "one case per quality part");
    let a = cache::native(&dir.join("nist/nist_ftc_08_asme1_rc.stp")).1;
    let starved = spec(json!({"sheet": {"size": "A4", "scale": "1:1"}, "hole_table": "never"}));
    let mut plan = model::plan(&a, &starved).unwrap();
    draftwright::hole_table::escalate(&a, &mut plan, None);
    consistent("explicitly starved FTC08", &plan, &a, &starved);
    assert!(
        not_drawable(&plan) > 0,
        "the explicitly starved drawing still exercises not-drawable states"
    );
    // The ladder drawings drop nothing (each case asserts it), so this is the sum the
    // whole-manifest test asserted over ladder plus overcrowded drawings: some overcrowded
    // quality drawing drops an item.
    let forced = overcrowded();
    let dropped = files.iter().any(|file| {
        let a = cache::native(&dir.join(file)).1;
        not_drawable(&model::plan_recovered(&a, &forced).unwrap()) > 0
    });
    assert!(dropped, "no item was dropped");
}

/// The editor's drawing review, as `drawing-review.ts` classifies an item.
#[derive(Clone, Debug, PartialEq)]
enum Review {
    Drawn,
    Hidden,
    Unplaced,
    Covered(String),
}

/// `drawing-review.ts`'s `drawingItems` as it is: an item's state read from its `basis`
/// sentence (`^conveyed by (.+?): ` and `authored:`) and whether it is placed, after the spec's
/// own omissions (of the item or of its view).
fn by_prose(plan: &Plan, spec: &Spec) -> BTreeMap<String, Review> {
    let hidden = |id: &str, view: Option<&str>| {
        spec.dimensions.get(id).is_some_and(|e| e.omit)
            || view
                .and_then(|v| spec.views.get(v.trim_start_matches("v:")))
                .is_some_and(|v| v.omit)
    };
    let add = |out: &mut BTreeMap<String, Review>, id: &str, placed: bool, view: Option<&str>| {
        let state = if hidden(id, view) {
            Review::Hidden
        } else if placed {
            Review::Drawn
        } else {
            Review::Unplaced
        };
        out.insert(id.to_string(), state);
    };
    let mut out = BTreeMap::new();
    for d in &plan.dimensions {
        add(&mut out, &d.id, d.placed.is_some(), Some(&d.view));
    }
    for g in &plan.gdt {
        add(&mut out, &g.id, g.placed.is_some(), Some(&g.view));
    }
    for o in &plan.omitted {
        if !out.contains_key(&o.id) {
            add(&mut out, &o.id, false, None);
        }
        let row = out.get_mut(&o.id).unwrap();
        let owner = o
            .basis
            .strip_prefix("conveyed by ")
            .and_then(|r| r.split_once(": "))
            .map(|(owner, _)| owner.to_string());
        if *row != Review::Hidden
            && let Some(owner) = owner
        {
            *row = Review::Covered(owner);
        }
        if o.basis.starts_with("authored:") {
            *row = Review::Hidden;
        }
    }
    out
}

/// The same review from the typed states alone: no string is matched.
fn by_state(plan: &Plan, spec: &Spec) -> BTreeMap<String, Review> {
    let hidden = |i: &Item| {
        spec.dimensions.get(&i.id).is_some_and(|e| e.omit)
            || i.view
                .as_deref()
                .and_then(|v| spec.views.get(v.trim_start_matches("v:")))
                .is_some_and(|v| v.omit)
    };
    items::items(plan)
        .into_iter()
        .filter(|i| {
            matches!(
                i.kind.as_str(),
                "dimension" | "control_frame" | "datum_feature" | "label"
            )
        })
        .map(|i| {
            let review = match &i.state {
                _ if hidden(&i) => Review::Hidden,
                State::Shown => Review::Drawn,
                State::OmittedByUser | State::NotAuthored => Review::Hidden,
                State::Covered { by } => Review::Covered(by.clone()),
                State::NotDrawable { .. } => Review::Unplaced,
            };
            (i.id, review)
        })
        .collect()
}

/// The editor's drawing review classifies every item from its typed state as it does from the
/// basis sentences: drawn, hidden, unplaced, or combined into the item that states it.
#[test]
fn the_drawing_review_reads_typed_states_and_classifies_as_from_prose() {
    let results = support::parallel(&slice(), |(part, a)| {
        let mut compared = BTreeMap::new();
        for (name, spec) in specs(a) {
            let plan = if spec == Spec::default() {
                cache::default_plan(a, true)
            } else {
                model::plan_recovered(a, &spec).unwrap()
            };
            let (prose, typed) = (by_prose(&plan, &spec), by_state(&plan, &spec));
            assert_eq!(
                prose.keys().collect::<Vec<_>>(),
                typed.keys().collect::<Vec<_>>(),
                "{part}, {name}: the items differ"
            );
            for (id, p) in &prose {
                let t = &typed[id];
                // A hole callout or location the hole table states is unplaced to the prose
                // review (no basis says so at its start) and covered by a real table when typed.
                // Improved composition can select a separately named per-view schedule.
                let tabled = *p == Review::Unplaced
                    && matches!(t, Review::Covered(owner)
                        if plan.hole_tables.iter().any(|table| table.id == *owner));
                assert!(
                    p == t || tabled,
                    "{part}, {name}: {id} is {p:?} from prose, {t:?} typed"
                );
                *compared.entry(format!("{t:?}")).or_insert(0) += 1;
            }
        }
        compared
    });
    let mut compared = BTreeMap::new();
    for found in results {
        for (kind, count) in found {
            *compared.entry(kind).or_insert(0) += count;
        }
    }
    eprintln!("compared: {compared:?}");
    // An item not drawable is hidden by its view's omission, to both reviews, on these specs;
    // its state is checked by the test above.
    for kind in ["Drawn", "Hidden"] {
        assert!(
            compared.contains_key(kind),
            "no item was {kind}: {compared:?}"
        );
    }
    assert!(
        compared.keys().any(|k| k.starts_with("Covered")),
        "no item was covered: {compared:?}"
    );
}

/// One label formatter: each item's label is the text the plan draws for it (a dimension's
/// terms, a frame's compartments), and a dimension the spec omits is labelled as it is drawn when
/// it is not.
#[test]
fn every_item_is_labelled_as_it_is_drawn() {
    support::parallel(&slice(), |(part, a)| {
        let base = cache::default_plan(a, true);
        for d in &base.dimensions {
            assert_eq!(
                items::label(&base, &d.id).as_deref(),
                Some(d.label.as_str())
            );
            assert_eq!(
                d.label,
                draftwright::callouts::label(&d.terms),
                "{part}: {}",
                d.id
            );
        }
        for g in &base.gdt {
            let label = items::label(&base, &g.id).unwrap();
            assert_eq!(label, g.cells.join(" | "), "{part}: {}", g.id);
        }
        // Each drawn dimension omitted in turn (the first five) keeps its label.
        for d in base.dimensions.iter().take(5) {
            let s = spec(json!({ "dimensions": { d.id.clone(): { "omit": true } } }));
            let less = model::plan_recovered(a, &s).unwrap();
            let o = less.omitted.iter().find(|o| o.id == d.id).unwrap();
            assert_eq!(o.state, State::OmittedByUser, "{part}: {}", d.id);
            assert_eq!(
                items::label(&less, &d.id).as_deref(),
                Some(d.label.as_str()),
                "{part}: {} omitted",
                d.id
            );
        }
    });
}

/// A problem's fix is an edit that resolves it: applied with `edit`, the problem is gone from
/// the plan. Each problem the spec can cause and an edit resolve is made, and fixed.
#[test]
fn a_problems_fix_resolves_it() {
    let features = fixture("recognition/plate.json");
    let plate = model::analyse_with(&fixture("parts/plate.step"), Some(&features))
        .unwrap()
        .1;
    let base = cache::default_plan(&plate, false);
    let linear = base.dimensions.iter().find(|d| d.kind == "linear").unwrap();
    // The holes' callout (the plate's is its hole pattern's), and a hole it states.
    let callout = base
        .dimensions
        .iter()
        .find(|d| d.kind == "callout" && d.measurements[0].feature.starts_with("f:hole:"))
        .unwrap();
    let hole = callout.measurements[0].feature.clone();
    // A dimension the plan view carries that the front or the side could carry instead.
    let elsewhere = ["front", "side"].into_iter().find_map(|v| {
        base.dimensions.iter().find_map(|d| {
            let s = spec(json!({ "dimensions": { d.id.clone(): { "view": v } } }));
            (d.view != format!("v:{v}") && model::plan(&plate, &s).is_ok())
                .then(|| (d.id.clone(), v))
        })
    });
    let (moved, to) = elsewhere.expect("a dimension that can move");
    let mut other = plate.binding.clone();
    other.source_sha256 = "0".repeat(64);
    let mut cases: Vec<(&str, &Analysis, Spec)> = vec![
        (
            "tolerance_withheld",
            &plate,
            spec(json!({ "dimensions": { linear.id.clone(): { "tolerance": "±0" } } })),
        ),
        (
            "tolerance_not_shown",
            &plate,
            spec(
                json!({ "dimensions": { linear.id.clone(): { "omit": true, "tolerance": "±0.1" } } }),
            ),
        ),
        (
            "tolerance_not_shown",
            &plate,
            spec(json!({ "features": { hole.clone(): { "fit": "H7" } },
                         "dimensions": { callout.id.clone(): { "omit": true } } })),
        ),
        (
            "dimension_view_not_shown",
            &plate,
            spec(json!({ "dimensions": { moved.clone(): { "view": to } },
                         "views": { to: { "omit": true } } })),
        ),
        (
            "view_plan_incomplete",
            &plate,
            spec(json!({ "views": { "front": { "omit": true }, "side": { "omit": true } } })),
        ),
        (
            "spec_entries_inactive",
            &plate,
            spec(json!({ "binding": other,
                         "dimensions": { callout.id.clone(): { "omit": true } },
                         "features": { hole.clone(): { "fit": "H7" } } })),
        ),
    ];
    let ctc01 = ap242("nist_ctc_01_asme1_ap242-e1.stp");
    let pmi = cache::default_plan(&ctc01, false);
    // A glyph on its lowered surface: one #224 attached to its hole's mark (datum C, in the plan)
    // goes back to its surface in another view when that view is omitted, so it is not dropped.
    let frame_view = pmi
        .gdt
        .iter()
        .find(|g| !g.basis.contains(", which sees the hole"))
        .expect("a glyph on its surface")
        .view
        .trim_start_matches("v:")
        .to_string();
    cases.push((
        "pmi_not_drawn",
        &ctc01,
        spec(json!({ "views": { frame_view: { "omit": true } } })),
    ));
    let ftc06 =
        corpus::dir().map(|dir| cache::native(&dir.join("nist/nist_ftc_06_asme1_rd.stp")).1);
    if let Some(a) = &ftc06 {
        cases.push((
            "section_not_shown",
            a,
            spec(json!({ "views": { "plan": { "omit": true } } })),
        ));
    } else {
        eprintln!("SKIPPED: section_not_shown (the corpus is not found)");
    }
    let mut fixed = BTreeSet::new();
    for (code, a, s) in &cases {
        let plan = model::plan(a, s).unwrap();
        let found: Vec<&model::Problem> =
            plan.problems.iter().filter(|p| p.code == *code).collect();
        assert!(
            !found.is_empty(),
            "{code}: not reported under {}",
            serde_json::to_string(s).unwrap()
        );
        for p in found {
            assert!(!p.fix.is_empty(), "{}: no fix", p.id);
            let ops = serde_json::to_value(&p.fix).unwrap();
            let edited = model::edit(s, &ops).unwrap_or_else(|e| panic!("{}: {ops}: {e}", p.id));
            let after = model::plan(a, &edited).unwrap();
            assert!(
                after.problems.iter().all(|q| q.id != p.id),
                "{}: still reported after its fix {ops}",
                p.id
            );
            fixed.insert(*code);
        }
    }
    assert!(fixed.len() >= 7, "{fixed:?}");
}
