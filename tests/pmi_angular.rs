//! Angular PMI dimensions (`draftwright::dims::angular`): nist_ctc_01's `60 ±0.5`, its sides
//! against draftwright's reading, its ink against the Analysis, and the refusal of an angle no
//! view shows true.
//!
//! draftwright's `AngularReference` for the record (`extract_pmi_report` on
//! `tests/fixtures/pmi/step/nist_ctc_01_asme1_ap242-e1.stp.gz`, draftwright origin/main, no part
//! frame): vertex (110, -75, -25), first witness (128.76388374866252, -107.5, -25), second
//! (91.23611625133717, -107.49999999999974, -25), sector `minor`; its principal axis z, so
//! draftwright draws it in the plan (`_angular_specs`), as its captured drawing records
//! (`tests/fixtures/pmi_drawings/`).

use std::io::Read as _;
use std::path::{Path, PathBuf};

use draftwright::dims::PlannedDimension;
use draftwright::haecceity::hlr::View;
use draftwright::model::{self, Analysis, Plan, PlannedView, Spec};
use draftwright::pmi::{Reading, Record};

/// The angle's item: record `dimension:#33`, named by its identity (an angular dimension on
/// faces 15 and 17).
const ID: &str = "d:pmi:dimension.angular:f15,17/angular";
const FEATURE: &str = "pmi:dimension.angular:f15,17";
const VERTEX: [f64; 3] = [110.0, -75.0, -25.0];
const FIRST: [f64; 3] = [128.76388374866252, -107.5, -25.0];
const SECOND: [f64; 3] = [91.23611625133717, -107.49999999999974, -25.0];

/// The analysed fixture *name*, decompressed into a directory of the calling *test*'s own: the
/// tests run in parallel, and one must not rewrite the file another is reading.
fn analysed(test: &str, name: &str) -> (draftwright::haecceity::Part, Analysis) {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let gz = std::fs::read(
        root.join("tests/fixtures/pmi/step")
            .join(format!("{name}.gz")),
    )
    .unwrap();
    let mut bytes = Vec::new();
    flate2::read::GzDecoder::new(&gz[..])
        .read_to_end(&mut bytes)
        .unwrap();
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR"))
        .join("pmi_angular")
        .join(test)
        .join(name);
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join(name);
    std::fs::write(&path, bytes).unwrap();
    model::analyse_native(&path).unwrap()
}

fn angular(a: &Analysis) -> &Record {
    let Some(Reading::Read(report)) = &a.pmi else {
        panic!("PMI not read")
    };
    let found: Vec<&Record> = report
        .records
        .iter()
        .filter(|r| r.kind == "angular")
        .collect();
    assert_eq!(found.len(), 1, "one angular record");
    found[0]
}

fn sub(a: [f64; 2], b: [f64; 2]) -> [f64; 2] {
    [a[0] - b[0], a[1] - b[1]]
}

fn len(a: [f64; 2]) -> f64 {
    a[0].hypot(a[1])
}

fn close3(a: [f64; 3], b: [f64; 3], tol: f64) -> bool {
    (0..3).all(|i| (a[i] - b[i]).abs() <= tol)
}

/// World to page in a planned view, as the plan places it: the envelope's projected centre at
/// the view's `at`, at its scale.
fn page(a: &Analysis, v: &PlannedView) -> impl Fn([f64; 3]) -> [f64; 2] {
    let view = View::new(v.direction, v.up).unwrap();
    let [lo, hi] = a.envelope;
    let (mut min, mut max) = ([f64::INFINITY; 2], [f64::NEG_INFINITY; 2]);
    for i in 0..8 {
        let c = [0, 1, 2].map(|k| if i >> k & 1 == 1 { hi[k] } else { lo[k] });
        let q = view.map(c);
        for k in 0..2 {
            min[k] = min[k].min(q[k]);
            max[k] = max[k].max(q[k]);
        }
    }
    let centre = [0.5 * (min[0] + max[0]), 0.5 * (min[1] + max[1])];
    let (at, s) = (v.at, v.scale);
    move |p| {
        let q = view.map(p);
        [
            at[0] + s * (q[0] - centre[0]),
            at[1] + s * (q[1] - centre[1]),
        ]
    }
}

