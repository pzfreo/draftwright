#[path = "common/corpus.rs"]
mod corpus;
use draftwright::{mcp, stateless::Cache};
use serde_json::{Value, json};
use std::path::Path;

fn open(name: &str, part: &str) -> (Cache, Value) {
    let root = Path::new(env!("CARGO_TARGET_TMPDIR")).join(format!("e2-{name}"));
    let _ = std::fs::remove_dir_all(&root);
    let cache = Cache::new(root);
    let step = match part {
        "plate" => {
            Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/mounting_plate.step")
        }
        "m10127" => corpus::dir().unwrap().join("mfcadpp/10127.step"),
        "grm03" => corpus::dir()
            .unwrap()
            .join("gramel/GRM-03_thumbwheel_drive_screw.step"),
        _ => corpus::dir().unwrap().join("nist/nist_ctc_01_asme1_rd.stp"),
    };
    let feature = match part {
        "plate" => "plate",
        "m10127" => "10127",
        "grm03" => "GRM-03_thumbwheel_drive_screw",
        _ => "nist_ctc_01_asme1_rd",
    };
    let summary = call(
        &cache,
        "inspect_part",
        json!({"step":step,"features":format!("tools/mcp-eval/features/{feature}.features.json")}),
    );
    (cache, summary)
}
fn call(cache: &Cache, tool: &str, args: Value) -> Value {
    let reply = mcp::reply(cache, &json!({"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":tool,"arguments":args}})).unwrap();
    assert_eq!(reply["result"]["isError"], false, "{reply}");
    let wire_bytes = serde_json::to_vec(&reply).unwrap().len();
    assert!(wire_bytes < 4096, "{tool}: {wire_bytes} bytes");
    eprintln!("E2_WIRE {tool} {wire_bytes}");
    let result = reply["result"]["structuredContent"].clone();
    mcp::validate(&mcp::schema(tool, true).unwrap(), &result).unwrap();
    result
}
fn query(summary: &Value, id: &str) -> Value {
    json!({"part_ref":summary["part_ref"],"spec_version":summary["spec_version"],"id":id})
}

#[test]
fn summary_points_to_live_task_items_within_wire_budget() {
    for part in ["plate", "m10127", "grm03", "ctc01"] {
        let (cache, summary) = open(part, part);
        let keys = summary["key_items"]
            .as_array()
            .expect("summary includes key items");
        assert!(!keys.is_empty());
        for item in keys {
            call(
                &cache,
                "item_options",
                query(&summary, item["id"].as_str().unwrap()),
            );
            call(
                &cache,
                "get_item",
                query(&summary, item["id"].as_str().unwrap()),
            );
        }
        let repeated = call(
            &cache,
            "inspect_part",
            json!({"part_ref":summary["part_ref"],"spec_version":summary["spec_version"]}),
        );
        assert_eq!(summary, repeated);
        if part != "grm03" {
            assert!(keys.iter().any(|i| i["id"] == "d:envelope/width.length"));
        }
        if part == "m10127" {
            assert!(keys.iter().any(|i| i["id"] == "d:f:hole:f3/bore.diameter"));
        }
    }
}

#[test]
fn options_list_valid_hole_fits_and_every_dimension_field() {
    let (cache, summary) = open("fits", "m10127");
    let result = call(
        &cache,
        "item_options",
        query(&summary, "d:f:hole:f3/bore.diameter"),
    );
    let fields = &result["settable"];
    let fits = fields["features/f:hole:f3/fit"]["choices"]
        .as_array()
        .expect("hole fit choices");
    assert_eq!(fits.len(), 21);
    assert!(fits.contains(&json!("H7")));
    assert!(!fits.contains(&json!("g6")));
    for fit in fits {
        call(
            &cache,
            "preview_ops",
            json!({"part_ref":summary["part_ref"],"spec_version":summary["spec_version"],"ops":[{"op":"set","path":"features/f:hole:f3/fit","value":fit}]}),
        );
    }
    for field in [
        "omit",
        "tolerance",
        "view",
        "place/view",
        "place/side",
        "place/lane",
        "place/along",
        "place/pinned",
    ] {
        assert!(
            fields
                .get(format!("dimensions/d:f:hole:f3~1bore.diameter/{field}"))
                .is_some(),
            "{field}"
        );
    }
    assert_eq!(
        fields["dimensions/d:f:hole:f3~1bore.diameter/tolerance"]["forms"],
        json!(["±T", "+U -L"])
    );
}

