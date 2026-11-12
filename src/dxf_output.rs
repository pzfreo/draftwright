//! Semantic DXF writer. The crate's curve/spline model is reused, but this small ASCII
//! writer supplies block records, ownership, groups and HATCH (unsupported by dxf 0.6).
//! All handles are allocated in drawing order; no clocks or random identifiers are used.
use std::collections::{BTreeMap, BTreeSet};
use std::fmt::{Display, Write};
use std::ops::Range;

use crate::dims::PlannedDimension;
use crate::output::{Curve, Drawing, HAlign, Item, P, TextRun, VAlign};

#[derive(Clone, Debug, Default, PartialEq)]
pub struct ViewItems {
    pub name: String,
    pub at: P,
    pub visible: Range<usize>,
    pub hidden: Range<usize>,
    pub centres: Range<usize>,
    pub thin: Range<usize>,
}
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Structure {
    pub sheet_number: u32,
    pub hatches: Vec<HatchRegion>,
    pub cutting: Vec<CuttingPlane>,
    pub views: Vec<ViewItems>,
    pub annotations: BTreeMap<String, Range<usize>>,
    pub furniture: BTreeMap<String, Range<usize>>,
    pub furniture_views: BTreeMap<String, String>,
    pub dimensions: BTreeMap<String, Dimension>,
    pub frames: BTreeMap<String, Frame>,
    pub title: Range<usize>,
    pub labels: Vec<TextRun>,
    pub title_fields: Vec<(String, TextRun)>,
}
#[derive(Clone, Debug, Default, PartialEq)]
pub struct CuttingPlane {
    pub name: String,
    pub view: String,
    pub items: Range<usize>,
}
/// A feature control frame, written as one TOLERANCE entity ([`crate::dims::gdt::frame`]).
#[derive(Clone, Debug, PartialEq)]
pub struct Frame {
    /// The compartments' text, as the plan states them.
    pub cells: Vec<String>,
    /// The box's left edge at mid height.
    pub at: P,
    /// Offsets of the annotation's items that are the frame's own ink, drawn by the entity.
    pub ink: Vec<Range<usize>>,
    /// How many of the annotation's first runs are not compartments (the count over the box).
    pub lead: usize,
}
/// Only the plan fields needed by DXF: avoid cloning planning explanations,
/// claims and placement history on the SVG/PDF/PNG path.
#[derive(Clone, Debug, PartialEq)]
pub struct Dimension {
    pub kind: String,
    pub view: String,
    pub value: f64,
    pub label: String,
    pub placed: Option<crate::dims::Placed>,
    pub diameter: bool,
    pub radial: bool,
    /// Byte range of the displayed primary number, excluding count/prefix/units.
    pub primary: Option<Range<usize>>,
    /// The same range when the label prints the value in inches.
    pub primary_in: Option<Range<usize>>,
}
impl From<&PlannedDimension> for Dimension {
    fn from(d: &PlannedDimension) -> Self {
        let diameter = d.label.contains(['⌀', 'ø', 'Ø'])
            && d.measurements
                .iter()
                .any(|m| m.parameter.contains(".diameter") && (m.value - d.value).abs() < 1e-5);
        let radial = d.terms.iter().any(|t| {
            t.trim_start_matches('(')
                .strip_prefix('R')
                .is_some_and(|n| n.chars().next().is_some_and(|c| c.is_ascii_digit()))
        }) && d
            .measurements
            .iter()
            .any(|m| m.parameter.contains(".radius") && (m.value - d.value).abs() < 1e-5);
        Self {
            kind: d.kind.clone(),
            view: d.view.clone(),
            value: d.value,
            label: d.label.clone(),
            placed: d.placed.clone(),
            diameter,
            radial,
            primary: primary_range(d, 1.),
            primary_in: primary_range(d, 1. / 25.4),
        }
    }
}

