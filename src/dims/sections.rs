//! Measurements carried by an explicitly requested longitudinal pocket section.
use super::{Measurement, Shape, Want, fmt, want_id};
use crate::model::{Analysis, Spec};

pub(super) fn apply(a: &Analysis, s: &Spec, wants: &mut Vec<Want>) {
    let Some(p) = crate::sections::pocket(a, s) else {
        return;
    };
    let r = &s.views.sections[0];
    let Some(name) = crate::sections::NAMES
        .iter()
        .copied()
        .find(|n| n.ends_with(&r.id.to_lowercase().repeat(2)))
    else {
        return;
    };
    let id = want_id(&r.through, "pocket_width.length");
    let Some(w) = wants.iter_mut().find(|w| w.id == id) else {
        return;
    };
    w.views = vec![name];
    if s.dimensions
        .get(&id)
        .is_none_or(|d| d.presentation.is_none())
    {
        w.terms = vec![fmt(p.width), "WIDE ×".into(), fmt(p.length), "LONG".into()];
        w.measurements.retain(|m| !m.parameter.contains("depth"));
    }
    // The floor crosses the section plane and supplies a real leader attachment.
    let frame = crate::bridge::holes::frame(a);
    let f = a.features.iter().find(|f| f.id == r.through).unwrap();
    let Ok(crate::lint::section_recesses::Grammar::Pocket(local)) =
        crate::lint::section_recesses::grammar(f)
    else {
        return;
    };
    let mut floor = local.origin;
    floor[local.axis] -= local.open_sign * local.depth / 2.;
    let floor = crate::bridge::holes::to_part(&frame, floor);
    let mut mouth = floor;
    mouth[2] = 2. * p.centre[2] - floor[2];
    w.shape = Shape::Callout {
        sites: vec![(floor, 0.)],
        arcs: Vec::new(),
    };
    w.basis += "; applied views/sections/0: measured width and length callout on the longitudinal section; width is perpendicular to the cutting plane";
    let mut measurements = vec![Measurement {
        feature: r.through.clone(),
        parameter: "pocket_depth.length".into(),
        value: p.depth,
    }];
    if p.mouth {
        measurements.push(Measurement {
            feature: r.through.clone(),
            parameter: "pocket_max_depth.length".into(),
            value: p.depth,
        });
    }
    wants.push(Want {
        id: want_id(&r.through, "pocket_depth.length"), views: vec![name], value: p.depth,
        terms: vec![fmt(p.depth)], measurements,
        basis: "applied views/sections/0: floor to mouth in the centre section, maximum depth from the cylinder tangent when cylindrical".into(),
        shape: Shape::Linear { a: floor, b: mouth, axis: 2, a_edge: false, b_edge: false, order: 0 }, turning: None,
    });
}
