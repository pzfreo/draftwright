use std::collections::BTreeMap;
use std::path::Path;
use std::sync::LazyLock;

use draftwright::{
    bridge::holes,
    hole_table::Setting,
    lint,
    model::{self, Analysis, Spec},
    pmi::{Field, Reading},
    text::Font,
};
use serde_json::json;

#[derive(Clone, Copy)]
enum Mouth {
    Plain,
    Chamfer,
    Counterbore,
    Spotface,
}

impl Mouth {
    fn name(self) -> &'static str {
        match self {
            Self::Plain => "plain",
            Self::Chamfer => "chamfer",
            Self::Counterbore => "counterbore",
            Self::Spotface => "spotface",
        }
    }

    fn bore(self) -> f64 {
        match self {
            Self::Plain => 8.,
            Self::Chamfer => 7.2,
            Self::Counterbore | Self::Spotface => 6.,
        }
    }

    fn uses_floor(self) -> bool {
        matches!(self, Self::Counterbore | Self::Spotface)
    }

    fn prepare(self, a: &mut Analysis) {
        for hole in a
            .features
            .iter_mut()
            .filter(|f| f.kind == "hole" && f.parameters["diameter"] == json!(3.))
        {
            hole.parameters["depth"] = json!(self.bore());
            match self {
                Self::Plain => {}
                Self::Chamfer => {
                    hole.parameters["location"][2] = json!(14.2);
                    hole.parameters.insert(
                        "mouth_chamfers".into(),
                        json!([{"axis":[0.,0.,-1.], "angle":45., "depth":0.8,
                                "diameter":3., "size":0.8}]),
                    );
                }
                Self::Counterbore | Self::Spotface => {
                    let kind = if matches!(self, Self::Spotface) {
                        "spotface"
                    } else {
                        "cbore"
                    };
                    hole.parameters
                        .insert(kind.into(), json!({"diameter":5., "depth":2.}));
                }
            }
        }
    }
}

fn fixture() -> &'static (draftwright::haecceity::Part, Analysis, Font) {
    static FIXTURE: LazyLock<(draftwright::haecceity::Part, Analysis, Font)> =
        LazyLock::new(|| {
            let root = Path::new(env!("CARGO_MANIFEST_DIR"));
            let (part, analysis) = model::analyse_native(
                &root.join("tests/fixtures/pmi/electronics-enclosure-base-depth-v2.step.gz"),
            )
            .unwrap();
            let font = Font::open(&root.join("fonts").join(draftwright::dims::FONT)).unwrap();
            (part, analysis, font)
        });
    &FIXTURE
}

#[test]
fn authored_blind_depth_case_table() {
    case_table(Setting::Never);
}

#[test]
fn authored_blind_depth_schedule_case_table() {
    case_table(Setting::Always);
}

