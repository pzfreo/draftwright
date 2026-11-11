//! Bosses: cylindrical (quiddity's `BossRecord`, draftwright's `BossFeature`) and regular
//! polygonal prisms (quiddity's `PolygonalBoss`, draftwright's `PolygonalBossFeature`): diameter
//! or size across flats, height and location (`render_boss_diameters` in
//! `annotations/from_model.py`, `annotations/_polygonal_prisms.py`, the boss heights in
//! `compiled.py`).
//!
//! Each boss states:
//! - its **diameter**: on a part turned whole, a cylindrical boss on the turning axis is one band
//!   of the profile, so its diameter is stated across it in a view that shows the profile
//!   (draftwright leads to it from its diameter column there), equal such bands sharing one
//!   counted diameter (`2× ø11.2`, beyond draftwright: #220); any other boss reads as a circle
//!   looking down its axis, so its diameter is called out with a leader in that view, identical
//!   bosses along one axis counted once (`2× ø22.9`), as draftwright collapses them. A thread
//!   or knurl the boss owns ([`crate::pmi_lowering::manufacturing`]) follows the diameter, an
//!   external thread's designation in place of it, as on a turned step; that boss keeps a
//!   callout of its own;
//! - for a **polygonal** boss, its size across flats instead, called out in the view down its
//!   axis with a leader to a flat: `HEX 100 A/F` for six sides, `4-SIDED 25.4 A/F` otherwise, as
//!   draftwright prints them. Identical polygonal bosses along one axis are counted once, as
//!   cylindrical ones are; draftwright calls each out on its own (it batches only hex pockets);
//! - its **height**, when quiddity gives one: from its base to its top along its axis, in a view
//!   that shows its profile, the extension lines on its outline (draftwright's witness span). A
//!   boss whose record gives no positive height is reported (`feature_not_dimensioned`), so the
//!   extent it stands to is not left unstated without trace. A height between the same two
//!   faces as an overall extent is the extent's ([`consolidate`]);
//! - its **location**, as a hole's is: both coordinates of its axis from the part's box in the
//!   view that looks down it, one dimension per distinct distance. draftwright locates no boss;
//!   the port does, since a boss whose position is not stated cannot be made. A boss on a turning
//!   axis needs none (the centre line locates it), nor one a recognised hole runs down (the
//!   hole's location states it).
//!
//! A boss that is a groove's floor is the groove's, as in draftwright and lint
//! ([`crate::lint::bosses::groove_floor`]); it gets no dimensions of its own here. Nor does a
//! boss that is a band of a turned profile, recorded on the same faces as a turned step
//! ([`crate::lint::bosses::turned_band`], draftwright's `boss_turned_step_owner`): the step's
//! diameter and length state it ([`super::turned`]), and its own would state them twice.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::Value;

use super::envelope::profile_views;
use super::holes as holes_dims;
use super::{
    AXES, Context, Measurement, NAMES, Omitted, P3, Planner, Problem, QFrame, Shape, Want, add,
    end_on, key, point, scale3, want_id,
};
use crate::bridge::Feature;
use crate::bridge::holes::{self, principal};
use crate::lint::bosses::{groove_floor, turned_band};
use crate::model::Analysis;

pub(super) const PLANNER: Planner = Planner {
    kinds: &["boss", "polygonal_boss"],
    wants,
};

/// A boss's cross-section.
pub(super) enum Section {
    /// A cylinder of this diameter.
    Round(f64),
    /// A regular prism: its number of sides, its size across flats, the centres of its flat faces
    /// (the part's frame), and its half-width across each world axis (to its corners).
    Polygon {
        sides: u64,
        across_flats: f64,
        flats: Vec<P3>,
        half: P3,
    },
}

/// A boss in the part's frame.
pub(super) struct Boss<'a> {
    feature: &'a Feature,
    /// The world axis it stands along.
    pub(super) axis: usize,
    /// The centre of its top, and of its base when its height is known.
    pub(super) top: P3,
    pub(super) base: Option<P3>,
    pub(super) section: Section,
    pub(super) height: Option<f64>,
}

impl Boss<'_> {
    /// How far its outline lies from its axis across world axis *j*.
    pub(super) fn half(&self, j: usize) -> f64 {
        match &self.section {
            Section::Round(d) => 0.5 * d,
            Section::Polygon { half, .. } => half[j],
        }
    }
}

