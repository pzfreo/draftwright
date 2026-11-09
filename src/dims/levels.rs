//! A prismatic part's height ladder (draftwright's `StepLevelFeature`, built in `model/detect.py`'s
//! `_append_prismatic_features` and compiled by `model/compiled.py`'s `_compile_step_ladders`):
//! the heights of its horizontal face levels from its base, in the front view, and the positions
//! of the shoulders between them along x (in the plan) and y (in the side), from the part's box.
//!
//! - **Levels** are the Analysis's `levels` (quiddity's `step_level_records`, read in the part's
//!   frame), each once, less those another owner states, as draftwright's ownership stage
//!   (`model/detect_ownership.py`) decides: the top of a base plate of a multi-axis plate part,
//!   a level whose every support is a side-facing pad's edge, the floor of an edge-open pocket
//!   cut along z, and the corner of a through step dimensioned as one ([`ownership`]). A part
//!   with turned steps has no ladder.
//! - **Heights** are `step_height.length.f<lowest support face>`, base to level, each a linear
//!   dimension whose witnesses stand on the level's support (its faces' largest x and smallest y;
//!   the part's box when it has none), the shortest innermost. Three or more equal rises (within
//!   10 %) are one `N× rise`, `step_height.length` (`_step_repeat`), counting the rise to the top
//!   when it matches too.
//! - **Shoulders** are quiddity's risers projected onto the levels (`project_step_shoulders`: a
//!   wall's foot on a level, a ramp's ends on levels or the part's top or bottom), less those at
//!   a through step's leg; each is `step_position.length.f<lowest riser face>` (local suffixes on a shared face) from the
//!   part's box, in the plan (x) or the side (y). One standing exactly on the box is not a
//!   distance (`_is_zero_step_position`): it is reported, not drawn. Equal shoulders are stated once
//!   (draftwright draws each riser's).
//! - Each rung and shoulder is its own item and measurement, anchored on its supporting faces, never on its measured value or a rank among siblings, so a level a revision adds
//!   renames no other rung (ADR4-6). draftwright addresses each set as one (`step_height.length`,
//!   `step_position.length`; ARCH0015-2, not ported).
//!
//! The source spacing gate is applied at candidate scale by [`crate::detail`]: approved rungs
//! too short for their arrows or too near the last readable rung move into a prismatic detail.
//! Every rung remains planned; a refused detail leaves its requirements on the source view.
//!
//! Through steps ([`super::steps`]) share the ownership stage: a step running along z is always
//! dimensioned as one; one running along x or y only when the levels, shoulders, plates and the
//! overall extents do not already state both its legs (`_through_step_legacy_complete`).

use std::collections::BTreeSet;

use serde_json::Value;

use super::{AXES, Context, Measurement, P3, Planner, Problem, QFrame, Shape, Want, fmt, point};
use crate::bridge::Feature;
use crate::bridge::holes::{dir_to_part, principal};
use crate::model::{LevelSupport, Riser};

pub(super) const PLANNER: Planner = Planner { kinds: &[], wants };

/// The pseudo-feature the ladder's dimensions name, as `envelope` names the overall extents.
const FEATURE: &str = "step_level";
/// The band within which draftwright's ownership stage takes two stations as one (mm).
const NEAR: f64 = 0.5;
/// quiddity's end margin for levels (`STEP_LADDER_BOUNDARY_MARGIN`).
const END_MARGIN: f64 = 0.6;

