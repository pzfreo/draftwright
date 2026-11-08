//! The guard on lint's independence (ADR1-9, ADR5-2; maintainer decision 1 in ADR R4): no file
//! of `src/lint` may name the Plan or the Spec, or reach them through another module. Lint reads
//! the Analysis and the rendered drawing only, so its denominator never comes from the plan.
//!
//! The check reads every file of the module (each rule module `mod.rs` declares must be one of
//! them) with its comments and string literals removed, and refuses:
//! - the Plan's and Spec's types and operations by name (`Plan`, `Spec`, `PlannedView`, `plan`,
//!   `spec`, `embedded_spec`, …), which also covers the drawing's embedded spec (`drawing.spec`);
//! - any `crate::` path other than the Analysis (`crate::model::Analysis`), the bridge's
//!   features (`crate::bridge`), the Analysis's PMI reading (`crate::pmi`) and its lowering
//!   (`crate::pmi_lowering`, a function of the Analysis alone), the material re-entry predicate
//!   it shares with the leader router (`crate::material`, a function of the Analysis's solids
//!   and a page projection alone, so the guard reads that file too), and the rendered drawing
//!   (`crate::output`) and the independent scope vocabulary/default (`crate::remedy_scope`,
//!   whose source is audited by this guard too).

use std::path::Path;

/// Identifiers lint's code may not use.
const FORBIDDEN: [&str; 14] = [
    "Plan",
    "PlannedSheet",
    "PlannedView",
    "Problem",
    "Spec",
    "SheetSpec",
    "ViewSpec",
    "plan",
    "spec",
    "embedded_spec",
    "edit",
    "render",
    "api",
    "layout",
];

/// The `crate::` paths lint may use.
const ALLOWED: [&str; 12] = [
    "crate::model::Analysis",
    "crate::bridge",
    "crate::pmi",
    "crate::pmi_lowering",
    "crate::material",
    "crate::arc",
    "crate::radius",
    "crate::output",
    "crate::remedy_scope",
    "crate::finding",
    "crate::quiddity_evidence",
    "crate::geometry_ids",
];

/// *source* with its comments and string and character literals blanked out.
fn code_of(source: &str) -> String {
    let c: Vec<char> = source.chars().collect();
    let mut out = String::with_capacity(source.len());
    let mut i = 0;
    while i < c.len() {
        if c[i] == '/' && c.get(i + 1) == Some(&'/') {
            while i < c.len() && c[i] != '\n' {
                i += 1;
            }
        } else if c[i] == '/' && c.get(i + 1) == Some(&'*') {
            i += 2;
            while i < c.len() && !(c[i] == '*' && c.get(i + 1) == Some(&'/')) {
                i += 1;
            }
            i += 2;
            out.push(' ');
        } else if c[i] == 'r' && matches!(c.get(i + 1), Some('"' | '#')) {
            // A raw string: r"…", r#"…"#.
            let mut j = i + 1;
            let mut hashes = 0;
            while c.get(j) == Some(&'#') {
                hashes += 1;
                j += 1;
            }
            if c.get(j) != Some(&'"') {
                out.push(c[i]);
                i += 1;
                continue;
            }
            j += 1;
            let close: Vec<char> = std::iter::once('"')
                .chain(std::iter::repeat_n('#', hashes))
                .collect();
            while j < c.len() && c[j..].iter().take(close.len()).ne(close.iter()) {
                j += 1;
            }
            i = j + close.len();
            out.push_str("\"\"");
        } else if c[i] == '"' {
            i += 1;
            while i < c.len() && c[i] != '"' {
                if c[i] == '\\' {
                    i += 1;
                }
                i += 1;
            }
            i += 1;
            out.push_str("\"\"");
        } else if c[i] == '\'' && c.get(i + 2) == Some(&'\'') {
            i += 3;
            out.push_str("' '");
        } else if c[i] == '\'' && c.get(i + 1) == Some(&'\\') {
            i += 2;
            while i < c.len() && c[i] != '\'' {
                i += 1;
            }
            i += 1;
            out.push_str("' '");
        } else {
            out.push(c[i]);
            i += 1;
        }
    }
    out
}

