//! Datums and feature control frames authored in the Spec (draftwright-rust #43 item 1), for a
//! part whose STEP carries no AP242 PMI, or to restate what it carries. Python draftwright's
//! `Sheet.datum(letter, ref)` and `Sheet.control(ref).<characteristic>(tol, to=…, diameter=…,
//! modifier=…)` are the reference for what may be said:
//!
//! - `features/<id>/datum` and `faces/<face>/datum`: a datum letter (ISO 5459) on a recognised
//!   feature (a feature or hole pattern, by its ID, as `features/<id>/fit` names one) or on a face
//!   (`bottom`, `top`, `left`, `right`, `front`, `back`: the planar faces on that side of the
//!   part's box; or `face:<n>`, the face the plan's problems name so).
//! - `features/<id>/controls` and `faces/<face>/controls`: feature control frames (ISO 1101),
//!   each `{characteristic, tolerance, datums?, diameter?, modifier?}` with one of draftwright's
//!   14 characteristics; `diameter` (a `⌀` zone) is the default for position and concentricity,
//!   and `modifier` is `M` or `L`.
//!
//! On a feature with cylinders, a datum or control applies to its cylinders (the axis, as
//! draftwright's `gdt_target` takes a feature's), else to its faces. Each is lowered into the
//! SAME record an AP242 datum or geometric tolerance on those faces is read into
//! ([`crate::pmi::Record`], with its attachment measured as [`crate::pmi`] measures it, from the
//! Analysis's [`crate::pmi::FaceAttachment`]s), and merged into the Analysis's PMI reading
//! ([`effective`]); lowering, placement, band reservation, drawing and lint then treat it as they
//! treat the file's own, and its item ID is its record's identity (`pmi:<identity>`). A record's
//! source is `spec:<path>` (`spec:features/f:hole:1/datum`), and its basis says it is
//! spec-authored. Where the file's PMI states the same thing (a datum of the same letter; a
//! tolerance of the same characteristic on the same faces) the Spec's wins: the file's record is
//! not drawn, and a `spec_overrides_pmi` warning says so. A control referring to a datum letter
//! nothing declares is drawn, with a `datum_undeclared` warning (draftwright's
//! `_validate_datums`). What cannot be read (an unknown feature or face, a characteristic,
//! letter or modifier draftwright has not, a tolerance not positive) is refused.

use std::borrow::Cow;
use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};

use crate::model::{Analysis, Invalid, Problem, Spec};
use crate::pmi::{Category, FaceAttachment, Outcome, Reading, Record, Report, Source};

/// The prefix of a Spec-authored record's source (`spec:features/f:hole:1/datum`).
pub const SOURCE_PREFIX: &str = "spec:";

/// draftwright's 14 characteristics (`_Control`'s methods), each with XCAF's type number, as the
/// AP242 reading kinds them (`pmi::tolerance_kind`).
pub const CHARACTERISTICS: [(&str, u32); 14] = [
    ("straightness", 13),
    ("flatness", 7),
    ("circularity", 3),
    ("cylindricity", 6),
    ("profile_line", 11),
    ("profile_surface", 12),
    ("angularity", 1),
    ("perpendicularity", 9),
    ("parallelism", 8),
    ("position", 10),
    ("concentricity", 5),
    ("symmetry", 14),
    ("circular_runout", 2),
    ("total_runout", 15),
];

/// Form tolerances, which take no datum reference (draftwright's form methods take no `to=`).
const FORM: [&str; 4] = ["straightness", "flatness", "circularity", "cylindricity"];

/// Characteristics whose zone may be a cylinder (`⌀`): an axis's straightness, orientation and
/// location. draftwright offers `diameter=` on position and concentricity only, where it is the
/// default; the others here accept it when asked.
const DIAMETRAL: [&str; 6] = [
    "straightness",
    "angularity",
    "perpendicularity",
    "parallelism",
    "position",
    "concentricity",
];

/// The sides of the part's box a face may be named by: the axis its outward normal lies along,
/// and whether it points up that axis. The front view looks along +y, so `front` is the -y side.
pub const SIDES: [(&str, usize, bool); 6] = [
    ("bottom", 2, false),
    ("top", 2, true),
    ("left", 0, false),
    ("right", 0, true),
    ("front", 1, false),
    ("back", 1, true),
];

