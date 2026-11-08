//! Polygonal boss and polygonal stock coverage (Python's `linting/polygonal_boss_coverage.py` and
//! `linting/polygonal_stock_coverage.py`): a square or hexagonal boss states its size across flats
//! and its height; whole-part hexagonal bar stock its size across flats and its length
//! (LINT-polygonal_boss_coverage-af_height, LINT-polygonal_stock_coverage-af_length, and their
//! `_requirement_state` codes).
//!
//! Each of the Analysis's `polygonal_boss` features (quiddity's `PolygonalBoss`: `axis`, `center`,
//! `side_count`, `across_flats`, `base`, `top`, and its ring of `flat_directions` and
//! `flat_centres`) has two requirements, `polygon_across_flats.length` and `boss_height.length`
//! (`top - base`), and each `polygonal_stock` feature (`PolygonalStock`, the same fields, six sides)
//! `polygon_across_flats.length` and `stock_length.length`, judged by [`parameters::coverage`].
//!
//! As in Python, a record is checked against quiddity's published contract before it is read:
//! four or six sides (six for stock), finite bounds with the centre midway along the axis, and one
//! regular ring of flats, each at half the size across flats from the centre (`boss_contract`,
//! `stock_contract`). A record that breaks it has both requirements reported unverifiable, with
//! why. Python reports the whole family unverifiable when quiddity gives more than one stock (a
//! stock is the whole part); so does the port.
//!
//! A four-sided boss that is exactly a recognised raised pad (same axis, base, top, centre and
//! sizes, within 1e-6) is the pad's: its measurements are the pad's requirements, not the boss's
//! (draftwright's `square_polygonal_boss_pad_owner`). Python also asks that the boss's defining
//! faces be a proper subset of the pad's; the bridge keeps each feature's constituent faces, not
//! its defining ones, so the port asks that the boss's faces be a subset of the pad's.

use std::f64::consts::PI;

use serde_json::Value;

use super::parameters::{self, Wanted, fmt, fmt3};
use super::{LintIssue, Mark};
use crate::bridge::Feature;
use crate::bridge::holes::{frame, point, to_part};
use crate::model::Analysis;

const AXES: [&str; 3] = ["x", "y", "z"];

/// A polygonal prism record, read.
struct Prism {
    axis: usize,
    center: [f64; 3],
    sides: u64,
    across_flats: f64,
    base: f64,
    top: f64,
    directions: Vec<[f64; 3]>,
    centres: Vec<[f64; 3]>,
}

/// Python's `round(x, 3)`.
fn round3(x: f64) -> f64 {
    format!("{x:.3}").parse().unwrap_or(x)
}

fn read(f: &Feature) -> Result<Prism, String> {
    let p = &f.parameters;
    let points = |name: &str| -> Result<Vec<[f64; 3]>, String> {
        p.get(name)
            .and_then(Value::as_array)
            .ok_or(format!("its record gives no {name}"))?
            .iter()
            .map(|v| point(Some(v)).ok_or(format!("its {name} are not points")))
            .collect()
    };
    let finite = |name: &str| {
        parameters::size(f, name).and_then(|x| {
            x.is_finite()
                .then_some(x)
                .ok_or(format!("its {name} is not finite"))
        })
    };
    Ok(Prism {
        axis: p
            .get("axis")
            .and_then(Value::as_str)
            .and_then(|a| AXES.iter().position(|x| *x == a))
            .ok_or("its record gives no principal axis")?,
        center: point(p.get("center")).ok_or("its record gives no centre")?,
        sides: p
            .get("side_count")
            .and_then(Value::as_u64)
            .ok_or("its record gives no side count")?,
        across_flats: finite("across_flats")?,
        base: finite("base")?,
        top: finite("top")?,
        directions: points("flat_directions")?,
        centres: points("flat_centres")?,
    })
}

impl Prism {
    fn in_plane(&self) -> [usize; 2] {
        let o: Vec<usize> = (0..3).filter(|&i| i != self.axis).collect();
        [o[0], o[1]]
    }

