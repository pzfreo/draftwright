//! Assembly drawing packs. Identity is product first, then conservative, proper-rigid
//! geometric equality (see `docs/model.md`, Drawing packs). Pack intent never edits geometry.

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;
#[cfg(not(target_arch = "wasm32"))]
use std::time::Instant;

// Profiling is native-only. std::time::Instant traps on wasm32-unknown-unknown;
// timing is never part of the report or exposed by the browser pack API.
#[cfg(target_arch = "wasm32")]
struct Instant;
#[cfg(target_arch = "wasm32")]
impl Instant {
    fn now() -> Self {
        Self
    }
    fn elapsed(&self) -> std::time::Duration {
        std::time::Duration::ZERO
    }
}

mod buy;
use crate::bom::Metadata;
mod ga;
pub use ga::{BalloonSpec, GaSpec, GeneralAssembly, HatchSpec, ItemReference};
mod keys;
pub use keys::part_keys;

use haecceity::Part;
use haecceity::cloud::FaceProbe;
use haecceity::geom;
use quiddity::correspondence::align::{Motion, align};
use quiddity::correspondence::fingerprint::{FaceFingerprint, Fingerprints, fingerprint};
use serde::{Deserialize, Serialize};

use crate::api::{Error, Files};
use crate::model::{self, Problem, Spec};
use crate::output::Drawing;
use crate::text::Font;

