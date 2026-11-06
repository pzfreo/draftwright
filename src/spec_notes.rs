//! Surface finish, threads, knurls and notes authored in the Spec (draftwright-rust #43 item 1,
//! the second part; stream K1b), the way [`crate::spec_gdt`] authors datums and controls. Python
//! draftwright's `Sheet.finish(ra, ref)`, `_Hole.thread(spec, depth=)`, `_Dim.thread(spec)`,
//! `_Dim.knurl(pitch, pattern)`, `Sheet.note(text, ref)` and `Sheet.default_surface_finish(ra)`
//! are the reference for what may be said:
//!
//! - `features/<id>/finish` and `faces/<face>/finish`: an ISO 1302 surface texture,
//!   `{ra, removal?}` (Ra in µm; `removal` is `required`, `prohibited` or `any`, the default),
//!   drawn as a face's AP242 surface texture is (the symbol on a leader from the face's edge);
//!   `sheet/default_finish`, the same, is the part's default finish beside the title block.
//! - `features/<id>/thread`: an ISO metric thread `{designation, length?}` (`M6`, `M10x1.25-6g`,
//!   `M5 LH`) on a hole the callouts state on its own, a hole pattern or a turned step, printed
//!   after its callout's size as an AP242 thread is (`⌀6 THRU M6`, `ø10 M10; 15 MIN FULL THREAD`).
//! - `features/<id>/knurl`: `{pattern, pitch}` (`straight` or `diamond`, mm) on a turned step,
//!   printed after its diameter as an AP242 knurl is (`ø24 STRAIGHT KNURL P0.8`).
//! - `features/<id>/note` and `faces/<face>/note`: free text on a leader to the feature or face,
//!   drawn as an AP242 surface label is. A note on the sheet is a general note (`notes`), and the
//!   general tolerance is the title block's (`sheet/title/tolerance`): both were authored before.
//!
//! Each is lowered into the record an AP242 requirement of its kind is read into
//! ([`crate::pmi::Record`], its source `spec:<path>`), merged into the Analysis's PMI reading with
//! the datums and controls ([`crate::spec_gdt::effective`]). Where the file states the same (a
//! finish on any of the same faces, the part's default finish, a thread or knurl on any of the
//! same faces) the Spec's wins with `spec_overrides_pmi`. What cannot be read (a roughness not
//! positive, an unknown removal, a designation that is not ISO metric, an empty note) is refused;
//! a thread or knurl its feature cannot carry (a knurl on a hole, a tap larger than its hole, a
//! length past its feature's) is reported as an AP242 one no feature owns is
//! (`pmi_not_lowered`, [`crate::pmi_lowering::manufacturing`]).

use serde::{Deserialize, Serialize};

use crate::model::{Analysis, Invalid, Spec};
use crate::pmi::{Category, Field, Record};
use crate::spec_gdt::SOURCE_PREFIX;

/// An ISO 1302 surface texture the Spec authors (draftwright's `finish(ra)`).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FinishSpec {
    /// The arithmetic mean roughness, µm.
    pub ra: f64,
    /// The material removal condition: `required` (the symbol's bar), `prohibited` (its circle)
    /// or `any` (the basic symbol, the default).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub removal: Option<String>,
}

/// An ISO metric thread the Spec authors on a hole, hole pattern or turned step (draftwright's
/// `thread(spec, depth=)`).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ThreadSpec {
    /// `M<size>[x<pitch>][-<class>][ LH|RH]`, printed as written.
    pub designation: String,
    /// The minimum full-thread length, mm; absent for the feature's full length.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub length: Option<f64>,
}

/// A knurl the Spec authors on a turned step (draftwright's `knurl(pitch, pattern)`).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct KnurlSpec {
    /// `straight` or `diamond`.
    pub pattern: String,
    /// Its pitch, mm.
    pub pitch: f64,
}

/// The removal conditions a finish may state, each with the term an AP242 surface texture states
/// it by (ISO 10303-1110), as [`crate::pmi_lowering`] draws it.
const REMOVALS: [(&str, &str); 3] = [
    ("any", "any process allowed"),
    ("required", "material removal required"),
    ("prohibited", "no material removal"),
];

/// The knurl patterns (draftwright's `KnurlRequirement.pattern`).
const PATTERNS: [&str; 2] = ["straight", "diamond"];

