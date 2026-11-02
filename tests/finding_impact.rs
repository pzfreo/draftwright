//! Source inventory, including untaken/refusal paths: a fixture-only survey misses new codes.
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use syn::parse::Parser;
use syn::visit::{self, Visit};
use syn::{Expr, FnArg, Item, Lit, Pat};

fn sources(path: &Path, out: &mut Vec<PathBuf>) {
    for entry in std::fs::read_dir(path).unwrap() {
        let path = entry.unwrap().path();
        if path.is_dir() {
            sources(&path, out);
        } else if path.extension().is_some_and(|e| e == "rs") {
            out.push(path);
        }
    }
}
fn testing(attrs: &[syn::Attribute]) -> bool {
    attrs.iter().any(|a| {
        a.path().is_ident("test")
            || (a.path().is_ident("cfg")
                && matches!(&a.meta, syn::Meta::List(l) if l.tokens.to_string() == "test"))
    })
}
fn values(e: &Expr, out: &mut BTreeSet<String>) {
    match e {
        Expr::Lit(e) => {
            if let Lit::Str(s) = &e.lit {
                out.insert(s.value());
            }
        }
        Expr::If(e) => {
            if let Some(syn::Stmt::Expr(e, _)) = e.then_branch.stmts.last() {
                values(e, out);
            }
            if let Some((_, e)) = &e.else_branch {
                values(e, out);
            }
        }
        Expr::Match(e) => {
            for arm in &e.arms {
                values(&arm.body, out);
            }
        }
        Expr::Block(e) => {
            if let Some(syn::Stmt::Expr(e, _)) = e.block.stmts.last() {
                values(e, out);
            }
        }
        Expr::MethodCall(e) => values(&e.receiver, out),
        Expr::Reference(e) => values(&e.expr, out),
        Expr::Paren(e) => values(&e.expr, out),
        Expr::Macro(e) if e.mac.path.is_ident("format") => {
            let tokens = e.mac.tokens.clone();
            let args = syn::punctuated::Punctuated::<Expr, syn::Token![,]>::parse_terminated
                .parse2(tokens)
                .unwrap();
            if let Some(Expr::Lit(first)) = args.first()
                && let Lit::Str(format) = &first.lit
            {
                let format = format.value();
                assert!(
                    format == "{family}_requirement_{state}"
                        || format
                            .strip_suffix("_requirement_{state}")
                            .is_some_and(|family| family
                                .bytes()
                                .all(|b| b.is_ascii_lowercase() || b == b'_')),
                    "new dynamic finding-code template needs source-inventory expansion: {format}"
                );
                out.insert(format);
            }
        }
        Expr::Tuple(e) => {
            if let Some(e) = e.elems.first() {
                values(e, out);
            }
        }
        _ => {}
    }
}
#[derive(Default)]
struct Inventory {
    collect: bool,
    local_states: BTreeSet<String>,
    local_arguments: BTreeMap<String, BTreeSet<usize>>,
    arguments: BTreeMap<String, BTreeSet<usize>>,
    codes: BTreeSet<String>,
    families: BTreeSet<String>,
    states: BTreeSet<String>,
}
impl<'ast> Visit<'ast> for Inventory {
    fn visit_item(&mut self, item: &'ast Item) {
        let attrs = match item {
            Item::Fn(i) => &i.attrs,
            Item::Mod(i) => &i.attrs,
            _ => {
                visit::visit_item(self, item);
                return;
            }
        };
        if !testing(attrs) {
            visit::visit_item(self, item);
        }
    }
    fn visit_item_fn(&mut self, f: &'ast syn::ItemFn) {
        if testing(&f.attrs) {
            return;
        }
        self.local_arguments
            .entry(f.sig.ident.to_string())
            .or_default();
        for (i, arg) in f.sig.inputs.iter().enumerate() {
            if matches!(arg, FnArg::Typed(a) if matches!(a.pat.as_ref(), Pat::Ident(p) if p.ident == "code"))
            {
                self.local_arguments
                    .entry(f.sig.ident.to_string())
                    .or_default()
                    .insert(i);
                self.arguments
                    .entry(f.sig.ident.to_string())
                    .or_default()
                    .insert(i);
            }
        }
        visit::visit_item_fn(self, f);
    }
    fn visit_expr_struct(&mut self, e: &'ast syn::ExprStruct) {
        if self.collect
            && e.path
                .segments
                .last()
                .is_some_and(|s| s.ident == "Problem" || s.ident == "LintIssue")
        {
            for f in &e.fields {
                if matches!(&f.member, syn::Member::Named(n) if n == "code") {
                    values(&f.expr, &mut self.codes);
                }
            }
        }
        visit::visit_expr_struct(self, e);
    }
    fn visit_local(&mut self, l: &'ast syn::Local) {
        let pat = if let Pat::Type(p) = &l.pat {
            p.pat.as_ref()
        } else {
            &l.pat
        };
        let code = match pat {
            Pat::Ident(p) => p.ident == "code",
            Pat::Tuple(p) => matches!(p.elems.first(), Some(Pat::Ident(p)) if p.ident == "code"),
            _ => false,
        };
        if let (Pat::Ident(p), Some(init)) = (pat, &l.init)
            && let Expr::Closure(c) = init.expr.as_ref()
        {
            self.local_arguments.entry(p.ident.to_string()).or_default();
            for (i, arg) in c.inputs.iter().enumerate() {
                let arg = if let Pat::Type(t) = arg {
                    t.pat.as_ref()
                } else {
                    arg
                };
                if matches!(arg, Pat::Ident(p) if p.ident == "state") {
                    self.local_states.insert(p.ident.to_string());
                }
                if matches!(arg, Pat::Ident(p) if p.ident == "code") {
                    self.local_arguments
                        .entry(p.ident.to_string())
                        .or_default()
                        .insert(i);
                }
            }
        }
        if self.collect
            && code
            && let Some(init) = &l.init
        {
            values(&init.expr, &mut self.codes);
        }
        visit::visit_local(self, l);
    }
    fn visit_expr_assign(&mut self, e: &'ast syn::ExprAssign) {
        if self.collect
            && matches!(e.left.as_ref(), Expr::Field(f) if matches!(&f.member, syn::Member::Named(n) if n == "code"))
        {
            values(&e.right, &mut self.codes);
        }
        visit::visit_expr_assign(self, e);
    }
    fn visit_macro(&mut self, m: &'ast syn::Macro) {
        let name = m.path.segments.last().unwrap().ident.to_string();
        if (name == "vec" || name == "problem")
            && let Ok(args) = syn::punctuated::Punctuated::<Expr, syn::Token![,]>::parse_terminated
                .parse2(m.tokens.clone())
        {
            if self.collect
                && name == "problem"
                && let Some(e) = args.first()
            {
                values(e, &mut self.codes);
            }
            for e in &args {
                self.visit_expr(e);
            }
        }
    }
    fn visit_expr_call(&mut self, e: &'ast syn::ExprCall) {
        if !self.collect {
            visit::visit_expr_call(self, e);
            return;
        }
        if let Expr::Path(p) = e.func.as_ref() {
            let name = p.path.segments.last().unwrap().ident.to_string();
            if let Some(indices) = self
                .local_arguments
                .get(&name)
                .or_else(|| self.arguments.get(&name))
            {
                for i in indices {
                    if let Some(arg) = e.args.iter().nth(*i) {
                        values(arg, &mut self.codes);
                    }
                }
            }
            if name == "Refusal"
                && let Some(arg) = e.args.first()
            {
                values(arg, &mut self.codes);
            }
            if p.path.segments.iter().any(|s| s.ident == "parameters") && name == "coverage" {
                values(e.args.first().unwrap(), &mut self.families);
            }
            // Requirement-code templates use these issue closures; enumerate their actual states.
            if self.local_states.contains(&name) && e.args.len() == 4 {
                let mut states = BTreeSet::new();
                values(e.args.first().unwrap(), &mut states);
                self.states.extend(states.into_iter().filter(|s| {
                    !s.is_empty() && s.bytes().all(|b| b.is_ascii_lowercase() || b == b'_')
                }));
            }
        }
        visit::visit_expr_call(self, e);
    }
}
#[test]
fn every_emitted_code_has_an_impact_and_a_documented_reason() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let mut paths = Vec::new();
    sources(&root.join("src"), &mut paths);
    let files: Vec<_> = paths
        .iter()
        .filter(|p| p.file_name().unwrap() != "finding.rs")
        .map(|p| syn::parse_file(&std::fs::read_to_string(p).unwrap()).unwrap())
        .collect();
    let mut inventory = Inventory::default();
    // Discover cross-module helper signatures, then each file's local helpers/closures before
    // collecting its call sites. Same-named helpers with different arguments stay distinct.
    for file in &files {
        inventory.visit_file(file);
    }
    for file in &files {
        inventory.collect = false;
        inventory.local_arguments.clear();
        inventory.local_states.clear();
        inventory.visit_file(file);
        inventory.collect = true;
        inventory.visit_file(file);
    }
    for family in &inventory.families {
        for state in &inventory.states {
            inventory
                .codes
                .insert(format!("{family}_requirement_{state}"));
        }
    }
    let templates: Vec<_> = inventory
        .codes
        .iter()
        .filter(|c| c.ends_with("_requirement_{state}") && !c.contains("{family}"))
        .cloned()
        .collect();
    for template in templates {
        for state in &inventory.states {
            inventory.codes.insert(template.replace("{state}", state));
        }
    }
    inventory
        .codes
        .retain(|c| c.contains('_') && c.bytes().all(|b| b.is_ascii_lowercase() || b == b'_'));
    let docs = std::fs::read_to_string(root.join("docs/finding-impact.md")).unwrap();
    for code in &inventory.codes {
        let p = draftwright::model::Problem {
            code: code.clone(),
            severity: "warning".into(),
            ..Default::default()
        };
        assert!(
            docs.contains(&format!("| `{code}` |")),
            "undocumented emitted code: {code}"
        );
        assert!(serde_json::to_value(p).unwrap()["impact"].is_string());
    }
    // The production table is checked against this inventory by its own unit test.
    let catalogue: BTreeSet<_> = docs
        .lines()
        .filter_map(|l| l.strip_prefix("| `"))
        .filter_map(|l| l.split('`').next())
        .map(String::from)
        .collect();
    assert_eq!(
        inventory.codes, catalogue,
        "source inventory and impact catalogue differ"
    );
}

