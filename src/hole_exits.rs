//! Partly covered through-hole exits: report consulted covering geometry.
use crate::model::{Analysis, Plan, Problem};

/// Consult the original recognition for the obstruction fact, and only the finished
/// drawing for any claim about how the hole is presented.
pub(crate) fn report(a: &Analysis, plan: &mut Plan) {
    let warnings = warnings(a, plan);
    plan.problems.retain(|p| p.code != "hole_exit_obstructed");
    plan.problems.extend(warnings);
    plan.status = crate::completeness::status(&plan.problems);
}

fn thru(text: &str) -> bool {
    text.split_whitespace().any(|word| word == "THRU")
}

fn drawn_thru(plan: &Plan, id: &str) -> bool {
    if plan.spec.features.get(id).is_some_and(|f| {
        f.suppress
            || f.reclassify
                .is_some_and(|k| k != crate::recognition_corrections::Kind::Hole)
    }) {
        return false;
    }
    plan.dimensions.iter().any(|d| {
        d.placed.is_some()
            && d.kind == "callout"
            && thru(&d.label)
            && d.measurements
                .iter()
                .any(|m| m.feature == id && m.parameter == "bore.diameter")
    }) || plan
        .hole_table
        .iter()
        .chain(&plan.hole_tables)
        .any(|table| {
            !table.blocks.is_empty()
                && table.blocks.iter().all(|b| b.placed.is_some())
                && table
                    .rows
                    .iter()
                    .any(|r| r.feature == id && r.cells.iter().any(|c| thru(c)))
        })
}

