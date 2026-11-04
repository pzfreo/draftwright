//! Lint on Python draftwright's captured sheets (`tests/fixtures/sheets/`), against the findings
//! Python's own lint made on them, and on the port's own drawings of the same parts.
//!
//! The port's lint judges a drawing from what it prints and claims, against the Analysis. Here
//! the drawing is Python's: each captured annotation becomes a mark (its PDF text runs, and the
//! measurements its registry says it draws), and the Analysis is the part read with the same
//! Python quiddity recognition draftwright drew from (`tests/fixtures/recognition/sheets/`,
//! written by `tools/recognise.py` with draftwright's quiddity 0.3.9).
//!
//! Python names a measured feature by its geometry (`hole@(x,y,z)/axis[sizes]`, or
//! `pattern@(centre)/axis`), so each is joined to the Analysis exactly, and only when exactly one
//! hole (or one hole group) fits: the same axis, the opening on its axis line within 0.01 mm and
//! within its length, the same diameter. The other families lint checks by their own sizes
//! (bosses, polygonal bosses and stock, chamfers, fillets, blends, through steps, slots, …:
//! [`FAMILIES`]) are joined the same way, by kind, axis,
//! sizes and the record's point ([`join_feature`]). A claim that fits none or several is dropped
//! and counted (LINT-_coverage_common-exact_correspondence: never guess).
//!
//! Every difference between the two lints' findings must be listed, with a verdict and the
//! reason, in `tests/fixtures/known_lint.json` (`python_sheets`); a listed difference that has
//! gone away must be taken off the list. What lint finds on the port's own drawings is listed
//! there too (`own_drawings`), by part and code. The parts are read from the corpus
//! (`DRAFTWRIGHT_CORPUS`, by default `~/repos/quiddity/tests/corpus`).

#[path = "common/corpus.rs"]
mod corpus;
#[path = "common/facts.rs"]
mod facts;

use std::collections::{BTreeMap, BTreeSet};
use std::io::Read as _;
use std::path::{Path, PathBuf};

use serde_json::{Value, json};

use draftwright::lint::holes::{HoleGroup, groups};
use draftwright::lint::{self, Mark, envelope, turned};
use draftwright::model::{self, Analysis};
use draftwright::output::Claim;
use serde::Deserialize;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Known {
    part: String,
    /// The hole group's first member, its description, and the requirement (`*` for all).
    subject: String,
    group: String,
    parameter: String,
    /// Each side's finding code, or none.
    python: Option<String>,
    rust: Option<String>,
    /// `rust-correct`, `rust-wrong`, `equivalent`, `undetermined` or `not-applicable`.
    verdict: String,
    reason: String,
}

fn fixture(rel: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(rel)
}

/// The part with its features, from the recorded recognition, or why there is none to compare.
/// The bridge reads ctc04 since its face check took OpenCascade's default-precision sphere areas
/// for what they are (`known_bridge_faces.json`), but its lint differences are not listed yet, so
/// it is not compared: its listed `analysis` entries stand for that until they are.
fn analysis(corpus: &Path, name: &str, file: &str) -> Result<Analysis, String> {
    if name == "ctc04" {
        return Err("not compared yet: the bridge reads it, its differences are not listed".into());
    }
    let step = corpus.join(file);
    let (part, mut analysis) = model::analyse(&step).unwrap();
    let mut json = String::new();
    flate2::read::GzDecoder::new(
        std::fs::File::open(fixture(&format!("recognition/sheets/{name}.json.gz"))).unwrap(),
    )
    .read_to_string(&mut json)
    .unwrap();
    let (recognition, features) =
        draftwright::bridge::read(&part, &analysis.binding.source_sha256, &json)
            .map_err(|e| format!("the bridge refuses the recognition: {e}"))?;
    analysis.recognition = Some(recognition);
    analysis.features = features;
    Ok(analysis)
}

fn dot(a: [f64; 3], b: [f64; 3]) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

fn sub(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}

/// How far *p* is from the line through *c* along the unit *axis*, across it and along it.
fn offsets(p: [f64; 3], c: [f64; 3], axis: [f64; 3]) -> (f64, f64) {
    let d = sub(p, c);
    let along = dot(d, axis);
    let across = (dot(d, d) - along * along).max(0.0).sqrt();
    (across, along.abs())
}

fn letter(axis: &str) -> Option<[f64; 3]> {
    match axis {
        "x" => Some([1.0, 0.0, 0.0]),
        "y" => Some([0.0, 1.0, 0.0]),
        "z" => Some([0.0, 0.0, 1.0]),
        _ => None,
    }
}

/// A hole's length along its axis: the bore and any counterbore, spotface or countersink (a
/// drill point's cone is not in the depth; [`reaches`] allows for it).
fn length(f: &draftwright::bridge::Feature) -> f64 {
    let p = &f.parameters;
    let get = |k: &str, j: &str| {
        p.get(k)
            .and_then(|v| v.get(j))
            .and_then(|v| v.as_f64())
            .unwrap_or(0.0)
    };
    p.get("depth").and_then(|v| v.as_f64()).unwrap_or(0.0)
        + get("cbore", "depth")
        + get("spotface", "depth")
        + get("csink", "depth")
}

/// How far a hole's opening can be from its centre (the centre of its faces' box) along its
/// axis: half its length, and half its diameter for a drill point's cone.
fn reaches(f: &draftwright::bridge::Feature) -> f64 {
    let d = f
        .parameters
        .get("diameter")
        .and_then(|v| v.as_f64())
        .unwrap_or(0.0);
    0.5 * (length(f) + d) + 0.01
}

/// The Analysis holes a Python feature names, when exactly one hole (or one group) fits.
fn join(
    analysis: &Analysis,
    groups: &[HoleGroup],
    feature: &facts::Feature,
    value: Option<f64>,
) -> Option<Vec<String>> {
    let axis = letter(feature.axis.as_deref()?)?;
    let origin = feature.origin?;
    let parallel = |a: Option<[f64; 3]>| a.is_some_and(|a| dot(a, axis).abs() > 1.0 - 1e-6);
    match feature.kind.as_str() {
        "hole" => {
            let key = feature.key.as_deref()?;
            let diameter: f64 = key
                .split("diameter=")
                .nth(1)?
                .split([',', ']'])
                .next()?
                .parse()
                .ok()?;
            let fits: Vec<&draftwright::bridge::Feature> = analysis
                .features
                .iter()
                .filter(|f| f.kind == "hole" && parallel(f.fingerprint.axis))
                .filter(|f| {
                    let d = f.parameters.get("diameter").and_then(|v| v.as_f64());
                    d.is_some_and(|d| (d - diameter).abs() <= 1e-3)
                })
                .filter(|f| {
                    let (across, along) = offsets(origin, f.fingerprint.position, axis);
                    across <= 0.01 && along <= reaches(f)
                })
                .collect();
            (fits.len() == 1).then(|| vec![fits[0].id.clone()])
        }
        "pattern" => {
            let fits: Vec<&HoleGroup> = groups
                .iter()
                .filter(|g| g.members.len() > 1 && parallel(g.axis))
                .filter(|g| value.is_none_or(|v| (v - g.sizes.diameter).abs() <= 1e-3))
                .filter(|g| {
                    let (across, along) = offsets(origin, g.centre(), axis);
                    let half = g
                        .members
                        .iter()
                        .filter_map(|m| analysis.features.iter().find(|f| &f.id == m))
                        .map(reaches)
                        .fold(0.0, f64::max);
                    across <= 0.01 && along <= half + 0.01
                })
                .collect();
            (fits.len() == 1).then(|| fits[0].members.clone())
        }
        _ => None,
    }
}

/// The parameters hole coverage reads.
fn sized(parameter: &str) -> bool {
    ["bore.", "counterbore.", "spotface.", "countersink."]
        .iter()
        .any(|p| parameter.starts_with(p))
}

/// The feature families lint judges by a feature's own sizes, by Python's kind, with the
/// parameters it checks.
const FAMILIES: [(&str, &[&str]); 13] = [
    ("boss", &["boss_height.length"]),
    (
        "polygonal_boss",
        &["polygon_across_flats.length", "boss_height.length"],
    ),
    (
        "polygonal_stock",
        &["polygon_across_flats.length", "stock_length.length"],
    ),
    ("flat", &["flat.length"]),
    ("groove", &["groove.length", "groove.diameter"]),
    ("chamfer", &["chamfer.length"]),
    ("fillet", &["fillet.radius"]),
    ("blend", &["blend.radius"]),
    ("paired_ramp_step", &["ramp_angle.angle", "ramp_run.length"]),
    (
        "circular_blind_step",
        &["circular_step_radius.radius", "circular_step_depth.length"],
    ),
    (
        "through_step",
        &[
            "through_step_leg.length.x",
            "through_step_leg.length.y",
            "through_step_leg.length.z",
        ],
    ),
    (
        "pocket",
        &[
            "pocket_width.length",
            "pocket_length.length",
            "pocket_depth.length",
            "pocket_max_depth.length",
            "location_pocket.location.x",
            "location_pocket.location.y",
            "location_pocket.x",
            "location_pocket.y",
            "location_pocket.z",
        ],
    ),
    (
        "slot",
        &[
            "slot_width.length",
            "slot_length.length",
            "slot_end_radius.radius",
            "location_slot.length",
        ],
    ),
];

/// The codes of the section recess dispositions, reported whatever the sheet says.
const DISPOSITIONS: [&str; 4] = [
    "passage_requirement_unsupported",
    "prismatic_pocket_requirement_unsupported",
    "section_recess_requirement_unsupported",
    "section_recess_recognition_refused",
];

/// Whether lint checks *parameter* of a Python feature of *kind* (outside hole coverage).
fn checked(kind: &str, parameter: &str) -> bool {
    FAMILIES
        .iter()
        .any(|(k, ps)| *k == kind && ps.contains(&parameter))
}

/// A feature record's point in quiddity's frame that Python names it by: a fillet's, chamfer's,
/// through step's, groove's or flat's `at`, a blend path's `at` or `center`, a boss's `location`,
/// a polygonal boss's or stock's `center`, a slot's centre
/// (quiddity's `Slot.location`: mid-span along its long axis, its centreline across, mid-depth).
fn local_point(f: &draftwright::bridge::Feature) -> Option<[f64; 3]> {
    let p = &f.parameters;
    if f.kind == "slot" {
        let get = |k: &str| p.get(k).and_then(|v| v.as_f64());
        let axis = |k: &str| {
            p.get(k)
                .and_then(|v| v.as_str())
                .and_then(|a| ["x", "y", "z"].iter().position(|x| *x == a))
        };
        let (long, width) = (axis("long_axis")?, axis("width_axis")?);
        let mut c = [0.0; 3];
        c[long] = (get("lo")? + get("hi")?) / 2.0;
        c[width] = get("w_center")?;
        c[3 - long - width] = (get("d_lo")? + get("d_hi")?) / 2.0;
        return Some(c);
    }
    let path = p.get("path");
    [
        p.get("at"),
        p.get("location"),
        p.get("center"),
        path.and_then(|v| v.get("at")),
        path.and_then(|v| v.get("center")),
    ]
    .into_iter()
    .find_map(|v| draftwright::bridge::holes::point(v))
}

