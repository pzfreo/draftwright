//! Recover nested assembly items using the same kernel cuts, crop and hatch primitives
//! as component drawings. Ownership is retained independently of the cut's outlines.
use super::*;
use crate::model::{Cut, PlannedView};
use crate::output::{Curve, Item};
use haecceity::classify::{Classifier, State};
use haecceity::hlr::{self, Plane};

pub(super) struct Recovery {
    pub cut: Cut,
    pub groups: Vec<usize>,
    pub loops: Vec<Vec<Vec<[f64; 3]>>>,
    pub solids: Vec<Vec<haecceity::brep::Solid>>,
    gauges: Vec<Option<([f64; 3], f64)>>,
    pub parts: BTreeMap<usize, haecceity::Part>,
}

#[allow(clippy::too_many_arguments)]
pub(super) fn request(
    part: &haecceity::Part,
    file: &StepFile,
    rows: &[&Row],
    candidates: &[Vec<Site>],
    settings: &GaSpec,
    plan: &Plan,
    frame: &haecceity::step::Placement,
    aliases: &BTreeMap<usize, crate::pack::Alias>,
) -> Option<Recovery> {
    let owner = owners(file, rows);
    let solids: Vec<Vec<_>> = (0..rows.len())
        .map(|group| {
            part.solids
                .iter()
                .filter(|s| {
                    s.faces
                        .first()
                        .and_then(|&f| part.face_source(f))
                        .and_then(|s| owner.get(&s.instance))
                        .copied()
                        == Some(group)
                })
                .cloned()
                .collect()
        })
        .collect();
    let bounds: Vec<_> = solids.iter().map(|s| group_bounds(part, s)).collect();
    let gauges: Vec<_> = solids.iter().map(|s| wall(part, s)).collect();
    let coverage: Vec<_> = candidates
        .iter()
        .map(|c| c.iter().map(|s| s.fraction).fold(0., f64::max))
        .collect();
    if std::env::var_os("DRAFTWRIGHT_GA_PROFILE").is_some() {
        for (i, row) in rows.iter().enumerate() {
            eprintln!(
                "GA item {}: visible fraction {:.3}, clearance {:.2}, wall {:?}",
                row.item,
                coverage[i],
                candidates[i].iter().map(|s| s.clearance).fold(0., f64::max),
                gauges[i].map(|(_, w)| w)
            );
        }
    }
    let profile_hidden: Vec<_> =
        candidates
            .iter()
            .enumerate()
            .map(|(i, c)| {
                let Some(solid) = solids[i].first() else {
                    return false;
                };
                let body = group_bounds(part, std::slice::from_ref(solid));
                let size = [0, 1, 2].map(|k| body[1][k] - body[0][k]);
                let axis = (0..3)
                    .max_by(|&j, &k| size[j].total_cmp(&size[k]))
                    .expect("three axes");
                let slender = size[axis]
                    > 3. * (0..3)
                        .filter(|&k| k != axis)
                        .map(|k| size[k])
                        .fold(0., f64::max);
                let axial = solids[i].iter().flat_map(|s| &s.faces).any(|&f| {
                    match &part.faces[f].surface {
                        geom::Surface::Cylinder { frame, .. } => frame.z[axis].abs() > 0.99,
                        _ => false,
                    }
                });
                let fraction = c
                    .iter()
                    .filter(|s| {
                        View::new(plan.views[s.view].direction, plan.views[s.view].up)
                            .expect("camera")
                            .toward[axis]
                            .abs()
                            < 0.9
                    })
                    .map(|s| s.fraction)
                    .fold(0., f64::max);
                slender && axial && fraction < 0.6
            })
            .collect();
    let groups: Vec<_> = candidates
        .iter()
        .enumerate()
        .filter_map(|(i, c)| {
            (!settings
                .balloons
                .get(&rows[i].item)
                .is_some_and(|b| b.suppress)
                && (settings.section_item == Some(rows[i].item)
                    || c.is_empty()
                    || coverage[i] < 0.15
                    || profile_hidden[i]
                    || (bounds.iter().enumerate().any(|(j, b)| {
                        j != i
                            && (0..3).all(|k| {
                                bounds[i][0][k] >= b[0][k] - 1e-5
                                    && bounds[i][1][k] <= b[1][k] + 1e-5
                            })
                    }) && gauges[i].is_some())))
            .then_some(i)
        })
        .collect();
    let first = settings
        .section_item
        .and_then(|item| rows.iter().position(|r| r.item == item))
        .or_else(|| groups.first().copied())?;
    let solid = solids[first].first()?;
    let bounds = group_bounds(part, std::slice::from_ref(solid));
    let origin = [0, 1, 2].map(|k| (bounds[0][k] + bounds[1][k]) / 2.);
    let cut = Cut {
        label: "A".into(),
        origin,
        normal: [0., 1., 0.],
        marked_in: "v:plan".into(),
        xref: None,
    };
    let mut parts = BTreeMap::new();
    let mut cuts: BTreeMap<Vec<u64>, Vec<Vec<[f64; 3]>>> = BTreeMap::new();
    let loops = rows
        .iter()
        .map(|row| {
            let mut loops = Vec::new();
            for &index in &row.part_indices {
                let (representative, alignment) = aliases
                    .get(&index)
                    .map(|a| (a.part, a.placement))
                    .unwrap_or((index, haecceity::step::IDENTITY));
                if let std::collections::btree_map::Entry::Vacant(entry) =
                    parts.entry(representative)
                {
                    let Ok(part) = file.part(representative) else {
                        continue;
                    };
                    entry.insert(part);
                }
                for placed in &file.parts[index].placements {
                    let motion = super::projection::compose(
                        frame,
                        &super::projection::compose(&placed.placement, &alignment),
                    );
                    let normal = super::projection::inverse_dir(&motion, cut.normal);
                    let point = super::projection::inverse_dir(
                        &motion,
                        [0, 1, 2].map(|k| cut.origin[k] - motion[k][3]),
                    );
                    let origin = normal.map(|n| n * geom::dot(normal, point));
                    let mut key = vec![representative as u64];
                    key.extend(
                        origin
                            .iter()
                            .chain(&normal)
                            .map(|n| ((n * 1e12).round() / 1e12 + 0.).to_bits()),
                    );
                    let contours = cuts.entry(key).or_insert_with(|| {
                        Plane::new(origin, normal)
                            .and_then(|plane| hlr::section(&parts[&representative], &plane).ok())
                            .unwrap_or_default()
                    });
                    loops.extend(contours.iter().map(|l| {
                        l.iter()
                            .map(|p| {
                                [0, 1, 2].map(|k| {
                                    motion[k][3] + (0..3).map(|j| motion[k][j] * p[j]).sum::<f64>()
                                })
                            })
                            .collect()
                    }));
                }
            }
            loops
        })
        .collect();
    Some(Recovery {
        cut,
        groups,
        loops,
        solids,
        gauges,
        parts,
    })
}

