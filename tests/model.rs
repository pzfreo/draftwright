//! The document model (ADR R1): analyse, plan, edit, render, and the spec carried in the output.

#[path = "common/corpus.rs"]
mod corpus;

use std::io::Read as _;
use std::path::{Path, PathBuf};

use draftwright::api;
use draftwright::model::{self, Spec};
use draftwright::text::Font;
use serde_json::json;

fn part(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts")
        .join(name)
}

fn font() -> Font {
    Font::open(&Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
        .unwrap()
}

#[test]
fn analysis_binds_to_its_file() {
    let (_, a) = model::analyse(&part("turned.step")).unwrap();
    assert_eq!(a.binding.source_sha256.len(), 64);
    assert_eq!(a.binding.part, "turned");
    assert!(a.binding.face_count > 0);
    let (_, b) = model::analyse(&part("prismatic.step")).unwrap();
    assert_ne!(a.binding.source_sha256, b.binding.source_sha256);
}

#[test]
fn every_default_says_why_and_the_views_fit_the_sheet_apart() {
    for name in ["turned.step", "prismatic.step"] {
        let (_, a) = model::analyse(&part(name)).unwrap();
        let plan = model::plan(&a, &Spec::default()).unwrap();
        assert!(plan.sheet.default && !plan.sheet.basis.is_empty());
        assert_eq!(plan.views.len(), 4);
        assert!(plan.views.iter().all(|v| v.default && !v.basis.is_empty()));
        assert_eq!(
            plan.problems
                .iter()
                .map(|p| (p.code.as_str(), p.severity.as_str()))
                .collect::<Vec<_>>(),
            [
                ("general_tolerance_missing", "warning"),
                ("unclaimed_geometry", "info")
            ],
            "a default Spec supplies no manufacturing tolerance; unrecognised interior or curved faces still require review"
        );
        assert_eq!(plan.completeness.claimed_faces, a.stock_faces.len());
        assert_eq!(plan.completeness.faces, a.binding.face_count);
        assert_eq!(
            plan.status,
            draftwright::completeness::Status::NeedsAttention
        );
        // Every view's ink lies inside the border, and no two views' boxes meet.
        let drawing =
            model::render(&model::analyse(&part(name)).unwrap().0, &a, &plan, &font()).unwrap();
        let [w, h] = plan.sheet.page;
        let mut boxes = Vec::new();
        for v in &plan.views {
            let pieces: Vec<[f64; 2]> = drawing.layers[..2]
                .iter()
                .flat_map(|l| l.items.iter())
                .filter_map(|i| match i {
                    draftwright::output::Item::Edge(c)
                    | draftwright::output::Item::TextStroke(c, _)
                    | draftwright::output::Item::FrameText(c, _) => Some(c.start()),
                    _ => None,
                })
                .filter(|p| (p[0] - v.at[0]).abs() < w / 2.0 && (p[1] - v.at[1]).abs() < h / 2.0)
                .collect();
            assert!(!pieces.is_empty());
            boxes.push(v.at);
        }
        for p in drawing.layers[1].items.iter().filter_map(|i| match i {
            draftwright::output::Item::Edge(c)
            | draftwright::output::Item::TextStroke(c, _)
            | draftwright::output::Item::FrameText(c, _) => Some(c.start()),
            _ => None,
        }) {
            assert!(
                p[0] > 10.0 && p[0] < w - 10.0 && p[1] > 10.0 && p[1] < h - 10.0,
                "{p:?} outside the border"
            );
        }
    }
}

#[test]
fn invalid_requests_say_what_would_do() {
    let (_, a) = model::analyse(&part("turned.step")).unwrap();
    let with = |ops: serde_json::Value| model::edit(&Spec::default(), &ops).unwrap();
    let e = model::plan(
        &a,
        &with(serde_json::json!([{ "op": "set", "path": "sheet/scale", "value": "3:1" }])),
    )
    .unwrap_err();
    assert!(e.choices.contains(&"2:1".to_string()), "{e}");
    let e = model::plan(
        &a,
        &with(serde_json::json!([{ "op": "set", "path": "views/top/omit", "value": true }])),
    )
    .unwrap_err();
    assert!(e.choices.contains(&"plan".to_string()), "{e}");
    let all = serde_json::json!(["front", "plan", "side", "iso"].map(
        |v| serde_json::json!({ "op": "set", "path": format!("views/{v}/omit"), "value": true })
    ));
    assert!(model::plan(&a, &with(all)).is_err());
    let e = model::edit(
        &Spec::default(),
        &serde_json::json!([{ "op": "set", "path": "sheet/colour", "value": "red" }]),
    )
    .unwrap_err();
    assert!(e.message.contains("colour"), "{e}");
    let e = model::edit(
        &Spec::default(),
        &serde_json::json!([{ "op": "rename", "path": "sheet/size" }]),
    )
    .unwrap_err();
    assert_eq!(e.choices, ["set", "remove", "add"]);
}

#[test]
fn edits_apply_and_the_spec_travels_in_the_output() {
    let (p, a) = model::analyse(&part("prismatic.step")).unwrap();
    let spec = model::edit(
        &Spec::default(),
        &serde_json::json!([
            { "op": "set", "path": "views/iso/omit", "value": true },
            { "op": "set", "path": "sheet/title/material", "value": "6082-T6 ß ]]>" },
            // Quoting the DXF's marker must not make the SVG or PDF read as a DXF.
            { "op": "set", "path": "sheet/title/title", "value": draftwright::output::DXF_SPEC },
            { "op": "set", "path": "sheet/size", "value": "A3" },
            { "op": "remove", "path": "sheet/size" },
        ]),
    )
    .unwrap();
    assert!(spec.views["iso"].omit && spec.sheet.size.is_none());
    let plan = model::plan(&a, &spec).unwrap();
    assert_eq!(plan.views.len(), 3);
    let drawing = model::render(&p, &a, &plan, &font()).unwrap();
    let pdf = drawing.pdf(&|_| {
        std::fs::read(Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .ok()
    });
    for bytes in [drawing.svg().into_bytes(), drawing.dxf(), pdf] {
        let back = model::embedded_spec(&bytes)
            .unwrap()
            .expect("the spec is embedded");
        assert_eq!(back, plan.spec);
        assert_eq!(
            back.binding.as_ref().unwrap().source_sha256,
            a.binding.source_sha256
        );
    }
}

#[test]
fn the_same_documents_give_the_same_bytes() {
    let run = || {
        let (p, a) = model::analyse(&part("turned.step")).unwrap();
        let plan = model::plan(&a, &Spec::default()).unwrap();
        let d = model::render(&p, &a, &plan, &font()).unwrap();
        (
            serde_json::to_string(&plan).unwrap(),
            d.svg(),
            d.dxf(),
            d.pdf(&|_| None),
        )
    };
    let (a, b) = (run(), run());
    assert!(a.0 == b.0, "plan differs");
    assert!(a.1 == b.1, "svg differs");
    assert!(a.2 == b.2, "dxf differs");
    assert!(a.3 == b.3, "pdf differs");
}

fn scratch(name: &str) -> PathBuf {
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR")).join("model");
    std::fs::create_dir_all(&dir).unwrap();
    dir.join(name)
}

fn codes(problems: &[model::Problem]) -> Vec<&str> {
    problems.iter().map(|p| p.code.as_str()).collect()
}

/// What could not be drawn as planned (the drawing's problems) reaches the problems `review` and
/// `render` answer, after the plan's, and so the command line, the service and the MCP server,
/// which answer through `api::call`. cgb242's front view once had a hidden stretch of no span
/// (`edges_not_drawn`); haecceity now passes over such stretches at source, so its drawing has
/// no problems of its own and the replies carry the plan's alone.
#[test]
fn the_drawings_problems_reach_review_and_render() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let mut step = Vec::new();
    flate2::read::GzDecoder::new(
        std::fs::File::open(dir.join("cadgenbench_inputs/cgb242.step.gz")).unwrap(),
    )
    .read_to_end(&mut step)
    .unwrap();
    let path = scratch("cgb242.step");
    std::fs::write(&path, step).unwrap();
    let step = path.display().to_string();

    let review = api::call("review", &json!({ "step": step })).unwrap();
    let render = api::call(
        "render",
        &json!({ "step": step, "output": scratch("cgb242").display().to_string(),
            "formats": ["svg"] }),
    )
    .unwrap();
    let (part, analysis) = model::analyse(&path).unwrap();
    let plan = model::plan_recovered(&analysis, &Spec::default()).unwrap();
    let drawing = model::render(&part, &analysis, &plan, &font()).unwrap();
    assert!(
        codes(&drawing.problems).is_empty(),
        "{:?}",
        codes(&drawing.problems)
    );
    let expected: Vec<&str> = plan
        .problems
        .iter()
        .chain(&drawing.problems)
        .map(|p| p.id.as_str())
        .collect();
    for reply in [&review, &render] {
        let ids: Vec<&str> = reply["problems"]
            .as_array()
            .unwrap()
            .iter()
            .map(|p| p["id"].as_str().unwrap())
            .collect();
        assert_eq!(ids, expected);
    }
}

/// tests/fixtures/parts/unresolved.step is turned.step with one plane (#148) written as an
/// `OFFSET_SURFACE`, which haecceity does not model: it reads the part, records face 3 as
/// unresolved and refuses to draw it. Every operation that draws answers `failed`, naming the
/// cause, and no file is written; rendering that part under a plan made from the sound file is
/// refused by `model::render`.
#[test]
fn geometry_the_kernel_did_not_resolve_is_refused_as_failed() {
    let step = part("unresolved.step");
    let out = scratch("unresolved");
    for (op, args) in [
        ("analyse", json!({ "step": step })),
        ("plan", json!({ "step": step })),
        ("review", json!({ "step": step })),
        (
            "render",
            json!({ "step": step, "output": out.display().to_string() }),
        ),
    ] {
        let e = api::call(op, &args).unwrap_err();
        assert_eq!(e.code, "failed", "{op}: {e}");
        assert!(
            e.message.contains("did not resolve") && e.message.contains("faces [3]"),
            "{op}: {e}"
        );
    }
    assert!(!out.with_extension("svg").exists());

    let (_, analysis) = model::analyse(&part("turned.step")).unwrap();
    let plan = model::plan(&analysis, &Spec::default()).unwrap();
    let broken = draftwright::haecceity::read_step(&std::fs::read(&step).unwrap()).unwrap();
    let refused = model::render(&broken, &analysis, &plan, &font()).unwrap_err();
    assert_eq!(refused.faces, [3]);
}

/// L2: input that is not what the request should name is `invalid`; input that is, which the
/// kernel or the bridge could not use, is `failed`.
#[test]
fn a_kernel_or_bridge_failure_is_failed_and_bad_input_invalid() {
    let code =
        |step: &Path, features: Option<&Path>| api::analyse(step, features).unwrap_err().code;
    assert_eq!(code(&part("missing.step"), None), "invalid");
    let not_step = scratch("not.step");
    std::fs::write(&not_step, "solid nothing\n").unwrap();
    assert_eq!(code(&not_step, None), "invalid");
    let recognition = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/recognition");
    // Another file's recognition is the caller's mistake.
    assert_eq!(
        code(&part("plate.step"), Some(&recognition.join("turned.json"))),
        "invalid"
    );
    // This file's, with a face the kernel measures differently, is a failure.
    let mut json: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(recognition.join("plate.json")).unwrap())
            .unwrap();
    json["faces"][3]["area"] = 301.0.into();
    let disagreeing = scratch("plate-disagreeing.json");
    std::fs::write(&disagreeing, json.to_string()).unwrap();
    let e = api::analyse(&part("plate.step"), Some(&disagreeing)).unwrap_err();
    assert_eq!(e.code, "failed", "{e}");
    assert!(e.message.contains("face 3 has area"), "{e}");
}