/// A cylindrical boss as quiddity records it (`location` its top's centre, `axis` outward,
/// `diameter`, `height`), or why it cannot be dimensioned. Since quiddity-rust v0.2.5,
/// height and top include convex end treatments; their separate callouts add no height.
fn read<'a>(f: &'a Feature, frame: &QFrame) -> Result<Boss<'a>, String> {
    let p = &f.parameters;
    let top = frame.point(point(p.get("location")).ok_or("its record gives no location")?);
    let local = point(p.get("axis")).ok_or("its record gives no axis")?;
    let dir = holes::dir_to_part(&frame.0, local);
    let n = dir.iter().map(|c| c * c).sum::<f64>().sqrt();
    let axis = principal(dir)
        .ok_or("its axis is not along x, y or z, so no standard view looks down it")?;
    let diameter = p
        .get("diameter")
        .and_then(Value::as_f64)
        .ok_or("its record gives no diameter")?;
    let height = p.get("height").and_then(Value::as_f64).filter(|h| *h > 0.0);
    Ok(Boss {
        feature: f,
        axis,
        top,
        base: height.map(|h| add(top, scale3(dir, -h / n))),
        section: Section::Round(diameter),
        height,
    })
}

/// A cylindrical boss whose axis is inclined to every principal axis, in the part's frame: no
/// standard view looks down it, so only its diameter is stated ([`inclined_callouts`]).
struct Inclined<'a> {
    feature: &'a Feature,
    /// The centre of its top, its outward axis (unit), and the centre of its base when its height
    /// is known.
    top: P3,
    dir: P3,
    base: Option<P3>,
    diameter: f64,
}

/// A cylindrical boss record ([`read`]) whose axis is readable but inclined; none otherwise.
fn read_inclined<'a>(f: &'a Feature, frame: &QFrame) -> Option<Inclined<'a>> {
    let p = &f.parameters;
    let dir = holes::dir_to_part(&frame.0, point(p.get("axis"))?);
    let n = dir.iter().map(|c| c * c).sum::<f64>().sqrt();
    if n == 0.0 || principal(dir).is_some() {
        return None;
    }
    let dir = dir.map(|c| c / n);
    let top = frame.point(point(p.get("location"))?);
    let height = p.get("height").and_then(Value::as_f64).filter(|h| *h > 0.0);
    Some(Inclined {
        feature: f,
        top,
        dir,
        base: height.map(|h| add(top, scale3(dir, -h))),
        diameter: p.get("diameter").and_then(Value::as_f64)?,
    })
}

/// Inclined bosses' diameters, called out as an inclined hole's are ([`super::holes`]): in the
/// views that see them as ellipses, most nearly end-on first, the leader to a major-axis end of
/// the rim nearer the viewer (its top when it faces the viewer, else its base). Bosses of one
/// diameter along one line are counted once, as principal ones along one axis are: keyed on the
/// direction, either way along it (as [`holes::Hole::requirement`] keys a hole's, signed), so
/// every member is seen as the first is.
fn inclined_callouts(inclined: &[Inclined], units: crate::units::Units) -> Vec<Want> {
    let fmt = |v| crate::dims::fmt_in(v, units);
    let mut groups: BTreeMap<([i64; 3], i64), Vec<&Inclined>> = BTreeMap::new();
    let mut order = Vec::new();
    for b in inclined {
        // Its largest component made positive: one key for both ways along the line.
        let sign = b.dir[holes_dims::oblique_axes(b.dir)[0]].signum();
        let k = (b.dir.map(|c| holes::size_key(sign * c)), key(b.diameter));
        let members = groups.entry(k).or_default();
        if members.is_empty() {
            order.push(k);
        }
        members.push(b);
    }
    order
        .into_iter()
        .map(|k| {
            let members = &groups[&k];
            let first = members[0];
            let n = members.len();
            let mut terms = Vec::new();
            if n > 1 {
                terms.push(format!("{n}×"));
            }
            terms.push(format!("ø{}", fmt(first.diameter)));
            let axes = holes_dims::oblique_axes(first.dir);
            let arcs = members
                .iter()
                .flat_map(|b| {
                    holes_dims::oblique_axes(b.dir).into_iter().flat_map(|k| {
                        let t = holes_dims::toward(k);
                        let facing = (0..3).map(|j| b.dir[j] * t[j]).sum::<f64>() > 0.0;
                        let end = if facing { b.top } else { b.base.unwrap_or(b.top) };
                        holes_dims::rim_tips(end, b.dir, 0.5 * b.diameter, k)
                    })
                })
                .collect();
            Want { units: units,
                id: want_id(&first.feature.id, "boss.diameter"),
                views: axes.into_iter().map(end_on).collect(),
                value: first.diameter,
                terms,
                measurements: members
                    .iter()
                    .map(|b| Measurement {
                        feature: b.feature.id.clone(),
                        parameter: "boss.diameter".into(),
                        value: b.diameter,
                    })
                    .collect(),
                turning: None,
                basis: format!(
                    "{}; its axis is inclined to every principal axis, so it is called out where it is seen most nearly end-on, as an ellipse, the leader to its rim",
                    if n > 1 {
                        format!("{n} bosses of one diameter are called out once and counted")
                    } else {
                        "a boss's diameter is called out with a leader".to_string()
                    }
                ),
                shape: Shape::Callout {
                    sites: members.iter().map(|b| (b.top, 0.5 * b.diameter)).collect(),
                    arcs,
                },
            }
        })
        .collect()
}

