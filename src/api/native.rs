use super::*;
use crate::haecceity::Part;
use crate::lint::{self, LintIssue};
use crate::model::{ANALYSIS_SCHEMA, Analysis, Plan, Problem, Spec};
use crate::output::Drawing;
use crate::text::Font;
use serde_json::{Value, json};
use std::path::Path;

/// Drawing extensions replaced on an output path, DXF included.
const EXTENSIONS: [&str; 4] = ["svg", "dxf", "pdf", "png"];

/// Where an analysis takes the part's features from.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Features<'a> {
    /// None: no dimensions, sections or lint that need features.
    None,
    /// Python quiddity's recognition JSON for the same file ([`crate::bridge`]).
    Python(&'a Path),
    /// Rust quiddity's recognition, run on the part ([`model::analyse_native`]). Refused when
    /// its document is refused; the families it does not give are in the Analysis's
    /// `unrecognised` and the plan's problems.
    Native,
}

impl<'a> From<Option<&'a Path>> for Features<'a> {
    fn from(features: Option<&'a Path>) -> Features<'a> {
        features.map_or(Features::None, Features::Python)
    }
}

/// The part at *step* (part *part* of an assembly alone, when given) and its analysis, with its
/// features from *features*.
pub(crate) fn analysed(
    step: &Path,
    part: Option<usize>,
    features: Features,
) -> Result<(Part, Analysis), Error> {
    Ok(match features {
        Features::None => model::analyse_with_part(step, None, part)?,
        Features::Python(json) => model::analyse_with_part(step, Some(json), part)?,
        Features::Native if part.is_none() => {
            crate::plan_cache::analyse_native_in(crate::plan_cache::dir().as_deref(), step)?
        }
        Features::Native => model::analyse_native_part(step, part)?,
    })
}

/// The distinct parts of the STEP file at *step*, in the order a part is chosen by
/// ([`model::analyse_step_part`]): each one's index, name, product id, face count and number of
/// placements.
pub fn parts(step: &Path) -> Result<Vec<model::FilePart>, Error> {
    let bytes = model::read_step_file(step).map_err(Error::invalid)?;
    Ok(model::parts(step, &bytes)?)
}

/// The Analysis of the STEP file at *step*, with the features in *features*, Python quiddity's
/// recognition JSON for the same file ([`crate::bridge`]), when given.
pub fn analyse(step: &Path, features: Option<&Path>) -> Result<Analysis, Error> {
    analyse_from(step, features.into())
}

/// The Analysis of the STEP file at *step*, with its features from *features*.
pub fn analyse_from(step: &Path, features: Features) -> Result<Analysis, Error> {
    analyse_part(step, None, features)
}

/// [`analyse_from`] of part *part* of an assembly alone, when given.
pub fn analyse_part(
    step: &Path,
    part: Option<usize>,
    features: Features,
) -> Result<Analysis, Error> {
    Ok(analysed(step, part, features)?.1)
}

/// The Plan for the STEP file at *step* (with *features*, as for [`analyse`]) under *spec*.
pub fn plan(step: &Path, features: Option<&Path>, spec: &Spec) -> Result<Plan, Error> {
    plan_from(step, features.into(), spec)
}

/// The Plan for the STEP file at *step*, with its features from *features*, under *spec*.
pub fn plan_from(step: &Path, features: Features, spec: &Spec) -> Result<Plan, Error> {
    plan_part(step, None, features, spec)
}

/// [`plan_from`] of part *part* of an assembly alone, when given.
pub fn plan_part(
    step: &Path,
    part: Option<usize>,
    features: Features,
    spec: &Spec,
) -> Result<Plan, Error> {
    let (_, analysis) = analysed(step, part, features)?;
    Ok(planned(&analysis, spec)?)
}

/// The Plan for *analysis* (made by [`analyse`]) under *spec*. No STEP file is read; a document
/// of a version [`model::plan`] does not read is refused. Every plan here has draftwright's
/// layout recovery applied ([`model::plan_recovered`]).
pub fn plan_analysed(analysis: &Analysis, spec: &Spec) -> Result<Plan, Error> {
    Ok(planned(analysis, spec)?)
}

/// [`model::plan_recovered`], through the plan cache that `DRAFTWRIGHT_PLAN_CACHE` names, when
/// set ([`crate::plan_cache`]).
fn planned(analysis: &Analysis, spec: &Spec) -> Result<Plan, model::Invalid> {
    crate::plan_cache::plan_recovered_in(crate::plan_cache::dir().as_deref(), analysis, spec)
}