/// ISO 261's coarse pitches by nominal size, for a designation that states none.
pub(crate) const COARSE: [(f64, f64); 40] = [
    (1.0, 0.25),
    (1.1, 0.25),
    (1.2, 0.25),
    (1.4, 0.3),
    (1.6, 0.35),
    (1.8, 0.35),
    (2.0, 0.4),
    (2.2, 0.45),
    (2.5, 0.45),
    (3.0, 0.5),
    (3.5, 0.6),
    (4.0, 0.7),
    (4.5, 0.75),
    (5.0, 0.8),
    (6.0, 1.0),
    (7.0, 1.0),
    (8.0, 1.25),
    (9.0, 1.25),
    (10.0, 1.5),
    (11.0, 1.5),
    (12.0, 1.75),
    (14.0, 2.0),
    (16.0, 2.0),
    (18.0, 2.5),
    (20.0, 2.5),
    (22.0, 2.5),
    (24.0, 3.0),
    (27.0, 3.0),
    (30.0, 3.5),
    (33.0, 3.5),
    (36.0, 4.0),
    (39.0, 4.0),
    (42.0, 4.5),
    (45.0, 4.5),
    (48.0, 5.0),
    (52.0, 5.0),
    (56.0, 5.5),
    (60.0, 5.5),
    (64.0, 6.0),
    (68.0, 6.0),
];

/// Whether *spec* authors any finish, thread, knurl or note, or a default finish.
pub fn authors(spec: &Spec) -> bool {
    spec.sheet.default_finish.is_some()
        || spec.features.values().any(|f| {
            f.finish.is_some() || f.thread.is_some() || f.knurl.is_some() || f.note.is_some()
        })
        || spec
            .faces
            .values()
            .any(|f| f.finish.is_some() || f.note.is_some())
}

fn invalid(message: String, choices: Vec<String>) -> Invalid {
    Invalid {
        message,
        choices,
        path: None,
        also: Vec::new(),
    }
}

/// A metric designation's nominal size and pitch (the coarse pitch when it states none), or why
/// it is not one: `M<size>`, then optionally `x<pitch>` (spaces allowed around the `x`), `-<class>`
/// and ` LH` or ` RH`.
pub(crate) fn metric(designation: &str) -> Result<(f64, f64), String> {
    let bad = || {
        format!(
            "{designation:?} is not an ISO metric thread designation: M<size>[x<pitch>][-<class>][ LH] (M6, M10x1.25-6g)"
        )
    };
    let s = designation.trim();
    let rest = s.strip_prefix('M').ok_or_else(bad)?;
    let number = |t: &str| -> Option<(f64, usize)> {
        let n = t
            .find(|c: char| !(c.is_ascii_digit() || c == '.'))
            .unwrap_or(t.len());
        let v: f64 = t[..n].parse().ok()?;
        (v.is_finite() && v > 0.0).then_some((v, n))
    };
    let (nominal, n) = number(rest).ok_or_else(bad)?;
    let mut rest = &rest[n..];
    let mut pitch = None;
    let trimmed = rest.trim_start();
    if let Some(after) = trimmed.strip_prefix(['x', 'X', '×']) {
        let (p, n) = number(after.trim_start()).ok_or_else(bad)?;
        pitch = Some(p);
        rest = &after.trim_start()[n..];
    }
    if let Some(after) = rest.strip_prefix('-') {
        let n = after
            .find(|c: char| !c.is_ascii_alphanumeric())
            .unwrap_or(after.len());
        if n == 0 {
            return Err(bad());
        }
        rest = &after[n..];
    }
    let hand = rest.trim();
    if !(hand.is_empty() || (matches!(hand, "LH" | "RH") && rest.starts_with(char::is_whitespace)))
    {
        return Err(bad());
    }
    let pitch = match pitch {
        Some(p) => p,
        None => COARSE
            .iter()
            .find(|(d, _)| (d - nominal).abs() < 1e-9)
            .map(|(_, p)| *p)
            .ok_or_else(|| {
                format!(
                    "{designation:?} states no pitch and M{} has no ISO 261 coarse pitch; write it (M{}x<pitch>)",
                    crate::pmi::fmt_g3(nominal),
                    crate::pmi::fmt_g3(nominal)
                )
            })?,
    };
    Ok((nominal, pitch))
}

/// A finish at *at* reads as one: a positive roughness and a known removal condition.
fn finish(at: &str, f: &FinishSpec) -> Result<(), Invalid> {
    if !(f.ra.is_finite() && f.ra > 0.0) {
        return Err(invalid(
            format!(
                "The roughness at {at}/ra is {}; Ra is a positive value in µm.",
                f.ra
            ),
            vec![],
        ));
    }
    if let Some(r) = &f.removal
        && !REMOVALS.iter().any(|(n, _)| n == r)
    {
        return Err(invalid(
            format!(
                "{r:?} at {at}/removal is not a material removal condition; they are required, prohibited and any."
            ),
            REMOVALS.iter().map(|(n, _)| n.to_string()).collect(),
        ));
    }
    Ok(())
}

