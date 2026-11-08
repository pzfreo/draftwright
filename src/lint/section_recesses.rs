//! Section recesses (quiddity's `SectionRecess`, the Analysis's `section_recess` features) read in
//! draftwright's drafting vocabulary, and the ones no grammar reads reported as unsupported.
//!
//! [`grammar`] is Python's `section_recess_fields` (`section_recess_contract.py`): from a record's
//! published frame, section profile, run interval and end surfaces only, it reads a recess as a
//! pocket (rectangular, edge-open polygonal, obround or rounded rectangle, perhaps with a
//! cylindrical mouth), a hexagonal pocket, a circular channel, a rectangular channel, or a
//! rectangular or round-bottom blind slot; or it says why none of these fits ([`Refusal`]). The
//! families that judge those grammars' requirements ([`blind_slots`](super::blind_slots), …) read
//! their recesses through it, so one reading decides what a recess is.
//!
//! [`coverage`] is Python's `lint_section_recess_coverage` (`linting/section_recess_coverage.py`,
//! which `passage_coverage.py` and `prismatic_pocket_coverage.py` only filter): every recess no
//! grammar reads is reported, whatever the marks say, as `passage_requirement_unsupported` (a
//! through-opening), `prismatic_pocket_requirement_unsupported` (a blind recess) or
//! `section_recess_requirement_unsupported`, with Python's message, `(k of N)` counted per kind;
//! and each recess recognition quiddity refused (its `SectionRecessRefusal`, kept by the bridge in
//! the recognition's refusals) as `section_recess_recognition_refused`
//! (LINT-code-passage_requirement_unsupported, LINT-code-prismatic_pocket_requirement_unsupported,
//! LINT-code-section_recess_requirement_unsupported, LINT-code-section_recess_recognition_refused).
//!
//! Python raises (and its lint fails) on a record that breaks the published contract, where it is
//! not merely outside the vocabulary (`ValueError` against `UnsupportedSectionRecess`). Such a
//! record is [`Refusal::Invalid`] here, and is reported unsupported with the reason, rather than
//! failing the whole lint. Python's checks of the record's exact Python types and key sets
//! (`_validate_public_value`, `_section_object`) are not repeated: the bridge reads JSON, and a
//! missing or ill-typed field is invalid here too.

use serde_json::{Map, Value};

use super::{LintIssue, Mark, Severity};
use crate::bridge::Feature;
use crate::model::Analysis;

type P3 = [f64; 3];
type P2 = [f64; 2];

/// Why a record is read by no drafting grammar.
#[derive(Clone, Debug, PartialEq)]
pub enum Refusal {
    /// Valid geometry outside draftwright's vocabulary (Python's `UnsupportedSectionRecess`).
    Unsupported(String),
    /// A record that breaks quiddity's published contract (Python's `ValueError`).
    Invalid(String),
}

impl Refusal {
    pub fn reason(&self) -> &str {
        match self {
            Refusal::Unsupported(r) | Refusal::Invalid(r) => r,
        }
    }
}

fn unsupported<T>(why: &str) -> Result<T, Refusal> {
    Err(Refusal::Unsupported(why.into()))
}

fn invalid<T>(why: &str) -> Result<T, Refusal> {
    Err(Refusal::Invalid(why.into()))
}

/// A pocket (`_pocket_from_bounds`), in quiddity's frame: axes are indices (0 = x).
#[derive(Clone, Debug, PartialEq)]
pub struct Pocket {
    /// The centre of its box.
    pub origin: P3,
    /// The run (depth) axis, and the section's wider and narrower axes.
    pub axis: usize,
    pub long_axis: usize,
    pub width_axis: usize,
    pub width: f64,
    pub length: f64,
    pub depth: f64,
    pub w_center: f64,
    /// Its extent along `long_axis`.
    pub lo: f64,
    pub hi: f64,
    /// Open at the part's edge (an edge-open recess): its location is fixed by the edge.
    pub edge_anchored: bool,
    /// The direction along `axis` it opens towards.
    pub open_sign: f64,
    /// A rounded rectangle's corner radius.
    pub corner_radius: Option<f64>,
    /// A cylindrical mouth: its axis, radius and a point on it (zero along the axis).
    pub mouth: Option<(usize, f64, P3)>,
}

/// A rectangular channel open along its run (`section_recess_fields`' channel).
#[derive(Clone, Debug, PartialEq)]
pub struct Channel {
    pub origin: P3,
    /// The run axis (its long axis), and the axis across it.
    pub axis: usize,
    pub width_axis: usize,
    pub width: f64,
    pub w_center: f64,
    pub lo: f64,
    pub hi: f64,
    pub d_lo: f64,
    pub d_hi: f64,
    pub open_sign: f64,
}

/// A rectangular or round-bottom blind slot: an edge-open recess with an open U section.
#[derive(Clone, Debug, PartialEq)]
pub struct BlindSlot {
    pub origin: P3,
    /// The run axis (mouth to end), and the axes across it and into the part.
    pub axis: usize,
    pub width_axis: usize,
    pub depth_axis: usize,
    pub open_sign: f64,
    pub depth_sign: f64,
    /// The opening's width (a round-bottom slot's flat floor is `width - 2 depth`).
    pub width: f64,
    pub length: f64,
    /// Its depth; a round-bottom slot's is its arcs' radius.
    pub depth: f64,
}

/// A recess as draftwright's drafting vocabulary reads it.
#[derive(Clone, Debug, PartialEq)]
pub enum Grammar {
    Pocket(Pocket),
    /// A regular hexagonal blind pocket: its run axis, depth and across-flats size, and the
    /// centre of its mouth.
    HexPocket {
        axis: usize,
        depth: f64,
        across_flats: f64,
        at: P3,
        /// Its floor's coordinate along the run axis (the mouth's is `at`'s).
        floor: f64,
    },
    /// A part-cylindrical channel: its run axis, radius, length and swept angle (degrees), the
    /// midpoint of its cylinder's axis, and its anchor (the arc's middle, mid-run).
    CircularChannel {
        axis: usize,
        radius: f64,
        length: f64,
        sweep: f64,
        centre: P3,
        anchor: P3,
    },
    Channel(Channel),
    RectangularBlindSlot(BlindSlot),
    RoundBottomBlindSlot(BlindSlot),
}

fn num(v: Option<&Value>, what: &str) -> Result<f64, Refusal> {
    v.and_then(Value::as_f64)
        .filter(|x| x.is_finite())
        .ok_or_else(|| Refusal::Invalid(format!("{what} must be a finite number")))
}

fn nums<const N: usize>(v: Option<&Value>, what: &str) -> Result<[f64; N], Refusal> {
    let a = v
        .and_then(Value::as_array)
        .filter(|a| a.len() == N)
        .ok_or_else(|| Refusal::Invalid(format!("{what} must contain {N} finite numbers")))?;
    let mut out = [0.0; N];
    for (o, x) in out.iter_mut().zip(a) {
        *o = num(Some(x), what)?;
    }
    Ok(out)
}

fn text<'a>(v: Option<&'a Value>, what: &str) -> Result<&'a str, Refusal> {
    v.and_then(Value::as_str)
        .ok_or_else(|| Refusal::Invalid(format!("{what} must be a string")))
}

/// A principal direction: its axis and sign (`_section_axis`).
fn section_axis(v: P3) -> Result<(usize, f64), Refusal> {
    let mut i = 0;
    for k in 1..3 {
        if v[k].abs() > v[i].abs() {
            i = k;
        }
    }
    let sign = if v[i] > 0.0 { 1.0 } else { -1.0 };
    if (0..3).any(|k| (v[k] - if k == i { sign } else { 0.0 }).abs() > 1e-9) {
        return unsupported("pocket requires principal section and run directions");
    }
    Ok((i, sign))
}

