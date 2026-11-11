//! Through-step coverage (Python's `linting/through_step_coverage.py`): a rectangular through
//! step states both legs of its open section (LINT-through_step_coverage-two_legs,
//! LINT-code-through_step_requirement_state).
//!
//! quiddity's `ThroughStep` gives the step's run `axis`, and its `section`: three points in the
//! plane across the run (its coordinates along the two other axes, in `x`, `y`, `z` order), whose
//! two segments are the legs. Each segment changes one coordinate; its length is the leg, a
//! requirement `through_step_leg.length.<axis>` named by the part's axis it lies along (quiddity's
//! axis taken through its frame; Python's IR is in the part's frame too). Each is judged by
//! [`parameters::coverage`]. A section that is not two legs along two different axes, or a leg
//! that lies along no axis of the part, cannot be checked and is reported unverifiable.
//!
//! A step running along x or y may instead have a leg stated by draftwright's legacy grammar
//! ([`alternatives`], Python's `_legacy_owner_plans`): a step height of the ladder (base to level)
//! or a step position (box to shoulder), or a plate's thickness, each alone when its interval is
//! the leg's, or with the overall extent when the leg is the rest of it, every interval within
//! 0.5 mm. Such a leg is stated when every term of one alternative is claimed by an annotation
//! whose text states the term's value (Python's `inapplicable`). Two steps whose legs have the same
//! alternatives cannot both be credited with the same ink: neither is, and a leg its own dimension
//! does not state is unverifiable (Python fails closed the same way).
//!
//! Python takes the legacy grammar only for a step draftwright's conversion did not lower (its
//! ownership stage left the legs to the ladder, plates and extents), and judges a lowered step by
//! its own legs alone; lint cannot see that decision, so a leg here is stated by either. The
//! intervals are the leg's own: Python's owners are draftwright's converted ladder and plates
//! (their levels, shoulders and faces within 0.5 mm of the leg), and the value it requires is the
//! owner's; here a claim on the ladder (`step_level`), a plate or the overall extents (`envelope`)
//! must state the leg's length, or the part of the extent it leaves, as an annotation does.
//! Python also matches each claim's drawn span to the interval; lint's marks carry no span, and
//! the ladder's anchored claims identify the interval, while the printed value verifies it.

use serde_json::Value;

use std::collections::BTreeMap;

use super::parameters::{self, Wanted, fmt, fmt3};
use super::{LintIssue, Mark};
use crate::bridge::Feature;
use crate::bridge::holes::{dir_to_part, frame, principal, to_part};
use crate::model::Analysis;

const AXES: [&str; 3] = ["x", "y", "z"];
/// The overall extents' parameters, by the part's axis (draftwright's `width`, `depth`, `height`).
const EXTENTS: [&str; 3] = ["width.length", "depth.length", "height.length"];
/// How near an owner's interval must be to a leg's, at both ends (mm; Python's `_matches`).
const NEAR: f64 = 0.5;

/// A through step's section: its three points across the run, in quiddity's frame.
fn section(f: &Feature) -> Result<Vec<[f64; 2]>, String> {
    let section: Vec<[f64; 2]> = f
        .parameters
        .get("section")
        .and_then(Value::as_array)
        .ok_or("its record gives no section")?
        .iter()
        .map(|q| {
            let q = q.as_array()?;
            Some([q.first()?.as_f64()?, q.get(1)?.as_f64()?])
        })
        .collect::<Option<_>>()
        .ok_or("its section is not a list of points")?;
    if section.len() != 3 {
        return Err(format!(
            "its section has {} points, not the three of two legs",
            section.len()
        ));
    }
    Ok(section)
}

/// A through step's legs in section order, each the part's axis it lies along and its length, or
/// why they cannot be read.
pub fn legs(f: &Feature, frame: &[[f64; 3]; 4]) -> Result<Vec<(&'static str, f64)>, String> {
    let p = &f.parameters;
    let run = p
        .get("axis")
        .and_then(Value::as_str)
        .and_then(|a| AXES.iter().position(|x| *x == a))
        .ok_or("its record gives no run axis")?;
    let section = section(f)?;
    let across: Vec<usize> = (0..3).filter(|&k| k != run).collect();
    let mut out = Vec::new();
    for w in section.windows(2) {
        let changed: Vec<usize> = (0..2).filter(|&i| w[0][i] != w[1][i]).collect();
        let [i] = changed[..] else {
            return Err("a segment of its section is not along one axis".into());
        };
        let mut local = [0.0; 3];
        local[across[i]] = 1.0;
        let along =
            principal(dir_to_part(frame, local)).ok_or("a leg lies along no axis of the part")?;
        out.push((AXES[along], (w[1][i] - w[0][i]).abs()));
    }
    if out[0].0 == out[1].0 {
        return Err("its two legs lie along one axis".into());
    }
    Ok(out)
}

