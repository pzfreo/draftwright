//! R5 I3 acceptance: the same item IDs survive sheet-addressed MCP edits and replay.
use draftwright::api::Features;
use draftwright::items::Op;
use draftwright::model::Spec;
use draftwright::session::{ItemFilter, Session};
use draftwright::{mcp, stateless::Cache};
use serde_json::{Value, json};
use std::path::Path;

fn spec() -> Spec {
    serde_json::from_value(json!({"sheets":{"max":2},"views":{"iso":{"sheet":2}}})).unwrap()
}
fn open(name: &str) -> (Cache, String) {
    let root = Path::new(env!("CARGO_TARGET_TMPDIR")).join(format!("r5-mcp-{name}"));
    let _ = std::fs::remove_dir_all(&root);
    let cache = Cache::new(root);
    let key = cache
        .register(
            &Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/mounting_plate.step"),
            None,
        )
        .unwrap();
    (cache, key)
}
fn checked(cache: &Cache, tool: &str, args: Value) -> Value {
    mcp::validate(&mcp::schema(tool, false).unwrap(), &args).unwrap();
    let result = mcp::call(cache, tool, &args).unwrap();
    mcp::validate(&mcp::schema(tool, true).unwrap(), &result).unwrap();
    result
}

#[test]
fn r5_qualified_addresses_and_near_are_sheet_local() {
    let (cache, key) = open("address");
    let s = cache.session(&key, &spec()).unwrap();
    assert_eq!(s.item("sheet:2/v:iso").unwrap().item.id, "v:iso");
    let error = s.item("sheet:1/v:iso").unwrap_err();
    assert!(error.message.contains("v:iso is on sheet 2"));
    assert!(error.choices.contains(&"sheet:2/v:iso".into()));
    assert!(s.options("sheet:2/v:iso").is_ok());
    assert!(!s.item("sheet:2/sheet:2").unwrap().basis.is_empty());
    assert!(
        s.options("sheet:2/sheet:2")
            .unwrap()
            .settable
            .contains_key("sheet/size")
    );
    let options = s.options("sheet:2/sheet:2").unwrap();
    assert!(options.settable.contains_key("sheet/preferred_max_size"));
    assert!(options.settable.contains_key("sheet/preferred_max_sheets"));
    assert!(!options.settable.contains_key("sheet/max_size"));
    assert!(!options.settable.contains_key("sheet/preferred_printer"));
    let filter: ItemFilter =
        serde_json::from_value(json!({"near":"sheet:2/v:iso","radius":10000})).unwrap();
    let nearby = s.items(&filter);
    assert!(!nearby.is_empty());
    assert!(nearby.iter().all(|i| i.item.sheet == 2));
    let local = checked(
        &cache,
        "list_items",
        json!({"part_ref":key,"spec":spec(),"filter":{"near":"v:iso","radius":10000}}),
    );
    assert!(
        local["items"]
            .as_array()
            .unwrap()
            .iter()
            .all(|i| i["sheet"] == 2)
    );
    for id in ["sheet:0/v:iso", "sheet:1/v:iso"] {
        assert!(
            mcp::call(
                &cache,
                "get_item",
                &json!({"part_ref":key,"spec":spec(),"id":id})
            )
            .is_err()
        );
        assert!(
            mcp::call(
                &cache,
                "list_items",
                &json!({"part_ref":key,"spec":spec(),"filter":{"near":id}})
            )
            .is_err()
        );
    }
    let overlay = checked(
        &cache,
        "render",
        json!({"part_ref":key,"spec":spec(),"sheet":2,"overlay_ids":["sheet:2/v:iso"],"dpi":30}),
    );
    assert!(overlay["bytes"].as_u64().unwrap() > 0);
    assert!(
        mcp::call(
            &cache,
            "render",
            &json!({"part_ref":key,"spec":spec(),"sheet":1,"overlay_ids":["sheet:2/v:iso"]})
        )
        .unwrap_err()
        .message
        .contains("v:iso is on sheet 2")
    );
}

#[test]
fn r5_list_items_filters_and_reports_sheet() {
    let (cache, key) = open("filter");
    let result = checked(
        &cache,
        "list_items",
        json!({"part_ref":key,"spec":spec(),"filter":{"sheet":2}}),
    );
    assert_eq!(result["total"], 2);
    assert!(
        result["items"]
            .as_array()
            .unwrap()
            .iter()
            .all(|i| i["sheet"] == 2)
    );
    let first = checked(
        &cache,
        "get_item",
        json!({"part_ref":key,"spec":spec(),"id":"sheet:1/v:front"}),
    );
    assert_eq!(first["item"]["sheet"], 1);
    let detail = checked(
        &cache,
        "get_item",
        json!({"part_ref":key,"spec":spec(),"id":"v:front","detail":true}),
    );
    assert_eq!(detail["item"]["item"]["sheet"], 1);
    let mut invalid_item = detail["item"]["item"].clone();
    invalid_item["sheet"] = Value::Null;
    assert!(serde_json::from_value::<draftwright::items::Item>(invalid_item).is_err());
    let single = checked(&cache, "get_item", json!({"part_ref":key,"id":"v:front"}));
    assert!(single["item"].get("sheet").is_none());
}

