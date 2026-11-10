//! The common Python CLI subset, shared with `render` so both author the same Spec.
use std::path::{Path, PathBuf};

use crate::{api::Error, model::Spec};
use clap::{Args, CommandFactory, Parser};

use super::{Cli, Given};

#[derive(Args, Default)]
pub(super) struct SheetOptions {
    #[arg(long)]
    title: Option<String>,
    #[arg(long)]
    number: Option<String>,
    #[arg(long)]
    material: Option<String>,
    #[arg(long)]
    drawn_by: Option<String>,
    #[arg(long)]
    revision: Option<String>,
    #[arg(long)]
    date: Option<String>,
    #[arg(long)]
    company: Option<String>,
    #[arg(long)]
    approved_by: Option<String>,
    /// General tolerance in the title block
    #[arg(long)]
    tolerance: Option<String>,
    /// Numeric drawing scale, e.g. 5 for 5:1 or 0.5 for 1:2 (default: auto)
    #[arg(long, allow_negative_numbers = true)]
    scale: Option<f64>,
    /// Draw a sheet frame (Rust always draws one)
    #[arg(long)]
    frame: bool,
}

impl SheetOptions {
    pub(super) fn with_scale(scale: Option<f64>) -> Self {
        Self {
            scale,
            ..Self::default()
        }
    }

    pub(super) fn apply(&self, spec: &mut Spec) -> Result<(), Error> {
        for (key, value) in [
            ("title", &self.title),
            ("number", &self.number),
            ("material", &self.material),
            ("drawn_by", &self.drawn_by),
            ("revision", &self.revision),
            ("date", &self.date),
            ("company", &self.company),
            ("approved_by", &self.approved_by),
            ("tolerance", &self.tolerance),
        ] {
            if let Some(value) = value {
                spec.sheet.title.insert(key.into(), value.clone());
            }
        }
        if let Some(scale) = self.scale {
            spec.sheet.scale = Some(scale_ratio(scale)?);
        }
        // SheetSpec has no frame switch: its renderer always draws the ISO frame. Accepting
        // --frame therefore requests the existing behaviour, including its clearance.
        Ok(())
    }
}

/// Pictorial shortcuts shared by the bare form, render, review and plan.
#[derive(Args, Default)]
pub(super) struct PictorialOptions {
    /// Shade the isometric view
    #[arg(long)]
    shaded: bool,
    /// Add a view from below (shaded with --shaded, otherwise lines)
    #[arg(long)]
    below: bool,
    /// Base shaded colour as six hex digits, with optional # (requires --shaded)
    #[arg(
        long,
        visible_alias = "color",
        value_name = "HEX",
        allow_hyphen_values = true
    )]
    colour: Option<String>,
}

impl PictorialOptions {
    pub(super) fn requested(&self) -> bool {
        self.shaded || self.below || self.colour.is_some()
    }

    pub(super) fn apply(&self, spec: &mut Spec) -> Result<(), Error> {
        use crate::shaded::ViewStyle;
        if self.colour.is_some() && !self.shaded {
            return Err(Error::invalid("--colour/--color requires --shaded"));
        }
        let colour = self.colour.as_deref().map(hex_colour).transpose()?;
        if self.shaded {
            let iso = spec.views.entry("iso".into()).or_default();
            iso.style = Some(ViewStyle::Shaded);
            if let Some(colour) = colour {
                iso.colour = Some(colour);
            }
        }
        if self.below {
            let below = spec.views.entry("iso_below".into()).or_default();
            below.omit = false;
            below.style = Some(if self.shaded {
                ViewStyle::Shaded
            } else {
                ViewStyle::Wireframe
            });
            if let Some(colour) = colour {
                below.colour = Some(colour);
            }
        }
        Ok(())
    }
}

fn hex_colour(value: &str) -> Result<[u8; 3], Error> {
    let hex = value.strip_prefix('#').unwrap_or(value);
    if hex.len() != 6 || !hex.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err(Error::invalid(format!(
            "--colour/--color expects six hexadecimal digits, e.g. '#b8c4cc' or b8c4cc; got {value:?}"
        )));
    }
    // The length and ASCII check above makes these byte boundaries and digits valid.
    Ok([0, 2, 4].map(|i| u8::from_str_radix(&hex[i..i + 2], 16).expect("checked hex pair")))
}