fn case_table(setting: Setting) {
    let (part, original, font) = fixture();
    let spec = Spec {
        hole_table: setting,
        ..Spec::default()
    };
    let scheduled = setting == Setting::Always;
    let mut cases = BTreeMap::new();
    for mouth in [
        Mouth::Plain,
        Mouth::Chamfer,
        Mouth::Counterbore,
        Mouth::Spotface,
    ] {
        let mut sample = original.clone();
        mouth.prepare(&mut sample);
        for (datum, distance) in [("surface", 8.), ("floor", mouth.bore())] {
            let wrong = if datum == "surface" && mouth.uses_floor() {
                -2.
            } else if distance == 8. {
                -0.8
            } else {
                8. - distance
            };
            for (name, noise, delta) in [
                ("exact", 0., 0.),
                ("0.0004", 0., 0.0004),
                ("0.006", 0., 0.006),
                ("0.02", 0., 0.02),
                ("off_endpoint_0.00001", 0.00001, 0.),
                ("off_endpoint_0.0004", 0.0004, 0.),
                ("off_endpoint_0.006", 0.006, 0.),
                ("off_endpoint_0.02", 0.02, 0.),
                ("rounded_8.2875", 0.2875, 0.29),
                ("exact_8.2875", 0.2875, 0.2875),
                ("wrong", 0., wrong),
                ("conflict", 0., 0.),
                ("reference", 0., 0.),
                ("basic", 0., 0.),
                ("specify", 0., 0.),
                ("specify_conveyed", 0., 0.),
            ] {
                if name == "specify_conveyed" && !(mouth.uses_floor() && datum == "surface") {
                    continue;
                }
                let mut a = sample.clone();
                if name.ends_with("8.2875") && datum == "surface" && matches!(mouth, Mouth::Plain) {
                    for hole in a
                        .features
                        .iter_mut()
                        .filter(|f| f.kind == "hole" && f.parameters["diameter"] == json!(3.))
                    {
                        hole.parameters["depth"] = json!(8.29);
                        hole.parameters["location"][2] = json!(15.2875);
                    }
                } else if name.ends_with("8.2875") {
                    continue;
                }
                let hole = holes::read(&a)
                    .into_iter()
                    .filter_map(|(_, h)| h.ok())
                    .find(|h| h.diameter == 3.)
                    .unwrap();
                let native_depth = hole.depth_from_mouth().unwrap();
                let authored_face = hole.feature.faces[0];
                let Some(Reading::Read(report)) = &mut a.pmi else {
                    panic!("missing AP242 fixture records")
                };
                let r = report
                    .records
                    .iter_mut()
                    .find(|r| r.part21_id == 2620)
                    .unwrap();
                let source = r.source_id.clone();
                let nominal = distance + delta;
                r.value = Some(nominal);
                r.reference = name == "reference";
                r.basic = name == "basic";
                let specify = name.starts_with("specify");
                let toleranced = !r.reference && !r.basic && !specify;
                r.upper_tol = toleranced.then_some(0.20);
                r.lower_tol = toleranced.then_some(-0.20);
                r.label = if toleranced {
                    format!("{nominal:.4} ±0.20")
                } else {
                    format!("{nominal}")
                };
                r.authored_depth_label = Some(r.label.clone());
                let g = r.geometry.as_mut().unwrap();
                for h in &mut g.hole_depths {
                    assert_eq!(h.ends[0][2], 15.);
                    h.ends[0][2] = 7. + distance + noise;
                }
                for p in g.stations.iter_mut().chain(&mut g.sites) {
                    if p[2] == 15. {
                        p[2] = 7. + distance + noise;
                    }
                }
                let label = r.label.clone();
                if specify {
                    r.fields.push((
                        "specify origin".into(),
                        Field::Text("general_tolerance".into()),
                    ));
                    g.sites.clear();
                    if name == "specify_conveyed" {
                        r.faces = vec![authored_face];
                    }
                } else if name == "conflict" {
                    let mut other = r.clone();
                    other.source_id = "dimension:#90000".into();
                    other.part21_id = 90000;
                    other.upper_tol = Some(0.10);
                    other.lower_tol = Some(-0.10);
                    other.label = format!("{nominal:.4} ±0.10");
                    other.authored_depth_label = Some(other.label.clone());
                    other.geometry.as_mut().unwrap().hole_depths[0].ends[0][2] -= 1e-12;
                    report.records.push(other);
                }
                let id = report.identities().id(&source).unwrap().to_owned();
                let plan = if scheduled {
                    model::plan_escalated(&a, &spec).unwrap()
                } else {
                    model::plan(&a, &spec).unwrap()
                };
                let drawing = model::render(part, &a, &plan, font).unwrap();
                let case = format!("{}/{datum}/{name}", mouth.name());
                let authored: Vec<_> = plan
                    .dimensions
                    .iter()
                    .filter(|d| {
                        d.measurements.iter().any(|m| m.feature == id) || d.basis.contains(&source)
                    })
                    .collect();
                let findings = lint::lint(&a, &drawing);

                // Outcomes observed on main 3a7a8eda with the same record and rendered drawing.
                let owns = match mouth {
                    Mouth::Plain => true,
                    Mouth::Chamfer => datum == "surface",
                    Mouth::Counterbore | Mouth::Spotface => datum == "floor",
                } && !matches!(name, "basic" | "reference" | "off_endpoint_0.02");
                let conflict = owns && name == "conflict";
                let mismatch =
                    owns && matches!(name, "0.0004" | "0.006" | "0.02" | "wrong" | "exact_8.2875");
                let floor_note = match mouth {
                    Mouth::Counterbore => Some("FROM CBORE FLOOR"),
                    Mouth::Spotface => Some("FROM SF FLOOR"),
                    Mouth::Plain | Mouth::Chamfer => None,
                };
                if scheduled {
                    let rows: Vec<_> = plan
                        .hole_tables
                        .iter()
                        .flat_map(|t| &t.rows)
                        .filter(|r| r.cells[1].starts_with("⌀3 "))
                        .collect();
                    assert_eq!(rows.len(), 4, "{case}");
                    if owns && !conflict {
                        let expected = match floor_note {
                            Some(note) => format!("↧ {label} {note} ⌴"),
                            None => format!("↧ {label}"),
                        };
                        assert!(
                            rows.iter().all(|r| {
                                r.cells[1].contains(&expected)
                                    && (floor_note.is_some() || !r.cells[1].contains("FROM "))
                            }),
                            "{case}: {:?}",
                            rows.iter().map(|r| &r.cells[1]).collect::<Vec<_>>()
                        );
                    }
                } else {
                    assert_eq!(authored.len(), usize::from(!conflict), "{case}");
                    if let Some(d) = authored.first() {
                        assert!(d.placed.is_some(), "{case}");
                        if owns {
                            assert_eq!(d.kind, "callout", "{case}");
                            let flat = d.label.split_whitespace().collect::<Vec<_>>().join(" ");
                            match floor_note {
                                Some(note) => {
                                    assert!(
                                        flat.contains(&format!("↧ {label} {note} ⌴"))
                                            && d.label.contains(note),
                                        "{case}: {}",
                                        d.label
                                    );
                                }
                                None => {
                                    assert!(
                                        d.label.contains(&format!("↧ {label}"))
                                            && !d.label.contains("FROM "),
                                        "{case}: {}",
                                        d.label
                                    );
                                }
                            }
                        } else if name == "specify_conveyed" {
                            assert_eq!(d.kind, "callout", "{case}");
                            assert!(
                                d.label.contains(&format!("↧ {native_depth}"))
                                    && !d.label.contains("FROM "),
                                "{case}: {}",
                                d.label
                            );
                            assert!(d.measurements.iter().all(|m| m.feature != id), "{case}");
                        } else {
                            assert_eq!(d.kind, "linear", "{case}");
                            assert_eq!(d.label, label, "{case}");
                            assert_eq!(d.id.starts_with("d:specify_general:"), specify, "{case}");
                        }
                    }
                }
                let plan_depth: Vec<_> = plan
                    .problems
                    .iter()
                    .filter(|p| p.code == "pmi_hole_depth_mismatch")
                    .collect();
                let legacy_depth = if name == "exact_8.2875" {
                    8.29
                } else if mouth.uses_floor() {
                    6.
                } else {
                    8.
                };
                let lint_depth: Vec<_> = findings
                    .iter()
                    .filter(|p| p.requirements.iter().any(|r| r.parameter == "bore.depth"))
                    .collect();
                assert_eq!(
                    plan_depth.len(),
                    usize::from(mismatch),
                    "{case}: {plan_depth:?}"
                );
                assert_eq!(
                    plan.problems
                        .iter()
                        .filter(|p| p
                            .message
                            .contains("conflicting AP242 hole depth requirements"))
                        .count(),
                    if conflict { 2 } else { 0 },
                    "{case}"
                );
                assert!(
                    findings.iter().all(|p| p.code != "pmi_value_mismatch"),
                    "{case}"
                );
                if !scheduled {
                    assert_eq!(
                        lint_depth.len(),
                        usize::from(mismatch),
                        "{case}: {lint_depth:?}"
                    );
                    if mismatch {
                        let legacy_depth = if name == "exact_8.2875" {
                            8.29
                        } else if mouth.uses_floor() {
                            6.
                        } else {
                            8.
                        };
                        assert!(
                            plan_depth[0]
                                .message
                                .contains(&format!("recognised depth {legacy_depth} mm")),
                            "{case}: {plan_depth:?}"
                        );
                        assert_eq!(
                            lint_depth[0].code, "hole_requirement_unverifiable",
                            "{case}"
                        );
                        assert!(
                            lint_depth[0]
                                .message
                                .ends_with(&format!("does not state {legacy_depth}")),
                            "{case}: {lint_depth:?}"
                        );
                    }
                    assert_eq!(
                        findings
                            .iter()
                            .filter(|p| p.code == "pmi_not_rendered")
                            .count(),
                        if conflict { 2 } else { 0 },
                        "{case}"
                    );
                } else {
                    assert_eq!(
                        lint_depth.len(),
                        usize::from(mismatch),
                        "{case}: {lint_depth:?}"
                    );
                    if mismatch {
                        assert_eq!(lint_depth[0].code, "hole_requirement_missing", "{case}");
                        assert!(
                            lint_depth[0]
                                .message
                                .contains(&format!("bore.depth ({legacy_depth})")),
                            "{case}: {lint_depth:?}"
                        );
                    }
                    assert_eq!(
                        findings
                            .iter()
                            .filter(|p| p.code == "pmi_not_rendered")
                            .count(),
                        if conflict || mismatch { 2 } else { 0 },
                        "{case}",
                    );
                }
                let native: Vec<_> = plan
                    .dimensions
                    .iter()
                    .filter(|d| d.kind == "callout" && d.label.contains("4× ⌀3"))
                    .map(|d| &d.label)
                    .collect();
                cases.insert(
                    case,
                    json!({
                        "authored": authored.iter()
                            .map(|d| json!([d.kind, d.label, d.placed.is_some(), d.id, d.basis]))
                            .collect::<Vec<_>>(),
                        "native": native,
                        "rows": plan.hole_tables.iter().flat_map(|t| &t.rows)
                            .map(|row| (row.feature.as_str(), row.cells.as_slice()))
                            .collect::<Vec<_>>(),
                        "problems": plan.problems, "lint": findings,
                    }),
                );
            }
        }
    }
    assert_eq!(cases.len(), 108);
    dump_cases(if scheduled { "schedule" } else { "callout" }, &cases);
}

