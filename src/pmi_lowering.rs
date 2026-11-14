//! A part's AP242 PMI lowered to drawing items: stage 2a of drawing PMI. draftwright lowers its
//! PMI records in `model/detect.py` (`build_pmi_features`): a complete geometric tolerance
//! becomes a feature control frame (`ControlFrame`), a complete datum a datum feature symbol
//! (`DatumRef`), each with the view and strip it is drawn in and the model-space site its leader
//! hangs from (`model/declare.py`'s `gdt_target`, and `annotations/_gdt.py`'s
//! `_datum_alternate_view`). This module makes the same decisions from the Analysis's PMI
//! ([`crate::pmi::Reading`]); [`crate::dims::gdt`] places and draws what it lowers.
//!
//! - **A feature control frame** carries the record's characteristic, its magnitude as
//!   draftwright prints it (`_fmt_pmi_magnitude`), the zone's `⌀` or `S⌀`, the material
//!   condition (`Ⓜ`, `Ⓛ`), the datum letters in precedence order with repeats removed, and the
//!   all-around or all-over scope. It is drawn in the view that sees its axis end-on (draftwright's
//!   face-on rule: the faces' common cylinder axis when every face is a cylinder on one, else
//!   their box's longest extent, else z), in that view's roomiest strip (below the front and the
//!   side, above the plan), its leader from the centre of the faces' box.
//! - **A datum feature symbol** carries its letter. It is drawn in the view that sees its plane
//!   or cylinder edge-on (x and z: the front; y: the side), from a proven site on its first face,
//!   on the side its outward normal points to (a plane's) or its site lies on (a cylinder's): a
//!   y-normal plane whose side view is not shown is drawn in the plan instead.
//!
//! - **A surface label or common label** (text on edges or faces: `A`, `F1.1`, `2X R`) carries
//!   its text, drawn with a leader from its first proven site (draftwright's `ref_pts[0]`: a
//!   surface label's edge at its parameter midpoint, a common label's face at the vertex nearest
//!   the face's box centre, [`label_geometry`]) in the front view, or the side view when its
//!   references' box is longest along y, above or below by whether the site is above the part's
//!   centre (`build_pmi_features`' label branch, drawn as `render_gdt` draws a `Note`). A
//!   label of a lone symbol (ctc04's and ftc07's `⌴`, ftc07's private-use U+F055: a fragment of
//!   a callout whose sizes the file states elsewhere) is not drawn, as it would print an empty box.
//! - **A thread or knurl** ([`manufacturing`], `lower_ap242_manufacturing_requirements`) is no
//!   item of its own: it is given to the one hole or turned step its cylinder proves, its words
//!   and structured statement reconciled and its text checked against the cylinder, and that
//!   feature's callout prints its suffix (`⌀1.6 ↧ 4 M2 x 0.4-6H RH; 2.5 MIN FULL THREAD`), or,
//!   with two or more and one suffix over 52 characters, `SEE MFG n` and the manufacturing
//!   requirements table states it ([`crate::tables::manufacturing`]).
//! - **Drawing-wide requirements** ([`sheet_requirements`], `lower_ap242_document_requirements`):
//!   the one general tolerance fills the title block's tolerance cell, the one default surface
//!   texture (`Ra 3.2 µm unless otherwise specified`) is drawn beside the title block, and each
//!   part note (semantic text: general note, surface treatment, heat treatment or edge
//!   condition) is a general note before the Spec notes: indexed notes first by `note index`,
//!   then unindexed notes in model order; two of one default are
//!   ambiguous and neither is drawn. PMI note omission and Spec duplicate reconciliation use
//!   [`crate::notes`]; the model and existing Spec notes are never migrated by planning.
//!   No-requirement values remain read as info, without ink or a Problem. Bare coating and
//!   heat-treatment values get their kind as a prefix; process prose stays as authored.
//!
//! What is not lowered is said, never guessed: a record whose reading left blockers (datum
//! targets, composite relations, projected or non-uniform zones, datum modifiers, common
//! datums, …), a modifier draftwright has no drawing for (only all-around, all-over, the zone's
//! form and the material condition are drawn, as in draftwright), an attachment its faces do
//! not prove, or a thread or knurl no one feature owns (`pmi_not_lowered`); a source the reading
//! did not read (`pmi_not_extracted`); a file whose PMI could not be read (`pmi_not_read`);
//! graphical callouts are triaged against their semantic carriers ([`crate::graphical_pmi`]),
//! with `pmi_presentation_not_read` only a fallback for readings without an inventory. Toleranced and basic
//! dimensions are lowered with the other dimensions ([`crate::dims`]' PMI dimensions, stage 2b),
//! from where their faces put them ([`dimension_geometry`], here).
//!
//! draftwright draws its PMI only when asked (`pmi="annotate"`; its default is off). The port has
//! no such option: a part's PMI is drawn whenever it has some. Drawing-wide part notes are
//! lowered together in [`sheet_requirements`], so a PMI policy can gate that operation.

use std::collections::{BTreeMap, BTreeSet};

use haecceity::Part;
use haecceity::geom::Surface;
use serde::{Deserialize, Serialize};

use crate::model::{Analysis, Problem};
use crate::pmi::{Category, Geometry, Identities, Outcome, Reading, Record};

/// A record lowered to a drawing item, before placement.
#[derive(Clone, Debug, PartialEq)]
pub struct Lowered {
    /// The record's item ID, `pmi:<identity>` ([`crate::pmi::identity`]), e.g.
    /// `pmi:geometric_tolerance.flatness:f12`; never its Part 21 instance, which is `source`.
    pub id: String,
    pub source: String,
    pub glyph: Glyph,
    /// The view it is drawn in, `front`, `plan` or `side`, and its strip there: `above`,
    /// `below`, `left` or `right`.
    pub view: &'static str,
    pub side: &'static str,
    /// Where its leader hangs from, mm in the drawing's frame.
    pub site: [f64; 3],
    /// A datum's other proven sites on its face, for placement to fall back on.
    pub alternatives: Vec<[f64; 3]>,
    pub faces: Vec<usize>,
    pub basis: String,
}

/// What a lowered record draws.
#[derive(Clone, Debug, PartialEq)]
pub enum Glyph {
    ControlFrame {
        /// draftwright's characteristic name (`position`, `flatness`, …).
        characteristic: String,
        /// The magnitude as printed.
        tolerance: String,
        /// `diameter` (`⌀`) or `spherical_diameter` (`S⌀`).
        zone: Option<&'static str>,
        /// `M` or `L`.
        material: Option<&'static str>,
        datums: Vec<String>,
        all_around: bool,
        all_over: bool,
    },
    Datum {
        letter: String,
        /// `plane` or `cylinder`.
        surface: String,
    },
    /// A surface or common label's text.
    Label { text: String },
    /// A semantic feature note: a numbered flag referring to the notes block,
    /// routed by the same callout machinery as the other labels.
    FlagNote { text: String, number: usize },
    /// A face's ISO 1302 surface texture symbol: the requirement haecceity's shared notation
    /// draws (#258), its parameters one per line (`Ra 0.8`, in µm, as the record states them to
    /// three significant figures) under its material removal condition's mark.
    Finish {
        texture: haecceity::pmi::SurfaceTexture,
        /// `plane` or `cylinder`.
        surface: String,
        /// Its sites lie inside the cylinder's outline, seen head-on ([`Geometry::face_on`]):
        /// its leader ends in a dot there, not an arrowhead on a silhouette.
        face_on: bool,
    },
}

/// Modifiers a feature control frame draws (draftwright's `_SUPPORTED_GTOL_SCOPE_MODIFIERS` and
/// its zone and material qualifiers).
const SCOPE: [&str; 2] = ["all_around", "all_over"];
const QUALIFIERS: [&str; 4] = [
    "diameter_zone",
    "spherical_diameter_zone",
    "maximum_material_requirement",
    "least_material_requirement",
];

/// The view that sees world axis *k* end-on (draftwright's `_END_ON`), and its feature strip
/// (`_FEATURE_SIDE`).
fn face_on(k: usize) -> (&'static str, &'static str) {
    match k {
        0 => ("side", "below"),
        1 => ("front", "below"),
        _ => ("plan", "above"),
    }
}

fn axis_index(a: &str) -> Option<usize> {
    ["X", "Y", "Z"].iter().position(|x| *x == a)
}

/// The Analysis's PMI lowered: the items to draw, and the problems met (none when the part has
/// no PMI). *shown* says whether a principal view (`front`, `plan`, `side`) is on the sheet.
pub fn lower(analysis: &Analysis, shown: &dyn Fn(&str) -> bool) -> (Vec<Lowered>, Vec<Problem>) {
    // Lowering reads only immutable Analysis and which principal views are shown.
    // Demand, placement and recovery otherwise repeat the same geometric reading.
    let names = ["front", "plan", "side"];
    let mask = names.map(shown);
    crate::planning::memo(analysis, "lowered-pmi", mask.map(u8::from).to_vec(), || {
        lower_uncached(analysis, &|name| {
            names
                .iter()
                .zip(mask)
                .any(|(n, visible)| *n == name && visible)
        })
    })
}

/// Lower into an explicit drawing unit; source evidence remains in millimetres.
pub(crate) fn lower_in(
    analysis: &Analysis,
    shown: &dyn Fn(&str) -> bool,
    units: crate::units::Units,
) -> (Vec<Lowered>, Vec<Problem>) {
    let (mut items, problems) = lower(analysis, shown);
    if !units.is_mm() {
        for item in &mut items {
            if let Glyph::ControlFrame { tolerance, .. } = &mut item.glyph
                && let Ok(mm) = tolerance.parse::<f64>()
            {
                *tolerance = units.magnitude(mm);
            }
        }
    }
    (items, problems)
}

fn lower_uncached(
    analysis: &Analysis,
    shown: &dyn Fn(&str) -> bool,
) -> (Vec<Lowered>, Vec<Problem>) {
    let mut items = Vec::new();
    let mut problems = Vec::new();
    let report = match &analysis.pmi {
        None => return (items, problems),
        Some(Reading::Refused(why)) => {
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
            return (items, problems);
        }
        Some(Reading::Read(r)) => r,
    };
    // The Spec's datums and controls merged into the reading: what overriding met
    // ([`crate::spec_gdt`]).
    problems.extend(report.spec_problems.iter().cloned());
    problems.extend(report.reader.problems.iter().cloned());
    for s in report
        .sources
        .iter()
        .filter(|s| s.outcome == Outcome::NotExtracted)
    {
        // A requirement lost is an error; a shape aspect, property or standard the reader did
        // not read states no requirement on its own.
        let requirement = s.category != Category::Other;
        problems.push(Problem {
            id: format!("p:pmi_not_extracted:{}", s.id),
            code: "pmi_not_extracted".into(),
            severity: if requirement { "error" } else { "info" }.into(),
            message: format!(
                "A STEP {} is not drawn because its annotation is not supported. Add the requirement to the Spec and review it manually.",
                s.id.split(':').next().unwrap_or("annotation").replace('_', " ")
            ),
            subjects: Vec::new(),
            refs: vec![s.id.clone(), s.entity.clone(), s.reason.clone()],
            ..Default::default()
        });
    }
    if report.reader.callouts.is_empty() && report.accounting.presentation > 0 {
        problems.push(Problem {
            id: "p:pmi_presentation_not_read:part".into(),
            code: "pmi_presentation_not_read".into(),
            severity: "info".into(),
            message: format!(
                "The STEP reading has no callout inventory (the fallback for older reads). Its {} instance(s) of PMI presentation (graphic annotation) are counted, not read or drawn; only semantic PMI can be checked.",
                report.accounting.presentation
            ),
            subjects: Vec::new(),
            refs: vec!["part".into()],
            ..Default::default()
        });
    }
    let centre = {
        let [lo, hi] = analysis.envelope;
        [0, 1, 2].map(|k| 0.5 * (lo[k] + hi[k]))
    };
    let mut owned: Option<Manufacturing> = None;
    let ids = report.identities();
    for r in &report.records {
        match r.category {
            Category::GeometricTolerance | Category::Datum => {
                let lowered = if r.category == Category::Datum {
                    datum(r, centre)
                } else {
                    control_frame(r, centre)
                };
                let lowered = identified(lowered, &ids, r);
                match lowered {
                    Ok(mut item) => {
                        alternate_view(&mut item, shown);
                        items.push(item);
                    }
                    Err(reasons) => problems.push(Problem {
                        id: format!("p:pmi_not_lowered:{}", r.source_id),
                        code: "pmi_not_lowered".into(),
                        severity: "error".into(),
                        message: format!(
                            "PMI {} ({}) is not drawn: {}.",
                            r.source_id,
                            r.label,
                            crate::pmi::reader_reason(&reasons.join("; "))
                        ),
                        subjects: Vec::new(),
                        refs: std::iter::once(r.source_id.clone())
                            .chain(reasons)
                            .collect(),
                        ..Default::default()
                    }),
                }
            }
            // A face's surface texture is its ISO 1302 symbol, attached as a datum's symbol is.
            Category::ManufacturingRequirement if r.kind == "surface_finish" => {
                match identified(surface_finish(r, centre), &ids, r) {
                    Ok(mut item) => {
                        alternate_view(&mut item, shown);
                        items.push(item);
                    }
                    Err(reasons) => problems.push(Problem {
                        id: format!("p:pmi_not_lowered:{}", r.source_id),
                        code: "pmi_not_lowered".into(),
                        severity: "error".into(),
                        message: format!(
                            "PMI surface finish {} ({}) is not drawn: {}.",
                            r.source_id,
                            r.label,
                            crate::pmi::reader_reason(&reasons.join("; "))
                        ),
                        subjects: Vec::new(),
                        refs: std::iter::once(r.source_id.clone())
                            .chain(reasons)
                            .collect(),
                        ..Default::default()
                    }),
                }
            }
            // A surface label, and a common label (read as a dimension), is drawn as its text.
            Category::SurfaceLabel => {
                if r.kind == "flag_note" {
                    match identified(label(r, centre), &ids, r) {
                        Ok(mut item) => {
                            item.glyph = Glyph::FlagNote {
                                text: r.label.clone(),
                                number: 0,
                            };
                            items.push(item);
                        }
                        Err(reasons) if r.blockers.is_empty() && !r.label.trim().is_empty() => problems.push(Problem {
                            id: format!("p:pmi_note_unattached:{}", r.source_id), code: "pmi_note_unattached".into(), severity: "warning".into(),
                            message: format!("Feature note {} is stated in the notes block without a flag: {}.", r.source_id, crate::pmi::reader_reason(&reasons.join("; "))),
                            refs: vec![r.source_id.clone()], ..Default::default()
                        }),
                        Err(_) => labelled(&mut items, &mut problems, &ids, r, centre),
                    }
                } else {
                    labelled(&mut items, &mut problems, &ids, r, centre);
                }
            }
            Category::Dimension if r.kind == "common_label" => {
                labelled(&mut items, &mut problems, &ids, r, centre)
            }
            // A dimension is stated or refused by stage 2b (`dims::pmi_dimensions`), never here.
            Category::Dimension => {}
            // A drawing-wide requirement is the sheet's ([`sheet_requirements`]).
            Category::ManufacturingRequirement if sheet_kind(&r.kind) => {}
            // A thread or knurl is printed on the callout of the one feature its cylinder proves,
            // or refused with draftwright's reason ([`manufacturing`]).
            Category::ManufacturingRequirement if owned_kind(&r.kind) => {
                let m = owned.get_or_insert_with(|| manufacturing(analysis));
                problems.extend(
                    m.refused
                        .iter()
                        .filter(|p| p.refs.first() == Some(&r.source_id))
                        .cloned(),
                );
            }
            // Other manufacturing requirements are read, not drawn yet: said with draftwright's
            // code for a record that produced no feature, never dropped. A face's requirement is
            // a warning; a part's note is information.
            _ => {
                let face = !r.faces.is_empty() || !r.edges.is_empty();
                problems.push(Problem {
                    id: format!("p:pmi_not_lowered:{}", r.source_id),
                    code: "pmi_not_lowered".into(),
                    severity: if face { "warning" } else { "info" }.into(),
                    message: format!(
                        "The STEP {} annotation ({}) is not supported for drawing. Add the requirement to the Spec and review it manually.",
                        r.kind.replace('_', " "),
                        r.label
                    ),
                    subjects: Vec::new(),
                    refs: vec![r.source_id.clone(), "drawing it is not ported yet".into()],
                    ..Default::default()
                });
            }
        }
    }
    (items, problems)
}

/// draftwright's `_datum_alternate_view`: a y-normal plane whose side view is not drawn is shown
/// in the plan, below for left and above for right (a datum's symbol, and a face finish's).
fn alternate_view(item: &mut Lowered, shown: &dyn Fn(&str) -> bool) {
    if let Glyph::Datum { surface, .. } | Glyph::Finish { surface, .. } = &item.glyph
        && surface == "plane"
        && item.view == "side"
        && !shown("side")
        && shown("plan")
    {
        item.side = if item.side == "left" {
            "below"
        } else {
            "above"
        };
        item.view = "plan";
        item.basis += "; the side view is not drawn, so the plan carries it";
    }
}

/// *lowered* with its record's item ID, or refused when the record has none: its identity is
/// another record's too (ADR R1 invariant 5), said beside any other reason.
fn identified(
    lowered: Result<Lowered, Vec<String>>,
    ids: &Identities,
    r: &Record,
) -> Result<Lowered, Vec<String>> {
    match (lowered, ids.id(&r.source_id)) {
        (Ok(mut item), Ok(id)) => {
            item.id = id.to_string();
            Ok(item)
        }
        (Ok(_), Err(why)) => Err(vec![why]),
        (Err(mut reasons), Err(why)) => {
            reasons.push(why);
            Err(reasons)
        }
        (Err(reasons), Ok(_)) => Err(reasons),
    }
}

/// A surface or common label lowered into *items*, or its refusal into *problems*.
fn labelled(
    items: &mut Vec<Lowered>,
    problems: &mut Vec<Problem>,
    ids: &Identities,
    r: &Record,
    centre: [f64; 3],
) {
    match identified(label(r, centre), ids, r) {
        Ok(item) => items.push(item),
        Err(reasons) => problems.push(Problem {
            id: format!("p:pmi_not_lowered:{}", r.source_id),
            code: "pmi_not_lowered".into(),
            // A value-less radius, or a lone symbol, loses nothing the drawn faces and their
            // dimensions do not state.
            severity: if reasons
                .iter()
                .all(|why| why == VALUELESS_RADIUS || why == LONE_SYMBOL)
            {
                "warning"
            } else {
                "error"
            }
            .into(),
            message: format!(
                "PMI {} {} ({}) is not drawn: {}.",
                r.kind.replace('_', " "),
                r.source_id,
                r.label,
                crate::pmi::reader_reason(&reasons.join("; "))
            ),
            subjects: Vec::new(),
            refs: std::iter::once(r.source_id.clone())
                .chain(reasons)
                .collect(),
            ..Default::default()
        }),
    }
}

/// Why a common label that states a radius without its value (`4X R`) is not drawn (#214).
const VALUELESS_RADIUS: &str = "it states a radius without its value (ASME Y14.5's full radius, set by the dimensions of the faces it joins), which an ISO 129-1 sheet does not print; its faces' dimensions should state it";

/// Why a label that is a lone symbol with no letter or digit (`⌴`, or a private-use glyph of the
/// authoring system's font) is not drawn.
const LONE_SYMBOL: &str = "its text is a lone symbol with no size, depth or name, so it would print as an empty box; the requirement it marks is stated, or not, by its features' own dimensions";

/// A surface or common label as a leadered text label (`build_pmi_features`' label branch), or
/// why it is not one: its reading's or attachment's blockers, no text, a lone symbol, no proven
/// site, or a radius without its value.
fn label(r: &Record, centre: [f64; 3]) -> Result<Lowered, Vec<String>> {
    let mut reasons = blockers(r);
    if r.label.trim().is_empty() {
        reasons.push("the label has no text".into());
    } else if !r.label.chars().any(char::is_alphanumeric) {
        reasons.push(LONE_SYMBOL.into());
    }
    let tokens: Vec<&str> = r.label.split_whitespace().collect();
    if r.kind == "common_label"
        && tokens
            .iter()
            .enumerate()
            .any(|(i, t)| *t == "R" && tokens.get(i + 1).is_none_or(|n| n.parse::<f64>().is_err()))
    {
        reasons.push(VALUELESS_RADIUS.into());
    }
    let g = r.geometry.as_ref();
    let site = g.and_then(|g| g.sites.first().copied());
    if site.is_none() && reasons.is_empty() {
        reasons.push("the label's references give no proven site".into());
    }
    let (Some(site), true) = (site, reasons.is_empty()) else {
        return Err(dedup(reasons));
    };
    let axis = g
        .and_then(|g| g.axis.as_deref())
        .unwrap_or(r.dominant_axis.as_str());
    let view = match g.and_then(|g| g.view.as_deref()) {
        Some("plan") => "plan",
        Some("side") => "side",
        Some("front") => "front",
        _ => {
            if axis == "Y" {
                "side"
            } else {
                "front"
            }
        }
    };
    let side = if site[2] >= centre[2] {
        "above"
    } else {
        "below"
    };
    let what = if matches!(r.kind.as_str(), "note" | "flag_note") {
        // A note the Spec authors on a feature or face ([`crate::spec_notes`]).
        "note"
    } else if r.category == Category::SurfaceLabel {
        "surface label"
    } else {
        "common label"
    };
    Ok(Lowered {
        id: String::new(),
        source: r.source_id.clone(),
        glyph: Glyph::Label {
            text: r.label.clone(),
        },
        view,
        side,
        site,
        alternatives: Vec::new(),
        faces: r.faces.clone(),
        basis: format!(
            "{}: the {} {what} {} ({}), drawn in the {view} view (its references' longest extent is {}) on the {side} side, as its site lies {side} the part's centre, its leader from its first proven site",
            authored_by(r).0,
            authored_by(r).1,
            r.label,
            r.source_id,
            axis.to_lowercase()
        ),
    })
}

