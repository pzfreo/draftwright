mod build_support;

fn main() {
    let root = std::path::PathBuf::from(std::env::var_os("CARGO_MANIFEST_DIR").unwrap());
    let files = build_support::inputs(&root).expect("engine fingerprint inputs");
    // A dependency checkout need not carry a lockfile; also watch for one being created.
    println!("cargo:rerun-if-changed=Cargo.lock");
    // Directory watches also catch newly added and removed source/assets.
    for directory in ["src", "schemas", "fonts"] {
        println!("cargo:rerun-if-changed={directory}");
    }
    for path in &files {
        println!("cargo:rerun-if-changed={}", path.display());
    }
    println!(
        "cargo:rustc-env=DRAFTWRIGHT_ENGINE_VERSION={}",
        build_support::fingerprint(&root, &files).expect("engine fingerprint")
    );
}
