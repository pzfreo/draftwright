//! `draftwright-mcp`, driven as an MCP client drives it: JSON-RPC 2.0 lines through its stdin and
//! stdout.

use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use serde_json::{Value, json};

fn part(name: &str) -> String {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts")
        .join(name)
        .display()
        .to_string()
}

fn scratch(name: &str) -> PathBuf {
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR")).join("mcp");
    std::fs::create_dir_all(&dir).unwrap();
    dir.join(name)
}

/// The replies to *messages*, sent to one server after the initialisation handshake, by id.
fn session(messages: &[Value]) -> Vec<Value> {
    session_with_cache(messages, None)
}

fn session_with_cache(messages: &[Value], cache: Option<&Path>) -> Vec<Value> {
    let mut command = Command::new(env!("CARGO_BIN_EXE_draftwright-mcp"));
    if let Some(cache) = cache {
        command.env("DRAFTWRIGHT_CACHE", cache);
    }
    let mut child = command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let mut stdin = child.stdin.take().unwrap();
    let mut stdout = child.stdout.take().unwrap();
    let reader = std::thread::spawn(move || {
        let mut bytes = Vec::new();
        std::io::Read::read_to_end(&mut stdout, &mut bytes).unwrap();
        bytes
    });
    let handshake = [
        json!({ "jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": { "name": "test", "version": "0" } } }),
        json!({ "jsonrpc": "2.0", "method": "notifications/initialized" }),
    ];
    for m in handshake.iter().chain(messages) {
        writeln!(stdin, "{m}").unwrap();
    }
    drop(stdin);
    assert!(child.wait().unwrap().success());
    let replies: Vec<Value> = String::from_utf8(reader.join().unwrap())
        .unwrap()
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect();
    // One reply to each request, none to the notification.
    assert_eq!(replies.len(), messages.len() + 1);
    let init = &replies[0]["result"];
    assert_eq!(init["protocolVersion"], "2025-06-18");
    assert_eq!(init["serverInfo"]["name"], "draftwright");
    assert!(init["capabilities"]["tools"].is_object());
    assert!(init["capabilities"]["resources"].is_object());
    replies[1..].to_vec()
}

fn call(id: u32, tool: &str, arguments: Value) -> Value {
    json!({ "jsonrpc": "2.0", "id": id, "method": "tools/call",
        "params": { "name": tool, "arguments": arguments } })
}

/// A tool result's first content, read as JSON.
fn first(reply: &Value) -> Value {
    serde_json::from_str(reply["result"]["content"][0]["text"].as_str().unwrap()).unwrap()
}

#[test]
fn the_tools_are_the_operations() {
    let replies = session(&[json!({ "jsonrpc": "2.0", "id": 1, "method": "tools/list" })]);
    let tools = replies[0]["result"]["tools"].as_array().unwrap();
    let names: Vec<&str> = tools.iter().map(|t| t["name"].as_str().unwrap()).collect();
    assert_eq!(names, draftwright::api::OPERATIONS);
    for t in tools {
        assert!(t["description"].as_str().unwrap().len() > 40);
        assert_eq!(t["inputSchema"]["type"], "object");
        for required in t["inputSchema"]["required"].as_array().unwrap() {
            assert!(t["inputSchema"]["properties"][required.as_str().unwrap()].is_object());
        }
    }
    // plan, render and review take the analyse tool's Analysis, inline; plan then needs no step.
    for t in tools {
        let name = t["name"].as_str().unwrap();
        let schema = &t["inputSchema"];
        let takes = ["plan", "render", "review"].contains(&name);
        assert_eq!(
            schema["properties"]["analysis"].is_object(),
            takes,
            "{name}"
        );
        if takes {
            assert_eq!(schema["properties"]["analysis"]["type"], "object");
            let required = schema["required"].as_array().unwrap();
            assert_eq!(required.contains(&json!("step")), name != "plan", "{name}");
        }
    }
}

