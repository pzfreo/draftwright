//! Default nominal display intentionally departs from Python's one-decimal `_fmt`.
//! Tolerance magnitudes and explicit measurement precision have separate policies.
thread_local! {
    // Unrendered placement comparison only. Live plans always leave this scope first.
    static COMPARISON: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
}

/// Authored PMI owns its layout and precision; compact nominal recipes are
/// only a comparison for automatically generated dimensions.
pub(crate) fn geometry_eligible(analysis: &crate::model::Analysis) -> bool {
    analysis
        .recognition
        .as_ref()
        .is_some_and(|r| r.recogniser.starts_with("quiddity-rust "))
        && !authored_pmi(analysis)
}

pub(crate) fn authored_pmi(analysis: &crate::model::Analysis) -> bool {
    matches!(&analysis.pmi, Some(crate::pmi::Reading::Read(report)) if !report.records.is_empty())
}

pub(crate) fn comparison() -> bool {
    COMPARISON.get()
}

pub(crate) fn with_comparison<T>(run: impl FnOnce() -> T) -> T {
    struct Restore(bool);
    impl Drop for Restore {
        fn drop(&mut self) {
            COMPARISON.set(self.0);
        }
    }
    let _restore = Restore(COMPARISON.replace(true));
    run()
}

pub(crate) fn clean(value: f64) -> f64 {
    (value * 1e6).round() / 1e6
}

pub(crate) fn format(value: f64) -> String {
    let value = clean(value);
    if comparison() {
        let rounded = value.round();
        return if (value - rounded).abs() < 1e-6 {
            format!("{}", if rounded == 0. { 0 } else { rounded as i64 })
        } else {
            let text = format!("{value:.1}");
            if text == "-0.0" { "0.0".into() } else { text }
        };
    }
    let places = (0..3)
        .find(|&places| {
            let factor = 10_f64.powi(places as i32);
            ((value * factor).round() / factor - value).abs() <= 1e-6 + 1e-12
        })
        .unwrap_or(3);
    let text = format!("{value:.places$}");
    if places == 3
        && text
            .parse::<f64>()
            .is_ok_and(|n| (n - value).abs() > 1e-6 + 1e-12)
    {
        return if text.parse::<f64>() == Ok(0.) {
            "0.000".into()
        } else {
            text
        };
    }
    let text = if places == 0 {
        text.as_str()
    } else {
        text.trim_end_matches('0').trim_end_matches('.')
    };
    if text == "-0" {
        "0".into()
    } else {
        text.into()
    }
}

