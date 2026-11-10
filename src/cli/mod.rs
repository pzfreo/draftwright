//! The command line over the document model (ADR R1), through [`crate::api`]. Each command
//! prints JSON.
//!
//! - `draftwright analyse part.step [--features r.json | --no-features]`: the Analysis
//! - `draftwright plan part.step [--features r.json | --no-features] [--spec s.json]`: the Plan;
//!   or `draftwright plan [part.step] --analysis a.json [--spec s.json]`, from an Analysis
//!   without analysing again
//! - `draftwright render part.step [--features r.json | --no-features | --analysis a.json]
//!   [--spec s.json] -o out [--formats svg,pdf,png] [--dpi 150]`: writes the files
//!   (`out.svg`, …; svg and pdf unless named, a PNG at `--dpi` pixels per inch; DXF export is
//!   disabled until it is fixed) and prints
//!   what was written, with the problems: the plan's, and what could not be drawn as planned
//! - `draftwright review part.step [--features r.json | --no-features | --analysis a.json]
//!   [--spec s.json]`: the drawing as SVG text, with the problems and lint's findings on the
//!   drawing
//! - `draftwright edit s.json ops.json`: the edited Spec
//! - `draftwright spec drawing.(svg|dxf|pdf|png)`: the Spec embedded in a rendered drawing
//! - `draftwright diff old.step new.step [--spec s.json] [--part K] -o dir [--title T]
//!   [--dpi 150]`: the drawing of *old* with the Spec, the Spec carried to *new* through
//!   quiddity's correspondence (ADR R3) and *new* drawn with it, and what changed
//!   ([`crate::revision`]). Writes `report.json`, `report.md` (a pull request comment),
//!   `spec.json` (the carried Spec), `changes.svg` and `changes.png` (the new drawing marked up)
//!   and `drawing.svg` into *dir*, and prints the verdict
//! - `draftwright parts part.step`: the file's distinct parts (`{parts: [{part, name,
//!   product_id, faces, instances}]}`), by which one part of an assembly is chosen
//! - `draftwright serve`: the JSON-lines service. One request per line on stdin, such as
//!   `{"id": 1, "op": "plan", "step": "part.step", "spec": {…}}`, and one reply per line on
//!   stdout: `{"id": 1, "ok": true, "result": …}` or `{"id": 1, "ok": false, "error": {"code",
//!   "message", "choices"?}}`. `{"op": "hello"}` gives the protocol version and the operations;
//!   the others, and their arguments, are those of [`crate::api::call`].
//! - `draftwright completions <SHELL>`: a completion script for bash, zsh, fish, powershell or
//!   elvish, on stdout
//!
//! The command line is parsed by clap: `--help` (on any command), `--version`, and clap's usage
//! errors on stderr. Values are checked here, so a bad `--part` or `--dpi` is refused as JSON.
//!
//! The features come from Rust quiddity's recognition, run on the part
//! ([`crate::api::Features::Native`]), unless one of these says otherwise. A part whose
//! recognition document is refused is not drawn (exit 1, with the refusal), and the families
//! that recognition does not give are named in the plan's problems (`features_not_recognised`).
//! `--features` names Python quiddity's recognition JSON for the same file
//! (`tools/recognise.py`, read by [`crate::bridge`]), used instead. `--no-features`
//! analyses the part with no features (no feature dimensions, sections or lint), as the library
//! does when given none: the way to draw a part whose recognition is refused. `--analysis` names
//! an Analysis that `draftwright analyse` printed for the same STEP file (with its features),
//! used in place of analysing the file again; it is refused with `--features` or
//! `--no-features`, or when made from another file. `serve` takes Python's JSON or none, as
//! [`crate::api::call`] does.
//!
//! `analyse`, `plan`, `render` and `review` take `--part K`: part K of an assembly (as `parts`
//! lists them) drawn alone, in its own frame and with its PMI only. Without it the whole file is
//! drawn as one part, and an assembly's plan warns (`assembly_drawn_whole`), naming its parts;
//! when every body coincides with one part's (`coincident_bodies`), or lies within the largest
//! body's box (`assembly_principal_drawn`), that part is drawn alone.
//!
//! Exit codes: 0 done; 2 the request is invalid (the JSON error says what would be accepted), or
//! the command line is (clap's usage error); 1 something failed and nothing was written in its
//! place.