/// A note at *at* reads as one: text on one line.
fn note(at: &str, text: &str) -> Result<(), Invalid> {
    if text.trim().is_empty() || text.contains('\n') {
        return Err(invalid(
            format!("The note at {at} is empty or more than one line; a note is one line of text."),
            vec![],
        ));
    }
    Ok(())
}

/// Checks every finish, thread, knurl and note the spec authors reads as one: the form, never the
/// part (that is [`records`]' and [`crate::pmi_lowering::manufacturing`]'s). A refusal names the
/// entry's path.
pub(crate) fn check(spec: &Spec) -> Result<(), Invalid> {
    if let Some(f) = &spec.sheet.default_finish {
        finish("sheet/default_finish", f)?;
    }
    for (id, f) in &spec.features {
        let base = format!("features/{}", crate::items::key(id));
        if let Some(x) = &f.finish {
            finish(&format!("{base}/finish"), x)?;
        }
        if let Some(t) = &f.modelled_thread
            && let Some(designation) = &t.designation
        {
            note(&format!("{base}/modelled_thread/designation"), designation)?;
        }
        if let Some(t) = &f.thread {
            let at = format!("{base}/thread");
            metric(&t.designation).map_err(|why| invalid(format!("{why}, at {at}."), vec![]))?;
            if t.length.is_some_and(|l| !(l.is_finite() && l > 0.0)) {
                return Err(invalid(
                    format!("The thread length at {at}/length is not a positive length in mm."),
                    vec![],
                ));
            }
        }
        if let Some(k) = &f.knurl {
            let at = format!("{base}/knurl");
            if !PATTERNS.contains(&k.pattern.as_str()) {
                return Err(invalid(
                    format!(
                        "{:?} at {at}/pattern is not a knurl pattern; they are straight and diamond.",
                        k.pattern
                    ),
                    PATTERNS.map(String::from).to_vec(),
                ));
            }
            if !(k.pitch.is_finite() && k.pitch > 0.0) {
                return Err(invalid(
                    format!("The knurl pitch at {at}/pitch is not a positive length in mm."),
                    vec![],
                ));
            }
        }
        if !f.note_satisfies.is_empty() && f.note.is_none() {
            return Err(invalid(
                format!("{base}/note_satisfies needs a feature note."),
                vec![],
            ));
        }
        if let Some(n) = &f.note {
            note(&format!("{base}/note"), n)?;
        }
    }
    for (key, f) in &spec.faces {
        let base = format!("faces/{}", crate::items::key(key));
        if let Some(x) = &f.finish {
            finish(&format!("{base}/finish"), x)?;
        }
        if let Some(n) = &f.note {
            note(&format!("{base}/note"), n)?;
        }
    }
    Ok(())
}

/// A finish's record fields: its removal condition's term, then Ra in mm, as an AP242 surface
/// texture's are read.
fn texture_fields(f: &FinishSpec) -> Vec<(String, Field)> {
    let removal = f.removal.as_deref().unwrap_or("any");
    let term = REMOVALS
        .iter()
        .find(|(n, _)| *n == removal)
        .map_or("any process allowed", |(_, t)| t);
    vec![
        ("material removal".into(), Field::Text(term.into())),
        ("Ra".into(), Field::Length(f.ra / 1000.0)),
    ]
}