fn number_range(s: &str) -> Option<Range<usize>> {
    let after_count = s.find('×').map_or(0, |at| {
        let prefix = s[..at].trim().trim_start_matches('(');
        if !prefix.is_empty() && prefix.chars().all(|c| c.is_ascii_digit()) {
            at + '×'.len_utf8()
        } else {
            0
        }
    });
    // Inch values drop the leading zero (.197), so a point before a digit starts one too.
    let tail = &s[after_count..];
    let start = after_count
        + tail
            .char_indices()
            .find(|&(i, c)| {
                c.is_ascii_digit()
                    || matches!(c, '-' | '+')
                    || (c == '.' && tail[i + 1..].starts_with(|n: char| n.is_ascii_digit()))
            })?
            .0;
    let end = s[start..]
        .find(|c: char| !c.is_ascii_digit() && !matches!(c, '.' | '-' | '+'))
        .map_or(s.len(), |n| start + n);
    Some(start..end)
}
fn primary_range(d: &PlannedDimension, scale: f64) -> Option<Range<usize>> {
    for term in &d.terms {
        if term.ends_with('×') || term.starts_with('±') {
            continue;
        }
        let Some(n) = number_range(term) else {
            continue;
        };
        let Ok(value) = term[n.clone()].parse::<f64>() else {
            continue;
        };
        if !printed_value_matches(d.value * scale, value) {
            continue;
        }
        if let Some(at) = d.label.find(term) {
            return Some(at + n.start..at + n.end);
        }
    }
    None
}
fn printed_value_matches(nominal: f64, printed: f64) -> bool {
    (printed - nominal).abs() <= 0.000501
        // A Spec can request fewer than the default three decimal places.
        // Rounding the displayed number must not turn it into a fixed override.
        || (0..=2).any(|precision| {
            let factor = 10_f64.powi(precision);
            let value = nominal * factor;
            (printed - value.round() / factor).abs() < 1e-9
                || (printed - value.round_ties_even() / factor).abs() < 1e-9
        })
}
#[derive(Clone, Copy, Debug)]
struct DimStyle {
    font: &'static str,
    factor: f64,
    precision: u8,
    fixed: bool,
    leader: bool,
    inches: bool,
    /// The style of a feature control frame: the text is the run's size and DIMGAP leaves the box
    /// twice that tall, as the layout draws it.
    frame: bool,
}
impl Default for DimStyle {
    fn default() -> Self {
        Self {
            font: "IBMPlexMono-Regular.ttf",
            factor: 1.,
            precision: 3,
            fixed: false,
            leader: false,
            inches: false,
            frame: false,
        }
    }
}
impl DimStyle {
    fn name(self) -> String {
        let mut name = if self.factor >= 1. {
            format!("DW-1to{}", self.factor)
        } else {
            format!("DW-{}to1", 1. / self.factor)
        };
        if self.fixed {
            write!(name, "-P{}", self.precision).unwrap();
        }
        if self.inches {
            name.push_str("-IN");
        }
        if self.leader {
            name.push_str("-LEADER");
        }
        if self.frame {
            name.push_str("-FCF");
        }
        name
    }
    /// Keep HEADER defaults, named styles and per-entity DSTYLE overrides in
    /// agreement. QCAD's dxflib importer regenerates dimensions, ignores *D
    /// blocks and named dimstyles, and reads DIMLFAC from the entity's XDATA.
    fn variables(self) -> Vec<(&'static str, i32, f64)> {
        let mut vars = vec![
            ("DIMSCALE", 40, 1.),
            ("DIMASZ", 41, crate::dims::ARROW),
            ("DIMEXO", 42, crate::dims::GAP),
            ("DIMEXE", 44, 1.),
            (
                "DIMTXT",
                140,
                crate::dims::TEXT * if self.frame { 1. } else { 0.698 },
            ),
            // Geometry is in millimetres; inch sheets measure in inches.
            (
                "DIMLFAC",
                144,
                if self.inches {
                    self.factor / 25.4
                } else {
                    self.factor
                },
            ),
            (
                "DIMGAP",
                147,
                if self.frame {
                    0.5 * crate::dims::TEXT
                } else {
                    0.8
                },
            ),
            ("DIMDEC", 271, self.precision.into()),
            ("DIMADEC", 179, self.precision.into()),
            // Inch values drop the leading zero and keep trailing ones (.312, 1.000).
            (
                "DIMZIN",
                78,
                match (self.inches, self.fixed) {
                    (true, _) => 4.,
                    (false, true) => 0.,
                    (false, false) => 8.,
                },
            ),
            ("DIMAZIN", 79, if self.fixed { 0. } else { 2. }),
            ("DIMLUNIT", 277, 2.),
            ("DIMDSEP", 278, 46.),
            ("DIMAUNIT", 275, 0.),
            ("DIMJUST", 280, 0.),
            ("DIMATFIT", 289, 3.),
            ("DIMTIH", 73, 1.),
            ("DIMTOH", 74, 1.),
            ("DIMTAD", 77, 0.),
            ("DIMTOFL", 172, 0.),
            ("DIMTIX", 174, 0.),
            ("DIMTMOVE", 279, if self.leader { 1. } else { 2. }),
            ("DIMCEN", 141, 0.),
            ("DIMTVP", 145, 0.),
            ("DIMTOL", 71, 0.),
            ("DIMLIM", 72, 0.),
            ("DIMTP", 47, 0.),
            ("DIMTM", 48, 0.),
            ("DIMTFAC", 146, 1.),
            ("DIMTDEC", 272, 6.),
            ("DIMTZIN", 284, 8.),
        ];
        // The numeric DXF codes determine both HEADER and XDATA value types.
        vars.sort_by_key(|v| v.1);
        vars
    }
}
fn dimension_integer(code: i32) -> bool {
    (70..=79).contains(&code) || code >= 170
}
/// The DIMENSION text override: the measured value stays live (`<>`) while
/// prefixes, tolerances and notes stay in the text itself. QCAD regenerates
/// dimensions from per-entity data and ignores DIMTOL/DIMTP/DIMTM and the
/// automatic diameter/radius prefix of named styles, so only inline text is
/// shown by every reader. (In AutoCAD a regenerated radial/diametric `<>` adds
/// its own R/diameter sign; the stored *D block shows the intended text.)
fn live_label(d: &Dimension, kind: i32, inches: bool) -> String {
    // Angles are degrees on every sheet; only lengths are printed in inches.
    let primary = if inches && kind != 5 {
        &d.primary_in
    } else {
        &d.primary
    };
    let Some(primary) = primary else {
        return override_text(&d.label);
    };
    let mut label = d.label.clone();
    label.replace_range(primary.clone(), "<>");
    if kind == 5 {
        label = label.replace("<>°", "<>");
    }
    override_text(&label)
}
fn override_text(label: &str) -> String {
    encode_mtext(&asme_words(label))
}
/// U+21A7 and the other hole glyphs, and the circled modifiers, have no outline in the fonts QCAD and
/// AutoCAD substitute, so they print as `?` in dimensions, callouts and hole
/// tables alike. Use the ASME Y14.5 words instead.
fn asme_words(text: &str) -> String {
    text.replace('↧', "DEEP")
        .replace('⌴', "CBORE")
        .replace('⌵', "CSK")
        // Circled modifiers have no glyph in SHX fonts; ASME's plain-text form is (M).
        .replace('Ⓜ', "(M)")
        .replace('Ⓛ', "(L)")
        .replace('Ⓕ', "(F)")
        .replace('Ⓟ', "(P)")
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct HatchRegion {
    pub view: String,
    pub clip: Option<[f64; 4]>,
    pub items: Range<usize>,
    pub loops: Vec<Vec<P>>,
}
const VISIBLE: &str = "A-OUTL";
const HIDDEN: &str = "A-HIDN";
const CENTRE: &str = "A-CNTR";
const DIMS: &str = "A-DIMS";
const TEXT: &str = "A-TEXT";
const PMI: &str = "A-PMI";
const HATCH: &str = "A-HATCH";
const CUT: &str = "A-CUTP";
const BORDER: &str = "A-BORD";
const LAYERS: [(&str, i32, &str); 10] = [
    (VISIBLE, 50, "CONTINUOUS"),
    (HIDDEN, 25, "ISO_DASH"),
    (CENTRE, 15, "ISO_CHAIN"),
    (DIMS, 5, "CONTINUOUS"),
    (TEXT, 5, "CONTINUOUS"),
    (PMI, 5, "CONTINUOUS"),
    (HATCH, 5, "CONTINUOUS"),
    (CUT, 5, "ISO_CHAIN"),
    (BORDER, 50, "CONTINUOUS"),
    ("Defpoints", 0, "CONTINUOUS"),
];
#[derive(Default)]
struct Tags(String);
impl Tags {
    fn tag(&mut self, code: i32, value: impl Display) {
        writeln!(self.0, "{code:3}\n{value}").expect("string write");
    }
    fn point(&mut self, code: i32, p: P) {
        self.tag(code, p[0]);
        self.tag(code + 10, p[1]);
        self.tag(code + 20, 0);
    }
    fn handle(&mut self, code: i32, h: u64) {
        self.tag(code, format_args!("{h:X}"));
    }
}
struct Block {
    name: String,
    record: u64,
    begin: u64,
    end: u64,
    at: P,
    entities: Tags,
}
struct Writer {
    next: u64,
    model: u64,
    blocks: Vec<Block>,
    entities: Tags,
    groups: Vec<(String, Vec<u64>)>,
    weight: Option<i16>,
    colour: Option<u32>,
    continuous: bool,
}
impl Writer {
    fn new() -> Self {
        Self {
            next: 0x100,
            model: 0,
            blocks: Vec::new(),
            entities: Tags::default(),
            groups: Vec::new(),
            weight: None,
            colour: None,
            continuous: false,
        }
    }
    fn id(&mut self) -> u64 {
        let h = self.next;
        self.next += 1;
        h
    }
    fn block(&mut self, name: String, at: P) -> usize {
        let record = self.id();
        let begin = self.id();
        let end = self.id();
        self.blocks.push(Block {
            name,
            record,
            begin,
            end,
            at,
            entities: Tags::default(),
        });
        self.blocks.len() - 1
    }
    fn entity(&mut self, kind: &str, layer: &str, block: Option<usize>) -> (u64, Tags) {
        let h = self.id();
        let mut t = Tags::default();
        t.tag(0, kind);
        t.handle(5, h);
        t.handle(330, block.map_or(self.model, |i| self.blocks[i].record));
        t.tag(100, "AcDbEntity");
        t.tag(8, layer);
        if self.continuous {
            t.tag(6, "CONTINUOUS");
        }
        if let Some(weight) = self.weight {
            t.tag(370, weight);
        }
        if let Some(colour) = self.colour {
            t.tag(420, colour);
        }
        (h, t)
    }
    fn put(&mut self, t: Tags, block: Option<usize>) {
        match block {
            Some(i) => self.blocks[i].entities.0.push_str(&t.0),
            None => self.entities.0.push_str(&t.0),
        }
    }
    fn insert(&mut self, i: usize, layer: &str) -> u64 {
        let (h, mut t) = self.entity("INSERT", layer, None);
        t.tag(100, "AcDbBlockReference");
        t.tag(2, &self.blocks[i].name);
        t.point(10, self.blocks[i].at);
        self.put(t, None);
        h
    }
    fn curve(&mut self, c: &Curve, layer: &str, block: Option<usize>) -> Option<u64> {
        // A projected isolated pole paints no curve. Degree-zero SPLINEs pass
        // some audits but cannot be constructed by CAD/ezdxf curve readers.
        let point_only = match c {
            Curve::Beziers(p) => p.iter().all(|p| p.len() < 2),
            Curve::Rational(p) => p.iter().all(|p| p.len() < 2),
            _ => false,
        };
        if point_only {
            return None;
        }

        let kind = match c {
            Curve::Line(..) => "LINE",
            Curve::Conic { radii, t0, t1, .. }
                if (radii[0] - radii[1]).abs() <= 1e-12 * radii[0] =>
            {
                if (t1 - t0).abs() >= std::f64::consts::TAU - 1e-12 {
                    "CIRCLE"
                } else {
                    "ARC"
                }
            }
            Curve::Conic { .. } => "ELLIPSE",
            _ => "SPLINE",
        };
        let (h, mut t) = self.entity(kind, layer, block);
        match c {
            Curve::Line(a, b) => {
                t.tag(100, "AcDbLine");
                t.point(10, *a);
                t.point(11, *b);
            }
            Curve::Conic {
                centre,
                radii,
                x_axis,
                y_axis,
                t0,
                t1,
            } => {
                let ccw = x_axis[0] * y_axis[1] - x_axis[1] * y_axis[0] > 0.;
                let (a, b) = if ccw { (*t0, *t1) } else { (-t0, -t1) };
                let (a, b) = (a.min(b), a.max(b));
                if kind == "CIRCLE" || kind == "ARC" {
                    t.tag(100, "AcDbCircle");
                    t.point(10, *centre);
                    t.tag(40, radii[0]);
                    if kind == "ARC" {
                        let rot = x_axis[1].atan2(x_axis[0]);
                        t.tag(100, "AcDbArc");
                        t.tag(50, (a + rot).to_degrees());
                        t.tag(51, (b + rot).to_degrees());
                    }
                } else {
                    // DXF requires the major axis first, even for vertically long ellipses.
                    let swap = radii[1] > radii[0];
                    let (axis, major, minor, shift) = if swap {
                        (
                            *y_axis,
                            radii[1],
                            radii[0],
                            if ccw {
                                -std::f64::consts::FRAC_PI_2
                            } else {
                                std::f64::consts::FRAC_PI_2
                            },
                        )
                    } else {
                        (*x_axis, radii[0], radii[1], 0.)
                    };
                    t.tag(100, "AcDbEllipse");
                    t.point(10, *centre);
                    t.point(11, [axis[0] * major, axis[1] * major]);
                    t.tag(40, minor / major);
                    t.tag(41, a + shift);
                    t.tag(42, b + shift);
                }
            }
            _ => {
                let mut pieces = match c {
                    Curve::Beziers(p) => p
                        .iter()
                        .map(|p| p.iter().map(|q| [q[0], q[1], 1.]).collect())
                        .collect(),
                    Curve::Rational(p) => p.clone(),
                    _ => unreachable!(),
                };
                // High-order kernel curves exceed common CAD/ezdxf evaluator limits.
                // Use the painted formats' existing 0.1 µm chord approximation.
                if pieces.iter().any(|p| p.len() > 11) {
                    pieces = crate::output::polynomial(&pieces)
                        .into_iter()
                        .map(|p| p.into_iter().map(|q| [q[0], q[1], 1.]).collect())
                        .collect();
                }
                let s = crate::output::spline(pieces);
                t.tag(100, "AcDbSpline");
                t.tag(70, if s.weight_values.is_empty() { 8 } else { 12 });
                t.tag(71, s.degree_of_curve);
                t.tag(72, s.knot_values.len());
                t.tag(73, s.control_points.len());
                t.tag(74, 0);
                for k in s.knot_values {
                    t.tag(40, k);
                }
                for w in s.weight_values {
                    t.tag(41, w);
                }
                for p in s.control_points {
                    t.point(10, [p.x, p.y]);
                }
            }
        }
        self.put(t, block);
        Some(h)
    }
    fn item(&mut self, i: &Item, layer: &str, block: Option<usize>) -> Vec<u64> {
        match i {
            Item::Glyph(..) | Item::TextStroke(..) => Vec::new(),
            Item::Edge(c) => self.curve(c, layer, block).into_iter().collect(),
            Item::Face(w) => {
                // SOLID supports triangular and quadrilateral arrowheads directly.
                if w.len() == 1
                    && (3..=4).contains(&w[0].len())
                    && w[0].iter().all(|c| matches!(c, Curve::Line(..)))
                {
                    let p: Vec<_> = w[0].iter().map(Curve::start).collect();
                    let (h, mut t) = self.entity("SOLID", layer, block);
                    t.tag(100, "AcDbTrace");
                    t.point(10, p[0]);
                    t.point(11, p[1]);
                    t.point(12, *p.last().unwrap());
                    t.point(13, p[2]);
                    self.put(t, block);
                    vec![h]
                } else {
                    w.iter()
                        .flatten()
                        .filter_map(|c| self.curve(c, layer, block))
                        .collect()
                }
            }
        }
    }
    /// DXF LINE has no dash-offset property. Clip centre-mark dashes using the
    /// same phase as SVG/PDF, on the chain layer with a continuous entity override.
    fn centre(&mut self, item: &Item, block: Option<usize>) {
        let Item::Edge(Curve::Line(a, b)) = item else {
            self.item(item, CENTRE, block);
            return;
        };
        let length = (b[0] - a[0]).hypot(b[1] - a[1]);
        if length < 1e-10 {
            return;
        }
        let pattern = [8., 1.5, 0.2, 1.5];
        let mut phase = crate::output::centre_phase(item);
        let mut k = 0;
        while phase >= pattern[k] {
            phase -= pattern[k];
            k = (k + 1) % 4;
        }
        let mut t = 0.;
        self.continuous = true;
        while t < length {
            let end = (t + pattern[k] - phase).min(length);
            if k % 2 == 0 && end > t {
                let p = |at: f64| {
                    [
                        a[0] + (b[0] - a[0]) * at / length,
                        a[1] + (b[1] - a[1]) * at / length,
                    ]
                };
                self.curve(&Curve::Line(p(t), p(end)), CENTRE, block);
            }
            t = end;
            phase = 0.;
            k = (k + 1) % 4;
        }
        self.continuous = false;
    }
    fn text(
        &mut self,
        r: &TextRun,
        layer: &str,
        block: Option<usize>,
        attr: Option<(&str, bool)>,
    ) -> Option<u64> {
        let multiline = r.text.contains('\n') && attr.is_none();
        let kind = attr.map_or(if multiline { "MTEXT" } else { "TEXT" }, |(_, def)| {
            if def { "ATTDEF" } else { "ATTRIB" }
        });
        let (h, mut t) = self.entity(kind, layer, block);
        let cap = font_data(&r.font)
            .and_then(|b| ttf_parser::Face::parse(b, 0).ok())
            .map_or(0.7, |f| {
                f.capital_height().unwrap_or(700) as f64 / f.units_per_em() as f64
            });
        let height = r.size * cap;
        t.tag(100, if multiline { "AcDbMText" } else { "AcDbText" });
        let insertion = if multiline && r.v_align == VAlign::Baseline {
            let (sin, cos) = r.rotation.to_radians().sin_cos();
            [r.at[0] - sin * height, r.at[1] + cos * height]
        } else {
            r.at
        };
        t.point(10, insertion);
        t.tag(40, height);
        let words = asme_words(&r.text);
        // The words are longer than the glyphs they replace; condense them to
        // the width the layout gave the label, so table cells do not overflow.
        // An empty field (a title-block attribute to fill in) keeps the font's width.
        let width = cad_width(text_font(r))
            * if words.is_empty() {
                1.
            } else {
                r.text.chars().count() as f64 / words.chars().count() as f64
            };
        let mut content = if multiline {
            encode_mtext(&words)
        } else {
            encode(&words)
        };
        if multiline {
            if width != 1. {
                content = format!("\\W{width:.3};{content}");
            }
            let mut parts = Vec::new();
            let mut tail = content.as_str();
            while !tail.is_empty() {
                let mut end = tail.len().min(250);
                while !tail.is_char_boundary(end) {
                    end -= 1;
                }
                parts.push(&tail[..end]);
                tail = &tail[end..];
            }
            for (i, part) in parts.iter().enumerate() {
                t.tag(if i + 1 == parts.len() { 1 } else { 3 }, part);
            }
            if parts.is_empty() {
                t.tag(1, "");
            }
        } else {
            t.tag(1, content);
        }
        t.tag(7, style(text_font(r)));
        if multiline {
            t.tag(
                71,
                match (r.h_align, r.v_align) {
                    (HAlign::Left, VAlign::Baseline) => 1,
                    (HAlign::Center, VAlign::Baseline) => 2,
                    (HAlign::Right, VAlign::Baseline) => 3,
                    (HAlign::Left, VAlign::Middle) => 4,
                    (HAlign::Center, VAlign::Middle) => 5,
                    (HAlign::Right, VAlign::Middle) => 6,
                },
            );
            t.tag(50, r.rotation);
            t.tag(41, 0);
        } else {
            t.tag(41, width);
            t.tag(50, r.rotation);
            t.tag(
                72,
                match r.h_align {
                    HAlign::Left => 0,
                    HAlign::Center => 1,
                    HAlign::Right => 2,
                },
            );
            t.point(11, r.at);
            if let Some((tag, def)) = attr {
                t.tag(
                    100,
                    if def {
                        "AcDbAttributeDefinition"
                    } else {
                        "AcDbAttribute"
                    },
                );
                if def {
                    t.tag(3, tag);
                }
                t.tag(2, tag);
                t.tag(70, 0);
                t.tag(74, if r.v_align == VAlign::Middle { 2 } else { 0 });
            } else {
                t.tag(100, "AcDbText");
                t.tag(73, if r.v_align == VAlign::Middle { 2 } else { 0 });
            }
        }
        self.put(t, block);
        Some(h)
    }
}
impl Writer {
    /// A feature control frame as one TOLERANCE entity (AcDbFcf): editable as a geometric
    /// tolerance, its box drawn by the reader from the style's DIMTXT and DIMGAP.
    fn tolerance(&mut self, f: &Frame, layer: &str, style: &str) -> u64 {
        let (h, mut t) = self.entity("TOLERANCE", layer, None);
        t.tag(100, "AcDbFcf");
        t.tag(3, style);
        t.point(10, f.at);
        t.tag(1, fcf_text(&f.cells));
        t.point(11, [1., 0.]);
        self.put(t, None);
        h
    }
}
/// The letters of gdt.shx (AutoCAD; QCAD ships the same mapping as gdt.cxf) that draw the
/// symbols haecceity writes in a frame: its characteristics, the zone's diameter sign and the
/// circled modifiers. Any other symbol is written as plain text (`asme_words`).
const GDT: [(char, char); 19] = [
    ('∠', 'a'),
    ('⟂', 'b'),
    ('⏥', 'c'),
    ('⌓', 'd'),
    ('○', 'e'),
    ('∥', 'f'),
    ('⌭', 'g'),
    ('↗', 'h'),
    ('⌯', 'i'),
    ('⌖', 'j'),
    ('⌒', 'k'),
    ('Ⓛ', 'l'),
    ('Ⓜ', 'm'),
    ('⌀', 'n'),
    ('Ⓟ', 'p'),
    ('◎', 'r'),
    ('Ⓢ', 's'),
    ('⌰', 't'),
    ('⏤', 'u'),
];
/// A frame's cells as a TOLERANCE's text: cells separated by `%%v`, each symbol switched to
/// the gdt font.
fn fcf_text(cells: &[String]) -> String {
    cells
        .iter()
        .map(|cell| {
            let mut out = String::new();
            let mut plain = String::new();
            for c in cell.chars() {
                if let Some((_, g)) = GDT.iter().find(|(u, _)| *u == c) {
                    out.push_str(&encode_mtext(&asme_words(&plain)));
                    plain.clear();
                    write!(out, "{{\\Fgdt;{g}}}").unwrap();
                } else {
                    plain.push(c);
                }
            }
            out.push_str(&encode_mtext(&asme_words(&plain)));
            out
        })
        .collect::<Vec<_>>()
        .join("%%v")
}
fn font_data(font: &str) -> Option<&'static [u8]> {
    match font {
        "IBMPlexMono-Regular.ttf" => Some(crate::api::MONO),
        crate::title_block::FONT => Some(crate::title_block::FONT_DATA),
        _ => None,
    }
}
fn style(font: &str) -> String {
    font.trim_end_matches(".ttf")
        .trim_end_matches(".otf")
        .to_string()
}
/// Keep the drawing's font-family style names, but request widely installed
/// alternatives. STYLE group 4 is a CJK bigfont, not a fallback list; using it
/// for a second TTF would not provide substitution.
fn cad_font_file(font: &str) -> &str {
    match font {
        // The single-stroke drafting font every DWG/DXF program ships (AutoCAD,
        // its viewers, BricsCAD, DraftSight, QCAD, LibreCAD).
        "IBMPlexMono-Regular.ttf" | crate::title_block::FONT => "romans.shx",
        _ => font,
    }
}
fn encode(s: &str) -> String {
    let mut out = String::new();
    for c in s.chars() {
        match c {
            '⌀' | 'ø' | 'Ø' => out.push_str("%%c"),
            '°' => out.push_str("%%d"),
            '±' => out.push_str("%%p"),
            '\n' => out.push_str("\\P"),
            // SHX fonts have neither; ASME writes counts as 4X.
            '−' | '–' | '—' => out.push('-'),
            '×' => out.push('X'),
            _ => out.push(c),
        }
    }
    out
}
/// romans.shx advances about one cap height per character. Condense it to
/// the Plex advance the layout used, so text stays inside its cells.
fn cad_width(font: &str) -> f64 {
    match font {
        "IBMPlexMono-Regular.ttf" => 0.85,
        crate::title_block::FONT => 0.75,
        _ => 1.,
    }
}
fn safe_name(s: &str) -> String {
    s.chars()
        .map(|c| {
            if c.is_control() || "<>/\\\":;?*|=`".contains(c) {
                '_'
            } else {
                c
            }
        })
        .collect()
}