/// One profile vertex: its point and bulge.
struct Vertex {
    point: P2,
    bulge: f64,
}

/// One end of the run: open or capped, and its surface.
struct End<'a> {
    open: bool,
    surface: &'a Map<String, Value>,
}

impl End<'_> {
    /// A plane square to the run (`PlanarEndSurface` with no gradient).
    fn perpendicular(&self) -> bool {
        self.surface.get("type").and_then(Value::as_str) == Some("plane")
            && self
                .surface
                .get("gradient")
                .and_then(Value::as_array)
                .is_some_and(|g| g.len() == 2 && g.iter().all(|x| x.as_f64() == Some(0.0)))
    }
}

/// A record's geometry, read once.
struct Geometry<'a> {
    kind: &'a str,
    shape: &'a str,
    origin: P3,
    run: P3,
    u: P3,
    v: P3,
    low: f64,
    high: f64,
    closed: bool,
    vertices: Vec<Vertex>,
    profile: &'a Map<String, Value>,
    ends: [End<'a>; 2],
    defining: &'a [Value],
    constituent: &'a [Value],
}

impl<'a> Geometry<'a> {
    fn read(p: &'a Map<String, Value>) -> Result<Self, Refusal> {
        let object = |v: Option<&'a Value>, what: &str| {
            v.and_then(Value::as_object)
                .ok_or_else(|| Refusal::Invalid(format!("{what} must be an object")))
        };
        let class = object(p.get("classification"), "classification")?;
        let evidence = object(p.get("evidence"), "evidence")?;
        let faces = |name: &str| {
            evidence
                .get(name)
                .and_then(Value::as_array)
                .map(Vec::as_slice)
                .ok_or_else(|| Refusal::Invalid(format!("evidence must list its {name}")))
        };
        let g = object(p.get("geometry"), "geometry")?;
        let frame = object(g.get("frame"), "frame")?;
        let profile = object(g.get("profile"), "profile")?;
        let [low, high] = nums(g.get("run_interval"), "run interval")?;
        let ends = object(g.get("ends"), "ends")?;
        let end = |name: &str| -> Result<End<'a>, Refusal> {
            let e = object(ends.get(name), "end")?;
            let open = match text(e.get("condition"), "end condition")? {
                "open" => true,
                "capped" => false,
                _ => return invalid("section end condition must be 'open' or 'capped'"),
            };
            Ok(End {
                open,
                surface: object(e.get("surface"), "end surface")?,
            })
        };
        let vertices = profile
            .get("boundary")
            .and_then(Value::as_array)
            .ok_or(Refusal::Invalid("profile must list its boundary".into()))?
            .iter()
            .map(|v| {
                Ok(Vertex {
                    point: nums(v.get("point"), "profile point")?,
                    bulge: num(v.get("bulge"), "bulge")?,
                })
            })
            .collect::<Result<_, Refusal>>()?;
        Ok(Geometry {
            kind: text(class.get("feature_kind"), "feature kind")?,
            shape: text(class.get("section_shape"), "section shape")?,
            origin: nums(frame.get("origin"), "frame origin")?,
            run: nums(frame.get("run"), "run")?,
            u: nums(frame.get("u"), "u")?,
            v: nums(frame.get("v"), "v")?,
            low,
            high,
            closed: match text(profile.get("closure"), "profile closure")? {
                "closed" => true,
                "open" => false,
                _ => return invalid("profile closure must be open or closed"),
            },
            vertices,
            profile,
            ends: [end("low")?, end("high")?],
            defining: faces("defining_faces")?,
            constituent: faces("constituent_faces")?,
        })
    }

    fn perpendicular_ends(&self) -> bool {
        self.ends.iter().all(End::perpendicular)
    }

    fn points(&self) -> Vec<P2> {
        self.vertices.iter().map(|v| v.point).collect()
    }

    /// A section point at station *s* along the run, in quiddity's frame.
    fn world(&self, p: P2, s: f64) -> P3 {
        [0, 1, 2].map(|i| self.origin[i] + p[0] * self.u[i] + p[1] * self.v[i] + s * self.run[i])
    }
}

/// A recess's faces as its record publishes them, in quiddity's frame: its section boundary
/// (each vertex's point and the bulge of the edge it starts) swept along its run between the
/// ends of its run interval. An open boundary has no wall across the gap from its last vertex
/// back to its first: that is its opening. Each end is capped by a plane square to the run
/// (`Some(true)`), open (`Some(false)`), or capped by another surface (`None`).
#[derive(Clone, Debug, PartialEq)]
pub struct Swept {
    pub origin: P3,
    pub run: P3,
    pub u: P3,
    pub v: P3,
    pub interval: [f64; 2],
    pub boundary: Vec<(P2, f64)>,
    pub closed: bool,
    pub caps: [Option<bool>; 2],
}

/// *f*'s recess as a swept section ([`Swept`]), or why its record cannot be read.
pub fn swept(f: &Feature) -> Result<Swept, Refusal> {
    let g = Geometry::read(&f.parameters)?;
    if g.vertices.len() < 2 {
        return invalid("section profile needs at least two vertices");
    }
    Ok(Swept {
        origin: g.origin,
        run: g.run,
        u: g.u,
        v: g.v,
        interval: [g.low, g.high],
        boundary: g.vertices.iter().map(|v| (v.point, v.bulge)).collect(),
        closed: g.closed,
        caps: [0, 1].map(|i| {
            let e = &g.ends[i];
            if e.open {
                Some(false)
            } else if e.perpendicular() {
                Some(true)
            } else {
                None
            }
        }),
    })
}

