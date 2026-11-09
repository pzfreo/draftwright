mod build_support;

fn main() {
    let root = std::path::PathBuf::from(std::env::var_os("CARGO_MANIFEST_DIR").unwrap());
    // Read the resolved dependency, so --version cannot drift from the linked quiddity.
    let lock: toml::Value =
        toml::from_str(&std::fs::read_to_string(root.join("Cargo.lock")).expect("Cargo.lock"))
            .expect("valid Cargo.lock");
    let versions: Vec<_> = lock["package"]
        .as_array()
        .expect("lock packages")
        .iter()
        .filter(|package| package["name"].as_str() == Some("quiddity"))
        .map(|package| package["version"].as_str().expect("quiddity version"))
        .collect();
    assert_eq!(versions.len(), 1, "one resolved quiddity version");
    println!(
        "cargo:rustc-env=DRAFTWRIGHT_QUIDDITY_VERSION={}",
        versions[0]
    );
    let files = build_support::inputs(&root).expect("engine fingerprint inputs");
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
