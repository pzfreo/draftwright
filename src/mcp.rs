//! Thin local MCP transports over the stateless Session API.
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{Shutdown, TcpListener, TcpStream};
use std::path::Path;
use std::time::{Duration, Instant};

use crate::api::Error;
use crate::model::Spec;
use crate::session::{Delta, ItemAnswer, ItemFilter, ProblemFilter};
use crate::stateless::{Cache, PAGE_BYTES, spec_hash};
use serde_json::{Value, json};

pub const TOOLS: [&str; 14] = [
    "inspect_part",
    "list_items",
    "get_item",
    "list_problems",
    "item_options",
    "preview_ops",
    "apply_ops",
    "view_sheet",
    "render_pdf",
    "get_spec",
    "spec_schema",
    "analyse",
    "render",
    "parts",
];
const CORRECTION_ATTEMPTS: usize = 8;

pub const VERSIONS: [&str; 3] = ["2025-11-25", "2025-06-18", "2025-03-26"];
pub const INSTRUCTIONS: &str = "Register STEP with inspect_part {step}; keep part_ref. Call spec_schema {} for the full Spec schema before authoring unfamiliar fields; item_options gives valid edit paths and choices. The Spec is state: pass spec or immutable spec_version to each query. get_spec loads a drawing's current head; apply_ops requires that parent and returns a new version. preview_ops keeps no version. Lists and delta events are paged below 4096 bytes; follow next_cursor. Delta artifacts are lossless and readable with resources/read (events may be paged there without applying again). get_item explains basis and valid operations. view_sheet gives PNG, render_pdf delivery PDF. No live session or authentication; localhost development only.";

fn description(tool: &str) -> &'static str {
    match tool {
        "inspect_part" => {
            "Register a local STEP path (optional recognition JSON in features; optional part, the index of one part of an assembly from the parts tool, to draw it alone) and obtain part_ref and initial spec_version. Summary includes key_items (envelope sizes and a size/representative item per feature, bounded; list_items finds the rest). problem_filter optionally returns a paged problem_page with fixes in this same call. With part_ref, summarize the Spec's sheet, views, item/problem counts; multi-sheet results add sheets and sheet_details (number, size, scale, page). detail=true adds a lossless plan_ref readable with resources/read."
        }
        "list_items" => {
            "List stable item IDs, labels, views, kinds, sheet numbers (absent means sheet 1) and typed states for part_ref plus spec or spec_version. Filter by view/kind/state/feature or near an ID/region. Follow next_cursor with the same input. detail=true adds placement and anchors; default omits geometry."
        }
        "get_item" => {
            "Read one stable item id: label, state, value, explanation (basis) and allowed operations. Supply part_ref plus spec or spec_version. detail=true includes resolved geometry, anchors and features. IDs accept sheet:<n>/<id>; a wrong sheet offers the correct address. An absent sheet means sheet 1. Unknown IDs return nearest valid IDs."
        }
        "list_problems" => {
            "List drawing problems by code, severity, subject item ID or feature ID (all its dimensions/annotations) for part_ref plus spec or spec_version. Follow next_cursor for all problems. Rows include fixes ordered local, view, sheet, each with scope, description and ops; fix retains the preferred bundle. Oversized rows keep the preferred fix with scope and description; detail_ref reads all alternatives. Descriptions state the changes; sheet remedies warn that the whole sheet is laid out again and hand placements may change. detail=true adds explanations. Use filter.feature for one query covering a whole feature."
        }
        "item_options" => {
            "Get every settable Spec path with choices, ranges and tolerance forms, compatible view anchors, scales and relative placements for an item id in part_ref plus spec or spec_version. IDs accept sheet:<n>/<id>. Views offer move_to_sheet at views/<name>/sheet with choices and keep_together/relative_to consequences. Linear dimensions accept only sides parallel to their span. Fits use features/<feature-id>/fit, not dimension tolerance; classes are valid for the feature kind and diameter. Datums and GD&T frames use features/<feature-id>/datum (a letter) and features/<feature-id>/controls (a list of {characteristic, tolerance, datums?, diameter?, modifier?}); finish {ra, removal?}, thread {designation, length?}, knurl {pattern, pitch} and note (text) sit beside them, and sheet/default_finish on the sheet. Descriptors with paths share choices across all those paths. View/sheet hidden, centre_marks, centre_lines, thread_depiction and knurl_depiction are booleans; view settings win. Combine place subfields into one place object when setting a view relation. For projected gaps use align=projection (front with plan/side, either direction): geometry stays aligned across side, gap is between areas including bands. Related anchors offer it first. Use a recognised feature or pattern id (f:...) to query recognition controls: features/<id>/reclassify, suppress, split or pattern {arrangement, members}; add {id, kind, faces} to feature_declarations. Sheet options explain declarations; item_options with id face:<index> reads one face fingerprint. Options are candidates; preview_ops checks their effects. Large replies keep full structuredContent; the text channel gives a lossless detail_ref readable with resources/read {uri:draftwright:<detail_ref>}."
        }
        "preview_ops" => {
            "Try up to 32 Spec ops without saving or changing the drawing head. Supply part_ref and spec or spec_version. Ops are set/add {path,value} or remove {path}, with RFC6901 escaping (~1 for a slash inside an ID). Returns paged change/problem events and lossless delta_ref."
        }
        "apply_ops" => {
            "Commit up to 32 Spec ops to drawing_ref (default: default), requiring its current spec_version as parent. Returns new immutable spec_version and paged delta; stale parents are refused with current head. Batch all edits for one request in ops. Tolerances are strings \"±T\" or \"+U -L\"; fits use features/f:hole:f6/fit (e.g. H7). Datums: set features/<id>/datum or faces/<bottom|top|left|right|front|back|face:n>/datum to a letter; GD&T: add to features/<id>/controls or faces/<face>/controls {characteristic:position,tolerance:0.1,datums:[A]}. Finish: set features/<id>/finish, faces/<face>/finish or sheet/default_finish {ra:0.8}; thread: features/<id>/thread {designation:M6,length?:10}; knurl: features/<id>/knurl {pattern:straight,pitch:0.8}; note: features/<id>/note or faces/<face>/note text. Move views with set views/<name>/sheet n (no new op kind); set sheets/max first in the same batch. Line work: set sheet/hidden or views/side/hidden to a boolean; centre_marks, centre_lines, thread_depiction and knurl_depiction use the same paths. View paths use bare names, views/iso/omit; title drawing number is sheet/title/number. Use paths such as dimensions/d:envelope~1width.length/place with {side:above,lane:0,pinned:true}. Label text uses dimensions/<id>/text {prefix?,suffix?} or {template:\"{value} AFTER PLATING\"}; {value} retains all measured terms. Set style to reference or basic. Read later delta pages via resources/read, never re-apply to page."
        }
        "view_sheet" => {
            "Render a PNG for visual review of part_ref plus spec or spec_version. Optional region is paper millimetres [left,bottom,right,top], y up; sheet defaults to 1; overlay_ids is true for all local items or a list of bare or sheet:<n>/<id> addresses on that sheet. dpi defaults to 150, maximum 400. Returns image content and artifact_ref; exports embed the Spec."
        }
        "render_pdf" => {
            "Render the delivery PDF for part_ref plus spec or spec_version, returning artifact_ref, MIME type, byte size and Spec hash. Read bytes using resources/read {uri:draftwright:<artifact_ref>}. sheet selects one page (default 1). The PDF embeds the bound Spec for resuming the drawing."
        }
        "get_spec" => {
            "Read the complete author Spec and immutable spec_version. Supply part_ref and a spec_version (or inline spec); without either, read drawing_ref's current head (default: default). Keep this version as apply_ops parent; queries otherwise use the default Spec when no version is supplied."
        }
        "spec_schema" => {
            "Read the full draft-2020-12 author Spec schema without registering a part. No inputs. Returns spec (the standalone schema document) and spec_read (a JSON Pointer fragment within spec naming get_spec's spec value shape). Fetch once when authoring unfamiliar fields; item_options gives geometry-specific edit choices. Server validation and corrective guidance remain authoritative."
        }
        "analyse" => {
            "One-shot recognition of a local STEP path, optionally using validated recognition JSON in features. Returns the full Analysis document including features and semantic PMI. Prefer inspect_part when editing: it registers the reusable content/cache handle."
        }
        "render" => {
            "One-shot PNG or PDF render from a local STEP path (optional features) or registered part_ref, with optional spec/spec_version. format is png or pdf; sheet selects one page (default 1). PNG accepts region, overlay_ids (boolean or ID list) and dpi. Returns artifact metadata; read it via resources/read or use view_sheet for MCP image content."
        }
        "parts" => {
            "List the distinct parts of a local STEP path: each one's index (part), name, product_id, face count and number of placements (instances). Pass an index as part to inspect_part, analyse or render to draw that part of an assembly alone; without it an assembly is drawn whole, with an assembly_drawn_whole warning (unless its bodies all coincide, coincident_bodies, or lie within the largest one's box, assembly_principal_drawn: that part is drawn alone)."
        }
        _ => unreachable!("published tool"),
    }
}