use std::io::{BufRead, Write};

use crate::api::{self, Error, Features};
use crate::model::Spec;
use clap::{Args, CommandFactory, Parser, Subcommand};
use serde_json::{Value, json};

/// The service's protocol version, given by `hello`.
const PROTOCOL: u32 = 1;

mod compat;
pub mod mcp;
mod version;

const AFTER_HELP: &str = "Discovery: schemas/spec.json, docs/model.md and docs/mcp.md ship in the release.
Bare render: draftwright part.step [--title TITLE] [--out STEM | --out-dir DIR].
Subcommand names take priority: use ./render for a file named render.
Pictorial options for render, review, plan and bare render:
  --shaded       Shade the isometric view
  --below        Add a view from below (shaded with --shaded, otherwise lines)
  --colour HEX   Set the shaded colour; --color is an alias; requires --shaded
JSON-lines serve: {\"op\":\"hello\"} lists operations; spec_schema and item_options use the MCP argument shapes.";

/// The command line. Values are kept as text and checked by [`run`], so a bad `--part` or
/// `--dpi` is refused with the JSON error it has always had; only the shape of the command line
/// is clap's.
#[derive(Parser)]
#[command(
    name = "draftwright",
    version = version::version(),
    about = "draftwright — engineering drawings from STEP",
    after_help = AFTER_HELP,
    subcommand_required = true,
    args_override_self = true
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

/// Where the part's features come from, and the Spec and part drawn.
#[derive(Args)]
struct Drawing {
    /// Python quiddity's recognition JSON for the same STEP file, used instead of Rust
    /// quiddity's recognition
    #[arg(long, value_name = "JSON")]
    features: Option<String>,
    /// analyse the part with no features (no feature dimensions, sections or lint)
    #[arg(long)]
    no_features: bool,
    /// an Analysis `draftwright analyse` printed for the same STEP file, used instead of
    /// analysing it again
    #[arg(long, value_name = "JSON")]
    analysis: Option<String>,
    /// the Spec (without it, the defaults)
    #[arg(long, value_name = "JSON")]
    spec: Option<String>,
    /// part K of an assembly, as `draftwright parts` lists them, drawn alone
    #[arg(long, value_name = "K", allow_negative_numbers = true)]
    part: Option<String>,
}

#[derive(Subcommand)]
enum Command {
    /// print the part's Analysis
    Analyse {
        step: String,
        #[command(flatten)]
        drawing: Drawing,
    },
    /// print the Plan; from --analysis the STEP file may be left out
    Plan {
        #[arg(required_unless_present = "analysis")]
        step: Option<String>,
        #[command(flatten)]
        drawing: Drawing,
        #[command(flatten)]
        pictorial: compat::PictorialOptions,
    },
    /// write the drawing's files and print what was written, with the problems
    Render {
        step: String,
        #[command(flatten)]
        drawing: Drawing,
        #[command(flatten)]
        pictorial: compat::PictorialOptions,
        /// the output stem (out.svg, out.pdf, …)
        #[arg(
            short = 'o',
            long = "out",
            value_name = "STEM",
            required_unless_present = "out_dir",
            conflicts_with = "out_dir",
            allow_hyphen_values = true
        )]
        output: Option<String>,
        /// Output directory, using the STEP filename as the stem
        #[arg(long)]
        out_dir: Option<String>,
        #[command(flatten)]
        sheet: Box<compat::SheetOptions>,
        /// the formats written, of svg, pdf and png
        #[arg(long, value_name = "LIST", default_value = "svg,pdf")]
        formats: String,
        /// a PNG's pixels per inch (default 150)
        #[arg(long, value_name = "DPI", allow_negative_numbers = true)]
        dpi: Option<String>,
    },
    /// print the drawing as SVG text, with the problems and lint's findings
    Review {
        step: String,
        #[command(flatten)]
        drawing: Drawing,
        #[command(flatten)]
        pictorial: compat::PictorialOptions,
    },
    /// print a Spec with edit operations applied
    Edit {
        #[arg(value_name = "SPEC")]
        spec: String,
        ops: String,
    },
    /// print the Spec embedded in a rendered drawing (SVG, DXF, PDF or PNG)
    Spec { drawing: String },
    /// print the file's distinct parts
    Parts { step: String },
    /// write an assembly's drawing pack into a directory
    Pack {
        step: String,
        /// the output directory
        #[arg(
            short = 'o',
            value_name = "DIRECTORY",
            required = true,
            allow_hyphen_values = true
        )]
        output: Option<String>,
        /// the pack Spec
        #[arg(long, value_name = "JSON")]
        spec: Option<String>,
        /// the formats written
        #[arg(long, value_name = "LIST", default_value = "pdf,csv,json")]
        formats: String,
    },
    /// carry the Spec to a new revision of the part, draw it, and write what changed
    Diff {
        old: String,
        new: String,
        /// the output directory
        #[arg(
            short = 'o',
            value_name = "DIRECTORY",
            required = true,
            allow_hyphen_values = true
        )]
        output: Option<String>,
        /// the Spec of the old revision
        #[arg(long, value_name = "JSON")]
        spec: Option<String>,
        /// part K of an assembly, as `draftwright parts` lists them
        #[arg(long, value_name = "K", allow_negative_numbers = true)]
        part: Option<String>,
        /// the report's title (default: the new file's name)
        #[arg(long, value_name = "TITLE", allow_hyphen_values = true)]
        title: Option<String>,
        /// changes.png's pixels per inch (default 150)
        #[arg(long, value_name = "DPI", allow_negative_numbers = true)]
        dpi: Option<String>,
    },
    /// print the Spec's JSON Schema
    #[command(name = "spec_schema")]
    SpecSchema,
    /// print the settable paths of one item of a part opened by the MCP server
    #[command(name = "item_options")]
    ItemOptions {
        part_ref: String,
        id: String,
        #[arg(long, value_name = "JSON")]
        spec: Option<String>,
    },
    /// answer JSON-lines requests on stdin
    Serve,
    /// the MCP server over stdio
    McpStdio,
    /// the MCP server over HTTP on a loopback address
    McpHttp {
        /// default 127.0.0.1:8765
        address: Option<String>,
    },
    /// print a shell completion script to stdout
    Completions {
        #[arg(value_enum)]
        shell: clap_complete::Shell,
    },
}

