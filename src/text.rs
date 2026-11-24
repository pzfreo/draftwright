//! Text as draftwright draws and measures its labels — what build123d's `Text` gives it through
//! OpenCascade's `Font_BRepTextBuilder`: each glyph's outline from the font file, laid out along
//! its line at the glyph's advance, lines a line spacing apart, the whole centred on its
//! bounding box (`Align.CENTER`).

use ttf_parser::{Face, GlyphId, OutlineBuilder};

pub type P = [f64; 2];

/// One piece of a glyph's outline: a line, or a quadratic or cubic Bézier by its control points.
#[derive(Clone, Debug, PartialEq)]
pub enum Segment {
    Line(P, P),
    Quad(P, P, P),
    Cubic(P, P, P, P),
}

impl Segment {
    pub fn at(&self, t: f64) -> P {
        let lerp = |a: P, b: P| [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
        match *self {
            Segment::Line(a, b) => lerp(a, b),
            Segment::Quad(a, b, c) => {
                let (ab, bc) = (lerp(a, b), lerp(b, c));
                lerp(ab, bc)
            }
            Segment::Cubic(a, b, c, d) => {
                let (ab, bc, cd) = (lerp(a, b), lerp(b, c), lerp(c, d));
                let (abc, bcd) = (lerp(ab, bc), lerp(bc, cd));
                lerp(abc, bcd)
            }
        }
    }

    /// The segment's box: its ends', widened to any extreme between them (where a coordinate's
    /// derivative vanishes).
    fn bounds(&self) -> [P; 2] {
        let mut b = [[f64::INFINITY; 2], [f64::NEG_INFINITY; 2]];
        let mut add = |p: P| {
            for k in 0..2 {
                b[0][k] = b[0][k].min(p[k]);
                b[1][k] = b[1][k].max(p[k]);
            }
        };
        add(self.at(0.0));
        add(self.at(1.0));
        for k in 0..2 {
            let roots: Vec<f64> = match *self {
                Segment::Line(..) => Vec::new(),
                Segment::Quad(a, b, c) => {
                    let d = a[k] - 2.0 * b[k] + c[k];
                    if d == 0.0 {
                        Vec::new()
                    } else {
                        vec![(a[k] - b[k]) / d]
                    }
                }
                Segment::Cubic(a, b, c, d) => {
                    // B'(t)/3 = qa t² + qb t + qc
                    let qa = -a[k] + 3.0 * b[k] - 3.0 * c[k] + d[k];
                    let qb = 2.0 * (a[k] - 2.0 * b[k] + c[k]);
                    let qc = b[k] - a[k];
                    if qa.abs() < 1e-15 {
                        if qb == 0.0 {
                            Vec::new()
                        } else {
                            vec![-qc / qb]
                        }
                    } else {
                        let disc = qb * qb - 4.0 * qa * qc;
                        if disc < 0.0 {
                            Vec::new()
                        } else {
                            let s = disc.sqrt();
                            vec![(-qb + s) / (2.0 * qa), (-qb - s) / (2.0 * qa)]
                        }
                    }
                }
            };
            for t in roots.into_iter().filter(|t| (0.0..=1.0).contains(t)) {
                add(self.at(t));
            }
        }
        b
    }
}

/// A closed loop of segments: a glyph's outer boundary or a hole in it.
pub type Contour = Vec<Segment>;

/// A string laid out: its glyphs' contours, and their bounding box.
#[derive(Clone, Debug)]
pub struct Laid {
    pub contours: Vec<Contour>,
    pub min: P,
    pub max: P,
}

/// A font file's data.
pub struct Font {
    data: Vec<u8>,
}

impl Font {
    pub fn new(data: Vec<u8>) -> Option<Self> {
        Face::parse(&data, 0).ok()?;
        Some(Font { data })
    }

    #[cfg(not(target_arch = "wasm32"))]
    pub fn open(path: &std::path::Path) -> Option<Self> {
        Font::new(std::fs::read(path).ok()?)
    }

    fn face(&self) -> Face<'_> {
        // Invariant: Font bytes are bundled at build time and parsed unchanged, independently of user input.
        Face::parse(&self.data, 0).expect("parsed in new")
    }

    /// Resolve the diameter sign through the bundled font's slashed-o outline, as the
    /// generated callout path already does. Both characters use the same advance in prose.
    pub(crate) fn glyph(face: &Face<'_>, c: char) -> Option<GlyphId> {
        face.glyph_index(c)
            .or_else(|| (c == '⌀').then(|| face.glyph_index('ø')).flatten())
    }

    /// Whether this text path can draw a character, including its explicit diameter fallback.
    pub fn supports(&self, c: char) -> bool {
        Self::glyph(&self.face(), c).is_some()
    }

