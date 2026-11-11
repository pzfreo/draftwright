//! Stream K5 (#43 item 2): Python draftwright's dimensioning-completeness rules, one test each,
//! on draftwright's own evaluation fixtures (`tests/fixtures/parts/k5/`, see its README) read
//! with Rust quiddity's recognition.

#[path = "common/corpus.rs"]
mod corpus;

use std::path::{Path, PathBuf};

use draftwright::model::{self, Analysis, Plan, PlannedView, Spec};

fn fixture(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts/k5")
        .join(name)
}

fn analysed(name: &str) -> (draftwright::haecceity::Part, Analysis) {
    model::analyse_native(&fixture(name)).unwrap()
}

fn planned(name: &str) -> (Analysis, Plan) {
    let (_, a) = analysed(name);
    let plan = model::plan(&a, &Spec::default()).unwrap();
    (a, plan)
}

/// Where world point *p* lands on the page in view *v*.
fn page_of(v: &PlannedView, analysis: &Analysis, p: [f64; 3]) -> [f64; 2] {
    let [lo, hi] = analysis.envelope;
    let (t, u) = (v.direction, v.up);
    let right = [
        u[1] * t[2] - u[2] * t[1],
        u[2] * t[0] - u[0] * t[2],
        u[0] * t[1] - u[1] * t[0],
    ];
    let q = [0, 1, 2].map(|k| p[k] - 0.5 * (lo[k] + hi[k]));
    let dot = |a: [f64; 3]| a[0] * q[0] + a[1] * q[1] + a[2] * q[2];
    [v.at[0] + v.scale * dot(right), v.at[1] + v.scale * dot(u)]
}

fn number(f: &draftwright::bridge::Feature, key: &str) -> f64 {
    f.parameters[key].as_f64().unwrap()
}

/// ARCH0020-2, ISSUE-1357-c: on draftwright's `turned-step-compound.step` (two parallel shafts,
/// two bands each, on axis lines 80 mm apart along x), each body's step lengths are a chain of
/// its own whose witnesses start at that body's silhouette, never at the other body's outline;
/// both chains are drawn and claim their own bands.
#[test]
fn each_turned_body_has_its_own_chain_on_its_own_silhouette() {
    let (a, plan) = planned("turned-step-compound.step");
    let steps: Vec<_> = a
        .features
        .iter()
        .filter(|f| f.kind == "turned_step")
        .collect();
    assert_eq!(steps.len(), 4);
    let mut lines = 0;
    for f in &steps {
        let id = format!("d:{}/step.length", f.id);
        let d = plan
            .dimensions
            .iter()
            .find(|d| d.id == id)
            .unwrap_or_else(|| panic!("{id} is planned"));
        let placed = d.placed.as_ref().unwrap_or_else(|| panic!("{id} is drawn"));
        assert!(d.measurements.iter().all(|m| m.feature == f.id), "{id}");
        let v = plan.views.iter().find(|v| v.id == d.view).unwrap();
        let profile = &f.parameters["profile"];
        let origin: Vec<f64> = profile["axis_origin"]
            .as_array()
            .unwrap()
            .iter()
            .map(|x| x.as_f64().unwrap())
            .collect();
        // Every band of this body, by its ends along z and its radius.
        let body: Vec<(f64, f64, f64)> = steps
            .iter()
            .filter(|g| g.parameters["profile"] == *profile)
            .map(|g| {
                (
                    number(g, "lo"),
                    number(g, "hi"),
                    0.5 * number(g, "diameter"),
                )
            })
            .collect();
        for w in &placed.lines[..2] {
            lines += 1;
            let start = w[0];
            // The witness is horizontal on the page: a station along z. Its start must be at
            // the body's silhouette there (within the 1 mm gap), on either side.
            let near = body.iter().any(|&(lo, hi, r)| {
                [lo, hi].iter().any(|&z| {
                    [-r, r].iter().any(|&s| {
                        let p = page_of(v, &a, [origin[0] + s, origin[1], z]);
                        (p[0] - start[0]).abs() <= 1.0 + 1e-6 && (p[1] - start[1]).abs() <= 1e-3
                    })
                })
            });
            assert!(
                near,
                "{id}: its witness starts at {start:?}, off its own body's silhouette"
            );
        }
    }
    assert_eq!(lines, 8);
}

