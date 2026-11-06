//! Slot coverage (Python's `linting/slot_coverage.py`): a lone through slot states its width, its
//! length, and its end radius when its ends are proven arcs (LINT-slot_coverage-lone_slot,
//! LINT-code-slot_requirement_state).
//!
//! Each of the Analysis's `slot` features (quiddity's `Slot`: `width`, `length`, `end_radius`
//! when the ends are proven arcs, and its place: `long_axis`, `lo`/`hi` along it, `w_center` on
//! `width_axis`) has the requirements `slot_width.length`, `slot_length.length`, for an
//! obround `slot_end_radius.radius`, and its location `location_slot.length`, judged by
//! [`parameters::coverage`]. The location is draftwright's (`compiled.py`, `location_datum` is
//! `bbox` for a slot): the offset of the slot's low end along its long axis from the part's box
//! ([`location`]); a slot whose long axis lies along no axis of the part has a location that
//! cannot be checked.
//! Its transverse centre also needs locating (`location_slot_across.length`),
//! unless it lies on the datum itself. A table cell for another feature cannot
//! state this requirement, even when its numerical coordinate matches.
//!
//! Lone obround slots that are congruent (the same width, length and full-round ends, seen through
//! the same axis: [`grouped`]) are dimensioned together by one `N× SLOT W × L` callout
//! (`dims::slots`; `features/<id>/split` keeps a slot out), and
//! each is located by its centre along its long axis rather than its low end. An obround's end
//! radius that is half its width is stated by a slot callout (`SLOT` and the width) that claims
//! the slot's width: such a callout leaves no separate radius to find (the same for a pattern).
//! A full-round obround split from the callout is dimensioned by its two end centres
//! ([`ends_located`]): it wants its width, its centre-to-centre distance (`slot_centres.length`)
//! and its low end's centre located (`location_slot.length`), and no length or end radius.
//!
//! A slot that is a member of a recognised slot pattern (quiddity's `SlotArray` or `SlotGrid`, a
//! `slot_pattern` feature whose faces include the slot's) is the pattern's, not a lone slot,
//! as in Python. The pattern has its own requirements ([`pattern_wanted`];
//! LINT-slot_coverage-slot_pattern, Python's `_parameter_ids` and `_physical_requirement_count`):
//! its member's width and length (`slot_width.length`, `slot_length.length`), an obround's end
//! radius (`slot_end_radius.radius`), a linear array's pitch (`pitch.length`) or a grid's column
//! and row pitches (`grid_pitch.length.col`, `.row`, quiddity's `col_pitch` and `row_pitch`), and
//! its centre's location from the part's box in x and in y (`location_slot_pattern.length.x`,
//! `.y`: the array's centroid or the grid's centre, as the dimensions locate it), five
//! requirements for an array and six for a grid, one more for an obround. As in Python, a
//! pattern cut along anything but the part's z has no location vocabulary, and is one
//! requirement that cannot be checked; so is a pattern whose members cannot be read or are not
//! identical (never a guess from one member).

use serde_json::Value;

use super::parameters::{self, Wanted, fmt, fmt3};
use super::{LintIssue, Mark, Requirement, Severity};
use crate::bridge::Feature;
use crate::bridge::holes::{dir_to_part, frame, principal, to_part};
use crate::model::Analysis;

const AXES: [&str; 3] = ["x", "y", "z"];

/// A slot's location: the part's axis its long axis lies along, and the offset of its low end
/// along it from the part's box (the Analysis's envelope), of its centre for a slot the planner
/// calls out with its congruent slots ([`grouped`]), or of its low end's centre for a slot
/// located by its end centres ([`ends_located`]); or why it cannot be read.
pub fn location(analysis: &Analysis, f: &Feature) -> Result<(&'static str, f64), String> {
    location_to(analysis, f, grouped(analysis, f))
}

/// [`location`] to the slot's centre when *centre*, else to its low end, or to that end's
/// centre for a slot located by its end centres ([`ends_located`]).
fn location_to(
    analysis: &Analysis,
    f: &Feature,
    centre: bool,
) -> Result<(&'static str, f64), String> {
    let p = &f.parameters;
    let long = p
        .get("long_axis")
        .and_then(Value::as_str)
        .and_then(|a| AXES.iter().position(|x| *x == a))
        .ok_or("its record gives no long axis")?;
    let (lo, hi) = (parameters::size(f, "lo")?, parameters::size(f, "hi")?);
    let frame = frame(analysis);
    let mut d = [0.0; 3];
    d[long] = 1.0;
    let d = dir_to_part(&frame, d);
    let k = principal(d).ok_or("its long axis lies along no axis of the part")?;
    // The ends' coordinates along the part's axis k: quiddity's origin, and its long axis
    // carried onto k (its other axes are square to k, so they add nothing).
    let ends = [lo, hi].map(|t| frame[0][k] + t * d[k]);
    // The low end the planner dimensions ([`outline`]: the exact centre of the slot's faces less
    // half its length, where the record agrees with it). quiddity rounds `lo`, `hi` and `length`
    // to 0.01 mm each, so the recorded `lo` and that end can round apart on a 0.005 tie (cgb245:
    // 95.96 recorded, 95.9648 from the centre); lint judges the end the drawing states.
    let together = centre;
    let inset = match parameters::size(f, "end_radius") {
        Ok(r) if !together && ends_located(analysis, f) => r,
        _ => 0.0,
    };
    let low = match outline(analysis, f) {
        Ok(o) if o.long == k && together => 0.5 * (o.ends[0] + o.ends[1]),
        Ok(o) if o.long == k => o.ends[0] + inset,
        _ if together => 0.5 * (ends[0] + ends[1]),
        _ => ends[0].min(ends[1]) + inset,
    };
    Ok((AXES[k], low - analysis.envelope[0][k]))
}

/// A through slot in the part's frame, as the planner places it (`dims::slots`): the axes it
/// runs along, spans across and passes through, its ends along the first, its centre line on the
/// second, its span through it, its width, and its end radius when its ends are proven arcs.
#[derive(Clone, Debug, PartialEq)]
pub struct Outline {
    pub long: usize,
    pub wide: usize,
    pub deep: usize,
    pub ends: [f64; 2],
    pub centre: f64,
    pub through: [f64; 2],
    pub width: f64,
    pub end_radius: Option<f64>,
}