/// A through step's run (the part's axis it lies along) and its legs as intervals in the part's
/// frame, in section order: each leg's axis and its low and high coordinates on it.
fn spans(f: &Feature, frame: &[[f64; 3]; 4]) -> Result<(usize, Vec<Span>), String> {
    let legs = legs(f, frame)?;
    let p = &f.parameters;
    let local = p
        .get("axis")
        .and_then(Value::as_str)
        .and_then(|a| AXES.iter().position(|x| *x == a))
        .ok_or("its record gives no run axis")?;
    let mut unit = [0.0; 3];
    unit[local] = 1.0;
    let run =
        principal(dir_to_part(frame, unit)).ok_or("its run lies along no axis of the part")?;
    let at = crate::bridge::holes::point(p.get("at")).ok_or("its record gives no at")?;
    let across: Vec<usize> = (0..3).filter(|&k| k != local).collect();
    let ends: Vec<[f64; 3]> = section(f)?
        .iter()
        .map(|q| {
            let mut l = at;
            l[across[0]] = q[0];
            l[across[1]] = q[1];
            to_part(frame, l)
        })
        .collect();
    let spans = legs
        .iter()
        .zip(ends.windows(2))
        .map(|((axis, _), w)| {
            // Invariant: The parsed record has a principal x, y or z axis.
            let k = AXES.iter().position(|a| a == axis).expect("an axis name");
            (k, w[0][k].min(w[1][k]), w[0][k].max(w[1][k]))
        })
        .collect();
    Ok((run, spans))
}

/// A leg as an interval: the part's axis it lies along, and its low and high coordinates.
type Span = (usize, f64, f64);

/// ADR1-8: the support planes of a through step's leg `through_step_leg.length.<axis>`: its two
/// ends along the part's axis it lies along (the box face or level it opens to, and the concave
/// corner), as redundancy walks them.
pub(crate) fn leg_span(
    analysis: &Analysis,
    f: &Feature,
    parameter: &str,
) -> Option<super::redundancy::Span> {
    let axis = parameter.strip_prefix("through_step_leg.length.")?;
    let k = AXES.iter().position(|a| *a == axis)?;
    let (_, spans) = spans(f, &frame(analysis)).ok()?;
    let (_, lo, hi) = spans.into_iter().find(|s| s.0 == k)?;
    Some(super::redundancy::Span::new(k, lo, hi))
}

/// A leg's requirement (`through_step_leg.length.<axis>`) and its alternative statements, each a
/// list of terms all of which must be stated.
pub type Alternatives = (String, Vec<Vec<Term>>);

/// One term of an alternative statement of a leg: an annotation claiming *parameter* (or one of
/// the ladder's parameters under it, `step_height.length.f6`) on *feature*, whose text states
/// *value*.
#[derive(Clone, Debug, PartialEq)]
pub struct Term {
    pub feature: String,
    pub parameter: String,
    pub value: f64,
}

impl Term {
    fn new(feature: &str, parameter: &str, value: f64) -> Term {
        Term {
            feature: feature.into(),
            parameter: parameter.into(),
            value,
        }
    }

    /// Whether some mark claims it and its text states its value.
    fn stated(&self, marks: &[Mark]) -> bool {
        marks.iter().any(|m| {
            m.claims.iter().any(|c| {
                c.feature == self.feature
                    && (c.parameter == self.parameter
                        || c.parameter
                            .strip_prefix(self.parameter.as_str())
                            .is_some_and(|rest| rest.starts_with('.'))
                        || (self.feature == "step_level"
                            && matches!(
                                c.parameter.as_str(),
                                "step_height.length" | "step_position.length"
                            )
                            && self
                                .parameter
                                .strip_prefix(&c.parameter)
                                .is_some_and(|rest| rest.starts_with('.'))))
            }) && m.states_text(&m.text, self.value)
        })
    }

