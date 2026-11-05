//! Project each distinct placed product once per local camera, then resolve
//! inter-product occlusion with the assembled kernel ray caster. Repeated instances
//! share their expensive native edge/silhouette projection; crops share source cuts.
use super::*;
use haecceity::hlr::{self, Exact, Plane, Projected};
use haecceity::step::Placement;

pub(super) fn compose(a: &Placement, b: &Placement) -> Placement {
    std::array::from_fn(|i| {
        std::array::from_fn(|j| {
            if j == 3 {
                a[i][3] + (0..3).map(|k| a[i][k] * b[k][3]).sum::<f64>()
            } else {
                (0..3).map(|k| a[i][k] * b[k][j]).sum()
            }
        })
    })
}
pub(super) fn inverse_dir(m: &Placement, p: [f64; 3]) -> [f64; 3] {
    [0, 1, 2].map(|j| (0..3).map(|i| m[i][j] * p[i]).sum())
}
fn shift(p: [f64; 2], d: [f64; 2]) -> [f64; 2] {
    [p[0] + d[0], p[1] + d[1]]
}
fn translated(exact: &Exact, d: [f64; 2]) -> Exact {
    match exact {
        Exact::Line(a, b) => Exact::Line(shift(*a, d), shift(*b, d)),
        Exact::Conic {
            centre,
            radii,
            x_axis,
            y_axis,
            t0,
            t1,
        } => Exact::Conic {
            centre: shift(*centre, d),
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
        } => Exact::Nurbs {
            degree: *degree,
            poles: poles.iter().map(|&p| shift(p, d)).collect(),
            weights: weights.clone(),
            knots: knots.clone(),
            t0: *t0,
            t1: *t1,
        },
    }
}
fn interval(exact: &Exact, lo: f64, hi: f64) -> Exact {
    let mut exact = exact.clone();
    let (a, b) = exact.range();
    match &mut exact {
        Exact::Line(p, q) => {
            let d = [q[0] - p[0], q[1] - p[1]];
            let start = *p;
            *p = [start[0] + lo * d[0], start[1] + lo * d[1]];
            *q = [start[0] + hi * d[0], start[1] + hi * d[1]];
        }
        Exact::Conic { t0, t1, .. } | Exact::Nurbs { t0, t1, .. } => {
            *t0 = a + (b - a) * lo;
            *t1 = a + (b - a) * hi;
        }
    }
    exact
}

type Depths = BTreeMap<[u64; 2], Option<[f64; 3]>>;
struct LocalProjection {
    pieces: Vec<Projected>,
    depths: Depths,
}

