//! LINT-angular-profile_angle_requirement (ISO 129-1, ASME Y14.5): an outer-profile corner
//! needs its geometry defined, not necessarily its angle printed. A corner the linear
//! dimensions fix needs no angle; a 45° corner chamfer is called out by its size (`C<leg>`), any
//! other cut across a corner of principal edges is located by its legs by default (#217), any
//! other corner gets its angle; a regular polygon's equal corners
//! get one counted angle; an angle with no clear place is dropped and
//! stays a lint warning, and an angle whose edge no linear dimension places is reported; Spec
//! overrides choose per corner and per profile.
#[path = "common/corpus.rs"]
mod corpus;

use draftwright::api::{self, Features};
use draftwright::dims::PlannedDimension;
use draftwright::items::State;
use draftwright::lint::profile_angles::{self, Corner};
use draftwright::model::{self, Analysis, Plan, Spec};
use serde_json::json;
use std::sync::OnceLock;

/// A synthetic part: a 40 × 20 × 10 block whose top face (face 1) has the outer profile
/// through *corners* (anticlockwise, seen from +z).
fn part(corners: &[[f64; 2]]) -> Analysis {
    let n = corners.len();
    let supports: Vec<_> = (0..n)
        .map(|k| {
            let (a, b) = (corners[k], corners[(k + 1) % n]);
            json!({"kind":"line","start":[a[0],a[1],10],"end":[b[0],b[1],10]})
        })
        .collect();
    let (lo, hi) = corners.iter().fold(
        ([f64::INFINITY; 2], [f64::NEG_INFINITY; 2]),
        |(lo, hi), p| {
            (
                [lo[0].min(p[0]), lo[1].min(p[1])],
                [hi[0].max(p[0]), hi[1].max(p[1])],
            )
        },
    );
    serde_json::from_value(json!({"schema":model::ANALYSIS_SCHEMA,
        "binding":{"source_sha256":"","part":"test","face_count":20,"loader":"test"},
        "envelope":[[lo[0],lo[1],0],[hi[0],hi[1],10]],"iso_bounds":[lo[0],lo[1],hi[0],hi[1]],
        "features":[],
        // One (empty) attachment per face, so the Spec can name `face:1`.
        "face_attachments":vec![json!({}); 20],
        "outer_profiles":[{"face":1,"body_faces":(1..=12).collect::<Vec<_>>(),
            "edges":(1..=n).collect::<Vec<_>>(),"profile":{"origin":[0,0,10],"normal":[0,0,1],
            "schema_version":1,"boundary_kind":"outer","inner_loop_count":0,"supports":supports}}]}))
    .unwrap()
}

fn plan(a: &Analysis, spec: serde_json::Value) -> Plan {
    model::plan(a, &serde_json::from_value(spec).unwrap()).unwrap()
}

fn angles(p: &Plan) -> Vec<&PlannedDimension> {
    p.dimensions
        .iter()
        .filter(|d| {
            !d.id.starts_with(CHAMFER)
                && d.measurements
                    .iter()
                    .any(|m| m.parameter == "profile_angle.angle")
        })
        .collect()
}

/// A profile chamfer callout's ID prefix.
const CHAMFER: &str = "d:envelope/profile_chamfer.length.";

fn chamfers(p: &Plan) -> Vec<&PlannedDimension> {
    p.dimensions
        .iter()
        .filter(|d| d.id.starts_with(CHAMFER))
        .collect()
}

/// A corner's angle ID (`d:envelope/profile_angle.angle.f1_e1_e2`) from its requirement.
fn angle_id(feature: &str) -> String {
    format!(
        "d:envelope/profile_angle.angle.{}",
        feature
            .strip_prefix("outer_profile:")
            .unwrap()
            .replace(':', "_")
    )
}

fn stations(p: &Plan) -> Vec<&PlannedDimension> {
    p.dimensions
        .iter()
        .filter(|d| d.id.starts_with("d:envelope/profile_location_"))
        .collect()
}

/// A trapezoid: its sloping side runs from (40, 0) to (30, 20). Every station of both its
/// corners is on the part's box except x = 30.
const TRAPEZOID: [[f64; 2]; 4] = [[0., 0.], [40., 0.], [30., 20.], [0., 20.]];
/// A 40 × 20 rectangle with its four corners chamfered 5 × 45°.
const OCTAGON: [[f64; 2]; 8] = [
    [5., 0.],
    [35., 0.],
    [40., 5.],
    [40., 15.],
    [35., 20.],
    [5., 20.],
    [0., 15.],
    [0., 5.],
];
/// A pentagon whose sloping side runs from (40, 10) to (25, 20): x = 25 and y = 10 are both
/// off the box.
const PENTAGON: [[f64; 2]; 5] = [[0., 0.], [40., 0.], [40., 10.], [25., 20.], [0., 20.]];

#[test]
fn a_linearly_fixed_corner_needs_no_angle() {
    let corner = Corner {
        feature: "outer_profile:f1:e1:e2".into(),
        axis: 2,
        vertex: [40., 0., 10.],
        first: [0., 0., 10.],
        second: [30., 20., 10.],
        virtual_vertex: false,
        ends: [[0., 0., 10.], [30., 20., 10.]],
    };
    let a = part(&TRAPEZOID);
    let box_only: Vec<(usize, f64, f64)> = Vec::new();
    let missing = profile_angles::undefined_stations(&corner, &|k, v| {
        profile_angles::rooted(&a, &box_only, k, v)
    });
    assert_eq!(missing.len(), 1, "{missing:?}");
    assert_eq!(missing[0].0, 0);
    assert!((missing[0].1 - 30.).abs() < 1e-9);
    // A chain of linear dimensions from the box reaches x = 30: the corner is defined.
    let chain = vec![(0, 0., 12.), (0, 12., 30.)];
    assert!(
        profile_angles::undefined_stations(&corner, &|k, v| {
            profile_angles::rooted(&a, &chain, k, v)
        })
        .is_empty()
    );
    // An unrooted span proves nothing.
    let floating = vec![(0, 12., 30.)];
    assert_eq!(
        profile_angles::undefined_stations(&corner, &|k, v| {
            profile_angles::rooted(&a, &floating, k, v)
        })
        .len(),
        1
    );
}

/// A placed station's value and the coordinate it locates: its dimension is measured from one
/// side of the part's box (*lo*..*hi* on its axis).
fn station_reaches(d: &PlannedDimension, v: f64, lo: f64, hi: f64) -> bool {
    let value: f64 = d.label.parse().unwrap();
    (value - (v - lo)).abs() < 1e-3 || (value - (hi - v)).abs() < 1e-3
}

