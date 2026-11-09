//! Stream K6 (#43 item 2): one owner per measurement, no over-dimensioning. Each test names the
//! rules of `docs/rules/` it checks (ADR1-8, ISSUE-1154-a, ARCH0015-2, ADR4-U1, and the reference
//! flag of #26/#33): a part and a spec go in, a property of the plan or of lint's findings on
//! its marks comes out.

use std::collections::BTreeMap;
use std::io::Read as _;
use std::path::{Path, PathBuf};

use draftwright::bridge::holes;
use draftwright::dims::fmt;
use draftwright::items::State;
use draftwright::lint::{self, Mark};
use draftwright::model::{self, Analysis, DimensionSource, DimensionSpec, Plan, Spec};
use draftwright::output::Claim;
use serde_json::json;

fn fixture(path: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(path)
}

/// The plan's drawn items as lint reads them: each label's text and its claims.
fn marks(p: &Plan) -> Vec<Mark> {
    p.dimensions
        .iter()
        .filter(|d| d.placed.is_some())
        .map(|d| Mark {
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

fn codes(a: &Analysis, marks: &[Mark]) -> Vec<String> {
    lint::check(a, marks).into_iter().map(|i| i.code).collect()
}

/// A spec that authors exactly *ids* (`dimension_source: authored`).
fn authored<'a>(ids: impl IntoIterator<Item = &'a str>) -> Spec {
    Spec {
        dimension_source: DimensionSource::Authored,
        dimensions: ids
            .into_iter()
            .map(|id| (id.to_string(), DimensionSpec::default()))
            .collect(),
        ..Spec::default()
    }
}

/// draftwright's flush-hub plate (#1154): a ø8 hub along x flush with both faces of a 4.5 mm
/// plate, so the hub's height and the plate's overall width are the same two faces.
fn flush_hub() -> Analysis {
    serde_json::from_value(json!({"schema":model::ANALYSIS_SCHEMA,
        "binding":{"source_sha256":"","part":"flush_hub","face_count":8,"loader":"test"},
        "envelope":[[0,0,0],[4.5,10,24]],"iso_bounds":[-20,-20,20,20],"solid_count":1,
        "features":[{"id":"f:boss:f0","kind":"boss","faces":[6,7],
        "parameters":{"axis":[1,0,0],"location":[4.5,5,8],"diameter":8,"height":4.5},
        "fingerprint":{"version":"test","family":"boss","subtype":"BossRecord","sizes":{},"position":[2.25,5,8],"axis":[1,0,0],"neighbourhood":[]}}]}))
    .unwrap()
}

/// ISSUE-1154-a and ADR1-8 (authored-set parity, draftwright's
/// `test_an_authored_set_that_keeps_the_owner_critiques_the_same_as_the_automatic_build` and
/// `test_an_owner_the_authored_set_omits_is_refused`): an authored set decides what is drawn, not
/// where the geometry states a fact. The set the automatic plan draws, authored back, plans the
/// same items with the same claims, and the flush hub's height is still conveyed by the overall
/// width, not merely left out of the set. An authored set that leaves the owner out hands it
/// nothing: the height is then not authored.
#[test]
fn an_authored_set_keeps_the_owner_that_conveys_a_left_out_measurement() {
    let a = flush_hub();
    let height = "d:f:boss:f0/boss_height.length";
    let width = "d:envelope/width.length";
    let auto = model::plan(&a, &Spec::default()).unwrap();
    let conveyed = auto.omitted.iter().find(|o| o.id == height).unwrap();
    assert_eq!(conveyed.state, State::Covered { by: width.into() });

    let drawn: Vec<&str> = auto.dimensions.iter().map(|d| d.id.as_str()).collect();
    assert!(drawn.contains(&width) && !drawn.contains(&height));
    let again = model::plan(&a, &authored(drawn.iter().copied())).unwrap();
    let states = |p: &Plan| -> BTreeMap<String, (State, String)> {
        p.omitted
            .iter()
            .map(|o| (o.id.clone(), (o.state.clone(), o.basis.clone())))
            .collect()
    };
    assert_eq!(states(&again)[height], states(&auto)[height]);
    let claims = |p: &Plan| -> Vec<(String, String, Vec<String>)> {
        p.dimensions
            .iter()
            .map(|d| {
                let mut m: Vec<String> = d
                    .measurements
                    .iter()
                    .map(|m| format!("{}/{}", m.feature, m.parameter))
                    .collect();
                m.sort();
                (d.id.clone(), d.label.clone(), m)
            })
            .collect()
    };
    assert_eq!(claims(&again), claims(&auto));
    assert_eq!(
        codes(&a, &marks(&again)),
        codes(&a, &marks(&auto)),
        "the authored set critiques as the automatic plan does"
    );

    // An owner the set leaves out takes nothing.
    let without: Vec<&str> = drawn.iter().copied().filter(|id| *id != width).collect();
    let refused = model::plan(&a, &authored(without)).unwrap();
    let left = refused.omitted.iter().find(|o| o.id == height).unwrap();
    assert_eq!(left.state, State::NotAuthored);
}

fn gunzip(path: &Path) -> String {
    let mut text = String::new();
    flate2::read::GzDecoder::new(std::fs::File::open(path).unwrap())
        .read_to_string(&mut text)
        .unwrap();
    text
}

/// A step fixture part (`tests/fixtures/recognition/steps/`) read with its recognition.
fn steps_fixture(name: &str) -> Analysis {
    let dir = fixture("recognition/steps");
    let json = gunzip(&dir.join(format!("{name}.json.gz")));
    let (part, mut a) = model::analyse(&dir.join(format!("{name}.step"))).unwrap();
    let (r, features) = draftwright::bridge::read(&part, &a.binding.source_sha256, &json).unwrap();
    a.recognition = Some(r);
    a.features = features;
    a
}

fn ladder(p: &Plan) -> Vec<String> {
    let mut out: Vec<String> = p
        .dimensions
        .iter()
        .filter(|d| d.id.starts_with("d:step_level/step_height.length"))
        .map(|d| d.id.clone())
        .collect();
    out.sort();
    out
}

/// ARCH0015-2: a correlated set (the step-height ladder) is approved or omitted whole, never
/// half a staircase. Each rung keeps its own ID (ADR4-6), so the spec's decision on any rung is
/// the set's: omitting the 16 rung omits the 32 rung too (an `info` problem says so, its fix
/// restoring the set), and an authored set listing the 16 rung alone draws both.
#[test]
fn a_height_ladder_is_omitted_or_approved_whole() {
    let a = steps_fixture("plates_pads_levels_and_slanted_steps");
    let (h16, h32) = (
        "d:step_level/step_height.length.f14",
        "d:step_level/step_height.length.f20",
    );
    let full = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(ladder(&full), [h16, h32]);

    let spec: Spec = serde_json::from_value(json!({"dimensions": {h16: {"omit": true}}})).unwrap();
    let omitted = model::plan(&a, &spec).unwrap();
    assert_eq!(ladder(&omitted), Vec::<String>::new());
    let with = omitted.omitted.iter().find(|o| o.id == h32).unwrap();
    assert_eq!(with.state, State::OmittedByUser);
    assert!(with.basis.contains("omitted whole"), "{}", with.basis);
    let p = omitted
        .problems
        .iter()
        .find(|p| p.code == "correlated_set_whole")
        .expect("the spread is reported");
    assert_eq!(
        (p.severity.as_str(), &p.subjects[..]),
        ("info", &[h32.to_string()][..])
    );
    let restored = model::edit(&spec, &serde_json::to_value(&p.fix).unwrap()).unwrap();
    assert_eq!(ladder(&model::plan(&a, &restored).unwrap()), [h16, h32]);

    // An authored set listing one rung approves the ladder.
    let others: Vec<&str> = full
        .dimensions
        .iter()
        .map(|d| d.id.as_str())
        .filter(|id| *id != h32)
        .collect();
    let approved = model::plan(&a, &authored(others)).unwrap();
    assert_eq!(ladder(&approved), [h16, h32]);
}

/// ARCH0015-2 with the spec's own decision on a member: omitting the 16 rung never overrides a
/// tolerance (or `omit: false`) the spec states on the 32 rung. The 32 rung is drawn with its
/// tolerance, a `correlated_set_conflict` warning names both rungs, and its fix applies and
/// draws the whole ladder with the tolerance kept. No problem offers a fix that cannot apply.
#[test]
fn a_ladder_omission_never_overrides_a_rung_the_spec_decides() {
    let a = steps_fixture("plates_pads_levels_and_slanted_steps");
    let (h16, h32) = (
        "d:step_level/step_height.length.f14",
        "d:step_level/step_height.length.f20",
    );
    for own in [json!({"tolerance": "±0.1"}), json!({"omit": false})] {
        let spec: Spec =
            serde_json::from_value(json!({"dimensions": {h16: {"omit": true}, h32: own}})).unwrap();
        let plan = model::plan(&a, &spec).unwrap();
        assert_eq!(ladder(&plan), [h32], "{own}");
        if own.get("tolerance").is_some() {
            let label = &plan.dimensions.iter().find(|d| d.id == h32).unwrap().label;
            assert!(label.contains("±0.1"), "{label}");
        }
        assert!(
            plan.problems.iter().all(|p| !matches!(
                p.code.as_str(),
                "correlated_set_whole" | "tolerance_not_shown"
            )),
            "{:?}",
            plan.problems
        );
        let p = plan
            .problems
            .iter()
            .find(|p| p.code == "correlated_set_conflict")
            .expect("the conflict is reported");
        assert_eq!(
            (p.severity.as_str(), &p.subjects[..]),
            ("warning", &[h16.to_string(), h32.to_string()][..])
        );
        for q in plan.problems.iter().filter(|q| !q.fix.is_empty()) {
            model::edit(&spec, &serde_json::to_value(&q.fix).unwrap())
                .unwrap_or_else(|e| panic!("{}'s fix does not apply: {e:?}", q.code));
        }
        let restored = model::edit(&spec, &serde_json::to_value(&p.fix).unwrap()).unwrap();
        let whole = model::plan(&a, &restored).unwrap();
        assert_eq!(ladder(&whole), [h16, h32], "{own}");
        if own.get("tolerance").is_some() {
            let label = &whole.dimensions.iter().find(|d| d.id == h32).unwrap().label;
            assert!(label.contains("±0.1"), "{label}");
        }
    }
}

/// ADR4-U1: over-dimensioning that is neither identical nor coincident is reported. A grid's
/// column pitch, printed beside every member's own location along that axis, states a
/// separation the locations already fix (`over_dimensioned_pattern`). The plan's own marks
/// (the grid located by its centre with its pitches) are not over-dimensioned.
#[test]
fn a_pattern_pitch_beside_every_members_location_is_reported() {
    let (part, mut a) = model::analyse(&fixture("parts/plate.step")).unwrap();
    let json = std::fs::read_to_string(fixture("recognition/plate.json")).unwrap();
    model::recognise(&part, &mut a, &json).unwrap();
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let own = marks(&plan);
    assert!(!codes(&a, &own).contains(&"over_dimensioned_pattern".to_string()));
    let pitch = own
        .iter()
        .find(|m| {
            m.claims
                .iter()
                .any(|c| c.parameter == "grid_pitch.length.col")
        })
        .expect("the plate's grid prints its column pitch");
    let pattern = pitch.claims[0].feature.clone();
    let recognition = a.recognition.as_ref().unwrap();
    let members = &recognition
        .patterns
        .iter()
        .find(|p| p.id == pattern)
        .unwrap()
        .members;
    // The column pitch runs along the axis on which the members take two stations.
    let read = holes::read(&a);
    let at: Vec<[f64; 3]> = read
        .iter()
        .filter(|(f, _)| members.contains(&f.id))
        .map(|(_, h)| h.as_ref().unwrap().at)
        .collect();
    let mut over = own.clone();
    let k = (0..3)
        .find(|&k| {
            let mut s: Vec<i64> = at.iter().map(|p| (p[k] * 100.0).round() as i64).collect();
            s.sort();
            s.dedup();
            s.len() == 2 && holes::principal(read[0].1.as_ref().unwrap().direction) != Some(k)
        })
        .unwrap();
    for (f, h) in read.iter().filter(|(f, _)| members.contains(&f.id)) {
        let h = h.as_ref().unwrap();
        over.push(Mark {
            name: format!("{}/location", f.id),
            view: Some("v:plan".into()),
            text: fmt(h.at[k] - a.envelope[0][k]),
            claims: vec![Claim {
                feature: f.id.clone(),
                parameter: format!("location.length.{}", ["x", "y", "z"][k]),
            }],
        });
    }
    let found: Vec<_> = lint::check(&a, &over)
        .into_iter()
        .filter(|i| i.code == "over_dimensioned_pattern")
        .collect();
    assert_eq!(found.len(), 1, "{found:?}");
    assert_eq!(found[0].annotation.as_deref(), Some(pitch.name.as_str()));
}

/// #33 and #26: reference dimensions are only for redundant, untoleranced ones. Lint reports a
/// bracketed label that prints a tolerance or a fit (`toleranced_reference`), and the planner
/// never draws one: on GRM-03-shaped chains a toleranced member stays controlling. Here the
/// boss fixture's reference member, given a tolerance by the spec, is controlling and lint
/// finds no toleranced reference; the same label forced into brackets is reported.
#[test]
fn no_toleranced_dimension_is_drawn_as_reference() {
    let (part, mut a) = model::analyse(&fixture("parts/boss_circle.step")).unwrap();
    let json = std::fs::read_to_string(fixture("recognition/boss_circle.json")).unwrap();
    model::recognise(&part, &mut a, &json).unwrap();
    let plan = model::plan(
        &a,
        &serde_json::from_value(json!({"sheet":{"references":true}})).unwrap(),
    )
    .unwrap();
    let reference = plan
        .dimensions
        .iter()
        .find(|d| d.reference)
        .expect("the boss height closes the level-to-overall chain");
    let spec: Spec = serde_json::from_value(
        json!({"dimensions": {reference.id.clone(): {"tolerance": "±0.05"}}}),
    )
    .unwrap();
    let toleranced = model::plan(&a, &spec).unwrap();
    for d in &toleranced.dimensions {
        if d.label.contains('±') {
            assert!(!d.reference && !d.label.starts_with('('), "{}", d.label);
        }
    }
    assert!(!codes(&a, &marks(&toleranced)).contains(&"toleranced_reference".to_string()));

    let mut forced = marks(&toleranced);
    let m = forced.iter_mut().find(|m| m.name == reference.id).unwrap();
    m.text = format!("({})", m.text);
    let found: Vec<_> = lint::check(&a, &forced)
        .into_iter()
        .filter(|i| i.code == "toleranced_reference")
        .collect();
    assert_eq!(found.len(), 1, "{found:?}");
    assert_eq!(found[0].annotation.as_deref(), Some(reference.id.as_str()));
    // An untoleranced reference, and a fit class, are told apart.
    forced
        .iter_mut()
        .find(|m| m.name == reference.id)
        .unwrap()
        .text = "(ø20 H7)".into();
    assert!(codes(&a, &forced).contains(&"toleranced_reference".to_string()));
    forced
        .iter_mut()
        .find(|m| m.name == reference.id)
        .unwrap()
        .text = format!("({})", fmt(reference.value));
    assert!(!codes(&a, &forced).contains(&"toleranced_reference".to_string()));
}

/// One measurement, one owner, on the test parts (#43 item 2's acceptance): on the fixture parts
/// (and the flush hub and the ladder fixture above), with the automatic spec, lint finds no
/// measurement stated twice, no closed chain, no over-dimensioned pattern and no toleranced
/// reference among the plan's drawn items. On the base, boss_circle closed a chain (6 + 3 = 9,
/// all controlling); its boss height is now reference.
#[test]
fn no_measurement_is_drawn_twice_on_the_test_parts() {
    let mut parts: Vec<(String, Analysis)> =
        ["plate", "turned", "prismatic", "two_grids", "boss_circle"]
            .iter()
            .map(|name| {
                let (part, mut a) =
                    model::analyse(&fixture(&format!("parts/{name}.step"))).unwrap();
                let json =
                    std::fs::read_to_string(fixture(&format!("recognition/{name}.json"))).unwrap();
                model::recognise(&part, &mut a, &json).unwrap();
                (name.to_string(), a)
            })
            .collect();
    parts.push(("flush_hub".into(), flush_hub()));
    parts.push((
        "plates_pads_levels_and_slanted_steps".into(),
        steps_fixture("plates_pads_levels_and_slanted_steps"),
    ));
    for (name, a) in &parts {
        let plan = model::plan(a, &Spec::default()).unwrap();
        let found: Vec<String> = codes(a, &marks(&plan))
            .into_iter()
            .filter(|c| {
                [
                    "duplicate_measurement",
                    "closed_dimension_chain",
                    "over_dimensioned_pattern",
                    "toleranced_reference",
                ]
                .contains(&c.as_str())
            })
            .collect();
        assert!(found.is_empty(), "{name}: {found:?}");
    }
}

/// ADR4-U1, resolved in the plan: boss_circle's ø12 boss stands 3 on a 6 plate in a 9 part, and
/// the ladder rung (6), the boss height (3) and the overall height (9) close a chain, all
/// measured from the base. The boss height, the chain member between two stated stations, is
/// drawn as reference `(3)`, determined by the rung and the overall height; the rung and the
/// overall height stay controlling.
#[test]
fn a_boss_height_on_a_stated_level_is_a_reference() {
    let (part, mut a) = model::analyse(&fixture("parts/boss_circle.step")).unwrap();
    let json = std::fs::read_to_string(fixture("recognition/boss_circle.json")).unwrap();
    model::recognise(&part, &mut a, &json).unwrap();
    let plan = model::plan(
        &a,
        &serde_json::from_value(json!({"sheet":{"references":true}})).unwrap(),
    )
    .unwrap();
    let get = |id: &str| plan.dimensions.iter().find(|d| d.id == id).unwrap();
    let boss = get("d:f:boss:f0/boss_height.length");
    assert_eq!((boss.label.as_str(), boss.reference), ("(3)", true));
    assert_eq!(
        boss.determined_by,
        [
            "d:step_level/step_height.length.f1",
            "d:envelope/height.length"
        ]
    );
    for id in [
        "d:step_level/step_height.length.f1",
        "d:envelope/height.length",
    ] {
        assert!(!get(id).reference, "{id}");
    }
    // Toleranced, the boss height is controlling (reference only for untoleranced ones).
    let spec: Spec = serde_json::from_value(
        json!({"dimensions": {"d:f:boss:f0/boss_height.length": {"tolerance": "±0.1"}}}),
    )
    .unwrap();
    let toleranced = model::plan(&a, &spec).unwrap();
    let boss = toleranced
        .dimensions
        .iter()
        .find(|d| d.id == "d:f:boss:f0/boss_height.length")
        .unwrap();
    assert_eq!((boss.label.as_str(), boss.reference), ("3 ±0.1", false));
}
