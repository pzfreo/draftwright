use sha2::{Digest, Sha256};
use std::fs;
use std::io;
use std::path::{Path, PathBuf};

pub fn inputs(root: &Path) -> io::Result<Vec<PathBuf>> {
    fn collect(
        root: &Path,
        path: &Path,
        extensions: &[&str],
        files: &mut Vec<PathBuf>,
    ) -> io::Result<()> {
        for entry in fs::read_dir(path)? {
            let path = entry?.path();
            if path.is_dir() {
                collect(root, &path, extensions, files)?;
            } else if path
                .extension()
                .and_then(|e| e.to_str())
                .is_some_and(|e| extensions.contains(&e))
            {
                files.push(path.strip_prefix(root).unwrap().to_path_buf());
            }
        }
        Ok(())
    }
    let mut files = ["Cargo.toml", "build.rs", "build_support.rs"]
        .map(PathBuf::from)
        .to_vec();
    // Cargo does not require dependencies to carry a lockfile. Include it when present,
    // while keeping downstream path/git/crates.io builds independent of its presence.
    if root.join("Cargo.lock").try_exists()? {
        files.push("Cargo.lock".into());
    }
    for (directory, extensions) in [
        ("src", &["rs"][..]),
        ("schemas", &["json"][..]),
        ("fonts", &["ttf", "otf"][..]),
    ] {
        collect(root, &root.join(directory), extensions, &mut files)?;
    }
    files.sort();
    Ok(files)
}

pub fn fingerprint(root: &Path, files: &[PathBuf]) -> io::Result<String> {
    let mut hash = Sha256::new();
    let mut files = files.to_vec();
    files.sort();
    for path in files {
        let name = path.to_str().unwrap().replace('\\', "/");
        let bytes = fs::read(root.join(&path))?;
        hash.update((name.len() as u64).to_le_bytes());
        hash.update(name.as_bytes());
        hash.update((bytes.len() as u64).to_le_bytes());
        hash.update(bytes);
    }
    Ok(hash.finalize().iter().map(|b| format!("{b:02x}")).collect())
}