/// Avoid printing binary reciprocal noise or silently rounding authored scales.
fn scale_ratio(scale: f64) -> Result<String, Error> {
    if !scale.is_finite() || scale <= 0.0 {
        return Err(Error::invalid("--scale must be a finite positive number"));
    }
    let ratio = if scale >= 1.0 { scale } else { scale.recip() };
    let unclean = || {
        Error::invalid(
            "--scale must give a clean finite n:1 or 1:n ratio with at most six decimal places",
        )
    };
    if !ratio.is_finite() {
        return Err(unclean());
    }
    let text = format!("{ratio:.6}");
    let clean: f64 = text.parse().map_err(|_| unclean())?;
    // Permit floating-point representation noise, not a change in the requested scale.
    if (clean - ratio).abs() > ratio * f64::EPSILON * 8.0 {
        return Err(unclean());
    }
    let text = text.trim_end_matches('0').trim_end_matches('.');
    Ok(if scale >= 1.0 {
        format!("{text}:1")
    } else {
        format!("1:{text}")
    })
}

#[derive(Parser)]
#[command(name = "draftwright", version = super::version::version(), args_override_self = true,
    about = "Render a STEP file using the common Python CLI options",
    after_help = "Subcommand names take priority over file names. Use ./render for a file named render.\nFor more controls use draftwright render --help and --spec.")]
struct Bare {
    step: String,
    #[command(flatten)]
    sheet: SheetOptions,
    #[command(flatten)]
    pictorial: PictorialOptions,
    /// the Spec to use as the base for command-line overrides
    #[arg(long, value_name = "JSON")]
    spec: Option<String>,
    /// Output prefix; relative paths use the working directory
    #[arg(long, conflicts_with = "out_dir")]
    out: Option<String>,
    /// Output directory (default: beside the STEP input)
    #[arg(long)]
    out_dir: Option<String>,
}

pub(super) fn is_bare(args: &[std::ffi::OsString]) -> bool {
    let Some(first) = args.get(1) else {
        return false;
    };
    let first = first.to_string_lossy();
    let mut command = Cli::command();
    command.build(); // Clap adds its help subcommand during build.
    if command.get_subcommands().any(|c| c.get_name() == first) {
        return false;
    }
    // Top-level help and version still describe the subcommand interface. Other leading
    // options may precede the bare filename, as in Python's CLI.
    !matches!(first.as_ref(), "--help" | "-h" | "--version" | "-V")
}

pub(super) fn parse(args: Vec<std::ffi::OsString>) -> Result<Given, clap::Error> {
    let bare = Bare::try_parse_from(args).map_err(|mut error| {
        if let Some(clap::error::ContextValue::String(option)) =
            error.get(clap::error::ContextKind::InvalidArg)
            && option.starts_with('-')
        {
            let option = option.split('=').next().unwrap_or(option);
            let hint = match option {
                "--document-type" => "use render --spec with sheet.title.document_type",
                "--sheet" => "use render --spec with sheet.title.sheet",
                "--projection" => "use render --spec with sheet.projection",
                "--zones" => "use render --spec with sheet.zones",
                "--page" => "use render --spec with sheet.size (A4..A0)",
                "--pmi" => "use render --spec with pmi (first, strict or ignore)",
                "--format" | "-f" => "use render --formats svg,pdf,png; DXF is disabled",
                "--install-completion" | "--show-completion" => {
                    "use draftwright completions <shell>"
                }
                _ => "there is no Rust equivalent for this option",
            };
            error.insert(
                clap::error::ContextKind::Suggested,
                clap::error::ContextValue::StyledStrs(vec![
                    format!("Python option {option} is refused: {hint}").into(),
                ]),
            );
        }
        error
    })?;
    Ok(Given {
        positionals: vec![bare.step],
        output: bare.out,
        out_dir: bare.out_dir,
        sheet: bare.sheet,
        pictorial: bare.pictorial,
        spec: bare.spec,
        bare: true,
        ..Given::default()
    })
}

pub(super) fn output(step: &str, out: Option<&str>, out_dir: Option<&str>) -> PathBuf {
    let source = Path::new(step);
    let mut prefix = match (out, out_dir) {
        (Some(out), _) => PathBuf::from(out),
        (_, Some(dir)) => Path::new(dir).join(source.file_stem().unwrap_or_default()),
        _ => source.with_extension(""),
    };
    if prefix
        .extension()
        .and_then(|e| e.to_str())
        .is_some_and(|e| matches!(e, "svg" | "pdf" | "png" | "dxf"))
    {
        prefix.set_extension("");
    }
    prefix
}