/// Definition points from the actual placed witnesses. Broken views remain grouped:
/// their discontinuous geometry cannot state a true measurement with one DIMLFAC.
fn dimension(d: &Dimension, drawing: &Drawing) -> Option<(i32, Vec<P>, f64)> {
    let p = d.placed.as_ref()?;
    let frame = drawing.sheet.frames.get(d.view.trim_start_matches("v:"))?;
    if frame.broken.is_some() {
        return None;
    }
    if d.kind == "angular" && p.lines.len() > 2 {
        let [a, b] = p.lines[0];
        let [c, e] = p.lines[1];
        let u = [b[0] - a[0], b[1] - a[1]];
        let v = [e[0] - c[0], e[1] - c[1]];
        let cross = u[0] * v[1] - u[1] * v[0];
        if cross.abs() < 1e-12 {
            return None;
        }
        let k = ((c[0] - a[0]) * v[1] - (c[1] - a[1]) * v[0]) / cross;
        let centre = [a[0] + k * u[0], a[1] + k * u[1]];
        return Some((
            5,
            vec![p.lines[2][0], p.lines.last()?[1], centre, p.lines[2][0]],
            1.,
        ));
    }
    if p.arrows.len() == 2 && p.lines.len() >= 3 {
        let axis = [
            p.arrows[1][0][0] - p.arrows[0][0][0],
            p.arrows[1][0][1] - p.arrows[0][0][1],
        ];
        let n = axis[0].hypot(axis[1]);
        if n < 1e-10 {
            return None;
        }
        let mut ends = [p.lines[0][0], p.lines[1][0]];
        // Witnesses start GAP short of the geometry. Undo that offset along each witness.
        for (q, line) in ends.iter_mut().zip(&p.lines) {
            let delta = [line[1][0] - line[0][0], line[1][1] - line[0][1]];
            let len = delta[0].hypot(delta[1]);
            if len > 0. {
                for k in 0..2 {
                    q[k] -= delta[k] / len * crate::dims::GAP;
                }
            }
        }
        let aligned = axis[0].abs() > 1e-6 && axis[1].abs() > 1e-6;
        let measure = if aligned {
            (ends[1][0] - ends[0][0]).hypot(ends[1][1] - ends[0][1])
        } else if axis[0].abs() > axis[1].abs() {
            (ends[1][0] - ends[0][0]).abs()
        } else {
            (ends[1][1] - ends[0][1]).abs()
        };
        if (measure / frame.scale - d.value).abs() > 1e-4 {
            return None;
        }
        // Profile diameters keep the planned linear witnesses and dimension
        // line. A circular leader below uses the diametric/radial entity types.
        return Some((
            if aligned { 1 } else { 0 },
            vec![ends[0], ends[1], p.arrows[0][0]],
            1. / frame.scale,
        ));
    }
    // A manufacturing label can override a proven primary radius/diameter.
    // Its additional requirements stay editable in the same DIMENSION text.
    let diameter = d.diameter;
    let radial = d.radial;
    if diameter || radial {
        let radius = if radial { d.value } else { d.value / 2. };
        let tip = p.arrows.first()?[0];
        let view = drawing.sheet.views.iter().find(|v| v.name == d.view)?;
        let circle = view
            .edges
            .iter()
            .filter_map(|c| {
                if let Curve::Conic { centre, radii, .. } = c {
                    ((radii[0] - radii[1]).abs() < 1e-8
                        && (radii[0] / frame.scale - radius).abs() < 1e-5)
                        .then_some((*centre, radii[0]))
                } else {
                    None
                }
            })
            .min_by(|(a, r), (b, s)| {
                (((tip[0] - a[0]).hypot(tip[1] - a[1]) - r).abs())
                    .total_cmp(&((tip[0] - b[0]).hypot(tip[1] - b[1]) - s).abs())
            })?;
        if ((tip[0] - circle.0[0]).hypot(tip[1] - circle.0[1]) - circle.1).abs() > 0.05 {
            return None;
        }
        // The leader router samples curved rims; definition points must lie on
        // the actual circle so a DIMENSION measures its true radius/diameter.
        let length = (tip[0] - circle.0[0]).hypot(tip[1] - circle.0[1]);
        if length < 1e-12 {
            return None;
        }
        let rim = [0, 1].map(|k| circle.0[k] + (tip[k] - circle.0[k]) * circle.1 / length);
        let opposite = [2. * circle.0[0] - rim[0], 2. * circle.0[1] - rim[1]];
        return Some((
            if radial { 4 } else { 3 },
            vec![if radial { circle.0 } else { opposite }, rim],
            1. / frame.scale,
        ));
    }
    None
}

