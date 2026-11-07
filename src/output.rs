//! A drawing written out as draftwright writes it — SVG (build123d's `ExportSVG`), DXF (its
//! `ExportDXF`, through ezdxf) and PDF (the SVG through svglib and reportlab, with the labels'
//! text laid invisibly over their glyphs so the PDF can be searched). The port lays the same
//! invisible text over the SVG's glyphs too, and keeps the callout symbols (`⌀ ↧ ⌴ ⌵`) in both,
//! where draftwright's PDF text has `ø` and spaces.
//!
//! The drawing is layers of curves on a page, in millimetres with y up: each layer's lines are
//! drawn in its style, and its faces (glyphs, arrowheads) filled where the layer fills.

use std::collections::{BTreeMap, BTreeSet};
use std::f64::consts::{FRAC_PI_2, PI};
use std::fmt::Write as _;

use crate::model::Problem;
use serde::{Deserialize, Serialize};

pub type P = [f64; 2];

/// Centre baseline label ink on a table/note anchor. Strokes and glyphs share the
/// same transform; all exporters consume these visible primitives.
pub(crate) fn centred_label_ink(
    ink: crate::dims::LabelInk,
    at: P,
    items: &mut Vec<Item>,
) -> crate::text::Laid {
    use crate::text::Segment;
    let centre = [0, 1].map(|k| 0.5 * (ink.min[k] + ink.max[k]));
    let local = |p: P| [p[0] - centre[0], p[1] - centre[1]];
    for [a, b] in ink.strokes {
        let a = local(a);
        let b = local(b);
        items.push(Item::Edge(Curve::Line(
            [at[0] + a[0], at[1] + a[1]],
            [at[0] + b[0], at[1] + b[1]],
        )));
    }
    crate::text::Laid {
        contours: ink
            .glyphs
            .into_iter()
            .flatten()
            .map(|c| {
                c.into_iter()
                    .map(|s| match s {
                        Segment::Line(a, b) => Segment::Line(local(a), local(b)),
                        Segment::Quad(a, b, c) => Segment::Quad(local(a), local(b), local(c)),
                        Segment::Cubic(a, b, c, d) => {
                            Segment::Cubic(local(a), local(b), local(c), local(d))
                        }
                    })
                    .collect()
            })
            .collect(),
        min: local(ink.min),
        max: local(ink.max),
    }
}

/// Stable rendered item name for the isometric's required scale disclaimer.
pub const NTS_CAPTION_ID: &str = "note_iso_nts";

/// A curve as the exporters draw it.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub enum Curve {
    Line(P, P),
    /// An arc of an ellipse (or circle): `centre + radii[0] cos t x_axis + radii[1] sin t
    /// y_axis` for t running from `t0` to `t1`.
    Conic {
        centre: P,
        radii: [f64; 2],
        x_axis: P,
        y_axis: P,
        t0: f64,
        t1: f64,
    },
    /// Bézier pieces of 2 to 4 poles, end to end; at least one.
    Beziers(Vec<Vec<P>>),
    /// Rational Bézier pieces, end to end, by their homogeneous poles `[x w, y w, w]`: a NURBS
    /// curve cut at its knots; at least one.
    Rational(Vec<Vec<[f64; 3]>>),
}

impl Curve {
    fn conic_at(centre: P, radii: [f64; 2], x: P, y: P, t: f64) -> P {
        let (c, s) = (t.cos() * radii[0], t.sin() * radii[1]);
        [
            centre[0] + c * x[0] + s * y[0],
            centre[1] + c * x[1] + s * y[1],
        ]
    }

    pub fn start(&self) -> P {
        match self {
            Curve::Line(a, _) => *a,
            Curve::Conic {
                centre,
                radii,
                x_axis,
                y_axis,
                t0,
                ..
            } => Curve::conic_at(*centre, *radii, *x_axis, *y_axis, *t0),
            Curve::Beziers(b) => b[0][0],
            Curve::Rational(r) => point(r[0][0]),
        }
    }

    pub fn end(&self) -> P {
        match self {
            Curve::Line(_, b) => *b,
            Curve::Conic {
                centre,
                radii,
                x_axis,
                y_axis,
                t1,
                ..
            } => Curve::conic_at(*centre, *radii, *x_axis, *y_axis, *t1),
            // Invariant: projected drops empty curves; curve constructors emit nonempty pieces of at least two poles.
            Curve::Beziers(b) => *b[b.len() - 1].last().unwrap(),
            // Invariant: projected drops empty curves; curve constructors emit nonempty pieces of at least two poles.
            Curve::Rational(r) => point(*r[r.len() - 1].last().unwrap()),
        }
    }

    /// An exact parameter interval of the curve, with 0 ≤ lo < hi ≤ 1.
    pub fn trim(&self, lo: f64, hi: f64) -> Self {
        fn split<const N: usize>(p: &[[f64; N]], t: f64) -> (Vec<[f64; N]>, Vec<[f64; N]>) {
            let mut q = p.to_vec();
            // Invariant: trim splits only nonempty Bezier pieces produced by curve construction.
            let (mut left, mut right) = (vec![q[0]], vec![*q.last().unwrap()]);
            while q.len() > 1 {
                q = q
                    .windows(2)
                    .map(|w| std::array::from_fn(|k| (1.0 - t) * w[0][k] + t * w[1][k]))
                    .collect();
                left.push(q[0]);
                // Invariant: The loop runs only while q has more than one pole; windows(2) leaves at least one.
                right.push(*q.last().unwrap());
            }
            right.reverse();
            (left, right)
        }
        fn pieces<const N: usize>(p: &[Vec<[f64; N]>], lo: f64, hi: f64) -> Vec<Vec<[f64; N]>> {
            let n = p.len() as f64;
            p.iter()
                .enumerate()
                .filter_map(|(i, poles)| {
                    let a = (lo * n - i as f64).max(0.0);
                    let b = (hi * n - i as f64).min(1.0);
                    if b <= a {
                        return None;
                    }
                    let left = split(poles, b).0;
                    Some(split(&left, a / b).1)
                })
                .collect()
        }
        match self {
            Self::Line(..) => Self::Line(self.at(lo), self.at(hi)),
            Self::Conic {
                centre,
                radii,
                x_axis,
                y_axis,
                t0,
                t1,
            } => Self::Conic {
                centre: *centre,
                radii: *radii,
                x_axis: *x_axis,
                y_axis: *y_axis,
                t0: t0 + lo * (t1 - t0),
                t1: t0 + hi * (t1 - t0),
            },
            Self::Beziers(p) => Self::Beziers(pieces(p, lo, hi)),
            Self::Rational(p) => Self::Rational(pieces(p, lo, hi)),
        }
    }

    /// The curve's box, `[x0, y0, x1, y1]`: exact for a line or a straight Bézier piece; for a
    /// conic or any other piece, each coordinate's extremes found by sampling 64 steps and
    /// refining each turning point by golden section to 1e-12 of the parameter (a piece turns
    /// at most a few times).
    pub fn bbox(&self) -> [f64; 4] {
        let mut b = [f64::INFINITY, f64::INFINITY, -f64::INFINITY, -f64::INFINITY];
        let mut grow = |p: P| {
            b = [
                b[0].min(p[0]),
                b[1].min(p[1]),
                b[2].max(p[0]),
                b[3].max(p[1]),
            ];
        };
        let mut sweep = |f: &dyn Fn(f64) -> P, t0: f64, t1: f64| {
            const N: usize = 64;
            let at = |i: usize| t0 + (t1 - t0) * i as f64 / N as f64;
            let samples: Vec<P> = (0..=N).map(|i| f(at(i))).collect();
            for p in &samples {
                grow(*p);
            }
            for k in [0, 1] {
                for sign in [1.0, -1.0] {
                    for i in 1..N {
                        let v = |j: usize| sign * samples[j][k];
                        if v(i) >= v(i - 1) && v(i) >= v(i + 1) {
                            let (mut lo, mut hi) = (at(i - 1), at(i + 1));
                            let g = 0.5 * (5f64.sqrt() - 1.0);
                            while (hi - lo).abs() > 1e-12 * (t1 - t0).abs().max(1.0) {
                                let (m1, m2) = (hi - g * (hi - lo), lo + g * (hi - lo));
                                if sign * f(m1)[k] > sign * f(m2)[k] {
                                    hi = m2;
                                } else {
                                    lo = m1;
                                }
                            }
                            grow(f(0.5 * (lo + hi)));
                        }
                    }
                }
            }
        };
        match self {
            Curve::Line(a, c) => {
                grow(*a);
                grow(*c);
            }
            Curve::Conic {
                centre,
                radii,
                x_axis,
                y_axis,
                t0,
                t1,
            } => sweep(
                &|t| Curve::conic_at(*centre, *radii, *x_axis, *y_axis, t),
                *t0,
                *t1,
            ),
            Curve::Beziers(pieces) => {
                // A straight piece (a polyline's) spans its end points. Sampled, a piece
                // parallel to an axis ties at every sample and has each one refined.
                for poles in pieces.iter().filter(|p| p.len() != 2) {
                    sweep(&|t| de_casteljau(poles, t), 0.0, 1.0);
                }
                for poles in pieces.iter().filter(|p| p.len() == 2) {
                    grow(poles[0]);
                    grow(poles[1]);
                }
            }
            Curve::Rational(pieces) => {
                for poles in pieces {
                    sweep(&|t| rational_at(poles, t), 0.0, 1.0);
                }
            }
        }
        b
    }

    /// The point at *u*, 0 at the start and 1 at the end: a conic's angle in proportion, and
    /// for pieces end to end, each piece an equal share.
    pub fn at(&self, u: f64) -> P {
        let u = u.clamp(0.0, 1.0);
        let piece = |n: usize| {
            let x = u * n as f64;
            let k = (x.floor() as usize).min(n - 1);
            (k, x - k as f64)
        };
        match self {
            Curve::Line(a, b) => [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u],
            Curve::Conic {
                centre,
                radii,
                x_axis,
                y_axis,
                t0,
                t1,
            } => Curve::conic_at(*centre, *radii, *x_axis, *y_axis, t0 + (t1 - t0) * u),
            Curve::Beziers(pieces) => {
                let (k, t) = piece(pieces.len());
                de_casteljau(&pieces[k], t)
            }
            Curve::Rational(pieces) => {
                let (k, t) = piece(pieces.len());
                rational_at(&pieces[k], t)
            }
        }
    }
}

/// A Bézier piece's point at *t*, by de Casteljau on its poles.
fn de_casteljau(poles: &[P], t: f64) -> P {
    let mut q = poles.to_vec();
    while q.len() > 1 {
        q = q
            .windows(2)
            .map(|w| [0, 1].map(|k| w[0][k] + (w[1][k] - w[0][k]) * t))
            .collect();
    }
    q[0]
}

/// The box of *items*' curves (a face by its boundary), or none when there are none.
pub fn items_bbox<'a>(items: impl IntoIterator<Item = &'a Item>) -> Option<[f64; 4]> {
    let mut out: Option<[f64; 4]> = None;
    for item in items {
        let curves: Vec<&Curve> = match item {
            Item::Edge(c) => vec![c],
            Item::Face(wires) => wires.iter().flatten().collect(),
        };
        for c in curves {
            let b = c.bbox();
            out = Some(out.map_or(b, |o| {
                [
                    o[0].min(b[0]),
                    o[1].min(b[1]),
                    o[2].max(b[2]),
                    o[3].max(b[3]),
                ]
            }));
        }
    }
    out
}

/// What a layer holds: a curve stroked, or a face (its outer boundary and holes) filled.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub enum Item {
    Edge(Curve),
    Face(Vec<Vec<Curve>>),
}

#[derive(Clone, Debug)]
pub struct Style {
    pub colour: [u8; 3],
    /// Line width, mm.
    pub width: f64,
    /// Dash and gap, mm.
    pub dash: Option<&'static [f64]>,
    /// Faces filled in the line colour.
    pub fill: bool,
    /// DXF line weight, hundredths of a mm.
    pub weight: i16,
}

