//! Spec-authored tolerances and fits reach the label verbatim (ARCH0011-1, ARCH0019-2/3,
//! ADR5-D2), compared with draftwright's sheets for the same declarations.
//!
//! draftwright's labels below were printed by `tools/probe_tolerances.py` (draftwright at
//! origin/main 586628b) for the same tolerances and fits declared through its `Sheet` API on the
//! same corpus parts; each difference has a verdict.

#[path = "common/local_fix.rs"]
mod local_fix;

#[path = "common/corpus.rs"]
mod corpus;

use std::path::{Path, PathBuf};

use draftwright::haecceity::Part;
use draftwright::model::{self, Analysis, Plan, Spec};
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

/// The fixture part *name* with its recognition.
fn analysed(name: &str) -> (Part, Analysis) {
    model::analyse_with(
        &fixture(&format!("parts/{name}.step")),
        Some(&fixture(&format!("recognition/{name}.json"))),
    )
    .unwrap()
}

/// A spec as draftwright's `Sheet` API would print it: a fit as its class alone (`fits.FitClass`'s
/// default), so the port's labels compare with draftwright's (`sheet/fit_limits: false`).
fn spec(value: Value) -> Spec {
    let mut s: Spec = serde_json::from_value(value).unwrap();
    s.sheet.fit_limits.get_or_insert(false);
    s
}

fn label<'a>(plan: &'a Plan, id: &str) -> &'a str {
    &plan
        .dimensions
        .iter()
        .find(|d| d.id == id)
        .unwrap_or_else(|| panic!("no {id}"))
        .label
}

fn problem<'a>(plan: &'a Plan, id: &str) -> Option<&'a model::Problem> {
    plan.problems.iter().find(|p| p.id == id)
}

fn codes<'a>(plan: &'a Plan, code: &str) -> Vec<&'a str> {
    plan.problems
        .iter()
        .filter(|p| p.code == code)
        .map(|p| p.id.as_str())
        .collect()
}

/// A verdict on a difference from draftwright's sheet, with its evidence.
struct Differs {
    id: &'static str,
    python: &'static str,
    rust: &'static str,
    verdict: &'static str,
}