/// The supports of the part's face levels and its risers, read in the part's frame with Rust
/// quiddity: what [`crate::model::Analysis`] records for the ladder. A level is kept as quiddity's
/// `step_level_records` keeps it: strictly inside its solid's height by the end margin.
pub(crate) fn evidence(part: &haecceity::Part) -> (Vec<LevelSupport>, Vec<Riser>) {
    use crate::quiddity_evidence::{face_levels_with_faces, recognise_risers};
    let scope = |faces: &[usize]| {
        let s = faces
            .first()
            .and_then(|f| part.solids.iter().position(|s| s.faces.contains(f)));
        s.map_or_else(|| part.bounds(), |s| part.solid_bounds(s))
    };
    let supports = face_levels_with_faces(part, 0.01)
        .into_iter()
        .filter_map(|(level, faces)| {
            let b = scope(&faces);
            let margin = END_MARGIN.min((b.max[2] - b.min[2]).max(0.0) * 0.25);
            let inside = b.min[2] + margin < level.z && level.z < b.max[2] - margin;
            let (x, y) = (level.x_span, level.y_span);
            inside.then_some(LevelSupport {
                faces,
                z: level.z,
                x_span: [x.0, x.1],
                y_span: [y.0, y.1],
            })
        })
        .collect();
    let risers = recognise_risers(part)
        .into_iter()
        .map(|r| Riser {
            faces: r.faces,
            vertical: r.vertical,
            axis: r.axis.to_string(),
            positions: r.positions,
            other_axis: r.other_axis.to_string(),
            other_positions: r.other_positions,
            z_lo: r.z_lo,
            z_hi: r.z_hi,
            lo_at_envelope: r.lo_at_envelope,
            hi_at_envelope: r.hi_at_envelope,
            tol: r.tol,
            body_levels: r.body_levels.iter().map(|l| l.z).collect(),
        })
        .collect();
    (supports, risers)
}

/// A through step (quiddity's `ThroughStep`) in the part's frame.
pub(super) struct Through<'a> {
    pub(super) feature: &'a Feature,
    /// The world axis it runs along.
    pub(super) run: usize,
    /// Its open section: an end on the part's box, the concave corner, the other end.
    pub(super) section: [P3; 3],
    /// Both its ends are in solid (`endpoint_scopes`), so its corner is a level of the part.
    solid: bool,
}

impl Through<'_> {
    /// Its two legs in section order: the world axis each lies along, and its two ends' points.
    pub(super) fn legs(&self) -> Vec<(usize, P3, P3)> {
        (0..2)
            .map(|i| {
                let (a, b) = (self.section[i], self.section[i + 1]);
                // Invariant: The level reader requires both legs and the changed coordinate.
                (changed(a, b).expect("read checks the legs"), a, b)
            })
            .collect()
    }

    /// Its legs as intervals: the axis and the low and high coordinates.
    fn spans(&self) -> Vec<(usize, f64, f64)> {
        self.legs()
            .into_iter()
            .map(|(k, a, b)| (k, a[k].min(b[k]), a[k].max(b[k])))
            .collect()
    }
}

/// The one axis along which *a* and *b* differ, if exactly one does.
fn changed(a: P3, b: P3) -> Option<usize> {
    let size = a.iter().chain(&b).fold(1.0f64, |m, c| m.max(c.abs()));
    let along: Vec<usize> = (0..3)
        .filter(|&k| (a[k] - b[k]).abs() > 1e-9 * size)
        .collect();
    (along.len() == 1).then(|| along[0])
}

/// A record's axis letter, as the world axis it lies along in the part's frame.
fn world_axis(frame: &QFrame, letter: Option<&Value>) -> Option<usize> {
    principal(frame.letter(letter?)?)
}

/// *f* as a through step, or why it cannot be read: its run `axis`, `at` and `section` (three
/// points across the run, in quiddity's frame, on its two other axes in order), whose two legs
/// must each lie along one axis of the part and not the same one.
pub(super) fn through<'a>(f: &'a Feature, frame: &QFrame) -> Result<Through<'a>, String> {
    let p = &f.parameters;
    let local = p
        .get("axis")
        .and_then(Value::as_str)
        .and_then(|a| AXES.iter().position(|x| *x == a))
        .ok_or("its record gives no run axis")?;
    let run = world_axis(frame, p.get("axis")).ok_or("its run lies along no axis of the part")?;
    let at = point(p.get("at")).ok_or("its record gives no at")?;
    let across: Vec<usize> = (0..3).filter(|&k| k != local).collect();
    let pairs: Vec<[f64; 2]> = p
        .get("section")
        .and_then(Value::as_array)
        .ok_or("its record gives no section")?
        .iter()
        .map(|v| match v.as_array().map(Vec::as_slice) {
            Some([a, b]) => Some([a.as_f64()?, b.as_f64()?]),
            _ => None,
        })
        .collect::<Option<_>>()
        .ok_or("its section is not points")?;
    let [s0, s1, s2] = pairs[..] else {
        return Err("its section is not three points".into());
    };
    let section = [s0, s1, s2].map(|s| {
        let mut l = at;
        l[across[0]] = s[0];
        l[across[1]] = s[1];
        frame.point(l)
    });
    let k0 = changed(section[0], section[1]);
    let k1 = changed(section[1], section[2]);
    match (k0, k1) {
        (Some(a), Some(b)) if a != b && a != run && b != run => {}
        _ => return Err("its section is not two legs along two axes of the part".into()),
    }
    let solid = match p.get("endpoint_scopes") {
        None => true,
        Some(v) => v
            .as_array()
            .is_some_and(|s| s.len() == 2 && s.iter().all(|e| e.as_str() == Some("solid"))),
    };
    Ok(Through {
        feature: f,
        run,
        section,
        solid,
    })
}