fn group_bounds(part: &haecceity::Part, solids: &[haecceity::brep::Solid]) -> [[f64; 3]; 2] {
    let mut b = [[f64::INFINITY; 3], [f64::NEG_INFINITY; 3]];
    for f in solids.iter().flat_map(|s| &s.faces) {
        let fb = part.face_bounds(*f);
        for k in [0, 1, 2] {
            b[0][k] = b[0][k].min(fb.min[k]);
            b[1][k] = b[1][k].max(fb.max[k]);
        }
    }
    b
}

fn reserve(a: &Analysis, plan: &Plan, size: [f64; 2]) -> Option<[f64; 4]> {
    let mut obstacles = crate::view_gutters::bands(a, plan);
    obstacles.push(plan.sheet.title_block);
    obstacles.extend(
        plan.tables
            .iter()
            .filter_map(|t| t.placed.as_ref().map(|p| p.cells)),
    );
    let frame = crate::layout::FRAME + 4.;
    let region = [
        frame,
        frame,
        plan.sheet.page[0] - frame,
        plan.sheet.page[1] - frame,
    ];
    crate::layout::fit_auxiliary_box(size, region, &obstacles, crate::layout::Corner::TopLeft, 4.)
        .map(|at| [at[0], at[1], at[0] + size[0], at[1] + size[1]])
}