/// One station short is not fixed: by default it is completed linearly (#217), with no angle.
#[test]
fn a_nearly_fixed_corner_is_completed_linearly_by_default() {
    let a = part(&TRAPEZOID);
    assert_eq!(profile_angles::requirements(&a).len(), 2);
    let p = plan(&a, json!({}));
    assert!(angles(&p).is_empty(), "{:?}", angles(&p));
    let found = stations(&p);
    assert_eq!(found.len(), 1, "{found:?}");
    assert!(
        found[0]
            .id
            .starts_with("d:envelope/profile_location_x.length.f1_")
    );
    assert!(
        station_reaches(found[0], 30., 0., 40.),
        "{}",
        found[0].label
    );
    assert!(found[0].placed.is_some());
}

/// Two angles fix the sloping edge's direction, not where it lies (l-plate-5-holes, #217): an
/// under-defined corner gets the stations of its edge's ends, never an angle alone.
#[test]
fn an_under_defined_corner_gets_its_stations_not_an_angle() {
    let a = part(&PENTAGON);
    let p = plan(&a, json!({}));
    assert!(angles(&p).is_empty(), "{:?}", angles(&p));
    let found = stations(&p);
    assert_eq!(found.len(), 2, "{found:?}");
    assert!(
        found
            .iter()
            .any(|d| d.id.contains("location_x") && station_reaches(d, 25., 0., 40.))
    );
    assert!(
        found
            .iter()
            .any(|d| d.id.contains("location_y") && station_reaches(d, 10., 0., 20.))
    );
    // The profile's `angle` choice still gives each corner its angle.
    let p = plan(
        &a,
        json!({"faces": {"face:1": {"profile_corners": "angle"}}}),
    );
    let found = angles(&p);
    assert_eq!(found.len(), 2, "{found:?}");
    for d in &found {
        assert_eq!(d.view, "v:plan");
        assert!(
            !d.label.contains('×') && d.label.ends_with('°'),
            "{}",
            d.label
        );
        assert_eq!(d.measurements.len(), 1);
    }
    assert!(stations(&p).is_empty());
}

/// A station whose corner lies on the view's silhouette is drawn on that side, so neither
/// witness runs over the part (l-plate-5-holes' two `20`s ran through the plate, #217): the
/// pentagon's y = 10 corner is on the right edge, though the plan's vertical strip is on the left.
#[test]
fn a_station_is_drawn_on_the_side_its_corner_is_open_to() {
    let a = part(&PENTAGON);
    let p = plan(&a, json!({}));
    let view = p.views.iter().find(|v| v.id == "v:plan").unwrap();
    // The part's 40 × 20 box on the page, about the view's centre.
    let (hx, hy) = (20. * view.scale, 10. * view.scale);
    let inside = |q: [f64; 2]| {
        (q[0] - view.at[0]).abs() < hx - 1e-6 && (q[1] - view.at[1]).abs() < hy - 1e-6
    };
    let found = stations(&p);
    assert_eq!(found.len(), 2, "{found:?}");
    for d in found {
        let placed = d.placed.as_ref().unwrap();
        for w in &placed.lines[..2] {
            let mid = [(w[0][0] + w[1][0]) / 2., (w[0][1] + w[1][1]) / 2.];
            assert!(!inside(mid), "{} witness {w:?} runs over the part", d.id);
        }
    }
}

/// An octagon of equal corners but unequal sides (STC-08's chamfered rectangle) is not a
/// regular polygon: no counted `8× 135°`. Its four 45° chamfers are called out once by their
/// size, `4× C5` as equal edge chamfers are (#217, maintainer 2026-10-28): their principal edges
/// are the box's, so no station is needed.
#[test]
fn an_irregular_equal_angled_octagon_gets_one_chamfer_callout() {
    let a = part(&OCTAGON);
    let corners = profile_angles::requirements(&a);
    assert_eq!(corners.len(), 8);
    for c in &corners {
        let (other, leg) = profile_angles::chamfer(c, &corners).expect("a chamfer corner");
        assert_ne!(other.feature, c.feature);
        assert!((leg - 5.).abs() < 1e-9, "{leg}");
    }
    let p = plan(&a, json!({}));
    assert!(angles(&p).is_empty(), "{:?}", angles(&p));
    assert!(stations(&p).is_empty(), "{:?}", stations(&p));
    let found = chamfers(&p);
    assert_eq!(found.len(), 1, "{found:?}");
    assert_eq!(found[0].label, "4× C5");
    assert_eq!(found[0].view, "v:plan");
    assert!(found[0].placed.is_some());
    // Every corner's 135° is claimed by the callout.
    assert_eq!(found[0].measurements.len(), 8);
    assert!(
        found[0]
            .measurements
            .iter()
            .all(|m| { m.parameter == "profile_angle.angle" && (m.value - 135.).abs() < 1e-6 })
    );
    // The `linear` choice locates the legs instead: x = 5 and 35, y = 5 and 15.
    let p = plan(
        &a,
        json!({"faces": {"face:1": {"profile_corners": "linear"}}}),
    );
    assert!(chamfers(&p).is_empty());
    assert_eq!(stations(&p).len(), 4, "{:?}", stations(&p));
    // Naming a corner's angle keeps its cut out of the callout: hidden, that cut is located by
    // its legs, `5` on each axis; the other three keep the counted callout.
    let hidden = angle_id(&corners[0].feature);
    let p = plan(&a, json!({"dimensions": {&hidden: {"omit": true}}}));
    let found = chamfers(&p);
    assert_eq!(found.len(), 1, "{found:?}");
    assert_eq!(found[0].label, "3× C5");
    let found = stations(&p);
    assert_eq!(found.len(), 2, "{found:?}");
    assert!(found.iter().all(|d| d.label == "5"), "{found:?}");
    // The profile's `angle` choice keeps its one counted angle.
    let p = plan(
        &a,
        json!({"faces": {"face:1": {"profile_corners": "angle"}}}),
    );
    let found = angles(&p);
    assert_eq!(found.len(), 1, "{found:?}");
    assert_eq!(found[0].label, "8× 135°");
    assert!(stations(&p).is_empty());
    assert!(chamfers(&p).is_empty());
}

