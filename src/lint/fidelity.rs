//! Stream K7 (#43 item 2): the checks draftwright ran on its own sheet that what a label states
//! is what its ink draws. Like [`super::structural`], whose `check` runs them, they read the
//! rendered drawing alone: each dimension's text (its annotation's runs), its ink
//! ([`Drawing::sheet`]), and the transform its view was drawn with ([`Sheet::frames`]), never
//! the part, the Plan or the Spec. A thing whose view has no recorded transform (a captured
//! Python sheet) is not judged.
//!
//! - `label_vs_measured` (draftwright's `_lint_dim`): a linear dimension's stated value against
//!   the span between its two extension lines, undoing a recorded break before dividing by
//!   its view's scale. A label
//!   correctly rounded at the precision it prints is fine; otherwise a warning past 0.5% and an
//!   error from 5% ("possible axis swap or wrong endpoint"). The port prints one decimal place
//!   at least (`dims::fmt`), the sheet precision draftwright's `_displayed_decimals` takes as the
//!   floor, and a bare number may assert more places. `N× v` is read as `v`: every producer of
//!   the port draws a counted linear dimension across one member (a pattern's first pitch, one
//!   rise of equal levels, one of the equal features it spans), where draftwright tags each
//!   per-unit producer (`label_value`) and reads an untagged one as a span of N pitches. An
//!   `N× ⌀d` counts d-diameter features, as draftwright reads it.
//! - `nominal_rounded` (`_lint_display_precision`): once per sheet, info, the dimensions whose
//!   correctly rounded label prints a nominal the model does not have (by more than 0.0005 mm),
//!   naming the largest. The Spec can raise precision by semantic measurement ID; the drawing
//!   records the approved policy alongside the ink for this independent check.
//! - `angular_label_vs_geometry` (`lint_angular_geometry`): an angular dimension's degrees
//!   against the angle between its drawn rays (its two extension lines, from their meeting point,
//!   the vertex), within half its last printed place. Never the planned angle.
//! - `angular_geometry_mismatch` (`lint_angular_geometry`): its rays must meet at a vertex in a
//!   proper sector, and its arc must be drawn about that vertex at one radius, within the sector,
//!   on both halves of it, reaching each ray at the arc's radius (where its arrowhead's tip is:
//!   draftwright reads the tip as the arc's end vertex too), each extension line running along its
//!   ray past the arc. The port draws an arc as chords within 0.005 mm of it, so its chords'
//!   ends are on the circle.
//! - `angular_support_mismatch` (`lint_angular_supports`, its drawn half): each ray must lie
//!   along a straight visible edge of the view, either both forwards or both extended backwards,
//!   so the angle remains the corner's physical included angle, and a quantity mark (`n×`) cannot be proved by one drawn corner, so it is
//!   refused. draftwright compares the drawn witnesses with the supports its registry binds;
//!   lint here has the drawn view's edges, which the faces of an angle shown true project to.
//! - `datum_leader_remote` (`_lint_datum_leader_locality`): a datum feature symbol (a PMI datum,
//!   `pmi:datum.…`) whose shaft, from the triangle's base on the surface to the frame, is longer
//!   than max(24 mm, 2.5 × the frame's larger side). The base is the far side of the symbol's
//!   ink along its shaft. Warning.
//!
//! Not ported here: `interior_label_on_narrow_material`, which reads the view's filled material
//! field; the drawing records the view's edges, not which side of them is material.

use std::collections::BTreeMap;

use super::{LintIssue, Severity};
use crate::output::{Curve, Drawing, Drawn, Kind, P, linear_ink};

