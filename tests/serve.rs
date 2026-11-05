//! `draftwright serve`, driven as a host drives it: JSON lines through its stdin and stdout.

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
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR")).join("serve");
    std::fs::create_dir_all(&dir).unwrap();
    dir.join(name)
}

/// The replies to *lines*, sent to one `draftwright serve` until its stdin closes.
fn serve(lines: &[String]) -> Vec<Value> {
    let mut child = Command::new(env!("CARGO_BIN_EXE_draftwright"))
        .arg("serve")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let mut stdin = child.stdin.take().unwrap();
    // Drain responses concurrently with large inline Analysis requests.
    let requests = lines.to_vec();
    let writer = std::thread::spawn(move || {
        for line in requests {
            writeln!(stdin, "{line}").unwrap();
        }
    });
    let out = child.wait_with_output().unwrap();
    writer.join().unwrap();
    assert!(out.status.success());
    String::from_utf8(out.stdout)
        .unwrap()
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect()
}

#[test]
fn the_service_answers_each_line() {
    let step = part("prismatic.step");
    let out = scratch("prismatic");
    let edit = json!({ "id": 3, "op": "edit", "spec": {},
        "ops": [{ "op": "set", "path": "views/iso/omit", "value": true },
                { "op": "set", "path": "sheet/title/material", "value": "Stahl ß ]]>" }] });
    let replies = serve(&[
        json!({ "id": 1, "op": "hello" }).to_string(),
        "not json".into(),
        json!({ "id": 2, "op": "plan", "step": step }).to_string(),
        edit.to_string(),
        json!({ "id": 4, "op": "edit", "spec": {},
            "ops": [{ "op": "rename", "path": "sheet/size" }] })
        .to_string(),
        json!({ "id": 5, "op": "plan", "step": step,
            "spec": { "sheet": { "scale": "3:1" } } })
        .to_string(),
        json!({ "id": 6, "op": "draw" }).to_string(),
        json!({ "id": 7, "op": "plan" }).to_string(),
    ]);
    assert_eq!(replies.len(), 8);
    assert_eq!(replies[0]["result"]["protocol"], 1);
    assert_eq!(replies[0]["result"]["ops"][0], "analyse");
    assert_eq!(replies[1]["ok"], false);
    assert_eq!(replies[1]["error"]["code"], "invalid");
    assert_eq!(replies[2]["id"], 2);
    assert_eq!(replies[2]["result"]["schema"], "draftwright.plan/1");
    assert_eq!(replies[2]["result"]["views"].as_array().unwrap().len(), 4);
    let spec = replies[3]["result"].clone();
    assert_eq!(spec["views"]["iso"]["omit"], true);
    assert_eq!(
        replies[4]["error"]["choices"],
        json!(["set", "remove", "add"])
    );
    assert!(
        replies[5]["error"]["choices"]
            .as_array()
            .unwrap()
            .contains(&json!("2:1"))
    );
    assert_eq!(replies[6]["error"]["code"], "invalid");
    assert!(
        replies[7]["error"]["message"]
            .as_str()
            .unwrap()
            .contains("step")
    );

    // Render with the edited spec, then read the spec back from every format.
    let replies = serve(&[
        json!({ "id": 1, "op": "render", "step": step, "spec": spec,
            "output": out.display().to_string() })
        .to_string(),
        json!({ "id": 2, "op": "review", "step": step, "spec": spec }).to_string(),
        json!({ "id": 3, "op": "render", "step": step, "output": out.display().to_string(),
            "formats": ["tiff"] })
        .to_string(),
        json!({ "id": 4, "op": "render", "step": step, "spec": spec,
            "output": out.display().to_string(), "formats": ["png"], "dpi": 50 })
        .to_string(),
    ]);
    assert_eq!(replies[0]["ok"], true, "{}", replies[0]);
    let mut written = replies[0]["result"]["written"].as_array().unwrap().clone();
    assert_eq!(written.len(), 2);
    assert!(
        replies[1]["result"]["svg"]
            .as_str()
            .unwrap()
            .starts_with("<?xml")
    );
    let problems = replies[1]["result"]["problems"].as_array().unwrap();
    assert_eq!(problems.len(), 2, "{problems:?}");
    assert_eq!(
        problems[1],
        json!({
            "id": "p:unclaimed_geometry:sheet", "code": "unclaimed_geometry", "severity": "info",
            "impact": "advisory", "group": "unclaimed_geometry",
            "message": "Analysis features and dimensioned stock claim 6 of 10 faces; 4 faces are unclaimed. Review the geometry: ordinary stock faces may also be unclaimed; this is not a count of missed features.",
            "subjects": ["face:2", "face:3", "face:7", "face:8"]
        })
    );
    assert_eq!(problems[0]["code"], "general_tolerance_missing");
    assert_eq!(problems[0]["severity"], "warning");
    assert_eq!(problems[0]["impact"], "decision");
    assert_eq!(
        replies[1]["result"]["summary"]["impacts"]["decision"]["findings"],
        1
    );
    assert_eq!(
        replies[1]["result"]["summary"]["groups"]["unclaimed_geometry"],
        1
    );
    assert_eq!(problems[0]["subjects"], json!(["sheet"]));
    assert_eq!(
        problems[0]["fix"][0],
        json!({ "op": "set", "path": "sheet/title/tolerance", "value": "UNSPECIFIED" })
    );
    let fixed: Vec<draftwright::items::Op> =
        serde_json::from_value(problems[0]["fix"].clone()).unwrap();
    // A4 at 2:1 without the isometric, at normal spacing since its trial is judged by its
    // ink rather than an empty band past the border (#163; compact lanes were the fallback
    // that held it there before). The retained layout keeps no spacing override.
    assert_eq!(fixed.len(), 5);
    for (path, value) in [
        ("sheet/keep_layout", json!(true)),
        ("sheet/size", json!("A4")),
        ("sheet/scale", json!("2:1")),
        ("sheets/max", json!(1)),
    ] {
        assert_eq!(
            fixed.iter().find(|op| op.path == path).unwrap().value,
            Some(value)
        );
    }
    assert!(!fixed.iter().any(|op| op.path == "sheet/dimension_spacing"));
    // No feature-size requirements; the sheet still proves every overall extent.
    assert_eq!(replies[1]["result"]["lint"], json!([]));
    assert_eq!(replies[2]["error"]["choices"], json!(["svg", "pdf", "png"]));
    assert_eq!(replies[3]["ok"], true, "{}", replies[3]);
    written.extend(replies[3]["result"]["written"].as_array().unwrap().clone());
    assert_eq!(written.len(), 3);
    let reads: Vec<String> = written
        .iter()
        .enumerate()
        .map(|(i, f)| json!({ "id": i, "op": "spec", "drawing": f }).to_string())
        .collect();
    for reply in serve(&reads) {
        let back = &reply["result"];
        assert_eq!(back["sheet"]["title"]["material"], "Stahl ß ]]>", "{reply}");
        assert_eq!(back["views"]["iso"]["omit"], true);
        assert_eq!(back["binding"]["part"], "prismatic");
    }
}