/// The Analysis feature a Python feature of a [`FAMILIES`] kind names, when exactly one fits: the
/// same kind; its direction's largest component along Python's axis letter; every size Python's
/// key gives (`radius=`, `width=`, …) within 1.5e-3 of the record's; and Python's origin at the
/// record's point (all three coordinates within 2e-3: quiddity rounds to 1e-3). Python places a
/// boss elsewhere on its axis (its top or its base), so a boss's origin need only be on the
/// boss's axis line within its height; and a slot's only along its long axis (within 0.01 mm,
/// quiddity's slot records being rounded to 0.01), Python zeroing some of its other coordinates.
fn join_feature(analysis: &Analysis, feature: &facts::Feature) -> Option<String> {
    use draftwright::bridge::holes::{frame, to_part};
    use draftwright::lint::parameters::direction;
    let frame = frame(analysis);
    let axis = ["x", "y", "z"]
        .iter()
        .position(|a| Some(*a) == feature.axis.as_deref())?;
    let origin = feature.origin?;
    let key = feature.key.as_deref()?;
    let sizes: Vec<(&str, f64)> = key
        .rsplit_once('[')
        .map(|(_, s)| s.trim_end_matches(']'))
        .unwrap_or("")
        .split(',')
        .filter_map(|kv| kv.split_once('='))
        .map(|(k, v)| Some((k, v.parse().ok()?)))
        .collect::<Option<_>>()?;
    let fits: Vec<&draftwright::bridge::Feature> = analysis
        .features
        .iter()
        .filter(|f| f.kind == feature.kind)
        .filter(|f| {
            let p = &f.parameters;
            let path = p.get("path");
            let d = direction(&frame, p.get("axis"))
                .or_else(|| direction(&frame, p.get("long_axis")))
                .or_else(|| direction(&frame, path.and_then(|v| v.get("direction"))))
                .or_else(|| direction(&frame, path.and_then(|v| v.get("normal"))));
            d.is_some_and(|d| {
                (0..3)
                    .max_by(|&i, &j| d[i].abs().total_cmp(&d[j].abs()))
                    .unwrap()
                    == axis
            })
        })
        .filter(|f| {
            sizes.iter().all(|(k, v)| {
                f.parameters
                    .get(*k)
                    .and_then(|x| x.as_f64())
                    .is_some_and(|x| (x - v).abs() <= 1.5e-3)
            })
        })
        .filter(|f| {
            local_point(f).is_some_and(|l| {
                let at = to_part(&frame, l);
                match f.kind.as_str() {
                    "boss" => {
                        let d = direction(&frame, f.parameters.get("axis")).unwrap();
                        let n = dot(d, d).sqrt();
                        let (across, along) = offsets(origin, at, d.map(|c| c / n));
                        let height = f.parameters.get("height").and_then(|v| v.as_f64());
                        across <= 2e-3 && height.is_some_and(|h| along <= h + 2e-3)
                    }
                    "slot" => (at[axis] - origin[axis]).abs() <= 1e-2,
                    _ => (0..3).all(|k| (at[k] - origin[k]).abs() <= 2e-3),
                }
            })
        })
        .collect();
    (fits.len() == 1).then(|| fits[0].id.clone())
}

/// The Analysis slot pattern a Python `slot_pattern` names, when exactly one fits: cut along z
/// (Python's axis letter; lint checks no other), with its centre, as lint locates it
/// ([`lint::slots::pattern_wanted`]), at Python's origin in x and y (within 2e-3).
fn join_slot_pattern(analysis: &Analysis, feature: &facts::Feature) -> Option<String> {
    let origin = feature.origin?;
    if feature.axis.as_deref() != Some("z") {
        return None;
    }
    let fits: Vec<&draftwright::bridge::Feature> = analysis
        .features
        .iter()
        .filter(|f| f.kind == "slot_pattern")
        .filter(|p| {
            lint::slots::pattern_wanted(analysis, p).is_ok_and(|wanted| {
                ["x", "y"].iter().enumerate().all(|(k, axis)| {
                    wanted
                        .iter()
                        .find(|w| w.parameter == format!("location_slot_pattern.length.{axis}"))
                        .and_then(|w| w.values.as_ref().ok())
                        .is_some_and(|v| (analysis.envelope[0][k] + v[0] - origin[k]).abs() <= 2e-3)
                })
            })
        })
        .collect();
    (fits.len() == 1).then(|| fits[0].id.clone())
}

/// The Analysis section recess a Python `pocket` names, when exactly one fits: read as a pocket in
/// the part's frame ([`lint::pockets::placed`]), cut along Python's axis letter, of Python's
/// depth, width and length (within 1.5e-3), centred at Python's origin (within 2e-3).
fn join_pocket(analysis: &Analysis, feature: &facts::Feature) -> Option<String> {
    let origin = feature.origin?;
    let key = feature.key.as_deref()?;
    let size = |k: &str| -> Option<f64> {
        key.split(&format!("{k}="))
            .nth(1)?
            .split([',', ']'])
            .next()?
            .parse()
            .ok()
    };
    let (depth, width, length) = (size("depth")?, size("width")?, size("length")?);
    let fits: Vec<String> = lint::pockets::placed(analysis)
        .into_iter()
        .filter_map(|(f, p)| Some((f, p.ok()?)))
        .filter(|(_, p)| {
            Some(["x", "y", "z"][p.depth_axis]) == feature.axis.as_deref()
                && (p.depth - depth).abs() <= 1.5e-3
                && (p.width - width).abs() <= 1.5e-3
                && (p.length - length).abs() <= 1.5e-3
                && (0..3).all(|k| (p.centre[k] - origin[k]).abs() <= 2e-3)
        })
        .map(|(f, _)| f.id.clone())
        .collect();
    (fits.len() == 1).then(|| fits[0].clone())
}

/// The Analysis section recess a Python `channel` names, when exactly one fits: read as a channel
/// in the part's frame ([`lint::channels::placed`]), of Python's width (within 1.5e-3), centred at
/// Python's origin (within 2e-3).
fn join_channel(analysis: &Analysis, width: Option<f64>, at: [f64; 3]) -> Option<String> {
    let fits: Vec<String> = lint::channels::placed(analysis)
        .into_iter()
        .filter_map(|(f, c)| Some((f, c.ok()?)))
        .filter(|(_, c)| {
            width.is_none_or(|w| (c.width - w).abs() <= 1.5e-3)
                && (0..3).all(|k| (c.centre[k] - at[k]).abs() <= 2e-3)
        })
        .map(|(f, _)| f.id.clone())
        .collect();
    (fits.len() == 1).then(|| fits[0].clone())
}

/// The Analysis section recess a Python `hex_pocket` names, when exactly one fits: read as a
/// hexagonal pocket along Python's axis letter, of Python's depth (within 1.5e-3), its mouth's
/// centre across the axis at Python's origin (within 2e-3; Python zeroes the coordinate along).
fn join_hex(
    analysis: &Analysis,
    axis: Option<&str>,
    depth: Option<f64>,
    at: [f64; 3],
) -> Option<String> {
    let fits: Vec<String> = lint::pockets::hexes(analysis)
        .into_iter()
        .filter(|h| {
            h.placed.as_ref().is_ok_and(|(k, mouth)| {
                axis.is_none_or(|a| a == ["x", "y", "z"][*k])
                    && (0..3).all(|i| i == *k || (mouth[i] - at[i]).abs() <= 2e-3)
            }) && depth.is_none_or(|d| (h.depth - d).abs() <= 1.5e-3)
        })
        .map(|h| h.feature.id.clone())
        .collect();
    (fits.len() == 1).then(|| fits[0].clone())
}

/// The Analysis section recess a Python `circular_channel` names, when exactly one fits: read as
/// a circular seat along Python's axis letter, of Python's radius and length (within 1.5e-3), its
/// anchor at Python's origin (within 2e-3).
fn join_seat(
    analysis: &Analysis,
    axis: Option<&str>,
    sizes: Option<(f64, f64)>,
    at: [f64; 3],
) -> Option<String> {
    let fits: Vec<String> = lint::channels::seats(analysis)
        .into_iter()
        .filter_map(|(f, s)| Some((f, s.ok()?)))
        .filter(|(_, s)| {
            axis.is_none_or(|a| a == ["x", "y", "z"][s.run_axis])
                && sizes.is_none_or(|(r, l)| {
                    (s.radius - r).abs() <= 1.5e-3 && (s.length - l).abs() <= 1.5e-3
                })
                && (0..3).all(|k| (s.anchor[k] - at[k]).abs() <= 2e-3)
        })
        .map(|(f, _)| f.id.clone())
        .collect();
    (fits.len() == 1).then(|| fits[0].clone())
}

/// A size `name=` in a Python feature key.
fn key_size(feature: &facts::Feature, name: &str) -> Option<f64> {
    feature
        .key
        .as_deref()?
        .split(&format!("{name}="))
        .nth(1)?
        .split([',', ']'])
        .next()?
        .parse()
        .ok()
}

/// The Analysis plate a Python `plate` thickness names, when exactly one fits: Python's
/// compiled span (draftwright's `plate_span`: the slab's two faces along its axis, at quiddity's
/// `u`, `v` across it) is the plate's, carried into the part's frame, end for end (within 2e-3).
/// (Python's plate key names the part's centre, not the plate's.)
fn join_plate(analysis: &Analysis, span: [[f64; 3]; 2]) -> Option<String> {
    use draftwright::bridge::holes::{frame, to_part};
    let frame = frame(analysis);
    let near = |a: [f64; 3], b: [f64; 3]| (0..3).all(|k| (a[k] - b[k]).abs() <= 2e-3);
    let fits: Vec<&draftwright::bridge::Feature> = analysis
        .features
        .iter()
        .filter(|f| f.kind == "plate")
        .filter(|f| {
            let p = &f.parameters;
            let get = |k: &str| p.get(k).and_then(|v| v.as_f64());
            let Some(a) = ["x", "y", "z"]
                .iter()
                .position(|a| p.get("axis").and_then(|v| v.as_str()) == Some(*a))
            else {
                return false;
            };
            let (Some(lo), Some(hi), Some(u), Some(v)) = (get("lo"), get("hi"), get("u"), get("v"))
            else {
                return false;
            };
            let others: Vec<usize> = (0..3).filter(|&k| k != a).collect();
            let end = |t: f64| {
                let mut c = [0.0; 3];
                c[a] = t;
                c[others[0]] = u;
                c[others[1]] = v;
                to_part(&frame, c)
            };
            let (l, h) = (end(lo), end(hi));
            (near(l, span[0]) && near(h, span[1])) || (near(l, span[1]) && near(h, span[0]))
        })
        .collect();
    (fits.len() == 1).then(|| fits[0].id.clone())
}