/// *f*'s recess as draftwright's drafting vocabulary reads it (`section_recess_fields`), or why
/// none does.
pub fn grammar(f: &Feature) -> Result<Grammar, Refusal> {
    let g = Geometry::read(&f.parameters)?;
    if g.defining.is_empty() {
        return invalid("section recess requires defining-face evidence");
    }
    match (g.kind, g.shape) {
        ("channel", "circular") => return circular_channel(&g),
        ("pocket", "hexagonal") => return hex_pocket(&g),
        ("pocket", "obround") => return obround_pocket(&g).map(Grammar::Pocket),
        ("pocket", "general") => return rounded_pocket(&g).map(Grammar::Pocket),
        ("pocket", "rectangular") | ("edge_open_recess", "polygonal") => {
            return pocket(&g).map(Grammar::Pocket);
        }
        _ => {}
    }
    if !matches!(g.kind, "edge_open_recess" | "channel") {
        return unsupported("profile has no supported drafting grammar");
    }
    if !g.perpendicular_ends() {
        return unsupported("drafting grammar requires perpendicular run ends");
    }
    let (ri, rs) = section_axis(g.run)?;
    let (ui, us) = section_axis(g.u)?;
    let (vi, vs) = section_axis(g.v)?;
    if ri == ui || ri == vi || ui == vi {
        return invalid("section frame must use perpendicular principal directions");
    }
    if g.closed {
        return invalid("channel and edge-open recess must have an open profile");
    }
    let pts = g.points();
    if pts.len() != 4 {
        return unsupported("open profile needs a supported four-vertex U section");
    }
    // The opening is the published gap between the first and last vertices.
    let across: Vec<usize> = (0..2).filter(|&i| pts[0][i] != pts[3][i]).collect();
    let [wl] = across[..] else {
        return unsupported("U-section opening must follow a principal direction");
    };
    let dl = 1 - wl;
    let (open, floor) = (pts[0][dl], pts[1][dl]);
    if pts[2][dl] != floor || open == floor {
        return unsupported("U-section requires a parallel flat floor");
    }
    let [(wi, ws), (di, dbs)] = [[(ui, us), (vi, vs)][wl], [(ui, us), (vi, vs)][dl]];
    let depth_sign = dbs * if open > floor { 1.0 } else { -1.0 };
    let (w_lo, w_hi) = sorted(pts[0][wl], pts[3][wl]);
    let width = w_hi - w_lo;
    let depth = (open - floor).abs();
    let mut bounds = [[0.0; 2]; 3];
    bounds[ri] = pair(g.origin[ri] + rs * g.low, g.origin[ri] + rs * g.high);
    bounds[wi] = pair(g.origin[wi] + ws * w_lo, g.origin[wi] + ws * w_hi);
    bounds[di] = pair(g.origin[di] + dbs * floor, g.origin[di] + dbs * open);
    let centre = bounds.map(|b| (b[0] + b[1]) / 2.0);
    let length = g.high - g.low;
    if !centre
        .iter()
        .chain(&[width, depth, length])
        .all(|x| x.is_finite())
        || bounds.iter().any(|b| b[1] <= b[0])
    {
        return invalid("projected section measurements must remain finite and positive");
    }
    let open_sign = rs * if g.ends[1].open { 1.0 } else { -1.0 };
    let bulges: Vec<f64> = g.vertices.iter().map(|v| v.bulge).collect();
    if bulges.iter().all(|&b| b == 0.0) {
        if g.shape != "rectangular" || pts[0][wl] != pts[1][wl] || pts[2][wl] != pts[3][wl] {
            return unsupported("rectangular U-section requires two perpendicular walls");
        }
        if g.kind == "channel" {
            return Ok(Grammar::Channel(Channel {
                origin: centre,
                axis: ri,
                width_axis: wi,
                width,
                w_center: centre[wi],
                lo: bounds[ri][0],
                hi: bounds[ri][1],
                d_lo: bounds[di][0],
                d_hi: bounds[di][1],
                open_sign: depth_sign,
            }));
        }
        return Ok(Grammar::RectangularBlindSlot(BlindSlot {
            origin: centre,
            axis: ri,
            width_axis: wi,
            depth_axis: di,
            open_sign,
            depth_sign,
            width,
            length,
            depth,
        }));
    }
    if g.kind != "edge_open_recess" || g.shape != "general" {
        return unsupported("curved channel has no supported drafting grammar");
    }
    // Two equal quarter arcs (bulge tan 22.5°, signed by the section's winding) and a straight
    // floor between them.
    let quarter = 0.414213562373;
    let winding = (pts[1][0] - pts[0][0]) * (pts[2][1] - pts[1][1])
        - (pts[1][1] - pts[0][1]) * (pts[2][0] - pts[1][0]);
    let expected = quarter * if winding > 0.0 { 1.0 } else { -1.0 };
    let inset = if pts[3][wl] > pts[0][wl] { 1.0 } else { -1.0 };
    if bulges != [expected, 0.0, expected, 0.0]
        || (pts[1][wl] - pts[0][wl] - inset * depth).abs() > 1e-9
        || (pts[3][wl] - pts[2][wl] - inset * depth).abs() > 1e-9
        || width <= 2.0 * depth
    {
        return unsupported("round-bottom profile requires two equal quarter arcs");
    }
    Ok(Grammar::RoundBottomBlindSlot(BlindSlot {
        origin: centre,
        axis: ri,
        width_axis: wi,
        depth_axis: di,
        open_sign,
        depth_sign,
        width,
        length,
        depth,
    }))
}

fn sorted(a: f64, b: f64) -> (f64, f64) {
    if a <= b { (a, b) } else { (b, a) }
}

fn pair(a: f64, b: f64) -> [f64; 2] {
    let (lo, hi) = sorted(a, b);
    [lo, hi]
}

/// A pocket from its run interval and section bounds (`_pocket_from_bounds`): the wider of the
/// section's extents is its length.
fn pocket_from_bounds(
    g: &Geometry,
    (low, high): (f64, f64),
    section: [P2; 2],
    edge_anchored: bool,
) -> Result<Pocket, Refusal> {
    let (ri, rs) = section_axis(g.run)?;
    let (ui, us) = section_axis(g.u)?;
    let (vi, vs) = section_axis(g.v)?;
    let mut bounds = [[0.0; 2]; 3];
    bounds[ri] = pair(g.origin[ri] + rs * low, g.origin[ri] + rs * high);
    bounds[ui] = pair(
        g.origin[ui] + us * section[0][0],
        g.origin[ui] + us * section[0][1],
    );
    bounds[vi] = pair(
        g.origin[vi] + vs * section[1][0],
        g.origin[vi] + vs * section[1][1],
    );
    let extent = |i: usize| bounds[i][1] - bounds[i][0];
    // Python's max over (extent, index): the later axis wins a tie.
    let span = if (extent(vi), vi) > (extent(ui), ui) {
        vi
    } else {
        ui
    };
    let wide = if span == ui { vi } else { ui };
    let centre = bounds.map(|b| b[0] / 2.0 + b[1] / 2.0);
    let (width, length, depth) = (extent(wide), extent(span), high - low);
    if !centre
        .iter()
        .chain(&[width, length, depth])
        .all(|x| x.is_finite())
    {
        return invalid("projected pocket measurements must be finite");
    }
    if bounds.iter().any(|b| b[1] <= b[0]) {
        return invalid("projected pocket extents must remain positive");
    }
    Ok(Pocket {
        origin: centre,
        axis: ri,
        long_axis: span,
        width_axis: wide,
        width,
        length,
        depth,
        w_center: centre[wide],
        lo: bounds[span][0],
        hi: bounds[span][1],
        edge_anchored,
        open_sign: rs * if g.ends[1].open { 1.0 } else { -1.0 },
        corner_radius: None,
        mouth: None,
    })
}

/// A cylindrical end (quiddity's `CylindricalEndSurface`): a branch of a cylinder whose axis lies
/// in the section plane; heights are along the run.
struct Cylinder {
    point: P3,
    direction: P2,
    radius: f64,
    positive: bool,
}

impl Cylinder {
    fn read(s: &Map<String, Value>) -> Result<Self, Refusal> {
        let positive = match text(s.get("branch"), "cylinder branch")? {
            "positive" => true,
            "negative" => false,
            _ => return invalid("cylindrical end needs an explicit branch"),
        };
        let radius = num(s.get("radius"), "cylinder radius")?;
        if radius <= 0.0 {
            return invalid("cylindrical end needs a positive radius");
        }
        Ok(Cylinder {
            point: nums(s.get("axis_point"), "cylinder axis point")?,
            direction: nums(s.get("axis_direction"), "cylinder direction")?,
            radius,
            positive,
        })
    }

    fn offset(&self, p: P2) -> f64 {
        let [x, y] = self.direction;
        (-y * (p[0] - self.point[0]) + x * (p[1] - self.point[1])) / x.hypot(y)
    }

    fn height_at(&self, offset: f64) -> Result<f64, Refusal> {
        // Factored, as quiddity does, against cancellation near the branch's edge.
        let d = (self.radius - offset.abs()) * (self.radius + offset.abs());
        if d <= 0.0 {
            return invalid("cylinder branch must exist strictly over the complete profile");
        }
        Ok(self.point[2] + if self.positive { d.sqrt() } else { -d.sqrt() })
    }

