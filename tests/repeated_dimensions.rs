//! Each quantity stated once (the maintainer's directive of 2026-10-09, `docs/quality-2026-10-09.md`),
//! on corpus parts read with Rust quiddity's recognition as the command line reads them:
//!
//! - a band of a turned part, were quiddity to report it both as a turned step and as a boss, is
//!   dimensioned by its step alone (draftwright's `boss_turned_step_owner`);
//! - parallel hole rows with the same stations show one pitch (draftwright's
//!   `_coalesce_aligned_linear_pitch_dims`);
//! - slots sharing a width across one span state it once, counted (`2× 6`, draftwright's shared
//!   slot width), and bosses sharing a height across one span likewise (`4× 38.1`, the same rule
//!   beyond draftwright, which repeats them);
//! - the merged item claims every member's measurement, so lint, which reads only the drawing's
//!   claims, still finds each feature's size stated.

#[path = "common/corpus.rs"]
mod corpus;

use std::path::Path;

use draftwright::dims::PlannedDimension;
use draftwright::lint::{self, Mark};
use draftwright::model::{self, Analysis, DimensionSpec, Plan, Spec};
use draftwright::output::Claim;

fn native(dir: &Path, file: &str) -> Analysis {
    model::analyse_native(&dir.join(file))
        .unwrap_or_else(|e| panic!("{file}: {e:?}"))
        .1
}

/// The plan's items as lint's marks: their labels and claims.
fn marks(plan: &Plan) -> Vec<Mark> {
    plan.dimensions
        .iter()
        .map(|d| Mark {
            precision: None,
            units: Default::default(),
            name: d.id.clone(),
            view: Some(d.view.clone()),
            text: d.label.clone(),
            claims: d
                .measurements
                .iter()
                .map(|m| Claim {
                    feature: m.feature.clone(),
                    parameter: m.parameter.clone(),
                })
                .collect(),
        })
        .collect()
}

fn parameter(d: &PlannedDimension) -> &str {
    d.measurements.first().map_or("", |m| m.parameter.as_str())
}

/// A linear item's drawn span along the axis it measures: its dimension line's ends on the page.
fn span(d: &PlannedDimension) -> Option<[f64; 2]> {
    let placed = d.placed.as_ref()?;
    let line = placed.lines.last()?;
    let k = usize::from(placed.rotation == 90.0);
    let (a, b) = (line[0][k], line[1][k]);
    Some([a.min(b), a.max(b)])
}

/// No two linear items in one view state the same parameter, with the same label, across the
/// same span: the same quantity drawn twice.
fn assert_each_quantity_once(name: &str, plan: &Plan) {
    let linear: Vec<&PlannedDimension> = plan
        .dimensions
        .iter()
        .filter(|d| d.kind == "linear")
        .collect();
    for (i, a) in linear.iter().enumerate() {
        for b in &linear[i + 1..] {
            let (Some(sa), Some(sb)) = (span(a), span(b)) else {
                continue;
            };
            let same = a.view == b.view
                && a.label == b.label
                && parameter(a) == parameter(b)
                && (sa[0] - sb[0]).abs() < 1e-3
                && (sa[1] - sb[1]).abs() < 1e-3;
            assert!(
                !same,
                "{name}: {} and {} state {:?} twice",
                a.id, b.id, a.label
            );
        }
    }
}

/// The item claiming *parameter* of *feature*.
fn claiming<'a>(plan: &'a Plan, feature: &str, parameter: &str) -> &'a PlannedDimension {
    let found: Vec<&PlannedDimension> = plan
        .dimensions
        .iter()
        .filter(|d| {
            d.measurements
                .iter()
                .any(|m| m.feature == feature && m.parameter == parameter)
        })
        .collect();
    assert_eq!(found.len(), 1, "{feature} {parameter}: {found:?}");
    found[0]
}