/// The Analysis turned step a Python `step` names, when exactly one fits: its band along Python's
/// axis letter, of Python's diameter and length (within 1.5e-3), with its axial midpoint at
/// Python's origin (within 2e-3).
fn join_step(analysis: &Analysis, feature: &facts::Feature) -> Option<String> {
    use draftwright::bridge::holes::frame;
    let frame = frame(analysis);
    let axis = ["x", "y", "z"]
        .iter()
        .position(|a| Some(*a) == feature.axis.as_deref())?;
    let origin = feature.origin?;
    let size = |k: &str| -> Option<f64> {
        feature
            .key
            .as_deref()?
            .split(&format!("{k}="))
            .nth(1)?
            .split([',', ']'])
            .next()?
            .parse()
            .ok()
    };
    let (diameter, length) = (size("diameter")?, size("length")?);
    let fits: Vec<&draftwright::bridge::Feature> = analysis
        .features
        .iter()
        .filter(|f| f.kind == "turned_step")
        .filter(|f| {
            turned::band(&frame, f).is_ok_and(|b| {
                let mut mid = b.on;
                mid[b.axis] = (b.lo + b.hi) / 2.0;
                b.axis == axis
                    && (b.diameter - diameter).abs() <= 1.5e-3
                    && (b.hi - b.lo - length).abs() <= 1.5e-3
                    && (0..3).all(|k| (mid[k] - origin[k]).abs() <= 2e-3)
            })
        })
        .collect();
    (fits.len() == 1).then(|| fits[0].id.clone())
}

/// Python's sheet as marks, and how many of its claims that lint reads (hole sizes, the
/// [`FAMILIES`]' sizes, and the ledgers' extents, step lengths and diameters) could not be
/// joined.
fn marks(analysis: &Analysis, groups: &[HoleGroup], f: &facts::Facts) -> (Vec<Mark>, usize) {
    let mut unjoined = 0;
    let marks = f
        .annotations
        .iter()
        .map(|a| {
            let mut claims = Vec::new();
            for m in &a.measurements {
                let Some(feature) = &m.feature else { continue };
                // The ledgers' evidence: the overall extents (Python's `rotational` OD is the
                // port's `envelope` one), the turned steps' lengths and diameters, and the
                // bosses' diameters.
                let ledger = match (feature.kind.as_str(), m.parameter.as_str()) {
                    ("envelope", p) if envelope::PARAMETERS.contains(&p) => {
                        Some(Some("envelope".to_string()))
                    }
                    ("rotational", "od.diameter") => Some(Some("envelope".to_string())),
                    ("step", "step.length" | "step.diameter") => Some(join_step(analysis, feature)),
                    ("boss", "boss.diameter") => Some(join_feature(analysis, feature)),
                    _ => None,
                };
                if let Some(joined) = ledger {
                    match joined {
                        Some(id) => claims.push(Claim {
                            feature: id,
                            parameter: m.parameter.clone(),
                        }),
                        None => unjoined += 1,
                    }
                    continue;
                }
                if (feature.kind.as_str(), m.parameter.as_str())
                    == ("channel", "channel_width.length")
                    || (feature.kind.as_str(), m.parameter.as_str())
                        == ("plate", "thickness.length")
                {
                    let joined = match feature.kind.as_str() {
                        "channel" => feature.origin.and_then(|at| {
                            let width = feature.key.as_deref().and_then(|k| {
                                k.split("width=")
                                    .nth(1)?
                                    .split([',', ']'])
                                    .next()?
                                    .parse()
                                    .ok()
                            });
                            join_channel(analysis, width, at)
                        }),
                        _ => m
                            .compiled
                            .as_ref()
                            .and_then(|c| c.first())
                            .and_then(|c| {
                                let span = c.get("span")?.as_array()?;
                                let point =
                                    |i: usize| draftwright::bridge::holes::point(span.get(i));
                                Some([point(0)?, point(1)?])
                            })
                            .and_then(|span| join_plate(analysis, span)),
                    };
                    match joined {
                        Some(id) => claims.push(Claim {
                            feature: id,
                            parameter: m.parameter.clone(),
                        }),
                        None => unjoined += 1,
                    }
                    continue;
                }
                if ["hex_pocket", "circular_channel"].contains(&feature.kind.as_str()) {
                    let joined = feature.origin.and_then(|at| {
                        if feature.kind == "hex_pocket" {
                            join_hex(
                                analysis,
                                feature.axis.as_deref(),
                                key_size(feature, "depth"),
                                at,
                            )
                        } else {
                            let sizes =
                                key_size(feature, "radius").zip(key_size(feature, "length"));
                            join_seat(analysis, feature.axis.as_deref(), sizes, at)
                        }
                    });
                    match joined {
                        Some(id) => claims.push(Claim {
                            feature: id,
                            parameter: m.parameter.clone(),
                        }),
                        None => unjoined += 1,
                    }
                    continue;
                }
                if feature.kind == "pocket" {
                    // A Z pocket's two locations are one structured fact on Python's sheet,
                    // claimed by both of its location dimensions; here each is a requirement.
                    let parameters: Vec<String> = if m.parameter == "location_pocket.location" {
                        vec![
                            "location_pocket.location.x".into(),
                            "location_pocket.location.y".into(),
                        ]
                    } else {
                        vec![m.parameter.clone()]
                    };
                    match join_pocket(analysis, feature) {
                        Some(id) => claims.extend(parameters.into_iter().map(|parameter| Claim {
                            feature: id.clone(),
                            parameter,
                        })),
                        None => unjoined += 1,
                    }
                    continue;
                }
                if feature.kind == "slot_pattern" {
                    // Python names a Z pattern's locations `location_slot_pattern.location.<axis>`;
                    // the port's dimensions claim them as `location_slot_pattern.length.<axis>`.
                    let parameter = m
                        .parameter
                        .strip_prefix("location_slot_pattern.location.")
                        .map_or(m.parameter.clone(), |axis| {
                            format!("location_slot_pattern.length.{axis}")
                        });
                    match join_slot_pattern(analysis, feature) {
                        Some(id) => claims.push(Claim {
                            feature: id,
                            parameter,
                        }),
                        None => unjoined += 1,
                    }
                    continue;
                }
                if checked(&feature.kind, &m.parameter) {
                    match join_feature(analysis, feature) {
                        Some(id) => claims.push(Claim {
                            feature: id,
                            parameter: m.parameter.clone(),
                        }),
                        None => unjoined += 1,
                    }
                    continue;
                }
                if !["hole", "pattern"].contains(&feature.kind.as_str()) || !sized(&m.parameter) {
                    continue;
                }
                let value = (m.parameter == "bore.diameter")
                    .then_some(m.value)
                    .flatten();
                match join(analysis, groups, feature, value) {
                    Some(ids) => claims.extend(ids.into_iter().map(|id| Claim {
                        feature: id,
                        parameter: m.parameter.clone(),
                    })),
                    None => unjoined += 1,
                }
            }
            Mark {
                name: a.name.clone(),
                view: a.view.clone().map(|v| format!("v:{v}")),
                text: a.text.join(" "),
                claims,
            }
        })
        .collect();
    (marks, unjoined)
}

/// The group a Python hole finding is about: its message names the quantity, the diameter and
/// a point on the holes' axis (or the pattern's), in the part's frame.
fn python_subject(analysis: &Analysis, groups: &[HoleGroup], message: &str) -> (usize, String) {
    let n: usize = message.split('×').next().unwrap().trim().parse().unwrap();
    let diameter: f64 = message
        .split('ø')
        .nth(1)
        .unwrap()
        .split(' ')
        .next()
        .unwrap()
        .parse()
        .unwrap();
    let at: Vec<f64> = message
        .split(" at (")
        .nth(1)
        .unwrap()
        .split(')')
        .next()
        .unwrap()
        .split(", ")
        .map(|c| c.parse().unwrap())
        .collect();
    let at = [at[0], at[1], at[2]];
    let parameter = match message.split(" requirement ").nth(1) {
        Some(rest) => rest.split(' ').next().unwrap().to_string(),
        None => "*".to_string(),
    };
    let fits: Vec<usize> = groups
        .iter()
        .enumerate()
        .filter(|(_, g)| g.members.len() == n && (g.sizes.diameter - diameter).abs() < 0.05)
        .filter(|(_, g)| {
            let axis = g.axis.unwrap();
            let reach = g
                .members
                .iter()
                .filter_map(|m| analysis.features.iter().find(|f| &f.id == m))
                .map(reaches)
                .fold(0.0, f64::max);
            // Python zeroes a loose through hole's coordinate along its axis.
            let anywhere = g.sizes.through && !g.pattern;
            let on = |c: [f64; 3]| {
                let (across, along) = offsets(at, c, axis);
                across <= 0.01 && (anywhere || along <= reach)
            };
            on(g.centre()) || g.centres.iter().any(|c| on(*c))
        })
        .map(|(i, _)| i)
        .collect();
    assert_eq!(fits.len(), 1, "{message}: {fits:?}");
    (fits[0], parameter)
}

/// The [`FAMILIES`] kind a finding code belongs to (`fillet_requirement_missing` → `fillet`).
fn family_of(code: &str) -> Option<&'static str> {
    if code == "boss_height_missing" {
        return Some("boss");
    }
    FAMILIES
        .iter()
        .map(|(k, _)| *k)
        .find(|k| code.starts_with(&format!("{k}_requirement_")))
}

/// The requirements a Python finding of a [`FAMILIES`] kind is about: its message names the
/// record's point in quiddity's frame (`fillet at (x, y, z) …`), which must be exactly one
/// feature's; the finding covers each of the parameters lint checks for that kind.
fn python_feature_subject(analysis: &Analysis, code: &str, message: &str) -> Vec<(String, String)> {
    let kind = family_of(code).unwrap();
    let at: Vec<f64> = message
        .split(" at (")
        .nth(1)
        .and_then(|rest| rest.split(')').next())
        .unwrap_or_else(|| panic!("{message}: no point to read"))
        .split(", ")
        .map(|c| c.parse().unwrap())
        .collect();
    let fits: Vec<&draftwright::bridge::Feature> = analysis
        .features
        .iter()
        .filter(|f| f.kind == kind)
        .filter(|f| local_point(f).is_some_and(|l| (0..3).all(|k| (l[k] - at[k]).abs() <= 1.5e-3)))
        .collect();
    assert_eq!(fits.len(), 1, "{message}: {} features fit", fits.len());
    let (_, parameters) = FAMILIES.iter().find(|(k, _)| *k == kind).unwrap();
    parameters
        .iter()
        .map(|p| (fits[0].id.clone(), p.to_string()))
        .collect()
}

/// A feature as a known difference describes it: its kind, sizes and centre (the centre of its
/// faces' box), in the part's frame.
fn describe_feature(analysis: &Analysis, id: &str) -> String {
    let f = analysis.features.iter().find(|f| f.id == id).unwrap();
    let sizes: Vec<String> = f
        .fingerprint
        .sizes
        .iter()
        .map(|(k, v)| format!("{k}={v}"))
        .collect();
    let c = f.fingerprint.position;
    format!(
        "{} [{}] at ({:.3}, {:.3}, {:.3})",
        f.kind,
        sizes.join(", "),
        c[0],
        c[1],
        c[2]
    )
}

