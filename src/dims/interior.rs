//! Python's bounded interior rays and corridor retry, against proven blank face and settled ink.
use super::placement::{Frame, Page, Side, arrow, boxes_overlap, crosses, label_box};
use super::{CAP, P2, Placed, PlannedDimension, Shape, Want, font, label_ink, lane_pitch};
use crate::model::{Analysis, PlannedView, Spec};
use std::collections::{BTreeMap, BTreeSet};

pub(crate) const BASIS: &str = "; proven blank interior of its carrying view";

fn padded(b: [f64; 4], pad: f64) -> [f64; 4] {
    [b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad]
}
fn contains(b: [f64; 4], p: P2) -> bool {
    p[0] >= b[0] && p[0] <= b[2] && p[1] >= b[1] && p[1] <= b[3]
}

/// Whether a segment comes within *c* of a box (the box grown by *c*).
fn near_box(s: [P2; 2], b: [f64; 4], c: f64) -> bool {
    let b = padded(b, c);
    contains(b, s[0]) || contains(b, s[1]) || crosses(s, b)
}

fn distance(p: P2, [a, b]: [P2; 2]) -> f64 {
    let d = [b[0] - a[0], b[1] - a[1]];
    let n = d[0] * d[0] + d[1] * d[1];
    let t = if n == 0.0 {
        0.0
    } else {
        (((p[0] - a[0]) * d[0] + (p[1] - a[1]) * d[1]) / n).clamp(0.0, 1.0)
    };
    (p[0] - a[0] - t * d[0]).hypot(p[1] - a[1] - t * d[1])
}

