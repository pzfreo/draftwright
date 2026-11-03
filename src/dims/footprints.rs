//! Feature footprints from the Analysis, shared by leader scoring and collision reporting.
//! These are drafting obstacles, not projected line-work (ADR R1). Circular openings are exact;
//! side-on cylinders and slots use their projected bounds. Principal-face inner loops fill gaps
//! in recognition. The Analysis retains no complete outer wire, so solid bounds detect re-entry
//! (`crate::material`) and measure the distance to an exit without pretending to describe a
//! concave silhouette.

use std::cell::{OnceCell, RefCell};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::rc::Rc;

use super::placement::Frame;
use super::{Measurement, P2, P3};
use crate::bridge::holes::{self, dir_to_part, point, to_part};
use crate::lint::profiles::{Kind, Wire};
use crate::model::Analysis;

const HAIR: f64 = 0.05;

#[derive(Clone)]
enum Shape {
    Circle(P2, f64),
    Box([f64; 4]),
    Loop(Vec<[P2; 2]>, [f64; 4]),
}

struct Footprint {
    id: String,
    shape: Shape,
}

pub(super) struct Footprints {
    slot_layout: bool,
    features: Vec<Footprint>,
    solids: Vec<[f64; 4]>,
    view: String,
    members: BTreeMap<String, BTreeSet<String>>,
    // Shafts recur across jobs and the placement/refinement passes of this resolved view.
    // Exact bits keep near-equal candidates distinct; no cache survives a changed frame.
    counts: RefCell<HashMap<[u64; 4], usize>>,
    index: OnceCell<Index>,
    extensions: RefCell<HashMap<ExtensionKey, BTreeSet<String>>>,
    extension_counts: RefCell<HashMap<OwnersKey, Rc<ExtensionCounts>>>,
    extension_count_values: RefCell<HashMap<(usize, [u64; 4]), usize>>,
}

type OwnersKey = Vec<(String, String, u64)>;
type ExtensionKey = ([u64; 4], OwnersKey);

struct ExtensionCounts {
    // A repeated footprint of the same feature counts once. Owned features
    // and the view's material are excluded exactly as crossed_extension does.
    features: Vec<Option<usize>>,
    id: usize,
}

/// Physical owners are fixed throughout one dimension's lane search.
/// Both paths below use the same clipping predicate, including the foreign
/// opening check; only the ownership preparation is shared.
pub(super) struct WitnessClipper<'a> {
    shapes: Vec<&'a Shape>,
    slot_width: bool,
    extensions: ExtensionCounter<'a>,
}
impl WitnessClipper<'_> {
    pub(super) fn clip(&self, line: &mut [P2; 2]) -> bool {
        clip_witness(line, self.shapes.iter().copied(), self.slot_width, |s| {
            self.extensions.count(s) != 0
        })
    }
}
fn clip_witness<'a>(
    line: &mut [P2; 2],
    shapes: impl Iterator<Item = &'a Shape>,
    slot_width: bool,
    crossed: impl Fn([P2; 2]) -> bool,
) -> bool {
    let exit = shapes
        .flat_map(|s| match s {
            Shape::Box(b) if slot_width => clip_box(*line, *b, true).into_iter().collect(),
            _ => s.spans(*line),
        })
        .filter(|(a, _)| *a <= 1e-9)
        .map(|(_, b)| b)
        .max_by(f64::total_cmp);
    if let Some(exit) = exit {
        let d = [line[1][0] - line[0][0], line[1][1] - line[0][1]];
        let length = d[0].hypot(d[1]);
        let t = exit + super::GAP / length.max(1e-12);
        if t >= 1.0 {
            return false;
        }
        let next = [line[0][0] + t * d[0], line[0][1] + t * d[1]];
        if slot_width && crossed([line[0], next]) {
            return false;
        }
        line[0] = next;
    }
    true
}

pub(super) struct ExtensionCounter<'a> {
    footprints: &'a Footprints,
    prepared: Rc<ExtensionCounts>,
}
impl ExtensionCounter<'_> {
    pub(super) fn count(&self, shaft: [P2; 2]) -> usize {
        let key = [shaft[0][0], shaft[0][1], shaft[1][0], shaft[1][1]].map(f64::to_bits);
        let key = (self.prepared.id, key);
        if let Some(&count) = self.footprints.extension_count_values.borrow().get(&key) {
            return count;
        }
        let length = (shaft[1][0] - shaft[0][0]).hypot(shaft[1][1] - shaft[0][1]);
        let mut ids: Vec<_> = self
            .footprints
            .near_indices(shaft)
            .filter_map(|i| {
                let id = self.prepared.features[i]?;
                self.footprints.features[i]
                    .shape
                    .crossed_with_length(shaft, length)
                    .then_some(id)
            })
            .collect();
        ids.sort_unstable();
        ids.dedup();
        let count = ids.len();
        let mut saved = self.footprints.extension_count_values.borrow_mut();
        if saved.len() >= 8192 {
            saved.clear();
        }
        saved.insert(key, count);
        count
    }
}

// A conservative broad phase only: surviving shapes use the unchanged crossing predicate.
struct Index {
    by_x: Vec<(usize, [f64; 4])>,
    bounded: bool,
}

type Projections = BTreeMap<(String, Vec<u64>), Rc<Footprints>>;
type ModelInk = Rc<(super::leaders::SegmentIndex, Vec<[f64; 4]>)>;
type Models = BTreeMap<(String, Vec<u64>, bool), ModelInk>;

/// Plan-local projections, keyed by the resolved frame so a repacked view cannot reuse stale ink.
#[derive(Default)]
pub(crate) struct Cache {
    views: RefCell<Projections>,
    models: RefCell<Models>,
}

fn frame_key(view: &crate::model::PlannedView) -> Vec<u64> {
    let mut key: Vec<_> = view
        .direction
        .iter()
        .chain(&view.up)
        .chain(&view.at)
        .chain(std::iter::once(&view.scale))
        .map(|v| v.to_bits())
        .collect();
    if let Some(detail) = &view.detail {
        key.extend(detail.bounds.iter().flatten().map(|v| v.to_bits()));
    }
    key
}

impl Cache {
    pub(super) fn model(
        &self,
        analysis: &Analysis,
        view: &crate::model::PlannedView,
    ) -> Option<ModelInk> {
        let ink = analysis.edge_ink.as_ref()?;
        let geometry = frame_key(view);
        let cut =
            view.cut.is_some() || view.detail.as_ref().is_some_and(|d| d.source_cut.is_some());
        let key = (view.id.clone(), geometry, cut);
        let mut models = self.models.borrow_mut();
        if models.len() >= 128 && !models.contains_key(&key) {
            models.clear();
        }
        // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
        let frame_key = serde_json::to_vec(&key).expect("model frame serializes");
        Some(
            models
                .entry(key)
                .or_insert_with(|| {
                    crate::planning::memo(analysis, "projected-model-index", frame_key, || {
                        Rc::new(super::leaders::SegmentIndex::for_view(analysis, ink, view))
                    })
                })
                .clone(),
        )
    }

