//! #32: applying the proposed fixes for a leader crossing re-spaces the dimension stack. The
//! editor's spindle with its reviewer's PMI (`tests/fixtures/samples/spindle-pmi.step.gz`: g6 on
//! Ø15, datums A and B, position on the Ø4 cross hole, heights from A) has datum B's leader
//! crossing the 52 and 22 heights, and a fix proposed for each. Both applied, as the editor
//! applies them (Session `apply`, committed), used to put 52 and 22 on one offset line with the
//! 52's text stranded at the sheet's top left, while the problem count dropped. Since #33 the
//! heights are a baseline from datum A, so the leader crosses 52 and the 34-from-A rung instead.
//!
//! The checks here read the plan's ink independently of the engine's own lint helpers.

use std::io::Read as _;

use draftwright::{lint, model, session::Session};

fn session() -> Session {
    let path = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/samples/spindle-pmi.step.gz"
    );
    let mut bytes = Vec::new();
    flate2::read::GzDecoder::new(std::fs::File::open(path).unwrap())
        .read_to_end(&mut bytes)
        .unwrap();
    let (part, analysis) =
        model::analyse_native_step(std::path::Path::new("spindle.step"), &bytes).unwrap();
    // Keep this repair regression's deliberately crossed starting condition. Automatic
    // datums now fit inside the dimension lanes; an explicit hand placement beyond them
    // must still expose the same two crossings and offer the same dimension-stack fixes.
    let (items, _) = draftwright::pmi_lowering::lower(&analysis, &|_| true);
    let b = items
        .iter()
        .find(|g| {
            matches!(&g.glyph,
        draftwright::pmi_lowering::Glyph::Datum { letter, .. } if letter == "B")
        })
        .unwrap();
    let a = items
        .iter()
        .find(|g| {
            matches!(&g.glyph,
        draftwright::pmi_lowering::Glyph::Datum { letter, .. } if letter == "A")
        })
        .unwrap();
    // The cross hole's position frame keeps its leader in the side view, as it had when this
    // case was written: since #224 it attaches to the hole's callout where the view's area can
    // grow into vacant paper, which the starting layout has and the relaid one does not.
    let position = items
        .iter()
        .find(|g| g.id.starts_with("pmi:geometric_tolerance.position:f7:"))
        .unwrap();
    let spec = serde_json::from_value(serde_json::json!({
        "annotations": {
            b.id.clone(): { "place": { "view": "front", "side": "right", "lane": 0 } },
            a.id.clone(): { "place": { "view": "front", "side": "below", "lane": 0 } },
            position.id.clone(): { "place": { "view": "side", "side": "above" } }
        },
        "dimensions": {
            "d:envelope/height.length": { "place": { "side": "right", "lane": 1 } },
            "d:f:turned_step:f6/step_position.length": { "place": { "side": "right", "lane": 0 } }
        }
    }))
    .unwrap();
    Session::new(part, analysis, spec).unwrap()
}

type Line = [[f64; 2]; 2];

/// A linear dimension's dimension line: the axis it runs along, its offset across, the span
/// between its extension lines and the line's extent.
fn dimension_line(lines: &[Line]) -> Option<(usize, f64, [f64; 2], [f64; 2])> {
    let [e0, e1, d] = lines else { return None };
    let k = if (d[0][1] - d[1][1]).abs() < 1e-6 {
        0
    } else {
        1
    };
    let (a, b) = (e0[0][k], e1[0][k]);
    Some((
        k,
        d[0][1 - k],
        [a.min(b), a.max(b)],
        [d[0][k].min(d[1][k]), d[0][k].max(d[1][k])],
    ))
}

/// The leader crossings on datum B, and the edits that move each crossed dimension to the left
/// of the front view, with the constraints that keep the sheet (the form the engine's local
/// fixes take). The engine no longer proposes these: each trades more findings for the crossing
/// than it clears, so they are applied here explicitly, as an agent could request them. The
/// tests below are about how such edits are applied and laid out.
fn b_crossings(s: &Session) -> (Vec<String>, Vec<draftwright::items::Op>) {
    use draftwright::items::Op;
    let crossings: Vec<_> = s
        .plan()
        .problems
        .iter()
        .filter(|p| {
            p.code == "annotation_leader_crossing"
                && p.subjects.first().is_some_and(|d| d.ends_with("t=B"))
        })
        .collect();
    let plan = s.plan();
    let ops = crossings
        .iter()
        .flat_map(|p| {
            let id = &p.subjects[1];
            [
                Op::set(
                    format!("dimensions/{}/place", draftwright::items::key(id)),
                    serde_json::json!({"side": "left", "view": "front"}),
                ),
                Op::set("sheet/keep_layout", serde_json::json!(true)),
                Op::set("sheet/size", serde_json::json!(plan.sheet.size)),
                Op::set("sheet/scale", serde_json::json!(plan.sheet.scale)),
                Op::set("sheets/max", serde_json::json!(1)),
            ]
        })
        .collect();
    (
        crossings.iter().map(|p| p.subjects[1].clone()).collect(),
        ops,
    )
}