fn dump_cases(name: &str, cases: &BTreeMap<String, serde_json::Value>) {
    if let Some(path) = std::env::var_os("DRAFTWRIGHT_PMI_DEPTH_CASES") {
        let path = Path::new(&path).join(format!("{name}.json"));
        std::fs::write(path, serde_json::to_vec_pretty(cases).unwrap()).unwrap();
    }
}

#[test]
fn authored_floor_depth_survives_the_hole_schedule() {
    let (part, original, font) = fixture();
    let mut cases = BTreeMap::new();
    for (kind, nominal) in [
        ("counterbore", 6.),
        ("stepped", 6.),
        ("spotface", 6.),
        ("countersink", 6.),
        ("chamfered_counterbore", 6.4),
    ] {
        let mut a = original.clone();
        if kind == "spotface" {
            Mouth::Spotface.prepare(&mut a);
        } else {
            Mouth::Counterbore.prepare(&mut a);
        }
        for hole in a
            .features
            .iter_mut()
            .filter(|f| f.kind == "hole" && f.parameters["diameter"] == json!(3.))
        {
            match kind {
                "stepped" => {
                    hole.parameters["cbore"]["depth"] = json!(1.);
                    hole.parameters
                        .insert("outer_steps".into(), json!([{"diameter":6., "depth":1.}]));
                }
                "countersink" => {
                    hole.parameters.remove("cbore");
                    hole.parameters["location"][2] = json!(13.);
                    hole.parameters.insert("csink".into(), json!({"axis":[0.,0.,-1.],"location":[hole.parameters["location"][0],hole.parameters["location"][1],15.],"depth":2.,"major_diameter":7.,"included_angle":90.}));
                }
                "chamfered_counterbore" => {
                    hole.parameters.insert("mouth_chamfers".into(), json!([{"axis":[0.,0.,-1.],"angle":45.,"depth":0.4,"diameter":5.,"size":0.4}]));
                }
                _ => {}
            }
        }
        let Some(Reading::Read(report)) = &mut a.pmi else {
            unreachable!()
        };
        let r = report
            .records
            .iter_mut()
            .find(|r| r.part21_id == 2620)
            .unwrap();
        r.value = Some(nominal);
        r.upper_tol = Some(0.2);
        r.lower_tol = Some(-0.2);
        let label = format!("{nominal:.2} ±0.20");
        r.authored_depth_label = Some(label.clone());
        for span in &mut r.geometry.as_mut().unwrap().hole_depths {
            span.ends[0][2] = 13.;
        }
        let spec = Spec {
            hole_table: Setting::Always,
            ..Spec::default()
        };
        let plan = model::plan_escalated(&a, &spec).unwrap();
        let drawing = model::render(part, &a, &plan, font).unwrap();
        let expected_cell = match kind {
            "counterbore" | "stepped" | "chamfered_counterbore" => {
                format!("↧ {label} FROM CBORE FLOOR ⌴")
            }
            "spotface" => format!("↧ {label} FROM SF FLOOR ⌴SF"),
            "countersink" => format!("↧ {label} ⌵"),
            _ => unreachable!(),
        };
        let rows: Vec<_> = lint::marks(&drawing)
            .into_iter()
            .filter(|m| {
                m.name.starts_with("hole_schedule_")
                    && m.name.ends_with("/size")
                    && m.text.contains(&expected_cell)
                    && (kind != "countersink" || !m.text.contains("FROM "))
            })
            .collect();
        assert_eq!(rows.len(), 4, "{kind}: {:#?}", plan.hole_tables);
        let findings = lint::lint(&a, &drawing);
        let failures: Vec<_> = findings
            .iter()
            .filter(|p| {
                p.requirements.iter().any(|r| {
                    r.parameter == "bore.depth"
                        && rows
                            .iter()
                            .any(|m| m.claims.iter().any(|c| c.feature == r.feature))
                }) || (p.code == "pmi_not_rendered"
                    && p.sources.iter().any(|s| s == "dimension:#2620"))
            })
            .collect();
        let consumer =
            draftwright::evaluation::drawing_consumer(&a, Ok(std::slice::from_ref(&drawing)));
        let supported = consumer
            .holes
            .iter()
            .filter(|h| {
                h.state == draftwright::evaluation::State::Supported
                    && rows
                        .iter()
                        .any(|m| m.claims.iter().any(|c| c.feature == h.feature))
            })
            .count();
        assert_eq!((failures.len(), supported), (0, 4), "{kind}: {failures:?}");
        cases.insert(
            kind.into(),
            json!({"problems":plan.problems,"lint":findings}),
        );
    }
    dump_cases("schedule-exact", &cases);
}

