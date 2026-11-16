//! The part's AP242 surface and common labels, and its drawing-wide requirements (general
//! tolerance, default surface finish, edge conditions), on the drawing
//! ([`draftwright::pmi_lowering`], [`draftwright::dims::gdt`], the title block and the general
//! notes): each drawn and searchable in the SVG and the PDF, the spec's tolerance winning over the
//! PMI's, ambiguous defaults refused, and a turned part keeping the view its PMI needs. What each
//! states is compared with draftwright's drawings by `tests/pmi_drawings.rs`.

#[path = "common/pdf.rs"]
mod pdf;

use crate::cache;

use std::io::Read as _;
use std::path::{Path, PathBuf};

use draftwright::model::{self, Analysis, Plan, Spec};

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

/// An AP242 fixture's part and Analysis (`tests/fixtures/pmi/step/<stem>.*.gz`), its bytes
/// written to a scratch file of each call's own, as the tests run in parallel and several read
/// one fixture.
fn analysed_uncached(stem: &str) -> (draftwright::haecceity::Part, Analysis) {
    static N: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
    let n = N.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let dir = root().join("tests/fixtures/pmi/step");
    let gz = std::fs::read_dir(&dir)
        .unwrap()
        .map(|e| e.unwrap().path())
        .find(|p| {
            let name = p.file_name().unwrap().to_string_lossy();
            name.split('.').next() == Some(stem)
        })
        .unwrap();
    let mut bytes = Vec::new();
    flate2::read::GzDecoder::new(std::fs::read(&gz).unwrap().as_slice())
        .read_to_end(&mut bytes)
        .unwrap();
    let file = gz.file_name().unwrap().to_string_lossy();
    let out = Path::new(env!("CARGO_TARGET_TMPDIR"))
        .join("pmi_labels")
        // nextest processes each start their local counter at zero.
        .join(format!("{}-{n}", std::process::id()));
    std::fs::create_dir_all(&out).unwrap();
    let path = out.join(file.strip_suffix(".gz").unwrap());
    std::fs::write(&path, &bytes).unwrap();
    model::analyse_native(&path).unwrap()
}

fn analysed(stem: &str) -> (std::sync::Arc<draftwright::haecceity::Part>, Analysis) {
    let path = std::fs::read_dir(root().join("tests/fixtures/pmi/step"))
        .unwrap()
        .map(|e| e.unwrap().path())
        .find(|p| p.file_name().unwrap().to_string_lossy().split('.').next() == Some(stem))
        .unwrap();
    cache::analysed(stem, &std::fs::read(path).unwrap(), true, || {
        analysed_uncached(stem)
    })
}

fn font() -> draftwright::text::Font {
    draftwright::text::Font::open(&root().join("fonts").join(draftwright::dims::FONT)).unwrap()
}

/// The drawing's text as the SVG and the PDF write it, checked to be its runs.
fn written(d: &draftwright::output::Drawing) -> Vec<String> {
    let runs: Vec<String> = d.text.iter().map(|r| r.text.clone()).collect();
    assert_eq!(pdf::svg_text(&d.svg()), runs, "the SVG's text");
    let bytes = d.pdf(&|name: &str| std::fs::read(root().join("fonts").join(name)).ok());
    assert_eq!(pdf::pdf_text(&bytes), runs, "the PDF's text");
    assert!(!d.dxf().is_empty());
    runs
}

/// The runs of the drawing's annotation *name*.
fn runs<'a>(d: &'a draftwright::output::Drawing, name: &str) -> Vec<&'a str> {
    let a = d
        .annotations
        .iter()
        .find(|a| a.name == name)
        .unwrap_or_else(|| panic!("{name} is drawn"));
    assert!(a.claims.is_empty(), "{name} claims no measurement");
    a.runs.iter().map(|&i| d.text[i].text.as_str()).collect()
}

fn codes<'a>(plan: &'a Plan, subject: &str) -> Vec<&'a str> {
    plan.problems
        .iter()
        .filter(|p| p.subjects.iter().chain(&p.refs).any(|s| s == subject))
        .map(|p| p.code.as_str())
        .collect()
}

