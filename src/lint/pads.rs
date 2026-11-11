//! Raised pad coverage (Python's `linting/pad_coverage.py`): a rectangular raised pad states its
//! footprint's width and length, its height from the face it stands on, and its location in both
//! directions across it (LINT-pad_coverage-size_height_location, LINT-code-pad_requirement_state).
//!
//! Each of the Analysis's `pad` features (quiddity's `RaisedPad`: its box `x0`…`z1`, the `axis`
//! it stands along and its `direction`) is read in the part's frame. Its footprint's axes are
//! draftwright's fixed pair for its axis (`_PAD_PLANE_AXES`: x stands on y and z, y on z and x,
//! z on x and y), the first its length and the second its width. It has, judged by
//! [`parameters::coverage`]:
//!
//! - `pad_width.length`, `pad_length.length` and `pad_height.length`;
//! - its location from draftwright's common datum, the corner of the part's box (`datum_xy`, the
//!   Analysis's envelope minimum), to its centre: `location_pad.location.x` and `.y` for a pad
//!   standing along z; `location_pad.<length axis>` and `location_pad.<width axis>` for any
//!   other, a direction in which its centre is on the datum (within 1e-6) left out, as
//!   draftwright's compiler omits it (`pad_location_coincident_with_datum`).
//!
//! Python follows a Z pad's two locations through one structured fact; here each is a
//! requirement its claiming annotation must print. Not reached: `_suppressed` and `dropped`
//! (lint cannot read the author's omissions or the placement's drops) and the assembly
//! severity.

use serde_json::Value;

use super::parameters::{self, Wanted, fmt, fmt3};
use super::{LintIssue, Mark};
use crate::bridge::Feature;
use crate::bridge::holes::{dir_to_part, frame, principal, to_part};
use crate::model::Analysis;

const AXES: [&str; 3] = ["x", "y", "z"];

/// draftwright's footprint axes for a pad standing along each axis: its length, then its width.
const PLANE_AXES: [[usize; 2]; 3] = [[1, 2], [2, 0], [0, 1]];

/// A pad in the part's frame.
#[derive(Clone, Debug, PartialEq)]
pub struct Placed {
    pub axis: usize,
    pub bounds: [[f64; 2]; 3],
}

impl Placed {
    fn extent(&self, k: usize) -> f64 {
        self.bounds[k][1] - self.bounds[k][0]
    }

    fn centre(&self) -> [f64; 3] {
        self.bounds.map(|b| (b[0] + b[1]) / 2.0)
    }
}

/// *f*'s pad in the part's frame, or why it cannot be read.
pub fn placed(analysis: &Analysis, f: &Feature) -> Result<Placed, String> {
    let frame = frame(analysis);
    let p = &f.parameters;
    let axis = p
        .get("axis")
        .and_then(Value::as_str)
        .and_then(|a| AXES.iter().position(|x| *x == a))
        .ok_or("its record gives no axis")?;
    let corner = |end: usize| -> Result<[f64; 3], String> {
        let mut c = [0.0; 3];
        for (k, a) in AXES.iter().enumerate() {
            c[k] = parameters::size(f, &format!("{a}{end}"))?;
        }
        Ok(to_part(&frame, c))
    };
    let (a, b) = (corner(0)?, corner(1)?);
    let mut e = [0.0; 3];
    e[axis] = 1.0;
    let axis =
        principal(dir_to_part(&frame, e)).ok_or("its axis lies along no axis of the part")?;
    Ok(Placed {
        axis,
        bounds: [0, 1, 2].map(|k| [a[k].min(b[k]), a[k].max(b[k])]),
    })
}

/// A pad's locations from the datum *datum*: each direction's parameter and distance, leaving
/// out a direction across a pad not standing along z in which its centre is on the datum.
pub fn locations(p: &Placed, datum: [f64; 3]) -> Vec<(String, f64)> {
    let c = p.centre();
    if p.axis == 2 {
        return vec![
            ("location_pad.location.x".into(), (c[0] - datum[0]).abs()),
            ("location_pad.location.y".into(), (c[1] - datum[1]).abs()),
        ];
    }
    PLANE_AXES[p.axis]
        .iter()
        .map(|&k| (format!("location_pad.{}", AXES[k]), (c[k] - datum[k]).abs()))
        .filter(|(_, v)| *v > 1e-6)
        .collect()
}

/// The pads' requirements.
pub fn wanted(analysis: &Analysis) -> Vec<Wanted<'_>> {
    let mut out = Vec::new();
    for f in analysis.features.iter().filter(|f| f.kind == "pad") {
        let p = match placed(analysis, f) {
            Ok(p) => p,
            Err(why) => {
                out.push(Wanted {
                    feature: f,
                    parameter: "pad.?".into(),
                    values: Err(why),
                    callout: "its sizes and location".into(),
                });
                continue;
            }
        };
        let [long, wide] = PLANE_AXES[p.axis];
        for (parameter, v, word) in [
            ("pad_width.length", p.extent(wide), "wide"),
            ("pad_length.length", p.extent(long), "long"),
            ("pad_height.length", p.extent(p.axis), "high"),
        ] {
            out.push(Wanted {
                feature: f,
                parameter: parameter.into(),
                values: Ok(vec![v]),
                callout: format!("{} {word}", fmt(v)),
            });
        }
        for (parameter, v) in locations(&p, analysis.envelope[0]) {
            let axis = parameter.rsplit('.').next().unwrap_or("?").to_string();
            out.push(Wanted {
                feature: f,
                parameter,
                values: Ok(vec![v]),
                callout: format!(
                    "its location {} along {axis} from the part's corner",
                    fmt(v)
                ),
            });
        }
    }
    out
}

