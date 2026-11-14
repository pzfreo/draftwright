//! The part's AP242 dimensions (sizes and locations, toleranced or basic) on the sheet: stage 2b
//! of drawing PMI, draftwright's `model/pmi_lowering.py` (`lower_ap242_hole_tolerances`,
//! `lower_ap242_nominal_diameters`, `lower_ap242_nominal_step_lengths`) and
//! `annotations/_pmi_dimensions.py` (`render_pmi`). Each dimension record of the Analysis's PMI,
//! with where its faces put it ([`crate::pmi_lowering::dimension_geometry`]), either:
//!
//! - **annotates a model dimension**, which then states it (draftwright's rule: a source
//!   requirement on a quantity a recognised feature's dimension already states is given to that
//!   dimension, so the quantity is stated once): a bore diameter whose cylinders are the holes a
//!   callout calls out (their axis lines, their axis and their diameter) gives the callout its
//!   tolerance (`6× ⌀10 +0.09 -0.0 THRU`; a limit pair as its deviations, `⌀35 ±0.2`), or, with
//!   no tolerance, is stated by it; a plain length between a turned step's two shoulders is
//!   stated by the step's length;
//! - **stands beside them**, drawn as its own dimension (draftwright's standalone fallback): a
//!   length between its two proven stations (aligned for an oblique span in a principal plane),
//!   along x, y or z in the front view (x and z; the plan
//!   when only the plan's witness is proven), or the side then the plan (y); a diameter by a
//!   leader to its cylinder where it is seen end-on; a radius by a leader to a proven point of
//!   its arc; an angle about the vertex of its two planar sides, in the view that shows it true
//!   ([`super::angular`]). Its label is the record's (`⌀62 +0.03/0`, `30`), an angle's with its unit
//!   (`60° ±0.5°`, [`degrees`]); a basic dimension's is boxed. It is an ordinary planned dimension, placed with the others;
//! - **or is not drawn, and says why**: a record whose reading left blockers, whose faces lie in
//!   several placements of the part, or whose faces prove no witness (`pmi_not_lowered`, with
//!   draftwright's reasons); a kind the port does not draw yet: a diameter or radius not along
//!   x, y or z, a bolt circle whose holes carry
//!   different requirements (draftwright splits it; the port splits count groups, grids and
//!   lines), a counted turned diameter a record names only one of (`pmi_dimensions_not_ported`).
//!
//! Derived targets use the kernel-resolved axes and planes; unresolved supplemental-only
//! geometry stays refused. A stated ISO 286 hole class
//! without limits is printed only on a uniquely matched recognised bore owner, as the class and
//! optional stacked deviations from draftwright's table; one outside the table is refused.

use std::collections::{BTreeMap, BTreeSet};

use super::turned::Turned;
use super::{Context, Measurement, P3, Problem, Shape, Want, end_on, fmt, tolerance, want_id};
use crate::model::{Analysis, Spec};
use crate::pmi::{Category, Identities, Reading, Record};
use crate::pmi_lowering::Cylinder;

/// What the plan keeps of a model item's PMI tolerance, by its ID (which routing does not
/// change): the band the PMI put on its label, and the sources it came
/// from, so a spec's own tolerance can take its place. (Every item that states a PMI dimension
/// claims it: a measurement of feature `pmi:<identity>`, its item ID, [`feature`].)
#[derive(Clone, Debug, Default)]
pub(super) struct Note {
    /// The band as printed.
    pub band: String,
    /// The item's terms before the band.
    pub before: Vec<String>,
    /// The sources whose tolerance it is.
    pub banded: Vec<String>,
    /// Their claims' features ([`feature`]).
    pub claimed: Vec<String>,
    /// The clause the band added to the item's basis.
    pub clause: String,
    /// A fit's limits term, which the sheet drops when it hides fit limits.
    pub limits: Option<String>,
}

/// A record's tolerance: its signed upper and lower deviations, and its limits when it states
/// them.
#[derive(Clone, Copy, Debug, PartialEq)]
struct Req {
    upper: f64,
    lower: f64,
    limits: Option<(f64, f64)>,
    fit: Option<([u8; 2], u8)>,
}

/// A requirement as a comparable key (its numbers' bits).
type Key = (u64, u64, u64, u64, Option<([u8; 2], u8)>);

impl Req {
    fn key(&self) -> Key {
        let (lo, hi) = self
            .limits
            .map_or((u64::MAX, u64::MAX), |(l, h)| (l.to_bits(), h.to_bits()));
        (self.upper.to_bits(), self.lower.to_bits(), lo, hi, self.fit)
    }

    /// Puts it on *w*'s label: a symmetric band as `±T`, an asymmetric limit pair as the limits
    /// in place of the value, any other as `+U -L` (draftwright's `bore_callout_value` and
    /// `_fmt_tolerance`). The note to undo it, or none when the label states no value.
    fn print(&self, w: &mut Want) -> Option<Note> {
        let before = w.terms.clone();
        let units = if matches!(w.shape, Shape::Angular { .. }) {
            crate::units::Units::Mm
        } else {
            w.units
        };
        let symmetric = self.upper > 0.0 && self.upper == -self.lower;
        let mut limits = None;
        let (ok, band) = if let Some((letters, grade)) = self.fit {
            let class = format!(
                "{}{grade}",
                std::str::from_utf8(&letters)
                    // Invariant: Fit letters are parsed as ASCII alphabetic characters and padded only with NUL.
                    .unwrap()
                    .trim_end_matches('\0')
            );
            let term = if units.is_mm() {
                tolerance::limits_term((self.lower, self.upper))
            } else {
                format!("({})", units.deviations(self.upper, self.lower))
            };
            let ok = tolerance::insert(w, &term) && tolerance::insert(w, &class);
            limits = Some(term);
            (ok, tolerance::with_limits(&class, (self.lower, self.upper)))
        } else {
            match self.limits {
                Some((lo, hi)) if !symmetric => {
                    (tolerance::limits(w, lo, hi), format!("{lo} - {hi}"))
                }
                _ => {
                    let text = if units.is_mm() {
                        tolerance::band(self.upper, self.lower)
                    } else if symmetric {
                        format!("±{}", units.magnitude(self.upper))
                    } else {
                        units.deviations(self.upper, self.lower)
                    };
                    (tolerance::insert(w, &text), text)
                }
            }
        };
        ok.then_some(Note {
            band,
            before,
            banded: Vec::new(),
            claimed: Vec::new(),
            clause: String::new(),
            limits,
        })
    }
}

/// The feature a PMI dimension's claim names: the record's item ID, `pmi:<identity>`, never its
/// Part 21 instance. A record whose identity another shares has none, and is not lowered
/// ([`lower`]): its shared identity stands in while that is decided.
pub(super) fn feature(ids: &Identities, source: &str) -> String {
    match (ids.ids.get(source), ids.shared.get(source)) {
        (Some(id), _) => id.clone(),
        (None, Some((identity, _))) => format!("pmi:{identity}"),
        // Invariant: The PMI reader assigns a semantic or presentation identity to every record.
        (None, None) => unreachable!("every record of the reading has an identity"),
    }
}

/// Plane spans touching a required profile corner may leave the nearer side of
/// their proven faces, freeing the corner for its angle. Their datum is unchanged.
pub(super) fn local_plane_spans(a: &Analysis, corners: &[P3]) -> BTreeSet<String> {
    if corners.is_empty() {
        return BTreeSet::new();
    }
    let Some(Reading::Read(report)) = &a.pmi else {
        return BTreeSet::new();
    };
    let ids = report.identities();
    report
        .records
        .iter()
        .filter(|r| {
            r.category == Category::Dimension
                && matches!(r.kind.as_str(), "linear" | "thickness")
                && r.face_groups.len() == 2
                && r.face_groups.iter().all(|fs| {
                    !fs.is_empty()
                        && fs.iter().all(|&f| {
                            a.face_attachments
                                .get(f)
                                .is_some_and(|f| f.surface.as_deref() == Some("plane"))
                        })
                })
                && r.geometry
                    .as_ref()
                    .is_some_and(|g| g.axis.is_some() && g.reasons.is_empty())
                && r.face_groups.iter().flatten().any(|&f| {
                    a.face_attachments[f].bounds.is_some_and(|bounds| {
                        corners.iter().any(|p| {
                            (0..3).all(|k| p[k] >= bounds[k] - 1e-6 && p[k] <= bounds[k + 3] + 1e-6)
                        })
                    })
                })
        })
        .map(|r| feature(&ids, &r.source_id))
        .collect()
}

/// Whether a planned item is a basic PMI dimension: its label is then boxed.
pub(crate) fn basic(measurements: &[Measurement]) -> bool {
    measurements
        .iter()
        .any(|m| m.feature.starts_with("pmi:") && m.parameter.ends_with(".basic"))
}

/// A claim on a PMI dimension: its feature, its kind as the parameter (`.basic` for a basic
/// one), its value.
fn claim(ids: &Identities, r: &Record, value: f64) -> Measurement {
    Measurement {
        feature: feature(ids, &r.source_id),
        parameter: if r.basic {
            format!("{}.basic", r.kind)
        } else {
            r.kind.clone()
        },
        value,
    }
}

/// What [`lower`] returns: the standalone items, the problems, and the notes.
pub(super) type Lowered = (Vec<Want>, Vec<Problem>, BTreeMap<String, Note>);

