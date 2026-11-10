//! Render never panics: parts whose drawing once stopped the port with a panic, analysed,
//! planned and rendered whole, as the command line does.

use draftwright::api::{self, Features};
use draftwright::model::Spec;
use std::io::Read;
use std::path::{Path, PathBuf};

/// The AP242 fixture *name* (`tests/fixtures/pmi/step/<name>.gz`), unzipped to a STEP file.
fn fixture(name: &str) -> PathBuf {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/pmi/step");
    let mut step = Vec::new();
    flate2::read::GzDecoder::new(std::fs::File::open(root.join(format!("{name}.gz"))).unwrap())
        .read_to_end(&mut step)
        .unwrap();
    let path = Path::new(env!("CARGO_TARGET_TMPDIR")).join(name);
    std::fs::write(&path, step).unwrap();
    path
}

/// nist_stc_10's top view has closed periodic cubics (where the cross holes meet the ø7 holes)
/// whose stretches haecceity unwraps into the curve's second period, past its knots:
/// `output::bezier_pieces` took the span index past the poles and the render panicked ("range
/// end index 28 out of range for slice of length 25"). Each stretch is now wrapped into the
/// domain and drawn, so the part renders in every format with no edge left out.
/// Native DXF is an accepted format and embeds the same resumable Spec.
#[test]
fn dxf_export_is_editable() {
    let (_, files) = api::render_from(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/plate.step"),
        Features::Native,
        &Spec::default(),
        &["dxf"],
        api::DPI,
    )
    .unwrap();
    assert_eq!(files.len(), 1);
    let d = dxf::Drawing::load(&mut std::io::Cursor::new(&files[0].1)).unwrap();
    assert!(
        d.entities()
            .any(|e| matches!(e.specific, dxf::entities::EntityType::RotatedDimension(_)))
    );
    assert!(d.blocks().any(|b| b.name == "TITLE_BLOCK"));
}

#[test]
fn nist_stc_10_renders() {
    let step = fixture("nist_stc_10_asme1_ap242-e2.stp");
    let (plan, files) = api::render_from(
        &step,
        Features::Native,
        &Spec::default(),
        &["svg", "pdf", "png"],
        api::DPI,
    )
    .unwrap();
    let names: Vec<&str> = files.iter().map(|(n, _)| n.as_str()).collect();
    assert_eq!(names.len(), 3, "{names:?}");
    assert!(files.iter().all(|(_, bytes)| !bytes.is_empty()));
    let undrawn: Vec<&str> = plan
        .problems
        .iter()
        .filter(|p| p.code == "edges_not_drawn")
        .map(|p| p.message.as_str())
        .collect();
    assert!(undrawn.is_empty(), "{undrawn:?}");
}
