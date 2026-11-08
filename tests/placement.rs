//! Where the dimensions land, and what then goes wrong (review M3, ARCH0003-2).
//!
//! - **A ratchet** on the placement problems (`annotation_*`) of the port's own drawings of the
//!   captured corpus parts (the corpus's, with their features from
//!   `tests/fixtures/recognition/corpus/`, planned under the default spec): every part and code,
//!   with its count, is listed in `tests/fixtures/known_placement.json` with a verdict and the
//!   evidence. A new, changed or vanished entry fails, and the failure prints the entries to
//!   paste, each with the problems behind it, for a verdict. Set `PLACEMENT_REPORT=1` to print
//!   every placement problem.
//! - **Ink in its area:** every placed item's ink, as `dims::render` draws it (the dims layer of
//!   `model::render`), lies inside the area its view reserved, or the plan reports it
//!   (`annotation_outside_area`); and the side view's ink never runs past its bands outward.
//! - **Edit locality:** omitting one dimension, or adding one to an authored set, moves no other.
//!
//! Only the plan is drawn here: the dims layer is `dims::render` of the plan's dimensions alone,
//! so the part's hidden lines (minutes for connector109) add nothing to these checks.

#[path = "common/reference_geometry.rs"]
mod reference_geometry;

#[path = "common/corpus.rs"]
mod corpus;
#[path = "common/facts.rs"]
mod facts;

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use draftwright::api;
use draftwright::dims::{self, PlannedDimension};
use draftwright::model::{self, Analysis, DimensionSource, DimensionSpec, Plan, Spec};
use draftwright::output::{Curve, Item};
use serde::{Deserialize, Serialize};

/// Printed alternatives name a representative and all dependencies; intrinsic
/// ownership remains an exclusion even when its owner's dimension is omitted.
fn conveyed_owners(basis: &str) -> Vec<&str> {
    let Some((owner, why)) = basis
        .strip_prefix("conveyed by ")
        .and_then(|rest| rest.split_once(": "))
    else {
        return Vec::new();
    };
    if why.starts_with("recognised ownership") {
        return Vec::new();
    }
    why.split_once("; dependencies: ")
        .map_or_else(|| vec![owner], |(_, ids)| ids.split(", ").collect())
}

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Known {
    part: String,
    code: String,
    count: usize,
    /// `rust-correct`, `rust-wrong`, `equivalent`, `undetermined` or `not-applicable`.
    verdict: String,
    reason: String,
}

const VERDICTS: [&str; 5] = [
    "rust-correct",
    "rust-wrong",
    "equivalent",
    "undetermined",
    "not-applicable",
];

fn fixture(path: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(path)
}

/// The captured parts with corpus recognition, analysed: none when the corpus is missing.
fn corpus_parts() -> Vec<(String, Analysis)> {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED (corpus parts): the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return Vec::new();
    };
    let names: Vec<String> = facts::names()
        .into_iter()
        .filter(|n| corpus::recognition(n).is_some())
        .collect();
    std::thread::scope(|s| {
        let handles: Vec<_> = names
            .iter()
            .map(|name| {
                let dir = &dir;
                s.spawn(move || {
                    let f = facts::load(name);
                    (name.clone(), corpus::analyse(dir, &f.binding.file, name).1)
                })
            })
            .collect();
        handles.into_iter().map(|h| h.join().unwrap()).collect()
    })
}

/// The fixture parts with their recognition.
fn fixture_parts() -> Vec<(String, Analysis)> {
    ["plate", "turned", "prismatic", "two_grids", "boss_circle"]
        .iter()
        .map(|n| {
            let (part, mut a) = model::analyse(&fixture(&format!("parts/{n}.step"))).unwrap();
            let json = std::fs::read_to_string(fixture(&format!("recognition/{n}.json"))).unwrap();
            model::recognise(&part, &mut a, &json).unwrap();
            (n.to_string(), a)
        })
        .collect()
}

#[test]
fn placement_problems_are_known() {
    let parts = corpus_parts();
    if parts.is_empty() {
        return;
    }
    let known: Vec<Known> =
        serde_json::from_str(&std::fs::read_to_string(fixture("known_placement.json")).unwrap())
            .unwrap();
    let mut listed: BTreeMap<(String, String), &Known> = BTreeMap::new();
    for k in &known {
        assert!(
            VERDICTS.contains(&k.verdict.as_str()) && !k.reason.is_empty() && k.count > 0,
            "{k:?}"
        );
        assert!(
            listed.insert((k.part.clone(), k.code.clone()), k).is_none(),
            "{} {} is listed twice",
            k.part,
            k.code
        );
    }
    let report = std::env::var_os("PLACEMENT_REPORT").is_some();
    // Per part and code: the problems' messages.
    let mut found: BTreeMap<(String, String), Vec<String>> = BTreeMap::new();
    for (name, analysis) in &parts {
        let plan = model::plan(analysis, &Spec::default()).unwrap();
        for p in plan
            .problems
            .iter()
            .filter(|p| p.code.starts_with("annotation_"))
        {
            if report {
                eprintln!("{name}: {} {}", p.code, p.message);
            }
            found
                .entry((name.clone(), p.code.clone()))
                .or_default()
                .push(p.message.clone());
        }
    }
    let mut wrong = Vec::new();
    for ((part, code), messages) in &found {
        match listed.get(&(part.clone(), code.clone())) {
            Some(k) if k.count == messages.len() => {}
            k => wrong.push(format!(
                "{}\n  (listed: {}; the problems: {})",
                serde_json::to_string_pretty(&Known {
                    part: part.clone(),
                    code: code.clone(),
                    count: messages.len(),
                    verdict: "VERDICT".into(),
                    reason: k.map_or_else(|| "EVIDENCE".into(), |k| k.reason.clone()),
                })
                .unwrap(),
                k.map_or_else(|| "no".into(), |k| format!("count {}", k.count)),
                messages.join(" | ")
            )),
        }
    }
    for (key, k) in &listed {
        if !found.contains_key(key) {
            wrong.push(format!(
                "{} {} (count {}) is listed but no longer found: take it off",
                k.part, k.code, k.count
            ));
        }
    }
    eprintln!(
        "{} placement problems on {} parts, in {} part and code pairs",
        found.values().map(Vec::len).sum::<usize>(),
        parts.len(),
        found.len()
    );
    assert!(
        wrong.is_empty(),
        "placement problems differ from known_placement.json; give each entry below a verdict \
         (one of {VERDICTS:?}) and the evidence, and replace its entry:\n{}",
        wrong.join("\n")
    );
}