/// A polygonal boss as quiddity records it (`axis` a letter of its frame, `base` and `top` along
/// it, `center` between them, `across_flats`, `side_count`, and each flat's outward direction and
/// centre), or why it cannot be dimensioned.
pub(super) fn read_polygon<'a>(f: &'a Feature, frame: &QFrame) -> Result<Boss<'a>, String> {
    let p = &f.parameters;
    let number = |name: &str| {
        p.get(name)
            .and_then(Value::as_f64)
            .ok_or_else(|| format!("its record gives no {name}"))
    };
    let points = |name: &str| {
        p.get(name)
            .and_then(Value::as_array)
            .and_then(|a| {
                a.iter()
                    .map(|v| point(Some(v)))
                    .collect::<Option<Vec<P3>>>()
            })
            .ok_or_else(|| format!("its record gives no {name}"))
    };
    let q = p
        .get("axis")
        .and_then(Value::as_str)
        .and_then(|a| AXES.iter().position(|x| *x == a))
        .ok_or("its record gives no axis")?;
    // Invariant: AXES contains exactly the three supported coordinate letters.
    let along = frame.letter(&Value::from(AXES[q])).expect("a letter");
    let axis = principal(along)
        .ok_or("its axis is not along x, y or z, so no standard view looks down it")?;
    let sides = p
        .get("side_count")
        .and_then(Value::as_u64)
        .ok_or("its record gives no side_count")?;
    let across_flats = number("across_flats")?;
    let centre = point(p.get("center")).ok_or("its record gives no center")?;
    // quiddity rounds `center` to 1e-4 mm in its own frame, whose origin is not round in the
    // part's, so a boss 400 mm from an edge reads 400.00002 there. Across its axis the box around
    // its faces (the fingerprint's position) is exact for a round boss and a prism with an even
    // number of sides (not an odd one: a pentagon's box is not centred on it); it is taken when
    // it agrees within that rounding, and the record otherwise.
    let c = f.fingerprint.position;
    let at = |t: f64| {
        let mut l = centre;
        l[q] = t;
        let mut p = frame.point(l);
        for k in (0..3).filter(|&k| k != axis) {
            if (c[k] - p[k]).abs() <= 1e-3 {
                p[k] = c[k];
            }
        }
        p
    };
    let (base, top) = (number("base")?, number("top")?);
    let flats: Vec<P3> = points("flat_centres")?
        .into_iter()
        .map(|c| frame.point(c))
        .collect();
    let normals: Vec<P3> = points("flat_directions")?
        .into_iter()
        .map(|d| holes::dir_to_part(&frame.0, d))
        .collect();
    if sides < 3 || flats.len() as u64 != sides || normals.len() as u64 != sides {
        return Err(format!(
            "its record gives {} flat centres and {} directions for {sides} sides",
            flats.len(),
            normals.len()
        ));
    }
    // Its corners lie on the circumscribed circle, a half-angle either side of each flat's
    // normal, whatever order the flats are listed in.
    let half_angle = std::f64::consts::PI / sides as f64;
    let r = 0.5 * across_flats / half_angle.cos();
    let mut half = [0.0; 3];
    for n in &normals {
        let side = [0, 1, 2]
            .map(|k| along[(k + 1) % 3] * n[(k + 2) % 3] - along[(k + 2) % 3] * n[(k + 1) % 3]);
        for s in [-1.0, 1.0] {
            for (j, h) in half.iter_mut().enumerate() {
                let c = r * (half_angle.cos() * n[j] + s * half_angle.sin() * side[j]);
                *h = f64::max(*h, c.abs());
            }
        }
    }
    let height = Some((top - base).abs()).filter(|h| *h > 0.0);
    Ok(Boss {
        feature: f,
        axis,
        top: at(top),
        base: height.map(|_| at(base)),
        section: Section::Polygon {
            sides,
            across_flats,
            flats,
            half,
        },
        height,
    })
}

