//! AP242 thread and knurl requirements on the drawing (PMI stage 2c,
//! `pmi_lowering::manufacturing`): each is given to the hole or turned step its cylinder proves
//! and printed on that feature's callout, its suffix or, with two or more and one too long,
//! `SEE MFG n` and the manufacturing requirements table, as draftwright's
//! `lower_ap242_manufacturing_requirements` and `manufacturing_schedule` do; what no one feature
//! owns is reported with draftwright's reason, never dropped.
//!
//! draftwright's callouts and table rows are in `tests/fixtures/pmi_drawings/<stem>.json.gz`
//! (`tools/capture_pmi_drawings.py`: the owner's callout that prints each requirement, and
//! `manufacturing_table`).

#[path = "common/pdf.rs"]
mod pdf;

use std::io::Read as _;
use std::path::{Path, PathBuf};

use draftwright::model::{self, Analysis, Plan, Spec};
use draftwright::pmi::{Field, Reading, Record};
use serde_json::Value;

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn gunzip(raw: &[u8]) -> Vec<u8> {
    let mut out = Vec::new();
    flate2::read::GzDecoder::new(raw)
        .read_to_end(&mut out)
        .unwrap();
    out
}

/// The fixture *stem*'s part and Analysis (Rust quiddity's features), from its own scratch file.
fn analysed(stem: &str) -> (draftwright::haecceity::Part, Analysis) {
    let dir = root().join("tests/fixtures/pmi/step");
    let gz = std::fs::read_dir(&dir)
        .unwrap()
        .map(|e| e.unwrap().path())
        .find(|p| {
            p.file_name()
                .unwrap()
                .to_string_lossy()
                .starts_with(&format!("{stem}."))
        })
        .unwrap();
    let name = gz.file_name().unwrap().to_string_lossy().into_owned();
    analysed_bytes(
        name.strip_suffix(".gz").unwrap(),
        &gunzip(&std::fs::read(&gz).unwrap()),
    )
}

/// The STEP file *name* of *bytes*' part and Analysis, from its own scratch file.
fn analysed_bytes(name: &str, bytes: &[u8]) -> (draftwright::haecceity::Part, Analysis) {
    static N: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
    let n = N.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    // nextest runs each test in its own process, with its own counter starting at zero.
    let thread = std::thread::current();
    let test = thread.name().unwrap_or("unnamed");
    let scratch = Path::new(env!("CARGO_TARGET_TMPDIR"))
        .join("pmi_thread_knurl")
        .join(format!("{}-{test}-{n}", std::process::id()));
    std::fs::create_dir_all(&scratch).unwrap();
    let path = scratch.join(name);
    std::fs::write(&path, bytes).unwrap();
    let out = model::analyse_native(&path).unwrap();
    std::fs::remove_file(&path).unwrap();
    std::fs::remove_dir(&scratch).unwrap();
    out
}

/// draftwright's drawing of *stem* (`tools/capture_pmi_drawings.py`).
fn captured(stem: &str) -> Value {
    let path = root().join(format!("tests/fixtures/pmi_drawings/{stem}.json.gz"));
    serde_json::from_slice(&gunzip(&std::fs::read(path).unwrap())).unwrap()
}

/// draftwright's label of the callout that prints *source*, `ø` read as the port's `⌀` on a hole
/// (the port prints a hole's `⌀` and a turned diameter's `ø`, as draftwright does).
fn python_label(drawing: &Value, source: &str) -> String {
    let item = drawing["items"]
        .as_array()
        .unwrap()
        .iter()
        .find(|i| i["source_id"] == source)
        .unwrap();
    let drawn = item["drawn"].as_array().unwrap();
    assert_eq!(drawn.len(), 1, "{source}: {drawn:?}");
    drawn[0]["text"].as_str().unwrap().to_string()
}

fn records(a: &Analysis) -> &[Record] {
    match &a.pmi {
        Some(Reading::Read(r)) => &r.records,
        other => panic!("not read: {other:?}"),
    }
}

fn edit(a: &Analysis, source: &str, f: impl Fn(&mut Record)) -> Analysis {
    let mut b = a.clone();
    let Some(Reading::Read(r)) = &mut b.pmi else {
        unreachable!()
    };
    r.records
        .iter_mut()
        .filter(|r| r.source_id == source)
        .for_each(f);
    b
}

/// The plan's item claiming *source* of *a* (by its item ID, `pmi:<identity>`), with its label.
fn claimed<'a>(plan: &'a Plan, a: &Analysis, source: &str) -> Vec<(&'a str, &'a str)> {
    let Some(Reading::Read(report)) = &a.pmi else {
        panic!("its PMI is read")
    };
    let feature = report.identities().ids[source].clone();
    plan.dimensions
        .iter()
        .filter(|d| d.measurements.iter().any(|m| m.feature == feature))
        .map(|d| (d.id.as_str(), d.label.as_str()))
        .collect()
}

/// The plan's refusal of *source*, if any: its message.
fn refused(plan: &Plan, source: &str) -> Option<String> {
    plan.problems
        .iter()
        .find(|p| p.code == "pmi_not_lowered" && p.refs.first().is_some_and(|s| s == source))
        .map(|p| p.message.clone())
}

#[test]
fn a_single_thread_prints_its_suffix_on_its_holes_callout() {
    let (part, a) = analysed("string_post_tapped");
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let source = "manufacturing_requirement:#3157";
    let python = python_label(&captured("string_post_tapped"), source);
    assert_eq!(python, "⌀1.6 ↧ 4 M2 x 0.4-6H RH; 2.5 MIN FULL THREAD");
    assert_eq!(
        claimed(&plan, &a, source),
        [("d:f:hole:f17/bore.diameter", "M2-6H ↧ 2.5\n⌀1.6 ↧ 4")]
    );
    assert_eq!(refused(&plan, source), None);
    // One requirement: no table, in draftwright's drawing or the port's.
    assert!(captured("string_post_tapped")["manufacturing_table"].is_null());
    assert!(plan.tables.is_empty());
    // Drawn, searchable, and credited by the coverage lint.
    let font =
        draftwright::text::Font::open(&root().join("fonts").join(draftwright::dims::FONT)).unwrap();
    let drawing = model::render(&part, &a, &plan, &font).unwrap();
    let svg = pdf::svg_text(&drawing.svg()).join(" ");
    assert!(svg.contains("M2-6H ↧ 2.5"), "{svg}");
    assert!(svg.contains("⌀1.6 ↧ 4"), "{svg}");
    let lint = draftwright::lint::lint(&a, &drawing);
    assert!(
        !lint.iter().any(|i| i.sources.iter().any(|s| s == source)),
        "{lint:#?}"
    );
}

