//! Text inventory from the same font outlines and symbol strokes that paint the sheet.

use std::collections::BTreeSet;

use serde::Serialize;

use crate::dims::PlannedDimension;
use crate::output::{Curve, Drawing, Item, P, TEXT_PEN};
use crate::text::Segment;

type Bounds = [f64; 4];

#[derive(Clone, Debug, Default, PartialEq)]
pub struct TextIndex {
    pub(crate) runs: Vec<Source>,
    pub(crate) outline_labels: Vec<(Source, Bounds)>,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub(crate) struct Source {
    id: String,
    #[serde(flatten)]
    kind: Kind,
    #[serde(skip_serializing_if = "Option::is_none")]
    view: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum Kind {
    Dimension {
        features: BTreeSet<String>,
        model_value: f64,
    },
    Annotation,
    HoleTag,
    Balloon,
    TableCell {
        table_id: String,
        row: usize,
        column: usize,
    },
    Note,
    TitleBlock {
        field: String,
        is_label: bool,
    },
    Caption,
    ViewLabel,
    Zone,
    Scale,
}

impl Source {
    pub(crate) fn new(id: &str, kind: Kind) -> Self {
        Self {
            id: id.into(),
            kind,
            view: None,
        }
    }

    pub(crate) fn in_view(mut self, view: Option<&str>) -> Self {
        self.view = view.map(str::to_owned);
        self
    }

    pub(crate) fn dimension(d: &PlannedDimension) -> Self {
        Self::new(
            &d.id,
            Kind::Dimension {
                features: d.measurements.iter().map(|m| m.feature.clone()).collect(),
                model_value: d.value,
            },
        )
        .in_view(Some(&d.view))
    }

    pub(crate) fn cell(id: &str, row: usize, column: usize) -> Self {
        Self::new(
            id,
            Kind::TableCell {
                table_id: id.into(),
                row,
                column,
            },
        )
    }

    pub(crate) fn title(field: &str, is_label: bool) -> Self {
        Self::new(
            &format!("title_block/{field}"),
            Kind::TitleBlock {
                field: field.into(),
                is_label,
            },
        )
    }
}

#[derive(Serialize)]
struct Document<'a> {
    schema: &'static str,
    origin: &'static str,
    y_axis: &'static str,
    sheets: Vec<Sheet>,
    entries: Vec<Entry<'a>>,
}

#[derive(Serialize)]
struct Sheet {
    number: u32,
    size_mm: P,
}

#[derive(Serialize)]
struct Entry<'a> {
    #[serde(flatten)]
    source: &'a Source,
    text: &'a str,
    sheet: u32,
    bbox_mm: Bounds,
    bbox_pt: Bounds,
    rotation: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    svg_text_index: Option<usize>,
}

fn outline_bounds(curve: &Curve, mut include: impl FnMut(Bounds)) {
    if let Curve::Beziers(pieces) = curve {
        for poles in pieces {
            let segment = match poles.as_slice() {
                [a, b] => Segment::Line(*a, *b),
                [a, b, c] => Segment::Quad(*a, *b, *c),
                [a, b, c, d] => Segment::Cubic(*a, *b, *c, *d),
                _ => unreachable!("the glyph painter uses at most cubic curves"),
            };
            let [[x0, y0], [x1, y1]] = segment.bounds();
            include([x0, y0, x1, y1]);
        }
    } else {
        include(curve.bbox());
    }
}

fn entries(drawing: &Drawing) -> impl Iterator<Item = Entry<'_>> {
    let mut bounds = vec![None::<Bounds>; drawing.text.len()];
    for layer in &drawing.layers {
        for item in &layer.items {
            let (index, pad) = match *item {
                Item::Glyph(_, index) => (index, 0.),
                Item::TextStroke(_, index) => (index, layer.style.width / 2.),
                Item::FrameText(_, index) => (index, TEXT_PEN / 2.),
                _ => continue,
            };
            let Some(slot) = bounds.get_mut(index) else {
                continue;
            };
            let mut include = |[x0, y0, x1, y1]: Bounds| {
                let ink = [x0 - pad, y0 - pad, x1 + pad, y1 + pad];
                *slot = Some(slot.map_or(ink, |b| {
                    [
                        b[0].min(ink[0]),
                        b[1].min(ink[1]),
                        b[2].max(ink[2]),
                        b[3].max(ink[3]),
                    ]
                }));
            };
            match item {
                Item::Glyph(wires, _) => {
                    for curve in wires.iter().flatten() {
                        outline_bounds(curve, &mut include);
                    }
                }
                Item::TextStroke(curve, _) | Item::FrameText(curve, _) => include(curve.bbox()),
                _ => unreachable!(),
            }
        }
    }
    let index = &drawing.sheet.text_index;
    debug_assert_eq!(index.runs.len(), drawing.text.len());
    debug_assert_eq!(index.outline_labels.len(), drawing.sheet.dxf.labels.len());
    let searchable = drawing
        .text
        .iter()
        .zip(&index.runs)
        .zip(bounds)
        .enumerate()
        .map(|(i, ((run, source), bounds))| (run, source, bounds, Some(i)));
    let outlined = drawing
        .sheet
        .dxf
        .labels
        .iter()
        .zip(&index.outline_labels)
        .map(|(run, (source, bounds))| (run, source, Some(*bounds), None));
    searchable
        .chain(outlined)
        .map(move |(run, source, bounds, svg_text_index)| {
            let [x0, y0, x1, y1] = bounds.unwrap_or([run.at[0], run.at[1], run.at[0], run.at[1]]);
            let h = drawing.page[1];
            let mut bbox_mm = [x0, h - y1, x1, h - y0];
            if bounds.is_some() {
                // Enclose also the six-decimal coordinates used by the SVG painter.
                bbox_mm[0] = ((bbox_mm[0] - 1e-6) * 1e6).floor() / 1e6;
                bbox_mm[1] = ((bbox_mm[1] - 1e-6) * 1e6).floor() / 1e6;
                bbox_mm[2] = ((bbox_mm[2] + 1e-6) * 1e6).ceil() / 1e6;
                bbox_mm[3] = ((bbox_mm[3] + 1e-6) * 1e6).ceil() / 1e6;
            }
            Entry {
                source,
                text: &run.text,
                sheet: drawing.sheet.dxf.sheet_number,
                bbox_mm,
                bbox_pt: bbox_mm.map(|mm| mm * 72. / 25.4),
                rotation: -run.rotation,
                svg_text_index,
            }
        })
}

pub(crate) fn encode(drawings: &[Drawing]) -> Vec<u8> {
    let document = Document {
        schema: "draftwright.text/1",
        origin: "top-left",
        y_axis: "down",
        sheets: drawings
            .iter()
            .map(|d| Sheet {
                number: d.sheet.dxf.sheet_number,
                size_mm: d.page,
            })
            .collect(),
        entries: drawings.iter().flat_map(entries).collect(),
    };
    serde_json::to_vec(&document).expect("rendered text serializes to JSON")
}
