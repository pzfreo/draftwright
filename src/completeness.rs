//! Bounded completeness: Analysis face claims and recognised features without drawn dimensions.
//! Neither count proves recognition recall or manufacturing readiness.

use std::collections::BTreeSet;

use serde::{Deserialize, Serialize};

use crate::items::State;
use crate::model::{Analysis, Plan, Problem};

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    Clear,
    #[default]
    NeedsAttention,
}

pub fn status(problems: &[Problem]) -> Status {
    if problems.iter().any(|p| p.severity != "info") {
        Status::NeedsAttention
    } else {
        Status::Clear
    }
}

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Completeness {
    pub claimed_faces: usize,
    pub faces: usize,
    pub unplanned_features: usize,
}

/// Refresh after escalation, which can move a hole's measurements into a table.
pub fn report(analysis: &Analysis, plan: &mut Plan) {
    crate::nominal::report(plan);
    crate::units::advise(analysis, plan);
    plan.problems
        .retain(|p| p.code != "relief_profiles_per_model");
    let severity = if analysis.solid_count.is_some_and(|n| n > 1) {
        "info"
    } else {
        "warning"
    };
    // quiddity's accounting, where the recognition carries it (#207): a face of any record,
    // evidence records included, is a feature's; body faces are credited below.
    let accounted = |group: crate::model::FaceGroup| -> Vec<usize> {
        analysis
            .face_accounting
            .as_ref()
            .map_or(Vec::new(), |groups| {
                (0..groups.len().min(analysis.binding.face_count))
                    .filter(|&i| groups[i] == group)
                    .collect()
            })
    };
    let mut claimed: BTreeSet<usize> = analysis
        .features
        .iter()
        .filter(|f| f.kind != "turned_body")
        .flat_map(|f| f.faces.iter().copied())
        .filter(|&i| i < analysis.binding.face_count)
        .chain(accounted(crate::model::FaceGroup::Feature))
        .collect();
    let feature_claims = claimed.len();
    for n in crate::notches::requirements(analysis) {
        if plan
            .dimensions
            .iter()
            .any(|d| d.placed.is_some() && d.measurements.iter().any(|m| m.feature == n.id))
        {
            claimed.extend(n.faces);
        }
    }
    let marks: Vec<_> = plan
        .dimensions
        .iter()
        .filter(|d| d.placed.is_some())
        .map(|d| crate::lint::Mark {
            precision: None,
            units: plan.spec.units,
            name: d.id.clone(),
            view: Some(d.view.clone()),
            text: d.label.clone(),
            claims: d
                .measurements
                .iter()
                .map(|m| crate::output::Claim {
                    feature: m.feature.clone(),
                    parameter: m.parameter.clone(),
                })
                .collect(),
        })
        .collect();
    let mut marks = marks;
    if !plan.spec.dimension_schemes.is_empty() {
        let (extra, _) = crate::lint::schemes::evidence(analysis, &marks);
        marks.extend(extra);
    }
    let overall_stated = crate::lint::envelope::outcomes(analysis, &marks)
        .iter()
        .all(|o| o.state == crate::lint::turned::State::Placed);
    if analysis.solid_count == Some(1) && overall_stated && analysis.face_accounting.is_some() {
        claimed.extend(accounted(crate::model::FaceGroup::Body));
    } else if analysis.solid_count == Some(1) && overall_stated {
        // Without quiddity's accounting, the envelope planes stand in for its body evidence.
        claimed.extend(
            analysis
                .stock_faces
                .iter()
                .copied()
                .filter(|i| *i < analysis.binding.face_count),
        );
        // Older analyses can prove an undecorated six-face box without face identities.
        let box_stock = analysis.binding.face_count == 6
            && analysis.principal_profiles.len() == 1
            && analysis.principal_profiles[0].faces.len() == 6
            && analysis.principal_profiles[0].faces.iter().all(|f| {
                f.inner.is_empty() && f.outer_edges == 4 && f.outer_arcs.is_empty() && !f.unsplit
            })
            && (0..3).all(|k| {
                [0, 1].iter().all(|&end| {
                    analysis.principal_profiles[0]
                        .faces
                        .iter()
                        .filter(|f| f.axis == k && (f.at - analysis.envelope[end][k]).abs() <= 1e-5)
                        .count()
                        == 1
                })
            });
        if box_stock {
            claimed.extend(0..6);
        }
    }
    let mut stated: BTreeSet<&str> = plan
        .dimensions
        .iter()
        .filter(|d| d.placed.is_some() || matches!(d.state, State::Covered { .. }))
        .flat_map(|d| d.measurements.iter().map(|m| m.feature.as_str()))
        .collect();
    stated.extend(
        plan.spec
            .features
            .iter()
            .filter(|(id, f)| {
                !f.note_satisfies.is_empty()
                    && plan.gdt.iter().any(|g| {
                        g.placed.is_some() && g.source == format!("spec:features/{id}/note")
                    })
            })
            .map(|(id, _)| id.as_str()),
    );
    // Table-owned scalar spans live in the covered-item ledger; the schedule retains their
    // measurement provenance while lint independently verifies its printed member cells.
    stated.extend(
        plan.hole_tables
            .iter()
            .flat_map(|t| t.covered.iter())
            .map(|m| m.feature.as_str()),
    );
    // Radius coverage may be carried by a general note or a located radius table.
    stated.extend(
        plan.notes
            .iter()
            .filter(|n| n.placed.is_some())
            .flat_map(|n| &n.measurements)
            .filter(|m| {
                matches!(
                    m.parameter.as_str(),
                    "blend.radius" | "fillet.radius" | "cylindrical_profile.radius"
                )
            })
            .map(|m| m.feature.as_str()),
    );
    stated.extend(
        plan.radius_table
            .iter()
            .flat_map(|t| &t.rows)
            .map(|r| r.feature.as_str()),
    );
    if plan
        .notes
        .iter()
        .any(|n| n.id == crate::lint::blends::RELIEF_NOTE && n.placed.is_some())
    {
        let features: Vec<_> = analysis
            .features
            .iter()
            .filter(|f| crate::lint::blends::is_relief(analysis, f))
            .map(|f| f.id.as_str())
            .collect();
        stated.extend(features.iter().copied());
        if !features.is_empty() {
            plan.problems.push(Problem {
                id: "p:relief_profiles_per_model".into(),
                code: "relief_profiles_per_model".into(),
                severity: "info".into(),
                message:
                    "Grouped shallow relief radii are governed by the placed model-profile note."
                        .into(),
                subjects: features
                    .into_iter()
                    .map(String::from)
                    .chain(std::iter::once(crate::lint::blends::RELIEF_NOTE.into()))
                    .collect(),
                ..Default::default()
            });
        }
    }
    for o in &plan.omitted {
        if let State::Covered { by } = &o.state
            && plan
                .dimensions
                .iter()
                .any(|d| d.id == *by && d.placed.is_some())
            && let Some((feature, _)) = o.id.strip_prefix("d:").and_then(|id| id.rsplit_once('/'))
        {
            stated.insert(feature);
        }
    }
    for p in analysis.recognition.iter().flat_map(|r| &r.patterns) {
        if stated.contains(p.id.as_str()) {
            stated.extend(p.members.iter().map(String::as_str));
        }
    }
    for pattern in analysis
        .features
        .iter()
        .filter(|f| f.kind == "slot_pattern")
    {
        if stated.contains(pattern.id.as_str())
            && let Some(members) = pattern.parameters.get("slots").and_then(|v| v.as_array())
        {
            stated.extend(members.iter().filter_map(|m| m.as_str()));
        }
    }
    // The measurements drawn, for a feature another's dimensions state (#233).
    let said: BTreeSet<(&str, &str)> = plan
        .dimensions
        .iter()
        .filter(|d| d.placed.is_some() || matches!(d.state, State::Covered { .. }))
        .flat_map(|d| &d.measurements)
        .chain(plan.hole_tables.iter().flat_map(|t| &t.covered))
        .map(|m| (m.feature.as_str(), m.parameter.as_str()))
        .collect();
    let bands = crate::lint::turned::bands(analysis);
    let unplanned: Vec<String> = analysis
        .features
        .iter()
        .filter(|f| {
            if stated.contains(f.id.as_str()) || crate::dims::undrafted(f) {
                return false;
            }
            let by = crate::dims::stated_by(analysis, f);
            if !by.is_empty() && by.iter().all(|(o, p)| said.contains(&(o.as_str(), *p))) {
                return false;
            }
            if f.kind == "boss"
                && crate::lint::bosses::groove_floor(analysis, f)
                && analysis.features.iter().any(|g| {
                    g.kind == "groove"
                        && stated.contains(g.id.as_str())
                        && g.faces.iter().any(|i| f.faces.contains(i))
                })
            {
                return false;
            }
            if f.kind == "turned_step"
                && bands
                    .as_ref()
                    .is_ok_and(|bs| !bs.iter().any(|b| b.id == f.id))
                && analysis.features.iter().any(|g| {
                    g.kind == "groove"
                        && stated.contains(g.id.as_str())
                        && g.faces.iter().any(|i| f.faces.contains(i))
                })
            {
                return false;
            }
            true
        })
        .map(|f| f.id.clone())
        .collect();
    plan.completeness = Completeness {
        claimed_faces: claimed.len(),
        faces: analysis.binding.face_count,
        unplanned_features: unplanned.len(),
    };
    plan.problems.retain(|p| {
        !matches!(
            p.code.as_str(),
            "unclaimed_geometry" | "shape_not_recognised"
        ) && !p.id.starts_with("p:feature_unplanned:")
    });
    for p in &mut plan.problems {
        if matches!(
            p.code.as_str(),
            "dimensions_not_ported" | "feature_not_dimensioned"
        ) {
            p.severity = severity.into();
        }
    }
    if claimed.len() < analysis.binding.face_count {
        // About the faces, not the sheet (#25): its subjects are the unclaimed faces themselves,
        // `face:<index>`, as no feature has them. The ID keeps its old form, so an editor that
        // remembers the problem still knows it.
        let unclaimed = (0..analysis.binding.face_count)
            .filter(|i| !claimed.contains(i))
            .map(|i| format!("face:{i}"))
            .collect();
        plan.problems.push(Problem {
            id: "p:unclaimed_geometry:sheet".into(), code: "unclaimed_geometry".into(),
            severity: "info".into(),
            message: format!("Analysis {} claim {} of {} faces; {} faces are unclaimed. Review the geometry: ordinary stock faces may also be unclaimed; this is not a count of missed features.", if claimed.len()>feature_claims {"features and dimensioned stock"} else {"features"}, claimed.len(), analysis.binding.face_count, analysis.binding.face_count - claimed.len()),
            subjects: unclaimed, ..Default::default()
        });
    }
    let shapes = crate::unexplained::problems(analysis, &plan.spec, &claimed);
    plan.problems.extend(shapes);
    for id in unplanned {
        if !plan.problems.iter().any(|p| {
            p.subjects.contains(&id)
                && matches!(
                    p.code.as_str(),
                    "dimensions_not_ported"
                        | "feature_not_dimensioned"
                        | "angled_step_requirement_unsupported"
                        | "modelled_thread_not_depicted"
                )
        }) {
            plan.problems.push(Problem {
                id: format!("p:feature_unplanned:{id}"), code: "feature_not_dimensioned".into(),
                severity: severity.into(), message: format!("{} has no dimension drawn or carried by the hole table; review its omitted or undrawable items.", crate::wording::capital(&crate::wording::feature_id(analysis, &id))),
                subjects: vec![id], ..Default::default()
            });
        }
    }
    plan.status = status(&plan.problems);
}
