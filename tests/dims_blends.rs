//! G3: blend radii, exact occurrence credit and editable general radius notes.
#[path = "common/corpus.rs"]
mod corpus;

#[path = "common/support.rs"]
#[macro_use]
mod support;

use crate::cache;

use draftwright::items::State;
use draftwright::model::{self, Spec};
use serde_json::json;

fn missing(a: &model::Analysis, p: &model::Plan, part: &draftwright::haecceity::Part) -> usize {
    let drawing = model::render(
        part,
        a,
        p,
        &draftwright::text::Font::new(include_bytes!("../fonts/IBMPlexMono-Regular.ttf").to_vec())
            .unwrap(),
    )
    .unwrap();
    draftwright::lint::lint(a, &drawing)
        .iter()
        .filter(|i| {
            matches!(
                i.code.as_str(),
                "blend_requirement_missing" | "fillet_requirement_missing"
            )
        })
        .count()
}

// ISSUE-1433-a, ARCH0015-1: numerous equal radii use notes with one exact credit per blend.
part_cases!(many_blends_are_stated_without_counted_radius_clutter;
    cgb245 => "cadgenbench_inputs/cgb245.step",
    nist_ctc_01_asme1_rd => "nist/nist_ctc_01_asme1_rd.stp",
);
fn many_blends_are_stated_without_counted_radius_clutter(file: &str) {
    let Some(dir) = corpus::dir() else { return };

    let (part, a) = model::analyse_native(&dir.join(file)).unwrap();
    let p = model::plan(&a, &Spec::default()).unwrap();
    // v0.2.3 (#127) proves these inside rounds as fillets. They now use
    // counted radius callouts; retain exact occurrence credit and independent lint.
    let n = a
        .features
        .iter()
        .filter(|f| matches!(f.kind.as_str(), "blend" | "fillet"))
        .count();
    assert!(n > 20);
    if file.contains("cgb245") {
        assert_eq!(missing(&a, &p, &part), 0, "{file}");
        assert!(p.dimensions.iter().any(|d| {
            d.placed.is_some()
                && d.measurements
                    .iter()
                    .any(|m| m.parameter == "fillet.radius")
        }));
    } else {
        assert_eq!(
            missing(&a, &p, &part),
            0,
            "real CTC-01 radii are stated and geometric relief is excluded"
        );
        assert!(p.notes.iter().all(|n| n.placed.is_some()));
    }
    assert!(
        p.notes
            .iter()
            .all(|n| !n.text.contains("REF SITE") && !n.text.contains("UNSTATED"))
    );
    for note in p
        .notes
        .iter()
        .filter(|n| n.placed.is_some() && !n.measurements.is_empty())
    {
        for d in p.dimensions.iter().filter(|d| d.placed.is_some()) {
            assert!(!d.measurements.iter().any(|m| matches!(
                m.parameter.as_str(),
                "blend.radius" | "fillet.radius"
            ) && (m.value - note.measurements[0].value).abs()
                < 0.05));
        }
    }
    assert!(
        p.notes.iter().any(|n| n.text.contains("RADII R"))
            || p.dimensions.iter().any(|d| d.placed.is_some()
                && d.label.contains('R')
                && d.measurements
                    .iter()
                    .any(|m| m.parameter == "fillet.radius")),
        "{file}"
    );
    assert!(
        p.dimensions
            .iter()
            .all(|d| !d.label.contains("31× R") && !d.label.contains("82× R"))
    );
    let again = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(
        serde_json::to_value(&p).unwrap(),
        serde_json::to_value(again).unwrap()
    );
}

#[test]
fn ctc02_radius_exceptions_retain_credit() {
    let Some(dir) = corpus::dir() else { return };
    let (part, a) = cache::native(&dir.join("nist/nist_ctc_02_asme1_rc.stp"));
    let p = cache::default_plan(&a, false);
    assert_eq!(
        missing(&a, &p, &part),
        0,
        "CTC-02 real exceptions and defaults are stated; oblique relief is excluded"
    );
    assert!(p.notes.iter().all(|n| n.placed.is_some()));
    assert!(
        p.notes
            .iter()
            .all(|n| !n.text.contains("UNSTATED") && !n.text.contains("REF SITE"))
    );
}