/// Every point of *d*'s drawn ink: its lines' ends, its arrowheads' and symbols' corners, and its
/// glyphs' outline poles (a Bézier lies in its poles' hull).
fn ink(d: &PlannedDimension) -> Vec<[f64; 2]> {
    let mut items = Vec::new();
    dims::render(std::slice::from_ref(d), &mut items, &mut Vec::new());
    let mut points = Vec::new();
    let mut curve = |c: &Curve| match c {
        Curve::Line(a, b) => points.extend([*a, *b]),
        Curve::Beziers(pieces) => points.extend(pieces.iter().flatten().copied()),
        other => panic!("{}: a dimension draws no {other:?}", d.id),
    };
    for item in &items {
        match item {
            Item::Edge(c) => curve(c),
            Item::Face(wires) => wires.iter().flatten().for_each(&mut curve),
        }
    }
    points
}

/// How far *p* lies outside *area* (zero or less inside).
fn past(area: [f64; 4], p: [f64; 2]) -> f64 {
    (area[0] - p[0])
        .max(p[0] - area[2])
        .max(area[1] - p[1])
        .max(p[1] - area[3])
}

fn reported(plan: &Plan, code: &str, id: &str) -> bool {
    plan.problems
        .iter()
        .any(|p| p.code == code && p.subjects.first().is_some_and(|s| s == id))
}

/// M3: placed ink lies in its view's area (the view's box and its bands) or is reported, and a
/// report means it does not; in the side view, whose bands come from its lanes, no ink runs past
/// them outward (a horizontal item's ink stays within the area's height, a vertical one's within
/// its width; only along its lane, as a callout's label or a short span's outside arrows, may it
/// run on).
#[test]
fn placed_ink_lies_in_its_views_area_or_is_reported() {
    let mut parts = fixture_parts();
    parts.extend(corpus_parts());
    let (mut checked, mut outside) = (0, 0);
    for (name, analysis) in &parts {
        let plan = model::plan(analysis, &Spec::default()).unwrap();
        for d in plan.dimensions.iter().filter(|d| d.placed.is_some()) {
            let v = plan.views.iter().find(|v| v.id == d.view).unwrap();
            let points = ink(d);
            assert!(!points.is_empty(), "{name}: {} draws nothing", d.id);
            let worst = points
                .iter()
                .map(|p| past(v.area, *p))
                .fold(f64::NEG_INFINITY, f64::max);
            checked += 1;
            let is_reported = reported(&plan, "annotation_outside_area", &d.id);
            assert_eq!(
                worst > 1e-6,
                is_reported,
                "{name}: {} reaches {worst:.3} mm past {}'s area {:?}, and is {}reported",
                d.id,
                d.view,
                v.area,
                if is_reported { "" } else { "not " }
            );
            outside += usize::from(is_reported);
            if d.view == "v:side" {
                let p = d.placed.as_ref().unwrap();
                let k = if p.rotation == 0.0 { 1 } else { 0 };
                for q in &points {
                    assert!(
                        q[k] >= v.area[k] - 1e-6 && q[k] <= v.area[k + 2] + 1e-6,
                        "{name}: {} runs past the side view's band at {q:?} (area {:?})",
                        d.id,
                        v.area
                    );
                }
            }
        }
    }
    eprintln!("{checked} placed items; {outside} reported past their view's area");
}

fn ids(plan: &Plan) -> Vec<&str> {
    plan.dimensions.iter().map(|d| d.id.as_str()).collect()
}

/// The correlated set (ARCH0015-2) a dimension belongs to: the step-height ladder, or the
/// ladder's shoulders along one axis, as `dims::correlated_set` reads its ID.
fn ladder_set<'a>(plan: &'a Plan, id: &'a str) -> Option<&'a str> {
    let id = plan
        .ids
        .iter()
        .find_map(|(old, new)| (new == id).then_some(old.as_str()))
        .unwrap_or(id);
    const HEIGHTS: &str = "d:step_level/step_height.length";
    if id.starts_with(HEIGHTS) {
        return Some(HEIGHTS);
    }
    let rest = id.strip_prefix("d:step_level/step_position.length.")?;
    rest.get(..1)
        .filter(|a| ["x", "y", "z"].contains(a))
        .map(|_| &id[..id.len() - rest.len() + 1])
}

/// The placed items by ID.
fn placed(plan: &Plan) -> BTreeMap<&str, &PlannedDimension> {
    plan.dimensions
        .iter()
        .filter(|d| d.placed.is_some())
        .map(|d| (d.id.as_str(), d))
        .collect()
}

