//! Blind slot coverage (Python's `linting/rectangular_blind_slot_coverage.py` and
//! `linting/round_bottom_blind_slot_coverage.py`): a rectangular blind slot states its width,
//! its mouth-to-end length and its depth; a round-bottom one its length, the flat width of its
//! floor and its side radius (LINT-rectangular_blind_slot_coverage-requirements,
//! LINT-round_bottom_blind_slot_coverage-requirements, and their `_requirement_state` codes).
//!
//! Blind slots reach the Analysis as `section_recess` features; those that
//! [`section_recesses::grammar`](super::section_recesses::grammar) reads as a blind slot (an edge-open recess with an open U
//! section, straight or with two equal quarter arcs) have three requirements each, judged by
//! [`parameters::coverage`]: `rectangular_blind_slot_width.length`, `_length.length` and
//! `_depth.length`, or `round_bottom_blind_slot_length.length`, `_flat_width.length` and
//! `_radius.radius`. Python follows each to its outcome through the IR feature it joins the record
//! to by every structural fact; here the Analysis feature is the record, so there is no join to
//! fail, and the values are the grammar's. Not reached: `_suppressed` (lint cannot read the
//! author's omissions) and the assembly severity.
//!
//! quiddity's semicircular blind slots (`semicircular_blind_slot`, #233) state their width (the
//! half-round floor's diameter), length and depth: `semicircular_blind_slot_width.length`,
//! `_length.length` and `_depth.length`, from the record's radius, length and straight height.

use super::parameters::{self, Wanted, fmt, fmt3};
use super::section_recesses::{BlindSlot, Grammar, grammar, recesses};
use super::{LintIssue, Mark};
use crate::bridge::Feature;
use crate::bridge::holes::{frame, to_part};
use crate::model::Analysis;

/// The blind slots of one kind, with their readings, in quiddity's order.
fn slots(analysis: &Analysis, round: bool) -> Vec<(&Feature, BlindSlot)> {
    recesses(analysis)
        .into_iter()
        .filter_map(|f| match grammar(f) {
            Ok(Grammar::RectangularBlindSlot(s)) if !round => Some((f, s)),
            Ok(Grammar::RoundBottomBlindSlot(s)) if round => Some((f, s)),
            _ => None,
        })
        .collect()
}

/// The blind slots' requirements: for a rectangular slot (*round* false) its width, length and
/// depth; for a round-bottom one its length, floor's flat width and radius.
pub fn wanted(analysis: &Analysis, round: bool) -> Vec<Wanted<'_>> {
    let mut out = Vec::new();
    for (f, s) in slots(analysis, round) {
        let sizes = if round {
            [
                ("round_bottom_blind_slot_length.length", s.length, "long"),
                (
                    "round_bottom_blind_slot_flat_width.length",
                    s.width - 2.0 * s.depth,
                    "bottom flat",
                ),
                ("round_bottom_blind_slot_radius.radius", s.depth, "R"),
            ]
        } else {
            [
                ("rectangular_blind_slot_width.length", s.width, "wide"),
                ("rectangular_blind_slot_length.length", s.length, "long"),
                ("rectangular_blind_slot_depth.length", s.depth, "deep"),
            ]
        };
        out.extend(sizes.into_iter().map(|(parameter, v, word)| Wanted {
            feature: f,
            parameter: parameter.into(),
            values: Ok(vec![v]),
            callout: if word == "R" {
                format!("R{}", fmt(v))
            } else {
                format!("{} {word}", fmt(v))
            },
        }));
    }
    out
}