/// Self-visibility belongs to the reusable product projection. Resolve its
/// attachment depth once, then ask the assembled kernel only whether something
/// lies in front of that point. `any_hit` stops at the first occluder; it does not
/// intersect every turn of every screw behind an already-hidden edge.
struct Occlusion<'a, 'b> {
    owners: &'a BTreeMap<usize, usize>,
    skip: Vec<usize>,
    rays: &'a RayCaster<'a>,
    camera: View,
    cut: Option<&'a model::Cut>,
    motion: Placement,
    offset: [f64; 2],
    local_camera: View,
    local_plane: Option<Plane>,
    local_depth: f64,
    local_rays: &'a RayCaster<'a>,
    depths: &'b mut Depths,
    instances: &'a [usize],
    depth: f64,
    occluders: Vec<[f64; 4]>,
}
impl Occlusion<'_, '_> {
    fn visible(&mut self, q: [f64; 2]) -> Option<bool> {
        let target = q;
        // Local HLR already proves self-visibility. A different product can
        // obscure this point only inside its conservative projected bounds.
        if !self.occluders.iter().any(|b| {
            q[0] >= b[0] - 1e-4 && q[0] <= b[2] + 1e-4 && q[1] >= b[1] - 1e-4 && q[1] <= b[3] + 1e-4
        }) {
            return Some(true);
        }
        for offset in [[0., 0.], [1e-5, 0.], [-1e-5, 0.], [0., 1e-5], [0., -1e-5]] {
            let q = [0, 1].map(|k| q[k] - self.offset[k] + offset[k]);
            let key = q.map(|n| ((n * 1e9).round() / 1e9 + 0.).to_bits());
            let point = if let Some(p) = self.depths.get(&key) {
                *p
            } else {
                let mut origin = [0, 1, 2].map(|k| {
                    self.local_camera.x[k] * q[0]
                        + self.local_camera.y[k] * q[1]
                        + self.local_camera.toward[k] * self.local_depth
                });
                if let Some(plane) = &self.local_plane {
                    let t = geom::dot([0, 1, 2].map(|k| plane.origin[k] - origin[k]), plane.normal)
                        / geom::dot(self.local_camera.toward, plane.normal);
                    origin =
                        [0, 1, 2].map(|k| origin[k] + self.local_camera.toward[k] * (t - 1e-5));
                }
                let hits = self.local_rays.hits(
                    origin,
                    self.local_camera.toward.map(|c| -c),
                    f64::INFINITY,
                )?;
                let point = hits
                    .first()
                    .map(|h| [0, 1, 2].map(|k| origin[k] - self.local_camera.toward[k] * h.t));
                self.depths.insert(key, point);
                point
            };
            if let Some(p) = point {
                let p = [0, 1, 2].map(|k| {
                    self.motion[k][3] + (0..3).map(|j| self.motion[k][j] * p[j]).sum::<f64>()
                });
                let limit = self.cut.map_or(f64::INFINITY, |c| {
                    geom::dot([0, 1, 2].map(|k| c.origin[k] - p[k]), c.normal)
                        / geom::dot(self.camera.toward, c.normal)
                });
                return match self.rays.other_hit(
                    (p, self.camera.toward, limit.max(0.)),
                    &self.skip,
                    (1e-4, limit.max(0.)),
                ) {
                    Some(hit) => Some(!hit),
                    // A contact confined to an edge's tolerance band needs the
                    // neighbouring faces too. Resolve that ray's exact first
                    // owner before abandoning this entire composed camera.
                    None => self.ownership(target),
                };
            }
        }
        // A traced silhouette can sit just outside its surface. Its native HLR
        // is still authoritative; a whole-assembly ownership ray resolves it.
        self.ownership(q)
    }

    fn ownership(&self, q: [f64; 2]) -> Option<bool> {
        let mut origin = [0, 1, 2].map(|k| {
            self.camera.x[k] * q[0] + self.camera.y[k] * q[1] + self.camera.toward[k] * self.depth
        });
        if let Some(c) = self.cut {
            let t = geom::dot([0, 1, 2].map(|k| c.origin[k] - origin[k]), c.normal)
                / geom::dot(self.camera.toward, c.normal);
            origin = [0, 1, 2].map(|k| origin[k] + self.camera.toward[k] * (t - 1e-5));
        }
        let hits = self
            .rays
            .hits(origin, self.camera.toward.map(|c| -c), f64::INFINITY)?;
        Some(
            hits.first()
                .and_then(|h| self.owners.get(&h.face))
                .is_none_or(|s| self.instances.contains(s)),
        )
    }
}

fn nurbs(exact: &Exact) -> Option<haecceity::nurbs::NurbsCurve> {
    match exact {
        Exact::Nurbs {
            degree,
            poles,
            weights,
            knots,
            ..
        } => haecceity::nurbs::NurbsCurve::new(
            *degree,
            poles.iter().map(|p| [p[0], p[1], 0.]).collect(),
            weights.clone(),
            knots.clone(),
        ),
        _ => None,
    }
}