/// A plate (quiddity's `Plate`) in the part's frame: its thin axis and its two faces' coordinates
/// on it, low first.
#[derive(Clone)]
struct Plate<'a> {
    feature: &'a Feature,
    axis: usize,
    lo: f64,
    hi: f64,
}

fn plate<'a>(f: &'a Feature, frame: &QFrame) -> Option<Plate<'a>> {
    let p = &f.parameters;
    let letter = p.get("axis")?.as_str()?;
    let local = AXES.iter().position(|a| *a == letter)?;
    let axis = world_axis(frame, p.get("axis"))?;
    let n = |k: &str| p.get(k).and_then(Value::as_f64);
    let (u, v) = (n("u")?, n("v")?);
    let across: Vec<usize> = (0..3).filter(|&k| k != local).collect();
    let at = |c: f64| {
        let mut l = [0.0; 3];
        l[local] = c;
        l[across[0]] = u;
        l[across[1]] = v;
        frame.point(l)[axis]
    };
    let (a, b) = (at(n("lo")?), at(n("hi")?));
    Some(Plate {
        feature: f,
        axis,
        lo: a.min(b),
        hi: a.max(b),
    })
}

/// A raised pad (quiddity's `RaisedPad`) in the part's frame: the axis it faces along and its box.
struct Pad {
    axis: usize,
    lo: P3,
    hi: P3,
}

fn pad(f: &Feature, frame: &QFrame) -> Option<Pad> {
    let p = &f.parameters;
    let n = |k: &str| p.get(k).and_then(Value::as_f64);
    let axis = world_axis(frame, p.get("axis"))?;
    let a = frame.point([n("x0")?, n("y0")?, n("z0")?]);
    let b = frame.point([n("x1")?, n("y1")?, n("z1")?]);
    Some(Pad {
        axis,
        lo: [0, 1, 2].map(|k| a[k].min(b[k])),
        hi: [0, 1, 2].map(|k| a[k].max(b[k])),
    })
}

/// The floor of an edge-open pocket cut along z, from a section recess draftwright reads as one
/// (`section_recess_pocket_fields`: an `edge_open_recess` with a `polygonal` open chain of three or
/// four vertices along its axes, planar run ends normal to its run, one capped and one open, and
/// a principal frame): its capped end's height. Any other recess has none.
fn edge_floor(f: &Feature, frame: &QFrame) -> Option<f64> {
    let p = &f.parameters;
    let class = p.get("classification")?;
    if class.get("feature_kind")?.as_str()? != "edge_open_recess"
        || class.get("section_shape")?.as_str()? != "polygonal"
    {
        return None;
    }
    let g = p.get("geometry")?;
    let mut capped = None;
    for (end, n) in ["low", "high"].into_iter().zip(0..) {
        let e = g.get("ends")?.get(end)?;
        let s = e.get("surface")?;
        let flat = s
            .get("gradient")?
            .as_array()?
            .iter()
            .all(|x| x.as_f64() == Some(0.0));
        if s.get("type")?.as_str()? != "plane" || !flat {
            return None;
        }
        match e.get("condition")?.as_str()? {
            "capped" if capped.is_none() => capped = Some(n),
            "capped" => return None,
            "open" => {}
            _ => return None,
        }
    }
    let profile = g.get("profile")?;
    if profile.get("closure")?.as_str()? != "open" {
        return None;
    }
    let mut pts: Vec<[f64; 2]> = Vec::new();
    for v in profile.get("boundary")?.as_array()? {
        let xy = v.get("point")?.as_array()?;
        if v.get("bulge")?.as_f64()? != 0.0 {
            return None;
        }
        pts.push([xy.first()?.as_f64()?, xy.get(1)?.as_f64()?]);
    }
    let rectilinear = pts.windows(2).all(|w| {
        let moved = (0..2).filter(|&i| w[0][i] != w[1][i]).count();
        moved == 1
    });
    if !(3..=4).contains(&pts.len()) || !rectilinear {
        return None;
    }
    let fr = g.get("frame")?;
    let unit = |name: &str| {
        let v = point(fr.get(name))?;
        let i = (0..3).max_by(|&a, &b| v[a].abs().total_cmp(&v[b].abs()))?;
        let s = v[i].signum();
        (0..3)
            .all(|k| (v[k] - if k == i { s } else { 0.0 }).abs() <= 1e-9)
            .then_some(v)
    };
    let [run, u, v] = [unit("run")?, unit("u")?, unit("v")?];
    let index = |d: P3| (0..3).find(|&k| d[k] != 0.0);
    if [index(run), index(u), index(v)]
        .iter()
        .collect::<BTreeSet<_>>()
        .len()
        != 3
    {
        return None;
    }
    let origin = point(fr.get("origin"))?;
    let interval = g.get("run_interval")?.as_array()?;
    let end = interval.get(capped?)?.as_f64()?;
    let floor = frame.point([0, 1, 2].map(|k| origin[k] + end * run[k]));
    (principal(dir_to_part(&frame.0, run)) == Some(2)).then_some(floor[2])
}