#[test]
fn several_long_requirements_refer_to_the_manufacturing_requirements_table() {
    let (part, a) = analysed("thumbwheel_thread_knurl");
    let plan = model::plan(
        &a,
        &serde_json::from_value(serde_json::json!({"sheet":{"callouts":{"style":"note"}}}))
            .unwrap(),
    )
    .unwrap();
    let drawing_py = captured("thumbwheel_thread_knurl");
    for (source, id) in [
        (
            "manufacturing_requirement:#689",
            "d:f:hole:f12/bore.diameter",
        ),
        (
            "manufacturing_requirement:#722",
            "d:f:turned_step:f9/step.diameter",
        ),
        (
            "manufacturing_requirement:#749",
            "d:f:turned_step:f2/step.diameter",
        ),
    ] {
        let expected = match source {
            "manufacturing_requirement:#689" => "M2-6H SEE MFG 1",
            "manufacturing_requirement:#722" => "ø6 SEE MFG 2",
            _ => "M5-6g SEE MFG 3",
        };
        assert_eq!(claimed(&plan, &a, source), [(id, expected)], "{source}");
        assert_eq!(refused(&plan, source), None);
    }
    let mut rows: Vec<Vec<String>> =
        serde_json::from_value(drawing_py["manufacturing_table"].clone()).unwrap();
    assert_eq!(rows[2], ["MFG 2", "ø6 MAX AFTER KNURL; DIAMOND KNURL P1;"]);
    let table = plan
        .tables
        .iter()
        .find(|t| t.id == "manufacturing_requirements")
        .unwrap();
    // Note form retains the tapping drill as well as its full thread requirement.
    rows[1][1] = "M2 x 0.4-6H RH; 2.5 MIN FULL THREAD; ⌀1.6 ↧".into();
    rows.insert(2, vec![String::new(), "3.8".into()]);
    assert_eq!(table.rows, rows);
    assert!(table.default);
    let p = table.placed.as_ref().expect("the table has a place");
    // The requirement column is left-aligned at the padding, as `_left_align_cols=(1,)`.
    let font =
        draftwright::text::Font::open(&root().join("fonts").join(draftwright::dims::FONT)).unwrap();
    for (row, at) in table.rows.iter().zip(&p.cells_at) {
        let laid = draftwright::tables::text_ink(&row[1], &font);
        let left = at[1].unwrap()[0] - 0.5 * (laid.max[0] - laid.min[0]);
        assert!(
            (left - (p.columns[1] + draftwright::tables::PAD)).abs() < 1e-9,
            "{row:?}"
        );
    }
    // The table does not cover the general notes, the views or the dimensions.
    assert!(
        !plan
            .problems
            .iter()
            .any(|p| p.subjects.iter().any(|s| s == "manufacturing_requirements")),
        "{:#?}",
        plan.problems
    );
    let drawing = model::render(&part, &a, &plan, &font).unwrap();
    let text = pdf::svg_text(&drawing.svg());
    for cell in rows.iter().flatten().filter(|c| !c.is_empty()) {
        assert!(text.contains(cell), "{cell}");
    }
    let lint = draftwright::lint::lint(&a, &drawing);
    for source in ["#689", "#722", "#749"] {
        let source = format!("manufacturing_requirement:{source}");
        assert!(
            !lint.iter().any(|i| i.sources.contains(&source)),
            "{source}"
        );
    }
}

#[test]
fn a_requirement_no_one_feature_owns_is_reported_with_draftwrights_reason() {
    let (part, a) = analysed("thumbwheel_thread_knurl");
    let lint_of = |b: &Analysis| {
        let plan = model::plan(b, &Spec::default()).unwrap();
        let font =
            draftwright::text::Font::open(&root().join("fonts").join(draftwright::dims::FONT))
                .unwrap();
        let drawing = model::render(&part, b, &plan, &font).unwrap();
        (plan.clone(), draftwright::lint::lint(b, &drawing))
    };
    // No owner: the tapped hole is not recognised.
    let mut b = a.clone();
    b.features.retain(|f| f.id != "f:hole:f12");
    let source = "manufacturing_requirement:#689";
    let (plan, lint) = lint_of(&b);
    let why = refused(&plan, source).unwrap();
    assert!(
        why.split(" Graphical callout").next().unwrap().ends_with(
            "is not drawn: unmatched manufacturing requirement: no canonical feature matches source topology."
        ),
        "{why}"
    );
    assert!(why.contains("Graphical callout #697 (\"Internal thread requirement\")"));
    assert!(plan.problems.iter().any(|p| p.code == "pmi_not_lowered"
        && p.refs.contains(&source.to_string())
        && p.refs.contains(&"graphical_callout:#697".into())));
    assert!(claimed(&plan, &b, source).is_empty());
    assert!(lint.iter().any(|i| i.code == "pmi_not_lowered"
        && i.sources == [source]
        && i.message.contains("no canonical feature matches")));
    // Only the knurl needs a process note; the thread keeps its compact designation.
    let labels: Vec<&str> = plan.dimensions.iter().map(|d| d.label.as_str()).collect();
    assert!(
        labels.contains(&"ø6 MAX AFTER KNURL SEE MFG 1\nDIAMOND KNURL P1"),
        "{labels:?}"
    );
    assert!(labels.contains(&"M5-6g"), "{labels:?}");
    // Two owners: a second turned step on the knurl's cylinder.
    let mut b = a.clone();
    let mut twin = b
        .features
        .iter()
        .find(|f| f.id == "f:turned_step:f9")
        .unwrap()
        .clone();
    twin.id = "f:turned_step:9".into();
    b.features.push(twin);
    let plan = model::plan(&b, &Spec::default()).unwrap();
    let why = refused(&plan, "manufacturing_requirement:#722").unwrap();
    assert!(
        why.contains(
            "ambiguous manufacturing requirement: source topology matches 2 canonical features"
        ),
        "{why}"
    );
    // Text that disagrees with its cylinder: the knurl's maximum diameter, in words and fields.
    let b = edit(&a, "manufacturing_requirement:#722", |r| {
        r.label = r.label.replace("DIA 6 mm", "DIA 7 mm");
        for (name, v) in &mut r.fields {
            if name == "major diameter" {
                *v = Field::Length(7.0);
            }
        }
    });
    let plan = model::plan(&b, &Spec::default()).unwrap();
    let why = refused(&plan, "manufacturing_requirement:#722").unwrap();
    assert!(
        why.split(" Graphical callout")
            .next()
            .unwrap()
            .ends_with("manufacturing requirement text disagrees with source cylinder."),
        "{why}"
    );
    // Words and fields that disagree.
    let b = edit(&a, "manufacturing_requirement:#722", |r| {
        r.label = r.label.replace("DIA 6 mm", "DIA 7 mm");
    });
    let plan = model::plan(&b, &Spec::default()).unwrap();
    let why = refused(&plan, "manufacturing_requirement:#722").unwrap();
    assert!(
        why.split(" Graphical callout")
            .next()
            .unwrap()
            .ends_with("structured knurl values disagree with prose."),
        "{why}"
    );
    // Words outside draftwright's grammar, paired with a structured statement.
    let b = edit(&a, "manufacturing_requirement:#749", |r| {
        r.label = "M5 thread".into();
    });
    let plan = model::plan(&b, &Spec::default()).unwrap();
    let why = refused(&plan, "manufacturing_requirement:#749").unwrap();
    assert!(
        why.split(" Graphical callout")
            .next()
            .unwrap()
            .ends_with("structured thread cannot reconcile with prose."),
        "{why}"
    );
    assert!(records(&a).iter().any(|r| r.source_id == source));
}

/// The draftwright-editor's straight knurl (AP242 `turned_knurl`, pitch 1, 75 teeth) on the ⌀24
/// cylinder of draftwright.io's spindle sample, its text edited by *edit*. Beyond draftwright:
/// its Part 21 pass never reads a `turned_knurl`.
fn spindle(edit: impl Fn(String) -> String) -> (draftwright::haecceity::Part, Analysis) {
    let gz = root().join("tests/fixtures/turned_knurl/spindle_turned_knurl.step.gz");
    let text = String::from_utf8(gunzip(&std::fs::read(gz).unwrap())).unwrap();
    analysed_bytes("spindle_turned_knurl.step", edit(text).as_bytes())
}