#[test]
fn applying_both_proposed_fixes_keeps_the_stack_apart_and_text_on_its_lines() {
    let mut s = session();
    let (moved, ops) = b_crossings(&s);
    assert_eq!(
        moved,
        [
            "d:envelope/height.length",
            "d:f:turned_step:f6/step_position.length"
        ],
        "the review sample's two crossings"
    );
    assert_eq!(ops.len(), 10); // one placement and four intent constraints per fix
    assert_eq!(
        (&*s.plan().sheet.size, &*s.plan().sheet.scale),
        ("A4", "1:1")
    );
    let sheet_before = layout(s.plan());
    for p in s.plan().problems.iter().filter(|p| {
        moved.iter().any(|id| p.subjects.contains(id)) && p.code == "annotation_leader_crossing"
    }) {
        // Only the hole-table sheet remedy is offered: every local move trades as many
        // findings as it clears, or more.
        assert!(
            p.offered_fixes()
                .iter()
                .all(|f| f.scope() != draftwright::fixes::Scope::Local),
            "{p:?}"
        );
    }
    let errors = |s: &Session| -> Vec<String> {
        lint::structural::check(s.drawing())
            .into_iter()
            .filter(|i| i.severity == lint::Severity::Error)
            .map(|i| i.id)
            .collect()
    };
    let lint_before = errors(&s);
    let mut replay = s.state().clone();
    let delta = s.apply(&ops, true).unwrap();
    assert_eq!(
        layout(s.plan())["sheets"],
        sheet_before["sheets"],
        "#126 keeps paper, scale and count"
    );
    let moved_views: Vec<_> = layout(s.plan())["views"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|v| !sheet_before["views"].as_array().unwrap().contains(v))
        .map(|v| v[0].as_str().unwrap().to_owned())
        .collect();
    assert!(
        !moved_views.is_empty(),
        "the boolean does not store page frames"
    );
    let notice = delta
        .notices
        .iter()
        .find(|p| p.code == "layout_replanned")
        .unwrap();
    for id in moved_views {
        assert!(notice.subjects.contains(&id), "{id} is reported");
    }
    assert_eq!(draftwright::wording::carries_id(&notice.message), None);
    assert!(notice.message.contains("front view"));
    assert!(notice.message.contains("side view"));
    for view in &s.plan().views {
        let old = sheet_before["views"]
            .as_array()
            .unwrap()
            .iter()
            .find(|v| v[0] == view.id)
            .unwrap();
        assert_eq!(
            serde_json::json!([view.sheet, view.scale, view.direction, view.up]),
            serde_json::json!([old[1], old[3], old[4], old[5]])
        );
    }
    // #126 alternatives can change with the selected paper without changing a
    // finding. Their PlanDelta metadata must still replay exactly over the wire.
    let wire: draftwright::session::Delta =
        serde_json::from_slice(&serde_json::to_vec(&delta).unwrap()).unwrap();
    wire.apply_to(&mut replay).unwrap();
    assert_eq!(
        serde_json::to_value(&replay).unwrap(),
        serde_json::to_value(s.state()).unwrap()
    );

    // The datum-B crossings are gone, and nothing hard is added in their place.
    assert!(b_crossings(&s).0.is_empty());
    let added: Vec<_> = delta
        .problems_added
        .iter()
        .filter(|p| p.severity == "error" || p.severity == "warning")
        .map(|p| format!("{} {:?}", p.code, p.subjects))
        .collect();
    assert!(added.is_empty(), "new findings: {added:?}");
    let new_lint: Vec<_> = errors(&s)
        .into_iter()
        .filter(|i| !lint_before.contains(i))
        .collect();
    assert!(new_lint.is_empty(), "new lint errors: {new_lint:?}");

    let linear: Vec<_> = s
        .plan()
        .dimensions
        .iter()
        .filter(|d| d.kind == "linear")
        .filter_map(|d| Some((d.id.as_str(), d.placed.as_ref()?)))
        .collect();
    for id in &moved {
        assert!(linear.iter().any(|(d, _)| d == id), "{id} is drawn");
    }
    // No two dimensions on one offset line over overlapping spans.
    for (i, (a, pa)) in linear.iter().enumerate() {
        for (b, pb) in &linear[i + 1..] {
            let (Some(la), Some(lb)) = (dimension_line(&pa.lines), dimension_line(&pb.lines))
            else {
                continue;
            };
            let overlap = la.2[1].min(lb.2[1]) - la.2[0].max(lb.2[0]);
            assert!(
                la.0 != lb.0 || (la.1 - lb.1).abs() >= 0.5 || overlap <= 0.5,
                "{a} and {b} share the offset line {}",
                la.1
            );
        }
    }
    // Every dimension's text within its normal distance of its own line: 1 mm across it (LIFT)
    // and, beyond a line run on to it, 1 mm past its end; a text height is the bound.
    for (id, p) in &linear {
        let Some((k, offset, _, extent)) = dimension_line(&p.lines) else {
            continue;
        };
        let b = p.label_box;
        let along = (extent[0] - b[k + 2]).max(b[k] - extent[1]).max(0.0);
        let across = (b[1 - k] - offset).max(offset - b[3 - k]).max(0.0);
        assert!(
            along.hypot(across) <= 3.0,
            "{id}'s text is {along:.1} mm along and {across:.1} mm across from its line"
        );
    }
}