#[test]
fn options_explain_sheet_and_bare_view_keys() {
    let (cache, summary) = open("sheet-view", "plate");
    let view = call(&cache, "item_options", query(&summary, "v:iso"));
    assert!(view["settable"].get("views/iso/omit").is_some());
    for field in ["relative_to", "side", "gap", "align", "pinned"] {
        assert!(
            view["settable"]
                .get(format!("views/iso/place/{field}"))
                .is_some()
        );
    }
    let description = view["settable"]["views/iso/place/gap"]["consequences"][0]
        .as_str()
        .unwrap();
    assert!(
        description.contains("annotation bands") && description.contains("not their part outlines")
    );
    let sheet = call(&cache, "item_options", query(&summary, "sheet"));
    for field in [
        "size",
        "scale",
        "title",
        "fit_limits",
        "fit_deviations",
        "references",
    ] {
        assert!(sheet["settable"].get(format!("sheet/{field}")).is_some());
    }
    assert!(sheet["settable"].get("sheet/table_columns").is_some());
    assert!(
        sheet["settable"]["sheet/title"]["fields"]
            .as_array()
            .unwrap()
            .contains(&json!("number"))
    );
}

#[test]
fn refusals_give_valid_choices_and_an_executable_corrected_op() {
    let (cache, summary) = open("refusals", "m10127");
    for bad in [
        json!({"op":"set","path":"dimensions/d:f:hole:f3~1bore.diameter/tolerance","value":"H7"}),
        json!({"op":"set","path":"dimensions/d:envelope~1width.length/tolerance","value":{"symmetric":0.02}}),
        json!({"op":"set","path":"views/v:iso/omit","value":true}),
        json!({"op":"set","path":"sheet/title/drawing_number","value":"DW-0001"}),
    ] {
        let args = json!({"part_ref":summary["part_ref"],"spec_version":summary["spec_version"],"ops":[bad]});
        let error = mcp::call(&cache, "preview_ops", &args).unwrap_err();
        assert!(!error.choices.is_empty(), "{error}");
        let (_, correction) = error
            .message
            .split_once("Corrected op: ")
            .expect("refusal includes correction");
        let corrected: Value = serde_json::from_str(correction).unwrap();
        call(
            &cache,
            "preview_ops",
            json!({"part_ref":summary["part_ref"],"spec_version":summary["spec_version"],"ops":[corrected]}),
        );
    }
    let preview = call(
        &cache,
        "preview_ops",
        json!({"part_ref":summary["part_ref"],"spec_version":summary["spec_version"],"ops":[
            {"op":"set","path":"dimensions/d:f:hole:f3~1bore.diameter/fit","value":"H7"}
        ]}),
    );
    assert!(
        preview["events"]["items"]
            .as_array()
            .unwrap()
            .iter()
            .any(|e| {
                let item = &e["item"];
                item["id"] == "d:f:hole:f3/bore.diameter"
                    && item["state"]["kind"] == "shown"
                    && item["label"].as_str().is_some_and(|s| s.contains("H7"))
            })
    );
    assert_eq!(
        cache
            .head(summary["part_ref"].as_str().unwrap(), "default")
            .unwrap(),
        summary["spec_version"]
    );
}