#[test]
fn a_semantic_turned_knurl_prints_on_its_diameters_callout() {
    let (part, a) = spindle(|s| s);
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let source = "manufacturing_requirement:#925";
    let labels: Vec<&str> = claimed(&plan, &a, source).iter().map(|c| c.1).collect();
    assert_eq!(labels, ["ø24 MAX AFTER KNURL\nSTRAIGHT KNURL P1"]);
    assert_eq!(refused(&plan, source), None);
    assert!(
        !plan
            .problems
            .iter()
            .any(|p| p.code == "pmi_not_extracted" && p.message.contains("turned_knurl")),
        "{:#?}",
        plan.problems
    );
    let font =
        draftwright::text::Font::open(&root().join("fonts").join(draftwright::dims::FONT)).unwrap();
    let drawing = model::render(&part, &a, &plan, &font).unwrap();
    assert!(
        pdf::svg_text(&drawing.svg())
            .join(" ")
            .contains("STRAIGHT KNURL P1")
    );
}

#[test]
fn a_turned_knurl_with_no_tooth_count_is_not_lowered() {
    // Nothing confirms that the diametral pitch is the circular pitch, so it is not printed.
    let (_, a) = spindle(|s| s.replace("#935,#937,#938", "#935,#938"));
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let source = "manufacturing_requirement:#925";
    let why = refused(&plan, source).unwrap();
    assert!(
        why.split(" Graphical callout")
            .next()
            .unwrap()
            .ends_with("turned knurl has no tooth count to confirm its pitch."),
        "{why}"
    );
}

/// The draftwright-editor's diamond knurl (pitch 1, 75 teeth, helix angle 30°) on the same
/// spindle, its text edited by *edit*.
fn diamond(edit: impl Fn(String) -> String) -> (draftwright::haecceity::Part, Analysis) {
    let gz = root().join("tests/fixtures/turned_knurl/spindle_diamond_knurl.step.gz");
    let text = String::from_utf8(gunzip(&std::fs::read(gz).unwrap())).unwrap();
    analysed_bytes("spindle_diamond_knurl.step", edit(text).as_bytes())
}

#[test]
fn a_semantic_diamond_knurl_prints_on_its_diameters_callout() {
    let (_, a) = diamond(|s| s);
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let source = "manufacturing_requirement:#925";
    let labels: Vec<&str> = claimed(&plan, &a, source).iter().map(|c| c.1).collect();
    assert_eq!(labels, ["ø24 MAX AFTER KNURL\nDIAMOND KNURL P1"]);
    assert_eq!(refused(&plan, source), None);
}

#[test]
fn a_diagonal_turned_knurl_is_reported_not_drawn() {
    // A valid diagonal knurl (helix angle and hand stated) reaches the port, which has no
    // drawing for it: it stays a pmi_not_extracted error rather than a guessed callout.
    let (_, a) = diamond(|s| {
        s.replace("TURNED_KNURL('','diamond')", "TURNED_KNURL('','diagonal')")
            .replace(
                "#939,#942),#841);",
                "#939,#942,#99999),#841);\n#99999=DESCRIPTIVE_REPRESENTATION_ITEM('helix hand','right');",
            )
    });
    let plan = model::plan(&a, &Spec::default()).unwrap();
    assert!(
        plan.problems.iter().any(|p| p.code == "pmi_not_extracted"
            && p.message.contains("A STEP knurl is not drawn")
            && p.message.contains("Add the requirement to the Spec")
            && p.refs.contains(&"knurl:0:0".into())
            && p.refs.contains(&"turned_knurl".into())
            && p.refs.iter().any(|r| r.contains("not projected"))),
        "{:#?}",
        plan.problems
    );
    assert!(plan.dimensions.iter().all(|d| !d.label.contains("KNURL")));
}

#[test]
fn a_turned_knurl_whose_pitch_and_teeth_disagree_is_not_lowered() {
    // π·24/1 is 75.4 teeth, not 60: the pitch is not the circular pitch the editor writes.
    let (_, a) = spindle(|s| s.replace("COUNT_MEASURE(75.)", "COUNT_MEASURE(60.)"));
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let source = "manufacturing_requirement:#925";
    let why = refused(&plan, source).unwrap();
    assert!(
        why.split(" Graphical callout")
            .next()
            .unwrap()
            .ends_with("turned knurl pitch 1 and tooth count 60 disagree: π·24/1 is 75.4 teeth."),
        "{why}"
    );
    assert!(
        claimed(&plan, &a, source)
            .iter()
            .all(|c| !c.1.contains("KNURL"))
    );
}

#[test]
fn the_requirements_drawing_is_deterministic() {
    let (_, a) = analysed("thumbwheel_thread_knurl");
    let one = serde_json::to_string(&model::plan(&a, &Spec::default()).unwrap()).unwrap();
    let two = serde_json::to_string(&model::plan(&a, &Spec::default()).unwrap()).unwrap();
    assert_eq!(one, two);
}

/// The straight-knurl spindle with the draftwright-editor's external thread on its ⌀10 band,
/// hand-written as the editor's `pmi_writer.rs` writes one (AP242 `thread`, side external,
/// form M, fit class 6g, qualifier M10x1.5, and a 'thread manufacturing' attribute set with
/// pitch 1.5 and thread length 18, the band's length as specify-core states it).
fn external_thread() -> (draftwright::haecceity::Part, Analysis) {
    let gz = root().join("tests/fixtures/turned_knurl/spindle_external_thread.step.gz");
    analysed_bytes(
        "spindle_external_thread.step",
        &gunzip(&std::fs::read(gz).unwrap()),
    )
}

/// The lines of the drawing's thin layer (threads and knurls drawn over the outline), page mm.
fn thin_lines(svg: &str) -> Vec<[f64; 4]> {
    let Some(start) = svg.find("id=\"thin\"") else {
        return Vec::new();
    };
    let group = &svg[start..start + svg[start..].find("</g>").unwrap()];
    group
        .split("<line ")
        .skip(1)
        .map(|l| {
            ["x1", "y1", "x2", "y2"].map(|k| {
                let at = l.find(&format!("{k}=\"")).unwrap() + k.len() + 2;
                l[at..at + l[at..].find('"').unwrap()].parse().unwrap()
            })
        })
        .collect()
}

fn length(l: &[f64; 4]) -> f64 {
    (l[2] - l[0]).hypot(l[3] - l[1])
}

/// The scale of the plan's front view (the profile views share it).
fn front_scale(plan: &Plan) -> f64 {
    plan.views.iter().find(|v| v.id == "v:front").unwrap().scale
}

#[test]
fn a_semantic_external_thread_prints_its_designation_in_place_of_the_diameter() {
    let (part, a) = external_thread();
    let r = records(&a)
        .iter()
        .find(|r| r.kind == "external_thread")
        .expect("the AP242 external thread is projected");
    let source = r.source_id.clone();
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let labels: Vec<&str> = claimed(&plan, &a, &source).iter().map(|c| c.1).collect();
    assert_eq!(labels, ["M10-6g"]);
    assert_eq!(refused(&plan, &source), None);
    // Neither the thread nor its manufacturing attribute set is reported not projected.
    assert!(
        !plan.problems.iter().any(|p| p.code == "pmi_not_extracted"
            && (p.message.contains("(thread)")
                || p.message.contains("(attribute set)")
                    && p.message.contains("general_property"))),
        "{:#?}",
        plan.problems
    );
    // The knurl still prints on its own diameter.
    let knurl = "manufacturing_requirement:#925";
    let labels: Vec<&str> = claimed(&plan, &a, knurl).iter().map(|c| c.1).collect();
    assert_eq!(labels, ["ø24 MAX AFTER KNURL\nSTRAIGHT KNURL P1"]);
    let font =
        draftwright::text::Font::open(&root().join("fonts").join(draftwright::dims::FONT)).unwrap();
    let drawing = model::render(&part, &a, &plan, &font).unwrap();
    let svg = drawing.svg();
    assert!(pdf::svg_text(&svg).join(" ").contains("M10-6g"));
    let lint = draftwright::lint::lint(&a, &drawing);
    assert!(
        !lint.iter().any(|i| i.sources.contains(&source)),
        "{lint:#?}"
    );
}