#[derive(Clone, Debug)]
pub struct Layer {
    pub name: String,
    pub style: Style,
    pub items: Vec<Item>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum HAlign {
    Left,
    Center,
    Right,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum VAlign {
    Baseline,
    Middle,
}

/// A label's text, laid invisibly over its drawn glyphs in the PDF and the SVG.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TextRun {
    pub text: String,
    /// The anchor, mm.
    pub at: P,
    /// Em size, mm.
    pub size: f64,
    /// Degrees anticlockwise.
    pub rotation: f64,
    /// The font file's name, as `fonts` resolves it.
    pub font: String,
    pub h_align: HAlign,
    pub v_align: VAlign,
}

/// A measurement an annotation states: the feature (its Analysis ID) and the parameter, in the
/// role vocabulary (`bore.diameter`, `counterbore.depth`, …).
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Claim {
    pub feature: String,
    pub parameter: String,
}

/// What an annotation on the drawing is, for lint (`crate::lint`), which judges the drawing
/// from what it draws rather than from the plan it was drawn from: its label's text runs, the
/// view it belongs to and the measurements it claims to state. The claims are provenance, as
/// Python draftwright's registry is; lint still checks the label's text against the part.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct Annotation {
    /// The plan item it draws, e.g. `d:f:hole:3/bore.diameter`.
    pub name: String,
    /// The view, `v:<name>`; none for sheet furniture.
    pub view: Option<String>,
    /// Its label's runs, as indices into [`Drawing::text`].
    pub runs: Vec<usize>,
    pub claims: Vec<Claim>,
    /// The compiler-bound identities belonging to the approved cell. These remain separate
    /// from its live claims so a changed owner or parameter loses proof. Manufacturing
    /// rows retain source provenance here without adding a second physical measurement owner.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub approved_claims: Vec<Claim>,
    /// Display-complete compiler approval for a measurement schedule cell. Kept apart from
    /// its actual text runs so damaged content cannot certify itself. This is evidence only;
    /// the physical requirement inventory still comes independently from Analysis.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub approved_text: Option<String>,
    /// Model-space datum of a coordinate schedule cell, resolved by the Plan.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub coordinate_origin: Option<[f64; 3]>,
    /// Explicit structured-note authority; prose is never parsed to infer these identities.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub satisfies: Vec<Claim>,
}

/// What a thing drawn on the sheet is, to lint's sheet checks.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum Kind {
    /// A dimension or callout of [`Drawing::annotations`], by the same name.
    Dimension,
    TitleBlock,
    /// The `SCALE` note beside the title block (ISO 7200 §4).
    ScaleNote,
    /// ISO 5456-2's projection-method symbol beside the scale note.
    ProjectionSymbol,
    /// The general notes block.
    Notes,
    /// A spec table (`t:<k>`), by its whole ink.
    Table,
    /// A section's caption or a cutting plane's letter.
    Caption,
    /// A section's hatching (draftwright's `is_section_hatch`).
    Hatch,
    /// A centreline (draftwright's `is_centerline`): the cutting plane's chain line, by its line.
    Centreline,
    /// A cutting plane's arrowhead or wing stub.
    CuttingPlane,
    /// A feature control frame, datum feature symbol, PMI label or default surface finish
    /// symbol of [`Drawing::annotations`], by the same name (`crate::dims::gdt`).
    Gdt,
}

/// Literal wording evidence attached to a finished drawing, with no Spec or planned value.
#[derive(Clone, Debug, PartialEq)]
pub struct LabelWording {
    pub prefix: String,
    pub suffix: String,
    pub reference: bool,
    pub count: Option<usize>,
}
impl LabelWording {
    /// Verify the wrapper around the live printed body. Never substitute an approved value.
    pub(crate) fn body(&self, label: &str) -> Option<String> {
        let label = if self.reference {
            label.strip_prefix('(')?.strip_suffix(')')?
        } else {
            label
        };
        let mut body = label;
        if !self.prefix.is_empty() {
            body = body.strip_prefix(&self.prefix)?.strip_prefix(' ')?;
        }
        if !self.suffix.is_empty() {
            body = body.strip_suffix(&self.suffix)?.strip_suffix(' ')?;
        }
        if body.trim().is_empty() {
            return None;
        }
        let body = self
            .count
            .map_or_else(|| body.to_string(), |n| format!("{n}× {body}"));
        Some(if self.reference {
            format!("({body})")
        } else {
            body
        })
    }
}

/// Read live generated text using only evidence carried by the rendered drawing.
pub(crate) fn drawn_body(drawing: &Drawing, annotation: &Annotation) -> Option<String> {
    let text = annotation
        .runs
        .iter()
        .filter_map(|&i| drawing.text.get(i))
        .map(|r| r.text.as_str())
        .collect::<Vec<_>>()
        .join(" ");
    match drawing.sheet.label_text.get(&annotation.name) {
        Some(wording) => wording.body(&text),
        None => Some(text),
    }
}

/// A thing drawn on the sheet: its name, what it is, the box of all its ink, and the box of its
/// label's text, if it has one (a dimension's is the label's ink box; the port draws every label
/// along or across the page, so the box is the text's own). Its straight line-work is each line
/// it draws by its ends (draftwright's `segments`: extension, dimension and leader lines, a
/// block's rules; not its glyphs or arrowheads), and a callout's leader its elbow, where the
/// shaft meets the shoulder.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Drawn {
    pub name: String,
    pub kind: Kind,
    /// `[x0, y0, x1, y1]`, mm.
    pub ink: [f64; 4],
    pub label: Option<[f64; 4]>,
    pub lines: Vec<[P; 2]>,
    /// Filled arrowhead outlines, kept separate from dimension-reading line grammar.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub heads: Vec<[P; 2]>,
    /// Datum triangle as drawn: base end, apex, base end. Separate from shaft/frame ink.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub datum_triangle: Option<[P; 3]>,
    /// A callout's shaft-to-shoulder bend. Datum symbols have no elbow: their shaft
    /// ends on the frame border, recorded by `lines` and `label`.
    pub elbow: Option<P>,
    /// Placed inside its view on purpose, clear of the view's edges (draftwright's `interior`
    /// placement region), retained from the Plan's proven candidate provenance.
    pub interior: bool,
}

/// A view as drawn: its name (`v:front`), the box of its visible edges, mm, and those edges.
#[derive(Clone, Debug, PartialEq)]
pub struct DrawnView {
    pub name: String,
    pub bbox: [f64; 4],
    pub edges: Vec<Curve>,
}

/// A title-block field as drawn: its field (draftwright's name, `drawing_number`), its cell and
/// the box of its value's ink, mm.
#[derive(Clone, Debug, PartialEq)]
pub struct DrawnField {
    pub field: String,
    pub cell: [f64; 4],
    pub ink: [f64; 4],
}

/// A remote section address claimed by rendered furniture, with its actual text run.
#[derive(Clone, Debug, PartialEq)]
pub struct DrawnReference {
    pub name: String,
    /// Name of the target view or cutting-plane letter.
    pub target: String,
    pub prefix: String,
    pub sheet: u32,
    pub zone: String,
    pub run: usize,
}

/// The sheet as lint's sheet checks read it (`crate::lint::structural`): where the drawing may
/// go, and the views and things drawn, measured from the ink as it was drawn. Written to no file
/// format.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Sheet {
    /// Resolved source inventory for independent rendered-balloon verification.
    pub characteristics: Vec<crate::characteristics::Characteristic>,
    pub inspection_enabled: bool,
    /// Compiled wording wrappers; lint reads the live measured body, never authored numbers.
    pub label_text: BTreeMap<String, LabelWording>,
    /// Approved display precision of each dimension's leading value. Ink is still checked.
    pub precision: BTreeMap<String, u8>,
    /// Remote section addresses, verified against printed text by structural lint.
    pub references: Vec<DrawnReference>,
    /// The drawable area, inside the border (`[x0, y0, x1, y1]`); none when no border is drawn.
    pub drawable: Option<[f64; 4]>,
    /// Each view drawn, in drawing order.
    pub views: Vec<DrawnView>,
    /// Every annotation and piece of sheet furniture drawn, in drawing order.
    pub items: Vec<Drawn>,
    /// Each title-block field drawn with a value, top row first.
    pub fields: Vec<DrawnField>,
    /// Centres actually drawn in each end-on view, for the independent feature lint.
    pub centre_marks: std::collections::BTreeMap<String, Vec<P>>,
    /// The transforms actually used to draw each view.
    pub frames: std::collections::BTreeMap<String, DrawnFrame>,
}