#[test]
fn one_feature_problem_query_returns_all_member_fixes() {
    let (cache, summary) = open("feature-problems", "ctc01");
    // Use authored bad pins to exercise member fixes. The two historical
    // corridor overruns are correctly eliminated by ISSUE-563-b.
    let edited = call(
        &cache,
        "apply_ops",
        json!({"part_ref":summary["part_ref"], "parent":summary["spec_version"], "ops":[
            {"op":"set", "path":"dimensions/d:f:slot:f4~1slot_length.length/place", "value":{"side":"below", "lane":99, "pinned":true}},
            {"op":"set", "path":"dimensions/d:f:slot:f4~1slot_width.length/place", "value":{"side":"left", "lane":99, "pinned":true}}
        ]}),
    );
    let version = &edited["spec_version"];
    let mut cursor = 0;
    let mut problems = Vec::new();
    loop {
        let result = call(
            &cache,
            "list_problems",
            json!({
                "part_ref":summary["part_ref"], "spec_version":version,
                "filter":{"feature":"f:slot:f4"}, "cursor":cursor
            }),
        );
        for row in result["items"].as_array().unwrap() {
            problems.push(if let Some(key) = row["detail_ref"].as_str() {
                serde_json::from_slice::<Value>(&cache.artifact_bytes(key).unwrap()).unwrap()
            } else {
                row.clone()
            });
        }
        let Some(next) = result["next_cursor"].as_u64() else {
            break;
        };
        assert!(next > cursor);
        cursor = next;
    }
    assert_eq!(
        problems
            .iter()
            .filter(|p| p["code"] == "annotation_off_sheet")
            .count(),
        2
    );
    assert!(
        problems
            .iter()
            .all(|p| p["code"] != "annotation_outside_area")
    );
    // A later radius repair must not erase the pinned members' reset remedies
    // when it replaces their collision records during the final audit.
    for p in problems
        .iter()
        .filter(|p| p["code"] == "annotation_off_sheet")
    {
        assert!(!p["fix"].as_array().unwrap().is_empty(), "{p}");
    }
    let mut ops = Vec::new();
    for p in &problems {
        ops.extend(
            p["fix"]
                .as_array()
                .map(Vec::as_slice)
                .unwrap_or(&[])
                .iter()
                .cloned(),
        );
    }
    assert!(!ops.is_empty());
    let preview = call(
        &cache,
        "preview_ops",
        json!({"part_ref":summary["part_ref"],"spec_version":version,"ops":ops}),
    );
    let delta: Value = serde_json::from_slice(
        &cache
            .artifact_bytes(preview["delta_ref"].as_str().unwrap())
            .unwrap(),
    )
    .unwrap();
    let added = delta["problems_added"].as_array().unwrap();
    let resolved = delta["problems_resolved"].as_array().unwrap();
    let gone: Vec<_> = resolved
        .iter()
        .filter(|p| !added.iter().any(|q| q["id"] == p["id"]))
        .collect();
    // The new local pitch fix also clears both slot crossings. (With lanes compacted the fixes
    // also took the 100 slot length's extension off the ⌀35 hole; in the 3 mm frame gutter's
    // layout, 2026-10-25, that crossing stays.) Other records can refresh their scoped
    // alternatives without becoming new physical findings. #226: the same fixes also clear
    // two label_crowded notices (line-work within 0.5 mm of 650 and 370). With #223–#225's
    // CTC-01 changes the 4× R25 label is no longer crowded before the fixes, so it has none
    // to clear.
    assert_eq!(gone.len(), 8);
    assert_eq!(
        gone.iter().filter(|p| p["code"] == "label_crowded").count(),
        2
    );
    for p in &problems {
        assert!(gone.iter().any(|resolved| resolved["id"] == p["id"]));
    }
    assert!(
        added
            .iter()
            .all(|p| resolved.iter().any(|q| q["id"] == p["id"]))
    );
}

