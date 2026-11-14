//! Hidden-line precedence and section material checks against the corpus B-rep.
#[path = "common/corpus.rs"]
mod corpus;

use crate::cache;
use draftwright::haecceity::{
    classify::{Classifier, State},
    hlr::View,
};
use draftwright::{
    api,
    model::{self},
    output::{Curve, Item},
    text::Font,
};

fn distance(p: [f64; 2], a: [f64; 2], b: [f64; 2]) -> f64 {
    let v = [b[0] - a[0], b[1] - a[1]];
    let vv = v[0] * v[0] + v[1] * v[1];
    let t = if vv == 0.0 {
        0.0
    } else {
        (((p[0] - a[0]) * v[0] + (p[1] - a[1]) * v[1]) / vv).clamp(0.0, 1.0)
    };
    (p[0] - a[0] - t * v[0]).hypot(p[1] - a[1] - t * v[1])
}

#[test]
fn m10020_hidden_ink_does_not_cover_visible_edges_and_warm_geometry_is_reused() {
    let Some(dir) = corpus::dir() else { return };
    let (part, a) = cache::native(&dir.join("mfcadpp/10020.step"));
    let plan = cache::default_plan(&a, true);
    let mut cache = model::Projections::default();
    let geometry = model::render_geometry(&part, &a, &plan, &mut cache).unwrap();
    for (id, g) in &geometry.views {
        if id == "v:iso" {
            continue;
        }
        let segments: Vec<_> = g
            .visible
            .iter()
            .flat_map(|item| {
                let Item::Edge(c) = item else { panic!("edge") };
                let n = if matches!(c, Curve::Line(..)) {
                    1
                } else {
                    2048
                };
                (0..n)
                    .map(|i| (c.at(i as f64 / n as f64), c.at((i + 1) as f64 / n as f64)))
                    .collect::<Vec<_>>()
            })
            .collect();
        for item in &g.hidden {
            let Item::Edge(c) = item else { panic!("edge") };
            let mut covered = 0.0;
            for i in 0..2048 {
                let (a, b, p) = (
                    c.at(i as f64 / 2048.),
                    c.at((i + 1) as f64 / 2048.),
                    c.at((i as f64 + 0.5) / 2048.),
                );
                if segments.iter().any(|&(a, b)| distance(p, a, b) <= 0.4) {
                    covered += (b[0] - a[0]).hypot(b[1] - a[1]);
                }
            }
            assert!(
                covered < 1.0,
                "{id}: hidden curve covers {covered} mm of visible ink: {c:?}"
            );
        }
    }
    let stats = cache.stats();
    let warm = model::render_geometry(&part, &a, &plan, &mut cache).unwrap();
    assert_eq!(
        stats,
        cache.stats(),
        "warm render has zero HLR and bounds passes"
    );
    assert_eq!(
        serde_json::to_value(geometry).unwrap(),
        serde_json::to_value(warm).unwrap()
    );
}