pub(super) fn append(
    part: &haecceity::Part,
    a: &Analysis,
    plan: &mut Plan,
    recovery: &Recovery,
    settings: &GaSpec,
) -> bool {
    let camera = View::new([0., -1., 0.], [0., 0., 1.]).expect("front camera");
    let size = model::extent(a, &camera).0;
    let scale = plan.sheet.scale_factor;
    let Some(area) = reserve(a, plan, [size[0] * scale + 36., size[1] * scale + 40.]) else {
        return false;
    };
    let Some(parent) = plan.views.iter().find(|v| v.id == "v:plan").cloned() else {
        return false;
    };
    let mut section = parent.clone();
    section.id = "v:section_aa".into();
    section.direction = [0., -1., 0.];
    section.up = [0., 0., 1.];
    section.at = [(area[0] + area[2]) / 2., (area[1] + area[3]) / 2. + 4.];
    section.area = area;
    section.scale = scale;
    section.cut = Some(recovery.cut.clone());
    section.line_work.clear();
    section.basis =
        "Full section through an item without a distinguishable external attachment site".into();
    let (x0, x1, y) = model::cutting_line(
        a,
        &parent,
        &View::new(parent.direction, parent.up).expect("parent camera"),
        &recovery.cut,
    );
    plan.views
        .iter_mut()
        .find(|v| v.id == parent.id)
        .expect("parent")
        .line_work
        .push(crate::centrelines::LineWork {
            id: "ink:v:plan:cut:A".into(),
            kind: "cutting_plane".into(),
            curves: vec![Curve::Line([x0, y], [x1, y])],
            centre: None,
            radial: false,
            global_axis: false,
            overrun: 0.,
        });
    plan.views.push(section.clone());
    if settings.details != Some(false) {
        // A cut material strip narrower than 0.7 paper mm needs an enlarged crop.
        // Measure the real section region rather than inferring material or a fit.
        if let Some((group, point, thickness)) = recovery.thin_site(part, a, &section) {
            let factor = (1.2 / (thickness * scale)).ceil().max(2.);
            let automatic_scale = plan
                .spec
                .scales()
                .iter()
                .copied()
                .filter(|s| *s >= scale * factor)
                .min_by(f64::total_cmp)
                .unwrap_or(10.);
            let detail_scale = settings
                .detail_scale
                .as_ref()
                .and_then(|name| {
                    plan.spec
                        .scales()
                        .iter()
                        .find(|s| model::scale_name(**s) == *name)
                })
                .copied()
                .unwrap_or(automatic_scale);
            if detail_scale <= scale {
                return false;
            }
            let half = (10. / detail_scale).max(thickness * 3.);
            let mut bounds = [point, point];
            for k in [0, 2] {
                bounds[0][k] -= half;
                bounds[1][k] += half;
            }
            let Some(area) = reserve(
                a,
                plan,
                [
                    2. * half * detail_scale + 36.,
                    2. * half * detail_scale + 28.,
                ],
            ) else {
                return false;
            };
            let mut detail = section.clone();
            detail.id = "v:detail_b".into();
            detail.cut = None;
            detail.scale = detail_scale;
            detail.at = [(area[0] + area[2]) / 2., (area[1] + area[3]) / 2. + 3.];
            detail.area = area;
            detail.detail = Some(crate::detail::Detail {
                label: "B".into(),
                axis: 1,
                source_view: section.id,
                source_cut: Some(recovery.cut.clone()),
                kind: format!("assembly item {}", group + 1),
                bounds,
                marked_bounds: bounds,
                dimensions: Vec::new(),
                scale: detail_scale,
                authored: false,
                partial_profile: true,
            });
            plan.views.push(detail);
        }
    }
    true
}

fn standard(name: &str) -> bool {
    name.to_ascii_lowercase()
        .split(|c: char| !c.is_alphanumeric())
        .any(|s| ["screw", "pin", "shaft", "washer", "rod"].contains(&s))
}

/// The exception applies to longitudinal cuts, not a shaft's transverse section.
fn longitudinal(part: &haecceity::Part, solid: &haecceity::brep::Solid, normal: [f64; 3]) -> bool {
    solid
        .faces
        .iter()
        .filter_map(|&face| {
            if part.faces[face].reversed {
                return None;
            }
            let (frame, radius) = match &part.faces[face].surface {
                geom::Surface::Cylinder { frame, radius }
                | geom::Surface::Cone { frame, radius, .. } => (frame, *radius),
                _ => return None,
            };
            let (lo, hi) = part.extent_along(&[face], frame.z);
            Some((frame.z, radius.abs() * (hi - lo)))
        })
        .max_by(|a, b| a.1.total_cmp(&b.1))
        .is_some_and(|(axis, _)| geom::dot(axis, normal).abs() < 1e-6)
}