/// The analysis already stores its resolved principal curves. Convert their
/// exact polynomial/rational forms back to kernel pieces without another HLR.
fn resolved(a: &Analysis, direction: [f64; 3], up: [f64; 3]) -> Option<Vec<Projected>> {
    if a.squared.is_some() {
        return None;
    }
    let camera = View::new(direction, up)?;
    let ink = a.edge_ink.as_ref()?.resolved.iter().find(|r| {
        View::new(r.direction, r.up).is_some_and(|v| {
            geom::dist(v.toward, camera.toward) < 1e-10 && geom::dist(v.x, camera.x) < 1e-10
        })
    })?;
    let mut out = Vec::new();
    for (curves, visible) in [(&ink.visible, true), (&ink.hidden, false)] {
        for curve in curves {
            use crate::output::Curve;
            let exact: Vec<Exact> = match curve {
                Curve::Line(a, b) => vec![Exact::Line(*a, *b)],
                Curve::Conic {
                    centre,
                    radii,
                    x_axis,
                    y_axis,
                    t0,
                    t1,
                } => vec![Exact::Conic {
                    centre: *centre,
                    radii: *radii,
                    x_axis: *x_axis,
                    y_axis: *y_axis,
                    t0: *t0,
                    t1: *t1,
                }],
                Curve::Beziers(pieces) => pieces
                    .iter()
                    .map(|p| Exact::Nurbs {
                        degree: p.len() - 1,
                        poles: p.clone(),
                        weights: vec![1.; p.len()],
                        knots: std::iter::repeat_n(0., p.len())
                            .chain(std::iter::repeat_n(1., p.len()))
                            .collect(),
                        t0: 0.,
                        t1: 1.,
                    })
                    .collect(),
                Curve::Rational(pieces) => pieces
                    .iter()
                    .map(|p| Exact::Nurbs {
                        degree: p.len() - 1,
                        poles: p.iter().map(|p| [p[0] / p[2], p[1] / p[2]]).collect(),
                        weights: p.iter().map(|p| p[2]).collect(),
                        knots: std::iter::repeat_n(0., p.len())
                            .chain(std::iter::repeat_n(1., p.len()))
                            .collect(),
                        t0: 0.,
                        t1: 1.,
                    })
                    .collect(),
            };
            for exact in exact {
                let spline = nurbs(&exact);
                if matches!(exact, Exact::Nurbs { .. }) && spline.is_none() {
                    return None;
                }
                let (lo, hi) = exact.range();
                let points = (0..=32)
                    .map(|i| {
                        let t = lo + (hi - lo) * i as f64 / 32.;
                        if let Some(s) = &spline {
                            let p = s.value(t);
                            [p[0], p[1]]
                        } else {
                            exact.at(t)
                        }
                    })
                    .collect();
                out.push(Projected {
                    class: hlr::Class::Sharp,
                    visible,
                    points,
                    exact: Some(exact),
                });
            }
        }
    }
    Some(out)
}