/// Where an operation that draws takes the part's Analysis from.
#[derive(Clone, Copy)]
enum Given<'a> {
    /// Analysed from the STEP file (one part of it alone, when given), with its features from
    /// these.
    Features(Features<'a>, Option<usize>),
    /// The caller's, checked against the STEP file.
    Analysis(&'a Analysis),
}

/// The part at *step* and its analysis, as *given*.
fn read(step: &Path, given: Given) -> Result<(Part, Analysis), Error> {
    match given {
        Given::Features(features, part) => analysed(step, part, features),
        Given::Analysis(analysis) => Ok((bound(step, analysis)?, analysis.clone())),
    }
}

/// Whether *analysis* was made from the *part* a request names: any, when it names none. Part 0
/// of a file of one part is the whole file, which its analysis binds (no `part_index`).
fn made_of_part(analysis: &Analysis, part: Option<usize>) -> bool {
    part.is_none()
        || part == analysis.binding.part_index
        || (part == Some(0) && analysis.binding.part_index.is_none() && analysis.parts.is_empty())
}

/// The part at *step*, read without recognition, when *analysis* is of this version and was made
/// from that very file: one part is never drawn with another's analysis. The part of an assembly
/// it was made from alone (`binding.part_index`) is read alone again.
fn bound(step: &Path, analysis: &Analysis) -> Result<Part, Error> {
    if analysis.schema != ANALYSIS_SCHEMA {
        return Err(Error {
            choices: vec![ANALYSIS_SCHEMA.to_string()],
            ..Error::invalid(format!(
                "The analysis is {:?}, which this draftwright does not draw from; analyse {} again.",
                analysis.schema,
                step.display()
            ))
        });
    }
    let (part, read) = model::analyse_with_part(step, None, analysis.binding.part_index)?;
    let (given, found) = (&analysis.binding, &read.binding);
    if given.source_sha256 != found.source_sha256 || given.face_count != found.face_count {
        return Err(Error::invalid(format!(
            "The analysis was made from another file (sha256 {}, {} faces), not {} (sha256 {}, \
             {} faces); analyse {} and pass that analysis, or omit it.",
            given.source_sha256,
            given.face_count,
            step.display(),
            found.source_sha256,
            found.face_count,
            step.display()
        )));
    }
    Ok(part)
}

/// *spec* with the edit operations *ops* applied.
pub fn edit(spec: &Spec, ops: &Value) -> Result<Spec, Error> {
    Ok(model::edit(spec, ops)?)
}

/// The analysis, the plan and the drawing of the STEP file at *step* under *spec*. The plan's
/// problems include the drawing's: what could not be drawn as planned.
fn drawn(step: &Path, given: Given, spec: &Spec) -> Result<(Analysis, Plan, Vec<Drawing>), Error> {
    let (part, analysis) = read(step, given)?;
    let mut plan = planned(&analysis, spec)?;
    let font = Font::new(MONO.to_vec()).expect("the bundled font parses");
    let drawings = model::render_sheets(&part, &analysis, &plan, &font).map_err(|e| {
        Error::failed(format!(
            "cannot draw {}: the kernel did not resolve its {e} on reading",
            step.display()
        ))
    })?;
    plan.problems
        .extend(drawings.iter().flat_map(|d| d.problems.iter().cloned()));
    plan.status = crate::completeness::status(&plan.problems);
    Ok((analysis, plan, drawings))
}

/// The plan (its problems with the drawing's), and the drawing in each of *formats* (from
/// [`FORMATS`]) as bytes, each carrying the spec; a PNG at [`DPI`].
pub fn render(
    step: &Path,
    features: Option<&Path>,
    spec: &Spec,
    formats: &[&str],
) -> Result<(Plan, Files), Error> {
    render_at(step, features, spec, formats, DPI)
}

/// [`render`], with a PNG at *dpi* pixels per inch: refused, as draftwright refuses it, when a
/// PNG is asked for and *dpi* is not positive.
pub fn render_at(
    step: &Path,
    features: Option<&Path>,
    spec: &Spec,
    formats: &[&str],
    dpi: f64,
) -> Result<(Plan, Files), Error> {
    render_from(step, features.into(), spec, formats, dpi)
}

/// [`render_at`], with the part's features from *features*.
pub fn render_from(
    step: &Path,
    features: Features,
    spec: &Spec,
    formats: &[&str],
    dpi: f64,
) -> Result<(Plan, Files), Error> {
    render_part(step, None, features, spec, formats, dpi)
}

/// [`render_from`] of part *part* of an assembly alone, when given.
pub fn render_part(
    step: &Path,
    part: Option<usize>,
    features: Features,
    spec: &Spec,
    formats: &[&str],
    dpi: f64,
) -> Result<(Plan, Files), Error> {
    rendered(step, Given::Features(features, part), spec, formats, dpi)
}

/// [`render_at`] from *analysis* (made by [`analyse`] from the STEP file at *step*) in place of
/// analysing it again: refused when *analysis* is of another version, or was made from another
/// file (its binding's SHA-256 or face count differs).
pub fn render_analysed(
    step: &Path,
    analysis: &Analysis,
    spec: &Spec,
    formats: &[&str],
    dpi: f64,
) -> Result<(Plan, Files), Error> {
    rendered(step, Given::Analysis(analysis), spec, formats, dpi)
}

fn rendered(
    step: &Path,
    given: Given,
    spec: &Spec,
    formats: &[&str],
    dpi: f64,
) -> Result<(Plan, Files), Error> {
    if formats.contains(&"dxf") {
        return Err(Error {
            choices: FORMATS.map(String::from).to_vec(),
            ..Error::invalid("DXF export is disabled until it is fixed.".to_string())
        });
    }
    if let Some(other) = formats.iter().find(|f| !FORMATS.contains(f)) {
        return Err(Error {
            choices: FORMATS.map(String::from).to_vec(),
            ..Error::invalid(format!("{other:?} is not a format."))
        });
    }
    if formats.contains(&"png") && !(dpi > 0.0 && dpi.is_finite()) {
        return Err(Error::invalid(format!(
            "png export needs dpi > 0, got {dpi}"
        )));
    }
    let (_, plan, drawings) = drawn(step, given, spec)?;
    let mut files = Vec::new();
    for &format in formats {
        if format == "pdf" {
            files.push((
                format.into(),
                Drawing::pdf_pages(&drawings, &|name| match name {
                    "IBMPlexMono-Regular.ttf" => Some(MONO.to_vec()),
                    crate::title_block::FONT => Some(crate::title_block::FONT_DATA.to_vec()),
                    _ => None,
                }),
            ));
        } else {
            for (i, drawing) in drawings.iter().enumerate() {
                let suffix = if i == 0 {
                    format.into()
                } else {
                    format!("sheet{}.{format}", i + 1)
                };
                let bytes = if format == "svg" {
                    drawing.svg().into_bytes()
                } else {
                    drawing.png(dpi).map_err(Error::invalid)?
                };
                files.push((suffix, bytes));
            }
        }
    }
    Ok((plan, files))
}

/// Writes each rendered file at *out* with its format's extension appended (`part.rev2` →
/// `part.rev2.svg`; an *out* already ending in a drawing format's extension has it replaced, as
/// draftwright's `_output_prefix` does), and returns the paths written. Each is written to a
/// uniquely and exclusively created sibling, then atomically replaces its destination. A failed
/// write/rename does not replace that destination and removes only this invocation's staged copy
/// (ADR5-10). Concurrent writers use last-writer-wins per file (the last successful rename).
/// A multi-format export is not transactional: formats may come from different writers, and a
/// later failure leaves already published formats in place.
pub fn write(out: &Path, files: &[(String, Vec<u8>)]) -> Result<Vec<String>, Error> {
    let stem = match out.extension().and_then(|e| e.to_str()) {
        Some(e) if EXTENSIONS.contains(&e) => out.with_extension(""),
        _ => out.to_path_buf(),
    };
    let named = |suffix: &str| {
        let mut name = stem.clone().into_os_string();
        name.push(format!(".{suffix}"));
        std::path::PathBuf::from(name)
    };
    let mut written = Vec::new();
    for (format, bytes) in files {
        let path = named(format);
        write_file(&path, |file| {
            use std::io::Write;
            file.write_all(bytes)
        })?;
        written.push(path.display().to_string());
    }
    Ok(written)
}

static EXPORT_STAGED: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

fn write_file(
    path: &Path,
    write: impl FnOnce(&mut std::fs::File) -> std::io::Result<()>,
) -> Result<(), Error> {
    let (staged, mut file) = loop {
        let staged = path.with_file_name(format!(
            ".draftwright-export.{}.{}.part",
            std::process::id(),
            EXPORT_STAGED.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
        ));
        match std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&staged)
        {
            Ok(file) => break (staged, file),
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(e) => {
                return Err(Error::failed(format!(
                    "cannot write {}: {e}",
                    staged.display()
                )));
            }
        }
    };
    let discard = || {
        let _ = std::fs::remove_file(&staged);
    };
    let result = write(&mut file);
    drop(file);
    result.map_err(|e| {
        discard();
        Error::failed(format!("cannot write {}: {e}", staged.display()))
    })?;
    std::fs::rename(&staged, path).map_err(|e| {
        discard();
        Error::failed(format!("cannot write {}: {e}", path.display()))
    })
}

