//! A part's AP242 semantic PMI read into draftwright's PMI records: stage 1 of drawing PMI
//! (draftwright `pmi.py`, `extract_pmi_report`). The Analysis carries the report ([`attach`],
//! [`Reading`]), each geometric tolerance and datum with where it attaches ([`Geometry`]);
//! [`crate::pmi_lowering`] lowers it to Plan items. The PMI coverage lint is a later stage.
//!
//! draftwright reads PMI through OpenCascade's XCAF document, with Part 21 overlays where XCAF
//! loses something (magnitudes, datum supports, dimension groups). The port reads it through
//! haecceity's semantic reader ([`haecceity::pmi::read`]), which walks the Part 21 instances
//! from each part and reports every instance it did not read as a finding. The report keeps
//! draftwright's shape:
//!
//! - [`Record`] is `PmiRecord`, for the categories the port projects: `dimension` (with common
//!   labels), `geometric_tolerance`, `datum`, `manufacturing_requirement` and `surface_label`
//!   (below). `kind`, `type_code` (XCAF's type number for the same kind), `value` (mm, or
//!   degrees for an angle), the deviations, the limits, `label` (as
//!   `_pmi_schema._make_label` and `pmi._geometric_tolerance_record` write it), `datum_refs`,
//!   `gtol_modifiers` (draftwright's names), `basic` and `part21_id` keep draftwright's
//!   meaning, with two differences of convention: a deviation is signed (`lower_tol` -0.2, not
//!   draftwright's unsigned 0.2, so a g6 shaft's two negative deviations survive), and an
//!   unstated nominal is `None`, not 0. One datum record per datum, as `_coalesce_datum_records`
//!   projects XCAF's occurrences onto their datum feature. A record's `blockers` are its
//!   `lowering_blockers`: what the record does not carry (a dimension's qualifier, a tolerance's
//!   projected zone, a datum's targets, …) and every haecceity finding on an instance the record
//!   was read from.
//! - Manufacturing requirements, surface labels and common labels are read from Part 21 alone,
//!   as draftwright reads them (`_pmi_part21.py`), from what haecceity reads of them: a
//!   requirement in words (haecceity's note) is a record labelled with its text and kinded by its
//!   name, taking the faces and `fields` of its structured statement (a 'pmi-assist' attribute
//!   set, or the part's general tolerance class) where the pairing is unambiguous on the part
//!   (`PartReader::requirements`); a structured statement left unpaired is a record of its own;
//!   text on a shape aspect is a surface label (a 'NOTE' aspect) or a common label (XCAF's
//!   `CommonLabel`, a `dimension` of kind `common_label`) (`PartReader::labels`). Their `value`
//!   is `None` (draftwright's placeholder 0). AP242 surface textures (ISO 1302), which
//!   draftwright does not read, are records too (`PartReader::semantic_textures`): the part's
//!   own a default `surface_texture`, one on faces a `surface_finish`. Part-level `semantic
//!   text` attribute sets are manufacturing requirements too (`PartReader::part_notes`):
//!   general notes, surface treatments, heat treatments and edge conditions, with text and
//!   optional one-based `note index` (or legacy order/index) fields; they need no presentation.
//! - The faces a record applies to replace draftwright's XCAF shapes: `faces`, the drawing's face
//!   indices (haecceity's, the file's own order, as [`crate::bridge`] uses), `face_ids` their
//!   `ADVANCED_FACE` instances, and `face_groups`, a location's two sides or a size's one
//!   (draftwright's `reference_item_groups`). `ref_bbox` (their box) and `dominant_axis` are in
//!   the frame the drawing uses, the file's own: the port draws a part where the file puts it,
//!   which is draftwright's frame-less extraction (`frame=None`). A geometric tolerance's and a
//!   datum's `geometry` (draftwright's `ref_pts`, `reference_axis`, `reference_surface_kind` and
//!   `reference_normal`) is derived from the faces for lowering, and so is a dimension's (its
//!   proven stations, or its cylinders: [`crate::pmi_lowering::dimension_geometry`]).
//! - [`Source`] is `PmiSourceEntity`: what was not read, and why, as a `not_extracted` source;
//!   every read record is a source too (`extracted`, or `partially_extracted` with its
//!   blockers). Recognised Draftwright bookkeeping is an `extracted` source of category
//!   `other`, with no drawing record or requirement. Not read: every haecceity finding not on a record's instances; every item
//!   haecceity reads that the port does not project (attribute sets that are no part note, label or
//!   structured requirement, general tolerance tables, AP242 `thread` and `turned_knurl`
//!   features the port does not read as records, material,
//!   standards, datum targets, tolerance relations); and every record whose face or edge does
//!   not resolve to the drawing's part, which is never guessed. Presentation is counted
//!   ([`Accounting`]); decoded callout names and semantic links are retained for triage by
//!   [`crate::graphical_pmi`]. Graphical strokes are not interpreted as requirements.
//!
//! - [`Material`] is draftwright's `MaterialFact`: each part's stated material, for the coverage
//!   lint's title-block check; it stays a source not projected too, as no record holds it.
//!
//! The census is per Part 21 instance, not per XCAF label, so its sources do not pair one for one
//! with draftwright's; records are compared one for one (`tests/pmi.rs`). Output order is the
//! category (dimensions, tolerances, datums, requirements, surface labels), then the part, then
//! the reader's order.

use std::collections::{BTreeMap, BTreeSet};

use haecceity::Part;
use haecceity::geom::{Bounds, Surface};
use haecceity::p21::{self, Attribute, Document, RawEntity};
use haecceity::pmi::{
    self, Anchor, AngleUnit, AttributeValue, AuxiliaryClassification, DatumId, DimTolerance,
    Dimension, DimensionId, DimensionKind, EdgeIndex, FaceIndex, Feature, FeatureId,
    GeneralTolerance, GeometricTolerance, GroupKind, LengthUnit, LocationKind, NoteOwner,
    PartIdentifiers, PartPmi, PartProvenance, Quantity, SizeKind, ToleranceKind, ToleranceModifier,
    ToleranceTarget, Value,
};
use haecceity::step::{PartDefinition, read_part_definitions};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

pub mod graphical;

pub const PMI_SCHEMA: &str = "draftwright.pmi/1";

/// draftwright's source categories (`PmiSourceCategory`), and `other` for what haecceity reads
/// or reports that belongs to none of them (shape aspects, properties, standards).
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Category {
    Dimension,
    GeometricTolerance,
    Datum,
    ManufacturingRequirement,
    SurfaceLabel,
    Other,
}

/// draftwright's extraction outcomes (`PmiExtractionOutcome`), less `presentation_only`:
/// graphic presentation does not become a semantic record ([`ReaderEvidence::callouts`]).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Outcome {
    Extracted,
    PartiallyExtracted,
    NotExtracted,
}

/// One source and the outcome of reading it.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Source {
    /// `<category>:#<instance>` for a record (the record's `source_id`), `finding:#<instance>`
    /// for a finding, `<item>:<part>:<index>` for an item this stage does not project.
    pub id: String,
    pub category: Category,
    /// The subject's entity type, lower case (a complex instance's leaves joined by `+`).
    pub entity: String,
    /// The instances it concerns (the subject first for a finding; ascending otherwise).
    pub instances: Vec<u64>,
    pub outcome: Outcome,
    pub reason: String,
}

/// One semantic PMI requirement of a part (draftwright's `PmiRecord`).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Record {
    pub source_id: String,
    pub category: Category,
    /// draftwright's kind: `linear`, `diameter`, `radius`, `angular`, `thickness`, … for a
    /// dimension (`_pmi_schema._DIM_TYPE`), `position`, `flatness`, … for a tolerance
    /// (`_GTOL_TYPE`), `datum`.
    pub kind: String,
    /// XCAF's type number for the kind (`XCAFDimTolObjects_DimensionType` or
    /// `…_GeomToleranceType`), where it has one.
    pub type_code: Option<u32>,
    /// The part (haecceity's `PartId`: `read_part_definitions`' order).
    pub part: usize,
    /// The record's own instance: the dimension, the tolerance, or the datum's feature (its
    /// `datum` when it has none), as draftwright's `part21_id`.
    pub part21_id: u64,
    /// Nominal (dimension) or magnitude (tolerance), mm or degrees; `None` where the file
    /// states none.
    pub value: Option<f64>,
    /// Signed deviations from nominal, in the value's unit.
    pub upper_tol: Option<f64>,
    pub lower_tol: Option<f64>,
    /// Limits, in the value's unit.
    pub lower_bound: Option<f64>,
    pub upper_bound: Option<f64>,
    /// An ISO 286 tolerance class (`H7`, `g6`).
    pub fit: Option<String>,
    pub label: String,
    /// The datum letters of a tolerance's datum system, compartment by compartment.
    pub datum_refs: Vec<String>,
    pub gtol_modifiers: Vec<String>,
    /// Theoretically exact (basic) dimension.
    pub basic: bool,
    /// The drawing's faces the record applies to, ascending.
    pub faces: Vec<usize>,
    /// Their `ADVANCED_FACE` instances, ascending.
    pub face_ids: Vec<u64>,
    /// A dimension's sides: a location's from and to, a size's feature (drawing faces).
    pub face_groups: Vec<Vec<usize>>,
    /// A dimension's sides' edges, group by group as `face_groups` (draftwright's reference
    /// groups take edges as well as faces).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub edge_groups: Vec<Vec<usize>>,
    /// The drawing's edges the record applies to, ascending.
    pub edges: Vec<usize>,
    /// The box of `faces` in the drawing's frame, `[xmin, ymin, zmin, xmax, ymax, zmax]`.
    pub ref_bbox: Option<[f64; 6]>,
    /// `X`, `Y`, `Z`, or `?`: the box's longest extent (draftwright `_dominant_from_bbox`).
    pub dominant_axis: String,
    /// Every instance the record was read from, ascending.
    pub instances: Vec<u64>,
    pub blockers: Vec<String>,
    /// A structured requirement's named fields (draftwright's `structured_fields`): a thread's
    /// designation, fit class and pitch, a knurl's pattern and pitch, a general tolerance's class,
    /// or a semantic part note's explicit `note order` index.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub fields: Vec<(String, Field)>,
    /// Where a geometric tolerance or datum attaches, derived from its faces for lowering
    /// (draftwright's `ref_pts`, `reference_axis`, `reference_surface_kind` and
    /// `reference_normal`), or a dimension's resolved targets and witnesses.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub geometry: Option<Geometry>,
}

/// Specify's exact per-requirement STEP authorship token (specify-core-rust #22).
/// Retained in the existing source diagnostic, so Analysis roundtrips without schema changes.
const SPECIFY_WRITTEN: &str = "Specify-written PMI (specify-core:written:v1).";
const SPECIFY_MARKER: &str = "specify-core:written:v1";

pub(crate) fn specify_written(report: &Report, source: &str) -> bool {
    report
        .sources
        .iter()
        .any(|s| s.id == source && s.reason.starts_with(SPECIFY_WRITTEN))
}

/// A dimension Specify wrote with its nominal alone: one the general tolerance covers
/// (specify-core 0.1.4, #63). It states nothing the geometry does not; Specify itself reads
/// it back as settling no question (`existing.rs`). Draftwright gives it to its own plan
/// where that plan dimensions the same geometry (#265).
pub(crate) fn specify_general(report: &Report, r: &Record) -> bool {
    r.category == Category::Dimension
        && !r.basic
        && r.upper_tol.is_none()
        && r.lower_tol.is_none()
        && r.lower_bound.is_none()
        && r.upper_bound.is_none()
        && r.fit.is_none()
        && match r.fields.iter().find(|(k, _)| k == "specify origin") {
            Some((_, Field::Text(origin))) => origin == "general_tolerance",
            Some(_) => false,
            None => specify_written(report, &r.source_id),
        }
}

/// The authored reference reaches a datum feature, including a composite's members.
pub(crate) fn datum_referenced(r: &Record) -> bool {
    r.fields.iter().any(|(k, _)| k == "reference datum")
}

/// Check only this requirement's root, never a shared feature or a referenced datum.
fn specify_root(doc: &Document, r: &Record) -> bool {
    let root = r.part21_id;
    let direct = match r.kind.as_str() {
        "internal_thread" | "external_thread" => text_of(doc, root, "THREAD", 1) == SPECIFY_MARKER,
        "knurl" => text_of(doc, root, "TURNED_KNURL", 0) == SPECIFY_MARKER,
        _ => false,
    };
    // A dimension's token describes its own `SHAPE_DIMENSION_REPRESENTATION` (Specify's
    // `provenance::dimension`).
    let dimension = r.category == Category::Dimension
        && r.instances.iter().any(|&rep| {
            attributes(doc, rep, "SHAPE_DIMENSION_REPRESENTATION").is_some()
                && doc.referrers(rep).iter().any(|&id| {
                    text_of(doc, id, "DESCRIPTION_ATTRIBUTE", 0) == SPECIFY_MARKER
                        && ref_of(doc, id, "DESCRIPTION_ATTRIBUTE", 1) == Some(rep)
                })
        });
    direct
        || dimension
        || r.instances.iter().any(|&link| {
            ref_of(doc, link, "PROPERTY_DEFINITION_REPRESENTATION", 0) == Some(root)
                && attributes(doc, root, "PROPERTY_DEFINITION").is_some()
                && doc.referrers(link).iter().any(|&id| {
                    text_of(doc, id, "DESCRIPTION_ATTRIBUTE", 0) == SPECIFY_MARKER
                        && ref_of(doc, id, "DESCRIPTION_ATTRIBUTE", 1) == Some(link)
                })
        })
}

/// A structured requirement's field value, as draftwright holds it: text, or a length in mm.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum Field {
    Text(String),
    Length(f64),
}

const MISSING_NOMINAL: &str = "the file states no nominal value (haecceity reads it without one)";
const MEASURED_NOMINAL: &str = "geometry (no stated value)";

/// A measured fallback retains its source's originally empty label discriminator.
/// The measured number is drawing information, not a newly authored source value.
pub(crate) fn nominal_measured(r: &Record) -> bool {
    r.fields.iter().any(|(key, value)| {
        key == "nominal source" && *value == Field::Text(MEASURED_NOMINAL.into())
    })
}

fn missing_nominal_finding(f: &pmi::Finding) -> bool {
    f.kind == pmi::FindingKind::Nonconformance
        && f.detail
            .starts_with("a dimension without a dimensional_characteristic_representation:")
}

/// How the file's semantic-PMI instances were accounted for (haecceity's accounting).
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Accounting {
    pub semantic: usize,
    pub consumed: usize,
    pub reported: usize,
    pub presentation: usize,
    pub validation: usize,
}

/// A part's PMI as draftwright reads it (`PmiExtractionReport`).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Report {
    pub schema: String,
    /// The parts of the file, in haecceity's order (`Record::part` indexes it).
    pub parts: Vec<String>,
    pub sources: Vec<Source>,
    pub records: Vec<Record>,
    pub accounting: Accounting,
    /// The material each part states (draftwright's `material_facts`), in part order.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub materials: Vec<Material>,
    /// Reader evidence is indirect to keep the frequent `Reading` enum compact.
    #[serde(flatten)]
    pub reader: Box<ReaderEvidence>,
    /// The part drawn, when the drawing is of one part of several read alone
    /// ([`report_part`]): the records, sources and materials are then that part's only.
    /// Absent when the whole file is drawn.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub drawn: Option<usize>,
    /// What merging the Spec's datums and controls into the reading met
    /// ([`crate::spec_gdt::effective`]): an AP242 record the Spec overrides, a datum letter
    /// nothing declares. Never serialised: only an effective reading has them, and lowering
    /// reports them ([`crate::pmi_lowering::lower`]).
    #[serde(skip)]
    pub spec_problems: Vec<crate::model::Problem>,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct ReaderEvidence {
    /// Non-fatal interpretations remain visible without refusing a repaired requirement.
    #[serde(
        default,
        rename = "reader_problems",
        skip_serializing_if = "Vec::is_empty"
    )]
    pub problems: Vec<crate::model::Problem>,
    /// Decoded annotations and part-qualified links. Absent in older saved readings.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub callouts: Vec<graphical::Callout>,
}

/// A part's material as the file states it (CAx-IF's 'material name' property: draftwright's
/// `MaterialFact`, read by haecceity).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Material {
    /// `material:#<instance>`, the property's `PROPERTY_DEFINITION`, as draftwright names it.
    pub source_id: String,
    /// The part (`Record::part`'s index).
    pub part: usize,
    /// The material's designation (the descriptive item's name: draftwright's `designation`);
    /// empty when the file states none, which makes the material unusable.
    pub designation: String,
    /// Its common name (the item's description), when stated.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
}

impl Material {
    /// The reading's census source that accounts for it (`material:<part>:0`).
    pub fn census_id(&self) -> String {
        format!("material:{}:0", self.part)
    }
}

/// The drawing item IDs of a part's PMI records: each record's semantic identity, never its
/// Part 21 instance, so a file whose intent is written again (new instance numbers, the same
/// requirements on the same faces) keeps every item's ID ([`Report::identities`]).
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Identities {
    /// `pmi:<identity>` by the record's `source_id`, for every record whose identity no other
    /// record shares.
    pub ids: BTreeMap<String, String>,
    /// The records whose identity another shares, by `source_id`: the identity and the other
    /// records' `source_id`s. None of them has an ID (ADR R1 invariant 5: refused, not guessed).
    pub shared: BTreeMap<String, (String, Vec<String>)>,
}

impl Identities {
    /// The item ID of the record *source*, or why it has none: its identity is shared, or the
    /// reading has no such record.
    pub fn id(&self, source: &str) -> Result<&str, String> {
        if let Some(id) = self.ids.get(source) {
            return Ok(id);
        }
        match self.shared.get(source) {
            Some((identity, others)) => Err(format!(
                "its identity (pmi:{identity}: what it requires, of which faces, against which datums) is also {}'s, so its drawing item could not be told from theirs and none of them is drawn",
                others.join(", ")
            )),
            None => Err(format!("the reading has no record {source}")),
        }
    }

    /// The `source_id` of the record whose item ID is *id*.
    pub fn source(&self, id: &str) -> Option<&str> {
        self.ids
            .iter()
            .find(|(_, i)| i.as_str() == id)
            .map(|(s, _)| s.as_str())
    }
}

/// *text* with every character but ASCII letters, digits, `_`, `.` and `-` written `%XX` (its
/// UTF-8 bytes), so an identity's fields never contain its separators (`:`, `,`, `=`) or a `/`.
fn escaped(text: &str) -> String {
    let mut out = String::new();
    for b in text.bytes() {
        if b.is_ascii_alphanumeric() || b"_.-".contains(&b) {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

/// A record's semantic identity (the editor's `gdt::editor_key`, upstreamed): its category and
/// kind (`.basic` for a basic dimension), its part in a file of several, the drawing faces and
/// edges it applies to, its datum references and modifiers, and, for a datum or a label, its
/// letter or text. Each field after the first is tagged (`p`, `f`, `e`, `ref=`, `mod=`, `t=`) and
/// left out when empty: `geometric_tolerance.position:f12,13:ref=A,B:mod=maximum_material_requirement`.
/// No Part 21 instance enters it, and neither does a value: a rewrite of the same intent keeps
/// it, and a changed magnitude is the same item with another label.
pub fn identity(r: &Record, parts: usize) -> String {
    let join = |v: &[String]| v.iter().map(|x| escaped(x)).collect::<Vec<_>>().join(",");
    let numbers = |v: &[usize]| {
        v.iter()
            .map(|x| x.to_string())
            .collect::<Vec<_>>()
            .join(",")
    };
    let mut out = format!("{}.{}", category_name(r.category), escaped(&r.kind));
    if r.basic {
        out += ".basic";
    }
    if parts > 1 {
        out += &format!(":p{}", r.part);
    }
    if !r.faces.is_empty() {
        out += &format!(":f{}", numbers(&r.faces));
    }
    if !r.edges.is_empty() {
        out += &format!(":e{}", numbers(&r.edges));
    }
    if !r.datum_refs.is_empty() {
        out += &format!(":ref={}", join(&r.datum_refs));
    }
    if !r.gtol_modifiers.is_empty() {
        out += &format!(":mod={}", join(&r.gtol_modifiers));
    }
    let labelled = r.category == Category::Datum
        || r.category == Category::SurfaceLabel
        || r.kind == "common_label";
    if labelled {
        out += &format!(":t={}", escaped(&r.label));
    }
    out
}

impl Report {
    /// Every record's drawing item ID, `pmi:<identity>` ([`identity`]). Records that share an
    /// identity (a composite frame's two segments: one requirement's faces and datums, two
    /// magnitudes) are told apart by their labels, `:t=<label>` on each of them, a discriminator
    /// from what they state, never from their order; those whose labels are the same too get
    /// none ([`Identities::shared`]).
    pub fn identities(&self) -> Identities {
        fn group<'a>(
            records: Vec<&'a Record>,
            key: &dyn Fn(&Record) -> String,
        ) -> BTreeMap<String, Vec<&'a Record>> {
            let mut by: BTreeMap<String, Vec<&Record>> = BTreeMap::new();
            for r in records {
                by.entry(key(r)).or_default().push(r);
            }
            by
        }
        let parts = self.parts.len();
        let mut out = Identities::default();
        for (base, records) in group(self.records.iter().collect(), &|r| identity(r, parts)) {
            let labelled = records.len() > 1 && !base.contains(":t=");
            let by = if labelled {
                group(records, &|r| {
                    format!(
                        "{base}:t={}",
                        escaped(if nominal_measured(r) { "" } else { &r.label })
                    )
                })
            } else {
                BTreeMap::from([(base, records)])
            };
            for (identity, records) in by {
                if let [one] = records.as_slice() {
                    out.ids
                        .insert(one.source_id.clone(), format!("pmi:{identity}"));
                    continue;
                }
                for r in &records {
                    let others = records
                        .iter()
                        .filter(|o| o.source_id != r.source_id)
                        .map(|o| o.source_id.clone());
                    out.shared
                        .insert(r.source_id.clone(), (identity.clone(), others.collect()));
                }
            }
        }
        out
    }

    /// The material the title block states when the caller gives none (draftwright's
    /// `_resolve_title_document_defaults`): the file's one material, if usable. Several are
    /// ambiguous; one of a file of several parts is not the drawing's part's (draftwright's 'not
    /// owned by the single shaped source product definition'), unless that part is the one
    /// drawn alone ([`Report::drawn`]); an empty designation is unusable.
    pub fn title_material(&self) -> Option<&Material> {
        match self.materials.as_slice() {
            [m] if self.owns(m) && !m.designation.trim().is_empty() => Some(m),
            _ => None,
        }
    }

    /// Whether *m* is the drawing's part's material: the file has one part, or *m* is the part
    /// drawn alone.
    pub fn owns(&self, m: &Material) -> bool {
        self.parts.len() == 1 || self.drawn == Some(m.part)
    }
}

/// An infinite target resolved by the kernel from a derived shape aspect's faces.
/// Coordinates and support faces are in the drawing's placed frame; supplemental items
/// remain cross-check evidence in the kernel and never select the drawing target.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DerivedTarget {
    pub kind: DerivedKind,
    pub point: [f64; 3],
    pub direction: [f64; 3],
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub radius: Option<f64>,
    pub faces: Vec<usize>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DerivedKind {
    Axis,
    MedianPlane,
}

/// Where a geometric tolerance or datum attaches to the part, in the drawing's frame (the
/// file's), as draftwright measures it from the record's faces (`_datum_geometry_from_shapes`,
/// `_datum_reference_normal` and `_common_principal_cylinder_axis`), and where a dimension does.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GeometryRefusal {
    NominalMismatch { measured: f64, nominal: f64 },
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct Geometry {
    /// Machine-readable attachment refusal; prose is diagnostic, never a decision key.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub refusal: Option<GeometryRefusal>,
    /// One entry per authored reference group when any is derived. Direct companion
    /// planes/axes are resolved during lowering; unmeasurable groups remain `None`.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub targets: Vec<Option<DerivedTarget>>,
    /// A datum's attachment site on each face, face by face (`ref_pts`): a point inside a plane,
    /// or on a cylinder's projected silhouette (`_datum_face_site`); a radius's proven point on
    /// its arc; a linear dimension's trimmed-face witnesses when its composite box gives none.
    /// A tolerance on derived geometry keeps a point on its resolved target.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub sites: Vec<[f64; 3]>,
    /// Other proven sites on a datum's first face, ranked as draftwright ranks its choice and
    /// spread over the face (on a cylinder, on either proven silhouette): where the port's placement
    /// may attach the symbol when the first site's has no clear place.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub alternatives: Vec<[f64; 3]>,
    /// `X`, `Y` or `Z` (`reference_axis`): a datum's common plane normal or cylinder axis; a
    /// tolerance's common principal cylinder axis, when every face is a cylinder on one; a
    /// dimension's measured direction or cylinder axis (below).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub axis: Option<String>,
    /// A datum's surface: `plane` or `cylinder` (`reference_surface_kind`).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub surface: Option<String>,
    /// A planar datum's outward unit normal (`reference_normal`).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub normal: Option<[f64; 3]>,
    /// A linear or thickness dimension's two proven reference stations (draftwright's
    /// `ref_pts` for them), from and to; its `axis` is then the direction they are measured
    /// along, when it is x, y or z ([`crate::pmi_lowering::dimension_geometry`]).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub stations: Vec<[f64; 3]>,
    /// Trimmed planar witnesses, separately proven in each edge-on principal view.
    /// Routing may choose among them without changing the authored face groups or span.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub view_witnesses: BTreeMap<String, [[f64; 3]; 2]>,
    /// A diameter's or radius's exact cylinders (draftwright's `cylindrical_refs`), identical
    /// ones once; its `axis` is then their common axis, when it is x, y or z. A thread's or
    /// knurl's too ([`crate::pmi_lowering::requirement_geometry`]).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub cylinders: Vec<crate::pmi_lowering::Cylinder>,
    /// Bores whose two rims are covered by the authored location endpoints. This geometric
    /// evidence is independent of the nominal; the feature merge still proves a blind owner.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub hole_depths: Vec<crate::pmi_lowering::HoleDepth>,
    /// Explicit finite axis points, by authored reference group (CAx-IF RP §5.1.4).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub axis_points: Vec<Vec<AxisPoint>>,
    /// A linear dimension's explicitly proven witness view (`front`, `plan` or `side`),
    /// selected from its trimmed reference faces (draftwright's `support_view`).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub view: Option<String>,
    /// An angular dimension's sides: its vertex and a witness on each side
    /// ([`crate::dims::angular::geometry`]); its `axis` is then the normal of their plane, when it
    /// is x, y or z.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub angle: Option<crate::dims::angular::Angle>,
    /// Why the attachment is not proven: the record is then not lowered.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub reasons: Vec<String>,
    /// A surface finish's sites lie inside its cylinder's outline, on the face as its edge-on
    /// view sees it head-on ([`FaceAttachment::face_on`]), not on a silhouette: its leader ends
    /// in a dot there (ISO 128-22), not an arrowhead.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub face_on: bool,
}

/// A finite point on a kernel-proven deriving axis. Its supplemental point and support
/// faces stay together; an infinite axis's arbitrary origin is never an endpoint.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct AxisPoint {
    pub point: [f64; 3],
    pub faces: Vec<usize>,
}