/// What the command was given, for [`run`].
#[derive(Default)]
struct Given {
    positionals: Vec<String>,
    spec: Option<String>,
    inline_spec: Option<Value>,
    inline_analysis: Option<Value>,
    features: Option<String>,
    analysis: Option<String>,
    no_features: bool,
    part: Option<String>,
    output: Option<String>,
    formats: Option<String>,
    dpi: Option<String>,
    title: Option<String>,
    sheet: compat::SheetOptions,
    pictorial: compat::PictorialOptions,
    out_dir: Option<String>,
    bare: bool,
}

impl Given {
    fn drawing(positionals: Vec<String>, d: Drawing) -> Self {
        Given {
            positionals,
            spec: d.spec,
            features: d.features,
            analysis: d.analysis,
            no_features: d.no_features,
            part: d.part,
            ..Given::default()
        }
    }
}

/// Run the command line with argv, including the executable name; return its exit status.
pub fn main(args: Vec<std::ffi::OsString>) -> u8 {
    if compat::is_bare(&args) {
        return match compat::parse(args) {
            Ok(given) => finish("render", &given),
            Err(error) => parse_error(error),
        };
    }
    let cli = match Cli::try_parse_from(args) {
        Ok(cli) => cli,
        Err(error) => return parse_error(error),
    };
    let (name, given) = match cli.command {
        Command::Completions { shell } => {
            clap_complete::generate(
                shell,
                &mut Cli::command(),
                "draftwright",
                &mut std::io::stdout(),
            );
            return 0;
        }
        command @ (Command::McpStdio | Command::McpHttp { .. }) => {
            let args: Vec<String> = match command {
                Command::McpHttp { address } => {
                    std::iter::once("mcp-http".into()).chain(address).collect()
                }
                _ => vec!["mcp-stdio".into()],
            };
            return match crate::mcp::run(&args) {
                Ok(()) => 0,
                Err(e) => {
                    eprintln!("{e}");
                    1
                }
            };
        }
        Command::Serve => return serve(),
        Command::Analyse { step, drawing } => ("analyse", Given::drawing(vec![step], drawing)),
        Command::Plan {
            step,
            drawing,
            pictorial,
        } => (
            "plan",
            Given {
                pictorial,
                ..Given::drawing(step.into_iter().collect(), drawing)
            },
        ),
        Command::Review {
            step,
            drawing,
            pictorial,
        } => (
            "review",
            Given {
                pictorial,
                ..Given::drawing(vec![step], drawing)
            },
        ),
        Command::Render {
            step,
            drawing,
            pictorial,
            output,
            out_dir,
            sheet,
            formats,
            dpi,
        } => (
            "render",
            Given {
                output,
                out_dir,
                sheet: *sheet,
                pictorial,
                formats: Some(formats),
                dpi,
                ..Given::drawing(vec![step], drawing)
            },
        ),
        Command::Edit { spec, ops } => (
            "edit",
            Given {
                positionals: vec![spec, ops],
                ..Given::default()
            },
        ),
        Command::Spec { drawing } => (
            "spec",
            Given {
                positionals: vec![drawing],
                ..Given::default()
            },
        ),
        Command::Parts { step } => (
            "parts",
            Given {
                positionals: vec![step],
                ..Given::default()
            },
        ),
        Command::Pack {
            step,
            output,
            spec,
            formats,
        } => (
            "pack",
            Given {
                positionals: vec![step],
                output,
                spec,
                formats: Some(formats),
                ..Given::default()
            },
        ),
        Command::Diff {
            old,
            new,
            output,
            spec,
            part,
            title,
            dpi,
        } => (
            "diff",
            Given {
                positionals: vec![old, new],
                output,
                spec,
                part,
                title,
                dpi,
                ..Given::default()
            },
        ),
        Command::SpecSchema => ("spec_schema", Given::default()),
        Command::ItemOptions { part_ref, id, spec } => (
            "item_options",
            Given {
                positionals: vec![part_ref, id],
                spec,
                ..Given::default()
            },
        ),
    };
    finish(name, &given)
}

