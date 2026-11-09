//! Opaque vector pictorials. Convex coplanar patches are merged within each B-rep face,
//! then a BSP splits crossing polygons and emits them back to front. No raster depth buffer.
use crate::{
    model::{Analysis, Invalid, PlannedView, Spec},
    output::{Curve, Item, Layer, Style},
};
use haecceity::{
    Part,
    geom::{cross, dot, norm, sub, unit},
    hlr::{Unresolved, View},
};
use serde::{Deserialize, Serialize};
type P = [f64; 3];

#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ViewStyle {
    Wireframe,
    Shaded,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Polygon {
    pub points: Vec<[f64; 2]>,
    pub colour: [u8; 3],
}
#[derive(Clone, Debug)]
struct Patch {
    points: Vec<P>,
    normal: P,
}

pub(crate) fn enabled(spec: &Spec, id: &str) -> bool {
    spec.views
        .get(id.trim_start_matches("v:"))
        .is_some_and(|s| s.style == Some(ViewStyle::Shaded))
}
pub(crate) fn check(spec: &Spec) -> Result<(), Invalid> {
    for (name, s) in spec.views.iter() {
        let error = |message: &str| Invalid {
            message: message.into(),
            choices: Vec::new(),
            path: Some(format!("views/{name}")),
            also: Vec::new(),
        };
        if (s.style.is_some() || s.colour.is_some() || s.direction.is_some())
            && !matches!(name.as_str(), "iso" | "iso_below")
        {
            return Err(error(
                "Pictorial style, colour and direction apply only to iso and iso_below.",
            ));
        }
        if let Some(d) = s.direction
            && (!d.iter().all(|n| n.is_finite() && n.abs() <= 1e6)
                || View::new(d, [0., 0., 1.]).is_none())
        {
            return Err(error(
                "Pictorial direction must be finite, nonzero and not parallel to Z (up).",
            ));
        }
        if name == "iso_below" && (s.place.is_some() || s.sheet.is_some()) {
            return Err(error(
                "iso_below shares the iso's sheet and reserved area; place the iso instead.",
            ));
        }
    }
    if spec.views.get("iso_below").is_some_and(|s| !s.omit)
        && (spec
            .views
            .get("iso")
            .is_some_and(|s| s.place.is_some() || s.sheet.is_some_and(|n| n > 1))
            || spec
                .sheets
                .as_ref()
                .and_then(|s| s.max)
                .is_some_and(|n| n > 1))
    {
        return Err(crate::model::invalid("Paired pictorials use the automatic single-sheet iso zone; remove the iso placement/sheet assignment or omit iso_below.", &[]).at("views", "iso_below"));
    }
    for (id, d) in &spec.dimensions {
        let view = d
            .place
            .as_ref()
            .and_then(|p| p.view.as_deref())
            .or(d.view.as_deref());
        if view.is_some_and(|v| v == "iso_below" || enabled(spec, v)) && !d.omit {
            return Err(crate::model::invalid(
                "Pictorial shaded views carry no dimensions; choose a principal view.",
                &["front", "plan", "side"],
            )
            .at("dimensions", id));
        }
    }
    for (id, d) in &spec.annotations {
        if d.place
            .as_ref()
            .and_then(|p| p.view.as_deref())
            .is_some_and(|v| v == "iso_below" || enabled(spec, v))
            && !d.omit
        {
            return Err(crate::model::invalid(
                "Pictorial shaded views carry no annotations; choose a principal view.",
                &["front", "plan", "side"],
            )
            .at("annotations", id));
        }
    }
    Ok(())
}

pub(crate) fn record(spec: &Spec, views: &mut [PlannedView]) {
    for v in views {
        let name = v.id.trim_start_matches("v:");
        if let Some(s) = spec.views.get(name) {
            for (field, present) in [
                ("style", s.style.is_some()),
                ("colour", s.colour.is_some()),
                ("direction", s.direction.is_some()),
            ] {
                let path = format!("views/{name}/{field}");
                if present && !v.from_spec.contains(&path) {
                    v.from_spec.push(path);
                }
            }
        }
    }
}