/// Slot *f*'s [`Outline`], or why its record cannot be read. quiddity rounds `lo`, `hi` and
/// `w_center` to 0.01 mm in its own frame; the box around its faces (the fingerprint's position)
/// is exact and is taken when it agrees within that rounding, as the planner takes it.
pub fn outline(analysis: &Analysis, f: &Feature) -> Result<Outline, String> {
    let p = &f.parameters;
    let letter = |name: &str| {
        p.get(name)
            .and_then(Value::as_str)
            .and_then(|a| AXES.iter().position(|x| *x == a))
            .ok_or_else(|| format!("its record gives no {name}"))
    };
    let (ql, qw) = (letter("long_axis")?, letter("width_axis")?);
    if ql == qw {
        return Err("its long and width axes are the same".into());
    }
    let qd = 3 - ql - qw;
    let frame = frame(analysis);
    let world = |q: usize| {
        let mut d = [0.0; 3];
        d[q] = 1.0;
        principal(dir_to_part(&frame, d)).ok_or("its axes lie along no axis of the part")
    };
    let (long, wide) = (world(ql)?, world(qw)?);
    let deep = 3 - long - wide;
    let (width, length) = (
        parameters::size(f, "width")?,
        parameters::size(f, "length")?,
    );
    let (lo, hi) = (parameters::size(f, "lo")?, parameters::size(f, "hi")?);
    let w = parameters::size(f, "w_center")?;
    let (d0, d1) = (parameters::size(f, "d_lo")?, parameters::size(f, "d_hi")?);
    let point = |t: f64, d: f64| {
        let mut q = [0.0; 3];
        q[ql] = t;
        q[qw] = w;
        q[qd] = d;
        to_part(&frame, q)
    };
    let (a, b) = (point(lo, d0), point(hi, d1));
    let c = f.fingerprint.position;
    let exact = |k: usize, recorded: f64| {
        if (c[k] - recorded).abs() <= 0.01 {
            c[k]
        } else {
            recorded
        }
    };
    let along = exact(long, 0.5 * (a[long] + b[long]));
    Ok(Outline {
        long,
        wide,
        deep,
        ends: [along - 0.5 * length, along + 0.5 * length],
        centre: exact(wide, a[wide]),
        through: [a[deep].min(b[deep]), a[deep].max(b[deep])],
        width,
        end_radius: p.get("end_radius").and_then(Value::as_f64),
    })
}

/// What makes lone slots congruent, so the planner calls them out together (`dims::slots`'s
/// `congruence`): the axis they pass through, their width and length, and their end radius.
type Congruent = (usize, i64, i64, Option<i64>);

fn congruence(analysis: &Analysis, f: &Feature) -> Option<Congruent> {
    let o = outline(analysis, f).ok()?;
    let key = crate::bridge::holes::size_key;
    Some((
        o.deep,
        key(o.width),
        key(parameters::size(f, "length").ok()?),
        o.end_radius.map(key),
    ))
}

/// The record field a slot split from its congruent slots' callout carries
/// (`features/<id>/split`, [`crate::recognition_corrections`]): it is dimensioned on its own.
pub const ALONE: &str = "split_from_slot_callout";

/// Whether slot *f* was split from its congruent slots' callout ([`ALONE`]).
pub fn alone(f: &Feature) -> bool {
    f.parameters.get(ALONE).and_then(Value::as_bool) == Some(true)
}

/// The record field a keyhole slot carries in an Analysis whose circular seats were left out
/// ([`without_recesses`]), so it is still read as one ([`keyed`]).
const KEYHOLE: &str = "keyhole_slot";

/// Whether slot *f* is a keyhole: a circular seat through it, at the middle of its length, opens
/// it sideways (nist_ftc_08, nist_stc_08). The seat's axis and callout carry that compound; the
/// slot keeps its own items, out of any slot callout.
pub fn keyed(analysis: &Analysis, f: &Feature) -> bool {
    if f.parameters.get(KEYHOLE).and_then(Value::as_bool) == Some(true) {
        return true;
    }
    let Ok(o) = outline(analysis, f) else {
        return false;
    };
    let mid = 0.5 * (o.ends[0] + o.ends[1]);
    super::channels::seats(analysis)
        .into_iter()
        .filter_map(|(_, s)| s.ok())
        .any(|s| {
            s.run_axis == o.deep
                && (s.centre[o.long] - mid).abs() <= FULL_ROUND
                && (s.centre[o.wide] - o.centre).abs() <= 0.5 * o.width + s.radius
        })
}

/// *analysis* without added recess and rectangular through-notch annotations
/// (the established route templates' comparison scope),
/// its keyhole slots still marked as such ([`KEYHOLE`]), so their slots are grouped as in the
/// drawing itself.
pub fn without_recesses(analysis: &Analysis) -> Analysis {
    let keyholes: Vec<String> = analysis
        .features
        .iter()
        .filter(|f| f.kind == "slot" && keyed(analysis, f))
        .map(|f| f.id.clone())
        .collect();
    let mut out = analysis.clone();
    // This is a historical layout reference, not the final feature inventory.
    // Through-notch labels and their coordinate notes are also new additions;
    // leaving them here would change the old routes they are meant to preserve.
    out.features.retain(|f| {
        !matches!(
            f.kind.as_str(),
            "section_recess" | "rectangular_through_notch"
        )
    });
    // Specify's general-tolerance sizes of those recesses go with them (#265): planned as
    // draftwright's own dimensions, they are the recess dimensions this drawing leaves out.
    let recess: std::collections::BTreeSet<usize> = analysis
        .features
        .iter()
        .filter(|f| f.kind == "section_recess")
        .flat_map(|f| f.faces.iter().copied())
        .collect();
    if let Some(crate::pmi::Reading::Read(report)) = &mut out.pmi {
        let gone: std::collections::BTreeSet<String> = report
            .records
            .iter()
            .filter(|r| {
                crate::pmi::specify_general(report, r) && r.faces.iter().any(|f| recess.contains(f))
            })
            .map(|r| r.source_id.clone())
            .collect();
        report.records.retain(|r| !gone.contains(&r.source_id));
    }
    for f in out.features.iter_mut().filter(|f| keyholes.contains(&f.id)) {
        f.parameters.insert(KEYHOLE.into(), Value::Bool(true));
    }
    out
}

/// Whether lone slot *f* is dimensioned with congruent lone slots by one slot callout, and so
/// located by its centre: only full-round obrounds are; a slot split from the callout ([`alone`])
/// or a keyhole ([`keyed`]) is in no group.
pub fn grouped(analysis: &Analysis, f: &Feature) -> bool {
    if alone(f) || !full_round(f) || keyed(analysis, f) {
        return false;
    }
    let Some(k) = congruence(analysis, f) else {
        return false;
    };
    let (_, lone) = split(analysis);
    lone.iter().any(|g| g.id == f.id) && {
        lone.iter().any(|g| {
            g.id != f.id && !alone(g) && !keyed(analysis, g) && congruence(analysis, g) == Some(k)
        })
    }
}

/// Whether lone slot *f*, split from its congruent slots' callout ([`alone`]), is dimensioned by
/// its two end centres: a full-round obround that is no keyhole ([`keyed`]) is located at its low
/// end's centre and states its centre-to-centre distance (`slot_centres.length`) and its width,
/// which with the centres fixes the full-round ends, in place of its length and end radius. Any
/// other split slot is dimensioned singly by its length, end radius and low end.
pub fn ends_located(analysis: &Analysis, f: &Feature) -> bool {
    alone(f) && full_round(f) && !keyed(analysis, f)
}

/// How far twice an end radius may differ from the width and the ends still be full-round:
/// quiddity rounds both to 0.01 mm (nist_ctc_03's 19.05 wide slots record R9.52, not 9.525).
pub const FULL_ROUND: f64 = 0.016;

/// Whether slot *f*'s ends are full-round: its end radius half its width, to [`FULL_ROUND`].
fn full_round(f: &Feature) -> bool {
    matches!(
        (parameters::size(f, "width"), parameters::size(f, "end_radius")),
        (Ok(w), Ok(r)) if (2.0 * r - w).abs() <= FULL_ROUND
    )
}

/// Whether a slot callout (`SLOT` in its text) claims *owner*'s width.
fn slot_callout_claims(owner: &str, marks: &[Mark]) -> bool {
    marks.iter().any(|m| {
        m.text.split_whitespace().any(|t| t == "SLOT")
            && m.claims
                .iter()
                .any(|c| c.feature == owner && c.parameter == "slot_width.length")
    })
}

