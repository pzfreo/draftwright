//! The bridge from Python quiddity: its recognition JSON, written by `tools/recognise.py`, read
//! into the Analysis's features until Rust quiddity recognises them itself ([docs/model.md]).
//!
//! The JSON is proved to belong to the part before anything is taken from it: the same file
//! (sha256), the same faces in the same order (each face's surface type and area). Face indices
//! are haecceity's, which are the STEP file's own order; quiddity's working-shape indices are
//! mapped through its `caller_index`.
//!
//! Every record quiddity reports has one home, by its type ([`home`]): a feature, evidence (facts
//! about faces that are not features, such as face levels), or a refusal (quiddity saw something
//! and declined to call it a feature). A record type the bridge does not know is refused.
//!
//! quiddity's derived hole patterns are kept beside the features (`Recognition::patterns`), each
//! naming its member holes by feature ID, as `f:hole_pattern:<n>`, ranked by faces. Its derived
//! slot patterns (`SlotArray`, `SlotGrid`) become `slot_pattern` features, `f:slot_pattern:<n>`
//! ranked by faces like any feature, on their members' faces, whose `slots` parameter names the
//! member slots by feature ID in quiddity's order: the slot features the lint and dimension
//! planners already take a `slot_pattern`'s faces to own. A member must be exactly one slot
//! record, and the members' own pattern links (`dependents`) must agree, or the read is refused.
//!
//! The read is fail-closed on what the bridge knows (ADR3-4): quiddity's document format and
//! version, the quiddity release that wrote it ([`QUIDDITY_VERSIONS`]), its coordinate space, its
//! families and the record types each carries ([`RECORDS`]), and its derived collections.
//!
//! A feature's ID is `f:<kind>:<n>`: its rank, from 1, among the features of its kind ordered by
//! their faces. Its fingerprint follows quiddity-rust's `docs/correspondence.md` until quiddity
//! issues its own: family and subtype, intrinsic sizes, axis, position in the part's frame, and
//! pattern count and pitch (a hole's or slot's in its pattern, and a slot pattern's own).

use std::collections::BTreeMap;

use haecceity::Part;
use haecceity::geom::Bounds;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

mod flats;
pub mod holes;
pub mod native;
pub mod recess_patterns;
pub(crate) mod refused_threads;
pub(crate) mod voids;

pub const RECOGNITION_FORMAT: &str = "draftwright.recognition/1";
pub const FINGERPRINT_VERSION: &str = "draftwright-bridge/2";
/// The one quiddity document format and version the bridge reads (`document.py`).
pub const QUIDDITY_FORMAT: &str = "quiddity-recognition";
pub const QUIDDITY_FORMAT_VERSION: u64 = 3;
/// The quiddity releases whose documents the bridge is checked against (ADR3-4: the provider's
/// version is pinned): 0.3.9, draftwright's own, and 0.4.0.dev0, quiddity's main, which wrote the
/// fixtures in `tests/fixtures/recognition/`. Another is refused until it is checked and added.
pub const QUIDDITY_VERSIONS: [&str; 2] = ["0.3.9", "0.4.0.dev0"];
/// The derived collections a format-3 document carries that the bridge does not read, and why:
/// `hole_pairs` restates pairs of holes it reads; `turned_profiles` restates the turned steps;
/// `oriented_slot_patterns` group features it reads one by one
/// (draftwright gives oriented slot arrays no drafting grammar, #1430). Any other is refused.
const DERIVED_NOT_READ: [&str; 3] = ["hole_pairs", "oriented_slot_patterns", "turned_profiles"];

/// Where the features came from, and the frame quiddity's coordinates are in.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Recognition {
    pub recogniser: String,
    /// quiddity's working frame in the part's: a point `l` in it is
    /// `origin + l[0] x + l[1] y + l[2] z`. Coordinates and axis letters in a feature's
    /// parameters are in this frame.
    pub frame: [[f64; 3]; 4],
    /// quiddity's evidence records: facts about faces, not features.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub evidence: Vec<Record>,
    /// What quiddity declined to recognise, and why (in each record's parameters).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub refusals: Vec<Record>,
    /// quiddity's hole patterns (its derived `hole_patterns`): arrangements of hole features,
    /// ordered by their faces.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub patterns: Vec<HolePattern>,
}

/// A hole pattern quiddity derived: which holes, and how they are arranged.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HolePattern {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub legacy_id: Option<String>,
    /// `f:hole_pattern:<n>`: its rank, from 1, among the patterns ordered by their faces.
    pub id: String,
    /// `grid`, `bolt_circle` or `linear`, from the sizes quiddity gives it.
    pub arrangement: String,
    /// The member holes' feature IDs, in quiddity's order.
    pub members: Vec<String>,
    /// The members' faces, ascending.
    pub faces: Vec<usize>,
    /// quiddity's record without its member records (`holes`); points and directions are in
    /// quiddity's frame (`Recognition::frame`).
    pub parameters: Map<String, Value>,
}

/// A quiddity record that is not a feature, ordered by family, subtype and faces.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Record {
    pub family: String,
    pub subtype: String,
    /// The part's faces, ascending.
    pub faces: Vec<usize>,
    pub parameters: Map<String, Value>,
}

/// Where a quiddity record goes in the Analysis.
#[derive(Clone, Copy, Debug, PartialEq)]
enum Home {
    Feature,
    Evidence,
    Refusal,
}