/// Why a record is not drawn: its code and its reason.
struct Refusal(&'static str, String);

fn not_lowered(why: impl Into<String>) -> Refusal {
    Refusal("pmi_not_lowered", why.into())
}

fn not_ported(why: impl Into<String>) -> Refusal {
    Refusal("pmi_dimensions_not_ported", why.into())
}

/// A record's tolerance: its upper and lower deviations, signed, from its deviations or from its
/// limits less its nominal (in decimal, as draftwright's `_requirement` computes them), or none
/// when it states no tolerance.
fn requirement(r: &Record, value: f64) -> Option<Req> {
    match (r.lower_bound, r.upper_bound, r.lower_tol, r.upper_tol) {
        (Some(lo), Some(hi), _, _) => Some(Req {
            upper: decimal_sub(hi, value),
            lower: decimal_sub(lo, value),
            limits: Some((lo, hi)),
            fit: None,
        }),
        (_, _, lo, hi) if lo.is_some() || hi.is_some() => Some(Req {
            upper: hi.unwrap_or(0.0),
            lower: lo.unwrap_or(0.0),
            limits: None,
            fit: None,
        }),
        _ => {
            let fit = symbolic_fit(r)?;
            let (lower, upper) = tolerance::deviations(r.fit.as_deref()?, value)?;
            Some(Req {
                upper,
                lower,
                limits: None,
                fit: Some(fit),
            })
        }
    }
}

// A stated ISO class without limits: the class, whose limits the plan prints from the table.
// A hole class is upper case (`H7`), a shaft class lower case (`g6`, `js6`); mixed case is
// neither (ISO 286's letters are case-significant).
fn symbolic_fit(r: &Record) -> Option<([u8; 2], u8)> {
    let code = r.fit.as_deref()?;
    let (letters, grade) = tolerance::fit(code).ok()?;
    let mut chars = [0; 2];
    if letters.len() > chars.len()
        || !(letters.bytes().all(|b| b.is_ascii_uppercase())
            || letters.bytes().all(|b| b.is_ascii_lowercase()))
    {
        return None;
    }
    chars[..letters.len()].copy_from_slice(letters.as_bytes());
    Some((chars, grade as u8))
}

/// Whether a symbolic fit's class is a shaft class (lower case, `g6`).
fn shaft_class(r: &Record) -> bool {
    symbolic_fit(r).is_some_and(|(letters, _)| letters[0].is_ascii_lowercase())
}

/// Whether the record is a diameter stated by an ISO 286 class alone, which the plan prints
/// with its limits from the table: a hole class on a bore's internal cylinders, or a shaft
/// class on a turned diameter's external ones (#30). A class on the other kind of cylinder (a
/// shaft class on a bore) is not one, and stays undrawn with its reading's blocker.
pub(super) fn symbolic_bore(r: &Record) -> bool {
    let shaft = shaft_class(r);
    r.kind == "diameter"
        && r.lower_tol.is_none()
        && r.upper_tol.is_none()
        && r.lower_bound.is_none()
        && r.upper_bound.is_none()
        && symbolic_fit(r).is_some()
        && r.value
            .zip(r.fit.as_deref())
            .is_some_and(|(v, code)| tolerance::deviations(code, v).is_some())
        && r.geometry.as_ref().is_some_and(|g| {
            g.reasons.is_empty()
                && !g.cylinders.is_empty()
                && g.cylinders
                    .iter()
                    .all(|c| c.internal != shaft && (!shaft || c.principal().is_some()))
        })
}

/// *a* − *b* in decimal, as Python's `Decimal(str(a)) - Decimal(str(b))`: each from its
/// shortest round-trip digits, so 35 − 34.8 is 0.2, not 0.20000000000000284.
fn decimal_sub(a: f64, b: f64) -> f64 {
    let parse = |v: f64| -> Option<(i128, u32)> {
        let s = format!("{v}");
        let (neg, s) = s
            .strip_prefix('-')
            .map_or((false, s.as_str()), |t| (true, t));
        let (whole, frac) = s.split_once('.').unwrap_or((s, ""));
        let digits: i128 = format!("{whole}{frac}").parse().ok()?;
        Some((if neg { -digits } else { digits }, frac.len() as u32))
    };
    match (parse(a), parse(b)) {
        (Some((x, p)), Some((y, q))) if p.max(q) <= 18 => {
            let scale = p.max(q);
            let d = x * 10i128.pow(scale - p) - y * 10i128.pow(scale - q);
            format!("{}e-{scale}", d).parse().unwrap_or(a - b)
        }
        _ => a - b,
    }
}

/// The record's label as callout terms: its words, a leading `ø` (draftwright's spelling, which
/// [`crate::pmi`]'s labels no longer use) as the port's `⌀`.
fn terms(label: &str) -> Vec<String> {
    label
        .split_whitespace()
        .map(|t| match t.strip_prefix('ø') {
            Some(rest) => format!("⌀{rest}"),
            None => t.to_string(),
        })
        .collect()
}

/// An angle's terms with their unit: the record's label states degrees by its kind alone
/// (`60 ±0.5`), but on the sheet a bare number reads as millimetres, so every value, its
/// deviations and limits included, carries `°` (ISO 129-1: `60° ±0.5°`).
fn degrees(terms: &[String]) -> Vec<String> {
    terms
        .iter()
        .map(|t| {
            t.split('/')
                .map(|v| {
                    // After the last digit, so a reference value `(60)` reads `(60°)`.
                    match v.rfind(|c: char| c.is_ascii_digit()) {
                        Some(i) if v[i + 1..].chars().all(|c| matches!(c, ')' | ']')) => {
                            format!("{}°{}", &v[..=i], &v[i + 1..])
                        }
                        _ => v.to_string(),
                    }
                })
                .collect::<Vec<_>>()
                .join("/")
        })
        .collect()
}

/// The world axis an `X`/`Y`/`Z` letter names.
fn axis(letter: Option<&str>) -> Option<usize> {
    ["X", "Y", "Z"].iter().position(|x| Some(*x) == letter)
}

/// A callout's holes, each its feature, centre and diameter: the bore diameters it states, with
/// its sites (the planner keeps them in the same order).
fn holes_of(w: &Want) -> Vec<(String, P3, f64)> {
    let Shape::Callout { sites, .. } = &w.shape else {
        return Vec::new();
    };
    let bores: Vec<&Measurement> = w
        .measurements
        .iter()
        .filter(|m| m.parameter == "bore.diameter")
        .collect();
    if bores.len() != sites.len() {
        return Vec::new();
    }
    bores
        .iter()
        .zip(sites)
        .map(|(m, (c, _))| (m.feature.clone(), *c, m.value))
        .collect()
}

/// The callout (by index into *wants*) whose holes the record's cylinders are, and which of its
/// holes: a hole along the record's axis whose diameter is its nominal (to 1e-4 mm) and whose
/// centre lies on one of its cylinders' axis lines (to 0.01 mm across the axis), draftwright's
/// `_internal_member_matches` and `_hole_tolerance_proposal`. `None` when no callout has such a
/// hole, or when the holes are in several callouts (draftwright's ambiguous correlation): the
/// record then stands beside them.
fn hole_callout(
    wants: &[Want],
    cylinders: &[Cylinder],
    k: usize,
    value: f64,
) -> Option<(usize, BTreeSet<usize>)> {
    let mut found: Vec<(usize, BTreeSet<usize>)> = Vec::new();
    for (i, w) in wants.iter().enumerate() {
        if w.id.as_str().rsplit_once('/').map(|(_, p)| p) != Some("bore.diameter")
            || w.views.first() != Some(&end_on(k))
        {
            continue;
        }
        let members: BTreeSet<usize> = holes_of(w)
            .iter()
            .enumerate()
            .filter(|(_, (_, c, d))| {
                (d - value).abs() <= 1e-4
                    && cylinders.iter().any(|cy| {
                        cy.principal() == Some(k)
                            && (0..3).all(|j| j == k || (cy.origin[j] - c[j]).abs() <= 0.01)
                    })
            })
            .map(|(m, _)| m)
            .collect();
        if !members.is_empty() {
            found.push((i, members));
        }
    }
    (found.len() == 1).then(|| found.remove(0))
}

/// Inclined bores already have a callout to their elliptical rim. Match one by
/// its native faces, full axis line and diameter, rather than treating its
/// approximate end-on view as evidence of a principal cylinder direction.
fn inclined_hole_callout(
    a: &Analysis,
    wants: &[Want],
    r: &Record,
    cylinders: &[Cylinder],
    value: f64,
) -> Option<(usize, BTreeSet<usize>)> {
    let frame = crate::bridge::holes::frame(a);
    let mut found = Vec::new();
    for (i, w) in wants.iter().enumerate() {
        let mut members = BTreeSet::new();
        let mut faces: BTreeSet<usize> = BTreeSet::new();
        for (m, (id, _, diameter)) in holes_of(w).iter().enumerate() {
            let Some(f) = a.features.iter().find(|f| f.id == *id && f.kind == "hole") else {
                continue;
            };
            if !f.faces.iter().any(|f| r.faces.contains(f)) || (diameter - value).abs() > 1e-4 {
                continue;
            }
            let Ok(hole) = crate::bridge::holes::Hole::read(f, &frame) else {
                continue;
            };
            let matches = cylinders.iter().any(|c| {
                let dot: f64 = (0..3).map(|k| hole.direction[k] * c.direction[k]).sum();
                let delta = [0, 1, 2].map(|k| hole.at[k] - c.origin[k]);
                let along: f64 = (0..3).map(|k| delta[k] * c.direction[k]).sum();
                (dot.abs() - 1.).abs() < 1e-9
                    && (0..3).all(|k| (delta[k] - along * c.direction[k]).abs() < 0.01)
            });
            if matches {
                members.insert(m);
                faces.extend(&f.faces);
            }
        }
        if !members.is_empty() && !r.faces.is_empty() && r.faces.iter().all(|f| faces.contains(f)) {
            found.push((i, members));
        }
    }
    (found.len() == 1).then(|| found.remove(0))
}

/// The turned step length (by index into *wants*) a plain length states: one whose two ends lie
/// at the record's stations along its axis (to 0.01 mm) and whose value is its nominal
/// (draftwright's `lower_ap242_nominal_step_lengths`, which needs exactly one such step).
fn step_length(wants: &[Want], stations: &[P3], k: usize, value: f64) -> Option<usize> {
    let found: Vec<usize> = wants
        .iter()
        .enumerate()
        .filter(|(_, w)| {
            let Shape::Linear { a, b, axis, .. } = &w.shape else {
                return false;
            };
            let ends = |p: f64, q: f64| {
                ((p - stations[0][k]).abs() <= 0.01 && (q - stations[1][k]).abs() <= 0.01)
                    || ((q - stations[0][k]).abs() <= 0.01 && (p - stations[1][k]).abs() <= 0.01)
            };
            *axis == k
                && w.id.as_str().ends_with("/step.length")
                && (w.value - value).abs() <= 1e-6
                && ends(a[k], b[k])
        })
        .map(|(i, _)| i)
        .collect();
    (found.len() == 1).then(|| found[0])
}

/// Reuse a native centre ordinate only when it states the proven compound
/// endpoint in full. This adds the authored basic box without a second layout
/// item; a matching number or one coincident projected coordinate is insufficient.
fn compound_bore_location(a: &Analysis, wants: &[Want], r: &Record, k: usize) -> Option<usize> {
    let g = r.geometry.as_ref()?;
    if g.sites.len() != 2 || g.stations.len() != 2 || r.face_groups.len() != 2 {
        return None;
    }
    let side = r.face_groups.iter().position(|group| {
        group.len() > 1
            && group.iter().all(|&f| {
                a.face_attachments
                    .get(f)
                    .is_some_and(|f| f.surface.as_deref() == Some("cylinder"))
            })
    })?;
    let group = &r.face_groups[side];
    let bore_axis = a.face_attachments[*group.first()?].exact_axis?;
    let found: Vec<_> = wants
        .iter()
        .enumerate()
        .filter(|(_, w)| {
            let Shape::Linear {
                a: p, b: q, axis, ..
            } = &w.shape
            else {
                return false;
            };
            let owns = |id: &str| {
                let faces = a
                    .features
                    .iter()
                    .find(|f| f.id == id)
                    .map(|f| &f.faces)
                    .or_else(|| {
                        a.recognition
                            .as_ref()?
                            .patterns
                            .iter()
                            .find(|p| p.id == id)
                            .map(|p| &p.faces)
                    });
                faces.is_some_and(|fs| !fs.is_empty() && fs.iter().all(|f| group.contains(f)))
                    || a.recognition.iter().flat_map(|r| &r.patterns).any(|p| {
                        p.id == id
                            && crate::pmi_lowering::empty_centre_pattern_support(
                                a,
                                group,
                                &p.faces,
                                k,
                                g.stations[side][k],
                            )
                    })
            };
            *axis == k
                && !w.measurements.is_empty()
                && w.measurements
                    .iter()
                    .all(|m| m.parameter.starts_with("location.") && owns(&m.feature))
                && (0..3).all(|i| i == bore_axis || (q[i] - g.stations[side][i]).abs() < 1e-6)
                && (p[k] - g.stations[1 - side][k]).abs() < 1e-6
                && (w.value - r.value.unwrap_or(f64::NAN)).abs() < 1e-6
        })
        .map(|(i, _)| i)
        .collect();
    (found.len() == 1).then(|| found[0])
}

/// A measured fallback can use an existing native size on precisely the same
/// two support planes. Retain its local witnesses instead of adding a second
/// dimension in an arbitrary principal view.
fn measured_plane_size(a: &Analysis, wants: &[Want], r: &Record, k: usize) -> Option<usize> {
    if !crate::pmi::nominal_measured(r)
        || r.basic
        || r.face_groups.len() != 2
        || r.faces.is_empty()
        || r.faces.iter().any(|&f| {
            a.face_attachments
                .get(f)
                .is_none_or(|f| f.surface.as_deref() != Some("plane") || f.axis != Some(k))
        })
    {
        return None;
    }
    let g = r.geometry.as_ref()?;
    let [p, q] = g.stations.as_slice() else {
        return None;
    };
    let found: Vec<_> = wants
        .iter()
        .enumerate()
        .filter(|(_, w)| {
            let proven = |lo: f64, hi: f64| {
                (lo - p[k].min(q[k])).abs() < 1e-6 && (hi - p[k].max(q[k])).abs() < 1e-6
            };
            let owns = |f: &crate::bridge::Feature| r.faces.iter().all(|i| f.faces.contains(i));
            match &w.shape {
                Shape::Linear {
                    a: x, b: y, axis, ..
                } if *axis == k => {
                    proven(x[k].min(y[k]), x[k].max(y[k]))
                        && w.measurements.iter().any(|m| {
                            !m.parameter.starts_with("location")
                                && a.features
                                    .iter()
                                    .find(|f| f.id == m.feature)
                                    .is_some_and(owns)
                        })
                }
                _ => false,
            }
        })
        .map(|(i, _)| i)
        .collect();
    (found.len() == 1).then(|| found[0])
}

/// Whether a plain diameter's label is the size as a callout prints it: `⌀6`, or `⌀6 THRU`
/// (draftwright's `_canonical_nominal_diameter_label`, which spells it `ø6`).
fn canonical(r: &Record, value: f64, units: crate::units::Units) -> bool {
    let fmt = |v| units.format(v);
    let size = format!("⌀{}", fmt(value));
    let label = qualifier(r).map_or(r.label.as_str(), |q| {
        // Invariant: q was selected by checking this same label suffix.
        r.label.strip_suffix(q).unwrap().trim_end()
    });
    label == size || label == format!("{size} THRU")
}

fn qualifier(r: &Record) -> Option<&'static str> {
    ["MAX", "MIN", "AVG"]
        .into_iter()
        .find(|q| r.label.ends_with(&format!(" {q}")))
}