/// nist_ftc_06: an envelope extent with ± and with a pair of deviations, a band of nothing, a
/// pattern's bore and grid pitch toleranced, a hole pair with one fit, and collapsed bosses with
/// equal and unequal fits.
#[test]
fn ftc06_prints_the_authored_bands_as_draftwright_does() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let (_, a) = corpus::analyse(&dir, "nist/nist_ftc_06_asme1_rd.stp", "ftc06");
    // draftwright: envelope().tolerance(0.02, on="width"), (0, on="height"),
    // (0.02, 0.1, on="depth"); pattern 7 .tolerance(0.03, on="bore.diameter"), pattern 8
    // .tolerance(0.1, on="grid_pitch.length.row"); of(hole 10).fit("H7") (its 2× pair); bosses
    // 11 h6 (of four), 15 and 16 g6, 17 h6 and 18 h7.
    let s = spec(json!({
        "dimensions": {
            "d:envelope/width.length": { "tolerance": "±0.02" },
            "d:envelope/height.length": { "tolerance": "±0" },
            "d:envelope/depth.length": { "tolerance": "+0.1 -0.02" },
            "d:f:hole_pattern:2/bore.diameter": { "tolerance": "±0.03" },
            "d:f:hole_pattern:1/grid_pitch.length.row": { "tolerance": "±0.1" }
        },
        "features": {
            "f:hole:11": { "fit": "H7" }, "f:hole:12": { "fit": "H7" },
            "f:boss:1": { "fit": "h6" },
            "f:boss:5": { "fit": "g6" }, "f:boss:7": { "fit": "g6" },
            "f:boss:6": { "fit": "h6" }, "f:boss:8": { "fit": "h7" }
        }
    }));
    let plan = model::plan(&a, &s).unwrap();
    // Equal to draftwright's labels.
    for (id, python) in [
        ("d:envelope/width.length", "304.8 ±0.02"),
        ("d:envelope/depth.length", "97.79 +0.1 -0.02"),
        ("d:f:hole_pattern:1/grid_pitch.length.row", "127 ±0.1"),
        ("d:f:hole:11/bore.diameter", "2× ⌀6.35 H7 ↧ 12.7"),
        ("d:f:boss:5/boss.diameter", "2× ø31.75 g6"),
    ] {
        assert_eq!(label(&plan, id), python, "{id}");
    }
    // The same band as draftwright's, wrapped where the maintainer decided (2026-10-28): a
    // callout symbol stays on the line of its value. draftwright breaks after the counterbore
    // symbol ("4× ⌀7.137 ±0.03 THRU ⌴\n⌀10.16 ↧ 7.94"); the port carries `⌴` to the next line.
    assert_eq!(
        label(&plan, "d:f:hole_pattern:2/bore.diameter"),
        "4× ⌀7.137 ±0.03 THRU\n⌴ ⌀10.16 ↧ 7.94"
    );
    let differs = [
        // ARCH0019-2: draftwright prints the band of nothing as "±0.0", a tolerance that states
        // none; the port withholds it and says so.
        Differs {
            id: "d:envelope/height.length",
            python: "247.7 ±0.0",
            rust: "247.65",
            verdict: "rust-correct",
        },
        // ARCH0019-3: draftwright splits the four bosses into "3× ø44.5" and "ø44.5 h6", and
        // the pair into "ø22.9 h6" and "ø22.9 h7", so every authored fit is on the sheet; the
        // port keeps the collapsed mark, prints no fit on it and reports the fits it withholds.
        // Splitting needs the boss and hole planners to group by fit.
        Differs {
            id: "d:f:boss:1/boss.diameter",
            python: "3× ø44.5 | ø44.5 h6",
            rust: "4× ø44.45",
            verdict: "rust-wrong",
        },
        Differs {
            id: "d:f:boss:6/boss.diameter",
            python: "ø22.9 h6 | ø22.9 h7",
            rust: "2× ø22.86",
            verdict: "rust-wrong",
        },
    ];
    for d in &differs {
        assert_eq!(label(&plan, d.id), d.rust, "{}", d.id);
        assert_ne!(d.python, d.rust);
        assert!(["rust-correct", "rust-wrong"].contains(&d.verdict));
        let p = problem(&plan, &format!("p:tolerance_withheld:{}", d.id))
            .unwrap_or_else(|| panic!("{} is withheld without a word", d.id));
        assert_eq!(p.severity, "warning");
        assert_eq!(p.subjects[0], d.id);
    }
    assert_eq!(codes(&plan, "tolerance_withheld").len(), differs.len());
    let unequal = problem(&plan, "p:tolerance_withheld:d:f:boss:6/boss.diameter").unwrap();
    assert_eq!(
        unequal.subjects,
        ["d:f:boss:6/boss.diameter", "f:boss:6", "f:boss:8"]
    );
    assert!(
        unequal
            .message
            .contains("(h6, h7, in the order of the problem's subjects)"),
        "{}",
        unequal.message
    );
    let nothing = problem(&plan, "p:tolerance_withheld:d:envelope/height.length").unwrap();
    // The message says what to do; the path it acts on is the fix's (the message names no ID).
    assert!(
        nothing.message.contains("remove the tolerance"),
        "{}",
        nothing.message
    );
    assert_eq!(
        serde_json::to_value(local_fix::edits(nothing, &plan)).unwrap(),
        serde_json::json!([{ "op": "remove", "path": "dimensions/d:envelope~1height.length/tolerance" }])
    );
    assert!(codes(&plan, "tolerance_not_shown").is_empty());
}