/// Exact local HLR supplies the curves. Only visibility can change between
/// products. Uniform screen samples bound the intervals; transitions are bisected
/// to 0.01 paper mm, retaining analytic curve parameters rather than polylining.
fn occlude(
    piece: &Projected,
    resolver: &mut Occlusion<'_, '_>,
    scale: f64,
) -> Option<Vec<Projected>> {
    if !piece.visible {
        return Some(vec![piece.clone()]);
    }
    let length: f64 = piece
        .points
        .windows(2)
        .map(|p| (p[1][0] - p[0][0]).hypot(p[1][1] - p[0][1]))
        .sum();
    let steps = ((length * scale / 0.25).ceil() as usize)
        .max(piece.points.len())
        .max(2);
    let spline = piece.exact.as_ref().and_then(nurbs);
    let at = |t: f64| {
        if let Some(e) = &piece.exact {
            let (a, b) = e.range();
            let parameter = a + t * (b - a);
            if let Some(spline) = &spline {
                let p = spline.value(parameter);
                [p[0], p[1]]
            } else {
                e.at(parameter)
            }
        } else {
            let u = t * (piece.points.len() - 1) as f64;
            let i = (u.floor() as usize).min(piece.points.len() - 2);
            let f = u - i as f64;
            [0, 1].map(|k| piece.points[i][k] + f * (piece.points[i + 1][k] - piece.points[i][k]))
        }
    };
    let mut runs = Vec::new();
    let mut start = 0.;
    let mut state = resolver.visible(at(0.))?;
    for i in 1..=steps {
        let t = i as f64 / steps as f64;
        let next = resolver.visible(at(t))?;
        if next != state {
            let mut lo = (i - 1) as f64 / steps as f64;
            let mut hi = t;
            while (hi - lo) * length * scale > 0.01 {
                let mid = (lo + hi) / 2.;
                if resolver.visible(at(mid))? == state {
                    lo = mid
                } else {
                    hi = mid
                }
            }
            let end = (lo + hi) / 2.;
            runs.push((start, end, state));
            start = end;
            state = next;
        }
    }
    runs.push((start, 1., state));
    Some(
        runs.into_iter()
            .filter(|(lo, hi, _)| hi - lo > 1e-9)
            .map(|(lo, hi, visible)| Projected {
                class: piece.class,
                visible,
                points: (0..=steps)
                    .map(|i| i as f64 / steps as f64)
                    .filter(|t| *t > lo && *t < hi)
                    .map(&at)
                    .fold(vec![at(lo)], |mut ps, p| {
                        ps.push(p);
                        ps
                    })
                    .into_iter()
                    .chain(std::iter::once(at(hi)))
                    .collect(),
                exact: piece.exact.as_ref().map(|e| interval(e, lo, hi)),
            })
            .collect(),
    )
}