/// A part's PMI as the Analysis carries it: the report, or why the file's PMI could not be
/// read at all.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Reading {
    Read(Report),
    Refused(String),
}

/// The entity types of semantic PMI (ISO 10303-242's dimensions, tolerances and datums), by
/// which a file that cannot be read is known to carry PMI that is then lost.
const SEMANTIC: [&str; 6] = [
    "DIMENSIONAL_SIZE",
    "DIMENSIONAL_LOCATION",
    "GEOMETRIC_TOLERANCE",
    "DATUM",
    "DATUM_FEATURE",
    "DATUM_SYSTEM",
];

/// A single part's latest PMI reading. Material, manufacturing requirements (including threads)
/// and frames all consume this one report. Keeping the reader across edits also retains empty
/// and refused readings; changed bytes replace the reading rather than accumulate documents.
#[derive(Default)]
pub struct Reader {
    source: Option<[u8; 32]>,
    reading: Option<Reading>,
    reads: usize,
}

impl Reader {
    /// The number of semantic reading attempts actually made, including refusals.
    pub fn reads(&self) -> usize {
        self.reads
    }

    /// The reading to put in `Analysis::pmi`. Identical bytes do not parse again.
    /// Keep one reader per immutable part; replace it when the geometry changes.
    pub fn attach(&mut self, bytes: &[u8], part: &Part) -> Option<Reading> {
        let bytes = match crate::model::step_bytes(bytes) {
            Ok(b) => b,
            Err(e) => return Some(Reading::Refused(e)),
        };
        let bytes = &*bytes;
        let source = Sha256::digest(bytes).into();
        if self.source != Some(source) {
            self.reads += 1;
            self.reading = attached(bytes, read(bytes, part));
            self.source = Some(source);
        }
        self.reading.clone()
    }
}

/// What the Analysis carries of the STEP file *bytes*' PMI, read on the drawing's *part*: the
/// report when the file has any PMI to account for (a record, a source, or a counted instance),
/// none when it has none. A file that cannot be read is refused with the reason when its text
/// names a semantic PMI entity, so PMI the reader loses is said, never dropped.
pub fn attach(bytes: &[u8], part: &Part) -> Option<Reading> {
    Reader::default().attach(bytes, part)
}

/// [`attach`], given the file's part definitions (`read_part_definitions` of *bytes*, or why
/// they could not be read) and, when the drawing is of one part of several, which
/// ([`read_part`]).
pub(crate) fn attach_with(
    bytes: &[u8],
    part: &Part,
    defs: Result<&[PartDefinition], &str>,
    chosen: Option<usize>,
) -> (Option<Reading>, Option<crate::bom::Metadata>) {
    match defs
        .map_err(str::to_string)
        .and_then(|defs| read_with_metadata(bytes, part, defs, chosen))
    {
        Ok(read) => (attached(bytes, read.pmi), read.bom),
        Err(error) => (attached(bytes, Err(error)), None),
    }
}

fn attached(bytes: &[u8], read: Result<Report, String>) -> Option<Reading> {
    match read {
        Ok(r) => {
            let a = &r.accounting;
            let any = !r.records.is_empty()
                || !r.sources.is_empty()
                || a.semantic + a.presentation + a.reported > 0;
            any.then_some(Reading::Read(r))
        }
        Err(e) => {
            let text = String::from_utf8_lossy(bytes);
            let named = SEMANTIC.iter().any(|n| {
                text.match_indices(n).any(|(i, _)| {
                    let after = text[i + n.len()..].trim_start();
                    let before = text[..i].trim_end();
                    after.starts_with('(') && (before.ends_with('=') || before.ends_with('('))
                })
            });
            named.then_some(Reading::Refused(e))
        }
    }
}

/// Reads the semantic PMI of the STEP file *bytes*, whose part the drawing read as *part*
/// (`haecceity::read_step` of the same bytes). Refused when the file's parts cannot be read or
/// do not match *part*'s faces; everything within a part that cannot be read is a source with a
/// reason.
pub fn read(bytes: &[u8], part: &Part) -> Result<Report, String> {
    read_part(bytes, part, None)
}

/// [`read`], where the drawing's *part* is part *chosen* of the file's parts read alone
/// (`haecceity::StepFile::part`), when given ([`report_part`]).
pub fn read_part(bytes: &[u8], part: &Part, chosen: Option<usize>) -> Result<Report, String> {
    let bytes = &*crate::model::step_bytes(bytes)?;
    let defs = read_part_definitions(bytes).map_err(|e| format!("cannot read the parts: {e}"))?;
    read_with(bytes, part, &defs, chosen)
}

fn read_with(
    bytes: &[u8],
    part: &Part,
    defs: &[PartDefinition],
    chosen: Option<usize>,
) -> Result<Report, String> {
    read_with_metadata(bytes, part, defs, chosen).and_then(|read| read.pmi)
}

struct WithMetadata {
    pmi: Result<Report, String>,
    bom: Option<crate::bom::Metadata>,
}

fn read_with_metadata(
    bytes: &[u8],
    part: &Part,
    defs: &[PartDefinition],
    chosen: Option<usize>,
) -> Result<WithMetadata, String> {
    let doc = Document::parse(bytes.to_vec()).map_err(|e| format!("cannot read Part 21: {e}"))?;
    let metadata = chosen
        .or_else(|| (defs.len() == 1).then_some(0))
        .and_then(|i| defs.get(i))
        .and_then(|d| crate::bom::read(&doc, std::slice::from_ref(d)).pop());
    let report = pmi::read(&doc, defs)
        .map_err(|e| e.to_string())
        .and_then(|read| report_part(&doc, defs, part, &read, chosen));
    Ok(WithMetadata {
        pmi: report,
        bom: metadata,
    })
}

/// The report of a haecceity reading *read* of *doc* (whose parts are *defs*), on the drawing's
/// *part*.
pub fn report(
    doc: &Document,
    defs: &[PartDefinition],
    part: &Part,
    read: &pmi::PmiRead,
) -> Result<Report, String> {
    report_part(doc, defs, part, read, None)
}

/// [`report`], where the drawing's *part* is part *chosen* of *defs* read alone, when given:
/// only that part's records, findings and material are reported, and the other parts' are
/// dropped (they are not on this drawing). Its records keep their part's index in *defs*.
pub fn report_part(
    doc: &Document,
    defs: &[PartDefinition],
    part: &Part,
    read: &pmi::PmiRead,
    chosen: Option<usize>,
) -> Result<Report, String> {
    let drawing = Drawing::new(part, defs, chosen)?;
    let drawn = |p: usize| chosen.is_none_or(|k| k == p);
    let mut records = Vec::new();
    let mut sources = Vec::new();
    let mut legacy_presentations: BTreeMap<u64, BTreeSet<String>> = BTreeMap::new();
    for (p, (pmi, prov)) in read.parts.iter().zip(&read.provenance.parts).enumerate() {
        if !drawn(p) {
            continue;
        }
        let r = PartReader {
            doc,
            p,
            pmi,
            prov,
            identifiers: &read.identifiers.parts[p],
            def: Some(&defs[p]),
            drawing: &drawing,
            numbers: characteristic_numbers(doc, pmi, prov),
        };
        r.read(&mut records, &mut sources, &mut legacy_presentations);
    }
    let mut attached = vec![false; read.findings.len()];
    let mut reader_problems = read
        .findings
        .iter()
        .filter(|f| {
            f.part.is_none_or(|p| drawn(p.0))
                && (repaired_read(f) || f.kind == pmi::FindingKind::Fact)
        })
        .map(|f| {
            let fact = f.kind == pmi::FindingKind::Fact;
            let code = if fact {
                "pmi_read_fact"
            } else {
                "pmi_read_nonconformance"
            };
            crate::model::Problem {
                id: format!("p:{code}:{}:{}", f.ids.first().unwrap_or(&0), f.detail),
                code: code.into(),
                severity: if fact { "info" } else { "warning" }.into(),
                message: if fact {
                    format!("The STEP reader preserved a PMI statement: {}.", f.detail)
                } else {
                    format!(
                        "The STEP reader interpreted a nonconforming PMI statement: {}.",
                        f.detail
                    )
                },
                refs: f.ids.iter().map(|id| format!("#{id}")).collect(),
                ..Default::default()
            }
        })
        .collect::<Vec<_>>();
    for record in &records {
        if nominal_measured(record) {
            let units = if record.kind == "angular" {
                "deg"
            } else {
                "mm"
            };
            let detail = read.findings.iter().find(|f| {
                f.part == Some(pmi::PartId(record.part))
                    && f.ids.contains(&record.part21_id)
                    && missing_nominal_finding(f)
            }).map_or("a dimension without a dimensional_characteristic_representation: no value is stated", |f| f.detail.as_str());
            reader_problems.push(crate::model::Problem {
                id: format!("p:pmi_read_fact:{}:measured_nominal", record.source_id),
                code: "pmi_read_fact".into(),
                severity: "info".into(),
                message: format!("PMI dimension {}: {detail}. Its nominal {} {} was measured from the referenced geometry; the file states no nominal, and only explicitly stated tolerances are retained.", record.source_id, fmt(record.value.expect("measured nominal"), None), units),
                refs: vec![record.source_id.clone(), format!("#{}", record.part21_id)],
                ..Default::default()
            });
        }
    }
    for record in &mut records {
        for (i, f) in read.findings.iter().enumerate() {
            if repaired_read(f) || f.kind == pmi::FindingKind::Fact {
                attached[i] = true;
                continue;
            }
            let on = f.part == Some(pmi::PartId(record.part))
                && f.ids
                    .first()
                    .is_some_and(|id| record.instances.binary_search(id).is_ok());
            if on {
                if nominal_measured(record) && missing_nominal_finding(f) {
                    attached[i] = true;
                    continue;
                }
                if f.detail.contains("more than one id_attribute")
                    && !requirement_id_owner(doc, record, f.ids[0])
                {
                    continue;
                }
                attached[i] = true;
                // Identifier ambiguity does not invalidate the requirement's geometry or
                // value. Retain it as item metadata and report it at the final carrier.
                if f.detail.contains("more than one id_attribute") {
                    record.fields.retain(|(name, _)| name != "id");
                    record
                        .fields
                        .push(("id ambiguity".into(), Field::Text(f.detail.clone())));
                    continue;
                }
                record.blockers.push(format!(
                    "haecceity {} on #{} ({}): {}",
                    f.kind.as_str(),
                    f.ids[0],
                    f.entity,
                    f.detail
                ));
            }
        }
    }
    for (f, _) in read.findings.iter().zip(&attached).filter(|(_, a)| !**a) {
        // A finding on another part than the one drawn is that part's.
        if f.part.is_some_and(|p| !drawn(p.0)) {
            continue;
        }
        if repaired_read(f) || f.kind == pmi::FindingKind::Fact {
            continue;
        }
        let subject = f.ids.first().copied().unwrap_or(0);
        let bom_fact = crate::bom::accounts_for(doc, subject, defs);
        let bookkeeping = is_legacy_answers(doc, subject) || bom_fact;
        sources.push(Source {
            id: format!("finding:#{subject}"),
            category: category_of_entity(&f.entity),
            entity: f.entity.clone(),
            instances: f.ids.clone(),
            outcome: if bookkeeping {
                Outcome::Extracted
            } else {
                Outcome::NotExtracted
            },
            reason: if bom_fact {
                "File-stated BOM property or BOM bookkeeping (not a drawing requirement)".into()
            } else if bookkeeping {
                format!(
                    "Draftwright stored answers (not a drawing requirement); {}",
                    f.detail
                )
            } else {
                format!("haecceity {}: {}", f.kind.as_str(), f.detail)
            },
        });
    }
    attach_record_geometry(part, &mut records);
    records.sort_by_key(|r| (r.category, r.part));
    let mut record_sources: Vec<Source> = records
        .iter()
        .map(|r| Source {
            id: r.source_id.clone(),
            category: r.category,
            entity: entity_name(doc, r.part21_id),
            instances: r.instances.clone(),
            outcome: if r.blockers.is_empty() {
                Outcome::Extracted
            } else {
                Outcome::PartiallyExtracted
            },
            reason: if specify_root(doc, r) {
                format!("{SPECIFY_WRITTEN} {}", r.blockers.join("; "))
            } else if r.blockers.is_empty() && crate::pmi_lowering::no_part_requirement(r) {
                "info: no requirement stated; read, with no drawing needed".into()
            } else {
                r.blockers.join("; ")
            },
        })
        .collect();
    record_sources.append(&mut sources);
    let materials = read
        .parts
        .iter()
        .zip(&read.provenance.parts)
        .enumerate()
        .filter(|(p, _)| drawn(*p))
        .filter_map(|(p, (pmi, prov))| {
            // Kept even when unusable (an empty designation), so the lint reports it rather than
            // the file seeming to state no material; named as the census names it when the
            // reading carries no `PROPERTY_DEFINITION` instance.
            let m = pmi.material.as_ref()?;
            let source_id = prov
                .material
                .iter()
                .find(|&&id| entity_name(doc, id) == "property_definition")
                .map_or_else(|| format!("material:{p}:0"), |id| format!("material:#{id}"));
            Some(Material {
                source_id,
                part: p,
                designation: m.id.clone(),
                name: m.name.clone(),
            })
        })
        .collect();
    let a = &read.accounting;
    let readers: Vec<_> = read
        .parts
        .iter()
        .zip(&read.provenance.parts)
        .enumerate()
        .map(|(p, (pmi, prov))| PartReader {
            doc,
            p,
            pmi,
            prov,
            identifiers: &read.identifiers.parts[p],
            def: Some(&defs[p]),
            drawing: &drawing,
            numbers: characteristic_numbers(doc, pmi, prov),
        })
        .collect();
    let callouts = read
        .callouts
        .iter()
        .filter_map(|c| {
            // A callout linked only to other assembly parts is not this part's annotation.
            let targets = c.links.iter().flat_map(|l| &l.targets).collect::<Vec<_>>();
            if !targets.is_empty() && targets.iter().all(|t| !drawn(t.part.0)) {
                return None;
            }
            Some(graphical::Callout {
                id: c.id,
                name: c.name.clone(),
                legacy_sources: legacy_presentations
                    .get(&c.id)
                    .map(|s| s.iter().cloned().collect())
                    .unwrap_or_default(),
                links: c
                    .links
                    .iter()
                    .map(|l| graphical::Link {
                        association: l.association,
                        definition: l.definition,
                        entity: l.entity.clone(),
                        sources: record_sources
                            .iter()
                            .filter(|s| {
                                s.outcome == Outcome::NotExtracted
                                    && s.instances.first() == Some(&l.definition)
                            })
                            .map(|s| s.id.clone())
                            .collect(),
                        targets: l
                            .targets
                            .iter()
                            .filter(|t| drawn(t.part.0))
                            .map(|t| {
                                let p = t.part.0;
                                graphical::Target {
                                    part: p,
                                    item: format!("{:?}", t.item),
                                    sources: readers[p].presented_sources(
                                        &t.item,
                                        l.definition,
                                        &records,
                                        &record_sources,
                                    ),
                                }
                            })
                            .collect(),
                    })
                    .collect(),
            })
        })
        .collect();
    Ok(Report {
        schema: PMI_SCHEMA.into(),
        parts: defs.iter().map(|d| d.name.clone()).collect(),
        drawn: chosen,
        sources: record_sources,
        records,
        accounting: Accounting {
            semantic: a.semantic,
            consumed: a.consumed,
            reported: a.reported,
            presentation: a.presentation,
            validation: a.validation,
        },
        materials,
        reader: Box::new(ReaderEvidence {
            problems: reader_problems,
            callouts,
        }),
        spec_problems: Vec::new(),
    })
}

fn attach_record_geometry(part: &Part, records: &mut [Record]) {
    for r in records.iter_mut() {
        r.geometry = match r.category {
            Category::GeometricTolerance => Some(tolerance_geometry(part, r)),
            Category::Datum => Some(datum_geometry(part, r)),
            Category::Dimension => Some(crate::pmi_lowering::dimension_geometry(part, r)),
            Category::SurfaceLabel => Some(crate::pmi_lowering::label_geometry(part, r)),
            Category::ManufacturingRequirement if crate::pmi_lowering::owned_kind(&r.kind) => {
                Some(crate::pmi_lowering::requirement_geometry(part, r))
            }
            Category::ManufacturingRequirement if r.kind == "surface_finish" => {
                Some(finish_geometry(part, r))
            }
            _ => None,
        };
    }
    for r in records.iter_mut() {
        if crate::dims::symbolic_bore(r)
            && r.blockers
                == [format!(
                    "the ISO 286 class {} is stated without limits; they are not computed",
                    // Invariant: symbolic_bore requires a parsed ISO fit class.
                    r.fit.as_deref().expect("symbolic class")
                )]
        {
            // The literal class is understood. Other reading/geometry blockers remain fatal.
            r.blockers.clear();
        }
    }
}

/// Internal entry point serving the Python binding: lower a caller-owned semantic
/// model bound to this native part's face/edge indices.
/// No AP242 write/read is involved; this uses the file reader's typed-item conversions
/// and the same geometry attachment and downstream planning/lowering as imported PMI.
/// General tolerance tables fail; model notes and knurls are not read.
#[doc(hidden)]
pub fn from_model(part: &Part, model: &PartPmi) -> Result<Report, String> {
    model.validate().map_err(|errors| {
        errors
            .iter()
            .map(ToString::to_string)
            .collect::<Vec<_>>()
            .join("; ")
    })?;
    // The typed conversions consult this only for optional file provenance. Caller models
    // have no Part 21 instances; no requirements or geometry are serialized into it.
    let doc = Document::parse(b"ISO-10303-21;HEADER;FILE_DESCRIPTION((''),'2;1');FILE_NAME('','','',(''),(''),'','');FILE_SCHEMA(('AP242'));ENDSEC;DATA;ENDSEC;END-ISO-10303-21;".to_vec()).map_err(|e| e.to_string())?;
    let drawing = Drawing {
        part,
        chosen: Some(0),
        faces: BTreeMap::new(),
        edges: BTreeMap::new(),
    };
    let prov = PartProvenance::default();
    let identifiers = PartIdentifiers::default();
    let r = PartReader {
        doc: &doc,
        p: 0,
        pmi: model,
        prov: &prov,
        identifiers: &identifiers,
        def: None,
        drawing: &drawing,
        numbers: BTreeMap::new(),
    };
    let mut records = Vec::new();
    records.extend((0..model.dimensions.len()).map(|i| r.dimension(DimensionId(i))));
    records.extend((0..model.tolerances.len()).map(|i| r.tolerance(i)));
    records.extend((0..model.datums.len()).map(|i| r.datum(DatumId(i))));
    let mut used = Used {
        notes: vec![false; model.notes.len()],
        general: vec![false; model.general.len()],
        attributes: vec![false; model.attributes.len()],
        threads: vec![false; model.threads.len()],
        knurls: vec![false; model.knurls.len()],
        textures: vec![false; model.surface_textures.len()],
    };
    records.extend(r.semantic_threads(&mut used));
    records.extend(r.semantic_textures(&mut used));
    for general in &model.general {
        let GeneralTolerance::Class { text, .. } = general else {
            return Err("caller general tolerance tables are not yet lowered".into());
        };
        let mut item = r.record(
            Category::ManufacturingRequirement,
            0,
            &[],
            Support::default(),
            vec![],
        );
        item.kind = "general_tolerances".into();
        item.label = text.clone();
        item.fields = vec![("tolerance class".into(), Field::Text(text.clone()))];
        records.push(item);
    }
    for attribute in &model.attributes {
        if attribute.name == "manufacturing process" && attribute.on.is_none() {
            let mut item = r.record(
                Category::ManufacturingRequirement,
                0,
                &[],
                Support::default(),
                vec![],
            );
            item.kind = "general_note".into();
            item.label = attribute
                .items
                .iter()
                .filter_map(|(_, v)| match v {
                    AttributeValue::Text(text) => Some(format!("PROCESS: {text}")),
                    _ => None,
                })
                .collect::<Vec<_>>()
                .join("; ");
            records.push(item);
        }
    }
    for (index, record) in records.iter_mut().enumerate() {
        record.source_id = format!("caller:{}:{index}", category_name(record.category));
    }
    attach_record_geometry(part, &mut records);
    if let Some(why) = records
        .iter()
        .flat_map(|r| &r.blockers)
        .find(|b| b.starts_with(UNRESOLVED))
    {
        return Err(why.clone());
    }
    let sources = records
        .iter()
        .map(|r| Source {
            id: r.source_id.clone(),
            category: r.category,
            entity: "caller PMI".into(),
            instances: vec![],
            outcome: if r.blockers.is_empty() {
                Outcome::Extracted
            } else {
                Outcome::PartiallyExtracted
            },
            reason: r.blockers.join("; "),
        })
        .collect();
    let materials = model
        .material
        .iter()
        .map(|m| Material {
            source_id: "material:0:0".into(),
            part: 0,
            designation: m.id.clone(),
            name: m.name.clone(),
        })
        .collect();
    Ok(Report {
        schema: PMI_SCHEMA.into(),
        parts: vec!["part".into()],
        drawn: None,
        accounting: Accounting {
            semantic: records.len(),
            consumed: records.len(),
            reported: 0,
            presentation: 0,
            validation: 0,
        },
        records,
        sources,
        materials,
        reader: Box::default(),
        spec_problems: vec![],
    })
}