#[test]
fn common_labels_are_drawn_with_leaders_and_searchable() {
    let (part, a) = analysed_uncached("nist_ftc_10_asme1_ap242-e2");
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    let labels: Vec<_> = plan.gdt.iter().filter(|g| g.kind == "label").collect();
    // F65, F81 and the five '4's are drawn (front and side, by their faces' longest extent);
    // '4X R0.15 MAX' names no face and is refused.
    let mut texts: Vec<&str> = labels.iter().map(|g| g.cells[0].as_str()).collect();
    texts.sort();
    assert_eq!(texts, ["4", "4", "4", "4", "4", "F65", "F81"]);
    assert!(labels.iter().any(|g| g.view == "v:side"));
    assert!(labels.iter().any(|g| g.view == "v:front"));
    assert_eq!(
        codes(&plan, "dimension:#20824"),
        ["pmi_not_lowered"],
        "a label on no face is refused"
    );
    let d = model::render(&part, &a, &plan, &font()).unwrap();
    let text = written(&d);
    for g in &labels {
        let p = g.placed.as_ref().unwrap();
        assert!(g.basis.starts_with("source-authored"), "{}", g.basis);
        // A leader with an arrowhead at its site, a shelf, and the text beyond it.
        assert_eq!(p.arrows.len(), 1, "{}", g.id);
        assert_eq!(p.lines.len(), 2, "{}", g.id);
        assert_eq!(runs(&d, &g.id), g.cells, "{}", g.id);
        assert!(text.contains(&g.cells[0]));
    }
    // Deterministic.
    let again = model::plan_recovered(&a, &Spec::default()).unwrap();
    assert_eq!(
        serde_json::to_string(&again).unwrap(),
        serde_json::to_string(&plan).unwrap()
    );
}

/// ftc07's `⌴` and private-use U+F055 common labels are fragments of callouts whose sizes the
/// file states elsewhere: drawn alone they print an empty box, so they are refused with why (a
/// warning: the lone symbol states nothing), while its named labels are still drawn.
#[test]
fn a_lone_symbol_label_is_refused_not_drawn_as_an_empty_box() {
    let (_, a) = analysed("nist_ftc_07_asme1_ap242-e2");
    let plan = cache::default_plan(&a, true);
    let mut texts: Vec<&str> = plan
        .gdt
        .iter()
        .filter(|g| g.kind == "label")
        .map(|g| g.cells[0].as_str())
        .collect();
    texts.sort();
    assert_eq!(texts, ["F1.1", "F1.2"]);
    for label in plan.gdt.iter().map(|g| g.label.as_str()) {
        assert!(label.chars().any(char::is_alphanumeric), "{label:?}");
    }
    for source in ["dimension:#16482", "dimension:#16423"] {
        let refused: Vec<_> = plan
            .problems
            .iter()
            .filter(|p| p.code == "pmi_not_lowered" && p.refs.iter().any(|r| r == source))
            .collect();
        assert_eq!(refused.len(), 1, "{source}");
        assert_eq!(refused[0].severity, "warning");
        assert!(
            refused[0].message.contains("lone symbol"),
            "{}",
            refused[0].message
        );
    }
}

#[test]
fn surface_labels_attach_at_their_edges_midpoints() {
    let (_, a) = analysed("nist_ctc_01_asme1_ap242-e1");
    let plan = cache::default_plan(&a, true);
    let mut labels: Vec<(&str, &str)> = plan
        .gdt
        .iter()
        .filter(|g| g.kind == "label")
        .map(|g| (g.cells[0].as_str(), g.view.as_str()))
        .collect();
    labels.sort();
    assert_eq!(labels, [("A", "v:front"), ("B", "v:front")]);
}

#[test]
fn sheet_requirements_are_on_the_title_block_beside_it_and_in_the_notes() {
    let (part, a) = analysed("spool_fits");
    let plan = cache::default_plan(&a, true);
    let kinds: Vec<(&str, &str, &str)> = plan
        .requirements
        .iter()
        .map(|q| (q.kind.as_str(), q.text.as_str(), q.carrier.as_str()))
        .collect();
    assert_eq!(
        kinds,
        [
            ("general_tolerance", "ISO 2768-m", "title_block"),
            ("default_surface_finish", "Ra 3.2", "sheet"),
            ("document_note", "Break sharp edges 0.2 max", "notes"),
        ]
    );
    let cell = plan
        .sheet
        .title_cells
        .iter()
        .find(|c| c.field == "general_tolerance")
        .unwrap();
    assert_eq!(cell.value.as_deref(), Some("ISO 2768-m"));
    let note = plan
        .notes
        .iter()
        .find(|n| n.id == "pmi:manufacturing_requirement.edge_condition")
        .unwrap();
    assert!(note.placed.is_some() && note.default);
    // The finish sits on the band above the title block, from its left end.
    let finish = plan.requirements[1].placed.as_ref().unwrap();
    let block = plan.sheet.title_block;
    assert!((finish.glyph[0] - block[0]).abs() < 1e-9);
    assert!(finish.glyph[1] > draftwright::title_block::top(block));
    let d = model::render(&part, &a, &plan, &font()).unwrap();
    let text = written(&d);
    for q in &plan.requirements {
        let read = runs(&d, &q.id).join(" ");
        assert!(read.contains(&q.text), "{}: {read:?}", q.id);
    }
    assert!(text.iter().any(|t| t == "Ra 3.2"));
    assert!(text.iter().any(|t| t == "ISO 2768-m"));
    assert!(text.iter().any(|t| t == "1  Break sharp edges 0.2 max"));
}

