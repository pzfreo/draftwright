//! Exact, model-space references for dimensions independent of recognition.
//! Reference keys are versioned fingerprints, never traversal indices or paper positions.
use std::collections::BTreeMap;

use haecceity::{
    Part,
    geom::{Curve, Surface},
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use super::Analysis;

type P = [f64; 3];
/// Discovery metadata only; excluded from the v1 geometry fingerprint.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct Membership {
    /// Current Analysis feature IDs owning any incident face; empty when unrecognised.
    #[serde(default)]
    pub features: Vec<String>,
    /// Incident stock-face indices, as listed in Analysis.stock_faces.
    #[serde(default)]
    pub stock_faces: Vec<usize>,
    /// Named stock sides from the same resolver as Spec faces: bottom/top/left/right/front/back.
    #[serde(default)]
    pub stock_sides: Vec<String>,
    /// Incident B-rep face indices (one for a face record).
    #[serde(default)]
    pub faces: Vec<usize>,
    /// Every boundary edge, including curved edges without a dimension reference.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub edges: Option<Vec<BoundaryEdge>>,
    /// Boundary vertex reference keys, sorted and deduplicated.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub vertices: Option<Vec<String>>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BoundaryEdge {
    /// Index in Analysis.edge_ink.edges; a discovery address, never a Spec reference.
    pub index: usize,
    /// Present for straight edges; copy this full key into a dimension's between.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reference: Option<String>,
    /// Full reference keys for the edge's endpoints, including a closed edge's seam vertex.
    pub vertices: [String; 2],
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", from = "ReferenceRead")]
pub enum Reference {
    Plane {
        #[serde(flatten)]
        membership: Membership,
        origin: P,
        normal: P,
        bounds: [P; 2],
    },
    Cylinder {
        #[serde(flatten)]
        membership: Membership,
        origin: P,
        axis: P,
        radius: f64,
        bounds: [P; 2],
        sites: Vec<P>,
    },
    Sphere {
        #[serde(flatten)]
        membership: Membership,
        centre: P,
        radius: f64,
        bounds: [P; 2],
        sites: Vec<P>,
    },
    Edge {
        #[serde(flatten)]
        membership: Membership,
        ends: [P; 2],
    },
    Vertex {
        #[serde(flatten)]
        membership: Membership,
        point: P,
    },
}

// Serde does not support flatten with deny_unknown_fields. Keep membership flat on
// this read shape so unknown fields (including geometry from another variant) are
// rejected, while the public Reference API and serialized document remain unchanged.
#[derive(Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
enum ReferenceRead {
    Plane {
        #[serde(default)]
        features: Vec<String>,
        #[serde(default)]
        stock_faces: Vec<usize>,
        #[serde(default)]
        stock_sides: Vec<String>,
        #[serde(default)]
        faces: Vec<usize>,
        #[serde(default)]
        edges: Option<Vec<BoundaryEdge>>,
        #[serde(default)]
        vertices: Option<Vec<String>>,
        origin: P,
        normal: P,
        bounds: [P; 2],
    },
    Cylinder {
        #[serde(default)]
        features: Vec<String>,
        #[serde(default)]
        stock_faces: Vec<usize>,
        #[serde(default)]
        stock_sides: Vec<String>,
        #[serde(default)]
        faces: Vec<usize>,
        #[serde(default)]
        edges: Option<Vec<BoundaryEdge>>,
        #[serde(default)]
        vertices: Option<Vec<String>>,
        origin: P,
        axis: P,
        radius: f64,
        bounds: [P; 2],
        sites: Vec<P>,
    },
    Sphere {
        #[serde(default)]
        features: Vec<String>,
        #[serde(default)]
        stock_faces: Vec<usize>,
        #[serde(default)]
        stock_sides: Vec<String>,
        #[serde(default)]
        faces: Vec<usize>,
        #[serde(default)]
        edges: Option<Vec<BoundaryEdge>>,
        #[serde(default)]
        vertices: Option<Vec<String>>,
        centre: P,
        radius: f64,
        bounds: [P; 2],
        sites: Vec<P>,
    },
    Edge {
        #[serde(default)]
        features: Vec<String>,
        #[serde(default)]
        stock_faces: Vec<usize>,
        #[serde(default)]
        stock_sides: Vec<String>,
        #[serde(default)]
        faces: Vec<usize>,
        #[serde(default)]
        edges: Option<Vec<BoundaryEdge>>,
        #[serde(default)]
        vertices: Option<Vec<String>>,
        ends: [P; 2],
    },
    Vertex {
        #[serde(default)]
        features: Vec<String>,
        #[serde(default)]
        stock_faces: Vec<usize>,
        #[serde(default)]
        stock_sides: Vec<String>,
        #[serde(default)]
        faces: Vec<usize>,
        #[serde(default)]
        edges: Option<Vec<BoundaryEdge>>,
        #[serde(default)]
        vertices: Option<Vec<String>>,
        point: P,
    },
}

impl From<ReferenceRead> for Reference {
    fn from(value: ReferenceRead) -> Self {
        match value {
            ReferenceRead::Plane {
                features,
                stock_faces,
                stock_sides,
                faces,
                edges,
                vertices,
                origin,
                normal,
                bounds,
            } => Self::Plane {
                membership: Membership {
                    features,
                    stock_faces,
                    stock_sides,
                    faces,
                    edges,
                    vertices,
                },
                origin,
                normal,
                bounds,
            },
            ReferenceRead::Cylinder {
                features,
                stock_faces,
                stock_sides,
                faces,
                edges,
                vertices,
                origin,
                axis,
                radius,
                bounds,
                sites,
            } => Self::Cylinder {
                membership: Membership {
                    features,
                    stock_faces,
                    stock_sides,
                    faces,
                    edges,
                    vertices,
                },
                origin,
                axis,
                radius,
                bounds,
                sites,
            },
            ReferenceRead::Sphere {
                features,
                stock_faces,
                stock_sides,
                faces,
                edges,
                vertices,
                centre,
                radius,
                bounds,
                sites,
            } => Self::Sphere {
                membership: Membership {
                    features,
                    stock_faces,
                    stock_sides,
                    faces,
                    edges,
                    vertices,
                },
                centre,
                radius,
                bounds,
                sites,
            },
            ReferenceRead::Edge {
                features,
                stock_faces,
                stock_sides,
                faces,
                edges,
                vertices,
                ends,
            } => Self::Edge {
                membership: Membership {
                    features,
                    stock_faces,
                    stock_sides,
                    faces,
                    edges,
                    vertices,
                },
                ends,
            },
            ReferenceRead::Vertex {
                features,
                stock_faces,
                stock_sides,
                faces,
                edges,
                vertices,
                point,
            } => Self::Vertex {
                membership: Membership {
                    features,
                    stock_faces,
                    stock_sides,
                    faces,
                    edges,
                    vertices,
                },
                point,
            },
        }
    }
}

impl Reference {
    pub fn membership(&self) -> &Membership {
        match self {
            Self::Plane { membership, .. }
            | Self::Cylinder { membership, .. }
            | Self::Sphere { membership, .. }
            | Self::Edge { membership, .. }
            | Self::Vertex { membership, .. } => membership,
        }
    }
    fn membership_mut(&mut self) -> &mut Membership {
        match self {
            Self::Plane { membership, .. }
            | Self::Cylinder { membership, .. }
            | Self::Sphere { membership, .. }
            | Self::Edge { membership, .. }
            | Self::Vertex { membership, .. } => membership,
        }
    }
}

fn geometry_value(r: &Reference) -> serde_json::Value {
    let mut value = serde_json::to_value(r).expect("finite geometry");
    let fields = value.as_object_mut().expect("reference object");
    for key in [
        "features",
        "stock_faces",
        "stock_sides",
        "faces",
        "edges",
        "vertices",
    ] {
        fields.remove(key);
    }
    value
}

/// Refresh discovery after native/bridge recognition or authored recognition corrections.
/// Geometry and fingerprints never depend on recognised ownership.
pub(crate) fn memberships(analysis: &mut Analysis) {
    let sides: Vec<_> = crate::spec_gdt::SIDES
        .iter()
        .filter_map(|(name, ..)| {
            crate::spec_gdt::named_faces(analysis, name)
                .ok()
                .map(|faces| (*name, faces))
        })
        .collect();
    let Some(ink) = &mut analysis.edge_ink else {
        return;
    };
    for r in ink.references.values_mut().flatten() {
        let m = r.membership_mut();
        m.features = analysis
            .features
            .iter()
            .filter(|f| f.faces.iter().any(|i| m.faces.contains(i)))
            .map(|f| f.id.clone())
            .collect();
        m.features.sort();
        m.features.dedup();
        m.stock_faces = m
            .faces
            .iter()
            .filter(|i| analysis.stock_faces.contains(i))
            .copied()
            .collect();
        m.stock_sides = sides
            .iter()
            .filter(|(_, faces)| faces.iter().any(|i| m.stock_faces.contains(i)))
            .map(|(name, _)| name.to_string())
            .collect();
    }
}

pub(super) fn evidence(part: &Part) -> BTreeMap<String, Vec<Reference>> {
    // Multiple coincident entities deliberately remain ambiguous instead of picking an index.
    let mut out: BTreeMap<String, Vec<Reference>> = BTreeMap::new();
    fn insert(
        out: &mut BTreeMap<String, Vec<Reference>>,
        prefix: &str,
        r: Reference,
        boundary: Vec<String>,
    ) -> String {
        // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
        let mut value =
            serde_json::to_value((geometry_value(&r), boundary)).expect("finite geometry");
        fn quantize(v: &mut serde_json::Value) {
            match v {
                serde_json::Value::Number(n) => {
                    if let Some(f) = n.as_f64() {
                        *v = serde_json::json!((f * 1e6).round() / 1e6 + 0.0);
                    }
                }
                serde_json::Value::Array(a) => a.iter_mut().for_each(quantize),
                serde_json::Value::Object(o) => o.values_mut().for_each(quantize),
                _ => {}
            }
        }
        quantize(&mut value);
        // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
        let hash = Sha256::digest(serde_json::to_vec(&value).expect("geometry serializes"));
        let hash: String = hash.iter().map(|b| format!("{b:02x}")).collect();
        let key = format!("{prefix}:v1:{hash}");
        out.entry(key.clone()).or_default().push(r);
        key
    }
    let vertices: BTreeMap<_, _> = part
        .edges
        .iter()
        .flat_map(|e| [(e.vertices.0, e.start), (e.vertices.1, e.end)])
        .collect();
    let mut incident = vec![Vec::new(); part.edges.len()];
    for (i, f) in part.faces.iter().enumerate() {
        for (edge, _) in f.loops.iter().flat_map(|l| &l.edges) {
            incident[*edge].push(i);
        }
    }
    for faces in &mut incident {
        faces.sort_unstable();
        faces.dedup();
    }
    let mut vertex_faces: BTreeMap<_, Vec<usize>> = BTreeMap::new();
    for (i, e) in part.edges.iter().enumerate() {
        for v in [e.vertices.0, e.vertices.1] {
            vertex_faces.entry(v).or_default().extend(&incident[i]);
        }
    }
    let mut vertex_keys = BTreeMap::new();
    for (v, p) in &vertices {
        let mut faces = vertex_faces.remove(v).unwrap_or_default();
        faces.sort_unstable();
        faces.dedup();
        let key = insert(
            &mut out,
            "vertex",
            Reference::Vertex {
                point: *p,
                membership: Membership {
                    faces,
                    ..Default::default()
                },
            },
            vec![],
        );
        vertex_keys.insert(*v, key);
    }
    let mut edge_keys = BTreeMap::new();
    for (i, e) in part.edges.iter().enumerate() {
        if matches!(e.curve, Curve::Line { .. }) {
            let mut ends = [e.start, e.end];
            // Invariant: Kernel-loaded edge endpoints are finite; no caller Analysis is used for geometry extraction.
            ends.sort_by(|a, b| a.partial_cmp(b).expect("finite endpoints"));
            let key = insert(
                &mut out,
                "edge",
                Reference::Edge {
                    ends,
                    membership: Membership {
                        faces: incident[i].clone(),
                        ..Default::default()
                    },
                },
                vec![],
            );
            edge_keys.insert(i, key);
        }
    }
    for (i, f) in part.faces.iter().enumerate() {
        if !matches!(
            f.surface,
            Surface::Plane { .. } | Surface::Cylinder { .. } | Surface::Sphere { .. }
        ) {
            continue;
        }
        let needs_sites = !matches!(f.surface, Surface::Plane { .. });
        let b = part.face_bounds(i);
        let bounds = [b.min, b.max];
        let mut boundary = Vec::new();
        let mut sites = Vec::new();
        for (e, _) in f.loops.iter().flat_map(|l| &l.edges) {
            let edge = &part.edges[*e];
            let mut ends = [edge.start, edge.end];
            // Invariant: Kernel-loaded edge endpoints are finite; no caller Analysis is used for geometry extraction.
            ends.sort_by(|a, b| a.partial_cmp(b).expect("finite endpoints"));
            boundary.push(
                serde_json::to_string(&ends.map(|p| p.map(|v| (v * 1e6).round() / 1e6 + 0.0)))
                    // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
                    .unwrap(),
            );
            if needs_sites {
                sites.extend(ends);
                if !matches!(edge.curve, Curve::Line { .. }) {
                    // Sample in a canonical direction too: reversed traversal of a circle
                    // otherwise visits equivalent parameters with different last-bit sines.
                    let sense = if edge.is_closed() {
                        true
                    } else if ends[0] == edge.start {
                        edge.same_sense
                    } else {
                        !edge.same_sense
                    };
                    let (a, b) = haecceity::sampling::edge_interval(
                        &edge.curve,
                        ends[0],
                        ends[1],
                        sense,
                        edge.is_closed(),
                    );
                    sites.extend((1..16).map(|j| edge.curve.value(a + (b - a) * j as f64 / 16.0)));
                }
            }
        }
        boundary.sort();
        // Invariant: sites consists of finite kernel endpoints and evaluated curve points.
        sites.sort_by(|a, b| a.partial_cmp(b).expect("finite sites"));
        sites.dedup();
        // A boundaryless sphere still has exact surface sites.
        let r = match &f.surface {
            Surface::Plane { frame } => {
                // Invariant: Kernel planes carry normalized frame axes.
                let normal = unit(frame.z).expect("plane normal");
                let normal = canonical(normal);
                let origin = mul(normal, dot(frame.origin, normal));
                Reference::Plane {
                    membership: Membership::default(),
                    origin,
                    normal,
                    bounds,
                }
            }
            Surface::Cylinder { frame, radius } => {
                // Invariant: Kernel cylinders carry normalized frame axes.
                let axis = canonical(unit(frame.z).expect("cylinder axis"));
                let origin = sub(frame.origin, mul(axis, dot(frame.origin, axis)));
                Reference::Cylinder {
                    membership: Membership::default(),
                    origin,
                    axis,
                    radius: *radius,
                    bounds,
                    sites,
                }
            }
            Surface::Sphere { frame, radius } => {
                if sites.is_empty() {
                    sites.extend((0..3).flat_map(|k| {
                        [-1.0, 1.0].map(move |s| {
                            let mut p = frame.origin;
                            p[k] += s * radius;
                            p
                        })
                    }));
                }
                Reference::Sphere {
                    membership: Membership::default(),
                    centre: frame.origin,
                    radius: *radius,
                    bounds,
                    sites,
                }
            }
            _ => continue,
        };
        let mut r = r;
        let mut indices: Vec<_> = f
            .loops
            .iter()
            .flat_map(|l| &l.edges)
            .map(|(e, _)| *e)
            .collect();
        indices.sort_unstable();
        indices.dedup();
        let edges: Vec<_> = indices
            .iter()
            .map(|i| {
                let e = &part.edges[*i];
                BoundaryEdge {
                    index: *i,
                    reference: edge_keys.get(i).cloned(),
                    vertices: [
                        vertex_keys[&e.vertices.0].clone(),
                        vertex_keys[&e.vertices.1].clone(),
                    ],
                }
            })
            .collect();
        let mut vertices: Vec<_> = edges
            .iter()
            .flat_map(|e| e.vertices.iter().cloned())
            .collect();
        vertices.sort();
        vertices.dedup();
        *r.membership_mut() = Membership {
            faces: vec![i],
            edges: Some(edges),
            vertices: Some(vertices),
            ..Default::default()
        };
        insert(&mut out, "face", r, boundary);
    }
    out
}

pub(crate) fn sub(a: P, b: P) -> P {
    [0, 1, 2].map(|k| a[k] - b[k])
}
fn add(a: P, b: P) -> P {
    [0, 1, 2].map(|k| a[k] + b[k])
}
fn mul(a: P, s: f64) -> P {
    a.map(|v| v * s)
}
pub(crate) fn dot(a: P, b: P) -> f64 {
    (0..3).map(|k| a[k] * b[k]).sum()
}
pub(crate) fn unit(a: P) -> Option<P> {
    let n = dot(a, a).sqrt();
    (n > 1e-10).then(|| mul(a, 1.0 / n))
}
fn canonical(a: P) -> P {
    if a.iter().find(|v| v.abs() > 1e-10).is_some_and(|v| *v < 0.0) {
        mul(a, -1.0)
    } else {
        a
    }
}
fn midpoint(b: [P; 2]) -> P {
    mul(add(b[0], b[1]), 0.5)
}

#[derive(Clone, Debug)]
pub struct Measurement {
    pub value: f64,
    pub witnesses: Option<[P; 2]>,
    pub angle: Option<crate::dims::angular::Angle>,
    pub sites: Vec<P>,
}

impl Analysis {
    /// A transverse analytic feature-axis station, independent of profile/notch evidence.
    pub(crate) fn axis_station(&self, id: &str, k: usize) -> Option<f64> {
        match self.dimension_reference(&format!("axis:{id}")).ok()? {
            Reference::Cylinder { origin, axis, .. } if axis[k].abs() < 1e-6 => Some(origin[k]),
            Reference::Edge { ends, .. } if (ends[0][k] - ends[1][k]).abs() < 1e-6 => {
                Some(0.5 * (ends[0][k] + ends[1][k]))
            }
            _ => None,
        }
    }

    pub(crate) fn scheme_station(&self, key: &str, k: usize) -> Result<[f64; 3], String> {
        let a = self;
        if key == "min" || key == "max" {
            let mut p = a.envelope[0];
            p[k] = a.envelope[usize::from(key == "max")][k];
            return Ok(p);
        }
        if let Some((end, id)) = key.split_once(':')
            && ["start", "end"].contains(&end)
        {
            let f = a.features.iter().find(|f| f.id == id).ok_or_else(|| {
                format!("Unknown feature {id}; choose a current Analysis feature ID.")
            })?;
            let b = crate::lint::turned::band(&crate::bridge::holes::frame(a), f)?;
            if b.axis != k {
                return Err(format!(
                    "{key} is not a shoulder on the chosen axis; choose the feature's turning axis."
                ));
            }
            let mut p = b.on;
            p[k] = if end == "start" { b.lo } else { b.hi };
            // Witnesses start on the outside of the profile, rather than its centre line.
            // Invariant: Excluding one axis from 0..3 leaves two radial axes.
            let radial = (0..3).find(|&j| j != k).unwrap();
            p[radial] += b.diameter / 2.;
            return Ok(p);
        }
        if let Some(n) = crate::notches::requirements(a).iter().find(|n| n.id == key) {
            return Ok(n.mouth);
        }
        if let Some(f) = a.features.iter().find(|f| f.id == key) {
            if f.kind == "oriented_chamfer" {
                return crate::oriented_chamfers::read(a, f).map(|r| r.corner);
            }
            if f.kind == "turned_step" {
                return Err(format!(
                    "{key} has two shoulders; choose start:{key} or end:{key}."
                ));
            }
            if matches!(f.kind.as_str(), "slot" | "slot_pattern") {
                return Ok(f.fingerprint.position);
            }
            // Trimmed cylindrical bounds need not be centred on the analytic feature axis.
            let axis_key = format!("axis:{key}");
            if matches!(a.dimension_reference(&axis_key),Ok(Reference::Cylinder {axis,..}) if axis[k].abs()<1e-8)
            {
                return a.scheme_station(&axis_key, k);
            }
            // A planar datum feature must prove one plane perpendicular to the measuring axis.
            let planes: Vec<_> = f
                .faces
                .iter()
                .filter_map(|&i| a.face_attachments.get(i))
                .filter_map(|f| f.bounds.zip(f.normal))
                .filter(|(_, n)| n[k].abs() > 1. - 1e-8)
                .map(|(b, _)| [0, 1, 2].map(|j| (b[j] + b[j + 3]) / 2.))
                .collect();
            if let Some(p) = planes.first() {
                if planes.iter().any(|q| (p[k] - q[k]).abs() > 1e-6) {
                    return Err(format!(
                        "{key} has multiple datum planes; choose a face:v1:<fingerprint> instead."
                    ));
                }
                return Ok(*p);
            }
            if f.fingerprint.axis.is_some_and(|axis| axis[k].abs() > 1e-8) {
                return Err(format!(
                    "{key}'s axis does not define a station across {}; choose a perpendicular axis or planar face.",
                    ["x", "y", "z"][k]
                ));
            }
            return Ok(f.fingerprint.position);
        }
        match a.dimension_reference(key)? {
            Reference::Plane {
                origin,
                normal,
                bounds,
                ..
            } if normal[k].abs() > 1. - 1e-8 => {
                let mut p = [0, 1, 2].map(|j| (bounds[0][j] + bounds[1][j]) / 2.);
                p[k] = origin[k];
                Ok(p)
            }
            Reference::Vertex { point, .. } => Ok(point),
            Reference::Edge { ends, .. } if (ends[0][k] - ends[1][k]).abs() < 1e-8 => {
                Ok([0, 1, 2].map(|j| (ends[0][j] + ends[1][j]) / 2.))
            }
            Reference::Cylinder {
                origin,
                axis,
                bounds,
                ..
            } if axis[k].abs() < 1e-8 => {
                let centre = [0, 1, 2].map(|j| (bounds[0][j] + bounds[1][j]) / 2.);
                let t: f64 = (0..3).map(|j| (centre[j] - origin[j]) * axis[j]).sum();
                Ok([0, 1, 2].map(|j| origin[j] + t * axis[j]))
            }
            _ => Err(format!(
                "{key} does not define one station on this axis; choose a perpendicular planar face, a transverse cylinder axis, vertex or constant-axis edge."
            )),
        }
    }

    /// Inventory to copy into an authored dimension. Empty in older Analyses: analyse again.
    pub fn dimension_references(&self) -> &BTreeMap<String, Vec<Reference>> {
        static EMPTY: std::sync::OnceLock<BTreeMap<String, Vec<Reference>>> =
            std::sync::OnceLock::new();
        self.edge_ink
            .as_ref()
            .map(|e| &e.references)
            .unwrap_or_else(|| EMPTY.get_or_init(BTreeMap::new))
    }
    /// Resolve a planar dimension reference to its unique trimmed physical face.
    /// Coplanar faces with different bounds remain distinct stations in lint.
    pub fn dimension_reference_face(&self, key: &str, axis: usize) -> Option<usize> {
        let Reference::Plane { bounds, normal, .. } = self.dimension_reference(key).ok()? else {
            return None;
        };
        let mut faces = self
            .face_attachments
            .iter()
            .enumerate()
            .filter(|(_, face)| {
                face.surface.as_deref() == Some("plane")
                    && face.axis == Some(axis)
                    && face.normal.is_some_and(|n| {
                        ((0..3).map(|k| n[k] * normal[k]).sum::<f64>().abs() - 1.).abs() < 1e-6
                    })
                    && face.bounds.is_some_and(|b| {
                        (0..3).all(|k| {
                            (b[k] - bounds[0][k]).abs() < 1e-6
                                && (b[k + 3] - bounds[1][k]).abs() < 1e-6
                        })
                    })
            });
        let (i, _) = faces.next()?;
        faces.next().is_none().then_some(i)
    }

    /// Resolve an explicit cylindrical face axis to its unique trimmed physical face.
    /// Bounds and surface identity, rather than discovery membership, establish ownership.
    pub fn dimension_reference_axis_face(&self, key: &str) -> Option<usize> {
        let face_key = key.strip_prefix("axis:")?;
        let Reference::Cylinder { bounds, .. } = self.dimension_reference(face_key).ok()? else {
            return None;
        };
        let mut faces = self
            .face_attachments
            .iter()
            .enumerate()
            .filter(|(_, face)| {
                face.surface.as_deref() == Some("cylinder")
                    && face.bounds.is_some_and(|b| {
                        (0..3).all(|k| {
                            (b[k] - bounds[0][k]).abs() < 1e-6
                                && (b[k + 3] - bounds[1][k]).abs() < 1e-6
                        })
                    })
            });
        let (i, _) = faces.next()?;
        faces.next().is_none().then_some(i)
    }

    pub fn dimension_reference(&self, key: &str) -> Result<Reference, String> {
        if self.dimension_references().is_empty() {
            return Err("This Analysis records no dimension references; analyse the STEP again, then copy stable keys from Analysis.edge_ink.references.".into());
        }
        if let Some(id) = key.strip_prefix("axis:") {
            let candidates: Vec<_> = if id.starts_with("face:") {
                vec![self.dimension_reference(id).map_err(|e| format!(
                    "{e} For a cylinder axis use axis:<key from Analysis.edge_ink.references>: prefix the complete face:v1:… key with axis: only once."
                ))?]
            } else {
                let f=self.features.iter().find(|f| f.id==id).ok_or_else(|| format!("Unknown axis feature {id}; choose axis:<key from Analysis.edge_ink.references> (prefix the complete cylindrical face:v1:… key with axis:)."))?;
                f.faces
                    .iter()
                    .filter_map(|&i| {
                        let a = self.face_attachments.get(i)?;
                        let bounds = a.bounds?;
                        self.dimension_references()
                            .values()
                            .flatten()
                            .find(|r| match r {
                                Reference::Cylinder { bounds: b, .. } => (0..3).all(|k| {
                                    (b[0][k] - bounds[k]).abs() < 1e-6
                                        && (b[1][k] - bounds[k + 3]).abs() < 1e-6
                                }),
                                _ => false,
                            })
                            .cloned()
                    })
                    .collect()
            };
            let mut lines = Vec::new();
            for r in candidates {
                if let Reference::Cylinder {
                    origin,
                    axis,
                    bounds,
                    ..
                } = r
                {
                    let c = midpoint(bounds);
                    let p = add(origin, mul(axis, dot(sub(c, origin), axis)));
                    lines.push(Reference::Edge {
                        membership: Membership::default(),
                        ends: [sub(p, axis), add(p, axis)],
                    });
                }
            }
            let first = lines
                .first()
                .ok_or("An axis needs a cylindrical face; choose axis:<key from Analysis.edge_ink.references> (for example axis:face:v1:…).")?;
            if lines.iter().any(|r| !coaxial(first, r)) {
                return Err("The feature has several cylinder axes; choose axis:<key from Analysis.edge_ink.references> (for example axis:face:v1:…) for one cylinder.".into());
            }
            return Ok(first.clone());
        }
        match self.dimension_references().get(key).map(Vec::as_slice) {
            Some([r]) => Ok(r.clone()),
            Some(_) => Err(format!(
                "Reference {key} is ambiguous (coincident entities); choose a unique face, edge or vertex fingerprint."
            )),
            None => Err(format!(
                "Unknown reference {key}; copy a face:v1:…, edge:v1:… or vertex:v1:… key from Analysis.edge_ink.references; analyse the STEP again if absent."
            )),
        }
    }
    /// Measure only geometry and a quantity request. Used independently by lint from ink claims.
    pub fn measure_references(
        &self,
        refs: &[String],
        kind: &str,
        along: Option<&str>,
    ) -> Result<Measurement, String> {
        let r: Vec<_> = refs
            .iter()
            .map(|s| self.dimension_reference(s))
            .collect::<Result<_, _>>()?;
        let mut out = Measurement {
            value: 0.0,
            witnesses: None,
            angle: None,
            sites: vec![],
        };
        if along.is_some_and(|v| kind != "linear" || !["x", "y", "z", "true"].contains(&v)) {
            return Err("along is x, y, z or true for a linear distance only.".into());
        }
        match (kind, r.as_slice()) {
            ("diameter" | "radius", [Reference::Cylinder { radius, sites, .. } | Reference::Sphere { radius, sites, .. }]) => {
                out.value = radius * if kind == "diameter" { 2.0 } else { 1.0 };
                out.sites = sites.clone();
            }
            ("angle", [a, b]) => {
                let (pa, da) = direction(a)?;
                let (pb, db) = direction(b)?;
                if matches!(a, Reference::Plane { .. }) != matches!(b, Reference::Plane { .. }) {
                    return Err("An angle needs two planar faces or two straight edges.".into());
                }
                let angle = if matches!(a, Reference::Plane { .. }) {
                    crate::dims::angular::from_planes([(pa, da), (pb, db)], [pa, pb])
                        .map_err(String::from)?
                } else {
                    let coupling = dot(da, db);
                    let determinant = 1.0 - coupling * coupling;
                    if determinant <= 1e-12 {
                        return Err("Parallel edges have no angular sector; choose nonparallel straight edges.".into());
                    }
                    let delta = sub(pb, pa);
                    let first = (dot(delta, da) - coupling * dot(delta, db)) / determinant;
                    let second = coupling * first - dot(delta, db);
                    let vertex = add(pa, mul(da, first));
                    if dot(sub(vertex, add(pb, mul(db, second))), sub(vertex, add(pb, mul(db, second)))) > 1e-12 {
                        return Err("Skew edges do not meet; choose coplanar straight edges.".into());
                    }
                    let witness = |r: &Reference| {
                        // Invariant: candidates contains only Edge references, filtered in the match above.
                        let Reference::Edge { ends, .. } = r else { unreachable!() };
                        *ends.iter().max_by(|a, b| {
                            dot(sub(**a, vertex), sub(**a, vertex)).total_cmp(&dot(sub(**b, vertex), sub(**b, vertex)))
                        // Invariant: The candidate endpoints array has exactly two entries.
                        }).expect("two endpoints")
                    };
                    crate::dims::angular::Angle { vertex, first: witness(a), second: witness(b) }
                };
                out.value = angle.degrees().ok_or("The angular supports are degenerate; choose distinct half-rays.")?;
                out.angle = Some(angle);
            }
            ("linear", [a, b]) => {
                let axis = ["x", "y", "z"].iter().position(|s| Some(*s) == along);
                let points = if let Some(k) = axis {
                    let pa = station(a, k)?;
                    let pb = station(b, k)?;
                    [pa, pb]
                } else {
                    closest(a, b)?
                };
                out.value = if let Some(k) = axis {
                    (points[1][k] - points[0][k]).abs()
                } else {
                    let delta = sub(points[1], points[0]);
                    dot(delta, delta).sqrt()
                };
                out.witnesses = Some(points);
            }
            _ => return Err("Use linear/angle with two references, or diameter/radius with one cylindrical or spherical face.".into()),
        }
        if !out.value.is_finite() || out.value <= 1e-7 {
            return Err("The requested quantity is zero or degenerate; choose distinct, nonintersecting supports (or nonparallel angular references).".into());
        }
        Ok(out)
    }
}
fn direction(r: &Reference) -> Result<(P, P), String> {
    match r {
        Reference::Plane {
            origin,
            normal,
            bounds,
            ..
        } => {
            let c = midpoint(*bounds);
            Ok((sub(c, mul(*normal, dot(sub(c, *origin), *normal))), *normal))
        }
        Reference::Edge { ends, .. } => Ok((
            ends[0],
            unit(sub(ends[1], ends[0])).ok_or("Degenerate edge")?,
        )),
        _ => Err(
            "Use planar faces or straight edges for an angle; use axis: for a cylinder's axis."
                .into(),
        ),
    }
}
fn coaxial(a: &Reference, b: &Reference) -> bool {
    let (Ok((pa, da)), Ok((pb, db))) = (direction(a), direction(b)) else {
        return false;
    };
    dot(da, db).abs() > 1.0 - 1e-8
        && dot(
            sub(sub(pb, pa), mul(da, dot(sub(pb, pa), da))),
            sub(sub(pb, pa), mul(da, dot(sub(pb, pa), da))),
        ) < 1e-10
}
fn station(r: &Reference, k: usize) -> Result<P, String> {
    match r {
        Reference::Vertex { point, .. } => Ok(*point),
        Reference::Edge { ends, .. } if (ends[0][k] - ends[1][k]).abs() < 1e-7 => {
            Ok(midpoint(*ends))
        }
        Reference::Plane {
            origin,
            normal,
            bounds,
            ..
        } if normal[k].abs() > 1.0 - 1e-8 => {
            let mut p = midpoint(*bounds);
            p[k] = origin[k];
            Ok(p)
        }
        _ => Err(format!(
            "This reference has no unique {} station; choose a perpendicular plane, edge, vertex or axis, or use along: true.",
            ["x", "y", "z"][k]
        )),
    }
}
fn closest(a: &Reference, b: &Reference) -> Result<[P; 2], String> {
    if let Reference::Plane { origin, normal, .. } = a {
        let p = match b {
            Reference::Vertex { point, .. } => *point,
            Reference::Edge { ends, .. } if dot(sub(ends[1], ends[0]), *normal).abs() < 1e-8 => midpoint(*ends),
            Reference::Plane { normal: n, bounds, .. } if dot(*n, *normal).abs() > 1.0-1e-8 => midpoint(*bounds),
            _ => return Err("Intersecting or unsupported supports have no nonzero true distance; use parallel planes, points, straight edges or axes.".into()),
        };
        return Ok([sub(p, mul(*normal, dot(sub(p, *origin), *normal))), p]);
    }
    if matches!(b, Reference::Plane { .. }) {
        let [p, q] = closest(b, a)?;
        return Ok([q, p]);
    }
    let line = |r: &Reference| -> Result<(P, Option<P>), String> {
        match r {
            Reference::Vertex { point, .. } => Ok((*point, None)),
            Reference::Edge { ends, .. } => Ok((midpoint(*ends), unit(sub(ends[1], ends[0])))),
            _ => Err(
                "Use axis:<key from Analysis.edge_ink.references> (for example axis:face:v1:…) for a cylinder, or a plane, straight edge or vertex."
                    .into(),
            ),
        }
    };
    let (pa, da) = line(a)?;
    let (pb, db) = line(b)?;
    let d = sub(pb, pa);
    Ok(match (da, db) {
        (None, None) => [pa, pb],
        (Some(u), None) => [add(pa, mul(u, dot(d, u))), pb],
        (None, Some(v)) => [pa, sub(pb, mul(v, dot(d, v)))],
        (Some(u), Some(v)) => {
            let c = dot(u, v);
            let det = 1.0 - c * c;
            let s = if det < 1e-10 {
                dot(d, u)
            } else {
                (dot(d, u) - c * dot(d, v)) / det
            };
            let t = c * s - dot(d, v);
            [add(pa, mul(u, s)), add(pb, mul(v, t))]
        }
    })
}

#[cfg(test)]
mod tests {
    #[test]
    fn reference_fingerprints_ignore_topology_enumeration_and_edge_sense() {
        let mut p =
            haecceity::read_step(include_bytes!("../../tests/fixtures/parts/plate.step")).unwrap();
        let before = super::evidence(&p);
        p.faces.reverse();
        let n = p.edges.len();
        let faces = p.faces.len();
        for solid in &mut p.solids {
            for face in &mut solid.faces {
                *face = faces - 1 - *face;
            }
        }
        p.edges.reverse();
        for f in &mut p.faces {
            for (edge, _) in &mut f.pcurves {
                *edge = n - 1 - *edge;
            }
            for l in &mut f.loops {
                for (e, sense) in &mut l.edges {
                    *e = n - 1 - *e;
                    *sense = !*sense;
                }
            }
        }
        for e in &mut p.edges {
            std::mem::swap(&mut e.start, &mut e.end);
            e.vertices = (e.vertices.1, e.vertices.0);
            e.same_sense = !e.same_sense;
            e.samples.reverse();
        }
        // Part is immutable after its first query; rebuilding also discards all topology caches.
        let reordered = haecceity::Part::new(p.faces, p.edges, p.solids);
        let after = super::evidence(&reordered);
        let geometry = |refs: std::collections::BTreeMap<String, Vec<super::Reference>>| {
            refs.into_iter()
                .map(|(key, records)| {
                    (
                        key,
                        records
                            .iter()
                            .map(super::geometry_value)
                            .collect::<Vec<_>>(),
                    )
                })
                .collect::<std::collections::BTreeMap<_, _>>()
        };
        assert_eq!(geometry(before), geometry(after));
    }
}