/// What draftwright's ownership stage decides for a prismatic part: the ladder's levels and
/// shoulders, and which through steps are dimensioned as through steps.
pub(super) struct Ownership<'a> {
    /// The ladder's levels, ascending, each once.
    pub(super) levels: Vec<f64>,
    /// The shoulders, `(axis, position)`, ascending by axis then position, each once.
    pub(super) shoulders: Vec<(usize, f64)>,
    /// Through steps dimensioned as through steps.
    pub(super) lowered: Vec<Through<'a>>,
    /// Through steps whose legs the ladder, plates and overall extents already state, with what
    /// states them.
    pub(super) legacy: Vec<(Through<'a>, Vec<String>)>,
    pub(super) problems: Vec<Problem>,
}

/// Python's `round(z, 3)`, as a key.
fn thousandths(z: f64) -> i64 {
    (z * 1e3).round() as i64
}

use crate::geometry_ids::{height_parameter, position_parameter};

fn near(z: f64, zs: &[f64]) -> bool {
    zs.iter().any(|o| (z - o).abs() < NEAR)
}

/// quiddity's `project_step_shoulders` over numeric *levels*: each riser whose foot (a wall) or
/// both ends (a ramp, an end on the part's top or bottom counting) sit on one of its solid's
/// levels near *levels*, at its positions; sorted, every occurrence kept.
fn project(risers: &[Riser], levels: &[f64]) -> Vec<(usize, f64)> {
    let Some(first) = risers.first() else {
        return Vec::new();
    };
    if levels.is_empty() {
        return Vec::new();
    }
    let tol = first.tol;
    let mut out = Vec::new();
    for r in risers {
        let owned: Vec<f64> = r
            .body_levels
            .iter()
            .copied()
            .filter(|o| levels.iter().any(|s| (s - o).abs() < tol))
            .collect();
        let on = |z: f64| owned.iter().any(|l| (z - l).abs() < tol);
        let tied = if r.vertical {
            on(r.z_lo)
        } else {
            (r.lo_at_envelope || on(r.z_lo)) && (r.hi_at_envelope || on(r.z_hi))
        };
        if !tied {
            continue;
        }
        for (axis, positions) in [(&r.axis, &r.positions), (&r.other_axis, &r.other_positions)] {
            let Some(k) = AXES.iter().position(|a| a == axis) else {
                continue;
            };
            out.extend(positions.iter().map(|p| (k, *p)));
        }
    }
    out.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.total_cmp(&b.1)));
    out
}

/// The datum levels a lowered through step owns (`_datum_level_zs`): its corner's height, for a
/// step running along x or y whose ends are in solid, or one of whose legs rises from the part's
/// base to it.
fn owned_levels(steps: &[Through], base: f64) -> Vec<f64> {
    let mut out = Vec::new();
    for s in steps.iter().filter(|s| s.run != 2) {
        let level = s.section[1][2];
        let from_base = s.legs().iter().any(|(k, a, b)| {
            *k == 2
                && (((a[2] - base).abs() <= 1e-6 && (b[2] - level).abs() <= 1e-6)
                    || ((b[2] - base).abs() <= 1e-6 && (a[2] - level).abs() <= 1e-6))
        });
        if s.solid || from_base {
            out.push(level);
        }
    }
    out
}

