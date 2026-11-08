//! S7 acceptance: real transports, immutable versions, paging and fresh-process parity.
#[path = "common/corpus.rs"]
mod corpus;
#[path = "common/session_edits.rs"]
mod session_edits;
use draftwright::{Session, api::Features, mcp, model::Spec, stateless::Cache};
use serde_json::{Value, json};
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};

fn scratch(name: &str) -> Cache {
    let root =
        Path::new(env!("CARGO_TARGET_TMPDIR")).join(format!("s7-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&root);
    Cache::new(root)
}
fn parts() -> Vec<PathBuf> {
    let mut parts = vec![
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/mounting_plate.step"),
    ];
    if let Some(c) = corpus::dir() {
        parts.extend(
            [
                "mfcadpp/10127.step",
                "gramel/GRM-03_thumbwheel_drive_screw.step",
                "nist/nist_ctc_01_asme1_rd.stp",
            ]
            .map(|f| c.join(f)),
        );
    }
    parts
}
fn bytes(v: &impl serde::Serialize) -> Vec<u8> {
    serde_json::to_vec(v).unwrap()
}
fn checked(cache: &Cache, name: &str, args: Value) -> Value {
    mcp::validate(&mcp::schema(name, false).unwrap(), &args).unwrap();
    let out = mcp::call(cache, name, &args).unwrap_or_else(|e| panic!("{name}: {e}"));
    mcp::validate(&mcp::schema(name, true).unwrap(), &out)
        .unwrap_or_else(|e| panic!("{name}: {e}"));
    out
}
fn fresh(cache: &Cache, args: Value) -> Value {
    let mut child = Command::new(env!("CARGO_BIN_EXE_draftwright"))
        .arg("serve")
        .env("DRAFTWRIGHT_CACHE", &cache.root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    writeln!(child.stdin.take().unwrap(), "{args}").unwrap();
    let result = child.wait_with_output().unwrap();
    assert!(result.status.success());
    let reply: Value = serde_json::from_slice(&result.stdout).unwrap();
    assert_eq!(reply["ok"], true, "{reply}");
    reply["result"].clone()
}
struct Http {
    child: Child,
    address: String,
    max_default_wire: std::cell::Cell<usize>,
}
impl Drop for Http {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}
impl Http {
    fn new(cache: &Cache) -> Self {
        let mut child = Command::new(env!("CARGO_BIN_EXE_draftwright"))
            .args(["mcp-http", "127.0.0.1:0"])
            .env("DRAFTWRIGHT_CACHE", &cache.root)
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        let mut line = String::new();
        BufReader::new(child.stderr.take().unwrap())
            .read_line(&mut line)
            .unwrap();
        let address = line
            .trim()
            .strip_prefix("draftwright MCP listening on http://")
            .unwrap()
            .trim_end_matches("/mcp")
            .to_string();
        Self {
            child,
            address,
            max_default_wire: std::cell::Cell::new(0),
        }
    }
    fn request(&self, method: &str, body: &Value, headers: &str) -> (u16, Vec<u8>) {
        let body = bytes(body);
        let mut stream = std::net::TcpStream::connect(&self.address).unwrap();
        stream
            .set_read_timeout(Some(std::time::Duration::from_secs(120)))
            .unwrap();
        write!(stream,"{method} /mcp HTTP/1.1\r\nHost: {}\r\nAccept: application/json, text/event-stream\r\nContent-Type: application/json\r\nContent-Length: {}\r\n{headers}\r\n",self.address,body.len()).unwrap();
        stream.write_all(&body).unwrap();
        let mut out = Vec::new();
        stream.read_to_end(&mut out).unwrap();
        let split = out.windows(4).position(|s| s == b"\r\n\r\n").unwrap();
        let status = std::str::from_utf8(&out[..split])
            .unwrap()
            .split_whitespace()
            .nth(1)
            .unwrap()
            .parse()
            .unwrap();
        (status, out[split + 4..].to_vec())
    }
    fn rpc(&self, method: &str, params: Value) -> Value {
        let (status, body) = self.request(
            "POST",
            &json!({"jsonrpc":"2.0","id":1,"method":method,"params":params}),
            "MCP-Protocol-Version: 2025-06-18\r\n",
        );
        assert_eq!(status, 200);
        let response: Value = serde_json::from_slice(&body).unwrap();
        if method == "tools/call"
            && [
                "inspect_part",
                "list_items",
                "list_problems",
                "preview_ops",
                "apply_ops",
            ]
            .contains(&params["name"].as_str().unwrap_or(""))
            && params["arguments"]["detail"] != true
        {
            assert!(
                body.len() < 4096,
                "{} default wire response is {} bytes",
                params["name"],
                body.len()
            );
            self.max_default_wire
                .set(self.max_default_wire.get().max(body.len()));
        }
        response["result"].clone()
    }
    fn tool(&self, name: &str, args: Value) -> Value {
        let result = self.rpc("tools/call", json!({"name":name,"arguments":args}));
        assert_eq!(result["isError"], false, "{result}");
        result["structuredContent"].clone()
    }
    fn artifact(&self, key: &str) -> Value {
        let result = self.rpc(
            "resources/read",
            json!({"uri":format!("draftwright:{key}")}),
        );
        serde_json::from_str(result["contents"][0]["text"].as_str().unwrap()).unwrap()
    }
}
const HTTP_HEAD_LIMIT: usize = 16 * 1024;
const PING: &[u8] = br#"{"jsonrpc":"2.0","id":1,"method":"ping"}"#;

fn http_wire(body: &[u8]) -> Vec<u8> {
    let mut wire = format!(
        "POST /mcp HTTP/1.1\r\nContent-Type: application/json\r\nAccept: application/json, text/event-stream\r\nContent-Length: {}\r\n\r\n",
        body.len()
    ).into_bytes();
    wire.extend_from_slice(body);
    wire
}
fn http_socket(http: &Http, timeout: u64) -> std::net::TcpStream {
    let stream = std::net::TcpStream::connect(&http.address).unwrap();
    stream
        .set_read_timeout(Some(std::time::Duration::from_secs(timeout)))
        .unwrap();
    stream
        .set_write_timeout(Some(std::time::Duration::from_secs(2)))
        .unwrap();
    stream
}
fn http_response(stream: &mut std::net::TcpStream) -> (u16, Vec<u8>) {
    let mut wire = Vec::new();
    // A reset, truncated response or missing EOF must fail on Linux as well as macOS.
    stream.read_to_end(&mut wire).unwrap();
    let split = wire.windows(4).position(|w| w == b"\r\n\r\n").unwrap();
    let head = std::str::from_utf8(&wire[..split]).unwrap();
    let status = head.split_whitespace().nth(1).unwrap().parse().unwrap();
    let length: usize = head
        .lines()
        .find_map(|line| line.strip_prefix("Content-Length: "))
        .unwrap()
        .parse()
        .unwrap();
    let body = wire[split + 4..].to_vec();
    assert_eq!(body.len(), length);
    (status, body)
}
fn queued_ping(http: &Http) -> std::net::TcpStream {
    let mut stream = http_socket(http, 2);
    stream.write_all(&http_wire(PING)).unwrap();
    stream
}
fn assert_ping(stream: &mut std::net::TcpStream) {
    let (status, body) = http_response(stream);
    assert_eq!(status, 200);
    assert_eq!(
        serde_json::from_slice::<Value>(&body).unwrap(),
        json!({"jsonrpc":"2.0","id":1,"result":{}})
    );
}
#[test]
fn http_rejects_oversized_heads_and_serves_queued_pings() {
    let http = Http::new(&scratch("http-head-limits"));
    let mut aggregate = "POST /mcp HTTP/1.1\r\n".to_owned() + &"X-Small: x\r\n".repeat(1000);
    aggregate.push_str("X-Last: ");
    aggregate.push_str(&"x".repeat(HTTP_HEAD_LIMIT + 1 - aggregate.len()));
    let cases = [
        (
            "request line",
            format!("POST /{} HTTP/1.1\r\n", "x".repeat(HTTP_HEAD_LIMIT)).into_bytes(),
        ),
        (
            "header",
            format!(
                "POST /mcp HTTP/1.1\r\nX-Large: {}",
                "x".repeat(HTTP_HEAD_LIMIT)
            )
            .into_bytes(),
        ),
        ("aggregate", aggregate.into_bytes()),
        ("no newline", vec![b'x'; HTTP_HEAD_LIMIT]),
        ("unread input", vec![b'x'; 128 * 1024]),
    ];
    for (name, wire) in cases {
        let mut hostile = http_socket(&http, 2);
        let start = std::time::Instant::now();
        hostile.write_all(&wire).unwrap();
        let mut following = queued_ping(&http);
        assert_eq!(http_response(&mut hostile).0, 431, "{name}");
        assert!(
            start.elapsed() < std::time::Duration::from_millis(750),
            "{name}"
        );
        assert_ping(&mut following);
    }
}
#[test]
fn http_rejects_bare_lf_heads_promptly() {
    let http = Http::new(&scratch("http-bare-lf"));
    let mut hostile = http_socket(&http, 2);
    let start = std::time::Instant::now();
    hostile
        .write_all(
            &http_wire(b"{}")
                .into_iter()
                .filter(|b| *b != b'\r')
                .collect::<Vec<_>>(),
        )
        .unwrap();
    let mut following = queued_ping(&http);
    assert_eq!(http_response(&mut hostile).0, 400);
    assert!(start.elapsed() < std::time::Duration::from_millis(750));
    assert_ping(&mut following);
}
fn http_slow_request(body: bool) {
    use std::sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    };
    use std::time::{Duration, Instant};
    let http = Http::new(&scratch(if body {
        "http-slow-body"
    } else {
        "http-slow-head"
    }));
    let mut hostile = http_socket(&http, 12);
    let start = Instant::now();
    hostile
        .write_all(b"POST /mcp HTTP/1.1\r\nX-Slow: ")
        .unwrap();
    let mut following = queued_ping(&http);
    let stop = Arc::new(AtomicBool::new(false));
    let stopped = stop.clone();
    let mut sender = hostile.try_clone().unwrap();
    let writer = std::thread::spawn(move || {
        // The body starts six seconds into the head deadline: it must not get a new one.
        let mut head_finished = false;
        while !stopped.load(Ordering::Relaxed) && start.elapsed() < Duration::from_secs(12) {
            let data = if body && !head_finished && start.elapsed() >= Duration::from_secs(6) {
                head_finished = true;
                b"\r\nContent-Type: application/json\r\nAccept: application/json, text/event-stream\r\nContent-Length: 1024\r\n\r\n".as_slice()
            } else {
                b"x".as_slice()
            };
            if sender.write_all(data).is_err() {
                break;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
    });
    let response = http_response(&mut hostile);
    let elapsed = start.elapsed();
    stop.store(true, Ordering::Relaxed);
    writer.join().unwrap();
    assert_eq!(response.0, 408);
    assert!(
        (Duration::from_secs(9)..Duration::from_secs(12)).contains(&elapsed),
        "{elapsed:?}"
    );
    assert_ping(&mut following);
}
#[test]
fn http_slow_head_has_absolute_deadline_and_serves_queued_ping() {
    http_slow_request(false);
}
#[test]
fn http_slow_body_shares_head_deadline_and_serves_queued_ping() {
    http_slow_request(true);
}
#[test]
fn http_preserves_exact_byte_limits_and_transport_guards() {
    let http = Http::new(&scratch("http-boundaries"));
    let base = http_wire(PING);
    let split = base.windows(4).position(|w| w == b"\r\n\r\n").unwrap();
    for (total, expected) in [(HTTP_HEAD_LIMIT, 200), (HTTP_HEAD_LIMIT + 1, 431)] {
        let mut wire = base[..split + 2].to_vec();
        wire.extend_from_slice(b"X-Pad: ");
        let padding = total - wire.len() - 4;
        wire.extend("é".repeat(padding / 2).as_bytes());
        wire.extend(std::iter::repeat_n(b'x', padding % 2));
        wire.extend_from_slice(b"\r\n\r\n");
        assert_eq!(wire.len(), total);
        wire.extend_from_slice(PING);
        let mut stream = http_socket(&http, 2);
        stream.write_all(&wire[..total - 1]).unwrap();
        stream.write_all(&wire[total - 1..]).unwrap();
        assert_eq!(http_response(&mut stream).0, expected);
    }
    let mut body = PING.to_vec();
    body.resize(8 * 1024 * 1024, b' ');
    let mut stream = http_socket(&http, 2);
    stream.write_all(&http_wire(&body)).unwrap();
    assert_ping(&mut stream);
    assert_eq!(
        http.request("POST", &json!({}), "Content-Length: 8388609\r\n")
            .0,
        413
    );
    assert_eq!(
        http.request("POST", &json!({}), "Transfer-Encoding: chunked\r\n")
            .0,
        400
    );
    assert_eq!(
        http.request(
            "POST",
            &json!({}),
            &format!("Origin: http://{}\r\n", http.address)
        )
        .0,
        200
    );
    assert_eq!(
        http.request("POST", &json!({}), "Origin: https://malicious.example\r\n")
            .0,
        403
    );
    let refused = Command::new(env!("CARGO_BIN_EXE_draftwright"))
        .args(["mcp-http", "0.0.0.0:0"])
        .output()
        .unwrap();
    assert!(!refused.status.success());
    assert!(
        String::from_utf8(refused.stderr)
            .unwrap()
            .contains("only loopback")
    );
}
#[test]
fn every_tool_input_output_and_refusal_match_published_schemas() {
    let cache = scratch("schemas");
    let step = &parts()[0];
    let summary = checked(&cache, "inspect_part", json!({"step":step,"detail":true}));
    let key = summary["part_ref"].as_str().unwrap();
    let state = checked(&cache, "get_spec", json!({"part_ref":key}));
    let spec = state["spec"].clone();
    let id = cache
        .session(key, &serde_json::from_value(spec.clone()).unwrap())
        .unwrap()
        .plan()
        .dimensions[0]
        .id
        .clone();
    let ops = json!([{"op":"set","path":"sheet/title/title","value":"S7"}]);
    let requests = [
        ("analyse", json!({"step":step})),
        ("list_items", json!({"part_ref":key,"spec":spec})),
        ("get_item", json!({"part_ref":key,"spec":spec,"id":id})),
        ("list_problems", json!({"part_ref":key,"spec":spec})),
        ("item_options", json!({"part_ref":key,"spec":spec,"id":id})),
        ("preview_ops", json!({"part_ref":key,"spec":spec,"ops":ops})),
        (
            "apply_ops",
            json!({"part_ref":key,"parent":state["spec_version"],"ops":ops}),
        ),
        (
            "view_sheet",
            json!({"part_ref":key,"spec":spec,"region":[10,10,100,100],"overlay_ids":true,"dpi":30}),
        ),
        ("render_pdf", json!({"part_ref":key,"spec":spec})),
        ("render", json!({"step":step,"format":"pdf"})),
    ];
    for (name, args) in requests {
        checked(&cache, name, args);
    }
    for name in mcp::TOOLS {
        assert!(
            mcp::validate(&mcp::schema(name, true).unwrap(), &json!({})).is_err(),
            "{name}"
        );
        let args = json!({"unknown":true});
        assert!(mcp::validate(&mcp::schema(name, false).unwrap(), &args).is_err());
        let error = mcp::call(&cache, name, &args).unwrap_err();
        mcp::validate(&mcp::schema(name, true).unwrap(), &json!({"error":error})).unwrap();
        let path =
            Path::new(env!("CARGO_MANIFEST_DIR")).join(format!("schemas/mcp/{name}.input.json"));
        assert_eq!(
            mcp::schema(name, false).unwrap(),
            serde_json::from_slice::<Value>(&std::fs::read(path).unwrap()).unwrap()
        );
    }
    assert!(mcp::call(&cache, "view_sheet", &json!({"part_ref":key,"dpi":0})).is_err());
    assert!(
        mcp::call(
            &cache,
            "preview_ops",
            &json!({"part_ref":key,"ops":[{"op":"set","path":"sheet/size"}]})
        )
        .is_err()
    );
}
#[test]
fn four_slice_parts_default_payloads_are_bounded_and_paging_is_lossless() {
    let cache = scratch("budgets");
    for step in parts() {
        let summary = checked(&cache, "inspect_part", json!({"step":step}));
        assert!(bytes(&summary).len() < 4096);
        let key = summary["part_ref"].as_str().unwrap();
        let session = cache.session(key, &Spec::default()).unwrap();
        let mut cursor = 0;
        let mut all = Vec::new();
        let mut max_page = 0;
        loop {
            let page = checked(
                &cache,
                "list_items",
                json!({"part_ref":key,"cursor":cursor,"limit":100}),
            );
            max_page = max_page.max(bytes(&page).len());
            assert!(bytes(&page).len() < 4096);
            all.extend(
                page["items"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|i| i["id"].as_str().unwrap().to_string()),
            );
            if let Some(next) = page["next_cursor"].as_u64() {
                assert!(next > cursor);
                cursor = next;
            } else {
                break;
            }
        }
        assert_eq!(
            all,
            session
                .items(&Default::default())
                .iter()
                .map(|i| i.item.id.clone())
                .collect::<Vec<_>>()
        );
        let problems = checked(&cache, "list_problems", json!({"part_ref":key}));
        assert!(bytes(&problems).len() < 4096);
        let mut problem_page = problems.clone();
        let mut problem_ids = Vec::new();
        loop {
            assert!(bytes(&problem_page).len() < 4096);
            problem_ids.extend(
                problem_page["items"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|p| p["id"].as_str().unwrap().to_string()),
            );
            let Some(cursor) = problem_page["next_cursor"].as_u64() else {
                break;
            };
            problem_page = checked(
                &cache,
                "list_problems",
                json!({"part_ref":key,"cursor":cursor}),
            );
        }
        assert_eq!(
            problem_ids,
            session
                .problems(&Default::default())
                .iter()
                .map(|p| p.id.clone())
                .collect::<Vec<_>>()
        );
        let mut max_delta = 0;
        for ops in session_edits::edits(&session) {
            let d = checked(
                &cache,
                "preview_ops",
                json!({"part_ref":key,"spec":session.spec(),"ops":ops}),
            );
            max_delta = max_delta.max(bytes(&d).len());
            assert!(bytes(&d).len() < 4096);
        }
        eprintln!(
            "MCP_BUDGET {} summary={} list_max={} problems={} delta_max={}",
            step.display(),
            bytes(&summary).len(),
            max_page,
            bytes(&problems).len(),
            max_delta
        );
    }
    let rows = (0..200)
        .map(|i| json!({"id":format!("i:{i}"),"label":"x".repeat(10000)}))
        .collect();
    let p = mcp::page(&cache, rows, &json!({})).unwrap();
    assert!(bytes(&p).len() < 4096);
    assert!(p["next_cursor"].is_number());
    assert!(p["items"][0]["detail_ref"].is_string());
}
#[test]
fn warm_cache_and_fresh_process_are_byte_identical_to_session() {
    let cache = scratch("fresh");
    for step in parts() {
        let key = cache.register(&step, None).unwrap();
        let mut s = Session::open(&step, Features::Native, Spec::default()).unwrap();
        cache.session(&key, s.spec()).unwrap();
        let warm = cache.session(&key, s.spec()).unwrap();
        assert_eq!(warm.stats().projections, 0);
        assert_eq!(warm.stats().bounds, 0);
        assert_eq!(bytes(warm.plan()), bytes(s.plan()));
        let chosen = s
            .plan()
            .dimensions
            .iter()
            .find(|d| d.kind == "linear" && d.placed.is_some())
            .unwrap();
        let dimension = &chosen.id;
        let fetched = fresh(
            &cache,
            json!({"op":"mcp_get_item","arguments":{"part_ref":key,"spec":s.spec(),"id":dimension,"detail":true}}),
        );
        assert_eq!(
            bytes(&fetched["item"]),
            bytes(&serde_json::to_value(s.item(dimension).unwrap()).unwrap())
        );
        let fetched = fresh(
            &cache,
            json!({"op":"mcp_item_options","arguments":{"part_ref":key,"spec":s.spec(),"id":dimension}}),
        );
        assert_eq!(
            bytes(&fetched),
            bytes(&serde_json::to_value(s.options(dimension).unwrap()).unwrap())
        );
        let options = s.options(dimension).unwrap();
        assert!(options.placements.iter().all(|p| matches!(
            p.side,
            Some(
                draftwright::placement_intent::Side::Left
                    | draftwright::placement_intent::Side::Right
            )
        ) == (chosen.placed.as_ref().unwrap().rotation
            == 90.0)));
        let filter = draftwright::session::ProblemFilter {
            subject: Some(dimension.clone()),
            ..Default::default()
        };
        let fetched = fresh(
            &cache,
            json!({"op":"mcp_list_problems","arguments":{"part_ref":key,"spec":s.spec(),"filter":filter,"detail":true}}),
        );
        assert_eq!(
            fetched["items"],
            serde_json::to_value(s.problems(&filter)).unwrap()
        );

        assert_eq!(warm.drawing().svg(), s.drawing().svg());
        assert_eq!(
            bytes(&cache.items(&key, s.spec(), &Default::default()).unwrap()),
            bytes(&s.items(&Default::default()))
        );
        let ops = &session_edits::edits(&s)[5];
        let before = s.spec().clone();
        let expected = s.apply(ops, true).unwrap();
        let direct = cache.apply(&key, &before, ops).unwrap();
        assert_eq!(bytes(&direct.delta), bytes(&expected));
        assert_eq!(bytes(&direct.spec), bytes(s.spec()));
        let response = fresh(
            &cache,
            json!({"op":"stateless_apply","part_ref":key,"spec":before,"ops":ops}),
        );
        assert_eq!(
            bytes(&response["delta"]),
            bytes(&serde_json::to_value(&expected).unwrap())
        );
        assert_eq!(response["spec"], serde_json::to_value(s.spec()).unwrap());
        let preview = fresh(
            &cache,
            json!({"op":"stateless_preview","part_ref":key,"spec":before,"ops":ops}),
        );
        assert_eq!(preview, response["delta"]);
    }
}
#[test]
fn s0_edits_through_http_serve_and_session_have_identical_specs_plans_and_deltas() {
    let cache = scratch("parity");
    let http = Http::new(&cache);
    for step in parts() {
        http.max_default_wire.set(0);
        let registered = http.tool("inspect_part", json!({"step":step}));
        let key = registered["part_ref"].as_str().unwrap();
        let mut current = http.tool("get_spec", json!({"part_ref":key}));
        http.tool("list_items", json!({"part_ref":key,"limit":100}));
        http.tool("list_problems", json!({"part_ref":key,"limit":100}));
        let mut s = Session::open(&step, Features::Native, Spec::default()).unwrap();
        // A real, live serve Session independently follows the complete S0 sequence.
        let mut serve = Command::new(env!("CARGO_BIN_EXE_draftwright"))
            .arg("serve")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .spawn()
            .unwrap();
        let mut input = serve.stdin.take().unwrap();
        let mut output = BufReader::new(serve.stdout.take().unwrap());
        let mut exchange = |req: Value| {
            writeln!(input, "{req}").unwrap();
            input.flush().unwrap();
            let mut line = String::new();
            output.read_line(&mut line).unwrap();
            let r: Value = serde_json::from_str(&line).unwrap();
            assert_eq!(r["ok"], true, "{r}");
            r["result"].clone()
        };
        exchange(json!({"op":"open","step":step,"features":"native"}));
        for ops in session_edits::edits(&s) {
            let old = s.spec().clone();
            let expected = s.apply(&ops, true).unwrap();
            let served = exchange(json!({"op":"apply","ops":ops}));
            assert_eq!(
                bytes(&served),
                bytes(&serde_json::to_value(&expected).unwrap())
            );
            let applied = http.tool(
                "apply_ops",
                json!({"part_ref":key,"parent":current["spec_version"],"ops":ops}),
            );
            assert!(bytes(&applied).len() < 4096);
            let delta = http.artifact(applied["delta"]["delta_ref"].as_str().unwrap());
            assert_eq!(
                bytes(&delta),
                bytes(&serde_json::to_value(&expected).unwrap())
            );
            let mut delta_page = applied["delta"].clone();
            let mut events = Vec::new();
            loop {
                events.extend(
                    delta_page["events"]["items"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .cloned(),
                );
                let Some(cursor) = delta_page["events"]["next_cursor"].as_u64() else {
                    break;
                };
                let read = http.rpc("resources/read", json!({"uri":format!("draftwright:{}",applied["delta"]["delta_ref"].as_str().unwrap()),"events":true,"cursor":cursor}));
                delta_page =
                    serde_json::from_str(read["contents"][0]["text"].as_str().unwrap()).unwrap();
                assert!(bytes(&delta_page).len() < 4096);
            }
            assert_eq!(
                events.len(),
                expected.changed.len()
                    + expected.added.len()
                    + expected.removed.len()
                    + expected.problems_added.len()
                    + expected.problems_resolved.len()
                    + expected.notices.len()
            );
            assert_eq!(
                http.tool("get_spec", json!({"part_ref":key}))["spec_version"],
                applied["spec_version"]
            );
            current = http.tool(
                "get_spec",
                json!({"part_ref":key,"spec_version":applied["spec_version"]}),
            );
            assert_eq!(
                bytes(&current["spec"]),
                bytes(&serde_json::to_value(s.spec()).unwrap())
            );
            let inspection = http.tool(
                "inspect_part",
                json!({"part_ref":key,"spec_version":current["spec_version"],"detail":true}),
            );
            let plan = http.artifact(inspection["plan_ref"].as_str().unwrap());
            assert_eq!(
                bytes(&plan),
                bytes(&serde_json::to_value(s.plan()).unwrap())
            );
            assert_eq!(
                http.tool("preview_ops", json!({"part_ref":key,"spec":old,"ops":ops}))["delta_ref"],
                applied["delta"]["delta_ref"]
            );
        }
        eprintln!(
            "MCP_WIRE {} max_default={}",
            step.display(),
            http.max_default_wire.get()
        );
        drop(exchange);
        drop(input);
        assert!(serve.wait().unwrap().success());
    }
}
#[test]
fn unknown_keys_ids_stale_heads_and_concurrent_writers_are_refused_with_corrections() {
    let cache = scratch("refusals");
    let step = &parts()[0];
    assert!(
        cache
            .summary("missing", &Spec::default())
            .unwrap_err()
            .message
            .contains("inspect_part")
    );
    let key = cache.register(step, None).unwrap();
    let original = cache.head(&key, "drawing").unwrap();
    let error = cache
        .item(&key, &Spec::default(), "d:envelope/width.lengt")
        .unwrap_err();
    assert!(error.choices.contains(&"d:envelope/width.length".into()));
    let mut threads = Vec::new();
    for title in ["A", "B"] {
        let (cache, key, parent) = (cache.clone(), key.clone(), original.clone());
        threads.push(std::thread::spawn(move || {
            cache.apply_version(
                &key,
                "drawing",
                &parent,
                &[draftwright::items::Op::set(
                    "sheet/title/title",
                    json!(title),
                )],
            )
        }));
    }
    let results: Vec<_> = threads.into_iter().map(|t| t.join().unwrap()).collect();
    assert_eq!(results.iter().filter(|r| r.is_ok()).count(), 1);
    let refused = results.iter().find_map(|r| r.as_ref().err()).unwrap();
    assert!(refused.message.contains("current head"));
    assert_eq!(refused.choices, [cache.head(&key, "drawing").unwrap()]);
    let held = cache.head(&key, "drawing").unwrap();
    assert!(
        cache
            .apply_version(
                &key,
                "drawing",
                &held,
                &[draftwright::items::Op::set("sheet/size", json!("INVALID"))]
            )
            .is_err()
    );
    assert_eq!(cache.head(&key, "drawing").unwrap(), held);
    let parent = cache.head(&key, "drawing").unwrap();
    let before = cache.get_version(&parent).unwrap();
    let (noop, applied) = cache.apply_version(&key, "drawing", &parent, &[]).unwrap();
    assert_ne!(noop, parent);
    assert_eq!(bytes(&before), bytes(&applied.spec));
    assert_eq!(bytes(&cache.get_version(&parent).unwrap()), bytes(&before));
    assert_eq!(bytes(&cache.get_version(&noop).unwrap()), bytes(&before));
    assert!(cache.head(&key, "../outside").is_err());
}
#[test]
fn streamable_http_and_stdio_publish_the_same_tools_and_obey_transport_rules() {
    let cache = scratch("transports");
    let http = Http::new(&cache);
    let tools = http.rpc("tools/list", json!({}));
    let mut child = Command::new(env!("CARGO_BIN_EXE_draftwright"))
        .arg("mcp-stdio")
        .env("DRAFTWRIGHT_CACHE", &cache.root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let mut input = child.stdin.take().unwrap();
    writeln!(
        input,
        "{}",
        json!({"jsonrpc":"2.0","method":"notifications/initialized"})
    )
    .unwrap();
    writeln!(
        input,
        "{}",
        json!({"jsonrpc":"2.0","id":1,"method":"tools/list"})
    )
    .unwrap();
    drop(input);
    let out = child.wait_with_output().unwrap();
    assert!(out.status.success());
    assert_eq!(
        serde_json::from_slice::<Value>(&out.stdout).unwrap()["result"],
        tools
    );
    assert_eq!(http.request("GET", &json!({}), "").0, 405);
    assert_eq!(
        http.request(
            "POST",
            &json!({"jsonrpc":"2.0","method":"notifications/initialized"}),
            ""
        )
        .0,
        202
    );
    assert_eq!(
        http.request("POST", &json!({}), "Origin: https://malicious.example\r\n")
            .0,
        403
    );
    assert_eq!(
        http.request("POST", &json!({}), "MCP-Protocol-Version: invalid\r\n")
            .0,
        400
    );
    for t in tools["tools"].as_array().unwrap() {
        assert_eq!(t["outputSchema"]["type"], "object");
        assert_eq!(
            t["outputSchema"],
            mcp::schema(t["name"].as_str().unwrap(), true).unwrap()
        );
    }
}
#[test]
fn regional_png_id_overlay_and_pdf_reuse_renderers_and_embed_spec() {
    let cache = scratch("render");
    let key = cache.register(&parts()[0], None).unwrap();
    let s = cache.session(&key, &Spec::default()).unwrap();
    let full = cache
        .render(&key, s.spec(), None, "png", false, 30.0)
        .unwrap();
    assert_eq!(full, s.drawing().png(30.0).unwrap());
    let crop = cache
        .render(
            &key,
            s.spec(),
            Some([10.0, 10.0, 100.0, 100.0]),
            "png",
            false,
            30.0,
        )
        .unwrap();
    let decoder = png::Decoder::new(std::io::Cursor::new(&crop));
    let reader = decoder.read_info().unwrap();
    assert_eq!(
        [reader.info().width as u64, reader.info().height as u64],
        draftwright::png::size([90.0, 90.0], 30.0)
    );
    let overlay = cache
        .render(&key, s.spec(), None, "png", true, 30.0)
        .unwrap();
    assert_ne!(overlay, full);
    for b in [
        full,
        crop,
        overlay,
        cache
            .render(&key, s.spec(), None, "pdf", false, 30.0)
            .unwrap(),
    ] {
        assert_eq!(
            bytes(&draftwright::model::embedded_spec(&b).unwrap().unwrap()),
            bytes(s.spec())
        );
    }
    assert!(
        cache
            .render(
                &key,
                s.spec(),
                Some([1.0, 1.0, 0.0, 0.0]),
                "png",
                false,
                30.0
            )
            .is_err()
    );
}

#[test]
fn content_keys_survive_paths_and_cold_cache_rebuilds_refuse_corruption() {
    let cache = scratch("cache-bindings");
    let step = parts()[0].clone();
    let key = cache.register(&step, None).unwrap();
    let cold = cache.session(&key, &Spec::default()).unwrap();
    assert!(cold.stats().projections > 0);
    assert!(cold.stats().bounds > 0);
    let dir = cache.root.join("parts").join(&key);
    let stored = std::fs::read(dir.join("projections.json")).unwrap();
    let projections = draftwright::model::Projections::from_disk(&stored).unwrap();
    assert_eq!(projections.disk_bytes().unwrap(), stored);
    let alias = cache.root.join("alias.step");
    std::fs::write(&alias, std::fs::read(&step).unwrap()).unwrap();
    assert_eq!(cache.register(&alias, None).unwrap(), key);
    assert_eq!(
        bytes(cache.session(&key, &Spec::default()).unwrap().plan()),
        bytes(cold.plan())
    );
    std::fs::remove_file(dir.join("analysis.json")).unwrap();
    std::fs::remove_file(dir.join("projections.json")).unwrap();
    assert_eq!(
        bytes(cache.session(&key, &Spec::default()).unwrap().plan()),
        bytes(cold.plan())
    );
    std::fs::write(dir.join("source.step"), b"corrupted STEP").unwrap();
    assert!(
        cache
            .session(&key, &Spec::default())
            .err()
            .unwrap()
            .message
            .contains("cached STEP differs")
    );
}

#[test]
fn a_failed_history_write_keeps_the_parent_live_and_can_be_retried() {
    let cache = scratch("history-write-failure");
    let key = cache.register(&parts()[0], None).unwrap();
    let parent = cache.head(&key, "drawing").unwrap();
    let artifacts = cache.root.join("artifacts");
    assert!(!artifacts.exists());
    std::fs::write(&artifacts, b"not a directory").unwrap();
    let ops = [draftwright::items::Op::set(
        "sheet/title/material",
        json!("Steel"),
    )];
    assert!(cache.apply_version(&key, "drawing", &parent, &ops).is_err());
    assert_eq!(cache.head(&key, "drawing").unwrap(), parent);
    std::fs::remove_file(&artifacts).unwrap();
    let (version, applied) = cache.apply_version(&key, "drawing", &parent, &ops).unwrap();
    assert_eq!(cache.head(&key, "drawing").unwrap(), version);
    assert_eq!(
        bytes(&cache.get_version(&version).unwrap()),
        bytes(&applied.spec)
    );
    assert_eq!(applied.spec.sheet.title["material"], "Steel");
}
