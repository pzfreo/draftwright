use std::sync::OnceLock;

/// Report the linked quiddity's own package metadata, without consulting a lockfile.
pub fn version() -> &'static str {
    static VERSION: OnceLock<String> = OnceLock::new();
    VERSION.get_or_init(|| {
        let capabilities: serde_json::Value =
            serde_json::from_str(&quiddity::file_api::capabilities())
                .expect("quiddity capabilities JSON");
        let quiddity = capabilities["package"]["version"]
            .as_str()
            .expect("quiddity package version");
        format!("{} (quiddity-rust {quiddity})", env!("CARGO_PKG_VERSION"))
    })
}