    /// Its identity in an ambiguity check: the owner, the parameter and the value to 1e-3.
    fn key(&self) -> (String, String, i64) {
        (
            self.feature.clone(),
            self.parameter.clone(),
            (self.value * 1000.0).round() as i64,
        )
    }
}

/// A plate (quiddity's `Plate`) in the part's frame: its thin axis and its two faces' coordinates
/// on it, low first.
fn plate(f: &Feature, frame: &[[f64; 3]; 4]) -> Option<(usize, f64, f64)> {
    let p = &f.parameters;
    let letter = p.get("axis")?.as_str()?;
    let local = AXES.iter().position(|a| *a == letter)?;
    let mut unit = [0.0; 3];
    unit[local] = 1.0;
    let axis = principal(dir_to_part(frame, unit))?;
    let n = |k: &str| p.get(k).and_then(Value::as_f64);
    let across: Vec<usize> = (0..3).filter(|&k| k != local).collect();
    let at = |c: f64| -> Option<f64> {
        let mut l = [0.0; 3];
        l[local] = c;
        l[across[0]] = n("u")?;
        l[across[1]] = n("v")?;
        Some(to_part(frame, l)[axis])
    };
    let (a, b) = (at(n("lo")?)?, at(n("hi")?)?);
    Some((axis, a.min(b), a.max(b)))
}

/// The other statements of a through step's legs in draftwright's legacy grammar, by leg in
/// section order (`through_step_leg.length.<axis>`, its alternatives, each a list of terms all of
/// which must be stated), for a step running along x or y whose legs can be read; none otherwise.
pub fn alternatives(analysis: &Analysis, f: &Feature) -> Vec<Alternatives> {
    let frame = frame(analysis);
    let Ok((run, spans)) = spans(f, &frame) else {
        return Vec::new();
    };
    if run == 2 {
        return Vec::new();
    }
    let [lo, hi] = analysis.envelope;
    let near = |x: f64, y: f64| (x - y).abs() < NEAR;
    let plates: Vec<(&Feature, (usize, f64, f64))> = analysis
        .features
        .iter()
        .filter(|g| g.kind == "plate")
        .filter_map(|g| Some((g, plate(g, &frame)?)))
        .collect();
    let mut out = Vec::new();
    for (k, a, b) in spans {
        let extent = Term::new("envelope", EXTENTS[k], hi[k] - lo[k]);
        let ladder = |at: f64| {
            if k == 2 && analysis.level_supports.iter().any(|s| !s.faces.is_empty()) {
                crate::geometry_ids::height_parameter(analysis, at)
            } else if k < 2 && analysis.risers.iter().any(|r| !r.faces.is_empty()) {
                crate::geometry_ids::position_parameter(analysis, k, at)
            } else if k == 2 {
                "step_height.length".into()
            } else {
                "step_position.length".into()
            }
        };
        let mut alts = Vec::new();
        // A level's height from the base, or a shoulder's position from the box.
        if near(a, lo[k]) {
            alts.push(vec![Term::new("step_level", &ladder(b), b - lo[k])]);
        }
        if near(b, hi[k]) {
            alts.push(vec![
                Term::new("step_level", &ladder(a), a - lo[k]),
                extent.clone(),
            ]);
        }
        for (g, (axis, plo, phi)) in &plates {
            if *axis != k {
                continue;
            }
            let thickness = Term::new(&g.id, "thickness.length", phi - plo);
            if near(*plo, a) && near(*phi, b) {
                alts.push(vec![thickness.clone()]);
            }
            let rest_above = near(*plo, lo[k]) && near(*phi, a) && near(hi[k], b);
            let rest_below = near(*phi, hi[k]) && near(lo[k], a) && near(*plo, b);
            if rest_above || rest_below {
                alts.push(vec![thickness, extent.clone()]);
            }
        }
        out.push((format!("through_step_leg.length.{}", AXES[k]), alts));
    }
    out
}

/// The through steps' requirements: each leg, or, when the legs cannot be read, one requirement
/// (`through_step_leg.length`) reported unverifiable.
pub fn wanted(analysis: &Analysis) -> Vec<Wanted<'_>> {
    let frame = frame(analysis);
    let mut out = Vec::new();
    for f in analysis
        .features
        .iter()
        .filter(|f| f.kind == "through_step")
    {
        match legs(f, &frame) {
            Ok(legs) => out.extend(legs.into_iter().map(|(axis, length)| Wanted {
                feature: f,
                parameter: format!("through_step_leg.length.{axis}"),
                values: Ok(vec![length]),
                callout: format!("{} along {axis}", fmt(length)),
            })),
            Err(why) => out.push(Wanted {
                feature: f,
                parameter: "through_step_leg.length".into(),
                values: Err(why),
                callout: "its legs".into(),
            }),
        }
    }
    out
}