#[test]
fn an_external_thread_is_drawn_at_its_minor_diameter_over_its_length() {
    let (part, a) = external_thread();
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let font =
        draftwright::text::Font::open(&root().join("fonts").join(draftwright::dims::FONT)).unwrap();
    let drawing = model::render(&part, &a, &plan, &font).unwrap();
    let s = front_scale(&plan);
    // ISO 6410: thin lines along the axis at d3 = 10 − 1.22687·1.5 over the 18 mm thread
    // length, one each side of the axis, in each profile view (front and side, not the
    // section).
    let minor = 10.0 - 1.226_87 * 1.5;
    let lines: Vec<[f64; 4]> = thin_lines(&drawing.svg())
        .into_iter()
        .filter(|l| (length(l) - 18.0 * s).abs() < 1e-6)
        .collect();
    let pairs = lines
        .iter()
        .enumerate()
        .flat_map(|(i, a)| lines[i + 1..].iter().map(move |b| (a, b)))
        .filter(|(a, b)| {
            (a[0] - a[2]).abs() < 1e-9
                && (b[0] - b[2]).abs() < 1e-9
                && (a[1] - b[1]).abs() < 1e-9
                && ((a[0] - b[0]).abs() - minor * s).abs() < 1e-3
        })
        .count();
    assert_eq!(pairs, 2, "{lines:?}");
}

#[test]
fn a_knurl_is_drawn_as_a_band_of_thin_lines_over_its_face() {
    let font =
        draftwright::text::Font::open(&root().join("fonts").join(draftwright::dims::FONT)).unwrap();
    for (straight, (part, a)) in [(true, spindle(|s| s)), (false, diamond(|s| s))] {
        let plan = model::plan(&a, &Spec::default()).unwrap();
        let s = front_scale(&plan);
        let drawing = model::render(&part, &a, &plan, &font).unwrap();
        let lines = thin_lines(&drawing.svg());
        assert!(lines.len() >= 6, "{lines:?}");
        // Over the middle half of the 12 mm knurled length, within the ⌀24 band's width.
        let (lo, hi) = lines.iter().fold((f64::MAX, f64::MIN), |(lo, hi), l| {
            (lo.min(l[1]).min(l[3]), hi.max(l[1]).max(l[3]))
        });
        assert!(hi - lo <= 6.0 * s + 1e-6, "{lo} {hi}");
        for l in &lines {
            let dx = (l[2] - l[0]).abs();
            let dy = (l[3] - l[1]).abs();
            if straight {
                // Along the (vertical) axis.
                assert!(dx < 1e-9, "{l:?}");
            } else {
                // Crossed at 30° to it.
                assert!((dx.atan2(dy).to_degrees() - 30.0).abs() < 1e-3, "{l:?}");
            }
            assert!(length(l) <= 6.0 * s / if straight { 1.0 } else { 0.75f64.sqrt() } + 1e-6);
        }
        if !straight {
            let rising = lines.iter().filter(|l| (l[2] - l[0]) * (l[3] - l[1]) > 0.0);
            assert!(rising.count() * 2 >= lines.len() - 2);
        }
    }
}

/// The editor's fragments: a thread's and knurl's thin lines belong to their view's fragment,
/// so they move with it, and none are left in the furniture; together the fragments still hold
/// every line of the sheet.
#[test]
fn thread_and_knurl_lines_travel_in_their_views_fragment() {
    let (part, a) = external_thread();
    let s = draftwright::session::Session::new(part, a, Spec::default()).unwrap();
    let full = s.drawing().svg();
    let fragments = s.svg_fragments(None);
    assert_eq!(
        full.matches("<line ").count(),
        fragments
            .values()
            .map(|f| f.matches("<line ").count())
            .sum::<usize>()
    );
    let total = thin_lines(&full).len();
    let front = thin_lines(&fragments["v:front"]).len();
    assert!(front > 0 && front < total, "{front} of {total}");
    assert_eq!(
        fragments
            .iter()
            .filter(|(id, _)| id.starts_with("v:"))
            .map(|(_, f)| thin_lines(f).len())
            .sum::<usize>(),
        total
    );
    assert!(thin_lines(&fragments["furniture"]).is_empty());
}

/// The thread's callout is led to the ⌀10 band's silhouette at an angle, not along it: a leader
/// lying on the part's edge is ambiguous (ISO 128-22). The spindle turns about z, which its
/// profile views draw upright, so the silhouette is vertical on the page; the default sheet and
/// a Spec-named A3 at 2:1 (where the leader once ran down the outline) are both checked.
#[test]
fn the_threads_leader_meets_the_silhouette_at_an_angle() {
    let (_, a) = external_thread();
    let a3: Spec = serde_json::from_value(serde_json::json!({
        "schema": "draftwright.spec/2",
        "sheet": { "size": "A3", "scale": "2:1" }
    }))
    .unwrap();
    for spec in [Spec::default(), a3] {
        let plan = model::plan(&a, &spec).unwrap();
        let d = plan
            .dimensions
            .iter()
            .find(|d| d.label.starts_with("M10-6g"))
            .expect("the thread's callout");
        let [tip, elbow] = d.placed.as_ref().expect("placed").lines[0];
        let (dx, dy) = ((elbow[0] - tip[0]).abs(), (elbow[1] - tip[1]).abs());
        assert!(
            dx.atan2(dy).to_degrees() >= 15.0 - 1e-9,
            "{}: {tip:?} {elbow:?}",
            plan.sheet.basis
        );
    }
}

/// A thread and a fit (J3, #30) on one part: the threaded diameter prints only its thread's
/// designation, which carries the thread's own tolerance class, and a fit or band given to it
/// is withheld and reported rather than spliced into the designation; a plain diameter keeps its
/// shaft fit with its ISO 286 limits.
#[test]
fn a_threaded_diameter_keeps_its_designation_and_a_plain_one_its_fit() {
    let gz = root().join("tests/fixtures/pmi/step/hw_thumbwheel_thread_knurl_intent.step.gz");
    let (_, a) = analysed_bytes(
        "hw_thumbwheel_thread_knurl_intent.step",
        &gunzip(&std::fs::read(gz).unwrap()),
    );
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let labels: Vec<&str> = plan.dimensions.iter().map(|d| d.label.as_str()).collect();
    assert!(labels.contains(&"ø10 f7"), "{labels:?}");
    assert!(labels.contains(&"M5-6g"), "{labels:?}");

    let (_, a) = external_thread();
    let id = "d:f:turned_step:f2/step.diameter";
    let spec: Spec = serde_json::from_value(serde_json::json!({
        "schema": "draftwright.spec/2",
        "dimensions": { id: { "tolerance": "+0 -0.1" } }
    }))
    .unwrap();
    let plan = model::plan(&a, &spec).unwrap();
    let d = plan.dimensions.iter().find(|d| d.id == id).unwrap();
    assert_eq!(d.label, "M10-6g");
    assert!(
        plan.problems
            .iter()
            .any(|p| p.code == "tolerance_withheld" && p.subjects == [id]),
        "{:?}",
        plan.problems
    );
}