/// The shoulder sites a lowered through step's legs make redundant
/// (`_through_step_shoulder_sites`): a leg along x or y from one side of the part's box, at its
/// other end.
fn owned_sites(steps: &[Through], lo: P3, hi: P3) -> Vec<(usize, f64)> {
    let mut out = Vec::new();
    for s in steps {
        for (k, a, b) in s.spans() {
            if k == 2 {
                continue;
            }
            if (a - lo[k]).abs() < NEAR {
                out.push((k, b));
            } else if (b - hi[k]).abs() < NEAR {
                out.push((k, a));
            }
        }
    }
    out
}

/// What states both of *step*'s legs in draftwright's legacy grammar
/// (`_through_step_legacy_owners`): a level's height (and the overall extent above it), a
/// shoulder's position (and the extent beyond it), a plate's thickness (and the extent beyond a
/// plate on the box), each leg within 0.5 mm at both ends; none when a leg has no owner, or more
/// than one of the step's own plates fits it.
fn legacy_owners(
    step: &Through,
    bounds: (P3, P3),
    levels: &[f64],
    shoulders: &[(usize, f64)],
    plates: &[Plate],
) -> Option<Vec<String>> {
    let (lo, hi) = bounds;
    let ladder = || "the step ladder".to_string();
    let extent = |k: usize| format!("the overall {} extent", AXES[k]);
    let mut intervals: Vec<(usize, f64, f64, Vec<String>)> = Vec::new();
    for &z in levels {
        intervals.push((2, lo[2].min(z), lo[2].max(z), vec![ladder()]));
        intervals.push((2, z.min(hi[2]), z.max(hi[2]), vec![ladder(), extent(2)]));
    }
    for &(k, at) in shoulders {
        intervals.push((k, lo[k].min(at), lo[k].max(at), vec![ladder()]));
        intervals.push((k, at.min(hi[k]), at.max(hi[k]), vec![ladder(), extent(k)]));
    }
    // Only the step's own plates (sharing a face with it) can state its legs.
    let own: Vec<&Plate> = plates
        .iter()
        .filter(|p| {
            p.feature
                .faces
                .iter()
                .any(|f| step.feature.faces.contains(f))
        })
        .collect();
    for p in &own {
        let k = p.axis;
        let name = format!("{}'s thickness", p.feature.id);
        intervals.push((k, p.lo, p.hi, vec![name.clone()]));
        if (p.lo - lo[k]).abs() < NEAR {
            intervals.push((k, p.hi, hi[k], vec![name.clone(), extent(k)]));
        }
        if (p.hi - hi[k]).abs() < NEAR {
            intervals.push((k, lo[k], p.lo, vec![name, extent(k)]));
        }
    }
    let mut owners: Vec<String> = Vec::new();
    for (k, a, b) in step.spans() {
        let matches: Vec<&Vec<String>> = intervals
            .iter()
            .filter(|(ik, ia, ib, _)| *ik == k && (ia - a).abs() < NEAR && (ib - b).abs() < NEAR)
            .map(|(_, _, _, o)| o)
            .collect();
        if matches.is_empty() {
            return None;
        }
        let fitting: BTreeSet<&str> = own
            .iter()
            .filter(|p| {
                let name = format!("{}'s thickness", p.feature.id);
                matches.iter().any(|o| o.contains(&name))
            })
            .map(|p| p.feature.id.as_str())
            .collect();
        if fitting.len() > 1 {
            return None;
        }
        for o in matches.into_iter().flatten() {
            if !owners.contains(o) {
                owners.push(o.clone());
            }
        }
    }
    Some(owners)
}