/// The through-step coverage findings.
pub fn coverage(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let frame = frame(analysis);
    let noun = |f: &Feature| {
        let p = &f.parameters;
        let mut s = format!("through step {}", f.id);
        if let Some(a) = parameters::direction(&frame, p.get("axis")) {
            s += &format!(" along {}", fmt3(a));
        }
        if let Ok(legs) = legs(f, &frame) {
            let legs: Vec<String> = legs
                .iter()
                .map(|(axis, l)| format!("{} ({axis})", fmt(*l)))
                .collect();
            s += &format!(", legs {}", legs.join(" × "));
        }
        if let Some(at) = parameters::place(&frame, p.get("at")) {
            s += &format!(", at {}", fmt3(at));
        }
        s
    };
    // The alternatives of each step running along x or y, and which steps share theirs.
    let alts: BTreeMap<&str, Vec<Alternatives>> = analysis
        .features
        .iter()
        .filter(|f| f.kind == "through_step")
        .map(|f| (f.id.as_str(), alternatives(analysis, f)))
        .filter(|(_, a)| !a.is_empty() && a.iter().all(|(_, alts)| !alts.is_empty()))
        .collect();
    type Signature = Vec<(String, Vec<Vec<(String, String, i64)>>)>;
    let signature = |a: &[Alternatives]| -> Signature {
        let mut s: Signature = a
            .iter()
            .map(|(p, alts)| {
                (
                    p.clone(),
                    alts.iter()
                        .map(|t| t.iter().map(Term::key).collect())
                        .collect(),
                )
            })
            .collect();
        s.sort();
        s
    };
    let mut shared: BTreeMap<Signature, usize> = BTreeMap::new();
    for a in alts.values() {
        *shared.entry(signature(a)).or_default() += 1;
    }
    let mut wanted = wanted(analysis);
    wanted.retain_mut(|w| {
        let Some(a) = alts.get(w.feature.id.as_str()) else {
            return true;
        };
        let Some((_, leg)) = a.iter().find(|(p, _)| *p == w.parameter) else {
            return true;
        };
        if shared[&signature(a)] > 1 {
            let own = w.values.as_ref().is_ok_and(|v| {
                marks.iter().any(|m| {
                    m.claims
                        .iter()
                        .any(|c| c.feature == w.feature.id && c.parameter == w.parameter)
                        && v.iter().all(|&x| m.states_text(&m.text, x))
                })
            });
            if !own {
                w.values = Err(
                    "another through step's legs have the same alternative owners, so their \
                     dimensions cannot be credited to either"
                        .into(),
                );
            }
            return true;
        }
        !leg.iter()
            .any(|terms| terms.iter().all(|t| t.stated(marks)))
    });
    parameters::coverage(
        "through_step",
        &wanted,
        marks,
        noun,
        "a linear dimension of the leg, in a view that sees the step's section",
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::output::Claim;
    use serde_json::json;

    fn part(frame: [[f64; 3]; 4], record: Value) -> Analysis {
        serde_json::from_value(json!({
            "schema": "draftwright.analysis/2",
            "binding": { "source_sha256": "", "part": "test", "face_count": 2, "loader": "test" },
            "envelope": [[0.0, 0.0, 0.0], [100.0, 100.0, 10.0]],
            "features": [{
                "id": "f:through_step:1", "kind": "through_step", "faces": [0, 1],
                "parameters": record,
                "fingerprint": { "version": "test", "family": "through_steps",
                                 "subtype": "ThroughStep", "sizes": {},
                                 "position": [0.0, 0.0, 0.0], "neighbourhood": [] },
            }],
            "iso_bounds": [0.0, 0.0, 0.0, 0.0],
            "recognition": { "recogniser": "test", "frame": frame },
        }))
        .unwrap()
    }

    /// m10127's through step (quiddity 0.3.9): its section's first segment runs along y
    /// (11.512), its second along x (18.734); quiddity's frame turns x and y about, which leaves
    /// the axes' names alone.
    #[test]
    fn a_through_step_states_both_legs_named_by_the_parts_axes() {
        let record = json!({ "axis": "z", "at": [6.876, 13.323, -0.044], "length": 44.729,
                             "section": [[-2.491, 19.079], [-2.491, 7.567], [16.243, 7.567]] });
        let flip = [
            [16.0, 19.0, 22.0],
            [-1.0, 0.0, 0.0],
            [0.0, -1.0, 0.0],
            [0.0, 0.0, 1.0],
        ];
        let a = part(flip, record.clone());
        let w = wanted(&a);
        let got: Vec<(&str, String)> = w
            .iter()
            .map(|w| (w.parameter.as_str(), fmt(w.values.as_ref().unwrap()[0])))
            .collect();
        assert_eq!(
            got,
            [
                ("through_step_leg.length.y", "11.512".to_string()),
                ("through_step_leg.length.x", "18.734".to_string())
            ]
        );
        let mark = |text: &str, axis: &str| Mark {
            precision: None,
            units: Default::default(),
            name: format!("m:{axis}"),
            view: Some("v:plan".into()),
            text: text.into(),
            claims: vec![Claim {
                feature: "f:through_step:1".into(),
                parameter: format!("through_step_leg.length.{axis}"),
            }],
        };
        assert!(coverage(&a, &[mark("18.7", "x"), mark("11.5", "y")]).is_empty());
        let found: Vec<String> = coverage(&a, &[mark("18.7", "x")])
            .into_iter()
            .map(|i| format!("{} {}", i.code, i.requirements[0].parameter))
            .collect();
        assert_eq!(
            found,
            ["through_step_requirement_missing through_step_leg.length.y"]
        );
        // quiddity's frame turning its x onto the part's z: the legs are named by the part's.
        let turned = [[0.0; 3], [0.0, 0.0, 1.0], [0.0, 1.0, 0.0], [-1.0, 0.0, 0.0]];
        let mut r = record.clone();
        r["axis"] = json!("z");
        let names: Vec<String> = wanted(&part(turned, r))
            .into_iter()
            .map(|w| w.parameter)
            .collect();
        assert_eq!(
            names,
            ["through_step_leg.length.y", "through_step_leg.length.z"]
        );
        // A section that is not two legs cannot be checked.
        let mut bad = record;
        bad["section"] = json!([[0.0, 0.0], [1.0, 1.0], [2.0, 1.0]]);
        let issues = coverage(&part(flip, bad), &[]);
        assert_eq!(issues.len(), 1);
        assert_eq!(issues[0].code, "through_step_requirement_unverifiable");
    }
    #[test]
    fn face_ladder_terms_keep_captured_generic_claims_and_check_the_value() {
        for parameter in ["step_height.length", "step_position.length"] {
            let term = Term::new("step_level", &format!("{parameter}.f6"), 10.);
            let mark = |parameter: &str, text: &str| Mark {
                precision: None,
                units: Default::default(),
                name: "ladder".into(),
                view: None,
                text: text.into(),
                claims: vec![Claim {
                    feature: "step_level".into(),
                    parameter: parameter.into(),
                }],
            };
            assert!(term.stated(&[mark(parameter, "10")]));
            assert!(term.stated(&[mark(&format!("{parameter}.f6"), "10")]));
            assert!(!term.stated(&[mark(parameter, "11")]));
            assert!(!term.stated(&[mark(&format!("{parameter}.f7"), "10")]));
        }
    }

    /// test_framed_step_family_evidence's legacy case, as draftwright's `stepped_block` fixture
    /// cuts it: a 20 × 10 step along y from the top edge of a 40 × 30 × 20 block. Its legs are
    /// stated by the ladder and the overall extents (the level 10 above the base and the height
    /// above it; the shoulder 20 from the box and the width beyond it), or by a base plate's
    /// thickness and the height; not by half of either. Two steps with the same alternatives get
    /// no credit from them.
    #[test]
    fn a_through_step_along_y_may_be_stated_by_the_ladder_or_a_plate() {
        let identity = [[0.0; 3], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]];
        let record = json!({ "axis": "y", "at": [10.0, 0.0, 5.0], "length": 30.0,
                             "section": [[0.0, 10.0], [0.0, 0.0], [20.0, 0.0]] });
        let mut a = part(identity, record);
        a.envelope = [[-20.0, -15.0, -10.0], [20.0, 15.0, 10.0]];
        let step = &a.features[0];
        type Named = Vec<Vec<(String, String, String)>>;
        let alts: Vec<(String, Named)> = alternatives(&a, step)
            .into_iter()
            .map(|(p, alts)| {
                let alts = alts
                    .into_iter()
                    .map(|t| {
                        t.into_iter()
                            .map(|t| (t.feature, t.parameter, fmt(t.value)))
                            .collect()
                    })
                    .collect();
                (p, alts)
            })
            .collect();
        let term = |f: &str, p: &str, v: &str| (f.to_string(), p.to_string(), v.to_string());
        assert_eq!(
            alts,
            [
                (
                    "through_step_leg.length.z".to_string(),
                    vec![vec![
                        term("step_level", "step_height.length", "10"),
                        term("envelope", "height.length", "20")
                    ]]
                ),
                (
                    "through_step_leg.length.x".to_string(),
                    vec![vec![
                        term("step_level", "step_position.length", "20"),
                        term("envelope", "width.length", "40")
                    ]]
                ),
            ]
        );
        let mark = |name: &str, text: &str, feature: &str, parameter: &str| Mark {
            precision: None,
            units: Default::default(),
            name: name.into(),
            view: Some("v:front".into()),
            text: text.into(),
            claims: vec![Claim {
                feature: feature.into(),
                parameter: parameter.into(),
            }],
        };
        let ladder = [
            mark("dim_step_0", "10", "step_level", "step_height.length.h10"),
            mark(
                "dim_shoulder_x0",
                "20",
                "step_level",
                "step_position.length.x20",
            ),
            mark("dim_height", "20", "envelope", "height.length"),
            mark("m_env_width", "40", "envelope", "width.length"),
        ];
        let found = |a: &Analysis, marks: &[Mark]| -> Vec<String> {
            coverage(a, marks)
                .into_iter()
                .map(|i| {
                    format!(
                        "{} {} {}",
                        i.code, i.subjects[0], i.requirements[0].parameter
                    )
                })
                .collect()
        };
        assert!(found(&a, &ladder).is_empty());
        // Python's own claims name the ladder as one (`step_height.length`).
        let mut pythons = ladder.clone();
        pythons[0].claims[0].parameter = "step_height.length".into();
        assert!(found(&a, &pythons).is_empty());
        // Half an alternative states nothing; a misprinted term neither.
        assert_eq!(
            found(&a, &ladder[1..]),
            ["through_step_requirement_missing f:through_step:1 through_step_leg.length.z"]
        );
        let mut wrong = ladder.clone();
        wrong[1].text = "21".into();
        assert_eq!(
            found(&a, &wrong),
            ["through_step_requirement_missing f:through_step:1 through_step_leg.length.x"]
        );
        // A base plate 10 thick: its thickness and the height state the leg above it.
        let mut plated = a.clone();
        let mut plate = plated.features[0].clone();
        plate.id = "f:plate:1".into();
        plate.kind = "plate".into();
        plate.parameters = serde_json::from_value(
            json!({ "axis": "z", "lo": -10.0, "hi": 0.0, "u": 0.0, "v": 0.0 }),
        )
        .unwrap();
        plated.features.push(plate);
        let by_plate = [
            mark("dim_plate_z0", "10", "f:plate:1", "thickness.length"),
            ladder[1].clone(),
            ladder[2].clone(),
            ladder[3].clone(),
        ];
        assert!(found(&plated, &by_plate).is_empty());
        // Two steps with the same alternatives: the ink cannot be credited to either.
        let mut twins = a.clone();
        let mut twin = twins.features[0].clone();
        twin.id = "f:through_step:2".into();
        twins.features.push(twin);
        let found_twins = found(&twins, &ladder);
        assert_eq!(found_twins.len(), 4);
        assert!(
            found_twins
                .iter()
                .all(|f| f.starts_with("through_step_requirement_unverifiable")),
            "{found_twins:?}"
        );
        // Their own dimensions still state their legs.
        let own: Vec<Mark> = ["f:through_step:1", "f:through_step:2"]
            .iter()
            .flat_map(|id| {
                [
                    mark("z", "10", id, "through_step_leg.length.z"),
                    mark("x", "20", id, "through_step_leg.length.x"),
                ]
            })
            .collect();
        assert!(found(&twins, &own).is_empty());
        // A step along z has no alternatives.
        let mut along_z = a.clone();
        along_z.features[0].parameters["axis"] = json!("z");
        assert!(alternatives(&along_z, &along_z.features[0]).is_empty());
    }
}
