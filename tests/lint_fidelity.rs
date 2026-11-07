//! Stream K7 (#43 item 2): lint's checks that what a label states is what its ink draws
//! (`lint::fidelity`, run by `lint::structural::check`, which `review` reports and
//! `tools/quality.py` reads):
//!
//! - **Each check on a deliberately broken input**: a sheet built through the public types, sound
//!   and then broken one way (a span that is not the label's value, an angle whose degrees are
//!   not its rays', an arc off its vertex, a ray along no edge of the view, a remote datum symbol).
//! - **On the port's own drawing** of nist_ctc_01 with its AP242 PMI (linear dimensions, the
//!   `60 ±0.5` angle and three datum symbols): every linear and angular dimension agrees with its
//!   ink, so only its remote datum C is reported; moving one extension line of a dimension or of
//!   the angle is caught.

use std::collections::BTreeMap;
use std::io::Read as _;
use std::path::{Path, PathBuf};

use draftwright::lint::{LintIssue, Severity, structural};
use draftwright::model;
use draftwright::output::{
    Annotation, Curve, Drawing, Drawn, DrawnFrame, DrawnView, Kind, Sheet, TextRun,
};

type P = [f64; 2];

const FIDELITY: [&str; 7] = [
    "label_vs_measured",
    "nominal_rounded",
    "angular_label_vs_geometry",
    "angular_geometry_mismatch",
    "angular_support_mismatch",
    "datum_leader_remote",
    "datum_leader_not_normal",
];

fn run(text: &str, at: P) -> TextRun {
    TextRun {
        text: text.into(),
        at,
        size: 3.5,
        rotation: 0.0,
        font: "IBM Plex Mono".into(),
        h_align: draftwright::output::HAlign::Left,
        v_align: draftwright::output::VAlign::Baseline,
    }
}

/// An A3 sheet with one view, `v:front`, drawn at *scale* with *edges*, and *items*, each with
/// its label's text.
fn sheet(scale: f64, edges: Vec<Curve>, items: Vec<(Drawn, &str)>) -> Drawing {
    let mut text = Vec::new();
    let mut annotations = Vec::new();
    let mut drawn = Vec::new();
    for (d, label) in items {
        text.push(run(label, [d.ink[0], d.ink[1]]));
        annotations.push(Annotation {
            name: d.name.clone(),
            view: Some("v:front".into()),
            runs: vec![text.len() - 1],
            ..Annotation::default()
        });
        drawn.push(d);
    }
    Drawing {
        page: [420.0, 297.0],
        layers: Vec::new(),
        link: None,
        text,
        annotations,
        sheet: Sheet {
            characteristics: Vec::new(),
            inspection_enabled: false,
            label_text: Default::default(),
            precision: Default::default(),
            references: Vec::new(),
            drawable: Some([10.0, 10.0, 410.0, 287.0]),
            views: vec![DrawnView {
                name: "v:front".into(),
                bbox: [50.0, 50.0, 250.0, 250.0],
                edges,
            }],
            items: drawn,
            fields: Vec::new(),
            centre_marks: BTreeMap::new(),
            frames: BTreeMap::from([(
                "front".to_string(),
                DrawnFrame {
                    direction: [0.0, -1.0, 0.0],
                    up: [0.0, 0.0, 1.0],
                    at: [150.0, 150.0],
                    scale,
                    half_axis: None,
                    broken: None,
                },
            )]),
        },
        problems: Vec::new(),
        spec: None,
    }
}

fn bbox(points: impl Iterator<Item = P>) -> [f64; 4] {
    points.fold(
        [
            f64::INFINITY,
            f64::INFINITY,
            f64::NEG_INFINITY,
            f64::NEG_INFINITY,
        ],
        |b, p| {
            [
                b[0].min(p[0]),
                b[1].min(p[1]),
                b[2].max(p[0]),
                b[3].max(p[1]),
            ]
        },
    )
}

fn dimension(
    name: &str,
    kind: Kind,
    lines: Vec<[P; 2]>,
    label: [f64; 4],
    elbow: Option<P>,
) -> Drawn {
    let ink = bbox(
        lines
            .iter()
            .flatten()
            .copied()
            .chain([[label[0], label[1]], [label[2], label[3]]]),
    );
    Drawn {
        datum_triangle: None,
        heads: Vec::new(),
        name: name.into(),
        kind,
        ink,
        label: Some(label),
        lines,
        elbow,
        interior: false,
    }
}

