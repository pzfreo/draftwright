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
        .find(|f| f.id == "f:hole:1")
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
        let new: Vec<_> = actual.difference(&original).collect();
        assert!(
            new.is_empty(),
            "new hard findings: {new:#?}; grade {:?}; {}; new placements {:#?}",
            gdt_misplaced(&trial),
            trial.sheet.basis,
            trial
                .dimensions
                .iter()
                .filter(|d| d.id.starts_with("d:f:section_recess:"))
                .map(|d| (&d.id, &d.placed))
                .collect::<Vec<_>>()
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
