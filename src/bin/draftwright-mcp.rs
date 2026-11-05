//! The MCP server over the document model (ADR R1): MCP over stdio, one JSON-RPC 2.0 message per
//! line, with one tool for each operation of [`draftwright::api`]. Results are their documents as
//! JSON text (and, from `review`, the drawing as SVG text); an invalid request is a tool error
//! carrying what would be accepted.

use std::io::{BufRead, Write};

use draftwright::api;
use serde_json::{Value, json};

/// The MCP revisions spoken, newest first. Tools are the same in each.
const VERSIONS: [&str; 4] = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"];

const INSTRUCTIONS: &str = "Draftwright makes engineering drawings from STEP files. A drawing \
is described by a spec (the author's decisions; JSON) and previewed as a plan (every view, the \
sheet and scale, each with whether it is a default and why, and any problems). Nothing is kept \
between calls: pass the spec to each call that takes one, and keep the latest. Usual order: plan \
the STEP with no spec to see the defaults; edit the spec to change what is wrong; plan or review \
again; render to write the files. A rendered drawing carries its spec, which the spec tool reads \
back, so work can resume from a drawing alone.";

const SPEC: &str = "The spec: the author's decisions, keyed by stable IDs. Omit it, or pass {}, \
for all defaults. Use the spec from the plan or the edit tool's result. Shape: {\"sheet\": \
{\"size\": \"A3\", \"scale\": \"2:1\", \"title\": {\"title\": \"BRACKET\", \"number\": \
\"DWG-001\", \"material\": \"6082-T6\"}}, \"views\": {\"iso\": {\"omit\": true}}, \
\"dimension_source\": \"auto\", \"dimensions\": {\"d:envelope/width.length\": {\"omit\": \
true}, \"d:envelope/height.length\": {\"tolerance\": \"±0.05\"}}, \"features\": \
{\"f:hole:1\": {\"fit\": \"H7\"}, \"f:hole_pattern:1\": {\"controls\": [{\"characteristic\": \
\"position\", \"tolerance\": 0.1, \"datums\": [\"A\"]}]}}, \"faces\": {\"bottom\": {\"datum\": \
\"A\"}}, \"notes\": [{\"id\": \"n:1\", \"text\": \"BREAK ALL SHARP \
EDGES\"}], \"tables\": [{\"id\": \"t:1\", \"rows\": [[\"REV\", \"DESCRIPTION\"], [\"A\", \
\"INITIAL RELEASE\"]], \"prefer\": \"tr\"}]} (a table's row 0 is its header; prefer is the corner \
it goes towards: tr, tl, br or bl). Unknown fields are refused.";