/// Called before annotation planning. Both pictorials share the existing unannotated iso zone.
pub(crate) fn configure(
    a: &Analysis,
    spec: &Spec,
    views: &mut [PlannedView],
) -> Result<(), Invalid> {
    let pair = views.iter().any(|v| v.id == "v:iso") && views.iter().any(|v| v.id == "v:iso_below");
    for v in views
        .iter_mut()
        .filter(|v| matches!(v.id.as_str(), "v:iso" | "v:iso_below"))
    {
        let s = spec.views.get(v.id.trim_start_matches("v:"));
        let direction = s.and_then(|s| s.direction);
        if direction.is_none() && !pair && v.id == "v:iso" {
            continue;
        }
        if let Some(d) = direction {
            v.direction = d;
        }
        if pair {
            let mid = (v.area[0] + v.area[2]) / 2.;
            if v.id == "v:iso" {
                v.area[2] = mid - 2.;
            } else {
                v.area[0] = mid + 2.;
            }
        }
        let camera = View::new(v.direction, v.up).expect("validated pictorial camera");
        let (size, _) = crate::model::extent(a, &camera);
        let fit_scale = v
            .scale
            .min((v.area[2] - v.area[0] - 4.) / size[0])
            .min((v.area[3] - v.area[1] - 10.) / size[1])
            .max(1e-6);
        if v.id == "v:iso" && spec.iso_scale_factor.is_some() && fit_scale < v.scale - 1e-9 {
            return Err(crate::model::invalid(
                "The authored iso scale does not fit the pictorial camera/paired zone; enlarge the paper, omit iso_below or remove iso_scale_factor.",
                &[],
            ));
        }
        v.scale = fit_scale;
        v.at = [
            (v.area[0] + v.area[2]) / 2.,
            (v.area[1] + v.area[3]) / 2. + 3.,
        ];
        if v.id == "v:iso_below" {
            v.scale_note = Some("NOT TO SCALE".into());
        }
        v.default = false;
        v.basis += "; pictorial camera fitted within the existing iso zone (shared when iso_below is requested)";
        if direction.is_some() {
            v.from_spec
                .push(format!("views/{}/direction", v.id.trim_start_matches("v:")));
        }
    }
    Ok(())
}

fn shade(normal: P, camera: &View, colour: [u8; 3]) -> [u8; 3] {
    // Camera-relative upper-left light: readable from above and below, invariant under part translation.
    let light = unit(std::array::from_fn(|k| {
        -camera.x[k] + 2. * camera.y[k] + 3. * camera.toward[k]
    }))
    .unwrap();
    let intensity = 0.35 + 0.65 * dot(normal, light).max(0.);
    colour.map(|v| (v as f64 * intensity).round() as u8)
}

