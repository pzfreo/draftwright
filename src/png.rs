//! The drawing as a PNG: the layers [`Drawing::pdf`] draws, rasterised at a resolution (draftwright
//! renders its PDF through PDFium at `dpi`, 150 by default, and refuses `dpi <= 0`). The pixels need
//! not match PDFium's; the page is the same size, `ceil(page × dpi)` pixels each way as PDFium makes
//! it, white, with each layer's lines and faces in its style (round caps, mitred joins, faces filled
//! non-zero and stroked, as in the PDF). The glyphs are the faces the drawing already holds; the
//! invisible text and the link are not drawn.
//!
//! The bytes depend only on the drawing and the resolution. tiny-skia rasterises on one thread
//! without SIMD (the scalar path, so no float differs between instruction sets); every point is put
//! on a 1/256-pixel grid before it is handed over, so a last-bit difference in the platform's sine
//! and cosine (arcs become cubics) does not reach the pixels, short of a point on a grid boundary.
//! The PNG is 8-bit RGB with fixed filtering and compression, its physical size in a `pHYs` chunk
//! and no time chunk. The spec is carried in an iTXt chunk keyed [`SPEC_KEY`], uncompressed, and
//! read back by [`spec`].

use tiny_skia::{
    Color, FillRule, LineCap, Paint, PathBuilder, Pixmap, Stroke, StrokeDash, Transform,
};

use crate::output::{Curve, Drawing, GENERATED_BY, Item, LINK, P, PathSeg, path_segments};

/// draftwright's default resolution, pixels per inch.
pub const DPI: f64 = 150.0;

/// The most pixels a PNG may have (a 1 GiB raster while it is drawn): A0 at 400 dpi fits.
pub const MAX_PIXELS: u64 = 1 << 28;

/// The keyword of the iTXt chunk that carries the spec, as the PDF's document information does.
pub const SPEC_KEY: &str = "DraftwrightSpec";

const SIGNATURE: &[u8] = b"\x89PNG\r\n\x1a\n";

/// The size in pixels of a page *page* (mm) at *dpi*, rounded up as PDFium rounds it. A page
/// that is a whole number of pixels to within 1e-9 of one is that number.
pub fn size(page: P, dpi: f64) -> [u64; 2] {
    page.map(|mm| (mm / 25.4 * dpi - 1e-9).ceil().max(1.0) as u64)
}

impl Drawing {
    /// The PNG at *dpi*, or why there is none: a *dpi* that is not positive, or a page too large
    /// at it.
    pub fn png(&self, dpi: f64) -> Result<Vec<u8>, String> {
        self.png_region(dpi, None)
    }
    /// Render a paper region directly, keeping the full drawing's embedded Spec.
    pub fn png_region(&self, dpi: f64, region: Option<[f64; 4]>) -> Result<Vec<u8>, String> {
        if !(dpi > 0.0 && dpi.is_finite()) {
            return Err(format!("png export needs dpi > 0, got {dpi}"));
        }
        let [left, bottom, right, top] = region.unwrap_or([0.0, 0.0, self.page[0], self.page[1]]);
        if ![left, bottom, right, top].iter().all(|x| x.is_finite())
            || left < 0.0
            || bottom < 0.0
            || right > self.page[0]
            || top > self.page[1]
            || right <= left
            || top <= bottom
        {
            return Err(format!(
                "region must be [left,bottom,right,top] within page {:?}, with positive width and height",
                self.page
            ));
        }
        let [w, h] = size([right - left, top - bottom], dpi);
        if w.checked_mul(h).is_none_or(|pixels| pixels > MAX_PIXELS) {
            return Err(format!(
                "a {w} × {h} pixel PNG at {dpi} dpi is more than {MAX_PIXELS} pixels; choose a lower dpi"
            ));
        }
        let mut pixmap = Pixmap::new(w as u32, h as u32)
            .ok_or_else(|| format!("cannot make a {w} × {h} pixel raster"))?;
        pixmap.fill(Color::WHITE);
        let k = dpi / 25.4;
        let page_h = top;
        // Page mm, y up, to pixels, y down, on the 1/256-pixel grid.
        let at = |p: P| {
            let q = |v: f64| ((v * 256.0).round() / 256.0) as f32;
            (q((p[0] - left) * k), q((page_h - p[1]) * k))
        };
        for layer in self.paint_layers() {
            let st = &layer.style;
            let mut paint = Paint::default();
            let [r, g, b] = st.colour;
            paint.set_color_rgba8(r, g, b, 255);
            paint.anti_alias = true;
            let mut stroke = Stroke {
                width: (st.width * k) as f32,
                line_cap: LineCap::Round,
                dash: st.dash.and_then(|pattern| {
                    StrokeDash::new(pattern.iter().map(|n| (n * k) as f32).collect(), 0.0)
                }),
                ..Stroke::default()
            };
            for item in &layer.items {
                if layer.name == "centres" {
                    stroke.dash = StrokeDash::new(
                        st.dash.unwrap().iter().map(|n| (n * k) as f32).collect(),
                        (crate::output::centre_phase(item) * k) as f32,
                    );
                }
                let mut pb = PathBuilder::new();
                let mut trace = |wire: &[Curve]| {
                    let (x, y) = at(wire[0].start());
                    pb.move_to(x, y);
                    for curve in wire {
                        for seg in path_segments(curve) {
                            match seg {
                                PathSeg::Line(b) => {
                                    let (x, y) = at(b);
                                    pb.line_to(x, y);
                                }
                                PathSeg::Cubic(c1, c2, b) => {
                                    let ((x1, y1), (x2, y2), (x, y)) = (at(c1), at(c2), at(b));
                                    pb.cubic_to(x1, y1, x2, y2, x, y);
                                }
                            }
                        }
                    }
                };
                match item {
                    Item::Edge(curve) => trace(std::slice::from_ref(curve)),
                    Item::Face(wires) => wires.iter().for_each(|w| trace(w)),
                }
                // A path of no extent (a curve whose ends and poles fall on one grid point) has
                // nothing to draw.
                let Some(path) = pb.finish() else { continue };
                if matches!(item, Item::Face(_)) && st.fill {
                    pixmap.fill_path(
                        &path,
                        &paint,
                        FillRule::Winding,
                        Transform::identity(),
                        None,
                    );
                }
                if st.width > 0. {
                    pixmap.stroke_path(&path, &paint, &stroke, Transform::identity(), None);
                }
            }
        }
        encode(&pixmap, dpi, self.spec.as_deref())
    }
}

