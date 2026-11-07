//! Stream K4 (#43 item 2): the Python leader-correctness rules, each checked on a sheet built
//! through the public types (an Analysis, and a drawing with one view and its recorded
//! transform), sound and then broken one way, through `lint::lint`:
//!
//! - ISSUE-1378-a, LINT-code-diameter_leader_target_*: a diameter leader's tip touches the rim
//!   of a hole it names (a double-D's trimmed profile, flats included), never blank material or
//!   another hole's rim; a callout whose holes cannot be read says so.
//! - ISSUE-1479-a, LINT-code-radius_leader_target_*: a radius leader's tip is on the named
//!   blend's trimmed profile arc, not a centre bore or the untrimmed circle's extension, and the
//!   check follows the geometry wherever it is; a view that cannot show the arc says so.
//! - ADR2-8, ISSUE-798-b/d, LINT-code-leader_crosses_silhouette: a shaft cutting back into the
//!   material after leaving it at its tip is reported with its measured depth; leaving its own
//!   body is not, and a second body is. The router prices the same predicate
//!   (`draftwright::material::reentry`), and the plan's report of a leader cutting back across
//!   its view and lint's finding name the same callouts with the same depth: on a pin's leader
//!   pinned to cut across the plate-and-pin assembly, and on the port's own drawings.
//!
//! The automatic drawings of the fixture parts (and string_post's blend) are linted too: every
//! hole and blend callout the planner places passes, so ISSUE-1378-a and ISSUE-1479-a are met
//! by the planner, not only checked.

#[path = "common/cache.rs"]
mod cache;
#[path = "common/local_fix.rs"]
mod local_fix;

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use draftwright::fixes::Scope;
use draftwright::items::{Op, key};
use draftwright::lint::{self, LintIssue, Severity};
use draftwright::model::{self, Analysis, Spec};
use draftwright::output::{Annotation, Claim, Drawing, Drawn, DrawnFrame, DrawnView, Kind, Sheet};
use draftwright::{Session, api};
use serde_json::{Value, json};

type P = [f64; 2];

/// The view's frame: looking along +z with y up, so the part's (x, y) is the page's, at scale 2
/// about [100, 100] (the envelope's centre is the origin).
const AT: P = [100.0, 100.0];
const SCALE: f64 = 2.0;

fn page(x: f64, y: f64) -> P {
    [AT[0] + SCALE * x, AT[1] + SCALE * y]
}

fn feature(id: &str, kind: &str, parameters: Value) -> Value {
    json!({
        "id": id, "kind": kind, "faces": [0], "parameters": parameters,
        "fingerprint": { "version": "test", "family": "test", "subtype": "Test", "sizes": {},
                         "axis": [0.0, 0.0, 1.0], "position": [0.0, 0.0, 0.0],
                         "neighbourhood": [] },
    })
}

/// A 20 mm cube about the origin with *features*, *blend_sites* and *solids* (each a
/// principal profile's box; none for the envelope alone).
fn analysis(features: Vec<Value>, blend_sites: Value, solids: &[[[f64; 3]; 2]]) -> Analysis {
    let profiles: Vec<Value> = solids
        .iter()
        .map(|b| json!({ "bounds": b, "faces": [] }))
        .collect();
    serde_json::from_value(json!({
        "schema": model::ANALYSIS_SCHEMA,
        "binding": { "source_sha256": "test", "part": "test", "face_count": 1, "loader": "test" },
        "envelope": [[-10.0, -10.0, -10.0], [10.0, 10.0, 10.0]],
        "iso_bounds": [0.0, 0.0, 0.0, 0.0],
        "features": features,
        "blend_sites": blend_sites,
        "principal_profiles": profiles,
    }))
    .unwrap()
}

fn hole(id: &str, x: f64, y: f64, diameter: f64) -> Value {
    feature(
        id,
        "hole",
        json!({ "axis": [0.0, 0.0, 1.0], "location": [x, y, 10.0], "diameter": diameter,
                "depth": 6.0, "bottom": "flat" }),
    )
}

/// A callout *name* in the view, claiming *claims*, its leader from *tip* to *elbow* and a
/// shoulder 10 mm on.
fn callout(name: &str, claims: &[(&str, &str)], tip: P, elbow: P) -> (Annotation, Drawn) {
    let end = [elbow[0] + 10.0, elbow[1]];
    let label = [
        elbow[0] + 1.0,
        elbow[1] + 0.5,
        elbow[0] + 9.0,
        elbow[1] + 3.0,
    ];
    (
        Annotation {
            name: name.into(),
            view: Some("v:plan".into()),
            claims: claims
                .iter()
                .map(|(f, p)| Claim {
                    feature: f.to_string(),
                    parameter: p.to_string(),
                })
                .collect(),
            ..Annotation::default()
        },
        Drawn {
            datum_triangle: None,
            heads: Vec::new(),
            name: name.into(),
            kind: Kind::Dimension,
            ink: [
                tip[0].min(elbow[0]),
                tip[1].min(elbow[1]),
                end[0].max(tip[0]),
                label[3].max(tip[1]),
            ],
            label: Some(label),
            lines: vec![[tip, elbow], [elbow, end]],
            elbow: Some(elbow),
            interior: false,
        },
    )
}

/// A sheet with the plan view drawn looking along *direction* (up +y, or +z when looking
/// along y), holding *items*.
fn drawing(direction: [f64; 3], items: Vec<(Annotation, Drawn)>) -> Drawing {
    let (annotations, drawn) = items.into_iter().unzip();
    let up = if direction[1] != 0.0 {
        [0.0, 0.0, 1.0]
    } else {
        [0.0, 1.0, 0.0]
    };
    Drawing {
        page: [420.0, 297.0],
        layers: Vec::new(),
        link: None,
        text: Vec::new(),
        annotations,
        sheet: Sheet {
            characteristics: Vec::new(),
            inspection_enabled: false,
            label_text: Default::default(),
            precision: Default::default(),
            references: Vec::new(),
            drawable: Some([10.0, 10.0, 410.0, 287.0]),
            views: vec![DrawnView {
                name: "v:plan".into(),
                bbox: [80.0, 80.0, 120.0, 120.0],
                edges: Vec::new(),
            }],
            items: drawn,
            fields: Vec::new(),
            centre_marks: BTreeMap::new(),
            frames: BTreeMap::from([(
                "plan".to_string(),
                DrawnFrame {
                    direction,
                    up,
                    at: AT,
                    scale: SCALE,
                    half_axis: None,
                    broken: None,
                },
            )]),
        },
        problems: Vec::new(),
        spec: None,
    }
}

const ALONG_Z: [f64; 3] = [0.0, 0.0, 1.0];

/// The findings on *drawing* whose code starts with *prefix*, as (code, annotation).
fn found(a: &Analysis, drawing: &Drawing, prefix: &str) -> Vec<(String, String)> {
    lint::lint(a, drawing)
        .into_iter()
        .filter(|i| i.code.starts_with(prefix))
        .map(|i| (i.code, i.annotation.unwrap_or_default()))
        .collect()
}

fn one(code: &str, name: &str) -> Vec<(String, String)> {
    vec![(code.to_string(), name.to_string())]
}

/// ISSUE-1378-a, LINT-code-diameter_leader_target_mismatch and _unverifiable: a hole callout's
/// tip on its own hole's rim (opening, or far end, counterbore none here) passes; in blank
/// material, or on the rim of a hole the callout does not name, it is a warning naming the
/// callout and its measurement; a hole whose record cannot be read is unverifiable. A
/// double-D bore's boundary is its trimmed profile: a flat and an arc pass, the major circle
/// where the flats cut it away does not.
#[test]
fn a_diameter_tip_touches_the_rim_of_the_hole_it_names() {
    let a = analysis(
        vec![
            hole("f:hole:1", 2.0, 0.0, 4.0),
            hole("f:hole:2", -6.0, 5.0, 2.0),
            feature(
                "f:double_d_bore:1",
                "double_d_bore",
                json!({ "axis": [0.0, 0.0, 1.0], "location": [-5.0, -5.0, 10.0],
                        "major_diameter": 6.0, "across_flats": 4.0,
                        "flat_direction": [1.0, 0.0, 0.0], "through": true }),
            ),
            feature("f:hole:3", "hole", json!({ "location": [0.0, 0.0, 0.0] })),
        ],
        json!({}),
        &[],
    );
    let bore = [("f:hole:1", "bore.diameter")];
    let check = |tip: P, claims: &[(&str, &str)]| {
        found(
            &a,
            &drawing(ALONG_Z, vec![callout("d:c", claims, tip, [130.0, 110.0])]),
            "diameter_leader_target",
        )
    };
    // On the rim, either side.
    assert!(check(page(4.0, 0.0), &bore).is_empty());
    assert!(check(page(2.0, -2.0), &bore).is_empty());
    // In blank material, 0.5 mm off the rim.
    assert_eq!(
        check(page(4.25, 0.0), &bore),
        one("diameter_leader_target_mismatch", "d:c")
    );
    // On another hole's rim, one the callout does not name.
    assert_eq!(
        check(page(-5.0, 5.0), &bore),
        one("diameter_leader_target_mismatch", "d:c")
    );
    // ... and fine when it names that hole too (a counted callout tips one member).
    assert!(check(page(-5.0, 5.0), &[bore[0], ("f:hole:2", "bore.diameter")]).is_empty());
    // A double-D: a flat (x = −3, |y + 5| ≤ √5), an arc (the top of the major circle).
    let dd = [("f:double_d_bore:1", "bore.diameter")];
    assert!(check(page(-3.0, -4.0), &dd).is_empty());
    assert!(check(page(-5.0, -2.0), &dd).is_empty());
    // The major circle where the flat cut it away is blank material.
    assert_eq!(
        check(page(-2.0, -5.0), &dd),
        one("diameter_leader_target_mismatch", "d:c")
    );
    // A hole without an axis or diameter cannot be checked.
    assert_eq!(
        check(page(0.0, 0.0), &[("f:hole:3", "bore.diameter")]),
        one("diameter_leader_target_unverifiable", "d:c")
    );
    // The finding carries the callout's measurement and where the tip is.
    let issues = lint::lint(
        &a,
        &drawing(
            ALONG_Z,
            vec![callout("d:c", &bore, page(4.25, 0.0), [130.0, 110.0])],
        ),
    );
    let issue: &LintIssue = issues
        .iter()
        .find(|i| i.code == "diameter_leader_target_mismatch")
        .unwrap();
    assert_eq!(issue.measurements, ["f:hole:1/bore.diameter"]);
    assert_eq!(issue.at, Some(page(4.25, 0.0)));
    assert_eq!(issue.severity, Severity::Warning);
}