fn the_angle(plan: &Plan) -> &PlannedDimension {
    plan.dimensions
        .iter()
        .find(|d| d.id == ID)
        .unwrap_or_else(|| panic!("{ID} planned; problems: {:?}", plan.problems))
}

#[test]
fn ctc_01s_sides_are_draftwrights() {
    for name in ["nist_ctc_01_asme1_ap242-e1.stp", "nist_ctc_01_merge.step"] {
        let (_, a) = analysed("sides", name);
        let r = angular(&a);
        assert_eq!(r.label, "60 ±0.5");
        let g = r.geometry.as_ref().unwrap();
        assert!(g.reasons.is_empty(), "{name}: {:?}", g.reasons);
        assert_eq!(g.axis.as_deref(), Some("Z"));
        let angle = g.angle.as_ref().unwrap();
        assert!(close3(angle.vertex, VERTEX, 1e-6), "{name}: {angle:?}");
        assert!(close3(angle.first, FIRST, 1e-6), "{name}: {angle:?}");
        assert!(close3(angle.second, SECOND, 1e-6), "{name}: {angle:?}");
        assert!((angle.degrees().unwrap() - 60.0).abs() < 1e-9);
        assert_eq!(angle.principal(), Some(2));
    }
}

/// CTC-01's hexagonal boss centre, averaged from its corners, lies 2.7e-5 short of the 225 at
/// which its holes are located: one span, so one dimension (`225`, the holes'), not `225` and
/// `225.000` side by side; the boss's location is conveyed by it, and the boss is located by the
/// holes' lines on both axes (`400` and `225`).
#[test]
fn ctc_01s_boss_and_holes_on_one_line_are_located_once() {
    let (_, a) = analysed("located_once", "nist_ctc_01_asme1_ap242-e1.stp");
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    let boss = "d:f:polygonal_boss:f104/location.length.y";
    assert!(plan.dimensions.iter().all(|d| d.id != boss));
    let by = plan
        .omitted
        .iter()
        .find_map(|o| match &o.state {
            draftwright::items::State::Covered { by } if o.id == boss => Some(by.clone()),
            _ => None,
        })
        .expect("conveyed");
    let owner = plan.dimensions.iter().find(|d| d.id == by).unwrap();
    assert_eq!(owner.label, "225");
    assert!(owner.placed.is_some());
    // No two drawn linear dimensions in one view state the same span.
    let spans: Vec<_> = plan
        .dimensions
        .iter()
        .filter(|d| d.kind == "linear" && d.placed.is_some())
        .map(|d| {
            (
                d.view.as_str(),
                d.measurements[0].parameter.as_str(),
                (d.value * 1e3).round(),
            )
        })
        .collect();
    let y225 = spans
        .iter()
        .filter(|s| s.0 == "v:plan" && s.1 == "location.length.y" && s.2 == 225_000.0)
        .count();
    assert_eq!(y225, 1, "{spans:?}");
    let unlocated: Vec<_> = plan
        .problems
        .iter()
        .filter(|p| p.code == "feature_not_located" && p.id.contains("f:polygonal_boss:f104/"))
        .map(|p| &p.message)
        .collect();
    assert!(unlocated.is_empty(), "{unlocated:?}");
}

