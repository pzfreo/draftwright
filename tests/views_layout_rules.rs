//! Views-and-layout catalogue ports; fixtures and thresholds follow the Python rule tests.
#[path = "common/corpus.rs"]
mod corpus;
use draftwright::model::{self, Analysis, Plan, Spec};
use serde_json::json;
use std::io::Read as _;
use std::path::{Path, PathBuf};

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).to_path_buf()
}
fn spec(v: serde_json::Value) -> Spec {
    serde_json::from_value(v).unwrap()
}
fn bare() -> Analysis {
    model::analyse(&root().join("tests/fixtures/parts/plate.step"))
        .unwrap()
        .1
}
fn pinned(a: &Analysis, scale: &str) -> Result<Plan, model::Invalid> {
    model::plan_recovered(a, &spec(json!({"sheet":{"scale":scale}})))
}

#[test]
fn explicit_scales_warn_below_legibility_and_refuse_degenerate_geometry() {
    let mut a = bare();
    a.envelope = [[0.0; 3], [680.0, 860.0, 80.0]];
    let p = pinned(&a, "1:10").unwrap();
    assert_eq!(p.sheet.scale, "1:10");
    let warning = p
        .problems
        .iter()
        .find(|p| p.code == "legibility_floor_breached")
        .expect("explicit subfloor scale warning");
    assert_eq!(warning.severity, "warning");
    assert!(
        warning.message.contains("10 mm") && warning.message.contains("0.125"),
        "{}",
        warning.message
    );
    let tiny = pinned(&a, "1:1000").unwrap();
    assert_eq!(tiny.sheet.scale, "1:1000");
    assert!(
        tiny.problems
            .iter()
            .any(|p| p.code == "geometry_below_resolution")
    );
    assert!(
        !pinned(&a, "1:5")
            .unwrap()
            .problems
            .iter()
            .any(|p| p.code == "legibility_floor_breached")
    );
    // Python does not nag about an inherently thin part: no page-fitting scale is legible.
    a.envelope = [[0.0; 3], [3000.0, 3000.0, 15.0]];
    assert!(
        !pinned(&a, "1:20")
            .unwrap()
            .problems
            .iter()
            .any(|p| p.code == "legibility_floor_breached")
    );
    a.envelope[1][2] = 0.0;
    assert!(
        pinned(&a, "1:1")
            .unwrap_err()
            .message
            .contains("three-dimensional extent")
    );
}

#[test]
fn principal_relations_cannot_contradict_the_projection() {
    let a = bare();
    for (method, plan_side, side_side) in [("third", "above", "right"), ("first", "below", "left")]
    {
        for (view, anchor, good, bad) in [
            (
                "plan",
                "front",
                plan_side,
                if plan_side == "above" {
                    "below"
                } else {
                    "above"
                },
            ),
            (
                "side",
                "front",
                side_side,
                if side_side == "right" {
                    "left"
                } else {
                    "right"
                },
            ),
        ] {
            let asked = |side: &str, align: &str| {
                spec(
                    json!({"sheet":{"projection":method,"size":"A2","scale":"1:1"},"views":{view:{"place":{"relative_to":anchor,"side":side,"align":align}}}}),
                )
            };
            let p = model::plan(&a, &asked(good, "middle")).unwrap();
            let front = p.views.iter().find(|v| v.id == "v:front").unwrap();
            let other = p
                .views
                .iter()
                .find(|v| v.id == format!("v:{view}"))
                .unwrap();
            let k = usize::from(view == "side");
            assert!(
                (front.at[k] - other.at[k]).abs() < 1e-9,
                "{method} {view}: projection origins must align"
            );
            for s in [
                asked(bad, "middle"),
                asked(good, "start"),
                asked(if view == "plan" { "left" } else { "above" }, "middle"),
            ] {
                let rejected = model::plan(&a, &s).unwrap();
                assert!(
                    rejected
                        .problems
                        .iter()
                        .any(|e| e.code == "projection_relation_infeasible"
                            && e.severity == "error"
                            && e.message.contains("projection"))
                );
                assert_eq!(rejected.spec.views, s.views);
            }
        }
    }
}

