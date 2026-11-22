//! Direct authored constraints across an inclined cylindrical axis.
use super::{Mark, location::AXES};
use crate::model::Analysis;
use crate::model::dimension_geometry::Reference;

fn sub(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    std::array::from_fn(|k| a[k] - b[k])
}
fn dot(a: [f64; 3], b: [f64; 3]) -> f64 {
    (0..3).map(|k| a[k] * b[k]).sum()
}
fn unit(a: [f64; 3]) -> Option<[f64; 3]> {
    let n = dot(a, a).sqrt();
    (n > 1e-10).then(|| a.map(|v| v / n))
}

fn cross(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}

fn axis(a: &Analysis, id: &str) -> Option<([f64; 3], [f64; 3])> {
    let f = a.features.iter().find(|f| f.id == id && f.kind == "hole")?;
    if crate::bridge::holes::principal(f.fingerprint.axis?).is_some() {
        return None;
    }
    if crate::bridge::holes::Hole::read(f, &crate::bridge::holes::frame(a))
        .ok()?
        .principal
        .is_some()
    {
        return None;
    }
    let Reference::Edge { ends, .. } = a.dimension_reference(&format!("axis:{}", f.id)).ok()?
    else {
        return None;
    };
    let d = unit(sub(ends[1], ends[0]))?;
    crate::bridge::holes::principal(d)
        .is_none()
        .then_some((ends[0], d))
}

struct Constraint {
    normal: [f64; 3],
    witnesses: [[f64; 3]; 2],
    value: f64,
}

// A physical edge/plane is an anchor. Another feature axis is only a relative
// separation, and a true point-to-axis distance is a radius, not a linear station.
fn constraint(a: &Analysis, id: &str, refs: &[String], along: &str) -> Option<Constraint> {
    let (origin, d) = axis(a, id)?;
    let i = refs.iter().position(|r| r.starts_with("axis:"))?;
    if refs.len() != 2 || refs[1 - i].starts_with("axis:") {
        return None;
    }
    let Reference::Edge { ends, .. } = a.dimension_reference(&refs[i]).ok()? else {
        return None;
    };
    let other_axis = unit(sub(ends[1], ends[0]))?;
    let off = sub(ends[0], origin);
    if dot(d, other_axis).abs() < 1. - 1e-8 || dot(cross(off, d), cross(off, d)) > 1e-10 {
        return None;
    }
    // An explicit face alias must belong to this feature; coincident peers cannot
    // lend their identity just because their axes happen to share a line.
    if refs[i] != format!("axis:{id}") {
        let face = a.dimension_reference_axis_face(&refs[i])?;
        if !a
            .features
            .iter()
            .find(|f| f.id == id)?
            .faces
            .contains(&face)
        {
            return None;
        }
    }
    let anchor = a.dimension_reference(&refs[1 - i]).ok()?;
    let membership = match &anchor {
        Reference::Plane { membership, .. }
        | Reference::Edge { membership, .. }
        | Reference::Vertex { membership, .. } => membership,
        _ => return None,
    };
    if membership.faces.iter().any(|i| {
        a.features
            .iter()
            .find(|f| f.id == id)
            .is_some_and(|f| f.faces.contains(i))
    }) {
        return None;
    }
    if along == "true"
        && match &anchor {
            Reference::Vertex { .. } => true,
            Reference::Edge { ends, .. } => dot(d, unit(sub(ends[1], ends[0]))?).abs() > 1. - 1e-8,
            _ => false,
        }
    {
        return None;
    }
    let m = a.measure_references(refs, "linear", Some(along)).ok()?;
    let witnesses = m.witnesses?;
    let normal = if along == "true" {
        unit(sub(witnesses[1], witnesses[0]))?
    } else {
        let k = AXES.iter().position(|v| *v == along)?;
        std::array::from_fn(|j| if j == k { 1. } else { 0. })
    };
    (dot(normal, d).abs() < 1e-6).then_some(Constraint {
        normal,
        witnesses,
        value: m.value,
    })
}

fn view_normal(view: &str) -> Option<[f64; 3]> {
    match view.trim_start_matches("v:") {
        "front" | "rear" => Some([0., 1., 0.]),
        "side" => Some([1., 0., 0.]),
        "plan" => Some([0., 0., 1.]),
        _ => None,
    }
}

/// Two independent, printed linear constraints remove both transverse freedoms.
/// Translation along the axis changes no piercing point and needs no station.
pub(crate) fn located(a: &Analysis, marks: &[Mark], id: &str) -> bool {
    let Some((_, d)) = axis(a, id) else {
        return false;
    };
    let mut normals = Vec::new();
    for mark in marks {
        if mark.text.trim_start().starts_with('(') {
            continue;
        }
        for claim in &mark.claims {
            let Some(refs) = claim.feature.strip_prefix("geometry:") else {
                continue;
            };
            let Some(along) = claim.parameter.strip_prefix("linear.") else {
                continue;
            };
            let refs: Vec<_> = refs.split('|').map(String::from).collect();
            let Some(c) = constraint(a, id, &refs, along) else {
                continue;
            };
            if !super::location::nominal_in(&mark.text, c.value, mark.units, mark.precision) {
                continue;
            }
            let Some(toward) = mark.view.as_deref().and_then(view_normal) else {
                continue;
            };
            if dot(c.normal, toward).abs() > 1e-6 {
                continue;
            }
            if !ink_matches(a, mark, &c) {
                continue;
            }
            if normals
                .iter()
                .any(|&n| dot(cross(n, c.normal), d).abs() > 1e-6)
            {
                return true;
            }
            normals.push(c.normal);
        }
    }
    false
}