/// ARCH0003-2 (edit locality, as R4 translates it: a small spec edit changes only the affected
/// plan items). Omitting a default or authored dimension leaves unrelated items, views, sheet
/// and ink as they were. A blanket radius note depends on its explicit exceptions; losing one
/// withdraws that note and restores the fillet leaders it conveyed. On plate.step this used to fail:
/// lanes were packed in order, so omitting the column pitch moved the x location and the bore
/// callout.
#[test]
fn omitting_one_dimension_moves_no_other() {
    let mut parts = fixture_parts();
    // Corpus parts with items in all three views, the side's among them.
    parts.extend(
        corpus_parts()
            .into_iter()
            .filter(|(n, _)| ["m10127", "hanger", "ftc06"].contains(&n.as_str())),
    );
    let mut edits = 0;
    let mut shared = 0;
    for (name, analysis) in &parts {
        let full = model::plan(analysis, &Spec::default()).unwrap();
        let all = placed(&full);
        let authored = |listed: &[&str]| Spec {
            dimension_source: DimensionSource::Authored,
            dimensions: listed
                .iter()
                .map(|id| (id.to_string(), DimensionSpec::default()))
                .collect(),
            ..Spec::default()
        };
        let every = ids(&full);
        let conveyed_by = |id: &str| -> Vec<&str> {
            full.omitted
                .iter()
                .filter(|o| conveyed_owners(&o.basis).contains(&id))
                .map(|o| o.id.as_str())
                .collect()
        };
        // The complete set: every item drawn and every item one of them conveys (one span, one
        // dimension: ARCH0009-6), as an author lists them; a shared size counts what is listed.
        let complete: Vec<&str> = every
            .iter()
            .copied()
            .chain(every.iter().flat_map(|i| conveyed_by(i)))
            .collect();
        assert_eq!(
            placed(&model::plan(analysis, &authored(&complete)).unwrap())
                .values()
                .map(|d| (&d.id, &d.placed))
                .collect::<Vec<_>>(),
            all.values().map(|d| (&d.id, &d.placed)).collect::<Vec<_>>(),
            "{name}: the authored complete set is placed as the defaults are"
        );
        for gone in &every {
            let mut spec = Spec::default();
            spec.dimensions.insert(
                gone.to_string(),
                DimensionSpec {
                    omit: true,
                    ..Default::default()
                },
            );
            let rest: Vec<&str> = every.iter().copied().filter(|i| i != gone).collect();
            // Omitting it brings back a coincident location it conveyed (ARCH0009-6), drawn in
            // its lane, which the item merged away shares (a size it shared among three or more
            // is shared again by the rest, the others conveyed by the one drawn); one left out of
            // an authored set, with what it conveyed, conveys nothing back.
            let conveyed = conveyed_by(gone);
            let listed: Vec<&str> = complete
                .iter()
                .copied()
                .filter(|i| i != gone && !conveyed.contains(i))
                .collect();
            for (how, spec, promoted) in [
                ("omitted", spec, conveyed.clone()),
                ("left out of the authored set", authored(&listed), vec![]),
            ] {
                let plan = model::plan(analysis, &spec).unwrap();
                let mut promoted = promoted.clone();
                // A correlated set (ARCH0015-2) is approved or omitted whole: in this test it is
                // one item. Omitting a rung omits the rest of its ladder (each named by a
                // correlated_set_whole problem); leaving one out of an authored set that lists
                // the others draws it as before. Every item outside the set stays fixed.
                let siblings: Vec<&str> = every
                    .iter()
                    .copied()
                    .filter(|i| {
                        i != gone
                            && ladder_set(&full, gone)
                                .is_some_and(|s| ladder_set(&full, i) == Some(s))
                    })
                    .collect();
                let whole = |id: &str| {
                    plan.problems.iter().any(|p| {
                        p.code == "correlated_set_whole" && p.subjects.iter().any(|s| s == id)
                    })
                };
                let rest: Vec<&str> = if siblings.is_empty() {
                    rest.clone()
                } else if how == "omitted" {
                    for s in &siblings {
                        assert!(
                            plan.omitted.iter().any(|o| o.id == *s
                                && o.state == draftwright::items::State::OmittedByUser)
                                && whole(s),
                            "{name}: {gone} {how}: {s} is neither drawn nor omitted with its set"
                        );
                    }
                    // What an omitted sibling conveyed comes back, as for the item omitted.
                    for s in &siblings {
                        promoted.extend(conveyed_by(s));
                    }
                    rest.iter()
                        .copied()
                        .filter(|i| !siblings.contains(i))
                        .collect()
                } else {
                    assert!(
                        whole(gone),
                        "{name}: {gone} {how}: its set does not approve it"
                    );
                    every.clone()
                };
                // A blanket radius default depends on its exceptions. Withdrawing it
                // restores the fillet leaders it conveyed; all unrelated ink stays fixed.
                for o in &full.omitted {
                    let draftwright::items::State::Covered { by } = &o.state else {
                        continue;
                    };
                    if !by.starts_with("n:")
                        || !plan.notes.iter().any(|n| {
                            n.id == *by
                                && matches!(n.state, draftwright::items::State::NotDrawable { .. })
                        })
                    {
                        continue;
                    }
                    let Some(d) = plan
                        .dimensions
                        .iter()
                        .find(|d| d.id == o.id && d.placed.is_some())
                    else {
                        continue;
                    };
                    assert!(
                        full.dimensions
                            .iter()
                            .find(|d| d.id == *gone)
                            .unwrap()
                            .measurements
                            .iter()
                            .any(|m| matches!(
                                m.parameter.as_str(),
                                "blend.radius" | "fillet.radius"
                            )),
                        "only a radius exception can withdraw a radius default"
                    );
                    assert!(reported(&plan, "notes_not_placed", by));
                    assert_eq!(d.label, o.label);
                    let hidden = model::edit(&Spec::default(), &serde_json::json!([{
                        "op":"set", "path":format!("annotations/{}/omit", draftwright::items::key(by)), "value":true
                    }])).unwrap();
                    let hidden = model::plan(analysis, &hidden).unwrap();
                    assert_eq!(
                        d.placed,
                        hidden
                            .dimensions
                            .iter()
                            .find(|d| d.id == o.id)
                            .unwrap()
                            .placed,
                        "restored radius uses its established geometry"
                    );
                    promoted.push(o.id.as_str());
                }
                edits += 1;
                // Fill describes the remaining drawn items, not placement stability.
                let sheet_geometry = |sheet: &draftwright::model::PlannedSheet| {
                    let mut value = serde_json::to_value(sheet).unwrap();
                    value.as_object_mut().unwrap().remove("fill");
                    value["basis"] = serde_json::Value::String(
                        sheet
                            .basis
                            .split("; drawn-item fill ")
                            .next()
                            .unwrap()
                            .into(),
                    );
                    value
                };
                assert_eq!(
                    sheet_geometry(&plan.sheet),
                    sheet_geometry(&full.sheet),
                    "{name}: {gone} {how}"
                );
                for old in full
                    .views
                    .iter()
                    .filter(|v| !plan.views.iter().any(|n| n.id == v.id))
                {
                    let d = old
                        .detail
                        .as_ref()
                        .expect("only optional recovery furniture may disappear");
                    assert!(
                        !d.authored
                            && d.dimensions
                                .iter()
                                .all(|id| plan.dimensions.iter().all(|dim| dim.id != *id))
                    );
                }
                assert_eq!(
                    plan.views
                        .iter()
                        .map(|v| (&v.id, v.at, v.area))
                        .collect::<Vec<_>>(),
                    full.views
                        .iter()
                        .filter(|v| plan.views.iter().any(|n| n.id == v.id))
                        .map(|v| (&v.id, v.at, v.area))
                        .collect::<Vec<_>>(),
                    "{name}: {gone} {how}"
                );
                let back: Vec<&str> = ids(&plan)
                    .into_iter()
                    .filter(|i| promoted.contains(i))
                    .collect();
                assert_eq!(back.is_empty(), promoted.is_empty(), "{name}: {gone} {how}");
                for p in promoted.iter().filter(|p| !back.contains(p)) {
                    assert!(
                        plan.omitted.iter().any(|o| o.id == *p
                            && back
                                .iter()
                                .any(|b| o.basis.starts_with(&format!("conveyed by {b}: ")))),
                        "{name}: {gone} {how}: {p} is neither drawn nor conveyed by what is"
                    );
                }
                assert_eq!(
                    ids(&plan)
                        .into_iter()
                        .filter(|i| !promoted.contains(i))
                        .collect::<Vec<_>>(),
                    rest,
                    "{name}: {gone} {how}"
                );
                for d in &plan.dimensions {
                    if promoted.contains(&d.id.as_str()) {
                        let Some(p) = d.placed.as_ref() else {
                            panic!("{name}: {} is not drawn", d.id);
                        };
                        // A previously auxiliary interval becomes controlling when any
                        // determining dimension is withdrawn. It states its own span; the
                        // unrelated items below still retain their exact established ink.
                        if full.omitted.iter().any(|o| {
                            o.id == d.id
                                && o.basis
                                    .contains("other stated dimensions determine this interval")
                        }) {
                            assert!(!d.reference, "{name}: {} stayed reference", d.id);
                            let view = plan.views.iter().find(|v| v.id == d.view).unwrap();
                            let (a, b) = (p.arrows[0][0], p.arrows[1][0]);
                            assert!(
                                ((a[0] - b[0]).hypot(a[1] - b[1]) - d.value * view.scale).abs()
                                    < 1e-6,
                                "{name}: {} does not state its promoted interval",
                                d.id
                            );
                            continue;
                        }
                        // In the lane of the item it was merged into: its dimension line on that
                        // item's (the merged items hold no lane of their own).
                        let lane = |p: &dims::Placed| {
                            let [a, b] = p.lines[2];
                            if a[0] == b[0] { a[0] } else { a[1] }
                        };
                        // Its survivor: the item it was conveyed by (the omitted one, or with a
                        // correlated set, the sibling omitted with it).
                        let by = full
                            .omitted
                            .iter()
                            .find(|o| o.id == d.id)
                            .and_then(|o| {
                                conveyed_owners(&o.basis)
                                    .into_iter()
                                    .find(|b| b == gone || siblings.contains(b))
                            })
                            .unwrap_or(gone);
                        if let (true, Some(survivor)) = (d.kind == "linear", all.get(by)) {
                            // An underside derived from a slab and the overall is not a
                            // coincident span. Restore its own front datum-to-level span;
                            // the existing checks still hold every unrelated item fixed.
                            let slab = full
                                .omitted
                                .iter()
                                .find(|o| o.id == d.id)
                                .and_then(|o| match &o.state {
                                    draftwright::items::State::Covered { by } => {
                                        all.get(by.as_str())
                                    }
                                    _ => None,
                                })
                                .and_then(|owner| {
                                    owner
                                        .measurements
                                        .iter()
                                        .find(|m| m.parameter == "thickness.length")
                                })
                                .and_then(|m| analysis.features.iter().find(|f| f.id == m.feature));
                            if let Some(slab) = slab
                                && d.measurements.iter().any(|m| {
                                    m.feature == "step_level"
                                        && m.parameter.starts_with("step_height.length.f")
                                })
                            {
                                let slab = draftwright::lint::plates::read(analysis, slab).unwrap();
                                assert_eq!(slab.axis, 2);
                                assert!((slab.span[1][2] - analysis.envelope[1][2]).abs() <= 1e-3);
                                assert!(
                                    (d.value - (slab.span[0][2] - analysis.envelope[0][2])).abs()
                                        <= 1e-3
                                );
                                assert_eq!(d.view, "v:front");
                                let view = plan.views.iter().find(|v| v.id == d.view).unwrap();
                                let (a, b) = (p.arrows[0][0], p.arrows[1][0]);
                                assert!(
                                    ((a[0] - b[0]).hypot(a[1] - b[1]) - d.value * view.scale).abs()
                                        < 1e-6
                                );
                                continue;
                            }
                            // Counted walls may lie on different axes. On promotion the
                            // remaining wall must state its own interval in an edge-on view.
                            if d.measurements
                                .iter()
                                .any(|m| m.parameter == "thickness.length")
                            {
                                let m = d
                                    .measurements
                                    .iter()
                                    .find(|m| m.parameter == "thickness.length")
                                    .unwrap();
                                let feature = analysis
                                    .features
                                    .iter()
                                    .find(|f| f.id == m.feature)
                                    .unwrap();
                                let wall =
                                    draftwright::lint::plates::read(analysis, feature).unwrap();
                                let view = plan.views.iter().find(|v| v.id == d.view).unwrap();
                                let (a, b) = (p.arrows[0][0], p.arrows[1][0]);
                                assert!(
                                    ((a[0] - b[0]).hypot(a[1] - b[1])
                                        - wall.thickness() * view.scale)
                                        .abs()
                                        < 1e-6,
                                    "{name}: {} does not state its promoted wall",
                                    d.id
                                );
                                if survivor
                                    .measurements
                                    .iter()
                                    .any(|old| old.parameter == "thickness.length")
                                {
                                    assert!(
                                        survivor
                                            .measurements
                                            .iter()
                                            .any(|old| old.feature == m.feature
                                                && old.parameter == m.parameter
                                                && old.value == m.value)
                                    );
                                }
                                continue;
                            }
                            // An item retried inside its view (its own exterior witness
                            // crossed another feature) holds no exterior lane to hand on: the
                            // promoted item is placed on its own merits, outside.
                            if survivor.region == Some(dims::CandidateRegion::Interior) {
                                continue;
                            }
                            // Nor does a callout in another view that conveyed it (ADR1-8: a
                            // boss height a hole callout's depth states): the promoted item is
                            // placed in its own view, outside.
                            if survivor.kind != "linear"
                                && survivor.view != d.view
                                && conveyed_by(by).contains(&d.id.as_str())
                            {
                                continue;
                            }
                            let survivor = survivor.placed.as_ref().unwrap();
                            assert!(
                                (lane(p) - lane(survivor)).abs() < 1e-9,
                                "{name}: {} is not drawn in {gone}'s lane",
                                d.id
                            );
                            shared += 1;
                        }
                        continue;
                    }
                    let was = full.dimensions.iter().find(|e| e.id == d.id).unwrap();
                    if full.omitted.iter().any(|o| {
                        back.contains(&o.id.as_str())
                            && o.basis
                                .contains("other stated dimensions determine this interval")
                            && conveyed_owners(&o.basis).contains(&d.id.as_str())
                    }) {
                        // Restoring an auxiliary interval may reflow the participating
                        // chain's text. Its measured supports and arrows remain fixed.
                        assert_eq!(
                            (d.view.as_str(), d.value, d.label.as_str()),
                            (was.view.as_str(), was.value, was.label.as_str())
                        );
                        let (now, old) = (d.placed.as_ref().unwrap(), was.placed.as_ref().unwrap());
                        assert_eq!(&now.lines[..2], &old.lines[..2], "{} witnesses", d.id);
                        assert_eq!(now.arrows, old.arrows, "{} arrows", d.id);
                        continue;
                    }
                    // ADR1-8: an item conveyed across views (m10127's boss height, by the hole
                    // callout down its axis) comes back in its own view, where the omitted owner
                    // held no lane for it; a leader callout there may re-route around its ink.
                    // Its view and text stay, and every linear item stays fixed.
                    let owner_view = full
                        .dimensions
                        .iter()
                        .find(|e| e.id == *gone)
                        .map(|e| &e.view);
                    if d.kind != "linear"
                        && owner_view.is_some_and(|v| *v != d.view)
                        && plan
                            .dimensions
                            .iter()
                            .any(|e| promoted.contains(&e.id.as_str()) && e.view == d.view)
                    {
                        assert_eq!(
                            (d.view.as_str(), d.label.as_str()),
                            (was.view.as_str(), was.label.as_str())
                        );
                        continue;
                    }
                    reference_geometry::assert_local(was, d);
                }
            }
        }
    }
    eprintln!(
        "{edits} single-dimension edits, none moving another item; {shared} items brought back in \
         their survivor's lane"
    );
    assert!(shared > 0, "no item merged away was brought back");
}