/// A feature control frame the Spec authors (draftwright's `control(ref).<characteristic>(…)`).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ControlSpec {
    /// One of [`CHARACTERISTICS`] (`position`, `flatness`, …).
    pub characteristic: String,
    /// The tolerance's magnitude, mm.
    pub tolerance: f64,
    /// The datum letters referred to, in precedence order (draftwright's `to="A B"`).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub datums: Vec<String>,
    /// A cylindrical (`⌀`) zone: the default for position and concentricity.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub diameter: Option<bool>,
    /// The material condition: `M` (maximum) or `L` (least).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub modifier: Option<String>,
}

/// What the Spec authors on a face.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields, default)]
pub struct FaceSpec {
    /// A datum letter on the face.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub datum: Option<String>,
    /// Feature control frames on the face.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub controls: Vec<ControlSpec>,
    /// An ISO 1302 surface texture on the face ([`crate::spec_notes`]).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub finish: Option<crate::spec_notes::FinishSpec>,
    /// A note on a leader to the face ([`crate::spec_notes`]).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
    /// How the corners of the face's outer profile are defined: `auto` (linear where the
    /// dimensions nearly fix them, else angles), `angle` or `linear` ([`crate::dims`]).
    #[serde(skip_serializing_if = "crate::dims::CornerMode::is_auto")]
    pub profile_corners: crate::dims::CornerMode,
}

/// Whether *spec* authors any datum or control.
pub fn authors(spec: &Spec) -> bool {
    spec.features
        .values()
        .any(|f| f.datum.is_some() || !f.controls.is_empty())
        || spec
            .faces
            .values()
            .any(|f| f.datum.is_some() || !f.controls.is_empty())
}

fn invalid(message: String, choices: Vec<String>) -> Invalid {
    Invalid {
        message,
        choices,
        path: None,
        also: Vec::new(),
    }
}

/// A datum letter as ISO 5459 writes it: one to three capital letters.
fn letter(at: &str, l: &str) -> Result<(), Invalid> {
    let t = l.trim();
    if !t.is_empty() && t.len() <= 3 && t.bytes().all(|b| b.is_ascii_uppercase()) {
        return Ok(());
    }
    let upper = t.to_ascii_uppercase();
    let close =
        !upper.is_empty() && upper.len() <= 3 && upper.bytes().all(|b| b.is_ascii_uppercase());
    Err(invalid(
        if close {
            format!("{l:?} at {at} reads as \"{upper}\": a datum letter is written in capitals.")
        } else {
            format!("{l:?} at {at} is not a datum letter: one to three capital letters (A, B, …).")
        },
        if close { vec![upper] } else { vec![] },
    ))
}

/// Checks every datum and control the spec authors reads as one: the form, never the part (that
/// is [`effective`]'s). A refusal names the entry's path.
pub(crate) fn check(spec: &Spec) -> Result<(), Invalid> {
    let entries = spec
        .features
        .iter()
        .map(|(id, f)| {
            (
                format!("features/{}", crate::items::key(id)),
                &f.datum,
                &f.controls,
            )
        })
        .chain(spec.faces.iter().map(|(k, f)| {
            (
                format!("faces/{}", crate::items::key(k)),
                &f.datum,
                &f.controls,
            )
        }));
    for (base, datum, controls) in entries {
        if let Some(d) = datum {
            letter(&format!("{base}/datum"), d)?;
        }
        for (i, c) in controls.iter().enumerate() {
            let at = format!("{base}/controls/{i}");
            let names: Vec<String> = CHARACTERISTICS.iter().map(|(n, _)| n.to_string()).collect();
            let name = c.characteristic.as_str();
            if !names.iter().any(|n| n == name) {
                let lower = name.trim().to_ascii_lowercase().replace([' ', '-'], "_");
                let mut near = names.clone();
                near.sort_by_key(|n| (crate::dims::levenshtein(&lower, n), n.clone()));
                return Err(invalid(
                    format!(
                        "{name:?} at {at} is not a characteristic; draftwright's are {}.",
                        names.join(", ")
                    ),
                    near,
                ));
            }
            if !(c.tolerance.is_finite() && c.tolerance > 0.0) {
                return Err(invalid(
                    format!(
                        "The tolerance at {at} is {}; a tolerance is a positive magnitude in mm.",
                        c.tolerance
                    ),
                    vec![],
                ));
            }
            if FORM.contains(&name) && !c.datums.is_empty() {
                return Err(invalid(
                    format!(
                        "{name} at {at} is a form tolerance and refers to no datum; remove its datums."
                    ),
                    vec![],
                ));
            }
            for d in &c.datums {
                letter(&format!("{at}/datums"), d)?;
            }
            if c.diameter == Some(true) && !DIAMETRAL.contains(&name) {
                return Err(invalid(
                    format!(
                        "{name} at {at} takes no ⌀ zone; a ⌀ zone is for {}.",
                        DIAMETRAL.join(", ")
                    ),
                    vec![],
                ));
            }
            match c.modifier.as_deref() {
                None | Some("M") | Some("L") => {}
                Some("P") => {
                    return Err(invalid(
                        format!(
                            "The modifier P at {at} (a projected tolerance zone) is not drawn: the drawing draws M and L only, as it does for AP242 PMI."
                        ),
                        vec!["M".into(), "L".into()],
                    ));
                }
                Some(m) => {
                    return Err(invalid(
                        format!(
                            "{m:?} at {at} is not a material condition; the modifiers are M (maximum) and L (least)."
                        ),
                        vec!["M".into(), "L".into()],
                    ));
                }
            }
        }
    }
    // And every finish, thread, knurl and note ([`crate::spec_notes`]).
    crate::spec_notes::check(spec)
}