/// Whether slot *f*'s ends are full-round (its end radius half its width) and a slot callout
/// claiming *owner*'s width (the slot's own, or its pattern's) states them: `SLOT` and the width,
/// so no separate radius is wanted.
fn slot_callout_states_ends(owner: &str, f: &Feature, marks: &[Mark]) -> bool {
    let Ok(w) = parameters::size(f, "width") else {
        return false;
    };
    full_round(f)
        && marks.iter().any(|m| {
            m.text.split_whitespace().any(|t| t == "SLOT")
                && super::holes::states(&m.text, w)
                && m.claims
                    .iter()
                    .any(|c| c.feature == owner && c.parameter == "slot_width.length")
        })
}

/// The member slots of slot pattern *p*: the `slot` features inside its faces.
pub fn members<'a>(analysis: &'a Analysis, p: &Feature) -> Vec<&'a Feature> {
    analysis
        .features
        .iter()
        .filter(|f| f.kind == "slot" && f.faces.iter().all(|i| p.faces.binary_search(i).is_ok()))
        .collect()
}

/// The slot patterns, and the lone slots: every `slot` feature not inside a pattern's faces.
fn split(analysis: &Analysis) -> (Vec<&Feature>, Vec<&Feature>) {
    let patterns: Vec<&Feature> = analysis
        .features
        .iter()
        .filter(|f| f.kind == "slot_pattern")
        .collect();
    let lone = analysis
        .features
        .iter()
        .filter(|f| f.kind == "slot")
        .filter(|f| {
            !patterns
                .iter()
                .any(|p| f.faces.iter().all(|i| p.faces.binary_search(i).is_ok()))
        })
        .collect();
    (patterns, lone)
}

/// The lone slots' requirements.
pub fn wanted(analysis: &Analysis) -> Vec<Wanted<'_>> {
    let (_, lone) = split(analysis);
    let mut out = Vec::new();
    for f in lone {
        let by_centres = ends_located(analysis, f);
        for (parameter, field) in [
            ("slot_width.length", "width"),
            ("slot_length.length", "length"),
        ] {
            if by_centres && field == "length" {
                // Its end centres' distance apart: the length less the full-round ends.
                let v = parameters::size(f, "length")
                    .and_then(|l| Ok(l - 2.0 * parameters::size(f, "end_radius")?));
                out.push(Wanted {
                    feature: f,
                    parameter: "slot_centres.length".into(),
                    callout: v.as_ref().map_or("its end centres' distance".into(), |v| {
                        format!("end centres {} apart", fmt(*v))
                    }),
                    values: v.map(|v| vec![v]),
                });
                continue;
            }
            let v = parameters::size(f, field);
            out.push(Wanted {
                feature: f,
                parameter: parameter.into(),
                callout: v
                    .as_ref()
                    .map_or(format!("its {field}"), |v| format!("{field} {}", fmt(*v))),
                values: v.map(|v| vec![v]),
            });
        }
        // An end radius quiddity did not prove (null) is no requirement: the ends may be square.
        // Full-round ends whose centres are located are fixed by the width.
        if let Some(r) = f.parameters.get("end_radius").and_then(|v| v.as_f64())
            && !by_centres
        {
            out.push(Wanted {
                feature: f,
                parameter: "slot_end_radius.radius".into(),
                values: Ok(vec![r]),
                callout: format!("R{}", fmt(r)),
            });
        }
        let at = location(analysis, f);
        out.push(Wanted {
            feature: f,
            parameter: "location_slot.length".into(),
            callout: at.as_ref().map_or("its location".into(), |(axis, v)| {
                let at = if grouped(analysis, f) {
                    "centre"
                } else if by_centres {
                    "low end's centre"
                } else {
                    "low end"
                };
                format!("its {at} {} from the part's {axis} edge", fmt(*v))
            }),
            values: at.map(|(_, v)| vec![v]),
        });
        let across =
            outline(analysis, f).map(|o| (AXES[o.wide], o.centre - analysis.envelope[0][o.wide]));
        if across.as_ref().is_ok_and(|(_, d)| d.abs() < 1e-6) {
            continue;
        }
        out.push(Wanted {
            feature: f,
            parameter: "location_slot_across.length".into(),
            callout: across
                .as_ref()
                .map_or("its transverse location".into(), |(axis, d)| {
                    format!("its centre line {} from the part's {axis} edge", fmt(*d))
                }),
            values: across.map(|(_, d)| vec![d]),
        });
    }
    out
}

/// A slot record's centre in quiddity's frame (its `Slot.location`): mid-span along its long
/// axis, on its centre line across, mid-depth.
pub(crate) fn centre(f: &Feature) -> Result<[f64; 3], String> {
    let axis = |k: &str| {
        f.parameters
            .get(k)
            .and_then(Value::as_str)
            .and_then(|a| AXES.iter().position(|x| *x == a))
            .ok_or_else(|| format!("its record gives no {k}"))
    };
    let (long, wide) = (axis("long_axis")?, axis("width_axis")?);
    if long == wide {
        return Err("its long and width axes are the same".into());
    }
    let mut c = [0.0; 3];
    c[long] = (parameters::size(f, "lo")? + parameters::size(f, "hi")?) / 2.0;
    c[wide] = parameters::size(f, "w_center")?;
    c[3 - long - wide] = (parameters::size(f, "d_lo")? + parameters::size(f, "d_hi")?) / 2.0;
    Ok(c)
}