/// draftwright's ownership stage over the Analysis (`prepare_prismatic_ownership`,
/// `resolve_through_step_ownership` and `_append_prismatic_features`).
pub(super) fn ownership<'a>(cx: &Context<'a>) -> Ownership<'a> {
    let a = cx.analysis;
    let frame = &cx.frame;
    let [lo, hi] = a.envelope;
    let mut problems = Vec::new();
    let unread = |f: &Feature, what: &str| Problem {
        id: format!("p:feature_not_dimensioned:{}", f.id),
        code: "feature_not_dimensioned".into(),
        severity: "warning".into(),
        message: format!(
            "{} is not dimensioned: {what}, so the step ladder cannot tell whether it states a level.",
            crate::wording::capital(&crate::wording::feature(f))
        ),
        subjects: vec![f.id.clone()],
        ..Default::default()
    };
    let of = |kind: &'a str| a.features.iter().filter(move |f| f.kind == kind);
    // A part with turned steps has their chain, not a ladder.
    let profiles = of("turned_step").next().is_some();
    // Plates and pads only decide the ladder's levels, so an unreadable one matters only when
    // there is a ladder.
    let mut plates = Vec::new();
    for f in of("plate") {
        match plate(f, frame) {
            Some(p) => plates.push(p),
            None if !profiles => problems.push(unread(
                f,
                "its record does not lie along an axis of the part",
            )),
            None => {}
        }
    }
    let multi = plates.iter().map(|p| p.axis).collect::<BTreeSet<_>>().len() >= 2;
    let plates: Vec<Plate> = if !profiles && cx.turned.is_none() && multi {
        plates
    } else {
        Vec::new()
    };
    let mut pads = Vec::new();
    for f in of("pad") {
        match pad(f, frame) {
            Some(p) => pads.push(p),
            None if !profiles => problems.push(unread(
                f,
                "its record does not lie along an axis of the part",
            )),
            None => {}
        }
    }
    let mut steps = Vec::new();
    for f in of("through_step") {
        match through(f, frame) {
            Ok(s) => steps.push(s),
            Err(why) => problems.push(Problem {
                id: format!("p:feature_not_dimensioned:{}", f.id),
                code: "feature_not_dimensioned".into(),
                severity: "warning".into(),
                message: format!(
                    "{} is not dimensioned: {why}.",
                    crate::wording::capital(&crate::wording::feature(f))
                ),
                subjects: vec![f.id.clone()],
                ..Default::default()
            }),
        }
    }
    let floors: Vec<f64> = of("section_recess")
        .filter_map(|f| edge_floor(f, frame))
        .collect();
    let at_base: BTreeSet<i64> = plates
        .iter()
        .filter(|p| p.axis == 2 && (p.lo - lo[2]).abs() < NEAR)
        .map(|p| thousandths(p.hi))
        .collect();
    // A level every support of which is a side-facing pad's top or bottom edge is the pad's.
    let pads_own = |s: &LevelSupport| {
        pads.iter().any(|p| {
            p.axis != 2
                && (near(s.z, &[p.lo[2], p.hi[2]]))
                && (s.x_span[0] - p.lo[0]).abs() < NEAR
                && (s.x_span[1] - p.hi[0]).abs() < NEAR
                && (s.y_span[0] - p.lo[1]).abs() < NEAR
                && (s.y_span[1] - p.hi[1]).abs() < NEAR
        })
    };
    let side_pads: Vec<f64> = a
        .level_supports
        .iter()
        .filter(|s| pads_own(s) && a.level_supports.iter().all(|o| o.z != s.z || pads_own(o)))
        .map(|s| s.z)
        .collect();
    let mut zs: Vec<f64> = if profiles {
        Vec::new()
    } else {
        a.levels.clone()
    };
    zs.sort_by(f64::total_cmp);
    // One level per thousandth of height, which its rung's ID names.
    zs.dedup_by(|a, b| thousandths(*a - lo[2]) == thousandths(*b - lo[2]));
    let ownership_zs: Vec<f64> = zs
        .iter()
        .copied()
        .filter(|z| {
            !at_base.contains(&thousandths(*z)) && !near(*z, &side_pads) && !near(*z, &floors)
        })
        .collect();

    // A step along z is always a through step; one along x or y only when the legacy grammar
    // does not state both its legs, recomputed until no more are uncovered (a fixed point).
    let bounds = (lo, hi);
    let shoulders = project(&a.risers, &ownership_zs);
    let (mut lowered, mut rest): (Vec<Through>, Vec<Through>) = steps.into_iter().partition(|s| {
        s.run == 2 || legacy_owners(s, bounds, &ownership_zs, &shoulders, &plates).is_none()
    });
    let (remaining_zs, remaining_shoulders, remaining_plates) = loop {
        let spans: Vec<(usize, f64, f64)> = lowered.iter().flat_map(|s| s.spans()).collect();
        let owned = owned_levels(&lowered, lo[2]);
        let sites = owned_sites(&lowered, lo, hi);
        let zs: Vec<f64> = ownership_zs
            .iter()
            .copied()
            .filter(|z| !near(*z, &owned))
            .collect();
        let shoulders: Vec<(usize, f64)> = project(&a.risers, &zs)
            .into_iter()
            .filter(|(k, at)| !sites.iter().any(|(sk, s)| sk == k && (at - s).abs() < NEAR))
            .collect();
        let kept: Vec<Plate> = plates
            .iter()
            .filter(|p| {
                !spans.iter().any(|(k, a, b)| {
                    *k == p.axis && (p.lo - a).abs() <= 1e-6 && (p.hi - b).abs() <= 1e-6
                })
            })
            .cloned()
            .collect();
        let (promoted, still): (Vec<Through>, Vec<Through>) = rest
            .into_iter()
            .partition(|s| legacy_owners(s, bounds, &zs, &shoulders, &kept).is_none());
        rest = still;
        if promoted.is_empty() {
            break (zs, shoulders, kept);
        }
        lowered.extend(promoted);
    };
    let legacy: Vec<(Through, Vec<String>)> = rest
        .into_iter()
        .map(|s| {
            let owners = legacy_owners(
                &s,
                bounds,
                &remaining_zs,
                &remaining_shoulders,
                &remaining_plates,
            )
            // Invariant: The preceding promotion check excluded promoted owners.
            .expect("not promoted, so owned");
            (s, owners)
        })
        .collect();
    lowered.sort_by_cached_key(|s| {
        crate::geometry_ids::presentation_key(a, &s.feature.id).into_owned()
    });

    // The ladder: the levels no other owner states, and the shoulders between them.
    let through_zs = owned_levels(&lowered, lo[2]);
    let sites = owned_sites(&lowered, lo, hi);
    let levels: Vec<f64> = zs
        .iter()
        .copied()
        .filter(|z| {
            !at_base.contains(&thousandths(*z))
                && !near(*z, &side_pads)
                && !near(*z, &through_zs)
                && !near(*z, &floors)
        })
        .collect();
    let mut shoulders: Vec<(usize, f64)> = project(&a.risers, &levels)
        .into_iter()
        .filter(|(k, at)| *k < 2 && !sites.iter().any(|(sk, s)| sk == k && (at - s).abs() < NEAR))
        .collect();
    // One shoulder per thousandth of position, which its ID names (draftwright draws each riser's).
    shoulders
        .dedup_by(|a, b| a.0 == b.0 && thousandths(a.1 - lo[a.0]) == thousandths(b.1 - lo[b.0]));
    Ownership {
        levels,
        shoulders,
        lowered,
        legacy,
        problems,
    }
}