pub fn schema(tool: &str, output: bool) -> Option<Value> {
    macro_rules! files { ($($name:literal),*) => {match (tool,output) {$(( $name, false) => Some(include_str!(concat!("../schemas/mcp/",$name,".input.json"))),($name,true)=>Some(include_str!(concat!("../schemas/mcp/",$name,".output.json"))),)* _=>None}}; }
    let text = files!(
        "inspect_part",
        "list_items",
        "get_item",
        "list_problems",
        "item_options",
        "preview_ops",
        "apply_ops",
        "view_sheet",
        "render_pdf",
        "get_spec",
        "spec_schema",
        "analyse",
        "render",
        "parts"
    )?;
    Some(serde_json::from_str(text).expect("published schema"))
}
/// Validate the vocabulary used by our standalone draft-2020-12 schemas.
pub fn validate(schema: &Value, value: &Value) -> Result<(), String> {
    // The generator emits finite literal choices, optionally sharing underscore
    // prefixes/suffixes, e.g. centre_(marks|lines)|(thread|knurl)_depiction.
    // Match the whole key exactly, including text after the closing group.
    fn name_matches(choices: &str, key: &str) -> bool {
        let mut depth = 0;
        let mut start = 0;
        for (i, c) in choices.char_indices() {
            match c {
                '(' => depth += 1,
                ')' => depth -= 1,
                '|' if depth == 0 => {
                    if name_matches(&choices[start..i], key) {
                        return true;
                    }
                    start = i + 1;
                }
                _ => {}
            }
        }
        let last = &choices[start..];
        if let Some((prefix, rest)) = last.split_once('(') {
            rest.rsplit_once(')').is_some_and(|(alternatives, suffix)| {
                key.strip_prefix(prefix)
                    .and_then(|key| key.strip_suffix(suffix))
                    .is_some_and(|middle| name_matches(alternatives, middle))
            })
        } else {
            last == key
        }
    }
    fn visit(root: &Value, s: &Value, v: &Value, path: &str) -> Result<(), String> {
        let bad = || format!("{path}: accepted shape is {s}");
        if let Some(r) = s["$ref"].as_str() {
            return visit(
                root,
                root.pointer(r.trim_start_matches('#')).ok_or_else(bad)?,
                v,
                path,
            );
        }
        if let Some(list) = s["anyOf"].as_array()
            && !list.iter().any(|s| visit(root, s, v, path).is_ok())
        {
            return Err(bad());
        }
        if let Some(types) = s.get("type") {
            let accepts = |t: &str| match t {
                "object" => v.is_object(),
                "array" => v.is_array(),
                "string" => v.is_string(),
                "number" => v.is_number(),
                "integer" => v.as_u64().is_some() || v.as_i64().is_some(),
                "boolean" => v.is_boolean(),
                "null" => v.is_null(),
                _ => false,
            };
            let valid = types.as_str().is_some_and(accepts)
                || types
                    .as_array()
                    .is_some_and(|ts| ts.iter().any(|t| t.as_str().is_some_and(accepts)));
            if !valid {
                return Err(bad());
            }
        }
        if s.get("const").is_some_and(|c| c != v)
            || s["enum"].as_array().is_some_and(|l| !l.contains(v))
        {
            return Err(bad());
        }
        if let Some(n) = v.as_f64() {
            for (k, valid) in [
                ("minimum", s["minimum"].as_f64().is_none_or(|b| n >= b)),
                ("maximum", s["maximum"].as_f64().is_none_or(|b| n <= b)),
                (
                    "exclusiveMinimum",
                    s["exclusiveMinimum"].as_f64().is_none_or(|b| n > b),
                ),
            ] {
                if !valid {
                    return Err(format!("{path}: choose {k} {}", s[k]));
                }
            }
        }
        if let Some(o) = v.as_object() {
            if s["minProperties"]
                .as_u64()
                .is_some_and(|n| o.len() < n as usize)
                || s["maxProperties"]
                    .as_u64()
                    .is_some_and(|n| o.len() > n as usize)
            {
                return Err(bad());
            }
            if let Some(r) = s["required"].as_array() {
                for key in r {
                    if !o.contains_key(key.as_str().unwrap()) {
                        return Err(format!("{path}: provide required field {key}"));
                    }
                }
            }
            for (key, val) in o {
                if let Some(names) = s.get("propertyNames") {
                    visit(root, names, &Value::String(key.clone()), path)?;
                }
                // Generated schemas group equal shapes with anchored, literal name choices.
                let patterns: Vec<_> = s["patternProperties"]
                    .as_object()
                    .into_iter()
                    .flatten()
                    .filter(|(pattern, _)| {
                        pattern
                            .strip_prefix("^(")
                            .and_then(|p| p.strip_suffix(")$(?!\n)"))
                            .is_some_and(|names| name_matches(names, key))
                    })
                    .map(|(_, shape)| shape)
                    .collect();
                if let Some(p) = s["properties"].get(key) {
                    visit(root, p, val, &format!("{path}/{key}"))?;
                }
                for p in &patterns {
                    visit(root, p, val, &format!("{path}/{key}"))?;
                }
                if s["properties"].get(key).is_some() || !patterns.is_empty() {
                    continue;
                }
                if s["additionalProperties"] == false {
                    if key == "preferred_printer" && path.ends_with("/sheet") {
                        return Err(format!("{path}: {}", crate::model::REMOVED_PRINTER));
                    }
                    return Err(format!(
                        "{path}: {key:?} is unknown; accepted fields: {}; grouped fields: {}",
                        s["properties"], s["patternProperties"]
                    ));
                } else if s["additionalProperties"].is_object() {
                    visit(
                        root,
                        &s["additionalProperties"],
                        val,
                        &format!("{path}/{key}"),
                    )?;
                }
            }
        }
        if let Some(a) = v.as_array() {
            if s["minItems"].as_u64().is_some_and(|n| a.len() < n as usize)
                || s["maxItems"].as_u64().is_some_and(|n| a.len() > n as usize)
            {
                return Err(bad());
            }
            if let Some(item) = s.get("items") {
                for (i, val) in a.iter().enumerate() {
                    visit(root, item, val, &format!("{path}/{i}"))?;
                }
            }
        }
        Ok(())
    }
    visit(schema, schema, value, "input")
}
fn value<T: serde::Serialize>(v: &T) -> Value {
    serde_json::to_value(v).expect("document serialises")
}
fn text<'a>(args: &'a Value, key: &str) -> Result<&'a str, Error> {
    args[key]
        .as_str()
        .ok_or_else(|| Error::invalid(format!("provide {key}, a string")))
}
fn spec(cache: &Cache, args: &Value) -> Result<Spec, Error> {
    if args.get("spec").is_some() && args.get("spec_version").is_some() {
        return Err(Error::invalid(
            "provide spec or spec_version, rather than both",
        ));
    }
    if let Some(k) = args["spec_version"].as_str() {
        cache.get_version(k)
    } else {
        serde_json::from_value(args.get("spec").cloned().unwrap_or_else(|| json!({})))
            .map_err(|e| Error::invalid(format!("provide a valid Spec: {e}")))
    }
}
fn compact(i: &ItemAnswer) -> Value {
    let mut row = json!({"id":i.item.id,"kind":i.item.kind,"label":i.item.label,"view":i.item.view,"state":i.item.state});
    if !i.item.characteristic_numbers.is_empty() {
        row["characteristic_numbers"] = json!(i.item.characteristic_numbers);
    }
    if i.item.explicit_sheet || i.item.sheet != 1 {
        row["sheet"] = json!(i.item.sheet);
    }
    row
}
fn small(cache: &Cache, row: Value) -> Result<Value, Error> {
    if value_bytes(&row) < PAGE_BYTES / 4 {
        return Ok(row);
    }
    let detail = cache.artifact(&serde_json::to_vec(&row).unwrap(), "json")?;
    // Keep a problem actionable when verbose alternatives exceed the row budget.
    // Its preferred bundle still carries scope and the complete trade description;
    // the resource retains all alternatives and explanations losslessly.
    if row.get("code").is_some() && row.get("subjects").is_some() {
        let mut summary = json!({"id":row["id"],"code":row["code"],
            "severity":row["severity"],"subjects":row["subjects"],"detail_ref":detail});
        for field in ["impact", "group"] {
            if let Some(label) = row.get(field) {
                summary[field] = label.clone();
            }
        }
        if let Some(preferred) = row["fixes"].as_array().and_then(|fixes| fixes.first()) {
            summary["fix"] = preferred["ops"].clone();
            summary["scope"] = preferred["scope"].clone();
            summary["description"] = preferred["description"].clone();
        }
        if value_bytes(&summary) < PAGE_BYTES / 4 {
            return Ok(summary);
        }
        // A long trade cannot be offered without its complete description. Keep
        // the finding identifiable; the resource contains the full remedies.
        for field in ["fix", "scope", "description"] {
            summary.as_object_mut().unwrap().remove(field);
        }
        if value_bytes(&summary) < PAGE_BYTES / 4 {
            return Ok(summary);
        }
    }
    let id = row
        .get("id")
        .or_else(|| row.pointer("/item/id"))
        .filter(|id| id.as_str().is_some_and(|s| s.len() <= 256));
    Ok(json!({"id":id,"detail_ref":detail}))
}
fn value_bytes(v: &Value) -> usize {
    serde_json::to_vec(v).unwrap().len()
}
pub fn page(cache: &Cache, rows: Vec<Value>, args: &Value) -> Result<Value, Error> {
    let start = args["cursor"].as_u64().unwrap_or(0) as usize;
    let limit = args["limit"].as_u64().unwrap_or(20) as usize;
    if !(1..=100).contains(&limit) || args.get("cursor").is_some_and(|v| v.as_u64().is_none()) {
        return Err(Error::invalid(
            "use a nonnegative cursor and limit between 1 and 100",
        ));
    }
    if start > rows.len() {
        return Err(Error::invalid(format!(
            "cursor must be between 0 and {}",
            rows.len()
        )));
    }
    let mut selected = Vec::new();
    let mut end = start;
    for row in rows.iter().skip(start).take(limit) {
        let row = small(cache, row.clone())?;
        selected.push(row);
        if value_bytes(&json!({"items":selected,"total":rows.len(),"next_cursor":end+1}))
            > PAGE_BYTES / 3
        {
            selected.pop();
            break;
        }
        end += 1;
    }
    Ok(
        json!({"items":selected,"total":rows.len(),"next_cursor":if end<rows.len(){Some(end)}else{None}}),
    )
}
fn problem_summary(p: &crate::model::Problem) -> Value {
    let labels = p.labels();
    let fixes = p.offered_fixes();
    let preferred = fixes.first().map(|f| f.ops.as_slice()).unwrap_or_default();
    json!({"id":p.id,"code":p.code,"severity":p.severity,"impact":labels.impact,"group":labels.group,"subjects":p.subjects,"fix":preferred,"fixes":fixes})
}
fn delta_page(cache: &Cache, delta: &Delta, args: &Value) -> Result<Value, Error> {
    let detail = args["detail"].as_bool().unwrap_or(false);
    let mut rows = Vec::new();
    for (change, items) in [("changed", &delta.changed), ("added", &delta.added)] {
        for item in items {
            rows.push(json!({"change":change,"item":if detail{value(item)}else{compact(item)}}));
        }
    }
    for id in &delta.removed {
        rows.push(json!({"change":"removed","id":id}));
    }
    for (change, problems) in [
        ("problem_added", &delta.problems_added),
        ("problem_resolved", &delta.problems_resolved),
        ("notice", &delta.notices),
    ] {
        for p in problems {
            let labels = p.labels();
            rows.push(json!({"change":change,"problem":if detail || change == "notice"{value(p)}else{json!({"id":p.id,"code":p.code,"severity":p.severity,"impact":labels.impact,"group":labels.group,"subjects":p.subjects})}}));
        }
    }
    Ok(
        json!({"events":page(cache,rows,args)?,"delta_ref":cache.artifact(&serde_json::to_vec(delta).unwrap(),"json")?,"plans":delta.plans}),
    )
}
pub fn call(cache: &Cache, tool: &str, args: &Value) -> Result<Value, Error> {
    let Some(input) = schema(tool, false) else {
        return Err(Error {
            choices: TOOLS.map(str::to_string).to_vec(),
            ..Error::invalid("choose a published tool")
        });
    };
    if ["preview_ops", "apply_ops"].contains(&tool)
        && let Some(ops) = args["ops"].as_array()
    {
        crate::stateless::check_operation_count(ops.len())?;
    }
    // Discovery uses a shallow outline; retain main's complete preflight constraints
    // and error messages. Load the private documents once, outside tools/list.
    static VALIDATION: std::sync::OnceLock<Value> = std::sync::OnceLock::new();
    let validation = VALIDATION.get_or_init(|| {
        serde_json::from_str(include_str!("../schemas/mcp-validation.json"))
            .expect("private input validation schemas")
    });
    validate(validation.get(tool).unwrap_or(&input), args).map_err(Error::invalid)?;
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| dispatch(cache, tool, args)))
        .unwrap_or_else(|_| {
            Err(Error::failed(
                "engine stopped; retry with a valid STEP and Spec",
            ))
        })
}
fn corrective_error(
    session: &mut crate::session::Session,
    ops: &[crate::items::Op],
    mut error: Error,
) -> Error {
    if error.code != "invalid" {
        return error;
    }
    let mut attempt = || -> Option<(usize, Value, Vec<String>)> {
        let mut given = session.spec().clone();
        let mut accepted = Vec::new();
        // Discover only a bounded prefix, without planning or installing any edit.
        for (index, operation) in ops.iter().take(CORRECTION_ATTEMPTS).enumerate() {
            let op = value(operation);
            let parts: Vec<_> = operation
                .path
                .split('/')
                .map(|s| s.replace("~1", "/").replace("~0", "~"))
                .collect();
            // Unknown view keys are refused during planning, not edit validation.
            let refused_view = operation.op == "set"
                && matches!(parts.as_slice(), [section, id, _]
                    if section == "views"
                        && id.starts_with("v:")
                        && error.message == format!("There is no view called {id:?}.")
                        && error.choices.iter().any(|v| v == id.trim_start_matches("v:")));
            match crate::model::edit(&given, &json!([op])) {
                Ok(spec) if !refused_view => {
                    given = spec;
                    accepted.push(operation.clone());
                    continue;
                }
                Err(refusal) if Error::from(refusal.clone()) != error => continue,
                Ok(_) | Err(_) => {}
            }
            if op["op"] != "set" {
                continue;
            }
            let mut corrected = op.clone();
            let mut choices = Vec::new();
            match parts.as_slice() {
                [section, id, field] if section == "views" && id.starts_with("v:") => {
                    choices = session
                        .plan()
                        .views
                        .iter()
                        .map(|v| v.id.trim_start_matches("v:").to_string())
                        .collect();
                    if choices.iter().any(|v| v == id.trim_start_matches("v:")) {
                        corrected["path"] = json!(format!(
                            "views/{}/{field}",
                            crate::items::key(id.trim_start_matches("v:"))
                        ));
                    }
                }
                [sheet, title, field] if sheet == "sheet" && title == "title" => {
                    choices = crate::title_block::KEYS.map(|k| k.0.to_string()).to_vec();
                    if let Some((key, _)) = crate::title_block::KEYS
                        .iter()
                        .find(|(key, cell)| cell == field && key != cell)
                    {
                        corrected["path"] = json!(format!("sheet/title/{key}"));
                    }
                }
                [section, id, field]
                    if section == "dimensions"
                        && ["fit", "tolerance"].contains(&field.as_str()) =>
                {
                    let options = session.options(id).ok()?;
                    let fit_fields: Vec<_> = options
                        .settable
                        .iter()
                        .filter(|(p, _)| p.starts_with("features/") && p.ends_with("/fit"))
                        .collect();
                    if fit_fields.len() == 1
                        && fit_fields[0].1.get("paths").is_none()
                        && op["value"].as_str().is_some_and(|v| {
                            fit_fields[0].1["choices"]
                                .as_array()
                                .is_some_and(|a| a.contains(&json!(v)))
                        })
                    {
                        corrected["path"] = json!(fit_fields[0].0);
                        choices = fit_fields[0].1["choices"]
                            .as_array()?
                            .iter()
                            .filter_map(|v| v.as_str().map(str::to_string))
                            .collect();
                    } else if field == "tolerance" {
                        choices = vec!["±T".into(), "+U -L".into()];
                        if let Some(object) = op["value"].as_object()
                            && object.len() == 1
                            && let Some(n) = object.get("symmetric").and_then(Value::as_f64)
                            && n.is_finite()
                            && n >= 0.0
                        {
                            corrected["value"] = json!(format!("±{n}"));
                        } else if let Some(s) = op["value"].as_str() {
                            corrected["value"] =
                                json!(s.replace("+/-", "±").replace("+-", "±").replace('/', " "));
                        }
                    }
                }
                _ => {}
            }
            if corrected != op {
                accepted.push(serde_json::from_value(corrected.clone()).ok()?);
                // One candidate preview is the independent corrective planner budget.
                return crate::stateless::session_apply(session, &accepted, false)
                    .ok()
                    .map(|_| (index, corrected, choices));
            }
        }
        None
    };
    if let Some((index, op, choices)) = attempt() {
        error.choices = choices;
        error
            .message
            .push_str(&format!(" Replace ops[{index}]. Corrected op: {op}"));
    }
    error
}