/// A blend of radius 3 about the z axis through (*x*, *y*), its trimmed arc the quarter from
/// +x to +y (proven points at a quarter, a half and three quarters of it).
fn quarter_blend(x: f64, y: f64) -> (Value, Value) {
    let sites: Vec<[f64; 3]> = [22.5f64, 45.0, 67.5]
        .iter()
        .map(|d| {
            let t = d.to_radians();
            [x + 3.0 * t.cos(), y + 3.0 * t.sin(), 10.0]
        })
        .collect();
    (
        feature(
            "f:blend:1",
            "blend",
            json!({ "radius": 3.0, "side": "concave",
                    "path": { "at": [x, y, 0.0], "direction": [0.0, 0.0, 1.0] } }),
        ),
        json!({ "f:blend:1": sites }),
    )
}

/// ISSUE-1479-a, LINT-code-radius_leader_target_mismatch and _unverifiable: a radius tip on
/// the blend's trimmed arc passes, at its middle or near an end; on a centre bore (a smaller
/// circle about the same axis), off the circle, or on the untrimmed circle's extension it is a
/// warning; moved with the geometry it still passes; in a view that does not show the arc
/// end-on it cannot be checked.
#[test]
fn a_radius_tip_lands_on_the_trimmed_profile_arc() {
    let check = |at: P, tip_deg: f64, r: f64, direction: [f64; 3]| {
        let (f, sites) = quarter_blend(at[0], at[1]);
        let a = analysis(vec![f], sites, &[]);
        let t = tip_deg.to_radians();
        let tip = page(at[0] + r * t.cos(), at[1] + r * t.sin());
        found(
            &a,
            &drawing(
                direction,
                vec![callout(
                    "d:r",
                    &[("f:blend:1", "blend.radius")],
                    tip,
                    [130.0, 110.0],
                )],
            ),
            "radius_leader_target",
        )
    };
    let mismatch = one("radius_leader_target_mismatch", "d:r");
    for at in [[0.0, 0.0], [-4.5, 3.25]] {
        assert!(check(at, 45.0, 3.0, ALONG_Z).is_empty(), "{at:?}");
        assert!(check(at, 2.0, 3.0, ALONG_Z).is_empty(), "{at:?}");
        assert!(check(at, 88.0, 3.0, ALONG_Z).is_empty(), "{at:?}");
        // A centre bore of radius 1 on the same axis.
        assert_eq!(check(at, 45.0, 1.0, ALONG_Z), mismatch, "{at:?}");
        // Off the circle by 0.1 mm.
        assert_eq!(check(at, 45.0, 3.1, ALONG_Z), mismatch, "{at:?}");
        // On the circle, past the trimmed arc.
        assert_eq!(check(at, 180.0, 3.0, ALONG_Z), mismatch, "{at:?}");
        assert_eq!(check(at, -20.0, 3.0, ALONG_Z), mismatch, "{at:?}");
    }
    // A view along y cannot show the arc as an arc.
    assert_eq!(
        check([0.0, 0.0], 45.0, 3.0, [0.0, -1.0, 0.0]),
        one("radius_leader_target_unverifiable", "d:r")
    );
}

/// #145: lint reads actual drawn shafts, including hand-authored/pinned geometry.
#[test]
fn radius_shafts_meet_the_middle_arc_radially_from_either_side() {
    for concave in [true, false] {
        let (mut f, sites) = quarter_blend(0.0, 0.0);
        f["parameters"]["side"] = json!(if concave { "concave" } else { "convex" });
        let a = analysis(vec![f], sites, &[]);
        for (angle, deviation, bad) in [
            (45.0f64, 0.0f64, false),
            (0.0, 0.0, true),
            (90.0, 0.0, true),
            (45.0, 16.0, true),
            (45.0, 180.0, false),
        ] {
            let t = angle.to_radians();
            let tip = page(3.0 * t.cos(), 3.0 * t.sin());
            let direction =
                t + if concave { std::f64::consts::PI } else { 0.0 } + deviation.to_radians();
            let elbow = [
                tip[0] + 20.0 * direction.cos(),
                tip[1] + 20.0 * direction.sin(),
            ];
            let issues = lint::lint(
                &a,
                &drawing(
                    ALONG_Z,
                    vec![callout("d:r", &[("f:blend:1", "blend.radius")], tip, elbow)],
                ),
            );
            assert!(
                issues
                    .iter()
                    .filter(|i| i.code.starts_with("radius_leader_"))
                    .count()
                    <= 1
            );
            let radial: Vec<_> = issues
                .iter()
                .filter(|i| i.code == "radius_leader_not_radial")
                .collect();
            assert_eq!(radial.len(), usize::from(bad), "{angle} {deviation}");
            if bad {
                assert_eq!(radial[0].severity, Severity::Warning);
                assert_eq!(radial[0].annotation.as_deref(), Some("d:r"));
                assert_eq!(radial[0].measurements, ["f:blend:1/blend.radius"]);
            }
        }
    }
}

/// A consolidated fillet is checked against its own face, independently of material side.
#[test]
fn fillet_radius_shafts_use_the_fillets_own_cylindrical_evidence() {
    let f = feature(
        "f:fillet:1",
        "fillet",
        json!({"radius":3.0, "axis":"z", "at":[3.0,0.0,0.0]}),
    );
    let mut a = analysis(vec![f], json!({}), &[]);
    a.radius_arcs.insert("f:fillet:1".into(), serde_json::from_value(json!([{
        "arc":{"centre":[0.0,0.0,0.0],"radius":3.0,"u":[1.0,0.0,0.0],"v":[0.0,1.0,0.0],"start":0.0,"sweep":std::f64::consts::FRAC_PI_2},
        "axis":[0.0,0.0,1.0],"centre_open":true,"tips":[]
    }])).unwrap());
    let tip = page(3.0 / 2.0f64.sqrt(), 3.0 / 2.0f64.sqrt());
    let draw = drawing(
        ALONG_Z,
        vec![callout(
            "d:r",
            &[
                ("f:fillet:1", "fillet.radius"),
                ("f:blend:missing", "blend.radius"),
            ],
            tip,
            page(-5.0, -5.0),
        )],
    );
    assert!(found(&a, &draw, "radius_leader").is_empty());
    a.radius_arcs.get_mut("f:fillet:1").unwrap()[0].centre_open = false;
    assert!(found(&a, &draw, "radius_leader_not_radial").is_empty());
}

/// ISSUE-1374-b: a fillet.radius tip moved off its round loses credit: past the trimmed arc on
/// the same circle, or off the circle, it is reported.
#[test]
fn a_fillet_radius_tip_moved_off_its_arc_is_reported() {
    let f = feature(
        "f:fillet:1",
        "fillet",
        json!({"radius":3.0, "axis":"z", "at":[3.0,0.0,0.0]}),
    );
    let mut a = analysis(vec![f], json!({}), &[]);
    a.radius_arcs.insert("f:fillet:1".into(), serde_json::from_value(json!([{
        "arc":{"centre":[0.0,0.0,0.0],"radius":3.0,"u":[1.0,0.0,0.0],"v":[0.0,1.0,0.0],"start":0.0,"sweep":std::f64::consts::FRAC_PI_2},
        "axis":[0.0,0.0,1.0],"centre_open":true,"tips":[]
    }])).unwrap());
    // The shaft runs along the tip's own radial, through the centre, so only the tip's place
    // on the trimmed arc can raise a finding.
    let check = |deg: f64, r: f64| {
        let t = deg.to_radians();
        let tip = page(r * t.cos(), r * t.sin());
        let elbow = page(-5.5 * t.cos(), -5.5 * t.sin());
        let draw = drawing(
            ALONG_Z,
            vec![callout(
                "d:r",
                &[("f:fillet:1", "fillet.radius")],
                tip,
                elbow,
            )],
        );
        lint::lint(&a, &draw)
            .into_iter()
            .filter(|i| i.code.starts_with("radius_leader"))
            .map(|i| (i.code, i.annotation.unwrap_or_default(), i.message))
            .collect::<Vec<_>>()
    };
    assert!(check(45.0, 3.0).is_empty());
    for (deg, r) in [(180.0, 3.0), (45.0, 3.5)] {
        let issues = check(deg, r);
        assert_eq!(issues.len(), 1, "{deg} {r}: {issues:?}");
        let (code, name, message) = &issues[0];
        assert_eq!(
            (code.as_str(), name.as_str()),
            ("radius_leader_not_radial", "d:r")
        );
        assert!(
            message.contains("does not touch its named trimmed profile arc"),
            "{deg} {r}: {message}"
        );
    }
}

#[test]
fn toroidal_radius_direction_limits_are_explicit() {
    let f = feature(
        "f:blend:1",
        "blend",
        json!({"radius":3.0,"path":{"at":[0.0,0.0,0.0],"normal":[0.0,0.0,1.0]}}),
    );
    let a = analysis(vec![f], json!({"f:blend:1":[[3.0,0.0,0.0]]}), &[]);
    let draw = drawing(
        ALONG_Z,
        vec![callout(
            "d:r",
            &[("f:blend:1", "blend.radius")],
            page(3.0, 0.0),
            page(10.0, 10.0),
        )],
    );
    let issues = lint::lint(&a, &draw);
    assert!(issues.iter().any(|i| {
        i.code == "radius_leader_target_unverifiable"
            && i.severity == Severity::Info
            && i.evidence
                .as_ref()
                .is_some_and(|why| why.contains("toroidal"))
    }));
}

/// ADR2-8, ISSUE-798-b/d, LINT-code-leader_crosses_silhouette: a shaft leaving its own body
/// (whatever it passes over inside it, a bore included) is not reported; a shaft that goes on
/// into a second body is, as info, with the depth it cuts; a deeper cut reads deeper. The
/// predicate is `material::reentry`, which the router prices (its own tests are in
/// `src/material.rs`).
#[test]
fn a_shaft_cutting_back_into_the_material_is_reported_with_its_depth() {
    // Two bodies side by side: x −10..0 and 5..10 (page x 80..100 and 110..120).
    let a = analysis(
        vec![hole("f:hole:1", -5.0, 0.0, 4.0)],
        json!({}),
        &[
            [[-10.0, -10.0, -10.0], [0.0, 10.0, 10.0]],
            [[5.0, -10.0, -10.0], [10.0, 10.0, 10.0]],
        ],
    );
    let bore = [("f:hole:1", "bore.diameter")];
    let lint_of = |tip: P, elbow: P| -> Vec<LintIssue> {
        lint::lint(
            &a,
            &drawing(ALONG_Z, vec![callout("d:c", &bore, tip, elbow)]),
        )
        .into_iter()
        .filter(|i| i.code == "leader_crosses_silhouette")
        .collect()
    };
    // Out of its own body to the left, across the bore: one traversal.
    assert!(lint_of(page(-7.0, 0.0), [60.0, 100.0]).is_empty());
    // Out to the right across the gap and through the second body: a 10 mm cut.
    let cut = lint_of(page(-3.0, 0.0), [130.0, 100.0]);
    assert_eq!(cut.len(), 1, "{cut:#?}");
    assert_eq!(cut[0].severity, Severity::Info);
    assert_eq!(cut[0].annotation.as_deref(), Some("d:c"));
    assert!(
        cut[0]
            .message
            .contains("cuts 10.0 mm back through view 'plan'"),
        "{}",
        cut[0].message
    );
    // A shallower cut (stopping inside the second body) reads less.
    let shallow = lint_of(page(-3.0, 0.0), [113.0, 100.0]);
    assert!(
        shallow[0].message.contains("cuts 3.0 mm"),
        "{}",
        shallow[0].message
    );
}