/// Every family and record type quiddity's recognition document (format 3) can carry, and the
/// home each takes: its capability manifest's `output` records are features, its `evidence`
/// records evidence, and its section recess refusals refusals. The families are the document's
/// (`RecognitionResult` fields), as quiddity 0.3.9's manifest lists them (0.4.0.dev0's is the
/// same); a section recess refusal comes in `section_recesses`, as `evidence.py` files it, and a
/// countersink in either `countersinks` or `holes`. Anything else is refused until it is given a
/// home here (ADR3-4, ADR3-5).
const RECORDS: &[(&str, &str, Home)] = &[
    ("angled_steps", "AngledStep", Home::Feature),
    ("blends", "Blend", Home::Feature),
    ("bosses", "BossRecord", Home::Feature),
    ("chamfers", "Chamfer", Home::Feature),
    ("circular_blind_steps", "CircularBlindStep", Home::Feature),
    (
        "circular_face_patterns",
        "CircularFacePattern",
        Home::Feature,
    ),
    ("countersinks", "CounterSink", Home::Feature),
    ("holes", "CounterSink", Home::Feature),
    ("double_d_bores", "DoubleDBore", Home::Feature),
    ("fillets", "Fillet", Home::Feature),
    ("cylindrical_profiles", "CylindricalProfile", Home::Feature),
    ("face_grooves", "FaceGroove", Home::Feature),
    ("turned_bodies", "TurnedBody", Home::Feature),
    ("flats", "Flat", Home::Feature),
    ("freeform_surfaces", "FreeformSurface", Home::Feature),
    ("grooves", "Groove", Home::Feature),
    ("gusset_ribs", "GussetRib", Home::Feature),
    ("gusset_rib_patterns", "GussetRibArray", Home::Feature),
    ("gusset_rib_patterns", "GussetRibMirrorPair", Home::Feature),
    ("holes", "HoleRecord", Home::Feature),
    ("interior_voids", "InteriorVoid", Home::Feature),
    ("oblique_through_steps", "ObliqueThroughStep", Home::Feature),
    ("oriented_chamfers", "OrientedChamfer", Home::Feature),
    ("oriented_slots", "OrientedSlot", Home::Feature),
    ("oriented_slot_patterns", "OrientedSlotArray", Home::Feature),
    ("oriented_slot_patterns", "OrientedSlotGrid", Home::Feature),
    ("paired_ramp_steps", "PairedRampStep", Home::Feature),
    ("plates", "Plate", Home::Feature),
    ("polygonal_bosses", "PolygonalBoss", Home::Feature),
    ("polygonal_stock", "PolygonalStock", Home::Feature),
    ("pads", "RaisedPad", Home::Feature),
    ("section_recesses", "SectionRecess", Home::Feature),
    ("sheet_metal_bodies", "SheetMetalBody", Home::Feature),
    ("slots", "Slot", Home::Feature),
    ("slot_patterns", "SlotArray", Home::Feature),
    ("slot_patterns", "SlotGrid", Home::Feature),
    ("thin_wall_bodies", "ThinWallBody", Home::Feature),
    ("through_steps", "ThroughStep", Home::Feature),
    ("turned_steps", "TurnedStep", Home::Feature),
    ("step_levels", "FaceLevel", Home::Evidence),
    ("holes", "HoleSpec", Home::Evidence),
    (
        "repeating_radial_profiles",
        "RepeatingRadialProfile",
        Home::Evidence,
    ),
    ("risers", "RiserEvidence", Home::Evidence),
    ("section_recesses", "SectionRecessRefusal", Home::Refusal),
];