/// Where a surface or common label attaches (draftwright's `_surface_label_topology` and
/// `_common_label_topology`): a surface label's edges each at their parameter midpoint, a common
/// label's faces each at the vertex nearest the face's box centre; `axis` is the longest extent
/// of their box (`_dominant_from_bbox`), none when it has none.
pub fn label_geometry(part: &Part, r: &Record) -> Geometry {
    let mut g = Geometry::default();
    g.reasons.extend(crate::pmi::placements_reason(r));
    let surface = r.category == Category::SurfaceLabel && r.kind != "flag_note";
    let (noun, wrong) = if surface {
        ("surface label", !r.faces.is_empty())
    } else {
        ("common-label", !r.edges.is_empty())
    };
    if wrong {
        g.reasons.push(format!(
            "one {} reference is not {}",
            noun.replace('-', " "),
            if surface { "an edge" } else { "a face" }
        ));
    }
    let mut boxes = Vec::new();
    if surface {
        for &e in &r.edges {
            let Some(edge) = part.edges.get(e) else {
                continue;
            };
            let closed = edge.vertices.0 == edge.vertices.1;
            let (a, b) = haecceity::sampling::edge_interval(
                &edge.curve,
                edge.start,
                edge.end,
                edge.same_sense,
                closed,
            );
            let p = edge.curve.value(0.5 * (a + b));
            if a.is_finite() && b.is_finite() && p.iter().all(|c| c.is_finite()) {
                g.sites.push(p);
            } else {
                g.reasons.push(format!(
                    "{noun} reference could not be measured (edge has no finite parameter interval)"
                ));
            }
            boxes.extend(edge_box(part, e));
        }
    } else {
        for &f in &r.faces {
            let b = face_box(part, f);
            let c = [0, 1, 2].map(|i| 0.5 * (b[i] + b[i + 3]));
            let d = |p: &[f64; 3]| (0..3).map(|i| (p[i] - c[i]).powi(2)).sum::<f64>();
            let face = &part.faces[f];
            let vertices = face.loops.iter().flat_map(|l| {
                l.edges
                    .iter()
                    .flat_map(|&(e, _)| [part.edges[e].start, part.edges[e].end])
                    .chain(l.vertex)
            });
            match vertices.fold(None, |best: Option<[f64; 3]>, p| match best {
                Some(q) if d(&q) <= d(&p) => Some(q),
                _ => Some(p),
            }) {
                Some(p) => g.sites.push(p),
                None => g.reasons.push(format!(
                    "{noun} reference could not be measured (face has no topological vertices)"
                )),
            }
            boxes.push(b);
        }
    }
    if r.kind == "flag_note"
        && let Some(&first) = r.faces.first()
        && let Surface::Cylinder { frame, .. } = &part.faces[first].surface
        && let Some(k) = exact_axis(frame.z)
        && r.faces.iter().all(|&f| matches!(&part.faces[f].surface, Surface::Cylinder { frame, .. } if exact_axis(frame.z) == Some(k)))
    {
        // Identify a bore in the view that actually shows its rim. The existing
        // flag/leader solver can then use the same physical attachment as a bore callout.
        g.view = Some(["side", "front", "plan"][k].into());
    }
    if g.sites.is_empty() && g.reasons.is_empty() {
        g.reasons
            .push(format!("{noun} reference geometry is unavailable"));
    }
    if let Some(first) = boxes.first().copied() {
        let b = boxes.iter().fold(first, |mut m, b| {
            for i in 0..3 {
                m[i] = m[i].min(b[i]);
                m[i + 3] = m[i + 3].max(b[i + 3]);
            }
            m
        });
        let spans = [0, 1, 2].map(|i| (b[i + 3] - b[i]).abs());
        let k = (0..3).fold(0, |k, i| if spans[i] > spans[k] { i } else { k });
        if spans[k] > 1e-6 {
            g.axis = Some(XYZ[k].to_string());
        }
    }
    g.reasons = dedup(std::mem::take(&mut g.reasons));
    g
}

/// The drawing-wide manufacturing requirements the sheet carries
/// ([`sheet_requirements`]): general tolerances, a default surface texture and part notes.
pub fn sheet_kind(kind: &str) -> bool {
    matches!(
        kind,
        "general_tolerances"
            | "surface_texture"
            | "edge_condition"
            | "general_note"
            | "surface_treatment"
            | "heat_treatment"
    )
}

/// Canonical part-note kinds, including the NIST/haecceity spellings.
pub(crate) fn part_note_kind(name: &str) -> Option<&'static str> {
    match name {
        "general note" => Some("general_note"),
        "surface treatment" | "coating" => Some("surface_treatment"),
        "heat treatment" => Some("heat_treatment"),
        "edge condition" | "edges" => Some("edge_condition"),
        _ => None,
    }
}

/// Explicit absence of a part requirement, preserved in the reading and inventory as info.
pub(crate) fn no_part_requirement(r: &Record) -> bool {
    r.category == Category::ManufacturingRequirement
        && r.blockers.is_empty()
        && matches!(
            r.kind.as_str(),
            "general_note" | "surface_treatment" | "heat_treatment" | "edge_condition"
        )
        && matches!(
            r.label.trim().to_ascii_lowercase().as_str(),
            "" | "none" | "n/a" | "na" | "-"
        )
}

/// Keep process prose as authored; qualify a bare coating/heat-treatment value: haecceity's
/// shared wording (#258), which names the kinds as AP242's semantic text items do (`heat
/// treatment`), where a record's kind is `heat_treatment`.
fn part_note_text(r: &Record) -> String {
    haecceity::presentation::notes::part_note_text(&r.kind.replace('_', " "), &r.label)
}

/// A drawing-wide requirement the sheet states.
#[derive(Clone, Debug, PartialEq)]
pub struct SheetRequirement {
    /// The record's item ID, `pmi:<identity>`.
    pub id: String,
    pub source: String,
    /// `general_tolerance` (the title block's tolerance cell), `default_surface_finish` (a
    /// surface texture symbol beside the title block), `document_note` (a general note), or
    /// `no_requirement` (an explicit absence: read as info, with no drawing).
    pub kind: &'static str,
    /// What the sheet states: the tolerance's designation, the finish's Ra value in µm, the
    /// note's text.
    pub value: String,
    pub basis: String,
    /// A default finish's material removal condition as an AP242 surface texture states it
    /// (`material removal required`, `no material removal`); empty for any process allowed, and
    /// for a finish stated in words.
    pub removal: String,
}

/// The Analysis's drawing-wide requirements (draftwright's `lower_ap242_document_requirements`),
/// and the problems met: the one general tolerance not blocked (its designation: the prose before
/// any `;`, or the structured tolerance class, which must agree with it), the one surface
/// texture stated as `Ra <value> µm unless otherwise specified`, and every part note on no
/// face or edge, with text: explicit note indices first, then unindexed notes in model order.
/// Two general tolerances, or two surface textures, are ambiguous:
/// neither is drawn. A record the reading blocked, or one whose statement is not a supported
/// default, is said with its reason (`pmi_not_lowered`). Other document kinds draftwright knows
/// (a datum scheme, a model representation note) are reported as not ported.
pub(crate) fn sheet_requirements_in(
    analysis: &Analysis,
    units: crate::units::Units,
) -> (Vec<SheetRequirement>, Vec<Problem>) {
    let (mut requirements, problems) = sheet_requirements(analysis);
    if !units.is_mm() {
        for r in &mut requirements {
            if r.kind == "default_surface_finish"
                && let Ok(um) = r.value.parse::<f64>()
            {
                r.value = units.magnitude(um / 1000.0);
            }
        }
    }
    (requirements, problems)
}

pub fn sheet_requirements(analysis: &Analysis) -> (Vec<SheetRequirement>, Vec<Problem>) {
    let mut out = Vec::new();
    let mut problems = Vec::new();
    let Some(Reading::Read(report)) = &analysis.pmi else {
        return (out, problems);
    };
    let refuse = |problems: &mut Vec<Problem>, r: &Record, why: &str| {
        problems.push(Problem {
            id: format!("p:pmi_not_lowered:{}", r.source_id),
            code: "pmi_not_lowered".into(),
            severity: "error".into(),
            message: format!(
                "PMI {} {} ({}) is not drawn: {}.",
                r.kind.replace('_', " "),
                r.source_id,
                r.label,
                crate::pmi::reader_reason(why)
            ),
            subjects: Vec::new(),
            refs: vec![r.source_id.clone(), why.to_string()],
            ..Default::default()
        })
    };
    let records: Vec<&Record> = report
        .records
        .iter()
        .filter(|r| {
            (r.category == Category::ManufacturingRequirement && sheet_kind(&r.kind))
                || r.kind == "flag_note"
        })
        .collect();
    let ids = report.identities();
    for r in &records {
        if no_part_requirement(r) {
            // No requirement is authored: retain the read source, never demand a fix or ink.
            if let Ok(id) = ids.id(&r.source_id) {
                out.push(SheetRequirement {
                    id: id.into(),
                    source: r.source_id.clone(),
                    kind: "no_requirement",
                    value: r.label.clone(),
                    removal: String::new(),
                    basis: format!(
                        "info: AP242 {} {} states no requirement; read, with no drawing needed",
                        r.kind.replace('_', " "),
                        r.source_id
                    ),
                });
            }
            continue;
        }
        let mut why = dedup(r.blockers.clone());
        why.extend(ids.id(&r.source_id).err());
        if !why.is_empty() {
            refuse(&mut problems, r, &why.join("; "));
        }
    }
    let open: Vec<&Record> = records
        .iter()
        .copied()
        .filter(|r| {
            !no_part_requirement(r) && r.blockers.is_empty() && ids.ids.contains_key(&r.source_id)
        })
        .collect();
    let id = |r: &Record| ids.ids[&r.source_id].clone();
    let of =
        |kind: &str| -> Vec<&Record> { open.iter().copied().filter(|r| r.kind == kind).collect() };
    let tolerances = of("general_tolerances");
    match tolerances[..] {
        [] => {}
        [r] => match designation(r) {
            Ok(d) => out.push(SheetRequirement {
                id: id(r),
                source: r.source_id.clone(),
                kind: "general_tolerance",
                basis: format!(
                    "source-authored: the AP242 general tolerance {} ({}), the part's one, in the title block's tolerance cell (draftwright's GeneralTolerance)",
                    d, r.source_id
                ),
                value: d,
                removal: String::new(),
            }),
            Err(why) => refuse(&mut problems, r, why),
        },
        _ => {
            for r in &tolerances {
                refuse(
                    &mut problems,
                    r,
                    "ambiguous document default: multiple general-tolerance requirements",
                );
            }
        }
    }
    let finishes = of("surface_texture");
    match finishes[..] {
        [] => {}
        [r] => match default_ra(&r.label) {
            Some(ra) => out.push(SheetRequirement {
                id: id(r),
                source: r.source_id.clone(),
                kind: "default_surface_finish",
                basis: format!(
                    "{}: the {} surface texture '{}' ({}), the part's default finish, drawn as a surface texture symbol beside the title block (draftwright's DefaultSurfaceFinish)",
                    authored_by(r).0,
                    authored_by(r).1,
                    r.label,
                    r.source_id
                ),
                value: ra,
                removal: r
                    .fields
                    .iter()
                    .find_map(|(n, f)| match f {
                        crate::pmi::Field::Text(t)
                            if n == "material removal" && t != "any process allowed" =>
                        {
                            Some(t.clone())
                        }
                        _ => None,
                    })
                    .unwrap_or_default(),
            }),
            None => refuse(
                &mut problems,
                r,
                "surface-texture requirement is not a supported document default",
            ),
        },
        _ => {
            for r in &finishes {
                refuse(
                    &mut problems,
                    r,
                    "ambiguous document default: multiple surface-texture requirements",
                );
            }
        }
    }
    let mut notes: Vec<_> = open
        .iter()
        .copied()
        .filter(|r| {
            matches!(
                r.kind.as_str(),
                "edge_condition"
                    | "general_note"
                    | "surface_treatment"
                    | "heat_treatment"
                    | "flag_note"
            )
        })
        .collect();
    notes.sort_by_key(|r| {
        let order = r.fields.iter().find_map(|(key, value)| match value {
            crate::pmi::Field::Text(t) if key == "note order" => t.parse::<u64>().ok(),
            _ => None,
        });
        (r.part, order.is_none(), order.unwrap_or(0), r.part21_id)
    });
    for r in notes {
        if r.kind != "flag_note" && (!r.faces.is_empty() || !r.edges.is_empty()) {
            refuse(
                &mut problems,
                r,
                "face-specific requirement cannot be a document-wide note",
            );
        } else {
            out.push(SheetRequirement {
                id: id(r),
                source: r.source_id.clone(),
                kind: "document_note",
                value: part_note_text(r),
                basis: format!(
                    "source-authored: the AP242 {} '{}' ({}), a general note (draftwright's DocumentNote)",
                    r.kind.replace('_', " "),
                    r.label.trim(),
                    r.source_id
                ),
                removal: String::new(),
            });
        }
    }
    (out, problems)
}

/// A drawing-wide requirement as the plan carries it.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PlannedRequirement {
    /// The record's item ID, `pmi:<identity>`; `source` is its Part 21 provenance.
    pub id: String,
    /// `general_tolerance`, `default_surface_finish`, `document_note` or `no_requirement`.
    pub kind: String,
    pub source: String,
    /// What the sheet states: the designation, `Ra <value>`, the note's text.
    pub text: String,
    /// What carries it: `title_block` (its tolerance cell), `sheet` (the symbol beside the title
    /// block), `notes` (the general note of the same ID), or `none` (no requirement authored).
    pub carrier: String,
    pub basis: String,
    /// Shown, or not drawable as its carrier is not (a general note with no place).
    pub state: crate::items::State,
    /// The default finish's symbol.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub placed: Option<crate::title_block::FinishPlaced>,
}

/// The drawing-wide *requirements* given their carriers on a sheet whose title block is *block*
/// and whose title the spec gives as *given* (the title to draw, *title*, takes the general
/// tolerance), and the problems met. draftwright's precedence (`_resolve_title_document_defaults`):
/// a tolerance the spec states, even blank, wins over the PMI's, which is then not drawn and,
/// when they differ, reported (`step_general_tolerance_disagreement`, a warning, as
/// draftwright's PMI lint reports it).
pub fn plan_sheet(
    requirements: &[SheetRequirement],
    given: &std::collections::BTreeMap<String, String>,
    title: &mut std::collections::BTreeMap<String, String>,
    block: [f64; 4],
) -> (Vec<PlannedRequirement>, Vec<Problem>) {
    let mut out = Vec::new();
    let mut problems = Vec::new();
    for r in requirements {
        let id = r.id.clone();
        let mut planned = PlannedRequirement {
            id: id.clone(),
            kind: r.kind.into(),
            source: r.source.clone(),
            text: r.value.clone(),
            carrier: "notes".into(),
            basis: r.basis.clone(),
            // A general note's is its note's, set when the notes are placed.
            state: crate::items::State::Shown,
            placed: None,
        };
        match r.kind {
            "no_requirement" => {
                planned.carrier = "none".into();
                planned.state = crate::items::State::NotAuthored;
            }
            "general_tolerance" => {
                planned.carrier = "title_block".into();
                match given.get("tolerance") {
                    None => {
                        title.insert("tolerance".into(), r.value.clone());
                    }
                    Some(v) if v.trim() == r.value => {
                        planned.basis +=
                            "; the spec states the same tolerance (sheet/title/tolerance)";
                    }
                    Some(v) => {
                        problems.push(Problem {
                            id: format!("p:step_general_tolerance_disagreement:{}", r.source),
                            code: "step_general_tolerance_disagreement".into(),
                            severity: "warning".into(),
                            message: format!(
                                "The title block states the spec's general tolerance {v:?} (sheet/title/tolerance), which wins; the part's AP242 PMI {} states {:?}, which is not drawn.",
                                r.source, r.value
                            ),
                            subjects: vec!["sheet".into()],
                            refs: vec![r.source.clone(), "sheet/title/tolerance".into()],
                            ..Default::default()
                        });
                        continue;
                    }
                }
            }
            "default_surface_finish" => {
                planned.carrier = "sheet".into();
                planned.text = if r.source == "process" {
                    r.value.clone()
                } else {
                    format!("Ra {}", r.value)
                };
                planned.placed = Some(crate::title_block::default_finish(
                    block,
                    &planned.text,
                    &r.removal,
                ));
            }
            _ => {}
        }
        out.push(planned);
    }
    (out, problems)
}

/// A general tolerance's designation (`_general_tolerance_designation`): its prose before any
/// `;`, or its structured tolerance class, which must agree with the prose unless the prose is
/// only `general tolerances`.
fn designation(r: &Record) -> Result<String, &'static str> {
    let prose = r.label.split(';').next().unwrap_or("").trim().to_string();
    let class = r.fields.iter().find(|(n, _)| n == "tolerance class");
    let d = match (r.fields.is_empty(), class) {
        (true, _) => prose,
        (false, Some((_, crate::pmi::Field::Text(t)))) if !t.trim().is_empty() => {
            if r.label != "general tolerances" && prose != *t {
                return Err("structured general-tolerance class disagrees with prose");
            }
            t.clone()
        }
        (false, _) => return Err("structured general-tolerance class is missing"),
    };
    if d.is_empty() {
        return Err("general-tolerance designation is empty");
    }
    Ok(d)
}

/// The Ra value of a default surface texture stated as draftwright reads one
/// (`Ra <value> µm unless otherwise specified`, case and spacing free, `um`, `µm` or `μm`).
fn default_ra(label: &str) -> Option<String> {
    let lower = label.trim().to_lowercase();
    let words: Vec<&str> = lower.split_whitespace().collect();
    let (ra, rest) = match words.as_slice() {
        ["ra", value, rest @ ..] => (*value, rest.to_vec()),
        _ => return None,
    };
    // The unit may be written against the value (`3.2µm`).
    let (value, rest) = match ["um", "µm", "μm"]
        .iter()
        .find_map(|u| ra.strip_suffix(u).filter(|v| !v.is_empty()))
    {
        Some(v) => (v, rest),
        None => match rest.split_first() {
            Some((u, tail)) if ["um", "µm", "μm"].contains(u) => (ra, tail.to_vec()),
            _ => return None,
        },
    };
    let number = |s: &str| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit());
    let ok = match value.split_once('.') {
        Some((i, f)) => number(i) && number(f),
        None => number(value),
    };
    (ok && rest == ["unless", "otherwise", "specified"]).then(|| value.to_string())
}

/// The principal views the Analysis's PMI items are drawn in, as lowering targets them with
/// every view shown: a turned part keeps a view one of them needs (draftwright's
/// `annotation_view_required`, which vetoes the reduction for any GD&T, datum or label aspect
/// targeting a view the reduced set lacks).
pub fn views(analysis: &Analysis) -> BTreeSet<&'static str> {
    lower(analysis, &|_| true)
        .0
        .iter()
        .map(|i| i.view)
        .collect()
}

/// What stops a record lowering: its reading's blockers, then its attachment's.
fn blockers(r: &Record) -> Vec<String> {
    let mut out = r.blockers.clone();
    if let Some(g) = &r.geometry {
        out.extend(g.reasons.iter().cloned());
    }
    out
}

/// A geometric tolerance as a feature control frame (`build_pmi_features`' first branch and
/// `control_frame`), or why it is not one.
fn control_frame(r: &Record, centre: [f64; 3]) -> Result<Lowered, Vec<String>> {
    let mut reasons = blockers(r);
    let scope: Vec<&str> = r
        .gtol_modifiers
        .iter()
        .map(String::as_str)
        .filter(|m| !QUALIFIERS.contains(m))
        .collect();
    for m in &scope {
        if !SCOPE.contains(m) {
            reasons.push(format!(
                "geometric-tolerance modifier '{m}' is not supported"
            ));
        }
    }
    if scope.len() > 1 {
        reasons.push(format!(
            "geometric-tolerance modifier combination ({}) is not supported",
            scope.join(", ")
        ));
    }
    let Some(value) = r.value else {
        reasons.push("the tolerance states no magnitude".into());
        return Err(dedup(reasons));
    };
    if !reasons.is_empty() {
        return Err(dedup(reasons));
    }
    let has = |m: &str| r.gtol_modifiers.iter().any(|x| x == m);
    let site = r
        .geometry
        .as_ref()
        .filter(|g| !g.targets.is_empty())
        .and_then(|g| g.sites.first())
        .copied()
        .unwrap_or_else(|| {
            r.ref_bbox
                .map_or(centre, |b| [0, 1, 2].map(|k| 0.5 * (b[k] + b[k + 3])))
        });
    let proven = r.geometry.as_ref().and_then(|g| g.axis.as_deref());
    let (axis, why) = match (proven, r.dominant_axis.as_str()) {
        (Some(a), _) => (a, "its faces' common cylinder axis"),
        (None, a @ ("X" | "Y" | "Z")) => (a, "its faces' longest extent"),
        _ => ("Z", "no axis of its own (z by default)"),
    };
    // Invariant: The preceding geometry check admits only x, y or z axes.
    let k = axis_index(axis).expect("an axis letter");
    let (view, side) = face_on(k);
    let mut datums: Vec<String> = Vec::new();
    for d in &r.datum_refs {
        if !datums.contains(d) {
            datums.push(d.clone());
        }
    }
    Ok(Lowered {
        id: String::new(),
        source: r.source_id.clone(),
        glyph: Glyph::ControlFrame {
            characteristic: r.kind.clone(),
            tolerance: crate::pmi::fmt_magnitude(value, None),
            zone: if has("diameter_zone") {
                Some("diameter")
            } else if has("spherical_diameter_zone") {
                Some("spherical_diameter")
            } else {
                None
            },
            material: if has("maximum_material_requirement") {
                Some("M")
            } else if has("least_material_requirement") {
                Some("L")
            } else {
                None
            },
            datums,
            all_around: has("all_around"),
            all_over: has("all_over"),
        },
        view,
        side,
        site,
        alternatives: Vec::new(),
        faces: r.faces.clone(),
        basis: format!(
            "{}: the {} {} tolerance {}, drawn where {} ({}) is seen end-on, in the {view} view's {side} strip (draftwright's face-on rule), its leader from the centre of its faces",
            authored_by(r).0,
            authored_by(r).1,
            r.kind.replace('_', " "),
            r.source_id,
            why,
            axis.to_lowercase()
        ),
    })
}

/// A datum as a datum feature symbol (`build_pmi_features`' datum branch and `datum`), or why it
/// is not one.
fn datum(r: &Record, centre: [f64; 3]) -> Result<Lowered, Vec<String>> {
    // A nonconformance the reader read through (a datum feature naming no face of its own, read
    // as its datum's usage's faces) says how the datum was read, not that it is lost: it goes to
    // the basis, and the faces decide.
    let (read_through, mut reasons): (Vec<String>, Vec<String>) =
        blockers(r).into_iter().partition(|b| read_through(b));
    if r.label.trim().is_empty() {
        reasons.push("the datum has no letter".into());
    }
    let at = edge_on(r, centre, "datum", reasons)?;
    Ok(Lowered {
        id: String::new(),
        source: r.source_id.clone(),
        glyph: Glyph::Datum {
            letter: r.label.trim().to_string(),
            surface: at.surface.clone(),
        },
        view: at.view,
        side: at.side,
        site: at.site,
        alternatives: at.alternatives,
        faces: r.faces.clone(),
        basis: format!(
            "{}: the {} datum {} ({}) on a {} along {}, drawn edge-on in the {} view on the {} side {}",
            authored_by(r).0,
            authored_by(r).1,
            r.label.trim(),
            r.source_id,
            at.surface,
            at.axis.to_lowercase(),
            at.view,
            at.side,
            at.how
        ) + &read_through
            .iter()
            .map(|b| format!("; {}", crate::pmi::reader_reason(b)))
            .collect::<String>(),
    })
}