/// A merge the spec's labels break draws both items, each in a lane of its own. The defaults
/// merge one span's items into one dimension (ARCH0009-6) and the merged items share their
/// survivor's lane (`dims::placement::owners`); a tolerance on the survivor makes the labels
/// differ, so the plan draws both, and the one merged away moves to its strip's next free lane
/// (`dims::placement::separate`). On ctc01 a tolerance on the hole pattern's x location used to
/// draw the hex boss's x location (400) on the same three lines as the pattern's (400 ±0.05), the
/// labels overlapping. Every other item stays where it was.
#[test]
fn a_merge_the_spec_breaks_draws_both_in_their_own_lanes() {
    let mut parts = fixture_parts();
    parts.extend(
        corpus_parts()
            .into_iter()
            .filter(|(n, _)| ["ctc01", "ftc06", "m10127", "hanger"].contains(&n.as_str())),
    );
    let mut broken = 0;
    for (name, analysis) in &parts {
        let full = model::plan(analysis, &Spec::default()).unwrap();
        let survivors: Vec<&str> = full
            .omitted
            .iter()
            .flat_map(|o| conveyed_owners(&o.basis))
            .collect();
        for survivor in survivors {
            if let Some(note) = full.notes.iter().find(|n| n.id == survivor) {
                assert!(note.placed.is_some());
                let covered: Vec<_> = full.omitted.iter().filter(|d| {
                    matches!(&d.state, draftwright::items::State::Covered { by } if by == survivor)
                }).collect();
                assert!(!covered.is_empty());
                assert!(!note.measurements.is_empty());
                // A note is not a linear merge whose tolerance separates two dimension lanes.
                continue;
            }
            let mut spec = Spec::default();
            spec.dimensions.insert(
                survivor.to_string(),
                DimensionSpec {
                    tolerance: Some("±0.05".into()),
                    ..Default::default()
                },
            );
            let plan = model::plan(analysis, &spec).unwrap();
            let back: Vec<&PlannedDimension> = plan
                .dimensions
                .iter()
                .filter(|d| full.omitted.iter().any(|o| o.id == d.id))
                .collect();
            if back.is_empty() {
                continue;
            }
            broken += 1;
            // No two drawn linear items in one view share a dimension line.
            let linear: Vec<(&str, &str, [[f64; 2]; 2])> = plan
                .dimensions
                .iter()
                .filter(|d| d.kind == "linear")
                .filter_map(|d| Some((d.id.as_str(), d.view.as_str(), d.placed.as_ref()?.lines[2])))
                .collect();
            for (k, a) in linear.iter().enumerate() {
                for b in &linear[k + 1..] {
                    assert!(
                        a.1 != b.1 || a.2 != b.2,
                        "{name}: tolerance on {survivor}: {} and {} share a dimension line",
                        a.0,
                        b.0
                    );
                }
            }
            for d in &plan.dimensions {
                if d.id == survivor || back.iter().any(|b| b.id == d.id) {
                    continue;
                }
                let was = full.dimensions.iter().find(|e| e.id == d.id).unwrap();
                assert_eq!(
                    d.placed, was.placed,
                    "{name}: {} moved when {survivor} took a tolerance",
                    d.id
                );
            }
        }
    }
    eprintln!("{broken} merges broken by a tolerance, each drawn in two lanes");
    assert!(broken > 0, "no merge was broken");
}