/// The finish, thread, knurl and note records *spec* authors, features first, then faces, then the
/// default finish, each as the AP242 reading would hold one: `surface_finish` (on faces,
/// attached as a datum is), `internal_thread` (a hole's or pattern's) or `external_thread`,
/// `knurl`, a surface label of kind `note` (its leader from its first face's proven site), and
/// the part's `surface_texture`. Refused: an unknown feature or face ([`crate::spec_gdt`]'s
/// refusals), a thread or knurl on a feature of no kind that takes one.
pub(crate) fn records(
    analysis: &Analysis,
    spec: &Spec,
    part: usize,
) -> Result<Vec<Record>, Invalid> {
    use crate::spec_gdt::{attachments, feature_faces, named_faces, record};
    let patterns = analysis
        .recognition
        .as_ref()
        .map_or(&[][..], |r| r.patterns.as_slice());
    let mut out = Vec::new();
    let finish_record = |base: &str, faces: &[usize], f: &FinishSpec| {
        let mut r = record(
            analysis,
            part,
            format!("{SOURCE_PREFIX}{base}/finish"),
            Category::ManufacturingRequirement,
            faces,
        );
        r.kind = "surface_finish".into();
        r.label = format!("Ra {} µm", crate::pmi::fmt_g3(f.ra));
        r.fields = texture_fields(f);
        r.geometry = Some(crate::pmi::finish_geometry_of(
            &r,
            &attachments(analysis, &r),
        ));
        r
    };
    let note_record = |base: &str, faces: &[usize], text: &str| {
        let mut r = record(
            analysis,
            part,
            format!("{SOURCE_PREFIX}{base}/note"),
            Category::SurfaceLabel,
            faces,
        );
        r.kind = "note".into();
        r.label = text.trim().to_string();
        // Its leader hangs from each face's first proven site (a label's `sites`); its view
        // follows its faces' longest extent, as a common label's does.
        let mut g = crate::pmi::Geometry {
            reasons: crate::pmi::placements_reason(&r).into_iter().collect(),
            ..Default::default()
        };
        g.sites = r
            .faces
            .iter()
            .filter_map(|&f| analysis.face_attachments[f].sites.first().copied())
            .collect();
        if g.sites.is_empty() {
            g.reasons
                .push("the note's faces give no proven site for its leader".into());
        }
        r.geometry = Some(g);
        r
    };
    for (id, f) in &spec.features {
        if f.finish.is_none() && f.thread.is_none() && f.knurl.is_none() && f.note.is_none() {
            continue;
        }
        let base = format!("features/{}", crate::items::key(id));
        let faces = feature_faces(analysis, id)?;
        if let Some(x) = &f.finish {
            out.push(finish_record(&base, &faces, x));
        }
        // The kind of feature a thread or knurl is on: a hole or hole pattern takes an internal
        // thread; a turned step or boss an external thread or a knurl.
        let kind = analysis
            .features
            .iter()
            .find(|g| g.id == *id)
            .map(|g| g.kind.as_str())
            .or_else(|| {
                patterns
                    .iter()
                    .any(|p| p.id == *id)
                    .then_some("hole_pattern")
            })
            .unwrap_or("");
        let internal = matches!(kind, "hole" | "hole_pattern");
        let external = matches!(kind, "turned_step" | "boss");
        if let Some(t) = &f.thread {
            if !(internal || external) {
                return Err(invalid(
                    format!(
                        "{id} is a {kind}, which takes no thread ({base}/thread): a thread is on a hole, a hole pattern or a turned step."
                    ),
                    vec![],
                ));
            }
            let (nominal, pitch) = metric(&t.designation).map_err(|why| invalid(why, vec![]))?;
            let mut r = record(
                analysis,
                part,
                format!("{SOURCE_PREFIX}{base}/thread"),
                Category::ManufacturingRequirement,
                &faces,
            );
            r.kind = if internal {
                "internal_thread"
            } else {
                "external_thread"
            }
            .into();
            r.label = t.designation.trim().to_string();
            r.fields = vec![
                ("feature".into(), Field::Text(id.clone())),
                ("designation".into(), Field::Text(r.label.clone())),
                ("nominal".into(), Field::Length(nominal)),
                ("pitch".into(), Field::Length(pitch)),
            ];
            if let Some(l) = t.length {
                r.fields.push(("length".into(), Field::Length(l)));
            }
            out.push(r);
        }
        if let Some(k) = &f.knurl {
            if !external {
                return Err(invalid(
                    format!(
                        "{id} is a {kind}, which takes no knurl ({base}/knurl): a knurl is on a turned step."
                    ),
                    vec![],
                ));
            }
            let mut r = record(
                analysis,
                part,
                format!("{SOURCE_PREFIX}{base}/knurl"),
                Category::ManufacturingRequirement,
                &faces,
            );
            r.kind = "knurl".into();
            r.label = format!(
                "{} knurl, {} mm pitch",
                k.pattern,
                crate::pmi::fmt_g3(k.pitch)
            );
            r.fields = vec![
                ("feature".into(), Field::Text(id.clone())),
                ("pattern".into(), Field::Text(k.pattern.clone())),
                ("pitch".into(), Field::Length(k.pitch)),
            ];
            out.push(r);
        }
        if let Some(n) = &f.note {
            out.push(note_record(&base, &faces, n));
        }
    }
    for (key, f) in &spec.faces {
        if f.finish.is_none() && f.note.is_none() {
            continue;
        }
        let base = format!("faces/{}", crate::items::key(key));
        let faces = named_faces(analysis, key)?;
        if let Some(x) = &f.finish {
            out.push(finish_record(&base, &faces, x));
        }
        if let Some(n) = &f.note {
            out.push(note_record(&base, &faces, n));
        }
    }
    if let Some(f) = &spec.sheet.default_finish {
        let mut r = record(
            analysis,
            part,
            format!("{SOURCE_PREFIX}sheet/default_finish"),
            Category::ManufacturingRequirement,
            &[],
        );
        r.kind = "surface_texture".into();
        // As the reading labels a part's own surface texture, which the sheet reads its Ra from.
        r.label = format!(
            "Ra {} µm unless otherwise specified",
            crate::pmi::fmt_g3(f.ra)
        );
        r.fields = texture_fields(f);
        out.push(r);
    }
    Ok(out)
}