/// Whether blocker *b* is a reader nonconformance the reader read through as its detail states
/// (`…: read as …`, `…; read as …`), losing nothing.
pub fn read_through(b: &str) -> bool {
    b.starts_with("haecceity nonconformance on ")
        && (b.contains(": read as ") || b.contains("; read as "))
}

/// Who authored record *r*, for its basis: the STEP's AP242 PMI, or the Spec
/// ([`crate::spec_gdt`], whose records' sources are `spec:<path>`).
fn authored_by(r: &Record) -> (&'static str, &'static str) {
    if r.source_id.starts_with(crate::spec_gdt::SOURCE_PREFIX) {
        ("spec-authored", "spec's")
    } else {
        ("source-authored", "AP242")
    }
}

/// Where a datum feature symbol, or a face's surface finish symbol, attaches.
struct EdgeOn {
    view: &'static str,
    side: &'static str,
    site: [f64; 3],
    alternatives: Vec<[f64; 3]>,
    surface: String,
    axis: String,
    how: &'static str,
}

/// The view that sees record *r*'s plane or cylinder edge-on (x and z: the front; y: the side),
/// its first proven site and the strip its outward normal points to (a plane's) or its site lies
/// on (a cylinder's), or why there is none (*reasons* already met, then the attachment's; *noun*
/// names the record: `datum` or `surface finish`).
fn edge_on(
    r: &Record,
    centre: [f64; 3],
    noun: &str,
    reasons: Vec<String>,
) -> Result<EdgeOn, Vec<String>> {
    let mut reasons = reasons;
    let g = r.geometry.as_ref();
    let axis = g.and_then(|g| g.axis.as_deref());
    let surface = g.and_then(|g| g.surface.clone());
    let site = g.and_then(|g| g.sites.first().copied());
    let (Some(axis), Some(surface), Some(site)) = (axis, surface, site) else {
        if reasons.is_empty() {
            reasons.push(format!("{noun} reference surface is unavailable"));
        }
        return Err(dedup(reasons));
    };
    if !reasons.is_empty() {
        return Err(dedup(reasons));
    }
    // Invariant: The preceding geometry check admits only x, y or z axes.
    let k = axis_index(axis).expect("an axis letter");
    let view = if k == 1 { "side" } else { "front" };
    let planar = surface == "plane";
    // A plane's leader follows its normal in the edge-on view; a cylinder attaches radially to
    // its profile.
    let (across, low, high) = if planar && k < 2 {
        (k, "left", "right")
    } else if !planar && k == 2 {
        (0, "left", "right")
    } else {
        (2, "below", "above")
    };
    let side = match g.and_then(|g| g.normal) {
        Some(n) if n[across].abs() < 0.9 => {
            return Err(vec![format!(
                "{noun} surface normal cannot be shown by an axial leader in this view"
            )]);
        }
        Some(n) => {
            if n[across] > 0.0 {
                high
            } else {
                low
            }
        }
        None if planar => {
            return Err(vec![format!(
                "{noun} reference surface has no outward normal"
            )]);
        }
        None => {
            if site[across] >= centre[across] {
                high
            } else {
                low
            }
        }
    };
    let how = if planar {
        "its outward normal points to"
    } else {
        "its site on the profile lies on"
    };
    Ok(EdgeOn {
        view,
        side,
        site,
        // Symbols that must retain their side (notably finishes) use only that silhouette.
        // Datum placement also reads the face attachments to try the opposite proven side.
        alternatives: g
            .map(|g| {
                g.alternatives
                    .iter()
                    .copied()
                    .filter(|p| planar || (p[across] - site[across]).abs() <= 1e-6)
                    .collect()
            })
            .unwrap_or_default(),
        surface,
        axis: axis.to_string(),
        how,
    })
}

/// Moves a cylinder finish's attachment *at* to the strip of its view that carries no
/// dimension lanes, when one of its record's proven sites lies on that side of *centre*: left of
/// the front view for a z axis (the height ladder runs on its right), above it for an x axis
/// (the width chain runs below; `layout::Strips::reserve_gdt`). A y axis's side view carries
/// lanes on both sides, so its side stays. Whether it moved.
fn lane_free_side(at: &mut EdgeOn, r: &Record, centre: [f64; 3]) -> bool {
    let (across, side) = match (at.view, axis_index(&at.axis)) {
        ("front", Some(2)) => (0, "left"),
        ("front", Some(0)) => (2, "above"),
        _ => return false,
    };
    if at.side == side {
        return false;
    }
    let Some(g) = r.geometry.as_ref() else {
        return false;
    };
    let low = side == "left";
    let on: Vec<[f64; 3]> = g
        .sites
        .iter()
        .take(1)
        .chain(&g.alternatives)
        .copied()
        .filter(|p| {
            if low {
                p[across] < centre[across]
            } else {
                p[across] > centre[across]
            }
        })
        .collect();
    let Some((first, rest)) = on.split_first() else {
        return false;
    };
    at.site = *first;
    at.alternatives = rest.to_vec();
    at.side = side;
    true
}

/// A face's AP242 surface texture (`surface_finish`) as its ISO 1302 symbol, attached as a
/// datum feature symbol is ([`edge_on`]), or why it is not: its reading's or attachment's
/// blockers, or no parameter to state.
fn surface_finish(r: &Record, centre: [f64; 3]) -> Result<Lowered, Vec<String>> {
    use haecceity::pmi::{
        Decimal, Length, LengthUnit, MaterialRemoval, SurfaceTexture, SurfaceTextureParameter,
    };
    let mut reasons = blockers(r);
    let mut texture = SurfaceTexture {
        on: None,
        material_removal: MaterialRemoval::AnyProcessAllowed,
        parameters: Vec::new(),
    };
    for (name, value) in &r.fields {
        match value {
            crate::pmi::Field::Text(t) if name == "material removal" => {
                texture.material_removal =
                    MaterialRemoval::from_term(t).unwrap_or(MaterialRemoval::AnyProcessAllowed);
            }
            crate::pmi::Field::Length(mm) => {
                // Stated in µm to three significant figures, as the record's label states it;
                // the stored value is mm. The shared formatter now retains the supplied unit,
                // so give it explicit micrometres for ISO 1302's implicit roughness unit.
                let um: f64 = crate::pmi::fmt_g3(mm * 1000.0)
                    .parse()
                    .unwrap_or(mm * 1000.0);
                if let Ok(value) = Decimal::parse(&um.to_string()) {
                    texture.parameters.push(SurfaceTextureParameter {
                        characteristic: name.clone(),
                        value: Length {
                            value,
                            unit: LengthUnit::Micrometre,
                        },
                    });
                }
            }
            crate::pmi::Field::Text(_) => {}
        }
    }
    if texture.parameters.is_empty() {
        reasons.push("the surface texture states no parameter".into());
    }
    let mut at = edge_on(r, centre, "surface finish", reasons)?;
    let face_on = r.geometry.as_ref().is_some_and(|g| g.face_on);
    let lane_free = at.surface == "cylinder" && lane_free_side(&mut at, r, centre);
    Ok(Lowered {
        id: String::new(),
        source: r.source_id.clone(),
        glyph: Glyph::Finish {
            texture,
            surface: at.surface.clone(),
            face_on,
        },
        view: at.view,
        side: at.side,
        site: at.site,
        alternatives: at.alternatives,
        faces: r.faces.clone(),
        basis: format!(
            "{}: the {} surface texture {} ({}) on a {} along {}, its ISO 1302 symbol on a leader from the face's edge in the {} view on the {} side {}",
            authored_by(r).0,
            authored_by(r).1,
            r.label,
            r.source_id,
            at.surface,
            at.axis.to_lowercase(),
            at.view,
            at.side,
            at.how
        ) + if face_on {
            "; the face shows no silhouette in that view, so the leader ends in a dot on the face inside its outline"
        } else {
            ""
        } + if lane_free {
            "; that strip carries none of the view's dimension lanes"
        } else {
            ""
        },
    })
}

fn dedup(mut v: Vec<String>) -> Vec<String> {
    let mut seen = std::collections::BTreeSet::new();
    v.retain(|s| seen.insert(s.clone()));
    v
}

/// One exact finite cylinder a diameter or radius refers to (draftwright's
/// `CylindricalReference.canonical`): its axis as the foot of the perpendicular from the origin
/// and a unit direction whose largest component is positive, its radius, its extent along that
/// direction from the foot, and whether the material is outside it (`internal`: a bore).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Cylinder {
    pub origin: [f64; 3],
    pub direction: [f64; 3],
    pub radius: f64,
    pub interval: [f64; 2],
    pub internal: bool,
}

impl Cylinder {
    /// The point of its axis halfway along it.
    pub fn midpoint(&self) -> [f64; 3] {
        let t = 0.5 * (self.interval[0] + self.interval[1]);
        [0, 1, 2].map(|i| self.origin[i] + t * self.direction[i])
    }

    /// The world axis (0, 1, 2) it lies along, when it is principal (draftwright's
    /// `principal_axis`, to 1e-6).
    pub fn principal(&self) -> Option<usize> {
        let d = self.direction;
        let k = (0..3).fold(0, |k, i| if d[i].abs() > d[k].abs() { i } else { k });
        ((d[k] - 1.0).abs() <= 1e-6 && (0..3).all(|i| i == k || d[i].abs() <= 1e-6)).then_some(k)
    }
}

const XYZ: [&str; 3] = ["X", "Y", "Z"];

/// Whether *p* lies on face *f*, its boundary included (to 1e-6 mm), as `face_sites` tests it.
fn on_face(part: &Part, f: usize, p: [f64; 3]) -> bool {
    let surface = &part.faces[f].surface;
    let (Some(domain), Some((u, v))) = (part.domain(f), surface.parameters(p, None)) else {
        return false;
    };
    let q = surface.value(u, v);
    let d = ((q[0] - p[0]).powi(2) + (q[1] - p[1]).powi(2) + (q[2] - p[2]).powi(2)).sqrt();
    let e = 1e-9;
    d <= 1e-6
        && [(0.0, 0.0), (e, 0.0), (-e, 0.0), (0.0, e), (0.0, -e)]
            .iter()
            .any(|(du, dv)| domain.contains(u + du, v + dv))
}

/// A face's box as `[xmin, ymin, zmin, xmax, ymax, zmax]`.
fn face_box(part: &Part, f: usize) -> [f64; 6] {
    let b = part.face_bounds(f);
    [b.min[0], b.min[1], b.min[2], b.max[0], b.max[1], b.max[2]]
}

/// An edge's box, from the points that stand in for its curve.
fn edge_box(part: &Part, e: usize) -> Option<[f64; 6]> {
    let edge = part.edges.get(e)?;
    let mut b: Option<[f64; 6]> = None;
    for p in edge.samples.iter().chain([&edge.start, &edge.end]) {
        let o = b.get_or_insert([p[0], p[1], p[2], p[0], p[1], p[2]]);
        for i in 0..3 {
            o[i] = o[i].min(p[i]);
            o[i + 3] = o[i + 3].max(p[i]);
        }
    }
    b
}

/// The axis (0, 1, 2) a unit direction lies along exactly (to 1e-6), as draftwright's planar
/// tests read a plane's normal.
fn exact_axis(d: [f64; 3]) -> Option<usize> {
    let k = (0..3).fold(0, |k, i| if d[i].abs() > d[k].abs() { i } else { k });
    ((d[k].abs() - 1.0).abs() <= 1e-6 && (0..3).all(|i| i == k || d[i].abs() <= 1e-6)).then_some(k)
}

/// The face's cylinder, canonical, or why it is none.
fn cylinder(part: &Part, f: usize) -> Result<Cylinder, &'static str> {
    let face = &part.faces[f];
    let Surface::Cylinder { frame, radius } = &face.surface else {
        return Err("one diameter reference face is not cylindrical");
    };
    let (u0, u1, v0, v1) = part
        .uv_bounds(f)
        .ok_or("one cylindrical reference could not be measured (no parameter range)")?;
    let raw = frame.z;
    let n = (raw[0] * raw[0] + raw[1] * raw[1] + raw[2] * raw[2]).sqrt();
    if n <= 1e-12 || v1 <= v0 || v1.is_nan() || v0.is_nan() {
        return Err("one cylindrical reference could not be measured (degenerate axis or span)");
    }
    let unit = raw.map(|c| c / n);
    let k = (0..3).fold(0, |k, i| if unit[i].abs() > unit[k].abs() { i } else { k });
    let sign = if unit[k] < 0.0 { -1.0 } else { 1.0 };
    let direction = unit.map(|c| {
        let c = sign * c;
        if c.abs() < 1e-12 { 0.0 } else { c }
    });
    let p = frame.origin;
    let along = p[0] * direction[0] + p[1] * direction[1] + p[2] * direction[2];
    let origin = [0, 1, 2].map(|i| {
        let c = p[i] - along * direction[i];
        if c.abs() < 1e-12 { 0.0 } else { c }
    });
    let stations = [along + sign * v0, along + sign * v1];
    // The material side: the face's normal at its parameter centre against the radial direction
    // (draftwright reads OpenCascade's face orientation, the same fact for a cylinder whose
    // surface normal points away from its axis).
    let (u, v) = (0.5 * (u0 + u1), 0.5 * (v0 + v1));
    let q = face.surface.value(u, v);
    let foot = [0, 1, 2].map(|i| p[i] + v * unit[i]);
    let radial = [0, 1, 2].map(|i| q[i] - foot[i]);
    let normal = part
        .face_normal(f, u, v)
        .ok_or("one cylindrical reference could not be measured (no normal)")?;
    let out = normal[0] * radial[0] + normal[1] * radial[1] + normal[2] * radial[2];
    Ok(Cylinder {
        origin,
        direction,
        radius: *radius,
        interval: [stations[0].min(stations[1]), stations[0].max(stations[1])],
        internal: out < 0.0,
    })
}

/// Where a dimension attaches, from its faces (draftwright's `_proved_planar_linear` with
/// `_linear_reference_stations` for a linear or thickness dimension, `_cylindrical_references`
/// with `_diameter_reference_blockers` for a diameter): what [`crate::dims`] draws it from, and
/// the reasons it cannot be drawn when its faces prove no witness. A radius is read as a
/// diameter's cylinders are, with a proven site on its arc; an angle by its two planar sides
/// ([`crate::dims::angular::geometry`]); the curved kinds keep no geometry (not drawn: see
/// `pmi_dimensions`).
pub fn dimension_geometry(part: &Part, r: &Record) -> Geometry {
    let mut g = Geometry {
        targets: r
            .geometry
            .as_ref()
            .map_or_else(Vec::new, |g| g.targets.clone()),
        ..Geometry::default()
    };
    g.reasons.extend(crate::pmi::placements_reason(r));
    let value = r.value;
    match r.kind.as_str() {
        "linear" | "thickness" => linear_geometry(part, r, value, &mut g),
        "diameter" | "radius" => cylinder_geometry(part, r, value, &mut g),
        "angular" => crate::dims::angular::geometry(part, r, &mut g),
        "common_label" => return label_geometry(part, r),
        _ => {}
    }
    if matches!(r.kind.as_str(), "linear" | "thickness")
        && g.reasons.is_empty() && g.targets.is_empty()
        && r.face_groups.len() == 2 && r.face_groups.iter().all(|fs| !fs.is_empty())
        && let Some(k) = g.axis.as_deref().and_then(|a| XYZ.iter().position(|x| *x == a))
        && r.face_groups.iter().flatten().all(|&f| matches!(&part.faces[f].surface, Surface::Plane { frame } if exact_axis(frame.z) == Some(k)))
    {
        let boxes: Vec<Vec<_>> = r.face_groups.iter().map(|fs| fs.iter().map(|&f| face_box(part, f)).collect()).collect();
        for name in ["front", "plan", "side"] {
            let across = match (k, name) {
                (0, "front") | (1, "side") => 2,
                (0, "plan") | (2, "side") => 1,
                _ => 0,
            };
            let preferred = if !g.sites.is_empty() && g.view.as_deref() == Some(name) {
                g.stations[0][across]
            } else if name == "plan" {
                0.5 * (g.stations[0][across] + g.stations[1][across])
            } else {
                let lo = boxes.iter().flatten().map(|b| b[across]).fold(f64::INFINITY, f64::min);
                let hi = boxes.iter().flatten().map(|b| b[across+3]).fold(f64::NEG_INFINITY, f64::max);
                0.5 * (lo+hi)
            };
            if let Planar::Projected(_, _, points) = projected_planar_witness_in_view(part, &r.face_groups, &boxes, k, Some(name), Some(preferred))
                && (0..2).all(|i| (points[i][k]-g.stations[i][k]).abs()<1e-6)
            {
                g.view_witnesses.insert(name.into(), points);
            }
        }
    }
    g.reasons = dedup(std::mem::take(&mut g.reasons));
    g
}

/// A missing nominal is recoverable only after the same attachment proofs that
/// validate stated values. Lengths use the dimension direction, not a 3D box diagonal.
pub(crate) fn measured_value(kind: &str, g: &Geometry) -> Option<f64> {
    if !g.reasons.is_empty() {
        return None;
    }
    let value = match kind {
        "linear" | "thickness" => {
            let [p, q] = g.stations.as_slice() else {
                return None;
            };
            match g
                .axis
                .as_deref()
                .and_then(|axis| XYZ.iter().position(|v| *v == axis))
            {
                Some(k) => (q[k] - p[k]).abs(),
                None => (0..3).map(|k| (q[k] - p[k]).powi(2)).sum::<f64>().sqrt(),
            }
        }
        "diameter" => 2. * g.cylinders.first()?.radius,
        "radius" => g.cylinders.first()?.radius,
        "angular" => g.angle.as_ref()?.degrees()?,
        _ => return None,
    };
    let value = crate::nominal::clean(value);
    (value.is_finite() && value > 1e-9).then_some(value)
}

fn linear_geometry(part: &Part, r: &Record, value: Option<f64>, g: &mut Geometry) {
    if !g.targets.is_empty() {
        derived_linear_geometry(part, r, value, g);
        return;
    }
    let word = |s: &str| {
        if r.kind == "thickness" {
            s.replace("linear dimension", "thickness dimension")
                .replace("linear reference", "thickness reference")
        } else {
            s.to_string()
        }
    };
    let groups = &r.face_groups;
    let edges = |i: usize| r.edge_groups.get(i).map_or(&[][..], Vec::as_slice);
    if groups.len() != 2 || (0..2).any(|i| groups[i].is_empty() && edges(i).is_empty()) {
        g.reasons.push(word(
            "linear dimension needs two measurable authored reference groups",
        ));
        return;
    }
    // Each group's items' boxes, its faces' then its edges' (an edge's from its samples).
    let all_boxes: Vec<Vec<[f64; 6]>> = (0..2)
        .map(|i| {
            let mut bs: Vec<[f64; 6]> = groups[i].iter().map(|&f| face_box(part, f)).collect();
            bs.extend(edges(i).iter().filter_map(|&e| edge_box(part, e)));
            bs
        })
        .collect();
    let faces_only = (0..2).all(|i| edges(i).is_empty() && !groups[i].is_empty());
    let boxes = &all_boxes;
    let centre = |bs: &[[f64; 6]]| {
        let mut m = bs[0];
        for b in &bs[1..] {
            for i in 0..3 {
                m[i] = m[i].min(b[i]);
                m[i + 3] = m[i + 3].max(b[i + 3]);
            }
        }
        [0, 1, 2].map(|i| 0.5 * (m[i] + m[i + 3]))
    };
    if boxes.iter().any(Vec::is_empty) {
        g.reasons.push(word(
            "linear dimension needs two measurable authored reference groups",
        ));
        return;
    }
    let mut stations = [centre(&boxes[0]), centre(&boxes[1])];
    let mut plane_axis = None;
    let proof = if faces_only {
        planar(part, groups, boxes)
    } else {
        Planar::Not
    };
    match proof {
        Planar::Not => {
            if faces_only && let Some(points) = oblique_plane_witness(part, groups) {
                stations = points;
                g.sites = points.to_vec();
            } else if r.kind == "linear"
                && faces_only
                && let Some(bore) = plane_to_bore(part, groups, boxes)
            {
                stations = bore.0;
                plane_axis = Some(bore.1);
                g.view = Some(bore.2.into());
            } else if r.kind == "linear"
                && faces_only
                && let Some(witness) = projected_plane_to_bore(part, groups, boxes)
            {
                stations = witness.stations;
                plane_axis = witness.axis;
                g.view = Some(witness.view.into());
                g.sites = stations.to_vec();
            } else if r.kind == "linear" && faces_only && mixed(part, groups) {
                // A refused attachment still measures along the datum normal.
                // Box centres can differ in depth without changing this location.
                let plane = groups
                    .iter()
                    .flatten()
                    .find_map(|&f| {
                        if let Surface::Plane { frame } = &part.faces[f].surface {
                            Some(frame)
                        } else {
                            None
                        }
                    })
                    .expect("mixed groups contain a plane");
                let span: f64 = (0..3)
                    .map(|i| (stations[1][i] - stations[0][i]) * plane.z[i])
                    .sum();
                let projected = [
                    stations[0],
                    [0, 1, 2].map(|i| stations[0][i] + span * plane.z[i]),
                ];
                let (_, _, reasons, refusal) =
                    stations_proof(projected, value, exact_axis(plane.z));
                g.refusal = refusal;
                g.reasons.extend(reasons.into_iter().map(|s| word(&s)));
                g.reasons.push(
                    "plane-to-bore location has no proven face-supported axis witness".into(),
                );
                g.stations = stations.to_vec();
                return;
            } else if value.is_none() {
                g.reasons
                    .push("linear reference groups have no proven measurable span".into());
                return;
            }
        }
        Planar::Unwitnessed => {
            g.stations = stations.to_vec();
            g.reasons
                .push("parallel planar source faces have no proven shared witness".into());
            return;
        }
        Planar::Axis(k, view, witness) => {
            plane_axis = k;
            g.view = view.map(String::from);
            for (i, c) in witness {
                for s in &mut stations {
                    s[i] = c;
                }
            }
        }
        Planar::Projected(k, view, witness) => {
            plane_axis = Some(k);
            g.view = Some(view.into());
            stations = witness;
            g.sites = witness.to_vec();
        }
    }
    let (points, axis, reasons, refusal) = stations_proof(stations, value, plane_axis);
    g.refusal = refusal;
    g.stations = points.to_vec();
    g.axis = axis.map(|k| XYZ[k].to_string());
    g.reasons.extend(reasons.into_iter().map(|s| word(&s)));
}