/// A drawing to look at: the plan rendered as SVG, with its problems and lint's findings on the
/// drawing.
#[derive(Clone, Debug, Serialize)]
pub struct Review {
    /// Counts across problems, lint and structural, each finding counted once.
    pub summary: crate::finding::Summary,
    pub svg: String,
    /// Physical holes whose bore sizes actually reached these rendered sheets.
    pub drawing_consumer: crate::evaluation::DrawingConsumer,
    /// SVGs for sheets 2..m, in document order.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub sheets: Vec<String>,
    /// The plan's problems, and the drawing's: what could not be drawn as planned.
    pub problems: Vec<Problem>,
    /// What lint finds on the rendered drawing, judged against the part's analysis alone
    /// ([`crate::lint`]): e.g. a hole whose sizes the sheet does not state.
    pub lint: Vec<LintIssue>,
    /// What lint's structural checks find on the drawn sheet ([`crate::lint::structural`]):
    /// e.g. ink past the border. draftwright's lint runs them first, in the same list; they are
    /// kept apart here so `lint` stays coverage alone.
    pub structural: Vec<LintIssue>,
}

/// The drawing of the STEP file at *step* under *spec*, as SVG text, with its problems and
/// lint's findings.
pub fn review(step: &Path, features: Option<&Path>, spec: &Spec) -> Result<Review, Error> {
    review_from(step, features.into(), spec)
}

/// [`review`], with the part's features from *features*.
pub fn review_from(step: &Path, features: Features, spec: &Spec) -> Result<Review, Error> {
    review_part(step, None, features, spec)
}

/// [`review_from`] of part *part* of an assembly alone, when given.
pub fn review_part(
    step: &Path,
    part: Option<usize>,
    features: Features,
    spec: &Spec,
) -> Result<Review, Error> {
    reviewed(step, Given::Features(features, part), spec)
}

/// [`review`] from *analysis* in place of analysing the STEP file again, refused as
/// [`render_analysed`] refuses it.
pub fn review_analysed(step: &Path, analysis: &Analysis, spec: &Spec) -> Result<Review, Error> {
    reviewed(step, Given::Analysis(analysis), spec)
}