/// F1's thin manufacturing ink and K4's drawn transform must use the same crop as a detail.
#[test]
fn an_external_thread_detail_clips_its_ink_and_records_its_true_transform() {
    let (part, a) = external_thread();
    let manufacturing = draftwright::pmi_lowering::manufacturing(&a);
    let thread = manufacturing
        .owned
        .iter()
        .find(|o| {
            matches!(
                o.depiction.as_ref().map(|d| &d.kind),
                Some(draftwright::pmi_lowering::DepictionKind::Thread { .. })
            )
        })
        .unwrap();
    let spec: Spec = serde_json::from_value(serde_json::json!({
        "sheet": {"size": "A3", "scale": "1:2"},
        "views": {"detail_c": {"detail": {"around": thread.owner, "scale_factor": 3.0}}}
    }))
    .unwrap();
    let plan = model::plan(&a, &spec).unwrap();
    let detail = plan
        .views
        .iter()
        .find(|v| v.id == "v:detail_c")
        .expect("authored detail");
    let bounds = detail.detail.as_ref().unwrap().bounds;
    let view = draftwright::haecceity::hlr::View::new(detail.direction, detail.up).unwrap();
    let corners: Vec<_> = (0..8)
        .map(|i| view.map([0, 1, 2].map(|k| bounds[(i >> k) & 1][k])))
        .collect();
    let lo = [0, 1].map(|k| corners.iter().map(|p| p[k]).fold(f64::INFINITY, f64::min));
    let hi = [0, 1].map(|k| {
        corners
            .iter()
            .map(|p| p[k])
            .fold(f64::NEG_INFINITY, f64::max)
    });
    let centre = [0, 1].map(|k| (lo[k] + hi[k]) / 2.0);
    let ink = draftwright::cosmetic::ink(&a, &manufacturing, detail);
    assert!(
        !ink.is_empty(),
        "thread detail retains its conventional lines"
    );
    for curve in &ink {
        let b = curve.bbox();
        for k in 0..2 {
            assert!(
                b[k] >= detail.at[k] + detail.scale * (lo[k] - centre[k]) - 1e-6,
                "{b:?}"
            );
            assert!(
                b[k + 2] <= detail.at[k] + detail.scale * (hi[k] - centre[k]) + 1e-6,
                "{b:?}"
            );
        }
    }
    let font =
        draftwright::text::Font::open(&root().join("fonts").join(draftwright::dims::FONT)).unwrap();
    let drawing = model::render(&part, &a, &plan, &font).unwrap();
    let frame = &drawing.sheet.frames["detail_c"];
    let whole = view.map([0, 1, 2].map(|k| (a.envelope[0][k] + a.envelope[1][k]) / 2.0));
    for k in 0..2 {
        assert!((frame.at[k] - detail.at[k] - detail.scale * (whole[k] - centre[k])).abs() < 1e-6);
    }
}
/// A reference is only useful while its complete, source-bound row reaches the sheet.
#[test]
fn manufacturing_references_require_complete_source_matched_ink() {
    let (part, a) = analysed("thumbwheel_thread_knurl");
    let plan = model::plan(
        &a,
        &serde_json::from_value(serde_json::json!({"sheet":{"callouts":{"style":"note"}}}))
            .unwrap(),
    )
    .unwrap();
    let font =
        draftwright::text::Font::open(&root().join("fonts").join(draftwright::dims::FONT)).unwrap();
    let original = model::render(&part, &a, &plan, &font).unwrap();
    let unresolved = |drawing: &draftwright::output::Drawing| {
        draftwright::lint::lint(&a, drawing)
            .into_iter()
            .filter(|i| i.code == "manufacturing_reference_unresolved")
            .collect::<Vec<_>>()
    };
    assert!(unresolved(&original).is_empty());
    for damage in ["header text", "header absent"] {
        let mut drawing = original.clone();
        let header = drawing
            .annotations
            .iter_mut()
            .find(|a| a.name == "manufacturing_requirements_header")
            .unwrap();
        if damage == "header text" {
            drawing.text[header.runs[0]].text = "WRONG HEADER".into();
        } else {
            header.runs.clear();
        }
        assert_eq!(unresolved(&drawing).len(), 3, "{damage}");
    }
    // Removing the whole table cannot leave the short leaders certified by provenance alone.
    let mut absent = original.clone();
    absent
        .annotations
        .retain(|a| !a.name.starts_with("manufacturing_requirements/"));
    absent
        .sheet
        .items
        .retain(|i| i.name != "manufacturing_requirements");
    assert_eq!(unresolved(&absent).len(), 3);
    let mut punctuated = absent.clone();
    for annotation in original.annotations.iter().filter(|a| {
        !a.name.starts_with("manufacturing_requirements/")
            && a.runs
                .iter()
                .any(|&i| original.text[i].text.contains("SEE MFG"))
    }) {
        let last = *annotation.runs.last().unwrap();
        punctuated.text[last].text.push('.');
    }
    assert_eq!(unresolved(&punctuated).len(), 3);
    let rows: Vec<_> = original
        .annotations
        .iter()
        .filter(|a| a.name.starts_with("manufacturing_requirements/"))
        .collect();
    assert_eq!(rows.len(), 3);
    for row in rows {
        assert!(
            row.claims.is_empty(),
            "a row is source provenance, not a second measurement owner"
        );
        assert_eq!(row.approved_claims.len(), 1);
        for damage in ["text", "empty", "source", "tag", "duplicate"] {
            let mut drawing = original.clone();
            let index = drawing
                .annotations
                .iter()
                .position(|a| a.name == row.name)
                .unwrap();
            match damage {
                "text" => {
                    drawing.text[row.runs[1]].text = "INCOMPLETE REQUIREMENT".into();
                }
                "empty" => drawing.annotations[index].runs.clear(),
                "source" => {
                    drawing.annotations[index].approved_claims[0].feature =
                        "pmi:wrong-source".into()
                }
                "tag" => drawing.text[row.runs[0]].text = "MFG 999".into(),
                "duplicate" => drawing.annotations.push(row.clone()),
                _ => unreachable!(),
            }
            let issues = unresolved(&drawing);
            assert_eq!(issues.len(), 1, "{} {damage}: {issues:?}", row.name);
            assert_eq!(issues[0].severity, draftwright::lint::Severity::Error);
            assert!(issues[0].sources.len() == 1 && issues[0].annotation.is_some());
        }
    }
}

#[test]
fn authored_manufacturing_rows_keep_the_compilers_source_bindings() {
    let (part, analysis) = analysed("thumbwheel_thread_knurl");
    let spec = model::edit(
        &serde_json::from_value(serde_json::json!({"sheet":{"callouts":{"style":"note"}}}))
            .unwrap(),
        &serde_json::json!([{
            "op":"set", "path":"features/f:hole:f12/thread",
            "value":{"designation":"M2x0.4-6H", "length":2.5}
        }]),
    )
    .unwrap();
    let plan = model::plan(&analysis, &spec).unwrap();
    assert!(
        plan.tables
            .iter()
            .any(|t| t.id == "manufacturing_requirements" && t.placed.is_some())
    );
    let font =
        draftwright::text::Font::open(&root().join("fonts").join(draftwright::dims::FONT)).unwrap();
    // The renderer receives the original Analysis, exactly as the batch and session paths do.
    let drawing = model::render(&part, &analysis, &plan, &font).unwrap();
    let effective = draftwright::spec_gdt::effective(&analysis, &spec).unwrap();
    let issues = draftwright::lint::lint(&effective, &drawing);
    assert!(
        !issues
            .iter()
            .any(|i| i.code == "manufacturing_reference_unresolved"),
        "{issues:?}"
    );
    let manufacturing = draftwright::pmi_lowering::manufacturing(&effective);
    let authored = manufacturing
        .owned
        .iter()
        .find(|o| o.source.starts_with("spec:"))
        .unwrap();
    assert!(drawing.annotations.iter().any(|a| {
        a.name.starts_with("manufacturing_requirements/")
            && a.approved_claims
                .iter()
                .any(|c| c.feature == authored.id && c.parameter == authored.aspect)
    }));
}