// A verbose scoped remedy must not turn a real warning into an ID-only row.
// Follow the public default bundle plus the editor's explicit opposite-side
// placement, as a client fixing every problem involving this feature does.
#[test]
fn verbose_feature_problem_stays_actionable_and_can_be_cleared() {
    let (cache, summary) = open("actionable-feature", "ctc01");
    let query = json!({"part_ref":summary["part_ref"],
        "spec_version":summary["spec_version"],"problem_filter":{"feature":"f:slot:f4"}});
    let inspected = call(&cache, "inspect_part", query);
    let rows = inspected["problem_page"]["items"].as_array().unwrap();
    assert_eq!(rows.len(), 1);
    let row = &rows[0];
    assert_eq!(row["code"], "extension_through_feature");
    assert_eq!(row["impact"], "advisory");
    assert!(row["group"].as_str().is_some_and(|group| !group.is_empty()));
    assert!(
        row["subjects"]
            .as_array()
            .unwrap()
            .contains(&json!("f:slot:f4"))
    );
    let mut ops = row["fix"]
        .as_array()
        .expect("inline actionable fix")
        .clone();
    assert!(!ops.is_empty());
    if row.get("detail_ref").is_some() {
        assert_eq!(row["scope"], "local");
        // The verbose row keeps the local remedy's wording: the move, with any findings it adds
        // named after "Trade-off:". Since the grid callouts lost their "(2×2)" (#225) the 650's
        // move below the plan adds none.
        let description = row["description"].as_str().unwrap();
        assert!(
            description.starts_with("Move the 650 dimension below the plan view"),
            "{row}"
        );
    }
    let answer = call(
        &cache,
        "get_item",
        json!({"part_ref":summary["part_ref"],
        "spec_version":summary["spec_version"],"id":row["subjects"][0],"detail":true}),
    );
    let p = &answer["item"]["placement"]["geometry"];
    let line: usize = row["id"]
        .as_str()
        .unwrap()
        .rsplit(':')
        .next()
        .unwrap()
        .parse()
        .unwrap();
    let horizontal = p["rotation"].as_f64().unwrap().rem_euclid(180.0).abs() < 1e-6;
    let k = usize::from(horizontal);
    let outward =
        p["lines"][line][1][k].as_f64().unwrap() > p["lines"][line][0][k].as_f64().unwrap();
    let side = match (horizontal, outward) {
        (true, true) => "below",
        (true, false) => "above",
        (false, true) => "left",
        (false, false) => "right",
    };
    let id = row["subjects"][0].as_str().unwrap();
    ops.push(
        json!({"op":"set","path":format!("dimensions/{}/place", draftwright::items::key(id)),
        "value":{"view":answer["item"]["item"]["view"].as_str().unwrap().trim_start_matches("v:"),
        "side":side,"lane":0}}),
    );
    let applied = call(
        &cache,
        "apply_ops",
        json!({"part_ref":summary["part_ref"],
        "parent":summary["spec_version"],"ops":ops}),
    );
    let remaining = call(
        &cache,
        "list_problems",
        json!({"part_ref":summary["part_ref"],
        "spec_version":applied["spec_version"],"filter":{"feature":"f:slot:f4"}}),
    );
    assert_eq!(remaining["total"], 0, "{remaining}");
    let delta: Value = serde_json::from_slice(
        &cache
            .artifact_bytes(applied["delta"]["delta_ref"].as_str().unwrap())
            .unwrap(),
    )
    .unwrap();
    assert!(
        delta["removed"].as_array().unwrap().is_empty(),
        "no dimension is hidden"
    );
}