/// The ink: extension lines along the sides' projected traces from just past each witness, an
/// unbroken arc about the projected vertex spanning the 60° between them, arrowheads at its ends
/// on the sides, and the label `60° ±0.5°` (its unit on every value) on the bisector just outside the arc (ISO 129-1, #227).
#[test]
fn ctc_01s_angle_is_drawn_true_in_the_plan() {
    let (part, a) = analysed("drawn", "nist_ctc_01_asme1_ap242-e1.stp");
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    let d = the_angle(&plan);
    assert_eq!(d.kind, "angular");
    assert_eq!(d.view, "v:plan");
    assert_eq!(d.label, "60° ±0.5°");
    assert_eq!(d.terms, ["60°", "±0.5°"]);
    assert_eq!(d.value, 60.0);
    // The source annotation owns both its PMI requirement and the same physical
    // V-notch angle; the automatic duplicate must not print a second arc.
    assert_eq!(d.measurements.len(), 2);
    assert_eq!(d.measurements[0].feature, FEATURE);
    assert_eq!(d.measurements[0].parameter, "angular");
    assert_eq!(d.measurements[0].value, 60.0);
    assert_eq!(d.measurements[1].feature, "notch:f97:e104:e108");
    assert_eq!(d.measurements[1].parameter, "notch.angle");
    assert!((d.measurements[1].value - 60.0).abs() < 1e-9);
    assert_eq!(
        plan.dimensions
            .iter()
            .filter(|d| d.kind == "angular")
            .count(),
        2
    );
    assert!(
        plan.omitted
            .iter()
            .any(|d| d.id == "d:envelope/notch.angle.f97_e104_e108"
                && matches!(&d.state, draftwright::items::State::Covered { by } if by == ID))
    );
    assert!(
        !plan.problems.iter().any(|p| p
            .subjects
            .iter()
            .chain(&p.refs)
            .any(|s| s == "dimension:#33")),
        "the record is not refused"
    );

    let g = angular(&a).geometry.as_ref().unwrap();
    let angle = g.angle.as_ref().unwrap();
    let v = plan.views.iter().find(|v| v.id == "v:plan").unwrap();
    let to_page = page(&a, v);
    let vertex = to_page(angle.vertex);
    let witnesses = [to_page(angle.first), to_page(angle.second)];
    let p = d.placed.as_ref().expect("placed");

    // The sides' traces: from the vertex towards each witness, in page directions.
    let rays: Vec<[f64; 2]> = witnesses
        .iter()
        .map(|w| {
            let r = sub(*w, vertex);
            [r[0] / len(r), r[1] / len(r)]
        })
        .collect();
    let along = |q: [f64; 2], r: [f64; 2]| {
        let o = sub(q, vertex);
        (o[0] * r[1] - o[1] * r[0]).abs() <= 1e-9 && o[0] * r[0] + o[1] * r[1] > 0.0
    };
    // Two extension lines, one on each side's trace, from past its witness outwards.
    let extensions: Vec<&[[f64; 2]; 2]> = p.lines.iter().take(2).collect();
    for (r, w) in rays.iter().zip(&witnesses) {
        let e = extensions
            .iter()
            .find(|l| along(l[0], *r) && along(l[1], *r))
            .unwrap_or_else(|| panic!("an extension line along {r:?}: {:?}", &p.lines[..2]));
        assert!(
            len(sub(e[0], vertex)) > len(sub(*w, vertex)),
            "starts past its witness"
        );
        assert!(len(sub(e[1], vertex)) > len(sub(e[0], vertex)));
    }

    // The arc: every other line a chord of one circle about the vertex.
    let arc: Vec<[f64; 2]> = p.lines[2..].iter().flat_map(|l| [l[0], l[1]]).collect();
    assert!(arc.len() >= 4);
    let radius = len(sub(arc[0], vertex));
    for q in &arc {
        assert!((len(sub(*q, vertex)) - radius).abs() <= 1e-9, "on the arc");
    }
    let max_extension = extensions
        .iter()
        .map(|l| len(sub(l[1], vertex)))
        .fold(0.0, f64::max);
    assert!(max_extension > radius, "extension lines run past the arc");
    // It spans the 60° between the rays (the plan's scale is uniform, so the page angle is the
    // true one), unbroken: each chord starts where the one before it ends (#227).
    let angle_of = |q: [f64; 2]| {
        let o = sub(q, vertex);
        o[1].atan2(o[0])
    };
    let bisector = {
        let b = [rays[0][0] + rays[1][0], rays[0][1] + rays[1][1]];
        b[1].atan2(b[0])
    };
    let offsets: Vec<f64> = arc
        .iter()
        .map(|q| {
            let mut t = angle_of(*q) - bisector;
            while t > std::f64::consts::PI {
                t -= 2.0 * std::f64::consts::PI;
            }
            while t < -std::f64::consts::PI {
                t += 2.0 * std::f64::consts::PI;
            }
            t.to_degrees()
        })
        .collect();
    let lo = offsets.iter().copied().fold(f64::INFINITY, f64::min);
    let hi = offsets.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    assert!(
        (lo + 30.0).abs() < 1e-9 && (hi - 30.0).abs() < 1e-9,
        "{lo} {hi}"
    );
    for pair in p.lines[2..].windows(2) {
        assert!(
            len(sub(pair[0][1], pair[1][0])) <= 1e-9,
            "the arc is unbroken"
        );
    }

    // An arrowhead at each end of the arc, its tip on a side.
    assert_eq!(p.arrows.len(), 2);
    for [tip, tail] in &p.arrows {
        assert!((len(sub(*tip, vertex)) - radius).abs() <= 1e-9);
        assert!(rays.iter().any(|r| along(*tip, *r)), "tip on a side");
        assert!((len(sub(*tip, *tail)) - draftwright::dims::ARROW).abs() < 0.01);
    }

    // The label centred on the bisector, outside the arc and clear of it.
    let b = p.label_box;
    let centre = [0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3])];
    assert!(
        (angle_of(centre) - bisector).abs() < 1e-6,
        "on the bisector"
    );
    assert!(len(sub(centre, vertex)) > radius, "outside the arc");
    assert!(
        p.lines[2..]
            .iter()
            .flat_map(|l| [l[0], l[1]])
            .all(|q| q[0] < b[0] || q[0] > b[2] || q[1] < b[1] || q[1] > b[3]),
        "the label is clear of the arc"
    );

    // Rendered: its text and its claim.
    let font = draftwright::text::Font::open(
        &PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("fonts")
            .join(draftwright::dims::FONT),
    )
    .unwrap();
    let drawing = model::render(&part, &a, &plan, &font).unwrap();
    let annotation = drawing
        .annotations
        .iter()
        .find(|x| x.name == ID)
        .expect("drawn");
    assert_eq!(annotation.view.as_deref(), Some("v:plan"));
    let text: Vec<&str> = annotation
        .runs
        .iter()
        .map(|&i| drawing.text[i].text.as_str())
        .collect();
    assert_eq!(text, ["60° ±0.5°"]);
    assert!(
        annotation
            .claims
            .iter()
            .any(|c| c.feature == FEATURE && c.parameter == "angular")
    );

    // Deterministic to the byte.
    let again = model::plan_recovered(&a, &Spec::default()).unwrap();
    assert_eq!(
        serde_json::to_string(&plan).unwrap(),
        serde_json::to_string(&again).unwrap()
    );
}