/// The fix checks: an edit that still puts two dimensions on one line (both pinned to lane 0,
/// which the stack does not re-space) is reported as a new hard finding, not as having resolved
/// the crossings.
#[test]
fn a_shared_line_left_by_an_edit_is_a_new_error() {
    let mut s = session();
    let (_, mut ops) = b_crossings(&s);
    for op in ops.iter_mut().filter(|op| op.path.ends_with("/place")) {
        let mut v = serde_json::to_value(&*op).unwrap();
        v["value"]["pinned"] = serde_json::json!(true);
        // Construct the conflicting edit explicitly: repair offers leave
        // lanes automatic so that applying several remedies can compose.
        v["value"]["lane"] = serde_json::json!(0);
        v["value"].as_object_mut().unwrap().remove("along");
        *op = serde_json::from_value(v).unwrap();
    }
    let delta = s.apply(&ops, true).unwrap();
    let shared: Vec<_> = delta
        .problems_added
        .iter()
        .filter(|p| p.code == "dimension_line_shared")
        .collect();
    assert_eq!(shared.len(), 1, "{:?}", delta.problems_added);
    assert_eq!(shared[0].severity, "error");
    assert!(
        lint::structural::check(s.drawing())
            .iter()
            .any(|i| i.code == "dimension_line_shared" && i.severity == lint::Severity::Error)
    );
}

#[path = "common/corpus.rs"]
mod corpus;

