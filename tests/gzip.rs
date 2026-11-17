//! Gzipped STEP input (maintainer decision 2026-10-15): gzip is told by its magic bytes and
//! decompressed in memory, so a `.step` and its `.step.gz` read, bind and draw identically.

use std::path::{Path, PathBuf};
use std::process::Command;

use draftwright::api::{self, Features};
use draftwright::model;
use sha2::{Digest, Sha256};

fn fixture(path: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(path)
}

fn gzip(bytes: &[u8]) -> Vec<u8> {
    let mut encoder = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::fast());
    std::io::Write::write_all(&mut encoder, bytes).unwrap();
    encoder.finish().unwrap()
}

fn gunzip(bytes: &[u8]) -> Vec<u8> {
    let mut text = Vec::new();
    std::io::Read::read_to_end(&mut flate2::read::GzDecoder::new(bytes), &mut text).unwrap();
    text
}

/// A fresh scratch directory with `plain/<name>` holding *text* and `gz/<name>.gz` its gzip.
fn scratch(test: &str, name: &str, text: &[u8]) -> (PathBuf, PathBuf, PathBuf) {
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR")).join(format!("gzip-{test}"));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(dir.join("plain")).unwrap();
    std::fs::create_dir_all(dir.join("gz")).unwrap();
    let plain = dir.join("plain").join(name);
    let gz = dir.join("gz").join(format!("{name}.gz"));
    std::fs::write(&plain, text).unwrap();
    std::fs::write(&gz, gzip(text)).unwrap();
    (dir, plain, gz)
}

/// The command line's exit code, stdout and stderr.
fn run(args: &[&str]) -> (Option<i32>, String, String) {
    let out = Command::new(env!("CARGO_BIN_EXE_draftwright"))
        .args(args)
        .output()
        .unwrap();
    (
        out.status.code(),
        String::from_utf8(out.stdout).unwrap(),
        String::from_utf8_lossy(&out.stderr).into_owned(),
    )
}

fn s(p: &Path) -> &str {
    p.to_str().unwrap()
}

/// Each command that reads a STEP file prints byte-identical output for the gzipped file, and
/// render writes byte-identical files.
#[test]
fn every_command_reads_a_gzipped_file_as_the_plain_one() {
    let text = std::fs::read(fixture("parts/plate.step")).unwrap();
    let (dir, plain, gz) = scratch("cli", "plate.step", &text);
    for command in ["analyse", "plan", "review", "parts"] {
        let a = run(&[command, s(&plain)]);
        assert_eq!(a.0, Some(0), "{command}: {}", a.2);
        assert_eq!(run(&[command, s(&gz)]), a, "{command}");
    }
    // Planned from the plain file's Analysis: the gzipped file is bound to it.
    let analysis = dir.join("analysis.json");
    std::fs::write(&analysis, run(&["analyse", s(&plain)]).1).unwrap();
    let a = run(&["plan", s(&plain), "--analysis", s(&analysis)]);
    assert_eq!(a.0, Some(0), "{}", a.2);
    assert_eq!(run(&["plan", s(&gz), "--analysis", s(&analysis)]), a);
    // render: the same files (the printed paths name each directory).
    let (out_a, out_b) = (dir.join("plain/out"), dir.join("gz/out"));
    let a = run(&[
        "render",
        s(&plain),
        "-o",
        s(&out_a),
        "--formats",
        "svg,pdf,png",
    ]);
    let b = run(&[
        "render",
        s(&gz),
        "-o",
        s(&out_b),
        "--formats",
        "svg,pdf,png",
    ]);
    assert_eq!(a.0, Some(0), "{}", a.2);
    assert_eq!(b.1.replace("/gz/", "/plain/"), a.1);
    for ext in ["svg", "pdf", "png"] {
        assert_eq!(
            std::fs::read(out_a.with_extension(ext)).unwrap(),
            std::fs::read(out_b.with_extension(ext)).unwrap(),
            "{ext}"
        );
    }
    let _ = std::fs::remove_dir_all(&dir);
}