#[test]
fn inspection_batches_a_filtered_problem_page_with_registration() {
    let (cache, summary) = open("inspect-problems", "ctc01");
    let result = call(
        &cache,
        "inspect_part",
        json!({"part_ref":summary["part_ref"],"spec_version":summary["spec_version"],"problem_filter":{"feature":"f:slot:f4"}}),
    );
    let problems = call(
        &cache,
        "list_problems",
        json!({"part_ref":summary["part_ref"],"spec_version":summary["spec_version"],"filter":{"feature":"f:slot:f4"}}),
    );
    assert_eq!(result["problem_page"], problems);
    let actual: Vec<Value> = result["problem_page"]["items"]
        .as_array()
        .unwrap()
        .iter()
        .map(|row| {
            if let Some(key) = row["detail_ref"].as_str() {
                serde_json::from_slice(&cache.artifact_bytes(key).unwrap()).unwrap()
            } else {
                row.clone()
            }
        })
        .collect();
    assert_eq!(actual.len(), 1);
    assert!(
        actual
            .iter()
            .all(|p| p["code"] == "extension_through_feature")
    );
    let all = call(
        &cache,
        "inspect_part",
        json!({"part_ref":summary["part_ref"],"spec_version":summary["spec_version"],"problem_filter":{}}),
    );
    let mut found = all["problem_page"]["items"].as_array().unwrap().clone();
    let mut cursor = all["problem_page"]["next_cursor"].clone();
    while !cursor.is_null() {
        let page = call(
            &cache,
            "list_problems",
            json!({"part_ref":summary["part_ref"],"spec_version":summary["spec_version"],"cursor":cursor}),
        );
        assert!(!page["items"].as_array().unwrap().is_empty());
        found.extend(page["items"].as_array().unwrap().iter().cloned());
        cursor = page["next_cursor"].clone();
    }
    assert_eq!(
        found.len(),
        all["problem_page"]["total"].as_u64().unwrap() as usize
    );
    let session = cache
        .session(
            summary["part_ref"].as_str().unwrap(),
            &cache
                .get_version(summary["spec_version"].as_str().unwrap())
                .unwrap(),
        )
        .unwrap();
    assert_eq!(
        found
            .iter()
            .map(|p| p["id"].as_str().unwrap())
            .collect::<Vec<_>>(),
        session
            .state()
            .problems
            .iter()
            .map(|p| p.id.as_str())
            .collect::<Vec<_>>()
    );
}

#[test]
fn fit_options_respect_nominal_table_boundaries() {
    for nominal in [0.0, 0.001, 250.0, 250.001] {
        let step = corpus::dir().unwrap().join("mfcadpp/10127.step");
        let (part, mut analysis) = draftwright::model::analyse_native(&step).unwrap();
        let hole = analysis
            .features
            .iter_mut()
            .find(|f| f.kind == "hole")
            .unwrap();
        let id = hole.id.clone();
        hole.parameters.insert("diameter".into(), json!(nominal));
        let session =
            draftwright::Session::new(part, analysis, draftwright::model::Spec::default()).unwrap();
        let options =
            serde_json::to_value(session.options(&format!("d:{id}/bore.diameter")).unwrap())
                .unwrap();
        let fields = options["settable"].as_object().expect("settable paths");
        let fits = fields.get(&format!("features/{id}/fit"));
        assert_eq!(
            fits.is_some(),
            nominal > 0.0 && nominal <= 250.0,
            "{nominal}"
        );
    }
}

#[test]
fn batch_refusal_corrects_the_named_failure_and_reports_its_index() {
    let (cache, summary) = open("batch-refusal", "plate");
    let title = json!({"op":"set","path":"sheet/title/drawing_number","value":"DW-0001"});
    for ops in [
        json!([{"op":"set","path":"sheet/scale","value":"1:1"}, title]),
        json!([{"op":"set","path":"sheet/title/title","value":"PLATE"}, title]),
    ] {
        let error = mcp::call(
            &cache,
            "preview_ops",
            &json!({"part_ref":summary["part_ref"],"spec_version":summary["spec_version"],"ops":ops}),
        ).unwrap_err();
        let (_, correction) = error.message.split_once("Corrected op: ").unwrap();
        let corrected: Value = serde_json::from_str(correction).unwrap();
        assert!(error.message.contains("Replace ops[1]."), "{error}");
        assert_eq!(corrected["path"], "sheet/title/number");
    }
    let error = mcp::call(
        &cache, "preview_ops",
        &json!({"part_ref":summary["part_ref"],"spec_version":summary["spec_version"],"ops":[title,
            {"op":"set","path":"dimensions/d:envelope~1width.length/tolerance","value":{"symmetric":0.02}}
        ]}),
    ).unwrap_err();
    assert!(error.message.contains("Replace ops[1]."), "{error}");
    let (_, correction) = error.message.split_once("Corrected op: ").unwrap();
    let corrected: Value = serde_json::from_str(correction).unwrap();
    assert_eq!(corrected["value"], "±0.02");
    let error = mcp::call(
        &cache,
        "preview_ops",
        &json!({"part_ref":summary["part_ref"],"spec_version":summary["spec_version"],"ops":[
            {"op":"set","path":"dimensions/d:envelope~1width.length/tolerance","value":"nonsense"}, title
        ]}),
    ).unwrap_err();
    assert!(!error.message.contains("Corrected op:"), "{error}");
}