/// A slot pattern's requirements, or why it cannot be checked: its members (the record's `slots`,
/// by feature ID) unreadable or not identical, or the pattern not cut along the part's z.
pub fn pattern_wanted<'a>(
    analysis: &'a Analysis,
    p: &'a Feature,
) -> Result<Vec<Wanted<'a>>, String> {
    let ids: Vec<&str> = p
        .parameters
        .get("slots")
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(Value::as_str).collect())
        .unwrap_or_default();
    if ids.len() < 2 {
        return Err("its record names fewer than two member slots".into());
    }
    let members: Vec<&Feature> = ids
        .iter()
        .map(|id| {
            analysis
                .features
                .iter()
                .find(|f| f.id == *id && f.kind == "slot")
                .ok_or_else(|| format!("its member {id} is not a slot of the part"))
        })
        .collect::<Result<_, _>>()?;
    let first = members[0];
    let key = |f: &Feature| -> Result<(Value, Value, f64, f64, Option<f64>), String> {
        Ok((
            f.parameters.get("long_axis").cloned().unwrap_or_default(),
            f.parameters.get("width_axis").cloned().unwrap_or_default(),
            parameters::size(f, "width")?,
            parameters::size(f, "length")?,
            f.parameters.get("end_radius").and_then(Value::as_f64),
        ))
    };
    // [`centre`] checks that the slots' long and width axes are two distinct axes.
    centre(first)?;
    let (long0, wide0, width, length, end_radius) = key(first)?;
    for f in &members[1..] {
        let (long, wide, w, l, r) = key(f)?;
        let same = |a: f64, b: f64| (a - b).abs() < 1e-6;
        if (&long, &wide) != (&long0, &wide0)
            || !same(w, width)
            || !same(l, length)
            || r.is_some() != end_radius.is_some()
            || r.zip(end_radius).is_some_and(|(a, b)| !same(a, b))
        {
            return Err(format!(
                "its slots are not identical ({} and {})",
                first.id, f.id
            ));
        }
    }
    let frame = frame(analysis);
    // The slots' depth axis: quiddity's axis that is neither their long nor their width axis.
    let normal = AXES.map(|a| {
        let named = [&long0, &wide0].contains(&&Value::from(a));
        if named { 0.0 } else { 1.0 }
    });
    match principal(dir_to_part(&frame, normal)) {
        Some(2) => {}
        k => {
            return Err(format!(
                "it is cut along {}, and draftwright locates a slot pattern only when it is cut along z (`_parameter_ids`)",
                k.map_or("no axis of the part", |k| AXES[k])
            ));
        }
    }
    let centre = match p.parameters.get("center") {
        Some(c) => {
            parameters::place(&frame, Some(c)).ok_or("its record's centre is not a point")?
        }
        None => {
            let mut c = [0.0; 3];
            for f in &members {
                let at = to_part(&frame, centre(f)?);
                (0..3).for_each(|k| c[k] += at[k] / members.len() as f64);
            }
            c
        }
    };
    let want = |parameter: &str, values: Result<Vec<f64>, String>, callout: String| Wanted {
        feature: p,
        parameter: parameter.into(),
        values,
        callout,
    };
    let sized = |parameter: &str, v: Result<f64, String>, what: &str| {
        let callout = v
            .as_ref()
            .map_or(format!("its {what}"), |v| format!("{what} {}", fmt(*v)));
        want(parameter, v.map(|v| vec![v]), callout)
    };
    let mut out = vec![
        sized("slot_width.length", Ok(width), "member width"),
        sized("slot_length.length", Ok(length), "member length"),
    ];
    if let Some(r) = end_radius {
        out.push(want(
            "slot_end_radius.radius",
            Ok(vec![r]),
            format!("R{}", fmt(r)),
        ));
    }
    if p.parameters.get("row_pitch").is_some_and(|v| !v.is_null()) {
        for (name, field) in [("col", "col_pitch"), ("row", "row_pitch")] {
            out.push(sized(
                &format!("grid_pitch.length.{name}"),
                parameters::size(p, field),
                &format!("{name} pitch"),
            ));
        }
    } else {
        out.push(sized("pitch.length", parameters::size(p, "pitch"), "pitch"));
    }
    for k in [0, 1] {
        let d = centre[k] - analysis.envelope[0][k];
        out.push(want(
            &format!("location_slot_pattern.length.{}", AXES[k]),
            Ok(vec![d]),
            format!("its centre {} from the part's {} edge", fmt(d), AXES[k]),
        ));
    }
    Ok(out)
}

/// A grouped slot's centre ([`grouped`]), or the low end centre of a slot located by its end
/// centres ([`ends_located`]), can stand on another feature's location line (a hole's
/// or pattern's `location….<axis>`, a seat's `seat_location.location.<axis>`, another slot's
/// `location_slot_across.length`): the planner omits the slot's location as conveyed by that line
/// (`dims::holes::consolidate`, ARCH0009-6). A mark in the slot's view claiming such a location on
/// the slot's axis, and stating its distance, claims the slot's `location_slot.length` too, for
/// every rule ([`super::lint`]: coverage, and the location network's routes).
/// The same rule applies across every slot's width: a coincident ordinate in
/// its end-on view states its transverse centre too. The station graph still
/// checks the actual witness direction and endpoints independently.
pub(crate) fn conveyed_locations(analysis: &Analysis, marks: &mut [Mark]) {
    let (_, lone) = split(analysis);
    let at: Vec<(&str, (&str, f64), usize, &str)> = lone
        .iter()
        .filter(|f| grouped(analysis, f) || ends_located(analysis, f))
        .filter_map(|f| {
            Some((
                f.id.as_str(),
                location(analysis, f).ok()?,
                outline(analysis, f).ok()?.deep,
                "location_slot.length",
            ))
        })
        .chain(lone.iter().filter_map(|f| {
            let (walls, centre) = super::redundancy::slot_walls(analysis, &f.id)?;
            Some((
                f.id.as_str(),
                (
                    AXES[walls.axis],
                    (centre - analysis.envelope[0][walls.axis]).abs(),
                ),
                outline(analysis, f).ok()?.deep,
                "location_slot_across.length",
            ))
        }))
        .collect();
    if at.is_empty() {
        return;
    }
    // The view that looks along each axis (the planner's `end_on`: lint reads no plan).
    let view = |deep: usize| format!("v:{}", ["side", "front", "plan"][deep]);
    // The axis a claimed location measures along, when the claim names one.
    let axis_of = |c: &crate::output::Claim| -> Option<&'static str> {
        if c.parameter == "location_slot_across.length" {
            let g = lone.iter().find(|f| f.id == c.feature)?;
            return Some(AXES[outline(analysis, g).ok()?.wide]);
        }
        let (stem, axis) = c.parameter.rsplit_once('.')?;
        let axis = AXES.iter().find(|a| **a == axis)?;
        (stem.starts_with("location") || stem.starts_with("seat_location")).then_some(*axis)
    };
    for m in marks {
        // A coordinate cell identifies its row's hole, without a witness or
        // alignment line reaching a slot at the same numerical station.
        if m.name.starts_with("hole_schedule_") || m.name.starts_with("hole_table_plan/") {
            continue;
        }
        let conveyed: Vec<(&str, &str)> = at
            .iter()
            .filter(|(id, (axis, d), deep, parameter)| {
                m.view.as_deref() == Some(view(*deep).as_str())
                    && super::holes::states(&m.text, *d)
                    && m.claims
                        .iter()
                        .any(|c| c.feature != *id && axis_of(c) == Some(axis))
                    && (*parameter != "location_slot_across.length"
                        || super::location::edges(analysis, std::slice::from_ref(m))
                            .iter()
                            .any(|span| {
                                let Some(k) = AXES.iter().position(|a| a == axis) else {
                                    return false;
                                };
                                let near = analysis.envelope[0][k];
                                let centre = near + d;
                                span.axis == k
                                    && super::redundancy::Span::new(k, span.a, span.b)
                                        .same(super::redundancy::Span::new(k, near, centre))
                            }))
                    && !m
                        .claims
                        .iter()
                        .any(|c| c.feature == *id && c.parameter == *parameter)
            })
            .map(|(id, _, _, parameter)| (*id, *parameter))
            .collect();
        for (id, parameter) in conveyed {
            m.claims.push(crate::output::Claim {
                feature: id.into(),
                parameter: parameter.into(),
            });
        }
    }
}