/// Closest witnesses on two infinite semantic targets, independent of the authored nominal.
/// Non-parallel planes have no constant separation; intersecting axis/plane targets do not
/// establish a nonzero location. Parallel axes use the support centre to choose their station.
pub(crate) fn derived_stations(
    a: &crate::pmi::DerivedTarget,
    b: &crate::pmi::DerivedTarget,
    centre: [f64; 3],
) -> Result<[[f64; 3]; 2], &'static str> {
    use crate::pmi::DerivedKind::{Axis, MedianPlane};
    let dot = |a: [f64; 3], b: [f64; 3]| (0..3).map(|i| a[i] * b[i]).sum::<f64>();
    let sub = |a: [f64; 3], b: [f64; 3]| [0, 1, 2].map(|i| a[i] - b[i]);
    let along = |p: [f64; 3], d: [f64; 3], t: f64| [0, 1, 2].map(|i| p[i] + d[i] * t);
    let cross = |a: [f64; 3], b: [f64; 3]| {
        [
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        ]
    };
    let parallel = |a: [f64; 3], b: [f64; 3]| {
        let n = cross(a, b);
        dot(n, n) <= 1e-18
    };
    match (a.kind, b.kind) {
        (MedianPlane, MedianPlane) => {
            if !parallel(a.direction, b.direction) {
                return Err("derived planes are not parallel");
            }
            let p = along(centre, a.direction, dot(sub(a.point, centre), a.direction));
            Ok([p, along(p, a.direction, dot(sub(b.point, p), a.direction))])
        }
        (Axis, MedianPlane) | (MedianPlane, Axis) => {
            let (axis, plane) = if a.kind == Axis { (a, b) } else { (b, a) };
            if dot(axis.direction, plane.direction).abs() > 1e-9 {
                return Err(
                    "derived axis intersects its reference plane; no constant nonzero distance",
                );
            }
            let p = along(
                axis.point,
                axis.direction,
                dot(sub(centre, axis.point), axis.direction),
            );
            let q = along(
                p,
                plane.direction,
                dot(sub(plane.point, p), plane.direction),
            );
            Ok(if a.kind == Axis { [p, q] } else { [q, p] })
        }
        (Axis, Axis) => {
            let d = sub(b.point, a.point);
            let n = cross(a.direction, b.direction);
            let p = if parallel(a.direction, b.direction) {
                along(a.point, a.direction, dot(sub(centre, a.point), a.direction))
            } else {
                let t = dot(cross(d, b.direction), n) / dot(n, n);
                along(a.point, a.direction, t)
            };
            Ok([
                p,
                along(b.point, b.direction, dot(sub(p, b.point), b.direction)),
            ])
        }
    }
}

fn derived_linear_geometry(part: &Part, r: &Record, value: Option<f64>, g: &mut Geometry) {
    use crate::pmi::{DerivedKind, DerivedTarget};
    let target = |i: usize| -> Option<DerivedTarget> {
        if let Some(Some(t)) = g.targets.get(i) {
            return Some(t.clone());
        }
        let faces = r.face_groups.get(i)?;
        let &first = faces.first()?;
        let (kind, point, direction, radius) = match &part.faces[first].surface {
            Surface::Plane { frame } => (DerivedKind::MedianPlane, frame.origin, frame.z, None),
            Surface::Cylinder { frame, radius } => {
                (DerivedKind::Axis, frame.origin, frame.z, Some(*radius))
            }
            _ => return None,
        };
        // Direct support may accompany a derived target, but may not introduce another
        // plane or axis by averaging a face group.
        let candidate = DerivedTarget {
            kind,
            point,
            direction,
            radius,
            faces: faces.clone(),
        };
        for &f in &faces[1..] {
            let (p, d) = match (&part.faces[f].surface, kind) {
                (Surface::Plane { frame }, DerivedKind::MedianPlane)
                | (Surface::Cylinder { frame, .. }, DerivedKind::Axis) => (frame.origin, frame.z),
                _ => return None,
            };
            let dot = |x: [f64; 3], y: [f64; 3]| (0..3).map(|i| x[i] * y[i]).sum::<f64>();
            if (dot(d, direction).abs() - 1.0).abs() > 1e-9 {
                return None;
            }
            let delta = [0, 1, 2].map(|i| p[i] - point[i]);
            let residual = if kind == DerivedKind::MedianPlane {
                dot(delta, direction).abs()
            } else {
                let t = dot(delta, direction);
                (0..3)
                    .map(|i| (delta[i] - t * direction[i]).powi(2))
                    .sum::<f64>()
                    .sqrt()
            };
            if residual > 1e-6 {
                return None;
            }
        }
        Some(candidate)
    };
    let (Some(a), Some(b)) = (target(0), target(1)) else {
        g.reasons
            .push("derived location needs two resolved axis or plane targets".into());
        return;
    };
    let centre = r
        .ref_bbox
        .map_or([0.0; 3], |b| [0, 1, 2].map(|i| 0.5 * (b[i] + b[i + 3])));
    g.targets = vec![Some(a.clone()), Some(b.clone())];
    match derived_stations(&a, &b, centre) {
        Ok(s) => {
            let (points, axis, reasons, refusal) = stations_proof(s, value, None);
            g.refusal = refusal;
            g.stations = points.to_vec();
            g.axis = axis.map(|k| XYZ[k].into());
            g.reasons.extend(reasons);
            // An axis location is read in the view that sees the axis end-on.
            if let Some(k) = [a, b]
                .iter()
                .find(|t| t.kind == DerivedKind::Axis)
                .and_then(|t| exact_axis(t.direction))
            {
                g.view = Some(["side", "front", "plan"][k].into());
            }
        }
        Err(why) => g.reasons.push(why.into()),
    }
}