#[test]
fn spec_can_switch_thread_and_knurl_ink_independently_without_losing_requirements() {
    let (part, a) = external_thread();
    let auto = model::plan(&a, &Spec::default()).unwrap();
    let m = draftwright::pmi_lowering::manufacturing(&a);
    let front = |p: &Plan| p.views.iter().find(|v| v.id == "v:front").unwrap().clone();
    let automatic = draftwright::cosmetic::ink(&a, &m, &front(&auto));
    assert!(automatic.len() > 2);
    for (thread, knurl, expected) in [
        (false, false, 0),
        (true, false, 2),
        (false, true, automatic.len() - 2),
        (true, true, automatic.len()),
    ] {
        // Hold the automatic paper and scale: depiction ink scales with the view, and with the
        // 3 mm frame gutter (2026-10-25) dropping the depictions lets A4 at 1:1 fit.
        let s: Spec = serde_json::from_value(serde_json::json!({
            "sheet":{"thread_depiction":false,"knurl_depiction":false,
                     "size":auto.sheet.size,"scale":auto.sheet.scale},
            "views":{"front":{"thread_depiction":thread,"knurl_depiction":knurl}}
        }))
        .unwrap();
        let p = model::plan(&a, &s).unwrap();
        assert_eq!(
            draftwright::cosmetic::ink(&a, &m, &front(&p)).len(),
            expected
        );
        let label = p
            .dimensions
            .iter()
            .find(|d| d.label.starts_with("M10-6g"))
            .unwrap();
        assert_eq!(label.label.contains("FULL AVAILABLE"), !thread);
        assert_eq!(label.placed.as_ref().unwrap().rotation, 0.0);
        let drawing = model::render(
            &part,
            &a,
            &p,
            &draftwright::text::Font::new(draftwright::api::MONO.to_vec()).unwrap(),
        )
        .unwrap();
        assert!(
            !draftwright::lint::lint(&a, &drawing)
                .iter()
                .any(|i| i.code == "pmi_not_rendered")
        );
    }
    let mut session = draftwright::Session::new(part, a, Spec::default()).unwrap();
    let before = session.svg_fragments(None);
    assert!(!thin_lines(&before["v:front"]).is_empty());
    let delta = session
        .apply(
            &[
                draftwright::items::Op::set("sheet/thread_depiction", serde_json::json!(false)),
                draftwright::items::Op::set("sheet/knurl_depiction", serde_json::json!(false)),
            ],
            true,
        )
        .unwrap();
    assert!(delta.geometry.views.iter().any(|(id, _)| id == "v:front"));
    let fragments = session.svg_fragments(Some(&delta));
    assert!(thin_lines(&fragments["v:front"]).is_empty());
}

/// Coarse designations, depth aliases and Spec controls are independent of legacy captures.
#[test]
fn compact_taps_print_the_exact_stated_full_thread_depth_and_drill_values() {
    for stem in ["string_post_tapped", "hw_string_post_tapped_intent"] {
        let (part, a) = analysed(stem);
        let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
        let d = plan
            .dimensions
            .iter()
            .find(|d| d.label.starts_with("M2-6H"))
            .unwrap();
        assert_eq!(d.label, "M2-6H ↧ 2.5\n⌀1.6 ↧ 4", "{stem}");
        let placed = d.placed.as_ref().unwrap();
        assert_eq!(placed.rotation, 0.0);
        assert!(placed.label_box[3] - placed.label_box[1] > draftwright::callouts::line_spacing());
        let font = draftwright::text::Font::new(draftwright::api::MONO.to_vec()).unwrap();
        let drawing = model::render(&part, &a, &plan, &font).unwrap();
        let thread = drawing.annotations.iter().find(|m| m.name == d.id).unwrap();
        assert_eq!(thread.runs.len(), 2);
        assert!(
            !draftwright::lint::lint(&a, &drawing)
                .iter()
                .any(|i| i.code == "pmi_source_text_mismatch")
        );
        for (old, new) in [("2.5", "6.5"), ("1.6", "1.7"), ("↧ 4", "↧ 5")] {
            let mut damaged = drawing.clone();
            for &run in &thread.runs {
                damaged.text[run].text = damaged.text[run].text.replace(old, new);
            }
            assert!(
                draftwright::lint::lint(&a, &damaged)
                    .iter()
                    .any(|i| i.code == "pmi_source_text_mismatch"),
                "{stem}: {old}"
            );
        }
    }
}

#[test]
fn structured_full_thread_aliases_must_agree_with_each_other_and_prose() {
    let (_, a) = analysed("string_post_tapped");
    let source = "manufacturing_requirement:#3157";
    let same = edit(&a, source, |r| {
        r.fields
            .push(("full thread depth".into(), Field::Length(2.5)))
    });
    assert!(
        draftwright::pmi_lowering::manufacturing(&same)
            .refused
            .is_empty()
    );
    let disagree = edit(&a, source, |r| {
        r.fields
            .push(("full thread depth".into(), Field::Length(3.0)))
    });
    assert!(
        draftwright::pmi_lowering::manufacturing(&disagree)
            .refused
            .iter()
            .any(|p| p.message.contains("aliases disagree"))
    );
    let prose = edit(&a, source, |r| {
        r.fields.retain(|(name, _)| name != "minimum full thread");
        r.fields
            .push(("full thread depth".into(), Field::Length(3.0)));
    });
    assert!(
        draftwright::pmi_lowering::manufacturing(&prose)
            .refused
            .iter()
            .any(|p| p.message.contains("disagree with prose"))
    );
}

#[test]
fn spec_and_pmi_threads_share_short_designations_and_one_style_override() {
    let (_, a) = external_thread();
    let id = "d:f:turned_step:f2/step.diameter";
    let pmi = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(
        pmi.dimensions.iter().find(|d| d.id == id).unwrap().label,
        "M10-6g"
    );
    for (designation, expected) in [
        ("M10x1.5-6g RH", "M10-6g"),
        ("M10x1-6g LH", "M10 x 1-6g LH"),
    ] {
        let spec: Spec = serde_json::from_value(serde_json::json!({
            "features":{"f:turned_step:f2":{"thread":{"designation":designation}}}
        }))
        .unwrap();
        let plan = model::plan(&a, &spec).unwrap();
        assert_eq!(
            plan.dimensions.iter().find(|d| d.id == id).unwrap().label,
            expected
        );
    }
    let note: Spec = serde_json::from_value(serde_json::json!({
        "sheet":{"callouts":{"style":"full"}},
        "features":{"f:turned_step:f2":{"callouts":{"style":"note"}}},
        "dimensions":{id:{"text":{"template":"{value} AFTER PLATING"}}}
    }))
    .unwrap();
    let plan = model::plan(&a, &note).unwrap();
    assert_eq!(
        plan.dimensions.iter().find(|d| d.id == id).unwrap().label,
        "M10-6g SEE MFG 1 AFTER\nPLATING"
    );
    assert!(
        plan.tables
            .iter()
            .any(|t| t.id == "manufacturing_requirements")
    );
}

#[test]
fn callout_wrapping_measures_real_ink_and_preserves_words_and_forced_breaks() {
    let input = "DIAMOND KNURL P0.8 FULL WIDTH; CUT/FORMED PERMITTED"
        .split_whitespace()
        .map(str::to_string)
        .collect::<Vec<_>>();
    let wrapped = draftwright::callouts::wrap(&input);
    let font = draftwright::text::Font::new(draftwright::api::MONO.to_vec()).unwrap();
    assert!(draftwright::callouts::lines(&wrapped).len() > 1);
    for line in draftwright::callouts::lines(&wrapped) {
        let ink = draftwright::dims::label_ink(line, &font);
        assert!(ink.max[0] - ink.min[0] <= 48.0);
    }
    assert_eq!(
        draftwright::callouts::label(&wrapped)
            .split_whitespace()
            .collect::<Vec<_>>(),
        input.iter().map(String::as_str).collect::<Vec<_>>()
    );
    let forced = vec![
        "M2-6H".into(),
        "↧".into(),
        "2.5".into(),
        "\n".into(),
        "⌀1.6".into(),
        "↧".into(),
        "4".into(),
    ];
    assert_eq!(draftwright::callouts::wrap(&forced), forced);
    let word = vec!["ONE_UNBREAKABLE_MANUFACTURING_WORD_EXCEEDING_THE_WIDTH".into()];
    assert_eq!(draftwright::callouts::wrap(&word), word);
}