/// The findings of one kind of blind slot.
fn findings(analysis: &Analysis, marks: &[Mark], round: bool) -> Vec<LintIssue> {
    let read = slots(analysis, round);
    let frame = frame(analysis);
    let noun = |f: &Feature| {
        let mut s = format!(
            "{} blind slot {}",
            if round { "round-bottom" } else { "rectangular" },
            f.id
        );
        if let Some((_, b)) = read.iter().find(|(g, _)| g.id == f.id) {
            s += &if round {
                format!(
                    " {} bottom flat × R{} × {} long",
                    fmt(b.width - 2.0 * b.depth),
                    fmt(b.depth),
                    fmt(b.length)
                )
            } else {
                format!(
                    " {} × {} × {} deep",
                    fmt(b.width),
                    fmt(b.length),
                    fmt(b.depth)
                )
            };
            s += &format!(", at {}", fmt3(to_part(&frame, b.origin)));
        }
        s
    };
    parameters::coverage(
        if round {
            "round_bottom_blind_slot"
        } else {
            "rectangular_blind_slot"
        },
        &wanted(analysis, round),
        marks,
        noun,
        "the slot's callout, in the view that looks into it",
    )
}

/// The rectangular blind slot findings, then the round-bottom ones (Python's order).
pub fn coverage(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let mut issues = findings(analysis, marks, false);
    issues.extend(findings(analysis, marks, true));
    issues
}

/// The semicircular blind slots' requirements: the width, length and depth of each.
pub fn semicircular_wanted(analysis: &Analysis) -> Vec<Wanted<'_>> {
    let mut out = Vec::new();
    for f in analysis
        .features
        .iter()
        .filter(|f| f.kind == "semicircular_blind_slot")
    {
        let radius = parameters::size(f, "radius");
        let depth = radius
            .clone()
            .and_then(|r| Ok(r + parameters::size(f, "straight_height")?));
        for (parameter, value, word) in [
            (
                "semicircular_blind_slot_width.length",
                radius.map(|r| 2.0 * r),
                "wide",
            ),
            (
                "semicircular_blind_slot_length.length",
                parameters::size(f, "length"),
                "long",
            ),
            ("semicircular_blind_slot_depth.length", depth, "deep"),
        ] {
            out.push(Wanted {
                feature: f,
                parameter: parameter.into(),
                callout: value.as_ref().map_or(format!("its {parameter}"), |v| {
                    format!("{} {word}", fmt(*v))
                }),
                values: value.map(|v| vec![v]),
            });
        }
    }
    out
}

/// The semicircular blind slot findings.
pub fn semicircular_coverage(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let noun = |f: &Feature| format!("semicircular blind slot {}", f.id);
    parameters::coverage(
        "semicircular_blind_slot",
        &semicircular_wanted(analysis),
        marks,
        noun,
        "the slot's callout, in the view that looks into it",
    )
}

#[cfg(test)]
mod tests {
    use super::super::paired_ramp_steps::tests::part;
    use super::super::section_recesses::tests::recess;
    use super::*;
    use crate::output::Claim;

    fn mark(text: &str, parameters: &[&str]) -> Mark {
        Mark {
            precision: None,
            units: Default::default(),
            name: "m_slot".into(),
            view: Some("v:front".into()),
            text: text.into(),
            claims: parameters
                .iter()
                .map(|p| Claim {
                    feature: "f:section_recess:1".into(),
                    parameter: p.to_string(),
                })
                .collect(),
        }
    }