impl Recovery {
    /// Section spans are independently classified per item, so mating solids never
    /// cancel by whole-assembly parity. These spans supply hatch and dot sites.
    fn spans(
        &self,
        group: usize,
        part: &haecceity::Part,
        v: &PlannedView,
        a: &Analysis,
        intent: Option<&HatchSpec>,
    ) -> Vec<[[f64; 2]; 2]> {
        let camera = View::new(v.direction, v.up).expect("section camera");
        let centre = crate::detail::crop_extent(a, v).1;
        let loops: Vec<Vec<_>> = self.loops[group]
            .iter()
            .map(|l| {
                l.iter()
                    .map(|p| {
                        let q = camera.map(*p);
                        [0, 1].map(|k| v.at[k] + v.scale * (q[k] - centre[k]))
                    })
                    .collect()
            })
            .collect();
        let classifier = Classifier::for_faces(
            part,
            self.solids[group]
                .iter()
                .flat_map(|s| s.faces.iter().copied())
                .collect(),
        );
        let to_model = |q: [f64; 2], side: f64| {
            let q = [0, 1].map(|k| centre[k] + (q[k] - v.at[k]) / v.scale);
            [q[0], self.cut.origin[1] + side * 1e-3, q[1]]
        };
        let angle = intent
            .and_then(|h| h.angle)
            .unwrap_or([45., 135., 30., 120., 60., 150.][group % 6]);
        let spacing = intent
            .and_then(|h| h.spacing)
            .unwrap_or(1.5 + (group / 6) as f64 * 0.5);
        let turn = (45. - angle).to_radians();
        let rotate = |p: [f64; 2], t: f64| {
            [
                p[0] * t.cos() - p[1] * t.sin(),
                p[0] * t.sin() + p[1] * t.cos(),
            ]
        };
        let rotated: Vec<Vec<_>> = loops
            .iter()
            .map(|l| l.iter().map(|&p| rotate(p, turn)).collect())
            .collect();
        crate::section::hatch(&rotated, spacing, Some(&|_| true))
            .into_iter()
            .map(|[p, q]| [rotate(p, -turn), rotate(q, -turn)])
            .filter(|[p, q]| {
                let mid = [(p[0] + q[0]) / 2., (p[1] + q[1]) / 2.];
                [-1., 1.]
                    .into_iter()
                    .all(|s| classifier.classify(to_model(mid, s)) == State::In)
            })
            .collect()
    }
    fn thin_site(
        &self,
        part: &haecceity::Part,
        a: &Analysis,
        v: &PlannedView,
    ) -> Option<(usize, [f64; 3], f64)> {
        self.groups
            .iter()
            .filter_map(|&i| {
                self.gauges[i].and_then(|(point, thickness)| {
                    (thickness * v.scale < 0.7
                        && (point[1] - self.cut.origin[1]).abs() < 1e-5
                        && self
                            .spans(i, part, v, a, None)
                            .iter()
                            .all(|[p, q]| (p[0] - q[0]).hypot(p[1] - q[1]) < 1.4))
                    .then_some((i, point, thickness))
                })
            })
            .min_by(|a, b| a.2.total_cmp(&b.2))
    }
    #[allow(clippy::too_many_arguments)]
    pub(super) fn augment(
        &self,
        part: &haecceity::Part,
        a: &Analysis,
        plan: &Plan,
        candidates: &mut [Vec<Site>],
        hatches: &mut BTreeMap<String, Vec<Item>>,
        rows: &[&Row],
        settings: &GaSpec,
        material: &mut std::collections::BTreeSet<String>,
    ) {
        let witnesses: Vec<Vec<_>> = self
            .solids
            .iter()
            .enumerate()
            .map(|(i, solids)| {
                if self.groups.contains(&i) || standard(&rows[i].name) {
                    solids
                        .iter()
                        .map(|s| {
                            (
                                s.faces[0],
                                Classifier::for_faces(part, s.faces.clone()),
                                longitudinal(part, s, self.cut.normal),
                            )
                        })
                        .collect()
                } else {
                    Vec::new()
                }
            })
            .collect();
        let occupied: Vec<_> = self
            .solids
            .iter()
            .enumerate()
            .map(|(i, _)| {
                self.groups.contains(&i).then(|| {
                    Classifier::for_faces(
                        part,
                        self.solids
                            .iter()
                            .enumerate()
                            .filter(|(j, _)| *j != i)
                            .flat_map(|(_, s)| s.iter().flat_map(|s| s.faces.iter().copied()))
                            .collect(),
                    )
                })
            })
            .collect();
        for &i in &self.groups {
            candidates[i].clear();
        }
        for (vi, v) in plan
            .views
            .iter()
            .enumerate()
            .filter(|(_, v)| v.cut.is_some() || v.detail.is_some())
        {
            let centre = crate::detail::crop_extent(a, v).1;
            let mut ink = Vec::new();
            for (i, row) in rows.iter().enumerate() {
                let spans = self.spans(i, part, v, a, settings.hatching.get(&row.item));
                if v.cut.is_some() && !spans.is_empty() {
                    material.insert(v.id.clone());
                }
                let ordinary = standard(&row.name);
                let rect = [
                    v.at[0] - (v.area[2] - v.area[0] - 36.) / 2.,
                    v.at[1] - (v.area[3] - v.area[1] - 28.) / 2.,
                    v.at[0] + (v.area[2] - v.area[0] - 36.) / 2.,
                    v.at[1] + (v.area[3] - v.area[1] - 28.) / 2.,
                ];
                for [p, q] in spans {
                    let tip = [(p[0] + q[0]) / 2., (p[1] + q[1]) / 2.];
                    let mapped = [0, 1].map(|k| centre[k] + (tip[k] - v.at[k]) / v.scale);
                    let model_point = [mapped[0], self.cut.origin[1], mapped[1]];
                    let lengthwise = ordinary
                        && witnesses[i]
                            .iter()
                            .find(|(_, c, _)| c.classify(model_point) == State::In)
                            .is_some_and(|(_, _, long)| *long);
                    if (v.detail.is_none()
                        || (tip[0] > rect[0] + 2.
                            && tip[0] < rect[2] - 2.
                            && tip[1] > rect[1] + 2.
                            && tip[1] < rect[3] - 2.))
                        && self.groups.contains(&i)
                        && !settings.balloons.get(&row.item).is_some_and(|b| {
                            b.suppress
                                || b.view
                                    .as_deref()
                                    .is_some_and(|wanted| wanted != v.id.trim_start_matches("v:"))
                        })
                    {
                        let mapped = [0, 1].map(|k| centre[k] + (tip[k] - v.at[k]) / v.scale);
                        let point = [mapped[0], self.cut.origin[1], mapped[1]];
                        if occupied[i]
                            .as_ref()
                            .is_none_or(|other| other.classify(point) == State::Out)
                            && let Some((face, _, _)) =
                                witnesses[i].iter().find(|(_, classifier, _)| {
                                    [-1., 1.].into_iter().all(|side| {
                                        classifier.classify([
                                            point[0],
                                            point[1] + side * 1e-3,
                                            point[2],
                                        ]) == State::In
                                    })
                                })
                            && let Some(source) = part.face_source(*face)
                        {
                            candidates[i].push(Site {
                                view: vi,
                                face: *face,
                                instance: source.instance,
                                point,
                                tip,
                                distance: 1e9 + v.scale,
                                interior: true,
                                fraction: 1.,
                                clearance: (p[0] - q[0]).hypot(p[1] - q[1]) / 2.,
                            });
                        }
                    }
                    if !settings
                        .hatching
                        .get(&row.item)
                        .and_then(|h| h.omit)
                        .unwrap_or(lengthwise)
                    {
                        ink.extend(if v.detail.is_some() {
                            crate::detail::clip(Curve::Line(p, q), rect)
                                .into_iter()
                                .map(Item::Edge)
                                .collect()
                        } else {
                            vec![Item::Edge(Curve::Line(p, q))]
                        });
                    }
                }
            }
            for &i in &self.groups {
                if settings.balloons.get(&rows[i].item).is_some_and(|b| {
                    b.suppress
                        || b.view
                            .as_deref()
                            .is_some_and(|wanted| wanted != v.id.trim_start_matches("v:"))
                }) {
                    continue;
                }
                let camera = View::new(v.direction, v.up).expect("section camera");
                let crop = v.detail.as_ref().map(|d| d.bounds);
                for l in &self.loops[i] {
                    for edge in l.windows(2) {
                        let point = [0, 1, 2].map(|k| (edge[0][k] + edge[1][k]) / 2.);
                        if crop.is_some_and(|b| {
                            (0..3).any(|k| {
                                k != 1
                                    && (point[k] < b[0][k] + 2. / v.scale
                                        || point[k] > b[1][k] - 2. / v.scale)
                            })
                        }) || occupied[i]
                            .as_ref()
                            .is_some_and(|other| other.classify(point) != State::Out)
                        {
                            continue;
                        }
                        let Some((face, _, _)) = witnesses[i].iter().find(|(_, classifier, _)| {
                            matches!(classifier.classify(point), State::In | State::On)
                        }) else {
                            continue;
                        };
                        let Some(source) = part.face_source(*face) else {
                            continue;
                        };
                        let q = camera.map(point);
                        let tip = [0, 1].map(|k| v.at[k] + v.scale * (q[k] - centre[k]));
                        candidates[i].push(Site {
                            view: vi,
                            face: *face,
                            instance: source.instance,
                            point,
                            tip,
                            distance: 1e9 + v.scale - 0.1,
                            interior: false,
                            fraction: 1.,
                            clearance: 0.,
                        });
                    }
                }
            }
            hatches.insert(v.id.clone(), ink);
        }
        for c in candidates {
            c.sort_by(|a, b| {
                b.distance
                    .total_cmp(&a.distance)
                    .then(b.clearance.total_cmp(&a.clearance))
            });
        }
    }
}