/// What two groups of faces prove as parallel planes (draftwright's
/// `_parallel_planar_reference_witness`).
enum Planar {
    /// Not every face is a principal plane, thin along its normal, one station per group, one
    /// normal for both.
    Not,
    /// Parallel planes with no proven witness in a principal view.
    Unwitnessed,
    /// Parallel planes: their normal, when a view's witness proves it; the one view that does,
    /// when only one does; the witness coordinates the stations take.
    Axis(Option<usize>, Option<&'static str>, Vec<(usize, f64)>),
    /// Points on the trimmed patches sharing a transverse coordinate in this view.
    /// Their hidden coordinates may differ; the measured span is the planes' separation.
    Projected(usize, &'static str, [[f64; 3]; 2]),
}

fn planar(part: &Part, groups: &[Vec<usize>], boxes: &[Vec<[f64; 6]>]) -> Planar {
    let mut axes = Vec::new();
    for (faces, bs) in groups.iter().zip(boxes) {
        let mut axis: Option<(usize, f64)> = None;
        for (&f, b) in faces.iter().zip(bs) {
            let Surface::Plane { frame } = &part.faces[f].surface else {
                return Planar::Not;
            };
            let Some(k) = exact_axis(frame.z) else {
                return Planar::Not;
            };
            if b[k + 3] - b[k] > 1e-5 {
                return Planar::Not;
            }
            let station = 0.5 * (b[k] + b[k + 3]);
            match axis {
                Some((a, s)) if a != k || (s - station).abs() > 1e-5 => return Planar::Not,
                _ => axis = Some((k, station)),
            }
        }
        // Invariant: A group is created only from its first resolved face and axis.
        axes.push(axis.expect("a group has a face").0);
    }
    if axes[0] != axes[1] {
        return Planar::Not;
    }
    let k = axes[0];
    let midpoint = |i: usize| {
        let all: Vec<&[f64; 6]> = boxes.iter().flatten().collect();
        let lo = all.iter().map(|b| b[i]).fold(f64::INFINITY, f64::min);
        let hi = all
            .iter()
            .map(|b| b[i + 3])
            .fold(f64::NEG_INFINITY, f64::max);
        let m = 0.5 * (lo + hi);
        boxes
            .iter()
            .all(|bs| bs.iter().any(|b| b[i] - 1e-6 <= m && m <= b[i + 3] + 1e-6))
    };
    match k {
        0 if midpoint(2) => Planar::Axis(Some(0), None, Vec::new()),
        0 => match common_x_witness(part, groups, boxes) {
            Some((y, z)) => Planar::Axis(Some(0), Some("plan"), vec![(1, y), (2, z)]),
            None => projected_planar_witness(part, groups, boxes, k),
        },
        2 if midpoint(0) => Planar::Axis(Some(2), None, Vec::new()),
        2 => projected_planar_witness(part, groups, boxes, k),
        _ => match (midpoint(2), midpoint(0)) {
            (true, true) => Planar::Axis(Some(1), None, Vec::new()),
            (true, false) => Planar::Axis(Some(1), Some("side"), Vec::new()),
            (false, true) => Planar::Axis(Some(1), Some("plan"), Vec::new()),
            (false, false) => projected_planar_witness(part, groups, boxes, k),
        },
    }
}

/// A composite's bounding-box midpoint can lie between its patches. Search face pairs in
/// each edge-on view instead: both attachment points must lie on a trimmed face and share
/// the displayed transverse coordinate. A common 3D normal ray is unnecessary when the
/// faces overlap in projection (for example, a split datum beside a recessed end face).
/// This search never uses the nominal; `stations_proof` still checks the plane separation.
fn projected_planar_witness(
    part: &Part,
    groups: &[Vec<usize>],
    boxes: &[Vec<[f64; 6]>],
    k: usize,
) -> Planar {
    projected_planar_witness_in_view(part, groups, boxes, k, None, None)
}

fn projected_planar_witness_in_view(
    part: &Part,
    groups: &[Vec<usize>],
    boxes: &[Vec<[f64; 6]>],
    k: usize,
    selected: Option<&str>,
    preferred: Option<f64>,
) -> Planar {
    let views = match k {
        0 => [("front", 2, 1), ("plan", 1, 2)],
        1 => [("side", 2, 0), ("plan", 0, 2)],
        _ => [("front", 0, 1), ("side", 1, 0)],
    };
    let fractions = [0.5, 0.1, 0.2, 0.3, 0.4, 0.6, 0.7, 0.8, 0.9];
    for (view, across, hidden) in views {
        if selected.is_some_and(|name| name != view) {
            continue;
        }
        let mut candidates = Vec::new();
        for (&a, ba) in groups[0].iter().zip(&boxes[0]) {
            for (&b, bb) in groups[1].iter().zip(&boxes[1]) {
                let lo = ba[across].max(bb[across]);
                let hi = ba[across + 3].min(bb[across + 3]);
                if hi >= lo {
                    candidates.push((lo, hi, a, b, ba, bb));
                }
            }
        }
        // Use a patch near the normal dimension strip, rather than the first face's
        // ordinal: vertical front/side dimensions run on the right, horizontal plan
        // dimensions above, and the others below or left (dims::placement::strip).
        // This keeps extensions short and leaves paper for hole marks and frames.
        let high = k == 2 || (k == 0 && view == "plan");
        candidates.sort_by(|a, b| {
            let order = (a.0 + a.1).total_cmp(&(b.0 + b.1));
            (if high { order.reverse() } else { order })
                .then(a.2.cmp(&b.2))
                .then(a.3.cmp(&b.3))
        });
        for (lo, hi, a, b, ba, bb) in candidates {
            let transverse_candidates = preferred
                .filter(|v| *v >= lo && *v <= hi)
                .into_iter()
                .chain(fractions.map(|fraction| lo + fraction * (hi - lo)));
            for transverse in transverse_candidates {
                let point = |face, bounds: &[f64; 6]| {
                    fractions.iter().find_map(|t| {
                        let mut p = [0.0; 3];
                        p[k] = 0.5 * (bounds[k] + bounds[k + 3]);
                        p[across] = transverse;
                        p[hidden] = bounds[hidden] + t * (bounds[hidden + 3] - bounds[hidden]);
                        on_face(part, face, p).then_some(p)
                    })
                };
                if let (Some(p), Some(q)) = (point(a, ba), point(b, bb)) {
                    return Planar::Projected(k, view, [p, q]);
                }
            }
        }
    }
    Planar::Unwitnessed
}

/// A point inside both trimmed x faces (draftwright's `_common_x_face_witness`): the largest
/// overlap of their boxes first, its centre then a 9 × 9 grid over it.
fn common_x_witness(
    part: &Part,
    groups: &[Vec<usize>],
    boxes: &[Vec<[f64; 6]>],
) -> Option<(f64, f64)> {
    let mut candidates = Vec::new();
    for (i, a) in boxes[0].iter().enumerate() {
        for (j, b) in boxes[1].iter().enumerate() {
            let (y0, z0, y1, z1) = (
                a[1].max(b[1]),
                a[2].max(b[2]),
                a[4].min(b[4]),
                a[5].min(b[5]),
            );
            if y1 >= y0 && z1 >= z0 {
                candidates.push(((y1 - y0) * (z1 - z0), y0, z0, y1, z1, i, j));
            }
        }
    }
    candidates.sort_by(|p, q| {
        q.0.total_cmp(&p.0)
            .then(p.1.total_cmp(&q.1))
            .then(p.2.total_cmp(&q.2))
    });
    let fractions = std::iter::once((0.5, 0.5)).chain(
        (1..10).flat_map(|i| (1..10).map(move |j| (f64::from(i) / 10.0, f64::from(j) / 10.0))),
    );
    let fractions: Vec<(f64, f64)> = fractions.collect();
    for (_, y0, z0, y1, z1, i, j) in candidates {
        for &(fy, fz) in &fractions {
            let (y, z) = (y0 + (y1 - y0) * fy, z0 + (z1 - z0) * fz);
            let inside = [(groups[0][i], boxes[0][i]), (groups[1][j], boxes[1][j])]
                .iter()
                .all(|(f, b)| on_face(part, *f, [0.5 * (b[0] + b[3]), y, z]));
            if inside {
                return Some((y, z));
            }
        }
    }
    None
}

/// Whether the two groups are one plane and one cylinder (draftwright's `_is_mixed_planar_bore`).
fn mixed(part: &Part, groups: &[Vec<usize>]) -> bool {
    let kind = |group: &[usize]| {
        if !group.is_empty()
            && group
                .iter()
                .all(|&f| matches!(part.faces[f].surface, Surface::Plane { .. }))
        {
            1
        } else if !group.is_empty()
            && group
                .iter()
                .all(|&f| matches!(part.faces[f].surface, Surface::Cylinder { .. }))
        {
            2
        } else {
            0
        }
    };
    let kinds: Vec<_> = groups.iter().map(|g| kind(g)).collect();
    kinds == [1, 2] || kinds == [2, 1]
}

/// Parallel tilted planes measure along their common normal, never between box centres.
/// Require an actual normal ray joining two trimmed patches, independent of the nominal.
fn oblique_plane_witness(part: &Part, groups: &[Vec<usize>]) -> Option<[[f64; 3]; 2]> {
    let Surface::Plane { frame } = &part.faces[*groups.first()?.first()?].surface else {
        return None;
    };
    let n = frame.z;
    if exact_axis(n).is_some() || projection_view(n).is_none() {
        return None;
    }
    let mut levels = Vec::new();
    for group in groups {
        let mut level = None;
        for &f in group {
            let Surface::Plane { frame } = &part.faces[f].surface else {
                return None;
            };
            let dot: f64 = (0..3).map(|i| n[i] * frame.z[i]).sum();
            if (dot.abs() - 1.).abs() > 1e-9 {
                return None;
            }
            let at: f64 = (0..3).map(|i| n[i] * frame.origin[i]).sum();
            if level.is_some_and(|v: f64| (v - at).abs() > 1e-6) {
                return None;
            }
            level = Some(at);
        }
        levels.push(level?);
    }
    for &f in &groups[0] {
        let (u0, u1, v0, v1) = part.uv_bounds(f)?;
        for t in [0.5, 0.1, 0.3, 0.7, 0.9] {
            for v in [0.5, 0.1, 0.3, 0.7, 0.9] {
                let p = part.faces[f]
                    .surface
                    .value(u0 + t * (u1 - u0), v0 + v * (v1 - v0));
                let q = [0, 1, 2].map(|i| p[i] + n[i] * (levels[1] - levels[0]));
                if on_face(part, f, p) && groups[1].iter().any(|&g| on_face(part, g, q)) {
                    return Some([p, q]);
                }
            }
        }
    }
    None
}

/// A bore's axis is a line, not its bounding-box centre. First try a real normal
/// ray onto the datum patch. If the patches miss in depth, prove independent face
/// witnesses sharing their transverse coordinate in a principal projection.
struct LinearWitness {
    stations: [[f64; 3]; 2],
    axis: Option<usize>,
    view: &'static str,
}

/// A newly proven empty-centre pattern owns its complete cylindrical support.
/// Native recognition may include blind-hole caps that the PMI does not name.
/// Call only after the compound endpoint is proven; this does not prove symmetry.
/// Requiring an empty measured station keeps existing physical-axis ownership strict.
pub(crate) fn empty_centre_pattern_support(
    a: &Analysis,
    group: &[usize],
    faces: &[usize],
    k: usize,
    at: f64,
) -> bool {
    let Some(axis) = group
        .first()
        .and_then(|&f| a.face_attachments.get(f)?.exact_axis)
    else {
        return false;
    };
    if axis == k
        || !group.iter().all(|&f| {
            a.face_attachments.get(f).is_some_and(|f| {
                f.surface.as_deref() == Some("cylinder")
                    && f.exact_axis == Some(axis)
                    && f.support
                        .get(k - usize::from(k > axis))
                        .is_some_and(|v| (v - at).abs() > 1e-6)
            })
        })
    {
        return false;
    }
    let bores: Vec<_> = faces
        .iter()
        .filter(|&&f| {
            a.face_attachments
                .get(f)
                .is_some_and(|f| f.surface.as_deref() == Some("cylinder"))
        })
        .collect();
    bores.len() == group.len() && bores.iter().all(|f| group.contains(f))
}

/// A compound hole group can carry its central bore as well as surrounding bores.
/// Prove that centre by half-turn symmetry of the finite cylindrical supports,
/// and require an actual member axis there. Never choose an axis by the nominal,
/// average unrelated faces, or invent an axis in a pattern's empty centre.
fn central_bore_witnesses(cylinders: &[Cylinder]) -> Option<Vec<&Cylinder>> {
    let first = cylinders.first()?;
    let near =
        |a: [f64; 3], b: [f64; 3], tolerance: f64| (0..3).all(|i| (a[i] - b[i]).abs() <= tolerance);
    if cylinders
        .iter()
        .any(|c| !near(c.direction, first.direction, 1e-9))
    {
        return None;
    }
    if cylinders.iter().all(|c| near(c.origin, first.origin, 1e-6)) {
        return Some(cylinders.iter().collect());
    }
    if cylinders.iter().any(|c| !c.internal) {
        return None;
    }
    let centre = [0, 1, 2].map(|i| {
        let lo = cylinders
            .iter()
            .map(|c| c.origin[i])
            .fold(f64::INFINITY, f64::min);
        let hi = cylinders
            .iter()
            .map(|c| c.origin[i])
            .fold(f64::NEG_INFINITY, f64::max);
        0.5 * (lo + hi)
    });
    let central: Vec<_> = cylinders
        .iter()
        .filter(|c| near(c.origin, centre, 1e-6))
        .collect();
    if central.is_empty()
        || cylinders.iter().any(|c| {
            let opposite = [0, 1, 2].map(|i| 2. * centre[i] - c.origin[i]);
            !cylinders.iter().any(|other| {
                near(other.origin, opposite, 1e-6)
                    && (other.radius - c.radius).abs() <= 1e-6
                    && (0..2).all(|i| (other.interval[i] - c.interval[i]).abs() <= 1e-6)
            })
        })
    {
        return None;
    }
    Some(central)
}

fn projected_plane_to_bore(
    part: &Part,
    groups: &[Vec<usize>],
    boxes: &[Vec<[f64; 6]>],
) -> Option<LinearWitness> {
    if !mixed(part, groups) {
        return None;
    }
    let pi = usize::from(!matches!(
        part.faces[groups[0][0]].surface,
        Surface::Plane { .. }
    ));
    let bi = 1 - pi;
    let Surface::Plane { frame: plane } = &part.faces[groups[pi][0]].surface else {
        return None;
    };
    let n = plane.z;
    let level: f64 = (0..3).map(|i| n[i] * plane.origin[i]).sum();
    for &f in &groups[pi] {
        let Surface::Plane { frame } = &part.faces[f].surface else {
            return None;
        };
        if ((0..3).map(|i| frame.z[i] * n[i]).sum::<f64>().abs() - 1.).abs() > 1e-9
            || ((0..3).map(|i| frame.origin[i] * n[i]).sum::<f64>() - level).abs() > 1e-6
        {
            return None;
        }
    }
    let cylinders = groups[bi]
        .iter()
        .map(|&f| cylinder(part, f))
        .collect::<Result<Vec<_>, _>>()
        .ok()?;
    let (witnesses, centre_station) = central_bore_witnesses(&cylinders)
        .map(|witnesses| (witnesses, None))
        .or_else(|| {
            // A datum location measures only its normal coordinate. A compound group
            // can be centred along that coordinate without being symmetric across the
            // plane (a hole pattern and a separate bore on its centre row, for example).
            // Prove reflection of the finite supports along that coordinate. When
            // no member lies at the midpoint, measure only that proven coordinate;
            // no bore axis is asserted in the other directions. Neither the nominal
            // nor a box diagonal is used to select a witness.
            let k = exact_axis(n)?;
            let first = cylinders.first()?;
            if cylinders.iter().any(|c| {
                !c.internal
                    || c.direction
                        .iter()
                        .zip(first.direction)
                        .any(|(a, b)| (a - b).abs() > 1e-9)
            }) {
                return None;
            }
            let lo = cylinders
                .iter()
                .map(|c| c.origin[k])
                .fold(f64::INFINITY, f64::min);
            let hi = cylinders
                .iter()
                .map(|c| c.origin[k])
                .fold(f64::NEG_INFINITY, f64::max);
            let middle = 0.5 * (lo + hi);
            let central: Vec<_> = cylinders
                .iter()
                .filter(|c| (c.origin[k] - middle).abs() < 1e-6)
                .collect();
            // Reflection preserves multiplicity, not just the set of stations. A
            // missing corner must not borrow another bore's reflected partner.
            let mut matched = vec![false; cylinders.len()];
            for c in &cylinders {
                let (i, _) = cylinders.iter().enumerate().find(|(i, other)| {
                    !matched[*i]
                        && (other.origin[k] - (2. * middle - c.origin[k])).abs() < 1e-6
                        && (other.radius - c.radius).abs() < 1e-6
                        && (0..2).all(|i| (other.interval[i] - c.interval[i]).abs() < 1e-6)
                })?;
                matched[i] = true;
            }
            if central.is_empty() {
                Some((cylinders.iter().collect(), Some((k, middle))))
            } else {
                Some((central, None))
            }
        })?;
    let first = witnesses.first()?;
    if (0..3).map(|i| n[i] * first.direction[i]).sum::<f64>().abs() > 1e-9 {
        return None;
    }
    let pair = |p, q| if pi == 0 { [p, q] } else { [q, p] };
    let k = exact_axis(n);
    let view = match k {
        Some(k) => first
            .principal()
            .filter(|&axis| axis != k)
            .map(|axis| ["side", "front", "plan"][axis])
            .unwrap_or(if k == 1 { "side" } else { "front" }),
        None => projection_view(n)?,
    };
    for c in witnesses {
        for t in [0.5, 0.1, 0.3, 0.7, 0.9] {
            let along = c.interval[0] + t * (c.interval[1] - c.interval[0]);
            let mut q = [0, 1, 2].map(|i| c.origin[i] + along * c.direction[i]);
            if let Some((k, middle)) = centre_station {
                // Transverse coordinates only place the witness on the drawing.
                // Use the group's support midpoint, as the pattern planner does;
                // only k is proven by reflection and measured by plane_axis.
                q = [0, 1, 2].map(|i| {
                    let lo = boxes[bi].iter().map(|b| b[i]).fold(f64::INFINITY, f64::min);
                    let hi = boxes[bi]
                        .iter()
                        .map(|b| b[i + 3])
                        .fold(f64::NEG_INFINITY, f64::max);
                    0.5 * (lo + hi)
                });
                q[k] = middle;
            }
            let offset = level - (0..3).map(|i| q[i] * n[i]).sum::<f64>();
            let p = [0, 1, 2].map(|i| q[i] + offset * n[i]);
            if groups[pi].iter().any(|&f| on_face(part, f, p)) {
                return Some(LinearWitness {
                    stations: pair(p, q),
                    axis: k,
                    view,
                });
            }
            if let Some(k) = k {
                for (view, hidden) in [("front", 1), ("side", 0), ("plan", 2)] {
                    if hidden == k {
                        continue;
                    }
                    for (&f, bounds) in groups[pi].iter().zip(&boxes[pi]) {
                        for t in [0.5, 0.1, 0.3, 0.7, 0.9] {
                            let mut p = p;
                            p[hidden] = bounds[hidden] + t * (bounds[hidden + 3] - bounds[hidden]);
                            if on_face(part, f, p) {
                                return Some(LinearWitness {
                                    stations: pair(p, q),
                                    axis: Some(k),
                                    view,
                                });
                            }
                        }
                    }
                }
            }
        }
    }
    None
}

/// A location from a principal plane to a principal bore's axis across it (draftwright's
/// `_planar_bore_axis_witness`): the stations, at the plane and at the axis, on one point of the
/// plane's face beside the axis halfway along their common extent down the bore; the plane's
/// normal; and the view that sees the bore end on. draftwright proves only an x or y plane to a
/// z bore, seen in plan; any other pair is proved the same way here (#34: a turned part's cross
/// hole located from its end face, seen in the side or front view), and the z bore keeps
/// draftwright's witness and view.
fn plane_to_bore(
    part: &Part,
    groups: &[Vec<usize>],
    boxes: &[Vec<[f64; 6]>],
) -> Option<([[f64; 3]; 2], usize, &'static str)> {
    if !mixed(part, groups) || groups.iter().any(|g| g.len() != 1) {
        return None;
    }
    let plane_i = usize::from(!matches!(
        part.faces[groups[0][0]].surface,
        Surface::Plane { .. }
    ));
    let bore_i = 1 - plane_i;
    let Surface::Plane { frame: plane } = &part.faces[groups[plane_i][0]].surface else {
        return None;
    };
    let Surface::Cylinder { frame: bore, .. } = &part.faces[groups[bore_i][0]].surface else {
        return None;
    };
    let axis = exact_axis(plane.z)?;
    // The bore's axis, across the plane's normal.
    let along = exact_axis(bore.z).filter(|&j| j != axis)?;
    let (pb, bb) = (boxes[plane_i][0], boxes[bore_i][0]);
    let (z0, z1) = (pb[along].max(bb[along]), pb[along + 3].min(bb[along + 3]));
    if z1 - z0 <= 1e-6 || pb[axis + 3] - pb[axis] > 1e-5 {
        return None;
    }
    let centre = bore.origin;
    let mut witness = centre;
    witness[axis] = 0.5 * (pb[axis] + pb[axis + 3]);
    witness[along] = 0.5 * (z0 + z1);
    if !on_face(part, groups[plane_i][0], witness) {
        return None;
    }
    let mut points = [witness, witness];
    points[bore_i][axis] = centre[axis];
    Some((points, axis, ["side", "front", "plan"][along]))
}

/// draftwright's `_linear_reference_stations`: the axis two stations are measured along (their
/// largest difference, or the planes' normal), refused when they coincide, when they are
/// oblique outside a principal view's plane (an oblique one inside it keeps no axis), or when
/// the span they prove is not the nominal value.
fn stations_proof(
    s: [[f64; 3]; 2],
    nominal: Option<f64>,
    plane_axis: Option<usize>,
) -> (
    [[f64; 3]; 2],
    Option<usize>,
    Vec<String>,
    Option<crate::pmi::GeometryRefusal>,
) {
    let delta = [0, 1, 2].map(|i| s[1][i] - s[0][i]);
    let m = delta.map(f64::abs);
    let k = plane_axis.unwrap_or_else(|| (0..3).fold(0, |k, i| if m[i] > m[k] { i } else { k }));
    let primary = m[k];
    if primary <= 1e-9 {
        return (
            s,
            None,
            vec!["linear reference groups occupy the same station".into()],
            None,
        );
    }
    let transverse = (0..3).filter(|&i| i != k).map(|i| m[i]).fold(0.0, f64::max);
    let oblique = plane_axis.is_none() && transverse > 0.005f64.max(primary * 1e-3);
    if oblique && projection_view(delta).is_none() {
        return (
            s,
            None,
            vec![format!(
                "linear reference relationship does not lie in a principal projection plane (delta=({}, {}, {}) mm)",
                g6(delta[0]),
                g6(delta[1]),
                g6(delta[2])
            )],
            None,
        );
    }
    let span = if oblique {
        (delta[0].powi(2) + delta[1].powi(2) + delta[2].powi(2)).sqrt()
    } else {
        primary
    };
    let axis = (!oblique).then_some(k);
    if let Some(nominal) = nominal
        && (span - nominal).abs() > (if oblique { 0.05 } else { 0.01f64 }).max(nominal.abs() * 5e-4)
    {
        return (
            s,
            axis,
            vec![format!(
                "linear reference-station span {} mm differs from nominal {} mm",
                g6(span),
                g6(nominal)
            )],
            Some(crate::pmi::GeometryRefusal::NominalMismatch {
                measured: span,
                nominal,
            }),
        );
    }
    (s, axis, Vec::new(), None)
}

/// The principal view whose plane holds a span (draftwright's `_linear_projection_view`): the
/// one axis its difference has none of.
pub(crate) fn projection_view(delta: [f64; 3]) -> Option<&'static str> {
    let primary = delta.iter().map(|d| d.abs()).fold(0.0, f64::max);
    if (delta[0].powi(2) + delta[1].powi(2) + delta[2].powi(2)).sqrt() <= 1e-9 {
        return None;
    }
    let tolerance = 0.005f64.max(primary * 1e-3);
    let zeros: Vec<usize> = (0..3).filter(|&i| delta[i].abs() <= tolerance).collect();
    match zeros[..] {
        [i] => Some(["side", "front", "plan"][i]),
        _ => None,
    }
}

fn cylinder_geometry(part: &Part, r: &Record, value: Option<f64>, g: &mut Geometry) {
    let radius = r.kind == "radius";
    if !g.targets.is_empty()
        && !matches!(g.targets.as_slice(), [Some(t)] if t.kind == crate::pmi::DerivedKind::Axis && t.radius.is_some())
    {
        g.reasons
            .push("derived size target has no resolved cylinder radius".into());
        return;
    }
    let noun = if radius { "radius" } else { "diameter" };
    for &f in &r.faces {
        match cylinder(part, f) {
            Ok(c) => {
                if !g.cylinders.iter().any(|d| same_cylinder(d, &c)) {
                    g.cylinders.push(c);
                }
            }
            Err(why) => g.reasons.push(why.replace("diameter", noun)),
        }
    }
    // A rim edge of a referenced cylinder names that cylinder again (NIST STC-10's hole sizes
    // reference each hole's face and its rim); any other edge is no size reference.
    if !r
        .edges
        .iter()
        .all(|&e| g.cylinders.iter().any(|c| edge_on_cylinder(part, e, c)))
    {
        g.reasons
            .push(format!("one {noun} reference is not a face"));
    }
    if let [Some(target)] = g.targets.as_slice() {
        let mut d = target.direction;
        let k = (0..3)
            .max_by(|&i, &j| d[i].abs().total_cmp(&d[j].abs()))
            // Invariant: The iterator covers the fixed three coordinate axes.
            .unwrap();
        if d[k] < 0.0 {
            d = d.map(|v| -v);
        }
        let along: f64 = (0..3).map(|i| target.point[i] * d[i]).sum();
        let origin = [0, 1, 2].map(|i| target.point[i] - along * d[i]);
        for c in &mut g.cylinders {
            c.origin = origin;
            c.direction = d;
            // Invariant: The preceding match rejected a missing target radius.
            c.radius = target.radius.expect("resolved radius checked above");
        }
    }
    if g.cylinders.is_empty() {
        if g.reasons.is_empty() {
            g.reasons.push(format!(
                "{noun} dimension needs a measurable cylindrical-face reference"
            ));
        }
        return;
    }
    let first = g.cylinders[0].direction;
    let directions_agree = g
        .cylinders
        .iter()
        .all(|c| (0..3).all(|i| (c.direction[i] - first[i]).abs() <= 1e-9));
    if !directions_agree {
        g.reasons.push(format!(
            "{noun} references do not share one cylinder axis direction"
        ));
    } else if first.iter().all(|c| c.abs() > 1e-6) {
        g.reasons.push(format!(
            "{noun} cylinder axis does not lie in a principal projection plane"
        ));
    }
    if g.cylinders
        .iter()
        .any(|c| c.internal != g.cylinders[0].internal)
    {
        g.reasons.push(format!(
            "{noun} references mix internal and external cylindrical faces"
        ));
    }
    let size = |c: &Cylinder| if radius { c.radius } else { 2.0 * c.radius };
    let reference = value.unwrap_or_else(|| size(&g.cylinders[0]));
    let tolerance = value.map_or(1e-6, |v| 0.01f64.max(v.abs() * 5e-4));
    let off: Vec<String> = g
        .cylinders
        .iter()
        .filter(|c| (size(c) - reference).abs() > tolerance)
        .map(|c| g6(size(c)))
        .collect();
    if !off.is_empty() {
        g.reasons.push(format!(
            "cylindrical reference {noun}(s) {} mm differ from {} {} mm",
            off.join(", "),
            if value.is_some() {
                "nominal"
            } else {
                "the other reference size"
            },
            g6(reference)
        ));
    }
    let axes: BTreeSet<Option<usize>> = g.cylinders.iter().map(Cylinder::principal).collect();
    if axes.len() == 1
        && let Some(Some(k)) = axes.first()
    {
        g.axis = Some(XYZ[*k].to_string());
    }
    if radius {
        // A radius is drawn to its arc: a proven point of its first face, at its parameter
        // centre.
        let f = r.faces[0];
        if let Some((u0, u1, v0, v1)) = part.uv_bounds(f) {
            let p = part.faces[f]
                .surface
                .value(0.5 * (u0 + u1), 0.5 * (v0 + v1));
            if on_face(part, f, p) {
                g.sites.push(p);
            }
        }
        if g.sites.is_empty() {
            g.reasons
                .push("the radius's face has no proven point at its parameter centre".into());
        }
    }
}

/// Whether every point that stands in for edge *e* lies on cylinder *c*, within its extent (to
/// 1e-4 mm).
fn edge_on_cylinder(part: &Part, e: usize, c: &Cylinder) -> bool {
    part.edges.get(e).is_some_and(|edge| {
        edge.samples
            .iter()
            .chain([&edge.start, &edge.end])
            .all(|p| {
                let d: [f64; 3] = [0, 1, 2].map(|i| p[i] - c.origin[i]);
                let t: f64 = (0..3).map(|i| d[i] * c.direction[i]).sum();
                let off = (0..3)
                    .map(|i| (d[i] - t * c.direction[i]).powi(2))
                    .sum::<f64>()
                    .sqrt();
                (off - c.radius).abs() <= 1e-4
                    && c.interval[0] - 1e-4 <= t
                    && t <= c.interval[1] + 1e-4
            })
    })
}

/// Geometrically identical cylinders (one face XCAF repeats in a group): draftwright coalesces
/// them.
fn same_cylinder(a: &Cylinder, b: &Cylinder) -> bool {
    let near = |x: &[f64], y: &[f64]| x.iter().zip(y).all(|(p, q)| (p - q).abs() <= 1e-9);
    near(&a.origin, &b.origin)
        && near(&a.direction, &b.direction)
        && (a.radius - b.radius).abs() <= 1e-9
        && near(&a.interval, &b.interval)
        && a.internal == b.internal
}

/// `%.6g`, as draftwright writes a measurement in a reason (`f"{value:.6g}"`).
fn g6(v: f64) -> String {
    if v == 0.0 {
        return "0".into();
    }
    let sci = format!("{v:.5e}");
    // Invariant: The finite nonzero value is formatted locally with Rust scientific notation and an integer exponent.
    let (mantissa, exp) = sci.split_once('e').expect("exponent");
    // Invariant: The finite nonzero value is formatted locally with Rust scientific notation and an integer exponent.
    let exp: i32 = exp.parse().expect("exponent");
    if !(-4..6).contains(&exp) {
        let m = mantissa.trim_end_matches('0').trim_end_matches('.');
        let sign = if exp < 0 { '-' } else { '+' };
        return format!("{m}e{sign}{:02}", exp.abs());
    }
    let places = usize::try_from(5 - exp).unwrap_or(0);
    let s = format!("{v:.places$}");
    if s.contains('.') {
        s.trim_end_matches('0').trim_end_matches('.').into()
    } else {
        s
    }
}

// ---- Thread and knurl requirements on their owners' callouts (stage 2c) ----

/// The manufacturing requirement kinds draftwright gives to the feature their cylinder proves
/// (`lower_ap242_manufacturing_requirements`).
const OWNED_KINDS: [&str; 3] = ["internal_thread", "external_thread", "knurl"];

/// Whether a manufacturing requirement's kind is a thread or knurl ([`manufacturing`]'s).
pub fn owned_kind(kind: &str) -> bool {
    OWNED_KINDS.contains(&kind)
}

/// A suffix longer than this, with two or more requirements imported, moves them all into the
/// manufacturing requirements table (draftwright's `manufacturing_schedule` threshold).
const SCHEDULE_SUFFIX: usize = 52;

/// The width a table row's requirement text wraps at (`_wrap_requirement`).
const SCHEDULE_WRAP: usize = 44;

/// The manufacturing requirements table's ID on the plan (draftwright's annotation name).
pub const SCHEDULE_ID: &str = "manufacturing_requirements";

/// A thread or knurl requirement given to the one feature its cylinder proves.
#[derive(Clone, Debug, PartialEq)]
pub struct Owned {
    /// The record's item ID, `pmi:<identity>`: what its owner's callout claims.
    pub id: String,
    /// The record's `source_id`.
    pub source: String,
    /// The feature that carries it (`f:hole:2`, `f:turned_step:4`).
    pub owner: String,
    /// `thread` or `knurl`.
    pub aspect: &'static str,
    /// The terms after the owner's size (`callout_suffix`): `M2 x 0.4-6H RH; 2.5 MIN FULL
    /// THREAD`.
    pub suffix: String,
    /// The whole requirement, as a table row states it (`callout_text`): a knurl's maximum
    /// diameter leads (`ø6 MAX AFTER KNURL; …`).
    pub text: String,
    /// A through tap: its suffix states `THRU`, so the hole callout's own `THRU` is not
    /// repeated.
    pub through: bool,
    /// How the owner's profile views draw it ([`crate::cosmetic`]): an external thread's
    /// minor diameter or a knurl's band; none for an internal thread.
    pub depiction: Option<Depiction>,
    requirement: Aspect,
    presentation_reason: &'static str,
}

/// Where and what a turned step's thread or knurl is drawn, in the drawing's frame (as
/// [`crate::lint::turned::band`] reads the step).
#[derive(Clone, Debug, PartialEq)]
pub struct Depiction {
    /// The world axis the step turns about.
    pub axis: usize,
    /// A point on that axis.
    pub on: [f64; 3],
    /// The threaded or knurled length along the axis.
    pub span: [f64; 2],
    /// The major (thread) or maximum (knurl) diameter.
    pub diameter: f64,
    pub kind: DepictionKind,
}

#[derive(Clone, Debug, PartialEq)]
pub enum DepictionKind {
    /// ISO 6410: a thin line at the minor diameter over the thread length.
    Thread { minor: f64 },
    /// A band of thin lines over part of the knurled length: straight (along the axis) or
    /// diamond (crossed); its pitch along the circumference.
    Knurl { diamond: bool, pitch: f64 },
}

/// What the part's thread and knurl requirements come to ([`manufacturing`]).
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Manufacturing {
    /// Each requirement given to its owner, in the reading's order.
    pub owned: Vec<Owned>,
    /// Each requirement not given to one, and why (`pmi_not_lowered`).
    pub refused: Vec<Problem>,
    /// Each owned requirement's table reference (`MFG 1`, by source), when the table is used.
    pub tags: Vec<(String, String)>,
}

impl Owned {
    pub(crate) fn presentation_reason(&self) -> &'static str {
        self.presentation_reason
    }
    pub(crate) fn knurl_values(&self) -> Option<(&str, f64, Option<f64>)> {
        match &self.requirement {
            Aspect::Knurl(k) => Some((&k.pattern, k.pitch, k.maximum_diameter)),
            _ => None,
        }
    }
    pub(crate) fn thread(&self) -> Option<&Thread> {
        match &self.requirement {
            Aspect::Thread(t) => Some(t),
            _ => None,
        }
    }
}

impl Manufacturing {
    /// What *o*'s owner's callout prints for it: its suffix, or `SEE MFG n` when the table
    /// states it (`manufacturing_callout_suffix`).
    pub fn printed(&self, o: &Owned) -> String {
        self.tags.iter().find(|(s, _)| *s == o.source).map_or_else(
            || o.suffix.clone(),
            |(_, tag)| {
                if o.suffix.contains(&format!("SEE {tag}")) {
                    o.suffix.clone()
                } else {
                    format!("SEE {tag}")
                }
            },
        )
    }

    /// The requirements feature *id* carries, its thread before its knurl (`_manufacturing_suffix`).
    pub fn on(&self, id: &str) -> Vec<&Owned> {
        let mut out: Vec<&Owned> = self.owned.iter().filter(|o| o.owner == id).collect();
        out.sort_by_key(|o| o.aspect != "thread");
        out
    }

    /// Whether feature *id*'s callout prints an external thread's designation itself (not
    /// `SEE MFG n`): the designation then stands in place of the plain diameter, as ISO 6410
    /// designates a thread (`M10 x 1.5-6g RH`, not `ø10 M10 x 1.5-6g RH`). Beyond
    /// draftwright, which prints the diameter before it (draftwright-rust #23).
    pub fn replaces_diameter(&self, id: &str) -> bool {
        self.on(id).iter().any(|o| match &o.requirement {
            Aspect::Thread(t) => !t.internal && self.printed(o) == o.suffix,
            Aspect::Knurl(_) => false,
        })
    }

    /// Who states the requirements feature *id* carries, for its callout's basis: `the spec's`
    /// when the Spec authors one ([`crate::spec_notes`]), else `the part's AP242`.
    pub fn author(&self, id: &str) -> &'static str {
        if self
            .on(id)
            .iter()
            .any(|o| o.source.starts_with(crate::spec_gdt::SOURCE_PREFIX))
        {
            "the spec's"
        } else {
            "the part's AP242"
        }
    }

    /// The terms feature *id*'s callout adds after its size, the requirements' printed text
    /// joined by `; ` and split at its spaces, and whether a through tap's suffix states the
    /// callout's `THRU` (draftwright then drops the bore's own).
    pub fn terms(&self, id: &str) -> (Vec<String>, bool) {
        let on = self.on(id);
        let printed: Vec<String> = on.iter().map(|o| self.printed(o)).collect();
        let through = on
            .iter()
            .zip(&printed)
            .any(|(o, p)| o.through && *p == o.suffix);
        let text = printed.join("; ");
        (
            text.split(' ')
                .filter(|s| !s.is_empty())
                .map(str::to_string)
                .collect(),
            through,
        )
    }

    /// The manufacturing requirements table's rows, the header first (`ManufacturingSchedule.rows`:
    /// `REF | MANUFACTURING REQUIREMENT`, each requirement's text wrapped at its words, its tag on
    /// its first line), or none when the callouts print the requirements themselves.
    pub fn rows(&self) -> Vec<Vec<String>> {
        if self.tags.is_empty() {
            return Vec::new();
        }
        let mut rows = vec![vec![
            "REF".to_string(),
            "MANUFACTURING REQUIREMENT".to_string(),
        ]];
        for (source, tag) in &self.tags {
            let Some(o) = self.owned.iter().find(|o| o.source == *source) else {
                continue;
            };
            for (i, line) in wrap(&o.text, SCHEDULE_WRAP).into_iter().enumerate() {
                rows.push(vec![if i == 0 { tag.clone() } else { String::new() }, line]);
            }
        }
        rows
    }

    /// The item IDs of the requirements the table states, in its order.
    pub fn scheduled(&self) -> Vec<String> {
        self.tags
            .iter()
            .filter_map(|(s, _)| self.owned.iter().find(|o| o.source == *s))
            .map(|o| o.id.clone())
            .collect()
    }
}