#[test]
fn coverage_on_pythons_sheets_matches_pythons_lint_or_is_known() {
    let Some(corpus) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let known: Vec<Known> = serde_json::from_value(
        serde_json::from_str::<Value>(
            &std::fs::read_to_string(fixture("known_lint.json")).unwrap(),
        )
        .unwrap()["python_sheets"]
            .clone(),
    )
    .unwrap();
    let verdicts = [
        "rust-correct",
        "rust-wrong",
        "equivalent",
        "undetermined",
        "not-applicable",
    ];
    for k in &known {
        assert!(
            verdicts.contains(&k.verdict.as_str()) && !k.reason.is_empty(),
            "{k:?}"
        );
    }
    type Key = (String, String, String, Option<String>, Option<String>);
    let listed: BTreeSet<Key> = known
        .iter()
        .map(|k| {
            (
                k.part.clone(),
                k.subject.clone(),
                k.parameter.clone(),
                k.python.clone(),
                k.rust.clone(),
            )
        })
        .collect();
    assert_eq!(listed.len(), known.len(), "a difference is listed twice");

    let mut differ: BTreeMap<Key, String> = BTreeMap::new();
    let (mut agree, mut joined, mut unjoined_total, mut judged) = (0, 0, 0, 0);
    for name in facts::names() {
        let f = facts::load(&name);
        let analysis = match analysis(&corpus, &name, &f.binding.file) {
            Ok(a) => a,
            Err(e) => {
                // The whole sheet is one difference: the port cannot read the part's features.
                differ.insert((name.clone(), "analysis".into(), "*".into(), None, None), e);
                continue;
            }
        };
        assert_eq!(analysis.binding.source_sha256, f.binding.sha256, "{name}");
        let groups = groups(&analysis);
        let (marks, unjoined) = marks(&analysis, &groups, &f);
        unjoined_total += unjoined;
        joined += marks.iter().map(|m| m.claims.len()).sum::<usize>();
        let ours = lint::check(&analysis, &marks);
        judged += groups.iter().map(|g| g.requirements().len()).sum::<usize>();

        // Each side's findings by (group, requirement).
        let mut python: BTreeMap<(usize, String), String> = BTreeMap::new();
        for finding in f
            .lint
            .iter()
            .filter(|l| l.code.starts_with("hole_requirement_"))
        {
            python.insert(
                python_subject(&analysis, &groups, &finding.message),
                finding.code.clone(),
            );
        }
        let mut rust: BTreeMap<(usize, String), String> = BTreeMap::new();
        for issue in ours
            .iter()
            .filter(|i| i.code.starts_with("hole_requirement_"))
        {
            let g = groups
                .iter()
                .position(|g| g.members == issue.subjects)
                .unwrap();
            rust.insert(
                (g, issue.requirements[0].parameter.clone()),
                issue.code.clone(),
            );
        }
        let r = |x: f64| match format!("{x:.3}") {
            s if s == "-0.000" => "0.000".to_string(),
            s => s,
        };
        let describe = |g: usize| {
            let h = &groups[g];
            let c = h.centre();
            format!(
                "{}× ø{} {} at ({}, {}, {})",
                h.members.len(),
                h.sizes.diameter,
                if h.sizes.through { "THRU" } else { "blind" },
                r(c[0]),
                r(c[1]),
                r(c[2])
            )
        };
        let mut seen = BTreeSet::new();
        for ((g, parameter), code) in &python {
            seen.insert((*g, parameter.clone()));
            // Python's "all requirements" finding stands for each of the group's requirements
            // (those lint has ported: its locations are not yet).
            let theirs: Vec<(String, Option<&String>)> = if parameter == "*" {
                groups[*g]
                    .requirements()
                    .into_iter()
                    .map(|(p, _)| {
                        let r = rust.get(&(*g, p.clone()));
                        (p, r)
                    })
                    .collect()
            } else {
                vec![(parameter.clone(), rust.get(&(*g, parameter.clone())))]
            };
            for (p, r) in theirs {
                seen.insert((*g, p.to_string()));
                if r == Some(code) {
                    agree += 1;
                } else {
                    differ.insert(
                        (
                            name.clone(),
                            groups[*g].members[0].clone(),
                            p.to_string(),
                            Some(code.clone()),
                            r.cloned(),
                        ),
                        describe(*g),
                    );
                }
            }
        }
        for ((g, parameter), code) in &rust {
            if !seen.contains(&(*g, parameter.clone())) {
                differ.insert(
                    (
                        name.clone(),
                        groups[*g].members[0].clone(),
                        parameter.clone(),
                        None,
                        Some(code.clone()),
                    ),
                    describe(*g),
                );
            }
        }

        // The feature families: each side's findings by (feature, requirement).
        {
            use draftwright::lint::{
                blends, blind_slots, bosses, chamfers, circular_blind_steps, fillets, flats,
                grooves, paired_ramp_steps, pockets, polygonal, slots, steps,
            };
            judged += bosses::wanted(&analysis).len()
                + polygonal::boss_wanted(&analysis).len()
                + polygonal::stock_wanted(&analysis).len()
                + pockets::wanted(&analysis).len()
                + flats::wanted(&analysis).len()
                + grooves::wanted(&analysis).len()
                + chamfers::wanted(&analysis).len()
                + fillets::wanted(&analysis).len()
                + blends::wanted(&analysis).len()
                + paired_ramp_steps::wanted(&analysis).len()
                + circular_blind_steps::wanted(&analysis).len()
                + blind_slots::wanted(&analysis, false).len()
                + blind_slots::wanted(&analysis, true).len()
                + steps::wanted(&analysis).len()
                + slots::wanted(&analysis).len();
        }
        // Angled steps are reported whatever the sheet says, and name no requirement: the two
        // lints agree when they report as many.
        let code = "angled_step_requirement_unsupported";
        let (theirs, mine) = (
            f.lint.iter().filter(|l| l.code == code).count(),
            ours.iter().filter(|i| i.code == code).count(),
        );
        if theirs == mine {
            agree += theirs;
        } else {
            differ.insert(
                (
                    name.clone(),
                    "angled_steps".into(),
                    "*".into(),
                    Some(format!("{theirs}× {code}")),
                    Some(format!("{mine}× {code}")),
                ),
                "angled steps".into(),
            );
        }
        // Section recesses no grammar reads, and refused recess recognitions, are reported
        // whatever the sheet says, by Python's message: the two lints agree finding by finding.
        for code in DISPOSITIONS {
            let messages = |found: Vec<&String>| {
                let mut m: Vec<String> = found.into_iter().cloned().collect();
                m.sort();
                m
            };
            let theirs = messages(
                f.lint
                    .iter()
                    .filter(|l| l.code == code)
                    .map(|l| &l.message)
                    .collect(),
            );
            let mine = messages(
                ours.iter()
                    .filter(|i| i.code == code)
                    .map(|i| &i.message)
                    .collect(),
            );
            for m in &theirs {
                if mine.contains(m) {
                    agree += 1;
                }
            }
            for (side, list, other) in [(0, &theirs, &mine), (1, &mine, &theirs)] {
                for m in list.iter().filter(|m| !other.contains(m)) {
                    let (p, r) = if side == 0 {
                        (Some(m.clone()), None)
                    } else {
                        (None, Some(m.clone()))
                    };
                    differ.insert(
                        (name.clone(), "section_recesses".into(), code.into(), p, r),
                        "section recess disposition".into(),
                    );
                }
            }
        }
        // Hexagonal pockets and circular seats: Python names each by its measurement and its
        // point (the mouth's centre, or the seat's anchor, in the part's frame).
        for (prefix, noun) in [
            ("hex_pocket_requirement_", "hex pocket measurement "),
            (
                "circular_channel_requirement_",
                "circular seat measurement ",
            ),
        ] {
            let rust: BTreeMap<(String, String), String> = ours
                .iter()
                .filter(|i| i.code.starts_with(prefix))
                .map(|i| {
                    (
                        (i.subjects[0].clone(), i.requirements[0].parameter.clone()),
                        i.code.clone(),
                    )
                })
                .collect();
            let mut python: BTreeMap<(String, String), String> = BTreeMap::new();
            for finding in f.lint.iter().filter(|l| l.code.starts_with(prefix)) {
                let rest = finding
                    .message
                    .strip_prefix(noun)
                    .unwrap_or_else(|| panic!("{}: not read", finding.message));
                let (parameter, at) = rest.split_once(" at (").unwrap();
                let at: Vec<f64> = at
                    .split(')')
                    .next()
                    .unwrap()
                    .split(", ")
                    .map(|c| c.parse().unwrap())
                    .collect();
                let at = [at[0], at[1], at[2]];
                let id = if prefix.starts_with("hex") {
                    join_hex(&analysis, None, None, at)
                } else {
                    join_seat(&analysis, None, None, at)
                }
                .unwrap_or_else(|| panic!("{}: no one recess fits", finding.message));
                python.insert((id, parameter.to_string()), finding.code.clone());
            }
            for key in python.keys().chain(rust.keys()).collect::<BTreeSet<_>>() {
                let (theirs, mine) = (python.get(key), rust.get(key));
                if theirs == mine {
                    agree += 1;
                } else {
                    differ.insert(
                        (
                            name.clone(),
                            key.0.clone(),
                            key.1.clone(),
                            theirs.cloned(),
                            mine.cloned(),
                        ),
                        describe_feature(&analysis, &key.0),
                    );
                }
            }
        }
        judged += lint::pockets::hex_wanted(&analysis).len()
            + lint::channels::circular_wanted(&analysis).len();
        // Open channels: Python names each by its centre (in the part's frame) and the measurement
        // (`<kind>.<parameter>`); the port's findings by the channel and the requirement.
        {
            let rust: BTreeMap<(String, String), String> = ours
                .iter()
                .filter(|i| i.code.starts_with("channel_requirement_"))
                .map(|i| {
                    (
                        (i.subjects[0].clone(), i.requirements[0].parameter.clone()),
                        i.code.clone(),
                    )
                })
                .collect();
            let mut python: BTreeMap<(String, String), String> = BTreeMap::new();
            for finding in f
                .lint
                .iter()
                .filter(|l| l.code.starts_with("channel_requirement_"))
            {
                let at: Vec<f64> = finding
                    .message
                    .split(" at (")
                    .nth(1)
                    .and_then(|r| r.split(')').next())
                    .unwrap()
                    .split(", ")
                    .map(|c| c.parse().unwrap())
                    .collect();
                let id = join_channel(&analysis, None, [at[0], at[1], at[2]])
                    .unwrap_or_else(|| panic!("{}: no one channel fits", finding.message));
                let role = finding
                    .message
                    .split(" measurement ")
                    .nth(1)
                    .and_then(|r| r.split(' ').next())
                    .unwrap();
                let parameter = role.split_once('.').unwrap().1.to_string();
                python.insert((id, parameter), finding.code.clone());
            }
            judged += lint::channels::wanted(&analysis).len();
            for key in python.keys().chain(rust.keys()).collect::<BTreeSet<_>>() {
                let (theirs, mine) = (python.get(key), rust.get(key));
                if theirs == mine {
                    agree += 1;
                } else {
                    differ.insert(
                        (
                            name.clone(),
                            key.0.clone(),
                            key.1.clone(),
                            theirs.cloned(),
                            mine.cloned(),
                        ),
                        describe_feature(&analysis, &key.0),
                    );
                }
            }
        }
        let rust: BTreeMap<(String, String), String> = ours
            .iter()
            .filter(|i| family_of(&i.code).is_some())
            .map(|i| {
                (
                    (i.subjects[0].clone(), i.requirements[0].parameter.clone()),
                    i.code.clone(),
                )
            })
            .collect();
        let mut python: BTreeMap<(String, String), String> = BTreeMap::new();
        for finding in f.lint.iter().filter(|l| family_of(&l.code).is_some()) {
            if finding.code == "boss_height_missing" {
                // Python's one finding counts the bosses (`<n> boss height(s) are not
                // dimensioned`) without naming them: the port's per-boss findings agree when
                // there are as many.
                let n: usize = finding.message.split(' ').next().unwrap().parse().unwrap();
                let mine: Vec<&(String, String)> = rust
                    .iter()
                    .filter(|(_, c)| *c == "boss_height_missing")
                    .map(|(k, _)| k)
                    .collect();
                if mine.len() == n {
                    for k in mine {
                        python.insert(k.clone(), finding.code.clone());
                    }
                } else {
                    differ.insert(
                        (
                            name.clone(),
                            "bosses".into(),
                            "boss_height.length".into(),
                            Some(format!("{n}× {}", finding.code)),
                            Some(format!("{}× boss_height_missing", mine.len())),
                        ),
                        finding.message.clone(),
                    );
                    for k in mine {
                        python.insert(k.clone(), finding.code.clone());
                    }
                }
                continue;
            }
            for key in python_feature_subject(&analysis, &finding.code, &finding.message) {
                python.insert(key, finding.code.clone());
            }
        }
        for key in python.keys().chain(rust.keys()).collect::<BTreeSet<_>>() {
            let (theirs, mine) = (python.get(key), rust.get(key));
            if theirs == mine {
                agree += 1;
            } else {
                differ.insert(
                    (
                        name.clone(),
                        key.0.clone(),
                        key.1.clone(),
                        theirs.cloned(),
                        mine.cloned(),
                    ),
                    describe_feature(&analysis, &key.0),
                );
            }
        }
        if std::env::var_os("LINT_REPORT").is_some() {
            eprintln!(
                "== {name}: {} hole groups, {} marks claim sizes, {unjoined} claims unjoined",
                groups.len(),
                marks.iter().filter(|m| !m.claims.is_empty()).count()
            );
            for (i, g) in groups.iter().enumerate() {
                eprintln!("   group {i}: {} {:?}", describe(i), g.members);
            }
            for i in &ours {
                eprintln!("   rust {} {}", i.code, i.message);
            }
            for l in f
                .lint
                .iter()
                .filter(|l| l.code.starts_with("hole_requirement_") || family_of(&l.code).is_some())
            {
                eprintln!("   python {} {}", l.code, l.message);
            }
        }
    }
    let found: BTreeSet<Key> = differ.keys().cloned().collect();
    let unlisted: Vec<_> = differ.iter().filter(|(k, _)| !listed.contains(k)).collect();
    for k in &known {
        let key = (
            k.part.clone(),
            k.subject.clone(),
            k.parameter.clone(),
            k.python.clone(),
            k.rust.clone(),
        );
        if let Some(group) = differ.get(&key) {
            assert_eq!(
                &k.group, group,
                "{k:?}: the group's description has changed"
            );
        }
    }
    let stale: Vec<_> = listed.difference(&found).collect();
    eprintln!(
        "{judged} requirements judged: {agree} findings agree, {} differ ({} known), the \
         rest neither side reports; {joined} claims joined, {unjoined_total} not",
        differ.len(),
        known.len()
    );
    assert!(
        unlisted.is_empty() && stale.is_empty(),
        "differences not in known_lint.json: {unlisted:#?}\nlisted but no longer differing: {stale:#?}"
    );
}