/// *pixmap* (opaque) as an 8-bit RGB PNG at *dpi*, carrying *spec*.
fn encode(pixmap: &Pixmap, dpi: f64, spec: Option<&str>) -> Result<Vec<u8>, String> {
    let rgb: Vec<u8> = pixmap
        .data()
        .as_chunks::<4>()
        .0
        .iter()
        .flat_map(|&[r, g, b, _]| [r, g, b])
        .collect();
    let mut out = Vec::new();
    let mut encoder = ::png::Encoder::new(&mut out, pixmap.width(), pixmap.height());
    encoder.set_color(::png::ColorType::Rgb);
    encoder.set_depth(::png::BitDepth::Eight);
    encoder.set_compression(::png::Compression::Balanced);
    encoder.set_filter(::png::Filter::Adaptive);
    let per_metre = (dpi / 0.0254).round() as u32;
    encoder.set_pixel_dims(Some(::png::PixelDimensions {
        xppu: per_metre,
        yppu: per_metre,
        unit: ::png::Unit::Meter,
    }));
    let failed = |e: ::png::EncodingError| format!("cannot encode the PNG: {e}");
    encoder
        .add_text_chunk("Software".into(), "draftwright".into())
        .map_err(failed)?;
    encoder
        .add_text_chunk("Comment".into(), format!("{GENERATED_BY}: {LINK}"))
        .map_err(failed)?;
    if let Some(spec) = spec {
        encoder
            .add_itxt_chunk(SPEC_KEY.into(), spec.into())
            .map_err(failed)?;
    }
    let mut writer = encoder.write_header().map_err(failed)?;
    writer.write_image_data(&rgb).map_err(failed)?;
    writer.finish().map_err(failed)?;
    Ok(out)
}

/// Whether *file* is a PNG.
pub fn is_png(file: &[u8]) -> bool {
    file.starts_with(SIGNATURE)
}

/// The JSON of a PNG's spec: the text of its first iTXt chunk keyed [`SPEC_KEY`] (compressed or
/// not, so a tool that re-saves it compressed still reads), or none.
pub fn spec(file: &[u8]) -> Result<Option<String>, String> {
    let truncated = || "its PNG chunks are truncated".to_string();
    let mut rest = file.strip_prefix(SIGNATURE).ok_or("it is not a PNG")?;
    while !rest.is_empty() {
        let len = u32::from_be_bytes(rest.get(..4).ok_or_else(truncated)?.try_into().unwrap());
        let len = len as usize;
        let kind = rest.get(4..8).ok_or_else(truncated)?;
        let data = rest.get(8..8 + len).ok_or_else(truncated)?;
        rest = rest.get(12 + len..).ok_or_else(truncated)?;
        if kind == b"IEND" {
            break;
        }
        if kind != b"iTXt" {
            continue;
        }
        let mut fields = data.splitn(2, |&b| b == 0);
        if fields.next() != Some(SPEC_KEY.as_bytes()) {
            continue;
        }
        let broken = || format!("its {SPEC_KEY} chunk is not a valid iTXt chunk");
        let after = fields.next().ok_or_else(broken)?;
        let (&compressed, after) = after.split_first().ok_or_else(broken)?;
        let after = after.get(1..).ok_or_else(broken)?;
        // The language tag and the translated keyword, each ended by a zero.
        let mut fields = after.splitn(3, |&b| b == 0);
        let (_, _, text) = (
            fields.next(),
            fields.next(),
            fields.next().ok_or_else(broken)?,
        );
        let text = if compressed == 1 {
            use std::io::Read as _;
            let mut out = Vec::new();
            flate2::read::ZlibDecoder::new(text)
                .read_to_end(&mut out)
                .map_err(|_| broken())?;
            out
        } else {
            text.to_vec()
        };
        return String::from_utf8(text).map(Some).map_err(|_| broken());
    }
    Ok(None)
}