/// draftwright's `_MATERIAL_LABEL_DISCREPANCY`: from here a label's disagreement is an error.
const MATERIAL_DISCREPANCY: f64 = 0.05;
/// A disagreement below this fraction of the measured length is not reported.
const MINOR_DISCREPANCY: f64 = 0.005;
/// draftwright's `_NOMINAL_SHIFT_FLOOR`, mm.
const NOMINAL_SHIFT_FLOOR: f64 = 5e-4;
/// The port's sheet precision, decimal places (`dims::fmt`).
const SHEET_DECIMALS: usize = 1;
/// draftwright's tolerance on angular witness ink, page mm.
const WITNESS: f64 = 0.01;
/// How far a ray may lie from the view edge it stands for, page mm: the edge and the ray are
/// projected from the same face by one transform, so only rounding separates them.
const ON_EDGE: f64 = 0.01;
/// draftwright's floor on a datum shaft's length, page mm.
const DATUM_REACH: f64 = 24.0;

fn issue(
    id: String,
    code: &str,
    severity: Severity,
    message: String,
    d: Option<&Drawn>,
) -> LintIssue {
    LintIssue {
        sheet: None,
        id,
        code: code.into(),
        severity,
        message,
        at: None,
        view: None,
        subjects: Vec::new(),
        requirements: Vec::new(),
        measurements: Vec::new(),
        sources: Vec::new(),
        stage: None,
        annotation: d.map(|d| d.name.clone()),
        related: Vec::new(),
        evidence: None,
        fix: None,
    }
}

/// Each annotation's text (its runs, joined by spaces) and view, by name.
fn texts(drawing: &Drawing) -> BTreeMap<&str, (String, Option<&str>)> {
    let mut out = BTreeMap::new();
    for a in &drawing.annotations {
        out.entry(a.name.as_str()).or_insert_with(|| {
            let text = a
                .runs
                .iter()
                .filter_map(|&i| drawing.text.get(i))
                .map(|r| r.text.as_str())
                .collect::<Vec<_>>()
                .join(" ");
            (text, a.view.as_deref())
        });
    }
    out
}

/// Every finding of this module on *drawing*.
pub(super) fn check(drawing: &Drawing) -> Vec<LintIssue> {
    let texts = texts(drawing);
    let frame = |view: Option<&str>| {
        let view = view?;
        let frame = drawing
            .sheet
            .frames
            .get(view.strip_prefix("v:").unwrap_or(view))?;
        (frame.scale.is_finite() && frame.scale > 0.0).then_some(frame)
    };
    let mut issues = Vec::new();
    let mut rounded: Vec<(f64, String, f64)> = Vec::new();
    for d in &drawing.sheet.items {
        match d.kind {
            Kind::Dimension if d.elbow.is_none() => {
                let Some((text, view)) = texts.get(d.name.as_str()) else {
                    continue;
                };
                let Some(frame) = frame(*view) else {
                    continue;
                };
                let scale = frame.scale;
                let unfolded_span = |a, b| len(sub(frame.unfold(b), frame.unfold(a)));
                let body = drawing
                    .sheet
                    .label_text
                    .get(&d.name)
                    .map(|wording| wording.body(text));
                let text = match &body {
                    Some(Some(body)) if label_value(body).is_some() => {
                        body.trim_start_matches('(').trim_end_matches(')')
                    }
                    Some(_) => {
                        issues.push(issue(format!("l:label_vs_measured:{}", d.name), "label_vs_measured", Severity::Error,
                            "Authored wording or its measured value is missing or damaged; restore the generated value and wording wrapper.".into(), Some(d)));
                        continue;
                    }
                    None => text.as_str(),
                };
                if let Some(l) = linear_ink(&d.lines) {
                    let unfolded = frame.broken.as_ref().is_some_and(|b| b.axis == l.k);
                    let measured = if unfolded {
                        let points = l.span.map(|along| {
                            let mut p = [l.offset; 2];
                            p[l.k] = along;
                            p
                        });
                        unfolded_span(points[0], points[1])
                    } else {
                        l.span[1] - l.span[0]
                    };
                    issues.extend(label_vs_measured(
                        d,
                        text,
                        measured,
                        scale,
                        unfolded,
                        drawing.sheet.precision.get(&d.name).copied(),
                        &mut rounded,
                    ));
                } else if let Some(span) = drawing
                    .sheet
                    .frames
                    .get(view.map_or("", |v| v.strip_prefix("v:").unwrap_or(v)))
                    .and_then(|f| f.half_axis)
                    .and_then(|axis| half_span(&d.lines, axis))
                {
                    issues.extend(label_vs_measured(
                        d,
                        text,
                        span,
                        scale,
                        false,
                        drawing.sheet.precision.get(&d.name).copied(),
                        &mut rounded,
                    ));
                } else if let Some(span) = aligned_span(&d.lines) {
                    let unfolded = frame.broken.is_some();
                    let span = if unfolded {
                        unfolded_span(
                            meet(d.lines[0], d.lines[2]).unwrap_or(d.lines[0][0]),
                            meet(d.lines[1], d.lines[2]).unwrap_or(d.lines[1][0]),
                        )
                    } else {
                        span
                    };
                    issues.extend(label_vs_measured(
                        d,
                        text,
                        span,
                        scale,
                        unfolded,
                        drawing.sheet.precision.get(&d.name).copied(),
                        &mut rounded,
                    ));
                } else if d.lines.len() >= 3 {
                    issues.extend(angular(drawing, d, text, *view));
                }
            }
            Kind::Gdt if d.name.starts_with("pmi:datum.") => {
                issues.extend(datum_leader(d));
                issues.extend(datum_square(d));
            }
            _ => {}
        }
    }
    rounded.sort_by(|a, b| b.0.total_cmp(&a.0).then(a.1.cmp(&b.1)));
    if let Some((shift, label, value)) = rounded.first() {
        let i = issue(
            "l:nominal_rounded".into(),
            "nominal_rounded",
            Severity::Info,
            format!(
                "{} dimension(s) print a nominal the model does not have, rounded to the sheet's \
                 precision; the largest is '{label}' for {value:.4} ({shift:.4} mm). Within a \
                 general tolerance this is ordinary. Where a fit depends on it, the dimension \
                 needs more places (set precision for its measurement ID)",
                rounded.len()
            ),
            None,
        );
        issues.push(i);
    }
    issues
}