fn parse_error(error: clap::Error) -> u8 {
    let code = error.exit_code() as u8;
    let _ = error.print();
    code
}

/// The Python one-liner uses the bare CLI's destination rules and render pipeline.
pub fn make_drawing(
    step: String,
    out: Option<String>,
    spec: &str,
    scale: Option<f64>,
    source: Option<&str>,
) -> Result<(String, String), Error> {
    let spec = serde_json::from_str(spec)
        .map_err(|e| Error::invalid(format!("the spec is not valid: {e}")))?;
    let analysis = source
        .map(|source| {
            guarded("analyse", || {
                let (part, mut analysis) =
                    crate::model::analyse_native_part(std::path::Path::new(&step), None)?;
                let bytes = crate::model::read_step_file(std::path::Path::new(source))
                    .map_err(Error::invalid)?;
                // The source supplies PMI only. Never substitute its geometry for the shape.
                // The PMI reader checks the supplied part's faces and reports refused correlations.
                analysis.pmi = crate::pmi::attach(&bytes, &part);
                Ok(json_of(&analysis))
            })
        })
        .transpose()?;
    let result = run(
        "render",
        &Given {
            positionals: vec![step],
            output: out,
            inline_spec: Some(spec),
            inline_analysis: analysis,
            sheet: compat::SheetOptions::with_scale(scale),
            bare: true,
            ..Given::default()
        },
    )?;
    let written = &result["written"];
    let path = |suffix: &str| -> Result<String, Error> {
        written
            .as_array()
            .and_then(|paths| {
                paths
                    .iter()
                    .filter_map(Value::as_str)
                    .find(|path| path.ends_with(suffix))
            })
            .map(str::to_owned)
            .ok_or_else(|| Error::failed(format!("render did not write {suffix}")))
    };
    Ok((path(".svg")?, path(".pdf")?))
}

