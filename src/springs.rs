//! Recognised coil springs: simplified outline/end coils and measured data.
//! The original B-rep and recognition remain available in the Analysis.
use std::collections::BTreeSet;

use haecceity::{geom, hlr::View};
use serde_json::json;

use crate::bridge::{Feature, holes};
use crate::model::{Analysis, PlannedView};
use crate::output::{Curve, Item};
use crate::tables::TableSpec;

#[derive(Clone, Copy, Debug, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Depiction {
    Simplified,
    Full,
}
fn simplified(spec: &crate::model::Spec, feature: &Feature) -> bool {
    spec.features.get(&feature.id).and_then(|f| f.spring) != Some(Depiction::Full)
}

pub(crate) struct Spring<'a> {
    pub feature: &'a Feature,
    pub on: [f64; 3],
    pub axis: [f64; 3],
    pub start: f64,
    pub end: f64,
    pub outer: f64,
    pub wire: f64,
}
impl Spring<'_> {
    pub fn at(&self, t: f64) -> [f64; 3] {
        geom::add(self.on, geom::scale(self.axis, t))
    }
}
pub(crate) fn read(a: &Analysis) -> impl Iterator<Item = Spring<'_>> {
    a.features
        .iter()
        .filter(|f| f.kind == "helical_spring")
        .filter_map(|f| {
            let p = &f.parameters;
            Some(Spring {
                feature: f,
                on: holes::point(p.get("axis")?.get("point"))?,
                axis: geom::unit(holes::point(p.get("axis")?.get("direction"))?)?,
                start: p.get("start")?.as_f64()?,
                end: p.get("end")?.as_f64()?,
                outer: p.get("outer_diameter")?.as_f64()?,
                wire: p.get("wire_diameter")?.as_f64()?,
            })
        })
}
pub(crate) fn faces(a: &Analysis, spec: &crate::model::Spec) -> BTreeSet<usize> {
    read(a)
        .filter(|s| simplified(spec, s.feature))
        .flat_map(|s| s.feature.faces.iter().copied())
        .collect()
}
pub(crate) fn cache_key(a: &Analysis, spec: &crate::model::Spec) -> Vec<u64> {
    let mut key = Vec::new();
    for s in read(a).filter(|s| simplified(spec, s.feature)) {
        key.push(0x2162);
        key.extend(
            s.on.into_iter()
                .chain(s.axis)
                .chain([
                    s.start,
                    s.end,
                    s.outer,
                    s.wire,
                    s.feature.parameters["pitch"].as_f64().unwrap(),
                ])
                .map(f64::to_bits),
        );
        key.push(u64::from(s.feature.parameters["hand"] == "left"));
        key.push(s.feature.faces.len() as u64);
        key.extend(s.feature.faces.iter().map(|i| *i as u64));
    }
    key
}
pub(crate) fn whole(a: &Analysis) -> bool {
    let claimed: BTreeSet<_> = read(a)
        .flat_map(|s| s.feature.faces.iter().copied())
        .collect();
    !claimed.is_empty() && claimed.len() == a.binding.face_count
}
fn wrap(text: &str) -> Vec<String> {
    let mut lines = vec![String::new()];
    for word in text.split_whitespace() {
        if lines.last().unwrap().len() + word.len() + 1 > 30 {
            lines.push(String::new());
        }
        let line = lines.last_mut().unwrap();
        if !line.is_empty() {
            line.push(' ');
        }
        line.push_str(word);
    }
    lines
}
#[cfg(test)]
pub(crate) fn tables(a: &Analysis) -> Vec<TableSpec> {
    tables_in(a, crate::units::Units::Mm)
}
pub(crate) fn tables_in(a: &Analysis, units: crate::units::Units) -> Vec<TableSpec> {
    read(a)
        .enumerate()
        .map(|(i, s)| {
            let p = &s.feature.parameters;
            let mut rows = vec![vec![
                format!("SPRING DATA S{}", i + 1),
                "VALUE".into(),
                "BASIS".into(),
            ]];
            for (label, key, basis) in [
                ("Wire ⌀ (mm)", "wire_diameter", "measured"),
                ("Mean ⌀ (mm)", "mean_diameter", "measured"),
                ("Free length (mm)", "free_length", "measured"),
                (
                    "Active coils",
                    "active_coils",
                    p["active_coils_basis"].as_str().unwrap_or("unknown"),
                ),
                (
                    "Total coils",
                    "total_coils",
                    p["total_coils_basis"].as_str().unwrap_or("measured"),
                ),
                ("Hand", "hand", "measured"),
                ("Ends", "end_type", "recognised"),
            ] {
                let value = p.get(key).map_or("—".into(), |v| match v {
                    serde_json::Value::String(s) => s.clone(),
                    _ => v.as_f64().map_or("—".into(), |v| {
                        if label.contains("(mm)") {
                            units.format(v)
                        } else {
                            crate::dims::fmt(v)
                        }
                    }),
                });
                for (j, line) in wrap(basis).into_iter().enumerate() {
                    rows.push(vec![
                        if j == 0 {
                            label.replace("(mm)", &format!("({})", units.symbol()))
                        } else {
                            String::new()
                        },
                        if j == 0 { value.clone() } else { String::new() },
                        line,
                    ]);
                }
            }
            // The recogniser does not infer rate. Only an explicitly stated value may appear.
            if let Some(rate) = p.get("rate").and_then(|v| v.as_f64()) {
                rows.push(vec![
                    format!("Rate (N/{})", units.symbol()),
                    crate::dims::fmt(rate * units.millimetres()),
                    "stated".into(),
                ]);
            }
            TableSpec {
                id: format!("spring_data_{}", s.feature.id),
                rows: rows.into_iter().map(|r| json!(r)).collect(),
                prefer: Some("tr".into()),
                sheet: None,
            }
        })
        .collect()
}

