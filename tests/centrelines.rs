//! ISO 128 furniture belongs to the planned view, independently of dimensional intent.
#[path = "common/corpus.rs"]
mod corpus;

#[path = "common/support.rs"]
#[macro_use]
mod support;

#[path = "common/cache.rs"]
mod cache;

use draftwright::{
    api,
    model::{self, Spec},
    text::Font,
};
use serde_json::Value;
use std::path::Path;

fn drawing(file: &Path) -> (model::Analysis, model::Plan, draftwright::output::Drawing) {
    drawing_with(file, &Spec::default())
}

fn drawing_with(
    file: &Path,
    spec: &Spec,
) -> (model::Analysis, model::Plan, draftwright::output::Drawing) {
    let (part, a) = cache::native(file);
    let p = model::plan_recovered(&a, spec).unwrap();
    let d = model::render(&part, &a, &p, &Font::new(api::MONO.to_vec()).unwrap()).unwrap();
    (a, p, d)
}

fn work(p: &model::Plan, kind: &str) -> Vec<Value> {
    serde_json::to_value(p).unwrap()["views"]
        .as_array()
        .unwrap()
        .iter()
        .flat_map(|v| v["line_work"].as_array().cloned().unwrap_or_default())
        .filter(|w| w["kind"] == kind)
        .collect()
}