/// The least distance between two segments.
fn segment_distance(s: [P2; 2], t: [P2; 2]) -> f64 {
    let side = |a: P2, b: P2, c: P2| (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
    let (p, q) = (side(s[0], s[1], t[0]), side(s[0], s[1], t[1]));
    let (r, u) = (side(t[0], t[1], s[0]), side(t[0], t[1], s[1]));
    if p * q < 0.0 && r * u < 0.0 {
        return 0.0;
    }
    distance(s[0], t)
        .min(distance(s[1], t))
        .min(distance(t[0], s))
        .min(distance(t[1], s))
}

fn bounds_of(points: impl IntoIterator<Item = P2>) -> [f64; 4] {
    points.into_iter().fold(
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

/// Immutable tip preparation reused by leader directions/reaches. Clones
/// start fresh so construction-time changes cannot inherit old verdicts.
#[derive(Default)]
struct ProofMemo {
    visibility: std::cell::RefCell<BTreeMap<[u64; 4], bool>>,
    bands: std::cell::RefCell<BTreeMap<[u64; 3], Option<AttachmentBand>>>,
    fingerprint: std::cell::OnceCell<[u8; 32]>,
}
impl ProofMemo {
    fn query<const N: usize>(
        memo: &std::cell::RefCell<BTreeMap<[u64; N], bool>>,
        key: [u64; N],
        run: impl FnOnce() -> bool,
    ) -> bool {
        if let Some(&found) = memo.borrow().get(&key) {
            return found;
        }
        let found = run();
        let mut memo = memo.borrow_mut();
        if memo.len() >= 4096 {
            memo.clear();
        }
        memo.insert(key, found);
        found
    }
}
impl Clone for ProofMemo {
    fn clone(&self) -> Self {
        Self::default()
    }
}
#[derive(Clone)]
struct AttachmentBand {
    direction: P2,
    chords: std::rc::Rc<Vec<usize>>,
}

/// A view's projected edge evidence, prepared once per view and planning pass, in model mm at
/// 1:1 about the view's centre: every edge's chords, curved faces' silhouettes (or their boxes
/// where a silhouette is not bounded exactly), and the planar faces square to the view.
#[derive(Clone, serde::Serialize)]
pub(super) struct Proof {
    centre: P2,
    /// Every chord, with the edge each belongs to at `edges[e]..edges[e + 1]`.
    segments: Vec<[P2; 2]>,
    edges: Vec<usize>,
    /// Toward-viewer coordinates of the actual edge chords' endpoints.
    depths: Vec<[f64; 2]>,
    require_visible_attachment: bool,
    #[serde(skip)]
    memo: ProofMemo,
    /// Boxes a curved face's silhouette may lie anywhere within.
    boxes: Vec<[f64; 4]>,
    /// The covers of cylinders seen end-on: holes and bosses, drawn as circles.
    holes: Vec<[f64; 4]>,
    /// Planar faces square to the view: their box and their chords.
    faces: Vec<Face>,
    /// All planar projections and full curved-face covers, for proving exterior voids.
    exterior_faces: Vec<([f64; 4], Vec<usize>)>,
    curved_cover: Vec<[f64; 4]>,
    grid: Grid,
    /// Whether [`Proof::blank`] also accepts openings the part surrounds or flanks
    /// ([`callout_proofs`]).
    voids: bool,
}

#[derive(Clone, serde::Serialize)]
struct Face {
    bounds: [f64; 4],
    chords: Vec<usize>,
    depth: f64,
}

/// A uniform bucket grid over a view's chords: queries visit only nearby chords.
#[derive(Clone, serde::Serialize)]
struct Grid {
    origin: P2,
    cell: f64,
    size: [usize; 2],
    cells: Vec<Vec<u32>>,
}

impl Grid {
    fn new(segments: &[[P2; 2]]) -> Self {
        let b = bounds_of(segments.iter().flatten().copied());
        let (origin, extent) = if b[0].is_finite() {
            ([b[0], b[1]], (b[2] - b[0]).max(b[3] - b[1]))
        } else {
            ([0.0, 0.0], 1.0)
        };
        let cell = (extent / 128.0).max(1e-3);
        let size = [
            ((b[2] - b[0]).max(0.0) / cell) as usize + 1,
            ((b[3] - b[1]).max(0.0) / cell) as usize + 1,
        ];
        let mut grid = Self {
            origin,
            cell,
            size,
            cells: vec![Vec::new(); size[0] * size[1]],
        };
        for (i, s) in segments.iter().enumerate() {
            let [x0, y0, x1, y1] = grid.range(bounds_of(*s));
            for y in y0..=y1 {
                for x in x0..=x1 {
                    grid.cells[y * size[0] + x].push(i as u32);
                }
            }
        }
        grid
    }

    fn range(&self, b: [f64; 4]) -> [usize; 4] {
        let at = |v: f64, k: usize| {
            (((v - self.origin[k]) / self.cell).floor().max(0.0) as usize).min(self.size[k] - 1)
        };
        [at(b[0], 0), at(b[1], 1), at(b[2], 0), at(b[3], 1)]
    }

    /// Indices of the chords that may meet box *b* (some more than once).
    fn near(&self, b: [f64; 4]) -> impl Iterator<Item = usize> + '_ {
        let [x0, y0, x1, y1] = self.range(b);
        (y0..=y1).flat_map(move |y| {
            (x0..=x1)
                .flat_map(move |x| self.cells[y * self.size[0] + x].iter().map(|&i| i as usize))
        })
    }
}

/// How far stored evidence may stray from the exact geometry, model mm.
const SLACK: f64 = crate::model::projected_ink::TOLERANCE + crate::model::projected_ink::GRID;

pub(super) fn proofs(
    analysis: &Analysis,
    views: &[PlannedView],
) -> BTreeMap<String, std::rc::Rc<Proof>> {
    prepared_proofs(analysis, views, false)
}

/// [`proofs`] for feature callout routes, which may also put a label in an opening the part
/// surrounds or flanks: a slot's opening, the gap between lugs (maintainer 2026-10-25). Paper and
/// scale trials and other interior placement keep face-only proofs.
pub(super) fn callout_proofs(
    analysis: &Analysis,
    views: &[PlannedView],
) -> BTreeMap<String, std::rc::Rc<Proof>> {
    let key = serde_json::to_vec(
        &views
            .iter()
            .map(|v| (&v.id, v.direction, v.up, &v.cut))
            .collect::<Vec<_>>(),
    )
    // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
    .expect("projection inputs serialize");
    crate::planning::memo(analysis, "callout-proofs", key, || {
        proofs(analysis, views)
            .into_iter()
            .map(|(id, p)| {
                let mut p = (*p).clone();
                p.voids = true;
                (id, std::rc::Rc::new(p))
            })
            .collect()
    })
}

/// Datum placement can prove exterior paper without a square planar face.
pub(super) fn exterior_proofs(
    analysis: &Analysis,
    views: &[PlannedView],
) -> BTreeMap<String, std::rc::Rc<Proof>> {
    prepared_proofs(analysis, views, true)
}

fn prepared_proofs(
    analysis: &Analysis,
    views: &[PlannedView],
    exterior: bool,
) -> BTreeMap<String, std::rc::Rc<Proof>> {
    let Some(ink) = analysis.edge_ink.as_ref() else {
        return BTreeMap::new();
    };
    views
        .iter()
        .filter(|v| v.cut.is_none() && v.id != "v:iso" && v.broken.is_none())
        .filter_map(|v| {
            let view = haecceity::hlr::View::new(v.direction, v.up)?;
            let centre = crate::model::extent(analysis, &view).1;
            let canonical =
                crate::planning::lookup::<bool>(analysis, "canonical-circular-recipes", &[])
                    .unwrap_or(false);
            let key = serde_json::to_vec(&(v.direction, v.up, canonical, exterior))
                // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
                .expect("projection inputs serialize");
            let mut proof = crate::planning::memo(analysis, "interior", key, || {
                let prepared = if exterior {
                    build_camera_with_exterior(ink, v.direction, v.up, centre, true)
                } else {
                    build(ink, v, centre)
                };
                prepared.map(|mut p| {
                    // Replay the established comparison's old attachment recipe.
                    // Actual drawings always require visible physical attachments.
                    p.require_visible_attachment = !canonical;
                    std::rc::Rc::new(p)
                })
            })?;
            // Preserve the established eligibility of dimension/interior routing.
            // Only datum exterior placement opts into entirely turned views.
            if !exterior && proof.faces.is_empty() {
                return None;
            }
            // A cutting-plane line this view marks is fixed ink; its letters are boxes.
            for cut in views.iter().filter_map(|q| q.cut.as_ref()) {
                let name = v.id.trim_start_matches("v:");
                if cut.marked_in != v.id && cut.marked_in != name {
                    continue;
                }
                let proof = std::rc::Rc::make_mut(&mut proof);
                let (x0, x1, y) = crate::model::cutting_line(analysis, v, &view, cut);
                let (items, letters) = crate::section::cutting_plane(x0, x1, y);
                for item in &items {
                    let crate::output::Item::Face(loops) = item else {
                        continue;
                    };
                    for c in loops.iter().flatten() {
                        if let crate::output::Curve::Line(a, b) = c {
                            proof
                                .segments
                                .push([proof.model_point(v, *a), proof.model_point(v, *b)]);
                        }
                    }
                }
                for at in letters {
                    let lo = proof.model_point(v, [at[0] - 4.0, at[1] - 5.0]);
                    let hi = proof.model_point(v, [at[0] + 4.0, at[1] + 5.0]);
                    proof.boxes.push([lo[0], lo[1], hi[0], hi[1]]);
                }
                proof.grid = Grid::new(&proof.segments);
            }
            Some((v.id.clone(), proof))
        })
        .collect()
}

fn build(ink: &crate::model::projected_ink::EdgeInk, v: &PlannedView, centre: P2) -> Option<Proof> {
    build_camera(ink, v.direction, v.up, centre)
}

fn build_camera(
    ink: &crate::model::projected_ink::EdgeInk,
    direction: [f64; 3],
    up: [f64; 3],
    centre: P2,
) -> Option<Proof> {
    build_camera_with_exterior(ink, direction, up, centre, false)
}

fn build_camera_with_exterior(
    ink: &crate::model::projected_ink::EdgeInk,
    direction: [f64; 3],
    up: [f64; 3],
    centre: P2,
    exterior: bool,
) -> Option<Proof> {
    let view = haecceity::hlr::View::new(direction, up)?;
    let d = direction;
    let n = d.iter().map(|x| x * x).sum::<f64>().sqrt();
    let d = d.map(|x| x / n);
    let dot = |a: [f64; 3]| a[0] * d[0] + a[1] * d[1] + a[2] * d[2];
    let mut segments = Vec::new();
    let mut depths = Vec::new();
    let mut edges = vec![0];
    for e in &ink.edges {
        let points: Vec<P2> = e.iter().map(|p| view.map(*p)).collect();
        segments.extend(points.windows(2).map(|w| [w[0], w[1]]));
        depths.extend(e.windows(2).map(|w| [dot(w[0]), dot(w[1])]));
        edges.push(segments.len());
    }
    let exterior_faces = ink
        .planes
        .iter()
        .map(|f| {
            let chords: Vec<usize> = f
                .edges
                .iter()
                .filter(|&&e| e + 1 < edges.len())
                .flat_map(|&e| edges[e]..edges[e + 1])
                .collect();
            (bounds_of(chords.iter().flat_map(|&i| segments[i])), chords)
        })
        .filter(|(_, chords)| !chords.is_empty())
        .collect::<Vec<_>>();
    let faces: Vec<_> = ink
        .planes
        .iter()
        .filter(|f| {
            let m = f.normal.iter().map(|x| x * x).sum::<f64>().sqrt();
            (dot(f.normal).abs() / m.max(1e-12)) > 1.0 - 1e-9
        })
        .map(|f| {
            let chords: Vec<usize> = f
                .edges
                .iter()
                .filter(|&&e| e + 1 < edges.len())
                .flat_map(|&e| edges[e]..edges[e + 1])
                .collect();
            let depth = f
                .edges
                .iter()
                .filter_map(|&e| ink.edges.get(e))
                .flatten()
                .next()
                .map_or(0.0, |p| dot(*p));
            Face {
                bounds: bounds_of(chords.iter().flat_map(|&i| segments[i])),
                chords,
                depth,
            }
        })
        .filter(|face| !face.chords.is_empty())
        .collect();
    if !exterior && faces.is_empty() {
        return None;
    }
    // An entirely turned part has no plane square to a side view, but its
    // curved-face covers still prove exterior paper. `blank` continues to
    // require a containing planar face for every interior label.
    if exterior_faces.is_empty() && ink.curved.is_empty() {
        return None;
    }
    let mut boxes = Vec::new();
    let mut holes = Vec::new();
    let mut curved_cover = Vec::new();
    for f in &ink.curved {
        let [lo, hi] = f.bounds;
        let corners =
            (0..8).map(|i| [0, 1, 2].map(|k| if i >> k & 1 == 0 { lo[k] } else { hi[k] }));
        curved_cover.push(bounds_of(corners.clone().map(|p| view.map(p))));
        if let Some(c) = &f.cylinder {
            let m = c.axis.iter().map(|x| x * x).sum::<f64>().sqrt();
            let axis = c.axis.map(|x| x / m.max(1e-12));
            let along = dot(axis).abs();
            if along > 1.0 - 1e-9 {
                // Seen end-on: its outline is its rims, which are edges.
                holes.push(bounds_of(corners.clone().map(|p| view.map(p))));
                continue;
            }
            if along < 1e-9 {
                // Seen square: the silhouettes are the generators at ± radius.
                let h: Vec<f64> = corners
                    .map(|p| (0..3).map(|k| (p[k] - c.origin[k]) * axis[k]).sum())
                    .collect();
                let (h0, h1) = (
                    h.iter().copied().fold(f64::INFINITY, f64::min),
                    h.iter().copied().fold(f64::NEG_INFINITY, f64::max),
                );
                let w = [
                    axis[1] * d[2] - axis[2] * d[1],
                    axis[2] * d[0] - axis[0] * d[2],
                    axis[0] * d[1] - axis[1] * d[0],
                ];
                for s in [-1.0, 1.0] {
                    let at = |t: f64| {
                        view.map([0, 1, 2].map(|k| c.origin[k] + t * axis[k] + s * c.radius * w[k]))
                    };
                    segments.push([at(h0), at(h1)]);
                }
                continue;
            }
        }
        boxes.push(bounds_of(corners.map(|p| view.map(p))));
    }
    let grid = Grid::new(&segments);
    Some(Proof {
        centre,
        segments,
        edges,
        depths,
        require_visible_attachment: true,
        memo: ProofMemo::default(),
        boxes,
        holes,
        faces,
        exterior_faces,
        curved_cover,
        grid,
        voids: false,
    })
}

impl Proof {
    pub(super) fn geometry_key(&self) -> [u8; 32] {
        use sha2::{Digest, Sha256};
        *self.memo.fingerprint.get_or_init(|| {
            Sha256::digest(serde_json::to_vec(self).expect("prepared projection serializes")).into()
        })
    }

    fn model_point(&self, v: &PlannedView, p: P2) -> P2 {
        [
            (p[0] - v.at[0]) / v.scale + self.centre[0],
            (p[1] - v.at[1]) / v.scale + self.centre[1],
        ]
    }

    fn page_point(&self, v: &PlannedView, q: P2) -> P2 {
        [
            v.at[0] + v.scale * (q[0] - self.centre[0]),
            v.at[1] + v.scale * (q[1] - self.centre[1]),
        ]
    }

    /// Whether a box (model mm) stays clear of every chord and curved-face box by *c*.
    fn box_clear(&self, b: [f64; 4], c: f64) -> bool {
        let g = padded(b, c);
        self.grid.near(g).all(|i| !near_box(self.segments[i], b, c))
            && self.boxes.iter().all(|x| !boxes_overlap(*x, g))
    }

    /// Whether a model segment stays clear of every chord and curved-face box by *c*.
    fn line_clear(&self, s: [P2; 2], c: f64) -> bool {
        let g = padded(bounds_of(s), c);
        self.grid
            .near(g)
            .all(|i| segment_distance(s, self.segments[i]) > c)
            && self.boxes.iter().all(|x| !near_box(s, *x, c))
    }

    /// Whether page point *p* stays *c* paper mm clear of every projected edge, silhouette and
    /// curved-face box.
    pub(super) fn point_clear(&self, v: &PlannedView, p: P2, c: f64) -> bool {
        let q = self.model_point(v, p);
        self.line_clear([q, q], c / v.scale + SLACK)
    }

    pub(super) fn touches(&self, v: &PlannedView, tip: P2) -> bool {
        let q = self.model_point(v, tip);
        let c = 0.05 / v.scale + SLACK;
        self.touches_model(q, c, self.require_visible_attachment)
    }

    /// Whether a leader *tip* lies on a physical edge only where a planar face nearer the
    /// viewer covers it, so the reader sees that edge as hidden ink (#226).
    pub(super) fn hidden_tip(&self, v: &PlannedView, tip: P2) -> bool {
        let q = self.model_point(v, tip);
        let c = 0.05 / v.scale + SLACK;
        self.touches_model(q, c, false) && !self.touches_model(q, c, true)
    }

    /// The edges, visible or hidden, that page segment *s* crosses (a cylinder's side-on
    /// silhouette generators are not edges and are left out): the points inside
    /// it where it meets a physical edge chord (a chord's end included, so a line through a
    /// polyline's vertex is not missed), those within [`SLACK`] of one another along it (an edge
    /// seen end-on over another, or the two chords at a vertex) counted once.
    pub(super) fn lines_crossed(&self, v: &PlannedView, s: [P2; 2]) -> usize {
        let s = [self.model_point(v, s[0]), self.model_point(v, s[1])];
        let d = [s[1][0] - s[0][0], s[1][1] - s[0][1]];
        let length = d[0].hypot(d[1]);
        let mut at: Vec<f64> = self
            .grid
            .near(bounds_of(s))
            .filter(|&i| i < self.depths.len())
            .filter_map(|i| {
                let [a, b] = self.segments[i];
                let e = [b[0] - a[0], b[1] - a[1]];
                let den = d[0] * e[1] - d[1] * e[0];
                if den.abs() < 1e-12 {
                    return None;
                }
                let w = [a[0] - s[0][0], a[1] - s[0][1]];
                let t = (w[0] * e[1] - w[1] * e[0]) / den;
                let u = (w[0] * d[1] - w[1] * d[0]) / den;
                (t > 0.0 && t < 1.0 && (0.0..=1.0).contains(&u)).then_some(t * length)
            })
            .collect();
        at.sort_by(f64::total_cmp);
        at.dedup_by(|b, a| *b - *a <= SLACK);
        at.len()
    }

    fn touches_model(&self, q: P2, c: f64, visible: bool) -> bool {
        ProofMemo::query(
            &self.memo.visibility,
            [
                q[0].to_bits(),
                q[1].to_bits(),
                c.to_bits(),
                u64::from(visible),
            ],
            || self.touches_model_uncached(q, c, visible),
        )
    }

    fn touches_model_uncached(&self, q: P2, c: f64, visible: bool) -> bool {
        self.grid
            .near(padded([q[0], q[1], q[0], q[1]], c))
            .any(|i| {
                // A conservative silhouette generator is an obstacle, not
                // evidence that the trimmed face has a physical rim here.
                if i >= self.depths.len() || distance(q, self.segments[i]) > c {
                    return false;
                }
                let [a, z] = self.segments[i];
                let d = [z[0] - a[0], z[1] - a[1]];
                let n = d[0] * d[0] + d[1] * d[1];
                let t = if n > 1e-18 {
                    (((q[0] - a[0]) * d[0] + (q[1] - a[1]) * d[1]) / n).clamp(0.0, 1.0)
                } else {
                    0.0
                };
                let [from, to] = self.depths[i];
                let depth = if n > 1e-18 {
                    from + t * (to - from)
                } else {
                    from.max(to)
                };
                !visible
                    || !self.faces.iter().any(|f| {
                        f.depth > depth + 2.0 * SLACK
                            && self.face_contains(f, q)
                            && f.chords
                                .iter()
                                .all(|&j| distance(q, self.segments[j]) > SLACK)
                    })
            })
    }

    fn segment_clear(&self, v: &PlannedView, [a, b]: [P2; 2], attachment: bool) -> bool {
        let length = (b[0] - a[0]).hypot(b[1] - a[1]);
        let t = if attachment {
            (0.6 / length.max(1e-12)).min(1.0)
        } else {
            0.0
        };
        let start = [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])];
        let s = [self.model_point(v, start), self.model_point(v, b)];
        let c = 0.2 / v.scale + SLACK;
        if !attachment {
            return self.line_clear(s, c);
        }
        let banded = self.banded(v, [a, b]);
        let g = padded(bounds_of(s), c);
        self.grid.near(g).all(|i| {
            banded.as_ref().is_some_and(|b| b.contains(&i))
                || segment_distance(s, self.segments[i]) > c
        }) && self.boxes.iter().all(|x| !near_box(s, *x, c))
    }

    /// The chords a leader leaving its tip must cross to reach open paper: those parallel to the
    /// tip's own edge and within 3 paper mm of it (a chamfer or step's concentric outlines, as
    /// around a hex boss's flats), crossed at more than 20°. The leader crosses them wherever
    /// its label sits, outside the view too.
    fn banded(&self, v: &PlannedView, [a, b]: [P2; 2]) -> Option<std::rc::Rc<Vec<usize>>> {
        let tip = self.model_point(v, a);
        let unit = |[p, q]: [P2; 2]| {
            let d = [q[0] - p[0], q[1] - p[1]];
            let n = d[0].hypot(d[1]);
            (n > 1e-12).then(|| [d[0] / n, d[1] / n])
        };
        let key = [tip[0].to_bits(), tip[1].to_bits(), v.scale.to_bits()];
        let cached = self.memo.bands.borrow().get(&key).cloned();
        let prepared = if let Some(cached) = cached {
            cached
        } else {
            let touch = 0.05 / v.scale + SLACK;
            let own = self
                .grid
                .near(padded([tip[0], tip[1], tip[0], tip[1]], touch))
                .filter(|&i| i < self.depths.len() && distance(tip, self.segments[i]) <= touch)
                .find_map(|i| unit(self.segments[i]));
            let prepared = own.map(|own| {
                let reach = 3.0 / v.scale;
                let chords = self
                    .grid
                    .near(padded([tip[0], tip[1], tip[0], tip[1]], reach))
                    .filter(|&i| {
                        let [p, _] = self.segments[i];
                        unit(self.segments[i]).is_some_and(|d| {
                            (d[0] * own[1] - d[1] * own[0]).abs() < 2f64.to_radians().sin()
                                && ((p[0] - tip[0]) * own[1] - (p[1] - tip[1]) * own[0]).abs()
                                    <= reach
                        })
                    })
                    .collect();
                AttachmentBand {
                    direction: own,
                    chords: std::rc::Rc::new(chords),
                }
            });
            let mut memo = self.memo.bands.borrow_mut();
            if memo.len() >= 4096 {
                memo.clear();
            }
            memo.insert(key, prepared.clone());
            prepared
        }?;
        let leader = unit([tip, self.model_point(v, b)])?;
        // The angular gate belongs to this ray, even when its tip preparation
        // is shared. No direction is forgiven by a previous ray's result.
        let own = prepared.direction;
        if (own[0] * leader[1] - own[1] * leader[0]).abs() < 20f64.to_radians().sin() {
            None
        } else {
            Some(prepared.chords)
        }
    }

    fn arrow_clear(&self, v: &PlannedView, [tip, tail]: [P2; 2], attachment: bool) -> bool {
        let d = [tail[0] - tip[0], tail[1] - tip[1]];
        let length = d[0].hypot(d[1]).max(1e-12);
        let t = if attachment {
            (0.6 / length).min(1.0)
        } else {
            0.0
        };
        let normal = [
            -super::ARROW_HALF * d[1] / length,
            super::ARROW_HALF * d[0] / length,
        ];
        let start = [tip[0] + t * d[0], tip[1] + t * d[1]];
        let points = [
            [start[0] + t * normal[0], start[1] + t * normal[1]],
            [start[0] - t * normal[0], start[1] - t * normal[1]],
            [tail[0] + normal[0], tail[1] + normal[1]],
            [tail[0] - normal[0], tail[1] - normal[1]],
        ];
        let b = bounds_of(points.map(|p| self.model_point(v, p)));
        let c = 0.2 / v.scale + SLACK;
        if !attachment {
            return self.box_clear(b, c);
        }
        let banded = self.banded(v, [tip, tail]);
        let g = padded(b, c);
        self.grid.near(g).all(|i| {
            banded.as_ref().is_some_and(|b| b.contains(&i)) || !near_box(self.segments[i], b, c)
        }) && self.boxes.iter().all(|x| !boxes_overlap(*x, g))
    }

    /// Whether thin annotation *lines* and *arrows* stay clear of the view's line work; an
    /// arrow tip may touch the edge it ends on, and the first *witnesses* lines start at the
    /// corner they witness (their first 0.6 mm is that corner's attachment).
    /// With *straight_ok*, the lines may cross projected edges (dimension and extension lines
    /// crossing an outline where unavoidable, ISO 129-1), including an edge blend's silhouette,
    /// but never a hole or boss seen end-on.
    pub(super) fn lines_clear(
        &self,
        v: &PlannedView,
        lines: &[[P2; 2]],
        arrows: &[[P2; 2]],
        witnesses: usize,
        straight_ok: bool,
    ) -> bool {
        let c = 0.2 / v.scale + SLACK;
        let curved_clear = |s: [P2; 2]| {
            let m = [self.model_point(v, s[0]), self.model_point(v, s[1])];
            self.holes.iter().all(|x| !near_box(m, *x, c))
        };
        // The arc ends on the corner's edges, which a blended edge's cover surrounds: the last
        // 1.5 mm before each arrow tip is the arc's attachment.
        let attached = |s: [P2; 2]| {
            arrows.iter().any(|a| {
                s.iter()
                    .all(|p| (p[0] - a[0][0]).hypot(p[1] - a[0][1]) <= 1.5)
            })
        };
        lines.iter().enumerate().all(|(i, s)| {
            if i < witnesses && !straight_ok {
                self.witness_clear(v, *s)
            } else if straight_ok {
                attached(*s) || curved_clear(*s)
            } else {
                self.segment_clear(v, *s, false)
            }
        }) && (straight_ok || arrows.iter().all(|a| self.arrow_clear(v, *a, true)))
    }

    /// A witness line extends its corner's edge: it may run along a projected edge on that same
    /// line (the edge continuing past the corner), but may not cross or approach any other.
    /// Its first 0.6 mm is the corner's attachment.
    fn witness_clear(&self, v: &PlannedView, [a, b]: [P2; 2]) -> bool {
        let length = (b[0] - a[0]).hypot(b[1] - a[1]);
        let t = (0.6 / length.max(1e-12)).min(1.0);
        let start = [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])];
        let s = [self.model_point(v, start), self.model_point(v, b)];
        let c = 0.2 / v.scale + SLACK;
        let d = [s[1][0] - s[0][0], s[1][1] - s[0][1]];
        let n = d[0].hypot(d[1]).max(1e-12);
        let on_line = |p: P2| ((p[0] - s[0][0]) * d[1] - (p[1] - s[0][1]) * d[0]).abs() / n <= c;
        let g = padded(bounds_of(s), c);
        self.grid.near(g).all(|i| {
            let e = self.segments[i];
            (on_line(e[0]) && on_line(e[1])) || segment_distance(s, e) > c
        }) && self.boxes.iter().all(|x| !near_box(s, *x, c))
    }

    pub(super) fn annotation_clear(&self, v: &PlannedView, p: &Placed, callout: bool) -> bool {
        self.blank(v, p.label_box)
            && (!callout || self.touches(v, p.lines[0][0]))
            && p.lines
                .iter()
                .enumerate()
                .all(|(i, s)| self.segment_clear(v, *s, callout && i == 0))
            && p.arrows.iter().all(|s| self.arrow_clear(v, *s, callout))
            && (!callout || {
                let q = p.lines[0][1];
                self.blank(v, [q[0], q[1], q[0], q[1]])
            })
    }

    /// A linear dimension in a proven opening: witnesses may continue their
    /// support edge, and dimension arrows may touch that edge at their tips.
    /// Only the usual 0.6 mm attachment collars are omitted from ink checks.
    pub(super) fn opening_linear_clear(&self, v: &PlannedView, p: &Placed) -> bool {
        self.blank(v, p.label_box)
            && p.lines.iter().take(2).all(|s| self.witness_clear(v, *s))
            && p.lines.iter().skip(2).all(|s| {
                let d = [s[1][0] - s[0][0], s[1][1] - s[0][1]];
                let length = d[0].hypot(d[1]);
                if length < 1e-9 {
                    return true;
                }
                let mut gaps = Vec::new();
                for arrow in &p.arrows {
                    if self.touches(v, arrow[0]) && distance(arrow[0], *s) < 1e-8 {
                        let t = ((arrow[0][0] - s[0][0]) * d[0] + (arrow[0][1] - s[0][1]) * d[1])
                            / (length * length);
                        let (lo, hi) = ((t - 0.6 / length).max(0.), (t + 0.6 / length).min(1.));
                        for end in [lo, hi] {
                            let mut collar = p.clone();
                            collar.lines =
                                vec![[arrow[0], [s[0][0] + end * d[0], s[0][1] + end * d[1]]]];
                            collar.arrows = vec![*arrow];
                            if !self.attachment_clear(v, &collar) {
                                return false;
                            }
                        }
                        gaps.push((lo, hi));
                    }
                }
                gaps.sort_by(|a, b| a.0.total_cmp(&b.0));
                let at = |t: f64| [s[0][0] + t * d[0], s[0][1] + t * d[1]];
                let mut start = 0.;
                for (lo, hi) in gaps.into_iter().chain(std::iter::once((1., 1.))) {
                    if lo > start && !self.segment_clear(v, [at(start), at(lo)], false) {
                        return false;
                    }
                    start = start.max(hi);
                }
                true
            })
            && p.arrows
                .iter()
                .all(|a| self.arrow_clear(v, *a, self.touches(v, a[0])))
    }

    /// The attachment collar may forgive its nearest source chord only. A
    /// different view edge crossed close to the tip is still a crossing.
    fn attachment_clear(&self, v: &PlannedView, p: &Placed) -> bool {
        let tip = self.model_point(v, p.lines[0][0]);
        let tolerance = 0.05 / v.scale + SLACK;
        let near = padded([tip[0], tip[1], tip[0], tip[1]], tolerance);
        let closest = self
            .grid
            .near(near)
            .filter(|&i| i < self.depths.len())
            .map(|i| distance(tip, self.segments[i]))
            .fold(f64::INFINITY, f64::min);
        let eps = 1e-8 / v.scale;
        let banded = self.banded(v, p.lines[0]);
        let clear = |line: [P2; 2]| {
            let s = line.map(|p| self.model_point(v, p));
            self.grid.near(padded(bounds_of(s), eps)).all(|i| {
                (i < self.depths.len() && distance(tip, self.segments[i]) <= closest + eps)
                    || banded.as_ref().is_some_and(|b| b.contains(&i))
                    || segment_distance(s, self.segments[i]) > eps
            }) && self.boxes.iter().all(|b| !near_box(s, *b, eps))
        };
        clear(p.lines[0])
            && p.arrows
                .iter()
                .flat_map(|a| super::leaders::head(*a))
                .all(clear)
    }

    /// A leader-attached frame may keep a proven point within a face. Feature
    /// size/radius callouts still require their physical rim attachment.
    pub(super) fn leader_clear(&self, v: &PlannedView, p: &Placed, edge_required: bool) -> bool {
        if edge_required {
            return self.annotation_clear(v, p, true) && self.attachment_clear(v, p);
        }
        let Some([tip, elbow]) = p.lines.first().copied() else {
            return false;
        };
        let on_edge = self.touches(v, tip);
        self.blank(v, p.label_box)
            && (!on_edge || self.attachment_clear(v, p))
            && (on_edge || self.blank(v, [tip[0], tip[1], tip[0], tip[1]]))
            && self.blank(v, [elbow[0], elbow[1], elbow[0], elbow[1]])
            && p.lines
                .iter()
                .enumerate()
                .all(|(i, s)| self.segment_clear(v, *s, on_edge && i == 0))
            && p.arrows.iter().all(|s| self.arrow_clear(v, *s, on_edge))
    }

    /// A bounded notch may carry its angle in the open recess within the view's
    /// rectangular envelope. Prove the whole label outside every projected face,
    /// with the same ink margins as face-interior labels. Curved faces use their
    /// full bounds (including end-on cylinders), so this fails closed near them.
    pub(super) fn exterior_annotation_clear(&self, v: &PlannedView, p: &Placed) -> bool {
        self.exterior_clear(v, p.label_box, &p.lines, &p.arrows)
    }

    /// Prove annotation ink on exterior paper within the view's overall bounds.
    /// Shared by compact datum placement and collision reporting.
    pub(super) fn exterior_clear(
        &self,
        v: &PlannedView,
        label: [f64; 4],
        lines: &[[P2; 2]],
        arrows: &[[P2; 2]],
    ) -> bool {
        let lo = self.model_point(v, [label[0], label[1]]);
        let hi = self.model_point(v, [label[2], label[3]]);
        let b = [lo[0], lo[1], hi[0], hi[1]];
        let c = 0.5 / v.scale + SLACK;
        let middle = [(b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0];
        self.box_clear(b, c)
            && self
                .curved_cover
                .iter()
                .all(|x| !boxes_overlap(*x, padded(b, c)))
            && self.exterior_faces.iter().all(|(fb, chords)| {
                !contains(*fb, middle)
                    || chords
                        .iter()
                        .filter(|&&i| {
                            let [a, z] = self.segments[i];
                            (a[1] > middle[1]) != (z[1] > middle[1])
                                && middle[0]
                                    < a[0] + (middle[1] - a[1]) * (z[0] - a[0]) / (z[1] - a[1])
                        })
                        .count()
                        % 2
                        == 0
            })
            && lines.iter().all(|s| self.segment_clear(v, *s, false))
            && arrows.iter().all(|s| self.arrow_clear(v, *s, false))
    }

    /// A visible profile attachment and its complete exterior ink. Unlike an
    /// interior label, this label does not need a containing planar face.
    pub(super) fn profile_clear(&self, v: &PlannedView, p: &Placed) -> bool {
        let lo = self.model_point(v, [p.label_box[0], p.label_box[1]]);
        let hi = self.model_point(v, [p.label_box[2], p.label_box[3]]);
        self.box_clear([lo[0], lo[1], hi[0], hi[1]], 0.3 / v.scale + SLACK)
            && self.touches(v, p.lines[0][0])
            && p.lines
                .iter()
                .enumerate()
                .all(|(i, s)| self.segment_clear(v, *s, i == 0))
            && p.arrows.iter().all(|s| self.arrow_clear(v, *s, true))
    }

    /// Whole-label containment in one planar face square to the view, or in a void the view
    /// shows (a through slot's opening, the open paper between lugs), clear of all projected
    /// edges and silhouettes by 0.5 paper mm (maintainer 2026-10-25: labels sit inside where the
    /// paper is blank). A void is proven as exterior paper is: outside every curved face's
    /// cover and every planar face's projection, so curved material never carries a label.
    pub(super) fn blank(&self, v: &PlannedView, label: [f64; 4]) -> bool {
        let lo = self.model_point(v, [label[0], label[1]]);
        let hi = self.model_point(v, [label[2], label[3]]);
        let c = (0.5 / v.scale).max(0.0005) + SLACK;
        let b = [lo[0], lo[1], hi[0], hi[1]];
        let middle = [(b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0];
        self.box_clear(b, c)
            && (self
                .faces
                .iter()
                .any(|face| self.face_contains(face, middle))
                || (self.voids && self.void(b, c)))
    }

    /// Whether model box *b* lies in no curved face's cover (padded by *c*) and its middle in no
    /// planar face's projection, yet within some planar face's extent: an opening the part
    /// surrounds or flanks, never paper off the part.
    fn void(&self, b: [f64; 4], c: f64) -> bool {
        let middle = [(b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0];
        self.exterior_faces
            .iter()
            .any(|(fb, _)| contains(*fb, middle))
            && self
                .curved_cover
                .iter()
                .all(|x| !boxes_overlap(*x, padded(b, c)))
            && self.exterior_faces.iter().all(|(fb, chords)| {
                !contains(*fb, middle)
                    || chords
                        .iter()
                        .filter(|&&i| {
                            let [a, z] = self.segments[i];
                            (a[1] > middle[1]) != (z[1] > middle[1])
                                && middle[0]
                                    < a[0] + (middle[1] - a[1]) * (z[0] - a[0]) / (z[1] - a[1])
                        })
                        .count()
                        % 2
                        == 0
            })
    }

    fn face_contains(&self, face: &Face, q: P2) -> bool {
        contains(face.bounds, q)
            && face
                .chords
                .iter()
                .filter(|&&i| {
                    let [a, z] = self.segments[i];
                    (a[1] > q[1]) != (z[1] > q[1])
                        && q[0] < a[0] + (q[1] - a[1]) * (z[0] - a[0]) / (z[1] - a[1])
                })
                .count()
                % 2
                == 1
    }

    /// Points on the arcs of the circle about *centre* (page) of *radius* (model mm), a quarter,
    /// half and three quarters along each: physical attachments for a radius callout.
    fn arc_stations(&self, v: &PlannedView, centre: P2, radius: f64) -> Vec<P2> {
        let c = self.model_point(v, centre);
        self.arc_model_stations(c, radius)
            .into_iter()
            .map(|p| self.page_point(v, p))
            .collect()
    }

    fn arc_model_stations(&self, c: P2, radius: f64) -> Vec<P2> {
        let mut out = Vec::new();
        for e in self.edges.windows(2) {
            let chords = &self.segments[e[0]..e[1]];
            if chords.len() < 2
                || chords
                    .iter()
                    .flatten()
                    .any(|p| ((p[0] - c[0]).hypot(p[1] - c[1]) - radius).abs() > 2.0 * SLACK)
            {
                continue;
            }
            let lengths: Vec<f64> = chords
                .iter()
                .map(|[a, b]| (b[0] - a[0]).hypot(b[1] - a[1]))
                .collect();
            let total: f64 = lengths.iter().sum();
            for station in [0.25, 0.5, 0.75] {
                let mut left = station * total;
                for (s, l) in chords.iter().zip(&lengths) {
                    // Invariant: The inner loop can run only when chords has a member.
                    if left <= *l || std::ptr::eq(s, chords.last().unwrap()) {
                        let t = if *l > 0.0 { (left / l).min(1.0) } else { 0.0 };
                        out.push([
                            s[0][0] + t * (s[1][0] - s[0][0]),
                            s[0][1] + t * (s[1][1] - s[0][1]),
                        ]);
                        break;
                    }
                    left -= l;
                }
            }
        }
        out
    }
}

/// Visibility of physical circular rims before layout. Missing evidence leaves
/// the established presentation unchanged; proven occlusion can select a profile.
pub(super) fn visible_circular_rims(
    analysis: &Analysis,
    name: &str,
    sites: &[(super::P3, f64)],
) -> Option<bool> {
    let ink = analysis.edge_ink.as_ref()?;
    let direction = super::TOWARD.iter().find(|(n, _)| *n == name)?.1;
    let up = if direction[2].abs() > 0.9 {
        [0., 1., 0.]
    } else {
        [0., 0., 1.]
    };
    let camera = haecceity::hlr::View::new(direction, up)?;
    let key = serde_json::to_vec(&(direction, up)).ok()?;
    let proof = crate::planning::memo(analysis, "routing-visibility", key, || {
        build_camera(ink, direction, up, [0., 0.]).map(std::rc::Rc::new)
    })?;
    let mut found = false;
    for (centre, radius) in sites {
        for q in proof.arc_model_stations(camera.map(*centre), *radius) {
            found = true;
            if proof.touches_model(q, 2.0 * SLACK, proof.require_visible_attachment) {
                return Some(true);
            }
        }
    }
    found.then_some(false)
}

fn text_conflict(a: &Placed, b: &Placed) -> bool {
    boxes_overlap(padded(a.label_box, 0.5), b.label_box)
        || a.lines
            .iter()
            .chain(&a.arrows)
            .any(|s| crosses(*s, padded(b.label_box, 0.5)))
        || b.lines
            .iter()
            .chain(&b.arrows)
            .any(|s| crosses(*s, padded(a.label_box, 0.5)))
}

fn conflict(a: &Placed, b: &Placed) -> bool {
    text_conflict(a, b)
        || a.lines.iter().chain(&a.arrows).any(|s| {
            b.lines
                .iter()
                .chain(&b.arrows)
                .any(|t| super::leaders::lines_meet(*s, *t))
        })
}

/// Overlapping parallel linear spans need room for their common-origin
/// arrowheads and text row; a sub-millimetre double baseline is not a lane.
fn opening_lanes_too_close(a: &Placed, b: &Placed) -> bool {
    if a.rotation != b.rotation || a.arrows.len() != 2 || b.arrows.len() != 2 {
        return false;
    }
    let (Some(x), Some(y)) = (a.lines.get(2), b.lines.get(2)) else {
        return false;
    };
    let k = usize::from(a.rotation != 0.);
    let extent = |s: &[P2; 2]| [s[0][k].min(s[1][k]), s[0][k].max(s[1][k])];
    let (xspan, yspan) = (extent(x), extent(y));
    xspan[0].max(yspan[0]) < xspan[1].min(yspan[1]) - super::ARROW
        && (x[0][1 - k] - y[0][1 - k]).abs() < CAP + 1.
}

/// A spacing preference, applied only after material and settled-ink clearance.
fn prefers_centre_row(frame: &Frame, w: &Want, v: &PlannedView, prismatic: bool) -> bool {
    let Shape::Callout { sites, .. } = &w.shape else {
        return false;
    };
    prismatic
        && w.measurements
            .iter()
            .any(|m| m.parameter == "bore.diameter")
        && !sites.is_empty()
        && sites.iter().all(|(centre, _)| {
            let p = frame.page(*centre);
            (p[0] - v.at[0]).hypot(p[1] - v.at[1]) <= 1e-3 * frame.scale()
        })
}

#[allow(clippy::too_many_arguments)]
fn callouts(
    frame: &Frame,
    w: &Want,
    old: &Placed,
    proof: &Proof,
    v: &PlannedView,
    recess_context: bool,
    centre_row: bool,
    peers: &[&Placed],
) -> Vec<Placed> {
    let Shape::Callout { sites, arcs } = &w.shape else {
        return Vec::new();
    };
    let radial = w.measurements.iter().any(|m| {
        m.parameter == "bore.diameter" || recess_context && m.parameter == "slot_end_radius.radius"
    });
    let ink = label_ink(&w.terms, font());
    let ray = old
        .lines
        .first()
        .map(|[a, b]| [b[0] - a[0], b[1] - a[1]])
        .unwrap_or([1.0, 0.0]);
    let n = ray[0].hypot(ray[1]).max(1e-12);
    let u = [ray[0] / n, ray[1] / n];
    let mut attachments = vec![old.lines[0][0]];
    if !radial {
        // Physical arc anchors, rather than the absent half of a construction circle.
        'sites: for (centre, radius) in sites {
            for mut tip in proof.arc_stations(v, frame.page(*centre), *radius) {
                if !arcs.is_empty() {
                    // The stored edge is chord sampled. Restore its exact radius
                    // before using a physical station as a leader attachment.
                    let Some((c, r, angle)) = arcs.iter().find_map(|arc| {
                        let (c, r, span) = arc.projected(|p| frame.page(p));
                        let angle = (tip[1] - c[1]).atan2(tip[0] - c[0]);
                        (((tip[0] - c[0]).hypot(tip[1] - c[1]) - r).abs()
                            <= 2.0 * SLACK * frame.scale()
                            && span.contains(angle, 1e-9))
                        .then_some((c, r, angle))
                    }) else {
                        continue;
                    };
                    if ((tip[0] - c[0]).hypot(tip[1] - c[1]) - r).abs()
                        > crate::lint::leaders::TOLERANCE
                    {
                        tip = [c[0] + r * angle.cos(), c[1] + r * angle.sin()];
                    }
                }
                if !attachments
                    .iter()
                    .any(|p| (p[0] - tip[0]).hypot(p[1] - tip[1]) <= 0.05)
                {
                    attachments.push(tip);
                }
                if attachments.len() == 64 {
                    break 'sites;
                }
            }
        }
    }
    let mut out = Vec::new();
    let mut rotations = [0.0_f64, 45.0, -45.0, 90.0, -90.0, 135.0, -135.0, 180.0];
    if centre_row {
        // Snap the two rays nearest horizontal; preserve the other six directions within
        // the same eight-ray budget, including safe alternatives when the row is blocked.
        let horizontal = |i: usize| {
            let (s, c) = rotations[i].to_radians().sin_cos();
            u[0] * c - u[1] * s
        };
        let right = (0..rotations.len())
            .max_by(|&a, &b| horizontal(a).total_cmp(&horizontal(b)))
            // Invariant: rotations is a fixed nonempty array of eight angles.
            .unwrap();
        let left = (0..rotations.len())
            .min_by(|&a, &b| horizontal(a).total_cmp(&horizontal(b)))
            // Invariant: rotations is a fixed nonempty array of eight angles.
            .unwrap();
        let angle = u[1].atan2(u[0]).to_degrees();
        rotations[right] = -angle;
        rotations[left] = 180.0 - angle;
    }
    // Share Python's 64 alternatives round-robin over anchors; grouping cannot multiply work.
    let stations: Vec<_> = if radial && recess_context {
        // Short radial routes round both physical end caps get first claim.
        // Finer directions can pass between a new centre mark and a slot wall.
        let offsets: &[f64] = if arcs.is_empty() {
            &[0.0]
        } else {
            &[0.0, 5.625, 2.8125, 8.4375]
        };
        let angles: Vec<_> = offsets
            .iter()
            .copied()
            .flat_map(|offset| (0..32).map(move |a| f64::from(a) * 11.25 + offset))
            .collect();
        (1..=8)
            .flat_map(|n| angles.iter().copied().map(move |a| (a, n)))
            .collect()
    } else {
        // Non-radial profile leaders also try the established 22.5-degree
        // escape phase. Changing an exterior attachment must not rotate the
        // entire interior inventory and lose a previously clear short route.
        let phase = 22.5 - u[1].atan2(u[0]).to_degrees();
        let canonical: Vec<_> = if through_notch(w) && !recess_context {
            // A narrow clear interval between established witnesses can fall
            // between the eight profile rays. Three fixed escape phases keep
            // this family bounded (32 directions with the original rays).
            [0., -11.25, 11.25]
                .into_iter()
                .flat_map(|offset| rotations.map(|a| a + phase + offset))
                .collect()
        } else {
            (!arcs.is_empty() && !recess_context)
                .then_some(rotations.map(|a| a + phase))
                .into_iter()
                .flatten()
                .collect()
        };
        canonical
            .into_iter()
            .chain(rotations)
            .flat_map(|a| (1..=8).map(move |n| (a, n)))
            .collect()
    };
    for (rotation, lane) in stations {
        let (s, c) = rotation.to_radians().sin_cos();
        let d = [u[0] * c - u[1] * s, u[0] * s + u[1] * c];
        let tips: Vec<_> = if radial && !arcs.is_empty() {
            arcs.iter()
                .filter_map(|arc| {
                    let (centre, radius, span) = arc.projected(|p| frame.page(p));
                    let angle = d[1].atan2(d[0]);
                    span.contains(angle, 1e-9)
                        .then_some([centre[0] + radius * d[0], centre[1] + radius * d[1]])
                })
                .collect()
        } else if radial {
            sites
                .iter()
                .take(64)
                .map(|(centre, radius)| {
                    let q = frame.page(*centre);
                    let r = radius * frame.scale();
                    [q[0] + r * d[0], q[1] + r * d[1]]
                })
                .collect()
        } else {
            attachments.clone()
        };
        for tip in tips {
            let outline = frame.outline();
            let exit = (0..2)
                .filter_map(|k| {
                    if d[k] > 0.0 {
                        Some((outline[k + 2] - tip[k]) / d[k])
                    } else if d[k] < 0.0 {
                        Some((outline[k] - tip[k]) / d[k])
                    } else {
                        None
                    }
                })
                .fold(f64::INFINITY, f64::min)
                .max(0.0);
            if lane as f64 * lane_pitch() + 0.5 >= exit {
                continue;
            }
            let elbow = [
                tip[0] + lane as f64 * lane_pitch() * d[0],
                tip[1] + lane as f64 * lane_pitch() * d[1],
            ];
            let right = d[0] >= -1e-9;
            let end = [
                elbow[0]
                    + if right {
                        super::SHOULDER
                    } else {
                        -super::SHOULDER
                    },
                elbow[1],
            ];
            let label_at = [
                if right {
                    end[0] + 1.0 - ink.min[0]
                } else {
                    end[0] - 1.0 - ink.max[0]
                },
                elbow[1] - 0.5 * CAP,
            ];
            let p = Placed {
                lines: vec![[tip, elbow], [elbow, end]],
                arrows: vec![arrow(tip, elbow)],
                label_at,
                rotation: 0.0,
                label_box: label_box(&ink, label_at, 0.0),
            };
            if proof.annotation_clear(v, &p, true)
                && (arcs.is_empty() || peers.iter().all(|q| !conflict(&p, q)))
            {
                out.push(p);
            }
            if out.len() == 64 {
                return out;
            }
        }
    }
    out
}

pub(super) fn through_notch(w: &Want) -> bool {
    w.measurements
        .iter()
        .any(|m| m.feature.starts_with("f:rectangular_through_notch:"))
}

// Rank text alternatives without allocating their unchanged witnesses and
// arrowheads. The stable order and every final clearance predicate are shared
// with the exhaustive route; only the candidate's storage is lazy.
fn first_clear_opening_lane(
    bases: &[Placed],
    drawable: [f64; 4],
    preference: impl Fn(&Placed) -> (bool, bool),
    order: impl Fn(&(bool, bool, f64), &(bool, bool, f64)) -> std::cmp::Ordering,
    clear: impl Fn(&Placed) -> bool,
) -> Option<Placed> {
    struct Alternative {
        rank: (bool, bool, f64),
        base: usize,
        slide: Option<(f64, [f64; 2])>,
    }
    fn prepare(probe: &mut Placed, base: &Placed, slide: Option<(f64, [f64; 2])>) {
        probe.label_at = base.label_at;
        probe.label_box = base.label_box;
        probe.rotation = base.rotation;
        if let Some(line) = base.lines.get(2) {
            probe.lines[2] = *line;
        }
        if let Some((delta, extent)) = slide {
            let k = usize::from(base.rotation != 0.);
            probe.label_box[k] += delta;
            probe.label_box[k + 2] += delta;
            probe.label_at[k] += delta;
            probe.lines[2][0][k] = extent[0];
            probe.lines[2][1][k] = extent[1];
        }
    }
    let mut probe = bases.first()?.clone();
    let mut alternatives = Vec::with_capacity(bases.len().saturating_mul(17));
    for (base, p) in bases.iter().enumerate() {
        let preferred = preference(p);
        let length = |[a, b]: &[P2; 2]| (a[0] - b[0]).hypot(a[1] - b[1]);
        let prefix: f64 = p.lines.iter().take(2).map(length).sum();
        let suffix: Vec<_> = p.lines.iter().skip(3).map(length).collect();
        // Text slides change only the dimension line. Retain every addition
        // in its original order while reusing the constant witness lengths.
        let rank = |q: &Placed| {
            let sum = q.lines.get(2).map_or(prefix, |[a, b]| {
                let (x, y) = (a[0] - b[0], a[1] - b[1]);
                let run = if x.is_finite() && y == 0. {
                    x.abs()
                } else if y.is_finite() && x == 0. {
                    y.abs()
                } else {
                    x.hypot(y)
                };
                suffix.iter().fold(prefix + run, |sum, next| sum + next)
            });
            (preferred.0, preferred.1, sum)
        };
        probe.lines.clone_from(&p.lines);
        probe.arrows.clone_from(&p.arrows);
        alternatives.push(Alternative {
            rank: rank(p),
            base,
            slide: None,
        });
        for slide in super::placement::slide_parameters(p, drawable).take(16) {
            prepare(&mut probe, p, Some(slide));
            alternatives.push(Alternative {
                rank: rank(&probe),
                base,
                slide: Some(slide),
            });
        }
    }
    alternatives.sort_by(|a, b| order(&a.rank, &b.rank));
    let mut previous = None;
    for a in alternatives {
        if previous != Some(a.base) {
            probe.lines.clone_from(&bases[a.base].lines);
            probe.arrows.clone_from(&bases[a.base].arrows);
            previous = Some(a.base);
        }
        prepare(&mut probe, &bases[a.base], a.slide);
        if clear(&probe) {
            return Some(probe);
        }
    }
    None
}

#[allow(clippy::too_many_arguments)]
pub(super) fn place(
    analysis: &Analysis,
    spec: &Spec,
    views: &[PlannedView],
    frames: &BTreeMap<&str, Frame>,
    items: &[Want],
    chosen: &[Option<&str>],
    sides: &[Option<Side>],
    placed: &mut [Option<Placed>],
    page: &Page,
    obstacles: &[PlannedDimension],
    cache: &super::footprints::Cache,
    yield_callouts: bool,
    final_inventory: bool,
) -> BTreeSet<String> {
    let mut moved = BTreeSet::new();
    // Extra recess footprints can force radius retries. Retain the established routes on
    // drawings whose feature inventory is unaffected by this reader change.
    let recess_context = items.iter().any(|w| {
        w.measurements
            .iter()
            .any(|m| m.feature.starts_with("f:section_recess:"))
    });
    // Python excludes both a rotational envelope and a stepped turning profile. A plain
    // cylinder need not publish a turned_step record, so use the scanned analytic faces too:
    // an exterior surface of revolution spans the envelope in both transverse directions.
    let round_stock = analysis.features.iter().any(|f| f.kind == "turned_step")
        || analysis.principal_profiles.iter().any(|s| {
            s.curved_faces.iter().any(|f| {
                f.axis
                    .and_then(crate::bridge::holes::principal)
                    .is_some_and(|axis| {
                        (0..3).filter(|&k| k != axis).all(|k| {
                            (0..2).all(|end| {
                                (f.bounds[end][k] - analysis.envelope[end][k]).abs() <= 1e-3
                            })
                        })
                    })
            })
        });
    let prismatic = !round_stock;
    let proofs = proofs(analysis, views);
    let openings = items
        .iter()
        .any(through_notch)
        .then(|| callout_proofs(analysis, views));
    // Centre-line furniture (G6) is not annotation ink: interior text keeps clear of its
    // strokes, and a callout's shaft does not run beside another feature's stroke, but
    // witness and dimension lines may cross or continue it as they do outside the view.
    let (furniture, obstacles): (Vec<&PlannedDimension>, Vec<&PlannedDimension>) =
        obstacles.iter().partition(|d| d.kind == "ink");
    let furniture_clear = |p: &Placed, w: &Want| {
        let callout = matches!(w.shape, Shape::Callout { .. });
        furniture.iter().all(|d| {
            let Some(q) = d.placed.as_ref() else {
                return true;
            };
            let own = w
                .measurements
                .iter()
                .any(|m| d.id.ends_with(&format!(":{}", m.feature)));
            q.lines.iter().all(|t| {
                !near_box(*t, p.label_box, 0.5)
                    && (!callout
                        || own
                        || p.lines
                            .first()
                            .is_none_or(|s| !crate::centrelines::runs_beside(*s, *t)))
            })
        })
    };
    // Linear retries are decided first, against the exterior ink, so a callout's own interior
    // move (which depends on its label's length) never decides whether a peer linear
    // dimension goes inside: a tolerance or fit edit on the callout leaves them as they were.
    let order: Vec<usize> = (0..items.len())
        .filter(|&i| matches!(items[i].shape, Shape::Linear { .. }))
        .chain((0..items.len()).filter(|&i| !matches!(items[i].shape, Shape::Linear { .. })))
        .collect();
    let movable_callout = |i: usize, j: usize| {
        let opening_row = !final_inventory
            || items[i].measurements.iter().any(|m| {
                if !m.parameter.starts_with("location.") {
                    return false;
                }
                let Shape::Linear { axis, .. } = items[i].shape else {
                    return false;
                };
                analysis
                    .features
                    .iter()
                    .find(|f| f.id == m.feature)
                    .and_then(|f| crate::lint::rectangular_notches::read(analysis, f).ok())
                    .is_some_and(|n| n.wide == axis)
            });
        yield_callouts
            && opening_row
            && matches!(items[j].shape, Shape::Callout { .. })
            && spec
                .dimensions
                .get(&items[j].id)
                .is_none_or(|d| d.place.is_none())
    };
    for i in order {
        let movable: Vec<_> = (0..items.len()).map(|j| movable_callout(i, j)).collect();
        // A plate thickness witnesses its two faces at the silhouette. An
        // interior lane can move the dimension line across the view while
        // leaving its witnesses attached to the far edge, reversing their
        // physical meaning even when the face has blank room for its text.
        if items[i]
            .measurements
            .iter()
            .any(|m| m.parameter == "thickness.length")
        {
            continue;
        }
        if spec
            .dimensions
            .get(&items[i].id)
            .is_some_and(|d| d.place.is_some())
        {
            continue;
        }
        let (Some(name), Some(old)) = (chosen[i], placed[i].as_ref()) else {
            continue;
        };
        if matches!(items[i].shape, Shape::Linear { .. })
            && placed.iter().enumerate().any(|(j, q)| {
                i != j
                    && chosen[j] == chosen[i]
                    && super::placement::slot_chain_claims(
                        &items[i].measurements,
                        &items[j].measurements,
                    )
                    && q.as_ref()
                        .is_some_and(|q| super::placement::forms_chain(old, q))
            })
        {
            continue;
        }
        let Some(v) = views.iter().find(|v| v.id.trim_start_matches("v:") == name) else {
            continue;
        };
        let proof = if through_notch(&items[i]) {
            openings.as_ref().and_then(|proofs| proofs.get(&v.id))
        } else {
            proofs.get(&v.id)
        };
        let Some(proof) = proof else {
            continue;
        };
        let frame = &frames[name];
        let f = cache.get(analysis, v);
        let extensions = f.extension_counter(&items[i].measurements);
        let clipper = through_notch(&items[i]).then(|| f.witness_clipper(&items[i].measurements));
        let centre_row = prefers_centre_row(frame, &items[i], v, prismatic);
        let mut baseline_candidates = usize::MAX;
        let slot_layout = super::placement::automatic_slot_layout(analysis);
        let opening_linear =
            through_notch(&items[i]) && matches!(items[i].shape, Shape::Linear { .. });
        let varying_gaps = slot_layout
            && items[i]
                .measurements
                .iter()
                .any(|m| m.parameter == "slot_width.length");
        let ordered_opening = opening_linear && !varying_gaps && !centre_row;
        // This is the identical raw-stroke predicate used by text_conflict,
        // indexed once for the fixed peers of this opening's lane search.
        let peer_ink = (through_notch(&items[i]) && matches!(items[i].shape, Shape::Linear { .. }))
            .then(|| {
                super::leaders::SegmentIndex::new(
                    placed
                        .iter()
                        .enumerate()
                        .filter(|(j, _)| *j != i && !movable[*j])
                        .filter_map(|(_, p)| p.as_ref())
                        .chain(obstacles.iter().filter_map(|d| d.placed.as_ref()))
                        .flat_map(|q| q.lines.iter().chain(&q.arrows).copied())
                        .collect(),
                )
            });
        let mut candidates = match &items[i].shape {
            Shape::Callout { .. } => {
                let peers: Vec<_> = placed
                    .iter()
                    .enumerate()
                    .filter(|(j, _)| *j != i)
                    .filter_map(|(_, p)| p.as_ref())
                    .chain(obstacles.iter().filter_map(|d| d.placed.as_ref()))
                    .collect();
                callouts(
                    frame,
                    &items[i],
                    old,
                    proof,
                    v,
                    recess_context,
                    centre_row,
                    &peers,
                )
            }
            Shape::Linear { .. }
                if through_notch(&items[i])
                    || !page.fits(old, name)
                    || old.lines.iter().take(2).any(|s| extensions.count(*s) != 0)
                    || obstacles
                        .iter()
                        .filter_map(|d| d.placed.as_ref())
                        .any(|p| conflict(old, p))
                    || placed.iter().enumerate().any(|(j, p)| {
                        j != i && p.as_ref().is_some_and(|p| text_conflict(old, p))
                    }) =>
            {
                let mut candidates = sides[i]
                    .map(|side| {
                        let mut out = super::placement::interior_lanes(frame, &items[i], side);
                        if through_notch(&items[i]) {
                            let other = match side {
                                Side::Above => Side::Below,
                                Side::Below => Side::Above,
                                Side::Left => Side::Right,
                                Side::Right => Side::Left,
                            };
                            out.extend(super::placement::interior_lanes(frame, &items[i], other));
                            for side in [side, other] {
                                if final_inventory {
                                    out.extend(super::placement::opening_lanes(
                                        frame, &items[i], side,
                                    ));
                                }
                                out.extend((0..8).filter_map(|lane| {
                                    super::placement::place(frame, &items[i], side, lane)
                                }));
                            }
                        }
                        out
                    })
                    .unwrap_or_default();
                // Preserve a proven original lane; the new local stations and
                // physical reference pairs are fallbacks for blocked routes.
                if !through_notch(&items[i]) {
                    baseline_candidates = candidates.len().min(8);
                }
                if !slot_layout && !through_notch(&items[i]) {
                    candidates.truncate(baseline_candidates);
                }
                let references: Vec<_> = if through_notch(&items[i]) {
                    super::notches::references(&items[i], analysis)
                } else if slot_layout {
                    super::slots::width_references(&items[i], analysis)
                        .into_iter()
                        .chain(super::placement::pattern_references(&items[i], analysis))
                        .collect()
                } else {
                    Vec::new()
                };
                for shape in references {
                    let mut alternative = items[i].clone();
                    alternative.shape = shape;
                    for side in [
                        super::placement::Side::Above,
                        super::placement::Side::Below,
                        super::placement::Side::Left,
                        super::placement::Side::Right,
                    ] {
                        let Shape::Linear { axis, .. } = alternative.shape else {
                            continue;
                        };
                        let horizontal = super::placement::across(frame, axis) == Some(true);
                        if horizontal
                            == matches!(
                                side,
                                super::placement::Side::Above | super::placement::Side::Below
                            )
                        {
                            candidates.extend(super::placement::interior_lanes(
                                frame,
                                &alternative,
                                side,
                            ));
                            if through_notch(&items[i]) {
                                if final_inventory {
                                    candidates.extend(super::placement::opening_lanes(
                                        frame,
                                        &alternative,
                                        side,
                                    ));
                                }
                                candidates.extend((0..8).filter_map(|lane| {
                                    super::placement::place(frame, &alternative, side, lane)
                                }));
                            }
                        }
                    }
                }
                candidates
            }
            _ => Vec::new(),
        };

        if opening_linear {
            candidates.insert(0, old.clone());
            candidates = candidates
                .into_iter()
                // Slides change only text and the third line. Prove and clip
                // their identical witnesses once, before expanding the family.
                .filter_map(|mut p| {
                    p.lines
                        .iter_mut()
                        .take(2)
                        .all(|s| clipper.as_ref().expect("opening witnesses").clip(s))
                        .then_some(p)
                })
                .filter(|p| {
                    if !ordered_opening {
                        return true;
                    }
                    // Every slide retains these witnesses and arrows. A
                    // contact made by that immutable ink cannot be repaired
                    // by moving its label/run-on, so reject the family once.
                    let mut strokes = p.clone();
                    strokes.lines.truncate(2);
                    let blocked = |q: &Placed, linear: bool| {
                        strokes
                            .lines
                            .iter()
                            .chain(&strokes.arrows)
                            .any(|s| crosses(*s, padded(q.label_box, 0.5)))
                            || if linear {
                                super::placement::ink_through(
                                    &strokes,
                                    super::contacts::clear_box(q.label_box),
                                )
                            } else {
                                strokes.lines.iter().chain(&strokes.arrows).any(|s| {
                                    q.lines
                                        .iter()
                                        .chain(&q.arrows)
                                        .any(|t| super::leaders::lines_meet(*s, *t))
                                })
                            }
                    };
                    !placed.iter().enumerate().any(|(j, q)| {
                        j != i
                            && !movable[j]
                            && q.as_ref().is_some_and(|q| {
                                blocked(q, matches!(items[j].shape, Shape::Linear { .. }))
                            })
                    }) && !obstacles
                        .iter()
                        .filter_map(|d| d.placed.as_ref())
                        .any(|q| blocked(q, false))
                })
                .collect();
            if !ordered_opening {
                candidates = candidates
                    .into_iter()
                    .flat_map(|p| {
                        let shifted = super::placement::slides(&p, page.drawable())
                            .take(16)
                            .collect::<Vec<_>>();
                        std::iter::once(p).chain(shifted)
                    })
                    .collect();
            }
        }

        let geometry_clear = |p: &Placed| {
            if through_notch(&items[i]) && matches!(items[i].shape, Shape::Linear { .. }) {
                proof.opening_linear_clear(v, p)
                    || (!boxes_overlap(p.label_box, frame.outline())
                        && p.lines
                            .iter()
                            .skip(2)
                            .chain(&p.arrows)
                            .all(|s| !near_box(*s, frame.outline(), 0.)))
            } else {
                proof.annotation_clear(v, p, matches!(items[i].shape, Shape::Callout { .. }))
            }
        };
        let clear = |p: &Placed| {
            page.fits_interior(p, name)
                && (!through_notch(&items[i])
                    || page.area(name).is_none_or(|b| {
                        super::placement::ink_points(p)
                            .all(|q| q[0] >= b[0] && q[0] <= b[2] && q[1] >= b[1] && q[1] <= b[3])
                    }))
                && peer_ink
                    .as_ref()
                    .is_none_or(|ink| !ink.crosses_box(padded(p.label_box, 0.5)))
                && !placed.iter().enumerate().any(|(j, q)| {
                    j != i
                        && !movable[j]
                        && q.as_ref().is_some_and(|q| {
                            if through_notch(&items[i])
                                && matches!(items[i].shape, Shape::Linear { .. })
                                && matches!(items[j].shape, Shape::Linear { .. })
                            {
                                // Ordinary linear dimensions share and cross witness
                                // lines under the same rules used in exterior lanes.
                                (final_inventory && opening_lanes_too_close(p, q))
                                    || text_conflict(p, q)
                                    || super::placement::ink_through(
                                        p,
                                        super::contacts::clear_box(q.label_box),
                                    )
                                    || super::placement::ink_through(
                                        q,
                                        super::contacts::clear_box(p.label_box),
                                    )
                                    || p.lines.get(2).zip(q.lines.get(2)).is_some_and(|(a, b)| {
                                        super::leaders::segments_cross(*a, *b)
                                    })
                                    || super::placement::linear_pair_contact(p, q, false)
                            } else {
                                conflict(p, q)
                                    || (final_inventory
                                        && through_notch(&items[i])
                                        && matches!(items[i].shape, Shape::Linear { .. })
                                        && q.arrows.len() == 1
                                        && super::placement::linear_pair_contact(p, q, false))
                            }
                        })
                })
                && !obstacles
                    .iter()
                    .filter_map(|d| d.placed.as_ref())
                    .any(|q| conflict(p, q))
                && furniture_clear(p, &items[i])
                && p.lines
                    .iter()
                    .take(if matches!(items[i].shape, Shape::Callout { .. }) {
                        1
                    } else {
                        2
                    })
                    .all(|s| {
                        if matches!(items[i].shape, Shape::Callout { .. }) {
                            !f.crosses_feature(*s)
                        } else {
                            extensions.count(*s) == 0
                        }
                    })
                && geometry_clear(p)
        };
        // The physical opening and preferred side are invariant across its
        // candidate lanes. Prepare them once, rather than reading the feature
        // record again for every comparison.
        let opening = opening_linear
            .then(|| {
                items[i].measurements.iter().find_map(|m| {
                    analysis
                        .features
                        .iter()
                        .find(|f| f.id == m.feature)
                        .and_then(|f| crate::lint::rectangular_notches::read(analysis, f).ok())
                })
            })
            .flatten();
        let route_rank = |p: &Placed| {
            let length = |p: &Placed| {
                p.lines
                    .iter()
                    .map(|[a, b]| (a[0] - b[0]).hypot(a[1] - b[1]))
                    .sum::<f64>()
            };
            let outside_row = |p: &Placed| {
                centre_row
                    && (0.5 * (p.label_box[1] + p.label_box[3]) - v.at[1]).abs() >= super::TEXT
            };
            let opposite_opening = |p: &Placed| {
                let (Some(n), Shape::Linear { axis, .. }) = (&opening, &items[i].shape) else {
                    return false;
                };
                if *axis != n.wide {
                    return false;
                }
                let (at, mouth) = (frame.page(n.at), frame.page(n.mouth));
                let k = usize::from(super::placement::across(frame, *axis) == Some(true));
                let station = p.arrows.first().map_or(0., |a| a[0][k]);
                (station - mouth[k]) * (mouth[k] - at[k]) < 0.
            };
            (opposite_opening(p), outside_row(p), length(p))
        };
        let rank_order = |a: &(bool, bool, f64), b: &(bool, bool, f64)| {
            a.0.cmp(&b.0)
                .then_with(|| a.1.cmp(&b.1))
                .then_with(|| a.2.total_cmp(&b.2))
        };
        let route_order = |a: &Placed, b: &Placed| rank_order(&route_rank(a), &route_rank(b));
        if ordered_opening {
            if let Some(p) = first_clear_opening_lane(
                &candidates,
                page.drawable(),
                |p| {
                    let (side, row, _) = route_rank(p);
                    (side, row)
                },
                rank_order,
                clear,
            ) {
                for (j, position) in placed.iter_mut().enumerate() {
                    if movable[j] && position.as_ref().is_some_and(|q| conflict(&p, q)) {
                        *position = None;
                    }
                }
                placed[i] = Some(p);
                moved.insert(items[i].id.clone());
            }
            continue;
        }
        let baseline_clear = std::cell::Cell::new(false);
        let evaluated = candidates
            .into_iter()
            .enumerate()
            .filter_map(|(index, mut p)| {
                if index >= baseline_candidates && baseline_clear.get() {
                    return None;
                }
                if !opening_linear
                    && matches!(items[i].shape, Shape::Linear { .. })
                    && !p
                        .lines
                        .iter_mut()
                        .take(2)
                        .all(|s| f.interior_witness(s, &items[i].measurements))
                {
                    return None;
                }
                Some((index >= baseline_candidates, p))
            })
            .filter_map(|(fallback, p)| {
                let mut choices = Vec::new();
                if slot_layout
                    && items[i]
                        .measurements
                        .iter()
                        .any(|m| m.parameter == "slot_width.length")
                    && proof.blank(v, p.label_box)
                {
                    // Keep compact witness gaps within two paper millimetres.
                    for extra in [0.25, 0.5, 1.] {
                        let mut q = p.clone();
                        let mut clear = true;
                        for line in q.lines.iter_mut().take(2) {
                            let [a, b] = *line;
                            let n = (b[0] - a[0]).hypot(b[1] - a[1]);
                            if n <= extra + 1. {
                                clear = false;
                                break;
                            }
                            let next = [
                                a[0] + extra * (b[0] - a[0]) / n,
                                a[1] + extra * (b[1] - a[1]) / n,
                            ];
                            // Never skip an unrelated feature to manufacture clearance.
                            if extensions.count([a, next]) != 0 {
                                clear = false;
                                break;
                            }
                            line[0] = next;
                        }
                        if clear {
                            choices.push(q);
                        }
                    }
                }
                // For a fixed physical route, use the smallest valid gap.
                // Removing more witness ink must not improve its route score.
                let found = if choices.is_empty() {
                    clear(&p).then_some(p)
                } else {
                    choices.insert(0, p);
                    choices.into_iter().find(|p| clear(p))
                };
                found.map(|p| {
                    if !fallback {
                        baseline_clear.set(true);
                    }
                    (fallback, p)
                })
            });
        let candidate = evaluated
            .min_by(|(fallback_a, a), (fallback_b, b)| {
                fallback_a.cmp(fallback_b).then_with(|| route_order(a, b))
            })
            .map(|(_, p)| p);

        if let Some(p) = candidate {
            for (j, position) in placed.iter_mut().enumerate() {
                if movable[j] && position.as_ref().is_some_and(|q| conflict(&p, q)) {
                    *position = None;
                }
            }
            placed[i] = Some(p);
            moved.insert(items[i].id.clone());
        }
    }
    moved
}

/// Recheck local opening dimensions after the selected sheet's text and
/// leaders have settled. Only this family's linear items may move; every
/// other printed item, GD&T glyph and furniture stroke is an obstacle.
pub(crate) fn finish_openings(analysis: &Analysis, spec: &Spec, plan: &mut crate::model::Plan) {
    finish_openings_with(analysis, spec, plan, true);
}

/// Attachment can grow a frame after the first lane pass. Retry only when
/// that finished unit actually contacts the new opening dimensions.
pub(crate) fn openings_need_settling(plan: &crate::model::Plan) -> bool {
    let ids: BTreeSet<_> = plan
        .dimensions
        .iter()
        .filter(|d| {
            d.measurements
                .iter()
                .any(|m| m.feature.starts_with("f:rectangular_through_notch:"))
        })
        .map(|d| d.id.as_str())
        .collect();
    if ids.is_empty() {
        return false;
    }
    let contacts = super::contacts::contacts(plan);
    plan.problems.iter().chain(&contacts).any(|p| {
        matches!(
            p.code.as_str(),
            "annotation_overlap"
                | "annotation_ink_overlap"
                | "annotation_leader_crossing"
                | "label_crowded"
                | "extension_through_feature"
        ) && p.subjects.iter().any(|id| ids.contains(id.as_str()))
    })
}

/// Revisit lanes around the whole coupled hole-frame unit after attachment.
pub(crate) fn finish_attached_openings(
    analysis: &Analysis,
    spec: &Spec,
    plan: &mut crate::model::Plan,
) {
    finish_openings_with(analysis, spec, plan, false);
}

/// Settle free linear text against the completed inventory. The existing
/// own-span alternatives move only the label; measured ends, arrows, datum
/// symbols and callout/frame units stay fixed.
fn settle_linear_labels(
    analysis: &Analysis,
    spec: &Spec,
    plan: &mut crate::model::Plan,
) -> BTreeSet<String> {
    if !plan.inspection_balloons.is_empty() {
        return BTreeSet::new();
    }
    let crowded: BTreeSet<_> = super::contacts::contacts(plan)
        .into_iter()
        .filter(|p| p.code == "label_crowded")
        .flat_map(|p| p.subjects)
        .collect();
    if crowded.is_empty() {
        return BTreeSet::new();
    }
    let page = Page::new(analysis, &plan.sheet, &plan.views);
    let proofs = callout_proofs(analysis, &plan.views);
    let mut fixed = super::callout_routes::furniture(plan);
    fixed.extend(plan.gdt.iter().filter_map(super::gdt::proxy));
    let mut changed = BTreeSet::new();
    for i in 0..plan.dimensions.len() {
        let d = &plan.dimensions[i];
        if d.kind != "linear"
            || !crowded.contains(&d.id)
            || spec
                .dimensions
                .get(&d.id)
                .is_some_and(|s| s.place.is_some())
        {
            continue;
        }
        let (Some(old), Some(view)) = (
            d.placed.as_ref(),
            plan.views.iter().find(|v| v.id == d.view),
        ) else {
            continue;
        };
        if old.arrows.len() != 2
            || old.lines.len() < 3
            || !(old.rotation == 0. || old.rotation.abs() == 90.)
        {
            continue;
        }
        let Some(proof) = proofs.get(&view.id) else {
            continue;
        };
        let k = usize::from(old.rotation != 0.);
        let lo = old
            .arrows
            .iter()
            .map(|a| a[0][k])
            .fold(f64::INFINITY, f64::min);
        let hi = old
            .arrows
            .iter()
            .map(|a| a[0][k])
            .fold(f64::NEG_INFINITY, f64::max);
        let others: Vec<_> = plan
            .dimensions
            .iter()
            .enumerate()
            .filter(|(j, _)| *j != i)
            .map(|(_, q)| q)
            .chain(&fixed)
            .filter_map(|q| q.placed.as_ref())
            .collect();
        let next = super::label_lanes::alternatives_placed(old, page.drawable(), &others)
            .take(64)
            .find(|p| {
                p.lines == old.lines
                    && p.arrows == old.arrows
                    && p.label_box[k] >= lo
                    && p.label_box[k + 2] <= hi
                    && page.fits_interior(p, view.id.trim_start_matches("v:"))
                    && (proof.blank(view, p.label_box)
                        || proof.exterior_clear(view, p.label_box, &[], &[]))
                    && others.iter().all(|q| {
                        !text_conflict(p, q)
                            && !super::placement::ink_through(
                                q,
                                super::contacts::clear_box(p.label_box),
                            )
                            && !super::placement::ink_through(
                                p,
                                super::contacts::clear_box(q.label_box),
                            )
                    })
            });
        if let Some(next) = next
            && next != *old
        {
            let id = d.id.clone();
            plan.dimensions[i].placed = Some(next);
            changed.insert(id);
        }
    }
    changed
}

fn finish_openings_with(
    analysis: &Analysis,
    spec: &Spec,
    plan: &mut crate::model::Plan,
    yield_callouts: bool,
) {
    if crate::nominal::comparison()
        || !plan.dimensions.iter().any(|d| {
            d.measurements
                .iter()
                .any(|m| m.feature.starts_with("f:rectangular_through_notch:"))
        })
    {
        return;
    }
    let wants = super::wants(analysis).0;
    let mut indices = Vec::new();
    let mut items = Vec::new();
    let mut placed = Vec::new();
    for (i, d) in plan.dimensions.iter().enumerate() {
        if let Some(w) = wants.iter().find(|w| w.id == d.id) {
            let mut w = w.clone();
            w.terms.clone_from(&d.terms);
            w.measurements.clone_from(&d.measurements);
            indices.push(i);
            items.push(w);
            placed.push(d.placed.clone());
        }
    }
    let chosen: Vec<_> = items
        .iter()
        .zip(&indices)
        .map(|(w, &i)| {
            (through_notch(w)
                && matches!(w.shape, Shape::Linear { .. })
                && plan.dimensions[i].placed.is_some()
                && plan.views.iter().any(|v| v.id == plan.dimensions[i].view))
            .then(|| {
                super::NAMES
                    .iter()
                    .copied()
                    .find(|name| plan.dimensions[i].view == format!("v:{name}"))
            })
            .flatten()
        })
        .collect();
    let frames = plan
        .views
        .iter()
        .filter_map(|v| {
            let name = v.id.strip_prefix("v:")?;
            super::NAMES
                .contains(&name)
                .then(|| (name, Frame::new(analysis, v)))
        })
        .collect();
    let (sides, _) = super::placement::lanes(
        analysis,
        &frames,
        items.iter().zip(chosen.iter().copied()),
        &vec![None; items.len()],
    );
    let mut fixed = super::callout_routes::furniture(plan);
    fixed.extend(plan.gdt.iter().filter_map(super::gdt::proxy));
    fixed.extend(
        plan.dimensions
            .iter()
            .enumerate()
            .filter(|(i, _)| !indices.contains(i))
            .map(|(_, d)| d.clone()),
    );
    let cache = super::footprints::Cache::default();
    let before = super::recheck_cached(analysis, plan, &cache);
    let mut changed = place(
        analysis,
        spec,
        &plan.views,
        &frames,
        &items,
        &chosen,
        &sides,
        &mut placed,
        &Page::new(analysis, &plan.sheet, &plan.views),
        &fixed,
        &cache,
        yield_callouts,
        true,
    );
    let proofs = callout_proofs(analysis, &plan.views);
    // Outside text needs the usual one millimetre past its arrowhead, not
    // the legacy three. Settle this against the selected drawing only, so
    // a local text repair cannot change the paper/scale search.
    let page = Page::new(analysis, &plan.sheet, &plan.views);
    for i in 0..items.len() {
        if !through_notch(&items[i])
            || spec
                .dimensions
                .get(&items[i].id)
                .and_then(|d| d.place.as_ref())
                .is_some_and(|p| !crate::placement_intent::automatic_pin(p))
        {
            continue;
        }
        let (Some(name), Some(old)) = (chosen[i], placed[i].as_ref()) else {
            continue;
        };
        let Some(view) = plan.views.iter().find(|v| v.id == format!("v:{name}")) else {
            continue;
        };
        let Some(proof) = proofs.get(&view.id) else {
            continue;
        };
        let k = usize::from(old.rotation != 0.);
        let mut candidates = std::iter::once(old.clone())
            .chain(super::placement::slides(old, page.drawable()).take(16));
        let found = candidates.find_map(|mut p| {
            let lo = p
                .arrows
                .iter()
                .map(|a| a[0][k])
                .fold(f64::INFINITY, f64::min);
            let hi = p
                .arrows
                .iter()
                .map(|a| a[0][k])
                .fold(f64::NEG_INFINITY, f64::max);
            let gap = super::ARROW + 1.;
            let delta = if p.label_box[k] > hi + gap {
                hi + gap - p.label_box[k]
            } else if p.label_box[k + 2] < lo - gap {
                lo - gap - p.label_box[k + 2]
            } else {
                return None;
            };
            p.label_at[k] += delta;
            p.label_box[k] += delta;
            p.label_box[k + 2] += delta;
            let peers = || {
                placed
                    .iter()
                    .enumerate()
                    .filter(|(j, _)| *j != i)
                    .filter_map(|(_, p)| p.as_ref())
                    .chain(fixed.iter().filter_map(|d| d.placed.as_ref()))
            };
            let clear = page.fits_interior(&p, name)
                && (proof.opening_linear_clear(view, &p)
                    || !boxes_overlap(p.label_box, frames[name].outline()))
                && peers().all(|q| {
                    !text_conflict(&p, q)
                        && !super::placement::ink_through(
                            &p,
                            super::contacts::clear_box(q.label_box),
                        )
                        && !super::placement::ink_through(
                            q,
                            super::contacts::clear_box(p.label_box),
                        )
                })
                && !super::label_lanes::off_span(
                    &p,
                    placed
                        .iter()
                        .enumerate()
                        .filter(|(j, _)| {
                            *j != i
                                && matches!(items[*j].shape, Shape::Linear { .. })
                                && plan.dimensions[indices[*j]].view == view.id
                        })
                        .filter_map(|(_, p)| p.as_ref()),
                );
            clear.then_some(p)
        });
        drop(candidates);
        if let Some(p) = found {
            placed[i] = Some(p);
            changed.insert(items[i].id.clone());
        }
    }
    // A V-notch's angle is a bounded profile corner too. Linear witnesses
    // can settle after its first placement; retry only contacted labels on
    // the final inventory, preserving pins and every stated requirement.
    let mut angle_views = vec![None; items.len()];
    let mut angles = Vec::new();
    for i in 0..items.len() {
        if !(super::profile_angles::is_profile_angle(&items[i])
            || items[i]
                .measurements
                .iter()
                .any(|m| m.parameter == "notch.angle"))
            || spec
                .dimensions
                .get(&items[i].id)
                .is_some_and(|d| d.place.is_some())
        {
            continue;
        }
        let Some(p) = placed[i].as_ref() else {
            continue;
        };
        let contacted = placed
            .iter()
            .enumerate()
            .filter(|(j, _)| *j != i)
            .filter_map(|(_, q)| q.as_ref())
            .chain(fixed.iter().filter_map(|d| d.placed.as_ref()))
            .any(|q| {
                text_conflict(p, q)
                    || super::placement::ink_through(q, super::contacts::clear_box(p.label_box))
            });
        if !contacted {
            continue;
        }
        angle_views[i] = super::NAMES.iter().copied().find(|name| {
            plan.dimensions[indices[i]].view == format!("v:{name}") && frames.contains_key(name)
        });
        if angle_views[i].is_some() {
            angles.push((i, placed[i].take().expect("placed angle")));
        }
    }
    let angle_proofs = super::interior::proofs(analysis, &plan.views);
    super::angular::place_profile(
        &frames,
        &items,
        &angle_views,
        &fixed,
        &mut placed,
        &page,
        &plan.views,
        &angle_proofs,
    );
    for (i, old) in angles {
        if placed[i].is_none() {
            placed[i] = Some(old);
        } else {
            changed.insert(items[i].id.clone());
        }
    }
    for ((&i, w), p) in indices.iter().zip(&items).zip(placed) {
        if !changed.contains(&w.id) {
            continue;
        }
        let d = &mut plan.dimensions[i];
        d.region = p
            .as_ref()
            .filter(|p| {
                plan.views.iter().find(|v| v.id == d.view).is_some_and(|v| {
                    if through_notch(w) && matches!(w.shape, Shape::Linear { .. }) {
                        proofs
                            .get(&v.id)
                            .is_some_and(|proof| proof.opening_linear_clear(v, p))
                    } else {
                        angle_proofs
                            .get(&v.id)
                            .is_some_and(|proof| proof.annotation_clear(v, p, false))
                    }
                })
            })
            .map(|_| super::CandidateRegion::Interior);
        d.placed = p;
    }
    let labels = settle_linear_labels(analysis, spec, plan);
    if !labels.is_empty() {
        changed.extend(labels);
        super::contacts::report(analysis, plan);
    }
    let after = super::recheck_cached(analysis, plan, &cache);
    let codes = before
        .iter()
        .chain(&after)
        .map(|p| p.code.clone())
        .collect();
    let mut all = plan.dimensions.clone();
    all.extend(plan.gdt.iter().filter_map(super::gdt::proxy));
    super::callout_routes::refresh(analysis, plan, &all, &cache, &changed, &codes);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::projected_ink::{CurvedFace, Cylinder, EdgeInk, PlanarFace};

    fn view() -> PlannedView {
        serde_json::from_value(serde_json::json!({
            "id": "v:plan", "direction": [0,0,1], "up": [0,1,0],
            "at": [0,0], "scale": 1, "area": [-50,-50,50,50],
            "state": {"kind": "shown"}, "default": true, "basis": "test"
        }))
        .unwrap()
    }

    /// A 100 mm square plate face seen in plan, plus further edges as polylines at z = 0.
    fn ink(extra: Vec<Vec<P2>>) -> EdgeInk {
        let square = [[-50., -50.], [50., -50.], [50., 50.], [-50., 50.]];
        let mut edges: Vec<Vec<[f64; 3]>> = (0..4)
            .map(|i| {
                let (a, b) = (square[i], square[(i + 1) % 4]);
                vec![[a[0], a[1], 0.], [b[0], b[1], 0.]]
            })
            .collect();
        edges.extend(
            extra
                .into_iter()
                .map(|e| e.into_iter().map(|p| [p[0], p[1], 0.]).collect()),
        );
        EdgeInk {
            resolved: Vec::new(),
            references: Default::default(),
            edges,
            planes: vec![PlanarFace {
                normal: [0., 0., 1.],
                edges: vec![0, 1, 2, 3],
            }],
            curved: vec![],
        }
    }

    fn proof(ink: &EdgeInk) -> Proof {
        build(ink, &view(), [0., 0.]).unwrap()
    }

    #[test]
    fn a_neighbouring_edge_is_not_forgiven_by_the_attachment_collar() {
        let p = Placed {
            lines: vec![[[0., 0.], [15., 0.]], [[15., 0.], [18., 0.]]],
            arrows: vec![arrow([0., 0.], [15., 0.])],
            label_at: [19., -1.1],
            label_box: [19., -1.1, 29., 1.2],
            rotation: 0.,
        };
        let blank = proof(&ink(vec![vec![[0., -10.], [0., 10.]]]));
        assert!(blank.leader_clear(&view(), &p, true));
        let blocked = proof(&ink(vec![
            vec![[0., -10.], [0., 10.]],
            vec![[0.3, -10.], [1.3, 10.]],
        ]));
        assert!(!blocked.leader_clear(&view(), &p, true));
        // An outline parallel to the tip's own edge (a chamfer band around a hex's flats) must
        // be crossed by any leader leaving the tip; crossing it squarely is forgiven.
        let banded = proof(&ink(vec![
            vec![[0., -10.], [0., 10.]],
            vec![[1., -10.], [1., 10.]],
        ]));
        assert!(banded.leader_clear(&view(), &p, true));
    }

    #[test]
    fn a_frame_keeps_its_face_site_and_never_crosses_an_edge() {
        let p = Placed {
            lines: vec![[[0., 0.], [15., 10.]], [[15., 10.], [18., 10.]]],
            arrows: vec![arrow([0., 0.], [15., 10.])],
            label_at: [19., 8.8],
            label_box: [19., 8.8, 29., 11.2],
            rotation: 0.,
        };
        let blank = proof(&ink(vec![]));
        assert!(blank.leader_clear(&view(), &p, false));
        assert!(
            !blank.leader_clear(&view(), &p, true),
            "a size callout needs a rim"
        );
        let blocked = proof(&ink(vec![vec![[8., -40.], [8., 40.]]]));
        assert!(
            !blocked.leader_clear(&view(), &p, false),
            "the frame shaft crosses view ink"
        );
    }

    #[test]
    fn a_turned_part_proves_exterior_paper_without_a_planar_interior() {
        let mut ink = EdgeInk {
            resolved: Vec::new(),
            references: Default::default(),
            edges: vec![],
            planes: vec![],
            curved: vec![CurvedFace {
                bounds: [[0.0, -5.0, -5.0], [20.0, 5.0, 5.0]],
                cylinder: Some(Cylinder {
                    origin: [0.0; 3],
                    axis: [1.0, 0.0, 0.0],
                    radius: 5.0,
                }),
            }],
        };
        let v = view();
        let p = build_camera_with_exterior(&ink, v.direction, v.up, [0.0; 2], true).unwrap();
        let label = [3.0, 8.0, 9.0, 14.0];
        assert!(
            !p.blank(&v, label),
            "no planar face can carry an interior label"
        );
        assert!(p.exterior_clear(&v, label, &[[[4.0, 6.5], [4.0, 8.0]]], &[]));
        assert!(
            !p.exterior_clear(&v, [3.0, -1.0, 9.0, 1.0], &[], &[]),
            "empty line-work cannot excuse a box inside the cylinder"
        );
        assert!(
            !p.exterior_clear(&v, label, &[[[4.0, -1.0], [4.0, 8.0]]], &[]),
            "the shaft must also stay off the cylinder's silhouette"
        );
        ink.curved.clear();
        assert!(
            build_camera_with_exterior(&ink, v.direction, v.up, [0.0; 2], true).is_none(),
            "missing surface evidence fails closed"
        );
    }

    fn semicircle() -> Vec<P2> {
        (0..=32)
            .map(|i| {
                let t = std::f64::consts::PI * i as f64 / 32.0;
                [5. * t.cos(), 5. * t.sin()]
            })
            .collect()
    }

    fn dimension() -> Placed {
        Placed {
            lines: vec![
                [[-10., -10.], [-10., 0.]],
                [[10., -10.], [10., 0.]],
                [[-10., 0.], [10., 0.]],
            ],
            arrows: vec![[[-10., 0.], [-8., 0.]], [[10., 0.], [8., 0.]]],
            label_box: [-3., 2., 3., 5.],
            label_at: [-3., 2.],
            rotation: 0.,
        }
    }

    #[test]
    fn opening_linear_tips_forgive_only_their_actual_supports() {
        let p = dimension();
        let supports = vec![
            vec![[-10., -20.], [-10., 20.]],
            vec![[10., -20.], [10., 20.]],
        ];
        assert!(proof(&ink(supports.clone())).opening_linear_clear(&view(), &p));
        let mut blocked = supports;
        blocked.push(vec![[-9.9, -1.], [-9.7, 1.]]);
        assert!(
            !proof(&ink(blocked)).opening_linear_clear(&view(), &p),
            "the 0.6 mm attachment collar must not forgive a neighbouring edge"
        );
    }

    #[test]
    fn blank_label_does_not_excuse_dimension_line_or_arrow_on_ink() {
        let v = view();
        let p = dimension();
        assert!(proof(&ink(vec![])).annotation_clear(&v, &p, false));
        for line in [
            [[-20., 0.], [20., 0.]],
            [[-9., -1.], [-9., 1.]],
            [[0., -1.], [0., 1.]],
        ] {
            let proof = proof(&ink(vec![line.to_vec()]));
            assert!(proof.blank(&v, p.label_box));
            assert!(!proof.annotation_clear(&v, &p, false));
        }
    }

    #[test]
    fn a_label_over_an_edge_or_off_the_face_is_not_blank() {
        let v = view();
        let label = [-3., 2., 3., 5.];
        assert!(proof(&ink(vec![])).blank(&v, label));
        // An edge through it, or within the half-millimetre gap of it.
        assert!(!proof(&ink(vec![vec![[0., -10.], [0., 10.]]])).blank(&v, label));
        assert!(!proof(&ink(vec![vec![[-10., 5.3], [10., 5.3]]])).blank(&v, label));
        // A hole loop around it: the face's even/odd containment excludes the hole; a callout
        // proof also takes the opening the face surrounds as blank paper (a label in a slot).
        let hole = vec![[-6., 0.], [6., 0.], [6., 8.], [-6., 8.], [-6., 0.]];
        let mut holed = ink(vec![hole]);
        holed.planes[0].edges.push(4);
        assert!(!proof(&holed).blank(&v, label));
        let mut callout = proof(&holed);
        callout.voids = true;
        assert!(callout.blank(&v, label));
        assert!(
            !callout.blank(&v, [-3., 6., 3., 9.]),
            "across the opening's edge"
        );
        assert!(!callout.blank(&v, [60., 2., 66., 5.]), "off the plate");
        // Off the plate entirely.
        assert!(!proof(&ink(vec![])).blank(&v, [60., 2., 66., 5.]));
        // No face square to the view proves nothing.
        let mut tilted = ink(vec![]);
        tilted.planes[0].normal = [0., 1., 1.];
        assert!(build(&tilted, &v, [0., 0.]).is_none());
    }

    #[test]
    fn exterior_notch_space_excludes_every_projected_face_and_curved_cover() {
        let v = view();
        let p = dimension();
        let hole = vec![
            [-20., -20.],
            [20., -20.],
            [20., 20.],
            [-20., 20.],
            [-20., -20.],
        ];
        let mut recessed = ink(vec![hole]);
        recessed.planes[0].edges.push(4);
        assert!(proof(&recessed).exterior_annotation_clear(&v, &p));
        assert!(!proof(&ink(vec![])).exterior_annotation_clear(&v, &p));
        // Another face can cover the recess without crossing the annotation ink.
        // Oblique faces count too, even though they cannot prove face-interior labels.
        let mut covered = recessed.clone();
        covered.planes.push(PlanarFace {
            normal: [0., 1., 1.],
            edges: vec![4],
        });
        assert!(!proof(&covered).exterior_annotation_clear(&v, &p));
        // End-on cylinders have no silhouette through their centre; their full
        // projected surface bounds must nevertheless exclude exterior authority.
        recessed.curved.push(CurvedFace {
            bounds: [[-20., -20., -5.], [20., 20., 0.]],
            cylinder: Some(Cylinder {
                origin: [0., 0., 0.],
                axis: [0., 0., 1.],
                radius: 20.,
            }),
        });
        assert!(!proof(&recessed).exterior_annotation_clear(&v, &p));
    }

    #[test]
    fn curved_faces_bound_their_silhouettes() {
        let v = view();
        let label = [-3., 2., 3., 5.];
        // A sphere or torus may outline anywhere in its box.
        let mut round = ink(vec![]);
        round.curved.push(CurvedFace {
            bounds: [[-1., 0., -5.], [1., 10., 0.]],
            cylinder: None,
        });
        assert!(!proof(&round).blank(&v, label));
        // A cylinder seen end-on outlines only at its rims (edges).
        let mut bore = ink(vec![]);
        bore.curved.push(CurvedFace {
            bounds: [[-10., -10., -5.], [10., 10., 0.]],
            cylinder: Some(Cylinder {
                origin: [0., 0., 0.],
                axis: [0., 0., 1.],
                radius: 10.,
            }),
        });
        assert!(proof(&bore).blank(&v, label));
        // Seen square to its axis, at its generators ± radius: one runs through the label.
        let mut side = ink(vec![]);
        side.curved.push(CurvedFace {
            bounds: [[-20., -0.5, -7.], [20., 3.5, -3.]],
            cylinder: Some(Cylinder {
                origin: [0., 1.5, -5.],
                axis: [1., 0., 0.],
                radius: 2.,
            }),
        });
        let p = proof(&side);
        assert!(!p.blank(&v, label));
        assert!(p.blank(&v, [-3., 6., 3., 9.]));
    }

    #[test]
    fn a_silhouette_bound_cannot_prove_a_physical_attachment() {
        let mut evidence = ink(vec![]);
        evidence.curved.push(CurvedFace {
            bounds: [[-10., -2., -2.], [10., 2., 2.]],
            cylinder: Some(Cylinder {
                origin: [0., 0., 0.],
                axis: [1., 0., 0.],
                radius: 2.,
            }),
        });
        let p = proof(&evidence);
        assert!(!p.touches(&view(), [0., 2.]));
        assert!(p.touches(&view(), [-50., 0.]));
        assert!(!p.blank(&view(), [-3., 1., 3., 3.]));
    }

    #[test]
    fn an_edge_behind_a_front_face_is_not_a_visible_attachment() {
        let mut hidden = ink(vec![semicircle()]);
        for p in &mut hidden.edges[4] {
            p[2] = -5.0;
        }
        assert!(!proof(&hidden).touches(&view(), [0., 5.]));
        assert!(proof(&ink(vec![semicircle()])).touches(&view(), [0., 5.]));
        assert!(proof(&hidden).touches(&view(), [-50., 0.]));
    }

    #[test]
    fn ranked_lane_views_match_exhaustive_placed_candidates() {
        let a = Placed {
            lines: vec![
                [[0., -5.], [0., 15.]],
                [[20., -5.], [20., 15.]],
                [[0., 10.], [20., 10.]],
            ],
            arrows: vec![arrow([0., 10.], [1., 10.]), arrow([20., 10.], [19., 10.])],
            label_at: [8., 10.],
            label_box: [8., 10., 12., 12.],
            rotation: 0.,
        };
        let mut b = a.clone();
        for q in b.lines.iter_mut().chain(&mut b.arrows).flatten() {
            q[0] += 12.;
            q[1] += 7.;
        }
        b.label_at = [44., 17.];
        b.label_box = [44., 17., 48., 19.];
        let bases = [a, b];
        let drawable = [-100., -100., 100., 100.];
        let rank = |p: &Placed| {
            (
                p.arrows[0][0][0] > 10.,
                p.rotation != 0.,
                p.lines
                    .iter()
                    .map(|[a, b]| (a[0] - b[0]).hypot(a[1] - b[1]))
                    .sum::<f64>(),
            )
        };
        let order = |a: &(bool, bool, f64), b: &(bool, bool, f64)| {
            a.0.cmp(&b.0)
                .then_with(|| a.1.cmp(&b.1))
                .then_with(|| a.2.total_cmp(&b.2))
        };
        for case in 0..5 {
            let clear = |p: &Placed| match case {
                0 => true,
                1 => p.label_box[0] >= 10.,
                2 => p.label_box[2] < 10.,
                3 => p.label_box[1] > 16.,
                _ => false,
            };
            let expected = bases
                .iter()
                .flat_map(|p| {
                    std::iter::once(p.clone())
                        .chain(super::super::placement::slides(p, drawable).take(16))
                })
                .filter(&clear)
                .min_by(|a, b| order(&rank(a), &rank(b)));
            assert_eq!(
                first_clear_opening_lane(
                    &bases,
                    drawable,
                    |p| {
                        let (side, row, _) = rank(p);
                        (side, row)
                    },
                    order,
                    clear
                ),
                expected
            );
        }
    }

    #[test]
    fn prepared_attachment_bands_keep_each_rays_angle_and_scale() {
        let p = proof(&ink(vec![
            vec![[0., -10.], [0., 10.]],
            vec![[2., -10.], [2., 10.]],
        ]));
        let v = view();
        let across = [[0., 0.], [10., 0.]];
        let band = p.banded(&v, across).unwrap();
        assert!(band.contains(&4) && band.contains(&5));
        assert!(p.banded(&v, [[0., 0.], [0., 10.]]).is_none());
        assert!(p.banded(&v, across).unwrap().contains(&5));
        let mut enlarged = v;
        enlarged.scale = 2.;
        let band = p.banded(&enlarged, across).unwrap();
        assert!(band.contains(&4));
        assert!(
            !band.contains(&5),
            "the three-paper-mm band shrinks in model units"
        );
    }

    #[test]
    fn cloned_attachment_proofs_recheck_changed_depth_and_visibility() {
        let original = proof(&ink(vec![semicircle()]));
        assert!(original.touches(&view(), [0., 5.]));
        let mut behind = original.clone();
        // The first four chords are the front face's outline. Move only
        // the circular edge behind it while retaining its projection.
        for depth in behind.depths.iter_mut().skip(4) {
            *depth = [-5.; 2];
        }
        assert!(!behind.touches(&view(), [0., 5.]));
        assert!(behind.hidden_tip(&view(), [0., 5.]));
        assert!(original.touches(&view(), [0., 5.]));
    }

    #[test]
    fn a_tip_on_an_edge_behind_a_front_face_is_hidden() {
        let mut hidden = ink(vec![semicircle()]);
        for p in &mut hidden.edges[4] {
            p[2] = -5.0;
        }
        let behind = proof(&hidden);
        assert!(behind.hidden_tip(&view(), [0., 5.]));
        // On the face's own outline, or on no edge at all, it is not.
        assert!(!behind.hidden_tip(&view(), [-50., 0.]));
        assert!(!behind.hidden_tip(&view(), [0., 20.]));
        assert!(!proof(&ink(vec![semicircle()])).hidden_tip(&view(), [0., 5.]));
    }

    #[test]
    fn crossed_lines_count_each_printed_line_once() {
        // Three vertical lines, the middle one twice (an edge seen end-on over another), and a
        // polyline whose vertex the segment passes through.
        let vertical = |x: f64| vec![[x, -10.], [x, 10.]];
        let p = proof(&ink(vec![
            vertical(-20.),
            vertical(0.),
            vertical(0.),
            vertical(20.),
            vec![[30., -10.], [30., 0.], [30., 10.]],
        ]));
        assert_eq!(p.lines_crossed(&view(), [[-40., 0.], [40., 0.]]), 4);
        // The plate's outline counts too; a line the segment only reaches is not crossed.
        assert_eq!(p.lines_crossed(&view(), [[-60., 5.], [-20., 5.]]), 1);
        assert_eq!(p.lines_crossed(&view(), [[-10., 20.], [10., 20.]]), 0);
    }

    #[test]
    fn partial_arc_tip_and_complete_leader_must_clear_ink() {
        let v = view();
        let p = Placed {
            lines: vec![[[0., 5.], [0., 12.]], [[0., 12.], [5., 12.]]],
            arrows: vec![[[0., 5.], [0., 7.]]],
            label_box: [6., 11., 12., 14.],
            label_at: [6., 11.],
            rotation: 0.,
        };
        assert!(proof(&ink(vec![semicircle()])).annotation_clear(&v, &p, true));
        assert!(!proof(&ink(vec![semicircle()])).touches(&v, [0., -5.]));
        for line in [[[-2., 9.], [2., 9.]], [[3., 10.], [3., 15.]]] {
            let proof = proof(&ink(vec![semicircle(), line.to_vec()]));
            assert!(proof.blank(&v, p.label_box));
            assert!(!proof.annotation_clear(&v, &p, true));
        }
        let mut void = p;
        void.lines[0][1] = [0., 60.];
        assert!(!proof(&ink(vec![semicircle()])).annotation_clear(&v, &void, true));
    }

    #[test]
    fn failed_exterior_corridors_retry_the_whole_linear_dimension_inside() {
        let file = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/parts/mounting_plate.step");
        let mut a = crate::model::analyse_native(&file).unwrap().1;
        let sheet = crate::model::plan(&a, &Spec::default()).unwrap().sheet;
        a.envelope = [[-50., -50., -1.], [50., 50., 1.]];
        a.features.clear();
        a.principal_profiles.clear();
        a.edge_ink = Some(ink(vec![]));
        let mut v = view();
        v.at = [150., 150.];
        v.area = [80., 80., 220., 220.];
        let views = [v];
        let frame = Frame::new(&a, &views[0]);
        let frames = BTreeMap::from([("plan", frame)]);
        let w = Want {
            id: "d:test/location.length.x".into(),
            views: vec!["plan"],
            value: 20.,
            terms: vec!["20".into()],
            measurements: vec![],
            basis: "test".into(),
            turning: None,
            shape: Shape::Linear {
                a: [-10., -20., 0.],
                b: [10., -20., 0.],
                axis: 0,
                a_edge: false,
                b_edge: false,
                order: 1,
            },
        };
        let mut old = dimension();
        old.label_box = [10000., 10000., 10006., 10003.];
        let page = Page::new(&a, &sheet, &views);
        assert!(!page.fits(&old, "plan"));
        let mut placed = [Some(old)];
        let moved = place(
            &a,
            &Spec::default(),
            &views,
            &frames,
            &[w],
            &[Some("plan")],
            &[Some(Side::Above)],
            &mut placed,
            &page,
            &[],
            &super::super::footprints::Cache::default(),
            false,
            false,
        );
        assert!(moved.contains("d:test/location.length.x"));
        let p = placed[0].as_ref().unwrap();
        assert_eq!(p.lines.len(), 3);
        assert_eq!(p.arrows.len(), 2);
        assert!(page.fits_interior(p, "plan"));
        assert!(proofs(&a, &views)["v:plan"].annotation_clear(&views[0], p, false));
    }

    #[test]
    fn final_sweep_chooses_blank_face_when_every_exterior_route_crosses() {
        let file = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/parts/mounting_plate.step");
        let mut a = crate::model::analyse_native(&file).unwrap().1;
        let sheet = crate::model::plan(&a, &Spec::default()).unwrap().sheet;
        a.envelope = [[-50., -50., -1.], [50., 50., 1.]];
        a.features.clear();
        a.principal_profiles.clear();
        a.edge_ink = Some(ink(vec![vec![[0., -5.], [0., 5.]]]));
        let mut v = view();
        v.at = [150., 150.];
        v.area = [70., 70., 230., 230.];
        let d: PlannedDimension = serde_json::from_value(serde_json::json!({
            "id":"d:test", "kind":"callout", "view":"v:plan", "value":6.,
            "label":"M6", "terms":["M6"], "measurements":[], "default":true,
            "basis":"test", "state":{"kind":"shown"},
            "placed": {"lines":[[[150.,150.],[90.,145.]],[[90.,145.],[87.,145.]]],
                "arrows":[[[150.,150.],[147.,149.75]]], "label_at":[80.,143.],
                "label_box":[80.,143.,86.,146.], "rotation":0.}
        }))
        .unwrap();
        let mut blocker = d.clone();
        blocker.id = "barrier".into();
        let p = blocker.placed.as_mut().unwrap();
        let square = [[100., 100.], [200., 100.], [200., 200.], [100., 200.]];
        p.lines = (0..4)
            .map(|i| {
                let a = square[i];
                let b = square[(i + 1) % 4];
                let u = [(b[0] - a[0]) / 100., (b[1] - a[1]) / 100.];
                [[a[0] - u[0], a[1] - u[1]], [b[0] + u[0], b[1] + u[1]]]
            })
            .collect();
        p.arrows.clear();
        p.label_box = [220., 220., 225., 223.];
        let views = [v];
        let proofs = proofs(&a, &views);
        let routes = super::super::leaders::settled_routes(
            &a,
            &sheet,
            &views,
            &d,
            None,
            &[blocker],
            Some(&proofs["v:plan"]),
        );
        assert!(!routes.is_empty());
        let next = &routes[0];
        assert!(proofs["v:plan"].annotation_clear(&views[0], next, true));
        assert!(
            next.lines
                .iter()
                .map(|[a, b]| (a[0] - b[0]).hypot(a[1] - b[1]))
                .sum::<f64>()
                < 40.
        );
        assert_eq!(next.rotation, 0.);
    }

    #[test]
    fn an_inward_hole_leader_keeps_its_tip_on_the_rim_over_a_proven_floor() {
        let file = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/parts/mounting_plate.step");
        let mut a = crate::model::analyse_native(&file).unwrap().1;
        let sheet = crate::model::plan(&a, &Spec::default()).unwrap().sheet;
        a.envelope = [[-50., -50., -2.], [50., 50., 0.]];
        a.features.clear();
        a.principal_profiles.clear();
        let circle: Vec<P2> = (0..=128)
            .map(|i| {
                let t = std::f64::consts::TAU * i as f64 / 128.;
                [30. * t.cos(), 30. * t.sin()]
            })
            .collect();
        let mut drawing_ink = ink(vec![circle.clone()]);
        drawing_ink.planes[0].edges.push(4);
        drawing_ink
            .edges
            .push(circle.iter().map(|p| [p[0], p[1], -1.]).collect());
        drawing_ink.planes.push(PlanarFace {
            normal: [0., 0., 1.],
            edges: vec![5],
        });
        a.edge_ink = Some(drawing_ink);
        let mut v = view();
        v.at = [150., 150.];
        v.area = [70., 70., 230., 230.];
        let views = [v];
        let d: PlannedDimension = serde_json::from_value(serde_json::json!({
            "id":"d:test/bore.diameter", "kind":"callout", "view":"v:plan", "value":60.,
            "label":"⌀60", "terms":["⌀60"], "measurements":[{"feature":"test","parameter":"bore.diameter","value":60.}],
            "default":true, "basis":"test", "state":{"kind":"shown"},
            "placed":{"lines":[[[180.,150.],[210.,150.]],[[210.,150.],[213.,150.]]],
                "arrows":[[[180.,150.],[182.7,150.]]], "label_at":[214.,149.],
                "label_box":[214.,149.,221.,152.], "rotation":0.}
        })).unwrap();
        let w = Want {
            id: d.id.clone(),
            views: vec!["plan"],
            value: 60.,
            terms: d.terms.clone(),
            measurements: d.measurements.clone(),
            basis: "test".into(),
            turning: None,
            shape: Shape::Callout {
                sites: vec![([0., 0., 0.], 30.)],
                arcs: Vec::new(),
            },
        };
        let mut barrier = d.clone();
        barrier.id = "barrier".into();
        barrier.kind = "ink".into();
        let p = barrier.placed.as_mut().unwrap();
        p.lines = (0..128)
            .map(|i| {
                let q = |i: usize| {
                    let t = std::f64::consts::TAU * i as f64 / 128.;
                    [150. + 31. * t.cos(), 150. + 31. * t.sin()]
                };
                let a = q(i);
                let b = q(i + 1);
                let e = [0.05 * (b[0] - a[0]), 0.05 * (b[1] - a[1])];
                [[a[0] - e[0], a[1] - e[1]], [b[0] + e[0], b[1] + e[1]]]
            })
            .collect();
        p.arrows.clear();
        p.label_box = [240., 240., 245., 243.];
        let proofs = proofs(&a, &views);
        let routes = super::super::leaders::settled_routes(
            &a,
            &sheet,
            &views,
            &d,
            Some(&w),
            &[barrier],
            Some(&proofs["v:plan"]),
        );
        assert!(!routes.is_empty(), "the hole floor has room for the label");
        let p = &routes[0];
        let [tip, elbow] = p.lines[0];
        assert!(((tip[0] - 150.).hypot(tip[1] - 150.) - 30.).abs() < 1e-6);
        assert!((tip[0] - 150.) * (elbow[0] - tip[0]) + (tip[1] - 150.) * (elbow[1] - tip[1]) < 0.);
        assert!(proofs["v:plan"].annotation_clear(&views[0], p, true));
        assert_eq!(p.rotation, 0.);
    }

    #[test]
    fn a_blocked_centre_row_keeps_safe_interior_alternatives() {
        let file = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/parts/mounting_plate.step");
        let mut a = crate::model::analyse_native(&file).unwrap().1;
        a.envelope = [[-50., -50., -1.], [50., 50., 1.]];
        let v = view();
        let frame = Frame::new(&a, &v);
        let circle: Vec<P2> = (0..=64)
            .map(|i| {
                let t = std::f64::consts::TAU * i as f64 / 64.0;
                [5. * t.cos(), 5. * t.sin()]
            })
            .collect();
        let drawing_ink = ink(vec![
            circle,
            vec![[5., 0.], [49., 0.]],
            vec![[-49., 0.], [-5., 0.]],
        ]);
        let proof = proof(&drawing_ink);
        let w = Want {
            id: "d:test/bore.diameter".into(),
            views: vec!["plan"],
            value: 10.,
            terms: vec!["⌀10 THRU".into()],
            measurements: vec![super::super::Measurement {
                feature: "test".into(),
                parameter: "bore.diameter".into(),
                value: 10.,
            }],
            basis: "test".into(),
            turning: None,
            shape: Shape::Callout {
                arcs: Vec::new(),
                sites: vec![([0., 0., 0.], 5.)],
            },
        };
        let mut old = dimension();
        old.lines = vec![[[0., 5.], [0., 12.]], [[0., 12.], [3., 12.]]];
        let candidates = callouts(&frame, &w, &old, &proof, &v, false, true, &[]);
        assert!(
            !candidates.is_empty(),
            "a preferred row is not a hard constraint"
        );
        assert!(candidates.len() <= 64);
        assert!(
            candidates
                .iter()
                .any(|p| { p.lines[0][0][0].abs() < 1e-6 && (p.lines[0][0][1] - 5.).abs() < 1e-6 }),
            "the clear upward route must remain available"
        );
        for p in candidates {
            assert!(proof.annotation_clear(&v, &p, true));
            assert!(0.5 * (p.label_box[1] + p.label_box[3]).abs() >= super::super::TEXT);
        }
    }

    #[test]
    fn radius_candidates_use_physical_arc_anchors_and_never_the_missing_semicircle() {
        let file = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/parts/mounting_plate.step");
        let mut a = crate::model::analyse_native(&file).unwrap().1;
        a.envelope = [[-50., -50., -1.], [50., 50., 1.]];
        let v = view();
        let frame = Frame::new(&a, &v);
        let ink = ink(vec![semicircle()]);
        let proof = proof(&ink);
        let w = Want {
            id: "d:test/slot_end_radius.radius".into(),
            views: vec!["plan"],
            value: 5.,
            terms: vec!["R5".into()],
            measurements: vec![],
            basis: "test".into(),
            turning: None,
            shape: Shape::Callout {
                arcs: Vec::new(),
                sites: vec![([0., 0., 0.], 5.)],
            },
        };
        let mut old = dimension();
        old.lines = vec![[[0., -5.], [0., -12.]], [[0., -12.], [3., -12.]]];
        let candidates = callouts(&frame, &w, &old, &proof, &v, false, false, &[]);
        assert!(!candidates.is_empty());
        for p in candidates {
            assert!(p.lines[0][0][1] >= 0.0);
            assert!(proof.annotation_clear(&v, &p, true));
        }
    }
}