/// nist_ctc_01: a hole pair's fit, a grid's bore, a collapsed fillet mark, a slot width with a
/// one-sided pair, a hex boss's size across flats and an overall height with a whole tolerance.
#[test]
fn ctc01_prints_the_authored_bands_as_draftwright_does() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let (_, a) = corpus::analyse(&dir, "nist/nist_ctc_01_asme1_rd.stp", "ctc01");
    // draftwright: of(hole 2).fit("H7") (its 2× pair); pattern 0 .tolerance(0.05,
    // on="bore.diameter"); fillets 12–18 each .tolerance(0.5); slot 3 .tolerance(0, 0.1,
    // on="slot_width.length"); polygonal boss 5 .tolerance(0.2,
    // on="polygon_across_flats.length"); envelope().tolerance(1, on="height").
    let s = spec(json!({
        "dimensions": {
            "d:f:hole_pattern:1/bore.diameter": { "tolerance": "±0.05" },
            "d:f:fillet:6/fillet.radius": { "tolerance": "±0.5" },
            "d:f:slot:1/slot_width.length": { "tolerance": "+0.1 -0" },
            "d:f:polygonal_boss:1/polygon_across_flats.length": { "tolerance": "±0.2" },
            "d:envelope/height.length": { "tolerance": "±1" }
        },
        "features": { "f:hole:1": { "fit": "H7" }, "f:hole:2": { "fit": "H7" } }
    }));
    let plan = model::plan(&a, &s).unwrap();
    for (id, python, given) in [
        ("d:f:hole:1/bore.diameter", "2× ⌀20 H7 ↧ 45", "fit"),
        (
            "d:f:hole_pattern:1/bore.diameter",
            "4× ⌀35 ±0.05 THRU",
            "tolerance",
        ),
        ("d:f:fillet:6/fillet.radius", "7× R50 ±0.5", "tolerance"),
        // Python prints "40 +0.1 -0.0"; ISO 129-1 writes a zero deviation 0, unsigned.
        ("d:f:slot:1/slot_width.length", "40 +0.1 0", "tolerance"),
        (
            "d:f:polygonal_boss:1/polygon_across_flats.length",
            "HEX 100 ±0.2 A/F",
            "tolerance",
        ),
        ("d:envelope/height.length", "150 ±1.0", "tolerance"),
    ] {
        let d = plan.dimensions.iter().find(|d| d.id == id).unwrap();
        assert_eq!(d.label, python, "{id}");
        assert_eq!(d.label, d.terms.join(" "));
        assert_eq!(d.from_spec, [given], "{id}");
        assert!(!d.default, "{id}");
    }
    // The spec changes nothing else and adds no problem: the longer labels place as the shorter
    // ones did on this sheet. (From stream d-placement-fit until callout leaders were placed
    // around the dimensions, stream d-callout-leaders, the fillet callout beside the plan ran
    // past the plan's reserved area with its longer label 7× R50 ±0.5; its leader now stands
    // where either label fits.)
    let bare = model::plan(&a, &Spec::default()).unwrap();
    let added = "p:annotation_outside_area:d:f:fillet:6/fillet.radius|v:plan";
    assert_eq!(
        bare.problems
            .iter()
            .map(|p| p.id.as_str())
            .collect::<Vec<_>>(),
        plan.problems
            .iter()
            .map(|p| p.id.as_str())
            .filter(|id| *id != added)
            .collect::<Vec<_>>()
    );
    assert!(
        !plan.problems.iter().any(|p| p.id == added),
        "the fillet callout's longer label still fits beside the plan"
    );
    // Members with different fits: draftwright holds the pair as one feature, so it cannot be
    // told two fits (not-applicable); the port withholds the fit and names both.
    let mixed = spec(json!({
        "features": { "f:hole:1": { "fit": "H7" }, "f:hole:2": { "fit": "H8" } }
    }));
    let plan = model::plan(&a, &mixed).unwrap();
    assert_eq!(label(&plan, "d:f:hole:1/bore.diameter"), "2× ⌀20 ↧ 45");
    let p = problem(&plan, "p:tolerance_withheld:d:f:hole:1/bore.diameter").unwrap();
    assert_eq!(
        p.subjects,
        ["d:f:hole:1/bore.diameter", "f:hole:1", "f:hole:2"]
    );
    // One member with a fit and one without: also withheld.
    let one = spec(json!({ "features": { "f:hole:2": { "fit": "H7" } } }));
    let plan = model::plan(&a, &one).unwrap();
    assert_eq!(label(&plan, "d:f:hole:1/bore.diameter"), "2× ⌀20 ↧ 45");
    assert!(problem(&plan, "p:tolerance_withheld:d:f:hole:1/bore.diameter").is_some());
}