/// The analyse tool's result, passed back as `analysis`, gives the same plan and review as
/// analysing the STEP again; one made from another file is a tool error naming both digests.
#[test]
fn an_analysis_is_passed_back_in_place_of_analysing() {
    let step = part("turned.step");
    let replies = session(&[
        call(1, "analyse", json!({ "step": step })),
        call(2, "analyse", json!({ "step": part("prismatic.step") })),
    ]);
    let (analysis, other) = (first(&replies[0]), first(&replies[1]));
    let replies = session(&[
        call(1, "plan", json!({ "step": step })),
        call(2, "plan", json!({ "analysis": analysis })),
        call(3, "review", json!({ "step": step })),
        call(4, "review", json!({ "step": step, "analysis": analysis })),
        call(
            5,
            "render",
            json!({ "step": step, "analysis": other,
            "output": scratch("other").display().to_string() }),
        ),
    ]);
    assert_eq!(replies[0]["result"]["isError"], false);
    assert_eq!(replies[0]["result"], replies[1]["result"]);
    assert_eq!(replies[2]["result"]["isError"], false);
    assert_eq!(replies[2]["result"], replies[3]["result"]);
    assert_eq!(replies[4]["result"]["isError"], true);
    let error = &first(&replies[4])["error"];
    assert_eq!(error["code"], "invalid", "{error}");
    for digest in [&analysis, &other] {
        let sha = digest["binding"]["source_sha256"].as_str().unwrap();
        assert!(error["message"].as_str().unwrap().contains(sha), "{error}");
    }
}

#[test]
fn a_drawing_is_planned_edited_rendered_and_resumed() {
    let step = part("turned.step");
    let out = scratch("turned");
    let replies = session(&[
        call(1, "plan", json!({ "step": step })),
        call(
            2,
            "edit",
            json!({ "ops": [{ "op": "set", "path": "views/side/omit", "value": true }] }),
        ),
        call(
            3,
            "edit",
            json!({ "ops": [{ "op": "rename", "path": "sheet/size" }] }),
        ),
        call(
            4,
            "plan",
            json!({ "step": step, "spec": { "views": { "top": {} } } }),
        ),
        call(5, "draw", json!({})),
        json!({ "jsonrpc": "2.0", "id": 6, "method": "unknown/method" }),
        json!({ "jsonrpc": "2.0", "id": 7, "method": "ping" }),
    ]);
    let plan = first(&replies[0]);
    assert_eq!(replies[0]["result"]["isError"], false);
    assert_eq!(plan["views"].as_array().unwrap().len(), 4);
    let spec = first(&replies[1]);
    assert_eq!(spec["views"]["side"]["omit"], true);
    // Refusals are tool errors that say what would be accepted.
    assert_eq!(replies[2]["result"]["isError"], true);
    assert_eq!(
        first(&replies[2])["error"]["choices"],
        json!(["set", "remove", "add"])
    );
    assert_eq!(replies[3]["result"]["isError"], true);
    let views = &first(&replies[3])["error"]["choices"];
    assert!(
        views.as_array().unwrap().contains(&json!("plan")),
        "{views}"
    );
    // An unknown tool or method is a protocol error.
    assert_eq!(replies[4]["error"]["code"], -32602);
    assert_eq!(replies[5]["error"]["code"], -32601);
    assert_eq!(replies[6]["result"], json!({}));

    let replies = session(&[
        call(
            1,
            "render",
            json!({ "step": step, "spec": spec,
            "output": out.display().to_string(), "formats": ["svg", "pdf", "png"] }),
        ),
        call(2, "review", json!({ "step": step, "spec": spec })),
    ]);
    let written = first(&replies[0])["written"].clone();
    assert_eq!(written.as_array().unwrap().len(), 3, "{}", replies[0]);
    let review = replies[1]["result"]["content"].as_array().unwrap();
    assert_eq!(review.len(), 2);
    assert!(review[1]["text"].as_str().unwrap().contains("<svg"));
    let findings: Value = serde_json::from_str(review[0]["text"].as_str().unwrap()).unwrap();
    assert_eq!(findings["lint"], json!([]), "{findings}");
    let reads: Vec<Value> = written
        .as_array()
        .unwrap()
        .iter()
        .enumerate()
        .map(|(i, f)| call(i as u32 + 1, "spec", json!({ "drawing": f })))
        .collect();
    for reply in session(&reads) {
        let back = first(&reply);
        assert_eq!(back["views"]["side"]["omit"], true, "{reply}");
        assert_eq!(back["binding"]["part"], "turned");
    }
}

