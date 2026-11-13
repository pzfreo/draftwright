//! quiddity's hole records, read once for every reader of the Analysis: the sheet demand
//! ([`crate::demand`]), the dimensions ([`crate::dims`]) and lint ([`crate::lint`]). It reads the
//! Analysis alone, so lint may use it and still never read the Plan or the Spec.
//!
//! - [`Hole`] is a hole record, typed and in the part's frame;
//! - [`principal`] is the one rule for a direction lying along x, y or z;
//! - [`size_key`] is the one resolution sizes are compared at, and [`Hole::requirement`] the
//!   machining requirement identical holes share;
//! - [`established`] (and [`established_with`], given the [`bosses`]) says whether a recognised
//!   hole pattern is one requirement;
//! - [`frame`], [`to_part`], [`dir_to_part`] and [`point`] read a record's coordinates into the
//!   part's frame, for every family.
//!
//! The record's fields are quiddity's (`holes.py`'s `HoleRecord`, `countersinks.py`'s
//! `CounterSink`): a countersink's sizes are `major_diameter` and `included_angle`.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{Map, Value};

use super::{Feature, HolePattern};
use crate::model::Analysis;

/// The identity frame: quiddity's coordinates when the Analysis carries no recognition frame.
const IDENTITY: [[f64; 3]; 4] = [[0.0; 3], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]];

/// A counterbore or spotface: its diameter, and its depth when quiddity gives one.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Recess {
    pub diameter: f64,
    /// The cylinder's own length, as quiddity measures it (an entry cone is not in it).
    pub depth: Option<f64>,
    /// The axial depth of a mouth chamfer cut into this recess's mouth ([`MouthChamfer`]), or 0.
    /// A depth read from the surface the mouth opens on includes it ([`Recess::from_mouth`]).
    pub entry: f64,
}

impl Recess {
    /// Its depth from the surface its mouth opens on: the cylinder's length and, when a mouth
    /// chamfer is cut into its mouth, the chamfer's axial depth (quiddity-rust#244: ⌴⌀5 ↧ 2.25
    /// for a 2.0 mm cylinder under a 0.25 mm entry). The one depth a drawing states.
    pub fn from_mouth(&self) -> Option<f64> {
        self.depth.map(|d| d + self.entry)
    }
}

/// A conical chamfer on the mouth of a hole (quiddity's `mouth_chamfers`, quiddity-rust#244), a
/// nested claim on the hole: its cone is not one of the hole's own faces.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct MouthChamfer {
    /// The diameter of the cylinder whose mouth it breaks (the bore's, or a recess's).
    pub diameter: f64,
    /// Its radial size.
    pub size: f64,
    /// Its axial depth.
    pub depth: f64,
    /// Its angle (degrees) from the transverse face, not the cone's included angle.
    pub angle: f64,
    /// The direction out of the hole at the end it breaks, unit, in the part's frame.
    pub outward: [f64; 3],
    /// Whether it breaks the hole's opening, where quiddity records the hole and its recesses
    /// (its axis, which points into the hole, runs with the hole's own), rather than the other
    /// end of a through hole.
    pub at_opening: bool,
}

impl MouthChamfer {
    /// Whether it is called out `C<size>`, as every other chamfer is
    /// ([`crate::dims::c_form`]): equal legs at 45°.
    pub fn is_c_form(&self) -> bool {
        crate::dims::c_form(self.size, self.depth, self.angle)
    }

    /// Whether *other* is the same chamfer (size and angle) at the other end.
    pub fn matches(&self, other: &MouthChamfer) -> bool {
        (self.size - other.size).abs() < 1e-3 && (self.angle - other.angle).abs() < 1e-2
    }
}

/// A countersink: its major diameter, and its included angle (degrees) when quiddity gives one.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Countersink {
    pub diameter: f64,
    pub angle: Option<f64>,
}

/// A drilling's requirement and the line its axis lies on ([`Hole::segment`]).
type Drilling = (Vec<i64>, [i64; 3]);
/// A segment of a drilling: the stretch of its line it spans, its bore and its ID.
type Segment<'a> = ([f64; 2], f64, &'a str);

/// A recognised hole, in the part's frame.
#[derive(Clone, Debug, PartialEq)]
pub struct Hole<'a> {
    pub feature: &'a Feature,
    /// Its location (the opening's centre, on its axis).
    pub at: [f64; 3],
    /// Its drilling direction as quiddity orients it, unit length.
    pub direction: [f64; 3],
    /// The principal axis (0, 1, 2) it lies along and its sign along it; none when oblique.
    pub principal: Option<(usize, i8)>,
    pub diameter: f64,
    /// A fact of the feature (`bottom: through`), never read from a missing depth (ADR4-7).
    pub through: bool,
    /// quiddity's bottom (`through`, `flat`, `drill_point`, …), if it gives one.
    pub bottom: Option<&'a str>,
    /// The bore's depth (for a through hole, the thickness it passes through), if given.
    pub depth: Option<f64>,
    /// The innermost counterbore-class step (quiddity's `cbore`).
    pub counterbore: Option<Recess>,
    /// The larger counterbore-class steps outside it, outermost first, each depth its own
    /// mouth-to-floor span (quiddity's `outer_steps`, quiddity-rust#118); empty when there are
    /// none.
    pub outer_steps: Vec<Recess>,
    pub spotface: Option<Recess>,
    pub countersink: Option<Countersink>,
    /// Its mouth chamfers, the opening's first.
    pub mouth_chamfers: Vec<MouthChamfer>,
    /// quiddity's frame it was read through, for its pattern's points.
    pub frame: [[f64; 3]; 4],
}