/// A chamfered rectangle inset from the part's box: its chamfers' callout needs their principal
/// edges' offsets as stations, never their legs, and lint locates a called-out chamfer from
/// those offsets alone.
#[test]
fn an_inset_chamfer_locates_its_principal_edges_only() {
    let mut a = part(&OCTAGON);
    // A wider body: the box runs 10 mm past the profile all round.
    a.envelope = [[-10., -10., 0.], [50., 30., 10.]];
    let p = plan(&a, json!({}));
    assert_eq!(chamfers(&p).len(), 1, "{:?}", chamfers(&p));
    let found = stations(&p);
    assert_eq!(found.len(), 2, "{found:?}");
    for d in &found {
        assert_eq!(d.label, "2× 10", "{}", d.id);
    }
    let corners = profile_angles::requirements(&a);
    let c = &corners[0];
    let (other, _) = profile_angles::chamfer(c, &corners).unwrap();
    let box_only: Vec<(usize, f64, f64)> = Vec::new();
    let located = |k: usize, v: f64| profile_angles::rooted(&a, &box_only, k, v);
    assert!(!profile_angles::chamfer_located(c, other, &located));
    let offsets = vec![(0, -10., 0.), (1, -10., 0.), (0, 40., 50.), (1, 20., 30.)];
    let located = |k: usize, v: f64| profile_angles::rooted(&a, &offsets, k, v);
    assert!(profile_angles::chamfer_located(c, other, &located));
}

/// A chamfer callout states its size in the edge chamfers' house form.
#[test]
fn a_chamfer_callout_is_read_by_its_size() {
    assert!(profile_angles::states_chamfer("C21.075", 21.075));
    assert!(profile_angles::states_chamfer("4× C21.075", 21.0752));
    assert!(!profile_angles::states_chamfer("4× C21.075", 21.2));
    assert!(!profile_angles::states_chamfer("21.075", 21.075));
    assert!(!profile_angles::states_chamfer("135°", 135.));
}

/// A corner off a cut (here a triangle's base corners, whose sloping edges meet at the apex)
/// keeps its angle by default: only a cut across a corner of principal edges is located by its
/// legs automatically.
#[test]
fn a_corner_off_a_cut_keeps_its_angle() {
    let a = part(&[[0., 0.], [40., 0.], [20., 20.]]);
    assert_eq!(profile_angles::requirements(&a).len(), 2);
    let p = plan(&a, json!({}));
    assert!(stations(&p).is_empty(), "{:?}", stations(&p));
    let found = angles(&p);
    assert_eq!(found.len(), 1, "{found:?}");
    assert_eq!(found[0].label, "2× 45°");
}

/// An angle drawn where no linear dimension places its edge is reported (#217): the profile's
/// `angle` choice on the pentagon draws both angles but not the stations x = 25 and y = 10.
#[test]
fn an_angle_whose_edge_is_not_located_is_reported() {
    let a = part(&PENTAGON);
    let corners = profile_angles::requirements(&a);
    let box_only: Vec<(usize, f64, f64)> = Vec::new();
    let located = |k: usize, v: f64| profile_angles::rooted(&a, &box_only, k, v);
    assert!(
        corners
            .iter()
            .all(|c| !profile_angles::located_with_angle(c, &located))
    );
    // One fully located end of the sloping edge places it once its angle is stated.
    let chain = vec![(0, 0., 25.)];
    let located = |k: usize, v: f64| profile_angles::rooted(&a, &chain, k, v);
    assert!(
        corners
            .iter()
            .all(|c| profile_angles::located_with_angle(c, &located))
    );
    // End to end on the pentagon fixture part, whose corners are not cuts: by default (and under
    // its `angle` choice) it gets angles but no stations, so lint reports the corners whose
    // edges stay unplaced; its `linear` choice locates them and nothing is reported.
    let pentagon = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/coverage-holes/profile-pentagon.step");
    let unlocated = |r: &api::Review| -> Vec<String> {
        r.lint
            .iter()
            .filter(|i| i.code == "profile_corner_unlocated")
            .flat_map(|i| i.subjects.clone())
            .collect()
    };
    let r = review(
        &pentagon,
        json!({"faces": {"face:6": {"profile_corners": "angle"}}}),
    );
    assert!(!unlocated(&r).is_empty(), "{:?}", r.lint);
    let r = review(&pentagon, json!({}));
    assert!(!unlocated(&r).is_empty(), "{:?}", r.lint);
    let r = review(
        &pentagon,
        json!({"faces": {"face:6": {"profile_corners": "linear"}}}),
    );
    assert!(unlocated(&r).is_empty(), "{:?}", r.lint);
    assert!(missing(&r).is_empty(), "{:?}", r.lint);
}

#[test]
fn a_regular_polygon_gets_one_counted_angle() {
    let hexagon: Vec<[f64; 2]> = (0..6)
        .map(|k| {
            let t = (60. * k as f64).to_radians();
            [20. + 20. * t.cos(), 20. + 20. * t.sin()]
        })
        .collect();
    let a = part(&hexagon);
    assert_eq!(profile_angles::requirements(&a).len(), 6);
    let p = plan(&a, json!({}));
    let found = angles(&p);
    assert_eq!(found.len(), 1, "{found:?}");
    assert_eq!(found[0].id, "d:envelope/profile_angle.angle.f1");
    assert_eq!(found[0].label, "6× 120°");
    assert_eq!(found[0].measurements.len(), 6);
}

#[test]
fn a_shown_corner_keeps_its_angle_and_a_hidden_one_is_omitted() {
    let a = part(&TRAPEZOID);
    let id = profile_angles::requirements(&a)
        .iter()
        .map(|c| angle_id(&c.feature))
        .next()
        .unwrap();
    // Show an angle the defaults would draw anyway: it stays, now listed in the spec.
    let p = plan(&a, json!({"dimensions": {&id: {}}}));
    assert!(angles(&p).iter().any(|d| d.id == id), "{:?}", angles(&p));
    // Hide a corner's angle: the id stays known, omitted, and its stations are still drawn.
    let b = part(&PENTAGON);
    let hidden = angle_id(&profile_angles::requirements(&b)[0].feature);
    let p = plan(&b, json!({"dimensions": {&hidden: {"omit": true}}}));
    let d = p.dimensions.iter().find(|d| d.id == hidden);
    assert!(d.is_none_or(|d| d.state == State::OmittedByUser), "{d:?}");
    assert!(
        p.omitted
            .iter()
            .any(|o| o.id == hidden && o.state == State::OmittedByUser)
    );
    assert!(angles(&p).iter().all(|d| d.state == State::OmittedByUser));
    assert_eq!(stations(&p).len(), 2, "{:?}", stations(&p));
}

fn ctc03() -> &'static std::path::PathBuf {
    static PATH: OnceLock<std::path::PathBuf> = OnceLock::new();
    PATH.get_or_init(|| {
        corpus::dir()
            .expect("corpus")
            .join("nist/nist_ctc_03_asme1_rc.stp")
    })
}