/// Merge adjacent triangles only when their union is convex and coplanar. Holes remain holes:
/// no hull approximation or loop filling is used. Curved faces retain their faceted normals.
fn merge(a: &Patch, b: &Patch, eps: f64) -> Option<Patch> {
    if dot(a.normal, b.normal) < 1. - 1e-10 {
        return None;
    }
    for i in 0..a.points.len() {
        let j = (i + 1) % a.points.len();
        for k in 0..b.points.len() {
            let l = (k + 1) % b.points.len();
            if norm(sub(a.points[i], b.points[l])) > eps
                || norm(sub(a.points[j], b.points[k])) > eps
            {
                continue;
            }
            let mut p = Vec::new();
            for n in 0..a.points.len() - 1 {
                p.push(a.points[(j + n) % a.points.len()]);
            }
            for n in 0..b.points.len() - 1 {
                p.push(b.points[(l + n) % b.points.len()]);
            }
            if (0..p.len()).all(|n| {
                dot(
                    cross(
                        sub(p[(n + 1) % p.len()], p[n]),
                        sub(p[(n + 2) % p.len()], p[(n + 1) % p.len()]),
                    ),
                    a.normal,
                ) >= -eps * eps
            }) {
                // Remove collinear boundary vertices after merging, keeping paths compact.
                let q: Vec<_> = (0..p.len())
                    .filter(|&n| {
                        norm(cross(
                            sub(p[n], p[(n + p.len() - 1) % p.len()]),
                            sub(p[(n + 1) % p.len()], p[n]),
                        )) > eps * eps
                    })
                    .map(|n| p[n])
                    .collect();
                if q.len() >= 3 {
                    return Some(Patch {
                        points: q,
                        normal: a.normal,
                    });
                }
            }
        }
    }
    None
}
fn merge_face(mut patches: Vec<Patch>, eps: f64) -> Vec<Patch> {
    // Stable greedy convex decomposition; merging never crosses a B-rep face boundary.
    let mut i = 0;
    while i < patches.len() {
        let mut j = i + 1;
        while j < patches.len() {
            if let Some(p) = merge(&patches[i], &patches[j], eps) {
                patches[i] = p;
                patches.remove(j);
                j = i + 1;
            } else {
                j += 1;
            }
        }
        i += 1;
    }
    patches
}
fn split(
    p: Patch,
    normal: P,
    offset: f64,
    eps: f64,
) -> (Option<Patch>, Option<Patch>, Option<Patch>) {
    let distances: Vec<_> = p.points.iter().map(|&v| dot(normal, v) - offset).collect();
    let positive = distances.iter().any(|&d| d > eps);
    let negative = distances.iter().any(|&d| d < -eps);
    match (positive, negative) {
        (false, false) => (None, Some(p), None),
        (true, false) => (None, None, Some(p)),
        (false, true) => (Some(p), None, None),
        (true, true) => {
            let (mut back, mut front) = (Vec::new(), Vec::new());
            for i in 0..p.points.len() {
                let j = (i + 1) % p.points.len();
                let (a, b, da, db) = (p.points[i], p.points[j], distances[i], distances[j]);
                if da <= eps {
                    back.push(a);
                }
                if da >= -eps {
                    front.push(a);
                }
                if (da > eps && db < -eps) || (da < -eps && db > eps) {
                    let q = std::array::from_fn(|k| a[k] + da / (da - db) * (b[k] - a[k]));
                    back.push(q);
                    front.push(q);
                }
            }
            (
                Some(Patch {
                    points: back,
                    normal: p.normal,
                }),
                None,
                Some(Patch {
                    points: front,
                    normal: p.normal,
                }),
            )
        }
    }
}

/// Iterative BSP traversal (no native/WASM call-stack growth). Crossing faces and visibility
/// cycles are split. Refuse pathological fragment growth rather than silently misorder faces.
fn ordered(patches: Vec<Patch>, toward: P, eps: f64) -> Result<Vec<Patch>, ()> {
    enum Task {
        Sort(Vec<Patch>),
        Emit(Vec<Patch>),
    }
    let mut tasks = vec![Task::Sort(patches)];
    let mut out = Vec::new();
    let mut fragments = 0;
    while let Some(task) = tasks.pop() {
        match task {
            Task::Emit(p) => out.extend(p),
            Task::Sort(mut p) => {
                if p.is_empty() {
                    continue;
                }
                let pivot = p.remove(p.len() / 2);
                let (n, d) = (pivot.normal, dot(pivot.normal, pivot.points[0]));
                let (mut back, mut same, mut front) = (Vec::new(), vec![pivot], Vec::new());
                for patch in p {
                    let (b, s, f) = split(patch, n, d, eps);
                    if b.is_some() && f.is_some() {
                        fragments += 1;
                    }
                    if fragments > 200_000 {
                        return Err(());
                    }
                    back.extend(b);
                    same.extend(s);
                    front.extend(f);
                }
                if dot(n, toward) < 0. {
                    std::mem::swap(&mut back, &mut front);
                }
                tasks.push(Task::Sort(front));
                tasks.push(Task::Emit(same));
                tasks.push(Task::Sort(back));
            }
        }
    }
    Ok(out)
}