fn finish(name: &str, given: &Given) -> u8 {
    match run(name, given) {
        Ok(out) => {
            println!("{}", serde_json::to_string_pretty(&out).expect("JSON"));
            0
        }
        Err(error) => {
            println!(
                "{}",
                serde_json::to_string_pretty(&json!({ "ok": false, "error": error }))
                    .expect("JSON")
            );
            if error.code == "invalid" { 2 } else { 1 }
        }
    }
}

fn read_json(path: &str) -> Result<Value, Error> {
    let text = std::fs::read_to_string(path)
        .map_err(|e| Error::invalid(format!("cannot read {path}: {e}")))?;
    serde_json::from_str(&text).map_err(|e| Error::invalid(format!("{path} is not valid: {e}")))
}

/// Runs the command, its arguments put as [`api::call`] takes them.
fn run(command: &str, given: &Given) -> Result<Value, Error> {
    // clap has required each command's positionals; `plan` from an Analysis may have none.
    let arg = |i: usize| {
        given
            .positionals
            .get(i - 1)
            .map(String::as_str)
            .ok_or_else(|| Error::invalid(format!("{command} needs a STEP file")))
    };
    let mut spec = match &given.spec {
        Some(path) => read_json(path)?,
        None => given.inline_spec.clone().unwrap_or(Value::Null),
    };
    if command == "render" || (matches!(command, "plan" | "review") && given.pictorial.requested())
    {
        let mut typed = if spec.is_null() {
            Spec::default()
        } else {
            serde_json::from_value(spec)
                .map_err(|e| Error::invalid(format!("the spec is not valid: {e}")))?
        };
        if command == "render" {
            given.sheet.apply(&mut typed)?;
        }
        given.pictorial.apply(&mut typed)?;
        spec = json_of(&typed);
    }
    let render_output = if command == "render" && (given.bare || given.out_dir.is_some()) {
        Some(compat::output(
            arg(1)?,
            given.output.as_deref(),
            given.out_dir.as_deref(),
        ))
    } else {
        given.output.as_ref().map(std::path::PathBuf::from)
    };
    if let Some(dir) = given.out_dir.as_deref() {
        std::fs::create_dir_all(dir)
            .map_err(|e| Error::failed(format!("cannot create {dir}: {e}")))?;
    }
    let features = given.features.as_deref();
    let analysis = match &given.analysis {
        Some(path) => read_json(path)?,
        None => given.inline_analysis.clone().unwrap_or(Value::Null),
    };
    let no_features = given.no_features;
    let part = given
        .part
        .as_deref()
        .map(|k| {
            k.parse::<usize>().map_err(|_| {
                Error::invalid(format!(
                    "--part {k:?} is not a part's index; `draftwright parts <step>` lists them"
                ))
            })
        })
        .transpose()?;
    if no_features && (features.is_some() || !analysis.is_null()) {
        return Err(Error::invalid(
            "--no-features is not taken with --features or --analysis: it means no features at all.",
        ));
    }
    let output = || {
        render_output
            .as_ref()
            .and_then(|p| p.to_str())
            .ok_or_else(|| Error::invalid("render needs -o <output stem>"))
    };
    let formats = || {
        given
            .formats
            .as_deref()
            .unwrap_or("svg,pdf")
            .split(',')
            .collect::<Vec<_>>()
    };
    let dpi = || {
        given
            .dpi
            .as_deref()
            .map(|d| {
                d.parse::<f64>()
                    .map_err(|_| Error::invalid(format!("--dpi {d:?} is not a number")))
            })
            .transpose()
    };
    if command == "parts" {
        return guarded(command, || {
            let parts = api::parts(std::path::Path::new(arg(1)?))?;
            let keys = crate::pack::part_keys(&parts);
            let parts: Vec<_> = parts
                .iter()
                .zip(keys)
                .map(|(part, key)| {
                    let mut value = serde_json::to_value(part).expect("part serialises");
                    value["key"] = json!(key);
                    value
                })
                .collect();
            Ok(json!({"parts": parts}))
        });
    }
    if command == "pack" {
        let step = std::path::Path::new(arg(1)?);
        let dir = std::path::Path::new(
            given
                .output
                .as_deref()
                .ok_or_else(|| Error::invalid("pack needs -o <directory>"))?,
        );
        let spec = if spec.is_null() {
            crate::pack::PackSpec::default()
        } else {
            serde_json::from_value(spec)
                .map_err(|e| Error::invalid(format!("invalid pack Spec: {e}")))?
        };
        let formats: Vec<_> = given
            .formats
            .as_deref()
            .unwrap_or("pdf,csv,json")
            .split(',')
            .collect();
        return guarded(command, || {
            let (report, files, timings) = crate::pack::render_profiled(step, &spec, &formats)?;
            std::fs::create_dir_all(dir)
                .map_err(|e| Error::failed(format!("cannot create {}: {e}", dir.display())))?;
            let written = api::write(&dir.join("pack"), &files)?;
            Ok(json!({ "written": written, "report": report, "timings": timings }))
        });
    }
    if command == "diff" {
        let (old, new) = (std::path::Path::new(arg(1)?), std::path::Path::new(arg(2)?));
        let dir = std::path::PathBuf::from(
            given
                .output
                .as_deref()
                .ok_or_else(|| Error::invalid("diff needs -o <output directory>"))?,
        );
        let spec = if spec.is_null() {
            json!({ "schema": crate::model::SPEC_SCHEMA, "sheet": {} })
        } else {
            spec
        };
        let dpi = dpi()?.unwrap_or(api::DPI);
        let title = given.title.clone().unwrap_or_else(|| {
            new.file_name()
                .map_or("part".into(), |n| n.to_string_lossy().into_owned())
        });
        return guarded(command, || {
            let revised = crate::revision::diff(old, new, part, &spec)?;
            let marked = revised.marked();
            let pretty = |v: &Value| serde_json::to_vec_pretty(v).expect("JSON serialises");
            let files: Vec<(&str, Vec<u8>)> = vec![
                ("report.json", pretty(&json_of(&revised.report))),
                (
                    "report.md",
                    revised.markdown(&title, Some("changes.png")).into_bytes(),
                ),
                ("spec.json", pretty(&revised.report.carried_spec)),
                ("changes.svg", marked.svg().into_bytes()),
                ("changes.png", marked.png(dpi).map_err(Error::failed)?),
                ("drawing.svg", revised.new.drawing().svg().into_bytes()),
            ];
            std::fs::create_dir_all(&dir)
                .map_err(|e| Error::failed(format!("cannot create {}: {e}", dir.display())))?;
            let mut written = Vec::new();
            for (name, bytes) in files {
                let path = dir.join(name);
                std::fs::write(&path, bytes)
                    .map_err(|e| Error::failed(format!("cannot write {}: {e}", path.display())))?;
                written.push(path.display().to_string());
            }
            Ok(json!({
                "written": written,
                "verdict": revised.report.verdict,
                "summary": revised.report.summary,
            }))
        });
    }
    if matches!(command, "analyse" | "plan" | "render" | "review")
        && features.is_none()
        && analysis.is_null()
        && !no_features
    {
        let step = std::path::Path::new(arg(1)?);
        let spec: Spec = if spec.is_null() {
            Spec::default()
        } else {
            serde_json::from_value(spec)
                .map_err(|e| Error::invalid(format!("the spec is not valid: {e}")))?
        };
        let native = Features::Native;
        return guarded(command, || {
            Ok(match command {
                "analyse" => json_of(&api::analyse_part(step, part, native)?),
                "plan" => json_of(&api::plan_part(step, part, native, &spec)?),
                "review" => json_of(&api::review_part(step, part, native, &spec)?),
                _ => {
                    let (output, dpi) = (output()?, dpi()?.unwrap_or(api::DPI));
                    let (plan, files) =
                        api::render_part(step, part, native, &spec, &formats(), dpi)?;
                    let written = api::write(std::path::Path::new(output), &files)?;
                    json!({ "written": written, "problems": plan.problems })
                }
            })
        });
    }
    let request = match command {
        // From an Analysis, plan needs no STEP file.
        "plan" if !analysis.is_null() => json!({
            "step": given.positionals.first(),
            "features": features,
            "analysis": analysis,
            "spec": spec,
            "part": part,
        }),
        "analyse" | "plan" | "review" => json!({
            "step": arg(1)?,
            "features": features,
            "analysis": analysis,
            "spec": spec,
            "part": part,
        }),
        "render" => json!({
            "step": arg(1)?,
            "features": features,
            "analysis": analysis,
            "spec": spec,
            "part": part,
            "output": output()?,
            "formats": formats(),
            "dpi": dpi()?,
        }),
        "spec_schema" => json!({}),
        "item_options" => {
            let mut request = json!({ "part_ref": arg(1)?, "id": arg(2)? });
            if !spec.is_null() {
                request["spec"] = spec;
            }
            request
        }
        "edit" => json!({ "spec": read_json(arg(1)?)?, "ops": read_json(arg(2)?)? }),
        "spec" => json!({ "drawing": arg(1)? }),
        "parts" => json!({ "step": arg(1)? }),
        other => unreachable!("{other} is not a command"),
    };
    api::call(command, &request)
}