/// On a turned part a band is a turned step, and quiddity's aggregate rejects a boss on the same
/// faces (`reconcile_boss_turned_step_candidates`, ported in quiddity-rust a02b758). Should one
/// reach the analysis, the plan dimensions the band by the step's diameter and length alone, as
/// draftwright's sheets of the same parts do (grm05, string_post: no boss item), and lint asks no
/// boss height of it: each band is given a boss on its faces, as quiddity-rust reported them
/// before the reconciliation.
#[test]
fn a_turned_band_is_dimensioned_by_its_step_not_again_as_a_boss() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED (corpus part): the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    for file in [
        "gramel/GRM-05_depth_lock_bolt.step",
        "gramel/GRM-03_thumbwheel_drive_screw.step",
        "gramel/string_post.step",
        "nist/nist_ctc_05_asme1_rd.stp",
    ] {
        let mut a = native(&dir, file);
        assert!(
            a.features
                .iter()
                .all(|f| f.kind != "boss" || !lint::bosses::turned_band(&a, f)),
            "{file}: quiddity reports a band as a boss too"
        );
        let steps: Vec<_> = a
            .features
            .iter()
            .filter(|f| f.kind == "turned_step")
            .cloned()
            .collect();
        assert!(!steps.is_empty(), "{file}: no turned step");
        for (n, mut f) in steps.into_iter().enumerate() {
            // A boss record as quiddity-rust gave one: the band's diameter and height, its axis,
            // and its top on the axis (here taken through the origin; placement only).
            let size = |k: &str| f.parameters[k].as_f64().unwrap();
            let (lo, hi, diameter) = (size("lo"), size("hi"), size("diameter"));
            let axis = f.fingerprint.axis.expect("a turned step's axis");
            let location: Vec<f64> = axis.iter().map(|c| c * hi).collect();
            f.parameters = serde_json::json!({
                "axis": axis, "diameter": diameter, "height": hi - lo, "location": location,
            })
            .as_object()
            .unwrap()
            .clone();
            f.id = format!("f:boss:band{}", n + 1);
            f.kind = "boss".into();
            f.fingerprint.family = "boss".into();
            a.features.push(f);
        }
        let bands: Vec<&str> = a
            .features
            .iter()
            .filter(|f| f.kind == "boss" && lint::bosses::turned_band(&a, f))
            .map(|f| f.id.as_str())
            .collect();
        let plan = model::plan(&a, &Spec::default()).unwrap();
        for d in &plan.dimensions {
            assert!(
                d.measurements
                    .iter()
                    .all(|m| !bands.contains(&m.feature.as_str())),
                "{file}: {} states a band's boss record ({})",
                d.id,
                d.label
            );
        }
        for f in a.features.iter().filter(|f| f.kind == "turned_step") {
            for p in ["step.diameter", "step.length"] {
                let n = plan
                    .dimensions
                    .iter()
                    .filter(|d| {
                        d.measurements
                            .iter()
                            .any(|m| m.feature == f.id && m.parameter == p)
                    })
                    .count();
                assert!(n <= 1, "{file}: {} {p} is stated {n} times", f.id);
            }
        }
        let issues = lint::check(&a, &marks(&plan));
        assert!(
            issues.iter().all(|i| i.code != "boss_height_missing"),
            "{file}: {issues:?}"
        );
        assert_each_quantity_once(file, &plan);
    }
}