/// A horizontal linear dimension below y 100 whose extension lines stand at *x0* and *x1*.
fn linear(x0: f64, x1: f64) -> Drawn {
    dimension(
        &format!("d:f:step:{x0}/step.length"),
        Kind::Dimension,
        vec![
            [[x0, 99.0], [x0, 89.0]],
            [[x1, 99.0], [x1, 89.0]],
            [[x0, 90.0], [x1, 90.0]],
        ],
        [x0 + 2.0, 91.0, x0 + 8.0, 94.0],
        None,
    )
}

fn found<'a>(issues: &'a [LintIssue], code: &str) -> Vec<&'a LintIssue> {
    issues.iter().filter(|i| i.code == code).collect()
}

fn fidelity(issues: &[LintIssue]) -> Vec<String> {
    let mut out: Vec<String> = issues
        .iter()
        .filter(|i| FIDELITY.contains(&i.code.as_str()))
        .map(|i| format!("{} {}", i.code, i.annotation.as_deref().unwrap_or("")))
        .collect();
    out.sort();
    out
}

#[test]
fn a_linear_label_states_its_drawn_span_divided_by_the_view_scale() {
    // 25 mm at 2:1 is 50 mm on the page.
    let sound = sheet(2.0, Vec::new(), vec![(linear(100.0, 150.0), "25")]);
    assert!(fidelity(&structural::check(&sound)).is_empty());
    // A wrong endpoint: 30 mm drawn, 25 stated.
    let wrong = structural::check(&sheet(2.0, Vec::new(), vec![(linear(100.0, 160.0), "25")]));
    let [i] = found(&wrong, "label_vs_measured")[..] else {
        panic!("{wrong:?}")
    };
    assert_eq!(i.severity, Severity::Error);
    assert!(i.message.contains("16.7%"), "{}", i.message);
    // 1% off is a warning; a reference, a tolerance and a counted dimension read their value.
    let near = structural::check(&sheet(
        2.0,
        Vec::new(),
        vec![(linear(100.0, 150.5), "(25 ±0.1)")],
    ));
    assert_eq!(
        found(&near, "label_vs_measured")[0].severity,
        Severity::Warning
    );
    for label in ["(25)", "25 ±0.1", "3× 25", "2× ⌀25", "⌀25 h7"] {
        let ok = structural::check(&sheet(2.0, Vec::new(), vec![(linear(100.0, 150.0), label)]));
        assert!(fidelity(&ok).is_empty(), "{label}: {ok:?}");
    }
    // An inch label is compared in millimetres: 0.30 INCH is 7.62 mm.
    let inch = structural::check(&sheet(
        1.0,
        Vec::new(),
        vec![(linear(100.0, 107.62), "0.30 ±0.02 INCH")],
    ));
    assert!(fidelity(&inch).is_empty(), "{inch:?}");
    // An angle's degrees never meet a linear span.
    let angle = structural::check(&sheet(1.0, Vec::new(), vec![(linear(100.0, 150.0), "30°")]));
    assert!(fidelity(&angle).is_empty());
}

#[test]
fn a_correctly_rounded_label_is_noted_once_per_sheet() {
    // 25.04 prints as 25 at one place: no disagreement, one note naming the largest.
    let drawing = sheet(
        1.0,
        Vec::new(),
        vec![(linear(100.0, 125.04), "25"), (linear(200.0, 210.02), "10")],
    );
    let issues = structural::check(&drawing);
    assert!(found(&issues, "label_vs_measured").is_empty());
    let [i] = found(&issues, "nominal_rounded")[..] else {
        panic!("{issues:?}")
    };
    assert_eq!(i.severity, Severity::Info);
    assert!(
        i.message.starts_with("2 dimension(s)") && i.message.contains("'25'"),
        "{}",
        i.message
    );
    // A bare label written to two places asserts them: 25.04 drawn for 25.4 is off (by 1.4%, past the 0.5% floor).
    let precise = structural::check(&sheet(
        1.0,
        Vec::new(),
        vec![(linear(100.0, 125.04), "25.40")],
    ));
    assert_eq!(found(&precise, "label_vs_measured").len(), 1);
}