/// A difference between the two sides' completeness ledgers, listed in `known_lint.json`
/// (`ledgers`).
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct KnownLedger {
    part: String,
    /// `envelope` or `turned_steps`.
    ledger: String,
    /// The envelope's axis, or the turned step's Analysis ID (its span when only Python has it).
    subject: String,
    parameter: String,
    python: Option<String>,
    rust: Option<String>,
    /// `rust-correct`, `rust-wrong`, `equivalent`, `undetermined` or `not-applicable`.
    verdict: String,
    reason: String,
}

/// A section recess's grammar as the port reads it, and the sizes and places draftwright's
/// `section_recess_fields` gives for that grammar (by its names; quiddity's frame), or the
/// grammar and why when none reads it (`unsupported: …`, `invalid: …`).
fn recess_reading(f: &draftwright::bridge::Feature) -> (String, BTreeMap<String, f64>) {
    use draftwright::lint::section_recesses::{Grammar, Refusal, grammar};
    let fields = |pairs: &[(&str, f64)]| -> BTreeMap<String, f64> {
        pairs.iter().map(|(k, v)| (k.to_string(), *v)).collect()
    };
    match grammar(f) {
        Ok(Grammar::Pocket(p)) => {
            let mut m = fields(&[
                ("width", p.width),
                ("length", p.length),
                ("depth", p.depth),
                ("w_center", p.w_center),
                ("lo", p.lo),
                ("hi", p.hi),
                ("open_sign", p.open_sign),
            ]);
            if let Some(r) = p.corner_radius {
                m.insert("corner_radius".into(), r);
            }
            if let Some((_, r, _)) = p.mouth {
                m.insert("mouth_radius".into(), r);
            }
            ("pocket".into(), m)
        }
        Ok(Grammar::Channel(c)) => (
            "channel".into(),
            fields(&[
                ("width", c.width),
                ("w_center", c.w_center),
                ("lo", c.lo),
                ("hi", c.hi),
                ("d_lo", c.d_lo),
                ("d_hi", c.d_hi),
                ("open_sign", c.open_sign),
            ]),
        ),
        Ok(Grammar::RectangularBlindSlot(b)) => (
            "rectangular_blind_slot".into(),
            fields(&[
                ("width", b.width),
                ("length", b.length),
                ("depth", b.depth),
                ("open_sign", b.open_sign),
                ("depth_sign", b.depth_sign),
            ]),
        ),
        Ok(Grammar::RoundBottomBlindSlot(b)) => (
            "round_bottom_blind_slot".into(),
            fields(&[
                ("length", b.length),
                ("radius", b.depth),
                ("flat_width", b.width - 2.0 * b.depth),
                ("open_sign", b.open_sign),
                ("depth_sign", b.depth_sign),
            ]),
        ),
        Ok(Grammar::HexPocket { depth, .. }) => ("hex_pocket".into(), fields(&[("depth", depth)])),
        Ok(Grammar::CircularChannel { radius, length, .. }) => (
            "circular_channel".into(),
            fields(&[("radius", radius), ("length", length)]),
        ),
        Err(Refusal::Unsupported(why)) => (format!("unsupported: {why}"), BTreeMap::new()),
        Err(Refusal::Invalid(why)) => (format!("invalid: {why}"), BTreeMap::new()),
    }
}

fn state(s: turned::State) -> String {
    match s {
        turned::State::Placed => "placed",
        turned::State::Missing => "missing",
        turned::State::Unverifiable => "unverifiable",
    }
    .into()
}