/// ISSUE-1433-a: a small equal group keeps a compact leader; omission loses every credit.
#[test]
fn few_blends_share_a_radius_callout_and_lose_credit_when_hidden() {
    let Some(dir) = corpus::dir() else { return };
    let (part, a) = cache::native(&dir.join("gramel/string_post.step"));
    let p = cache::default_plan(&a, false);
    let radius = p
        .dimensions
        .iter()
        .find(|d| {
            d.measurements
                .iter()
                .any(|m| m.parameter == "fillet.radius")
        })
        .expect("fillet radius callout");
    assert_eq!(radius.label, "2× R0.25");
    assert_eq!(radius.measurements.len(), 2);
    // quiddity 0.6.0 reports these two R0.25 turned rounds (faces 7 and 14) as fillets, not
    // blends (#297); their leader evidence is the fillet's trimmed arcs.
    for f in a.features.iter().filter(|f| f.kind == "fillet") {
        let arcs = &a.radius_arcs[&f.id];
        assert!(!arcs.is_empty());
        for arc in arcs {
            for &site in &arc.tips {
                assert!(
                    f.faces.iter().any(|&face| {
                        let surface = &part.faces[face].surface;
                        let Some((u, v)) = surface.parameters(site, None) else {
                            return false;
                        };
                        let projected = surface.value(u, v);
                        part.domain(face).is_some_and(|d| d.contains(u, v))
                            && (0..3).all(|k| (site[k] - projected[k]).abs() < 1e-6)
                    }),
                    "{}'s leader evidence is outside its defining surface",
                    f.id
                );
            }
        }
    }
    assert_eq!(missing(&a, &p, &part), 0);
    let spec = model::edit(&Spec::default(), &json!([{"op":"set", "path":format!("dimensions/{}/omit", draftwright::items::key(&radius.id)), "value":true}])).unwrap();
    let hidden = model::plan(&a, &spec).unwrap();
    assert_eq!(missing(&a, &hidden, &part), 2);
}

/// ISSUE-1351-a / LINT-_registry-structured_note_satisfaction: a note is editable by stable ID;
/// only drawn exact claims count. Prose cannot replace a hidden structured note.
#[test]
fn a_general_radius_note_can_be_hidden_and_moved_without_borrowing_prose_credit() {
    let Some(dir) = corpus::dir() else { return };
    let (part, a) = cache::native(&dir.join("nist/nist_ctc_03_asme1_rc.stp"));
    let p = cache::default_plan(&a, false);
    let note = p
        .notes
        .iter()
        .find(|n| n.text.contains("RADII R"))
        .expect("general radius note");
    let path = format!("annotations/{}", draftwright::items::key(&note.id));
    let with_prose = model::edit(
        &Spec::default(),
        &json!([
            {"op":"add", "path":"notes", "value":{"text":"BREAK ALL SHARP EDGES"}}
        ]),
    )
    .unwrap();
    let mixed = model::plan(&a, &with_prose).unwrap();
    assert_eq!(missing(&a, &mixed, &part), 0);
    assert!(mixed.notes_block.as_ref().unwrap().ruled);
    assert!(!mixed.radius_notes_block.as_ref().unwrap().ruled);
    assert!(mixed.notes.iter().all(|n| n.placed.is_some()));
    // An exception leader states blends; since quiddity-rust v0.2.2 (#120) cgb245's R3 blends
    // share their leader with the R3 fillets, so the leader may state fillets too.
    let blends = |d: &draftwright::dims::PlannedDimension| {
        d.measurements
            .iter()
            .filter(|m| {
                matches!(
                    m.parameter.as_str(),
                    "blend.radius" | "fillet.radius" | "cylindrical_profile.radius"
                )
            })
            .count()
    };
    let exception = p
        .dimensions
        .iter()
        .find(|d| {
            d.placed.is_some()
                && d.measurements
                    .iter()
                    .all(|m| m.parameter == "blend.radius" || m.parameter == "fillet.radius")
                && blends(d) > 0
        })
        .unwrap();
    let less = model::edit(&Spec::default(), &json!([
        {"op":"set", "path":format!("dimensions/{}/omit", draftwright::items::key(&exception.id)), "value":true}
    ])).unwrap();
    let less = model::plan(&a, &less).unwrap();
    assert_eq!(
        missing(&a, &less, &part),
        // The exception's blends that no other drawn leader states once it is hidden (a shared
        // fillet leader's blends fall back to a leader of their own).
        exception
            .measurements
            .iter()
            .filter(|m| matches!(
                m.parameter.as_str(),
                "blend.radius" | "fillet.radius" | "cylindrical_profile.radius"
            ))
            .filter(|m| !less.dimensions.iter().any(|d| {
                d.placed.is_some() && d.measurements.iter().any(|n| n.feature == m.feature)
            }))
            .count()
            + note
                .measurements
                .iter()
                .filter(|m| matches!(
                    m.parameter.as_str(),
                    "blend.radius" | "fillet.radius" | "cylindrical_profile.radius"
                ))
                .filter(|m| !less
                    .dimensions
                    .iter()
                    .any(|d| d.placed.is_some()
                        && d.measurements.iter().any(|n| n.feature == m.feature)))
                .count()
    );
    assert!(
        less.notes
            .iter()
            .any(|n| n.id == note.id && n.placed.is_none())
    );
    let spec = model::edit(
        &Spec::default(),
        &json!([
            {"op":"set", "path":format!("{path}/omit"), "value":true},
            {"op":"add", "path":"notes", "value":{"text":note.text}}
        ]),
    )
    .unwrap();
    let hidden = model::plan(&a, &spec).unwrap();
    assert!(missing(&a, &hidden, &part) > 0);
    assert_eq!(
        hidden.notes.iter().find(|n| n.id == note.id).unwrap().state,
        State::OmittedByUser
    );
    let moved_spec = model::edit(&Spec::default(), &json!([{"op":"set", "path":format!("{path}/place"), "value":{"view":"front", "side":"above", "lane":1}}])).unwrap();
    let moved = model::plan(&a, &moved_spec).unwrap();
    let moved_note = moved.notes.iter().find(|n| n.id == note.id).unwrap();
    assert_ne!(moved_note.placed, note.placed);
    assert_eq!(missing(&a, &moved, &part), 0);
    let unplaced_spec = model::edit(
        &moved_spec,
        &json!([{"op":"set", "path":"views/front/omit", "value":true}]),
    )
    .unwrap();
    let unplaced = model::plan(&a, &unplaced_spec).unwrap();
    assert!(
        unplaced
            .notes
            .iter()
            .find(|n| n.id == note.id)
            .unwrap()
            .placed
            .is_none()
    );
    assert!(
        unplaced
            .problems
            .iter()
            .any(|p| p.code == "notes_not_placed" && p.subjects.contains(&note.id))
    );
    assert!(missing(&a, &unplaced, &part) > 0);

    let font =
        draftwright::text::Font::new(include_bytes!("../fonts/IBMPlexMono-Regular.ttf").to_vec())
            .unwrap();
    let mut drawing = model::render(&part, &a, &p, &font).unwrap();
    let index = drawing
        .annotations
        .iter()
        .position(|n| n.name == note.id)
        .unwrap();
    // The note's own blends (quiddity-rust v0.2.2: cgb245's general radius is R2, 32 blends).
    let noted = note
        .measurements
        .iter()
        .filter(|m| {
            matches!(
                m.parameter.as_str(),
                "blend.radius" | "fillet.radius" | "cylindrical_profile.radius"
            )
        })
        .count();
    // A prose-only radius/count still cannot borrow the structured note's claims.
    for text in [
        format!(
            "R{}, {noted} BLENDS; OTHER BLEND RADII UNSTATED",
            note.measurements[0].value
        ),
        format!(
            "R{}, {noted} BLENDS; REF SITE XYZ (1, 2, 3)",
            note.measurements[0].value
        ),
    ] {
        let mut unlocated = drawing.clone();
        for &run in &unlocated.annotations[index].runs {
            unlocated.text[run].text = text.clone();
        }
        assert_eq!(
            draftwright::lint::lint(&a, &unlocated)
                .iter()
                .filter(|i| matches!(
                    i.code.as_str(),
                    "blend_requirement_missing" | "fillet_requirement_missing"
                ))
                .count(),
            noted
        );
    }
    let mut annotation = serde_json::to_value(&drawing.annotations[index]).unwrap();
    assert!(annotation["claims"].as_array().unwrap().is_empty());
    let lost = annotation["satisfies"].as_array_mut().unwrap().remove(0)["feature"]
        .as_str()
        .unwrap()
        .to_string();
    drawing.annotations[index] = serde_json::from_value(annotation).unwrap();
    drawing.annotations.push(drawing.annotations[index].clone());
    let missing: Vec<_> = draftwright::lint::lint(&a, &drawing)
        .into_iter()
        .filter(|i| {
            matches!(
                i.code.as_str(),
                "blend_requirement_missing" | "fillet_requirement_missing"
            )
        })
        .collect();
    assert_eq!(
        missing.len(),
        1,
        "duplicate notes cannot borrow another occurrence's credit"
    );
    assert_eq!(missing[0].subjects, [lost]);
}