fn dispatch(cache: &Cache, tool: &str, args: &Value) -> Result<Value, Error> {
    if tool == "spec_schema" {
        let spec: Value = serde_json::from_str(include_str!("../schemas/spec.json"))
            .expect("published Spec schema");
        return Ok(json!({"spec": spec, "spec_read": "#/$defs/spec_read"}));
    }
    if tool == "parts" {
        return Ok(json!({ "parts": crate::api::parts(Path::new(text(args, "step")?))? }));
    }
    let registered;
    let part_ref = if let Some(step) = args["step"].as_str() {
        if !["inspect_part", "analyse", "render"].contains(&tool) {
            return Err(Error::invalid(
                "register step with inspect_part, then pass part_ref",
            ));
        }
        if args.get("part_ref").is_some() {
            return Err(Error::invalid(
                "provide step to register or part_ref to query, rather than both",
            ));
        }
        registered = cache.register_part(
            Path::new(step),
            args["features"].as_str().map(Path::new),
            crate::api::request_part(tool, args)?,
        )?;
        &registered
    } else {
        if args.get("part").is_some_and(|p| !p.is_null()) {
            return Err(Error::invalid(
                "part is chosen when the STEP is registered: pass step with part (part_ref already names its part)",
            ));
        }
        text(args, "part_ref")?
    };
    let given = spec(cache, args)?;
    match tool {
        "analyse" => Ok(value(cache.session(part_ref, &given)?.analysis())),
        "inspect_part" => {
            let mut summary = cache.summary(part_ref, &given)?;
            let session = cache.session(part_ref, &given)?;
            cache.version(session.spec())?;
            if let Some(version) = args["spec_version"].as_str() {
                summary["spec_version"] = json!(version);
            }
            cache.head(part_ref, args["drawing_ref"].as_str().unwrap_or("default"))?;
            if args["detail"] == true {
                summary["plan_ref"] =
                    json!(cache.artifact(&serde_json::to_vec(session.plan()).unwrap(), "json")?);
            }
            if let Some(filter) = args.get("problem_filter") {
                let filter: ProblemFilter = serde_json::from_value(filter.clone())
                    .map_err(|e| Error::invalid(e.to_string()))?;
                summary["problem_page"] = page(
                    cache,
                    session
                        .problems(&filter)
                        .iter()
                        .map(problem_summary)
                        .collect(),
                    &json!({}),
                )?;
                // The summary and page share the complete MCP envelope's byte budget.
                while value_bytes(
                    &json!({"content":[{"type":"text","text":summary.to_string()}],"structuredContent":summary,"isError":false}),
                ) + 128
                    > PAGE_BYTES
                {
                    if summary["key_items"]
                        .as_array_mut()
                        .expect("key items")
                        .pop()
                        .is_some()
                    {
                        continue;
                    }
                    let rows = summary["problem_page"]["items"]
                        .as_array_mut()
                        .expect("page items");
                    if rows.pop().is_none() {
                        break;
                    }
                    let count = rows.len();
                    summary["problem_page"]["next_cursor"] = json!(count);
                }
            }
            Ok(summary)
        }
        "get_spec" => {
            let version = if let Some(version) = args["spec_version"].as_str() {
                version.to_string()
            } else if args.get("spec").is_some() {
                cache.version(cache.session(part_ref, &given)?.spec())?
            } else {
                cache.head(part_ref, args["drawing_ref"].as_str().unwrap_or("default"))?
            };
            let s = cache.get_version(&version)?;
            // A version handle is accepted only for the part it is bound to.
            cache.session(part_ref, &s)?;
            Ok(json!({"spec_version":version,"spec":s}))
        }
        "list_items" => {
            let filter: ItemFilter =
                serde_json::from_value(args.get("filter").cloned().unwrap_or_else(|| json!({})))
                    .map_err(|e| Error::invalid(e.to_string()))?;
            page(
                cache,
                cache
                    .items(part_ref, &given, &filter)?
                    .iter()
                    .map(|i| {
                        if args["detail"] == true {
                            value(i)
                        } else {
                            compact(i)
                        }
                    })
                    .collect(),
                args,
            )
        }
        "get_item" => {
            let i = cache.item(part_ref, &given, text(args, "id")?)?;
            let answer = if args["detail"] == true {
                value(&i)
            } else {
                let mut c = compact(&i);
                c["basis"] = json!(i.basis);
                c["value"] = json!(i.value);
                c["operations"] = json!(i.operations);
                c["operation_scopes"] = json!(i.operation_scopes);
                c
            };
            Ok(json!({"item":answer}))
        }
        "list_problems" => {
            let filter: ProblemFilter =
                serde_json::from_value(args.get("filter").cloned().unwrap_or_else(|| json!({})))
                    .map_err(|e| Error::invalid(e.to_string()))?;
            page(
                cache,
                cache
                    .problems(part_ref, &given, &filter)?
                    .iter()
                    .map(|p| {
                        if args["detail"] == true {
                            value(p)
                        } else {
                            problem_summary(p)
                        }
                    })
                    .collect(),
                args,
            )
        }
        "item_options" => Ok(value(&cache.options(
            part_ref,
            &given,
            text(args, "id")?,
        )?)),
        "preview_ops" => {
            let ops: Vec<crate::items::Op> = serde_json::from_value(args["ops"].clone())
                .map_err(|e| Error::invalid(format!("valid ops: {e}")))?;
            let applied = cache.apply_with_refusal(part_ref, &given, &ops, |session, error| {
                corrective_error(session, &ops, error)
            })?;
            delta_page(cache, &applied.delta, args)
        }
        "apply_ops" => {
            if args.get("spec").is_some() || args.get("spec_version").is_some() {
                return Err(Error::invalid(
                    "apply_ops takes parent (the current Spec version); omit spec and spec_version",
                ));
            }
            if args.get("cursor").is_some_and(|v| v != 0) {
                return Err(Error::invalid(
                    "read further delta pages using resources/read {uri: draftwright:<delta_ref>, events: true, cursor: <next_cursor>}; do not apply the ops again",
                ));
            }
            let ops: Vec<crate::items::Op> = serde_json::from_value(args["ops"].clone())
                .map_err(|e| Error::invalid(format!("valid ops: {e}")))?;
            let (version, applied) = cache.apply_version_with_refusal(
                part_ref,
                args["drawing_ref"].as_str().unwrap_or("default"),
                text(args, "parent")?,
                &ops,
                |session, error| corrective_error(session, &ops, error),
            )?;
            Ok(json!({"spec_version":version,"delta":delta_page(cache,&applied.delta,args)?}))
        }
        "view_sheet" | "render_pdf" | "render" => {
            let format = if tool == "render_pdf" {
                "pdf"
            } else {
                args["format"].as_str().unwrap_or("png")
            };
            let region = args
                .get("region")
                .map(|r| serde_json::from_value(r.clone()))
                .transpose()
                .map_err(|e| Error::invalid(format!("valid region: {e}")))?;
            let ids = args["overlay_ids"].as_array().map(|ids| {
                ids.iter()
                    .map(|id| {
                        id.as_str()
                            .expect("overlay IDs are schema-validated")
                            .to_owned()
                    })
                    .collect::<Vec<_>>()
            });
            let bytes = cache.render_sheet_with_ids(
                part_ref,
                &given,
                region,
                format,
                args["overlay_ids"] == true,
                args["dpi"].as_f64().unwrap_or(crate::png::DPI),
                args["sheet"].as_u64().unwrap_or(1) as u32,
                ids.as_deref(),
            )?;
            Ok(
                json!({"artifact_ref":cache.artifact(&bytes,format)?,"mime_type":if format=="png"{"image/png"}else{"application/pdf"},"bytes":bytes.len(),"spec_version":spec_hash(cache.session(part_ref,&given)?.spec())}),
            )
        }
        _ => unreachable!(),
    }
}
fn rpc_error(id: Value, code: i32, message: &str) -> Value {
    json!({"jsonrpc":"2.0","id":id,"error":{"code":code,"message":message}})
}
/// Pack delivery for the document-passing MCP adapter. Native `pack` keeps its
/// own envelope and defaults; this adapter never exports files implicitly.
pub fn pack_tool(cache: &Cache, args: &Value) -> Result<Value, Error> {
    let formats = match args.get("formats") {
        None | Some(Value::Null) => Vec::new(),
        Some(Value::Array(list)) => list
            .iter()
            .map(|format| {
                format
                    .as_str()
                    .filter(|f| crate::pack::FORMATS.contains(f))
                    .ok_or_else(|| Error::invalid("pack formats must be pdf,csv,json"))
            })
            .collect::<Result<Vec<_>, _>>()?,
        Some(_) => {
            return Err(Error::invalid(
                "pack formats must be a list of pdf,csv,json",
            ));
        }
    };
    let mut seen = std::collections::BTreeSet::new();
    if formats.iter().any(|format| !seen.insert(*format)) {
        return Err(Error::invalid(
            "pack formats must be unique; remove duplicate formats",
        ));
    }
    let spec = args
        .get("spec")
        .filter(|v| !v.is_null())
        .map(|v| serde_json::from_value::<crate::pack::PackSpec>(v.clone()))
        .transpose()
        .map_err(|e| Error::invalid(format!("invalid pack spec: {e}")))?
        .unwrap_or_default();
    let step = args.get("step").filter(|v| !v.is_null());
    let part_ref = args.get("part_ref").filter(|v| !v.is_null());
    if step.is_some() && part_ref.is_some() {
        return Err(Error::invalid("pack takes step or part_ref, not both"));
    }
    let source = step.or(part_ref).and_then(Value::as_str).ok_or_else(|| {
        Error::invalid("pack needs step (a path or registered part ref) or part_ref")
    })?;
    let registered = part_ref.is_some()
        || source.split_once('-').is_some_and(|(key, _)| {
            key.len() >= 64 && key.bytes().take(64).all(|b| b.is_ascii_hexdigit())
        });
    let pack = if registered {
        let (name, bytes) = cache.source(source)?;
        crate::pack::build_bytes(&name, &bytes, &spec)?
    } else {
        crate::pack::build(Path::new(source), &spec)?
    };
    let mut content = Vec::new();
    // JSON delivery replaces the inline report, so it never appears twice.
    if !formats.contains(&"json") {
        let report = serde_json::to_value(&pack.report).expect("report serialises");
        content.push(json!({"type":"text","text":serde_json::to_string(&report).expect("report serialises")}));
    }
    let failures = pack
        .report
        .rows
        .iter()
        .filter(|row| row.error.is_some())
        .count();
    content.push(json!({"type":"text","text":format!("Drawing pack: {} rows; {} pages; {failures} failed parts; {} findings.", pack.report.rows.len(), pack.report.pages, pack.report.findings.len())}));
    for format in formats {
        let (bytes, mime) = match format {
            "pdf" => (pack.pdf(), "application/pdf"),
            "csv" => (pack.report.csv().into_bytes(), "text/csv"),
            _ => (
                serde_json::to_vec_pretty(&pack.report).expect("report serialises"),
                "application/json",
            ),
        };
        let key = cache.artifact(&bytes, format)?;
        content.push(json!({"type":"resource_link","uri":format!("draftwright:{key}"),"name":format!("pack.{format}"),"mimeType":mime,"size":bytes.len()}));
    }
    Ok(json!({"content":content,"isError":false}))
}