    /// The ring's checks both families share: finite bounds, centre midway, paired distinct
    /// supports.
    fn bounds_and_ring(&self, family: &str) -> Result<(), String> {
        let c = self.center;
        if !c.iter().all(|x| x.is_finite())
            || self.across_flats <= 0.0
            || self.top <= self.base
            || (c[self.axis] - (self.base + self.top) / 2.0).abs() > 2e-3
        {
            return Err(format!(
                "the {family} provider contract requires finite physical bounds"
            ));
        }
        let n = self.sides as usize;
        if self.directions.len() != n || self.centres.len() != n {
            return Err(format!(
                "the {family} provider contract requires paired supports"
            ));
        }
        let key = |q: &[f64; 3]| q.map(round3).map(f64::to_bits);
        for list in [&self.directions, &self.centres] {
            let mut keys: Vec<_> = list.iter().map(key).collect();
            keys.sort_unstable();
            keys.dedup();
            if keys.len() != n {
                return Err(format!(
                    "the {family} provider contract requires distinct supports"
                ));
            }
        }
        Ok(())
    }

    /// One regular ring: flats evenly spaced either way round, and opposite flats opposed.
    fn regular(&self, family: &str) -> Result<(), String> {
        let n = self.sides as usize;
        let [a, b] = self.in_plane();
        let tol = PI / 90.0 + 2e-3;
        let angles: Vec<f64> = self
            .directions
            .iter()
            .map(|d| d[b].atan2(d[a]).rem_euclid(2.0 * PI))
            .collect();
        let step = 2.0 * PI / n as f64;
        let gaps: Vec<f64> = (0..n)
            .map(|i| (angles[(i + 1) % n] - angles[i]).rem_euclid(2.0 * PI))
            .collect();
        let anticlockwise = gaps.iter().all(|g| (g - step).abs() <= tol);
        let clockwise = gaps.iter().all(|g| (g - (2.0 * PI - step)).abs() <= tol);
        let norm = |d: &[f64; 3]| (d[0] * d[0] + d[1] * d[1] + d[2] * d[2]).sqrt();
        let opposed = (0..n / 2).all(|i| {
            let (d, e) = (&self.directions[i], &self.directions[i + n / 2]);
            (0..3).map(|k| d[k] * e[k]).sum::<f64>() <= -tol.cos() * norm(d) * norm(e)
        });
        if !((anticlockwise || clockwise) && opposed) {
            return Err(format!(
                "the {family} provider contract requires one regular support ring"
            ));
        }
        Ok(())
    }
}

/// quiddity's `PolygonalBoss` contract (draftwright's `_validate_polygonal_boss_source`).
fn boss_contract(f: &Feature) -> Result<Prism, String> {
    let b = read(f)?;
    let family = "polygonal-boss";
    if b.sides != 4 && b.sides != 6 {
        return Err(format!(
            "the {family} provider contract requires four or six sides"
        ));
    }
    b.bounds_and_ring(family)?;
    let [i, j] = b.in_plane();
    for (d, c) in b.directions.iter().zip(&b.centres) {
        let norm = (d[0] * d[0] + d[1] * d[1] + d[2] * d[2]).sqrt();
        let support: f64 = [i, j].iter().map(|&k| (c[k] - b.center[k]) * d[k]).sum();
        let half = b.across_flats / 2.0;
        // Python's isclose(support, half, rel_tol=1e-3, abs_tol=0.202).
        let close = (support - half).abs() <= (1e-3 * support.abs().max(half.abs())).max(0.202);
        if (norm - 1.0).abs() > 1e-3
            || d[b.axis].abs() > 1e-6
            || !close
            || !(b.base - 1e-6 <= c[b.axis] && c[b.axis] <= b.top + 1e-6)
        {
            return Err(format!(
                "the {family} provider contract requires physical supports"
            ));
        }
    }
    b.regular(family)?;
    // The flats' centres span a polygon, not a line.
    let n = b.centres.len();
    let area: Vec<f64> = (0..3)
        .map(|k| {
            (0..n)
                .map(|m| {
                    let (s, e) = (b.centres[m], b.centres[(m + 1) % n]);
                    s[(k + 1) % 3] * e[(k + 2) % 3] - s[(k + 2) % 3] * e[(k + 1) % 3]
                })
                .sum()
        })
        .collect();
    if area.iter().map(|a| a * a).sum::<f64>() <= 1e-12 {
        return Err(format!(
            "the {family} provider contract requires a nondegenerate support ring"
        ));
    }
    Ok(b)
}