    /// *text* at *size* (the em, in drawing units), centred on its bounding box. A character
    /// the font lacks is drawn as its missing-glyph box, as FreeType substitutes it.
    pub fn text(&self, text: &str, size: f64) -> Laid {
        let laid = self.baseline(text, size);
        if laid.contours.is_empty() {
            return Laid {
                contours: laid.contours,
                min: [0.0; 2],
                max: [0.0; 2],
            };
        }
        let (min, max) = (laid.min, laid.max);
        let centre = [0.5 * (min[0] + max[0]), 0.5 * (min[1] + max[1])];
        let shift = |p: P| [p[0] - centre[0], p[1] - centre[1]];
        let mut contours = laid.contours;
        for s in contours.iter_mut().flatten() {
            *s = match *s {
                Segment::Line(a, b) => Segment::Line(shift(a), shift(b)),
                Segment::Quad(a, b, c) => Segment::Quad(shift(a), shift(b), shift(c)),
                Segment::Cubic(a, b, c, d) => {
                    Segment::Cubic(shift(a), shift(b), shift(c), shift(d))
                }
            };
        }
        Laid {
            contours,
            min: shift(min),
            max: shift(max),
        }
    }

    /// The pen's advance over *text* (one line) at *size*: where the next character would start.
    pub fn advance(&self, text: &str, size: f64) -> f64 {
        let face = self.face();
        let scale = size / face.units_per_em() as f64;
        text.chars()
            .map(|c| {
                let glyph = Self::glyph(&face, c).unwrap_or(GlyphId(0));
                face.glyph_hor_advance(glyph).unwrap_or(0) as f64 * scale
            })
            .sum()
    }

    /// Baseline advance for multiline text, using the face's vertical metrics.
    pub fn line_spacing(&self, size: f64) -> f64 {
        let face = self.face();
        (face.ascender() as f64 - face.descender() as f64 + face.line_gap() as f64) * size
            / face.units_per_em() as f64
    }

    /// *text* at *size* as the pen lays it: the first line's baseline on y = 0, starting at
    /// x = 0, not centred. `min` and `max` are its ink's box (both zero when it has no ink).
    pub fn baseline(&self, text: &str, size: f64) -> Laid {
        let face = self.face();
        let scale = size / face.units_per_em() as f64;
        let line = self.line_spacing(size);
        let mut contours = Vec::new();
        for (row, chars) in text.split('\n').enumerate() {
            let mut pen = 0.0;
            for c in chars.chars() {
                let glyph = Self::glyph(&face, c).unwrap_or(GlyphId(0));
                let mut outline = Outline {
                    scale,
                    origin: [pen, -(row as f64) * line],
                    contours: Vec::new(),
                    start: [0.0; 2],
                    last: [0.0; 2],
                };
                face.outline_glyph(glyph, &mut outline);
                contours.extend(outline.contours.into_iter().filter(|c| !c.is_empty()));
                pen += face.glyph_hor_advance(glyph).unwrap_or(0) as f64 * scale;
            }
        }
        let (mut min, mut max) = ([f64::INFINITY; 2], [f64::NEG_INFINITY; 2]);
        for s in contours.iter().flatten() {
            let b = s.bounds();
            for k in 0..2 {
                min[k] = min[k].min(b[0][k]);
                max[k] = max[k].max(b[1][k]);
            }
        }
        if contours.is_empty() {
            (min, max) = ([0.0; 2], [0.0; 2]);
        }
        Laid { contours, min, max }
    }
}

/// Collects a glyph's outline, scaled and placed.
struct Outline {
    scale: f64,
    origin: P,
    contours: Vec<Contour>,
    start: P,
    last: P,
}

impl Outline {
    fn at(&self, x: f32, y: f32) -> P {
        [
            self.origin[0] + x as f64 * self.scale,
            self.origin[1] + y as f64 * self.scale,
        ]
    }

    fn push(&mut self, s: Segment, to: P) {
        if let Some(c) = self.contours.last_mut() {
            c.push(s);
        }
        self.last = to;
    }
}

impl OutlineBuilder for Outline {
    fn move_to(&mut self, x: f32, y: f32) {
        let p = self.at(x, y);
        self.contours.push(Vec::new());
        (self.start, self.last) = (p, p);
    }

    fn line_to(&mut self, x: f32, y: f32) {
        let p = self.at(x, y);
        if p != self.last {
            self.push(Segment::Line(self.last, p), p);
        }
    }

    fn quad_to(&mut self, x1: f32, y1: f32, x: f32, y: f32) {
        let (c, p) = (self.at(x1, y1), self.at(x, y));
        self.push(Segment::Quad(self.last, c, p), p);
    }

    fn curve_to(&mut self, x1: f32, y1: f32, x2: f32, y2: f32, x: f32, y: f32) {
        let (c1, c2, p) = (self.at(x1, y1), self.at(x2, y2), self.at(x, y));
        self.push(Segment::Cubic(self.last, c1, c2, p), p);
    }

    fn close(&mut self) {
        if self.last != self.start {
            let (from, to) = (self.last, self.start);
            self.push(Segment::Line(from, to), to);
        }
    }
}