/// What *source* does that lint may not: each forbidden identifier and `crate::` path it uses.
pub fn violations(source: &str) -> Vec<String> {
    let code = code_of(source);
    let mut out = Vec::new();
    for word in code.split(|c: char| !(c.is_alphanumeric() || c == '_')) {
        if FORBIDDEN.contains(&word) {
            out.push(word.to_string());
        }
    }
    // `crate::` paths, with `use crate::model::{A, B}` read as each of its names.
    let squeezed: String = code.chars().filter(|c| !c.is_whitespace()).collect();
    let mut rest = squeezed.as_str();
    while let Some(k) = rest.find("crate::") {
        rest = &rest[k..];
        let end = rest
            .find(|c: char| !(c.is_alphanumeric() || c == '_' || c == ':' || c == '{' || c == ','))
            .unwrap_or(rest.len());
        let path = &rest[..end];
        let paths: Vec<String> = match path.split_once('{') {
            Some((stem, names)) => names
                .split(',')
                .filter(|n| !n.is_empty())
                .map(|n| format!("{stem}{n}"))
                .collect(),
            None => vec![path.to_string()],
        };
        for p in paths {
            let allowed = ALLOWED
                .iter()
                .any(|a| p == *a || p.starts_with(&format!("{a}::")));
            if !allowed {
                out.push(p);
            }
        }
        rest = &rest[end.max(1)..];
    }
    out
}

/// Each file of `src/lint` and what it does that lint may not.
fn module_violations() -> Vec<(String, Vec<String>)> {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("src/lint");
    let mut files: Vec<_> = std::fs::read_dir(&dir)
        .expect("src/lint is readable")
        .map(|e| e.unwrap().path())
        .filter(|p| p.extension().is_some_and(|e| e == "rs"))
        .collect();
    files.sort();
    assert!(files.len() >= 2, "src/lint has its files");
    // Every module `mod.rs` declares is one of the files checked: a rule module kept elsewhere
    // (a directory module, a `#[path]`) would escape the guard.
    let declared: Vec<String> = std::fs::read_to_string(dir.join("mod.rs"))
        .unwrap()
        .lines()
        .filter_map(|l| {
            let l = l.trim().strip_prefix("pub ").unwrap_or(l.trim());
            l.strip_prefix("mod ")?
                .strip_suffix(';')
                .map(str::to_string)
        })
        .collect();
    for m in &declared {
        assert!(
            files.contains(&dir.join(format!("{m}.rs"))),
            "lint's module {m} is not a file the guard reads"
        );
    }
    assert!(
        declared.len() >= 8,
        "the guard reads lint's rule modules: {declared:?}"
    );
    // `crate::material` is allowed to lint, so it is held to lint's rule as well.
    files.push(dir.parent().unwrap().join("material.rs"));
    // The scope vocabulary/default has no authoring or drawing state. Audit it too.
    files.push(dir.parent().unwrap().join("remedy_scope.rs"));
    // Shared arc geometry/evidence is audited by the same independence guard.
    files.push(dir.parent().unwrap().join("arc.rs"));
    files.push(dir.parent().unwrap().join("radius.rs"));
    // Finding labels read only codes, severity and structured cause metadata.
    files.push(dir.parent().unwrap().join("finding.rs"));
    // Ladder identities are pure functions of Analysis face evidence; audit them too.
    files.push(dir.parent().unwrap().join("geometry_ids.rs"));
    files
        .into_iter()
        .filter(|p| p.file_name().unwrap() != "guard.rs")
        .map(|p| {
            let source = std::fs::read_to_string(&p).unwrap();
            (p.display().to_string(), violations(&source))
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lint_never_reads_the_plan_or_the_spec() {
        for (file, found) in module_violations() {
            assert!(
                found.is_empty(),
                "{file} reaches the Plan or the Spec (ADR1-9, ADR5-2): {found:?}"
            );
        }
    }

    #[test]
    fn the_guard_catches_what_it_should() {
        let bad = [
            "use crate::model::Plan;",
            "use crate::model::{Analysis, Spec};",
            "fn f(p: &Plan) {}",
            "let s = drawing.spec.clone();",
            "crate::model::plan(&a, &s)",
            "use crate::api::review;",
            "use crate::layout;",
            "crate::fixes::scope(&[])",
            "let v: Vec<crate::model::PlannedView> = vec![];",
        ];
        for source in bad {
            assert!(!violations(source).is_empty(), "missed: {source}");
        }
        let fine = [
            "use crate::model::Analysis;",
            "use crate::output::{Claim, Drawing};",
            "use crate::bridge::Feature;",
            "let scope = crate::remedy_scope::UNBOUND_SCOPE;",
            "let m = crate::material::solids(a, f);",
            "let r = crate::radius::projected(a, id, n, page);",
            "let s = crate::arc::Span { start: 0.0, sweep: 1.0 };",
            "// the Plan and the Spec are not read\nlet x = 1;",
            "/* Plan */ let s = \"the spec, in a message\";",
            "let v = r#\"plan\"#;",
            "let c = 'x';",
            "fn planned_area() {}",
        ];
        for source in fine {
            assert!(
                violations(source).is_empty(),
                "flagged: {source}: {:?}",
                violations(source)
            );
        }
    }
}