fn dimension_text_box(items: &[Item], runs: &[usize], primary: [f64; 4]) -> [f64; 4] {
    if runs.len() <= 1 {
        return primary;
    }
    // The plan's label_box can describe the first line of a wrapped callout.
    // Group 11 is the midpoint of the complete dimension text, including its
    // continuation lines, rather than that first line alone.
    crate::output::items_bbox(items.iter().filter(
        |item| matches!(item, Item::Glyph(_, r) | Item::TextStroke(_, r) if runs.contains(r)),
    ))
    .unwrap_or(primary)
}

pub(crate) fn write(drawing: &Drawing) -> Vec<u8> {
    let mut w = Writer::new();
    let model = w.block("*Model_Space".into(), [0., 0.]);
    w.model = w.blocks[model].record;
    w.block("*Paper_Space".into(), [0., 0.]);
    let mut consumed: BTreeSet<(usize, usize)> = BTreeSet::new();
    let mut runs = BTreeSet::new();
    let mut run_handles = BTreeMap::new();
    // View blocks use sheet coordinates about a sheet-coordinate base point. INSERT
    // translation thus moves the whole view without altering its 1:1 paper depiction.
    for view in &drawing.sheet.dxf.views {
        let block = w.block(safe_name(&view.name), view.at);
        for (layer, range, name) in [
            (0, &view.visible, VISIBLE),
            (1, &view.hidden, HIDDEN),
            (3, &view.centres, CENTRE),
        ] {
            if let Some(l) = drawing.layers.get(layer) {
                for i in range.clone() {
                    if let Some(item) = l.items.get(i) {
                        if name == CENTRE {
                            w.centre(item, Some(block));
                        } else {
                            w.item(item, name, Some(block));
                        }
                        consumed.insert((layer, i));
                    }
                }
            }
        }
        w.weight = Some(25);
        if let Some((li, l)) = drawing
            .layers
            .iter()
            .enumerate()
            .find(|(_, l)| l.name == "thin")
        {
            for i in view.thin.clone() {
                if let Some(item) = l.items.get(i) {
                    w.item(item, VISIBLE, Some(block));
                    consumed.insert((li, i));
                }
            }
        }
        w.weight = None;
        w.insert(block, VISIBLE);
    }
    let mut styles: BTreeMap<String, DimStyle> = BTreeMap::new();
    let mut view_handles: BTreeMap<String, Vec<u64>> = BTreeMap::new();
    let mut dim_index = 0;
    for a in &drawing.annotations {
        let range = drawing
            .sheet
            .dxf
            .annotations
            .get(&a.name)
            .cloned()
            .unwrap_or(0..0);
        let layer = if drawing
            .sheet
            .items
            .iter()
            .any(|i| i.name == a.name && i.kind == crate::output::Kind::Gdt)
        {
            PMI
        } else {
            DIMS
        };
        let native = drawing
            .sheet
            .dxf
            .dimensions
            .get(&a.name)
            .and_then(|d| dimension(d, drawing).map(|v| (d, v)));
        let block = native.as_ref().map(|_| {
            dim_index += 1;
            w.block(format!("*D{dim_index}"), [0., 0.])
        });
        let mut handles = Vec::new();
        let frame = drawing.sheet.dxf.frames.get(&a.name);
        if let Some(l) = drawing.layers.get(2) {
            for i in range.clone() {
                if let Some(item) = l.items.get(i) {
                    let own =
                        frame.is_some_and(|f| f.ink.iter().any(|r| r.contains(&(i - range.start))));
                    if !own {
                        handles.extend(w.item(item, layer, block));
                    }
                    consumed.insert((2, i));
                }
            }
        }
        if let Some(f) = frame {
            let style = DimStyle {
                frame: true,
                ..DimStyle::default()
            };
            styles.insert(style.name(), style);
            handles.push(w.tolerance(f, layer, &style.name()));
        }
        for (n, &r) in a.runs.iter().enumerate() {
            if frame.is_some_and(|f| n >= f.lead) {
                runs.insert(r);
                continue;
            }
            if drawing
                .sheet
                .dxf
                .title_fields
                .iter()
                .any(|(_, f)| drawing.text.get(r).is_some_and(|run| run.at == f.at))
            {
                continue;
            }
            if let Some(run) = drawing.text.get(r) {
                let h = *run_handles
                    .entry(r)
                    .or_insert_with(|| w.text(run, layer, block, None));
                handles.extend(h);
                runs.insert(r);
            }
        }
        if let (Some((d, (kind, points, factor))), Some(block)) = (native, block) {
            let precision = drawing.sheet.precision.get(&a.name).copied();
            let inches = drawing.sheet.units == crate::units::Units::In;
            let live = live_label(d, kind, inches);
            let font = "IBMPlexMono-Regular.ttf";
            let leader = matches!(kind, 3 | 4);
            let style = DimStyle {
                font,
                factor,
                precision: precision.unwrap_or(3),
                fixed: precision.is_some(),
                leader,
                inches,
                frame: false,
            };
            let name = style.name();
            styles.insert(name.clone(), style);
            let (h, mut t) = w.entity("DIMENSION", DIMS, None);
            if let Some(v) = &a.view {
                view_handles.entry(v.clone()).or_default().push(h);
            }
            t.tag(100, "AcDbDimension");
            t.tag(280, 0);
            t.tag(2, &w.blocks[block].name);
            t.point(10, if kind <= 1 { points[2] } else { points[0] });
            let bbox = dimension_text_box(
                &drawing.layers[2].items[range.clone()],
                &a.runs,
                d.placed.as_ref().expect("placed dimension").label_box,
            );
            let at = [(bbox[0] + bbox[2]) / 2., (bbox[1] + bbox[3]) / 2.];
            t.point(11, at);
            t.tag(70, kind | 32 | 128);
            t.tag(71, 5);
            // The primary measured value remains live even in manufacturing wording.
            t.tag(1, live);
            t.tag(3, name);
            t.tag(53, d.placed.as_ref().unwrap().rotation);
            match kind {
                0 | 1 => {
                    t.tag(100, "AcDbAlignedDimension");
                    t.point(13, points[0]);
                    t.point(14, points[1]);
                    let arrows = &d.placed.as_ref().unwrap().arrows;
                    t.tag(
                        50,
                        (arrows[1][0][1] - arrows[0][0][1])
                            .atan2(arrows[1][0][0] - arrows[0][0][0])
                            .to_degrees()
                            .rem_euclid(180.),
                    );
                    if kind == 0 {
                        t.tag(100, "AcDbRotatedDimension");
                    }
                }
                3 | 4 => {
                    t.tag(
                        100,
                        if kind == 3 {
                            "AcDbDiametricDimension"
                        } else {
                            "AcDbRadialDimension"
                        },
                    );
                    t.point(15, points[1]);
                    let bend = d.placed.as_ref().unwrap().lines.first().map(|l| l[1]);
                    t.tag(
                        40,
                        bend.map_or(0., |b| (b[0] - points[1][0]).hypot(b[1] - points[1][1])),
                    );
                }
                5 => {
                    t.tag(100, "AcDb3PointAngularDimension");
                    t.point(13, points[0]);
                    t.point(14, points[1]);
                    t.point(15, points[2]);
                    t.point(16, points[3]);
                }
                _ => unreachable!(),
            }
            t.tag(1001, "ACAD");
            t.tag(1000, "DSTYLE");
            t.tag(1002, "{");
            for (_, code, value) in style.variables() {
                t.tag(1070, code);
                t.tag(if dimension_integer(code) { 1070 } else { 1040 }, value);
            }
            t.tag(1002, "}");
            t.tag(1001, "DRAFTWRIGHT");
            t.tag(1000, &a.name);
            t.tag(1000, &d.kind);
            w.put(t, None);
        } else if !handles.is_empty() {
            if let Some(v) = &a.view {
                view_handles.entry(v.clone()).or_default().extend(&handles);
            }
            w.group(a.name.clone(), handles);
        }
    }
    if let Some(l) = drawing.layers.get(2) {
        for (name, range) in &drawing.sheet.dxf.furniture {
            let layer = if drawing
                .sheet
                .items
                .iter()
                .any(|i| i.name == *name && i.kind == crate::output::Kind::Gdt)
            {
                PMI
            } else {
                TEXT
            };
            let mut handles = Vec::new();
            for i in range.clone() {
                if let Some(item) = l.items.get(i) {
                    if !consumed.contains(&(2, i)) {
                        handles.extend(w.item(item, layer, None));
                        consumed.insert((2, i));
                    }
                    if let Item::Glyph(_, r) | Item::TextStroke(_, r) = item
                        && let Some(run) = drawing.text.get(*r)
                    {
                        let h = *run_handles
                            .entry(*r)
                            .or_insert_with(|| w.text(run, layer, None, None));
                        handles.extend(h);
                        runs.insert(*r);
                    }
                }
            }
            if let Some(view) = drawing.sheet.dxf.furniture_views.get(name) {
                view_handles
                    .entry(view.clone())
                    .or_default()
                    .extend(&handles);
            }
            w.group(name.clone(), handles);
        }
    }
    if let Some(l) = drawing.layers.get(2) {
        for region in &drawing.sheet.dxf.hatches {
            let mut boundary = closed_boundaries(&region.loops);
            if let Some(rect) = region.clip {
                boundary = clip_loops(boundary, rect);
            }
            let closed = !boundary.is_empty() && !region.items.is_empty();
            let block = w
                .blocks
                .iter()
                .position(|b| b.name == safe_name(&region.view));
            if closed {
                w.hatch(&boundary, block, hatch_phase(&region.loops));
            }
            for i in region.items.clone() {
                if let Some(item) = l.items.get(i) {
                    if !closed {
                        w.item(item, HATCH, block);
                    }
                    consumed.insert((2, i));
                }
            }
        }
        for plane in &drawing.sheet.dxf.cutting {
            let mut handles = Vec::new();
            for i in plane.items.clone() {
                if let Some(item) = l.items.get(i) {
                    handles.extend(w.item(item, CUT, None));
                    consumed.insert((2, i));
                }
            }
            if !handles.is_empty() {
                view_handles
                    .entry(plane.view.clone())
                    .or_default()
                    .extend(&handles);
                w.group(plane.name.clone(), handles);
            }
        }
    }
    // The block's field values are INSERT attributes; ATTDEFs are invisible defaults.
    let title = w.block("TITLE_BLOCK".into(), [0., 0.]);
    if let Some(tb) = drawing
        .sheet
        .items
        .iter()
        .find(|i| i.kind == crate::output::Kind::TitleBlock)
    {
        for [a, b] in tb.lines.iter().take(4) {
            let _ = w.curve(&Curve::Line(*a, *b), BORDER, Some(title));
            if let Some(layer) = drawing.layers.first() {
                for (i, item) in layer.items.iter().enumerate() {
                    if matches!(item,Item::Edge(Curve::Line(p,q)) if (*p==*a && *q==*b)||(*p==*b && *q==*a))
                    {
                        consumed.insert((0, i));
                    }
                }
            }
        }
    }

    w.weight = Some(5);
    w.colour = Some(0x0033b2);
    if let Some(l) = drawing.layers.get(2) {
        for i in drawing.sheet.dxf.title.clone() {
            if let Some(item) = l.items.get(i) {
                w.item(item, BORDER, Some(title));
                consumed.insert((2, i));
            }
        }
    }
    let mut fields = Vec::new();
    for (field, r) in &drawing.sheet.dxf.title_fields {
        let tag = field.to_uppercase();
        w.text(r, BORDER, Some(title), Some((&tag, true)));
        fields.push((tag, r));
        for (i, run) in drawing.text.iter().enumerate() {
            if (run.at[0] - r.at[0]).abs() < 1e-6 && (run.at[1] - r.at[1]).abs() < 1e-6 {
                runs.insert(i);
            }
        }
    }
    for r in &drawing.sheet.dxf.labels {
        w.text(r, BORDER, Some(title), None);
    }
    // Other text in title cells (pack labels) is ordinary block text.
    if let Some(tb) = drawing
        .sheet
        .items
        .iter()
        .find(|i| i.kind == crate::output::Kind::TitleBlock)
    {
        for (i, r) in drawing.text.iter().enumerate() {
            if !runs.contains(&i) && within(r.at, tb.ink) {
                w.text(r, BORDER, Some(title), None);
                runs.insert(i);
            }
        }
    }
    let (insert, mut t) = w.entity("INSERT", BORDER, None);
    t.tag(100, "AcDbBlockReference");
    t.tag(66, 1);
    t.tag(2, "TITLE_BLOCK");
    t.point(10, [0., 0.]);
    w.put(t, None);
    for (tag, r) in fields {
        let start = w.entities.0.len();
        w.text(r, BORDER, None, Some((&tag, false)));
        // Attributes belong to their INSERT, not to model space.
        let old = format!("330\n{:X}\n", w.model);
        let new = format!("330\n{insert:X}\n");
        let tail = w.entities.0[start..].replace(&old, &new);
        w.entities.0.truncate(start);
        w.entities.0.push_str(&tail);
    }
    let (_, mut end) = w.entity("SEQEND", BORDER, None);
    end.0 = end.0.replace(
        &format!("330\n{:X}\n", w.model),
        &format!("330\n{insert:X}\n"),
    );
    w.put(end, None);
    w.weight = None;
    w.colour = None;
    let border = w.block("SHEET_BORDER".into(), [0., 0.]);
    let mut border_ink = false;
    for (li, l) in drawing.layers.iter().enumerate() {
        if l.name.starts_with("shaded-") {
            continue;
        }
        let layer = match l.name.as_str() {
            "part" => VISIBLE,
            "hidden" => HIDDEN,
            "centres" => CENTRE,
            "dims" => DIMS,
            "projection_symbol" | "centring_marks" => BORDER,
            _ => VISIBLE,
        };
        w.weight = Some(l.style.weight);
        for (i, item) in l.items.iter().enumerate() {
            if consumed.contains(&(li, i)) {
                continue;
            }
            let is_border = li == 0
                && match item {
                    Item::Edge(Curve::Line(a, b)) => {
                        let v = &drawing.sheet.views;
                        !v.iter()
                            .any(|v| v.edges.iter().any(|c| c == &Curve::Line(*a, *b)))
                    }
                    _ => false,
                };
            if is_border || layer == BORDER {
                w.item(item, BORDER, Some(border));
                border_ink = true;
            } else {
                w.item(item, layer, None);
            }
        }
    }
    w.weight = None;
    if border_ink {
        w.insert(border, BORDER);
    }
    for (i, r) in drawing.text.iter().enumerate() {
        if !runs.contains(&i) {
            w.text(r, TEXT, None, None);
        }
    }
    for (view, handles) in view_handles {
        w.group(format!("VIEW_{}_ANNOT", safe_name(&view)), handles);
    }
    finish(w, drawing, styles)
}
fn within(p: P, b: [f64; 4]) -> bool {
    p[0] >= b[0] && p[0] <= b[2] && p[1] >= b[1] && p[1] <= b[3]
}
fn table(t: &mut Tags, kind: &str, h: u64, count: usize) {
    t.tag(0, "TABLE");
    t.tag(2, kind);
    t.handle(5, h);
    t.handle(330, 0);
    t.tag(100, "AcDbSymbolTable");
    t.tag(70, count);
}
fn record(t: &mut Tags, kind: &str, h: u64, owner: u64, subclass: &str, name: &str) {
    t.tag(0, kind);
    t.handle(if kind == "DIMSTYLE" { 105 } else { 5 }, h);
    t.handle(330, owner);
    t.tag(100, "AcDbSymbolTableRecord");
    t.tag(100, subclass);
    t.tag(2, name);
    t.tag(70, 0);
}
fn finish(mut w: Writer, drawing: &Drawing, styles: BTreeMap<String, DimStyle>) -> Vec<u8> {
    let root = w.id();
    let dict = w.id();
    let layout_dict = w.id();
    let layout_ids = [w.id(), w.id()];
    let plot_settings = w.id();
    // AutoCAD refuses a LAYER without a PlotStyleName (390), which must point
    // at a placeholder held in ACAD_PLOTSTYLENAME.
    let plot_styles = w.id();
    let normal = w.id();
    let group_ids: Vec<_> = (0..w.groups.len()).map(|_| w.id()).collect();
    let mut reactors: BTreeMap<u64, Vec<u64>> = BTreeMap::new();
    for ((_, handles), id) in w.groups.iter().zip(&group_ids) {
        for h in handles {
            reactors.entry(*h).or_default().push(*id);
        }
    }
    w.entities = with_reactors(&w.entities, &reactors);

    let mut out = Tags(
        String::from_utf8(
            drawing
                .spec
                .as_ref()
                .map_or_else(Vec::new, |s| crate::output::dxf_spec_comments(s)),
        )
        .unwrap(),
    );
    out.tag(0, "SECTION");
    out.tag(2, "HEADER");
    for (name, code, value) in [
        ("$ACADVER", 1, "AC1027"),
        ("$DWGCODEPAGE", 3, "ANSI_1252"),
        ("$INSUNITS", 70, "4"),
        ("$MEASUREMENT", 70, "1"),
        ("$LTSCALE", 40, "1"),
        ("$PSLTSCALE", 70, "1"),
        ("$TDCREATE", 40, "2451545"),
        ("$TDUPDATE", 40, "2451545"),
        ("$TDUCREATE", 40, "2451545"),
        ("$TDUUPDATE", 40, "2451545"),
    ] {
        out.tag(9, name);
        out.tag(code, value);
    }
    out.tag(9, "$INSBASE");
    out.point(10, [0., 0.]);
    out.tag(9, "$DIMSTYLE");
    out.tag(2, "STANDARD");
    out.tag(9, "$TEXTSTYLE");
    out.tag(7, "STANDARD");
    out.tag(9, "$DIMTXSTY");
    out.tag(7, "STANDARD");
    // Readers that take dimension settings from the header see the sheet's units.
    let standard = DimStyle {
        inches: drawing.sheet.units == crate::units::Units::In,
        ..DimStyle::default()
    };
    for (name, code, value) in standard.variables() {
        out.tag(9, format!("${name}"));
        out.tag(if dimension_integer(code) { 70 } else { 40 }, value);
    }
    out.tag(9, "$HANDSEED");
    out.tag(5, "__HANDSEED__");
    use sha2::Digest;
    let mut hash = sha2::Sha256::new();
    hash.update(drawing.spec.as_deref().unwrap_or("").as_bytes());
    hash.update(drawing.sheet.dxf.sheet_number.to_le_bytes());
    hash.update(w.entities.0.as_bytes());
    for block in &w.blocks {
        hash.update(block.name.as_bytes());
        hash.update(block.entities.0.as_bytes());
    }
    let digest = hash.finalize();
    let guid = uuid::Uuid::from_slice(&digest[..16])
        .expect("16 bytes")
        .to_string();
    for name in ["$FINGERPRINTGUID", "$VERSIONGUID"] {
        out.tag(9, name);
        out.tag(2, format!("{{{guid}}}"));
    }
    out.tag(9, "$EXTMIN");
    out.point(10, [0., 0.]);
    out.tag(9, "$EXTMAX");
    out.point(10, drawing.page);
    out.tag(9, "$LIMMIN");
    out.tag(10, 0);
    out.tag(20, 0);
    out.tag(9, "$LIMMAX");
    out.tag(10, drawing.page[0]);
    out.tag(20, drawing.page[1]);
    out.tag(0, "ENDSEC");
    out.tag(0, "SECTION");
    out.tag(2, "CLASSES");
    out.tag(0, "CLASS");
    out.tag(1, "LAYOUT");
    out.tag(2, "AcDbLayout");
    out.tag(3, "ObjectDBX Classes");
    out.tag(90, 0);
    out.tag(91, 2);
    out.tag(280, 0);
    out.tag(281, 0);
    for (dxf, cpp) in [
        ("ACDBDICTIONARYWDFLT", "AcDbDictionaryWithDefault"),
        ("ACDBPLACEHOLDER", "AcDbPlaceHolder"),
    ] {
        out.tag(0, "CLASS");
        out.tag(1, dxf);
        out.tag(2, cpp);
        out.tag(3, "ObjectDBX Classes");
        out.tag(90, 0);
        out.tag(91, 1);
        out.tag(280, 0);
        out.tag(281, 0);
    }
    out.tag(0, "ENDSEC");
    out.tag(0, "SECTION");
    out.tag(2, "TABLES");
    let vp = w.id();
    table(&mut out, "VPORT", vp, 1);
    let active = w.id();
    record(
        &mut out,
        "VPORT",
        active,
        vp,
        "AcDbViewportTableRecord",
        "*ACTIVE",
    );
    for (code, point) in [
        (10, [0., 0.]),
        (11, [1., 1.]),
        (12, [drawing.page[0] / 2., drawing.page[1] / 2.]),
        (13, [0., 0.]),
        (14, [10., 10.]),
        (15, [10., 10.]),
    ] {
        out.tag(code, point[0]);
        out.tag(code + 10, point[1]);
    }
    out.tag(16, 0);
    out.tag(26, 0);
    out.tag(36, 1);
    out.point(17, [0., 0.]);
    out.tag(40, drawing.page[1] * 1.05);
    out.tag(41, drawing.page[0] / drawing.page[1]);
    out.tag(42, 50);
    out.tag(43, 0);
    out.tag(44, 0);
    out.tag(50, 0);
    out.tag(51, 0);
    for (code, value) in [
        (71, 0),
        (72, 1000),
        (73, 1),
        (74, 3),
        (75, 0),
        (76, 0),
        (77, 0),
        (78, 0),
        (281, 0),
        (65, 0),
    ] {
        out.tag(code, value);
    }
    out.tag(0, "ENDTAB");
    for name in ["VIEW", "UCS"] {
        let id = w.id();
        table(&mut out, name, id, 0);
        out.tag(0, "ENDTAB");
    }
    let lt = w.id();
    table(&mut out, "LTYPE", lt, 5);
    for (name, pattern) in [
        ("ByBlock", vec![]),
        ("ByLayer", vec![]),
        ("CONTINUOUS", vec![]),
        ("ISO_DASH", vec![1.5875, -0.79375]),
        ("ISO_CHAIN", vec![8., -1.5, 0.2, -1.5]),
    ] {
        record(
            &mut out,
            "LTYPE",
            w.id(),
            lt,
            "AcDbLinetypeTableRecord",
            name,
        );
        out.tag(3, name);
        out.tag(72, 65);
        out.tag(73, pattern.len());
        out.tag(40, pattern.iter().map(|v: &f64| v.abs()).sum::<f64>());
        for v in pattern {
            out.tag(49, v);
            out.tag(74, 0);
        }
    }
    out.tag(0, "ENDTAB");
    let la = w.id();
    table(&mut out, "LAYER", la, LAYERS.len() + 1);
    for (name, weight, linetype) in std::iter::once(("0", 25, "CONTINUOUS")).chain(LAYERS) {
        record(&mut out, "LAYER", w.id(), la, "AcDbLayerTableRecord", name);
        out.tag(62, 7);
        // ACI 7 alone is white in QCAD's white-background CLI renderer. Give
        // every layer an explicit RGB, including black outlines and borders.
        out.tag(
            420,
            match name {
                HIDDEN => 0x7f7f7f,
                DIMS | TEXT | PMI | HATCH | CUT => 0x0033b2,
                _ => 0,
            },
        );
        out.tag(6, linetype);
        out.tag(290, if name == "Defpoints" { 0 } else { 1 });
        out.tag(370, weight);
        out.handle(390, normal);
    }
    out.tag(0, "ENDTAB");
    let st = w.id();
    let mut fonts: BTreeSet<_> = drawing
        .text
        .iter()
        .chain(&drawing.sheet.dxf.labels)
        .map(|r| text_font(r).to_string())
        .collect();
    fonts.insert("IBMPlexMono-Regular.ttf".into());
    fonts.extend(styles.values().map(|s| s.font.to_string()));
    table(&mut out, "STYLE", st, fonts.len() + 1);
    let mut textstyles = BTreeMap::new();
    for (name, file) in std::iter::once((
        "STANDARD".to_string(),
        "IBMPlexMono-Regular.ttf".to_string(),
    ))
    .chain(fonts.iter().map(|f| (style(f), f.clone())))
    {
        let id = w.id();
        if name != "STANDARD" {
            textstyles.insert(file.clone(), id);
        }
        record(&mut out, "STYLE", id, st, "AcDbTextStyleTableRecord", &name);
        out.tag(40, 0);
        out.tag(41, cad_width(&file));
        out.tag(50, 0);
        out.tag(71, 0);
        out.tag(42, 3);
        out.tag(3, cad_font_file(&file));
        out.tag(4, "");
    }
    out.tag(0, "ENDTAB");
    let ds = w.id();
    table(&mut out, "DIMSTYLE", ds, styles.len() + 1);
    out.tag(100, "AcDbDimStyleTable");
    out.tag(71, 0);
    for (name, style) in std::iter::once(("STANDARD".to_string(), standard)).chain(styles) {
        record(
            &mut out,
            "DIMSTYLE",
            w.id(),
            ds,
            "AcDbDimStyleTableRecord",
            &name,
        );
        // Diameter and radius signs are written in the text (`%%c<>`, `R<>`), where QCAD
        // and LibreCAD show them. AutoCAD would add its own sign to `<>` as well, but a
        // DIMPOST prefix replaces that default; `\A1;` is a prefix that prints nothing.
        if style.leader {
            out.tag(3, "\\A1;<>");
        }
        for (_, code, value) in style.variables() {
            out.tag(code, value);
        }
        out.handle(340, textstyles[style.font]);
    }
    out.tag(0, "ENDTAB");
    let ap = w.id();
    table(&mut out, "APPID", ap, 2);
    record(
        &mut out,
        "APPID",
        w.id(),
        ap,
        "AcDbRegAppTableRecord",
        "ACAD",
    );
    record(
        &mut out,
        "APPID",
        w.id(),
        ap,
        "AcDbRegAppTableRecord",
        "DRAFTWRIGHT",
    );
    out.tag(0, "ENDTAB");
    let br = w.id();
    table(&mut out, "BLOCK_RECORD", br, w.blocks.len());
    for (i, b) in w.blocks.iter().enumerate() {
        record(
            &mut out,
            "BLOCK_RECORD",
            b.record,
            br,
            "AcDbBlockTableRecord",
            &b.name,
        );
        if i < 2 {
            out.handle(340, layout_ids[i]);
        }
        out.tag(280, 0);
        out.tag(281, 1);
    }
    out.tag(0, "ENDTAB");
    out.tag(0, "ENDSEC");
    out.tag(0, "SECTION");
    out.tag(2, "BLOCKS");
    for b in &w.blocks {
        out.tag(0, "BLOCK");
        out.handle(5, b.begin);
        out.handle(330, b.record);
        out.tag(100, "AcDbEntity");
        out.tag(8, "0");
        out.tag(100, "AcDbBlockBegin");
        out.tag(2, &b.name);
        out.tag(70, usize::from(b.name.starts_with("*D")));
        out.point(10, b.at);
        out.tag(3, &b.name);
        out.tag(1, "");
        out.0.push_str(&b.entities.0);
        out.tag(0, "ENDBLK");
        out.handle(5, b.end);
        out.handle(330, b.record);
        out.tag(100, "AcDbEntity");
        out.tag(8, "0");
        out.tag(100, "AcDbBlockEnd");
    }
    out.tag(0, "ENDSEC");
    out.tag(0, "SECTION");
    out.tag(2, "ENTITIES");
    out.0.push_str(&w.entities.0);
    out.tag(0, "ENDSEC");

    out.tag(0, "SECTION");
    out.tag(2, "OBJECTS");
    dictionary(&mut out, root, 0);
    out.tag(3, "ACAD_GROUP");
    out.handle(350, dict);
    out.tag(3, "ACAD_LAYOUT");
    out.handle(350, layout_dict);
    out.tag(3, "ACAD_PLOTSETTINGS");
    out.handle(350, plot_settings);
    out.tag(3, "ACAD_PLOTSTYLENAME");
    out.handle(350, plot_styles);
    out.tag(0, "ACDBDICTIONARYWDFLT");
    out.handle(5, plot_styles);
    out.handle(330, root);
    out.tag(100, "AcDbDictionary");
    out.tag(281, 1);
    out.tag(3, "Normal");
    out.handle(350, normal);
    out.tag(100, "AcDbDictionaryWithDefault");
    out.handle(340, normal);
    out.tag(0, "ACDBPLACEHOLDER");
    out.handle(5, normal);
    out.handle(330, plot_styles);
    dictionary(&mut out, layout_dict, root);
    for (i, name) in ["Model", "Layout1"].iter().enumerate() {
        out.tag(3, name);
        out.handle(350, layout_ids[i]);
    }
    dictionary(&mut out, plot_settings, root);
    for (i, name) in ["Model", "Layout1"].iter().enumerate() {
        layout(
            &mut out,
            layout_ids[i],
            layout_dict,
            w.blocks[i].record,
            name,
            i,
            drawing.page,
        );
    }
    dictionary(&mut out, dict, root);
    for ((name, _), id) in w.groups.iter().zip(&group_ids) {
        out.tag(3, name);
        out.handle(350, *id);
    }
    for ((name, handles), id) in w.groups.iter().zip(group_ids) {
        out.tag(0, "GROUP");
        out.handle(5, id);
        out.handle(330, dict);
        out.tag(100, "AcDbGroup");
        out.tag(300, name);
        out.tag(70, 0);
        out.tag(71, 1);
        for h in handles {
            out.handle(340, *h);
        }
    }
    out.tag(0, "ENDSEC");
    out.tag(0, "EOF");
    out.0
        .replace("__HANDSEED__", &format!("{:X}", w.next))
        .into_bytes()
}
/// R2000+ layouts have reciprocal links with their BLOCK_RECORDs.
fn layout(t: &mut Tags, id: u64, owner: u64, block: u64, name: &str, tab: usize, page: P) {
    t.tag(0, "LAYOUT");
    t.handle(5, id);
    t.handle(330, owner);
    t.tag(100, "AcDbPlotSettings");
    t.tag(1, "");
    t.tag(4, "UserDefinedMetric");
    t.tag(6, "");
    for code in [40, 41, 42, 43, 46, 47, 48, 49, 140, 141] {
        t.tag(code, 0);
    }
    t.tag(44, page[0]);
    t.tag(45, page[1]);
    t.tag(142, 1);
    t.tag(143, 1);
    t.tag(70, if tab == 0 { 1024 } else { 0 });
    t.tag(72, 1);
    t.tag(73, 0);
    t.tag(74, 5);
    t.tag(7, "");
    t.tag(75, 16);
    t.tag(76, 0);
    t.tag(77, 2);
    t.tag(78, 300);
    t.tag(147, 1);
    t.tag(148, 0);
    t.tag(149, 0);
    t.tag(100, "AcDbLayout");
    t.tag(1, name);
    t.tag(70, 1);
    t.tag(71, tab);
    t.tag(10, 0);
    t.tag(20, 0);
    t.tag(11, page[0]);
    t.tag(21, page[1]);
    t.point(12, [0., 0.]);
    t.point(14, [0., 0.]);
    t.point(15, page);
    t.tag(146, 0);
    t.point(13, [0., 0.]);
    t.point(16, [1., 0.]);
    t.point(17, [0., 1.]);
    t.tag(76, 1);
    t.handle(330, block);
}
fn dictionary(t: &mut Tags, h: u64, owner: u64) {
    t.tag(0, "DICTIONARY");
    t.handle(5, h);
    t.handle(330, owner);
    t.tag(100, "AcDbDictionary");
    t.tag(281, 1);
}