/// The faces a datum or control on feature or pattern *id* applies to: its cylinders, when it has
/// any, else all its faces; or why there is none.
pub(crate) fn feature_faces(analysis: &Analysis, id: &str) -> Result<Vec<usize>, Invalid> {
    let patterns = analysis
        .recognition
        .as_ref()
        .map_or(&[][..], |r| r.patterns.as_slice());
    let faces = match analysis.features.iter().find(|f| f.id == id) {
        Some(f) => &f.faces,
        None => match patterns.iter().find(|p| p.id == id) {
            Some(p) => &p.faces,
            None => {
                let mut near: Vec<&str> = analysis
                    .features
                    .iter()
                    .map(|f| f.id.as_str())
                    .chain(patterns.iter().map(|p| p.id.as_str()))
                    .collect();
                near.sort_by_key(|c| (crate::dims::levenshtein(id, c), *c));
                near.truncate(5);
                return Err(invalid(
                    format!(
                        "There is no feature {id:?} on this part, so its datum and controls (features/{}) cannot be attached; a feature is named by its ID as the plan gives it.",
                        crate::items::key(id)
                    ),
                    near.into_iter().map(String::from).collect(),
                )
                .at("features", id));
            }
        },
    };
    let cylinders: Vec<usize> = faces
        .iter()
        .copied()
        .filter(|&f| analysis.face_attachments[f].surface.as_deref() == Some("cylinder"))
        .collect();
    Ok(if cylinders.is_empty() {
        faces.clone()
    } else {
        cylinders
    })
}

/// The faces face key *key* names: a side of the part's box (its planar faces lying on that side,
/// facing out), or `face:<n>`.
pub(crate) fn named_faces(analysis: &Analysis, key: &str) -> Result<Vec<usize>, Invalid> {
    let forms = || {
        SIDES
            .iter()
            .map(|(s, ..)| s.to_string())
            .chain(std::iter::once("face:<n>".to_string()))
            .collect::<Vec<_>>()
    };
    if let Some(n) = key.strip_prefix("face:") {
        let count = analysis.face_attachments.len();
        return match n.parse::<usize>() {
            Ok(i) if i < count => Ok(vec![i]),
            _ => Err(invalid(
                format!(
                    "There is no face {key:?} on this part: its faces are face:0 to face:{}.",
                    count.saturating_sub(1)
                ),
                forms(),
            )
            .at("faces", key)),
        };
    }
    let Some(&(side, k, high)) = SIDES.iter().find(|(s, ..)| *s == key) else {
        return Err(invalid(
            format!(
                "{key:?} names no face: a face is named by the side of the part it is on ({}) or by its index (face:<n>).",
                SIDES.map(|s| s.0).join(", ")
            ),
            forms(),
        ));
    };
    let [lo, hi] = analysis.envelope;
    let at = if high { hi[k] } else { lo[k] };
    let tol = 1e-4 * (1.0 + (hi[k] - lo[k]).abs());
    let faces: Vec<usize> = analysis
        .face_attachments
        .iter()
        .enumerate()
        .filter(|(_, a)| {
            a.surface.as_deref() == Some("plane")
                && a.axis == Some(k)
                && a.support.first().is_some_and(|s| (s - at).abs() <= tol)
                && a.normal.is_some_and(|n| (n[k] > 0.0) == high)
        })
        .map(|(f, _)| f)
        .collect();
    if faces.is_empty() {
        return Err(invalid(
            format!(
                "The part has no planar face on its {side} (facing {}{} at {at}); name a face by its index (face:<n>) instead.",
                if high { "+" } else { "-" },
                ["x", "y", "z"][k]
            ),
            forms(),
        )
        .at("faces", key));
    }
    Ok(faces)
}