/// Geometry the kernel did not resolve is a tool error coded `failed`, naming the cause.
#[test]
fn unresolved_geometry_is_a_failed_tool_error() {
    let replies = session(&[call(
        1,
        "review",
        json!({ "step": part("unresolved.step") }),
    )]);
    assert_eq!(replies[0]["result"]["isError"], true);
    let error = &first(&replies[0])["error"];
    assert_eq!(error["code"], "failed", "{error}");
    assert!(
        error["message"].as_str().unwrap().contains("faces [3]"),
        "{error}"
    );
}

#[test]
fn pack_defaults_are_one_compact_report_without_exports_on_large_assemblies() {
    use draftwright::pack::{self, PackSpec};
    for fixture in ["gramil", "bom-small"] {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join(format!("tests/fixtures/assembly/{fixture}.step.gz"));
        let cache = scratch(&format!("pack-default-{fixture}-{}", std::process::id()));
        let report =
            serde_json::to_value(pack::build(&path, &PackSpec::default()).unwrap().report).unwrap();
        let replies = session_with_cache(
            &[
                call(1, "pack", json!({"step":path})),
                call(2, "pack", json!({"step":path,"formats":[]})),
            ],
            Some(&cache),
        );
        for reply in replies {
            let result = &reply["result"];
            assert_eq!(result["isError"], false, "{reply}");
            assert!(
                result.get("structuredContent").is_none(),
                "report must not be duplicated"
            );
            let blocks = result["content"].as_array().unwrap();
            assert_eq!(blocks.len(), 2);
            assert!(blocks.iter().all(|block| block["type"] == "text"
                && block.get("blob").is_none()
                && block.get("resource").is_none()));
            let text = blocks[0]["text"].as_str().unwrap();
            assert_eq!(serde_json::from_str::<Value>(text).unwrap(), report);
            assert_eq!(
                text,
                serde_json::to_string(&report).unwrap(),
                "compact JSON"
            );
            let summary = blocks[1]["text"].as_str().unwrap();
            assert!(summary.starts_with("Drawing pack:"));
            assert!(summary.len() < 200);
            assert!(serde_json::from_str::<Value>(summary).is_err());
            let wire = serde_json::to_vec(&reply).unwrap();
            assert!(wire.len() < 96 * 1024, "{fixture}: {} bytes", wire.len());
            assert_eq!(
                String::from_utf8(wire.clone())
                    .unwrap()
                    .matches(pack::REPORT_SCHEMA)
                    .count(),
                1
            );
            eprintln!("MCP_PACK_DEFAULT {fixture}: {} bytes", wire.len() + 1);
        }
        assert!(
            !cache.exists(),
            "default pack must not create file artifacts"
        );
    }
}