/// M4, ADR R3 invariant 3: across a revision, a feature's ID may name another feature, so the
/// spec's feature-keyed dimension entries are kept but inactive, listed in one warning, and the
/// spec stays bound to its revision; the envelope's entries carry over.
#[test]
fn feature_entries_from_another_revision_are_kept_inactive() {
    let recognition = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/recognition");
    let (_, plate) =
        model::analyse_with(&part("plate.step"), Some(&recognition.join("plate.json"))).unwrap();
    let (_, other) = model::analyse(&part("prismatic.step")).unwrap();
    let hole = "d:f:hole_pattern:f6/bore.diameter";
    let gone = "d:f:hole:9/bore.diameter";
    let envelope = "d:envelope/width.length";
    let spec: Spec = serde_json::from_value(json!({
        "binding": other.binding,
        "dimensions": { hole: { "omit": true }, gone: { "omit": true }, envelope: { "omit": true } },
    }))
    .unwrap();
    let plan = model::plan(&plate, &spec).unwrap();
    let ids: Vec<&str> = plan.dimensions.iter().map(|d| d.id.as_str()).collect();
    assert!(ids.contains(&hole), "the inactive omission is not applied");
    assert!(!ids.contains(&envelope), "the envelope's carries over");
    let dimension = plan.dimensions.iter().find(|d| d.id == hole).unwrap();
    assert!(dimension.default);
    let inactive: Vec<_> = plan
        .problems
        .iter()
        .filter(|p| p.code == "spec_entries_inactive")
        .collect();
    assert_eq!(inactive.len(), 1, "{:?}", plan.problems);
    assert_eq!(inactive[0].severity, "warning");
    assert_eq!(inactive[0].refs, [gone, hole]);
    assert!(codes(&plan.problems).contains(&"spec_from_other_revision"));
    // Kept, and still bound to the revision they were made for.
    assert_eq!(plan.spec.dimensions.len(), 3);
    assert_eq!(plan.spec.binding, Some(other.binding.clone()));

    // In an authored set, an inactive entry does not count as listed, and says so.
    let mut authored = spec.clone();
    authored.dimension_source = model::DimensionSource::Authored;
    authored
        .dimensions
        .values_mut()
        .for_each(|d| d.omit = false);
    let plan = model::plan(&plate, &authored).unwrap();
    let omitted = plan.omitted.iter().find(|o| o.id == hole).unwrap();
    assert!(omitted.basis.starts_with("inactive"), "{}", omitted.basis);
    assert!(plan.dimensions.iter().any(|d| d.id == envelope));

    // Without them, the spec rebinds to this revision.
    let mut rebound = spec.clone();
    rebound.dimensions.retain(|id, _| !id.starts_with("d:f:"));
    let plan = model::plan(&plate, &rebound).unwrap();
    assert!(!codes(&plan.problems).contains(&"spec_entries_inactive"));
    assert_eq!(plan.spec.binding, Some(plate.binding.clone()));
}