/// ISSUE-1357-c: a body's chain goes in the profile view that separates its axis line from its
/// sibling's. With the second shaft of `turned-step-compound.step` moved to lie behind the first
/// (its axis line 30 mm along y, the same x), the front view superimposes them and the side view
/// separates them, so every step length is drawn in the side view.
#[test]
fn a_bodys_chain_goes_in_the_view_that_separates_it() {
    let (_, mut a) = analysed("turned-step-compound.step");
    for f in a.features.iter_mut() {
        let Some(profile) = f.parameters.get_mut("profile") else {
            continue;
        };
        if profile["axis_origin"][0].as_f64() == Some(40.0) {
            profile["axis_origin"] = serde_json::json!([-40.0, 30.0, 0.0]);
        }
    }
    a.envelope = [[-55.0, -15.0, 0.0], [-25.0, 45.0, 52.0]];
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let lengths: Vec<_> = plan
        .dimensions
        .iter()
        .filter(|d| d.id.ends_with("/step.length"))
        .collect();
    assert_eq!(lengths.len(), 4);
    for d in lengths {
        assert_eq!(d.view, "v:side", "{}", d.id);
    }
}

fn font() -> draftwright::text::Font {
    draftwright::text::Font::open(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("fonts")
            .join(draftwright::dims::FONT),
    )
    .unwrap()
}

/// Lint's findings on the sheet *plan* draws of fixture *name*.
fn linted(name: &str, spec: &Spec) -> (Plan, Vec<draftwright::lint::LintIssue>) {
    let (part, a) = analysed(name);
    let plan = model::plan(&a, spec).unwrap();
    let drawing = model::render(&part, &a, &plan, &font()).unwrap();
    let issues = draftwright::lint::lint(&a, &drawing);
    (plan, issues)
}

/// The (feature, parameter) pairs the drawn dimensions of *plan* claim.
fn claimed(plan: &Plan) -> std::collections::BTreeSet<(String, String)> {
    plan.dimensions
        .iter()
        .filter(|d| d.placed.is_some())
        .flat_map(|d| {
            d.measurements
                .iter()
                .map(|m| (m.feature.clone(), m.parameter.clone()))
        })
        .collect()
}

/// ISSUE-885-a: distinct raised pads are each dimensioned by their own footprint, height and
/// location. draftwright's `pad-compound-equal.step` has two bodies with equal pads at distinct
/// sites; `pad-z-positive.step` and `pad-x-positive.step` a pad standing along z and along x.
/// Every pad's five requirements are drawn and lint finds none of them missing.
#[test]
fn each_raised_pad_states_its_footprint_height_and_location() {
    for name in [
        "pad-compound-equal.step",
        "pad-z-positive.step",
        "pad-x-positive.step",
    ] {
        let (plan, issues) = linted(name, &Spec::default());
        let (_, a) = analysed(name);
        let pads: Vec<_> = a.features.iter().filter(|f| f.kind == "pad").collect();
        assert!(!pads.is_empty(), "{name}: has pads");
        let claimed = claimed(&plan);
        for f in &pads {
            let z = f.parameters["axis"].as_str() == Some("z");
            let mut wanted = vec!["pad_width.length", "pad_length.length", "pad_height.length"];
            if z {
                wanted.extend(["location_pad.location.x", "location_pad.location.y"]);
            }
            for p in wanted {
                assert!(
                    claimed.contains(&(f.id.clone(), p.to_string())),
                    "{name}: {} {p} is drawn",
                    f.id
                );
            }
            assert!(
                claimed
                    .iter()
                    .filter(|(g, p)| *g == f.id && p.starts_with("location_pad"))
                    .count()
                    >= 2,
                "{name}: {} is located in both directions",
                f.id
            );
        }
        assert!(
            !plan
                .problems
                .iter()
                .any(|p| p.code == "dimensions_not_ported"
                    && p.subjects.iter().any(|s| s.starts_with("f:pad:"))),
            "{name}"
        );
        let found: Vec<_> = issues
            .iter()
            .filter(|i| i.code.starts_with("pad_"))
            .map(|i| i.message.clone())
            .collect();
        assert!(found.is_empty(), "{name}: {found:?}");
    }
}