#[test]
fn r5_move_options_apply_and_delta_replay() {
    let (cache, key) = open("move");
    let initial: Spec = serde_json::from_value(json!({"sheets":{"max":2}})).unwrap();
    let options = checked(
        &cache,
        "item_options",
        json!({"part_ref":key,"spec":initial,"id":"v:iso"}),
    );
    assert!(
        options["operations"]
            .as_array()
            .unwrap()
            .contains(&json!("move_to_sheet"))
    );
    assert_eq!(
        options["settable"]["views/iso/sheet"]["choices"],
        json!([1, 2])
    );
    let relation: Spec = serde_json::from_value(json!({"sheets":{"max":2,"keep_together":[["iso","front"]]},"views":{"iso":{"place":{"relative_to":"front","side":"right"}}}})).unwrap();
    let related = checked(
        &cache,
        "item_options",
        json!({"part_ref":key,"spec":relation,"id":"v:iso"}),
    );
    assert_eq!(
        related["settable"]["views/iso/sheet"]["consequences"]
            .as_array()
            .unwrap()
            .len(),
        2
    );
    assert!(mcp::call(&cache,"preview_ops",&json!({"part_ref":key,"spec":relation,"ops":[{"op":"set","path":"views/iso/sheet","value":2}]})).is_err());
    let parent = cache.head(&key, "default").unwrap();
    let result = checked(
        &cache,
        "apply_ops",
        json!({"part_ref":key,"parent":parent,"ops":[
            {"op":"set","path":"sheets/max","value":2},
            {"op":"set","path":"views/iso/sheet","value":2}
        ]}),
    );
    assert_eq!(result["delta"]["plans"], 1);
    let bytes = cache
        .artifact_bytes(result["delta"]["delta_ref"].as_str().unwrap())
        .unwrap();
    let delta: draftwright::session::Delta = serde_json::from_slice(&bytes).unwrap();
    assert!(delta.plan.collections.contains_key("sheets"));
    assert!(
        delta
            .changed
            .iter()
            .any(|i| i.item.id == "v:iso" && i.item.sheet == 2)
    );
    assert!(!delta.removed.contains(&"v:iso".into()));
    assert!(
        delta
            .annotations
            .values()
            .all(|a| a.provenance.iter().all(|i| i.sheet == 1))
    );
    assert!(delta.annotations.values().any(|a| !a.provenance.is_empty()));
    let mut replay = cache
        .session(&key, &Spec::default())
        .unwrap()
        .state()
        .clone();
    delta.apply_to(&mut replay).unwrap();
    let final_spec = cache
        .get_version(result["spec_version"].as_str().unwrap())
        .unwrap();
    assert_eq!(
        serde_json::to_value(&replay).unwrap(),
        serde_json::to_value(cache.session(&key, &final_spec).unwrap().state()).unwrap()
    );
}

#[test]
fn r5_summary_and_render_select_sheet() {
    let (cache, key) = open("render");
    let summary = checked(
        &cache,
        "inspect_part",
        json!({"part_ref":key,"spec":spec()}),
    );
    assert_eq!(summary["sheets"], 2);
    assert_eq!(summary["sheet_details"].as_array().unwrap().len(), 2);
    let first = checked(
        &cache,
        "render",
        json!({"part_ref":key,"spec":spec(),"sheet":1,"format":"png"}),
    );
    let second = checked(
        &cache,
        "render",
        json!({"part_ref":key,"spec":spec(),"sheet":2,"format":"png"}),
    );
    assert_ne!(first["artifact_ref"], second["artifact_ref"]);
    let pdf = checked(
        &cache,
        "render_pdf",
        json!({"part_ref":key,"spec":spec(),"sheet":2}),
    );
    let bytes = cache
        .artifact_bytes(pdf["artifact_ref"].as_str().unwrap())
        .unwrap();
    assert_eq!(
        draftwright::model::embedded_spec(&bytes)
            .unwrap()
            .unwrap()
            .sheets
            .unwrap()
            .max,
        Some(2)
    );
    let error = mcp::call(
        &cache,
        "render",
        &json!({"part_ref":key,"spec":spec(),"sheet":3}),
    )
    .unwrap_err();
    assert!(error.choices.contains(&"2".into()));
}