/// quiddity's `PolygonalStock` contract (draftwright's `_validate_polygonal_stock_source`): each
/// flat's centre is the midpoint of its side, rebuilt from its neighbours' support lines, within
/// quiddity's rounding (flats to 0.001, centre and size to 0.0001) and 0.2 mm of model tolerance.
fn stock_contract(f: &Feature) -> Result<Prism, String> {
    let s = read(f)?;
    let family = "polygonal-stock";
    if s.sides != 6 {
        return Err(format!(
            "the {family} provider contract requires exactly six sides"
        ));
    }
    s.bounds_and_ring(family)?;
    let [i, j] = s.in_plane();
    let normals: Vec<[f64; 2]> = s
        .directions
        .iter()
        .map(|d| {
            let l = d[i].hypot(d[j]);
            if l == 0.0 {
                Err(format!("a {family} support direction cannot be zero"))
            } else {
                Ok([d[i] / l, d[j] / l])
            }
        })
        .collect::<Result<_, _>>()?;
    let distance = s.across_flats / 2.0;
    let n = normals.len();
    let mut expected = Vec::new();
    for (m, nm) in normals.iter().enumerate() {
        let mut meets = Vec::new();
        for k in [(m + n - 1) % n, (m + 1) % n] {
            let o = normals[k];
            let det = nm[0] * o[1] - nm[1] * o[0];
            if det.abs() <= 1e-9 {
                return Err(format!("adjacent {family} support lines must intersect"));
            }
            meets.push([
                distance * (o[1] - nm[1]) / det,
                distance * (nm[0] - o[0]) / det,
            ]);
        }
        let mut offset = [0.0; 3];
        offset[i] = (meets[0][0] + meets[1][0]) / 2.0;
        offset[j] = (meets[0][1] + meets[1][1]) / 2.0;
        expected.push(offset);
    }
    let (point_q, centre_q, dir_q, af_q, floor, model) = (5e-4, 5e-5, 5e-4, 5e-5, 2e-3, 0.2);
    let angular_q = f64::hypot(dir_q, dir_q);
    let min_gap_sine = (PI / 3.0 - PI / 90.0).sin();
    let radial_base = f64::max(
        floor,
        model + point_q + centre_q + af_q / 2.0 + s.across_flats * dir_q * dir_q,
    );
    let tangential_tol = f64::max(
        floor,
        2.0 * model + point_q + centre_q + s.across_flats / 2.0 * angular_q / min_gap_sine,
    );
    let axial_tol = f64::max(floor, point_q + centre_q);
    for ((d, c), e) in s.directions.iter().zip(&s.centres).zip(&expected) {
        let norm = (d[0] * d[0] + d[1] * d[1] + d[2] * d[2]).sqrt();
        let r: Vec<f64> = (0..3).map(|k| c[k] - s.center[k] - e[k]).collect();
        let radial = (r[i] * d[i] + r[j] * d[j]) / norm;
        let tangential = (-r[i] * d[j] + r[j] * d[i]) / norm;
        let expected_tangential = (-e[i] * d[j] + e[j] * d[i]) / norm;
        let radial_tol = radial_base + expected_tangential.abs() * angular_q;
        if (norm - 1.0).abs() > 1e-3
            || d[s.axis].abs() > 1e-6
            || radial.abs() > radial_tol
            || tangential.abs() > tangential_tol
            || r[s.axis].abs() > axial_tol
        {
            return Err(format!(
                "the {family} provider contract requires physical supports"
            ));
        }
    }
    s.regular(family)?;
    Ok(s)
}