pub(crate) fn render(
    part: &Part,
    a: &Analysis,
    v: &PlannedView,
    spec: &Spec,
) -> Result<Vec<Polygon>, Unresolved> {
    let camera = View::new(v.direction, v.up).expect("validated pictorial camera");
    let diagonal = norm(sub(a.envelope[1], a.envelope[0]));
    let eps = (diagonal * 1e-9).max(1e-9);
    let mut patches = Vec::new();
    let mut refused = Vec::new();
    let drawn = crate::quiddity_evidence::drawn_faces(part);
    for (face, mesh) in part
        .mesh((diagonal * 0.001).max(1e-5), 0.3)
        .into_iter()
        .enumerate()
    {
        if !drawn.contains(&face) {
            continue;
        }
        let Ok(mesh) = mesh else {
            refused.push(face);
            continue;
        };
        if !mesh.converged || mesh.folded_dropped.0 > 0 {
            refused.push(face);
            continue;
        }
        let triangles = mesh
            .triangles
            .iter()
            .filter_map(|t| {
                let points = t.map(|i| mesh.points[i]);
                let normal = unit(cross(sub(points[1], points[0]), sub(points[2], points[0])))?;
                (dot(normal, camera.toward) > 1e-9).then(|| Patch {
                    points: points.to_vec(),
                    normal,
                })
            })
            .collect();
        patches.extend(merge_face(triangles, eps));
    }
    if !refused.is_empty() {
        return Err(Unresolved {
            faces: refused,
            edges: Vec::new(),
        });
    }
    let sorted = ordered(patches, camera.toward, eps).map_err(|()| Unresolved {
        faces: drawn,
        edges: Vec::new(),
    })?;
    let colour = spec
        .views
        .get(v.id.trim_start_matches("v:"))
        .and_then(|s| s.colour)
        .unwrap_or([184, 196, 204]);
    let centre = crate::model::extent(a, &camera).1;
    Ok(sorted
        .into_iter()
        .map(|p| Polygon {
            colour: shade(p.normal, &camera, colour),
            points: p
                .points
                .into_iter()
                .map(|q| {
                    let xy = camera.map(q);
                    [0, 1].map(|k| v.at[k] + v.scale * (xy[k] - centre[k]))
                })
                .collect(),
        })
        .collect())
}

pub(crate) fn layers(id: &str, polygons: &[Polygon]) -> Vec<Layer> {
    let mut layers: Vec<Layer> = Vec::new();
    for p in polygons {
        let item = Item::Face(vec![
            (0..p.points.len())
                .map(|i| Curve::Line(p.points[i], p.points[(i + 1) % p.points.len()]))
                .collect(),
        ]);
        if let Some(last) = layers.last_mut()
            && last.style.colour == p.colour
        {
            // One compound fill per consecutive colour run avoids antialias seams on
            // shared coplanar boundaries and repeats less SVG/PDF path furniture.
            if let (Some(Item::Face(wires)), Item::Face(more)) = (last.items.last_mut(), item) {
                wires.extend(more);
            }
        } else {
            layers.push(Layer {
                name: format!("shaded-{}-{}", id.trim_start_matches("v:"), layers.len()),
                style: Style {
                    colour: p.colour,
                    // Same-colour seam coverage: smaller than the HLR edge by 25×.
                    // It closes subpixel T-junction gaps in vector/PDF rasterisers.
                    width: 0.02,
                    dash: None,
                    fill: true,
                    weight: 0,
                },
                items: vec![item],
            });
        }
    }
    layers
}