/// *text* broken at its words into lines of at most *width* characters, a longer word alone on
/// its line (`_wrap_requirement`).
fn wrap(text: &str, width: usize) -> Vec<String> {
    let mut lines = Vec::new();
    let mut current = String::new();
    for word in text.split_whitespace() {
        let n = current.chars().count();
        if n > 0 && n + word.chars().count() + 1 > width {
            lines.push(std::mem::take(&mut current));
            current.push_str(word);
        } else {
            if n > 0 {
                current.push(' ');
            }
            current.push_str(word);
        }
    }
    if !current.is_empty() {
        lines.push(current);
    }
    lines
}

/// A thread requirement (draftwright's `ThreadRequirement`), from its words, its structured
/// statement, or both reconciled.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Thread {
    pub(crate) internal: bool,
    pub(crate) designation: String,
    pub(crate) nominal: f64,
    pub(crate) pitch: f64,
    pub(crate) class: String,
    pub(crate) hand: String,
    pub(crate) full_available_length: bool,
    pub(crate) minimum_full_thread: Option<f64>,
    pub(crate) drill_diameter: Option<f64>,
    pub(crate) drill_depth: Option<f64>,
    pub(crate) drill_point_angle: Option<f64>,
    pub(crate) through: bool,
}

impl Thread {
    /// Compare rendered designation ink with this source requirement alone. Lint uses
    /// this pure parser without consulting presentation settings or the planned label.
    pub(crate) fn designation_matches(&self, text: &str) -> bool {
        crate::spec_notes::metric(text).is_ok_and(|(diameter, pitch)| {
            (diameter - self.nominal).abs() < 1e-6
                && (pitch - self.pitch).abs() < 1e-6
                && (self.class.is_empty()
                    || text.split_once('-').is_some_and(|(_, tail)| {
                        tail.split_whitespace().next() == Some(self.class.as_str())
                    }))
                && text.ends_with("LH") == (self.hand == "LH")
        })
    }

    /// Conventional metric tapping drills. Fractional coarse pitches at M8/M9/M12
    /// use the available drill sizes in Walter's Threading Technical Compendium;
    /// do not classify those stated drills as non-standard by subtracting pitch.
    pub(crate) fn conventional_drill(&self) -> bool {
        let recommended = [(8.0, 1.25, 6.8), (9.0, 1.25, 7.8), (12.0, 1.75, 10.2)]
            .into_iter()
            .find(|&(d, p, _)| {
                same_number(d, self.nominal, 1e-9) && same_number(p, self.pitch, 1e-9)
            })
            .map_or(self.nominal - self.pitch, |(_, _, drill)| drill);
        self.drill_diameter
            .is_some_and(|d| same_number(d, recommended, 1e-6))
    }

    /// `ThreadRequirement.__post_init__`'s refusals.
    fn check(self) -> Result<Self, String> {
        for (name, v) in [("nominal_diameter", self.nominal), ("pitch", self.pitch)] {
            if !(v.is_finite() && v > 0.0) {
                return Err(format!("thread {name} must be finite and positive"));
            }
        }
        for (name, v) in [
            ("minimum_full_thread", self.minimum_full_thread),
            ("drill_diameter", self.drill_diameter),
            ("drill_depth", self.drill_depth),
            ("drill_point_angle", self.drill_point_angle),
        ] {
            if v.is_some_and(|v| !(v.is_finite() && v > 0.0)) {
                return Err(format!("thread {name} must be finite and positive"));
            }
        }
        if self.internal
            && let (Some(full), Some(depth)) = (self.minimum_full_thread, self.drill_depth)
            && full > depth
        {
            return Err("thread minimum full thread cannot exceed drill depth".into());
        }
        if self.through
            && (!self.internal || self.drill_depth.is_some() || self.drill_point_angle.is_some())
        {
            return Err("through thread must be internal without drill depth or point".into());
        }
        Ok(self)
    }

    /// `callout_suffix`: the terms the owner's own dimensions do not state.
    fn suffix(&self) -> String {
        if !self.internal {
            let length = if self.full_available_length {
                ", FULL AVAILABLE LENGTH"
            } else {
                ""
            };
            let explicit = self
                .minimum_full_thread
                .map(|v| format!("; {} MIN FULL THREAD", g6(v)))
                .unwrap_or_default();
            return format!("{}{length}{explicit}", self.designation);
        }
        let mut terms = vec![self.designation.clone()];
        if self.through {
            terms.push("THRU".into());
        }
        if let Some(full) = self.minimum_full_thread {
            terms.push(format!("{} MIN FULL THREAD", g6(full)));
        }
        if let Some(angle) = self.drill_point_angle {
            terms.push(format!("{}° CONVENTIONAL DRILL POINT", g6(angle)));
        }
        terms.join("; ")
    }
}

/// A knurl requirement (draftwright's `KnurlRequirement`).
#[derive(Clone, Debug, PartialEq)]
struct Knurl {
    /// `straight` or `diamond`.
    pattern: String,
    pitch: f64,
    full_width: bool,
    edge_chamfer: Option<f64>,
    maximum_diameter: Option<f64>,
    /// `cut`, `formed`: the processes permitted.
    processes: Vec<&'static str>,
}

impl Knurl {
    fn check(self) -> Result<Self, String> {
        if !(self.pitch.is_finite() && self.pitch > 0.0) {
            return Err("knurl pitch must be finite and positive".into());
        }
        for (name, v) in [
            ("edge_chamfer", self.edge_chamfer),
            ("maximum_diameter", self.maximum_diameter),
        ] {
            if v.is_some_and(|v| !(v.is_finite() && v > 0.0)) {
                return Err(format!("knurl {name} must be finite and positive"));
            }
        }
        Ok(self)
    }

    /// `callout_suffix`: compact terms after the owner's diameter.
    fn suffix(&self) -> String {
        let magnitude = g6;
        let mut terms = Vec::new();
        if self.maximum_diameter.is_some() {
            terms.push("MAX AFTER KNURL".to_string());
        }
        let mut knurl = format!(
            "{} KNURL P{}",
            self.pattern.to_uppercase(),
            magnitude(self.pitch)
        );
        if self.full_width {
            knurl += " FULL WIDTH";
            if let Some(c) = self.edge_chamfer {
                knurl += &format!(" TO C{} CHAMFERS", magnitude(c));
            }
        }
        terms.push(knurl);
        if !self.processes.is_empty() {
            let p: Vec<String> = self.processes.iter().map(|p| p.to_uppercase()).collect();
            terms.push(format!("{} PERMITTED", p.join("/")));
        }
        terms.join("; ")
    }

    /// `callout_text`: the whole requirement, its maximum diameter leading.
    fn text(&self) -> String {
        match self.maximum_diameter {
            None => self.suffix(),
            Some(d) => format!("ø{} {}", g6(d), self.suffix()),
        }
    }
}

/// A position in a requirement's words, matched as draftwright's patterns match them
/// (case-insensitive, `\s` runs where they allow them).
struct Words<'a> {
    s: &'a str,
    at: usize,
}

impl<'a> Words<'a> {
    /// Whitespace skipped; how much.
    fn spaces(&mut self) -> usize {
        let rest = &self.s[self.at..];
        let n = rest.len() - rest.trim_start().len();
        self.at += n;
        n
    }

    /// At least one whitespace character (`\s+`).
    fn space(&mut self) -> bool {
        self.spaces() > 0
    }

    /// *word* exactly, letters in either case.
    fn lit(&mut self, word: &str) -> bool {
        let rest = &self.s[self.at..];
        let ok = rest.len() >= word.len()
            && rest.is_char_boundary(word.len())
            && rest[..word.len()].eq_ignore_ascii_case(word);
        if ok {
            self.at += word.len();
        }
        ok
    }

    /// `\d+(?:\.\d+)?`.
    fn number(&mut self) -> Option<f64> {
        let rest = &self.s[self.at..];
        let int = rest
            .find(|c: char| !c.is_ascii_digit())
            .unwrap_or(rest.len());
        if int == 0 {
            return None;
        }
        let frac = rest[int..]
            .strip_prefix('.')
            .map(|f| f.find(|c: char| !c.is_ascii_digit()).unwrap_or(f.len()))
            .filter(|&n| n > 0)
            .map_or(0, |n| n + 1);
        let v = rest[..int + frac].parse().ok()?;
        self.at += int + frac;
        Some(v)
    }

    /// `[A-Za-z0-9]+`.
    fn alnum(&mut self) -> Option<&'a str> {
        let rest = &self.s[self.at..];
        let n = rest
            .find(|c: char| !c.is_ascii_alphanumeric())
            .unwrap_or(rest.len());
        (n > 0).then(|| {
            self.at += n;
            &rest[..n]
        })
    }

    fn end(&self) -> bool {
        self.at == self.s.len()
    }
}

/// A thread designation at the start of the words (`M<nominal>\s*x\s*<pitch>-<class>\s+RH|LH`):
/// the designation as written, its nominal size, pitch, class and hand.
fn thread_designation(w: &mut Words) -> Option<(String, f64, f64, String, String)> {
    let start = w.at;
    if !w.lit("M") {
        return None;
    }
    let nominal = w.number()?;
    w.spaces();
    if !w.lit("x") {
        return None;
    }
    w.spaces();
    let pitch = w.number()?;
    if !w.lit("-") {
        return None;
    }
    let class = w.alnum()?.to_string();
    if !w.space() {
        return None;
    }
    let hand = if w.lit("RH") {
        "RH"
    } else if w.lit("LH") {
        "LH"
    } else {
        return None;
    };
    Some((
        w.s[start..w.at].to_string(),
        nominal,
        pitch,
        class,
        hand.to_string(),
    ))
}

/// A thread from its words (`_text_thread_requirement`, `_EXTERNAL_THREAD`, `_INTERNAL_THREAD`).
fn prose_thread(kind: &str, label: &str) -> Result<Thread, String> {
    let internal = kind == "internal_thread";
    let syntax = || format!("unsupported {} syntax", kind.replace('_', " "));
    let text = label.trim();
    let mut w = Words { s: text, at: 0 };
    let (designation, nominal, pitch, class, hand) =
        thread_designation(&mut w).ok_or_else(syntax)?;
    if !w.lit(",") {
        return Err(syntax());
    }
    w.spaces();
    let mut t = Thread {
        internal,
        designation,
        nominal,
        pitch,
        class,
        hand,
        full_available_length: !internal,
        minimum_full_thread: None,
        drill_diameter: None,
        drill_depth: None,
        drill_point_angle: None,
        through: false,
    };
    if !internal {
        let region = (|| {
            if !w.lit("full available length on nominal DIA") || !w.space() {
                return None;
            }
            let region = w.number()?;
            (w.space() && w.lit("region") && w.end()).then_some(region)
        })()
        .ok_or_else(syntax)?;
        if !same_number(nominal, region, 1e-6) {
            return Err("external thread designation and nominal region disagree".into());
        }
        return t.check();
    }
    let after = w.at;
    // `<full> mm minimum full thread; DIA <drill> tapping drill x <depth> mm full-diameter
    // depth[; conventional <angle> degree drill point]`.
    let blind = (|| {
        let full = w.number()?;
        w.spaces();
        if !w.lit("mm minimum full thread;") {
            return None;
        }
        w.spaces();
        if !(w.lit("DIA") && w.space()) {
            return None;
        }
        let drill = w.number()?;
        if !(w.space() && w.lit("tapping drill") && w.space() && w.lit("x") && w.space()) {
            return None;
        }
        let depth = w.number()?;
        w.spaces();
        if !w.lit("mm full-diameter depth") {
            return None;
        }
        let before = w.at;
        let angle = (|| {
            if !w.lit(";") {
                return None;
            }
            w.spaces();
            if !(w.lit("conventional") && w.space()) {
                return None;
            }
            let angle = w.number()?;
            (w.space() && w.lit("degree drill point")).then_some(angle)
        })();
        if angle.is_none() {
            w.at = before;
        }
        w.end().then_some((full, drill, depth, angle))
    })();
    if let Some((full, drill, depth, angle)) = blind {
        t.minimum_full_thread = Some(full);
        t.drill_diameter = Some(drill);
        t.drill_depth = Some(depth);
        t.drill_point_angle = angle;
        return t.check();
    }
    w.at = after;
    // `full thread through; DIA <drill> tapping drill through`.
    let through = (|| {
        if !w.lit("full thread through;") {
            return None;
        }
        w.spaces();
        if !(w.lit("DIA") && w.space()) {
            return None;
        }
        let drill = w.number()?;
        (w.space() && w.lit("tapping drill through") && w.end()).then_some(drill)
    })();
    let drill = through.ok_or_else(syntax)?;
    t.drill_diameter = Some(drill);
    t.through = true;
    t.check()
}

/// A knurl from its words (`_text_knurl_requirement`, `_KNURL`).
fn prose_knurl(label: &str) -> Result<Knurl, String> {
    let text = label.trim();
    let mut w = Words { s: text, at: 0 };
    let parsed = (|| {
        let pattern = if w.lit("Straight") {
            "straight"
        } else if w.lit("Diamond") {
            "diamond"
        } else {
            return None;
        };
        if !w.lit(" knurl,") {
            return None;
        }
        w.spaces();
        let pitch = w.number()?;
        w.spaces();
        if !w.lit("mm pitch,") {
            return None;
        }
        w.spaces();
        let before = w.at;
        let chamfer = (|| {
            if !w.lit("full width between C") {
                return None;
            }
            let c = w.number()?;
            if !w.lit(" chamfers,") {
                return None;
            }
            w.spaces();
            Some(c)
        })();
        if chamfer.is_none() {
            w.at = before;
        }
        if !(w.lit("DIA") && w.space()) {
            return None;
        }
        let diameter = w.number()?;
        w.spaces();
        if !w.lit("mm maximum after knurling;") {
            return None;
        }
        w.spaces();
        (w.lit("cut or formed process permitted") && w.end())
            .then_some((pattern, pitch, chamfer, diameter))
    })();
    let (pattern, pitch, chamfer, diameter) =
        parsed.ok_or("unsupported knurl requirement syntax")?;
    Knurl {
        pattern: pattern.into(),
        pitch,
        full_width: chamfer.is_some(),
        edge_chamfer: chamfer,
        maximum_diameter: Some(diameter),
        processes: vec!["cut", "formed"],
    }
    .check()
}

/// `_same_number`: within *tol*, or a millionth of *b*.
fn same_number(a: f64, b: f64, tol: f64) -> bool {
    (a - b).abs() <= tol.max(b.abs() * 1e-6)
}

/// The structured field names draftwright reads for each kind
/// (`_STRUCTURED_MANUFACTURING_FIELDS`).
fn structured_names(kind: &str) -> &'static [&'static str] {
    match kind {
        "internal_thread" => &[
            "thread side",
            "designation",
            "nominal size",
            "pitch",
            "fit class",
            "hand",
            "through",
            "tapping drill diameter",
            "tapping drill depth",
            "minimum full thread",
            "full thread length",
            "full thread depth",
            "drill diameter",
            "drill depth",
        ],
        "external_thread" => &[
            "thread side",
            "designation",
            "nominal size",
            "pitch",
            "fit class",
            "hand",
            "thread length",
        ],
        _ => &[
            "pattern",
            "diametral pitch",
            "major diameter",
            "pitch",
            "maximum diameter",
        ],
    }
}

/// A requirement's structured fields, checked (`_structured_fields`, `_structured_number`,
/// `_structured_text`).
struct Fields<'a>(&'a [(String, crate::pmi::Field)]);

impl<'a> Fields<'a> {
    fn checked(r: &'a Record) -> Result<Self, String> {
        let names: Vec<&str> = r.fields.iter().map(|(n, _)| n.as_str()).collect();
        let distinct: BTreeSet<&str> = names.iter().copied().collect();
        if distinct.len() != names.len() || names.iter().any(|n| n.is_empty()) {
            return Err("structured manufacturing fields have duplicate or empty names".into());
        }
        let known = structured_names(&r.kind);
        let unknown: Vec<&str> = distinct
            .iter()
            .copied()
            .filter(|n| {
                !known.contains(n) && !matches!(*n, "id" | "id ambiguity" | "characteristic number")
            })
            .collect();
        if !unknown.is_empty() {
            return Err(format!(
                "unsupported structured manufacturing fields: {}",
                unknown.join(", ")
            ));
        }
        Ok(Fields(&r.fields))
    }

    fn get(&self, name: &str) -> Option<&'a crate::pmi::Field> {
        self.0.iter().find(|(n, _)| n == name).map(|(_, v)| v)
    }

    fn has(&self, name: &str) -> bool {
        self.get(name).is_some()
    }

    fn number(&self, name: &str) -> Result<f64, String> {
        match self.get(name) {
            Some(crate::pmi::Field::Length(v)) if v.is_finite() && *v > 0.0 => Ok(*v),
            _ => Err(format!(
                "structured manufacturing {name} needs a positive length measure"
            )),
        }
    }

    fn alias(&self, name: &str, alias: &str) -> Result<f64, String> {
        if self.has(name) && self.has(alias) {
            return Err(format!(
                "structured manufacturing has both {name} and {alias}"
            ));
        }
        self.number(if self.has(name) { name } else { alias })
    }

    fn text(&self, name: &str) -> Result<String, String> {
        match self.get(name) {
            Some(crate::pmi::Field::Text(s)) if !s.trim().is_empty() => Ok(s.trim().to_string()),
            _ => Err(format!(
                "structured manufacturing {name} needs descriptive text"
            )),
        }
    }
}

/// `M<nominal>\s*x\s*<pitch>` as the whole of *s*.
fn metric_size(s: &str) -> Option<(f64, f64)> {
    let mut w = Words { s, at: 0 };
    if !w.lit("M") {
        return None;
    }
    let nominal = w.number()?;
    w.spaces();
    if !w.lit("x") {
        return None;
    }
    w.spaces();
    let pitch = w.number()?;
    w.end().then_some((nominal, pitch))
}

/// A thread from its structured statement (`_structured_thread_requirement` with
/// `_structured_thread_extent`), *prose* its words' reading when they have one.
fn structured_thread(
    r: &Record,
    prose: Option<&Thread>,
    cylinders: &[Cylinder],
) -> Result<Thread, String> {
    let f = Fields::checked(r)?;
    let side = f.text("thread side")?.to_lowercase();
    if !matches!(side.as_str(), "internal" | "external") || r.kind != format!("{side}_thread") {
        return Err("structured thread side disagrees with requirement kind".into());
    }
    let internal = side == "internal";
    let (nominal, pitch) = metric_size(&f.text("designation")?)
        .ok_or("structured thread designation or nominal size is unsupported")?;
    let disagree = "structured thread designation disagrees with nominal size or pitch";
    if f.has("nominal size") {
        let size = f.text("nominal size")?;
        let mut w = Words { s: &size, at: 0 };
        let stated = w.lit("M").then(|| w.number()).flatten().filter(|_| w.end());
        if !stated.is_some_and(|n| same_number(nominal, n, 1e-6)) {
            return Err(disagree.into());
        }
    }
    if f.has("pitch") && !same_number(pitch, f.number("pitch")?, 1e-6) {
        return Err(disagree.into());
    }
    let class = f.text("fit class")?;
    if !class.chars().all(|c| c.is_ascii_alphanumeric()) {
        return Err("structured thread fit class is unsupported".into());
    }
    let hand = match f.text("hand")?.to_lowercase().as_str() {
        "right" | "rh" => "RH",
        "left" | "lh" => "LH",
        _ => return Err("structured thread hand is unsupported".into()),
    };
    let through = match f.get("through") {
        None => false,
        Some(crate::pmi::Field::Text(s)) if matches!(s.to_lowercase().as_str(), "true" | "yes") => {
            true
        }
        Some(crate::pmi::Field::Text(s)) if matches!(s.to_lowercase().as_str(), "false" | "no") => {
            false
        }
        Some(_) => return Err("structured thread through flag is unsupported".into()),
    };
    let designation = format!("M{} x {}-{class} {hand}", g6(nominal), g6(pitch));
    let (mut drill, mut depth, mut full) = (None, None, None);
    let full_available_length = if internal {
        drill = Some(f.alias("tapping drill diameter", "drill diameter")?);
        depth = if through {
            None
        } else {
            Some(f.alias("tapping drill depth", "drill depth")?)
        };
        if through && (f.has("tapping drill depth") || f.has("drill depth")) {
            return Err("structured through tap cannot declare a blind drill depth".into());
        }
        for name in [
            "minimum full thread",
            "full thread length",
            "full thread depth",
        ] {
            if f.has(name) {
                let value = f.number(name)?;
                if full.is_some_and(|v| !same_number(v, value, 1e-6)) {
                    return Err("structured full thread depth aliases disagree".into());
                }
                full = Some(value);
            }
        }
        if through && let Some(full) = full {
            let fits = !cylinders.is_empty()
                && cylinders
                    .iter()
                    .all(|c| full <= c.interval[1] - c.interval[0] + 0.01);
            if !fits {
                return Err("structured minimum full thread exceeds source cylinder".into());
            }
        }
        false
    } else {
        if through {
            return Err("structured external thread cannot be a through tap".into());
        }
        if f.has("thread length") {
            // A tip chamfer can shorten the source face: its owner's span is checked once the
            // owner is known ([`manufacturing`]).
            f.number("thread length")?;
        } else if !prose.is_some_and(|p| p.full_available_length) {
            return Err("structured external thread has no length requirement".into());
        }
        true
    };
    Thread {
        internal,
        designation,
        nominal,
        pitch,
        class,
        hand: hand.into(),
        full_available_length,
        minimum_full_thread: full,
        drill_diameter: drill,
        drill_depth: depth,
        drill_point_angle: None,
        through,
    }
    .check()
}

/// Whether record *r* pairs a requirement in words with its structured statement (draftwright's
/// two source IDs): it has fields, and its label is its words, not its kind (an unpaired
/// structured statement is labelled with its kind, [`crate::pmi`]).
fn has_parameters(r: &Record) -> bool {
    r.fields.iter().any(|(name, _)| {
        !matches!(
            name.as_str(),
            "id" | "id ambiguity" | "characteristic number"
        )
    })
}
fn paired(r: &Record) -> bool {
    has_parameters(r) && r.label != r.kind.replace('_', " ")
}

/// A thread from its record (`_thread_requirement`): its words and its structured statement
/// reconciled, value by value.
fn thread(r: &Record, cylinders: &[Cylinder]) -> Result<Thread, String> {
    if !has_parameters(r) {
        return prose_thread(&r.kind, &r.label);
    }
    let prose = match prose_thread(&r.kind, &r.label) {
        Ok(t) => Some(t),
        Err(_) if paired(r) => return Err("structured thread cannot reconcile with prose".into()),
        Err(_) => None,
    };
    let structured = structured_thread(r, prose.as_ref(), cylinders)?;
    let Some(prose) = prose else {
        return Ok(structured);
    };
    let num = |a: Option<f64>, b: Option<f64>| match (a, b) {
        (Some(a), Some(b)) => same_number(a, b, 1e-6),
        (a, b) => a == b,
    };
    let s = &structured;
    let agree = s.internal == prose.internal
        && same_number(s.nominal, prose.nominal, 1e-6)
        && same_number(s.pitch, prose.pitch, 1e-6)
        && s.class == prose.class
        && s.hand == prose.hand
        && num(s.minimum_full_thread, prose.minimum_full_thread)
        && num(s.drill_diameter, prose.drill_diameter)
        && num(s.drill_depth, prose.drill_depth)
        && s.through == prose.through
        && s.full_available_length == prose.full_available_length;
    if !agree {
        return Err("structured thread values disagree with prose".into());
    }
    Thread {
        drill_point_angle: prose.drill_point_angle,
        ..structured
    }
    .check()
}