#[test]
fn pack_explicit_exports_are_readable_resources_and_json_replaces_inline_report() {
    use draftwright::pack::{self, PackSpec};
    let path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/assembly/pack-small.step.gz");
    let cache = scratch(&format!("pack-exports-{}", std::process::id()));
    let key = draftwright::stateless::Cache::new(&cache)
        .register(&path, None)
        .unwrap();
    let replies = session_with_cache(
        &[
            call(
                1,
                "pack",
                json!({"part_ref":key,"formats":["pdf","csv","json"]}),
            ),
            call(2, "pack", json!({"step":path,"formats":["pdf"]})),
            call(3, "pack", json!({"step":path,"formats":["json"]})),
            call(4, "pack", json!({"step":path,"formats":["csv","csv"]})),
            call(5, "pack", json!({"step":path,"formats":["json","json"]})),
            call(6, "pack", json!({"step":path,"formats":["svg"]})),
            call(7, "pack", json!({"step":path,"formats":"pdf"})),
            json!({"jsonrpc":"2.0","id":8,"method":"tools/list"}),
        ],
        Some(&cache),
    );
    let listing = session_with_cache(
        &[json!({"jsonrpc":"2.0","id":1,"method":"resources/list"})],
        Some(&cache),
    );
    assert_eq!(listing[0]["result"], json!({"resources":[]}));
    let content = replies[0]["result"]["content"].as_array().unwrap();
    assert_eq!(replies[0]["result"]["isError"], false);
    assert_eq!(content.len(), 4);
    assert!(
        content[0]["text"]
            .as_str()
            .unwrap()
            .starts_with("Drawing pack:")
    );
    assert!(serde_json::to_vec(&replies[0]).unwrap().len() < 2048);
    for block in &content[1..] {
        assert_eq!(block["type"], "resource_link");
        assert!(block.get("text").is_none() && block.get("blob").is_none());
    }
    assert_eq!(replies[1]["result"]["content"].as_array().unwrap().len(), 3);
    let (_, expected) = pack::render(&path, &PackSpec::default(), &pack::FORMATS).unwrap();
    assert_eq!(
        first(&replies[1]),
        serde_json::from_slice::<Value>(&expected[2].1).unwrap()
    );
    assert_eq!(replies[2]["result"]["content"].as_array().unwrap().len(), 2);
    for reply in &replies[3..7] {
        assert_eq!(reply["result"]["isError"], true, "{reply}");
        assert_eq!(first(reply)["error"]["code"], "invalid");
    }
    let tools = replies[7]["result"]["tools"].as_array().unwrap();
    let schema = &tools.iter().find(|t| t["name"] == "pack").unwrap()["inputSchema"]["properties"]
        ["formats"];
    assert_eq!(schema["uniqueItems"], true);
    assert_eq!(schema["default"], json!([]));
    let reads: Vec<_> = content[1..].iter().enumerate().map(|(i, link)| json!({"jsonrpc":"2.0","id":i+1,"method":"resources/read","params":{"uri":link["uri"]}})).collect();
    let resources = session_with_cache(&reads, Some(&cache));
    for ((reply, link), (format, bytes)) in resources.iter().zip(&content[1..]).zip(expected) {
        let resource = &reply["result"]["contents"][0];
        assert_eq!(resource["uri"], link["uri"]);
        assert_eq!(resource["mimeType"], link["mimeType"]);
        assert_eq!(link["name"], format!("pack.{format}"));
        assert_eq!(link["size"], bytes.len());
        let actual = if format == "json" {
            assert!(resource.get("blob").is_none());
            resource["text"].as_str().unwrap().as_bytes().to_vec()
        } else {
            assert!(resource.get("text").is_none());
            decode_pack_base64(resource["blob"].as_str().unwrap())
        };
        assert_eq!(actual, bytes, "{format}");
    }
    let bad = session_with_cache(
        &[
            json!({"jsonrpc":"2.0","id":1,"method":"resources/read","params":{"uri":"draftwright:../../outside.pdf"}}),
        ],
        Some(&cache),
    );
    assert_eq!(bad[0]["error"]["code"], -32602);
    std::fs::remove_dir_all(cache).unwrap();
}

fn decode_pack_base64(encoded: &str) -> Vec<u8> {
    let mut output = Vec::new();
    let mut bits = 0u32;
    let mut count = 0;
    for byte in encoded.bytes().take_while(|b| *b != b'=') {
        let value = match byte {
            b'A'..=b'Z' => byte - b'A',
            b'a'..=b'z' => byte - b'a' + 26,
            b'0'..=b'9' => byte - b'0' + 52,
            b'+' => 62,
            b'/' => 63,
            _ => panic!("invalid base64"),
        };
        bits = (bits << 6) | u32::from(value);
        count += 6;
        if count >= 8 {
            count -= 8;
            output.push((bits >> count) as u8);
        }
    }
    output
}

#[test]
fn spec_part_selection_is_applied_by_the_one_shot_tool() {
    let step = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/steerability/pivot-mount-plate.step.gz");
    let replies = session(&[call(1, "plan", json!({"step":step,"spec":{"part":1}}))]);
    assert_ne!(replies[0]["result"]["isError"], true, "{:?}", replies[0]);
    assert_eq!(first(&replies[0])["binding"]["part_index"], 1);
}