// ----------------------------------------------------------------------------------------
// The port's own drawings
// ----------------------------------------------------------------------------------------

fn fixture(path: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(path)
}

fn font() -> draftwright::text::Font {
    draftwright::text::Font::open(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"),
    )
    .unwrap()
}

/// Each fixture part's automatic plan and drawing, and string_post's from the corpus (a
/// blend radius callout) when the corpus is present.
fn drawings() -> Vec<(String, Analysis, model::Plan, Drawing)> {
    let mut out = Vec::new();
    for name in ["plate", "turned", "prismatic", "two_grids", "boss_circle"] {
        let (part, mut a) = model::analyse(&fixture(&format!("parts/{name}.step"))).unwrap();
        let json = std::fs::read_to_string(fixture(&format!("recognition/{name}.json"))).unwrap();
        model::recognise(&part, &mut a, &json).unwrap();
        let plan = model::plan(&a, &Spec::default()).unwrap();
        let drawing = model::render(&part, &a, &plan, &font()).unwrap();
        out.push((name.to_string(), a, plan, drawing));
    }
    if let Ok(corpus) = std::env::var("DRAFTWRIGHT_CORPUS") {
        let path = Path::new(&corpus).join("gramel/string_post.step");
        if path.is_file() {
            let (part, a) = model::analyse_native(&path).unwrap();
            let plan = model::plan(&a, &Spec::default()).unwrap();
            let drawing = model::render(&part, &a, &plan, &font()).unwrap();
            out.push(("string_post".into(), a, plan, drawing));
        } else {
            assert!(
                std::env::var("DRAFTWRIGHT_CORPUS_REQUIRED").is_err(),
                "string_post is missing from the corpus"
            );
        }
    }
    out
}

fn claims(d: &Drawing, parameter: &str) -> usize {
    d.annotations
        .iter()
        .filter(|a| a.claims.iter().any(|c| c.parameter == parameter))
        .filter(|a| {
            d.sheet
                .items
                .iter()
                .any(|i| i.name == a.name && i.elbow.is_some())
        })
        .count()
}

/// ISSUE-1378-a and ISSUE-1479-a met by the planner, and the router and lint agreeing
/// (ISSUE-798-d): on the port's automatic drawings no hole or blend callout's tip misses its
/// target, and the callouts the plan reports cutting back across their view are exactly those
/// lint reports crossing the silhouette (none on these drawings: the router finds every leader
/// a route that does not cut back; `the_plan_and_lint_agree_on_a_leader_cutting_back_into_the_
/// material` is the case where both report one).
#[test]
fn the_ports_own_callouts_touch_their_targets() {
    let (mut holes, mut blends) = (0, 0);
    for (name, a, plan, drawing) in drawings() {
        let issues = lint::lint(&a, &drawing);
        let target: Vec<&LintIssue> = issues
            .iter()
            .filter(|i| i.code.contains("_leader_target_") || i.code.contains("_witness_target_"))
            .collect();
        assert!(target.is_empty(), "{name}: {target:#?}");
        holes += claims(&drawing, "bore.diameter");
        blends += claims(&drawing, "blend.radius");
        let planned: BTreeSet<&str> = plan
            .problems
            .iter()
            .filter(|p| p.message.contains("runs back across its own view"))
            .map(|p| p.subjects[0].as_str())
            .collect();
        let linted: BTreeSet<&str> = issues
            .iter()
            .filter(|i| i.code == "leader_crosses_silhouette")
            .filter_map(|i| i.annotation.as_deref())
            .collect();
        assert_eq!(planned, linted, "{name}");
    }
    // Eight across the fixtures and string_post (a counted callout names several holes):
    // boss_circle's six holes are one callout since #223 (its 24.3 × 24.38 "circle" was two).
    assert!(holes >= 8, "{holes} hole callouts checked");
    if std::env::var("DRAFTWRIGHT_CORPUS").is_ok() {
        assert!(blends >= 1, "{blends} blend callouts checked");
    }
}

/// ISSUE-798-d on a leader that does cut back into the material: the plate-and-pin assembly's
/// pin callout pinned with its bend out past the plate's edge, so its shaft leaves the pin and
/// crosses the plate. The plan (the router's measure, reported for a pinned leader it did not
/// route) and lint name the same callout with the same depth; pinned the other way, clear of
/// the plate, neither reports it.
#[test]
fn the_plan_and_lint_agree_on_a_leader_cutting_back_into_the_material() {
    use std::io::Read as _;
    const ID: &str = "d:f:turned_step:1/step.diameter";
    let mut bytes = Vec::new();
    flate2::read::GzDecoder::new(
        std::fs::File::open(fixture("pmi/step/assembly_plate_pin.step.gz")).unwrap(),
    )
    .read_to_end(&mut bytes)
    .unwrap();
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR")).join("leader_rules");
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("assembly_plate_pin.step");
    std::fs::write(&path, bytes).unwrap();
    let reports = |offset: [f64; 2]| -> (Vec<model::Problem>, Vec<LintIssue>, model::Plan) {
        let mut s = Session::open(&path, api::Features::Native, Spec::default()).unwrap();
        s.apply(
            &[Op::set(
                format!("dimensions/{}/place", key(ID)),
                json!({"offset": offset, "pinned": true}),
            )],
            true,
        )
        .unwrap();
        let planned = s
            .plan()
            .problems
            .iter()
            .filter(|p| p.code == "annotation_line_over_view")
            .filter(|p| p.subjects.first().map(String::as_str) == Some(ID))
            .cloned()
            .collect();
        let linted = lint::lint(s.analysis(), s.drawing())
            .into_iter()
            .filter(|i| i.code == "leader_crosses_silhouette")
            .collect();
        (planned, linted, s.plan().clone())
    };
    let depth = |text: &str, before: &str| -> String {
        let at = text.find(before).unwrap_or_else(|| panic!("{text}")) + before.len();
        text[at..].split(' ').next().unwrap().to_string()
    };
    // Down as well as out: the inclined pin's own callouts (ø10, ø6, ⌀4.2 ↧ 8) share the view and
    // move its default place, so straight out from there now clears the plate.
    let (planned, linted, plan) = reports([15.0, -15.0]);
    assert_eq!(planned.len(), 1, "{planned:#?}");
    assert_eq!(linted.len(), 1, "{linted:#?}");
    assert_eq!(linted[0].annotation.as_deref(), Some(ID));
    let (p, l) = (
        depth(&planned[0].message, "cutting "),
        depth(&linted[0].message, "cuts "),
    );
    assert_eq!(p, l, "{} / {}", planned[0].message, linted[0].message);
    assert!(p.parse::<f64>().unwrap() > 1.0, "{p}");
    // The pin is the author's: the fix offered is to release it, locally with the layout kept
    // or, when keeping the layout is not offered (as since haecceity v0.2.7's one-line
    // deviations narrowed the view's callouts), at sheet scope.
    let releases: Vec<_> = if planned[0].offered_fixes()[0].scope() == Scope::Local {
        local_fix::edits(&planned[0], &plan)
    } else {
        planned[0]
            .fix
            .iter()
            .filter(|op| !local_fix::constraint(op))
            .collect()
    };
    assert_eq!(releases.len(), 1, "{:?}", planned[0].fix);
    assert_eq!(
        releases[0].path,
        format!("dimensions/{}/place", key(ID)),
        "{:?}",
        planned[0].fix
    );
    let (planned, linted, _) = reports([-15.0, -15.0]);
    assert!(
        planned.is_empty() && linted.is_empty(),
        "{planned:#?} {linted:#?}"
    );
}

/// Build the native regression part through the public analysis and rendering APIs.
fn drawing_10000() -> Option<(Analysis, model::Plan, Drawing)> {
    native_drawing("mfcadpp/10000.step", false)
}

fn native_drawing(file: &str, recovered: bool) -> Option<(Analysis, model::Plan, Drawing)> {
    let corpus = std::env::var("DRAFTWRIGHT_CORPUS")
        .unwrap_or_else(|_| "/Users/paul/repos/quiddity/tests/corpus".into());
    let path = Path::new(&corpus).join(file);
    if !path.is_file() {
        assert!(std::env::var("DRAFTWRIGHT_CORPUS_REQUIRED").is_err());
        return None;
    }
    let (part, a) = cache::native(&path);
    let plan = cache::default_plan(&a, recovered);
    let drawing = model::render(&part, &a, &plan, &font()).unwrap();
    Some((a, plan, drawing))
}

#[test]
fn circular_step_radius_tip_follows_the_trimmed_arc_10000() {
    let Some((a, plan, drawing)) = drawing_10000() else {
        return;
    };
    let radius = plan
        .dimensions
        .iter()
        .find(|d| d.id.ends_with("/circular_step_radius.radius"))
        .unwrap();
    let p = radius.placed.as_ref().unwrap();
    let tip = p.lines[0][0];
    let frame = &drawing.sheet.frames["side"];
    let view = draftwright::haecceity::hlr::View::new(frame.direction, frame.up).unwrap();
    let centre = view.map([0, 1, 2].map(|k| (a.envelope[0][k] + a.envelope[1][k]) / 2.0));
    let page = |q| {
        let q = view.map(q);
        [0, 1].map(|k| frame.at[k] + frame.scale * (q[k] - centre[k]))
    };
    let feature = &radius.measurements[0].feature;
    assert!(
        a.profile_arcs[feature].iter().any(|arc| {
            // Check against the actual edge's parameter interval, without using
            // the placement's span clamp or lint's target predicate.
            let c = page(arc.centre);
            if ((tip[0] - c[0]).hypot(tip[1] - c[1]) - arc.radius * frame.scale).abs() > 0.002 {
                return false;
            }
            let points: Vec<_> = (0..=1000)
                .map(|i| page(arc.point(arc.start + arc.sweep * f64::from(i) / 1000.0)))
                .collect();
            points
                .iter()
                .any(|q| (q[0] - tip[0]).hypot(q[1] - tip[1]) < 0.02)
        }),
        "tip {tip:?} must touch the trimmed quarter arc"
    );
    assert!(
        !lint::lint(&a, &drawing)
            .iter()
            .any(|i| i.code.starts_with("radius_leader_target_"))
    );
    let mut broken = drawing.clone();
    let item = broken
        .sheet
        .items
        .iter_mut()
        .find(|d| d.name == radius.id)
        .unwrap();
    let arc = &a.profile_arcs[feature][0];
    let absent = page(arc.point(arc.start + arc.sweep + std::f64::consts::FRAC_PI_4));
    item.lines[0][0] = absent;
    assert!(
        lint::lint(&a, &broken)
            .iter()
            .any(|i| i.code == "radius_leader_target_mismatch"
                && i.annotation.as_deref() == Some(radius.id.as_str()))
    );
}