/// The box of *faces*' attachments.
fn bbox(analysis: &Analysis, faces: &[usize]) -> Option<[f64; 6]> {
    let mut out: Option<[f64; 6]> = None;
    for b in faces
        .iter()
        .filter_map(|&f| analysis.face_attachments[f].bounds)
    {
        let o = out.get_or_insert(b);
        for i in 0..3 {
            o[i] = o[i].min(b[i]);
            o[i + 3] = o[i + 3].max(b[i + 3]);
        }
    }
    out
}

/// A record of *category* on *faces*, as the AP242 reading makes one, its attachment measured
/// from the faces' attachments.
pub(crate) fn record(
    analysis: &Analysis,
    part: usize,
    source_id: String,
    category: Category,
    faces: &[usize],
) -> Record {
    let mut faces = faces.to_vec();
    faces.sort_unstable();
    faces.dedup();
    let ref_bbox = bbox(analysis, &faces);
    let mut blockers = Vec::new();
    if faces
        .iter()
        .any(|&f| analysis.face_attachments[f].bounds.is_none())
    {
        blockers.push("one of its faces has no finite extent".to_string());
    }
    Record {
        source_id,
        category,
        kind: String::new(),
        type_code: None,
        part,
        part21_id: 0,
        value: None,
        upper_tol: None,
        lower_tol: None,
        lower_bound: None,
        upper_bound: None,
        fit: None,
        label: String::new(),
        datum_refs: Vec::new(),
        gtol_modifiers: Vec::new(),
        basic: false,
        // A Spec record is read from no Part 21 instance; its faces are one placement's, so it
        // states one instance per face (`pmi::placements_reason` reads the two counts).
        face_ids: faces.iter().map(|&f| f as u64).collect(),
        faces,
        face_groups: Vec::new(),
        edge_groups: Vec::new(),
        edges: Vec::new(),
        dominant_axis: ref_bbox.map_or("?", crate::pmi::dominant).to_string(),
        ref_bbox,
        instances: Vec::new(),
        blockers,
        fields: Vec::new(),
        geometry: None,
    }
}

/// *analysis* with the datums and controls *spec* authors merged into its PMI reading, each as
/// the record an AP242 datum or tolerance on its faces is read into; or *analysis* itself when
/// the spec authors none, when the reading already holds them (an analysis this function made:
/// it is idempotent, whatever spec it is given then), or when the spec is another revision's
/// (its feature entries are inactive: `model::plan` lists them). Refused: an entry that cannot be
/// read ([`check`]), an unknown feature or face, one datum letter on two targets, two controls
/// of one characteristic on the same faces, or an Analysis made before faces' attachments were
/// recorded. Authored gears are also lowered into the independent requirement inventory
/// ([`crate::gears`]); their anchors must belong to this revision.
pub fn effective<'a>(analysis: &'a Analysis, spec: &Spec) -> Result<Cow<'a, Analysis>, Invalid> {
    let bound = crate::recognition_corrections::canonical(analysis, spec)?;
    let spec = bound.as_ref();
    let corrected = crate::recognition_corrections::effective(analysis, spec)?;
    let merged = match corrected {
        Cow::Borrowed(a) => pmi_effective(a, spec)?,
        Cow::Owned(a) => Cow::Owned(pmi_effective(&a, spec)?.into_owned()),
    };
    Ok(crate::process::effective(
        crate::gears::effective(merged, spec)?,
        spec,
    ))
}