/// ctc02's two pairs of parallel rows (four 75 mm pitches, two 75 mm pitches and four 48 mm
/// pitches, each pair on the same stations along y) show one pitch per pair, claiming both
/// patterns', as draftwright's own ctc02 sheet does (`dim_pitch_plan1` `4× 75` and
/// `dim_pitch_plan3` `2× 75` each claim two patterns' pitch.length); the other moves to the
/// omitted list, conveyed by it. Rows on other stations keep their own (`2× 75` along x, twice,
/// as on draftwright's sheet).
#[test]
fn parallel_rows_on_the_same_stations_show_one_pitch() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED (corpus part): the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let a = native(&dir, "nist/nist_ctc_02_asme1_rc.stp");
    let plan = model::plan(&a, &Spec::default()).unwrap();
    for (one, other, label) in [
        ("f:hole_pattern:f13", "f:hole_pattern:f73", "4× 75"),
        ("f:hole_pattern:f41", "f:hole_pattern:f101", "2× 75"),
        // quiddity-rust 0.1.1 (accepted for alpha.13) no longer finds the 49.5 linear array
        // (alpha.8's f:hole_pattern:f510, faces 462…), and since #223 the bridge drops quiddity's
        // four uneven three-hole "bolt circles", so the 48 rows on faces 470… and 510… are
        // numbered 7 and 9 (were 9 and 11), and the row on 470…, first by ID, carries the pitch.
        ("f:hole_pattern:f470", "f:hole_pattern:f510", "4× 48"),
    ] {
        let d = claiming(&plan, one, "pitch.length");
        assert_eq!(d.label, label);
        assert_eq!(claiming(&plan, other, "pitch.length").id, d.id);
        let gone = format!("d:{other}/pitch.length");
        let o = plan
            .omitted
            .iter()
            .find(|o| o.id == gone)
            .unwrap_or_else(|| panic!("{gone} is omitted"));
        assert!(
            o.basis.starts_with(&format!("conveyed by {}", d.id)),
            "{o:?}"
        );
    }
    // Rows on different stations: each states its own pitch.
    let along_x: Vec<&str> = plan
        .dimensions
        .iter()
        .filter(|d| parameter(d) == "pitch.length" && d.label == "2× 75")
        .filter(|d| d.placed.as_ref().is_some_and(|p| p.rotation == 0.0))
        .map(|d| d.id.as_str())
        .collect();
    assert_eq!(along_x.len(), 2, "{along_x:?}");
    assert_each_quantity_once("ctc02", &plan);
}

/// cgb245's two ø6 slots run side by side across the same 6 mm span: one width, counted
/// (`2× 6`), claiming both, as draftwright's shared slot width prints it. The one split from the
/// slot callout states its end centres' distance apart, the other its own length.
#[test]
fn slots_sharing_a_width_across_one_span_state_it_once_counted() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED (corpus part): the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let mut a = native(&dir, "cadgenbench_inputs/cgb245.step");
    // The two are congruent, so one slot callout would state both: the second is split from
    // the callout, so each keeps its own size dimensions and the shared width is tested (the
    // split one states its end centres' distance apart in place of its length).
    corpus::split_slot(&mut a, "f:slot:f21");
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let d = claiming(&plan, "f:slot:f13", "slot_width.length");
    assert_eq!(d.label, "2× 6");
    assert_eq!(claiming(&plan, "f:slot:f21", "slot_width.length").id, d.id);
    let centres = claiming(&plan, "f:slot:f21", "slot_centres.length");
    assert_ne!(
        claiming(&plan, "f:slot:f13", "slot_length.length").id,
        centres.id
    );
    assert!(plan.dimensions.iter().all(|d| {
        d.measurements
            .iter()
            .all(|m| m.feature != "f:slot:f21" || m.parameter != "slot_length.length")
    }));
    let issues = lint::check(&a, &marks(&plan));
    // Lint judges each slot's low end where the planner puts it (the exact centre of its faces
    // less half its length), not quiddity's separately rounded `lo`: on quiddity-rust 0.1.1 the
    // two fall either side of a 0.005 mm tie (285.965 drawn, 95.96 recorded).
    // #319: the shared width locates neither slot across the part. Slot 1 and
    // slot 2 each lack their 50.356 transverse ordinate; the hole table's cells
    // name other features. Require those two genuine gaps, while continuing to
    // reject every missing size, end radius or longitudinal location above.
    let slot_findings: Vec<_> = issues
        .iter()
        .filter(|i| i.code.starts_with("slot_requirement_"))
        .map(|i| i.id.as_str())
        .collect();
    assert_eq!(
        slot_findings,
        [
            "l:slot_requirement_missing:f:slot:f13/location_slot_across.length",
            "l:slot_requirement_missing:f:slot:f21/location_slot_across.length",
        ],
        "{issues:?}"
    );
    assert_each_quantity_once("cgb245", &plan);
}