    /// test_blind_slot_completeness's `_rect_part` (a 10 wide, 5 deep slot run 20 into a
    /// 30 × 20 × 40 block from its z = 0 face): its callout `OPEN SLOT 10 × 20 × 5 DEEP` states all
    /// three; one stating the width alone leaves the length and depth missing; a misprinted
    /// depth is unverifiable.
    #[test]
    fn a_rectangular_blind_slot_states_width_length_and_depth() {
        // Run along z (capped at z = 20, open at z = 0), u along x, v along y; the U opens
        // towards +y at y = 10.
        let frame = [
            [0.0, 0.0, 0.0],
            [0.0, 0.0, -1.0],
            [-1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
        ];
        let u = [
            ([5.0, 10.0], 0.0),
            ([5.0, 5.0], 0.0),
            ([-5.0, 5.0], 0.0),
            ([-5.0, 10.0], 0.0),
        ];
        let a = part(
            "section_recess",
            recess(
                "edge_open_recess",
                "rectangular",
                frame,
                [-20.0, 0.0],
                [false, true],
                false,
                &u,
            ),
        );
        let w = wanted(&a, false);
        let got: Vec<(&str, f64)> = w
            .iter()
            .map(|w| (w.parameter.as_str(), w.values.as_ref().unwrap()[0]))
            .collect();
        assert_eq!(
            got,
            [
                ("rectangular_blind_slot_width.length", 10.0),
                ("rectangular_blind_slot_length.length", 20.0),
                ("rectangular_blind_slot_depth.length", 5.0)
            ]
        );
        assert!(wanted(&a, true).is_empty());
        let all = [
            "rectangular_blind_slot_width.length",
            "rectangular_blind_slot_length.length",
            "rectangular_blind_slot_depth.length",
        ];
        assert!(coverage(&a, &[mark("OPEN SLOT 10 × 20 × 5 DEEP", &all)]).is_empty());
        let codes = |marks: &[Mark]| -> Vec<String> {
            coverage(&a, marks)
                .into_iter()
                .map(|i| format!("{} {}", i.code, i.requirements[0].parameter))
                .collect()
        };
        assert_eq!(
            codes(&[mark("10", &all[..1])]),
            [
                "rectangular_blind_slot_requirement_missing rectangular_blind_slot_length.length",
                "rectangular_blind_slot_requirement_missing rectangular_blind_slot_depth.length"
            ]
        );
        assert_eq!(
            codes(&[mark("OPEN SLOT 10 × 20 × 6 DEEP", &all)]),
            ["rectangular_blind_slot_requirement_unverifiable rectangular_blind_slot_depth.length"]
        );
        assert_eq!(
            coverage(&a, &[])[0].message,
            "rectangular blind slot f:section_recess:1 10 × 20 × 5 deep, at (0, 7.5, 10): \
             requirement rectangular_blind_slot_width.length (10 wide) is not stated on the sheet"
        );
    }

    /// test_blind_slot_completeness's `_round_part` (flat 4, radius 3, run 20): its callout
    /// `ROUND-BOTTOM OPEN SLOT 4 BOTTOM FLAT × R3 × 20 LONG` states all three.
    #[test]
    fn a_round_bottom_blind_slot_states_length_flat_and_radius() {
        let q = 0.414213562373;
        let x_run = [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ];
        let rb = [
            ([-5.0, 10.0], q),
            ([-2.0, 7.0], 0.0),
            ([2.0, 7.0], q),
            ([5.0, 10.0], 0.0),
        ];
        let a = part(
            "section_recess",
            recess(
                "edge_open_recess",
                "general",
                x_run,
                [-20.0, 0.0],
                [false, true],
                false,
                &rb,
            ),
        );
        let all = [
            "round_bottom_blind_slot_length.length",
            "round_bottom_blind_slot_flat_width.length",
            "round_bottom_blind_slot_radius.radius",
        ];
        let got: Vec<(String, f64)> = wanted(&a, true)
            .into_iter()
            .map(|w| (w.parameter, w.values.unwrap()[0]))
            .collect();
        assert_eq!(
            got,
            [
                (all[0].to_string(), 20.0),
                (all[1].to_string(), 4.0),
                (all[2].to_string(), 3.0)
            ]
        );
        assert!(
            coverage(
                &a,
                &[mark(
                    "ROUND-BOTTOM OPEN SLOT 4 BOTTOM FLAT × R3 × 20 LONG",
                    &all
                )]
            )
            .is_empty()
        );
        assert_eq!(
            coverage(&a, &[]).len(),
            3,
            "each requirement is missing without a callout"
        );
    }
}