fn reviewed(step: &Path, given: Given, spec: &Spec) -> Result<Review, Error> {
    let (analysis, plan, drawings) = drawn(step, given, spec)?;
    let drawing = &drawings[0];
    // Lint judges the drawing against the requirements the plan drew from: the file's PMI with
    // the spec's datums and controls merged in ([`crate::spec_gdt`]); the plan accepted them.
    let merged = crate::spec_gdt::effective(&analysis, &plan.spec).ok();
    let analysis = merged.as_deref().unwrap_or(&analysis);
    // Preserve the existing rendering/evaluation order; labels are bookkeeping afterward.
    let svg = drawing.svg();
    let drawing_consumer = crate::evaluation::drawing_consumer(analysis, Ok(&drawings));
    let sheets = drawings.iter().skip(1).map(Drawing::svg).collect();
    let lint = lint::lint_sheets(analysis, &drawings);
    let structural = lint::structural::check_sheets(&drawings);
    let summary = crate::finding::Summary::new(
        plan.problems
            .iter()
            .map(Problem::labels)
            .chain(lint.iter().chain(&structural).map(LintIssue::labels)),
    );
    Ok(Review {
        summary,
        svg,
        drawing_consumer,
        sheets,
        problems: plan.problems,
        lint,
        structural,
    })
}

/// The spec embedded in the drawing (SVG, DXF, PDF or PNG) at *drawing*.
pub fn spec(drawing: &Path) -> Result<Spec, Error> {
    let bytes = std::fs::read(drawing)
        .map_err(|e| Error::invalid(format!("cannot read {}: {e}", drawing.display())))?;
    model::embedded_spec(&bytes)
        .map_err(|e| {
            Error::invalid(format!(
                "cannot read the spec in {}: {e}",
                drawing.display()
            ))
        })?
        .ok_or_else(|| Error::invalid(format!("{} carries no draftwright spec", drawing.display())))
}

/// Operation *op* with JSON arguments *args*, its result as JSON:
///
/// | op | arguments | result |
/// | --- | --- | --- |
/// | `analyse` | `step`, `features`? | the Analysis |
/// | `plan` | `step` and `features`?, or `analysis` (and `step`?); `spec`? | the Plan |
/// | `edit` | `spec`, `ops` | the edited Spec |
/// | `render` | `step`, `features`? or `analysis`?, `spec`?, `output`, `formats`?, `dpi`? | `{written, problems}` |
/// | `review` | `step`, `features`? or `analysis`?, `spec`? | `{svg, problems, lint}` |
/// | `spec` | `drawing` | the Spec embedded in the drawing |
/// | `parts` | `step` | `{parts}`: the file's distinct parts ([`parts`]) |
///
/// `analyse`, `plan`, `render` and `review` also take `part`, the index of one part of an
/// assembly (as `parts` lists them) to draw alone, in its own frame and with its PMI only.
/// Without it the whole file is drawn as one part, and an assembly's plan warns
/// (`assembly_drawn_whole`), unless every body coincides with one part's (`coincident_bodies`)
/// or lies within the largest body's box (`assembly_principal_drawn`), when that part is drawn
/// alone. An `analysis` already names its part (`binding.part_index`): a
/// `part` beside it must be the same.
///
/// `step`, `features`, `output` and `drawing` are file paths; `features` is Python quiddity's
/// recognition JSON for the STEP file ([`crate::bridge`]). `analysis` is the Analysis document
/// itself, as `analyse` returned it, in place of analysing `step` again (so not beside
/// `features`, which it already carries): `plan` then needs no `step`, and when `step` is given
/// the analysis must have been made from it, as for `render` and `review`. An absent `spec` is
/// the default one; absent `formats` are [`DEFAULT_FORMATS`], and an absent `dpi` (the PNG's) is
/// [`DPI`]. A panic inside an operation is answered as `failed`, so a long-lived adapter outlives
/// one bad part.
pub fn call(op: &str, args: &Value) -> Result<Value, Error> {
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| dispatch(op, args))).unwrap_or_else(
        |panic| {
            let why = panic
                .downcast_ref::<&str>()
                .map(|s| s.to_string())
                .or_else(|| panic.downcast_ref::<String>().cloned())
                .unwrap_or_default();
            Err(Error::failed(format!("{op} stopped: {why}")))
        },
    )
}