#[test]
fn inventory_catches_new_literals_helpers_macros_and_templates() {
    let source = syn::parse_file(
        r#"
        fn make(code: &str) { let p = Problem { code: code.into() }; }
        fn produce() {
            let p = Problem { code: "new_direct".into() };
            p.code = "new_assignment".into();
            let code = if true { "new_branch" } else { "new_else" };
            let (code, text): (&str, &str) = ("new_typed_tuple", "explanation");
            let found = |code: &str| {};
            found("new_closure");
            make("new_helper");
            let p = vec![LintIssue { code: "new_vector".into() }];
            problem!("new_macro", "warning", vec![], "explanation");
            parameters::coverage("new_family", &[], &[], noun, "explanation");
        }
    "#,
    )
    .unwrap();
    let mut inventory = Inventory::default();
    inventory.visit_file(&source);
    inventory.collect = true;
    inventory.visit_file(&source);
    for code in [
        "new_direct",
        "new_assignment",
        "new_branch",
        "new_else",
        "new_typed_tuple",
        "new_closure",
        "new_helper",
        "new_vector",
        "new_macro",
    ] {
        assert!(inventory.codes.contains(code), "inventory missed {code}");
    }
    assert!(inventory.families.contains("new_family"));
}

#[path = "common/corpus.rs"]
mod corpus;