#[test]
fn r5_session_fragments_and_steps() {
    let step =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/mounting_plate.step");
    let mut s = Session::open(&step, Features::Native, spec()).unwrap();
    assert!(!s.svg_fragments(None).contains_key("v:iso"));
    let second = s.svg_fragments_on_sheet(None, 2).unwrap();
    assert!(second.contains_key("v:iso"));
    assert!(!second.contains_key("v:front"));
    assert!(second.keys().all(|id| id == "v:iso" || id == "furniture"));
    assert!(s.svg_fragments_on_sheet(None, 3).is_err());
    assert_eq!(
        s.drawing_on_sheet(2)
            .unwrap()
            .sheet
            .views
            .iter()
            .map(|v| v.name.as_str())
            .collect::<Vec<_>>(),
        ["v:iso"]
    );
    let mut replay = s.state().clone();
    // The baseline and the first usable smallest-first trial record two recovered
    // runs for this explicitly assigned drawing. Replaying the prepared prefix costs none.
    assert_eq!(s.stats().sheet_steps, 2);
    let before = s.stats();
    s.apply(&[Op::set("views/iso/sheet", json!(1))], false)
        .unwrap();
    assert_eq!(s.stats(), before);
    let delta = s
        .apply(&[Op::set("views/iso/sheet", json!(1))], true)
        .unwrap();
    delta.apply_to(&mut replay).unwrap();
    assert_eq!(
        serde_json::to_value(&replay).unwrap(),
        serde_json::to_value(s.state()).unwrap()
    );
    // Preview prepared this exact single-sheet prefix; commit reuses the finished result.
    assert_eq!(s.stats().sheet_steps, before.sheet_steps);
    s.undo().unwrap().apply_to(&mut replay).unwrap();
    let font = draftwright::text::Font::new(draftwright::api::MONO.to_vec()).unwrap();
    assert_eq!(
        replay
            .render_sheet(
                s.part(),
                s.analysis(),
                &font,
                &mut draftwright::model::Projections::default(),
                2
            )
            .unwrap()
            .svg(),
        s.drawing_on_sheet(2).unwrap().svg()
    );
    s.redo().unwrap().apply_to(&mut replay).unwrap();
    assert_eq!(
        serde_json::to_value(&replay).unwrap(),
        serde_json::to_value(s.state()).unwrap()
    );
}

#[test]
fn r5_schemas_admit_all_sheet_controls() {
    let input = json!({"part_ref":"key","spec":{"sheets":{"max":3,"smallest_scale":"1:2","keep_together":[["side","section_aa"]],"hole_table":2,"gdt_on_sheet_1":true}},"filter":{"sheet":2,"near":"sheet:2/v:iso"}});
    mcp::validate(&mcp::schema("list_items", false).unwrap(), &input).unwrap();
    for sheet in [0, 10] {
        assert!(
            mcp::validate(
                &mcp::schema("render", false).unwrap(),
                &json!({"part_ref":"key","sheet":sheet})
            )
            .is_err()
        );
    }
}

#[test]
fn projection_alignment_options_follow_the_anchor_and_refusals_name_valid_choices() {
    let (cache, key) = open("projection-alignment");
    let ordinary = json!(["start", "middle", "end", "outside"]);
    let projected = json!(["projection", "start", "middle", "end", "outside"]);
    for view in ["front", "plan", "side", "iso"] {
        let options = checked(
            &cache,
            "item_options",
            json!({"part_ref":key,"id":format!("v:{view}")}),
        );
        assert_eq!(
            options["settable"][format!("views/{view}/place/align")]["choices"],
            if view == "iso" { &ordinary } else { &projected }.clone()
        );
    }
    for (view, anchor, side, applies) in [
        ("plan", "front", "above", true),
        ("side", "front", "right", true),
        ("front", "plan", "below", true),
        ("front", "side", "left", true),
        ("plan", "side", "above", false),
        ("side", "plan", "right", false),
        ("iso", "front", "right", false),
        ("plan", "iso", "above", false),
    ] {
        let spec =
            json!({"views":{view:{"place":{"relative_to":anchor,"side":side,"align":"middle"}}}});
        let options = checked(
            &cache,
            "item_options",
            json!({"part_ref":key,"spec":spec,"id":format!("v:{view}")}),
        );
        assert_eq!(
            options["settable"][format!("views/{view}/place/align")]["choices"],
            if applies { &projected } else { &ordinary }.clone(),
            "{view} against {anchor}"
        );
        let args = json!({"part_ref":key,"spec":spec,"ops":[{"op":"set","path":format!("views/{view}/place"),"value":{"relative_to":anchor,"side":side,"align":"projection","gap":15,"pinned":true}}]});
        if applies {
            checked(&cache, "preview_ops", args);
        } else {
            let error = mcp::call(&cache, "preview_ops", &args).unwrap_err();
            assert_eq!(error.code, "invalid");
            assert!(error.message.contains("cannot be projection"), "{error}");
            assert!(
                error
                    .message
                    .contains("valid choices are start, middle, end, outside"),
                "{error}"
            );
            assert_eq!(serde_json::to_value(error.choices).unwrap(), ordinary);
        }
    }
}