/// ISSUE-1450-a: same-face evidence decides ownership; an equal but unrelated boss survives.
#[test]
fn a_blend_owns_its_cylindrical_face_without_erasing_unrelated_equal_bosses() {
    let fixture = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts/prismatic.step");
    let (_, mut a) = cache::native(&fixture);
    let template = a.features[0].clone();
    a.features.clear();
    for (id, kind, faces, parameters) in [
        (
            "f:blend:1",
            "blend",
            vec![1],
            json!({"radius":2.0,"path":{"at":[10.0,10.0,0.0],"direction":[0.0,0.0,1.0]}}),
        ),
        (
            "f:boss:1",
            "boss",
            vec![1],
            json!({"diameter":4.0,"height":10.0,"axis":[0.0,0.0,1.0],"location":[10.0,10.0,10.0]}),
        ),
        (
            "f:boss:2",
            "boss",
            vec![2],
            json!({"diameter":4.0,"height":10.0,"axis":[0.0,0.0,1.0],"location":[20.0,10.0,10.0]}),
        ),
    ] {
        let mut f = template.clone();
        f.id = id.into();
        f.kind = kind.into();
        f.faces = faces;
        f.parameters = parameters.as_object().unwrap().clone();
        a.features.push(f);
    }
    a.envelope = [[0.0, 0.0, 0.0], [40.0, 30.0, 10.0]];
    a.levels.clear();
    a.level_supports.clear();
    a.risers.clear();
    let p = cache::default_plan(&a, false);
    assert!(
        !p.dimensions
            .iter()
            .flat_map(|d| &d.measurements)
            .any(|m| m.feature == "f:boss:1" && m.parameter == "boss.diameter")
    );
    assert!(
        p.dimensions
            .iter()
            .flat_map(|d| &d.measurements)
            .any(|m| m.feature == "f:boss:2" && m.parameter == "boss.diameter")
    );
    assert!(
        !draftwright::lint::coverage::inventory(&a)
            .iter()
            .flat_map(|(_, rs)| rs)
            .any(|r| r.feature == "f:boss:1")
    );
    a.features[1].parameters.insert("height".into(), json!(5.0));
    let short = cache::default_plan(&a, false);
    assert!(
        !short
            .dimensions
            .iter()
            .flat_map(|d| &d.measurements)
            .any(|m| m.feature == "f:boss:1" && m.parameter == "boss.diameter")
    );
    assert!(
        short
            .dimensions
            .iter()
            .flat_map(|d| &d.measurements)
            .any(|m| m.feature == "f:boss:1" && m.parameter == "boss_height.length")
    );
    a.features[1].faces.clear();
    let p = cache::default_plan(&a, false);
    assert!(
        p.dimensions
            .iter()
            .flat_map(|d| &d.measurements)
            .any(|m| m.feature == "f:boss:1" && m.parameter == "boss.diameter")
    );
}