fn section(file: &str, check_hidden: bool) {
    let Some(dir) = corpus::dir() else { return };
    let (part, a) = cache::native(&dir.join(file));
    let plan = cache::default_plan(&a, true);
    let v = plan.views.iter().find(|v| v.cut.is_some()).unwrap();
    let cut = v.cut.as_ref().unwrap();
    let mut cache = model::Projections::default();
    let geometry = model::render_geometry(&part, &a, &plan, &mut cache).unwrap();
    if check_hidden {
        assert!(
            geometry
                .views
                .iter()
                .find(|(id, _)| *id == v.id)
                .unwrap()
                .1
                .hidden
                .is_empty(),
            "{file}: hidden ink in section"
        );
    }
    // The section is judged on the physical sheet that carries it.
    let drawing = if plan.sheets.is_empty() {
        model::render_annotations(
            &part,
            &a,
            &plan,
            &Font::new(api::MONO.to_vec()).unwrap(),
            geometry,
            &mut cache,
        )
        .unwrap()
    } else {
        model::render_sheets(&part, &a, &plan, &Font::new(api::MONO.to_vec()).unwrap())
            .unwrap()
            .swap_remove(v.sheet as usize - 1)
    };
    let view = View::new(v.direction, v.up).unwrap();
    let centre = view.map([0, 1, 2].map(|k| (a.envelope[0][k] + a.envelope[1][k]) / 2.));
    let to_model = |q: [f64; 2], side: f64| {
        let u = centre[0] + (q[0] - v.at[0]) / v.scale;
        let w = centre[1] + (q[1] - v.at[1]) / v.scale;
        let base = [0, 1, 2].map(|k| view.x[k] * u + view.y[k] * w);
        let along = (0..3)
            .map(|k| (cut.origin[k] - base[k]) * cut.normal[k])
            .sum::<f64>()
            / (0..3).map(|k| view.toward[k] * cut.normal[k]).sum::<f64>();
        [0, 1, 2].map(|k| base[k] + view.toward[k] * along + side * cut.normal[k] * 0.01)
    };
    if file.contains("string_post") {
        for x in [-0.8, 0.8] {
            let projected = view.map([x, 0.0, 2.0]);
            let target = [0, 1].map(|k| v.at[k] + v.scale * (projected[k] - centre[k]));
            assert!(
                drawing.layers[0].items.iter().any(|item| match item {
                    Item::Edge(Curve::Line(a, b)) => distance(target, *a, *b) < 1e-4,
                    Item::Edge(Curve::Beziers(pieces)) => pieces.iter().any(|poles| {
                        poles.len() == 2 && distance(target, poles[0], poles[1]) < 1e-4
                    }),
                    _ => false,
                }),
                "{file}: cut bore wall at {target:?} must be visible"
            );
        }
    }
    let classifier = Classifier::new(&part);
    let mut count = 0;
    for item in &drawing.layers[2].items {
        let Item::Edge(Curve::Line(p, q)) = item else {
            continue;
        };
        if ((q[1] - p[1]) - (q[0] - p[0])).abs() > 1e-9
            || ![p, q].iter().all(|p| {
                v.area[0] <= p[0] && p[0] <= v.area[2] && v.area[1] <= p[1] && p[1] <= v.area[3]
            })
        {
            continue;
        }
        count += 1;
        for t in [0.1, 0.5, 0.9] {
            let at = [p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])];
            for side in [-1., 1.] {
                let point = to_model(at, side);
                assert_eq!(
                    classifier.classify(point),
                    State::In,
                    "{file}: hatch at {point:?} is not cut material"
                );
                if file.contains("string_post") {
                    assert!(
                        !(point[2] > 0. && point[2] < 4. && point[0].hypot(point[1]) < 0.8),
                        "blind bore is a void"
                    );
                }
            }
        }
    }
    assert!(count > 0, "{file}: cut material must be hatched");
}
#[test]
fn ftc10_section_has_no_hidden_ink_and_hatches_only_cut_material() {
    section("nist/nist_ftc_10_asme1_rb.stp", true);
}

#[test]
fn string_post_section_bores_are_unhatched() {
    section("gramel/string_post.step", true);
}
#[test]
fn ftc06_section_bores_are_unhatched() {
    section("nist/nist_ftc_06_asme1_rd.stp", true);
}

#[test]
fn m10063_side_hidden_verticals_are_continuous_dash_runs() {
    let Some(dir) = corpus::dir() else { return };
    let (part, a) = cache::native(&dir.join("mfcadpp/10063.step"));
    let plan = cache::default_plan(&a, true);
    let geometry =
        model::render_geometry(&part, &a, &plan, &mut model::Projections::default()).unwrap();
    let side = &geometry
        .views
        .iter()
        .find(|(id, _)| id == "v:side")
        .unwrap()
        .1;
    let mut verticals = Vec::new();
    for item in &side.hidden {
        let Item::Edge(c) = item else { continue };
        let (a, b) = (c.start(), c.end());
        if (a[0] - b[0]).abs() < 1e-6 && (c.at(0.5)[0] - a[0]).abs() < 1e-6 {
            verticals.push((a[0], a[1].min(b[1]), a[1].max(b[1])));
        }
    }
    assert!(
        verticals.iter().filter(|(_, lo, hi)| hi - lo > 20.).count() >= 2,
        "the long hidden side-channel verticals must each span multiple dashes"
    );
    for (i, &(x, lo, hi)) in verticals.iter().enumerate() {
        for &(other_x, other_lo, other_hi) in &verticals[i + 1..] {
            assert!(
                (x - other_x).abs() > 1e-6 || other_lo > hi + 0.0081 || lo > other_hi + 0.0081,
                "touching collinear hidden paths restart dashes: {x}, {lo}..{hi}, {other_lo}..{other_hi}"
            );
        }
    }
}