/// One span, one dimension (ARCH0009-6, `dims::holes::consolidate`): the planner draws one
/// position line for lone slots whose low ends lie the same distance from the part's box on the
/// same axis (within 1e-6 mm, its `SAME_SPAN`), seen through the same axis (its same view); the
/// survivor claims only its own slot, and the others are omitted as conveyed by it. Each mark
/// claiming one of those slots' `location_slot.length` also claims the others', so the drawn line
/// states them all; its text is still judged against each slot's value.
fn shared_locations(analysis: &Analysis, marks: &[Mark]) -> Vec<Mark> {
    const PARAMETER: &str = "location_slot.length";
    let (_, lone) = split(analysis);
    let at: Vec<(&str, (&str, f64), usize)> = lone
        .iter()
        .filter_map(|f| {
            Some((
                f.id.as_str(),
                location(analysis, f).ok()?,
                outline(analysis, f).ok()?.deep,
            ))
        })
        .collect();
    if at.len() < 2 {
        return marks.to_vec();
    }
    marks
        .iter()
        .map(|m| {
            let mut m = m.clone();
            let shared: Vec<&str> = m
                .claims
                .iter()
                .filter(|c| c.parameter == PARAMETER)
                .filter_map(|c| at.iter().find(|(id, ..)| *id == c.feature))
                .flat_map(|(id, (axis, d), deep)| {
                    at.iter()
                        .filter(move |(o, (k, e), s)| {
                            o != id && k == axis && s == deep && (e - d).abs() <= 1e-6
                        })
                        .map(|(o, ..)| *o)
                })
                .collect();
            for id in shared {
                if !m
                    .claims
                    .iter()
                    .any(|c| c.feature == id && c.parameter == PARAMETER)
                {
                    m.claims.push(crate::output::Claim {
                        feature: id.into(),
                        parameter: PARAMETER.into(),
                    });
                }
            }
            m
        })
        .collect()
}