/// CTC-03's two identical obround slots stand side by side, their widths on different spans:
/// they are dimensioned as slots, one `2× SLOT 19.05 × 107.95` callout claiming both widths and
/// both lengths, so no size label repeats in the plan. Their ends are full-round (R9.52 recorded,
/// half the 19.05 width to quiddity's rounding), which the callout implies: no `2× R9.52`.
#[test]
fn identical_slots_share_one_width_counted() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED (corpus part): the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let a = native(&dir, "nist/nist_ctc_03_asme1_rc.stp");
    let plan = model::plan(&a, &Spec::default()).unwrap();
    for parameter in ["slot_width.length", "slot_length.length"] {
        let d = claiming(&plan, "f:slot:f62", parameter);
        assert_eq!(d.label, "2× SLOT 19.05 × 107.95");
        assert!(d.placed.is_some(), "{}", d.id);
        assert_eq!(claiming(&plan, "f:slot:f66", parameter).id, d.id);
    }
    assert!(
        plan.dimensions
            .iter()
            .flat_map(|d| &d.measurements)
            .all(|m| m.parameter != "slot_end_radius.radius")
    );
    let issues = lint::check(&a, &marks(&plan));
    assert!(
        !issues
            .iter()
            .any(|i| i.code.starts_with("slot_requirement_")
                && !i.id.ends_with("location_slot.length")),
        "{issues:?}"
    );
    assert_each_quantity_once("ctc03", &plan);
}

/// ftc06's four ø44.5 bosses stand 38.1 high from the base, and ftc07's four square bosses 2.5
/// proud of one face: each set's height is one dimension across its one span, counted, claiming
/// every boss (draftwright's sheets repeat it four times on the same span), and lint finds every
/// boss's height stated. Omitting the dimension that carries it promotes the next, counting the
/// rest.
#[test]
fn bosses_sharing_a_height_across_one_span_state_it_once_counted() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED (corpus part): the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    for (file, bosses, label) in [
        (
            "nist/nist_ftc_06_asme1_rd.stp",
            ["f:boss:f21", "f:boss:f33", "f:boss:f35", "f:boss:f37"],
            "4× 38.1",
        ),
        (
            "nist/nist_ftc_07_asme1_rd.stp",
            [
                "f:polygonal_boss:f235",
                "f:polygonal_boss:f237",
                "f:polygonal_boss:f240",
                "f:polygonal_boss:f241",
            ],
            "4× 2.54",
        ),
    ] {
        let a = native(&dir, file);
        let plan = model::plan(&a, &Spec::default()).unwrap();
        let d = claiming(&plan, bosses[0], "boss_height.length");
        assert_eq!(d.label, label, "{file}");
        for b in &bosses[1..] {
            assert_eq!(claiming(&plan, b, "boss_height.length").id, d.id, "{file}");
        }
        let issues = lint::check(&a, &marks(&plan));
        assert!(
            issues.iter().all(|i| i.code != "boss_height_missing"),
            "{file}: {issues:?}"
        );
        assert_each_quantity_once(file, &plan);

        let mut spec = Spec::default();
        spec.dimensions.insert(
            d.id.clone(),
            DimensionSpec {
                omit: true,
                ..Default::default()
            },
        );
        let without = model::plan(&a, &spec).unwrap();
        let next = claiming(&without, bosses[1], "boss_height.length");
        assert_eq!(next.label, label.replacen('4', "3", 1), "{file}");
        assert!(
            next.measurements.iter().all(|m| m.feature != bosses[0]),
            "{file}: the omitted boss's height is not claimed"
        );
    }
}