pub(super) fn not_dimensioned(f: &Feature, why: &str) -> Problem {
    Problem {
        id: format!("p:feature_not_dimensioned:{}", f.id),
        code: "feature_not_dimensioned".into(),
        severity: "warning".into(),
        message: format!(
            "{} is not dimensioned: {why}.",
            crate::wording::capital(&crate::wording::feature(f))
        ),
        subjects: vec![f.id.clone()],
        ..Default::default()
    }
}

/// Keep an established exterior statement's view. A formerly interior statement
/// whose rim is actually hidden needs the visible-profile repair instead.
pub(super) fn prefer_established(analysis: &Analysis, wants: &mut [Want]) {
    let Some(interior) =
        crate::planning::lookup::<BTreeSet<String>>(analysis, "canonical-interior-statements", &[])
    else {
        return;
    };
    let Some(recognition) = &analysis.recognition else {
        return;
    };
    let frame = QFrame(recognition.frame);
    for w in wants {
        if w.views.len() < 2
            || !matches!(w.shape, Shape::Callout { .. })
            || !w
                .measurements
                .iter()
                .all(|m| m.parameter == "boss.diameter")
            || interior.contains(&w.id)
        {
            continue;
        }
        let Some(f) = w
            .measurements
            .first()
            .and_then(|m| analysis.features.iter().find(|f| f.id == m.feature))
        else {
            continue;
        };
        let Ok(b) = read(f, &frame) else { continue };
        let view = end_on(b.axis);
        w.views.retain(|&v| v != view);
        w.views.insert(0, view);
        w.basis = "the established exterior cylindrical statement keeps its end view and every diameter claim".into();
    }
}

/// The same counted cylindrical statement, with physical attachment stations
/// on the visible profile's generators and end rims. The selected view determines
/// the radial axis; an explicitly requested end view keeps circular rim sites.
pub(super) fn route_profile(analysis: &Analysis, w: &mut Want, view: &str) {
    super::modelled_threads::route_profile(analysis, w, view);
    if w.views.len() < 2
        || !matches!(w.shape, Shape::Callout { .. })
        || !w
            .measurements
            .iter()
            .all(|m| m.parameter == "boss.diameter")
    {
        return;
    }
    let Some(recognition) = &analysis.recognition else {
        return;
    };
    let frame = QFrame(recognition.frame);
    let Some((_, toward)) = super::TOWARD.iter().find(|(n, _)| *n == view) else {
        return;
    };
    let mut sites = Vec::new();
    for m in &w.measurements {
        let Some(f) = analysis.features.iter().find(|f| f.id == m.feature) else {
            continue;
        };
        let Ok(b) = read(f, &frame) else { continue };
        let Section::Round(d) = b.section else {
            continue;
        };
        if toward[b.axis].abs() > 0.9 {
            sites.push((b.top, d / 2.0));
            continue;
        }
        let Some(radial) = (0..3).find(|&k| k != b.axis && toward[k] == 0.0) else {
            continue;
        };
        let base = b.base.unwrap_or(b.top);
        for c in [base, scale3(add(base, b.top), 0.5), b.top] {
            for sign in [-1.0, 1.0] {
                let mut p = c;
                p[radial] += sign * d / 2.0;
                sites.push((p, 0.0));
            }
        } // End-cap rim stations project onto the visible cap edge too. They
        // avoid a size's own wall-height witness without inventing an anchor.
        for c in [base, b.top] {
            for fraction in [-0.5_f64, 0.0, 0.5] {
                let mut p = c;
                p[radial] += fraction * d / 2.0;
                let across = (1.0 - fraction * fraction).sqrt() * d / 2.0;
                for k in 0..3 {
                    p[k] += toward[k] * across;
                }
                sites.push((p, 0.0));
            }
        }
    }
    if !sites.is_empty() {
        w.shape = Shape::Callout {
            sites,
            arcs: Vec::new(),
        };
    }
}