/// GRM-03 presents its general tolerance and default finish on the part's own solid (#15, its
/// manifold solid brep), not on a face: they are the part's, so they are lowered to the title
/// block and beside it, not refused as unresolved items.
#[test]
fn requirements_presented_on_the_parts_own_solid_are_document_level() {
    let (_, a) = analysed("GRM-03_thumbwheel_drive_screw_AP242_PMI");
    let plan = cache::default_plan(&a, true);
    let kinds: Vec<(&str, &str, &str)> = plan
        .requirements
        .iter()
        .map(|q| (q.kind.as_str(), q.text.as_str(), q.carrier.as_str()))
        .collect();
    assert_eq!(
        kinds,
        [
            ("general_tolerance", "ISO 2768-m", "title_block"),
            ("default_surface_finish", "Ra 3.2", "sheet"),
        ]
    );
    let cell = plan
        .sheet
        .title_cells
        .iter()
        .find(|c| c.field == "general_tolerance")
        .unwrap();
    assert_eq!(cell.value.as_deref(), Some("ISO 2768-m"));
    assert!(plan.requirements[1].placed.is_some());
    for source in [
        "manufacturing_requirement:#2016",
        "manufacturing_requirement:#2012",
    ] {
        assert!(
            codes(&plan, source).is_empty(),
            "{source}: {:?}",
            codes(&plan, source)
        );
    }
    assert!(
        plan.problems
            .iter()
            .all(|p| p.code != "general_tolerance_missing"
                && (p.code != "pmi_not_extracted" || p.severity == "info")),
        "{:#?}",
        plan.problems
    );
}

#[test]
fn the_specs_tolerance_wins_and_a_different_pmi_one_is_reported() {
    let (_, a) = analysed("spool_fits");
    let mut spec = Spec::default();
    spec.sheet
        .title
        .insert("tolerance".into(), "ISO 2768-f".into());
    let plan = model::plan(&a, &spec).unwrap();
    let cell = plan
        .sheet
        .title_cells
        .iter()
        .find(|c| c.field == "general_tolerance")
        .unwrap();
    assert_eq!(cell.value.as_deref(), Some("ISO 2768-f"));
    assert!(
        plan.requirements
            .iter()
            .all(|q| q.kind != "general_tolerance")
    );
    assert_eq!(
        codes(&plan, "manufacturing_requirement:#6475"),
        ["step_general_tolerance_disagreement"]
    );
    // The same value is the PMI's stated by the spec: carried, nothing to report.
    spec.sheet
        .title
        .insert("tolerance".into(), "ISO 2768-m".into());
    let plan = model::plan(&a, &spec).unwrap();
    assert!(
        plan.requirements
            .iter()
            .any(|q| q.kind == "general_tolerance")
    );
    assert!(codes(&plan, "manufacturing_requirement:#6475").is_empty());
}

#[test]
fn pmi_supplies_the_general_tolerance_but_a_spec_blank_still_needs_attention() {
    let (_, a) = analysed("spool_fits");
    let plan = cache::default_plan(&a, false);
    assert!(
        !plan
            .problems
            .iter()
            .any(|p| p.code == "general_tolerance_missing")
    );
    let mut spec = Spec::default();
    spec.sheet.title.insert("tolerance".into(), "".into());
    let plan = model::plan(&a, &spec).unwrap();
    assert!(
        plan.problems
            .iter()
            .any(|p| p.code == "general_tolerance_missing")
    );
    assert!(
        plan.problems
            .iter()
            .any(|p| p.code == "step_general_tolerance_disagreement")
    );
    let (_, a) = analysed("assembly_plate_pin");
    let plan = cache::default_plan(&a, false);
    assert!(
        plan.problems
            .iter()
            .any(|p| p.code == "general_tolerance_missing"),
        "ambiguous PMI must not silently supply a tolerance"
    );
}

#[test]
fn two_defaults_of_one_kind_are_ambiguous_and_neither_is_drawn() {
    let (_, a) = analysed("assembly_plate_pin");
    let plan = cache::default_plan(&a, false);
    let kinds: Vec<&str> = plan.requirements.iter().map(|q| q.kind.as_str()).collect();
    // The plate's and the pin's edge conditions are both notes; their two general tolerances
    // and two surface textures are refused.
    assert_eq!(kinds, ["document_note", "document_note"]);
    for source in [
        "manufacturing_requirement:#1566",
        "manufacturing_requirement:#1662",
        "manufacturing_requirement:#1558",
        "manufacturing_requirement:#1654",
    ] {
        let p = plan
            .problems
            .iter()
            .find(|p| {
                p.code == "pmi_not_lowered" && p.refs.first().map(String::as_str) == Some(source)
            })
            .unwrap();
        assert!(p.message.contains("ambiguous document default"), "{p:?}");
    }
    let title = plan
        .sheet
        .title_cells
        .iter()
        .find(|c| c.field == "general_tolerance")
        .unwrap();
    assert_eq!(title.value, None);
}