    /// The height bounds over a polygon: the offset is affine, so its extremes are at vertices,
    /// though the crest may cross the interior.
    fn bounds(&self, points: &[P2]) -> Result<(f64, f64), Refusal> {
        if points.len() < 3 {
            return invalid("height bounds need a complete polygon");
        }
        let offsets: Vec<f64> = points.iter().map(|&p| self.offset(p)).collect();
        let lo = offsets.iter().copied().fold(f64::INFINITY, f64::min);
        let hi = offsets.iter().copied().fold(f64::NEG_INFINITY, f64::max);
        let far = lo.abs().max(hi.abs());
        let near = if lo <= 0.0 && 0.0 <= hi {
            0.0
        } else {
            lo.abs().min(hi.abs())
        };
        let (a, b) = (self.height_at(far)?, self.height_at(near)?);
        Ok((a.min(b), a.max(b)))
    }
}

/// Python's `round(x, 3)`: correctly rounded to three decimals.
fn round3(x: f64) -> f64 {
    format!("{x:.3}").parse().unwrap_or(x)
}

/// A rectangular pocket, or an edge-open polygonal recess read as one, perhaps with a
/// cylindrical mouth (`section_recess_pocket_fields`, `_pocket_profile_bounds`).
fn pocket(g: &Geometry) -> Result<Pocket, Refusal> {
    let faces = |v: &[Value]| -> Option<Vec<u64>> { v.iter().map(Value::as_u64).collect() };
    let (Some(defining), Some(constituent)) = (faces(g.defining), faces(g.constituent)) else {
        return invalid("face references must be non-negative integers");
    };
    if defining.is_empty() || !defining.iter().all(|f| constituent.contains(f)) {
        return invalid("pocket requires defining faces contained in constituent faces");
    }
    let (ri, _) = section_axis(g.run)?;
    let (ui, _) = section_axis(g.u)?;
    let (vi, _) = section_axis(g.v)?;
    if ri == ui || ri == vi || ui == vi {
        return invalid("section frame directions must be perpendicular");
    }
    let (u, v) = (g.u, g.v);
    let cross = [
        u[1] * v[2] - u[2] * v[1],
        u[2] * v[0] - u[0] * v[2],
        u[0] * v[1] - u[1] * v[0],
    ];
    if (0..3).any(|k| (cross[k] - g.run[k]).abs() > 1e-9) {
        return invalid("section frame must be right-handed");
    }
    let (mut low, mut high) = (g.low, g.high);
    if high <= low {
        return invalid("run interval must increase");
    }
    let mut curved = Vec::new();
    for (k, end) in g.ends.iter().enumerate() {
        match end.surface.get("type").and_then(Value::as_str) {
            Some("cylinder") => curved.push((k, Cylinder::read(end.surface)?)),
            Some("plane") => {
                let [a, b] = nums(end.surface.get("gradient"), "end gradient")?;
                if a != 0.0 || b != 0.0 {
                    return unsupported("pocket requires perpendicular run ends");
                }
            }
            _ => return invalid("end requires a known surface type"),
        }
    }
    if g.ends[0].open == g.ends[1].open {
        return invalid("pocket requires exactly one capped and one open end");
    }
    let edge_anchored = g.kind == "edge_open_recess";
    let (points, bounds) = profile_bounds(g, edge_anchored)?;
    let mut mouth = None;
    if !curved.is_empty() {
        if edge_anchored || curved.len() != 1 {
            return unsupported("curved mouth requires one cylindrical end on a closed pocket");
        }
        let (k, c) = &curved[0];
        let high_open = *k == 1;
        if !g.ends[*k].open || c.positive != high_open {
            return invalid("cylindrical pocket mouth must open away from its planar floor");
        }
        if round3(c.height_at(c.offset([0.0, 0.0]))?) != if high_open { high } else { low } {
            return invalid(
                "cylindrical mouth must agree with its published centroid intersection",
            );
        }
        let (m_lo, m_hi) = c.bounds(&points)?;
        if if high_open { m_lo <= low } else { m_hi >= high } {
            return invalid("cylindrical pocket requires positive depth over its complete profile");
        }
        let direction = [0, 1, 2].map(|i| c.direction[0] * u[i] + c.direction[1] * v[i]);
        let (axis, _) = section_axis(direction)?;
        let mut at = [0, 1, 2]
            .map(|i| g.origin[i] + c.point[0] * u[i] + c.point[1] * v[i] + c.point[2] * g.run[i]);
        at[axis] = 0.0;
        mouth = Some((axis, c.radius, at));
        if high_open {
            high = m_hi;
        } else {
            low = m_lo;
        }
    }
    let mut p = pocket_from_bounds(g, (low, high), bounds, edge_anchored)?;
    p.mouth = mouth;
    Ok(p)
}

/// A rectangular section's points and bounds: a closed rectangle, or for an edge-open recess an
/// open chain of three or four vertices, every side along a section axis
/// (`_pocket_profile_bounds`).
fn profile_bounds(g: &Geometry, edge_anchored: bool) -> Result<(Vec<P2>, [P2; 2]), Refusal> {
    if g.closed == edge_anchored {
        return invalid("profile closure disagrees with pocket classification");
    }
    let n = g.vertices.len();
    if !(if edge_anchored {
        n == 3 || n == 4
    } else {
        n == 4
    }) {
        return unsupported("pocket requires a rectangular boundary or open chain");
    }
    if g.vertices.iter().any(|v| v.bulge != 0.0) {
        return unsupported("curved profiles need their own drafting semantics");
    }
    let points = g.points();
    if edge_anchored {
        let opening = g
            .profile
            .get("opening")
            .and_then(Value::as_array)
            .filter(|o| o.len() == 2)
            .ok_or(Refusal::Invalid(
                "opening must join the two loose endpoints".into(),
            ))?;
        let ends: [P2; 2] = [
            nums(opening.first(), "opening point")?,
            nums(opening.get(1), "opening point")?,
        ];
        if ends != [points[n - 1], points[0]] {
            return invalid("opening must join the two loose endpoints");
        }
    }
    let bounds = [0, 1].map(|i| {
        let lo = points.iter().map(|p| p[i]).fold(f64::INFINITY, f64::min);
        let hi = points
            .iter()
            .map(|p| p[i])
            .fold(f64::NEG_INFINITY, f64::max);
        [lo, hi]
    });
    if bounds.iter().any(|b| b[1] <= b[0]) {
        return invalid("pocket section must have positive extents");
    }
    let distinct = (0..n).all(|i| (0..i).all(|j| points[i] != points[j]));
    if !distinct
        || points
            .iter()
            .any(|p| (0..2).any(|i| !bounds[i].contains(&p[i])))
    {
        return unsupported("profile does not follow rectangular supports");
    }
    let mut chain = points.clone();
    if !edge_anchored {
        chain.push(points[0]);
    }
    if chain
        .windows(2)
        .any(|w| (0..2).filter(|&i| w[0][i] != w[1][i]).count() != 1)
    {
        return unsupported("profile contains a diagonal or crossing support");
    }
    Ok((points, bounds))
}

