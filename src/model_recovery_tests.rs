//! Replay the view-conflict gate independently of improvements to the lane solver.
use super::*;
use std::io::Read;

fn recorded(name: &str) -> Plan {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/sheet-policy")
        .join(name);
    let mut bytes = Vec::new();
    flate2::read::GzDecoder::new(std::fs::File::open(path).unwrap())
        .read_to_end(&mut bytes)
        .unwrap();
    serde_json::from_slice(&bytes).unwrap()
}

#[test]
fn a_valid_candidate_crossing_two_views_is_rejected_even_when_the_planner_returns_it() {
    let dir = std::env::var_os("DRAFTWRIGHT_CORPUS")
        .map(std::path::PathBuf::from)
        .or_else(|| {
            std::env::var_os("HOME")
                .map(|h| std::path::PathBuf::from(h).join("repos/quiddity/tests/corpus"))
        })
        .filter(|d| d.is_dir());
    let Some(dir) = dir else {
        assert!(
            std::env::var_os("DRAFTWRIGHT_CORPUS_REQUIRED").is_none_or(|v| v != "1"),
            "required corpus missing"
        );
        return;
    };
    let (_, mut analysis) = analyse_native(&dir.join("mfcadpp/1004.step")).unwrap();
    let hole = analysis
        .features
        .iter_mut()
        .find(|f| f.id == "f:hole:f13")
        .unwrap();
    hole.parameters["location"][1] = serde_json::json!(-60.0);
    hole.parameters["location"][2] = serde_json::json!(120.0);
    let first = recorded("1004-view-gate-first.plan.json.gz");
    let unsafe_try = recorded("1004-view-gate-unsafe.plan.json.gz");
    assert_eq!(first.binding.source_sha256, analysis.binding.source_sha256);
    assert_eq!(
        unsafe_try.binding.source_sha256,
        analysis.binding.source_sha256
    );
    assert_eq!(over_views(&first), 0);
    assert_eq!(over_views(&unsafe_try), 2);
    assert!(findings(&unsafe_try).clean());
    assert!(carried(&first).is_subset(&carried(&unsafe_try)));
    let named: Spec = serde_json::from_value(serde_json::json!({
        "sheet": {"size": "A4", "scale": "1:1"}
    }))
    .unwrap();
    // Replay the same real unsafe proposal for every arrangement request. This
    // exercises the recovery veto even after the ordinary planner repairs the
    // historical drawing. No production path selects a fixture or a part name.
    // The recorded proposals were planned against the frame, before the 3 mm frame gutter
    // (2026-10-25): judge them as they were drawn, so the veto still meets a valid candidate.
    let plan = crate::layout::with_gutter(0.0, || {
        crate::planning::with(&analysis, || {
            for arrangement in [
                None,
                Some("columns"),
                Some("stacked-iso"),
                Some("staggered-side"),
            ] {
                let analysis_key: [u8; 32] =
                    Sha256::digest(serde_json::to_vec(&analysis).unwrap()).into();
                // Native recovery prepares its established-route policy before the
                // first candidate; inject the same unsafe drawing on either side of
                // that preparation, under the planner's complete exact input key.
                for established in [false, true] {
                    let mut policy = crate::planning::policy_key(&analysis);
                    policy.1 = established;
                    let key = serde_json::to_vec(&(
                        analysis_key,
                        policy,
                        &named,
                        true,
                        arrangement,
                        false,
                    ))
                    .unwrap();
                    crate::planning::store(
                        &analysis,
                        "candidate",
                        key,
                        Ok::<Plan, Invalid>(unsafe_try.clone()),
                    );
                }
            }
            recover_uncached(&analysis, &Spec::default(), first).unwrap()
        })
    });
    assert_eq!(over_views(&plan), 0);
    assert_ne!(
        (plan.sheet.size.as_str(), plan.sheet.scale.as_str()),
        ("A4", "1:1")
    );
    let gate = plan
        .sheet
        .basis
        .split_once("candidates: ")
        .unwrap()
        .1
        .split("; ")
        .find(|t| t.starts_with("A4 1:1 fill"))
        .expect("the unsafe A4 1:1 proposal is tested");
    assert!(
        gate.contains("valid and complete, 2 additional view conflicts"),
        "{}",
        plan.sheet.basis
    );
}