    pub(super) fn get(
        &self,
        analysis: &Analysis,
        view: &crate::model::PlannedView,
    ) -> Rc<Footprints> {
        let key = frame_key(view);
        // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
        let frame_key = serde_json::to_vec(&(&view.id, &key)).expect("footprint frame serializes");
        self.views
            .borrow_mut()
            .entry((view.id.clone(), key))
            .or_insert_with(|| {
                crate::planning::memo(analysis, "projected-feature-footprints", frame_key, || {
                    Rc::new(Footprints::new(
                        analysis,
                        &Frame::new(analysis, view),
                        view.id.trim_start_matches("v:"),
                    ))
                })
            })
            .clone()
    }
}

fn bounds(points: impl Iterator<Item = P2>) -> [f64; 4] {
    points.fold(
        [
            f64::INFINITY,
            f64::INFINITY,
            f64::NEG_INFINITY,
            f64::NEG_INFINITY,
        ],
        |b, p| {
            [
                b[0].min(p[0]),
                b[1].min(p[1]),
                b[2].max(p[0]),
                b[3].max(p[1]),
            ]
        },
    )
}

fn box_points(b: [P3; 2]) -> impl Iterator<Item = P3> {
    (0..8).map(move |i| [0, 1, 2].map(|k| b[(i >> k) & 1][k]))
}

fn clip(s: [P2; 2], b: [f64; 4]) -> Option<(f64, f64)> {
    clip_box(s, b, false)
}

// A witness on its own box's boundary must still leave that rim. Foreign
// feature-crossing queries retain the open-box predicate above.
fn clip_box(s: [P2; 2], b: [f64; 4], closed: bool) -> Option<(f64, f64)> {
    let d = [s[1][0] - s[0][0], s[1][1] - s[0][1]];
    let (mut lo, mut hi) = (0.0_f64, 1.0_f64);
    for k in 0..2 {
        if d[k].abs() < 1e-12 {
            if if closed {
                s[0][k] < b[k] || s[0][k] > b[k + 2]
            } else {
                s[0][k] <= b[k] || s[0][k] >= b[k + 2]
            } {
                return None;
            }
        } else {
            let a = (b[k] - s[0][k]) / d[k];
            let c = (b[k + 2] - s[0][k]) / d[k];
            lo = lo.max(a.min(c));
            hi = hi.min(a.max(c));
        }
    }
    (hi > lo).then_some((lo, hi))
}

impl Shape {
    fn polygon(edges: Vec<[P2; 2]>) -> Self {
        let b = bounds(edges.iter().flatten().copied());
        Self::Loop(edges, b)
    }