#[test]
fn compact_nullable_schema_types_keep_validation_strict() {
    // Published inputs are shallow; server preflight retains the original strict shape.
    let preflight: Value =
        serde_json::from_str(include_str!("../schemas/mcp-validation.json")).unwrap();
    let schema = &preflight["inspect_part"];
    for spec in [
        json!({"sheet":{"size":null,"fit_limits":null}}),
        json!({"sheet":{"size":"A4","fit_limits":true}}),
    ] {
        mcp::validate(schema, &json!({"part_ref":"part","spec":spec})).unwrap();
    }
    for spec in [
        json!({"sheet":{"size":42}}),
        json!({"sheet":{"fit_limits":"yes"}}),
    ] {
        assert!(mcp::validate(schema, &json!({"part_ref":"part","spec":spec})).is_err());
    }
    for (types, accepted, refused) in [
        (json!(["string", "null"]), json!("A4"), json!(42)),
        (json!(["number", "null"]), json!(0.02), json!("0.02")),
        (json!(["boolean", "null"]), json!(true), json!([])),
        (json!(["object", "null"]), json!({}), json!([])),
    ] {
        let schema = json!({"type":types});
        mcp::validate(&schema, &accepted).unwrap();
        mcp::validate(&schema, &Value::Null).unwrap();
        assert!(mcp::validate(&schema, &refused).is_err());
    }
}

#[test]
fn shared_schema_types_preserve_state_alternatives_and_detail_placements() {
    let schema = mcp::schema("list_items", false).unwrap();
    for state in [
        Value::Null,
        json!({"kind":"shown"}),
        json!({"kind":"covered", "by":"d:owner"}),
        json!({"kind":"not_drawable", "reason_code":"unsupported"}),
    ] {
        mcp::validate(
            &schema,
            &json!({"part_ref":"part", "filter":{"state":state}}),
        )
        .unwrap();
    }
    for state in [
        json!("shown"),
        json!([]),
        json!({}),
        json!({"kind":"covered"}),
        json!({"kind":"not_drawable"}),
        json!({"kind":"shown", "extra":true}),
    ] {
        assert!(
            mcp::validate(
                &schema,
                &json!({"part_ref":"part", "filter":{"state":state}})
            )
            .is_err()
        );
    }
    let schema = mcp::schema("item_options", true).unwrap();
    let mut options = json!({"id":"dimension", "operations":[], "placements":[],
        "view_anchors":[], "scales":[], "settable":{}});
    for view in ["front", "side", "detail_a", "detail_h"] {
        options["placements"] = json!([{"view":view, "side":"above"}]);
        mcp::validate(&schema, &options).unwrap();
    }
    options["placements"] = json!([{"view":"detail_i", "side":"above"}]);
    assert!(mcp::validate(&schema, &options).is_err());
}