/// A pattern's members with one fit state it on the pattern's callout; a pitch takes a
/// tolerance (the planner collapses only gaps equal within 1e-4 mm, so it is every gap's).
#[test]
fn a_patterns_callout_and_pitch_state_their_bands() {
    let (_, a) = analysed("two_grids");
    let pattern = a.recognition.as_ref().unwrap().patterns[0].clone();
    let mut features = serde_json::Map::new();
    for m in &pattern.members {
        features.insert(m.clone(), json!({ "fit": "G7" }));
    }
    let s = spec(json!({
        "features": features,
        "dimensions": { format!("d:{}/pitch.length", pattern.id): { "tolerance": "±0.05" } }
    }));
    let plan = model::plan(&a, &s).unwrap();
    assert_eq!(
        label(&plan, &format!("d:{}/bore.diameter", pattern.id)),
        "3× ⌀6 G7 THRU"
    );
    assert_eq!(
        label(&plan, &format!("d:{}/pitch.length", pattern.id)),
        "2× 10 ±0.05"
    );
    // A fit is set on holes, not on their pattern.
    let e = model::plan(
        &a,
        &spec(json!({ "features": { pattern.id.clone(): { "fit": "H7" } } })),
    )
    .unwrap_err();
    assert!(e.message.contains("is a hole pattern"), "{e}");
    assert_eq!(e.choices, pattern.members);
}

/// What draftwright refuses, and what the port adds to it, is refused with a message that names
/// the entry and what would do.
#[test]
fn a_band_that_cannot_be_read_or_applied_is_refused() {
    let (_, a) = analysed("plate");
    let refused = |s: Value| model::plan(&a, &spec(s)).unwrap_err();
    // The forms: ± and a pair of deviations; a close spelling says what it reads as.
    let e = model::edit(
        &Spec::default(),
        &json!([{ "op": "set", "path": "dimensions/d:envelope~1width.length/tolerance", "value": "+-0.1" }]),
    )
    .unwrap_err();
    assert!(
        e.message.contains("d:envelope/width.length") && e.message.contains("reads as \"±0.1\""),
        "{e}"
    );
    let e = refused(json!({ "dimensions": { "d:envelope/width.length": { "tolerance": "0.1" } } }));
    assert!(e.message.contains("d:envelope/width.length"), "{e}");
    assert_eq!(e.choices, ["±0.05", "+0.1 -0.02"]);
    // A fit from outside draftwright's table, of the wrong case, or on a feature that takes none.
    let hole = "f:hole:1";
    let class_only = model::plan(&a, &spec(json!({"features":{hole:{"fit":"K7"}}}))).unwrap();
    assert!(
        !class_only
            .problems
            .iter()
            .any(|p| p.code == "fit_deviations_unavailable")
    );
    assert!(
        class_only
            .dimensions
            .iter()
            .any(|d| d.placed.is_some() && d.label.contains("K7"))
    );
    let p = model::plan(
        &a,
        &spec(json!({"sheet":{"fit_deviations":true}, "features":{hole:{"fit":"K7"}}})),
    )
    .unwrap();
    assert!(
        p.problems
            .iter()
            .any(|p| p.code == "fit_deviations_unavailable")
    );
    assert!(label(&p, "d:f:hole_pattern:1/bore.diameter").contains("K7"));
    let e = refused(json!({ "features": { hole: { "fit": "h7" } } }));
    assert!(e.message.starts_with("\"h7\" reads as \"H7\""), "{e}");
    assert_eq!(e.choices, ["H7"]);
    let e = refused(json!({ "features": { "f:hole:9": { "fit": "H7" } } }));
    assert!(e.message.contains("no feature \"f:hole:9\""), "{e}");
    assert!(e.choices.contains(&hole.to_string()), "{e}");
    // A size that has a tolerance and a fit is told to take one.
    let e = refused(json!({
        "dimensions": { "d:f:hole_pattern:1/bore.diameter": { "tolerance": "±0.1" } },
        "features": { hole: { "fit": "H7" } }
    }));
    assert!(e.message.contains("one or the other"), "{e}");
    // A nominal size past draftwright's table (250 mm).
    let mut big = a.clone();
    let f = big.features.iter_mut().find(|f| f.id == hole).unwrap();
    f.parameters.insert("diameter".into(), json!(300.0));
    let p = model::plan(
        &big,
        &spec(json!({"sheet":{"fit_deviations":true}, "features":{hole:{"fit":"H7"}}})),
    )
    .unwrap();
    assert!(
        p.problems
            .iter()
            .any(|p| p.code == "fit_deviations_unavailable")
    );
    assert!(
        p.dimensions
            .iter()
            .any(|d| d.placed.is_some() && d.label.contains("H7"))
    );
    // A boss takes a shaft class, and a slot or fillet no fit at all.
    let (_, b) = analysed("boss_circle");
    let e = model::plan(
        &b,
        &spec(json!({ "features": { "f:boss:1": { "fit": "H7" } } })),
    )
    .unwrap_err();
    assert!(e.message.starts_with("\"H7\" reads as \"h7\""), "{e}");
    let plan = model::plan(
        &b,
        &spec(json!({ "features": { "f:boss:1": { "fit": "h7" } } })),
    )
    .unwrap();
    assert_eq!(label(&plan, "d:f:boss:1/boss.diameter"), "ø12 h7");
}