/// Whether *label* states an angle by its unit (draftwright's `is_angular_label`).
fn angular_label(label: &str) -> bool {
    label.contains('°') || label.to_lowercase().contains("deg")
}

/// The leading number of *s* (digits with at most one point) and the rest.
fn number(s: &str) -> Option<(f64, &str)> {
    let end = s
        .char_indices()
        .scan(false, |point, (i, c)| {
            if c.is_ascii_digit() {
                Some(i + 1)
            } else if c == '.' && !*point {
                *point = true;
                Some(i)
            } else {
                None
            }
        })
        .last()?;
    if end == 0 {
        return None;
    }
    Some((s[..end].parse().ok()?, &s[end..]))
}

/// The value a linear dimension's label asserts about its drawn span (draftwright's
/// `_label_value`, with the port's per-unit reading of `N× v`; see the module's notes): the
/// leading number of the text before any tolerance, after a diameter, radius or reference
/// bracket.
pub(crate) fn label_value(label: &str) -> Option<f64> {
    let label = label.trim();
    if let Some((count, rest)) = number(label)
        && let Some(rest) = rest.trim_start().strip_prefix(['×', 'x'])
        && count.fract() == 0.0
    {
        let rest = rest.trim_start().trim_start_matches(['ø', '⌀', 'R', 'r']);
        return number(rest.trim_start()).map(|(v, _)| v);
    }
    let body = label.split(['±', '+']).next()?;
    let start = body.find(|c: char| c.is_ascii_digit())?;
    number(&body[start..]).map(|(v, _)| v)
}

/// The places a label's value is written to (draftwright's `_displayed_decimals`): the sheet's,
/// unless the label is a bare number written to more.
fn displayed_decimals(label: &str) -> usize {
    let bare = label.trim().trim_start_matches(['⌀', 'Ø', 'ø', 'R', ' ']);
    let is_bare = !bare.is_empty()
        && bare.chars().all(|c| c.is_ascii_digit() || c == '.')
        && bare.chars().filter(|&c| c == '.').count() <= 1
        && bare.starts_with(|c: char| c.is_ascii_digit());
    let places = if is_bare {
        bare.split_once('.').map_or(0, |(_, f)| f.len())
    } else {
        0
    };
    SHEET_DECIMALS.max(places)
}