#[test]
fn hole_location_uses_perpendicular_witnesses_and_rejects_oblique_crossings_10000() {
    let Some((_, plan, drawing)) = drawing_10000() else {
        return;
    };
    let location = plan
        .dimensions
        .iter()
        .find(|d| d.id == "d:f:hole:1/location.length.y")
        .unwrap();
    assert_eq!(location.view, "v:side");
    let p = location.placed.as_ref().unwrap();
    assert!(
        p.lines[..2]
            .iter()
            .all(|l| (l[1][0] - l[0][0]).abs() < 1e-9),
        "17.5 must have vertical witnesses: {p:?}"
    );
    assert!(
        !lint::structural::check(&drawing)
            .iter()
            .any(|i| i.code == "oblique_witness_crossing")
    );

    let mut broken = drawing.clone();
    let item = broken
        .sheet
        .items
        .iter_mut()
        .find(|d| d.name == location.id)
        .unwrap();
    for line in item.lines.iter_mut().take(2) {
        line[1][0] += (line[1][1] - line[0][1]) / 3.0_f64.sqrt();
    }
    assert!(
        lint::structural::check(&broken)
            .iter()
            .any(|i| i.code == "oblique_witness_crossing"
                && i.annotation.as_deref() == Some(location.id.as_str()))
    );
}

/// Rounded slot dimensions must not discard their exact cylindrical boundary evidence.
#[test]
fn rounded_slot_radii_keep_their_exact_arcs_on_nist_parts() {
    let corpus = std::env::var("DRAFTWRIGHT_CORPUS")
        .unwrap_or_else(|_| "/Users/paul/repos/quiddity/tests/corpus".into());
    for name in ["nist_ctc_03_asme1_rc", "nist_ftc_09_asme1_rd"] {
        let path = Path::new(&corpus).join(format!("nist/{name}.stp"));
        if !path.is_file() {
            assert!(std::env::var("DRAFTWRIGHT_CORPUS_REQUIRED").is_err());
            return;
        }
        let (part, a) = model::analyse_native(&path).unwrap();
        let mut rounded = false;
        for f in a.features.iter().filter(|f| f.kind == "slot") {
            let arcs = a.profile_arcs.get(&f.id).expect("slot has boundary arcs");
            assert!(!arcs.is_empty());
            let radii: Vec<_> = f
                .faces
                .iter()
                .filter_map(|&i| {
                    if let draftwright::haecceity::geom::Surface::Cylinder { radius, .. } =
                        part.faces[i].surface
                    {
                        Some(radius)
                    } else {
                        None
                    }
                })
                .collect();
            assert!(
                arcs.iter()
                    .all(|arc| radii.iter().any(|r| (r - arc.radius).abs() < 1e-10))
            );
            let nominal = f.parameters["end_radius"].as_f64().unwrap();
            rounded |= arcs.iter().any(|arc| (arc.radius - nominal).abs() > 0.002);
        }
        assert!(rounded, "exercise the rounded-radius evidence gap");
        let plan = model::plan(&a, &Spec::default()).unwrap();
        let drawing = model::render(&part, &a, &plan, &font()).unwrap();
        let issues: Vec<_> = lint::lint(&a, &drawing)
            .into_iter()
            .filter(|i| {
                i.code.starts_with("radius_leader_target_")
                    && i.annotation
                        .as_deref()
                        .is_some_and(|id| id.ends_with("/slot_end_radius.radius"))
            })
            .collect();
        assert!(issues.is_empty(), "{name}: {issues:?}");
    }
}

/// The exported drawing keeps its established counted leaders, rather than an
/// untagged centre table, and each representative tip touches physical arc ink.
#[test]
fn recovered_ctc02_keeps_counted_radius_leaders_on_trimmed_arcs() {
    let Some((a, plan, drawing)) = native_drawing("nist/nist_ctc_02_asme1_rc.stp", true) else {
        return;
    };
    assert!(plan.radius_table.is_none());
    assert_eq!(
        (plan.sheet.size.as_str(), plan.sheet.scale.as_str()),
        ("A2", "1:5")
    );
    for (label, count) in [("4× R15", 4), ("6× R18", 6)] {
        let d = plan
            .dimensions
            .iter()
            .find(|d| d.label == label)
            .expect(label);
        assert_eq!(d.view, "v:plan");
        assert_eq!(d.measurements.len(), count);
        let tip = d.placed.as_ref().expect("visible counted leader").lines[0][0];
        let frame = &drawing.sheet.frames["plan"];
        let view = draftwright::haecceity::hlr::View::new(frame.direction, frame.up).unwrap();
        let centre = view.map([0, 1, 2].map(|k| (a.envelope[0][k] + a.envelope[1][k]) / 2.0));
        assert!(
            d.measurements.iter().any(|m| {
                a.radius_arcs.get(&m.feature).is_some_and(|arcs| {
                    arcs.iter().any(|arc| {
                        let aligned: f64 = arc
                            .axis
                            .iter()
                            .zip(frame.direction)
                            .map(|(x, y)| x * y)
                            .sum();
                        if aligned.abs() < 1.0 - 1e-9 {
                            return false;
                        }
                        let page = |q| {
                            let q = view.map(q);
                            [0, 1].map(|k| frame.at[k] + frame.scale * (q[k] - centre[k]))
                        };
                        let c = page(arc.arc.centre);
                        let u = page([0, 1, 2].map(|k| arc.arc.centre[k] + arc.arc.u[k]));
                        let v = page([0, 1, 2].map(|k| arc.arc.centre[k] + arc.arc.v[k]));
                        let delta = [tip[0] - c[0], tip[1] - c[1]];
                        let angle = (delta[0] * (v[0] - c[0]) + delta[1] * (v[1] - c[1]))
                            .atan2(delta[0] * (u[0] - c[0]) + delta[1] * (u[1] - c[1]));
                        let along = (angle - arc.arc.start).rem_euclid(std::f64::consts::TAU);
                        let r = frame.scale * arc.arc.radius;
                        ((tip[0] - c[0]).hypot(tip[1] - c[1]) - r).abs() < 0.002
                            && (along <= arc.arc.sweep + 0.002 / r
                                || along >= std::f64::consts::TAU - 0.002 / r)
                    })
                })
            }),
            "{label} tip {tip:?} must touch a proven trimmed surface site"
        );
    }
    // No radius tip misses its arc. Since quiddity-rust v0.2.2 (#120) the `16× R25` callout reads
    // in the plan, where seven of its fillets lie along z; those are cast fillets drafted 2° off
    // z, so no section is exactly end-on and lint can only call their tip unverifiable (an
    // advisory finding), not mismatched.
    let found = lint::lint(&a, &drawing);
    assert!(
        !found
            .iter()
            .any(|i| i.code == "radius_leader_target_mismatch"),
        "{found:#?}"
    );
    let unverifiable: Vec<_> = found
        .iter()
        .filter(|i| i.code.starts_with("radius_leader_target_"))
        .map(|i| i.annotation.as_deref().unwrap_or_default())
        .collect();
    assert!(
        unverifiable
            .iter()
            // #127 inserts concave rounds before the same drafted R25 group.
            .all(|n| *n == "d:f:fillet:51/fillet.radius"),
        "{unverifiable:?}"
    );
}

/// Perpendicular witness repair must not displace the existing hole schedule
/// onto a larger sheet at the same geometry scale.
#[test]
fn recovered_ftc09_keeps_a1_and_perpendicular_witnesses() {
    let corpus = std::env::var("DRAFTWRIGHT_CORPUS")
        .unwrap_or_else(|_| "/Users/paul/repos/quiddity/tests/corpus".into());
    let path = Path::new(&corpus).join("nist/nist_ftc_09_asme1_rd.stp");
    if !path.is_file() {
        assert!(std::env::var("DRAFTWRIGHT_CORPUS_REQUIRED").is_err());
        return;
    }
    let (part, mut a) = cache::native(&path);
    // Its congruent slots 1 and 2, called out together, drop the slot radius leader whose
    // crowding made this case: slot 2 is split from their callout (`features/<id>/split`).
    let slot = a.features.iter_mut().find(|f| f.id == "f:slot:2").unwrap();
    slot.parameters.insert(
        draftwright::lint::slots::ALONE.into(),
        serde_json::Value::Bool(true),
    );
    let plan = cache::default_plan(&a, true);
    let drawing = model::render(&part, &a, &plan, &font()).unwrap();
    assert_eq!(
        (plan.sheet.size.as_str(), plan.sheet.scale.as_str()),
        ("A1", "1:1")
    );
    assert!(plan.radius_table.is_none());
    let table = plan
        .hole_tables
        .iter()
        .find(|t| t.id == "hole_schedule_front")
        .expect("established hole schedule");
    assert_eq!(table.rows.len(), 30);
    let iso = plan.views.iter().find(|v| v.id == "v:iso").unwrap();
    assert!(
        (iso.scale - 1.3).abs() < 1e-9,
        "retain the established orientation scale"
    );
    for k in 0..2 {
        assert!(iso.at[k] + iso.scale * a.iso_bounds[k] >= iso.area[k] - 1e-6);
        assert!(iso.at[k] + iso.scale * a.iso_bounds[k + 2] <= iso.area[k + 2] + 1e-6);
        assert!(iso.at[k] + iso.scale * a.iso_bounds[k] >= draftwright::layout::margin() + 2.0);
        assert!(
            iso.at[k] + iso.scale * a.iso_bounds[k + 2]
                <= plan.sheet.page[k] - draftwright::layout::margin() - 2.0
        );
    }
    for d in plan.dimensions.iter().filter(|d| d.kind == "linear") {
        let Some(p) = d.placed.as_ref() else { continue };
        assert_eq!(p.arrows.len(), 2);
        let [a, b] = [p.arrows[0][0], p.arrows[1][0]];
        let axis = [b[0] - a[0], b[1] - a[1]];
        for [from, to] in p.lines.iter().take(2) {
            let dot = (to[0] - from[0]) * axis[0] + (to[1] - from[1]) * axis[1];
            assert!(
                dot.abs() < 1e-6,
                "{} must have perpendicular witnesses",
                d.id
            );
        }
    }
    assert!(
        !lint::structural::check(&drawing)
            .iter()
            .any(|i| i.code == "oblique_witness_crossing")
    );
}

