use std::path::Path;

use draftwright::{mcp, model, stateless::Cache};
use serde_json::{Value, json};

fn discovery() -> Value {
    let cache = Cache::new("unused-schema-cache");
    let reply = mcp::reply(
        &cache,
        &json!({"jsonrpc":"2.0","id":1,"method":"tools/call",
                "params":{"name":"spec_schema","arguments":{}}}),
    )
    .unwrap();
    assert_eq!(reply["result"]["isError"], false);
    let result = reply["result"]["structuredContent"].clone();
    mcp::validate(&mcp::schema("spec_schema", true).unwrap(), &result).unwrap();
    assert_eq!(result["spec_read"], "#/$defs/spec_read");
    assert_eq!(result["spec"]["additionalProperties"], false);
    assert_eq!(
        result["spec"]["$schema"],
        "https://json-schema.org/draft/2020-12/schema"
    );
    // Text-only MCP clients get exactly the same document.
    let text: Value =
        serde_json::from_str(reply["result"]["content"][0]["text"].as_str().unwrap()).unwrap();
    assert_eq!(text, result);
    result
}

#[test]
fn schema_discovery_needs_no_part_or_cache_and_refuses_arguments() {
    let result = discovery();
    // The generated read shape shares the complete process contract, including its choices.
    assert_eq!(
        result["spec"]["$defs"]["spec_read"]["properties"]["process"],
        result["spec"]["properties"]["process"]
    );
    assert_eq!(
        result["spec"]["properties"]["process"]["enum"],
        json!([
            "machined",
            "3D printed: FDM",
            "3D printed: resin",
            "3D printed: powder",
            "3D printed: metal",
            "sheet metal",
            "moulded",
            "cast",
            null
        ])
    );
    assert_eq!(
        result["spec"]["$defs"]["sheet"]["properties"]["uncited_datums"]["anyOf"][0]["type"],
        "boolean"
    );
    let gap = result["spec"]["$defs"]["view_place"]["properties"]["gap"]["description"]
        .as_str()
        .unwrap();
    assert!(gap.contains("annotation bands") && gap.contains("not their part outlines"));
    assert_eq!(
        result["spec"]["properties"]["iso_scale_factor"]["anyOf"][0]["type"],
        "number"
    );
    assert!(
        mcp::call(
            &Cache::new("unused"),
            "spec_schema",
            &json!({"part_ref":"unused"})
        )
        .is_err()
    );
}

#[test]
fn known_bad_specs_are_refused_and_existing_preflight_guidance_is_preserved() {
    let result = discovery();
    let schema = &result["spec"];
    for bad in [
        json!(null),
        json!([]),
        json!({"unknown":true}),
        json!({"schema":"draftwright.spec/3"}),
        json!({"sheet":{"size":42}}),
        json!({"sheet":{"centre_marks":"false"}}),
        json!({"sheet":{"uncited_datums":"false"}}),
        json!({"sheet":{"projection":"second"}}),
        json!({"views":{"side":[]}}),
        json!({"dimensions":{"d:x":{"kind":"bad"}}}),
        json!({"dimensions":{"d:x":{"between":[]}}}),
        json!({"annotations":{"pmi:x":{"text":{"prefix":42}}}}),
        json!({"precision":{"d:x":16}}),
        json!({"feature_declarations":[{"id":"x","faces":[]}]}),
        json!({"dimension_schemes":{"x":{"scheme":"bad","axis":"x"}}}),
        json!({"tables":[{"id":"t:1","rows":[[null]]}]}),
    ] {
        assert!(mcp::validate(schema, &bad).is_err(), "{bad}");
    }
    let private: Value =
        serde_json::from_str(include_str!("../schemas/mcp-validation.json")).unwrap();
    for (tool, validation) in private.as_object().unwrap() {
        for bad in [
            json!({"sheet":{"size":42}}),
            json!({"sheet":{"centre_marks":"false"}}),
            json!({"sheet":{"uncited_datums":"false"}}),
            json!({"dimensions\n":{}}),
        ] {
            let mut args = json!({"part_ref":"unused","spec":bad});
            if ["get_item", "item_options"].contains(&tool.as_str()) {
                args["id"] = json!("x");
            }
            if ["preview_ops", "apply_ops"].contains(&tool.as_str()) {
                args["ops"] = json!([]);
            }
            if tool == "apply_ops" {
                args["parent"] = json!("unused");
            }
            let expected = mcp::validate(validation, &args).unwrap_err();
            assert!(expected.contains("input/spec"), "{tool}: {expected}");
            let actual = mcp::call(&Cache::new("unused"), tool, &args).unwrap_err();
            assert_eq!(actual.code, "invalid");
            assert_eq!(actual.message, expected, "{tool}");
        }
    }
}

#[test]
fn callout_styles_are_discovered_for_sheet_and_feature_without_context_growth() {
    let schema = discovery()["spec"].clone();
    for style in ["auto", "compact", "full", "note"] {
        let value = json!({"sheet":{"callouts":{"style":style}}, "features":{"f:turned_step:1":{"callouts":{"style":style}}}});
        mcp::validate(&schema, &value).unwrap();
        let _: model::Spec = serde_json::from_value(value).unwrap();
    }
    assert!(mcp::validate(&schema, &json!({"sheet":{"callouts":{"style":"wide"}}})).is_err());
}