impl Writer {
    fn hatch(&mut self, loops: &[Vec<P>], block: Option<usize>, phase: f64) {
        let (_, mut t) = self.entity("HATCH", HATCH, block);
        t.tag(100, "AcDbHatch");
        t.point(10, [0., 0.]);
        t.tag(210, 0);
        t.tag(220, 0);
        t.tag(230, 1);
        t.tag(2, "ANSI31");
        t.tag(70, 0);
        t.tag(71, 0);
        t.tag(91, loops.len());
        for polygon in loops {
            t.tag(92, 2);
            t.tag(72, 0);
            t.tag(73, 1);
            let n = polygon.len() - 1;
            t.tag(93, n);
            for p in &polygon[..n] {
                t.tag(10, p[0]);
                t.tag(20, p[1]);
            }
            t.tag(97, 0);
        }
        t.tag(75, 0);
        t.tag(76, 1);
        // ANSI31 already has a 45° base line; group 52 is the additional
        // rotation applied when CAD regenerates the named pattern.
        t.tag(52, 0);
        t.tag(41, crate::section::HATCH_SPACING / 2f64.sqrt() / 3.175);
        t.tag(77, 0);
        t.tag(78, 1);
        t.tag(53, 45);
        t.tag(43, 0);
        t.tag(44, phase);
        t.tag(45, 0);
        t.tag(46, crate::section::HATCH_SPACING);
        t.tag(79, 0);
        t.tag(98, 0);
        self.put(t, block);
    }
}
/// Clip known closed polygons to a half-section rectangle without changing painted ink.
pub(crate) fn clip_loops(loops: Vec<Vec<P>>, rect: [f64; 4]) -> Vec<Vec<P>> {
    loops
        .into_iter()
        .filter_map(|mut p| {
            if p.first() != p.last() {
                return None;
            }
            p.pop();
            for (axis, limit, sign) in [
                (0, rect[0], 1.),
                (0, rect[2], -1.),
                (1, rect[1], 1.),
                (1, rect[3], -1.),
            ] {
                let mut out = Vec::new();
                if p.is_empty() {
                    return None;
                }
                for i in 0..p.len() {
                    let a = p[i];
                    let b = p[(i + 1) % p.len()];
                    let ia = (a[axis] - limit) * sign >= 0.;
                    let ib = (b[axis] - limit) * sign >= 0.;
                    if ia {
                        out.push(a);
                    }
                    if ia != ib {
                        let t = (limit - a[axis]) / (b[axis] - a[axis]);
                        out.push([a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])]);
                    }
                }
                p = out;
            }
            if p.len() < 3 {
                return None;
            }
            p.push(p[0]);
            Some(p)
        })
        .collect()
}