fn review(path: &std::path::Path, spec: serde_json::Value) -> api::Review {
    let spec: Spec = serde_json::from_value(spec).unwrap();
    api::review_from(path, Features::Native, &spec).unwrap()
}

fn missing(r: &api::Review) -> Vec<String> {
    r.lint
        .iter()
        .filter(|i| i.code.starts_with("angular_requirement_"))
        .flat_map(|i| i.requirements.iter().map(|q| q.feature.clone()))
        .collect()
}

/// FTC-08's chamfered-rectangle octagon: its four 45° chamfers take one `4× C21.075` (#217,
/// maintainer 2026-10-28), and its inset sides' offsets the two counted stations `2× 12.827`
/// and `2× 11.938`, each locating its mirror; lint reads every corner as stated and located.
#[test]
fn ftc08_chamfers_are_called_out_by_size() {
    let Some(dir) = corpus::dir() else {
        return;
    };
    let path = dir.join("nist/nist_ftc_08_asme1_rc.stp");
    let a = model::analyse_native(&path).unwrap().1;
    // Preserve the captured metric chamfer and station wording.
    let spec = Spec {
        units: draftwright::units::Units::Mm,
        ..Spec::default()
    };
    let p = model::plan_recovered(&a, &spec).unwrap();
    let found = chamfers(&p);
    assert_eq!(found.len(), 1, "{found:?}");
    assert_eq!(found[0].label, "4× C21.075");
    assert_eq!(found[0].measurements.len(), 8);
    assert!(found[0].placed.is_some());
    let found = stations(&p);
    let mut labels: Vec<&str> = found.iter().map(|d| d.label.as_str()).collect();
    labels.sort();
    assert_eq!(labels, ["2× 11.938", "2× 12.827"], "{found:?}");
    for d in &found {
        assert_eq!(d.measurements.len(), 2);
        assert!(d.placed.is_some(), "{}", d.id);
        // No recess location re-seated onto a station's line (dw-examples' FTC-08).
        assert!(
            !p.problems
                .iter()
                .any(|q| q.code == "dimension_line_shared" && q.subjects.contains(&d.id)),
            "{:?}",
            p.problems
        );
    }
    assert!(angles(&p).is_empty(), "{:?}", angles(&p));
    let r = review(&path, json!({"units":"mm"}));
    assert!(
        !r.lint.iter().any(|i| i.code == "profile_corner_unlocated"),
        "{:?}",
        r.lint
    );
    assert!(
        !missing(&r)
            .iter()
            .any(|f| f.starts_with("outer_profile:f1:")),
        "{:?}",
        r.lint
    );
}

/// CTC-03's two mirror-equal 135° corners: by default one counted `2× 135°`, drawn where clear
/// or dropped with both corners still reported; `faces/face:37/profile_corners: linear` locates their stations instead, and lint
/// reads those drawn dimensions as defining both.
#[test]
fn ctc03_corners_are_stated_by_angle_or_linearly_by_profile_choice() {
    if corpus::dir().is_none() {
        return;
    }
    let r = review(ctc03(), json!({}));
    // Mirror-equal corners share one counted angle; a dropped one leaves both corners missing.
    let missing_now = missing(&r);
    let a = model::analyse_native(ctc03()).unwrap().1;
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    let unplaced: Vec<&str> = angles(&p)
        .iter()
        .filter(|d| d.placed.is_none())
        .flat_map(|d| d.measurements.iter().map(|m| m.feature.as_str()))
        .collect();
    assert_eq!(missing_now.len(), unplaced.len(), "{missing_now:?}");
    assert!(missing_now.iter().all(|m| unplaced.contains(&m.as_str())));
    assert!(
        angles(&p).iter().all(|d| d.label.starts_with("2× ")),
        "{:?}",
        angles(&p)
    );
    let linear = review(
        ctc03(),
        json!({"faces": {"face:37": {"profile_corners": "linear"}}}),
    );
    assert!(
        !linear
            .problems
            .iter()
            .any(|p| p.code == "angular_dimension_dropped"),
        "{:?}",
        linear.problems
    );
    assert!(missing(&linear).is_empty(), "{:?}", missing(&linear));
    let a = model::analyse_native(ctc03()).unwrap().1;
    let p = model::plan_recovered(
        &a,
        &serde_json::from_value(json!({"faces": {"face:37": {"profile_corners": "linear"}}}))
            .unwrap(),
    )
    .unwrap();
    assert!(angles(&p).is_empty());
    assert!(!stations(&p).is_empty());
    assert!(stations(&p).iter().all(|d| d.placed.is_some()));
}

/// mfcadpp/10049 under the `angle` choice: a corner whose angle finds no clear place is
/// dropped, never forced, and lint still reports it missing.
#[test]
fn a_failed_placement_leaves_the_corner_a_lint_warning() {
    let Some(dir) = corpus::dir() else {
        return;
    };
    let r = review(
        &dir.join("mfcadpp/10049.step"),
        json!({"faces": {"face:1": {"profile_corners": "angle"}}}),
    );
    let dropped: Vec<_> = r
        .problems
        .iter()
        .filter(|p| p.code == "angular_dimension_dropped")
        .flat_map(|p| p.subjects.clone())
        .collect();
    assert!(!dropped.is_empty());
    let missing = missing(&r);
    for id in &dropped {
        assert!(
            missing.iter().any(|m| &angle_id(m) == id),
            "{id}: {missing:?}"
        );
    }
}

/// A plate's face is required even behind a nearer face of another plate (#217: a bent
/// bracket's base plate behind the motor plate's end face); a plain face stays hidden.
#[test]
fn a_plate_face_behind_another_plates_end_is_required() {
    let mut a = part(&PENTAGON);
    // The upright plate's end face: a thin strip nearer the camera, on the same body.
    let mut end = a.outer_profiles[0].clone();
    end.face = 13;
    end.edges = vec![21, 22, 23, 24];
    end.profile = serde_json::from_value(json!({"origin":[0,0,30],"normal":[0,0,1],
        "schema_version":1,"boundary_kind":"outer","inner_loop_count":0,"supports":[
            {"kind":"line","start":[0,0,30],"end":[3,0,30]},
            {"kind":"line","start":[3,0,30],"end":[3,20,30]},
            {"kind":"line","start":[3,20,30],"end":[0,20,30]},
            {"kind":"line","start":[0,20,30],"end":[0,0,30]}]}))
    .unwrap();
    a.outer_profiles.push(end);
    assert!(profile_angles::requirements(&a).is_empty());
    let plate = |id: &str, faces: [usize; 2]| {
        serde_json::from_value(json!({"id":id,"kind":"plate","faces":faces,"parameters":{},
            "fingerprint":{"version":"draftwright-bridge/2","family":"plates",
                "subtype":"Plate","sizes":{},"position":[0,0,0],"neighbourhood":[]}}))
        .unwrap()
    };
    a.features = vec![plate("f:plate:1", [1, 2]), plate("f:plate:2", [13, 14])];
    let found = profile_angles::requirements(&a);
    assert_eq!(found.len(), 2, "{found:?}");
    assert!(
        found
            .iter()
            .all(|c| c.feature.starts_with("outer_profile:f1:"))
    );
}