    fn spans(&self, s: [P2; 2]) -> Vec<(f64, f64)> {
        let d = [s[1][0] - s[0][0], s[1][1] - s[0][1]];
        let len2 = d[0] * d[0] + d[1] * d[1];
        if len2 < 1e-18 {
            return vec![];
        }
        match self {
            Self::Box(b) => clip(s, *b).into_iter().collect(),
            Self::Circle(c, r) => {
                let q = [s[0][0] - c[0], s[0][1] - c[1]];
                let b = q[0] * d[0] + q[1] * d[1];
                let disc = b * b - len2 * (q[0] * q[0] + q[1] * q[1] - r * r);
                if disc <= 0.0 {
                    return vec![];
                }
                let lo = ((-b - disc.sqrt()) / len2).max(0.0);
                let hi = ((-b + disc.sqrt()) / len2).min(1.0);
                if hi > lo { vec![(lo, hi)] } else { vec![] }
            }
            Self::Loop(edges, _) => {
                let cross = |a: P2, b: P2| a[0] * b[1] - a[1] * b[0];
                let mut cuts = vec![0.0, 1.0];
                for [a, b] in edges {
                    let e = [b[0] - a[0], b[1] - a[1]];
                    let den = cross(d, e);
                    if den.abs() < 1e-12 {
                        continue;
                    }
                    let q = [a[0] - s[0][0], a[1] - s[0][1]];
                    let (t, u) = (cross(q, e) / den, cross(q, d) / den);
                    if (0.0..=1.0).contains(&t) && (0.0..=1.0).contains(&u) {
                        cuts.push(t);
                    }
                }
                cuts.sort_by(f64::total_cmp);
                cuts.dedup_by(|a, b| (*a - *b).abs() < 1e-9);
                cuts.windows(2)
                    .filter_map(|w| {
                        let t = 0.5 * (w[0] + w[1]);
                        let p = [s[0][0] + t * d[0], s[0][1] + t * d[1]];
                        let inside = edges
                            .iter()
                            .filter(|[a, b]| {
                                (a[1] > p[1]) != (b[1] > p[1])
                                    && p[0] < a[0] + (p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
                            })
                            .count()
                            % 2
                            == 1;
                        inside.then_some((w[0], w[1]))
                    })
                    .collect()
            }
        }
    }

    #[cfg(test)]
    fn crossed(&self, s: [P2; 2]) -> bool {
        self.crossed_with_length(s, (s[1][0] - s[0][0]).hypot(s[1][1] - s[0][1]))
    }

    fn crossed_with_length(&self, s: [P2; 2], len: f64) -> bool {
        // Reject disjoint bounds before testing loop edges. Convex shapes need one interval.
        let Some((a, b)) = clip(s, self.bounds()) else {
            return false;
        };
        if (b - a) * len <= HAIR {
            return false;
        }
        if let Self::Box(_) = self {
            return a * len > HAIR;
        }
        if let Self::Circle(c, r) = self
            && (s[0][0] - c[0]).hypot(s[0][1] - c[1]) <= *r
        {
            return false;
        }
        self.spans(s)
            .iter()
            .any(|(a, b)| (b - a) * len > HAIR && a * len > HAIR)
    }

    fn bounds(&self) -> [f64; 4] {
        match self {
            Self::Circle(c, r) => [c[0] - r, c[1] - r, c[0] + r, c[1] + r],
            Self::Box(b) => *b,
            Self::Loop(_, b) => *b,
        }
    }
}

fn wire(w: &Wire, frame: &Frame) -> Shape {
    let mut lines = Vec::new();
    for e in &w.edges {
        let (a, b, m) = (frame.page(e.start), frame.page(e.end), frame.page(e.mid));
        if e.kind == Kind::Circle
            && let (Some(c), Some(r)) = (e.centre, e.radius)
        {
            let c = frame.page(c);
            let r = r * frame.scale();
            let angle = |p: P2| (p[1] - c[1]).atan2(p[0] - c[0]);
            let start = angle(a);
            let mut sweep = (angle(b) - start).rem_euclid(std::f64::consts::TAU);
            let mid = (angle(m) - start).rem_euclid(std::f64::consts::TAU);
            if (a[0] - b[0]).hypot(a[1] - b[1]) < 1e-6 {
                sweep = std::f64::consts::TAU;
            } else if mid > sweep {
                sweep -= std::f64::consts::TAU;
            }
            let n = (sweep.abs() / (std::f64::consts::PI / 64.0)).ceil() as usize;
            let mut last = a;
            for j in 1..=n {
                let t = start + sweep * j as f64 / n as f64;
                let next = if j == n {
                    b
                } else {
                    [c[0] + r * t.cos(), c[1] + r * t.sin()]
                };
                lines.push([last, next]);
                last = next;
            }
        } else {
            lines.push([a, b]);
        }
    }
    Shape::polygon(lines)
}

impl Footprints {
    pub(super) fn new(analysis: &Analysis, frame: &Frame, view: &str) -> Self {
        let qframe = holes::frame(analysis);
        let mut features = Vec::new();
        for (_, read) in holes::read(analysis) {
            let Ok(h) = read else { continue };
            let at = frame.page(h.at);
            let along = frame.page([0, 1, 2].map(|k| h.at[k] + h.direction[k]));
            let radial = h.opening_diameter() * 0.5;
            let shape = if (at[0] - along[0]).hypot(at[1] - along[1]) < 1e-6 {
                Shape::Circle(at, radial * frame.scale())
            } else {
                // The face-box centre is in the part's frame and does not assume which end
                // a record's drilling direction starts at.
                let c = h.feature.fingerprint.position;
                let depth = h.depth.unwrap_or_else(|| {
                    (0..3)
                        .map(|k| {
                            (analysis.envelope[1][k] - analysis.envelope[0][k])
                                * h.direction[k].abs()
                        })
                        .sum()
                });
                let b = [0, 1].map(|j| {
                    [0, 1, 2].map(|k| {
                        c[k] + if j == 0 { -1.0 } else { 1.0 }
                            * (0.5 * depth * h.direction[k].abs()
                                + radial * (1.0 - h.direction[k] * h.direction[k]).max(0.0).sqrt())
                    })
                });
                Shape::Box(bounds(box_points(b).map(|p| frame.page(p))))
            };
            features.push(Footprint {
                id: h.feature.id.clone(),
                shape,
            });
        }
        for f in &analysis.features {
            if f.kind == "double_d_bore"
                && let (Some(axis), Some(location), Some(diameter)) = (
                    point(f.parameters.get("axis")),
                    point(f.parameters.get("location")),
                    f.parameters.get("major_diameter").and_then(|v| v.as_f64()),
                )
            {
                let c = f.fingerprint.position;
                let direction = dir_to_part(&qframe, axis);
                let at = frame.page(to_part(&qframe, location));
                let end = frame.page([0, 1, 2].map(|k| c[k] + direction[k]));
                let middle = frame.page(c);
                let shape = if (middle[0] - end[0]).hypot(middle[1] - end[1]) < 1e-6 {
                    Shape::Circle(at, 0.5 * diameter * frame.scale())
                } else {
                    let depth = (0..3)
                        .map(|k| {
                            (analysis.envelope[1][k] - analysis.envelope[0][k]) * direction[k].abs()
                        })
                        .sum::<f64>();
                    let b = [0, 1].map(|j| {
                        [0, 1, 2].map(|k| {
                            c[k] + if j == 0 { -1.0 } else { 1.0 }
                                * (0.5 * depth * direction[k].abs()
                                    + 0.5
                                        * diameter
                                        * (1.0 - direction[k] * direction[k]).max(0.0).sqrt())
                        })
                    });
                    Shape::Box(bounds(box_points(b).map(|p| frame.page(p))))
                };
                features.push(Footprint {
                    id: f.id.clone(),
                    shape,
                });
            }
            if f.kind == "section_recess"
                && let Ok(crate::lint::section_recesses::Grammar::Pocket(p)) =
                    crate::lint::section_recesses::grammar(f)
            {
                let b = [0, 1].map(|j| {
                    [0, 1, 2].map(|k| {
                        p.origin[k]
                            + if j == 0 { -1.0 } else { 1.0 }
                                * 0.5
                                * if k == p.axis {
                                    p.depth
                                } else if k == p.long_axis {
                                    p.length
                                } else {
                                    p.width
                                }
                    })
                });
                features.push(Footprint {
                    id: f.id.clone(),
                    shape: Shape::Box(bounds(
                        box_points(b).map(|p| frame.page(to_part(&qframe, p))),
                    )),
                });
            }
        }
        for f in &analysis.features {
            if f.kind == "slot" {
                let p = &f.parameters;
                let axis = |name| {
                    p.get(name)
                        .and_then(|v| v.as_str())
                        .and_then(|a| ["x", "y", "z"].iter().position(|v| *v == a))
                };
                let number = |name| p.get(name).and_then(|v| v.as_f64());
                if let (
                    Some(l),
                    Some(w),
                    Some(lo),
                    Some(hi),
                    Some(c),
                    Some(width),
                    Some(dlo),
                    Some(dhi),
                ) = (
                    axis("long_axis"),
                    axis("width_axis"),
                    number("lo"),
                    number("hi"),
                    number("w_center"),
                    number("width"),
                    number("d_lo"),
                    number("d_hi"),
                ) && l != w
                {
                    let mut b = [[0.0; 3]; 2];
                    b[0][l] = lo;
                    b[1][l] = hi;
                    b[0][w] = c - 0.5 * width;
                    b[1][w] = c + 0.5 * width;
                    b[0][3 - l - w] = dlo;
                    b[1][3 - l - w] = dhi;
                    features.push(Footprint {
                        id: f.id.clone(),
                        shape: Shape::Box(bounds(
                            box_points(b).map(|p| frame.page(to_part(&qframe, p))),
                        )),
                    });
                }
            }
        }
        // Only end-on principal faces describe opening footprints. Duplicate boundary faces
        // share their feature ID, so a shaft counts each crossed feature just once.
        for source in &analysis.principal_profiles {
            for face in &source.faces {
                let axis = [0, 1, 2].map(|k| f64::from(k == face.axis));
                let a = frame.page([0.0; 3]);
                let b = frame.page(axis);
                if (a[0] - b[0]).hypot(a[1] - b[1]) > 1e-6 || face.unsplit {
                    continue;
                }
                for w in &face.inner {
                    let shape = wire(w, frame);
                    let b = shape.bounds();
                    let matching = features
                        .iter()
                        .filter(|f| {
                            let c = f.shape.bounds();
                            (0..4).all(|k| (b[k] - c[k]).abs() < 0.1 * frame.scale())
                        })
                        .min_by_key(|f| &f.id);
                    if matching.is_some() {
                        continue;
                    }
                    // An unrecognised opening belongs to the view's outline, not an invented
                    // feature ID. Recognised non-round openings use their actual feature.
                    let centre = [0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3])];
                    let id = analysis
                        .features
                        .iter()
                        .filter(|f| {
                            matches!(
                                f.kind.as_str(),
                                "pocket" | "section_recess" | "double_d_bore" | "polygonal_bore"
                            )
                        })
                        .find(|f| {
                            let p = frame.page(f.fingerprint.position);
                            (p[0] - centre[0]).hypot(p[1] - centre[1]) < 0.1 * frame.scale()
                        })
                        .map_or_else(|| format!("v:{view}"), |f| f.id.clone());
                    features.push(Footprint { id, shape });
                }
            }
        }
        // The material the router's re-entry and lint's `leader_crosses_silhouette` share
        // (ADR2-8): each solid's projected box.
        let solids = crate::material::solids(analysis, |p| frame.page(p));
        let mut members: BTreeMap<String, BTreeSet<String>> = analysis
            .recognition
            .iter()
            .flat_map(|r| &r.patterns)
            .map(|p| (p.id.clone(), p.members.iter().cloned().collect()))
            .collect();
        for f in &analysis.features {
            if f.kind == "slot_pattern"
                && let Some(slots) = f.parameters.get("slots").and_then(|v| v.as_array())
            {
                members.insert(
                    f.id.clone(),
                    slots
                        .iter()
                        .filter_map(|v| v.as_str().map(String::from))
                        .collect(),
                );
            }
        }
        // A face-level height also witnesses the recess wall that supplies
        // that exact level support. Match the real face and all support bounds;
        // another face at the same height does not confer ownership.
        for level in &analysis.level_supports {
            for feature in analysis
                .features
                .iter()
                .filter(|f| f.kind == "section_recess")
            {
                let owns_support = feature
                    .faces
                    .iter()
                    .filter_map(|&i| analysis.face_attachments.get(i))
                    .any(|f| {
                        f.surface.as_deref() == Some("plane")
                            && f.axis == Some(2)
                            && f.bounds.is_some_and(|b| {
                                let wanted = [
                                    level.x_span[0],
                                    level.y_span[0],
                                    level.z,
                                    level.x_span[1],
                                    level.y_span[1],
                                    level.z,
                                ];
                                b.iter().zip(wanted).all(|(a, b)| (a - b).abs() <= 1e-6)
                            })
                    });
                if owns_support {
                    members
                        .entry(format!(
                            "step_level/{}",
                            super::key(level.z - analysis.envelope[0][2])
                        ))
                        .or_default()
                        .insert(feature.id.clone());
                }
            }
        }
        if let Some(crate::pmi::Reading::Read(report)) = &analysis.pmi {
            let ownership =
                crate::planning::memo(analysis, "pmi-witness-members", Vec::new(), || {
                    let mut witnessed: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
                    let ids = report.identities();
                    for r in report
                        .records
                        .iter()
                        .filter(|r| r.category == crate::pmi::Category::Dimension)
                    {
                        let Some(g) = r
                            .geometry
                            .as_ref()
                            .filter(|g| g.reasons.is_empty() && g.stations.len() == 2)
                        else {
                            continue;
                        };
                        let Ok(id) = ids.id(&r.source_id) else {
                            continue;
                        };
                        for (_, hole) in holes::read(analysis) {
                            let Ok(h) = hole else { continue };
                            let Some(along) = h.direction.iter().position(|v| v.abs() > 1. - 1e-6)
                            else {
                                continue;
                            };
                            if r.faces.iter().any(|f| h.feature.faces.contains(f))
                                && g.stations.iter().any(|p| {
                                    (0..3).all(|k| k == along || (p[k] - h.at[k]).abs() < 1e-6)
                                })
                            {
                                // Exclude only the actual witnessed source member, not
                                // every bore in a composite group. An axis extension may
                                // leave its own bore; other group members remain obstacles.
                                witnessed
                                    .entry(id.into())
                                    .or_default()
                                    .insert(h.feature.id.clone());
                            }
                        }
                    }
                    witnessed
                });
            members.extend(ownership);
        }
        Self {
            slot_layout: super::placement::automatic_slot_layout(analysis),
            features,
            solids,
            view: format!("v:{view}"),
            members,
            counts: RefCell::default(),
            index: OnceCell::new(),
            extensions: RefCell::new(HashMap::new()),
            extension_counts: RefCell::default(),
            extension_count_values: RefCell::default(),
        }
    }

    pub(super) fn automatic_slot_layout(&self) -> bool {
        self.slot_layout
    }

    pub(super) fn crossed(&self, shaft: [P2; 2], _owners: &[Measurement]) -> BTreeSet<String> {
        self.crossed_ids(shaft)
            .into_iter()
            .map(str::to_owned)
            .collect()
    }

    /// Whether *shaft* crosses a feature's footprint, the view's material aside: an interior
    /// leader runs over material by design, and its ink proof keeps it off every edge.
    pub(super) fn crosses_feature(&self, shaft: [P2; 2]) -> bool {
        let len = (shaft[1][0] - shaft[0][0]).hypot(shaft[1][1] - shaft[0][1]);
        self.near(shaft)
            .any(|f| f.shape.crossed_with_length(shaft, len))
    }

    pub(super) fn crossing_count(&self, shaft: [P2; 2]) -> usize {
        let key = [shaft[0][0], shaft[0][1], shaft[1][0], shaft[1][1]].map(f64::to_bits);
        if let Some(count) = self.counts.borrow().get(&key) {
            return *count;
        }
        let count = self.crossed_ids(shaft).len();
        self.counts.borrow_mut().insert(key, count);
        count
    }

    fn near(&self, shaft: [P2; 2]) -> impl Iterator<Item = &Footprint> {
        self.near_indices(shaft).map(|i| &self.features[i])
    }

    fn near_indices(&self, shaft: [P2; 2]) -> impl Iterator<Item = usize> {
        let index = self.index.get_or_init(|| {
            let mut by_x: Vec<_> = self
                .features
                .iter()
                .enumerate()
                .map(|(i, f)| (i, f.shape.bounds()))
                .collect();
            let bounded = by_x
                .iter()
                .all(|(_, b)| b.iter().all(|v| v.is_finite()) && b[0] <= b[2] && b[1] <= b[3]);
            by_x.sort_by(|a, b| a.1[0].total_cmp(&b.1[0]).then(a.0.cmp(&b.0)));
            Index { by_x, bounded }
        });
        let b = bounds(shaft.into_iter());
        let bounded = index.bounded && shaft.iter().flatten().all(|v| v.is_finite());
        index
            .by_x
            .iter()
            .take_while(move |(_, f)| !bounded || f[0] <= b[2])
            .filter(move |(_, f)| !bounded || (f[2] >= b[0] && f[1] <= b[3] && f[3] >= b[1]))
            .map(|(i, _)| *i)
    }

    fn crossed_ids(&self, shaft: [P2; 2]) -> BTreeSet<&str> {
        let len = (shaft[1][0] - shaft[0][0]).hypot(shaft[1][1] - shaft[0][1]);
        let mut result: BTreeSet<&str> = self
            .near(shaft)
            .filter(|f| f.shape.crossed_with_length(shaft, len))
            .map(|f| f.id.as_str())
            .collect();
        if self
            .solids
            .iter()
            .any(|b| clip(shaft, *b).is_some_and(|(a, c)| a * len > HAIR && (c - a) * len > HAIR))
        {
            result.insert(self.view.as_str());
        }
        result
    }

    fn owned(&self, id: &str, owners: &[Measurement]) -> bool {
        owners.iter().any(|m| {
            m.feature == id
                || (m.feature == "step_level"
                    && m.parameter.starts_with("step_height.length.h")
                    && self
                        .members
                        .get(&format!("step_level/{}", super::key(m.value)))
                        .is_some_and(|members| members.contains(id)))
                || self
                    .members
                    .get(&m.feature)
                    .is_some_and(|members| members.contains(id))
        })
    }

    /// An interior witness leaves its own opening at the near rim, keeping the normal gap.
    pub(super) fn interior_witness(&self, line: &mut [P2; 2], owners: &[Measurement]) -> bool {
        let slot_width =
            self.slot_layout && owners.iter().any(|m| m.parameter == "slot_width.length");
        clip_witness(
            line,
            self.features
                .iter()
                .filter(|f| self.owned(&f.id, owners))
                .map(|f| &f.shape),
            slot_width,
            |s| !self.crossed_extension(s, owners).is_empty(),
        )
    }

    pub(super) fn witness_clipper(&self, owners: &[Measurement]) -> WitnessClipper<'_> {
        WitnessClipper {
            shapes: self
                .features
                .iter()
                .filter(|f| self.owned(&f.id, owners))
                .map(|f| &f.shape)
                .collect(),
            slot_width: self.slot_layout
                && owners.iter().any(|m| m.parameter == "slot_width.length"),
            extensions: self.extension_counter(owners),
        }
    }

    /// Bind ownership once for the many exact witness-count queries in a linear solve.
    pub(super) fn extension_counter(&self, owners: &[Measurement]) -> ExtensionCounter<'_> {
        let key: OwnersKey = owners
            .iter()
            .map(|m| (m.feature.clone(), m.parameter.clone(), m.value.to_bits()))
            .collect();
        let cached = self.extension_counts.borrow().get(&key).cloned();
        let prepared = cached.unwrap_or_else(|| {
            let mut ids = BTreeMap::new();
            let features = self
                .features
                .iter()
                .map(|f| {
                    if f.id == self.view || self.owned(&f.id, owners) {
                        return None;
                    }
                    let next = ids.len();
                    Some(*ids.entry(&f.id).or_insert(next))
                })
                .collect();
            let id = self.extension_counts.borrow().len();
            let prepared = Rc::new(ExtensionCounts { features, id });
            self.extension_counts
                .borrow_mut()
                .insert(key, prepared.clone());
            prepared
        });
        ExtensionCounter {
            footprints: self,
            prepared,
        }
    }

    /// A dimension witnesses its own feature (all members for a pattern). Unlike a leader,
    /// its extension does not select one member to label, so only other features are obstacles.
    pub(super) fn crossed_extension(
        &self,
        shaft: [P2; 2],
        owners: &[Measurement],
    ) -> BTreeSet<String> {
        // Witnesses recur across solve orders and furniture retries in this exact
        // projected frame. Ownership is part of the key; no other feature's
        // crossing can be forgiven by a previously measured owner's result.
        let key = (
            shaft
                .into_iter()
                .flatten()
                .map(f64::to_bits)
                .collect::<Vec<_>>()
                .try_into()
                // Invariant: The witness data is an array of exactly two endpoints.
                .expect("two witness endpoints"),
            owners
                .iter()
                .map(|m| (m.feature.clone(), m.parameter.clone(), m.value.to_bits()))
                .collect(),
        );
        if let Some(hits) = self.extensions.borrow().get(&key) {
            return hits.clone();
        }
        let len = (shaft[1][0] - shaft[0][0]).hypot(shaft[1][1] - shaft[0][1]);
        let hits: BTreeSet<_> = self
            .near(shaft)
            .filter(|f| {
                f.id != self.view
                    && !self.owned(&f.id, owners)
                    && f.shape.crossed_with_length(shaft, len)
            })
            .map(|f| f.id.clone())
            .collect();
        let mut saved = self.extensions.borrow_mut();
        if saved.len() >= 8192 {
            saved.clear();
        }
        saved.insert(key, hits.clone());
        hits
    }
    /// The view's material: each solid's box on the page ([`crate::material::solids`]).
    pub(super) fn solids(&self) -> &[[f64; 4]] {
        &self.solids
    }

    /// Travel to an exit is a length tie-break, never a crossing or a containment warning.
    pub(super) fn inside_length(&self, shaft: [P2; 2]) -> f64 {
        let len = (shaft[1][0] - shaft[0][0]).hypot(shaft[1][1] - shaft[0][1]);
        self.solids
            .iter()
            .filter_map(|b| clip(shaft, *b))
            .map(|(a, b)| (b - a) * len)
            .sum()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn analysis() -> Analysis {
        serde_json::from_value(serde_json::json!({
            "schema":crate::model::ANALYSIS_SCHEMA,
            "binding":{"source_sha256":"test","part":"test","face_count":1,"loader":"test"},
            "envelope":[[-10.0,-10.0,-10.0],[10.0,10.0,10.0]],"iso_bounds":[0.0,0.0,0.0,0.0],
            "features":[{"id":"f:hole:1","kind":"hole","faces":[0],
                "parameters":{"axis":[0.0,0.0,1.0],"location":[2.0,0.0,3.0],"diameter":4.0,"depth":6.0,"bottom":"flat"},
                "fingerprint":{"version":"test","family":"holes","subtype":"HoleRecord","sizes":{},"axis":[0.0,0.0,1.0],
                    "position":[2.0,0.0,0.0],"neighbourhood":[]}}]
        })).unwrap()
    }

    #[test]
    fn a_recess_wall_height_owns_only_its_recorded_support_face() {
        let corpus = std::env::var_os("DRAFTWRIGHT_CORPUS")
            .map(std::path::PathBuf::from)
            .or_else(|| {
                std::env::var_os("HOME")
                    .map(|p| std::path::PathBuf::from(p).join("repos/quiddity/tests/corpus"))
            })
            .filter(|p| p.is_dir());
        let Some(corpus) = corpus else {
            assert!(
                std::env::var_os("DRAFTWRIGHT_CORPUS_REQUIRED").is_none_or(|v| v != "1"),
                "required corpus missing"
            );
            return;
        };
        let (_, a) =
            crate::model::analyse_native(&corpus.join("nist/nist_ftc_09_asme1_rd.stp")).unwrap();
        let level = a
            .level_supports
            .iter()
            .find(|s| (s.z - 109.08707180743679).abs() < 1e-6)
            .unwrap();
        let frame = Frame::looking(&a, [0., -1., 0.], [0., 0., 1.], [200., 200.], 1.);
        let f = Footprints::new(&a, &frame, "front");
        let shaft = [
            frame.page([level.x_span[1] + 1., level.y_span[0], level.z]),
            frame.page([a.envelope[1][0] + 10., level.y_span[0], level.z]),
        ];
        let owner = Measurement {
            feature: "step_level".into(),
            parameter: "step_height.length.h248_787".into(),
            value: level.z - a.envelope[0][2],
        };
        assert!(
            f.crossed_extension(shaft, &[])
                .contains("f:section_recess:5")
        );
        let owned = f.crossed_extension(shaft, std::slice::from_ref(&owner));
        assert!(!owned.contains("f:section_recess:5"));
        assert!(
            owned.contains("f:hole:10"),
            "an unrelated crossed hole remains reported"
        );
        let mut other = owner;
        other.value += 1.;
        assert!(
            f.crossed_extension(shaft, &[other])
                .contains("f:section_recess:5")
        );
    }

    #[test]
    fn cylinders_project_as_end_on_circles_and_side_on_depth_bands() {
        let a = analysis();
        let plan = Frame::looking(&a, [0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [50.0, 50.0], 2.0);
        let front = Frame::looking(&a, [0.0, -1.0, 0.0], [0.0, 0.0, 1.0], [50.0, 50.0], 2.0);
        let p = Footprints::new(&a, &plan, "plan");
        let f = Footprints::new(&a, &front, "front");
        assert!(matches!(p.features[0].shape, Shape::Circle(_, 4.0)));
        assert!(matches!(f.features[0].shape, Shape::Box(_)));
        let shaft = [front.page([-2.0, 0.0, 2.0]), front.page([6.0, 0.0, 2.0])];
        assert!(f.crossed(shaft, &[]).contains("f:hole:1"));
        let outside = [front.page([-2.0, 0.0, 4.0]), front.page([6.0, 0.0, 4.0])];
        assert!(!f.crossed(outside, &[]).contains("f:hole:1"));
    }

    #[test]
    fn each_feature_counts_once_and_a_target_exemption_stays_near_the_tip() {
        let footprint = |id: &str, c| Footprint {
            id: id.into(),
            shape: Shape::Circle(c, 1.0),
        };
        let f = Footprints {
            slot_layout: false,
            view: "v:plan".into(),
            solids: vec![],
            members: BTreeMap::new(),
            counts: RefCell::default(),
            index: OnceCell::new(),
            extensions: RefCell::new(HashMap::new()),
            extension_counts: RefCell::default(),
            extension_count_values: RefCell::default(),
            features: vec![
                footprint("f:own", [0.0, 0.0]),
                footprint("f:other", [5.0, 0.0]),
                footprint("f:other", [5.0, 0.0]),
                footprint("f:own", [8.0, 0.0]),
            ],
        };
        let owner = [Measurement {
            feature: "f:own".into(),
            parameter: "diameter".into(),
            value: 2.0,
        }];
        assert_eq!(
            f.crossed([[0.0, 0.0], [10.0, 0.0]], &owner),
            BTreeSet::from(["f:own".into(), "f:other".into()])
        );
        assert!(f.crossed([[0.0, 0.0], [-3.0, 0.0]], &owner).is_empty());
        let witness = [[-2.0, 0.0], [10.0, 0.0]];
        for _ in 0..2 {
            assert_eq!(
                f.crossed_extension(witness, &owner),
                BTreeSet::from(["f:other".into()])
            );
            assert_eq!(
                f.crossed_extension(witness, &[]),
                BTreeSet::from(["f:own".into(), "f:other".into()])
            );
            assert!(
                f.crossed_extension([[-2.0, 3.0], [10.0, 3.0]], &owner)
                    .is_empty()
            );
        }
    }

    #[test]
    fn a_pattern_exempts_its_attached_member_but_not_a_later_member_crossing() {
        let f = Footprints {
            slot_layout: false,
            view: "v:plan".into(),
            solids: vec![],
            counts: RefCell::default(),
            index: OnceCell::new(),
            extensions: RefCell::new(HashMap::new()),
            extension_counts: RefCell::default(),
            extension_count_values: RefCell::default(),
            members: BTreeMap::from([(
                "f:pattern".into(),
                BTreeSet::from(["f:a".into(), "f:b".into()]),
            )]),
            features: vec![
                Footprint {
                    id: "f:a".into(),
                    shape: Shape::Circle([0.0, 0.0], 1.0),
                },
                Footprint {
                    id: "f:b".into(),
                    shape: Shape::Circle([5.0, 0.0], 1.0),
                },
            ],
        };
        let owner = [Measurement {
            feature: "f:pattern".into(),
            parameter: "pitch".into(),
            value: 5.0,
        }];
        assert!(f.crossed([[0.0, 0.0], [-3.0, 0.0]], &owner).is_empty());
        let witness = [[-2.0, 0.0], [10.0, 0.0]];
        for _ in 0..2 {
            assert_eq!(f.crossed_extension(witness, &owner), BTreeSet::new());
            assert_eq!(
                f.crossed_extension(witness, &[]),
                BTreeSet::from(["f:a".into(), "f:b".into()])
            );
            assert!(
                f.crossed_extension([[-2.0, 3.0], [10.0, 3.0]], &owner)
                    .is_empty()
            );
        }

        assert_eq!(
            f.crossed([[0.0, 0.0], [8.0, 0.0]], &owner),
            BTreeSet::from(["f:b".into()])
        );
        assert!(
            f.crossed_extension([[0.0, 0.0], [8.0, 0.0]], &owner)
                .is_empty()
        );
    }

    #[test]
    fn collision_reports_shafts_and_both_extensions_with_distinct_ids() {
        use super::super::{Placed, PlannedDimension};
        use crate::model::{PlannedSheet, PlannedView};
        let a = analysis();
        let view:PlannedView=serde_json::from_value(serde_json::json!({
            "id":"v:plan","direction":[0.0,0.0,1.0],"up":[0.0,1.0,0.0],"at":[50.0,50.0],
            "scale":1.0,"area":[20.0,20.0,100.0,100.0],"default":true,"basis":"","state":{"kind":"shown"}})).unwrap();
        let sheet: PlannedSheet = serde_json::from_value(serde_json::json!({
            "size":"A4","page":[297.0,210.0],"scale":"1:1","scale_factor":1.0,
            "arrangement":"columns","title_block":[200.0,10.0,280.0,30.0],"default":true,"basis":"",
            "state":{"kind":"shown"},"title":{}}))
        .unwrap();
        let frame = Frame::new(&a, &view);
        let line = [frame.page([-2.0, 0.0, 3.0]), frame.page([6.0, 0.0, 3.0])];
        let dim = |id: &str, kind: &str, lines| PlannedDimension {
            reference: false,
            determined_by: Vec::new(),
            region: None,
            id: id.into(),
            kind: kind.into(),
            view: "v:plan".into(),
            value: 1.0,
            label: id.into(),
            terms: vec![id.into()],
            measurements: vec![],
            default: true,
            from_spec: vec![],
            basis: String::new(),
            state: crate::items::State::Shown,
            placed: Some(Placed {
                lines,
                arrows: vec![],
                label_at: [90.0, 90.0],
                rotation: 0.0,
                label_box: [90.0, 90.0, 95.0, 93.0],
            }),
        };
        let dims = [
            dim("d:callout", "callout", vec![line, line]),
            dim("d:linear", "linear", vec![line, line, line]),
        ];
        let problems =
            super::super::placement::collisions(&a, &dims, &sheet, std::slice::from_ref(&view));
        let features: Vec<_> = problems
            .iter()
            .filter(|p| p.code.ends_with("through_feature"))
            .collect();
        assert_eq!(features.len(), 3, "{features:?}");
        assert_eq!(
            features
                .iter()
                .filter(|p| p.code == "leader_through_feature")
                .count(),
            1
        );
        assert_eq!(
            features
                .iter()
                .map(|p| &p.id)
                .collect::<BTreeSet<_>>()
                .len(),
            3
        );
        assert!(
            features
                .iter()
                .all(|p| p.severity == "warning" && p.subjects[1] == "f:hole:1")
        );
        assert!(
            features
                .iter()
                .filter(|p| p.code == "extension_through_feature")
                .all(|p| p.fix.is_empty())
        );

        let stub = [frame.page([-2., 4., 3.]), line[0]];
        let bent = dim("pmi:datum.test", "callout", vec![stub, line]);
        let found =
            super::super::placement::collisions(&a, &[bent], &sheet, std::slice::from_ref(&view));
        assert_eq!(
            found
                .iter()
                .filter(|p| p.code == "leader_through_feature")
                .count(),
            1,
            "the second datum shaft leg crosses the hole"
        );
        let separate = [frame.page([-2., 4., 3.]), frame.page([-2., 2., 3.])];
        let extended = dim("pmi:datum.test", "callout", vec![separate, line]);
        let found = super::super::placement::collisions(&a, &[extended], &sheet, &[view]);
        assert!(
            !found.iter().any(|p| p.code == "leader_through_feature"),
            "a disconnected face extension is not the datum shaft"
        );
    }

    #[test]
    fn plan_cache_reuses_a_projection_but_rebuilds_after_view_repacking() {
        let mut a = analysis();
        a.edge_ink = Some(
            serde_json::from_value(serde_json::json!({
                "edges": [[[0., 0., 0.], [10., 0., 0.]]], "planes": [], "curved": []
            }))
            .unwrap(),
        );
        let mut view: crate::model::PlannedView = serde_json::from_value(serde_json::json!({
            "id":"v:plan","direction":[0.0,0.0,1.0],"up":[0.0,1.0,0.0],"at":[50.0,50.0],
            "scale":1.0,"area":[20.0,20.0,100.0,100.0],"default":true,"basis":"","state":{"kind":"shown"}})).unwrap();
        let cache = Cache::default();
        let first_model = cache.model(&a, &view).unwrap();
        assert!(Rc::ptr_eq(&first_model, &cache.model(&a, &view).unwrap()));
        let first = cache.get(&a, &view);
        assert!(Rc::ptr_eq(&first, &cache.get(&a, &view)));
        view.at[0] += 10.0;
        let moved = cache.get(&a, &view);
        let moved_model = cache.model(&a, &view).unwrap();
        assert!(!Rc::ptr_eq(&first_model, &moved_model));
        view.scale *= 2.;
        assert!(!Rc::ptr_eq(&moved_model, &cache.model(&a, &view).unwrap()));
        assert!(!Rc::ptr_eq(&first, &moved));
        assert_eq!(first.features.len(), moved.features.len());
        for (a, b) in first.features.iter().zip(&moved.features) {
            assert!((b.shape.bounds()[0] - a.shape.bounds()[0] - 10.0).abs() < 1e-9);
        }
        // Enlarging a different crop can keep the same camera, scale and page
        // centre while changing the world centre mapped there. Cached ink must move.
        view.id = "v:detail_a".into();
        view.detail = Some(
            serde_json::from_value(serde_json::json!({
                "label":"A","axis":2,"source_view":"v:plan","kind":"authored-feature",
                "bounds":[[0.,0.,0.],[10.,10.,10.]],"marked_bounds":[[0.,0.,0.],[10.,10.,10.]],
                "dimensions":[],"scale":2.,"authored":true,"partial_profile":true
            }))
            .unwrap(),
        );
        let crop = cache.get(&a, &view);
        let crop_model = cache.model(&a, &view).unwrap();
        view.detail.as_mut().unwrap().bounds[0][0] += 2.0;
        let changed = cache.get(&a, &view);
        let changed_model = cache.model(&a, &view).unwrap();
        assert!(!Rc::ptr_eq(&crop_model, &changed_model));
        assert!(!Rc::ptr_eq(&crop, &changed));
        assert!(!crop.features.is_empty());
        for (before, after) in crop.features.iter().zip(&changed.features) {
            assert!((after.shape.bounds()[0] - before.shape.bounds()[0] + view.scale).abs() < 1e-9);
        }
    }

    #[test]
    fn ownership_count_cache_matches_uncached_crossings_and_deduplicates_features() {
        let f = Footprints {
            slot_layout: false,
            features: vec![
                Footprint {
                    id: "f:hole".into(),
                    shape: Shape::Circle([0., 0.], 2.),
                },
                Footprint {
                    id: "f:hole".into(),
                    shape: Shape::Box([-1., -1., 1., 1.]),
                },
                Footprint {
                    id: "f:slot".into(),
                    shape: Shape::Box([3., -2., 5., 2.]),
                },
                Footprint {
                    id: "v:plan".into(),
                    shape: Shape::Box([-8., -8., 8., 8.]),
                },
            ],
            solids: vec![],
            view: "v:plan".into(),
            members: BTreeMap::from([
                (
                    "f:pattern".into(),
                    BTreeSet::from(["f:hole".into(), "f:slot".into()]),
                ),
                ("step_level/4".into(), BTreeSet::from(["f:slot".into()])),
            ]),
            counts: RefCell::default(),
            index: OnceCell::new(),
            extensions: RefCell::default(),
            extension_counts: RefCell::default(),
            extension_count_values: RefCell::default(),
        };
        for (feature, parameter, value) in [
            ("", "length", 0.),
            ("f:hole", "length", 4.),
            ("f:pattern", "length", 4.),
            ("step_level", "step_height.length.h4", 4.),
            ("step_level", "step_height.length.h4", 5.),
        ] {
            let owners = [Measurement {
                feature: feature.into(),
                parameter: parameter.into(),
                value,
            }];
            let counter = f.extension_counter(&owners);
            let repeated = f.extension_counter(&owners);
            assert!(Rc::ptr_eq(&counter.prepared, &repeated.prepared));
            for x in -8..=8 {
                for y in -8..=8 {
                    for shaft in [
                        [[-9., f64::from(y)], [f64::from(x), 0.]],
                        [[f64::from(x), -9.], [f64::from(x), f64::from(y)]],
                    ] {
                        let length = (shaft[1][0] - shaft[0][0]).hypot(shaft[1][1] - shaft[0][1]);
                        let expected = f
                            .features
                            .iter()
                            .filter(|p| {
                                p.id != f.view
                                    && !f.owned(&p.id, &owners)
                                    && p.shape.crossed_with_length(shaft, length)
                            })
                            .map(|p| &p.id)
                            .collect::<BTreeSet<_>>()
                            .len();
                        assert_eq!(counter.count(shaft), expected);
                        assert_eq!(repeated.count(shaft), expected);
                        assert_eq!(f.crossed_extension(shaft, &owners).len(), expected);
                    }
                }
            }
        }
    }

    #[test]
    fn containing_features_allow_an_exit_but_still_report_reentry() {
        let vertices = [
            [0.0, 0.0],
            [6.0, 0.0],
            [6.0, 4.0],
            [4.0, 4.0],
            [4.0, 1.0],
            [2.0, 1.0],
            [2.0, 4.0],
            [0.0, 4.0],
        ];
        let f = Footprints {
            slot_layout: false,
            features: vec![Footprint {
                id: "f:cavity".into(),
                shape: Shape::polygon(
                    (0..vertices.len())
                        .map(|i| [vertices[i], vertices[(i + 1) % vertices.len()]])
                        .collect(),
                ),
            }],
            solids: vec![],
            view: "v:plan".into(),
            members: BTreeMap::new(),
            counts: RefCell::default(),
            index: OnceCell::new(),
            extensions: RefCell::new(HashMap::new()),
            extension_counts: RefCell::default(),
            extension_count_values: RefCell::default(),
        };
        let exit = [[1.0, 2.0], [-1.0, 2.0]];
        let reentry = [[1.0, 2.0], [7.0, 2.0]];
        assert!(f.crossed(exit, &[]).is_empty());
        assert!(f.crossed_extension(exit, &[]).is_empty());
        assert_eq!(f.crossed(reentry, &[]), BTreeSet::from(["f:cavity".into()]));
        assert_eq!(
            f.crossed_extension(reentry, &[]),
            BTreeSet::from(["f:cavity".into()])
        );
    }

    #[test]
    fn circles_reject_tangency_and_allow_any_initial_exit() {
        let f = Shape::Circle([0.0, 0.0], 2.0);
        assert!(f.crossed([[-3.0, 0.0], [3.0, 0.0]]));
        assert!(f.crossed([[-3.0, 0.0], [3.0, 0.0]]));
        assert!(!f.crossed([[-3.0, 2.0], [3.0, 2.0]]));
        assert!(!f.crossed([[2.0, 0.0], [4.0, 0.0]]));
        assert!(!f.crossed([[0.0, 0.0], [4.0, 0.0]]));
        assert!(!f.crossed([[0.0, 0.0], [4.0, 0.0]]));
    }

    #[test]
    fn boxes_count_side_on_bands_but_not_their_edges_or_short_touches() {
        let f = Shape::Box([-1.0, -2.0, 1.0, 2.0]);
        assert!(f.crossed([[-4.0, 0.0], [4.0, 0.0]]));
        assert!(!f.crossed([[-4.0, 2.0], [4.0, 2.0]]));
        assert!(!f.crossed([[0.99, 0.0], [4.0, 0.0]]));
        assert!(!f.crossed([[0.0, 0.0], [0.0, 0.0]]));
    }

    #[test]
    fn a_concave_loop_reports_reentry_after_the_exempt_tip() {
        let vertices = [
            [0.0, 0.0],
            [6.0, 0.0],
            [6.0, 4.0],
            [4.0, 4.0],
            [4.0, 1.0],
            [2.0, 1.0],
            [2.0, 4.0],
            [0.0, 4.0],
        ];
        let edges = (0..vertices.len())
            .map(|i| [vertices[i], vertices[(i + 1) % vertices.len()]])
            .collect();
        let f = Shape::polygon(edges);
        assert!(f.crossed([[1.0, 2.0], [7.0, 2.0]]));
        assert!(!f.crossed([[1.0, 2.0], [-1.0, 2.0]]));
    }
    #[test]
    fn interior_witness_leaves_its_own_rim_without_skipping_an_unrelated_feature() {
        let f = Footprints {
            slot_layout: false,
            view: "v:plan".into(),
            solids: vec![],
            members: BTreeMap::new(),
            counts: RefCell::default(),
            index: OnceCell::new(),
            extensions: RefCell::new(HashMap::new()),
            extension_counts: RefCell::default(),
            extension_count_values: RefCell::default(),
            features: vec![
                Footprint {
                    id: "f:own".into(),
                    shape: Shape::Circle([0., 0.], 3.),
                },
                Footprint {
                    id: "f:other".into(),
                    shape: Shape::Circle([7., 0.], 1.),
                },
            ],
        };
        let owners = [Measurement {
            feature: "f:own".into(),
            parameter: "pitch.length".into(),
            value: 10.,
        }];
        let mut line = [[1., 0.], [10., 0.]];
        assert!(f.interior_witness(&mut line, &owners));
        assert_eq!(line[0], [4., 0.]);
        assert_eq!(
            f.crossed_extension(line, &owners),
            BTreeSet::from(["f:other".into()])
        );
        let mut unowned = [[1., 0.], [10., 0.]];
        assert!(f.interior_witness(&mut unowned, &[]));
        assert_eq!(unowned[0], [1., 0.]);
        let mut short = [[1., 0.], [2., 0.]];
        assert!(!f.interior_witness(&mut short, &owners));
    }
    #[test]
    fn an_owned_box_boundary_is_trimmed_without_jumping_a_foreign_opening() {
        let footprints = |foreign_y| Footprints {
            slot_layout: true,
            view: "v:plan".into(),
            solids: vec![],
            members: BTreeMap::new(),
            counts: RefCell::default(),
            index: OnceCell::new(),
            extensions: RefCell::default(),
            extension_counts: RefCell::default(),
            extension_count_values: RefCell::default(),
            features: vec![
                Footprint {
                    id: "f:own".into(),
                    shape: Shape::Box([0., 0., 4., 10.]),
                },
                Footprint {
                    id: "f:other".into(),
                    shape: Shape::Circle([4., foreign_y], 1.),
                },
            ],
        };
        let owners = [Measurement {
            feature: "f:own".into(),
            parameter: "slot_width.length".into(),
            value: 4.,
        }];
        let f = footprints(15.);
        let mut line = [[4., 1.], [4., 20.]];
        assert!(f.interior_witness(&mut line, &owners));
        assert_eq!(line[0], [4., 11.]);
        assert_eq!(
            f.crossed_extension(line, &owners),
            BTreeSet::from(["f:other".into()])
        );
        let f = footprints(7.);
        let original = [[4., 1.], [4., 20.]];
        let mut line = original;
        assert!(!f.interior_witness(&mut line, &owners));
        assert_eq!(line, original);
        for f in [footprints(15.), footprints(7.)] {
            let clipper = f.witness_clipper(&owners);
            for start in [[4., 1.], [4., 9.], [4., 14.]] {
                let mut direct = [start, [4., 20.]];
                let mut prepared = direct;
                assert_eq!(
                    f.interior_witness(&mut direct, &owners),
                    clipper.clip(&mut prepared)
                );
                assert_eq!(direct, prepared);
            }
        }
    }
}