/// #145: the real front-view R5 remains drawn and has a radial shaft from either side.
/// The solid-box silhouette test deliberately cannot see its original boss crossing.
#[test]
fn ctc01_r5_meets_the_fillet_from_the_open_centre_side() {
    let corpus = std::env::var("DRAFTWRIGHT_CORPUS")
        .unwrap_or_else(|_| "/Users/paul/repos/quiddity/tests/corpus".into());
    let path = Path::new(&corpus).join("nist/nist_ctc_01_asme1_rd.stp");
    if !path.is_file() {
        assert!(std::env::var("DRAFTWRIGHT_CORPUS_REQUIRED").is_err());
        return;
    }
    let (part, a) = model::analyse_native(&path).unwrap();
    let p = model::plan(&a, &Spec::default()).unwrap();
    let d = p.dimensions.iter().find(|d| d.label == "6× R5").unwrap();
    assert_eq!(d.view, "v:front");
    assert_eq!(d.measurements.len(), 6);
    let placed = d.placed.as_ref().expect("never drop the callout");
    let view = p.views.iter().find(|v| v.id == d.view).unwrap();
    let projection = draftwright::haecceity::hlr::View::new(view.direction, view.up).unwrap();
    let middle = projection.map([0, 1, 2].map(|k| (a.envelope[0][k] + a.envelope[1][k]) / 2.0));
    let page = |q| {
        let q = projection.map(q);
        [0, 1].map(|k| view.at[k] + view.scale * (q[k] - middle[k]))
    };
    let [tip, elbow] = placed.lines[0];
    assert!(
        d.measurements
            .iter()
            .any(|m| a
                .radius_arcs
                .get(&m.feature)
                .is_some_and(|arcs| arcs.iter().any(|arc| {
                    let aligned: f64 = arc
                        .axis
                        .iter()
                        .zip(view.direction)
                        .map(|(x, y)| x * y)
                        .sum();
                    if aligned.abs() < 1.0 - 1e-9 {
                        return false;
                    }
                    let c = page(arc.arc.centre);
                    let u = [c[0] - tip[0], c[1] - tip[1]];
                    let v = [elbow[0] - tip[0], elbow[1] - tip[1]];
                    (u[0] * v[0] + u[1] * v[1]).abs()
                        >= 15.0f64.to_radians().cos() * u[0].hypot(u[1]) * v[0].hypot(v[1]) - 1e-9
                })))
    );
    let drawing = model::render(&part, &a, &p, &font()).unwrap();
    assert!(
        !lint::lint(&a, &drawing)
            .iter()
            .any(|i| i.annotation.as_deref() == Some(d.id.as_str())
                && i.code.starts_with("radius_leader_"))
    );
    let convex = p.dimensions.iter().find(|d| d.label == "7× R50").unwrap();
    assert_eq!(
        convex
            .measurements
            .iter()
            .filter(|m| m.parameter == "fillet.radius")
            .count(),
        7
    );
    assert!(convex.placed.is_some(), "retain the plate corner radius");
    assert!(
        !lint::lint(&a, &drawing)
            .iter()
            .any(|i| i.annotation.as_deref() == Some(convex.id.as_str())
                && i.code.starts_with("radius_leader_")),
        "convex R50 must approach radially from either side"
    );
    let material = draftwright::material::solids(&a, page);
    // One initial interval is intentionally exempt even when it runs through a boss.
    let box_centre = [0, 1].map(|k| (material[0][k] + material[0][k + 2]) / 2.0);
    assert_eq!(
        draftwright::material::reentry(
            &material,
            [box_centre, [material[0][2] + 10.0, box_centre[1]]]
        ),
        0.0
    );
}

/// Locality also holds at the recovered planner used by sessions and the CLI,
/// independently of an earlier plan/cache entry for the unedited document.
#[test]
fn recovered_radius_routes_survive_unrelated_annotation_edits() {
    let Some(corpus) = std::env::var_os("DRAFTWRIGHT_CORPUS") else {
        return;
    };
    let path = Path::new(&corpus).join("nist/nist_ctc_01_asme1_rd.stp");
    let (_, a) = model::analyse_native(&path).unwrap();
    for edit in [json!({"omit":true}), json!({"tolerance":"±0.05"})] {
        let spec: Spec = serde_json::from_value(json!({"dimensions":{
            "d:f:hole_pattern:1/location.length.x": edit
        }}))
        .unwrap();
        // Plan the edited document first: locality is not a cache-history policy.
        let edited = model::plan_recovered(&a, &spec).unwrap();
        let full = model::plan_recovered(&a, &Spec::default()).unwrap();
        let mut compared = 0;
        for before in full.dimensions.iter().filter(|d| {
            d.placed.is_some()
                && d.measurements
                    .iter()
                    .any(|m| matches!(m.parameter.as_str(), "fillet.radius" | "blend.radius"))
        }) {
            let after = edited
                .dimensions
                .iter()
                .find(|d| d.id == before.id)
                .unwrap();
            assert_eq!(
                after.measurements, before.measurements,
                "{} claims",
                before.id
            );
            assert_eq!(after.view, before.view, "{} carrier", before.id);
            let (old, new) = (
                before.placed.as_ref().unwrap(),
                after.placed.as_ref().unwrap(),
            );
            let prior_view = full.views.iter().find(|v| v.id == before.view).unwrap();
            let current_view = edited.views.iter().find(|v| v.id == after.view).unwrap();
            assert_eq!(old.lines.len(), new.lines.len());
            assert_eq!(old.arrows.len(), new.arrows.len());
            for (p, q) in old
                .lines
                .iter()
                .chain(&old.arrows)
                .flatten()
                .zip(new.lines.iter().chain(&new.arrows).flatten())
            {
                for k in 0..2 {
                    assert!(
                        (p[k] - prior_view.at[k] - q[k] + current_view.at[k]).abs() < 1e-9,
                        "{} route moved for {spec:?}",
                        before.id
                    );
                }
            }
            for k in 0..4 {
                assert!(
                    (old.label_box[k] - prior_view.at[k % 2] - new.label_box[k]
                        + current_view.at[k % 2])
                        .abs()
                        < 1e-9,
                    "{} label moved for {spec:?}",
                    before.id
                );
            }
            compared += 1;
        }
        assert!(compared >= 2);
    }
}

/// #145: convex-corner repair cannot put text under the schedule's origin leader.
#[test]
fn ctc04_radius_labels_keep_the_schedule_origin_leader_clear() {
    let Some((_, p, _)) = native_drawing("nist/nist_ctc_04_asme1_rd.stp", true) else {
        return;
    };
    let origin = p
        .balloons
        .iter()
        .find(|b| b.id == "hole_schedule_plan/origin")
        .expect("retain the schedule origin")
        .leader
        .unwrap();
    let d = p.dimensions.iter().find(|d| d.label == "8× R25").unwrap();
    let b = d.placed.as_ref().expect("retain the radius").label_box;
    let inside = |q: P| q[0] > b[0] && q[0] < b[2] && q[1] > b[1] && q[1] < b[3];
    let side = |a: P, b: P, c: P| (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
    let corners = [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]];
    assert!(!origin.into_iter().any(inside));
    for i in 0..4 {
        let edge = [corners[i], corners[(i + 1) % 4]];
        assert!(
            !(side(origin[0], origin[1], edge[0]) * side(origin[0], origin[1], edge[1]) < -1e-12
                && side(edge[0], edge[1], origin[0]) * side(edge[0], edge[1], origin[1]) < -1e-12)
        );
    }
}

/// A retained automatic route has only the drawn finding, never a duplicate plan warning.
#[test]
fn retained_radius_routes_have_one_finding_per_callout() {
    let Some((a, p, draw)) = native_drawing("mfcadpp_holdout/363.step", false) else {
        return;
    };
    assert!(
        !p.problems
            .iter()
            .any(|i| i.code == "radius_leader_routing_fallback")
    );
    let mut counts = BTreeMap::new();
    for warning in lint::lint(&a, &draw)
        .into_iter()
        .filter(|i| i.code.starts_with("radius_leader_"))
    {
        let id = warning.annotation.unwrap();
        assert!(
            p.dimensions
                .iter()
                .any(|d| d.id == id && d.placed.is_some())
        );
        *counts.entry(id).or_insert(0) += 1;
    }
    assert!(counts.values().all(|&count| count == 1));
}

/// Toroidal meridians come from real trimmed faces, with exact minor-radius centres.
#[test]
fn toroidal_meridian_evidence_stays_on_the_trimmed_minor_circles() {
    let (part, mut a) = model::analyse(&fixture("parts/turned.step")).unwrap();
    let recognition = std::fs::read_to_string(fixture("recognition/turned.json")).unwrap();
    model::recognise(&part, &mut a, &recognition).unwrap();
    let mut checked = 0;
    for f in a.features.iter().filter(|f| f.kind == "fillet") {
        let tori: Vec<_> = f
            .faces
            .iter()
            .copied()
            .filter(|&i| {
                matches!(
                    part.faces[i].surface,
                    draftwright::haecceity::geom::Surface::Torus { .. }
                )
            })
            .collect();
        if tori.len() != f.faces.len() {
            continue;
        }
        for section in a.radius_arcs.get(&f.id).into_iter().flatten() {
            assert!(!section.tips.is_empty());
            for &tip in &section.tips {
                let delta = [0, 1, 2].map(|k| tip[k] - section.arc.centre[k]);
                assert!(
                    (delta.iter().map(|x| x * x).sum::<f64>().sqrt() - section.arc.radius).abs()
                        < 1e-9
                );
                assert!(
                    delta
                        .iter()
                        .zip(section.axis)
                        .map(|(x, y)| x * y)
                        .sum::<f64>()
                        .abs()
                        < 1e-9
                );
                assert!(tori.iter().any(|&i| {
                    let surface = &part.faces[i].surface;
                    let Some((u0, u1, v0, v1)) = part.uv_bounds(i) else {
                        return false;
                    };
                    let Some((u, v)) =
                        surface.parameters(tip, Some(((u0 + u1) / 2.0, (v0 + v1) / 2.0)))
                    else {
                        return false;
                    };
                    let q = surface.value(u, v);
                    q.iter().zip(tip).all(|(x, y)| (x - y).abs() < 1e-9)
                        && part.domain(i).unwrap().contains(u, v)
                }));
            }
            checked += 1;
        }
    }
    assert!(checked > 0, "exercise readable toroidal minor circles");
}