fn dispatch(op: &str, args: &Value) -> Result<Value, Error> {
    if matches!(op, "spec_schema" | "item_options") {
        // These discovery operations share MCP's published argument and result shapes.
        // Service envelopes are transport metadata, never tool arguments.
        let mut args = args.get("arguments").unwrap_or(args).clone();
        if let Some(fields) = args.as_object_mut() {
            fields.remove("op");
            if op == "spec_schema" {
                fields.remove("id");
            }
        }
        return crate::mcp::call(&crate::stateless::Cache::environment(), op, &args);
    }
    let text = |key: &str| {
        args.get(key)
            .and_then(Value::as_str)
            .map(Path::new)
            .ok_or_else(|| Error::invalid(format!("{op} needs {key:?}, a file path")))
    };
    let features = match args.get("features") {
        None | Some(Value::Null) => None,
        Some(Value::String(path)) => Some(Path::new(path.as_str())),
        Some(_) => {
            return Err(Error::invalid(format!(
                "{op}'s \"features\" is a file path"
            )));
        }
    };
    let given = || -> Result<Spec, Error> {
        match args.get("spec") {
            None | Some(Value::Null) => Ok(Spec::default()),
            Some(s) => serde_json::from_value(s.clone())
                .map_err(|e| Error::invalid(format!("the spec is not valid: {e}"))),
        }
    };
    let part = request_part(op, args)?;
    // The caller's Analysis, if given: only in place of features, which it already carries, and
    // of a part, which it names.
    let analysis = || -> Result<Option<Analysis>, Error> {
        let analysis: Option<Analysis> = match args.get("analysis") {
            None | Some(Value::Null) => None,
            Some(_) if features.is_some() => {
                return Err(Error::invalid(format!(
                    "{op} takes \"analysis\" or \"features\", not both: the analysis already \
                     carries the features it was made with."
                )));
            }
            Some(a) => Some(
                serde_json::from_value(a.clone())
                    .map_err(|e| Error::invalid(format!("the analysis is not valid: {e}")))?,
            ),
        };
        if let Some(a) = &analysis
            && !made_of_part(a, part)
        {
            return Err(Error::invalid(format!(
                "{op}'s \"part\" ({}) is not the part the analysis was made from ({}); omit \
                 \"part\", or analyse that part again.",
                part.map_or("none".into(), |k| k.to_string()),
                a.binding
                    .part_index
                    .map_or("the whole file".into(), |k| format!("part {k}"))
            )));
        }
        Ok(analysis)
    };
    let features = Features::from(features);
    match op {
        "analyse" => Ok(json_of(&analyse_part(text("step")?, part, features)?)),
        "parts" => Ok(json!({ "parts": parts(text("step")?)? })),
        "plan" => match analysis()? {
            Some(analysis) => {
                if args.get("step").is_some_and(|s| !s.is_null()) {
                    bound(text("step")?, &analysis)?;
                }
                Ok(json_of(&plan_analysed(&analysis, &given()?)?))
            }
            None => Ok(json_of(&plan_part(
                text("step")?,
                part,
                features,
                &given()?,
            )?)),
        },
        "edit" => {
            let ops = args
                .get("ops")
                .ok_or_else(|| Error::invalid("edit needs \"ops\", a list of operations"))?;
            Ok(json_of(&edit(&given()?, ops)?))
        }
        "render" => {
            let formats: Vec<&str> = match args.get("formats") {
                None | Some(Value::Null) => DEFAULT_FORMATS.to_vec(),
                Some(Value::Array(list)) => list
                    .iter()
                    .map(|f| {
                        f.as_str()
                            .ok_or_else(|| Error::invalid("\"formats\" is a list of format names"))
                    })
                    .collect::<Result<_, _>>()?,
                Some(_) => return Err(Error::invalid("\"formats\" is a list of format names")),
            };
            let dpi = match args.get("dpi") {
                None | Some(Value::Null) => DPI,
                Some(Value::Number(n)) => n.as_f64().unwrap_or(f64::NAN),
                Some(_) => return Err(Error::invalid("\"dpi\" is a number of pixels per inch")),
            };
            let output = text("output")?;
            let (plan, files) = match analysis()? {
                Some(a) => render_analysed(text("step")?, &a, &given()?, &formats, dpi)?,
                None => render_part(text("step")?, part, features, &given()?, &formats, dpi)?,
            };
            Ok(json!({ "written": write(output, &files)?, "problems": plan.problems }))
        }
        "review" => Ok(json_of(&match analysis()? {
            Some(a) => review_analysed(text("step")?, &a, &given()?)?,
            None => review_part(text("step")?, part, features, &given()?)?,
        })),
        "spec" => Ok(json_of(&spec(text("drawing")?)?)),
        other => Err(Error {
            choices: OPERATIONS.map(String::from).to_vec(),
            ..Error::invalid(format!("{other:?} is not an operation."))
        }),
    }
}

fn json_of(document: &impl Serialize) -> Value {
    serde_json::to_value(document).expect("a document serialises")
}

/// A request's `part`: the index of one part of an assembly, or none.
pub(crate) fn request_part(op: &str, args: &Value) -> Result<Option<usize>, Error> {
    match args.get("part") {
        None | Some(Value::Null) => Ok(None),
        Some(v) => v
            .as_u64()
            .and_then(|k| usize::try_from(k).ok())
            .map(Some)
            .ok_or_else(|| {
                Error::invalid(format!(
                    "{op}'s \"part\" is a part's index (0, 1, …), as the parts operation lists them"
                ))
            }),
    }
}

/// Additional operations on the connection's one live part; batch operations still work.
pub const SESSION_OPERATIONS: [&str; 8] = [
    "open", "apply", "undo", "redo", "item", "items", "problems", "options",
];