/// ISSUE-909-a: the raised pad on draftwright's sloped-profile case study reaches the drawing
/// with its complete footprint (draftwright's sheet: `16.7` wide, `31` long, located `26.5`
/// along x), and omitting a required pad dimension makes lint report that requirement.
#[test]
fn the_sloped_profile_pad_reaches_the_drawing_and_an_omission_is_reported() {
    let name = "issue_909_basic_part_design_017_body.step";
    let (plan, issues) = linted(name, &Spec::default());
    let pad = |parameter: &str| {
        plan.dimensions
            .iter()
            .find(|d| {
                d.placed.is_some()
                    && d.measurements
                        .iter()
                        .any(|m| m.feature.starts_with("f:pad:") && m.parameter == parameter)
            })
            .map(|d| d.label.clone())
    };
    assert_eq!(pad("pad_width.length").as_deref(), Some("16.7"));
    assert_eq!(pad("pad_length.length").as_deref(), Some("31"));
    assert_eq!(pad("location_pad.location.x").as_deref(), Some("26.5"));
    assert_eq!(pad("pad_height.length").as_deref(), Some("7 HIGH"));
    assert!(!issues.iter().any(|i| i.code.starts_with("pad_")));
    let width = plan
        .dimensions
        .iter()
        .find(|d| d.id.ends_with("/pad_width.length"))
        .unwrap()
        .id
        .clone();
    let spec: Spec =
        serde_json::from_value(serde_json::json!({"dimensions": {width: {"omit": true}}})).unwrap();
    let (_, issues) = linted(name, &spec);
    let missing: Vec<_> = issues
        .iter()
        .filter(|i| i.code == "pad_requirement_missing")
        .collect();
    assert_eq!(missing.len(), 1, "{missing:?}");
    assert!(missing[0].message.contains("pad_width.length"));
}

/// Distance from page point *p* to the segment *a*–*b*.
fn to_segment(p: [f64; 2], a: [f64; 2], b: [f64; 2]) -> f64 {
    let d = [b[0] - a[0], b[1] - a[1]];
    let l = d[0] * d[0] + d[1] * d[1];
    let t = if l > 0.0 {
        (((p[0] - a[0]) * d[0] + (p[1] - a[1]) * d[1]) / l).clamp(0.0, 1.0)
    } else {
        0.0
    };
    (p[0] - a[0] - t * d[0]).hypot(p[1] - a[1] - t * d[1])
}

/// LINT-oriented_slot_coverage-width_length, ISSUE-1432-a: mfcadpp_holdout/467's free-direction
/// slot (2 wide, 4.543 long, its section turned about 27° in the y-z plane) is called out once,
/// `SLOT 2 WIDE × 4.543 LONG` (#225: no internal `ORIENTED`), in the view along its run,
/// claiming both requirements, its arrow on the slot's rotated rim; lint finds neither
/// requirement missing.
#[test]
fn an_oriented_slot_is_called_out_once_with_both_requirements_on_its_rim() {
    let Some(dir) = corpus::dir() else { return };
    let path = dir.join("mfcadpp_holdout/467.step");
    let (part, a) = model::analyse_native(&path).unwrap();
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let slot = a
        .features
        .iter()
        .find(|f| f.kind == "oriented_slot")
        .expect("467 has an oriented slot");
    let callouts: Vec<_> = plan
        .dimensions
        .iter()
        .filter(|d| d.measurements.iter().any(|m| m.feature == slot.id))
        .collect();
    assert_eq!(callouts.len(), 1, "one callout states the slot");
    let d = callouts[0];
    assert_eq!(d.label, "SLOT 2 WIDE × 4.543 LONG");
    assert_eq!(d.view, "v:side");
    let mut params: Vec<_> = d
        .measurements
        .iter()
        .map(|m| m.parameter.as_str())
        .collect();
    params.sort();
    assert_eq!(
        params,
        ["oriented_slot_length.length", "oriented_slot_width.length"]
    );
    let placed = d.placed.as_ref().expect("the callout is drawn");
    let v = plan.views.iter().find(|v| v.id == d.view).unwrap();
    // The rim: the record's section boundary in its passage frame (the part's: native).
    let source = &slot.parameters["source"];
    let triple = |x: &serde_json::Value| -> [f64; 3] { [0, 1, 2].map(|k| x[k].as_f64().unwrap()) };
    let fr = &source["frame"];
    let (o, run, u, w) = (
        triple(&fr["origin"]),
        triple(&fr["run"]),
        triple(&fr["u"]),
        triple(&fr["v"]),
    );
    let station = source["run_interval"][0].as_f64().unwrap();
    let rim: Vec<[f64; 2]> = source["section"]["boundary"]
        .as_array()
        .unwrap()
        .iter()
        .map(|e| {
            let c = &e["point"];
            let (p, q) = (c[0].as_f64().unwrap(), c[1].as_f64().unwrap());
            page_of(
                v,
                &a,
                [0, 1, 2].map(|k| o[k] + station * run[k] + p * u[k] + q * w[k]),
            )
        })
        .collect();
    let tip = placed.arrows[0][0];
    let off = (0..rim.len())
        .map(|i| to_segment(tip, rim[i], rim[(i + 1) % rim.len()]))
        .fold(f64::INFINITY, f64::min);
    assert!(off < 1e-3, "the tip {tip:?} is {off} mm off the rim");
    let drawing = model::render(&part, &a, &plan, &font()).unwrap();
    let issues = draftwright::lint::lint(&a, &drawing);
    let left: Vec<_> = issues
        .iter()
        .filter(|i| i.code.starts_with("oriented_slot_"))
        .map(|i| i.message.clone())
        .collect();
    assert!(left.is_empty(), "{left:?}");
}

