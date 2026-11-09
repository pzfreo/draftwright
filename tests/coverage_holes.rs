//! Python issues 1058/1370: physical requirements survive missing or misleading ink.
use draftwright::{
    lint::{self, Mark},
    model::{self, Analysis, Spec},
    output::Claim,
};
use serde_json::{Value, json};

fn feature(kind: &str, n: usize, parameters: Value) -> Value {
    json!({"id":format!("f:{kind}:{n}"),"kind":kind,"faces":[n],"parameters":parameters,
        "fingerprint":{"version":"test","family":kind,"subtype":kind,"sizes":{},
        "position":[0,0,5],"axis":[0,0,1],"neighbourhood":[]}})
}
fn analysis(features: Vec<Value>) -> Analysis {
    serde_json::from_value(json!({"schema":model::ANALYSIS_SCHEMA,
      "binding":{"source_sha256":"","part":"test","face_count":20,"loader":"test"},
      "envelope":[[-25,-25,0],[25,25,10]],"iso_bounds":[-25,-25,25,25],"features":features}))
    .unwrap()
}
fn marks(a: &Analysis) -> Vec<Mark> {
    model::plan(a, &Spec::default())
        .unwrap()
        .dimensions
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
fn double_d(through: bool, direction: [f64; 3]) -> Value {
    feature(
        "double_d_bore",
        1,
        json!({"axis":[0,0,1],"location":[0,0,5],
        "major_diameter":10,"across_flats":7.2,"depth":10,"through":through,"flat_direction":direction}),
    )
}
#[test]
fn double_d_planner_states_both_roles_in_any_flat_direction_and_never_invents_thru() {
    let ink = |direction| {
        let p = model::plan(&analysis(vec![double_d(true, direction)]), &Spec::default()).unwrap();
        p.dimensions
            .into_iter()
            .find(|d| d.label.contains("DOUBLE-D"))
            .unwrap()
            .placed
    };
    assert_eq!(
        ink([1., 0., 0.]),
        ink([-1., 0., 0.]),
        "flat direction is an unoriented line"
    );
    for direction in [[1., 0., 0.], [-1., 0., 0.], [0.866025403784, 0.5, 0.]] {
        for through in [true, false] {
            let a = analysis(vec![double_d(through, direction)]);
            let p = model::plan(&a, &Spec::default()).unwrap();
            let d = p
                .dimensions
                .iter()
                .find(|d| {
                    d.measurements
                        .iter()
                        .any(|m| m.parameter == "profile_across_flats.length")
                })
                .expect("compound double-D callout");
            assert_eq!(
                d.label,
                if through {
                    "⌀10 THRU DOUBLE-D 7.2 A/F"
                } else {
                    "⌀10 ↧ 10 DOUBLE-D 7.2 A/F"
                }
            );
            assert!(d.placed.is_some());
            assert!(!p.problems.iter().any(|p| p.code == "dimensions_not_ported"
                && p.subjects.contains(&"f:double_d_bore:f6".into())));
            assert!(
                !lint::check(&a, &marks(&a))
                    .iter()
                    .any(|i| i.code == "profiled_bore_not_dimensioned")
            );
        }
    }
}
#[test]
fn double_d_ink_requires_one_live_callout_with_truthful_roles_and_units() {
    let a = analysis(vec![double_d(true, [1., 0., 0.])]);
    let good = marks(&a);
    let index = good
        .iter()
        .position(|m| m.text.contains("DOUBLE-D"))
        .expect("profile callout");
    for bad in [
        "⌀7.2 THRU DOUBLE-D 10 A/F",
        "⌀10 THRU DOUBLE-D 7.2",
        "⌀10 THRU DOUBLE-D 7.2 A/Fextra",
        "⌀10 THRU 7.2 A/F",
        "⌀10 DOUBLE-D 7.2 A/F",
    ] {
        let mut damaged = good.clone();
        damaged[index].text = bad.into();
        assert!(
            lint::check(&a, &damaged)
                .iter()
                .any(|i| i.code == "profiled_bore_not_dimensioned"),
            "{bad}"
        );
    }
    let mut removed = good;
    removed.remove(index);
    assert!(
        lint::check(&a, &removed)
            .iter()
            .any(|i| i.code == "profiled_bore_not_dimensioned")
    );
}
#[test]
fn a_second_countersink_seat_is_unverifiable_without_recounting_the_bore() {
    let seat = |z: f64| json!({"axis":[0,0,1],"location":[0,0,z],"major_diameter":14,"drill_diameter":6,"included_angle":90,"depth":4});
    let a = analysis(vec![
        feature(
            "hole",
            1,
            json!({"axis":[0,0,1],"location":[0,0,10],"diameter":6,"depth":10,"bottom":"through","csink":seat(10.)}),
        ),
        feature("countersink", 2, seat(10.)),
        feature("countersink", 3, seat(0.)),
    ]);
    let found = lint::check(&a, &marks(&a));
    let second: Vec<_> = found
        .iter()
        .filter(|i| {
            i.code == "hole_requirement_unverifiable"
                && i.subjects.contains(&"f:countersink:3".into())
        })
        .collect();
    assert_eq!(second.len(), 2, "{found:?}");
    assert!(second.iter().all(|i| {
        i.requirements
            .iter()
            .all(|r| r.feature == "f:countersink:3" && r.parameter.starts_with("countersink."))
    }));
    assert!(
        !found.iter().any(|i| i.code.starts_with("hole_requirement_")
            && i.subjects.contains(&"f:countersink:f7".into()))
    );
    assert!(!found.iter().any(|i| {
        i.code.starts_with("hole_requirement_")
            && i.requirements
                .iter()
                .any(|r| r.parameter == "bore.diameter")
    }));
}

#[test]
fn every_prismatic_shoulder_station_needs_its_own_truthful_drawing_evidence() {
    let mut a = analysis(vec![]);
    a.levels = vec![4.];
    a.risers = serde_json::from_value(json!([{"vertical":true,"axis":"x","positions":[-10,10],
        "other_axis":"y","other_positions":[],"z_lo":4,"z_hi":10,"lo_at_envelope":false,
        "hi_at_envelope":true,"tol":0.01,"body_levels":[4]}]))
    .unwrap();
    let good = marks(&a);
    let check = |ms: &[Mark]| {
        lint::check(&a, ms)
            .into_iter()
            .filter(|i| i.code == "unrecognised_defining_geometry")
            .collect::<Vec<_>>()
    };
    assert!(check(&good).is_empty(), "{:?}", check(&good));
    let mut removed = good.clone();
    removed.retain(|m| {
        !m.claims
            .iter()
            .any(|c| c.parameter == "step_position.length.x15")
    });
    assert_eq!(check(&removed).len(), 1);
    let mut corrupt = good;
    corrupt
        .iter_mut()
        .filter(|m| {
            m.claims
                .iter()
                .any(|c| c.parameter == "step_position.length.x15")
        })
        .for_each(|m| m.text = "35".into());
    assert_eq!(check(&corrupt).len(), 1);
    a.risers[0].positions.pop();
    assert!(
        !lint::check(&a, &[])
            .iter()
            .any(|i| i.code == "unrecognised_defining_geometry"),
        "a lone transition may belong to a plate scheme"
    );
}

#[test]
fn independent_countersink_fixtures_keep_each_seat_and_reject_misleading_ink() {
    for name in [
        "countersink-single.step",
        "countersink-mixed-pair.step",
        "countersink-topology-a.step",
        "countersink-deburr.step",
        "countersink-external-cone.step",
    ] {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/coverage-holes")
            .join(name);
        let (_, a) = model::analyse_native(&path).unwrap();
        let good = marks(&a);
        let seats: Vec<_> = a
            .features
            .iter()
            .filter(|f| f.kind == "countersink")
            .collect();
        let expected = match name {
            "countersink-mixed-pair.step" | "countersink-topology-a.step" => 2,
            "countersink-external-cone.step" | "countersink-deburr.step" => 0,
            _ => 1,
        };
        assert_eq!(seats.len(), expected, "{name}");
        let gaps = |ms: &[Mark]| {
            lint::check(&a, ms)
                .into_iter()
                .filter(|i| {
                    i.requirements
                        .iter()
                        .any(|r| r.parameter.starts_with("countersink."))
                })
                .collect::<Vec<_>>()
        };
        assert!(gaps(&good).is_empty(), "{name}: {:?}", gaps(&good));
        for m in good.iter().filter(|m| m.text.contains('⌵')) {
            let (prefix, seat) = m.text.split_once("⌵ ⌀").unwrap();
            let (diameter, angle) = seat.split_once(" × ").unwrap();
            let swapped = format!("{prefix}⌵ ⌀{} × {diameter}°", angle.trim_end_matches('°'));
            for bad in [m.text.replace('°', ""), m.text.replace('°', "rad"), swapped] {
                let mut damaged = good.clone();
                damaged.iter_mut().find(|n| n.name == m.name).unwrap().text = bad.clone();
                assert!(!gaps(&damaged).is_empty(), "{name}: {bad}");
            }
        }
    }
}

#[test]
fn outer_profile_angles_keep_corner_identity_and_require_live_degree_ink() {
    let mut a = analysis(vec![]);
    let source = json!({"face":1,"body_faces":[1,2,3,4,5,6],"edges":[1,2,3,4],"profile":{
        "origin":[0,0,10],"normal":[0,0,1],"schema_version":1,"boundary_kind":"outer","inner_loop_count":1,
        "supports":[{"kind":"line","start":[0,0,10],"end":[40,0,10]},
            {"kind":"line","start":[40,0,10],"end":[30,20,10]},
            {"kind":"line","start":[30,20,10],"end":[0,20,10]},
            {"kind":"line","start":[0,20,10],"end":[0,0,10]}]}});
    a.envelope = [[0., 0., 0.], [40., 20., 10.]];
    // Same body back face is not another requirement; tied foremost faces stay distinct.
    a.outer_profiles = serde_json::from_value(json!([source])).unwrap();
    let mut back = a.outer_profiles[0].clone();
    back.face = 2;
    back.profile.origin[2] = 0.;
    a.outer_profiles.push(back);
    let requirements = draftwright::lint::profile_angles::requirements(&a);
    assert_eq!(requirements.len(), 2);
    let good: Vec<_> = requirements
        .iter()
        .map(|c| Mark {
            name: c.feature.clone(),
            view: Some("v:plan".into()),
            text: format!("{}°", draftwright::dims::fmt(c.degrees())),
            claims: vec![Claim {
                feature: c.feature.clone(),
                parameter: "profile_angle.angle".into(),
            }],
        })
        .collect();
    let gaps = |ms: &[Mark]| {
        lint::check(&a, ms)
            .into_iter()
            .filter(|i| i.code.starts_with("angular_requirement_"))
            .collect::<Vec<_>>()
    };
    assert!(gaps(&good).is_empty(), "{:?}", gaps(&good));
    let target = good
        .iter()
        .position(|m| {
            m.claims
                .iter()
                .any(|c| c.parameter == "profile_angle.angle")
        })
        .unwrap();
    let mut removed = good.clone();
    removed.remove(target);
    assert_eq!(gaps(&removed).len(), 1);
    for text in ["90°", "63.4", "63.4 radians"] {
        let mut damaged = good.clone();
        damaged[target].text = text.into();
        assert_eq!(gaps(&damaged).len(), 1, "{text}");
    }
    let mut other = good;
    other[target].claims[0].feature = "outer_profile:foreign".into();
    assert_eq!(
        gaps(&other).len(),
        1,
        "stale unrelated provenance cannot satisfy a physical corner"
    );
}

/// CTC-01: a 5 mm chamfer leaves the hex boss's top cap a smaller hexagon bounded by chamfer
/// faces. Its sides stay parallel to the boss's flats, so the HEX callout states its 120°.
#[test]
fn a_chamfered_polygon_cap_takes_its_corner_angles_from_the_polygon_callout() {
    let cap = |turn: f64| {
        let c = 45.0 / 30f64.to_radians().cos();
        let corners: Vec<_> = (0..6)
            .map(|k| {
                let t = (30. + 60. * k as f64).to_radians();
                [c * t.cos(), c * t.sin(), 50.]
            })
            .collect();
        let supports: Vec<_> = (0..6)
            .map(|k| json!({"kind":"line","start":corners[k],"end":corners[(k + 1) % 6]}))
            .collect();
        let flats: Vec<_> = (0..6)
            .map(|k| {
                let t = (60. * k as f64 + turn).to_radians();
                [t.cos(), t.sin(), 0.]
            })
            .collect();
        let mut boss = feature(
            "polygonal_boss",
            61,
            json!({"axis":"z","side_count":6,"across_flats":100,"center":[0,0,25],
                "flat_directions":flats}),
        );
        boss["faces"] = json!([61, 62, 63, 64, 65, 66]);
        let mut a = analysis(vec![boss]);
        a.envelope = [[-50., -50., 0.], [50., 50., 50.]];
        a.outer_profiles = serde_json::from_value(json!([{"face":67,
            "body_faces":(60..=80).collect::<Vec<_>>(),"edges":[1,2,3,4,5,6],"profile":{
            "origin":[0,0,50],"normal":[0,0,1],"schema_version":1,"boundary_kind":"outer",
            "inner_loop_count":0,"supports":supports}}]))
        .unwrap();
        lint::profile_angles::requirements(&a).len()
    };
    assert_eq!(cap(0.), 0, "sides parallel to the stated hexagon's flats");
    assert_eq!(
        cap(15.),
        6,
        "a cap not aligned with the polygon keeps every corner"
    );
}

fn shafts(xs: &[f64]) -> Analysis {
    let features=xs.iter().enumerate().flat_map(|(n,&x)|[(30,0,20),(20,20,40)].into_iter().enumerate().map(move |(i,(d,lo,hi))|
        feature("turned_step",2*n+i+1,json!({"axis":"z","lo":lo,"hi":hi,"diameter":d,"profile":{"axis":"z","axis_origin":[x,0,0]}})))).collect();
    let mut a = analysis(features);
    a.envelope = [
        [*xs.first().unwrap() - 15., -15., 0.],
        [*xs.last().unwrap() + 15., 15., 40.],
    ];
    a
}
#[test]
fn a_middle_turned_body_uses_its_own_corridor_and_falls_through_to_the_clear_side() {
    for xs in [[-80., 0., 80.], [-32., 0., 90.]] {
        let a = shafts(&xs);
        let p = model::plan(
            &a,
            &serde_json::from_value(json!({"sheet":{"preferred_max_size":"A0"}})).unwrap(),
        )
        .unwrap();
        let view = p.views.iter().find(|v| v.id == "v:front").unwrap();
        let centre = view.at[0] - view.scale * (a.envelope[0][0] + a.envelope[1][0]) / 2.;
        let chain: Vec<_> = p
            .dimensions
            .iter()
            .filter(|d| {
                d.measurements.iter().any(|m| {
                    ["f:turned_step:3", "f:turned_step:4"].contains(&m.feature.as_str())
                        && m.parameter == "step.length"
                })
            })
            .collect();
        assert_eq!(chain.len(), 2);
        for d in chain {
            let placed = d.placed.as_ref().unwrap();
            let x = placed.arrows[0][0][0];
            assert!(
                (x - centre).abs() / view.scale < 60.,
                "a middle body's chain must not stand beyond a sibling"
            );
            if xs[0] == -32. {
                assert!(x > centre, "the left corridor has no room: try the right");
            }
        }
    }
}
#[test]
fn an_omitted_separating_view_reports_superimposed_profiles_with_a_restoring_fix() {
    let mut a = shafts(&[-50., 50.]);
    for f in &mut a.features {
        let p = &mut f.parameters;
        let x = p["profile"]["axis_origin"][0].clone();
        p["profile"]["axis_origin"] = json!([0, x, 0]);
    }
    a.envelope = [[-15., -65., 0.], [15., 65., 40.]];
    let spec: Spec = serde_json::from_value(
        json!({"views":{"side":{"omit":true},"plan":{"omit":true},"iso":{"omit":true}}}),
    )
    .unwrap();
    let p = model::plan(&a, &spec).unwrap();
    let found: Vec<_> = p
        .problems
        .iter()
        .filter(|p| p.code == "turned_profile_superimposed")
        .collect();
    assert!(!found.is_empty());
    assert!(found.iter().all(|p| {
        p.message.contains("side")
            && p.fix
                .iter()
                .any(|op| op.path == "views/side/omit" && op.value == Some(json!(false)))
    }));
    assert_eq!(
        p.dimensions
            .iter()
            .filter(|d| d.placed.is_some()
                && d.measurements.iter().any(|m| m.parameter == "step.length"))
            .count(),
        4,
        "Policy B keeps and reports every chain"
    );
}

#[test]
fn independent_double_d_fixtures_are_owned_once_and_blind_profiles_stay_blind() {
    for (name, count) in [
        ("double-d-single-z.step", 1),
        ("double-d-roll-30.step", 1),
        ("double-d-axis-y.step", 1),
        ("double-d-blind.step", 0),
        ("double-d-round-bore.step", 0),
    ] {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/coverage-holes")
            .join(name);
        let (_, a) = model::analyse_native(&path).unwrap();
        assert_eq!(
            a.features
                .iter()
                .filter(|f| f.kind == "double_d_bore")
                .count(),
            count,
            "{name}"
        );
        let ms = marks(&a);
        assert_eq!(
            ms.iter().filter(|m| m.text.contains("DOUBLE-D")).count(),
            count,
            "{name}"
        );
        assert!(
            !lint::check(&a, &ms)
                .iter()
                .any(|i| i.code == "profiled_bore_not_dimensioned"),
            "{name}"
        );
        if count > 0 {
            assert!(
                !a.features.iter().any(|f| f.kind == "hole"),
                "a double-D must not be recounted as a round hole"
            );
        }
        if name == "double-d-blind.step" {
            assert!(
                !ms.iter()
                    .any(|m| m.text.contains("DOUBLE-D") && m.text.contains("THRU"))
            );
        }
    }
}
#[test]
fn a_real_two_face_bore_reports_only_its_unattributed_seat() {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/coverage-holes/countersink-two-face.step");
    let (_, a) = model::analyse_native(&path).unwrap();
    assert_eq!(
        a.features
            .iter()
            .filter(|f| f.kind == "countersink")
            .count(),
        2
    );
    assert_eq!(a.features.iter().filter(|f| f.kind == "hole").count(), 1);
    let findings = lint::check(&a, &marks(&a));
    let missing: Vec<_> = findings
        .iter()
        .filter(|i| {
            i.code == "hole_requirement_unverifiable"
                && i.requirements
                    .iter()
                    .any(|r| r.parameter.starts_with("countersink."))
        })
        .collect();
    // quiddity-rust 0.1.1 (accepted for alpha.13): the hole record carries no seat (its
    // `csink` is null where alpha.8 gave the 90° seat), so neither countersink's diameter nor
    // angle is attributable: 2 -> 4 findings, each still on one countersink. quiddity-rust
    // 0.2.0 attributes one seat to the hole again, leaving the other seat's two: 4 -> 2.
    assert_eq!(missing.len(), 2, "{findings:?}");
    assert!(
        missing
            .iter()
            .all(|i| i.subjects.len() == 1 && i.subjects[0].starts_with("f:countersink:"))
    );
}

#[test]
fn independent_outer_profile_evidence_retains_every_undrawn_corner_requirement() {
    for (name, count) in [
        ("profile-trapezoid.step", 2),
        ("profile-triangle.step", 3),
        ("profile-pentagon.step", 4),
    ] {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/coverage-holes")
            .join(name);
        let (_, a) = model::analyse_native(&path).unwrap();
        assert_eq!(
            lint::profile_angles::requirements(&a).len(),
            count,
            "{name}"
        );
        // Each corner is either stated by a placed angle claiming it or still reported missing:
        // nothing is credited silently (marks carry no ink, so linear definition is not read).
        let marks = marks(&a);
        let stated = lint::profile_angles::requirements(&a)
            .iter()
            .filter(|c| {
                marks.iter().any(|m| {
                    m.claims
                        .iter()
                        .any(|k| k.feature == c.feature && k.parameter == "profile_angle.angle")
                })
            })
            .count();
        let missing = lint::check(&a, &marks)
            .into_iter()
            .filter(|i| i.code == "angular_requirement_missing")
            .count();
        assert_eq!(missing, count - stated, "{name}: {stated} stated");
    }
}

#[test]
fn a_present_longitudinal_view_is_never_offered_as_a_restoration_fix() {
    let mut a = shafts(&[-50., 0., 50.]);
    for (n, at) in [[0., 0., 0.], [0., 100., 0.], [100., 0., 0.]]
        .iter()
        .enumerate()
    {
        for f in &mut a.features[2 * n..2 * n + 2] {
            f.parameters["profile"]["axis_origin"] = json!(at);
        }
    }
    a.envelope = [[-15., -15., 0.], [115., 115., 40.]];
    let p = model::plan(&a, &Spec::default()).unwrap();
    assert!(
        !p.problems
            .iter()
            .any(|p| p.code == "turned_profile_superimposed"),
        "both longitudinal views are already available"
    );
}

#[test]
fn a_body_local_corridor_preserves_an_authored_unpinned_lane() {
    let a = shafts(&[-80., 0., 80.]);
    let x = |lane| {
        let spec: Spec =
            serde_json::from_value(json!({"sheet":{"preferred_max_size":"A0"},"dimensions":{
                "d:f:turned_step:1/step.length":{"place":{"side":"left","lane":lane}}
            }}))
            .unwrap();
        model::plan(&a, &spec)
            .unwrap()
            .dimensions
            .into_iter()
            .find(|d| d.id == "d:f:turned_step:1/step.length")
            .unwrap()
            .placed
            .unwrap()
            .arrows[0][0][0]
    };
    assert!((x(0) - x(1) - draftwright::dims::LANE_NORMAL).abs() < 1e-6);
}

/// Defect E2: holes and bosses on inclined axes get a callout in the view that sees them most
/// nearly end-on, instead of none.
#[test]
fn inclined_holes_and_bosses_are_called_out_where_seen_most_nearly_end_on() {
    let (y, z) = (-0.375, 0.927); // 22° off z, as nist_ctc_04's counterbored holes
    let hole = |n: usize, x: f64| {
        feature(
            "hole",
            n,
            json!({"axis":[0,y,z],"location":[x,0,10],"diameter":6,"depth":10.8,
                "bottom":"through","cbore":null,"csink":null,"spotface":null}),
        )
    };
    let boss = feature(
        "boss",
        3,
        json!({"axis":[0,-y,-z],"location":[0,10,0],"diameter":12,"height":3}),
    );
    // As large, tilted as far but in the x–z plane: another line, so called out on its own.
    let other = feature(
        "boss",
        4,
        json!({"axis":[-y,0,-z],"location":[20,30,0],"diameter":12,"height":3}),
    );
    let a = analysis(vec![hole(1, -10.0), hole(2, 10.0), boss, other]);
    let p = model::plan(&a, &Spec::default()).unwrap();
    let callout = |label: &str| {
        p.dimensions
            .iter()
            .find(|d| d.label == label)
            .unwrap_or_else(|| panic!("{label} is called out"))
    };
    let holes = callout("2× ⌀6 THRU");
    assert_eq!(holes.view, "v:plan");
    assert!(holes.placed.is_some());
    assert_eq!(holes.measurements.len(), 2);
    let bosses: Vec<_> = p.dimensions.iter().filter(|d| d.label == "ø12").collect();
    assert_eq!(bosses.len(), 2, "{bosses:#?}");
    for boss in bosses {
        assert_eq!(boss.view, "v:plan");
        assert!(boss.placed.is_some());
        assert_eq!(boss.measurements.len(), 1);
    }
    // The holes' locations and the boss's height and location still need a view square to the
    // axis, and say so.
    let unstated: Vec<&str> = p
        .problems
        .iter()
        .filter(|q| q.code == "feature_not_dimensioned")
        .flat_map(|q| q.subjects.iter().map(String::as_str))
        .collect();
    assert_eq!(unstated, ["f:hole:1", "f:hole:2", "f:boss:3", "f:boss:4"]);
    let findings = lint::check(&a, &marks(&a));
    assert!(
        !findings
            .iter()
            .any(|i| i.code == "hole_requirement_missing" || i.code == "feature_not_dimensioned"),
        "{findings:#?}"
    );
}