/// The slot coverage findings: the lone slots' requirements, each slot pattern's, and each slot
/// pattern whose requirements cannot be read as one requirement that cannot be checked.
pub fn coverage(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let shared = shared_locations(analysis, marks);
    let frame = frame(analysis);
    let noun = |f: &Feature| {
        let p = &f.parameters;
        let mut s = format!("slot {}", f.id);
        if let (Ok(w), Ok(l)) = (parameters::size(f, "width"), parameters::size(f, "length")) {
            s += &format!(" {} × {}", fmt(w), fmt(l));
        }
        if let Ok(r) = parameters::size(f, "end_radius") {
            s += &format!(", ends R{}", fmt(r));
        }
        if let Some(a) = parameters::direction(&frame, p.get("long_axis")) {
            s += &format!(", long along {}", fmt3(a));
        }
        s
    };
    let mut lone = wanted(analysis);
    lone.retain(|w| {
        w.parameter != "slot_end_radius.radius"
            || !slot_callout_states_ends(&w.feature.id, w.feature, marks)
    });
    // The drawing says where a slot is located from: by its centre when a slot callout states
    // its size (as the planner draws a group), else by its low end (a sheet that dimensions it
    // singly, Python draftwright's among them).
    for w in lone
        .iter_mut()
        .filter(|w| w.parameter == "location_slot.length")
    {
        let centre = slot_callout_claims(&w.feature.id, marks);
        if centre != grouped(analysis, w.feature) {
            let at = location_to(analysis, w.feature, centre);
            w.callout = at.as_ref().map_or("its location".into(), |(axis, v)| {
                let from = if centre {
                    "centre"
                } else if ends_located(analysis, w.feature) {
                    "low end's centre"
                } else {
                    "low end"
                };
                format!("its {from} {} from the part's {axis} edge", fmt(*v))
            });
            w.values = at.map(|(_, v)| vec![v]);
        }
    }
    let mut issues = parameters::coverage(
        "slot",
        &lone,
        &shared,
        noun,
        "a linear dimension or radius callout, in the view that sees the slot's outline",
    );
    let (patterns, _) = split(analysis);
    for p in patterns {
        let why = match pattern_wanted(analysis, p) {
            Ok(mut wanted) => {
                // A pattern's member's full-round ends are stated by the pattern's slot callout.
                if members(analysis, p)
                    .first()
                    .is_some_and(|m| slot_callout_states_ends(&p.id, m, marks))
                {
                    wanted.retain(|w| w.parameter != "slot_end_radius.radius");
                }
                let noun = |f: &Feature| {
                    let mut s = format!("slot pattern {}", f.id);
                    if let Some(n) = f.parameters.get("slots").and_then(Value::as_array) {
                        s += &format!(" of {} slots", n.len());
                    }
                    s
                };
                issues.extend(parameters::coverage(
                    "slot",
                    &wanted,
                    marks,
                    noun,
                    "a grouped slot callout, pitch or location dimension, in the view that looks through the slots",
                ));
                continue;
            }
            Err(why) => why,
        };
        issues.push(LintIssue {
            sheet: None,
            id: format!("l:slot_requirement_unverifiable:{}/?", p.id),
            code: "slot_requirement_unverifiable".into(),
            severity: Severity::Warning,
            message: format!(
                "slot pattern {}: its requirements cannot be checked: {why}",
                p.id
            ),
            at: None,
            view: None,
            subjects: vec![p.id.clone()],
            requirements: vec![Requirement {
                feature: p.id.clone(),
                parameter: "?".into(),
            }],
            measurements: Vec::new(),
            sources: Vec::new(),
            stage: None,
            annotation: None,
            related: Vec::new(),
            evidence: Some(why),
            fix: None,
        });
    }
    issues
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::output::Claim;
    use serde_json::{Value, json};

    fn part(features: &[(&str, &[usize], Value)]) -> Analysis {
        let features: Vec<Value> = features
            .iter()
            .enumerate()
            .map(|(i, (kind, faces, r))| {
                json!({
                    "id": format!("f:{kind}:{}", i + 1), "kind": kind, "faces": faces,
                    "parameters": r,
                    "fingerprint": { "version": "test", "family": "slots", "subtype": "Slot",
                                     "sizes": {}, "position": [0.0, 0.0, 0.0],
                                     "neighbourhood": [] },
                })
            })
            .collect();
        serde_json::from_value(json!({
            "schema": "draftwright.analysis/2",
            "binding": { "source_sha256": "", "part": "test", "face_count": 9, "loader": "test" },
            "envelope": [[0.0, 0.0, 0.0], [100.0, 100.0, 10.0]],
            "features": features,
            "iso_bounds": [0.0, 0.0, 0.0, 0.0],
        }))
        .unwrap()
    }

    fn slot(end_radius: Option<f64>) -> Value {
        json!({ "width": 6.0, "length": 14.97, "end_radius": end_radius, "long_axis": "z",
                "width_axis": "y", "lo": -110.93, "hi": -95.96, "w_center": 41.5,
                "d_lo": -13.74, "d_hi": -11.74 })
    }

    /// LINT-slot_coverage-lone_slot: width, length and location, and the end radius only when
    /// quiddity proved the ends are arcs. cgb245's slots, in its frame (quiddity's z is the
    /// part's x) and box (from x = -190): stated as `6`, `15.0`, `2× R3` and `79.1`, the low
    /// end's offset from the box along the long axis.
    #[test]
    fn a_lone_slot_states_its_width_length_location_and_proven_end_radius() {
        // The second slot is cgb245's other one, at the far end: the first's location does not
        // state it (one span, one dimension shares only a coincident end).
        let mut far = slot(None);
        far["lo"] = json!(95.96);
        far["hi"] = json!(110.93);
        let mut a = part(&[("slot", &[0], slot(Some(3.0))), ("slot", &[1], far)]);
        a.recognition = Some(
            serde_json::from_value(json!({
                "recogniser": "test",
                "frame": [[-0.000128, 7.68098, -3.64814], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0],
                          [1.0, 0.0, 0.0]],
            }))
            .unwrap(),
        );
        a.envelope = [[-190.000128, -84.0, -18.6], [190.0, 99.0, 6.6]];
        let w = wanted(&a);
        let names: Vec<String> = w
            .iter()
            .map(|w| format!("{} {}", w.feature.id, w.parameter))
            .collect();
        assert_eq!(
            names,
            [
                "f:slot:1 slot_width.length",
                "f:slot:1 slot_length.length",
                "f:slot:1 slot_end_radius.radius",
                "f:slot:1 location_slot.length",
                "f:slot:1 location_slot_across.length",
                "f:slot:2 slot_width.length",
                "f:slot:2 slot_length.length",
                "f:slot:2 location_slot.length",
                "f:slot:2 location_slot_across.length"
            ]
        );
        assert_eq!(
            location(&a, w[0].feature).map(|(k, v)| (k, fmt(v))),
            Ok(("x", "79.07".into()))
        );
        let mark = |text: &str, f: &str, p: &str| Mark {
            name: format!("m:{f}:{p}"),
            view: Some("v:plan".into()),
            text: text.into(),
            claims: vec![Claim {
                feature: f.into(),
                parameter: p.into(),
            }],
        };
        let marks = [
            mark("6", "f:slot:1", "slot_width.length"),
            mark("15.0", "f:slot:1", "slot_length.length"),
            mark("2× R3", "f:slot:1", "slot_end_radius.radius"),
            mark("79.1", "f:slot:1", "location_slot.length"),
            mark("6", "f:slot:2", "slot_width.length"),
            mark("15.1", "f:slot:2", "slot_length.length"),
            // The transverse locations are independent requirements too (#319).
            mark("50.181", "f:slot:1", "location_slot_across.length"),
            mark("50.181", "f:slot:2", "location_slot_across.length"),
        ];
        let found: Vec<String> = coverage(&a, &marks)
            .into_iter()
            .map(|i| format!("{} {}", i.code, i.requirements[0].parameter))
            .collect();
        assert_eq!(
            found,
            [
                "slot_requirement_unverifiable slot_length.length",
                "slot_requirement_missing location_slot.length"
            ]
        );
        // A long axis along no axis of the part: the location cannot be checked.
        let first = w[0].feature.clone();
        a.recognition.as_mut().unwrap().frame[3] = [0.6, 0.8, 0.0];
        assert_eq!(
            location(&a, &first),
            Err("its long axis lies along no axis of the part".into())
        );
    }

    /// quiddity rounds a slot's `lo`, `hi` and `length` to 0.01 mm each, so its recorded `lo`
    /// (95.96) and the low end the planner draws (the exact centre of its faces, 103.444798, less
    /// half of 14.96: 95.9648) can fall either side of a 0.005 tie, as on cgb245. Lint judges the
    /// drawn end: `95.965` states the location and `95.960` (the recorded end) does not.
    #[test]
    fn a_slot_location_is_judged_at_the_end_the_planner_draws() {
        let mut a = part(&[(
            "slot",
            &[0],
            json!({ "width": 6.0, "length": 14.96, "end_radius": 3.0, "long_axis": "x",
                    "width_axis": "y", "lo": 95.96, "hi": 110.93, "w_center": 50.0,
                    "d_lo": 0.0, "d_hi": 10.0 }),
        )]);
        a.features[0].fingerprint.position = [103.444798, 50.0, 5.0];
        let f = &a.features[0];
        let (axis, at) = location(&a, f).unwrap();
        assert_eq!(axis, "x");
        assert_eq!(at, outline(&a, f).unwrap().ends[0] - a.envelope[0][0]);
        assert_eq!(fmt(at), "95.965");
        let unverified = |text: &str| -> Vec<String> {
            let marks = [Mark {
                name: "m:f:slot:1:location_slot.length".into(),
                view: Some("v:plan".into()),
                text: text.into(),
                claims: vec![Claim {
                    feature: "f:slot:1".into(),
                    parameter: "location_slot.length".into(),
                }],
            }];
            coverage(&a, &marks)
                .into_iter()
                .filter(|i| i.requirements[0].parameter == "location_slot.length")
                .map(|i| i.code)
                .collect()
        };
        assert!(unverified("95.965").is_empty());
        assert_eq!(unverified("95.960"), ["slot_requirement_unverifiable"]);
    }

    /// #228: two lone slots whose low ends lie 10 from the part's x face, both seen along z, are
    /// located by one dimension (ARCH0009-6) that claims only the first; it states the second's
    /// location too. A slot whose low end is elsewhere, or one cut through y (another view), is
    /// not stated by it, nor is a line whose text misstates the span. The slots differ in length,
    /// so none is called out with congruent slots ([`grouped`]) and located by its centre.
    #[test]
    fn one_position_line_states_every_slot_on_its_span() {
        let slot = |lo: f64, length: f64, wide: &str, w: f64, d: [f64; 2]| {
            json!({ "width": 4.5, "length": length, "end_radius": 2.25, "long_axis": "x",
                    "width_axis": wide, "lo": lo, "hi": lo + length, "w_center": w,
                    "d_lo": d[0], "d_hi": d[1] })
        };
        let a = part(&[
            ("slot", &[0], slot(10.0, 7.5, "y", 20.0, [0.0, 10.0])),
            ("slot", &[1], slot(10.0, 8.5, "y", 60.0, [0.0, 10.0])),
            ("slot", &[2], slot(12.0, 9.5, "y", 80.0, [0.0, 10.0])),
            ("slot", &[3], slot(10.0, 10.5, "z", 5.0, [40.0, 50.0])),
        ]);
        assert!(a.features.iter().all(|f| !grouped(&a, f)));
        let missing = |text: &str| -> Vec<String> {
            let marks = [Mark {
                name: "m:f:slot:1:location_slot.length".into(),
                view: Some("v:plan".into()),
                text: text.into(),
                claims: vec![Claim {
                    feature: "f:slot:1".into(),
                    parameter: "location_slot.length".into(),
                }],
            }];
            coverage(&a, &marks)
                .into_iter()
                .filter(|i| i.requirements[0].parameter == "location_slot.length")
                .map(|i| format!("{} {}", i.code, i.subjects[0]))
                .collect()
        };
        assert_eq!(
            missing("10"),
            [
                "slot_requirement_missing f:slot:3",
                "slot_requirement_missing f:slot:4"
            ]
        );
        assert_eq!(
            missing("11"),
            [
                "slot_requirement_unverifiable f:slot:1",
                "slot_requirement_unverifiable f:slot:2",
                "slot_requirement_missing f:slot:3",
                "slot_requirement_missing f:slot:4"
            ]
        );
    }

    /// nema17-bracket: congruent lone slots (4.5 × 7.5, ends R2.25, seen along z), however each
    /// runs, are dimensioned together by one `N× SLOT W × L` callout and located by their
    /// centres. Their full-round ends are stated by the callout that claims their width; a width
    /// alone does not state them. A slot of another length, or seen through another axis, is not
    /// in the group and keeps its low-end location.
    #[test]
    fn congruent_slots_are_located_by_their_centres_and_a_slot_callout_states_their_ends() {
        let slot = |long: &str, wide: &str, lo: f64, length: f64, w: f64, d: [f64; 2]| {
            json!({ "width": 4.5, "length": length, "end_radius": 2.25, "long_axis": long,
                    "width_axis": wide, "lo": lo, "hi": lo + length, "w_center": w,
                    "d_lo": d[0], "d_hi": d[1] })
        };
        let a = part(&[
            ("slot", &[0], slot("y", "x", 10.0, 7.5, 10.0, [0.0, 3.0])),
            ("slot", &[1], slot("x", "y", 16.25, 7.5, 5.0, [0.0, 3.0])),
            ("slot", &[2], slot("x", "y", 40.0, 9.5, 50.0, [0.0, 3.0])),
            ("slot", &[3], slot("x", "z", 60.0, 7.5, 5.0, [40.0, 50.0])),
        ]);
        let together: Vec<bool> = a.features.iter().map(|f| grouped(&a, f)).collect();
        assert_eq!(together, [true, true, false, false]);
        let at = |i: usize| location(&a, &a.features[i]).map(|(k, v)| (k, fmt(v)));
        assert_eq!(at(0), Ok(("y", "13.75".into())));
        assert_eq!(at(1), Ok(("x", "20".into())));
        assert_eq!(at(2), Ok(("x", "40".into())));
        let mark = |name: &str, text: &str, claims: &[(&str, &str)]| Mark {
            name: name.into(),
            view: Some("v:plan".into()),
            text: text.into(),
            claims: claims
                .iter()
                .map(|(f, p)| Claim {
                    feature: (*f).into(),
                    parameter: (*p).into(),
                })
                .collect(),
        };
        let group = |text: &str| {
            mark(
                "d:f:slot:1/slot_width.length",
                text,
                &[
                    ("f:slot:1", "slot_width.length"),
                    ("f:slot:2", "slot_width.length"),
                    ("f:slot:1", "slot_length.length"),
                    ("f:slot:2", "slot_length.length"),
                ],
            )
        };
        let found = |marks: &[Mark]| -> Vec<String> {
            coverage(&a, marks)
                .into_iter()
                .filter(|i| ["f:slot:1", "f:slot:2"].contains(&i.subjects[0].as_str()))
                .map(|i| {
                    format!(
                        "{} {} {}",
                        i.code, i.subjects[0], i.requirements[0].parameter
                    )
                })
                .collect()
        };
        let located = [
            mark("a", "13.75", &[("f:slot:1", "location_slot.length")]),
            mark("b", "20", &[("f:slot:2", "location_slot.length")]),
            // Keep the complete fixture located across as well as along (#319).
            mark("c", "10", &[("f:slot:1", "location_slot_across.length")]),
            mark("d", "5", &[("f:slot:2", "location_slot_across.length")]),
        ];
        let mut marks = located.to_vec();
        marks.push(group("2× SLOT 4.5 × 7.5"));
        assert_eq!(found(&marks), Vec::<String>::new());
        // The low end no longer locates a grouped slot.
        marks[0].text = "10".into();
        assert_eq!(
            found(&marks),
            ["slot_requirement_unverifiable f:slot:1 location_slot.length"]
        );
        // A counted width and length state the size but not the ends, and a sheet without a slot
        // callout locates each slot by its low end (as draftwright's does), not its centre.
        let mut marks = located.to_vec();
        marks.push(group("2× 4.5 7.5"));
        assert_eq!(
            found(&marks),
            [
                "slot_requirement_missing f:slot:1 slot_end_radius.radius",
                "slot_requirement_unverifiable f:slot:1 location_slot.length",
                "slot_requirement_missing f:slot:2 slot_end_radius.radius",
                "slot_requirement_unverifiable f:slot:2 location_slot.length"
            ]
        );
    }

    /// gantry-plate: a grouped slot's centre stands on a hole row's location line, so the planner
    /// omits the slot's location as conveyed by the hole's (ARCH0009-6); that line, in the slot's
    /// view, on its axis and stating its distance, locates it. One on another axis, in another
    /// view, or stating another distance does not.
    #[test]
    fn a_grouped_slots_centre_on_another_features_location_line_is_located_by_it() {
        let slot = |w: f64| {
            json!({ "width": 5.0, "length": 25.0, "end_radius": 2.5, "long_axis": "x",
                    "width_axis": "y", "lo": 20.25, "hi": 45.25, "w_center": w,
                    "d_lo": 0.0, "d_hi": 3.0 })
        };
        let a = part(&[("slot", &[0], slot(12.75)), ("slot", &[1], slot(52.75))]);
        let missing = |view: &str, text: &str, parameter: &str| -> Vec<String> {
            let claim = |feature: &str, parameter: &str| Claim {
                feature: feature.into(),
                parameter: parameter.into(),
            };
            let mut marks = [
                Mark {
                    name: "d:f:hole:1/location.length.x".into(),
                    view: Some(view.into()),
                    text: text.into(),
                    claims: vec![claim("f:hole:1", parameter)],
                },
                Mark {
                    name: "d:f:slot:1/slot_width.length".into(),
                    view: Some("v:plan".into()),
                    text: "2× SLOT 5 × 25".into(),
                    claims: vec![
                        claim("f:slot:1", "slot_width.length"),
                        claim("f:slot:2", "slot_width.length"),
                    ],
                },
            ];
            conveyed_locations(&a, &mut marks);
            coverage(&a, &marks)
                .into_iter()
                .filter(|i| i.requirements[0].parameter == "location_slot.length")
                .map(|i| i.subjects[0].clone())
                .collect()
        };
        assert!(missing("v:plan", "32.75", "location.length.x").is_empty());
        let both = ["f:slot:1", "f:slot:2"];
        assert_eq!(missing("v:plan", "32.75", "location.length.y"), both);
        assert_eq!(missing("v:front", "32.75", "location.length.x"), both);
        assert_eq!(missing("v:plan", "20.25", "location.length.x"), both);
    }

    #[test]
    fn table_ordinates_locate_only_their_named_feature() {
        let a = part(&[(
            "slot",
            &[0],
            json!({
                "width": 8., "length": 30., "end_radius": 4.,
                "long_axis": "x", "width_axis": "y", "lo": 20., "hi": 50.,
                "w_center": 40., "d_lo": 0., "d_hi": 10.
            }),
        )]);
        for name in ["hole_schedule_1/y", "hole_table_plan/1/y"] {
            let mut marks = [Mark {
                name: name.into(),
                view: Some("v:plan".into()),
                text: "40".into(),
                claims: vec![Claim {
                    feature: "f:hole:2".into(),
                    parameter: "location.length.y".into(),
                }],
            }];
            conveyed_locations(&a, &mut marks);
            assert_eq!(marks[0].claims.len(), 1, "{name} has no slot witness");
            let across_missing = |marks: &[Mark]| {
                coverage(&a, marks).iter().any(|i| {
                    i.code == "slot_requirement_missing"
                        && i.requirements.iter().any(|r| {
                            r.feature == "f:slot:1" && r.parameter == "location_slot_across.length"
                        })
                })
            };
            assert!(across_missing(&marks));
            marks[0].claims.push(Claim {
                feature: "f:slot:1".into(),
                parameter: "location_slot_across.length".into(),
            });
            conveyed_locations(&a, &mut marks);
            assert!(
                !across_missing(&marks),
                "a cell about the slot itself locates it"
            );
            assert_eq!(
                marks[0].claims.len(),
                2,
                "a statement about the slot survives"
            );
        }
    }

    #[test]
    fn a_transverse_slot_location_requires_the_actual_near_edge_span() {
        let a = part(&[
            (
                "slot",
                &[0],
                json!({
                    "width": 8., "length": 30., "end_radius": 4.,
                    "long_axis": "x", "width_axis": "y", "lo": 20., "hi": 50.,
                    "w_center": 40., "d_lo": 0., "d_hi": 10.
                }),
            ),
            ("hole", &[1], json!({})),
        ]);
        let claimed = |ends: Option<[f64; 2]>, view: &str| {
            let mut m = Mark {
                name: "hole-ordinate".into(),
                view: Some(view.into()),
                text: "40".into(),
                claims: vec![
                    Claim {
                        feature: "f:hole:2".into(),
                        parameter: "location.length.y".into(),
                    },
                    Claim {
                        feature: "station_ink".into(),
                        parameter: "witnesses".into(),
                    },
                ],
            };
            if let Some([p, q]) = ends {
                m.claims.push(Claim {
                    feature: super::super::location::span_claim(1, p, q),
                    parameter: "station.distance".into(),
                });
            }
            let mut marks = [m];
            conveyed_locations(&a, &mut marks);
            marks[0]
                .claims
                .iter()
                .any(|c| c.feature == "f:slot:1" && c.parameter == "location_slot_across.length")
        };
        assert!(claimed(Some([0., 40.]), "v:plan"));
        assert!(claimed(Some([40., 0.]), "v:plan"));
        // Same number and axis, but measured from the opposite envelope edge.
        assert!(!claimed(Some([60., 100.]), "v:plan"));
        assert!(!claimed(Some([10., 50.]), "v:plan"));
        assert!(!claimed(None, "v:plan"));
        assert!(!claimed(Some([0., 40.]), "v:front"));
    }

    /// LINT-slot_coverage-slot_pattern: a linear array of three obround slots has its member's
    /// width, length and end radius, its pitch, and its centroid's x and y location as
    /// requirements (five and the radius); a grid has its column and row pitches instead of the
    /// pitch, and its centre is quiddity's. A pattern cut along anything but z, or of slots that
    /// are not identical, is one requirement that cannot be checked.
    #[test]
    fn a_slot_pattern_states_its_member_pitches_and_location() {
        let member = |w_center: f64| {
            json!({ "width": 6.0, "length": 20.0, "end_radius": 3.0, "long_axis": "x",
                    "width_axis": "y", "lo": 10.0, "hi": 30.0, "w_center": w_center,
                    "d_lo": 0.0, "d_hi": 10.0 })
        };
        let slots = json!(["f:slot:1", "f:slot:2", "f:slot:3"]);
        let array = json!({ "slots": slots, "pitch": 25.0, "direction": [0.0, 1.0, 0.0] });
        let mut a = part(&[
            ("slot", &[0], member(20.0)),
            ("slot", &[1], member(45.0)),
            ("slot", &[2], member(70.0)),
            ("slot_pattern", &[0, 1, 2], array),
        ]);
        let wanted = |a: &Analysis| -> Result<Vec<String>, String> {
            let p = a
                .features
                .iter()
                .find(|f| f.kind == "slot_pattern")
                .unwrap();
            Ok(pattern_wanted(a, p)?
                .iter()
                .map(|w| format!("{} {:?}", w.parameter, w.values.as_ref().unwrap()))
                .collect())
        };
        assert_eq!(
            wanted(&a).unwrap(),
            [
                "slot_width.length [6.0]",
                "slot_length.length [20.0]",
                "slot_end_radius.radius [3.0]",
                "pitch.length [25.0]",
                "location_slot_pattern.length.x [20.0]",
                "location_slot_pattern.length.y [45.0]"
            ]
        );
        let mark = |text: &str, ps: &[&str]| Mark {
            name: format!("m:{text}"),
            view: Some("v:plan".into()),
            text: text.into(),
            claims: ps
                .iter()
                .map(|p| Claim {
                    feature: "f:slot_pattern:4".into(),
                    parameter: p.to_string(),
                })
                .collect(),
        };
        let mut marks = vec![
            mark(
                "3× SLOT 6 × 20",
                &["slot_width.length", "slot_length.length"],
            ),
            mark("2× R3", &["slot_end_radius.radius"]),
            mark("2× 25", &["pitch.length"]),
            mark("20", &["location_slot_pattern.length.x"]),
            mark("45", &["location_slot_pattern.length.y"]),
        ];
        let found = |a: &Analysis, marks: &[Mark]| -> Vec<String> {
            coverage(a, marks)
                .into_iter()
                .map(|i| format!("{} {}", i.code, i.requirements[0].parameter))
                .collect()
        };
        assert!(found(&a, &marks).is_empty(), "{:?}", found(&a, &marks));
        marks[2].text = "2× 24".into();
        marks.remove(4);
        assert_eq!(
            found(&a, &marks),
            [
                "slot_requirement_unverifiable pitch.length",
                "slot_requirement_missing location_slot_pattern.length.y"
            ]
        );

        // A 3×1 grid about (20, 45): the column and row pitches.
        a.features[3].parameters = json!({ "slots": slots, "rows": 3, "cols": 1, "angle": 0.0,
                                           "col_pitch": 30.0, "row_pitch": 25.0,
                                           "center": [20.0, 45.0, 5.0] })
        .as_object()
        .unwrap()
        .clone();
        let names: Vec<String> = wanted(&a).unwrap()[3..].to_vec();
        assert_eq!(
            names,
            [
                "grid_pitch.length.col [30.0]",
                "grid_pitch.length.row [25.0]",
                "location_slot_pattern.length.x [20.0]",
                "location_slot_pattern.length.y [45.0]"
            ]
        );

        // Slots that differ, or a pattern cut along y.
        a.features[1].parameters["length"] = json!(21.0);
        assert_eq!(
            wanted(&a),
            Err("its slots are not identical (f:slot:1 and f:slot:2)".into())
        );
        for f in &mut a.features[..3] {
            f.parameters["length"] = json!(20.0);
            f.parameters["width_axis"] = json!("z");
        }
        assert!(wanted(&a).unwrap_err().starts_with("it is cut along y"));
        assert_eq!(found(&a, &marks), ["slot_requirement_unverifiable ?"]);
    }

    /// A pattern's members are the pattern's: no lone-slot requirement, and a pattern whose
    /// requirements cannot be read (here, it names no member slots) is reported unverifiable
    /// rather than left out.
    #[test]
    fn a_slot_patterns_members_are_not_lone_slots() {
        let a = part(&[
            ("slot", &[0, 1], slot(None)),
            ("slot", &[2, 3], slot(None)),
            ("slot", &[5], slot(None)),
            ("slot_pattern", &[0, 1, 2, 3], json!({ "pitch": 20.0 })),
        ]);
        let found: Vec<String> = coverage(&a, &[])
            .into_iter()
            .map(|i| {
                format!(
                    "{} {} {}",
                    i.code, i.subjects[0], i.requirements[0].parameter
                )
            })
            .collect();
        // The remaining lone slot needs both location axes (#319); pattern
        // members still contribute no separate lone-slot requirements.
        assert_eq!(
            found,
            [
                "slot_requirement_missing f:slot:3 slot_width.length",
                "slot_requirement_missing f:slot:3 slot_length.length",
                "slot_requirement_missing f:slot:3 location_slot.length",
                "slot_requirement_missing f:slot:3 location_slot_across.length",
                "slot_requirement_unverifiable f:slot_pattern:4 ?"
            ]
        );
    }
}