const VERTEX: P = [150.0, 150.0];

fn polar(r: f64, degrees: f64) -> P {
    let t = degrees.to_radians();
    [VERTEX[0] + r * t.cos(), VERTEX[1] + r * t.sin()]
}

/// An angle about [`VERTEX`] between rays at *a* and *b* degrees: extension lines from 5 mm to
/// past the arc, the arc at *radius* as chords either side of the label.
fn angle(a: f64, b: f64, radius: f64) -> Drawn {
    let mut lines = vec![
        [polar(5.0, a), polar(radius + 2.0, a)],
        [polar(5.0, b), polar(radius + 2.0, b)],
    ];
    let mid = (a + b) / 2.0;
    for (from, to) in [(a, mid - 5.0), (mid + 5.0, b)] {
        let n = 8;
        for k in 0..n {
            let t = |k: usize| from + (to - from) * k as f64 / n as f64;
            lines.push([polar(radius, t(k)), polar(radius, t(k + 1))]);
        }
    }
    let at = polar(radius, mid);
    dimension(
        "d:pmi:dimension.angular:f1,2/angular",
        Kind::Dimension,
        lines,
        [at[0] - 4.0, at[1] - 1.5, at[0] + 4.0, at[1] + 1.5],
        None,
    )
}

/// The view's edges along both rays of a 0°–60° corner, as its faces project edge-on.
fn corner() -> Vec<Curve> {
    vec![
        Curve::Line(VERTEX, polar(60.0, 0.0)),
        Curve::Line(VERTEX, polar(60.0, 60.0)),
    ]
}

#[test]
fn an_angle_states_the_degrees_between_its_rays() {
    let sound = structural::check(&sheet(
        1.0,
        corner(),
        vec![(angle(0.0, 60.0, 20.0), "60 ±0.5")],
    ));
    assert!(fidelity(&sound).is_empty(), "{sound:?}");
    let wrong = structural::check(&sheet(
        1.0,
        corner(),
        vec![(angle(0.0, 60.0, 20.0), "45 ±0.5")],
    ));
    assert_eq!(
        fidelity(&wrong),
        ["angular_label_vs_geometry d:pmi:dimension.angular:f1,2/angular"]
    );
    // Within half the last printed place: 60.4 rays under 60 are fine, 60.6 not.
    let corner_at = |b: f64| {
        vec![
            Curve::Line(VERTEX, polar(60.0, 0.0)),
            Curve::Line(VERTEX, polar(60.0, b)),
        ]
    };
    let ok = structural::check(&sheet(
        1.0,
        corner_at(60.4),
        vec![(angle(0.0, 60.4, 20.0), "60")],
    ));
    assert!(fidelity(&ok).is_empty(), "{ok:?}");
    let off = structural::check(&sheet(
        1.0,
        corner_at(60.6),
        vec![(angle(0.0, 60.6, 20.0), "60")],
    ));
    assert_eq!(found(&off, "angular_label_vs_geometry").len(), 1);
}

#[test]
fn an_angle_draws_an_arc_about_its_vertex_with_extension_lines_past_it() {
    let code = "angular_geometry_mismatch";
    // Parallel extension lines have no vertex.
    let mut parallel = angle(0.0, 60.0, 20.0);
    parallel.lines[1] = [[150.0, 160.0], [180.0, 160.0]];
    let issues = structural::check(&sheet(1.0, corner(), vec![(parallel, "60")]));
    assert!(
        found(&issues, code)[0].message.contains("unusable"),
        "{issues:?}"
    );
    // One chord off the circle.
    let mut bent = angle(0.0, 60.0, 20.0);
    bent.lines[3][1] = polar(21.0, 10.0);
    let issues = structural::check(&sheet(1.0, corner(), vec![(bent, "60")]));
    assert_eq!(found(&issues, code).len(), 1, "{issues:?}");
    // Only one half of the arc drawn.
    let mut half = angle(0.0, 60.0, 20.0);
    half.lines.truncate(2 + 8);
    let issues = structural::check(&sheet(1.0, corner(), vec![(half, "60")]));
    assert_eq!(found(&issues, code).len(), 1, "{issues:?}");
    // An extension line stopping short of the arc.
    let mut short = angle(0.0, 60.0, 20.0);
    short.lines[0] = [polar(5.0, 0.0), polar(15.0, 0.0)];
    let issues = structural::check(&sheet(1.0, corner(), vec![(short, "60")]));
    assert_eq!(found(&issues, code).len(), 1, "{issues:?}");
    // An arc running past a ray, out of its sector.
    let mut wide = angle(0.0, 60.0, 20.0);
    wide.lines.push([polar(20.0, 60.0), polar(20.0, 70.0)]);
    let issues = structural::check(&sheet(1.0, corner(), vec![(wide, "60")]));
    assert_eq!(found(&issues, code).len(), 1, "{issues:?}");
}