fn label_vs_measured(
    d: &Drawn,
    label: &str,
    page: f64,
    scale: f64,
    unfolded: bool,
    precision: Option<u8>,
    rounded: &mut Vec<(f64, String, f64)>,
) -> Option<LintIssue> {
    if angular_label(label) {
        return None;
    }
    // A PMI label in inches states its unit (`pmi`'s `INCH`) and the record's own digits: its
    // value and its rounding are read in inches, then compared in millimetres.
    let inch = label.split_whitespace().any(|t| t == "INCH");
    let (unit, places) = if inch {
        let digits: String = label
            .trim()
            .chars()
            .take_while(|c| c.is_ascii_digit() || *c == '.')
            .collect();
        (
            25.4,
            digits.split_once('.').map_or(0, |(_, f)| f.len()) as i32,
        )
    } else {
        (
            1.0,
            precision.map_or_else(|| displayed_decimals(label) as i32, i32::from),
        )
    };
    let value = unit * label_value(label)?;
    let measured = page / scale;
    let shift = (value - measured).abs();
    if shift <= unit * 0.5 * 10f64.powi(-places) + 1e-9 {
        if shift > NOMINAL_SHIFT_FLOOR {
            rounded.push((shift, label.to_string(), measured));
        }
        return None;
    }
    if measured <= 1e-6 {
        return None;
    }
    let ratio = shift / measured;
    if ratio <= MINOR_DISCREPANCY {
        return None;
    }
    let mut i = issue(
        format!("l:label_vs_measured:{}", d.name),
        "label_vs_measured",
        if ratio >= MATERIAL_DISCREPANCY {
            Severity::Error
        } else {
            Severity::Warning
        },
        format!(
            "Dim '{label}': label value {value:.3} differs from {} {page:.3}{} \
             by {:.1}% — possible axis swap or wrong endpoint",
            if unfolded {
                "unfolded span"
            } else {
                "measured path length"
            },
            if scale != 1.0 {
                format!(" (÷{scale} = {measured:.3})")
            } else {
                String::new()
            },
            ratio * 100.0
        ),
        Some(d),
    );
    i.fix = Some("draw the dimension between the points its value measures".into());
    Some(i)
}

fn sub(a: P, b: P) -> P {
    [a[0] - b[0], a[1] - b[1]]
}

fn dot(a: P, b: P) -> f64 {
    a[0] * b[0] + a[1] * b[1]
}

fn cross(a: P, b: P) -> f64 {
    a[0] * b[1] - a[1] * b[0]
}

fn len(a: P) -> f64 {
    a[0].hypot(a[1])
}

/// Where the carriers of two lines meet; none when they are parallel.
fn meet(a: [P; 2], b: [P; 2]) -> Option<P> {
    let (u, v) = (sub(a[1], a[0]), sub(b[1], b[0]));
    let den = cross(u, v);
    if den.abs() <= 1e-12 * len(u) * len(v) {
        return None;
    }
    let t = cross(sub(b[0], a[0]), v) / den;
    Some([a[0][0] + t * u[0], a[0][1] + t * u[1]])
}

/// An angular dimension's rays from its ink alone: the vertex where its two extension lines'
/// carriers meet, and the unit direction of each from it, towards the line's far end.
fn rays(lines: &[[P; 2]]) -> Result<(P, [P; 2]), &'static str> {
    let (e0, e1) = (lines[0], lines[1]);
    let vertex = meet(e0, e1).ok_or("its extension lines are parallel")?;
    let unit = |e: [P; 2]| {
        let far = if len(sub(e[0], vertex)) > len(sub(e[1], vertex)) {
            e[0]
        } else {
            e[1]
        };
        let d = sub(far, vertex);
        let n = len(d);
        (n.is_finite() && n > 1e-9).then(|| [d[0] / n, d[1] / n])
    };
    let u = unit(e0).ok_or("an extension line ends at the vertex")?;
    let v = unit(e1).ok_or("an extension line ends at the vertex")?;
    let angle = cross(u, v).abs().atan2(dot(u, v));
    if !(1e-9 < angle && angle < std::f64::consts::PI - 1e-9) {
        return Err("its rays have no proper sector");
    }
    Ok((vertex, [u, v]))
}

