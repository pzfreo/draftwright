//! Assertions for a one-shot CLI refusal (serve and MCP keep their JSON replies).

pub fn failure(args: &[&str], code: i32) -> String {
    let out = std::process::Command::new(env!("CARGO_BIN_EXE_draftwright"))
        .args(args)
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(code), "{args:?}: {out:?}");
    assert!(out.stdout.is_empty(), "{args:?}: {out:?}");
    let stderr = String::from_utf8(out.stderr).unwrap();
    assert!(stderr.starts_with("error: "), "{args:?}: {stderr}");
    stderr
}