fn text_font(r: &TextRun) -> &str {
    &r.font
}

fn encode_mtext(s: &str) -> String {
    let mut out = String::new();
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '{' => out.push_str("\\{"),
            '}' => out.push_str("\\}"),
            _ => out.push_str(&encode(&c.to_string())),
        }
    }
    out
}

impl Writer {
    fn group(&mut self, name: String, mut handles: Vec<u64>) {
        if let Some((_, existing)) = self.groups.iter_mut().find(|(n, _)| *n == name) {
            existing.append(&mut handles);
            existing.sort_unstable();
            existing.dedup();
        } else if !handles.is_empty() {
            handles.sort_unstable();
            handles.dedup();
            self.groups.push((name, handles));
        }
    }
}

fn with_reactors(tags: &Tags, reactors: &BTreeMap<u64, Vec<u64>>) -> Tags {
    let mut out = Tags::default();
    let mut handle = 0;
    let lines: Vec<_> = tags.0.lines().collect();
    for pair in lines.as_chunks::<2>().0 {
        let code = pair[0].trim().parse::<i32>().expect("writer tag");
        if code == 5 {
            handle = u64::from_str_radix(pair[1], 16).expect("writer handle");
        }
        if code == 330
            && let Some(groups) = reactors.get(&handle)
        {
            out.tag(102, "{ACAD_REACTORS");
            for g in groups {
                out.handle(330, *g);
            }
            out.tag(102, "}");
        }
        out.tag(code, pair[1]);
    }
    out
}