/// #126: geometry-only CTC-01 at the reported authored paper/scale. These are
/// candidates just like the existing annotation fixes, not full preview plans.
#[test]
fn ctc01_crossings_offer_local_edits_and_identify_the_sheet_remedy() {
    let Some(root) = corpus::dir() else { return };
    let (part, analysis) =
        model::analyse_native(&root.join("nist/nist_ctc_01_asme1_rd.stp")).unwrap();
    let spec =
        serde_json::from_value(serde_json::json!({"sheet":{"size":"A3","scale":"1:5"}})).unwrap();
    let plan = model::plan_recovered(&analysis, &spec).unwrap();
    assert_eq!((&*plan.sheet.size, &*plan.sheet.scale), ("A3", "1:5"));
    let crossings: Vec<_> = plan
        .problems
        .iter()
        .filter(|p| {
            matches!(
                p.code.as_str(),
                "annotation_leader_crossing" | "extension_through_feature"
            )
        })
        .collect();
    // CTC-01's 350 row pitch is witnessed from the grid's right-hand column, so no leader
    // crosses its extension lines (two did on alpha.12, when it was witnessed from the left).
    // The extension crossings below still exercise the remedies.
    assert_eq!(
        crossings
            .iter()
            .filter(|p| p.code == "annotation_leader_crossing")
            .count(),
        0
    );
    assert_eq!(
        crossings
            .iter()
            .filter(|p| p.code == "extension_through_feature")
            .count(),
        9
    );
    // CTC-01 plans no radius-centre table (its 4× R25 leader is not obstructed), so no
    // dimension carries one. A crossing's first offer is a local edit when one leaves fewer
    // findings than it clears; the other seven (the front-view hole location's five among them)
    // have only local edits that trade as many findings as they clear, so keep just the sheet
    // remedy. The rest of this test checks the table-carrier contract for a plan that has the table.
    if plan.radius_table.is_none() {
        let mut local = 0;
        for p in &crossings {
            let fixes = p.offered_fixes();
            assert_eq!(p.fix, fixes[0].ops);
            if fixes[0].scope() == draftwright::fixes::Scope::Local {
                local += 1;
            } else {
                assert!(
                    fixes
                        .iter()
                        .all(|f| f.scope() == draftwright::fixes::Scope::Sheet),
                    "{}",
                    p.id
                );
            }
        }
        assert_eq!(local, 2);
        return;
    }
    // Table-carrier edits cannot cheaply prove that automatic radius-centre
    // coordinates survive; those offers must be honest sheet fixes. Only the
    // front-view location has a verified local carrier here.
    let mut session = Session::new(part, analysis.clone(), spec.clone()).unwrap();
    let before_layout = layout(&plan);
    let mut tried = Vec::new();
    let mut applied = 0;
    for p in &crossings {
        let fixes = p.offered_fixes();
        assert!(!fixes.is_empty(), "{}", p.id);
        assert_eq!(p.fix, fixes[0].ops);
        let local = fixes
            .iter()
            .find(|f| f.scope() == draftwright::fixes::Scope::Local);
        if p.subjects[0] != "d:f:hole:f73/location.length.z" {
            assert!(
                local.is_none(),
                "table-carrier change cannot promise retention: {p:?}"
            );
            assert!(
                fixes[0]
                    .description()
                    .contains("The whole sheet is laid out again")
            );
            continue;
        }
        let fix = local.unwrap();
        assert!(fix.description().contains("Trade-off:"));
        assert!(fix.description().contains("extension_through_feature"));
        assert!(fix.description().contains("⌀25 hole"));
        assert!(fix.description().contains("40 wide slot"));
        if tried.contains(&fix.ops) {
            continue;
        }
        tried.push(fix.ops.clone());
        let delta = session.apply(&fix.ops, false).unwrap();
        let new_crossings: Vec<_> = delta
            .problems_added
            .iter()
            .filter(|p| p.code == "extension_through_feature")
            .collect();
        assert_eq!(new_crossings.len(), 5);
        // Every newly visible finding, of any code, must have been disclosed.
        for added in &delta.problems_added {
            assert!(
                fix.description().contains(&format!("[{}]", added.code)),
                "undisclosed code: {added:?}"
            );
            assert!(
                fix.description().contains(&added.message),
                "undisclosed finding: {added:?}"
            );
        }
        session.apply(&fix.ops, true).unwrap();
        applied += 1;
    }
    assert_eq!(applied, 1);
    assert_eq!(layout(session.plan())["sheets"], before_layout["sheets"]);
    let unresolved = crossings
        .iter()
        .filter(|old| {
            session
                .plan()
                .problems
                .iter()
                .any(|p| p.code == old.code && p.subjects == old.subjects)
        })
        .count();
    assert_eq!(unresolved, 6, "five original crossings are cleared");
    assert_eq!(
        session
            .plan()
            .problems
            .iter()
            .filter(|p| matches!(
                p.code.as_str(),
                "annotation_leader_crossing" | "extension_through_feature"
            ))
            .count(),
        11,
        "five disclosed replacements keep the total at eleven"
    );
    assert_eq!(
        session.plan().radius_table.as_ref().unwrap().rows,
        plan.radius_table.as_ref().unwrap().rows,
        "all radius-centre coordinates remain on the drawing"
    );
    eprintln!(
        "CTC-01: one local edit clears five original crossings and adds five disclosed replacements; eleven remain. Plan-view alternatives are sheet scope."
    );
    // Re-run the review's two compatible edits together. The second is now
    // explicitly sheet scope, and both alternatives disclose the replacement
    // crossings. Combining them does not turn a sheet offer into a local promise.
    session.undo().unwrap();
    let hole = crossings
        .iter()
        .find(|p| p.subjects[0] == "d:f:hole:f73/location.length.z")
        .unwrap()
        .offered_fixes()[0]
        .clone();
    let slot = crossings
        .iter()
        .find(|p| p.subjects[0] == "d:f:slot:f8/slot_length.length")
        .unwrap()
        .offered_fixes()[0]
        .clone();
    assert_eq!(hole.scope(), draftwright::fixes::Scope::Local);
    assert_eq!(slot.scope(), draftwright::fixes::Scope::Sheet);
    assert!(slot.description().contains("extension_through_feature"));
    assert!(slot.description().contains("⌀35 hole"));
    let description = format!("{} {}", hole.description(), slot.description());
    let together: Vec<_> = hole.ops.into_iter().chain(slot.ops).collect();
    let delta = session.apply(&together, true).unwrap();
    assert_eq!(
        delta
            .problems_added
            .iter()
            .filter(|p| p.code == "extension_through_feature")
            .count(),
        6
    );
    for added in &delta.problems_added {
        assert!(
            description.contains(&format!("[{}]", added.code)),
            "undisclosed code: {added:?}"
        );
        assert!(
            description.contains(&added.message),
            "undisclosed finding: {added:?}"
        );
    }
    assert_eq!(
        crossings
            .iter()
            .filter(|old| session
                .plan()
                .problems
                .iter()
                .any(|p| p.code == old.code && p.subjects == old.subjects))
            .count(),
        5
    );
    assert_eq!(
        session
            .plan()
            .problems
            .iter()
            .filter(|p| matches!(
                p.code.as_str(),
                "annotation_leader_crossing" | "extension_through_feature"
            ))
            .count(),
        11
    );
    assert_eq!(layout(session.plan())["sheets"], before_layout["sheets"]);
    assert_eq!(
        session.plan().radius_table.as_ref().unwrap().rows,
        plan.radius_table.as_ref().unwrap().rows
    );
    let remedies = plan
        .problems
        .iter()
        .find(|p| p.code == "annotation_leader_crossing")
        .unwrap()
        .offered_fixes();
    let sheet = remedies
        .iter()
        .find(|f| f.ops.iter().any(|op| op.path == "hole_table"))
        .unwrap();
    assert_eq!(sheet.scope(), draftwright::fixes::Scope::Sheet);
    let description = sheet.description();
    assert!(
        description.contains("The whole sheet is laid out again")
            && description.contains("hole table")
            && description.contains("1:10"),
        "{description}"
    );
}