/// A bare pin retains automatic presentation, including consolidated radius ownership.
#[test]
fn a_bare_pin_keeps_a_shared_radius_leader_and_its_default() {
    let Some(dir) = corpus::dir() else { return };
    let (part, a) = cache::native(&dir.join("cadgenbench_inputs/cgb245.step"));
    let p = cache::default_plan(&a, false);
    let shared = p
        .dimensions
        .iter()
        .find(|d| {
            let side = |concave| {
                d.measurements.iter().any(|m| {
                    m.parameter == "fillet.radius"
                        && a.features.iter().any(|f| {
                            f.id == m.feature
                                && (f.parameters.get("side").and_then(serde_json::Value::as_str)
                                    == Some("concave"))
                                    == concave
                        })
                })
            };
            side(true) && side(false)
        })
        .unwrap();
    let spec = model::edit(
        &Spec::default(),
        &json!([{
            "op":"set", "path":format!("dimensions/{}/place", draftwright::items::key(&shared.id)),
            "value":{"pinned":true}
        }]),
    )
    .unwrap();
    let pinned = model::plan(&a, &spec).unwrap();
    let held = pinned
        .dimensions
        .iter()
        .find(|d| d.id == shared.id)
        .unwrap();
    assert_eq!(held.label, shared.label);
    assert_eq!(held.placed, shared.placed);
    assert_eq!(
        serde_json::to_value(&held.measurements).unwrap(),
        serde_json::to_value(&shared.measurements).unwrap()
    );
    assert_eq!(
        pinned.dimensions.iter().map(|d| &d.id).collect::<Vec<_>>(),
        p.dimensions.iter().map(|d| &d.id).collect::<Vec<_>>()
    );
    assert_eq!(
        pinned
            .notes
            .iter()
            .map(|n| (&n.text, &n.placed))
            .collect::<Vec<_>>(),
        p.notes
            .iter()
            .map(|n| (&n.text, &n.placed))
            .collect::<Vec<_>>()
    );
    assert_eq!(missing(&a, &pinned, &part), 0);
}

/// Real corners must remain dimensioned when grouped shallow relief is excluded.
#[test]
fn ctc01_states_real_corners_and_the_radius_default_without_logo_callouts() {
    let Some(dir) = corpus::dir() else { return };
    let (part, a) = cache::native(&dir.join("nist/nist_ctc_01_asme1_rd.stp"));
    let p = cache::default_plan(&a, false);
    // 4× R25 is a placed callout in the plan view, and no radius-centre table is planned: its
    // leader is not obstructed. (On alpha.12 the 350 row pitch was witnessed from the grid's
    // left-hand column, its extension lines crossed the 4× R25 leader, and the corners were
    // tabulated; the pitch is now witnessed from the right-hand column.)
    assert!(
        p.dimensions
            .iter()
            .any(|d| d.label == "4× R25" && d.view == "v:plan" && d.placed.is_some())
    );
    assert!(p.radius_table.is_none());
    // v0.2.3 (#127): the former default's real inside rounds are now 18× R10.
    assert!(
        p.dimensions
            .iter()
            .any(|d| d.label == "18× R10" && d.placed.is_some())
    );
    assert_eq!(missing(&a, &p, &part), 0);
    let excluded: Vec<_> = a
        .features
        .iter()
        .filter(|f| {
            a.radius_reliefs
                .iter()
                .any(|r| !f.faces.is_empty() && f.faces.iter().all(|face| r.faces.contains(face)))
        })
        .map(|f| f.id.as_str())
        .collect();
    assert!(excluded.len() >= 4);
    assert!(
        !p.dimensions
            .iter()
            .flat_map(|d| &d.measurements)
            .chain(p.notes.iter().flat_map(|n| &n.measurements))
            .any(|m| excluded.contains(&m.feature.as_str()))
    );
    assert!(
        p.notes
            .iter()
            .any(|n| n.text == "SHALLOW RELIEF PROFILES PER MODEL"
                && n.placed.is_some()
                && n.measurements.is_empty())
    );
}