#[test]
fn a_plate_marks_every_pattern_member_and_lint_reads_the_drawn_marks() {
    let (a, p, mut d) =
        drawing(&Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/plate.step"));
    assert!(work(&p, "cross").len() >= 4);
    assert!(d.svg().contains("stroke-dasharray=\"8 1.5 0.2 1.5\""));
    assert!(
        !draftwright::lint::lint(&a, &d)
            .iter()
            .any(|i| i.code == "feature_no_centermark")
    );
    d.sheet.centre_marks.clear();
    assert!(
        draftwright::lint::lint(&a, &d)
            .iter()
            .any(|i| i.code == "feature_no_centermark")
    );
}

#[test]
fn turned_axes_extend_beyond_the_profile_and_survive_omitted_dimensions() {
    let Some(dir) = corpus::dir() else { return };
    let (a, p, _) = drawing(&dir.join("gramel/GRM-05_depth_lock_bolt.step"));
    let axes = work(&p, "axis");
    assert!(!axes.is_empty());
    assert!(axes.iter().any(|w| w["global_axis"] == true));
    for v in &p.views {
        let frame = draftwright::haecceity::hlr::View::new(v.direction, v.up).unwrap();
        let corners: Vec<_> = (0..8)
            .map(|i| frame.map([0, 1, 2].map(|k| a.envelope[(i >> k) & 1][k])))
            .collect();
        for w in v.line_work.iter().filter(|w| w.global_axis) {
            let c = &w.curves[0];
            let (start, end) = (c.start(), c.end());
            let k = usize::from((end[1] - start[1]).abs() > (end[0] - start[0]).abs());
            let lo = corners.iter().map(|p| p[k]).fold(f64::INFINITY, f64::min);
            let hi = corners
                .iter()
                .map(|p| p[k])
                .fold(f64::NEG_INFINITY, f64::max);
            let half = (hi - lo) * v.scale / 2.;
            assert!(start[k].min(end[k]) <= v.at[k] - half - 4.99);
            assert!(start[k].max(end[k]) >= v.at[k] + half + 4.99);
        }
    }
    let mut s = Spec::default();
    for d in &p.dimensions {
        s.dimensions.entry(d.id.clone()).or_default().omit = true;
    }
    let omitted = model::plan_recovered(&a, &s).unwrap();
    assert_eq!(work(&p, "axis"), work(&omitted, "axis"));
}

#[test]
fn bolt_circle_furniture_is_radial_and_tangential() {
    let Some(dir) = corpus::dir() else { return };
    let (_, p, _) = drawing(&dir.join("nist/nist_ctc_05_asme1_rd.stp"));
    let circles = work(&p, "pitch_circle");
    assert!(!circles.is_empty());
    let radial = work(&p, "cross");
    assert!(radial.iter().any(|w| w["radial"] == true));
    for w in radial.iter().filter(|w| w["radial"] == true) {
        let c = &w["centre"];
        let circle = circles
            .iter()
            .find(|circle| {
                let q = &circle["curves"][0]["Conic"]["centre"];
                let r = circle["curves"][0]["Conic"]["radii"][0].as_f64().unwrap();
                let distance = (c[0].as_f64().unwrap() - q[0].as_f64().unwrap())
                    .hypot(c[1].as_f64().unwrap() - q[1].as_f64().unwrap());
                (distance - r).abs() < 0.05
            })
            .unwrap();
        let q = &circle["curves"][0]["Conic"]["centre"];
        let r = [0, 1].map(|k| c[k].as_f64().unwrap() - q[k].as_f64().unwrap());
        let vectors: Vec<_> = w["curves"]
            .as_array()
            .unwrap()
            .iter()
            .map(|c| {
                [0, 1]
                    .map(|k| c["Line"][1][k].as_f64().unwrap() - c["Line"][0][k].as_f64().unwrap())
            })
            .collect();
        assert!((vectors[0][0] * r[1] - vectors[0][1] * r[0]).abs() < 1e-6);
        assert!((vectors[1][0] * r[0] + vectors[1][1] * r[1]).abs() < 1e-6);
    }
}

/// `bolt_square.step`: a 40 × 40 × 6 plate with a central Ø10 hole and four Ø4 holes on a Ø20
/// circle at 0°, 90°, 180° and 270°, made in build123d (`Box(40, 40, 6)`, `Hole(5)`,
/// `PolarLocations(10, 4)`, `Hole(2)`). quiddity reports the four as a rectangle turned 45°:
/// they are called out as a bolt circle located by its centre, never from the plate's faces
/// one by one (#223).
#[test]
fn a_square_of_four_on_the_axes_is_a_bolt_circle() {
    let (a, p, d) = drawing(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/bolt_square.step"),
    );
    let pattern = &a.recognition.as_ref().unwrap().patterns[0];
    assert_eq!(pattern.arrangement, "bolt_circle");
    assert_eq!(pattern.parameters["diameter"].as_f64(), Some(20.0));
    let callout = p
        .dimensions
        .iter()
        .find(|d| d.id == format!("d:{}/bore.diameter", pattern.id))
        .unwrap();
    assert!(
        callout.label.contains("EQ SP ON ø20 BC"),
        "{}",
        callout.label
    );
    assert!(!p.dimensions.iter().any(|d| {
        d.measurements
            .iter()
            .any(|m| pattern.members.contains(&m.feature) && m.parameter.starts_with("location."))
    }));
    assert_eq!(work(&p, "pitch_circle").len(), 1);
    assert!(
        !draftwright::lint::lint(&a, &d)
            .iter()
            .any(|i| i.code.starts_with("hole_pattern_requirement"))
    );
}

/// `rect_near_circle.step`: dw-examples' gantry plate's twelve Ø5.1 holes on a 65.5 × 65.5 × 3
/// plate, made in build123d (`Box(65.5, 65.5, 3)`, `Hole(2.55)`): four on a 19.85 × 20 rectangle
/// at x = -19.85 and 0, y = ±10, three more on y = 0 and a column of five at x = 19.85. quiddity
/// reports the four as a bolt circle (89.6° and 90.4° apart, within its 4%). They are not one:
/// no "EQ SP … BC" callout, no pitch circle, and every hole is located (#223).
#[test]
fn a_rectangle_near_a_circle_is_not_a_bolt_circle() {
    let (a, p, d) = drawing(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/rect_near_circle.step"),
    );
    let patterns = &a.recognition.as_ref().unwrap().patterns;
    assert!(
        patterns.iter().all(|p| p.arrangement != "bolt_circle"),
        "{patterns:?}"
    );
    assert!(
        !p.dimensions.iter().any(|d| d.label.contains("BC")),
        "a bolt-circle callout"
    );
    assert!(work(&p, "pitch_circle").is_empty());
    let holes = a.features.iter().filter(|f| f.kind == "hole").count();
    let called: usize = p
        .dimensions
        .iter()
        .filter(|d| {
            d.measurements
                .iter()
                .any(|m| m.parameter == "bore.diameter")
        })
        .filter_map(|d| d.label.split('×').next()?.trim().parse::<usize>().ok())
        .sum();
    assert_eq!(called, holes, "every hole is counted in a callout");
    assert!(
        !draftwright::lint::lint(&a, &d)
            .iter()
            .any(|i| i.code.starts_with("hole_requirement_missing")
                || i.code.starts_with("hole_pattern_requirement"))
    );
}

/// A grid located by its centre marks that centre, where `plate.step` has no hole; where a hole
/// table locates the holes instead, no mark is drawn (#223).
#[test]
fn a_grid_located_by_its_centre_marks_the_centre() {
    let file = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/plate.step");
    let (_, p, _) = drawing(&file);
    let crosses = work(&p, "cross");
    let at = |w: &Value| [0, 1].map(|k| w["centre"][k].as_f64().unwrap());
    let mark = crosses
        .iter()
        .find(|w| w["id"] == "ink:v:plan:f:hole_pattern:1")
        .unwrap();
    let members: Vec<_> = crosses
        .iter()
        .filter(|w| w["id"].as_str().unwrap().starts_with("ink:v:plan:f:hole:"))
        .map(at)
        .collect();
    assert_eq!(members.len(), 4);
    for k in 0..2 {
        let mean = members.iter().map(|c| c[k]).sum::<f64>() / 4.0;
        assert!((at(mark)[k] - mean).abs() < 1e-6);
    }
    // FTC-10's grids are located by its hole table, not from their centres.
    let Some(dir) = corpus::dir() else { return };
    let (_, p, _) = drawing(&dir.join("nist/nist_ftc_10_asme1_rb.stp"));
    assert!(!p.hole_tables.is_empty());
    assert!(
        !work(&p, "cross")
            .iter()
            .any(|w| w["id"].as_str().unwrap().contains("hole_pattern"))
    );
}

#[test]
fn section_cutting_line_is_planned_and_labels_keep_clear() {
    let Some(dir) = corpus::dir() else { return };
    let (_, p, drawn) = drawing(&dir.join("gramel/string_post.step"));
    let cutting = work(&p, "cutting_plane");
    assert_eq!(cutting.len(), 1);
    let ends = cutting[0]["curves"][0]["Line"].as_array().unwrap();
    let (x0, x1, y) = (
        ends[0][0].as_f64().unwrap(),
        ends[1][0].as_f64().unwrap(),
        ends[0][1].as_f64().unwrap(),
    );
    for d in &p.dimensions {
        if d.view == "v:plan"
            && let Some(g) = &d.placed
        {
            let b = g.label_box;
            for letter in drawn.sheet.items.iter().filter(|i| {
                i.kind == draftwright::output::Kind::CuttingPlane
                    || (i.kind == draftwright::output::Kind::Caption
                        && matches!(i.name.as_str(), "section_a_left" | "section_a_right"))
            }) {
                let l = letter.label.unwrap_or(letter.ink);
                if letter.kind == draftwright::output::Kind::CuttingPlane && d.kind == "callout" {
                    for [a, z] in &g.lines {
                        // The section glyph's full ink box is a hard reservation, including its wing.
                        let mut lo: f64 = 0.;
                        let mut hi: f64 = 1.;
                        for k in 0..2 {
                            let delta = z[k] - a[k];
                            if delta.abs() < 1e-9 {
                                if a[k] <= l[k] || a[k] >= l[k + 2] {
                                    hi = -1.;
                                    break;
                                }
                            } else {
                                let x = (l[k] - a[k]) / delta;
                                let y = (l[k + 2] - a[k]) / delta;
                                lo = lo.max(x.min(y));
                                hi = hi.min(x.max(y));
                            }
                        }
                        assert!(hi <= lo, "{} leader touches {}", d.id, letter.name);
                    }
                }
                assert!(
                    b[2] < l[0] || l[2] < b[0] || b[3] < l[1] || l[3] < b[1],
                    "{} label touches {}",
                    d.id,
                    letter.name
                );
            }
            assert!(
                b[1] > y || b[3] < y || b[2] < x0 || b[0] > x1,
                "{} label lies on A-A",
                d.id
            );
            if d.kind == "callout" {
                for [a, b] in &g.lines {
                    assert!(
                        (a[1] - y).abs() > 0.1
                            || (b[1] - y).abs() > 0.1
                            || a[0].max(b[0]) < x0
                            || a[0].min(b[0]) > x1,
                        "{} leader runs along A-A",
                        d.id
                    );
                }
            }
        }
    }
    assert!(p.views.iter().filter(|v| v.cut.is_some()).any(|v| {
        serde_json::to_value(v).unwrap()["line_work"]
            .as_array()
            .is_some_and(|w| w.iter().any(|w| w["kind"] == "axis"))
    }));
}

#[test]
fn fixed_ink_review_catches_the_captured_section_defects() {
    let Some(dir) = corpus::dir() else { return };
    let cases: Vec<Value> =
        serde_json::from_str(include_str!("fixtures/g6_section_contacts.json")).unwrap();
    for case in cases {
        let (_, _, mut drawn) = drawing(&dir.join(case["step"].as_str().unwrap()));
        let code = case["code"].as_str().unwrap();
        assert!(
            !draftwright::lint::structural::check(&drawn)
                .iter()
                .any(|i| i.code == code),
            "{}: fixed sheet still has {code}: {:?}",
            case["step"],
            draftwright::lint::structural::check(&drawn)
                .iter()
                .filter(|i| i.code == code)
                .collect::<Vec<_>>()
        );
        // The captured ink is replayed on the layout it was captured on: its sheet, and the
        // dimension set before hole schedules (Stage H moved both on these parts).
        let mut captured_on = Spec {
            hole_table: draftwright::hole_table::Setting::Never,
            ..Spec::default()
        };
        captured_on.sheet.size = case["sheet"]["size"].as_str().map(String::from);
        captured_on.sheet.scale = case["sheet"]["scale"].as_str().map(String::from);
        let (_, captured_plan, captured_drawing) =
            drawing_with(&dir.join(case["step"].as_str().unwrap()), &captured_on);
        drawn = captured_drawing;
        for captured in case["dimensions"].as_array().unwrap() {
            let mut g: draftwright::dims::Placed =
                serde_json::from_value(captured["placed"].clone()).unwrap();
            let frame = &captured["frame"];
            let v = captured_plan
                .views
                .iter()
                .find(|v| v.id == frame["id"].as_str().unwrap())
                .unwrap();
            assert_eq!(v.scale, frame["scale"].as_f64().unwrap());
            let delta = [0, 1].map(|k| v.at[k] - frame["at"][k].as_f64().unwrap());
            for point in g.lines.iter_mut().chain(&mut g.arrows).flatten() {
                for k in 0..2 {
                    point[k] += delta[k];
                }
            }
            for (k, shift) in delta.iter().enumerate() {
                g.label_at[k] += shift;
                g.label_box[k] += shift;
                g.label_box[k + 2] += shift;
            }
            let item = drawn
                .sheet
                .items
                .iter_mut()
                .find(|i| i.name == captured["id"].as_str().unwrap())
                .unwrap();
            item.label = Some(g.label_box);
            item.lines = g.lines;
            item.elbow = Some(item.lines[0][1]);
        }
        let found: Vec<_> = draftwright::lint::structural::check(&drawn)
            .into_iter()
            .filter(|i| i.code == code)
            .collect();
        assert!(
            !found.is_empty(),
            "{}: captured {} was missed",
            case["source"],
            code
        );
        if code == "leader_centerline_overlap" {
            assert_eq!(
                found
                    .iter()
                    .filter(|i| i.related == ["section_line"])
                    .count(),
                2,
                "both original string_post leaders must be counted"
            );
        }
    }
}

#[test]
fn an_actual_section_hatch_is_not_a_label_on_its_axis() {
    let Some(dir) = corpus::dir() else { return };
    let (_, _, mut drawn) = drawing(&dir.join("gramel/string_post.step"));
    let contacts = |d: &draftwright::output::Drawing| {
        draftwright::lint::structural::check(d)
            .into_iter()
            .filter(|i| {
                i.code == "label_centerline_overlap"
                    && i.annotation.as_deref() == Some("section_hatch")
            })
            .count()
    };
    assert_eq!(contacts(&drawn), 0);
    let hatch = drawn
        .sheet
        .items
        .iter_mut()
        .find(|i| i.name == "section_hatch")
        .unwrap();
    // Emulate Python's fallback on the same captured geometry, without changing any ink.
    hatch.label = Some(hatch.ink);
    assert!(
        contacts(&drawn) > 0,
        "the whole-ink fallback should flag this hatch as text"
    );
}

part_cases!(callouts_do_not_borrow_hole_centre_strokes;
    nist_ctc_02_asme1_rc => "nist/nist_ctc_02_asme1_rc.stp",
    nist_ctc_04_asme1_rd => "nist/nist_ctc_04_asme1_rd.stp",
    nist_ctc_05_asme1_rd => "nist/nist_ctc_05_asme1_rd.stp",
    nist_ftc_06_asme1_rd => "nist/nist_ftc_06_asme1_rd.stp",
    nist_ftc_07_asme1_rd => "nist/nist_ftc_07_asme1_rd.stp",
    nist_ftc_10_asme1_rb => "nist/nist_ftc_10_asme1_rb.stp",
);
fn callouts_do_not_borrow_hole_centre_strokes(file: &str) {
    let Some(dir) = corpus::dir() else { return };

    // Keep each historical paper while testing routing quality on complete ink.
    let size = match file {
        "nist/nist_ctc_02_asme1_rc.stp" => "A0",
        "nist/nist_ctc_04_asme1_rd.stp"
        | "nist/nist_ftc_06_asme1_rd.stp"
        | "nist/nist_ftc_10_asme1_rb.stp" => "A1",
        "nist/nist_ctc_05_asme1_rd.stp" => "A2",
        "nist/nist_ftc_07_asme1_rd.stp" => "A3",
        _ => unreachable!(),
    };
    let mut spec = Spec::default();
    spec.sheet.size = Some(size.into());
    let (_, p, drawn) = drawing_with(&dir.join(file), &spec);
    let contacts: Vec<_> = draftwright::lint::structural::check(&drawn)
        .into_iter()
        .filter(|i| {
            matches!(
                i.code.as_str(),
                "leader_centerline_overlap"
                    | "label_centerline_overlap"
                    | "label_section_furniture_overlap"
            )
        })
        .collect();
    assert!(contacts.is_empty(), "{file}: {contacts:?}");
    if file.ends_with("nist_ftc_10_asme1_rb.stp") {
        let d = p
            .dimensions
            .iter()
            .find(|d| d.id == "d:f:hole:17/bore.diameter")
            .unwrap();
        let v = p.views.iter().find(|v| v.id == d.view).unwrap();
        let centre = v
            .line_work
            .iter()
            .find(|w| w.id == "ink:v:side:f:hole:17")
            .unwrap()
            .centre
            .unwrap();
        let shelf_end = d.placed.as_ref().unwrap().lines[1][1];
        for stroke in v.line_work.iter().flat_map(|w| &w.curves) {
            let (a, b) = (stroke.start(), stroke.end());
            let u = [b[0] - a[0], b[1] - a[1]];
            let length2 = u[0] * u[0] + u[1] * u[1];
            if length2 < 1e-12 {
                continue;
            }
            let t = (((shelf_end[0] - a[0]) * u[0] + (shelf_end[1] - a[1]) * u[1]) / length2)
                .clamp(0., 1.);
            let gap = (shelf_end[0] - a[0] - t * u[0]).hypot(shelf_end[1] - a[1] - t * u[1]);
            assert!(gap >= 1. - 1e-6, "{file}: shelf endpoint gap {gap}");
        }
        let [tip, elbow] = d.placed.as_ref().unwrap().lines[0];
        let radius = d.value * v.scale / 2.;
        let u = [tip[0] - centre[0], tip[1] - centre[1]];
        assert!(
            (u[0].hypot(u[1]) - radius).abs() < 1e-6,
            "the revised tip must stay on the opening rim"
        );
        assert!(
            u[0] * (elbow[0] - tip[0]) + u[1] * (elbow[1] - tip[1]) > 0.,
            "the leader must leave the opening outward"
        );
    }
}

/// The longest stretch of *line* lying within 1 mm of the stroke *c*–*e* at under ten degrees
/// to it, sampled every 0.05 mm: a leader there reads as part of the centre line.
fn beside_run(line: [[f64; 2]; 2], c: [f64; 2], e: [f64; 2]) -> f64 {
    let [a, b] = line;
    let (u, v) = ([b[0] - a[0], b[1] - a[1]], [e[0] - c[0], e[1] - c[1]]);
    let (lu, lv) = (u[0].hypot(u[1]), v[0].hypot(v[1]));
    if lu < 1e-9
        || lv < 1e-9
        || (u[0] * v[1] - u[1] * v[0]).abs() > 10f64.to_radians().sin() * lu * lv
    {
        return 0.;
    }
    let distance = |p: [f64; 2]| {
        let t = (((p[0] - c[0]) * v[0] + (p[1] - c[1]) * v[1]) / (lv * lv)).clamp(0., 1.);
        (p[0] - c[0] - t * v[0]).hypot(p[1] - c[1] - t * v[1])
    };
    let n = (lu / 0.05).ceil() as usize;
    let (mut run, mut best) = (0f64, 0f64);
    for i in 0..=n {
        let t = i as f64 / n as f64;
        if distance([a[0] + u[0] * t, a[1] + u[1] * t]) < 1. {
            run += lu / n as f64;
            best = best.max(run);
        } else {
            run = 0.;
        }
    }
    best
}

// cgb207's R5 ran 36.5 mm beside hole 1's axis (0.775 mm off, then tilted to converge on it)
// and string_post's ⌀1.6 8.9 mm along the A-A cutting line.
// No leader shaft on these sheets may run beside a centre stroke, at any small angle, other
// than its own hole's centre mark.
part_cases!(leaders_do_not_run_beside_centre_lines;
    cgb207 => "cadgenbench_inputs/cgb207.step",
    cgb203 => "cadgenbench_inputs/cgb203.step",
    string_post => "gramel/string_post.step",
);
fn leaders_do_not_run_beside_centre_lines(file: &str) {
    let Some(dir) = corpus::dir() else { return };

    let (_, p, _) = drawing(&dir.join(file));
    let mut checked = 0;
    for d in p.dimensions.iter() {
        let Some(placed) = d.placed.as_ref().filter(|p| !p.arrows.is_empty()) else {
            continue;
        };
        let v = p.views.iter().find(|v| v.id == d.view).unwrap();
        let shaft = placed.lines[0];
        let [a, b] = shaft;
        let n = (b[0] - a[0]).hypot(b[1] - a[1]);
        // A radial leader leaving its own hole along that hole's centre mark is attached,
        // not running along another line: the cross whose centre its line passes through.
        let radial = |c: [f64; 2]| {
            ((c[0] - a[0]) * (b[1] - a[1]) - (c[1] - a[1]) * (b[0] - a[0])).abs() / n < 1e-3
        };
        for stroke in v
            .line_work
            .iter()
            .filter(|w| !(w.kind == "cross" && w.centre.is_some_and(radial)))
            .flat_map(|w| &w.curves)
        {
            let run = beside_run(shaft, stroke.start(), stroke.end());
            assert!(
                run <= 0.5,
                "{file}: {} runs {run:.2} mm beside a centre stroke",
                d.id
            );
        }
        checked += 1;
    }
    assert!(checked > 0, "{file}");
}

/// A label that must stay across the end of an axis shortens that axis's overrun (ISO 128-24)
/// rather than being parted from its shelf by the stroke: FTC-10's prescribed ⌀5.5 ↧ 35
/// stays joined to its shelf, and hole 13's axis stops short of it, still past the hole.
#[test]
fn a_label_across_an_axis_end_trims_the_overrun() {
    let Some(dir) = corpus::dir() else { return };
    let file = dir.join("nist/nist_ftc_10_asme1_rb.stp");
    let sheet: Spec = serde_json::from_value(serde_json::json!({
        "sheet": {"size": "A1", "scale": "2:1"}
    }))
    .unwrap();
    let (analysis, base, _) = drawing_with(&file, &sheet);
    // Longer faithful labels can select a different automatic station. Prescribe
    // the scenario this test proves: this label straddles the far axis overrun.
    // Its measured tip, shelf gap, axis column and physical span stay independently checked.
    let natural = base
        .dimensions
        .iter()
        .find(|d| d.id == "d:f:hole:f174/bore.diameter")
        .unwrap()
        .placed
        .as_ref()
        .unwrap();
    let side = base.views.iter().find(|v| v.id == "v:side").unwrap();
    let axis = side
        .line_work
        .iter()
        .find(|w| w.id == "ink:v:side:f:hole:f130")
        .unwrap();
    let a = axis.curves[0].start();
    let b = axis.curves[0].end();
    let elbow = [a[0] - 5., a[1].max(b[1]) + 0.5];
    let tip = natural.lines[0][0];
    let spec: Spec = serde_json::from_value(serde_json::json!({
        "sheet": {"size": base.sheet.size, "scale": base.sheet.scale},
        "dimensions": {"d:f:hole:f174/bore.diameter": {"place": {
            "view": "side", "pinned": true, "offset": [elbow[0] - tip[0], elbow[1] - tip[1]]
        }}}
    }))
    .unwrap();
    let p = model::plan_recovered(&analysis, &spec).unwrap();
    let d = p
        .dimensions
        .iter()
        .find(|d| d.id == "d:f:hole:f174/bore.diameter")
        .unwrap();
    let placed = d.placed.as_ref().unwrap();
    let [elbow, end] = placed.lines[1];
    let label = placed.label_box;
    // The text follows its own shelf, a millimetre on, with nothing between them.
    let gap = if end[0] > elbow[0] {
        label[0] - end[0]
    } else {
        end[0] - label[2]
    };
    assert!((gap - 1.).abs() < 1e-6, "text gap {gap}");
    let v = p.views.iter().find(|v| v.id == d.view).unwrap();
    let axis = v
        .line_work
        .iter()
        .find(|w| w.id == "ink:v:side:f:hole:f130")
        .unwrap();
    let (a, b) = (axis.curves[0].start(), axis.curves[0].end());
    assert!((a[0] - b[0]).abs() < 1e-9, "a vertical axis");
    let (low, high) = (a[1].min(b[1]), a[1].max(b[1]));
    let holes = draftwright::bridge::holes::read(&analysis);
    let h = holes
        .iter()
        .find_map(|(f, h)| (f.id == "f:hole:f130").then_some(h.as_ref().unwrap()))
        .unwrap();
    let centre = [0, 1, 2].map(|k| (analysis.envelope[0][k] + analysis.envelope[1][k]) / 2.0);
    let projected =
        |q: [f64; 3]| v.at[1] + v.scale * (0..3).map(|k| v.up[k] * (q[k] - centre[k])).sum::<f64>();
    let ends = [
        projected(h.at),
        projected([0, 1, 2].map(|k| {
            h.at[k]
                + h.direction[k]
                    * (h.depth.unwrap()
                        + h.counterbore.and_then(|r| r.depth).unwrap_or(0.0)
                        + h.spotface.and_then(|r| r.depth).unwrap_or(0.0))
        })),
    ];
    let physical_low = ends[0].min(ends[1]);
    let physical_high = ends[0].max(ends[1]);
    // The opening-to-bottom span is 65 mm with 3 mm overrun each end (round 3: y 245..316).
    assert!(
        (low - (physical_low - 3.0)).abs() < 1e-6,
        "far end keeps its overrun: {low}"
    );
    assert!(
        high < label[1] - 0.5 + 1e-6,
        "axis end {high} below the label {}",
        label[1]
    );
    assert!(
        high >= physical_high + 1. - 1e-6,
        "at least 1 mm overrun stays: {high}"
    );
    assert!(
        a[0] > label[0] && a[0] < label[2],
        "the label still stands over the axis column"
    );
}

#[test]
fn linework_overrides_leave_sections_and_cutting_plane_rules_intact() {
    let Some(dir) = corpus::dir() else { return };
    let (part, a) = cache::native(&dir.join("gramel/string_post.step"));
    let s: Spec = serde_json::from_value(serde_json::json!({
        "sheet":{"hidden":false,"centre_marks":false,"centre_lines":false},
        "views":{"section_aa":{"hidden":true,"thread_depiction":true,"knurl_depiction":true}}
    }))
    .unwrap();
    let p = model::plan_recovered(&a, &s).unwrap();
    let section = p.views.iter().find(|v| v.cut.is_some()).unwrap();
    let g = model::render_geometry(&part, &a, &p, &mut model::Projections::default()).unwrap();
    assert!(
        g.views
            .iter()
            .find(|(id, _)| id == &section.id)
            .unwrap()
            .1
            .hidden
            .is_empty()
    );
    assert!(
        draftwright::cosmetic::ink(&a, &draftwright::pmi_lowering::manufacturing(&a), section)
            .is_empty()
    );
    assert_eq!(work(&p, "cutting_plane").len(), 1);
    assert!(work(&p, "cross").is_empty());
    assert!(work(&p, "axis").is_empty());
}

/// `linkrods.step.gz` (OCCT sample data): a ø1.5 boss's round face is the part's left and front
/// box sides. Its ⌀0.440 hole is located from the ⌀1.034 hole's axis on that boss (3.875 and
/// 0.125), not 4.625 and 0.625 from the boss's tangents, and the ⌀1.034 hole is located by
/// those dimensions' witnesses on its centre; `origin/<axis>: min` keeps the box sides (#223).
#[test]
fn a_hole_beside_a_round_boss_is_located_from_its_axis() {
    use std::io::Read;
    let mut bytes = Vec::new();
    flate2::read::GzDecoder::new(
        std::fs::File::open(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/linkrods.step.gz"),
        )
        .unwrap(),
    )
    .read_to_end(&mut bytes)
    .unwrap();
    let (part, a) = cache::analysed("linkrods.step", &bytes, true, || {
        model::analyse_native_step(Path::new("linkrods.step"), &bytes).unwrap()
    });
    let font = Font::new(api::MONO.to_vec()).unwrap();
    let at = |id: &str| {
        let f = a.features.iter().find(|f| f.id == id).unwrap();
        draftwright::bridge::holes::Hole::read(f, &draftwright::bridge::holes::frame(&a))
            .unwrap()
            .at
    };
    let (origin, beside) = (at("f:hole:1"), at("f:hole:2"));
    let locations = |p: &model::Plan| -> Vec<(String, f64, Vec<String>)> {
        p.dimensions
            .iter()
            .filter(|d| d.id.contains("/location.length."))
            .map(|d| {
                let claims = d
                    .measurements
                    .iter()
                    .map(|m| format!("{} {}", m.feature, m.parameter))
                    .collect();
                (d.id.clone(), d.value, claims)
            })
            .collect()
    };

    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    let found = locations(&p);
    assert_eq!(found.len(), 2, "{found:?}");
    for (k, axis) in ["x", "y"].iter().enumerate() {
        let (_, value, claims) = found
            .iter()
            .find(|(id, ..)| *id == format!("d:f:hole:2/location.length.{axis}"))
            .unwrap();
        assert!(
            (value - (beside[k] - origin[k]).abs()).abs() < 1e-6,
            "{axis}: {value}"
        );
        assert!(claims.contains(&format!("f:hole:1 location.length.{axis}")));
    }
    let d = model::render(&part, &a, &p, &font).unwrap();
    assert!(
        !draftwright::lint::lint(&a, &d)
            .iter()
            .any(|i| i.code == "feature_not_located"),
    );

    // A schedule states coordinates from the box corner, not the distances from the axis.
    let spec: Spec = serde_json::from_value(serde_json::json!({"hole_table": "always"})).unwrap();
    let p = model::plan_recovered(&a, &spec).unwrap();
    let rows: Vec<_> = p.hole_tables.iter().flat_map(|t| &t.rows).collect();
    assert!(!rows.is_empty());
    for r in &rows {
        let hole = if r.feature == "f:hole:1" {
            origin
        } else {
            beside
        };
        for (k, axis) in ["x", "y"].iter().enumerate() {
            let m = r
                .measurements
                .iter()
                .find(|m| m.parameter == format!("location.length.{axis}"))
                .unwrap_or_else(|| panic!("{r:?}"));
            let corner = hole[k] - a.envelope[0][k];
            assert!(
                (m.value - corner).abs() < 1e-6,
                "{} {axis}: {}",
                r.feature,
                m.value
            );
            // The cells are the tag, the callout, then `X` and `Y`.
            let cell: f64 = r.cells[2 + k].parse().unwrap();
            assert!((cell - corner).abs() < 1e-3, "{} {axis}: {r:?}", r.feature);
        }
    }

    // The Spec's origin keeps the box sides.
    let spec: Spec =
        serde_json::from_value(serde_json::json!({"origin": {"x": "min", "y": "min"}})).unwrap();
    let p = model::plan_recovered(&a, &spec).unwrap();
    let found = locations(&p);
    assert_eq!(found.len(), 4, "{found:?}");
    for (id, value, _) in &found {
        let k = if id.ends_with('x') { 0 } else { 1 };
        let hole = if id.starts_with("d:f:hole:1/") {
            origin
        } else {
            beside
        };
        assert!(
            (value - (hole[k] - a.envelope[0][k])).abs() < 1e-6,
            "{id}: {value}"
        );
    }
}

/// A hole beside planar box sides is still located from them: `plate.step`'s sides are faces
/// (#223).
#[test]
fn holes_beside_planar_sides_are_located_from_the_box() {
    let (_, p, _) =
        drawing(&Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/plate.step"));
    let located: Vec<_> = p
        .dimensions
        .iter()
        .filter(|d| d.id.contains("/location.length."))
        .collect();
    assert!(!located.is_empty());
    for d in located {
        assert!(
            !d.basis.contains("from the axis of"),
            "{}: {}",
            d.id,
            d.basis
        );
    }
}

/// `bearing-608zz.step.gz` (dw-examples): the ⌀8 bore lies on the axis of the ø22 round face,
/// which is the end view's whole outline. Its centre lines locate it; a location from the ø22's
/// tangent only repeats half the diameter, so none is drawn (#104). The callout says why, and
/// lint still finds the bore located and marked.
#[test]
fn a_bore_concentric_with_a_round_outline_is_not_located_from_its_tangent() {
    let (a, p, d) = drawing(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/bearing-608zz.step.gz"),
    );
    let located: Vec<_> = p
        .dimensions
        .iter()
        .filter(|d| d.id.contains("/location.length."))
        .map(|d| (&d.id, &d.label))
        .collect();
    assert!(located.is_empty(), "{located:?}");
    let bore = p
        .dimensions
        .iter()
        .find(|d| d.id == "d:f:hole:f10/bore.diameter")
        .unwrap();
    assert!(
        bore.basis
            .contains("on the axis of the part's round outline"),
        "{}",
        bore.basis
    );
    assert!(!work(&p, "cross").is_empty());
    let found = draftwright::lint::lint(&a, &d);
    assert!(
        !found
            .iter()
            .any(|i| i.code == "feature_not_located" || i.code == "feature_no_centermark"),
        "{:?}",
        found.iter().map(|i| &i.code).collect::<Vec<_>>()
    );

    // The Spec's origin keeps the box sides.
    let spec: Spec =
        serde_json::from_value(serde_json::json!({"origin": {"x": "min", "y": "min", "z": "min"}}))
            .unwrap();
    let p = model::plan_recovered(&a, &spec).unwrap();
    // v0.2.3 (#123) adds two coaxial recesses: their coincident locations share
    // one dimension per axis (ARCH0009-6), whose first ID is now a face groove.
    // The explicit origin must still put every bore/recess coordinate on paper.
    for feature in ["f:hole:f10", "f:face_groove:f2", "f:face_groove:f9"] {
        for axis in ["x", "y"] {
            let id = format!("d:{feature}/location.length.{axis}");
            let owner = p.dimensions.iter().find(|d| d.id == id).or_else(|| {
                let alias = p.omitted.iter().find(|d| d.id == id)?;
                assert_eq!(alias.label.parse::<f64>().unwrap(), 11.0);
                let draftwright::items::State::Covered { by } = &alias.state else {
                    panic!("{id}: the explicit location must be shown or conveyed");
                };
                p.dimensions.iter().find(|d| &d.id == by)
            });
            let owner = owner.expect("the coordinate resolves to a live dimension");
            assert!(
                owner.placed.is_some()
                    && owner
                        .measurements
                        .iter()
                        .any(|m| m.parameter == format!("location.length.{axis}")
                            && (m.value - 11.0).abs() < 1e-6),
                "{feature}: its {axis} coordinate from the explicit box origin must be drawn"
            );
        }
    }
}

/// quiddity-rust#123: the full OD boss is now a body. Shield recesses are visible
/// end-on, so they alone need no automatic cut, but the author can cut the profile.
#[test]
fn the_608zz_states_both_shield_recesses_and_accepts_a_forced_half_section() {
    let file =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/bearing-608zz.step.gz");
    let (a, p, _) = drawing(&file);
    assert_eq!(
        a.features
            .iter()
            .filter(|f| f.kind == "turned_body")
            .count(),
        1
    );
    assert_eq!(
        a.features
            .iter()
            .filter(|f| f.kind == "face_groove")
            .count(),
        2
    );
    assert!(p.views.iter().all(|v| v.half_section.is_none()));
    assert_eq!(
        p.dimensions
            .iter()
            .filter(|d| d.placed.is_some()
                && d.measurements.iter().any(|m| m.parameter == "od.diameter"))
            .count(),
        1
    );
    for f in a.features.iter().filter(|f| f.kind == "face_groove") {
        for parameter in [
            "annular_groove_outer.diameter",
            "annular_groove_inner.diameter",
            "annular_groove_depth.length",
            "face_groove_outer_wall_angle.angle",
            "face_groove_inner_wall_angle.angle",
        ] {
            assert_eq!(
                p.dimensions
                    .iter()
                    .filter(|d| d.placed.is_some()
                        && d.measurements
                            .iter()
                            .any(|m| m.feature == f.id && m.parameter == parameter))
                    .count(),
                1,
                "{}: {parameter}",
                f.id
            );
        }
    }
    assert!(!p.problems.iter().any(|p| matches!(
        p.code.as_str(),
        "shape_not_recognised" | "feature_not_dimensioned"
    )));
    let spec = serde_json::from_value(serde_json::json!({"views":{"front":{"half_section":true}}}))
        .unwrap();
    let (_, forced, d) = drawing_with(&file, &spec);
    assert!(
        forced
            .views
            .iter()
            .find(|v| v.id == "v:front")
            .unwrap()
            .half_section
            .is_some()
    );
    assert!(
        d.sheet
            .items
            .iter()
            .any(|i| i.name == "v:front/half_section_hatch")
    );
    assert!(
        !forced
            .problems
            .iter()
            .any(|p| p.code == "section_not_shown")
    );
}

/// `bearing-lm10uu.step.gz` (dw-examples): every circle of the end view shares the bore's centre
/// lines, which run on past its rim to the ø19's. A leader to the ⌀10 bore leaves at an angle,
/// not down a centre line beyond the bore's own overrun, which reads as that line (#104).
#[test]
fn a_leader_to_a_coaxial_bore_leaves_its_centre_lines() {
    let (_, p, _) = drawing(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/bearing-lm10uu.step.gz"),
    );
    assert!(
        p.dimensions
            .iter()
            .any(|d| d.id == "d:f:hole:1/bore.diameter" && d.placed.is_some())
    );
    let along: Vec<_> = p
        .problems
        .iter()
        .filter(|q| q.code == "leader_along_centreline")
        .map(|q| &q.message)
        .collect();
    assert!(along.is_empty(), "{along:?}");
}

/// `lm8uu.step.gz` (dw-examples): the end view's ⌀12.4 bore shares its centre lines with the ø15
/// and groove circles. Its leader kept 1.5–2 mm beside the vertical one for 9.5 mm, which review
/// reads as that line, while the router's beside test looked only 1 mm off it (#104).
#[test]
fn a_leader_beside_a_coaxial_bores_centre_line_moves_off_it() {
    let (_, p, _) =
        drawing(&Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/lm8uu.step.gz"));
    assert!(
        p.dimensions
            .iter()
            .any(|d| d.id == "d:f:hole:1/bore.diameter" && d.placed.is_some())
    );
    let along: Vec<_> = p
        .problems
        .iter()
        .filter(|q| q.code == "leader_along_centreline")
        .map(|q| &q.message)
        .collect();
    assert!(along.is_empty(), "{along:?}");
}

/// `bearing-lm10uu.step.gz` (dw-examples): a part turned whole whose ⌀10 bore steps inside (a
/// ⌀16.4 ↧ 1.4 spotface at each end, a ⌀11.2 × 0.7 ring on its floor) draws its profile view as
/// an ISO 128-50 half section: hatched on the side of the axis its lengths are dimensioned on, no
/// hidden edges, the ring's ⌀11.2 drawn as a section edge and dimensioned there as a half
/// dimension that lint still measures against its label.
#[test]
fn a_turned_part_with_a_stepped_bore_draws_its_profile_as_a_half_section() {
    let (_, p, mut d) = drawing(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/bearing-lm10uu.step.gz"),
    );
    let side = p.views.iter().find(|v| v.id == "v:side").unwrap();
    assert!(side.half_section.is_some(), "{}", side.basis);
    assert!(p.views.iter().filter(|v| v.half_section.is_some()).count() == 1);
    let frame = &d.sheet.frames["side"];
    let (k, axis) = frame.half_axis.unwrap();
    let scale = frame.scale;
    // The cut half: where the lengths' dimension lines are.
    let lengths = p
        .dimensions
        .iter()
        .find(|q| q.id == "d:envelope/depth.length")
        .unwrap();
    let cut_sign = (lengths.placed.as_ref().unwrap().lines[2][0][k] - axis).signum();
    let in_cut = |q: [f64; 2]| (q[k] - axis) * cut_sign > 1e-6;
    let area = side.area;
    let inside =
        |b: [f64; 4]| b[0] >= area[0] && b[2] <= area[2] && b[1] >= area[1] && b[3] <= area[3];
    let hidden = d.layers.iter().find(|l| l.name == "hidden").unwrap();
    assert!(
        !hidden.items.iter().any(|i| match i {
            draftwright::output::Item::Edge(c) => inside(c.bbox()),
            _ => false,
        }),
        "a half section draws no hidden edges"
    );
    let hatch = d
        .sheet
        .items
        .iter()
        .find(|i| i.name == "v:side/half_section_hatch")
        .unwrap();
    assert!(in_cut([hatch.ink[0], hatch.ink[1]]) && in_cut([hatch.ink[2], hatch.ink[3]]));
    // The ring's ⌀11.2 is a visible edge of the cut half.
    let ring = axis + cut_sign * 5.6 * scale;
    let view = d.sheet.views.iter().find(|v| v.name == "v:side").unwrap();
    let on_ring: f64 = view
        .edges
        .iter()
        .map(|e| e.bbox())
        .filter(|b| (b[k] - ring).abs() < 1e-3 && (b[k + 2] - ring).abs() < 1e-3)
        .map(|b| b[3 - k] - b[1 - k])
        .sum();
    assert!(on_ring > 0.7 * scale, "{on_ring}");
    // ⌀11.2: one witness on the cut edge, one arrow, the line past the axis.
    let ring_dim = p
        .dimensions
        .iter()
        .find(|q| q.id == "d:f:boss:f12/boss.diameter")
        .unwrap()
        .placed
        .as_ref()
        .unwrap();
    assert_eq!((ring_dim.lines.len(), ring_dim.arrows.len()), (2, 1));
    assert!((ring_dim.lines[0][0][k] - ring).abs() < 1e-6);
    // Its witness starts at the ring nearer the dimension line, not across the section from
    // the far one.
    let witness = ring_dim.lines[0];
    let run = (witness[0][1 - k] - witness[1][1 - k]).abs();
    assert!(
        run < 0.5 * (area[3 - k] - area[1 - k]),
        "{witness:?} in {area:?}"
    );
    let line = ring_dim.lines[1];
    assert!((line[0][k] - axis) * (line[1][k] - axis) < 0.0, "{line:?}");
    // The rings' heights: witnesses from the cut half.
    for id in [
        "d:f:boss:f12/boss_height.length",
        "d:f:boss:f16/boss_height.length",
    ] {
        let q = p.dimensions.iter().find(|q| q.id == id).unwrap();
        for w in &q.placed.as_ref().unwrap().lines[..2] {
            assert!(w.iter().all(|e| in_cut(*e)), "{id}: {w:?}");
        }
    }
    let measured = |d: &draftwright::output::Drawing| {
        draftwright::lint::structural::check(d)
            .into_iter()
            .filter(|i| i.code == "label_vs_measured")
            .count()
    };
    assert_eq!(measured(&d), 0);
    let drawn = d
        .sheet
        .items
        .iter_mut()
        .find(|i| i.name == "d:f:boss:f12/boss.diameter")
        .unwrap();
    assert_eq!(drawn.lines.len(), 2);
    for e in &mut drawn.lines[0] {
        e[k] += cut_sign;
    }
    assert_eq!(
        measured(&d),
        1,
        "a half dimension's label is measured from the axis"
    );
}

/// lm8uu (dw-examples): a bearing sleeve whose ⌀12.4 through bore runs under two ⌀13.4 outer
/// grooves draws its profile as a half section, so the thin wall left at each groove reads as a
/// section edge; its end view stays whole.
#[test]
fn a_bore_under_outer_grooves_draws_its_profile_as_a_half_section() {
    let (_, p, d) =
        drawing(&Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/lm8uu.step.gz"));
    let side = p.views.iter().find(|v| v.id == "v:side").unwrap();
    assert!(side.half_section.is_some(), "{}", side.basis);
    assert!(side.basis.contains("runs under a groove"), "{}", side.basis);
    assert_eq!(
        p.views.iter().filter(|v| v.half_section.is_some()).count(),
        1
    );
    assert!(
        d.sheet
            .items
            .iter()
            .any(|i| i.name == "v:side/half_section_hatch")
    );
}

/// `views/<name>/half_section` steers the choice: false keeps lm10uu's and lm8uu's side views
/// whole, true cuts t8-flange-nut's front view (a turned part the automatic rule leaves whole),
/// and a view with no turned profile seen side on stays whole with a warning.
#[test]
fn the_spec_forces_or_forbids_a_half_section() {
    let parts = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts");
    let spec = |json: Value| -> Spec { serde_json::from_value(json).unwrap() };
    let (_, p, _) = drawing_with(
        &parts.join("bearing-lm10uu.step.gz"),
        &spec(serde_json::json!({"views": {"side": {"half_section": false}}})),
    );
    assert!(p.views.iter().all(|v| v.half_section.is_none()));
    let ring = p
        .dimensions
        .iter()
        .find(|q| q.id == "d:f:boss:f12/boss.diameter")
        .unwrap();
    assert_eq!(ring.placed.as_ref().unwrap().lines.len(), 3);
    let (_, p, d) = drawing_with(
        &parts.join("lm8uu.step.gz"),
        &spec(serde_json::json!({"views": {"side": {"half_section": false}}})),
    );
    assert!(p.views.iter().all(|v| v.half_section.is_none()));
    assert!(
        d.sheet
            .items
            .iter()
            .all(|i| i.kind != draftwright::output::Kind::Hatch)
    );
    let (_, p, _) = drawing(&parts.join("t8-flange-nut.step.gz"));
    assert!(p.views.iter().all(|v| v.half_section.is_none()));
    let (_, p, d) = drawing_with(
        &parts.join("t8-flange-nut.step.gz"),
        &spec(
            serde_json::json!({"views": {"front": {"half_section": true}, "plan": {"half_section": true}}}),
        ),
    );
    let front = p.views.iter().find(|v| v.id == "v:front").unwrap();
    assert!(front.half_section.is_some());
    assert!(front.basis.contains("as the spec asks"));
    assert!(
        d.sheet
            .items
            .iter()
            .any(|i| i.name == "v:front/half_section_hatch")
    );
    let plan = p.views.iter().find(|v| v.id == "v:plan").unwrap();
    assert!(plan.half_section.is_none());
    assert!(
        p.problems
            .iter()
            .any(|q| q.id == "p:section_not_shown:v:plan" && q.severity == "warning")
    );
}

#[test]
fn a_half_section_drops_the_centre_lines_of_holes_it_cuts_away() {
    let Some(dir) = corpus::dir() else { return };
    let (_, a) = cache::native(&dir.join("cadgenbench/flanged_spool_132.step"));
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    let v = p.views.iter().find(|v| v.half_section.is_some()).unwrap();
    let h = v.half_section.as_ref().unwrap();
    let n = h.cut(v).normal;
    let r = h.rect(&a, v, true);
    let in_cut = |q: [f64; 2]| r[0] <= q[0] && q[0] <= r[2] && r[1] <= q[1] && q[1] <= r[3];
    let mut kept = 0;
    for (f, hole) in draftwright::bridge::holes::read(&a) {
        let Ok(hole) = hole else { continue };
        let Some(w) = v
            .line_work
            .iter()
            .find(|w| w.id == format!("ink:{}:{}", v.id, f.id))
        else {
            continue;
        };
        if !draftwright::centrelines::segments(w)
            .iter()
            .flatten()
            .all(|&q| in_cut(q))
        {
            continue;
        }
        // A hole marked on the cut half is one the cutting plane passes through.
        let off: f64 = (0..3).map(|k| (hole.at[k] - h.origin[k]) * n[k]).sum();
        assert!(off.abs() <= hole.diameter, "{}: {off}", f.id);
        kept += 1;
    }
    assert!(
        kept > 0,
        "the holes on the cutting plane keep their centre lines"
    );
}