#[allow(clippy::too_many_arguments)]
pub(super) fn prepare(
    part: &haecceity::Part,
    a: &Analysis,
    plan: &Plan,
    file: &StepFile,
    frame: &Placement,
    reused: &BTreeMap<usize, (&haecceity::Part, &Analysis)>,
    aliases: &BTreeMap<usize, crate::pack::Alias>,
    cut_parts: Option<&BTreeMap<usize, haecceity::Part>>,
    conventional: &std::collections::BTreeSet<usize>,
    projections: &mut model::Projections,
) -> Result<(), Error> {
    let caster = RayCaster::for_faces(part, crate::quiddity_evidence::drawn_faces(part));
    let whole_carrier = crate::modelled_threads::carrier(part, a, &plan.spec, true);
    let whole_rays = whole_carrier
        .as_ref()
        .map(|p| RayCaster::for_faces(p, crate::quiddity_evidence::drawn_faces(p)));
    let owners: BTreeMap<_, _> = (0..part.faces.len())
        .filter_map(|f| part.face_source(f).map(|s| (f, s.instance)))
        .collect();
    let carrier_owners: BTreeMap<_, _> = whole_carrier
        .as_ref()
        .map(|p| {
            p.solids
                .iter()
                .enumerate()
                .flat_map(|(i, s)| s.faces.iter().map(move |&f| (f, i)))
                .collect()
        })
        .unwrap_or_default();
    let representatives: std::collections::BTreeSet<_> = (0..file.parts.len())
        .map(|i| aliases.get(&i).map_or(i, |a| a.part))
        .collect();
    let mut locals = BTreeMap::new();
    for &index in &representatives {
        if !cut_parts.is_some_and(|parts| parts.contains_key(&index))
            && !reused.get(&index).is_some_and(|(_, a)| a.squared.is_none())
        {
            locals.insert(
                index,
                file.part(index).map_err(|e| Error::failed(e.to_string()))?,
            );
        }
    }
    let carriers: BTreeMap<_, _> = representatives
        .iter()
        .filter_map(|&i| {
            if !conventional.contains(&i) {
                return None;
            }
            let (part, a) = reused[&i];
            crate::modelled_threads::carrier(part, a, &plan.spec, true).map(|p| (i, p))
        })
        .collect();
    let carrier_rays: BTreeMap<_, _> = carriers
        .iter()
        .map(|(&i, p)| {
            (
                i,
                RayCaster::for_faces(p, crate::quiddity_evidence::drawn_faces(p)),
            )
        })
        .collect();
    let local_rays: BTreeMap<_, _> = representatives
        .iter()
        .map(|&i| {
            let local = cut_parts
                .and_then(|p| p.get(&i))
                .or_else(|| locals.get(&i))
                .unwrap_or_else(|| reused[&i].0);
            (
                i,
                RayCaster::for_faces(local, crate::quiddity_evidence::drawn_faces(local)),
            )
        })
        .collect();
    let bounds: BTreeMap<_, _> = representatives
        .iter()
        .map(|&i| {
            let local = cut_parts
                .and_then(|p| p.get(&i))
                .or_else(|| locals.get(&i))
                .unwrap_or_else(|| reused[&i].0);
            (i, local.bounds())
        })
        .collect();
    let mut memo: BTreeMap<Vec<u64>, LocalProjection> = BTreeMap::new();
    // Keep full camera evidence whenever any view asks to show hidden lines.
    // Otherwise those curves (including dense physical-thread NURBS) would be
    // converted, clipped and bounded by the renderer only to be discarded.
    // Cropped GA profiles likewise derive break contacts from visible ink only.
    let retain_hidden = plan
        .views
        .iter()
        .any(|v| v.line_style.hidden.unwrap_or(v.id != "v:iso"));
    let mut cameras = std::collections::BTreeSet::new();
    for v in &plan.views {
        let started = crate::pack::Instant::now();
        let mut native_seconds = 0.;
        let cut = v
            .cut
            .as_ref()
            .or_else(|| v.detail.as_ref().and_then(|d| d.source_cut.as_ref()));
        let camera = View::new(v.direction, v.up).expect("planned camera");
        let thread_depiction = v.line_style.thread_depiction != Some(false);
        let rays = if thread_depiction {
            whole_rays.as_ref().unwrap_or(&caster)
        } else {
            &caster
        };
        let owners = if thread_depiction && whole_carrier.is_some() {
            &carrier_owners
        } else {
            &owners
        };

        let mut camera_key: Vec<_> = v
            .direction
            .iter()
            .chain(&v.up)
            .chain(
                cut.into_iter()
                    .flat_map(|c| c.origin.iter().chain(&c.normal)),
            )
            .map(|x| x.to_bits())
            .collect();
        camera_key.push(u64::from(thread_depiction));
        if !cameras.insert(camera_key) {
            continue;
        }
        let depth = (0..8)
            .map(|mask| {
                geom::dot(
                    [0, 1, 2].map(|k| a.envelope[(mask >> k) & 1][k]),
                    camera.toward,
                )
            })
            .fold(f64::NEG_INFINITY, f64::max)
            + 1.;
        let screen_boxes: Vec<_> = file
            .parts
            .iter()
            .enumerate()
            .flat_map(|(index, definition)| {
                let (representative, alignment) = aliases
                    .get(&index)
                    .map(|a| (a.part, a.placement))
                    .unwrap_or((index, haecceity::step::IDENTITY));
                let b = bounds[&representative];
                definition.placements.iter().map(move |placed| {
                    let motion = compose(frame, &compose(&placed.placement, &alignment));
                    let mut screen = [
                        f64::INFINITY,
                        f64::INFINITY,
                        f64::NEG_INFINITY,
                        f64::NEG_INFINITY,
                    ];
                    for mask in 0..8 {
                        let p = [0, 1, 2].map(|k| {
                            if (mask >> k) & 1 == 0 {
                                b.min[k]
                            } else {
                                b.max[k]
                            }
                        });
                        let q = camera.map([0, 1, 2].map(|k| {
                            motion[k][3] + (0..3).map(|j| motion[k][j] * p[j]).sum::<f64>()
                        }));
                        for k in 0..2 {
                            screen[k] = screen[k].min(q[k]);
                            screen[k + 2] = screen[k + 2].max(q[k]);
                        }
                    }
                    (&placed.instances, screen)
                })
            })
            .collect();
        let mut assembled = Vec::new();
        let mut unresolved = false;
        for (index, definition) in file.parts.iter().enumerate() {
            let (representative, alignment) = aliases
                .get(&index)
                .map(|a| (a.part, a.placement))
                .unwrap_or((index, haecceity::step::IDENTITY));
            let local = cut_parts
                .and_then(|p| p.get(&representative))
                .or_else(|| locals.get(&representative))
                .unwrap_or_else(|| reused[&representative].0);
            let local = if thread_depiction {
                carriers.get(&representative).unwrap_or(local)
            } else {
                local
            };
            let local_rays = if thread_depiction {
                carrier_rays
                    .get(&representative)
                    .unwrap_or(&local_rays[&representative])
            } else {
                &local_rays[&representative]
            };
            for placed in &definition.placements {
                let motion = compose(frame, &compose(&placed.placement, &alignment));
                let toward = inverse_dir(&motion, camera.toward);
                let up = inverse_dir(&motion, camera.y);
                let local_camera = View::new(toward, up).expect("rigid camera");
                let mut local_cut = cut.and_then(|c| {
                    let normal = inverse_dir(&motion, c.normal);
                    let point = inverse_dir(&motion, [0, 1, 2].map(|k| c.origin[k] - motion[k][3]));
                    let distance = geom::dot(normal, point);
                    Plane::new(normal.map(|n| n * distance), normal)
                });
                // A section removes only the negative half-space. Products wholly
                // behind the plane retain their uncut projection; products wholly
                // in the removed half contribute nothing. Besides avoiding repeat
                // HLR, this shares the local depth samples with the parent camera.
                if let Some(plane) = &local_cut {
                    let bounds = bounds[&representative];
                    let distances: Vec<_> = (0..8)
                        .map(|mask| {
                            geom::dot(
                                [0, 1, 2].map(|k| {
                                    (if (mask >> k) & 1 == 0 {
                                        bounds.min[k]
                                    } else {
                                        bounds.max[k]
                                    }) - plane.origin[k]
                                }),
                                plane.normal,
                            )
                        })
                        .collect();
                    if distances.iter().all(|d| *d < -1e-6) {
                        continue;
                    }
                    if distances.iter().all(|d| *d > 1e-6) {
                        local_cut = None;
                    }
                }
                let mut key = vec![representative as u64, u64::from(thread_depiction)];
                key.extend(
                    toward
                        .iter()
                        .chain(&up)
                        .chain(
                            local_cut
                                .iter()
                                .flat_map(|p| p.origin.iter().chain(&p.normal)),
                        )
                        .map(|x| ((x * 1e12).round() / 1e12 + 0.).to_bits()),
                );
                if !memo.contains_key(&key) {
                    let native_started = crate::pack::Instant::now();
                    let pieces = match &local_cut {
                        Some(plane) => hlr::project_section(local, &local_camera, plane),
                        None => {
                            match (!carriers.contains_key(&representative) || !thread_depiction)
                                .then(|| reused.get(&representative))
                                .flatten()
                                .and_then(|(_, a)| {
                                    model::pack_projection(a, toward, up)
                                        .or_else(|| resolved(a, toward, up))
                                }) {
                                Some(p) => Ok(p),
                                None => hlr::project(local, &local_camera),
                            }
                        }
                    }
                    .map_err(|e| Error::failed(e.to_string()))?;
                    let seconds = native_started.elapsed().as_secs_f64();
                    native_seconds += seconds;
                    if seconds > 0.1 && std::env::var_os("DRAFTWRIGHT_GA_PROFILE").is_some() {
                        eprintln!(
                            "GA native {} {:?}: {:.3}s",
                            file.parts[representative].name, toward, seconds
                        );
                    }

                    memo.insert(
                        key.clone(),
                        LocalProjection {
                            pieces,
                            depths: BTreeMap::new(),
                        },
                    );
                }
                let entry = memo.get_mut(&key).expect("stored local projection");
                let pieces: Vec<_> = entry
                    .pieces
                    .iter()
                    .filter(|p| retain_hidden || p.visible)
                    .cloned()
                    .collect();
                let delta = camera.map([0, 1, 2].map(|k| motion[k][3]));
                let local_bounds = bounds[&representative];
                let local_depth = (0..8)
                    .map(|mask| {
                        geom::dot(
                            [0, 1, 2].map(|k| {
                                if (mask >> k) & 1 == 0 {
                                    local_bounds.min[k]
                                } else {
                                    local_bounds.max[k]
                                }
                            }),
                            local_camera.toward,
                        )
                    })
                    .fold(f64::NEG_INFINITY, f64::max)
                    + 1.;
                for piece in pieces {
                    let moved = Projected {
                        class: piece.class,
                        visible: piece.visible,
                        points: piece.points.iter().map(|&p| shift(p, delta)).collect(),
                        exact: piece.exact.as_ref().map(|e| translated(e, delta)),
                    };
                    if moved.points.len() < 2 {
                        continue;
                    }
                    let mut resolver = Occlusion {
                        owners,
                        skip: owners
                            .iter()
                            .filter(|(_, instance)| placed.instances.contains(instance))
                            .map(|(&f, _)| f)
                            .collect(),
                        rays,
                        camera,
                        cut,
                        motion,
                        offset: delta,
                        local_camera,
                        local_plane: local_cut,
                        local_depth,
                        local_rays,
                        depths: &mut entry.depths,
                        instances: &placed.instances,
                        depth,
                        occluders: screen_boxes
                            .iter()
                            .filter(|(instances, _)| {
                                !instances.iter().any(|i| placed.instances.contains(i))
                            })
                            .map(|(_, b)| *b)
                            .collect(),
                    };
                    if let Some(pieces) = occlude(&moved, &mut resolver, v.scale) {
                        assembled.extend(pieces.into_iter().filter(|p| retain_hidden || p.visible))
                    } else {
                        unresolved = true;
                        break;
                    }
                }
                if unresolved {
                    break;
                }
            }
            if unresolved {
                break;
            }
        }
        if std::env::var_os("DRAFTWRIGHT_GA_PROFILE").is_some() {
            eprintln!(
                "GA camera {}: {:.3}s, native {:.3}s, fallback {}",
                v.id,
                started.elapsed().as_secs_f64(),
                native_seconds,
                unresolved
            );
        }
        if !unresolved {
            projections.assembly_view(a, &plan.spec, v, assembled);
        }
        // An unresolved ownership ray falls back to whole-assembly kernel HLR.
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::output::{Curve, Item};
    fn distance(p: [f64; 2], items: &[Item]) -> f64 {
        items
            .iter()
            .filter_map(|item| match item {
                Item::Edge(c) => Some(c),
                _ => None,
            })
            .map(|curve| {
                if let Curve::Line(a, b) = curve {
                    let d = [b[0] - a[0], b[1] - a[1]];
                    let len = d[0] * d[0] + d[1] * d[1];
                    let t = (((p[0] - a[0]) * d[0] + (p[1] - a[1]) * d[1]) / len.max(1e-20))
                        .clamp(0., 1.);
                    (p[0] - a[0] - t * d[0]).hypot(p[1] - a[1] - t * d[1])
                } else {
                    (0..=512)
                        .map(|i| {
                            let q = curve.at(i as f64 / 512.);
                            (p[0] - q[0]).hypot(p[1] - q[1])
                        })
                        .fold(f64::INFINITY, f64::min)
                }
            })
            .fold(f64::INFINITY, f64::min)
    }
    #[test]
    fn reused_product_curves_agree_with_whole_assembly_hlr() {
        for name in ["pack-small.step.gz", "pack-nested.step.gz"] {
            let path = Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/assembly")
                .join(name);
            let bytes = model::read_step_file(&path).unwrap();
            let file = StepFile::read(&bytes).unwrap();
            let (part, a, frame) = model::analyse_pack_assembly(name, &bytes, None).unwrap();
            let mut spec = Spec::default();
            spec.sheet.hidden = Some(false);
            let plan = crate::api::plan_analysed(&a, &spec).unwrap();
            let mut reused = model::Projections::default();
            prepare(
                &part,
                &a,
                &plan,
                &file,
                &frame,
                &BTreeMap::new(),
                &BTreeMap::new(),
                None,
                &std::collections::BTreeSet::new(),
                &mut reused,
            )
            .unwrap();
            let actual = model::render_geometry(&part, &a, &plan, &mut reused).unwrap();
            let native =
                model::render_geometry(&part, &a, &plan, &mut model::Projections::default())
                    .unwrap();
            for ((view, actual), (_, native)) in actual.views.iter().zip(&native.views) {
                for (source, reference) in [
                    (&actual.visible, &native.visible),
                    (&native.visible, &actual.visible),
                ] {
                    for curve in source.iter().filter_map(|i| match i {
                        Item::Edge(c) => Some(c),
                        _ => None,
                    }) {
                        for t in [0.2, 0.5, 0.8] {
                            let d = distance(curve.at(t), reference);
                            assert!(
                                d < 0.2,
                                "{name} {view}: visible curve disagrees with native HLR by {d} mm"
                            );
                        }
                    }
                }
            }
        }
    }
}

#[cfg(test)]
mod exact_cache_tests {
    use super::*;
    use crate::model::projected_ink::{EdgeInk, ResolvedView};
    use crate::output::Curve;
    #[test]
    fn resolved_curves_retain_their_exact_rational_geometry() {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/assembly/pack-small.step.gz");
        let bytes = model::read_step_file(&path).unwrap();
        let (_, mut a, _) = model::analyse_pack_assembly("fixture", &bytes, None).unwrap();
        let w = 0.5f64.sqrt();
        let curves = vec![
            Curve::Line([1., 2.], [4., 8.]),
            Curve::Conic {
                centre: [3., 4.],
                radii: [5., 2.],
                x_axis: [1., 0.],
                y_axis: [0., 1.],
                t0: 0.2,
                t1: 2.4,
            },
            Curve::Beziers(vec![vec![[0., 0.], [1., 3.], [4., -2.], [5., 1.]]]),
            Curve::Rational(vec![vec![[1., 0., 1.], [w, w, w], [0., 1., 1.]]]),
        ];
        a.edge_ink = Some(EdgeInk {
            references: BTreeMap::new(),
            edges: Vec::new(),
            planes: Vec::new(),
            curved: Vec::new(),
            resolved: vec![ResolvedView {
                direction: [0., -1., 0.],
                up: [0., 0., 1.],
                visible: curves.clone(),
                hidden: Vec::new(),
            }],
        });
        let cached = resolved(&a, [0., -1., 0.], [0., 0., 1.]).unwrap();
        assert_eq!(cached.len(), curves.len());
        for (source, cached) in curves.iter().zip(cached) {
            let exact = cached.exact.unwrap();
            let (lo, hi) = exact.range();
            for t in [0., 0.1, 0.3, 0.5, 0.8, 1.] {
                let p = source.at(t);
                let q = exact.at(lo + t * (hi - lo));
                assert!((p[0] - q[0]).hypot(p[1] - q[1]) < 1e-10);
            }
        }
    }
}