/// A cylindrical wall is proven by overlapping coaxial inner/outer faces and
/// classification of material between them. It is not inferred from a name.
fn wall(part: &haecceity::Part, solids: &[haecceity::brep::Solid]) -> Option<([f64; 3], f64)> {
    let faces: Vec<_> = solids
        .iter()
        .flat_map(|s| s.faces.iter().copied())
        .collect();
    let cylinders: Vec<_> = faces
        .iter()
        .filter_map(|&f| match &part.faces[f].surface {
            geom::Surface::Cylinder { frame, radius } => Some((f, frame, *radius)),
            _ => None,
        })
        .collect();
    let classifier = Classifier::for_faces(part, faces);
    let mut best: Option<([f64; 3], f64)> = None;
    for (i, (f, a, ra)) in cylinders.iter().enumerate() {
        for (g, b, rb) in cylinders.iter().skip(i + 1) {
            let width = (ra - rb).abs();
            if width < 0.02
                || width >= 2.
                || best.is_some_and(|(_, w)| w <= width)
                || geom::norm(geom::cross(a.z, b.z)) > 1e-6
                || geom::norm(geom::cross(
                    [0, 1, 2].map(|k| a.origin[k] - b.origin[k]),
                    a.z,
                )) > 1e-5
            {
                continue;
            }
            let (fl, fh) = part.extent_along(&[*f], a.z);
            let (gl, gh) = part.extent_along(&[*g], a.z);
            if fh.min(gh) - fl.max(gl) < 1e-3 {
                continue;
            }
            let middle = (fl.max(gl) + fh.min(gh)) / 2.;
            let Some(radial) = geom::unit(geom::cross(a.z, [0., 1., 0.])) else {
                continue;
            };
            let point = [0, 1, 2].map(|k| {
                a.origin[k]
                    + a.z[k] * (middle - geom::dot(a.origin, a.z))
                    + radial[k] * (ra + rb) / 2.
            });
            if [-1., 1.].into_iter().all(|side| {
                classifier.classify([point[0], point[1] + side * 1e-3, point[2]]) == State::In
            }) {
                best = Some((point, width));
            }
        }
    }
    let centre = group_bounds(part, solids);
    let base = best.map_or(
        [0, 1, 2].map(|k| (centre[0][k] + centre[1][k]) / 2.),
        |(p, _)| p,
    );
    let planes: Vec<_> = solids
        .iter()
        .flat_map(|s| &s.faces)
        .filter_map(|&f| match &part.faces[f].surface {
            geom::Surface::Plane { frame } => Some(frame),
            _ => None,
        })
        .collect();
    for (i, a) in planes.iter().enumerate() {
        if a.z[1].abs() > 0.9 {
            continue;
        }
        for b in &planes[i + 1..] {
            let width = geom::dot([0, 1, 2].map(|k| b.origin[k] - a.origin[k]), a.z).abs();
            if width < 0.02
                || width >= 2.
                || best.is_some_and(|(_, w)| w <= width)
                || geom::norm(geom::cross(a.z, b.z)) > 1e-6
            {
                continue;
            }
            let middle = (geom::dot(a.origin, a.z) + geom::dot(b.origin, a.z)) / 2.;
            let p = [0, 1, 2].map(|k| base[k] + a.z[k] * (middle - geom::dot(base, a.z)));
            if [-1., 1.]
                .into_iter()
                .all(|side| classifier.classify([p[0], p[1] + side * 1e-3, p[2]]) == State::In)
            {
                best = Some((p, width));
            }
        }
    }

    best
}