/// A knurl from its record (`_knurl_requirement`, `_structured_knurl_requirement`).
fn knurl(r: &Record) -> Result<Knurl, String> {
    if !has_parameters(r) {
        return prose_knurl(&r.label);
    }
    let prose = match prose_knurl(&r.label) {
        Ok(k) => Some(k),
        Err(_) if paired(r) => return Err("structured knurl cannot reconcile with prose".into()),
        Err(_) => None,
    };
    let f = Fields::checked(r)?;
    let pattern = f.text("pattern")?.to_lowercase();
    if !matches!(pattern.as_str(), "straight" | "diamond") {
        return Err("structured knurl pattern is unsupported".into());
    }
    if prose.is_none() && (f.has("diametral pitch") || f.has("major diameter")) {
        // These labels alone do not establish a linear pitch or a maximum after knurling.
        return Err("structured knurl aliases need a matching prose requirement".into());
    }
    let structured = Knurl {
        pattern,
        pitch: f.alias("diametral pitch", "pitch")?,
        full_width: false,
        edge_chamfer: None,
        maximum_diameter: Some(f.alias("major diameter", "maximum diameter")?),
        processes: Vec::new(),
    }
    .check()?;
    let Some(prose) = prose else {
        return Ok(structured);
    };
    let agree = structured.pattern == prose.pattern
        && same_number(structured.pitch, prose.pitch, 1e-6)
        && match (structured.maximum_diameter, prose.maximum_diameter) {
            (Some(a), Some(b)) => same_number(a, b, 1e-6),
            _ => false,
        };
    if !agree {
        return Err("structured knurl values disagree with prose".into());
    }
    Ok(Knurl {
        full_width: prose.full_width,
        edge_chamfer: prose.edge_chamfer,
        processes: prose.processes,
        ..structured
    })
}

/// Where a thread or knurl requirement applies (draftwright's `_manufacturing_requirement_topology`
/// with `_cylindrical_references_from_shapes`): its faces' exact cylinders, identical ones once,
/// and the reasons a face is none. A requirement on an edge, or on no face, has none.
pub fn requirement_geometry(part: &Part, r: &Record) -> Geometry {
    let mut g = Geometry::default();
    let noun = "manufacturing requirement";
    if !r.edges.is_empty() {
        g.reasons
            .push(format!("one {noun} reference is not a face"));
    }
    for &f in &r.faces {
        match cylinder(part, f) {
            Ok(c) => {
                if !g.cylinders.iter().any(|d| same_cylinder(d, &c)) {
                    g.cylinders.push(c);
                }
            }
            Err(why) => g.reasons.push(
                why.replace("one diameter reference", &format!("one {noun} reference"))
                    .replace(
                        "one cylindrical reference",
                        &format!("one {noun} reference"),
                    ),
            ),
        }
    }
    if g.cylinders.is_empty() && g.reasons.is_empty() {
        g.reasons
            .push(format!("{noun} reference geometry is unavailable"));
    }
    g.reasons = dedup(g.reasons);
    g
}

/// A feature a thread or knurl may belong to (draftwright's `StepFeature`, `BossFeature`,
/// `HoleFeature`), read in the drawing's frame.
struct Owner {
    id: String,
    /// `hole`, `step` or `boss`; or `pattern`, a hole pattern the callouts state as one, which
    /// only a Spec-authored thread names ([`crate::spec_notes`]): no AP242 requirement's
    /// cylinder proves a pattern ([`owns`]).
    kind: &'static str,
    axis: usize,
    diameter: f64,
    /// A hole's opening (its member station); a point on a step's or boss's axis.
    at: [f64; 3],
    /// A step's or boss's span along its axis; a hole's none.
    span: Option<[f64; 2]>,
    through: bool,
    /// A hole's cylinder depth, as the source's drill depth states it.
    depth: Option<f64>,
    /// The axial entry of the chamfer on a hole's mouth, which depths from the surface include.
    entry: f64,
}

/// The features that may own a thread or knurl: each hole the callouts state on its own (a
/// member of a hole pattern the callouts state as a pattern is not one: draftwright's pattern
/// owns a thread only when it has one member), each turned step that is a band of its own, and
/// each cylindrical boss that is neither a groove's floor nor a turned step's band (as
/// [`crate::dims`] reads them).
fn owners(analysis: &Analysis) -> Vec<Owner> {
    use crate::bridge::holes;
    let frame = holes::frame(analysis);
    let mut out = Vec::new();
    let readable: Vec<holes::Hole> = holes::read(analysis)
        .into_iter()
        .filter_map(|(_, h)| h.ok())
        .filter(|h| h.principal.is_some() && (h.through || h.depth.is_some()))
        .collect();
    let reads: Vec<&holes::Hole> = readable.iter().collect();
    let bosses = holes::bosses(analysis);
    let mut taken: BTreeSet<usize> = BTreeSet::new();
    for p in analysis
        .recognition
        .as_ref()
        .map_or(&[][..], |r| r.patterns.as_slice())
    {
        if let Ok(members) = holes::established_with(p, &reads, &bosses)
            && !members.iter().any(|m| taken.contains(m))
        {
            let h = &readable[members[0]];
            out.push(Owner {
                id: p.id.clone(),
                kind: "pattern",
                // Invariant: The hole iterator was filtered to principal-axis holes.
                axis: h.principal.expect("a principal hole").0,
                diameter: h.diameter,
                at: h.at,
                span: None,
                through: h.through,
                depth: h.depth,
                entry: h.entry,
            });
            taken.extend(members);
        }
    }
    for (i, h) in readable.iter().enumerate() {
        if taken.contains(&i) {
            continue;
        }
        out.push(Owner {
            id: h.feature.id.clone(),
            kind: "hole",
            // Invariant: The hole iterator was filtered to principal-axis holes.
            axis: h.principal.expect("a principal hole").0,
            diameter: h.diameter,
            at: h.at,
            span: None,
            through: h.through,
            depth: h.depth,
            entry: h.entry,
        });
    }
    if let Ok(steps) = crate::lint::turned::bands(analysis) {
        for f in steps {
            if let Ok(b) = crate::lint::turned::band(&frame, f) {
                out.push(Owner {
                    id: f.id.clone(),
                    kind: "step",
                    axis: b.axis,
                    diameter: b.diameter,
                    at: b.on,
                    span: Some([b.lo, b.hi]),
                    through: false,
                    depth: None,
                    entry: 0.0,
                });
            }
        }
    }
    for f in analysis.features.iter().filter(|f| f.kind == "boss") {
        if crate::lint::bosses::groove_floor(analysis, f)
            || crate::lint::bosses::turned_band(analysis, f)
        {
            continue;
        }
        let p = &f.parameters;
        let (Some(top), Some(dir), Some(diameter)) = (
            holes::point(p.get("location")),
            holes::point(p.get("axis")),
            p.get("diameter").and_then(serde_json::Value::as_f64),
        ) else {
            continue;
        };
        let top = holes::to_part(&frame, top);
        let dir = holes::dir_to_part(&frame, dir);
        let Some(k) = holes::principal(dir) else {
            continue;
        };
        let height = p
            .get("height")
            .and_then(serde_json::Value::as_f64)
            .filter(|h| *h > 0.0);
        let span = height.map(|h| {
            let base = top[k] - dir[k].signum() * h;
            [base.min(top[k]), base.max(top[k])]
        });
        out.push(Owner {
            id: f.id.clone(),
            kind: "boss",
            axis: k,
            diameter,
            at: top,
            span,
            through: false,
            depth: None,
            entry: 0.0,
        });
    }
    out
}

/// Whether *c*'s axis is the line along world axis *k* through *p* (`_same_axis_line`, 0.01 mm).
fn same_axis_line(c: &Cylinder, p: [f64; 3], k: usize) -> bool {
    (0..3).all(|i| i == k || (c.origin[i] - p[i]).abs() <= 0.01)
}

/// `_external_owner_matches`: an external cylinder on the step's or boss's axis, of its
/// diameter, within its span.
fn external_owner(c: &Cylinder, o: &Owner) -> bool {
    if c.internal || c.principal() != Some(o.axis) {
        return false;
    }
    if !same_number(2.0 * c.radius, o.diameter, 0.01) || !same_axis_line(c, o.at, o.axis) {
        return false;
    }
    let Some([lo, hi]) = o.span else {
        return false;
    };
    let [a, b] = c.interval;
    lo - 0.01 <= a && a < b && b <= hi + 0.01
}

/// `_internal_member_matches`: an internal cylinder on the hole's axis, of its diameter, running
/// from its opening its depth (a through hole's within the part's box).
fn internal_owner(c: &Cylinder, o: &Owner, envelope: [[f64; 3]; 2]) -> bool {
    if !c.internal || c.principal() != Some(o.axis) {
        return false;
    }
    if !same_number(2.0 * c.radius, o.diameter, 0.01) || !same_axis_line(c, o.at, o.axis) {
        return false;
    }
    let k = o.axis;
    let [lo, hi] = c.interval;
    let station = o.at[k];
    if o.through {
        let [box_lo, box_hi] = [envelope[0][k], envelope[1][k]];
        if !(lo - 0.01 <= station && station <= hi + 0.01) {
            return false;
        }
        if lo < box_lo - 0.01 || hi > box_hi + 0.01 || hi.min(box_hi) - lo.max(box_lo) <= 0.01 {
            return false;
        }
        return o.depth.is_none_or(|d| same_number(hi - lo, d, 0.01));
    }
    let Some(depth) = o.depth else {
        return false;
    };
    same_number(hi - lo, depth, 0.01) && (lo - station).abs().min((hi - station).abs()) <= 0.01
}

/// A requirement's owner (its index), its reading, and how its owner's views draw it.
type Proposal = (usize, Aspect, Option<Depiction>);

/// A requirement read from its record.
#[derive(Clone, Debug, PartialEq)]
enum Aspect {
    Thread(Thread),
    Knurl(Knurl),
}

/// A thread or knurl the Spec authors on feature `feature` (its record's field): the owner it
/// names, if the callouts state that feature (a hole on its own, a hole pattern, a turned step or
/// boss), and what its callout prints; or why its feature cannot carry it: a knurl not on a
/// turned step; a tap whose hole is larger than its nominal size or smaller than its minor
/// diameter's bound (nominal less 1.5 pitches); an external thread whose nominal size is not its
/// diameter; a length past a blind hole's depth or a step's span.
fn authored(
    r: &Record,
    candidates: &[Owner],
    analysis: &Analysis,
) -> Result<(usize, Aspect), String> {
    let why = blockers(r);
    if !why.is_empty() {
        return Err(dedup(why).join("; "));
    }
    let f = Fields(&r.fields);
    let feature = f.text("feature")?;
    let knurl = r.kind == "knurl";
    let Some(i) = candidates.iter().position(|o| o.id == feature) else {
        let pattern = analysis
            .recognition
            .as_ref()
            .and_then(|rec| rec.patterns.iter().find(|p| p.members.contains(&feature)));
        return Err(match pattern {
            Some(p) if !knurl => format!(
                "{feature} is called out with its pattern {}, so the thread is set on the pattern (features/{}/thread)",
                p.id,
                crate::items::key(&p.id)
            ),
            _ => format!(
                "no callout states {feature} on its own, so it carries no {}",
                if knurl { "knurl" } else { "thread" }
            ),
        });
    };
    let o = &candidates[i];
    let num = |v: f64| crate::dims::fmt(v);
    if knurl {
        if o.kind != "step" && o.kind != "boss" {
            return Err(format!(
                "{feature} is not a turned diameter, so it carries no knurl"
            ));
        }
        return Ok((
            i,
            Aspect::Knurl(
                Knurl {
                    pattern: f.text("pattern")?.to_string(),
                    pitch: f.number("pitch")?,
                    full_width: false,
                    edge_chamfer: None,
                    maximum_diameter: None,
                    processes: Vec::new(),
                }
                .check()?,
            ),
        ));
    }
    let designation = f.text("designation")?;
    let nominal = f.number("nominal")?;
    let pitch = f.number("pitch")?;
    let length = f.number("length").ok();
    if matches!(o.kind, "hole" | "pattern") {
        let minor = nominal - 1.5 * pitch;
        if o.diameter > nominal + 0.01 || o.diameter < minor - 0.01 {
            return Err(format!(
                "its hole is ⌀{}, which a {designation} tap cannot be cut in (from ⌀{} to ⌀{})",
                num(o.diameter),
                num(minor),
                num(nominal)
            ));
        }
        if let (Some(l), Some(d), false) = (length, o.depth, o.through)
            && l > d + o.entry + 0.01
        {
            return Err(format!(
                "its thread length {} exceeds its hole's depth {}",
                num(l),
                num(d + o.entry)
            ));
        }
    } else {
        if !same_number(o.diameter, nominal, 0.01) {
            return Err(format!(
                "its diameter is ⌀{}, not the nominal size of {designation}",
                num(o.diameter)
            ));
        }
        if let (Some(l), Some([lo, hi])) = (length, o.span)
            && l > hi - lo + 0.01
        {
            return Err(format!(
                "its thread length {} exceeds its length {}",
                num(l),
                num(hi - lo)
            ));
        }
    }
    let internal = matches!(o.kind, "hole" | "pattern");
    let class = designation
        .split_once('-')
        .map(|(_, rest)| rest.split_whitespace().next().unwrap_or(""))
        .unwrap_or("")
        .to_string();
    let hand = if designation.ends_with("LH") {
        "LH"
    } else {
        "RH"
    }
    .to_string();
    Ok((
        i,
        Aspect::Thread(
            Thread {
                internal,
                designation,
                nominal,
                pitch,
                class,
                hand,
                full_available_length: !internal && length.is_none(),
                minimum_full_thread: length,
                drill_diameter: internal.then_some(o.diameter),
                // The Spec's thread states the drill the callout prints: from the surface the hole
                // opens on. (A source's own drill depth is matched against the cylinder, above.)
                drill_depth: if internal && !o.through {
                    o.depth.map(|d| d + o.entry)
                } else {
                    None
                },
                drill_point_angle: None,
                through: internal && o.through && length.is_none(),
            }
            .check()?,
        ),
    ))
}

/// `_manufacturing_owner_matches`: whether *o* owns requirement *a* on cylinder *c*.
fn owns(a: &Aspect, c: &Cylinder, o: &Owner, envelope: [[f64; 3]; 2]) -> bool {
    match a {
        Aspect::Thread(t) if t.internal => {
            o.kind == "hole"
                && if t.through {
                    o.through && internal_owner(c, o, envelope)
                } else {
                    !o.through
                        && o.depth.is_some_and(|d| {
                            t.drill_depth.is_some_and(|dd| same_number(dd, d, 0.01))
                        })
                        && internal_owner(c, o, envelope)
                }
        }
        _ => matches!(o.kind, "step" | "boss") && external_owner(c, o),
    }
}

/// The part's turned chamfers as `_turned_tip_chamfers_cover_gap` reads them: axis, legs, angle
/// and centre, in the drawing's frame.
fn turned_chamfers(analysis: &Analysis) -> Vec<(usize, f64, f64, f64, [f64; 3])> {
    use crate::bridge::holes;
    let frame = holes::frame(analysis);
    analysis
        .features
        .iter()
        .filter(|f| f.kind == "chamfer")
        .filter_map(|f| {
            let p = &f.parameters;
            if p.get("turned").and_then(serde_json::Value::as_bool) != Some(true) {
                return None;
            }
            let letter = p.get("axis")?.as_str()?;
            let q = ["x", "y", "z"].iter().position(|a| *a == letter)?;
            let mut d = [0.0; 3];
            d[q] = 1.0;
            let k = holes::principal(holes::dir_to_part(&frame, d))?;
            let at = holes::to_part(&frame, holes::point(p.get("at"))?);
            let n = |name: &str| p.get(name).and_then(serde_json::Value::as_f64);
            Some((k, n("leg1")?, n("leg2")?, n("angle")?, at))
        })
        .collect()
}

/// `_turned_tip_chamfers_cover_gap`: the owner's span runs past the thread's cylinder only where
/// a recognised equal-leg 45° end chamfer fills the gap.
fn tip_chamfers_cover(
    c: &Cylinder,
    o: &Owner,
    chamfers: &[(usize, f64, f64, f64, [f64; 3])],
) -> bool {
    let Some([owner_lo, owner_hi]) = o.span else {
        return false;
    };
    let k = o.axis;
    let [source_lo, source_hi] = c.interval;
    let mut found = false;
    for (lo, hi) in [(owner_lo, source_lo), (source_hi, owner_hi)] {
        let gap = hi - lo;
        if gap <= 0.01 {
            continue;
        }
        found = true;
        let station = 0.5 * (lo + hi);
        let covered = chamfers.iter().any(|&(axis, leg1, leg2, angle, at)| {
            let radial = (0..3)
                .filter(|&i| i != k)
                .map(|i| (at[i] - c.origin[i]).powi(2))
                .sum::<f64>()
                .sqrt();
            axis == k
                && same_number(leg1, gap, 0.01)
                && same_number(leg2, gap, 0.01)
                && same_number(angle, 45.0, 0.01)
                && same_number(at[k], station, 0.01)
                && same_number(radial, c.radius - gap / 2.0, 0.01)
        });
        if !covered {
            return false;
        }
    }
    found
}