/// A part whose geometry the kernel did not resolve (`unresolved.step`, see tests/model.rs) is
/// answered `failed`, naming the cause, and the service goes on to the next line.
#[test]
fn the_service_answers_unresolved_geometry_as_failed() {
    let step = part("unresolved.step");
    let out = scratch("unresolved");
    let replies = serve(&[
        json!({ "id": 1, "op": "analyse", "step": step }).to_string(),
        json!({ "id": 2, "op": "render", "step": step, "output": out.display().to_string() })
            .to_string(),
        json!({ "id": 3, "op": "plan", "step": part("turned.step") }).to_string(),
    ]);
    for reply in &replies[..2] {
        assert_eq!(reply["ok"], false, "{reply}");
        assert_eq!(reply["error"]["code"], "failed", "{reply}");
        let message = reply["error"]["message"].as_str().unwrap();
        assert!(
            message.contains("did not resolve its geometry"),
            "{message}"
        );
    }
    assert_eq!(replies[2]["ok"], true);
}

/// An Analysis from `analyse`, passed back inline as `analysis`, stands in for analysing the STEP
/// again: the same replies, and plan needs no `step`. One made from another file is refused.
#[test]
fn the_service_takes_an_analysis_in_place_of_analysing() {
    let step = part("plate.step");
    let features = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/recognition/plate.json")
        .display()
        .to_string();
    let replies = serve(&[
        json!({ "id": 1, "op": "analyse", "step": step, "features": features }).to_string(),
        json!({ "id": 2, "op": "analyse", "step": part("prismatic.step") }).to_string(),
    ]);
    let (analysis, other) = (&replies[0]["result"], &replies[1]["result"]);
    let output = scratch("plate").display().to_string();
    let replies = serve(&[
        json!({ "id": 1, "op": "plan", "step": step, "features": features }).to_string(),
        json!({ "id": 2, "op": "plan", "analysis": analysis }).to_string(),
        json!({ "id": 3, "op": "review", "step": step, "features": features }).to_string(),
        json!({ "id": 4, "op": "review", "step": step, "analysis": analysis }).to_string(),
        json!({ "id": 5, "op": "render", "step": step, "analysis": analysis,
            "output": output, "formats": ["svg"] })
        .to_string(),
        json!({ "id": 6, "op": "review", "step": step, "analysis": other }).to_string(),
        json!({ "id": 7, "op": "plan", "step": step, "features": features,
            "analysis": analysis })
        .to_string(),
    ]);
    assert_eq!(replies[0]["ok"], true, "{}", replies[0]);
    assert_eq!(replies[0]["result"], replies[1]["result"]);
    assert_eq!(replies[2]["ok"], true, "{}", replies[2]);
    assert_eq!(replies[2]["result"], replies[3]["result"]);
    assert_eq!(replies[4]["ok"], true, "{}", replies[4]);
    assert_eq!(
        std::fs::read_to_string(replies[4]["result"]["written"][0].as_str().unwrap()).unwrap(),
        replies[2]["result"]["svg"].as_str().unwrap()
    );
    for (reply, says) in [
        (
            &replies[5],
            other["binding"]["source_sha256"].as_str().unwrap(),
        ),
        (
            &replies[5],
            analysis["binding"]["source_sha256"].as_str().unwrap(),
        ),
        (&replies[6], "not both"),
    ] {
        assert_eq!(reply["error"]["code"], "invalid", "{reply}");
        assert!(
            reply["error"]["message"].as_str().unwrap().contains(says),
            "{reply}"
        );
    }
}