/// A straight-sided capsule from its two semicircular ends (`_obround_pocket_fields`).
fn obround_pocket(g: &Geometry) -> Result<Pocket, Refusal> {
    if !g.perpendicular_ends() {
        return unsupported("obround pocket requires perpendicular run ends");
    }
    let vs = &g.vertices;
    let mut bulges: Vec<f64> = vs.iter().map(|v| v.bulge).collect();
    bulges.sort_by(f64::total_cmp);
    if vs.len() != 4 || bulges != [0.0, 0.0, 1.0, 1.0] {
        return unsupported("obround pocket requires two straight sides and semicircular ends");
    }
    let arcs: Vec<usize> = (0..4).filter(|&i| vs[i].bulge == 1.0).collect();
    if (arcs[1] - arcs[0]) % 4 != 2 {
        return unsupported("obround end arcs must be opposite");
    }
    let next = |i: usize| vs[(i + 1) % 4].point;
    let centres: Vec<P2> = arcs
        .iter()
        .map(|&i| [0, 1].map(|j| (vs[i].point[j] + next(i)[j]) / 2.0))
        .collect();
    let long: Vec<usize> = (0..2).filter(|&j| centres[0][j] != centres[1][j]).collect();
    let [long] = long[..] else {
        return unsupported("obround pocket requires a principal long direction");
    };
    let wide = 1 - long;
    let mut radii = Vec::new();
    for (&i, &c) in arcs.iter().zip(&centres) {
        let (first, last) = (vs[i].point, next(i));
        if first[long] != last[long] {
            return unsupported("obround end chord must be perpendicular to its long direction");
        }
        let r = (last[wide] - first[wide]).abs() / 2.0;
        if r <= 0.0 {
            return invalid("obround radius must be positive");
        }
        radii.push(r);
        // A positive semicircle runs anticlockwise: its midpoint lies to the chord's right.
        let mid = [
            c[0] + (last[1] - first[1]) / 2.0,
            c[1] - (last[0] - first[0]) / 2.0,
        ];
        let other = if c == centres[0] {
            centres[1]
        } else {
            centres[0]
        };
        if (mid[long] - c[long]) * (c[long] - other[long]) <= 0.0 {
            return unsupported("obround caps must face away from the straight span");
        }
    }
    if (radii[0] - radii[1]).abs() > 1e-9 {
        return unsupported("obround end radii must agree");
    }
    let r = radii[0];
    let bounds = [0, 1].map(|j| {
        [
            centres[0][j].min(centres[1][j]) - r,
            centres[0][j].max(centres[1][j]) + r,
        ]
    });
    if (0..4).any(|i| vs[i].bulge == 0.0 && vs[i].point[wide] != next(i)[wide]) {
        return unsupported("obround straight sides must be parallel");
    }
    pocket_from_bounds(g, (g.low, g.high), bounds, false)
}

/// A rectangle with four equal quarter-circle corners (`_rounded_rectangle_pocket_fields`).
fn rounded_pocket(g: &Geometry) -> Result<Pocket, Refusal> {
    if !g.perpendicular_ends() {
        return unsupported("rounded pocket requires perpendicular run ends");
    }
    let vs = &g.vertices;
    if !g.closed || vs.len() != 8 {
        return unsupported("general pocket requires a rounded rectangular profile");
    }
    let pts = g.points();
    let min = |i: usize| pts.iter().map(|p| p[i]).fold(f64::INFINITY, f64::min);
    let max = |i: usize| pts.iter().map(|p| p[i]).fold(f64::NEG_INFINITY, f64::max);
    let (x0, x1, y0, y1) = (min(0), max(0), min(1), max(1));
    let quarter = 0.414213562373;
    let arcs: Vec<usize> = (0..8).filter(|&i| vs[i].bulge != 0.0).collect();
    if arcs.len() != 4 || arcs.iter().any(|&i| vs[i].bulge != quarter) {
        return unsupported("rounded pocket requires four equal quarter arcs");
    }
    let r = (pts[(arcs[0] + 1) % 8][0] - pts[arcs[0]][0]).abs();
    if r <= 0.0 || 2.0 * r >= (x1 - x0).min(y1 - y0) {
        return unsupported("rounded pocket requires positive straight sides");
    }
    let expected = [
        ([x0, y0 + r], quarter),
        ([x0 + r, y0], 0.0),
        ([x1 - r, y0], quarter),
        ([x1, y0 + r], 0.0),
        ([x1, y1 - r], quarter),
        ([x1 - r, y1], 0.0),
        ([x0 + r, y1], quarter),
        ([x0, y1 - r], 0.0),
    ];
    let fits = (0..8).any(|shift| {
        vs.iter().enumerate().all(|(i, v)| {
            let (p, b) = expected[(i + shift) % 8];
            (v.point[0] - p[0]).hypot(v.point[1] - p[1]) <= 1e-9 && v.bulge == b
        })
    });
    if !fits {
        return unsupported("rounded pocket boundary must have equal tangent corners");
    }
    let mut p = pocket_from_bounds(g, (g.low, g.high), [[x0, x1], [y0, y1]], false)?;
    p.corner_radius = Some(r);
    Ok(p)
}

/// A part-cylindrical channel open at both ends (`circular_channel_fields`,
/// `circular_channel_geometry`).
fn circular_channel(g: &Geometry) -> Result<Grammar, Refusal> {
    if !g.perpendicular_ends() || !g.ends.iter().all(|e| e.open) {
        return unsupported("circular channel requires two perpendicular open ends");
    }
    if g.closed || g.vertices.len() != 2 {
        return unsupported("circular channel requires a two-vertex open arc");
    }
    let (first, last) = (&g.vertices[0], &g.vertices[1]);
    let b = first.bulge;
    if b == 0.0 || last.bulge != 0.0 {
        return unsupported("circular channel requires exactly one curved physical edge");
    }
    let (dx, dy) = (
        last.point[0] - first.point[0],
        last.point[1] - first.point[1],
    );
    let chord = dx.hypot(dy);
    if chord == 0.0 {
        return invalid("circular channel endpoints must be distinct");
    }
    let radius = chord * (1.0 + b * b) / (4.0 * b.abs());
    let mid = [0, 1].map(|i| (first.point[i] + last.point[i]) / 2.0);
    let factor = (1.0 - b * b) / (4.0 * b);
    let centre = [mid[0] - factor * dy, mid[1] + factor * dx];
    let arc_middle = [mid[0] + b * dy / 2.0, mid[1] - b * dx / 2.0];
    let (run, _) = section_axis(g.run)?;
    let across: Vec<usize> = (0..3).filter(|&i| i != run).collect();
    let mut line = [g.world(centre, g.low), g.world(centre, g.high)];
    if line[1][run] < line[0][run] {
        line.swap(0, 1);
    }
    let section: Vec<P2> = [first.point, arc_middle, last.point]
        .iter()
        .map(|&p| {
            let w = g.world(p, 0.0);
            [w[across[0]], w[across[1]]]
        })
        .collect();
    let length = g.high - g.low;
    if !(radius.is_finite() && length.is_finite()) || radius <= 0.0 || length <= 0.0 {
        return invalid("circular channel sizes must be positive");
    }
    if across
        .iter()
        .any(|&i| (line[0][i] - line[1][i]).abs() > 1e-7)
    {
        return invalid("circular channel centreline must follow its run axis");
    }
    if (line[1][run] - line[0][run] - length).abs() > 1e-7 {
        return invalid("circular channel ordered axis endpoints must span its length");
    }
    let c = [line[0][across[0]], line[0][across[1]]];
    let vectors: Vec<P2> = section.iter().map(|p| [p[0] - c[0], p[1] - c[1]]).collect();
    if vectors
        .iter()
        .any(|v| (v[0].hypot(v[1]) - radius).abs() > 1e-6)
    {
        return invalid("circular channel arc points must lie on its cylinder");
    }
    // Equal signed half-arcs prove the middle point is on the physical arc, a major arc too.
    let half = |a: P2, b: P2| (a[0] * b[1] - a[1] * b[0]).atan2(a[0] * b[0] + a[1] * b[1]);
    let (left, right) = (half(vectors[0], vectors[1]), half(vectors[1], vectors[2]));
    if left.abs() < 1e-9 || (left - right).abs() > 1e-7 || left.abs() >= std::f64::consts::PI - 1e-9
    {
        return invalid("circular channel requires an ordered nondegenerate open arc");
    }
    let centre = [0, 1, 2].map(|i| (line[0][i] + line[1][i]) / 2.0);
    let mut anchor = centre;
    anchor[across[0]] = section[1][0];
    anchor[across[1]] = section[1][1];
    Ok(Grammar::CircularChannel {
        axis: run,
        radius,
        length,
        sweep: (left + right).abs().to_degrees(),
        centre,
        anchor,
    })
}

