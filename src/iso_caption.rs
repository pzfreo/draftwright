//! The isometric's required scale disclaimer, planned before free late furniture.
//! Python: projection._fit_iso_view, builder's post-fit furniture, and
//! annotations._placement_occupancy.place_iso_nts_note.

use serde::{Deserialize, Serialize};

use crate::dims::{Placed, PlannedDimension};
use crate::items::State;
use crate::model::{Analysis, NTS_CAPTION, NTS_CAPTION_ID, Plan, PlannedSheet, PlannedView};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PlannedCaption {
    pub id: String,
    pub view: String,
    pub sheet: u32,
    pub text: String,
    pub height: f64,
    /// Centre of the caption's ink, in paper mm.
    pub at: [f64; 2],
    pub ink: [f64; 4],
    pub default: bool,
    pub basis: String,
    pub state: State,
}

pub(crate) fn proxy(c: &PlannedCaption) -> PlannedDimension {
    PlannedDimension {
        callout_offset: None,
        id: c.id.clone(),
        kind: "caption".into(),
        view: c.view.clone(),
        label: c.text.clone(),
        value: 0.0,
        reference: false,
        determined_by: Vec::new(),
        region: None,
        terms: Vec::new(),
        measurements: Vec::new(),
        default: true,
        from_spec: Vec::new(),
        basis: c.basis.clone(),
        state: State::Shown,
        placed: Some(Placed {
            lines: Vec::new(),
            arrows: Vec::new(),
            label_at: c.at,
            rotation: 0.0,
            label_box: c.ink,
        }),
    }
}

fn view_box(analysis: &Analysis, v: &PlannedView) -> [f64; 4] {
    if v.id == "v:iso" && v.direction == crate::model::VIEWS[3].1 {
        let b = analysis.drawing_iso_bounds();
        [
            v.at[0] + v.scale * b[0],
            v.at[1] + v.scale * b[1],
            v.at[0] + v.scale * b[2],
            v.at[1] + v.scale * b[3],
        ]
    } else {
        let (size, _) = crate::detail::crop_extent(analysis, v);
        [
            v.at[0] - v.scale * size[0] / 2.,
            v.at[1] - v.scale * size[1] / 2.,
            v.at[0] + v.scale * size[0] / 2.,
            v.at[1] + v.scale * size[1] / 2.,
        ]
    }
}

pub(crate) fn plan(
    analysis: &Analysis,
    sheet: &PlannedSheet,
    views: &[PlannedView],
    ink: &[PlannedDimension],
) -> Vec<PlannedCaption> {
    let Some(iso) = views
        .iter()
        .find(|v| {
            v.id == "v:iso"
                && v.sheet == sheet.number
                && (v.scale / sheet.scale_factor - 1.0).abs() >= 1e-6
        })
        .filter(|_| analysis.drawing_iso_bounds() != [0.0; 4])
    else {
        return Vec::new();
    };
    let mut obstacles: Vec<_> = views
        .iter()
        .filter(|v| v.sheet == sheet.number)
        .map(|v| {
            let b = view_box(analysis, v);
            if v.id == iso.id {
                b
            } else {
                // The caption belongs to the isometric's composed footprint.
                // A clear silhouette does not release a neighbouring view's
                // reserved ladder band for that footprint to occupy.
                [
                    b[0].min(v.area[0]),
                    b[1].min(v.area[1]),
                    b[2].max(v.area[2]),
                    b[3].max(v.area[3]),
                ]
            }
        })
        .collect();
    obstacles.push(sheet.title_block);
    obstacles.extend(crate::sheet_fit::furniture_bounds(analysis, sheet, views));
    obstacles.extend(section_text_obstacles(analysis, sheet, views));
    let pad = 1.2f64.max(crate::dims::ARROW / 2.0);
    for d in ink.iter().filter(|d| {
        views
            .iter()
            .find(|v| v.id == d.view)
            .is_none_or(|v| v.sheet == sheet.number)
    }) {
        let Some(p) = &d.placed else { continue };
        obstacles.push(p.label_box);
        // A PMI item's rendered ink box spans its glyph and leader together.
        // Keep the caption outside that whole item, including the empty corner of
        // a diagonal shaft's box, as the rendered caption audit requires.
        if d.id.starts_with("pmi:") {
            obstacles.extend(crate::dims::ink_box(d));
        }
        obstacles.extend(p.lines.iter().chain(&p.arrows).map(|[a, b]| {
            [
                a[0].min(b[0]) - pad,
                a[1].min(b[1]) - pad,
                a[0].max(b[0]) + pad,
                a[1].max(b[1]) + pad,
            ]
        }));
    }
    let height = crate::section::FONT_SIZE;
    let laid = crate::dims::font().text(NTS_CAPTION, height);
    let size = [laid.max[0] - laid.min[0], laid.max[1] - laid.min[1]];
    let at = crate::model::nts_caption_at(
        sheet.page,
        view_box(analysis, iso),
        iso.at[0],
        size,
        &obstacles,
    );
    vec![PlannedCaption {
        id: NTS_CAPTION_ID.into(),
        view: iso.id.clone(),
        sheet: iso.sheet,
        text: NTS_CAPTION.into(),
        height,
        at,
        ink: [
            at[0] - size[0] / 2.0,
            at[1] - size[1] / 2.0,
            at[0] + size[0] / 2.0,
            at[1] + size[1] / 2.0,
        ],
        default: true,
        basis: "the drawn isometric scale differs from the sheet scale".into(),
        state: State::Shown,
    }]
}