#[test]
fn the_service_keeps_a_session_and_answers_deltas_and_queries() {
    let replies = serve(&[
        json!({"op":"open", "step":part("plate.step"), "features":Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/recognition/plate.json")}).to_string(),
        json!({"op":"apply", "commit":false, "ops":[{"op":"set","path":"sheet/title/material","value":"Steel"}]}).to_string(),
        json!({"op":"item","item":"sheet"}).to_string(),
        json!({"op":"apply", "ops":[{"op":"set","path":"sheet/title/material","value":"Steel"}]}).to_string(),
        json!({"op":"undo"}).to_string(),
        json!({"op":"redo"}).to_string(),
        json!({"op":"items","filter":{"kind":"dimension"}}).to_string(),
        json!({"op":"options","item":"sheet"}).to_string(),
        json!({"op":"problems","filter":{"severity":"error"}}).to_string(),
        json!({"op":"plan","step":part("prismatic.step")}).to_string(),
    ]);
    for reply in &replies {
        assert_eq!(reply["ok"], true, "{reply}");
    }
    assert_eq!(replies[1]["result"], replies[3]["result"]);
    assert_eq!(replies[2]["result"]["item"]["kind"], "sheet");
    assert!(!replies[6]["result"].as_array().unwrap().is_empty());
    assert_eq!(replies[9]["result"]["schema"], "draftwright.plan/1");
}

#[test]
fn item_options_requires_nested_arguments_to_separate_item_and_request_ids() {
    let step = PathBuf::from(part("prismatic.step"));
    let key = draftwright::stateless::Cache::environment()
        .register(&step, None)
        .unwrap();
    let arguments = json!({"part_ref":key,"id":"sheet"});
    let expected = draftwright::api::call("item_options", &arguments).unwrap();
    let mut requests = Vec::new();
    for op in ["item_options", "mcp_item_options"] {
        for id in [json!(17), json!("sheet")] {
            requests.push(json!({"id":id,"op":op,"part_ref":key}).to_string());
        }
        for bad in [Value::Null, json!([]), json!("sheet")] {
            requests.push(json!({"id":18,"op":op,"arguments":bad}).to_string());
        }
    }
    // serde_json keeps the last duplicate key: it must still refuse the ambiguous shape.
    requests.push(format!(
        r#"{{"id":19,"op":"item_options","part_ref":{},"id":"sheet"}}"#,
        json!(key)
    ));
    requests.push(json!({"id":20,"op":"item_options","arguments":arguments}).to_string());
    requests.push(json!({"id":21,"op":"mcp_item_options","arguments":arguments}).to_string());
    let replies = serve(&requests);
    assert_eq!(replies.len(), requests.len());
    for (request, reply) in requests.iter().zip(&replies).take(requests.len() - 2) {
        let request: Value = serde_json::from_str(request).unwrap();
        assert_eq!(reply["id"], request["id"]);
        assert_eq!(reply["ok"], false, "{reply}");
        assert_eq!(reply["error"]["code"], "invalid");
        let message = reply["error"]["message"].as_str().unwrap();
        assert!(
            message.contains("nested arguments object")
                && message.contains("arguments.id")
                && message.contains("request id"),
            "{message}"
        );
    }
    for (reply, id) in replies[replies.len() - 2..].iter().zip([20, 21]) {
        assert_eq!(reply["id"], id);
        assert_eq!(reply["ok"], true, "{reply}");
        assert_eq!(reply["result"], expected);
    }
}