#[test]
fn a_fitted_source_thread_keeps_its_authored_drill_depth() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/pmi/step/string_post_tapped.step.gz");
    let (part, mut a) = model::analyse_native(&path).unwrap();
    let tap = a
        .features
        .iter_mut()
        .find(|f| f.id == "f:hole:f17")
        .unwrap();
    tap.parameters["csink"] = json!({"axis":[0.,0.,1.],"location":[0.,0.,-0.8],"depth":0.8,"major_diameter":3.2,"included_angle":90.});
    let spec: Spec =
        serde_json::from_value(json!({"features":{"f:hole:f17":{"fit":"H11"}}})).unwrap();
    let Some(Reading::Read(template)) = &fixture().1.pmi else {
        unreachable!()
    };
    let source_depth = template
        .records
        .iter()
        .find(|r| r.part21_id == 2620)
        .unwrap();
    let Some(Reading::Read(report)) = &mut a.pmi else {
        unreachable!()
    };
    let thread = report.records.iter().find(|r| r.part21_id == 3157).unwrap();
    let cylinder = thread.geometry.as_ref().unwrap().cylinders[0].clone();
    let mut depth = source_depth.clone();
    depth.geometry.as_mut().unwrap().hole_depths = vec![draftwright::pmi_lowering::HoleDepth {
        cylinder,
        ends: [[0., 0., 0.], [0., 0., 4.]],
    }];
    depth.faces = vec![17, 19];
    for (value, mismatch) in [(4., false), (3.5, true)] {
        let mut analysis = a.clone();
        let Some(Reading::Read(report)) = &mut analysis.pmi else {
            unreachable!()
        };
        depth.value = Some(value);
        depth.lower_tol = Some(-0.2);
        depth.upper_tol = Some(0.2);
        depth.label = format!("{value:.2} ±0.20");
        depth.authored_depth_label = Some(depth.label.clone());
        report.records.push(depth.clone());
        let plan = model::plan(&analysis, &spec).unwrap();
        let d = plan
            .dimensions
            .iter()
            .find(|d| d.id == "d:f:hole:f17/bore.diameter")
            .unwrap();
        let label = d.label.split_whitespace().collect::<Vec<_>>().join(" ");
        assert_eq!(
            label,
            format!("M2-6H ↧ 2.5 ⌀1.6 H11 ↧ {value:.2} ±0.20 ⌵ ⌀3.2 × 90° FAR SIDE")
        );
        assert!(
            d.measurements
                .iter()
                .any(|m| m.parameter == "bore.depth" && m.value == value)
        );
        assert_eq!(
            plan.problems
                .iter()
                .any(|p| p.code == "pmi_hole_depth_mismatch"),
            mismatch
        );
        let drawing = model::render(&part, &analysis, &plan, &fixture().2).unwrap();
        let findings = lint::lint(&analysis, &drawing);
        assert_eq!(
            findings
                .iter()
                .any(|i| i.code == "hole_requirement_unverifiable"
                    && i.requirements.iter().any(|r| r.parameter == "bore.depth")),
            mismatch,
            "{findings:?}"
        );
    }
}