fn pmi_effective<'a>(analysis: &'a Analysis, spec: &Spec) -> Result<Cow<'a, Analysis>, Invalid> {
    let merged = match &analysis.pmi {
        Some(Reading::Read(r)) => r
            .records
            .iter()
            .any(|r| r.source_id.starts_with(SOURCE_PREFIX)),
        _ => false,
    };
    let other_revision = spec
        .binding
        .as_ref()
        .is_some_and(|b| b.source_sha256 != analysis.binding.source_sha256);
    // The finishes, threads, knurls and notes the Spec authors ride the same merge
    // ([`crate::spec_notes`]).
    if !(authors(spec) || crate::spec_notes::authors(spec)) || merged || other_revision {
        return Ok(Cow::Borrowed(analysis));
    }
    check(spec)?;
    if analysis.face_attachments.len() != analysis.binding.face_count {
        return Err(invalid(
            "This Analysis records no face attachments (it was made by an earlier draftwright), so the spec's datums and controls cannot be attached; analyse the STEP file again.".into(),
            vec![],
        ));
    }
    let mut out = analysis.clone();
    let (mut report, refused) = match out.pmi.take() {
        Some(Reading::Read(r)) => (r, None),
        Some(Reading::Refused(why)) => (empty_report(analysis), Some(why)),
        None => (empty_report(analysis), None),
    };
    let part = report.drawn.unwrap_or(0);
    let mut problems = Vec::new();
    if let Some(why) = refused {
        // The file's PMI could not be read: said as lowering says it, and kept in the census, while
        // the Spec's own are drawn.
        problems.push(Problem {
            id: "p:pmi_not_read:part".into(),
            code: "pmi_not_read".into(),
            severity: "error".into(),
            message: format!(
                "The STEP file carries AP242 semantic PMI that could not be read ({why}); none of it is drawn."
            ),
            subjects: Vec::new(),
            refs: vec!["part".into()],
            ..Default::default()
        });
        report.sources.push(Source {
            id: "reading:part".into(),
            category: Category::Other,
            entity: "semantic pmi".into(),
            instances: Vec::new(),
            outcome: Outcome::NotExtracted,
            reason: why,
        });
    }
    // The Spec's records, features first, then faces, each entry's datum before its controls.
    let mut records: Vec<Record> = Vec::new();
    let mut letters: BTreeMap<String, String> = BTreeMap::new();
    let mut frames: BTreeMap<(String, Vec<usize>), String> = BTreeMap::new();
    let entries: Vec<Entry<'_>> = {
        let mut v = Vec::new();
        // Every feature or face the part does not have is refused at once (the first, and the
        // rest as `also`).
        let mut refused = Vec::new();
        for (id, f) in &spec.features {
            if f.datum.is_some() || !f.controls.is_empty() {
                match feature_faces(analysis, id) {
                    Ok(faces) => v.push((
                        format!("features/{}", crate::items::key(id)),
                        faces,
                        &f.datum,
                        &f.controls,
                    )),
                    Err(e) => refused.push(e),
                }
            }
        }
        for (key, f) in &spec.faces {
            if f.datum.is_some() || !f.controls.is_empty() {
                match named_faces(analysis, key) {
                    Ok(faces) => v.push((
                        format!("faces/{}", crate::items::key(key)),
                        faces,
                        &f.datum,
                        &f.controls,
                    )),
                    Err(e) => refused.push(e),
                }
            }
        }
        if let Some(e) = Invalid::first_of(refused) {
            return Err(e);
        }
        v
    };
    for (base, faces, datum, controls) in entries {
        if let Some(l) = datum {
            let l = l.trim().to_string();
            let at = format!("{base}/datum");
            if let Some(other) = letters.insert(l.clone(), at.clone()) {
                return Err(invalid(
                    format!(
                        "Datum {l} is set at both {other} and {at}; a datum letter names one feature."
                    ),
                    vec![],
                ));
            }
            let mut r = record(
                analysis,
                part,
                format!("{SOURCE_PREFIX}{at}"),
                Category::Datum,
                &faces,
            );
            r.kind = "datum".into();
            r.label = l;
            r.geometry = Some(crate::pmi::datum_geometry_of(
                &r,
                &attachments(analysis, &r),
            ));
            records.push(r);
        }
        for (i, c) in controls.iter().enumerate() {
            let at = format!("{base}/controls/{i}");
            let name = c.characteristic.as_str();
            let mut r = record(
                analysis,
                part,
                format!("{SOURCE_PREFIX}{at}"),
                Category::GeometricTolerance,
                &faces,
            );
            if let Some(other) = frames.insert((name.to_string(), r.faces.clone()), at.clone()) {
                return Err(invalid(
                    format!(
                        "{other} and {at} are both a {name} tolerance on the same faces; give one."
                    ),
                    vec![],
                ));
            }
            let code = CHARACTERISTICS
                .iter()
                .find(|(n, _)| *n == name)
                .map(|(_, c)| *c)
                // Invariant: ControlSpec::check matched the characteristic against CHARACTERISTICS.
                .expect("checked");
            r.kind = name.into();
            r.type_code = Some(code);
            r.value = Some(c.tolerance);
            r.label = format!("{name} {}", crate::pmi::fmt_g3(c.tolerance));
            r.datum_refs = c.datums.iter().map(|d| d.trim().to_string()).collect();
            // In the reading's order: the zone, then the material condition.
            let diametral = c
                .diameter
                .unwrap_or(matches!(name, "position" | "concentricity"));
            if diametral {
                r.gtol_modifiers.push("diameter_zone".into());
            }
            match c.modifier.as_deref() {
                Some("M") => r.gtol_modifiers.push("maximum_material_requirement".into()),
                Some("L") => r.gtol_modifiers.push("least_material_requirement".into()),
                _ => {}
            }
            r.geometry = Some(crate::pmi::tolerance_geometry_of(
                &r,
                &attachments(analysis, &r),
            ));
            records.push(r);
        }
    }
    records.extend(crate::spec_notes::records(analysis, spec, part)?);
    // Where the file states the same, the Spec wins: a datum of the same letter, a tolerance of
    // the same characteristic on the same faces.
    let overrides = |pmi: &Record| -> Option<usize> {
        records.iter().position(|s| match pmi.category {
            Category::Datum => s.category == Category::Datum && s.label == pmi.label.trim(),
            Category::GeometricTolerance => {
                s.category == Category::GeometricTolerance
                    && s.kind == pmi.kind
                    && s.faces == pmi.faces
            }
            // A finish, default finish, thread or knurl ([`crate::spec_notes::overrides`]).
            _ => crate::spec_notes::overrides(s, pmi),
        })
    };
    let overridden: Vec<_> = report
        .records
        .iter()
        .filter_map(|pmi| overrides(pmi).map(|i| (pmi.clone(), i)))
        .collect();
    let manufacturing = if overridden.iter().any(|(r, _)| {
        matches!(r.kind.as_str(), "internal_thread" | "external_thread")
            && crate::pmi::specify_written(&report, &r.source_id)
    }) {
        crate::pmi_lowering::manufacturing(analysis)
    } else {
        Default::default()
    };
    let mut dropped = BTreeSet::new();
    for (pmi, i) in overridden {
        let s = &mut records[i];
        let specify = crate::pmi::specify_written(&report, &pmi.source_id);
        let thread = manufacturing
            .owned
            .iter()
            .find(|o| o.source == pmi.source_id)
            .and_then(|o| o.thread());
        let number = |name: &str| {
            s.fields.iter().find_map(|(key, value)| match value {
                crate::pmi::Field::Length(n) if key == name => Some(*n),
                _ => None,
            })
        };
        let refines = specify
            && s.kind == pmi.kind
            && thread.is_some_and(|t| {
                number("nominal").is_some_and(|n| (n - t.nominal).abs() < 1e-6)
                    && number("pitch").is_some_and(|n| (n - t.pitch).abs() < 1e-6)
            });
        if refines
            && number("length").is_none()
            && let Some(length) = thread.and_then(|t| t.minimum_full_thread)
        {
            s.fields
                .push(("length".into(), crate::pmi::Field::Length(length)));
        }
        let code = if refines {
            "spec_refines_pmi"
        } else {
            "spec_overrides_pmi"
        };
        let message = if refines {
            format!(
                "The Spec's {} refines Specify's thread designation.",
                s.label
            )
        } else {
            format!(
                "The Spec's {} replaces {} {} annotation, which is not drawn. Remove the corresponding setting to draw the original requirement.",
                s.label,
                if specify {
                    "Specify's"
                } else {
                    "the STEP file's"
                },
                pmi.label
            )
        };
        dropped.insert(pmi.source_id.clone());
        problems.push(Problem {
            id: format!("p:{code}:{}", pmi.source_id),
            code: code.into(),
            severity: if refines { "info" } else { "warning" }.into(),
            message,
            subjects: Vec::new(),
            refs: vec![pmi.source_id.clone(), s.source_id.clone()],
            ..Default::default()
        });
    }
    report.records.retain(|r| !dropped.contains(&r.source_id));
    report.sources.retain(|s| !dropped.contains(&s.id));
    // A datum referred to that nothing declares (draftwright's `_validate_datums`, a warning).
    let declared: BTreeSet<String> = report
        .records
        .iter()
        .chain(&records)
        .filter(|r| r.category == Category::Datum)
        .map(|r| r.label.trim().to_string())
        .collect();
    for r in &records {
        let missing: Vec<&str> = r
            .datum_refs
            .iter()
            .filter(|d| !declared.contains(d.as_str()))
            .map(String::as_str)
            .collect();
        if !missing.is_empty() {
            let path = &r.source_id[SOURCE_PREFIX.len()..];
            problems.push(Problem {
                id: format!("p:datum_undeclared:{}", r.source_id),
                code: "datum_undeclared".into(),
                severity: "warning".into(),
                message: format!(
                    "The {} at {path} refers to datum {}, which neither the spec nor the STEP file declares. Set it on its feature or face (features/<id>/datum or faces/<face>/datum).",
                    r.label,
                    missing.join(", ")
                ),
                subjects: Vec::new(),
                refs: vec![r.source_id.clone()],
                ..Default::default()
            });
        }
    }
    for r in &records {
        report.sources.push(Source {
            id: r.source_id.clone(),
            category: r.category,
            entity: "spec".into(),
            instances: Vec::new(),
            outcome: Outcome::Extracted,
            reason: "Spec-authored requirement extracted; drawing depends on its attachment and placement".into(),
        });
    }
    report.records.extend(records);
    report.spec_problems = problems;
    out.pmi = Some(Reading::Read(report));
    Ok(Cow::Owned(out))
}