/// v0.4.2 returns usable semantic values for these explicitly repaired forms. Other
/// nonconformances still block lowering; equal/invalid bounds are not repairs.
fn repaired_read(f: &pmi::Finding) -> bool {
    f.kind == pmi::FindingKind::Nonconformance
        && (matches!(f.detail.as_str(),
            "plus/minus deviations were stated reversed; upper and lower bounds swapped (tolerance_value WR1)"
            | "dimension limits were stated reversed; upper and lower limits swapped"
            | "runout_zone_definition without an orientation angle on a non-runout tolerance; read the explicitly stated zone form without a runout angle")
            || ((f.detail.starts_with("duplicate source identifier ")
                || f.detail.starts_with("empty source identifier "))
                && f.detail.ends_with("retained as identifier metadata, with content-derived JSON ids for empty or repeated item ids")))
}

/// The drawing's part, indexed by the file instances its faces and edges were read from.
struct Drawing<'a> {
    part: &'a Part,
    chosen: Option<usize>,
    /// `ADVANCED_FACE` instance → (placed instance, drawing face).
    faces: BTreeMap<u64, Vec<(usize, usize)>>,
    edges: BTreeMap<u64, Vec<(usize, usize)>>,
}

impl<'a> Drawing<'a> {
    /// *part* indexed, checked to be read from the file whose parts are *defs*: all of them in
    /// every placement (`haecceity::read_step`), or part *chosen* alone, read once
    /// (`haecceity::StepFile::part`).
    fn new(part: &'a Part, defs: &[PartDefinition], chosen: Option<usize>) -> Result<Self, String> {
        let mut faces: BTreeMap<u64, Vec<(usize, usize)>> = BTreeMap::new();
        for f in 0..part.faces.len() {
            let s = part.face_source(f).ok_or_else(|| {
                format!("face {f} of the drawing's part was not read from a file")
            })?;
            faces.entry(s.entity).or_default().push((s.instance, f));
        }
        let mut edges: BTreeMap<u64, Vec<(usize, usize)>> = BTreeMap::new();
        for e in 0..part.edges.len() {
            if let Some(s) = part.edge_source(e) {
                edges.entry(s.entity).or_default().push((s.instance, e));
            }
        }
        let counted: usize = match chosen {
            Some(k) => defs.get(k).map_or(0, |d| d.faces.len()),
            None => defs
                .iter()
                .map(|d| d.faces.len() * d.placements.len())
                .sum(),
        };
        if counted != part.faces.len() {
            return Err(format!(
                "the file's parts have {counted} placed faces and the drawing's part {}: they \
                 were not read from the same file",
                part.faces.len()
            ));
        }
        Ok(Drawing {
            part,
            chosen,
            faces,
            edges,
        })
    }

    /// The drawing's faces (or edges) read from *entity* in one of *def*'s placements.
    fn resolve(
        map: &BTreeMap<u64, Vec<(usize, usize)>>,
        def: &PartDefinition,
        entity: u64,
    ) -> Vec<usize> {
        let placed: BTreeSet<usize> = def
            .placements
            .iter()
            .flat_map(|p| p.instances.iter().copied())
            .collect();
        map.get(&entity)
            .into_iter()
            .flatten()
            .filter(|(i, _)| placed.contains(i))
            .map(|&(_, f)| f)
            .collect()
    }
}

/// What a set of features is made of, on the drawing.
#[derive(Default)]
struct Support {
    faces: BTreeSet<usize>,
    face_ids: BTreeSet<u64>,
    edges: BTreeSet<usize>,
    instances: BTreeSet<u64>,
    /// Supplemental geometry anchors (no drawing face), by entity.
    geometry: BTreeSet<String>,
    all_around: bool,
    /// Why a face or edge could not be resolved: the record is not read.
    unresolved: Vec<String>,
}

/// The items of a part the port has read into records (or reported itself), by index.
struct Used {
    notes: Vec<bool>,
    general: Vec<bool>,
    attributes: Vec<bool>,
    threads: Vec<bool>,
    knurls: Vec<bool>,
    textures: Vec<bool>,
}

/// A structured requirement: a user defined attribute set described 'pmi-assist'.
struct Structured {
    attr: usize,
    pdef: u64,
    kind: String,
    feature: Option<FeatureId>,
    /// The Part 21 items its shape aspect is used on (draftwright's `reference_item_ids`).
    items: BTreeSet<u64>,
    fields: Vec<(String, Field)>,
    blockers: Vec<String>,
}

/// What a requirement in words is presented on (draftwright's `read_manufacturing_requirements`):
/// the shape aspects of the part's draughting callouts named like it, and their items.
struct Presentation {
    callouts: Vec<u64>,
    items: BTreeSet<u64>,
    /// A thread's callouts have several supports: draftwright keeps none of them.
    ambiguous: bool,
    reasons: Vec<String>,
}

/// Part-owned bookkeeping written by Specify's `resume::attribute_set`, Specify's
/// characteristic-number history (0.1.4, its numbering's high-water mark; the numbers
/// themselves sit on each requirement) and the editor/MCP project exporter. Match the names and
/// single text item, as Specify's `resume::is_answers` does; similarly named foreign properties
/// and feature attributes remain reportable.
fn is_draftwright_record(a: &pmi::AttributeSet) -> bool {
    let item = match a.name.as_str() {
        "pmi-assist answers" | "draftwright editor project" => a.name.as_str(),
        "specify characteristic numbers" => "history",
        _ => return false,
    };
    a.on.is_none()
        && matches!(a.items.as_slice(), [(name, AttributeValue::Text(_))] if name == item)
}

/// The producer's exact UDA spelling. Text keeps its file spelling (including leading zeros).
fn characteristic_value(a: &pmi::AttributeSet) -> Option<String> {
    if a.name != "characteristic number" {
        return None;
    }
    match a.items.as_slice() {
        [(name, AttributeValue::Text(n))]
            if name == "characteristic number"
                && !n.is_empty()
                && n.bytes().all(|b| b.is_ascii_digit())
                && n.bytes().any(|b| b != b'0') =>
        {
            Some(n.clone())
        }
        [(name, AttributeValue::Integer(n))] if name == "characteristic number" && *n > 0 => {
            Some(n.to_string())
        }
        _ => None,
    }
}

/// Whether a finding's subject is the requirement's own id carrier, not its support.
fn requirement_id_owner(doc: &Document, r: &Record, owner: u64) -> bool {
    match r.category {
        Category::Datum => {
            attributes(doc, owner, "DATUM").is_some() && text_of(doc, owner, "DATUM", 4) == r.label
        }
        Category::ManufacturingRequirement => match r.kind.as_str() {
            "internal_thread" | "external_thread" | "knurl" => {
                ref_of(doc, owner, "PRODUCT_DEFINITION_SHAPE", 2) == Some(r.part21_id)
            }
            "surface_finish" | "surface_texture" => {
                text_of(doc, owner, "PROPERTY_DEFINITION", 0) == "surface texture"
            }
            _ => owner == r.part21_id,
        },
        _ => owner == r.part21_id,
    }
}

fn characteristic_owner(doc: &Document, owner: u64) -> bool {
    let supported = |name: &str| {
        [
            "dimensional_size",
            "dimensional_location",
            "geometric_tolerance",
            "datum_feature",
        ]
        .iter()
        .any(|root| haecceity::express::is_a(&name.to_ascii_lowercase(), root))
    };
    match doc.get(owner) {
        Some(RawEntity::Simple { name, .. }) => supported(name),
        Some(RawEntity::Complex { parts, .. }) => parts.iter().any(|p| supported(&p.name)),
        None => false,
    }
}

fn characteristic_numbers(
    doc: &Document,
    pmi: &PartPmi,
    prov: &PartProvenance,
) -> BTreeMap<(Category, u64), BTreeSet<String>> {
    let mut numbers: BTreeMap<(Category, u64), BTreeSet<String>> = BTreeMap::new();
    for (i, a) in pmi.attributes.iter().enumerate() {
        let Some(number) = characteristic_value(a) else {
            continue;
        };
        let category = match a.on {
            Some(NoteOwner::Dimension(_)) => Category::Dimension,
            Some(NoteOwner::Tolerance(_)) => Category::GeometricTolerance,
            Some(NoteOwner::Feature(_)) => Category::Datum,
            _ => continue,
        };
        if let Some(owner) = prov.attributes.get(i).and_then(|ids| {
            ids.iter()
                .find_map(|&id| ref_of(doc, id, "PROPERTY_DEFINITION", 2))
        }) {
            numbers.entry((category, owner)).or_default().insert(number);
        }
    }
    numbers
}

/// Python Specify's earlier stored-answer form, documented by Specify's `resume` module:
/// a property on the product definition, described 'schema 1', with one named text item.
/// It has no UDA association, so haecceity reports it as an unmodelled property.
fn is_legacy_answers(doc: &Document, id: u64) -> bool {
    const NAME: &str = "pmi-assist answers";
    if text_of(doc, id, "PROPERTY_DEFINITION", 0) != NAME
        || text_of(doc, id, "PROPERTY_DEFINITION", 1) != "schema 1"
        || !ref_of(doc, id, "PROPERTY_DEFINITION", 2)
            .is_some_and(|owner| attributes(doc, owner, "PRODUCT_DEFINITION").is_some())
    {
        return false;
    }
    let reps = representations(doc, id);
    let [rep] = reps.iter().copied().collect::<Vec<_>>()[..] else {
        return false;
    };
    let items = refs_of(doc, rep, "REPRESENTATION", 1);
    matches!(items.as_slice(), [item]
        if text_of(doc, *item, "DESCRIPTIVE_REPRESENTATION_ITEM", 0) == NAME
        && matches!(attributes(doc, *item, "DESCRIPTIVE_REPRESENTATION_ITEM"), Some(a) if matches!(a.get(1), Some(Attribute::String(_)))))
}

struct PartReader<'a> {
    doc: &'a Document,
    p: usize,
    pmi: &'a PartPmi,
    prov: &'a PartProvenance,
    identifiers: &'a PartIdentifiers,
    def: Option<&'a PartDefinition>,
    drawing: &'a Drawing<'a>,
    numbers: BTreeMap<(Category, u64), BTreeSet<String>>,
}

