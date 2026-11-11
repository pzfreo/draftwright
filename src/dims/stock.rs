//! Whole-part polygonal stock (quiddity's `PolygonalStock`, draftwright's `PolygonalStockFeature`,
//! ARCH0015-4, #1082): a part that is one regular hexagonal prism and nothing else is bar stock,
//! dimensioned by its size across flats and its axial length, which replace the overall extents
//! ([`super::envelope`]): across the axis those depend on how the hexagon is turned, and along it
//! the length is the same span.
//!
//! - The **size across flats** is called out in the view that looks down the stock's axis, with a
//!   leader to a flat: `HEX 34.6 A/F` (draftwright's `polygonal_prism_jobs`).
//! - The **axial length**, from one end to the other, is a linear dimension in the view that shows
//!   the profile (draftwright's `render_boss_heights`, role `stock_length`).
//!
//! quiddity decides what is stock: one solid that is exactly a regular prism, no other faces (an
//! attached boss, a recess or a second body is not stock). Its record is read as a polygonal
//! boss's is ([`super::bosses::read_polygon`]), after the contract lint checks it against
//! (`crate::lint::polygonal::stock_wanted`: six sides, one regular ring of flats, and a single
//! stock). A record that breaks it, or that the planner cannot place (an axis along no world axis),
//! is reported (`feature_not_dimensioned`), and the overall extents are then kept, so the part's
//! size is never left unstated without trace.

use super::bosses::{Boss, Section, not_dimensioned, read_polygon};
use super::envelope::profile_views;
use super::{AXES, Context, Measurement, Planner, Problem, Shape, Want, end_on, want_id};
use crate::bridge::Feature;
use crate::lint::polygonal::stock_wanted;

pub(super) const PLANNER: Planner = Planner {
    kinds: &["polygonal_stock"],
    wants,
};

/// Each polygonal stock record, read, or why it cannot be dimensioned.
fn read<'a>(cx: &Context<'a>) -> Vec<(&'a Feature, Result<Boss<'a>, String>)> {
    let wanted = stock_wanted(cx.analysis);
    cx.analysis
        .features
        .iter()
        .filter(|f| f.kind == "polygonal_stock")
        .map(|f| {
            let broken = wanted
                .iter()
                .find_map(|w| (w.feature.id == f.id).then(|| w.values.clone().err())?);
            let read = match broken {
                Some(why) => Err(why),
                None => read_polygon(f, &cx.frame),
            };
            (f, read)
        })
        .collect()
}

/// Whether the part is stock this planner dimensions, so its overall extents are replaced.
pub(super) fn whole(cx: &Context) -> bool {
    read(cx).iter().any(|(_, r)| r.is_ok())
}

fn wants(cx: &Context) -> (Vec<Want>, Vec<Problem>) {
    let fmt = |v| crate::dims::fmt_in(v, cx.spec.units);
    let mut out = Vec::new();
    let mut problems = Vec::new();
    for (f, read) in read(cx) {
        let b = match read {
            Ok(b) => b,
            Err(why) => {
                problems.push(not_dimensioned(f, &why));
                continue;
            }
        };
        let Section::Polygon {
            sides,
            across_flats,
            flats,
            ..
        } = &b.section
        else {
            // Invariant: The stock reader admits only Prism sections.
            unreachable!("a stock record is read as a prism")
        };
        let (Some(length), Some(base)) = (b.height, b.base) else {
            // Invariant: The stock reader checks positive length before emitting stock.
            unreachable!("the contract gives a stock a positive length")
        };
        let k = b.axis;
        let across: Vec<&str> = (0..3).filter(|&j| j != k).map(|j| AXES[j]).collect();
        let view = end_on(k);
        let parameter = "polygon_across_flats.length";
        out.push(Want { units: cx.spec.units,
            id: want_id(&f.id, parameter),
            views: vec![view],
            value: *across_flats,
            terms: vec![
                if *sides == 6 {
                    "HEX".into()
                } else {
                    format!("{sides}-SIDED")
                },
                fmt(*across_flats),
                "A/F".into(),
            ],
            measurements: vec![Measurement {
                feature: f.id.clone(),
                parameter: parameter.into(),
                value: *across_flats,
            }],
            turning: None,
            basis: format!(
                "the part is {sides}-sided stock along {}: its size across flats is called out where its outline is seen, in place of the overall {} and {} extents, which depend on how the stock is turned",
                AXES[k], across[0], across[1]
            ),
            shape: Shape::Callout { arcs: Vec::new(),
                sites: flats.iter().map(|c| (*c, 0.0)).collect(),
            },
        });
        let views = profile_views(k);
        let j = (0..3)
            .find(|&j| j != k && profile_views(j).contains(&views[0]))
            // Invariant: The fixed principal/profile view tables include both radial axes.
            .expect("a profile view shows a radial axis");
        let rim = |p: [f64; 3]| {
            let mut p = p;
            p[j] += b.half(j);
            p
        };
        let parameter = "stock_length.length";
        out.push(Want { units: cx.spec.units,
            id: want_id(&f.id, parameter),
            views,
            value: length,
            terms: vec![fmt(length)],
            measurements: vec![Measurement {
                feature: f.id.clone(),
                parameter: parameter.into(),
                value: length,
            }],
            turning: None,
            basis: format!(
                "the stock's length, end to end along its axis, in a view that shows its profile, in place of the overall {} extent (the same span)",
                AXES[k]
            ),
            shape: Shape::Linear {
                a: rim(base),
                b: rim(b.top),
                axis: k,
                a_edge: true,
                b_edge: true,
                order: 2,
            },
        });
    }
    (out, problems)
}