#[cfg(test)]
mod tests {
    use super::*;
    fn triangle(p: [P; 3]) -> Patch {
        Patch {
            normal: unit(cross(sub(p[1], p[0]), sub(p[2], p[0]))).unwrap(),
            points: p.to_vec(),
        }
    }
    fn depth(p: &Patch, x: f64, y: f64) -> Option<f64> {
        let mut sign = 0.;
        for i in 0..p.points.len() {
            let (a, b) = (p.points[i], p.points[(i + 1) % p.points.len()]);
            let d = (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0]);
            if d.abs() < 1e-9 {
                return None;
            }
            if sign == 0. {
                sign = d.signum();
            }
            if sign * d < 0. {
                return None;
            }
        }
        let a = p.points[0];
        Some(a[2] - (p.normal[0] * (x - a[0]) + p.normal[1] * (y - a[1])) / p.normal[2])
    }
    fn check_visibility(input: Vec<Patch>) {
        let output = ordered(input.clone(), [0., 0., 1.], 1e-9).unwrap();
        let mut checked = 0;
        for ix in -19..20 {
            for iy in -19..20 {
                let (x, y) = (ix as f64 * 0.091 + 0.0031, iy as f64 * 0.087 + 0.0027);
                let expected = input
                    .iter()
                    .filter_map(|p| depth(p, x, y))
                    .max_by(f64::total_cmp);
                let actual = output.iter().filter_map(|p| depth(p, x, y)).next_back();
                if let Some(z) = expected {
                    assert!(
                        (actual.unwrap() - z).abs() < 1e-7,
                        "{x},{y}: {actual:?} expected {z}"
                    );
                    checked += 1;
                } else {
                    assert!(actual.is_none());
                }
            }
        }
        assert!(checked > 100);
    }
    #[test]
    fn bsp_splits_interpenetrating_triangles_and_cycles_in_either_input_order() {
        let a = triangle([[-2., -2., -2.], [2., -2., 2.], [0., 2., 0.]]);
        let b = triangle([[-2., -2., 2.], [2., -2., -2.], [0., 2., 0.]]);
        let c = triangle([[-2., -1., 0.3], [2., -1., 0.3], [0., 2., 0.3]]);
        let input = vec![a, b, c];
        assert!(ordered(input.clone(), [0., 0., 1.], 1e-9).unwrap().len() > 3);
        check_visibility(input.clone());
        check_visibility(input.into_iter().rev().collect());
    }
    #[test]
    fn bsp_orders_a_noninterpenetrating_visibility_cycle() {
        let strip = |xy: [[f64; 2]; 4], z: fn(f64, f64) -> f64| {
            let points: Vec<P> = xy.into_iter().map(|[x, y]| [x, y, z(x, y)]).collect();
            let normal = unit(cross(sub(points[1], points[0]), sub(points[2], points[0]))).unwrap();
            Patch { points, normal }
        };
        // Three thin strips overlap only near the corners of a triangle. A is behind B
        // at the right corner, B behind C at the top, and C behind A at the left.
        let a = strip(
            [[-2.2, -0.15], [2.2, -0.15], [2.2, 0.15], [-2.2, 0.15]],
            |_, _| 0.,
        );
        let b = strip(
            [[-0.15, 1.85], [1.85, -0.15], [2.15, 0.15], [0.15, 2.15]],
            |x, _| x - 1.,
        );
        let c = strip(
            [[-2.15, 0.15], [-1.85, -0.15], [0.15, 1.85], [-0.15, 2.15]],
            |_, y| y - 1.,
        );
        check_visibility(vec![a, b, c]);
    }
    #[test]
    fn painter_handles_concave_depth_ranges() {
        // Separate surfaces have overlapping depth ranges; average depth cannot order them.
        let a = triangle([[-2., -2., -10.], [2., -2., 10.], [0., 2., 0.]]);
        let b = triangle([[0., -1., -1.], [1., -1., 4.], [0., 1., -1.]]);
        check_visibility(vec![b, a]);
    }
    #[test]
    fn coplanar_merge_preserves_holes_and_reduces_rectangles() {
        let square = vec![
            triangle([[0., 0., 0.], [2., 0., 0.], [2., 2., 0.]]),
            triangle([[0., 0., 0.], [2., 2., 0.], [0., 2., 0.]]),
        ];
        let merged = merge_face(square, 1e-9);
        assert_eq!(merged.len(), 1);
        assert_eq!(merged[0].points.len(), 4);
        let mut ring = Vec::new();
        let outer = [[-2., -2., 0.], [2., -2., 0.], [2., 2., 0.], [-2., 2., 0.]];
        let inner = [[-1., -1., 0.], [1., -1., 0.], [1., 1., 0.], [-1., 1., 0.]];
        for i in 0..4 {
            let j = (i + 1) % 4;
            ring.push(triangle([outer[i], outer[j], inner[j]]));
            ring.push(triangle([outer[i], inner[j], inner[i]]));
        }
        let ring = merge_face(ring, 1e-9);
        assert!(ring.len() < 8);
        assert!(ring.iter().all(|p| depth(p, 0.013, 0.017).is_none()));
        assert!(ring.iter().any(|p| depth(p, 1.5, 0.1).is_some()));
    }
    #[test]
    fn lambert_is_bounded_directional_and_preserves_colour_ratios() {
        let camera = View::new([0., 0., 1.], [0., 1., 0.]).unwrap();
        let light = unit([-1., 2., 3.]).unwrap();
        let colour = [100, 150, 200];
        assert_eq!(shade(light, &camera, colour), colour);
        assert_eq!(shade(light.map(|x| -x), &camera, colour), [35, 53, 70]);
        let side = shade([1., 0., 0.], &camera, colour);
        assert_eq!(side, [35, 53, 70]);
    }
}