impl PartReader<'_> {
    /// Resolve the reader's typed, part-local IDs through the same roots as semantic lowering.
    /// Face/support overlap and the graphical annotation's wording are not identity evidence.
    fn presented_sources(
        &self,
        item: &pmi::PresentedItem,
        definition: u64,
        records: &[Record],
        sources: &[Source],
    ) -> Vec<String> {
        use pmi::PresentedItem as I;
        let (kind, index, own) = match item {
            I::Dimension(id) => return vec![self.dimension(*id).source_id],
            I::Tolerance(id) => return vec![self.tolerance(id.0).source_id],
            I::Datum(id) => return vec![self.datum(*id).source_id],
            I::Feature(id) => {
                let mut ids: Vec<_> = self
                    .pmi
                    .datums
                    .iter()
                    .enumerate()
                    .filter(|(_, d)| d.feature() == Some(*id))
                    .map(|(i, _)| self.datum(DatumId(i)).source_id)
                    .collect();
                ids.extend(
                    records
                        .iter()
                        .filter(|r| {
                            r.part == self.p
                                && (matches!(r.kind.as_str(), "common_label" | "surface_label")
                                    || r.category == Category::ManufacturingRequirement)
                                && (r.part21_id == definition
                                    || ref_of(self.doc, r.part21_id, "PROPERTY_DEFINITION", 2)
                                        == Some(definition))
                        })
                        .map(|r| r.source_id.clone()),
                );
                ids.sort();
                ids.dedup();
                return ids;
            }
            I::Note(i) => ("note", *i, self.prov.notes.get(*i)),
            I::Attribute(i) => ("attribute_set", *i, self.prov.attributes.get(*i)),
            I::SurfaceTexture(i) => ("surface_texture", *i, self.prov.surface_textures.get(*i)),
            I::General(i) => ("general_tolerance", *i, self.prov.general.get(*i)),
            I::Thread(i) => ("thread", *i, self.prov.threads.get(*i)),
            I::Knurl(i) => ("knurl", *i, self.prov.knurls.get(*i)),
            I::DatumTarget(i) => ("datum_target", i.0, self.prov.datum_targets.get(i.0)),
            I::Material => {
                return vec![self.property(&self.prov.material).map_or_else(
                    || format!("material:{}:0", self.p),
                    |id| format!("material:#{id}"),
                )];
            }
        };
        let own = own.map_or(&[][..], Vec::as_slice);
        let definition_root = matches!(item, I::Thread(_) | I::Knurl(_) | I::SurfaceTexture(_));
        let root = match item {
            // The native manufacturing readers above use these definition roots, not a
            // property on their supporting feature. Keep precisely that source identity.
            I::Thread(_) | I::Knurl(_) => own.first().copied(),
            I::SurfaceTexture(_) => self
                .subject(own, &|n| n == "SURFACE_TEXTURE_REPRESENTATION")
                .or_else(|| own.first().copied()),
            _ => self.property(own),
        };
        let mut ids: Vec<_> = records
            .iter()
            .filter(|r| {
                r.part == self.p
                    && root.is_some_and(|root| {
                        if definition_root {
                            r.part21_id == root
                        } else {
                            r.instances.contains(&root)
                        }
                    })
            })
            .map(|r| r.source_id.clone())
            .collect();
        // `keep` can retain the same definition as a refused source instead of a record.
        // Match its own source root, never any face/support instance it happens to share.
        if let Some(root) = root {
            ids.extend(
                sources
                    .iter()
                    .filter(|s| {
                        s.id.split_once(":#")
                            .and_then(|(_, id)| id.parse::<u64>().ok())
                            == Some(root)
                    })
                    .map(|s| s.id.clone()),
            );
        }
        let census = format!("{kind}:{}:{index}", self.p);
        if sources.iter().any(|s| s.id == census) {
            ids.push(census);
        }
        ids.sort();
        ids.dedup();
        ids
    }

    fn read(
        &self,
        records: &mut Vec<Record>,
        sources: &mut Vec<Source>,
        legacy: &mut BTreeMap<u64, BTreeSet<String>>,
    ) {
        for d in 0..self.pmi.dimensions.len() {
            self.keep(self.dimension(DimensionId(d)), records, sources);
        }
        for t in 0..self.pmi.tolerances.len() {
            self.keep(self.tolerance(t), records, sources);
        }
        for d in 0..self.pmi.datums.len() {
            self.keep(self.datum(DatumId(d)), records, sources);
        }
        let mut used = Used {
            notes: vec![false; self.pmi.notes.len()],
            general: vec![false; self.pmi.general.len()],
            attributes: vec![false; self.pmi.attributes.len()],
            threads: vec![false; self.pmi.threads.len()],
            knurls: vec![false; self.pmi.knurls.len()],
            textures: vec![false; self.pmi.surface_textures.len()],
        };
        for r in self.semantic_knurls(&mut used) {
            self.keep(r, records, sources);
        }
        for r in self.semantic_threads(&mut used) {
            self.keep(r, records, sources);
        }
        for r in self.semantic_textures(&mut used) {
            self.keep(r, records, sources);
        }
        for r in self.part_notes(&mut used) {
            self.keep(r, records, sources);
        }
        // Structured requirements first: their attribute sets are not labels.
        for r in self.requirements(sources, &mut used, legacy) {
            self.keep(r, records, sources);
        }
        for r in self.labels(sources, &mut used) {
            self.keep(r, records, sources);
        }
        self.not_projected(sources, &used);
    }

    /// PMI practice §7.4 semantic text on the part, one attribute set per note. These
    /// sets have no presentation requirement and are read independently of their producer.
    fn part_notes(&self, used: &mut Used) -> Vec<Record> {
        let mut out = Vec::new();
        let normalized = |s: &str| {
            s.split_whitespace()
                .collect::<Vec<_>>()
                .join(" ")
                .to_lowercase()
        };
        for (i, a) in self.pmi.attributes.iter().enumerate() {
            if used.attributes[i] || normalized(&a.name) != "semantic text" {
                continue;
            }
            let notes: Vec<_> = a
                .items
                .iter()
                .filter(|(kind, _)| {
                    crate::pmi_lowering::part_note_kind(&normalized(kind)).is_some()
                        || normalized(kind) == "semantic text"
                })
                .collect();
            if notes.is_empty() {
                continue;
            }
            let ids = self.prov.attributes.get(i).map_or(&[][..], Vec::as_slice);
            let Some(pdef) = self.property(ids) else {
                continue;
            };
            used.attributes[i] = true;
            let mut blockers = Vec::new();
            if notes.len() != 1 {
                blockers.push("part semantic text must state one note per attribute set".into());
            }
            let (kind, value) = notes[0];
            let (category, kind, support) = match a.on {
                None => (
                    Category::ManufacturingRequirement,
                    crate::pmi_lowering::part_note_kind(&normalized(kind))
                        .unwrap_or("general_note"),
                    Support::default(),
                ),
                Some(NoteOwner::Feature(f)) => {
                    (Category::SurfaceLabel, "flag_note", self.support(&[f]))
                }
                _ => {
                    blockers.push("semantic text attached to a dimension or tolerance needs an explicit annotation owner".into());
                    (
                        Category::ManufacturingRequirement,
                        "semantic_text",
                        Support::default(),
                    )
                }
            };
            let mut r = self.record(category, pdef, ids, support, blockers);
            r.kind = kind.into();
            if let AttributeValue::Text(text) = value {
                r.label = text.clone();
            } else {
                r.blockers
                    .push("part semantic text note is not text".into());
            }
            // Specify's one-based integer `note index` is surfaced by haecceity. Keep the
            // older order/index names too; absent indices retain the model's instance order.
            for (name, value) in &a.items {
                let name = normalized(name);
                if matches!(name.as_str(), "order" | "index" | "note index") {
                    let index = match value {
                        AttributeValue::Text(t) => t.trim().parse::<u64>().ok(),
                        AttributeValue::Integer(n) => u64::try_from(*n).ok(),
                        _ => None,
                    }
                    .filter(|&n| name != "note index" || n > 0);
                    if let Some(index) = index {
                        r.fields
                            .push(("note order".into(), Field::Text(index.to_string())));
                    } else {
                        r.blockers.push(if name == "note index" {
                            "part note index is not a positive integer".into()
                        } else {
                            "part note order/index is not a non-negative integer".into()
                        });
                    }
                }
            }
            out.push(r);
        }
        out
    }

    // AP242 turned_knurl as a structured knurl: its pattern, its major diameter as the maximum
    // diameter after knurling and its diametral pitch, the circular pitch specify-core writes,
    // as draftwright's linear pitch, but only where its number of teeth confirms that reading
    // (π·major diameter / pitch within one tooth of it); otherwise the record is not lowered.
    fn semantic_knurls(&self, used: &mut Used) -> Vec<Record> {
        let mut out = Vec::new();
        for (index, k) in self.pmi.knurls.iter().enumerate() {
            let pattern = match k.pattern {
                pmi::KnurlPattern::Straight => "straight",
                pmi::KnurlPattern::Diamond => "diamond",
                pmi::KnurlPattern::Diagonal => continue,
            };
            let own = self
                .prov
                .knurls
                .get(index)
                .map(Vec::as_slice)
                .unwrap_or_default();
            let mut r = self.record(
                Category::ManufacturingRequirement,
                own.first().copied().unwrap_or(0),
                own,
                self.support(&[k.feature]),
                Vec::new(),
            );
            r.source_id = format!("manufacturing_requirement:#{}", r.part21_id);
            r.kind = "knurl".into();
            r.label = "knurl".into();
            let (pitch, diameter) = (k.diametral_pitch.mm(), k.major_diameter.mm());
            r.fields = vec![
                ("pattern".into(), Field::Text(pattern.into())),
                ("pitch".into(), Field::Length(pitch)),
                ("maximum diameter".into(), Field::Length(diameter)),
            ];
            let teeth = std::f64::consts::PI * diameter / pitch;
            match k.number_of_teeth {
                Some(n) if (teeth - f64::from(n.0)).abs() <= 1.0 => {}
                Some(n) => r.blockers.push(format!(
                    "turned knurl pitch {pitch} and tooth count {} disagree: \
                     π·{diameter}/{pitch} is {teeth:.1} teeth",
                    n.0
                )),
                None => r
                    .blockers
                    .push("turned knurl has no tooth count to confirm its pitch".into()),
            }
            used.knurls[index] = true;
            out.push(r);
        }
        out
    }

    // Semantic threads use their stated manufacturing fields and the native lowering rules. An
    // external thread (the editor's M10×1.5-6g on a turned diameter) is read the same way, as
    // draftwright's structured `external_thread`: its 'thread manufacturing' attribute set states
    // its pitch and thread length.
    fn semantic_threads(&self, used: &mut Used) -> Vec<Record> {
        let mut out = Vec::new();
        for (index, thread) in self.pmi.threads.iter().enumerate() {
            if thread.form != "M" {
                continue;
            }
            let side = match thread.side {
                pmi::ThreadSide::Internal => "internal",
                pmi::ThreadSide::External => "external",
            };
            let attributes: Vec<_> = self
                .pmi
                .attributes
                .iter()
                .enumerate()
                .filter(|(_, a)| {
                    a.name == "thread manufacturing"
                        && a.on == Some(NoteOwner::Feature(thread.feature))
                })
                .collect();
            let [(attribute_index, attribute)] = attributes.as_slice() else {
                continue;
            };
            let own = self
                .prov
                .threads
                .get(index)
                .map(Vec::as_slice)
                .unwrap_or_default();
            let mut instances = own.to_vec();
            instances.extend(
                self.prov
                    .attributes
                    .get(*attribute_index)
                    .into_iter()
                    .flatten(),
            );
            instances.sort_unstable();
            instances.dedup();
            let mut r = self.record(
                Category::ManufacturingRequirement,
                own.first().copied().unwrap_or(0),
                &instances,
                self.support(&[thread.feature]),
                Vec::new(),
            );
            r.source_id = format!("manufacturing_requirement:#{}", r.part21_id);
            r.kind = format!("{side}_thread");
            let designation = thread.qualifier.clone().unwrap_or_default();
            r.label = format!("{side} thread");
            r.fields = vec![
                ("thread side".into(), Field::Text(side.into())),
                ("designation".into(), Field::Text(designation)),
                (
                    "nominal size".into(),
                    Field::Text(format!("M{}", thread.major_diameter.mm())),
                ),
                ("fit class".into(), Field::Text(thread.fit_class.clone())),
                (
                    "hand".into(),
                    Field::Text(
                        if thread.hand == pmi::Hand::Left {
                            "left"
                        } else {
                            "right"
                        }
                        .into(),
                    ),
                ),
            ];
            if thread.fit_class.is_empty() {
                r.blockers
                    .push("semantic thread has no stated fit class".into());
            }
            for (name, value) in &attribute.items {
                let value = match value {
                    AttributeValue::Text(s) => Some(Field::Text(s.clone())),
                    AttributeValue::Measure(v) => v.as_length().map(|l| Field::Length(l.mm())),
                    _ => None,
                };
                if let Some(value) = value {
                    r.fields.push((name.clone(), value));
                }
            }
            used.threads[index] = true;
            used.attributes[*attribute_index] = true;
            out.push(r);
        }
        out
    }

    // AP242 surface textures (ISO 10303-1110, ISO 1302) as manufacturing requirements, which
    // draftwright does not read: the part's own (no faces) is its default finish, kind
    // `surface_texture` and labelled as draftwright's text route states one (`Ra 3.2 µm unless
    // otherwise specified`), which `pmi_lowering::sheet_requirements` draws beside the title
    // block; one on faces is kind `surface_finish` (draftwright's face finish), labelled with its
    // parameters (`Ra 0.8 µm`), which `pmi_lowering` draws as an ISO 1302 symbol on its face.
    // The fields keep the material removal condition and each parameter's value in mm.
    fn semantic_textures(&self, used: &mut Used) -> Vec<Record> {
        let mut out = Vec::new();
        for (index, t) in self.pmi.surface_textures.iter().enumerate() {
            let own = self
                .prov
                .surface_textures
                .get(index)
                .map(Vec::as_slice)
                .unwrap_or_default();
            let subject = self
                .subject(own, &|n| n == "SURFACE_TEXTURE_REPRESENTATION")
                .or_else(|| own.first().copied())
                .unwrap_or(0);
            let support = t.on.map(|f| self.support(&[f])).unwrap_or_default();
            let mut r = self.record(
                Category::ManufacturingRequirement,
                subject,
                own,
                support,
                Vec::new(),
            );
            r.source_id = format!("manufacturing_requirement:#{}", r.part21_id);
            let stated: Vec<String> = t
                .parameters
                .iter()
                .map(|p| format!("{} {} µm", p.characteristic, fmt_g3(p.value.mm() * 1000.0)))
                .collect();
            if t.on.is_none() {
                r.kind = "surface_texture".into();
                r.label = format!("{} unless otherwise specified", stated.join(", "));
            } else {
                r.kind = "surface_finish".into();
                r.label = stated.join(", ");
            }
            r.fields = std::iter::once((
                "material removal".to_string(),
                Field::Text(t.material_removal.term().into()),
            ))
            .chain(
                t.parameters
                    .iter()
                    .map(|p| (p.characteristic.clone(), Field::Length(p.value.mm()))),
            )
            .collect();
            used.textures[index] = true;
            out.push(r);
        }
        out
    }

    /// Requirement ids are index-parallel to provenance. Never use the feature array:
    /// a shared supporting shape is not the requirement's identity (quiddity-rust#143).
    fn identifier(&self, r: &Record) -> Option<&str> {
        let p = self.prov;
        let i = self.identifiers;
        let groups = match r.category {
            Category::Dimension if r.kind != "common_label" => vec![(&p.dimensions, &i.dimensions)],
            Category::GeometricTolerance => vec![(&p.tolerances, &i.tolerances)],
            Category::Datum => {
                // Several labels may share one DATUM_FEATURE. The id belongs to the
                // DATUM, so matching only the feature instance would borrow its first id.
                return self
                    .pmi
                    .datums
                    .iter()
                    .zip(&i.datums)
                    .find_map(|(datum, id)| {
                        (datum.label().as_str() == r.label)
                            .then_some(id.as_deref())
                            .flatten()
                    });
            }
            Category::ManufacturingRequirement => match r.kind.as_str() {
                "internal_thread" | "external_thread" => vec![(&p.threads, &i.threads)],
                "knurl" => vec![(&p.knurls, &i.knurls)],
                "surface_finish" | "surface_texture" => {
                    vec![(&p.surface_textures, &i.surface_textures)]
                }
                _ => vec![
                    (&p.notes, &i.notes),
                    (&p.attributes, &i.attributes),
                    (&p.general, &i.general),
                ],
            },
            _ => vec![(&p.attributes, &i.attributes)],
        };
        groups.into_iter().find_map(|(provenance, identifiers)| {
            provenance.iter().zip(identifiers).find_map(|(ids, value)| {
                ids.contains(&r.part21_id)
                    .then_some(value.as_deref())
                    .flatten()
            })
        })
    }

    /// Keeps a read record, or reports the one whose support does not resolve.
    fn keep(&self, mut record: Record, records: &mut Vec<Record>, sources: &mut Vec<Source>) {
        if let Some(id) = self
            .identifier(&record)
            .filter(|id| crate::characteristics::numeric(id))
        {
            record.fields.push(("id".into(), Field::Text(id.into())));
        }
        if matches!(
            record.category,
            Category::Dimension | Category::GeometricTolerance | Category::Datum
        ) && let Some(values) = self.numbers.get(&(record.category, record.part21_id))
            && values.len() == 1
            && let Some(number) = values.first()
        {
            record
                .fields
                .push(("characteristic number".into(), Field::Text(number.clone())));
        }
        if let Some(why) = record.blockers.iter().find(|b| b.starts_with(UNRESOLVED))
            && !record
                .fields
                .iter()
                .any(|(k, _)| k == "characteristic number" || k == "id")
        {
            sources.push(Source {
                id: record.source_id.clone(),
                category: record.category,
                entity: entity_name(self.doc, record.part21_id),
                instances: record.instances.clone(),
                outcome: Outcome::NotExtracted,
                reason: why.clone(),
            });
        } else {
            records.push(record);
        }
    }

    /// Items haecceity reads that the port does not project and did not *use* in a record.
    /// Known bookkeeping stays an extracted metadata source; unsupported items are not read.
    fn not_projected(&self, sources: &mut Vec<Source>, used: &Used) {
        let pmi = self.pmi;
        let prov = self.prov;
        let none: Vec<bool> = Vec::new();
        // (item, category, count, provenance, used for a record)
        type Group<'g> = (&'g str, Category, usize, &'g [Vec<u64>], &'g [bool]);
        let groups: [Group; 9] = [
            (
                "standard",
                Category::Other,
                pmi.standards.len(),
                &prov.standards,
                &none,
            ),
            (
                "general_tolerance",
                Category::ManufacturingRequirement,
                pmi.general.len(),
                &prov.general,
                &used.general,
            ),
            (
                "thread",
                Category::ManufacturingRequirement,
                pmi.threads.len(),
                &prov.threads,
                &used.threads,
            ),
            (
                "knurl",
                Category::ManufacturingRequirement,
                pmi.knurls.len(),
                &prov.knurls,
                &used.knurls,
            ),
            (
                "surface_texture",
                Category::ManufacturingRequirement,
                pmi.surface_textures.len(),
                &prov.surface_textures,
                &used.textures,
            ),
            (
                "note",
                Category::ManufacturingRequirement,
                pmi.notes.len(),
                &prov.notes,
                &used.notes,
            ),
            (
                "attribute_set",
                Category::Other,
                pmi.attributes.len(),
                &prov.attributes,
                &used.attributes,
            ),
            (
                "datum_target",
                Category::Datum,
                pmi.datum_targets.len(),
                &prov.datum_targets,
                &none,
            ),
            (
                "tolerance_relation",
                Category::GeometricTolerance,
                pmi.tolerance_relations.len(),
                &prov.tolerance_relations,
                &none,
            ),
        ];
        for (item, category, n, ids, used) in groups {
            for i in (0..n).filter(|&i| !used.get(i).copied().unwrap_or(false)) {
                let instances = ids.get(i).cloned().unwrap_or_default();
                let bookkeeping = item == "attribute_set"
                    && (is_draftwright_record(&pmi.attributes[i])
                        || instances.iter().any(|&id| {
                            crate::bom::accounts_for(
                                self.doc,
                                id,
                                std::slice::from_ref(self.def.expect("file PMI context")),
                            )
                        })
                        || (pmi.attributes[i].name == "specify pmi origin"
                            && matches!(pmi.attributes[i].on, Some(NoteOwner::Dimension(_))))
                        || (characteristic_value(&pmi.attributes[i]).is_some()
                            && ids.get(i).is_some_and(|instances| {
                                instances.iter().any(|&id| {
                                    let owner = ref_of(self.doc, id, "PROPERTY_DEFINITION", 2);
                                    owner.is_some_and(|owner| {
                                        [
                                            Category::Dimension,
                                            Category::GeometricTolerance,
                                            Category::Datum,
                                        ]
                                        .into_iter()
                                        .any(|category| {
                                            self.numbers
                                                .get(&(category, owner))
                                                .is_some_and(|values| values.len() == 1)
                                        }) && characteristic_owner(self.doc, owner)
                                    })
                                })
                            })));
                sources.push(Source {
                    id: format!("{item}:{}:{i}", self.p),
                    category,
                    entity: instances
                        .first()
                        .map_or_else(String::new, |&id| entity_name(self.doc, id)),
                    instances,
                    outcome: if bookkeeping {
                        Outcome::Extracted
                    } else {
                        Outcome::NotExtracted
                    },
                    reason: if bookkeeping {
                        format!(
                            "Draftwright record {:?} (not a drawing requirement)",
                            pmi.attributes[i].name
                        )
                    } else {
                        format!("{NOT_PROJECTED} ({})", item.replace('_', " "))
                    },
                });
            }
        }
        if pmi.material.is_some() {
            sources.push(Source {
                id: format!("material:{}:0", self.p),
                category: Category::Other,
                entity: prov
                    .material
                    .first()
                    .map_or_else(String::new, |&id| entity_name(self.doc, id)),
                instances: prov.material.clone(),
                outcome: Outcome::NotExtracted,
                reason: format!("{NOT_PROJECTED} (material)"),
            });
        }
    }

    /// The faces, edges and instances of *features*, resolved to the drawing.
    fn support(&self, features: &[FeatureId]) -> Support {
        let mut s = Support::default();
        let mut seen = BTreeSet::new();
        let mut todo: Vec<FeatureId> = features.to_vec();
        while let Some(f) = todo.pop() {
            if !seen.insert(f) {
                continue;
            }
            if let Some(ids) = self.prov.features.get(f.0) {
                s.instances.extend(ids);
            }
            match self.pmi.features.get(f.0) {
                Some(Feature::Items(anchors)) => {
                    for a in anchors {
                        self.anchor(*a, &mut s);
                    }
                }
                Some(Feature::Group { members, kind }) => {
                    s.all_around |= *kind == GroupKind::AllAround;
                    todo.extend(members);
                }
                Some(Feature::Derived { from, geometry, .. }) => {
                    if geometry.is_none() {
                        s.unresolved
                            .push("derived feature has no resolved geometry".into());
                    }
                    todo.extend(from);
                }
                None => s
                    .unresolved
                    .push(format!("{UNRESOLVED}: feature {} is not the part's", f.0)),
            }
        }
        s
    }

    fn target(&self, feature: FeatureId) -> Result<Option<DerivedTarget>, String> {
        self.target_inner(feature, &mut BTreeSet::new())
    }

    fn target_inner(
        &self,
        feature: FeatureId,
        seen: &mut BTreeSet<FeatureId>,
    ) -> Result<Option<DerivedTarget>, String> {
        if !seen.insert(feature) {
            return Err("derived target contains a cycle".into());
        }
        let result = (|| {
            let geometry = match self.pmi.features.get(feature.0) {
                Some(Feature::Derived {
                    geometry: Some(id), ..
                }) => &self.pmi.derived_geometry[id.0],
                Some(Feature::Derived { geometry: None, .. }) => {
                    return Err("derived feature has no resolved geometry".into());
                }
                Some(Feature::Group { members, .. }) => {
                    let targets = members
                        .iter()
                        .map(|&m| self.target_inner(m, seen))
                        .collect::<Result<Vec<_>, _>>()?;
                    if targets.iter().all(Option::is_none) {
                        return Ok(None);
                    }
                    return match targets.as_slice() {
                        [one] => Ok(one.clone()),
                        _ => Err("derived target group has no single axis or plane".into()),
                    };
                }
                _ => return Ok(None),
            };
            let mut support = Support::default();
            for &face in &geometry.deriving_faces {
                self.anchor(Anchor::Face(face), &mut support);
            }
            if !support.unresolved.is_empty() {
                return Err(support.unresolved.join("; "));
            }
            let point = geometry.point.each_ref().map(|l| l.mm());
            let direction = geometry.direction.0.each_ref().map(|d| d.to_f64());
            let (point, direction) = if self.drawing.chosen.is_some() {
                (point, direction)
            } else {
                let [placement] = self.def.expect("file PMI context").placements.as_slice() else {
                    return Err("derived target has more than one occurrence placement".into());
                };
                let m = &placement.placement;
                let rotate =
                    |v: [f64; 3]| [0, 1, 2].map(|i| (0..3).map(|j| m[i][j] * v[j]).sum::<f64>());
                let p = rotate(point);
                ([0, 1, 2].map(|i| p[i] + m[i][3]), rotate(direction))
            };
            Ok(Some(DerivedTarget {
                kind: match geometry.kind {
                    pmi::DerivedGeometryKind::Axis => DerivedKind::Axis,
                    pmi::DerivedGeometryKind::MedianPlane => DerivedKind::MedianPlane,
                },
                point,
                direction,
                radius: geometry.radius.as_ref().map(|r| r.mm()),
                faces: support.faces.into_iter().collect(),
            }))
        })();
        seen.remove(&feature);
        result
    }

    /// The pinned kernel validates a derived point against its analytic deriving axis.
    /// Preserve the explicit point, rather than interpreting that descriptor as an infinite
    /// axis. A group may mix these points with direct planes or circular rims.
    fn axis_points(
        &self,
        feature: FeatureId,
        seen: &mut BTreeSet<FeatureId>,
    ) -> Result<Option<Vec<AxisPoint>>, String> {
        if !seen.insert(feature) {
            return Err("derived point group contains a cycle".into());
        }
        let result = (|| {
            let geometry = match self.pmi.features.get(feature.0) {
                Some(Feature::Group { members, .. }) => {
                    let mut points = Vec::new();
                    for &member in members {
                        let Some(found) = self.axis_points(member, seen)? else {
                            return Ok(None);
                        };
                        points.extend(found);
                    }
                    return Ok(Some(points));
                }
                Some(Feature::Items(_)) => return Ok(Some(Vec::new())),
                Some(Feature::Derived { geometry, .. }) => {
                    let named = self.prov.features.get(feature.0).into_iter().flatten().any(|&id| {
                        matches!(self.doc.get(id), Some(RawEntity::Simple { name, attributes, .. })
                            if name == "DERIVED_SHAPE_ASPECT" && matches!(attributes.first(), Some(Attribute::String(n)) if n.trim().eq_ignore_ascii_case("axis point")))
                    });
                    if !named {
                        return Ok(None);
                    }
                    &self.pmi.derived_geometry[geometry
                        .ok_or("axis point has no resolved deriving axis")?
                        .0]
                }
                None => return Err("axis point is not the part's feature".into()),
            };
            let axis = self
                .target(feature)?
                .ok_or("axis point has no proven deriving axis")?;
            if axis.kind != DerivedKind::Axis {
                return Err("axis point is not derived from an axis".into());
            }
            let mut points = Vec::new();
            for id in &geometry.supplemental {
                let item = &self.pmi.geometry[id.0];
                let [(name, attrs)] = item.item.leaves.as_slice() else {
                    continue;
                };
                if name != "CARTESIAN_POINT" {
                    continue;
                }
                let [
                    pmi::GeometryValue::Text(_),
                    pmi::GeometryValue::List(coords),
                ] = attrs.as_slice()
                else {
                    continue;
                };
                let factor = item
                    .length_unit
                    .as_ref()
                    .ok_or("axis point has no supplemental length unit")?
                    .millimetres();
                let values: Option<Vec<f64>> = coords
                    .iter()
                    .map(|v| match v {
                        pmi::GeometryValue::Real(v) => Some(v.to_f64() * factor),
                        pmi::GeometryValue::Integer(v) => Some(*v as f64 * factor),
                        _ => None,
                    })
                    .collect();
                let point: [f64; 3] = values
                    .ok_or("axis point coordinates are not numeric")?
                    .try_into()
                    .map_err(|_| "axis point needs three coordinates")?;
                if point.iter().any(|v| !v.is_finite()) {
                    return Err("axis point is not finite".into());
                }
                let point = if self.drawing.chosen.is_some() {
                    point
                } else {
                    let [placement] = self
                        .def
                        .ok_or("axis point has no source placement")?
                        .placements
                        .as_slice()
                    else {
                        return Err("axis point has more than one occurrence placement".into());
                    };
                    let m = &placement.placement;
                    [0, 1, 2].map(|i| (0..3).map(|j| m[i][j] * point[j]).sum::<f64>() + m[i][3])
                };
                let delta = [0, 1, 2].map(|k| point[k] - axis.point[k]);
                let t: f64 = (0..3).map(|k| delta[k] * axis.direction[k]).sum();
                if (0..3).any(|k| (delta[k] - t * axis.direction[k]).abs() > 1e-6) {
                    return Err("supplemental axis point is off its deriving axis".into());
                }
                points.push(AxisPoint {
                    point,
                    faces: axis.faces.clone(),
                });
            }
            if points.len() != 1 {
                return Err("axis point needs one explicit supplemental point".into());
            }
            Ok(Some(points))
        })();
        seen.remove(&feature);
        result
    }

    fn targets(&self, features: &[FeatureId], r: &mut Record) {
        let mut axis_points: Vec<Vec<AxisPoint>> = features
            .iter()
            .map(|&f| {
                if r.kind != "linear" || r.type_code != Some(2) {
                    return Vec::new();
                }
                match self.axis_points(f, &mut BTreeSet::new()) {
                    Ok(points) => points.unwrap_or_default(),
                    Err(why) => {
                        r.blockers.push(why);
                        Vec::new()
                    }
                }
            })
            .collect();
        let targets: Vec<_> = features
            .iter()
            .enumerate()
            .map(|(i, &f)| match self.target(f) {
                Ok(t) => t,
                // Several finite points do not define one infinite axis. The hole-depth
                // endpoint proof resolves their complete group; other ownership stays refused.
                Err(_) if !axis_points[i].is_empty() => None,
                Err(why) => {
                    r.blockers.push(why);
                    None
                }
            })
            .collect();
        if axis_points.iter().all(Vec::is_empty) {
            axis_points.clear();
        }
        if targets.iter().any(Option::is_some) || !axis_points.is_empty() {
            r.geometry = Some(Geometry {
                targets,
                axis_points,
                ..Geometry::default()
            });
        }
    }

    fn anchor(&self, a: Anchor, s: &mut Support) {
        if self.def.is_none() {
            match a {
                Anchor::Face(i) if i.0 < self.drawing.part.faces.len() => {
                    s.faces.insert(i.0);
                }
                Anchor::Edge(i) if i.0 < self.drawing.part.edges.len() => {
                    s.edges.insert(i.0);
                }
                _ => s.unresolved.push(format!(
                    "{UNRESOLVED}: caller anchor {a:?} is not on this part"
                )),
            }
            return;
        }
        let p = self.p;
        match a {
            Anchor::Face(i) => match self.def.expect("file PMI context").faces.get(i.0) {
                Some(&entity) => {
                    let found = Drawing::resolve(
                        &self.drawing.faces,
                        self.def.expect("file PMI context"),
                        entity,
                    );
                    if found.is_empty() {
                        s.unresolved.push(format!(
                            "{UNRESOLVED}: face {} of part {p} (#{entity}) is none of the \
                             drawing's faces",
                            i.0
                        ));
                    }
                    s.faces.extend(found);
                    s.face_ids.insert(entity);
                }
                None => s.unresolved.push(format!(
                    "{UNRESOLVED}: part {p} has no face {} ({} faces)",
                    i.0,
                    self.def.expect("file PMI context").faces.len()
                )),
            },
            Anchor::Edge(i) => match self.def.expect("file PMI context").edges.get(i.0) {
                Some(&entity) => {
                    let found = Drawing::resolve(
                        &self.drawing.edges,
                        self.def.expect("file PMI context"),
                        entity,
                    );
                    if found.is_empty() {
                        s.unresolved.push(format!(
                            "{UNRESOLVED}: edge {} of part {p} (#{entity}) is none of the \
                             drawing's edges",
                            i.0
                        ));
                    }
                    s.edges.extend(found);
                }
                None => s.unresolved.push(format!(
                    "{UNRESOLVED}: part {p} has no edge {} ({} edges)",
                    i.0,
                    self.def.expect("file PMI context").edges.len()
                )),
            },
            Anchor::Geometry(g) => {
                let entity = self
                    .pmi
                    .geometry
                    .get(g.0)
                    .map_or_else(|| format!("geometry {}", g.0), |x| x.item.entity());
                if let Some(ids) = self.prov.geometry.get(g.0) {
                    s.instances.extend(ids);
                }
                s.geometry.insert(entity);
            }
        }
    }

    /// A record's common fields from its support.
    fn record(
        &self,
        category: Category,
        part21_id: u64,
        own: &[u64],
        support: Support,
        mut blockers: Vec<String>,
    ) -> Record {
        let Support {
            faces,
            face_ids,
            edges,
            mut instances,
            geometry,
            unresolved,
            ..
        } = support;
        instances.extend(own);
        blockers.splice(0..0, unresolved);
        if !geometry.is_empty() {
            blockers.push(format!(
                "applies to supplemental geometry ({}), not only the part's faces and edges",
                geometry.into_iter().collect::<Vec<_>>().join(", ")
            ));
        }
        let faces: Vec<usize> = faces.into_iter().collect();
        let ref_bbox = bbox(self.drawing.part, &faces);
        Record {
            source_id: format!("{}:#{part21_id}", category_name(category)),
            category,
            kind: String::new(),
            type_code: None,
            part: self.p,
            part21_id,
            value: None,
            upper_tol: None,
            lower_tol: None,
            lower_bound: None,
            upper_bound: None,
            fit: None,
            label: String::new(),
            datum_refs: Vec::new(),
            gtol_modifiers: Vec::new(),
            basic: false,
            dominant_axis: ref_bbox.map_or("?", dominant).into(),
            ref_bbox,
            faces,
            face_ids: face_ids.into_iter().collect(),
            face_groups: Vec::new(),
            edge_groups: Vec::new(),
            edges: edges.into_iter().collect(),
            instances: instances.into_iter().collect(),
            blockers,
            fields: Vec::new(),
            geometry: None,
        }
    }

    /// The instance among *ids* whose entity has one of *leaves* (upper case), the first of
    /// them when several do.
    fn subject(&self, ids: &[u64], leaves: &dyn Fn(&str) -> bool) -> Option<u64> {
        ids.iter().copied().find(|&id| {
            self.doc.get(id).is_some_and(|e| match e {
                RawEntity::Simple { name, .. } => leaves(name),
                RawEntity::Complex { parts, .. } => parts.iter().any(|p| leaves(&p.name)),
            })
        })
    }

    fn dimension(&self, id: DimensionId) -> Record {
        let d: &Dimension = &self.pmi.dimensions[id.0];
        let own = self
            .prov
            .dimensions
            .get(id.0)
            .map_or(&[][..], Vec::as_slice);
        let part21_id = self
            .subject(own, &|n| {
                matches!(
                    n,
                    "DIMENSIONAL_SIZE"
                        | "ANGULAR_SIZE"
                        | "DIMENSIONAL_SIZE_WITH_PATH"
                        | "DIMENSIONAL_LOCATION"
                        | "ANGULAR_LOCATION"
                        | "DIMENSIONAL_LOCATION_WITH_PATH"
                        | "DIRECTED_DIMENSIONAL_LOCATION"
                )
            })
            .or_else(|| own.first().copied())
            .unwrap_or(0);
        let mut blockers = Vec::new();
        let (groups, kind, code): (Vec<FeatureId>, &str, Option<u32>) = match &d.kind {
            DimensionKind::Size {
                feature,
                kind,
                path,
                angle,
            } => {
                if path.is_some() {
                    blockers.push("the dimension's path is not carried".into());
                }
                let (k, c) = if angle.is_some() {
                    ("angular", Some(28))
                } else {
                    size_kind(kind)
                };
                (vec![*feature], k, c)
            }
            DimensionKind::Location {
                from,
                to,
                kind,
                path,
                directed,
                angle,
            } => {
                if path.is_some() {
                    blockers.push("the dimension's path is not carried".into());
                }
                if *directed {
                    blockers.push("the dimension's direction (from, to) is not carried".into());
                }
                let (k, c) = if angle.is_some() {
                    ("angular", Some(11))
                } else {
                    location_kind(kind)
                };
                (vec![*from, *to], k, c)
            }
        };
        if let DimensionKind::Size {
            kind: SizeKind::Other(n),
            ..
        }
        | DimensionKind::Location {
            kind: LocationKind::Other(n),
            angle: None,
            ..
        } = &d.kind
        {
            blockers.push(format!("dimension kind {n:?} is not one draftwright knows"));
        }
        if let DimensionKind::Size { angle: Some(a), .. }
        | DimensionKind::Location { angle: Some(a), .. } = &d.kind
            && *a != pmi::AngleSelection::Equal
        {
            blockers.push(format!("the angle selection {a:?} is not carried"));
        }
        if let Some(q) = &d.qualifier
            && !matches!(
                q,
                pmi::Qualifier::Maximum | pmi::Qualifier::Minimum | pmi::Qualifier::Average
            )
        {
            blockers.push(format!("the value qualifier {q:?} is not carried"));
        }
        for m in &d.modifiers {
            blockers.push(format!("the dimension modifier {m:?} is not carried"));
        }
        if let Some(p) = &d.principle {
            blockers.push(format!("the tolerance principle {p:?} is not carried"));
        }
        let face_groups: Vec<Vec<usize>> = groups
            .iter()
            .map(|g| self.support(&[*g]).faces.into_iter().collect())
            .collect();
        let edge_groups: Vec<Vec<usize>> = groups
            .iter()
            .map(|g| self.support(&[*g]).edges.into_iter().collect())
            .collect();
        let support = self.support(&groups);
        let mut r = self.record(Category::Dimension, part21_id, own, support, blockers);
        r.kind = kind.into();
        r.type_code = code;
        r.face_groups = face_groups;
        if edge_groups.iter().any(|g| !g.is_empty()) {
            r.edge_groups = edge_groups;
        }
        r.value = d.nominal.as_ref().map(measure);
        if d.nominal.is_none() {
            r.blockers.push(MISSING_NOMINAL.into());
        }
        match &d.tolerance {
            DimTolerance::None => {}
            DimTolerance::Basic => r.basic = true,
            DimTolerance::Deviations(b) => {
                r.upper_tol = Some(measure(b.upper()));
                r.lower_tol = Some(measure(b.lower()));
            }
            DimTolerance::Limits(b) => {
                r.lower_bound = Some(measure(b.lower()));
                r.upper_bound = Some(measure(b.upper()));
            }
            DimTolerance::Fit { class, limits } => {
                r.fit = Some(class.to_string());
                match limits {
                    Some(b) => {
                        r.lower_bound = Some(measure(b.lower()));
                        r.upper_bound = Some(measure(b.upper()));
                    }
                    None => r.blockers.push(format!(
                        "the ISO 286 class {class} is stated without limits; they are not computed"
                    )),
                }
            }
        }
        r.label = dimension_label(&r, d);
        if let Some(suffix) = d.qualifier.as_ref().and_then(|q| match q {
            pmi::Qualifier::Maximum => Some("MAX"),
            pmi::Qualifier::Minimum => Some("MIN"),
            pmi::Qualifier::Average => Some("AVG"),
            _ => None,
        }) {
            r.label += &format!(" {suffix}");
        }
        // Intent is dimension-owned; never borrow it from a shared aspect or datum.
        let origins: Vec<_> = self
            .pmi
            .attributes
            .iter()
            .filter(|a| a.name == "specify pmi origin" && a.on == Some(NoteOwner::Dimension(id)))
            .collect();
        if let [origin] = origins.as_slice() {
            let values: Vec<_> = origin.items.iter().filter(|(k, _)| k == "origin").collect();
            if let [(.., AttributeValue::Text(value))] = values.as_slice()
                && matches!(
                    value.as_str(),
                    "general_tolerance" | "requirement" | "imported"
                )
            {
                r.fields
                    .push(("specify origin".into(), Field::Text(value.clone())));
            }
        }
        let mut todo = groups.clone();
        let mut seen = BTreeSet::new();
        while let Some(feature) = todo.pop() {
            if !seen.insert(feature) {
                continue;
            }
            for datum in &self.pmi.datums {
                if datum.feature() == Some(feature)
                    || datum.feature().is_some_and(|d| {
                        let reference = self.support(&[feature]);
                        let datum = self.support(&[d]);
                        !reference.faces.is_empty()
                            && reference.faces == datum.faces
                            && reference.edges == datum.edges
                    })
                {
                    if r.fields.iter().any(|(key, value)| {
                        key == "reference datum" && *value == Field::Text(datum.label().to_string())
                    }) {
                        continue;
                    }
                    r.fields.push((
                        "reference datum".into(),
                        Field::Text(datum.label().to_string()),
                    ));
                }
            }
            if let Some(Feature::Group { members, .. }) = self.pmi.features.get(feature.0) {
                todo.extend(members);
            }
        }
        self.targets(&groups, &mut r);
        if r.value.is_none() && r.blockers == [MISSING_NOMINAL] {
            let geometry = crate::pmi_lowering::dimension_geometry(self.drawing.part, &r);
            if let Some(value) = crate::pmi_lowering::measured_value(&r.kind, &geometry) {
                r.value = Some(value);
                r.blockers.clear();
                r.fields.push((
                    "nominal source".into(),
                    Field::Text(MEASURED_NOMINAL.into()),
                ));
                r.label = dimension_label(&r, d);
                if let Some(suffix) = d.qualifier.as_ref().and_then(|q| match q {
                    pmi::Qualifier::Maximum => Some("MAX"),
                    pmi::Qualifier::Minimum => Some("MIN"),
                    pmi::Qualifier::Average => Some("AVG"),
                    _ => None,
                }) {
                    r.label += &format!(" {suffix}");
                }
            }
        }
        r
    }

    fn tolerance(&self, t: usize) -> Record {
        let g: &GeometricTolerance = &self.pmi.tolerances[t];
        let own = self.prov.tolerances.get(t).map_or(&[][..], Vec::as_slice);
        let part21_id = self
            .subject(own, &|n| {
                n == "GEOMETRIC_TOLERANCE"
                    || ToleranceKind::of_entity(&n.to_ascii_lowercase()).is_some()
            })
            .or_else(|| own.first().copied())
            .unwrap_or(0);
        let mut blockers = Vec::new();
        let (features, dimension): (Vec<FeatureId>, Option<DimensionId>) = match &g.target {
            ToleranceTarget::Feature(f) => (vec![*f], None),
            ToleranceTarget::Dimension(d) => {
                (dimension_features(&self.pmi.dimensions[d.0]), Some(*d))
            }
            ToleranceTarget::Relation {
                relating, related, ..
            } => (vec![*relating, *related], None),
            ToleranceTarget::WholePart => (Vec::new(), None),
        };
        if let ToleranceTarget::Relation { name, .. } = &g.target {
            blockers.push(format!("applies to a shape aspect relationship {name:?}"));
        }
        let support = self.support(&features);
        let all_around = support.all_around;
        let mut modifiers: Vec<String> = Vec::new();
        if all_around {
            modifiers.push("all_around".into());
        }
        // On the part's shape, it "applies to all surfaces" (§6.3): all over, which XCAF and
        // draftwright name as a modifier.
        if g.target == ToleranceTarget::WholePart
            || g.auxiliary.contains(&AuxiliaryClassification::AllOver)
        {
            modifiers.push("all_over".into());
        }
        let material = |m: &ToleranceModifier| {
            matches!(
                m,
                ToleranceModifier::MaximumMaterialRequirement
                    | ToleranceModifier::LeastMaterialRequirement
            )
        };
        for m in g.modifiers.iter().filter(|m| !material(m)) {
            modifiers.push(m.as_enum().to_ascii_lowercase());
        }
        if let Some(z) = &g.zone {
            if z.form.is_diametral() {
                modifiers.push("diameter_zone".into());
            } else if z.form.is_spherical() {
                modifiers.push("spherical_diameter_zone".into());
            }
            if z.projected.is_some() {
                blockers.push("the projected tolerance zone is not carried".into());
            }
            if z.non_uniform.is_some() {
                blockers.push("the non-uniform tolerance zone is not carried".into());
            }
            if z.runout_angle.is_some() {
                blockers.push("the runout zone angle is not carried".into());
            }
            if z.affected_plane.is_some() {
                blockers.push("the affected plane is not carried".into());
            }
        }
        for m in g.modifiers.iter().filter(|m| material(m)) {
            modifiers.push(m.as_enum().to_ascii_lowercase());
        }
        let mut unique = BTreeSet::new();
        modifiers.retain(|m| unique.insert(m.clone()));
        if g.unit_basis.is_some() {
            blockers.push("the unit-basis (per unit area or length) is not carried".into());
        }
        if g.maximum.is_some() {
            blockers.push("the maximum tolerance value is not carried".into());
        }
        if g.unequal.is_some() {
            blockers.push("the unequally disposed zone value is not carried".into());
        }
        if g.auxiliary
            .contains(&AuxiliaryClassification::UnlessOtherwiseSpecified)
        {
            blockers.push("the 'unless otherwise specified' classification is not carried".into());
        }
        let mut datum_refs = Vec::new();
        if let Some(system) = &g.datums {
            for c in system.compartments() {
                if c.references.len() > 1 {
                    blockers.push(format!(
                        "a compartment of {} datums (common datum) is carried as separate letters",
                        c.references.len()
                    ));
                }
                if !c.modifiers.is_empty() {
                    blockers.push("datum compartment modifiers are not carried".into());
                }
                for reference in &c.references {
                    if !reference.modifiers.is_empty() {
                        blockers.push("datum reference modifiers are not carried".into());
                    }
                    match self.pmi.datums.get(reference.datum.0) {
                        Some(d) => datum_refs.push(d.label().to_string()),
                        None => blockers.push(format!(
                            "{UNRESOLVED}: datum {} is not the part's",
                            reference.datum.0
                        )),
                    }
                }
            }
        }
        if self
            .pmi
            .tolerance_relations
            .iter()
            .any(|rel| rel.relating.0 == t || rel.related.0 == t)
        {
            blockers.push(
                "it is related to another tolerance (composite, …), which is not carried".into(),
            );
        }
        let mut r = self.record(
            Category::GeometricTolerance,
            part21_id,
            own,
            support,
            blockers,
        );
        if let Some(d) = dimension {
            r.instances
                .extend(self.prov.dimensions.get(d.0).into_iter().flatten());
            r.instances.sort_unstable();
            r.instances.dedup();
        }
        let (kind, code) = tolerance_kind(g.kind);
        r.kind = kind.into();
        r.type_code = Some(code);
        r.value = g.magnitude.as_ref().map(measure);
        match r.value {
            Some(v) => r.label = format!("{kind} {}", fmt_g3(v)),
            None => {
                r.label = kind.into();
                r.blockers.push("the tolerance states no magnitude".into());
            }
        }
        r.datum_refs = datum_refs;
        r.gtol_modifiers = modifiers;
        self.targets(&features, &mut r);
        r.blockers.dedup();
        r
    }

    fn datum(&self, id: DatumId) -> Record {
        let d = &self.pmi.datums[id.0];
        let own = self.prov.datums.get(id.0).map_or(&[][..], Vec::as_slice);
        let mut features: Vec<FeatureId> = d.feature().into_iter().collect();
        let mut blockers = Vec::new();
        for t in d.targets() {
            match self.pmi.datum_targets.get(t.0) {
                Some(target) => features.extend(target.on()),
                None => blockers.push(format!(
                    "{UNRESOLVED}: datum target {} is not the part's",
                    t.0
                )),
            }
        }
        if !d.targets().is_empty() {
            blockers.push(format!(
                "established by {} datum target(s), which are not carried",
                d.targets().len()
            ));
        }
        let support = self.support(&features);
        let feature_ids: Vec<u64> = d
            .feature()
            .and_then(|f| self.prov.features.get(f.0))
            .cloned()
            .unwrap_or_default();
        let part21_id = self
            .subject(&feature_ids, &|n| n == "DATUM_FEATURE")
            .or_else(|| self.subject(own, &|n| n == "DATUM_FEATURE"))
            .or_else(|| self.subject(own, &|n| n == "DATUM"))
            .or_else(|| own.first().copied())
            .unwrap_or(0);
        // Preserve the legacy feature source for ordinary datums, but distinguish
        // separate requirements that share one datum feature by their own DATUM roots.
        let part21_id = if d.feature().is_some()
            && self
                .pmi
                .datums
                .iter()
                .filter(|other| other.feature() == d.feature())
                .count()
                > 1
        {
            self.subject(own, &|n| n == "DATUM").unwrap_or(part21_id)
        } else {
            part21_id
        };
        let mut r = self.record(Category::Datum, part21_id, own, support, blockers);
        r.kind = "datum".into();
        r.label = d.label().to_string();
        r
    }

    /// The instance among *ids* that is a `property_definition`.
    fn property(&self, ids: &[u64]) -> Option<u64> {
        self.subject(ids, &|n| n == "PROPERTY_DEFINITION")
    }

    /// The structured requirement attribute set *i* states, when it is one: a property described
    /// 'pmi-assist' (draftwright's `read_structured_manufacturing_requirements`, less 'default
    /// tolerances', which haecceity reads as general tolerances), its fields text or lengths.
    fn structured(&self, i: usize) -> Option<Structured> {
        let a = &self.pmi.attributes[i];
        let pdef = self.property(self.prov.attributes.get(i)?)?;
        if text_of(self.doc, pdef, "PROPERTY_DEFINITION", 1) != "pmi-assist" {
            return None;
        }
        let kind = a.name.to_lowercase().replace(' ', "_");
        let mut blockers = Vec::new();
        if !STRUCTURED_KINDS.contains(&kind.as_str()) {
            blockers.push("structured manufacturing property kind is unsupported".into());
        }
        if !user_defined(self.doc, pdef) {
            blockers.push(
                "structured manufacturing property has no valid user-defined attribute association"
                    .into(),
            );
        }
        let feature = match a.on {
            Some(NoteOwner::Feature(f)) => Some(f),
            _ => {
                blockers.push("structured manufacturing property has no shape aspect".into());
                None
            }
        };
        let doc = self.doc;
        let items = ref_of(doc, pdef, "PROPERTY_DEFINITION", 2)
            .filter(|&t| attributes(doc, t, "SHAPE_ASPECT").is_some())
            .map(|t| aspect_items(doc, t))
            .unwrap_or_default();
        // draftwright's `_structured_manufacturing_fields`: one representation, its items text
        // or measures.
        let reps = representations(doc, pdef);
        match reps.iter().collect::<Vec<_>>()[..] {
            [&r] => match attributes(doc, r, "REPRESENTATION") {
                Some(a) if a.len() >= 2 => {
                    for item in refs_of(doc, r, "REPRESENTATION", 1) {
                        if attributes(doc, item, "DESCRIPTIVE_REPRESENTATION_ITEM").is_none()
                            && attributes(doc, item, "MEASURE_REPRESENTATION_ITEM").is_none()
                        {
                            blockers.push(format!(
                                "structured representation item #{item} is unsupported"
                            ));
                        }
                    }
                }
                _ => blockers.push("structured manufacturing representation is malformed".into()),
            },
            _ => blockers.push(format!(
                "structured manufacturing property has {} representations",
                reps.len()
            )),
        }
        let mut fields = Vec::new();
        for (name, v) in &a.items {
            let name = name.to_lowercase();
            match v {
                AttributeValue::Text(t) => fields.push((name, Field::Text(t.clone()))),
                AttributeValue::Measure(Value {
                    quantity: Quantity::Length(l),
                    ..
                }) if l.mm().is_finite() && l.mm() > 0.0 => {
                    fields.push((name, Field::Length(l.mm())));
                }
                _ => blockers.push(format!(
                    "structured field {name:?} is neither text nor a positive length"
                )),
            }
        }
        let mut names = BTreeSet::new();
        if !fields
            .iter()
            .all(|(n, _)| !n.is_empty() && names.insert(n.clone()))
        {
            blockers.push("structured manufacturing fields have duplicate or empty names".into());
        }
        Some(Structured {
            attr: i,
            pdef,
            kind,
            feature,
            items,
            fields,
            blockers,
        })
    }

    /// The faces of a structured requirement's shape aspect.
    fn structured_support(&self, st: &Structured, blockers: &mut Vec<String>) -> Support {
        let Some(f) = st.feature else {
            return Support::default();
        };
        let s = self.support(&[f]);
        if s.faces.is_empty() && s.edges.is_empty() && s.geometry.is_empty() {
            blockers.push("structured manufacturing shape aspect has no geometry items".into());
        }
        s
    }

    /// Where requirement in words *pdef* is presented (draftwright's
    /// `read_manufacturing_requirements`): the callouts among *named* whose name is its
    /// representation's (ignoring case), else whose name's words include its kind's
    /// ([`name_tokens`]); their shape aspects on this part; and the items those are used on. A
    /// thread whose callouts have several items keeps none of them.
    fn presentation(&self, pdef: u64, named: &[(u64, String)]) -> Presentation {
        let doc = self.doc;
        let semantic = text_of(doc, pdef, "PROPERTY_DEFINITION", 1)
            .trim()
            .to_lowercase();
        let reps = representations(doc, pdef);
        let rep = match reps.iter().collect::<Vec<_>>()[..] {
            [&r] => text_of(doc, r, "REPRESENTATION", 0).trim().to_lowercase(),
            _ => String::new(),
        };
        let mut matched: Vec<u64> = named
            .iter()
            .filter(|(_, n)| !rep.is_empty() && n.to_lowercase() == rep)
            .map(|&(c, _)| c)
            .collect();
        if matched.is_empty() {
            let words = name_tokens(&semantic);
            matched = named
                .iter()
                .filter(|(_, n)| !words.is_empty() && words.is_subset(&name_tokens(n)))
                .map(|&(c, _)| c)
                .collect();
        }
        let mut reasons = Vec::new();
        let mut aspects: Vec<u64> = Vec::new();
        let mut bare = Vec::new();
        for &c in &matched {
            let of = callout_aspects(doc, c);
            if of.is_empty() {
                bare.push(format!("#{c}"));
            }
            for a in of {
                // Another part's aspect is that part's presentation.
                if ref_of(doc, a, "SHAPE_ASPECT", 2)
                    == Some(self.def.expect("file PMI context").shape)
                    && !aspects.contains(&a)
                {
                    aspects.push(a);
                }
            }
        }
        let mut items: BTreeSet<u64> = aspects.iter().flat_map(|&a| aspect_items(doc, a)).collect();
        let ambiguous =
            matches!(semantic.as_str(), "internal thread" | "external thread") && items.len() > 1;
        if ambiguous {
            reasons.push(AMBIGUOUS_CALLOUTS.to_string());
            aspects.clear();
            items.clear();
        }
        if !bare.is_empty() {
            reasons.push(format!(
                "matched semantic callout(s) have no shape-aspect association: {}",
                bare.join(", ")
            ));
        }
        let empty: Vec<String> = aspects
            .iter()
            .filter(|&&a| aspect_items(doc, a).is_empty())
            .map(|a| format!("#{a}"))
            .collect();
        if !empty.is_empty() {
            reasons.push(format!(
                "associated shape aspect(s) have no representation items: {}",
                empty.join(", ")
            ));
        }
        Presentation {
            callouts: matched,
            items,
            ambiguous,
            reasons,
        }
    }

    /// Whether Part 21 item *i* is a solid of this part: a requirement presented on the part's
    /// own solid applies to the whole part (document level), not to a face or edge of it.
    fn own_solid(&self, i: u64) -> bool {
        ["MANIFOLD_SOLID_BREP", "BREP_WITH_VOIDS"].iter().any(|e| {
            ref_of(self.doc, i, e, 1).is_some_and(|shell| {
                ["CLOSED_SHELL", "OPEN_SHELL"].iter().any(|s| {
                    refs_of(self.doc, shell, s, 1)
                        .iter()
                        .any(|&f| self.def.expect("file PMI context").face_index(f).is_some())
                })
            })
        })
    }

    /// The faces and edges of the part among Part 21 *items*; the part's own solid anchors
    /// nothing ([`Self::own_solid`]), and another item is not resolved.
    fn item_support(&self, items: &BTreeSet<u64>) -> Support {
        let mut s = Support::default();
        for &i in items {
            if let Some(f) = self.def.expect("file PMI context").face_index(i) {
                self.anchor(Anchor::Face(FaceIndex(f)), &mut s);
            } else if let Some(e) = self.def.expect("file PMI context").edge_index(i) {
                self.anchor(Anchor::Edge(EdgeIndex(e)), &mut s);
            } else if !self.own_solid(i) {
                s.unresolved.push(format!(
                    "{UNRESOLVED}: presented item #{i} is neither a face nor an edge of part {}",
                    self.p
                ));
            }
        }
        s
    }

    /// The part's manufacturing requirements (draftwright's
    /// `_manufacturing_requirement_projection`). Each requirement in words (a 'manufacturing
    /// requirement' property: haecceity's notes) is a record on the faces of the part's
    /// draughting callouts named like it ([`Self::presentation`]). A thread or knurl takes the
    /// faces and fields of the structured statement of its kind used on exactly those faces, or,
    /// its callouts ambiguous, the one whose thread designation is its own; a general tolerance
    /// takes the general tolerance class when it is the only one left. A structured statement
    /// left unpaired is a record of its own, labelled as draftwright labels it (its kind, or its
    /// tolerance class). draftwright matches callouts across the file, the port within the part.
    fn requirements(
        &self,
        sources: &mut Vec<Source>,
        used: &mut Used,
        legacy: &mut BTreeMap<u64, BTreeSet<String>>,
    ) -> Vec<Record> {
        let pmi = self.pmi;
        let doc = self.doc;
        let structured: Vec<Structured> = (0..pmi.attributes.len())
            .filter_map(|i| self.structured(i))
            .collect();
        for st in &structured {
            used.attributes[st.attr] = true;
        }
        let mut paired = vec![false; structured.len()];
        let classes: Vec<usize> = (0..pmi.general.len())
            .filter(|&g| matches!(pmi.general[g], GeneralTolerance::Class { .. }))
            .collect();
        let named: Vec<(u64, String)> = doc
            .ids()
            .filter(|&c| attributes(doc, c, "DRAUGHTING_CALLOUT").is_some())
            .map(|c| {
                (
                    c,
                    text_of(doc, c, "DRAUGHTING_CALLOUT", 0).trim().to_string(),
                )
            })
            .collect();
        let pdefs: Vec<Option<u64>> = (0..pmi.notes.len())
            .map(|n| {
                let ids = self.prov.notes.get(n).map_or(&[][..], Vec::as_slice);
                self.property(ids)
            })
            .collect();
        let shown: Vec<Option<Presentation>> = pdefs
            .iter()
            .map(|p| p.map(|p| self.presentation(p, &named)))
            .collect();
        let ambiguous: Vec<Option<Designation>> = pmi
            .notes
            .iter()
            .zip(&shown)
            .filter(|(_, s)| s.as_ref().is_some_and(|s| s.ambiguous))
            .map(|(n, _)| prose_designation(&n.text))
            .collect();
        let attribute_ids = |i: usize| self.prov.attributes.get(i).into_iter().flatten();
        let mut out = Vec::new();
        for (n, note) in pmi.notes.iter().enumerate() {
            let ids = self.prov.notes.get(n).map_or(&[][..], Vec::as_slice);
            used.notes[n] = true;
            let (Some(pdef), Some(shown)) = (pdefs[n], &shown[n]) else {
                sources.push(Source {
                    id: format!("note:{}:{n}", self.p),
                    category: Category::ManufacturingRequirement,
                    entity: String::new(),
                    instances: ids.to_vec(),
                    outcome: Outcome::NotExtracted,
                    reason: "manufacturing requirement has no property definition among its \
                             instances"
                        .into(),
                });
                continue;
            };
            if note.kind.trim().eq_ignore_ascii_case("semantic text") {
                let (category, kind, support, blockers) = match note.on {
                    None => (Category::ManufacturingRequirement, "general_note", Support::default(), Vec::new()),
                    Some(NoteOwner::Feature(f)) => (Category::SurfaceLabel, "flag_note", self.support(&[f]), Vec::new()),
                    _ => (Category::ManufacturingRequirement, "semantic_text", Support::default(), vec!["semantic text attached to a dimension or tolerance needs an explicit annotation owner".into()]),
                };
                let mut r = self.record(category, pdef, ids, support, blockers);
                r.kind = kind.into();
                r.label = note.text.clone();
                out.push(r);
                continue;
            }
            // Retain the association the existing legacy requirement reader just used.
            // Triage must neither repeat nor broaden its name-based correspondence.
            for c in &shown.callouts {
                legacy
                    .entry(*c)
                    .or_default()
                    .insert(format!("manufacturing_requirement:#{pdef}"));
            }
            if note.text.is_empty()
                && crate::pmi_lowering::part_note_kind(&note.kind.to_lowercase()).is_none()
            {
                sources.push(Source {
                    id: format!("manufacturing_requirement:#{pdef}"),
                    category: Category::ManufacturingRequirement,
                    entity: entity_name(doc, pdef),
                    instances: ids.to_vec(),
                    outcome: Outcome::NotExtracted,
                    reason: "manufacturing requirement has no authoritative text".into(),
                });
                continue;
            }
            let words = note.kind.to_lowercase();
            let words: Vec<&str> = words.split_whitespace().collect();
            let kind = if let Some(kind) = crate::pmi_lowering::part_note_kind(&words.join(" ")) {
                kind.to_string()
            } else if words.is_empty() {
                "manufacturing_requirement".to_string()
            } else {
                words.join("_")
            };
            let mut own = ids.to_vec();
            let mut fields = Vec::new();
            let mut pair = None;
            if kind == "general_tolerances" {
                let free: Vec<usize> = classes
                    .iter()
                    .copied()
                    .filter(|&g| !used.general[g])
                    .collect();
                if let [g] = free[..]
                    && let GeneralTolerance::Class { text, .. } = &pmi.general[g]
                {
                    used.general[g] = true;
                    own.extend(self.prov.general.get(g).into_iter().flatten());
                    fields.push(("tolerance class".into(), Field::Text(text.clone())));
                }
            } else {
                let mut free: Vec<usize> = (0..structured.len())
                    .filter(|&j| {
                        !paired[j]
                            && structured[j].kind == kind
                            && !shown.items.is_empty()
                            && shown.items == structured[j].items
                    })
                    .collect();
                if free.is_empty() && shown.items.is_empty() && shown.ambiguous {
                    // A designation unique among the ambiguous threads pairs it; its face is
                    // still checked when it is lowered.
                    let d = prose_designation(&note.text);
                    if d.is_some() && ambiguous.iter().filter(|x| **x == d).count() == 1 {
                        free = (0..structured.len())
                            .filter(|&j| {
                                !paired[j]
                                    && structured[j].kind == kind
                                    && structured[j].items.len() == 1
                                    && structured_designation(&structured[j].fields) == d
                            })
                            .collect();
                    }
                }
                if let [j] = free[..] {
                    paired[j] = true;
                    pair = Some(j);
                }
            }
            let mut blockers: Vec<String> = shown
                .reasons
                .iter()
                .filter(|r| pair.is_none() || r.as_str() != AMBIGUOUS_CALLOUTS)
                .cloned()
                .collect();
            let mut support = self.item_support(&shown.items);
            if let Some(j) = pair {
                let st = &structured[j];
                own.extend(attribute_ids(st.attr));
                if shown.items.is_empty() {
                    support = self.structured_support(st, &mut blockers);
                }
                blockers.extend(st.blockers.iter().cloned());
                fields = st.fields.clone();
            }
            let mut r = self.record(
                Category::ManufacturingRequirement,
                pdef,
                &own,
                support,
                blockers,
            );
            r.kind = kind;
            r.label = note.text.clone();
            r.fields = fields;
            out.push(r);
        }
        for (j, st) in structured.iter().enumerate() {
            if paired[j] {
                continue;
            }
            let mut blockers = Vec::new();
            let support = self.structured_support(st, &mut blockers);
            blockers.extend(st.blockers.iter().cloned());
            let own: Vec<u64> = attribute_ids(st.attr).copied().collect();
            let mut r = self.record(
                Category::ManufacturingRequirement,
                st.pdef,
                &own,
                support,
                blockers,
            );
            r.kind = st.kind.clone();
            r.label = st.kind.replace('_', " ");
            r.fields = st.fields.clone();
            out.push(r);
        }
        for &g in &classes {
            let GeneralTolerance::Class { text, .. } = &pmi.general[g] else {
                continue;
            };
            if used.general[g] {
                continue;
            }
            used.general[g] = true;
            let ids = self.prov.general.get(g).map_or(&[][..], Vec::as_slice);
            let Some(pdef) = self.property(ids) else {
                sources.push(Source {
                    id: format!("general_tolerance:{}:{g}", self.p),
                    category: Category::ManufacturingRequirement,
                    entity: String::new(),
                    instances: ids.to_vec(),
                    outcome: Outcome::NotExtracted,
                    reason: "general tolerance has no property definition among its instances"
                        .into(),
                });
                continue;
            };
            let mut r = self.record(
                Category::ManufacturingRequirement,
                pdef,
                ids,
                Support::default(),
                Vec::new(),
            );
            r.kind = "general_tolerances".into();
            r.label = text.clone();
            r.fields = vec![("tolerance class".into(), Field::Text(text.clone()))];
            out.push(r);
        }
        out
    }

    /// The part's surface labels and common labels (draftwright's `read_surface_labels` and
    /// `read_common_labels`): text on a shape aspect, which haecceity reads as an attribute set.
    /// A property of a shape aspect described 'NOTE' is a surface label; a 'semantic text'
    /// property of another shape aspect, datum feature or composite group is a common label,
    /// which draftwright reads only where XCAF imports it as a `CommonLabel` dimension matched by
    /// its presentation name: so one descriptive item, named, with a presentation callout, its
    /// name unique on the part. A candidate that is not read is a source not read, with why.
    fn labels(&self, sources: &mut Vec<Source>, used: &mut Used) -> Vec<Record> {
        let doc = self.doc;
        struct Candidate {
            attr: usize,
            pdef: u64,
            surface: bool,
            name: String,
            text: String,
            callouts: usize,
            reasons: Vec<String>,
            support: Support,
        }
        let mut candidates = Vec::new();
        for (i, a) in self.pmi.attributes.iter().enumerate() {
            if used.attributes[i] {
                continue;
            }
            let Some(pdef) = self
                .prov
                .attributes
                .get(i)
                .and_then(|ids| self.property(ids))
            else {
                continue;
            };
            let Some(aspect) = ref_of(doc, pdef, "PROPERTY_DEFINITION", 2) else {
                continue;
            };
            let surface = attributes(doc, aspect, "SHAPE_ASPECT").is_some()
                && text_of(doc, aspect, "SHAPE_ASPECT", 1) == "NOTE";
            let common = !surface
                && a.name.to_lowercase() == "semantic text"
                && COMMON_LABEL_ASPECTS
                    .iter()
                    .any(|e| attributes(doc, aspect, e).is_some());
            if !surface && !common {
                continue;
            }
            used.attributes[i] = true;
            let noun = if surface {
                "surface label"
            } else {
                "common label"
            };
            let mut reasons = Vec::new();
            let reps = representations(doc, pdef).len();
            if reps != 1 {
                reasons.push(format!("{noun} has {reps} linked representations"));
            }
            let texts: Vec<(&str, &str)> = a
                .items
                .iter()
                .filter_map(|(k, v)| match v {
                    AttributeValue::Text(t) => Some((k.as_str(), t.as_str())),
                    _ => None,
                })
                .collect();
            let (mut name, mut text) = ("", "");
            if reps == 1 {
                match texts[..] {
                    [(k, t)] => {
                        (name, text) = (k, t);
                        if !surface && name.is_empty() {
                            reasons.push("common label has no presentation name".into());
                        }
                        if text.is_empty() {
                            reasons.push(format!("{noun} has no authoritative text"));
                        }
                    }
                    _ => reasons.push(format!(
                        "linked representation has {} descriptive items",
                        texts.len()
                    )),
                }
            }
            let shared = callouts(doc, pdef)
                .intersection(&callouts(doc, aspect))
                .count();
            if surface && shared == 0 {
                reasons.push("surface label has no shared semantic/presentation callout".into());
            } else if !surface && shared != 1 {
                reasons.push(format!(
                    "common label has {shared} shared semantic/presentation callouts"
                ));
            }
            let support = match a.on {
                Some(NoteOwner::Feature(f)) => self.support(&[f]),
                _ => Support::default(),
            };
            if support.faces.is_empty() && support.edges.is_empty() && support.geometry.is_empty() {
                reasons.push(if surface {
                    "surface label shape aspect has no representation items".into()
                } else {
                    "common-label shape aspect has no representation items".into()
                });
            }
            candidates.push(Candidate {
                attr: i,
                pdef,
                surface,
                name: name.to_string(),
                text: text.to_string(),
                callouts: shared,
                reasons,
                support,
            });
        }
        let mut named: BTreeMap<String, usize> = BTreeMap::new();
        for c in candidates
            .iter()
            .filter(|c| !c.surface && !c.name.is_empty())
        {
            *named.entry(c.name.clone()).or_default() += 1;
        }
        let mut out = Vec::new();
        for c in candidates {
            let ids = self
                .prov
                .attributes
                .get(c.attr)
                .map_or(&[][..], Vec::as_slice);
            let refused = if c.surface {
                c.text.is_empty().then(|| c.reasons.join("; "))
            } else if c.name.is_empty() || c.callouts == 0 {
                Some(format!(
                    "not a common label draftwright reads (no named presentation of it): {}",
                    c.reasons.join("; ")
                ))
            } else if named[&c.name] > 1 {
                Some(format!(
                    "common-label correspondence is ambiguous for {:?}",
                    c.name
                ))
            } else {
                None
            };
            if let Some(reason) = refused {
                sources.push(Source {
                    id: format!("attribute_set:{}:{}", self.p, c.attr),
                    category: if c.surface {
                        Category::SurfaceLabel
                    } else {
                        Category::Other
                    },
                    entity: entity_name(doc, c.pdef),
                    instances: ids.to_vec(),
                    outcome: Outcome::NotExtracted,
                    reason,
                });
                continue;
            }
            let category = if c.surface {
                Category::SurfaceLabel
            } else {
                Category::Dimension
            };
            let mut r = self.record(category, c.pdef, ids, c.support, c.reasons);
            if c.surface {
                r.kind = "surface_label".into();
            } else {
                r.kind = "common_label".into();
                // XCAF's `CommonLabel` dimension type.
                r.type_code = Some(30);
            }
            r.label = c.text;
            out.push(r);
        }
        out
    }
}

