//! #226: on the finished plan, a callout leader ending on an edge its view shows hidden is
//! `leader_to_hidden_edge`, and a long leader running across its own view, crossing more of its
//! lines than a leader leaving the other way would, is `leader_across_view`.
#[path = "common/corpus.rs"]
mod corpus;

use draftwright::model::{self, Plan, Spec};

/// The plan of corpus part *file*, or None (skipped) when the corpus is missing.
fn plan(file: &str) -> Option<Plan> {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED (corpus part): the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return None;
    };
    let (_, analysis) = model::analyse_native(&dir.join(file)).unwrap();
    Some(model::plan_recovered(&analysis, &Spec::default()).unwrap())
}

/// The subjects of each *code* finding.
fn found(plan: &Plan, code: &str) -> Vec<Vec<String>> {
    plan.problems
        .iter()
        .filter(|p| p.code == code)
        .map(|p| {
            assert_eq!(p.severity, "warning", "{}", p.message);
            p.subjects.clone()
        })
        .collect()
}

#[test]
fn leaders_across_their_own_view_are_reported() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED (corpus part): the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let (_, mut analysis) =
        model::analyse_native(&dir.join("cadgenbench_inputs/cgb245.step")).unwrap();
    // Its two obround slots are congruent, so one slot callout would state them: the second is
    // split from the callout, so the first keeps the radius leader this test reads (the split
    // one is dimensioned by its end centres, with no radius).
    corpus::split_slot(&mut analysis, "f:slot:f21");
    let automatic = model::plan_recovered(&analysis, &Spec::default()).unwrap();
    // Local slot witnesses now let the automatic callout stay beside its slot.
    // Keep testing the diagnostic with a deliberately authored long route.
    assert!(found(&automatic, "leader_across_view").is_empty());
    assert!(
        automatic
            .dimensions
            .iter()
            .any(|d| d.id == "d:f:slot:f13/slot_end_radius.radius" && d.placed.is_some())
    );
    let spec = model::edit(
        &Spec::default(),
        &serde_json::json!([{
            "op":"set", "path":"dimensions/d:f:slot:f13~1slot_end_radius.radius/place",
            "value":{"view":"plan", "side":"above", "lane":0, "along":"middle", "pinned":true}
        }]),
    )
    .unwrap();
    let p = model::plan_recovered(&analysis, &spec).unwrap();
    let mut across = found(&p, "leader_across_view");
    across.sort();
    assert_eq!(across, [["d:f:slot:f13/slot_end_radius.radius", "v:plan"]]);
    for d in p
        .dimensions
        .iter()
        .filter(|d| d.id.contains("slot_end_radius"))
    {
        let s = d.placed.as_ref().unwrap().lines[0];
        assert!((s[1][0] - s[0][0]).hypot(s[1][1] - s[0][1]) >= 15.0);
    }
    assert!(found(&automatic, "leader_to_hidden_edge").is_empty());
}

#[test]
fn leaders_ending_on_hidden_edges_are_reported() {
    // GRM-03's ⌀1.6 blind hole opens on the head's far face, so the side view, looking at the
    // head end-on, shows it dashed;
    // string post's flat is under its flange in the plan.
    for (file, id, view) in [
        (
            "gramel/GRM-03_thumbwheel_drive_screw.step",
            "d:f:hole:1/bore.diameter",
            "v:side",
        ),
        (
            "gramel/string_post.step",
            "d:f:flat:f11/flat.length",
            "v:plan",
        ),
    ] {
        let Some(p) = plan(file) else {
            return;
        };
        assert_eq!(found(&p, "leader_to_hidden_edge"), [[id, view]], "{file}");
    }
}

#[test]
fn ordinary_leaders_are_not_reported() {
    let Some(p) = plan("ttt_inputs/sm-hanger.step") else {
        return;
    };
    assert!(p.dimensions.iter().any(|d| d.kind == "callout"));
    for code in ["leader_to_hidden_edge", "leader_across_view"] {
        assert!(found(&p, code).is_empty(), "{code}");
    }
}