/// A regular hexagonal blind pocket (`hex_pocket_fields`, `hex_pocket_geometry`): six straight
/// walls fitting a regular hexagon within quiddity's 0.001 mm rounding of its vertices.
fn hex_pocket(g: &Geometry) -> Result<Grammar, Refusal> {
    if !g.perpendicular_ends() || g.ends[0].open == g.ends[1].open {
        return unsupported("hex pocket requires one open end and one planar floor");
    }
    if !g.closed || g.vertices.len() != 6 || g.vertices.iter().any(|v| v.bulge != 0.0) {
        return unsupported("hex pocket requires six straight physical walls");
    }
    let (run, _) = section_axis(g.run)?;
    section_axis(g.u)?;
    section_axis(g.v)?;
    let across: Vec<usize> = (0..3).filter(|&i| i != run).collect();
    let depth = g.high - g.low;
    // hex_pocket_geometry's failures are outside the vocabulary here, as in Python.
    if depth.is_nan() || depth <= 0.0 {
        return unsupported("hex pocket depth must be positive");
    }
    let vs: Vec<P2> = g
        .vertices
        .iter()
        .map(|v| {
            let w = g.world(v.point, 0.0);
            [w[across[0]], w[across[1]]]
        })
        .collect();
    let centre = [0, 1].map(|i| vs.iter().map(|p| p[i]).sum::<f64>() / 6.0);
    let edges: Vec<P2> = (0..6)
        .map(|i| [vs[(i + 1) % 6][0] - vs[i][0], vs[(i + 1) % 6][1] - vs[i][1]])
        .collect();
    let turns: Vec<f64> = (0..6)
        .map(|i| edges[i][0] * edges[(i + 1) % 6][1] - edges[i][1] * edges[(i + 1) % 6][0])
        .collect();
    if !(turns.iter().all(|&t| t > 1e-12) || turns.iter().all(|&t| t < -1e-12)) {
        return unsupported("hex pocket requires a convex ordered section");
    }
    let winding = if turns[0] > 0.0 { 1.0 } else { -1.0 };
    // The least-squares rotation of a regular hexagon onto the vertices, as complex numbers.
    let basis: Vec<P2> = (0..6)
        .map(|i| {
            let a = winding * i as f64 * std::f64::consts::PI / 3.0;
            [a.cos(), a.sin()]
        })
        .collect();
    let mut rotation = [0.0, 0.0];
    for (p, q) in vs.iter().zip(&basis) {
        let d = [p[0] - centre[0], p[1] - centre[1]];
        // d × conj(q)
        rotation[0] += d[0] * q[0] + d[1] * q[1];
        rotation[1] += d[1] * q[0] - d[0] * q[1];
    }
    rotation = rotation.map(|x| x / 6.0);
    let mids: Vec<P2> = (0..6)
        .map(|i| [0, 1].map(|j| (vs[i][j] + vs[(i + 1) % 6][j]) / 2.0))
        .collect();
    let across_flats = (0..6)
        .map(|i| {
            let (m, n, e) = (mids[i], mids[(i + 3) % 6], edges[i]);
            ((n[0] - m[0]) * e[1] - (n[1] - m[1]) * e[0]).abs() / e[0].hypot(e[1])
        })
        .fold(f64::INFINITY, f64::min);
    let size = rotation[0].hypot(rotation[1]);
    if !across_flats.is_finite() || across_flats <= 0.0 || size == 0.0 {
        return unsupported("hex pocket across-flats size must be positive and finite");
    }
    let scale = across_flats / 3f64.sqrt() / size;
    let witness = rotation.map(|x| x * scale);
    let regular = vs.iter().zip(&basis).all(|(p, q)| {
        let w = [
            centre[0] + witness[0] * q[0] - witness[1] * q[1],
            centre[1] + witness[0] * q[1] + witness[1] * q[0],
        ];
        (w[0] - p[0]).abs().max((w[1] - p[1]).abs()) <= 0.0005 + 1e-9
    });
    if !regular {
        return unsupported("six-sided profile is not regular at the published polygon resolution");
    }
    // The mouth's centre: the section's centre across, the open end along.
    let (opening, closed) = if g.ends[1].open {
        (g.high, g.low)
    } else {
        (g.low, g.high)
    };
    let mut at = [0.0; 3];
    at[run] = g.origin[run] + opening * g.run[run];
    let floor = g.origin[run] + closed * g.run[run];
    at[across[0]] = centre[0];
    at[across[1]] = centre[1];
    Ok(Grammar::HexPocket {
        axis: run,
        depth,
        across_flats,
        at,
        floor,
    })
}

/// Python's `{x:g}`: six significant figures, trailing zeros dropped.
fn general(x: f64) -> String {
    if x == 0.0 || !x.is_finite() {
        return format!("{x}");
    }
    let sci = format!("{x:.5e}");
    // Invariant: The finite nonzero value is formatted locally with Rust scientific notation and an integer exponent.
    let (mantissa, exp) = sci.split_once('e').expect("an exponent");
    // Invariant: The finite nonzero value is formatted locally with Rust scientific notation and an integer exponent.
    let exp: i32 = exp.parse().expect("an exponent");
    if !(-4..6).contains(&exp) {
        let m = mantissa.trim_end_matches('0').trim_end_matches('.');
        return format!("{m}e{}{:02}", if exp < 0 { '-' } else { '+' }, exp.abs());
    }
    let s = format!("{x:.*}", (5 - exp) as usize);
    if s.contains('.') {
        s.trim_end_matches('0').trim_end_matches('.').into()
    } else {
        s
    }
}

/// The Analysis's section recesses in quiddity's order (its record `index`).
pub fn recesses(analysis: &Analysis) -> Vec<&Feature> {
    let mut out: Vec<&Feature> = analysis
        .features
        .iter()
        .filter(|f| f.kind == "section_recess")
        .collect();
    out.sort_by_key(|f| f.parameters.get("index").and_then(Value::as_u64));
    out
}