/// The reason prefix of a record whose face, edge or reference does not resolve: not read.
const UNRESOLVED: &str = "unresolved";

/// Reader names belong in structured diagnostics, while the message explains the limitation.
pub(crate) fn reader_reason(reason: &str) -> String {
    reason
        .replace(" (haecceity reads it without one)", "")
        .replace("haecceity", "the STEP reader")
}

/// Keep a useful refusal in user-facing lint only when it contains drawing language.
/// Full diagnostics, including source identities and authored paths, remain in evidence.
pub(crate) fn public_reason(reason: &str) -> Option<String> {
    let reason = reason
        .rsplit_once("is not drawn: ")
        .map_or(reason, |(_, why)| why);
    let reason = reader_reason(reason);
    let internal = crate::wording::carries_id(&reason).is_some()
        || [
            "spec:",
            "features/",
            "faces/",
            "dimensions/",
            "annotations/",
            "sheet/",
        ]
        .iter()
        .any(|path| reason.contains(path))
        || reason
            .as_bytes()
            .windows(2)
            .any(|pair| pair[0] == b'#' && pair[1].is_ascii_digit());
    (!internal).then(|| reason.trim().trim_end_matches('.').to_string())
}

/// The reason of an item read but not projected.
const NOT_PROJECTED: &str = "read by haecceity; not projected to a draftwright record (the port \
                             projects dimensions, geometric tolerances, datums, manufacturing \
                             requirements, surface labels and common labels)";