/// The axis of a round feature a bolt circle can be concentric with (a boss's), in the part's
/// frame: a point on it and its direction.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Axis {
    pub at: [f64; 3],
    pub direction: [f64; 3],
}

/// The principal axis a direction lies along, if it does: draftwright's `_is_principal_axis`,
/// exactly one component of the unit direction left after snapping those under 1e-6 to zero.
pub fn principal(d: [f64; 3]) -> Option<usize> {
    let n = (d[0] * d[0] + d[1] * d[1] + d[2] * d[2]).sqrt();
    if n == 0.0 {
        return None;
    }
    let along: Vec<usize> = (0..3).filter(|&k| (d[k] / n).abs() >= 1e-6).collect();
    (along.len() == 1).then(|| along[0])
}

/// A length, angle or direction component as sizes are compared: to 1e-6, quiddity's own
/// identity (`HoleSpec`) less floating-point noise.
pub fn size_key(x: f64) -> i64 {
    (x * 1e6).round() as i64
}

/// A record's point or vector field, if it is three numbers.
pub fn point(v: Option<&Value>) -> Option<[f64; 3]> {
    let a = v?.as_array()?;
    Some([
        a.first()?.as_f64()?,
        a.get(1)?.as_f64()?,
        a.get(2)?.as_f64()?,
    ])
}

/// quiddity's working frame in the part's: the recognition record's, or the identity when the
/// Analysis has none.
pub fn frame(analysis: &Analysis) -> [[f64; 3]; 4] {
    analysis.recognition.as_ref().map_or(IDENTITY, |r| r.frame)
}

/// quiddity's local point *l* in the part's frame.
pub fn to_part(frame: &[[f64; 3]; 4], l: [f64; 3]) -> [f64; 3] {
    [0, 1, 2].map(|k| frame[0][k] + (0..3).map(|j| l[j] * frame[j + 1][k]).sum::<f64>())
}

/// quiddity's local direction *d* in the part's frame.
pub fn dir_to_part(frame: &[[f64; 3]; 4], d: [f64; 3]) -> [f64; 3] {
    [0, 1, 2].map(|k| (0..3).map(|j| d[j] * frame[j + 1][k]).sum::<f64>())
}

/// A record's `mouth_chamfers` (`{axis, diameter, size, depth, angle, …}` each): empty when
/// absent, an error when unreadable. The opening's come first.
fn mouth_chamfers(
    p: &Map<String, Value>,
    axis: [f64; 3],
    frame: &[[f64; 3]; 4],
) -> Result<Vec<MouthChamfer>, String> {
    let mut out = match p.get("mouth_chamfers") {
        None | Some(Value::Null) => Vec::new(),
        Some(Value::Array(entries)) => entries
            .iter()
            .map(|entry| {
                let r = entry
                    .as_object()
                    .ok_or_else(|| format!("its mouth chamfer {entry} is not a record"))?;
                let size = |name: &str| {
                    r.get(name)
                        .and_then(Value::as_f64)
                        .filter(|v| v.is_finite() && *v > 0.0)
                        .ok_or_else(|| format!("its mouth chamfer has no {name}"))
                };
                let into = point(r.get("axis")).ok_or("its mouth chamfer has no axis")?;
                let out = dir_to_part(frame, into);
                let n = out.iter().map(|c| c * c).sum::<f64>().sqrt();
                if n == 0.0 {
                    return Err("its mouth chamfer's axis is a zero vector".into());
                }
                Ok(MouthChamfer {
                    diameter: size("diameter")?,
                    size: size("size")?,
                    depth: size("depth")?,
                    angle: size("angle")?,
                    outward: out.map(|c| -c / n),
                    at_opening: (0..3).map(|k| into[k] * axis[k]).sum::<f64>() > 0.0,
                })
            })
            .collect::<Result<Vec<_>, String>>()?,
        Some(v) => return Err(format!("its mouth_chamfers is {v}, not a list")),
    };
    out.sort_by_key(|c| !c.at_opening);
    Ok(out)
}

/// A recess or countersink record's two sizes (*first* required, *second* optional): none when
/// absent, an error when unreadable.
fn sized(
    p: &Map<String, Value>,
    name: &str,
    first: &str,
    second: &str,
) -> Result<Option<(f64, Option<f64>)>, String> {
    match p.get(name) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::Object(r)) => {
            let d = r
                .get(first)
                .and_then(Value::as_f64)
                .ok_or_else(|| format!("its {name} has no {first}"))?;
            Ok(Some((d, r.get(second).and_then(Value::as_f64))))
        }
        Some(v) => Err(format!("its {name} is {v}, not a record")),
    }
}