/// #138: pins retain relative drawing intent, rather than absolute page coordinates,
/// when a sheet-scale remedy rebuilds their canonical frame.
#[test]
fn changing_sheet_scale_can_move_a_hand_placed_callout() {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts/mounting_plate.step");
    let (_, analysis) = model::analyse_native(&path).unwrap();
    let base = model::plan_recovered(&analysis, &Default::default()).unwrap();
    let callout = base
        .dimensions
        .iter()
        .find(|d| d.kind == "callout" && d.placed.is_some())
        .unwrap();
    let mut spec: model::Spec = serde_json::from_value(serde_json::json!({
        "sheet": { "scale": "1:1" },
        "dimensions": { &callout.id: { "place": {
            "view": callout.view.trim_start_matches("v:"), "offset": [12.0, 12.0], "pinned": true
        } } }
    }))
    .unwrap();
    let before = model::plan_recovered(&analysis, &spec).unwrap();
    spec.sheet.scale = Some("1:2".into());
    let after = model::plan_recovered(&analysis, &spec).unwrap();
    let position = |p: &model::Plan| {
        p.dimensions
            .iter()
            .find(|d| d.id == callout.id)
            .unwrap()
            .placed
            .as_ref()
            .unwrap()
            .label_at
    };
    assert_ne!(position(&before), position(&after));
    assert_eq!(
        before.spec.dimensions[&callout.id].place,
        after.spec.dimensions[&callout.id].place
    );
}

/// What the local promise protects, independently of the engine's fit and recovery scores.
fn layout(p: &model::Plan) -> serde_json::Value {
    serde_json::json!({
        "sheets": std::iter::once(&p.sheet).chain(&p.sheets).map(|s| (&s.size, &s.scale, s.page)).collect::<Vec<_>>(),
        "views": p.views.iter().map(|v| (&v.id, v.sheet, v.at, v.scale, v.direction, v.up)).collect::<Vec<_>>()
    })
}