pub(crate) fn base64(bytes: &[u8]) -> String {
    const ALPHABET: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::new();
    for b in bytes.chunks(3) {
        let n = ((b[0] as u32) << 16)
            | ((b.get(1).copied().unwrap_or(0) as u32) << 8)
            | b.get(2).copied().unwrap_or(0) as u32;
        for (k, shift) in [18, 12, 6, 0].into_iter().enumerate() {
            out.push(if k > b.len() {
                '='
            } else {
                ALPHABET[((n >> shift) & 63) as usize] as char
            });
        }
    }
    out
}
pub fn reply(cache: &Cache, request: &Value) -> Option<Value> {
    let id = request.get("id").cloned().unwrap_or(Value::Null);
    if request["jsonrpc"] != "2.0" || !request.is_object() {
        return Some(rpc_error(id, -32600, "use a JSON-RPC 2.0 object"));
    }
    let method = request["method"].as_str();
    if method.is_none() && (request.get("result").is_some() || request.get("error").is_some()) {
        return None;
    }
    if request.get("id").is_none()
        && (method.is_some() || request.get("result").is_some() || request.get("error").is_some())
    {
        return None;
    }
    let params = &request["params"];
    let result = match method {
        Some("initialize") => {
            json!({"protocolVersion":params["protocolVersion"].as_str().filter(|v|VERSIONS.contains(v)).unwrap_or(VERSIONS[0]),"capabilities":{"tools":{},"resources":{}},"serverInfo":{"name":"draftwright-stateless","version":env!("CARGO_PKG_VERSION")},"instructions":INSTRUCTIONS})
        }
        Some("ping") => json!({}),
        Some("tools/list") => {
            json!({"tools":TOOLS.iter().map(|name|json!({"name":name,"description":description(name),"inputSchema":schema(name,false),"outputSchema":schema(name,true)})).collect::<Vec<_>>()})
        }
        Some("tools/call") => {
            let Some(tool) = params["name"].as_str().filter(|n| TOOLS.contains(n)) else {
                return Some(rpc_error(id, -32602, "choose a tool from tools/list"));
            };
            let called = call(cache, tool, params.get("arguments").unwrap_or(&json!({})));
            let (result, failed) = match called {
                Ok(v) => (v, false),
                Err(e) => (json!({"error":e}), true),
            };
            let mut content =
                vec![json!({"type":"text","text":serde_json::to_string(&result).unwrap()})];
            // Keep every option in structuredContent. Avoid duplicating a large descriptor
            // map in the text channel; text-only clients can read its lossless resource.
            if tool == "item_options"
                && !failed
                && value_bytes(
                    &json!({"content":content,"structuredContent":result,"isError":false}),
                ) + 128
                    > PAGE_BYTES
                && let Ok(detail) = small(cache, result.clone())
            {
                content[0]["text"] = json!(detail.to_string());
            }
            if tool == "view_sheet"
                && !failed
                && let Ok(bytes) = cache.artifact_bytes(result["artifact_ref"].as_str().unwrap())
            {
                content.push(json!({"type":"image","mimeType":"image/png","data":base64(&bytes)}));
            }
            json!({"content":content,"structuredContent":result,"isError":failed})
        }
        Some("resources/list") => json!({"resources":[]}),
        Some("resources/read") => {
            let uri = params["uri"].as_str().unwrap_or("");
            let read = uri
                .strip_prefix("draftwright:")
                .ok_or_else(|| Error::invalid("use draftwright:<artifact_ref> returned by a tool"))
                .and_then(|key| cache.artifact_bytes(key));
            match read {
                Ok(bytes) if params["events"] == true => {
                    let parsed: Result<Delta, _> = serde_json::from_slice(&bytes);
                    match parsed.and_then(|d| {
                        delta_page(cache, &d, params).map_err(serde::de::Error::custom)
                    }) {
                        Ok(events) => {
                            json!({"contents":[{"uri":uri,"mimeType":"application/json","text":events.to_string()}]})
                        }
                        Err(e) => return Some(rpc_error(id, -32602, &e.to_string())),
                    }
                }
                Ok(bytes) => {
                    if uri.ends_with(".json") {
                        json!({"contents":[{"uri":uri,"mimeType":"application/json","text":String::from_utf8_lossy(&bytes)}]})
                    } else {
                        json!({"contents":[{"uri":uri,"mimeType":if uri.ends_with(".png"){"image/png"}else if uri.ends_with(".csv"){"text/csv"}else{"application/pdf"},"blob":base64(&bytes)}]})
                    }
                }
                Err(e) => return Some(rpc_error(id, -32602, &e.to_string())),
            }
        }
        _ => {
            return Some(rpc_error(
                id,
                -32601,
                "supported methods: initialize, ping, tools/list, tools/call, resources/read",
            ));
        }
    };
    Some(json!({"jsonrpc":"2.0","id":id,"result":result}))
}
pub fn stdio(cache: &Cache) -> std::io::Result<()> {
    let mut stdout = std::io::stdout().lock();
    for line in std::io::stdin().lock().lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let response = match serde_json::from_str(&line) {
            Ok(request) => reply(cache, &request),
            Err(e) => Some(rpc_error(Value::Null, -32700, &e.to_string())),
        };
        if let Some(response) = response {
            writeln!(stdout, "{response}")?;
            stdout.flush()?;
        }
    }
    Ok(())
}
/// Sequential local HTTP keeps expensive geometry work within the memory guard.
pub fn http(cache: &Cache, listener: TcpListener) -> std::io::Result<()> {
    for mut stream in listener.incoming().flatten() {
        // Every response half-closes, and refusals drain unread input for at most 100 ms
        // (`send`), so an early refusal is not lost to a reset without stalling the next
        // queued connection.
        let _ = connection(cache, &mut stream);
    }
    Ok(())
}
fn send(stream: &mut TcpStream, status: &str, body: &[u8]) -> std::io::Result<()> {
    // Closing with unread input can reset queued fragments of a formatted write.
    let head = format!(
        "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\nAllow: POST, GET\r\n\r\n",
        body.len()
    );
    stream.write_all(head.as_bytes())?;
    stream.write_all(body)?;
    stream.shutdown(Shutdown::Write)?;
    if !status.starts_with('4') {
        return Ok(());
    }
    // Drain unread input before close to preserve refusals on Linux too.
    let deadline = Instant::now() + Duration::from_millis(100);
    let mut discarded = [0; 8192];
    while Instant::now() < deadline {
        stream.set_read_timeout(Some(
            deadline
                .saturating_duration_since(Instant::now())
                .max(Duration::from_micros(1)),
        ))?;
        match stream.read(&mut discarded) {
            Ok(0) | Err(_) => break,
            Ok(_) => {}
        }
    }
    Ok(())
}
const HTTP_HEAD_BYTES: usize = 16 * 1024;
const HTTP_READ_DEADLINE: Duration = Duration::from_secs(10);