/// Whether the Spec's record *s* states what the file's *pmi* states, so the file's is not drawn:
/// a finish on any of the same faces, the part's default finish, a thread (of either kind) or a
/// knurl on any of the same faces.
pub(crate) fn overrides(s: &Record, pmi: &Record) -> bool {
    let thread = |k: &str| k == "internal_thread" || k == "external_thread";
    let shares = || s.faces.iter().any(|f| pmi.faces.contains(f));
    s.category == Category::ManufacturingRequirement
        && pmi.category == Category::ManufacturingRequirement
        && match s.kind.as_str() {
            "surface_texture" => pmi.kind == "surface_texture",
            "surface_finish" | "knurl" => pmi.kind == s.kind && shares(),
            k if thread(k) => thread(&pmi.kind) && shares(),
            _ => false,
        }
}

/// Refuse unknown roles rather than allowing a typo to suppress unrelated coverage.
pub(crate) fn check_claims(analysis: &Analysis, spec: &Spec) -> Result<(), Invalid> {
    for (id, f) in &spec.features {
        if let Some(view) = &f.gusset_view {
            let valid = crate::lint::gussets::groups(analysis).into_iter().any(|g| {
                g.owner.id == *id
                    && g.read.is_ok_and(|(ribs, _)| {
                        ribs[0]
                            .supports
                            .iter()
                            .any(|(k, _)| crate::dims::end_on(*k) == view)
                    })
            });
            if !valid {
                return Err(invalid(
                    format!(
                        "features/{id}/gusset_view must look along a leg of a readable gusset group (front, side or plan)."
                    ),
                    vec![],
                ));
            }
        }
        if f.modelled_thread.is_some()
            && !analysis
                .features
                .iter()
                .any(|f| f.id == *id && f.kind == "modelled_thread")
        {
            return Err(invalid(
                format!("features/{id}/modelled_thread requires a recognised modelled thread."),
                vec![],
            ));
        }
    }
    if spec.features.values().all(|f| f.note_satisfies.is_empty()) {
        return Ok(());
    }
    let ids = crate::dims::measurement_ids(analysis);
    let pocket_roles: std::collections::BTreeSet<_> = crate::lint::pockets::placed(analysis)
        .into_iter()
        .flat_map(|(f, p)| {
            p.ok().into_iter().flat_map(move |p| {
                crate::lint::pockets::locations(&p, analysis.envelope[0])
                    .into_iter()
                    .map(move |(role, _)| format!("d:{}/{role}", f.id))
            })
        })
        .collect();
    for (id, f) in &spec.features {
        for role in &f.note_satisfies {
            if !ids.contains(&format!("d:{id}/{role}"))
                && !pocket_roles
                    .iter()
                    .any(|w| w.feature.id == *id && w.parameter == *role)
                && !(analysis.features.iter().any(|feature| feature.id == *id)
                    && matches!(
                        role.as_str(),
                        "location.length.x" | "location.length.y" | "location.length.z"
                    ))
            {
                return Err(invalid(
                    format!("{role:?} is not a measurement role of {id}."),
                    vec![],
                ));
            }
        }
    }
    Ok(())
}

pub(crate) fn satisfaction(spec: &Spec, source: &str) -> Vec<crate::output::Claim> {
    let Some(id) = source
        .strip_prefix("spec:features/")
        .and_then(|s| s.strip_suffix("/note"))
    else {
        return vec![];
    };
    spec.features
        .get(id)
        .into_iter()
        .flat_map(|f| &f.note_satisfies)
        .map(|p| crate::output::Claim {
            feature: id.into(),
            parameter: p.clone(),
        })
        .collect()
}