/// The home of a record of *record_type* in *family*, or why it has none: a record type, or a
/// family, the bridge does not know, or a known record type in a family it does not come in.
fn home(family: &str, record_type: &str) -> Result<Home, String> {
    if let Some(&(_, _, h)) = RECORDS.iter().find(|r| r.0 == family && r.1 == record_type) {
        return Ok(h);
    }
    if !RECORDS.iter().any(|r| r.1 == record_type) {
        return Err(format!(
            "quiddity reports a {record_type} ({family}), which the bridge has no home for"
        ));
    }
    if !RECORDS.iter().any(|r| r.0 == family) {
        return Err(format!(
            "quiddity reports a family {family:?} (a {record_type}), which the bridge does not know"
        ));
    }
    let known: Vec<&str> = RECORDS
        .iter()
        .filter(|r| r.1 == record_type)
        .map(|r| r.0)
        .collect();
    Err(format!(
        "quiddity reports a {record_type} in {family}; the bridge knows it only in {}",
        known.join(" or ")
    ))
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Feature {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub legacy_id: Option<String>,
    pub id: String,
    pub kind: String,
    /// The part's faces, ascending.
    pub faces: Vec<usize>,
    /// quiddity's record, without its links to other records (those are by discovery order). A
    /// slot pattern's `slots` are its members' feature IDs, in quiddity's order, in place of
    /// their records.
    pub parameters: Map<String, Value>,
    pub fingerprint: Fingerprint,
}

/// A revision-stable signature of a feature in the part's frame. Lengths are mm, angles degrees,
/// all rounded to 1e-6.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Fingerprint {
    pub version: String,
    pub family: String,
    pub subtype: String,
    /// Intrinsic sizes by name; a counterbore's diameter is `cbore.diameter`.
    pub sizes: BTreeMap<String, f64>,
    /// The axis or normal as quiddity gives it, as a line: its first non-zero component positive.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub axis: Option<[f64; 3]>,
    /// The centre of the box around the feature's faces.
    pub position: [f64; 3],
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pattern: Option<Pattern>,
    /// The feature's neighbourhood in the attributed adjacency graph (Joshi & Chang): for each of
    /// its faces and each face that face meets, `type>in|out:type:convex|concave|smooth|unknown`
    /// (`in` when the neighbour is the feature's own), sorted. Rigid motion leaves it unchanged, and
    /// it survives a feature moving or resizing (quiddity-rust docs/correspondence.md).
    pub neighbourhood: Vec<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Pattern {
    pub count: usize,
    /// The least distance between two members, mm.
    pub pitch: f64,
}

/// The record fields, and named dimensions, that are a feature's intrinsic sizes: the size names
/// the corpus's records use. A size under another name is not in the fingerprint (it is still in
/// the parameters).
const SIZES: [&str; 18] = [
    "across_flats",
    "angle",
    "axial_length",
    "corner_radius",
    "depth",
    "diameter",
    "end_radius",
    "half_width",
    "height",
    "height_above_base",
    "leg1",
    "leg2",
    "length",
    "major_radius",
    "minor_radius",
    "radius",
    "thickness",
    "width",
];

#[derive(Deserialize)]
struct Wrapper {
    format: String,
    source_sha256: String,
    recogniser: String,
    faces: Vec<FaceCheck>,
    quiddity: Document,
}

#[derive(Deserialize)]
struct FaceCheck {
    surface: String,
    area: f64,
}

#[derive(Deserialize)]
struct Document {
    format: String,
    format_version: u64,
    package: Package,
    coordinate_space: String,
    frame: QFrame,
    faces: Vec<QFace>,
    features: Vec<QFeature>,
    derived: Map<String, Value>,
}

#[derive(Deserialize)]
struct Package {
    name: String,
    version: String,
}

#[derive(Deserialize)]
struct QFrame {
    origin: [f64; 3],
    x: [f64; 3],
    y: [f64; 3],
    z: [f64; 3],
}

#[derive(Deserialize)]
struct QFace {
    index: usize,
    caller_index: usize,
}

#[derive(Deserialize)]
struct QFeature {
    index: usize,
    family: String,
    record_type: String,
    record: Map<String, Value>,
    constituent_faces: Vec<usize>,
    #[serde(default)]
    instances: Vec<QInstance>,
}

#[derive(Deserialize)]
struct QInstance {
    face_indices: Vec<usize>,
}

#[derive(Deserialize)]
struct Derived {
    #[serde(default)]
    section_recess_patterns: Vec<Map<String, Value>>,
    #[serde(default)]
    hole_patterns: Vec<QHolePattern>,
    #[serde(default)]
    slot_patterns: Vec<QSlotPattern>,
}

#[derive(Deserialize)]
struct QHolePattern {
    holes: Vec<Map<String, Value>>,
    #[serde(flatten)]
    rest: Map<String, Value>,
}

/// quiddity's `SlotArray` or `SlotGrid`: its member `Slot` records, and its arrangement.
#[derive(Deserialize)]
struct QSlotPattern {
    slots: Vec<Map<String, Value>>,
    #[serde(flatten)]
    rest: Map<String, Value>,
}

/// Reads recognition JSON for *part*, the STEP file whose sha256 is *sha256*.
pub fn read(part: &Part, sha256: &str, json: &str) -> Result<(Recognition, Vec<Feature>), String> {
    let w: Wrapper = serde_json::from_str(json).map_err(|e| {
        format!("the recognition JSON is not valid ({e}); write it with tools/recognise.py")
    })?;
    if w.format != RECOGNITION_FORMAT {
        return Err(format!(
            "the recognition JSON is {:?}, not {RECOGNITION_FORMAT:?}; write it with tools/recognise.py",
            w.format
        ));
    }
    if w.source_sha256 != sha256 {
        return Err(
            "the recognition JSON was made from a different file; recognise this one".into(),
        );
    }
    check_faces(part, &w.faces)?;

    let doc = w.quiddity;
    check_document(&doc, &w.recogniser)?;
    let derived: Derived = serde_json::from_value(Value::from(doc.derived.clone()))
        .map_err(|e| format!("quiddity's derived records are not readable ({e})"))?;
    let mut to_part = vec![None; doc.faces.len()];
    for f in &doc.faces {
        if f.index >= to_part.len() || f.caller_index >= part.faces.len() {
            return Err(format!("quiddity's face {} is out of range", f.index));
        }
        to_part[f.index] = Some(f.caller_index);
    }
    let map = |faces: &[usize]| -> Result<Vec<usize>, String> {
        let mut out = faces
            .iter()
            .map(|&i| {
                to_part
                    .get(i)
                    .copied()
                    .flatten()
                    .ok_or_else(|| format!("quiddity names face {i}, which it does not list"))
            })
            .collect::<Result<Vec<_>, _>>()?;
        out.sort_unstable();
        Ok(out)
    };
    let f = &doc.frame;
    let frame = [f.origin, f.x, f.y, f.z];

    // Each feature with the document index of its record, and a slot pattern's members'.
    let mut features: Vec<(Feature, Option<usize>, Vec<usize>)> = Vec::new();
    let (mut evidence, mut refusals) = (Vec::new(), Vec::new());
    let slot_members = slot_members(&derived.slot_patterns, &doc.features)?;
    for q in &doc.features {
        let home = home(&q.family, &q.record_type)?;
        let faces = map(&q.constituent_faces)?;
        let mut parameters = q.record.clone();
        parameters.remove("dependents");
        if home != Home::Feature {
            let record = Record {
                family: q.family.clone(),
                subtype: q.record_type.clone(),
                faces,
                parameters,
            };
            match home {
                Home::Evidence => evidence.push(record),
                _ => refusals.push(record),
            }
            continue;
        }
        if faces.is_empty() {
            return Err(format!("a {} has no faces to anchor it", q.family));
        }
        let pattern = if q.instances.len() > 1 {
            let centres = q
                .instances
                .iter()
                .map(|i| Ok(centre(part, &map(&i.face_indices)?)))
                .collect::<Result<Vec<_>, String>>()?;
            Some(pattern(&centres))
        } else if let Some(members) = slot_members.iter().find(|m| m.contains(&q.index)) {
            Some(slot_pattern(members, &doc.features)?)
        } else {
            hole_pattern(&derived.hole_patterns, &q.record)?
        };
        features.push((
            Feature {
                legacy_id: None,
                id: String::new(),
                kind: kind(&q.family),
                fingerprint: Fingerprint {
                    version: FINGERPRINT_VERSION.into(),
                    family: q.family.clone(),
                    subtype: q.record_type.clone(),
                    sizes: sizes(&q.record),
                    axis: axis(&q.record, &frame)
                        .map_err(|e| format!("a {}'s axis {e}", q.record_type))?,
                    position: centre(part, &faces).map(round),
                    pattern,
                    neighbourhood: neighbourhood(part, &faces),
                },
                faces,
                parameters,
            },
            Some(q.index),
            Vec::new(),
        ));
    }
    for (p, members) in derived.slot_patterns.iter().zip(slot_members) {
        let mut faces = Vec::new();
        for m in &members {
            let q = doc
                .features
                .iter()
                .find(|q| q.index == *m)
                // Invariant: slot_members resolved every index from doc.features, refusing absent or ambiguous members.
                .expect("a member");
            faces.extend(map(&q.constituent_faces)?);
        }
        faces.sort_unstable();
        faces.dedup();
        let has = |k: &str| p.rest.contains_key(k);
        let subtype = if ["rows", "cols", "row_pitch", "col_pitch", "center"]
            .iter()
            .all(|k| has(k))
        {
            "SlotGrid"
        } else if has("pitch") && has("direction") {
            "SlotArray"
        } else {
            return Err(format!(
                "a slot pattern has neither a grid's nor a linear array's sizes ({:?})",
                p.rest.keys().collect::<Vec<_>>()
            ));
        };
        features.push((
            Feature {
                legacy_id: None,
                id: String::new(),
                kind: "slot_pattern".into(),
                fingerprint: Fingerprint {
                    version: FINGERPRINT_VERSION.into(),
                    family: "slot_patterns".into(),
                    subtype: subtype.into(),
                    sizes: sizes(&p.rest),
                    axis: None,
                    position: centre(part, &faces).map(round),
                    pattern: Some(slot_pattern(&members, &doc.features)?),
                    neighbourhood: neighbourhood(part, &faces),
                },
                faces,
                parameters: p.rest.clone(),
            },
            None,
            members,
        ));
    }

    // IDs: by kind, then by faces. Two features of one kind on the same faces have no ID apart.
    features.sort_by(|a, b| (&a.0.kind, &a.0.faces).cmp(&(&b.0.kind, &b.0.faces)));
    for i in 1..features.len() {
        let (a, b) = (&features[i - 1].0, &features[i].0);
        if a.kind == b.kind && a.faces == b.faces {
            return Err(format!(
                "quiddity reports two {}s on faces {:?}; they cannot be told apart",
                a.kind, a.faces
            ));
        }
    }
    let mut n = 0;
    for i in 0..features.len() {
        n = if i > 0 && features[i - 1].0.kind == features[i].0.kind {
            n + 1
        } else {
            1
        };
        features[i].0.id = format!("f:{}:{n}", features[i].0.kind);
    }
    // A slot pattern names its members by their IDs, in quiddity's order, where its record had
    // their records.
    let ids: BTreeMap<usize, String> = features
        .iter()
        .filter_map(|(f, index, _)| Some(((*index)?, f.id.clone())))
        .collect();
    let mut features: Vec<Feature> = features
        .into_iter()
        .map(|(mut f, _, members)| {
            if !members.is_empty() {
                let named: Vec<Value> = members.iter().map(|m| ids[m].clone().into()).collect();
                f.parameters.insert("slots".into(), named.into());
            }
            f
        })
        .collect();
    // The released 0.3.9 bridge draws these occurrences independently. Keep
    // their individual size and datum-location owners when replaying archived
    // documents; the 0.4 contract and native reader use unified patterns.
    if doc.package.version != "0.3.9" {
        recess_patterns::append(
            part,
            &frame,
            &mut features,
            &derived.section_recess_patterns,
        )?;
    }
    flats::merge(part, &frame, &mut features);
    let mut patterns = hole_patterns(&derived.hole_patterns, &features)?;
    voids::reconcile(part, &mut features, &mut patterns);
    // Records with no ID are ordered by everything they carry.
    let key = |r: &Record| {
        (
            r.family.clone(),
            r.subtype.clone(),
            r.faces.clone(),
            Value::from(r.parameters.clone()).to_string(),
        )
    };
    evidence.sort_by_cached_key(key);
    refusals.sort_by_cached_key(key);
    let recognition = Recognition {
        recogniser: format!("{} via tools/recognise.py", w.recogniser),
        frame,
        evidence,
        refusals,
        patterns,
    };
    Ok((recognition, features))
}

/// Proves that quiddity's document is one the bridge reads (ADR3-4): its format and version, the
/// quiddity release that wrote it (one of [`QUIDDITY_VERSIONS`], and the one the wrapper names),
/// local coordinates (mapped by its frame), and only derived collections the bridge knows.
fn check_document(doc: &Document, recogniser: &str) -> Result<(), String> {
    if doc.format != QUIDDITY_FORMAT || doc.format_version != QUIDDITY_FORMAT_VERSION {
        return Err(format!(
            "quiddity's document is {:?} version {}; the bridge reads {QUIDDITY_FORMAT:?} version {QUIDDITY_FORMAT_VERSION} only",
            doc.format, doc.format_version
        ));
    }
    let (name, version) = (&doc.package.name, &doc.package.version);
    if name != "quiddity" || !QUIDDITY_VERSIONS.contains(&version.as_str()) {
        return Err(format!(
            "the recognition JSON was written by {name} {version}; the bridge is checked against quiddity {} only",
            QUIDDITY_VERSIONS.join(" and ")
        ));
    }
    if recogniser != format!("quiddity {version}") {
        return Err(format!(
            "the recognition JSON says it was written by {recogniser:?}, but quiddity's document by quiddity {version}; write it with tools/recognise.py"
        ));
    }
    if doc.coordinate_space != "local" {
        return Err(format!(
            "quiddity's document is in {:?} coordinates; the bridge reads local ones, mapped by its frame",
            doc.coordinate_space
        ));
    }
    let read = ["hole_patterns", "slot_patterns", "section_recess_patterns"];
    if let Some(other) = doc
        .derived
        .keys()
        .find(|k| !read.contains(&k.as_str()) && !DERIVED_NOT_READ.contains(&k.as_str()))
    {
        return Err(format!(
            "quiddity's document derives {other:?}, which the bridge does not know"
        ));
    }
    Ok(())
}

/// Proves that the JSON's faces are the part's: in number, and each in surface type (where both
/// readings name an analytic surface) and area. The file is already proved the same (sha256), so
/// this proves the order: that OpenCascade's traversal, which numbered the JSON's faces, is
/// haecceity's. A face whose area haecceity cannot integrate cannot be proved, so it is refused.
///
/// The JSON's areas are OpenCascade's at its default precision; haecceity's are integrated to
/// near machine precision. Over the 4,031 faces of the repository's recognitions
/// (`tests/fixtures/known_bridge_faces.json` lists every face past 1e-4, with evidence), planes,
/// cylinders, cones and tori agree within 2.4e-5 relative (to a face of 1 mm²), so they are held
/// to 1e-3. Spheres and B-splines are held to 1e-2: OpenCascade's default rule is up to 2.5e-3 off
/// its own converged area on sphere patches that reach a pole (nist_ctc_04's faces 367, 409 and
/// 410; haecceity is within 1.5e-6 of the converged area), a file's pcurves can bound a sphere
/// patch 3.9e-4 differently from its edges (nist_ftc_07; OpenCascade integrates the pcurves'
/// region, haecceity the edges'), and a B-spline sliver of 0.002 mm² differs by 1.5e-4 mm², below
/// what its edges' tolerance determines. Area cannot tell apart faces of the same type and size
/// (a pattern's instances), so swapping two of those is not detected here.
fn check_faces(part: &Part, faces: &[FaceCheck]) -> Result<(), String> {
    if faces.len() != part.faces.len() {
        return Err(format!(
            "the recognition JSON has {} faces and the part {}; recognise this file",
            faces.len(),
            part.faces.len()
        ));
    }
    let analytic = |s: &str| ["plane", "cylinder", "cone", "sphere", "torus"].contains(&s);
    for (i, check) in faces.iter().enumerate() {
        let kind = format!("{:?}", part.faces[i].surface.kind()).to_lowercase();
        let both = analytic(&check.surface) && analytic(&kind);
        if both && check.surface != kind {
            return Err(format!(
                "face {i} is a {kind} here and a {} in the recognition JSON",
                check.surface
            ));
        }
        let tol = if both && kind != "sphere" { 1e-3 } else { 1e-2 };
        let Some([area, _]) = part.face_mass(i) else {
            return Err(format!(
                "face {i}'s area cannot be measured here, so it cannot be matched to the recognition JSON"
            ));
        };
        if (area - check.area).abs() > tol * check.area.max(1.0) {
            return Err(format!(
                "face {i} has area {area:.6} here and {:.6} in the recognition JSON",
                check.area
            ));
        }
    }
    Ok(())
}

/// A family's name for one of its features: `holes` → `hole`, `bosses` → `boss`,
/// `thin_wall_bodies` → `thin_wall_body`; `polygonal_stock` has no plural to strip.
fn kind(family: &str) -> String {
    if family == "rectangular_through_notches" {
        return "rectangular_through_notch".into();
    }
    if let Some(stem) = family.strip_suffix("sses") {
        format!("{stem}ss")
    } else if let Some(stem) = family.strip_suffix("ies") {
        format!("{stem}y")
    } else {
        family.strip_suffix('s').unwrap_or(family).to_string()
    }
}

fn round(x: f64) -> f64 {
    let r = (x * 1e6).round() / 1e6;
    if r == 0.0 { 0.0 } else { r }
}

fn sizes(record: &Map<String, Value>) -> BTreeMap<String, f64> {
    let mut out = BTreeMap::new();
    let mut take = |prefix: &str, fields: &Map<String, Value>| {
        for (k, v) in fields {
            if let Some(x) = v.as_f64()
                && SIZES.contains(&k.as_str())
            {
                out.insert(format!("{prefix}{k}"), round(x));
            }
        }
    };
    take("", record);
    for (k, v) in record {
        if let Some(nested) = v.as_object() {
            let prefix = if k == "named_dimensions" {
                String::new()
            } else {
                format!("{k}.")
            };
            take(&prefix, nested);
        }
    }
    out
}

/// The record's `axis` in the part's frame; none if it has none, an error if it is unreadable.
fn axis(record: &Map<String, Value>, frame: &[[f64; 3]; 4]) -> Result<Option<[f64; 3]>, String> {
    let Some(value) = record.get("axis") else {
        return Ok(None);
    };
    let local = match value {
        Value::String(s) if s == "x" => [1.0, 0.0, 0.0],
        Value::String(s) if s == "y" => [0.0, 1.0, 0.0],
        Value::String(s) if s == "z" => [0.0, 0.0, 1.0],
        Value::Null => return Ok(None),
        Value::Object(line) => line
            .get("direction")
            .and_then(point)
            .ok_or_else(|| format!("{value} is not an axis line"))?,
        v => point(v).ok_or_else(|| format!("{v} is not an axis"))?,
    };
    let mut d = [0, 1, 2].map(|k| (0..3).map(|j| local[j] * frame[j + 1][k]).sum::<f64>());
    if let Some(first) = d.iter().find(|c| c.abs() > 1e-9)
        && *first < 0.0
    {
        d = d.map(|c| -c);
    }
    Ok(Some(d.map(round)))
}

fn point(v: &Value) -> Option<[f64; 3]> {
    let c: Vec<f64> = v
        .as_array()?
        .iter()
        .map(Value::as_f64)
        .collect::<Option<_>>()?;
    <[f64; 3]>::try_from(c).ok()
}

/// The centre of the box around *faces*, in the part's frame.
/// The feature's attributed-adjacency neighbourhood: each of its faces, each face that one
/// meets, and how they meet, as sorted strings.
fn neighbourhood(part: &Part, faces: &[usize]) -> Vec<String> {
    use haecceity::brep::Arc;
    let kind = |f: usize| format!("{:?}", part.faces[f].surface.kind()).to_lowercase();
    let mut out: Vec<String> = faces
        .iter()
        .flat_map(|&f| {
            part.neighbours(f).into_iter().map(move |g| {
                let arc = match part.arc(f, g) {
                    Some(Arc::Convex) => "convex",
                    Some(Arc::Concave) => "concave",
                    Some(Arc::Smooth) => "smooth",
                    _ => "unknown",
                };
                let side = if faces.contains(&g) { "in" } else { "out" };
                format!("{}>{side}:{}:{arc}", kind(f), kind(g))
            })
        })
        .collect();
    out.sort();
    out
}

fn centre(part: &Part, faces: &[usize]) -> [f64; 3] {
    let mut b = Bounds::empty();
    for &f in faces {
        b.merge(&part.face_bounds(f));
    }
    b.centre()
}

fn pattern(members: &[[f64; 3]]) -> Pattern {
    let mut pitch = f64::INFINITY;
    for (i, a) in members.iter().enumerate() {
        for b in &members[i + 1..] {
            pitch = pitch.min(haecceity::geom::dist(*a, *b));
        }
    }
    Pattern {
        count: members.len(),
        pitch: round(pitch),
    }
}

/// The diameter of the circle through quiddity's rectangle of four (`width` and `height` about
/// its `center`) when it is a square with each hole on an axis line through that centre, one
/// hole each side of it on two axes; otherwise none (a lattice on the axes, or a rectangle).
fn square_on_axes(q: &QHolePattern) -> Option<f64> {
    if !q.rest.contains_key("width") || q.holes.len() != 4 {
        return None;
    }
    let c = holes::point(q.rest.get("center"))?;
    let mut radius: Option<f64> = None;
    let mut sides = Vec::new();
    for h in &q.holes {
        let at = holes::point(h.get("location"))?;
        let d = [0, 1, 2].map(|k| at[k] - c[k]);
        let on: Vec<usize> = (0..3).filter(|&k| d[k].abs() > 1e-6).collect();
        let [k] = on[..] else { return None };
        let r = *radius.get_or_insert(d[k].abs());
        if (d[k].abs() - r).abs() > 1e-6 * r.max(1.0) {
            return None;
        }
        sides.push((k, d[k] > 0.0));
    }
    sides.sort_unstable();
    sides.dedup();
    let r = radius?;
    (sides.len() == 4 && sides[0].0 == sides[1].0 && sides[2].0 == sides[3].0)
        .then(|| round(2.0 * r))
}

/// How far a bolt circle's members may stray from one radius (mm) and from even angular spacing
/// (degrees): modelling precision, not quiddity's recognition tolerance (2% of the radius, 4% of
/// the spacing), under which a 19.85 × 20 rectangle reads as four holes evenly on a circle (#223).
const CIRCLE_RADIUS_TOL: f64 = 0.01;
const CIRCLE_SPACING_TOL_DEG: f64 = 0.05;

/// Whether quiddity's bolt circle has its members on one circle about their centroid and evenly
/// spaced round it, to modelling precision ([`CIRCLE_RADIUS_TOL`], [`CIRCLE_SPACING_TOL_DEG`]),
/// seen along the first member's drilling axis.
fn evenly_on_circle(q: &QHolePattern) -> bool {
    use haecceity::geom::{cross, dot, sub, unit};
    let Some(at) = q
        .holes
        .iter()
        .map(|h| holes::point(h.get("location")))
        .collect::<Option<Vec<_>>>()
    else {
        return false;
    };
    let Some(axis) = q
        .holes
        .first()
        .and_then(|h| unit(holes::point(h.get("axis"))?))
    else {
        return false;
    };
    let seed = if axis[0].abs() < 0.9 {
        [1.0, 0.0, 0.0]
    } else {
        [0.0, 1.0, 0.0]
    };
    let Some(u) = unit(cross(axis, seed)) else {
        return false;
    };
    let v = cross(axis, u);
    let n = at.len() as f64;
    let c = [0, 1, 2].map(|k| at.iter().map(|p| p[k]).sum::<f64>() / n);
    let d: Vec<[f64; 2]> = at
        .iter()
        .map(|p| [dot(sub(*p, c), u), dot(sub(*p, c), v)])
        .collect();
    let radii: Vec<f64> = d.iter().map(|p| p[0].hypot(p[1])).collect();
    let r = radii.iter().sum::<f64>() / n;
    if d.len() < 3 || radii.iter().any(|x| (x - r).abs() > CIRCLE_RADIUS_TOL) {
        return false;
    }
    let mut angles: Vec<f64> = d.iter().map(|p| p[1].atan2(p[0]).to_degrees()).collect();
    angles.sort_by(f64::total_cmp);
    let even = 360.0 / n;
    let wrap = 360.0 - (angles[angles.len() - 1] - angles[0]);
    angles
        .windows(2)
        .map(|w| w[1] - w[0])
        .chain([wrap])
        .all(|g| (g - even).abs() <= CIRCLE_SPACING_TOL_DEG)
}

/// quiddity's hole patterns, each with its members found among *features* (the holes whose
/// records quiddity lists, matched as [`hole_pattern`] matches them). A pattern whose member is
/// not one of the holes is refused; a bolt circle not [`evenly_on_circle`] is dropped.
fn hole_patterns(qs: &[QHolePattern], features: &[Feature]) -> Result<Vec<HolePattern>, String> {
    let at = |r: &Map<String, Value>| (r.get("location").cloned(), r.get("diameter").cloned());
    let mut out = Vec::new();
    for q in qs {
        let mut members = Vec::new();
        let mut faces = Vec::new();
        for h in &q.holes {
            let hole = features
                .iter()
                .find(|f| f.kind == "hole" && at(&f.parameters) == at(h))
                .ok_or("a hole pattern has a member that is not a recognised hole")?;
            members.push(hole.id.clone());
            faces.extend(hole.faces.iter().copied());
        }
        faces.sort_unstable();
        faces.dedup();
        let has = |k: &str| q.rest.contains_key(k);
        let mut parameters = q.rest.clone();
        let arrangement = if has("diameter") && evenly_on_circle(q) {
            "bolt_circle"
        } else if has("diameter") {
            // quiddity's circle is only near even (a 19.85 × 20 rectangle passes its tolerance):
            // "EQ SP ON ⌀D BC" would place the holes where they are not, about a centre that is
            // no feature (#223). Its holes stay loose, each located by its own coordinates. The
            // Spec's `pattern` can still declare a bolt circle.
            continue;
        } else if let Some(diameter) = square_on_axes(q) {
            // A square of four turned 45° has its holes on the axes through its centre, evenly
            // spaced on one circle: a bolt circle of four, stated by its diameter, not a lattice
            // the views can space (#223). The Spec's `split` dissolves it into single holes.
            // The rectangle's sizes and 45° turn no longer describe it.
            for k in ["width", "height", "angle"] {
                parameters.remove(k);
            }
            parameters.insert("diameter".into(), diameter.into());
            "bolt_circle"
        } else if (has("width") && has("height"))
            || (has("rows") && has("cols") && has("row_pitch") && has("col_pitch"))
        {
            // quiddity's RectangularHoleSet (a proved rectangle of four) or RectGrid (an N×M
            // lattice): both a lattice of holes, laid out from their centres.
            "grid"
        } else if has("pitch") {
            "linear"
        } else {
            return Err(format!(
                "a hole pattern has none of a bolt circle's, grid's or linear pattern's sizes ({:?})",
                q.rest.keys().collect::<Vec<_>>()
            ));
        };
        out.push(HolePattern {
            legacy_id: None,
            id: String::new(),
            arrangement: arrangement.into(),
            members,
            faces,
            parameters,
        });
    }
    out.sort_by(|a, b| a.faces.cmp(&b.faces));
    if let Some(w) = out.windows(2).find(|w| w[0].faces == w[1].faces) {
        return Err(format!(
            "quiddity reports two hole patterns on faces {:?}; they cannot be told apart",
            w[0].faces
        ));
    }
    for (n, p) in out.iter_mut().enumerate() {
        p.id = format!("f:hole_pattern:{}", n + 1);
    }
    Ok(out)
}

/// The document indices of each slot pattern's members, in quiddity's order. A member is the one
/// slot record its record equals; the members' own pattern links (`dependents`,
/// `pattern_sibling`) must name each other and no other slot. A member that resolves to no slot,
/// or to more than one, or whose links disagree, is refused.
fn slot_members(
    patterns: &[QSlotPattern],
    features: &[QFeature],
) -> Result<Vec<Vec<usize>>, String> {
    let is_slot = |q: &QFeature| q.family == "slots" && q.record_type == "Slot";
    let mut out: Vec<Vec<usize>> = Vec::new();
    for p in patterns {
        let mut members = Vec::new();
        for m in &p.slots {
            let found: Vec<usize> = features
                .iter()
                .filter(|q| is_slot(q) && m.iter().all(|(k, v)| q.record.get(k) == Some(v)))
                .map(|q| q.index)
                .collect();
            match found[..] {
                [i] => members.push(i),
                [] => {
                    return Err("a slot pattern has a member that is not a recognised slot".into());
                }
                _ => {
                    return Err(format!(
                        "a slot pattern's member matches {} slots; it cannot be told which",
                        found.len()
                    ));
                }
            }
        }
        let mut sorted = members.clone();
        sorted.sort_unstable();
        sorted.dedup();
        if sorted.len() != members.len() || out.iter().flatten().any(|i| sorted.contains(i)) {
            return Err("quiddity puts one slot in a slot pattern twice".into());
        }
        for &i in &members {
            // Invariant: members contains only indices resolved from features above.
            let q = features.iter().find(|q| q.index == i).expect("a member");
            let mut linked: Vec<usize> = q
                .record
                .get("dependents")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter(|d| d.get("relation").and_then(Value::as_str) == Some("pattern_sibling"))
                .filter_map(|d| d.get("feature_index").and_then(Value::as_u64))
                .map(|d| d as usize)
                .filter(|d| features.iter().any(|q| q.index == *d && is_slot(q)))
                .collect();
            linked.sort_unstable();
            let others: Vec<usize> = sorted.iter().copied().filter(|&m| m != i).collect();
            if linked != others {
                return Err(format!(
                    "quiddity's slot {i} links to slots {linked:?} as its pattern siblings, but its slot pattern has {others:?}"
                ));
            }
        }
        out.push(members);
    }
    Ok(out)
}

/// The count and pitch of a slot pattern with *members* (document indices): the least distance
/// between their centres (quiddity's `Slot.location`).
fn slot_pattern(members: &[usize], features: &[QFeature]) -> Result<Pattern, String> {
    let centres = members
        .iter()
        .map(|m| {
            let r = &features
                .iter()
                .find(|q| q.index == *m)
                // Invariant: slot_pattern receives the same member indices already resolved by slot_members.
                .expect("a member")
                .record;
            let letter = |k: &str| {
                let a = r.get(k)?.as_str()?;
                ["x", "y", "z"].iter().position(|x| *x == a)
            };
            let number = |k: &str| r.get(k).and_then(Value::as_f64);
            let mut c = [0.0; 3];
            let (long, wide) = (letter("long_axis")?, letter("width_axis")?);
            if long == wide {
                return None;
            }
            c[long] = 0.5 * (number("lo")? + number("hi")?);
            c[wide] = number("w_center")?;
            c[3 - long - wide] = 0.5 * (number("d_lo")? + number("d_hi")?);
            Some(c)
        })
        .collect::<Option<Vec<_>>>()
        .ok_or("a slot pattern has a member with no location")?;
    Ok(pattern(&centres))
}

/// The hole pattern a hole belongs to: quiddity lists its members' records.
fn hole_pattern(
    patterns: &[QHolePattern],
    record: &Map<String, Value>,
) -> Result<Option<Pattern>, String> {
    let at = |r: &Map<String, Value>| (r.get("location").cloned(), r.get("diameter").cloned());
    let this = at(record);
    if this.0.is_none() {
        return Ok(None);
    }
    let Some(p) = patterns
        .iter()
        .find(|p| p.holes.iter().any(|h| at(h) == this))
    else {
        return Ok(None);
    };
    let members = p
        .holes
        .iter()
        .map(|h| {
            h.get("location")
                .and_then(point)
                .ok_or("a hole pattern has a member with no location")
        })
        .collect::<Result<Vec<_>, _>>()?;
    Ok(Some(pattern(&members)))
}

/// A recognised complete solid of revolution spanning the part's box, in its principal frame.
/// Multiple bodies and oblique axes retain their records but cannot own principal extents.
pub(crate) fn turned_body(analysis: &crate::model::Analysis) -> Option<(usize, [f64; 3], f64)> {
    let mut bodies = analysis.features.iter().filter(|f| f.kind == "turned_body");
    let f = bodies.next()?;
    if bodies.next().is_some() {
        return None;
    }
    let frame = holes::frame(analysis);
    let axis = crate::lint::parameters::direction(&frame, f.parameters.get("axis"))?;
    let k = holes::principal(axis)?;
    let on = crate::lint::parameters::place(&frame, f.parameters.get("axis_point"))?;
    let od = f.parameters.get("od")?.as_f64()?;
    let length = f.parameters.get("length")?.as_f64()?;
    let [lo, hi] = analysis.envelope;
    ((0..3).all(|j| {
        if j == k {
            ((hi[j] - lo[j]) - length).abs() <= 1e-3
        } else {
            (lo[j] - (on[j] - od / 2.0)).abs() <= 1e-3 && (hi[j] - (on[j] + od / 2.0)).abs() <= 1e-3
        }
    }))
    .then_some((k, on, od))
}

#[cfg(test)]
mod tests {
    use super::{QHolePattern, evenly_on_circle, kind, square_on_axes};

    /// Every family in draftwright's family table (`family_table.py`), with `-` as `_`.
    #[test]
    fn kinds_are_singular() {
        let cases = [
            ("angled_steps", "angled_step"),
            ("blends", "blend"),
            ("bosses", "boss"),
            ("chamfers", "chamfer"),
            ("channels", "channel"),
            ("circular_blind_steps", "circular_blind_step"),
            ("circular_face_patterns", "circular_face_pattern"),
            ("countersinks", "countersink"),
            ("double_d_bores", "double_d_bore"),
            ("fillets", "fillet"),
            ("flats", "flat"),
            ("freeform_surfaces", "freeform_surface"),
            ("grooves", "groove"),
            ("gusset_rib_patterns", "gusset_rib_pattern"),
            ("gusset_ribs", "gusset_rib"),
            ("hole_patterns", "hole_pattern"),
            ("holes", "hole"),
            ("interior_voids", "interior_void"),
            ("oblique_through_steps", "oblique_through_step"),
            ("oriented_chamfers", "oriented_chamfer"),
            ("oriented_slot_patterns", "oriented_slot_pattern"),
            ("oriented_slots", "oriented_slot"),
            ("outer_profile_angles", "outer_profile_angle"),
            ("pads", "pad"),
            ("paired_ramp_steps", "paired_ramp_step"),
            ("passages", "passage"),
            ("plates", "plate"),
            ("pocket_patterns", "pocket_pattern"),
            ("pockets", "pocket"),
            ("polygonal_bosses", "polygonal_boss"),
            ("polygonal_stock", "polygonal_stock"),
            ("prismatic_pockets", "prismatic_pocket"),
            ("rectangular_blind_slots", "rectangular_blind_slot"),
            ("repeating_radial_profiles", "repeating_radial_profile"),
            ("risers", "riser"),
            ("round_bottom_blind_slots", "round_bottom_blind_slot"),
            ("section_recess_patterns", "section_recess_pattern"),
            ("section_recesses", "section_recess"),
            ("sheet_metal_bodies", "sheet_metal_body"),
            ("slot_patterns", "slot_pattern"),
            ("slots", "slot"),
            ("step_levels", "step_level"),
            ("thin_wall_bodies", "thin_wall_body"),
            ("through_steps", "through_step"),
            ("turned_steps", "turned_step"),
        ];
        for (family, singular) in cases {
            assert_eq!(kind(family), singular, "{family}");
        }
    }

    fn rectangle(holes: [[f64; 2]; 4]) -> QHolePattern {
        serde_json::from_value(serde_json::json!({
            "holes": holes.map(|[x, y]| serde_json::json!({"location": [x, y, 6.0]})),
            "width": 1.0, "height": 1.0, "angle": 45.0, "center": [10.0, 20.0, 6.0],
        }))
        .unwrap()
    }

    /// Only a square with its holes on the axes through its centre is a circle of four (#223).
    #[test]
    fn a_square_turned_onto_the_axes_is_a_bolt_circle() {
        let diamond = rectangle([[18.0, 20.0], [10.0, 28.0], [2.0, 20.0], [10.0, 12.0]]);
        assert_eq!(square_on_axes(&diamond), Some(16.0));
        let lattice = rectangle([[14.0, 24.0], [6.0, 24.0], [6.0, 16.0], [14.0, 16.0]]);
        assert_eq!(square_on_axes(&lattice), None);
        let kite = rectangle([[18.0, 20.0], [10.0, 28.0], [4.0, 20.0], [10.0, 12.0]]);
        assert_eq!(square_on_axes(&kite), None);
        let one_axis = rectangle([[18.0, 20.0], [2.0, 20.0], [18.0, 20.0], [2.0, 20.0]]);
        assert_eq!(square_on_axes(&one_axis), None);
    }

    fn circle(holes: &[[f64; 2]]) -> QHolePattern {
        serde_json::from_value(serde_json::json!({
            "holes": holes.iter().map(|[x, y]| serde_json::json!({
                "location": [x, y, 3.0], "axis": [0.0, 0.0, -1.0],
            })).collect::<Vec<_>>(),
            "center": [0.0, 0.0, 3.0], "diameter": 1.0,
        }))
        .unwrap()
    }

    /// A bolt circle holds its holes on one radius and evenly round it to modelling precision,
    /// not to quiddity's recognition tolerance (#223).
    #[test]
    fn a_bolt_circle_is_even_to_modelling_precision() {
        let on = |r: f64, deg: f64| {
            let a = deg.to_radians();
            [5.0 + r * a.cos(), -2.0 + r * a.sin()]
        };
        // dw-examples' gantry plate: four holes on a 19.85 × 20 rectangle, 89.6° and 90.4° apart.
        let gantry = circle(&[[-19.85, 10.0], [0.0, 10.0], [-19.85, -10.0], [0.0, -10.0]]);
        assert!(!evenly_on_circle(&gantry));
        let square = circle(&[on(8.0, 0.0), on(8.0, 90.0), on(8.0, 180.0), on(8.0, 270.0)]);
        assert!(evenly_on_circle(&square));
        let six: Vec<_> = (0..6)
            .map(|i| on(30.0, 15.0 + 60.0 * f64::from(i)))
            .collect();
        assert!(evenly_on_circle(&circle(&six)));
        let mut turned = six.clone();
        turned[2] = on(30.0, 135.0 + 0.1);
        assert!(
            !evenly_on_circle(&circle(&turned)),
            "one hole 0.1° off even spacing"
        );
        let mut outward = six.clone();
        outward[4] = on(30.05, 255.0);
        assert!(
            !evenly_on_circle(&circle(&outward)),
            "one hole 0.05 mm off the radius"
        );
        let noisy: Vec<_> = (0..6)
            .map(|i| on(30.0 + 1e-4 * f64::from(i), 15.0 + 60.0 * f64::from(i)))
            .collect();
        assert!(
            evenly_on_circle(&circle(&noisy)),
            "modelling noise stays a bolt circle"
        );
    }
}