/// New information may use more paper, but may not add hard findings to
/// established dimension/GD&T ink. Exercise the same bounded reduction used
/// by automatic recovery, with its reference memo prepared normally.
#[test]
fn new_recess_locations_clear_established_pmi_ink() {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    let bytes =
        std::fs::read(root.join("tests/fixtures/pmi/step/nist_stc_09_asme1_ap242-e4.stp.gz"))
            .unwrap();
    let mut step = Vec::new();
    flate2::read::GzDecoder::new(bytes.as_slice())
        .read_to_end(&mut step)
        .unwrap();
    let dir = std::env::temp_dir().join(format!("draftwright-recess-pmi-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("stc09.stp");
    std::fs::write(&path, step).unwrap();
    let a = analyse_native(&path).unwrap().1;
    crate::planning::with(&a, || {
        let before = established_drawing(&a, &Spec::default()).unwrap();
        let spec: Spec =
            serde_json::from_value(serde_json::json!({"sheet":{"size":"A1","scale":"1:5"}}))
                .unwrap();
        let trial = plan_escalated_with(&a, &spec, true, None, false).unwrap();
        let hard = |p: &Plan| -> BTreeSet<_> {
            p.problems
                .iter()
                .filter(|p| HARD_LAYOUT.contains(&p.code.as_str()))
                .map(|p| {
                    let mut ids = p.subjects.clone();
                    ids.sort();
                    (p.code.clone(), ids)
                })
                .collect()
        };
        let non_locations = |p: &Plan| {
            carried(p)
                .into_iter()
                .filter(|(_, role)| !role.starts_with("location.length."))
                .collect::<BTreeSet<_>>()
        };
        let missing = |p: &Plan| {
            let mut p = p.clone();
            crate::dims::pmi_policy::audit(&a, &p.spec.clone(), &mut p);
            p.problems
                .into_iter()
                .filter(|p| p.code == "feature_not_located")
                .map(|p| p.id)
                .collect::<BTreeSet<_>>()
        };

        let original = hard(&before);
        let actual = hard(&trial);
        assert!(non_locations(&before).is_subset(&non_locations(&trial)));
        let lost: Vec<_> = missing(&trial)
            .difference(&missing(&before))
            .cloned()
            .collect();
        assert!(lost.is_empty(), "existing rooted locations lost: {lost:?}");
        // accepted regression, pzfreo/draftwright#452 (Paul 2026-11-13); restore when fixed
        let accepted: BTreeSet<_> = [
            ("annotation_ink_overlap", &["d:envelope/width.length", "pmi:geometric_tolerance.perpendicularity:f89:ref=A:mod=diameter_zone"][..]),
            ("annotation_ink_overlap", &["d:envelope/width.length", "pmi:geometric_tolerance.position:f34,35,36,37,92,93:ref=A,G,H:mod=diameter_zone"][..]),
            ("annotation_ink_overlap", &["d:envelope/width.length", "pmi:geometric_tolerance.position:f95,97,99,101,159,160,161,162:ref=A,B,C:mod=maximum_material_requirement"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f0/location.length.x", "d:f:slot:f102/slot_length.length"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f0/location.length.z", "pmi:geometric_tolerance.position:f0,1,121,122:ref=A,B,C:mod=diameter_zone"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f121/location.length.x", "d:f:hole:f42/location.length.x"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f121/location.length.x", "d:f:hole_pattern:f18/location.length.x"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f121/location.length.x", "d:f:hole_pattern:f48/pitch.length"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f121/location.length.x", "d:f:slot:f102/slot_length.length"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f26/location.length.z", "d:f:section_recess:f59/seat_diameter.diameter"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f34/location.length.x", "d:f:hole:f42/location.length.x"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f34/location.length.x", "d:f:hole_pattern:f18/location.length.x"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f34/location.length.x", "d:f:hole_pattern:f48/pitch.length"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f34/location.length.x", "pmi:geometric_tolerance.perpendicularity:f89:ref=A:mod=diameter_zone"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f34/location.length.x", "pmi:geometric_tolerance.position:f34,35,36,37,92,93:ref=A,G,H:mod=diameter_zone"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f34/location.length.x", "pmi:geometric_tolerance.position:f95,97,99,101,159,160,161,162:ref=A,B,C:mod=maximum_material_requirement"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f34/location.length.z", "d:pmi:dimension.linear:f94,96,98,100/linear"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f34/location.length.z", "pmi:geometric_tolerance.position:f89:ref=A,B,C:mod=diameter_zone"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f36/location.length.x", "d:f:hole:f42/location.length.x"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f36/location.length.x", "d:f:hole_pattern:f18/location.length.x"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f36/location.length.x", "d:f:hole_pattern:f48/pitch.length"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f36/location.length.x", "pmi:geometric_tolerance.perpendicularity:f89:ref=A:mod=diameter_zone"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f36/location.length.x", "pmi:geometric_tolerance.position:f95,97,99,101,159,160,161,162:ref=A,B,C:mod=maximum_material_requirement"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f36/location.length.z", "d:f:hole_pattern:f18/grid_pitch.length.row"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f36/location.length.z", "d:pmi:dimension.linear:f94,96,98,100/linear"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f36/location.length.z", "pmi:geometric_tolerance.position:f18,19,22,23:ref=A,B,C:mod=diameter_zone,maximum_material_requirement"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f42/bore.diameter", "pmi:geometric_tolerance.profile_surface:f91:ref=A,G,H"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f42/location.length.x", "d:f:hole:f89/location.length.x"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f89/location.length.x", "d:f:hole_pattern:f18/location.length.x"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f89/location.length.x", "d:f:hole_pattern:f48/pitch.length"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f89/location.length.x", "d:f:slot:f102/slot_length.length"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f92/location.length.z", "d:f:hole_pattern:f18/grid_pitch.length.row"][..]),
            ("annotation_ink_overlap", &["d:f:hole:f92/location.length.z", "d:pmi:dimension.linear:f94,96,98,100/linear"][..]),
            ("annotation_ink_overlap", &["d:f:hole_pattern:f18/grid_pitch.length.col", "pmi:geometric_tolerance.perpendicularity:f89:ref=A:mod=diameter_zone"][..]),
            ("annotation_ink_overlap", &["d:f:hole_pattern:f18/grid_pitch.length.col", "pmi:geometric_tolerance.position:f34,35,36,37,92,93:ref=A,G,H:mod=diameter_zone"][..]),
            ("annotation_ink_overlap", &["d:f:hole_pattern:f18/grid_pitch.length.col", "pmi:geometric_tolerance.position:f95,97,99,101,159,160,161,162:ref=A,B,C:mod=maximum_material_requirement"][..]),
            ("annotation_ink_overlap", &["d:f:hole_pattern:f18/grid_pitch.length.row", "d:f:hole_pattern:f18/location.length.z"][..]),
            ("annotation_ink_overlap", &["d:f:hole_pattern:f18/grid_pitch.length.row", "d:step_level/step_height.length.f56"][..]),
            ("annotation_ink_overlap", &["d:f:hole_pattern:f18/grid_pitch.length.row", "pmi:geometric_tolerance.position:f18,19,22,23:ref=A,B,C:mod=diameter_zone,maximum_material_requirement"][..]),
            ("annotation_ink_overlap", &["d:f:hole_pattern:f18/location.length.z", "d:pmi:dimension.linear:f94,96,98,100/linear"][..]),
            ("annotation_ink_overlap", &["d:f:hole_pattern:f18/location.length.z", "pmi:geometric_tolerance.position:f18,19,22,23:ref=A,B,C:mod=diameter_zone,maximum_material_requirement"][..]),
            ("annotation_ink_overlap", &["d:f:hole_pattern:f18/location.length.z", "pmi:geometric_tolerance.position:f89:ref=A,B,C:mod=diameter_zone"][..]),
            ("annotation_ink_overlap", &["d:f:hole_pattern:f30/location.length.z", "pmi:geometric_tolerance.position:f42,43,44,45,46,47,54,55:ref=A,B,C:mod=diameter_zone"][..]),
            ("annotation_ink_overlap", &["d:f:hole_pattern:f30/pitch.length", "pmi:geometric_tolerance.position:f42,43,44,45,46,47,54,55:ref=A,B,C:mod=diameter_zone"][..]),
            ("annotation_ink_overlap", &["d:f:hole_pattern:f38/pitch.length", "pmi:geometric_tolerance.perpendicularity:f84,85:ref=A:mod=diameter_zone"][..]),
            ("annotation_ink_overlap", &["d:f:hole_pattern:f38/pitch.length", "pmi:geometric_tolerance.position:f80,81,82,83:ref=A,D,B:mod=diameter_zone"][..]),
            ("annotation_ink_overlap", &["d:f:hole_pattern:f38/pitch.length", "pmi:geometric_tolerance.position:f84,85:ref=A,B,C:mod=diameter_zone"][..]),
            ("annotation_ink_overlap", &["d:f:section_recess:f59/seat_diameter.diameter", "d:step_level/step_height.length.f124"][..]),
            ("annotation_ink_overlap", &["d:f:section_recess:f59/seat_location.location.z", "pmi:geometric_tolerance.position:f42,43,44,45,46,47,54,55:ref=A,B,C:mod=diameter_zone"][..]),
            ("annotation_ink_overlap", &["d:f:slot:f102/location_slot_across.length", "d:pmi:dimension.linear:f94,96,98,100/linear"][..]),
            ("annotation_ink_overlap", &["d:f:slot:f102/slot_length.length", "d:f:slot:f94/location_slot.length"][..]),
            ("annotation_ink_overlap", &["d:f:slot:f102/slot_length.length", "pmi:geometric_tolerance.perpendicularity:f89:ref=A:mod=diameter_zone"][..]),
            ("annotation_ink_overlap", &["d:f:slot:f102/slot_length.length", "pmi:geometric_tolerance.position:f34,35,36,37,92,93:ref=A,G,H:mod=diameter_zone"][..]),
            ("annotation_ink_overlap", &["d:f:slot:f102/slot_length.length", "pmi:geometric_tolerance.position:f95,97,99,101,159,160,161,162:ref=A,B,C:mod=maximum_material_requirement"][..]),
            ("annotation_ink_overlap", &["d:f:slot:f102/slot_width.length", "d:f:slot:f94/slot_width.length"][..]),
            ("annotation_ink_overlap", &["d:f:slot:f94/slot_width.length", "d:pmi:dimension.linear:f102,104/linear"][..]),
            ("annotation_ink_overlap", &["d:pmi:dimension.linear:f102,104/linear", "d:pmi:dimension.linear:f94,96,98,100/linear"][..]),
            ("annotation_ink_overlap", &["d:pmi:dimension.linear:f94,96,98,100/linear", "d:step_level/step_height.length.f56"][..]),
            ("annotation_ink_overlap", &["d:pmi:dimension.linear:f94,96,98,100/linear", "pmi:geometric_tolerance.position:f18,19,22,23:ref=A,B,C:mod=diameter_zone,maximum_material_requirement"][..]),
            ("annotation_ink_overlap", &["d:pmi:dimension.linear:f94,96,98,100/linear", "pmi:geometric_tolerance.position:f89:ref=A,B,C:mod=diameter_zone"][..]),
            ("annotation_ink_overlap", &["d:step_level/step_height.length.f132", "pmi:geometric_tolerance.position:f38,39,40,41,86,87:ref=A,B,C:t=position%201.52"][..]),
            ("annotation_ink_overlap", &["d:step_level/step_height.length.f56", "pmi:geometric_tolerance.position:f18,19,22,23:ref=A,B,C:mod=diameter_zone,maximum_material_requirement"][..]),
            ("annotation_ink_overlap", &["pmi:datum.datum:f20,21:t=B", "pmi:datum.datum:f24,25:t=C"][..]),
            ("annotation_ink_overlap", &["pmi:datum.datum:f24,25:t=C", "pmi:datum.datum:f84,85:t=D"][..]),
            ("annotation_ink_overlap", &["pmi:datum.datum:f24,25:t=C", "pmi:datum.datum:f89:t=G"][..]),
            ("annotation_ink_overlap", &["pmi:geometric_tolerance.perpendicularity:f84,85:ref=A:mod=diameter_zone", "pmi:geometric_tolerance.profile_surface:f2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,112,113,114,115,116,117,118,119:ref=A,B,C:mod=all_around"][..]),
            ("annotation_ink_overlap", &["pmi:geometric_tolerance.position:f80,81,82,83:ref=A,D,B:mod=diameter_zone", "pmi:geometric_tolerance.profile_surface:f2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,112,113,114,115,116,117,118,119:ref=A,B,C:mod=all_around"][..]),
            ("annotation_ink_overlap", &["pmi:geometric_tolerance.position:f84,85:ref=A,B,C:mod=diameter_zone", "pmi:geometric_tolerance.profile_surface:f2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,112,113,114,115,116,117,118,119:ref=A,B,C:mod=all_around"][..]),
            ("annotation_overlap", &["d:f:chamfer:f119/chamfer.length", "d:f:section_recess:f59/seat_location.location.z"][..]),
            ("annotation_overlap", &["d:f:hole:f26/location.length.z", "d:f:hole_pattern:f30/bore.diameter"][..]),
            ("annotation_overlap", &["d:f:hole:f42/bore.diameter", "pmi:geometric_tolerance.position:f94,96,98,100:ref=A,B,C:mod=maximum_material_requirement"][..]),
            ("annotation_overlap", &["d:f:hole:f84/bore.diameter", "d:f:hole_pattern:f48/bore.diameter"][..]),
            ("annotation_overlap", &["d:f:hole_pattern:f30/bore.diameter", "d:step_level/step_height.length.f124"][..]),
            ("annotation_overlap", &["d:f:hole_pattern:f30/pitch.length", "d:f:section_recess:f59/seat_diameter.diameter"][..]),
        ].into_iter().map(|(code, ids)| (code.to_string(), ids.iter().map(|s| s.to_string()).collect::<Vec<_>>())).collect();
        let new: BTreeSet<_> = actual.difference(&original).cloned().collect();
        assert_eq!(
            new, accepted,
            "new hard findings beyond the reviewed CAD frame layout"
        );
        let auto = plan_escalated_with(&a, &Spec::default(), false, None, false).unwrap();
        assert!(non_locations(&auto).is_subset(&non_locations(&trial)));
        // A schedule can name individual coordinates where the other presentation
        // uses a retained pattern chain. Compare their actual rooted ink, rather
        // than requiring identical location-claim spellings in both presentations.
        let lost: Vec<_> = missing(&trial)
            .difference(&missing(&auto))
            .cloned()
            .collect();
        assert!(
            lost.is_empty(),
            "reduction loses rooted locations: {lost:?}"
        );
    });
}

#[test]
fn covered_aliases_follow_a_location_into_a_drawn_schedule_only() {
    let mut plan = recorded("1004-view-gate-first.plan.json.gz");
    let alias = "d:f:slot:extra/location_slot_across.length";
    let owner = "d:f:hole:extra/location.length.y";
    for (id, by) in [(alias, owner), (owner, "schedule")] {
        plan.omitted.push(crate::dims::Omitted {
            id: id.into(),
            label: "20".into(),
            basis: "same physical span".into(),
            state: ItemState::Covered { by: by.into() },
        });
    }
    let claim = ("f:slot:extra".into(), "location_slot_across.length".into());
    assert!(!carried(&plan).contains(&claim));
    let rows = vec![vec!["20".into()]];
    plan.hole_tables.push(crate::hole_table::HoleTable {
        row_order_before: Vec::new(),
        sheet: 1,
        id: "schedule".into(),
        view: Some("v:plan".into()),
        origin: None,
        pcd: None,
        covered: vec![],
        rows: vec![],
        default: true,
        basis: String::new(),
        state: ItemState::Shown,
        blocks: vec![crate::tables::PlannedTable {
            sheet: 1,
            id: "schedule".into(),
            rows: rows.clone(),
            row_claims: vec![],
            default: true,
            basis: String::new(),
            state: ItemState::Shown,
            placed: Some(crate::tables::lay(&rows, &[10.0], [10.0, 10.0])),
        }],
    });
    assert!(carried(&plan).contains(&claim));
    plan.hole_tables[0].blocks[0].placed = None;
    assert!(
        !carried(&plan).contains(&claim),
        "an unplaced table carries no alias"
    );
    plan.omitted.last_mut().unwrap().state = ItemState::Covered { by: alias.into() };
    assert!(
        !carried(&plan).contains(&claim),
        "a cycle without drawn ink is not coverage"
    );
}

#[test]
fn clear_fixed_sheet_finish_preserves_inventory_and_passes_fresh_audit() {
    let dir = std::env::var_os("DRAFTWRIGHT_CORPUS")
        .map(std::path::PathBuf::from)
        .or_else(|| {
            std::env::var_os("HOME")
                .map(|h| std::path::PathBuf::from(h).join("repos/quiddity/tests/corpus"))
        })
        .filter(|p| p.is_dir());
    let Some(dir) = dir else {
        assert!(std::env::var_os("DRAFTWRIGHT_CORPUS_REQUIRED").is_none_or(|v| v != "1"));
        return;
    };
    // cgb241 on A3: v0.2.3 (#127) states the four formerly unclaimed cylindrical
    // profiles. At 1:5 (the new complete automatic choice), their counted radii
    // settle clear; the old 1:10 seed crosses two leaders. Keep the same A3 sheet
    // and strengthen legibility while testing the identical finishing invariant.
    for (file, size, scale) in [
        ("cadgenbench_inputs/cgb241.step", "A3", "1:5"),
        ("mfcadpp/10163.step", "A4", "1:2"),
    ] {
        let (_, a) = analyse_native(&dir.join(file)).unwrap();
        crate::planning::with(&a, || {
            plan_automatic(&a, &Spec::default()).unwrap();
            let mut spec: Spec =
                serde_json::from_value(serde_json::json!({"sheet":{"size":size,"scale":scale}}))
                    .unwrap();
            let factor = layout::SCALES
                .into_iter()
                .find(|s| scale_name(*s) == scale)
                .unwrap();
            for (name, detail) in crate::detail::requests(&a, &spec, factor) {
                if !detail.authored {
                    spec.views.entry(name).or_default().omit = true;
                }
            }
            let raw = crate::planning::fixed_pair(|| {
                crate::planning::defer_nominals(|| crate::sheets::allocate(&a, &spec))
            })
            .unwrap();
            assert!(
                !raw.problems.iter().any(crate::sheet_policy::layout_problem),
                "{file}: expected clear settled ink: {:?}",
                raw.problems
                    .iter()
                    .filter(|p| crate::sheet_policy::layout_problem(p))
                    .map(|p| (&p.code, &p.message))
                    .collect::<Vec<_>>()
            );
            let mut fast = raw.clone();
            crate::planning::fixed_pair(|| finish_nominal_plan(&a, &spec, &mut fast));
            let mut full = raw;
            finish_nominal_plan(&a, &spec, &mut full);
            for plan in [&fast, &full] {
                assert!(
                    !plan
                        .problems
                        .iter()
                        .any(crate::sheet_policy::layout_problem),
                    "{file}: finishing introduced a layout problem"
                );
                assert!(
                    !crate::dims::recheck_cached(
                        &a,
                        plan,
                        &crate::dims::footprints::Cache::default(),
                    )
                    .iter()
                    .any(crate::sheet_policy::layout_problem),
                    "{file}: finished ink failed a fresh audit"
                );
            }
            // The full pass may choose different, equally clear routes. It must
            // carry exactly the same statements, text and omission decisions.
            let inventory = |plan: &Plan| {
                let mut value = serde_json::to_value(plan).unwrap();
                for dimension in value["dimensions"].as_array_mut().unwrap() {
                    dimension.as_object_mut().unwrap().remove("placed");
                }
                for view in value["views"].as_array_mut().unwrap() {
                    // Settled areas include leader extents; the view's scale,
                    // origin and source geometry must remain identical.
                    view.as_object_mut().unwrap().remove("area");
                }
                value.as_object_mut().unwrap().remove("sheet");
                value
            };
            assert!(
                inventory(&fast) == inventory(&full),
                "{file}: changed inventory"
            );
        });
    }
}

#[test]
fn fixed_page_fit_warning_keeps_real_view_and_title_overlaps() {
    let path =
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/plate.step");
    let (_, a) = analyse_native(&path).unwrap();
    let mut spec: Spec = serde_json::from_value(serde_json::json!({
        "sheet": {"size": "A3", "scale": "1:1"}
    }))
    .unwrap();
    let seed = plan_escalated(&a, &spec).unwrap();
    spec.sheet.keep_layout = Some(true);
    for on_title in [false, true] {
        let mut plan = seed.clone();
        plan.spec = spec.clone();
        plan.views
            .retain(|v| v.id == "v:front" || (!on_title && v.id == "v:plan"));
        assert_eq!(plan.views.len(), if on_title { 1 } else { 2 });
        plan.dimensions.clear();
        plan.gdt.clear();
        plan.captions.clear();
        plan.notes.clear();
        plan.notes_block = None;
        plan.radius_notes_block = None;
        plan.radius_table = None;
        plan.tables.clear();
        plan.hole_table = None;
        plan.hole_tables.clear();
        plan.balloons.clear();
        plan.requirements.clear();
        let centre = if on_title {
            let b = plan.sheet.title_block;
            [(b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0]
        } else {
            [90.0, 140.0]
        };
        let boxes = crate::sheet_fit::footprints(&a, &plan);
        for (view, b) in plan.views.iter_mut().zip(boxes) {
            for k in 0..2 {
                let delta = centre[k] - (b[k] + b[k + 2]) / 2.0;
                view.at[k] += delta;
                view.area[k] += delta;
                view.area[k + 2] += delta;
            }
        }
        assert!(crate::sheet_fit::inside_frames(&a, &plan));
        assert!(!finished_items_fit(&a, &plan));
        plan.problems.clear();
        plan.problems.push(Problem {
            id: "p:page_fit_uncertain:sheet".into(),
            code: "page_fit_uncertain".into(),
            severity: "warning".into(),
            message: "The views may not fit.".into(),
            subjects: vec!["sheet".into()],
            ..Default::default()
        });
        let repaired =
            crate::planning::fixed_pair(|| recover_candidates(&a, &spec, plan, true, false))
                .unwrap();
        assert!(
            repaired
                .problems
                .iter()
                .any(|p| p.code == "page_fit_uncertain")
        );
    }
}

/// The automatic enlargement veto ([`veto_grown_leaders`]) at the smallest sheet's established
/// leader: a larger sheet at a larger scale is dropped when the leader doubles, not when it
/// grows by a few millimetres (maintainer's rule of 2026-11-12); a larger sheet at the same
/// scale keeps the 2 mm rule, since nothing is drawn larger there to lengthen a leader.
#[test]
fn a_larger_sheet_is_refused_for_a_doubled_leader_and_the_trace_says_so() {
    let analysis: Analysis = serde_json::from_value(serde_json::json!({
        "schema": ANALYSIS_SCHEMA,
        "binding":{"source_sha256":"", "part":"plate", "face_count":0, "loader":"test"},
        "envelope":[[0,0,0],[100,60,10]], "iso_bounds":[0,0,100,60]
    }))
    .unwrap();
    let spec = Spec::default();
    let base = plan(&analysis, &spec).unwrap();
    let with_leader = |length: f64, sheet: &str, scale: f64| {
        let mut p = base.clone();
        let mut d = p.dimensions[0].clone();
        d.id = "d:f:hole:1/bore.diameter".into();
        d.kind = "callout".into();
        d.placed = Some(crate::dims::Placed {
            lines: vec![[[0.0, 0.0], [length, 0.0]]],
            arrows: Vec::new(),
            label_at: [length, 0.0],
            rotation: 0.0,
            label_box: [length, -1.0, length + 10.0, 1.0],
        });
        p.dimensions = vec![d];
        p.sheet.size = sheet.into();
        p.sheet.scale = scale_name(scale);
        p.sheet.scale_factor = scale;
        p
    };
    let candidate = |sheet: usize, scale: f64| crate::sheet_fit::Candidate {
        sheet,
        scale,
        width: spec.pages()[sheet].1,
        fill: 0.5,
        extra_crossings: 0,
    };
    let reference = with_leader(12.0, "A4", 1.0);
    let trace = |sheet: usize, scale: f64| {
        format!(
            "{} {} fill 50.0% (valid and complete)",
            spec.pages()[sheet].0,
            scale_name(scale)
        )
    };
    // A3 at 2:1 with the leader 25 mm (doubled), A2 at 2:1 with it 20 mm (not doubled), and
    // A3 at 1:1 with it 15 mm (3 mm longer at the same scale).
    let mut candidates = vec![
        (candidate(0, 1.0), reference.clone()),
        (candidate(1, 2.0), with_leader(25.0, "A3", 2.0)),
        (candidate(2, 2.0), with_leader(20.0, "A2", 2.0)),
        (candidate(1, 1.0), with_leader(15.0, "A3", 1.0)),
    ];
    let mut tried = vec![trace(0, 1.0), trace(1, 2.0), trace(2, 2.0), trace(1, 1.0)];
    veto_grown_leaders(&spec, &mut candidates, &mut tried, 0, &reference);
    let kept: Vec<_> = candidates.iter().map(|(c, _)| (c.sheet, c.scale)).collect();
    assert_eq!(kept, [(0, 1.0), (2, 2.0)]);
    assert_eq!(tried[0], trace(0, 1.0));
    assert!(
        tried[1].contains("(leader length quality veto")
            && tried[1].ends_with("automatic enlargement doubles an established leader's length"),
        "{}",
        tried[1]
    );
    assert_eq!(tried[2], trace(2, 2.0));
    assert!(
        tried[3]
            .ends_with("lengthens an established leader by more than 2 mm without a larger scale"),
        "{}",
        tried[3]
    );
}

#[test]
fn a_manufacturing_table_with_no_place_falls_back_to_full_callouts() {
    fn claimed<'a>(plan: &'a Plan, a: &Analysis, source: &str) -> Vec<(&'a str, &'a str)> {
        let Some(crate::pmi::Reading::Read(report)) = &a.pmi else {
            panic!("PMI read")
        };
        let feature = &report.identities().ids[source];
        plan.dimensions
            .iter()
            .filter(|d| d.measurements.iter().any(|m| &m.feature == feature))
            .map(|d| (d.id.as_str(), d.label.as_str()))
            .collect()
    }
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    let (part, a) = analyse_native(
        &root.join("tests/fixtures/pmi/step/GRM-03_thumbwheel_drive_screw_AP242_PMI.step.gz"),
    )
    .unwrap();
    let spec = |sheet: serde_json::Value| -> Spec {
        let mut sheet = sheet;
        sheet["callouts"] = serde_json::json!({"style": "note"});
        // Both declared datums drawn, as when this case was recorded: undrawn, datum A leaves
        // the table room on A4 at 5:1.
        sheet["uncited_datums"] = serde_json::json!(true);
        // Keep this a genuine no-place fallback: the new split form has its own test below.
        sheet["table_columns"] = serde_json::json!({"manufacturing_requirements":1});
        serde_json::from_value(serde_json::json!({ "sheet": sheet })).unwrap()
    };
    // With room, the requirements go to the table, as before.
    let roomy = plan(&a, &spec(serde_json::json!({}))).unwrap();
    let table = roomy
        .tables
        .iter()
        .find(|t| t.id == "manufacturing_requirements")
        .expect("the table, with room");
    assert!(table.placed.is_some());
    let font = crate::text::Font::open(&root.join("fonts").join(crate::dims::FONT)).unwrap();
    let given = spec(serde_json::json!({"size": "A4", "scale": "5:1"}));
    // frame laid out as CAD's TOLERANCE (Paul 2026-11-12): the smaller symbols
    // leave the 5:1 table room. Inject its no-place decision, keeping both real
    // replanning paths and every rendered requirement/claim assertion below.
    for replan in [plan, plan_recovered] {
        let mut unavailable = replan(&a, &given).unwrap();
        let table = unavailable
            .tables
            .iter_mut()
            .find(|t| t.id == "manufacturing_requirements")
            .unwrap();
        assert!(table.placed.is_some());
        table.placed = None;
        table.state = ItemState::NotDrawable {
            reason_code: "table_not_placed".into(),
        };
        unavailable.problems.push(Problem {
            id: "p:table_not_placed:manufacturing_requirements".into(),
            code: "table_not_placed".into(),
            severity: "error".into(),
            subjects: vec!["manufacturing_requirements".into()],
            ..Default::default()
        });
        let plan = manufacturing_fallback(&a, &given, unavailable, |s| replan(&a, s)).unwrap();
        assert!(
            !plan.problems.iter().any(|p| p.code == "table_not_placed"),
            "{:#?}",
            plan.problems
        );
        assert!(
            plan.tables
                .iter()
                .all(|t| t.id != "manufacturing_requirements")
        );
        // The requested spec stays the plan's: the fallback is no authored override.
        assert_eq!(plan.spec.sheet, given.sheet);
        assert!(plan.spec.features.is_empty());
        // The fallback says what it did, naming the requirements printed in full.
        let fallback = plan
            .problems
            .iter()
            .find(|p| p.code == "manufacturing_table_fallback")
            .expect("an info finding naming the fallback");
        assert_eq!(fallback.severity, "info");
        assert!(fallback.refs.len() >= 2, "{fallback:#?}");
        for source in &fallback.refs {
            let labels = claimed(&plan, &a, source);
            assert_eq!(labels.len(), 1, "{source}: {labels:?}");
            let (id, label) = labels[0];
            assert!(!label.contains("SEE MFG"), "{source}: {label:?}");
            let basis = &plan.dimensions.iter().find(|d| d.id == id).unwrap().basis;
            assert!(
                basis.contains("manufacturing table fallback")
                    && basis.contains("table has no place on the sheet")
                    && !basis.contains("callouts/style Full"),
                "{basis}"
            );
        }
        let thread = plan
            .dimensions
            .iter()
            .map(|d| d.label.split_whitespace().collect::<Vec<_>>().join(" "))
            .find(|l| l.contains("MIN FULL THREAD"))
            .expect("the thread in full");
        assert!(thread.contains("M2 x 0.4-6H RH"), "{thread}");
        // No hard finding: not in the plan, and not on the rendered sheet.
        let errors: Vec<_> = plan
            .problems
            .iter()
            .filter(|p| p.severity == "error")
            .map(|p| &p.id)
            .collect();
        assert!(errors.is_empty(), "{errors:?}");
        let drawing = render(&part, &a, &plan, &font).unwrap();
        let found: Vec<_> = crate::lint::lint(&a, &drawing)
            .into_iter()
            .chain(crate::lint::structural::check_sheets(std::slice::from_ref(
                &drawing,
            )))
            .filter(|i| i.severity == crate::lint::Severity::Error)
            .map(|i| i.id)
            .collect();
        assert!(found.is_empty(), "{found:?}");
    }
}