/// Nothing the spec authors is dropped in silence: a tolerance on a dimension the plan leaves
/// out, and a fit on a hole whose callout it leaves out, are reported against their IDs.
#[test]
fn a_band_on_what_the_plan_leaves_out_is_reported() {
    let (_, a) = analysed("plate");
    let callout = "d:f:hole_pattern:1/bore.diameter";
    let s = spec(json!({
        "dimensions": {
            "d:envelope/width.length": { "omit": true, "tolerance": "±0.1" },
            (callout): { "omit": true }
        },
        "features": { "f:hole:1": { "fit": "H7" } }
    }));
    let plan = model::plan(&a, &s).unwrap();
    assert_eq!(
        codes(&plan, "tolerance_not_shown"),
        [
            "p:tolerance_not_shown:d:envelope/width.length",
            "p:tolerance_not_shown:f:hole:1"
        ]
    );
    // An authored set that does not list the callout leaves the fit unstated too.
    let s = spec(json!({
        "dimension_source": "authored",
        "dimensions": { "d:envelope/width.length": { "tolerance": "±0.1" } },
        "features": { "f:hole:1": { "fit": "H7" } }
    }));
    let plan = model::plan(&a, &s).unwrap();
    assert_eq!(label(&plan, "d:envelope/width.length"), "60 ±0.1");
    assert_eq!(
        codes(&plan, "tolerance_not_shown"),
        ["p:tolerance_not_shown:f:hole:1"]
    );
}

/// ADR5-D2: only what the spec authors appears. Against the default plan, the authored items
/// gain exactly their band, every other item is unchanged, and the bands appear nowhere else.
#[test]
fn only_the_authored_bands_appear() {
    let (_, a) = analysed("plate");
    let s = spec(json!({
        "dimensions": { "d:envelope/height.length": { "tolerance": "+0.2 -0.1" } },
        "features": {
            "f:hole:1": { "fit": "H7" }, "f:hole:2": { "fit": "H7" },
            "f:hole:3": { "fit": "H7" }, "f:hole:4": { "fit": "H7" }
        }
    }));
    let bare = model::plan(&a, &Spec::default()).unwrap();
    let plan = model::plan(&a, &s).unwrap();
    assert_eq!(bare.dimensions.len(), plan.dimensions.len());
    for (b, d) in bare.dimensions.iter().zip(&plan.dimensions) {
        assert_eq!(b.id, d.id);
        let mut terms = b.terms.clone();
        match d.id.as_str() {
            "d:envelope/height.length" => terms.insert(1, "+0.2 -0.1".into()),
            "d:f:hole_pattern:1/bore.diameter" => terms.insert(2, "H7".into()),
            _ => {
                assert!(b.default && d.default && d.from_spec.is_empty(), "{}", d.id);
                assert_eq!(b.basis, d.basis);
            }
        }
        assert_eq!(d.terms, terms, "{}", d.id);
        assert_eq!(b.measurements, d.measurements);
    }
    assert_eq!(
        label(&plan, "d:f:hole_pattern:1/bore.diameter"),
        "4× ⌀6 H7 THRU"
    );
}