/// Pins are immutable intent: diagnose an infeasible convention without silently
/// moving or dropping the pinned view (the existing placement contract).
#[test]
fn contradictory_principal_pins_stay_fixed_and_report_infeasibility() {
    let a = bare();
    let s = spec(
        json!({"views":{"plan":{"place":{"relative_to":"front","side":"below","align":"start","pinned":true}}}}),
    );
    let p = model::plan(&a, &s).unwrap();
    let front = p.views.iter().find(|v| v.id == "v:front").unwrap();
    let plan = p.views.iter().find(|v| v.id == "v:plan").unwrap();
    assert!(plan.at[1] < front.at[1]);
    assert!(
        p.problems
            .iter()
            .any(|p| p.code == "projection_relation_infeasible"
                && p.severity == "error"
                && p.subjects.contains(&"v:plan".into()))
    );
    assert_eq!(p.spec.views, s.views);
}

fn corridor(p: &Plan, d: &draftwright::dims::PlannedDimension) {
    let q = d.placed.as_ref().expect("linear must be placed");
    let v = p.views.iter().find(|v| v.id == d.view).unwrap();
    for k in 0..2 {
        for x in q
            .lines
            .iter()
            .chain(&q.arrows)
            .flatten()
            .map(|a| a[k])
            .chain([q.label_box[k], q.label_box[k + 2]])
        {
            assert!(
                v.area[k] - 1e-6 <= x && x <= v.area[k + 2] + 1e-6,
                "{} {} axis {k}: {x} outside {:?}",
                d.id,
                v.id,
                v.area
            );
        }
    }
}

#[test]
fn every_source_and_explicit_linear_corridor_is_reserved() {
    let dir = corpus::dir().unwrap();
    for f in [
        "cadgenbench_inputs/cgb245.step",
        "nist/nist_ctc_01_asme1_rd.stp",
        "nist/nist_ftc_08_asme1_rc.stp",
    ] {
        let a = model::analyse_native(&dir.join(f)).unwrap().1;
        let p = model::plan_recovered(
            &a,
            &serde_json::from_value(serde_json::json!({"sheet":{"preferred_max_size":"A0"}}))
                .unwrap(),
        )
        .unwrap();
        let mut n = 0;
        for d in p.dimensions.iter().filter(|d| d.kind == "linear") {
            corridor(&p, d);
            n += 1;
        }
        assert!(n >= 3, "{f}");
    }
    let a = bare();
    // Each axis-aligned principal strip, including the formerly estimated bands.
    for (id, view, sides) in [
        ("d:envelope/width.length", "front", ["above", "below"]),
        ("d:envelope/width.length", "plan", ["above", "below"]),
        ("d:envelope/height.length", "front", ["left", "right"]),
        ("d:envelope/depth.length", "plan", ["left", "right"]),
        ("d:envelope/height.length", "side", ["left", "right"]),
        ("d:envelope/depth.length", "side", ["above", "below"]),
    ] {
        for side in sides {
            let s = spec(json!({"dimensions":{id:{"place":{"view":view,"side":side,"lane":6}}}}));
            let p = model::plan_recovered(&a, &s).unwrap();
            corridor(&p, p.dimensions.iter().find(|d| d.id == id).unwrap());
        }
    }
}