/// Refresh on the final live carriers, including notes and table-owned measurements.
pub(crate) fn report(plan: &mut crate::model::Plan) {
    if comparison() {
        return;
    }
    plan.problems.retain(|p| p.code != "nominal_rounded");
    let spec = &plan.spec;
    let mut problems = std::collections::BTreeMap::new();
    let mut check = |id: &str, measurements: &[crate::dims::Measurement], text: &str| {
        for m in measurements {
            let measurement = format!("d:{}/{}", m.feature, m.parameter);
            if spec.precision.contains_key(&measurement)
                || (id
                    .rsplit_once('/')
                    .is_some_and(|(_, role)| role == m.parameter)
                    && spec.precision.contains_key(id))
                || ((id.starts_with("d:authored:") || id.starts_with("d:scheme:"))
                    && spec.precision.contains_key(id))
            {
                continue;
            }
            let units = if m.parameter.contains("angle") {
                crate::units::Units::Mm
            } else {
                spec.units
            };
            let printed = units.format(m.value);
            // Symbolic thread designations and table references can replace a nominal.
            // Report only a numeric term that is actually printed on this carrier.
            let printed_here = text
                .split(|c: char| !c.is_ascii_digit() && c != '.' && c != '-')
                .any(|token| {
                    token == printed
                        || (m.feature.starts_with("f:modelled_thread:")
                            && m.parameter == "thread"
                            && token.parse::<f64>().ok() == printed.parse::<f64>().ok())
                });
            if printed_here
                && printed
                    .parse::<f64>()
                    .is_ok_and(|v| (v - clean(units.from_mm(m.value))).abs() > 1e-6 + 1e-12)
            {
                problems.insert((id.to_string(), measurement.clone()), crate::model::Problem {
                    id: format!("p:nominal_rounded:{id}:{measurement}"),
                    code: "nominal_rounded".into(), severity: "info".into(),
                    message: if spec.units.is_mm() {
                        format!("The dimension {text:?} has modelled nominal {} but prints {printed} at the default three-place limit.", clean(m.value))
                    } else {
                        format!("The dimension {text:?}: modelled nominal {} mm prints {printed} in at the default three-place limit.", clean(m.value))
                    },
                    subjects: vec![id.into()], ..Default::default()
                });
            }
        }
    };
    for d in &plan.dimensions {
        if d.placed.is_some() {
            check(&d.id, &d.measurements, &d.label);
        }
    }
    for n in &plan.notes {
        if n.placed.is_some() {
            check(&n.id, &n.measurements, &n.text);
        }
    }
    for t in plan.hole_table.iter().chain(&plan.hole_tables) {
        let text = t
            .rows
            .iter()
            .flat_map(|r| &r.cells)
            .cloned()
            .collect::<Vec<_>>()
            .join(" ");
        check(&t.id, &t.covered, &text);
        for r in &t.rows {
            check(&t.id, &r.measurements, &r.cells.join(" "));
        }
    }
    if let Some(t) = &plan.radius_table
        && t.table.placed.is_some()
    {
        for (r, cells) in t.rows.iter().zip(t.table.rows.iter().skip(1)) {
            let measurements = [
                ("radius_centre.location.x", r.centre[0]),
                ("radius_centre.location.y", r.centre[1]),
                ("blend.radius", r.radius),
            ]
            .map(|(parameter, value)| crate::dims::Measurement {
                feature: r.feature.clone(),
                parameter: parameter.into(),
                value,
            });
            check(&r.dimension, &measurements, &cells.join(" "));
        }
    }
    if !plan.spec.units.is_mm() && !problems.is_empty() {
        let mut subjects = vec!["sheet".into()];
        let mut refs = Vec::new();
        let mut precision = std::collections::BTreeSet::new();
        for ((id, measurement), problem) in &problems {
            if !subjects.contains(id) {
                subjects.push(id.clone());
            }
            refs.push(problem.message.clone());
            precision.insert(measurement.clone());
        }
        plan.problems.push(crate::model::Problem {
            id: "p:nominal_rounded:sheet".into(), code: "nominal_rounded".into(), severity: "info".into(),
            message: format!("{} dimensions print rounded from non-inch nominals at three decimal inch places. Use millimetres or request four-place precision where needed.", problems.len()),
            subjects, refs,
            fix: vec![crate::items::Op::set("units", serde_json::json!("mm"))],
            fixes: vec![crate::fixes::Fix::new("Print these dimensions to four decimal inch places", precision.into_iter().map(|id| crate::items::Op::set(format!("precision/{}", crate::items::key(&id)), serde_json::json!(4))).collect())],
            ..Default::default()
        });
    } else {
        plan.problems.extend(problems.into_values());
    }
}

#[cfg(test)]
mod tests {
    #[test]
    fn comparison_widths_cannot_escape_into_live_nominals() {
        super::with_comparison(|| {
            assert_eq!(super::format(4.75), "4.8");
            assert_eq!(crate::pmi::fmt(4.75, None), "4.8");
            assert_eq!(crate::lint::coverage::fmt(4.75), "4.75");
        });
        assert_eq!(super::format(4.75), "4.75");
        assert_eq!(crate::pmi::fmt(4.75, None), "4.75");
        assert_eq!(crate::lint::coverage::fmt(4.75), "4.75");
        let _ = std::panic::catch_unwind(|| super::with_comparison(|| panic!("comparison failed")));
        assert_eq!(super::format(4.75), "4.75");
    }

    #[test]
    fn shortest_nominals_remove_noise_without_rounding_hundredths() {
        for (value, text) in [
            (4.75, "4.75"),
            (7.4, "7.4"),
            (12., "12"),
            (247.65000001, "247.65"),
            (247.64999999, "247.65"),
            (1.875, "1.875"),
            (1.23456, "1.235"),
            (4.0001, "4.000"),
            (-0.0001, "0.000"),
            (-0.0000001, "0"),
            (4.750001, "4.75"),
            (-4.75, "-4.75"),
        ] {
            assert_eq!(super::format(value), text);
        }
    }
}