/// M5, ISSUE-1190-b: a section the part needs is never dropped silently. nist_ftc_06 needs
/// section A–A; it is not drawn when the plan view that marks its cut is omitted, or when both
/// views of its row are; a spec that asks for a section on a part that has none is told so.
/// The author's own omission of the section needs no saying.
#[test]
fn a_section_not_drawn_says_why() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let (_, analysis) = corpus::analyse(&dir, "nist/nist_ftc_06_asme1_rd.stp", "ftc06");
    let planned = |views: serde_json::Value| {
        let spec: Spec = serde_json::from_value(json!({ "views": views })).unwrap();
        model::plan(&analysis, &spec).unwrap()
    };
    let not_shown = |plan: &model::Plan| {
        plan.problems
            .iter()
            .find(|p| p.code == "section_not_shown")
            .map(|p| p.message.clone())
    };
    let plan = planned(json!({ "plan": { "omit": true } }));
    assert!(plan.views.iter().all(|v| v.id != "v:section_aa"));
    let why = not_shown(&plan).expect("reported");
    assert!(
        why.contains("plan view") && why.contains("views/plan/omit"),
        "{why}"
    );
    let plan = planned(json!({ "front": { "omit": true }, "side": { "omit": true } }));
    let why = not_shown(&plan).expect("reported");
    assert!(why.contains("front and side"), "{why}");
    assert_eq!(not_shown(&planned(json!({}))), None);
    assert_eq!(
        not_shown(&planned(json!({ "section_aa": { "omit": true } }))),
        None
    );

    let (_, plate) = model::analyse(&part("plate.step")).unwrap();
    let spec: Spec = serde_json::from_value(json!({ "views": { "section_aa": {} } })).unwrap();
    let plan = model::plan(&plate, &spec).unwrap();
    let why = not_shown(&plan).expect("reported");
    assert!(why.contains("no front-facing cutting plane"), "{why}");
    assert!(why.contains("remaining drawing are retained"), "{why}");
}