fn warnings(a: &Analysis, plan: &Plan) -> Vec<Problem> {
    let mut out = Vec::new();
    for f in a.features.iter().filter(|f| f.kind == "hole") {
        for (i, o) in f
            .parameters
            .get("exit_obstructions")
            .and_then(|v| v.as_array())
            .into_iter()
            .flatten()
            .enumerate()
        {
            let covered: Vec<usize> = o["faces"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(|v| v.as_u64().map(|n| n as usize))
                .collect();
            let features: Vec<_> = a
                .features
                .iter()
                .filter(|other| other.id != f.id && other.faces.iter().any(|i| covered.contains(i)))
                .collect();
            let names = features
                .iter()
                .map(|other| crate::wording::feature(other))
                .collect::<Vec<_>>()
                .join(", ");
            let obstruction = if names.is_empty() {
                "an adjacent face".into()
            } else {
                names
            };
            let percentage = o["covered_fraction"]
                .as_f64()
                .map_or_else(|| "unknown".into(), |f| format!("{:.0}%", 100. * f));
            let presentation = if f.parameters.get("bottom").and_then(|v| v.as_str())
                == Some("through")
                && drawn_thru(plan, &f.id)
            {
                "; drawn THRU — check the exit is clear"
            } else {
                ""
            };
            out.push(Problem {
                id: format!("p:hole_exit_obstructed:{}:{i}", f.id),
                code: "hole_exit_obstructed".into(),
                severity: "warning".into(),
                message: format!(
                    "{}: exit about {percentage} covered by {obstruction}{presentation}.",
                    crate::wording::capital(&crate::wording::feature(f))
                ),
                subjects: std::iter::once(f.id.clone())
                    .chain(features.iter().map(|f| f.id.clone()))
                    .collect(),
                refs: covered.iter().map(|i| format!("face:{i}")).collect(),
                ..Default::default()
            });
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::{self, FeatureSpec, PmiMode, Spec};
    use std::path::Path;

    fn fixture() -> std::path::PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/parts/partial_hole_exits.step.gz")
    }
    #[test]
    fn partially_obstructed_exits_are_thru_and_name_the_covering_geometry() {
        let (_, a) = model::analyse_native(&fixture()).unwrap();
        let obstructed: Vec<_> = a
            .features
            .iter()
            .filter(|f| {
                f.parameters
                    .get("exit_obstructions")
                    .and_then(|v| v.as_array())
                    .is_some_and(|v| !v.is_empty())
            })
            .collect();
        assert!(!obstructed.is_empty());
        let p = model::plan_recovered(&a, &Spec::default()).unwrap();
        for f in obstructed {
            assert_eq!(f.parameters["bottom"], "through");
            assert!(drawn_thru(&p, &f.id));
            assert!(
                p.dimensions.iter().any(|d| d.label.contains("THRU")
                    && d.measurements.iter().any(|m| m.feature == f.id)),
                "{}",
                f.id
            );
            assert!(p.problems.iter().any(|w| w.code == "hole_exit_obstructed"
                && w.subjects.contains(&f.id)
                && w.message.contains("exit about")
                && w.message.contains("drawn THRU")
                && !w.message.contains("fraction")
                && !w.refs.is_empty()));
        }
    }

    #[test]
    fn suppressed_or_unstated_holes_only_report_the_obstruction() {
        let (_, a) = model::analyse_native(&fixture()).unwrap();
        let f = a
            .features
            .iter()
            .find(|f| {
                f.kind == "hole"
                    && f.parameters
                        .get("exit_obstructions")
                        .and_then(|v| v.as_array())
                        .is_some_and(|v| !v.is_empty())
            })
            .unwrap();
        let mut suppressed = Spec::default();
        suppressed.features.insert(
            f.id.clone(),
            FeatureSpec {
                suppress: true,
                ..Default::default()
            },
        );
        let plan = model::plan_recovered(&a, &suppressed).unwrap();
        assert!(
            !plan
                .dimensions
                .iter()
                .any(|d| d.measurements.iter().any(|m| m.feature == f.id))
        );
        assert_fact_only(&plan, &f.id);

        let mut strict = Spec {
            pmi: PmiMode::Strict,
            ..Default::default()
        };
        strict.features.insert(
            f.id.clone(),
            FeatureSpec {
                datum: Some("A".into()),
                ..Default::default()
            },
        );
        let plan = model::plan_recovered(&a, &strict).unwrap();
        assert!(!drawn_thru(&plan, &f.id));
        assert_fact_only(&plan, &f.id);

        let mut plan = model::plan_recovered(&a, &Spec::default()).unwrap();
        // The fixture's drawn schedule still states THRU after its callouts are unplaced.
        for d in &mut plan.dimensions {
            d.placed = None;
        }
        assert!(drawn_thru(&plan, &f.id));
        // Without either the callout or its schedule, report only the obstruction.
        plan.hole_table = None;
        plan.hole_tables.clear();
        report(&a, &mut plan);
        assert_fact_only(&plan, &f.id);

        let mut reclassified = model::plan_recovered(&a, &Spec::default()).unwrap();
        reclassified.spec.features.insert(
            f.id.clone(),
            FeatureSpec {
                reclassify: Some(crate::recognition_corrections::Kind::Boss),
                ..Default::default()
            },
        );
        report(&a, &mut reclassified);
        assert_fact_only(&reclassified, &f.id);

        let mut blind = a.clone();
        blind
            .features
            .iter_mut()
            .find(|h| h.id == f.id)
            .unwrap()
            .parameters
            .insert("bottom".into(), serde_json::json!("flat"));
        let mut plan = model::plan_recovered(&a, &Spec::default()).unwrap();
        assert!(drawn_thru(&plan, &f.id));
        report(&blind, &mut plan);
        assert_fact_only(&plan, &f.id);
        blind
            .features
            .iter_mut()
            .find(|h| h.id == f.id)
            .unwrap()
            .parameters
            .remove("bottom");
        report(&blind, &mut plan);
        assert_fact_only(&plan, &f.id);
    }

    fn assert_fact_only(plan: &Plan, id: &str) {
        let warnings: Vec<_> = plan
            .problems
            .iter()
            .filter(|p| p.code == "hole_exit_obstructed" && p.subjects.iter().any(|s| s == id))
            .collect();
        assert!(!warnings.is_empty());
        for p in warnings {
            assert!(p.message.contains("exit about") && !p.refs.is_empty());
            assert!(!p.message.contains("drawn THRU"), "{}", p.message);
        }
    }
}