#[test]
fn compact_prefix_suffix_choices_and_authored_dimension_outputs_stay_strict() {
    let preflight: Value =
        serde_json::from_str(include_str!("../schemas/mcp-validation.json")).unwrap();
    let input = &preflight["inspect_part"];
    for name in [
        "fit_limits",
        "fit_deviations",
        "centre_marks",
        "centre_lines",
        "thread_depiction",
        "knurl_depiction",
    ] {
        for value in [json!(true), json!(false), Value::Null] {
            mcp::validate(
                input,
                &json!({"part_ref":"part","spec":{"sheet":{name:value}}}),
            )
            .unwrap();
        }
        for bad_name in [format!("{name}\n"), format!("{name}_extra")] {
            assert!(
                mcp::validate(
                    input,
                    &json!({"part_ref":"part","spec":{"sheet":{bad_name:true}}})
                )
                .is_err()
            );
        }
        assert!(
            mcp::validate(
                input,
                &json!({"part_ref":"part","spec":{"sheet":{name:"yes"}}})
            )
            .is_err()
        );
    }
    for name in [
        "thread_lines",
        "knurl_marks",
        "centre_depiction",
        "_depiction",
    ] {
        assert!(
            mcp::validate(
                input,
                &json!({"part_ref":"part","spec":{"sheet":{name:true}}})
            )
            .is_err()
        );
    }
    let discovered = mcp::call(&Cache::new("unused"), "spec_schema", &json!({})).unwrap();
    let output = json!({"type":"object", "properties":{
        "spec_version":{"type":"string"}, "spec":{"$ref":discovered["spec_read"]}},
        "required":["spec_version","spec"], "additionalProperties":false,
        "$defs":discovered["spec"]["$defs"]});
    let result = |dimension| {
        json!({"spec_version":"v","spec":{
            "dimensions":{"d:authored:test":dimension},
            "feature_declarations":[{"id":"f:declared:test","kind":"hole","faces":[]}]
        }})
    };
    mcp::validate(
        &output,
        &result(json!({"kind":"linear","between":["a","b"],"along":"true"})),
    )
    .unwrap();
    for dimension in [
        json!([]),
        Value::Null,
        json!({"kind":"bad"}),
        json!({"between":[]}),
        json!({"between":["a","b","c"]}),
        json!({"between":[1]}),
        json!({"along":"bad"}),
    ] {
        assert!(mcp::validate(&output, &result(dimension)).is_err());
    }
}