#[test]
fn internal_stations_require_three_details_of_two_kinds() {
    let dir = root().join("tests/fixtures/views_layout");
    let (part, mut a) = model::analyse(&dir.join("internal_station.step")).unwrap();
    let mut r = String::new();
    flate2::read::GzDecoder::new(
        std::fs::File::open(dir.join("internal_station.json.gz")).unwrap(),
    )
    .read_to_string(&mut r)
    .unwrap();
    model::recognise(&part, &mut a, &r).unwrap();
    let demand = draftwright::demand::read(&a);
    let section = demand
        .section
        .expect("dense internal station warrants a section");
    assert!((section.cut_y + 7.9).abs() < 1e-6, "{section:?}");
    let unsectioned =
        model::plan(&a, &spec(json!({"views":{"section_aa":{"omit":true}}}))).unwrap();
    let slot_requirements = |p: &Plan| -> std::collections::BTreeSet<(String, String, u64)> {
        p.dimensions
            .iter()
            .filter(|d| d.placed.is_some())
            .flat_map(|d| &d.measurements)
            .filter(|m| {
                m.parameter.starts_with("slot_") || m.parameter.starts_with("location_slot")
            })
            .map(|m| (m.feature.clone(), m.parameter.clone(), m.value.to_bits()))
            .collect()
    };
    let required = slot_requirements(&unsectioned);
    assert!(
        required.len() >= 5,
        "the fixture must exercise multiple required slot measurements"
    );
    for recovered in [false, true] {
        let p = if recovered {
            model::plan_recovered(&a, &Spec::default())
        } else {
            model::plan(&a, &Spec::default())
        }
        .unwrap();
        assert!(p.views.iter().any(|v| v.id == "v:section_aa"));
        assert_eq!(
            slot_requirements(&p),
            required,
            "section allocation lost a required slot measurement"
        );
        let drawing = model::render(
            &part,
            &a,
            &p,
            &draftwright::text::Font::new(draftwright::api::MONO.to_vec()).unwrap(),
        )
        .unwrap();
        assert!(
            drawing
                .sheet
                .items
                .iter()
                .any(|i| i.name == "section_hatch")
        );
        for parameter in ["slot_width.length", "slot_length.length"] {
            assert!(
                p.dimensions.iter().any(|d| d.placed.is_some()
                    && d.measurements.iter().any(|m| m.parameter == parameter)),
                "{parameter}"
            );
        }
    }
    let hex_id = draftwright::lint::pockets::hexes(&a)[0].feature.id.clone();
    let seat_id = draftwright::lint::channels::seats(&a)[0].0.id.clone();
    let mut two = a.clone();
    two.features.retain(|f| f.id == hex_id || f.id == seat_id);
    assert!(
        draftwright::demand::read(&two).section.is_none(),
        "one detail of each kind does not spend a section"
    );
    a.features.retain(|f| {
        draftwright::lint::section_recesses::grammar(f).is_ok_and(|g| {
            matches!(
                g,
                draftwright::lint::section_recesses::Grammar::HexPocket { .. }
            )
        })
    });
    assert!(
        draftwright::demand::read(&a).section.is_none(),
        "pockets of one kind alone do not warrant a section"
    );
}

#[test]
fn a_radial_flat_or_cross_slot_vetoes_turning_view_reduction() {
    let (_, a) = model::analyse_with(
        &root().join("tests/fixtures/parts/turned.step"),
        Some(&root().join("tests/fixtures/recognition/turned.json")),
    )
    .unwrap();
    for (kind, parameters) in [
        ("flat", json!({"axis":"y","at":[20.0,0.0,0.0],"across":8.0})),
        (
            "slot",
            json!({"long_axis":"x","width_axis":"z","width":4.0,"length":10.0,"lo":5.0,"hi":15.0,"w_center":0.0,"d_lo":-10.0,"d_hi":10.0}),
        ),
        (
            "fillet",
            json!({"axis":"y","at":[20.0,0.0,0.0],"radius":1.5,"turned":false}),
        ),
    ] {
        let mut a = a.clone();
        // A planner-input regression: these radial requirements need the side end-on.
        let mut f = a.features[0].clone();
        f.id = format!("f:radial:{kind}");
        f.kind = kind.into();
        f.parameters = parameters.as_object().unwrap().clone();
        a.features.push(f);
        for scale in [None, Some("1:1")] {
            let mut s = Spec::default();
            s.sheet.scale = scale.map(String::from);
            for recovered in [false, true] {
                let p = if recovered {
                    model::plan_recovered(&a, &s)
                } else {
                    model::plan_escalated(&a, &s)
                }
                .unwrap();
                assert!(p.views.iter().any(|v| v.id == "v:side"), "{kind}");
                assert!(
                    !p.problems.iter().any(|p| p.code == "view_plan_incomplete"),
                    "{kind}: {:?}",
                    p.problems
                );
                let required: Vec<_> = p
                    .dimensions
                    .iter()
                    .filter(|d| {
                        d.measurements
                            .iter()
                            .any(|m| m.feature == format!("f:radial:{kind}"))
                    })
                    .collect();
                assert!(!required.is_empty(), "{kind}: no requirements were planned");
                assert!(
                    required.iter().all(|d| d.placed.is_some()),
                    "{kind}: {required:?}"
                );
            }
        }
    }
}