/// An angle whose sides' plane no view looks along is refused with draftwright's reason, never
/// drawn foreshortened: ctc_01's record with its second side tilted out of the plan's plane.
#[test]
fn an_angle_no_view_shows_true_is_refused() {
    let (_, mut a) = analysed("refused", "nist_ctc_01_asme1_ap242-e1.stp");
    let Some(Reading::Read(report)) = &mut a.pmi else {
        panic!("PMI not read")
    };
    let r = report
        .records
        .iter_mut()
        .find(|r| r.kind == "angular")
        .unwrap();
    // As the derivation records an oblique pair of sides: no principal view, so no axis.
    let g = r.geometry.as_mut().unwrap();
    let angle = g.angle.as_mut().unwrap();
    angle.second[2] += 10.0;
    assert_eq!(angle.principal(), None);
    g.axis = None;
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    assert!(
        !plan
            .dimensions
            .iter()
            .any(|d| d.id.contains("dimension:#33"))
    );
    let p = plan
        .problems
        .iter()
        .find(|p| p.id == "p:pmi_not_lowered:dimension:#33")
        .unwrap_or_else(|| panic!("refused: {:?}", plan.problems));
    assert_eq!(p.severity, "error");
    assert!(p.message.contains("principal view"), "{}", p.message);
    assert!(
        p.message.contains("none shows the angle true"),
        "{}",
        p.message
    );
}