// The same geometric relief decision works on different carriers and preserves real radii.
part_cases!(grouped_relief_radii_are_excluded_on_several_parts_with_explicit_evidence;
    nist_ctc_01_asme1_rd => "nist/nist_ctc_01_asme1_rd.stp",
    nist_ctc_04_asme1_rd => "nist/nist_ctc_04_asme1_rd.stp",
    nist_ctc_02_asme1_rc => "nist/nist_ctc_02_asme1_rc.stp",
    nist_ftc_06_asme1_rd => "nist/nist_ftc_06_asme1_rd.stp",
    nist_ftc_08_asme1_rc => "nist/nist_ftc_08_asme1_rc.stp",
);
fn grouped_relief_radii_are_excluded_on_several_parts_with_explicit_evidence(file: &str) {
    let Some(dir) = corpus::dir() else { return };

    let (part, a) = cache::native(&dir.join(file));
    let p = cache::default_plan(&a, false);
    let evidence = serde_json::to_value(&a).unwrap();
    let reliefs = evidence["radius_reliefs"]
        .as_array()
        .expect("geometry-owned relief evidence");
    assert!(!reliefs.is_empty(), "{file}");
    let faces: Vec<_> = reliefs
        .iter()
        .flat_map(|r| r["faces"].as_array().unwrap())
        .filter_map(|v| v.as_u64())
        .collect();
    let excluded: Vec<_> = a
        .features
        .iter()
        .filter(|f| {
            matches!(f.kind.as_str(), "blend" | "fillet")
                && !f.faces.is_empty()
                && f.faces.iter().all(|f| faces.contains(&(*f as u64)))
        })
        .collect();
    assert!(excluded.len() >= 4, "{file}");
    assert!(
        !p.dimensions
            .iter()
            .flat_map(|d| &d.measurements)
            .chain(p.notes.iter().flat_map(|n| &n.measurements))
            .any(|m| excluded.iter().any(|f| f.id == m.feature)),
        "{file}: relief has no radius claims"
    );
    let drawing = model::render(
        &part,
        &a,
        &p,
        &draftwright::text::Font::new(include_bytes!("../fonts/IBMPlexMono-Regular.ttf").to_vec())
            .unwrap(),
    )
    .unwrap();
    assert!(
        !draftwright::lint::lint(&a, &drawing)
            .iter()
            .any(|i| matches!(
                i.code.as_str(),
                "blend_requirement_missing" | "fillet_requirement_missing"
            ) && excluded.iter().any(|f| i.subjects.contains(&f.id))),
        "{file}: relief is an explicit exclusion, never fabricated radius credit"
    );
    assert!(
        p.dimensions.iter().any(|d| d.placed.is_some()
            && d.measurements
                .iter()
                .any(|m| matches!(m.parameter.as_str(), "blend.radius" | "fillet.radius"))),
        "{file}: real radii survive"
    );
}

#[test]
fn real_corners_have_no_relief_exclusions() {
    let Some(dir) = corpus::dir() else { return };
    for file in ["cadgenbench_inputs/cgb245.step", "gramel/string_post.step"] {
        let (_, a) = cache::native(&dir.join(file));
        let evidence = serde_json::to_value(&a).unwrap();
        assert!(
            evidence
                .get("radius_reliefs")
                .is_none_or(|r| r.as_array().unwrap().is_empty()),
            "{file}: real corners are retained"
        );
    }
}

/// Routing cost may be reported, but cannot erase an otherwise drawable radius.
#[test]
fn radius_exceptions_are_stated_despite_routing_cost() {
    let Some(dir) = corpus::dir() else { return };
    let (_, a) = cache::native(&dir.join("nist/nist_ctc_02_asme1_rc.stp"));
    let p = cache::default_plan(&a, false);
    for label in ["4× R15", "6× R18"] {
        assert!(
            p.dimensions
                .iter()
                .any(|d| d.label == label && d.placed.is_some())
                || p.omitted.iter().any(|d| d.label == label
                    && matches!(&d.state, State::Covered { by } if by == "t:radius-centres")),
            "{label} survives routing as a callout or in a fully located table"
        );
    }
    assert!(!p.problems.iter().any(|p| p.code == "blend_callout_dropped"));
}