#[derive(Default)]
pub struct Service {
    session: Option<crate::session::Session>,
}
impl Service {
    pub fn call(&mut self, op: &str, args: &Value) -> Result<Value, Error> {
        std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| self.dispatch(op, args)))
            .unwrap_or_else(|_| Err(Error::failed(format!("{op} stopped"))))
    }
    fn dispatch(&mut self, op: &str, args: &Value) -> Result<Value, Error> {
        if matches!(op, "item_options" | "mcp_item_options")
            && !args.get("arguments").is_some_and(Value::is_object)
        {
            return Err(Error::invalid(
                r#"serve item_options requires a nested arguments object: put the item id in arguments.id and keep the request id at the top level (for example {"id":1,"op":"item_options","arguments":{"part_ref":"…","id":"sheet"}})."#,
            ));
        }
        if let Some(tool) = op.strip_prefix("mcp_") {
            let nested = args.get("arguments");
            let mut args = nested.unwrap_or(args).clone();
            if nested.is_none()
                && let Some(fields) = args.as_object_mut()
            {
                fields.remove("op");
                fields.remove("id");
            }
            return crate::mcp::call(&crate::stateless::Cache::environment(), tool, &args);
        }
        if op == "stateless_apply" || op == "stateless_preview" {
            let cache = crate::stateless::Cache::environment();
            let part_ref = args["part_ref"]
                .as_str()
                .ok_or_else(|| Error::invalid("provide part_ref from inspect_part"))?;
            let spec: Spec =
                serde_json::from_value(args.get("spec").cloned().unwrap_or_else(|| json!({})))
                    .map_err(|e| Error::invalid(e.to_string()))?;
            let ops: Vec<crate::items::Op> = serde_json::from_value(args["ops"].clone())
                .map_err(|e| Error::invalid(e.to_string()))?;
            return if op == "stateless_apply" {
                Ok(json_of(&cache.apply(part_ref, &spec, &ops)?))
            } else {
                Ok(json_of(&cache.preview(part_ref, &spec, &ops)?))
            };
        }
        if op == "open" {
            let step = args
                .get("step")
                .and_then(Value::as_str)
                .ok_or_else(|| Error::invalid("open needs \"step\", a file path"))?;
            let spec = args
                .get("spec")
                .filter(|v| !v.is_null())
                .map(|v| serde_json::from_value(v.clone()))
                .transpose()
                .map_err(|e| Error::invalid(format!("invalid spec: {e}")))?
                .unwrap_or_default();
            let part = request_part(op, args)?;
            let session = if let Some(a) = args.get("analysis").filter(|a| !a.is_null()) {
                if args.get("features").is_some_and(|v| !v.is_null()) {
                    return Err(Error::invalid("open takes analysis or features, not both"));
                }
                let analysis: Analysis = serde_json::from_value(a.clone())
                    .map_err(|e| Error::invalid(format!("invalid analysis: {e}")))?;
                if !made_of_part(&analysis, part) {
                    return Err(Error::invalid(
                        "open's part is not the part the analysis was made from; omit part",
                    ));
                }
                crate::session::Session::new(bound(Path::new(step), &analysis)?, analysis, spec)?
            } else {
                let features = match args.get("features") {
                    None | Some(Value::Null) => Features::Native,
                    Some(Value::String(s)) if s == "native" => Features::Native,
                    Some(Value::String(s)) if s == "none" => Features::None,
                    Some(Value::String(s)) => Features::Python(Path::new(s)),
                    _ => {
                        return Err(Error::invalid(
                            "open's features is native, none or a JSON file path",
                        ));
                    }
                };
                crate::session::Session::open_part(Path::new(step), part, features, spec)?
            };
            let result = json_of(session.state());
            self.session = Some(session);
            return Ok(result);
        }
        if !SESSION_OPERATIONS.contains(&op) {
            return call(op, args);
        }
        let session = self
            .session
            .as_mut()
            .ok_or_else(|| Error::invalid("open a part before using session operations"))?;
        let id = || {
            args.get("item")
                .and_then(Value::as_str)
                .ok_or_else(|| Error::invalid(format!("{op} needs \"item\", a stable item ID")))
        };
        match op {
            "apply" => {
                let ops = args
                    .get("ops")
                    .ok_or_else(|| Error::invalid("apply needs ops, a list of Spec operations"))?;
                let ops: Vec<crate::items::Op> = serde_json::from_value(ops.clone())
                    .map_err(|e| Error::invalid(format!("invalid ops: {e}")))?;
                let commit = match args.get("commit") {
                    None => true,
                    Some(Value::Bool(b)) => *b,
                    _ => return Err(Error::invalid("commit is a boolean")),
                };
                Ok(json_of(&session.apply(&ops, commit)?))
            }
            "undo" => Ok(json_of(&session.undo()?)),
            "redo" => Ok(json_of(&session.redo()?)),
            "item" => Ok(json_of(&session.item(id()?)?)),
            "options" => Ok(json_of(&session.options(id()?)?)),
            "items" => {
                let filter = serde_json::from_value(
                    args.get("filter").cloned().unwrap_or_else(|| json!({})),
                )
                .map_err(|e| Error::invalid(format!("invalid item filter: {e}")))?;
                Ok(json_of(&session.items(&filter)))
            }
            "problems" => {
                let filter = serde_json::from_value(
                    args.get("filter").cloned().unwrap_or_else(|| json!({})),
                )
                .map_err(|e| Error::invalid(format!("invalid problem filter: {e}")))?;
                Ok(json_of(&session.problems(&filter)))
            }
            _ => unreachable!(),
        }
    }
}

/// Stateless subprocess operations; tool calls use the same published MCP schemas.
pub const STATELESS_OPERATIONS: [&str; 16] = [
    "stateless_apply",
    "stateless_preview",
    "mcp_inspect_part",
    "mcp_list_items",
    "mcp_get_item",
    "mcp_list_problems",
    "mcp_item_options",
    "mcp_preview_ops",
    "mcp_apply_ops",
    "mcp_view_sheet",
    "mcp_render_pdf",
    "mcp_get_spec",
    "mcp_spec_schema",
    "mcp_analyse",
    "mcp_render",
    "mcp_parts",
];

#[cfg(test)]
mod export_tests {
    use super::*;
    use std::fs;
    use std::io::{self, Write};
    use std::path::PathBuf;
    use std::sync::{Barrier, Mutex, mpsc};
    use std::time::Duration;