/// A turned step along z, ø*diameter* from z −5 to 5, and the two flats *across* apart that
/// cut it over its whole span (none when *across* is None).
fn flatted_band(diameter: f64, across: Option<f64>) -> Vec<Value> {
    let mut out = vec![feature(
        "f:turned_step:1",
        "turned_step",
        json!({ "axis": "z", "diameter": diameter, "lo": -5.0, "hi": 5.0,
                "profile": { "axis": "z", "axis_origin": [0.0, 0.0, 0.0] } }),
    )];
    if let Some(across) = across {
        for (i, x) in [-0.5 * across, 0.5 * across].into_iter().enumerate() {
            out.push(feature(
                &format!("f:flat:{}", i + 1),
                "flat",
                json!({ "across": across, "at": [x, 0.0, 0.0], "axis": "z",
                        "axis_direction": [0.0, 0.0, 1.0], "axis_distance": 0.5 * across,
                        "axis_line": [0.0, 0.0], "opposed": true, "stock_span": [-5.0, 5.0],
                        "flat_plane": { "at": [x, 0.0, -5.0], "normal": [1.0, 0.0, 0.0] } }),
            ));
        }
    }
    out
}

/// ISSUE-1374-d, ARCH0017-6: a turned step's diameter tip lies on the band's own drawn cylinder.
/// Where flats cut the band (string_post's ø3.4, ctc05's flange), the outline across them is the
/// trimmed cylinder's edge on the flat, not the full circle's extreme in air; across the uncut
/// direction, and seen along the axis on the arcs left between the flats, the cylinder itself.
#[test]
fn a_step_diameter_tip_lies_on_the_bands_trimmed_outline() {
    let check = |across: Option<f64>, direction: [f64; 3], tip: P| {
        let a = analysis(flatted_band(3.4, across), json!({}), &[]);
        found(
            &a,
            &drawing(
                direction,
                vec![callout(
                    "d:d",
                    &[("f:turned_step:1", "step.diameter")],
                    tip,
                    [130.0, 110.0],
                )],
            ),
            "step_diameter_leader",
        )
    };
    let mismatch = one("step_diameter_leader_target_mismatch", "d:d");
    let front = [0.0, -1.0, 0.0];
    let side = [1.0, 0.0, 0.0];
    for s in [-1.0, 1.0] {
        // Uncut: the full circle's extreme.
        assert!(check(None, front, page(s * 1.7, 0.0)).is_empty());
        // Cut across x: the edge on the flat, anywhere along the band; not the circle in air.
        assert!(check(Some(2.4), front, page(s * 1.2, 0.0)).is_empty());
        assert!(check(Some(2.4), front, page(s * 1.2, 3.0)).is_empty());
        assert_eq!(check(Some(2.4), front, page(s * 1.7, 0.0)), mismatch);
        // The side view (y up the page here) shows y, where the cylinder is whole.
        assert!(check(Some(2.4), side, page(0.0, s * 1.7)).is_empty());
        assert_eq!(check(Some(2.4), side, page(0.0, s * 1.2)), mismatch);
        // Along the axis: the arcs between the flats, not where the flats cut the circle away.
        assert!(check(Some(2.4), ALONG_Z, page(0.0, s * 1.7)).is_empty());
        assert_eq!(check(Some(2.4), ALONG_Z, page(s * 1.7, 0.0)), mismatch);
    }
    // Beyond the band's span.
    assert_eq!(check(Some(2.4), front, page(1.2, 7.0)), mismatch);
}

/// ISSUE-1374-a, ISSUE-1276-a: a turned chamfer's tip lies on its bevel's profile, turned about
/// its coaxial band onto the outline (ctc05's 4× C6.35 tipped at the shaft axis before); a
/// prismatic bevel in a view that does not see its edge end-on cannot be checked.
#[test]
fn a_chamfer_tip_lies_on_its_bevel() {
    let check = |chamfer: Value, direction: [f64; 3], tip: P| {
        let mut features = flatted_band(10.0, None);
        features.push(chamfer);
        let a = analysis(features, json!({}), &[]);
        found(
            &a,
            &drawing(
                direction,
                vec![callout(
                    "d:c",
                    &[("f:chamfer:1", "chamfer.length")],
                    tip,
                    [130.0, 110.0],
                )],
            ),
            "chamfer_leader",
        )
    };
    let turned = feature(
        "f:chamfer:1",
        "chamfer",
        json!({ "angle": 45.0, "at": [0.0, -4.5, 4.5], "axis": "z", "corner": null,
                "leg1": 1.0, "leg2": 1.0, "turned": true }),
    );
    let front = [0.0, -1.0, 0.0];
    for s in [-1.0, 1.0] {
        assert!(check(turned.clone(), front, page(s * 4.5, 4.5)).is_empty());
        assert!(check(turned.clone(), front, page(s * 4.75, 4.25)).is_empty());
    }
    // At the axis, inside the band: the record's own point, seen through the shaft.
    assert_eq!(
        check(turned.clone(), front, page(0.0, 4.5)),
        one("chamfer_leader_target_mismatch", "d:c")
    );
    let prismatic = feature(
        "f:chamfer:1",
        "chamfer",
        json!({ "angle": 45.0, "at": [0.0, 9.5, 9.5], "axis": "x", "corner": null,
                "leg1": 1.0, "leg2": 1.0, "turned": false }),
    );
    assert_eq!(
        check(prismatic, ALONG_Z, page(0.0, 9.5)),
        one("chamfer_leader_target_unverifiable", "d:c")
    );
}

/// A callout counting members lint can measure and members it cannot (cgb243's 36× C0.5: turned
/// bevels with no band, prismatic ones seen end-on) is a mismatch only when every member was
/// measured; a tip on an unmeasurable member's bevel is unverifiable, not a miss.
#[test]
fn a_chamfer_tip_missing_only_the_measurable_members_is_unverifiable() {
    let turned = feature(
        "f:chamfer:1",
        "chamfer",
        json!({ "angle": 45.0, "at": [4.5, 2.0, 0.0], "axis": "y", "corner": null,
                "leg1": 1.0, "leg2": 1.0, "turned": true }),
    );
    let prismatic = feature(
        "f:chamfer:2",
        "chamfer",
        json!({ "angle": 45.0, "at": [0.0, 9.5, 0.0], "axis": "z", "corner": null,
                "leg1": 1.0, "leg2": 1.0, "turned": false }),
    );
    let check = |claims: &[(&str, &str)]| {
        let mut features = flatted_band(10.0, None);
        features.extend([turned.clone(), prismatic.clone()]);
        let a = analysis(features, json!({}), &[]);
        found(
            &a,
            &drawing(
                ALONG_Z,
                vec![callout("d:c", claims, page(4.5, 2.0), [130.0, 110.0])],
            ),
            "chamfer_leader",
        )
    };
    assert_eq!(
        check(&[
            ("f:chamfer:1", "chamfer.length"),
            ("f:chamfer:2", "chamfer.length")
        ]),
        one("chamfer_leader_target_unverifiable", "d:c")
    );
    // With every member measured, the same tip is a miss.
    assert_eq!(
        check(&[("f:chamfer:2", "chamfer.length")]),
        one("chamfer_leader_target_mismatch", "d:c")
    );
}

/// The real defects (ARCH0017-6, ISSUE-1374-a/d, ISSUE-1276-a, E4): string_post's ø3.4 and
/// ctc05's ø558.8 lead to their flat-cut bands' drawn outlines (ctc05's in the side view, where
/// the flange is the cylinder), ctc05's 4× C6.35 to its bevel, ftc10's 41× R1 and cgb202's two
/// R callouts to fillet arcs; lint finds no turned-diameter, chamfer or off-arc radius tip on
/// them. E4 stays open: the fillet shafts may still approach more than 15 degrees off radial
/// (`radius_leader_not_radial`, radial wording), since automatic routing keeps the drawn route
/// when no compliant radial candidate fits (#146); only the tip-off-arc form is refused here.
#[test]
fn flat_cut_bands_chamfers_and_fillets_lead_to_their_own_faces() {
    for (file, checks) in [
        (
            "gramel/string_post.step",
            &["d:f:turned_step:4/step.diameter"][..],
        ),
        (
            "nist/nist_ctc_05_asme1_rd.stp",
            &[
                "d:f:turned_step:1/step.diameter",
                "d:f:chamfer:1/chamfer.length",
            ][..],
        ),
        (
            "nist/nist_ftc_10_asme1_rb.stp",
            &["d:f:fillet:21/fillet.radius"][..],
        ),
        ("cadgenbench_inputs/cgb202.step.gz", &[][..]),
    ] {
        let Some((a, plan, drawing)) = native_drawing(file, false) else {
            continue;
        };
        if file.contains("cgb202") {
            // The same physical fillets as before. v0.4.2 merges interrupted torus
            // groups and renumbers run-local IDs; member faces remain source evidence.
            for face in [1436, 2104] {
                let f = a
                    .features
                    .iter()
                    .find(|f| f.kind == "fillet" && f.faces.contains(&face))
                    .unwrap_or_else(|| panic!("{file}: fillet face {face} retained"));
                assert!(
                    plan.dimensions.iter().any(|d| d.placed.is_some()
                        && d.measurements
                            .iter()
                            .any(|m| m.feature == f.id && m.parameter == "fillet.radius")),
                    "{file}: face {face} has a drawn radius claim"
                );
            }
        }
        for id in checks {
            assert!(
                plan.dimensions
                    .iter()
                    .any(|d| d.id == *id && d.placed.is_some()),
                "{file}: {id} is placed"
            );
        }
        if file.contains("ctc_05") {
            let d = plan
                .dimensions
                .iter()
                .find(|d| d.id == "d:f:turned_step:1/step.diameter")
                .unwrap();
            assert_eq!(
                d.view, "v:side",
                "ctc05's flange reads where it is the cylinder"
            );
        }
        let bad: Vec<_> = lint::lint(&a, &drawing)
            .into_iter()
            .filter(|i| {
                i.code.starts_with("step_diameter_leader")
                    || i.code.starts_with("chamfer_leader")
                    // The tip off its arc; a shaft off radial is E4's open half.
                    || (i.code.starts_with("radius_leader")
                        && i.message.contains("tip does not touch"))
            })
            .map(|i| (i.code, i.annotation))
            .collect();
        assert!(bad.is_empty(), "{file}: {bad:?}");
    }
}

// ---- ARCH0017-6: flats, recesses and slots; ISSUE-1374-a/d: credit goes with the leader ----

