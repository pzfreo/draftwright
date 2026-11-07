//! The document model ([ADR R1](../docs/adr/R1-document-model.md), [docs/model.md]):
//! - **Analysis**: what the part is;
//! - **Spec**: what the author decided, keyed by stable IDs;
//! - **Plan**: what the sheet will say, every item with its ID, default flag and basis.
//!
//! The operations over them are [`analyse`], [`plan`], [`edit`] and [`render`]: pure, stateless
//! and deterministic.
//!
//! This first cut covers the views (which views, the sheet, the scale and the placement) and a
//! title block. Features, dimensions and annotations join the same documents as they are ported.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::Path;

use haecceity::Part;
use haecceity::classify::{Classifier, State};
use haecceity::hlr::{self, Exact, Plane, Projected, Unresolved, View, project, project_section};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use crate::bridge::{Feature, Recognition};
use crate::dims::{Omitted, PlannedDimension};
use crate::items::State as ItemState;
use crate::layout::{self, Corner, FRAME, Fallback, Principals, Request, margin};
use crate::notes::{NoteSpec, NotesBlock, PlannedNote};
use crate::output::{
    Annotation, Claim, Curve, Drawing, Drawn, DrawnField, DrawnView, HAlign, Item, Kind, Layer,
    Sheet, TextRun, VAlign, items_bbox, layer_styles, projected,
};
use crate::tables::{PlannedTable, TableSpec};
use crate::text::{Font, Laid, Segment};
use crate::title_block::{self, TitleCell};

/// Version 2 added the isometric box (`iso_bounds`) and the recognised features, version 3 the
/// horizontal face levels (`levels`), version 4 their supports and the step risers
/// (`level_supports`, `risers`), version 5 the families the recogniser did not give
/// (`unrecognised`), version 6 the principal boundary faces' loops lint's profile check reads
/// (`principal_profiles`), version 7 the part's AP242 PMI (`pmi`).
pub const ANALYSIS_SCHEMA: &str = "draftwright.analysis/7";
/// The analyses [`plan`] reads: this version, and versions 2 to 6 (it reads them with none of
/// what they lack).
pub const ANALYSIS_SCHEMAS: [&str; 6] = [
    "draftwright.analysis/2",
    "draftwright.analysis/3",
    "draftwright.analysis/4",
    "draftwright.analysis/5",
    "draftwright.analysis/6",
    ANALYSIS_SCHEMA,
];
/// Version 2 names a dimension by what it measures, `d:<feature>/<parameter>` (version 1 added
/// the view that carries it, `@<view>`), and a PMI item by its record's semantic identity
/// (version 1 by its Part 21 instance, `pmi:<category>:#<n>`); the carrying view is the entry's
/// `view`. A version 1 spec is read through [`migrate`].
pub const SPEC_SCHEMA: &str = "draftwright.spec/2";
/// The specs [`plan`] reads: this version, and version 1 through [`migrate`].
pub const SPEC_SCHEMAS: [&str; 2] = ["draftwright.spec/1", SPEC_SCHEMA];
pub const PLAN_SCHEMA: &str = "draftwright.plan/1";

/// Which file, and which reading of it, a document was made from.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Binding {
    pub source_sha256: String,
    pub part: String,
    pub face_count: usize,
    pub loader: String,
    /// The part of an assembly read alone, by its index in the file's distinct parts
    /// (haecceity's order, as `draftwright parts` lists them): `part` is then that part's name,
    /// and the Analysis is in the part's own frame. Absent when the whole file was read.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub part_index: Option<usize>,
}

/// One distinct part of a STEP file, as `draftwright parts` lists it (specify-core's
/// `{part, name, faces, instances}`, with the product's id): what a caller chooses a part of an
/// assembly by. The index is the identity; the name and product id are reported beside it.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FilePart {
    pub part: usize,
    pub name: String,
    pub product_id: String,
    pub faces: usize,
    /// How many times the part is placed.
    pub instances: usize,
}

/// The group quiddity accounts a face to (`face_accounting` in its recognition document).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FaceGroup {
    /// A recognised feature's face, evidence records included.
    Feature,
    /// Part body: stock walls, plate edges and the like.
    Body,
    /// Named by a refused projection only.
    Refused,
    Unexplained,
}

fn single_part_keys(keys: &BTreeMap<String, usize>) -> bool {
    keys.len() <= 1
}

/// What the part is: regenerable from its STEP file, never edited.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Analysis {
    pub schema: String,
    pub binding: Binding,
    /// STEP product name for the single part or selected assembly member. Older analyses
    /// omit it and use the binding's file stem for the default drawing title.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub product_name: Option<String>,
    /// File-stated bill-of-materials data, independent of geometric PMI.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bom: Option<crate::bom::Metadata>,
    /// Source file stem when an assembly selection makes `binding.part` the product name.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_stem: Option<String>,
    /// The part's box in its own frame, mm.
    pub envelope: [[f64; 3]; 2],
    /// Absent in older analyses; several solids make completeness findings informational.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub solid_count: Option<usize>,
    /// With several solids, the faces of the one solid that fills the part's box, when exactly
    /// one does: support-plane chains are walked on it alone (#220).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub body_faces: Option<Vec<usize>>,
    /// Principal outer stock faces, credited only when the overall sizes are stated and
    /// [`Analysis::face_accounting`] is absent.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub stock_faces: Vec<usize>,
    /// quiddity's account of every face, by file face index (#207); absent for analyses whose
    /// recognition carries none.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub face_accounting: Option<Vec<FaceGroup>>,
    /// Where the features came from; absent when none were recognised.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub recognition: Option<Recognition>,
    #[serde(default)]
    pub features: Vec<Feature>,
    /// Exact trimmed-surface attachment points for recognised blends, in the part frame.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub blend_sites: BTreeMap<String, Vec<[f64; 3]>>,
    /// Cylindrical radius profiles and their material side, from the trimmed faces.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub radius_arcs: BTreeMap<String, Vec<crate::radius::RadiusArc>>,
    /// Exact circular boundary spans of partial radius features.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub profile_arcs: BTreeMap<String, Vec<crate::arc::ProfileArc>>,
    /// Small, grouped shallow relief regions whose profiles are governed by the model.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub radius_reliefs: Vec<crate::blends::Relief>,
    /// The isometric's projected box (visible and hidden edges), `[x0, y0, x1, y1]` mm at 1:1,
    /// about the projection of the envelope's centre: what the isometric is fitted by. All zero
    /// when the projection has no edges (the plan then reports `iso_projection_empty`).
    pub iso_bounds: [f64; 4],
    /// The part's edges and faces, compactly, from which the planner proves a region of a view
    /// blank before placing a label inside it ([`projected_ink`]). Absent in older analyses,
    /// which then keep every label outside the views.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub edge_ink: Option<projected_ink::EdgeInk>,
    /// Claimed thread edge/curved-face indices in the immutable raw projection evidence.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub modelled_thread_ink: BTreeMap<String, crate::modelled_threads::Evidence>,
    /// The heights (z, mm) of the part's horizontal face levels: what a prismatic part's height
    /// ladder dimensions ([`crate::demand::face_levels`]).
    #[serde(default)]
    pub levels: Vec<f64>,
    /// Each horizontal face level's support (quiddity's `FaceLevel` records, read in the part's
    /// frame): its height and the x and y extents of its faces, per solid.
    #[serde(default)]
    pub level_supports: Vec<LevelSupport>,
    /// The walls and ramps that rise between the levels (quiddity's `RiserEvidence`, read in the
    /// part's frame): where a prismatic part's steps are located along x and y.
    #[serde(default)]
    pub risers: Vec<Riser>,
    /// Each solid's principal boundary faces (each solid of a part of several, else the whole
    /// part), with their loops' edges: what lint's principal profile check judges
    /// ([`crate::lint::profiles`]), read from the part as draftwright's lint reads it.
    #[serde(default)]
    pub principal_profiles: Vec<crate::lint::profiles::Source>,
    /// Provider-issued ordered planar outer profiles, with exact body and support identities.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub outer_profiles: Vec<crate::lint::profile_angles::Source>,
    /// The families of features the recogniser did not give, so the part may have features of
    /// them that the Analysis lacks: empty for Python quiddity's recognition, which the bridge
    /// reads in full; for Rust quiddity's ([`analyse_native`]), the families its document has
    /// that [`crate::bridge::native`] does not read yet, and those it does not carry. The plan
    /// reports them (`features_not_recognised`).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub unrecognised: Vec<Unrecognised>,
    /// Recognisable shapes among the faces quiddity's accounting leaves unexplained (#213):
    /// protrusions, cuts, edge chamfers and tooth patterns ([`crate::unexplained`]). The plan
    /// warns of each whose faces no feature claims (`shape_not_recognised`).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub unexplained_shapes: Vec<crate::unexplained::Shape>,
    /// The part's AP242 semantic PMI ([`crate::pmi::attach`]): its records, each with where it
    /// attaches, and every source not read; or why the file's PMI could not be read. Absent
    /// when the file has none. The plan lowers it to feature control frames and datum feature
    /// symbols ([`crate::pmi_lowering`]).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pmi: Option<crate::pmi::Reading>,
    /// Each face's attachment, by face index ([`crate::pmi::face_attachments`]): where a datum
    /// or geometric tolerance the Spec authors on it attaches, as an AP242 record on the same
    /// faces would ([`crate::spec_gdt`]). Added to version 7 as an optional field; an Analysis
    /// without it refuses Spec-authored GD&T (analyse again).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub face_attachments: Vec<crate::pmi::FaceAttachment>,
    /// The file's distinct parts, when it holds more than one (an assembly): absent for a file
    /// of one part. With no part chosen (`binding.part_index` absent) they are drawn together
    /// as one, and the plan warns (`assembly_drawn_whole`).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub parts: Vec<FilePart>,
    /// Persistent identities when the source has multiple parts. Single-part documents keep
    /// the existing schema shape; their product name identifies their sole part.
    #[serde(default, skip_serializing_if = "single_part_keys")]
    pub part_keys: BTreeMap<String, usize>,
    /// The file's bodies that coincide with an earlier one (#212), when no part was chosen:
    /// when every body of an assembly coincides with the first, that part is read alone
    /// (`binding.part_index`) and these are not drawn; otherwise the file is drawn whole, these
    /// with it. The plan reports them (`coincident_bodies`).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub coincident: Vec<CoincidentBody>,
    /// Whether the part `binding.part_index` names was chosen as the assembly's principal part
    /// (#219), with no part chosen: its body the largest, holding every other within its box,
    /// drawn alone. The other parts are not drawn, and the plan says so
    /// (`assembly_principal_drawn`).
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub principal: bool,
    /// The rotation that set the part, a fraction of a degree to [`SQUARE_WITHIN`] off the
    /// file's axes, square to them in its own frame (#216): the part is read, drawn and
    /// dimensioned so moved, and the plan says so (`part_squared_to_axes`).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub squared: Option<Squared>,
}

/// A part's own frame (quiddity's `infer_part_frame`) within this many degrees of the file's
/// axes is taken as the axes it was meant to lie on (#216): a library or exported model a
/// fraction of a degree off them is drawn square, not as a tilted box.
pub const SQUARE_WITHIN: f64 = 2.0;

/// A part's own frame this many degrees or less off the file's axes is on them: round-off,
/// not a turn worth drawing square (`part_squared_to_axes` prints the angle to 0.01°).
const SQUARE_BEYOND: f64 = 0.01;

/// How a part slightly off the file's axes was set square to them ([`Analysis::squared`]).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Squared {
    /// The largest angle, degrees, between one of the part's own axes and the file axis it was
    /// set along.
    pub angle: f64,
    /// The rigid motion applied to the part as read (`haecceity::Part::transformed`): a
    /// rotation about the part's centre of mass.
    pub placement: haecceity::step::Placement,
}

impl Squared {
    /// The rotation about its centre of mass that sets *part*'s own frame along the file axes it
    /// is nearest, when it is off them by more than [`SQUARE_BEYOND`] and at most
    /// [`SQUARE_WITHIN`].
    fn of(part: &Part) -> Option<Squared> {
        // The frame is chosen from the plane normals and cylinder axes: with each of those on a
        // file axis, so is the frame, and it is not worked out (most parts, at no cost).
        let on_axis = SQUARE_BEYOND.to_radians().cos();
        if part.faces.iter().all(|face| match &face.surface {
            haecceity::geom::Surface::Plane { frame }
            | haecceity::geom::Surface::Cylinder { frame, .. } => {
                let z = frame.z;
                let norm = (z[0] * z[0] + z[1] * z[1] + z[2] * z[2]).sqrt();
                z.iter().any(|c| c.abs() >= on_axis * norm)
            }
            _ => true,
        }) {
            return None;
        }
        let frame = quiddity::frames::infer_part_frame(part).ok()?;
        let mut rows = [[0.0; 3]; 3];
        let mut taken = [false; 3];
        let mut cos = 1.0_f64;
        for axis in [frame.x, frame.y, frame.z] {
            let k = (0..3).max_by(|&a, &b| axis[a].abs().total_cmp(&axis[b].abs()))?;
            if taken[k] {
                return None;
            }
            taken[k] = true;
            // Row k is the axis, signed to the file axis it is nearest: it rotates onto that.
            rows[k] = axis.map(|c| c * axis[k].signum());
            cos = cos.min(axis[k].abs());
        }
        // Each row signed to its nearest axis, so within 2° the rows are a rotation, not a
        // reflection.
        let angle = cos.min(1.0).acos().to_degrees();
        if angle <= SQUARE_BEYOND || angle > SQUARE_WITHIN {
            return None;
        }
        let o = frame.origin;
        // About the centre of mass, which stays where it was.
        let placement = std::array::from_fn(|i| {
            let r = rows[i];
            [
                r[0],
                r[1],
                r[2],
                o[i] - (r[0] * o[0] + r[1] * o[1] + r[2] * o[2]),
            ]
        });
        Some(Squared { angle, placement })
    }
}

/// *part*, read from the file *analysis* was made from, as *analysis* drew it: set square to
/// the file's axes when it was ([`Analysis::squared`]).
pub fn as_analysed(part: Part, analysis: &Analysis) -> Result<Part, haecceity::step::StepError> {
    match &analysis.squared {
        Some(s) => part.transformed(&s.placement),
        None => Ok(part),
    }
}

/// A placed body of a STEP file that coincides with an earlier one (`coincident_bodies`).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CoincidentBody {
    /// Its part's index in `parts`, the part's name, and which of its placements it is (from 0,
    /// as the parts are numbered).
    pub part: usize,
    pub name: String,
    pub placement: usize,
    /// The part of the earlier body it coincides with.
    pub of: usize,
}

/// A family of features the recogniser did not give the Analysis (quiddity's family name).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Unrecognised {
    pub family: String,
    /// How many records of it the recogniser's document has on this part; absent when the
    /// document does not carry the family at all, so whether the part has any is not known.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub records: Option<usize>,
}

/// A horizontal face level's support: its height and the extents of its faces, mm.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LevelSupport {
    pub z: f64,
    pub x_span: [f64; 2],
    pub y_span: [f64; 2],
}

/// A step riser as quiddity records it (`RiserEvidence`): a wall (`vertical`) or ramp facing
/// along `axis`, at `positions` along it (and, for a bounded ramp, `other_positions` along
/// `other_axis`), rising from `z_lo` to `z_hi`; whether each end lies on the part's top or bottom;
/// the tolerance it was read with; and the heights of its solid's levels.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Riser {
    pub vertical: bool,
    pub axis: String,
    pub positions: Vec<f64>,
    pub other_axis: String,
    pub other_positions: Vec<f64>,
    pub z_lo: f64,
    pub z_hi: f64,
    pub lo_at_envelope: bool,
    pub hi_at_envelope: bool,
    pub tol: f64,
    pub body_levels: Vec<f64>,
}

/// What the author decided. Anything absent takes its default.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields, default)]
pub struct Spec {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub process: Option<crate::process::Process>,
    pub schema: String,
    /// Part identity from `draftwright parts`: a persistent key or its numeric index.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub part: Option<PartChoice>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub binding: Option<Binding>,
    #[serde(deserialize_with = "sheet_document")]
    pub sheet: SheetSpec,
    #[serde(skip_serializing_if = "crate::characteristics::Settings::is_default")]
    pub balloons: crate::characteristics::Settings,
    /// Authored isometric scale relative to the sheet scale; never automatically refitted.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub iso_scale_factor: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sheets: Option<crate::sheets::SheetsSpec>,
    /// Per standard view (`front`, `plan`, `side`, `iso`).
    #[serde(skip_serializing_if = "crate::sections::Views::is_empty")]
    pub views: crate::sections::Views,
    /// Where the dimensions come from: `auto` (the defaults, less what `dimensions` omits) or
    /// `authored` (exactly the dimensions listed, a complete set: ADR4-5).
    #[serde(skip_serializing_if = "DimensionSource::is_auto")]
    pub dimension_source: DimensionSource,
    /// Whether semantic PMI is exclusive, preferred (default), or withheld.
    #[serde(skip_serializing_if = "PmiMode::is_first")]
    pub pmi: PmiMode,
    /// Location origins by part axis: min, max, or a planar face/feature reference.
    #[serde(skip_serializing_if = "BTreeMap::is_empty")]
    pub origin: BTreeMap<String, String>,
    /// Authored location networks, by stable author name. Empty preserves automatic planning.
    #[serde(skip_serializing_if = "BTreeMap::is_empty")]
    pub dimension_schemes: BTreeMap<String, crate::dimension_scheme::Scheme>,
    /// Plan-time hole presentation; rescue tables also obey `never`.
    #[serde(skip_serializing_if = "crate::hole_table::Setting::is_auto")]
    pub hole_table: crate::hole_table::Setting,
    /// Per dimension, by its ID (`d:<feature>/<parameter>`).
    #[serde(skip_serializing_if = "BTreeMap::is_empty")]
    pub dimensions: BTreeMap<String, DimensionSpec>,
    /// A band on all overall extents; an extent's own tolerance or fit takes precedence.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub envelope_tolerance: Option<String>,
    /// Decimal places (0–15) by measurement ID, including compound callout roles.
    #[serde(
        default,
        skip_serializing_if = "BTreeMap::is_empty",
        deserialize_with = "crate::dims::precision::read"
    )]
    pub precision: BTreeMap<String, u8>,
    /// Placement and omission of semantic PMI frames, datums, labels and part notes, by stable ID.
    #[serde(skip_serializing_if = "BTreeMap::is_empty")]
    pub annotations: BTreeMap<String, crate::placement_intent::AnnotationSpec>,
    /// Per feature, by its ID (`f:<kind>:<n>`).
    #[serde(skip_serializing_if = "BTreeMap::is_empty")]
    pub features: BTreeMap<String, FeatureSpec>,
    /// Features authored from stable face fingerprints; sizes are read from Analysis evidence.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub feature_declarations: Vec<crate::recognition_corrections::Declaration>,
    /// Datums and feature control frames on faces, by the side of the part's box the face is
    /// on (`bottom`, `top`, `left`, `right`, `front`, `back`) or its index (`face:<n>`)
    /// ([`crate::spec_gdt`]).
    #[serde(skip_serializing_if = "BTreeMap::is_empty")]
    pub faces: BTreeMap<String, crate::spec_gdt::FaceSpec>,
    /// General notes, in the order they are numbered ([`crate::notes`]).
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub notes: Vec<NoteSpec>,
    /// Corner-block tables (revision blocks, BOMs, schedules), placed in order
    /// ([`crate::tables`]).
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub tables: Vec<TableSpec>,
    /// Authored external spur gear requirements, never inferred from geometry.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub gears: Vec<crate::gears::GearSpec>,
}

pub use crate::dims::recesses::DepthPresentation;
pub use crate::part_choice::PartChoice;

/// How the part's semantic PMI participates in a drawing.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum PmiMode {
    Strict,
    #[default]
    First,
    Ignore,
}
impl PmiMode {
    fn is_first(&self) -> bool {
        *self == Self::First
    }
}

/// The two sources of dimension intent (ADR4-5).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum DimensionSource {
    /// The defaults, less what the spec omits.
    #[default]
    Auto,
    /// Exactly the dimensions the spec lists.
    Authored,
}

impl DimensionSource {
    fn is_auto(&self) -> bool {
        *self == DimensionSource::Auto
    }
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields, default)]
pub struct DimensionSpec {
    /// Alternative measured depth term; values always come from the model geometry.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub presentation: Option<DepthPresentation>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub text: Option<crate::label_text::Text>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub style: Option<crate::label_text::Style>,
    /// Authored quantity; absent means an edit of an automatic dimension.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub kind: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub between: Option<Vec<String>>,
    /// x, y, z, or true (the default for an authored linear dimension).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub along: Option<String>,
    /// Leave it off the sheet (in `auto` only; an authored set leaves out by not listing).
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub omit: bool,
    /// Its tolerance, printed after its value: `±T` or `+U -L` ([`crate::dims`]'s tolerance
    /// rules; ARCH0011-1).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tolerance: Option<String>,
    /// ISO 286 class on a linear size or diameter; `sheet/fit_deviations` requests deviations.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fit: Option<String>,
    /// The view that carries it (`front`, `plan` or `side`), where the default would put it
    /// elsewhere: no longer part of its ID (`draftwright.spec/2`). One that cannot carry it is
    /// refused; one that is omitted leaves it in its default view, with a warning.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub view: Option<String>,
    /// Position relative to its feature and carrying view; never page coordinates.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub place: Option<crate::placement_intent::Place>,
}

pub use crate::springs::Depiction as SpringDepiction;

/// What the author decided of a feature.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields, default)]
pub struct FeatureSpec {
    /// View along either leg for gusset thickness, spacing/pitch and location.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub gusset_view: Option<String>,
    /// Intent for a recognised physical thread; no geometry is authored here.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub modelled_thread: Option<crate::modelled_threads::Settings>,
    /// Recognised spring depiction; full retains its measured data and dimensions.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub spring: Option<SpringDepiction>,
    /// Shared thread and knurl callout presentation.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub callouts: Option<crate::callouts::Settings>,
    /// Stable defining-face fingerprints, filled when a correction is saved.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub anchor: Option<crate::recognition_corrections::Anchor>,
    /// Author's geometric interpretation; uses the ordinary feature planners.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reclassify: Option<crate::recognition_corrections::Kind>,
    /// Remove a spurious recognition, including its feature requirements.
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub suppress: bool,
    /// Dissolve a recognised hole or slot pattern, retaining its members; on a slot, dimension it
    /// on its own rather than in its congruent slots' `N× SLOT W × L` callout.
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub split: bool,
    /// Group existing holes under this stable, author-chosen pattern ID.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pattern: Option<crate::recognition_corrections::PatternSpec>,
    /// An ISO 286 fit class on a hole's bore or a cylindrical boss's diameter (`H7`, `g6`),
    /// printed after the diameter of the callout that states it.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fit: Option<String>,
    /// A datum letter on the feature (ISO 5459's symbol, draftwright's `Sheet.datum`): on its
    /// cylinders' common axis when it has cylinders, else on its faces ([`crate::spec_gdt`]).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub datum: Option<String>,
    /// Feature control frames on the feature (ISO 1101, draftwright's `Sheet.control`), drawn
    /// as an AP242 tolerance on the same faces is ([`crate::spec_gdt`]).
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub controls: Vec<crate::spec_gdt::ControlSpec>,
    /// An ISO 1302 surface texture on the feature (draftwright's `finish`), on its cylinders when
    /// it has cylinders, else on its faces ([`crate::spec_notes`]).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub finish: Option<crate::spec_notes::FinishSpec>,
    /// An ISO metric thread on a hole, hole pattern or turned step (draftwright's `thread`),
    /// printed after its callout's size ([`crate::spec_notes`]).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub thread: Option<crate::spec_notes::ThreadSpec>,
    /// A knurl on a turned step (draftwright's `knurl`), printed after its diameter
    /// ([`crate::spec_notes`]).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub knurl: Option<crate::spec_notes::KnurlSpec>,
    /// A note on a leader to the feature (draftwright's `note`) ([`crate::spec_notes`]).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
    /// Roles this placed feature note explicitly satisfies; never inferred from its prose.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub note_satisfies: Vec<String>,
}

impl Default for Spec {
    fn default() -> Self {
        Spec {
            process: None,
            schema: SPEC_SCHEMA.into(),
            part: None,
            binding: None,
            sheet: SheetSpec::default(),
            balloons: Default::default(),
            iso_scale_factor: None,
            sheets: None,
            views: Default::default(),
            dimension_source: DimensionSource::Auto,
            pmi: PmiMode::First,
            origin: BTreeMap::new(),
            dimension_schemes: BTreeMap::new(),
            hole_table: crate::hole_table::Setting::Auto,
            dimensions: BTreeMap::new(),
            envelope_tolerance: None,
            precision: BTreeMap::new(),
            features: BTreeMap::new(),
            feature_declarations: Vec::new(),
            faces: BTreeMap::new(),
            annotations: BTreeMap::new(),
            notes: Vec::new(),
            tables: Vec::new(),
            gears: Vec::new(),
        }
    }
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields, default)]
pub struct SheetSpec {
    /// Inspection characteristic rings; off unless explicitly set to all.
    #[serde(skip_serializing_if = "crate::characteristics::Mode::is_off")]
    pub balloons: crate::characteristics::Mode,
    /// Keep explicit paper, scale and count without automatic selection or refitting.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub keep_layout: Option<bool>,
    /// Declared datums no tolerance frame cites. Unset (the default), those no dimension measures
    /// from either are not drawn, each reported as `datum_not_drawn` (info); true draws every
    /// declared datum; false hides every uncited one as the user's omission.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub uncited_datums: Option<bool>,
    /// ISO 5457 grid zones and centring marks on every sheet (default true). False omits
    /// both, unless a section cross-reference cites a zone ([`crate::sheets::furnish`]).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub zones: Option<bool>,
    /// Side-by-side blocks for a table, by its ID (including declared, radius and hole tables),
    /// from 1 to 4: 1 never splits it; unset splits a table taller than the drawable height
    /// into the fewest blocks that fit it ([`crate::hole_table::split`]).
    #[serde(skip_serializing_if = "BTreeMap::is_empty")]
    pub table_columns: BTreeMap<String, u32>,
    /// Draw automatically determined dimensions in parentheses; omitted by default.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub references: Option<bool>,
    /// Shared thread and knurl callout presentation.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub callouts: Option<crate::callouts::Settings>,
    /// Line-work intent; absent keeps the view's automatic convention.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub hidden: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub centre_marks: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub centre_lines: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub thread_depiction: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub knurl_depiction: Option<bool>,

    /// Preferred maximum blank paper between view annotation bands, mm (default 25).
    /// Spent only after sheet and scale selection; zero disables automatic gutters.
    #[serde(skip_serializing_if = "Option::is_none")]
    #[serde(deserialize_with = "view_gutter")]
    pub view_gutter: Option<f64>,
    /// `A4` … `A0`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub size: Option<String>,
    /// Preferred automatic paper, `A4` … `A0`; defaults to `A3`.
    #[serde(
        skip_serializing_if = "Option::is_none",
        deserialize_with = "preferred_max_size"
    )]
    pub preferred_max_size: Option<String>,
    /// Preferred sheet count, at least 1; defaults to 2. `sheets.max` stays hard.
    #[serde(
        skip_serializing_if = "Option::is_none",
        deserialize_with = "preferred_max_sheets"
    )]
    pub preferred_max_sheets: Option<u32>,
    /// `n:m`, from ISO 5455's series.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub scale: Option<String>,
    /// Dimension-line spacing: unset uses normal 10/7 mm lanes and falls back to compact
    /// 7/5.5 mm on the same sheet before a smaller scale or larger paper; `normal` forbids
    /// that fallback and `compact` uses compact lanes directly.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub dimension_spacing: Option<DimensionSpacing>,
    /// Clear space every view and annotation keeps inside the frame, mm, from 0 to 20: unset is
    /// 3; spare paper widens it further. 0 lets the drawing run to the frame. The title block,
    /// zones, centring marks and frame-corner tables stay on the frame.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub frame_gutter: Option<f64>,
    #[serde(skip_serializing_if = "BTreeMap::is_empty")]
    pub title: BTreeMap<String, String>,
    /// Legacy spelling of `fit_deviations`; explicit values remain supported.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fit_limits: Option<bool>,
    /// Show stacked ISO 286 deviations after the class; off by default.
    /// Takes precedence over the legacy `fit_limits` setting.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fit_deviations: Option<bool>,
    /// ISO 5456-2's projection method, `first` or `third` (angle): where the plan and side sit
    /// about the front, and the symbol beside the title block. Third angle unless set. Any
    /// other value is refused when the spec is read, before the part is (ISSUE-1514-a).
    #[serde(
        skip_serializing_if = "Option::is_none",
        deserialize_with = "projection_method"
    )]
    pub projection: Option<String>,
    /// The part's default surface texture (draftwright's `default_surface_finish`), its ISO 1302
    /// symbol beside the title block, as an AP242 one on the part is drawn ([`crate::spec_notes`]).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub default_finish: Option<crate::spec_notes::FinishSpec>,
}

/// Sheet-wide dimension-line spacing ([`SheetSpec::dimension_spacing`]).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum DimensionSpacing {
    /// First line 10 mm from the outline, then 7 mm between lines.
    #[default]
    Normal,
    /// First line 7 mm from the outline, then 5.5 mm between lines.
    Compact,
}

impl DimensionSpacing {
    pub fn is_normal(&self) -> bool {
        *self == Self::Normal
    }
}

fn view_gutter<'de, D: serde::Deserializer<'de>>(d: D) -> Result<Option<f64>, D::Error> {
    let gutter = Option::<f64>::deserialize(d)?;
    if gutter.is_some_and(|g| !valid_view_gutter(g)) {
        return Err(serde::de::Error::custom(
            "sheet/view_gutter must be from 0 to 1189 paper mm",
        ));
    }
    Ok(gutter)
}

fn valid_view_gutter(g: f64) -> bool {
    // No gap can exceed the largest supported sheet (A0). Huge finite gaps
    // otherwise overflow downstream layout/ink allocation before they can shrink.
    (0.0..=1189.0).contains(&g)
}

pub(crate) const REMOVED_PRINTER: &str = "unknown field `preferred_printer` in sheet; use sheet/preferred_max_size instead: A4 printer → preferred_max_size: \"A4\", A3 printer → preferred_max_size: \"A3\"";

fn sheet_document<'de, D: serde::Deserializer<'de>>(d: D) -> Result<SheetSpec, D::Error> {
    let value = serde_json::Value::deserialize(d)?;
    if value.get("preferred_printer").is_some() {
        return Err(serde::de::Error::custom(REMOVED_PRINTER));
    }
    serde_json::from_value(value).map_err(serde::de::Error::custom)
}

fn preferred_max_sheets<'de, D: serde::Deserializer<'de>>(d: D) -> Result<Option<u32>, D::Error> {
    let count = Option::<u32>::deserialize(d)?;
    if count == Some(0) {
        return Err(serde::de::Error::custom(
            "sheet/preferred_max_sheets must be at least 1",
        ));
    }
    Ok(count)
}

fn preferred_max_size<'de, D: serde::Deserializer<'de>>(d: D) -> Result<Option<String>, D::Error> {
    let value = Option::<String>::deserialize(d)?;
    if value
        .as_deref()
        .is_some_and(|v| !layout::PAGES.iter().any(|p| p.0 == v))
    {
        return Err(serde::de::Error::custom(
            "sheet/preferred_max_size must be A4, A3, A2, A1 or A0",
        ));
    }
    Ok(value)
}

fn projection_method<'de, D: serde::Deserializer<'de>>(d: D) -> Result<Option<String>, D::Error> {
    let p = Option::<String>::deserialize(d)?;
    match p.as_deref() {
        Some(m) if !PROJECTIONS.contains(&m) => Err(serde::de::Error::custom(format!(
            "{m:?} is not a projection method; the methods are {}",
            PROJECTIONS.join(", ")
        ))),
        _ => Ok(p),
    }
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields, default)]
pub struct ViewSpec {
    /// Line-work intent; absent keeps the view's automatic convention.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub hidden: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub centre_marks: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub centre_lines: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub thread_depiction: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub knurl_depiction: Option<bool>,
    /// A half section on this profile view of a turned part ([`crate::half_section`]): true
    /// forces it, false forbids it; absent keeps the automatic choice.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub half_section: Option<bool>,
    #[serde(rename = "break", skip_serializing_if = "Option::is_none")]
    pub broken: Option<crate::broken::Request>,

    #[serde(skip_serializing_if = "Option::is_none")]
    pub detail: Option<crate::detail::DetailSpec>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sheet: Option<u32>,
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub omit: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub place: Option<crate::placement_intent::ViewPlace>,
}

/// What the sheet will say.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Plan {
    /// Authored recognition changes, including suppression with no resulting annotation.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub recognition_corrections: Vec<crate::recognition_corrections::Applied>,
    pub schema: String,
    pub binding: Binding,
    /// Digest of the complete Analysis, including the supplied recognition, that determined
    /// this plan. Absent only in older saved plans. The Spec is embedded separately.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub analysis_sha256: Option<String>,
    /// Per-occurrence policy for recognised evidence without a dimension owner. Informational;
    /// unsupported manufacturing requirements retain their independent warnings.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub feature_dispositions: Vec<crate::dims::FeatureDisposition>,
    #[serde(default)]
    pub status: crate::completeness::Status,
    #[serde(default)]
    pub completeness: crate::completeness::Completeness,
    pub sheet: PlannedSheet,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub sheets: Vec<PlannedSheet>,
    /// Ordered automatic paper trials, including cheap footprint refusals.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub sheet_candidates: Vec<crate::sheet_policy::Candidate>,
    /// Escalation evidence for allocation; never inferred from a problem sentence.
    #[serde(skip)]
    pub table_outcome: crate::hole_table::TableOutcome,
    #[serde(skip)]
    pub(crate) table_restore: Option<Box<Plan>>,
    pub views: Vec<PlannedView>,
    /// Required late sheet furniture, reserved before the notes and tables.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub captions: Vec<crate::iso_caption::PlannedCaption>,
    /// Dimensions and callouts, each with its ID, value, label, default flag and basis.
    #[serde(default)]
    pub dimensions: Vec<PlannedDimension>,
    /// Canonical widths for the editor's snap guide; not part of the drawing document.
    #[serde(skip)]
    pub(crate) callout_widths: BTreeMap<String, f64>,
    /// Each callout's automatic leader tip, relative to its view's origin, and view: the origin
    /// of an offset pin (#12), the canonical plan's when there is one; not part of the drawing
    /// document.
    #[serde(skip)]
    pub(crate) callout_tips: BTreeMap<String, (String, [f64; 2])>,
    /// The gutter translation `view_gutters::apply` gave each view; not part of the document.
    #[serde(skip)]
    pub(crate) gutter_deltas: BTreeMap<String, [f64; 2]>,
    /// A pin replay's canonical gutter translations, spent instead of a new gutter solve.
    #[serde(skip)]
    pub(crate) replay_gutters: Option<BTreeMap<String, [f64; 2]>>,
    /// Canonical linear label positions relative to their carrier origins; editor metadata.
    #[serde(skip)]
    pub(crate) dimension_labels: BTreeMap<String, (String, [f64; 2])>,
    /// Dimensions the spec leaves out, and why (ADR4-8: an authored omission says so).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub omitted: Vec<Omitted>,
    /// General notes, each with its ID, text, rows, default flag, basis and place.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub notes: Vec<PlannedNote>,
    /// The block the notes are drawn in, when it has a place.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub notes_block: Option<NotesBlock>,
    /// A separate compact radius block when authored/PMI notes keep their ruled table.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub radius_notes_block: Option<NotesBlock>,
    /// Obstructed radius groups, located by every occurrence's centre in its named view.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub radius_table: Option<crate::dims::blends::RadiusTable>,
    /// Corner-block tables, each with its ID, cells, default flag, basis and place.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub tables: Vec<PlannedTable>,
    /// The part's AP242 geometric tolerances and datums as feature control frames and datum
    /// feature symbols, each with its ID, view, compartments, basis and place
    /// ([`crate::dims::gdt`]).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub gdt: Vec<crate::dims::gdt::PlannedGdt>,
    /// The part's drawing-wide AP242 requirements (general tolerance, default surface finish,
    /// edge conditions), each with what carries it ([`crate::pmi_lowering::plan_sheet`]).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub requirements: Vec<crate::pmi_lowering::PlannedRequirement>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub process: Option<crate::process::Resolved>,
    /// The plan-view hole table a dropped hole item escalated to ([`crate::hole_table`]).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub hole_table: Option<crate::hole_table::HoleTable>,
    /// Hole schedules chosen for legibility, one per end-on principal view.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub hole_tables: Vec<crate::hole_table::HoleTable>,
    /// The balloons beside the plan view: the hole table's rows' and dropped patterns'.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub balloons: Vec<crate::hole_table::Balloon>,
    /// File-owned characteristic numbers and their resolved drawing carriers.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub characteristics: Vec<crate::characteristics::Characteristic>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub inspection_balloons: Vec<crate::characteristics::Balloon>,
    pub problems: Vec<Problem>,
    /// The spec the plan was made from, embedded in every output.
    pub spec: Spec,
}

impl Plan {
    pub(crate) fn record_callout_offsets(&mut self) {
        let offsets: Vec<_> = self
            .dimensions
            .iter()
            .map(|d| {
                if d.kind != "callout" {
                    return None;
                }
                let (view, tip) = self.automatic_callout_tip(&d.id)?;
                if view != d.view {
                    return None;
                }
                let bend = d.placed.as_ref()?.lines.first()?[1];
                Some([bend[0] - tip[0], bend[1] - tip[1]])
            })
            .collect();
        for (d, offset) in self.dimensions.iter_mut().zip(offsets) {
            d.callout_offset = offset;
        }
    }
    /// A linear dimension's canonical label position in paper mm. Planning metadata only.
    pub fn automatic_dimension_label(&self, id: &str) -> Option<(&str, [f64; 2])> {
        let (view, label) = self.dimension_labels.get(id)?;
        let at = self.views.iter().find(|v| v.id == *view)?.at;
        Some((view, [at[0] + label[0], at[1] + label[1]]))
    }

    fn record_dimension_labels(&mut self) {
        self.dimension_labels = self
            .dimensions
            .iter()
            .filter(|d| d.kind == "linear")
            .filter_map(|d| {
                let label = d.placed.as_ref()?.label_at;
                let at = self.views.iter().find(|v| v.id == d.view)?.at;
                Some((
                    d.id.clone(),
                    (d.view.clone(), [label[0] - at[0], label[1] - at[1]]),
                ))
            })
            .collect();
    }

    /// Widest canonical callout in a view, in paper mm. None for a view without
    /// callouts or a deserialised Plan: this metadata is retained only by planning.
    pub fn canonical_callout_width(&self, view: &str) -> Option<f64> {
        self.callout_widths.get(view).copied()
    }
    /// A callout's automatic leader tip `(view, [x, y])` in paper mm, from which a pinned
    /// `place.offset` is measured. None for a non-callout, one with no automatic placement, or
    /// a deserialised Plan: this metadata is retained only by planning.
    pub fn automatic_callout_tip(&self, id: &str) -> Option<(&str, [f64; 2])> {
        let (view, t) = self.callout_tips.get(id)?;
        let at = self.views.iter().find(|v| v.id == *view)?.at;
        Some((view.as_str(), [at[0] + t[0], at[1] + t[1]]))
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PlannedSheet {
    #[serde(
        default = "crate::sheets::one",
        skip_serializing_if = "crate::sheets::is_one"
    )]
    pub number: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub zones: Option<crate::sheets::Zones>,
    /// Union of drawn-item footprints as a share of usable paper (not reserved bands).
    #[serde(default)]
    pub fill: f64,
    pub size: String,
    /// Width and height, mm.
    pub page: [f64; 2],
    pub scale: String,
    /// The principal views' scale as a number (page mm per world mm).
    pub scale_factor: f64,
    /// Where the isometric sits relative to the orthographic row: `columns`, `stacked-iso`.
    pub arrangement: String,
    /// The title block's box, `[x0, y0, x1, y1]` mm.
    pub title_block: [f64; 4],
    pub default: bool,
    pub basis: String,
    /// The sheet's state (its ID is `sheet`): always shown.
    pub state: crate::items::State,
    pub title: BTreeMap<String, String>,
    /// The title block's cells, each with its box and the value drawn in it (ISSUE-1585-a: a
    /// supplied field is recorded as drawn, in its own cell).
    #[serde(default)]
    pub title_cells: Vec<TitleCell>,
    /// Resolved projection furniture; absent in plans written before the symbol was ported.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub projection_symbol: Option<title_block::ProjectionPlaced>,
    /// The dimension-line spacing the drawing was planned with; absent means normal.
    #[serde(default, skip_serializing_if = "DimensionSpacing::is_normal")]
    pub dimension_spacing: DimensionSpacing,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PlannedView {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub detail: Option<crate::detail::Detail>,
    #[serde(
        default = "crate::sheets::one",
        skip_serializing_if = "crate::sheets::is_one"
    )]
    pub sheet: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub scale_note: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub xref: Option<crate::sheets::CrossRef>,
    pub id: String,
    /// Towards the viewer, and the view's up.
    pub direction: [f64; 3],
    pub up: [f64; 3],
    /// Where the centre of the part's box lands on the page, mm.
    pub at: [f64; 2],
    /// The view's own scale: the sheet's, except an isometric resized to fill its space.
    pub scale: f64,
    /// The page area reserved for the view, `[x0, y0, x1, y1]` mm: a principal view's box with
    /// its annotation bands, or the isometric's rectangle.
    pub area: [f64; 4],
    pub default: bool,
    pub basis: String,
    /// Always shown: a view the plan does not draw is not among its views.
    pub state: crate::items::State,
    /// Automatic chain-line furniture, in paper coordinates; never Spec-addressable.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub line_work: Vec<crate::centrelines::LineWork>,
    /// Effective authored line-work settings (view before sheet); absent preserves defaults.
    #[serde(default, skip_serializing_if = "crate::linework::Settings::is_empty")]
    pub line_style: crate::linework::Settings,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub from_spec: Vec<String>,
    /// A section view's cutting plane.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cut: Option<Cut>,
    /// A profile view drawn as a half section ([`crate::half_section`]).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub half_section: Option<crate::half_section::HalfSection>,
    #[serde(default, rename = "break", skip_serializing_if = "Option::is_none")]
    pub broken: Option<crate::broken::Break>,
}

/// A section's cutting plane: the material on the side `normal` points to is kept and seen from
/// the side removed. The plane is marked on the `marked_in` view, its ends lettered `label`.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Cut {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub xref: Option<crate::sheets::CrossRef>,
    pub label: String,
    pub origin: [f64; 3],
    pub normal: [f64; 3],
    pub marked_in: String,
}

#[derive(Clone, Debug, Default, Deserialize)]
pub struct Problem {
    pub id: String,
    pub code: String,
    /// `error`, `warning` or `info`.
    pub severity: String,
    pub message: String,
    /// The items it is about, by their IDs: the plan's items ([`crate::items::items`], the sheet
    /// as `sheet`) and the Analysis's features.
    pub subjects: Vec<String>,
    /// What else it names that is no item of the plan or feature of the Analysis: a PMI
    /// record's source (its Part 21 provenance, `geometric_tolerance:#123`), a source not read,
    /// a family the recogniser does not give, a view the plan does not draw, a spec entry not
    /// applied. Tool/version provenance and original reader diagnostics also live here.
    /// Pinned leader crossings repeat the callout ID as pin provenance.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub refs: Vec<String>,
    /// Ink crossed by a callout: leader, dimension_line, extension_line, arrow.
    /// Multiple kinds can occur for one ordered item pair.
    #[serde(default)]
    pub crossing_kinds: Vec<String>,
    /// Whether annotation_ink_overlap includes a callout line through the label.
    /// A flare-only arrowhead contact is false; absent on other problem codes.
    #[serde(default)]
    pub ink_is_leader: Option<bool>,
    /// The edit that resolves it, as [`edit`] takes it, where there is one.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub fix: Vec<crate::items::Op>,
    /// Alternative operation bundles; use `offered_fixes` for local → view → sheet order.
    #[serde(default)]
    pub fixes: Vec<crate::fixes::Fix>,
}

impl PartialEq for Problem {
    fn eq(&self, other: &Self) -> bool {
        self.id == other.id
            && self.code == other.code
            && self.severity == other.severity
            && self.message == other.message
            && self.subjects == other.subjects
            && self.refs == other.refs
            && self.crossing_kinds == other.crossing_kinds
            && self.ink_is_leader == other.ink_is_leader
            && self.offered_fixes() == other.offered_fixes()
    }
}
impl Problem {
    pub fn labels(&self) -> crate::finding::Labels {
        crate::finding::labels(
            &self.code,
            self.severity == "info",
            &self.subjects,
            std::iter::empty::<&str>(),
            &self.refs,
            &self.crossing_kinds,
            self.ink_is_leader,
        )
    }

    pub fn offered_fixes(&self) -> Vec<crate::fixes::Fix> {
        let mut fixes = self.fixes.clone();
        if !self.fix.is_empty() && !fixes.iter().any(|f| f.ops == self.fix) {
            fixes.insert(0, crate::fixes::Fix::from_ops(self.fix.clone()));
        }
        fixes.sort_by_key(crate::fixes::Fix::scope);
        fixes
    }
}
impl Serialize for Problem {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        use serde::ser::SerializeStruct;
        let mut s = serializer.serialize_struct("Problem", 12)?;
        s.serialize_field("id", &self.id)?;
        s.serialize_field("code", &self.code)?;
        s.serialize_field("severity", &self.severity)?;
        let labels = self.labels();
        s.serialize_field("impact", &labels.impact)?;
        s.serialize_field("group", &labels.group)?;
        s.serialize_field("message", &self.message)?;
        s.serialize_field("subjects", &self.subjects)?;
        if !self.refs.is_empty() {
            s.serialize_field("refs", &self.refs)?;
        }
        if !self.crossing_kinds.is_empty() {
            s.serialize_field("crossing_kinds", &self.crossing_kinds)?;
        }
        if let Some(is_leader) = self.ink_is_leader {
            s.serialize_field("ink_is_leader", &is_leader)?;
        }
        let fixes = self.offered_fixes();
        if let Some(fix) = fixes.first() {
            s.serialize_field("fix", &fix.ops)?;
        }
        if !fixes.is_empty() {
            s.serialize_field("fixes", &fixes)?;
        }
        s.end()
    }
}

/// A request that cannot be met as asked: what was wrong, and what would be accepted.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Invalid {
    pub message: String,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub choices: Vec<String>,
    /// The Spec entry refused, as an escaped [`crate::items::Op`] path
    /// (`dimensions/d:f:hole:1~1bore.diameter`), where the refusal is that the part has no such
    /// feature, face, dimension, annotation, view or measurement.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
    /// The other entries, as paths, that the same check found the part does not have: a check
    /// refuses at its first, and names the rest here so that
    /// [`crate::session::Session::open_spec`] sets them all aside after one plan.
    #[serde(skip)]
    pub(crate) also: Vec<String>,
}

impl Invalid {
    /// This refusal, naming the Spec entry *collection*/*key* (escaped) it refuses.
    pub(crate) fn at(mut self, collection: &str, key: &str) -> Invalid {
        self.path = Some(format!("{collection}/{}", crate::items::key(key)));
        self
    }
    /// The first of *refusals*, also naming the entries the others refuse when it names one
    /// ([`Invalid::also`]); `None` when there are none.
    pub(crate) fn first_of(refusals: impl IntoIterator<Item = Invalid>) -> Option<Invalid> {
        let mut refusals = refusals.into_iter();
        let mut first = refusals.next()?;
        if first.path.is_some() {
            for e in refusals {
                first.also.extend(e.path);
                first.also.extend(e.also);
            }
        }
        Some(first)
    }
}

impl std::fmt::Display for Invalid {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.message)?;
        if !self.choices.is_empty() {
            write!(f, " Choose one of: {}.", self.choices.join(", "))?;
        }
        Ok(())
    }
}

const INFEASIBLE_ISO_SCALE: &str = "The authored iso scale is infeasible in its composed view zone; the requested scale was not reduced.";

pub(crate) fn invalid(message: impl Into<String>, choices: &[&str]) -> Invalid {
    Invalid {
        path: None,
        also: Vec::new(),
        message: message.into(),
        choices: choices.iter().map(|c| c.to_string()).collect(),
    }
}

/// The standard views, third-angle (draftwright's default projection): towards the viewer, up.
pub(crate) const VIEWS: [(&str, [f64; 3], [f64; 3]); 4] = [
    ("front", [0.0, -1.0, 0.0], [0.0, 0.0, 1.0]),
    ("plan", [0.0, 0.0, 1.0], [0.0, 1.0, 0.0]),
    ("side", [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]),
    ("iso", [1.0, -1.0, 1.0], [0.0, 0.0, 1.0]),
];
/// Section A-A's view name (draftwright's `section_aa`).
pub const SECTION: &str = "section_aa";
const LOADER: &str = concat!("haecceity via draftwright ", env!("CARGO_PKG_VERSION"));

/// The Spec's projection methods (`sheet/projection`), ISO 5456-2's first and third angle.
pub const PROJECTIONS: [&str; 2] = ["first", "third"];

/// A scale as printed: `n:1` or `1:n`.
pub(crate) fn scale_name(s: f64) -> String {
    if s >= 1.0 {
        format!("{}:1", fmt_ratio(s))
    } else {
        format!("1:{}", fmt_ratio(1.0 / s))
    }
}

fn fmt_ratio(r: f64) -> String {
    if (r - r.round()).abs() < 1e-9 {
        format!("{}", r.round())
    } else {
        format!("{r:.3}")
    }
}

/// Why a STEP file gave no Analysis. It reads as its message.
#[derive(Clone, Debug, PartialEq)]
pub struct Unanalysed {
    /// `false`: the request named input that is not what it should be (a file that cannot be
    /// read, bytes that do not parse as STEP, recognition JSON not made from this file).
    /// `true`: the input is what it should be, and the kernel or the bridge could not use it (a
    /// STEP entity the kernel does not model, geometry it could not resolve, a face whose area
    /// it measures differently from the recognition's).
    pub failed: bool,
    pub message: String,
}

impl std::ops::Deref for Unanalysed {
    type Target = str;
    fn deref(&self) -> &str {
        &self.message
    }
}

impl std::fmt::Display for Unanalysed {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.message)
    }
}

fn unanalysed(failed: bool, message: String) -> Unanalysed {
    Unanalysed { failed, message }
}

/// The STEP text of *bytes*: decompressed in memory when they are gzip (they begin with its
/// magic bytes `1f 8b`, whatever the file is called), else as given. Every hash or binding of a
/// STEP file is of these bytes, so a `.step` and its `.step.gz` bind identically.
pub fn step_bytes(bytes: &[u8]) -> Result<std::borrow::Cow<'_, [u8]>, String> {
    if !bytes.starts_with(&[0x1f, 0x8b]) {
        return Ok(std::borrow::Cow::Borrowed(bytes));
    }
    let mut text = Vec::new();
    std::io::Read::read_to_end(&mut flate2::read::MultiGzDecoder::new(bytes), &mut text)
        .map_err(|e| format!("it is gzip but cannot be decompressed: {e}"))?;
    Ok(std::borrow::Cow::Owned(text))
}

/// The STEP text of the file at *path* ([`step_bytes`]: gzip is decompressed).
#[cfg(not(target_arch = "wasm32"))]
pub fn read_step_file(path: &Path) -> Result<Vec<u8>, String> {
    let bytes = std::fs::read(path).map_err(|e| format!("cannot read {}: {e}", path.display()))?;
    step_bytes(&bytes)
        .map(std::borrow::Cow::into_owned)
        .map_err(|e| format!("cannot read {}: {e}", path.display()))
}

/// Reads the STEP file at *path*: the part, and its analysis, without features.
#[cfg(not(target_arch = "wasm32"))]
pub fn analyse(path: &Path) -> Result<(Part, Analysis), Unanalysed> {
    analyse_with(path, None)
}

/// The distinct parts of the STEP file *bytes* (named *path*, for messages), in haecceity's
/// order: what [`analyse_step_part`]'s *part* indexes.
pub fn parts(path: &Path, bytes: &[u8]) -> Result<Vec<FilePart>, Unanalysed> {
    let bytes = step_bytes(bytes)
        .map_err(|e| unanalysed(false, format!("cannot read {}: {e}", path.display())))?;
    let defs = haecceity::step::read_part_definitions(&bytes)
        .map_err(|e| step_refused(&path.display().to_string(), e))?;
    Ok(file_parts(&defs))
}

fn file_parts(defs: &[haecceity::step::PartDefinition]) -> Vec<FilePart> {
    defs.iter()
        .enumerate()
        .map(|(part, d)| FilePart {
            part,
            name: d.name.clone(),
            product_id: d.product_id.clone(),
            faces: d.faces.len(),
            instances: d.placements.len(),
        })
        .collect()
}

/// The placed bodies of the whole-file *part* (read from the file whose parts are *defs*) that
/// coincide with an earlier one, each with the part of the first body of its group. Two bodies
/// coincide when their faces pair off with the same surface type and the same box, to a micron,
/// as the file places them (no transform between them is sought): two solids exported on top of
/// each other (#212), which drawn together count every feature once per body. Face boxes are
/// already read and stand for the faces' geometry well enough here: a hole, slot or edge break
/// moved, added or removed changes a face's box or the count.
fn coincident_bodies(part: &Part, defs: &[haecceity::step::PartDefinition]) -> Vec<CoincidentBody> {
    let placed: usize = defs.iter().map(|d| d.placements.len()).sum();
    if placed < 2 {
        return Vec::new();
    }
    let instance_faces = instance_faces(part);
    let micron = |x: f64| (x * 1e3).round() as i64;
    // Each distinct body's faces (surface type and box, sorted), with its part.
    type Faces = Vec<(u8, [i64; 6])>;
    let mut groups: Vec<(Faces, usize)> = Vec::new();
    let mut found = Vec::new();
    for (k, d) in defs.iter().enumerate() {
        for (placement, p) in d.placements.iter().enumerate() {
            let mut faces: Faces = p
                .instances
                .iter()
                .flat_map(|i| instance_faces.get(i).into_iter().flatten())
                .map(|&f| {
                    let b = part.face_bounds(f);
                    let (lo, hi) = (b.min.map(micron), b.max.map(micron));
                    (
                        part.faces[f].surface.kind() as u8,
                        [lo[0], lo[1], lo[2], hi[0], hi[1], hi[2]],
                    )
                })
                .collect();
            if faces.is_empty() {
                continue;
            }
            faces.sort_unstable();
            match groups.iter().find(|(g, _)| *g == faces) {
                Some(&(_, of)) => found.push(CoincidentBody {
                    part: k,
                    name: d.name.clone(),
                    placement,
                    of,
                }),
                None => groups.push((faces, k)),
            }
        }
    }
    found
}

/// The whole-file *part*'s faces by the instance of the file they came from.
fn instance_faces(part: &Part) -> BTreeMap<usize, Vec<usize>> {
    let mut faces: BTreeMap<usize, Vec<usize>> = BTreeMap::new();
    for f in 0..part.faces.len() {
        if let Some(s) = part.face_source(f) {
            faces.entry(s.instance).or_default().push(f);
        }
    }
    faces
}

/// The principal part of the whole-file *part* (read from the file whose parts are *defs*), when
/// it holds the others (#219): the part whose placed body has the largest box diagonal (the
/// earlier on a tie), when every other placed body lies within that body's box, to a micron. An
/// assembly drawn whole dimensions such bodies (a pulley's set screws, a bearing's inner sleeve)
/// as features of one part, so with no part chosen this one is drawn alone. None when a body
/// lies outside it (parts side by side, or a pin standing out of a plate, are still drawn whole)
/// or no body's faces can be told apart. Containment is by box in the assembly frame, not by
/// material, so a neighbour sitting in the corner of an L-bracket's box counts as held.
fn principal_part(part: &Part, defs: &[haecceity::step::PartDefinition]) -> Option<usize> {
    let instance_faces = instance_faces(part);
    // Each placed body's part and box.
    let mut bodies: Vec<(usize, [f64; 3], [f64; 3])> = Vec::new();
    for (k, d) in defs.iter().enumerate() {
        for p in &d.placements {
            let (mut lo, mut hi) = ([f64::INFINITY; 3], [f64::NEG_INFINITY; 3]);
            for &f in p
                .instances
                .iter()
                .flat_map(|i| instance_faces.get(i).into_iter().flatten())
            {
                let b = part.face_bounds(f);
                for a in 0..3 {
                    lo[a] = lo[a].min(b.min[a]);
                    hi[a] = hi[a].max(b.max[a]);
                }
            }
            if lo[0].is_finite() {
                bodies.push((k, lo, hi));
            }
        }
    }
    let diagonal = |&(_, lo, hi): &(usize, [f64; 3], [f64; 3])| {
        (0..3).map(|a| (hi[a] - lo[a]).powi(2)).sum::<f64>().sqrt()
    };
    let mut principal = bodies.first()?;
    for b in &bodies[1..] {
        if diagonal(b) > diagonal(principal) + 1e-6 {
            principal = b;
        }
    }
    let &(k, lo, hi) = principal;
    bodies
        .iter()
        .filter(|b| !std::ptr::eq(*b, principal))
        .all(|&(_, l, h)| (0..3).all(|a| l[a] >= lo[a] - 1e-3 && h[a] <= hi[a] + 1e-3))
        .then_some(k)
}

/// `0: plate, 1: pin`: an assembly's parts as a message names them.
pub fn part_list(parts: &[FilePart]) -> String {
    parts
        .iter()
        .map(|p| format!("{}: {}", p.part, p.name))
        .collect::<Vec<_>>()
        .join(", ")
}

/// Why *path* could not be read as STEP: a file that does not parse is not STEP; one that parses
/// but holds what the kernel does not model (or lost on reading) is the kernel's failure.
fn step_refused(name: &str, e: haecceity::step::StepError) -> Unanalysed {
    let failed = !matches!(e, haecceity::step::StepError::Parse(_));
    unanalysed(failed, format!("cannot read {} as STEP: {e}", name))
}

/// Reads the STEP file at *path*, with its features from *recognition*: Python quiddity's JSON
/// for the same file, written by `tools/recognise.py` ([`crate::bridge`]). Refused when the
/// kernel did not resolve a face or edge the isometric draws.
#[cfg(not(target_arch = "wasm32"))]
pub fn analyse_with(
    path: &Path,
    recognition: Option<&Path>,
) -> Result<(Part, Analysis), Unanalysed> {
    analyse_with_part(path, recognition, None)
}

/// [`analyse_with`], reading one part of an assembly alone when *part* is given
/// ([`analyse_step_part`]).
#[cfg(not(target_arch = "wasm32"))]
pub fn analyse_with_part(
    path: &Path,
    recognition: Option<&Path>,
    part: Option<usize>,
) -> Result<(Part, Analysis), Unanalysed> {
    let bytes = read_step_file(path).map_err(|e| unanalysed(false, e))?;
    let (part, mut analysis) = analyse_step_part(path, &bytes, part)?;
    if let Some(r) = recognition {
        let json = std::fs::read_to_string(r)
            .map_err(|e| unanalysed(false, format!("cannot read {}: {e}", r.display())))?;
        recognise(&part, &mut analysis, &json).map_err(|e| {
            // JSON of the right format made from this very file is what the request should
            // name; the bridge refusing it after that (a face it measures differently, a record
            // it has no home for) is the port's failure, not the caller's.
            let header = serde_json::from_str::<serde_json::Value>(&json).ok();
            let field = |k: &str| {
                header
                    .as_ref()
                    .and_then(|h| h.get(k)?.as_str().map(String::from))
            };
            let failed = field("format").as_deref() == Some(crate::bridge::RECOGNITION_FORMAT)
                && field("source_sha256").as_ref() == Some(&analysis.binding.source_sha256);
            unanalysed(failed, format!("{}: {e}", r.display()))
        })?;
    }
    Ok((part, analysis))
}

/// Reads *bytes*, the STEP file named *path* (which is not read: the name is for the part and
/// for messages), as [`analyse_with`] reads a file, without features: for callers that have the
/// file's bytes and no file system, such as a browser.
pub fn analyse_step(path: &Path, bytes: &[u8]) -> Result<(Part, Analysis), Unanalysed> {
    analyse_step_part(path, bytes, None)
}

/// [`analyse_step`], of one part of the file when *part* is given: part *k* of [`parts`], read
/// alone in its own frame (`haecceity::StepFile::part`, its first placement's shape with no
/// placement applied), its PMI only (other parts' records are dropped). The binding names the
/// part and its index. Part 0 of a file of one part is the whole file, read as with none. With
/// none, an assembly's parts are listed in `parts`, and its principal part, if any, is read alone
/// ([`principal_part`]; the plan warns that the others are not drawn), or the one part every
/// body coincides with; otherwise the file is read whole. Refused, naming the parts, when there
/// is no part *k*.
pub fn analyse_step_part(
    path: &Path,
    bytes: &[u8],
    chosen: Option<usize>,
) -> Result<(Part, Analysis), Unanalysed> {
    let stem = || {
        let plain = path
            .extension()
            .is_some_and(|e| e.eq_ignore_ascii_case("gz"));
        let path = if plain {
            Path::new(path.file_stem().unwrap_or_default())
        } else {
            path
        };
        path.file_stem().map_or(String::new(), |s| {
            s.to_string_lossy().trim_end_matches(".step").to_string()
        })
    };
    analyse_step_input(&path.display().to_string(), stem(), bytes, chosen)
}

/// Analyse in-memory STEP (or gzip) bytes with Rust feature recognition. `name` is only a
/// display name, never a filesystem path. Browser callers need no filesystem or clock.
pub fn analyse_native_bytes(name: &str, bytes: &[u8]) -> Result<(Part, Analysis), Unanalysed> {
    analyse_native_bytes_part(name, bytes, None)
}

/// [`analyse_native_bytes`] of part *part* of an assembly alone, when given (an index as
/// `parts` lists them), in its own frame and with its PMI only.
pub fn analyse_native_bytes_part(
    name: &str,
    bytes: &[u8],
    part: Option<usize>,
) -> Result<(Part, Analysis), Unanalysed> {
    let file = name.rsplit('/').next().unwrap_or(name);
    let plain = if file
        .get(file.len().saturating_sub(3)..)
        .is_some_and(|s| s.eq_ignore_ascii_case(".gz"))
    {
        &file[..file.len() - 3]
    } else {
        file
    };
    let stem = plain
        .rsplit_once('.')
        .filter(|(stem, _)| !stem.is_empty())
        .map_or(plain, |(stem, _)| stem)
        .trim_end_matches(".step");
    let (part, mut analysis) = analyse_step_input(name, stem.into(), bytes, part)?;
    recognise_native(&part, &mut analysis).map_err(|e| {
        unanalysed(
            true,
            format!("Rust quiddity's recognition of {name} was refused: {e}"),
        )
    })?;
    Ok((part, analysis))
}

fn analyse_step_input(
    name: &str,
    stem: String,
    bytes: &[u8],
    chosen: Option<usize>,
) -> Result<(Part, Analysis), Unanalysed> {
    let bytes =
        &*step_bytes(bytes).map_err(|e| unanalysed(false, format!("cannot read {}: {e}", name)))?;
    // The file's part definitions are read in both cases (the PMI reading needs them too, and
    // is given these rather than reading them again). A gzipped file's name is its STEP name
    // with `.gz` after it, so `part.step.gz` and `part.stp.gz` are named as `part.step` is.
    let mut chosen = chosen;
    let mut coincident = Vec::new();
    let mut principal = false;
    let (part, defs, part_name) = match chosen {
        None => {
            let part = haecceity::read_step(bytes).map_err(|e| step_refused(name, e))?;
            // `read_part_definitions` is this file's `parts`: read once, kept for part *k*.
            let file = haecceity::step::StepFile::read(bytes)
                .map_err(|e| format!("cannot read the parts: {e}"));
            if let Ok(file) = &file {
                coincident = coincident_bodies(&part, &file.parts);
            }
            match (file, coincident.first()) {
                // Every body of an assembly coincides with part *k*'s first: *k* alone is drawn,
                // as `--part k` draws it, so no feature is counted once per copy (#212). A file
                // of one part placed on itself is still read whole: its part 0 is the file.
                (Ok(file), Some(c))
                    if file.parts.len() > 1
                        && coincident.len() + 1
                            == file.parts.iter().map(|d| d.placements.len()).sum::<usize>() =>
                {
                    let k = c.of;
                    chosen = Some(k);
                    let part = file.part(k).map_err(|e| step_refused(name, e))?;
                    let name = file.parts[k].name.clone();
                    (part, Ok(file.parts), name)
                }
                // Bodies within the largest one's box: that part alone (#219), as `--part k`
                // draws it, so no body it holds is dimensioned as its features.
                (Ok(file), _) if file.parts.len() > 1 => match principal_part(&part, &file.parts) {
                    Some(k) => {
                        chosen = Some(k);
                        principal = true;
                        coincident = Vec::new();
                        let part = file.part(k).map_err(|e| step_refused(name, e))?;
                        let name = file.parts[k].name.clone();
                        (part, Ok(file.parts), name)
                    }
                    None => (part, Ok(file.parts), stem.clone()),
                },
                (file, _) => (part, file.map(|f| f.parts), stem.clone()),
            }
        }
        Some(k) => {
            let file = haecceity::step::StepFile::read(bytes).map_err(|e| step_refused(name, e))?;
            if k == 0 && file.parts.len() == 1 {
                // Part 0 of a file of one part is the whole file: drawn, titled (by the file's
                // stem, not the product's name) and bound exactly as without a part chosen.
                chosen = None;
                let part = haecceity::read_step(bytes).map_err(|e| step_refused(name, e))?;
                (part, Ok(file.parts), stem.clone())
            } else {
                if k >= file.parts.len() {
                    return Err(unanalysed(
                        false,
                        format!(
                            "{} has {} part(s) ({}); there is no part {k}",
                            name,
                            file.parts.len(),
                            part_list(&file_parts(&file.parts))
                        ),
                    ));
                }
                let part = file.part(k).map_err(|e| step_refused(name, e))?;
                let name = file.parts[k].name.clone();
                (part, Ok(file.parts), name)
            }
        }
    };
    analyse_read_part(
        name, stem, part_name, bytes, part, defs, chosen, coincident, principal,
    )
}

/// Analyse a part already read from a pack's shared StepFile, using the single-part pipeline.
pub(crate) fn analyse_pack_part(
    name: &str,
    bytes: &[u8],
    part: Part,
    defs: &[haecceity::step::PartDefinition],
    index: usize,
) -> Result<(Part, Analysis), Unanalysed> {
    let stem = Path::new(name)
        .file_stem()
        .unwrap_or_default()
        .to_string_lossy()
        .into_owned();
    analyse_read_part(
        name,
        stem,
        defs[index].name.clone(),
        bytes,
        part,
        Ok(defs.to_vec()),
        Some(index),
        Vec::new(),
        false,
    )
}

pub(crate) fn pack_projection(
    a: &Analysis,
    direction: [f64; 3],
    up: [f64; 3],
) -> Option<Vec<Projected>> {
    if a.squared.is_some() {
        return None;
    }
    projected_ink::prepared(&a.binding, direction, up)
}

/// Whole assembled geometry, without the merged-draw principal-part heuristic.
pub(crate) fn analyse_pack_assembly(
    name: &str,
    bytes: &[u8],
    horizontal: Option<usize>,
) -> Result<(Part, Analysis, haecceity::step::Placement), Unanalysed> {
    let part = haecceity::read_step(bytes).map_err(|e| step_refused(name, e))?;
    let b = part.bounds();
    let x = horizontal.unwrap_or_else(|| {
        (0..3)
            .max_by(|&i, &j| (b.max[i] - b.min[i]).total_cmp(&(b.max[j] - b.min[j])))
            .expect("three axes")
    });
    // Use one proper-rigid frame for the entire projection set. Preserve the
    // source depth axis where possible, and keep z up when swapping x and y.
    let mut dx = [0.; 3];
    dx[x] = 1.;
    let mut dy = [0.; 3];
    if x == 1 {
        dy[0] = -1.;
    } else {
        dy[1] = 1.;
    }
    let dz = haecceity::geom::cross(dx, dy);
    let placement = [dx, dy, dz].map(|d| [d[0], d[1], d[2], 0.]);
    let part = part
        .transformed(&placement)
        .map_err(|e| step_refused(name, e))?;
    let b = part.bounds();
    let mut analysis = Analysis {
        schema: ANALYSIS_SCHEMA.into(),
        binding: Binding {
            source_sha256: Sha256::digest(bytes)
                .iter()
                .map(|x| format!("{x:02x}"))
                .collect(),
            part: name.into(),
            face_count: part.faces.len(),
            loader: LOADER.into(),
            part_index: None,
        },
        product_name: None,
        bom: None,
        source_stem: Some(name.into()),
        envelope: [b.min, b.max],
        solid_count: Some(part.solids.len()),
        body_faces: None,
        stock_faces: Vec::new(),
        face_accounting: None,
        recognition: None,
        features: Vec::new(),
        blend_sites: BTreeMap::new(),
        radius_arcs: BTreeMap::new(),
        profile_arcs: BTreeMap::new(),
        radius_reliefs: Vec::new(),
        iso_bounds: [0.; 4],
        edge_ink: None,
        modelled_thread_ink: BTreeMap::new(),
        levels: Vec::new(),
        level_supports: Vec::new(),
        risers: Vec::new(),
        principal_profiles: Vec::new(),
        outer_profiles: Vec::new(),
        unrecognised: Vec::new(),
        unexplained_shapes: Vec::new(),
        pmi: None,
        face_attachments: Vec::new(),
        parts: Vec::new(),
        part_keys: BTreeMap::new(),
        coincident: Vec::new(),
        principal: false,
        squared: None,
    };
    // Envelope projections suffice to reserve GA ink. Manufacturing recognition,
    // PMI attachment and an eager isometric HLR are irrelevant here; the renderer
    // resolves the actual assembled outlines once per camera.
    let iso = View::new(VIEWS[3].1, VIEWS[3].2).expect("standard camera");
    let (size, _) = extent(&analysis, &iso);
    analysis.iso_bounds = [-size[0] / 2., -size[1] / 2., size[0] / 2., size[1] / 2.];
    Ok((part, analysis, placement))
}

#[allow(clippy::too_many_arguments)]
fn analyse_read_part(
    name: &str,
    stem: String,
    part_name: String,
    bytes: &[u8],
    part: Part,
    defs: Result<Vec<haecceity::step::PartDefinition>, String>,
    chosen: Option<usize>,
    coincident: Vec<CoincidentBody>,
    principal: bool,
) -> Result<(Part, Analysis), Unanalysed> {
    // A part a fraction of a degree off the file's axes is read in its own frame (#216), unless
    // the file carries PMI: that is placed, and its frames and sites measured, in the file's
    // axes, so a part with PMI is drawn in them. A motion the kernel refuses leaves the part
    // in the file's axes, as before.
    let (pmi, bom) = crate::pmi::attach_with(
        bytes,
        &part,
        defs.as_deref().map_err(String::as_str),
        chosen,
    );
    let mut squared = pmi.is_none().then(|| Squared::of(&part)).flatten();
    let moved = squared
        .as_ref()
        .and_then(|s| part.transformed(&s.placement).ok());
    if moved.is_none() {
        squared = None;
    }
    let part = moved.unwrap_or(part);
    let drawn = crate::quiddity_evidence::drawn_faces(&part);
    let b = crate::quiddity_evidence::drawn_bounds(&part, &drawn);
    let (level_supports, risers) = crate::dims::levels::evidence(&part);
    let mut analysis = Analysis {
        schema: ANALYSIS_SCHEMA.into(),
        binding: Binding {
            source_sha256: Sha256::digest(bytes)
                .iter()
                .map(|x| format!("{x:02x}"))
                .collect(),
            part: part_name,
            face_count: part.faces.len(),
            loader: LOADER.into(),
            part_index: chosen,
        },
        product_name: defs.as_ref().ok().and_then(|defs| {
            chosen
                .or_else(|| (defs.len() == 1).then_some(0))
                .and_then(|i| defs.get(i))
                .map(|d| d.name.clone())
        }),
        bom,
        source_stem: chosen.map(|_| stem),
        envelope: [b.min, b.max],
        solid_count: Some(part.solids.len()),
        body_faces: {
            let fills = |s: &haecceity::brep::Solid| {
                let (mut lo, mut hi) = ([f64::INFINITY; 3], [f64::NEG_INFINITY; 3]);
                for &f in &s.faces {
                    let fb = part.face_bounds(f);
                    for k in 0..3 {
                        lo[k] = lo[k].min(fb.min[k]);
                        hi[k] = hi[k].max(fb.max[k]);
                    }
                }
                (0..3).all(|k| (lo[k] - b.min[k]).abs() <= 1e-5 && (hi[k] - b.max[k]).abs() <= 1e-5)
            };
            let filling: Vec<_> = part.solids.iter().filter(|s| fills(s)).collect();
            match filling.as_slice() {
                [s] if part.solids.len() > 1 => Some(s.faces.clone()),
                _ => None,
            }
        },
        face_accounting: None,
        stock_faces: drawn
            .iter()
            .copied()
            .filter(|&f| {
                if !matches!(
                    part.faces[f].surface,
                    haecceity::geom::Surface::Plane { .. }
                ) {
                    return false;
                }
                let fb = part.face_bounds(f);
                (0..3).any(|k| {
                    fb.max[k] - fb.min[k] <= 1e-5
                        && ((fb.min[k] - b.min[k]).abs() <= 1e-5
                            || (fb.max[k] - b.max[k]).abs() <= 1e-5)
                })
            })
            .collect(),
        recognition: None,
        features: Vec::new(),
        blend_sites: BTreeMap::new(),
        radius_arcs: BTreeMap::new(),
        profile_arcs: BTreeMap::new(),
        radius_reliefs: crate::blends::reliefs(&part),
        iso_bounds: [0.0; 4],
        edge_ink: None,
        modelled_thread_ink: BTreeMap::new(),
        levels: crate::demand::face_levels(&part),
        level_supports,
        risers,
        principal_profiles: crate::lint::profiles::evidence(&part),
        outer_profiles: crate::lint::profile_angles::evidence(&part),
        unrecognised: Vec::new(),
        unexplained_shapes: Vec::new(),
        pmi,
        face_attachments: crate::pmi::face_attachments(&part),
        part_keys: defs
            .as_ref()
            .ok()
            .filter(|defs| defs.len() > 1)
            .map(|defs| {
                let parts = file_parts(defs);
                crate::pack::part_keys(&parts)
                    .into_iter()
                    .zip(parts.iter().map(|p| p.part))
                    .collect()
            })
            .unwrap_or_default(),
        parts: match &defs {
            Ok(defs) if defs.len() > 1 => file_parts(defs),
            _ => Vec::new(),
        },
        coincident,
        principal,
        squared,
    };
    let (_, toward, up) = VIEWS[3];
    // Invariant: Planned cameras come from the fixed views or checked section/detail cameras, with nonparallel axes.
    let iso = View::new(toward, up).expect("standard view");
    let centre = extent(&analysis, &iso).1;
    let mut bounds = [
        f64::INFINITY,
        f64::INFINITY,
        f64::NEG_INFINITY,
        f64::NEG_INFINITY,
    ];
    for p in project(&part, &iso)
        .map_err(|e| {
            unanalysed(
                true,
                format!(
                    "cannot draw {}: the kernel did not resolve its {e} on reading",
                    name
                ),
            )
        })?
        .iter()
        .flat_map(|piece| piece.points.iter())
    {
        let q = [p[0] - centre[0], p[1] - centre[1]];
        bounds = [
            bounds[0].min(q[0]),
            bounds[1].min(q[1]),
            bounds[2].max(q[0]),
            bounds[3].max(q[1]),
        ];
    }
    if bounds[0].is_finite() {
        analysis.iso_bounds = bounds;
    }
    analysis.edge_ink = Some(projected_ink::evidence(&part));
    dimension_geometry::memberships(&mut analysis);
    Ok((part, analysis))
}

/// [`analyse_step`], with its features from Rust quiddity's recognition, as [`analyse_native`].
#[cfg(not(target_arch = "wasm32"))]
pub fn analyse_native_step(path: &Path, bytes: &[u8]) -> Result<(Part, Analysis), Unanalysed> {
    analyse_native_step_part(path, bytes, None)
}

/// [`analyse_native_step`] of one part of the file when *part* is given ([`analyse_step_part`]).
#[cfg(not(target_arch = "wasm32"))]
pub fn analyse_native_step_part(
    path: &Path,
    bytes: &[u8],
    part: Option<usize>,
) -> Result<(Part, Analysis), Unanalysed> {
    let (part, mut analysis) = analyse_step_part(path, bytes, part)?;
    recognise_native(&part, &mut analysis).map_err(|e| {
        unanalysed(
            true,
            format!(
                "Rust quiddity's recognition of {} was refused: {e}",
                path.display()
            ),
        )
    })?;
    Ok((part, analysis))
}

/// Takes *analysis*'s features from Python quiddity's recognition JSON for the same file
/// ([`crate::bridge`]).
pub fn recognise(part: &Part, analysis: &mut Analysis, json: &str) -> Result<(), String> {
    let (recognition, features) = crate::bridge::read(part, &analysis.binding.source_sha256, json)?;
    analysis.recognition = Some(recognition);
    analysis.blend_sites = crate::blends::evidence(part, &features);
    analysis.radius_arcs = crate::radius::evidence(part, &features);
    analysis.profile_arcs = crate::arc::evidence(part, &features);
    analysis.features = features;
    dimension_geometry::memberships(analysis);
    analysis.unrecognised.clear();
    Ok(())
}

/// Families absent from the native document. Recognition/3 carries all families this
/// consumer requires; unsupported published families are reported per part instead.
pub const NOT_IN_NATIVE_DOCUMENT: [&str; 0] = [];

/// Reads the STEP file at *path*, as [`analyse_with`] reads it, with its features from Rust
/// quiddity's recognition document ([`crate::bridge::native`]) in place of Python quiddity's JSON.
/// Refused, as the kernel's failure, when the native reader refuses the document; never read as
/// a part without features.
#[cfg(not(target_arch = "wasm32"))]
pub fn analyse_native(path: &Path) -> Result<(Part, Analysis), Unanalysed> {
    analyse_native_part(path, None)
}

/// [`analyse_native`] of one part of the file when *part* is given ([`analyse_step_part`]).
#[cfg(not(target_arch = "wasm32"))]
pub fn analyse_native_part(
    path: &Path,
    part: Option<usize>,
) -> Result<(Part, Analysis), Unanalysed> {
    let (part, mut analysis) = analyse_with_part(path, None, part)?;
    recognise_native(&part, &mut analysis).map_err(|e| {
        unanalysed(
            true,
            format!(
                "Rust quiddity's recognition of {} was refused: {e}",
                path.display()
            ),
        )
    })?;
    Ok((part, analysis))
}

/// Takes *analysis*'s features from Rust quiddity's recognition of *part*
/// ([`recognise_document`]).
pub fn recognise_native(part: &Part, analysis: &mut Analysis) -> Result<(), String> {
    let recognition = quiddity::correspondence::recognise(part);
    recognise_document(
        part,
        analysis,
        &quiddity::recognition::document(&recognition, part.faces.len()),
    )
}

/// Takes *analysis*'s features from *doc*, Rust quiddity's recognition document for *part*
/// ([`crate::bridge::native`]), in the part's own frame, and records in `unrecognised` the
/// families *doc* has that are not read, and those it does not carry
/// ([`NOT_IN_NATIVE_DOCUMENT`]). A refused document leaves *analysis* as it was.
pub fn recognise_document(
    part: &Part,
    analysis: &mut Analysis,
    doc: &quiddity::recognition::RecognitionDocument,
) -> Result<(), String> {
    use crate::bridge::native;
    let mut doc = native::in_file_axes(doc);
    crate::bridge::refused_threads::complete(part, &mut doc);
    let doc = &doc;
    let read = native::read(part, doc)?;
    let mut features = read.holes;
    features.extend(read.features);
    // As the bridge orders them: by kind, then faces (each kind's IDs are ranked so already).
    features.sort_by(|a, b| (&a.kind, &a.faces).cmp(&(&b.kind, &b.faces)));
    let mut counts = BTreeMap::<&str, usize>::new();
    for r in doc
        .records
        .iter()
        .filter(|r| !native::represented(&r.family) || native::left_in_frame(r))
    {
        *counts.entry(r.family.as_str()).or_default() += 1;
    }
    let mut unrecognised: Vec<Unrecognised> = NOT_IN_NATIVE_DOCUMENT
        .iter()
        .filter(|f| !native::READ.contains(f) && !counts.contains_key(*f))
        .map(|f| Unrecognised {
            family: f.to_string(),
            records: None,
        })
        .collect();
    unrecognised.extend(counts.into_iter().map(|(f, n)| Unrecognised {
        family: f.to_string(),
        records: Some(n),
    }));
    analysis.unrecognised = unrecognised;
    analysis.face_accounting = doc
        .face_accounting
        .as_ref()
        .filter(|groups| groups.len() == part.faces.len())
        .map(|groups| {
            use quiddity::recognition::FaceAccounting as A;
            groups
                .iter()
                .map(|g| match g {
                    A::Feature { .. } => FaceGroup::Feature,
                    A::Body { .. } => FaceGroup::Body,
                    A::Refused { .. } => FaceGroup::Refused,
                    A::Unexplained => FaceGroup::Unexplained,
                })
                .collect()
        });
    analysis.unexplained_shapes = analysis
        .face_accounting
        .as_ref()
        .map_or(Vec::new(), |groups| {
            crate::unexplained::shapes(part, groups)
        });
    analysis.recognition = Some(Recognition {
        recogniser: read.recogniser,
        frame: native::IDENTITY,
        evidence: read.evidence,
        refusals: read.refusals,
        patterns: read.patterns,
    });
    analysis.blend_sites = crate::blends::evidence(part, &features);
    analysis.radius_arcs = crate::radius::evidence(part, &features);
    analysis.profile_arcs = crate::arc::evidence(part, &features);
    analysis.features = features;
    dimension_geometry::memberships(analysis);
    analysis.modelled_thread_ink = crate::modelled_threads::evidence(part, analysis);
    // Physical threads use conventional carriers. Their raw helix HLR would be
    // thrown away by the planner and renderer; keep raw conservative evidence
    // for the off override without eagerly resolving those expensive cameras.
    let inspection = if analysis
        .features
        .iter()
        .any(|f| f.kind == "modelled_thread")
    {
        BTreeSet::new()
    } else {
        crate::dims::nominal_inspection_views(analysis)
    };
    if !inspection.is_empty()
        && let Some(ink) = &mut analysis.edge_ink
    {
        ink.resolved = projected_ink::resolved(part, &analysis.binding, &inspection);
    }
    Ok(())
}

impl Analysis {
    /// Source projection plus the recognised spring's simplified outer silhouette.
    /// Keep the immutable raw bounds for analysis and older saved documents.
    pub(crate) fn drawing_iso_bounds(&self) -> [f64; 4] {
        let (_, toward, up) = VIEWS[3];
        let view = View::new(toward, up).expect("standard iso view");
        crate::springs::iso_bounds(self, &view)
    }
}

/// The box (width, height) and centre of the part's envelope seen in *view*, mm at 1:1.
pub(crate) fn extent(analysis: &Analysis, view: &View) -> ([f64; 2], [f64; 2]) {
    envelope_extent(analysis.envelope, view)
}

pub(crate) fn envelope_extent(envelope: [[f64; 3]; 2], view: &View) -> ([f64; 2], [f64; 2]) {
    let [lo, hi] = envelope;
    let (mut min, mut max) = ([f64::INFINITY; 2], [f64::NEG_INFINITY; 2]);
    for i in 0..8 {
        let corner = [0, 1, 2].map(|k| if i >> k & 1 == 0 { lo[k] } else { hi[k] });
        let p = view.map(corner);
        for k in 0..2 {
            min[k] = min[k].min(p[k]);
            max[k] = max[k].max(p[k]);
        }
    }
    (
        [max[0] - min[0], max[1] - min[1]],
        [0.5 * (min[0] + max[0]), 0.5 * (min[1] + max[1])],
    )
}

/// The problems that make a plan's layout invalid for the recovery: draftwright's hard layout
/// codes (`linting.quality._HARD_LAYOUT_CODES`) as the port reports them (`annotation_off_sheet`
/// is its `annotation_out_of_bounds`; views that do not fit are its `view_out_of_bounds` and
/// `view_overlap`). `annotation_over_view` is not one: the port judges it by the view's box,
/// draftwright's `view_annotation_overlap` by the view's line-work.
const HARD_LAYOUT: [&str; 9] = [
    "annotation_off_sheet",
    "annotation_over_title_block",
    "annotation_overlap",
    "annotation_ink_overlap",
    "title_field_overflow",
    "page_fit_uncertain",
    "views_do_not_fit",
    "notes_not_placed",
    "table_not_placed",
];

/// The tail of the `notes_not_placed` message for a blanket radius note withdrawn because an
/// exception is undrawn: a completeness refusal that no sheet or scale changes, so the sheet
/// choice does not count it as a hard layout finding.
const RADIUS_DEFAULT_WITHDRAWN: &str =
    "cannot assert a blanket radius while an exception is undrawn.";

/// A plan's findings for the recovery. The port drops for want of room only the hole items it
/// placed off the sheet ([`crate::hole_table`]: an item that collides is placed and reported; one
/// no shown view carries is kept unplaced, which no sheet or scale changes), so its completeness
/// tier reads those drops still reported (each a `*_dropped` blocker of draftwright's
/// `_is_required_scale_drop`; a hole table's rows clear the ones they state) and the one other
/// required loss draftwright's blockers have that a scale decides: hole locations too close to
/// dimension legibly ([`layout::crowded_locations`], draftwright's `location_ref_dropped`),
/// counted per view and axis, a pattern's centre ordinate apart (draftwright keeps it whatever
/// its spacing).
pub(crate) fn findings(plan: &Plan) -> layout::Findings {
    let mut axes: BTreeMap<(&str, &str), Vec<f64>> = BTreeMap::new();
    for d in &plan.dimensions {
        let Some(m) = d.measurements.first() else {
            continue;
        };
        let located = matches!(
            m.parameter.as_str(),
            "location.length.x" | "location.length.y"
        );
        let centre = d
            .measurements
            .iter()
            .any(|m| m.feature.starts_with("f:hole_pattern:"));
        if d.kind == "linear" && located && !centre {
            axes.entry((d.view.as_str(), m.parameter.as_str()))
                .or_default()
                .push(d.value);
        }
    }
    let scale = plan.sheet.scale_factor;
    layout::Findings {
        hard: usize::from(crate::sheets::floor(&plan.spec).is_some_and(|f| scale < f))
            + plan
                .problems
                .iter()
                .filter(|p| HARD_LAYOUT.contains(&p.code.as_str()) && p.severity != "info")
                .filter(|p| !p.message.ends_with(RADIUS_DEFAULT_WITHDRAWN))
                .count(),
        dropped: axes
            .values()
            .map(|o| layout::crowded_locations(o, scale))
            .sum::<usize>()
            + drops(plan),
    }
}

/// A plan's annotations over a view not their own: a label on a view's box or a line across
/// another view (`annotation_over_view`, `annotation_line_over_view`), at warning or error.
pub(crate) fn over_views(plan: &Plan) -> usize {
    plan.problems
        .iter()
        .filter(|p| {
            matches!(
                p.code.as_str(),
                "annotation_over_view" | "annotation_line_over_view"
            ) && p.severity != "info"
        })
        .count()
}

/// The hole items a plan dropped for want of room and still reports ([`crate::hole_table`]).
fn drops(plan: &Plan) -> usize {
    plan.problems
        .iter()
        .filter(|p| crate::hole_table::DROPS.contains(&p.code.as_str()))
        .count()
}

/// Name the reported hard findings counted by the recovery, including the Spec's scale floor.
pub(crate) fn hard_layout_codes(plan: &Plan) -> BTreeMap<&str, usize> {
    let mut codes = BTreeMap::new();
    for p in plan.problems.iter().filter(|p| {
        HARD_LAYOUT.contains(&p.code.as_str())
            && p.severity != "info"
            && !p.message.ends_with(RADIUS_DEFAULT_WITHDRAWN)
    }) {
        *codes.entry(p.code.as_str()).or_default() += 1;
    }
    if crate::sheets::floor(&plan.spec).is_some_and(|floor| plan.sheet.scale_factor < floor) {
        codes.insert("sheet_below_smallest_scale", 1);
    }
    codes
}

/// What a plan's recovery findings are, in words; *dropped* of them are hole items dropped.
fn said(f: layout::Findings, dropped: usize, codes: BTreeMap<&str, usize>) -> String {
    let mut hard = match f.hard {
        1 => "1 hard layout finding".to_string(),
        n => format!("{n} hard layout findings"),
    };
    if !codes.is_empty() {
        hard += &format!(
            " [{}]",
            codes
                .into_iter()
                .map(|(code, count)| format!("{code}: {count}"))
                .collect::<Vec<_>>()
                .join(", ")
        );
    }
    let crowded = match f.dropped - dropped {
        0 => None,
        1 => Some("1 hole location too close to read".to_string()),
        n => Some(format!("{n} hole locations too close to read")),
    };
    let lost = match dropped {
        0 => None,
        1 => Some("1 hole item dropped for want of room".to_string()),
        n => Some(format!("{n} hole items dropped for want of room")),
    };
    let parts: Vec<String> = (f.hard > 0)
        .then_some(hard)
        .into_iter()
        .chain(crowded)
        .chain(lost)
        .collect();
    if parts.is_empty() {
        "valid and complete".into()
    } else {
        parts.join(", ")
    }
}

/// Material crossings are counted by leader/feature pair and by witness line, as quality.py.
fn feature_crossings(plan: &Plan) -> usize {
    feature_crossings_where(plan, |_| true)
}

fn existing_feature_crossings(plan: &Plan) -> usize {
    feature_crossings_where(plan, |owner| !owner.starts_with("d:f:section_recess:"))
}

fn feature_crossings_where(plan: &Plan, include: impl Fn(&str) -> bool) -> usize {
    let mut leaders = BTreeSet::new();
    let mut extensions = BTreeSet::new();
    for p in &plan.problems {
        if !p.subjects.first().is_some_and(|owner| include(owner)) {
            continue;
        }
        if p.code == "leader_through_feature" {
            leaders.insert(p.subjects.clone());
        }
        if p.code == "extension_through_feature"
            && let Some(owner) = p.subjects.first()
        {
            extensions.insert((
                owner.clone(),
                p.id.rsplit(":line:").next().unwrap_or_default().to_string(),
            ));
        }
    }
    leaders.len() + extensions.len()
}

// Distinct pairs of dimension lines that cross each other.
fn dimension_line_crossings(plan: &Plan) -> usize {
    plan.problems
        .iter()
        .filter(|p| p.code == "dimension_line_crossing")
        .map(|p| &p.subjects)
        .collect::<BTreeSet<_>>()
        .len()
}

pub(crate) fn ink_findings(plan: &Plan) -> usize {
    plan.problems
        .iter()
        .filter(|p| {
            matches!(
                p.code.as_str(),
                "annotation_ink_overlap"
                    | "annotation_over_view"
                    | "annotation_line_over_view"
                    | "annotation_leader_crossing"
                    | "annotation_outside_area"
            )
        })
        .count()
}

/// How the sheet basis names each candidate's [`ink_findings`] count; [`note_final_conflicts`]
/// reads the count back by it.
const BASIS_CONFLICTS: &str = "annotation conflicts";

/// The sheet basis counts each candidate's annotation conflicts as the paper policy compared
/// them, before the selected drawing's later repairs (its final leader routes among them). When
/// the finished drawing's count differs from the one the basis states for its sheet and scale,
/// say so, so the basis and the plan's problems agree (#226).
pub(crate) fn note_final_conflicts(plan: &mut Plan) {
    let key = format!("{} {} fill ", plan.sheet.size, plan.sheet.scale);
    let Some(stated) = plan.sheet.basis.find(&key).and_then(|at| {
        let entry = &plan.sheet.basis[at..];
        let entry = &entry[..entry.find(')')?];
        let before = &entry[..entry.find(&format!(" {BASIS_CONFLICTS}"))?];
        before.rsplit([' ', '(']).next()?.parse::<usize>().ok()
    }) else {
        return;
    };
    let finished = ink_findings(plan);
    let note = "annotation conflicts were counted before its final repairs";
    if finished != stated && !plan.sheet.basis.contains(note) {
        plan.sheet.basis += &format!(
            "; the selected {} {} candidate's {note}, which take them from {stated} to {finished}",
            plan.sheet.size, plan.sheet.scale
        );
    }
}

pub(crate) fn longer_callouts(candidate: &Plan, baseline: &Plan) -> bool {
    let length = |d: &crate::dims::PlannedDimension| {
        d.placed.as_ref().map_or(0.0, |p| {
            p.lines
                .iter()
                .map(|s| (s[1][0] - s[0][0]).hypot(s[1][1] - s[0][1]))
                .sum::<f64>()
        })
    };
    candidate
        .dimensions
        .iter()
        .filter(|d| d.kind == "callout")
        .any(|d| {
            baseline
                .dimensions
                .iter()
                .find(|b| b.id == d.id && b.placed.is_some())
                .is_some_and(|b| length(d) > length(b) + 2.0 + 1e-6)
        })
}

fn rect_overlap(a: [f64; 4], b: [f64; 4]) -> bool {
    a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3]
}

/// Supported measurements already carried by the seed, including a table's replacements.
fn carried(plan: &Plan) -> BTreeSet<(String, String)> {
    let mut out: BTreeSet<(String, String)> = plan
        .dimensions
        .iter()
        .filter(|d| d.placed.is_some())
        .flat_map(|d| &d.measurements)
        .chain(
            plan.hole_table
                .iter()
                .chain(&plan.hole_tables)
                .flat_map(|t| {
                    t.rows
                        .iter()
                        .flat_map(|r| &r.measurements)
                        .chain(&t.covered)
                }),
        )
        // A placed general radius note and the radius-centre table carry radii as well.
        .chain(
            plan.notes
                .iter()
                .filter(|n| n.placed.is_some())
                .flat_map(|n| &n.measurements),
        )
        // A profile-corner angle is placed only where it is clear and otherwise dropped to a
        // lint warning: it never makes one sheet or scale less complete than another.
        .filter(|m| m.parameter != crate::dims::PROFILE_ANGLE)
        .map(|m| (m.feature.clone(), m.parameter.clone()))
        .collect();
    // Coverage follows its controlling ink through presentation changes too: a
    // coincident slot location can be owned by a hole location, then by its schedule.
    // Only drawn dimensions and fully placed tables can root that ownership chain.
    let mut live: BTreeSet<&str> = plan
        .dimensions
        .iter()
        .filter(|d| d.placed.is_some())
        .map(|d| d.id.as_str())
        .chain(
            plan.hole_table
                .iter()
                .chain(&plan.hole_tables)
                .filter(|t| !t.blocks.is_empty() && t.blocks.iter().all(|b| b.placed.is_some()))
                .map(|t| t.id.as_str()),
        )
        .collect();
    loop {
        let mut changed = false;
        for omitted in &plan.omitted {
            if let ItemState::Covered { by } = &omitted.state
                && live.contains(by.as_str())
            {
                changed |= live.insert(omitted.id.as_str());
            }
        }
        if !changed {
            break;
        }
    }
    for omitted in &plan.omitted {
        if matches!(omitted.state, ItemState::Covered { .. })
            && live.contains(omitted.id.as_str())
            && let Some((feature, parameter)) = omitted
                .id
                .strip_prefix("d:")
                .and_then(|id| id.rsplit_once('/'))
        {
            out.insert((feature.into(), parameter.into()));
        }
    }
    for r in plan.radius_table.iter().flat_map(|t| &t.rows) {
        if let Some(kind) = r
            .feature
            .strip_prefix("f:")
            .and_then(|f| f.split(':').next())
        {
            out.insert((r.feature.clone(), format!("{kind}.radius")));
        }
    }
    out
}

pub(crate) fn carries_required(required: &Plan, candidate: &Plan) -> bool {
    carried(required).is_subset(&carried(candidate))
}

/// Check actual bounds independently on each sheet; coincident coordinates on different
/// pages are expected. Keep the single-sheet gate identical.
pub(crate) fn drawn_sheets_fit(analysis: &Analysis, plan: &Plan) -> bool {
    sheets_fit(analysis, plan, true)
}

pub(crate) fn finished_sheets_fit(analysis: &Analysis, plan: &Plan) -> bool {
    sheets_fit(analysis, plan, false)
}

fn sheets_fit(analysis: &Analysis, plan: &Plan, reservations: bool) -> bool {
    if plan.sheets.is_empty() {
        return items_fit(analysis, plan, reservations);
    }
    std::iter::once(&plan.sheet)
        .chain(&plan.sheets)
        .all(|sheet| {
            let mut local = plan.clone();
            local.sheet = sheet.clone();
            local.views.retain(|v| v.sheet == sheet.number);
            let carries = |id: &str| local.views.iter().any(|v| v.id == id);
            local.dimensions.retain(|d| carries(&d.view));
            local.gdt.retain(|g| carries(&g.view));
            local.balloons.retain(|b| carries(&b.view));
            local.tables.retain(|t| t.sheet == sheet.number);
            local.hole_table = local.hole_table.filter(|t| t.sheet == sheet.number);
            local.hole_tables.retain(|t| t.sheet == sheet.number);
            if sheet.number > 1 {
                local.notes.clear();
                local.notes_block = None;
                local.radius_notes_block = None;
                local.radius_table = None;
                local.requirements.clear();
            }
            items_fit(analysis, &local, reservations)
        })
}

fn drawn_items_fit(analysis: &Analysis, p: &Plan) -> bool {
    items_fit(analysis, p, true)
}

/// Final paper candidates are judged by drawn views, not their estimated bands.
/// Intermediate packing/repair still needs the reservations to remain disjoint.
pub(crate) fn finished_items_fit(analysis: &Analysis, p: &Plan) -> bool {
    items_fit(analysis, p, false)
}

/// A rejected automatic pair still earns its explicit presentation. The trial
/// is bounded: an unfitting replay cannot start fit-fix searches.
pub(crate) fn explicit_fit_candidate(
    analysis: &Analysis,
    spec: &Spec,
) -> Result<Option<Plan>, Invalid> {
    crate::planning::explicit_pair(|| {
        crate::planning::isolated(analysis, || {
            // The automatic trial's synthetic max=1 is equivalent to the default
            // single-sheet request. Do not let it change nominal finishing order.
            let mut explicit = spec.clone();
            if crate::sheets::max_only(&explicit) {
                explicit.sheets = None;
            }
            let spec = &explicit;
            let plan = plan_automatic(analysis, spec)?;
            Ok(plan.sheets.is_empty().then_some(plan))
        })
    })
}

fn items_fit(analysis: &Analysis, p: &Plan, reservations: bool) -> bool {
    let inside = |b: [f64; 4]| {
        b[0] >= layout::margin() - 1e-6
            && b[1] >= layout::margin() - 1e-6
            && b[2] <= p.sheet.page[0] - layout::margin() + 1e-6
            && b[3] <= p.sheet.page[1] - layout::margin() + 1e-6
    };
    // Tables, notes and furniture stand against the frame; the rest keeps the frame gutter.
    let framed = crate::sheet_fit::framed_footprints(analysis, p);
    let on_frame = |b: [f64; 4]| {
        b[0] >= layout::FRAME - 1e-6
            && b[1] >= layout::FRAME - 1e-6
            && b[2] <= p.sheet.page[0] - layout::FRAME + 1e-6
            && b[3] <= p.sheet.page[1] - layout::FRAME + 1e-6
    };
    let mut title = p.sheet.title_block;
    title[3] = title_block::top(title);
    let footprints = crate::sheet_fit::footprints(analysis, p);
    // View reservations include empty annotation lanes. The finished dimensions
    // are checked separately; overlapping reservations do not mean that
    // the rendered views overlap (#295).
    let view_bounds = |i: usize| {
        if reservations {
            p.views[i].area
        } else {
            footprints[i]
        }
    };
    footprints.iter().copied().all(|b| {
        if framed.contains(&b) {
            on_frame(b)
        } else {
            inside(b)
        }
    }) && crate::sheet_fit::furniture_footprints(analysis, p)
        .into_iter()
        .all(|b| !rect_overlap(b, title))
        && (0..p.views.len()).all(|i| {
            let b = view_bounds(i);
            inside(b) && !rect_overlap(b, p.sheet.title_block)
        })
        && (0..p.views.len())
            .all(|i| (i + 1..p.views.len()).all(|j| !rect_overlap(view_bounds(i), view_bounds(j))))
}

pub(crate) fn undrawn_gdt(p: &Plan) -> usize {
    p.gdt
        .iter()
        .filter(|g| {
            g.placed.is_none()
                && !matches!(
                    g.state,
                    ItemState::OmittedByUser | ItemState::NotAuthored | ItemState::Covered { .. }
                )
        })
        .count()
}

/// Select from finished, complete drawings under the maintainer's sheet policy (2026-10-12).
/// Find the smallest fit and its largest scale; only its immediate larger sheet, with an
/// enlargement, may compete. Pins keep their canonical sheet; explicit choices override.
pub fn recover(analysis: &Analysis, spec: &Spec, first: Plan) -> Result<Plan, Invalid> {
    crate::decision_overrides::boundary(|| recover_inner(analysis, spec, first))
}

fn recover_inner(analysis: &Analysis, spec: &Spec, first: Plan) -> Result<Plan, Invalid> {
    let source_analysis = analysis;
    let bound = crate::recognition_corrections::canonical(analysis, spec)?;
    let spec = bound.as_ref();
    // The Spec's datums and controls merged into the PMI reading, once, so every later use of the
    // Analysis (and the planning memo, keyed by its address) sees them ([`crate::spec_gdt`]).
    let merged = crate::spec_gdt::effective(analysis, spec)?;
    let analysis = merged.as_ref();
    // Preview and commit often plan identical intent. All three pure inputs are
    // retained, including injected seed findings and pin geometry.
    static MEMO: crate::planning::BoundedMemo<Plan> = crate::planning::BoundedMemo::new(16);
    if spec.sheet.scale.is_some() || crate::placement_intent::has_pins(spec) {
        let mut plan = recover_uncached(analysis, spec, first)?;
        if !crate::planning::nominals_deferred() {
            finish_nominal_plan(analysis, spec, &mut plan);
        }
        crate::iso_caption::refresh(analysis, &mut plan);
        crate::hole_exits::report(source_analysis, &mut plan);
        return Ok(plan);
    }
    let analysis_key: [u8; 32] =
        crate::planning::memo(analysis, "analysis_digest", Vec::new(), || {
            // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
            Sha256::digest(serde_json::to_vec(analysis).expect("Analysis serializes")).into()
        });
    let bytes = serde_json::to_vec(&(
        analysis_key,
        spec,
        &first,
        crate::nominal::comparison(),
        UNCAPPED.get(),
    ))
    // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
    .expect("plan inputs serialize");
    let key = crate::planning::spaced_digest(&Sha256::digest(&bytes).into());
    let mut plan = if let Some(plan) = MEMO.get(&key) {
        plan
    } else {
        let plan = crate::planning::with(analysis, || recover_uncached(analysis, spec, first))?;
        MEMO.insert(key, plan.clone());
        plan
    };
    if !crate::planning::nominals_deferred() {
        finish_nominal_plan(analysis, spec, &mut plan);
    }
    crate::iso_caption::refresh(analysis, &mut plan);
    crate::hole_exits::report(source_analysis, &mut plan);
    Ok(plan)
}

/// The nearest sheet and scale that hold the views when a pinned pair does not (#13), as the
/// edit that sets them, chosen by the engine's own search: the pinned sheet at the scale the
/// recovery picks for it, else the pinned scale on the smallest sheet that holds it, else the
/// automatic pair. A pair is offered only when, pinned, its finished drawing fits. Only for a
/// spec that pins both (each search unpins one, so this never recurses).
struct FittingFix {
    ops: Vec<crate::items::Op>,
    size: String,
    scale: String,
}

fn fit_fix(analysis: &Analysis, spec: &Spec) -> Result<Option<FittingFix>, Invalid> {
    if crate::planning::bounded_pair() {
        return Ok(None);
    }
    let (Some(size), Some(scale)) = (&spec.sheet.size, &spec.sheet.scale) else {
        return Ok(None);
    };
    let unpinned = |size: bool, scale: bool| {
        let mut open = spec.clone();
        if size {
            open.sheet.size = None;
        }
        if scale {
            open.sheet.scale = None;
        }
        open
    };
    let mut tried = BTreeSet::new();
    for (open_size, open_scale) in [(false, true), (true, false), (true, true)] {
        let open = unpinned(open_size, open_scale);
        let found = if open_scale {
            recover(analysis, &open, plan_escalated_layout(analysis, &open)?)?
        } else {
            plan_escalated_layout(analysis, &open)?
        };
        let pair = (found.sheet.size.clone(), found.sheet.scale.clone());
        if (&pair.0, &pair.1) == (size, scale) || !tried.insert(pair.clone()) {
            continue;
        }
        let mut pinned = spec.clone();
        pinned.sheet.size = Some(pair.0.clone());
        pinned.sheet.scale = Some(pair.1.clone());
        let mut p = plan_escalated_layout(analysis, &pinned)?;
        let doubted = p
            .problems
            .iter()
            .any(|q| matches!(q.code.as_str(), "page_fit_uncertain" | "views_do_not_fit"));
        let fits = !doubted || drawn_items_fit(analysis, &p) || {
            crate::sheet_fit::correct_estimate(analysis, &mut p);
            compact_authored_row(analysis, &mut p);
            drawn_items_fit(analysis, &p)
        };
        if !fits {
            continue;
        }
        let mut ops = Vec::new();
        if pair.0 != *size {
            ops.push(crate::items::Op::set("sheet/size", pair.0.clone().into()));
        }
        if pair.1 != *scale {
            ops.push(crate::items::Op::set("sheet/scale", pair.1.clone().into()));
        }
        return Ok(Some(FittingFix {
            ops,
            size: pair.0,
            scale: pair.1,
        }));
    }
    Ok(None)
}

/// Spend free row space against measured ink before escalating an oblique authored quantity.
fn compact_authored_row(analysis: &Analysis, plan: &mut Plan) {
    if plan.spec.sheet.keep_layout == Some(true)
        || crate::dims::authored_placement_clear(&plan.spec, &plan.dimensions)
        || crate::placement_intent::has_pins(&plan.spec)
        || plan.spec.views.values().any(|v| v.place.is_some())
        || !plan.gdt.is_empty()
        || plan.hole_table.is_some()
        || !plan.hole_tables.is_empty()
        || plan.views.iter().any(|v| v.cut.is_some())
    {
        return;
    }
    let before = findings(plan);
    for step in 1..=plan.sheet.page[1].ceil() as usize {
        let mut trial = plan.clone();
        let dy = if plan.spec.sheet.projection.as_deref() == Some("first") {
            step as f64
        } else {
            -(step as f64)
        };
        for v in trial
            .views
            .iter_mut()
            .filter(|v| matches!(v.id.as_str(), "v:front" | "v:side"))
        {
            v.at[1] += dy;
            v.area[1] += dy;
            v.area[3] += dy;
        }
        if !drawn_items_fit(analysis, &trial) {
            continue;
        }
        crate::sheets::line_work(analysis, &trial.spec, &mut trial.views);
        let furniture = crate::centrelines::obstacles(&trial.views);
        let Ok((dims, omitted, problems)) = crate::dims::plan_with_obstacles(
            analysis,
            &trial.spec,
            &trial.sheet,
            &trial.views,
            &furniture,
            &[],
            &crate::dims::footprints::Cache::default(),
        ) else {
            continue;
        };
        trial.dimensions = dims;
        trial.omitted = omitted;
        trial.problems.retain(|p| {
            !p.code.starts_with("annotation_")
                && !matches!(
                    p.code.as_str(),
                    "leader_through_feature" | "extension_through_feature" | "dimension_text_stray"
                )
        });
        for p in problems {
            trial.problems.retain(|old| old.id != p.id);
            trial.problems.push(p);
        }
        if crate::dims::authored_placement_clear(&trial.spec, &trial.dimensions)
            && drawn_items_fit(analysis, &trial)
            && findings(&trial).hard <= before.hard
            && findings(&trial).dropped <= before.dropped
            && over_views(&trial) <= over_views(plan)
        {
            *plan = trial;
            break;
        }
    }
}

/// The first of the large sheets ([`layout::PAGES`]): A2 and larger are for parts that need
/// them at 1:1 (maintainer, 2026-10-15).
pub(crate) const LARGE_SHEET: usize = 2;

/// Whether the part's views fit A3 at 1:1 (maintainer, 2026-10-15): drawn full size, each with
/// one dimension row ([`layout::dim_pad`]) on either side, the row of views (the front with the
/// side and any sections beside it) is no wider than A3's usable paper and the column (the plan
/// above the front) no taller. Such a part exhausts single- and multi-sheet A4/A3 plans
/// before the automatic search admits A2 or larger.
pub(crate) fn fits_a3_at_full_size(analysis: &Analysis, first: &Plan) -> bool {
    let (_, w, h) = layout::PAGES[LARGE_SHEET - 1];
    let size = |v: &PlannedView| {
        // Invariant: Planned cameras come from the fixed views or checked section/detail cameras, with nonparallel axes.
        let view = View::new(v.direction, v.up).expect("planned view");
        let mut size = extent(analysis, &view).0;
        if let Some(b) = &v.broken {
            size[b.axis] -= b.reduction();
        }
        size
    };
    let views: Vec<&PlannedView> = first
        .views
        .iter()
        .filter(|v| v.id != "v:iso" && v.detail.is_none())
        .collect();
    let row: f64 = views
        .iter()
        .filter(|v| v.id != "v:plan")
        .map(|v| size(v)[0] + 2.0 * layout::dim_pad())
        .sum();
    let column: f64 = views
        .iter()
        .filter(|v| v.id == "v:plan" || v.id == "v:front")
        .map(|v| size(v)[1] + 2.0 * layout::dim_pad())
        .sum();
    row <= w - 2.0 * layout::margin() && column <= h - 2.0 * layout::margin()
}

pub(crate) fn established_key(spec: &Spec) -> Vec<u8> {
    if spec.pmi == PmiMode::First && spec.origin.is_empty() {
        serde_json::to_vec(&(
            spec.hole_table,
            &spec.sheet.projection,
            spec.sheet.references,
            crate::dims::idle_spec(spec),
        ))
        // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
        .expect("presentation inputs serialize")
    } else {
        serde_json::to_vec(&(
            spec.hole_table,
            &spec.sheet.projection,
            spec.sheet.references,
            spec.pmi,
            &spec.origin,
            crate::dims::idle_spec(spec),
        ))
        // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
        .expect("presentation inputs serialize")
    }
}

/// The established automatic presentation, shared by scale and hole-table
/// decisions. This comparison never replaces the full native final inventory.
pub(crate) fn established_drawing(analysis: &Analysis, spec: &Spec) -> Result<Plan, Invalid> {
    let key = established_key(spec);
    // A historical route template: the established normal-spacing drawing, whatever the
    // candidate's dimension spacing.
    let reference = crate::dims::with_compact(false, || {
        crate::planning::memo(
            analysis,
            "prepared-established-drawing",
            key.clone(),
            || {
                let reference = crate::lint::slots::without_recesses(analysis);
                let mut base = Spec {
                    hole_table: spec.hole_table,
                    pmi: spec.pmi,
                    origin: spec.origin.clone(),
                    ..Spec::default()
                };
                // This unrendered historical route template must not change with the
                // paper preferences. The recipient has its own ordered sheet search, just
                // as when packing annotation blocks from an explicit A0 template.
                base.sheet.preferred_max_size = Some("A0".into());
                base.sheet.projection = spec.sheet.projection.clone();
                base.sheet.references = spec.sheet.references;
                // The reference leaves out the view the drawing does, judged on the whole
                // Analysis and the spec (#222; the key holds what that reads from the spec).
                let idle = crate::dims::idle_view(analysis, spec, &crate::demand::read(analysis))
                    .map(|r| r.1);
                crate::planning::with(&reference, || {
                    // This recipe precedes the added notch bands as well as native recesses.
                    // Otherwise a displaced leader becomes its own established route, and
                    // refinement cannot detect the new model-edge or arrowhead contact.
                    // Only the comparison scope omits these annotations; the final solve
                    // retains the original source evidence and every notch requirement.
                    crate::planning::store(
                        &reference,
                        "v_notches",
                        Vec::new(),
                        Vec::<crate::lint::notch_evidence::Notch>::new(),
                    );
                    // Preserve the established presentation when reserving old routes.
                    // The actual drawing may move an occluded circular statement to its
                    // visible profile after those other routes have claimed their ink.
                    crate::planning::store(
                        &reference,
                        "canonical-circular-recipes",
                        Vec::new(),
                        true,
                    );
                    // Keep the established compact corridors as an unrendered
                    // comparison; live placements are remeasured at faithful widths.
                    crate::dims::with_idle_view(idle, || {
                        if crate::nominal::geometry_eligible(&reference) {
                            crate::nominal::with_comparison(|| plan_automatic(&reference, &base))
                        } else {
                            plan_automatic(&reference, &base)
                        }
                    })
                })
            },
        )
    });
    crate::planning::store(analysis, "established-drawing", key, reference.clone());
    if let Ok(plan) = &reference {
        crate::planning::store(analysis, "established-route-policy", Vec::new(), true);
        let clear: BTreeSet<_> = plan
            .dimensions
            .iter()
            .filter(|d| {
                d.kind == "linear"
                    && d.placed.is_some()
                    && !plan.problems.iter().any(|p| {
                        p.code == "extension_through_feature" && p.subjects.contains(&d.id)
                    })
            })
            .map(|d| d.id.clone())
            .collect();
        crate::planning::store(analysis, "established-clear-witnesses", Vec::new(), clear);
        let interior: BTreeSet<_> = plan
            .dimensions
            .iter()
            .filter(|d| d.region == Some(crate::dims::CandidateRegion::Interior))
            .map(|d| d.id.clone())
            .collect();
        crate::planning::store(
            analysis,
            "canonical-interior-statements",
            Vec::new(),
            interior,
        );
    }
    reference
}

pub(crate) fn dimension_text_edited(spec: &Spec) -> bool {
    spec.sheet.scale.is_none()
        && spec
            .dimensions
            .values()
            .filter(|d| d.tolerance.is_some() || d.text.is_some() || d.style.is_some())
            .count()
            + spec.features.values().filter(|f| f.fit.is_some()).count()
            == 1
}

fn annotation_text_edited(spec: &Spec) -> bool {
    spec.dimensions
        .values()
        .any(|d| d.tolerance.is_some() || d.text.is_some() || d.style.is_some())
        || spec.features.values().any(|f| f.fit.is_some())
}

fn complete_candidate(findings: layout::Findings, extra_view_conflicts: usize) -> bool {
    findings.clean() && extra_view_conflicts == 0
}

#[cfg(test)]
mod complete_candidate_tests {
    /// The lifted cap is #217's last resort for one search; it must never leak into a
    /// later plan on the same thread, even when that search fails.
    #[test]
    fn a_lifted_established_cap_is_restored_after_its_search() {
        use super::*;
        assert!(!UNCAPPED.get());
        let inner = with_uncapped(true, || {
            assert!(UNCAPPED.get());
            with_uncapped(false, || UNCAPPED.get())
        });
        assert!(!inner);
        assert!(!UNCAPPED.get());
        let failed = std::panic::catch_unwind(|| with_uncapped(true, || panic!("search failed")));
        assert!(failed.is_err());
        assert!(!UNCAPPED.get());
    }

    #[test]
    fn complete_sheet_never_accepts_additional_ink_over_foreign_views() {
        use super::*;
        let clear = layout::Findings::default();
        assert!(complete_candidate(clear, 0));
        assert!(!complete_candidate(clear, 1));
        assert!(!complete_candidate(
            layout::Findings {
                hard: 1,
                dropped: 0
            },
            0
        ));
        assert!(!complete_candidate(
            layout::Findings {
                hard: 0,
                dropped: 1
            },
            0
        ));
    }
}

fn recover_uncached(analysis: &Analysis, spec: &Spec, first: Plan) -> Result<Plan, Invalid> {
    // A first estimate without a table whose search under the established drawing's scale cap
    // is exhausted gets [`recover_tables`]'s last resort too: the cap has removed every scale,
    // so the ordinary ladder is searched without it and its drawing taken where it reads better
    // (STC-08 once #217's corner stations and the cited datums both reach it, its first
    // estimate placing no table). A table-free baseline (`hole_table: never`) is left to
    // [`recover_tables`], which applies the same resort itself. So does a capped drawing that
    // had to leave the established drawing's paper for larger: the established drawing the cap
    // keeps is gone, and the cap would only hold the scale on a near-empty sheet (STC-08 with
    // #217's chamfer callouts: A1 at 1:2, 19% fill, beside the established A2 at 1:2).
    let open = (!UNCAPPED.get()
        && first.hole_tables.is_empty()
        && spec.hole_table != crate::hole_table::Setting::Never)
        .then(|| first.clone());
    let plan = recover_capped(analysis, spec, first)?;
    let page = |size: &str| layout::PAGES.iter().position(|p| p.0 == size);
    let exhausted = recovery_exhausted(&plan);
    if let Some(first) = open
        && (exhausted
            || (spec.sheet.scale.is_none()
                && crate::dims::has_new_native_recess_dimensions(analysis)
                && page(&plan.sheet.size) > page(&established_drawing(analysis, spec)?.sheet.size)))
    {
        let ordinary = with_uncapped(true, || recover_capped(analysis, spec, first))?;
        let (o, c) = (
            recovery_rank(analysis, &ordinary),
            recovery_rank(analysis, &plan),
        );
        // Off the established paper the cap keeps nothing but a smaller scale, so an uncapped
        // drawing at a larger scale on no larger paper is taken unless it is harder, drops more
        // or fits worse; a couple of ink findings either way do not hold a near-empty sheet
        // (STC-08 with quiddity-rust v0.2.2's fillets: A1 1:1 with 52 ink findings against A1
        // 1:2 with 50).
        let larger = !exhausted
            && ordinary.sheet.scale_factor > plan.sheet.scale_factor
            && page(&ordinary.sheet.size) <= page(&plan.sheet.size)
            && (o.0, o.1, o.2) <= (c.0, c.1, c.2);
        let better = o < c || larger;
        if !recovery_exhausted(&ordinary) && better {
            return Ok(ordinary);
        }
    }
    Ok(plan)
}

fn recover_capped(analysis: &Analysis, spec: &Spec, first: Plan) -> Result<Plan, Invalid> {
    let mut plan = recover_callouts(analysis, spec, first)?;
    shorten_wrapped_callouts(analysis, spec, &mut plan);
    recover_recess_corridors(analysis, spec, &mut plan)?;
    Ok(plan)
}

/// Whether no standard sheet and legible scale gave *p* a valid complete drawing.
fn recovery_exhausted(p: &Plan) -> bool {
    p.problems
        .iter()
        .any(|q| q.code == "layout_recovery_exhausted")
}

/// How well a recovered drawing reads, lowest best: hard findings, drops, drawn items past
/// the paper, ink findings, feature crossings.
fn recovery_rank(analysis: &Analysis, p: &Plan) -> (usize, usize, bool, usize, usize) {
    let f = findings(p);
    (
        f.hard,
        f.dropped,
        !drawn_items_fit(analysis, p),
        ink_findings(p),
        feature_crossings(p),
    )
}

/// A schedule can make the first sheet complete while leaving a new recess
/// leader across another feature. Compare the next legible scale on that paper
/// against the complete printed drawing, including all established corridors.
fn recover_recess_corridors(
    analysis: &Analysis,
    spec: &Spec,
    plan: &mut Plan,
) -> Result<(), Invalid> {
    let blocked = |p: &Plan| {
        p.problems
            .iter()
            .filter(|q| {
                q.code == "leader_through_feature"
                    && q.subjects.first().is_some_and(|id| {
                        p.dimensions.iter().any(|d| {
                            &d.id == id
                                && d.measurements
                                    .iter()
                                    .any(|m| m.feature.starts_with("f:section_recess:"))
                        })
                    })
            })
            .count()
    };
    if crate::nominal::comparison()
        || !crate::nominal::geometry_eligible(analysis)
        || spec.sheet.scale.is_some()
        || crate::placement_intent::has_pins(spec)
        || annotation_text_edited(spec)
        || !plan.sheets.is_empty()
        || blocked(plan) == 0
    {
        return Ok(());
    }
    let floor = crate::sheets::floor(spec)
        .unwrap_or(crate::sheet_fit::ABSOLUTE_FLOOR)
        .max(crate::sheet_fit::min_geometry_scale(analysis));
    let Some(scale) = layout::SCALES
        .iter()
        .copied()
        .find(|s| *s < plan.sheet.scale_factor && *s >= floor)
    else {
        return Ok(());
    };
    let mut intent = spec.clone();
    intent.sheet.size = Some(plan.sheet.size.clone());
    intent.sheet.scale = Some(scale_name(scale));
    let mut trial = plan_escalated_with(analysis, &intent, true, None, false)?;
    if blocked(&trial) >= blocked(plan)
        || findings(&trial).hard > findings(plan).hard
        || findings(&trial).dropped > findings(plan).dropped
        || undrawn_gdt(&trial) > undrawn_gdt(plan)
        || ink_findings(&trial) > ink_findings(plan)
        || feature_crossings(&trial) > feature_crossings(plan)
        || crate::dims::extension_contacts(&trial.dimensions)
            > crate::dims::extension_contacts(&plan.dimensions)
        || !carried(plan).is_subset(&carried(&trial))
        || longer_callouts(&trial, plan)
        || !drawn_items_fit(analysis, &trial)
        || crate::sheet_fit::illegible_steps(analysis, &trial) > 0
        || !crate::dims::labels_on_spans(&trial.dimensions)
        || trial
            .dimensions
            .iter()
            .flat_map(|d| &d.measurements)
            .filter(|m| m.parameter == "channel_width.length")
            .any(|m| m.value * scale < crate::sheet_fit::MIN_OPENING)
    {
        return Ok(());
    }
    trial.spec.clone_from(&plan.spec);
    trial.sheet.default = spec.sheet.size.is_none();
    trial.sheet.basis = format!(
        "automatic reduction on {} to {} clears new recess leaders while retaining the complete drawing and established corridors; drawn-item fill {:.1}%",
        trial.sheet.size,
        trial.sheet.scale,
        trial.sheet.fill * 100.0
    );
    *plan = trial;
    Ok(())
}

/// Schedules and retained recipes settle before this last bounded route polish.
/// Freeze every other annotation and accept only a shorter, no-worse drawing.
fn shorten_wrapped_callouts(analysis: &Analysis, spec: &Spec, plan: &mut Plan) {
    let wrapped = |d: &crate::dims::PlannedDimension| {
        d.kind == "callout" && d.placed.is_some() && d.terms.iter().any(|t| t == "\u{2028}")
    };
    if crate::planning::lookup::<bool>(analysis, "canonical-circular-recipes", &[]).unwrap_or(false)
        || !plan.sheets.is_empty()
        || !spec.dimensions.is_empty()
        || !spec.views.is_empty()
        || spec.sheet.scale.is_some()
        || !plan.dimensions.iter().any(wrapped)
    {
        return;
    }
    let mut intent = spec.clone();
    for d in plan.dimensions.iter().filter(|d| !wrapped(d)) {
        intent.dimensions.entry(d.id.clone()).or_default().place =
            Some(crate::placement_intent::Place {
                pinned: true,
                ..Default::default()
            });
    }
    let mut fixed: Vec<_> = plan
        .gdt
        .iter()
        .filter_map(crate::dims::gdt::proxy)
        .collect();
    fixed.extend(crate::centrelines::obstacles(&plan.views));
    fixed.extend(
        plan.notes_block
            .iter()
            .chain(&plan.radius_notes_block)
            .map(crate::notes::proxy),
    );
    fixed.extend(plan.tables.iter().filter_map(crate::tables::proxy));
    fixed.extend(
        plan.radius_table
            .iter()
            .filter_map(|t| crate::tables::proxy(&t.table)),
    );
    fixed.extend(
        plan.hole_table
            .iter()
            .chain(&plan.hole_tables)
            .flat_map(|t| &t.blocks)
            .filter_map(crate::tables::proxy),
    );
    fixed.extend(plan.captions.iter().map(crate::iso_caption::proxy));
    fixed.extend(
        plan.requirements
            .iter()
            .filter_map(crate::dims::gdt::finish_proxy),
    );
    let mut candidate = plan.clone();
    let cache = crate::dims::footprints::Cache::default();
    crate::planning::store(analysis, "wrapped-route-polish", Vec::new(), true);
    let reports = crate::dims::refine_inventory(
        analysis,
        &intent,
        &candidate.sheet,
        &candidate.views,
        &mut candidate.dimensions,
        &fixed,
        &cache,
    );
    crate::planning::store(analysis, "wrapped-route-polish", Vec::new(), false);
    let ids: BTreeSet<_> = candidate.dimensions.iter().map(|d| d.id.clone()).collect();
    candidate.problems.retain(|p| {
        !(p.subjects.iter().any(|id| ids.contains(id))
            && (p.code.starts_with("annotation_")
                || matches!(
                    p.code.as_str(),
                    "leader_through_feature"
                        | "extension_through_feature"
                        | "dimension_line_shared"
                        | "dimension_text_stray"
                )))
    });
    candidate.problems.extend(reports.into_values().flatten());
    candidate
        .problems
        .extend(crate::dims::recheck_cached(analysis, &candidate, &cache));
    let length = |d: &crate::dims::PlannedDimension| {
        d.placed.as_ref().map_or(0.0, |p| {
            p.lines
                .iter()
                .map(|s| (s[1][0] - s[0][0]).hypot(s[1][1] - s[0][1]))
                .sum::<f64>()
        })
    };
    let mut shorter = false;
    for d in candidate.dimensions.iter().filter(|d| wrapped(d)) {
        let Some(old) = plan.dimensions.iter().find(|old| old.id == d.id) else {
            return;
        };
        if length(d) > length(old) + 1e-6 {
            return;
        }
        // Balloons are installed after the original solve. A conservative full
        // ink box protects their rings, labels and leaders during this optional
        // polish without treating their old overlap as permission for a new one.
        if let Some(ink) = crate::dims::ink_box(d)
            && plan.balloons.iter().any(|b| {
                let mut bounds = [
                    b.at[0] - b.radius,
                    b.at[1] - b.radius,
                    b.at[0] + b.radius,
                    b.at[1] + b.radius,
                ];
                for p in b.leader.iter().flatten() {
                    bounds = [
                        bounds[0].min(p[0]),
                        bounds[1].min(p[1]),
                        bounds[2].max(p[0]),
                        bounds[3].max(p[1]),
                    ];
                }
                if let Some(label) = b.label_box {
                    bounds = [
                        bounds[0].min(label[0]),
                        bounds[1].min(label[1]),
                        bounds[2].max(label[2]),
                        bounds[3].max(label[3]),
                    ];
                }
                rect_overlap(ink, bounds)
            })
        {
            return;
        }
        shorter |= length(d) < length(old) - 1e-6;
    }
    if !shorter
        || findings(&candidate).hard > findings(plan).hard
        || ink_findings(&candidate) > ink_findings(plan)
        || feature_crossings(&candidate) > feature_crossings(plan)
        || !carried(plan).is_subset(&carried(&candidate))
        || !drawn_sheets_fit(analysis, &candidate)
    {
        return;
    }
    for d in candidate.dimensions.iter_mut().filter(|d| wrapped(d)) {
        if let Some(old) = plan.dimensions.iter().find(|old| old.id == d.id)
            && length(d) < length(old) - 1e-6
        {
            d.basis += "; final wrapped-callout polish shortens the leader after schedules and retained annotations settle";
        }
        if let (Some(p), Some(v)) = (&d.placed, candidate.views.iter().find(|v| v.id == d.view))
            && let Some(line) = p.lines.first()
        {
            candidate.callout_tips.insert(
                d.id.clone(),
                (d.view.clone(), [line[0][0] - v.at[0], line[0][1] - v.at[1]]),
            );
        }
    }
    candidate.sheet.fill = crate::sheet_fit::fill(analysis, &candidate);
    if let Some((before, tail)) = candidate.sheet.basis.split_once("; drawn-item fill ")
        && let Some((_, after)) = tail.split_once('%')
    {
        candidate.sheet.basis = format!(
            "{before}; drawn-item fill {:.1}%{after}",
            candidate.sheet.fill * 100.0
        );
    }
    candidate.status = crate::completeness::status(&candidate.problems);
    *plan = candidate;
}

fn recover_callouts(analysis: &Analysis, spec: &Spec, first: Plan) -> Result<Plan, Invalid> {
    let mut best = recover_paper(analysis, spec, first)?;
    if crate::placement_intent::has_pins(spec) {
        return Ok(best);
    }
    let clean =
        |p: &Plan| findings(p).clean() && drawn_sheets_fit(analysis, p) && undrawn_gdt(p) == 0;
    if best.sheet.size == "A4" && clean(&best) {
        return Ok(best);
    }
    let m = crate::pmi_lowering::manufacturing_for(analysis, spec);
    if spec.sheet.size.is_none()
        // A PMI frame remains an authored annotation after its engine dimensions
        // become covered. Compact manufacturing text alone cannot justify shrinking it.
        && (!crate::dims::pmi_policy::carries(analysis) || best.gdt.is_empty())
        && m.owned
            .iter()
            .any(|o| crate::callouts::style(spec, &o.owner) == crate::callouts::Style::Auto)
    {
        // Compact manufacturing text must not cause a small turned part to take a
        // larger paper merely to enlarge it. Keep a complete, legible A4 candidate.
        let mut small = spec.clone();
        small.sheet.size = Some("A4".into());
        let seed = plan_escalated_layout(analysis, &small)?;
        let mut candidate = recover_paper(analysis, &small, seed)?;
        if candidate.sheet.size == "A4"
            && clean(&candidate)
            && crate::sheet_fit::illegible_steps(analysis, &candidate) == 0
            && carried(&best).is_subset(&carried(&candidate))
        {
            candidate.sheet.basis += "; auto compact manufacturing callouts fit cleanly on A4; retain the smaller paper before enlargement";
            candidate.spec = spec.clone();
            candidate.sheet.default = spec.sheet.size.is_none();
            return Ok(candidate);
        }
    }
    // Try one callout at a time on the chosen paper and scale before allowing it to demand
    // larger paper. Other feature overrides stay authoritative. A requirement several owners
    // share (a thread on several bores) is stated by one counted callout: its owners take the
    // note form together, in one trial, not one full paper search per owner.
    let mut intent = spec.clone();
    let mut callouts: Vec<(String, Vec<&str>, Vec<&str>)> = Vec::new();
    for o in &m.owned {
        if crate::callouts::style(spec, &o.owner) != crate::callouts::Style::Auto {
            continue;
        }
        let Some(d) = best
            .dimensions
            .iter()
            .find(|d| d.measurements.iter().any(|v| v.feature == o.id))
        else {
            continue;
        };
        // A one-word callout has no note form to gain, and a callout the drawing does not draw
        // (a hole table states it) cannot fit the sheet any better in another form.
        if d.label.split_whitespace().count() <= 1 || d.placed.is_none() {
            continue;
        }
        match callouts.iter_mut().find(|(id, ..)| *id == d.id) {
            Some((_, owners, ids)) => {
                owners.push(&o.owner);
                ids.push(&o.id);
            }
            None => callouts.push((d.id.clone(), vec![&o.owner], vec![&o.id])),
        }
    }
    for (_, owners, ids) in &callouts {
        let mut trial = intent.clone();
        for owner in owners {
            trial
                .features
                .entry((*owner).to_string())
                .or_default()
                .callouts = Some(crate::callouts::Settings {
                style: crate::callouts::Style::Note,
            });
        }
        trial.sheet.size = Some(spec.sheet.size.clone().unwrap_or_else(|| "A4".into()));
        trial.sheet.scale = Some(best.sheet.scale.clone());
        let seed = plan_escalated_layout(analysis, &trial)?;
        let mut candidate = recover_paper(analysis, &trial, seed)?;
        if clean(&candidate)
            && carried(&best).is_subset(&carried(&candidate))
            && (candidate.sheet.page[0] * candidate.sheet.page[1]
                < best.sheet.page[0] * best.sheet.page[1]
                || !clean(&best))
        {
            for d in &mut candidate.dimensions {
                if d.measurements
                    .iter()
                    .any(|v| ids.contains(&v.feature.as_str()))
                {
                    d.basis = d
                        .basis
                        .replace("callouts/style Note", "callouts/style Auto")
                        .replace("note form requested;", "auto note form selected for fit;");
                    d.basis += "; auto selected note form to fit the chosen paper and scale cleanly before paper escalation";
                }
            }
            intent = trial;
            candidate.spec = spec.clone();
            candidate.sheet.default = spec.sheet.size.is_none();
            best = candidate;
            if best.sheet.size == "A4" {
                break;
            }
        }
    }
    Ok(best)
}

fn recover_paper(analysis: &Analysis, spec: &Spec, first: Plan) -> Result<Plan, Invalid> {
    if spec.sheet.scale.is_some() || crate::placement_intent::has_pins(spec) {
        return recover_candidates(analysis, spec, first, true, false);
    }
    // Optional details are a last resort for unreadable features or smaller paper.
    // Search the complete ordinary drawing first, including the small-sheet allocation.
    let minimum = crate::sheets::floor(spec)
        .unwrap_or(0.0)
        .max(crate::sheet_fit::min_legible_scale(analysis));
    let has_optional = first
        .views
        .iter()
        .any(|v| v.detail.as_ref().is_some_and(|d| !d.authored))
        || layout::SCALES
            .into_iter()
            .filter(|s| *s >= minimum)
            .any(|scale| {
                crate::detail::requests(analysis, spec, scale)
                    .iter()
                    .any(|(_, d)| !d.authored)
            });
    if has_optional {
        // Both passes use the same Spec. Omit only the requests present at a candidate's
        // scale, so pairs with no optional crop share their expensive planning memo.
        // The original seed supplies requirements; suppressed geometry is never reused.
        let required = carried(&first);
        let mut ordinary = recover_candidates(analysis, spec, first.clone(), false, false)?;
        let complete = !ordinary
            .views
            .iter()
            .any(|v| v.detail.as_ref().is_some_and(|d| !d.authored))
            && findings(&ordinary).clean()
            && crate::sheet_fit::illegible_steps(analysis, &ordinary) == 0
            && undrawn_gdt(&ordinary) == 0
            && drawn_sheets_fit(analysis, &ordinary)
            && required.is_subset(&carried(&ordinary));
        let small_paper = ordinary.sheet.size == "A4" || ordinary.sheet.size == "A3";
        if complete
            && (small_paper
                || spec.sheet.size.is_some()
                || !fits_a3_at_full_size(analysis, &ordinary))
        {
            ordinary.spec = spec.clone();
            return Ok(ordinary);
        }
        let mut detailed = recover_candidates(analysis, spec, first, true, true)?;
        let detailed_complete = findings(&detailed).clean()
            && crate::sheet_fit::illegible_steps(analysis, &detailed) == 0
            && undrawn_gdt(&detailed) == 0
            && drawn_sheets_fit(analysis, &detailed)
            && required.is_subset(&carried(&detailed));
        // A refused detail search may return the invalid seed. A complete ordinary
        // drawing always wins over that fallback, irrespective of paper size.
        let paper = |p: &Plan| {
            layout::PAGES
                .iter()
                .position(|(name, ..)| *name == p.sheet.size)
        };
        if complete && (!detailed_complete || paper(&ordinary) <= paper(&detailed)) {
            ordinary.spec = spec.clone();
            return Ok(ordinary);
        }
        // Main's measured-corridor fallback can be physically fitting yet still
        // incomplete. A refused optional detail must not replace that retained
        // drawing with an off-sheet seed. Keep every seed measurement and every
        // remaining finding; this is recovery, never a claim of completeness.
        let ordinary_findings = findings(&ordinary);
        let detailed_findings = findings(&detailed);
        if !detailed_complete
            && drawn_sheets_fit(analysis, &ordinary)
            && crate::sheet_fit::illegible_steps(analysis, &ordinary) == 0
            && required.is_subset(&carried(&ordinary))
            && undrawn_gdt(&ordinary) <= undrawn_gdt(&detailed)
            && ordinary_findings.hard < detailed_findings.hard
            && ordinary_findings.dropped <= detailed_findings.dropped
        {
            ordinary.spec = spec.clone();
            return Ok(ordinary);
        }
        // A refused optional search keeps the seed. Keep the ordinary search's
        // evidence too: its real candidates must not disappear from the diagnostic.
        if !complete
            && !detailed_complete
            && let Some(ordinary) = ordinary
                .problems
                .iter()
                .find(|p| p.code == "layout_recovery_exhausted")
            && let Some(refused) = detailed
                .problems
                .iter_mut()
                .find(|p| p.code == "layout_recovery_exhausted")
            && refused.message != ordinary.message
        {
            let alternatives = refused
                .message
                .split_once(" Tried ")
                .map_or(refused.message.as_str(), |(_, tried)| tried);
            refused.message = format!(
                "{} Automatic detail alternatives: {alternatives}",
                ordinary.message
            );
        }
        return Ok(detailed);
    }
    recover_candidates(analysis, spec, first, true, false)
}

fn recover_candidates(
    analysis: &Analysis,
    spec: &Spec,
    mut first: Plan,
    allow_automatic: bool,
    require_automatic: bool,
) -> Result<Plan, Invalid> {
    use crate::sheet_fit::FILL_BAND;
    // Adding a recess must not enlarge all existing witnesses and leaders merely
    // to fill the page. Use the established drawing's scale as an automatic cap;
    // the finished solve still includes every new recess and may spend more paper.
    let established_plan = if spec.sheet.scale.is_none()
        && crate::dims::has_new_native_recess_dimensions(analysis)
        && first.dimensions.iter().any(|d| {
            d.measurements
                .iter()
                .any(|m| m.feature.starts_with("f:section_recess:"))
        }) {
        Some(established_drawing(analysis, spec)?)
    } else {
        None
    };
    let established = established_plan.as_ref().map(|reference| {
        (
            reference.sheet.scale_factor,
            reference
                .views
                .iter()
                .find(|v| v.id == "v:iso")
                .map(|v| v.scale),
            crate::dims::extension_contacts(&reference.dimensions),
            existing_feature_crossings(reference),
        )
    });
    let established_scale = established.map(|e| e.0);
    let orientation_cap = established.and_then(|e| e.1);
    let corridor_budget = established.map(|e| e.2);
    let existing_crossing_budget = established.map(|e| e.3);
    let extra_existing_crossings = |p: &Plan| {
        existing_crossing_budget.map_or(0, |budget| {
            existing_feature_crossings(p).saturating_sub(budget)
        })
    };
    let extra_corridors = |p: &Plan| {
        corridor_budget.map_or(0, |budget| {
            crate::dims::extension_contacts(&p.dimensions).saturating_sub(budget)
        })
    };
    let min_legible_scale = crate::sheets::floor(spec).map_or_else(
        || crate::sheet_fit::min_legible_scale(analysis),
        |floor| floor.max(crate::sheet_fit::min_legible_scale(analysis)),
    );
    // A comparison below the live readability floor cannot cap the search:
    // doing so removes every scale, rather than preserving an old corridor.
    // The existing crossing and leader budgets still constrain each candidate.
    let compare_diagram_fill = min_legible_scale + 1e-9 < first.sheet.scale_factor;
    let established_scale = established_scale.filter(|cap| {
        !UNCAPPED.get()
            && *cap >= min_legible_scale
            && established_plan
                .as_ref()
                .is_none_or(|p| findings(p).clean())
    });
    first.sheet.fill = crate::sheet_fit::fill(analysis, &first);
    if spec.sheet.scale.is_some()
        || crate::placement_intent::has_pins(spec)
        || dimension_text_edited(spec)
    {
        // A pinned sheet and scale are judged as the automatic search judges its candidates
        // (#13): the packed estimate's doubt is withdrawn when the finished drawing's items and
        // views all fit the sheet, so the pair the engine would choose itself raises nothing.
        // When they really do not fit, the problem carries the nearest pair that does.
        if let Some(i) = first
            .problems
            .iter()
            .position(|q| q.code == "page_fit_uncertain")
        {
            // The drawing as planned first; failing that, after the measured correction the
            // automatic search gives its candidates (so the pinned pair is drawn as the search
            // would draw it; a no-op for pinned placements).
            let mut corrected = first.clone();
            if !drawn_items_fit(analysis, &corrected) {
                crate::sheet_fit::correct_estimate(analysis, &mut corrected);
                compact_authored_row(analysis, &mut corrected);
            }
            // Reserved view bands can overlap or exceed the frame after the actual
            // ink has settled. They are packing estimates, not rendered overflow.
            // As in automatic trials, include captions, line work and furniture,
            // and reject actual view/view or view/title-block overlaps.
            if finished_items_fit(analysis, &corrected) {
                first = corrected;
                first.problems.remove(i);
                first.sheet.fill = crate::sheet_fit::fill(analysis, &first);
                if let Some((basis, _)) = first.sheet.basis.rsplit_once("; drawn-item fill ") {
                    first.sheet.basis =
                        format!("{basis}; drawn-item fill {:.1}%", first.sheet.fill * 100.0);
                }
                first.sheet.basis = first.sheet.basis.replacen(
                    "; the views may not fit",
                    "; the finished drawing fits",
                    1,
                );
                first.status = crate::completeness::status(&first.problems);
            } else if let Some(fix) = fit_fix(analysis, spec)? {
                let p = &mut first.problems[i];
                p.message = format!(
                    "{}; {} at {} fits.",
                    p.message.trim_end_matches('.'),
                    fix.size,
                    fix.scale
                );
                p.fix = fix.ops.clone();
                p.fixes = vec![crate::fixes::Fix::from_ops(fix.ops)];
            }
        }
        return Ok(first);
    }
    if !first.hole_tables.is_empty() {
        if let Some(reference) = &established_plan
            && established_scale.is_some_and(|cap| first.sheet.scale_factor <= cap)
            && findings(&first).clean()
            && undrawn_gdt(&first) == 0
            && drawn_items_fit(analysis, &first)
            && carried(reference).is_subset(&carried(&first))
            && extra_corridors(&first) == 0
            && extra_existing_crossings(&first) == 0
            && ink_findings(&first) <= ink_findings(reference)
            && over_views(&first) <= over_views(reference)
        {
            // The settled table already retains the established information and
            // has no additional annotation defects. Re-expanding its rows into
            // inline ladders merely to search for another scale can displace
            // old items and repeats the dense corridor solve unnecessarily.
            return Ok(first);
        }
        return recover_tables(analysis, spec, first);
    }
    let suppressed_detail = first.views.iter().any(|v| {
        v.detail.as_ref().is_some_and(|d| !d.authored)
            && (!allow_automatic
                || spec
                    .views
                    .get(v.id.trim_start_matches("v:"))
                    .is_some_and(|v| v.omit))
    });
    let seed_clean = findings(&first).clean()
        && crate::sheet_fit::illegible_steps(analysis, &first) == 0
        && undrawn_gdt(&first) == 0
        && drawn_items_fit(analysis, &first);
    let authored_reference = (!crate::nominal::comparison())
        .then(|| crate::dims::authored_placement_conflicts(spec, &first.dimensions));
    // A2 and larger only for a part whose views do not fit A3 at 1:1; asked at most once, and
    // only when a large sheet is in play (a named sheet is the user's choice).
    let mut small_part: Option<bool> = None;
    let is_small = |small: &mut Option<bool>| -> Result<bool, Invalid> {
        if spec.sheet.size.is_some() {
            return Ok(false);
        }
        Ok(*small.get_or_insert_with(|| fits_a3_at_full_size(analysis, &first)))
    };
    let first_large = layout::PAGES
        .iter()
        .position(|(n, _, _)| *n == first.sheet.size)
        .is_some_and(|i| i >= LARGE_SHEET);
    // A large first estimate of a small part is no sheet to keep: it vetoes nothing.
    let mut seed_kept = seed_clean
        && !suppressed_detail
        && !(first_large && is_small(&mut small_part)?)
        && established_scale.is_none_or(|s| first.sheet.scale_factor <= s)
        && extra_corridors(&first) == 0
        && extra_existing_crossings(&first) == 0;
    let required = carried(&first);
    let mut tried = Vec::new();
    // Basis traces name hard codes. Retained recovery diagnoses keep their
    // established wording, rather than changing merely because a search ran.
    let mut diagnostic_assessments = Vec::new();
    let mut candidates = Vec::new();
    let mut fitting_incomplete: Option<(usize, usize, usize, Plan)> = None;
    let mut smallest = None;
    let mut smallest_iso: Option<f64> = None;
    let mut recipes = BTreeMap::new();
    // Exhaust practical single sheets, then R5 allocation, before admitting large paper.
    let mut rule = true;
    let mut first_sheet = 0;
    loop {
        let mut cut = false;
        for (i, (name, _, _)) in layout::PAGES.iter().enumerate().skip(first_sheet) {
            if spec.sheet.size.as_ref().is_some_and(|n| n != name) {
                continue;
            }
            if smallest.is_some_and(|(j, _)| i > j + 1) {
                break;
            }
            // Practical paper already carries a complete, readable drawing. Large
            // sheets relieve a fit/legibility failure, not a low-fill enlargement.
            if i >= LARGE_SHEET && smallest.is_some_and(|(j, _)| j < LARGE_SHEET) {
                tried.push(format!(
                    "{name} and larger (a complete legible drawing already fits practical paper)"
                ));
                break;
            }
            if i >= LARGE_SHEET && rule && is_small(&mut small_part)? {
                tried.push(format!(
                    "{name} and larger (the views fit A3 at 1:1; large sheets are for larger parts)"
                ));
                cut = true;
                break;
            }
            for scale in layout::SCALES.into_iter().filter(|s| {
                *s >= min_legible_scale && established_scale.is_none_or(|cap| *s <= cap)
            }) {
                // On the one larger sheet only an enlargement of the smallest fit is allowed.
                if smallest.is_some_and(|fit| !crate::sheet_fit::allowed(fit, i, scale)) {
                    continue;
                }
                // A view larger than the usable paper cannot be repaired by annotation placement.
                // Reject it before running the finished solver (especially important on dense PMI).
                let (_, w, h) = layout::PAGES[i];
                if first
                    .views
                    .iter()
                    .filter(|v| v.id != "v:iso" && v.detail.is_none())
                    .any(|v| {
                        // Invariant: Planned cameras come from the fixed views or checked section/detail cameras, with nonparallel axes.
                        let view = View::new(v.direction, v.up).expect("planned view");
                        let (mut size, _) = extent(analysis, &view);
                        if let Some(b) = &v.broken {
                            size[b.axis] -= b.reduction();
                        }
                        size[0] * scale > w - 2.0 * layout::margin()
                            || size[1] * scale > h - 2.0 * layout::margin()
                    })
                {
                    tried.push(format!(
                        "{name} {} (principal ink exceeds usable paper; not a fit candidate)",
                        scale_name(scale)
                    ));
                    continue;
                }
                let mut named = spec.clone();
                named.sheet.size = Some((*name).into());
                named.sheet.scale = Some(scale_name(scale));
                // Decide omissions before the expensive candidate/arrangement planning.
                if !allow_automatic
                    || (layout::PAGES
                        .iter()
                        .position(|(n, ..)| *n == first.sheet.size)
                        .is_some_and(|parent| i > parent)
                        && crate::sheets::floor(spec).is_none())
                {
                    for (name, detail) in crate::detail::requests(analysis, &named, scale) {
                        if !detail.authored {
                            named.views.entry(name).or_default().omit = true;
                        }
                    }
                }
                let mut p = if first.sheet.size == *name
                    && first.sheet.scale_factor == scale
                    && seed_clean
                    && extra_corridors(&first) == 0
                    && !suppressed_detail
                    && (!require_automatic
                        || first
                            .views
                            .iter()
                            .any(|v| v.detail.as_ref().is_some_and(|d| !d.authored)))
                {
                    first.clone()
                } else {
                    match plan_escalated_with(analysis, &named, true, None, require_automatic) {
                        Ok(p) => p,
                        Err(e) if require_automatic && e.message == NO_AUTOMATIC_DETAIL => continue,
                        Err(e)
                            if spec.iso_scale_factor.is_some()
                                && e.message == INFEASIBLE_ISO_SCALE =>
                        {
                            tried.push(format!("{name} {} ({})", scale_name(scale), e.message));
                            continue;
                        }
                        Err(e) => return Err(e),
                    }
                };
                // A failed seed must not restrict the complete drawing to its first arrangement.
                // Try the other established arrangement, with no new leader-routing strategy.
                if !seed_clean
                    || (established_scale.is_some()
                        && (!findings(&p).clean()
                            || extra_corridors(&p) > 0
                            || extra_existing_crossings(&p) > 0))
                {
                    let quality = |p: &Plan| {
                        let f = findings(p);
                        (
                            f.hard,
                            f.dropped,
                            over_views(p),
                            extra_corridors(p),
                            extra_existing_crossings(p),
                            ink_findings(p),
                            feature_crossings(p),
                        )
                    };
                    for arrangement in [
                        layout::Arrangement::Columns,
                        layout::Arrangement::StackedIso,
                        layout::Arrangement::StaggeredSide,
                    ] {
                        if p.sheet.arrangement == arrangement.name() {
                            continue;
                        }
                        let alternative = match plan_escalated_with(
                            analysis,
                            &named,
                            true,
                            Some(arrangement),
                            require_automatic,
                        ) {
                            Ok(p) => p,
                            Err(e) if require_automatic && e.message == NO_AUTOMATIC_DETAIL => {
                                continue;
                            }
                            Err(e)
                                if spec.iso_scale_factor.is_some()
                                    && e.message == INFEASIBLE_ISO_SCALE =>
                            {
                                continue;
                            }
                            Err(e) => return Err(e),
                        };
                        if quality(&alternative) < quality(&p) {
                            p = alternative;
                        }
                    }
                }
                // A new recess witness can strand an existing datum ladder even
                // without GD&T. Compare complete movable annotation blocks
                // before rejecting otherwise usable paper.
                if !p.gdt.is_empty()
                    || (findings(&p).hard > 0
                        && crate::dims::has_new_native_recess_dimensions(analysis)
                        && p.problems.iter().any(|q| q.code == "dimension_line_shared")
                        && p.notes_block.is_none()
                        && p.radius_notes_block.is_none()
                        && p.radius_table.is_none()
                        && p.hole_table.is_none()
                        && p.hole_tables.is_empty())
                {
                    let mut roomy = named.clone();
                    roomy.sheet.size = Some("A0".into());
                    // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
                    let recipe_key = serde_json::to_vec(&roomy).expect("recipe Spec serializes");
                    let recipe = recipes.entry(recipe_key).or_insert_with(|| {
                        plan_escalated_with(analysis, &roomy, true, None, false)
                            .ok()
                            .filter(|q| findings(q).clean())
                    });
                    if let Some(recipe) = recipe
                        && let Some(packed) = crate::sheet_fit::pack_gdt(analysis, recipe, &p)
                        && carried(&p).is_subset(&carried(&packed))
                        && (
                            findings(&packed).hard,
                            findings(&packed).dropped,
                            over_views(&packed),
                            feature_crossings(&packed),
                            ink_findings(&packed),
                        ) < (
                            findings(&p).hard,
                            findings(&p).dropped,
                            over_views(&p),
                            feature_crossings(&p),
                            ink_findings(&p),
                        )
                    {
                        p = packed;
                    }
                }
                // Enlargement is for the dimensioned views. On the smallest fit's larger neighbour
                // the orientation view keeps the smallest fit's paper scale, rather than spending the
                // extra sheet on decorative growth.
                if spec.iso_scale_factor.is_none()
                    && !spec.views.contains_key("iso")
                    && let Some((fit, Some(cap))) = smallest.map(|(j, _)| (j, smallest_iso))
                    && i > fit
                    && let Some(iso) = p.views.iter_mut().find(|v| v.id == "v:iso")
                    && iso.scale > cap
                {
                    // Shrink about the drawn box's centre, so it stays inside its old footprint.
                    let b = analysis.drawing_iso_bounds();
                    let centre = [(b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0];
                    iso.at = [
                        iso.at[0] + (iso.scale - cap) * centre[0],
                        iso.at[1] + (iso.scale - cap) * centre[1],
                    ];
                    iso.scale = cap;
                    iso.basis += "; the smallest fit's orientation scale kept while dimensioned views enlarge";
                }
                crate::sheet_fit::correct_estimate(analysis, &mut p);
                compact_authored_row(analysis, &mut p);
                if spec.iso_scale_factor.is_none()
                    && !spec.views.contains_key("iso")
                    && let (Some(cap), Some(iso)) = (
                        orientation_cap,
                        p.views.iter_mut().find(|v| v.id == "v:iso"),
                    )
                    && iso.scale > cap
                {
                    let b = analysis.drawing_iso_bounds();
                    for k in 0..2 {
                        iso.at[k] += (iso.scale - cap) * 0.5 * (b[k] + b[k + 2]);
                    }
                    iso.scale = cap;
                    iso.basis += "; established orientation scale retained beside the new recess annotations";
                }
                crate::iso_caption::refresh(analysis, &mut p);
                // An estimate's iso budget can refuse a sheet whose finished drawing fits.
                // Only discharge that estimate when all actual item bounds and view extents fit.
                // Historical route comparisons are unrendered, pre-polish
                // templates. Keep their conservative reservations; only live
                // finished candidates can discharge an estimate from drawn bounds.
                let actual_fit = if crate::nominal::comparison() {
                    drawn_items_fit(analysis, &p)
                } else {
                    finished_items_fit(analysis, &p)
                        // Native allocation defers finishing to its enclosing paper
                        // trials, which judge the completed drawing. Direct recovery
                        // must check its final centre ink before publishing a choice.
                        && (crate::planning::nominals_deferred() || layout::with_gutter(
                            spec.sheet.frame_gutter.unwrap_or(layout::FRAME_GUTTER),
                            || {
                                if line_work_problems(&p.sheet, &p.views).is_empty() {
                                    return true;
                                }
                                // Packing can move centre ink outside its initial bounds.
                                // Precision polish may move it back inside: check that
                                // finished copy before rejecting otherwise fitting paper.
                                let mut finished = p.clone();
                                finished.spec.clone_from(&first.spec);
                                finish_nominal_plan(analysis, spec, &mut finished);
                                line_work_problems(&finished.sheet, &finished.views).is_empty()
                            },
                        ))
                };
                if actual_fit {
                    p.problems.retain(|q| q.code != "page_fit_uncertain");
                }
                p.status = crate::completeness::status(&p.problems);
                let mut f = findings(&p);
                f.hard += usize::from(!actual_fit);
                // Releasing empty reservations must not add oblique authored
                // stroke conflicts. Inherited contacts retain their existing
                // finishing path, just as the other crossing budgets do.
                let authored_conflicts = authored_reference.as_ref().map_or(0, |reference| {
                    crate::dims::authored_placement_conflicts(spec, &p.dimensions)
                        .difference(reference)
                        .count()
                });
                f.hard += authored_conflicts;
                let unreadable_steps = crate::sheet_fit::illegible_steps(analysis, &p);
                f.hard += unreadable_steps;
                f.dropped += required.difference(&carried(&p)).count();
                f.dropped += undrawn_gdt(&p);
                let over = over_views(&p).saturating_sub(over_views(&first));
                let crossings = feature_crossings(&p).saturating_sub(feature_crossings(&first));
                // A crossed dimension line reads worse than a line through material: an
                // enlargement that adds one is held to the same ratchet (#163).
                let line_crossings =
                    dimension_line_crossings(&p).saturating_sub(dimension_line_crossings(&first));
                p.sheet.fill = crate::sheet_fit::fill(analysis, &p);
                // A declined optional crop leaves an ordinary drawing. Its refusal must
                // not veto the small-sheet candidates that F1 would otherwise accept.
                // An invalid seed's refused crop cannot block a complete ordinary drawing;
                // main's material-crossing gate already protects valid first estimates.
                let detail_context = p.views.iter().any(|v| v.detail.is_some());
                let quality_veto = crate::sheets::floor(spec).is_none()
                    && detail_context
                    && (ink_findings(&p) > ink_findings(&first)
                        || feature_crossings(&p) > feature_crossings(&first)
                        || (p.views.iter().any(|v| v.detail.is_some())
                            && longer_callouts(&p, &first)));
                let mut hard_codes = hard_layout_codes(&p);
                if !actual_fit {
                    hard_codes.insert("finished_items_do_not_fit", 1);
                }
                if authored_conflicts > 0 {
                    hard_codes.insert("authored_annotation_conflict", authored_conflicts);
                }
                if unreadable_steps > 0 {
                    hard_codes.insert("unreadable_step_span", unreadable_steps);
                }
                let plain_verdict = if quality_veto {
                    format!(
                        "detail quality veto; {}",
                        said(f, drops(&p), BTreeMap::new())
                    )
                } else {
                    said(f, drops(&p), BTreeMap::new())
                };
                let verdict = if quality_veto {
                    format!("detail quality veto; {}", said(f, drops(&p), hard_codes))
                } else {
                    said(f, drops(&p), hard_codes)
                };
                diagnostic_assessments.push((verdict.clone(), plain_verdict));
                let selection_fill =
                    crate::sheet_fit::selection_fill(analysis, &p, compare_diagram_fill);
                tried.push(format!(
                    "{name} {} fill {:.1}% ({}, {over} additional view conflicts, {crossings} additional material crossings, {} {BASIS_CONFLICTS}, {unreadable_steps} unreadable step spans, {})",
                    p.sheet.scale,
                    selection_fill * 100.0,
                    verdict,
                    ink_findings(&p),
                    p.sheet.arrangement
                ));
                if line_crossings > 0 {
                    // Invariant: A candidate trace was pushed immediately before updating its final entry.
                    tried
                        .last_mut()
                        .expect("candidate trace")
                        .push_str(&format!(
                            "; {line_crossings} additional dimension-line crossings"
                        ));
                }
                let corridor_extra = extra_corridors(&p);
                let existing_extra = extra_existing_crossings(&p);
                if corridor_extra > 0 {
                    // Invariant: A candidate trace was pushed immediately before updating its final entry.
                    tried.last_mut().expect("candidate trace").push_str(&format!(
                        "; {corridor_extra} extra leader/extension corridors beyond the established drawing"));
                }
                if existing_extra > 0 {
                    tried
                        .last_mut()
                        // Invariant: A candidate trace was pushed immediately before updating its final entry.
                        .expect("candidate trace")
                        .push_str(&format!(
                            "; {existing_extra} extra material crossings in existing dimensions"
                        ));
                }
                // Reserving real corridors may reveal that the seed loses ink off paper.
                // If no complete drawing exists, retain all its measurements on a physically
                // fitting sheet with fewer hard findings, still reporting every missing item.
                if first.gdt.is_empty()
                    && !drawn_items_fit(analysis, &first)
                    && actual_fit
                    && carried(&first).is_subset(&carried(&p))
                    && undrawn_gdt(&p) <= undrawn_gdt(&first)
                    && corridor_extra == 0
                    && existing_extra == 0
                    && over == 0
                    && f.hard == 0
                {
                    let rank = (f.hard, f.dropped, i);
                    if fitting_incomplete
                        .as_ref()
                        .is_none_or(|(h, d, j, _)| rank < (*h, *d, *j))
                    {
                        fitting_incomplete = Some((f.hard, f.dropped, i, p.clone()));
                    }
                }
                // Valid and complete: no hard finding, nothing dropped or undrawn, and no more
                // annotations over another view than the seed (the port's own gate).
                if !complete_candidate(f, over)
                    || quality_veto
                    || corridor_extra > 0
                    || existing_extra > 0
                {
                    continue;
                }
                if smallest.is_none() {
                    smallest = Some((i, scale));
                    smallest_iso = p.views.iter().find(|v| v.id == "v:iso").map(|v| v.scale);
                }
                candidates.push((
                    crate::sheet_fit::Candidate {
                        sheet: i,
                        scale,
                        width: p.sheet.page[0],
                        fill: selection_fill,
                        extra_crossings: crossings + line_crossings,
                    },
                    p,
                ));
            }
        }
        if cut && candidates.is_empty() {
            tried.pop();
            // Already allocated content is assembled by the caller. Its completed document
            // gets the same fallback in sheets::allocate_counted, rather than nesting moves.
            let assigned = spec.views.values().any(|v| v.sheet.is_some_and(|n| n > 1))
                || spec.tables.iter().any(|t| t.sheet.is_some_and(|n| n > 1))
                || spec
                    .sheets
                    .as_ref()
                    .and_then(|s| s.hole_table)
                    .is_some_and(|n| n > 1);
            let mut fallback_spec = spec.clone();
            if !allow_automatic {
                for name in crate::detail::NAMES {
                    if !fallback_spec
                        .views
                        .get(name)
                        .is_some_and(|v| v.detail.is_some())
                    {
                        fallback_spec.views.entry(name.into()).or_default().omit = true;
                    }
                }
            }
            if !assigned
                && let Some(plan) =
                    crate::sheets::small_fallback(analysis, &fallback_spec, &first, &mut tried)
            {
                if extra_corridors(&plan) == 0 && extra_existing_crossings(&plan) == 0 {
                    return Ok(plan);
                }
                tried.push(
                    "practical-sheet allocation still obstructs established corridors".into(),
                );
            }
            rule = false;
            seed_kept =
                seed_clean && extra_corridors(&first) == 0 && extra_existing_crossings(&first) == 0;
            first_sheet = LARGE_SHEET;
            continue;
        }
        break;
    }
    if spec.sheet.size.is_none()
        && spec.dimensions.is_empty()
        && spec.views.is_empty()
        && !crate::planning::lookup::<bool>(analysis, "canonical-circular-recipes", &[])
            .unwrap_or(false)
        && let Some((small, _)) =
            candidates
                .iter()
                .enumerate()
                .min_by(|(_, (a, _)), (_, (b, _))| {
                    a.sheet.cmp(&b.sheet).then(b.scale.total_cmp(&a.scale))
                })
        && candidates[small]
            .1
            .dimensions
            .iter()
            .any(|d| d.kind == "callout" && d.terms.iter().any(|s| crate::callouts::is_break(s)))
    {
        let reference = candidates[small].1.clone();
        let sheet = candidates[small].0.sheet;
        candidates.retain(|(c, p)| {
            if c.sheet <= sheet || !longer_callouts(p, &reference) {
                return true;
            }
            let prefix = format!("{} {} fill ", layout::PAGES[c.sheet].0, scale_name(c.scale));
            for trace in tried.iter_mut().filter(|t| t.starts_with(&prefix)) {
                *trace = trace.replacen("(valid and complete", "(leader length quality veto", 1);
                *trace +=
                    "; automatic enlargement lengthens an established leader by more than 2 mm";
            }
            false
        });
    }
    let keys: Vec<_> = candidates.iter().map(|(c, _)| *c).collect();
    let Some((choice, (i, s))) =
        crate::sheet_fit::choose(&keys, spec.sheet.size.is_some(), seed_kept)
    else {
        if let Some((_, _, _, mut fitting)) = fitting_incomplete {
            fitting.spec = spec.clone();
            fitting.sheet.default = true;
            fitting.sheet.basis += &format!(
                "; no valid complete smallest fit; retained all seed measurements on fitting paper with fewer hard findings; candidates: {}",
                tried.join("; ")
            );
            return Ok(fitting);
        }
        // No valid complete pair: keep the initial drawing and its findings, on its own paper.
        // Only a seed below the absolute floor (1:10, the smallest scale the automatic choice
        // ever offers) is redrawn at that floor; a seed between it and the feature floor is
        // kept and the shortfall reported, as no legible candidate drew more validly.
        let floor = crate::sheet_fit::ABSOLUTE_FLOOR;
        if first.sheet.scale_factor < floor && crate::sheets::floor(spec).is_none_or(|f| floor >= f)
        {
            let mut named = spec.clone();
            named.sheet.size = Some(first.sheet.size.clone());
            named.sheet.scale = Some(scale_name(floor));
            first = plan_escalated_with(analysis, &named, true, None, false)?;
            first.sheet.default = true;
            first.spec = spec.clone();
            first.sheet.basis = format!(
                "{} at {}, the smallest automatic scale, as nothing smaller is legible",
                first.sheet.size, first.sheet.scale
            ) + first
                .sheet
                .basis
                .rsplit_once("; drawn-item fill ")
                .map(|(_, f)| format!("; drawn-item fill {f}"))
                .unwrap_or_default()
                .as_str();
        }
        let illegible = first.sheet.scale_factor < min_legible_scale
            || crate::sheet_fit::illegible_steps(analysis, &first) > 0;
        first.sheet.basis += &format!(
            "; no valid complete smallest fit at or above {}; candidates: {}",
            scale_name(min_legible_scale),
            tried.join("; ")
        );
        let below = if illegible {
            format!(
                "; it is below {}, the opening legibility floor, or has dimensioned steps shorter than the printed capital height",
                scale_name(min_legible_scale)
            )
        } else {
            String::new()
        };
        let f = findings(&first);
        let clean_retained = f.clean() && !illegible;
        let assessment = if clean_retained {
            "has no hard layout findings or space drops".into()
        } else {
            format!("has {}", said(f, drops(&first), BTreeMap::new()))
        };
        let diagnostic_trials = if clean_retained {
            tried.join("; ")
        } else {
            diagnostic_assessments
                .iter()
                .fold(tried.join("; "), |trace, (coded, plain)| {
                    trace.replace(coded, plain)
                })
        };
        let conclusion = if clean_retained {
            "no alternative standard sheet and legible scale passed every layout, coverage and annotation-quality check"
        } else {
            "no standard sheet and legible scale yields a valid complete drawing"
        };
        first.problems.push(Problem {
            id: "p:layout_recovery_exhausted:sheet".into(),
            code: "layout_recovery_exhausted".into(),
            severity: if illegible { "warning" } else { "info" }.into(),
            message: format!(
                "{} at {} {}{below}; {conclusion}, so this drawing and its findings are kept. Tried {}.",
                first.sheet.size,
                first.sheet.scale,
                assessment,
                diagnostic_trials
            ),
            subjects: vec!["sheet".into()],
            ..Default::default()
        });
        first.status = crate::completeness::status(&first.problems);
        return Ok(first);
    };
    // When new recess information needs more paper, preserve the established
    // dimensioned-view scale on the already permitted neighbouring sheet before
    // shrinking old geometry merely to improve the fill percentage. All these
    // candidates have passed the same validity, coverage and corridor gates.
    let retained = established_scale.and_then(|scale| {
        if choice.is_some_and(|j| keys[j].scale >= scale) {
            return None;
        }
        candidates
            .iter()
            .enumerate()
            .filter(|(_, (c, _))| {
                c.scale == scale
                    && (!seed_kept || c.extra_crossings == 0)
                    && (spec.sheet.size.is_some()
                        || (c.sheet == i && c.scale == s)
                        || (c.sheet == i + 1
                            && crate::sheet_fit::allowed((i, s), c.sheet, c.scale)))
            })
            .min_by_key(|(_, (c, p))| (c.sheet, ink_findings(p), feature_crossings(p)))
            .map(|(j, _)| j)
    });
    let choice = retained.or(choice);
    let estimate = format!(
        "first estimate {} at {} ({})",
        first.sheet.size,
        first.sheet.scale,
        if seed_kept {
            "valid and complete"
        } else if seed_clean {
            "valid and complete but excluded by the large-sheet rule"
        } else {
            "not valid and complete"
        }
    );
    let (mut chosen, kept) = match choice {
        Some(index) => (candidates.swap_remove(index).1, ""),
        None => (
            first.clone(),
            "; every allowed pair adds material crossings to the valid first estimate, which is kept",
        ),
    };
    chosen.sheet.basis = format!(
        "smallest fit {} at {}; at most one larger sheet only with enlargement; drawn-item fill {:.1}%, target {:.0}–{:.0}% (validity and completeness first); {estimate}{kept}; candidates: {}",
        layout::PAGES[i].0,
        scale_name(s),
        chosen.sheet.fill * 100.0,
        FILL_BAND[0] * 100.0,
        FILL_BAND[1] * 100.0,
        tried.join("; ")
    );
    if crate::sheet_fit::selection_fill(analysis, &chosen, compare_diagram_fill)
        != chosen.sheet.fill
    {
        chosen.sheet.basis += "; candidate scores omit source-only notes; the notes remain drawn and sheet.fill measures all ink";
    }
    if retained.is_some() {
        chosen.sheet.basis += "; the established dimensioned-view scale retained while making room for recess information";
    }
    chosen.sheet.default = true;
    chosen.spec = first.spec;
    if chosen.sheet.size != first.sheet.size || chosen.sheet.scale != first.sheet.scale {
        chosen.problems.push(Problem {
            id: "p:layout_replanned:sheet".into(),
            code: "layout_replanned".into(),
            severity: "info".into(),
            // The measured fill stays in the sheet basis: an edit that moves it by a fraction
            // must not churn this problem in an edit's delta.
            message: format!(
                "Sheet choice changed from {} at {} to {} at {} {}; the sheet basis lists the candidates.",
                first.sheet.size, first.sheet.scale, chosen.sheet.size, chosen.sheet.scale,
                if retained.is_some() { "to retain the established dimensioned-view scale" } else { "by drawn-item fill" },
            ),
            subjects: vec!["sheet".into()],
            ..Default::default()
        });
    }
    Ok(chosen)
}

// Shared canonical inputs for pin placement and the editor's label anchor metadata.
pub(crate) fn pin_base(spec: &Spec) -> Spec {
    let mut base = Spec::default();
    base.sheet.size = spec.sheet.size.clone();
    base.sheet.scale = spec.sheet.scale.clone();
    base.sheet.preferred_max_size = spec.sheet.preferred_max_size.clone();
    base.sheet.preferred_max_sheets = spec.sheet.preferred_max_sheets;
    base.sheet.dimension_spacing = spec.sheet.dimension_spacing;
    base.sheet.frame_gutter = spec.sheet.frame_gutter;
    base.sheet.projection = spec.sheet.projection.clone();
    base.sheet.fit_deviations = spec.sheet.fit_deviations;
    base.sheet.references = spec.sheet.references;
    base.sheet.view_gutter = spec.sheet.view_gutter;
    base.sheet.fit_limits = spec.sheet.fit_limits;
    base.sheet.table_columns = spec.sheet.table_columns.clone();
    base.pmi = spec.pmi;
    base.origin = spec.origin.clone();
    base.hole_table = spec.hole_table;
    base.iso_scale_factor = spec.iso_scale_factor;
    base.dimension_schemes = spec.dimension_schemes.clone();
    // A principal view the spec names is kept in the unpinned drawing too: naming it is what
    // keeps a view an automatic choice would leave out (#222, a turned part's repeated profile).
    for (name, ..) in VIEWS.iter().filter(|(name, ..)| *name != "iso") {
        if spec.views.contains_key(*name) {
            base.views.insert((*name).into(), ViewSpec::default());
        }
    }
    base.views.keep_sections_from(&spec.views);
    base.views.keep_breaks_from(&spec.views);
    if spec.dimensions.values().any(|d| d.between.is_some()) {
        base.dimension_source = spec.dimension_source;
        base.dimensions = spec.dimensions.clone();
        for d in base.dimensions.values_mut() {
            d.place = None;
            d.view = None;
            d.tolerance = None;
            d.fit = None;
            d.text = None;
            d.style = None;
        }
    }
    base
}

/// [`plan`] with draftwright's bounded layout recovery applied ([`recover`]): the plan every
/// caller (the library's [`crate::api`], so the command line, the JSON-lines service and MCP)
/// is given. A plan that cannot be made is refused as [`plan`] refuses it.
pub fn plan_recovered(analysis: &Analysis, spec: &Spec) -> Result<Plan, Invalid> {
    crate::decision_overrides::boundary(|| {
        let plan = crate::bom::with_titles(analysis, spec, || {
            layout::with_gutter(
                spec.sheet.frame_gutter.unwrap_or(layout::FRAME_GUTTER),
                || {
                    let plan = plan_recovered_once(analysis, spec)?;
                    let plan = balloon_fallback(analysis, spec, plan)?;
                    let mut plan = manufacturing_fallback(analysis, spec, plan, |s| {
                        plan_recovered_once(analysis, s)
                    })?;
                    crate::graphical_pmi::apply(analysis, &mut plan);
                    crate::hole_exits::report(analysis, &mut plan);
                    plan.record_callout_offsets();
                    Ok(plan)
                },
            )
        })?;
        retain_unapplied_edits(analysis, plan, plan_recovered)
    })
}

/// Validate the complete request first, then keep unapplied edits in the document without
/// letting them steer layout. In particular, a stale omit must not reserve peer routes.
fn retain_unapplied_edits(
    analysis: &Analysis,
    plan: Plan,
    replan: fn(&Analysis, &Spec) -> Result<Plan, Invalid>,
) -> Result<Plan, Invalid> {
    let warnings: Vec<_> = plan
        .problems
        .iter()
        .filter(|p| p.code == "dimension_edit_unapplied")
        .collect();
    if warnings.is_empty() {
        return Ok(plan);
    }
    let mut applicable = plan.spec.clone();
    let mut retained = BTreeMap::new();
    applicable.dimensions.retain(|id, edit| {
        let path = format!("dimensions/{}", crate::items::key(id));
        if warnings.iter().any(|p| p.subjects.contains(&path)) {
            retained.insert(id.clone(), edit.clone());
            false
        } else {
            true
        }
    });
    if retained.is_empty() {
        return Ok(plan);
    }
    let mut drawn = replan(analysis, &applicable)?;
    drawn.spec.dimensions.extend(retained);
    drawn.problems.extend(warnings.into_iter().cloned());
    Ok(drawn)
}

/// Inspection balloons are placed on the finished sheet, after the paper policy has chosen its
/// scale. When the automatic scale leaves some numbered characteristic without a clear place for
/// its balloon, the next smaller standard scale on the same sheet is tried, and taken when it
/// places more of them with no more hard layout findings and no more leader crossings: a larger
/// drawing never costs a reader the inspection numbers the smaller one shows, and the numbers
/// are never bought with crossings. An explicit scale is the user's to keep.
fn balloon_fallback(analysis: &Analysis, spec: &Spec, plan: Plan) -> Result<Plan, Invalid> {
    let missing = |p: &Plan| {
        p.problems
            .iter()
            .filter(|q| q.code == "characteristic_balloon_missing")
            .count()
    };
    if spec.sheet.scale.is_some()
        || !crate::characteristics::enabled(&plan)
        || missing(&plan) == 0
        || !plan.sheets.is_empty()
    {
        return Ok(plan);
    }
    let Some(&smaller) = layout::SCALES
        .iter()
        .find(|s| **s < plan.sheet.scale_factor - 1e-12)
    else {
        return Ok(plan);
    };
    let mut named = spec.clone();
    named.sheet.size = Some(plan.sheet.size.clone());
    named.sheet.scale = Some(scale_name(smaller));
    let Ok(mut trial) = plan_recovered_once(analysis, &named) else {
        return Ok(plan);
    };
    let crossings = |p: &Plan| {
        p.problems
            .iter()
            .filter(|q| q.code == "annotation_leader_crossing")
            .count()
    };
    // Balloons are not bought with crossings: the smaller scale must not cross more lines.
    if missing(&trial) >= missing(&plan)
        || findings(&trial).hard > findings(&plan).hard
        || crossings(&trial) > crossings(&plan)
    {
        return Ok(plan);
    }
    trial.sheet.basis = format!(
        "{}; {} at {} left {} inspection balloon(s) without a clear place, so the next smaller scale, which places more, is kept",
        trial.sheet.basis,
        plan.sheet.size,
        plan.sheet.scale,
        missing(&plan)
    );
    trial.sheet.default = plan.sheet.default;
    trial.spec = plan.spec.clone();
    Ok(trial)
}

/// draftwright's fallback for a manufacturing requirements table with no place on the finished
/// sheet (`_place_manufacturing_schedule`: the table is an alternative presentation, so when it
/// cannot be committed its short references are never emitted and the callouts print the
/// requirements in full). The port places its callouts before the table, so the drawing is
/// planned again with each scheduled requirement's owner in full wording, its `SEE MFG n`
/// replaced by the complete suffix (`⌀1.6 ↧ 3.8 M2 x 0.4-6H RH; 2.5 MIN FULL THREAD`), and
/// the `table_not_placed` error gone with the table. Only a drawing the paper policy could not
/// give room to (an explicit sheet/size and sheet/scale) reaches this; the requested spec is
/// kept as the plan's, so a re-plan reaches the same drawing, and an info finding
/// `manufacturing_table_fallback` names the requirements printed in full. A full replan that is
/// refused, or that still leaves the table unplaced (it cannot, having no table), keeps the first
/// drawing and its error: the fallback never makes a plannable spec unplannable.
///
/// [`plan`] applies it too, so the canonical and reference replays used for pins and radius
/// references see the fallback drawing whenever the requested spec reaches it; the paper policy's
/// sheet trials plan with [`plan_automatic`] and never do.
fn manufacturing_fallback(
    analysis: &Analysis,
    spec: &Spec,
    plan: Plan,
    replan: impl Fn(&Spec) -> Result<Plan, Invalid>,
) -> Result<Plan, Invalid> {
    let unplaced = format!("p:table_not_placed:{}", crate::pmi_lowering::SCHEDULE_ID);
    if !plan.problems.iter().any(|p| p.id == unplaced) {
        return Ok(plan);
    }
    let effective = crate::spec_gdt::effective(analysis, spec)?;
    let m = crate::pmi_lowering::manufacturing_for(effective.as_ref(), spec);
    let owners: Vec<(String, String)> = m
        .tags
        .iter()
        .filter_map(|(source, _)| {
            let o = m.owned.iter().find(|o| &o.source == source)?;
            Some((o.owner.clone(), source.clone()))
        })
        .collect();
    if owners.is_empty() {
        return Ok(plan);
    }
    let mut full = spec.clone();
    for (owner, _) in &owners {
        full.features.entry(owner.clone()).or_default().callouts =
            Some(crate::callouts::Settings {
                style: crate::callouts::Style::Full,
            });
    }
    let Ok(mut fallen) = replan(&full) else {
        return Ok(plan);
    };
    if fallen.problems.iter().any(|p| p.id == unplaced) {
        return Ok(plan);
    }
    fallen.spec = plan.spec;
    for d in &mut fallen.dimensions {
        for (_, source) in &owners {
            d.basis = d.basis.replace(
                &format!("callouts/style Full: {source} full wording requested"),
                &format!(
                    "manufacturing table fallback: {source} the manufacturing requirements table has no place on the sheet, so the callout states the requirement in full (draftwright's fallback; a larger sheet/size or smaller sheet/scale restores the table)"
                ),
            );
        }
    }
    let sources: Vec<String> = owners.iter().map(|(_, s)| s.clone()).collect();
    let mut subjects: Vec<String> = owners.iter().map(|(o, _)| o.clone()).collect();
    subjects.dedup();
    fallen.problems.push(Problem {
        id: format!("p:manufacturing_table_fallback:{}", crate::pmi_lowering::SCHEDULE_ID),
        code: "manufacturing_table_fallback".into(),
        severity: "info".into(),
        message: format!(
            "The manufacturing requirements table has no place on the sheet, so the callouts state {} in full in place of SEE MFG references (draftwright's fallback). A larger sheet (sheet/size) or a smaller scale (sheet/scale) restores the table.",
            sources.join(", ")
        ),
        subjects,
        refs: sources,
        ..Default::default()
    });
    Ok(fallen)
}

fn plan_recovered_once(analysis: &Analysis, spec: &Spec) -> Result<Plan, Invalid> {
    if spec
        .sheet
        .view_gutter
        .is_some_and(|g| !valid_view_gutter(g))
    {
        return Err(invalid(
            "sheet/view_gutter must be from 0 to 1189 paper mm.",
            &[],
        ));
    }
    let prepared = crate::modelled_threads::planning(analysis, spec);
    let mut plan = crate::dims::pmi_policy::planning(|| plan_recovered_inner(&prepared, spec))?;
    if spec.sheet.keep_layout == Some(true) || !crate::sheet_policy::finishes_selection(spec) {
        finish_selected(&prepared, &mut plan);
    }
    crate::modelled_threads::source_hash(
        analysis,
        matches!(&prepared, std::borrow::Cow::Owned(_)),
        &mut plan,
    );
    note_final_conflicts(&mut plan);
    Ok(plan)
}

/// The final dependent ink and findings, shared by candidate acceptance and publication.
pub(crate) fn finish_selected(analysis: &Analysis, plan: &mut Plan) {
    crate::half_section::apply(analysis, plan);
    crate::sheets::furnish(plan);
    crate::fixes::retain_local_layout(plan);
    crate::dims::contacts::report(analysis, plan);
    crate::characteristics::apply(analysis, plan);
}

fn plan_recovered_inner(analysis: &Analysis, spec: &Spec) -> Result<Plan, Invalid> {
    if spec.pmi == PmiMode::Ignore && crate::dims::pmi_policy::carries(analysis) {
        return crate::dims::pmi_policy::ignore(analysis, spec, plan_recovered);
    }
    crate::planning::with(analysis, || {
        let plan = if spec.sheet.keep_layout == Some(true) {
            let fixed = crate::local_layout::forced(spec)?;
            let mut plan = crate::planning::fixed_pair(|| plan_automatic(analysis, &fixed))?;
            plan.spec = spec.clone();
            plan
        } else {
            crate::sheet_policy::select(analysis, spec)?
        };
        if spec.sheet.keep_layout != Some(true) && crate::sheet_policy::finishes_selection(spec) {
            Ok(plan)
        } else {
            finish_presentation(analysis, spec, plan)
        }
    })
}

/// The same final routes are used by paper-choice previews and by the selected drawing.
/// Previews run in an isolated scope; only the selected drawing publishes these repairs.
pub(crate) fn finish_presentation(
    analysis: &Analysis,
    spec: &Spec,
    mut plan: Plan,
) -> Result<Plan, Invalid> {
    // Finish the selected drawing with the spacing it was planned with, which may be the
    // automatic compact fallback ([`crate::sheet_policy`]).
    let compact = plan.sheet.dimension_spacing == DimensionSpacing::Compact;
    crate::dims::with_compact(compact, || {
        let effective = crate::spec_gdt::effective(analysis, &plan.spec)?;
        // Added notch callouts can leave a fixed-pair paper trial with
        // deferred routes. Finish that expanded inventory in its chosen
        // frame, just as canonical pin replay does. Existing annotation
        // families retain their established finishing path.
        let expanded_notches = plan.dimensions.iter().any(|d| {
            d.measurements
                .iter()
                .any(|m| m.feature.starts_with("f:rectangular_through_notch:"))
        });
        if expanded_notches {
            finish_nominal_plan(effective.as_ref(), spec, &mut plan);
        }
        // Paper trials preview these same repairs before accepting a new scale.
        // A reference that cannot be planned (an authored item duplicating an automatic
        // one) leaves the drawing to its own pass.
        let natural = natural_spec(spec).and_then(|full| plan_recovered(analysis, &full).ok());
        crate::dims::callout_routes::prefer_interior(
            effective.as_ref(),
            spec,
            &mut plan,
            natural.as_ref(),
        );
        crate::dims::finish_openings(effective.as_ref(), spec, &mut plan);
        crate::dims::callout_routes::finish(effective.as_ref(), spec, &mut plan);
        crate::dims::hole_marks::attach(effective.as_ref(), spec, &mut plan);
        crate::dims::size_datums::attach(effective.as_ref(), spec, &mut plan);
        if crate::dims::openings_need_settling(&plan) {
            crate::dims::finish_attached_openings(effective.as_ref(), spec, &mut plan);
            crate::dims::hole_marks::attach(effective.as_ref(), spec, &mut plan);
            crate::dims::size_datums::attach(effective.as_ref(), spec, &mut plan);
            crate::dims::finish_attached_openings(effective.as_ref(), spec, &mut plan);
            crate::dims::callout_routes::finish_frames(effective.as_ref(), spec, &mut plan);
        }
        crate::dims::gdt::cited_undrawn(&mut plan);
        crate::dims::pmi_policy::audit(effective.as_ref(), &plan.spec.clone(), &mut plan);
        if expanded_notches
            || crate::dims::pmi_policy::carries(effective.as_ref())
            || !spec.origin.is_empty()
        {
            crate::fixes::attach(effective.as_ref(), &mut plan);
        }
        Ok(plan)
    })
}

/// The established planner, also used unchanged for explicit controls and forced candidates.
pub(crate) fn plan_automatic(analysis: &Analysis, spec: &Spec) -> Result<Plan, Invalid> {
    // R5's max-only shortcut asks for the same single-sheet plan before allocation.
    // Reuse that complete result when the paper policy has just requested it already.
    let key = serde_json::to_vec(&(
        spec,
        crate::planning::nominals_deferred(),
        crate::planning::policy_key(analysis),
        crate::planning::is_fixed_pair(),
    ))
    // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
    .expect("planning inputs serialize");
    // Every plan made for this spec leaves out the same idle view (#222).
    crate::dims::idle_decided(analysis, spec, || {
        crate::dims::spaced(spec, || {
            // A finished plan reused from a retained session scope skips the policies its
            // planning activated in this scope (the established route policy and its witness
            // and interior statement sets), which the session clears on every invocation.
            // Activate them again, exactly as the planning that produced it did, so later
            // planning in this invocation does not depend on whether this plan was reused.
            if spec.sheet.scale.is_none()
                && crate::planning::lookup::<Result<Plan, Invalid>>(analysis, "finished-plan", &key)
                    .is_some()
            {
                activate_established(analysis, spec)?;
            }
            crate::planning::memo(analysis, "finished-plan", key, || {
                plan_automatic_uncached(analysis, spec)
            })
        })
    })
}

/// The scope policies [`plan_automatic_uncached`] activates through [`established_drawing`],
/// with the same migrated spec and effective Analysis. A merged Analysis plans in its own
/// scope, so its policies never reach this one.
fn activate_established(analysis: &Analysis, spec: &Spec) -> Result<(), Invalid> {
    let spec = &migrate(spec, analysis)?;
    let bound = crate::recognition_corrections::canonical(analysis, spec)?;
    let spec = bound.as_ref();
    let merged = crate::spec_gdt::effective(analysis, spec)?;
    if matches!(merged, std::borrow::Cow::Borrowed(_))
        && crate::dims::has_new_native_recess_dimensions(analysis)
    {
        established_drawing(analysis, spec)?;
    }
    Ok(())
}

fn plan_automatic_uncached(analysis: &Analysis, spec: &Spec) -> Result<Plan, Invalid> {
    // Recovery may restore the caller's intent after a trial; retain the migrated keys
    // and schema there too, rather than restoring a version-1 spec onto a version-2 plan.
    let spec = &migrate(spec, analysis)?;
    let bound = crate::recognition_corrections::canonical(analysis, spec)?;
    let spec = bound.as_ref();
    // The Spec's datums and controls merged into the PMI reading, once, so every later use of the
    // Analysis (and the planning memo, keyed by its address) sees them ([`crate::spec_gdt`]).
    let merged = crate::spec_gdt::effective(analysis, spec)?;
    let analysis = merged.as_ref();
    let mut plan = crate::planning::with(analysis, || {
        if spec
            .sheets
            .as_ref()
            .and_then(|s| s.max)
            .is_some_and(|n| n > 1)
            && crate::sheets::max_only(spec)
        {
            // Permission to add sheets must not change an already complete
            // single-sheet drawing, including its final precision repairs.
            let mut single = spec.clone();
            single.sheets = None;
            let mut plan = plan_automatic(analysis, &single)?;
            if findings(&plan).clean()
                && undrawn_gdt(&plan) == 0
                && drawn_items_fit(analysis, &plan)
            {
                plan.spec = spec.clone();
                return Ok(plan);
            }
        }
        if spec.sheet.scale.is_none() && crate::dims::has_new_native_recess_dimensions(analysis) {
            established_drawing(analysis, spec)?;
        }
        if (spec.sheets.is_none()
            || (crate::planning::is_fixed_pair() && crate::sheets::max_only(spec)))
            && crate::nominal::geometry_eligible(analysis)
        {
            let mut plan =
                crate::planning::defer_nominals(|| crate::sheets::allocate(analysis, spec))?;
            finish_nominal_plan(analysis, spec, &mut plan);
            retain_dropped_location(analysis, spec, &mut plan);
            crate::iso_caption::refresh(analysis, &mut plan);
            Ok(plan)
        } else {
            crate::sheets::allocate(analysis, spec)
        }
    })?;
    crate::centrelines::trim_to_frame(&mut plan.views, plan.sheet.page);
    if !crate::placement_intent::has_pins(spec) {
        type Labels = BTreeMap<String, (String, [f64; 2])>;
        static LABELS: crate::planning::BoundedMemo<Labels> = crate::planning::BoundedMemo::new(64);
        let base = pin_base(spec);
        let key: [u8; 32] = Sha256::digest(
            serde_json::to_vec(&(
                &plan.analysis_sha256,
                &base,
                crate::nominal::comparison(),
                crate::planning::nominals_deferred(),
                crate::dims::idle_given(),
            ))
            // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
            .expect("canonical label inputs serialize"),
        )
        .into();
        if spec.dimensions.values().any(|d| {
            d.place.is_some()
                || d.view.is_some()
                || d.tolerance.is_some()
                || d.fit.is_some()
                || d.text.is_some()
                || d.style.is_some()
        }) || spec.features.values().any(|f| f.fit.is_some())
        {
            // A side/lane placement or wording edit must not become a new offset origin.
            // Retain canonical metadata without changing the drawing's solve or wire shape.
            plan.dimension_labels = if let Some(labels) = LABELS.get(&key) {
                labels
            } else {
                // Metadata preparation must not fill the live edit's retained solve cache.
                let labels =
                    crate::planning::isolated(analysis, || plan_recovered(analysis, &base))?
                        .dimension_labels;
                LABELS.insert(key, labels.clone());
                labels
            };
        } else {
            plan.record_dimension_labels();
            if spec == &base {
                LABELS.insert(key, plan.dimension_labels.clone());
            }
        }
    }
    crate::dims::restore_proven_arc_tips(analysis, spec, &mut plan);
    // PMI completeness belongs to the selected drawing. Attach its remedies once,
    // after that audit in plan_recovered_inner, rather than hashing and enriching
    // every automatic candidate and then repeating the work on the public plan.
    if !crate::dims::pmi_policy::carries(analysis) && spec.origin.is_empty() {
        crate::fixes::attach(analysis, &mut plan);
    }
    Ok(plan)
}

/// A selected ladder can lose a location when its long witness meets a longer
/// callout. Reserve the opposite annotation band from the start of one bounded
/// trial, rather than moving the witness outside its allocated view afterwards.
fn retain_dropped_location(analysis: &Analysis, spec: &Spec, plan: &mut Plan) {
    if spec.hole_table != crate::hole_table::Setting::Never
        || spec.sheet.size.is_some()
        || spec.sheet.scale.is_some()
        || crate::placement_intent::has_pins(spec)
    {
        return;
    }
    let Some(d) = plan.dimensions.iter().find(|d| {
        matches!(&d.state, ItemState::NotDrawable { reason_code } if reason_code == "location_ref_dropped")
    }) else {
        return;
    };
    let id = d.id.clone();
    let horizontal = d.measurements.iter().any(|m| m.parameter.ends_with(".x"));
    let side = match (horizontal, d.view.as_str()) {
        (true, "v:plan") => crate::placement_intent::Side::Below,
        (true, _) => crate::placement_intent::Side::Above,
        (false, _) => crate::placement_intent::Side::Right,
    };
    let mut trial_spec = spec.clone();
    trial_spec.sheet.size = Some(plan.sheet.size.clone());
    trial_spec.sheet.scale = Some(plan.sheet.scale.clone());
    trial_spec.dimensions.entry(id.clone()).or_default().place =
        Some(crate::placement_intent::Place {
            view: Some(d.view.trim_start_matches("v:").to_string()),
            side: Some(side),
            ..Default::default()
        });
    let Ok(mut trial) = plan_automatic(analysis, &trial_spec) else {
        return;
    };
    // Match sheet recovery's validity/completeness ordering: a visible line
    // crossing is preferable to losing a required dimension or spilling ink.
    let rank = |p: &Plan| {
        (
            findings(p).hard,
            drops(p),
            undrawn_gdt(p),
            ink_findings(p),
            feature_crossings(p),
        )
    };
    if drops(&trial) < drops(plan) && rank(&trial) < rank(plan) && drawn_items_fit(analysis, &trial)
    {
        trial.spec = spec.clone();
        trial.sheet.default = plan.sheet.default;
        trial.sheet.basis.clone_from(&plan.sheet.basis);
        if let Some(d) = trial.dimensions.iter_mut().find(|d| d.id == id)
            && let Some(original) = plan.dimensions.iter().find(|d| d.id == id)
        {
            d.default = original.default;
            d.from_spec.clone_from(&original.from_spec);
            d.basis = format!(
                "{}; opposite annotation band reserved to retain the location",
                original.basis
            );
        }
        *plan = trial;
    }
}

thread_local! {
    static UNCAPPED: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
}

/// Run *run* with the established drawing's scale cap on candidates lifted or kept
/// ([`recover_tables`]'s last resort).
fn with_uncapped<T>(uncapped: bool, run: impl FnOnce() -> T) -> T {
    struct Restore(bool);
    impl Drop for Restore {
        fn drop(&mut self) {
            UNCAPPED.set(self.0);
        }
    }
    let _restore = Restore(UNCAPPED.replace(uncapped));
    run()
}

/// Plans the hole schedules' presentation with *plan* and installs the schedules
/// ([`crate::hole_table::install`]). Where they find no clear place because a slot callout took
/// the place an origin mark needs, the presentation is planned once more with that place reserved
/// before the dimensions are laid out ([`crate::hole_table::slot_callout_takes_origin`], CTC-03).
/// `None` when the schedules are not installed.
///
/// A row replaces a callout whose size comes from PMI. Where that drawing has an error the plan
/// without schedules (*unscheduled*, which the selection was made from) does not, the schedules
/// are installed once more with those callouts kept beside their rows, as they were before; and
/// so they are where a frame or datum symbol on a callout the rows replace can attach to its hole
/// in no other way (a tag, a frame of the same hole, or for a frame a leader to the hole itself).
/// A newly recognized shared thread keeps that fallback when retaining size callouts avoids
/// additional frame-leader crossings; its thread statement still moves to each applicable row.
fn install_schedules(
    analysis: &Analysis,
    unscheduled: &Plan,
    selection: &crate::hole_table::Selection,
    plan: impl Fn(&crate::hole_table::Selection) -> Result<Plan, Invalid>,
) -> Result<Option<Plan>, Invalid> {
    let installed = install_selection(analysis, selection, &plan)?;
    let Some(replacing) = installed.as_ref().filter(|_| selection.pmi_replaced) else {
        return Ok(installed);
    };
    let errors = |p: &Plan| -> BTreeSet<String> {
        p.problems
            .iter()
            .filter(|q| q.severity == "error")
            .map(|q| q.id.clone())
            .collect()
    };
    let keeping = || {
        let kept = crate::hole_table::select_keeping_pmi(analysis, unscheduled);
        if kept.tables.is_empty() {
            return Ok(None);
        }
        install_selection(analysis, &kept, &plan)
    };
    if !errors(replacing).is_subset(&errors(unscheduled)) {
        return keeping();
    }
    // Replacing a size callout can force its tolerance frame onto a long leader.
    // Keep the source size beside its row when that avoids new crossings; the
    // table still replaces the generated drill callout of a shared thread.
    let crossings = |p: &Plan| {
        p.problems
            .iter()
            .filter(|q| q.code == "annotation_leader_crossing")
            .count()
    };
    let manufacturing = crate::pmi_lowering::manufacturing(analysis);
    let shared_thread = selection
        .tables
        .iter()
        .flat_map(|t| &t.rows)
        .flat_map(|r| &r.measurements)
        .any(|m| {
            m.parameter == "thread"
                && manufacturing
                    .owned
                    .iter()
                    .filter(|o| o.id == m.feature)
                    .count()
                    > 1
        });
    if shared_thread
        && crossings(replacing) > crossings(unscheduled)
        && let Some(kept) = keeping()?
        && errors(&kept).is_subset(&errors(unscheduled))
        && crossings(&kept) < crossings(replacing)
    {
        return Ok(Some(kept));
    }
    // A frame or datum symbol held a PMI callout back where it attached to a callout the rows
    // replace; it now attaches to the hole itself (a leader, a tag, or its hole's frame), and
    // only where it can attach nowhere do the callouts stay. Glyphs on callouts the rows keep,
    // or moved by the freed layout, are the layout's to place.
    let shown: BTreeSet<&str> = replacing
        .dimensions
        .iter()
        .filter(|d| d.state == crate::items::State::Shown && d.placed.is_some())
        .map(|d| d.id.as_str())
        .collect();
    let held_back: BTreeSet<String> = crate::dims::hole_marks::attachments(analysis, unscheduled)
        .into_iter()
        .filter(|(_, callout)| callout.as_deref().is_some_and(|c| !shown.contains(c)))
        .map(|(g, _)| g)
        .collect();
    if held_back.is_empty() {
        return Ok(installed);
    }
    let attached = |p: &Plan| -> BTreeSet<String> {
        crate::dims::hole_marks::attachments(analysis, p)
            .into_keys()
            .filter(|g| held_back.contains(g))
            .collect()
    };
    let held = attached(replacing);
    if held_back.is_subset(&held) {
        return Ok(installed);
    }
    match keeping()? {
        Some(kept) if !attached(&kept).is_subset(&held) => Ok(Some(kept)),
        _ => Ok(installed),
    }
}

fn install_selection(
    analysis: &Analysis,
    selection: &crate::hole_table::Selection,
    plan: &impl Fn(&crate::hole_table::Selection) -> Result<Plan, Invalid>,
) -> Result<Option<Plan>, Invalid> {
    let mut scheduled = plan(selection)?;
    let first = scheduled.clone();
    if crate::hole_table::install(analysis, &mut scheduled, selection) {
        return Ok(Some(scheduled));
    }
    if !crate::hole_table::slot_callout_takes_origin(analysis, &first, selection) {
        return Ok(None);
    }
    let mut reserved = selection.clone();
    reserved.reserve_origins = true;
    let mut scheduled = plan(&reserved)?;
    Ok(crate::hole_table::install(analysis, &mut scheduled, &reserved).then_some(scheduled))
}

fn recover_tables(analysis: &Analysis, spec: &Spec, first: Plan) -> Result<Plan, Invalid> {
    // Establish the ordinary recovered geometry before changing presentation. A table must
    // not undo a scale recovery (nor spend one merely because its ladder has disappeared).
    let mut baseline_spec = spec.clone();
    baseline_spec.hole_table = crate::hole_table::Setting::Never;
    let mut seed = first.spec.clone();
    seed.hole_table = crate::hole_table::Setting::Never;
    let unscheduled = plan_escalated_layout(analysis, &seed)?;
    let baseline = recover(analysis, &baseline_spec, unscheduled.clone())?;
    let exhausted = recovery_exhausted;
    let unrecovered = exhausted(&baseline);
    let scheduled = schedule_recovered(analysis, spec, baseline)?;
    // Nothing is valid without a table at any scale. The established drawing's scale caps
    // the ordinary search, whose candidates may escalate to tables; when nothing under the
    // cap is valid either, the cap has removed every scale rather than preserving an old
    // corridor, so search the scales the ordinary ladder reaches without it, from the
    // estimate without a table, as when the first estimate has none (STC-08). Its drawing
    // is taken when it reads better than the schedule on the first estimate's sheet.
    if unrecovered && exhausted(&recover(analysis, spec, unscheduled.clone())?) {
        let ordinary = with_uncapped(true, || recover(analysis, spec, unscheduled))?;
        let rank = |p: &Plan| recovery_rank(analysis, p);
        if !exhausted(&ordinary) && rank(&ordinary) < rank(&scheduled) {
            return Ok(ordinary);
        }
    }
    Ok(scheduled)
}

/// [`recover_tables`]'s schedule on the sheet its table-free recovery chose.
fn schedule_recovered(
    analysis: &Analysis,
    spec: &Spec,
    mut baseline: Plan,
) -> Result<Plan, Invalid> {
    baseline.spec = spec.clone();
    let selection = crate::hole_table::select(analysis, &baseline);
    if selection.tables.is_empty() {
        return Ok(baseline);
    }
    // The table is drawn on the sheet the recovery chose, so it never undoes that choice.
    let replanned = baseline
        .problems
        .iter()
        .any(|p| p.code == "layout_replanned");
    let mut at = spec.clone();
    if replanned {
        at.sheet.size = Some(baseline.sheet.size.clone());
        at.sheet.scale = Some(scale_name(baseline.sheet.scale_factor));
    }
    let installed = install_schedules(analysis, &baseline, &selection, |selection| {
        plan_with_presentation(
            analysis,
            &at,
            false,
            replanned,
            None,
            ViewPolicy {
                complete: true,
                require_detail: false,
            },
            Some(selection),
        )
        .map(|mut scheduled| {
            if replanned {
                scheduled.spec = spec.clone();
                scheduled.sheet.default = true;
            }
            scheduled
        })
    })?;
    if let Some(mut scheduled) = installed {
        // A genuine geometry recovery still belongs to the final plan; a resolved ladder's
        // exhausted-loss message does not. Preserve the successful recovery's trace.
        scheduled.problems.extend(
            baseline
                .problems
                .iter()
                .filter(|p| p.code == "layout_replanned")
                .cloned(),
        );
        if baseline
            .problems
            .iter()
            .any(|p| p.code == "layout_replanned")
        {
            scheduled.sheet.basis = baseline.sheet.basis.clone()
                + "; hole presentation changed at the recovered geometry";
        }
        crate::completeness::report(analysis, &mut scheduled);
        scheduled.sheet.fill = crate::sheet_fit::fill(analysis, &scheduled);
        Ok(scheduled)
    } else {
        baseline.problems.push(Problem { id:"p:hole_table_unavailable:sheet".into(), code:"hole_table_unavailable".into(), severity:if spec.hole_table == crate::hole_table::Setting::Always { "warning" } else { "info" }.into(), message:"The selected schedule has no clear place at the recovered geometry scale; the original dimensions are kept.".into(), subjects:vec!["sheet".into()], ..Default::default() });
        Ok(baseline)
    }
}

/// [`plan`] with draftwright's drop and escalate applied ([`crate::hole_table`]): a hole callout
/// or location placed off the sheet is dropped and reported, and may escalate to a plan-view hole
/// table with balloons. What the recovery judges, as draftwright's escalation runs inside every
/// build its recovery compares.
///
/// A plan that leaves feature control frames, datum feature symbols or PMI labels off the sheet
/// or over another view is made again with their room reserved beyond every dimension lane of
/// their strips (`layout::Strips::reserve_gdt`'s *beyond_lanes*), and that plan is taken when it
/// leaves fewer: the first reserves within draftwright's estimate where the lane count overstates
/// the dimensions, which on most parts keeps a larger scale, but where the dimensions run on
/// past it an item can find no place (nist_stc_09 e4 at 1:1: 13 frames across the plan).
pub fn plan_escalated(analysis: &Analysis, spec: &Spec) -> Result<Plan, Invalid> {
    crate::decision_overrides::boundary(|| {
        crate::bom::with_titles(analysis, spec, || {
            let mut plan = plan_escalated_layout(analysis, spec)?;
            crate::characteristics::apply(analysis, &mut plan);
            crate::hole_exits::report(analysis, &mut plan);
            Ok(plan)
        })
    })
}

pub(crate) fn plan_escalated_layout(analysis: &Analysis, spec: &Spec) -> Result<Plan, Invalid> {
    let bound = crate::recognition_corrections::canonical(analysis, spec)?;
    let spec = bound.as_ref();
    // The Spec's datums and controls merged into the PMI reading, once, so every later use of the
    // Analysis (and the planning memo, keyed by its address) sees them ([`crate::spec_gdt`]).
    let merged = crate::spec_gdt::effective(analysis, spec)?;
    let analysis = merged.as_ref();
    crate::planning::with(analysis, || {
        if spec.sheet.scale.is_none() && crate::dims::has_new_native_recess_dimensions(analysis) {
            established_drawing(analysis, spec)?;
        }
        let mut plan = plan_escalated_with(analysis, spec, false, None, false)?;
        if !crate::planning::nominals_deferred() {
            finish_nominal_plan(analysis, spec, &mut plan);
        }
        crate::iso_caption::refresh(analysis, &mut plan);
        Ok(plan)
    })
}

// Internal preflight refusal; only the optional-detail search requests this mode.
const NO_AUTOMATIC_DETAIL: &str = "no automatic detail survived this candidate";

fn plan_escalated_with(
    analysis: &Analysis,
    spec: &Spec,
    automatic_pair: bool,
    candidate_arrangement: Option<layout::Arrangement>,
    require_automatic: bool,
) -> Result<Plan, Invalid> {
    let automatic_pair = automatic_pair || crate::planning::is_fixed_pair();
    let analysis_key: [u8; 32] =
        crate::planning::memo(analysis, "analysis_digest", Vec::new(), || {
            // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
            Sha256::digest(serde_json::to_vec(analysis).expect("Analysis serializes")).into()
        });
    let key = serde_json::to_vec(&(
        analysis_key,
        crate::planning::policy_key(analysis),
        spec,
        automatic_pair,
        candidate_arrangement.map(|a| a.name()),
        require_automatic,
    ))
    // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
    .expect("candidate inputs serialize");
    crate::planning::memo(analysis, "candidate", key.clone(), || {
        // Reconstructing an immutable stateless parent revisits these exact candidates.
        // Keep owned results under all their inputs; the allocator and recovery still
        // execute their normal prefixes and validity checks for every planner invocation.
        static MEMO: crate::planning::BoundedMemo<Result<Plan, Invalid>> =
            crate::planning::BoundedMemo::new(64);
        let digest: [u8; 32] = Sha256::digest(&key).into();
        if let Some(plan) = MEMO.get(&digest) {
            return plan;
        }
        let plan = plan_escalated_uncached(
            analysis,
            spec,
            automatic_pair,
            candidate_arrangement,
            require_automatic,
        );
        MEMO.insert(digest, plan.clone());
        plan
    })
}

fn plan_escalated_uncached(
    analysis: &Analysis,
    spec: &Spec,
    automatic_pair: bool,
    candidate_arrangement: Option<layout::Arrangement>,
    require_automatic: bool,
) -> Result<Plan, Invalid> {
    let escalated = |spec: &Spec, beyond_lanes: bool, internal: bool| {
        plan_with(
            analysis,
            spec,
            beyond_lanes,
            automatic_pair || internal,
            candidate_arrangement,
            true,
            require_automatic,
        )
        .and_then(|mut p| {
            let selection = crate::hole_table::select(analysis, &p);
            if selection.tables.is_empty() && spec.hole_table == crate::hole_table::Setting::Always
                && p.dimensions.iter().any(|d| d.measurements.iter().any(|m| m.parameter == "bore.diameter")) {
                // About the holes, not the sheet (#25): its subjects are the hole callouts kept.
                let callouts = p.dimensions.iter().filter(|d| d.measurements.iter().any(|m| m.parameter == "bore.diameter")).map(|d| d.id.clone()).collect();
                p.problems.push(Problem { id:"p:hole_table_unavailable:sheet".into(), code:"hole_table_unavailable".into(), severity:"warning".into(), message:"A hole table needs holes located in X and Y from a datum corner. This part has no holes located that way; use the individual hole dimensions shown instead.".into(), subjects:callouts, ..Default::default() });
            }
            if !selection.tables.is_empty() {
                let mut scheduled = install_schedules(analysis, &p, &selection, |selection| {
                    plan_with_presentation(
                        analysis,
                        spec,
                        beyond_lanes,
                        automatic_pair || internal,
                        candidate_arrangement,
                        ViewPolicy { complete: true, require_detail: require_automatic },
                        Some(selection),
                    )
                })?;
                // Measured source corridors may outgrow the estimated sheet. An explicit
                // schedule request still deserves a transaction on larger automatic paper,
                // at the same scale, before falling back to the original dimensions.
                if scheduled.is_none()
                    && spec.hole_table == crate::hole_table::Setting::Always
                    && spec.sheet.size.is_none()
                    && !crate::placement_intent::has_pins(spec)
                {
                    let first = layout::PAGES.iter().position(|(name, ..)| *name == p.sheet.size)
                        // Invariant: The planned sheet size was selected from the fixed paper table.
                        .expect("planned paper is standard");
                    for (name, ..) in layout::PAGES.iter().skip(first + 1) {
                        let mut larger = spec.clone();
                        larger.sheet.size = Some((*name).into());
                        let candidate = install_schedules(analysis, &p, &selection, |selection| {
                            plan_with_presentation(
                                analysis, &larger, beyond_lanes, automatic_pair,
                                candidate_arrangement, ViewPolicy { complete: true, require_detail: require_automatic }, Some(selection),
                            )
                        })?;
                        if let Some(mut candidate) = candidate {
                            candidate.spec.clone_from(&p.spec);
                            candidate.sheet.default = true;
                            candidate.sheet.basis += "; larger automatic paper retains the requested schedule and measured corridors";
                            scheduled = Some(candidate);
                            break;
                        }
                    }
                }
                if let Some(scheduled) = scheduled {
                    p = scheduled;
                } else if spec.hole_table == crate::hole_table::Setting::Always || findings(&p).dropped > 0 {
                    // A failed optional compaction is not a new drawing problem when
                    // every original hole requirement remains legible. Explicit requests
                    // and actual losses still report the failed schedule transaction.
                    p.problems.push(Problem { id:"p:hole_table_unavailable:sheet".into(), code:"hole_table_unavailable".into(), severity:if spec.hole_table == crate::hole_table::Setting::Always { "warning" } else { "info" }.into(), message:"The selected hole table or one of its tags has no clear place; the original dimensions are kept.".into(), subjects:vec!["sheet".into()], ..Default::default() });
                }
            }
            p.table_outcome = crate::hole_table::escalate(analysis, &mut p,
                spec.sheets.as_ref().and_then(|s| s.hole_table).filter(|n| *n > 1));
            if p.dimensions.iter().any(|d| d.measurements.iter().any(|m| m.feature.starts_with("f:section_recess:")))
                && p.problems.iter().any(|q| matches!(q.code.as_str(), "annotation_outside_area" | "page_fit_uncertain" | "views_do_not_fit"))
                && let Some(packed) = crate::sheet_fit::pack_gdt(analysis, &p, &p)
            {
                p = packed;
            }
            if let Some(stable) = crate::dims::retain_established(analysis, spec, &p) {
                p = stable;
            }
            crate::completeness::report(analysis, &mut p);
            p.sheet.fill = crate::sheet_fit::fill(analysis, &p);
            if let Some((basis, _)) = p.sheet.basis.rsplit_once("; drawn-item fill ") {
                p.sheet.basis = format!("{basis}; drawn-item fill {:.1}%", p.sheet.fill * 100.0);
            }
            Ok(p)
        })
    };
    let first = match escalated(spec, false, false) {
        Err(e) if require_automatic && e.message == NO_AUTOMATIC_DETAIL => {
            return escalated(spec, true, false);
        }
        result => result?,
    };
    let misplaced = gdt_misplaced(&first);
    let shared = !first.gdt.is_empty()
        && first.dimensions.iter().any(|d| {
            d.measurements
                .iter()
                .any(|m| m.feature.starts_with("f:section_recess:"))
        });
    let grade = |p: &Plan| {
        let g = gdt_misplaced(p);
        (g.0, if shared { findings(p).hard } else { 0 }, g.1)
    };
    if misplaced == (0, 0) && (!shared || findings(&first).hard == 0) {
        return Ok(first);
    }
    let again = escalated(spec, true, false)?;
    let mut best = if grade(&again) < grade(&first) {
        again
    } else {
        first.clone()
    };
    // New annotation ink can consume a frame's old corridor. Compare a bounded
    // reduction on the same paper before keeping a frame over a view or beyond
    // its band. Every measurement and placed GD&T item must survive the trial.
    if spec.sheet.scale.is_none() && shared {
        let required = carried(&best);
        for _ in 0..2 {
            let Some(scale) = layout::SCALES.iter().copied().find(|s| {
                *s < best.sheet.scale_factor
                    && *s >= crate::sheet_fit::ABSOLUTE_FLOOR
                    && crate::sheets::floor(spec).is_none_or(|floor| *s >= floor)
            }) else {
                break;
            };
            let mut trial_spec = spec.clone();
            trial_spec.sheet.size = Some(best.sheet.size.clone());
            trial_spec.sheet.scale = Some(scale_name(scale));
            let mut trial = escalated(&trial_spec, true, true)?;
            trial.spec.clone_from(&best.spec);
            trial.sheet.default = spec.sheet.size.is_none();
            trial.sheet.basis = format!(
                "automatic reduction on {} to {} to keep the GD&T in its corridors; drawn-item fill {:.1}%",
                trial.sheet.size,
                trial.sheet.scale,
                trial.sheet.fill * 100.0
            );
            if grade(&trial) >= grade(&best)
                || findings(&trial).hard > findings(&best).hard
                || drops(&trial) > drops(&best)
                || undrawn_gdt(&trial) > undrawn_gdt(&best)
                || !required.is_subset(&carried(&trial))
            {
                break;
            }
            best = trial;
            if gdt_misplaced(&best) == (0, 0) && findings(&best).hard == 0 {
                break;
            }
        }
    }
    if grade(&best) < grade(&first) {
        best.sheet.basis += &format!(
            "; the GD&T's room reserved beyond every dimension lane of its strips; the shared corridors \
             improve on {} at {} while retaining every carried measurement and frame",
            first.sheet.size, first.sheet.scale,
        );
    }
    Ok(best)
}

/// A plan's feature control frames, datum feature symbols and PMI labels off the sheet or over
/// another view (`annotation_off_sheet`, `annotation_over_view`, `annotation_line_over_view`),
/// at warning or error.
fn gdt_misplaced(plan: &Plan) -> (usize, usize) {
    let ids: BTreeSet<&str> = plan.gdt.iter().map(|g| g.id.as_str()).collect();
    let mut found = (0, 0);
    for p in &plan.problems {
        if p.severity == "info" || !p.subjects.iter().any(|s| ids.contains(s.as_str())) {
            continue;
        }
        match p.code.as_str() {
            "annotation_off_sheet" | "annotation_over_view" | "annotation_line_over_view" => {
                found.0 += 1
            }
            "annotation_ink_overlap" | "annotation_overlap" | "annotation_outside_area" => {
                found.1 += 1
            }
            _ => {}
        }
    }
    // Never trade ink off the sheet or over another view for fewer label collisions.
    found
}

/// Plans the sheet: which views, where, at what scale and on what sheet, as draftwright's layout
/// does ([`layout`]): each principal view is a block of its box and the annotation bands reserved
/// around it, packed third-angle; the isometric takes the largest space left. Each choice the
/// spec leaves open is marked as a default, with the reason for it.
///
/// The bands, the turned-part view set and section A–A come from the features
/// ([`crate::demand`]); the height ladder is sized for the steps legible at the scale chosen.
/// The scale and sheet are draftwright's first choice, before it re-plans on its finished
/// drawing's findings. That recovery is [`recover`], applied by [`plan_recovered`], never here:
/// its tries are plans made by this function (ISSUE-1299-a).
pub fn plan(analysis: &Analysis, spec: &Spec) -> Result<Plan, Invalid> {
    crate::decision_overrides::boundary(|| {
        let plan = crate::bom::with_titles(analysis, spec, || {
            layout::with_gutter(
                spec.sheet.frame_gutter.unwrap_or(layout::FRAME_GUTTER),
                || {
                    let plan = plan_once(analysis, spec)?;
                    let mut plan =
                        manufacturing_fallback(analysis, spec, plan, |s| plan_once(analysis, s))?;
                    crate::graphical_pmi::apply(analysis, &mut plan);
                    crate::hole_exits::report(analysis, &mut plan);
                    plan.record_callout_offsets();
                    Ok(plan)
                },
            )
        })?;
        retain_unapplied_edits(analysis, plan, self::plan)
    })
}

fn plan_once(analysis: &Analysis, spec: &Spec) -> Result<Plan, Invalid> {
    let prepared = crate::modelled_threads::planning(analysis, spec);
    let mut plan = crate::dims::pmi_policy::planning(|| plan_inner(&prepared, spec))?;
    crate::half_section::apply(&prepared, &mut plan);
    crate::modelled_threads::source_hash(
        analysis,
        matches!(&prepared, std::borrow::Cow::Owned(_)),
        &mut plan,
    );
    crate::sheets::furnish(&mut plan);
    crate::fixes::retain_local_layout(&mut plan);
    crate::dims::contacts::report(&prepared, &mut plan);
    note_final_conflicts(&mut plan);
    crate::characteristics::apply(analysis, &mut plan);
    Ok(plan)
}

/// The request without the Spec's omissions (every automatic item, none left out), when it has
/// any: the drawing an omission must leave every other item of.
fn natural_spec(spec: &Spec) -> Option<Spec> {
    if spec.dimension_source != DimensionSource::Authored
        && !spec.dimensions.values().any(|d| d.omit)
    {
        return None;
    }
    let mut full = spec.clone();
    full.dimension_source = DimensionSource::Auto;
    for d in full.dimensions.values_mut() {
        d.omit = false;
    }
    full.dimensions
        .retain(|_, d| *d != DimensionSpec::default());
    Some(full)
}

fn plan_inner(analysis: &Analysis, spec: &Spec) -> Result<Plan, Invalid> {
    if spec.sheet.keep_layout == Some(true) {
        return plan_recovered(analysis, spec);
    }
    if spec.pmi == PmiMode::Ignore && crate::dims::pmi_policy::carries(analysis) {
        return crate::dims::pmi_policy::ignore(analysis, spec, plan);
    }
    crate::planning::with(analysis, || {
        crate::dims::spaced(spec, || {
            let mut plan = crate::dims::idle_decided(analysis, spec, || {
                plan_with(analysis, spec, false, false, None, false, false)
            })?;
            finish_nominal_plan(analysis, spec, &mut plan);
            let natural =
                natural_spec(spec).and_then(|full| crate::model::plan(analysis, &full).ok());
            crate::dims::callout_routes::prefer_interior(
                analysis,
                spec,
                &mut plan,
                natural.as_ref(),
            );
            crate::dims::finish_openings(analysis, spec, &mut plan);
            crate::dims::callout_routes::finish(analysis, spec, &mut plan);
            crate::dims::hole_marks::attach(analysis, spec, &mut plan);
            crate::dims::size_datums::attach(analysis, spec, &mut plan);
            if crate::dims::openings_need_settling(&plan) {
                crate::dims::finish_attached_openings(analysis, spec, &mut plan);
                crate::dims::hole_marks::attach(analysis, spec, &mut plan);
                crate::dims::size_datums::attach(analysis, spec, &mut plan);
                crate::dims::finish_attached_openings(analysis, spec, &mut plan);
                crate::dims::callout_routes::finish_frames(analysis, spec, &mut plan);
            }
            crate::dims::gdt::cited_undrawn(&mut plan);
            if !crate::placement_intent::has_pins(spec) {
                plan.record_dimension_labels();
            }
            crate::dims::pmi_policy::audit(analysis, spec, &mut plan);
            crate::fixes::attach(analysis, &mut plan);
            Ok(plan)
        })
    })
}

/// [`plan`], the GD&T's room reserved beyond every dimension lane of its strips when
/// *gdt_beyond_lanes* ([`crate::demand::Demand::gdt_beyond_lanes`]).
fn plan_with(
    analysis: &Analysis,
    spec: &Spec,
    gdt_beyond_lanes: bool,
    automatic_pair: bool,
    candidate_arrangement: Option<layout::Arrangement>,
    complete_views: bool,
    require_automatic: bool,
) -> Result<Plan, Invalid> {
    plan_with_presentation(
        analysis,
        spec,
        gdt_beyond_lanes,
        automatic_pair,
        candidate_arrangement,
        ViewPolicy {
            complete: complete_views,
            require_detail: require_automatic,
        },
        None,
    )
}

#[derive(Clone, Copy)]
struct ViewPolicy {
    complete: bool,
    require_detail: bool,
}

fn plan_with_presentation(
    analysis: &Analysis,
    spec: &Spec,
    gdt_beyond_lanes: bool,
    automatic_pair: bool,
    candidate_arrangement: Option<layout::Arrangement>,
    policy: ViewPolicy,
    presentation: Option<&crate::hole_table::Selection>,
) -> Result<Plan, Invalid> {
    crate::part_choice::check(analysis, spec)?;
    let ViewPolicy {
        complete: complete_views,
        require_detail: require_automatic,
    } = policy;
    let excluded = presentation
        .map(|p| &p.replaced)
        .cloned()
        .unwrap_or_default();
    // A document of a version this build does not know is refused, never read as the nearest
    // one it does (ADR3-4).
    if !ANALYSIS_SCHEMAS.contains(&analysis.schema.as_str()) {
        return Err(invalid(
            format!(
                "The analysis is {:?}, which this draftwright does not read; analyse the STEP file again.",
                analysis.schema
            ),
            &ANALYSIS_SCHEMAS,
        ));
    }
    let spec = &crate::recognition_corrections::bind(analysis, migrate(spec, analysis)?)?;
    if spec.sheet.preferred_max_sheets == Some(0) {
        return Err(invalid(
            "sheet/preferred_max_sheets must be at least 1.",
            &["1", "2"],
        ));
    }
    if spec
        .sheet
        .preferred_max_size
        .as_deref()
        .is_some_and(|v| !layout::PAGES.iter().any(|p| p.0 == v))
    {
        return Err(invalid(
            "sheet/preferred_max_size must be A4, A3, A2, A1 or A0.",
            &layout::PAGES.map(|p| p.0),
        ));
    }
    crate::dims::authored::check(spec)?;
    crate::dimension_scheme::check(spec)?;
    crate::dims::pmi_policy::validate(analysis, spec)?;
    if spec
        .iso_scale_factor
        .is_some_and(|s| !s.is_finite() || s <= 0.0)
    {
        return Err(invalid(
            "iso_scale_factor must be finite and positive.",
            &[],
        ));
    }
    if spec
        .sheet
        .view_gutter
        .is_some_and(|g| !valid_view_gutter(g))
    {
        return Err(invalid(
            "sheet/view_gutter must be from 0 to 1189 paper mm.",
            &[],
        ));
    }
    let merged = crate::spec_gdt::effective(analysis, spec)?;
    let analysis = merged.as_ref();
    crate::sheets::validate(spec)?;
    crate::detail::check(analysis, spec)?;
    crate::spec_notes::check_claims(analysis, spec)?;
    crate::dims::check_views(spec)?;
    crate::placement_intent::check(spec)?;
    crate::label_text::check(spec)?;
    title_block::check(&spec.sheet.title)?;
    crate::notes::check(&spec.notes)?;
    crate::tables::check(&spec.tables)?;
    crate::gears::check(&spec.gears)?;
    let mut problems = Vec::new();
    // The spec the plan follows: across a revision, without the entries that cannot be trusted.
    let canonical = if crate::placement_intent::has_pins(spec)
        && let Some(published) = crate::planning::pin_reference()
    {
        Some(published)
    } else if crate::placement_intent::has_pins(spec) {
        let mut base = pin_base(spec);
        // Naming an already shown principal view must not change a no-op view
        // pin's canonical composition. New radius inventories can make the
        // explicitly named-view search choose another iso arrangement (1000).
        // Keep names only when they actually restore a view the default omits.
        if !base.views.is_empty()
            && spec
                .views
                .values()
                .any(|v| v.place.as_ref().is_some_and(|p| p.pinned))
        {
            let mut unnamed = base.clone();
            unnamed.views.clear();
            unnamed.views.keep_sections_from(&spec.views);
            unnamed.views.keep_breaks_from(&spec.views);
            if let Ok(default) = plan_automatic(analysis, &unnamed)
                && base
                    .views
                    .keys()
                    .all(|name| default.views.iter().any(|v| v.id == format!("v:{name}")))
            {
                base.views.clear();
                base.views.keep_sections_from(&spec.views);
                base.views.keep_breaks_from(&spec.views);
            }
        }
        // A multi-sheet request is planned by R5 allocation steps whose sheet-1 spec omits
        // the moved views; its canonical is that allocation, not a single-sheet layout.
        if crate::planning::is_fixed_pair()
            || spec
                .sheets
                .as_ref()
                .and_then(|s| s.max)
                .is_some_and(|n| n > 1)
        {
            // A trial's omitted automatic crops belong to its canonical frame too.
            // Reintroducing them here moves the peers of an otherwise bare pin.
            // Pins do not belong to this reference, nor do allocation destinations: a
            // multi-sheet reference is the whole allocated drawing, with every sheet-1
            // frame where it finally sits, not one allocation step's sheet 1.
            base.views = crate::sheets::reference_views(spec);
            // A paper trial's sheet-count permission shapes its R5 allocation, so pin
            // offsets in a multi-sheet canonical are measured on that same allocation.
            if let Some(max) = spec.sheets.as_ref().and_then(|s| s.max).filter(|&n| n > 1) {
                base.sheets = Some(crate::sheets::SheetsSpec {
                    max: Some(max),
                    ..Default::default()
                });
            }
        }
        // Pins in recovered planning replay the finished recovered drawing,
        // including precision repairs, even in its first unexpanded candidate.
        let replay = |base: &Spec| {
            if complete_views || crate::planning::nominals_deferred() {
                let mut finished = plan_automatic(analysis, base)?;
                finish_nominal_plan(analysis, base, &mut finished);
                // A bare pin holds the published route, including final-inventory
                // interior/GD&T repair, rather than an intermediate paper trial.
                crate::dims::callout_routes::prefer_interior(analysis, base, &mut finished, None);
                crate::dims::finish_openings(analysis, base, &mut finished);
                crate::dims::callout_routes::finish(analysis, base, &mut finished);
                // The pinned plan starts from these frames and attaches its hole glyphs again,
                // so it takes their reservations before attaching grew them (#224).
                let areas: Vec<_> = finished.views.iter().map(|v| v.area).collect();
                crate::dims::hole_marks::attach(analysis, base, &mut finished);
                crate::dims::size_datums::attach(analysis, base, &mut finished);
                if crate::dims::openings_need_settling(&finished) {
                    crate::dims::finish_attached_openings(analysis, base, &mut finished);
                    crate::dims::hole_marks::attach(analysis, base, &mut finished);
                    crate::dims::size_datums::attach(analysis, base, &mut finished);
                    crate::dims::finish_attached_openings(analysis, base, &mut finished);
                    crate::dims::callout_routes::finish_frames(analysis, base, &mut finished);
                }
                for (v, area) in finished.views.iter_mut().zip(areas) {
                    v.area = area;
                }
                crate::dims::gdt::cited_undrawn(&mut finished);
                Ok(finished)
            } else {
                plan(analysis, base)
            }
        };
        let mut canonical = replay(&base)?;
        if spec.sheet.references != Some(true)
            && spec.dimensions.iter().any(|(id, d)| {
                d.place.as_ref().is_some_and(|p| p.pinned)
                    && canonical.omitted.iter().any(|o| {
                        &o.id == id
                            && o.basis
                                .contains("other stated dimensions determine this interval")
                    })
            })
        {
            // A pin on an auxiliary dimension is explicit intent to keep its historical ink,
            // even when the sheet no longer opts into references or the chain was edited.
            base.sheet.references = Some(true);
            canonical = replay(&base)?;
        }
        Some(canonical)
    } else if spec.sheet.scale.is_none() && annotation_text_edited(spec) {
        // Tolerance, fit and wording edits change annotation ink, not the part projections.
        // Keep authored geometry in the complete unedited frame too.
        let mut base = spec.clone();
        base.envelope_tolerance = None;
        // Text is laid out before optional final gutters. Reusing a spaced frame
        // here can exhaust a label's border clearance before its ink is settled.
        base.sheet.view_gutter = Some(0.0);
        for d in base.dimensions.values_mut() {
            d.tolerance = None;
            d.fit = None;
            d.text = None;
            d.style = None;
        }
        for f in base.features.values_mut() {
            f.fit = None;
        }
        // Clearing the edit must produce the actual unedited specification.
        // Empty entries otherwise disable established-layout retention.
        base.dimensions
            .retain(|_, d| *d != DimensionSpec::default());
        base.features.retain(|_, f| *f != FeatureSpec::default());
        Some(if complete_views {
            plan_automatic(analysis, &base)?
        } else {
            plan(analysis, &base)?
        })
    } else {
        None
    };
    let mut effective = spec.clone();
    if let Some(base) = &canonical {
        effective.sheet.size = Some(base.sheet.size.clone());
        effective.sheet.scale = Some(base.sheet.scale.clone());
        {
            // Pins and annotation text edits retain the recovered detail choice.
            // Recovery may reject an optional crop in favour of complete principal views.
            // These edits keep recovered frames, so they must also keep that detail choice:
            // adding the seed's crop back would place it in space the canonical iso owns.
            // Explicitly authored details still enter through their own requests.
            for (name, detail) in
                crate::detail::requests(analysis, &effective, base.sheet.scale_factor)
            {
                if !detail.authored && !base.views.iter().any(|v| v.id == format!("v:{name}")) {
                    effective.views.entry(name.clone()).or_default().omit = true;
                    // A refused crop is still a request the drawing could not satisfy.
                    // Keep its canonical diagnostic, so releasing a pin cannot appear to
                    // introduce a new refusal that was already present before the edit.
                    problems.extend(
                        base.problems
                            .iter()
                            .filter(|p| {
                                p.code == "detail_unplaceable"
                                    && p.refs.contains(&format!("v:{name}"))
                            })
                            .cloned(),
                    );
                }
            }
        }
        for (id, d) in &mut effective.dimensions {
            // #12: an offset that puts the bend where the canonical plan has it pins the
            // automatic placement, so it is planned as a bare pin: the ink stays as it is.
            if let Some(p) = d.place.as_mut()
                && p.view.as_ref().or(d.view.as_ref()).is_none_or(|view| {
                    crate::placement_intent::carrier(base, id).as_deref()
                        == Some(format!("v:{view}").as_str())
                })
                && p.offset
                    .is_some_and(|o| crate::placement_intent::automatic_offset(base, id, o))
            {
                p.offset = None;
            }
            if let Some(p) = d.place.as_mut()
                && p.pinned
                && p.view.is_none()
            {
                // ADR1-8: an item the canonical plan leaves to another (a boss height conveyed
                // by a hole callout in the view down the hole) is pinned in its own default view,
                // not its owner's, which may not see it across.
                let covered = base
                    .omitted
                    .iter()
                    .any(|o| &o.id == id && matches!(o.state, crate::items::State::Covered { .. }));
                p.view = d.view.clone().or_else(|| {
                    crate::placement_intent::carrier(base, id)
                        .filter(|_| !covered)
                        .map(|v| v.trim_start_matches("v:").into())
                });
            }
        }
        for (id, d) in &mut effective.annotations {
            let held = base.gdt.iter().find(|d| d.id == *id);
            if let Some(p) = d.place.as_ref()
                && p.pinned
                && let Some(g) = held.filter(|g| crate::dims::hole_marks::attached(g))
                && (crate::placement_intent::automatic_pin(p)
                    || p.view.as_deref() == Some(g.view.trim_start_matches("v:")))
            {
                // Opening-lane finishing reattaches glyphs after settling the
                // new rectangular-notch inventory. Hold the published carrier
                // through that pass; other inventories retain the existing pin semantics.
                d.place = base
                    .dimensions
                    .iter()
                    .any(|d| {
                        d.measurements
                            .iter()
                            .any(|m| m.feature.starts_with("f:rectangular_through_notch:"))
                    })
                    .then(|| crate::placement_intent::Place {
                        pinned: true,
                        view: Some(g.view.trim_start_matches("v:").into()),
                        ..Default::default()
                    });
            } else if let Some(p) = d.place.as_mut()
                && p.pinned
                && p.view.is_none()
            {
                p.view = held.map(|d| d.view.trim_start_matches("v:").into());
            }
        }
        effective
            .annotations
            .retain(|_, a| *a != Default::default());
    }
    let mut inactive = Vec::new();
    if let Some(b) = &spec.binding
        && b.source_sha256 != analysis.binding.source_sha256
    {
        // Until rebinding (ADR R3) lands, a feature's ID is rank-based and may name another
        // feature in this revision: its entries are kept in the spec but inactive (R3 invariant
        // 3), and listed. The sheet, the views and the envelope's dimensions carry over.
        let inactive_entries: Vec<(&str, &String)> = spec
            .dimensions
            .keys()
            .filter(|id| id.starts_with("d:f:") || id.starts_with("d:pmi:"))
            .map(|id| ("dimensions", id))
            .chain(spec.features.keys().map(|id| ("features", id)))
            .chain(spec.faces.keys().map(|id| ("faces", id)))
            .chain(spec.annotations.keys().map(|id| ("annotations", id)))
            .collect();
        inactive = inactive_entries
            .iter()
            .map(|(_, id)| id.to_string())
            .collect::<Vec<_>>();
        effective
            .dimensions
            .retain(|id, _| !id.starts_with("d:f:") && !id.starts_with("d:pmi:"));
        effective
            .precision
            .retain(|id, _| !id.starts_with("d:f:") && !id.starts_with("d:pmi:"));
        effective.annotations.clear();
        effective.features.clear();
        effective.faces.clear();
        problems.push(Problem {
            id: "p:rebind:part".into(),
            code: "spec_from_other_revision".into(),
            severity: "info".into(),
            message: format!(
                "The spec was made for a different revision of {}; its sheet, view and envelope dimension choices are carried over.",
                b.part
            ),
            subjects: vec![],
            ..Default::default()
        });
        if !inactive.is_empty() {
            problems.push(Problem {
                crossing_kinds: Vec::new(),
                ink_is_leader: None,
                id: "p:spec_entries_inactive:part".into(),
                code: "spec_entries_inactive".into(),
                severity: "warning".into(),
                message: format!(
                    "The spec was made for a different revision of {}, and its {} entr{} for features and PMI (this problem's refs) cannot be matched to this one's until revisions are rebound: they are kept in the spec but not applied, and the spec stays bound to its revision while they remain. Remove them (then set what is wanted by this revision's IDs), or edit the spec's binding to this revision's to apply them as they are.",
                    b.part,
                    inactive.len(),
                    if inactive.len() == 1 { "y" } else { "ies" }
                ),
                subjects: Vec::new(),
                refs: inactive.clone(),
                fixes: Vec::new(),
                // The message's first remedy: the entries removed, the spec binds to this
                // revision.
                fix: inactive_entries
                    .iter()
                    .map(|(at, id)| {
                        crate::items::Op::remove(format!("{at}/{}", crate::items::key(id)))
                    })
                    .collect(),
            });
        }
    }
    // An assembly drawn whole (no part chosen): its parts are drawn together as one, as they
    // always were (the maintainer's D4), and the plan says how to draw one alone.
    if !analysis.parts.is_empty() && analysis.binding.part_index.is_none() {
        problems.push(Problem {
            id: "p:assembly_drawn_whole:part".into(),
            code: "assembly_drawn_whole".into(),
            severity: "warning".into(),
            message: format!(
                "{} is an assembly of {} parts ({}); they are drawn together as one. To draw one part alone, choose it by its index: --part K on the command line, or \"part\": K in a request.",
                analysis.binding.part,
                analysis.parts.len(),
                part_list(&analysis.parts)
            ),
            subjects: Vec::new(),
            refs: analysis.parts.iter().map(|p| p.name.clone()).collect(),
            ..Default::default()
        });
    }
    // An assembly's principal part drawn alone (#219): the other parts are not drawn.
    if analysis.principal
        && spec.part.is_none()
        && let Some(k) = analysis.binding.part_index
    {
        let file = analysis
            .source_stem
            .as_ref()
            .unwrap_or(&analysis.binding.part);
        let others = analysis
            .parts
            .iter()
            .filter(|p| p.part != k)
            .map(|p| format!("{}: {}", p.part, p.name))
            .collect::<Vec<_>>()
            .join(", ");
        let placements = match analysis.parts.get(k).map_or(1, |p| p.instances) {
            1 => String::new(),
            n => format!(" (one of its {n} placements)"),
        };
        problems.push(Problem {
            id: "p:assembly_principal_drawn:part".into(),
            code: "assembly_principal_drawn".into(),
            severity: "warning".into(),
            message: format!(
                "{file} is an assembly of {} parts; its largest, part {k} ({}){placements}, holds the others within its box and is drawn alone, so they ({others}) are neither drawn nor dimensioned as its features; a general note on the sheet says so (annotations/{}/omit leaves it out). To draw another part, choose it by its index: --part K on the command line, or \"part\": K in a request.",
                analysis.parts.len(),
                analysis.binding.part,
                crate::notes::ASSEMBLY_NOTE
            ),
            subjects: Vec::new(),
            refs: analysis.parts.iter().map(|p| p.name.clone()).collect(),
            ..Default::default()
        });
    }
    // A part slightly off the file's axes, drawn square in its own frame (#216).
    if let Some(squared) = &analysis.squared {
        problems.push(Problem {
            id: "p:part_squared_to_axes:part".into(),
            code: "part_squared_to_axes".into(),
            severity: "info".into(),
            message: format!(
                "{} lies {:.2}° off the file's axes, within {SQUARE_WITHIN}° of them, so it is drawn and dimensioned square, in its own frame: its sizes are its own, not a tilted box's.",
                analysis.binding.part, squared.angle
            ),
            subjects: Vec::new(),
            refs: Vec::new(),
            ..Default::default()
        });
    }
    // Bodies on top of another (#212): dropped when the part they copy was drawn alone in
    // their place; otherwise drawn with it, so each of their features is counted again.
    if !analysis.coincident.is_empty() {
        let bodies = analysis
            .coincident
            .iter()
            .map(|c| {
                let placed = analysis.parts.get(c.part).is_some_and(|p| p.instances > 1);
                if placed {
                    format!("{}: {} (placement {})", c.part, c.name, c.placement)
                } else {
                    format!("{}: {}", c.part, c.name)
                }
            })
            .collect::<Vec<_>>()
            .join(", ");
        let file = analysis
            .source_stem
            .as_ref()
            .unwrap_or(&analysis.binding.part);
        let (severity, message) = match analysis.binding.part_index {
            Some(k) => (
                "info",
                format!(
                    "Every body of {file} coincides with part {k} ({}), which is drawn alone: the copies ({bodies}) are not drawn, so their features are not counted again. --part K on the command line, or \"part\": K in a request, draws part K instead.",
                    analysis.binding.part
                ),
            ),
            // A file of one part has no other part to choose: its part 0 is the whole file.
            None if analysis.parts.is_empty() => (
                "warning",
                format!(
                    "{file} has bodies that coincide with another ({bodies}); the file is drawn whole, so their features are counted once per copy."
                ),
            ),
            None => (
                "warning",
                format!(
                    "{file} has bodies that coincide with another ({bodies}); the file is drawn whole, so their features are counted once per copy. To draw one part alone, choose it by its index: --part K on the command line, or \"part\": K in a request."
                ),
            ),
        };
        problems.push(Problem {
            id: "p:coincident_bodies:part".into(),
            code: "coincident_bodies".into(),
            severity: severity.into(),
            message,
            subjects: Vec::new(),
            refs: analysis.coincident.iter().map(|c| c.name.clone()).collect(),
            ..Default::default()
        });
    }
    if !analysis.unrecognised.is_empty() {
        let mut known = Vec::new();
        let mut unknown = Vec::new();
        for u in &analysis.unrecognised {
            let family = u.family.replace('_', " ");
            match u.records {
                Some(n) => {
                    let name = if n == 1 {
                        if let Some(stem) = family.strip_suffix("ies") {
                            format!("{stem}y")
                        } else if family.ends_with("sses") {
                            family.trim_end_matches("es").to_string()
                        } else {
                            family.strip_suffix('s').unwrap_or(&family).to_string()
                        }
                    } else {
                        family.clone()
                    };
                    known.push(format!("{n} {name}"));
                }
                None => unknown.push(family),
            }
        }
        let mut message = String::new();
        if !known.is_empty() {
            let consequence = if analysis
                .unrecognised
                .iter()
                .filter_map(|u| u.records)
                .eq([1])
            {
                "This feature is"
            } else {
                "These features are"
            };
            message = format!(
                "Unrecognised features on this part: {}. {consequence} not dimensioned, sectioned or checked. ",
                known.join(", ")
            );
        }
        if !unknown.is_empty() {
            message += &format!(
                "Recognition does not cover {}; whether this part has any is unknown. ",
                unknown.join(", ")
            );
        }
        message += "If a missed feature matches a supported type, declare it in the Spec's feature_declarations using its face fingerprints. Otherwise, add the required dimensions and review it manually.";
        let mut refs: Vec<String> = analysis
            .unrecognised
            .iter()
            .map(|u| u.family.clone())
            .collect();
        if let Some(r) = &analysis.recognition {
            refs.push(r.recogniser.clone());
        }
        refs.push("Python quiddity's recognition (tools/recognise.py) gives these families".into());
        problems.push(Problem {
            id: "p:features_not_recognised:part".into(),
            code: "features_not_recognised".into(),
            severity: "warning".into(),
            message,
            subjects: Vec::new(),
            refs,
            ..Default::default()
        });
    }
    // Read from the title, declarations and PMI mode, which the placement memo leaves out of its
    // key.
    problems.extend(crate::dims::refused_threads(analysis, spec));
    problems.extend(crate::dims::possible_threads(analysis, spec));
    if !effective.annotations.is_empty() {
        let mut lowered = crate::pmi_lowering::lower(analysis, &|_| true).0;
        crate::label_text::annotations(&mut lowered, &effective)?;
        let mut annotation_ids: Vec<String> = lowered.into_iter().map(|i| i.id).collect();
        annotation_ids.extend(
            crate::pmi_lowering::sheet_requirements(analysis)
                .0
                .into_iter()
                .filter(|r| matches!(r.kind, "document_note" | "no_requirement"))
                .map(|r| r.id),
        );
        annotation_ids.extend(crate::dims::blends::note_ids(analysis));
        annotation_ids.extend(crate::dims::size_datums::ids(analysis));
        if crate::notes::assembly_note(analysis).is_some() {
            annotation_ids.push(crate::notes::ASSEMBLY_NOTE.into());
        }
        let missing = effective
            .annotations
            .keys()
            .filter(|id| !annotation_ids.contains(id))
            .map(|id| {
                (id, Invalid {
                    path: None,
                    also: Vec::new(),
                    message: format!(
                        "There is no annotation {id:?} on this part. Choose a current annotation ID, or remove annotations/{}.",
                        crate::items::key(id)
                    ),
                    choices: if annotation_ids.is_empty() {
                        vec![format!("remove annotations/{}", crate::items::key(id))]
                    } else {
                        annotation_ids.clone()
                    },
                }
                .at("annotations", id))
            });
        for (id, e) in missing {
            // Stale PMI and automatic-note identities can return after a revision. An
            // untyped name cannot identify either kind of annotation safely.
            if !["pmi:", "n:"].into_iter().any(|prefix| {
                id.strip_prefix(prefix)
                    .is_some_and(|identity| !identity.is_empty())
            }) {
                return Err(e);
            }
            problems.push(Problem {
                id: format!(
                    "p:annotation_edit_unapplied:{}",
                    e.path.as_deref().unwrap_or("unknown")
                ),
                code: "annotation_edit_unapplied".into(),
                severity: "warning".into(),
                message: "An annotation named by the saved Spec no longer exists; its edit is retained but cannot be applied.".into(),
                subjects: e.path.into_iter().collect(),
                ..Default::default()
            });
        }
    }
    let given = spec;
    let spec = &effective;
    let unknown = spec
        .views
        .keys()
        .filter(|name| {
            *name != SECTION
                && !spec.views.sections.iter().any(|r| r.view_name() == Some(name.as_str()))
                && !crate::detail::NAMES.contains(&name.as_str())
                && !VIEWS.iter().any(|(v, ..)| v == *name)
        })
        .map(|name| {
            let mut names = VIEWS.map(|v| v.0).to_vec();
            names.push(SECTION);
            invalid(
                if name.starts_with("v:") {
                    // The MCP's bounded correction recognises this established diagnostic.
                    format!("There is no view called {name:?}.")
                } else {
                    format!(
                        "There is no view called {name:?}; replace views/{name} with a listed view name, or remove it."
                    )
                },
                &names,
            )
            .at("views", name)
        });
    if let Some(e) = Invalid::first_of(unknown) {
        return Err(e);
    }
    crate::dims::check_known(analysis, spec)?;
    let mut demand = crate::demand::read(analysis);
    let named_section = if spec.views.sections.is_empty() {
        None
    } else {
        crate::sections::resolve(analysis, spec, &mut problems)?
    };
    let named_request = named_section
        .as_ref()
        .and_then(|_| spec.views.sections.first());
    if let Some(named) = named_section {
        demand.section = Some(named);
    }
    if spec.views.get(SECTION).is_some_and(|v| !v.omit) && demand.section.is_none() {
        demand.section = crate::demand::explicit_section(
            &demand.holes,
            0.5 * (analysis.envelope[0][1] + analysis.envelope[1][1]),
        );
    }
    // The analysis's demand assumes the default Spec; edited wording or datum visibility
    // (`sheet/uncited_datums`, a datum's omit) changes which symbols need room.
    if spec
        .annotations
        .values()
        .any(|a| a.text.is_some() || a.omit)
        || spec.sheet.uncited_datums.is_some()
    {
        demand.gdt = [
            crate::dims::gdt::demand_with_spec(analysis, spec, &|_| true),
            crate::dims::gdt::demand_with_spec(analysis, spec, &|v| v != "side"),
        ];
    }
    demand.gdt_beyond_lanes = gdt_beyond_lanes;
    demand.callout_bands = crate::dims::callout_bands(analysis, spec)?;
    // A turned part drops the view that repeats its profile, unless the spec asks for it, or
    // omits the profile view it keeps (front): the dropped view is then the only profile left;
    // or a PMI item is drawn in it (draftwright's `annotation_view_required`).
    let omitted = |name: &str| spec.views.get(name).is_some_and(|v| v.omit);
    let pmi_views = crate::pmi_lowering::views(analysis);
    let reduction = crate::demand::turned_views(&demand).filter(|(kept, dropped, _)| {
        !spec.views.contains_key(*dropped)
            && !omitted(kept[0])
            && !pmi_views.contains(dropped)
            && !(demand.section.is_some() && *dropped == "plan")
            // The single-pass estimate exposes missing-view diagnostics. The
            // automatic build preflights every requirement before projection,
            // as Python's builder does before accepting a reduced candidate.
            && (!complete_views
                || crate::dims::reduction_preserves_requirements(analysis, spec, kept, &excluded))
    });
    // A part that is not turned leaves out a plan or side view that adds nothing (#222).
    let reduction = reduction
        .or_else(|| crate::dims::idle_view(analysis, spec, &demand))
        .filter(|(_, dropped, _)| {
            !spec.dimensions.values().any(|d| {
                !d.omit
                    && (d.between.is_some() || spec.dimension_source == DimensionSource::Authored)
                    && d.place
                        .as_ref()
                        .and_then(|p| p.view.as_deref())
                        .or(d.view.as_deref())
                        == Some(*dropped)
            })
        });
    let shown = |name: &str| match spec.views.get(name) {
        Some(v) => !v.omit,
        None => !reduction.as_ref().is_some_and(|r| r.1 == name),
    };
    let principals = Principals {
        front: shown("front"),
        plan: shown("plan"),
        side: shown("side"),
    };
    // The bands hold the lanes the dimensions will take in the views shown (review M3).
    let shown_views: Vec<(&str, [f64; 3], [f64; 3])> = VIEWS
        .iter()
        .filter(|(name, ..)| shown(name))
        .copied()
        .collect();
    let (lanes, aligned) = if !automatic_pair
        && !gdt_beyond_lanes
        && spec.sheet.size.is_none()
        && spec.sheet.scale.is_none()
        && analysis
            .recognition
            .as_ref()
            .is_some_and(|r| !r.recogniser.starts_with("quiddity-rust "))
    {
        crate::dims::initial_lanes(analysis, &shown_views, &excluded, spec)?
    } else {
        crate::dims::lanes_excluding(analysis, &shown_views, &excluded, spec)?
    };
    demand.lanes = lanes;
    crate::dims::reserve_linear_intent(analysis, spec, &shown_views, &excluded, &mut demand.lanes)?;
    // Full printed extents reserve ink, but an authored linear tolerance must not recenter
    // every projection and its unrelated dimensions (ARCH0003-2). Keep the established
    // linear anchor bands separately; the placed blocks below still use the full extents.
    let linear_anchor_lanes = if spec
        .dimensions
        .values()
        .any(|d| d.tolerance.is_some() || d.text.is_some() || d.style.is_some())
    {
        let mut nominal = spec.clone();
        // This internal frame measures nominal ink, with all explicit bands removed.
        nominal.envelope_tolerance = None;
        for d in nominal.dimensions.values_mut() {
            d.tolerance = None;
            d.text = None;
            d.style = None;
        }
        let (before, _) =
            crate::dims::lanes_excluding(analysis, &shown_views, &excluded, &nominal)?;
        let mut anchors = demand.lanes;
        anchors.plate_front_top = before.plate_front_top;
        anchors.plate_side_top = before.plate_side_top;
        (anchors != demand.lanes).then_some(anchors)
    } else {
        None
    };

    if !(principals.front || principals.plan || principals.side) {
        return Err(invalid(
            "Every principal view is omitted; a sheet needs front, plan or side.",
            &["front", "plan", "side"],
        ));
    }
    let page = match &spec.sheet.size {
        Some(s) => Some(
            layout::PAGES
                .iter()
                .find(|(n, ..)| n == s)
                .map(|(_, w, h)| [*w, *h])
                .ok_or_else(|| {
                    invalid(
                        format!("{s:?} is not a sheet size."),
                        &layout::PAGES.map(|p| p.0),
                    )
                })?,
        ),
        None => None,
    };
    let scale = match &spec.sheet.scale {
        Some(s) => Some(
            layout::SCALES
                .iter()
                .copied()
                .find(|k| scale_name(*k) == *s)
                .ok_or_else(|| {
                    let names = layout::SCALES.map(scale_name);
                    invalid(
                        format!("{s:?} is not a standard scale."),
                        &names.iter().map(String::as_str).collect::<Vec<_>>(),
                    )
                })?,
        ),
        None => None,
    };
    // ARCH0004-3: the geometry floor is unconditional for an explicit scale.
    if let Some(scale) =
        scale.filter(|_| complete_views && given.sheet.scale.is_some() && !automatic_pair)
    {
        let smallest = (0..3)
            .map(|k| analysis.envelope[1][k] - analysis.envelope[0][k])
            .fold(f64::INFINITY, f64::min);
        if smallest <= 0.0 {
            return Err(invalid(
                "drawing geometry degenerates: the part has no three-dimensional extent",
                &[],
            ));
        }
        if smallest * scale < 0.1 {
            problems.push(Problem { id: "p:geometry_below_resolution:sheet".into(), code: "geometry_below_resolution".into(), severity: "warning".into(), message: format!("scale {} projects the smallest part dimension to {:.3} mm, below 0.1 mm drawing resolution; the requested scale is retained.", scale_name(scale), smallest * scale), subjects: vec!["sheet".into()], ..Default::default() });
        }
    }
    // ISO 5456-2's two methods: third angle (draftwright's) unless the spec says first.
    let first_angle = match spec.sheet.projection.as_deref() {
        None | Some("third") => false,
        Some("first") => true,
        Some(p) => {
            return Err(invalid(
                format!("{p:?} is not a projection method."),
                &PROJECTIONS,
            ));
        }
    };

    let page = page.or_else(|| presentation.map(|p| p.page));
    // A schedule removes annotation demand; it must not shrink or enlarge the proven feature ink.
    let scale = presentation.map(|p| p.scale).or(scale);
    let [lo, hi] = analysis.envelope;
    // Sections are marked on the plan; explicit cuts reserve a separate labelled frame.
    let section_label = named_request
        .map(|r| r.id.clone())
        .unwrap_or_else(|| crate::detail::automatic_section_label(spec));
    let section_name = crate::sections::view_name(&section_label).unwrap_or(SECTION);
    let section = demand.section.clone().filter(|_| {
        shown(section_name) && principals.plan && (principals.front || principals.side)
    });
    // A section left out is said, with why and what would bring it back (draftwright records
    // every section decision: ISSUE-1190-b); one the author omits needs no saying.
    let section_not_shown = |why: String, fix: Vec<crate::items::Op>| Problem {
        crossing_kinds: Vec::new(),
        ink_is_leader: None,
        id: format!("p:section_not_shown:v:{section_name}"),
        code: "section_not_shown".into(),
        severity: "warning".into(),
        message: format!("Section {section_label}\u{2013}{section_label} is not drawn: {why}."),
        subjects: Vec::new(),
        refs: vec![format!("v:{section_name}")],
        fixes: Vec::new(),
        fix,
    };
    match &demand.section {
        Some(_) if section.is_none() && shown(section_name) => problems.push(if !principals.plan {
            let (how, fix) = if omitted("plan") {
                (
                    "the spec omits it; remove views/plan/omit to show it",
                    vec![crate::items::Op::remove("views/plan/omit")],
                )
            } else {
                (
                    "it is left out as the turned part's repeated profile; set views/plan to {} to keep it",
                    Vec::new(),
                )
            };
            section_not_shown(
                format!(
                    "a z-axis hole with a counterbore, spotface or bottom needs it, but its cutting plane is marked on the plan view, and {how}"
                ),
                fix,
            )
        } else {
            section_not_shown(
                "it sits in the row of the front and side views, and the spec omits both; remove views/front/omit or views/side/omit to show it".into(),
                vec![crate::items::Op::remove("views/front/omit")],
            )
        }),
        None if spec.views.get(SECTION).is_some_and(|v| !v.omit) => problems.push(section_not_shown(
            "rejected views/section_aa: no front-facing cutting plane can be named from the recognised geometry (a z-axis hole or internal detail is needed); the request and the remaining drawing are retained".into(),
            vec![crate::items::Op::remove(format!("views/{SECTION}"))],
        )),
        Some(_) if section.is_none() && named_request.is_some() => problems.push(section_not_shown(
            format!("rejected views/sections/0: views/{section_name}/omit displaces the requested section; the request and remaining drawing are retained"),
            vec![crate::items::Op::remove(format!("views/{section_name}/omit"))],
        )),
        _ => {}
    }
    let mut request = Request::new(
        [hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]],
        principals,
        shown("iso"),
    );
    let breaks = crate::broken::resolve(analysis, spec, &mut problems);
    request.breaks = breaks
        .iter()
        .map(|(name, b)| (name.clone(), b.axis, b.remove[1] - b.remove[0], b.paper_gap))
        .collect();
    request.aligned = aligned;
    request.automatic_pair = automatic_pair;
    request.candidate_arrangement = candidate_arrangement;
    request.first_angle = first_angle;
    request.sections = usize::from(section.is_some());
    request.separate_section =
        named_request.is_some() || spec.views.get(SECTION).is_some_and(|v| !v.omit);
    // GD&T corridors depend on the aligned projection row. Keep their established
    // packing; a section-only lift must not change its readable scale or frame routes.
    // Items left of the front and plan column face no section, so they leave it free.
    request.section_row_fixed = demand
        .gdt
        .iter()
        .flatten()
        .any(|g| !(matches!(g.view, "front" | "plan") && g.side == "left"));
    // The tables are reserved towards their corners and the general notes block top right, in
    // draftwright's order, unless only a fallback would make room for them: then each is placed
    // where it can be, or reported (crate::tables, crate::notes).
    // Part PMI notes precede the drawing's own notes; reconciliation never changes the Spec.
    let (mut requirements, more) = crate::pmi_lowering::sheet_requirements(analysis);
    crate::process::defaults(analysis, spec, &mut requirements);
    problems.extend(more);
    let (mut notes_given, omitted_notes, more) =
        crate::notes::reconcile(&requirements, analysis, spec);
    problems.extend(more);
    let feature_note_ids: BTreeSet<String> = requirements.iter().filter(|requirement| {
        matches!(&analysis.pmi, Some(crate::pmi::Reading::Read(report)) if report.records.iter().any(|record| record.source_id == requirement.source && record.kind == "flag_note"))
    }).map(|r| r.id.clone()).collect();
    for note in &mut notes_given {
        if (note.id == crate::notes::ASSEMBLY_NOTE || feature_note_ids.contains(&note.id))
            && let Some(text) = spec.annotations.get(&note.id).and_then(|a| a.text.as_ref())
        {
            note.text = text
                .terms(&note.id, std::slice::from_ref(&note.text))?
                .join(" ");
        }
    }
    let mut radius_notes = if crate::dims::pmi_policy::strict(analysis, spec) {
        Vec::new()
    } else {
        crate::dims::blends::notes(analysis)
    };
    for (note, _) in &mut radius_notes {
        if let Some(text) = spec.annotations.get(&note.id).and_then(|a| a.text.as_ref()) {
            note.text = text
                .terms(&note.id, std::slice::from_ref(&note.text))?
                .join(" ");
        }
    }
    if spec.dimension_source == DimensionSource::Authored {
        radius_notes.retain(|(n, _)| spec.annotations.contains_key(&n.id));
    }
    notes_given.extend(
        radius_notes
            .iter()
            .filter(|(n, _)| {
                !(spec.dimension_source == DimensionSource::Authored
                    && !spec.annotations.contains_key(&n.id))
                    && !spec.annotations.get(&n.id).is_some_and(|s| {
                        s.omit
                            || s.place.as_ref().is_some_and(|p| {
                                p.side.is_some() || p.lane.is_some() || p.along.is_some()
                            })
                    })
            })
            .map(|(n, _)| n.clone()),
    );
    let notes_size = crate::notes::size(
        &notes_given
            .iter()
            .filter(|n| !radius_notes.iter().any(|(r, _)| r.id == n.id))
            .cloned()
            .collect::<Vec<_>>(),
    );
    request.iso_scale_factor = spec.iso_scale_factor;
    request.required_tables =
        crate::tables::sizes_on(&spec.tables, spec, page.unwrap_or([1189.0, 841.0]));
    request.required_tables.extend(crate::tables::sizes_on(
        &crate::gears::tables(analysis),
        spec,
        page.unwrap_or([1189.0, 841.0]),
    ));
    if let Some(p) = presentation {
        request
            .required_tables
            .extend(p.sizes(spec, page.unwrap_or(p.page)));
    }
    request.required_tables.extend(crate::tables::sizes_on(
        &crate::springs::tables(analysis),
        spec,
        page.unwrap_or([1189.0, 841.0]),
    ));
    // The part's manufacturing requirements table, when its threads and knurls are referred to
    // one (crate::pmi_lowering::manufacturing), before the notes, as draftwright reserves it.
    request.required_tables.extend(
        crate::tables::manufacturing_size_on(
            &crate::pmi_lowering::manufacturing_for(analysis, spec).rows(),
            spec,
            page.unwrap_or([1189.0, 841.0]),
        )
        .map(|s| (s, Corner::TopRight)),
    );
    request
        .required_tables
        .extend(notes_size.map(|s| (s, Corner::TopRight)));
    // The height ladder is sized for the steps legible at the scale it leads to.
    let (mut pick, mut strips, _) = crate::demand::size_with_details(
        &demand,
        &request,
        scale,
        page,
        lo[2],
        spec.views.values().any(|v| v.detail.is_some()),
        &|s| crate::detail::scaled(analysis, spec, s),
    );
    if !request.required_tables.is_empty() && pick.fallback.is_some() {
        let mut bare = request.clone();
        bare.required_tables.clear();
        let (p, s, _) = crate::demand::size_with_details(
            &demand,
            &bare,
            scale,
            page,
            lo[2],
            spec.views.values().any(|v| v.detail.is_some()),
            &|s| crate::detail::scaled(analysis, spec, s),
        );
        if p.fallback.is_none() {
            (request, pick, strips) = (bare, p, s);
        }
    }
    // An automatic radius schedule is a presentation choice. Reserve it around
    // the existing sheet/scale pair only when it fits; it cannot spend more paper
    // or shrink a user-requested scale while its ordinary callout remains valid.
    let radius_sizes = crate::tables::sizes_on(
        &crate::dims::blends::reservations(analysis, spec),
        spec,
        pick.page,
    );
    if !radius_sizes.is_empty() {
        let mut with_radii = request.clone();
        with_radii.required_tables.extend(radius_sizes);
        with_radii.automatic_pair = true;
        let (p, s, _) = crate::demand::size_with_details(
            &demand,
            &with_radii,
            Some(pick.scale),
            Some(pick.page),
            lo[2],
            false,
            &|scale| crate::detail::scaled(analysis, spec, scale),
        );
        if p.fallback.is_none() && p.page == pick.page && (p.scale - pick.scale).abs() < 1e-12 {
            with_radii.automatic_pair = request.automatic_pair;
            (request, pick, strips) = (with_radii, p, s);
        }
    }
    if complete_views && given.sheet.scale.is_some() && !automatic_pair {
        let smallest = request.size.into_iter().fold(f64::INFINITY, f64::min);
        if smallest * pick.scale < 10.0 {
            let (auto, _, _) = crate::demand::size(&demand, &request, None, page, lo[2]);
            if auto.fallback.is_none() && smallest * auto.scale >= 10.0 {
                problems.push(Problem {
                    id: "p:legibility_floor_breached:sheet".into(),
                    code: "legibility_floor_breached".into(),
                    severity: "warning".into(),
                    message: format!("scale {} projects the smallest part dimension to {:.1} mm, below the 10 mm legibility floor; annotations may crowd. Honouring the requested scale; use scale ≥ {} or omit the scale for automatic selection.", scale_name(pick.scale), smallest * pick.scale, 10.0 / smallest),
                    subjects: vec!["sheet".into()],
                    fix: vec![crate::items::Op::remove("sheet/scale")],
                    ..Default::default()
                });
            }
        }
    }
    request.strips = strips;
    let page_name = |page: [f64; 2]| {
        layout::PAGES
            .iter()
            .find(|(_, w, h)| [*w, *h] == page)
            .map_or_else(|| format!("{}x{}", page[0], page[1]), |p| p.0.into())
    };
    let size = page_name(pick.page);
    let scale = scale_name(pick.scale);
    match pick.fallback {
        Some(Fallback::Computed) => problems.push(Problem {
            id: "p:scale_fallback_applied:sheet".into(),
            code: "scale_fallback_applied".into(),
            severity: "warning".into(),
            message: format!(
                "No standard scale fits the views on {size}; the largest that fits, {scale}, is used."
            ),
            subjects: vec!["sheet".into()],
            ..Default::default()
        }),
        Some(Fallback::Overflow) => problems.push(Problem {
            id: "p:views_do_not_fit:sheet".into(),
            code: "views_do_not_fit".into(),
            severity: "error".into(),
            message: format!("The views do not fit {size} at {scale}."),
            subjects: vec!["sheet".into()],
            ..Default::default()
        }),
        Some(Fallback::Uncertain) => problems.push(Problem {
            id: "p:page_fit_uncertain:sheet".into(),
            code: "page_fit_uncertain".into(),
            severity: "warning".into(),
            message: format!("The views may not fit {size} at {scale}, as the spec asks."),
            subjects: vec!["sheet".into()],
            ..Default::default()
        }),
        None => {}
    }
    // A fallback is said as what it is, never as the rule that found nothing.
    let basis = match (pick.fallback, &spec.sheet.size, &spec.sheet.scale) {
        (Some(Fallback::Computed), None, _) => format!(
            "past draftwright's ladder, as no sheet and standard scale fits the views and their annotation bands: the largest scale that fits the largest sheet, {size}, found by bisection: {scale}, not a standard scale"
        ),
        (Some(Fallback::Computed), Some(_), _) => format!(
            "no standard scale fits the views on {size}: the largest scale that fits, found by bisection: {scale}, not a standard scale"
        ),
        (Some(Fallback::Overflow), _, None) => format!(
            "nothing fits: no scale, standard or computed, fits the views on {size}; {size} at {scale} is used, and the views do not fit"
        ),
        (Some(Fallback::Overflow), _, Some(_)) => format!(
            "no sheet holds the views at {scale}: the largest, {size}, is used, and the views do not fit"
        ),
        (Some(Fallback::Uncertain), ..) => "as the spec asks; the views may not fit".into(),
        (None, None, None) => format!(
            "the first sheet and scale on draftwright's ladder (every enlargement on the smallest sheet first, then reductions) whose views and the annotation bands their features need fit: {size} at {scale}"
        ),
        (None, Some(_), None) => {
            format!("the largest standard scale at which the views fit {size}")
        }
        (None, None, Some(_)) => format!("the smallest sheet that holds the views at {scale}"),
        (None, Some(_), Some(_)) => "as the spec asks".into(),
    };

    let authored_details = spec.views.values().any(|v| v.detail.is_some());
    let mut scaled_details = if pick.fallback.is_some() && !authored_details {
        // The demand search refused optional details at this pair. Do not compose them
        // again against the smaller bare reservation and displace established furniture.
        layout::Scaled::default()
    } else {
        crate::detail::scaled(analysis, spec, pick.scale)
    };
    let mut g = layout::geometry(
        &request,
        &scaled_details,
        pick.scale,
        pick.page,
        pick.tb_w,
        pick.arrangement,
        None,
    );
    if g.derived.len() != scaled_details.derived.len() && !authored_details {
        // A refused optional crop must release every reservation and phantom strip.
        scaled_details = layout::Scaled::default();
        g = layout::geometry(
            &request,
            &scaled_details,
            pick.scale,
            pick.page,
            pick.tb_w,
            pick.arrangement,
            None,
        );
    }
    if let Some(lanes) = linear_anchor_lanes {
        let anchors = crate::demand::Demand {
            lanes,
            ..demand.clone()
        };
        let (_, strips, _) =
            crate::demand::size(&anchors, &request, Some(pick.scale), Some(pick.page), lo[2]);
        let anchored_request = Request {
            strips,
            ..request.clone()
        };
        // Anchor the detail-aware layout: a bare frame would reclaim crop reservations
        // for the iso and undo the principal spacing chosen with those crops present.
        let frame = layout::geometry(
            &anchored_request,
            &scaled_details,
            pick.scale,
            pick.page,
            pick.tb_w,
            pick.arrangement,
            None,
        );
        let shift = [
            frame.section[0] - g.section[0],
            frame.section[1] - g.section[1],
        ];
        for b in &mut g.section_boxes {
            for k in 0..2 {
                b[k] += shift[k];
                b[k + 2] += shift[k];
            }
        }
        (g.front, g.plan, g.side, g.section) = (frame.front, frame.plan, frame.side, frame.section);
        (g.iso, g.iso_centre) = (frame.iso, frame.iso_centre);
    }
    let ib = analysis.drawing_iso_bounds().map(|b| b * pick.scale);
    let iso_factor = spec.iso_scale_factor.unwrap_or_else(|| {
        layout::iso_factor_with_detail(g.iso, g.iso_centre, ib, !g.derived.is_empty())
    });
    if shown("iso") && spec.iso_scale_factor.is_some() && analysis.drawing_iso_bounds() != [0.0; 4]
    {
        let b = [
            g.iso_centre[0] + ib[0] * iso_factor,
            g.iso_centre[1] + ib[1] * iso_factor,
            g.iso_centre[0] + ib[2] * iso_factor,
            g.iso_centre[1] + ib[3] * iso_factor,
        ];
        if g.iso[2] > g.iso[0]
            && g.iso[3] > g.iso[1]
            && (b[0] < g.iso[0] - 1e-6
                || b[1] < g.iso[1] - 1e-6
                || b[2] > g.iso[2] + 1e-6
                || b[3] > g.iso[3] + 1e-6)
        {
            if automatic_pair {
                return Err(invalid(INFEASIBLE_ISO_SCALE, &[]));
            }
            problems.push(Problem {
                id: "p:iso_layout_overflow:v:iso".into(),
                code: "iso_layout_overflow".into(),
                severity: "warning".into(),
                message: INFEASIBLE_ISO_SCALE.into(),
                subjects: vec!["v:iso".into()],
                ..Default::default()
            });
        }
    }
    if shown("iso") && analysis.drawing_iso_bounds() == [0.0; 4] {
        problems.push(Problem {
            id: "p:iso_projection_empty:v:iso".into(),
            code: "iso_projection_empty".into(),
            severity: "warning".into(),
            message: "The isometric projection of the part has no edges, so the isometric cannot be fitted to its space: it is planned at the sheet's scale and draws nothing. Omit it with views/iso/omit.".into(),
            subjects: vec!["v:iso".into()],
            fix: vec![crate::items::Op::set("views/iso/omit", serde_json::json!(true))],
            ..Default::default()
        });
    }
    let turned = |name: &str| match &reduction {
        Some((kept, dropped, why)) if kept.contains(&name) => {
            format!("; {why}, so the {dropped} view is left out")
        }
        _ => String::new(),
    };
    let mut views: Vec<PlannedView> = VIEWS
        .iter()
        .filter(|(name, ..)| shown(name))
        .map(|(name, direction, up)| {
            let [fv, pv, sv] = g.blocks;
            let (at, area, scale, basis) = match *name {
                "front" => (
                    g.front,
                    fv.footprint(g.front[0], g.front[1]),
                    pick.scale,
                    "the principal view, its box and annotation bands packed from the lower left"
                        .to_string(),
                ),
                "plan" => (
                    g.plan,
                    pv.footprint(g.plan[0], g.plan[1]),
                    pick.scale,
                    if first_angle {
                        "first-angle: the plan sits below the front, past both views' bands"
                    } else {
                        "third-angle: the plan sits above the front, past both views' bands"
                    }
                    .into(),
                ),
                "side" => (
                    g.side,
                    sv.footprint(g.side[0], g.side[1]),
                    pick.scale,
                    if first_angle {
                        "first-angle: the right side sits left of the front, past both views' bands"
                    } else {
                        "third-angle: the right side sits right of the front, past both views' bands"
                    }
                    .into(),
                ),
                _ => (
                    g.iso_centre,
                    g.iso,
                    pick.scale * iso_factor,
                    if iso_factor == 1.0 {
                        "the isometric, centred in the largest space the other views leave".into()
                    } else {
                        format!(
                            "the isometric, centred in the largest space the other views leave and scaled ×{iso_factor} to fill it (not to scale)"
                        )
                    },
                ),
            };
            PlannedView {
                detail: None,
                sheet: 1, scale_note: None, xref: None,
                id: format!("v:{name}"),
                direction: *direction,
                up: *up,
                at,
                scale,
                area,
                default: !spec.views.contains_key(*name),
                basis: basis + &turned(name),
                state: ItemState::Shown,
                cut: None,
                half_section: None,
                broken: None,
                line_work: Vec::new(),
                line_style: Default::default(), from_spec: Vec::new(),
            }
        })
        .collect();
    for v in &mut views {
        if let Some(b) = breaks.get(v.id.trim_start_matches("v:")) {
            let scaled = b.at_scale(v.scale);
            if scaled.reduction() > 0. {
                v.broken = Some(scaled);
                v.from_spec.push(b.source.clone());
            } else {
                crate::broken::not_applied(
                    &mut problems,
                    v.id.trim_start_matches("v:"),
                    &b.source,
                    "the removed interval is shorter on paper than the requested gap at this scale",
                );
            }
        }
    }
    if let (Some(cut), Some(area)) = (&section, g.section_boxes.first()) {
        let label = section_label.clone();
        views.push(PlannedView {
            detail: None,
            sheet: 1, scale_note: None, xref: None,
            id: format!("v:{section_name}"),
            direction: [0.0, -1.0, 0.0],
            up: [0.0, 0.0, 1.0],
            at: g.section,
            scale: pick.scale,
            area: *area,
            default: !spec.views.contains_key(SECTION) && named_request.is_none(),
            basis: if let Some(r) = named_request { format!("applied views/sections/0 through {}: longitudinal plane y = {}, seen from the front; displaced automatic section selection", r.through, fmt_ratio(cut.cut_y)) } else { format!(
                "draftwright's section rule: {}; the plane y = {} cuts the densest row ({} physical details on it), seen from the front and placed right of the side view",
                if spec.views.get(SECTION).is_some_and(|v| !v.omit) { "applied views/section_aa: explicit section through recognised geometry; displaced the automatic decision whether to show a section" } else if cut.internal_detail { "at least three unlike internal details need a readable section" } else { "a z-axis hole with a counterbore, spotface or bottom shows its profile in no standard view" },
                fmt_ratio(cut.cut_y),
                cut.holes
            ) },
            state: ItemState::Shown,
            line_work: Vec::new(),
            line_style: Default::default(), from_spec: if named_request.is_some() { vec!["views/sections/0".into()] } else if spec.views.contains_key(SECTION) { vec![format!("views/{SECTION}")] } else { Vec::new() },
            half_section: None,
                broken: None,
            cut: Some(Cut {
                xref: None,
                label,
                origin: [0.5 * (lo[0] + hi[0]), cut.cut_y, 0.5 * (lo[2] + hi[2])],
                normal: [0.0, 1.0, 0.0],
                marked_in: "v:plan".into(),
            }),
        });
    } else if section.is_some() {
        problems.push(section_not_shown(
            "the layout made no place for it on the sheet".into(),
            Vec::new(),
        ));
    }
    for (name, mut detail) in crate::detail::requests(analysis, spec, pick.scale) {
        let source = views.iter().find(|v| v.id == detail.source_view);
        if detail.authored && source.is_none() {
            return Err(invalid(
                format!(
                    "Authored detail {name}: source view {} does not exist in the plan for {}.",
                    detail.source_view,
                    // Invariant: Authored detail requests are emitted only from views with detail metadata.
                    spec.views[&name].detail.as_ref().unwrap().around_ids()
                ),
                &[],
            ));
        }
        let area = g
            .derived
            .iter()
            .find(|(id, _)| *id == name)
            .map(|(_, b)| *b);
        if let (Some(source), Some(area)) = (source, area) {
            detail.source_cut = source.cut.clone();
            views.push(PlannedView {
                sheet: 1,
                scale_note: None,
                xref: None,
                id: format!("v:{name}"),
                direction: source.direction,
                up: source.up,
                at: crate::detail::at(&detail, area),
                scale: detail.scale,
                area,
                default: !detail.authored,
                basis: crate::detail::basis(analysis, spec, &name, &detail),
                state: ItemState::Shown,
                line_work: Vec::new(),
                cut: None,
                half_section: None,
                broken: None,
                line_style: Default::default(),
                from_spec: Vec::new(),
                detail: Some(detail),
            });
        } else {
            problems.push(Problem {id:format!("p:detail_unplaceable:v:{name}"),code:"detail_unplaceable".into(),severity:"warning".into(),message:format!("Detail {name} has insufficient layout space for its crop at scale {}; its dimensions remain on the parent.", scale_name(detail.scale)),refs:vec![format!("v:{name}")],..Default::default()});
        }
    }
    if require_automatic
        && !views
            .iter()
            .any(|v| v.detail.as_ref().is_some_and(|d| !d.authored))
    {
        return Err(invalid(NO_AUTOMATIC_DETAIL, &[]));
    }
    if demand.lanes.recess_lanes
        && spec.iso_scale_factor.is_none()
        && spec.views.get("iso").is_none_or(|v| v.place.is_none())
        && let Some(i) = views.iter().position(|v| v.id == "v:iso")
    {
        let v = &views[i];
        let bounds = [
            v.at[0] + v.scale * analysis.drawing_iso_bounds()[0],
            v.at[1] + v.scale * analysis.drawing_iso_bounds()[1],
            v.at[0] + v.scale * analysis.drawing_iso_bounds()[2],
            v.at[1] + v.scale * analysis.drawing_iso_bounds()[3],
        ];
        let [w, h] = pick.page;
        let page = [
            request.margins.left + 0.1,
            request.margins.bottom + 0.1,
            w - request.margins.right - 0.1,
            h - request.margins.top - 0.1,
        ];
        let mut delta = [0.0; 2];
        let mut fits = true;
        for k in 0..2 {
            let lo = page[k] - bounds[k];
            let hi = page[k + 2] - bounds[k + 2];
            if lo > hi {
                fits = false;
                break;
            }
            delta[k] = 0.0f64.clamp(lo, hi);
        }
        let moved = [
            bounds[0] + delta[0],
            bounds[1] + delta[1],
            bounds[2] + delta[0],
            bounds[3] + delta[1],
        ];
        if fits
            && delta != [0.0; 2]
            && !rect_overlap(moved, g.title_block)
            && views
                .iter()
                .enumerate()
                .all(|(j, other)| j == i || !rect_overlap(moved, other.area))
        {
            views[i].at[0] += delta[0];
            views[i].at[1] += delta[1];
            views[i].basis += "; translated inside the drawing area at its existing scale";
        }
    }
    let mut title = spec.sheet.title.clone();
    title
        .entry("title".into())
        .or_insert_with(|| crate::title_block::default_title(analysis));
    title
        .entry("number".into())
        .or_insert_with(|| crate::title_block::default_number(analysis));
    // The file's one usable material, unless the spec states one, even blank (draftwright's
    // `_resolve_title_document_defaults`).
    if let Some(crate::pmi::Reading::Read(r)) = &analysis.pmi
        && let Some(m) = r.title_material()
    {
        if let Some(v) = spec.sheet.title.get("material")
            && v.trim() != m.designation.trim()
        {
            problems.push(Problem {
                id: format!("p:step_material_disagreement:{}", m.source_id),
                code: "step_material_disagreement".into(),
                severity: "warning".into(),
                message: format!(
                    "The title block uses the Spec's material {v:?} (sheet/title/material), which wins; the STEP material {} states {:?}. The two values disagree.",
                    m.source_id, m.designation
                ),
                subjects: vec!["sheet".into()],
                refs: vec![m.source_id.clone(), "sheet/title/material".into()],
                ..Default::default()
            });
        }
        title
            .entry("material".into())
            .or_insert_with(|| m.designation.trim().to_string());
    }
    let (mut requirements, more) = crate::pmi_lowering::plan_sheet(
        &requirements,
        &spec.sheet.title,
        &mut title,
        g.title_block,
    );
    problems.extend(more);
    let title_cells = title_block::cells(g.title_block, &title, &size);
    problems.extend(title_block::problems(&title_cells, pick.page));
    let mut sheet = PlannedSheet {
        number: 1,
        zones: None,
        fill: 0.0,
        size,
        page: pick.page,
        scale,
        scale_factor: pick.scale,
        arrangement: pick.arrangement.name().into(),
        title_block: g.title_block,
        default: given.sheet.size.is_none() || given.sheet.scale.is_none(),
        basis,
        state: ItemState::Shown,
        title,
        title_cells,
        projection_symbol: Some(title_block::projection(g.title_block, first_angle)),
        dimension_spacing: if crate::dims::compact() {
            DimensionSpacing::Compact
        } else {
            DimensionSpacing::Normal
        },
    };
    if let Some(base) = &canonical {
        sheet.page = base.sheet.page;
        sheet.scale_factor = base.sheet.scale_factor;
        sheet.title_block = base.sheet.title_block;
        sheet.basis = base.sheet.basis.clone()
            + if dimension_text_edited(spec) {
                "; view frames and sheet choice retained for annotation text edits"
            } else {
                "; anchor frames and sheet choice kept fixed for placement pins"
            };
        sheet.title_cells = title_block::cells(sheet.title_block, &sheet.title, &sheet.size);
        sheet.projection_symbol = Some(title_block::projection(sheet.title_block, first_angle));
    }
    if canonical.is_some() || spec.views.values().any(|v| v.place.is_some()) {
        crate::placement_intent::views(
            spec,
            &mut views,
            canonical.as_ref(),
            &sheet,
            &[],
            &mut problems,
        );
    }
    if let Some(presentation) = presentation
        && let Some(base) = &presentation.iso
        && spec.views.get("iso").is_none_or(|v| v.place.is_none())
        && let Some(v) = views.iter_mut().find(|v| v.id == "v:iso")
    {
        v.scale = base.scale;
        let outside = (0..2).any(|k| {
            v.at[k] + v.scale * analysis.drawing_iso_bounds()[k] < margin()
                || v.at[k] + v.scale * analysis.drawing_iso_bounds()[k + 2]
                    > sheet.page[k] - margin()
        });
        // Bare pins retain their canonical frame. Holding an automatic scale must not leave
        // its newly packed centre outside the page; in that case retain the proven frame too.
        if let Some(base) = canonical
            .as_ref()
            .and_then(|p| p.views.iter().find(|v| v.id == "v:iso"))
        {
            (v.at, v.area, v.scale) = (base.at, base.area, base.scale);
        } else if outside {
            (v.at, v.area, v.scale) = (base.at, base.area, base.scale);
        }
    }
    crate::dims::gdt::validate_placements(analysis, spec, &views)?;
    // Different arrangement requests can resolve to identical geometry. Solve that ink once,
    // retaining each request's sheet and view explanations in its returned Plan.
    let reusable = canonical.is_none()
        && crate::planning::enabled(analysis)
        && !crate::placement_intent::has_pins(spec);
    let key = if reusable {
        let mut geometry_sheet = sheet.clone();
        geometry_sheet.basis.clear();
        geometry_sheet.arrangement.clear();
        let mut geometry_views = views.clone();
        for view in &mut geometry_views {
            view.basis.clear();
        }
        serde_json::to_vec(&(
            spec,
            given,
            crate::planning::policy_key(analysis),
            &geometry_sheet,
            &geometry_views,
            &problems,
            &excluded,
            &requirements,
            &notes_given,
            &radius_notes,
            &inactive,
            presentation.map(|p| p.reserve_origins),
        ))
        // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
        .expect("resolved candidate inputs serialize")
    } else {
        Vec::new()
    };
    let sheet_basis = sheet.basis.clone();
    let arrangement = sheet.arrangement.clone();
    let view_bases: Vec<_> = views.iter().map(|v| v.basis.clone()).collect();
    if reusable
        && let Some(mut plan) =
            crate::planning::lookup::<Plan>(analysis, "resolved_candidate", &key)
    {
        plan.sheet.basis = format!(
            "{sheet_basis}; drawn-item fill {:.1}%",
            plan.sheet.fill * 100.0
        );
        plan.sheet.arrangement = arrangement;
        for (view, basis) in plan.views.iter_mut().zip(view_bases) {
            view.basis = basis;
        }
        if require_automatic
            && !plan
                .views
                .iter()
                .any(|v| v.detail.as_ref().is_some_and(|d| !d.authored))
        {
            return Err(invalid(NO_AUTOMATIC_DETAIL, &[]));
        }
        crate::linework::configure(spec, &mut plan.views);
        return Ok(plan);
    }
    let footprint_cache = crate::dims::footprints::Cache::default();
    // Reserve the finished radius corridors before an edited/promoted linear
    // witness chooses its lane. Late replay alone would see that new witness as
    // a reason to evict an unrelated, already clear radius leader.
    let radius_reference = radius_edit_reference(analysis, given, false);
    let radius_inventory = radius_reference.as_ref().is_some_and(|r| {
        r.dimensions.iter().any(|d| {
            d.placed.is_some()
                && d.measurements.iter().any(|m| {
                    matches!(
                        m.parameter.as_str(),
                        "blend.radius" | "fillet.radius" | "cylindrical_profile.radius"
                    )
                })
        })
    });
    let held_for = |views: &[PlannedView]| {
        let mut held = crate::placement_intent::held_dimensions(canonical.as_ref(), views);
        for d in crate::placement_intent::held_dimensions(radius_reference.as_ref(), views) {
            if radius_inventory
                && d.kind == "callout"
                && radius_reference.as_ref().is_some_and(|r| {
                    r.views.iter().any(|old| {
                        old.id == d.view
                            && views.iter().any(|v| {
                                v.id == old.id
                                    && v.scale == old.scale
                                    && v.direction == old.direction
                                    && v.up == old.up
                            })
                    })
                })
            {
                held.retain(|old| old.id != d.id);
                held.push(d);
            }
        }
        held
    };
    let held = held_for(&views);
    let mut fixed_spec = spec.clone();
    fixed_spec
        .annotations
        .retain(|_, a| a.place.as_ref().is_some_and(|p| p.pinned));
    let fixed = if fixed_spec.annotations.is_empty() {
        Vec::new()
    } else {
        crate::dims::gdt::plan_anchored(
            analysis,
            &fixed_spec,
            &sheet,
            &views,
            &[],
            canonical.as_ref(),
            &footprint_cache,
        )
        .0
        .iter()
        .filter(|g| fixed_spec.annotations.contains_key(&g.id))
        .filter_map(crate::dims::gdt::proxy)
        .collect()
    };
    crate::sheets::line_work(analysis, spec, &mut views);
    let mut furniture = crate::centrelines::obstacles(&views);
    furniture.extend(fixed.iter().cloned());
    if let Some(p) = presentation {
        // Only a second attempt reserves them (`install_schedules`).
        furniture.extend(crate::hole_table::origin_reservations(
            analysis, p, sheet.page, &views,
        ));
    }
    let ((mut dimensions, mut omitted, mut more), mut inventory) = crate::dims::plan_inventory(
        analysis,
        spec,
        &sheet,
        &views,
        &furniture,
        &held,
        &footprint_cache,
        &excluded,
    )?;
    // A bare pin whose ink is already settled beside a schedule adds no packing obstacle.
    // Repacking the isometric around its reserved band can consume the table's clear space.
    // Changed ink and explicit placements still reserve their full obstacle as before.
    let packing_dimensions: Vec<_> = dimensions
        .iter()
        .filter(|d| {
            !(presentation.is_some()
                && spec
                    .dimensions
                    .get(&d.id)
                    .and_then(|e| e.place.as_ref())
                    .is_some_and(crate::placement_intent::automatic_pin)
                && canonical.as_ref().is_some_and(|base| {
                    base.dimensions
                        .iter()
                        .any(|before| before.id == d.id && before.placed == d.placed)
                }))
        })
        .cloned()
        .collect();
    let obstacles = crate::placement_intent::obstacles(spec, &packing_dimensions, &fixed);
    if !obstacles.is_empty() && spec.sheet.keep_layout != Some(true) {
        let before: Vec<_> = views.iter().map(|v| (v.at, v.area, v.scale)).collect();
        problems.retain(|p| {
            !matches!(
                p.code.as_str(),
                "pinned_view_overlap" | "pinned_view_off_sheet" | "placement_anchor_not_shown"
            )
        });
        // Reserve explicit pin ink before packing the free views around it. A pin's carrier
        // keeps its origin and scale; the additional band is a real packing obstacle.
        let mut reserved = canonical.clone();
        for d in dimensions.iter().chain(&fixed) {
            let place = spec
                .dimensions
                .get(&d.id)
                .and_then(|d| d.place.as_ref())
                .or_else(|| spec.annotations.get(&d.id).and_then(|a| a.place.as_ref()));
            if place.is_some_and(|p| p.pinned && !crate::placement_intent::automatic_pin(p))
                && let Some(b) = crate::dims::ink_box(d)
            {
                for v in views
                    .iter_mut()
                    .chain(reserved.iter_mut().flat_map(|p| &mut p.views))
                    .filter(|v| v.id == d.view)
                {
                    v.area = [
                        v.area[0].min(b[0]),
                        v.area[1].min(b[1]),
                        v.area[2].max(b[2]),
                        v.area[3].max(b[3]),
                    ];
                }
            }
        }
        crate::placement_intent::views(
            spec,
            &mut views,
            reserved.as_ref(),
            &sheet,
            &obstacles,
            &mut problems,
        );
        if before
            != views
                .iter()
                .map(|v| (v.at, v.area, v.scale))
                .collect::<Vec<_>>()
        {
            crate::sheets::line_work(analysis, spec, &mut views);
            furniture = crate::centrelines::obstacles(&views);
            furniture.extend(fixed.iter().cloned());
            let held = held_for(&views);
            ((dimensions, omitted, more), inventory) = crate::dims::plan_inventory(
                analysis,
                spec,
                &sheet,
                &views,
                &furniture,
                &held,
                &footprint_cache,
                &excluded,
            )?;
        }
    }
    let empty_details: Vec<_> = views
        .iter()
        .filter(|v| {
            v.detail.as_ref().is_some_and(|d| !d.authored)
                && spec.dimension_source != DimensionSource::Authored
                && !v.detail.as_ref().is_some_and(|d| {
                    d.dimensions.iter().any(|id| {
                        spec.dimensions.get(id).is_some_and(|s| s.omit)
                            || omitted.iter().any(|o| {
                                o.id == *id && o.state == crate::items::State::OmittedByUser
                            })
                    })
                })
                && !dimensions
                    .iter()
                    .any(|d| d.view == v.id && d.placed.is_some())
        })
        .map(|v| v.id.clone())
        .collect();
    if !empty_details.is_empty() {
        let mut bare = given.clone();
        for id in &empty_details {
            bare.views
                .entry(id.trim_start_matches("v:").into())
                .or_default()
                .omit = true;
        }
        let mut p = plan_with_presentation(
            analysis,
            &bare,
            gdt_beyond_lanes,
            automatic_pair,
            candidate_arrangement,
            ViewPolicy {
                complete: complete_views,
                require_detail: false,
            },
            presentation,
        )?;
        p.spec = given.clone();
        if require_automatic
            && !p
                .views
                .iter()
                .any(|v| v.detail.as_ref().is_some_and(|d| !d.authored))
        {
            return Err(invalid(NO_AUTOMATIC_DETAIL, &[]));
        }
        return Ok(p);
    }
    for view in &mut views {
        if let Some(d) = &mut view.detail {
            d.dimensions = dimensions
                .iter()
                .filter(|dim| dim.view == view.id)
                .map(|dim| dim.id.clone())
                .collect();
        }
    }
    problems.extend(more);
    // A label that had to stay across an axis end shortens that axis's overrun; what follows
    // keeps clear of the trimmed furniture.
    crate::dims::nearest_shared_witnesses(analysis, spec, &views, &mut dimensions);
    crate::centrelines::trim(&mut views, &dimensions);
    furniture = crate::centrelines::obstacles(&views);
    furniture.extend(fixed.iter().cloned());
    // The part's PMI, placed beyond the dimensions; the notes and tables keep clear of both.
    let mut annotation_obstacles = dimensions.clone();
    let furniture_ink: Vec<_> = furniture.into_iter().filter(|d| d.kind == "ink").collect();
    annotation_obstacles.extend(furniture_ink.iter().cloned());
    let (mut gdt, more) = crate::dims::gdt::plan_anchored(
        analysis,
        spec,
        &sheet,
        &views,
        &annotation_obstacles,
        canonical.as_ref(),
        &footprint_cache,
    );
    problems.extend(more);
    // Settle labels after GD&T has claimed its corridors. Frames and datum attachments remain
    // fixed obstacles; the full dimension inventory preserves omission locality.
    for d in &mut inventory {
        if let Some(shown) = dimensions.iter().find(|shown| shown.id == d.id) {
            *d = shown.clone();
        }
    }
    let reserved: Vec<_> = gdt.iter().filter_map(crate::dims::gdt::proxy).collect();
    let mut label_spec = spec.clone();
    if annotation_text_edited(spec) || crate::placement_intent::automatic_pins_only(spec) {
        // The canonical plan has already settled against its own trimmed furniture.
        // Re-solving the same peers against the edited furniture can move them again.
        let (before, natural) = if canonical.is_some() {
            (held.clone(), held.clone())
        } else {
            let base = Spec::default();
            let (_, mut natural) = crate::dims::plan_inventory(
                analysis,
                &base,
                &sheet,
                &views,
                &furniture_ink,
                &[],
                &footprint_cache,
                &excluded,
            )?;
            let before = natural.clone();
            let mut natural_fixed = reserved.clone();
            natural_fixed.extend(furniture_ink.iter().cloned());
            crate::dims::settle_inventory(
                analysis,
                &base,
                &sheet,
                &views,
                &mut natural,
                &natural_fixed,
            );
            crate::dims::refine_inventory(
                analysis,
                &base,
                &sheet,
                &views,
                &mut natural,
                &natural_fixed,
                &footprint_cache,
            );
            crate::dims::settle_after_furniture(
                analysis,
                &base,
                &sheet,
                &views,
                &mut natural,
                &natural_fixed,
                &footprint_cache,
                false,
            );
            let _ = crate::dims::compact_inventory(
                analysis,
                &base,
                &sheet,
                &views,
                &mut natural,
                &natural_fixed,
                &footprint_cache,
            );
            (before, natural)
        };
        let bounded_notches = !crate::notches::requirements(analysis).is_empty();
        let linear_ink: Vec<_> = inventory
            .iter()
            .filter(|d| d.kind == "linear")
            .filter_map(|d| Some((d.id.clone(), d.placed.clone()?)))
            .collect();
        for d in &mut inventory {
            if spec
                .dimensions
                .get(&d.id)
                .is_some_and(|e| e.place.is_some())
            {
                continue;
            }
            let original = before.iter().find(|b| {
                b.id == d.id
                    && b.terms == d.terms
                    && b.view == d.view
                    && b.measurements == d.measurements
            });
            let settled = natural.iter().find(|b| b.id == d.id);
            if original.is_some()
                && !spec
                    .dimensions
                    .get(&d.id)
                    .is_some_and(|e| e.tolerance.is_some())
                && !d.measurements.iter().any(|m| {
                    spec.features
                        .get(&m.feature)
                        .is_some_and(|f| f.fit.is_some())
                })
                && let (Some(_), Some(c)) =
                    (d.placed.as_ref(), settled.and_then(|d| d.placed.as_ref()))
            {
                // Restoring an unchanged peer must not put it through newly grown
                // text or a moved witness. Keep the current solved geometry in that case.
                if bounded_notches
                    && linear_ink.iter().any(|(id, p)| {
                        *id != d.id
                            && crate::dims::text_contact(c, p)
                            && natural
                                .iter()
                                .find(|n| n.id == *id)
                                .and_then(|n| n.placed.as_ref())
                                .is_none_or(|old| !crate::dims::text_contact(c, old))
                    })
                {
                    continue;
                }
                label_spec
                    .dimensions
                    .entry(d.id.clone())
                    .or_default()
                    .place
                    .get_or_insert_with(Default::default)
                    .pinned = true;
                d.placed = Some(c.clone());
            }
        }
    }
    // Settled labels also keep clear of the (trimmed) centre-line furniture: a move never adds
    // a contact with a centre or cutting-plane stroke.
    let mut settle_fixed = reserved.clone();
    settle_fixed.extend(furniture_ink);
    crate::dims::settle_inventory(
        analysis,
        &label_spec,
        &sheet,
        &views,
        &mut inventory,
        &settle_fixed,
    );
    let mut rerouted = crate::dims::refine_inventory(
        analysis,
        &label_spec,
        &sheet,
        &views,
        &mut inventory,
        &settle_fixed,
        &footprint_cache,
    );
    if crate::nominal::geometry_eligible(analysis) && !crate::nominal::comparison() {
        // A conveyed alias has no ink. Unlike a user omission, it must not
        // obstruct the final physical solve with its latent raw witnesses.
        let conveyed: BTreeSet<_> = omitted
            .iter()
            .filter(|o| matches!(o.state, ItemState::Covered { .. }))
            .map(|o| o.id.as_str())
            .collect();
        inventory.retain(|d| !conveyed.contains(d.id.as_str()));
    }
    crate::dims::nearest_shared_witnesses(analysis, spec, &views, &mut inventory);
    rerouted.extend(crate::dims::settle_after_furniture(
        analysis,
        &label_spec,
        &sheet,
        &views,
        &mut inventory,
        &settle_fixed,
        &footprint_cache,
        false,
    ));
    let compacted = crate::dims::compact_inventory(
        analysis,
        &label_spec,
        &sheet,
        &views,
        &mut inventory,
        &settle_fixed,
        &footprint_cache,
    );
    let mut outside: Vec<_> = Vec::new();
    for (i, d) in dimensions.iter_mut().enumerate() {
        if let Some(settled) = inventory.iter().find(|settled| settled.id == d.id) {
            d.placed = settled.placed.clone();
        } else {
            outside.push(i);
        }
    }
    for i in outside {
        crate::dims::follow_lanes(std::slice::from_mut(&mut dimensions[i]), &compacted);
    }
    if !crate::placement_intent::has_pins(spec)
        && inventory.iter().any(|d| {
            d.measurements
                .iter()
                .any(|m| m.feature.starts_with("f:section_recess:"))
        })
    {
        if spec.dimension_source == DimensionSource::Authored
            || spec.dimensions.values().any(|d| d.omit)
        {
            // An omission leaves its original paper reservation behind. Restored
            // aliases must not resize unrelated views.
            let mut full = spec.clone();
            full.dimension_source = DimensionSource::Auto;
            for d in full.dimensions.values_mut() {
                d.omit = false;
            }
            full.dimensions
                .retain(|_, d| *d != DimensionSpec::default());
            let reference = plan_with_presentation(
                analysis,
                &full,
                gdt_beyond_lanes,
                automatic_pair,
                candidate_arrangement,
                policy,
                presentation,
            )?;
            let mut stable = BTreeSet::new();
            for view in &mut views {
                if let Some(before) = reference
                    .views
                    .iter()
                    .find(|v| v.id == view.id && v.at == view.at && v.scale == view.scale)
                {
                    view.area = before.area;
                    stable.insert(view.id.clone());
                }
            }
            for d in dimensions.iter_mut().chain(&mut inventory) {
                if stable.contains(&d.view)
                    && let Some(before) = reference.dimensions.iter().find(|b| {
                        b.id == d.id
                            && b.view == d.view
                            && b.terms == d.terms
                            && b.measurements == d.measurements
                    })
                {
                    d.placed = before.placed.clone();
                    d.region = before.region;
                } else if stable.contains(&d.view)
                    && d.kind == "linear"
                    && spec.dimensions.get(&d.id).is_none_or(|s| {
                        s.place.is_none()
                            && s.tolerance.is_none()
                            && s.text.is_none()
                            && s.style.is_none()
                    })
                    && let Some(crate::items::State::Covered { by }) = reference
                        .omitted
                        .iter()
                        .find(|o| o.id == d.id)
                        .map(|o| &o.state)
                    && let Some(owner) = reference.dimensions.iter().find(|o| {
                        &o.id == by
                            && o.view == d.view
                            && o.kind == "linear"
                            && o.region.is_none()
                            && (o.value - d.value).abs() < 1e-6
                    })
                    && let (Some(p), Some(q)) = (&d.placed, &owner.placed)
                    && p.rotation == q.rotation
                    && p.arrows.len() == 2
                    && q.arrows.len() == 2
                {
                    // An outward witness repair moves the owner's actual lane
                    // beyond its nominal reservation. A promoted counted alias
                    // inherits that settled lane, keeping its own measured ends.
                    let normal = usize::from(p.rotation == 0.0);
                    d.placed = Some(crate::dims::in_lane(p, q.arrows[0][0][normal]));
                    d.region = None;
                }
            }
        } else {
            crate::sheet_fit::reserve_annotation_bounds(
                analysis, &sheet, &mut views, &inventory, &gdt,
            );
        }
    }
    crate::sheet_fit::reserve_nominal_bounds(analysis, &sheet, &mut views, &inventory, &gdt);
    let settled_ids: BTreeSet<&str> = dimensions
        .iter()
        .chain(&reserved)
        .map(|d| d.id.as_str())
        .collect();
    problems.retain(|p| {
        if p.code == "annotation_leader_long"
            && p.subjects.iter().any(|id| rerouted.contains_key(id))
        {
            return false;
        }
        !matches!(
            p.code.as_str(),
            "annotation_outside_area"
                | "annotation_off_sheet"
                | "annotation_over_title_block"
                | "annotation_over_view"
                | "annotation_line_over_view"
                | "annotation_overlap"
                | "annotation_ink_overlap"
                | "annotation_leader_crossing"
                | "leader_through_feature"
                | "extension_through_feature"
                // #32: judged again on the settled ink below, as the collisions are.
                | "dimension_line_shared"
                | "dimension_text_stray"
        ) || !p
            .subjects
            .iter()
            .any(|id| settled_ids.contains(id.as_str()))
    });
    problems.extend(rerouted.into_values().flatten());
    crate::dims::blends::consolidate(&mut dimensions, spec, given);
    if annotation_text_edited(spec) && canonical.is_some() {
        // A fillet's final count includes blends consolidated after routing.
        // Compare complete claims before deciding that this peer was edited.
        for d in &mut dimensions {
            if d.measurements
                .iter()
                .any(|m| m.parameter == "fillet.radius")
                && d.measurements.iter().any(|m| m.parameter == "blend.radius")
                && let Some(before) = held.iter().find(|b| {
                    b.id == d.id
                        && b.view == d.view
                        && b.terms == d.terms
                        && b.measurements == d.measurements
                })
            {
                d.placed = before.placed.clone();
                d.region = before.region;
            }
        }
    }
    let mut ink = dimensions.clone();
    ink.extend(reserved);
    problems.extend(crate::dims::label_collisions(
        analysis,
        &ink,
        &sheet,
        &views,
        &footprint_cache,
    ));
    crate::dims::blends::clear_covered_findings(&dimensions, &mut problems);
    let radius_table = crate::dims::blends::tabulate_obstructed(
        analysis,
        spec,
        &sheet,
        &views,
        &mut dimensions,
        &gdt,
        &mut problems,
        complete_views,
    );
    for d in dimensions
        .iter()
        .filter(|d| matches!(&d.state, crate::items::State::Covered { by } if by.starts_with("n:") || (by.starts_with("d:") && d.measurements.iter().any(|m| m.parameter == "blend.radius"))))
    {
        let crate::items::State::Covered { by } = &d.state else {
            // Invariant: The iterator above selected only Covered states.
            unreachable!()
        };
        omitted.push(crate::dims::Omitted {
            id: d.id.clone(),
            label: d.label.clone(),
            basis: format!("conveyed by {by}: {}", d.basis),
            state: d.state.clone(),
        });
    }
    dimensions.retain(|d| !matches!(&d.state, ItemState::Covered { by } if by.starts_with("n:") || (by.starts_with("d:") && d.measurements.iter().any(|m| m.parameter == "blend.radius"))));
    let mut ink = dimensions.clone();
    ink.extend(
        annotation_obstacles
            .iter()
            .filter(|d| d.kind == "ink")
            .cloned(),
    );
    ink.extend(gdt.iter().filter_map(crate::dims::gdt::proxy));
    if let Some(table) = &radius_table {
        ink.extend(crate::tables::proxy(&table.table));
    }
    // The default finish's symbol beside the title block: what it meets, and kept clear of.
    for r in &requirements {
        problems.extend(crate::dims::gdt::finish_collisions_cached(
            analysis,
            &sheet,
            &views,
            &ink,
            r,
            &footprint_cache,
        ));
        ink.extend(crate::dims::gdt::finish_proxy(r));
    }
    // Python builder.py:976: reserve the constrained iso caption before free late furniture.
    let captions = crate::iso_caption::plan(analysis, &sheet, &views, &ink);
    ink.extend(captions.iter().map(crate::iso_caption::proxy));
    // The manufacturing requirements table goes before the notes, which keep clear of it.
    let (schedule, more) = crate::tables::manufacturing(analysis, spec, &sheet, &views, &ink);
    problems.extend(more);
    ink.extend(schedule.iter().filter_map(crate::tables::proxy));
    let (mut automatic, authored): (Vec<_>, Vec<_>) = notes_given
        .into_iter()
        .partition(|n| radius_notes.iter().any(|(r, _)| r.id == n.id));
    let (mut notes, mut notes_block, more) =
        crate::notes::plan(&authored, analysis, &sheet, &views, &ink);
    crate::notes::provenance(&mut notes, &requirements, spec);
    for note in notes
        .iter_mut()
        .filter(|n| feature_note_ids.contains(&n.id))
    {
        if spec
            .annotations
            .get(&note.id)
            .is_some_and(|a| a.text.is_some())
        {
            note.from_spec.push("text".into());
            note.basis += "; from_spec: text; original note text retained";
        }
    }
    if notes_block.is_none() {
        for flag in gdt
            .iter_mut()
            .filter(|g| notes.iter().any(|n| n.id == g.id))
        {
            flag.placed = None;
            flag.state = ItemState::NotDrawable {
                reason_code: "notes_not_placed".into(),
            };
        }
    }
    problems.extend(more);
    // Authored tables keep their established priority; automatic radii use the space left.
    let (mut tables, more) = crate::tables::plan(
        &spec.tables,
        analysis,
        &sheet,
        &views,
        &ink,
        notes_block.as_ref().map(|b| b.cells),
        spec,
    );
    problems.extend(more);
    tables.extend(schedule);
    ink.extend(tables.iter().filter_map(crate::tables::proxy));
    let (mut gear_tables, more) = crate::tables::plan(
        &crate::gears::tables(analysis),
        analysis,
        &sheet,
        &views,
        &ink,
        notes_block.as_ref().map(|b| b.cells),
        spec,
    );
    problems.extend(more);
    for (i, table) in gear_tables.iter_mut().enumerate() {
        table.basis = format!(
            "authored: gears/{i}, metric external spur gear requirements; ISO 21771-1/-2, ISO 53, ISO 54 and ISO 1328-1; placed as a standards data table"
        );
    }
    tables.extend(gear_tables);
    ink.extend(
        tables
            .iter()
            .filter(|t| t.id.starts_with("gear_data_"))
            .filter_map(crate::tables::proxy),
    );
    let (mut spring_tables, more) = crate::tables::plan(
        &crate::springs::tables(analysis),
        analysis,
        &sheet,
        &views,
        &ink,
        notes_block.as_ref().map(|b| b.cells),
        spec,
    );
    problems.extend(more);
    for table in &mut spring_tables {
        table.default = true;
        table.basis = "recognised spring data; measured values and explicit coil-count bases; rate only when stated; standard table sheet-space policy".into();
    }
    ink.extend(spring_tables.iter().filter_map(crate::tables::proxy));
    tables.extend(spring_tables);
    let exceptions_unstated =
        crate::dims::blends::exceptions_unstated(analysis, &dimensions, &radius_notes);
    if exceptions_unstated {
        automatic.retain(|n| n.id == crate::dims::blends::RELIEF_NOTE);
    }
    let (extra, block, mut more) = crate::notes::plan_radii(
        &automatic,
        analysis,
        &sheet,
        &views,
        &ink,
        notes_block.as_ref(),
    );
    if !authored.is_empty() {
        for p in &mut more {
            p.id += ":blend-radii";
        }
    }
    notes.extend(extra);
    problems.extend(more);
    let mut radius_notes_block = if authored.is_empty() {
        notes_block = block;
        None
    } else {
        block
    };
    problems.extend(crate::notes::radius_notes(
        &mut notes,
        &radius_notes,
        spec,
        &sheet,
        &views,
    ));
    let stated: BTreeSet<_> = dimensions
        .iter()
        .filter(|d| d.placed.is_some() || matches!(&d.state, ItemState::Covered { by } if by == crate::dims::blends::RADIUS_TABLE))
        .flat_map(|d| d.measurements.iter().map(|m| m.feature.as_str()))
        .chain(
            notes
                .iter()
                .filter(|n| n.placed.is_some())
                .flat_map(|n| n.measurements.iter().map(|m| m.feature.as_str())),
        )
        .collect();
    // A default must explicitly leave proven relief profiles to the model. Hiding or
    // losing that companion note cannot silently assign the default to their small arcs.
    let unresolved = (!analysis.radius_reliefs.is_empty()
        && !notes
            .iter()
            .any(|n| n.id == crate::dims::blends::RELIEF_NOTE && n.placed.is_some()))
        || analysis.features.iter().any(|f| {
            matches!(f.kind.as_str(), "blend" | "fillet" | "cylindrical_profile")
                && !crate::lint::blends::is_relief(analysis, f)
                && !stated.contains(f.id.as_str())
                && !radius_notes.iter().flat_map(|(_, ms)| ms).any(|m| {
                    matches!(f.kind.as_str(), "fillet" | "cylindrical_profile")
                        && f.parameters
                            .get("radius")
                            .and_then(|v| v.as_f64())
                            .is_some_and(|r| crate::dims::fmt(r) == crate::dims::fmt(m.value))
                })
        });
    if unresolved {
        for note in notes.iter_mut().filter(|n| {
            (n.placed.is_some()
                || (exceptions_unstated && matches!(n.state, ItemState::OmittedByUser)))
                && !spec.annotations.get(&n.id).is_some_and(|e| e.omit)
                && !n.measurements.is_empty()
                && crate::label_text::note_body(spec, n).starts_with("ALL UNSPECIFIED RADII")
        }) {
            note.placed = None;
            note.state = ItemState::NotDrawable {
                reason_code: "notes_not_placed".into(),
            };
            problems.push(Problem {
                id: format!("p:notes_not_placed:{}", note.id),
                code: "notes_not_placed".into(),
                severity: "error".into(),
                message: format!(
                    "{} {RADIUS_DEFAULT_WITHDRAWN}",
                    crate::wording::capital(&crate::wording::note(&note.text))
                ),
                subjects: vec![note.id.clone()],
                ..Default::default()
            });
        }
    }
    for note in notes
        .iter()
        .filter(|n| matches!(n.state, ItemState::NotDrawable { .. }))
    {
        for m in note.measurements.iter().filter(|m| {
            matches!(
                m.parameter.as_str(),
                "blend.radius" | "fillet.radius" | "cylindrical_profile.radius"
            )
        }) {
            problems.push(Problem {
                id: format!("p:feature_not_dimensioned:{}", m.feature),
                code: "feature_not_dimensioned".into(),
                severity: "warning".into(),
                message: format!(
                    "{} is not dimensioned: {} cannot safely state its radius.",
                    crate::wording::capital(&crate::wording::feature_id(analysis, &m.feature)),
                    crate::wording::note(&note.text)
                ),
                subjects: vec![m.feature.clone()],
                ..Default::default()
            });
        }
    }
    if !notes
        .iter()
        .any(|n| n.placed.is_some() && radius_notes.iter().any(|(r, _)| r.id == n.id))
    {
        if authored.is_empty() {
            notes_block = None;
        } else {
            radius_notes_block = None;
        }
    }
    crate::dims::blends::cover_fillets(&mut notes, &mut dimensions, spec, given);
    // The radius note/table is placed late. Its exact claims and the explicit relief
    // exclusions now resolve the earlier dimension-only coverage findings.
    let stated_radii: BTreeSet<_> = dimensions.iter()
        .filter(|d| d.placed.is_some() || matches!(&d.state, ItemState::Covered { by } if by == crate::dims::blends::RADIUS_TABLE))
        .flat_map(|d| &d.measurements)
        .chain(notes.iter().filter(|n| n.placed.is_some()).flat_map(|n| &n.measurements))
        .filter(|m| matches!(m.parameter.as_str(), "blend.radius" | "fillet.radius" | "cylindrical_profile.radius"))
        .map(|m| m.feature.as_str()).collect();
    problems.retain(|p| {
        p.code != "feature_not_dimensioned"
            || p.subjects.is_empty()
            || !p.subjects.iter().all(|id| {
                analysis.features.iter().any(|f| {
                    f.id == *id
                        && matches!(f.kind.as_str(), "blend" | "fillet" | "cylindrical_profile")
                        && (stated_radii.contains(id.as_str())
                            || crate::lint::blends::is_relief(analysis, f))
                })
            })
    });

    for d in dimensions
        .iter()
        .filter(|d| matches!(&d.state, ItemState::Covered { by } if by.starts_with("n:") || by == crate::dims::blends::RADIUS_TABLE))
    {
        let ItemState::Covered { by } = &d.state else {
            // Invariant: The iterator above selected only Covered states.
            unreachable!()
        };
        omitted.push(crate::dims::Omitted {
            id: d.id.clone(),
            label: d.label.clone(),
            basis: format!("conveyed by {by}: {}", d.basis),
            state: d.state.clone(),
        });
    }
    dimensions.retain(|d| !matches!(&d.state, ItemState::Covered { by } if by.starts_with("n:") || by == crate::dims::blends::RADIUS_TABLE));

    notes.extend(omitted_notes);

    // A drawing-wide requirement carried by a general note is drawn as that note is.
    for r in requirements.iter_mut().filter(|r| r.carrier == "notes") {
        if let Some(n) = notes.iter().find(|n| n.id == r.id) {
            r.state = n.state.clone();
        }
    }
    for o in &mut omitted {
        if inactive.contains(&o.id) {
            o.basis =
                "inactive: the spec lists it, for another revision (spec_entries_inactive)".into();
        }
    }
    let mut spec = given.clone();
    spec.schema = SPEC_SCHEMA.into();
    if inactive.is_empty() {
        spec.binding = Some(analysis.binding.clone());
    }
    // Relative to the view's origin: later packing (sheet_fit) moves a view and its ink
    // together, and the canonical plan's frame is not this one's.
    let (tip_dimensions, tip_views) = canonical
        .as_ref()
        .map_or((&dimensions, &views), |p| (&p.dimensions, &p.views));
    let mut callout_tips = BTreeMap::new();
    for d in tip_dimensions.iter().filter(|d| d.kind == "callout") {
        if let Some(tip) = d.placed.as_ref().and_then(|p| Some(p.lines.first()?[0]))
            && let Some(v) = tip_views.iter().find(|v| v.id == d.view)
        {
            // The first drawing of the callout, as automatic_offset reads it.
            callout_tips
                .entry(d.id.clone())
                .or_insert((d.view.clone(), [tip[0] - v.at[0], tip[1] - v.at[1]]));
        }
    }
    let mut plan = Plan {
        characteristics: Vec::new(),
        inspection_balloons: Vec::new(),
        recognition_corrections: Vec::new(),
        schema: PLAN_SCHEMA.into(),
        binding: analysis.binding.clone(),
        analysis_sha256: Some(crate::planning::memo(
            analysis,
            "analysis_sha256",
            Vec::new(),
            || {
                // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
                Sha256::digest(serde_json::to_vec(analysis).expect("analysis serializes"))
                    .iter()
                    .map(|b| format!("{b:02x}"))
                    .collect()
            },
        )),
        feature_dispositions: crate::dims::feature_dispositions(analysis),
        status: crate::completeness::Status::NeedsAttention,
        completeness: crate::completeness::Completeness::default(),
        sheet,
        sheets: Vec::new(),
        sheet_candidates: Vec::new(),
        table_outcome: crate::hole_table::TableOutcome::None,
        table_restore: None,
        views,
        captions,
        callout_widths: {
            let mut widths: BTreeMap<String, f64> = BTreeMap::new();
            for d in canonical.as_ref().map_or(&inventory, |p| &p.dimensions) {
                if d.kind == "callout"
                    && let Some(p) = &d.placed
                {
                    let width = widths.entry(d.view.clone()).or_default();
                    *width = width.max(p.label_box[2] - p.label_box[0]);
                }
            }
            widths
        },
        callout_tips,
        gutter_deltas: BTreeMap::new(),
        // A pin replay's later sheets are packed afresh, as the canonical drawing's were;
        // they spend that drawing's gutters (view_gutters::apply).
        replay_gutters: canonical
            .as_ref()
            .filter(|_| crate::placement_intent::has_pins(&spec))
            .map(|c| c.gutter_deltas.clone()),
        dimension_labels: canonical
            .as_ref()
            .map(|p| p.dimension_labels.clone())
            .unwrap_or_default(),
        dimensions,
        omitted,
        notes,
        notes_block,
        radius_notes_block,
        radius_table,
        tables,
        gdt,
        requirements,
        process: Some(crate::process::resolve(analysis.bom.as_ref(), &spec)),
        hole_table: None,
        hole_tables: Vec::new(),
        balloons: Vec::new(),
        problems,
        spec,
    };
    let unreadable = crate::sheet_fit::illegible_steps(analysis, &plan);
    if given.sheet.scale.is_some() && !automatic_pair && unreadable > 0 {
        plan.problems.push(Problem {
            id: "p:legibility_floor_breached:steps".into(),
            code: "legibility_floor_breached".into(),
            severity: "warning".into(),
            message: format!("{unreadable} dimensioned step spans project below the {:.1} mm printed capital height at the requested scale; enlarge their owning view or omit sheet/scale for automatic selection.", crate::sheet_fit::MIN_STEP_SPAN),
            subjects: vec!["sheet".into()],
            fix: vec![crate::items::Op::remove("sheet/scale")],
            ..Default::default()
        });
    }
    crate::recognition_corrections::report(analysis, &mut plan);
    if complete_views {
        let free_pair = automatic_pair && plan.spec.sheet.keep_layout != Some(true);
        crate::sheet_fit::reserve_linear_corridors(
            analysis,
            &mut plan,
            &inventory,
            free_pair,
            radius_reference.as_ref(),
            &footprint_cache,
        );
    }
    crate::placement_intent::finish(&mut plan, analysis);
    plan.problems
        .extend(line_work_problems(&plan.sheet, &plan.views));
    for p in &mut plan.problems {
        for subject in &mut p.subjects {
            if let Some(v) = plan.views.iter().find(|v| {
                annotation_obstacles
                    .iter()
                    .any(|w| w.kind == "ink" && w.view == v.id && w.id == *subject)
            }) {
                p.refs.push(subject.clone());
                *subject = v.id.clone();
            }
        }
        let mut seen = BTreeSet::new();
        p.subjects.retain(|s| seen.insert(s.clone()));
    }
    crate::iso_caption::refresh(analysis, &mut plan);
    crate::completeness::report(analysis, &mut plan);
    plan.sheet.fill = crate::sheet_fit::fill(analysis, &plan);
    plan.sheet.basis += &format!("; drawn-item fill {:.1}%", plan.sheet.fill * 100.0);
    let omitted_ink: Vec<_> = inventory
        .iter()
        .filter(|d| {
            plan.omitted
                .iter()
                .any(|o| o.id == d.id && o.state == ItemState::OmittedByUser)
        })
        .cloned()
        .collect();
    let captured = analysis
        .recognition
        .as_ref()
        .is_some_and(|r| !r.recogniser.starts_with("quiddity-rust "));
    if captured && crate::dims::has_recess_dimensions(analysis) {
        crate::planning::store(
            analysis,
            "complete-annotation-inventory",
            // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
            serde_json::to_vec(&plan.views).expect("views serialize"),
            inventory,
        );
        // Settle on roomy paper at this exact scale, then pack the complete
        // annotation blocks onto the initial sheet. Omitted items retain their
        // reservations, so the same packing applies to omission edits.
        if !automatic_pair
            && !gdt_beyond_lanes
            && plan.spec.sheet.size.is_none()
            && plan.spec.sheet.scale.is_none()
            && plan.views.iter().all(|v| v.cut.is_none())
            && ink_findings(&plan) > 0
        {
            // An optional crop consumes real columns. First try the complete
            // annotation reservation on this same paper and scale; this avoids
            // discarding a clean enlargement merely because the legacy estimate
            // reserved only narrow callout bands.
            if plan.views.iter().any(|v| v.detail.is_some()) {
                let mut complete = plan.spec.clone();
                complete.sheet.size = Some(plan.sheet.size.clone());
                complete.sheet.scale = Some(plan.sheet.scale.clone());
                let mut candidate = plan_with(
                    analysis,
                    &complete,
                    false,
                    true,
                    candidate_arrangement,
                    complete_views,
                    require_automatic,
                )?;
                if drawn_sheets_fit(analysis, &candidate)
                    && findings(&candidate).hard <= findings(&plan).hard
                    && ink_findings(&candidate) < ink_findings(&plan)
                    && feature_crossings(&candidate) <= feature_crossings(&plan)
                    && carries_required(&plan, &candidate)
                {
                    candidate.spec = plan.spec.clone();
                    candidate.sheet.default = plan.sheet.default;
                    let basis = plan
                        .sheet
                        .basis
                        .rsplit_once("; drawn-item fill ")
                        .map_or(plan.sheet.basis.as_str(), |(b, _)| b);
                    candidate.sheet.basis = format!(
                        "{basis}; complete annotation columns fitted at the same scale; drawn-item fill {:.1}%",
                        candidate.sheet.fill * 100.0
                    );
                    plan = candidate;
                }
            }
            if ink_findings(&plan) > 0 {
                let mut roomy = plan.spec.clone();
                roomy.sheet.size = Some("A0".into());
                roomy.sheet.scale = Some(plan.sheet.scale.clone());
                // A template must keep the recipient's actual view inventory. A0
                // may fit a crop the recipient declined; adding it makes packing
                // impossible before any measured annotation block is considered.
                for (name, detail) in
                    crate::detail::requests(analysis, &roomy, plan.sheet.scale_factor)
                {
                    if !detail.authored && !plan.views.iter().any(|v| v.id == format!("v:{name}")) {
                        roomy.views.entry(name).or_default().omit = true;
                    }
                }
                let template = plan_with(
                    analysis,
                    &roomy,
                    false,
                    true,
                    candidate_arrangement,
                    complete_views,
                    false,
                )?;
                let inventory: Vec<crate::dims::PlannedDimension> = crate::planning::lookup(
                    analysis,
                    "complete-annotation-inventory",
                    // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
                    &serde_json::to_vec(&template.views).expect("views serialize"),
                )
                .unwrap_or_default();
                if let Some(mut packed) =
                    crate::sheet_fit::pack_inventory(analysis, &template, &plan, &inventory)
                    && !inventory.is_empty()
                    && drawn_sheets_fit(analysis, &packed)
                    && findings(&packed).hard <= findings(&plan).hard
                    && ink_findings(&packed) < ink_findings(&plan)
                    && carries_required(&plan, &packed)
                {
                    packed.spec = plan.spec.clone();
                    let basis = plan
                        .sheet
                        .basis
                        .rsplit_once("; drawn-item fill ")
                        .map_or(plan.sheet.basis.as_str(), |(b, _)| b);
                    packed.sheet.basis = format!(
                        "{basis}; complete annotation blocks packed at the same scale; drawn-item fill {:.1}%",
                        packed.sheet.fill * 100.0
                    );
                    plan = packed;
                }
            }
        }
    }
    // Optional crops must release their furniture reservation when they strand a declared
    // table or notes. In particular a head must not prevent the multi-sheet allocator from
    // fitting the requested schedule after moving the iso and section to the next sheet.
    let furniture_unplaced = plan
        .problems
        .iter()
        .any(|p| matches!(p.code.as_str(), "table_not_placed" | "notes_not_placed"));
    let automatic_details: Vec<_> = plan
        .views
        .iter()
        .filter(|v| {
            v.detail
                .as_ref()
                .is_some_and(|d| !d.authored && (d.kind == "prismatic-steps" || furniture_unplaced))
        })
        .map(|v| v.id.trim_start_matches("v:").to_string())
        .collect();
    if !automatic_details.is_empty() && !crate::placement_intent::has_pins(given) {
        let mut bare_spec = given.clone();
        for name in &automatic_details {
            bare_spec.views.entry(name.clone()).or_default().omit = true;
        }
        let mut bare = plan_with_presentation(
            analysis,
            &bare_spec,
            gdt_beyond_lanes,
            automatic_pair,
            candidate_arrangement,
            ViewPolicy {
                complete: complete_views,
                require_detail: false,
            },
            presentation,
        )?;
        retain_short_leaders(analysis, &mut plan, &bare);
        let old = (
            findings(&plan),
            ink_findings(&plan),
            feature_crossings(&plan),
        );
        let new = (
            findings(&bare),
            ink_findings(&bare),
            feature_crossings(&bare),
        );
        let longer_routes = longer_callouts(&plan, &bare);
        if (new.0.hard, new.0.dropped, over_views(&bare), new.1, new.2)
            < (old.0.hard, old.0.dropped, over_views(&plan), old.1, old.2)
            || (longer_routes && new.0.hard <= old.0.hard && new.0.dropped <= old.0.dropped)
        {
            bare.spec = plan.spec.clone();
            bare.problems.push(Problem { id: "p:detail_unplaceable:quality".into(),
                code: "detail_unplaceable".into(), severity: "info".into(),
                message: "The optional enlargement introduced annotation conflicts or longer leaders; its dimensions remain on the parent view.".into(),
                refs: automatic_details.iter().map(|n| format!("v:{n}")).collect(), ..Default::default() });
            plan = bare;
        }
    }
    if require_automatic
        && !plan
            .views
            .iter()
            .any(|v| v.detail.as_ref().is_some_and(|d| !d.authored))
    {
        return Err(invalid(NO_AUTOMATIC_DETAIL, &[]));
    }
    plan.sheet.fill = crate::sheet_fit::fill(analysis, &plan);
    if let Some((basis, _)) = plan.sheet.basis.rsplit_once("; drawn-item fill ") {
        plan.sheet.basis = format!("{basis}; drawn-item fill {:.1}%", plan.sheet.fill * 100.0);
    }
    // PMI placement has now added all source warnings. A material stated in the finished
    // title block is drawn; retain its census and any real blank/conflicting override warning.
    if let Some(crate::pmi::Reading::Read(r)) = &analysis.pmi
        && let Some(m) = r.title_material()
    {
        let source = m.census_id();
        let drawn = plan.sheet.title_cells.iter().any(|cell| {
            cell.field == "material"
                && cell
                    .value
                    .as_deref()
                    .is_some_and(|v| v.trim() == m.designation.trim())
        });
        if drawn {
            plan.problems
                .retain(|p| p.code != "pmi_not_extracted" || p.refs.first() != Some(&source));
        } else {
            for p in plan
                .problems
                .iter_mut()
                .filter(|p| p.code == "pmi_not_extracted" && p.refs.first() == Some(&source))
            {
                p.message = format!(
                    "The STEP material {:?} is not stated in the title block because the Spec overrides it. Set sheet/title/material to the STEP material, or review the override.",
                    m.designation.trim()
                );
            }
        }
    }
    if crate::nominal::geometry_eligible(analysis) && !crate::nominal::comparison() {
        // No-op offsets were normalized to bare pins for the solve above.
        // Apply the same intent to final settling, while retaining authored drags.
        let mut final_intent = given.clone();
        for (id, d) in &mut final_intent.dimensions {
            if let Some(place) = d.place.as_mut()
                && place.offset.is_some()
                && effective
                    .dimensions
                    .get(id)
                    .and_then(|d| d.place.as_ref())
                    .is_some_and(|p| p.offset.is_none())
            {
                place.offset = None;
            }
        }
        if !crate::dims::default_nominal_intent(&final_intent) {
            // Authored inventory changes participate in their existing candidate
            // fit judgment. Only automatic precision polish is deferred.
            if !annotation_text_edited(&final_intent)
                && (!crate::placement_intent::has_pins(&final_intent)
                    || crate::placement_intent::automatic_pins_only(&final_intent))
            {
                settle_finished_nominals(
                    analysis,
                    &final_intent,
                    &mut plan,
                    &omitted_ink,
                    3,
                    NominalPass::All,
                    None,
                );
            }
        } else {
            // Printed linear lanes determine feasibility before hole-table
            // escalation can withdraw an off-sheet location. Only the expensive
            // leader rerouting waits for selection of the returned drawing.
            let spilled_linear = plan.problems.iter().any(|q| {
                q.code == "annotation_off_sheet"
                    && q.subjects.iter().any(|id| {
                        plan.dimensions
                            .iter()
                            .any(|d| d.id == *id && d.kind == "linear")
                    })
            });
            if spilled_linear {
                settle_finished_nominals(
                    analysis,
                    &final_intent,
                    &mut plan,
                    &omitted_ink,
                    0,
                    NominalPass::Linear,
                    None,
                );
            }
            crate::planning::store(
                analysis,
                "nominal-final-preparation",
                serde_json::to_vec(&(&plan.spec.dimensions, &plan.views))
                    // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
                    .expect("final frame serializes"),
                (final_intent, omitted_ink),
            );
        }
    }
    plan.status = crate::completeness::status(&plan.problems);
    if reusable {
        crate::planning::store(analysis, "resolved_candidate", key, plan.clone());
    }
    Ok(plan)
}

/// Precision repairs belong to the selected drawing, after sheet candidates have
/// been judged. Rejected candidates must not each launch another route search.
pub(crate) fn finish_nominal_plan(analysis: &Analysis, spec: &Spec, plan: &mut Plan) {
    if crate::nominal::comparison() {
        return;
    }
    let reference = plan
        .dimensions
        .iter()
        .any(|d| {
            d.measurements.iter().any(|m| {
                matches!(
                    m.parameter.as_str(),
                    "blend.radius" | "fillet.radius" | "cylindrical_profile.radius"
                )
            })
        })
        .then(|| radius_edit_reference(analysis, spec, false))
        .flatten();
    if !crate::nominal::geometry_eligible(analysis) {
        settle_finished_nominals(
            analysis,
            spec,
            plan,
            &[],
            0,
            NominalPass::Radius,
            reference.as_ref(),
        );
        return;
    }
    let (intent, omitted) = crate::planning::lookup::<(Spec, Vec<crate::dims::PlannedDimension>)>(
        analysis,
        "nominal-final-preparation",
        // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
        &serde_json::to_vec(&(&plan.spec.dimensions, &plan.views)).expect("final frame serializes"),
    )
    .unwrap_or_else(|| (spec.clone(), Vec::new()));
    if !crate::dims::default_nominal_intent(&intent) {
        settle_finished_nominals(
            analysis,
            &intent,
            plan,
            &omitted,
            0,
            NominalPass::Radius,
            reference.as_ref(),
        );
        return;
    }
    if !annotation_text_edited(&intent)
        && (!crate::placement_intent::has_pins(&intent)
            || crate::placement_intent::automatic_pins_only(&intent))
    {
        settle_finished_nominals(
            analysis,
            &intent,
            plan,
            &omitted,
            3,
            NominalPass::All,
            reference.as_ref(),
        );
    }
    crate::placement_intent::finish(plan, analysis);
    plan.status = crate::completeness::status(&plan.problems);
}

/// An annotation edit replays the finished radius routes, rather than repairing the
/// earlier, pre-polish inventory afresh against a different set of peers. This is
/// a pure reference, independent of which document happened to be planned first.
pub(crate) fn radius_edit_reference(
    analysis: &Analysis,
    spec: &Spec,
    recovered: bool,
) -> Option<Plan> {
    let edited = spec.dimension_source == DimensionSource::Authored
        || spec.dimensions.values().any(|d| {
            d.omit
                || d.tolerance.is_some()
                || d.fit.is_some()
                || d.text.is_some()
                || d.style.is_some()
                || d.place.as_ref().is_some_and(|p| *p != Default::default())
        })
        || spec.features.values().any(|f| f.fit.is_some());
    // Pins already replay the finished recovered drawing in its canonical frame.
    // A second primitive-plan reference can change even a no-op offset tip.
    if !edited
        || crate::nominal::comparison()
        || crate::placement_intent::has_pins(spec)
        || !analysis
            .features
            .iter()
            .any(|f| matches!(f.kind.as_str(), "blend" | "fillet" | "cylindrical_profile"))
    {
        return None;
    }
    if let Some(reference) = crate::planning::trial_radius_reference() {
        return Some(reference);
    }
    let mut base = spec.clone();
    base.dimension_source = DimensionSource::Auto;
    for d in base.dimensions.values_mut() {
        d.omit = false;
        d.tolerance = None;
        d.fit = None;
        d.text = None;
        d.style = None;
        d.place = None;
    }
    base.dimensions
        .retain(|_, d| *d != DimensionSpec::default());
    for f in base.features.values_mut() {
        f.fit = None;
    }
    base.features.retain(|_, f| *f != FeatureSpec::default());
    if &base == spec {
        return None;
    }
    let analysis_key: [u8; 32] =
        crate::planning::memo(analysis, "analysis_digest", Vec::new(), || {
            Sha256::digest(serde_json::to_vec(analysis).expect("Analysis serializes")).into()
        });
    let key = serde_json::to_vec(&(
        analysis_key,
        &base,
        crate::planning::is_fixed_pair(),
        crate::planning::nominals_deferred(),
        recovered,
        crate::dims::idle_given(),
    ))
    .expect("radius reference inputs serialize");
    crate::planning::memo(analysis, "radius-edit-reference", key.clone(), || {
        // The isolated reference depends on these complete inputs, not on the
        // live preparation scope. Preview/commit can use different scopes;
        // retain their identical reference without planning it again.
        static MEMO: crate::planning::BoundedMemo<Option<Plan>> =
            crate::planning::BoundedMemo::new(128);
        let digest = crate::planning::spaced_digest(&Sha256::digest(&key).into());
        if let Some(reference) = MEMO.get(&digest) {
            return reference;
        }
        // Do not replace the live candidate's policy/preparation entries,
        // notably its complete annotation inventory and sheet trials.
        // Primitive plans retain their primitive corridors. Only the outer
        // paper policy requests a finished recovered reference, then carries it
        // through its fixed-paper trials with trial_radius_reference above.
        let reference = crate::planning::isolated(analysis, || {
            if recovered {
                plan_recovered(analysis, &base).ok()
            } else {
                plan(analysis, &base).ok()
            }
        });
        MEMO.insert(digest, reference.clone());
        reference
    })
}

#[derive(Clone, Copy, PartialEq)]
enum NominalPass {
    Linear,
    All,
    Radius,
}

/// Final furniture and blend consolidation own the actual printed inventory.
/// Keep user omissions as latent obstacles, but never conveyed raw aliases.
fn settle_finished_nominals(
    analysis: &Analysis,
    spec: &Spec,
    plan: &mut Plan,
    omitted: &[crate::dims::PlannedDimension],
    remaining: usize,
    pass: NominalPass,
    radius_reference: Option<&Plan>,
) {
    let original_spec = spec;
    let route_leaders = pass != NominalPass::Linear;
    let radius_only = pass == NominalPass::Radius;
    if radius_only
        && !plan.dimensions.iter().any(|d| {
            d.measurements.iter().any(|m| {
                matches!(
                    m.parameter.as_str(),
                    "blend.radius" | "fillet.radius" | "cylindrical_profile.radius"
                )
            })
        })
    {
        return;
    }
    let mut fixed = crate::centrelines::obstacles(&plan.views);
    fixed.extend(plan.gdt.iter().filter_map(crate::dims::gdt::proxy));
    fixed.extend(plan.tables.iter().filter_map(crate::tables::proxy));
    if let Some(block) = &plan.notes_block {
        fixed.push(crate::notes::proxy(block));
    }
    if let Some(block) = &plan.radius_notes_block {
        fixed.push(crate::notes::proxy(block));
    }
    if let Some(table) = &plan.radius_table {
        fixed.extend(crate::tables::proxy(&table.table));
    }
    fixed.extend(
        crate::iso_caption::plan(analysis, &plan.sheet, &plan.views, &plan.dimensions)
            .iter()
            .map(crate::iso_caption::proxy),
    );
    let mut held_spec = spec.clone();
    // Bare pins replay the canonical drawing, including its settled peers.
    // Final text decisions may still change, but cannot solve those peers again.
    if crate::placement_intent::automatic_pins_only(spec) {
        for d in &plan.dimensions {
            held_spec
                .dimensions
                .entry(d.id.clone())
                .or_default()
                .place
                .get_or_insert_with(Default::default)
                .pinned = true;
        }
    }
    let spec = &held_spec;
    let mut items = plan.dimensions.clone();
    items.extend_from_slice(omitted);
    let cache = crate::dims::footprints::Cache::default();
    // Consolidation can release the witness or label corridor that prevented
    // an earlier repair. Reconsider the complete printed inventory before
    // fixing the final callouts, with the usual page and peer protections.
    if !radius_only {
        crate::dims::settle_inventory(analysis, spec, &plan.sheet, &plan.views, &mut items, &fixed);
    }
    // The fixed sheet trial already placed faithful text. Keep its settled routes
    // when the inventory is unchanged and a fresh audit is clear, rather than
    // searching for another clear presentation. Authored/latent inventory and
    // ordinary explicit plans retain the complete finishing pass.
    if !radius_only
        && crate::planning::is_fixed_pair()
        && !items.iter().any(|d| {
            d.measurements
                .iter()
                .any(|m| matches!(m.parameter.as_str(), "blend.radius" | "fillet.radius" | "cylindrical_profile.radius"))
        })
        && route_leaders
        && omitted.is_empty()
        && items.len() == plan.dimensions.len()
        && items.iter().zip(&plan.dimensions).all(|(a, b)| {
            a.id == b.id
                && a.placed == b.placed
                && a.label == b.label
                && a.terms == b.terms
                && a.basis == b.basis
        })
        && !plan
            .problems
            .iter()
            .any(crate::sheet_policy::layout_problem)
        && !crate::dims::recheck_cached(analysis, plan, &cache)
            .iter()
            .any(crate::sheet_policy::layout_problem)
        // The paper policy also measures leader/extension contacts from final ink (#221):
        // a trial that kept one would be refused for a contact the full pass can clear.
        && crate::dims::leader_on_extension(&plan.dimensions).is_empty()
    {
        return;
    }
    let mut reports = BTreeMap::new();
    if !radius_only && route_leaders {
        reports.extend(crate::dims::refine_inventory(
            analysis,
            spec,
            &plan.sheet,
            &plan.views,
            &mut items,
            &fixed,
            &cache,
        ));
        reports.extend(crate::dims::settle_after_furniture(
            analysis,
            spec,
            &plan.sheet,
            &plan.views,
            &mut items,
            &fixed,
            &cache,
            true,
        ));
    }
    if route_leaders {
        // Only the final radius repair sees the schedule/table/balloon inventory:
        // these items are drawn after the general leader placement has finished.
        let mut radius_fixed = fixed.clone();
        radius_fixed.extend(radius_furniture(plan));
        reports.extend(crate::dims::settle_radius_leaders(
            analysis,
            spec,
            &plan.sheet,
            &plan.views,
            &mut items,
            &radius_fixed,
            &cache,
            radius_reference,
        ));
    }
    // Collision reports from the raw inventory cannot survive consolidation of
    // either of their owners into another printed statement.
    let live: BTreeSet<_> = plan
        .dimensions
        .iter()
        .filter(|d| d.placed.is_some())
        .map(|d| d.id.as_str())
        .collect();
    plan.problems.retain(|q| {
        !matches!(
            q.code.as_str(),
            "annotation_ink_overlap" | "annotation_overlap" | "annotation_leader_crossing"
        ) || q
            .subjects
            .iter()
            .all(|id| !id.starts_with("d:") || live.contains(id.as_str()))
    });
    if reports.is_empty()
        && items
            .iter()
            .zip(&plan.dimensions)
            .all(|(a, b)| a.placed == b.placed)
    {
        return;
    }
    let mut trial = plan.clone();
    for d in &mut trial.dimensions {
        if let Some(s) = items.iter().find(|s| s.id == d.id) {
            d.placed = s.placed.clone();
            d.region = s.region;
            d.terms.clone_from(&s.terms);
            d.label.clone_from(&s.label);
            d.basis.clone_from(&s.basis);
        }
    }
    crate::sheet_fit::reserve_nominal_bounds(
        analysis,
        &trial.sheet,
        &mut trial.views,
        &trial.dimensions,
        &trial.gdt,
    );
    let checked = crate::dims::recheck(analysis, &trial);
    let codes: BTreeSet<_> = crate::dims::recheck(analysis, plan)
        .into_iter()
        .chain(checked.iter().cloned())
        .map(|q| q.code)
        .collect();
    trial.problems.retain(|q| {
        !codes.contains(&q.code)
            && !(matches!(
                q.code.as_str(),
                "annotation_line_over_view" | "annotation_leader_long"
            ) && reports.contains_key(q.subjects.first().map_or("", String::as_str)))
    });
    trial.problems.extend(checked);
    trial.problems.extend(reports.into_values().flatten());
    // Rechecking geometry replaces collision records, including their remedies.
    // Reattach the authored pin's reset operation before publishing this trial.
    crate::placement_intent::finish(&mut trial, analysis);
    if findings(&trial).hard <= findings(plan).hard
        && ink_findings(&trial) <= ink_findings(plan)
        && feature_crossings(&trial) <= feature_crossings(plan)
    {
        for d in &trial.dimensions {
            if d.kind == "callout"
                // An authored offset is measured from the canonical automatic
                // tip, not the rim tip recomputed for its dragged bend.
                && !original_spec.dimensions.get(&d.id).is_some_and(|s| s.place.as_ref().is_some_and(|p| p.pinned))
                && let (Some(p), Some(v)) = (&d.placed, trial.views.iter().find(|v| v.id == d.view))
                && let Some(s) = p.lines.first()
            {
                trial.callout_tips.insert(
                    d.id.clone(),
                    (d.view.clone(), [s[0][0] - v.at[0], s[0][1] - v.at[1]]),
                );
            }
        }
        trial.sheet.fill = crate::sheet_fit::fill(analysis, &trial);
        crate::completeness::report(analysis, &mut trial);
        // Another linear settlement is needed only when a leader moved and
        // changed its corridor. A smaller compact text box only releases ink.
        let changed = plan.dimensions.iter().zip(&trial.dimensions).any(|(a, b)| {
            a.kind == "callout"
                && a.placed.as_ref().map(|p| (&p.lines, &p.arrows))
                    != b.placed.as_ref().map(|p| (&p.lines, &p.arrows))
        });
        *plan = trial;
        if changed && remaining > 0 {
            settle_finished_nominals(
                analysis,
                original_spec,
                plan,
                omitted,
                remaining - 1,
                pass,
                radius_reference,
            );
        }
    } else if pass == NominalPass::All {
        // Another callout's refinement can cost the trial (nema17-bracket's hole callout left
        // its area); the radius repair alone may still be taken, so a radius leader is not
        // left off radial for a peer's sake.
        settle_finished_nominals(
            analysis,
            original_spec,
            plan,
            omitted,
            0,
            NominalPass::Radius,
            radius_reference,
        );
    }
}

/// Furniture drawn after ordinary callouts must also constrain the final radius repair.
fn radius_furniture(plan: &Plan) -> Vec<crate::dims::PlannedDimension> {
    let proxy =
        |id: String, view: String, label_box: [f64; 4], lines| crate::dims::PlannedDimension {
            callout_offset: None,
            id,
            view,
            kind: "furniture".into(),
            region: None,
            value: 0.0,
            label: String::new(),
            terms: Vec::new(),
            measurements: Vec::new(),
            default: true,
            from_spec: Vec::new(),
            basis: String::new(),
            state: ItemState::Shown,
            reference: false,
            determined_by: Vec::new(),
            placed: Some(crate::dims::Placed {
                lines,
                arrows: Vec::new(),
                label_at: [label_box[0], label_box[1]],
                label_box,
                rotation: 0.0,
            }),
        };
    let mut out: Vec<_> = plan
        .hole_table
        .iter()
        .chain(&plan.hole_tables)
        .flat_map(|table| table.blocks.iter().filter_map(crate::tables::proxy))
        .collect();
    for b in &plan.balloons {
        let ring = [
            b.at[0] - b.radius,
            b.at[1] - b.radius,
            b.at[0] + b.radius,
            b.at[1] + b.radius,
        ];
        let label = b.label_box.map_or(ring, |lb| {
            [
                lb[0].min(ring[0]),
                lb[1].min(ring[1]),
                lb[2].max(ring[2]),
                lb[3].max(ring[3]),
            ]
        });
        out.push(proxy(
            b.id.clone(),
            b.view.clone(),
            label,
            b.leader.into_iter().collect(),
        ));
    }
    for r in &plan.requirements {
        if let Some(p) = &r.placed {
            out.push(proxy(
                r.id.clone(),
                String::new(),
                p.glyph,
                p.strokes.clone(),
            ));
        }
    }
    out
}

/// Recompute centre-line page bounds after a view and its ink move together.
pub(crate) fn refresh_line_work_problems(plan: &mut Plan) {
    // Packing can move a cross back onto the page after its initial finding was made.
    // Recheck its actual final ink; do not suppress other annotations' off-sheet findings.
    plan.problems
        .retain(|p| !p.id.starts_with("p:annotation_off_sheet:ink:"));
    layout::with_gutter(
        plan.spec.sheet.frame_gutter.unwrap_or(layout::FRAME_GUTTER),
        || {
            for sheet in std::iter::once(&plan.sheet).chain(&plan.sheets) {
                plan.problems.extend(line_work_problems(sheet, &plan.views));
            }
        },
    );
    plan.status = crate::completeness::status(&plan.problems);
}

pub(crate) fn line_work_problems(sheet: &PlannedSheet, views: &[PlannedView]) -> Vec<Problem> {
    let [width, height] = sheet.page;
    let mut problems = Vec::new();
    for v in views.iter().filter(|v| v.sheet == sheet.number) {
        for w in v.line_work.iter().filter(|w| w.kind != "cutting_plane") {
            if w.curves.iter().any(|c| {
                let b = c.bbox();
                b[0] < margin() - 1e-9
                    || b[1] < margin() - 1e-9
                    || b[2] > width - margin() + 1e-9
                    || b[3] > height - margin() + 1e-9
            }) {
                problems.push(Problem {
                    id: format!("p:annotation_off_sheet:{}", w.id),
                    code: "annotation_off_sheet".into(),
                    severity: "error".into(),
                    message: format!(
                        "{} in {} extends past the drawable area; choose a larger sheet or a smaller scale.",
                        crate::wording::capital(&crate::wording::line_work(&w.kind)),
                        crate::wording::view(&v.id)
                    ),
                    subjects: vec![v.id.clone()],
                    refs: vec![w.id.clone()],
                    ..Default::default()
                });
            }
        }
    }
    problems
}

/// An optional crop may move furniture, but cannot spend a longer leader unnecessarily.
/// Translate the shorter parent route and retain it only after the normal geometry checks.
fn retain_short_leaders(analysis: &Analysis, plan: &mut Plan, bare: &Plan) {
    for d in &bare.dimensions {
        let Some(prior) = d.placed.as_ref().filter(|_| d.kind == "callout") else {
            continue;
        };
        let Some(i) = plan
            .dimensions
            .iter()
            .position(|p| p.id == d.id && p.view == d.view)
        else {
            continue;
        };
        let Some(now) = &plan.dimensions[i].placed else {
            continue;
        };
        let length = |p: &crate::dims::Placed| {
            p.lines
                .iter()
                .map(|s| (s[1][0] - s[0][0]).hypot(s[1][1] - s[0][1]))
                .sum::<f64>()
        };
        if length(now) <= length(prior) + 2.0 {
            continue;
        }
        let Some((v, source)) = plan
            .views
            .iter()
            .find(|v| v.id == d.view)
            .zip(bare.views.iter().find(|v| v.id == d.view))
            .filter(|(v, s)| (v.scale - s.scale).abs() < 1e-9)
        else {
            continue;
        };
        let delta = [v.at[0] - source.at[0], v.at[1] - source.at[1]];
        let mut candidate = plan.clone();
        let mut p = prior.clone();
        for q in p
            .lines
            .iter_mut()
            .flatten()
            .chain(p.arrows.iter_mut().flatten())
        {
            for k in 0..2 {
                q[k] += delta[k];
            }
        }
        for (k, shift) in delta.iter().enumerate() {
            p.label_at[k] += shift;
            p.label_box[k] += shift;
            p.label_box[k + 2] += shift;
        }
        candidate.dimensions[i].placed = Some(p);
        let checked = crate::dims::recheck(analysis, &candidate);
        let codes: BTreeSet<_> = crate::dims::recheck(analysis, plan)
            .into_iter()
            .chain(checked.iter().cloned())
            .map(|q| q.code)
            .collect();
        candidate.problems.retain(|q| !codes.contains(&q.code));
        candidate.problems.extend(checked);
        if findings(&candidate).hard <= findings(plan).hard
            && ink_findings(&candidate) <= ink_findings(plan)
            && feature_crossings(&candidate) <= feature_crossings(plan)
        {
            if let Some(p) = &candidate.dimensions[i].placed
                && let Some(tip) = p.lines.first().map(|l| l[0])
                && let Some(v) = candidate.views.iter().find(|v| v.id == d.view)
            {
                candidate.callout_tips.insert(
                    d.id.clone(),
                    (d.view.clone(), [tip[0] - v.at[0], tip[1] - v.at[1]]),
                );
            }
            *plan = candidate;
        }
    }
}

/// *spec* as this version reads it (`draftwright.spec/2`): a version 2 spec as it is; a version
/// 1 spec with each dimension's key rewritten to the form it now has (R1 invariant 9: a change
/// of form is read, never guessed at):
/// - `d:<feature>/<parameter>@<view>` becomes `d:<feature>/<parameter>`: the view was where the
///   plan routed it, not a decision, so the entry does not keep it as its `view`;
/// - a PMI dimension's feature, `pmi:<category>:#<n>` (its record's Part 21 instance), becomes
///   the record's item ID in *analysis* ([`crate::pmi::Report::identities`]), when the spec is
///   bound to the analysis's file (or to none): a Part 21 instance names a record only in its
///   own file. A key whose record is not in the analysis, has no ID, or is of another file, is
///   kept as it was, for [`plan`] to refuse with the nearest IDs.
///
/// Two keys that come to one are ambiguous, and refused with both as the candidates. Anything
/// but a version 1 or 2 spec is refused.
pub fn migrate(spec: &Spec, analysis: &Analysis) -> Result<Spec, Invalid> {
    if spec.schema == SPEC_SCHEMA {
        return Ok(spec.clone());
    }
    if !SPEC_SCHEMAS.contains(&spec.schema.as_str()) {
        return Err(invalid(
            format!(
                "The spec is {:?}, which this draftwright does not read.",
                spec.schema
            ),
            &SPEC_SCHEMAS,
        ));
    }
    let same_file = spec
        .binding
        .as_ref()
        .is_none_or(|b| b.source_sha256 == analysis.binding.source_sha256);
    let ids = match &analysis.pmi {
        Some(crate::pmi::Reading::Read(r)) if same_file => r.identities(),
        _ => crate::pmi::Identities::default(),
    };
    let mut out = spec.clone();
    out.schema = SPEC_SCHEMA.into();
    out.dimensions.clear();
    let mut from: BTreeMap<String, String> = BTreeMap::new();
    for (old, entry) in &spec.dimensions {
        let mut key = match old.rsplit_once('@') {
            Some((stem, view)) if old.starts_with("d:") && !view.contains('/') => stem.to_string(),
            _ => old.clone(),
        };
        if let Some((feature, parameter)) = key.strip_prefix("d:").and_then(|k| k.rsplit_once('/'))
            && let Some(source) = feature.strip_prefix("pmi:")
            && let Some(id) = ids.ids.get(source)
        {
            key = format!("d:{id}/{parameter}");
        }
        if let Some(first) = from.get(&key) {
            return Err(Invalid {
                path: None,
                also: Vec::new(),
                message: format!(
                    "The spec is {:?}, whose dimension keys {first:?} and {old:?} both read as {key:?} in {SPEC_SCHEMA} (a dimension's ID no longer names its view, and a PMI dimension is named by its record's identity): keep one of them.",
                    spec.schema
                ),
                choices: vec![first.clone(), old.clone()],
            });
        }
        from.insert(key.clone(), old.clone());
        out.dimensions.insert(key, entry.clone());
    }
    Ok(out)
}

/// Applies edit operations to a spec. Each operation is `{"op": "set" | "remove" | "add",
/// "path": "a/b/c", "value": …}`, with paths into the spec's JSON; a `/` inside a key (a
/// dimension's ID) is written `~1` and a `~` is written `~0`, as in a JSON pointer (RFC 6901).
/// `add` appends its value to the list at its path; a note added without an ID takes the next
/// free `n:<k>`, a table the next free `t:<k>`. The result is validated whole, so an unknown field or a value of the wrong kind
/// is refused with the reason.
pub fn edit(spec: &Spec, ops: &serde_json::Value) -> Result<Spec, Invalid> {
    // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
    let mut doc = serde_json::to_value(spec).expect("a spec serialises");
    let ops = ops
        .as_array()
        .ok_or_else(|| invalid("Edits are a list of operations.", &[]))?;
    for op in ops {
        let kind = op["op"].as_str().unwrap_or("");
        let raw_path = op["path"]
            .as_str()
            .ok_or_else(|| invalid("An edit needs a \"path\".", &[]))?;
        // A shallow ops JSON can otherwise construct a value whose recursive drop
        // exhausts the WASM stack, even when deserialization ultimately refuses it.
        if raw_path
            .split('/')
            .filter(|s| !s.is_empty())
            .take(65)
            .count()
            > 64
        {
            return Err(invalid(
                "An edit path must have at most 64 components.",
                &[],
            ));
        }
        let path: Vec<String> = raw_path
            .split('/')
            .filter(|s| !s.is_empty())
            .map(|s| s.replace("~1", "/").replace("~0", "~"))
            .collect();
        let Some((last, parents)) = path.split_last() else {
            return Err(invalid("An edit's path is empty.", &[]));
        };
        if !["set", "remove", "add"].contains(&kind) {
            return Err(invalid(
                format!("{kind:?} is not an edit."),
                &["set", "remove", "add"],
            ));
        }
        let absent = || {
            invalid(
                format!(
                    "There is nothing at {:?} to remove.",
                    op["path"].as_str().unwrap_or("")
                ),
                &[],
            )
        };
        // `set` and `add` make the objects on their path; `remove` needs every one of them there.
        let mut at = &mut doc;
        for key in parents {
            if at.is_array() {
                if path.len() != 3 || path[0] != "tables" || path[2] != "sheet" || kind == "add" {
                    return Err(invalid(
                        "List-index edits are supported only for tables/<index>/sheet.",
                        &[],
                    ));
                }
                let index = key
                    .parse::<usize>()
                    .ok()
                    .ok_or_else(|| invalid(format!("{key:?} is not a list index."), &[]))?;
                at = at.get_mut(index).ok_or_else(|| {
                    invalid(format!("{key:?} is not an existing list index."), &[])
                })?;
            } else {
                let object = at
                    .as_object_mut()
                    .ok_or_else(|| invalid(format!("{key:?} is not inside an object."), &[]))?;
                at = if kind != "remove" {
                    object
                        .entry(key.to_string())
                        .or_insert_with(|| serde_json::json!({}))
                } else {
                    object.get_mut(key).ok_or_else(absent)?
                };
            }
        }
        let object = at
            .as_object_mut()
            .ok_or_else(|| invalid(format!("{:?} is not an object.", parents.join("/")), &[]))?;
        if kind == "set" {
            object.insert(last.to_string(), op["value"].clone());
        } else if kind == "add" {
            let Some(list) = object
                .entry(last.to_string())
                .or_insert_with(|| serde_json::json!([]))
                .as_array_mut()
            else {
                return Err(invalid(
                    format!("{:?} is not a list to add to.", path.join("/")),
                    &[],
                ));
            };
            let mut value = op["value"].clone();
            if parents.is_empty()
                && (last == "notes" || last == "tables")
                && let Some(item) = value.as_object_mut()
                && !item.contains_key("id")
            {
                let ids = list.iter().filter_map(|n| n["id"].as_str());
                let id = if last == "notes" {
                    crate::notes::next_id(ids)
                } else {
                    crate::tables::next_id(ids)
                };
                item.insert("id".into(), id.into());
            }
            list.push(value);
        } else {
            object.remove(last.as_str()).ok_or_else(absent)?;
        }
    }
    // A later explicit allocation edit supersedes an inherited local promise.
    // An explicit keep_layout in this batch still wins (including offered local
    // bundles), so contradictory local requests remain transactional refusals.
    if spec.sheet.keep_layout == Some(true) {
        let explicit_guard = ops.iter().any(|op| {
            matches!(
                op["path"].as_str().map(|p| p.trim_matches('/')),
                Some("sheet/keep_layout" | "sheet")
            )
        });
        let allocation_edit = ops.iter().any(|op| {
            matches!(
                op["path"].as_str().map(|p| p.trim_matches('/')),
                Some(
                    "sheet/size"
                        | "sheet/scale"
                        | "sheets"
                        | "sheets/max"
                        | "sheets/smallest_scale"
                        | "sheet/dimension_spacing"
                        | "sheet/frame_gutter"
                )
            )
        });
        if !explicit_guard && allocation_edit {
            doc["sheet"]
                .as_object_mut()
                .expect("sheet is an object")
                .remove("keep_layout");
        }
    }
    let spec: Spec = serde_json::from_value(doc)
        .map_err(|e| invalid(format!("The edited spec is not valid: {e}."), &[]))?;
    crate::sheets::validate(&spec)?;
    if spec.sheet.keep_layout == Some(true) {
        crate::local_layout::forced(&spec)?;
    }
    crate::dims::check_tolerances(&spec)?;
    crate::spec_gdt::check(&spec)?;
    crate::dims::authored::check(&spec)?;
    crate::dimension_scheme::check(&spec)?;
    if let Some((axis, r)) = spec
        .origin
        .iter()
        .find(|(k, v)| !matches!(k.as_str(), "x" | "y" | "z") || v.is_empty())
    {
        return Err(invalid(
            format!(
                "origin/{axis}: choose x, y or z and a nonempty min, max or planar face/feature reference (got {r:?})."
            ),
            &[],
        ));
    }
    crate::dims::check_views(&spec)?;
    crate::placement_intent::check(&spec)?;
    crate::label_text::check(&spec)?;
    title_block::check(&spec.sheet.title)?;
    crate::notes::check(&spec.notes)?;
    crate::tables::check(&spec.tables)?;
    crate::gears::check(&spec.gears)?;
    crate::recognition_corrections::check(&spec)?;
    Ok(spec)
}

/// Draws the plan: each view projected with hidden lines, placed and scaled, and the title
/// block. The spec travels in the output. Refused when a drawn face or edge of *part* did not
/// resolve on reading.
pub fn render(
    part: &Part,
    analysis: &Analysis,
    plan: &Plan,
    font: &Font,
) -> Result<Drawing, Unresolved> {
    render_with(part, analysis, plan, font, &mut Projections::default())
}

/// What renders share: hidden-line projections and cuts, and the latest placed edges and
/// visible-ink bounds per projection. A different part binding discards the cached geometry.
#[derive(Clone, Default)]
pub struct Projections {
    binding: Option<String>,
    views: HashMap<Vec<u64>, Vec<Projected>>,
    cuts: HashMap<Vec<u64>, Vec<Vec<[f64; 3]>>>,
    cut_material: HashMap<Vec<u64>, bool>,
    hatches: HashMap<Vec<u64>, Vec<Item>>,
    geometry: HashMap<Vec<u64>, (Vec<u64>, ViewGeometry)>,
    stats: ProjectionStats,
    /// Whether the persisted projection/geometry entries changed after loading.
    pub(crate) dirty: bool,
}

/// Expensive passes actually performed by a kept cache, including unsuccessful projections.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct ProjectionStats {
    pub projections: usize,
    pub bounds: usize,
}

/// One view's projected edges on the page, with the bounds of its visible ink.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ViewGeometry {
    pub visible: Vec<Item>,
    pub hidden: Vec<Item>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub breaks: Vec<Curve>,
    pub bbox: Option<[f64; 4]>,
    pub undrawn: usize,
}

/// The geometry layer, in plan view order. Sheet furniture belongs to the annotation layer.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct GeometryLayer {
    pub views: Vec<(String, ViewGeometry)>,
}

fn projection_cut(v: &PlannedView) -> Option<&Cut> {
    v.cut
        .as_ref()
        .or_else(|| v.detail.as_ref().and_then(|d| d.source_cut.as_ref()))
}

fn projection_key(v: &PlannedView) -> Vec<u64> {
    let cut = projection_cut(v);
    bits(&[
        &v.direction,
        &v.up,
        cut.map_or(&[], |c| &c.origin[..]),
        cut.map_or(&[], |c| &c.normal[..]),
        v.detail.as_ref().map_or(&[], |d| &d.bounds[0][..]),
        v.detail.as_ref().map_or(&[], |d| &d.bounds[1][..]),
        v.half_section.as_ref().map_or(&[], |h| &h.origin[..]),
        v.half_section.as_ref().map_or(&[], |h| &h.keep[..]),
    ])
}

/// Numbers as a map key, by their bits.
fn bits(numbers: &[&[f64]]) -> Vec<u64> {
    numbers
        .iter()
        .flat_map(|n| n.iter().map(|x| x.to_bits()))
        .collect()
}

impl Projections {
    pub(crate) fn assembly_view(
        &mut self,
        a: &Analysis,
        spec: &Spec,
        v: &PlannedView,
        pieces: Vec<Projected>,
    ) {
        self.bind(a, spec);
        let cut = projection_cut(v);
        let mut key = bits(&[
            &v.direction,
            &v.up,
            cut.map_or(&[], |c| &c.origin[..]),
            cut.map_or(&[], |c| &c.normal[..]),
        ]);
        key.extend(crate::modelled_threads::cache_key(a, spec, v));
        self.views.insert(key, pieces);
    }

    /// Prime a GA detail using the ordinary crop renderer, without converting
    /// distant exact splines to Beziers. Positive rational weights keep a curve
    /// inside its control hull. Uncertain/degenerate input is retained so the
    /// renderer still reports it; the complete camera remains available to its
    /// parent and any other crops.
    pub(crate) fn assembly_crop(
        &mut self,
        part: &Part,
        a: &Analysis,
        plan: &Plan,
        v: &PlannedView,
    ) -> Result<(), Unresolved> {
        if v.detail.is_none() || v.half_section.is_some() {
            return Ok(());
        }
        self.bind(a, &plan.spec);
        let cut = projection_cut(v);
        let mut key = bits(&[
            &v.direction,
            &v.up,
            cut.map_or(&[], |c| &c.origin[..]),
            cut.map_or(&[], |c| &c.normal[..]),
        ]);
        key.extend(crate::modelled_threads::cache_key(a, &plan.spec, v));
        let Some(pieces) = self.views.remove(&key) else {
            return Ok(());
        };
        let (size, centre) = crate::detail::crop_extent(a, v);
        let rect = [
            centre[0] - size[0] / 2.,
            centre[1] - size[1] / 2.,
            centre[0] + size[0] / 2.,
            centre[1] + size[1] / 2.,
        ];
        let kept: Vec<_> = pieces
            .iter()
            .filter(|p| {
                let Some(Exact::Nurbs {
                    degree,
                    poles,
                    weights,
                    knots,
                    t0,
                    t1,
                }) = &p.exact
                else {
                    return true;
                };
                if *degree == 0
                    || poles.len() != weights.len()
                    || !poles.iter().flatten().all(|n| n.is_finite())
                    || !weights.iter().all(|n| n.is_finite() && *n > 0.)
                    || !knots.iter().all(|n| n.is_finite())
                {
                    return true;
                }
                let Some(curve) = haecceity::nurbs::NurbsCurve::new(
                    *degree,
                    poles.iter().map(|p| [p[0], p[1], 0.]).collect(),
                    weights.clone(),
                    knots.clone(),
                ) else {
                    return true;
                };
                let (lo, hi) = curve.domain();
                if !(*t0 >= lo
                    && *t0 <= hi
                    && *t1 >= lo
                    && *t1 <= hi
                    && (*t1 - *t0).abs() > 1e-9 * (hi - lo).max(1.))
                {
                    return true;
                }
                let mut b = [
                    f64::INFINITY,
                    f64::INFINITY,
                    f64::NEG_INFINITY,
                    f64::NEG_INFINITY,
                ];
                for p in poles {
                    for k in 0..2 {
                        b[k] = b[k].min(p[k]);
                        b[k + 2] = b[k + 2].max(p[k]);
                    }
                }
                let magnitude = b.iter().chain(&rect).map(|n| n.abs()).fold(1., f64::max);
                let pad = (1e-5 / v.scale).max(1e-12 * magnitude);
                !(b[2] < rect[0] - pad
                    || b[0] > rect[2] + pad
                    || b[3] < rect[1] - pad
                    || b[1] > rect[3] + pad)
            })
            .cloned()
            .collect();
        if kept.len() == pieces.len() {
            self.views.insert(key, pieces);
            return Ok(());
        }
        if std::env::var_os("DRAFTWRIGHT_GA_PROFILE").is_some() {
            eprintln!(
                "GA crop {}: {} of {} pieces",
                v.id,
                kept.len(),
                pieces.len()
            );
        }
        self.views.insert(key.clone(), kept);
        let result = self.geometry(part, a, v, &plan.spec);
        self.views.insert(key, pieces);
        result.map(|_| ())
    }

    /// Pack-only ownership-aware hatching, resolved with the ordinary cut primitives.
    pub(crate) fn assembly_hatch(
        &mut self,
        a: &Analysis,
        plan: &Plan,
        v: &PlannedView,
        ink: Vec<Item>,
        contours: &[Vec<[f64; 3]>],
        material: bool,
    ) {
        self.bind(a, &plan.spec);
        if let Some(cut) = &v.cut {
            let centre = extent(a, &View::new(v.direction, v.up).expect("section camera")).1;
            let mut key = bits(&[
                &v.direction,
                &v.up,
                &cut.origin,
                &cut.normal,
                &[v.scale],
                &centre,
                &v.at,
            ]);
            let extra = crate::modelled_threads::cache_key(a, &plan.spec, v);
            key.extend(&extra);
            self.hatches.insert(key, ink);
            let mut key = bits(&[&cut.origin, &cut.normal]);
            key.extend(extra);
            self.cuts.insert(key.clone(), contours.to_vec());
            if material {
                self.cut_material.insert(key, true);
            }
        }
    }

    pub fn stats(&self) -> ProjectionStats {
        self.stats
    }

    fn bind(&mut self, analysis: &Analysis, spec: &Spec) {
        let binding = if analysis
            .features
            .iter()
            .any(|f| f.kind == "modelled_thread")
        {
            let enabled: Vec<_> = crate::modelled_threads::threads(analysis)
                .map(|t| t.enabled(spec))
                .collect();
            let views: Vec<_> = spec
                .views
                .iter()
                .map(|(id, v)| (id, v.thread_depiction))
                .collect();
            format!(
                "{}:6410:{}",
                analysis.binding.source_sha256,
                serde_json::to_string(&(enabled, spec.sheet.thread_depiction, views))
                    // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
                    .expect("thread settings serialize")
            )
        } else {
            analysis.binding.source_sha256.clone()
        };
        let spring_key = crate::springs::cache_key(analysis, spec);
        let binding = if spring_key.is_empty() {
            binding
        } else {
            format!("{binding}:2162:{spring_key:?}")
        };
        if self.binding.as_ref() != Some(&binding) {
            self.views.clear();
            self.cuts.clear();
            self.cut_material.clear();
            self.hatches.clear();
            self.geometry.clear();
            self.binding = Some(binding);
            self.dirty = true;
        }
    }

    fn geometry(
        &mut self,
        part: &Part,
        analysis: &Analysis,
        v: &PlannedView,
        spec: &Spec,
    ) -> Result<ViewGeometry, Unresolved> {
        let (size, centre) = crate::detail::crop_extent(analysis, v);
        // Orientation views carry no hidden edges unless the Spec asks for them
        // (pictorial practice, ISO 128-30 / ASME Y14.3); the others draw them.
        let hidden = v.line_style.hidden.unwrap_or(v.id != "v:iso");
        let mut key = projection_key(v);
        key.extend(crate::modelled_threads::cache_key(analysis, spec, v));
        key.extend(v.broken.as_ref().map_or_else(Vec::new, |b| b.key()));
        let placement = bits(&[
            &[v.scale, f64::from(v.id == "v:iso"), f64::from(hidden)],
            &centre,
            &v.at,
        ]);
        if let Some((previous, geometry)) = self.geometry.get(&key)
            && *previous == placement
        {
            return Ok(geometry.clone());
        }
        let mut geometry = ViewGeometry {
            visible: Vec::new(),
            hidden: Vec::new(),
            breaks: Vec::new(),
            bbox: None,
            undrawn: 0,
        };
        let rect = [
            v.at[0] - size[0] * v.scale / 2.0,
            v.at[1] - size[1] * v.scale / 2.0,
            v.at[0] + size[0] * v.scale / 2.0,
            v.at[1] + size[1] * v.scale / 2.0,
        ];
        let mut boundary = crate::detail::Boundary::default();
        let place = |q: [f64; 2]| {
            [
                v.at[0] + v.scale * (q[0] - centre[0]),
                v.at[1] + v.scale * (q[1] - centre[1]),
            ]
        };
        // A half section draws the view as seen on one side of its axis and as cut on the
        // other, each projection clipped to its half ([`crate::half_section`]).
        let halves: Vec<(std::borrow::Cow<PlannedView>, Option<[f64; 4]>)> = match &v.half_section {
            None => vec![(std::borrow::Cow::Borrowed(v), None)],
            Some(h) => {
                let mut cut = v.clone();
                cut.cut = Some(h.cut(v));
                vec![
                    (
                        std::borrow::Cow::Borrowed(v),
                        Some(h.rect(analysis, v, false)),
                    ),
                    (
                        std::borrow::Cow::Owned(cut),
                        Some(h.rect(analysis, v, true)),
                    ),
                ]
            }
        };
        let carrier = crate::modelled_threads::carrier(
            part,
            analysis,
            spec,
            v.line_style.thread_depiction != Some(false),
        );
        for (source, half) in &halves {
            let keep = |curve: Curve| match half {
                Some(r) => crate::detail::clip(curve, *r),
                None => vec![curve],
            };
            for piece in self.view(part, analysis, source, spec)? {
                let mut moved = piece.clone();
                moved.points = piece.points.iter().map(|q| place(*q)).collect();
                moved.exact = piece
                    .exact
                    .as_ref()
                    .map(|e| placed(e, v.scale, centre, v.at));
                match projected(&moved) {
                    Some(curve) => {
                        let curve = crate::linework::stable_curve(curve);
                        let items = if piece.visible {
                            &mut geometry.visible
                        } else {
                            &mut geometry.hidden
                        };
                        if v.detail.is_some() {
                            let clipped = crate::detail::clip(curve.clone(), rect);
                            boundary.add(&curve, &clipped, rect);
                            items.extend(clipped.into_iter().map(Item::Edge));
                        } else {
                            items.extend(keep(curve).into_iter().map(Item::Edge));
                        }
                    }
                    None if piece.visible || hidden => geometry.undrawn += 1,
                    None => {}
                }
            }
            let (thick, _, thread_hidden) = crate::modelled_threads::ink(
                analysis,
                spec,
                source,
                carrier.as_ref().unwrap_or(part),
            );
            let clip_items = |items: Vec<Item>| -> Vec<Item> {
                items
                    .into_iter()
                    .flat_map(|item| match (item, half) {
                        (Item::Edge(c), Some(_)) => keep(c).into_iter().map(Item::Edge).collect(),
                        (item, _) => vec![item],
                    })
                    .collect()
            };
            geometry
                .visible
                .extend(clip_items(crate::springs::ink(analysis, source, spec)));
            geometry.visible.extend(clip_items(thick));
            geometry.hidden.extend(clip_items(thread_hidden));
        }
        if projection_cut(v).is_some() || v.half_section.is_some() || !hidden {
            geometry.hidden.clear();
        } else {
            // Visible ink takes precedence in orientation views too. A scale or
            // arrangement change must not reveal redundant hidden ink over it.
            geometry.hidden = crate::linework::hidden(&geometry.visible, geometry.hidden);
        }
        geometry.breaks = boundary.ink(rect);
        if let Some(b) = &v.broken {
            geometry.breaks.extend(b.ink(&geometry.visible, centre, v));
            geometry.visible = b.items(geometry.visible, centre, v);
            geometry.hidden = b.items(geometry.hidden, centre, v);
        }
        self.stats.bounds += 1;
        geometry.bbox = items_bbox(&geometry.visible);
        // Breaks are owned visible ink too; their short end strokes can extend
        // past a clipped silhouette and must count toward the drawn view bounds.
        for curve in &geometry.breaks {
            let b = curve.bbox();
            geometry.bbox = Some(geometry.bbox.map_or(b, |a| {
                [
                    a[0].min(b[0]),
                    a[1].min(b[1]),
                    a[2].max(b[2]),
                    a[3].max(b[3]),
                ]
            }));
        }
        self.geometry.insert(key, (placement, geometry.clone()));
        self.dirty = true;
        Ok(geometry)
    }

    fn view(
        &mut self,
        part: &Part,
        analysis: &Analysis,
        v: &PlannedView,
        spec: &Spec,
    ) -> Result<Vec<Projected>, Unresolved> {
        let cut = projection_cut(v);
        // Crops share their source camera's HLR; only the placed/trimmed geometry key
        // includes crop bounds. A second enlarged region needs no new kernel projection.
        let mut key = bits(&[
            &v.direction,
            &v.up,
            cut.map_or(&[], |c| &c.origin[..]),
            cut.map_or(&[], |c| &c.normal[..]),
        ]);
        key.extend(crate::modelled_threads::cache_key(analysis, spec, v));
        if let Some(pieces) = self.views.get(&key) {
            return Ok(pieces.clone());
        }
        // Invariant: Planned cameras come from the fixed views or checked section/detail cameras, with nonparallel axes.
        let view = View::new(v.direction, v.up).expect("a planned view");
        self.stats.projections += 1;
        let carrier = crate::modelled_threads::carrier(
            part,
            analysis,
            spec,
            v.line_style.thread_depiction != Some(false),
        );
        let source = carrier.as_ref().unwrap_or(part);
        let pieces = match cut.and_then(|c| Plane::new(c.origin, c.normal)) {
            Some(plane) => project_section(source, &view, &plane),
            None => match carrier
                .is_none()
                .then(|| projected_ink::prepared(&analysis.binding, v.direction, v.up))
                .flatten()
            {
                Some(pieces) => Ok(pieces),
                None => project(source, &view),
            },
        }?;
        self.views.insert(key, pieces.clone());
        self.dirty = true;
        Ok(pieces)
    }

    fn cut_material(
        &mut self,
        part: &Part,
        cut: &Cut,
        plane: &Plane,
        extra: &[u64],
    ) -> Result<bool, Unresolved> {
        let mut key = bits(&[&cut.origin, &cut.normal]);
        key.extend(extra);
        if let Some(material) = self.cut_material.get(&key) {
            return Ok(*material);
        }
        let loops = self.cut(part, cut, plane, extra)?;
        let material = cut_crosses_material(part, plane, &loops);
        self.cut_material.insert(key, material);
        Ok(material)
    }

    fn cut(
        &mut self,
        part: &Part,
        cut: &Cut,
        plane: &Plane,
        extra: &[u64],
    ) -> Result<Vec<Vec<[f64; 3]>>, Unresolved> {
        let mut key = bits(&[&cut.origin, &cut.normal]);
        key.extend(extra);
        if let Some(lines) = self.cuts.get(&key) {
            return Ok(lines.clone());
        }
        let lines = hlr::section(part, plane)?;
        self.cuts.insert(key, lines.clone());
        self.dirty = true;
        Ok(lines)
    }
}

/// Draws only the views' edges, reusing projections, placed curves and visible bounds.
pub fn render_geometry(
    part: &Part,
    analysis: &Analysis,
    plan: &Plan,
    projections: &mut Projections,
) -> Result<GeometryLayer, Unresolved> {
    projections.bind(analysis, &plan.spec);
    let views = plan
        .views
        .iter()
        .map(|v| {
            Ok((
                v.id.clone(),
                projections.geometry(part, analysis, v, &plan.spec)?,
            ))
        })
        .collect::<Result<_, Unresolved>>()?;
    Ok(GeometryLayer { views })
}

/// An annotation that can be rendered independently from every other item and the geometry.
pub enum AnnotationItem<'a> {
    Dimension(&'a PlannedDimension),
    Gdt(&'a crate::dims::gdt::PlannedGdt),
}

/// One item's ink and provenance. Run indices are local until appended to a drawing.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct RenderedItem {
    pub items: Vec<Item>,
    pub text: Vec<TextRun>,
    pub annotations: Vec<Annotation>,
    pub drawn: Vec<Drawn>,
    /// Session ink's owning items; absent for the unchanged single-sheet wire format.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub provenance: Vec<crate::items::Item>,
}

impl RenderedItem {
    /// Appends an item to the annotation layer, rebasing its searchable text's run indices.
    pub fn append(
        self,
        items: &mut Vec<Item>,
        text: &mut Vec<TextRun>,
        annotations: &mut Vec<Annotation>,
        sheet: &mut Sheet,
    ) {
        let offset = text.len();
        items.extend(self.items);
        text.extend(self.text);
        annotations.extend(self.annotations.into_iter().map(|mut a| {
            for run in &mut a.runs {
                *run += offset;
            }
            a
        }));
        sheet.items.extend(self.drawn);
    }
}

/// Draws a dimension, callout, feature control frame, datum symbol or PMI label from the plan.
pub fn render_item(item: AnnotationItem<'_>) -> RenderedItem {
    let mut out = RenderedItem::default();
    let interior = match &item {
        AnnotationItem::Dimension(d) => d.region == Some(crate::dims::CandidateRegion::Interior),
        AnnotationItem::Gdt(g) => g.basis.contains(crate::dims::interior::BASIS),
    };
    let heads = match &item {
        AnnotationItem::Dimension(d) => d
            .placed
            .as_ref()
            .map(|p| {
                p.arrows
                    .iter()
                    .flat_map(|&[tip, tail]| {
                        let n = [tail[1] - tip[1], tip[0] - tail[0]];
                        let k = (crate::dims::ARROW / 6.0) / n[0].hypot(n[1]).max(1e-12);
                        let a = [tail[0] + k * n[0], tail[1] + k * n[1]];
                        let b = [tail[0] - k * n[0], tail[1] - k * n[1]];
                        [[tip, a], [a, b], [b, tip]]
                    })
                    .collect()
            })
            .unwrap_or_default(),
        AnnotationItem::Gdt(_) => Vec::new(),
    };
    let (kind, label, lines, elbow) = match item {
        AnnotationItem::Dimension(d) => {
            out.annotations =
                crate::dims::render(std::slice::from_ref(d), &mut out.items, &mut out.text);
            let p = d.placed.as_ref();
            (
                Kind::Dimension,
                p.map(|p| p.label_box),
                p.map(|p| p.lines.clone()).unwrap_or_default(),
                p.filter(|_| d.kind == "callout")
                    .and_then(|p| p.lines.first())
                    .map(|l| l[1]),
            )
        }
        AnnotationItem::Gdt(g) => {
            out.annotations
                .extend(crate::dims::gdt::render(g, &mut out.items, &mut out.text));
            let p = g.placed.as_ref();
            (
                Kind::Gdt,
                p.map(|p| p.label_box),
                p.map(|p| p.lines.clone()).unwrap_or_default(),
                // A datum shaft ends at its frame; it has no callout elbow.
                p.filter(|_| g.kind != "datum_feature")
                    .and_then(|p| p.lines.first())
                    .map(|l| l[1]),
            )
        }
    };
    if let (Some(a), Some(ink)) = (out.annotations.first(), items_bbox(&out.items)) {
        out.drawn.push(Drawn {
            datum_triangle: match item {
                AnnotationItem::Gdt(g) if g.kind == "datum_feature" => g
                    .placed
                    .as_ref()
                    .and_then(|p| p.fills.first())
                    .and_then(|f| f.as_slice().try_into().ok()),
                _ => None,
            },
            heads,
            name: a.name.clone(),
            kind,
            ink,
            label,
            lines,
            elbow,
            interior,
        });
    }
    out
}

/// [`render`], projecting each view through *projections*: one kept across a part's renders
/// draws the same drawing without projecting a view again.
pub fn render_with(
    part: &Part,
    analysis: &Analysis,
    plan: &Plan,
    font: &Font,
    projections: &mut Projections,
) -> Result<Drawing, Unresolved> {
    if !plan.sheets.is_empty() {
        return render_sheet_with(part, analysis, plan, &plan.sheet, font, projections);
    }
    let geometry = render_geometry(part, analysis, plan, projections)?;
    render_annotations(part, analysis, plan, font, geometry, projections)
}

/// Draw every planned sheet in order, each carrying the whole Spec. Projections are shared
/// across the document; [`render`] remains the sheet-1 entry point.
pub fn render_sheets(
    part: &Part,
    analysis: &Analysis,
    plan: &Plan,
    font: &Font,
) -> Result<Vec<Drawing>, Unresolved> {
    render_sheets_with(part, analysis, plan, font, &mut Projections::default())
}

/// Pack rendering keeps projections when a failed part changes the final page total.
pub(crate) fn render_sheets_with(
    part: &Part,
    analysis: &Analysis,
    plan: &Plan,
    font: &Font,
    projections: &mut Projections,
) -> Result<Vec<Drawing>, Unresolved> {
    let mut drawings = vec![render_with(part, analysis, plan, font, projections)?];
    for sheet in &plan.sheets {
        drawings.push(render_sheet_with(
            part,
            analysis,
            plan,
            sheet,
            font,
            projections,
        )?);
    }
    Ok(drawings)
}

fn render_sheet_with(
    part: &Part,
    analysis: &Analysis,
    plan: &Plan,
    sheet: &PlannedSheet,
    font: &Font,
    projections: &mut Projections,
) -> Result<Drawing, Unresolved> {
    let mut selected = plan.clone();
    selected.sheet = sheet.clone();
    selected
        .inspection_balloons
        .retain(|b| b.sheet == sheet.number);
    selected.views.retain(|v| v.sheet == sheet.number);
    selected.tables.retain(|t| t.sheet == sheet.number);
    selected.hole_table = selected.hole_table.filter(|t| t.sheet == sheet.number);
    selected.hole_tables.retain(|t| t.sheet == sheet.number);
    let carries = |id: &str| selected.views.iter().any(|v| v.id == id);
    selected.dimensions.retain(|d| carries(&d.view));
    selected.gdt.retain(|g| carries(&g.view));
    selected.balloons.retain(|b| carries(&b.view));
    if sheet.number > 1 {
        selected.notes.clear();
        selected.notes_block = None;
        selected.radius_notes_block = None;
        selected.radius_table = None;
        selected.requirements.clear();
        selected.omitted.clear();
        selected.sheet.projection_symbol = None;
    }
    let geometry = render_geometry(part, analysis, &selected, projections)?;
    let remote: Vec<_> = plan
        .views
        .iter()
        .filter(|v| {
            v.sheet != sheet.number && v.cut.as_ref().is_some_and(|c| carries(&c.marked_in))
        })
        .collect();
    render_annotations_with_cuts(
        part,
        analysis,
        &selected,
        font,
        geometry,
        projections,
        &BTreeMap::new(),
        &remote,
    )
}

/// ISO 5457 centring-mark line width (at least 0.7 mm) and its reach inside the frame, mm.
const CENTRING_MARK: f64 = 0.7;
const CENTRING_INSET: f64 = 5.0;

/// Assembles a geometry layer with the plan's annotations and sheet furniture. Each dimension
/// and PMI item uses [`render_item`], so a caller can draw a changed item alone.
pub fn render_annotations(
    part: &Part,
    analysis: &Analysis,
    plan: &Plan,
    font: &Font,
    geometry: GeometryLayer,
    projections: &mut Projections,
) -> Result<Drawing, Unresolved> {
    render_annotations_with_items(
        part,
        analysis,
        plan,
        font,
        geometry,
        projections,
        &BTreeMap::new(),
    )
}

/// Assembles the same drawing, reusing independently rendered annotations by ID.
pub fn render_annotations_with_items(
    part: &Part,
    analysis: &Analysis,
    plan: &Plan,
    font: &Font,
    geometry: GeometryLayer,
    projections: &mut Projections,
    items: &BTreeMap<String, RenderedItem>,
) -> Result<Drawing, Unresolved> {
    render_annotations_with_cuts(
        part,
        analysis,
        plan,
        font,
        geometry,
        projections,
        items,
        &[],
    )
}

/// Session assembly selects the already allocated page and keeps geometry in Plan order.
/// Sheet furniture remains the renderer's responsibility (R5 I2).
#[allow(clippy::too_many_arguments)]
pub(crate) fn render_annotations_on_sheet(
    part: &Part,
    analysis: &Analysis,
    plan: &Plan,
    font: &Font,
    mut geometry: GeometryLayer,
    projections: &mut Projections,
    items: &BTreeMap<String, RenderedItem>,
    number: u32,
) -> Result<Drawing, Unresolved> {
    if plan.sheets.is_empty() && number == 1 {
        return render_annotations_with_items(
            part,
            analysis,
            plan,
            font,
            geometry,
            projections,
            items,
        );
    }
    let mut selected = plan.clone();
    selected.sheet = std::iter::once(&plan.sheet)
        .chain(&plan.sheets)
        .find(|s| s.number == number)
        // Invariant: The session validates this sheet against its plan before rendering.
        .expect("the session validates its sheet number")
        .clone();
    selected.views.retain(|v| v.sheet == number);
    let local = |id: &str| selected.views.iter().any(|v| v.id == id);
    geometry.views.retain(|(id, _)| local(id));
    selected.dimensions.retain(|d| local(&d.view));
    selected.gdt.retain(|g| local(&g.view));
    selected.balloons.retain(|b| local(&b.view));
    selected.inspection_balloons.retain(|b| b.sheet == number);
    selected.tables.retain(|t| t.sheet == number);
    selected.hole_table = selected.hole_table.filter(|t| t.sheet == number);
    selected.hole_tables.retain(|t| t.sheet == number);
    if number != 1 {
        selected.notes.clear();
        selected.requirements.clear();
        selected.radius_table = None;
        selected.sheet.projection_symbol = None;
    }
    let remote = plan
        .views
        .iter()
        .filter(|v| v.sheet != number && v.cut.as_ref().is_some_and(|c| local(&c.marked_in)))
        .collect::<Vec<_>>();
    render_annotations_with_cuts(
        part,
        analysis,
        &selected,
        font,
        geometry,
        projections,
        items,
        &remote,
    )
}

#[allow(clippy::too_many_arguments)]
fn render_annotations_with_cuts(
    part: &Part,
    analysis: &Analysis,
    plan: &Plan,
    font: &Font,
    geometry: GeometryLayer,
    projections: &mut Projections,
    items: &BTreeMap<String, RenderedItem>,
    remote_sections: &[&PlannedView],
) -> Result<Drawing, Unresolved> {
    projections.bind(analysis, &plan.spec);
    // A tangent/gap plane has retained geometry, but no new cut face. Refuse
    // that section's ink and report it, rather than captioning the retained half
    // as a manufactured section (ISSUE-1530-a). The rest of the sheet still draws.
    let mut rejected_sections = BTreeMap::new();
    for v in plan.views.iter().chain(remote_sections.iter().copied()) {
        let Some(cut) = &v.cut else {
            continue;
        };
        let Some(plane) = Plane::new(cut.origin, cut.normal) else {
            continue;
        };
        let [lo, hi] = analysis.envelope;
        let limits = [
            (0..3)
                .map(|k| plane.normal[k] * if plane.normal[k] >= 0.0 { lo[k] } else { hi[k] })
                .sum::<f64>(),
            (0..3)
                .map(|k| plane.normal[k] * if plane.normal[k] >= 0.0 { hi[k] } else { lo[k] })
                .sum::<f64>(),
        ];
        let at = (0..3).map(|k| plane.normal[k] * cut.origin[k]).sum::<f64>();
        let reason = if at <= limits[0] || at >= limits[1] {
            Some("the plane is not strictly inside the part's open extent")
        } else if !{
            let carrier = crate::modelled_threads::carrier(
                part,
                analysis,
                &plan.spec,
                v.line_style.thread_depiction != Some(false),
            );
            projections.cut_material(
                carrier.as_ref().unwrap_or(part),
                cut,
                &plane,
                &crate::modelled_threads::cache_key(analysis, &plan.spec, v),
            )?
        } {
            Some("cut_no_intersection: no material crosses the cutting plane")
        } else {
            None
        };
        if let Some(reason) = reason {
            rejected_sections.insert(v.id.clone(), reason);
        }
    }
    // Section furniture (hatch, cutting plane), and its text: what, where.
    let mut furniture: Vec<Item> = Vec::new();
    let mut captions: Vec<(String, [f64; 2])> = Vec::new();
    // What lint's sheet checks read: each view's visible box, and each thing drawn, by name.
    let mut sheet = Sheet::default();
    let mut caption_names: Vec<String> = Vec::new();
    let mut references = Vec::new();
    let mut layers: Vec<Layer> = layer_styles()
        .into_iter()
        .map(|(name, style)| Layer {
            name: name.into(),
            style,
            items: Vec::new(),
        })
        .collect();
    layers.push(Layer {
        name: "centres".into(),
        style: crate::output::centre_style(),
        items: Vec::new(),
    });
    // Threads and knurls drawn over turned steps' outlines, on a layer of their own that only a
    // part carrying them has (so every other sheet's bytes are unchanged).
    let manufacturing = if plan.spec.pmi == PmiMode::Ignore {
        crate::pmi_lowering::Manufacturing::default()
    } else {
        crate::pmi_lowering::manufacturing(analysis)
    };
    let mut thin: Vec<Item> = Vec::new();
    let mut undrawn = BTreeMap::new();
    // Invariant: Session geometry is generated in this same immutable plan view order.
    assert_eq!(
        plan.views.len(),
        geometry.views.len(),
        "geometry covers every planned view"
    );
    for (v, (name, geometry)) in plan.views.iter().zip(geometry.views) {
        // Invariant: Session geometry is generated in this same immutable plan view order.
        assert_eq!(v.id, name, "geometry follows plan view order");
        if rejected_sections.contains_key(&v.id) {
            continue;
        }
        if geometry.undrawn > 0 {
            undrawn.insert(v.id.clone(), geometry.undrawn);
        }
        if let Some(bbox) = geometry.bbox {
            sheet.views.push(DrawnView {
                name: v.id.clone(),
                bbox,
                edges: geometry
                    .visible
                    .iter()
                    .filter_map(|i| match i {
                        Item::Edge(c) => Some(c.clone()),
                        Item::Face(_) => None,
                    })
                    .collect(),
            });
        }
        sheet.frames.insert(
            v.id.trim_start_matches("v:").into(),
            crate::output::DrawnFrame {
                direction: v.direction,
                up: v.up,
                // DrawnFrame records where the whole part's centre lands. A detail is
                // laid out about its crop centre; keep lint's world-to-page transform exact.
                at: if v.detail.is_some() {
                    // Invariant: Planned cameras come from the fixed views or checked section/detail cameras, with nonparallel axes.
                    let view = View::new(v.direction, v.up).expect("planned view");
                    let whole = extent(analysis, &view).1;
                    let crop = crate::detail::crop_extent(analysis, v).1;
                    [0, 1].map(|k| v.at[k] + v.scale * (whole[k] - crop[k]))
                } else {
                    v.at
                },
                scale: v.scale,
                half_axis: v.half_section.as_ref().map(|h| h.page_axis(analysis, v)),
                broken: v.broken.as_ref().map(|b| b.geometry.clone()),
            },
        );
        for w in v.line_work.iter().filter(|w| w.kind != "cutting_plane") {
            let lines = crate::centrelines::segments(w);
            let ink: Vec<_> = w.curves.iter().cloned().map(Item::Edge).collect();
            if let Some(ink_box) = items_bbox(&ink) {
                sheet.items.push(Drawn {
                    datum_triangle: None,
                    heads: Vec::new(),
                    name: w.id.clone(),
                    kind: Kind::Centreline,
                    ink: ink_box,
                    label: None,
                    lines,
                    elbow: None,
                    interior: false,
                });
            }
            if let Some(c) = w.centre {
                sheet
                    .centre_marks
                    .entry(v.id.trim_start_matches("v:").into())
                    .or_default()
                    .push(c);
            }
            layers[3].items.extend(ink);
        }
        let mut local_thin: Vec<_> = crate::cosmetic::ink(analysis, &manufacturing, v)
            .into_iter()
            .map(Item::Edge)
            .collect();
        let carrier = crate::modelled_threads::carrier(
            part,
            analysis,
            &plan.spec,
            v.line_style.thread_depiction != Some(false),
        );
        let (_, thread_thin, _) =
            crate::modelled_threads::ink(analysis, &plan.spec, v, carrier.as_ref().unwrap_or(part));
        local_thin.extend(thread_thin);
        if let Some(b) = &v.broken {
            local_thin = b.items(local_thin, crate::detail::crop_extent(analysis, v).1, v);
        }
        thin.extend(local_thin);
        thin.extend(geometry.breaks.into_iter().map(Item::Edge));
        layers[0].items.extend(geometry.visible);
        layers[1].items.extend(geometry.hidden);
    }
    for v in &plan.views {
        let Some(d) = &v.detail else { continue };
        captions.push((crate::detail::caption(d), crate::detail::caption_at(v)));
        caption_names.push(format!("detail_caption_{}", d.label));
        if let Some(source) = plan.views.iter().find(|s| s.id == d.source_view) {
            let labels: Vec<_> = plan
                .dimensions
                .iter()
                .filter_map(|d| d.placed.as_ref().map(|p| p.label_box))
                .chain(
                    plan.gdt
                        .iter()
                        .filter_map(|g| g.placed.as_ref().map(|p| p.label_box)),
                )
                .chain(plan.balloons.iter().filter_map(|b| b.label_box))
                .collect();
            let mut avoid = labels.clone();
            avoid.extend(
                plan.dimensions
                    .iter()
                    .filter_map(|d| d.placed.as_ref())
                    .flat_map(|p| p.lines.iter())
                    .map(|s| {
                        [
                            s[0][0].min(s[1][0]) - 0.5,
                            s[0][1].min(s[1][1]) - 0.5,
                            s[0][0].max(s[1][0]) + 0.5,
                            s[0][1].max(s[1][1]) + 0.5,
                        ]
                    }),
            );
            let (circle, at) =
                crate::detail::marker(analysis, d, source, &plan.views, plan.sheet.page, &avoid);
            let [pw, ph] = plan.sheet.page;
            let drawable = [
                layout::margin(),
                layout::margin(),
                pw - layout::margin(),
                ph - layout::margin(),
            ];
            let curves = crate::detail::clip(circle, drawable)
                .into_iter()
                .flat_map(|arc| crate::detail::marker_ink(arc, &labels))
                .collect::<Vec<_>>();
            let lines = curves
                .iter()
                .flat_map(|c| {
                    (0..16).map(move |i| [c.at(i as f64 / 16.0), c.at((i + 1) as f64 / 16.0)])
                })
                .collect();
            let ink: Vec<_> = curves.into_iter().map(Item::Edge).collect();
            if let Some(b) = items_bbox(&ink) {
                sheet.items.push(Drawn {
                    datum_triangle: None,
                    heads: Vec::new(),
                    name: format!("detail_marker_{}", d.label),
                    kind: Kind::Centreline,
                    ink: b,
                    label: None,
                    lines,
                    elbow: None,
                    interior: false,
                });
            }
            layers[3].items.extend(ink);
            captions.push((d.label.clone(), at));
            caption_names.push(format!("detail_marker_label_{}", d.label));
        }
    }
    if !thin.is_empty() {
        layers.push(Layer {
            name: "thin".into(),
            style: crate::output::thin_style(),
            items: thin,
        });
    }
    // A half section's hatch, clipped to its cut half; it has no caption or cutting-plane line
    // (ISO 128-50: the cut through the axis of symmetry is obvious).
    for v in &plan.views {
        let Some(h) = &v.half_section else { continue };
        let cut = h.cut(v);
        let Some(plane) = Plane::new(cut.origin, cut.normal) else {
            continue;
        };
        let keep = h.rect(analysis, v, true);
        let ink: Vec<Item> = section_hatch(part, analysis, plan, v, (&plane, &cut), projections)?
            .into_iter()
            .flat_map(|item| match item {
                Item::Edge(c) => crate::detail::clip(c, keep)
                    .into_iter()
                    .map(Item::Edge)
                    .collect(),
                item => vec![item],
            })
            .collect();
        if let Some(bbox) = items_bbox(&ink) {
            sheet.items.push(Drawn {
                datum_triangle: None,
                heads: Vec::new(),
                name: format!("{}/half_section_hatch", v.id),
                kind: Kind::Hatch,
                ink: bbox,
                label: None,
                lines: Vec::new(),
                elbow: None,
                interior: false,
            });
        }
        furniture.extend(ink);
    }
    for v in plan.views.iter().chain(remote_sections.iter().copied()) {
        if rejected_sections.contains_key(&v.id) {
            continue;
        }
        let plane = v.cut.as_ref().and_then(|c| Plane::new(c.origin, c.normal));
        if let (Some(plane), Some(cut)) = (&plane, &v.cut) {
            let remote = v.sheet != plan.sheet.number;
            let f = if remote {
                let mut f = Furniture {
                    ink: Vec::new(),
                    hatch: 0,
                    chain: None,
                    text: Vec::new(),
                };
                cutting_furniture(plan, cut, &mut f);
                f
            } else {
                section_furniture(part, analysis, plan, v, (plane, cut), projections)?
            };
            // draftwright's pieces, each by its ink: the hatch, the chain line by its line, and
            // each end's arrowhead and wing stub (`section::cutting_plane`'s order).
            let mut piece = |name: String, kind: Kind, ink: &[Item], lines: Vec<[[f64; 2]; 2]>| {
                if let Some(ink) = items_bbox(ink) {
                    sheet.items.push(Drawn {
                        datum_triangle: None,
                        heads: Vec::new(),
                        name,
                        kind,
                        ink,
                        label: None,
                        lines,
                        elbow: None,
                        interior: false,
                    });
                }
            };
            piece(
                "section_hatch".into(),
                Kind::Hatch,
                &f.ink[..f.hatch],
                Vec::new(),
            );
            let plane_ink = &f.ink[f.hatch..];
            if let Some(line) = f.chain {
                let chain = plane_ink.len() - 4;
                piece(
                    "section_line".into(),
                    Kind::Centreline,
                    &plane_ink[..chain],
                    vec![line],
                );
                for (k, side) in ["left", "right"].into_iter().enumerate() {
                    let at = chain + 2 * k;
                    let (wing, head) = (&plane_ink[at..at + 1], &plane_ink[at + 1..at + 2]);
                    piece(
                        format!("section_arrow_{side}"),
                        Kind::CuttingPlane,
                        head,
                        Vec::new(),
                    );
                    piece(
                        format!("section_wing_{side}"),
                        Kind::CuttingPlane,
                        wing,
                        Vec::new(),
                    );
                }
            }
            // draftwright's names: the caption, then the cutting plane's letters.
            let l = cut.label.to_lowercase();
            if !remote {
                caption_names.push("section_caption".into());
            }
            if f.chain.is_some() {
                caption_names.extend([format!("section_{l}_left"), format!("section_{l}_right")]);
            }
            furniture.extend(f.ink);
            captions.extend(f.text);
            for (reference, prefix, name, target) in [
                (
                    v.xref.as_ref().filter(|_| !remote),
                    "FROM".to_string(),
                    format!("{}/xref", v.id),
                    format!("section_{l}_left"),
                ),
                (
                    cut.xref.as_ref().filter(|_| f.chain.is_some()),
                    format!("SECTION {}–{},", cut.label, cut.label),
                    format!("{}/cut/xref", v.id),
                    v.id.clone(),
                ),
            ] {
                if let Some(r) = reference {
                    references.push((
                        captions.len(),
                        name.clone(),
                        prefix.clone(),
                        target,
                        r.clone(),
                    ));
                    captions.push((format!("{prefix} SHEET {}, ZONE {}", r.sheet, r.zone), r.at));
                    caption_names.push(name);
                }
            }
        }
    }
    for v in &plan.views {
        if let Some(note) = &v.scale_note {
            if rejected_sections.contains_key(&v.id) {
                continue;
            }
            // Invariant: Planned cameras come from the fixed views or checked section/detail cameras, with nonparallel axes.
            let view = View::new(v.direction, v.up).expect("a planned view");
            let half_h = 0.5 * extent(analysis, &view).0[1] * v.scale;
            let gap = if v.cut.is_some() { 17.0 } else { 7.0 };
            captions.push((note.clone(), [v.at[0], v.at[1] - half_h - gap]));
            caption_names.push(format!("{}/scale_note", v.id));
        }
    }
    // The border and the title block, lower right.
    let [w, h] = plan.sheet.page;
    let rect = |x0: f64, y0: f64, x1: f64, y1: f64| {
        [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]
            .windows(2)
            .map(|p| Item::Edge(Curve::Line(p[0], p[1])))
            .collect::<Vec<_>>()
    };
    let part_layer = &mut layers[0].items;
    part_layer.extend(rect(FRAME, FRAME, w - FRAME, h - FRAME));
    sheet.drawable = Some([FRAME, FRAME, w - FRAME, h - FRAME]);
    // The block is the grid's four rows at the bottom of the box the layout reserves for it.
    let block = plan.sheet.title_block;
    let [tx0, ty0, tx1, _] = block;
    // Its sides on the frame (ISO 7200: flush at the right and bottom) are the frame's line,
    // drawn once.
    let on_frame = |e: &Item| match e {
        Item::Edge(Curve::Line(a, b)) => [0, 1].into_iter().any(|k| {
            [FRAME, [w, h][k] - FRAME]
                .iter()
                .any(|f| (a[k] - f).abs() < 1e-9 && (b[k] - f).abs() < 1e-9)
        }),
        _ => false,
    };
    part_layer.extend(
        rect(tx0, ty0, tx1, title_block::top(block))
            .into_iter()
            .filter(|e| !on_frame(e)),
    );
    let mut text = Vec::new();
    // The dimensions and callouts, drawn from the plan alone, one at a time so each one's ink
    // is measured.
    let mut annotations = Vec::new();
    for d in &plan.dimensions {
        if rejected_sections.contains_key(&d.view) {
            continue;
        }
        items
            .get(&d.id)
            .cloned()
            .unwrap_or_else(|| render_item(AnnotationItem::Dimension(d)))
            .append(
                &mut layers[2].items,
                &mut text,
                &mut annotations,
                &mut sheet,
            );
    }
    // The feature control frames and datum feature symbols, likewise.
    for g in &plan.gdt {
        items
            .get(&g.id)
            .cloned()
            .unwrap_or_else(|| render_item(AnnotationItem::Gdt(g)))
            .append(
                &mut layers[2].items,
                &mut text,
                &mut annotations,
                &mut sheet,
            );
    }
    // Glyphs *laid* out drawn moved to *at*, and the *run* (text, font file, size), if any, as
    // invisible text centred there; the run's index in the drawing's text.
    let mut label = |laid: Laid,
                     at: [f64; 2],
                     layer: &mut Vec<Item>,
                     run: Option<(&str, &str, f64)>|
     -> Option<usize> {
        let shift = |p: [f64; 2]| [p[0] + at[0], p[1] + at[1]];
        let curves: Vec<Curve> = laid
            .contours
            .iter()
            .flat_map(|c| c.iter())
            .map(|seg| match *seg {
                Segment::Line(a, b) => Curve::Line(shift(a), shift(b)),
                Segment::Quad(a, b, c) => Curve::Beziers(vec![vec![shift(a), shift(b), shift(c)]]),
                Segment::Cubic(a, b, c, d) => {
                    Curve::Beziers(vec![vec![shift(a), shift(b), shift(c), shift(d)]])
                }
            })
            .collect();
        if !curves.is_empty() {
            // One path of every contour: TrueType's winding makes the holes.
            let wires = laid
                .contours
                .iter()
                .scan(0usize, |start, c| {
                    let wire: Vec<Curve> = curves[*start..*start + c.len()].to_vec();
                    *start += c.len();
                    Some(wire)
                })
                .filter(|w| !w.is_empty())
                .collect();
            layer.push(Item::Face(wires));
        }
        let (s, font, size) = run?;
        text.push(TextRun {
            text: s.into(),
            at,
            size,
            rotation: 0.0,
            font: font.into(),
            h_align: HAlign::Center,
            v_align: VAlign::Middle,
        });
        Some(text.len() - 1)
    };
    let dims = &mut layers[2].items;
    dims.extend(furniture);
    // A text-only thing as drawn: the box of its ink *items*, which is also its label.
    let labelled = |name: &str, kind: Kind, items: &[Item]| {
        items_bbox(items).map(|b| Drawn {
            datum_triangle: None,
            heads: Vec::new(),
            name: name.into(),
            kind,
            ink: b,
            label: Some(b),
            lines: Vec::new(),
            elbow: None,
            interior: false,
        })
    };
    for (index, ((s, at), name)) in captions.iter().zip(&caption_names).enumerate() {
        let size = crate::section::FONT_SIZE;
        let run = (s.as_str(), "IBMPlexMono-Regular.ttf", size);
        let from = dims.len();
        if let Some(run) = label(font.text(s, size), *at, dims, Some(run))
            && let Some((_, name, prefix, target, r)) =
                references.iter().find(|(i, ..)| *i == index)
        {
            sheet.references.push(crate::output::DrawnReference {
                name: name.clone(),
                target: target.clone(),
                prefix: prefix.clone(),
                sheet: r.sheet,
                zone: r.zone.clone(),
                run,
            });
        }
        sheet
            .items
            .extend(labelled(name, Kind::Caption, &dims[from..]));
    }
    if let Some(grid) = &plan.sheet.zones {
        // All zone furniture lives outside the drawable border, preserving every view band.
        for n in 0..grid.columns {
            let x = (f64::from(n) + 0.5) * w / f64::from(grid.columns);
            let value = (n + 1).to_string();
            for y in [FRAME / 2., h - FRAME / 2.] {
                label(
                    font.text(&value, 3.),
                    [x, y],
                    dims,
                    Some((&value, "IBMPlexMono-Regular.ttf", 3.)),
                );
            }
            if n > 0 {
                let x = f64::from(n) * w / f64::from(grid.columns);
                for (a, b) in [(0., FRAME), (h - FRAME, h)] {
                    dims.push(Item::Edge(Curve::Line([x, a], [x, b])));
                }
            }
        }
        for n in 0..grid.rows {
            let y = h - (f64::from(n) + 0.5) * h / f64::from(grid.rows);
            let value = (b"ABCDEFGHJKLMNPQRSTUVWXYZ"[n as usize] as char).to_string();
            for x in [FRAME / 2., w - FRAME / 2.] {
                label(
                    font.text(&value, 3.),
                    [x, y],
                    dims,
                    Some((&value, "IBMPlexMono-Regular.ttf", 3.)),
                );
            }
            if n > 0 {
                let y = h - f64::from(n) * h / f64::from(grid.rows);
                for (a, b) in [(0., FRAME), (w - FRAME, w)] {
                    dims.push(Item::Edge(Curve::Line([a, y], [b, y])));
                }
            }
        }
    }
    // Each cell: its dividers, its label at its lower left, and its value (none drawn for a
    // field not supplied) centred in it, whole, and searchable. The ink is the font-safe value.
    let title_from = dims.len();
    // The runs that state the sheet's PMI requirements, by requirement ID.
    let mut stated: BTreeMap<String, Vec<usize>> = BTreeMap::new();
    let title_material = match &analysis.pmi {
        Some(crate::pmi::Reading::Read(r)) => r.title_material(),
        _ => None,
    };
    for c in &plan.sheet.title_cells {
        let [x0, y0, x1, y1] = c.cell;
        if x0 > tx0 {
            dims.push(Item::Edge(Curve::Line([x0, y0], [x0, y1])));
        } else if y1 < title_block::top(block) {
            // A row's first cell draws the line along the row's top.
            dims.push(Item::Edge(Curve::Line([tx0, y1], [tx1, y1])));
        }
        let laid = title_block::lay(&c.label, title_block::LABEL_TEXT);
        let at = [
            x0 + title_block::LABEL_PAD - laid.min[0],
            y0 + title_block::LABEL_PAD - laid.min[1],
        ];
        // Pack identifiers are also searchable in exported sheets. Existing title labels
        // retain their ordinary outline-only representation.
        let run = matches!(c.field.as_str(), "pack_item" | "pack_qty").then_some((
            c.label.as_str(),
            title_block::FONT,
            title_block::LABEL_TEXT,
        ));
        label(laid, at, dims, run);
        if let Some(v) = &c.value {
            let centre = [0.5 * (x0 + x1), 0.5 * (y0 + y1)];
            let size = title_block::value_size(v, c.cell);
            let run = (v.as_str(), title_block::FONT, size);
            let from = dims.len();
            let at = label(title_block::lay(v, size), centre, dims, Some(run));
            for r in &plan.requirements {
                if r.carrier == "title_block" && c.field == "general_tolerance" {
                    stated.entry(r.id.clone()).or_default().extend(at);
                }
            }
            // A material cell that says the file's material states its source.
            if c.field == "material"
                && let Some(m) = &title_material
                && v.as_str() == m.designation.trim()
            {
                stated
                    .entry(format!("pmi:{}", m.census_id()))
                    .or_default()
                    .extend(at);
            }
            if let Some(ink) = items_bbox(&dims[from..]) {
                sheet.fields.push(DrawnField {
                    field: c.field.clone(),
                    cell: c.cell,
                    ink,
                });
            }
        }
    }
    // The block's outline and every cell's ink.
    let outline = [tx0, ty0, tx1, title_block::top(block)];
    let ink = items_bbox(&dims[title_from..]).map_or(outline, |b| {
        [
            b[0].min(outline[0]),
            b[1].min(outline[1]),
            b[2].max(outline[2]),
            b[3].max(outline[3]),
        ]
    });
    // Its rules: the outline, and each cell's divider.
    let outline_rules = [[tx0, ty0], [tx1, ty0], [tx1, outline[3]], [tx0, outline[3]]];
    let mut rules: Vec<[[f64; 2]; 2]> = (0..4)
        .map(|k| [outline_rules[k], outline_rules[(k + 1) % 4]])
        .collect();
    rules.extend(dims[title_from..].iter().filter_map(|i| match i {
        Item::Edge(Curve::Line(a, b)) => Some([*a, *b]),
        _ => None,
    }));
    sheet.items.push(Drawn {
        datum_triangle: None,
        heads: Vec::new(),
        name: "title_block".into(),
        kind: Kind::TitleBlock,
        ink,
        label: None,
        lines: rules,
        elbow: None,
        interior: false,
    });
    // The general notes: the block's rules, and each row centred on its planned ink, searchable.
    let mixed = plan.notes.iter().any(|n| !n.id.starts_with("n:f:blend:"));
    for (i, block) in [plan.notes_block.as_ref(), plan.radius_notes_block.as_ref()]
        .into_iter()
        .enumerate()
    {
        if i == 1 && !mixed {
            continue;
        }
        let notes_from = dims.len();
        if let Some(block) = block {
            for [a, b] in crate::notes::rules(block) {
                dims.push(Item::Edge(Curve::Line(a, b)));
            }
            let row = block.title.as_str();
            let laid = crate::output::centred_label_ink(
                crate::tables::text_ink(row, font),
                block.title_at,
                dims,
            );
            label(
                laid,
                block.title_at,
                dims,
                Some((row, crate::dims::FONT, crate::notes::TEXT)),
            );
        }
        for n in plan.notes.iter().filter(|n| {
            n.placed.is_some() && (!mixed || (i == 1) == n.id.starts_with("n:f:blend:"))
        }) {
            // Invariant: The notes iterator selected only notes with placements.
            let p = n.placed.as_ref().unwrap();
            let runs: Vec<usize> = n
                .rows
                .iter()
                .zip(&p.rows_at)
                .filter_map(|(row, at)| {
                    let laid = crate::output::centred_label_ink(
                        crate::tables::text_ink(row, font),
                        *at,
                        dims,
                    );
                    label(
                        laid,
                        *at,
                        dims,
                        Some((row.trim_start(), crate::dims::FONT, crate::notes::TEXT)),
                    )
                })
                .collect();
            if n.id.starts_with("pmi:") {
                stated.entry(n.id.clone()).or_default().extend(&runs);
            }
            if !n.measurements.is_empty() || n.id == crate::dims::blends::RELIEF_NOTE {
                annotations.push(Annotation {
                    approved_claims: Vec::new(),
                    approved_text: None,
                    coordinate_origin: None,
                    claims: Vec::new(),
                    name: n.id.clone(),
                    view: None,
                    runs,
                    satisfies: n
                        .measurements
                        .iter()
                        .map(|m| crate::output::Claim {
                            feature: m.feature.clone(),
                            parameter: m.parameter.clone(),
                        })
                        .collect(),
                });
            }
        }
        if let Some(ink) = items_bbox(&dims[notes_from..]) {
            sheet.items.push(Drawn {
                datum_triangle: None,
                heads: Vec::new(),
                name: if i == 0 {
                    "general_notes"
                } else {
                    "blend_radius_notes"
                }
                .into(),
                kind: Kind::Notes,
                ink,
                label: None,
                lines: Vec::new(),
                elbow: None,
                interior: false,
            });
        }
    }
    // A part note covered by an identical one the block states (an assembly's parts stating
    // the same note) is stated by that note's runs.
    for n in &plan.notes {
        if let ItemState::Covered { by } = &n.state
            && n.id.starts_with("pmi:")
            && let Some(runs) = stated.get(by).cloned()
        {
            stated.entry(n.id.clone()).or_default().extend(runs);
        }
    }
    // The default surface finish beside the title block: its symbol and its requirement.
    for r in &plan.requirements {
        let Some(p) = &r.placed else { continue };
        let from = dims.len();
        for [a, b] in &p.strokes {
            dims.push(Item::Edge(Curve::Line(*a, *b)));
        }
        // A material removal condition's circle (`no material removal`), when it has one.
        for a in &p.arcs {
            dims.push(Item::Edge(Curve::Conic {
                centre: [a[0], a[1]],
                radii: [a[2], a[2]],
                x_axis: [1.0, 0.0],
                y_axis: [0.0, 1.0],
                t0: a[3].to_radians(),
                t1: a[4].to_radians(),
            }));
        }
        let run = (r.text.as_str(), title_block::FONT, title_block::TEXT);
        let at = label(
            title_block::lay(&r.text, title_block::TEXT),
            p.text_at,
            dims,
            Some(run),
        );
        stated.entry(r.id.clone()).or_default().extend(at);
        if let Some(ink) = items_bbox(&dims[from..]) {
            sheet.items.push(Drawn {
                datum_triangle: None,
                heads: Vec::new(),
                name: r.id.clone(),
                kind: Kind::Gdt,
                ink,
                label: Some(p.label_box),
                lines: p.strokes.clone(),
                elbow: None,
                interior: false,
            });
        }
    }
    // The tables: each one's rules, and each cell centred on its planned ink, searchable.
    for table in &plan.tables {
        let Some(p) = &table.placed else { continue };
        let table_from = dims.len();
        for [a, b] in crate::tables::rules(p) {
            dims.push(Item::Edge(Curve::Line(a, b)));
        }
        let mut manufacturing_runs: BTreeMap<String, Vec<usize>> = BTreeMap::new();
        let mut manufacturing_header = Vec::new();
        let mut data_runs = Vec::new();
        let mut tag = None;
        for (draw_row, (r, row, positions)) in
            crate::tables::cell_rows(table).into_iter().enumerate()
        {
            if table.id == crate::pmi_lowering::SCHEDULE_ID && r > 0 && !row[0].is_empty() {
                tag = Some(row[0].clone());
            }
            for (cell, at) in row.iter().zip(positions) {
                let Some(at) = at else { continue };
                let run = (cell.trim(), crate::dims::FONT, crate::tables::TEXT);
                let laid = crate::output::centred_label_ink(
                    crate::tables::text_ink(cell, font),
                    *at,
                    dims,
                );
                let index = label(laid, *at, dims, Some(run));
                if table.id.starts_with("gear_data_") || table.id.starts_with("spring_data_") {
                    data_runs.extend(index);
                }
                // One canonical header certifies the whole table. Continuation headers
                // repeat the same visible cells without duplicating its provenance.
                if table.id == crate::pmi_lowering::SCHEDULE_ID && draw_row == 0 {
                    manufacturing_header.extend(index);
                }
                if let Some(tag) = &tag
                    && r > 0
                {
                    manufacturing_runs
                        .entry(tag.clone())
                        .or_default()
                        .extend(index);
                }
            }
        }
        if let Some(owner) = table.id.strip_prefix("gear_data_") {
            annotations.push(Annotation {
                name: table.id.clone(),
                runs: data_runs.clone(),
                claims: vec![Claim {
                    feature: format!("gear:{owner}"),
                    parameter: "gear.requirement".into(),
                }],
                ..Default::default()
            });
        }
        if let Some(owner) = table.id.strip_prefix("spring_data_") {
            annotations.push(Annotation {
                name: table.id.clone(),
                runs: data_runs.clone(),
                claims: vec![Claim {
                    feature: owner.into(),
                    parameter: "spring.data".into(),
                }],
                ..Default::default()
            });
        }
        if !manufacturing_header.is_empty() {
            annotations.push(Annotation {
                name: "manufacturing_requirements_header".into(),
                runs: manufacturing_header,
                ..Default::default()
            });
        }
        if !manufacturing_runs.is_empty() {
            for binding in &table.row_claims {
                if let Some(runs) = manufacturing_runs.remove(&binding.tag) {
                    annotations.push(Annotation {
                        name: format!("{}/{}", table.id, binding.tag),
                        approved_claims: binding.claims.clone(),
                        approved_text: None,
                        coordinate_origin: None,
                        view: None,
                        runs,
                        claims: Vec::new(),
                        satisfies: Vec::new(),
                    });
                }
            }
        }
        if let Some(ink) = items_bbox(&dims[table_from..]) {
            sheet.items.push(Drawn {
                datum_triangle: None,
                heads: Vec::new(),
                name: table.id.clone(),
                kind: Kind::Table,
                ink,
                label: None,
                lines: Vec::new(),
                elbow: None,
                interior: false,
            });
        }
    }
    // Each radius-centre row locates one occurrence, unlike a representative group site.
    if let Some(t) = &plan.radius_table
        && let Some(p) = &t.table.placed
    {
        let start = dims.len();
        for [a, b] in crate::tables::rules(p) {
            dims.push(Item::Edge(Curve::Line(a, b)));
        }
        for (i, row, at) in crate::tables::cell_rows(&t.table) {
            let mut runs = Vec::new();
            for (cell, at) in row.iter().zip(at) {
                let Some(at) = at else { continue };
                let laid = crate::output::centred_label_ink(
                    crate::tables::text_ink(cell, font),
                    *at,
                    dims,
                );
                runs.extend(label(
                    laid,
                    *at,
                    dims,
                    Some((cell, crate::dims::FONT, crate::tables::TEXT)),
                ));
            }
            if i > 0
                && let Some(radius) = t.rows.get(i - 1)
            {
                annotations.push(Annotation {
                    approved_claims: Vec::new(),
                    approved_text: Some(
                        row.iter()
                            .filter(|c| !c.is_empty())
                            .cloned()
                            .collect::<Vec<_>>()
                            .join(" "),
                    ),
                    coordinate_origin: None,
                    name: format!("{}/{}", t.table.id, radius.feature),
                    view: Some(radius.view.clone()),
                    runs,
                    claims: vec![Claim {
                        feature: radius.feature.clone(),
                        parameter: "blend.radius".into(),
                    }],
                    satisfies: Vec::new(),
                });
            }
        }
        if let Some(ink) = items_bbox(&dims[start..]) {
            sheet.items.push(Drawn {
                datum_triangle: None,
                heads: Vec::new(),
                name: t.table.id.clone(),
                kind: Kind::Table,
                ink,
                label: None,
                lines: Vec::new(),
                elbow: None,
                interior: false,
            });
        }
    }
    // The hole table, drawn as the tables are, block by block. Each row is an annotation for
    // lint: its cells' text (the tag apart) and the measurements they state, as draftwright
    // registers the table's measurements.
    for t in plan.hole_table.iter().chain(&plan.hole_tables) {
        let table_from = dims.len();
        let mut row_runs: Vec<Vec<Vec<usize>>> = t
            .rows
            .iter()
            .map(|r| vec![Vec::new(); r.cells.len()])
            .collect();
        let headers = if t.view.is_some() { 2 } else { 1 };
        let per = t
            .blocks
            .first()
            .map_or(0, |b| b.rows.len().saturating_sub(headers));
        for (k, block) in t.blocks.iter().enumerate() {
            let Some(p) = &block.placed else { continue };
            for [a, b] in crate::tables::rules(p) {
                dims.push(Item::Edge(Curve::Line(a, b)));
            }
            for (r, (row, at)) in block.rows.iter().zip(&p.cells_at).enumerate() {
                for (c, (cell, at)) in row.iter().zip(at).enumerate() {
                    let Some(at) = at else { continue };
                    let run = (cell.trim(), crate::dims::FONT, crate::tables::TEXT);
                    let laid = if t.view.is_some() && r >= headers && c == 1 {
                        // Reuse the established callout symbols in scheduled size cells;
                        // a font's missing-glyph box must not stand for recess or depth.
                        let ink = crate::hole_table::size_ink(cell, font);
                        crate::output::centred_label_ink(ink, *at, dims)
                    } else {
                        crate::output::centred_label_ink(
                            crate::tables::text_ink(cell, font),
                            *at,
                            dims,
                        )
                    };
                    let index = label(laid, *at, dims, Some(run));
                    if r >= headers
                        && c > 0
                        && let Some(runs) = row_runs.get_mut(k * per + r - headers)
                    {
                        runs[c].extend(index);
                    }
                }
            }
        }
        if let Some(ink) = items_bbox(&dims[table_from..]) {
            sheet.items.push(Drawn {
                datum_triangle: None,
                heads: Vec::new(),
                name: t.id.clone(),
                kind: Kind::Table,
                ink,
                label: None,
                lines: Vec::new(),
                elbow: None,
                interior: false,
            });
        }
        for (row, cells) in t.rows.iter().zip(row_runs) {
            if t.view.is_none() {
                annotations.push(Annotation {
                    satisfies: Vec::new(),
                    approved_claims: Vec::new(),
                    approved_text: Some(
                        row.cells
                            .iter()
                            .skip(1)
                            .filter(|c| !c.is_empty())
                            .cloned()
                            .collect::<Vec<_>>()
                            .join(" "),
                    ),
                    coordinate_origin: None,
                    name: format!("{}/{}", t.id, row.tag),
                    view: Some("v:plan".into()),
                    runs: cells.into_iter().flatten().collect(),
                    claims: row
                        .measurements
                        .iter()
                        .map(|m| Claim {
                            feature: m.feature.clone(),
                            parameter: m.parameter.clone(),
                        })
                        .collect(),
                });
            } else {
                // A coordinate cell proves only its own axis. A matching number elsewhere in
                // this row or another row cannot certify a changed coordinate.
                for (c, runs) in cells.into_iter().enumerate().take(5).skip(1) {
                    if c == 4 {
                        annotations.push(Annotation {
                            satisfies: Vec::new(),
                            approved_claims: Vec::new(),
                            approved_text: Some(row.cells[c].clone()),
                            coordinate_origin: None,
                            name: format!("{}/{}/entry", t.id, row.tag),
                            view: t.view.clone(),
                            runs,
                            claims: vec![Claim {
                                feature: row.feature.clone(),
                                parameter: format!(
                                    "entry.view.{}",
                                    t.view
                                        .as_ref()
                                        .and_then(|v| plan.views.iter().find(|w| &w.id == v))
                                        .map_or_else(String::new, |v| {
                                            v.direction
                                                .iter()
                                                .map(|d| format!("{d:.6}"))
                                                .collect::<Vec<_>>()
                                                .join(",")
                                        })
                                ),
                            }],
                        });
                        continue;
                    }
                    let location = row
                        .measurements
                        .iter()
                        .filter(|m| m.parameter.starts_with("location.length."))
                        .nth(c.saturating_sub(2));
                    let claims = row
                        .measurements
                        .iter()
                        .filter(|m| {
                            if c == 1 {
                                !m.parameter.starts_with("location.length.")
                            } else {
                                location.is_some_and(|l| m.parameter == l.parameter)
                            }
                        })
                        .map(|m| Claim {
                            feature: m.feature.clone(),
                            parameter: m.parameter.clone(),
                        })
                        .collect();
                    annotations.push(Annotation {
                        satisfies: Vec::new(),
                        approved_claims: Vec::new(),
                        approved_text: Some(row.cells[c].clone()),
                        coordinate_origin: if c > 1 { t.origin } else { None },
                        name: format!("{}/{}/{}", t.id, row.tag, ["tag", "size", "x", "y"][c]),
                        view: t.view.clone(),
                        runs,
                        claims,
                    });
                }
            }
        }
    }

    for b in &plan.inspection_balloons {
        let from = dims.len();
        let circle = |r: f64, turn: f64| Curve::Conic {
            centre: b.at,
            radii: [r, r],
            x_axis: [1.0, 0.0],
            y_axis: [0.0, turn],
            t0: 0.0,
            t1: std::f64::consts::TAU,
        };
        dims.push(Item::Face(vec![
            vec![circle(b.radius, 1.0)],
            vec![circle(b.radius - 0.25, -1.0)],
        ]));
        let run = (b.number.as_str(), crate::dims::FONT, crate::dims::TEXT);
        label(
            font.text(&b.number, crate::dims::TEXT),
            b.at,
            dims,
            Some(run),
        );
        for [a, z] in &b.lines {
            dims.push(Item::Edge(Curve::Line(*a, *z)));
        }
        if let Some(ink) = items_bbox(&dims[from..]) {
            sheet.items.push(Drawn {
                datum_triangle: None,
                heads: Vec::new(),
                name: b.id.clone(),
                kind: Kind::Dimension,
                ink,
                label: Some(b.bounds()),
                lines: b.lines.clone(),
                elbow: None,
                interior: false,
            });
        }
    }
    // The balloons: each a ring with its tag, and its leader with an arrowhead at the hole's
    // rim. Furniture to lint's sheet checks, as draftwright marks a balloon a centreline: judged
    // by its leader and its ring's box.
    for b in &plan.balloons {
        let from = dims.len();
        let [cx, cy] = b.at;
        let circle = |r: f64, turn: f64| Curve::Conic {
            centre: b.at,
            radii: [r, r],
            x_axis: [1.0, 0.0],
            y_axis: [0.0, turn],
            t0: 0.0,
            t1: 2.0 * std::f64::consts::PI,
        };
        if b.radius > 0.0 {
            dims.push(Item::Face(vec![
                vec![circle(b.radius, 1.0)],
                vec![circle(b.radius - crate::hole_table::RING, -1.0)],
            ]));
        }
        let run = (b.tag.as_str(), crate::dims::FONT, crate::dims::TEXT);
        label(font.text(&b.tag, crate::dims::TEXT), b.at, dims, Some(run));
        let r = b.radius;
        let mut lines = vec![
            [[cx - r, cy - r], [cx + r, cy - r]],
            [[cx + r, cy - r], [cx + r, cy + r]],
            [[cx + r, cy + r], [cx - r, cy + r]],
            [[cx - r, cy + r], [cx - r, cy - r]],
        ];
        if b.radius == 0.0 {
            lines.clear();
        }
        if let Some([tip, end]) = b.leader {
            dims.push(Item::Edge(Curve::Line(tip, end)));
            if b.interior {
                // GA attachment sites lie inside a proven visible face (ISO 6433).
                dims.push(Item::Face(vec![vec![Curve::Conic {
                    centre: tip,
                    radii: [0.3, 0.3],
                    x_axis: [1., 0.],
                    y_axis: [0., 1.],
                    t0: 0.,
                    t1: 2. * std::f64::consts::PI,
                }]]));
            } else {
                let d = [end[0] - tip[0], end[1] - tip[1]];
                let len = d[0].hypot(d[1]).max(1e-12);
                let u = [d[0] / len, d[1] / len];
                let a = crate::dims::ARROW.min(len);
                let tail = [tip[0] + a * u[0], tip[1] + a * u[1]];
                let h = crate::dims::ARROW / 6.0;
                let b1 = [tail[0] - h * u[1], tail[1] + h * u[0]];
                let b2 = [tail[0] + h * u[1], tail[1] - h * u[0]];
                dims.push(Item::Face(vec![vec![
                    Curve::Line(tip, b1),
                    Curve::Line(b1, b2),
                    Curve::Line(b2, tip),
                ]]));
            }
            lines.insert(0, [tip, end]);
        }
        if let Some(ink) = items_bbox(&dims[from..]) {
            sheet.items.push(Drawn {
                datum_triangle: None,
                heads: Vec::new(),
                name: b.id.clone(),
                kind: if b.id.starts_with("hole_schedule_") {
                    Kind::Dimension
                } else {
                    Kind::Centreline
                },
                ink,
                label: b.label_box,
                lines,
                elbow: None,
                interior: b.id.starts_with("hole_schedule_"),
            });
        }
    }
    // The scale, outside the block (ISO 7200 §4), above its right end.
    let scale = format!("SCALE {}", plan.sheet.scale);
    let at = title_block::scale_note(block, &scale);
    let run = (scale.as_str(), title_block::FONT, title_block::TEXT);
    let from = dims.len();
    label(
        title_block::lay(&scale, title_block::TEXT),
        at,
        dims,
        Some(run),
    );
    sheet
        .items
        .extend(labelled("scale_note", Kind::ScaleNote, &dims[from..]));
    for caption in plan
        .captions
        .iter()
        .filter(|c| c.sheet == plan.sheet.number)
    {
        let from = dims.len();
        let run = (caption.text.as_str(), crate::dims::FONT, caption.height);
        label(
            font.text(&caption.text, caption.height),
            caption.at,
            dims,
            Some(run),
        );
        sheet
            .items
            .extend(labelled(&caption.id, Kind::Caption, &dims[from..]));
    }
    // Each drawing-wide PMI requirement by the runs that state it, for lint and the PMI checks.
    for (name, runs) in stated {
        if let Some(annotation) = annotations.iter_mut().find(|a| a.name == name) {
            // The block holds the normative words. The flag's numbered glyph is
            // still drawn and identified by its sheet item; do not prepend its
            // number to the note's semantic text or the wording override.
            annotation.runs = runs;
        } else {
            annotations.push(Annotation {
                approved_claims: Vec::new(),
                approved_text: None,
                coordinate_origin: None,
                satisfies: Vec::new(),
                name,
                view: None,
                runs,
                claims: Vec::new(),
            });
        }
    }
    // Rendered feature notes retain their explicit authority, separately from measurements.
    for a in &mut annotations {
        if let Some(g) = plan
            .gdt
            .iter()
            .find(|g| g.id == a.name && g.placed.is_some())
        {
            a.satisfies = crate::spec_notes::satisfaction(&plan.spec, &g.source);
        }
    }
    // Authored plate omissions are consumer provenance, with no ink or claimed value.
    annotations.extend(
        plan.omitted
            .iter()
            .filter(|o| {
                (o.id.ends_with("/thickness.length") || o.id.starts_with("d:f:pocket_pattern:"))
                    && matches!(
                        o.state,
                        crate::items::State::OmittedByUser | crate::items::State::NotAuthored
                    )
            })
            .filter_map(|o| {
                let (feature, parameter) = o.id.strip_prefix("d:")?.split_once('/')?;
                Some(Annotation {
                    approved_claims: Vec::new(),
                    approved_text: None,
                    coordinate_origin: None,
                    name: format!("suppressed:{}", o.id),
                    view: None,
                    runs: Vec::new(),
                    claims: vec![crate::output::Claim {
                        feature: feature.into(),
                        parameter: parameter.into(),
                    }],
                    satisfies: Vec::new(),
                })
            }),
    );
    if let Some(symbol) = &plan.sheet.projection_symbol {
        let ink: Vec<Item> = symbol.curves.iter().cloned().map(Item::Edge).collect();
        if let Some(bbox) = items_bbox(&ink) {
            sheet.items.push(Drawn {
                datum_triangle: None,
                heads: Vec::new(),
                name: "projection_symbol".into(),
                kind: Kind::ProjectionSymbol,
                ink: bbox,
                label: None,
                lines: symbol
                    .curves
                    .iter()
                    .filter_map(|c| match c {
                        Curve::Line(a, b) => Some([*a, *b]),
                        _ => None,
                    })
                    .collect(),
                elbow: None,
                interior: false,
            });
        }
        let mut style = layer_styles()[0].1.clone();
        style.width = title_block::PROJECTION_LINE;
        style.weight = 15;
        layers.push(Layer {
            name: "projection_symbol".into(),
            style,
            items: ink,
        });
    }
    if plan.sheet.zones.is_some() {
        // ISO 5457 centring marks: heavy lines at each side's midpoint, from the trimmed edge
        // to 5 mm inside the frame. A mark stops at the frame where it would meet the title
        // block, a table or any other drawn ink.
        let clear = |b: [f64; 4]| {
            let pad = 0.5 * CENTRING_MARK + 1.0;
            !sheet
                .items
                .iter()
                .flat_map(|d| std::iter::once(d.ink).chain(d.label))
                .chain(sheet.views.iter().map(|v| v.bbox))
                .any(|o| {
                    o[0] < b[2] + pad && b[0] < o[2] + pad && o[1] < b[3] + pad && b[1] < o[3] + pad
                })
        };
        let reach = FRAME + CENTRING_INSET;
        let (cx, cy) = (0.5 * w, 0.5 * h);
        let marks = [
            ([cx, 0.], [cx, FRAME], [cx, reach]),
            ([cx, h], [cx, h - FRAME], [cx, h - reach]),
            ([0., cy], [FRAME, cy], [reach, cy]),
            ([w, cy], [w - FRAME, cy], [w - reach, cy]),
        ];
        let items = marks
            .into_iter()
            .map(|(edge, frame, inside)| {
                let b = [
                    frame[0].min(inside[0]),
                    frame[1].min(inside[1]),
                    frame[0].max(inside[0]),
                    frame[1].max(inside[1]),
                ];
                Item::Edge(Curve::Line(edge, if clear(b) { inside } else { frame }))
            })
            .collect();
        let mut style = layer_styles()[0].1.clone();
        style.width = CENTRING_MARK;
        style.weight = 70;
        layers.push(Layer {
            name: "centring_marks".into(),
            style,
            items,
        });
    }
    sheet.inspection_enabled = crate::characteristics::enabled(plan);
    sheet.characteristics = plan
        .characteristics
        .iter()
        .filter(|c| c.sheet.unwrap_or(1) == plan.sheet.number)
        .cloned()
        .collect();
    crate::label_text::capture(plan, &mut sheet);
    if !plan.spec.precision.is_empty() {
        for d in &plan.dimensions {
            let role = d.id.rsplit_once('/').map_or("", |(_, p)| p);
            if let Some(p) = plan.spec.precision.get(&d.id).or_else(|| {
                d.measurements
                    .iter()
                    .filter(|m| m.parameter == role)
                    .find_map(|m| {
                        plan.spec
                            .precision
                            .get(&format!("d:{}/{}", m.feature, m.parameter))
                    })
            }) {
                sheet.precision.insert(d.id.clone(), *p);
            }
        }
    }
    // The cell annotations were bound above from the compiler's row measurements. Retain
    // those identities separately from the live claims, just as approved text is retained
    // separately from rendered runs; downstream mutations must preserve both contracts.
    for annotation in &mut annotations {
        if annotation.approved_text.is_some() {
            annotation.approved_claims = annotation.claims.clone();
        }
    }
    if !plan.spec.origin.is_empty() {
        let origin = crate::dims::pmi_policy::origin_point(analysis, &plan.spec);
        for a in &mut annotations {
            if plan
                .dimensions
                .iter()
                .any(|d| d.id == a.name && d.kind == "linear")
            {
                a.coordinate_origin = Some(origin);
            }
        }
    }
    Ok(Drawing {
        page: plan.sheet.page,
        layers,
        // The attribution link covers the "drawn by" cell, as draftwright's does.
        link: plan
            .sheet
            .title_cells
            .iter()
            .find(|c| c.field == "designed_by")
            .map(|c| c.cell),
        text,
        annotations,
        sheet,
        problems: crate::output::undrawn(&undrawn)
            .into_iter()
            .chain(if plan.spec.views.sections.is_empty() {
                Vec::new()
            } else {
                crate::sections::attachment_problems(analysis, plan)
            })
            .chain(rejected_sections.into_iter().map(|(id, reason)| Problem {
                id: format!("p:section_dropped:{id}"),
                code: "section_dropped".into(),
                severity: "warning".into(),
                message: format!("Section {id} is refused: {reason}."),
                subjects: vec![id],
                ..Default::default()
            }))
            .collect(),
        spec: Some(
            if !crate::dims::pmi_policy::carries(analysis) && plan.spec.pmi != PmiMode::First {
                // An inactive PMI policy must not change a part-without-PMI drawing's bytes.
                // The live session keeps the chosen policy in its Spec for a later PMI replacement.
                let mut embedded = plan.spec.clone();
                embedded.pmi = PmiMode::First;
                embeddable(&embedded)
            } else {
                embeddable(&plan.spec)
            },
        ),
    })
}

/// The caption draftwright prints under an isometric drawn off the sheet's scale, and the name
/// of the drawn item (draftwright's `note_iso_nts`).
pub const NTS_CAPTION: &str = "ISO VIEW (NTS)";
pub use crate::output::NTS_CAPTION_ID;

/// Where the NTS caption of *size* is centred, for an isometric whose drawn box is *iso* and
/// whose projection centre is at x = *centre_x* (draftwright's `place_iso_nts_note`): two font
/// heights below the iso; failing that, further below; beside it, left then right; and only
/// last above it. The first whose box, with the text padding round it, clears every obstacle
/// and stays inside the drawable area wins. Retry without extra text clearance, then slide
/// candidates vertically clear of obstacle edges before retaining required content at the
/// natural position; structural lint reports any unavoidable clash.
pub fn nts_caption_at(
    page: [f64; 2],
    iso: [f64; 4],
    centre_x: f64,
    size: [f64; 2],
    obstacles: &[[f64; 4]],
) -> [f64; 2] {
    let font = crate::section::FONT_SIZE;
    let [w, h] = size;
    let natural = [centre_x, (iso[1] - 2.0 * font).max(margin() + font)];
    let candidates = [
        natural,
        [centre_x, (iso[1] - 3.0 * font - h).max(margin() + font)],
        [iso[0] - font - w / 2.0, natural[1]],
        [iso[2] + font + w / 2.0, natural[1]],
        [
            centre_x,
            (iso[3] + 2.0 * font).min(page[1] - margin() - font),
        ],
    ];
    let clear = |c: [f64; 2], pad: f64| {
        let b = [
            c[0] - w / 2.0,
            c[1] - h / 2.0,
            c[0] + w / 2.0,
            c[1] + h / 2.0,
        ];
        if b[0] < margin()
            || b[1] < margin()
            || b[2] > page[0] - margin()
            || b[3] > page[1] - margin()
        {
            return false;
        }
        let k = [b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad];
        !obstacles
            .iter()
            .any(|o| k[0] < o[2] && k[2] > o[0] && k[1] < o[3] && k[3] > o[1])
    };
    // Dimension strokes already have their own obstacle padding. Do not let stacking
    // that pad with text clearance force the caption back onto real ink.
    [crate::tables::PAD, 0.0]
        .into_iter()
        .find_map(|pad| candidates.into_iter().find(|c| clear(*c, pad)))
        .or_else(|| {
            // A short extension line can still cross a sideways candidate's baseline.
            // Slide along the same five columns to the nearest obstacle edge before
            // retaining a clash. Existing clear candidates always take precedence.
            candidates.into_iter().find_map(|c| {
                let mut ys: Vec<_> = obstacles
                    .iter()
                    .flat_map(|o| [o[1] - h / 2.0 - 1e-6, o[3] + h / 2.0 + 1e-6])
                    .collect();
                ys.sort_by(|a, b| {
                    (a - c[1])
                        .abs()
                        .total_cmp(&(b - c[1]).abs())
                        .then(a.total_cmp(b))
                });
                ys.into_iter().map(|y| [c[0], y]).find(|c| clear(*c, 0.0))
            })
        })
        .unwrap_or(natural)
}

/// A section view's furniture: ink (the hatch's first lines, then the cutting plane's), and
/// texts with where each is centred.
struct Furniture {
    ink: Vec<Item>,
    /// How many of the ink's first items are the hatch.
    hatch: usize,
    /// The cutting plane's chain line, end to end, when it is drawn.
    chain: Option<[[f64; 2]; 2]>,
    text: Vec<(String, [f64; 2])>,
}

/// Prove a new section region from boundary samples on both sides of the
/// plane. Coplanar exposed faces are material on only one side. This test is
/// independent of hatch spacing, so even a small valid cut remains a section.
fn cut_crosses_material(part: &Part, plane: &Plane, loops: &[Vec<[f64; 3]>]) -> bool {
    let classifier = Classifier::new(part);
    let n = plane.normal;
    loops.iter().flat_map(|l| l.windows(2)).any(|edge| {
        let [a, b] = [edge[0], edge[1]];
        let direction = [0, 1, 2].map(|k| b[k] - a[k]);
        let length = direction.iter().map(|v| v * v).sum::<f64>().sqrt();
        if length <= 1e-9 {
            return false;
        }
        let inward = [
            n[1] * direction[2] - n[2] * direction[1],
            n[2] * direction[0] - n[0] * direction[2],
            n[0] * direction[1] - n[1] * direction[0],
        ];
        [-1.0, 1.0].into_iter().any(|side| {
            let point = [0, 1, 2].map(|k| (a[k] + b[k]) * 0.5 + side * inward[k] / length * 1e-4);
            [-1.0, 1.0].into_iter().all(|normal_side| {
                classifier.classify([0, 1, 2].map(|k| point[k] + normal_side * n[k] * 1e-5))
                    == State::In
            })
        })
    })
}

/// Section view *v*'s furniture: its cut face hatched, its caption, and the cutting-plane line
/// with its arrows and letters on the view that marks it (draftwright's `_add_section_view`).
fn section_furniture(
    part: &Part,
    analysis: &Analysis,
    plan: &Plan,
    v: &PlannedView,
    (plane, cut): (&Plane, &Cut),
    projections: &mut Projections,
) -> Result<Furniture, Unresolved> {
    let mut out = Furniture {
        ink: section_hatch(part, analysis, plan, v, (plane, cut), projections)?,
        hatch: 0,
        chain: None,
        text: Vec::new(),
    };
    // Invariant: Planned cameras come from the fixed views or checked section/detail cameras, with nonparallel axes.
    let view = &View::new(v.direction, v.up).expect("a planned view");
    out.hatch = out.ink.len();
    let half_h = 0.5 * extent(analysis, view).0[1] * v.scale;
    let l = &cut.label;
    out.text.push((
        format!("SECTION {l}\u{2013}{l}"),
        [v.at[0], v.at[1] - half_h - 7.0],
    ));
    cutting_furniture(plan, cut, &mut out);
    Ok(out)
}

/// The 45° hatch of the material plane *cut* leaves in view *v* (page mm), each line proven
/// inside the material on both sides of the plane: a full section's, or a half section's before
/// it is clipped to its cut half.
fn section_hatch(
    part: &Part,
    analysis: &Analysis,
    plan: &Plan,
    v: &PlannedView,
    (plane, cut): (&Plane, &Cut),
    projections: &mut Projections,
) -> Result<Vec<Item>, Unresolved> {
    use crate::section::{HATCH_SPACING, hatch};
    let mut ink = Vec::new();
    // Invariant: Planned cameras come from the fixed views or checked section/detail cameras, with nonparallel axes.
    let view = &View::new(v.direction, v.up).expect("a planned view");
    let (s, centre) = (v.scale, extent(analysis, view).1);
    // Hatch geometry depends on the part, plane and projection transform, not
    // annotation text or title fields. Binding changes discard this cache.
    let carrier = crate::modelled_threads::carrier(
        part,
        analysis,
        &plan.spec,
        v.line_style.thread_depiction != Some(false),
    );
    let part = carrier.as_ref().unwrap_or(part);
    let extra = crate::modelled_threads::cache_key(analysis, &plan.spec, v);
    let mut hatch_key = bits(&[
        &v.direction,
        &v.up,
        &cut.origin,
        &cut.normal,
        &[s],
        &centre,
        &v.at,
    ]);
    hatch_key.extend(&extra);
    if let Some(cached) = projections.hatches.get(&hatch_key) {
        ink = cached.clone();
    } else {
        let page: Vec<Vec<[f64; 2]>> = projections
            .cut(part, cut, plane, &extra)?
            .iter()
            .map(|l| {
                l.iter()
                    .map(|p| {
                        let q = view.map(*p);
                        [
                            v.at[0] + s * (q[0] - centre[0]),
                            v.at[1] + s * (q[1] - centre[1]),
                        ]
                    })
                    .collect()
            })
            .collect();
        // Only material crossing the plane is hatched. Coplanar faces and stray cut
        // pieces must not flip parity or conceal a boundary inside a candidate span.
        let faces: Vec<usize> = part
            .solids
            .iter()
            .flat_map(|solid| solid.faces.iter().copied())
            .collect();
        let classifier = if faces.is_empty() {
            Classifier::new(part)
        } else {
            Classifier::for_faces(part, faces)
        };
        let n = plane.normal;
        let to_model = |q: [f64; 2], side: f64| {
            let (u, w) = (
                centre[0] + (q[0] - v.at[0]) / s,
                centre[1] + (q[1] - v.at[1]) / s,
            );
            let base = [0, 1, 2].map(|k| view.x[k] * u + view.y[k] * w);
            let along = (0..3)
                .map(|k| (cut.origin[k] - base[k]) * n[k])
                .sum::<f64>()
                / (0..3).map(|k| view.toward[k] * n[k]).sum::<f64>();
            [0, 1, 2].map(|k| base[k] + view.toward[k] * along + side * n[k] * 1e-2)
        };
        for [a, b] in hatch(&page, HATCH_SPACING, Some(&|_| true)) {
            let point = |t: f64| [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])];
            let (p, q) = (to_model(a, 1.0), to_model(b, 1.0));
            let length = (0..3).map(|k| (q[k] - p[k]).powi(2)).sum::<f64>().sqrt();
            let direction = [0, 1, 2].map(|k| (q[k] - p[k]) / length);
            let mut stops = vec![0.0, 1.0];
            for side in [-1.0, 1.0] {
                let hits = classifier
                    .rays()
                    .hits(to_model(a, side), direction, length)
                    .ok_or_else(|| Unresolved {
                        faces: part
                            .solids
                            .iter()
                            .flat_map(|solid| solid.faces.iter().copied())
                            .collect(),
                        edges: Vec::new(),
                    })?;
                stops.extend(hits.iter().map(|hit| (hit.t / length).clamp(0.0, 1.0)));
            }
            stops.sort_by(f64::total_cmp);
            stops.dedup_by(|a, b| (*a - *b).abs() < 1e-9);
            for pair in stops.windows(2) {
                let middle = point((pair[0] + pair[1]) / 2.0);
                if (pair[1] - pair[0]) * length * s > 0.2
                    && [-1.0, 1.0]
                        .into_iter()
                        .all(|side| classifier.classify(to_model(middle, side)) == State::In)
                {
                    ink.push(Item::Edge(Curve::Line(point(pair[0]), point(pair[1]))));
                }
            }
        }
        projections.hatches.insert(hatch_key, ink.clone());
    }
    Ok(ink)
}

fn cutting_furniture(plan: &Plan, cut: &Cut, out: &mut Furniture) {
    use crate::section::cutting_plane;
    let l = &cut.label;
    if let Some(w) = plan
        .views
        .iter()
        .find(|w| w.id == cut.marked_in)
        .and_then(|v| v.line_work.iter().find(|w| w.kind == "cutting_plane"))
        && let Curve::Line(a, b) = w.curves[0]
    {
        let (ink, letters) = cutting_plane(a[0], b[0], a[1]);
        out.ink.extend(ink);
        out.chain = Some([a, b]);
        out.text.extend(letters.map(|at| (l.clone(), at)));
    }
}

/// Where the cutting-plane line of *cut* runs on the view *m* that marks it (page mm): from
/// `x0` to `x1` at height `y`, across the part's extent along the view's x and past it each
/// side. The renderer draws it there; the planner keeps interior labels off it.
pub(crate) fn cutting_line(
    analysis: &Analysis,
    m: &PlannedView,
    mv: &View,
    cut: &Cut,
) -> (f64, f64, f64) {
    let centre = extent(analysis, mv).1;
    let to_page = |p: [f64; 3]| {
        let q = mv.map(p);
        let q = m.broken.as_ref().map_or(q, |b| b.map(q));
        [
            m.at[0] + m.scale * (q[0] - centre[0]),
            m.at[1] + m.scale * (q[1] - centre[1]),
        ]
    };
    let [lo, hi] = analysis.envelope;
    let (a, b) = (
        to_page([lo[0], cut.origin[1], lo[2]]),
        to_page([hi[0], cut.origin[1], hi[2]]),
    );
    let overrun = crate::section::OVERRUN;
    (
        a[0].min(b[0]) - overrun,
        a[0].max(b[0]) + overrun,
        to_page(cut.origin)[1],
    )
}

/// A projected curve scaled by *s* about *centre* and moved to *at*.
fn placed(e: &Exact, s: f64, centre: [f64; 2], at: [f64; 2]) -> Exact {
    let m = |q: [f64; 2]| {
        [
            at[0] + s * (q[0] - centre[0]),
            at[1] + s * (q[1] - centre[1]),
        ]
    };
    match e.clone() {
        Exact::Line(a, b) => Exact::Line(m(a), m(b)),
        Exact::Conic {
            centre: c,
            radii,
            x_axis,
            y_axis,
            t0,
            t1,
        } => Exact::Conic {
            centre: m(c),
            radii: [radii[0] * s, radii[1] * s],
            x_axis,
            y_axis,
            t0,
            t1,
        },
        Exact::Nurbs {
            degree,
            poles,
            weights,
            knots,
            t0,
            t1,
        } => Exact::Nurbs {
            degree,
            poles: poles.into_iter().map(m).collect(),
            weights,
            knots,
            t0,
            t1,
        },
    }
}

/// *spec* as the JSON a drawing carries: `<`, `>` and `&` written as `\u` escapes, so no markup
/// (an end tag, a CDATA section's end, an entity) can be read inside it.
fn embeddable(spec: &Spec) -> String {
    serde_json::to_string(spec)
        // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
        .expect("a spec serialises")
        .replace('<', "\\u003c")
        .replace('>', "\\u003e")
        .replace('&', "\\u0026")
}

/// The spec embedded in a rendered SVG, DXF or PDF, so a drawing can be resumed from the file
/// alone: `None` when the drawing carries none. The format is told by the file's signature; a
/// file that is none of the three, or a spec that does not read, is an error saying why.
pub fn embedded_spec(file: &[u8]) -> Result<Option<Spec>, String> {
    let text = String::from_utf8_lossy(file);
    let start = text.trim_start_matches('\u{feff}').trim_start();
    let json = if crate::png::is_png(file) {
        crate::png::spec(file)?
    } else if file.starts_with(b"%PDF-") {
        pdf_spec(&text)?
    } else if start.starts_with('<') {
        svg_spec(&text)?
    } else if start
        .lines()
        .next()
        .is_some_and(|l| !l.trim().is_empty() && l.trim().parse::<i32>().is_ok())
    {
        // A DXF opens with a group code.
        dxf_spec(&text)
    } else {
        return Err("it is not an SVG, DXF, PDF or PNG drawing".into());
    };
    json.map(|j| {
        serde_json::from_str(&j)
            .map_err(|e| format!("it carries a draftwright spec that is not valid: {e}"))
    })
    .transpose()
}

/// The JSON of a DXF's spec: the 999 comments after the marker, joined. The marker counts only
/// as a whole comment line.
fn dxf_spec(text: &str) -> Option<String> {
    let i = ["999\r\n", "999\n"].iter().find_map(|code| {
        let i = text.find(&format!("{code}{}", crate::output::DXF_SPEC))?;
        let end = i + code.len() + crate::output::DXF_SPEC.len();
        matches!(text.as_bytes().get(end), Some(b'\r' | b'\n')).then_some(i + code.len())
    })?;
    let mut lines = text[i..]
        .split('\n')
        .map(|l| l.strip_suffix('\r').unwrap_or(l));
    lines.next();
    let mut json = String::new();
    while let (Some(code), Some(piece)) = (lines.next(), lines.next())
        && code.trim() == "999"
    {
        json += piece;
    }
    Some(json)
}

/// The JSON of an SVG's `draftwright:spec` element: its character data, CDATA sections taken as
/// they are and the rest with XML's predefined entities undone (a spec a re-saving editor wrote
/// without CDATA still reads).
fn svg_spec(text: &str) -> Result<Option<String>, String> {
    let Some(i) = text.find("<draftwright:spec") else {
        return Ok(None);
    };
    let broken = || "its draftwright:spec element is not closed".to_string();
    let mut rest = &text[i + text[i..].find('>').ok_or_else(broken)? + 1..];
    let mut json = String::new();
    loop {
        if let Some(r) = rest.strip_prefix("<![CDATA[") {
            let end = r.find("]]>").ok_or_else(broken)?;
            json += &r[..end];
            rest = &r[end + "]]>".len()..];
        } else if rest.starts_with("</draftwright:spec>") {
            return Ok(Some(json));
        } else if rest.starts_with('<') || rest.is_empty() {
            return Err(broken());
        } else {
            let end = rest.find('<').unwrap_or(rest.len());
            json += &rest[..end]
                .replace("&lt;", "<")
                .replace("&gt;", ">")
                .replace("&quot;", "\"")
                .replace("&apos;", "'")
                .replace("&amp;", "&");
            rest = &rest[end..];
        }
    }
}

/// The JSON of a PDF's `/DraftwrightSpec` document information string.
fn pdf_spec(text: &str) -> Result<Option<String>, String> {
    let broken = || "its /DraftwrightSpec string does not read".to_string();
    if let Some(i) = text.find("/DraftwrightSpec (") {
        return crate::output::pdf_unescape(&text[i + "/DraftwrightSpec (".len()..])
            .map(Some)
            .ok_or_else(broken);
    }
    // pdf-writer writes a string that is not all ASCII in hex.
    let Some(i) = text.find("/DraftwrightSpec <") else {
        return Ok(None);
    };
    let i = i + "/DraftwrightSpec <".len();
    let digits: Vec<u8> = text[i..i + text[i..].find('>').ok_or_else(broken)?]
        .bytes()
        .filter(|b| !b.is_ascii_whitespace())
        .collect();
    let bytes = digits
        .chunks(2)
        .map(|d| u8::from_str_radix(std::str::from_utf8(d).ok()?, 16).ok())
        .collect::<Option<Vec<u8>>>()
        .ok_or_else(broken)?;
    String::from_utf8(bytes).map(Some).map_err(|_| broken())
}

mod projection_store;

pub mod dimension_geometry;
pub mod projected_ink;

#[cfg(test)]
#[path = "model_recovery_tests.rs"]
mod recovery_tests;

#[cfg(test)]
mod squared_tests {
    use super::*;

    /// The size of *part*'s box along the file's axes.
    fn size(part: &Part) -> [f64; 3] {
        let b = crate::quiddity_evidence::drawn_bounds(
            part,
            &crate::quiddity_evidence::drawn_faces(part),
        );
        std::array::from_fn(|k| b.max[k] - b.min[k])
    }

    #[test]
    fn only_a_part_within_two_degrees_of_the_axes_is_squared() {
        let plate =
            haecceity::read_step(include_bytes!("../tests/fixtures/parts/plate.step")).unwrap();
        assert!(Squared::of(&plate).is_none());
        // Compounded, the largest axis turns about 1.12 times as far: 1.6° is within 2°, 2.5° not.
        for degrees in [0.3_f64, 1.0, 1.6, 2.5, 30.0] {
            let (s, c) = degrees.to_radians().sin_cos();
            // About z, then a little about x, so no file axis is left a part axis.
            let (sx, cx) = (degrees / 2.0).to_radians().sin_cos();
            let turned = plate
                .transformed(&[[c, -s, 0.0, 3.0], [s, c, 0.0, -2.0], [0.0, 0.0, 1.0, 1.0]])
                .and_then(|p| {
                    p.transformed(&[
                        [1.0, 0.0, 0.0, 0.0],
                        [0.0, cx, -sx, 0.0],
                        [0.0, sx, cx, 0.0],
                    ])
                })
                .unwrap();
            match Squared::of(&turned) {
                Some(squared) => {
                    assert!(degrees < 2.0, "{degrees}° squared");
                    // The largest axis's angle: the z and x turns compound on one axis.
                    assert!(squared.angle >= degrees && squared.angle < degrees * 1.2);
                    let back = turned.transformed(&squared.placement).unwrap();
                    for (got, want) in size(&back).iter().zip(size(&plate)) {
                        assert!((got - want).abs() < 1e-6, "{degrees}°: {got} {want}");
                    }
                }
                None => assert!(degrees > 2.0, "{degrees}° not squared"),
            }
        }
    }
}

#[cfg(all(test, not(target_arch = "wasm32")))]
mod ga_crop_cache_tests {
    use super::*;

    #[test]
    fn a_culled_detail_preserves_geometry_findings_and_its_parent_camera() {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/assembly/pack-small.step.gz");
        let bytes = read_step_file(&path).unwrap();
        let (part, a, _) = analyse_pack_assembly("crop-cache", &bytes, None).unwrap();
        let mut spec = Spec::default();
        spec.sheet.hidden = Some(false);
        let mut plan = crate::api::plan_analysed(&a, &spec).unwrap();
        let front = plan
            .views
            .iter()
            .find(|v| v.id == "v:front")
            .unwrap()
            .clone();
        let camera = View::new(front.direction, front.up).unwrap();
        let mut pieces = project(&part, &camera).unwrap();
        // The native part's straight edges, represented by equivalent rational
        // splines, exercise the pre-conversion path without changing geometry.
        for piece in &mut pieces {
            if let Some(Exact::Line(p, q)) = &piece.exact {
                piece.exact = Some(Exact::Nurbs {
                    degree: 1,
                    poles: vec![*p, *q],
                    weights: vec![1., 1.],
                    knots: vec![0., 0., 1., 1.],
                    t0: 0.,
                    t1: 1.,
                });
            }
        }
        // Even far outside the crop, malformed evidence must still produce
        // the same edges_not_drawn finding rather than disappearing in culling.
        pieces.push(Projected {
            class: hlr::Class::Sharp,
            visible: true,
            points: vec![[1000., 1000.], [1001., 1001.]],
            exact: Some(Exact::Nurbs {
                degree: 2,
                poles: vec![[1000., 1000.]],
                weights: vec![1.],
                knots: vec![0., 1.],
                t0: 0.,
                t1: 1.,
            }),
        });
        let mut bounds = a.envelope;
        bounds[1][0] = bounds[0][0] + (bounds[1][0] - bounds[0][0]) * 0.25;
        bounds[1][2] = bounds[0][2] + (bounds[1][2] - bounds[0][2]) * 0.5;
        let mut crop = front.clone();
        crop.id = "v:detail_b".into();
        crop.scale = 2.;
        crop.detail = Some(crate::detail::Detail {
            label: "B".into(),
            axis: 1,
            source_view: front.id.clone(),
            source_cut: None,
            kind: "crop-cache".into(),
            bounds,
            marked_bounds: bounds,
            dimensions: Vec::new(),
            scale: 2.,
            authored: true,
            partial_profile: true,
        });
        plan.views = vec![crop.clone()];
        let mut baseline = Projections::default();
        baseline.assembly_view(&a, &plan.spec, &crop, pieces.clone());
        let expected = render_geometry(&part, &a, &plan, &mut baseline).unwrap();
        let mut culled = Projections::default();
        culled.assembly_view(&a, &plan.spec, &crop, pieces);
        culled.assembly_crop(&part, &a, &plan, &crop).unwrap();
        assert_eq!(
            culled.stats().bounds,
            1,
            "distant native splines should be culled and the detail primed"
        );
        let actual = render_geometry(&part, &a, &plan, &mut culled).unwrap();
        let equal = |a: &ViewGeometry, b: &ViewGeometry| {
            assert_eq!(a.visible, b.visible);
            assert_eq!(a.hidden, b.hidden);
            assert_eq!(a.breaks, b.breaks);
            assert_eq!(a.bbox, b.bbox);
            assert_eq!(a.undrawn, b.undrawn);
        };
        equal(&expected.views[0].1, &actual.views[0].1);
        assert!(actual.views[0].1.undrawn > 0);
        plan.views = vec![front];
        let expected_parent = render_geometry(&part, &a, &plan, &mut baseline).unwrap();
        let actual_parent = render_geometry(&part, &a, &plan, &mut culled).unwrap();
        equal(&expected_parent.views[0].1, &actual_parent.views[0].1);
    }
}