/// Whether square boss *b* is exactly one of the Analysis's raised pads (its axis, base, top,
/// centre and both sizes within 1e-6, and its faces among the pad's).
fn pads_own(analysis: &Analysis, f: &Feature, b: &Prism) -> bool {
    if b.sides != 4 {
        return false;
    }
    let owners = analysis
        .features
        .iter()
        .filter(|p| p.kind == "pad")
        .filter(|p| f.faces.iter().all(|i| p.faces.binary_search(i).is_ok()))
        .filter(|p| {
            let get = |k: &str| parameters::size(p, k).ok();
            let axis = p
                .parameters
                .get("axis")
                .and_then(Value::as_str)
                .and_then(|a| AXES.iter().position(|x| *x == a));
            let bounds: Option<Vec<[f64; 2]>> = AXES
                .iter()
                .map(|a| Some([get(&format!("{a}0"))?, get(&format!("{a}1"))?]))
                .collect();
            let (Some(axis), Some(bounds)) = (axis, bounds) else {
                return false;
            };
            axis == b.axis
                && (b.base - bounds[axis][0]).abs() <= 1e-6
                && (b.top - bounds[axis][1]).abs() <= 1e-6
                && (0..3).all(|k| (b.center[k] - (bounds[k][0] + bounds[k][1]) / 2.0).abs() <= 1e-6)
                && (0..3)
                    .filter(|&k| k != axis)
                    .all(|k| (b.across_flats - (bounds[k][1] - bounds[k][0])).abs() <= 1e-6)
        })
        .count();
    owners == 1
}

fn shape(sides: u64) -> String {
    match sides {
        4 => "square".into(),
        6 => "hexagonal".into(),
        n => format!("{n}-sided"),
    }
}

/// The polygonal bosses' requirements: the size across flats and the height of each, both
/// unverifiable for a record that breaks quiddity's contract.
pub fn boss_wanted(analysis: &Analysis) -> Vec<Wanted<'_>> {
    let mut out = Vec::new();
    for f in analysis
        .features
        .iter()
        .filter(|f| f.kind == "polygonal_boss")
    {
        match boss_contract(f) {
            Ok(b) if pads_own(analysis, f, &b) => {}
            Ok(b) => {
                let h = b.top - b.base;
                out.push(Wanted {
                    feature: f,
                    parameter: "polygon_across_flats.length".into(),
                    values: Ok(vec![b.across_flats]),
                    callout: format!("{} A/F", fmt(b.across_flats)),
                });
                out.push(Wanted {
                    feature: f,
                    parameter: "boss_height.length".into(),
                    values: Ok(vec![h]),
                    callout: format!("its height {}", fmt(h)),
                });
            }
            Err(why) => {
                for (parameter, callout) in [
                    ("polygon_across_flats.length", "its size across flats"),
                    ("boss_height.length", "its height"),
                ] {
                    out.push(Wanted {
                        feature: f,
                        parameter: parameter.into(),
                        values: Err(why.clone()),
                        callout: callout.into(),
                    });
                }
            }
        }
    }
    out
}