/// Sections/details claim paper before the subordinate orientation view, as in
/// the ordinary derived-view policy. Letting an early isometric fill the largest
/// empty rectangle would otherwise force an unnecessarily large GA sheet.
pub(super) fn append_iso(a: &Analysis, plan: &mut Plan, spec: &Spec) -> bool {
    let mut obstacles = crate::view_gutters::bands(a, plan);
    obstacles.push(plan.sheet.title_block);
    obstacles.extend(
        plan.tables
            .iter()
            .filter_map(|t| t.placed.as_ref().map(|p| p.cells)),
    );
    let frame = crate::layout::FRAME + 4.;
    let region = [
        frame,
        frame,
        plan.sheet.page[0] - frame,
        plan.sheet.page[1] - frame,
    ];
    let bounds = a.drawing_iso_bounds();
    let scale = plan.sheet.scale_factor;
    let area = crate::layout::largest_empty_rect(
        region,
        &obstacles,
        [
            (bounds[2] - bounds[0]) * scale + 6.,
            (bounds[3] - bounds[1]) * scale + 18.,
        ],
    );
    let rect = [area[0] + 3., area[1] + 15., area[2] - 3., area[3] - 3.];
    if rect[0] >= rect[2] || rect[1] >= rect[3] {
        return false;
    }
    let at = [(rect[0] + rect[2]) / 2., (rect[1] + rect[3]) / 2.];
    let factor = spec.iso_scale_factor.unwrap_or_else(|| {
        crate::layout::iso_factor_with_detail(rect, at, bounds.map(|n| n * scale), true)
    });
    if factor < 0.5 && spec.iso_scale_factor.is_none() {
        return false;
    }
    let bbox = [
        at[0] + bounds[0] * scale * factor,
        at[1] + bounds[1] * scale * factor,
        at[0] + bounds[2] * scale * factor,
        at[1] + bounds[3] * scale * factor,
    ];
    if bbox[0] < rect[0]
        || bbox[1] < rect[1]
        || bbox[2] > rect[2]
        || bbox[3] > rect[3]
        || obstacles.iter().any(|b| crate::sheets::overlap(*b, bbox))
    {
        return false;
    }
    let Some(mut iso) = plan.views.iter().find(|v| v.id == "v:front").cloned() else {
        return false;
    };
    iso.id = "v:iso".into();
    iso.direction = [1., -1., 1.];
    iso.up = [0., 0., 1.];
    iso.at = at;
    iso.area = area;
    iso.scale = scale * factor;
    iso.cut = None;
    iso.detail = None;
    iso.line_work.clear();
    iso.basis = "Orientation view fitted after the GA section/detail reservations".into();
    plan.views.push(iso);
    if let Some(intent) = spec.views.get("iso") {
        plan.spec.views.insert("iso".into(), intent.clone());
    } else {
        plan.spec.views.remove("iso");
    }
    crate::linework::configure(&plan.spec, &mut plan.views);
    plan.captions = crate::iso_caption::plan(a, &plan.sheet, &plan.views, &plan.dimensions);
    true
}