/// Text and cutting-plane furniture use the same font, anchors and settled line work as
/// rendering, including section references whose view lives on another sheet. The fill
/// estimator's conservative text metrics are deliberately not used for these labels.
pub(crate) fn section_text_obstacles(
    analysis: &Analysis,
    sheet: &PlannedSheet,
    views: &[PlannedView],
) -> Vec<[f64; 4]> {
    let text_box = |text: &str, at: [f64; 2]| {
        let laid = crate::dims::font().text(text, crate::section::FONT_SIZE);
        [
            at[0] + laid.min[0],
            at[1] + laid.min[1],
            at[0] + laid.max[0],
            at[1] + laid.max[1],
        ]
    };
    let mut out = Vec::new();
    for v in views {
        if v.sheet == sheet.number {
            // Invariant: Planned cameras come from the fixed views or checked section/detail cameras, with nonparallel axes.
            let view = haecceity::hlr::View::new(v.direction, v.up).expect("planned view");
            let half = 0.5 * crate::model::extent(analysis, &view).0[1] * v.scale;
            if let Some(note) = &v.scale_note {
                let gap = if v.cut.is_some() { 17.0 } else { 7.0 };
                out.push(text_box(note, [v.at[0], v.at[1] - half - gap]));
            }
            if let Some(cut) = &v.cut {
                out.push(text_box(
                    &format!("SECTION {}–{}", cut.label, cut.label),
                    [v.at[0], v.at[1] - half - 7.0],
                ));
                if let Some(r) = &v.xref {
                    out.push(text_box(
                        &format!("FROM SHEET {}, ZONE {}", r.sheet, r.zone),
                        r.at,
                    ));
                }
            }
        }
        if let Some(cut) = &v.cut
            && let Some(marker) = views
                .iter()
                .find(|m| m.id == cut.marked_in && m.sheet == sheet.number)
            && let Some(work) = marker.line_work.iter().find(|w| w.kind == "cutting_plane")
            && let Some(crate::output::Curve::Line(a, b)) = work.curves.first()
        {
            let (ink, letters) = crate::section::cutting_plane(a[0], b[0], a[1]);
            out.extend(crate::output::items_bbox(&ink));
            out.extend(letters.map(|at| text_box(&cut.label, at)));
            if let Some(r) = &cut.xref {
                out.push(text_box(
                    &format!(
                        "SECTION {}–{}, SHEET {}, ZONE {}",
                        cut.label, cut.label, r.sheet, r.zone
                    ),
                    r.at,
                ));
            }
        }
    }
    out
}