/// A trailing BOTH SIDES qualifies the requirement before it: the 48 mm wrap applies to the
/// requirement, and the qualifier rides on its last line (#104: LM10UU's and the corpus FTC-10's
/// callouts stay one line; STC-10's wrapped one ends `10 BOTH SIDES`).
#[test]
fn both_sides_rides_on_the_requirements_last_line() {
    let terms = |s: &str| s.split(' ').map(str::to_string).collect::<Vec<_>>();
    let both = draftwright::callouts::BOTH_SIDES.to_string();
    let mut short = terms("⌀10 THRU ⌴SF ⌀16.4 ↧ 1.4");
    short.push(both.clone());
    assert_eq!(draftwright::callouts::wrap(&short), short);
    let mut long = terms("4× ⌀7 ±0.25 THRU ⌴ ⌀10 ↧ 10");
    let alone = draftwright::callouts::wrap(&long);
    long.push(both.clone());
    let wrapped = draftwright::callouts::wrap(&long);
    let lines = draftwright::callouts::lines(&wrapped);
    assert_eq!(lines.len(), draftwright::callouts::lines(&alone).len());
    assert_eq!(lines.last().unwrap().last(), Some(&both));
}

#[test]
fn auto_moves_a_three_line_knurl_to_its_complete_source_note() {
    let (part, a) = analysed("bolt_thread_knurl");
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    let d = plan
        .dimensions
        .iter()
        .find(|d| d.measurements.iter().any(|m| m.parameter == "knurl"))
        .unwrap();
    assert_eq!(d.label, "ø6.25 SEE MFG 1");
    assert!(d.basis.contains("exceed two lines"));
    let table = plan
        .tables
        .iter()
        .find(|t| t.id == "manufacturing_requirements")
        .unwrap();
    let note = table
        .rows
        .iter()
        .flat_map(|r| r.iter())
        .cloned()
        .collect::<Vec<_>>()
        .join(" ");
    assert!(note.contains("ø6.25 MAX AFTER KNURL"));
    assert!(note.contains("STRAIGHT KNURL P1"));
    assert!(note.contains("CUT/FORMED PERMITTED"));
    let font = draftwright::text::Font::new(draftwright::api::MONO.to_vec()).unwrap();
    let drawing = model::render(&part, &a, &plan, &font).unwrap();
    assert!(
        !draftwright::lint::lint(&a, &drawing)
            .iter()
            .any(|i| i.code == "manufacturing_reference_unresolved"
                || i.code == "pmi_source_text_mismatch")
    );
}

#[test]
fn an_internal_threads_bore_fit_stays_on_its_tapping_drill() {
    let (_, a) = analysed("string_post_tapped");
    let spec: Spec = serde_json::from_value(serde_json::json!({
        "features":{"f:hole:f17":{"fit":"H7"}}
    }))
    .unwrap();
    let plan = model::plan(&a, &spec).unwrap();
    let d = plan
        .dimensions
        .iter()
        .find(|d| d.id == "d:f:hole:f17/bore.diameter")
        .unwrap();
    assert_eq!(d.label, "M2-6H ↧ 2.5\n⌀1.6 H7 ↧ 4");
}

/// ISSUE-1298-a, draftwright's fallback (`_place_manufacturing_schedule`): a manufacturing
/// requirements table with no place on the sheet is an alternative presentation that is not
/// used, so no callout says `SEE MFG n` and each prints its requirement in full, with no
/// `table_not_placed` error. GRM-03's AP242 thumbwheel on A4 at 5:1: its views and callouts fit,
/// its 95.6 × 42 mm table does not (before the fallback: `table_not_placed`, three
/// `manufacturing_reference_unresolved` and two `pmi_source_text_mismatch` errors). The fallback
/// drawing has no error of any kind, in the plan or found on the rendered sheet.
#[test]
fn a_manufacturing_table_with_no_place_falls_back_to_full_callouts() {
    let (part, a) = analysed("GRM-03_thumbwheel_drive_screw_AP242_PMI");
    let spec = |sheet: serde_json::Value| -> Spec {
        let mut sheet = sheet;
        sheet["callouts"] = serde_json::json!({"style": "note"});
        // Both declared datums drawn, as when this case was recorded: undrawn, datum A leaves
        // the table room on A4 at 5:1.
        sheet["uncited_datums"] = serde_json::json!(true);
        // Keep this a genuine no-place fallback: the new split form has its own test below.
        sheet["table_columns"] = serde_json::json!({"manufacturing_requirements":1});
        serde_json::from_value(serde_json::json!({ "sheet": sheet })).unwrap()
    };
    // With room, the requirements go to the table, as before.
    let roomy = model::plan(&a, &spec(serde_json::json!({}))).unwrap();
    let table = roomy
        .tables
        .iter()
        .find(|t| t.id == "manufacturing_requirements")
        .expect("the table, with room");
    assert!(table.placed.is_some());
    let font =
        draftwright::text::Font::open(&root().join("fonts").join(draftwright::dims::FONT)).unwrap();
    let given = spec(serde_json::json!({"size": "A4", "scale": "5:1"}));
    for plan in [
        model::plan(&a, &given).unwrap(),
        model::plan_recovered(&a, &given).unwrap(),
    ] {
        assert!(
            !plan.problems.iter().any(|p| p.code == "table_not_placed"),
            "{:#?}",
            plan.problems
        );
        assert!(
            plan.tables
                .iter()
                .all(|t| t.id != "manufacturing_requirements")
        );
        // The requested spec stays the plan's: the fallback is no authored override.
        assert_eq!(plan.spec.sheet, given.sheet);
        assert!(plan.spec.features.is_empty());
        // The fallback says what it did, naming the requirements printed in full.
        let fallback = plan
            .problems
            .iter()
            .find(|p| p.code == "manufacturing_table_fallback")
            .expect("an info finding naming the fallback");
        assert_eq!(fallback.severity, "info");
        assert!(fallback.refs.len() >= 2, "{fallback:#?}");
        for source in &fallback.refs {
            let labels = claimed(&plan, &a, source);
            assert_eq!(labels.len(), 1, "{source}: {labels:?}");
            let (id, label) = labels[0];
            assert!(!label.contains("SEE MFG"), "{source}: {label:?}");
            let basis = &plan.dimensions.iter().find(|d| d.id == id).unwrap().basis;
            assert!(
                basis.contains("manufacturing table fallback")
                    && basis.contains("table has no place on the sheet")
                    && !basis.contains("callouts/style Full"),
                "{basis}"
            );
        }
        let thread = plan
            .dimensions
            .iter()
            .map(|d| d.label.split_whitespace().collect::<Vec<_>>().join(" "))
            .find(|l| l.contains("MIN FULL THREAD"))
            .expect("the thread in full");
        assert!(thread.contains("M2 x 0.4-6H RH"), "{thread}");
        // No hard finding: not in the plan, and not on the rendered sheet.
        let errors: Vec<_> = plan
            .problems
            .iter()
            .filter(|p| p.severity == "error")
            .map(|p| &p.id)
            .collect();
        assert!(errors.is_empty(), "{errors:?}");
        let drawing = model::render(&part, &a, &plan, &font).unwrap();
        let found: Vec<_> = draftwright::lint::lint(&a, &drawing)
            .into_iter()
            .chain(draftwright::lint::structural::check_sheets(
                std::slice::from_ref(&drawing),
            ))
            .filter(|i| i.severity == draftwright::lint::Severity::Error)
            .map(|i| i.id)
            .collect();
        assert!(found.is_empty(), "{found:?}");
    }
}