// When ink is available, its extension-line stations must match the references.
// Mark-only captured evidence still remeasures the references and leading nominal.
fn ink_matches(a: &Analysis, mark: &Mark, c: &Constraint) -> bool {
    let Some(word) = mark
        .claims
        .iter()
        .find_map(|c| c.feature.strip_prefix("station_witness:"))
    else {
        return true;
    };
    let Ok((frame, lines)) =
        serde_json::from_str::<(crate::output::DrawnFrame, Vec<[[f64; 2]; 2]>)>(word)
    else {
        return false;
    };
    let Some(view) = haecceity::hlr::View::new(frame.direction, frame.up) else {
        return false;
    };
    if lines.len() < 3
        || !frame.scale.is_finite()
        || frame.scale <= 0.
        || dot(c.normal, view.toward).abs() > 1e-6
    {
        return false;
    }
    let u = view.map(c.normal);
    let project = |p: [f64; 2]| p[0] * u[0] + p[1] * u[1];
    let centre = view.map(std::array::from_fn(|k| {
        (a.envelope[0][k] + a.envelope[1][k]) / 2.
    }));
    let station = |p| {
        let p = view.map(p);
        project([
            frame.at[0] + frame.scale * (p[0] - centre[0]),
            frame.at[1] + frame.scale * (p[1] - centre[1]),
        ])
    };
    let ink: Vec<_> = lines[..2].iter().map(|e| project(e[0])).collect();
    let want = c.witnesses.map(station);
    let near = |x: f64, y: f64| (x - y).abs() < 1e-5;
    let along = sub2(lines[2][1], lines[2][0]);
    (along[0] * u[1] - along[1] * u[0]).abs() < 1e-5
        && lines[..2]
            .iter()
            .all(|e| near(project(e[0]), project(e[1])))
        && ((near(ink[0], want[0]) && near(ink[1], want[1]))
            || (near(ink[0], want[1]) && near(ink[1], want[0])))
}
fn sub2(a: [f64; 2], b: [f64; 2]) -> [f64; 2] {
    [a[0] - b[0], a[1] - b[1]]
}

/// A current pair of measurable references, in existing views, for the author to copy.
pub(crate) fn fix(a: &Analysis, id: &str) -> Option<String> {
    let (_, d) = axis(a, id)?;
    let mut candidates = Vec::new();
    for along in ["y", "x", "z", "true"] {
        for key in a
            .dimension_references()
            .keys()
            .filter(|k| k.starts_with("face:") || k.starts_with("edge:"))
        {
            let refs = vec![format!("axis:{id}"), key.clone()];
            let Some(c) = constraint(a, id, &refs, along) else {
                continue;
            };
            let Some(view) = ["front", "side", "plan"]
                .into_iter()
                .find(|v| dot(c.normal, view_normal(v).unwrap()).abs() < 1e-6)
            else {
                continue;
            };
            // Prefer the side view for across-y, as on a flange seen edge-on in front.
            let view = if along == "y" { "side" } else { view };
            for (n, first) in &candidates {
                if dot(cross(*n, c.normal), d).abs() > 1e-6 {
                    return Some(format!(
                        "Locate the inclined hole with two authored dimensions: {first}; and {} from {} to {} with along: {along} in the {view} view. These constrain both directions across its axis.",
                        super::coverage::fmt(c.value),
                        refs[0],
                        refs[1]
                    ));
                }
            }
            candidates.push((
                c.normal,
                format!(
                    "{} from {} to {} with along: {along} in the {view} view",
                    super::coverage::fmt(c.value),
                    refs[0],
                    refs[1]
                ),
            ));
        }
    }
    Some("Locate the inclined hole with two independent authored distances from its axis to physical edges or datum faces, in principal views showing those distances true. Use along: true for a distance across an inclined axis; a coordinate along that axis has no unique station.".into())
}

/// Replace an impossible world-coordinate request with the axis's actual need.
pub(super) fn explain(a: &Analysis, issues: &mut [super::LintIssue]) {
    for issue in issues
        .iter_mut()
        .filter(|i| i.code == "feature_not_located")
    {
        let inclined: Vec<_> = issue
            .subjects
            .iter()
            .filter(|id| axis(a, id).is_some())
            .collect();
        if inclined.is_empty() {
            continue;
        }
        let mut fixes: Vec<_> = inclined.iter().filter_map(|id| fix(a, id)).collect();
        if issue.subjects.len() > inclined.len() {
            fixes.push(
                "Locate the other holes from datums along each named principal direction.".into(),
            );
        }
        issue.fix = Some(fixes.join(" "));
        issue
            .requirements
            .retain(|r| !inclined.contains(&&r.feature));
        issue
            .requirements
            .extend(inclined.into_iter().map(|id| super::Requirement {
                feature: id.clone(),
                parameter: "location.piercing_point".into(),
            }));
    }
}