/// Hiding the model-governing relief note cannot apply the blanket corner radius to relief.
#[test]
fn relief_profile_note_is_required_for_the_default_and_independent_lint() {
    let Some(dir) = corpus::dir() else { return };
    let (part, a) = cache::native(&dir.join("nist/nist_ctc_01_asme1_rd.stp"));
    let p = cache::default_plan(&a, false);
    let relief = p
        .notes
        .iter()
        .find(|n| n.text == "SHALLOW RELIEF PROFILES PER MODEL")
        .expect("explicit relief profile requirement");
    let spec = model::edit(&Spec::default(), &json!([{"op":"set", "path":format!("annotations/{}/omit", draftwright::items::key(&relief.id)), "value":true}])).unwrap();
    let hidden = model::plan(&a, &spec).unwrap();
    let disposition = p
        .problems
        .iter()
        .find(|p| p.code == "relief_profiles_per_model")
        .expect("model-governed features remain traceable");
    assert_eq!(disposition.severity, "info");
    assert!(disposition.subjects.contains(&relief.id));
    for f in a.features.iter().filter(|f| {
        matches!(f.kind.as_str(), "blend" | "fillet")
            && !f.faces.is_empty()
            && a.radius_reliefs
                .iter()
                .any(|r| f.faces.iter().all(|face| r.faces.contains(face)))
    }) {
        assert!(disposition.subjects.contains(&f.id));
    }
    assert!(
        !hidden
            .problems
            .iter()
            .any(|p| p.code == "relief_profiles_per_model")
    );
    assert!(
        hidden
            .notes
            .iter()
            .filter(|n| n.text.starts_with("ALL UNSPECIFIED RADII"))
            .all(|n| n.placed.is_none())
    );
    // The explicit 18× R10 callout survives; only the model-governed relief note
    // is missing now that #127 makes the real corners fillets.
    assert_eq!(missing(&a, &hidden, &part), 0);
    assert!(
        hidden
            .dimensions
            .iter()
            .any(|d| d.placed.is_some() && d.label == "18× R10")
    );
    let font =
        draftwright::text::Font::new(include_bytes!("../fonts/IBMPlexMono-Regular.ttf").to_vec())
            .unwrap();
    let mut drawing = model::render(&part, &a, &p, &font).unwrap();
    drawing.annotations.retain(|n| n.name != relief.id);
    let lint = draftwright::lint::lint(&a, &drawing);
    assert_eq!(
        lint.iter()
            .filter(|i| matches!(
                i.code.as_str(),
                "blend_requirement_missing" | "fillet_requirement_missing"
            ))
            .count(),
        0
    );
    assert!(
        lint.iter()
            .any(|i| i.code == "relief_profile_requirement_missing")
    );
}

/// A deeper version of the same relief must keep its radius requirements. The judgement
/// depends on measured depth and topology, even when names and feature families are identical.
#[test]
fn deep_profiles_are_not_exempted_as_shallow_relief() {
    let Some(dir) = corpus::dir() else { return };
    let path = dir.join("nist/nist_ctc_01_asme1_rd.stp");
    let bytes = std::fs::read_to_string(&path).unwrap();
    let (_, a) = model::analyse_native_step(&path, bytes.as_bytes()).unwrap();
    let evidence = serde_json::to_value(&a).unwrap();
    assert!(
        !evidence["radius_reliefs"]
            .as_array()
            .expect("shallow profile evidence")
            .is_empty()
    );
    let shallow_faces: Vec<_> = a
        .radius_reliefs
        .iter()
        .flat_map(|r| r.faces.iter().copied())
        .collect();
    // Move the whole pocket floor from 398 to 388 mm; only model-space Cartesian points
    // change. Analytic cylinder axes remain valid along x, and all trim curves move with it.
    let deep = bytes.replace("CARTESIAN_POINT('',(398.,", "CARTESIAN_POINT('',(388.,");
    assert_ne!(deep, bytes);
    let (_, a) = model::analyse_native_step(&path, deep.as_bytes()).unwrap();
    let evidence = serde_json::to_value(&a).unwrap();
    assert!(
        evidence
            .get("radius_reliefs")
            .is_none_or(|r| r.as_array().unwrap().is_empty())
    );
    let p = cache::default_plan(&a, false);
    let doc = serde_json::to_value(&p).unwrap();
    // #127 changes their family to concave fillets; every physical round still
    // needs an actual radius statement once its depth defeats relief exclusion.
    let rounds: Vec<_> = a
        .features
        .iter()
        .filter(|f| {
            matches!(f.kind.as_str(), "blend" | "fillet")
                && f.faces.iter().any(|face| shallow_faces.contains(face))
        })
        .collect();
    assert!(!rounds.is_empty());
    for f in rounds {
        assert!(
            p.dimensions
                .iter()
                .any(|d| d.placed.is_some() && d.measurements.iter().any(|m| m.feature == f.id))
                || doc["radius_table"]["rows"]
                    .as_array()
                    .is_some_and(|rs| rs.iter().any(|r| r["feature"] == f.id))
        );
    }
}