/// Across a revision, feature-keyed fits are kept in the spec but not applied, and listed, as
/// the other feature-keyed entries are (R3 invariant 3).
#[test]
fn fits_from_another_revision_are_kept_inactive() {
    let (_, a) = analysed("plate");
    let mut s = spec(json!({ "features": { "f:hole:1": { "fit": "H7" } } }));
    let (_, other) = analysed("two_grids");
    s.binding = Some(other.binding.clone());
    let plan = model::plan(&a, &s).unwrap();
    assert_eq!(
        label(&plan, "d:f:hole_pattern:1/bore.diameter"),
        "4× ⌀6 THRU"
    );
    let p = problem(&plan, "p:spec_entries_inactive:part").unwrap();
    assert!(p.refs.contains(&"f:hole:1".to_string()));
    assert_eq!(plan.spec.features, s.features);
}

/// `edit` sets a tolerance and a fit; the spec travels in the SVG, PDF and DXF, reads back
/// exactly, and plans the same labels again.
#[test]
fn an_edited_band_survives_the_round_trip_through_every_format() {
    let (p, a) = analysed("plate");
    let edited = model::edit(
        &Spec::default(),
        &json!([
            { "op": "set", "path": "sheet/fit_deviations", "value": true },
            { "op": "set", "path": "dimensions/d:envelope~1width.length/tolerance", "value": "±0.05" },
            { "op": "set", "path": "features/f:hole:1/fit", "value": "H7" },
            { "op": "set", "path": "features/f:hole:2/fit", "value": "H7" },
            { "op": "set", "path": "features/f:hole:3/fit", "value": "H7" },
            { "op": "set", "path": "features/f:hole:4/fit", "value": "H7" },
        ]),
    )
    .unwrap();
    let plan = model::plan(&a, &edited).unwrap();
    assert_eq!(label(&plan, "d:envelope/width.length"), "60 ±0.05");
    assert_eq!(
        label(&plan, "d:f:hole_pattern:1/bore.diameter"),
        "4× ⌀6 H7 +0.012\n0 THRU"
    );
    // The sheet hides fit limits when asked: the class alone, as draftwright prints it.
    let hidden = model::edit(
        &edited,
        &json!([{ "op": "set", "path": "sheet/fit_deviations", "value": false }]),
    )
    .unwrap();
    assert_eq!(
        label(
            &model::plan(&a, &hidden).unwrap(),
            "d:f:hole_pattern:1/bore.diameter"
        ),
        "4× ⌀6 H7 THRU"
    );
    let drawing = model::render(&p, &a, &plan, &font()).unwrap();
    // The label's text run is the plan's label, band included.
    for want in ["60 ±0.05", "4× ⌀6 H7 +0.012\n0 THRU"] {
        assert!(drawing.text.iter().any(|t| t.text == want), "{want}");
    }
    let pdf = drawing.pdf(&|_| {
        std::fs::read(Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .ok()
    });
    for bytes in [drawing.svg().into_bytes(), drawing.dxf(), pdf] {
        let back = model::embedded_spec(&bytes).unwrap().unwrap();
        assert_eq!(back, plan.spec);
        assert_eq!(
            back.dimensions["d:envelope/width.length"]
                .tolerance
                .as_deref(),
            Some("±0.05")
        );
        let again = model::plan(&a, &back).unwrap();
        assert_eq!(
            serde_json::to_string(&again.dimensions).unwrap(),
            serde_json::to_string(&plan.dimensions).unwrap()
        );
    }
    // Removing it takes it off again.
    let removed = model::edit(
        &edited,
        &json!([{ "op": "remove", "path": "dimensions/d:envelope~1width.length/tolerance" }]),
    )
    .unwrap();
    let plan = model::plan(&a, &removed).unwrap();
    assert_eq!(label(&plan, "d:envelope/width.length"), "60");
}

/// ISSUE-1154-a's tolerance clause: a toleranced boss height is not handed to an untoleranced
/// overall extent; a tolerance on the extent does not stop it taking an untoleranced height.
#[test]
fn a_toleranced_height_keeps_its_own_dimension() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let (_, mut a) = corpus::analyse(&dir, "mfcadpp/10127.step", "m10127");
    // Make the boss span the part's depth (y), flush with both faces, as dims_slots.rs does.
    let frame = a.recognition.as_ref().unwrap().frame;
    let [lo, hi] = a.envelope;
    let b = a.features.iter_mut().find(|f| f.kind == "boss").unwrap();
    let local: Vec<f64> = serde_json::from_value(b.parameters["axis"].clone()).unwrap();
    let mut top: Vec<f64> = serde_json::from_value(b.parameters["location"].clone()).unwrap();
    let dir3: Vec<f64> = (0..3)
        .map(|k| (0..3).map(|j| local[j] * frame[j + 1][k]).sum())
        .collect();
    let world_top: f64 = frame[0][1] + (0..3).map(|j| top[j] * frame[j + 1][1]).sum::<f64>();
    let target = if dir3[1] > 0.0 { hi[1] } else { lo[1] };
    let t = (target - world_top) / dir3[1];
    for j in 0..3 {
        top[j] += t * local[j];
    }
    b.parameters.insert("location".into(), top.into());
    b.parameters.insert("height".into(), (hi[1] - lo[1]).into());
    let id = "d:f:boss:1/boss_height.length";
    let heights = |plan: &Plan| {
        plan.dimensions
            .iter()
            .filter(|d| d.id.ends_with("/boss_height.length"))
            .map(|d| d.label.clone())
            .collect::<Vec<_>>()
    };
    assert!(heights(&model::plan(&a, &Spec::default()).unwrap()).is_empty());
    let toleranced = spec(json!({ "dimensions": { (id): { "tolerance": "±0.1" } } }));
    assert_eq!(
        heights(&model::plan(&a, &toleranced).unwrap()),
        ["32.268 ±0.1"]
    );
    let extent = spec(json!({
        "dimensions": { "d:envelope/depth.length": { "tolerance": "±0.1" } }
    }));
    let plan = model::plan(&a, &extent).unwrap();
    assert!(heights(&plan).is_empty());
    assert_eq!(label(&plan, "d:envelope/depth.length"), "32.268 ±0.1");
}

#[test]
fn a_generated_diameter_accepts_a_class_without_optional_deviations() {
    let (_, a) = analysed("plate");
    let p = model::plan(
        &a,
        &spec(json!({"dimensions":{"d:f:hole_pattern:1/bore.diameter":{"fit":"N9"}}})),
    )
    .unwrap();
    assert!(label(&p, "d:f:hole_pattern:1/bore.diameter").contains("N9"));
    assert!(
        !p.problems
            .iter()
            .any(|p| p.code == "fit_deviations_unavailable")
    );
    let contradictory = spec(
        json!({"dimensions":{"d:f:hole_pattern:1/bore.diameter":{"fit":"N9"}}, "features":{"f:hole:1":{"fit":"H7"}}}),
    );
    assert!(
        model::plan(&a, &contradictory)
            .unwrap_err()
            .message
            .contains("conflicting")
    );
    let unreadable = spec(json!({"features":{"f:hole:1":{"fit":"ſ9"}}}));
    assert!(model::plan(&a, &unreadable).is_err());
}