#[cfg(test)]
mod tests {
    use super::*;

    fn args(values: &[&str]) -> Vec<std::ffi::OsString> {
        std::iter::once("draftwright")
            .chain(values.iter().copied())
            .map(Into::into)
            .collect()
    }

    #[test]
    fn each_common_option_authors_the_same_spec_as_render() {
        for (option, key) in [
            ("--title", "title"),
            ("--number", "number"),
            ("--material", "material"),
            ("--drawn-by", "drawn_by"),
            ("--revision", "revision"),
            ("--date", "date"),
            ("--company", "company"),
            ("--approved-by", "approved_by"),
            ("--tolerance", "tolerance"),
        ] {
            let bare = Bare::try_parse_from(args(&["part.step", option, "value"])).unwrap();
            let render =
                Cli::try_parse_from(args(&["render", "part.step", "-o", "out", option, "value"]))
                    .unwrap();
            let super::super::Command::Render { sheet, .. } = render.command else {
                panic!("render")
            };
            let mut a = Spec::default();
            let mut b = Spec::default();
            bare.sheet.apply(&mut a).unwrap();
            sheet.apply(&mut b).unwrap();
            assert_eq!(a, b, "{option}");
            assert_eq!(a.sheet.title[key], "value", "{option}");
        }
        for (value, expected) in [
            ("5", "5:1"),
            ("0.5", "1:2"),
            ("0.8", "1:1.25"),
            ("2.5", "2.5:1"),
            ("0.3333333333333333", "1:3"),
            ("0.00001", "1:100000"),
        ] {
            let bare = Bare::try_parse_from(args(&["part.step", "--scale", value])).unwrap();
            let mut spec = Spec::default();
            bare.sheet.apply(&mut spec).unwrap();
            assert_eq!(spec.sheet.scale.as_deref(), Some(expected));
        }
        for value in ["0", "-1", "NaN", "inf"] {
            let bare = Bare::try_parse_from(args(&["part.step", "--scale", value])).unwrap();
            assert!(bare.sheet.apply(&mut Spec::default()).is_err());
        }
        let bare = Bare::try_parse_from(args(&["part.step", "--frame"])).unwrap();
        assert!(bare.sheet.frame);
        let mut spec = Spec::default();
        bare.sheet.apply(&mut spec).unwrap();
        assert_eq!(spec, Spec::default()); // The renderer always frames this Spec.
    }

    #[test]
    fn scales_that_need_long_or_infinite_ratios_are_refused() {
        for scale in [0.3, 0.14, 1.1234567, 1e-320] {
            let error = scale_ratio(scale).unwrap_err();
            assert!(error.message.contains("clean finite"), "{error:?}");
            assert!(error.message.contains("six decimal places"), "{error:?}");
        }
    }

    #[test]
    fn destinations_keep_python_prefix_rules() {
        for (values, expected) in [
            (vec!["dir/part.step"], "dir/part"),
            (vec!["dir/part.step", "--out", "drawing.pdf"], "drawing"),
            (vec!["dir/part.step", "--out-dir", "output"], "output/part"),
        ] {
            let bare = Bare::try_parse_from(args(&values)).unwrap();
            assert_eq!(
                output(&bare.step, bare.out.as_deref(), bare.out_dir.as_deref()),
                PathBuf::from(expected)
            );
        }
        assert!(
            Bare::try_parse_from(args(&["part.step", "--out", "a", "--out-dir", "b"])).is_err()
        );
    }

    #[test]
    fn subcommands_always_win_and_dot_slash_selects_a_path() {
        let mut built = Cli::command();
        built.build();
        assert!(
            built
                .get_subcommands()
                .any(|command| command.get_name() == "help")
        );
        assert!(!is_bare(&args(&["help"])));
        for command in built.get_subcommands() {
            assert!(!is_bare(&args(&[command.get_name()])));
            assert!(is_bare(&args(&[&format!("./{}", command.get_name())])));
        }
        assert!(is_bare(&args(&["part.step"])));
        assert!(is_bare(&args(&["--title", "Part", "part.step"])));
    }
}