/// An authored entry: its path, its faces, its datum letter and its controls.
type Entry<'a> = (String, Vec<usize>, &'a Option<String>, &'a Vec<ControlSpec>);

/// The attachments of *r*'s faces, in its order.
pub(crate) fn attachments(analysis: &Analysis, r: &Record) -> Vec<FaceAttachment> {
    r.faces
        .iter()
        .map(|&f| analysis.face_attachments[f].clone())
        .collect()
}

/// A reading of no PMI, for a part whose file carries none.
fn empty_report(analysis: &Analysis) -> Report {
    Report {
        schema: crate::pmi::PMI_SCHEMA.into(),
        parts: vec![analysis.binding.part.clone()],
        sources: Vec::new(),
        records: Vec::new(),
        accounting: Default::default(),
        materials: Vec::new(),
        drawn: None,
        spec_problems: Vec::new(),
        reader: Box::default(),
    }
}

/// Why a declared datum no frame cites and no dimension measures from is not drawn by default.
pub(crate) const UNCITED_DATUM: &str = "not drawn: no control frame cites this datum and no dimension measures from it (sheet/uncited_datums=true draws it)";

/// Why this datum symbol is hidden: by explicit Spec intent, or by default because no frame
/// cites it and no dimension measures from it (*origins*, the source ids of the datums dimensions
/// measure from; maintainer 2026-10-25, revising #141). Frame citations are semantic: hiding a
/// frame does not turn its datum into an uncited datum.
pub(crate) fn datum_omission(
    spec: &crate::model::Spec,
    report: &Report,
    record: &Record,
    origins: &std::collections::BTreeSet<String>,
) -> Option<&'static str> {
    if record.category != Category::Datum {
        return None;
    }
    if spec.annotations.values().any(|a| a.omit)
        && report
            .identities()
            .id(&record.source_id)
            .ok()
            .is_some_and(|id| spec.annotations.get(id).is_some_and(|a| a.omit))
    {
        return Some("datum symbol omitted by annotations/<id>/omit");
    }
    // Unset, a datum dimensions measure from counts as used; false hides every datum no frame
    // cites, as before.
    if spec.sheet.uncited_datums != Some(true)
        && (spec.sheet.uncited_datums == Some(false) || !origins.contains(&record.source_id))
        && !report.records.iter().any(|r| {
            r.category == Category::GeometricTolerance
                && r.datum_refs.iter().any(|d| d.trim() == record.label.trim())
        })
    {
        return Some(if spec.sheet.uncited_datums == Some(false) {
            "uncited datum symbol omitted by sheet/uncited_datums=false"
        } else {
            UNCITED_DATUM
        });
    }
    None
}