pub const SPEC_SCHEMA: &str = "draftwright.pack-spec/1";
pub const REPORT_SCHEMA: &str = "draftwright.pack/1";
pub const FORMATS: [&str; 3] = ["pdf", "csv", "json"];

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Supply {
    Make,
    Buy,
    Unknown,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct PartSpec {
    pub supply: Option<Supply>,
    pub item: Option<u32>,
    pub quantity: Option<usize>,
    pub material: Option<String>,
    pub part_number: Option<String>,
    pub description: Option<String>,
    pub revision: Option<String>,
    pub name: Option<String>,
    pub optional: Option<bool>,
    pub stock: Option<String>,
    /// Keep this product out of automatic geometry merges.
    pub separate: bool,
    pub drawing: Option<Spec>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct PackSpec {
    pub schema: String,
    pub parts: BTreeMap<String, PartSpec>,
    pub ga: GaSpec,
}

impl Default for PackSpec {
    fn default() -> Self {
        Self {
            schema: SPEC_SCHEMA.into(),
            parts: BTreeMap::new(),
            ga: GaSpec::default(),
        }
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct Finding {
    pub code: String,
    pub severity: String,
    pub message: String,
    pub products: Vec<String>,
}

#[derive(Clone, Debug, Serialize)]
pub struct Row {
    /// Stable leaf or common-stem key; `products` retains every member key.
    pub key: String,
    pub item: u32,
    #[serde(skip_serializing)]
    pub name: String,
    #[serde(flatten)]
    pub bom: Metadata,
    /// Provenance of each populated BOM value; absent fields have no source.
    pub sources: BTreeMap<String, ValueSource>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub quantity_range: Option<String>,
    pub reference: String,
    pub products: Vec<String>,
    pub part_indices: Vec<usize>,
    pub qty: usize,
    pub supply: Supply,
    pub buy_reasons: Vec<String>,
    /// One-based PDF page references, empty for bought or failed parts.
    pub sheets: Vec<usize>,
    pub problems: Vec<Problem>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<Error>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum ValueSource {
    Spec,
    File,
    Suggested,
    Guess,
}

#[derive(Clone, Debug, Serialize)]
pub struct Report {
    pub schema: String,
    pub rows: Vec<Row>,
    pub findings: Vec<Finding>,
    pub pages: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub general_assembly: Option<GeneralAssembly>,
}

/// A completed pack in memory. Drawings retain each part's own embedded drawing Spec.
/// The optional GA precedes the made-part sheets. Without it, an all-buy or
/// all-failed pack has zero drawings and a valid zero-page PDF.
pub struct Pack {
    pub report: Report,
    pub drawings: Vec<Drawing>,
    pub timings: Timings,
}

/// Wall-clock seconds, kept outside the deterministic manufacturing report.
#[derive(Default, Debug, Serialize)]
pub struct Timings {
    pub read_seconds: f64,
    pub grouping_seconds: f64,
    pub ga_seconds: f64,
    pub parts: Vec<PartTiming>,
}

#[derive(Default, Debug, Serialize)]
pub struct PartTiming {
    pub key: String,
    pub analysis_seconds: f64,
    pub drawing_seconds: f64,
}

struct Group {
    row: Row,
    part: Result<Part, String>,
    identity: Option<Fingerprints>,
    separate: bool,
}

/// Absolute position/distance tolerance in mm. Intrinsic scalars use 1e-7 relative,
/// with a 1e-7 absolute floor. No reflected motion is ever considered.
const DISTANCE: f64 = 1e-5;

fn close(a: f64, b: f64) -> bool {
    a.is_finite() && b.is_finite() && (a - b).abs() <= 1e-7 * a.abs().max(b.abs()).max(1.0)
}

fn face_equal(a: &FaceFingerprint, b: &FaceFingerprint, motion: &Motion) -> bool {
    a.surface == b.surface
        && a.adjacency == b.adjacency
        && a.parameters.len() == b.parameters.len()
        && a.parameters
            .iter()
            .all(|(k, x)| b.parameters.get(k).is_some_and(|y| close(*x, *y)))
        && match (a.area, b.area) {
            (Some(x), Some(y)) => close(x, y),
            _ => false,
        }
        && geom::dist(motion.point(a.centroid), b.centroid) <= DISTANCE
        && match (a.normal, b.normal) {
            (Some(x), Some(y)) => geom::dist(motion.dir(x), y) <= 1e-6,
            (None, None) => true,
            _ => false,
        }
}

fn equal_at(
    a: &Part,
    af: &Fingerprints,
    b: &Part,
    bf: &Fingerprints,
    m: &Motion,
) -> Option<Vec<(usize, usize)>> {
    let mut correspondence = Vec::new();
    let mut unused: BTreeSet<_> = (0..bf.faces.len()).collect();
    for face in &af.faces {
        let j = unused
            .iter()
            .copied()
            .find(|&j| face_equal(face, &bf.faces[j], m))?;
        // Face moments alone cannot distinguish equal boxes with different trims or freeform
        // surfaces. Test samples against exact trimmed faces in both directions as well.
        let probe = FaceProbe::new(b, j);
        if !a
            .face_cloud(face.index, 0.1)
            .iter()
            .all(|&p| probe.distance(m.point(p)) <= DISTANCE)
        {
            return None;
        }
        let probe = FaceProbe::new(a, face.index);
        if !b.face_cloud(j, 0.1).iter().all(|&p| {
            let q = geom::sub(p, m.t);
            let inverse = [0, 1, 2].map(|i| (0..3).map(|k| m.r[k][i] * q[k]).sum());
            probe.distance(inverse) <= DISTANCE
        }) {
            return None;
        }
        unused.remove(&j);
        correspondence.push((face.index, j));
    }
    Some(correspondence)
}

// An exact B-rep path also covers surfaces for which the kernel cannot integrate an
// area or invert a seam sample. Compare all defining geometry and trim topology, never
// entity IDs or cached samples. It is intentionally stricter than geometric equivalence.
fn same_surface(a: &geom::Surface, b: &geom::Surface) -> bool {
    use geom::Surface::*;
    match (a, b) {
        (Plane { frame: a }, Plane { frame: b }) => a == b,
        (
            Cylinder {
                frame: a,
                radius: ar,
            },
            Cylinder {
                frame: b,
                radius: br,
            },
        )
        | (
            Sphere {
                frame: a,
                radius: ar,
            },
            Sphere {
                frame: b,
                radius: br,
            },
        ) => a == b && ar == br,
        (
            Cone {
                frame: a,
                radius: ar,
                semi_angle: aa,
            },
            Cone {
                frame: b,
                radius: br,
                semi_angle: ba,
            },
        ) => a == b && ar == br && aa == ba,
        (
            Torus {
                frame: a,
                major: am,
                minor: an,
            },
            Torus {
                frame: b,
                major: bm,
                minor: bn,
            },
        ) => a == b && am == bm && an == bn,
        (
            Freeform {
                kind: ak,
                surface: a,
            },
            Freeform {
                kind: bk,
                surface: b,
            },
        ) => {
            ak == bk
                && a.degree_u == b.degree_u
                && a.degree_v == b.degree_v
                && a.control_points == b.control_points
                && a.weights == b.weights
                && a.knots_u == b.knots_u
                && a.knots_v == b.knots_v
        }
        _ => false,
    }
}

fn same_pcurve(a: &haecceity::brep::Pcurve, b: &haecceity::brep::Pcurve) -> bool {
    use haecceity::brep::Pcurve::*;
    match (a, b) {
        (Poles(a), Poles(b)) => a == b,
        (Line { point: a, dir: ad }, Line { point: b, dir: bd }) => a == b && ad == bd,
        _ => false,
    }
}

fn exact_brep(a: &Part, b: &Part) -> bool {
    a.faces.len() == b.faces.len()
        && a.edges.len() == b.edges.len()
        && a.solids.len() == b.solids.len()
        && !a.faces.is_empty()
        && a.unresolved_faces().is_empty()
        && b.unresolved_faces().is_empty()
        && a.unresolved_edges().is_empty()
        && b.unresolved_edges().is_empty()
        && a.solids
            .iter()
            .zip(&b.solids)
            .all(|(a, b)| a.faces == b.faces)
        && a.edges.iter().zip(&b.edges).all(|(a, b)| {
            a.curve == b.curve
                && a.start == b.start
                && a.end == b.end
                && a.vertices == b.vertices
                && a.same_sense == b.same_sense
        })
        && a.faces.iter().zip(&b.faces).all(|(a, b)| {
            a.reversed == b.reversed
                && a.solid == b.solid
                && same_surface(&a.surface, &b.surface)
                && a.loops.len() == b.loops.len()
                && a.loops
                    .iter()
                    .zip(&b.loops)
                    .all(|(a, b)| a.edges == b.edges && a.vertex == b.vertex)
                && a.pcurves.len() == b.pcurves.len()
                && a.pcurves
                    .iter()
                    .zip(&b.pcurves)
                    .all(|((ai, a), (bi, b))| ai == bi && same_pcurve(a, b))
        })
}

fn congruence(
    a: &Part,
    af: &Fingerprints,
    b: &Part,
    bf: &Fingerprints,
) -> Option<(Motion, Vec<(usize, usize)>)> {
    if exact_brep(a, b) {
        return Some((
            Motion::IDENTITY,
            (0..a.faces.len()).map(|i| (i, i)).collect(),
        ));
    }
    if a.faces.len() != b.faces.len()
        || a.edges.len() != b.edges.len()
        || a.solids.len() != b.solids.len()
        || !close(af.scale, bf.scale)
        || af.faces.is_empty()
        || !a.unresolved_faces().is_empty()
        || !b.unresolved_faces().is_empty()
        || !a.unresolved_edges().is_empty()
        || !b.unresolved_edges().is_empty()
    {
        return None;
    }
    if let Some(faces) = equal_at(a, af, b, bf, &Motion::IDENTITY) {
        return Some((Motion::IDENTITY, faces));
    }
    // The mean face centroid is translation-equivariant and independent of face order.
    // Try it before align(), which can choose a symmetric rotation that disagrees
    // with the exported face/seam layout. This is only a candidate, still proven below.
    let centroid = |f: &Fingerprints| {
        [0, 1, 2].map(|i| {
            f.faces.iter().map(|face| face.centroid[i]).sum::<f64>() / f.faces.len() as f64
        })
    };
    let translation = Motion {
        t: geom::sub(centroid(bf), centroid(af)),
        ..Motion::IDENTITY
    };
    if let Some(faces) = equal_at(a, af, b, bf, &translation) {
        return Some((translation, faces));
    }
    let (motion, alignment) = align(af, bf, &Default::default());
    alignment
        .found
        .then(|| equal_at(a, af, b, bf, &motion))
        .flatten()
        .map(|faces| (motion, faces))
}

/// Proven correspondence from a representative to a re-exported product.
struct Alias {
    part: usize,
    placement: haecceity::step::Placement,
    /// Representative STEP face entity -> this product's STEP face entity.
    faces: BTreeMap<u64, u64>,
}

fn finding(code: &str, message: String, products: Vec<String>) -> Finding {
    Finding {
        code: code.into(),
        severity: "info".into(),
        message,
        products,
    }
}

struct Prepared {
    part: Part,
    analysis: model::Analysis,
    plan: model::Plan,
    projections: model::Projections,
}

/// Build a pack, reading the assembly definitions once and recognising/drawing each group
/// once. Invalid pack intent refuses the request; individual failures retain their rows.
#[cfg(not(target_arch = "wasm32"))]
pub fn build(step: &Path, spec: &PackSpec) -> Result<Pack, Error> {
    let bytes = model::read_step_file(step).map_err(Error::invalid)?;
    build_bytes(&step.display().to_string(), &bytes, spec)
}

/// Build from STEP or gzip bytes without filesystem access. `name` supplies the
/// same display name and filename stem as the path-based API.
pub fn build_bytes(name: &str, bytes: &[u8], spec: &PackSpec) -> Result<Pack, Error> {
    let step = Path::new(name);
    if spec.schema != SPEC_SCHEMA {
        return Err(Error::invalid(format!(
            "pack Spec schema must be {SPEC_SCHEMA}"
        )));
    }
    let mut timings = Timings::default();
    let started = Instant::now();
    let bytes = model::step_bytes(bytes)
        .map_err(Error::invalid)?
        .into_owned();
    let file = haecceity::step::StepFile::read(&bytes).map_err(|e| Error::failed(e.to_string()))?;
    timings.read_seconds = started.elapsed().as_secs_f64();
    let started = Instant::now();
    let doc =
        haecceity::p21::Document::parse(bytes.clone()).map_err(|e| Error::failed(e.to_string()))?;
    let metadata = crate::bom::read(&doc, &file.parts);
    let keys = keys::keys(
        file.parts
            .iter()
            .map(|d| (d.name.as_str(), d.product_id.as_str())),
    );
    let mut groups: Vec<Group> = Vec::new();
    // Retain the already-proven proper-rigid correspondence for GA projection
    // reuse, including equal geometry exported under different products/frames.
    let mut aliases = BTreeMap::new();
    let mut findings = Vec::new();
    for (i, d) in file.parts.iter().enumerate() {
        let key = &keys[i];
        let separate = spec.parts.get(key).is_some_and(|p| p.separate);
        let part = file.part(i).map_err(|e| e.to_string());
        let mut identity = None;
        let mut correspondence = Motion::IDENTITY;
        let mut corresponding_faces = Vec::new();
        let merged = if separate {
            None
        } else {
            groups.iter_mut().position(|g| {
                if g.separate || !metadata[i].compatible(&g.row.bom) {
                    return false;
                }
                let (Ok(a), Ok(b)) = (&part, &g.part) else {
                    return false;
                };
                if a.faces.len() != b.faces.len()
                    || a.edges.len() != b.edges.len()
                    || a.solids.len() != b.solids.len()
                {
                    return false;
                }
                if exact_brep(a, b) {
                    corresponding_faces = (0..a.faces.len()).map(|i| (i, i)).collect();
                    return true;
                }
                // Fingerprint only genuinely different B-reps with compatible topology.
                // Re-exported twins take the exact path without expensive sampling.
                let af = identity.get_or_insert_with(|| fingerprint(a, &Default::default()));
                let bf = g
                    .identity
                    .get_or_insert_with(|| fingerprint(b, &Default::default()));
                if let Some((motion, faces)) = congruence(a, af, b, bf) {
                    correspondence = motion;
                    corresponding_faces = faces;
                    true
                } else {
                    false
                }
            })
        };
        let reason = buy::name_reason(&d.name);
        if let Some(j) = merged {
            let representative = groups[j].row.part_indices[0];
            let inverse = std::array::from_fn(|row| {
                std::array::from_fn(|col| {
                    if col == 3 {
                        -(0..3)
                            .map(|k| correspondence.r[k][row] * correspondence.t[k])
                            .sum::<f64>()
                    } else {
                        correspondence.r[col][row]
                    }
                })
            });
            let faces = match (&part, &groups[j].part) {
                (Ok(a), Ok(b)) => corresponding_faces
                    .into_iter()
                    .filter_map(|(ai, bi)| {
                        Some((b.face_source(bi)?.entity, a.face_source(ai)?.entity))
                    })
                    .collect(),
                _ => BTreeMap::new(),
            };
            aliases.insert(
                i,
                Alias {
                    part: representative,
                    placement: inverse,
                    faces,
                },
            );
            let row = &mut groups[j].row;
            row.products.push(key.clone());
            row.bom
                .product_ids
                .extend(metadata[i].product_ids.iter().cloned());
            row.bom.product_ids.sort();
            row.bom.product_ids.dedup();
            row.part_indices.push(i);
            row.qty = row
                .qty
                .checked_add(d.placements.len())
                .ok_or_else(|| Error::invalid("pack quantity overflow"))?;
            if let Some(reason) = reason
                && !row.buy_reasons.contains(&reason)
            {
                row.buy_reasons.push(reason);
            }
            continue;
        }
        aliases.insert(
            i,
            Alias {
                part: i,
                placement: haecceity::step::IDENTITY,
                faces: BTreeMap::new(),
            },
        );
        groups.push(Group {
            row: Row {
                key: key.clone(),
                item: 0,
                name: metadata[i].name.clone().unwrap_or_else(|| d.name.clone()),
                bom: metadata[i].clone(),
                sources: BTreeMap::new(),
                quantity_range: None,
                reference: key.clone(),
                products: vec![key.clone()],
                part_indices: vec![i],
                qty: d.placements.len(),
                supply: Supply::Make,
                buy_reasons: reason.into_iter().collect(),
                sheets: Vec::new(),
                problems: Vec::new(),
                error: None,
            },
            part,
            identity,
            separate,
        });
    }
    let mut canonical = BTreeSet::new();
    for g in &mut groups {
        let row = &mut g.row;
        if let Some(stem) = keys::common_stem(
            row.part_indices
                .iter()
                .map(|&i| file.parts[i].name.as_str()),
        ) {
            // A common exporter-label stem is only a fallback display name.
            // An occurrence UDA's explicit name remains a file fact.
            if row
                .part_indices
                .iter()
                .all(|&i| metadata[i].name.as_deref() == Some(file.parts[i].name.as_str()))
            {
                row.name = stem.clone();
            }
            // A stem must not steal another leaf's key, or another group's alias.
            if !keys
                .iter()
                .any(|key| key == &stem && !row.products.contains(key))
                && canonical.insert(stem.clone())
            {
                row.key = stem;
            }
        }
    }
    for g in &mut groups {
        g.row.bom.name = Some(g.row.name.clone());
    }
    timings.grouping_seconds = started.elapsed().as_secs_f64();
    let valid: BTreeSet<_> = keys
        .iter()
        .chain(groups.iter().map(|g| &g.row.key))
        .cloned()
        .collect();
    for (key, intent) in &spec.parts {
        if !valid.contains(key) {
            return Err(Error::invalid(format!(
                "unknown pack part key {key:?}; choose from {}",
                valid.iter().cloned().collect::<Vec<_>>().join(", ")
            )));
        }
        if intent.item == Some(0) || intent.quantity == Some(0) {
            return Err(Error::invalid(format!(
                "{key}: item and quantity must be positive"
            )));
        }
        if intent.separate && !keys.contains(key) {
            return Err(Error::invalid(format!(
                "{key}: separate must address leaf keys from parts, not a merged-group stem"
            )));
        }
    }
    let mut reserved = BTreeSet::new();
    let mut rows = Vec::new();
    let mut prepared = Vec::new();
    let mut bought_geometry = Vec::new();
    for g in groups {
        let mut row = g.row;
        let mut timing = PartTiming {
            key: row.key.clone(),
            ..Default::default()
        };
        if row.products.len() > 1 {
            findings.push(finding("pack_geometry_merge", format!("identical geometry: {} (proper rigid motion, distance tolerance {DISTANCE} mm)", row.products.join(", ")), row.products.clone()));
        }
        let addresses: BTreeSet<_> = row
            .products
            .iter()
            .chain(std::iter::once(&row.key))
            .collect();
        let intents: Vec<_> = addresses
            .iter()
            .filter_map(|k| spec.parts.get(*k))
            .collect();
        macro_rules! resolve {
            ($field:ident) => {{
                let values: Vec<_> = intents.iter().filter_map(|p| p.$field.as_ref()).collect();
                if values.windows(2).any(|w| w[0] != w[1]) {
                    return Err(Error::invalid(format!(
                        "{}: conflicting {} overrides in merged group; use separate",
                        row.key,
                        stringify!($field)
                    )));
                }
                values.first().map(|v| (*v).clone())
            }};
        }
        let stated_supply = row.bom.make_buy.as_deref().and_then(|v| match v {
            "bought" => Some(Supply::Buy),
            "made" => Some(Supply::Make),
            _ => None,
        });
        let suggested_supply = row.bom.suggested_fields.contains("make_buy");
        let supply_override = resolve!(supply);
        let supply = supply_override.or(stated_supply);
        row.sources.insert(
            "supply".into(),
            if supply_override.is_some() {
                ValueSource::Spec
            } else if stated_supply.is_some() {
                if suggested_supply {
                    ValueSource::Suggested
                } else {
                    ValueSource::File
                }
            } else {
                ValueSource::Guess
            },
        );
        macro_rules! text_override {
            ($field:ident) => {
                if let Some(value) = resolve!($field).filter(|s| !s.trim().is_empty()) {
                    row.bom.$field = Some(value.trim().into());
                    row.bom.suggested_fields.remove(stringify!($field));
                    row.sources
                        .insert(stringify!($field).into(), ValueSource::Spec);
                }
            };
        }
        text_override!(material);
        text_override!(part_number);
        text_override!(description);
        text_override!(revision);
        text_override!(name);
        if let Some(value) = resolve!(optional) {
            row.bom.optional = Some(value);
            row.bom.suggested_fields.remove("optional");
            row.sources.insert("optional".into(), ValueSource::Spec);
        }
        if let Some(value) = resolve!(stock) {
            let value = crate::bom::stock(&value)
                .ok_or_else(|| Error::invalid("pack stock must be from stock or to be made"))?;
            row.bom.stock = Some(value);
            row.bom.suggested_fields.remove("stock");
            row.sources.insert("stock".into(), ValueSource::Spec);
        }
        if let Some(value) = &row.bom.name {
            row.name = value.clone();
        }
        if let Some(supply) = supply_override {
            row.bom.make_buy = Some(
                match supply {
                    Supply::Make => "made",
                    Supply::Buy => "bought",
                    Supply::Unknown => "not known",
                }
                .into(),
            );
            row.bom.suggested_fields.remove("make_buy");
            row.sources.insert("make_buy".into(), ValueSource::Spec);
        }
        for (field, present) in [
            ("name", row.bom.name.is_some()),
            ("part_number", row.bom.part_number.is_some()),
            (
                "part_number_suggestion",
                row.bom.part_number_suggestion.is_some(),
            ),
            ("file_make_buy", row.bom.file_make_buy.is_some()),
            ("item_number", row.bom.item_number.is_some()),
            ("product_ids", !row.bom.product_ids.is_empty()),
            ("description", row.bom.description.is_some()),
            ("revision", row.bom.revision.is_some()),
            ("make_buy", row.bom.make_buy.is_some()),
            ("material", row.bom.material.is_some()),
            ("optional", row.bom.optional.is_some()),
            ("stock", row.bom.stock.is_some()),
            ("categories", !row.bom.categories.is_empty()),
            ("classifications", !row.bom.classifications.is_empty()),
            (
                "occurrence_references",
                !row.bom.occurrence_references.is_empty(),
            ),
        ] {
            if present {
                row.sources.entry(field.into()).or_insert(
                    if row.bom.suggested_fields.contains(field) {
                        ValueSource::Suggested
                    } else {
                        ValueSource::File
                    },
                );
            }
        }
        row.sources.insert("qty".into(), ValueSource::File);
        if let Some(qty) = resolve!(quantity) {
            row.qty = qty;
            row.sources.insert("qty".into(), ValueSource::Spec);
        }
        let item_override = resolve!(item);
        if let Some(item) = item_override.or(row.bom.item_number) {
            row.item = item;
            row.sources.insert(
                "item".into(),
                if item_override.is_some() {
                    ValueSource::Spec
                } else if row.bom.suggested_fields.contains("item_number") {
                    ValueSource::Suggested
                } else {
                    ValueSource::File
                },
            );
        }
        let values: Vec<_> = intents.iter().filter_map(|p| p.drawing.as_ref()).collect();
        if values
            .windows(2)
            .any(|w| serde_json::to_value(w[0]).ok() != serde_json::to_value(w[1]).ok())
        {
            return Err(Error::invalid(format!(
                "{}: conflicting drawing overrides in merged group; use separate",
                row.key
            )));
        }
        let mut drawing_spec = values.first().map(|v| (*v).clone()).unwrap_or_default();
        for (field, target) in [
            ("material", &mut row.bom.material),
            ("number", &mut row.bom.part_number),
            ("revision", &mut row.bom.revision),
        ] {
            if let Some(value) = drawing_spec
                .sheet
                .title
                .get(field)
                .filter(|s| !s.trim().is_empty())
            {
                *target = Some(value.trim().into());
                row.sources.insert(
                    if field == "number" {
                        "part_number"
                    } else {
                        field
                    }
                    .into(),
                    ValueSource::Spec,
                );
            }
        }
        if row.sources.get("material") == Some(&ValueSource::Spec)
            && let Some(material) = &row.bom.material
        {
            drawing_spec
                .sheet
                .title
                .entry("material".into())
                .or_insert_with(|| material.clone());
        }
        if row.sources.get("part_number") == Some(&ValueSource::Spec)
            && let Some(number) = &row.bom.part_number
        {
            drawing_spec
                .sheet
                .title
                .entry("number".into())
                .or_insert_with(|| number.clone());
        }
        if row.sources.get("revision") == Some(&ValueSource::Spec)
            && let Some(revision) = &row.bom.revision
        {
            drawing_spec
                .sheet
                .title
                .entry("revision".into())
                .or_insert_with(|| revision.clone());
        }
        row.quantity_range = row
            .bom
            .optional
            .filter(|optional| *optional)
            .map(|_| format!("0–{}", row.qty));
        if row.quantity_range.is_some() {
            row.sources.insert(
                "quantity_range".into(),
                if row.sources.get("optional") == Some(&ValueSource::Spec)
                    || row.sources.get("qty") == Some(&ValueSource::Spec)
                {
                    ValueSource::Spec
                } else if row.sources.get("optional") == Some(&ValueSource::Suggested) {
                    ValueSource::Suggested
                } else {
                    ValueSource::File
                },
            );
        }
        for field in &row.bom.suggested_fields {
            if field != "make_buy" && row.sources.get(field) == Some(&ValueSource::Suggested) {
                findings.push(finding(
                    "pack_bom_suggestion",
                    format!(
                        "{}: {field} is a stored Specify suggestion, not an explicit answer",
                        row.name
                    ),
                    row.products.clone(),
                ));
            }
        }
        row.reference = drawing_spec
            .sheet
            .title
            .get("number")
            .filter(|s| !s.trim().is_empty())
            .cloned()
            .or_else(|| row.bom.stated_part_number().cloned())
            .unwrap_or_else(|| row.key.clone());
        if file.parts.len() == 1 && file.parts[0].placements.len() > 1 {
            findings.push(finding("pack_single_product", "one product with multiple placements; quantity counts placements, geometry is drawn once in its own frame".into(), row.products.clone()));
        }
        if let Ok(part) = &g.part
            && part.solids.len() > 1
        {
            findings.push(finding("pack_compound", format!("{} contains {} solids in one product; upstream haecceity must label/split it if these are separate parts", row.name, part.solids.len()), row.products.clone()));
        }
        let started = Instant::now();
        // Known bought names need no geometry analysis unless the Spec requests a drawing.
        // Unknown shapes share their ONE native recognition with subsequent drawing.
        let read_error = g.part.as_ref().err().cloned();
        let mut analysed = if supply.is_some_and(|s| s != Supply::Buy)
            || (supply.is_none() && row.buy_reasons.is_empty())
            || file.parts.len() == 1
        {
            Some((|| -> Result<(Part, model::Analysis), Error> {
                let (part, mut analysis) =
                    if file.parts.len() == 1 && file.parts[0].placements.len() == 1 {
                        model::analyse_step_part(step, &bytes, Some(0))?
                    } else {
                        model::analyse_pack_part(
                            &step.display().to_string(),
                            &bytes,
                            g.part.map_err(Error::failed)?,
                            &file.parts,
                            row.part_indices[0],
                        )?
                    };
                let recognition = quiddity::correspondence::recognise(&part);
                let doc = quiddity::recognition::document(&recognition, part.faces.len());
                if row.buy_reasons.is_empty()
                    && !row
                        .part_indices
                        .iter()
                        .any(|&i| buy::custom(&file.parts[i].name))
                    && let Some(reason) = buy::shape_reason(&part, &doc)
                {
                    row.buy_reasons.push(reason);
                }
                model::recognise_document(&part, &mut analysis, &doc).map_err(Error::failed)?;
                Ok((part, analysis))
            })())
        } else {
            None
        };
        timing.analysis_seconds = started.elapsed().as_secs_f64();
        let guessed = if !row.buy_reasons.is_empty() && file.parts.len() > 1 {
            Supply::Buy
        } else {
            Supply::Make
        };
        row.supply = supply.unwrap_or(guessed);
        if stated_supply.is_some() && !suggested_supply {
            row.buy_reasons.clear();
        } else if suggested_supply && supply_override.is_none() {
            let code = if row.supply == Supply::Buy {
                "pack_buy_guess"
            } else {
                "pack_make_guess"
            };
            findings.push(finding(
                code,
                format!(
                    "{}: stored Specify name-token suggestion for {} (supply override wins)",
                    row.name,
                    if row.supply == Supply::Buy {
                        "buy"
                    } else {
                        "make"
                    }
                ),
                row.products.clone(),
            ));
        } else if !row.buy_reasons.is_empty() {
            findings.push(finding(
                "pack_buy_guess",
                format!(
                    "{}: guessed buy because {}; supply override wins",
                    row.name,
                    row.buy_reasons.join("; ")
                ),
                row.products.clone(),
            ));
        } else {
            findings.push(finding("pack_make_guess", format!("{}: guessed make; custom drawing identifier or no bounded purchased-part name/shape evidence (supply override wins)", row.name), row.products.clone()));
        }
        let started = Instant::now();
        let planned = if row.supply != Supply::Buy {
            match analysed
                .take()
                .expect("made groups analysed")
                .and_then(|(part, analysis)| {
                    let plan = crate::api::plan_analysed(&analysis, &drawing_spec)?;
                    Ok(Prepared {
                        part,
                        analysis,
                        plan,
                        projections: Default::default(),
                    })
                }) {
                Ok(p) => Some(p),
                Err(error) => {
                    row.error = Some(error);
                    None
                }
            }
        } else {
            if let Some(error) = read_error {
                row.error = Some(Error::failed(error));
            }
            None
        };
        if let Some(Ok((part, analysis))) = analysed {
            bought_geometry.push((row.part_indices[0], part, analysis));
        }
        if let Some(p) = &planned
            && let Some(number) = p.plan.sheet.title.get("number")
            && number != "DWG-001"
        {
            row.reference = number.clone();
        }
        let alias = !drawing_spec.sheet.title.contains_key("number")
            && row.reference == row.key
            && row.key.rsplit_once('#').is_some_and(|(name, suffix)| {
                name == row.name && !suffix.is_empty() && suffix.chars().all(|c| c.is_ascii_digit())
            });
        if row.reference == row.name || alias {
            row.reference.clear();
        }
        timing.drawing_seconds = started.elapsed().as_secs_f64();
        rows.push((
            row,
            planned,
            timing,
            drawing_spec.sheet.title.contains_key("title"),
        ));
    }
    rows.sort_by_key(|(row, ..)| row.supply == Supply::Buy);
    // Conflicting file claims are facts to report, not reasons to refuse a pack.
    let mut claims: BTreeMap<u32, Vec<usize>> = BTreeMap::new();
    for (index, (row, ..)) in rows.iter().enumerate() {
        if row.item > 0 {
            claims.entry(row.item).or_default().push(index);
        }
    }
    for (item, owners) in claims {
        let explicit: Vec<_> = owners
            .iter()
            .copied()
            .filter(|i| rows[*i].0.sources.get("item") == Some(&ValueSource::Spec))
            .collect();
        if explicit.len() > 1 {
            return Err(Error::invalid(format!("duplicate pack item {item}")));
        }
        if owners.len() > 1 {
            let names = owners
                .iter()
                .map(|i| rows[*i].0.key.clone())
                .collect::<Vec<_>>();
            findings.push(finding("pack_duplicate_item", format!("item {item} is claimed by rows {}; retaining the file claims and allocating conflicting rows automatically", names.join(", ")), names));
            for index in owners {
                if !explicit.contains(&index) {
                    rows[index].0.item = 0;
                }
            }
        }
        if let Some(&index) = explicit.first() {
            reserved.insert(rows[index].0.item);
        } else if rows.iter().any(|(r, ..)| r.item == item) {
            reserved.insert(item);
        }
    }
    let mut next = 1u32;
    for (row, ..) in &mut rows {
        if row.item == 0 {
            while reserved.contains(&next) {
                next = next
                    .checked_add(1)
                    .ok_or_else(|| Error::invalid("pack item numbers exhausted"))?;
            }
            row.item = next;
            row.sources.insert("item".into(), ValueSource::Guess);
            reserved.insert(next);
        }
    }
    let ga_started = Instant::now();
    let reused: BTreeMap<_, _> = rows
        .iter()
        .filter_map(|(r, p, ..)| {
            p.as_ref()
                .map(|p| (r.part_indices[0], (&p.part, &p.analysis)))
        })
        .chain(bought_geometry.iter().map(|(i, part, a)| (*i, (part, a))))
        .collect();
    let mut ga = if spec.ga.include.unwrap_or(rows.len() > 1) {
        match ga::prepare(
            step,
            &doc,
            &file,
            &spec.ga,
            rows.iter().map(|(r, ..)| r),
            &reused,
            &aliases,
        ) {
            Ok(ga) => Some(ga),
            Err(error) if error.code == "invalid" => return Err(error),
            Err(error) => {
                findings.push(finding("pack_ga_failed", error.message, Vec::new()));
                None
            }
        }
    } else {
        None
    };
    timings.ga_seconds = ga_started.elapsed().as_secs_f64();
    let font = Font::new(crate::api::MONO.to_vec()).expect("bundled font");
    // Final sheet totals exclude failed parts. Rendering normally happens once; if a
    // renderer fails, retain its row and renumber survivors using their cached projections.
    let drawings = loop {
        let total: usize = rows
            .iter()
            .filter_map(|(_, p, ..)| p.as_ref())
            .map(|p| 1 + p.plan.sheets.len())
            .sum::<usize>()
            + usize::from(ga.is_some());
        let mut drawings = Vec::new();
        if let Some(front) = &mut ga {
            let started = Instant::now();
            match front.render(total, &font) {
                Ok(drawing) => drawings.push(drawing),
                Err(error) => {
                    findings.push(finding("pack_ga_failed", error.message, Vec::new()));
                    ga = None;
                    continue;
                }
            }
            timings.ga_seconds += started.elapsed().as_secs_f64();
        }
        let mut failed = false;
        for (row, p, timing, explicit_title) in &mut rows {
            row.sheets.clear();
            let Some(p) = p else {
                continue;
            };
            let started = Instant::now();
            for (i, sheet) in std::iter::once(&mut p.plan.sheet)
                .chain(p.plan.sheets.iter_mut())
                .enumerate()
            {
                if file.parts.len() > 1 && !*explicit_title {
                    sheet.title.insert("title".into(), row.name.clone());
                }
                sheet.title.insert(
                    "sheet".into(),
                    format!("{}/{}", drawings.len() + i + 1, total),
                );
                crate::title_block::pack_cells(sheet, row.item, row.qty);
            }
            match model::render_sheets_with(
                &p.part,
                &p.analysis,
                &p.plan,
                &font,
                &mut p.projections,
            ) {
                Ok(sheets) => {
                    row.sheets = (drawings.len() + 1..=drawings.len() + sheets.len()).collect();
                    drawings.extend(sheets);
                }
                Err(error) => {
                    row.error = Some(Error::failed(error.to_string()));
                    failed = true;
                }
            }
            timing.drawing_seconds += started.elapsed().as_secs_f64();
        }
        if !failed {
            break drawings;
        }
        for (row, p, ..) in &mut rows {
            if row.error.is_some() {
                *p = None;
            }
        }
    };
    for (mut row, p, timing, _) in rows {
        row.bom.name = (!row.name.trim().is_empty()).then(|| row.name.clone());
        if let Some(p) = p {
            row.problems.extend(p.plan.problems);
            for sheet in std::iter::once(&p.plan.sheet).chain(p.plan.sheets.iter()) {
                row.problems
                    .extend(crate::title_block::problems(&sheet.title_cells, sheet.page));
            }
            row.problems.extend(
                row.sheets
                    .iter()
                    .flat_map(|&i| drawings[i - 1].problems.iter().cloned()),
            );
        }
        timings.parts.push(timing);
        prepared.push(row);
    }
    let general_assembly = ga.map(|g| {
        findings.extend(g.findings);
        g.report
    });
    Ok(Pack {
        report: Report {
            schema: REPORT_SCHEMA.into(),
            rows: prepared,
            findings,
            pages: drawings.len(),
            general_assembly,
        },
        drawings,
        timings,
    })
}

impl Report {
    /// RFC 4180 CSV, with semicolon-separated product keys, reasons and page references.
    pub fn csv(&self) -> String {
        fn quote(s: &str) -> String {
            format!("\"{}\"", s.replace('"', "\"\""))
        }
        let mut out =
            "item,name,products,qty,supply,buy_reasons,sheets,reference,material,description,part_number,revision,quantity_range,optional,stock,make_buy,categories,classifications,occurrence_references,supply_source,item_source,material_source,name_source,description_source,part_number_source,revision_source,quantity_range_source,optional_source,stock_source,make_buy_source,qty_source,categories_source,classifications_source,occurrence_references_source,product_ids,product_ids_source,stated_item_number,stated_item_number_source,file_make_buy,file_make_buy_source,part_number_suggestion,part_number_suggestion_source\r\n".to_string();
        for row in &self.rows {
            let source = |field: &str| {
                row.sources
                    .get(field)
                    .map(|v| match v {
                        ValueSource::Spec => "spec",
                        ValueSource::File => "file",
                        ValueSource::Guess => "guess",
                        ValueSource::Suggested => "suggested",
                    })
                    .unwrap_or("")
                    .to_string()
            };
            let fields = [
                row.item.to_string(),
                row.name.clone(),
                row.products.join("; "),
                row.qty.to_string(),
                match row.supply {
                    Supply::Make => "make",
                    Supply::Buy => "buy",
                    Supply::Unknown => "unknown",
                }
                .into(),
                row.buy_reasons.join("; "),
                row.sheets
                    .iter()
                    .map(usize::to_string)
                    .collect::<Vec<_>>()
                    .join("; "),
                row.reference.clone(),
                row.bom.material.clone().unwrap_or_default(),
                row.bom.description.clone().unwrap_or_default(),
                row.bom.part_number.clone().unwrap_or_default(),
                row.bom.revision.clone().unwrap_or_default(),
                row.quantity_range.clone().unwrap_or_default(),
                row.bom.optional.map(|v| v.to_string()).unwrap_or_default(),
                row.bom.stock.clone().unwrap_or_default(),
                row.bom.make_buy.clone().unwrap_or_default(),
                row.bom.categories.join("; "),
                row.bom.classifications.join("; "),
                row.bom.occurrence_references.join("; "),
                source("supply"),
                source("item"),
                source("material"),
                source("name"),
                source("description"),
                source("part_number"),
                source("revision"),
                source("quantity_range"),
                source("optional"),
                source("stock"),
                source("make_buy"),
                source("qty"),
                source("categories"),
                source("classifications"),
                source("occurrence_references"),
                row.bom.product_ids.join("; "),
                source("product_ids"),
                row.bom
                    .item_number
                    .map(|v| v.to_string())
                    .unwrap_or_default(),
                source("item_number"),
                row.bom.file_make_buy.clone().unwrap_or_default(),
                source("file_make_buy"),
                row.bom.part_number_suggestion.clone().unwrap_or_default(),
                source("part_number_suggestion"),
            ];
            out.push_str(
                &fields
                    .iter()
                    .map(|s| quote(s))
                    .collect::<Vec<_>>()
                    .join(","),
            );
            out.push_str("\r\n");
        }
        out
    }
}

/// Build once and encode the requested outputs. Files are named `pack.pdf`, `pack.csv`,
/// `pack.json` when written using `api::write(&directory.join("pack"), &files)`.
#[cfg(not(target_arch = "wasm32"))]
pub fn render(step: &Path, spec: &PackSpec, formats: &[&str]) -> Result<(Report, Files), Error> {
    let (report, files, _) = render_profiled(step, spec, formats)?;
    Ok((report, files))
}

/// Render with stage and per-group wall-clock seconds, separate from the stable report.
#[cfg(not(target_arch = "wasm32"))]
pub fn render_profiled(
    step: &Path,
    spec: &PackSpec,
    formats: &[&str],
) -> Result<(Report, Files, Timings), Error> {
    if formats.is_empty() || formats.iter().any(|f| !FORMATS.contains(f)) {
        return Err(Error::invalid("pack formats must be pdf,csv,json"));
    }
    let bytes = model::read_step_file(step).map_err(Error::invalid)?;
    render_bytes_profiled(&step.display().to_string(), &bytes, spec, formats)
}

/// Build and encode from STEP or gzip bytes without filesystem access.
pub fn render_bytes(
    name: &str,
    bytes: &[u8],
    spec: &PackSpec,
    formats: &[&str],
) -> Result<(Report, Files), Error> {
    let (report, files, _) = render_bytes_profiled(name, bytes, spec, formats)?;
    Ok((report, files))
}

fn render_bytes_profiled(
    name: &str,
    bytes: &[u8],
    spec: &PackSpec,
    formats: &[&str],
) -> Result<(Report, Files, Timings), Error> {
    if formats.is_empty() || formats.iter().any(|f| !FORMATS.contains(f)) {
        return Err(Error::invalid("pack formats must be pdf,csv,json"));
    }
    let pack = build_bytes(name, bytes, spec)?;
    let mut files = Vec::new();
    for &format in formats {
        let bytes = match format {
            "pdf" => pack.pdf(),
            "csv" => pack.report.csv().into_bytes(),
            _ => serde_json::to_vec_pretty(&pack.report).expect("report serialises"),
        };
        files.push((format.into(), bytes));
    }
    Ok((pack.report, files, pack.timings))
}

impl Pack {
    /// A page's SVG, numbered from one in PDF order (GA first when present).
    pub fn page_svg(&self, page: usize) -> Result<String, Error> {
        page.checked_sub(1)
            .and_then(|index| self.drawings.get(index))
            .map(Drawing::svg)
            .ok_or_else(|| {
                Error::invalid(format!(
                    "pack page {page} is out of range (1..={})",
                    self.drawings.len()
                ))
            })
    }

    /// The same PDF writer and bundled fonts used by the CLI.
    pub fn pdf(&self) -> Vec<u8> {
        Drawing::pdf_pages(&self.drawings, &|name| match name {
            "IBMPlexMono-Regular.ttf" => Some(crate::api::MONO.to_vec()),
            crate::title_block::FONT => Some(crate::title_block::FONT_DATA.to_vec()),
            _ => None,
        })
    }
}