/// The polygonal stock's requirements: its size across flats and its length; both unverifiable
/// for a record that breaks quiddity's contract, and for every stock when there is more than one.
pub fn stock_wanted(analysis: &Analysis) -> Vec<Wanted<'_>> {
    let stock: Vec<&Feature> = analysis
        .features
        .iter()
        .filter(|f| f.kind == "polygonal_stock")
        .collect();
    let mut out = Vec::new();
    for f in &stock {
        let read = if stock.len() == 1 {
            stock_contract(f)
        } else {
            Err(format!(
                "quiddity gives {} polygonal stocks, and a stock is the whole part",
                stock.len()
            ))
        };
        match read {
            Ok(s) => {
                let l = s.top - s.base;
                out.push(Wanted {
                    feature: f,
                    parameter: "polygon_across_flats.length".into(),
                    values: Ok(vec![s.across_flats]),
                    callout: format!("{} A/F", fmt(s.across_flats)),
                });
                out.push(Wanted {
                    feature: f,
                    parameter: "stock_length.length".into(),
                    values: Ok(vec![l]),
                    callout: format!("its length {}", fmt(l)),
                });
            }
            Err(why) => {
                for (parameter, callout) in [
                    ("polygon_across_flats.length", "its size across flats"),
                    ("stock_length.length", "its length"),
                ] {
                    out.push(Wanted {
                        feature: f,
                        parameter: parameter.into(),
                        values: Err(why.clone()),
                        callout: callout.into(),
                    });
                }
            }
        }
    }
    out
}

/// A polygonal feature in messages: its shape, size, height or length, axis and centre, in the
/// part's frame.
fn noun<'a>(analysis: &'a Analysis, what: &'a str) -> impl Fn(&Feature) -> String + 'a {
    let frame = frame(analysis);
    move |f: &Feature| {
        let p = &f.parameters;
        let mut s = format!("{what} {}", f.id);
        if let Ok(b) = read(f) {
            s += &format!(
                " {} {} A/F, {} {}",
                shape(b.sides),
                fmt(b.across_flats),
                if what == "polygonal boss" {
                    "height"
                } else {
                    "length"
                },
                fmt(b.top - b.base)
            );
        }
        if let Some(a) = parameters::direction(&frame, p.get("axis")) {
            s += &format!(", axis {}", fmt3(a));
        }
        if let Some(c) = point(p.get("center")) {
            s += &format!(", at {}", fmt3(to_part(&frame, c)));
        }
        s
    }
}

/// The polygonal boss findings.
pub fn boss_coverage(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    parameters::coverage(
        "polygonal_boss",
        &boss_wanted(analysis),
        marks,
        noun(analysis, "polygonal boss"),
        "an across-flats callout in the view along its axis, and a linear dimension of its height \
         in a view that sees it side-on",
    )
}

/// The polygonal stock findings.
pub fn stock_coverage(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    parameters::coverage(
        "polygonal_stock",
        &stock_wanted(analysis),
        marks,
        noun(analysis, "polygonal stock"),
        "an across-flats callout in the view along its axis, and a linear dimension of its length \
         in a view that sees it side-on",
    )
}

#[cfg(test)]
mod tests {
    use super::super::paired_ramp_steps::tests::part;
    use super::*;
    use crate::output::Claim;
    use serde_json::json;

    /// A regular *n*-sided prism along z about (cx, cy, mid-height), its first flat facing +x.
    fn prism(n: usize, af: f64, base: f64, top: f64) -> Value {
        let (directions, centres): (Vec<[f64; 3]>, Vec<[f64; 3]>) = (0..n)
            .map(|i| {
                let a = 2.0 * PI * i as f64 / n as f64;
                let d = [round3(a.cos()), round3(a.sin()), 0.0];
                let c = [
                    round3(10.0 + af / 2.0 * a.cos()),
                    round3(20.0 + af / 2.0 * a.sin()),
                    round3((base + top) / 2.0),
                ];
                (d, c)
            })
            .unzip();
        json!({ "axis": "z", "center": [10.0, 20.0, (base + top) / 2.0], "side_count": n,
                "across_flats": af, "base": base, "top": top,
                "flat_directions": directions, "flat_centres": centres })
    }

    fn mark(feature: &str, text: &str, parameter: &str) -> Mark {
        Mark {
            name: format!("m:{parameter}"),
            view: Some("v:plan".into()),
            text: text.into(),
            claims: vec![Claim {
                feature: feature.into(),
                parameter: parameter.into(),
            }],
        }
    }

    fn codes(found: Vec<LintIssue>) -> Vec<String> {
        found
            .into_iter()
            .map(|i| format!("{} {}", i.code, i.requirements[0].parameter))
            .collect()
    }