/// draftwright's `_AMBIGUOUS_MANUFACTURING_CALLOUTS`.
const AMBIGUOUS_CALLOUTS: &str = "thread presentation callouts have multiple distinct supports";

/// The structured requirements draftwright reads (`read_structured_manufacturing_requirements`).
const STRUCTURED_KINDS: [&str; 3] = ["internal_thread", "external_thread", "knurl"];

/// The shape aspects a common label is attached to (`_COMMON_LABEL_ASPECT_ENTITIES`).
const COMMON_LABEL_ASPECTS: [&str; 3] = [
    "SHAPE_ASPECT",
    "DATUM_FEATURE",
    "COMPOSITE_GROUP_SHAPE_ASPECT",
];

/// The attributes of instance *id*'s *entity* (its leaf, in a complex instance).
fn attributes<'d>(doc: &'d Document, id: u64, entity: &str) -> Option<&'d [Attribute]> {
    match doc.get(id)? {
        RawEntity::Simple {
            name, attributes, ..
        } => (name == entity).then_some(attributes.as_slice()),
        RawEntity::Complex { parts, .. } => parts
            .iter()
            .find(|p| p.name == entity)
            .map(|p| p.attributes.as_slice()),
    }
}

/// Attribute *k* of instance *id*'s *entity* as decoded text ("" when it is none).
fn text_of(doc: &Document, id: u64, entity: &str, k: usize) -> String {
    match attributes(doc, id, entity).and_then(|a| a.get(k)) {
        Some(Attribute::String(s)) => p21::decode(s).unwrap_or_else(|_| s.clone()),
        _ => String::new(),
    }
}

/// Attribute *k* of instance *id*'s *entity* as a reference.
fn ref_of(doc: &Document, id: u64, entity: &str, k: usize) -> Option<u64> {
    match attributes(doc, id, entity)?.get(k)? {
        Attribute::EntityRef(r) => Some(*r),
        _ => None,
    }
}

/// The instances referenced by attribute *k* of *id*'s *entity* (one, or a list's).
fn refs_of(doc: &Document, id: u64, entity: &str, k: usize) -> Vec<u64> {
    match attributes(doc, id, entity).and_then(|a| a.get(k)) {
        Some(Attribute::EntityRef(r)) => vec![*r],
        Some(Attribute::List(items)) => items
            .iter()
            .filter_map(|a| match a {
                Attribute::EntityRef(r) => Some(*r),
                _ => None,
            })
            .collect(),
        _ => Vec::new(),
    }
}

/// The draughting callouts a `draughting_model_item_association` relates to *subject*
/// (draftwright's `association_callouts`).
fn callouts(doc: &Document, subject: u64) -> BTreeSet<u64> {
    const DMIA: &str = "DRAUGHTING_MODEL_ITEM_ASSOCIATION";
    doc.referrers(subject)
        .iter()
        .filter(|&&a| ref_of(doc, a, DMIA, 2) == Some(subject))
        .flat_map(|&a| refs_of(doc, a, DMIA, 4))
        .filter(|&c| attributes(doc, c, "DRAUGHTING_CALLOUT").is_some())
        .collect()
}

/// The shape aspects a `draughting_model_item_association` relates draughting callout *c* to.
fn callout_aspects(doc: &Document, c: u64) -> Vec<u64> {
    const DMIA: &str = "DRAUGHTING_MODEL_ITEM_ASSOCIATION";
    let mut out = Vec::new();
    for &a in doc.referrers(c) {
        if refs_of(doc, a, DMIA, 4).contains(&c)
            && let Some(s) = ref_of(doc, a, DMIA, 2)
            && attributes(doc, s, "SHAPE_ASPECT").is_some()
            && !out.contains(&s)
        {
            out.push(s);
        }
    }
    out
}

/// The items a `geometric_item_specific_usage` uses shape aspect *aspect* on.
fn aspect_items(doc: &Document, aspect: u64) -> BTreeSet<u64> {
    const GISU: &str = "GEOMETRIC_ITEM_SPECIFIC_USAGE";
    doc.referrers(aspect)
        .iter()
        .filter(|&&g| ref_of(doc, g, GISU, 2) == Some(aspect))
        .flat_map(|&g| refs_of(doc, g, GISU, 4))
        .collect()
}

/// A callout's or kind's name as words, for matching one with the other (draftwright's
/// `_name_tokens`): lower case, alphanumeric, a plural 's' dropped, 'requirement' left out.
fn name_tokens(name: &str) -> BTreeSet<String> {
    name.to_lowercase()
        .replace(['_', '-'], " ")
        .split_whitespace()
        .filter_map(|raw| {
            let mut w: String = raw.chars().filter(|c| c.is_alphanumeric()).collect();
            if w.chars().count() > 3 && w.ends_with('s') && !w.ends_with("ss") {
                w.pop();
            }
            (!w.is_empty() && w != "requirement").then_some(w)
        })
        .collect()
}

/// The representations a property definition is represented by.
fn representations(doc: &Document, pdef: u64) -> BTreeSet<u64> {
    const PDR: &str = "PROPERTY_DEFINITION_REPRESENTATION";
    doc.referrers(pdef)
        .iter()
        .filter(|&&r| ref_of(doc, r, PDR, 0) == Some(pdef))
        .filter_map(|&r| ref_of(doc, r, PDR, 1))
        .collect()
}

/// Whether a property definition is associated with the 'user defined attribute' general
/// property (draftwright's `uda_properties`).
fn user_defined(doc: &Document, pdef: u64) -> bool {
    const GPA: &str = "GENERAL_PROPERTY_ASSOCIATION";
    doc.referrers(pdef).iter().any(|&a| {
        ref_of(doc, a, GPA, 3) == Some(pdef)
            && ref_of(doc, a, GPA, 2).is_some_and(|g| {
                text_of(doc, g, "GENERAL_PROPERTY", 1).to_lowercase() == "user defined attribute"
            })
    })
}

