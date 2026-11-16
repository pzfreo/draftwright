//! Engine-written placement intent. Content is always regenerated from STEP and Spec.
mod furniture;
mod geometry;
use crate::model::{self, Analysis, Invalid, Plan, Problem, Spec};
use crate::placement_intent::Place;
use serde::{Deserialize, Serialize};
use sha2::Digest;
use std::{
    cell::{Cell, RefCell},
    collections::{BTreeMap, BTreeSet},
};

pub const SCHEMA: &str = "draftwright.spec/4";
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EnginePins {
    pub written_by: Writer,
    pub source_sha256: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub geometry_sha256: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub layout_sha256: Option<String>,
    pub sheet: Sheet,
    pub views: BTreeMap<String, View>,
    pub annotations: BTreeMap<String, Annotation>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub rebase: Vec<String>,
    #[serde(default)]
    pub origins: [f64; 3],
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Writer {
    Engine,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Sheet {
    pub size: String,
    pub scale: String,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct View {
    pub at: [f64; 2],
    pub direction: [f64; 3],
    pub up: [f64; 3],
    pub scale: f64,
    pub area: [f64; 4],
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub lifted: bool,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub measured: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub furniture_at: Option<[f64; 2]>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub translations: Vec<[f64; 2]>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Annotation {
    pub place: Place,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub route: Option<Route>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub basis: Option<Basis>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub space: Option<[f64; 2]>,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub omit: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub region: Option<crate::dims::CandidateRegion>,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub absolute: bool,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub introduced: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub initial: Option<usize>,
    #[serde(default, skip_serializing_if = "zero_index")]
    pub variant: usize,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "lowercase", deny_unknown_fields)]
pub enum Route {
    Native {
        #[serde(default, skip_serializing_if = "Option::is_none")]
        initial: Option<usize>,
        #[serde(default, skip_serializing_if = "zero_index")]
        variant: usize,
    },
    Note {
        at: Vec<[f64; 2]>,
        bounds: [f64; 4],
    },
    Heading {
        at: [f64; 2],
        bounds: [f64; 4],
    },
    Grid {
        grid: furniture::Grid,
    },
    Marker {
        leader: Option<[[f64; 2]; 2]>,
        bounds: Option<[f64; 4]>,
    },
    Blocks {
        offsets: Vec<[f64; 2]>,
    },
    Dimension {
        #[serde(with = "paths")]
        lines: Vec<[[f64; 2]; 2]>,
        arrows: Vec<[[f64; 2]; 2]>,
        #[serde(default, skip_serializing_if = "zero")]
        rotation: f64,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        bounds: Option<[f64; 4]>,
    },
    Gdt {
        bounds: [f64; 4],
        tip: [f64; 2],
        #[serde(with = "paths")]
        lines: Vec<[[f64; 2]; 2]>,
        arrows: Vec<[[f64; 2]; 2]>,
        fills: Vec<Vec<[f64; 2]>>,
        #[serde(default, skip_serializing_if = "Vec::is_empty")]
        transforms: Vec<[f64; 2]>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        counted_after: Option<usize>,
    },
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Basis {
    pub axis: usize,
    pub station: f64,
}
thread_local! {
    static LOADING:RefCell<Option<EnginePins>> = const { RefCell::new(None) };
    static DIGEST: Cell<Option<[u8;32]>> = const { Cell::new(None) };
}
pub(crate) fn digest() -> Option<[u8; 32]> {
    DIGEST.get()
}
pub(crate) fn loading() -> bool {
    LOADING.with(|p| p.borrow().is_some())
}
pub(crate) fn reader_digest() -> Option<[u8; 32]> {
    READING.with(|p| {
        p.borrow().as_ref().map(|proof| {
            sha2::Sha256::digest(serde_json::to_vec(proof).expect("reader proof serializes")).into()
        })
    })
}
fn with_pins<T>(pins: EnginePins, run: impl FnOnce() -> T) -> T {
    struct Restore(Option<EnginePins>, Option<[u8; 32]>);
    impl Drop for Restore {
        fn drop(&mut self) {
            LOADING.with(|p| *p.borrow_mut() = self.0.take());
            DIGEST.set(self.1);
        }
    }
    let key = sha2::Sha256::digest(serde_json::to_vec(&pins).expect("pins serialize")).into();
    let _restore = Restore(
        LOADING.with(|p| p.replace(Some(pins))),
        DIGEST.replace(Some(key)),
    );
    run()
}
pub(crate) fn views(views: &mut [model::PlannedView]) {
    LOADING.with(|p| {
        let borrowed = p.borrow();
        let Some(pins) = borrowed.as_ref() else {
            return;
        };
        for v in views {
            if let Some(pin) = pins.views.get(&v.id) {
                v.at = pin.at;
                v.direction = pin.direction;
                v.up = pin.up;
                v.scale = pin.scale;
                v.area = pin.area;
                if pin.lifted && !v.basis.contains("free projection row lifted together")
                    && let Some(index) = v.basis.find("; Spec:") {
                    v.basis.insert_str(index, "; free projection row lifted together using measured annotation bounds");
                }
                if v.id == "v:iso" && let Some(index) = v.basis.find("; Spec:") {
                    let suffix = &v.basis[index..];
                    let scale = pins.sheet.scale.split_once(':')
                        .and_then(|(a,b)| Some(a.parse::<f64>().ok()? / b.parse::<f64>().ok()?)).unwrap_or(1.);
                    let factor = (pin.scale / scale * 10000.).round() / 10000.;
                    v.basis = if pin.measured {
                        format!("the automatic orientation view, fitted once around measured row blocks at {:.4} page mm per model mm (not to scale){suffix}", pin.scale)
                    } else if factor == 1. {
                        format!("the isometric, centred in the largest space the other views leave{suffix}")
                    } else {
                        format!("the isometric, centred in the largest space the other views leave and scaled ×{factor} to fill it (not to scale){suffix}")
                    };
                }
            }
        }
    });
}
pub(crate) fn wants_table(view: &str) -> bool {
    LOADING.with(|p| {
        p.borrow().as_ref().is_some_and(|p| {
            p.annotations
                .contains_key(&format!("hole_schedule_{}", view.trim_start_matches("v:")))
        })
    })
}
pub(crate) fn kept_origin(id: &str, axis: usize) -> Option<Basis> {
    LOADING.with(|p| {
        let p = p.borrow();
        let p = p.as_ref()?;
        if p.rebase.iter().any(|v| v == id) {
            return None;
        }
        p.annotations
            .get(id)
            .and_then(|p| p.basis.clone())
            .or(Some(Basis {
                axis,
                station: p.origins[axis],
            }))
    })
}
pub(crate) fn pinned_ink(id: &str) -> bool {
    LOADING.with(|p| {
        p.borrow()
            .as_ref()
            .and_then(|p| p.annotations.get(id))
            .is_some_and(|p| {
                !p.omit && (p.route.is_some() || p.place.lane.is_some() || p.place.offset.is_some())
            })
    })
}

fn basis(a: &Analysis, before: &Plan, id: &str) -> Option<Basis> {
    let d = before.dimensions.iter().find(|d| d.id == id)?;
    let axis = d.measurements.iter().find_map(|m| {
        m.parameter
            .strip_prefix("location.length.")
            .and_then(|s| ["x", "y", "z"].iter().position(|v| *v == s))
    })?;
    if let Some(pin) = before
        .spec
        .engine_pins
        .as_ref()
        .and_then(|p| p.annotations.get(id))
        .filter(|_| {
            !before
                .spec
                .engine_pins
                .as_ref()
                .is_some_and(|p| p.rebase.iter().any(|r| r == id))
        })
        .and_then(|p| p.basis.clone())
    {
        return Some(pin);
    }
    let mut station = a.envelope[0][axis];
    for g in &before.gdt {
        if g.kind == "datum_feature"
            && g.cells
                .first()
                .is_some_and(|letter| d.basis.contains(&format!("datum {letter} (")))
        {
            for &face in &g.faces {
                if let Some(f) = a.face_attachments.get(face)
                    && f.surface.as_deref() == Some("plane")
                    && f.axis == Some(axis)
                    && let Some(v) = f.sites.first()
                {
                    station = v[axis];
                }
            }
        }
    }
    if before
        .spec
        .origin
        .get(["x", "y", "z"][axis])
        .is_some_and(|o| o == "max")
    {
        station = a.envelope[1][axis];
    }
    Some(Basis { axis, station })
}
fn exact_offset(point: [f64; 2], base: [f64; 2]) -> bool {
    [0, 1]
        .into_iter()
        .all(|k| ((point[k] - base[k]) + base[k]).to_bits() == point[k].to_bits())
}
fn relative(point: [f64; 2], base: [f64; 2]) -> [f64; 2] {
    [0, 1].map(|k| {
        let raw = point[k] - base[k];
        let short = (raw * 1e9).round() / 1e9;
        if (short + base[k]).to_bits() == point[k].to_bits() {
            short
        } else {
            raw
        }
    })
}
fn absolute(point: [f64; 2], base: [f64; 2]) -> [f64; 2] {
    [point[0] + base[0], point[1] + base[1]]
}
fn rel_lines(lines: &[[[f64; 2]; 2]], base: [f64; 2]) -> Vec<[[f64; 2]; 2]> {
    lines
        .iter()
        .map(|[a, b]| [relative(*a, base), relative(*b, base)])
        .collect()
}
fn abs_lines(lines: &[[[f64; 2]; 2]], base: [f64; 2]) -> Vec<[[f64; 2]; 2]> {
    lines
        .iter()
        .map(|[a, b]| [absolute(*a, base), absolute(*b, base)])
        .collect()
}
pub(crate) fn persist(a: &Analysis, plan: &mut Plan, before: Option<&Plan>) {
    let mut annotations = BTreeMap::new();
    for d in &plan.dimensions {
        if let Some(p) = &d.placed {
            let at = plan
                .views
                .iter()
                .find(|v| v.id == d.view)
                .map_or([0., 0.], |v| v.at);
            let absolute_pin = std::iter::once(p.label_at)
                .chain([
                    [p.label_box[0], p.label_box[1]],
                    [p.label_box[2], p.label_box[3]],
                ])
                .chain(p.lines.iter().chain(&p.arrows).flatten().copied())
                .any(|q| !exact_offset(q, at));
            let at = if absolute_pin { [0., 0.] } else { at };
            annotations.insert(
                d.id.clone(),
                Annotation {
                    place: Place {
                        view: Some(d.view.clone()),
                        offset: Some(relative(p.label_at, at)),
                        pinned: true,
                        ..Default::default()
                    },
                    route: Some(Route::Dimension {
                        lines: rel_lines(&p.lines, at),
                        arrows: rel_lines(&p.arrows, at),
                        rotation: p.rotation,
                        bounds: (crate::dims::pin_bounds(
                            &d.terms,
                            p.label_at,
                            p.rotation,
                            d.kind == "linear",
                        ) != p.label_box)
                            .then(|| {
                                [
                                    relative([p.label_box[0], p.label_box[1]], at),
                                    relative([p.label_box[2], p.label_box[3]], at),
                                ]
                                .concat()
                                .try_into()
                                .expect("two corners")
                            }),
                    }),
                    space: Some({
                        let b = crate::dims::pin_bounds(
                            &d.terms,
                            p.label_at,
                            p.rotation,
                            d.kind == "linear",
                        );
                        [b[2] - b[0], b[3] - b[1]]
                    }),
                    basis: basis(a, before.unwrap_or(plan), &d.id),
                    omit: false,
                    region: d.region,
                    absolute: absolute_pin,
                    introduced: false,
                    initial: None,
                    variant: 0,
                },
            );
        }
    }
    for g in &plan.gdt {
        if let Some(p) = &g.placed {
            let at = plan
                .views
                .iter()
                .find(|v| v.id == g.view)
                .map_or([0., 0.], |v| v.at);
            let absolute_pin = std::iter::once(crate::dims::gdt::pin_origin(g))
                .chain(std::iter::once(p.tip))
                .chain(p.lines.iter().chain(&p.arrows).flatten().copied())
                .chain(p.fills.iter().flatten().copied())
                .any(|q| !exact_offset(q, at));
            let at = if absolute_pin { [0., 0.] } else { at };
            annotations.insert(
                g.id.clone(),
                Annotation {
                    place: Place {
                        view: Some(g.view.clone()),
                        side: parse_side(&g.side),
                        offset: Some(relative(crate::dims::gdt::pin_origin(g), at)),
                        pinned: true,
                        ..Default::default()
                    },
                    route: Some(Route::Gdt {
                        bounds: p.label_box,
                        transforms: p
                            .pose
                            .as_ref()
                            .map_or(Vec::new(), |(_, steps, _)| steps.clone()),
                        counted_after: p.pose.as_ref().and_then(|(_, _, count)| *count),
                        tip: relative(p.tip, at),
                        lines: rel_lines(&p.lines, at),
                        arrows: rel_lines(&p.arrows, at),
                        fills: p
                            .fills
                            .iter()
                            .map(|f| f.iter().map(|p| relative(*p, at)).collect())
                            .collect(),
                    }),
                    basis: None,
                    space: None,
                    omit: !crate::dims::gdt::has_ink(g),
                    region: g
                        .basis
                        .contains(crate::dims::interior::BASIS)
                        .then_some(crate::dims::CandidateRegion::Interior),
                    absolute: absolute_pin,
                    introduced: false,
                    initial: None,
                    variant: 0,
                },
            );
        }
    }
    for id in plan.omitted.iter().map(|d| d.id.clone()) {
        annotations.entry(id).or_insert(Annotation {
            place: Place {
                pinned: true,
                ..Default::default()
            },
            route: None,
            basis: None,
            space: None,
            omit: true,
            region: None,
            absolute: false,
            introduced: false,
            initial: None,
            variant: 0,
        });
    }
    for t in &plan.hole_tables {
        annotations.insert(
            t.id.clone(),
            Annotation {
                place: Place {
                    view: t.view.clone(),
                    pinned: true,
                    ..Default::default()
                },
                route: Some(Route::Blocks {
                    offsets: t
                        .blocks
                        .iter()
                        .filter_map(|b| b.placed.as_ref().map(|p| [p.cells[0], p.cells[1]]))
                        .collect(),
                }),
                basis: None,
                space: None,
                omit: false,
                region: None,
                absolute: false,
                introduced: false,
                initial: None,
                variant: 0,
            },
        );
    }
    for marker in &plan.balloons {
        annotations.insert(
            marker.id.clone(),
            Annotation {
                place: Place {
                    view: Some(marker.view.clone()),
                    offset: Some(marker.at),
                    pinned: true,
                    ..Default::default()
                },
                route: Some(Route::Marker {
                    leader: marker.leader,
                    bounds: marker.label_box,
                }),
                basis: None,
                space: None,
                omit: false,
                region: None,
                absolute: true,
                introduced: false,
                initial: None,
                variant: 0,
            },
        );
    }
    furniture::persist(plan, &mut annotations);
    let previous = before.unwrap_or(plan);
    let mut origins = previous
        .spec
        .engine_pins
        .as_ref()
        .map_or(a.envelope[0], |p| p.origins);
    if previous.spec.engine_pins.is_none() {
        for d in &previous.dimensions {
            if let Some(b) = basis(a, previous, &d.id) {
                origins[b.axis] = b.station;
            }
        }
    }
    for (id, pin) in &mut annotations {
        pin.introduced = if let Some(previous) = &previous.spec.engine_pins {
            previous
                .annotations
                .get(id)
                .is_none_or(|old| old.introduced || (old.omit && !pin.omit))
        } else {
            !previous.dimensions.iter().any(|d| &d.id == id)
                && !previous.gdt.iter().any(|g| &g.id == id)
                && !previous.balloons.iter().any(|b| &b.id == id)
                && !previous.hole_tables.iter().any(|t| &t.id == id)
                && !previous.omitted.iter().any(|o| &o.id == id)
        };
    }
    let geometry_sha256 = previous
        .spec
        .engine_pins
        .as_ref()
        .and_then(|p| p.geometry_sha256.clone());
    plan.spec.schema = SCHEMA.into();
    plan.spec.engine_pins = Some(Box::new(EnginePins {
        written_by: Writer::Engine,
        source_sha256: plan.binding.source_sha256.clone(),
        geometry_sha256,
        layout_sha256: Some(layout_key(&plan.spec)),
        sheet: Sheet {
            size: plan.sheet.size.clone(),
            scale: plan.sheet.scale.clone(),
        },
        views: plan
            .views
            .iter()
            .map(|v| {
                (
                    v.id.clone(),
                    View {
                        at: v.at,
                        direction: v.direction,
                        up: v.up,
                        scale: v.scale,
                        area: v.area,
                        lifted: v.basis.contains("free projection row lifted together"),
                        measured: v.basis.contains("automatic orientation view, fitted once"),
                        furniture_at: v.line_pose.as_ref().map(|p| p.0),
                        translations: v.line_pose.as_ref().map_or(Vec::new(), |p| p.1.clone()),
                    },
                )
            })
            .collect(),
        annotations,
        rebase: Vec::new(),
        origins,
    }));
    compact_dimensions(a, plan);
}
fn compact_dimensions(a: &Analysis, plan: &mut Plan) {
    let pins = plan.spec.engine_pins.as_ref().unwrap();
    let recipes = with_pins((**pins).clone(), || {
        crate::dims::pin_geometry::Recipes::new(a, &plan.spec, &plan.views)
    });
    let updates: Vec<_> = with_pins((**pins).clone(), || {
        plan.dimensions
            .iter()
            .filter_map(|d| {
                let view = plan.views.iter().find(|v| v.id == d.view)?;
                let (place, initial, variant) = recipes.encode(a, &plan.spec, view, d)?;
                Some((d.id.clone(), place, initial, variant))
            })
            .collect()
    });
    let pins = plan.spec.engine_pins.as_mut().unwrap();
    for (id, place, initial, variant) in updates {
        if let Some(pin) = pins.annotations.get_mut(&id) {
            pin.place = place;
            pin.place.pinned = false; // engine_pins entries are held implicitly
            pin.route = None;
            pin.initial = initial;
            pin.variant = variant;
            pin.space = None;
            pin.absolute = false;
        }
    }
}
fn zero_index(n: &usize) -> bool {
    *n == 0
}
fn parse_side(side: &str) -> Option<crate::placement_intent::Side> {
    serde_json::from_value(side.into()).ok()
}
fn apply(plan: &mut Plan, pins: &EnginePins, analysis: &Analysis) -> Vec<String> {
    let effective = crate::spec_gdt::effective(analysis, &plan.spec).expect("validated Spec");
    let (mut lowered, _) = crate::pmi_lowering::lower_in(
        &effective,
        &|name| plan.views.iter().any(|v| v.id == format!("v:{name}")),
        plan.spec.units,
    );
    crate::label_text::annotations(&mut lowered, &plan.spec).expect("validated label text");
    let mut remaining: BTreeSet<_> = pins.annotations.keys().cloned().collect();

    let recipes = pins
        .annotations
        .values()
        .any(|p| (p.route.is_none() && !p.omit) || matches!(p.route, Some(Route::Native { .. })))
        .then(|| crate::dims::pin_geometry::Recipes::new(analysis, &plan.spec, &plan.views));
    for d in &mut plan.dimensions {
        if let Some(pin) = pins.annotations.get(&d.id) {
            if pin.route.is_none() && !pin.omit || matches!(pin.route, Some(Route::Native { .. })) {
                let (initial, variant) =
                    if let Some(Route::Native { initial, variant }) = &pin.route {
                        (*initial, *variant)
                    } else {
                        (pin.initial, pin.variant)
                    };
                let view_id = pin.place.view.clone().unwrap_or_else(|| d.view.clone());
                if let Some(view) = plan.views.iter().find(|v| v.id == view_id)
                    && let Some(recipes) = &recipes
                    && let Some(placed) = recipes
                        .candidates(analysis, &plan.spec, view, d, &pin.place, initial)
                        .into_iter()
                        .nth(variant)
                {
                    d.view.clone_from(&view_id);
                    d.region = pin.region;
                    d.placed = Some(placed);
                    remaining.remove(&d.id);
                }
                continue;
            }
            if let Some(space) = pin.space {
                let b = crate::dims::pin_bounds(
                    &d.terms,
                    [0., 0.],
                    pin.route
                        .as_ref()
                        .and_then(|r| {
                            if let Route::Dimension { rotation, .. } = r {
                                Some(*rotation)
                            } else {
                                None
                            }
                        })
                        .unwrap_or(0.),
                    d.kind == "linear",
                );
                if (space[0] - (b[2] - b[0])).abs() > 1e-7
                    || (space[1] - (b[3] - b[1])).abs() > 1e-7
                {
                    continue;
                }
            }
            remaining.remove(&d.id);
            d.region = pin.region;
            if let Some(view) = &pin.place.view {
                d.view.clone_from(view);
            }
            if let (
                Some(at),
                Some(Route::Dimension {
                    lines,
                    arrows,
                    rotation,
                    bounds,
                }),
            ) = (pin.place.offset, &pin.route)
            {
                if let Some(view) = &pin.place.view {
                    d.view.clone_from(view);
                }
                let base = plan
                    .views
                    .iter()
                    .find(|v| v.id == d.view)
                    .map_or([0., 0.], |v| v.at);
                let base = if pin.absolute { [0., 0.] } else { base };
                let label_at = absolute(at, base);
                let label_box = bounds.map_or_else(
                    || crate::dims::pin_bounds(&d.terms, label_at, *rotation, d.kind == "linear"),
                    |bounds| {
                        let a = absolute([bounds[0], bounds[1]], base);
                        let b = absolute([bounds[2], bounds[3]], base);
                        [a[0], a[1], b[0], b[1]]
                    },
                );
                d.placed = Some(crate::dims::Placed {
                    lines: abs_lines(lines, base),
                    arrows: abs_lines(arrows, base),
                    label_at: absolute(at, base),
                    rotation: *rotation,
                    label_box,
                });
            }
        }
    }
    for g in &mut plan.gdt {
        if let Some(pin) = pins.annotations.get(&g.id) {
            let original = g.clone();
            crate::dims::gdt::retain_region(
                g,
                pin.region == Some(crate::dims::CandidateRegion::Interior),
            );
            if let Some(view) = &pin.place.view {
                g.view.clone_from(view);
            }
            if let Some(side) = pin.place.side {
                g.side = side.name().into();
            }
            if let Some(item) = lowered.iter().find(|i| i.id == g.id)
                && !(pin.omit && crate::dims::gdt::has_ink(g))
            {
                let base = plan
                    .views
                    .iter()
                    .find(|v| v.id == g.view)
                    .map_or([0., 0.], |v| v.at);
                let base = if pin.absolute { [0., 0.] } else { base };
                let mut decoded = pin.clone();
                decoded.place.offset = decoded.place.offset.map(|p| absolute(p, base));
                if let Some(Route::Gdt {
                    tip,
                    lines,
                    arrows,
                    fills,
                    ..
                }) = &mut decoded.route
                {
                    *tip = absolute(*tip, base);
                    *lines = abs_lines(lines, base);
                    *arrows = abs_lines(arrows, base);
                    for fill in fills {
                        for p in fill {
                            *p = absolute(*p, base);
                        }
                    }
                }
                if crate::dims::gdt::apply_pin(
                    g,
                    &decoded,
                    item,
                    plan.spec.units,
                    crate::dims::hole_marks::controlled_count(analysis, &item.faces),
                ) {
                    remaining.remove(&g.id);
                } else {
                    *g = original;
                }
            }
        }
    }
    for marker in &mut plan.balloons {
        if let Some(pin) = pins.annotations.get(&marker.id) {
            remaining.remove(&marker.id);
            if let (Some(at), Some(Route::Marker { leader, bounds })) =
                (pin.place.offset, &pin.route)
            {
                marker.at = at;
                marker.leader = *leader;
                marker.label_box = *bounds;
            }
        }
    }
    for t in &plan.hole_tables {
        remaining.remove(&t.id);
    }
    for o in &plan.omitted {
        remaining.remove(&o.id);
    }
    furniture::apply(plan, pins, &mut remaining);
    remaining.into_iter().collect()
}
pub(crate) fn apply_loading(plan: &mut Plan, analysis: &Analysis) {
    views(&mut plan.views);
    LOADING.with(|p| {
        if let Some(pins) = p.borrow().as_ref() {
            apply(plan, pins, analysis);
            crate::incremental::recheck(analysis, plan);
        }
    });
}
fn finding(plan: &mut Plan, code: &str, severity: &str, refs: Vec<String>, message: &str) {
    plan.problems.push(Problem {
        id: format!("p:{code}"),
        code: code.into(),
        severity: severity.into(),
        message: message.into(),
        refs,
        ..Default::default()
    });
    plan.status = crate::completeness::status(&plan.problems);
}
pub(crate) fn plan(a: &Analysis, spec: &Spec, recovered: bool) -> Result<Plan, Invalid> {
    let key = serde_json::to_vec(&(spec, recovered)).expect("pinned plan inputs serialize");
    crate::planning::memo(a, "engine-pins-plan", key, || {
        plan_uncached(a, spec, recovered)
    })
}
fn plan_uncached(a: &Analysis, spec: &Spec, recovered: bool) -> Result<Plan, Invalid> {
    let pins = spec.engine_pins.as_ref().expect("engine pins present");
    let mut base = spec.clone();
    base.engine_pins = None;
    base.schema = model::SPEC_SCHEMA.into();
    let run = |s: &Spec| {
        if recovered {
            model::plan_recovered(a, s)
        } else {
            model::plan(a, s)
        }
    };
    let camera_changed = base.views.iter().any(|(name, requested)| {
        let pin = pins.views.get(&format!("v:{name}"));
        (requested.omit && pin.is_some())
            || requested
                .direction
                .is_some_and(|direction| pin.is_none_or(|pin| direction != pin.direction))
    });
    let table_changed = base.hole_table == crate::hole_table::Setting::Never
        && pins
            .annotations
            .keys()
            .any(|id| id.starts_with("hole_schedule_"));
    let revised = pins.source_sha256 != a.binding.source_sha256;
    let same_geometry = pins
        .geometry_sha256
        .as_ref()
        .is_some_and(|sha| known_geometry(&a.binding).as_ref() == Some(sha));
    if pins
        .layout_sha256
        .as_ref()
        .is_some_and(|key| key != &layout_key(&base))
        || camera_changed
        || table_changed
        || (revised && !same_geometry)
        || base
            .sheet
            .scale
            .as_ref()
            .is_some_and(|s| s != &pins.sheet.scale)
        || base
            .sheet
            .size
            .as_ref()
            .is_some_and(|s| s != &pins.sheet.size)
    {
        let mut result = run(&base)?;
        finding(
            &mut result,
            "engine_pins_replanned",
            "warning",
            vec!["engine_pins".into()],
            "The STEP geometry or requested layout changed. The previous layout cannot be retained: stale engine pins were dropped and the drawing was fully re-planned. Review the new placements before printing.",
        );
        return Ok(result);
    }
    let mut forced = base.clone();
    forced.sheet.size = Some(pins.sheet.size.clone());
    forced.sheet.scale = Some(pins.sheet.scale.clone());
    forced.sheet.keep_layout = None;
    forced.sheets = None;
    let mut filtered = (**pins).clone();
    filtered.source_sha256.clone_from(&a.binding.source_sha256);
    filtered
        .annotations
        .retain(|id, _| !pins.rebase.contains(id));
    let scheduled = pins
        .annotations
        .keys()
        .any(|id| id.starts_with("hole_schedule_"));
    let mut reference = with_pins(filtered.clone(), || {
        if scheduled {
            model::plan_escalated(a, &forced)
        } else {
            model::plan_recovered(a, &forced)
        }
    })?;
    with_pins(filtered.clone(), || views(&mut reference.views));
    let mut dropped = with_pins(filtered.clone(), || apply(&mut reference, &filtered, a));
    dropped.extend(
        pins.views
            .keys()
            .filter(|id| !reference.views.iter().any(|v| &v.id == *id))
            .cloned(),
    );
    reference.spec = base.clone();
    reference.spec.engine_pins = Some(Box::new(filtered.clone()));
    let new_ink: BTreeSet<String> = reference
        .dimensions
        .iter()
        .map(|d| &d.id)
        .chain(reference.gdt.iter().map(|g| &g.id))
        .chain(reference.notes.iter().map(|n| &n.id))
        .chain(reference.tables.iter().map(|t| &t.id))
        .filter(|id| !pins.annotations.contains_key(*id) || dropped.contains(id))
        .cloned()
        .collect();
    // A title/material edit already has its final pinned solve. Only newly placed
    // ink needs the published-reference solve to reserve retained annotations.
    let mut result = if new_ink.is_empty() {
        reference.clone()
    } else {
        with_pins(filtered.clone(), || {
            crate::incremental::plan(a, &reference, &base)
        })?
    };
    let constrained = new_ink
        .into_iter()
        .filter(|id| {
            result
                .problems
                .iter()
                .any(|p| p.severity != "info" && p.subjects.iter().any(|s| s == id))
        })
        .collect();
    crate::incremental::constrain(&mut result, constrained);
    crate::incremental::recheck(a, &mut result);
    result.spec = spec.clone();
    persist(a, &mut result, Some(&reference));
    if !dropped.is_empty() {
        finding(
            &mut result,
            "engine_pins_partly_retained",
            "warning",
            dropped,
            "The kept layout was partly retained: missing or changed item pins were dropped. Existing stable item placements and dimension bases were kept; inspect the affected items.",
        );
    }
    Ok(result)
}

/// Full editing releases layout pins for deliberate composition changes; annotation edits
/// release just their own route, while ordinary title/notes/material changes retain the frame.
pub(crate) fn edit(before: &Spec, after: &mut Spec, ops: &[serde_json::Value]) {
    if before.engine_pins.is_none() || before == after {
        return;
    }
    let mut old_sheet = before.sheet.clone();
    let mut new_sheet = after.sheet.clone();
    old_sheet.title.clear();
    new_sheet.title.clear();
    let layout = ops.iter().any(|op| {
        let p = op["path"].as_str().unwrap_or("").trim_matches('/');
        [
            "views",
            "sheets",
            "hole_table",
            "units",
            "dimension_source",
            "dimension_schemes",
            "origin",
            "iso_scale_factor",
        ]
        .iter()
        .any(|root| p == *root || p.starts_with(&format!("{root}/")))
            || (p == "sheet" && old_sheet != new_sheet)
            || (p.starts_with("sheet/")
                && !p.starts_with("sheet/title/")
                && !["sheet/title", "sheet/zones"].contains(&p))
    });
    if layout {
        after.engine_pins = None;
        after.schema = model::SPEC_SCHEMA.into();
        return;
    }
    if let Some(pins) = &mut after.engine_pins {
        if before.precision != after.precision {
            pins.annotations.retain(|_, p| p.route.is_some() || p.omit);
        }
        if before.notes != after.notes {
            for note in &before.notes {
                if after.notes.iter().find(|n| n.id == note.id) != Some(note) {
                    pins.annotations.remove(&note.id);
                }
            }
            pins.annotations
                .retain(|_, p| !matches!(p.route, Some(Route::Heading { .. })));
        }
        for table in &before.tables {
            if after.tables.iter().find(|t| t.id == table.id) != Some(table) {
                pins.annotations.remove(&table.id);
            }
        }
        for op in ops {
            let parts: Vec<_> = op["path"]
                .as_str()
                .unwrap_or("")
                .trim_matches('/')
                .split('/')
                .collect();
            if parts.len() >= 2 && ["dimensions", "annotations"].contains(&parts[0]) {
                pins.annotations
                    .remove(&parts[1].replace("~1", "/").replace("~0", "~"));
            }
        }
    }
    if after.engine_pins.is_none() {
        after.schema = model::SPEC_SCHEMA.into();
    }
}

type FurniturePoses = BTreeMap<String, ([f64; 2], Vec<[f64; 2]>)>;
pub(crate) fn furniture_poses() -> Option<FurniturePoses> {
    LOADING.with(|p| {
        p.borrow().as_ref().map(|p| {
            p.views
                .iter()
                .filter_map(|(id, v)| {
                    v.furniture_at
                        .map(|at| (id.clone(), (at, v.translations.clone())))
                })
                .collect()
        })
    })
}

fn zero(v: &f64) -> bool {
    *v == 0.
}
mod paths {
    use serde::{Deserialize, Deserializer, Serialize, Serializer};
    pub fn serialize<S: Serializer>(
        lines: &[[[f64; 2]; 2]],
        serializer: S,
    ) -> Result<S::Ok, S::Error> {
        let mut paths: Vec<Vec<[f64; 2]>> = Vec::new();
        for [a, b] in lines {
            if let Some(path) = paths.last_mut()
                && path.last() == Some(a)
            {
                path.push(*b);
            } else {
                paths.push(vec![*a, *b]);
            }
        }
        paths.serialize(serializer)
    }
    pub fn deserialize<'de, D: Deserializer<'de>>(
        deserializer: D,
    ) -> Result<Vec<[[f64; 2]; 2]>, D::Error> {
        let paths = Vec::<Vec<[f64; 2]>>::deserialize(deserializer)?;
        if paths.iter().any(|p| p.len() < 2) {
            return Err(serde::de::Error::custom(
                "a route path needs at least two points",
            ));
        }
        Ok(paths
            .into_iter()
            .flat_map(|p| p.windows(2).map(|p| [p[0], p[1]]).collect::<Vec<_>>())
            .collect())
    }
}

pub(crate) fn block_offsets(id: &str) -> Option<Vec<[f64; 2]>> {
    LOADING.with(
        |p| match p.borrow().as_ref()?.annotations.get(id)?.route.as_ref()? {
            Route::Blocks { offsets } => Some(offsets.clone()),
            _ => None,
        },
    )
}

type MarkerPlace = ([f64; 2], Option<[[f64; 2]; 2]>);
pub(crate) fn marker_place(id: &str) -> Option<MarkerPlace> {
    LOADING.with(|p| {
        let p = p.borrow();
        let pin = p.as_ref()?.annotations.get(id)?;
        if let Some(Route::Marker { leader, .. }) = &pin.route {
            Some((pin.place.offset?, *leader))
        } else {
            None
        }
    })
}

pub(crate) fn introduced() -> Vec<String> {
    LOADING.with(|p| {
        p.borrow().as_ref().map_or(Vec::new(), |p| {
            p.annotations
                .iter()
                .filter(|(_, p)| p.introduced)
                .map(|(id, _)| id.clone())
                .collect()
        })
    })
}

pub(crate) fn origin_frame() -> Option<[f64; 3]> {
    LOADING.with(|p| p.borrow().as_ref().map(|p| p.origins))
}

pub(crate) fn keep_views(views: &mut Vec<model::PlannedView>) {
    LOADING.with(|p| {
        if let Some(pins) = p.borrow().as_ref() {
            views.retain(|v| pins.views.contains_key(&v.id));
        }
    });
}

type ReadGeometry = ([u8; 32], Option<String>);
thread_local! {
    static READING: RefCell<Option<ReadGeometry>> = const { RefCell::new(None) };
}

/// A reader's proof belongs to this synchronous planning call, never a shared memo.
#[must_use]
pub(crate) struct GeometryRead(Option<ReadGeometry>);
impl Drop for GeometryRead {
    fn drop(&mut self) {
        READING.with(|p| p.replace(self.0.take()));
    }
}
fn geometry_key(binding: &model::Binding) -> [u8; 32] {
    sha2::Sha256::digest(serde_json::to_vec(binding).expect("binding serializes")).into()
}
fn known_geometry(binding: &model::Binding) -> Option<String> {
    let key = geometry_key(binding);
    READING.with(|p| {
        p.borrow()
            .as_ref()
            .filter(|(k, _)| *k == key)
            .and_then(|(_, digest)| digest.clone())
    })
}
pub(crate) fn geometry(part: &crate::haecceity::Part) -> Result<String, Invalid> {
    // Verify this Part, even for trusted in-process callers reusing a Binding.
    geometry::digest(part).ok_or_else(|| Invalid {
        message: "Cannot verify identical B-rep: unresolved or empty geometry; open the updated STEP for a full plan.".into(),
        path: None,
        choices: Vec::new(),
        also: Vec::new(),
    })
}
/// A byte revision is not a geometry change. Verify only when persisted source identity changed.
pub(crate) fn prepare_read(
    part: &crate::haecceity::Part,
    a: &Analysis,
    spec: &Spec,
) -> GeometryRead {
    let proof = spec
        .engine_pins
        .as_ref()
        .is_some_and(|p| p.source_sha256 != a.binding.source_sha256)
        .then(|| {
            // Failed verification releases unverifiable pins with a finding; it never prevents opening.
            (geometry_key(&a.binding), geometry(part).ok())
        });
    GeometryRead(READING.with(|p| p.replace(proof)))
}

fn layout_key(spec: &Spec) -> String {
    // Hash only authored composition intent. Text and requirements are fresh content,
    // not a reason to discard every retained annotation's placement.
    let dimensions: BTreeMap<_, _> = spec
        .dimensions
        .iter()
        .filter_map(|(id, d)| d.place.as_ref().map(|p| (id, p)))
        .collect();
    let annotations: BTreeMap<_, _> = spec
        .annotations
        .iter()
        .filter_map(|(id, a)| a.place.as_ref().map(|p| (id, p)))
        .collect();
    let layout = serde_json::json!({
        "sheet": {"size":spec.sheet.size,"scale":spec.sheet.scale,
            "paper":spec.sheet.paper,"keep_layout":spec.sheet.keep_layout,
            "view_gutter":spec.sheet.view_gutter,"table_columns":spec.sheet.table_columns},
        "views":spec.views,"sheets":spec.sheets,"units":spec.units,
        "part":spec.part,"origin":spec.origin,"iso_scale_factor":spec.iso_scale_factor,
        "dimension_source":spec.dimension_source,"dimension_schemes":spec.dimension_schemes,
        "hole_table":spec.hole_table,"dimensions":dimensions,"annotations":annotations
    });
    sha2::Sha256::digest(serde_json::to_vec(&layout).expect("layout intent serializes"))
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

#[cfg(test)]
mod memo_tests {
    use super::*;
    #[test]
    fn loading_pins_keys_placement_and_shares_pure_memos() {
        let memo = crate::planning::BoundedMemo::new(2);
        let key = [9; 32];
        memo.insert(key, 10);
        let pins = serde_json::from_value(serde_json::json!({
            "written_by":"engine","source_sha256":"test",
            "sheet":{"size":"A4","scale":"1:1"},"views":{},"annotations":{}
        }))
        .unwrap();
        with_pins(pins, || {
            let pinned = crate::planning::spaced_digest(&key);
            assert_eq!(memo.get(&pinned), None);
            memo.insert(pinned, 20);
            assert_eq!(memo.get(&pinned), Some(20));
            assert_eq!(memo.get(&key), Some(10));
        });
        assert_eq!(memo.get(&key), Some(10));
    }

    #[test]
    fn reader_proofs_are_scoped_and_key_placement_memos() {
        let memo = crate::planning::BoundedMemo::new(2);
        let key = [7; 32];
        memo.insert(key, 10);
        {
            let _proof =
                GeometryRead(READING.with(|p| p.replace(Some((key, Some("verified".into()))))));
            assert!(reader_digest().is_some());
            assert!(
                !loading(),
                "a reader proof must not enable placement overrides"
            );
            let verified = crate::planning::spaced_digest(&key);
            assert_eq!(memo.get(&verified), None);
            memo.insert(verified, 20);
            assert_eq!(memo.get(&verified), Some(20));
            assert_eq!(memo.get(&key), Some(10));
            {
                let _nested = GeometryRead(READING.with(|p| p.replace(None)));
                assert!(!reader_digest().is_some());
                assert_eq!(memo.get(&key), Some(10));
            }
            assert!(reader_digest().is_some());
        }
        assert!(!reader_digest().is_some());
        assert_eq!(memo.get(&key), Some(10));
    }
    #[test]
    fn repeated_pinned_title_preview_hits_retained_plan_memo() {
        let bytes = include_bytes!("../tests/fixtures/parts/plate.step");
        let (part, a) = model::analyse_native_bytes("plate", bytes).unwrap();
        let mut s = crate::session::Session::new(part, a, Spec::default()).unwrap();
        s.apply_incremental(&[], true).unwrap();
        assert!(s.spec().engine_pins.is_some());
        let ops = [crate::items::Op::set(
            "sheet/title/drawn_by",
            serde_json::json!("Reviewer"),
        )];
        let before = serde_json::to_value(s.state()).unwrap();
        let first = s.apply(&ops, false).unwrap();
        let hits = crate::planning::memo_hits("engine-pins-plan");
        let second = s.apply(&ops, false).unwrap();
        assert!(crate::planning::memo_hits("engine-pins-plan") > hits);
        assert_eq!(
            serde_json::to_value(first).unwrap(),
            serde_json::to_value(second).unwrap()
        );
        assert_eq!(serde_json::to_value(s.state()).unwrap(), before);
    }
}