/// Whether a site of a planned item lies on cylinder *c*: a circle (centre, radius) on its axis
/// with its radius, or a point (radius 0) on its surface within its extent, to 0.01 mm.
fn on_cylinder(site: &(P3, f64), c: &Cylinder) -> bool {
    let (p, r) = site;
    let d: P3 = [0, 1, 2].map(|i| p[i] - c.origin[i]);
    let t = d[0] * c.direction[0] + d[1] * c.direction[1] + d[2] * c.direction[2];
    let off = [0, 1, 2].map(|i| d[i] - t * c.direction[i]);
    let across = (off[0] * off[0] + off[1] * off[1] + off[2] * off[2]).sqrt();
    if *r > 0.0 {
        across <= 0.01 && (r - c.radius).abs() <= 0.01
    } else {
        (across - c.radius).abs() <= 0.01 && c.interval[0] - 0.01 <= t && t <= c.interval[1] + 0.01
    }
}

/// The turned step's or boss's diameter (by index into *wants*) whose sites all lie on the
/// record's external cylinders and whose diameter is its nominal (draftwright's
/// `_external_owner_matches`: the cylinder's axis line, radius and extent); `None` when no item
/// is, or several are (draftwright's ambiguous ownership: the record stands beside them). An
/// item only some of whose sites the cylinders name states a size the record does not cover
/// alone: refused.
fn external_owner(
    wants: &[Want],
    cylinders: &[Cylinder],
    value: f64,
    turned: Option<&Turned>,
) -> Option<Result<usize, Refusal>> {
    let mut whole = Vec::new();
    let mut partly = Vec::new();
    for (i, w) in wants.iter().enumerate() {
        let parameter = w.id.as_str().rsplit_once('/').map(|(_, p)| p);
        // A part turned whole: its overall diameter, about the turning axis.
        if parameter == Some("od.diameter")
            && let (Some(t), Some(axis)) = (turned, w.turning)
            && (w.value - value).abs() <= 0.01
            && cylinders.iter().all(|c| {
                c.principal() == Some(axis)
                    && (0..3).all(|j| j == axis || (c.origin[j] - t.on[j]).abs() <= 0.01)
            })
        {
            whole.push(i);
            continue;
        }
        if !matches!(parameter, Some("step.diameter" | "boss.diameter"))
            || (w.value - value).abs() > 0.01
        {
            continue;
        }
        let Shape::Callout { sites, .. } = &w.shape else {
            continue;
        };
        let named = sites
            .iter()
            .filter(|s| cylinders.iter().any(|c| on_cylinder(s, c)))
            .count();
        if named == sites.len() && named > 0 {
            whole.push(i);
        } else if named > 0 {
            partly.push(i);
        }
    }
    match (whole.len(), partly.len()) {
        (1, 0) => Some(Ok(whole[0])),
        (0, 1) => Some(Err(not_ported(format!(
            "its cylinder is one of those {} states together; draftwright gives it to its own step, which the port does not split yet",
            wants[partly[0]].id
        )))),
        _ => None,
    }
}