fn read_timeout(reader: &BufReader<TcpStream>, deadline: Instant) -> std::io::Result<()> {
    let remaining = deadline.saturating_duration_since(Instant::now());
    if remaining.is_zero() {
        return Err(std::io::ErrorKind::TimedOut.into());
    }
    reader.get_ref().set_read_timeout(Some(remaining))
}
fn read_head(reader: &mut BufReader<TcpStream>, deadline: Instant) -> std::io::Result<String> {
    let mut head = Vec::with_capacity(HTTP_HEAD_BYTES);
    let mut line_start = 0;
    loop {
        if head.len() == HTTP_HEAD_BYTES {
            return Err(std::io::ErrorKind::InvalidInput.into());
        }
        read_timeout(reader, deadline)?;
        let available = reader.fill_buf()?;
        if available.is_empty() {
            return Err(std::io::ErrorKind::UnexpectedEof.into());
        }
        let bounded = &available[..available.len().min(HTTP_HEAD_BYTES - head.len())];
        let n = bounded
            .iter()
            .position(|b| *b == b'\n')
            .map_or(bounded.len(), |i| i + 1);
        head.extend_from_slice(&bounded[..n]);
        reader.consume(n);
        if head.last() == Some(&b'\n') {
            if line_start != 0 && matches!(&head[line_start..], b"\r\n" | b"\n") {
                return String::from_utf8(head)
                    .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e));
            }
            line_start = head.len();
        }
    }
}
fn read_refusal(stream: &mut TcpStream, error: std::io::Error) -> std::io::Result<()> {
    match error.kind() {
        std::io::ErrorKind::InvalidInput => {
            send(stream, "431 Request Header Fields Too Large", b"")
        }
        std::io::ErrorKind::TimedOut | std::io::ErrorKind::WouldBlock => {
            send(stream, "408 Request Timeout", b"")
        }
        std::io::ErrorKind::InvalidData | std::io::ErrorKind::UnexpectedEof => {
            send(stream, "400 Bad Request", b"")
        }
        _ => Err(error),
    }
}
fn connection(cache: &Cache, stream: &mut TcpStream) -> std::io::Result<()> {
    let deadline = Instant::now() + HTTP_READ_DEADLINE;
    let mut reader = BufReader::new(stream.try_clone()?);
    let head = match read_head(&mut reader, deadline) {
        Ok(head) => head,
        Err(e) => return read_refusal(stream, e),
    };
    let mut lines = head.split_inclusive('\n');
    let first = lines.next().unwrap();
    let mut headers = std::collections::BTreeMap::new();
    for line in lines {
        if line == "\r\n" {
            break;
        }
        if let Some((k, v)) = line.split_once(':') {
            headers.insert(k.to_ascii_lowercase(), v.trim().to_string());
        } else {
            return send(stream, "400 Bad Request", b"");
        }
    }
    if headers
        .get("origin")
        .is_some_and(|o| !origin_allowed(o, stream.local_addr().ok()))
    {
        return send(stream, "403 Forbidden", b"");
    }
    if headers
        .get("mcp-protocol-version")
        .is_some_and(|v| !VERSIONS.contains(&v.as_str()))
    {
        return send(stream, "400 Bad Request", b"");
    }
    let words: Vec<_> = first.split_whitespace().collect();
    if words.len() != 3 || words[1] != "/mcp" {
        return send(stream, "404 Not Found", b"");
    }
    if words[0] != "POST" {
        return send(stream, "405 Method Not Allowed", b"");
    }
    if headers
        .get("content-type")
        .is_none_or(|v| v.split(';').next() != Some("application/json"))
    {
        return send(stream, "415 Unsupported Media Type", b"");
    }
    if headers
        .get("accept")
        .is_none_or(|v| !v.contains("application/json") || !v.contains("text/event-stream"))
    {
        return send(stream, "406 Not Acceptable", b"");
    }
    if headers.contains_key("transfer-encoding") {
        return send(stream, "400 Bad Request", b"");
    }
    let Some(length) = headers
        .get("content-length")
        .and_then(|v| v.parse::<usize>().ok())
        .filter(|n| *n <= 8 * 1024 * 1024)
    else {
        return send(stream, "413 Content Too Large", b"");
    };
    let mut body = vec![0; length];
    let mut remaining = body.as_mut_slice();
    while !remaining.is_empty() {
        if let Err(e) = read_timeout(&reader, deadline) {
            return read_refusal(stream, e);
        }
        match reader.read(remaining) {
            Ok(0) => return send(stream, "400 Bad Request", b""),
            Ok(n) => remaining = &mut remaining[n..],
            Err(e) => return read_refusal(stream, e),
        }
    }
    let response = match serde_json::from_slice(&body) {
        Ok(request) => reply(cache, &request),
        Err(e) => Some(rpc_error(Value::Null, -32700, &e.to_string())),
    };
    match response {
        Some(response) => send(stream, "200 OK", &serde_json::to_vec(&response).unwrap()),
        None => send(stream, "202 Accepted", b""),
    }
}
fn origin_allowed(origin: &str, address: Option<std::net::SocketAddr>) -> bool {
    address.is_some_and(|address| {
        origin == format!("http://localhost:{}", address.port())
            || origin == format!("http://127.0.0.1:{}", address.port())
    })
}
pub fn run(args: &[String]) -> Result<(), Error> {
    let cache = Cache::environment();
    match args.first().map(String::as_str) {
        Some("mcp-stdio") => stdio(&cache).map_err(|e| Error::failed(e.to_string())),
        Some("mcp-http") => {
            let addr = args.get(1).map(String::as_str).unwrap_or("127.0.0.1:8765");
            let socket: std::net::SocketAddr = addr.parse().map_err(|_| {
                Error::invalid("mcp-http accepts a loopback address, e.g. 127.0.0.1:8765")
            })?;
            if !socket.ip().is_loopback() {
                return Err(Error::invalid(
                    "mcp-http accepts only loopback addresses, e.g. 127.0.0.1:8765",
                ));
            }
            let listener = TcpListener::bind(socket).map_err(|e| Error::failed(e.to_string()))?;
            eprintln!(
                "draftwright MCP listening on http://{}/mcp",
                listener
                    .local_addr()
                    .map_err(|e| Error::failed(e.to_string()))?
            );
            http(&cache, listener).map_err(|e| Error::failed(e.to_string()))
        }
        _ => Err(Error::invalid(
            "choose mcp-stdio or mcp-http [127.0.0.1:8765]",
        )),
    }
}

