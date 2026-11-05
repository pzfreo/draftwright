//! Pack-only general assembly: the ordinary envelope/view/sheet/HLR pipeline, with
//! visible, source-owned item references and a bottom-up ISO 7573 parts list.
use std::collections::BTreeMap;
use std::path::Path;

use haecceity::geom;
use haecceity::hlr::View;
use haecceity::p21::{Attribute, Document, RawEntity};
use haecceity::rays::RayCaster;
use haecceity::step::StepFile;
use serde::{Deserialize, Serialize};

use super::{Finding, Row, Supply, finding};
use crate::api::Error;
use crate::model::{self, Analysis, Plan, Spec};
use crate::output::Drawing;
use crate::tables::{self, PlannedTable};
use crate::text::Font;

mod projection;
mod sections;
mod threads;

const LIST: &str = "t:1";
const EXTENTS: [&str; 3] = ["width", "depth", "height"];

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct BalloonSpec {
    pub suppress: bool,
    /// Preferred carrier, front/plan/side/iso. A hidden item is still reported.
    pub view: Option<String>,
    /// Ring centre relative to that view's origin, in paper mm, y up.
    pub position: Option<[f64; 2]>,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct GaSpec {
    /// None: include only when there is more than one group.
    pub include: Option<bool>,
    /// Source axis x/y/z to read horizontally; absent chooses the longest.
    pub horizontal_axis: Option<String>,
    /// Recover indistinguishable items with a full section and, if needed, a detail.
    pub sections: Option<bool>,
    /// Item whose centre selects the section plane; absent uses the first unresolved item.
    pub section_item: Option<u32>,
    pub details: Option<bool>,
    /// Standard scale for an enlarged recovered region.
    pub detail_scale: Option<String>,
    /// Hatch intent by final item number.
    pub hatching: BTreeMap<u32, HatchSpec>,
    /// Existing view, paper, scale, title and envelope-dimension intent.
    pub drawing: Spec,
    /// None: 6 mm, larger than inspection and hole TAG rings.
    pub balloon_radius: Option<f64>,
    /// Keyed by the pack's final item numbers, including bought items.
    pub balloons: BTreeMap<u32, BalloonSpec>,
    /// None: draw the required parts list above the title block.
    pub parts_list: Option<bool>,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct HatchSpec {
    pub omit: Option<bool>,
    /// Angle in degrees on the sheet, 0–180.
    pub angle: Option<f64>,
    /// Perpendicular hatch spacing in paper mm.
    pub spacing: Option<f64>,
}

#[derive(Clone, Debug, Serialize)]
pub struct ItemReference {
    pub item: u32,
    pub key: String,
    pub view: String,
    /// Original ownership witness; generated cut faces are identified by `section`.
    pub face: usize,
    pub instance: usize,
    pub point: [f64; 3],
    pub tip: [f64; 2],
    pub at: [f64; 2],
    pub radius: f64,
    pub interior: bool,
    pub section: bool,
}

#[derive(Clone, Debug, Serialize)]
pub struct GeneralAssembly {
    pub sheet: usize,
    pub size: String,
    pub scale: String,
    pub envelope: [f64; 3],
    /// Source assembly coordinates to the common GA drawing frame, rows [R|t].
    pub frame: haecceity::step::Placement,
    pub balloons: Vec<ItemReference>,
    pub unballooned: Vec<u32>,
    pub suppressed: Vec<u32>,
    pub parts_list: Option<PlannedTable>,
    pub problems: Vec<model::Problem>,
    pub views: Vec<model::PlannedView>,
}

pub(super) struct Prepared {
    part: haecceity::Part,
    analysis: Analysis,
    plan: Plan,
    projections: model::Projections,
    hatches: BTreeMap<String, Vec<crate::output::Item>>,
    cuts: Vec<Vec<[f64; 3]>>,
    cut_material: std::collections::BTreeSet<String>,
    pub report: GeneralAssembly,
    pub findings: Vec<Finding>,
}

impl Prepared {
    pub fn render(&mut self, total: usize, font: &Font) -> Result<Drawing, Error> {
        let started = std::time::Instant::now();
        let sheet = &mut self.plan.sheet;
        sheet.title.insert("sheet".into(), format!("1/{total}"));
        crate::title_block::pack_cells(sheet, 0, 0);
        for cell in &mut sheet.title_cells {
            if matches!(cell.field.as_str(), "pack_item" | "pack_qty") {
                cell.value = None;
            }
        }
        for v in self.plan.views.iter().filter(|v| v.cut.is_some()) {
            if let Some(ink) = self.hatches.get(&v.id) {
                self.projections.assembly_hatch(
                    &self.analysis,
                    &self.plan,
                    v,
                    ink.clone(),
                    &self.cuts,
                    self.cut_material.contains(&v.id),
                );
            }
        }
        let mut drawing = model::render_with(
            &self.part,
            &self.analysis,
            &self.plan,
            font,
            &mut self.projections,
        )
        .map_err(|e| Error::failed(e.to_string()))?;
        for v in self.plan.views.iter().filter(|v| v.detail.is_some()) {
            if let Some(ink) = self.hatches.get(&v.id) {
                drawing.layers.push(crate::output::Layer {
                    name: format!("ga_hatch_{}", v.id),
                    style: crate::output::thin_style(),
                    items: ink.clone(),
                });
            }
        }
        self.report.problems = self.plan.problems.clone();
        self.report.problems.extend(drawing.problems.clone());
        self.report
            .problems
            .extend(
                crate::lint::structural::check(&drawing)
                    .into_iter()
                    .map(|p| model::Problem {
                        id: format!("p:ga:{}", p.code),
                        code: p.code,
                        severity: match p.severity {
                            crate::lint::Severity::Error => "error",
                            crate::lint::Severity::Warning => "warning",
                            crate::lint::Severity::Info => "info",
                        }
                        .into(),
                        message: p.message,
                        subjects: p.annotation.into_iter().chain(p.related).collect(),
                        ..Default::default()
                    }),
            );
        profile("render", started);
        Ok(drawing)
    }
}

/// Assembly lacks PRODUCT.id in the kernel's summary; follow the public Part 21
/// graph's definition -> formation -> product chain, with decoded string attributes.
fn assembly_metadata(
    doc: &Document,
    definition: u64,
    name: &str,
) -> Result<(String, crate::bom::Metadata), Error> {
    let attr = |id, index| match doc.get(id)? {
        RawEntity::Simple { attributes, .. } => attributes.get(index),
        _ => None,
    };
    let reference = |id, index| match attr(id, index)? {
        Attribute::EntityRef(id) => Some(*id),
        _ => None,
    };
    let number = reference(definition, 2)
        .and_then(|formation| reference(formation, 2))
        .and_then(|product| match attr(product, 0)? {
            Attribute::String(s) => Some(s.clone()),
            _ => None,
        })
        .unwrap_or_default();
    Ok((number, crate::bom::read_assembly(doc, definition, name)))
}

fn generic(name: &str) -> bool {
    let upper = name.trim().to_ascii_uppercase();
    upper.is_empty()
        || [
            "ASSEMBLY",
            "PART",
            "PRODUCT",
            "UNTITLED",
            "COMPONENT",
            "PART STUDIO",
            "DEFAULT",
            "DOCUMENT",
            "BODY",
            "SOLID",
            "OPEN CASCADE STEP TRANSLATOR",
        ]
        .iter()
        .any(|base| {
            upper == *base
                || upper.strip_prefix(base).is_some_and(|s| {
                    !s.is_empty()
                        && s.chars()
                            .all(|c| c.is_ascii_digit() || " _-().:#".contains(c))
                })
        })
}

#[derive(Clone)]
struct Site {
    view: usize,
    face: usize,
    instance: usize,
    point: [f64; 3],
    tip: [f64; 2],
    distance: f64,
    clearance: f64,
    interior: bool,
    fraction: f64,
}

fn owners(file: &StepFile, rows: &[&Row]) -> BTreeMap<usize, usize> {
    let mut owner = BTreeMap::new();
    for (group, row) in rows.iter().enumerate() {
        for &index in &row.part_indices {
            for placement in &file.parts[index].placements {
                for &instance in &placement.instances {
                    owner.insert(instance, group);
                }
            }
        }
    }
    owner
}

/// One depth/ownership map per camera supplies BOTH visible-area ranking and
/// interior attachment sites. Its cost depends on the drawing resolution, rather
/// than repeatedly shooting rays from every face of every repeated instance.
fn sites(
    part: &haecceity::Part,
    a: &Analysis,
    plan: &Plan,
    file: &StepFile,
    rows: &[&Row],
    settings: &GaSpec,
) -> Vec<Vec<Site>> {
    let owner = owners(file, rows);
    let faces = crate::quiddity_evidence::drawn_faces(part);
    let caster = RayCaster::for_faces(part, faces.clone());
    let mut instance_bounds: BTreeMap<usize, [[f64; 3]; 2]> = BTreeMap::new();
    for &face in &faces {
        let Some(source) = part.face_source(face) else {
            continue;
        };
        let b = part.face_bounds(face);
        let bounds = instance_bounds
            .entry(source.instance)
            .or_insert([[f64::INFINITY; 3], [f64::NEG_INFINITY; 3]]);
        for k in [0, 1, 2] {
            bounds[0][k] = bounds[0][k].min(b.min[k]);
            bounds[1][k] = bounds[1][k].max(b.max[k]);
        }
    }
    let mut result = vec![Vec::new(); rows.len()];
    for (vi, v) in plan
        .views
        .iter()
        .enumerate()
        .filter(|(_, v)| v.cut.is_none() && v.detail.is_none())
    {
        let camera = View::new(v.direction, v.up).expect("planned camera");
        let (size, centre) = model::extent(a, &camera);
        let step = (size[0].max(size[1]) / 160.).max(0.6 / v.scale);
        let dims = size.map(|s| (s / step).ceil().max(1.) as usize);
        let depth = a
            .envelope
            .iter()
            .flat_map(|p| {
                (0..8)
                    .map(move |mask| {
                        geom::dot(
                            [0, 1, 2].map(|k| a.envelope[(mask >> k) & 1][k]),
                            camera.toward,
                        )
                    })
                    .chain(std::iter::once(geom::dot(*p, camera.toward)))
            })
            .fold(f64::NEG_INFINITY, f64::max)
            + 1.;
        let mut grid = vec![None; dims[0] * dims[1]];
        let mut areas: BTreeMap<(usize, usize), f64> = BTreeMap::new();
        for y in 0..dims[1] {
            for x in 0..dims[0] {
                let q = [
                    centre[0] - size[0] / 2. + (x as f64 + 0.5) * size[0] / dims[0] as f64,
                    centre[1] - size[1] / 2. + (y as f64 + 0.5) * size[1] / dims[1] as f64,
                ];
                let origin = [0, 1, 2]
                    .map(|k| camera.x[k] * q[0] + camera.y[k] * q[1] + camera.toward[k] * depth);
                let Some(hits) = caster.hits(origin, camera.toward.map(|c| -c), f64::INFINITY)
                else {
                    continue;
                };
                let Some(hit) = hits.first() else { continue };
                let Some(source) = part.face_source(hit.face) else {
                    continue;
                };
                let Some(&group) = owner.get(&source.instance) else {
                    continue;
                };
                let point = [0, 1, 2].map(|k| origin[k] - hit.t * camera.toward[k]);
                grid[y * dims[0] + x] = Some((group, hit.face, source.instance, point, q));
                *areas.entry((group, source.instance)).or_default() +=
                    size[0] * size[1] / (dims[0] * dims[1]) as f64 * v.scale * v.scale;
            }
        }
        for y in 0..dims[1] {
            for x in 0..dims[0] {
                let Some((group, face, instance, point, q)) = grid[y * dims[0] + x] else {
                    continue;
                };
                let intent = settings.balloons.get(&rows[group].item);
                if intent.is_some_and(|b| {
                    b.suppress
                        || b.view
                            .as_deref()
                            .is_some_and(|s| s != v.id.trim_start_matches("v:"))
                }) {
                    continue;
                }
                let mut clearance = 0.;
                for r in 1..=5usize {
                    if x < r || y < r || x + r >= dims[0] || y + r >= dims[1] {
                        break;
                    }
                    if (x - r..=x + r).any(|xx| {
                        [y - r, y + r]
                            .into_iter()
                            .any(|yy| grid[yy * dims[0] + xx].is_none_or(|c| c.0 != group))
                    }) || (y - r..=y + r).any(|yy| {
                        [x - r, x + r]
                            .into_iter()
                            .any(|xx| grid[yy * dims[0] + xx].is_none_or(|c| c.0 != group))
                    }) {
                        break;
                    }
                    clearance = r as f64 * step * v.scale;
                }
                let projected = model::envelope_extent(instance_bounds[&instance], &camera).0;
                let area = areas[&(group, instance)];
                result[group].push(Site {
                    view: vi,
                    face,
                    instance,
                    point,
                    tip: [0, 1].map(|k| v.at[k] + v.scale * (q[k] - centre[k])),
                    distance: area,
                    fraction: (area / (projected[0] * projected[1] * v.scale * v.scale).max(1e-8))
                        .min(1.),
                    clearance,
                    interior: true,
                });
            }
        }
    }
    // Thin exposed rims may fall between ownership-map pixels. Resolve only
    // those missing groups on exact source edges, retaining arrow attachments.
    let missing: Vec<_> = result.iter().map(Vec::is_empty).collect();
    if missing.iter().any(|b| *b) {
        for face in crate::quiddity_evidence::drawn_faces(part) {
            let Some(source) = part.face_source(face) else {
                continue;
            };
            let Some(&group) = owner.get(&source.instance) else {
                continue;
            };
            if !missing[group]
                || settings
                    .balloons
                    .get(&rows[group].item)
                    .is_some_and(|b| b.suppress)
            {
                continue;
            }
            for edge in part.face_edges(face) {
                let samples = &part.edges[edge].samples;
                for &point in samples.iter().step_by((samples.len() / 8).max(1)) {
                    let Some(normal) = part.normal_at_point(face, point) else {
                        continue;
                    };
                    for (vi, v) in plan
                        .views
                        .iter()
                        .enumerate()
                        .filter(|(_, v)| v.cut.is_none() && v.detail.is_none())
                    {
                        if result[group].iter().filter(|s| s.view == vi).count() >= 16
                            || settings
                                .balloons
                                .get(&rows[group].item)
                                .and_then(|b| b.view.as_deref())
                                .is_some_and(|wanted| wanted != v.id.trim_start_matches("v:"))
                            || geom::dot(normal, v.direction) <= 1e-4
                            || caster.any_hit(point, v.direction, 1e-5, f64::INFINITY)
                                != Some(false)
                        {
                            continue;
                        }
                        let camera = View::new(v.direction, v.up).expect("planned camera");
                        let centre = model::extent(a, &camera).1;
                        let q = camera.map(point);
                        let b = part.face_bounds(face);
                        let size = model::envelope_extent([b.min, b.max], &camera).0;
                        let projected =
                            model::envelope_extent(instance_bounds[&source.instance], &camera).0;
                        result[group].push(Site {
                            view: vi,
                            face,
                            instance: source.instance,
                            point,
                            tip: [0, 1].map(|k| v.at[k] + v.scale * (q[k] - centre[k])),
                            distance: size[0] * size[1] * v.scale * v.scale,
                            clearance: 0.,
                            interior: false,
                            fraction: (size[0] * size[1] / (projected[0] * projected[1]).max(1e-8))
                                .min(1.),
                        });
                    }
                }
            }
        }
    }
    for candidates in &mut result {
        candidates.sort_by(|a, b| {
            b.distance
                .total_cmp(&a.distance)
                .then(b.clearance.total_cmp(&a.clearance))
                .then(a.view.cmp(&b.view))
                .then(a.face.cmp(&b.face))
        });
        // Retain spatially different choices, not thousands of near-identical pixels.
        let mut kept: Vec<Site> = Vec::new();
        for c in candidates.drain(..) {
            if kept.iter().filter(|s| s.view == c.view).count() < 32
                && kept.iter().all(|s| {
                    s.view != c.view || (s.tip[0] - c.tip[0]).hypot(s.tip[1] - c.tip[1]) >= 1.5
                })
            {
                kept.push(c);
            }
        }
        *candidates = kept;
    }
    result
}

fn reference_area(a: &Analysis, plan: &Plan, view: usize, radius: f64) -> [f64; 4] {
    let v = &plan.views[view];
    let size = crate::detail::crop_extent(a, v).0;
    let pad = 2. * radius + 8.;
    let frame = crate::layout::FRAME;
    [
        frame.max(v.at[0] - size[0] * v.scale / 2. - pad),
        frame.max(v.at[1] - size[1] * v.scale / 2. - pad),
        (plan.sheet.page[0] - frame).min(v.at[0] + size[0] * v.scale / 2. + pad),
        (plan.sheet.page[1] - frame).min(v.at[1] + size[1] * v.scale / 2. + pad),
    ]
}

fn fixed(plan: &Plan, a: &Analysis, carrier: usize) -> Vec<[f64; 4]> {
    let mut fixed = vec![plan.sheet.title_block];
    fixed.extend(plan.captions.iter().map(|c| c.ink));
    fixed.extend(
        plan.tables
            .iter()
            .filter_map(|t| t.placed.as_ref().map(|p| p.cells)),
    );
    fixed.extend(
        plan.views
            .iter()
            .enumerate()
            .filter(|(i, _)| *i != carrier)
            .map(|(_, v)| {
                let size = crate::detail::crop_extent(a, v).0;
                [
                    v.at[0] - size[0] * v.scale / 2.,
                    v.at[1] - size[1] * v.scale / 2.,
                    v.at[0] + size[0] * v.scale / 2.,
                    v.at[1] + size[1] * v.scale / 2.,
                ]
            }),
    );
    for v in &plan.views {
        if v.cut.is_some() || v.detail.is_some() {
            let size = crate::detail::crop_extent(a, v).0;
            let at = [v.at[0], v.at[1] - size[1] * v.scale / 2. - 7.];
            fixed.push([at[0] - 24., at[1] - 2., at[0] + 24., at[1] + 2.]);
        }
    }
    for b in &plan.balloons {
        fixed.push([
            b.at[0] - b.radius - 1.,
            b.at[1] - b.radius - 1.,
            b.at[0] + b.radius + 1.,
            b.at[1] + b.radius + 1.,
        ]);
        if let Some([p, q]) = b.leader {
            fixed.push([
                p[0].min(q[0]),
                p[1].min(q[1]),
                p[0].max(q[0]),
                p[1].max(q[1]),
            ]);
        }
    }
    fixed
}

/// Place the reserved list at its required title-block address, then use the
/// existing rigid view/annotation transport to clear it without changing scale or
/// projection alignment. Paper trials use the ordinary planner and ISO scale series.
fn place_list(a: &Analysis, plan: &mut Plan) -> bool {
    if !plan.sheets.is_empty() {
        return false;
    }
    let Some(table) = plan.tables.iter_mut().find(|t| t.id == LIST) else {
        return true;
    };
    let widths = tables::widths(&table.rows);
    let width: f64 = widths.iter().sum();
    let placed = tables::lay(
        &table.rows,
        &widths,
        [
            plan.sheet.title_block[2] - width,
            plan.sheet.title_block[3] + 2.,
        ],
    );
    let obstacle = placed.cells;
    table.placed = Some(placed);
    if obstacle[0] < crate::layout::FRAME || obstacle[3] > plan.sheet.page[1] - crate::layout::FRAME
    {
        return false;
    }
    let bands = crate::view_gutters::bands(a, plan);
    let overlap = |b: [f64; 4], c: [f64; 4]| crate::sheets::overlap(b, c);
    let mut lift = 0.0_f64;
    for (v, b) in plan.views.iter().zip(&bands) {
        if overlap(*b, obstacle) {
            if v.id == "v:iso" {
                return false;
            }
            lift = lift.max(obstacle[3] + 6. - b[1]);
        }
    }
    if lift > 0. {
        if plan.spec.sheet.keep_layout == Some(true)
            || plan.spec.views.values().any(|v| v.place.is_some())
        {
            return false;
        }
        let deltas: Vec<_> = plan
            .views
            .iter()
            .map(|v| {
                if v.id == "v:iso" {
                    [0., 0.]
                } else {
                    [0., lift]
                }
            })
            .collect();
        let mut moved = bands.clone();
        for (b, d) in moved.iter_mut().zip(&deltas) {
            crate::placement_intent::move_box(b, *d);
        }
        for (i, b) in moved.iter().enumerate() {
            if b[3] > plan.sheet.page[1] - crate::layout::FRAME - 3. || overlap(*b, obstacle) {
                return false;
            }
            if moved
                .iter()
                .enumerate()
                .any(|(j, c)| j != i && overlap(*b, *c))
            {
                return false;
            }
        }
        crate::view_gutters::transport(a, plan, bands, deltas);
    }
    true
}

fn layout(
    part: &haecceity::Part,
    a: &Analysis,
    spec: &Spec,
    recovery: Option<(&sections::Recovery, &GaSpec)>,
) -> Result<Plan, Error> {
    let delayed_iso = recovery.is_some()
        && spec.sheet.keep_layout != Some(true)
        && spec
            .views
            .get("iso")
            .is_none_or(|v| !v.omit && v.place.is_none())
        && spec
            .views
            .values()
            .all(|v| v.place.as_ref().is_none_or(|p| p.relative_to != "iso"));
    let mut planning = spec.clone();
    if delayed_iso {
        planning.views.entry("iso".into()).or_default().omit = true;
    }
    let fits = |plan: &mut Plan| {
        let list = place_list(a, plan);
        let derived = list
            && recovery.is_none_or(|(r, d)| sections::append(part, a, plan, r, d))
            && (!delayed_iso || sections::append_iso(a, plan, spec));
        if std::env::var_os("DRAFTWRIGHT_GA_PROFILE").is_some() {
            eprintln!(
                "GA trial {} {}: list {}, derived {}, views {:?}, pages {}",
                plan.sheet.size,
                plan.sheet.scale,
                list,
                derived,
                plan.views.iter().map(|v| v.id.as_str()).collect::<Vec<_>>(),
                plan.sheets.len() + 1
            );
        }
        derived
    };
    let first = crate::api::plan_analysed(a, &planning)?;
    let mut trial = first.clone();
    if fits(&mut trial) {
        return Ok(trial);
    }
    // A user-selected sheet/scale remains a constraint. The ordinary policy has
    // already tried its normal recovery; reductions are considered on A4 before A3.
    let papers: Vec<_> = spec
        .sheet
        .size
        .as_deref()
        .map_or_else(|| vec!["A4", "A3", "A2", "A1"], |s| vec![s]);
    for paper in papers {
        let scales: Vec<_> = if let Some(scale) = &spec.sheet.scale {
            vec![Some(scale.clone())]
        } else {
            std::iter::once(None)
                .chain(
                    crate::layout::SCALES
                        .iter()
                        .copied()
                        .filter(|s| {
                            *s + 1e-9
                                >= crate::sheet_fit::min_legible_scale(a)
                                    .max(crate::sheets::floor(spec).unwrap_or(0.))
                        })
                        .map(|s| {
                            Some(if s >= 1. {
                                format!("{s:.0}:1")
                            } else {
                                format!("1:{:.0}", 1. / s)
                            })
                        }),
                )
                .collect()
        };
        for scale in scales {
            let mut request = planning.clone();
            request.sheet.size = Some(paper.into());
            request.sheet.scale = scale;
            let mut plan = match crate::api::plan_analysed(a, &request) {
                Ok(p) => p,
                Err(e) => {
                    if std::env::var_os("DRAFTWRIGHT_GA_PROFILE").is_some() {
                        eprintln!("GA trial {paper}: refused {}", e.message);
                    }
                    continue;
                }
            };
            if plan
                .problems
                .iter()
                .any(crate::sheet_policy::layout_problem)
            {
                if std::env::var_os("DRAFTWRIGHT_GA_PROFILE").is_some() {
                    eprintln!(
                        "GA trial {paper}: layout problems {:?}",
                        plan.problems
                            .iter()
                            .filter(|p| crate::sheet_policy::layout_problem(p))
                            .map(|p| p.code.as_str())
                            .collect::<Vec<_>>()
                    );
                }
                continue;
            }
            if fits(&mut plan) {
                return Ok(plan);
            }
        }
    }
    Err(Error::failed(
        "GA parts list cannot fit above the title block clear of the views; adjust ga/drawing sheet, scale or view intent",
    ))
}

pub(super) fn prepare<'a>(
    step: &Path,
    document: &Document,
    file: &StepFile,
    settings: &GaSpec,
    rows: impl Iterator<Item = &'a Row>,
    reused: &BTreeMap<usize, (&haecceity::Part, &Analysis)>,
    aliases: &BTreeMap<usize, super::Alias>,
) -> Result<Prepared, Error> {
    let bytes = document.bytes();
    let started = std::time::Instant::now();
    let rows: Vec<_> = rows.collect();
    let radius = settings.balloon_radius.unwrap_or_else(|| {
        rows.iter()
            .map(|r| crate::dims::font().advance(&r.item.to_string(), crate::dims::TEXT) / 2. + 2.)
            .fold(6.0, f64::max)
    });
    if !radius.is_finite() || !(5.0..=15.0).contains(&radius) {
        return Err(Error::invalid("ga/balloon_radius must be 5–15 mm"));
    }
    if settings.section_item.is_some_and(|item| {
        !rows.iter().any(|r| r.item == item) || settings.sections == Some(false)
    }) {
        return Err(Error::invalid(
            "ga/section_item needs a known item and enabled sections",
        ));
    }
    if settings.detail_scale.as_ref().is_some_and(|scale| {
        !crate::layout::SCALES
            .iter()
            .any(|s| model::scale_name(*s) == *scale)
    }) {
        return Err(Error::invalid(
            "ga/detail_scale needs an existing standard scale",
        ));
    }
    for (&item, hatch) in &settings.hatching {
        if !rows.iter().any(|r| r.item == item)
            || hatch
                .angle
                .is_some_and(|a| !a.is_finite() || !(0. ..=180.).contains(&a))
            || hatch
                .spacing
                .is_some_and(|s| !s.is_finite() || !(0.5..=10.).contains(&s))
        {
            return Err(Error::invalid(format!(
                "invalid ga hatching intent for item {item}"
            )));
        }
    }
    for (&item, b) in &settings.balloons {
        if !rows.iter().any(|r| r.item == item) {
            return Err(Error::invalid(format!("unknown ga balloon item {item}")));
        }
        if b.view.as_deref().is_some_and(|v| {
            !["front", "plan", "side", "iso", "section_aa", "detail_b"].contains(&v)
        }) || b.position.is_some_and(|p| p.iter().any(|x| !x.is_finite()))
        {
            return Err(Error::invalid(format!(
                "invalid ga balloon intent for item {item}"
            )));
        }
    }
    let name = file.assemblies.first().map_or_else(
        || rows.first().map_or("Assembly", |r| r.name.as_str()),
        |a| a.name.as_str(),
    );
    let (number, metadata) = if let Some(root) = file.assemblies.first() {
        assembly_metadata(document, root.product_definition, &root.name)?
    } else {
        let definition = rows.first().map(|r| &file.parts[r.part_indices[0]]);
        (
            definition.map_or_else(String::new, |d| d.product_id.clone()),
            definition
                .map(|d| crate::bom::read_assembly(document, d.product_definition, &d.name))
                .unwrap_or_default(),
        )
    };
    let horizontal = settings
        .horizontal_axis
        .as_deref()
        .map(|s| {
            ["x", "y", "z"]
                .iter()
                .position(|a| *a == s)
                .ok_or_else(|| Error::invalid("ga/horizontal_axis must be x, y or z"))
        })
        .transpose()?;
    let stem = step.file_stem().unwrap_or_default().to_string_lossy();
    let stem = stem.strip_suffix(".step").unwrap_or(&stem);
    let name = if generic(name) { stem } else { name };
    let number = if generic(&number) {
        stem.to_owned()
    } else {
        number
    };
    let (part, mut analysis, frame) =
        model::analyse_pack_assembly(stem, bytes, horizontal).map_err(Error::from)?;
    profile("read", started);
    let started = std::time::Instant::now();
    let mut spec = settings.drawing.clone();
    spec.sheet.hidden.get_or_insert(false);
    spec.sheet
        .title
        .entry("title".into())
        .or_insert_with(|| name.into());
    spec.sheet.title.entry("number".into()).or_insert(number);
    spec.sheet
        .title
        .entry("document_type".into())
        .or_insert_with(|| "GENERAL ASSEMBLY".into());
    // The complete GA dimension set has exactly the three envelope sizes. Existing
    // per-dimension placement, text and omission overrides remain available.
    spec.dimension_source = model::DimensionSource::Authored;
    spec.pmi = model::PmiMode::Ignore;
    let mut sizes = Vec::new();
    for k in [0, 2, 1] {
        let id = format!("d:envelope/{}.length", EXTENTS[k]);
        let size = ((analysis.envelope[1][k] - analysis.envelope[0][k]) * 10.).round() / 10.;
        if sizes.contains(&size) && !settings.drawing.dimensions.contains_key(&id) {
            continue;
        }
        sizes.push(size);
        spec.dimensions
            .entry(id.clone())
            .or_default()
            .style
            .get_or_insert(crate::label_text::Style::Reference);
        spec.precision
            .entry(id)
            .or_insert(if size.fract().abs() < 1e-9 { 0 } else { 1 });
    }
    spec.dimensions.retain(|_, d| !d.omit);
    let mut list = vec![vec![
        "ITEM".into(),
        "QTY".into(),
        "NAME / DESCRIPTION".into(),
        "REFERENCE".into(),
        "SUPPLY".into(),
        "MATERIAL".into(),
    ]];
    let mut ordered = rows.clone();
    ordered.sort_by_key(|r| std::cmp::Reverse(r.item));
    for row in ordered {
        let reference = row.reference.clone();
        list.push(vec![
            row.item.to_string(),
            row.qty.to_string(),
            row.name.clone(),
            reference,
            match row.supply {
                Supply::Make => "make",
                Supply::Buy => "buy",
                Supply::Unknown => "unknown",
            }
            .into(),
            row.bom.material.clone().unwrap_or_default(),
        ]);
    }
    if settings.parts_list != Some(false) {
        // Use a reserved declared-table slot so the existing paper/scale policy
        // accounts for the full list before it chooses any view positions.
        if spec.tables.iter().any(|t| t.id == LIST) {
            return Err(Error::invalid(
                "ga/drawing table t:1 is reserved for the parts list",
            ));
        }
        spec.tables.push(tables::TableSpec {
            sheet: None,
            id: LIST.into(),
            rows: list.iter().map(|r| serde_json::json!(r)).collect(),
            prefer: Some("br".into()),
        });
    }
    let mut plan = layout(&part, &analysis, &spec, None)?;
    profile("layout", started);
    let started = std::time::Instant::now();
    let mut candidates = sites(&part, &analysis, &plan, file, &rows, settings);
    profile("external visibility", started);
    let started = std::time::Instant::now();
    let mut hatches = BTreeMap::new();
    let mut cut_material = std::collections::BTreeSet::new();
    let recovery = (settings.sections != Some(false))
        .then(|| {
            sections::request(
                &part,
                file,
                &rows,
                &candidates,
                settings,
                &plan,
                &frame,
                aliases,
            )
        })
        .flatten();
    if let Some(recovery) = &recovery {
        spec.views.entry("plan".into()).or_default();
        plan = layout(&part, &analysis, &spec, Some((recovery, settings)))?;
        candidates = sites(&part, &analysis, &plan, file, &rows, settings);
        recovery.augment(
            &part,
            &analysis,
            &plan,
            &mut candidates,
            &mut hatches,
            &rows,
            settings,
            &mut cut_material,
        );
    }
    profile("sections", started);
    let started = std::time::Instant::now();
    centres(&part, &analysis, &mut plan);
    let mut report = GeneralAssembly {
        sheet: 1,
        size: plan.sheet.size.clone(),
        scale: plan.sheet.scale.clone(),
        envelope: [0, 1, 2].map(|k| analysis.envelope[1][k] - analysis.envelope[0][k]),
        frame,
        balloons: Vec::new(),
        unballooned: Vec::new(),
        suppressed: Vec::new(),
        parts_list: plan.tables.iter().find(|t| t.id == LIST).cloned(),
        problems: plan.problems.clone(),
        views: plan.views.clone(),
    };
    let mut findings = Vec::new();
    // Select the clearest carrier first, separating endpoints before solving all
    // references on that carrier jointly. The existing lane assignment orders
    // whole rows/columns and rejects crossings between leaders.
    let mut pending: Vec<usize> = (0..rows.len()).collect();
    pending.sort_by_key(|&i| candidates[i].len());
    let mut selected: Vec<Option<&Site>> = vec![None; rows.len()];
    for &i in &pending {
        if settings
            .balloons
            .get(&rows[i].item)
            .is_some_and(|b| b.suppress)
        {
            report.suppressed.push(rows[i].item);
            continue;
        }
        selected[i] = candidates[i].iter().find(|site| {
            selected.iter().flatten().all(|other| {
                other.view != site.view
                    || (other.tip[0] - site.tip[0]).hypot(other.tip[1] - site.tip[1]) >= 3.
            })
        });
    }
    for vi in 0..plan.views.len() {
        let members: Vec<_> = pending
            .iter()
            .copied()
            .filter(|&i| selected[i].is_some_and(|s| s.view == vi))
            .collect();
        if members.is_empty() {
            continue;
        }
        let v = &plan.views[vi];
        let mut placement_plan = plan.clone();
        placement_plan.views[vi].area = reference_area(&analysis, &plan, vi, radius);
        let targets: Vec<_> = members
            .iter()
            .map(|&i| {
                (
                    rows[i].item.to_string(),
                    selected[i].expect("selected carrier").tip,
                )
            })
            .collect();
        let balloons = crate::hole_table::item_references(
            &targets,
            &analysis,
            v,
            radius,
            &placement_plan,
            &fixed(&plan, &analysis, vi),
        );
        for (i, balloon) in members.into_iter().zip(balloons) {
            let site = selected[i].expect("selected carrier");
            let intent = settings.balloons.get(&rows[i].item);
            let balloon = if let Some(position) = intent.and_then(|b| b.position) {
                let at = [v.at[0] + position[0], v.at[1] + position[1]];
                let d = [at[0] - site.tip[0], at[1] - site.tip[1]];
                let len = d[0].hypot(d[1]);
                (len > radius).then(|| crate::hole_table::Balloon {
                    id: String::new(),
                    tag: rows[i].item.to_string(),
                    view: v.id.clone(),
                    features: Vec::new(),
                    certifying: false,
                    interior: false,
                    at,
                    radius,
                    label_box: None,
                    leader: Some([
                        site.tip,
                        [at[0] - radius * d[0] / len, at[1] - radius * d[1] / len],
                    ]),
                    state: crate::items::State::Shown,
                })
            } else {
                balloon
            };
            if let Some(mut balloon) = balloon.filter(|b| b.leader.is_some()) {
                balloon.interior = site.interior;
                balloon.id = format!("ga_item_{}", rows[i].item);
                balloon.features = vec![rows[i].key.clone()];
                report.balloons.push(ItemReference {
                    item: rows[i].item,
                    key: rows[i].key.clone(),
                    view: balloon.view.clone(),
                    face: site.face,
                    instance: site.instance,
                    point: site.point,
                    tip: site.tip,
                    at: balloon.at,
                    radius,
                    interior: site.interior,
                    section: v.cut.is_some()
                        || v.detail.as_ref().is_some_and(|d| d.source_cut.is_some()),
                });
                plan.balloons.push(balloon);
            }
        }
    }
    for (i, row) in rows.iter().enumerate() {
        if report.suppressed.contains(&row.item)
            || report.balloons.iter().any(|b| b.item == row.item)
        {
            continue;
        }
        for site in &candidates[i] {
            if report.balloons.iter().any(|b| {
                b.view == plan.views[site.view].id
                    && (b.tip[0] - site.tip[0]).hypot(b.tip[1] - site.tip[1]) < 3.
            }) {
                continue;
            }
            let v = &plan.views[site.view];
            let mut placement = plan.clone();
            placement.views[site.view].area = reference_area(&analysis, &plan, site.view, radius);
            if let Some(mut b) = crate::hole_table::item_references(
                &[(row.item.to_string(), site.tip)],
                &analysis,
                v,
                radius,
                &placement,
                &fixed(&plan, &analysis, site.view),
            )
            .pop()
            .flatten()
            .filter(|b| b.leader.is_some())
            {
                b.id = format!("ga_item_{}", row.item);
                b.features = vec![row.key.clone()];
                b.interior = site.interior;
                report.balloons.push(ItemReference {
                    item: row.item,
                    key: row.key.clone(),
                    view: b.view.clone(),
                    face: site.face,
                    instance: site.instance,
                    point: site.point,
                    tip: site.tip,
                    at: b.at,
                    radius,
                    interior: site.interior,
                    section: v.cut.is_some()
                        || v.detail.as_ref().is_some_and(|d| d.source_cut.is_some()),
                });
                plan.balloons.push(b);
                break;
            }
        }
    }
    for (i, row) in rows.iter().enumerate() {
        if report.suppressed.contains(&row.item)
            || report.balloons.iter().any(|b| b.item == row.item)
        {
            continue;
        }
        report.unballooned.push(row.item);
        findings.push(finding(
            if candidates[i].is_empty() {
                "pack_ga_not_visible"
            } else {
                "pack_ga_balloon_unplaced"
            },
            format!(
                "Item {} ({}): {}; listed without a fabricated leader",
                row.item,
                row.name,
                if candidates[i].is_empty() {
                    "no proven visible face in the GA views"
                } else {
                    "no clear, distinguishable balloon route"
                }
            ),
            row.products.clone(),
        ));
    }
    report.balloons.sort_by_key(|b| b.item);
    report.unballooned.sort_unstable();
    profile("balloons", started);
    let started = std::time::Instant::now();
    let conventional = threads::prepare(&part, &mut analysis, file, &frame, reused, aliases);
    if std::env::var_os("DRAFTWRIGHT_GA_PROFILE").is_some() {
        eprintln!(
            "GA conventional threads: {} instances from {} products",
            analysis.features.len(),
            conventional.len()
        );
    }
    let mut projections = model::Projections::default();
    projection::prepare(
        &part,
        &analysis,
        &plan,
        file,
        &frame,
        reused,
        aliases,
        recovery.as_ref().map(|r| &r.parts),
        &conventional,
        &mut projections,
    )?;
    profile("component projections", started);
    let started = std::time::Instant::now();
    for view in plan.views.iter().filter(|v| v.detail.is_some()) {
        projections
            .assembly_crop(&part, &analysis, &plan, view)
            .map_err(|e| Error::failed(e.to_string()))?;
    }
    profile("detail geometry", started);
    crate::bom::fill_titles(&metadata, &settings.drawing, &mut plan);
    Ok(Prepared {
        part,
        analysis,
        plan,
        projections,
        hatches,
        cuts: recovery
            .as_ref()
            .map_or_else(Vec::new, |r| r.loops.iter().flatten().cloned().collect()),
        cut_material,
        report,
        findings,
    })
}

/// Cylindrical native surfaces give axes without manufacturing recognition. Merge
/// collinear spans before using the component drawings' chain-line primitive.
fn centres(part: &haecceity::Part, a: &Analysis, plan: &mut Plan) {
    let mut axes: Vec<([f64; 3], [f64; 3], f64, f64)> = Vec::new();
    for (f, face) in part.faces.iter().enumerate() {
        let haecceity::geom::Surface::Cylinder { frame, radius } = &face.surface else {
            continue;
        };
        let direction = frame.z;
        let (lo, hi) = part.extent_along(&[f], direction);
        let at = [0, 1, 2].map(|k| {
            frame.origin[k] + direction[k] * ((lo + hi) / 2. - geom::dot(frame.origin, direction))
        });
        if let Some((p, d, length, diameter)) = axes.iter_mut().find(|(p, d, _, _)| {
            geom::norm(geom::cross(*d, direction)) < 1e-6
                && geom::norm(geom::cross([0, 1, 2].map(|k| p[k] - at[k]), direction)) < 1e-5
        }) {
            let old = geom::dot(*p, *d);
            let (fl, fh) = part.extent_along(&[f], *d);
            let low = (old - *length / 2.).min(fl);
            let high = (old + *length / 2.).max(fh);
            *p = [0, 1, 2].map(|k| p[k] + d[k] * ((low + high) / 2. - old));
            *length = high - low;
            *diameter = diameter.max(2. * radius);
            continue;
        }
        axes.push((at, direction, hi - lo, 2. * radius));
    }
    for v in &mut plan.views {
        if v.line_style.centre_lines == Some(false) || v.id == "v:iso" || v.detail.is_some() {
            continue;
        }
        for (i, (at, direction, length, diameter)) in axes.iter().enumerate() {
            if let Some(w) = crate::centrelines::axis(
                a,
                v,
                format!("ink:{}:ga_axis:{i}", v.id),
                *at,
                *direction,
                *length,
                *diameter,
                false,
            ) {
                v.line_work.push(w);
            }
        }
    }
}

fn profile(stage: &str, started: std::time::Instant) {
    if std::env::var_os("DRAFTWRIGHT_GA_PROFILE").is_some() {
        eprintln!("GA {stage}: {:.3}s", started.elapsed().as_secs_f64());
    }
}