#[test]
fn crossing_remedies_are_scoped_and_ordered_in_mcp_rows() {
    let Some(root) = corpus::dir() else { return };
    let cache = Cache::new(Path::new(env!("CARGO_TARGET_TMPDIR")).join("fix-scope-mcp"));
    let spec = json!({"sheet":{"size":"A3","scale":"1:5"}});
    let summary = call(
        &cache,
        "inspect_part",
        json!({
            "step":root.join("nist/nist_ctc_01_asme1_rd.stp"), "spec":spec
        }),
    );
    for detail in [false, true] {
        for code in ["annotation_leader_crossing", "extension_through_feature"] {
            let mut cursor = 0;
            let mut count = 0;
            let mut local_count = 0;
            loop {
                let rows = call(
                    &cache,
                    "list_problems",
                    json!({
                        "part_ref":summary["part_ref"], "spec":spec,
                        "filter":{"code":code}, "detail":detail,"cursor":cursor
                    }),
                );
                for row in rows["items"].as_array().unwrap() {
                    let full;
                    let row = if let Some(key) = row["detail_ref"].as_str() {
                        full = serde_json::from_slice::<Value>(&cache.artifact_bytes(key).unwrap())
                            .unwrap();
                        &full
                    } else {
                        row
                    };
                    let fixes = row["fixes"].as_array().unwrap();
                    assert!(!fixes.is_empty(), "{row}");
                    // Radius-table carriers cannot promise information retention
                    // from the bounded trial; only the front-view hole location
                    // remains local. Keep the wire contract as strict as Session.
                    let local = row["id"]
                        .as_str()
                        .unwrap()
                        .contains("d:f:hole:f73/location.length.z");
                    // CTC-01 plans no radius-centre table (its 4× R25 leader is not
                    // obstructed), so no row is a table carrier and every first offer is local
                    // (on alpha.12 only the front-view hole location's was).
                    assert_eq!(fixes[0]["scope"], "local");
                    if local {
                        local_count += 1;
                        let ops = fixes[0]["ops"].as_array().unwrap();
                        for (path, value) in [
                            ("sheet/keep_layout", json!(true)),
                            ("sheet/size", json!("A3")),
                            ("sheet/scale", json!("1:5")),
                            ("sheets/max", json!(1)),
                        ] {
                            let constraints: Vec<_> =
                                ops.iter().filter(|op| op["path"] == path).collect();
                            assert_eq!(constraints.len(), 1);
                            assert_eq!(constraints[0]["value"], value);
                        }
                        assert!(
                            fixes[0]["description"]
                                .as_str()
                                .unwrap()
                                .contains("extension_through_feature")
                        );
                    } else {
                        // The local offer first; the others (the hole-table sheet remedy,
                        // checked below) re-lay the whole sheet.
                        assert!(fixes[1..].iter().all(|fix| fix["scope"] == "sheet"));
                    }
                    assert_eq!(row["fix"], fixes[0]["ops"]);
                    let rank = |fix: &Value| match fix["scope"].as_str().unwrap() {
                        "local" => 0,
                        "view" => 1,
                        "sheet" => 2,
                        _ => panic!("{fix}"),
                    };
                    assert!(fixes.windows(2).all(|f| rank(&f[0]) <= rank(&f[1])));
                    let sheet = fixes
                        .iter()
                        .find(|f| {
                            f["ops"]
                                .as_array()
                                .unwrap()
                                .iter()
                                .any(|op| op["path"] == "hole_table")
                        })
                        .unwrap();
                    assert_eq!(sheet["scope"], "sheet");
                    assert!(
                        sheet["description"]
                            .as_str()
                            .unwrap()
                            .contains("The whole sheet is laid out again")
                    );
                    count += 1;
                }
                let Some(next) = rows["next_cursor"].as_u64() else {
                    break;
                };
                cursor = next;
            }
            assert_eq!(
                local_count,
                if code == "extension_through_feature" {
                    5
                } else {
                    0
                }
            );
            assert_eq!(
                count,
                match code {
                    // No leader crosses the 350 row pitch's extension lines, which start at
                    // the grid's right-hand column (two did on alpha.12, from the left). The
                    // extension rows still exercise the remedies.
                    "annotation_leader_crossing" => 0,
                    "extension_through_feature" => 9,
                    _ => unreachable!(),
                }
            );
        }
    }
    let options = call(
        &cache,
        "item_options",
        json!({"part_ref":summary["part_ref"],"spec":spec,"id":"sheet"}),
    );
    assert_eq!(options["settable"]["hole_table"]["scope"], "sheet");
    assert_eq!(options["operation_scopes"]["scale"], "sheet");
}

#[test]
fn an_oversized_trade_keeps_the_finding_inline_and_the_remedy_lossless() {
    let root = Path::new(env!("CARGO_TARGET_TMPDIR")).join("e2-long-trade");
    let _ = std::fs::remove_dir_all(&root);
    let cache = Cache::new(root);
    let ops = json!([{"op":"set", "path":"dimensions/d:example/place", "value":{"side":"below"}}]);
    let description = "Trade-off: adds an extension crossing. ".repeat(100);
    let row = json!({"id":"p:example", "code":"extension_through_feature",
        "severity":"warning", "impact":"readability", "group":"extension-path",
        "subjects":["d:example", "f:slot:f4"], "fix":ops,
        "fixes":[{"scope":"local", "description":description, "ops":ops}]});
    let page = mcp::page(&cache, vec![row.clone()], &json!({})).unwrap();
    let inline = &page["items"][0];
    assert_eq!(inline["code"], row["code"]);
    assert_eq!(inline["subjects"], row["subjects"]);
    assert_eq!(inline["impact"], row["impact"]);
    assert_eq!(inline["group"], row["group"]);
    assert!(
        inline.get("fix").is_none(),
        "never offer an undisclosed trade"
    );
    let full: Value = serde_json::from_slice(
        &cache
            .artifact_bytes(inline["detail_ref"].as_str().unwrap())
            .unwrap(),
    )
    .unwrap();
    assert_eq!(
        full, row,
        "every alternative and its complete description remain available"
    );
    mcp::validate(&mcp::schema("list_problems", true).unwrap(), &page).unwrap();
}