/// The envelope's and the turned steps' completeness ledgers ([`envelope::outcomes`],
/// [`turned::outcomes`]) on Python's captured sheets, against Python's own ledgers for the same
/// drawings (`tests/fixtures/lint_ledgers.json`, written by `tools/capture_lint_ledgers.py`:
/// Python's lint reports no finding for either, so the sheet facts do not carry them). A turned
/// step is matched to Python's by its band's axis and span in the part's frame (to 1e-3). Each
/// section recess's drafting grammar is checked against draftwright's reading of the same
/// record ([`recess_reading`]). Every difference is listed, with a verdict, in `known_lint.json`
/// (`ledgers`), and a listed one that has gone away must be taken off.
#[test]
fn ledgers_on_pythons_sheets_match_pythons_or_are_known() {
    let Some(corpus) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let known: Vec<KnownLedger> = serde_json::from_value(
        serde_json::from_str::<Value>(
            &std::fs::read_to_string(fixture("known_lint.json")).unwrap(),
        )
        .unwrap()["ledgers"]
            .clone(),
    )
    .unwrap();
    let python: BTreeMap<String, Value> =
        serde_json::from_str(&std::fs::read_to_string(fixture("lint_ledgers.json")).unwrap())
            .unwrap();
    type Key = (
        String,
        String,
        String,
        String,
        Option<String>,
        Option<String>,
    );
    let mut listed = BTreeSet::new();
    for k in &known {
        assert!(
            [
                "rust-correct",
                "rust-wrong",
                "equivalent",
                "undetermined",
                "not-applicable"
            ]
            .contains(&k.verdict.as_str())
                && !k.reason.is_empty(),
            "{k:?}"
        );
        assert!(
            listed.insert((
                k.part.clone(),
                k.ledger.clone(),
                k.subject.clone(),
                k.parameter.clone(),
                k.python.clone(),
                k.rust.clone(),
            )),
            "{k:?} is listed twice"
        );
    }
    let mut differ: BTreeSet<Key> = BTreeSet::new();
    let mut agree = 0;
    let names = facts::names();
    assert_eq!(
        python.keys().collect::<BTreeSet<_>>(),
        names.iter().collect::<BTreeSet<_>>(),
        "lint_ledgers.json has a ledger for each captured sheet"
    );
    for name in names {
        let f = facts::load(&name);
        let theirs = &python[&name];
        assert_eq!(theirs["file"], f.binding.file.as_str(), "{name}");
        let analysis = match analysis(&corpus, &name, &f.binding.file) {
            Ok(a) => a,
            Err(_) => {
                differ.insert((
                    name.clone(),
                    "*".into(),
                    "analysis".into(),
                    "*".into(),
                    None,
                    None,
                ));
                continue;
            }
        };
        let groups = groups(&analysis);
        let (marks, _) = marks(&analysis, &groups, &f);
        let mut compare = |ledger: &str, subject: String, parameter: &str, p: Option<String>, r| {
            if p == r {
                agree += 1;
            } else {
                differ.insert((name.clone(), ledger.into(), subject, parameter.into(), p, r));
            }
        };

        let ours = envelope::outcomes(&analysis, &marks);
        let mut axes: BTreeMap<String, (Option<String>, Option<String>)> = BTreeMap::new();
        for o in theirs["envelope"].as_array().unwrap() {
            axes.entry(o["axis"].as_str().unwrap().into())
                .or_default()
                .0 = Some(o["state"].as_str().unwrap().into());
        }
        for o in &ours {
            axes.entry(o.axis.into()).or_default().1 = Some(state(o.state));
        }
        for (axis, (p, r)) in axes {
            let k = ["x", "y", "z"].iter().position(|a| *a == axis).unwrap();
            compare("envelope", axis, envelope::PARAMETERS[k], p, r);
        }

        // Python's turned step outcomes: (axis, span, parameter, state), in the part's frame. Each
        // of the port's takes the one of its axis and parameter whose span is its own (within
        // 1e-3); those left are Python's alone.
        let frame = draftwright::bridge::holes::frame(&analysis);
        let mut steps: Vec<(String, f64, f64, String, String)> = theirs["turned_steps"]
            .as_array()
            .unwrap()
            .iter()
            .map(|o| {
                let s = &o["span"];
                (
                    o["axis"].as_str().unwrap_or("?").to_string(),
                    s[0].as_f64().unwrap_or(f64::NAN),
                    s[1].as_f64().unwrap_or(f64::NAN),
                    o["parameter"].as_str().unwrap().to_string(),
                    o["state"].as_str().unwrap().to_string(),
                )
            })
            .collect();
        for o in turned::outcomes(&analysis, &marks) {
            let f = analysis
                .features
                .iter()
                .find(|f| f.id == o.feature)
                .unwrap();
            let b = turned::band(&frame, f).unwrap();
            let axis = ["x", "y", "z"][b.axis];
            let theirs = steps
                .iter()
                .position(|(a, lo, hi, p, _)| {
                    a == axis
                        && *p == o.parameter
                        && (lo - b.lo).abs() <= 1e-3
                        && (hi - b.hi).abs() <= 1e-3
                })
                .map(|i| steps.remove(i).4);
            compare(
                "turned_steps",
                o.feature.clone(),
                &o.parameter,
                theirs,
                Some(state(o.state)),
            );
        }
        for (axis, lo, hi, parameter, p) in steps {
            let at = format!("{axis} {lo:.3}..{hi:.3}");
            compare("turned_steps", at, &parameter, Some(p), None);
        }

        // Each section recess's drafting grammar, by its index: the port's reading
        // (`lint::section_recesses::grammar`) against draftwright's `section_recess_fields` on the
        // same recognition: the grammar, and its sizes and places in quiddity's frame (within
        // 2e-6), or why none reads it.
        let mut python_recesses: BTreeMap<u64, (String, BTreeMap<String, f64>)> = BTreeMap::new();
        for r in theirs["section_recesses"].as_array().unwrap() {
            let grammar = r["grammar"].as_str().unwrap().to_string();
            let fields: BTreeMap<String, f64> = r["fields"]
                .as_object()
                .map(|o| {
                    o.iter()
                        .map(|(k, v)| (k.clone(), v.as_f64().unwrap()))
                        .collect()
                })
                .unwrap_or_default();
            let grammar = match r["reason"].as_str() {
                Some(why) => format!("{grammar}: {why}"),
                None => grammar,
            };
            python_recesses.insert(r["index"].as_u64().unwrap(), (grammar, fields));
        }
        for f in lint::section_recesses::recesses(&analysis) {
            let index = f.parameters["index"].as_u64().unwrap();
            let (grammar, fields) = recess_reading(f);
            let theirs = python_recesses.remove(&index);
            let same = theirs.as_ref().is_some_and(|(g, p)| {
                *g == grammar
                    && fields
                        .iter()
                        .all(|(k, v)| p.get(k).is_some_and(|w| (v - w).abs() <= 2e-6))
            });
            let show = |g: &str, p: &BTreeMap<String, f64>| {
                let mut s = g.to_string();
                for (k, v) in p.iter().filter(|(k, _)| fields.contains_key(*k)) {
                    s += &format!(" {k}={v}");
                }
                s
            };
            let mine = show(&grammar, &fields);
            compare(
                "section_recesses",
                index.to_string(),
                "grammar",
                theirs.map(|(g, p)| if same { mine.clone() } else { show(&g, &p) }),
                Some(mine.clone()),
            );
        }
        for (index, (g, _)) in python_recesses {
            compare(
                "section_recesses",
                index.to_string(),
                "grammar",
                Some(g),
                None,
            );
        }
    }
    let unlisted: Vec<_> = differ.difference(&listed).collect();
    let stale: Vec<_> = listed.difference(&differ).collect();
    eprintln!(
        "ledgers: {agree} outcomes agree, {} differ ({} known)",
        differ.len(),
        known.len()
    );
    assert!(
        unlisted.is_empty() && stale.is_empty(),
        "ledger differences not in known_lint.json:\n{}\nlisted but no longer differing:\n{}",
        unlisted
            .iter()
            .map(|k| format!("{k:?}"))
            .collect::<Vec<_>>()
            .join("\n"),
        stale
            .iter()
            .map(|k| format!("{k:?}"))
            .collect::<Vec<_>>()
            .join("\n")
    );
}