/// A location and a ladder rung can state the same distance even though their planners and
/// witnesses differ. FTC-10's hole row sits on the 12.5 mm face level: one line states both.
#[test]
fn a_location_and_a_face_level_on_one_span_are_one_statement() {
    let Some(dir) = corpus::dir() else { return };
    let a = native(&dir, "nist/nist_ftc_10_asme1_rb.stp");
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    let location = claiming(&plan, "f:hole_pattern:f200", "location.length.z");
    let rung = claiming(&plan, "step_level", "step_height.length.f165");
    assert_eq!(location.id, rung.id);
    assert_eq!(location.label, "12.5");
    assert!(plan.omitted.iter().any(|o| {
        o.id == "d:step_level/step_height.length.f165"
            && o.state
                == draftwright::items::State::Covered {
                    by: location.id.clone(),
                }
    }));
    assert_each_quantity_once("ftc10", &plan);
    let annotations = draftwright::dims::render(&plan.dimensions, &mut Vec::new(), &mut Vec::new());
    let drawn = annotations.iter().find(|a| a.name == location.id).unwrap();
    assert!(
        drawn
            .claims
            .iter()
            .any(|m| m.feature == "step_level" && m.parameter == "step_height.length.f165")
    );

    let mut spec = Spec::default();
    spec.dimensions.insert(
        location.id.clone(),
        DimensionSpec {
            omit: true,
            ..Default::default()
        },
    );
    let without = model::plan_recovered(&a, &spec).unwrap();
    let rung = claiming(&without, "step_level", "step_height.length.f165");
    assert_eq!(rung.id, "d:step_level/step_height.length.f165");
    assert!(
        rung.measurements
            .iter()
            .all(|m| m.feature != "f:hole_pattern:f200")
    );
}

#[test]
fn an_authored_location_and_rung_with_equal_bands_share_the_authored_survivor() {
    let Some(dir) = corpus::dir() else { return };
    let a = native(&dir, "nist/nist_ftc_10_asme1_rb.stp");
    let mut spec = Spec::default();
    for id in [
        "d:f:hole_pattern:f200/location.length.z",
        "d:step_level/step_height.length.f165",
    ] {
        spec.dimensions.insert(
            id.into(),
            DimensionSpec {
                tolerance: Some("±0.1".into()),
                ..Default::default()
            },
        );
    }
    let plan = model::plan_recovered(&a, &spec).unwrap();
    assert_eq!(
        claiming(&plan, "f:hole_pattern:f200", "location.length.z").id,
        claiming(&plan, "step_level", "step_height.length.f165").id
    );
    let d = claiming(&plan, "step_level", "step_height.length.f165");
    assert_eq!(d.label, "12.5 ±0.1");
    assert!(!d.default);
    // Different requirements must remain distinct statements, even at the same stations.
    spec.dimensions
        .get_mut("d:step_level/step_height.length.f165")
        .unwrap()
        .tolerance = Some("±0.2".into());
    let different = model::plan_recovered(&a, &spec).unwrap();
    assert_ne!(
        claiming(&different, "f:hole_pattern:f200", "location.length.z").id,
        claiming(&different, "step_level", "step_height.length.f165").id
    );
}

#[test]
fn a_counted_boss_height_also_states_its_face_level() {
    let Some(dir) = corpus::dir() else { return };
    let a = native(&dir, "nist/nist_ftc_06_asme1_rd.stp");
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    let size = claiming(&plan, "f:boss:f21", "boss_height.length");
    let level = claiming(&plan, "step_level", "step_height.length.f11");
    assert_eq!(size.id, level.id);
    assert_eq!(size.label, "4× 38.1");
    for b in ["f:boss:f33", "f:boss:f35", "f:boss:f37"] {
        assert_eq!(claiming(&plan, b, "boss_height.length").id, size.id);
    }
    assert_eq!(
        size.measurements.len(),
        5,
        "the level is a claim, not a fifth boss"
    );
    let issues = lint::check(&a, &marks(&plan));
    assert!(
        issues.iter().all(|i| i.code != "boss_height_missing"),
        "{issues:?}"
    );
}
