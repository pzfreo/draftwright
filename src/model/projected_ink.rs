//! The part's edges and faces as compact evidence for annotation placement.
//!
//! The planner has no Part (ADR R1), so it cannot run hidden-line removal. Every curve the
//! renderer draws in an orthographic, uncut view is a projected edge (visible or hidden) or a
//! curved face's silhouette. The Analysis therefore carries each edge once, sampled to a coarse
//! chord tolerance, plus optional resolved principal curves with their visibility, the planar
//! faces by their edges (for material containment), and the curved
//! faces by what bounds their silhouettes. The planner projects these into any view itself, a
//! linear map: a superset of the drawn ink, so "clear of this" implies "clear of what is drawn".
use super::*;

/// How far a stored chord may stray from its edge, mm. Placement pads every clearance by it.
pub const TOLERANCE: f64 = 0.05;
/// Stored coordinates are rounded to this, mm (it is added to the padding too).
pub const GRID: f64 = 1e-3;

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EdgeInk {
    /// Visibility evidence for inclined bores in their own projection.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub bore_ends: BTreeMap<String, Vec<crate::auxiliary::BoreEnd>>,
    /// Exact recognition-independent dimension references, keyed by versioned fingerprint.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub references: BTreeMap<String, Vec<super::dimension_geometry::Reference>>,
    /// Resolved principal ink, preserving the renderer's visibility classification.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub resolved: Vec<ResolvedView>,
    /// Every edge of the part as a polyline in the part's frame, within [`TOLERANCE`].
    pub edges: Vec<Vec<[f64; 3]>>,
    /// Planar faces: their normal and the edges (indices into `edges`) of all their loops.
    pub planes: Vec<PlanarFace>,
    /// Non-planar faces: what bounds their silhouettes in any view.
    pub curved: Vec<CurvedFace>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ResolvedView {
    pub direction: [f64; 3],
    pub up: [f64; 3],
    pub visible: Vec<crate::output::Curve>,
    pub hidden: Vec<crate::output::Curve>,
}

// Recognition and rendering use identical kernel cameras. Retain the prepared
// pieces as well as the compact planner evidence: a second HLR of a dense
// axial view can cost much more than every label repair combined.
static PREPARED: crate::planning::BoundedMemo<Vec<Projected>> =
    crate::planning::BoundedMemo::new(4);

fn camera_key(binding: &Binding, direction: [f64; 3], up: [f64; 3]) -> [u8; 32] {
    Sha256::digest(
        serde_json::to_vec(&(binding, direction.map(f64::to_bits), up.map(f64::to_bits)))
            // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
            .expect("projection camera serializes"),
    )
    .into()
}

/// Native inspection's exact kernel pieces, before any sheet placement or crop.
/// Cuts have different geometry and must never use this uncut-camera evidence.
pub(super) fn prepared(
    binding: &Binding,
    direction: [f64; 3],
    up: [f64; 3],
) -> Option<Vec<Projected>> {
    PREPARED.get(&camera_key(binding, direction, up))
}

/// Immutable geometric evidence; no Part is retained by the planner.
pub(crate) fn resolved(
    part: &Part,
    binding: &Binding,
    names: &std::collections::BTreeSet<&str>,
) -> Vec<ResolvedView> {
    static MEMO: crate::planning::BoundedMemo<Vec<ResolvedView>> =
        crate::planning::BoundedMemo::new(4);
    let key: [u8; 32] =
        // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
        Sha256::digest(serde_json::to_vec(&(binding, names)).expect("projection inputs serialize"))
            .into();
    if let Some(ink) = MEMO.get(&key) {
        return ink;
    }
    let ink: Vec<_> = VIEWS[..3]
        .iter()
        .filter(|(name, _, _)| names.contains(name))
        .filter_map(|(_, direction, up)| {
            let view = View::new(*direction, *up)?;
            let key = camera_key(binding, *direction, *up);
            let pieces = if let Some(pieces) = PREPARED.get(&key) {
                pieces
            } else {
                let pieces = project(part, &view).ok()?;
                PREPARED.insert(key, pieces.clone());
                pieces
            };
            let mut resolved = ResolvedView {
                direction: *direction,
                up: *up,
                visible: Vec::new(),
                hidden: Vec::new(),
            };
            for piece in pieces {
                if let Some(curve) = crate::output::projected(&piece) {
                    if piece.visible {
                        resolved.visible.push(curve);
                    } else {
                        resolved.hidden.push(curve);
                    }
                }
            }
            Some(resolved)
        })
        .collect();
    MEMO.insert(key, ink.clone());
    ink
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PlanarFace {
    pub normal: [f64; 3],
    pub edges: Vec<usize>,
}

/// A curved face's box, and for a cylinder its axis and radius: seen square to its axis its
/// silhouettes are the two generators at ± radius, along the box's sides. Any other curved face
/// (and a cylinder seen obliquely) is a forbidden box in every view.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CurvedFace {
    pub bounds: [[f64; 3]; 2],
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cylinder: Option<Cylinder>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Cylinder {
    pub origin: [f64; 3],
    pub axis: [f64; 3],
    pub radius: f64,
}

fn round(p: [f64; 3]) -> [f64; 3] {
    p.map(|x| (x / GRID).round() * GRID + 0.0)
}

/// Adaptive chord sampling of one edge to [`TOLERANCE`].
fn sample(e: &haecceity::brep::Edge) -> Vec<[f64; 3]> {
    use haecceity::geom::Curve as C;
    if matches!(e.curve, C::Line { .. }) {
        return vec![round(e.start), round(e.end)];
    }
    let (a, b) =
        haecceity::sampling::edge_interval(&e.curve, e.start, e.end, e.same_sense, e.is_closed());
    let n = match e.curve {
        C::Nurbs(_) => 16,
        _ => ((b - a).abs() / std::f64::consts::FRAC_PI_2 * 4.0)
            .ceil()
            .max(2.0) as usize,
    };
    fn refine(
        c: &haecceity::geom::Curve,
        lo: (f64, [f64; 3]),
        hi: (f64, [f64; 3]),
        depth: usize,
        out: &mut Vec<[f64; 3]>,
    ) {
        let tm = 0.5 * (lo.0 + hi.0);
        let pm = c.value(tm);
        let mid = [0, 1, 2].map(|k| 0.5 * (lo.1[k] + hi.1[k]));
        let d = (0..3).map(|k| (pm[k] - mid[k]).powi(2)).sum::<f64>().sqrt();
        if depth < 12 && d > TOLERANCE {
            refine(c, lo, (tm, pm), depth + 1, out);
            refine(c, (tm, pm), hi, depth + 1, out);
        } else {
            out.push(round(hi.1));
        }
    }
    let mut out = vec![round(e.start)];
    let mut last = (a, e.curve.value(a));
    for i in 1..=n {
        let t = a + (b - a) * i as f64 / n as f64;
        let p = (t, e.curve.value(t));
        refine(&e.curve, last, p, 0, &mut out);
        last = p;
    }
    // Invariant: sample count is at least two before out is constructed.
    *out.last_mut().expect("two samples") = round(e.end);
    out
}

/// The part's edge evidence. Deterministic: edges and faces in the part's own order. Only the
/// [`drawn_faces`](crate::quiddity_evidence::drawn_faces) and their edges are ink; any other
/// edge keeps its index with no samples.
pub(super) fn evidence(part: &Part) -> EdgeInk {
    use haecceity::geom::Surface;
    let drawn = crate::quiddity_evidence::drawn_faces(part);
    let mut inked = vec![false; part.edges.len()];
    for &f in &drawn {
        for e in part.face_edges(f) {
            inked[e] = true;
        }
    }
    let edges = part
        .edges
        .iter()
        .zip(&inked)
        .map(|(e, &inked)| if inked { sample(e) } else { Vec::new() })
        .collect();
    let mut planes = Vec::new();
    let mut curved = Vec::new();
    for i in drawn {
        let face = &part.faces[i];
        let b = part.face_bounds(i);
        let bounds = [round(b.min), round(b.max)];
        match &face.surface {
            Surface::Plane { frame } => {
                if part.outer_loop(i).is_some() {
                    let mut edges: Vec<usize> = face
                        .loops
                        .iter()
                        .flat_map(|l| l.edges.iter().map(|&(e, _)| e))
                        .collect();
                    edges.sort_unstable();
                    edges.dedup();
                    planes.push(PlanarFace {
                        normal: frame.z,
                        edges,
                    });
                }
            }
            Surface::Cylinder { frame, radius } => curved.push(CurvedFace {
                bounds,
                cylinder: Some(Cylinder {
                    origin: frame.origin,
                    axis: frame.z,
                    radius: *radius,
                }),
            }),
            _ => curved.push(CurvedFace {
                bounds,
                cylinder: None,
            }),
        }
    }
    EdgeInk {
        bore_ends: BTreeMap::new(),
        resolved: Vec::new(),
        references: super::dimension_geometry::evidence(part),
        edges,
        planes,
        curved,
    }
}