fn angular(drawing: &Drawing, d: &Drawn, label: &str, view: Option<&str>) -> Vec<LintIssue> {
    let mut out = Vec::new();
    let mismatch = |message: String| {
        let mut i = issue(
            format!("l:angular_geometry_mismatch:{}", d.name),
            "angular_geometry_mismatch",
            Severity::Error,
            message,
            Some(d),
        );
        i.fix = Some(
            "draw the angle's arc about its vertex with extension lines along both rays".into(),
        );
        i
    };
    let (vertex, [u, v]) = match rays(&d.lines) {
        Ok(r) => r,
        Err(why) => {
            out.push(mismatch(format!(
                "Angle '{label}' has unusable reference geometry: {why}"
            )));
            return out;
        }
    };
    let signed = cross(u, v).atan2(dot(u, v));
    let angle = signed.abs();
    let actual = angle.to_degrees();
    // The leading nominal, at its printed resolution; a quantity mark counts corners.
    let body = match number(label.trim()) {
        Some((_, rest)) if rest.trim_start().starts_with('×') => {
            rest.trim_start().trim_start_matches('×').trim_start()
        }
        _ => label.trim(),
    };
    let nominal = number(body);
    let places = nominal.map_or(0, |_| {
        let digits: String = body
            .chars()
            .take_while(|c| c.is_ascii_digit() || *c == '.')
            .collect();
        digits.split_once('.').map_or(0, |(_, f)| f.len())
    });
    if nominal.is_none_or(|(n, _)| (n - actual).abs() > 0.5 * 10f64.powi(-(places as i32)) + 1e-8) {
        let mut i = issue(
            format!("l:angular_label_vs_geometry:{}", d.name),
            "angular_label_vs_geometry",
            Severity::Error,
            format!("Angle '{label}' does not agree with its reference rays ({actual:.6} degrees)"),
            Some(d),
        );
        i.at = Some(vertex);
        i.fix = Some("draw the angle between the edges whose angle it states".into());
        out.push(i);
    }
    // The arc: chords about the vertex, within the sector, on both halves of it.
    let polar = |p: P| {
        let r = sub(p, vertex);
        let offset = cross(u, r).atan2(dot(u, r)) * signed.signum();
        (len(r), offset.rem_euclid(std::f64::consts::TAU))
    };
    let mut radii = Vec::new();
    let mut halves = [false; 2];
    let mut escaped = false;
    for c in &d.lines[2..] {
        let (r0, r1) = (len(sub(c[0], vertex)), len(sub(c[1], vertex)));
        if (r0 - r1).abs() > WITNESS {
            escaped = true;
            continue;
        }
        let (_, offset) = polar([(c[0][0] + c[1][0]) / 2.0, (c[0][1] + c[1][1]) / 2.0]);
        if offset > angle + 1e-6 {
            escaped = true;
        } else {
            halves[usize::from(offset > angle / 2.0)] = true;
            radii.push((r0 + r1) / 2.0);
        }
    }
    radii.sort_by(f64::total_cmp);
    let radius = radii.get(radii.len() / 2).copied();
    let on_arc = radius.is_some_and(|radius| {
        radii.iter().all(|r| (r - radius).abs() <= WITNESS)
            && [(u, &d.lines[0]), (v, &d.lines[1])].iter().all(|(ray, e)| {
                let along = |p: P| dot(sub(p, vertex), *ray);
                let across = |p: P| cross(*ray, sub(p, vertex)).abs();
                // The arc reaches the ray at its radius (its arrowhead's tip) ...
                let tip = d.lines[2..]
                    .iter()
                    .flatten()
                    .any(|p| (along(*p) - radius).abs() <= WITNESS && across(*p) <= WITNESS);
                // ... and the extension line runs along the ray past the arc.
                let (lo, hi) = (along(e[0]).min(along(e[1])), along(e[0]).max(along(e[1])));
                let extension = across(e[0]) <= WITNESS
                    && across(e[1]) <= WITNESS
                    && lo >= -WITNESS
                    && lo < radius - WITNESS
                    && hi > radius + WITNESS;
                tip && extension
            })
    });
    if escaped || halves != [true, true] || !on_arc {
        out.push(mismatch(format!(
            "Angle '{label}' lacks a circular arc, arrow tips or extension lines at both rays \
             of its sector"
        )));
    }
    // Both supports may be extended through their common vertex. Reversing
    // both rays preserves the included angle; reversing only one changes it to
    // its supplement and must still be refused.
    let edges: Vec<[P; 2]> = view
        .and_then(|name| drawing.sheet.views.iter().find(|w| w.name == name))
        .map(|w| {
            w.edges
                .iter()
                .filter_map(|c| match c {
                    Curve::Line(a, b) => Some([*a, *b]),
                    _ => None,
                })
                .collect()
        })
        .unwrap_or_default();
    let supported = |ray: P, sense: f64| {
        edges.iter().any(|[a, b]| {
            let across = |p: P| cross(ray, sub(p, vertex)).abs();
            let along = |p: P| sense * dot(sub(p, vertex), ray);
            across(*a) <= ON_EDGE
                && across(*b) <= ON_EDGE
                && along(*a).max(along(*b)) > ON_EDGE
                && len(sub(*b, *a)) > ON_EDGE
        })
    };
    let quantity = match number(label.trim()) {
        Some((n, rest)) if rest.trim_start().starts_with('×') => n,
        _ => 1.0,
    };
    // A counted angle is proved by the closed loop of visible edges through the drawn corner:
    // exactly that many corners, each at the stated angle (a regular polygon's `8× 135°`).
    let forward = supported(u, 1.0) && supported(v, 1.0);
    let counted = quantity == 1.0
        || nominal.is_some_and(|(n, _)| {
            let tolerance = 0.5 * 10f64.powi(-(places as i32)) + 1e-8;
            let start = if forward { u } else { [-u[0], -u[1]] };
            loop_corners(&edges, vertex, start).is_some_and(|corners| {
                corners.len() as f64 == quantity
                    && corners.iter().all(|c| (c - n).abs() <= tolerance)
            })
        })
        // `2×`: the corner and its mirror image across a vertical or horizontal line, both
        // supported by visible edges (a rounded corner meets at its theoretical vertex).
        || (quantity == 2.0 && mirror_corner(&edges, vertex, [u, v], if forward { 1.0 } else { -1.0 }));
    let why = if !counted {
        Some(format!(
            "its quantity {quantity}× is not proved by the one corner drawn"
        ))
    } else if !((supported(u, 1.0) && supported(v, 1.0))
        || (supported(u, -1.0) && supported(v, -1.0)))
    {
        Some("a ray does not lie along a visible edge of its view".to_string())
    } else {
        None
    };
    if let Some(why) = why {
        let mut i = issue(
            format!("l:angular_support_mismatch:{}", d.name),
            "angular_support_mismatch",
            Severity::Error,
            format!("Angle '{label}': {why}"),
            Some(d),
        );
        i.at = Some(vertex);
        i.fix = Some("dimension the angle between the corner's own edges".into());
        out.push(i);
    }
    out
}