fn enclosure_with_floor_depth(kind: &str) -> Analysis {
    let mut a = fixture().1.clone();
    match kind {
        "spotface" => Mouth::Spotface.prepare(&mut a),
        "counterbore" | "stepped" => Mouth::Counterbore.prepare(&mut a),
        _ => unreachable!(),
    }
    if kind == "stepped" {
        for hole in a
            .features
            .iter_mut()
            .filter(|f| f.kind == "hole" && f.parameters["diameter"] == json!(3.))
        {
            hole.parameters["cbore"]["depth"] = json!(1.);
            hole.parameters
                .insert("outer_steps".into(), json!([{"diameter":6., "depth":1.}]));
        }
    }
    let Some(Reading::Read(report)) = &mut a.pmi else {
        unreachable!()
    };
    let r = report
        .records
        .iter_mut()
        .find(|r| r.part21_id == 2620)
        .unwrap();
    r.value = Some(6.);
    r.upper_tol = Some(0.2);
    r.lower_tol = Some(-0.2);
    r.label = "6.00 ±0.20".into();
    r.authored_depth_label = Some(r.label.clone());
    for span in &mut r.geometry.as_mut().unwrap().hole_depths {
        span.ends[0][2] = 13.;
    }
    a
}

#[test]
fn authored_spotface_floor_depth_prints_from_sf_floor() {
    let (part, _, font) = fixture();
    let a = enclosure_with_floor_depth("spotface");
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let d = plan
        .dimensions
        .iter()
        .find(|d| d.kind == "callout" && d.label.contains("4× ⌀3"))
        .unwrap();
    let flat = d.label.split_whitespace().collect::<Vec<_>>().join(" ");
    assert_eq!(flat, "4× ⌀3 H11 ↧ 6.00 ±0.20 FROM SF FLOOR ⌴SF ⌀5 ↧ 2");
    assert!(d.label.contains("FROM SF FLOOR"), "{}", d.label);
    let drawing = model::render(part, &a, &plan, font).unwrap();
    let findings = lint::lint(&a, &drawing);
    assert!(
        findings
            .iter()
            .all(|f| !f.code.starts_with("hole_requirement_") && f.code != "pmi_not_rendered"),
        "{findings:?}"
    );
}

