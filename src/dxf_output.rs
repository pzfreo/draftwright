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
    pub hatches: Vec<HatchRegion>,
    pub cutting: Vec<Range<usize>>,
    pub views: Vec<ViewItems>,
    pub annotations: BTreeMap<String, Range<usize>>,
    pub furniture: BTreeMap<String, Range<usize>>,
    pub dimensions: BTreeMap<String, PlannedDimension>,
    pub title: Range<usize>,
    pub labels: Vec<TextRun>,
    pub title_fields: Vec<(String, TextRun)>,
}
#[derive(Clone, Debug, Default, PartialEq)]
pub struct HatchRegion {
    pub view: String,
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
        if let Some(weight) = self.weight {
            t.tag(370, weight);
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
    fn curve(&mut self, c: &Curve, layer: &str, block: Option<usize>) -> u64 {
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
                        (*y_axis, radii[1], radii[0], -std::f64::consts::FRAC_PI_2)
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
                let pieces = match c {
                    Curve::Beziers(p) => p
                        .iter()
                        .map(|p| p.iter().map(|q| [q[0], q[1], 1.]).collect())
                        .collect(),
                    Curve::Rational(p) => p.clone(),
                    _ => unreachable!(),
                };
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
        h
    }
    fn item(&mut self, i: &Item, layer: &str, block: Option<usize>) -> Vec<u64> {
        match i {
            Item::Glyph(..) | Item::TextStroke(..) => Vec::new(),
            Item::Edge(c) => vec![self.curve(c, layer, block)],
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
                        .map(|c| self.curve(c, layer, block))
                        .collect()
                }
            }
        }
    }
    fn text(
        &mut self,
        r: &TextRun,
        layer: &str,
        block: Option<usize>,
        attr: Option<(&str, bool)>,
    ) -> u64 {
        let multiline =
            (r.text.contains('\n') || r.text.chars().any(symbol_font)) && attr.is_none();
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
        t.point(10, r.at);
        t.tag(40, height);
        let content = if multiline {
            encode_mtext(&r.text)
        } else {
            encode(&r.text)
        };
        if multiline {
            let parts: Vec<_> = content.as_bytes().chunks(250).collect();
            for (i, part) in parts.iter().enumerate() {
                t.tag(
                    if i + 1 == parts.len() { 1 } else { 3 },
                    std::str::from_utf8(part).expect("ASCII DXF encoding"),
                );
            }
            if parts.is_empty() {
                t.tag(1, "");
            }
        } else {
            t.tag(1, content);
        }
        t.tag(7, style(&r.font));
        if multiline {
            t.tag(
                71,
                match (r.h_align, r.v_align) {
                    (HAlign::Left, _) => 4,
                    (HAlign::Center, _) => 5,
                    (HAlign::Right, _) => 6,
                },
            );
            t.tag(50, r.rotation.to_radians());
            t.tag(41, 0);
        } else {
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
        h
    }
}
fn font_data(font: &str) -> Option<&'static [u8]> {
    match font {
        "IBMPlexMono-Regular.ttf" => Some(crate::api::MONO),
        crate::title_block::FONT => Some(crate::title_block::FONT_DATA),
        _ => None,
    }
}
fn style(font: &str) -> String {
    font.trim_end_matches(".ttf").to_string()
}
fn encode(s: &str) -> String {
    let mut out = String::new();
    for c in s.chars() {
        match c {
            '⌀' | 'ø' => out.push_str("%%c"),
            '°' => out.push_str("%%d"),
            '±' => out.push_str("%%p"),
            '\n' => out.push_str("\\P"),
            c if !c.is_ascii() => {
                for u in c.encode_utf16(&mut [0; 2]) {
                    write!(out, "\\U+{u:04X}").unwrap();
                }
            }
            _ => out.push(c),
        }
    }
    out
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
fn dimension(d: &PlannedDimension, drawing: &Drawing) -> Option<(i32, Vec<P>, f64)> {
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
        if d.label.contains('⌀')
            && d.measurements
                .iter()
                .any(|m| m.parameter.contains(".diameter") && (m.value - d.value).abs() < 1e-5)
        {
            return Some((3, vec![ends[0], ends[1]], 1. / frame.scale));
        }
        return Some((
            if aligned { 1 } else { 0 },
            vec![ends[0], ends[1], p.arrows[0][0]],
            1. / frame.scale,
        ));
    }
    // A manufacturing label can override a proven primary radius/diameter.
    // Its additional requirements stay editable in the same DIMENSION text.
    let diameter = d.label.contains('⌀')
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
        let opposite = [2. * circle.0[0] - tip[0], 2. * circle.0[1] - tip[1]];
        return Some((
            if radial { 4 } else { 3 },
            vec![if radial { circle.0 } else { opposite }, tip],
            1. / frame.scale,
        ));
    }
    None
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
                        w.item(item, name, Some(block));
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
    let mut styles: BTreeMap<String, (f64, u8, bool)> = BTreeMap::new();
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
        if let Some(l) = drawing.layers.get(2) {
            for i in range.clone() {
                if let Some(item) = l.items.get(i) {
                    handles.extend(w.item(item, layer, block));
                    consumed.insert((2, i));
                }
            }
        }
        for &r in &a.runs {
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
                handles.push(h);
                runs.insert(r);
            }
        }
        if let (Some((d, (kind, points, factor))), Some(block)) = (native, block) {
            let precision = drawing.sheet.precision.get(&a.name).copied();
            let name = format!("DW_{}_{:?}", factor.to_bits(), precision);
            styles.insert(
                name.clone(),
                (factor, precision.unwrap_or(3), precision.is_some()),
            );
            let (_, mut t) = w.entity("DIMENSION", DIMS, None);
            t.tag(100, "AcDbDimension");
            t.tag(280, 0);
            t.tag(2, &w.blocks[block].name);
            t.point(10, if kind <= 1 { points[2] } else { points[0] });
            let at = a
                .runs
                .first()
                .and_then(|r| drawing.text.get(*r))
                .map_or(d.placed.as_ref().unwrap().label_at, |r| r.at);
            t.point(11, at);
            t.tag(70, kind | 32 | 128);
            t.tag(71, 5);
            // Keep a numeric value live. Compound labels and requirements use an override
            // on the DIMENSION itself; there is no orphaned MTEXT to lose on a move.
            let numeric = d.label.trim().trim_end_matches('°');
            let numeric = if kind == 3 {
                numeric.trim_start_matches('⌀')
            } else if kind == 4 {
                numeric.trim_start_matches('R')
            } else {
                numeric
            };
            let plain = numeric.parse::<f64>().is_ok();
            t.tag(
                1,
                if plain {
                    "<>".into()
                } else {
                    encode_mtext(&d.label)
                },
            );
            t.tag(3, name);
            t.tag(53, d.placed.as_ref().unwrap().rotation);
            match kind {
                0 | 1 => {
                    t.tag(100, "AcDbAlignedDimension");
                    t.point(13, points[0]);
                    t.point(14, points[1]);
                    if kind == 0 {
                        t.tag(50, d.placed.as_ref().unwrap().rotation);
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
                    t.tag(40, 0);
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
            t.tag(1001, "DRAFTWRIGHT");
            t.tag(1000, &a.name);
            t.tag(1000, &d.kind);
            w.put(t, None);
        } else if !handles.is_empty() {
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
                        handles.push(h);
                        runs.insert(*r);
                    }
                }
            }
            w.group(name.clone(), handles);
        }
    }
    if let Some(l) = drawing.layers.get(2) {
        for region in &drawing.sheet.dxf.hatches {
            let closed = !region.loops.is_empty()
                && region.loops.iter().all(|p| {
                    p.len() >= 4
                        && p.first()
                            .zip(p.last())
                            .is_some_and(|(a, b)| (a[0] - b[0]).hypot(a[1] - b[1]) < 1e-6)
                });
            let block = w
                .blocks
                .iter()
                .position(|b| b.name == safe_name(&region.view));
            if closed {
                w.hatch(&region.loops, block);
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
        for range in &drawing.sheet.dxf.cutting {
            for i in range.clone() {
                if let Some(item) = l.items.get(i) {
                    w.item(item, CUT, None);
                    consumed.insert((2, i));
                }
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
            w.curve(&Curve::Line(*a, *b), BORDER, Some(title));
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
    let border = w.block("SHEET_BORDER".into(), [0., 0.]);
    let mut border_ink = false;
    for (li, l) in drawing.layers.iter().enumerate() {
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
fn finish(mut w: Writer, drawing: &Drawing, styles: BTreeMap<String, (f64, u8, bool)>) -> Vec<u8> {
    let root = w.id();
    let dict = w.id();
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
    use sha2::Digest;
    let digest = sha2::Sha256::digest(drawing.spec.as_deref().unwrap_or("").as_bytes());
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
    out.tag(2, "TABLES");
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
        if let Some(rgb) = match name {
            HIDDEN => Some(0x7f7f7f),
            DIMS | TEXT | PMI | HATCH | CUT => Some(0x0033b2),
            _ => None,
        } {
            out.tag(420, rgb);
        }
        out.tag(6, linetype);
        out.tag(290, if name == "Defpoints" { 0 } else { 1 });
        out.tag(370, weight);
    }
    out.tag(0, "ENDTAB");
    let st = w.id();
    let mut fonts: BTreeSet<_> = drawing
        .text
        .iter()
        .chain(&drawing.sheet.dxf.labels)
        .map(|r| r.font.clone())
        .collect();
    fonts.insert("IBMPlexMono-Regular.ttf".into());
    table(&mut out, "STYLE", st, fonts.len() + 1);
    let mut textstyle = 0;
    for (name, file) in std::iter::once((
        "STANDARD".to_string(),
        "IBMPlexMono-Regular.ttf".to_string(),
    ))
    .chain(fonts.iter().map(|f| (style(f), f.clone())))
    {
        let id = w.id();
        if name == "IBMPlexMono-Regular" {
            textstyle = id;
        }
        record(&mut out, "STYLE", id, st, "AcDbTextStyleTableRecord", &name);
        out.tag(40, 0);
        out.tag(41, 1);
        out.tag(50, 0);
        out.tag(71, 0);
        out.tag(42, 3);
        out.tag(3, file);
        out.tag(4, "");
    }
    out.tag(0, "ENDTAB");
    let ds = w.id();
    table(&mut out, "DIMSTYLE", ds, styles.len() + 1);
    out.tag(100, "AcDbDimStyleTable");
    out.tag(71, 0);
    for (name, (factor, precision, fixed)) in
        std::iter::once(("STANDARD".to_string(), (1., 3, false))).chain(styles)
    {
        record(
            &mut out,
            "DIMSTYLE",
            w.id(),
            ds,
            "AcDbDimStyleTableRecord",
            &name,
        );
        out.tag(40, 1);
        out.tag(41, crate::dims::ARROW);
        out.tag(42, crate::dims::GAP);
        out.tag(44, 1.);
        out.tag(140, crate::dims::TEXT * 0.698);
        out.tag(144, factor);
        out.tag(147, 0.8);
        out.tag(271, precision);
        out.tag(179, precision);
        out.tag(78, if fixed { 0 } else { 8 });
        out.tag(79, if fixed { 0 } else { 2 });
        out.tag(277, 2);
        out.tag(275, 0);
        out.tag(280, 0);
        out.tag(289, 3);
        out.handle(340, textstyle);
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
    for b in &w.blocks {
        record(
            &mut out,
            "BLOCK_RECORD",
            b.record,
            br,
            "AcDbBlockTableRecord",
            &b.name,
        );
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
    out.0.into_bytes()
}
fn dictionary(t: &mut Tags, h: u64, owner: u64) {
    t.tag(0, "DICTIONARY");
    t.handle(5, h);
    t.handle(330, owner);
    t.tag(100, "AcDbDictionary");
    t.tag(281, 1);
}

impl Writer {
    fn hatch(&mut self, loops: &[Vec<P>], block: Option<usize>) {
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
        t.tag(52, 45);
        t.tag(41, crate::section::HATCH_SPACING / 2f64.sqrt() / 3.175);
        t.tag(77, 0);
        t.tag(78, 1);
        t.tag(53, 45);
        t.tag(43, 0);
        let min_y = loops
            .iter()
            .flatten()
            .map(|p| p[1])
            .fold(f64::INFINITY, f64::min);
        let max_x = loops
            .iter()
            .flatten()
            .map(|p| p[0])
            .fold(f64::NEG_INFINITY, f64::max);
        t.tag(44, min_y - max_x);
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

/// Plex does not contain these engineering characters. MTEXT allows a local
/// font fallback while keeping the text style and the surrounding text in Plex.
fn encode_mtext(s: &str) -> String {
    let mut out = String::new();
    for c in s.chars() {
        if symbol_font(c) {
            write!(out, "{{\\fArial Unicode MS|b0|i0;\\U+{:04X}}}", c as u32).unwrap();
        } else {
            match c {
                '\\' => out.push_str("\\\\"),
                '{' => out.push_str("\\{"),
                '}' => out.push_str("\\}"),
                _ => out.push_str(&encode(&c.to_string())),
            }
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

fn symbol_font(c: char) -> bool {
    (c as u32) >= 0x2100 && c != '⌀'
}