fn hatch_phase(loops: &[Vec<P>]) -> f64 {
    loops
        .iter()
        .flatten()
        .map(|p| p[1])
        .fold(f64::INFINITY, f64::min)
        - loops
            .iter()
            .flatten()
            .map(|p| p[0])
            .fold(f64::NEG_INFINITY, f64::max)
}
/// Kernel section curves are individual face intersections, not ordered wires.
/// Join only unambiguous endpoint chains. An open or branching boundary stays lines.
fn closed_boundaries(paths: &[Vec<P>]) -> Vec<Vec<P>> {
    let near = |a: P, b: P| (a[0] - b[0]).hypot(a[1] - b[1]) <= 1e-5;
    let mut remaining: Vec<_> = paths.iter().filter(|p| p.len() >= 2).cloned().collect();
    let mut out = Vec::new();
    while !remaining.is_empty() {
        let mut chain = remaining.remove(0);
        while !near(chain[0], *chain.last().unwrap()) {
            let end = *chain.last().unwrap();
            let matches: Vec<_> = remaining
                .iter()
                .enumerate()
                .filter_map(|(i, p)| {
                    if near(end, p[0]) {
                        Some((i, false))
                    } else if near(end, *p.last().unwrap()) {
                        Some((i, true))
                    } else {
                        None
                    }
                })
                .collect();
            let [(i, reverse)] = matches.as_slice() else {
                return Vec::new();
            };
            let mut next = remaining.remove(*i);
            if *reverse {
                next.reverse();
            }
            chain.extend(next.into_iter().skip(1));
        }
        if chain.len() < 4 {
            return Vec::new();
        }
        *chain.last_mut().unwrap() = chain[0];
        out.push(chain);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn wrapped_callout_midpoint_includes_continuation_ink_but_not_its_leader() {
        let items = [
            Item::Glyph(vec![vec![Curve::Line([10., 10.], [20., 14.])]], 3),
            Item::Glyph(vec![vec![Curve::Line([12., 4.], [18., 8.])]], 4),
            Item::Edge(Curve::Line([0., 0.], [15., 12.])),
            Item::Glyph(vec![vec![Curve::Line([0., 0.], [100., 100.])]], 9),
        ];
        assert_eq!(
            dimension_text_box(&items, &[3, 4], [10., 10., 20., 14.]),
            [10., 4., 20., 14.]
        );
    }
    #[test]
    fn hatch_boundaries_join_reversed_face_intersections_and_reject_open_chains() {
        let p = vec![
            vec![[0., 0.], [10., 0.]],
            vec![[10., 10.], [10., 0.]],
            vec![[0., 10.], [10., 10.]],
            vec![[0., 0.], [0., 10.]],
        ];
        let loops = closed_boundaries(&p);
        assert_eq!(loops.len(), 1);
        assert_eq!(loops[0].len(), 5);
        assert_eq!(loops[0].first(), loops[0].last());
        assert!(closed_boundaries(&p[..3]).is_empty());
        let half = clip_loops(loops, [0., 0., 5., 10.]);
        assert!(half[0].iter().all(|p| p[0] <= 5.));
    }
}

#[cfg(test)]
mod tolerance_tests {
    use super::{Dimension, live_label, number_range, printed_value_matches};

    #[test]
    fn displayed_precision_does_not_freeze_the_measured_primary_value() {
        for printed in [48., 47.6, 47.63, 47.625] {
            assert!(printed_value_matches(47.625, printed));
        }
        assert!(printed_value_matches(2.5, 2.));
        assert!(!printed_value_matches(47.625, 47.));
        assert!(!printed_value_matches(0.2, 0.5));
    }

    #[test]
    fn compound_terms_distinguish_repetition_counts_from_measured_values() {
        for (term, value) in [
            ("2× 47.625", "47.625"),
            ("3×72.39", "72.39"),
            ("2× 143.158°", "143.158"),
            ("(2× 135°)", "135"),
            ("4× ⌀6.6 THRU", "6.6"),
            ("⌀2× 12.593 LONG × 180° ARC", "2"),
        ] {
            assert_eq!(&term[number_range(term).unwrap()], value);
        }
    }

    #[test]
    fn dimension_override_keeps_prefix_tolerance_and_depth_words() {
        let dim = |label: &str, primary| Dimension {
            kind: "callout".into(),
            view: String::new(),
            value: 20.,
            label: label.into(),
            placed: None,
            diameter: true,
            radial: false,
            primary: Some(primary),
            primary_in: None,
        };
        assert_eq!(
            live_label(&dim("⌀20 +0.05/-0.1 ↧ 45", 3..5), 3, false),
            "%%c<> +0.05/-0.1 DEEP 45"
        );
        assert_eq!(live_label(&dim("7× R50", 5..7), 4, false), "7X R<>");
        assert_eq!(
            live_label(&dim("25 ±0.15 THRU", 0..2), 0, false),
            "<> %%p0.15 THRU"
        );
    }

    #[test]
    fn inch_values_without_a_leading_zero_and_angles_stay_live() {
        assert_eq!(number_range("⌀.236 THRU"), Some(3..7));
        assert_eq!(number_range("2× R.197"), Some(5..9));
        let angle = Dimension {
            kind: "angle".into(),
            view: String::new(),
            value: 30.,
            label: "30°".into(),
            placed: None,
            diameter: false,
            radial: false,
            primary: Some(0..2),
            primary_in: None,
        };
        // Degrees are degrees on an inch sheet too.
        assert_eq!(live_label(&angle, 5, true), "<>");
    }
}