/// M6: `remove` of a path that is not there is refused, and makes nothing on its way.
#[test]
fn removing_what_is_not_there_is_refused() {
    for path in [
        "views/iso/omit",
        "sheet/size",
        "dimensions/d:envelope~1width.length",
    ] {
        let e =
            model::edit(&Spec::default(), &json!([{ "op": "remove", "path": path }])).unwrap_err();
        assert!(e.message.contains("nothing at"), "{path}: {e}");
    }
    let spec = model::edit(
        &Spec::default(),
        &json!([{ "op": "set", "path": "views/iso/omit", "value": true }]),
    )
    .unwrap();
    let e = model::edit(
        &spec,
        &json!([{ "op": "remove", "path": "views/plan/omit" }]),
    )
    .unwrap_err();
    assert!(e.message.contains("views/plan/omit"), "{e}");
    let back = model::edit(
        &spec,
        &json!([{ "op": "remove", "path": "views/iso/omit" }]),
    )
    .unwrap();
    assert!(!back.views["iso"].omit && !back.views.contains_key("plan"));
}

/// M6: the spec travels intact through every format whatever it holds (markup, a CDATA end,
/// characters outside ASCII); the format is told by the file's signature; a spec that does not
/// read is an error, not "no spec".
#[test]
fn the_embedded_spec_reads_back_exactly_or_is_refused() {
    let (p, a) = model::analyse(&part("prismatic.step")).unwrap();
    let awkward =
        "</draftwright:spec> <![CDATA[ ]]> ]]]]><![CDATA[> & &amp; < > \u{00df} \u{2013} \u{1f600}";
    let spec = model::edit(
        &Spec::default(),
        &json!([{ "op": "set", "path": "sheet/title/material", "value": awkward }]),
    )
    .unwrap();
    let plan = model::plan(&a, &spec).unwrap();
    let drawing = model::render(&p, &a, &plan, &font()).unwrap();
    let svg = drawing.svg();
    // The JSON carries no markup: one CDATA section holds it all.
    let i = svg.find("<![CDATA[").unwrap() + "<![CDATA[".len();
    let body = &svg[i..i + svg[i..].find("]]>").unwrap()];
    assert!(svg[i + body.len()..].starts_with("]]></draftwright:spec>"));
    assert!(!body.contains(['<', '>', '&']), "{body}");
    let pdf = drawing.pdf(&|_| None);
    for bytes in [svg.clone().into_bytes(), drawing.dxf(), pdf] {
        let back = model::embedded_spec(&bytes).unwrap().unwrap();
        assert_eq!(back.sheet.title["material"], awkward);
        assert_eq!(back, plan.spec);
    }
    // An editor's re-save without CDATA still reads.
    let i = svg.find("<![CDATA[").unwrap();
    let j = svg.find("]]></draftwright:spec>").unwrap();
    let json = &svg[i + "<![CDATA[".len()..j];
    let escaped = format!(
        "{}{}{}",
        &svg[..i],
        json.replace('&', "&amp;").replace('<', "&lt;"),
        &svg[j + "]]>".len()..]
    );
    assert_eq!(
        model::embedded_spec(escaped.as_bytes()).unwrap().unwrap(),
        plan.spec
    );

    // A corrupt spec is an error; a drawing without one is none; a file that is no drawing is
    // an error.
    let corrupt = svg.replacen("\"schema\"", "\"schema", 1);
    assert!(model::embedded_spec(corrupt.as_bytes()).is_err());
    let mut bare = drawing.clone();
    bare.spec = None;
    assert_eq!(model::embedded_spec(bare.svg().as_bytes()), Ok(None));
    assert_eq!(model::embedded_spec(&bare.dxf()), Ok(None));
    assert_eq!(model::embedded_spec(&bare.pdf(&|_| None)), Ok(None));
    assert!(model::embedded_spec(b"solid nothing\n").is_err());
    let e = api::spec(&part("turned.step")).unwrap_err();
    assert_eq!(e.code, "invalid");
}