/// A notch's flanks are stated by the notch: CTC-01's plate, now required behind the hex
/// boss, issues no corner on its V-slots' flank edges.
#[test]
fn a_notch_flank_corner_is_left_to_the_notch() {
    let Some(dir) = corpus::dir() else {
        return;
    };
    let a = model::analyse_native(&dir.join("nist/nist_ctc_01_asme1_rd.stp"))
        .unwrap()
        .1;
    // The notches' flank edges, from their angles' IDs (`notch.angle.f<face>_e<i>_e<j>`).
    let flanks: Vec<String> = plan(&a, json!({}))
        .dimensions
        .iter()
        .filter_map(|d| d.id.split("/notch.angle.f").nth(1))
        .flat_map(|s| s.split('_').skip(1).map(str::to_owned).collect::<Vec<_>>())
        .collect();
    assert!(!flanks.is_empty());
    let corners = profile_angles::requirements(&a);
    for c in &corners {
        assert!(
            !c.edge_ids().iter().any(|e| flanks.iter().any(|f| f == e)),
            "{}",
            c.feature
        );
    }
}

/// A mirror pair that keeps its angles takes one counted angle.
#[test]
fn mirror_equal_corners_get_one_counted_angle() {
    // A house: its eaves mirror each other across x = 20 and are not cuts (the ridge meets no
    // principal edge), so they keep their angles as one `2×`; the ridge keeps its own.
    let a = part(&[[0., 0.], [40., 0.], [40., 10.], [20., 20.], [0., 10.]]);
    let p = plan(&a, json!({}));
    let found = angles(&p);
    assert_eq!(found.len(), 2, "{found:?}");
    let pair: Vec<_> = found
        .iter()
        .filter(|d| d.label.starts_with("2× "))
        .collect();
    assert_eq!(pair.len(), 1, "{found:?}");
    assert_eq!(pair[0].measurements.len(), 2);
}

