//! Observed drawing-consumer coverage (Python #1202): size evidence comes from the actual
//! rendered sheets. Recognition supplies the denominator; an unsuccessful build is unknown,
//! never supported by a capability declaration or treated as a successful empty drawing.
use std::collections::{BTreeMap, BTreeSet};

use serde::Serialize;

use crate::{
    bridge::holes,
    lint,
    model::{Analysis, Plan},
    output::Drawing,
    pmi::{Category, Reading},
};

#[derive(Clone, Debug, Serialize)]
pub struct ReviewCoverage {
    /// Recognition records subject to the planner's feature completeness check.
    dimensionable_features: Vec<String>,
    /// Every recognized feature, including ones without their own drafting grammar.
    recognized_features: Vec<String>,
    /// Features the planner exempts from its undimensioned check.
    undrafted_features: BTreeSet<String>,
    /// Rendered measurement roles claimed on each exempt feature.
    undrafted_measurements: BTreeMap<String, BTreeSet<String>>,
    /// Features with the planner's inclined-axis location gap and no square rendered view.
    inclined_location_gaps: BTreeSet<String>,
    /// A general-tolerance value produced ink in a title-block cell.
    tolerance_box_filled: bool,
    /// Semantic source inventory. Absent when the file has no reading or the reader refused it.
    pmi: Option<PmiCoverage>,
}

#[derive(Clone, Debug, Serialize)]
struct PmiCoverage {
    requirements: BTreeSet<String>,
    /// Source identities carried by rendered marks. Review lint judges their fidelity.
    drawn_sources: BTreeSet<String>,
    /// Rendered semantic item IDs (as used by measurement claims) and their source IDs.
    drawn_items: BTreeMap<String, String>,
}