/// Each view's drawn box on the page: a principal view's part box (the envelope projected, at
/// its scale, centred at its position), the isometric's projected bounds.
fn view_boxes(plan: &Plan, analysis: &Analysis) -> Vec<(String, [f64; 4])> {
    plan.views
        .iter()
        .map(|v| {
            let b = if v.id == "v:iso" {
                let ib = analysis.iso_bounds;
                [
                    v.at[0] + v.scale * ib[0],
                    v.at[1] + v.scale * ib[1],
                    v.at[0] + v.scale * ib[2],
                    v.at[1] + v.scale * ib[3],
                ]
            } else {
                let view = haecceity::hlr::View::new(v.direction, v.up).unwrap();
                let [lo, hi] = v.detail.as_ref().map_or(analysis.envelope, |d| d.bounds);
                let (mut min, mut max) = ([f64::INFINITY; 2], [f64::NEG_INFINITY; 2]);
                for i in 0..8 {
                    let corner = [0, 1, 2].map(|k| if i >> k & 1 == 0 { lo[k] } else { hi[k] });
                    let p = view.map(corner);
                    for k in 0..2 {
                        min[k] = min[k].min(p[k]);
                        max[k] = max[k].max(p[k]);
                    }
                }
                let half = [0.5 * (max[0] - min[0]), 0.5 * (max[1] - min[1])];
                [
                    v.at[0] - v.scale * half[0],
                    v.at[1] - v.scale * half[1],
                    v.at[0] + v.scale * half[0],
                    v.at[1] + v.scale * half[1],
                ]
            };
            (v.id.clone(), b)
        })
        .collect()
}