/// Whether visible straight *edges* support a second corner mirroring the one at *vertex*
/// with rays *rays* (in their *sense*) across a vertical or horizontal line.
fn mirror_corner(edges: &[[P; 2]], vertex: P, rays: [P; 2], sense: f64) -> bool {
    const NEAR: f64 = 0.05;
    let along = |e: &[P; 2], d: P| {
        let w = sub(e[1], e[0]);
        let n = len(w);
        n > NEAR && cross(w, d).abs() / n <= 1e-4
    };
    let supported_at = |at: P, ray: P| {
        edges.iter().any(|[a, b]| {
            let across = |p: P| cross(ray, sub(p, at)).abs();
            let ahead = |p: P| sense * dot(sub(p, at), ray);
            across(*a) <= NEAR && across(*b) <= NEAR && ahead(*a).max(ahead(*b)) > NEAR
        })
    };
    (0..2).any(|k| {
        let flip = |d: P| {
            let mut d = d;
            d[k] = -d[k];
            d
        };
        let [u, v] = rays.map(flip);
        edges.iter().filter(|e| along(e, u)).any(|e| {
            edges.iter().filter(|f| along(f, v)).any(|f| {
                let (p, r) = (e[0], sub(e[1], e[0]));
                let (q, t) = (f[0], sub(f[1], f[0]));
                let det = cross(r, t);
                if det.abs() < 1e-12 {
                    return false;
                }
                let s = cross(sub(q, p), t) / det;
                let at = [p[0] + s * r[0], p[1] + s * r[1]];
                (at[1 - k] - vertex[1 - k]).abs() <= NEAR
                    && (at[k] - vertex[k]).abs() > 1.0
                    && supported_at(at, u)
                    && supported_at(at, v)
            })
        })
    })
}