/// L2: the format's extension is appended to the output's name, so two revisions' stems do not
/// write the same files; a drawing format's extension already on it is replaced, as
/// draftwright's `_output_prefix` does.
#[test]
fn render_appends_the_formats_extension() {
    let files = vec![("svg".to_string(), b"<svg/>".to_vec())];
    for (out, written) in [
        ("part.rev2", "part.rev2.svg"),
        ("part.rev3", "part.rev3.svg"),
        ("part.svg", "part.svg"),
        ("part.pdf", "part.svg"),
    ] {
        let paths = api::write(&scratch(out), &files).unwrap();
        assert_eq!(paths, [scratch(written).display().to_string()]);
    }
}

#[test]
fn projections_kept_across_edits_draw_what_a_fresh_render_draws() {
    for name in ["turned.step", "prismatic.step"] {
        let (part, analysis) = model::analyse(&part(name)).unwrap();
        let mut projections = model::Projections::default();
        let mut spec = Spec::default();
        for edit in [None, Some("omit"), Some("a2")] {
            if let Some(edit) = edit {
                let plan = model::plan(&analysis, &spec).unwrap();
                let op = match edit {
                    "omit" => json!({ "op": "set", "value": true,
                        "path": format!("dimensions/{}/omit",
                            plan.dimensions[0].id.replace('~', "~0").replace('/', "~1")) }),
                    _ => json!({ "op": "set", "path": "sheet/size", "value": "A2" }),
                };
                spec = api::edit(&spec, &json!([op])).unwrap();
            }
            let plan = model::plan(&analysis, &spec).unwrap();
            let fresh = model::render(&part, &analysis, &plan, &font()).unwrap();
            let kept =
                model::render_with(&part, &analysis, &plan, &font(), &mut projections).unwrap();
            assert_eq!(fresh.svg(), kept.svg(), "{name} after {edit:?}");
        }
    }
}