/// Every placed profile angle stays local to its corner: its arc's radius and its whole label
/// lie within twelve label heights of the corner, and its label sits on its arc. Checked on the synthetic shapes, the profile fixture parts and the affected
/// corpus parts.
#[test]
fn a_placed_profile_angle_stays_local_to_its_corner() {
    let shapes: [&[[f64; 2]]; 3] = [
        &PENTAGON,
        &TRAPEZOID,
        &[[0., 0.], [40., 0.], [30., 20.], [10., 20.]],
    ];
    let mut plans: Vec<(Analysis, Plan)> = shapes
        .iter()
        .map(|c| {
            let a = part(c);
            let p = plan(
                &a,
                json!({"faces": {"face:1": {"profile_corners": "angle"}}}),
            );
            (a, p)
        })
        .collect();
    // Each profile under the `angle` choice: by default its corners are located linearly.
    let angle = |faces: &[usize]| -> Spec {
        let faces: serde_json::Map<String, serde_json::Value> = faces
            .iter()
            .map(|f| (format!("face:{f}"), json!({"profile_corners": "angle"})))
            .collect();
        serde_json::from_value(json!({ "faces": faces })).unwrap()
    };
    for (f, faces) in [
        ("profile-pentagon.step", &[6][..]),
        ("profile-trapezoid.step", &[5]),
        ("profile-triangle.step", &[4]),
    ] {
        let a = model::analyse_native(
            &std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/coverage-holes")
                .join(f),
        )
        .unwrap()
        .1;
        let p = model::plan_recovered(&a, &angle(faces)).unwrap();
        plans.push((a, p));
    }
    if let Some(dir) = corpus::dir() {
        for (f, faces) in [
            ("nist/nist_ftc_08_asme1_rc.stp", &[1][..]),
            ("nist/nist_ctc_04_asme1_rd.stp", &[381, 396]),
            ("nist/nist_ctc_03_asme1_rc.stp", &[37]),
            ("cadgenbench_inputs/cgb207.step", &[238]),
            ("mfcadpp/10046.step", &[10, 14, 16, 20]),
            ("mfcadpp/10049.step", &[1]),
        ] {
            let a = model::analyse_native(&dir.join(f)).unwrap().1;
            let p = model::plan_recovered(&a, &angle(faces)).unwrap();
            plans.push((a, p));
        }
    }
    // Twelve heights of the sheet's 2.2 mm label text: CTC-04's former 100 mm sweep fails this.
    let reach = 12.0 * 2.2 + 0.5;
    let mut checked = 0;
    for (_, p) in &plans {
        for d in angles(p) {
            let Some(placed) = &d.placed else { continue };
            // The two witness lines meet at the vertex; the arc's chords lie at one radius.
            let [e0, e1] = [placed.lines[0], placed.lines[1]];
            let r = [e0[1][0] - e0[0][0], e0[1][1] - e0[0][1]];
            let t = [e1[1][0] - e1[0][0], e1[1][1] - e1[0][1]];
            let det = r[0] * t[1] - r[1] * t[0];
            let k = ((e1[0][0] - e0[0][0]) * t[1] - (e1[0][1] - e0[0][1]) * t[0]) / det;
            let vertex = [e0[0][0] + k * r[0], e0[0][1] + k * r[1]];
            let q = placed.lines[2][0];
            let radius = (q[0] - vertex[0]).hypot(q[1] - vertex[1]);
            assert!(radius <= reach, "{}: radius {radius} beyond {reach}", d.id);
            let b = placed.label_box;
            let far = [b[0], b[2]]
                .into_iter()
                .flat_map(|x| [b[1], b[3]].map(|y| (x - vertex[0]).hypot(y - vertex[1])))
                .fold(0.0, f64::max);
            assert!(
                far <= reach,
                "{}: label reaches {far} from its corner",
                d.id
            );
            let centre = [(b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0];
            let from = (centre[0] - vertex[0]).hypot(centre[1] - vertex[1]);
            let size = (b[2] - b[0]).max(b[3] - b[1]);
            assert!((from - radius).abs() <= size, "{}: label off its arc", d.id);
            checked += 1;
        }
    }
    // The clean fixture parts place most of their corners.
    assert!(checked >= 4, "{checked} placed profile angles checked");
}

/// The page vertex and arc radius of a placed angle: where its witness lines meet, and how far
/// its arc's first chord starts from there.
fn vertex_and_radius(placed: &draftwright::dims::Placed) -> ([f64; 2], f64) {
    let [e0, e1] = [placed.lines[0], placed.lines[1]];
    let r = [e0[1][0] - e0[0][0], e0[1][1] - e0[0][1]];
    let t = [e1[1][0] - e1[0][0], e1[1][1] - e1[0][1]];
    let det = r[0] * t[1] - r[1] * t[0];
    let k = ((e1[0][0] - e0[0][0]) * t[1] - (e1[0][1] - e0[0][1]) * t[0]) / det;
    let vertex = [e0[0][0] + k * r[0], e0[0][1] + k * r[1]];
    let q = placed.lines[2][0];
    (vertex, (q[0] - vertex[0]).hypot(q[1] - vertex[1]))
}

/// #227: an acute corner's angle is drawn near its corner. The label sits outside the arc, so
/// the arc need not leave the label's width between its arrowheads: the triangle fixture's 45°
/// arc stays within six label heights of its vertex (it took about 18 mm, eight heights, when
/// it did), its label outside the arc and within the sector, and every corner of the triangle
/// and the trapezoid finds a clear place (each dropped its 63.435° when the arcs were wider).
#[test]
fn an_acute_corner_angle_is_drawn_near_its_corner() {
    let plans: Vec<Plan> = ["profile-triangle.step", "profile-trapezoid.step"]
        .iter()
        .map(|f| {
            let a = model::analyse_native(
                &std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                    .join("tests/fixtures/coverage-holes")
                    .join(f),
            )
            .unwrap()
            .1;
            model::plan_recovered(&a, &Spec::default()).unwrap()
        })
        .collect();
    for p in &plans {
        for d in angles(p) {
            assert!(d.placed.is_some(), "{} {}: {:?}", d.id, d.label, d.state);
        }
    }
    let acute = angles(&plans[0])
        .into_iter()
        .find(|d| (d.value - 45.0).abs() < 1e-6)
        .expect("the triangle's 45° corner gets its angle");
    let placed = acute.placed.as_ref().unwrap();
    let (vertex, radius) = vertex_and_radius(placed);
    assert!(
        radius <= 6.0 * 2.2,
        "{}: arc radius {radius} mm from its corner",
        acute.id
    );
    // Its label is outside the arc and within the sector between its witness lines.
    let b = placed.label_box;
    let centre = [(b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0];
    assert!((centre[0] - vertex[0]).hypot(centre[1] - vertex[1]) > radius);
    let side = |l: [[f64; 2]; 2]| {
        let d = [l[1][0] - l[0][0], l[1][1] - l[0][1]];
        d[0] * (centre[1] - vertex[1]) - d[1] * (centre[0] - vertex[0])
    };
    let [s0, s1] = [side(placed.lines[0]), side(placed.lines[1])];
    assert!(s0 * s1 < 0.0, "{}: label outside its sector", acute.id);
}

/// Minimum distance from an arc chord to the axis-aligned planned label box.
/// Include clipped intersections, chord endpoints and projections of all box corners.
fn chord_box_gap(line: [[f64; 2]; 2], b: [f64; 4]) -> f64 {
    let delta = [0, 1].map(|k| line[1][k] - line[0][k]);
    let (mut lo, mut hi) = (0.0_f64, 1.0_f64);
    for k in 0..2 {
        if delta[k].abs() < 1e-12 {
            if line[0][k] < b[k] || line[0][k] > b[k + 2] {
                hi = -1.0;
                break;
            }
        } else {
            let a = (b[k] - line[0][k]) / delta[k];
            let z = (b[k + 2] - line[0][k]) / delta[k];
            lo = lo.max(a.min(z));
            hi = hi.min(a.max(z));
        }
    }
    if lo <= hi {
        return 0.0;
    }
    let endpoint_gap = line.into_iter().map(|p| {
        let d = [0, 1].map(|k| (b[k] - p[k]).max(p[k] - b[k + 2]).max(0.0));
        d[0].hypot(d[1])
    });
    let squared = delta[0] * delta[0] + delta[1] * delta[1];
    let corner_gap = [b[0], b[2]].into_iter().flat_map(|x| {
        [b[1], b[3]].map(move |y| {
            let v = [x - line[0][0], y - line[0][1]];
            let t = if squared > 0.0 {
                ((v[0] * delta[0] + v[1] * delta[1]) / squared).clamp(0.0, 1.0)
            } else {
                0.0
            };
            (v[0] - t * delta[0]).hypot(v[1] - t * delta[1])
        })
    });
    endpoint_gap.chain(corner_gap).fold(f64::INFINITY, f64::min)
}

/// Angular text stays attached to its arc on every recovered quality drawing.
/// The normal gap is 1 paper mm; allow 0.01 mm for the chord approximation
/// (at most 0.005 mm sagitta) and floating-point placement arithmetic.
#[test]
fn quality_angular_labels_stay_with_their_arcs() {
    let Some(root) = corpus::dir() else {
        return;
    };
    let mut count = 0;
    for file in include_str!("../tools/quality_parts.txt").lines() {
        let (_, analysis) = crate::cache::native(&root.join(file));
        let plan = crate::cache::default_plan(&analysis, true);
        for dimension in plan.dimensions.iter().filter(|d| d.kind == "angular") {
            let Some(placed) = &dimension.placed else {
                continue;
            };
            let gap = placed
                .lines
                .iter()
                .skip(2)
                .map(|&line| chord_box_gap(line, placed.label_box))
                .fold(f64::INFINITY, f64::min);
            assert!(
                gap <= 1.01,
                "{file}: {} ({}) floats {gap:.3} mm from its arc",
                dimension.id,
                dimension.label
            );
            // A small nearest-box gap alone can hide a slide along the interior
            // of the sweep. Text must be on the bisector, or beyond an arrowhead
            // with the same nearby arc continuing to it.
            let [a, b] = placed.lines[0];
            let [c, e] = placed.lines[1];
            let u = [b[0] - a[0], b[1] - a[1]];
            let v = [e[0] - c[0], e[1] - c[1]];
            let cross = |u: [f64; 2], v: [f64; 2]| u[0] * v[1] - u[1] * v[0];
            let t = cross([c[0] - a[0], c[1] - a[1]], v) / cross(u, v);
            let vertex = [a[0] + t * u[0], a[1] + t * u[1]];
            let unit = |p: [f64; 2]| {
                let r = [p[0] - vertex[0], p[1] - vertex[1]];
                let n = r[0].hypot(r[1]);
                [r[0] / n, r[1] / n]
            };
            let rays = [unit(placed.arrows[0][0]), unit(placed.arrows[1][0])];
            let bisector = [rays[0][0] + rays[1][0], rays[0][1] + rays[1][1]];
            let text = unit([
                (placed.label_box[0] + placed.label_box[2]) / 2.0,
                (placed.label_box[1] + placed.label_box[3]) / 2.0,
            ]);
            let dot = |u: [f64; 2], v: [f64; 2]| u[0] * v[0] + u[1] * v[1];
            let midpoint = cross(bisector, text).abs() <= 1e-6 && dot(bisector, text) > 0.0;
            let beyond = dot(bisector, text) < dot(bisector, rays[0]) - 1e-6;
            assert!(
                midpoint || beyond,
                "{file}: {} slid into the sector away from its midpoint",
                dimension.id
            );
            count += 1;
        }
        if file == "mfcadpp/10046.step" {
            // Existing readable midpoints take priority over earlier items'
            // new extension candidates; gaining another angle cannot evict these.
            for id in [
                "d:envelope/profile_angle.angle.f16_e52_e63",
                "d:envelope/profile_angle.angle.f16_e44_e46",
                "d:envelope/profile_angle.angle.f16_e46_e22",
                "d:envelope/profile_angle.angle.f20_e36_e74",
            ] {
                assert!(
                    plan.dimensions
                        .iter()
                        .any(|d| d.id == id && d.placed.is_some()),
                    "10046 lost an existing readable angular midpoint: {id}"
                );
            }
        }
        if file == "nist/nist_ctc_03_asme1_rc.stp" {
            assert!(
                plan.dimensions
                    .iter()
                    .any(|d| d.kind == "angular" && d.label == "2× 135°" && d.placed.is_some()),
                "CTC-03 must retain its counted angular dimension"
            );
        }
    }
    assert!(
        count > 0,
        "the quality survey must exercise angular dimensions"
    );
}

/// #519: `C<leg>` is for edge breaks. The octagon's 5 × 45° chamfers are a quarter of the part's
/// 20 mm width and stay `C5`; a 2 × 45° chamfer certainly does.
#[test]
fn a_small_45_degree_chamfer_stays_c() {
    let chamfered = part(&[[0., 0.], [40., 0.], [40., 18.], [38., 20.], [0., 20.]]);
    let p = plan(&chamfered, json!({}));
    let found = chamfers(&p);
    assert_eq!(found.len(), 1, "{found:?}");
    assert_eq!(found[0].label, "C2");
}

/// #519: a 45° cut of 12 mm across a corner of a 40 × 20 part (30% of its length; the limit is
/// 25% of the larger extent) is a feature of the part: no `C` callout, its two stations instead.
#[test]
fn a_large_45_degree_corner_cut_is_dimensioned_as_a_feature() {
    let a = part(&[[0., 0.], [40., 0.], [40., 8.], [28., 20.], [0., 20.]]);
    let p = plan(&a, json!({}));
    assert!(chamfers(&p).is_empty(), "{:?}", chamfers(&p));
    assert!(
        p.dimensions.iter().all(|d| !d.label.starts_with('C')),
        "{:?}",
        p.dimensions.iter().map(|d| &d.label).collect::<Vec<_>>()
    );
    let found = stations(&p);
    assert_eq!(found.len(), 2, "{found:?}");
    assert!(
        found
            .iter()
            .any(|d| d.id.contains("location_y") && station_reaches(d, 8., 0., 20.))
    );
    assert!(
        found
            .iter()
            .any(|d| d.id.contains("location_x") && station_reaches(d, 28., 0., 40.))
    );
}

/// #520: a cut designed as a start point and an angle (8 mm up the right side, 25° from the
/// vertical) is stated so: the start's station and `25°` (not the corner's 155°), and no second
/// computed length for where it meets the top. The far end follows from them.
#[test]
fn a_cut_from_a_round_start_is_a_station_and_its_acute_angle() {
    let far = 40. - 12. * 25f64.to_radians().tan();
    let a = part(&[[0., 0.], [40., 0.], [40., 8.], [far, 20.], [0., 20.]]);
    let corners = profile_angles::requirements(&a);
    // Both corners are obtuse in the profile: 155° and 115°.
    assert_eq!(corners.len(), 2);
    let included: Vec<f64> = corners.iter().map(Corner::degrees).collect();
    assert!(
        included.iter().any(|d| (d - 155.).abs() < 1e-6),
        "{included:?}"
    );
    let p = plan(&a, json!({}));
    let found = angles(&p);
    assert_eq!(found.len(), 1, "{found:?}");
    assert_eq!(found[0].label, "25°");
    assert_eq!(found[0].view, "v:plan");
    // It claims both corners of the cut.
    assert_eq!(found[0].measurements.len(), 2);
    let found = stations(&p);
    assert_eq!(found.len(), 1, "{found:?}");
    assert!(
        found[0].id.contains("location_y") && station_reaches(found[0], 8., 0., 20.),
        "{}",
        found[0].label
    );
    assert!(chamfers(&p).is_empty());
    // A Spec asking for the legs instead is honoured: its angle keeps the corner's own id.
    let p = plan(
        &a,
        json!({"faces": {"face:1": {"profile_corners": "linear"}}}),
    );
    assert!(angles(&p).is_empty());
    assert_eq!(stations(&p).len(), 2);
}

/// #519, #520, Jern's corner-cut block (`corner-cuts.step.gz`, 2.000 × 2.500 × 2.000 in, the file
/// in mm). Its large planar cut starts 0.500 in from one face at 25°; before, it printed `C27.112`
/// (the top face's leg), the legs `12.7` and `27.112` on the side and the obtuse 135°, 155° and
/// 115°. Now: the side view's `12.7` and `25°`, the plan's `45°` (its location follows from the
/// side view's start and angle), no `C`, no obtuse angle and no computed 1.067 in. The small flat
/// beside the bore's exit is dimensioned by the legs of its cut, nothing round to start from.
#[test]
fn jerns_corner_cut_is_a_start_point_and_acute_angles() {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts/corner-cuts.step.gz");
    let a = model::analyse_native(&path).unwrap().1;
    let p = plan(&a, json!({}));
    let labels: Vec<(&str, &str)> = p
        .dimensions
        .iter()
        .filter(|d| d.id.starts_with("d:envelope/profile_"))
        .map(|d| (d.view.as_str(), d.label.as_str()))
        .collect();
    assert!(labels.contains(&("v:side", "25°")), "{labels:?}");
    assert!(labels.contains(&("v:side", "12.7")), "{labels:?}");
    assert!(labels.contains(&("v:plan", "45°")), "{labels:?}");
    for (_, label) in &labels {
        assert!(!label.starts_with('C'), "{labels:?}");
        for obtuse in ["155°", "135°", "115°", "27.112", "23.688"] {
            assert_ne!(*label, obtuse, "{labels:?}");
        }
    }
    // The compound cut is stated only where an angle is true: each angle is claimed by corners of
    // a face seen square-on in its own view, never an angle of the plane foreshortened in another.
    for d in p.dimensions.iter().filter(|d| d.kind == "angular") {
        for m in &d.measurements {
            let face: usize = m
                .feature
                .strip_prefix("outer_profile:f")
                .and_then(|s| s.split(':').next())
                .and_then(|s| s.parse().ok())
                .unwrap();
            let source = a.outer_profiles.iter().find(|s| s.face == face).unwrap();
            let axis = (0..3)
                .max_by(|&i, &j| {
                    source.profile.normal[i]
                        .abs()
                        .total_cmp(&source.profile.normal[j].abs())
                })
                .unwrap();
            assert_eq!(d.view, format!("v:{}", ["side", "front", "plan"][axis]));
        }
    }
    // Each station is stated once across the views: the small flat's 11.542 is in the plan only.
    let mut stated: Vec<(String, String)> = p
        .dimensions
        .iter()
        .filter(|d| d.id.starts_with("d:envelope/profile_location_"))
        .map(|d| {
            let axis = d.id.split("profile_location_").nth(1).unwrap()[..1].to_string();
            (axis, d.label.clone())
        })
        .collect();
    let all = stated.len();
    stated.sort();
    stated.dedup();
    assert_eq!(
        stated.len(),
        all,
        "a station is dimensioned twice: {stated:?}"
    );
    // Every corner is stated and located: nothing left for lint to report.
    let r = review(&path, json!({}));
    let issues: Vec<_> = r
        .lint
        .iter()
        .filter(|i| {
            i.code.starts_with("angular_requirement_") || i.code == "profile_corner_unlocated"
        })
        .map(|i| (&i.code, &i.requirements))
        .collect();
    assert!(issues.is_empty(), "{issues:?}");
    // Nothing about the corners or their angles needs fixing before printing: only the bore's
    // location (#521) and the blank tolerance remain.
    let json = serde_json::to_value(&r).unwrap();
    let mut fixes: Vec<String> = ["problems", "lint", "structural"]
        .iter()
        .flat_map(|k| json[k].as_array().cloned().unwrap_or_default())
        .filter(|f| f["tier"] == "fix_before_printing")
        .map(|f| f["code"].as_str().unwrap().to_string())
        .collect();
    fixes.sort();
    assert_eq!(
        fixes,
        [
            "feature_not_dimensioned",
            "feature_not_located",
            "general_tolerance_missing"
        ],
        "{fixes:?}"
    );
    // In inches the start is `.500`.
    let p = plan(&a, json!({"units": "in"}));
    let labels: Vec<&str> = p.dimensions.iter().map(|d| d.label.as_str()).collect();
    assert!(labels.contains(&".500"), "{labels:?}");
    assert!(labels.contains(&"25°"), "{labels:?}");
}

/// #519: the limit is a fraction of the part's larger extent in the view, so a 1 mm edge break on
/// a 40 × 3 profile (a third of its thickness) stays `C1`.
#[test]
fn a_small_chamfer_on_a_thin_profile_stays_c() {
    let a = part(&[[0., 0.], [39., 0.], [40., 1.], [40., 3.], [0., 3.]]);
    let p = plan(&a, json!({}));
    let found = chamfers(&p);
    assert_eq!(found.len(), 1, "{found:?}");
    assert_eq!(found[0].label, "C1");
}

/// #520: start point and angle only for an angle that was designed: whole or half degrees. A cut
/// that ends at a computed intersection (mfcadpp/10155's 2.529°) keeps its two lengths, never a
/// dropped fractional angle.
#[test]
fn a_cut_at_a_computed_angle_keeps_its_two_lengths() {
    let far = 40. - 12. * 2.529f64.to_radians().tan();
    let a = part(&[[0., 0.], [40., 0.], [40., 8.], [far, 20.], [0., 20.]]);
    let p = plan(&a, json!({}));
    assert!(angles(&p).is_empty(), "{:?}", angles(&p));
    assert_eq!(stations(&p).len(), 2, "{:?}", stations(&p));
}

/// #520, the review's finding on 18ec88f0: a cut leaning 20°, 25° or 30° from the vertical off a
/// 40 × 20 × 10 block's side (`block-cut-<n>.step.gz`, 8 mm up the right side) states the 8 and
/// the angle (without recognition; with it the cut is a chamfer feature), drawn at the end whose reference lies along no edge, and the full review has
/// nothing to fix about it. Before, the angle was measured from the side edge's own line and
/// lint refused it as the corner's supplement.
#[test]
fn a_cut_leaning_from_its_side_edge_is_review_clean() {
    for degrees in [20, 25, 30] {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join(format!("tests/fixtures/parts/block-cut-{degrees}.step.gz"));
        let none: Spec = serde_json::from_value(json!({})).unwrap();
        let p = api::plan_from(&path, Features::None, &none).unwrap();
        let labels: Vec<&str> = p.dimensions.iter().map(|d| d.label.as_str()).collect();
        assert!(
            labels.contains(&format!("{degrees}°").as_str()),
            "{labels:?}"
        );
        assert!(labels.contains(&"8"), "{labels:?}");
        assert_eq!(
            labels.len(),
            5,
            "no computed length for the far end: {labels:?}"
        );
        // With and without recognition (which calls the cut a chamfer feature of its own).
        for r in [
            api::review_from(&path, Features::None, &none).unwrap(),
            review(&path, json!({})),
        ] {
            let json = serde_json::to_value(&r).unwrap();
            let fixes: Vec<String> = ["problems", "lint", "structural"]
                .iter()
                .flat_map(|k| json[k].as_array().cloned().unwrap_or_default())
                .filter(|f| f["tier"] == "fix_before_printing")
                .map(|f| f["code"].as_str().unwrap().to_string())
                .collect();
            assert_eq!(
                fixes,
                ["general_tolerance_missing"],
                "{degrees}°: {fixes:?}"
            );
            let corner_issues: Vec<_> = r
                .lint
                .iter()
                .filter(|i| i.code.starts_with("angular_") || i.code == "profile_corner_unlocated")
                .map(|i| &i.code)
                .collect();
            assert!(corner_issues.is_empty(), "{degrees}°: {corner_issues:?}");
        }
    }
}