#[test]
fn authored_stepped_counterbore_floor_depth_prints_from_cbore_floor() {
    let (part, _, font) = fixture();
    let a = enclosure_with_floor_depth("stepped");
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let d = plan
        .dimensions
        .iter()
        .find(|d| d.kind == "callout" && d.label.contains("4× ⌀3"))
        .unwrap();
    let flat = d.label.split_whitespace().collect::<Vec<_>>().join(" ");
    assert_eq!(
        flat,
        "4× ⌀3 H11 ↧ 6.00 ±0.20 FROM CBORE FLOOR ⌴ ⌀5 ↧ 2 ⌴ ⌀6 ↧ 1"
    );
    assert!(d.label.contains("FROM CBORE FLOOR"), "{}", d.label);
    let drawing = model::render(part, &a, &plan, font).unwrap();
    let findings = lint::lint(&a, &drawing);
    assert!(
        findings
            .iter()
            .all(|f| !f.code.starts_with("hole_requirement_") && f.code != "pmi_not_rendered"),
        "{findings:?}"
    );
}

#[test]
fn authored_floor_depth_table_cell_prints_the_same_note() {
    let (part, _, font) = fixture();
    let spec = Spec {
        hole_table: Setting::Always,
        ..Spec::default()
    };
    for (kind, expected) in [
        (
            "counterbore",
            "⌀3 H11 ↧ 6.00 ±0.20 FROM CBORE FLOOR ⌴ ⌀5 ↧ 2",
        ),
        ("spotface", "⌀3 H11 ↧ 6.00 ±0.20 FROM SF FLOOR ⌴SF ⌀5 ↧ 2"),
        (
            "stepped",
            "⌀3 H11 ↧ 6.00 ±0.20 FROM CBORE FLOOR ⌴ ⌀5 ↧ 2 ⌴ ⌀6 ↧ 1",
        ),
    ] {
        let a = enclosure_with_floor_depth(kind);
        let plan = model::plan_escalated(&a, &spec).unwrap();
        let rows: Vec<_> = plan
            .hole_tables
            .iter()
            .flat_map(|t| &t.rows)
            .filter(|r| r.cells[1].starts_with("⌀3 "))
            .collect();
        assert_eq!(rows.len(), 4, "{kind}");
        for r in &rows {
            assert_eq!(r.cells[1], expected, "{kind}");
        }
        let drawing = model::render(part, &a, &plan, font).unwrap();
        let findings = lint::lint(&a, &drawing);
        assert!(
            findings
                .iter()
                .all(|f| !f.code.starts_with("hole_requirement_") && f.code != "pmi_not_rendered"),
            "{kind}: {findings:?}"
        );
    }
}