// Every row locates one occurrence; changing or deleting its locator loses that occurrence
// and the dependent default. Copying another row cannot replace its exact identity.
// CTC-01 rd plans no radius table: its 4× R25 leader is not obstructed (pinned in
// ctc01_states_real_corners_and_the_radius_default_without_logo_callouts).
part_cases!(radius_centre_table_credits_every_occurrence_and_rejects_wrong_locators;
    nist_ctc_02_asme1_rc => "nist/nist_ctc_02_asme1_rc.stp",
    nist_ftc_08_asme1_rc => "nist/nist_ftc_08_asme1_rc.stp",
    nist_stc_10_asme1_ap242_e2 => "pmi/step/nist_stc_10_asme1_ap242-e2.stp.gz",
);
fn radius_centre_table_credits_every_occurrence_and_rejects_wrong_locators(file: &str) {
    let Some(dir) = corpus::dir() else { return };

    let path = if file.starts_with("pmi/") {
        std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures")
            .join(file)
    } else {
        dir.join(file)
    };
    let (part, a) = model::analyse_native(&path).unwrap();
    let spec =
        serde_json::from_value(serde_json::json!({"sheet":{"preferred_max_size":"A0"}})).unwrap();
    let p = model::plan(&a, &spec).unwrap();
    let doc = serde_json::to_value(&p).unwrap();
    if p.radius_table.is_none() {
        // v0.2.3 (#127) makes these former table-owned inside rounds fillets,
        // now stated directly by counted callouts. Exercise exact occurrence
        // credit on their actual carrier; the table path remains tested below.
        assert_eq!(missing(&a, &p, &part), 0, "{file}");
        let d = p
            .dimensions
            .iter()
            .find(|d| {
                d.placed.is_some()
                    && d.measurements
                        .iter()
                        .filter(|m| m.parameter == "fillet.radius")
                        .count()
                        >= 4
            })
            .unwrap();
        let ids: std::collections::BTreeSet<_> = d
            .measurements
            .iter()
            .filter(|m| m.parameter == "fillet.radius")
            .map(|m| m.feature.as_str())
            .collect();
        let font = draftwright::text::Font::new(
            include_bytes!("../fonts/IBMPlexMono-Regular.ttf").to_vec(),
        )
        .unwrap();
        let drawing = model::render(&part, &a, &p, &font).unwrap();
        let index = drawing
            .annotations
            .iter()
            .position(|a| a.name == d.id)
            .unwrap();
        let mut wrong = drawing.clone();
        for run in wrong.annotations[index].runs.clone() {
            wrong.text[run].text = "R99999".into();
        }
        let lost: std::collections::BTreeSet<_> = draftwright::lint::lint(&a, &wrong)
            .into_iter()
            .filter(|i| {
                matches!(
                    i.code.as_str(),
                    "fillet_requirement_missing" | "fillet_requirement_unverifiable"
                )
            })
            .flat_map(|i| i.subjects)
            .collect();
        assert!(
            ids.iter().all(|id| lost.contains(*id)),
            "{file}: misprinted radius lost exact occurrence credit"
        );
        let mut absent = drawing;
        absent.annotations.remove(index);
        let lost: std::collections::BTreeSet<_> = draftwright::lint::lint(&a, &absent)
            .into_iter()
            .filter(|i| {
                matches!(
                    i.code.as_str(),
                    "fillet_requirement_missing" | "fillet_requirement_unverifiable"
                )
            })
            .flat_map(|i| i.subjects)
            .collect();
        assert!(
            ids.iter().all(|id| lost.contains(*id)),
            "{file}: absent carrier cannot borrow credit"
        );
        return;
    }
    let table = doc["radius_table"].as_object().unwrap();
    let rows = table["rows"].as_array().unwrap();
    // 0.5.4 listed four blends (R0.17, R0.344, R1.027, R1.321). quiddity 0.6.0 reports the R0.344
    // round (f232) as a fillet at any radius (#297), stated by its own callout, so three remain.
    assert_eq!(rows.len(), 3, "{file}");
    assert!(table["table"].get("placed").is_some());
    let table_features: Vec<_> = rows
        .iter()
        .map(|r| r["feature"].as_str().unwrap())
        .collect();
    let covered: Vec<_> = rows
        .iter()
        .filter(|r| {
            p.omitted.iter().any(|d| {
                d.id == r["dimension"].as_str().unwrap()
                    && matches!(&d.state, State::Covered { by } if by == "t:radius-centres")
            })
        })
        .map(|r| r["feature"].as_str().unwrap())
        .collect();
    assert_eq!(table_features.len(), covered.len());
    assert!(
        covered
            .iter()
            .all(|f| table_features.iter().filter(|g| *g == f).count() == 1)
    );
    assert_eq!(missing(&a, &p, &part), 0, "{file}");
    let font =
        draftwright::text::Font::new(include_bytes!("../fonts/IBMPlexMono-Regular.ttf").to_vec())
            .unwrap();
    let drawing = model::render(&part, &a, &p, &font).unwrap();
    let index = drawing
        .annotations
        .iter()
        .position(|n| n.name.starts_with("t:radius-centres/"))
        .unwrap();
    let default_count = p
        .notes
        .iter()
        .filter(|n| n.placed.is_some())
        .flat_map(|n| &n.measurements)
        .filter(|m| m.parameter == "blend.radius")
        .count();
    let mut wrong = drawing.clone();
    let x = wrong.annotations[index].runs[1];
    wrong.text[x].text = "99999".into();
    assert_eq!(
        draftwright::lint::lint(&a, &wrong)
            .iter()
            .filter(|i| matches!(
                i.code.as_str(),
                "blend_requirement_missing" | "fillet_requirement_missing"
            ))
            .count(),
        1 + default_count,
        "{file}: wrong centre cannot state the radius"
    );
    let mut lost = drawing.clone();
    lost.annotations.remove(index);
    lost.annotations.push(
        lost.annotations
            .iter()
            .find(|n| n.name.starts_with("t:radius-centres/"))
            .unwrap()
            .clone(),
    );
    assert_eq!(
        draftwright::lint::lint(&a, &lost)
            .iter()
            .filter(|i| matches!(
                i.code.as_str(),
                "blend_requirement_missing" | "fillet_requirement_missing"
            ))
            .count(),
        1 + default_count,
        "{file}: duplicate row cannot borrow occurrence credit"
    );
    let restored: model::Analysis =
        serde_json::from_value(serde_json::to_value(&a).unwrap()).unwrap();
    assert_eq!(
        serde_json::to_value(model::plan(&restored, &spec).unwrap()).unwrap(),
        doc
    );
}