/// The part *name* (`tests/fixtures/parts/`) with *json* as its recognition, planned under
/// *spec*, rendered, and linted: each finding's code, first subject and requirement.
fn reviewed(name: &str, json: &Value, spec: &model::Spec) -> (model::Plan, Vec<String>) {
    let (part, mut analysis) = model::analyse(&fixture(&format!("parts/{name}.step"))).unwrap();
    model::recognise(&part, &mut analysis, &json.to_string()).unwrap();
    let plan = model::plan(&analysis, spec).unwrap();
    let font = draftwright::text::Font::new(
        std::fs::read(Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .unwrap(),
    )
    .unwrap();
    let drawing = model::render(&part, &analysis, &plan, &font).unwrap();
    let found = lint::lint(&analysis, &drawing)
        .into_iter()
        .map(|i| {
            format!(
                "{} {} {}",
                i.code, i.subjects[0], i.requirements[0].parameter
            )
        })
        .collect();
    (plan, found)
}

fn recognition(name: &str) -> Value {
    serde_json::from_str(
        &std::fs::read_to_string(fixture(&format!("recognition/{name}.json"))).unwrap(),
    )
    .unwrap()
}

/// The port's own callout states the plate's four ø6 holes (`4× ⌀6 THRU`), so review finds
/// nothing missing; with that callout omitted from the spec, each of their requirements is
/// missing again, and the fix says how to state them. Lint reads the drawing's annotations
/// (their text runs and claims), never the plan or the spec.
#[test]
fn review_finds_the_plates_holes_stated_by_its_own_callout() {
    let step = fixture("parts/plate.step");
    let features = fixture("recognition/plate.json");
    let review = draftwright::api::review(&step, Some(&features), &model::Spec::default()).unwrap();
    assert!(review.lint.is_empty(), "{:#?}", review.lint);

    let callout = "d:f:hole_pattern:1/bore.diameter";
    let mut spec = model::Spec::default();
    spec.dimensions.entry(callout.into()).or_default().omit = true;
    let review = draftwright::api::review(&step, Some(&features), &spec).unwrap();
    let found: Vec<(&str, &str)> = review
        .lint
        .iter()
        .map(|i| (i.code.as_str(), i.requirements[0].parameter.as_str()))
        .collect();
    // The diameter backstop reports the ø6 uncalled too, as draftwright's lint runs both
    // (`lint_feature_coverage` and `lint_hole_coverage`, linting/orchestration.py).
    assert_eq!(
        found,
        [
            ("feature_not_dimensioned", "bore.diameter"),
            ("hole_requirement_missing", "bore.diameter"),
            ("hole_requirement_missing", "bore.through"),
            ("hole_requirement_missing", "grouping.count"),
        ]
    );
    for issue in review
        .lint
        .iter()
        .filter(|i| i.code != "feature_not_dimensioned")
    {
        assert_eq!(issue.subjects.len(), 4, "{issue:?}");
        assert!(
            issue.fix.as_ref().unwrap().contains("\"4× ⌀6 THRU\""),
            "{issue:?}"
        );
    }
    // Without features there is nothing to require.
    let bare = draftwright::api::review(&step, None, &model::Spec::default()).unwrap();
    assert!(bare.lint.is_empty());
}

/// LINT-hole_coverage-csink_diameter_angle on the port's own drawing: the plate's holes
/// countersunk (quiddity's record: `major_diameter`, `included_angle`) are called out with their
/// countersink, which lint reads back as stated; a countersink whose angle quiddity did not give
/// is called out without it, and lint reports the angle unverifiable rather than guessing.
#[test]
fn a_countersunk_hole_is_called_out_and_its_callout_satisfies_lint() {
    let mut json = recognition("plate");
    for f in json["quiddity"]["features"].as_array_mut().unwrap() {
        let r = &mut f["record"];
        r["csink"] = json!({
            "axis": r["axis"].clone(), "location": r["location"].clone(),
            "major_diameter": 12.0, "drill_diameter": 6.0, "included_angle": 90.0, "depth": 3.0,
        });
    }
    let (plan, found) = reviewed("plate", &json, &model::Spec::default());
    let callout = plan
        .dimensions
        .iter()
        .find(|d| d.kind == "callout")
        .unwrap();
    assert_eq!(callout.label, "4× ⌀6 THRU ⌵ ⌀12 × 90°");
    assert!(
        !plan
            .problems
            .iter()
            .any(|p| p.code == "feature_not_dimensioned")
    );
    assert_eq!(found, Vec::<String>::new());

    for f in json["quiddity"]["features"].as_array_mut().unwrap() {
        f["record"]["csink"]
            .as_object_mut()
            .unwrap()
            .remove("included_angle");
    }
    let (plan, found) = reviewed("plate", &json, &model::Spec::default());
    assert!(
        plan.dimensions
            .iter()
            .any(|d| d.label == "4× ⌀6 THRU ⌵ ⌀12")
    );
    assert_eq!(
        found,
        ["hole_requirement_unverifiable f:hole:1 countersink.angle"]
    );
}

/// Two identical grids of 2×3 ø6 through holes 80 mm apart (`tests/fixtures/parts/two_grids.step`,
/// made with build123d: a 140×40×5 box less `Cylinder(3, 5)` at x = ±40 + {-10, 0, 10},
/// y = ±8), which quiddity 0.3.9 recognises as four identical linear patterns of three. Lint
/// groups the holes by the recognised patterns, as the dimensions call them out, so the port's
/// four `3× ⌀6 THRU` callouts state every requirement; its old fingerprint heuristic (count,
/// pitch, sizes, direction, level) could not tell the four apart and demanded `12×`. Python
/// draftwright on the same part prints the same four `3× ⌀6 THRU` callouts and its lint finds
/// nothing: the port now agrees (equivalent).
#[test]
fn identical_patterns_are_grouped_as_recognised() {
    let json = recognition("two_grids");
    let (part, mut analysis) = model::analyse(&fixture("parts/two_grids.step")).unwrap();
    model::recognise(&part, &mut analysis, &json.to_string()).unwrap();
    let g = groups(&analysis);
    assert_eq!(g.len(), 4);
    for (g, p) in g
        .iter()
        .zip(&analysis.recognition.as_ref().unwrap().patterns)
    {
        assert!(g.pattern);
        let mut members = p.members.clone();
        members.sort_by_key(|m| analysis.features.iter().position(|f| &f.id == m));
        assert_eq!(g.members, members);
    }
    let (plan, found) = reviewed("two_grids", &json, &model::Spec::default());
    let callouts: Vec<&str> = plan
        .dimensions
        .iter()
        .filter(|d| d.kind == "callout")
        .map(|d| d.label.as_str())
        .collect();
    assert_eq!(callouts, ["3× ⌀6 THRU"; 4]);
    assert_eq!(found, Vec::<String>::new());
    // Without the recognition record, the fingerprints alone cannot tell the patterns apart.
    analysis.recognition = None;
    let g = groups(&analysis);
    assert_eq!((g.len(), g[0].members.len(), g[0].pattern), (1, 12, false));
}

/// Lint reads a rendered drawing's own annotations: a callout that claims the plate's holes and
/// prints their sizes satisfies them; one that prints a wrong size, or is not there, does not.
#[test]
fn lint_reads_the_drawings_annotations() {
    use draftwright::output::{Annotation, HAlign, TextRun, VAlign};
    let (part, analysis) = model::analyse_with(
        &fixture("parts/plate.step"),
        Some(&fixture("recognition/plate.json")),
    )
    .unwrap();
    let plan = model::plan(&analysis, &model::Spec::default()).unwrap();
    let font = draftwright::text::Font::new(
        std::fs::read(Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .unwrap(),
    )
    .unwrap();
    let holes = &groups(&analysis)[0].members;
    let with = |label: &str| {
        // The drawing without the port's own callout, and one added by hand.
        let mut drawing = model::render(&part, &analysis, &plan, &font).unwrap();
        drawing.annotations.clear();
        drawing.text.push(TextRun {
            text: label.into(),
            at: [100.0, 100.0],
            size: 3.5,
            rotation: 0.0,
            font: "IBMPlexMono-Regular.ttf".into(),
            h_align: HAlign::Left,
            v_align: VAlign::Baseline,
        });
        drawing.annotations.push(Annotation {
            approved_claims: Vec::new(),
            approved_text: None,
            coordinate_origin: None,
            satisfies: Vec::new(),
            name: "d:f:hole:1/bore.diameter@v:plan".into(),
            view: Some("v:plan".into()),
            runs: vec![drawing.text.len() - 1],
            claims: holes
                .iter()
                .map(|h| Claim {
                    feature: h.clone(),
                    parameter: "bore.diameter".into(),
                })
                .collect(),
        });
        lint::lint(&analysis, &drawing)
            .into_iter()
            .map(|i| (i.code, i.requirements[0].parameter.clone()))
            .collect::<Vec<_>>()
    };
    let s = |a: &str, b: &str| (a.to_string(), b.to_string());
    // Clearing every annotation also removes envelope, pattern location and pitch proof.
    let missing = vec![
        s("missing_principal_dimension", "width.length"),
        s("missing_principal_dimension", "depth.length"),
        s("missing_principal_dimension", "height.length"),
        s("hole_pattern_requirement_missing", "location.length.x"),
        s("hole_pattern_requirement_missing", "location.length.y"),
        s("hole_pattern_requirement_missing", "grid_pitch.length.col"),
        s("hole_pattern_requirement_missing", "grid_pitch.length.row"),
    ];
    assert_eq!(with("4× ⌀6 THRU"), missing);
    // The misprinted ⌀6.5 leaves the ø6 uncovered by the diameter backstop as well.
    let mut wrong = vec![
        s("feature_not_dimensioned", "bore.diameter"),
        s("hole_requirement_unverifiable", "bore.diameter"),
    ];
    wrong.extend(missing.clone());
    assert_eq!(with("4× ⌀6.5 THRU"), wrong);
    let mut partial = vec![
        s("hole_requirement_missing", "bore.through"),
        s("hole_requirement_missing", "grouping.count"),
    ];
    partial.extend(missing);
    assert_eq!(with("⌀6"), partial);
}

/// On a captured sheet, taking a callout away or misprinting it is found, in every family: lint is
/// judging the sheet, not echoing it.
#[test]
fn lint_finds_what_is_taken_off_a_captured_sheet() {
    let Some(corpus) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let f = facts::load("m10127");
    let analysis = analysis(&corpus, "m10127", &f.binding.file).unwrap();
    let groups = groups(&analysis);
    let (marks, unjoined) = marks(&analysis, &groups, &f);
    assert_eq!(unjoined, 0);
    // quiddity refused one recess recognition on m10127, and the part has an opening no feature
    // states (its three-sided hole through the face at y 32.268, lint::profiles), both reported
    // whatever the sheet says (as Python's lint reports them on this sheet); nothing else is
    // found. The sheet's structured hole locations are not joined into claims here (location
    // coverage is compared on Python's recorded input, tests/lint_coverage.rs), so
    // feature_not_located is left out.
    // ADR1-8: the sheet also states the boss's 5.7 height beside the ⌀13 hole's `↧ 5.7` depth,
    // which runs between the same two planes (the boss stands on the hole's flat floor): one
    // interval stated twice (the port conveys the boss height by the hole callout).
    let refused = "section_recess_recognition_refused";
    let profile = "unrecognised_defining_geometry";
    let unjoined = "feature_not_located";
    let twice = "duplicate_measurement";
    let issues = lint::check(&analysis, &marks);
    let found: Vec<String> = issues
        .iter()
        .map(|i| i.code.clone())
        .filter(|c| c != unjoined)
        .collect();
    assert_eq!(found, [refused, profile, twice]);
    let dup = issues.iter().find(|i| i.code == twice).unwrap();
    assert_eq!(
        (dup.annotation.as_deref(), dup.related.as_slice()),
        (
            Some("m_bossheight_y0"),
            ["hc_front0".to_string()].as_slice()
        )
    );
    // hc_side0 prints "⌀8.1 THRU" for the through hole.
    let codes = |marks: &[Mark]| {
        lint::check(&analysis, marks)
            .into_iter()
            .filter(|i| {
                i.code != refused && i.code != profile && i.code != unjoined && i.code != twice
            })
            .map(|i| format!("{} {}", i.code, i.requirements[0].parameter))
            .collect::<Vec<_>>()
    };
    let without: Vec<Mark> = marks
        .iter()
        .filter(|m| m.name != "hc_side0")
        .cloned()
        .collect();
    assert_eq!(
        codes(&without),
        [
            "feature_not_dimensioned bore.diameter",
            "hole_requirement_missing bore.diameter",
            "hole_requirement_missing bore.through"
        ]
    );
    let misprinted: Vec<Mark> = marks
        .iter()
        .cloned()
        .map(|mut m| {
            if m.name == "hc_side0" {
                m.text = m.text.replace("8.1", "8.4").replace("THRU", "");
            }
            m
        })
        .collect();
    assert_eq!(
        codes(&misprinted),
        [
            "feature_not_dimensioned bore.diameter",
            "hole_requirement_unverifiable bore.diameter",
            "hole_requirement_missing bore.through"
        ]
    );
    // The other families: the boss's height (m_bossheight_y0, "5.7"), the fillet (m_fillet_y0,
    // "R4.2") and the through step's x leg (dim_through_step_z0_x, "18.7"), taken away; and the
    // fillet's radius misprinted. The hole callout's `↧ 5.7` still states the boss height's
    // interval (ADR1-8), so the height is found missing only with that callout gone too.
    let taken = |names: &[&str]| -> Vec<Mark> {
        marks
            .iter()
            .filter(|m| !names.contains(&m.name.as_str()))
            .cloned()
            .collect()
    };
    let without = taken(&["m_bossheight_y0", "m_fillet_y0", "dim_through_step_z0_x"]);
    assert_eq!(
        codes(&without),
        [
            "fillet_requirement_missing fillet.radius",
            "through_step_requirement_missing through_step_leg.length.x"
        ]
    );
    let bare = taken(&["m_bossheight_y0", "hc_front0"]);
    assert!(
        codes(&bare).contains(&"boss_height_missing boss_height.length".to_string()),
        "{:?}",
        codes(&bare)
    );
    let misprinted: Vec<Mark> = marks
        .iter()
        .cloned()
        .map(|mut m| {
            if m.name == "m_fillet_y0" {
                m.text = m.text.replace("4.2", "4.5");
            }
            m
        })
        .collect();
    assert_eq!(
        codes(&misprinted),
        ["fillet_requirement_unverifiable fillet.radius"]
    );

    // ctc01's hex boss: `HEX 100 A/F` (m_polygonal_boss_z0) and its height `50`
    // (m_bossheight_z0) state both; the height taken away, or the size misprinted, is found.
    let f = facts::load("ctc01");
    let ctc01 = self::analysis(&corpus, "ctc01", &f.binding.file).unwrap();
    let (marks, unjoined) = self::marks(&ctc01, &self::groups(&ctc01), &f);
    assert_eq!(unjoined, 0);
    let polygonal = |marks: &[Mark]| {
        lint::check(&ctc01, marks)
            .into_iter()
            .filter(|i| i.code.starts_with("polygonal_boss_"))
            .map(|i| format!("{} {}", i.code, i.requirements[0].parameter))
            .collect::<Vec<_>>()
    };
    assert!(polygonal(&marks).is_empty());
    let without: Vec<Mark> = marks
        .iter()
        .filter(|m| m.name != "m_bossheight_z0")
        .cloned()
        .collect();
    assert_eq!(
        polygonal(&without),
        ["polygonal_boss_requirement_missing boss_height.length"]
    );
    let misprinted: Vec<Mark> = marks
        .iter()
        .cloned()
        .map(|mut m| {
            if m.name == "m_polygonal_boss_z0" {
                m.text = m.text.replace("100", "110");
            }
            m
        })
        .collect();
    assert_eq!(
        polygonal(&misprinted),
        ["polygonal_boss_requirement_unverifiable polygon_across_flats.length"]
    );
}

/// What lint finds on the port's own drawing of a captured part, by code: a known entry.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct KnownOwn {
    part: String,
    code: String,
    count: usize,
    /// `rust-correct`, `rust-wrong`, `equivalent`, `undetermined` or `not-applicable`.
    verdict: String,
    reason: String,
}

/// Lint on the port's own drawings of the captured parts (the corpus's, with their features from
/// `tests/fixtures/recognition/corpus/`, planned under the default spec and rendered): every
/// finding, counted by part and code, is listed in `known_lint.json` (`own_drawings`) with a
/// verdict. The port's dimensions do not yet state blends or steps, so lint
/// reports those requirements missing on its drawings: correct, and pinned here until the
/// dimensions state them. Set `LINT_REPORT=1` to print each finding, and `LINT_OWN_ALL=1`
/// to draw connector109 too (see below).
#[test]
fn lint_on_the_ports_own_drawings_is_known() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let known: Vec<KnownOwn> = serde_json::from_value(
        serde_json::from_str::<Value>(
            &std::fs::read_to_string(fixture("known_lint.json")).unwrap(),
        )
        .unwrap()["own_drawings"]
            .clone(),
    )
    .unwrap();
    let font = draftwright::text::Font::new(
        std::fs::read(Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .unwrap(),
    )
    .unwrap();
    // ctc04 has no features: no corpus recognition is recorded for it. connector109 takes about six
    // minutes to render (haecceity's hidden lines over its 140 freeform thread faces; the other
    // parts take a second or less), so it is drawn only with LINT_OWN_ALL=1; its entries stay
    // listed, and are checked then.
    let all = std::env::var_os("LINT_OWN_ALL").is_some();
    let names: Vec<String> = facts::names()
        .into_iter()
        .filter(|n| corpus::recognition(n).is_some())
        .filter(|n| all || n != "connector109")
        .collect();
    // Each part on its own thread: rendering (hidden lines) dominates, and the parts are
    // independent. The findings are gathered in part order.
    let linted: Vec<Vec<lint::LintIssue>> = std::thread::scope(|s| {
        let handles: Vec<_> = names
            .iter()
            .map(|name| {
                let (dir, font) = (&dir, &font);
                s.spawn(move || {
                    let f = facts::load(name);
                    let (part, analysis) = corpus::analyse(dir, &f.binding.file, name);
                    // Keep the captured full-ISO drawing resource for this historical lint ledger.
                    let spec = serde_json::from_value(
                        serde_json::json!({"sheet":{"preferred_max_size":"A0"}}),
                    )
                    .unwrap();
                    let plan = model::plan(&analysis, &spec).unwrap();
                    let drawing = model::render(&part, &analysis, &plan, font).unwrap();
                    lint::lint(&analysis, &drawing)
                })
            })
            .collect();
        handles.into_iter().map(|h| h.join().unwrap()).collect()
    });
    let mut found: BTreeMap<(String, String), usize> = BTreeMap::new();
    for (name, issues) in names.iter().zip(linted) {
        for issue in issues {
            if std::env::var_os("LINT_REPORT").is_some() {
                eprintln!("{name}: {} {}", issue.code, issue.message);
            }
            *found.entry((name.clone(), issue.code)).or_default() += 1;
        }
    }
    let listed: BTreeMap<(String, String), usize> = known
        .iter()
        .map(|k| {
            assert!(
                [
                    "rust-correct",
                    "rust-wrong",
                    "equivalent",
                    "undetermined",
                    "not-applicable"
                ]
                .contains(&k.verdict.as_str())
                    && !k.reason.is_empty(),
                "{k:?}"
            );
            ((k.part.clone(), k.code.clone()), k.count)
        })
        .collect();
    assert_eq!(listed.len(), known.len(), "a part and code is listed twice");
    let listed: BTreeMap<(String, String), usize> = listed
        .into_iter()
        .filter(|((part, _), _)| names.contains(part))
        .collect();
    // Resolved findings retain explicit zero-count entries as regression guards.
    // Every listed positive count and every unlisted finding still participates
    // in the exact equality below; a zero expectation requires actual absence.
    for key in listed.keys() {
        found.entry(key.clone()).or_default();
    }
    eprintln!(
        "{} findings on the port's own drawings, in {} part and code pairs",
        found.values().sum::<usize>(),
        found.len()
    );
    assert_eq!(
        found, listed,
        "lint's findings on the port's own drawings (left) differ from known_lint.json's own_drawings (right)"
    );
}

/// Blind slots on the port's own drawings of the corpus parts that have them (Python quiddity
/// 0.3.9's recognition, `tests/fixtures/recognition/dims/`): the dimensions call each out
/// (`OPEN SLOT W × L × D DEEP`, `ROUND-BOTTOM OPEN SLOT …`), and lint reads every requirement
/// back as stated. Python draftwright's lint on its own sheets of the same parts (origin/main
/// 586628b, `build_drawing(...).lint()`, run 2026-10-08) reports no blind-slot finding either,
/// and the same section recess dispositions, message for message but one: m10047's prismatic
/// recess is 7.737 deep in Python's message and 7.736 here (equivalent). quiddity rounds a
/// recess's run interval to 0.001 mm in the frame it recognised in: draftwright's own
/// recognition, in the file's frame, gives 16.478..24.215; the recognition document the bridge
/// reads, in quiddity's working frame, 4.517..12.253.
#[test]
fn blind_slots_on_the_ports_own_drawings_are_stated() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let font = draftwright::text::Font::new(
        std::fs::read(Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .unwrap(),
    )
    .unwrap();
    let tail = "is not represented by Draftwright dimensions: profile has no supported drafting \
                grammar; review and define its manufacturing section outside automatic drawing \
                approval";
    let cases = [
        (
            "m10047",
            "mfcadpp/10047.step",
            ["round_bottom_blind_slot"; 2].as_slice(),
            vec![format!(
                "recognised 3-sided blind prismatic recess 7.736 mm deep (1 of 1) {tail}"
            )],
        ),
        (
            "m10245",
            "mfcadpp/10245.step",
            ["rectangular_blind_slot"].as_slice(),
            vec![format!(
                "recognised 3-sided blind prismatic recess 7.165 mm deep (1 of 1) {tail}"
            )],
        ),
        (
            "m11512",
            "mfcadpp_regressions/11512.step",
            ["rectangular_blind_slot"].as_slice(),
            vec![format!(
                "recognised 6-edge prismatic through-opening (1 of 1) {tail}"
            )],
        ),
    ];
    for (name, file, slots, python) in cases {
        let path = fixture(&format!("recognition/dims/{name}.json.gz"));
        let mut json = String::new();
        flate2::read::GzDecoder::new(std::fs::File::open(path).unwrap())
            .read_to_string(&mut json)
            .unwrap();
        let (part, mut analysis) = model::analyse(&dir.join(file)).unwrap();
        model::recognise(&part, &mut analysis, &json).unwrap();
        let round = slots[0].starts_with("round");
        let wanted = lint::blind_slots::wanted(&analysis, round);
        assert_eq!(wanted.len(), 3 * slots.len(), "{name}");
        assert!(lint::blind_slots::wanted(&analysis, !round).is_empty());
        let plan = model::plan(&analysis, &model::Spec::default()).unwrap();
        let drawing = model::render(&part, &analysis, &plan, &font).unwrap();
        let issues = lint::lint(&analysis, &drawing);
        let blind: Vec<&String> = issues
            .iter()
            .map(|i| &i.code)
            .filter(|c| c.contains("blind_slot"))
            .collect();
        assert!(blind.is_empty(), "{name}: {blind:?}");
        let mine: Vec<&String> = issues
            .iter()
            .filter(|i| DISPOSITIONS.contains(&i.code.as_str()))
            .map(|i| &i.message)
            .collect();
        assert_eq!(mine, python.iter().collect::<Vec<_>>(), "{name}");
    }
}

/// LINT-slot_coverage-slot_pattern on the two slot-pattern fixture plates
/// (`tests/fixtures/recognition/slot_patterns/`: a 1×3 array and a 3×2 grid of obround slots;
/// no corpus part has a slot pattern). Each pattern has Python's requirements (`_parameter_ids`:
/// width, length, end radius, the pitch or the column and row pitches, the centre's x and y),
/// and lint judges them on both drawings of each plate:
///
/// - **Python's sheet** (`<name>.facts.json.gz`, captured by `capture.py` there): the array's
///   are all stated, and Python's lint reports nothing about them either. The grid's row pitch
///   is not on the sheet: lint reports it missing, where Python reports `slot_dim_dropped` for
///   the same measurement (its placement found no room for it). Equivalent: lint sees no
///   placement drops, so the one requirement is missing either way.
/// - **The port's own drawing** (planned under the default spec and rendered): every
///   requirement is stated, the grid's row pitch (`2× 40`) included.
#[test]
fn slot_patterns_are_checked_on_both_sheets() {
    let dir = fixture("recognition/slot_patterns");
    let font = draftwright::text::Font::new(
        std::fs::read(Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .unwrap(),
    )
    .unwrap();
    let cases = [
        (
            "slot_array",
            vec![
                "slot_width.length 6",
                "slot_length.length 20",
                "slot_end_radius.radius 3",
                "pitch.length 25",
                "location_slot_pattern.length.x 60",
                "location_slot_pattern.length.y 30",
            ],
            Vec::<&str>::new(),
            Vec::<&str>::new(),
        ),
        (
            "slot_grid",
            vec![
                "slot_width.length 8",
                "slot_length.length 24",
                "slot_end_radius.radius 4",
                "grid_pitch.length.col 30",
                "grid_pitch.length.row 40",
                "location_slot_pattern.length.x 80",
                "location_slot_pattern.length.y 50",
            ],
            vec!["slot_requirement_missing grid_pitch.length.row"],
            vec!["slot_dim_dropped grid_pitch.length.row"],
        ),
    ];
    for (name, requirements, on_pythons, python) in cases {
        let f = facts::load_file(&dir.join(format!("{name}.facts.json.gz")));
        let mut json = String::new();
        flate2::read::GzDecoder::new(
            std::fs::File::open(dir.join(format!("{name}.json.gz"))).unwrap(),
        )
        .read_to_string(&mut json)
        .unwrap();
        let (part, mut a) = model::analyse(&dir.join(&f.binding.file)).unwrap();
        assert_eq!(a.binding.source_sha256, f.binding.sha256, "{name}");
        model::recognise(&part, &mut a, &json).unwrap();
        let patterns: Vec<&draftwright::bridge::Feature> = a
            .features
            .iter()
            .filter(|f| f.kind == "slot_pattern")
            .collect();
        assert_eq!(patterns.len(), 1, "{name}");
        let wanted: Vec<String> = lint::slots::pattern_wanted(&a, patterns[0])
            .unwrap()
            .iter()
            .map(|w| {
                let v = w.values.as_ref().unwrap();
                format!("{} {}", w.parameter, lint::parameters::fmt(v[0]))
            })
            .collect();
        assert_eq!(wanted, requirements, "{name}");

        let slot_findings = |issues: Vec<lint::LintIssue>| -> Vec<String> {
            issues
                .into_iter()
                .filter(|i| i.code.starts_with("slot_"))
                .map(|i| format!("{} {}", i.code, i.requirements[0].parameter))
                .collect()
        };
        let (marks, unjoined) = marks(&a, &groups(&a), &f);
        assert_eq!(unjoined, 0, "{name}");
        assert_eq!(
            slot_findings(lint::check(&a, &marks)),
            on_pythons,
            "{name}: lint on Python's sheet"
        );
        let pythons: Vec<String> = f
            .lint
            .iter()
            .filter(|i| i.code.starts_with("slot_"))
            .map(|i| {
                let parameter = i.measurements[0]["parameter"].as_str().unwrap_or("?");
                format!("{} {parameter}", i.code)
            })
            .collect();
        assert_eq!(pythons, python, "{name}: Python's lint");

        let plan = model::plan(&a, &model::Spec::default()).unwrap();
        let drawing = model::render(&part, &a, &plan, &font).unwrap();
        assert_eq!(
            slot_findings(lint::lint(&a, &drawing)),
            Vec::<String>::new(),
            "{name}: lint on the port's own drawing"
        );
    }
}

#[test]
fn prose_advice_has_conservative_scope_without_inventing_spec_edits() {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    let (_, analysis) =
        draftwright::model::analyse_native(&root.join("tests/fixtures/parts/mounting_plate.step"))
            .unwrap();
    let issues = draftwright::lint::check(&analysis, &[]);
    let mut offered = 0;
    for issue in issues.iter().filter(|i| i.fix.is_some()) {
        let wire = serde_json::to_value(issue).unwrap();
        assert_eq!(wire["scope"], "sheet");
        assert_eq!(wire["fix"].as_str(), issue.fix.as_deref());
        assert_eq!(wire["fix_description"].as_str(), issue.fix.as_deref());
        offered += 1;
    }
    assert!(offered > 0);
}