/// A record's `outer_steps` (`{diameter, depth}` each, outermost first): empty when absent, an
/// error when unreadable.
fn outer_steps(p: &Map<String, Value>) -> Result<Vec<Recess>, String> {
    match p.get("outer_steps") {
        None | Some(Value::Null) => Ok(Vec::new()),
        Some(Value::Array(steps)) => steps
            .iter()
            .map(|step| {
                let r = step
                    .as_object()
                    .ok_or_else(|| format!("its outer step {step} is not a record"))?;
                Ok(Recess {
                    diameter: r
                        .get("diameter")
                        .and_then(Value::as_f64)
                        .ok_or("its outer step has no diameter")?,
                    depth: r.get("depth").and_then(Value::as_f64),
                    entry: 0.0,
                })
            })
            .collect(),
        Some(v) => Err(format!("its outer_steps is {v}, not a list")),
    }
}

impl<'a> Hole<'a> {
    /// Reads hole feature *f*, whose coordinates are in quiddity's *frame*; an error says what
    /// cannot be read.
    pub fn read(f: &'a Feature, frame: &[[f64; 3]; 4]) -> Result<Hole<'a>, String> {
        let p = &f.parameters;
        let axis = point(p.get("axis")).ok_or("it has no axis")?;
        let location = point(p.get("location")).ok_or("it has no location")?;
        let d = dir_to_part(frame, axis);
        let n = (d[0] * d[0] + d[1] * d[1] + d[2] * d[2]).sqrt();
        if n == 0.0 {
            return Err("its axis is a zero vector".into());
        }
        let direction = d.map(|c| c / n);
        let bottom = p.get("bottom").and_then(Value::as_str);
        let recess = |name: &str| {
            sized(p, name, "diameter", "depth").map(|r| {
                r.map(|(diameter, depth)| Recess {
                    diameter,
                    depth,
                    entry: 0.0,
                })
            })
        };
        let chamfers = mouth_chamfers(p, axis, frame)?;
        // The opening's chamfer lengthens the depth of the recess whose mouth it breaks.
        let entered = |r: Option<Recess>| {
            r.map(|r| Recess {
                entry: chamfers
                    .iter()
                    .find(|c| c.at_opening && (c.diameter - r.diameter).abs() < 1e-3)
                    .map_or(0.0, |c| c.depth),
                ..r
            })
        };
        Ok(Hole {
            feature: f,
            at: to_part(frame, location),
            direction,
            principal: principal(direction).map(|k| (k, if direction[k] > 0.0 { 1 } else { -1 })),
            diameter: p
                .get("diameter")
                .and_then(Value::as_f64)
                .ok_or("it has no diameter")?,
            through: bottom == Some("through"),
            bottom,
            depth: p.get("depth").and_then(Value::as_f64),
            counterbore: entered(recess("cbore")?),
            outer_steps: outer_steps(p)?
                .into_iter()
                .map(|r| entered(Some(r)).unwrap_or(r))
                .collect(),
            spotface: entered(recess("spotface")?),
            countersink: sized(p, "csink", "major_diameter", "included_angle")?
                .map(|(diameter, angle)| Countersink { diameter, angle }),
            mouth_chamfers: chamfers,
            frame: *frame,
        })
    }

    /// What makes two holes one machining requirement (quiddity's `HoleSpec`, compared at
    /// [`size_key`]): the signed drilling direction, so holes from opposite faces stay apart
    /// (ISSUE-1229-a, ISSUE-1517-a, LINT-hole_coverage-opposed_bores_distinct); every size; and
    /// the depth only for a blind hole. Holes drilled the same way from faces at different
    /// heights are one requirement, as draftwright groups them.
    pub fn requirement(&self) -> Vec<i64> {
        let opt = |v: Option<f64>| v.map_or(i64::MIN, size_key);
        let mut k: Vec<i64> = self.direction.iter().map(|c| size_key(*c)).collect();
        k.extend([
            size_key(self.diameter),
            self.through as i64,
            if self.through { 0 } else { opt(self.depth) },
        ]);
        for r in [self.counterbore, self.spotface] {
            match r {
                Some(r) => k.extend([1, size_key(r.diameter), opt(r.depth)]),
                None => k.push(0),
            }
        }
        // Outer steps last, so a hole without them keeps the key it had before they existed.
        for r in &self.outer_steps {
            k.extend([2, size_key(r.diameter), opt(r.depth)]);
        }
        match self.countersink {
            Some(c) => k.extend([1, size_key(c.diameter), opt(c.angle)]),
            None => k.push(0),
        }
        // Mouth chamfers last, so a hole without them keeps the key it had before they existed.
        for c in &self.mouth_chamfers {
            k.extend([
                3,
                c.at_opening as i64,
                size_key(c.diameter),
                size_key(c.size),
                size_key(c.angle),
            ]);
        }
        k
    }

    /// The mouth chamfers a callout states: the one at the end its view looks at
    /// ([`Hole::toward_viewer`]), then the one at the other end unless it is the same chamfer
    /// (then the last says so: both ends).
    pub fn chamfers(&self) -> (Option<MouthChamfer>, Option<MouthChamfer>, bool) {
        let toward = self.toward_viewer();
        let seen = |c: &&MouthChamfer| (0..3).map(|k| c.outward[k] * toward[k]).sum::<f64>() >= 0.0;
        let near = self.mouth_chamfers.iter().find(seen).copied();
        let far = self.mouth_chamfers.iter().find(|c| !seen(c)).copied();
        match (near, far) {
            (Some(n), Some(f)) if n.matches(&f) => (near, None, true),
            _ => (near, far, false),
        }
    }

    /// The direction towards the viewer of the view its callout is drawn in ([`crate::dims`]:
    /// the view that looks along its axis).
    pub fn toward_viewer(&self) -> [f64; 3] {
        crate::dims::toward_viewer(self.direction)
    }

    /// Its recesses from the bore outward: the counterbore, the outer steps innermost first,
    /// then the spotface.
    pub fn recesses(&self) -> impl Iterator<Item = Recess> + '_ {
        self.counterbore
            .into_iter()
            .chain(self.outer_steps.iter().rev().copied())
            .chain(self.spotface)
    }