/// The tools: name, description, input schema.
fn tools() -> Value {
    let path = |what: &str| json!({ "type": "string", "description": what });
    let spec = json!({ "type": "object", "description": SPEC });
    let features = path(
        "Path of Python quiddity's feature recognition JSON for the same STEP file \
         (tools/recognise.py); without it the analysis has no features.",
    );
    let part = json!({
        "type": "integer",
        "minimum": 0,
        "description": "Draw one part of an assembly alone, in its own frame and with its PMI \
            only: its index, as the parts tool lists them. Without it the whole file is drawn as \
            one part, and an assembly's plan warns (assembly_drawn_whole), unless its bodies all \
            coincide (coincident_bodies) or lie within the largest one's box, when that part is \
            drawn alone (assembly_principal_drawn).",
    });
    let analysis = json!({
        "type": "object",
        "description": "The Analysis the analyse tool returned for this same STEP file, passed back \
            unchanged, so the part is not analysed again. Give it instead of features (it already \
            carries the features it was made with). Refused if it was made from another file.",
    });
    let mut listed = json!([
        {
            "name": "analyse",
            "description": "Read a STEP file and return its Analysis: the binding (the file's \
                SHA-256, the part's name and face count) and the envelope (the part's box, mm). \
                It is regenerated from the STEP, never edited. Pass it unchanged as plan, review \
                or render's analysis so they need not analyse the STEP again.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "step": path("Path of the STEP file."),
                    "features": features,
                    "part": part,
                },
                "required": ["step"],
            },
        },
        {
            "name": "plan",
            "description": "Plan the drawing of a STEP file under a spec, without drawing it: \
                the sheet (size, page, scale, title block), each view (id, direction, centre on \
                the page) and problems. Every item says whether it is a default and gives its \
                basis, the reason it was chosen, and its state: {\"kind\": \"shown\"}, \
                \"omitted_by_user\", \"not_authored\", \"covered\" (with \"by\", the item that \
                states it) or \"not_drawable\" (with \"reason_code\", the problem that says why). \
                Each problem has a code, severity, subjects (item IDs), refs (what else it names) \
                and, where one is known, a fix: edit operations to pass to the edit tool. The \
                plan's \"spec\" is the spec bound to this STEP file; keep it for later calls. \
                Give step (with features if any), or the analysis from the analyse tool, which \
                needs no step.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "step": path("Path of the STEP file; may be omitted when analysis is given."),
                    "features": features,
                    "analysis": analysis,
                    "spec": spec,
                    "part": part,
                },
                "required": [],
            },
        },
        {
            "name": "edit",
            "description": "Apply edits to a spec and return the edited spec. Nothing is \
                stored: pass the result to plan, review or render. Each edit is {\"op\": \"set\", \
                \"path\": …, \"value\": …}, {\"op\": \"remove\", \"path\": …} or {\"op\": \"add\", \
                \"path\": \"notes\", \"value\": {\"text\": \"DEBURR\"}} (a general note, numbered in \
                order; given no id it takes the next n:<k>; likewise \"path\": \"tables\" with \
                {\"rows\": [[…], …]} appends a table, taking the next t:<k>), with a path into the spec: \
                sheet/size (A4, A3, A2, A1, A0), sheet/scale (ISO 5455, e.g. 2:1, 1:1, \
                1:2), sheet/projection (first or third angle), \
                sheet/title/<field> (title, number, material, …), views/<front|plan|side|iso>/omit \
                (true leaves the view out), dimension_source (auto: the defaults less what is \
                omitted; authored: exactly the dimensions listed), dimensions/<id>/omit (true \
                leaves that dimension out; IDs are the plan's, d:<feature>/<parameter>, \
                with each / inside the ID written ~1, e.g. \
                dimensions/d:envelope~1width.length/omit), dimensions/<id>/tolerance \
                (printed after its value: \"±0.05\", or \"+0.1 -0.02\" for an upper and a lower \
                deviation), features/<id>/fit (an ISO 286 class on a hole, H7, G7, F8, or a \
                cylindrical boss, h6, g6, js6, k6, n6, p6; grades 5 to 11), features/<id>/datum and \
                faces/<bottom|top|left|right|front|back|face:n>/datum (a datum letter, A), \
                features/<id>/controls and faces/<face>/controls (feature control frames, a list \
                of {\"characteristic\": \"position\", \"tolerance\": 0.1, \"datums\": [\"A\"], \
                \"diameter\"?: true, \"modifier\"?: \"M\"|\"L\"}; add appends one), \
                features/<id>/finish and faces/<face>/finish ({\"ra\": 0.8, \"removal\"?: \
                required|prohibited|any}), sheet/default_finish (the same, beside the title block), \
                features/<id>/thread ({\"designation\": \"M6\", \"length\"?: 10}, on a hole, hole \
                pattern or turned step), features/<id>/knurl ({\"pattern\": \"straight\", \"pitch\": \
                0.8}, on a turned step), features/<id>/note and faces/<face>/note (text on a leader), \
                dimensions/<id>/view (front, \
                plan or side: the view that carries it, among those that can). An ID never names \
                a view, so it stays when the dimension moves to another view or the sheet or \
                scale changes; a PMI item's ID is its record's identity (pmi:<category>.<kind>:…). \
                A refused edit says why and, where it chose from a list, the choices; plan \
                validates the values against the part.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "spec": spec,
                    "ops": {
                        "type": "array",
                        "description": "The edits, applied in order.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "op": { "type": "string", "enum": ["set", "remove", "add"] },
                                "path": { "type": "string", "description": "Slash-separated, e.g. sheet/scale." },
                                "value": { "description": "The new value, for set; the item appended, for add." },
                            },
                            "required": ["op", "path"],
                        },
                    },
                },
                "required": ["ops"],
            },
        },
        {
            "name": "render",
            "description": "Draw a STEP file under a spec and write the drawing beside the \
                output stem, one file per format (out.svg, out.pdf, out.png; DXF export is disabled until it is fixed). Each file embeds \
                the spec. Returns the paths written and the problems (the plan's, and what could not \
                be drawn as planned).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "step": path("Path of the STEP file."),
                    "features": features,
                    "analysis": analysis,
                    "spec": spec,
                    "part": part,
                    "output": path("Output path; each format's extension is appended (a drawing format's extension \
                    already on it is replaced)."),
                    "formats": {
                        "type": "array",
                        "items": { "type": "string", "enum": api::FORMATS },
                        "description": "Which formats to write; svg and pdf if omitted.",
                    },
                    "dpi": {
                        "type": "number",
                        "description": "The PNG's resolution, pixels per inch (above 0); 150 if omitted.",
                    },
                },
                "required": ["step", "output"],
            },
        },
        {
            "name": "review",
            "description": "Draw a STEP file under a spec without writing a file, to check it by \
                eye: returns the problems (the plan's, and what could not be drawn as planned) and \
                lint's findings as JSON, then the drawing as \
                SVG text (page in mm; layers part, hidden and dims). Lint judges the drawn sheet \
                against the part alone: each finding names the features (subjects), what is \
                wrong, and a fix.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "step": path("Path of the STEP file."),
                    "features": features,
                    "analysis": analysis,
                    "spec": spec,
                    "part": part,
                },
                "required": ["step"],
            },
        },
        {
            "name": "spec",
            "description": "Read back the spec a drawing (SVG, DXF, PDF or PNG) was made from, to \
                resume work on it.",
            "inputSchema": {
                "type": "object",
                "properties": { "drawing": path("Path of a drawing that draftwright rendered.") },
                "required": ["drawing"],
            },
        },
        {
            "name": "parts",
            "description": "List the distinct parts of a STEP file: each one's index (part), name, \
                product_id, face count and number of placements (instances). Pass an index as \
                part to analyse, plan, review or render to draw that part of an assembly alone.",
            "inputSchema": {
                "type": "object",
                "properties": { "step": path("Path of the STEP file.") },
                "required": ["step"],
            },
        },
        {
            "name": "pack",
            "description": "Build an assembly drawing pack: GA, made-part sheets and BOM. Returns one compact pack report and a short summary by default. Files are exported only when formats is supplied, as resource links: read their URIs with resources/read for JSON text or base64 blobs. Requesting json replaces the inline report; formats must be unique.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "step": path("STEP path or registered part ref."),
                    "part_ref": path("Registered source assembly, alternative to step."),
                    "spec": { "type": "object", "description": "PackSpec (draftwright.pack-spec/1)." },
                    "formats": { "type": "array", "items": { "type": "string", "enum": ["pdf", "csv", "json"] }, "uniqueItems": true, "maxItems": 3, "default": [], "description": "Explicit exports only; omitted or empty returns just the report and summary. JSON replaces the inline report. Read file resource links with resources/read." },
                },
                "required": [],
                "anyOf": [{"required": ["step"]}, {"required": ["part_ref"]}],
            },
        },
    ]);
    for (name, description) in [
        (
            "spec_schema",
            "Read the complete author Spec schema without opening a part; the same schema ships as schemas/spec.json.",
        ),
        (
            "item_options",
            "Discover settable Spec paths, choices and placements for an item using part_ref and id; obtain part_ref through draftwright mcp-stdio inspect_part.",
        ),
    ] {
        let mut input = draftwright::mcp::schema(name, false).expect("published discovery schema");
        if name == "item_options" {
            // The standalone adapter presents required inputs explicitly, even when the
            // compact schema shares their string shape through additionalProperties.
            input["properties"]["part_ref"] = path("Content key returned by inspect_part.");
            input["properties"]["id"] = path("The stable item ID, for example sheet or v:plan.");
        }
        input
            .as_object_mut()
            .expect("object schema")
            .entry("required")
            .or_insert_with(|| json!([]));
        listed.as_array_mut().expect("tool list").push(json!({
            "name":name, "description":description, "inputSchema":input,
        }));
    }
    listed
}