/// A metric thread's designation as draftwright compares a prose requirement's with a
/// structured one's (`_prose_thread_designation`, `_structured_thread_designation`): nominal,
/// pitch, fit class (upper case) and hand.
#[derive(PartialEq)]
struct Designation(f64, f64, String, &'static str);

/// `M<nominal> x <pitch>` at the start of *s* (case-insensitive, spaces optional around `x`),
/// and the rest.
fn metric(s: &str) -> Option<(f64, f64, &str)> {
    let s = s.strip_prefix(['M', 'm'])?;
    let (nominal, rest) = number(s)?;
    let rest = rest.trim_start().strip_prefix(['x', 'X'])?.trim_start();
    let (pitch, rest) = number(rest)?;
    Some((nominal, pitch, rest))
}

/// `\d+(\.\d+)?` at the start of *s*, and the rest.
fn number(s: &str) -> Option<(f64, &str)> {
    let int = s.find(|c: char| !c.is_ascii_digit()).unwrap_or(s.len());
    if int == 0 {
        return None;
    }
    let frac = s[int..]
        .strip_prefix('.')
        .map(|f| f.find(|c: char| !c.is_ascii_digit()).unwrap_or(f.len()))
        .filter(|&n| n > 0)
        .map_or(0, |n| n + 1);
    let (n, rest) = s.split_at(int + frac);
    Some((n.parse().ok()?, rest))
}

fn prose_designation(text: &str) -> Option<Designation> {
    let (nominal, pitch, rest) = metric(text.trim())?;
    let rest = rest.strip_prefix('-')?;
    let end = rest
        .find(|c: char| !c.is_ascii_alphanumeric())
        .unwrap_or(rest.len());
    let (fit, after) = rest.split_at(end);
    let rest = after.trim_start();
    if fit.is_empty() || rest.len() == after.len() {
        return None;
    }
    let hand = rest.get(..2)?.to_ascii_uppercase();
    let bounded = rest[2..]
        .chars()
        .next()
        .is_none_or(|c| !(c.is_alphanumeric() || c == '_'));
    let hand = match hand.as_str() {
        "RH" if bounded => "RH",
        "LH" if bounded => "LH",
        _ => return None,
    };
    Some(Designation(nominal, pitch, fit.to_uppercase(), hand))
}

fn structured_designation(fields: &[(String, Field)]) -> Option<Designation> {
    let get = |k: &str| {
        fields.iter().find_map(|(n, v)| match v {
            Field::Text(t) if n == k => Some(t.as_str()),
            _ => None,
        })
    };
    let (nominal, pitch, rest) = metric(get("designation")?)?;
    if !rest.is_empty() {
        return None;
    }
    let hand = match get("hand")?.to_lowercase().as_str() {
        "rh" | "right" => "RH",
        "lh" | "left" => "LH",
        _ => return None,
    };
    Some(Designation(
        nominal,
        pitch,
        get("fit class")?.to_uppercase(),
        hand,
    ))
}

fn category_name(c: Category) -> &'static str {
    match c {
        Category::Dimension => "dimension",
        Category::GeometricTolerance => "geometric_tolerance",
        Category::Datum => "datum",
        Category::ManufacturingRequirement => "manufacturing_requirement",
        Category::SurfaceLabel => "surface_label",
        Category::Other => "other",
    }
}

/// The category of a finding's entity (lower case, `+`-joined leaves).
fn category_of_entity(entity: &str) -> Category {
    let leaves: Vec<&str> = entity.split('+').collect();
    let has = |f: &dyn Fn(&str) -> bool| leaves.iter().any(|l| f(l));
    if has(&|l| {
        l.starts_with("dimensional_")
            || l.starts_with("angular_")
            || l == "directed_dimensional_location"
    }) {
        Category::Dimension
    } else if has(&|l| l.ends_with("_tolerance") || l.starts_with("geometric_tolerance")) {
        Category::GeometricTolerance
    } else if has(&|l| l.starts_with("datum")) {
        Category::Datum
    } else {
        Category::Other
    }
}

/// An instance's entity type, lower case, a complex instance's leaves joined by `+`.
fn entity_name(doc: &Document, id: u64) -> String {
    match doc.get(id) {
        Some(RawEntity::Simple { name, .. }) => name.to_ascii_lowercase(),
        Some(RawEntity::Complex { parts, .. }) => parts
            .iter()
            .map(|p| p.name.to_ascii_lowercase())
            .collect::<Vec<_>>()
            .join("+"),
        None => String::new(),
    }
}

/// The features a dimension measures.
fn dimension_features(d: &Dimension) -> Vec<FeatureId> {
    match &d.kind {
        DimensionKind::Size { feature, .. } => vec![*feature],
        DimensionKind::Location { from, to, .. } => vec![*from, *to],
    }
}

/// A value in mm, or degrees for an angle. A length is converted from its stated digits less a
/// CAD writer's float noise (#214): at most six decimals in its own unit, one more per power of
/// ten the unit exceeds a mm by (`0.01500000000006` INCH is 0.015 in; 0.0000025 m keeps its
/// 2.5 µm), the product rounded to three decimals more, which keeps a conversion from inch, foot,
/// cm, m or µm exact (0.015 in is 0.381 mm, 0.016 in 0.4064 mm).
fn measure(v: &Value) -> f64 {
    match &v.quantity {
        Quantity::Length(l) => in_mm(l, l.value.stated_places().unwrap_or(usize::MAX)),
        Quantity::Angle(a) => match a.unit {
            AngleUnit::Degree => a.value.to_f64(),
            _ => a.rad().to_degrees(),
        },
    }
}

/// A length as the mm sheet prints it (#214): rounded first to the places its precision
/// qualifier states, in its own unit (`0.4999784176` INCH to three places is 0.500 in, 12.7 mm),
/// else as [`measure`] converts it. An unqualified value keeps the digits [`measure`] keeps, so an
/// inexact one prints them all (`0.4999784176` INCH unqualified is 12.69945136).
fn qualified(v: &Value) -> f64 {
    match (&v.quantity, v.decimal_places) {
        (Quantity::Length(l), Some(places)) => in_mm(l, usize::from(places)),
        _ => measure(v),
    }
}

/// *l* in mm: its value rounded to *places* in its own unit, capped as [`measure`] caps it, the
/// product to three more.
fn in_mm(l: &pmi::Length, places: usize) -> f64 {
    let mm = l.unit.millimetres();
    let cap = 6 + mm.log10().round().max(0.0) as usize;
    let places = places.min(cap);
    let round = |x: f64, p: usize| {
        let k = 10f64.powi(i32::try_from(p).unwrap_or(9));
        (x * k).round() / k
    };
    round(round(l.value.to_f64(), places) * mm, places + 3)
}

/// draftwright's kind and XCAF's type number for a size (`_DIM_TYPE`, 14–27).
fn size_kind(k: &SizeKind) -> (&'static str, Option<u32>) {
    match k {
        SizeKind::CurveLength => ("curve_length", Some(14)),
        SizeKind::Diameter => ("diameter", Some(15)),
        SizeKind::SphericalDiameter => ("diameter", Some(16)),
        SizeKind::Radius => ("radius", Some(17)),
        SizeKind::SphericalRadius => ("radius", Some(18)),
        // XCAF's toroidal types, which draftwright names by number (`type19` …).
        SizeKind::ToroidalMinorDiameter => ("type19", Some(19)),
        SizeKind::ToroidalMajorDiameter => ("type20", Some(20)),
        SizeKind::ToroidalMinorRadius => ("type21", Some(21)),
        SizeKind::ToroidalMajorRadius => ("type22", Some(22)),
        SizeKind::ToroidalHighMajorDiameter => ("type23", Some(23)),
        SizeKind::ToroidalLowMajorDiameter => ("type24", Some(24)),
        SizeKind::ToroidalHighMajorRadius => ("type25", Some(25)),
        SizeKind::ToroidalLowMajorRadius => ("type26", Some(26)),
        SizeKind::Thickness => ("thickness", Some(27)),
        SizeKind::Other(_) => ("size", None),
    }
}

/// draftwright's kind and XCAF's type number for a location (`_DIM_TYPE`, 1–10). The three
/// one-sided linear distances have no XCAF type.
fn location_kind(k: &LocationKind) -> (&'static str, Option<u32>) {
    match k {
        LocationKind::CurvedDistance => ("curved_dist", Some(1)),
        LocationKind::LinearDistance => ("linear", Some(2)),
        LocationKind::LinearDistanceCentreOuter => ("linear", Some(3)),
        LocationKind::LinearDistanceCentreInner => ("linear", Some(4)),
        LocationKind::LinearDistanceOuterCentre => ("linear", Some(5)),
        LocationKind::LinearDistanceOuterOuter => ("linear", Some(6)),
        LocationKind::LinearDistanceOuterInner => ("linear", Some(7)),
        LocationKind::LinearDistanceInnerCentre => ("linear", Some(8)),
        LocationKind::LinearDistanceInnerOuter => ("linear", Some(9)),
        LocationKind::LinearDistanceInnerInner => ("linear", Some(10)),
        LocationKind::LinearDistanceCentre
        | LocationKind::LinearDistanceInner
        | LocationKind::LinearDistanceOuter => ("linear", None),
        LocationKind::Other(_) => ("location", None),
    }
}

/// draftwright's kind and XCAF's type number for a tolerance (`_GTOL_TYPE`).
fn tolerance_kind(k: ToleranceKind) -> (&'static str, u32) {
    match k {
        ToleranceKind::Angularity => ("angularity", 1),
        ToleranceKind::CircularRunout => ("circular_runout", 2),
        ToleranceKind::Roundness => ("circularity", 3),
        ToleranceKind::Coaxiality => ("coaxiality", 4),
        ToleranceKind::Concentricity => ("concentricity", 5),
        ToleranceKind::Cylindricity => ("cylindricity", 6),
        ToleranceKind::Flatness => ("flatness", 7),
        ToleranceKind::Parallelism => ("parallelism", 8),
        ToleranceKind::Perpendicularity => ("perpendicularity", 9),
        ToleranceKind::Position => ("position", 10),
        ToleranceKind::LineProfile => ("profile_line", 11),
        ToleranceKind::SurfaceProfile => ("profile_surface", 12),
        ToleranceKind::Straightness => ("straightness", 13),
        ToleranceKind::Symmetry => ("symmetry", 14),
        ToleranceKind::TotalRunout => ("total_runout", 15),
    }
}

/// Why a record's faces give no single attachment: they lie in several placements of their
/// part (an assembly placing it more than once), so which one to annotate is not known.
pub(crate) fn placements_reason(r: &Record) -> Option<String> {
    // Caller PMI is bound directly to this one native shape. It has no Part 21
    // face identities or assembly occurrences to disambiguate.
    if r.source_id.starts_with("caller:") {
        return None;
    }
    (r.faces.len() > r.face_ids.len()).then(|| {
        format!(
            "its {} faces are drawn in {} placements of the part; which placement to annotate is \
             an open question",
            r.face_ids.len(),
            r.faces.len().div_ceil(r.face_ids.len().max(1))
        )
    })
}

/// The axis (0, 1, 2) a unit direction lies along, when it is principal to within
/// *tolerance*: draftwright's tests, a component ≥ 1e-6 with the others summing to at most
/// *tolerance* times it.
fn principal(d: [f64; 3], tolerance: f64) -> Option<usize> {
    let c = d.map(f64::abs);
    let k = (0..3).fold(0, |k, i| if c[i] > c[k] { i } else { k });
    let others = c[0] + c[1] + c[2] - c[k];
    (c[k] >= 1e-6 && others <= tolerance * c[k]).then_some(k)
}

/// One face's attachment, as [`datum_geometry`] and [`tolerance_geometry`] measure it from the
/// part: what a record on the face needs of it to lower. The Analysis carries one per face
/// ([`face_attachments`]), so a datum or tolerance authored in the Spec, planned with no part to
/// hand, attaches exactly as an AP242 record on the same faces does ([`crate::spec_gdt`]).
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FaceAttachment {
    /// The face's box, `[xmin, ymin, zmin, xmax, ymax, zmax]`; absent when it is not finite.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bounds: Option<[f64; 6]>,
    /// `plane` or `cylinder`; absent for any other surface.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub surface: Option<String>,
    /// A plane's normal or a cylinder's axis (0, 1, 2 for x, y, z), when it is principal.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub axis: Option<usize>,
    /// Where it lies across that axis: a plane's coordinate along it, a cylinder's axis's two
    /// coordinates across it.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub support: Vec<f64>,
    /// Its first proven site (`_datum_face_site`), then up to eight others spread over it.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub sites: Vec<[f64; 3]>,
    /// A plane's outward normal.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub normal: Option<[f64; 3]>,
    /// A cylinder's axis when it is exactly principal (`_common_principal_cylinder_axis`'s test).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub exact_axis: Option<usize>,
    /// A cylinder with no proven silhouette site (one trimmed by flats, say): proven points on
    /// it inside its outline in the view that sees it edge-on, on its half that faces that view
    /// and with nothing in front, where a surface finish's leader may end in a dot.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub face_on: Vec<[f64; 3]>,
}

/// Face *f*'s attachment on *part*.
pub(crate) fn face_attachment(part: &Part, f: usize) -> FaceAttachment {
    let b = part.face_bounds(f);
    let mut out = FaceAttachment {
        bounds: Some([b.min[0], b.min[1], b.min[2], b.max[0], b.max[1], b.max[2]]),
        ..FaceAttachment::default()
    };
    let (kind, frame) = match &part.faces[f].surface {
        Surface::Plane { frame } => ("plane", frame),
        Surface::Cylinder { frame, .. } => ("cylinder", frame),
        _ => return out,
    };
    out.surface = Some(kind.into());
    if kind == "cylinder" {
        let d = frame.z;
        let k = (0..3).fold(0, |k, i| if d[i].abs() > d[k].abs() { i } else { k });
        let exact =
            (d[k].abs() - 1.0).abs() <= 1e-6 && (0..3).all(|i| i == k || d[i].abs() <= 1e-6);
        out.exact_axis = exact.then_some(k);
    } else {
        out.normal = part.face_normal(f, 0.0, 0.0);
    }
    let Some(k) = principal(frame.z, 1e-6) else {
        return out;
    };
    let o = frame.origin;
    out.axis = Some(k);
    out.support = if kind == "plane" {
        vec![o[k]]
    } else {
        (0..3).filter(|&i| i != k).map(|i| o[i]).collect()
    };
    let found = face_sites(part, f, &b, kind, k);
    if let Some(first) = found.first() {
        out.sites.push(*first);
        out.sites.extend(spread(&found, &b, 8));
    } else if kind == "cylinder" {
        out.face_on = face_on_sites(part, f, &b, k);
    }
    out
}

/// Proven points on cylinder *f* (box *b*, axis along *k*) seen head-on in the view that sees it
/// edge-on (the front for an x or z axis, looking along +y; the side for a y axis, looking along
/// −x): at stations along the axis, midway between the axis and either side of the face's box,
/// on the face's trimmed domain, where its outward normal faces the viewer and no other face's
/// box lies between it and the viewer. Ranked by station, the nearer side first.
fn face_on_sites(part: &Part, f: usize, b: &Bounds, k: usize) -> Vec<[f64; 3]> {
    let surface = &part.faces[f].surface;
    let (Surface::Cylinder { frame, radius }, Some(domain)) = (surface, part.domain(f)) else {
        return Vec::new();
    };
    // The viewer's axis and the direction toward the viewer along it; the page axis across.
    let (depth, toward) = if k == 1 { (0, 1.0) } else { (1, -1.0) };
    let lateral = 3 - k - depth;
    let mut out = Vec::new();
    for t in [0.5, 0.3, 0.7, 0.15, 0.85] {
        for edge in [b.min[lateral], b.max[lateral]] {
            let offset = 0.5 * (edge - frame.origin[lateral]);
            if offset.is_nan() || offset.abs() >= *radius {
                continue;
            }
            let mut s = frame.origin;
            s[k] = b.min[k] + t * (b.max[k] - b.min[k]);
            s[lateral] += offset;
            s[depth] += toward * (radius * radius - offset * offset).sqrt();
            if !b.contains(s, 1e-6) {
                continue;
            }
            let Some((u, v)) = surface.parameters(s, None) else {
                continue;
            };
            let p = surface.value(u, v);
            let d = ((p[0] - s[0]).powi(2) + (p[1] - s[1]).powi(2) + (p[2] - s[2]).powi(2)).sqrt();
            let facing = part
                .face_normal(f, u, v)
                .is_some_and(|n| n[depth] * toward > 1e-6);
            let clear = (0..part.faces.len()).filter(|g| *g != f).all(|g| {
                let o = part.face_bounds(g);
                let ahead = if toward > 0.0 {
                    o.max[depth] > s[depth] + 1e-6
                } else {
                    o.min[depth] < s[depth] - 1e-6
                };
                !(ahead
                    && [k, lateral]
                        .iter()
                        .all(|&i| o.min[i] - 1e-6 <= s[i] && s[i] <= o.max[i] + 1e-6))
            });
            if d <= 1e-6 && domain.contains(u, v) && facing && clear {
                out.push(s);
            }
        }
    }
    out
}

/// Every face's attachment, for the Analysis: a face whose box, sites or normal are not finite
/// keeps only what is (JSON has no infinity), and a record on it is not lowered.
pub fn face_attachments(part: &Part) -> Vec<FaceAttachment> {
    let finite = |v: &[f64]| v.iter().all(|x| x.is_finite());
    (0..part.faces.len())
        .map(|f| {
            let mut a = face_attachment(part, f);
            if !a.bounds.is_some_and(|b| finite(&b)) {
                a.bounds = None;
            }
            if !a.sites.iter().all(|s| finite(s))
                || !a.face_on.iter().all(|s| finite(s))
                || !a.support.iter().all(|x| x.is_finite())
            {
                a.sites.clear();
                a.face_on.clear();
                a.support.clear();
                a.axis = None;
            }
            if !a.normal.is_none_or(|n| finite(&n)) {
                a.normal = None;
            }
            a
        })
        .collect()
}

/// The box centre of a face's attachment (not finite when its box is not).
fn att_centre(a: &FaceAttachment) -> [f64; 3] {
    a.bounds.map_or([f64::NAN; 3], |b| {
        [0, 1, 2].map(|k| 0.5 * (b[k] + b[k + 3]))
    })
}

/// A geometric tolerance's attachment: its faces' common principal cylinder axis, when every
/// face is a cylinder and they share one (`_common_principal_cylinder_axis`, which reads no axis
/// when any reference is not a cylindrical face). The leader's site is its faces' box centre,
/// which the record carries (`ref_bbox`).
fn tolerance_geometry(part: &Part, r: &Record) -> Geometry {
    let faces: Vec<FaceAttachment> = r.faces.iter().map(|&f| face_attachment(part, f)).collect();
    let mut g = tolerance_geometry_of(r, &faces);
    g.targets = r
        .geometry
        .as_ref()
        .map_or_else(Vec::new, |g| g.targets.clone());
    if let [Some(target)] = g.targets.as_slice() {
        g.axis = exact_target_axis(target.direction).map(|k| AXIS_NAMES[k].into());
        let centre = r
            .ref_bbox
            .map_or(target.point, |b| [0, 1, 2].map(|k| 0.5 * (b[k] + b[k + 3])));
        let t: f64 = (0..3)
            .map(|k| (centre[k] - target.point[k]) * target.direction[k])
            .sum();
        g.sites = vec![match target.kind {
            DerivedKind::Axis => [0, 1, 2].map(|k| target.point[k] + t * target.direction[k]),
            DerivedKind::MedianPlane => [0, 1, 2].map(|k| centre[k] - t * target.direction[k]),
        }];
    } else if let [Some(a), Some(b)] = g.targets.as_slice() {
        let centre = r
            .ref_bbox
            .map_or([0.0; 3], |b| [0, 1, 2].map(|k| 0.5 * (b[k] + b[k + 3])));
        match crate::pmi_lowering::derived_stations(a, b, centre) {
            Ok(points) => {
                g.sites = vec![[0, 1, 2].map(|k| 0.5 * (points[0][k] + points[1][k]))];
                let direction = if b.kind == DerivedKind::Axis {
                    b.direction
                } else {
                    a.direction
                };
                g.axis = exact_target_axis(direction).map(|k| AXIS_NAMES[k].into());
            }
            Err(why) => g.reasons.push(why.into()),
        }
    }
    g
}

fn exact_target_axis(d: [f64; 3]) -> Option<usize> {
    let k = principal(d, 1e-6)?;
    ((d[k].abs() - 1.0).abs() <= 1e-6).then_some(k)
}

/// [`tolerance_geometry`] from the attachments of *r*'s faces, in its order.
pub(crate) fn tolerance_geometry_of(r: &Record, faces: &[FaceAttachment]) -> Geometry {
    let reasons: Vec<String> = placements_reason(r).into_iter().collect();
    let mut axes = BTreeSet::new();
    let mut cylinders = r.edges.is_empty() && !r.faces.is_empty();
    for a in faces {
        match a.exact_axis {
            Some(k) if a.surface.as_deref() == Some("cylinder") => {
                axes.insert(k);
            }
            _ => cylinders = false,
        }
    }
    let axis = (cylinders && axes.len() == 1)
        // Invariant: The preceding condition requires axes.len() == 1.
        .then(|| AXIS_NAMES[*axes.first().expect("one axis")].to_string());
    Geometry {
        sites: Vec::new(),
        alternatives: Vec::new(),
        axis,
        surface: None,
        normal: None,
        reasons,
        ..Geometry::default()
    }
}

const AXIS_NAMES: [&str; 3] = ["X", "Y", "Z"];

/// A datum's attachment (`_datum_geometry_from_shapes` and `_datum_reference_normal`): every
/// face a plane or cylinder whose axis is principal, all of one kind, coplanar or coaxial; a
/// site on each; and a plane's outward normal, the faces agreeing.
fn datum_geometry(part: &Part, r: &Record) -> Geometry {
    let faces: Vec<FaceAttachment> = r.faces.iter().map(|&f| face_attachment(part, f)).collect();
    datum_geometry_of(r, &faces)
}

/// [`datum_geometry`] from the attachments of *r*'s faces, in its order.
pub(crate) fn datum_geometry_of(r: &Record, faces: &[FaceAttachment]) -> Geometry {
    let mut reasons: Vec<String> = placements_reason(r).into_iter().collect();
    let mut sites = Vec::new();
    let mut axes = Vec::new();
    let mut kinds = Vec::new();
    let mut supports: Vec<Vec<f64>> = Vec::new();
    let mut alternatives = Vec::new();
    if r.faces.is_empty() {
        reasons.push("referenced geometry is unavailable".into());
    }
    for a in faces {
        sites.push(att_centre(a));
        let Some(kind) = a.surface.as_deref() else {
            reasons.push("one datum reference is neither planar nor cylindrical".into());
            continue;
        };
        let Some(k) = a.axis else {
            reasons.push("one datum reference surface is not axis-aligned".into());
            continue;
        };
        supports.push(a.support.clone());
        axes.push(k);
        kinds.push(kind);
        match a.sites.first() {
            Some(site) => {
                // Invariant: The preceding branch has already pushed the first site.
                *sites.last_mut().expect("pushed") = *site;
                if alternatives.is_empty() && sites.len() == 1 {
                    alternatives = a.sites[1..].to_vec();
                }
            }
            None => reasons
                .push("one datum reference face has no proven interior attachment point".into()),
        }
    }
    let mut axis = None;
    if axes.is_empty() {
        reasons.push("datum reference surface is unavailable".into());
    } else if kinds.iter().any(|k| *k != kinds[0]) {
        reasons.push("datum reference faces mix planar and cylindrical surfaces".into());
    } else if axes.iter().any(|a| *a != axes[0])
        || supports[1..].iter().any(|s| {
            s.iter()
                .zip(&supports[0])
                .any(|(a, b)| (a - b).abs() > 1e-4)
        })
    {
        let qualifier = if kinds[0] == "plane" {
            "coplanar"
        } else {
            "coaxial"
        };
        reasons.push(format!("datum reference faces are not {qualifier}"));
    } else {
        axis = Some(AXIS_NAMES[axes[0]].to_string());
    }
    let surface =
        (!kinds.is_empty() && kinds.iter().all(|k| *k == kinds[0])).then(|| kinds[0].to_string());
    let mut normal = None;
    if reasons.is_empty() && surface.as_deref() == Some("plane") {
        let normals: Vec<[f64; 3]> = faces.iter().filter_map(|a| a.normal).collect();
        if normals.len() != r.faces.len() {
            reasons.push("datum reference surface has no usable normal".into());
        } else if normals[1..]
            .iter()
            .any(|n| n[0] * normals[0][0] + n[1] * normals[0][1] + n[2] * normals[0][2] < 0.999)
        {
            reasons.push("datum reference faces disagree about outward normal".into());
        } else {
            normal = Some(normals[0]);
        }
    }
    reasons.dedup();
    Geometry {
        sites,
        alternatives,
        axis,
        surface,
        normal,
        reasons,
        ..Geometry::default()
    }
}