#[test]
fn a_split_manufacturing_table_keeps_every_requirement_and_its_claims() {
    let (part, a) = analysed("GRM-03_thumbwheel_drive_screw_AP242_PMI");
    let mut spec: Spec = serde_json::from_value(serde_json::json!({
        "sheet":{"callouts":{"style":"note"},"uncited_datums":true}
    }))
    .unwrap();
    let roomy = model::plan(&a, &spec).unwrap();
    let original = roomy
        .tables
        .iter()
        .find(|t| t.id == "manufacturing_requirements")
        .unwrap();
    spec.sheet.size = Some("A4".into());
    spec.sheet.scale = Some("5:1".into());
    let font =
        draftwright::text::Font::open(&root().join("fonts").join(draftwright::dims::FONT)).unwrap();
    for plan in [
        model::plan(&a, &spec).unwrap(),
        model::plan_recovered(&a, &spec).unwrap(),
    ] {
        let table = plan.tables.iter().find(|t| t.id == original.id).unwrap();
        assert!(table.placed.as_ref().unwrap().blocks.len() >= 2);
        assert_eq!(table.rows, original.rows);
        assert_eq!(table.row_claims, original.row_claims);
        assert!(
            !plan
                .problems
                .iter()
                .any(|p| p.code == "manufacturing_table_fallback" || p.code == "table_not_placed")
        );
        let drawing = model::render(&part, &a, &plan, &font).unwrap();
        for header in &table.rows[0] {
            assert_eq!(
                drawing.text.iter().filter(|t| t.text == *header).count(),
                table.placed.as_ref().unwrap().blocks.len(),
                "every block repeats {header}"
            );
        }
        let errors: Vec<_> = draftwright::lint::lint(&a, &drawing)
            .into_iter()
            .chain(draftwright::lint::structural::check_sheets(
                std::slice::from_ref(&drawing),
            ))
            .filter(|i| i.severity == draftwright::lint::Severity::Error)
            .collect();
        assert!(errors.is_empty(), "{errors:?}");
    }
}

/// ISSUE-1298-a, `test_identical_typed_threads_on_distinct_owners_keep_two_owned_annotations`:
/// two identical holes that each carry the same thread are not counted into one `2×` callout;
/// each owner keeps a callout of its own stating its thread. bracket_positions' six ⌀10 through
/// holes are one `6×` callout; with an M12 tap on two of them, those two print apart and the
/// other four stay grouped.
#[test]
fn identical_threads_on_distinct_owners_keep_two_callouts() {
    let (_, a) = analysed("bracket_positions");
    let plain = model::plan(&a, &Spec::default()).unwrap();
    assert!(
        plain
            .dimensions
            .iter()
            .any(|d| d.label.starts_with("6× ⌀10 ") && d.label.ends_with(" THRU")),
        "{:?}",
        plain
            .dimensions
            .iter()
            .map(|d| &d.label)
            .collect::<Vec<_>>()
    );
    let tap = serde_json::json!({"thread": {"designation": "M12x1.75-6H"}});
    let spec: Spec = serde_json::from_value(
        serde_json::json!({"features": {"f:hole:f233": tap.clone(), "f:hole:f234": tap}}),
    )
    .unwrap();
    let plan = model::plan(&a, &spec).unwrap();
    let threaded: Vec<(&str, &str)> = plan
        .dimensions
        .iter()
        .filter(|d| d.label.contains("M12-6H"))
        .map(|d| (d.id.as_str(), d.label.as_str()))
        .collect();
    let ids: Vec<&str> = threaded.iter().map(|t| t.0).collect();
    assert_eq!(
        ids,
        ["d:f:hole:f233/bore.diameter", "d:f:hole:f234/bore.diameter"],
        "{threaded:?}"
    );
    for (_, label) in &threaded {
        assert!(
            label.starts_with("M12-6H THRU\n⌀10 ") && label.ends_with(" THRU"),
            "{label:?}"
        );
    }
    assert_eq!(threaded[0].1, threaded[1].1);
    assert!(
        plan.dimensions
            .iter()
            .any(|d| d.label.starts_with("4× ⌀10 ") && d.label.ends_with(" THRU")),
        "{:?}",
        plan.dimensions.iter().map(|d| &d.label).collect::<Vec<_>>()
    );
    assert!(!plan.dimensions.iter().any(|d| d.label.contains("2× M12")));
}

/// ISSUE-1298-a, the tap drill (`ThreadRequirement.drill_*`, `test_internal_minimum_full_thread_
/// cannot_exceed_tap_drill_depth`, `test_internal_tap_drill_depth_must_match_text_source_
/// cylinder_and_blind_hole`): an internal thread's tapping drill is read with the thread, in its
/// words and structured fields, and printed on its hole's callout; a minimum full thread deeper
/// than the drill, or a drill depth its cylinder does not have, is refused with draftwright's
/// reason, never drawn.
#[test]
fn a_tap_drill_is_read_with_its_thread_and_refused_when_inconsistent() {
    let (_, a) = analysed("string_post_tapped");
    let source = "manufacturing_requirement:#3157";
    let m = draftwright::pmi_lowering::manufacturing(&a);
    assert!(m.refused.is_empty());
    let owned = m.owned.iter().find(|o| o.source == source).unwrap();
    // The table's complete wording keeps the drill's diameter and depth.
    assert!(owned.text.ends_with("; ⌀1.6 ↧ 4"), "{}", owned.text);
    // The full thread case is draftwright's own, on the words alone (a structured statement
    // paired with words that fail is refused as irreconcilable).
    let set = |r: &mut Record, name: &str, from: &str, to: &str, v: f64| {
        r.label = r.label.replace(from, to);
        if name == "minimum full thread" {
            r.fields.clear();
        }
        for (n, value) in &mut r.fields {
            if n == name {
                *value = Field::Length(v);
            }
        }
    };
    for (name, from, to, v, reason) in [
        (
            "minimum full thread",
            "2.5 mm minimum full thread",
            "5 mm minimum full thread",
            5.0,
            "thread minimum full thread cannot exceed drill depth",
        ),
        (
            "tapping drill depth",
            "x 4 mm full-diameter depth",
            "x 5 mm full-diameter depth",
            5.0,
            "manufacturing requirement text disagrees with source cylinder",
        ),
        (
            "tapping drill diameter",
            "DIA 1.6 tapping drill",
            "DIA 1.7 tapping drill",
            1.7,
            "manufacturing requirement text disagrees with source cylinder",
        ),
    ] {
        let b = edit(&a, source, |r| set(r, name, from, to, v));
        let m = draftwright::pmi_lowering::manufacturing(&b);
        assert!(m.owned.iter().all(|o| o.source != source), "{name}");
        assert!(
            m.refused
                .iter()
                .any(|p| p.refs.first().is_some_and(|s| s == source)
                    && p.message.ends_with(&format!("{reason}."))),
            "{name}: {:#?}",
            m.refused
        );
        let plan = model::plan(&b, &Spec::default()).unwrap();
        assert!(refused(&plan, source).is_some(), "{name}");
        assert!(claimed(&plan, &b, source).is_empty(), "{name}");
    }
}