/// serve answers a request naming a gzipped file as it answers the plain one.
#[test]
fn serve_reads_a_gzipped_file_as_the_plain_one() {
    let text = std::fs::read(fixture("parts/plate.step")).unwrap();
    let (dir, plain, gz) = scratch("serve", "plate.step", &text);
    let mut requests = String::new();
    for step in [&plain, &gz] {
        for op in ["parts", "analyse", "plan"] {
            requests += &format!("{}\n", serde_json::json!({"id": 1, "op": op, "step": step}));
        }
    }
    let mut child = Command::new(env!("CARGO_BIN_EXE_draftwright"))
        .arg("serve")
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .spawn()
        .unwrap();
    std::io::Write::write_all(child.stdin.as_mut().unwrap(), requests.as_bytes()).unwrap();
    drop(child.stdin.take());
    let out = String::from_utf8(child.wait_with_output().unwrap().stdout).unwrap();
    let replies: Vec<&str> = out.lines().collect();
    assert_eq!(replies.len(), 6, "{out}");
    for k in 0..3 {
        assert!(replies[k].contains("\"ok\":true"), "{}", replies[k]);
        assert_eq!(replies[k], replies[k + 3]);
    }
    let _ = std::fs::remove_dir_all(&dir);
}

/// The binding's sha256 is the decompressed bytes', from a path or from bytes, and the gzipped
/// file is the same cache registration; a gzipped fixture with PMI reads as its STEP text.
#[test]
fn a_gzipped_file_binds_as_the_plain_one() {
    let text = std::fs::read(fixture("parts/plate.step")).unwrap();
    let (dir, plain, gz) = scratch("binding", "plate.step", &text);
    let sha: String = Sha256::digest(&text)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect();
    let a = api::analyse_from(&plain, Features::Native).unwrap();
    let b = api::analyse_from(&gz, Features::Native).unwrap();
    assert_eq!(a.binding.source_sha256, sha);
    assert_eq!(
        serde_json::to_string(&a).unwrap(),
        serde_json::to_string(&b).unwrap()
    );
    let (_, c) = model::analyse_step(&gz, &std::fs::read(&gz).unwrap()).unwrap();
    assert_eq!(c.binding.source_sha256, sha);
    assert_eq!(c.binding.part, a.binding.part);
    assert_eq!(
        model::parts(&gz, &std::fs::read(&gz).unwrap()).unwrap(),
        api::parts(&plain).unwrap()
    );

    let cache = draftwright::stateless::Cache::new(dir.join("cache"));
    let key = cache.register(&plain, None).unwrap();
    assert_eq!(cache.register(&gz, None).unwrap(), key);
    assert!(key.starts_with(&sha));
    cache.session(&key, &model::Spec::default()).unwrap();

    // A committed gzipped fixture with PMI: the same Analysis (PMI reading included) as its text.
    let gz = fixture("parts/manufacturing_pmi.step.gz");
    let text = gunzip(&std::fs::read(&gz).unwrap());
    let (dir2, plain, _) = scratch("binding-pmi", "manufacturing_pmi.step", &text);
    let a = api::analyse_from(&plain, Features::Native).unwrap();
    let b = api::analyse_from(&gz, Features::Native).unwrap();
    assert!(a.pmi.is_some());
    assert_eq!(
        serde_json::to_string(&a).unwrap(),
        serde_json::to_string(&b).unwrap()
    );
    let _ = std::fs::remove_dir_all(&dir);
    let _ = std::fs::remove_dir_all(&dir2);
}

/// A file that begins as gzip but does not decompress is refused, saying so.
#[test]
fn a_corrupt_gzip_is_refused_with_a_clear_error() {
    let text = std::fs::read(fixture("parts/plate.step")).unwrap();
    let (dir, _, gz) = scratch("corrupt", "plate.step", &text);
    let mut bytes = std::fs::read(&gz).unwrap();
    bytes.truncate(bytes.len() / 2);
    std::fs::write(&gz, &bytes).unwrap();
    let err = api::analyse(&gz, None).unwrap_err();
    assert!(
        err.message
            .starts_with(&format!("cannot read {}", gz.display()))
            && err.message.contains("gzip but cannot be decompressed"),
        "{}",
        err.message
    );
    let err = api::parts(&gz).unwrap_err();
    assert!(
        err.message.contains("gzip but cannot be decompressed"),
        "{}",
        err.message
    );
    let err = model::analyse_step(Path::new("x.step"), &[0x1f, 0x8b, 0, 1, 2]).unwrap_err();
    assert!(
        err.message.contains("gzip but cannot be decompressed"),
        "{err}"
    );
    let (code, out, err) = run(&["analyse", s(&gz)]);
    assert_eq!(code, Some(2), "{err}");
    assert!(out.is_empty(), "{out}");
    assert!(err.starts_with("error: "), "{err}");
    assert!(err.contains("gzip but cannot be decompressed"), "{err}");
    let _ = std::fs::remove_dir_all(&dir);
}