fn main() {
    let stdout = std::io::stdout();
    for line in std::io::stdin().lock().lines() {
        let Ok(line) = line else { break };
        if line.trim().is_empty() {
            continue;
        }
        let reply = match serde_json::from_str::<Value>(&line) {
            Ok(message) => answer(&message),
            Err(e) => Some(failure(Value::Null, -32700, &format!("Parse error: {e}"))),
        };
        if let Some(reply) = reply {
            let mut out = stdout.lock();
            if writeln!(out, "{reply}").and_then(|_| out.flush()).is_err() {
                break;
            }
        }
    }
}

fn failure(id: Value, code: i64, message: &str) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "error": { "code": code, "message": message } })
}

/// The reply to one message; none to a notification.
fn answer(message: &Value) -> Option<Value> {
    let Some(method) = message.get("method").and_then(Value::as_str) else {
        let id = message.get("id").cloned().unwrap_or(Value::Null);
        return Some(failure(
            id,
            -32600,
            "Invalid request: a JSON-RPC 2.0 object with a method",
        ));
    };
    // A notification (no id), such as notifications/initialized, needs no reply.
    let id = message.get("id")?.clone();
    let params = message.get("params").cloned().unwrap_or(json!({}));
    let result = match method {
        "initialize" => {
            let asked = params["protocolVersion"].as_str().unwrap_or("");
            let version = VERSIONS
                .iter()
                .find(|v| **v == asked)
                .unwrap_or(&VERSIONS[0]);
            json!({
                "protocolVersion": version,
                "capabilities": { "tools": {}, "resources": {} },
                "serverInfo": { "name": "draftwright", "version": env!("CARGO_PKG_VERSION") },
                "instructions": INSTRUCTIONS,
            })
        }
        "ping" => json!({}),
        "tools/list" => json!({ "tools": tools() }),
        "resources/read" => {
            return draftwright::mcp::reply(&draftwright::stateless::Cache::environment(), message);
        }
        "tools/call" => {
            let name = params["name"].as_str().unwrap_or("");
            if !api::OPERATIONS.contains(&name) {
                return Some(failure(id, -32602, &format!("Unknown tool: {name:?}")));
            }
            let args = params.get("arguments").cloned().unwrap_or(json!({}));
            let text = |x: &Value| json!({ "type": "text", "text": serde_json::to_string_pretty(x).expect("JSON") });
            let called = if name == "pack" {
                draftwright::mcp::pack_tool(&draftwright::stateless::Cache::environment(), &args)
            } else {
                api::call(name, &args)
            };
            match called {
                Ok(result) if name == "pack" => result,
                Ok(result) if name == "review" => json!({
                    "content": [
                        text(&json!({
                            "summary": result["summary"],
                            "problems": result["problems"],
                            "lint": result["lint"],
                            "structural": result["structural"],
                        })),
                        { "type": "text", "text": result["svg"] },
                    ],
                    "isError": false,
                }),
                Ok(result) => json!({ "content": [text(&result)], "isError": false }),
                Err(error) => json!({
                    "content": [text(&json!({ "error": error }))],
                    "isError": true,
                }),
            }
        }
        other => return Some(failure(id, -32601, &format!("Method not found: {other}"))),
    };
    Some(json!({ "jsonrpc": "2.0", "id": id, "result": result }))
}