#[test]
fn an_angles_rays_are_the_corners_physical_edges() {
    let code = "angular_support_mismatch";
    // No edge of the view along one ray: the angle is not between the corner's edges.
    let one = vec![
        Curve::Line(VERTEX, polar(60.0, 0.0)),
        Curve::Line(VERTEX, polar(60.0, 75.0)),
    ];
    let issues = structural::check(&sheet(1.0, one, vec![(angle(0.0, 60.0, 20.0), "60")]));
    assert_eq!(
        fidelity(&issues),
        [format!("{code} d:pmi:dimension.angular:f1,2/angular")]
    );
    // An edge on the ray's line but behind the vertex is not its support.
    let behind = vec![
        Curve::Line(VERTEX, polar(60.0, 0.0)),
        Curve::Line(VERTEX, polar(60.0, 240.0)),
    ];
    let issues = structural::check(&sheet(1.0, behind, vec![(angle(0.0, 60.0, 20.0), "60")]));
    assert_eq!(found(&issues, code).len(), 1, "{issues:?}");
    // A quantity mark is not proved by one corner.
    let counted = structural::check(&sheet(
        1.0,
        corner(),
        vec![(angle(0.0, 60.0, 20.0), "2× 60")],
    ));
    assert_eq!(
        fidelity(&counted),
        [format!("{code} d:pmi:dimension.angular:f1,2/angular")]
    );
}

/// LINT-angular-profile_angle_requirement: a regular polygon's equal corners carry one counted
/// angle, proved by the closed loop of visible edges through the drawn corner, which must have
/// exactly that many corners, each at the stated angle.
#[test]
fn a_counted_angle_is_proved_by_its_closed_polygon() {
    let code = "angular_support_mismatch";
    // A regular hexagon with one corner at the vertex, its sides along 0° and 120° from it,
    // each side drawn in two collinear pieces as hidden-line removal may split it.
    let mut at = VERTEX;
    let mut hexagon = Vec::new();
    for k in 0..6 {
        let t = (60.0 * k as f64).to_radians();
        let next = [at[0] + 40.0 * t.cos(), at[1] + 40.0 * t.sin()];
        let middle = [(at[0] + next[0]) / 2.0, (at[1] + next[1]) / 2.0];
        hexagon.push(Curve::Line(at, middle));
        hexagon.push(Curve::Line(middle, next));
        at = next;
    }
    let check = |label: &str, edges: Vec<Curve>| {
        let issues = structural::check(&sheet(1.0, edges, vec![(angle(0.0, 120.0, 20.0), label)]));
        found(&issues, code).len()
    };
    assert_eq!(check("6× 120°", hexagon.clone()), 0);
    assert_eq!(
        check("5× 120°", hexagon.clone()),
        1,
        "the count must match the loop"
    );
    // An open chain proves nothing beyond its one corner.
    let mut open = hexagon.clone();
    open.truncate(8);
    assert_eq!(check("6× 120°", open), 1);
    // A branching corner is ambiguous.
    let mut branched = hexagon;
    branched.push(Curve::Line(polar(40.0, 0.0), polar(80.0, -30.0)));
    assert_eq!(check("6× 120°", branched), 1);
}