#[test]
fn a_turned_part_keeps_the_view_its_pmi_is_drawn_in() {
    // The spool is turned about one axis, so the reduction would leave out its side view; its
    // flatness frame is drawn there (draftwright's `annotation_view_required`).
    let (_, a) = analysed("spool_fits");
    let plan = cache::default_plan(&a, false);
    assert!(plan.views.iter().any(|v| v.id == "v:side"));
    let flatness = plan
        .gdt
        .iter()
        .find(|g| g.cells[0] == "⏥")
        .expect("the flatness frame");
    assert_eq!(flatness.view, "v:side");
    assert!(flatness.placed.is_some());
    // A spec that asks for the side view to be left out still has its way, and the frame says
    // why it is not drawn.
    let mut spec = Spec::default();
    spec.views.insert(
        "side".into(),
        draftwright::model::ViewSpec {
            sheet: None,
            omit: true,
            ..Default::default()
        },
    );
    let plan = model::plan(&a, &spec).unwrap();
    assert!(plan.views.iter().all(|v| v.id != "v:side"));
    let flatness = plan.gdt.iter().find(|g| g.cells[0] == "⏥").unwrap();
    assert!(codes(&plan, &flatness.id).contains(&"pmi_not_drawn"));
}

/// #214: what the plan prints from inch-authored PMI is millimetres an mm sheet can state: no
/// `INCH`, no float noise from a conversion (`1.524000000006`), no signed zero deviation
/// (`-0.0`, ISO 129-1 writes `0`), and no common label that states a radius without its value
/// (`4X R`), which is refused with its reason.
#[test]
fn inch_authored_pmi_prints_as_millimetres() {
    let mut stems: Vec<String> = std::fs::read_dir(root().join("tests/fixtures/pmi/step"))
        .unwrap()
        .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
        .filter(|n| n.starts_with("nist_") && n.contains("_asme1_"))
        .map(|n| n.split('.').next().unwrap().to_string())
        .collect();
    stems.sort();
    assert!(stems.iter().any(|s| s.starts_with("nist_stc_09")));
    // The purpose is conversion of inch-authored PMI into explicit millimetres.
    let metric = Spec {
        units: draftwright::units::Units::Mm,
        ..Spec::default()
    };
    for stem in &stems {
        let (_, a) = analysed(stem);
        let plan = model::plan(&a, &metric).unwrap();
        let labels = plan
            .dimensions
            .iter()
            .map(|d| (d.id.as_str(), d.label.clone()))
            .chain(plan.gdt.iter().map(|g| (g.id.as_str(), g.cells.join(" "))));
        for (id, label) in labels {
            for token in label.split_whitespace() {
                assert_ne!(token, "INCH", "{stem} {id}: {label:?}");
                let digits = token.trim_start_matches(|c: char| !c.is_ascii_digit());
                let number: String = digits
                    .chars()
                    .take_while(|c| c.is_ascii_digit() || *c == '.')
                    .collect();
                let decimals = number.split_once('.').map_or(0, |(_, f)| f.len());
                assert!(decimals <= 5, "{stem} {id}: {label:?}");
                let zero = token
                    .strip_prefix(['+', '-'])
                    .is_some_and(|t| !t.is_empty() && t.chars().all(|c| c == '0' || c == '.'));
                assert!(!zero, "{stem} {id}: {label:?}");
                if id.contains("common_label") {
                    assert_ne!(token, "R", "{stem} {id}: {label:?}");
                }
            }
        }
    }
    let (_, a) = analysed("nist_stc_09_asme1_ap242-e4");
    let plan = model::plan(&a, &metric).unwrap();
    let linear: Vec<&str> = plan
        .dimensions
        .iter()
        .filter(|d| d.id.starts_with("d:pmi:dimension.linear:"))
        .map(|d| d.label.as_str())
        .collect();
    for want in ["6.35 ±0.254", "9.525 ±0.2032", "3.556 ±0.2032"] {
        assert!(linear.contains(&want), "{want}: {linear:?}");
    }
    let refused: Vec<_> = plan
        .problems
        .iter()
        .filter(|p| p.code == "pmi_not_lowered" && p.message.contains("radius without its value"))
        .collect();
    assert_eq!(refused.len(), 2, "2X R and 4X R");
    assert!(refused.iter().all(|p| p.severity == "warning"));
}