fn section_plan(a: &Analysis, cut_y: f64) -> Plan {
    let mut p = model::plan(a, &spec(json!({"sheet":{"size":"A2","scale":"2:1"}}))).unwrap();
    let mut v = p.views.iter().find(|v| v.id == "v:front").unwrap().clone();
    v.id = "v:section_aa".into();
    v.at = [440.0, 200.0];
    v.area = [350.0, 140.0, 540.0, 280.0];
    v.cut = Some(model::Cut {
        label: "A".into(),
        origin: [0.0, cut_y, 0.0],
        normal: [0.0, 1.0, 0.0],
        marked_in: "v:plan".into(),
        xref: None,
    });
    p.views.push(v);
    p
}

#[test]
fn section_hatch_excludes_coplanar_boundaries_and_retained_material() {
    for name in [
        "section_attached",
        "section_detached",
        "section_adjacent",
        "section_notch",
        "section_regions",
    ] {
        let (part, a) =
            model::analyse(&root().join(format!("tests/fixtures/views_layout/{name}.step")))
                .unwrap();
        let p = section_plan(&a, 0.0);
        let v = p.views.last().unwrap();
        let drawing = model::render(
            &part,
            &a,
            &p,
            &draftwright::text::Font::new(draftwright::api::MONO.to_vec()).unwrap(),
        )
        .unwrap();
        let mut lines = 0;
        for i in &drawing.layers[2].items {
            let draftwright::output::Item::Edge(draftwright::output::Curve::Line(from, to)) = i
            else {
                continue;
            };
            if ![from, to].iter().all(|q| {
                v.area[0] <= q[0] && q[0] <= v.area[2] && v.area[1] <= q[1] && q[1] <= v.area[3]
            }) || ((to[1] - from[1]) - (to[0] - from[0])).abs() > 1e-8
            {
                continue;
            }
            lines += 1;
            for t in [0.01, 0.25, 0.5, 0.75, 0.99] {
                let x = (from[0] + t * (to[0] - from[0]) - v.at[0]) / v.scale
                    + (a.envelope[0][0] + a.envelope[1][0]) / 2.0;
                let z = (from[1] + t * (to[1] - from[1]) - v.at[1]) / v.scale
                    + (a.envelope[0][2] + a.envelope[1][2]) / 2.0;
                let base = (-10.0001..=10.0001).contains(&x) && (-4.0001..=4.0001).contains(&z);
                let expected = if name == "section_regions" {
                    (base && (x + 5.0).hypot(z) >= 2.0 - 1e-3)
                        || ((15.9999..=20.0001).contains(&x) && (-2.0001..=2.0001).contains(&z))
                } else if name == "section_notch" {
                    base && !(x > 1e-4 && x < 10.0 - 1e-4 && z > 1e-4)
                } else {
                    base
                };
                assert!(
                    expected,
                    "{name}: hatch at [{x},{z}] describes an uncut face or void"
                );
            }
        }
        assert!(lines > 5, "{name}: {lines}");
    }
}