/// `2× θ` for two mirror-equal corners: the drawn corner and its mirror image across a vertical
/// or horizontal line must both stand on visible edges (a rounded corner's straight edges may
/// stop short of the theoretical vertex).
#[test]
fn a_two_times_angle_is_proved_by_its_mirror_corner() {
    let code = "angular_support_mismatch";
    let mirror = |p: P| [2.0 * (VERTEX[0] + 50.0) - p[0], p[1]];
    let mut edges = corner();
    // The mirror corner, its edges stopping 3 mm short of its vertex.
    edges.push(Curve::Line(
        mirror(polar(3.0, 0.0)),
        mirror(polar(60.0, 0.0)),
    ));
    edges.push(Curve::Line(
        mirror(polar(3.0, 60.0)),
        mirror(polar(60.0, 60.0)),
    ));
    let check = |edges: Vec<Curve>| {
        let issues =
            structural::check(&sheet(1.0, edges, vec![(angle(0.0, 60.0, 20.0), "2× 60°")]));
        found(&issues, code).len()
    };
    assert_eq!(check(edges.clone()), 0);
    assert_eq!(check(corner()), 1, "one corner proves no 2×");
    // A corner of another angle is no mirror.
    let mut other = corner();
    other.push(Curve::Line(mirror(VERTEX), mirror(polar(60.0, 0.0))));
    other.push(Curve::Line(mirror(VERTEX), mirror(polar(60.0, 75.0))));
    assert_eq!(check(other), 1);
}

#[test]
fn both_angular_supports_can_extend_through_their_common_corner() {
    for degrees in [60.0, 120.0] {
        let edges = vec![
            Curve::Line(VERTEX, polar(60.0, 0.0)),
            Curve::Line(VERTEX, polar(60.0, degrees)),
        ];
        let label = format!("{degrees}");
        let issues = structural::check(&sheet(
            1.0,
            edges,
            vec![(angle(180.0, 180.0 + degrees, 20.0), &label)],
        ));
        assert!(fidelity(&issues).is_empty(), "{issues:?}");
    }
}

/// A datum feature symbol below its surface at y 100: its triangle's base on the surface, its
/// shaft down *reach* mm from the triangle's apex to its 6 mm frame.
fn datum(reach: f64) -> Drawn {
    let apex = [120.0, 95.6];
    let near = [120.0, 95.6 - reach];
    let ink = [114.0, near[1] - 6.0, 126.0, 100.0];
    Drawn {
        datum_triangle: Some([[117.9, 100.], apex, [122.1, 100.]]),
        heads: Vec::new(),
        name: "pmi:datum.datum:f3:t=A".into(),
        kind: Kind::Gdt,
        ink,
        label: Some([117.0, near[1] - 6.0, 123.0, near[1]]),
        lines: vec![[apex, near]],
        elbow: Some(near),
        interior: false,
    }
}

#[test]
fn a_datum_symbol_sits_close_to_its_surface() {
    // From the base to the frame: 4.4 + reach; the limit is 24 mm.
    let close = structural::check(&sheet(1.0, Vec::new(), vec![(datum(19.0), "A")]));
    assert!(fidelity(&close).is_empty(), "{close:?}");
    let remote = structural::check(&sheet(1.0, Vec::new(), vec![(datum(30.0), "A")]));
    let [i] = found(&remote, "datum_leader_remote")[..] else {
        panic!("{remote:?}")
    };
    assert_eq!(i.severity, Severity::Warning);
    assert!(i.message.contains("34.4 mm"), "{}", i.message);
    // A feature control frame's leader is not a datum's.
    let mut frame = datum(30.0);
    frame.name = "pmi:geometric_tolerance.position:f3".into();
    let issues = structural::check(&sheet(1.0, Vec::new(), vec![(frame, "⌖ 0.1 A")]));
    assert!(fidelity(&issues).is_empty());
}

fn ctc01() -> Drawing {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let name = "nist_ctc_01_asme1_ap242-e1.stp";
    let gz = std::fs::read(
        root.join("tests/fixtures/pmi/step")
            .join(format!("{name}.gz")),
    )
    .unwrap();
    let mut bytes = Vec::new();
    flate2::read::GzDecoder::new(&gz[..])
        .read_to_end(&mut bytes)
        .unwrap();
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR")).join("lint_fidelity");
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join(name);
    std::fs::write(&path, bytes).unwrap();
    let (part, analysis) = model::analyse_native(&path).unwrap();
    // Fidelity compares the original full-ISO PMI drawing, independent of printer limits.
    let spec =
        serde_json::from_value(serde_json::json!({"sheet":{"preferred_max_size":"A0"}})).unwrap();
    let plan = model::plan(&analysis, &spec).unwrap();
    let font = draftwright::text::Font::new(
        std::fs::read(root.join("fonts/IBMPlexMono-Regular.ttf")).unwrap(),
    )
    .unwrap();
    model::render(&part, &analysis, &plan, &font).unwrap()
}