/// A cylinder's projected envelope includes the end silhouettes even where
/// the original open wire did not reach them. All drawing furniture uses it.
pub(crate) fn iso_bounds(a: &Analysis, view: &View) -> [f64; 4] {
    let mut bounds = a.iso_bounds;
    let centre = crate::model::extent(a, view).1;
    for s in read(a) {
        let axis = view.map(s.axis);
        let start = view.map(s.at(s.start));
        let end = view.map(s.at(s.end));
        for k in 0..2 {
            let radial = s.outer / 2. * (1. - axis[k].powi(2)).max(0.).sqrt();
            bounds[k] = bounds[k].min(start[k].min(end[k]) - radial - centre[k]);
            bounds[k + 2] = bounds[k + 2].max(start[k].max(end[k]) + radial - centre[k]);
        }
    }
    bounds
}

/// Drawing-only ink: a closed envelope and one turn at each end. The
/// middle turns are represented by their outline, never by every wire segment.
pub(crate) fn ink(a: &Analysis, v: &PlannedView, spec: &crate::model::Spec) -> Vec<Item> {
    let Some(view) = View::new(v.direction, v.up) else {
        return vec![];
    };
    let centre = crate::detail::crop_extent(a, v).1;
    let page = |p| {
        let q = view.map(p);
        [0, 1].map(|k| v.at[k] + v.scale * (q[k] - centre[k]))
    };
    let mut out = vec![];
    for s in read(a).filter(|s| simplified(spec, s.feature)) {
        let seed = if s.axis[0].abs() < 0.9 {
            [1., 0., 0.]
        } else {
            [0., 1., 0.]
        };
        let x = geom::unit(geom::cross(s.axis, seed)).unwrap();
        let y = geom::cross(s.axis, x);
        let radial = |t: f64| geom::add(geom::scale(x, t.cos()), geom::scale(y, t.sin()));
        if geom::dot(s.axis, view.toward).abs() > 1. - 1e-6 {
            for radius in [s.outer / 2., s.outer / 2. - s.wire] {
                let points: Vec<_> = (0..=64)
                    .map(|i| {
                        page(geom::add(
                            s.at(s.start),
                            geom::scale(radial(std::f64::consts::TAU * i as f64 / 64.), radius),
                        ))
                    })
                    .collect();
                out.extend(
                    points
                        .windows(2)
                        .map(|p| Item::Edge(Curve::Line(p[0], p[1]))),
                );
            }
        } else {
            let side = geom::unit(geom::cross(s.axis, view.toward)).unwrap();
            for sign in [-1., 1.] {
                let off = geom::scale(side, sign * s.outer / 2.);
                out.push(Item::Edge(Curve::Line(
                    page(geom::add(s.at(s.start), off)),
                    page(geom::add(s.at(s.end), off)),
                )));
            }
            // Close the same envelope in every oblique view. Without the end
            // silhouettes an isometric shows two disconnected coil arcs rather
            // than the outline used by the principal simplified representation.
            let front = geom::cross(side, s.axis);
            let facing = if geom::dot(s.axis, view.toward) >= 0. {
                1.
            } else {
                -1.
            };
            for (z, sign) in [(s.start, facing), (s.end, -facing)] {
                let points: Vec<_> = (0..=16)
                    .map(|i| {
                        let t = std::f64::consts::PI * i as f64 / 16.;
                        page(geom::add(
                            s.at(z),
                            geom::scale(
                                geom::add(
                                    geom::scale(side, t.cos()),
                                    geom::scale(front, sign * t.sin()),
                                ),
                                s.outer / 2.,
                            ),
                        ))
                    })
                    .collect();
                out.extend(
                    points
                        .windows(2)
                        .map(|p| Item::Edge(Curve::Line(p[0], p[1]))),
                );
            }
            let pitch = s.feature.parameters["pitch"].as_f64().unwrap();
            let span = pitch.min((s.end - s.start - s.wire) / 2.);
            let hand = if s.feature.parameters["hand"] == "left" {
                -1.
            } else {
                1.
            };
            for z in [s.start + s.wire / 2., s.end - s.wire / 2. - span] {
                let points: Vec<_> = (0..=64)
                    .map(|i| {
                        let t = i as f64 / 64.;
                        page(geom::add(
                            s.at(z + t * span),
                            geom::scale(
                                radial(hand * std::f64::consts::TAU * t),
                                (s.outer - s.wire) / 2.,
                            ),
                        ))
                    })
                    .collect();
                out.extend(
                    points
                        .windows(2)
                        .map(|p| Item::Edge(Curve::Line(p[0], p[1]))),
                );
            }
        }
    }
    if v.detail.is_some() {
        let (size, _) = crate::detail::crop_extent(a, v);
        let rect = [
            v.at[0] - size[0] * v.scale / 2.,
            v.at[1] - size[1] * v.scale / 2.,
            v.at[0] + size[0] * v.scale / 2.,
            v.at[1] + size[1] * v.scale / 2.,
        ];
        out = out
            .into_iter()
            .flat_map(|item| match item {
                Item::Edge(curve) => crate::detail::clip(curve, rect)
                    .into_iter()
                    .map(Item::Edge)
                    .collect(),
                _ => vec![item],
            })
            .collect();
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{self, Spec};
    use std::path::Path;
    fn fixture(name: &str) -> std::path::PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures")
            .join(name)
    }
    #[test]
    fn spring_data_and_two_dimensions_reach_the_drawing_without_segment_callouts() {
        for name in ["swept_left", "segmented", "torus_chain"] {
            let (part, a) =
                model::analyse_native(&fixture(&format!("springs/{name}.step.gz"))).unwrap();
            let springs: Vec<_> = read(&a).collect();
            assert_eq!(springs.len(), 1, "{name}");
            let s = &springs[0];
            assert!(s.outer > s.wire && s.end > s.start);
            assert!(
                !a.features
                    .iter()
                    .filter(|f| matches!(f.kind.as_str(), "hole" | "boss" | "fillet"))
                    .any(|f| f.faces.iter().any(|i| s.feature.faces.contains(i))),
                "{name}"
            );
            let p = model::plan_recovered(&a, &Spec::default()).unwrap();
            let dims: Vec<_> = p
                .dimensions
                .iter()
                .filter(|d| d.measurements.iter().any(|m| m.feature == s.feature.id))
                .collect();
            assert_eq!(dims.len(), 2, "{name}");
            assert!(dims.iter().all(|d| d.placed.is_some()), "{name}");
            for parameter in ["free_length", "outer_diameter"] {
                assert!(
                    dims.iter()
                        .any(|d| d.measurements.iter().any(|m| m.parameter == parameter)),
                    "{name}"
                );
            }
            let table = p
                .tables
                .iter()
                .find(|t| t.id.starts_with("spring_data_"))
                .unwrap();
            assert!(table.placed.is_some(), "{name}: {:?}", p.problems);
            let text = table
                .rows
                .iter()
                .flatten()
                .cloned()
                .collect::<Vec<_>>()
                .join(" ");
            assert!(
                text.contains("Active coils — unknown:") && text.contains("not established"),
                "{text}"
            );
            assert!(text.contains("Total coils") && text.contains("terminal extensions"));
            assert!(!text.contains("Rate"));
            assert!(!text.contains("Outer"));
            let font = crate::dims::font();
            let drawing = model::render(&part, &a, &p, font).unwrap();
            let printed = drawing
                .text
                .iter()
                .map(|r| r.text.as_str())
                .collect::<Vec<_>>()
                .join(" ");
            assert!(
                printed.contains("SPRING DATA")
                    && printed.contains("Wire")
                    && printed.contains("Total coils"),
                "{name}"
            );
            assert!(drawing.annotations.iter().any(|m| {
                m.name == table.id
                    && m.claims
                        .iter()
                        .any(|c| c.feature == s.feature.id && c.parameter == "spring.data")
            }));
            for v in &p.views {
                let edges = ink(&a, v, &p.spec);
                if v.id == "v:iso" {
                    let bounds = a.drawing_iso_bounds();
                    for item in &edges {
                        let Item::Edge(c) = item else {
                            panic!("spring edge")
                        };
                        let b = c.bbox();
                        for k in 0..2 {
                            assert!(b[k] >= v.at[k] + v.scale * bounds[k] - 1e-6);
                            assert!(b[k + 2] <= v.at[k] + v.scale * bounds[k + 2] + 1e-6);
                        }
                        for caption in p.captions.iter().filter(|c| c.view == v.id) {
                            assert!(
                                !(b[0] < caption.ink[2]
                                    && caption.ink[0] < b[2]
                                    && b[1] < caption.ink[3]
                                    && caption.ink[1] < b[3]),
                                "outline clears its caption"
                            );
                        }
                    }
                }
                if edges.len() == 162 {
                    let endpoints = |i: usize| match &edges[i] {
                        Item::Edge(Curve::Line(a, b)) => [*a, *b],
                        _ => panic!("outline edge"),
                    };
                    let near = |a: [f64; 2], b: [f64; 2]| {
                        assert!((a[0] - b[0]).hypot(a[1] - b[1]) < 1e-7);
                    };
                    // Each cap joins the two generators, including in the iso.
                    near(endpoints(2)[0], endpoints(1)[0]);
                    near(endpoints(17)[1], endpoints(0)[0]);
                    near(endpoints(18)[0], endpoints(1)[1]);
                    near(endpoints(33)[1], endpoints(0)[1]);
                }
                assert!(
                    edges.len() <= 162,
                    "simplified ink is independent of source segment count"
                );
            }
            // Synthetic spring ink obeys the same enlarged-detail crop as
            // B-rep ink; it must not leak whole coils into adjacent furniture.
            let mut crop = p.views.iter().find(|v| v.id == "v:front").unwrap().clone();
            let mut bounds = a.envelope;
            bounds[1][2] = (bounds[0][2] + bounds[1][2]) / 2.;
            crop.detail = Some(crate::detail::Detail {
                label: "A".into(),
                axis: 2,
                source_view: "v:front".into(),
                source_cut: None,
                kind: "authored-feature".into(),
                bounds,
                marked_bounds: bounds,
                dimensions: vec![],
                scale: crop.scale,
                authored: true,
                partial_profile: true,
            });
            let (size, _) = crate::detail::crop_extent(&a, &crop);
            let lo = [0, 1].map(|k| crop.at[k] - size[k] * crop.scale / 2.);
            let hi = [0, 1].map(|k| crop.at[k] + size[k] * crop.scale / 2.);
            let cropped = ink(&a, &crop, &p.spec);
            assert!(!cropped.is_empty());
            for item in cropped {
                let Item::Edge(c) = item else {
                    panic!("spring edge")
                };
                let b = c.bbox();
                assert!(
                    b[0] >= lo[0] - 1e-6
                        && b[1] >= lo[1] - 1e-6
                        && b[2] <= hi[0] + 1e-6
                        && b[3] <= hi[1] + 1e-6
                );
            }
        }
    }
    #[test]
    fn full_override_restores_wire_and_survives_spec_edits_without_losing_data() {
        let (part, a) = model::analyse_native(&fixture("springs/segmented.step.gz")).unwrap();
        let feature = read(&a).next().unwrap().feature.id.clone();
        let default = Spec::default();
        let full = model::edit(
            &default,
            &json!([{"op":"set","path":format!("features/{feature}/spring"),"value":"full"}]),
        )
        .unwrap();
        assert_eq!(full.features[&feature].spring, Some(Depiction::Full));
        let roundtrip: Spec = serde_json::from_slice(&serde_json::to_vec(&full).unwrap()).unwrap();
        assert_eq!(full, roundtrip);
        assert!(!faces(&a, &default).is_empty());
        assert!(faces(&a, &full).is_empty());
        assert!(crate::modelled_threads::carrier(&part, &a, &full, true).is_none());
        let p = model::plan_recovered(&a, &full).unwrap();
        assert!(
            p.tables
                .iter()
                .any(|t| t.id.starts_with("spring_data_") && t.placed.is_some())
        );
        assert_eq!(
            p.dimensions
                .iter()
                .filter(|d| d.measurements.iter().any(|m| m.feature == feature))
                .count(),
            2
        );
        assert!(p.views.iter().all(|v| ink(&a, v, &full).is_empty()));
        assert_ne!(cache_key(&a, &default), cache_key(&a, &full));
        let reset = model::edit(
            &full,
            &json!([{"op":"remove","path":format!("features/{feature}/spring")}]),
        )
        .unwrap();
        assert_eq!(cache_key(&a, &default), cache_key(&a, &reset));
        let font = crate::dims::font();
        let wire = model::render(&part, &a, &p, font).unwrap();
        assert!(
            wire.layers
                .iter()
                .any(|l| l.name == "part" && !l.items.is_empty())
        );
    }

    #[test]
    fn rate_is_only_printed_when_stated_and_active_coils_are_not_inferred() {
        let (_, mut a) = model::analyse_native(&fixture("springs/swept_left.step.gz")).unwrap();
        let f = a
            .features
            .iter_mut()
            .find(|f| f.kind == "helical_spring")
            .unwrap();
        assert!(f.parameters["active_coils"].is_null());
        f.parameters.insert("rate".into(), json!(12.5));
        let tables = tables(&a);
        assert!(
            tables[0]
                .rows
                .iter()
                .any(|r| r == &json!(["Rate (N/mm)", "12.5", "stated"]))
        );
        assert!(
            tables[0]
                .rows
                .iter()
                .any(|r| r[0] == "Active coils" && r[1] == "—")
        );
    }
}