/// ADR3-12: plural turned profiles are one input and no consumer merges them. On draftwright's
/// `groove-compound.step` (two parallel shafts, a groove each) each groove is called out on its
/// own and each body keeps its own chain; a groove whose record names neither shaft's profile
/// has no one owner and is refused with a problem naming it, not drawn on whichever body lies
/// under it.
#[test]
fn a_groove_names_one_turned_profile_or_is_refused() {
    let (_, a) = analysed("groove-compound.step");
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let grooves: Vec<_> = a.features.iter().filter(|f| f.kind == "groove").collect();
    assert_eq!(grooves.len(), 2);
    for g in &grooves {
        assert!(
            plan.dimensions.iter().any(|d| d.placed.is_some()
                && d.label == "4 WIDE × ø16"
                && d.measurements.iter().all(|m| m.feature == g.id)),
            "{} has its own callout",
            g.id
        );
    }
    let mut orphan = a.clone();
    let g = orphan
        .features
        .iter_mut()
        .find(|f| f.kind == "groove")
        .unwrap();
    g.parameters.remove("profile");
    let id = g.id.clone();
    let plan = model::plan(&orphan, &Spec::default()).unwrap();
    assert!(
        !plan
            .dimensions
            .iter()
            .any(|d| d.measurements.iter().any(|m| m.feature == id)),
        "{id} is not called out"
    );
    let refused = plan
        .problems
        .iter()
        .find(|p| p.code == "feature_not_dimensioned" && p.subjects == [id.clone()])
        .expect("the groove is reported");
    assert!(
        refused.message.contains("no one owner"),
        "{}",
        refused.message
    );
}

/// ISSUE-1374-c: a grouped length label must state the exact count and value and cannot skip an
/// equal adjacent member. On draftwright's `turned-step-repeated-lengths.step` (three adjoining
/// bands 10 long), lint's turned-step ledger credits `3× 10` claiming all three, and no band for
/// `2× 10` or `3× 999 NOTE 10` claiming all three, or for `2× 10` claiming the outer two (the
/// middle one is then missing).
#[test]
fn a_grouped_length_states_exactly_its_adjoining_members() {
    use draftwright::lint::turned::State;
    use draftwright::lint::{Mark, turned};
    use draftwright::output::Claim;
    let (_, a) = analysed("turned-step-repeated-lengths.step");
    let bands: Vec<String> = a
        .features
        .iter()
        .filter(|f| f.kind == "turned_step")
        .map(|f| f.id.clone())
        .collect();
    assert_eq!(bands.len(), 3);
    let grouped = |text: &str, members: &[&String]| Mark {
        precision: None,
        units: Default::default(),
        name: "m_steplen_typ".into(),
        view: Some("v:front".into()),
        text: text.into(),
        claims: members
            .iter()
            .map(|f| Claim {
                feature: f.to_string(),
                parameter: "step.length".into(),
            })
            .collect(),
    };
    let lengths = |marks: &[Mark]| -> Vec<State> {
        turned::outcomes(&a, marks)
            .into_iter()
            .filter(|o| o.parameter == "step.length")
            .map(|o| o.state)
            .collect()
    };
    let all: Vec<&String> = bands.iter().collect();
    assert_eq!(lengths(&[grouped("3× 10", &all)]), [State::Placed; 3]);
    assert_eq!(lengths(&[grouped("2× 10", &all)]), [State::Unverifiable; 3]);
    assert_eq!(
        lengths(&[grouped("3× 999 NOTE 10", &all)]),
        [State::Unverifiable; 3]
    );
    assert_eq!(
        lengths(&[grouped("2× 10", &[&bands[0], &bands[2]])]),
        [State::Unverifiable, State::Missing, State::Unverifiable]
    );
}