    /// The largest diameter it opens to: its bore's, or its widest recess's or countersink's.
    pub fn opening_diameter(&self) -> f64 {
        self.recesses()
            .map(|r| r.diameter)
            .chain(self.countersink.map(|c| c.diameter))
            .fold(self.diameter, f64::max)
    }

    /// The drilling a plain through hole is a segment of: its requirement and the line its axis
    /// lies on (where it crosses the plane through the origin square to it, to 1e-3 mm), with the
    /// stretch of that line it spans. Any other hole, or one whose depth is not given, has none.
    fn segment(&self) -> Option<(Drilling, [f64; 2])> {
        let plain =
            self.counterbore.is_none() && self.spotface.is_none() && self.countersink.is_none();
        let depth = self.depth.filter(|d| d.is_finite() && *d > 0.0)?;
        if !(self.through && plain) {
            return None;
        }
        let d = self.direction;
        let along: f64 = (0..3).map(|k| self.at[k] * d[k]).sum();
        let line = [0, 1, 2].map(|k| ((self.at[k] - along * d[k]) * 1e3).round() as i64);
        Some(((self.requirement(), line), [along, along + depth]))
    }
}

/// The holes among *holes* that continue another's drilling across a slit: plain through holes
/// of one requirement on one line whose segments are nearer each other than the bore is wide are
/// one hole that the slit splits (a clamp's cross hole, shf08's `hole:1` and `hole:4`), drilled
/// and counted once. Holes in walls farther apart (a clevis's two lugs) stay separate holes.
pub fn continuations<'h, 'a: 'h>(
    holes: impl IntoIterator<Item = &'h Hole<'a>>,
) -> BTreeSet<&'a str> {
    // Each drilling's segments: their spans, bores and IDs.
    let mut lines: BTreeMap<Drilling, Vec<Segment<'a>>> = BTreeMap::new();
    for h in holes {
        if let Some((line, span)) = h.segment() {
            lines
                .entry(line)
                .or_default()
                .push((span, h.diameter, h.feature.id.as_str()));
        }
    }
    let mut out = BTreeSet::new();
    for mut segments in lines.into_values() {
        segments.sort_by(|a, b| a.0[0].total_cmp(&b.0[0]));
        let mut reach = f64::NEG_INFINITY;
        for ([lo, hi], diameter, id) in segments {
            if lo - reach < diameter {
                out.insert(id);
            }
            reach = reach.max(hi);
        }
    }
    out
}

/// How many holes *holes* are as drawn and counted: a slit's [`continuations`] are not.
pub fn drillings<'h, 'a: 'h>(holes: impl IntoIterator<Item = &'h Hole<'a>> + Clone) -> usize {
    holes.clone().into_iter().count() - continuations(holes).len()
}

/// The Analysis's hole features, each read in the part's frame, or why it cannot be.
pub fn read(analysis: &Analysis) -> Vec<(&Feature, Result<Hole<'_>, String>)> {
    let frame = frame(analysis);
    analysis
        .features
        .iter()
        .filter(|f| f.kind == "hole")
        .map(|f| (f, Hole::read(f, &frame)))
        .collect()
}

/// The Analysis's bosses' axes (quiddity's `BossRecord`: `location`, `axis`), in the part's
/// frame; a boss whose record lacks either is left out, as it could not corroborate anything.
pub fn bosses(analysis: &Analysis) -> Vec<Axis> {
    let frame = frame(analysis);
    analysis
        .features
        .iter()
        .filter_map(|f| {
            let (place, direction) = match f.kind.as_str() {
                "boss" => ("location", "axis"),
                "turned_body" => ("axis_point", "axis"),
                _ => return None,
            };
            let at = to_part(&frame, point(f.parameters.get(place))?);
            let d = crate::lint::parameters::direction(&frame, f.parameters.get(direction))?;
            let n = (d[0] * d[0] + d[1] * d[1] + d[2] * d[2]).sqrt();
            (n > 0.0).then(|| Axis {
                at,
                direction: d.map(|c| c / n),
            })
        })
        .collect()
}

/// How far the axis of a round feature may pass from a bolt circle's centre and still be
/// concentric with it, mm: draftwright's `_BC_CONCENTRIC_TOL`, deliberately tight.
const CONCENTRIC: f64 = 0.5;