/// The discontinuous projection actually drawn, independent of author intent.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct BreakMap {
    /// Original projected envelope centre, in model mm.
    pub centre: P,
    pub axis: usize,
    pub remove: [f64; 2],
    pub gap: f64,
}
impl BreakMap {
    pub fn reduction(&self) -> f64 {
        self.remove[1] - self.remove[0] - self.gap
    }
    pub(crate) fn key(&self) -> Vec<u64> {
        vec![
            self.axis as u64,
            self.remove[0].to_bits(),
            self.remove[1].to_bits(),
            self.gap.to_bits(),
            self.centre[0].to_bits(),
            self.centre[1].to_bits(),
        ]
    }
    pub(crate) fn map(&self, mut p: [f64; 2]) -> [f64; 2] {
        let x = p[self.axis];
        p[self.axis] = if x <= self.remove[0] {
            x + self.reduction() / 2.
        } else if x >= self.remove[1] {
            x - self.reduction() / 2.
        } else {
            (self.remove[0] + self.remove[1]) / 2.
                + (x - (self.remove[0] + self.remove[1]) / 2.) * self.gap
                    / (self.remove[1] - self.remove[0])
        };
        p
    }
    pub(crate) fn contains_page(&self, p: P, at: P, scale: f64) -> bool {
        let mid = at[self.axis]
            + scale * ((self.remove[0] + self.remove[1]) / 2. - self.centre[self.axis]);
        (p[self.axis] - mid).abs() < self.gap * scale / 2. - 1e-6
    }
    /// Apply the discontinuity in paper space using the original projected frame.
    pub(crate) fn page(&self, p: P, at: P, scale: f64, inverse: bool) -> P {
        let q = [0, 1].map(|k| self.centre[k] + (p[k] - at[k]) / scale);
        let q = if inverse {
            self.inverse(q)
        } else {
            self.map(q)
        };
        [0, 1].map(|k| at[k] + scale * (q[k] - self.centre[k]))
    }
    pub(crate) fn inverse(&self, mut p: [f64; 2]) -> [f64; 2] {
        let x = p[self.axis];
        let mid = (self.remove[0] + self.remove[1]) / 2.;
        p[self.axis] = if x <= mid - self.gap / 2. {
            x - self.reduction() / 2.
        } else if x >= mid + self.gap / 2. {
            x + self.reduction() / 2.
        } else {
            mid + (x - mid) * (self.remove[1] - self.remove[0]) / self.gap
        };
        p
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DrawnFrame {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub broken: Option<BreakMap>,
    pub direction: [f64; 3],
    pub up: [f64; 3],
    pub at: P,
    pub scale: f64,
    /// A half section's axis on the page ([`crate::half_section`]): the page coordinate (0 for
    /// x, 1 for y) that crosses it, and its value there. A half dimension is measured from it.
    pub half_axis: Option<(usize, f64)>,
}

impl DrawnFrame {
    /// Undo the depicted shortening, retaining paper scale for independent ink measurements.
    pub(crate) fn unfold(&self, p: P) -> P {
        self.broken
            .as_ref()
            .map_or(p, |b| b.page(p, self.at, self.scale, true))
    }
}

#[derive(Clone, Debug)]
pub struct Drawing {
    /// Page width and height, mm.
    pub page: P,
    pub layers: Vec<Layer>,
    /// The attribution link's rectangle (x0, y0, x1, y1), mm.
    pub link: Option<[f64; 4]>,
    pub text: Vec<TextRun>,
    /// The annotations drawn, with what each states. Written to no file format.
    pub annotations: Vec<Annotation>,
    /// The views and things drawn, for lint's sheet checks. Written to no file format.
    pub sheet: Sheet,
    /// What could not be drawn as planned ([`undrawn`]). Written to no file format.
    pub problems: Vec<Problem>,
    /// The spec the drawing was made from (JSON), carried in the SVG's metadata, the DXF's
    /// opening comments and the PDF's document information so the drawing can be resumed from
    /// the file alone.
    pub spec: Option<String>,
}

pub const LINK: &str = "https://draftwright.io";
pub const GENERATED_BY: &str = "Generated by draftwright";

/// draftwright's three layers: visible edges, hidden edges and annotations.
pub fn layer_styles() -> [(&'static str, Style); 3] {
    [
        (
            "part",
            Style {
                colour: [0, 0, 0],
                width: 0.5,
                dash: None,
                fill: false,
                weight: 50,
            },
        ),
        (
            "hidden",
            Style {
                colour: [127, 127, 127],
                width: 0.25,
                dash: Some(&[1.5875, 0.79375]),
                fill: false,
                weight: 25,
            },
        ),
        (
            "dims",
            Style {
                colour: [0, 51, 178],
                width: 0.05,
                dash: None,
                fill: true,
                weight: 5,
            },
        ),
    ]
}

/// Thin continuous lines over a part's outline (ISO 128 type 01.1): a thread's minor diameter,
/// a knurl's band ([`crate::cosmetic`]).
pub fn thin_style() -> Style {
    Style {
        colour: [0, 0, 0],
        width: 0.25,
        dash: None,
        fill: false,
        weight: 25,
    }
}

/// Thin long-dash-dot centre furniture (ISO 128 type 04.1).
pub fn centre_style() -> Style {
    Style {
        colour: [0, 0, 0],
        width: 0.15,
        dash: Some(&[8.0, 1.5, 0.2, 1.5]),
        fill: false,
        weight: 15,
    }
}

/// Centre a long dash on the feature, so a cross cannot have a gap at its centre.
pub(crate) fn centre_phase(item: &Item) -> f64 {
    if let Item::Edge(Curve::Line(a, b)) = item {
        (4.0 - (b[0] - a[0]).hypot(b[1] - a[1]) / 2.0).rem_euclid(11.2)
    } else {
        0.0
    }
}

/// The comment that opens the spec in a DXF file; the comments after it carry the spec's JSON.
pub const DXF_SPEC: &str = "draftwright spec, JSON in the comments that follow";

/// The spec as DXF comments (group code 999) ahead of the first section. AutoCAD reads and ignores
/// them ("OPEN honors them and ignores the comments", DXF reference), as do ezdxf and the `dxf`
/// crate, so the file opens as before; a re-save drops them, as it would any other application's
/// note. The JSON is made ASCII (`\uXXXX`) and cut into lines of at most 255 characters, the
/// oldest readers' limit for a string; no line is trimmed when it is read back.
fn dxf_spec_comments(spec: &str) -> Vec<u8> {
    let mut ascii = String::new();
    for c in spec.chars() {
        if c.is_ascii() {
            ascii.push(c);
        } else {
            for unit in c.encode_utf16(&mut [0; 2]) {
                let _ = write!(ascii, "\\u{unit:04x}");
            }
        }
    }
    let mut out = format!("999\r\n{DXF_SPEC}\r\n");
    for line in ascii.as_bytes().chunks(255) {
        out += "999\r\n";
        out += std::str::from_utf8(line).expect("ASCII");
        out += "\r\n";
    }
    out.into_bytes()
}

/// A number as the SVG carries it.
fn num(x: f64) -> String {
    let s = format!("{x:.6}");
    let s = s.trim_end_matches('0').trim_end_matches('.');
    if s == "-0" { "0".into() } else { s.into() }
}

impl Drawing {
    /// Visible and hidden edges have precedence over centre furniture.
    pub(crate) fn paint_layers(&self) -> impl Iterator<Item = &Layer> {
        self.layers
            .iter()
            .filter(|l| l.name == "centres")
            .chain(self.layers.iter().filter(|l| l.name != "centres"))
    }

    /// The SVG: a page in millimetres, y up inside a flipping group, one group per layer, then
    /// the labels' text, invisible.
    pub fn svg(&self) -> String {
        let [w, h] = self.page;
        let mut s = String::new();
        let _ = write!(
            s,
            "<?xml version='1.0' encoding='utf-8'?>\n<svg xmlns:xlink=\"http://www.w3.org/1999/xlink\" width=\"{w:.3}mm\" height=\"{h:.3}mm\" viewBox=\"0 -{h:.3} {w:.3} {h:.3}\" version=\"1.1\" xmlns=\"http://www.w3.org/2000/svg\"><!-- {GENERATED_BY}: {LINK} --><metadata><rdf:RDF xmlns:rdf=\"http://www.w3.org/1999/02/22-rdf-syntax-ns#\" xmlns:dc=\"http://purl.org/dc/elements/1.1/\"><rdf:Description><dc:creator>draftwright</dc:creator><dc:source>{LINK}</dc:source><dc:description>{GENERATED_BY}</dc:description></rdf:Description></rdf:RDF>{}</metadata>\n  <g transform=\"scale(1,-1)\" stroke-linecap=\"round\">\n",
            self.spec.as_ref().map_or(String::new(), |j| format!(
                "<draftwright:spec xmlns:draftwright=\"{LINK}\"><![CDATA[{}]]></draftwright:spec>",
                j.replace("]]>", "]]]]><![CDATA[>")
            ))
        );
        for layer in self.paint_layers() {
            let st = &layer.style;
            let rgb = format!("rgb({},{},{})", st.colour[0], st.colour[1], st.colour[2]);
            let fill = if st.fill { rgb.clone() } else { "none".into() };
            let dash = st.dash.map_or(String::new(), |pattern| {
                format!(
                    " stroke-dasharray=\"{}\"",
                    pattern
                        .iter()
                        .map(|&n| num(n))
                        .collect::<Vec<_>>()
                        .join(" ")
                )
            });
            let _ = writeln!(
                s,
                "    <g fill=\"{fill}\" stroke=\"{rgb}\" stroke-width=\"{}\" id=\"{}\"{dash}>",
                num(st.width),
                layer.name
            );
            for item in &layer.items {
                let ink = svg_item(item);
                let ink = if layer.name == "centres" {
                    ink.replacen(
                        ' ',
                        &format!(" stroke-dashoffset=\"{}\" ", num(centre_phase(item))),
                        1,
                    )
                } else {
                    ink
                };
                let _ = writeln!(s, "      {ink}");
            }
            s += "    </g>\n";
        }
        s += "  </g>\n";
        // The labels' text, invisible over their drawn glyphs (which are outlines) so the SVG can
        // be searched, copied and read aloud: in a group with no ID, which is no layer.
        if !self.text.is_empty() {
            s += "  <g fill=\"rgb(0,0,0)\" fill-opacity=\"0\" stroke=\"none\" xml:space=\"preserve\">\n";
            for run in &self.text {
                let (x, y) = (num(run.at[0]), num(-run.at[1]));
                let family = if run.font.contains("Mono") {
                    "monospace"
                } else {
                    "sans-serif"
                };
                let anchor = match run.h_align {
                    HAlign::Left => "",
                    HAlign::Center => " text-anchor=\"middle\"",
                    HAlign::Right => " text-anchor=\"end\"",
                };
                let baseline = match run.v_align {
                    VAlign::Baseline => "",
                    VAlign::Middle => " dominant-baseline=\"central\"",
                };
                let rotate = if run.rotation == 0.0 {
                    String::new()
                } else {
                    format!(" transform=\"rotate({} {x} {y})\"", num(-run.rotation))
                };
                let text = run
                    .text
                    .replace('&', "&amp;")
                    .replace('<', "&lt;")
                    .replace('>', "&gt;");
                let _ = writeln!(
                    s,
                    "    <text x=\"{x}\" y=\"{y}\" font-family=\"{family}\" font-size=\"{}\"{anchor}{baseline}{rotate}>{text}</text>",
                    num(run.size)
                );
            }
            s += "  </g>\n";
        }
        if let Some([x0, y0, x1, y1]) = self.link {
            let _ = write!(
                s,
                "<a xlink:href=\"{LINK}\" href=\"{LINK}\" target=\"_blank\"><rect x=\"{x0:.3}\" y=\"{:.3}\" width=\"{:.3}\" height=\"{:.3}\" fill=\"transparent\" pointer-events=\"all\"/></a>",
                -y1,
                x1 - x0,
                y1 - y0
            );
        }
        s += "</svg>\n";
        s
    }

    /// The DXF (R2013, millimetres): each layer with its line weight, each curve as a LINE,
    /// CIRCLE, ARC, ELLIPSE or SPLINE, and each face as the curves of its boundary.
    pub fn dxf(&self) -> Vec<u8> {
        use dxf::entities::{Arc, Circle, Ellipse, Entity, EntityType, Line};
        use dxf::enums::{AcadVersion, Units};
        use dxf::tables::Layer as DxfLayer;
        use dxf::{Point, Vector};
        let mut d = dxf::Drawing::new();
        d.header.version = AcadVersion::R2013;
        d.header.default_drawing_units = Units::Millimeters;
        // The crate stamps the clock and random GUIDs: pin them, so the same drawing gives the
        // same bytes (reportlab's `invariant` dates for draftwright's PDFs are 2000-01-01 too).
        // The crate writes a local date as its time since local midnight, 1900-01-01, and the
        // zone's offset then and in 2000 differ (local mean time in many zones): the local date is
        // that epoch plus the 36524 days to 2000-01-01, so it is Julian day 2451545 in every zone.
        {
            use chrono::TimeZone;
            let local = chrono::Local
                .with_ymd_and_hms(1900, 1, 1, 0, 0, 0)
                .single()
                .expect("the crate's local epoch")
                + chrono::Duration::days(36524);
            let utc = chrono::Utc
                .with_ymd_and_hms(2000, 1, 1, 0, 0, 0)
                .single()
                .expect("a fixed time");
            (d.header.creation_date, d.header.update_date) = (local, local);
            (
                d.header.creation_date_universal,
                d.header.update_date_universal,
            ) = (utc, utc);
            use sha2::Digest;
            let digest = sha2::Sha256::digest(self.spec.as_deref().unwrap_or("").as_bytes());
            let id = uuid::Uuid::from_slice(&digest[..16]).expect("16 bytes");
            (d.header.fingerprint_guid, d.header.version_guid) = (id, id);
        }
        // The crate builds a line weight of a given value only by deserialising one.
        let weight = |w: i16| -> dxf::LineWeight {
            serde_json::from_value(serde_json::json!({ "raw_value": w }))
                .expect("a line weight is its raw value")
        };
        // ezdxf's standard layers, at the default weight, then the drawing's.
        for l in d.layers_mut() {
            l.line_weight = weight(-3);
        }
        d.add_layer(DxfLayer {
            name: "Defpoints".into(),
            line_weight: weight(-3),
            ..Default::default()
        });
        d.add_line_type(dxf::tables::LineType {
            name: "CENTRE".into(),
            description: "ISO 128 long dash dot".into(),
            element_count: 4,
            total_pattern_length: 11.2,
            dash_dot_space_lengths: vec![8.0, -1.5, 0.2, -1.5],
            ..Default::default()
        });
        for layer in self.paint_layers() {
            d.add_layer(DxfLayer {
                name: layer.name.clone(),
                line_weight: weight(layer.style.weight),
                line_type_name: if layer.name == "centres" {
                    "CENTRE".into()
                } else {
                    "CONTINUOUS".into()
                },
                ..Default::default()
            });
        }
        let pt = |p: P| Point::new(p[0], p[1], 0.0);
        for layer in self.paint_layers() {
            let curves = layer.items.iter().flat_map(|item| match item {
                Item::Edge(c) => vec![c],
                Item::Face(wires) => wires.iter().flatten().collect(),
            });
            for curve in curves {
                let kind = match curve {
                    Curve::Line(a, b) => EntityType::Line(Line::new(pt(*a), pt(*b))),
                    Curve::Conic {
                        centre,
                        radii,
                        x_axis,
                        y_axis,
                        t0,
                        t1,
                    } => {
                        // As an anticlockwise arc: a clockwise frame runs its parameter backwards.
                        let ccw = x_axis[0] * y_axis[1] - x_axis[1] * y_axis[0] > 0.0;
                        let (mut a, mut b) = if ccw { (*t0, *t1) } else { (-*t0, -*t1) };
                        if b < a {
                            (a, b) = (b, a);
                        }
                        let rotation = x_axis[1].atan2(x_axis[0]);
                        let closed = (b - a) >= 2.0 * PI - 1e-12;
                        if (radii[0] - radii[1]).abs() <= 1e-12 * radii[0] {
                            if closed {
                                EntityType::Circle(Circle::new(pt(*centre), radii[0]))
                            } else {
                                EntityType::Arc(Arc::new(
                                    pt(*centre),
                                    radii[0],
                                    (a + rotation).to_degrees(),
                                    (b + rotation).to_degrees(),
                                ))
                            }
                        } else {
                            let (start_parameter, end_parameter) =
                                if closed { (0.0, 2.0 * PI) } else { (a, b) };
                            EntityType::Ellipse(Ellipse {
                                center: pt(*centre),
                                major_axis: Vector::new(
                                    x_axis[0] * radii[0],
                                    x_axis[1] * radii[0],
                                    0.0,
                                ),
                                minor_axis_ratio: radii[1] / radii[0],
                                start_parameter,
                                end_parameter,
                                ..Default::default()
                            })
                        }
                    }
                    Curve::Beziers(pieces) => match pieces.as_slice() {
                        [only] if only.len() == 2 => {
                            EntityType::Line(Line::new(pt(only[0]), pt(only[1])))
                        }
                        _ => EntityType::Spline(spline(
                            pieces
                                .iter()
                                .map(|p| p.iter().map(|q| [q[0], q[1], 1.0]).collect())
                                .collect(),
                        )),
                    },
                    Curve::Rational(pieces) => EntityType::Spline(spline(pieces.clone())),
                };
                let mut entity = Entity::new(kind);
                entity.common.layer = layer.name.clone();
                // The crate writes these two always: weight and transparency by layer
                // (an explicit 0 would be the thinnest line and fully transparent).
                entity.common.lineweight_enum_value = -1;
                entity.common.transparency = 0x0200_00FF;
                d.add_entity(entity);
            }
        }
        let mut out = Vec::new();
        if let Some(spec) = &self.spec {
            out = dxf_spec_comments(spec);
        }
        d.save(&mut out).expect("writes to memory");
        out
    }

    /// The PDF: the page in points, each layer's paths stroked (faces filled) in its style, the
    /// labels' text invisible over them in the embedded fonts (looked up by file name in
    /// *fonts*), and the attribution link.
    pub fn pdf(&self, fonts: &dyn Fn(&str) -> Option<Vec<u8>>) -> Vec<u8> {
        Self::pdf_pages(std::slice::from_ref(self), fonts)
    }

    /// One PDF page per drawing, in order, with the first drawing's whole Spec embedded.
    /// A single drawing uses precisely the same objects and bytes as [`Self::pdf`].
    pub fn pdf_pages(drawings: &[Drawing], fonts: &dyn Fn(&str) -> Option<Vec<u8>>) -> Vec<u8> {
        use pdf_writer::{Finish, Name, Pdf, Ref, Str, TextStr};

        let (catalog, tree, page, content_id, info, link_id) = (
            Ref::new(1),
            Ref::new(2),
            Ref::new(3),
            Ref::new(4),
            Ref::new(5),
            Ref::new(6),
        );
        let mut next = 7;
        let mut alloc = || {
            next += 1;
            Ref::new(next - 1)
        };
        let mut pdf = Pdf::new();
        pdf.catalog(catalog).pages(tree);
        let mut pages = vec![(page, content_id, link_id)];
        for _ in 1..drawings.len() {
            pages.push((alloc(), alloc(), alloc()));
        }
        pdf.pages(tree)
            .kids(pages.iter().take(drawings.len()).map(|p| p.0))
            .count(drawings.len() as i32);
        let mut doc_info = pdf.document_info(info);
        doc_info
            .title(TextStr(GENERATED_BY))
            .creator(TextStr(GENERATED_BY))
            .producer(TextStr("draftwright"));
        if let Some(spec) = drawings.first().and_then(|d| d.spec.as_ref()) {
            doc_info.pair(Name(b"DraftwrightSpec"), Str(spec.as_bytes()));
        }
        doc_info.finish();

        for (drawing, (page, content_id, link_id)) in drawings.iter().zip(pages) {
            drawing.pdf_page(
                &mut pdf,
                (tree, page, content_id, link_id),
                fonts,
                &mut alloc,
            );
        }
        pdf.finish()
    }

    fn pdf_page(
        &self,
        pdf: &mut pdf_writer::Pdf,
        (tree, page, content_id, link_id): (
            pdf_writer::Ref,
            pdf_writer::Ref,
            pdf_writer::Ref,
            pdf_writer::Ref,
        ),
        fonts: &dyn Fn(&str) -> Option<Vec<u8>>,
        alloc: &mut dyn FnMut() -> pdf_writer::Ref,
    ) {
        use pdf_writer::types::{
            ActionType, AnnotationType, CidFontType, FontFlags, LineCapStyle, SystemInfo,
            TextRenderingMode, UnicodeCmap,
        };
        use pdf_writer::{Content, Filter, Finish, Name, Rect, Str};

        const K: f64 = 72.0 / 25.4;
        let mut c = Content::new();
        c.save_state();
        c.transform([K as f32, 0.0, 0.0, K as f32, 0.0, 0.0]);
        c.set_line_cap(LineCapStyle::RoundCap);
        for layer in self.paint_layers() {
            let st = &layer.style;
            let [r, g, b] = st.colour.map(|x| x as f32 / 255.0);
            c.set_stroke_rgb(r, g, b);
            c.set_fill_rgb(r, g, b);
            c.set_line_width(st.width as f32);
            match st.dash {
                Some(pattern) => c.set_dash_pattern(pattern.iter().map(|&n| n as f32), 0.0),
                None => c.set_dash_pattern([], 0.0),
            };
            for item in &layer.items {
                if layer.name == "centres" {
                    c.set_dash_pattern(
                        // Invariant: This branch executes only when the style has a dash pattern.
                        st.dash.unwrap().iter().map(|&n| n as f32),
                        centre_phase(item) as f32,
                    );
                }
                match item {
                    Item::Edge(curve) => {
                        let s = curve.start();
                        c.move_to(s[0] as f32, s[1] as f32);
                        pdf_curve(&mut c, curve);
                        c.stroke();
                    }
                    Item::Face(wires) => {
                        for wire in wires {
                            let s = wire[0].start();
                            c.move_to(s[0] as f32, s[1] as f32);
                            for curve in wire {
                                pdf_curve(&mut c, curve);
                            }
                        }
                        if st.fill {
                            c.fill_nonzero_and_stroke();
                        } else {
                            c.stroke();
                        }
                    }
                }
            }
        }
        c.restore_state();

        // The labels' text, invisible: each font subset to the glyphs shown. Each character is
        // shown by its own CID, which the ToUnicode CMap maps back to it alone so the text
        // extracted is the text written: its glyph's ID (in the whole font) where the font has
        // the glyph and no other character took it, else a CID after the font's glyphs drawn
        // (invisibly) as that glyph or `.notdef`.
        // The bundled fonts lack the callout symbols `⌀ ↧ ⌴ ⌵`, which the drawing's ink draws
        // as the font's `ø` and as strokes; this way they are still searched and copied as such.
        let faces: BTreeMap<&str, Vec<u8>> = self
            .text
            .iter()
            .map(|t| t.font.as_str())
            .collect::<BTreeSet<_>>()
            .into_iter()
            .filter_map(|f| Some((f, fonts(f)?)))
            .collect();
        let names: BTreeMap<&str, String> = faces
            .keys()
            .enumerate()
            .map(|(i, f)| (*f, format!("F{i}")))
            .collect();
        let mut chars: BTreeMap<&str, BTreeSet<char>> = BTreeMap::new();
        for run in &self.text {
            if faces.contains_key(run.font.as_str()) {
                chars
                    .entry(run.font.as_str())
                    .or_default()
                    .extend(run.text.chars());
            }
        }
        // Per font: each character's CID, and the glyphs of the CIDs after the font's own.
        let mut cids: BTreeMap<&str, (BTreeMap<char, u16>, Vec<u16>)> = BTreeMap::new();
        for (file, set) in &chars {
            let Ok(face) = ttf_parser::Face::parse(&faces[file], 0) else {
                continue;
            };
            let n = face.number_of_glyphs();
            let (mut of, mut extra, mut taken) = (BTreeMap::new(), Vec::new(), BTreeSet::new());
            for &ch in set {
                let g = crate::text::Font::glyph(&face, ch).map_or(0, |g| g.0);
                let cid = if g != 0 && taken.insert(g) {
                    g
                } else {
                    extra.push(g);
                    n + (extra.len() - 1) as u16
                };
                of.insert(ch, cid);
            }
            cids.insert(file, (of, extra));
        }
        let glyph = |file: &str, cid: u16, n: u16| -> u16 {
            if cid < n {
                cid
            } else {
                cids[file].1[(cid - n) as usize]
            }
        };
        for run in &self.text {
            let Some(data) = faces.get(run.font.as_str()) else {
                continue;
            };
            let Ok(face) = ttf_parser::Face::parse(data, 0) else {
                continue;
            };
            let (of, n) = (&cids[run.font.as_str()].0, face.number_of_glyphs());
            let em = face.units_per_em() as f64;
            let size = run.size * K;
            let shown: Vec<u16> = run.text.chars().map(|ch| of[&ch]).collect();
            let width: f64 = shown
                .iter()
                .map(|&cid| {
                    let g = glyph(run.font.as_str(), cid, n);
                    face.glyph_hor_advance(ttf_parser::GlyphId(g)).unwrap_or(0) as f64
                })
                .sum::<f64>()
                / em
                * size;
            let dx = match run.h_align {
                HAlign::Left => 0.0,
                HAlign::Center => -width / 2.0,
                HAlign::Right => -width,
            };
            let dy = match run.v_align {
                VAlign::Baseline => 0.0,
                VAlign::Middle => {
                    -(face.ascender() as f64 + face.descender() as f64) / em * size / 2.0
                }
            };
            let (cos, sin) = (
                run.rotation.to_radians().cos(),
                run.rotation.to_radians().sin(),
            );
            let origin = [
                run.at[0] * K + cos * dx - sin * dy,
                run.at[1] * K + sin * dx + cos * dy,
            ];
            let bytes: Vec<u8> = shown.iter().flat_map(|cid| cid.to_be_bytes()).collect();
            c.begin_text();
            c.set_text_rendering_mode(TextRenderingMode::Invisible);
            c.set_font(Name(names[run.font.as_str()].as_bytes()), size as f32);
            c.set_text_matrix([
                cos as f32,
                sin as f32,
                -sin as f32,
                cos as f32,
                origin[0] as f32,
                origin[1] as f32,
            ]);
            c.show(Str(&bytes));
            c.end_text();
        }
        let deflate = |data: &[u8]| {
            use std::io::Write as _;
            let mut z = flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::best());
            // Invariant: The compression writer targets an in-memory Vec.
            z.write_all(data).expect("writes to memory");
            // Invariant: The compression writer targets an in-memory Vec.
            z.finish().expect("writes to memory")
        };
        let content = deflate(&c.finish());
        pdf.stream(content_id, &content).filter(Filter::FlateDecode);

        let mut font_refs = Vec::new();
        for (file, (of, _)) in &cids {
            let data = &faces[file];
            // Invariant: The font map contains only faces successfully parsed above.
            let face = ttf_parser::Face::parse(data, 0).expect("parsed above");
            let n = face.number_of_glyphs();
            let by_cid: BTreeMap<u16, char> = of.iter().map(|(ch, cid)| (*cid, *ch)).collect();
            let em = face.units_per_em() as f64;
            let scale = |v: f64| (v * 1000.0 / em) as f32;
            // The font program holds only the glyphs shown (and `.notdef`, and the parts of
            // composite glyphs), renumbered in glyph order; the CIDs stay as above, mapped to
            // the new glyph IDs, so the content stream and widths are those of the whole font.
            // Its name is tagged, as a subset's must be, from the glyphs it keeps. A font the
            // subsetter cannot read (not a bundled one) is embedded whole, untagged.
            let used: BTreeSet<u16> = by_cid.keys().map(|&cid| glyph(file, cid, n)).collect();
            let used: Vec<u16> = used.into_iter().collect();
            let remapper = subsetter::GlyphRemapper::new_from_glyphs_sorted(&used);
            let subset = subsetter::subset(data, 0, &remapper).ok();
            let program = subset.as_deref().unwrap_or(data);
            let new_gid = |g: u16| match subset {
                // Invariant: used and the glyph remapper were built from every glyph in by_cid.
                Some(_) => remapper.get(g).expect("each glyph shown is kept"),
                None => g,
            };
            let mut base = file.trim_end_matches(".ttf").to_string();
            if subset.is_some() {
                use sha2::Digest;
                let bytes: Vec<u8> = used.iter().flat_map(|g| g.to_be_bytes()).collect();
                let tag: String = sha2::Sha256::digest(&bytes)[..6]
                    .iter()
                    .map(|b| (b'A' + b % 26) as char)
                    .collect();
                base = format!("{tag}+{base}");
            }
            let (type0, cid, descriptor, file_ref, cmap_ref) =
                (alloc(), alloc(), alloc(), alloc(), alloc());
            pdf.type0_font(type0)
                .base_font(Name(base.as_bytes()))
                .encoding_predefined(Name(b"Identity-H"))
                .descendant_font(cid)
                .to_unicode(cmap_ref);
            let mut cid_font = pdf.cid_font(cid);
            cid_font
                .subtype(CidFontType::Type2)
                .base_font(Name(base.as_bytes()))
                .system_info(SystemInfo {
                    registry: Str(b"Adobe"),
                    ordering: Str(b"Identity"),
                    supplement: 0,
                })
                .font_descriptor(descriptor);
            let gid_map = alloc();
            cid_font.cid_to_gid_map_stream(gid_map);
            let mut widths = cid_font.widths();
            for &cid in by_cid.keys() {
                let g = glyph(file, cid, n);
                widths.consecutive(
                    cid,
                    [scale(
                        face.glyph_hor_advance(ttf_parser::GlyphId(g)).unwrap_or(0) as f64,
                    )],
                );
            }
            widths.finish();
            cid_font.finish();
            let bbox = face.global_bounding_box();
            pdf.font_descriptor(descriptor)
                .name(Name(base.as_bytes()))
                .flags(FontFlags::NON_SYMBOLIC)
                .bbox(Rect::new(
                    scale(bbox.x_min as f64),
                    scale(bbox.y_min as f64),
                    scale(bbox.x_max as f64),
                    scale(bbox.y_max as f64),
                ))
                .italic_angle(0.0)
                .ascent(scale(face.ascender() as f64))
                .descent(scale(face.descender() as f64))
                .cap_height(scale(
                    face.capital_height().unwrap_or(face.ascender()) as f64
                ))
                .stem_v(80.0)
                .font_file2(file_ref);
            pdf.stream(file_ref, &deflate(program))
                .filter(Filter::FlateDecode)
                .pair(Name(b"Length1"), program.len() as i32);
            let mut cmap = UnicodeCmap::new(
                Name(b"Custom"),
                SystemInfo {
                    registry: Str(b"Adobe"),
                    ordering: Str(b"UCS"),
                    supplement: 0,
                },
            );
            for (cid, ch) in &by_cid {
                cmap.pair(*cid, *ch);
            }
            let cmap = cmap.finish();
            pdf.cmap(cmap_ref, &deflate(&cmap))
                .filter(Filter::FlateDecode);
            // Each CID shown to its glyph's new ID; the CIDs not shown to `.notdef`.
            let last = by_cid.keys().next_back().copied().unwrap_or(0);
            let map: Vec<u8> = (0..=last)
                .map(|cid| {
                    if by_cid.contains_key(&cid) {
                        new_gid(glyph(file, cid, n))
                    } else {
                        0
                    }
                })
                .flat_map(u16::to_be_bytes)
                .collect();
            pdf.stream(gid_map, &deflate(&map))
                .filter(Filter::FlateDecode);
            font_refs.push((names[file].clone(), type0));
        }

        let mut p = pdf.page(page);
        p.parent(tree)
            .media_box(Rect::new(
                0.0,
                0.0,
                (self.page[0] * K) as f32,
                (self.page[1] * K) as f32,
            ))
            .contents(content_id);
        if self.link.is_some() {
            p.annotations([link_id]);
        }
        let mut resources = p.resources();
        let mut font_dict = resources.fonts();
        for (name, r) in &font_refs {
            font_dict.pair(Name(name.as_bytes()), *r);
        }
        font_dict.finish();
        resources.finish();
        p.finish();
        if let Some([x0, y0, x1, y1]) = self.link {
            let mut a = pdf.annotation(link_id);
            a.subtype(AnnotationType::Link)
                .rect(Rect::new(
                    (x0 * K) as f32,
                    (y0 * K) as f32,
                    (x1 * K) as f32,
                    (y1 * K) as f32,
                ))
                .border(0.0, 0.0, 0.0, None);
            a.action()
                .action_type(ActionType::Uri)
                .uri(Str(LINK.as_bytes()));
        }
    }
}

/// One DXF spline through rational Bézier pieces (homogeneous poles): each raised to the
/// highest degree, joined by knots of full multiplicity.
fn spline(pieces: Vec<Vec<[f64; 3]>>) -> dxf::entities::Spline {
    let degree = pieces.iter().map(|p| p.len() - 1).max().unwrap_or(1);
    let mut s = dxf::entities::Spline {
        degree_of_curve: degree as i32,
        knot_values: vec![0.0; degree + 1],
        ..Default::default()
    };
    for (i, piece) in pieces.iter().enumerate() {
        let poles = elevate(piece, degree);
        for q in &poles[usize::from(i > 0)..] {
            s.control_points
                .push(dxf::Point::new(q[0] / q[2], q[1] / q[2], 0.0));
            s.weight_values.push(q[2]);
        }
        let repeat = if i + 1 == pieces.len() {
            degree + 1
        } else {
            degree
        };
        s.knot_values
            .extend(std::iter::repeat_n((i + 1) as f64, repeat));
    }
    if s.weight_values.iter().all(|w| (w - 1.0).abs() <= 1e-12) {
        s.weight_values.clear();
    }
    s
}

/// A homogeneous pole's point.
fn point(q: [f64; 3]) -> P {
    [q[0] / q[2], q[1] / q[2]]
}

/// A rational Bézier's point at *t*, by de Casteljau on its homogeneous poles.
fn rational_at(poles: &[[f64; 3]], t: f64) -> P {
    let mut q = poles.to_vec();
    while q.len() > 1 {
        q = q
            .windows(2)
            .map(|w| [0, 1, 2].map(|k| w[0][k] + (w[1][k] - w[0][k]) * t))
            .collect();
    }
    point(q[0])
}

/// Rational pieces as the polynomial ones SVG and PDF can draw (up to cubics): a piece of equal
/// weights and at most 4 poles is one already; any other is drawn as chords within 0.1 µm of it.
fn polynomial(pieces: &[Vec<[f64; 3]>]) -> Vec<Vec<P>> {
    let mut out = Vec::new();
    for piece in pieces {
        let w = piece[0][2];
        if piece.len() <= 4 && piece.iter().all(|q| (q[2] - w).abs() <= 1e-12 * w) {
            out.push(piece.iter().map(|q| point(*q)).collect());
            continue;
        }
        let mut ts = vec![0.0, 0.5, 1.0];
        for _ in 0..20 {
            let mut next = vec![ts[0]];
            let mut split = false;
            for w in ts.windows(2) {
                let (a, b, m) = (
                    rational_at(piece, w[0]),
                    rational_at(piece, w[1]),
                    rational_at(piece, 0.5 * (w[0] + w[1])),
                );
                if (m[0] - 0.5 * (a[0] + b[0])).hypot(m[1] - 0.5 * (a[1] + b[1])) > 1e-4 {
                    next.push(0.5 * (w[0] + w[1]));
                    split = true;
                }
                next.push(w[1]);
            }
            ts = next;
            if !split {
                break;
            }
        }
        out.extend(
            ts.windows(2)
                .map(|w| vec![rational_at(piece, w[0]), rational_at(piece, w[1])]),
        );
    }
    out
}

impl From<&haecceity::hlr::Exact> for Curve {
    /// A projected stretch's exact curve as drawn: a NURBS curve cut to the stretch and at its
    /// knots into rational Bézier pieces.
    fn from(exact: &haecceity::hlr::Exact) -> Self {
        use haecceity::hlr::Exact;
        match exact {
            Exact::Line(a, b) => Curve::Line(*a, *b),
            Exact::Conic {
                centre,
                radii,
                x_axis,
                y_axis,
                t0,
                t1,
            } => Curve::Conic {
                centre: *centre,
                radii: *radii,
                x_axis: *x_axis,
                y_axis: *y_axis,
                t0: *t0,
                t1: *t1,
            },
            Exact::Nurbs {
                degree,
                poles,
                weights,
                knots,
                t0,
                t1,
            } => {
                // Kernel geometry is an input boundary too: malformed spline arrays
                // become edges_not_drawn through projected(), rather than slice panics.
                let valid = *degree > 0
                    && poles.len() > *degree
                    && poles.len() == weights.len()
                    && poles
                        .len()
                        .checked_add(*degree)
                        .and_then(|n| n.checked_add(1))
                        == Some(knots.len())
                    && knots.iter().all(|k| k.is_finite())
                    && knots.windows(2).all(|w| w[0] <= w[1])
                    && poles.iter().flatten().all(|v| v.is_finite())
                    && weights.iter().all(|w| w.is_finite() && *w > 0.)
                    && t0.is_finite()
                    && t1.is_finite();
                if !valid {
                    return Curve::Rational(Vec::new());
                }
                let homogeneous = poles
                    .iter()
                    .zip(weights)
                    .map(|(p, w)| [p[0] * w, p[1] * w, *w])
                    .collect();
                Curve::Rational(stretch_pieces(
                    *degree,
                    knots.clone(),
                    homogeneous,
                    *t0,
                    *t1,
                ))
            }
        }
    }
}

/// A projected stretch as drawn: its exact curve where it has one, else its points. None when
/// there is nothing to draw: an exact stretch of no span (t0 == t1), one off its curve (a
/// parameter range past the knots of a curve that is not closed), or fewer than 2 points.
pub fn projected(piece: &haecceity::hlr::Projected) -> Option<Curve> {
    let curve = match &piece.exact {
        Some(exact) => Curve::from(exact),
        None => Curve::Beziers(piece.points.windows(2).map(|w| w.to_vec()).collect()),
    };
    match &curve {
        Curve::Beziers(b) if b.is_empty() => None,
        Curve::Rational(r) if r.is_empty() => None,
        _ => Some(curve),
    }
}

/// The problems for projected stretches left undrawn ([`projected`]), by view ID and count.
pub fn undrawn(counts: &BTreeMap<String, usize>) -> Vec<Problem> {
    counts
        .iter()
        .map(|(view, n)| Problem {
            id: format!("p:edges_not_drawn:{view}"),
            code: "edges_not_drawn".into(),
            severity: "info".into(),
            message: format!(
                "{n} projected edge piece{} in {} had no length to draw (an exact curve's \
                 stretch of no span or off its curve, or fewer than two points) and {} left out.",
                if *n == 1 { "" } else { "s" },
                crate::wording::view(view),
                if *n == 1 { "was" } else { "were" }
            ),
            subjects: vec![view.clone()],
            ..Default::default()
        })
        .collect()
}

/// Boehm's insertion of knot *t* into a B-spline of *degree* (homogeneous poles).
fn insert_knot(degree: usize, knots: &mut Vec<f64>, poles: &mut Vec<[f64; 3]>, t: f64) {
    let s = knots.partition_point(|k| *k <= t) - 1;
    let s = s.min(poles.len() - 1);
    let mut next = Vec::with_capacity(poles.len() + 1);
    for i in 0..=poles.len() {
        if i + degree <= s {
            next.push(poles[i]);
        } else if i > s {
            next.push(poles[i - 1]);
        } else {
            let a = (t - knots[i]) / (knots[i + degree] - knots[i]);
            next.push([0, 1, 2].map(|k| (1.0 - a) * poles[i - 1][k] + a * poles[i][k]));
        }
    }
    knots.insert(s + 1, t);
    *poles = next;
}

/// The stretch of a B-spline from *t0* to *t1* (either way round) as Bézier pieces, in order
/// from *t0*, where the range may leave the knot domain: haecceity unwraps a closed curve's
/// parameters along a stretch, so a stretch across the seam, or wholly in another period, runs
/// past the domain's ends (nist_stc_10's closed periodic cubics, from one period to two). Such
/// a range is cut at the seams and each part shifted into the domain. A range past the domain
/// of a curve that is not closed, beyond rounding, has no pieces: it is left out and reported
/// ([`undrawn`]), not guessed at.
fn stretch_pieces(
    degree: usize,
    knots: Vec<f64>,
    poles: Vec<[f64; 3]>,
    t0: f64,
    t1: f64,
) -> Vec<Vec<[f64; 3]>> {
    let (start, end) = (knots[degree], knots[knots.len() - degree - 1]);
    let (lo, hi) = (t0.min(t1), t0.max(t1));
    if start <= lo && hi <= end {
        return bezier_pieces(degree, knots, poles, t0, t1);
    }
    let period = end - start;
    if !period.is_finite() || period <= 0.0 || !lo.is_finite() || !hi.is_finite() {
        return Vec::new();
    }
    let tol = 1e-9 * period;
    let whole = bezier_pieces(degree, knots.clone(), poles.clone(), start, end);
    let (Some(first), Some(last)) = (whole.first(), whole.last()) else {
        return Vec::new();
    };
    let (first, last) = (rational_at(first, 0.0), rational_at(last, 1.0));
    let size = poles
        .iter()
        .flat_map(|q| [q[0] / q[2], q[1] / q[2]])
        .fold(1.0f64, |m, c| m.max(c.abs()));
    if (first[0] - last[0]).hypot(first[1] - last[1]) > 1e-9 * size {
        // Not closed: only rounding past the ends is drawn, clamped to them.
        if lo < start - tol || hi > end + tol {
            return Vec::new();
        }
        let clamp = |t: f64| t.clamp(start, end);
        return bezier_pieces(degree, knots, poles, clamp(t0), clamp(t1));
    }
    let mut pieces = Vec::new();
    let mut s = lo;
    while s < hi - tol {
        // The period s is in (a point within rounding of a seam taken as past it), and the part
        // of the range in it, shifted into the domain.
        let shift = ((s - start + tol) / period).floor() * period;
        let e = hi.min(end + shift);
        let clamp = |t: f64| (t - shift).clamp(start, end);
        pieces.extend(bezier_pieces(
            degree,
            knots.clone(),
            poles.clone(),
            clamp(s),
            clamp(e),
        ));
        s = e;
    }
    if t0 > t1 {
        pieces.reverse();
        pieces.iter_mut().for_each(|p| p.reverse());
    }
    pieces
}

/// The stretch of a B-spline from *t0* to *t1* (either way round, within its knot domain) as
/// Bézier pieces, in order from *t0*: every knot in between, and the ends, raised to full
/// multiplicity.
fn bezier_pieces(
    degree: usize,
    mut knots: Vec<f64>,
    mut poles: Vec<[f64; 3]>,
    t0: f64,
    t1: f64,
) -> Vec<Vec<[f64; 3]>> {
    let (lo, hi) = (t0.min(t1), t0.max(t1));
    let mut breaks: Vec<f64> = knots
        .iter()
        .copied()
        .filter(|k| *k > lo && *k < hi)
        .collect();
    breaks.extend([lo, hi]);
    breaks.sort_by(f64::total_cmp);
    breaks.dedup();
    for &b in &breaks {
        let have = knots.iter().filter(|k| **k == b).count();
        for _ in have..degree {
            insert_knot(degree, &mut knots, &mut poles, b);
        }
    }
    let mut pieces: Vec<Vec<[f64; 3]>> = breaks
        .windows(2)
        .map(|w| {
            // The span starting at w[0]: its poles are the Bézier's.
            let s = knots.partition_point(|k| *k <= w[0]) - 1;
            poles[s - degree..=s].to_vec()
        })
        .collect();
    if t0 > t1 {
        pieces.reverse();
        pieces.iter_mut().for_each(|p| p.reverse());
    }
    pieces
}

/// A PDF literal string's text, from just after its opening parenthesis: escapes undone, ending
/// at the parenthesis that balances it.
pub fn pdf_unescape(s: &str) -> Option<String> {
    let (mut out, mut depth, mut chars) = (Vec::new(), 1usize, s.bytes().peekable());
    while let Some(c) = chars.next() {
        match c {
            b'\\' => {
                let e = chars.next()?;
                match e {
                    b'n' => out.push(b'\n'),
                    b'r' => out.push(b'\r'),
                    b't' => out.push(b'\t'),
                    b'b' => out.push(8),
                    b'f' => out.push(12),
                    b'0'..=b'7' => {
                        let mut v = u32::from(e - b'0');
                        for _ in 0..2 {
                            match chars.peek() {
                                Some(d @ b'0'..=b'7') => {
                                    v = v * 8 + u32::from(d - b'0');
                                    chars.next();
                                }
                                _ => break,
                            }
                        }
                        out.push(v as u8);
                    }
                    b'\n' => {}
                    other => out.push(other),
                }
            }
            b'(' => {
                depth += 1;
                out.push(c);
            }
            b')' => {
                depth -= 1;
                if depth == 0 {
                    return String::from_utf8(out).ok();
                }
                out.push(c);
            }
            _ => out.push(c),
        }
    }
    None
}

/// A Bézier's (homogeneous) poles raised to *degree*.
fn elevate(poles: &[[f64; 3]], degree: usize) -> Vec<[f64; 3]> {
    let mut p = poles.to_vec();
    while p.len() - 1 < degree {
        let n = p.len();
        let mut q = vec![p[0]];
        for i in 1..n {
            let a = i as f64 / n as f64;
            q.push([0, 1, 2].map(|k| a * p[i - 1][k] + (1.0 - a) * p[i][k]));
        }
        q.push(p[n - 1]);
        p = q;
    }
    p
}

/// Geometry as SVG inspection sees it: exported control-point precision,
/// polynomial rational pieces, and the canonical origin of a circle element.
pub(crate) fn inspection_curve(curve: &Curve) -> Curve {
    // Invariant: num formats an f64 as a decimal string, which Rust parses back as f64.
    let round = |p: P| p.map(|x| num(x).parse::<f64>().expect("SVG coordinates are numbers"));
    let bezier = |p: &[Vec<P>]| {
        Curve::Beziers(
            p.iter()
                .map(|p| p.iter().map(|p| round(*p)).collect())
                .collect(),
        )
    };
    fn flatten(c: &Curve, a: f64, b: f64, depth: usize, into: &mut Vec<Vec<P>>) {
        let (p, q) = (c.at(a), c.at(b));
        if depth >= 20
            || [0.25, 0.5, 0.75].iter().all(|t| {
                let x = c.at(a + t * (b - a));
                (x[0] - p[0] - t * (q[0] - p[0])).hypot(x[1] - p[1] - t * (q[1] - p[1])) <= 0.002
            })
        {
            into.push(vec![p, q]);
        } else {
            let m = (a + b) / 2.;
            flatten(c, a, m, depth + 1, into);
            flatten(c, m, b, depth + 1, into);
        }
    }
    match curve {
        Curve::Line(a, b) => Curve::Line(round(*a), round(*b)),
        Curve::Conic {
            centre,
            radii,
            t0,
            t1,
            ..
        } if (radii[0] - radii[1]).abs() <= 1e-12 * radii[0]
            && (t1 - t0).abs() >= 2. * PI - 1e-12 =>
        {
            Curve::Conic {
                centre: round(*centre),
                radii: *radii,
                x_axis: [1., 0.],
                y_axis: [0., 1.],
                t0: 0.,
                t1: 2. * PI,
            }
        }
        Curve::Conic {
            centre,
            radii,
            x_axis,
            y_axis,
            t0,
            t1,
        } => {
            // SVG endpoint arcs reconstruct their centre from the exported endpoints.
            // In a near-semicircle the six-place endpoint rounding is significant.
            let phi = x_axis[1].atan2(x_axis[0]);
            let (sin, cos) = phi.sin_cos();
            let sweep = (x_axis[0] * y_axis[1] - x_axis[1] * y_axis[0] > 0.) == (*t1 > *t0);
            let du = t1 - t0;
            let ends = if du.abs() >= 2. * PI - 1e-12 {
                vec![t0 + du / 2., *t1]
            } else {
                vec![*t1]
            };
            let mut start = round(curve.start());
            let mut previous = *t0;
            let mut lines = Vec::new();
            for t in ends {
                let end = round(Curve::conic_at(*centre, *radii, *x_axis, *y_axis, t));
                let (dx, dy) = ((start[0] - end[0]) / 2., (start[1] - end[1]) / 2.);
                let (x, y) = (cos * dx + sin * dy, -sin * dx + cos * dy);
                let (mut rx, mut ry) = (radii[0], radii[1]);
                let scale = ((x / rx).powi(2) + (y / ry).powi(2)).sqrt().max(1.);
                rx *= scale;
                ry *= scale;
                let mut k = (((rx * ry).powi(2) - (rx * y).powi(2) - (ry * x).powi(2)).max(0.)
                    / ((rx * y).powi(2) + (ry * x).powi(2)).max(1e-30))
                .sqrt();
                if ((t - previous).abs() > PI) == sweep {
                    k = -k;
                }
                let (ux, uy) = (k * rx * y / ry, -k * ry * x / rx);
                let centre = [
                    (start[0] + end[0]) / 2. + cos * ux - sin * uy,
                    (start[1] + end[1]) / 2. + sin * ux + cos * uy,
                ];
                let from = ((y - uy) / ry).atan2((x - ux) / rx);
                let to = ((-y - uy) / ry).atan2((-x - ux) / rx);
                let mut angle = (to - from).rem_euclid(2. * PI);
                if !sweep {
                    angle -= 2. * PI;
                }
                let arc = Curve::Conic {
                    centre,
                    radii: [rx, ry],
                    x_axis: [cos, sin],
                    y_axis: [-sin, cos],
                    t0: from,
                    t1: from + angle,
                };
                let n = (angle.abs() / FRAC_PI_2).ceil().max(1.) as usize;
                for i in 0..n {
                    flatten(
                        &arc,
                        i as f64 / n as f64,
                        (i + 1) as f64 / n as f64,
                        0,
                        &mut lines,
                    );
                }
                start = end;
                previous = t;
            }
            Curve::Beziers(lines)
        }
        Curve::Beziers(p) => bezier(p),
        Curve::Rational(p) => bezier(&polynomial(p)),
    }
}

/// The SVG path commands for *curve*, from its start (already reached).
fn svg_segments(curve: &Curve, d: &mut String) {
    match curve {
        Curve::Line(_, b) => {
            let _ = write!(d, " L {},{}", num(b[0]), num(b[1]));
        }
        Curve::Conic {
            centre,
            radii,
            x_axis,
            y_axis,
            t0,
            t1,
        } => {
            let ccw = x_axis[0] * y_axis[1] - x_axis[1] * y_axis[0] > 0.0;
            let sweep = ccw == (t1 > t0);
            let rotation = x_axis[1].atan2(x_axis[0]).to_degrees();
            let du = t1 - t0;
            // A whole ellipse, or more than half, is drawn as two arcs.
            let mids: Vec<f64> = if du.abs() >= 2.0 * PI - 1e-12 {
                vec![t0 + du / 2.0, *t1]
            } else {
                vec![*t1]
            };
            let mut from = *t0;
            for to in mids {
                let e = Curve::conic_at(*centre, *radii, *x_axis, *y_axis, to);
                let large = (to - from).abs() > PI;
                // Radii and rotation in full, as build123d writes them: an arc nearly a
                // diameter long puts its centre wherever a rounded radius sends it.
                let _ = write!(
                    d,
                    " A {},{} {} {},{} {},{}",
                    radii[0],
                    radii[1],
                    rotation,
                    u8::from(large),
                    u8::from(sweep),
                    num(e[0]),
                    num(e[1])
                );
                from = to;
            }
        }
        Curve::Rational(pieces) => svg_segments(&Curve::Beziers(polynomial(pieces)), d),
        Curve::Beziers(pieces) => {
            for p in pieces {
                let pts: Vec<String> = p[1..]
                    .iter()
                    .map(|q| format!("{},{}", num(q[0]), num(q[1])))
                    .collect();
                let cmd = match p.len() {
                    2 => "L",
                    3 => "Q",
                    n => {
                        // Invariant: Beziers are line/quadratic/cubic pieces; higher-degree NURBS pass through polynomial first.
                        assert_eq!(n, 4, "a Bézier piece of {n} poles (at most 4 are drawn)");
                        "C"
                    }
                };
                let _ = write!(d, " {cmd} {}", pts.join(" "));
            }
        }
    }
}

fn svg_item(item: &Item) -> String {
    match item {
        Item::Edge(Curve::Line(a, b)) => {
            format!(
                "<line x1=\"{}\" y1=\"{}\" x2=\"{}\" y2=\"{}\" />",
                num(a[0]),
                num(a[1]),
                num(b[0]),
                num(b[1])
            )
        }
        Item::Edge(Curve::Conic {
            centre,
            radii,
            t0,
            t1,
            ..
        }) if (radii[0] - radii[1]).abs() <= 1e-12 * radii[0]
            && (t1 - t0).abs() >= 2.0 * PI - 1e-12 =>
        {
            format!(
                "<circle cx=\"{}\" cy=\"{}\" r=\"{}\" fill=\"none\" />",
                num(centre[0]),
                num(centre[1]),
                radii[0]
            )
        }
        Item::Edge(curve) => {
            let s = curve.start();
            let mut d = format!("M {},{}", num(s[0]), num(s[1]));
            svg_segments(curve, &mut d);
            format!("<path d=\"{d}\" fill=\"none\" />")
        }
        Item::Face(wires) => {
            let mut d = String::new();
            for wire in wires {
                let s = wire[0].start();
                let _ = write!(
                    d,
                    "{}M {},{}",
                    if d.is_empty() { "" } else { " " },
                    num(s[0]),
                    num(s[1])
                );
                for curve in wire {
                    svg_segments(curve, &mut d);
                }
            }
            format!("<path d=\"{d}\" />")
        }
    }
}

/// A path segment from the point already reached: a line to a point, or a cubic by its two
/// control points and its end.
pub(crate) enum PathSeg {
    Line(P),
    Cubic(P, P, P),
}

/// *curve*, from its start, as lines and cubics, as the PDF and the PNG draw it: arcs as cubics
/// of at most a quarter turn each, quadratics raised to cubics, rational pieces as
/// [`polynomial`] draws them.
pub(crate) fn path_segments(curve: &Curve) -> Vec<PathSeg> {
    match curve {
        Curve::Line(_, b) => vec![PathSeg::Line(*b)],
        Curve::Conic {
            centre,
            radii,
            x_axis,
            y_axis,
            t0,
            t1,
        } => {
            let n = ((t1 - t0).abs() / FRAC_PI_2).ceil().max(1.0) as usize;
            let dt = (t1 - t0) / n as f64;
            let alpha = 4.0 / 3.0 * (dt / 4.0).tan();
            let at = |t: f64| Curve::conic_at(*centre, *radii, *x_axis, *y_axis, t);
            let tangent = |t: f64| {
                let (s, co) = (-t.sin() * radii[0], t.cos() * radii[1]);
                [
                    s * x_axis[0] + co * y_axis[0],
                    s * x_axis[1] + co * y_axis[1],
                ]
            };
            (0..n)
                .map(|i| {
                    let (a, b) = (t0 + dt * i as f64, t0 + dt * (i + 1) as f64);
                    let (pa, pb, ta, tb) = (at(a), at(b), tangent(a), tangent(b));
                    let c1 = [pa[0] + alpha * ta[0], pa[1] + alpha * ta[1]];
                    let c2 = [pb[0] - alpha * tb[0], pb[1] - alpha * tb[1]];
                    PathSeg::Cubic(c1, c2, pb)
                })
                .collect()
        }
        Curve::Rational(pieces) => path_segments(&Curve::Beziers(polynomial(pieces))),
        Curve::Beziers(pieces) => pieces
            .iter()
            .map(|p| match p.len() {
                2 => PathSeg::Line(p[1]),
                3 => {
                    let c1 = [
                        p[0][0] + 2.0 / 3.0 * (p[1][0] - p[0][0]),
                        p[0][1] + 2.0 / 3.0 * (p[1][1] - p[0][1]),
                    ];
                    let c2 = [
                        p[2][0] + 2.0 / 3.0 * (p[1][0] - p[2][0]),
                        p[2][1] + 2.0 / 3.0 * (p[1][1] - p[2][1]),
                    ];
                    PathSeg::Cubic(c1, c2, p[2])
                }
                n => {
                    // Invariant: Beziers are line/quadratic/cubic pieces; higher-degree NURBS pass through polynomial first.
                    assert_eq!(n, 4, "a Bézier piece of {n} poles (at most 4 are drawn)");
                    PathSeg::Cubic(p[1], p[2], p[3])
                }
            })
            .collect(),
    }
}

/// The PDF path for *curve*, from its start (already reached).
fn pdf_curve(c: &mut pdf_writer::Content, curve: &Curve) {
    let f = |p: P| (p[0] as f32, p[1] as f32);
    for seg in path_segments(curve) {
        match seg {
            PathSeg::Line(b) => {
                c.line_to(b[0] as f32, b[1] as f32);
            }
            PathSeg::Cubic(c1, c2, b) => {
                let ((x1, y1), (x2, y2), (x3, y3)) = (f(c1), f(c2), f(b));
                c.cubic_to(x1, y1, x2, y2, x3, y3);
            }
        }
    }
}

// #32: the ink geometry of a linear dimension, shared by the layout (`dims`) and the structural
// lint, which may read only the rendered drawing (`lint::guard`).

/// #32: two parallel dimension lines closer than this, mm, read as one line.
const SAME_LINE: f64 = 0.5;
/// #32: a linear label further than this from its own dimension line, mm (one text height),
/// no longer reads as that line's. The layout keeps it within `dims::LIFT` across the line and,
/// beyond a short span's end, 1 mm past the line run on to it. Equal to the dimension text
/// height (`dims::TEXT`).
pub(crate) const STRAY_TEXT: f64 = 3.0;

/// A linear dimension read from its ink alone, in the layout's (and the drawing's) order: the two
/// extension lines across, then the dimension line. The page axis the line runs along (0: x),
/// its offset across, the span between the extension lines and the line's drawn extent.
pub(crate) struct LinearInk {
    pub(crate) k: usize,
    pub(crate) offset: f64,
    pub(crate) span: [f64; 2],
    pub(crate) extent: [f64; 2],
}

pub(crate) fn linear_ink(lines: &[[P; 2]]) -> Option<LinearInk> {
    let [e0, e1, d, decoration @ ..] = lines else {
        return None;
    };
    let k = (0..2)
        .find(|&k| (d[0][1 - k] - d[1][1 - k]).abs() < 1e-6 && (d[0][k] - d[1][k]).abs() > 1e-6)?;
    // A witness may be oblique. Its intersection with the dimension line is
    // the measured station; its overshooting endpoint is not an arrow tip.
    let station = |e: &[P; 2]| {
        let run = e[1][1 - k] - e[0][1 - k];
        (run.abs() > 1e-9)
            .then(|| e[0][k] + (d[0][1 - k] - e[0][1 - k]) * (e[1][k] - e[0][k]) / run)
    };
    let (a, b) = (station(e0)?, station(e1)?);
    if (a - b).abs() < 1e-6 {
        return None;
    }
    // Running ordinates add a closed 0.7 mm origin ring at the first witness.
    // Recognise that actual ink; arbitrary trailing strokes remain non-linear.
    if !decoration.is_empty() {
        let mut origin = d[0];
        origin[k] = a;
        if decoration.len() != 16
            || decoration.iter().enumerate().any(|(i, line)| {
                let next = decoration[(i + 1) % decoration.len()][0];
                (0..2).any(|j| (line[1][j] - next[j]).abs() > 1e-6)
                    || line
                        .iter()
                        .any(|p| ((p[0] - origin[0]).hypot(p[1] - origin[1]) - 0.7).abs() > 1e-6)
            })
        {
            return None;
        }
    }
    Some(LinearInk {
        k,
        offset: d[0][1 - k],
        span: [a.min(b), a.max(b)],
        extent: [d[0][k].min(d[1][k]), d[0][k].max(d[1][k])],
    })
}

/// #32: whether two linear dimensions (by their lines) are drawn on one dimension line over
/// overlapping spans, so their arrows and extension lines run into each other. Chained
/// dimensions meeting at one extension line do not overlap.
pub(crate) fn shared_dimension_line(a: &[[P; 2]], b: &[[P; 2]]) -> bool {
    let (Some(p), Some(q)) = (linear_ink(a), linear_ink(b)) else {
        return false;
    };
    p.k == q.k
        && (p.offset - q.offset).abs() < SAME_LINE
        && p.span[1].min(q.span[1]) - p.span[0].max(q.span[0]) > SAME_LINE
}

/// #32: how far a linear dimension's label box lies from its own dimension line, mm (0 on it or
/// touching it); none when *lines* are not a linear dimension's.
pub(crate) fn label_gap(lines: &[[P; 2]], label: [f64; 4]) -> Option<f64> {
    let l = linear_ink(lines)?;
    let (k, c) = (l.k, 1 - l.k);
    let along = (l.extent[0] - label[k + 2])
        .max(label[k] - l.extent[1])
        .max(0.0);
    let across = (label[c] - l.offset).max(l.offset - label[c + 2]).max(0.0);
    Some(along.hypot(across))
}

#[cfg(test)]
mod tests {
    use super::*;
    use haecceity::hlr::Exact;

    #[test]
    fn malformed_projected_splines_are_reported_as_undrawn() {
        for (degree, knots, poles, weights) in [
            (3, vec![], vec![], vec![]),
            (usize::MAX, vec![0., 1.], vec![[0., 0.]; 2], vec![1.; 2]),
            (1, vec![0., 0., 1., 1.], vec![[0., 0.]; 2], vec![1.]),
            (1, vec![1., 0., 1., 1.], vec![[0., 0.]; 2], vec![1.; 2]),
            (1, vec![0., 0., 1., 1.], vec![[0., 0.]; 2], vec![0.; 2]),
        ] {
            let c = Curve::from(&Exact::Nurbs {
                degree,
                knots,
                poles,
                weights,
                t0: 0.,
                t1: 1.,
            });
            assert!(matches!(c, Curve::Rational(p) if p.is_empty()));
        }
        let problems = undrawn(&BTreeMap::from([("v:front".into(), 1)]));
        assert_eq!(problems[0].code, "edges_not_drawn");
    }

    #[test]
    fn oblique_witnesses_still_reserve_their_measured_span() {
        let oblique = [
            [[0., 0.], [5.5, 11.]],
            [[10., 0.], [15.5, 11.]],
            [[5., 10.], [15., 10.]],
        ];
        let ink = linear_ink(&oblique).unwrap();
        assert_eq!(ink.span, [5., 15.]);
        let crossing = [
            [[12., 0.], [12., 12.]],
            [[18., 0.], [18., 12.]],
            [[12., 10.], [18., 10.]],
        ];
        assert!(shared_dimension_line(&oblique, &crossing));
        let beside = [
            [[16., 0.], [16., 12.]],
            [[20., 0.], [20., 12.]],
            [[16., 10.], [20., 10.]],
        ];
        assert!(!shared_dimension_line(&oblique, &beside));
    }

    /// The pieces' points, densely.
    fn trace(pieces: &[Vec<[f64; 3]>]) -> Vec<P> {
        pieces
            .iter()
            .flat_map(|p| (0..=1024).map(|k| rational_at(p, k as f64 / 1024.0)))
            .collect()
    }

    fn check(exact: Exact) {
        check_at(&exact, |t| exact.at(t), 1e-12);
    }

    /// The pieces of *exact* start and end (within *ends*) and run where *at* puts the stretch.
    fn check_at(exact: &Exact, at: impl Fn(f64) -> P, ends: f64) {
        let Curve::Rational(pieces) = Curve::from(exact) else {
            panic!("a NURBS stretch is rational")
        };
        let (t0, t1) = exact.range();
        let gap = |a: P, b: P| (a[0] - b[0]).hypot(a[1] - b[1]);
        assert!(
            gap(point(pieces[0][0]), at(t0)) < ends,
            "starts where the stretch does"
        );
        assert!(
            gap(point(*pieces.last().unwrap().last().unwrap()), at(t1)) < ends,
            "ends where it does"
        );
        let traced = trace(&pieces);
        let to_segment = |q: P, a: P, b: P| {
            let (dx, dy) = (b[0] - a[0], b[1] - a[1]);
            let len2 = dx * dx + dy * dy;
            let t = if len2 == 0.0 {
                0.0
            } else {
                (((q[0] - a[0]) * dx + (q[1] - a[1]) * dy) / len2).clamp(0.0, 1.0)
            };
            gap(q, [a[0] + t * dx, a[1] + t * dy])
        };
        for k in 0..=200 {
            let q = at(t0 + (t1 - t0) * k as f64 / 200.0);
            let near = traced
                .windows(2)
                .map(|w| to_segment(q, w[0], w[1]))
                .fold(f64::INFINITY, f64::min);
            // Traced at 1024 points a piece, their chords stand within ~1e-6 (shrinking as the
            // square of the spacing, as a sagitta does).
            assert!(
                near < 1e-5,
                "point {k} of the stretch {near:e} from the pieces"
            );
        }
    }

    #[test]
    fn a_clamped_cubic_cut_to_a_stretch_either_way() {
        let nurbs = |t0, t1| Exact::Nurbs {
            degree: 3,
            poles: vec![
                [0.0, 0.0],
                [1.0, 2.0],
                [2.0, -1.0],
                [3.0, 3.0],
                [4.0, 0.0],
                [5.0, 1.0],
            ],
            weights: vec![1.0; 6],
            knots: vec![0.0, 0.0, 0.0, 0.0, 0.7, 1.4, 2.0, 2.0, 2.0, 2.0],
            t0,
            t1,
        };
        check(nurbs(0.3, 1.7));
        check(nurbs(1.7, 0.3));
        check(nurbs(0.0, 2.0));
    }

    #[test]
    fn a_rational_circle_stays_round() {
        let h = std::f64::consts::FRAC_1_SQRT_2;
        let circle = Exact::Nurbs {
            degree: 2,
            poles: vec![
                [1.0, 0.0],
                [1.0, 1.0],
                [0.0, 1.0],
                [-1.0, 1.0],
                [-1.0, 0.0],
                [-1.0, -1.0],
                [0.0, -1.0],
                [1.0, -1.0],
                [1.0, 0.0],
            ],
            weights: vec![1.0, h, 1.0, h, 1.0, h, 1.0, h, 1.0],
            knots: vec![0.0, 0.0, 0.0, 1.0, 1.0, 2.0, 2.0, 3.0, 3.0, 4.0, 4.0, 4.0],
            t0: 0.5,
            t1: 3.5,
        };
        check(circle.clone());
        let Curve::Rational(pieces) = Curve::from(&circle) else {
            unreachable!()
        };
        for p in trace(&pieces) {
            assert!(
                (p[0].hypot(p[1]) - 1.0).abs() < 1e-12,
                "{p:?} off the circle"
            );
        }
    }

    #[test]
    fn an_unclamped_cubic_cut_inside_its_domain() {
        check(Exact::Nurbs {
            degree: 3,
            poles: vec![
                [0.0, 0.0],
                [1.0, 1.0],
                [2.0, 0.0],
                [3.0, 1.0],
                [4.0, 0.0],
                [5.0, 1.0],
                [6.0, 0.0],
            ],
            weights: vec![1.0; 7],
            knots: (0..11).map(f64::from).collect(),
            t0: 3.5,
            t1: 6.5,
        });
    }

    /// A hidden stretch of a clamped cubic from t = 0 to t = 0, as cgb242's front view had
    /// until haecceity stopped making stretches that short (quiddity-rust 56a5c4f): a guard
    /// now, not drawn, and reported.
    #[test]
    fn a_stretch_of_no_span_is_not_drawn() {
        let poles = vec![
            [0.0, 0.0, 1.0],
            [1.0, 2.0, 1.0],
            [2.0, -1.0, 1.0],
            [3.0, 0.0, 1.0],
        ];
        let knots = vec![0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0];
        for t in [0.0, 0.4, 1.0] {
            assert!(bezier_pieces(3, knots.clone(), poles.clone(), t, t).is_empty());
        }
        let piece = |exact, points| haecceity::hlr::Projected {
            class: haecceity::hlr::Class::Sharp,
            visible: false,
            points,
            exact,
        };
        let nurbs = Exact::Nurbs {
            degree: 3,
            poles: poles.iter().map(|q| [q[0], q[1]]).collect(),
            weights: vec![1.0; 4],
            knots,
            t0: 0.0,
            t1: 0.0,
        };
        assert_eq!(projected(&piece(Some(nurbs), vec![[0.0, 0.0]; 2])), None);
        assert_eq!(projected(&piece(None, vec![[1.0, 1.0]])), None);
        assert_eq!(
            projected(&piece(None, vec![[1.0, 1.0], [2.0, 1.0]])),
            Some(Curve::Beziers(vec![vec![[1.0, 1.0], [2.0, 1.0]]]))
        );
        let reported = undrawn(&BTreeMap::from([("v:front".to_string(), 1)]));
        assert_eq!(reported.len(), 1);
        assert_eq!(reported[0].id, "p:edges_not_drawn:v:front");
        assert_eq!(reported[0].subjects, ["v:front"]);
        assert!(
            reported[0]
                .message
                .starts_with("1 projected edge piece in the front view had no length to draw")
        );
    }

    /// A degree-5 polynomial piece is drawn by chords: SVG commands of the right arity, and a
    /// PDF path that ends where the curve does.
    #[test]
    fn a_quintic_is_drawn_whole() {
        let exact = Exact::Nurbs {
            degree: 5,
            poles: vec![
                [0.0, 0.0],
                [1.0, 3.0],
                [2.0, -2.0],
                [3.0, 4.0],
                [4.0, -1.0],
                [5.0, 2.0],
            ],
            weights: vec![1.0; 6],
            knots: [[0.0; 6], [1.0; 6]].concat(),
            t0: 0.0,
            t1: 1.0,
        };
        let curve = Curve::from(&exact);
        let svg = svg_item(&Item::Edge(curve.clone()));
        let d = svg.split("d=\"").nth(1).unwrap().split('"').next().unwrap();
        let tokens: Vec<&str> = d.split([' ', ',']).filter(|t| !t.is_empty()).collect();
        let mut i = 0;
        while i < tokens.len() {
            let arity = match tokens[i] {
                "M" | "L" => 2,
                "Q" => 4,
                "C" => 6,
                other => panic!("unexpected command {other:?} in {d}"),
            };
            let args = tokens[i + 1..]
                .iter()
                .take_while(|t| t.parse::<f64>().is_ok())
                .count();
            assert_eq!(args, arity, "{} in {d}", tokens[i]);
            i += 1 + args;
        }
        let end = exact.at(1.0);
        let last: Vec<f64> = tokens[tokens.len() - 2..]
            .iter()
            .map(|t| t.parse().unwrap())
            .collect();
        assert!(
            (last[0] - end[0]).hypot(last[1] - end[1]) < 1e-6,
            "{last:?}"
        );

        let mut c = pdf_writer::Content::new();
        c.move_to(0.0, 0.0);
        pdf_curve(&mut c, &curve);
        let ops = String::from_utf8(c.finish().to_vec()).unwrap();
        let last_op = ops.lines().last().unwrap();
        let xy: Vec<f64> = last_op
            .split(' ')
            .rev()
            .skip(1)
            .take(2)
            .map(|t| t.parse().unwrap())
            .collect();
        let (x, y) = (xy[1], xy[0]);
        assert!(
            (x - end[0]).abs() < 1e-5 && (y - end[1]).abs() < 1e-5,
            "the PDF path ends at {last_op:?}, not {end:?}"
        );
    }

    /// nist_stc_10's closed periodic cubic (a top view's projected edge, placed on the sheet) as
    /// haecceity gave it: unclamped uniform knots, its domain [0, 0.00788…] one period, and the
    /// stretch unwrapped to the second period, t0 a rounding below the domain's end and t1 two
    /// periods. `bezier_pieces` took the span index past the poles and panicked.
    fn stc10(t0: f64, t1: f64) -> Exact {
        Exact::Nurbs {
            degree: 3,
            poles: vec![
                [79.979556728642, 352.61152944204383],
                [79.0001392174733, 352.4442335451668],
                [78.01988640129849, 352.6116638110308],
                [77.6148149557302, 352.7904870243704],
                [78.020855682099, 352.6114695503978],
                [78.99909425782201, 352.44427128241233],
                [79.9800207266354, 352.6115729333044],
                [80.38510562634539, 352.7904949493546],
                [79.979556728642, 352.61152944204383],
                [79.0001392174733, 352.4442335451668],
                [78.01988640129849, 352.6116638110308],
            ],
            weights: vec![1.0; 11],
            knots: vec![
                -0.00295532488714019,
                -0.0019702165914268,
                -0.000985108295713398,
                0.0,
                0.000985108295713398,
                0.0019702165914268,
                0.00295532488714019,
                0.00394043318285359,
                0.00492554147856699,
                0.00591064977428039,
                0.00689575806999378,
                0.00788086636570718,
                0.00886597466142058,
                0.00985108295713398,
                0.0108361912528474,
            ],
            t0,
            t1,
        }
    }

    /// The pieces of a closed stretch run past its domain trace the curve at the parameters
    /// wrapped into the domain, from the wrapped t0 to the wrapped t1.
    fn check_wrapped(exact: Exact, period: f64) {
        check_at(&exact, |t| exact.at(t.rem_euclid(period)), 1e-9);
    }

    #[test]
    fn nist_stc_10s_periodic_cubic_in_its_second_period() {
        let period = 0.00788086636570718;
        let exact = stc10(0.007880866365707166, 0.015761732731414346);
        check_wrapped(exact.clone(), period);
        let Curve::Rational(pieces) = Curve::from(&exact) else {
            unreachable!()
        };
        // Drawn as the domain is, once: one piece a knot span, none of no length at the seam.
        let Curve::Rational(domain) = Curve::from(&stc10(0.0, period)) else {
            unreachable!()
        };
        assert_eq!(pieces.len(), 8);
        assert_eq!(domain.len(), 8);
        for (p, q) in pieces.iter().flatten().zip(domain.iter().flatten()) {
            assert!((0..3).all(|k| (p[k] - q[k]).abs() < 1e-9), "{p:?} != {q:?}");
        }
        check_wrapped(stc10(0.015761732731414346, 0.007880866365707166), period);
        // Across the seam, either way, and in a period below the domain.
        check_wrapped(stc10(0.5 * period, 1.5 * period), period);
        check_wrapped(stc10(1.5 * period, 0.5 * period), period);
        check_wrapped(stc10(-0.75 * period, -0.25 * period), period);
        check_wrapped(stc10(-0.25 * period, 2.25 * period), period);
    }

    /// A stretch past the knots of a curve that is not closed has no pieces (left out and
    /// reported by `undrawn`), and a rounding past its ends is clamped to them.
    #[test]
    fn a_stretch_off_an_open_curve_is_not_drawn() {
        let poles = vec![[0.0, 0.0], [1.0, 2.0], [2.0, -1.0], [3.0, 0.0]];
        let nurbs = |t0, t1| Exact::Nurbs {
            degree: 3,
            poles: poles.clone(),
            weights: vec![1.0; 4],
            knots: vec![0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0],
            t0,
            t1,
        };
        assert_eq!(Curve::from(&nurbs(0.5, 1.5)), Curve::Rational(Vec::new()));
        assert_eq!(Curve::from(&nurbs(-0.5, 0.5)), Curve::Rational(Vec::new()));
        assert_eq!(
            Curve::from(&nurbs(f64::NAN, 0.5)),
            Curve::Rational(Vec::new())
        );
        assert_eq!(
            Curve::from(&nurbs(-1e-17, 1.0 + 1e-16)),
            Curve::from(&nurbs(0.0, 1.0))
        );
    }
}