/// The repeat of a uniform staircase (`_step_repeat`): with three or more levels whose rises
/// from the base are all within 10 % of their mean, the count of rises (the rise to the top too,
/// when it matches) and the mean rise.
fn repeat(levels: &[f64], base: f64, top: f64) -> Option<(usize, f64)> {
    if levels.len() < 3 {
        return None;
    }
    let mut rises = vec![levels[0] - base];
    rises.extend(levels.windows(2).map(|w| w[1] - w[0]));
    let mean = rises.iter().sum::<f64>() / rises.len() as f64;
    if mean <= 0.0 || !rises.iter().all(|r| (r - mean).abs() / mean <= 0.10) {
        return None;
    }
    let last = levels[levels.len() - 1];
    let top_too = ((top - last) - mean).abs() / mean <= 0.10;
    Some((rises.len() + usize::from(top_too), mean))
}

fn wants(cx: &Context) -> (Vec<Want>, Vec<Problem>) {
    let a = cx.analysis;
    let o = ownership(cx);
    let mut problems = o.problems;
    let mut out = Vec::new();
    let [lo, hi] = a.envelope;
    let base = lo[2];
    // The witnesses stand on the level's support: its faces' largest x and smallest y (the last
    // support at that height to the thousandth, as draftwright keys them), else the part's box.
    // The levels and their supports are read by different paths, so a level with no support is
    // reported (an Analysis of version 3 has none, and says so below).
    let station = |z: f64| {
        a.level_supports
            .iter()
            .rev()
            .find(|s| thousandths(s.z) == thousandths(z))
            .map(|s| [s.x_span[1], s.y_span[0]])
    };
    if !a.level_supports.is_empty() {
        for &z in o.levels.iter().filter(|&&z| station(z).is_none()) {
            problems.push(Problem {
                id: format!("p:step_level_support_missing:{}", fmt(z - base)),
                code: "step_level_support_missing".into(),
                severity: "info".into(),
                message: format!(
                    "No support is recorded for the face level {} above the base, so its height's witnesses stand on the part's box.",
                    fmt(z - base)
                ),
                subjects: Vec::new(),
                refs: vec![FEATURE.into()],
                ..Default::default()
            });
        }
    }
    let rung = |z: f64| {
        let [x, y] = station(z).unwrap_or([hi[0], lo[1]]);
        Shape::Linear {
            a: [x, y, base],
            b: [x, y, z],
            axis: 2,
            a_edge: false,
            b_edge: false,
            order: 1,
        }
    };
    // Each rung is its own measurement, discriminated by its height, as its ID is.
    let height =
        |parameter: String, value: f64, terms: Vec<String>, shape: Shape, basis: String| Want {
            id: super::want_id(FEATURE, &parameter),
            views: vec!["front"],
            value,
            terms,
            measurements: vec![Measurement {
                feature: FEATURE.into(),
                parameter,
                value,
            }],
            basis,
            shape,
            turning: None,
        };
    if let Some((n, rise)) = repeat(&o.levels, base, hi[2]) {
        out.push(height(
            "step_height.length".into(),
            rise,
            vec![format!("{n}×"), fmt(rise)],
            rung(o.levels[0]),
            format!(
                "the part's {} face levels rise in equal steps from its base, so one rise states them all ({n}× {}), in the front view",
                o.levels.len(),
                fmt(rise)
            ),
        ));
    } else {
        for &z in &o.levels {
            out.push(height(
                height_parameter(a, z),
                z - base,
                vec![fmt(z - base)],
                rung(z),
                "a face level's height from the part's base, in the front view (draftwright's step-height ladder)".into(),
            ));
        }
    }
    for &(k, at) in &o.shoulders {
        let value = (at - lo[k]).abs();
        let view = if k == 0 { "plan" } else { "side" };
        // Exact, as draftwright's `_is_zero_step_position` is: any nonzero shoulder is a distance.
        if value == 0.0 {
            problems.push(Problem {
                id: format!("p:step_position_coincident_with_datum:{}{}", AXES[k], fmt(at)),
                code: "step_position_coincident_with_datum".into(),
                severity: "info".into(),
                message: format!(
                    "The {} shoulder at {} stands on the part's box, so its position is no distance; it is not dimensioned.",
                    AXES[k].to_uppercase(),
                    fmt(at)
                ),
                subjects: Vec::new(),
                refs: vec![FEATURE.into()],
                ..Default::default()
            });
            continue;
        }
        let mut b = lo;
        b[k] = at;
        let parameter = position_parameter(a, k, at);
        out.push(Want {
            id: super::want_id(FEATURE, &parameter),
            views: vec![view],
            value,
            terms: vec![fmt(value)],
            measurements: vec![Measurement {
                feature: FEATURE.into(),
                parameter,
                value,
            }],
            basis: format!(
                "a step's shoulder located along {} from the part's box, in the {view} view (draftwright's step positions)",
                AXES[k]
            ),
            shape: Shape::Linear {
                a: lo,
                b,
                axis: k,
                a_edge: true,
                b_edge: true,
                order: 1,
            },
            turning: None,
        });
    }
    // An Analysis of version 3 has levels but no risers: the shoulders cannot be found.
    if a.schema == "draftwright.analysis/3" && !a.levels.is_empty() {
        problems.push(Problem {
            id: "p:dimensions_not_ported:step_level".into(),
            code: "dimensions_not_ported".into(),
            severity: "info".into(),
            message: "The analysis (draftwright.analysis/3) records no step risers, so no step position is dimensioned and a through step along x or y is dimensioned as one; analyse the STEP file again.".into(),
            subjects: Vec::new(),
            refs: vec![FEATURE.into()],
            ..Default::default()
        });
    }
    (out, problems)
}