/// A face surface finish's attachment: a datum's ([`datum_geometry`]), as its ISO 1302 symbol
/// attaches where a datum feature symbol does, on its face's edge line in the view that sees the
/// face edge-on (or on the profile of a cylinder); the reasons name the finish.
fn finish_geometry(part: &Part, r: &Record) -> Geometry {
    let faces: Vec<FaceAttachment> = r.faces.iter().map(|&f| face_attachment(part, f)).collect();
    finish_geometry_of(r, &faces)
}

/// [`finish_geometry`] from the attachments of *r*'s faces, in its order. Where no face proves a
/// silhouette site, a cylinder's head-on sites ([`FaceAttachment::face_on`]) stand in, from the
/// faces that have them (the finish applies to the whole feature; the faces of it seen from
/// behind are left out when they are coaxial with those): the geometry is then `face_on`.
pub(crate) fn finish_geometry_of(r: &Record, faces: &[FaceAttachment]) -> Geometry {
    let head_on = faces.iter().all(|a| a.sites.is_empty())
        && faces.iter().any(|a| !a.face_on.is_empty())
        && faces.iter().all(|a| {
            a.surface.as_deref() == Some("cylinder")
                && a.exact_axis.is_some()
                && a.exact_axis == faces[0].exact_axis
                && a.support.len() == 2
                && a.support
                    .iter()
                    .zip(&faces[0].support)
                    .all(|(x, y)| (x - y).abs() <= 1e-4)
        });
    let mut g = if head_on {
        let mut seen = r.clone();
        let mut used = Vec::new();
        seen.faces.clear();
        for (f, a) in r.faces.iter().zip(faces) {
            if !a.face_on.is_empty() {
                seen.faces.push(*f);
                used.push(FaceAttachment {
                    sites: a.face_on.clone(),
                    ..a.clone()
                });
            }
        }
        let mut g = datum_geometry_of(&seen, &used);
        g.face_on = true;
        g
    } else {
        datum_geometry_of(r, faces)
    };
    for why in &mut g.reasons {
        *why = why.replace("datum reference", "surface finish");
    }
    g
}

/// The proven sites on face *f* (box *b*, of *kind*, its normal or axis along *k*), the one
/// `_datum_face_site` chooses first: on a cylinder, points of the silhouette the edge-on view
/// projects (`_cylindrical_datum_site`'s stations, then others along the axis on the same side);
/// on a plane, the points of a 13 × 13 grid over its parameter range that lie on the face, nearest
/// the box's centre across the view (then in space) first. Where no grid point lies on the face
/// (a narrow ring), draftwright takes mesh triangles' centroids; the port takes finer grids (52,
/// then 208, per side). Empty when none is proven.
fn face_sites(part: &Part, f: usize, b: &Bounds, kind: &str, k: usize) -> Vec<[f64; 3]> {
    let surface = &part.faces[f].surface;
    let Some(domain) = part.domain(f) else {
        return Vec::new();
    };
    let centre = b.centre();
    if kind == "cylinder" {
        let Surface::Cylinder { frame, radius } = surface else {
            return Vec::new();
        };
        let radial = if k < 2 { 2 } else { 0 };
        let signs = if k < 2 { [-1.0, 1.0] } else { [1.0, -1.0] };
        let mut stations = vec![centre[k]];
        if k == 2 {
            stations.insert(0, centre[k] + ((b.max[2] - b.min[2]) / 4.0).min(10.0));
        }
        let on = |station: f64, sign: f64| {
            let mut s = frame.origin;
            s[k] = station;
            s[radial] += sign * radius;
            if !b.contains(s, 1e-6) {
                return None;
            }
            let (u, v) = surface.parameters(s, None)?;
            let p = surface.value(u, v);
            let d = ((p[0] - s[0]).powi(2) + (p[1] - s[1]).powi(2) + (p[2] - s[2]).powi(2)).sqrt();
            // On the face, its boundary included (draftwright measures the distance to the face,
            // 1e-6): a silhouette on the seam between two halves of a split cylinder is on both.
            let e = 1e-9;
            let inside = [(0.0, 0.0), (e, 0.0), (-e, 0.0), (0.0, e), (0.0, -e)]
                .iter()
                .any(|(du, dv)| domain.contains(u + du, v + dv));
            (d <= 1e-6 && inside).then_some(s)
        };
        let Some((first, sign)) = stations
            .iter()
            .flat_map(|&st| signs.map(|sg| (st, sg)))
            .find_map(|(st, sg)| on(st, sg).map(|s| (s, sg)))
        else {
            return Vec::new();
        };
        let mut out = vec![first];
        for t in [0.3, 0.7, 0.15, 0.85, 0.05, 0.95] {
            if let Some(s) = on(b.min[k] + t * (b.max[k] - b.min[k]), sign) {
                out.push(s);
            }
        }
        // Keep the original ranking, then prove the other silhouette against the same
        // trimmed domain. Mirroring an attachment alone would be invalid on a partial face.
        for station in stations
            .into_iter()
            .chain([0.3, 0.7, 0.15, 0.85, 0.05, 0.95].map(|t| b.min[k] + t * (b.max[k] - b.min[k])))
        {
            if let Some(s) = on(station, -sign) {
                out.push(s);
            }
        }
        return out;
    }
    let Some((u0, u1, v0, v1)) = part.uv_bounds(f) else {
        return Vec::new();
    };
    if ![u0, u1, v0, v1].iter().all(|x| x.is_finite()) {
        return Vec::new();
    }
    let hidden = if k == 1 { 0 } else { 1 };
    for n in [13usize, 52, 208] {
        let mut found: Vec<([f64; 2], [f64; 3])> = Vec::new();
        for ui in 0..n {
            let u = u0 + (ui as f64 + 0.5) * (u1 - u0) / n as f64;
            for vi in 0..n {
                let v = v0 + (vi as f64 + 0.5) * (v1 - v0) / n as f64;
                if domain.contains(u, v) {
                    let p = surface.value(u, v);
                    let key = [
                        (p[hidden] - centre[hidden]).abs(),
                        (0..3).map(|i| (p[i] - centre[i]).powi(2)).sum(),
                    ];
                    found.push((key, p));
                }
            }
        }
        if !found.is_empty() {
            found.sort_by(|(ka, pa), (kb, pb)| {
                ka[0]
                    .total_cmp(&kb[0])
                    .then(ka[1].total_cmp(&kb[1]))
                    .then(pa[0].total_cmp(&pb[0]))
                    .then(pa[1].total_cmp(&pb[1]))
                    .then(pa[2].total_cmp(&pb[2]))
            });
            return found.into_iter().map(|(_, p)| p).collect();
        }
    }
    Vec::new()
}

/// Up to *n* of *sites* (ranked) after the first, each at least a tenth of the face's box
/// diagonal from those kept before it: other places a symbol may attach.
fn spread(sites: &[[f64; 3]], b: &Bounds, n: usize) -> Vec<[f64; 3]> {
    let gap = 0.1 * b.diagonal();
    let mut kept: Vec<[f64; 3]> = sites.iter().take(1).copied().collect();
    for s in sites.iter().skip(1) {
        if kept.len() > n {
            break;
        }
        let far = kept
            .iter()
            .all(|k| ((0..3).map(|i| (k[i] - s[i]).powi(2)).sum::<f64>()).sqrt() >= gap);
        if far {
            kept.push(*s);
        }
    }
    kept.split_off(1.min(kept.len()))
}

/// The box of *faces* on *part*.
fn bbox(part: &Part, faces: &[usize]) -> Option<[f64; 6]> {
    let mut out: Option<[f64; 6]> = None;
    for &f in faces {
        let b = part.face_bounds(f);
        if !(b.min.iter().chain(&b.max).all(|x| x.is_finite())) {
            continue;
        }
        let o = out.get_or_insert([b.min[0], b.min[1], b.min[2], b.max[0], b.max[1], b.max[2]]);
        for i in 0..3 {
            o[i] = o[i].min(b.min[i]);
            o[i + 3] = o[i + 3].max(b.max[i]);
        }
    }
    out
}

/// draftwright's `_dominant_from_bbox`: the axis of the largest extent, `?` when none is.
pub(crate) fn dominant(b: [f64; 6]) -> &'static str {
    let spans = [("X", b[3] - b[0]), ("Y", b[4] - b[1]), ("Z", b[5] - b[2])];
    // Python's max keeps the first of equal spans.
    let mut best = spans[0];
    for s in &spans[1..] {
        if s.1.abs() > best.1.abs() {
            best = *s;
        }
    }
    if best.1.abs() > 1e-6 { best.0 } else { "?" }
}

/// The label draftwright writes for a dimension (`pmi._dimension_label`), from the values in mm
/// (degrees) with draftwright's number formatting. draftwright prints a diameter, thickness or
/// linear distance stated in a conversion-based unit in that unit (`0.25 ±0.01 INCH`); the port
/// does not, as the sheet is in mm (#214). A deviation keeps its sign. A diameter is `⌀` and a
/// spherical one `S⌀` (ISO 129-1:2004 §7.2), as haecceity's shared notation states them (#258),
/// where draftwright writes `ø` for both.
fn dimension_label(r: &Record, d: &Dimension) -> String {
    let Some(value) = r.value else {
        return String::new();
    };
    let prefix = match (&d.kind, r.kind.as_str()) {
        (
            DimensionKind::Size {
                kind: SizeKind::SphericalDiameter,
                ..
            },
            _,
        ) => "S⌀",
        (_, "diameter") => "⌀",
        (_, "radius") => "R",
        _ => "",
    };
    // The sheet is in mm (#214): a dimension authored in another unit prints its exact conversion
    // at its qualified precision ([`qualified`]), which three places would round (0.3125 in is
    // 7.9375 mm), never its own unit.
    if let Some(nominal) = &d.nominal
        && matches!(&nominal.quantity, Quantity::Length(l) if l.unit != LengthUnit::Millimetre)
    {
        let mm = |v: &Value| fmt_magnitude(qualified(v), None);
        return match &d.tolerance {
            DimTolerance::Limits(b)
            | DimTolerance::Fit {
                limits: Some(b), ..
            } => format!("{prefix}{} - {prefix}{}", mm(b.lower()), mm(b.upper())),
            DimTolerance::Deviations(b) => format!(
                "{prefix}{}{}",
                mm(nominal),
                deviations(qualified(b.upper()), qualified(b.lower()), None)
            ),
            _ => format!("{prefix}{}", mm(nominal)),
        };
    }
    match (r.lower_bound, r.upper_bound, r.upper_tol, r.lower_tol) {
        (Some(lo), Some(hi), _, _) => format!(
            "{prefix}{} - {prefix}{}",
            fmt_magnitude(lo, None),
            fmt_magnitude(hi, None)
        ),
        (_, _, Some(up), Some(lo)) => {
            format!("{prefix}{}{}", fmt(value, None), deviations(up, lo, None))
        }
        _ => format!("{prefix}{}", fmt(value, None)),
    }
}

/// The deviation suffix (`_make_label`): ` ±t`, ` +u/0`, ` 0/-l`, ` +u/-l`, each sign as stated.
fn deviations(upper: f64, lower: f64, places: Option<usize>) -> String {
    let signed = |v: f64| {
        let sign = if v < 0.0 { "-" } else { "+" };
        format!("{sign}{}", fmt_magnitude(v.abs(), places))
    };
    if upper > 1e-9 && upper == -lower {
        format!(" ±{}", fmt_magnitude(upper, places))
    } else if lower.abs() <= 1e-9 {
        format!(" {}/0", signed(upper))
    } else if upper.abs() <= 1e-9 {
        format!(" 0/{}", signed(lower))
    } else {
        format!(" {}/{}", signed(upper), signed(lower))
    }
}

/// A nominal with at most three decimal places; or exactly *decimals*
/// places (no negative zero).
pub fn fmt(v: f64, decimals: Option<usize>) -> String {
    match decimals {
        Some(d) => {
            let s = format!("{v:.d$}");
            if s.trim_start_matches('-')
                .chars()
                .all(|c| c == '0' || c == '.')
            {
                format!("{:.d$}", 0.0)
            } else {
                s
            }
        }
        None => {
            if crate::nominal::comparison() {
                return crate::nominal::format(v);
            }

            let v = (v * 1e6).round() / 1e6;
            for places in 0..=3 {
                let text = format!("{v:.places$}");
                if places == 3
                    || text
                        .parse::<f64>()
                        .is_ok_and(|n| (n - v).abs() <= 1e-6 + 1e-12)
                {
                    if places == 3
                        && text
                            .parse::<f64>()
                            .is_ok_and(|n| (n - v).abs() > 1e-6 + 1e-12)
                    {
                        return if text.parse::<f64>() == Ok(0.) {
                            "0.000".into()
                        } else {
                            text
                        };
                    }
                    let text = if places == 0 {
                        text.as_str()
                    } else {
                        text.trim_end_matches('0').trim_end_matches('.')
                    };
                    return if text == "-0" {
                        "0".into()
                    } else {
                        text.into()
                    };
                }
            }
            // Invariant: The finite loop always returns at places == 3, independently of input.
            unreachable!()
        }
    }
}

/// draftwright's `_fmt_pmi_magnitude`: at most 13 significant digits without trailing zeros, or
/// exactly *decimals* places of the value's shortest decimal form, rounded half to even.
pub fn fmt_magnitude(v: f64, decimals: Option<usize>) -> String {
    match decimals {
        Some(d) => round_decimal(&format!("{v}"), d),
        None if v == 0.0 => format!("{v:.1}"),
        None => {
            let sci = format!("{v:.12e}");
            plain(&sci)
        }
    }
}

/// `%.3g`, as draftwright labels a tolerance (`f"{value:.3g}"`).
pub fn fmt_g3(v: f64) -> String {
    if v == 0.0 {
        return "0".into();
    }
    let sci = format!("{v:.2e}");
    // Invariant: The finite nonzero value is formatted locally with Rust scientific notation and an integer exponent.
    let (mantissa, exp) = sci.split_once('e').expect("exponent");
    // Invariant: The finite nonzero value is formatted locally with Rust scientific notation and an integer exponent.
    let exp: i32 = exp.parse().expect("exponent");
    if !(-4..3).contains(&exp) {
        let m = mantissa.trim_end_matches('0').trim_end_matches('.');
        let sign = if exp < 0 { '-' } else { '+' };
        return format!("{m}e{sign}{:02}", exp.abs());
    }
    let places = usize::try_from(2 - exp).unwrap_or(0);
    let s = format!("{v:.places$}");
    if s.contains('.') {
        s.trim_end_matches('0').trim_end_matches('.').into()
    } else {
        s
    }
}

/// A `d.ddde±x` string as a plain decimal without trailing zeros.
fn plain(sci: &str) -> String {
    // Invariant: The finite nonzero value is formatted locally with Rust scientific notation and an integer exponent.
    let (mantissa, exp) = sci.split_once('e').expect("exponent");
    // Invariant: The finite nonzero value is formatted locally with Rust scientific notation and an integer exponent.
    let exp: i64 = exp.parse().expect("exponent");
    let negative = mantissa.starts_with('-');
    let digits: String = mantissa.chars().filter(char::is_ascii_digit).collect();
    // The point sits after the first digit, moved by the exponent.
    let point = 1 + exp;
    let mut s = if point <= 0 {
        format!(
            "0.{}{digits}",
            "0".repeat(usize::try_from(-point).unwrap_or(0))
        )
    } else if usize::try_from(point).unwrap_or(0) >= digits.len() {
        format!(
            "{digits}{}",
            "0".repeat(usize::try_from(point).unwrap_or(0) - digits.len())
        )
    } else {
        let p = usize::try_from(point).unwrap_or(0);
        format!("{}.{}", &digits[..p], &digits[p..])
    };
    if s.contains('.') {
        s = s.trim_end_matches('0').trim_end_matches('.').to_string();
    }
    let s = s.trim_start_matches('0');
    let s = if s.is_empty() || s.starts_with('.') {
        format!("0{s}")
    } else {
        s.to_string()
    };
    if negative { format!("-{s}") } else { s }
}

/// A decimal string (as Rust prints an `f64`, no exponent) rounded half to even to *d* places.
fn round_decimal(text: &str, d: usize) -> String {
    let negative = text.starts_with('-');
    let body = text.trim_start_matches('-');
    let (int, frac) = body.split_once('.').unwrap_or((body, ""));
    let mut digits: Vec<u8> = int.bytes().chain(frac.bytes()).map(|b| b - b'0').collect();
    let keep = int.len() + d;
    if digits.len() < keep {
        digits.resize(keep, 0);
    }
    let rest = digits.split_off(keep);
    let up = match rest.first() {
        Some(&r) if r > 5 => true,
        Some(&5) => rest[1..].iter().any(|&x| x > 0) || digits.last().is_some_and(|x| x % 2 == 1),
        _ => false,
    };
    if up {
        let mut i = digits.len();
        loop {
            if i == 0 {
                digits.insert(0, 1);
                break;
            }
            i -= 1;
            if digits[i] == 9 {
                digits[i] = 0;
            } else {
                digits[i] += 1;
                break;
            }
        }
    }
    let n_int = digits.len() - d;
    let int: String = digits[..n_int]
        .iter()
        .map(|x| char::from(b'0' + x))
        .collect();
    let frac: String = digits[n_int..]
        .iter()
        .map(|x| char::from(b'0' + x))
        .collect();
    let sign = if negative && digits.iter().any(|&x| x > 0) {
        "-"
    } else {
        ""
    };
    if d == 0 {
        format!("{sign}{int}")
    } else {
        format!("{sign}{int}.{frac}")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn caller_surface_texture_uses_the_ap242_lowering_and_rejects_foreign_faces() {
        let bytes = crate::model::read_step_file(std::path::Path::new(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/fixtures/surface_texture/plate_face_ra0_8.step.gz"
        )))
        .unwrap();
        let part = haecceity::read_step(&bytes).unwrap();
        let defs = haecceity::step::read_part_definitions(&bytes).unwrap();
        let doc = Document::parse(bytes).unwrap();
        let read = pmi::read(&doc, &defs).unwrap();
        let mut model = read.parts[0].clone();
        let imported = report(&doc, &defs, &part, &read).unwrap();
        let caller = from_model(&part, &model).unwrap();
        let signature = |report: &Report| {
            report
                .records
                .iter()
                .filter(|r| r.kind == "surface_finish")
                .map(|r| {
                    (
                        r.kind.clone(),
                        r.label.clone(),
                        r.faces.clone(),
                        r.fields.clone(),
                        r.geometry.clone(),
                    )
                })
                .collect::<Vec<_>>()
        };
        assert!(!signature(&imported).is_empty());
        assert_eq!(signature(&caller), signature(&imported));
        let feature = pmi::FeatureId(model.features.len());
        model
            .features
            .push(pmi::Feature::Items(vec![pmi::Anchor::Face(
                pmi::FaceIndex(part.faces.len()),
            )]));
        model.surface_textures[0].on = Some(feature);
        assert!(
            from_model(&part, &model)
                .unwrap_err()
                .contains("caller anchor")
        );
    }

    #[test]
    fn numbers_format_as_draftwright_formats_them() {
        assert_eq!(fmt(35.0, None), "35");
        // Default nominals preserve thousandths; tolerances retain their own policy.
        assert_eq!(fmt(3.175, None), "3.175");
        assert_eq!(fmt(0.25, Some(3)), "0.250");
        assert_eq!(fmt(-0.0001, Some(2)), "0.00");
        assert_eq!(fmt_magnitude(0.508, None), "0.508");
        assert_eq!(fmt_magnitude(0.30000000000000004, None), "0.3");
        assert_eq!(fmt_magnitude(100.0, None), "100");
        assert_eq!(fmt_magnitude(0.0254, None), "0.0254");
        assert_eq!(fmt_magnitude(0.02, Some(2)), "0.02");
        assert_eq!(fmt_magnitude(0.125, Some(2)), "0.12");
        assert_eq!(fmt_magnitude(0.135, Some(2)), "0.14");
        assert_eq!(fmt_magnitude(9.995, Some(2)), "10.00");
        assert_eq!(fmt_g3(0.381), "0.381");
        assert_eq!(fmt_g3(1.016), "1.02");
        assert_eq!(fmt_g3(0.38099999999999995), "0.381");
        assert_eq!(fmt_g3(0.75), "0.75");
        assert_eq!(fmt_g3(100.0), "100");
        assert_eq!(fmt_g3(1234.0), "1.23e+03");
        assert_eq!(fmt_g3(0.00001), "1e-05");
    }

    #[test]
    fn converted_lengths_drop_the_writers_float_noise() {
        let inch = |text: &str| {
            measure(&Value::length(
                pmi::Decimal::parse(text).unwrap(),
                LengthUnit::Inch,
            ))
        };
        // nist_stc_08 states its frames' magnitudes with noise: 0.01500000000006 in (#214).
        assert_eq!(fmt_magnitude(inch("0.01500000000006"), None), "0.381");
        assert_eq!(fmt_magnitude(inch("0.06000000000024"), None), "1.524");
        assert_eq!(fmt_magnitude(inch("0.016"), None), "0.4064");
        assert_eq!(fmt_magnitude(inch("0.0001"), None), "0.00254");
        assert_eq!(fmt_magnitude(inch("12.00"), None), "304.8");
        let mm = Value::length(pmi::Decimal::parse("0.3").unwrap(), LengthUnit::Millimetre);
        assert_eq!(measure(&mm), 0.3);
        // A metre's six decimals are a µm: its places go to nine, keeping 2.5 µm.
        let metre = |text: &str| {
            measure(&Value::length(
                pmi::Decimal::parse(text).unwrap(),
                LengthUnit::Metre,
            ))
        };
        assert_eq!(fmt_magnitude(metre("0.0000025"), None), "0.0025");
        assert_eq!(fmt_magnitude(metre("2.5E-6"), None), "0.0025");
    }

    #[test]
    fn deviations_keep_their_signs() {
        assert_eq!(deviations(0.5, -0.5, None), " ±0.5");
        assert_eq!(deviations(0.0, -0.2, None), " 0/-0.2");
        assert_eq!(deviations(0.2, 0.0, None), " +0.2/0");
        assert_eq!(deviations(0.05, -0.1, None), " +0.05/-0.1");
        // A g6 shaft: both below nominal, which draftwright's unsigned label cannot say.
        assert_eq!(deviations(-0.009, -0.025, None), " -0.009/-0.025");
    }

    #[test]
    fn entities_fall_in_draftwright_categories() {
        assert_eq!(category_of_entity("dimensional_size"), Category::Dimension);
        assert_eq!(
            category_of_entity(
                "geometric_tolerance+geometric_tolerance_with_datum_reference+position_tolerance"
            ),
            Category::GeometricTolerance
        );
        assert_eq!(category_of_entity("datum_feature"), Category::Datum);
        assert_eq!(category_of_entity("shape_aspect"), Category::Other);
    }
}