// A blend on an oblique axis has no arc in any principal view, so no leader of its own. An equal
// fillet leader's count states it, so the general radius note is placed instead of withdrawn;
// hiding that leader leaves the exception undrawn and the note withdrawn again, as an error.
// Since quiddity-rust v0.2.2 (#120) most oblique rounds are fillets with a unit-vector axis,
// counted the same way (cgb207, FTC-07 and CTC-03's oblique blends became fillets, and the first
// two lost their general note with them).
part_cases!(oblique_radius_exceptions_join_an_equal_fillet_leader;
    cgb245 => "cadgenbench_inputs/cgb245.step",
    nist_ctc_03_asme1_rc => "nist/nist_ctc_03_asme1_rc.stp",
    nist_ftc_08_asme1_rc => "nist/nist_ftc_08_asme1_rc.stp",
);

fn oblique_radius_exceptions_join_an_equal_fillet_leader(file: &str) {
    let Some(dir) = corpus::dir() else { return };
    let (part, a) = cache::native(&dir.join(file));
    let p = cache::default_plan(&a, false);
    let withdrawn = |p: &model::Plan| p.problems.iter().any(|x| x.code == "notes_not_placed");
    assert!(!withdrawn(&p), "{file}: {:#?}", p.problems);
    let note = p
        .notes
        .iter()
        .find(|n| n.text.starts_with("ALL UNSPECIFIED RADII") && n.placed.is_some());
    let general = note.map(|n| n.measurements[0].value);
    // The exceptions: blends or fillets of another radius whose axis is along no principal
    // direction.
    let oblique: Vec<&draftwright::bridge::Feature> = a
        .features
        .iter()
        .filter(|f| {
            let axis = match f.kind.as_str() {
                "blend" => f
                    .parameters
                    .get("path")
                    .and_then(|p| p["direction"].as_array()),
                "fillet" => f
                    .parameters
                    .get("axis")
                    .and_then(serde_json::Value::as_array),
                _ => None,
            };
            f.parameters
                .get("radius")
                .and_then(serde_json::Value::as_f64)
                != general
                && axis.is_some_and(|d| {
                    d.iter()
                        .filter(|c| c.as_f64().unwrap().abs() > 1e-6)
                        .count()
                        > 1
                })
        })
        .collect();
    assert!(!oblique.is_empty(), "{file}");
    for f in &oblique {
        let leader = p
            .dimensions
            .iter()
            .find(|d| d.placed.is_some() && d.measurements.iter().any(|m| m.feature == f.id))
            .unwrap_or_else(|| panic!("{file}: {} is not stated", f.id));
        assert!(
            leader
                .measurements
                .iter()
                .any(|m| m.parameter == "fillet.radius"),
            "{file}: {}",
            leader.label
        );
        assert_eq!(Some(leader.value), f.parameters["radius"].as_f64());
        assert_eq!(
            leader.label,
            format!(
                "{}× R{}",
                leader.measurements.len(),
                p.sheet.units.format(leader.value)
            )
        );
        assert!(
            !p.problems
                .iter()
                .any(|x| x.code == "feature_not_dimensioned" && x.subjects == [f.id.clone()])
        );
    }
    assert_eq!(missing(&a, &p, &part), 0, "{file}");
    // Hiding the leader leaves the exception undrawn: the note is withdrawn, as an error.
    let leader = p
        .dimensions
        .iter()
        .find(|d| d.measurements.iter().any(|m| m.feature == oblique[0].id))
        .unwrap();
    let spec = model::edit(&Spec::default(), &json!([{"op":"set", "path":format!("dimensions/{}/omit", draftwright::items::key(&leader.id)), "value":true}])).unwrap();
    let hidden = model::plan(&a, &spec).unwrap();
    if note.is_some() {
        assert!(withdrawn(&hidden), "{file}");
    } else {
        assert!(
            hidden
                .notes
                .iter()
                .all(|n| !n.text.starts_with("ALL UNSPECIFIED RADII"))
        );
    }
    assert!(missing(&a, &hidden, &part) > 0, "{file}");
}