#[cfg(test)]
mod batch_tests {
    #[test]
    fn summaries_keep_scoped_alternatives_in_order() {
        use crate::{fixes::Fix, items::Op, model::Problem};
        let problem = Problem {
            fix: vec![Op::remove("sheet/scale")],
            fixes: vec![
                Fix::new("Move view", vec![Op::remove("views/iso/place")]),
                Fix::new(
                    "Move label",
                    vec![Op::set("dimensions/d/place/lane", json!(1))],
                ),
            ],
            ..Default::default()
        };
        let summary = super::problem_summary(&problem);
        let wire = serde_json::to_value(&problem).unwrap();
        assert_eq!(summary["impact"], wire["impact"]);
        assert_eq!(summary["group"], wire["group"]);
        assert_eq!(summary["fixes"], wire["fixes"]);
        assert_eq!(summary["fix"], wire["fix"]);
        assert_eq!(summary["fixes"][0]["scope"], "local");
        assert_eq!(summary["fixes"][1]["scope"], "view");
        assert_eq!(summary["fixes"][2]["scope"], "sheet");
    }

    use super::*;
    use crate::stateless::{MAX_OPS, work};
    use std::time::Instant;

    const LIMIT: usize = 32;

    fn open(name: &str, dense: bool) -> (Cache, String, String) {
        let root = std::env::temp_dir().join(format!("h5-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let cache = Cache::new(root);
        let step = if dense {
            Path::new(&std::env::var("DRAFTWRIGHT_CORPUS").unwrap())
                .join("nist/nist_ctc_04_asme1_rd.stp")
        } else {
            Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/mounting_plate.step")
        };
        let key = cache.register(&step, None).unwrap();
        let parent = cache.head(&key, "default").unwrap();
        work::take();
        (cache, key, parent)
    }

    fn invalid_batch(count: usize, index: usize) -> Value {
        let mut ops = vec![json!({"op":"set","path":"sheet/title/title","value":"H5"}); count];
        ops[index] = json!({"op":"set","path":"sheet/title/drawing_number","value":"H5-001"});
        json!(ops)
    }

    fn batch_args(tool: &str, key: &str, parent: &str, ops: Value) -> Value {
        let version = if tool == "apply_ops" {
            "parent"
        } else {
            "spec_version"
        };
        json!({"part_ref":key,version:parent,"ops":ops})
    }

    fn refusal_work(name: &str, index: usize) {
        let (cache, key, parent) = open(name, false);
        for tool in ["preview_ops", "apply_ops"] {
            let args = batch_args(tool, &key, &parent, invalid_batch(LIMIT, index));
            let error = call(&cache, tool, &args).unwrap_err();
            let plans = work::take();
            assert_eq!(error.code, "invalid");
            assert_eq!(cache.head(&key, "default").unwrap(), parent);
            assert!(plans <= 2, "{tool}: refusal at {index} used {plans} plans");
            if index == 0 {
                assert!(error.message.contains("Replace ops[0]. Corrected op:"));
            }
        }
    }

    #[test]
    fn invalid_batch_beginning_bounds_plans() {
        refusal_work("beginning", 0);
    }

    #[test]
    fn invalid_batch_end_bounds_plans() {
        refusal_work("end", LIMIT - 1);
    }

    #[test]
    fn correction_budget_returns_original_refusal() {
        let (cache, key, parent) = open("budget", false);
        let ops = invalid_batch(9, 8);
        let spec = cache.get_version(&parent).unwrap();
        let original = Error::from(crate::model::edit(&spec, &ops).unwrap_err());
        let error = call(
            &cache,
            "apply_ops",
            &json!({"part_ref":key,"parent":parent,"ops":ops}),
        )
        .unwrap_err();
        let plans = work::take();
        assert_eq!(error, original);
        assert_eq!(plans, 1);
        assert_eq!(cache.head(&key, "default").unwrap(), parent);
    }

    #[test]
    fn correction_planner_budget_returns_original_when_preview_refuses() {
        let (cache, key, parent) = open("planner-budget", false);
        let ops = json!([
            {"op":"set","path":"sheet/scale","value":"invalid"},
            {"op":"set","path":"sheet/title/drawing_number","value":"H5-001"}
        ]);
        let spec = cache.get_version(&parent).unwrap();
        let original = Error::from(crate::model::edit(&spec, &ops).unwrap_err());
        let error = call(
            &cache,
            "apply_ops",
            &batch_args("apply_ops", &key, &parent, ops),
        )
        .unwrap_err();
        let plans = work::take();
        assert_eq!(error, original);
        assert_eq!(plans, 2);
        assert_eq!(cache.head(&key, "default").unwrap(), parent);
    }

    #[test]
    fn planner_refusal_uses_original_view_context_without_replay() {
        let (cache, key, parent) = open("view-context", false);
        for index in [0, 7, LIMIT - 1] {
            let mut ops = invalid_batch(LIMIT, index);
            ops[index] = json!({"op":"set","path":"views/v:iso/omit","value":true});
            for tool in ["preview_ops", "apply_ops"] {
                let error =
                    call(&cache, tool, &batch_args(tool, &key, &parent, ops.clone())).unwrap_err();
                let plans = work::take();
                assert_eq!(plans, if index < 8 { 3 } else { 2 }, "{tool}: {error}");
                if index < 8 {
                    assert!(
                        error
                            .message
                            .contains(&format!("Replace ops[{index}]. Corrected op:"))
                    );
                    let corrected: Value =
                        serde_json::from_str(error.message.split_once("Corrected op: ").unwrap().1)
                            .unwrap();
                    assert_eq!(corrected["path"], "views/iso/omit");
                } else {
                    assert_eq!(error.message, "There is no view called \"v:iso\".");
                }
                assert_eq!(cache.head(&key, "default").unwrap(), parent);
            }
        }
    }

    #[test]
    fn batch_limit_schemas_transactions_and_stale_parent() {
        let (cache, key, parent) = open("limit", false);
        // Validate the whole transaction: a temporary bad value may be overwritten.
        let result = call(
            &cache,
            "apply_ops",
            &json!({"part_ref":key,"parent":parent,"ops":[
                {"op":"set","path":"sheet/size","value":"invalid"},
                {"op":"set","path":"sheet/size","value":"A3"}
            ]}),
        )
        .unwrap();
        let head = result["spec_version"].as_str().unwrap();
        work::take();
        let error = call(
            &cache,
            "apply_ops",
            &json!({"part_ref":key,"parent":parent,"ops":invalid_batch(1,0)}),
        )
        .unwrap_err();
        assert!(error.message.starts_with("stale parent"));
        assert_eq!(error.choices, [head]);
        assert_eq!(work::take(), 0);
        for tool in ["preview_ops", "apply_ops"] {
            let input = schema(tool, false).unwrap();
            assert_eq!(MAX_OPS, LIMIT);
            assert!(description(tool).contains(&format!("up to {MAX_OPS}")));
            assert_eq!(input["properties"]["ops"]["maxItems"], MAX_OPS);
            let args = batch_args(tool, &key, head, invalid_batch(LIMIT + 1, LIMIT));
            assert!(validate(&input, &args).is_err());
            assert!(
                call(&cache, tool, &args)
                    .unwrap_err()
                    .message
                    .contains("32")
            );
            assert_eq!(work::take(), 0);
            assert_eq!(cache.head(&key, "default").unwrap(), head);
        }
        let ops = serde_json::from_value::<Vec<crate::items::Op>>(invalid_batch(LIMIT + 1, LIMIT))
            .unwrap();
        assert!(
            cache
                .apply(&key, &Spec::default(), &ops)
                .err()
                .unwrap()
                .message
                .contains("32")
        );
        assert!(
            cache
                .preview(&key, &Spec::default(), &ops)
                .unwrap_err()
                .message
                .contains("32")
        );
        assert!(
            cache
                .apply_version(&key, "default", head, &ops)
                .err()
                .unwrap()
                .message
                .contains("32")
        );
        assert_eq!(work::take(), 0);
    }

    #[test]
    fn dense_invalid_batch_latency_and_plans() {
        if std::env::var_os("DRAFTWRIGHT_CORPUS").is_none() {
            assert_ne!(
                std::env::var("DRAFTWRIGHT_CORPUS_REQUIRED").as_deref(),
                Ok("1")
            );
            return;
        }
        let (cache, key, parent) = open("dense", true);
        let mut maximum_plans = 0;
        for (name, count, index) in [
            ("beginning", LIMIT, 0),
            ("budget_boundary", LIMIT, 7),
            ("end", LIMIT, LIMIT - 1),
            ("over_limit", LIMIT + 1, LIMIT),
        ] {
            for tool in ["preview_ops", "apply_ops"] {
                let mut worst_ms = 0.0_f64;
                let mut plans = 0;
                for _ in 0..1 {
                    work::take();
                    let started = Instant::now();
                    let error = call(
                        &cache,
                        tool,
                        &batch_args(tool, &key, &parent, invalid_batch(count, index)),
                    )
                    .unwrap_err();
                    worst_ms = worst_ms.max(started.elapsed().as_secs_f64() * 1000.0);
                    plans = plans.max(work::take());
                    assert_eq!(error.code, "invalid");
                    assert_eq!(cache.head(&key, "default").unwrap(), parent);
                }
                eprintln!(
                    "H5_METRIC {tool} {name} ops={count} index={index} plans={plans} worst_ms={worst_ms:.3}"
                );
                maximum_plans = maximum_plans.max(plans);
            }
        }
        assert!(
            maximum_plans <= 2,
            "dense invalid batches used {maximum_plans} plans"
        );
    }
}