fn assert_review_summary(file: &str, expected: [(usize, usize); 4]) {
    let Some(dir) = corpus::dir() else { return };
    let review = draftwright::api::review_from(
        &dir.join(file),
        draftwright::api::Features::Native,
        &Default::default(),
    )
    .unwrap();
    let wire = serde_json::to_value(&review).unwrap();
    let mut impacts: BTreeMap<&str, (usize, BTreeSet<&str>)> = BTreeMap::new();
    let mut groups = BTreeMap::<&str, usize>::new();
    for list in ["problems", "lint", "structural"] {
        for f in wire[list].as_array().unwrap() {
            let impact = f["impact"].as_str().unwrap();
            let group = f["group"].as_str().unwrap();
            assert!(!group.is_empty());
            let entry = impacts.entry(impact).or_default();
            entry.0 += 1;
            entry.1.insert(group);
            *groups.entry(group).or_default() += 1;
        }
    }
    for (impact, (findings, count)) in ["incomplete", "decision", "readability", "advisory"]
        .into_iter()
        .zip(expected)
    {
        let actual = impacts.get(impact);
        assert_eq!(actual.map_or(0, |i| i.0), findings, "{file}: {impact}");
        assert_eq!(
            actual.map_or(0, |i| i.1.len()),
            count,
            "{file}: {impact} groups"
        );
        assert_eq!(wire["summary"]["impacts"][impact]["findings"], findings);
        assert_eq!(wire["summary"]["impacts"][impact]["groups"], count);
    }
    assert_eq!(
        wire["summary"]["groups"],
        serde_json::to_value(groups).unwrap()
    );
    if file.contains("ctc_01") {
        assert_eq!(
            wire["summary"]["groups"]["dimensions_not_ported:section_recess"],
            4
        );
        assert_eq!(wire["summary"]["groups"]["extension_through_feature"], 9);
        // CTC-01's 350 row pitch is witnessed from the grid's right-hand column, so no callout
        // leader crosses its extension lines (on alpha.12, from the left-hand column, two did).
        assert!(wire["summary"]["groups"]["annotation_leader_crossing:extension_line"].is_null());
    }
}