/// The included angles (degrees) of the closed loop of straight *edges* through *start*, walked
/// first along *direction*; none when the loop does not close or branches at a corner.
fn loop_corners(edges: &[[P; 2]], start: P, direction: P) -> Option<Vec<f64>> {
    const NEAR: f64 = 0.02;
    let unit = |d: P| {
        let n = len(d);
        (n > 1e-9).then(|| [d[0] / n, d[1] / n])
    };
    let (mut p, mut d) = (start, unit(direction)?);
    let mut corners = Vec::new();
    for _ in 0..256 {
        // Run on through collinear edges to the far end of this side.
        while let Some(q) = edges
            .iter()
            .flat_map(|e| [(e[0], e[1]), (e[1], e[0])])
            .filter(|(a, b)| {
                len(sub(*a, p)) <= NEAR
                    && cross(d, sub(*b, p)).abs() <= NEAR
                    && dot(d, sub(*b, p)) > NEAR
            })
            .map(|(_, b)| b)
            .max_by(|a, b| dot(d, sub(*a, p)).total_cmp(&dot(d, sub(*b, p))))
        {
            p = q;
        }
        let turns: Vec<P> = edges
            .iter()
            .flat_map(|e| [(e[0], e[1]), (e[1], e[0])])
            .filter(|(a, b)| len(sub(*a, p)) <= NEAR && len(sub(*b, p)) > NEAR)
            .filter_map(|(_, b)| unit(sub(b, p)))
            .filter(|w| cross(d, *w).abs() > 1e-3 || dot(d, *w) > 0.0)
            .filter(|w| dot(d, *w) < 1.0 - 1e-6)
            .collect();
        let [w] = turns.as_slice() else {
            return None;
        };
        corners.push(dot([-d[0], -d[1]], *w).clamp(-1.0, 1.0).acos().to_degrees());
        d = *w;
        if len(sub(p, start)) <= NEAR {
            return Some(corners);
        }
    }
    None
}