/// The part's thread and knurl requirements given to their owners, as draftwright's
/// `lower_ap242_manufacturing_requirements` and `manufacturing_schedule` decide it:
///
/// - each record of kind `internal_thread`, `external_thread` or `knurl` is read from its words
///   and its structured statement, reconciled value by value (`_thread_requirement`,
///   `_knurl_requirement`); its one cylinder must be the size its text states (a tap's drill
///   diameter and drill depth, an external thread's nominal size, a knurl's maximum diameter);
/// - each internal-thread cylinder must prove one distinct hole owner; a shared requirement
///   reaches all of those callouts. Other requirements have one owner: a blind hole of the drill depth or a
///   through hole whose bore it is (an internal thread), a turned step or boss whose diameter
///   and span hold it (an external thread or knurl; a structured thread length is the cylinder's,
///   or the owner's when recognised 45° tip chamfers fill the difference);
/// - two requirements of one aspect on one owner are both refused;
/// - with two or more requirements owned and one suffix longer than 52 characters, each owner's
///   callout prints `SEE MFG n` and the manufacturing requirements table states them, in the
///   order of their source IDs.
///
/// What is not owned is refused with draftwright's reason, never dropped: a record a face of
/// which another requirement already claims, whose reading left blockers, whose faces are not
/// one exact cylinder, whose text disagrees with its cylinder, or that no feature or several
/// own. A requirement a boss owns prints on the boss's diameter callout, as a turned step's does.
pub fn manufacturing(analysis: &Analysis) -> Manufacturing {
    let mut out = Manufacturing::default();
    let Some(Reading::Read(report)) = &analysis.pmi else {
        return out;
    };
    let records: Vec<&Record> = report
        .records
        .iter()
        .filter(|r| r.category == Category::ManufacturingRequirement && owned_kind(&r.kind))
        .collect();
    if records.is_empty() {
        return out;
    }
    let candidates = owners(analysis);
    let chamfers = turned_chamfers(analysis);
    let mut claimed: Vec<(usize, u64)> = Vec::new();
    // Each record's proposal: its owner's index and its aspect, or why it has none.
    let mut proposals: Vec<Result<Vec<Proposal>, String>> = Vec::new();
    for r in &records {
        let proposal = (|| {
            // A thread or knurl the Spec authors names its feature ([`crate::spec_notes`]).
            if r.source_id.starts_with(crate::spec_gdt::SOURCE_PREFIX) {
                return authored(r, &candidates, analysis).map(|(owner, aspect)| {
                    let o = &candidates[owner];
                    let depiction = match &aspect {
                        Aspect::Thread(t) if !t.internal && t.full_available_length => {
                            o.span.map(|span| Depiction {
                                axis: o.axis,
                                on: o.at,
                                span,
                                diameter: o.diameter,
                                kind: DepictionKind::Thread {
                                    minor: t.nominal - 1.226_87 * t.pitch,
                                },
                            })
                        }
                        _ => None,
                    };
                    vec![(owner, aspect, depiction)]
                });
            }
            let mut reasons = blockers(r);
            let g = r.geometry.clone().unwrap_or_default();
            if reasons.is_empty() {
                // draftwright's topology resolver gives each imported face to one requirement.
                let held: Vec<u64> = r
                    .faces
                    .iter()
                    .filter_map(|f| claimed.iter().find(|(c, _)| c == f).map(|(_, by)| *by))
                    .filter(|by| *by != r.part21_id)
                    .collect();
                for by in held {
                    reasons.push(format!(
                        "one imported face is already claimed by manufacturing requirement #{by}"
                    ));
                }
                if reasons.is_empty() {
                    claimed.extend(r.faces.iter().map(|&f| (f, r.part21_id)));
                }
            }
            if !reasons.is_empty() {
                return Err(dedup(reasons).join("; "));
            }
            let aspect = if r.kind == "knurl" {
                Aspect::Knurl(knurl(r)?)
            } else {
                Aspect::Thread(thread(r, &g.cylinders)?)
            };
            let disagree =
                || "manufacturing requirement text disagrees with source cylinder".to_string();
            let c = g.cylinders.first().ok_or_else(disagree)?;
            let expected = match &aspect {
                Aspect::Knurl(k) => k.maximum_diameter,
                Aspect::Thread(t) if t.internal => t.drill_diameter,
                Aspect::Thread(t) => Some(t.nominal),
            };
            if !expected.is_some_and(|d| same_number(2.0 * c.radius, d, 0.01)) {
                return Err(disagree());
            }
            if let Aspect::Thread(t) = &aspect
                && t.internal
                && !t.through
                && !t
                    .drill_depth
                    .is_some_and(|d| same_number(c.interval[1] - c.interval[0], d, 0.01))
            {
                return Err(disagree());
            }
            // One semantic tap may apply to several drilled holes. Prove every
            // cylinder and owner before giving the shared requirement to their callouts.
            // External thread and knurl depictions still require one continuous cylinder.
            if g.cylinders.len() > 1 && !matches!(&aspect, Aspect::Thread(t) if t.internal) {
                return Err("manufacturing requirement needs one continuous cylinder".into());
            }
            let mut matched = Vec::new();
            for cylinder in &g.cylinders {
                if !expected.is_some_and(|d| same_number(2.0 * cylinder.radius, d, 0.01))
                    || matches!(&aspect, Aspect::Thread(t) if t.internal && !t.through
                        && !t.drill_depth.is_some_and(|d| same_number(cylinder.interval[1] - cylinder.interval[0], d, 0.01)))
                {
                    return Err(disagree());
                }
                let matches: Vec<usize> = (0..candidates.len())
                    .filter(|&i| owns(&aspect, cylinder, &candidates[i], analysis.envelope))
                    .collect();
                match matches[..] {
                    [owner] if !matched.contains(&owner) => matched.push(owner),
                    [] => return Err("unmatched manufacturing requirement: no canonical feature matches source topology".into()),
                    _ => return Err(format!("ambiguous manufacturing requirement: source topology matches {} canonical features", matches.len())),
                }
            }
            let owner = matched[0];
            let o = &candidates[owner];
            if let Aspect::Thread(t) = &aspect
                && !t.internal
                && let Ok(length) = Fields(&r.fields).number("thread length")
            {
                let span = o.span.ok_or("external thread owner has no axial span")?;
                let source = c.interval[1] - c.interval[0];
                if !(same_number(source, length, 0.01)
                    || (same_number(span[1] - span[0], length, 0.01)
                        && tip_chamfers_cover(c, o, &chamfers)))
                {
                    return Err("structured thread length disagrees with source cylinder or evidenced owner span".into());
                }
            }
            // The drawn length is the requirement's cylinder: where a stated thread length is the
            // owner's span, the difference is a tip chamfer, whose narrower profile the minor
            // diameter line must not run past.
            let span = c.interval;
            let kind = match &aspect {
                Aspect::Thread(t) if t.internal => None,
                // ISO 724's basic minor diameter of an external thread, d3 = d − 1.22687·P.
                Aspect::Thread(t) => Some(DepictionKind::Thread {
                    minor: t.nominal - 1.226_87 * t.pitch,
                }),
                Aspect::Knurl(k) => Some(DepictionKind::Knurl {
                    diamond: k.pattern == "diamond",
                    pitch: k.pitch,
                }),
            };
            let depiction = kind.filter(|_| o.kind == "step").map(|kind| Depiction {
                axis: o.axis,
                on: o.at,
                span,
                diameter: o.diameter,
                kind,
            });
            Ok(matched
                .into_iter()
                .map(|owner| (owner, aspect.clone(), depiction.clone()))
                .collect())
        })();
        proposals.push(proposal);
    }
    // One requirement of each aspect per owner.
    let aspect_name = |a: &Aspect| match a {
        Aspect::Thread(_) => "thread",
        Aspect::Knurl(_) => "knurl",
    };
    let mut claims: BTreeMap<(usize, &'static str), usize> = BTreeMap::new();
    for p in proposals.iter().flatten().flatten() {
        *claims.entry((p.0, aspect_name(&p.1))).or_default() += 1;
    }
    let ids = report.identities();
    for (r, p) in records.iter().zip(proposals) {
        let refusal = match (p, ids.id(&r.source_id)) {
            (Err(why), _) | (Ok(_), Err(why)) => Some(why),
            (Ok(proposals), Ok(id)) => {
                if let Some((_, aspect, _)) = proposals
                    .iter()
                    .find(|(owner, aspect, _)| claims[&(*owner, aspect_name(aspect))] > 1)
                {
                    let name = aspect_name(aspect);
                    Some(format!(
                        "ambiguous {name} ownership: multiple manufacturing requirements claim canonical feature"
                    ))
                } else {
                    for (owner, aspect, depiction) in proposals {
                        let name = aspect_name(&aspect);
                        let o = &candidates[owner];
                        let (suffix, text, through) = match &aspect {
                            Aspect::Thread(t) => {
                                let mut text = t.suffix();
                                if let Some(d) = t.drill_diameter {
                                    text += &format!("; ⌀{}", g6(d));
                                    if let Some(depth) = t.drill_depth {
                                        text += &format!(" ↧ {}", g6(depth));
                                    } else {
                                        text += " THRU";
                                    }
                                }
                                (t.suffix(), text, t.through)
                            }
                            Aspect::Knurl(k) => (k.suffix(), k.text(), false),
                        };
                        out.owned.push(Owned {
                            id: id.to_string(),
                            source: r.source_id.clone(),
                            owner: o.id.clone(),
                            aspect: name,
                            suffix,
                            text,
                            through,
                            depiction,
                            requirement: aspect,
                            presentation_reason: "source wording",
                        });
                    }
                    None
                }
            }
        };
        if let Some(why) = refusal {
            out.refused.push(Problem {
                id: format!("p:pmi_not_lowered:{}", r.source_id),
                code: "pmi_not_lowered".into(),
                severity: "error".into(),
                message: format!(
                    "PMI {} {} ({}) is not drawn: {}.",
                    r.kind.replace('_', " "),
                    r.source_id,
                    r.label,
                    crate::pmi::reader_reason(&why)
                ),
                subjects: Vec::new(),
                refs: vec![r.source_id.clone(), why],
                ..Default::default()
            });
        }
    }
    // The table, for two or more requirements one of which is too long for its callout.
    if out.owned.len() >= 2
        && out
            .owned
            .iter()
            .any(|o| o.suffix.chars().count() > SCHEDULE_SUFFIX)
    {
        let mut sources: Vec<&str> = out.owned.iter().map(|o| o.source.as_str()).collect();
        sources.sort();
        sources.dedup();
        out.tags = sources
            .iter()
            .enumerate()
            .map(|(i, s)| (s.to_string(), format!("MFG {}", i + 1)))
            .collect();
    }
    out
}

/// ISO 261 coarse pitch may be omitted; ISO 965 class and left hand remain explicit.
fn short_designation(t: &Thread) -> String {
    let mut s = format!("M{}", g6(t.nominal));
    if !crate::spec_notes::COARSE
        .iter()
        .any(|&(d, p)| same_number(d, t.nominal, 1e-9) && same_number(p, t.pitch, 1e-9))
    {
        s += &format!(" x {}", g6(t.pitch));
    }
    if !t.class.is_empty() {
        s += &format!("-{}", t.class);
    }
    if t.hand == "LH" {
        s += " LH";
    }
    s
}

fn depiction_enabled(o: &Owned, spec: &crate::model::Spec) -> bool {
    let Some(d) = &o.depiction else {
        return false;
    };
    // Require an enabled, non-omitted principal profile view. A cylinder length alone
    // never proves thread extent. Section and isometric views do not draw cosmetics.
    [("front", 1), ("plan", 2), ("side", 0)]
        .into_iter()
        .any(|(name, toward)| {
            if toward == d.axis {
                return false;
            }
            let view = spec.views.get(name);
            if view.is_some_and(|v| v.omit) {
                return false;
            }
            match d.kind {
                DepictionKind::Thread { .. } => {
                    view.and_then(|v| v.thread_depiction)
                        .or(spec.sheet.thread_depiction)
                        != Some(false)
                }
                DepictionKind::Knurl { .. } => {
                    view.and_then(|v| v.knurl_depiction)
                        .or(spec.sheet.knurl_depiction)
                        != Some(false)
                }
            }
        })
}

/// Presentation of each source requirement, shared by demand, placement and table reservation.
/// The source requirement and complete note text remain unchanged for independent lint.
pub fn manufacturing_for(analysis: &Analysis, spec: &crate::model::Spec) -> Manufacturing {
    use crate::callouts::{BREAK, Style};
    let length = |v| {
        if spec.units.is_mm() {
            g6(v)
        } else {
            spec.units.format(v)
        }
    };
    let mut m = manufacturing(analysis);
    m.tags.clear();
    let candidates = owners(analysis);
    let mut scheduled = Vec::new();
    for o in &mut m.owned {
        if !spec.units.is_mm() {
            match &o.requirement {
                Aspect::Thread(t) => {
                    if let Some(v) = t.minimum_full_thread {
                        let old = format!("{} MIN FULL THREAD", g6(v));
                        let new = format!("{} MIN FULL THREAD", length(v));
                        o.suffix = o.suffix.replace(&old, &new);
                        o.text = o.text.replace(&old, &new);
                    }
                }
                Aspect::Knurl(_) => {
                    o.suffix = spec.units.length_text(&o.suffix);
                    o.text = spec.units.length_text(&o.text);
                }
            }
        }
        let style = crate::callouts::style(spec, &o.owner);
        let owner = candidates.iter().find(|c| c.id == o.owner);
        let diameter = owner.map_or(0.0, |c| c.diameter);
        let shown = depiction_enabled(o, spec);
        let (identity, mut compact, auxiliary) = match &o.requirement {
            Aspect::Thread(t) => {
                let identity = short_designation(t);
                let mut label = identity.clone();
                if t.through {
                    label += " THRU";
                }
                if let Some(depth) = t.minimum_full_thread {
                    label += &if t.internal {
                        format!(" ↧ {}", length(depth))
                    } else {
                        format!("; {} MIN FULL THREAD", length(depth))
                    };
                }
                if !t.internal && t.full_available_length && !shown {
                    label += ", FULL AVAILABLE LENGTH";
                }
                // Blind taps retain the exact source drill dimensions on a second line.
                // Through taps omit only a conventional tapping drill.
                if t.internal
                    && (!t.through || !t.conventional_drill())
                    && let Some(d) = t.drill_diameter
                {
                    label += &format!(" {BREAK} ⌀{}", length(d));
                    if let Some(depth) = t.drill_depth {
                        label += &format!(" ↧ {}", length(depth));
                    } else {
                        label += " THRU";
                    }
                }
                (identity, label, t.drill_point_angle.is_some())
            }
            Aspect::Knurl(k) => {
                let identity = format!("ø{}", length(k.maximum_diameter.unwrap_or(diameter)));
                let mut label = identity.clone();
                if k.maximum_diameter.is_some() {
                    label += " MAX AFTER KNURL";
                }
                label += &format!(
                    " {BREAK} {} KNURL P{}",
                    k.pattern.to_uppercase(),
                    length(k.pitch)
                );
                if k.full_width && !shown {
                    label += " FULL WIDTH";
                }
                // Keep chamfer limits unless the drawing explicitly dimensions the matching
                // chamfers. Complete source text remains in the process note as well.
                if let Some(c) = k.edge_chamfer {
                    let dimensioned = o
                        .depiction
                        .as_ref()
                        .is_some_and(|d| crate::dims::chamfer_stated(analysis, spec, c, d));
                    if !dimensioned {
                        label += &format!(" TO C{} CHAMFERS", length(c));
                    }
                }
                (identity, label, !k.processes.is_empty())
            }
        };
        let full = match &o.requirement {
            Aspect::Thread(t) if t.internal => {
                let mut label = format!("⌀{}", length(diameter));
                if let Some(depth) = t.drill_depth {
                    label += &format!(" ↧ {}", length(depth));
                } else if !t.through {
                    label += " THRU";
                }
                label + " " + &o.suffix
            }
            Aspect::Knurl(k) => format!(
                "ø{} {}",
                length(k.maximum_diameter.unwrap_or(diameter)),
                o.suffix
            ),
            _ => o.suffix.clone(),
        };
        let terms = |s: &str| {
            s.split(' ')
                .filter(|s| !s.is_empty())
                .map(str::to_string)
                .collect::<Vec<_>>()
        };
        let measured = if auxiliary && style != Style::Full {
            match compact.split_once('\n') {
                Some((first, rest)) => {
                    format!("{} SEE MFG 1 \n {}", first.trim_end(), rest.trim_start())
                }
                None => format!("{compact} SEE MFG 1"),
            }
        } else {
            compact.clone()
        };
        let too_long = crate::callouts::lines(&crate::callouts::wrap(&terms(&measured))).len() > 2;
        let note = style == Style::Note || (style == Style::Auto && too_long);
        o.presentation_reason = match style {
            Style::Full => "full wording requested",
            Style::Note => {
                "note form requested; the source-bound manufacturing note states the complete requirement"
            }
            Style::Auto if too_long => {
                "auto selected note form: compact callout would exceed two lines; complete wording in the source-bound manufacturing note"
            }
            _ if auxiliary => {
                "compact identity; secondary manufacturing terms moved to the source-bound manufacturing note"
            }
            Style::Compact => "compact wording requested",
            _ => {
                "compact default; thread/knurl extent retained explicitly or evidenced by the drawing"
            }
        };
        if note || (style != Style::Full && auxiliary) {
            scheduled.push(o.source.clone());
            if note {
                compact = identity;
            }
        }
        o.suffix = if style == Style::Full { full } else { compact };
    }
    scheduled.sort();
    scheduled.dedup();
    for (i, source) in scheduled.into_iter().enumerate() {
        let tag = format!("MFG {}", i + 1);
        for o in m.owned.iter_mut().filter(|o| o.source == source) {
            // Reference on the identity line, keeping blind tap/drill lines separate.
            if let Some((first, rest)) = o.suffix.split_once('\n') {
                o.suffix = format!("{} SEE {tag} \n {}", first.trim_end(), rest.trim_start());
            } else {
                o.suffix += &format!(" SEE {tag}");
            }
        }
        m.tags.push((source, tag));
    }
    m
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn derived_distances_use_infinite_targets_not_their_arbitrary_origins() {
        use crate::pmi::{
            DerivedKind::{Axis, MedianPlane},
            DerivedTarget,
        };
        let target = |kind, point, direction| DerivedTarget {
            kind,
            point,
            direction,
            radius: None,
            faces: vec![],
        };
        let axis = target(Axis, [0.0, 100.0, 0.0], [0.0, 1.0, 0.0]);
        let plane = target(MedianPlane, [3.0, 999.0, 23.0], [-1.0, 0.0, 0.0]);
        let parallel = target(Axis, [3.0, -100.0, 4.0], [0.0, -1.0, 0.0]);
        let plane2 = target(MedianPlane, [8.0, -999.0, -23.0], [1.0, 0.0, 0.0]);
        let distance = |a: &DerivedTarget, b: &DerivedTarget| {
            let s = derived_stations(a, b, [0.0; 3]).unwrap();
            (0..3)
                .map(|i| (s[0][i] - s[1][i]).powi(2))
                .sum::<f64>()
                .sqrt()
        };
        assert_eq!(distance(&axis, &plane), 3.0);
        assert_eq!(distance(&plane, &axis), 3.0);
        assert_eq!(distance(&axis, &parallel), 5.0);
        assert_eq!(distance(&plane, &plane2), 5.0);
        let skew = target(Axis, [0.0, 0.0, 7.0], [1.0, 0.0, 0.0]);
        assert_eq!(distance(&axis, &skew), 7.0);
        let nonparallel = target(MedianPlane, [2.0, 3.0, 4.0], [0.0, 1.0, 0.0]);
        assert!(derived_stations(&plane, &nonparallel, [0.0; 3]).is_err());
        assert!(derived_stations(&axis, &nonparallel, [0.0; 3]).is_err());
    }

    #[test]
    fn cached_lowering_retains_all_principal_view_variants_and_diagnostics() {
        use std::io::Read;
        let compressed = include_bytes!("../tests/fixtures/pmi/step/hw_every_kind.step.gz");
        let mut bytes = Vec::new();
        flate2::read::GzDecoder::new(compressed.as_slice())
            .read_to_end(&mut bytes)
            .unwrap();
        let path = std::env::temp_dir().join(format!(
            "draftwright-pmi-lowering-cache-{}.stp",
            std::process::id()
        ));
        std::fs::write(&path, bytes).unwrap();
        let analysis = crate::model::analyse(&path).unwrap().1;
        std::fs::remove_file(path).unwrap();
        let shown = |mask: usize, name: &str| {
            ["front", "plan", "side"]
                .iter()
                .enumerate()
                .any(|(i, n)| *n == name && mask & (1 << i) != 0)
        };
        let expected: Vec<_> = (0..8)
            .map(|mask| lower_uncached(&analysis, &|name| shown(mask, name)))
            .collect();
        assert!(expected.iter().any(|(items, _)| !items.is_empty()));
        crate::planning::with(&analysis, || {
            for mask in (0..8).chain((0..8).rev()) {
                assert_eq!(lower(&analysis, &|name| shown(mask, name)), expected[mask]);
            }
        });
    }

    /// draftwright's default finish statement (`Ra <value> µm unless otherwise specified`), and
    /// what is not one.
    #[test]
    fn a_default_surface_texture_is_read_as_draftwright_reads_it() {
        for (label, ra) in [
            ("Ra 3.2 µm unless otherwise specified", Some("3.2")),
            ("  RA 0.8um UNLESS  OTHERWISE specified ", Some("0.8")),
            ("Ra 12 μm unless otherwise specified", Some("12")),
            ("Ra 3.2 µm", None),
            ("Ra 3.2 mm unless otherwise specified", None),
            ("Rz 3.2 µm unless otherwise specified", None),
            ("Ra .8 µm unless otherwise specified", None),
            ("Ra 3.2 µm unless otherwise specified; ground", None),
        ] {
            assert_eq!(default_ra(label).as_deref(), ra, "{label:?}");
        }
    }

    /// draftwright's thread and knurl grammars (`_INTERNAL_THREAD`, `_EXTERNAL_THREAD`, `_KNURL`)
    /// and their `callout_suffix` and `callout_text`, against draftwright's own output
    /// (`_thread_requirement`, `_knurl_requirement` on these labels, origin/main 4dfc63f).
    #[test]
    fn requirements_in_words_read_and_print_as_draftwrights() {
        let thread = |kind: &str, label: &str| prose_thread(kind, label).map(|t| t.suffix());
        let cases = [
            (
                "internal_thread",
                "M2 x 0.4-6H RH, 2.5 mm minimum full thread; DIA 1.6 tapping drill x 4 mm full-diameter depth",
                Ok("M2 x 0.4-6H RH; 2.5 MIN FULL THREAD"),
            ),
            (
                "internal_thread",
                "M2 x 0.4-6H RH, 2.5 mm minimum full thread; DIA 1.6 tapping drill x 4 mm full-diameter depth; conventional 118 degree drill point",
                Ok("M2 x 0.4-6H RH; 2.5 MIN FULL THREAD; 118° CONVENTIONAL DRILL POINT"),
            ),
            (
                "internal_thread",
                "M5 x 0.8-6H RH, full thread through; DIA 4.2 tapping drill through",
                Ok("M5 x 0.8-6H RH; THRU"),
            ),
            (
                "internal_thread",
                "  m2x0.4-6h lh,2.5mm minimum full thread;dia 1.6 tapping drill x 4mm full-diameter depth ",
                Ok("m2x0.4-6h lh; 2.5 MIN FULL THREAD"),
            ),
            (
                "internal_thread",
                "M2 x 0.4-6H RH, 5 mm minimum full thread; DIA 1.6 tapping drill x 4 mm full-diameter depth",
                Err("thread minimum full thread cannot exceed drill depth"),
            ),
            (
                "internal_thread",
                "M5 thread",
                Err("unsupported internal thread syntax"),
            ),
            (
                "external_thread",
                "M5 x 0.8-6g RH, full available length on nominal DIA 5 region",
                Ok("M5 x 0.8-6g RH, FULL AVAILABLE LENGTH"),
            ),
            (
                "external_thread",
                "M5 x 0.8-6g RH, full available length on nominal DIA 6 region",
                Err("external thread designation and nominal region disagree"),
            ),
        ];
        for (kind, label, want) in cases {
            assert_eq!(
                thread(kind, label),
                want.map(str::to_string).map_err(str::to_string),
                "{label:?}"
            );
        }
        let k = prose_knurl(
            "Straight knurl, 1 mm pitch, full width between C0.3 chamfers, DIA 6.25 mm maximum after knurling; cut or formed process permitted",
        )
        .unwrap();
        assert_eq!(
            k.suffix(),
            "MAX AFTER KNURL; STRAIGHT KNURL P1 FULL WIDTH TO C0.3 CHAMFERS; CUT/FORMED PERMITTED"
        );
        assert_eq!(
            k.text(),
            "ø6.25 MAX AFTER KNURL; STRAIGHT KNURL P1 FULL WIDTH TO C0.3 CHAMFERS; CUT/FORMED PERMITTED"
        );
        assert_eq!(
            wrap(&k.text(), SCHEDULE_WRAP),
            [
                "ø6.25 MAX AFTER KNURL; STRAIGHT KNURL P1",
                "FULL WIDTH TO C0.3 CHAMFERS; CUT/FORMED",
                "PERMITTED"
            ]
        );
        assert_eq!(
            prose_knurl("Diamond knurl, 1 mm pitch, DIA 6 mm maximum after knurling"),
            Err("unsupported knurl requirement syntax".to_string())
        );
    }

    /// The table is used for two or more requirements one of whose suffixes is over 52
    /// characters, tagged in their source IDs' order; a callout then prints its tag.
    #[test]
    fn long_requirements_go_to_the_table_in_source_order() {
        let owned = |source: &str, suffix: &str| Owned {
            presentation_reason: "source wording",
            requirement: Aspect::Knurl(Knurl {
                pattern: "straight".into(),
                pitch: 1.0,
                full_width: false,
                edge_chamfer: None,
                maximum_diameter: None,
                processes: Vec::new(),
            }),
            id: format!("pmi:manufacturing_requirement.internal_thread:t={source}"),
            source: source.into(),
            owner: format!("f:{source}"),
            aspect: "thread",
            suffix: suffix.into(),
            text: suffix.into(),
            through: false,
            depiction: None,
        };
        let mut m = Manufacturing {
            owned: vec![
                owned("manufacturing_requirement:#9", "M2 x 0.4-6H RH"),
                owned("manufacturing_requirement:#10", &"X".repeat(53)),
            ],
            ..Default::default()
        };
        assert!(m.rows().is_empty());
        assert_eq!(m.printed(&m.owned[0].clone()), "M2 x 0.4-6H RH");
        // As draftwright sorts them: by source ID, as text.
        m.tags = vec![
            ("manufacturing_requirement:#10".into(), "MFG 1".into()),
            ("manufacturing_requirement:#9".into(), "MFG 2".into()),
        ];
        assert_eq!(m.printed(&m.owned[0].clone()), "SEE MFG 2");
        assert_eq!(
            m.terms("f:manufacturing_requirement:#9").0,
            ["SEE", "MFG", "2"]
        );
        assert_eq!(m.rows()[0], ["REF", "MANUFACTURING REQUIREMENT"]);
        assert_eq!(m.rows()[1][0], "MFG 1");
    }
}