#[test]
fn maximum_paper_is_discovered_and_removed_printer_points_to_its_replacement() {
    let schema = discovery()["spec"].clone();
    for maximum in ["A4", "A3", "A2", "A1", "A0"] {
        mcp::validate(&schema, &json!({"sheet":{"preferred_max_size":maximum}})).unwrap();
    }
    assert!(mcp::validate(&schema, &json!({"sheet":{"preferred_max_size":"A5"}})).is_err());
    mcp::validate(&schema, &json!({"sheet":{"preferred_max_sheets":3}})).unwrap();
    assert!(mcp::validate(&schema, &json!({"sheet":{"preferred_max_sheets":0}})).is_err());
    assert!(mcp::validate(&schema, &json!({"sheet":{"max_size":"A3"}})).is_err());
    let error = mcp::validate(&schema, &json!({"sheet":{"preferred_printer":"A4"}})).unwrap_err();
    assert!(error.contains("sheet/preferred_max_size"), "{error}");
    assert!(error.contains("A4 printer") && error.contains("A3 printer"));
    let private: Value =
        serde_json::from_str(include_str!("../schemas/mcp-validation.json")).unwrap();
    let error = mcp::validate(
        &private["inspect_part"],
        &json!({"part_ref":"unused", "spec":{"sheet":{"preferred_printer":"A3"}}}),
    )
    .unwrap_err();
    assert!(error.contains("sheet/preferred_max_size"), "{error}");
}

// Rust json! accepts trailing commas. Keep strings intact while normalizing literal
// examples for JSON parsing; expressions with runtime values are tested by their own
// session tests and are deliberately not guessed here.
fn literal_json(source: &str) -> Option<Value> {
    let mut normalized = String::new();
    let mut quoted = false;
    let mut escaped = false;
    let mut depth = 0usize;
    for (i, c) in source.char_indices() {
        if !quoted {
            if matches!(c, '{' | '[') {
                depth += 1;
            }
            if matches!(c, '}' | ']') {
                depth = depth.checked_sub(1)?;
            }
            if c == ',' && source[i + 1..].trim_start().starts_with(['}', ']']) {
                continue;
            }
        }
        normalized.push(c);
        if c == '"' && !escaped {
            quoted = !quoted;
        }
        escaped = quoted && c == '\\' && !escaped;
        if depth == 0 {
            return serde_json::from_str(&normalized).ok();
        }
    }
    None
}

// Serde can deserialize an empty JSON sequence as a defaulted struct. The public
// document contract requires objects (as main's preflight did), so such negative
// specimens are not valid authored-object examples even if their normalized Spec
// passes model checks.
fn authored_object_shapes(value: &Value, normalized: &Value) -> bool {
    match (value, normalized) {
        (Value::Object(object), Value::Object(normalized)) => object.iter().all(|(key, child)| {
            normalized
                .get(key)
                .is_none_or(|other| authored_object_shapes(child, other))
        }),
        (Value::Array(array), Value::Array(normalized)) => array
            .iter()
            .zip(normalized)
            .all(|(child, other)| authored_object_shapes(child, other)),
        (Value::Array(_), Value::Object(_)) | (Value::Object(_), Value::Array(_)) => false,
        _ => true,
    }
}

#[test]
fn all_literal_spec_examples_in_the_test_suite_validate_in_authored_and_read_shapes() {
    let result = discovery();
    let schema = &result["spec"];
    let mut read = schema.clone();
    read["$ref"] = result["spec_read"].clone();
    let keys = schema["properties"].as_object().unwrap();
    let mut count = 0;
    fn visit(
        value: &Value,
        keys: &serde_json::Map<String, Value>,
        schema: &Value,
        read: &Value,
        origin: &Path,
        count: &mut usize,
    ) {
        if let Some(object) = value.as_object() {
            if !object.is_empty()
                && object.keys().all(|key| keys.contains_key(key))
                && let Ok(spec) = serde_json::from_value::<model::Spec>(value.clone())
                && authored_object_shapes(value, &serde_json::to_value(&spec).unwrap())
                && model::edit(&spec, &json!([])).is_ok()
                && model::SPEC_SCHEMAS.contains(&spec.schema.as_str())
            {
                for shape in [schema, read] {
                    mcp::validate(shape, value)
                        .unwrap_or_else(|e| panic!("{}: {value}: {e}", origin.display()));
                    // Include all model defaults, not just fields written in the literal.
                    mcp::validate(shape, &serde_json::to_value(&spec).unwrap()).unwrap();
                }
                *count += 1;
            }
            for child in object.values() {
                visit(child, keys, schema, read, origin, count);
            }
        } else if let Some(array) = value.as_array() {
            for child in array {
                visit(child, keys, schema, read, origin, count);
            }
        }
    }
    let mut directories = vec![Path::new(env!("CARGO_MANIFEST_DIR")).join("tests")];
    while let Some(directory) = directories.pop() {
        for entry in std::fs::read_dir(directory).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                directories.push(path);
                continue;
            }
            if path.extension().is_some_and(|ext| ext == "rs") {
                let source = std::fs::read_to_string(&path).unwrap();
                for (_, tail) in source
                    .match_indices("json!(")
                    .map(|(i, marker)| (i, &source[i + marker.len()..]))
                {
                    let tail = tail.trim_start();
                    if tail.starts_with(['{', '['])
                        && let Some(value) = literal_json(tail)
                    {
                        visit(&value, keys, schema, &read, &path, &mut count);
                    }
                }
            } else if path.extension().is_some_and(|ext| ext == "json") {
                let value =
                    serde_json::from_slice::<Value>(&std::fs::read(&path).unwrap()).unwrap();
                visit(&value, keys, schema, &read, &path, &mut count);
            }
        }
    }
    assert!(count > 1800, "only {count} Spec examples checked");
    eprintln!("{count} valid literal/fixture Spec examples checked in authored and read shapes");
}