/// The raised pad findings.
pub fn coverage(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let noun = |f: &Feature| {
        let mut s = format!("raised pad {}", f.id);
        if let Ok(p) = placed(analysis, f) {
            let [long, wide] = PLANE_AXES[p.axis];
            s += &format!(
                " {} × {} × {} high along {}, at {}",
                fmt(p.extent(long)),
                fmt(p.extent(wide)),
                fmt(p.extent(p.axis)),
                AXES[p.axis],
                fmt3(p.centre())
            );
        }
        s
    };
    parameters::coverage(
        "pad",
        &wanted(analysis),
        marks,
        noun,
        "the pad's sizes in the view that looks down on it and its height side-on, and linear \
         dimensions of its location from the part's corner",
    )
}

#[cfg(test)]
mod tests {
    use super::super::paired_ramp_steps::tests::part;
    use super::*;
    use crate::output::Claim;
    use serde_json::json;

    fn mark(text: &str, parameters: &[&str]) -> Mark {
        Mark {
            units: Default::default(),
            name: format!("m:{text}"),
            view: Some("v:plan".into()),
            text: text.into(),
            claims: parameters
                .iter()
                .map(|p| Claim {
                    feature: "f:pad:1".into(),
                    parameter: p.to_string(),
                })
                .collect(),
        }
    }

    /// test_pad_completeness_evidence's `_lone("z", -1)`: a 15 × 10 × 5 pad under a 40 × 30 × 10
    /// body, its box x 5..20, y 8..18, z 0..5 on a part whose box starts at the origin. Five
    /// requirements (`test_pad_ledger_tracks_five_physical_requirements_and_fails_closed`): width
    /// 10 (y), length 15 (x), height 5, and its centre's location 12.5 along x and 13 along y;
    /// swapping the two locations' labels states neither
    /// (`test_swapping_valid_x_y_pad_location_values_loses_drawing_credit`). draftwright
    /// (origin/main 586628b) draws this part with exactly these five values (`20`, `10`,
    /// `5 HIGH`, `15`, `13`), and the y pad below with `13` along z and `12.5` along x.
    #[test]
    fn a_raised_pad_states_its_sizes_height_and_location() {
        let mut a = part(
            "pad",
            json!({ "x0": 5.0, "x1": 25.0, "y0": 8.0, "y1": 18.0, "z0": 0.0, "z1": 5.0,
                    "axis": "z", "direction": -1 }),
        );
        a.envelope = [[0.0, 0.0, 0.0], [40.0, 30.0, 15.0]];
        let got: Vec<(String, String)> = wanted(&a)
            .into_iter()
            .map(|w| (w.parameter, fmt(w.values.unwrap()[0])))
            .collect();
        let want: Vec<(String, String)> = [
            ("pad_width.length", "10"),
            ("pad_length.length", "20"),
            ("pad_height.length", "5"),
            ("location_pad.location.x", "15"),
            ("location_pad.location.y", "13"),
        ]
        .iter()
        .map(|(p, v)| (p.to_string(), v.to_string()))
        .collect();
        assert_eq!(got, want);
        let sizes = mark(
            "20 × 10 × 5 HIGH",
            &["pad_width.length", "pad_length.length", "pad_height.length"],
        );
        let x = mark("15", &["location_pad.location.x"]);
        let y = mark("13", &["location_pad.location.y"]);
        assert!(coverage(&a, &[sizes.clone(), x, y]).is_empty());
        let swapped = [
            sizes,
            mark("13", &["location_pad.location.x"]),
            mark("15", &["location_pad.location.y"]),
        ];
        let codes: Vec<String> = coverage(&a, &swapped)
            .into_iter()
            .map(|i| format!("{} {}", i.code, i.requirements[0].parameter))
            .collect();
        assert_eq!(
            codes,
            [
                "pad_requirement_unverifiable location_pad.location.x",
                "pad_requirement_unverifiable location_pad.location.y"
            ]
        );
        assert_eq!(coverage(&a, &[]).len(), 5);
    }

    /// A pad standing along y (`_lone("y", -1)`: x 5..20, y 0..5, z 8..18) is located along z and
    /// x, its footprint's length and width; one whose centre is on the datum across is located
    /// only along its length.
    #[test]
    fn a_side_pad_is_located_along_its_footprint_axes() {
        let mut a = part(
            "pad",
            json!({ "x0": 5.0, "x1": 20.0, "y0": 0.0, "y1": 5.0, "z0": 8.0, "z1": 18.0,
                    "axis": "y", "direction": -1 }),
        );
        a.envelope = [[0.0, 0.0, 0.0], [40.0, 15.0, 30.0]];
        let p = placed(&a, &a.features[0]).unwrap();
        let named = |l: Vec<(String, f64)>| -> Vec<(String, String)> {
            l.into_iter().map(|(p, v)| (p, fmt(v))).collect()
        };
        assert_eq!(
            named(locations(&p, a.envelope[0])),
            [
                ("location_pad.z".to_string(), "13".to_string()),
                ("location_pad.x".to_string(), "12.5".to_string())
            ]
        );
        assert_eq!(
            named(locations(&p, [12.5, 0.0, 0.0])),
            [("location_pad.z".to_string(), "13".to_string())]
        );
        let sizes: Vec<(String, String)> = wanted(&a)
            .into_iter()
            .take(3)
            .map(|w| (w.parameter, fmt(w.values.unwrap()[0])))
            .collect();
        assert_eq!(
            sizes,
            [
                ("pad_width.length".to_string(), "15".to_string()),
                ("pad_length.length".to_string(), "10".to_string()),
                ("pad_height.length".to_string(), "5".to_string())
            ]
        );
    }
}