#[test]
fn gap_tangent_and_outside_sections_are_reported_without_false_cut_ink() {
    let (part, a) =
        model::analyse(&root().join("tests/fixtures/views_layout/section_gap.step")).unwrap();
    for y in [-9.0, -8.0, -4.0, 0.0, 4.0, 8.0, 9.0] {
        let mut p = section_plan(&a, y);
        // A required measurement carried by a refused section cannot leave
        // floating dimension ink after that section's geometry is withdrawn.
        let mut carried = p.dimensions.remove(0);
        carried.view = "v:section_aa".into();
        let carried_id = carried.id.clone();
        p.dimensions.push(carried);
        let d = model::render(
            &part,
            &a,
            &p,
            &draftwright::text::Font::new(draftwright::api::MONO.to_vec()).unwrap(),
        )
        .unwrap();
        assert!(
            !d.sheet.items.iter().any(|i| i.name.starts_with("section_")),
            "y={y}"
        );
        assert!(
            !d.sheet.views.iter().any(|v| v.name == "v:section_aa"),
            "y={y}"
        );
        assert!(
            !d.sheet.items.iter().any(|i| i.name == carried_id),
            "y={y}: a refused section still states {carried_id}"
        );
        assert!(
            d.problems.iter().any(|p| p.code == "section_dropped"
                && p.message.contains(if y.abs() >= 8.0 {
                    "not strictly inside"
                } else {
                    "cut_no_intersection"
                })),
            "y={y}: {:?}",
            d.problems
        );
    }
}

#[test]
fn explicit_dimensions_that_reflow_keep_their_reserved_corridors() {
    let a = model::analyse_native(&corpus::dir().unwrap().join("nist/nist_ctc_01_asme1_rd.stp"))
        .unwrap()
        .1;
    let source = model::plan(&a, &Spec::default()).unwrap();
    let slot = source
        .dimensions
        .iter()
        .find(|d| d.id.ends_with("/slot_length.length") && d.view == "v:plan")
        .unwrap();
    let s = spec(json!({"dimensions":{
        "d:envelope/width.length":{"place":{"view":"plan","side":"above","lane":6}},
        slot.id.clone():{"place":{"view":"plan","side":"above","lane":6}}
    }}));
    let p = model::plan_recovered(&a, &s).unwrap();
    let mut normals = Vec::new();
    for id in ["d:envelope/width.length", slot.id.as_str()] {
        let d = p.dimensions.iter().find(|d| d.id == id).unwrap();
        corridor(&p, d);
        normals.push(d.placed.as_ref().unwrap().arrows[0][0][1]);
    }
    assert!((normals[0] - normals[1]).abs() >= 7.0 - 1e-6, "{normals:?}");
}

#[test]
fn indirect_principal_relations_report_projection_infeasibility() {
    let a = bare();
    let s = spec(json!({"views":{"plan":{"place":{"relative_to":"iso","side":"below"}}}}));
    let p = model::plan(&a, &s).unwrap();
    assert!(
        p.problems
            .iter()
            .any(|p| p.code == "projection_relation_infeasible" && p.severity == "error")
    );
    let s = spec(json!({"views":{"side":{"place":{"relative_to":"plan","side":"right"}}}}));
    assert!(
        model::plan(&a, &s)
            .unwrap()
            .problems
            .iter()
            .any(|e| e.code == "projection_relation_infeasible" && e.severity == "error")
    );
}