#[test]
fn authored_depth_under_spotfaced_counterbore_attaches_with_from_cbore_floor() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts/blind_recess_depths/spotface.step.gz");
    let (part, mut a) = model::analyse_native(&path).unwrap();
    let hole = holes::read(&a)[0].1.clone().unwrap();
    let entry = [0, 1, 2].map(|k| hole.at[k] + 5. * hole.direction[k]);
    let floor = [0, 1, 2].map(|k| entry[k] + 15. * hole.direction[k]);
    let Some(Reading::Read(template)) = &fixture().1.pmi else {
        unreachable!()
    };
    let mut report = template.clone();
    report.records.retain(|r| r.part21_id == 2620);
    let r = &mut report.records[0];
    let source = r.source_id.clone();
    r.value = Some(15.);
    r.upper_tol = Some(0.2);
    r.lower_tol = Some(-0.2);
    r.label = "15.00 ±0.20".into();
    r.authored_depth_label = Some(r.label.clone());
    r.faces = hole.feature.faces.clone();
    let g = r.geometry.as_mut().unwrap();
    g.axis = Some("Z".into());
    g.stations = vec![entry, floor];
    g.sites = vec![entry, floor];
    g.hole_depths = vec![draftwright::pmi_lowering::HoleDepth {
        cylinder: draftwright::pmi_lowering::Cylinder {
            origin: [entry[0], entry[1], 0.],
            direction: [0., 0., 1.],
            radius: hole.diameter / 2.,
            interval: [entry[2].min(floor[2]), entry[2].max(floor[2])],
            internal: true,
        },
        ends: [entry, floor],
    }];
    a.pmi = Some(Reading::Read(report));

    let expected = "⌀6 ↧ 15.00 ±0.20 FROM CBORE FLOOR ⌴ ⌀12 ↧ 4.25 ⌴SF ⌀18 ↧ 0.75";
    let font = &fixture().2;

    let plan = model::plan(&a, &Spec::default()).unwrap();
    let authored: Vec<_> = plan
        .dimensions
        .iter()
        .filter(|d| d.basis.contains(&source))
        .collect();
    assert_eq!(authored.len(), 1, "{authored:?}");
    assert_eq!(authored[0].kind, "callout", "{authored:?}");
    let flat = authored[0]
        .label
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");
    assert_eq!(flat, expected);
    assert!(
        authored[0].label.contains("FROM CBORE FLOOR"),
        "{}",
        authored[0].label
    );
    let drawing = model::render(&part, &a, &plan, font).unwrap();
    let findings = lint::lint(&a, &drawing);
    assert!(
        findings
            .iter()
            .all(|f| !f.code.starts_with("hole_requirement_") && f.code != "pmi_not_rendered"),
        "{findings:?}"
    );

    let spec = Spec {
        hole_table: Setting::Always,
        ..Spec::default()
    };
    let plan = model::plan_escalated(&a, &spec).unwrap();
    assert!(
        plan.dimensions
            .iter()
            .all(|d| !d.basis.contains(&source) || d.kind == "callout"),
        "{:#?}",
        plan.dimensions
    );
    let rows: Vec<_> = plan
        .hole_tables
        .iter()
        .flat_map(|t| &t.rows)
        .filter(|r| r.cells[1].starts_with("⌀6 "))
        .collect();
    assert_eq!(rows.len(), 1, "{:#?}", plan.hole_tables);
    assert_eq!(rows[0].cells[1], expected);
    let drawing = model::render(&part, &a, &plan, font).unwrap();
    let findings = lint::lint(&a, &drawing);
    assert!(
        findings
            .iter()
            .all(|f| !f.code.starts_with("hole_requirement_") && f.code != "pmi_not_rendered"),
        "{findings:?}"
    );
}