    static LOCK: Mutex<()> = Mutex::new(());

    struct Scratch(PathBuf);
    impl Scratch {
        fn new(name: &str) -> Self {
            let path = std::env::temp_dir()
                .join(format!("draftwright-export-{name}-{}", std::process::id()));
            let _ = fs::remove_dir_all(&path);
            fs::create_dir(&path).unwrap();
            Self(path)
        }
        fn entries(&self) -> Vec<PathBuf> {
            let mut entries: Vec<_> = fs::read_dir(&self.0)
                .unwrap()
                .map(|e| e.unwrap().path())
                .collect();
            entries.sort();
            entries
        }
    }
    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn simultaneous_exports_keep_complete_payloads_and_foreign_staging() {
        let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let dir = Scratch::new("simultaneous");
        let foreign = dir.0.join("part.rev2.svg.part");
        fs::write(&foreign, b"another invocation").unwrap();
        let barrier = Barrier::new(2);
        std::thread::scope(|scope| {
            let handles: Vec<_> = (0..2)
                .map(|writer| {
                    let dir = &dir;
                    let barrier = &barrier;
                    scope.spawn(move || {
                        let files: Files = FORMATS
                            .iter()
                            .enumerate()
                            .map(|(i, &format)| {
                                (
                                    format.into(),
                                    vec![b'A' + writer * 4 + i as u8; 65536 + writer as usize],
                                )
                            })
                            .collect();
                        barrier.wait();
                        (0..8)
                            .map(|_| write(&dir.0.join("part.rev2.pdf"), &files))
                            .collect::<Vec<_>>()
                    })
                })
                .collect();
            for handle in handles {
                for result in handle.join().unwrap() {
                    let written = result.unwrap();
                    assert_eq!(
                        written,
                        FORMATS.map(|f| dir.0.join(format!("part.rev2.{f}")).display().to_string())
                    );
                }
            }
        });
        for (i, format) in FORMATS.iter().enumerate() {
            let bytes = fs::read(dir.0.join(format!("part.rev2.{format}"))).unwrap();
            assert!(bytes == vec![b'A' + i as u8; 65536] || bytes == vec![b'E' + i as u8; 65537]);
        }
        assert_eq!(fs::read(&foreign).unwrap(), b"another invocation");
        assert_eq!(
            dir.entries().len(),
            FORMATS.len() + 1,
            "only destinations and the foreign staging file"
        );
    }

    #[test]
    fn last_rename_wins_without_touching_the_other_writers_stage() {
        let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let dir = Scratch::new("last-writer");
        let path = dir.0.join("part.svg");
        fs::write(&path, b"old destination").unwrap();
        let (ready_tx, ready_rx) = mpsc::channel();
        let (resume_tx, resume_rx) = mpsc::channel();
        std::thread::scope(|scope| {
            let first_path = &path;
            let first = scope.spawn(move || {
                write_file(first_path, |file| {
                    file.write_all(b"first payload")?;
                    ready_tx.send(()).unwrap();
                    resume_rx.recv_timeout(Duration::from_secs(10)).unwrap();
                    Ok(())
                })
            });
            ready_rx.recv_timeout(Duration::from_secs(10)).unwrap();
            let stage = dir.entries().into_iter().find(|p| p != &path).unwrap();
            let second = write_file(&path, |file| {
                file.write_all(b"second payload")?;
                assert_eq!(fs::read(&path).unwrap(), b"old destination");
                Ok(())
            });
            let intermediate = fs::read(&path);
            let retained = fs::read(&stage);
            resume_tx.send(()).unwrap();
            let first = first.join().unwrap();
            eprintln!(
                "H3 last-writer: completed={}, retained_stage={}",
                usize::from(first.is_ok()) + usize::from(second.is_ok()),
                usize::from(retained.as_ref().is_ok_and(|b| b == b"first payload"))
            );
            second.unwrap();
            first.unwrap();
            assert_eq!(intermediate.unwrap(), b"second payload");
            assert_eq!(retained.unwrap(), b"first payload");
        });
        assert_eq!(fs::read(&path).unwrap(), b"first payload");
        assert_eq!(dir.entries(), [path]);
    }

    #[test]
    fn failed_partial_write_cleans_only_its_owned_stage() {
        let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let dir = Scratch::new("write-failure");
        let path = dir.0.join("part.svg");
        fs::write(&path, b"old destination").unwrap();
        let (ready_tx, ready_rx) = mpsc::channel();
        let (resume_tx, resume_rx) = mpsc::channel();
        std::thread::scope(|scope| {
            let first_path = &path;
            let first = scope.spawn(move || {
                write_file(first_path, |file| {
                    file.write_all(b"successful payload")?;
                    ready_tx.send(()).unwrap();
                    resume_rx.recv_timeout(Duration::from_secs(10)).unwrap();
                    Ok(())
                })
            });
            ready_rx.recv_timeout(Duration::from_secs(10)).unwrap();
            let stage = dir.entries().into_iter().find(|p| p != &path).unwrap();
            let failed = write_file(&path, |file| {
                file.write_all(b"partial")?;
                Err(io::Error::other("injected write failure"))
            });
            let retained = fs::read(&stage);
            let destination = fs::read(&path);
            let entries = dir.entries();
            resume_tx.send(()).unwrap();
            let first = first.join().unwrap();
            eprintln!(
                "H3 partial-write failure: retained_stage={}",
                usize::from(retained.as_ref().is_ok_and(|b| b == b"successful payload"))
            );
            let error = failed.unwrap_err();
            assert_eq!(error.code, "failed");
            assert!(error.message.contains("injected write failure"));
            assert_eq!(destination.unwrap(), b"old destination");
            assert_eq!(retained.unwrap(), b"successful payload");
            assert_eq!(entries.len(), 2);
            first.unwrap();
        });
        assert_eq!(fs::read(&path).unwrap(), b"successful payload");
        assert_eq!(dir.entries(), [path]);
    }