/// Reconcile after measured refits, pinned placement, or allocation to another sheet.
/// Tables already avoided the reserved caption; include them here in case later recovery
/// changed a view. Never retain an obsolete NTS claim when a refit restores sheet scale.
pub(crate) fn refresh(analysis: &Analysis, p: &mut Plan) {
    let mut ink = p.dimensions.clone();
    ink.extend(crate::centrelines::obstacles(&p.views));
    ink.extend(p.gdt.iter().filter_map(crate::dims::gdt::proxy));
    // Balloons and their leaders are settled defining annotation ink.
    for b in &p.balloons {
        let mut d = proxy(&PlannedCaption {
            id: b.id.clone(),
            view: b.view.clone(),
            sheet: 1,
            text: b.tag.clone(),
            height: 3.0,
            at: b.at,
            ink: {
                let label = b.label_box.unwrap_or([b.at[0], b.at[1], b.at[0], b.at[1]]);
                [
                    label[0].min(b.at[0] - b.radius),
                    label[1].min(b.at[1] - b.radius),
                    label[2].max(b.at[0] + b.radius),
                    label[3].max(b.at[1] + b.radius),
                ]
            },
            default: true,
            basis: String::new(),
            state: State::Shown,
        });
        d.placed
            .as_mut()
            // Invariant: The proxy constructor always supplies a placement.
            .expect("balloon proxy")
            .lines
            .extend(b.leader);
        ink.push(d);
    }
    let mut captions: Vec<PlannedCaption> = std::iter::once(&p.sheet)
        .chain(&p.sheets)
        .flat_map(|sheet| {
            let mut local = ink.clone();
            if sheet.number == 1 {
                local.extend(
                    p.requirements
                        .iter()
                        .filter_map(crate::dims::gdt::finish_proxy),
                );
                local.extend(
                    p.radius_table
                        .iter()
                        .filter_map(|t| crate::tables::proxy(&t.table)),
                );
                local.extend(
                    p.notes_block
                        .iter()
                        .chain(&p.radius_notes_block)
                        .map(crate::notes::proxy),
                );
            }
            local.extend(
                p.tables
                    .iter()
                    .filter(|t| t.sheet == sheet.number)
                    .filter_map(crate::tables::proxy),
            );
            for t in p
                .hole_table
                .iter()
                .chain(&p.hole_tables)
                .filter(|t| t.sheet == sheet.number)
            {
                local.extend(t.blocks.iter().filter_map(crate::tables::proxy));
            }
            plan(analysis, sheet, &p.views, &local)
        })
        .collect();
    // Refresh the ink without discarding already published provenance for this same caption.
    for caption in &mut captions {
        if let Some(previous) = p.captions.iter().find(|old| old.id == caption.id) {
            caption.basis.clone_from(&previous.basis);
        }
    }
    p.captions = captions;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detail_obstacle_equals_its_crop_footprint() {
        let (_, a) = crate::model::analyse_native(
            &std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/parts/mounting_plate.step"),
        )
        .unwrap();
        let spec = serde_json::from_value(serde_json::json!({"sheet":{"size":"A2","scale":"1:2"},"views":{"detail_c":{"detail":{"around":"f:hole:f6","from":"plan","scale_factor":2}}}})).unwrap();
        let p = crate::model::plan(&a, &spec).unwrap();

        let v = p.views.iter().find(|v| v.id == "v:detail_c").unwrap();
        let mut crop_only = p.clone();
        crop_only.views = vec![v.clone()];
        let footprint = crate::sheet_fit::footprints(&a, &crop_only)[0];
        assert_eq!(view_box(&a, v), footprint);
    }

    #[test]
    fn a_section_reference_is_settled_ink_for_the_iso_caption() {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
        let (_, analysis) =
            crate::model::analyse_native(&root.join("tests/fixtures/parts/plate.step")).unwrap();
        let p = crate::model::plan(&analysis, &crate::model::Spec::default()).unwrap();
        let base = plan(&analysis, &p.sheet, &p.views, &[]);
        let at = base.first().expect("scaled iso fixture").at;
        let mut views = p.views.clone();
        let mut section = views.iter().find(|v| v.id == "v:front").unwrap().clone();
        section.id = "v:section_aa".into();
        section.cut = Some(crate::model::Cut {
            xref: None,
            label: "A".into(),
            origin: [0.0; 3],
            normal: [0.0, 1.0, 0.0],
            marked_in: "v:plan".into(),
        });
        section.xref = Some(crate::sheets::CrossRef {
            sheet: 2,
            zone: "B3".into(),
            at,
        });
        views.push(section);
        let chosen = plan(&analysis, &p.sheet, &views, &[]);
        assert_ne!(
            chosen[0].at, at,
            "the reference label occupies the natural caption position"
        );
        let text = crate::dims::font().text("FROM SHEET 2, ZONE B3", crate::section::FONT_SIZE);
        let b = [
            at[0] + text.min[0],
            at[1] + text.min[1],
            at[0] + text.max[0],
            at[1] + text.max[1],
        ];
        let c = chosen[0].ink;
        assert!(!(c[0] < b[2] && c[2] > b[0] && c[1] < b[3] && c[3] > b[1]));
    }
}