#[test]
fn ctc01_counts_gaps_and_repeated_causes_without_hiding_findings() {
    assert_review_summary(
        "nist/nist_ctc_01_asme1_rd.stp",
        // The soft preferences retain this main-readable A3 drawing and its
        // reported unavailable detail; summary groups must retain every finding.
        // The 350 row pitch's extension lines cross no leader, so the 4× R25 leader is not
        // obstructed and stays a counted callout rather than a radius-centre table; a radius
        // leader that breaks the middle-arc/radial rule takes a longer compliant route, so 4× R25
        // now meets its arc radially (no radius_leader_not_radial). Advisory: no
        // annotation_leader_crossing on the 350 line. #226: four labels with line-work closer
        // than 0.5 mm are label_crowded notices: advisory (11, 3) -> (15, 4). quiddity-rust
        // 0.2.1 recognises the two V-slot notches and their rounded channels, which draftwright
        // does not draw yet, so they are reported as features_not_recognised: incomplete
        // (6, 2) -> (7, 3); their faces are no longer unclaimed_geometry: advisory -> (14, 3).
        // v0.2.3 (#127) states the inside rounds explicitly as 18× R10; the
        // 350 pitch passes within 0.5 mm of that new callout (one advisory).
        // cx/ctc01 consumes the published rectangular through-notch family:
        // both cuts are sized and located, so its one incomplete finding/group
        // goes (7,3 -> 6,2). Every other finding remains counted.
        // Reviewer round 1 replaces coordinate notes with linear opening
        // dimensions. Four old crowded labels clear; two legal blank-opening
        // placements remain inside-extents evidence. The unavailable detail
        // stays reported. No extension finding is hidden.
        [(6, 2), (1, 1), (1, 1), (13, 4)],
    );
}
#[test]
fn unsupported_recesses_and_rounding_have_separate_impacts() {
    // A fresh automatic choice no longer carries the planner's internal
    // layout_replanned trace (that notice belongs to kept layouts and local fixes).
    // quiddity-rust 0.1.1 (accepted for alpha.13) publishes 1000's plate hi unrounded
    // (4.2190876 where alpha.8 gave 4.219), so the printed 4.219 adds a nominal_rounded
    // advisory: advisory (11, 2) -> (12, 2). quiddity-rust 0.2.0 publishes hi on the grid
    // again (4.219, its #84), so that advisory goes: (12, 2) -> (11, 2). #226: a line within
    // 0.5 mm of a label is a label_crowded notice: (11, 2) -> (12, 3). #213: unexplained faces
    // 22 and 23 meet at a concave edge, a cut neither sized nor located: shape_not_recognised,
    // incomplete (11, 4) -> (12, 5). #226: the 2 × 2.81 pocket and HEX 1.935 A/F leaders end on
    // the dashed outlines of recesses their views see only hidden, two leader_to_hidden_edge
    // warnings: readability (0, 0) -> (2, 1).
    // v0.2.3 (#127) now dimensions the former unexplained cylindrical cut: one
    // fewer incomplete finding and group. The size is now stated, while its
    // bounded automatic leader retains the existing radius_leader_not_radial
    // fallback: one readability finding in its own group, alongside the two
    // unchanged hidden-edge count. Its interior label also makes structural
    // review report the legitimate blank-face placement and existing 0.0005 mm
    // nominal rounding: advisory (12, 3) -> (14, 4). Neither notice is suppressed.
    // A dimension place override can steer the radius leader.
    assert_review_summary("mfcadpp/1000.step", [(11, 4), (1, 1), (3, 2), (14, 4)]);
}
#[test]
fn informational_hole_bottom_and_recovery_remain_advisory() {
    // The automatic sheet choice's recovery is stated in its sheet basis, not as a
    // layout_replanned notice; the remaining informational findings stay advisory. #226: the
    // 2.4 A/F dimension within 0.5 mm of the section arrowhead is a label_crowded notice.
    // #213: the two shallow concentric recesses on its top face (0.33 deep) are cuts neither
    // sized nor located: shape_not_recognised, incomplete (1, 1) -> (3, 2). #226: its leader
    // ends on the flat the plan sees only hidden, under the flange: leader_to_hidden_edge,
    // readability (0, 0) -> (1, 1).
    assert_review_summary("gramel/string_post.step", [(3, 2), (1, 1), (1, 1), (3, 3)]);
}