/// *op*'s result, a panic inside it answered as `failed`, as [`api::call`] answers it.
fn guarded(op: &str, f: impl FnOnce() -> Result<Value, Error>) -> Result<Value, Error> {
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(f)).unwrap_or_else(|panic| {
        let why = panic
            .downcast_ref::<&str>()
            .map(|s| s.to_string())
            .or_else(|| panic.downcast_ref::<String>().cloned())
            .unwrap_or_default();
        Err(Error::failed(format!("{op} stopped: {why}")))
    })
}

fn json_of(document: &impl serde::Serialize) -> Value {
    serde_json::to_value(document).expect("a document serialises")
}

/// Answers requests, one JSON object per line, until stdin closes.
fn serve() -> u8 {
    let stdout = std::io::stdout();
    let mut service = api::Service::default();
    for line in std::io::stdin().lock().lines() {
        let Ok(line) = line else { break };
        if line.trim().is_empty() {
            continue;
        }
        let reply = match serde_json::from_str::<Value>(&line) {
            Ok(request) => reply(&request, &mut service),
            Err(e) => json!({ "id": null, "ok": false, "error": Error::invalid(e.to_string()) }),
        };
        let mut out = stdout.lock();
        if writeln!(out, "{reply}").and_then(|_| out.flush()).is_err() {
            break;
        }
    }
    0
}

/// A request's reply: its result, or a structured error.
fn reply(request: &Value, service: &mut api::Service) -> Value {
    let Some(fields) = request.as_object() else {
        let error = Error::invalid("a request is a JSON object");
        return json!({ "id": null, "ok": false, "error": error });
    };
    let id = fields.get("id").cloned().unwrap_or(Value::Null);
    let result = match fields.get("op").and_then(Value::as_str) {
        Some("hello") => Ok(
            json!({ "protocol": PROTOCOL, "ops": api::OPERATIONS.iter().chain(api::SESSION_OPERATIONS.iter()).chain(api::STATELESS_OPERATIONS.iter()).collect::<Vec<_>>() }),
        ),
        Some(op) => service.call(op, request),
        None => Err(Error::invalid("a request needs \"op\"")),
    };
    match result {
        Ok(result) => json!({ "id": id, "ok": true, "result": result }),
        Err(error) => json!({ "id": id, "ok": false, "error": error }),
    }
}