    #[test]
    fn failed_rename_keeps_the_other_stage_and_destination() {
        let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let dir = Scratch::new("rename-failure");
        let path = dir.0.join("part.svg");
        fs::create_dir(&path).unwrap();
        fs::write(path.join("keep"), b"destination contents").unwrap();
        let (ready_tx, ready_rx) = mpsc::channel();
        let (resume_tx, resume_rx) = mpsc::channel();
        std::thread::scope(|scope| {
            let first_path = &path;
            let first = scope.spawn(move || {
                write_file(first_path, |file| {
                    file.write_all(b"successful payload")?;
                    ready_tx.send(()).unwrap();
                    resume_rx.recv_timeout(Duration::from_secs(10)).unwrap();
                    Ok(())
                })
            });
            ready_rx.recv_timeout(Duration::from_secs(10)).unwrap();
            let stage = dir.entries().into_iter().find(|p| p != &path).unwrap();
            let failed = write_file(&path, |file| file.write_all(b"failed payload"));
            let retained = fs::read(&stage);
            let destination = fs::read(path.join("keep"));
            let entries = dir.entries();
            fs::remove_dir_all(&path).unwrap();
            resume_tx.send(()).unwrap();
            let first = first.join().unwrap();
            eprintln!(
                "H3 rename failure: retained_stage={}",
                usize::from(retained.as_ref().is_ok_and(|b| b == b"successful payload"))
            );
            assert_eq!(failed.unwrap_err().code, "failed");
            assert_eq!(destination.unwrap(), b"destination contents");
            assert_eq!(retained.unwrap(), b"successful payload");
            assert_eq!(entries.len(), 2);
            first.unwrap();
        });
        assert_eq!(fs::read(&path).unwrap(), b"successful payload");
        assert_eq!(dir.entries(), [path]);
    }

    #[test]
    fn export_batch_failure_keeps_published_formats_and_foreign_files() {
        let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let dir = Scratch::new("batch");
        let foreign = dir.0.join("part.pdf.part");
        fs::write(&foreign, b"another invocation").unwrap();
        fs::write(dir.0.join("part.svg"), b"old SVG").unwrap();
        fs::write(dir.0.join("part.png"), b"unrequested PNG").unwrap();
        fs::create_dir(dir.0.join("part.pdf")).unwrap();
        let error = write(
            &dir.0.join("part"),
            &[
                ("svg".into(), b"new SVG".to_vec()),
                ("pdf".into(), b"new PDF".to_vec()),
                ("dxf".into(), b"not reached".to_vec()),
            ],
        )
        .unwrap_err();
        assert_eq!(error.code, "failed");
        assert_eq!(fs::read(dir.0.join("part.svg")).unwrap(), b"new SVG");
        assert_eq!(
            fs::read(dir.0.join("part.png")).unwrap(),
            b"unrequested PNG"
        );
        eprintln!(
            "H3 batch failure: retained_foreign={}",
            usize::from(fs::read(&foreign).is_ok_and(|b| b == b"another invocation"))
        );
        assert_eq!(fs::read(&foreign).unwrap(), b"another invocation");
        assert_eq!(dir.entries().len(), 4);
        assert!(!dir.0.join("part.dxf").exists());
    }
    #[test]
    fn exclusive_creation_skips_collisions_without_removing_foreign_entries() {
        let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let dir = Scratch::new("collisions");
        let path = dir.0.join("part.svg");
        fs::write(&path, b"old destination").unwrap();
        let next = EXPORT_STAGED.load(std::sync::atomic::Ordering::Relaxed);
        let candidate = |id| {
            dir.0.join(format!(
                ".draftwright-export.{}.{id}.part",
                std::process::id()
            ))
        };
        fs::write(candidate(next), b"foreign file").unwrap();
        fs::create_dir(candidate(next + 1)).unwrap();
        fs::write(candidate(next + 1).join("keep"), b"foreign directory").unwrap();
        #[cfg(unix)]
        std::os::unix::fs::symlink(&path, candidate(next + 2)).unwrap();
        #[cfg(not(unix))]
        fs::write(candidate(next + 2), b"foreign file").unwrap();
        write_file(&path, |file| {
            file.write_all(b"new destination")?;
            assert!(candidate(next + 3).is_file(), "collisions must be retried");
            assert_eq!(fs::read(&path).unwrap(), b"old destination");
            Ok(())
        })
        .unwrap();
        assert_eq!(fs::read(&path).unwrap(), b"new destination");
        assert_eq!(fs::read(candidate(next)).unwrap(), b"foreign file");
        assert_eq!(
            fs::read(candidate(next + 1).join("keep")).unwrap(),
            b"foreign directory"
        );
        assert!(fs::symlink_metadata(candidate(next + 2)).is_ok());
        assert_eq!(dir.entries().len(), 4);
    }
}