/// A callout's holes of one requirement: its band's bits (upper, lower), and the holes.
type Group = (Option<Key>, Vec<usize>);

/// What one record becomes.
enum Outcome {
    Refused(Refusal),
    /// Drawn on its own.
    Standalone(Want),
    /// Stated by a callout: its holes, and the band it puts on it.
    Hole(usize, BTreeSet<usize>, Option<Req>),
    /// Stated by another item as it is.
    Stated(usize, &'static str),
    /// Stated by a turned step's or boss's diameter, which takes its band.
    Band(usize, Req),
    /// A Specify general-tolerance dimension the planners' own item conveys (#265): that
    /// item's basis says so.
    Conveyed(usize, String),
    /// A Specify general-tolerance dimension no planners' item conveys: one more size the
    /// drawing states, planned as the planners' own ([`general`], #265).
    General(Want),
}

/// The parameter of a Specify general-tolerance dimension planned as draftwright's own.
pub(crate) const GENERAL_SIZE: &str = "general_size.length";

/// The feature of a Specify general-tolerance dimension planned as draftwright's own, for the
/// record whose identity is `id` (`pmi:<identity>`).
pub(crate) fn general_feature(id: &str) -> String {
    format!("specify_general:{}", id.strip_prefix("pmi:").unwrap_or(id))
}

/// What a planners' item's basis says when it conveys the Specify general-tolerance dimension
/// `source_id` (#265), so the PMI report can credit that item ([`conveys`]) without a claim.
fn conveyed_note(how: &str) -> String {
    format!("; it conveys Specify's general-tolerance {how}, drawn as draftwright plans it (#265)")
}

/// Whether a planned item's basis says it conveys the Specify general-tolerance dimension
/// `source_id` ([`conveyed_note`]).
pub(crate) fn conveys(basis: &str, source_id: &str) -> bool {
    basis.contains(&format!(
        "it conveys Specify's general-tolerance dimension {source_id} "
    ))
}

/// A Specify general-tolerance dimension no planners' item conveys, as an ordinary planned
/// dimension (#265): the same span and views as *own* would have, but its feature is
/// `specify_general:<identity>`, never `pmi:`, so it takes no PMI precedence or placement
/// priority and is not the file's requirement downstream. Candidates, lanes, redundancy and
/// drop findings treat it as any planned dimension. Its basis names the record's source, which
/// carries its characteristic number and Specify's authorship ([`crate::characteristics`]).
fn general(report: &crate::pmi::Report, r: &Record, own: Want, units: crate::units::Units) -> Want {
    let fmt = |v| units.format(v);
    let feature = general_feature(&own.measurements[0].feature);
    let reason = report
        .sources
        .iter()
        .find(|s| s.id == r.source_id)
        .map_or("", |s| s.reason.trim());
    Want {
        units: Default::default(),
        id: want_id(&feature, GENERAL_SIZE),
        measurements: vec![super::Measurement {
            feature,
            parameter: GENERAL_SIZE.into(),
            value: own.value,
        }],
        basis: format!(
            "Specify's general-tolerance dimension {} ({}): no planned item states this size, so it is planned as draftwright's own dimension, under the general tolerance (#265); {reason}",
            r.source_id,
            fmt(own.value)
        ),
        ..own
    }
}

/// The planners' item that conveys a Specify general-tolerance dimension (#265), which would
/// otherwise stand as *own*: an engine item of the same span, else the engine item that
/// dimensions the recognised feature its faces belong to, when every face is either such a
/// feature's or the part's stock (an overall size between stock faces goes to the envelope
/// item along its axis). The stop-gap until one dimension planner serves Specify and
/// Draftwright (#264).
fn conveyed(
    a: &crate::model::Analysis,
    wants: &[Want],
    r: &Record,
    own: &Want,
) -> Option<(usize, String)> {
    let Shape::Linear {
        a: p,
        b: q,
        axis: k,
        ..
    } = own.shape
    else {
        return None;
    };
    let span = |a: &P3, b: &P3, k: usize| (a[k].min(b[k]), a[k].max(b[k]));
    let (lo, hi) = span(&p, &q, k);
    let engine = |w: &Want| !super::pmi_policy::authored(w);
    if let Some(i) = wants.iter().position(|w| {
        engine(w)
            && matches!(&w.shape, Shape::Linear { a, b, axis, .. } if *axis == k && {
                let (l, h) = span(a, b, k);
                (l - lo).abs() < 1e-6 && (h - hi).abs() < 1e-6
            })
    }) {
        return Some((i, format!("dimension {} of the same span", r.source_id)));
    }
    let stock = |f: &usize| a.stock_faces.contains(f);
    let owns = |w: &Want, f: usize| {
        w.measurements.iter().any(|m| {
            a.features
                .iter()
                .any(|feature| feature.id == m.feature && feature.faces.contains(&f))
        })
    };
    let rest: Vec<usize> = r.faces.iter().copied().filter(|f| !stock(f)).collect();
    if !rest
        .iter()
        .all(|&f| wants.iter().any(|w| engine(w) && owns(w, f)))
    {
        return None;
    }
    let i = match rest.first() {
        Some(&f) => wants.iter().position(|w| engine(w) && owns(w, f))?,
        None => wants.iter().position(|w| {
            engine(w)
                && w.measurements.iter().any(|m| m.feature == "envelope")
                && matches!(&w.shape, Shape::Linear { axis, .. } if *axis == k)
        })?,
    };
    Some((
        i,
        format!(
            "dimension {} ({}), which this item's feature conveys",
            r.source_id,
            fmt(own.value)
        ),
    ))
}

/// The Analysis's PMI dimensions: their standalone items, the problems, and the notes for the
/// items they are on. *wants* are the planners' items; a callout or step that states a record
/// takes its band and its source here.
pub(super) fn lower(cx: &Context, wants: &mut [Want]) -> Lowered {
    let mut standalone = Vec::new();
    let mut problems = Vec::new();
    let mut notes: BTreeMap<String, Note> = BTreeMap::new();
    let Some(Reading::Read(report)) = &cx.analysis.pmi else {
        return (standalone, problems, notes);
    };
    let converted = if cx.spec.units.is_mm() {
        std::borrow::Cow::Borrowed(report.records.as_slice())
    } else {
        std::borrow::Cow::Owned(
            report
                .records
                .iter()
                .map(|r| {
                    let mut r = r.clone();
                    if r.kind != "angular" {
                        r.label = cx.spec.units.length_text(&r.label);
                    }
                    r
                })
                .collect::<Vec<_>>(),
        )
    };
    let records: Vec<&Record> = converted
        .iter()
        // A common label (text on its faces, read as a dimension) is drawn as a label by
        // `pmi_lowering`, not here.
        .filter(|r| r.category == Category::Dimension && r.kind != "common_label")
        .collect();
    // A record whose identity another shares has no item ID, so it is not drawn (ADR R1
    // invariant 5).
    let ids = report.identities();
    let mut outcomes: Vec<Outcome> = records
        .iter()
        .map(|r| {
            match (
                outcome(
                    cx.analysis,
                    &ids,
                    r,
                    wants,
                    cx.turned.as_ref(),
                    cx.spec.units,
                ),
                ids.id(&r.source_id),
            ) {
                (o @ Outcome::Refused(_), _) | (o, Ok(_)) => o,
                (_, Err(why)) => Outcome::Refused(not_lowered(why)),
            }
        })
        .collect();
    // Specify's nominal-only general dimensions are drawn as the planners draw that geometry,
    // taking only the file's values (#265); in strict mode they stand as the file's PMI.
    if !super::pmi_policy::strict(cx.analysis, cx.spec) {
        for (j, r) in records.iter().enumerate() {
            if let Outcome::Standalone(own) = &outcomes[j]
                && crate::pmi::specify_general(report, r)
                && !crate::pmi::datum_referenced(r)
                // Preserve the composite's explicit patch witnesses (cx/pmi-groups).
                && !(r.face_groups.iter().any(|group| group.len() > 1)
                    && r.geometry.as_ref().is_some_and(|g| !g.sites.is_empty()))
            {
                outcomes[j] = match conveyed(cx.analysis, wants, r, own) {
                    Some((i, how)) => Outcome::Conveyed(i, how),
                    None => Outcome::General(general(report, r, own.clone(), cx.spec.units)),
                };
            }
        }
    }

    // A callout states a band only when every hole it calls out has the same one: one with
    // holes of different requirements is split by requirement when it is a plain count group,
    // as draftwright splits it (`lower_ap242_hole_tolerances`); a pattern's is not split.
    let mut by_callout: BTreeMap<usize, Vec<usize>> = BTreeMap::new();
    for (j, o) in outcomes.iter().enumerate() {
        if let Outcome::Hole(i, ..) = o {
            by_callout.entry(*i).or_default().push(j);
        }
    }
    let mut split_off: Vec<Want> = Vec::new();
    for (i, js) in by_callout {
        let n = holes_of(&wants[i]).len();
        let id = wants[i].id.clone();
        // Each hole's band, from the records that name it.
        let mut bands: Vec<BTreeSet<Key>> = vec![BTreeSet::new(); n];
        let mut reqs: BTreeMap<Key, Req> = BTreeMap::new();
        for &j in &js {
            if let Outcome::Hole(_, members, Some(q)) = &outcomes[j] {
                reqs.insert(q.key(), *q);
                for &m in members {
                    bands[m].insert(q.key());
                }
            }
        }
        let toleranced: Vec<usize> = js
            .iter()
            .copied()
            .filter(|&j| matches!(outcomes[j], Outcome::Hole(_, _, Some(_))))
            .collect();
        if bands.iter().any(|b| b.len() > 1) {
            for &j in &toleranced {
                outcomes[j] = Outcome::Refused(not_lowered(format!(
                    "ambiguous hole tolerance ownership: a hole {id} calls out has conflicting AP242 requirements"
                )));
            }
            bands.iter_mut().for_each(BTreeSet::clear);
        }
        let key = |m: usize| bands[m].first().copied();
        let keys: Vec<Option<Key>> = (0..n).map(key).collect();
        let uniform = keys.iter().all(|k| *k == keys[0]);
        // A plain count group, or a grid or line of holes (whose pitches still locate the
        // holes once its callout is split); a bolt circle's callout states equal spacing of all
        // its holes, so it is not split.
        let plain = wants[i].terms.first().is_some_and(|t| t.ends_with('×'))
            && !wants[i].terms.iter().any(|t| t.starts_with("EQ SP"));
        // The groups: each its key and its holes, in the callout's order.
        let mut groups: Vec<Group> = Vec::new();
        if uniform || !plain {
            groups.push((if uniform { keys[0] } else { None }, (0..n).collect()));
            if !uniform && !toleranced.is_empty() {
                let covered = keys.iter().filter(|k| k.is_some()).count();
                let distinct: BTreeSet<_> = keys.iter().flatten().collect();
                let why = if distinct.len() > 1 {
                    format!(
                        "the {n} holes of the bolt circle {id} calls out carry different AP242 tolerances; draftwright gives each its own, which the port does not yet for a bolt circle"
                    )
                } else {
                    format!(
                        "its tolerance covers {covered} of the {n} holes of the bolt circle {id} calls out; draftwright gives them their own, which the port does not yet for a bolt circle"
                    )
                };
                for &j in &toleranced {
                    if matches!(outcomes[j], Outcome::Hole(..)) {
                        outcomes[j] = Outcome::Refused(not_ported(why.clone()));
                    }
                }
            }
        } else {
            for (m, k) in keys.iter().enumerate() {
                match groups.iter_mut().find(|(g, _)| g == k) {
                    Some((_, ms)) => ms.push(m),
                    None => groups.push((*k, vec![m])),
                }
            }
        }
        let parts = split(&wants[i], &groups);
        for (g, (mut w, members)) in parts.into_iter().enumerate() {
            let stating: Vec<usize> = js
                .iter()
                .copied()
                .filter(|&j| match &outcomes[j] {
                    Outcome::Hole(_, ms, _) => ms.iter().any(|m| members.contains(m)),
                    _ => false,
                })
                .collect();
            let mut note = None;
            if let Some(q) = groups[g].0.map(|k| reqs[&k]) {
                let with: Vec<usize> = stating
                    .iter()
                    .copied()
                    .filter(|&j| matches!(outcomes[j], Outcome::Hole(_, _, Some(_))))
                    .collect();
                if let Some(mut printed) = q.print(&mut w) {
                    printed.banded = with.iter().map(|&j| records[j].source_id.clone()).collect();
                    printed.claimed = printed.banded.iter().map(|s| feature(&ids, s)).collect();
                    printed.clause = format!(
                        "; its tolerance {} is the part's AP242 PMI ({}): draftwright gives a toleranced bore diameter to the hole callout whose holes it names (lower_ap242_hole_tolerances)",
                        printed.band,
                        printed.banded.join(", ")
                    );
                    w.basis += &printed.clause;
                    note = Some(printed);
                } else {
                    for j in with {
                        outcomes[j] = Outcome::Refused(not_lowered(format!(
                            "{id}'s label states no value for its tolerance to follow"
                        )));
                    }
                }
            }
            let stating: Vec<usize> = stating
                .into_iter()
                .filter(|&j| matches!(outcomes[j], Outcome::Hole(..)))
                .collect();
            let nominal: Vec<String> = stating
                .iter()
                .filter(|&&j| matches!(outcomes[j], Outcome::Hole(_, _, None)))
                .map(|&j| records[j].source_id.clone())
                .collect();
            if !nominal.is_empty() {
                w.basis += &format!(
                    "; it states the part's AP242 diameter ({}), which draftwright gives to the hole callout whose holes it names (lower_ap242_nominal_diameters)",
                    nominal.join(", ")
                );
            }
            for &j in &stating {
                w.measurements.push(claim(&ids, records[j], w.value));
            }
            if let Some(n) = note {
                notes.insert(w.id.as_str().to_string(), n);
            }
            if g == 0 {
                wants[i] = w;
            } else {
                split_off.push(w);
            }
        }
    }
    standalone.extend(split_off);

    // A turned step's or boss's diameter states a band only when every record on it agrees.
    let mut by_item: BTreeMap<usize, Vec<usize>> = BTreeMap::new();
    for (j, o) in outcomes.iter().enumerate() {
        if let Outcome::Band(i, _) = o {
            by_item.entry(*i).or_default().push(j);
        }
    }
    for (i, js) in by_item {
        let reqs: BTreeMap<Key, Req> = js
            .iter()
            .filter_map(|&j| match outcomes[j] {
                Outcome::Band(_, q) => Some((q.key(), q)),
                _ => None,
            })
            .collect();
        let id = wants[i].id.clone();
        // Invariant: The preceding condition requires exactly one requested band.
        let q = *reqs.values().next().expect("a band");
        // A positively identified thread designation stands in place of the diameter (ISO 6410) and
        // carries its own tolerance class: a band or fit on that diameter has no term to ride,
        // and is reported, not appended to the designation.
        if tolerance::thread_designated(&wants[i]) {
            for &j in &js {
                outcomes[j] = Outcome::Refused(not_lowered(format!(
                    "{id} is stated by its thread designation, which carries the thread's own tolerance class; a diameter tolerance or fit is not printed on it"
                )));
            }
            continue;
        }
        // A counted item (`2× ø130`) states its size of each of its diameters: the records must
        // name as many distinct cylinders.
        let count: usize = wants[i]
            .terms
            .first()
            .and_then(|t| t.strip_suffix('×'))
            .and_then(|n| n.parse().ok())
            .unwrap_or(1);
        let mut named: Vec<[f64; 2]> = Vec::new();
        for &j in &js {
            for c in records[j].geometry.iter().flat_map(|g| &g.cylinders) {
                if !named.iter().any(|n| {
                    (n[0] - c.interval[0]).abs() <= 0.01 && (n[1] - c.interval[1]).abs() <= 0.01
                }) {
                    named.push(c.interval);
                }
            }
        }
        if named.len() < count {
            for j in js {
                outcomes[j] = Outcome::Refused(not_ported(format!(
                    "it names {} of the {count} diameters {id} states together; draftwright gives it to its own step, which the port does not split yet",
                    named.len()
                )));
            }
            continue;
        }
        let w = &mut wants[i];
        let printed = if reqs.len() == 1 { q.print(w) } else { None };
        let Some(mut printed) = printed else {
            for j in js {
                outcomes[j] = Outcome::Refused(not_lowered(format!(
                    "ambiguous external diameter tolerance ownership: {id} is named by AP242 diameters of different tolerances"
                )));
            }
            continue;
        };
        let sources: Vec<String> = js.iter().map(|&j| records[j].source_id.clone()).collect();
        printed.clause = format!(
            "; its tolerance {} is the part's AP242 PMI ({}): draftwright gives a toleranced external diameter to the turned step or boss whose cylinder it names (lower_ap242_external_diameter_tolerances)",
            printed.band,
            sources.join(", ")
        );
        w.basis += &printed.clause;
        for &j in &js {
            w.measurements.push(claim(&ids, records[j], w.value));
        }
        printed.claimed = sources.iter().map(|s| feature(&ids, s)).collect();
        printed.banded = sources;
        notes.insert(w.id.as_str().to_string(), printed);
    }

    for (r, o) in records.iter().zip(outcomes) {
        match o {
            Outcome::Refused(Refusal(code, why)) => problems.push(refusal(r, code, &why)),
            Outcome::Standalone(w) | Outcome::General(w) => standalone.push(w),
            Outcome::Hole(..) | Outcome::Band(..) => {}
            Outcome::Stated(i, how) => {
                let w = &mut wants[i];
                w.basis += &format!("; it states the part's AP242 {how} ({})", r.source_id);
                w.measurements.push(claim(&ids, r, w.value));
            }
            // Not claimed: a claim makes the planners' item authored PMI, which every later
            // stage reads as the file's own statement and location evidence.
            Outcome::Conveyed(i, how) => wants[i].basis += &conveyed_note(&how),
        }
    }
    // Qualifiers belong to the requirement even when a recognised size owns its ink.
    for r in &records {
        if let Some(q) = qualifier(r) {
            let manufacturing = crate::pmi_lowering::manufacturing(cx.analysis);
            let id = feature(&ids, &r.source_id);
            for w in wants
                .iter_mut()
                .filter(|w| w.measurements.iter().any(|m| m.feature == id))
            {
                let suffix = manufacturing
                    .owned
                    .iter()
                    .filter(|o| {
                        o.knurl_values().is_some()
                            && w.measurements.iter().any(|m| m.feature == o.id)
                    })
                    .find_map(|o| {
                        let terms = manufacturing.terms(&o.owner).0;
                        (!terms.is_empty())
                            .then(|| w.terms.windows(terms.len()).position(|s| s == terms))
                            .flatten()
                    })
                    .unwrap_or_else(|| {
                        w.terms
                            .iter()
                            .position(|t| matches!(t.as_str(), "SEE" | "THRU" | "↧" | "EQ"))
                            .unwrap_or(w.terms.len())
                    });
                // An equal word in the knurl's manufacturing suffix is not the
                // size's qualifier: that suffix may subsequently move into a note.
                if !w.terms[..suffix].iter().any(|t| t == q) {
                    w.terms.insert(suffix, q.into());
                }
            }
        }
    }
    (standalone, problems, notes)
}

fn refusal(r: &Record, code: &str, why: &str) -> Problem {
    let open = if why.contains("supplemental geometry") || why.contains("stated without limits") {
        "; add the required dimension in the Spec and review it manually"
    } else {
        ""
    };
    let faces = if matches!(
        r.geometry.as_ref().and_then(|g| g.refusal.as_ref()),
        Some(crate::pmi::GeometryRefusal::NominalMismatch { .. })
    ) {
        format!(
            "; reference face groups {:?} (STEP faces {:?})",
            r.face_groups, r.face_ids
        )
    } else {
        String::new()
    };
    let refs = vec![r.source_id.clone(), why.to_string()];
    Problem {
        id: format!("p:{code}:{}", r.source_id),
        code: code.into(),
        severity: if code == "pmi_not_lowered" {
            "error"
        } else {
            "info"
        }
        .into(),
        message: format!(
            "PMI dimension {} ({}) is not drawn: {}{faces}{open}.",
            r.source_id,
            if r.label.is_empty() {
                &r.kind
            } else {
                &r.label
            },
            crate::pmi::reader_reason(why)
        ),
        subjects: Vec::new(),
        refs,
        ..Default::default()
    }
}

/// One record's outcome against the planners' items (and the part's turning, when it is turned
/// whole).
fn outcome(
    a: &Analysis,
    ids: &Identities,
    r: &Record,
    wants: &[Want],
    turned: Option<&Turned>,
    units: crate::units::Units,
) -> Outcome {
    let fmt = |v| units.format(v);
    let symbolic = symbolic_bore(r);
    if !r.blockers.is_empty() {
        return Outcome::Refused(not_lowered(r.blockers.join("; ")));
    }
    let Some(g) = &r.geometry else {
        return Outcome::Refused(not_lowered(
            "its attachment was not derived (the Analysis predates PMI dimensions; analyse the part again)",
        ));
    };
    if !g.reasons.is_empty() {
        return Outcome::Refused(not_lowered(g.reasons.join("; ")));
    }
    let Some(value) = r.value else {
        return Outcome::Refused(not_lowered("the file states no nominal value"));
    };
    let band = requirement(r, value);
    let k = axis(g.axis.as_deref());
    match r.kind.as_str() {
        "angular" => {
            let Some(angle) = &g.angle else {
                return Outcome::Refused(not_lowered(
                    "the sides of its angle were not derived (the Analysis predates angular dimensions; analyse the part again)",
                ));
            };
            // draftwright's `_angular_renderable`: drawn only where the angle shows true.
            let Some(k) = angle.principal() else {
                return Outcome::Refused(not_lowered(
                    "supported angular ink requires explicit coplanar rays in a principal view: the plane of its sides is oblique to every view, so none shows the angle true",
                ));
            };
            let view = end_on(k);
            let basis = format!(
                "source-authored: the AP242 angular dimension {} ({}), about the vertex of its two planar sides in the {view} view, which looks along the normal of their plane and so shows the angle true (draftwright's render_pmi); no recognised feature's dimension states it, so it stands beside them{}",
                r.source_id,
                r.label,
                if r.basic {
                    "; basic, so its label is boxed"
                } else {
                    ""
                }
            );
            let mut want = own(
                ids,
                r,
                value,
                vec![view],
                Shape::Angular {
                    angle: angle.clone(),
                    alternates: Vec::new(),
                },
                basis,
            );
            want.terms = degrees(&want.terms);
            Outcome::Standalone(want)
        }
        "linear" | "thickness" => {
            let Some(k) = k else {
                let [a, b] = g.stations.as_slice() else {
                    return Outcome::Refused(not_lowered("a length needs two proven stations"));
                };
                let delta = [0, 1, 2].map(|k| b[k] - a[k]);
                let Some(view) = crate::pmi_lowering::projection_view(delta) else {
                    return Outcome::Refused(not_lowered(
                        "its stations do not lie in a principal projection plane",
                    ));
                };
                return Outcome::Standalone(own(
                    ids,
                    r,
                    value,
                    vec![view],
                    Shape::Aligned { a: *a, b: *b },
                    format!(
                        "source-authored: AP242 {} between its two proven stations, exact aligned span in the {view} view",
                        r.source_id
                    ),
                ));
            };
            if band.is_none()
                && r.label.trim() == fmt(value)
                && let Some(i) = measured_plane_size(a, wants, r, k)
            {
                return Outcome::Stated(
                    i,
                    "measured size between the same authored support planes",
                );
            }
            if r.basic
                && band.is_none()
                && r.label.trim() == fmt(value)
                && let Some(i) = compound_bore_location(a, wants, r, k)
            {
                return Outcome::Stated(
                    i,
                    "basic datum-to-bore location on the same proven compound centre",
                );
            }
            if !r.basic
                && band.is_none()
                && r.label.trim() == fmt(value)
                // Automatic baseline rungs hide their local step lengths. A derived PMI
                // requirement must then stand on its own instead of claiming hidden ink.
                && (g.targets.is_empty() || !wants.iter().any(|w| w.id.ends_with("/step_position.length")))
                && let Some(i) = step_length(wants, &g.stations, k, value)
            {
                return Outcome::Stated(
                    i,
                    "length, which draftwright gives to the turned step it spans (lower_ap242_nominal_step_lengths)",
                );
            }
            Outcome::Standalone(linear(ids, r, g, k, value))
        }
        "diameter" => {
            let internal = g.cylinders.iter().all(|c| c.internal);
            let owner = if internal {
                match k {
                    Some(k) => hole_callout(wants, &g.cylinders, k, value),
                    None => inclined_hole_callout(a, wants, r, &g.cylinders, value),
                }
            } else {
                None
            };
            if !r.basic
                && let Some((i, members)) = owner
                && (band.is_some() || canonical(r, value, units))
            {
                return Outcome::Hole(i, members, band);
            }
            let Some(k) = k else {
                return Outcome::Refused(not_ported(
                    "its cylinder's axis is not along x, y or z and no recognised bore callout proves its faces and full axis",
                ));
            };
            if symbolic && internal {
                return Outcome::Refused(not_lowered(
                    "a symbolic fit requires a uniquely matched recognised bore callout",
                ));
            }
            if !r.basic && !internal {
                match external_owner(wants, &g.cylinders, value, turned) {
                    Some(Ok(i)) => match band {
                        Some(b) => return Outcome::Band(i, b),
                        None if canonical(r, value, units) => {
                            return Outcome::Stated(
                                i,
                                "diameter, which draftwright gives to the turned step or boss whose cylinder it names (lower_ap242_nominal_diameters)",
                            );
                        }
                        None => {}
                    },
                    Some(Err(refusal)) => return Outcome::Refused(refusal),
                    None => {}
                }
            }
            // A shaft fit is printed on the turned diameter that states its size, as a hole
            // fit is on its bore's callout; with no such owner it is not drawn standalone.
            if symbolic {
                return Outcome::Refused(not_lowered(
                    "a symbolic shaft fit requires a uniquely matched recognised turned diameter",
                ));
            }
            // draftwright's `_standalone_cylinder_blocker`: one leader names one cylinder.
            let first = &g.cylinders[0];
            if g.cylinders[1..]
                .iter()
                .any(|c| (0..3).any(|j| (c.origin[j] - first.origin[j]).abs() > 0.01))
            {
                return Outcome::Refused(not_lowered(
                    "standalone diameter fallback references multiple distinct cylinder axis lines; no single truthful leader target exists",
                ));
            }
            let n = g.cylinders.len() as f64;
            let centre = g.cylinders.iter().fold([0.0; 3], |s, c| {
                let m = c.midpoint();
                [s[0] + m[0] / n, s[1] + m[1] / n, s[2] + m[2] / n]
            });
            Outcome::Standalone(leader(
                ids,
                r,
                k,
                value,
                "diameter",
                vec![(centre, first.radius)],
                "to its cylinder",
            ))
        }
        "radius" => {
            let Some(k) = k else {
                return Outcome::Refused(not_ported(
                    "its arc's axis is not along x, y or z; the port draws a radius where its arc is seen end-on",
                ));
            };
            let site = g.sites[0];
            Outcome::Standalone(leader(
                ids,
                r,
                k,
                value,
                "radius",
                vec![(site, 0.0)],
                "to a proven point of its arc",
            ))
        }
        other => Outcome::Refused(not_lowered(format!(
            "it states {}; a dimension is drawn along a straight projected path",
            match other {
                "curve_length" => "a length along a curve",
                "curved_dist" => "a distance along a curve",
                "oriented" => "a distance along a stated direction",
                _ => "a quantity draftwright has no drawing for",
            }
        ))),
    }
}

/// The source's own item: its feature `pmi:<source>`, its kind as the parameter.
fn own(
    ids: &Identities,
    r: &Record,
    value: f64,
    views: Vec<&'static str>,
    shape: Shape,
    basis: String,
) -> Want {
    let m = claim(ids, r, value);
    Want {
        units: Default::default(),
        id: want_id(&m.feature, &m.parameter),
        views,
        value,
        terms: terms(&r.label),
        measurements: vec![m],
        basis,
        shape,
        turning: None,
    }
}

/// A length between its two proven stations, along world axis *k*, where draftwright draws it
/// (`_pmi_front_linear` and the y branch of `_place_pmi_record`): x and z in the front view (x in
/// the plan when only the plan's witness is proven), y in the side view, else the plan. A z
/// length to an x bore's axis is drawn in the side view, which sees the bore end on (#34; not in
/// draftwright, whose plane-to-bore witness is z bores only). Its ends sit across the axis where draftwright's witness does (`_pmi_witness_from_bbox`): the faces'
/// box's middle in the front and side views, the stations' mean in the plan (for y, when it lies
/// on the faces).
fn linear(ids: &Identities, r: &Record, g: &crate::pmi::Geometry, k: usize, value: f64) -> Want {
    let s = &g.stations;
    let b = r.ref_bbox.unwrap_or_else(|| {
        let mut b = [
            f64::INFINITY,
            f64::INFINITY,
            f64::INFINITY,
            f64::NEG_INFINITY,
            f64::NEG_INFINITY,
            f64::NEG_INFINITY,
        ];
        for p in s {
            for i in 0..3 {
                b[i] = b[i].min(p[i]);
                b[i + 3] = b[i + 3].max(p[i]);
            }
        }
        b
    });
    let mid = |i: usize| 0.5 * (b[i] + b[i + 3]);
    let mean = |i: usize| s.iter().map(|p| p[i]).sum::<f64>() / s.len() as f64;
    let plan = g.view.as_deref() == Some("plan");
    let (mut views, at): (Vec<&'static str>, [f64; 3]) = match k {
        0 if plan => (vec!["plan"], [0.0, mean(1), mid(2)]),
        0 => (vec!["front"], [0.0, mean(1), mid(2)]),
        2 if g.view.as_deref() == Some("side") => (vec!["side"], [mean(0), mid(1), 0.0]),
        2 => (vec!["front"], [mid(0), mean(1), 0.0]),
        _ => {
            let x = mean(0);
            let x = if b[0] - 1e-6 <= x && x <= b[3] + 1e-6 {
                x
            } else {
                mid(0)
            };
            let views = match g.view.as_deref() {
                Some("plan") => vec!["plan"],
                Some("side") => vec!["side"],
                _ => vec!["side", "plan"],
            };
            (views, [x, 0.0, mid(2)])
        }
    };
    let lo = s.iter().map(|p| p[k]).fold(f64::INFINITY, f64::min);
    let hi = s.iter().map(|p| p[k]).fold(f64::NEG_INFINITY, f64::max);
    let (mut a, mut e) = (at, at);
    a[k] = lo;
    e[k] = hi;
    // Explicit face sites can carry proven witnesses away from the
    // composite's box midpoint. Preserve them through projection into the drawing.
    if !g.targets.is_empty() || !g.sites.is_empty() {
        a = s[0];
        e = s[1];
    }
    if !g.view_witnesses.is_empty() {
        for name in ["front", "side", "plan"] {
            if g.view_witnesses.contains_key(name) && !views.contains(&name) {
                views.push(name);
            }
        }
    }
    let view = views[0];
    let basis = format!(
        "source-authored: the AP242 {} dimension {} ({}), between its two proven stations along {}, in the {view} view (draftwright's render_pmi); no recognised feature's dimension states it, so it stands beside them{}",
        r.kind,
        r.source_id,
        r.label,
        ["x", "y", "z"][k],
        if r.basic {
            "; basic, so its label is boxed"
        } else {
            ""
        }
    );
    own(
        ids,
        r,
        value,
        views,
        Shape::Linear {
            a,
            b: e,
            axis: k,
            a_edge: false,
            b_edge: false,
            order: 1,
        },
        basis,
    )
}

/// A diameter or radius led to its feature where it is seen end-on.
fn leader(
    ids: &Identities,
    r: &Record,
    k: usize,
    value: f64,
    what: &str,
    sites: Vec<(P3, f64)>,
    how: &str,
) -> Want {
    let view = end_on(k);
    let basis = format!(
        "source-authored: the AP242 {what} {} ({}), led {how} in the {view} view, which sees it end-on (draftwright's render_pmi); no recognised feature's dimension states it, so it stands beside them{}",
        r.source_id,
        r.label,
        if r.basic {
            "; basic, so its label is boxed"
        } else {
            ""
        }
    );
    own(
        ids,
        r,
        value,
        vec![view],
        Shape::Callout {
            sites,
            arcs: Vec::new(),
        },
        basis,
    )
}

/// Drops the limits a PMI fit put beside its class when the spec hides fit limits
/// (`sheet/fit_deviations`, off by default).
pub(super) fn hide_fit_limits(
    spec: &Spec,
    notes: &BTreeMap<String, Note>,
    kept: &mut [(Want, bool, Option<&'static str>, Vec<&'static str>)],
) {
    if tolerance::limits_shown(spec) {
        return;
    }
    for (w, ..) in kept.iter_mut() {
        if let Some(term) = notes.get(w.id.as_str()).and_then(|n| n.limits.as_ref())
            && let Some(i) = w.terms.iter().position(|t| t == term)
        {
            w.terms.remove(i);
        }
    }
}

/// A spec's own tolerance or fit on a size wins over an imported one (draftwright's
/// `_hole_ownership_conflict`: "existing authored ownership wins"): the PMI band comes off the
/// kept item's label, its claims and its basis clause with it, and each source it came from is
/// reported not drawn. The spec's requirement is the one [`tolerance::apply`] then prints, or,
/// when it withholds it (a band of nothing, a fit the members do not share), the label states
/// neither, and the report says so.
pub(super) fn yield_to_spec(
    spec: &Spec,
    notes: &BTreeMap<String, Note>,
    kept: &mut [(Want, bool, Option<&'static str>, Vec<&'static str>)],
    problems: &mut Vec<Problem>,
) {
    for (w, ..) in kept.iter_mut() {
        let Some(note) = notes.get(w.id.as_str()) else {
            continue;
        };
        let Some((what, printed)) = tolerance::authored(spec, w) else {
            continue;
        };
        let band = &note.band;
        w.terms = note.before.clone();
        w.basis = w.basis.replacen(&note.clause, "", 1);
        w.measurements
            .retain(|m| !note.claimed.contains(&m.feature));
        let label = if printed {
            String::new()
        } else {
            format!("; the Spec's {what} cannot be printed, so the label states no tolerance")
        };
        for s in &note.banded {
            let mut refs = vec![s.clone(), "draftwright lower_ap242_hole_tolerances".into()];
            if !printed {
                refs.push("tolerance_withheld".into());
            }
            problems.push(Problem {
                id: format!("p:pmi_not_lowered:{s}"),
                code: "pmi_not_lowered".into(),
                severity: "error".into(),
                message: format!(
                    "The STEP tolerance {band} on {} is not drawn because the Spec supplies its own {what}{label}. Remove the Spec override to use the STEP tolerance.",
                    super::wording_of(w)
                ),
                subjects: vec![w.id.clone()],
                refs,
                ..Default::default()
            });
        }
    }
}

/// The PMI dimensions the plan leaves out with the items that state them (the spec omits them,
/// or another item conveys them without claiming them), each reported not drawn.
pub(super) fn omitted(
    analysis: &crate::model::Analysis,
    all: &[Want],
    kept: &[(Want, bool, Option<&'static str>, Vec<&'static str>)],
    omitted: &[super::Omitted],
) -> Vec<Problem> {
    let claimed: BTreeSet<&str> = kept
        .iter()
        .flat_map(|(w, ..)| w.measurements.iter().map(|m| m.feature.as_str()))
        .filter(|f| f.starts_with("pmi:"))
        .collect();
    let ids = match &analysis.pmi {
        Some(Reading::Read(r)) => r.identities(),
        _ => Identities::default(),
    };
    let mut out = Vec::new();
    for o in omitted {
        let Some(w) = all.iter().find(|w| w.id == o.id) else {
            continue;
        };
        for m in &w.measurements {
            // Named by its record's source, as the other PMI problems are.
            let Some(source) = ids.source(&m.feature) else {
                continue;
            };
            if claimed.contains(m.feature.as_str()) {
                continue;
            }
            out.push(Problem {
                crossing_kinds: Vec::new(),
                ink_is_leader: None,
                id: format!("p:pmi_not_drawn:{source}"),
                code: "pmi_not_drawn".into(),
                severity: "warning".into(),
                message: format!(
                    "PMI dimension {source} is not drawn: the plan leaves out {}, which states it ({}).",
                    if o.label.is_empty() { "the dimension" } else { &o.label },
                    crate::wording::left_out(&o.state)
                ),
                subjects: vec![o.id.clone()],
                refs: vec![source.to_string()],
                fixes: Vec::new(),
                // Shown again, the item states it.
                fix: match o.state {
                    crate::items::State::OmittedByUser => vec![crate::items::Op::remove(format!(
                        "dimensions/{}/omit",
                        crate::items::key(&o.id)
                    ))],
                    _ => Vec::new(),
                },
            });
        }
    }
    out
}

/// The pad between a basic dimension's label and its box, mm.
pub const BOX_PAD: f64 = 0.75;

/// A basic dimension's label box takes in its frame, so the collision checks see the ink drawn.
pub(super) fn finish(out: &mut [super::PlannedDimension]) {
    for d in out.iter_mut().filter(|d| crate::label_text::basic(d)) {
        if let Some(p) = &mut d.placed {
            let b = &mut p.label_box;
            *b = [
                b[0] - BOX_PAD,
                b[1] - BOX_PAD,
                b[2] + BOX_PAD,
                b[3] + BOX_PAD,
            ];
        }
    }
}

/// A callout cut into *groups* of its holes (by index), each its own callout of those holes: its
/// count, measurements and sites theirs, its ID its first hole's. One group is the callout as it
/// is.
fn split(w: &Want, groups: &[Group]) -> Vec<(Want, Vec<usize>)> {
    if groups.len() == 1 {
        return vec![(w.clone(), groups[0].1.clone())];
    }
    let holes = holes_of(w);
    let Shape::Callout { sites, .. } = &w.shape else {
        return vec![(w.clone(), groups[0].1.clone())];
    };
    groups
        .iter()
        .map(|(_, members)| {
            let features: BTreeSet<&str> = members.iter().map(|&m| holes[m].0.as_str()).collect();
            let mut part = w.clone();
            let n = members.len();
            if n > 1 {
                part.terms[0] = format!("{n}×");
            } else {
                part.terms.remove(0);
            }
            part.measurements
                .retain(|m| features.contains(m.feature.as_str()));
            part.shape = Shape::Callout { arcs: Vec::new(),
                sites: members.iter().map(|&m| sites[m]).collect(),
            };
            part.id = want_id(&holes[members[0]].0, "bore.diameter");
            part.basis += &format!(
                "; {n} of the {} identical holes, called out apart from the others because their AP242 requirements differ (draftwright splits a count group by requirement){}",
                holes.len(),
                if w.id.as_str().starts_with("d:f:hole_pattern:") {
                    "; the pattern's pitches still locate them"
                } else {
                    ""
                }
            );
            (part, members.clone())
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn inch_limits_and_tolerance_magnitudes_keep_their_units() {
        let mut w = Want {
            units: crate::units::Units::In,
            id: "d:test/bore.diameter".into(),
            views: vec!["plan"],
            value: 35.,
            terms: vec!["⌀1.378".into()],
            measurements: vec![],
            basis: String::new(),
            shape: Shape::Callout {
                arcs: vec![],
                sites: vec![],
            },
            turning: None,
        };
        let limits = Req {
            upper: 0.1,
            lower: -0.2,
            limits: Some((34.8, 35.1)),
            fit: None,
        };
        limits.print(&mut w).unwrap();
        assert_eq!(w.terms, ["⌀1.370", "-", "⌀1.382"]);
        let mut w = Want {
            terms: vec!["⌀1.378".into()],
            ..w
        };
        let band = Req {
            upper: 0.0254,
            lower: -0.0254,
            limits: None,
            fit: None,
        };
        band.print(&mut w).unwrap();
        assert_eq!(w.terms, ["⌀1.378", "±.001"]);
        assert_eq!(w.value, 35.);
    }

    #[test]
    fn every_angle_value_carries_its_unit() {
        let t = |label: &str| degrees(&terms(label)).join(" ");
        assert_eq!(t("60 ±0.5"), "60° ±0.5°");
        assert_eq!(t("30"), "30°");
        assert_eq!(t("45 +0.5/0"), "45° +0.5°/0°");
        assert_eq!(t("59.5 - 60.5"), "59.5° - 60.5°");
        // A value that already states its unit keeps one.
        assert_eq!(t("60° ±0.5°"), "60° ±0.5°");
        assert_eq!(t("(60)"), "(60°)");
        assert_eq!(t("2X 60"), "2X 60°");
    }

    #[test]
    fn limits_become_deviations_in_decimal() {
        assert_eq!(decimal_sub(35.0, 34.8), 0.2);
        assert_eq!(decimal_sub(35.2, 35.0), 0.2);
        assert_eq!(decimal_sub(6.35, 6.35), 0.0);
        assert_eq!(decimal_sub(0.5588, 0.4318), 0.127);
    }

    #[test]
    fn symmetric_limits_print_as_a_band_and_asymmetric_ones_as_limits() {
        let callout = || Want {
            units: Default::default(),
            id: "d:f:hole:1/bore.diameter".into(),
            views: vec!["plan"],
            value: 35.0,
            terms: vec!["2×".into(), "⌀35".into(), "THRU".into()],
            measurements: Vec::new(),
            basis: String::new(),
            shape: Shape::Callout {
                arcs: Vec::new(),
                sites: Vec::new(),
            },
            turning: None,
        };
        let limits = |lo: f64, hi: f64| Req {
            upper: decimal_sub(hi, 35.0),
            lower: decimal_sub(lo, 35.0),
            limits: Some((lo, hi)),
            fit: None,
        };
        let mut w = callout();
        assert_eq!(limits(34.8, 35.2).print(&mut w).unwrap().band, "±0.2");
        assert_eq!(w.terms, ["2×", "⌀35", "±0.2", "THRU"]);
        let mut w = callout();
        let note = limits(34.8, 35.1).print(&mut w).unwrap();
        assert_eq!(w.terms, ["2×", "⌀34.8", "-", "⌀35.1", "THRU"]);
        assert_eq!(note.before, callout().terms);
        let mut w = callout();
        let shaft = Req {
            upper: -0.009,
            lower: -0.025,
            limits: None,
            fit: None,
        };
        shaft.print(&mut w).unwrap();
        assert_eq!(w.terms, ["2×", "⌀35", "-0.009/-0.025", "THRU"]);
    }

    #[test]
    fn shaft_fits_take_their_iso_286_limits() {
        // A diameter stated by a shaft class alone (#30), its limits from ISO 286-2.
        let record = |code: &str, value: f64| Record {
            source_id: "dimension:#1".into(),
            category: Category::Dimension,
            kind: "diameter".into(),
            type_code: None,
            part: 0,
            part21_id: 1,
            value: Some(value),
            upper_tol: None,
            lower_tol: None,
            lower_bound: None,
            upper_bound: None,
            fit: Some(code.into()),
            label: format!("⌀{value} {code}"),
            datum_refs: Vec::new(),
            gtol_modifiers: Vec::new(),
            basic: false,
            faces: Vec::new(),
            face_ids: Vec::new(),
            face_groups: Vec::new(),
            edge_groups: Vec::new(),
            edges: Vec::new(),
            ref_bbox: None,
            dominant_axis: "Z".into(),
            instances: Vec::new(),
            blockers: Vec::new(),
            fields: Vec::new(),
            geometry: None,
        };
        let step = |value: f64| Want {
            units: Default::default(),
            id: "d:f:turned_step:1/step.diameter".into(),
            views: vec!["front"],
            value,
            terms: vec![format!("⌀{value}")],
            measurements: Vec::new(),
            basis: String::new(),
            shape: Shape::Callout {
                arcs: Vec::new(),
                sites: Vec::new(),
            },
            turning: None,
        };
        for (code, value, upper, lower, printed) in [
            ("g6", 15.0, -0.006, -0.017, "g6 -0.006\n-0.017"),
            ("h7", 20.0, 0.0, -0.021, "h7 0\n-0.021"),
            ("f7", 25.0, -0.020, -0.041, "f7 -0.020\n-0.041"),
            ("k6", 30.0, 0.015, 0.002, "k6 +0.015\n+0.002"),
            ("js6", 10.0, 0.0045, -0.0045, "js6 +0.0045\n-0.0045"),
        ] {
            let q = requirement(&record(code, value), value).expect(code);
            assert!(
                (q.upper - upper).abs() < 1e-9 && (q.lower - lower).abs() < 1e-9,
                "{code}"
            );
            assert!(q.limits.is_none());
            let mut w = step(value);
            let note = q.print(&mut w).unwrap();
            assert_eq!(note.band, printed);
            assert_eq!(w.terms.join(" "), format!("⌀{value} {printed}"));
        }
        // ISO 286's letters are case-significant: a mixed-case class is neither kind.
        assert!(requirement(&record("Js6", 10.0), 10.0).is_none());
    }

    #[test]
    fn labels_read_as_callout_terms() {
        assert_eq!(terms("ø62 +0.03/0"), ["⌀62", "+0.03/0"]);
        assert_eq!(terms("⌀62 +0.03/0"), ["⌀62", "+0.03/0"]);
        assert_eq!(terms("ø34.8 - ø35.2"), ["⌀34.8", "-", "⌀35.2"]);
        assert_eq!(terms("0.30 ±0.02 INCH"), ["0.30", "±0.02", "INCH"]);
        assert_eq!(terms("R12.7 ±0.508"), ["R12.7", "±0.508"]);
    }
}