    /// LINT-polygonal_boss_coverage-af_height (test_polygonal_boss_completeness_evidence):
    /// ctc01's hex boss, `HEX 100 A/F` and its height `50`, states both; a height left off is
    /// missing; a record whose flats are not one regular ring cannot be checked.
    #[test]
    fn a_polygonal_boss_states_its_size_across_flats_and_height() {
        let id = "f:polygonal_boss:1";
        let a = part("polygonal_boss", prism(6, 100.0, 0.0, 50.0));
        let af = mark(id, "HEX 100 A/F", "polygon_across_flats.length");
        let h = mark(id, "50", "boss_height.length");
        assert!(boss_coverage(&a, &[af.clone(), h]).is_empty());
        assert_eq!(
            codes(boss_coverage(&a, std::slice::from_ref(&af))),
            ["polygonal_boss_requirement_missing boss_height.length"]
        );
        assert_eq!(
            boss_coverage(&a, &[af])[0].message,
            "polygonal boss f:polygonal_boss:1 hexagonal 100 A/F, height 50, axis (0, 0, 1), at \
             (10, 20, 25): requirement boss_height.length (its height 50) is not stated on the \
             sheet"
        );
        let mut skew = prism(6, 100.0, 0.0, 50.0);
        skew["flat_directions"][1] = json!([0.0, 1.0, 0.0]);
        let found = boss_coverage(&part("polygonal_boss", skew), &[]);
        assert_eq!(
            codes(found.clone()),
            [
                "polygonal_boss_requirement_unverifiable polygon_across_flats.length",
                "polygonal_boss_requirement_unverifiable boss_height.length"
            ]
        );
        assert_eq!(
            found[0].evidence.as_deref(),
            Some("the polygonal-boss provider contract requires physical supports")
        );
        // A square boss that is exactly a recognised pad is the pad's.
        let mut a = part("polygonal_boss", prism(4, 20.0, 0.0, 5.0));
        let mut pad = a.features[0].clone();
        pad.id = "f:pad:1".into();
        pad.kind = "pad".into();
        pad.parameters = json!({ "axis": "z", "x0": 0.0, "x1": 20.0, "y0": 10.0, "y1": 31.0,
                                 "z0": 0.0, "z1": 5.0 })
        .as_object()
        .unwrap()
        .clone();
        a.features.push(pad);
        assert_eq!(boss_wanted(&a).len(), 2, "a pad 21 wide is not the boss");
        a.features[1].parameters["y1"] = json!(30.0);
        assert!(boss_wanted(&a).is_empty());
    }

    /// LINT-polygonal_stock_coverage-af_length (test_issue_1371): hexagonal bar stock states its
    /// size across flats and its length; a second stock makes both unverifiable.
    #[test]
    fn polygonal_stock_states_its_size_across_flats_and_length() {
        let id = "f:polygonal_stock:1";
        let a = part("polygonal_stock", prism(6, 24.0, -30.0, 30.0));
        let af = mark(id, "HEX 24 A/F", "polygon_across_flats.length");
        let l = mark(id, "60", "stock_length.length");
        assert!(stock_coverage(&a, &[af.clone(), l]).is_empty());
        assert_eq!(
            codes(stock_coverage(&a, &[af])),
            ["polygonal_stock_requirement_missing stock_length.length"]
        );
        let mut two = a.clone();
        let mut second = two.features[0].clone();
        second.id = "f:polygonal_stock:2".into();
        second.faces = vec![1];
        two.features.push(second);
        assert!(stock_wanted(&two).iter().all(|w| {
            w.values
                .as_ref()
                .is_err_and(|e| e.contains("2 polygonal stocks"))
        }));
        let square = part("polygonal_stock", prism(4, 24.0, -30.0, 30.0));
        assert_eq!(
            stock_wanted(&square)[0].values,
            Err("the polygonal-stock provider contract requires exactly six sides".into())
        );
    }
}