#[test]
fn analysing_step_bytes_matches_analysing_the_file() {
    // A browser has the file's bytes and no file system: the analysis from the bytes must be the
    // one from the file, without features and with Rust quiddity's.
    for name in ["turned.step", "prismatic.step"] {
        let path = part(name);
        let bytes = std::fs::read(&path).unwrap();
        let json = |a: &model::Analysis| serde_json::to_string(a).unwrap();
        let (_, from_file) = model::analyse(&path).unwrap();
        let (_, from_bytes) = model::analyse_step(&path, &bytes).unwrap();
        assert_eq!(
            json(&from_file),
            json(&from_bytes),
            "{name} without features"
        );
        let (_, from_file) = model::analyse_native(&path).unwrap();
        let (_, from_bytes) = model::analyse_native_step(&path, &bytes).unwrap();
        assert_eq!(
            json(&from_file),
            json(&from_bytes),
            "{name} with native features"
        );
    }
}

/// A surface model read beside the solid (#215: STC-07's CATIA fills, bounded by sketch
/// curves) is construction geometry: the envelope, the overall dimensions, the stock faces and
/// the edge ink come from the solid alone. The plate gains a triangle 7.5 mm above its top.
#[test]
fn a_surface_model_beside_the_solid_is_not_the_part() {
    let source = std::fs::read_to_string(part("plate.step")).unwrap();
    let fill = "#900=CARTESIAN_POINT('',(0.,0.,10.));
#901=CARTESIAN_POINT('',(5.,0.,10.));
#902=CARTESIAN_POINT('',(0.,5.,10.));
#903=VERTEX_POINT('',#900);
#904=VERTEX_POINT('',#901);
#905=VERTEX_POINT('',#902);
#906=DIRECTION('',(1.,0.,0.));
#907=DIRECTION('',(-0.707106781187,0.707106781187,0.));
#908=DIRECTION('',(0.,-1.,0.));
#909=VECTOR('',#906,1.);
#910=VECTOR('',#907,1.);
#911=VECTOR('',#908,1.);
#912=LINE('',#900,#909);
#913=LINE('',#901,#910);
#914=LINE('',#902,#911);
#915=EDGE_CURVE('',#903,#904,#912,.T.);
#916=EDGE_CURVE('',#904,#905,#913,.T.);
#917=EDGE_CURVE('',#905,#903,#914,.T.);
#918=ORIENTED_EDGE('',*,*,#915,.T.);
#919=ORIENTED_EDGE('',*,*,#916,.T.);
#920=ORIENTED_EDGE('',*,*,#917,.T.);
#921=EDGE_LOOP('',(#918,#919,#920));
#922=FACE_OUTER_BOUND('',#921,.T.);
#923=DIRECTION('',(0.,0.,1.));
#924=AXIS2_PLACEMENT_3D('',#900,#923,#906);
#925=PLANE('',#924);
#926=ADVANCED_FACE('Fill.1',(#922),#925,.T.);
#927=OPEN_SHELL('Fill.1',(#926));
#928=SHELL_BASED_SURFACE_MODEL('',(#927));
#929=MANIFOLD_SURFACE_SHAPE_REPRESENTATION('',(#928),#713);
#930=SHAPE_REPRESENTATION_RELATIONSHIP('','',#10,#929);";
    let source = source.replace(
        "ENDSEC;\nEND-ISO-10303-21;",
        &format!("{fill}\nENDSEC;\nEND-ISO-10303-21;"),
    );
    let path = scratch("plate_with_fill.step");
    std::fs::write(&path, source).unwrap();
    let (_, plain) = model::analyse(&part("plate.step")).unwrap();
    let (_, filled) = model::analyse(&path).unwrap();
    // The fill is read (one face more), but it is not the part.
    assert_eq!(filled.binding.face_count, plain.binding.face_count + 1);
    assert_eq!(filled.solid_count, Some(1));
    assert_eq!(filled.envelope, plain.envelope);
    assert_eq!(filled.stock_faces, plain.stock_faces);
    assert_eq!(filled.principal_profiles, plain.principal_profiles);
    let (ink, plain_ink) = (
        filled.edge_ink.as_ref().unwrap(),
        plain.edge_ink.as_ref().unwrap(),
    );
    assert_eq!(ink.planes, plain_ink.planes);
    assert_eq!(ink.curved, plain_ink.curved);
    // The fill's edges keep their indices, with no ink.
    assert_eq!(ink.edges.len(), plain_ink.edges.len() + 3);
    assert_eq!(ink.edges[..plain_ink.edges.len()], plain_ink.edges[..]);
    assert!(ink.edges[plain_ink.edges.len()..].iter().all(Vec::is_empty));
    // The overall dimensions state the plate, not the plate and its fill.
    let overall = |a: &model::Analysis| {
        let plan = model::plan(a, &Spec::default()).unwrap();
        let mut d: Vec<(String, f64)> = plan
            .dimensions
            .iter()
            .filter(|d| d.id.starts_with("d:envelope/"))
            .map(|d| (d.id.clone(), d.value))
            .collect();
        d.sort_by(|a, b| a.0.cmp(&b.0));
        d
    };
    let (plain_overall, filled_overall) = (overall(&plain), overall(&filled));
    assert!(!plain_overall.is_empty());
    assert_eq!(filled_overall, plain_overall);
    std::fs::remove_file(path).unwrap();
}