fn wants(cx: &Context) -> (Vec<Want>, Vec<Problem>) {
    let fmt = |v| crate::dims::fmt_in(v, cx.spec.units);
    let analysis = cx.analysis;
    let [lo, _] = analysis.envelope;
    let mut problems = Vec::new();
    let mut bosses = Vec::new();
    let mut inclined: Vec<Inclined> = Vec::new();
    for f in &analysis.features {
        let read = match f.kind.as_str() {
            "boss" if groove_floor(analysis, f) || turned_band(analysis, f) => {
                continue;
            }
            "boss" if let Some(b) = read_inclined(f, &cx.frame) => {
                problems.push(Problem {
                    message: format!(
                        "{}'s height and location are not dimensioned: its axis is inclined to every principal axis, so they need a view square to it; its diameter is called out where it is seen most nearly end-on.",
                        crate::wording::capital(&crate::wording::feature(f))
                    ),
                    ..not_dimensioned(f, "")
                });
                inclined.push(b);
                continue;
            }
            "boss" => read(f, &cx.frame),
            "polygonal_boss" => read_polygon(f, &cx.frame),
            _ => continue,
        };
        match read {
            Ok(b) => bosses.push(b),
            Err(why) => problems.push(not_dimensioned(f, &why)),
        }
    }
    // A thread or knurl a boss owns follows its diameter, as a turned step's does
    // ([`super::turned`], `_manufacturing_suffix`).
    let manufacturing = if bosses.is_empty() {
        crate::pmi_lowering::Manufacturing::default()
    } else {
        crate::pmi_lowering::manufacturing(analysis)
    };
    let carried = |id: &str, terms: &mut Vec<String>| {
        // An external thread's designation stands in place of the diameter (ISO 6410).
        if manufacturing.replaces_diameter(id) {
            terms.retain(|t| !t.starts_with('ø'));
        }
        terms.extend(manufacturing.terms(id).0);
    };
    let on_turning = |b: &Boss| {
        matches!(b.section, Section::Round(_))
            && cx.turned.as_ref().is_some_and(|t| {
                b.axis == t.axis && (0..3).all(|j| j == t.axis || (b.top[j] - t.on[j]).abs() < 1e-3)
            })
    };
    // Recognised holes, by the axis they lie along and where they pierce the view down it.
    let drilled: Vec<(usize, P3)> = holes::read(analysis)
        .into_iter()
        .filter_map(|(_, h)| {
            let h = h.ok()?;
            Some((h.principal?.0, h.at))
        })
        .collect();

    let mut out: Vec<Want> = Vec::new();
    // The diameter item of each size of round band on the turning axis, by its index in *out*.
    let mut bands: BTreeMap<i64, usize> = BTreeMap::new();
    // Callouts by axis, side count (0 for a cylinder) and size; a boss carrying a thread or knurl
    // keeps its own callout (one annotation per owner), keyed by its index + 1 (0 means shared).
    let mut circles: BTreeMap<(usize, u64, i64, usize), Vec<usize>> = BTreeMap::new();
    let mut order = Vec::new();
    let mut located: BTreeMap<(&'static str, usize, i64), Vec<usize>> = BTreeMap::new();
    for (i, b) in bosses.iter().enumerate() {
        // The blend's radius owns this same circle; its independent height is still stated.
        if crate::lint::bosses::boss_owner(analysis, b.feature).is_none() {
            if on_turning(b) {
                let Section::Round(diameter) = b.section else {
                    // Invariant: on_turning admits only round profile bands.
                    unreachable!("a band of the profile is round")
                };
                let r = 0.5 * diameter;
                let views = profile_views(b.axis);
                let j = (0..3)
                    .find(|&j| j != b.axis && profile_views(j).contains(&views[0]))
                    // Invariant: The fixed principal/profile view tables include both radial axes.
                    .expect("a profile view shows a radial axis");
                let at = match b.base {
                    Some(base) => scale3(add(base, b.top), 0.5),
                    None => b.top,
                };
                let mut a = at;
                let mut c = at;
                a[j] -= r;
                c[j] += r;
                let id = b.feature.id.as_str();
                // Equal bands of the profile share one diameter (`2× ø11.2`, #220); one carrying
                // a thread or knurl keeps its own (one annotation per owner).
                let plain = manufacturing.on(id).is_empty();
                if let Some(&w) = bands.get(&key(diameter)).filter(|_| plain) {
                    let w = &mut out[w];
                    w.measurements.push(Measurement {
                        feature: b.feature.id.clone(),
                        parameter: "boss.diameter".into(),
                        value: diameter,
                    });
                    let n = w.measurements.len();
                    if n == 2 {
                        w.terms.insert(0, String::new());
                    }
                    w.terms[0] = format!("{n}×");
                    w.basis = format!(
                        "the part is turned about {}, and {n} equal bosses are bands of its profile: one diameter states them all, across the first in a view that shows the profile",
                        AXES[b.axis]
                    );
                } else {
                    if plain {
                        bands.insert(key(diameter), out.len());
                    }
                    let mut terms = vec![format!("ø{}", fmt(diameter))];
                    carried(id, &mut terms);
                    out.push(Want { units: cx.spec.units,
                    id: want_id(id, "boss.diameter"),
                    views: vec![views[0]],
                    value: diameter,
                    terms,
                    measurements: std::iter::once(Measurement {
                        feature: b.feature.id.clone(),
                        parameter: "boss.diameter".into(),
                        value: diameter,
                    })
                    .chain(super::turned::claims(&manufacturing, vec![id], diameter))
                    .collect(),
                    turning: None,
                    basis: format!(
                        "the part is turned about {}, and this boss is a band of its profile: its diameter is stated across it in a view that shows the profile{}",
                        AXES[b.axis],
                        super::turned::carries(&manufacturing, &[id], "boss")
                    ),
                    shape: Shape::Linear {
                        a,
                        b: c,
                        axis: j,
                        a_edge: false,
                        b_edge: false,
                        order: 0,
                    },
                });
                }
            } else {
                let own = if manufacturing.on(&b.feature.id).is_empty() {
                    0
                } else {
                    i + 1
                };
                let k = match &b.section {
                    Section::Round(d) => (b.axis, 0, key(*d), own),
                    Section::Polygon {
                        sides,
                        across_flats,
                        ..
                    } => (b.axis, *sides, key(*across_flats), own),
                };
                let members = circles.entry(k).or_default();
                if members.is_empty() {
                    order.push(k);
                }
                members.push(i);
                let view = end_on(b.axis);
                let coaxial = drilled.iter().any(|(k, at)| {
                    *k == b.axis && (0..3).all(|j| j == b.axis || (at[j] - b.top[j]).abs() < 1e-3)
                });
                if !coaxial {
                    for a in (0..3).filter(|&a| a != b.axis) {
                        let d = b.top[a] - lo[a];
                        if d.abs() < 1e-6 {
                            let id = &b.feature.id;
                            problems.push(Problem {
                            id: format!("p:location_on_datum:{id}/{}", AXES[a]),
                            code: "location_on_datum".into(),
                            severity: "info".into(),
                            message: format!(
                                "{} lies on the part's {} face, so its {} location measures nothing and is not dimensioned.",
                                crate::wording::capital(&crate::wording::feature(b.feature)),
                                ["left", "front", "bottom"][a],
                                AXES[a]
                            ),
                            subjects: vec![id.clone()],
                            ..Default::default()
                        });
                            continue;
                        }
                        located.entry((view, a, key(d))).or_default().push(i);
                    }
                }
            }
        }
        let (Some(h), Some(base)) = (b.height, b.base) else {
            problems.push(not_dimensioned(
                b.feature,
                "its record gives it no positive height, so the height it stands to is not stated",
            ));
            continue;
        };
        let views = profile_views(b.axis);
        let j = (0..3)
            .find(|&j| j != b.axis && profile_views(j).contains(&views[0]))
            // Invariant: The fixed principal/profile view tables include both radial axes.
            .expect("a profile view shows a radial axis");
        let rim = |p: P3| {
            let mut p = p;
            p[j] += b.half(j);
            p
        };
        out.push(Want { units: cx.spec.units,
            id: want_id(&b.feature.id, "boss_height.length"),
            views,
            value: h,
            terms: vec![fmt(h)],
            measurements: vec![Measurement {
                feature: b.feature.id.clone(),
                parameter: "boss_height.length".into(),
                value: h,
            }],
            turning: None,
            basis: "a boss's height, from its base to its top along its axis, in a view that shows its profile".into(),
            shape: Shape::Linear {
                a: rim(base),
                b: rim(b.top),
                axis: b.axis,
                a_edge: false,
                b_edge: false,
                order: 0,
            },
        });
    }

    for k in order {
        let members = &circles[&k];
        let first = &bosses[members[0]];
        let view = end_on(first.axis);
        let n = members.len();
        let mut terms = Vec::new();
        if n > 1 {
            terms.push(format!("{n}×"));
        }
        let (parameter, value, sites, basis): (&str, f64, Vec<(P3, f64)>, String) = match &first
            .section
        {
            Section::Round(d) => {
                terms.push(format!("ø{}", fmt(*d)));
                carried(&first.feature.id, &mut terms);
                (
                    "boss.diameter",
                    *d,
                    members
                        .iter()
                        .map(|&m| (bosses[m].top, bosses[m].half(0)))
                        .collect(),
                    if n > 1 {
                        format!(
                            "{n} identical bosses along {} are called out once and counted, where they are seen as circles",
                            AXES[first.axis]
                        )
                    } else {
                        "a boss's diameter is called out where it is seen as a circle".into()
                    },
                )
            }
            Section::Polygon {
                sides,
                across_flats,
                ..
            } => {
                terms.push(if *sides == 6 {
                    "HEX".into()
                } else {
                    format!("{sides}-SIDED")
                });
                terms.extend([fmt(*across_flats), "A/F".into()]);
                (
                    "polygon_across_flats.length",
                    *across_flats,
                    // A leader to a flat: each flat face's centre lies on the outline seen
                    // down the axis.
                    members
                        .iter()
                        .flat_map(|&m| match &bosses[m].section {
                            Section::Polygon {
                                flats,
                                sides,
                                across_flats,
                                ..
                            } => {
                                let boss = &bosses[m];
                                let mut sites = flats.clone();
                                // The newly dimensioned notch bands may obstruct the flat
                                // centres of a bevelled polygonal host. Exact shared edges
                                // prove that host; quarter points remain on its proven flats.
                                let bevel_edges: Vec<_> = analysis
                                    .outer_profiles
                                    .iter()
                                    .filter(|p| {
                                        analysis.features.iter().any(|f| {
                                            f.kind == "oriented_chamfer"
                                                && f.faces.contains(&p.face)
                                        })
                                    })
                                    .flat_map(|p| &p.edges)
                                    .collect();
                                let bevelled = analysis
                                    .outer_profiles
                                    .iter()
                                    .filter(|p| boss.feature.faces.contains(&p.face))
                                    .any(|p| p.edges.iter().any(|e| bevel_edges.contains(&e)));
                                if bevelled && !crate::notches::requirements(analysis).is_empty() {
                                    let normals = boss.feature.parameters["flat_directions"]
                                        .as_array()
                                        // Invariant: The polygonal boss reader validates flat_directions as an array.
                                        .unwrap();
                                    let step = 0.25
                                        * across_flats
                                        * (std::f64::consts::PI / *sides as f64).tan();
                                    let mut axis = [0.; 3];
                                    axis[boss.axis] = 1.;
                                    for (c, n) in flats.iter().zip(normals) {
                                        let n = holes::dir_to_part(
                                            &cx.frame.0,
                                            // Invariant: The polygonal boss reader validates every flat direction as a three-number point.
                                            point(Some(n)).unwrap(),
                                        );
                                        let t = [0, 1, 2].map(|k| {
                                            axis[(k + 1) % 3] * n[(k + 2) % 3]
                                                - axis[(k + 2) % 3] * n[(k + 1) % 3]
                                        });
                                        for sign in [-1., 1.] {
                                            sites
                                                .push([0, 1, 2].map(|k| c[k] + sign * step * t[k]));
                                        }
                                    }
                                }
                                sites
                            }
                            // Invariant: The group was filtered to polygonal bosses by its side count.
                            Section::Round(_) => unreachable!("grouped by side count"),
                        })
                        .map(|c| (c, 0.0))
                        .collect(),
                    if n > 1 {
                        format!(
                            "{n} identical {sides}-sided bosses along {} are called out once and counted, across their flats, where their outlines are seen",
                            AXES[first.axis]
                        )
                    } else {
                        format!(
                            "a {sides}-sided boss's size across its flats is called out where its outline is seen"
                        )
                    },
                )
            }
        };
        let profile = !crate::planning::lookup::<bool>(analysis, "canonical-circular-recipes", &[])
            .unwrap_or(false)
            && matches!(first.section, Section::Round(_))
            && members.iter().all(|&m| bosses[m].base.is_some())
            && analysis
                .recognition
                .as_ref()
                .is_some_and(|r| r.recogniser.starts_with("quiddity-rust "))
            && super::interior::visible_circular_rims(analysis, view, &sites) == Some(false);
        let views = if profile {
            let mut v = profile_views(first.axis);
            v.push(view);
            v
        } else {
            vec![view]
        };
        let basis = if profile {
            format!(
                "{n} identical cylindrical bosses retain every diameter claim; their end-on rims are occluded, so visible profile walls are preferred"
            )
        } else {
            basis
        } + &super::turned::carries(&manufacturing, &[&first.feature.id], "boss");
        out.push(Want {
            units: cx.spec.units,
            id: want_id(&first.feature.id, parameter),
            views,
            value,
            terms,
            measurements: members
                .iter()
                .map(|&m| Measurement {
                    feature: bosses[m].feature.id.clone(),
                    parameter: parameter.into(),
                    value: match bosses[m].section {
                        Section::Round(d) => d,
                        Section::Polygon { across_flats, .. } => across_flats,
                    },
                })
                // Only a round callout prints what the boss carries, so only it claims it.
                .chain(
                    super::turned::claims(&manufacturing, vec![first.feature.id.as_str()], value)
                        .filter(|_| parameter == "boss.diameter"),
                )
                .collect(),
            turning: None,
            basis,
            shape: Shape::Callout {
                sites,
                arcs: Vec::new(),
            },
        });
    }

    out.extend(inclined_callouts(&inclined, cx.spec.units));
    for ((view, a, _), owners) in located {
        let first = &bosses[owners[0]];
        let d = first.top[a] - lo[a];
        let parameter = format!("location.length.{}", AXES[a]);
        // Invariant: The selected view comes from the fixed principal view table.
        let view: &'static str = NAMES.iter().find(|n| **n == view).expect("a view");
        let mut from = first.top;
        from[a] = lo[a];
        out.push(Want {
            units: cx.spec.units,
            id: want_id(&first.feature.id, &parameter),
            views: vec![view],
            value: d,
            terms: vec![fmt(d)],
            measurements: owners
                .iter()
                .map(|&i| Measurement {
                    feature: bosses[i].feature.id.clone(),
                    parameter: parameter.clone(),
                    value: d,
                })
                .collect(),
            turning: None,
            basis: format!(
                "located in {} from the part's {} face, in the view that looks down the boss{}",
                AXES[a],
                ["left", "front", "bottom"][a],
                if owners.len() > 1 {
                    "; one dimension serves every boss at this distance"
                } else {
                    ""
                }
            ),
            shape: Shape::Linear {
                a: from,
                b: first.top,
                axis: a,
                a_edge: true,
                b_edge: false,
                order: 1,
            },
        });
    }
    (out, problems)
}

/// How far two support planes may differ and still be one face: quiddity records a boss's
/// height to 0.01 mm, so the base it gives lies within half that of the face it stands on.
/// draftwright's `_same_support_planes` compares at 1e-6; the difference is a maintainer
/// question in `docs/next-session.md`.
const SAME_PLANE: f64 = 0.005 + 1e-6;

/// One measurement, one owner (ISSUE-1154-a, draftwright's `_consolidated_owner`): a boss
/// height that runs between the same two support planes as an overall extent (a hub flush with
/// both faces of a plate) states the same fact twice, so the overall extent keeps it and the
/// height moves to *omitted*, naming the dimension that carries it.
///
/// The comparison is of planes, never of values: a boss standing proud keeps its height even
/// when it equals an extent between other faces. Only a default height yields (one the spec
/// lists is kept), and only to an extent that is itself kept: an extent the spec omits, or a
/// part turned whole (whose overall length stands in for the step chain draftwright withholds
/// it for: ISSUE-1511-a's inactive owner), takes nothing, so the fact is never moved to nowhere.
pub(super) fn consolidate(
    analysis: &Analysis,
    kept: &mut Vec<(Want, bool, Option<&'static str>, Vec<&'static str>)>,
    omitted: &mut Vec<Omitted>,
) {
    let frame = QFrame(analysis.recognition.as_ref().map_or(
        [[0.0; 3], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        |r| r.frame,
    ));
    if super::turned::turned(analysis, &frame).is_some() {
        return;
    }
    let planes = |w: &Want| match w.shape {
        Shape::Linear { a, b, axis, .. } => {
            Some((axis, a[axis].min(b[axis]), a[axis].max(b[axis])))
        }
        Shape::Callout { .. } | Shape::Angular { .. } | Shape::Aligned { .. } => None,
    };
    let parameter = |w: &Want| w.measurements.first().map(|m| m.parameter.clone());
    let extents: Vec<(String, (usize, f64, f64))> = kept
        .iter()
        .filter(|(w, ..)| {
            crate::dims::is_envelope_extent(&w.id)
                && parameter(w).is_some_and(|p| {
                    ["width.length", "depth.length", "height.length"].contains(&p.as_str())
                })
        })
        .filter_map(|(w, ..)| Some((w.id.clone(), planes(w)?)))
        .collect();
    let mut gone = BTreeSet::new();
    for (i, (w, default, ..)) in kept.iter().enumerate() {
        if !*default || parameter(w).as_deref() != Some("boss_height.length") {
            continue;
        }
        let Some((k, lo, hi)) = planes(w) else {
            continue;
        };
        let Some((owner, _)) = extents.iter().find(|(_, (k2, lo2, hi2))| {
            *k2 == k && (lo - lo2).abs() <= SAME_PLANE && (hi - hi2).abs() <= SAME_PLANE
        }) else {
            continue;
        };
        gone.insert(i);
        omitted.push(Omitted {
            id: w.id.clone(),
            label: w.terms.join(" "),
            basis: format!(
                "conveyed by {owner}: the boss spans the part's full {} extent, between the same two support planes, so the overall extent states its height (one measurement, one owner)",
                AXES[k]
            ),
            state: crate::items::State::Covered {
                by: owner.to_string(),
            },
        });
    }
    let mut i = 0;
    kept.retain(|_| {
        i += 1;
        !gone.contains(&(i - 1))
    });
}