#[test]
fn unguarded_legacy_edits_report_a_changed_sheet_and_preview_does_not_commit() {
    let mut s = session();
    let (_, mut ops) = b_crossings(&s);
    ops.retain(|op| {
        !matches!(
            op.path.as_str(),
            "sheet/keep_layout" | "sheet/size" | "sheet/scale" | "sheets/max"
        )
    });
    let before = layout(s.plan());
    let preview = s.apply(&ops, false).unwrap();
    assert_eq!(layout(s.plan()), before);
    assert!(preview.notices.iter().any(|p| p.code == "layout_replanned"));
    let delta = s.apply(&ops, true).unwrap();
    assert_ne!(layout(s.plan()), before);
    assert_eq!(
        (&*s.plan().sheet.size, &*s.plan().sheet.scale),
        ("A3", "2:1")
    );
    assert!(delta.notices.iter().any(|p| p.code == "layout_replanned"));
    let mut replay = session().state().clone();
    delta.apply_to(&mut replay).unwrap();
    assert_eq!(
        serde_json::to_value(&replay).unwrap(),
        serde_json::to_value(s.state()).unwrap()
    );
    let rebuilt = model::plan_recovered(s.analysis(), s.spec()).unwrap();
    assert_eq!(
        serde_json::to_value(s.plan()).unwrap(),
        serde_json::to_value(rebuilt).unwrap()
    );
}

#[test]
fn a_retained_layout_refuses_conflicting_sheet_intent() {
    let mut s = session();
    let (_, mut ops) = b_crossings(&s);
    ops.push(draftwright::items::Op::set("sheet/scale", "1:2".into()));
    let before = layout(s.plan());
    let error = s.apply(&ops, true).unwrap_err();
    assert!(
        error
            .to_string()
            .contains("cannot keep the retained sheet layout")
    );
    assert_eq!(layout(s.plan()), before);
}

#[test]
fn a_stale_local_offer_cannot_restore_an_old_sheet() {
    let mut s = session();
    let (_, offered) = b_crossings(&s);
    let sheet_edit = vec![
        draftwright::items::Op::set("sheet/size", "A3".into()),
        draftwright::items::Op::set("sheet/scale", "1:1".into()),
    ];
    s.apply(&sheet_edit, true).unwrap();
    let before = layout(s.plan());
    assert!(s.apply(&offered, true).is_err());
    assert_eq!(layout(s.plan()), before);
}

#[test]
fn local_intent_is_boolean_and_missing_information_refuses_preview_and_commit() {
    let mut s = session();
    let (_, mut ops) = b_crossings(&s);
    for op in ops.iter().filter(|op| op.path == "sheet/keep_layout") {
        assert_eq!(op.value, Some(serde_json::json!(true)));
    }
    let shown = s
        .plan()
        .dimensions
        .iter()
        .find(|d| d.placed.is_some())
        .unwrap()
        .id
        .clone();
    ops.push(draftwright::items::Op::set(
        format!("dimensions/{}/omit", draftwright::items::key(&shown)),
        serde_json::json!(true),
    ));
    let before = serde_json::to_value(s.state()).unwrap();
    for commit in [false, true] {
        let error = s.apply(&ops, commit).unwrap_err();
        assert!(
            error.to_string().contains("No local fix is available"),
            "{error}"
        );
        assert_eq!(serde_json::to_value(s.state()).unwrap(), before);
    }
    let old_frames = serde_json::json!([{"op":"set","path":"sheet/keep_layout",
        "value":{"size":"A4","scale":"1:1","views":{},"required":[]}}]);
    assert!(
        model::edit(s.spec(), &old_frames).is_err(),
        "page snapshots are not Spec intent"
    );
}

#[test]
fn an_explicit_sheet_edit_releases_an_inherited_local_promise() {
    let mut s = session();
    let (_, ops) = b_crossings(&s);
    s.apply(&ops, true).unwrap();
    assert_eq!(s.spec().sheet.keep_layout, Some(true));
    let retained = s.spec().clone();
    let ops = vec![draftwright::items::Op::set("sheet/scale", "1:2".into())];
    let edited = model::edit(&retained, &serde_json::to_value(&ops).unwrap()).unwrap();
    assert_eq!(edited.sheet.keep_layout, None);
    let preview = s.apply(&ops, false).unwrap();
    assert_eq!(s.spec(), &retained);
    assert!(preview.notices.iter().any(|p| p.code == "layout_replanned"));
    let delta = s.apply(&ops, true).unwrap();
    assert_eq!(s.spec().sheet.keep_layout, None);
    assert_eq!(s.plan().sheet.scale, "1:2");
    assert_eq!(delta.notices, preview.notices);
    let rebuilt = model::plan_recovered(s.analysis(), &edited).unwrap();
    assert_eq!(
        serde_json::to_value(&rebuilt).unwrap(),
        serde_json::to_value(s.plan()).unwrap()
    );
}
