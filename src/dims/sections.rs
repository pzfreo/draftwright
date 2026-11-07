//! Measurements carried by an explicitly requested longitudinal pocket section.
use super::{Measurement, Shape, Want, fmt, want_id};
use crate::model::{Analysis, Spec};

pub(super) fn apply(a: &Analysis, s: &Spec, wants: &mut Vec<Want>) {
    let Some(p) = crate::sections::pocket(a, s) else {
        return;
    };
    let r = &s.views.sections[0];
    let Some(name) = r.view_name() else {
        return;
    };
    let id = want_id(&r.through, "pocket_width.length");
    let Some(w) = wants.iter_mut().find(|w| w.id == id) else {
        return;
    };
    w.views = vec![name];
    // The floor crosses the section plane and supplies a real leader attachment.
    let Some(floor) = crate::sections::floor(a, s) else {
        return;
    };
    w.shape = Shape::Callout {
        sites: vec![(floor, 0.)],
        arcs: Vec::new(),
    };
    w.basis += "; applied views/sections/0: measured pocket callout attached to the floor in the longitudinal section";
    // The usual depth form keeps its leader on the floor. A floating linear
    // dimension can otherwise have its witnesses shortened to unrelated stock.
    if !w
        .measurements
        .iter()
        .any(|m| m.parameter == "pocket_floor_from_far_extreme.length")
    {
        if !w
            .measurements
            .iter()
            .any(|m| m.parameter == "pocket_depth.length")
        {
            w.measurements.push(Measurement {
                feature: r.through.clone(),
                parameter: "pocket_depth.length".into(),
                value: p.depth,
            });
        }
        return;
    }
    wants.push(Want {
        id: want_id(&r.through, "pocket_depth.length"),
        views: vec![name],
        value: p.depth,
        terms: vec![fmt(p.depth), "DEEP FROM TANGENT".into()],
        measurements: ["pocket_depth.length", "pocket_max_depth.length"]
            .map(|parameter| Measurement {
                feature: r.through.clone(),
                parameter: parameter.into(),
                value: p.depth,
            })
            .to_vec(),
        basis: "applied views/sections/0: depth callout led to the keyway floor".into(),
        shape: Shape::Callout {
            sites: vec![(floor, 0.)],
            arcs: Vec::new(),
        },
        turning: None,
    });
}