/// The disposition findings: each recess no grammar reads, then each refused recognition.
pub fn coverage(analysis: &Analysis, _marks: &[Mark]) -> Vec<LintIssue> {
    let refused: Vec<(&Feature, Refusal)> = recesses(analysis)
        .into_iter()
        .filter_map(|f| grammar(f).err().map(|r| (f, r)))
        .collect();
    let kind = |f: &Feature| {
        f.parameters
            .get("classification")
            .and_then(|c| c.get("feature_kind"))
            .and_then(Value::as_str)
            .unwrap_or("?")
            .to_string()
    };
    let total = |k: &str| refused.iter().filter(|(f, _)| kind(f) == k).count();
    let mut seen: Vec<String> = Vec::new();
    let mut issues = Vec::new();
    for (f, why) in &refused {
        let k = kind(f);
        seen.push(k.clone());
        let count = format!(
            "({} of {})",
            seen.iter().filter(|s| **s == k).count(),
            total(&k)
        );
        let g = f.parameters.get("geometry");
        let sides = g
            .and_then(|g| g.get("profile"))
            .and_then(|p| p.get("boundary"))
            .and_then(Value::as_array)
            .map_or(0, Vec::len);
        let (code, description) = match k.as_str() {
            "passage" => (
                "passage_requirement_unsupported",
                format!("recognised {sides}-edge prismatic through-opening {count}"),
            ),
            "pocket" => {
                let deep = g
                    .and_then(|g| g.get("run_interval"))
                    .and_then(Value::as_array)
                    .and_then(|r| Some(r.get(1)?.as_f64()? - r.first()?.as_f64()?))
                    .map_or("?".into(), general);
                (
                    "prismatic_pocket_requirement_unsupported",
                    format!(
                        "recognised {sides}-sided blind prismatic recess {deep} mm deep {count}"
                    ),
                )
            }
            _ => {
                let number = |name: &str| {
                    f.parameters
                        .get(name)
                        .and_then(Value::as_u64)
                        .map_or("?".into(), |n| n.to_string())
                };
                (
                    "section_recess_requirement_unsupported",
                    format!(
                        "section recess {} on body {}",
                        number("index"),
                        number("body")
                    ),
                )
            }
        };
        let reason = match why {
            Refusal::Unsupported(r) => r.clone(),
            Refusal::Invalid(r) => format!("its record is not valid ({r})"),
        };
        issues.push(LintIssue {
            sheet: None,
            id: format!("l:{code}:{}", f.id),
            code: code.into(),
            severity: Severity::Warning,
            message: format!(
                "{description} is not represented by Draftwright dimensions: {reason}; review \
                 and define its manufacturing section outside automatic drawing approval"
            ),
            at: None,
            view: None,
            subjects: vec![f.id.clone()],
            requirements: Vec::new(),
            measurements: Vec::new(),
            sources: Vec::new(),
            stage: None,
            annotation: None,
            related: Vec::new(),
            evidence: Some(why.reason().into()),
            fix: Some(format!(
                "Review and dimension the section of {} by hand.",
                super::parameters::feature_name(f)
            )),
        });
    }
    let refusals = analysis
        .recognition
        .iter()
        .flat_map(|r| &r.refusals)
        .filter(|r| r.subtype == "SectionRecessRefusal");
    for (i, r) in refusals.enumerate() {
        let get = |name: &str| {
            r.parameters.get(name).map_or("?".into(), |v| match v {
                Value::String(s) => s.clone(),
                v => v.to_string(),
            })
        };
        let code = "section_recess_recognition_refused";
        let faces: Vec<String> = r.faces.iter().map(usize::to_string).collect();
        issues.push(LintIssue {
            sheet: None,
            id: format!("l:{code}:{}", i + 1),
            code: code.into(),
            severity: Severity::Warning,
            message: format!(
                "section recess recognition on body {} refused: {}",
                get("body"),
                get("reason")
            ),
            at: None,
            view: None,
            subjects: Vec::new(),
            requirements: Vec::new(),
            measurements: Vec::new(),
            sources: Vec::new(),
            stage: None,
            annotation: None,
            related: Vec::new(),
            evidence: Some(format!("quiddity refused faces {}", faces.join(", "))),
            fix: Some(
                "Review those faces by hand and identify any missed recess that matches a supported feature type.".into(),
            ),
        });
    }
    issues
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use serde_json::json;

    /// A section recess record: its kind and shape, frame (`run`, `u`, `v` at `origin`), run
    /// interval, ends (`open` low and high), profile (closed or open; vertex points and bulges).
    pub(crate) fn recess(
        kind: &str,
        shape: &str,
        frame: [P3; 4],
        interval: [f64; 2],
        open: [bool; 2],
        closed: bool,
        boundary: &[(P2, f64)],
    ) -> Value {
        let end = |o: bool| {
            json!({ "condition": if o { "open" } else { "capped" },
                    "surface": { "type": "plane", "gradient": [0.0, 0.0] } })
        };
        let mut profile = json!({
            "closure": if closed { "closed" } else { "open" },
            "boundary": boundary.iter().map(|(p, b)| json!({ "point": p, "bulge": b }))
                .collect::<Vec<_>>(),
        });
        if !closed {
            let n = boundary.len();
            profile["opening"] = json!([boundary[n - 1].0, boundary[0].0]);
        }
        json!({
            "index": 0, "body": 0,
            "classification": { "feature_kind": kind, "section_shape": shape },
            "evidence": { "defining_faces": [1, 2], "constituent_faces": [1, 2, 3] },
            "geometry": {
                "type": "section_recess",
                "frame": { "origin": frame[0], "run": frame[1], "u": frame[2], "v": frame[3] },
                "run_interval": interval,
                "ends": { "low": end(open[0]), "high": end(open[1]) },
                "profile": profile,
            },
        })
    }

    pub(crate) const Z: [P3; 4] = [
        [0.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
    ];

    fn feature(record: Value) -> Feature {
        serde_json::from_value(json!({
            "id": "f:section_recess:1", "kind": "section_recess", "faces": [1, 2, 3],
            "parameters": record,
            "fingerprint": { "version": "test", "family": "section_recesses",
                             "subtype": "SectionRecess", "sizes": {},
                             "position": [0.0, 0.0, 0.0], "neighbourhood": [] },
        }))
        .unwrap()
    }

    fn pentagon() -> Vec<(P2, f64)> {
        (0..5)
            .map(|i| {
                let a = std::f64::consts::TAU * i as f64 / 5.0;
                ([12.0 * a.cos(), 12.0 * a.sin()], 0.0)
            })
            .collect()
    }

    /// Python's `{:g}`, which the pocket depth is printed with.
    #[test]
    fn numbers_print_as_pythons_general_format() {
        for (x, s) in [
            (6.0, "6"),
            (20.51, "20.51"),
            (26.051 - 23.947, "2.104"),
            (6.333 - -3.593, "9.926"),
            (0.5, "0.5"),
            (123456.7, "123457"),
            (1234567.0, "1.23457e+06"),
            (0.00001234, "1.234e-05"),
        ] {
            assert_eq!(general(x), s, "{x}");
        }
    }

    /// The grammars, from the shapes of Python's own tests: a rectangular pocket (its wider
    /// side is its length), a rectangular and a round-bottom blind slot (an edge-open U section),
    /// a rectangular channel, a regular hexagonal pocket and a half-round channel; and a pentagon
    /// pocket no grammar reads (test_issue_1246's `RegularPolygon(12, 5)` pocket, 6 mm deep).
    #[test]
    fn each_grammar_reads_its_recess() {
        let rect = [
            ([-15.0, -12.0], 0.0),
            ([15.0, -12.0], 0.0),
            ([15.0, 12.0], 0.0),
            ([-15.0, 12.0], 0.0),
        ];
        let p = recess(
            "pocket",
            "rectangular",
            Z,
            [4.0, 10.0],
            [false, true],
            true,
            &rect,
        );
        let Ok(Grammar::Pocket(p)) = grammar(&feature(p)) else {
            panic!()
        };
        assert_eq!(
            (
                p.axis,
                p.long_axis,
                p.width_axis,
                p.width,
                p.length,
                p.depth
            ),
            (2, 0, 1, 24.0, 30.0, 6.0)
        );
        assert_eq!(
            (p.origin, p.open_sign, p.edge_anchored),
            ([0.0, 0.0, 7.0], 1.0, false)
        );

        // A U open towards +y: the opening is the gap from the last vertex to the first.
        let u = [
            ([-3.0, 10.0], 0.0),
            ([-3.0, 6.0], 0.0),
            ([3.0, 6.0], 0.0),
            ([3.0, 10.0], 0.0),
        ];
        let x_run = [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ];
        let s = recess(
            "edge_open_recess",
            "rectangular",
            x_run,
            [-20.0, 0.0],
            [false, true],
            false,
            &u,
        );
        let Ok(Grammar::RectangularBlindSlot(s)) = grammar(&feature(s)) else {
            panic!()
        };
        assert_eq!(
            (
                s.axis,
                s.width_axis,
                s.depth_axis,
                s.width,
                s.length,
                s.depth
            ),
            (0, 1, 2, 6.0, 20.0, 4.0)
        );
        assert_eq!((s.open_sign, s.depth_sign), (1.0, 1.0));
        let c = recess(
            "channel",
            "rectangular",
            x_run,
            [-20.0, 0.0],
            [true, true],
            false,
            &u,
        );
        assert!(matches!(grammar(&feature(c)), Ok(Grammar::Channel(c)) if c.width == 6.0));

        // Round-bottom: quarter arcs of radius 2 into a floor 4 wide (opening 8).
        let q = 0.414213562373;
        let rb = [
            ([-4.0, 10.0], q),
            ([-2.0, 8.0], 0.0),
            ([2.0, 8.0], q),
            ([4.0, 10.0], 0.0),
        ];
        let r = recess(
            "edge_open_recess",
            "general",
            x_run,
            [-20.0, 0.0],
            [false, true],
            false,
            &rb,
        );
        let Ok(Grammar::RoundBottomBlindSlot(r)) = grammar(&feature(r.clone())) else {
            panic!("{:?}", grammar(&feature(r)))
        };
        assert_eq!((r.width, r.depth, r.length), (8.0, 2.0, 20.0));

        // m1000's hexagonal pocket (quiddity 0.3.9), 2.104 deep.
        let hex = [
            ([-1.048, 0.387], 0.0),
            ([-0.859, -0.714], 0.0),
            ([0.189, -1.101], 0.0),
            ([1.048, -0.387], 0.0),
            ([0.859, 0.714], 0.0),
            ([-0.189, 1.101], 0.0),
        ];
        let h = recess(
            "pocket",
            "hexagonal",
            Z,
            [23.947, 26.051],
            [false, true],
            true,
            &hex,
        );
        let Ok(Grammar::HexPocket { across_flats, .. }) = grammar(&feature(h)) else {
            panic!()
        };
        assert!((across_flats - 1.9347).abs() < 1e-3, "{across_flats}");

        // m1000's half-round channel, ø2.
        let arc = [([-1.0, -0.4244], -1.0), ([1.0, -0.4244], 0.0)];
        let ch = recess(
            "channel",
            "circular",
            x_run,
            [-6.26, 6.333],
            [true, true],
            false,
            &arc,
        );
        let Ok(Grammar::CircularChannel { radius, sweep, .. }) = grammar(&feature(ch)) else {
            panic!()
        };
        assert_eq!((radius, sweep.round()), (1.0, 180.0));

        let pent = recess(
            "pocket",
            "pentagonal",
            Z,
            [4.0, 10.0],
            [false, true],
            true,
            &pentagon(),
        );
        assert_eq!(
            grammar(&feature(pent)),
            Err(Refusal::Unsupported(
                "profile has no supported drafting grammar".into()
            ))
        );
    }

    /// test_issue_1246 (`test_a_prismatic_pocket_is_specific_actionable_and_non_info`): a
    /// pentagonal blind pocket 6 mm deep is one warning, `recognised 5-sided blind prismatic
    /// recess 6 mm deep (1 of 1)`, whatever the sheet says; a supported pocket is not reported;
    /// a through-opening is a passage, counted on its own; quiddity's refusal is reported.
    #[test]
    fn a_recess_no_grammar_reads_is_reported_unsupported() {
        let pent = recess(
            "pocket",
            "pentagonal",
            Z,
            [4.0, 10.0],
            [false, true],
            true,
            &pentagon(),
        );
        let mut tri = recess(
            "passage",
            "triangular",
            Z,
            [0.0, 20.0],
            [true, true],
            true,
            &[([0.0, 0.0], 0.0), ([5.0, 0.0], 0.0), ([0.0, 5.0], 0.0)],
        );
        tri["index"] = json!(1);
        let rect = recess(
            "pocket",
            "rectangular",
            Z,
            [4.0, 10.0],
            [false, true],
            true,
            &[
                ([-1.0, -1.0], 0.0),
                ([1.0, -1.0], 0.0),
                ([1.0, 1.0], 0.0),
                ([-1.0, 1.0], 0.0),
            ],
        );
        let mut a = super::super::paired_ramp_steps::tests::part("section_recess", pent);
        for (i, r) in [tri, rect].into_iter().enumerate() {
            let mut f = a.features[0].clone();
            f.id = format!("f:section_recess:{}", i + 2);
            f.faces = vec![i + 5];
            f.parameters = r.as_object().unwrap().clone();
            a.features.push(f);
        }
        a.recognition.as_mut().unwrap().refusals.push(
            serde_json::from_value(json!({
                "family": "section_recesses", "subtype": "SectionRecessRefusal",
                "faces": [27, 28, 29],
                "parameters": { "body": 0, "reason": "unsupported_support_geometry" },
            }))
            .unwrap(),
        );
        let found: Vec<(String, String)> = coverage(&a, &[])
            .into_iter()
            .map(|i| (i.code, i.message))
            .collect();
        let tail = "is not represented by Draftwright dimensions: profile has no supported \
                    drafting grammar; review and define its manufacturing section outside \
                    automatic drawing approval";
        assert_eq!(
            found,
            [
                (
                    "prismatic_pocket_requirement_unsupported".to_string(),
                    format!("recognised 5-sided blind prismatic recess 6 mm deep (1 of 1) {tail}")
                ),
                (
                    "passage_requirement_unsupported".to_string(),
                    format!("recognised 3-edge prismatic through-opening (1 of 1) {tail}")
                ),
                (
                    "section_recess_recognition_refused".to_string(),
                    "section recess recognition on body 0 refused: unsupported_support_geometry"
                        .to_string()
                ),
            ]
        );
    }

    /// Any other recess outside the grammars is `section_recess_requirement_unsupported`, named by
    /// its index and body (test_recognition_boundary_failures): here an edge-open U section whose
    /// floor end slopes; and a record that breaks quiddity's contract (a pocket with two open
    /// ends) is reported with why, where Python's lint would fail.
    #[test]
    fn other_recesses_and_invalid_records_are_reported_with_why() {
        let u = [
            ([-3.0, 10.0], 0.0),
            ([-3.0, 6.0], 0.0),
            ([3.0, 6.0], 0.0),
            ([3.0, 10.0], 0.0),
        ];
        let mut slot = recess(
            "edge_open_recess",
            "rectangular",
            Z,
            [0.0, 20.0],
            [false, true],
            false,
            &u,
        );
        slot["index"] = json!(3);
        slot["geometry"]["ends"]["low"]["surface"]["gradient"] = json!([0.5, 0.0]);
        let a = super::super::paired_ramp_steps::tests::part("section_recess", slot);
        let found = coverage(&a, &[]);
        assert_eq!(found.len(), 1);
        assert_eq!(found[0].code, "section_recess_requirement_unsupported");
        assert_eq!(
            found[0].message,
            "section recess 3 on body 0 is not represented by Draftwright dimensions: drafting \
             grammar requires perpendicular run ends; review and define its manufacturing section \
             outside automatic drawing approval"
        );
        let rect = [
            ([-1.0, -1.0], 0.0),
            ([1.0, -1.0], 0.0),
            ([1.0, 1.0], 0.0),
            ([-1.0, 1.0], 0.0),
        ];
        let open = recess(
            "pocket",
            "rectangular",
            Z,
            [0.0, 3.0],
            [true, true],
            true,
            &rect,
        );
        assert_eq!(
            grammar(&feature(open.clone())),
            Err(Refusal::Invalid(
                "pocket requires exactly one capped and one open end".into()
            ))
        );
        let found = coverage(
            &super::super::paired_ramp_steps::tests::part("section_recess", open),
            &[],
        );
        assert_eq!(found[0].code, "prismatic_pocket_requirement_unsupported");
        assert!(
            found[0].message.contains(
                "its record is not valid (pocket requires exactly one capped and one open end)"
            ),
            "{}",
            found[0].message
        );
    }
}