pub(crate) fn review_coverage(
    analysis: &Analysis,
    plan: &Plan,
    drawings: &[Drawing],
) -> ReviewCoverage {
    let recognized_features = analysis.features.iter().map(|f| f.id.clone()).collect();
    let undrafted_features: BTreeSet<_> = analysis
        .features
        .iter()
        .filter(|feature| crate::dims::undrafted(feature))
        .map(|feature| feature.id.clone())
        .collect();
    let dimensionable_features = analysis
        .features
        .iter()
        .filter(|feature| !undrafted_features.contains(&feature.id))
        .map(|feature| feature.id.clone())
        .collect();
    let marks: Vec<_> = if undrafted_features.is_empty() && analysis.pmi.is_none() {
        Vec::new()
    } else {
        drawings.iter().flat_map(lint::marks).collect()
    };
    let mut undrafted_measurements = BTreeMap::<String, BTreeSet<String>>::new();
    for claim in marks.iter().flat_map(|mark| &mark.claims) {
        if undrafted_features.contains(&claim.feature) {
            undrafted_measurements
                .entry(claim.feature.clone())
                .or_default()
                .insert(claim.parameter.clone());
        }
    }
    let inclined_location_gaps = plan
        .problems
        .iter()
        .filter(|p| p.code == "feature_not_dimensioned" && p.inclined_axis_gap)
        .flat_map(|p| &p.subjects)
        .filter(|id| {
            let axis = analysis
                .features
                .iter()
                .find(|f| &f.id == *id)
                .and_then(|f| f.fingerprint.axis);
            !axis.is_some_and(|axis| {
                let len = axis.iter().map(|v| v * v).sum::<f64>().sqrt();
                drawings
                    .iter()
                    .flat_map(|d| d.sheet.frames.values())
                    .any(|frame| {
                        let dot = axis
                            .iter()
                            .zip(frame.direction)
                            .map(|(a, b)| a * b)
                            .sum::<f64>();
                        len > 0.0 && dot.abs() >= len * (1.0 - 1e-6)
                    })
            })
        })
        .cloned()
        .collect();
    let pmi = match &analysis.pmi {
        Some(Reading::Read(report)) => {
            let excluded: BTreeSet<_> = report
                .records
                .iter()
                .filter(|r| crate::pmi_lowering::no_part_requirement(r))
                .map(|r| &r.source_id)
                .collect();
            let requirements = report
                .records
                .iter()
                .map(|r| &r.source_id)
                .chain(
                    report
                        .sources
                        .iter()
                        .filter(|s| s.category != Category::Other || s.id.starts_with("material:"))
                        .map(|s| &s.id),
                )
                .filter(|source| !excluded.contains(source))
                .cloned()
                .collect();
            let identities = report.identities();
            let drawn_sources: BTreeSet<_> = lint::pmi_coverage::drawn(&marks, &identities)
                .into_iter()
                .map(str::to_owned)
                .collect();
            let drawn_items = identities
                .ids
                .into_iter()
                .filter(|(source, _)| drawn_sources.contains(source))
                .map(|(source, item)| (item, source))
                .collect();
            Some(PmiCoverage {
                requirements,
                drawn_sources,
                drawn_items,
            })
        }
        _ => None,
    };
    ReviewCoverage {
        dimensionable_features,
        recognized_features,
        undrafted_features,
        undrafted_measurements,
        inclined_location_gaps,
        tolerance_box_filled: drawings.iter().any(|d| {
            d.sheet
                .fields
                .iter()
                .any(|f| f.field == "general_tolerance")
        }),
        pmi,
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum State {
    Supported,
    Unsupported,
    Unknown,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct HoleObservation {
    pub feature: String,
    pub state: State,
    pub carriers: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct DrawingConsumer {
    pub supported: usize,
    pub unsupported: usize,
    pub unknown: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub build_error: Option<String>,
    pub holes: Vec<HoleObservation>,
}

/// Observe a build, including a failed one. The caller supplies every sheet so a hole on a
/// later sheet is judged once against the same physical inventory. No planning is performed.
pub fn drawing_consumer(analysis: &Analysis, build: Result<&[Drawing], &str>) -> DrawingConsumer {
    let (marks, build_error) = match build {
        Ok(drawings) => (
            drawings.iter().flat_map(lint::marks).collect::<Vec<_>>(),
            None,
        ),
        Err(error) => (Vec::new(), Some(error.to_string())),
    };
    let observations: Vec<_> = holes::read(analysis)
        .into_iter()
        .map(|(feature, hole)| {
            let unknown = build_error.clone().or_else(|| hole.as_ref().err().cloned());
            if let Some(reason) = unknown {
                return HoleObservation {
                    feature: feature.id.clone(),
                    state: State::Unknown,
                    carriers: Vec::new(),
                    reason: Some(reason),
                };
            }
            // Invariant: The None case returned its refusal before this branch.
            let hole = hole.expect("the unreadable record returned above");
            let carriers: Vec<_> = marks
                .iter()
                .filter(|m| {
                    m.claims
                        .iter()
                        .any(|c| c.feature == feature.id && c.parameter == "bore.diameter")
                        && lint::holes::states_bore(&m.text, hole.diameter)
                        && lint::holes::verified_schedule_size(analysis, m)
                })
                .map(|m| m.name.clone())
                .collect();
            let supported = !carriers.is_empty();
            HoleObservation {
                feature: feature.id.clone(),
                state: if supported {
                    State::Supported
                } else {
                    State::Unsupported
                },
                carriers,
                reason: (!supported)
                    .then(|| "No rendered annotation states this hole's bore size.".into()),
            }
        })
        .collect();
    DrawingConsumer {
        supported: observations
            .iter()
            .filter(|o| o.state == State::Supported)
            .count(),
        unsupported: observations
            .iter()
            .filter(|o| o.state == State::Unsupported)
            .count(),
        unknown: observations
            .iter()
            .filter(|o| o.state == State::Unknown)
            .count(),
        build_error,
        holes: observations,
    }
}