/// Whether segment *s* passes through the open box *b* (Liang–Barsky).
fn crosses(s: [[f64; 2]; 2], b: [f64; 4]) -> bool {
    let (p, d) = (s[0], [s[1][0] - s[0][0], s[1][1] - s[0][1]]);
    let (mut t0, mut t1) = (0.0f64, 1.0f64);
    for (num, den) in [
        (p[0] - b[0], -d[0]),
        (b[2] - p[0], d[0]),
        (p[1] - b[1], -d[1]),
        (b[3] - p[1], d[1]),
    ] {
        if den == 0.0 {
            if num <= 0.0 {
                return false;
            }
        } else if den < 0.0 {
            t0 = t0.max(num / den);
        } else {
            t1 = t1.min(num / den);
        }
    }
    t1 - t0 > 1e-9
}

fn reported_pair(plan: &Plan, code: &str, id: &str, view: &str) -> bool {
    plan.problems
        .iter()
        .any(|p| p.code == code && p.subjects == [id.to_string(), view.to_string()])
}

/// ISSUE-1215-e (c) and review M3: whatever placement cannot fit stays on the plan and is
/// reported, never dropped or clipped. On every part, each placed item whose ink leaves the
/// drawing area is an `annotation_off_sheet` error, and each line of it that runs across a view
/// not its own (a leader to the isometric, a ladder rung through the side view) is an
/// `annotation_line_over_view` naming that view; and every item the plan keeps is placed.
#[test]
fn what_does_not_fit_the_page_is_reported() {
    let mut parts = fixture_parts();
    parts.extend(corpus_parts());
    let mut crossings = 0;
    for (name, analysis) in &parts {
        let plan = model::plan(analysis, &Spec::default()).unwrap();
        let m = draftwright::layout::margin();
        let [w, h] = plan.sheet.page;
        let boxes = view_boxes(&plan, analysis);
        for d in &plan.dimensions {
            let Some(p) = d.placed.as_ref() else {
                let draftwright::items::State::NotDrawable { reason_code } = &d.state else {
                    panic!("{name}: {} is unplaced without a not-drawable state", d.id);
                };
                assert!(
                    reported(&plan, reason_code, &d.id),
                    "{name}: {} is neither placed nor reported for {reason_code}",
                    d.id
                );
                continue;
            };
            let b = p.label_box;
            let off = |q: &[f64; 2]| {
                q[0] < m - 1e-9 || q[1] < m - 1e-9 || q[0] > w - m + 1e-9 || q[1] > h - m + 1e-9
            };
            if off(&[b[0], b[1]]) || off(&[b[2], b[3]]) || p.lines.iter().flatten().any(off) {
                assert!(
                    reported(&plan, "annotation_off_sheet", &d.id),
                    "{name}: {} runs off the sheet unreported",
                    d.id
                );
            }
            for (view, vb) in &boxes {
                if *view != d.view && p.lines.iter().any(|l| crosses(*l, *vb)) {
                    crossings += 1;
                    assert!(
                        reported_pair(&plan, "annotation_line_over_view", &d.id, view),
                        "{name}: a line of {} runs across {view} unreported",
                        d.id
                    );
                }
            }
        }
    }
    eprintln!("{crossings} lines across another view, each reported");
}