#[test]
fn reference_style_callout_keeps_floor_note_whole_and_within_width() {
    let font = &fixture().2;
    for (kind, note) in [
        ("counterbore", "FROM CBORE FLOOR"),
        ("stepped", "FROM CBORE FLOOR"),
        ("spotface", "FROM SF FLOOR"),
    ] {
        let a = enclosure_with_floor_depth(kind);
        let base = model::plan(&a, &Spec::default()).unwrap();
        let id = base
            .dimensions
            .iter()
            .find(|d| d.kind == "callout" && d.label.contains("4× ⌀3"))
            .unwrap()
            .id
            .clone();
        let spec: Spec =
            serde_json::from_value(json!({"dimensions": {&id: {"style": "reference"}}})).unwrap();
        let plan = model::plan(&a, &spec).unwrap();
        let d = plan.dimensions.iter().find(|d| d.id == id).unwrap();
        assert!(d.reference, "{kind}: {}", d.label);
        assert!(
            d.label.starts_with('(') && d.label.ends_with(')'),
            "{kind}: {}",
            d.label
        );
        assert!(d.label.contains(note), "{kind}: {}", d.label);
        let placed = d.placed.as_ref().unwrap();
        let box_width = placed.label_box[2] - placed.label_box[0];
        assert!(
            box_width <= draftwright::callouts::WIDTH,
            "{kind}: label_box width {box_width:.2} > {}: {}",
            draftwright::callouts::WIDTH,
            d.label
        );
        let ink = draftwright::dims::label_ink(&d.terms, font);
        let ink_width = ink.max[0] - ink.min[0];
        assert!(
            ink_width <= draftwright::callouts::WIDTH,
            "{kind}: ink width {ink_width:.2} > {}: {:?}",
            draftwright::callouts::WIDTH,
            d.terms
        );
    }
}