/// A section recess record (quiddity's `SectionRecess`) of *kind* and *shape*, its *boundary*
/// (`[x, y, bulge]` in a frame whose run is +z, u +x and v +y) swept over z 6 to 10, its low end
/// capped or not and its high end open.
fn recess(
    id: &str,
    kind: &str,
    shape: &str,
    boundary: &[[f64; 3]],
    closed: bool,
    capped: bool,
) -> Value {
    let end = |capped: bool| {
        json!({ "condition": if capped { "capped" } else { "open" },
                "surface": { "type": "plane", "gradient": [0.0, 0.0] } })
    };
    let points: Vec<Value> = boundary
        .iter()
        .map(|[x, y, b]| json!({ "point": [x, y], "bulge": b }))
        .collect();
    let mut profile =
        json!({ "boundary": points, "closure": if closed { "closed" } else { "open" } });
    if !closed {
        let (first, last) = (boundary[0], boundary[boundary.len() - 1]);
        profile["opening"] = json!([[last[0], last[1]], [first[0], first[1]]]);
    }
    feature(
        id,
        "section_recess",
        json!({
            "classification": { "feature_kind": kind, "section_shape": shape },
            "evidence": { "defining_faces": [1], "constituent_faces": [1] },
            "geometry": {
                "type": "section_recess",
                "frame": { "origin": [0.0, 0.0, 0.0], "run": [0.0, 0.0, 1.0],
                           "u": [1.0, 0.0, 0.0], "v": [0.0, 1.0, 0.0] },
                "profile": profile,
                "run_interval": [6.0, 10.0],
                "ends": { "low": end(capped), "high": end(false) },
            },
        }),
    )
}

/// ARCH0017-6 for flats, recesses and slots: a callout's tip lies on the faces the record
/// publishes for the feature it names, never in an open mouth, an opening or empty space. A flat's
/// face is the chord its plane leaves on the band it cuts (on stock no band records, its plane
/// within the part's box); a recess's faces are its section's walls swept along its run, its floor
/// and capped ends; a through slot's, its outline swept through it (a slot pattern's, its member
/// slots'); an oriented slot's, its passage section swept along its run.
#[test]
fn flat_recess_and_slot_tips_lie_on_their_own_faces() {
    let check = |features: Vec<Value>,
                 claims: &[(&str, &str)],
                 direction: [f64; 3],
                 tip: P,
                 prefix: &str| {
        let a = analysis(features, json!({}), &[]);
        found(
            &a,
            &drawing(direction, vec![callout("d:x", claims, tip, [130.0, 110.0])]),
            prefix,
        )
    };
    let front = [0.0, -1.0, 0.0];
    // A flat on its band: the chord along the axis, edge-on from the front.
    let flat = |direction, tip| {
        check(
            flatted_band(3.4, Some(2.4)),
            &[("f:flat:1", "flat.length")],
            direction,
            tip,
            "flat_leader",
        )
    };
    let flat_miss = one("flat_leader_target_mismatch", "d:x");
    assert!(flat(ALONG_Z, page(-1.2, 0.5)).is_empty());
    assert_eq!(
        flat(ALONG_Z, page(-1.2, 1.5)),
        flat_miss,
        "past the chord, in air"
    );
    assert!(flat(front, page(-1.2, 3.0)).is_empty());
    assert_eq!(
        flat(front, page(-1.7, 0.0)),
        flat_miss,
        "the cut-away circle"
    );
    assert_eq!(flat(front, page(-1.2, 7.0)), flat_miss, "beyond its span");
    // A flat on stock no band records: its plane within the part's box.
    let plane = feature(
        "f:flat:1",
        "flat",
        json!({ "across": 10.0, "at": [5.0, 0.0, 0.0], "axis": "z", "axis_direction": [0.0, 0.0, 1.0],
                "axis_distance": 5.0, "axis_line": [0.0, 0.0], "opposed": false, "stock_span": [-5.0, 5.0],
                "flat_plane": { "at": [5.0, 0.0, -5.0], "normal": [1.0, 0.0, 0.0] } }),
    );
    let prismatic = |tip| {
        check(
            vec![plane.clone()],
            &[("f:flat:1", "flat.length")],
            ALONG_Z,
            tip,
            "flat_leader",
        )
    };
    assert!(prismatic(page(5.0, 3.0)).is_empty());
    assert_eq!(prismatic(page(6.0, 3.0)), flat_miss);

    // Recesses, seen down their run unless said.
    let rect = [
        [-3.0, -2.0, 0.0],
        [3.0, -2.0, 0.0],
        [3.0, 2.0, 0.0],
        [-3.0, 2.0, 0.0],
    ];
    let pocket =
        |closed_profile: &[[f64; 3]], kind: &str, closed: bool, capped: bool, direction, tip| {
            check(
                vec![recess(
                    "f:section_recess:1",
                    kind,
                    "rectangular",
                    closed_profile,
                    closed,
                    capped,
                )],
                &[("f:section_recess:1", "pocket_width.length")],
                direction,
                tip,
                "recess_leader",
            )
        };
    let recess_miss = one("recess_leader_target_mismatch", "d:x");
    // A blind pocket: its walls and its floor; outside it is not the pocket.
    assert!(pocket(&rect, "pocket", true, true, ALONG_Z, page(3.0, 0.0)).is_empty());
    assert!(pocket(&rect, "pocket", true, true, ALONG_Z, page(0.0, 0.0)).is_empty());
    assert_eq!(
        pocket(&rect, "pocket", true, true, ALONG_Z, page(4.0, 0.0)),
        recess_miss
    );
    // From the front: its near wall face between floor and mouth, not below its floor.
    assert!(pocket(&rect, "pocket", true, true, front, page(0.0, 8.0)).is_empty());
    assert_eq!(
        pocket(&rect, "pocket", true, true, front, page(0.0, 4.0)),
        recess_miss
    );
    // Through: the middle is its open mouth.
    assert!(pocket(&rect, "pocket", true, false, ALONG_Z, page(3.0, 1.0)).is_empty());
    assert_eq!(
        pocket(&rect, "pocket", true, false, ALONG_Z, page(0.0, 0.0)),
        recess_miss
    );
    // Open at a corner: two walls, and the floor of their extents up to the part's edges.
    let corner = [[-3.0, 2.0, 0.0], [-3.0, -2.0, 0.0], [3.0, -2.0, 0.0]];
    assert!(
        pocket(
            &corner,
            "edge_open_recess",
            false,
            true,
            ALONG_Z,
            page(2.5, 1.5)
        )
        .is_empty()
    );
    assert_eq!(
        pocket(
            &corner,
            "edge_open_recess",
            false,
            true,
            ALONG_Z,
            page(4.0, 0.0)
        ),
        recess_miss
    );
    // A circular seat: its arc, never the chord across its mouth.
    let seat = |tip| {
        check(
            vec![recess(
                "f:section_recess:1",
                "channel",
                "circular",
                &[[-2.0, 0.0, 1.0], [2.0, 0.0, 0.0]],
                false,
                false,
            )],
            &[("f:section_recess:1", "seat_diameter.diameter")],
            ALONG_Z,
            tip,
            "recess_leader",
        )
    };
    assert!(seat(page(0.0, -2.0)).is_empty());
    assert!(seat(page(2f64.sqrt(), -(2f64.sqrt()))).is_empty());
    assert_eq!(seat(page(0.0, 0.0)), recess_miss);
    // An unreadable record says so.
    assert_eq!(
        check(
            vec![feature("f:section_recess:1", "section_recess", json!({}))],
            &[("f:section_recess:1", "pocket_width.length")],
            ALONG_Z,
            page(0.0, 0.0),
            "recess_leader",
        ),
        one("recess_leader_target_unverifiable", "d:x")
    );

    // A through slot (obround, its ends semicircles), and a slot pattern of it.
    let slot = feature(
        "f:slot:1",
        "slot",
        json!({ "long_axis": "x", "width_axis": "y", "width": 4.0, "length": 10.0, "lo": -5.0,
                "hi": 5.0, "w_center": 0.0, "d_lo": 5.0, "d_hi": 10.0, "end_radius": 2.0 }),
    );
    let slots = |claims: &[(&str, &str)], tip| {
        check(
            vec![
                slot.clone(),
                feature("f:slot_pattern:1", "slot_pattern", json!({})),
            ],
            claims,
            ALONG_Z,
            tip,
            "slot_leader",
        )
    };
    let lone = [("f:slot:1", "slot_width.length")];
    let slot_miss = one("slot_leader_target_mismatch", "d:x");
    assert!(slots(&lone, page(0.0, 2.0)).is_empty());
    assert!(slots(&lone, page(5.0, 0.0)).is_empty());
    assert_eq!(slots(&lone, page(0.0, 0.0)), slot_miss, "its opening");
    assert_eq!(
        slots(&lone, page(4.9, 1.9)),
        slot_miss,
        "the box corner past its arc"
    );
    let grouped = [("f:slot_pattern:1", "slot_width.length")];
    assert!(slots(&grouped, page(0.0, -2.0)).is_empty());
    assert_eq!(slots(&grouped, page(0.0, 0.0)), slot_miss);
    // An oriented slot: its passage section along its run.
    let oriented = feature(
        "f:oriented_slot:1",
        "oriented_slot",
        json!({ "width": 2.0, "length": 6.0, "source": {
            "frame": { "origin": [0.0, 0.0, 0.0], "run": [0.0, 0.0, 1.0], "u": [1.0, 0.0, 0.0], "v": [0.0, 1.0, 0.0] },
            "run_interval": [5.0, 10.0],
            "section": { "closure": "closed", "boundary": [
                { "point": [-3.0, -1.0] }, { "point": [3.0, -1.0] }, { "point": [3.0, 1.0] }, { "point": [-3.0, 1.0] } ] } } }),
    );
    let tilted = |tip| {
        check(
            vec![oriented.clone()],
            &[("f:oriented_slot:1", "oriented_slot.width")],
            ALONG_Z,
            tip,
            "slot_leader",
        )
    };
    assert!(tilted(page(0.0, 1.0)).is_empty());
    assert_eq!(tilted(page(0.0, 0.0)), slot_miss);
}

/// *d* with its one callout's label drawn as *text*.
fn labelled(mut d: Drawing, text: &str) -> Drawing {
    d.text.push(draftwright::output::TextRun {
        text: text.into(),
        at: [131.0, 111.0],
        size: 3.0,
        rotation: 0.0,
        font: "IBMPlexMono-Regular.ttf".into(),
        h_align: draftwright::output::HAlign::Left,
        v_align: draftwright::output::VAlign::Baseline,
    });
    let run = d.text.len() - 1;
    for a in &mut d.annotations {
        a.runs = vec![run];
    }
    d
}

/// The requirement findings of *code_prefix* on *drawing* naming *feature*.
fn requirement(a: &Analysis, drawing: &Drawing, prefix: &str, feature: &str) -> Vec<String> {
    lint::lint(a, drawing)
        .into_iter()
        .filter(|i| {
            i.code.starts_with(prefix) && i.requirements.iter().any(|r| r.feature == feature)
        })
        .map(|i| i.code)
        .collect()
}