/// cgb245 (CLI trial, 2026-10-08), with its corpus features.
fn cgb245() -> Option<(Analysis, Plan)> {
    let (_, analysis) = corpus_parts().into_iter().find(|(n, _)| n == "cgb245")?;
    let plan = model::plan(&analysis, &Spec::default()).unwrap();
    Some((analysis, plan))
}

/// ISSUE-1215-e (a): a strip with no room for its lanes passes them to the strip across the
/// view. On cgb245 (A2 at 1:2) the band left of the plan holds two lanes before the sheet's
/// margin, where its nine vertical dimensions used to stack off the sheet (10
/// `annotation_off_sheet` errors). The y locations that do not fit there now stand right of the
/// plan, on the sheet and clear of the isometric: all of them since the layout keeps the
/// isometric off the plan's right band (drawing quality 3; hole:1's used to fit on neither side,
/// its next lane on the right crossing the isometric, and stayed off the sheet).
#[test]
fn a_strip_without_room_falls_through_to_the_opposite_strip() {
    let Some((analysis, plan)) = cgb245() else {
        return;
    };
    let boxes = view_boxes(&plan, &analysis);
    let outline = boxes.iter().find(|(v, _)| v == "v:plan").unwrap().1;
    let off: Vec<&str> = plan
        .problems
        .iter()
        .filter(|p| p.code == "annotation_off_sheet")
        .map(|p| p.subjects[0].as_str())
        .collect();
    assert!(off.is_empty(), "{off:?}");
    let mut moved = 0;
    for d in &plan.dimensions {
        if d.view != "v:plan" || !d.id.contains("location.length.y") || off.contains(&d.id.as_str())
        {
            continue;
        }
        let p = d.placed.as_ref().unwrap();
        if p.label_box[0] > outline[2] {
            moved += 1;
            assert!(
                !reported(&plan, "annotation_off_sheet", &d.id)
                    && !plan
                        .problems
                        .iter()
                        .any(|q| q.subjects.first() == Some(&d.id)
                            && q.code == "annotation_line_over_view"),
                "{} fell through but does not fit",
                d.id
            );
        }
    }
    assert!(moved >= 6, "{moved} y locations fell through to the right");
}

/// ISSUE-1215-e: a starved overall extent takes the opposite strip's first free lane.
/// cgb245 at A4 1:2 still starves. The improved named-pair arrangement fits m1000's
/// A3 5:1 height ladder on the right; its overall extent must remain outside every rung.
#[test]
fn a_starved_overall_extent_has_first_claim_on_the_opposite_strip() {
    let parts = corpus_parts();
    let mut checked = 0;
    for (name, analysis) in parts.iter().filter(|(n, _)| n == "cgb245" || n == "m1000") {
        let sheet = if name == "cgb245" {
            serde_json::json!({ "size": "A4", "scale": "1:2" })
        } else {
            serde_json::json!({ "size": "A3", "scale": "5:1" })
        };
        let spec: Spec = serde_json::from_value(serde_json::json!({ "sheet": sheet })).unwrap();
        let plan = model::plan(analysis, &spec).unwrap();
        let boxes = view_boxes(&plan, analysis);
        let front = boxes.iter().find(|(v, _)| v == "v:front").unwrap().1;
        let id = "d:envelope/height.length";
        let d = plan.dimensions.iter().find(|d| d.id == id).unwrap();
        let p = d.placed.as_ref().unwrap();
        if name == "cgb245" {
            assert!(
                p.label_box[2] < front[0],
                "{name}: {id}'s label {:?} left of the front {front:?}",
                p.label_box
            );
        } else {
            assert!(p.label_box[0] > front[2]);
            let rungs: Vec<_> = plan
                .dimensions
                .iter()
                .filter(|d| {
                    d.id != id
                        && d.view == "v:front"
                        && d.placed.as_ref().is_some_and(|q| q.rotation == 90.0)
                })
                .collect();
            assert!(rungs.len() >= 4, "{name}: missing height ladder");
            for rung in rungs {
                assert!(
                    rung.placed.as_ref().unwrap().label_box[2] < p.label_box[0],
                    "{name}: {} is outside the overall extent",
                    rung.id
                );
            }
        }
        // The required precision-limit information is independent of strip placement.
        // Keep its severity and both-value wording checked; every placement finding still fails.
        for q in plan
            .problems
            .iter()
            .filter(|q| q.subjects.first().is_some_and(|s| s == id) && q.code == "nominal_rounded")
        {
            assert_eq!(q.severity, "info");
            assert!(
                q.message.contains(&d.label)
                    && q.message.contains("modelled nominal")
                    && q.message.contains("but prints")
            );
        }
        let against: Vec<&str> = plan
            .problems
            .iter()
            .filter(|q| q.subjects.first().is_some_and(|s| s == id) && q.code != "nominal_rounded")
            .map(|q| q.code.as_str())
            .collect();
        assert!(against.is_empty(), "{name}: {id} reported {against:?}");
        checked += 1;
    }
    assert!(parts.is_empty() || checked == 2, "{checked} parts checked");
}