#[test]
fn reserved_location_corridors_choose_witnesses_clear_of_foreign_holes() {
    for (file, id, foreign) in [
        (
            "nist/nist_ftc_06_asme1_rd.stp",
            "d:f:hole:5/location.length.y",
            vec!["f:hole:11", "f:hole:12"],
        ),
        (
            "nist/nist_ftc_07_asme1_rd.stp",
            "d:f:hole:8/location.length.y",
            vec!["f:hole:5", "f:hole:6", "f:hole:7"],
        ),
    ] {
        let a = model::analyse_native(&corpus::dir().unwrap().join(file))
            .unwrap()
            .1;
        let p = model::plan_recovered(
            &a,
            &serde_json::from_value(serde_json::json!({"sheet":{"preferred_max_size":"A0"}}))
                .unwrap(),
        )
        .unwrap();
        let d = p.dimensions.iter().find(|d| d.id == id).unwrap();
        corridor(&p, d);
        assert!(
            !p.problems
                .iter()
                .any(|p| p.code == "extension_through_feature" && p.subjects.contains(&id.into())),
            "{file}"
        );
        let view = p.views.iter().find(|v| v.id == d.view).unwrap();
        let frame = draftwright::haecceity::hlr::View::new(view.direction, view.up).unwrap();
        let middle = [0, 1, 2].map(|k| (a.envelope[0][k] + a.envelope[1][k]) / 2.0);
        let centre = frame.map(middle);
        for (f, h) in draftwright::bridge::holes::read(&a) {
            if !foreign.contains(&f.id.as_str()) {
                continue;
            }
            let h = h.unwrap();
            let q = frame.map(h.at);
            let c = [0, 1].map(|k| view.at[k] + view.scale * (q[k] - centre[k]));
            for [from, to] in &d.placed.as_ref().unwrap().lines[..2] {
                let vector = [to[0] - from[0], to[1] - from[1]];
                let length = vector[0] * vector[0] + vector[1] * vector[1];
                let t = (((c[0] - from[0]) * vector[0] + (c[1] - from[1]) * vector[1]) / length)
                    .clamp(0.0, 1.0);
                assert!(
                    (from[0] + t * vector[0] - c[0]).hypot(from[1] + t * vector[1] - c[1])
                        >= h.diameter * view.scale / 2.0 - 1e-6,
                    "{file}: witness crosses {}",
                    f.id
                );
            }
        }
    }
}

#[test]
fn isometric_hidden_ink_obeys_visible_line_precedence() {
    let (part, a) = model::analyse_native(
        &corpus::dir()
            .unwrap()
            .join("cadgenbench_inputs/cgb207.step"),
    )
    .unwrap();
    let default = model::plan_recovered(&a, &Spec::default()).unwrap();
    let g =
        model::render_geometry(&part, &a, &default, &mut model::Projections::default()).unwrap();
    let iso = &g.views.iter().find(|(id, _)| id == "v:iso").unwrap().1;
    assert!(
        iso.hidden.is_empty(),
        "the isometric omits hidden edges by default"
    );
    // Requested hidden edges still yield to visible ink.
    let p = model::plan_recovered(&a, &spec(json!({"views": {"iso": {"hidden": true}}}))).unwrap();
    let g = model::render_geometry(&part, &a, &p, &mut model::Projections::default()).unwrap();
    let iso = &g.views.iter().find(|(id, _)| id == "v:iso").unwrap().1;
    let visible: Vec<_> = iso
        .visible
        .iter()
        .filter_map(|i| {
            if let draftwright::output::Item::Edge(c) = i {
                Some(c.clone())
            } else {
                None
            }
        })
        .collect();
    let length = |c: &draftwright::output::Curve| {
        (0..256)
            .map(|i| {
                let a = c.at(i as f64 / 256.0);
                let b = c.at((i + 1) as f64 / 256.0);
                (a[0] - b[0]).hypot(a[1] - b[1])
            })
            .sum::<f64>()
    };
    assert!(!iso.hidden.is_empty(), "truly hidden edges must survive");
    for i in &iso.hidden {
        let draftwright::output::Item::Edge(c) = i else {
            continue;
        };
        let remaining: f64 = draftwright::linework::subtract(c, &visible)
            .iter()
            .map(&length)
            .sum();
        assert!(
            length(c) - remaining < 0.01,
            "isometric hidden edge retraces {:.4} mm of visible ink: {c:?}",
            length(c) - remaining
        );
    }
}