/// ISSUE-1374-a: the chamfer ledger credits a callout only while its leader is on the bevel. The
/// same `C1` callout states its chamfer with its tip on the bevel; moved to the shaft's axis it
/// is a mismatch and its chamfer is missing; a prismatic bevel called out in a view that does not
/// see its edge end-on loses credit too. Two equal compound bevels share one callout and both
/// keep their identities: a tip on either credits both, a tip on neither credits neither.
#[test]
fn a_chamfer_leader_off_its_bevel_loses_the_chamfers_credit() {
    let bevel = |id: &str, z: f64| {
        feature(
            id,
            "chamfer",
            json!({ "angle": 45.0, "at": [0.0, -4.5, z], "axis": "z", "corner": null,
                    "leg1": 1.0, "leg2": 1.0, "turned": true }),
        )
    };
    let front = [0.0, -1.0, 0.0];
    let run = |features: Vec<Value>, claims: &[(&str, &str)], direction, tip| {
        let mut all = flatted_band(10.0, None);
        all.extend(features);
        let a = analysis(all, json!({}), &[]);
        let d = labelled(
            drawing(direction, vec![callout("d:c", claims, tip, [130.0, 110.0])]),
            "C1",
        );
        (a, d)
    };
    let one_bevel = [("f:chamfer:1", "chamfer.length")];
    let (a, d) = run(
        vec![bevel("f:chamfer:1", 4.5)],
        &one_bevel,
        front,
        page(4.5, 4.5),
    );
    assert!(requirement(&a, &d, "chamfer_", "f:chamfer:1").is_empty());
    let (a, d) = run(
        vec![bevel("f:chamfer:1", 4.5)],
        &one_bevel,
        front,
        page(0.0, 4.5),
    );
    assert_eq!(
        found(&a, &d, "chamfer_leader"),
        one("chamfer_leader_target_mismatch", "d:c")
    );
    assert_eq!(
        requirement(&a, &d, "chamfer_", "f:chamfer:1"),
        ["chamfer_requirement_missing"]
    );
    // Wrong view: an edge along x is not seen end-on looking along z.
    let prismatic = feature(
        "f:chamfer:1",
        "chamfer",
        json!({ "angle": 45.0, "at": [0.0, 9.5, 9.5], "axis": "x", "corner": null,
                "leg1": 1.0, "leg2": 1.0, "turned": false }),
    );
    let (a, d) = run(vec![prismatic.clone()], &one_bevel, ALONG_Z, page(0.0, 9.5));
    assert_eq!(
        requirement(&a, &d, "chamfer_", "f:chamfer:1"),
        ["chamfer_requirement_missing"]
    );
    let side = [1.0, 0.0, 0.0];
    let (a, d) = run(vec![prismatic], &one_bevel, side, page(-9.5, 9.5));
    assert!(
        found(&a, &d, "chamfer_leader").is_empty(),
        "seen end-on, on its section"
    );
    assert!(requirement(&a, &d, "chamfer_", "f:chamfer:1").is_empty());
    // Equal compound bevels at either end of the band share the callout.
    let both = [
        ("f:chamfer:1", "chamfer.length"),
        ("f:chamfer:2", "chamfer.length"),
    ];
    let pair = || vec![bevel("f:chamfer:1", 4.5), bevel("f:chamfer:2", -4.5)];
    let (a, d) = run(pair(), &both, front, page(4.5, -4.5));
    for id in ["f:chamfer:1", "f:chamfer:2"] {
        assert!(requirement(&a, &d, "chamfer_", id).is_empty(), "{id}");
    }
    let (a, d) = run(pair(), &both, front, page(0.0, 0.0));
    for id in ["f:chamfer:1", "f:chamfer:2"] {
        assert_eq!(
            requirement(&a, &d, "chamfer_", id),
            ["chamfer_requirement_missing"],
            "{id}"
        );
    }
}

/// A linear dimension *name* in the plan view along page axis *k* between page stations *span*,
/// its dimension line *offset* across, claiming *claims*.
fn linear(
    name: &str,
    claims: &[(&str, &str)],
    k: usize,
    span: [f64; 2],
    offset: f64,
) -> (Annotation, Drawn) {
    let at = |along: f64, across: f64| {
        if k == 0 {
            [along, across]
        } else {
            [across, along]
        }
    };
    let lines = vec![
        [at(span[0], offset - 8.0), at(span[0], offset + 1.0)],
        [at(span[1], offset - 8.0), at(span[1], offset + 1.0)],
        [at(span[0], offset), at(span[1], offset)],
    ];
    let (mut a, mut d) = callout(name, claims, [0.0, 0.0], [1.0, 1.0]);
    d.lines = lines;
    d.elbow = None;
    a.name = name.into();
    (a, d)
}

/// ISSUE-1374-d: a turned step's diameter and length earn credit only from ink on the band. The
/// `ø3.4` callout tipped on the band's outline states the diameter; tipped at its axis it is a
/// mismatch and the diameter is missing. The `10` length dimension whose extension lines stand at
/// the band's shoulders (z −5 and 5) states its length; moved 1 mm along the axis it is
/// `step_length_witness_target_mismatch` and the length is missing; seen along the axis it cannot
/// be judged (info) and keeps its credit.
#[test]
fn turned_step_credit_needs_its_witnesses_and_leader_on_the_band() {
    let a = analysis(flatted_band(3.4, None), json!({}), &[]);
    let front = [0.0, -1.0, 0.0];
    let diameter = |tip| {
        labelled(
            drawing(
                front,
                vec![callout(
                    "d:d",
                    &[("f:turned_step:1", "step.diameter")],
                    tip,
                    [130.0, 110.0],
                )],
            ),
            "ø3.4",
        )
    };
    let d = diameter(page(1.7, 0.0));
    assert!(found(&a, &d, "step_diameter_leader").is_empty());
    assert!(
        !requirement(&a, &d, "turned_step_", "f:turned_step:1")
            .iter()
            .any(|c| c.contains("missing"))
    );
    let d = diameter(page(0.0, 0.0));
    assert_eq!(
        found(&a, &d, "step_diameter_leader"),
        one("step_diameter_leader_target_mismatch", "d:d")
    );
    assert_eq!(
        requirement(&a, &d, "turned_step_requirement_missing", "f:turned_step:1"),
        ["turned_step_requirement_missing"]
    );

    let length = |direction, shift: f64| {
        let [lo, hi] = [page(0.0, -5.0)[1], page(0.0, 5.0)[1]];
        labelled(
            drawing(
                direction,
                vec![linear(
                    "d:l",
                    &[("f:turned_step:1", "step.length")],
                    1,
                    [lo + shift, hi + shift],
                    120.0,
                )],
            ),
            "10",
        )
    };
    let d = length(front, 0.0);
    assert!(found(&a, &d, "step_length_witness").is_empty());
    assert!(requirement(&a, &d, "axial_length_missing", "f:turned_step:1").is_empty());
    let d = length(front, 1.0);
    assert_eq!(
        found(&a, &d, "step_length_witness"),
        one("step_length_witness_target_mismatch", "d:l")
    );
    assert_eq!(
        requirement(&a, &d, "axial_length_missing", "f:turned_step:1"),
        ["axial_length_missing"]
    );
    let d = length(ALONG_Z, 1.0);
    assert_eq!(
        found(&a, &d, "step_length_witness"),
        one("step_length_witness_target_unverifiable", "d:l")
    );
    assert!(requirement(&a, &d, "axial_length_missing", "f:turned_step:1").is_empty());
}

/// ISSUE-1276-a's knurl case: a knurl is printed on its turned step's diameter callout (AP242's on
/// thumbwheel_thread_knurl's ø6 shank, `crate::pmi_lowering::manufacturing`; a Spec's the same
/// way, `crate::spec_notes`), so its leader is the band's: in a profile view, tipped on the
/// knurled band's own outline at its own station, not on the coaxial thread or the axis. Moved to
/// the axis, it is a mismatch and the knurled diameter loses its credit.
#[test]
fn a_knurl_note_leads_to_its_knurled_surface() {
    let (part, a) = cache::native(&fixture("pmi/step/thumbwheel_thread_knurl.step.gz"));
    for (spec, knurl) in [
        (Spec::default(), "DIAMOND KNURL P1"),
        (
            serde_json::from_value(json!({
                "features": { "f:turned_step:4": { "knurl": { "pattern": "straight", "pitch": 0.8 } } }
            }))
            .unwrap(),
            "STRAIGHT KNURL P0.8",
        ),
    ] {
        let plan = model::plan(&a, &spec).unwrap();
        let mut drawing = model::render(&part, &a, &plan, &font()).unwrap();
        let name = "d:f:turned_step:4/step.diameter";
        let ann = drawing
            .annotations
            .iter()
            .find(|x| x.name == name)
            .expect("the knurled band's callout");
        let text: Vec<&str> = ann
            .runs
            .iter()
            .map(|&i| drawing.text[i].text.as_str())
            .collect();
        let text = text.join(" ");
        assert!(text.contains(knurl), "{text}");
        let view = ann.view.clone().unwrap();
        let f = drawing.sheet.frames[view.trim_start_matches("v:")].clone();
        let step = a
            .features
            .iter()
            .find(|x| x.id == "f:turned_step:4")
            .unwrap();
        let band = lint::turned::band(&draftwright::bridge::holes::frame(&a), step).unwrap();
        assert!(
            f.direction[band.axis].abs() < 1e-9,
            "{view} shows the band in profile"
        );
        let issues = lint::lint(&a, &drawing);
        let bad: Vec<_> = issues
            .iter()
            .filter(|i| {
                i.annotation.as_deref() == Some(name) && i.code.starts_with("step_diameter_leader")
            })
            .collect();
        assert!(bad.is_empty(), "{bad:#?}");
        assert!(!issues.iter().any(|i| {
            i.code == "turned_step_requirement_missing"
                && i.requirements
                    .iter()
                    .any(|r| r.feature == step.id && r.parameter == "step.diameter")
        }));
        // To the axis: off the knurled surface.
        let view_box = draftwright::haecceity::hlr::View::new(f.direction, f.up).unwrap();
        let [lo, hi] = a.envelope;
        let centre = view_box.map([0, 1, 2].map(|k| 0.5 * (lo[k] + hi[k])));
        let mut on = band.on;
        on[band.axis] = 0.5 * (band.lo + band.hi);
        let q = view_box.map(on);
        let axis_tip = [0, 1].map(|k| f.at[k] + f.scale * (q[k] - centre[k]));
        let item = drawing
            .sheet
            .items
            .iter_mut()
            .find(|i| i.name == name && i.elbow.is_some())
            .unwrap();
        item.lines[0][0] = axis_tip;
        let issues = lint::lint(&a, &drawing);
        assert!(issues.iter().any(|i| i.annotation.as_deref() == Some(name)
            && i.code == "step_diameter_leader_target_mismatch"));
        assert!(issues.iter().any(|i| {
            i.code == "turned_step_requirement_missing"
                && i.requirements
                    .iter()
                    .any(|r| r.feature == step.id && r.parameter == "step.diameter")
        }));
    }
}