/// Leaders stay off other views (CLI trial: on cgb245 the plan's callouts stacked above the
/// ladder, their 45° leaders running across the sheet towards the isometric). A callout whose
/// lane does not fit the page goes out to the side of its view as draftwright's plan callouts
/// do: the 2×2 pattern's callout now stands right of the plan, within its height, its leader
/// crossing no other view.
#[test]
fn a_callout_that_does_not_fit_its_lane_goes_beside_its_view() {
    let Some((analysis, plan)) = cgb245() else {
        return;
    };
    let boxes = view_boxes(&plan, &analysis);
    let outline = boxes.iter().find(|(v, _)| v == "v:plan").unwrap().1;
    let d = plan
        .dimensions
        .iter()
        .find(|d| d.id == "d:f:hole_pattern:f17/bore.diameter")
        .unwrap();
    let p = d.placed.as_ref().unwrap();
    let b = p.label_box;
    assert!(
        b[0] > outline[2] && b[1] >= outline[1] && b[3] <= outline[3],
        "label {b:?} beside the plan {outline:?}"
    );
    for (view, vb) in &boxes {
        if view != "v:plan" {
            assert!(!p.lines.iter().any(|l| crosses(*l, *vb)), "crosses {view}");
        }
    }
}

/// What fits on neither side shares a lane already held (draftwright's lateral tier reuse): on
/// ftc09 (Rust quiddity's recognition, the command line's default, through the recovery) the two
/// slot locations that ran off the sheet, past every lane their strips hold (below the front),
/// now chain with their own slot's length, end to end on its dimension line, as draftwright's
/// sheets stand each slot position in its slot length's column; nothing is off the sheet.
/// ftc08's slot 4 location (plan) was a third case until overflowing strips were packed before
/// falling through (2026-10-25): its strip now holds it in a lane of its own, on the sheet.
#[test]
fn a_location_that_fits_on_neither_side_shares_a_held_lane() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found");
        return;
    };
    for (file, slots) in [
        // Slot n in a view.
        // Nothing off the sheet; no stranded slot location left to chain.
        ("nist/nist_ftc_08_asme1_rc.stp", &[][..]),
        (
            "nist/nist_ftc_09_asme1_rd.stp",
            &["f78@front", "f86@front"][..],
        ),
    ] {
        let (_, mut analysis) = model::analyse_native(&dir.join(file)).unwrap();
        if !slots.is_empty() {
            // FTC-09's slots 1 and 2 are congruent, so one slot callout would state them and
            // each would be located by its centre: slot 2 is split from the callout, so slot 1
            // keeps the low-end location and length whose lanes this test reads.
            corpus::split_slot(&mut analysis, "f:slot:f82");
        }
        let plan = api::plan_analysed(
            &analysis,
            &Spec {
                hole_table: draftwright::hole_table::Setting::Never,
                // Exercise the existing lateral-tier mechanism on its original
                // one-page paper; automatic whole-view allocation can instead
                // give these locations independent room on another page.
                sheet: model::SheetSpec {
                    size: Some("A1".into()),
                    ..Default::default()
                },
                ..Spec::default()
            },
        )
        .unwrap();
        let off: Vec<&str> = plan
            .problems
            .iter()
            .filter(|p| p.code == "annotation_off_sheet")
            .map(|p| p.subjects[0].as_str())
            .collect();
        assert!(off.is_empty(), "{file}: {off:?}");
        for slot in slots {
            let (n, view) = slot.split_once('@').unwrap();
            let line = |param: &str| {
                let id = format!("d:f:slot:{n}/{param}.length");
                let d = plan
                    .dimensions
                    .iter()
                    .find(|d| d.id == id && d.view == format!("v:{view}"));
                let p = d.and_then(|d| d.placed.as_ref());
                p.unwrap_or_else(|| panic!("{file}: {id} placed")).lines[2]
            };
            let (loc, len) = (line("location_slot"), line("slot_length"));
            // Along the line: x for a horizontal dimension, y for a vertical one.
            let k = usize::from(loc[0][0] == loc[1][0]);
            let span = |l: [[f64; 2]; 2]| (l[0][k].min(l[1][k]), l[0][k].max(l[1][k]));
            let ((a0, a1), (b0, b1)) = (span(loc), span(len));
            assert!(
                (loc[0][1 - k] - len[0][1 - k]).abs() < 1e-9
                    && ((a1 - b0).abs() < 1e-6 || (b1 - a0).abs() < 1e-6),
                "{file}: slot {slot}'s location {loc:?} chains with its length {len:?}"
            );
        }
    }
}