/// Read the actual triangle and first shaft, including pinned and bent leaders.
/// A tiny angular tolerance permits floating-point transport, not an oblique departure.
fn datum_square(d: &Drawn) -> Option<LintIssue> {
    let [a, apex, b] = d.datum_triangle?;
    let [start, end] = *d.lines.first()?;
    let base = sub(b, a);
    let shaft = sub(end, start);
    let norm = len(base) * len(shaft);
    if norm > 1e-12
        && len(sub(start, apex)) <= ON_EDGE
        && dot(base, shaft).abs() <= norm * 0.1f64.to_radians().sin()
        && dot(shaft, sub(apex, [(a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0])) > 0.0
    {
        return None;
    }
    let mut i = issue(
        format!("l:datum_leader_not_normal:{}", d.name),
        "datum_leader_not_normal",
        Severity::Warning,
        format!(
            "Datum leader {} must leave its triangle square to the face; use a straight normal leader or a bend after a square stub.",
            d.name
        ),
        Some(d),
    );
    i.at = Some(apex);
    Some(i)
}

fn datum_leader(d: &Drawn) -> Option<LintIssue> {
    let [near, elbow] = *d.lines.first()?;
    let label = d.label?;
    let shaft = sub(near, elbow);
    let n = len(shaft);
    if n <= 1e-9 {
        return None;
    }
    let toward = [shaft[0] / n, shaft[1] / n];
    // The triangle's base is the symbol's ink furthest from the frame along its shaft.
    let length = [
        [d.ink[0], d.ink[1]],
        [d.ink[2], d.ink[1]],
        [d.ink[0], d.ink[3]],
        [d.ink[2], d.ink[3]],
    ]
    .iter()
    .map(|c| dot(sub(*c, elbow), toward))
    .fold(n, f64::max);
    let limit = DATUM_REACH.max(2.5 * (label[2] - label[0]).max(label[3] - label[1]));
    (length > limit).then(|| {
        let mut i = issue(
            format!("l:datum_leader_remote:{}", d.name),
            "datum_leader_remote",
            Severity::Warning,
            format!("datum leader {} is {length:.1} mm from its surface", d.name),
            Some(d),
        );
        i.at = Some(elbow);
        i.fix = Some("set the datum symbol closer to its surface".into());
        i
    })
}

/// A half dimension's span in a half section ([`crate::half_section`]): one witness parallel to
/// the *axis* (the page coordinate crossing it, and its value there) and the dimension line
/// across it, the diameter twice the witness's distance from the axis.
fn half_span(lines: &[[P; 2]], (k, at): (usize, f64)) -> Option<f64> {
    let [w, d] = lines else { return None };
    let along =
        |l: &[P; 2]| (l[0][k] - l[1][k]).abs() < 1e-6 && (l[0][1 - k] - l[1][1 - k]).abs() > 1e-6;
    let across = (d[0][1 - k] - d[1][1 - k]).abs() < 1e-6 && (d[0][k] - d[1][k]).abs() > 1e-6;
    (along(w) && across).then(|| 2.0 * (w[0][k] - at).abs())
}

/// Recognise perpendicular extension lines and a parallel dimension line at any rotation.
fn aligned_span(lines: &[[P; 2]]) -> Option<f64> {
    let [a, b, d] = lines else { return None };
    let delta = [d[1][0] - d[0][0], d[1][1] - d[0][1]];
    let length = delta[0].hypot(delta[1]);
    if length < 1e-9 {
        return None;
    }
    let u = delta.map(|x| x / length);
    let project = |p: P| p[0] * u[0] + p[1] * u[1];
    if [a, b]
        .iter()
        .any(|e| (project(e[1]) - project(e[0])).abs() > 1e-6)
    {
        return None;
    }
    let span = (project(b[0]) - project(a[0])).abs();
    (span > 1e-9).then_some(span)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn labels_read_as_draftwright_reads_them_with_the_ports_counted_dimensions() {
        assert_eq!(label_value("12.5"), Some(12.5));
        assert_eq!(label_value("⌀8.5"), Some(8.5));
        assert_eq!(label_value("7.5 ±0.1"), Some(7.5));
        assert_eq!(label_value("(25)"), Some(25.0));
        assert_eq!(label_value("4× ⌀8.5 THRU"), Some(8.5));
        assert_eq!(label_value("3× 20"), Some(20.0));
        assert_eq!(label_value("M10"), Some(10.0));
        assert_eq!(label_value("THRU"), None);
        assert_eq!(displayed_decimals("40"), 1);
        assert_eq!(displayed_decimals("4.45"), 2);
        assert_eq!(displayed_decimals("12 ±0.05"), 1);
    }
}