/// Whether recognised pattern *p* is one machining requirement, given the readable *holes*:
/// the indices of its members in *holes*, or why it is not. Its members must all be readable
/// holes, more than one, with one [`Hole::requirement`], drilled along an axis of the part, and
/// on distinct axis lines (draftwright refuses an oblique pattern plane and coaxial members,
/// in drafting and so in lint). A grid or linear pattern is not judged for its regularity
/// here: draftwright keeps quiddity's as one requirement whatever its spacing.
///
/// A bolt circle of fewer than five holes does not establish a circle by itself (three points,
/// or a rectangle's corners, always fit one: ISSUE-1596-a); it is one requirement only when
/// something round sits on its centre: a hole of *holes* that is not a member here, whose axis
/// is parallel to the members' and passes within 0.5 mm of quiddity's fitted centre
/// (draftwright's `bolt_circle_is_corroborated`, LINT-hole_coverage-bolt_circle_corroboration).
/// [`established_with`] counts bosses too.
pub fn established(p: &HolePattern, holes: &[&Hole]) -> Result<Vec<usize>, String> {
    established_with(p, holes, &[])
}

/// [`established`], with *bosses* (their axes, [`bosses`]) as round features a bolt circle of
/// fewer than five holes can be concentric with, as draftwright counts them.
pub fn established_with(
    p: &HolePattern,
    holes: &[&Hole],
    bosses: &[Axis],
) -> Result<Vec<usize>, String> {
    let members: Vec<usize> = p
        .members
        .iter()
        .map(|m| holes.iter().position(|h| &h.feature.id == m))
        .collect::<Option<_>>()
        .ok_or_else(|| {
            format!(
                "its pattern {} has a member that cannot be called out with it",
                p.id
            )
        })?;
    if members.len() < 2 {
        return Err(format!("its pattern {} has one member", p.id));
    }
    let first = holes[members[0]].requirement();
    if members.iter().any(|&m| holes[m].requirement() != first) {
        return Err(format!(
            "its pattern {} is not of identical holes, so each is called out as itself",
            p.id
        ));
    }
    // draftwright's pattern refusals (`_append_hole_features`), which its lint follows too: a
    // pattern whose plane is oblique, or whose members share an axis line (coaxial bores apart
    // only along the drilling direction), has no pattern callout that can be drawn.
    let Some(k) = principal(holes[members[0]].direction) else {
        return Err(format!(
            "its pattern {} lies in an oblique plane, so its holes are dimensioned singly",
            p.id
        ));
    };
    let mut across: Vec<Vec<i64>> = members
        .iter()
        .map(|&m| {
            (0..3)
                .filter(|&i| i != k)
                .map(|i| size_key(holes[m].at[i]))
                .collect()
        })
        .collect();
    across.sort();
    across.dedup();
    if across.len() < members.len() {
        return Err(format!(
            "its pattern {} has members on one axis line, apart only along the drilling direction, so its holes are dimensioned singly",
            p.id
        ));
    }
    if p.arrangement == "bolt_circle" && members.len() < 5 {
        let h = holes[members[0]];
        let centre = point(p.parameters.get("center")).map(|c| to_part(&h.frame, c));
        let others = holes
            .iter()
            .enumerate()
            .filter(|(i, _)| !members.contains(i))
            .map(|(_, o)| Axis {
                at: o.at,
                direction: o.direction,
            });
        let dot = |u: [f64; 3], v: [f64; 3]| u[0] * v[0] + u[1] * v[1] + u[2] * v[2];
        // Parallel to the members, and its axis line within 0.5 mm of the centre.
        let concentric = |c: [f64; 3], a: &Axis| {
            let d = [c[0] - a.at[0], c[1] - a.at[1], c[2] - a.at[2]];
            let along = dot(d, a.direction);
            dot(a.direction, h.direction).abs() >= 1.0 - 1e-6
                && (dot(d, d) - along * along).max(0.0).sqrt() <= CONCENTRIC
        };
        let corroborated = centre.is_some_and(|c| {
            others
                .chain(bosses.iter().copied())
                .any(|a| concentric(c, &a))
        });
        if !corroborated {
            return Err(format!(
                "quiddity's bolt circle {} has fewer than five members and no hole or boss on its centre, which does not establish a circle (ISSUE-1596-a), so its holes are dimensioned singly",
                p.id
            ));
        }
    }
    Ok(members)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn feature(parameters: Value) -> Feature {
        serde_json::from_value(json!({
            "id": "f:hole:1", "kind": "hole", "faces": [0], "parameters": parameters,
            "fingerprint": { "version": "test", "family": "holes", "subtype": "HoleRecord",
                             "sizes": {}, "position": [0.0, 0.0, 0.0], "neighbourhood": [] },
        }))
        .unwrap()
    }

    #[test]
    fn a_principal_axis_is_exactly_one_component() {
        assert_eq!(principal([0.0, 0.0, -1.0]), Some(2));
        assert_eq!(principal([1e-9, 2.0, 0.0]), Some(1));
        assert_eq!(principal([0.0, 0.01, 1.0]), None);
        assert_eq!(principal([0.0; 3]), None);
    }

    /// quiddity's countersink fields are `major_diameter` and `included_angle`.
    #[test]
    fn a_countersink_is_read_by_quidditys_field_names() {
        let f = feature(json!({
            "axis": [0.0, 0.0, -1.0], "location": [5.0, 5.0, 10.0], "diameter": 6.6,
            "depth": 10.0, "bottom": "through", "cbore": null, "spotface": null,
            "csink": { "major_diameter": 12.4, "included_angle": 90.0, "drill_diameter": 6.6,
                       "depth": 2.9, "axis": [0.0, 0.0, -1.0], "location": [5.0, 5.0, 10.0] },
        }));
        let h = Hole::read(&f, &IDENTITY).unwrap();
        assert_eq!(
            h.countersink,
            Some(Countersink {
                diameter: 12.4,
                angle: Some(90.0)
            })
        );
        assert_eq!(h.principal, Some((2, -1)));
        let mut odd = f.clone();
        odd.parameters["csink"] = json!({ "diameter": 12.4 });
        assert_eq!(
            Hole::read(&odd, &IDENTITY).unwrap_err(),
            "its csink has no major_diameter"
        );
    }

    /// quiddity-rust#244: a hole's mouth chamfers are read from `mouth_chamfers`, the opening's
    /// first; the opening's lengthens the depth of the recess whose mouth it breaks, and only
    /// that one's; holes alike but for a chamfer are not one requirement.
    #[test]
    fn mouth_chamfers_are_read_and_deepen_the_recess_they_break() {
        let entry = |diameter: f64, axis: f64| {
            json!({ "axis": [0.0, 0.0, axis], "location": [0.0, 0.0, 2.0], "diameter": diameter,
                    "size": 0.25, "depth": 0.25, "angle": 45.0, "faces": [1] })
        };
        let hole = |chamfers: Value| {
            feature(json!({
                "axis": [0.0, 0.0, -1.0], "location": [0.0, 0.0, 1.75], "diameter": 3.3,
                "depth": 4.75, "bottom": "through", "cbore": { "diameter": 5.0, "depth": 2.0 },
                "mouth_chamfers": chamfers,
            }))
        };
        fn read(f: &Feature) -> Hole<'_> {
            Hole::read(f, &IDENTITY).unwrap()
        }
        // The repro: 2.0 plus its 0.25 entry is 2.25 from the mouth; the cylinder stays 2.0.
        let f = hole(json!([entry(5.0, -1.0)]));
        let h = read(&f);
        let cbore = h.counterbore.unwrap();
        assert_eq!(
            (cbore.depth, cbore.entry, cbore.from_mouth()),
            (Some(2.0), 0.25, Some(2.25))
        );
        assert!(h.mouth_chamfers[0].at_opening && h.mouth_chamfers[0].is_c_form());
        // On the bore's diameter, or the far end's (its axis against the hole's), it is not the
        // counterbore's.
        for other in [entry(3.3, -1.0), entry(5.0, 1.0)] {
            let f = hole(json!([other]));
            assert_eq!(read(&f).counterbore.unwrap().from_mouth(), Some(2.0));
        }
        // The opening's chamfer comes first whatever the file's order; the same size and angle
        // at both ends is one chamfer, `both`.
        let f = hole(json!([entry(5.0, 1.0), entry(5.0, -1.0)]));
        let h = read(&f);
        assert!(h.mouth_chamfers[0].at_opening && !h.mouth_chamfers[1].at_opening);
        let (near, far, both) = h.chamfers();
        assert!(near.is_some() && far.is_none() && both);
        // A chamfer is part of the requirement; holes without one keep the key they had.
        let plain = hole(json!([]));
        assert_ne!(
            read(&plain).requirement(),
            read(&hole(json!([entry(5.0, -1.0)]))).requirement()
        );
        let mut old = plain.clone();
        old.parameters.remove("mouth_chamfers");
        assert_eq!(read(&plain).requirement(), read(&old).requirement());
        // The chamfer a callout states first is the one at the end its view looks at, not the
        // opening's: a hole drilled up (+z) from a flange's far face and chamfered at the top
        // (flanged_spool_132's ⌀10) is seen from above, where its chamfer is.
        let up = feature(json!({
            "axis": [0.0, 0.0, 1.0], "location": [0.0, 0.0, 128.0], "diameter": 10.0,
            "depth": 11.0, "bottom": "through",
            "mouth_chamfers": [entry(10.0, -1.0)],
        }));
        let h = read(&up);
        assert!(!h.mouth_chamfers[0].at_opening);
        assert_eq!(h.toward_viewer(), [0.0, 0.0, 1.0]);
        let (seen, other, both) = h.chamfers();
        assert!(seen.is_some() && other.is_none() && !both);
        // Seen from below it would be the other end's.
        let down = feature(json!({
            "axis": [0.0, 0.0, -1.0], "location": [0.0, 0.0, 140.0], "diameter": 10.0,
            "depth": 11.0, "bottom": "through",
            "mouth_chamfers": [entry(10.0, 1.0)],
        }));
        let (seen, other, _) = read(&down).chamfers();
        assert!(seen.is_none() && other.is_some());
        // An entry that cannot be read is said.
        let f = hole(json!([{ "axis": [0.0, 0.0, -1.0], "diameter": 5.0, "size": 0.25 }]));
        assert_eq!(
            Hole::read(&f, &IDENTITY).unwrap_err(),
            "its mouth chamfer has no depth"
        );
    }

    /// A through hole's depth is not part of its requirement; a blind hole's is.
    #[test]
    fn the_requirement_reads_depth_only_when_blind() {
        let base = json!({ "axis": [0.0, 0.0, 1.0], "location": [0.0, 0.0, 0.0],
                           "diameter": 6.0, "depth": 5.0, "bottom": "through" });
        let mut other = base.clone();
        other["depth"] = json!(8.0);
        let (a, b) = (feature(base.clone()), feature(other.clone()));
        let key = |f: &Feature| Hole::read(f, &IDENTITY).unwrap().requirement();
        assert_eq!(key(&a), key(&b));
        let mut blind = (base, other);
        blind.0["bottom"] = json!("flat");
        blind.1["bottom"] = json!("flat");
        assert_ne!(key(&feature(blind.0)), key(&feature(blind.1)));
    }

    /// #225: shf08's clamp hole, ⌀4 THRU drilled along -x and split by a 1.2 mm slit into two
    /// coaxial segments, is one hole; the same pair either side of a 48 mm gap (sm-hanger's two
    /// flanges) is two, as are blind or counterbored segments and holes off the line.
    #[test]
    fn a_slit_splits_one_through_hole_but_a_wide_gap_leaves_two() {
        let segment = |id: &str, x: f64, edit: &dyn Fn(&mut Value)| {
            let mut p = json!({ "axis": [-1.0, 0.0, 0.0], "location": [x, -5.0, -11.0],
                                "diameter": 4.0, "depth": 8.9, "bottom": "through" });
            edit(&mut p);
            let mut f = feature(p);
            f.id = id.into();
            f
        };
        let none = |_: &mut Value| {};
        fn read(fs: &[Feature]) -> Vec<Hole<'_>> {
            fs.iter()
                .map(|f| Hole::read(f, &IDENTITY).unwrap())
                .collect()
        }
        let split = [
            segment("f:hole:1", -0.6, &none),
            segment("f:hole:4", 9.5, &none),
        ];
        let holes = read(&split);
        assert_eq!(continuations(&holes), BTreeSet::from(["f:hole:1"]));
        assert_eq!(drillings(&holes), 1);

        let lugs = [
            segment("f:hole:1", -24.0, &none),
            segment("f:hole:2", 28.0, &none),
        ];
        assert_eq!(drillings(&read(&lugs)), 2);
        let blind = |p: &mut Value| p["bottom"] = json!("flat");
        let pair = [
            segment("f:hole:1", -0.6, &blind),
            segment("f:hole:4", 9.5, &blind),
        ];
        assert_eq!(drillings(&read(&pair)), 2);
        let bored = |p: &mut Value| p["cbore"] = json!({ "diameter": 8.0, "depth": 2.0 });
        let pair = [
            segment("f:hole:1", -0.6, &bored),
            segment("f:hole:4", 9.5, &bored),
        ];
        assert_eq!(drillings(&read(&pair)), 2);
        let off = |p: &mut Value| p["location"][1] = json!(5.0);
        let pair = [
            segment("f:hole:1", -0.6, &none),
            segment("f:hole:4", 9.5, &off),
        ];
        assert_eq!(drillings(&read(&pair)), 2);
    }

    /// LINT-hole_coverage-bolt_circle_corroboration (draftwright's `bolt_circle_is_corroborated`):
    /// a four-hole bolt circle is one requirement only when a hole that is not a member, or a
    /// boss, sits on its centre (its axis parallel to the members' and within 0.5 mm of quiddity's
    /// centre); five or more establish a circle by themselves. quiddity's frame here turns its z
    /// onto the part's x, so the centre is taken through it.
    #[test]
    fn a_small_bolt_circle_needs_something_round_on_its_centre() {
        // quiddity's x, y, z are the part's y, z, x, and its origin is at the part's (5, 0, 0).
        let frame = [
            [5.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
            [1.0, 0.0, 0.0],
        ];
        let hole = |id: &str, at: [f64; 3], diameter: f64| {
            let mut f = feature(json!({ "axis": [0.0, 0.0, 1.0], "location": at,
                                        "diameter": diameter, "depth": 5.0,
                                        "bottom": "through" }));
            f.id = id.into();
            f
        };
        // Four ø6 holes on a ø40 circle about quiddity's (50, 50), and a ø20 bore on it.
        let mut fs: Vec<Feature> = [[70.0, 50.0], [50.0, 70.0], [30.0, 50.0], [50.0, 30.0]]
            .iter()
            .enumerate()
            .map(|(i, c)| hole(&format!("f:hole:{}", i + 1), [c[0], c[1], 0.0], 6.0))
            .collect();
        fs.push(hole("f:hole:5", [50.0, 50.0, 0.0], 20.0));
        let read: Vec<Hole> = fs.iter().map(|f| Hole::read(f, &frame).unwrap()).collect();
        let all: Vec<&Hole> = read.iter().collect();
        let circle = |members: &[&str]| HolePattern {
            presentation_order: None,
            legacy_id: None,
            id: "f:hole_pattern:1".into(),
            arrangement: "bolt_circle".into(),
            members: members.iter().map(|m| m.to_string()).collect(),
            faces: vec![],
            parameters: json!({ "center": [50.0, 50.0, 0.0], "diameter": 40.0 })
                .as_object()
                .unwrap()
                .clone(),
        };
        let four = circle(&["f:hole:1", "f:hole:2", "f:hole:3", "f:hole:4"]);
        assert_eq!(established(&four, &all), Ok(vec![0, 1, 2, 3]));
        // Without the bore, nothing corroborates it.
        let err = established(&four, &all[..4]).unwrap_err();
        assert!(err.contains("fewer than five"), "{err}");
        // A boss on the centre does, through established_with: in the part's frame its axis is
        // along x through (5, 50, 50); one 0.6 mm off, or across the holes, does not.
        let boss = |at: [f64; 3], direction: [f64; 3]| Axis { at, direction };
        assert_eq!(
            established_with(
                &four,
                &all[..4],
                &[boss([0.0, 50.0, 50.0], [-1.0, 0.0, 0.0])]
            ),
            Ok(vec![0, 1, 2, 3])
        );
        assert!(
            established_with(
                &four,
                &all[..4],
                &[boss([0.0, 50.6, 50.0], [1.0, 0.0, 0.0])]
            )
            .is_err()
        );
        assert!(
            established_with(
                &four,
                &all[..4],
                &[boss([5.0, 50.0, 50.0], [0.0, 1.0, 0.0])]
            )
            .is_err()
        );
        // The bore cannot be a member (it is not of the members' size), and a circle without a
        // centre has nothing on it.
        let mut five = four.clone();
        five.members.push("f:hole:5".into());
        assert_eq!(
            established(&five, &all).unwrap_err(),
            "its pattern f:hole_pattern:1 is not of identical holes, so each is called out as itself"
        );
        let mut centreless = four.clone();
        centreless.parameters.remove("center");
        assert!(established(&centreless, &all).is_err());
    }

    /// The bosses' axes, in the part's frame; a boss without a location or axis is left out.
    #[test]
    fn bosses_are_read_in_the_parts_frame() {
        let mut a: Analysis = serde_json::from_value(json!({
            "schema": "draftwright.analysis/2",
            "binding": { "source_sha256": "", "part": "test", "face_count": 2, "loader": "test" },
            "envelope": [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]],
            "features": [],
            "iso_bounds": [0.0, 0.0, 0.0, 0.0],
            "recognition": { "recogniser": "test",
                             "frame": [[5.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0],
                                       [1.0, 0.0, 0.0]] },
        }))
        .unwrap();
        for (i, p) in [
            json!({ "axis": [0.0, 0.0, 2.0], "location": [50.0, 50.0, 1.0], "diameter": 30.0 }),
            json!({ "diameter": 30.0 }),
        ]
        .into_iter()
        .enumerate()
        {
            let mut f = feature(p);
            f.id = format!("f:boss:{}", i + 1);
            f.kind = "boss".into();
            a.features.push(f);
        }
        assert_eq!(
            bosses(&a),
            [Axis {
                at: [6.0, 50.0, 50.0],
                direction: [1.0, 0.0, 0.0]
            }]
        );
    }

    /// draftwright's refusals: a pattern drilled along no axis of the part, or whose members
    /// share an axis line, is not one requirement; a grid however spaced is.
    #[test]
    fn an_oblique_or_coaxial_pattern_is_not_established() {
        let hole = |id: &str, axis: [f64; 3], at: [f64; 3]| {
            let mut f = feature(json!({ "axis": axis, "location": at, "diameter": 6.0,
                                        "depth": 5.0, "bottom": "through" }));
            f.id = id.into();
            f
        };
        let pattern = |arrangement: &str| HolePattern {
            presentation_order: None,
            legacy_id: None,
            id: "f:hole_pattern:1".into(),
            arrangement: arrangement.into(),
            members: vec!["f:hole:1".into(), "f:hole:2".into(), "f:hole:3".into()],
            faces: vec![],
            parameters: Map::new(),
        };
        let established_of = |fs: &[Feature], arrangement: &str| {
            let read: Vec<Hole> = fs
                .iter()
                .map(|f| Hole::read(f, &IDENTITY).unwrap())
                .collect();
            established(&pattern(arrangement), &read.iter().collect::<Vec<_>>())
        };
        let z = [0.0, 0.0, 1.0];
        // Unevenly spaced, off any lattice: still one requirement.
        let uneven = [
            hole("f:hole:1", z, [0.0, 0.0, 0.0]),
            hole("f:hole:2", z, [10.0, 3.0, 0.0]),
            hole("f:hole:3", z, [25.0, 0.0, 0.0]),
        ];
        assert_eq!(established_of(&uneven, "grid"), Ok(vec![0, 1, 2]));
        let tilted = [0.0, 0.6, 0.8];
        let oblique = [
            hole("f:hole:1", tilted, [0.0, 0.0, 0.0]),
            hole("f:hole:2", tilted, [10.0, 0.0, 0.0]),
            hole("f:hole:3", tilted, [20.0, 0.0, 0.0]),
        ];
        assert!(
            established_of(&oblique, "linear")
                .unwrap_err()
                .contains("oblique")
        );
        let coaxial = [
            hole("f:hole:1", z, [0.0, 0.0, 0.0]),
            hole("f:hole:2", z, [0.0, 0.0, 10.0]),
            hole("f:hole:3", z, [10.0, 0.0, 0.0]),
        ];
        assert!(
            established_of(&coaxial, "linear")
                .unwrap_err()
                .contains("one axis line")
        );
    }
}