#[test]
fn the_ports_own_pmi_drawing_says_what_it_draws() {
    let drawing = ctc01();
    let issues = structural::check(&drawing);
    // Linear/angular ink agrees with its labels. C's remote route in the front view is gone:
    // #224 attaches C to its hole's `⌀35` callout in the plan, which sees the bore end-on
    // (`a_datum_symbol_sits_close_to_its_surface` keeps the remote-leader check covered).
    assert!(fidelity(&issues).is_empty(), "{issues:#?}");
    let angular = drawing
        .sheet
        .items
        .iter()
        .position(|d| d.name == "d:pmi:dimension.angular:f15,17/angular")
        .expect("the 60 ±0.5 angle is drawn");
    let linear = drawing
        .sheet
        .items
        .iter()
        .position(|d| {
            d.kind == Kind::Dimension
                && d.elbow.is_none()
                && d.lines.len() == 3
                && d.name.starts_with("d:")
        })
        .expect("a linear dimension is drawn");
    // Move one extension line, and the dimension line's end on it, 5 mm along the line.
    let mut moved = drawing.clone();
    let d = &mut moved.sheet.items[linear];
    let k = usize::from((d.lines[2][0][0] - d.lines[2][1][0]).abs() < 1e-6);
    let at = d.lines[1][0][k];
    for line in [1, 2] {
        for p in &mut d.lines[line] {
            if (p[k] - at).abs() < 1e-6 {
                p[k] += 5.0;
            }
        }
    }
    let issues = structural::check(&moved);
    assert_eq!(found(&issues, "label_vs_measured").len(), 1, "{issues:#?}");
    // Turn one of the angle's extension lines 5° about the vertex.
    let mut turned = drawing.clone();
    let d = &mut turned.sheet.items[angular];
    let [a, b] = [d.lines[0], d.lines[1]];
    let (u, v) = (
        [a[1][0] - a[0][0], a[1][1] - a[0][1]],
        [b[1][0] - b[0][0], b[1][1] - b[0][1]],
    );
    let den = u[0] * v[1] - u[1] * v[0];
    let t = ((b[0][0] - a[0][0]) * v[1] - (b[0][1] - a[0][1]) * v[0]) / den;
    let vertex = [a[0][0] + t * u[0], a[0][1] + t * u[1]];
    let (s, c) = 5f64.to_radians().sin_cos();
    for p in &mut d.lines[0] {
        let r = [p[0] - vertex[0], p[1] - vertex[1]];
        *p = [
            vertex[0] + c * r[0] - s * r[1],
            vertex[1] + s * r[0] + c * r[1],
        ];
    }
    let codes: Vec<String> = fidelity(&structural::check(&turned));
    for code in [
        "angular_label_vs_geometry",
        "angular_geometry_mismatch",
        "angular_support_mismatch",
    ] {
        assert!(
            codes.iter().any(|c| c.starts_with(code)),
            "{code}: {codes:?}"
        );
    }
}

/// Drawn geometry receives the same check whether automatic, authored, or pinned.
#[test]
fn datum_departure_is_square_before_any_bend_and_frame_interior_stays_strict() {
    let mut d = datum(10.);
    let check = |d: Drawn| structural::check(&sheet(1., Vec::new(), vec![(d, "A")]));
    assert!(found(&check(d.clone()), "datum_leader_not_normal").is_empty());
    d.lines[0][1][0] += 8.;
    assert_eq!(found(&check(d.clone()), "datum_leader_not_normal").len(), 1);
    let [apex, end] = d.lines[0];
    d.lines = vec![
        [apex, [apex[0], apex[1] - 2.4]],
        [[apex[0], apex[1] - 2.4], end],
    ];
    assert!(found(&check(d.clone()), "datum_leader_not_normal").is_empty());
    // A square departure does not excuse a later segment through the strict frame interior.
    let b = d.label.unwrap();
    d.lines
        .push([[b[0] - 2., b[1] + 3.], [b[2] + 2., b[1] + 3.]]);
    assert_eq!(found(&check(d), "leader_line_through_text").len(), 1);
    let border = datum(10.);
    assert!(found(&check(border), "leader_line_through_text").is_empty());
}
