//! Only free length and outer diameter are dimensioned; remaining data is tabulated.
use super::{Context, Measurement, Planner, Problem, Shape, Want, want_id};
use crate::bridge::holes;

pub(super) const PLANNER: Planner = Planner {
    kinds: &["helical_spring"],
    wants,
};
fn wants(cx: &Context) -> (Vec<Want>, Vec<Problem>) {
    let fmt = |v| crate::dims::fmt_in(v, cx.spec.units);
    let mut out = vec![];
    let mut problems = vec![];
    for s in crate::springs::read(cx.analysis) {
        let Some(k) = holes::principal(s.axis) else {
            problems.push(Problem { id:format!("p:feature_not_dimensioned:{}",s.feature.id),code:"feature_not_dimensioned".into(),severity:"warning".into(),message:"The spring axis is oblique; its free length and outer diameter remain in the spring data table.".into(),subjects:vec![s.feature.id.clone()],..Default::default() });
            continue;
        };
        let views = super::envelope::profile_views(k);
        let j = (0..3)
            .find(|&j| j != k && super::envelope::profile_views(j).contains(&views[0]))
            .unwrap();
        for (parameter, value, a, b, axis, terms) in [
            (
                "free_length",
                s.end - s.start,
                s.at(s.start),
                s.at(s.end),
                k,
                vec![fmt(s.end - s.start)],
            ),
            (
                "outer_diameter",
                s.outer,
                {
                    let mut p = s.at((s.start + s.end) / 2.);
                    p[j] -= s.outer / 2.;
                    p
                },
                {
                    let mut p = s.at((s.start + s.end) / 2.);
                    p[j] += s.outer / 2.;
                    p
                },
                j,
                vec![format!("⌀{}", fmt(s.outer))],
            ),
        ] {
            let full = cx.spec.features.get(&s.feature.id).and_then(|f| f.spring)
                == Some(crate::model::SpringDepiction::Full);
            out.push(Want { units: cx.spec.units,
                id: want_id(&s.feature.id, parameter),
                views: views.clone(),
                value,
                terms,
                measurements: vec![Measurement {
                    feature: s.feature.id.clone(),
                    parameter: parameter.into(),
                    value,
                }],
                turning: None,
                basis: if full {
                    "recognised spring: full wire depiction requested; free length and outer diameter only, remaining measured data in spring table"
                } else {
                    "recognised spring: simplified outline and end coils; free length and outer diameter only, remaining measured data in spring table"
                }.into(),
                shape: if parameter == "outer_diameter" {
                    Shape::Callout { sites: vec![(a, 0.), (b, 0.)], arcs: vec![] }
                } else {
                    Shape::Linear { a, b, axis, a_edge: true, b_edge: true, order: 1 }
                },
            });
        }
    }
    (out, problems)
}
