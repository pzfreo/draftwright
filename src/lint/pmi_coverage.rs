//! PMI coverage (Python's `linting/pmi_coverage.py`, `_lint_pmi`): every AP242 PMI source of the
//! part is on the drawing or reported, and a drawn feature control frame states its source.
//!
//! The requirements are the Analysis's PMI reading ([`Reading`]): its records and the sources it
//! did not read. The evidence is the drawing's marks: a record is drawn when an annotation is named
//! `pmi:<source>` (a feature control frame, datum feature symbol, label or drawing-wide
//! requirement) or claims a measurement of feature `pmi:<source>` (a dimension stating a PMI
//! dimension). Which records lower to a drawing item at all is [`lower`]'s decision, a function of
//! the Analysis alone that drawing the part also takes; lint asks it only to tell a record not
//! lowered from one lowered but not drawn, never for what to count. In the order Python reports
//! them:
//!
//! - `pmi_not_extracted`: the reading was refused (`AP242 PMI could not be inventoried`), or a
//!   source was not read or read in part (`partially_extracted`: a record with blockers). An
//!   error, as Python's `annotate` mode makes it; a source of no draftwright category (a shape
//!   aspect, property or standard haecceity reports, which draftwright's census does not hold)
//!   is info, as the plan reports it. A material the title block states is drawn, not reported.
//! - `pmi_not_lowered`: a record that is not drawn and that [`lower`] does not lower: its reading's
//!   blockers, its modifiers or its attachment stop it, a surface or common label has no proven
//!   site, a drawing-wide requirement ([`sheet_requirements`]) is ambiguous or not a supported
//!   default, a thread or knurl no one feature owns ([`manufacturing`]: its text disagrees with
//!   its cylinder, or no feature or several prove it); a dimension that does not lower
//!   ([`dimension`]). Error; a manufacturing requirement of
//!   a kind draftwright does not support is a warning.
//! - `pmi_not_rendered`: a record [`lower`] or [`sheet_requirements`] lowers, a thread or knurl
//!   [`manufacturing`] gives to its owner (its owner's callout claims it), or a dimension that
//!   lowers, that no annotation draws or states. Error.
//! - `pmi_value_mismatch`: a frame whose text runs do not state its source's magnitude (one
//!   number, within half the display's 13th significant digit, as Python allows), its zone
//!   (`⌀`, `S⌀` or none) and its datum letters (repeats removed). Error.
//! - `pmi_source_unknown`: an annotation that claims a source the reading does not hold (a
//!   fabricated provenance); `pmi_unreconciled`: one that claims a source when the part has no
//!   PMI reading to check it against (warning).
//!
//! - `step_material_ambiguous`: the parts state more than one material, so none can be the
//!   title block's; `step_material_unavailable`: the one material stated is one part's of
//!   several (draftwright's 'not owned by the single shaped source product definition').
//!   Warnings.
//!
//! Python's `pmi_present_but_ignored` is not-applicable: the port has no switch that ignores a
//! part's PMI. Source-site fidelity is checked by [`source_sites`] against the finished leader
//! and independently recorded face attachments, including census-only sources with no typed
//! record. Edge-note and face-finish text runs are checked independently of their formatter.
//! Manufacturing references are checked against complete source-matched rendered rows by
//! [`manufacturing_references`]. Filled glyph-path text checks remain pending. The title block's
//! material and general tolerance against the file (`step_material_mismatch`, `_disagreement`,
//! `step_general_tolerance_*`) depend on whether the caller authored the field.

use std::collections::BTreeSet;

use super::{LintIssue, Mark, Severity};
use crate::model::Analysis;
use crate::output::{Drawing, Kind};
use crate::pmi::{Category, Identities, Outcome, Reading, Record, Report};
use crate::pmi_lowering::{lower, manufacturing, sheet_requirements};

/// Supported manufacturing requirements, including part semantic text.
const MANUFACTURING: [&str; 8] = [
    "external_thread",
    "internal_thread",
    "knurl",
    "edge_condition",
    "general_note",
    "surface_treatment",
    "heat_treatment",
    "surface_finish",
];

fn issue(code: &str, subject: &str, severity: Severity, message: String) -> LintIssue {
    LintIssue {
        sheet: None,
        id: format!("l:{code}:{subject}"),
        code: code.into(),
        severity,
        message,
        at: None,
        view: None,
        subjects: Vec::new(),
        requirements: Vec::new(),
        measurements: Vec::new(),
        sources: Vec::new(),
        stage: None,
        annotation: None,
        related: Vec::new(),
        evidence: None,
        fix: None,
    }
}

/// A source's issue: named by it and implicating it.
fn sourced(code: &str, source: &str, severity: Severity, message: String) -> LintIssue {
    LintIssue {
        sources: vec![source.to_string()],
        ..issue(code, source, severity, message)
    }
}

fn category_name(c: Category) -> &'static str {
    match c {
        Category::Dimension => "dimension",
        Category::GeometricTolerance => "geometric_tolerance",
        Category::Datum => "datum",
        Category::ManufacturingRequirement => "manufacturing_requirement",
        Category::SurfaceLabel => "surface_label",
        Category::Other => "other",
    }
}

/// The PMI sources the marks draw or state: each `pmi:<id>` annotation name and claim, read as
/// the source of the record whose item ID it is (a record's ID is its semantic identity,
/// [`crate::pmi::Report::identities`]); any other (a material's census source, an ID no record
/// has) as it is.
fn drawn<'a>(marks: &'a [Mark], ids: &'a Identities) -> BTreeSet<&'a str> {
    let source = |name: &'a str| {
        name.strip_prefix("pmi:")
            .map(|s| ids.source(name).unwrap_or(s))
    };
    let mut out = BTreeSet::new();
    for m in marks {
        out.extend(source(&m.name));
        for c in &m.claims {
            out.extend(source(&c.feature));
        }
    }
    out
}

/// The PMI coverage findings on a drawing whose annotations are *marks*.
pub fn coverage(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let ids = match &analysis.pmi {
        Some(Reading::Read(r)) => r.identities(),
        _ => Identities::default(),
    };
    let on_sheet = drawn(marks, &ids);
    let report = match &analysis.pmi {
        Some(Reading::Read(r)) => r,
        Some(Reading::Refused(why)) => {
            let mut finding = issue(
                "pmi_not_extracted",
                "part",
                Severity::Error,
                format!(
                    "STEP annotations could not be read: {}. Add the requirements to the Spec and review them manually.",
                    crate::pmi::reader_reason(why)
                ),
            );
            finding.evidence = Some(why.clone());
            let mut out = vec![finding];
            out.extend(unreconciled(&on_sheet));
            return out;
        }
        None => return unreconciled(&on_sheet),
    };
    // Suppression provenance is explicitly separate from drawn ink and measurements.
    let omitted: BTreeSet<&str> = marks
        .iter()
        .filter_map(|m| m.name.strip_prefix("suppressed:"))
        .filter_map(|id| ids.source(id))
        .collect();
    let mut out = Vec::new();
    for r in report.records.iter().filter(|r| {
        r.category == Category::Datum
            && omitted.contains(r.source_id.as_str())
            && !on_sheet.contains(r.source_id.as_str())
    }) {
        out.push(sourced("pmi_datum_suppressed", &r.source_id, Severity::Info,
            format!("Datum {} has an explicit symbol omission recorded in the drawing; no symbol is drawn.", r.label)));
    }
    // A material the title block states (its cell's run named `pmi:material:<part>:0`) is on the
    // drawing, as draftwright's title block accounts for its material fact.
    for s in report.sources.iter().filter(|s| {
        matches!(
            s.outcome,
            Outcome::NotExtracted | Outcome::PartiallyExtracted
        ) && !(s.id.starts_with("material:") && on_sheet.contains(s.id.as_str()))
    }) {
        let severity = if s.category == Category::Other {
            Severity::Info
        } else {
            Severity::Error
        };
        let mut finding = sourced(
            "pmi_not_extracted",
            &s.id,
            severity,
            format!(
                "A STEP {} annotation is not fully represented on the drawing. Add the requirement to the Spec and review it manually.",
                s.id.split(':')
                    .next()
                    .unwrap_or("unknown")
                    .replace('_', " ")
            ),
        );
        finding.evidence = Some(format!(
            "AP242 {} {} was discovered but not extracted: {}",
            category_name(s.category),
            s.id,
            s.reason
        ));
        out.push(finding);
    }
    // Lowering does not depend on which views are drawn, only where an item goes. A drawing-wide
    // requirement lowers to the sheet (the title block, the finish symbol, a general note).
    let (items, mut why_not) = lower(analysis, &|_| true);
    let (sheet, more) = sheet_requirements(analysis);
    why_not.extend(more);
    let mut lowered: BTreeSet<&str> = items.iter().map(|l| l.source.as_str()).collect();
    lowered.extend(sheet.iter().map(|r| r.source.as_str()));
    // A thread or knurl given to its owner lowers to that owner's callout.
    let manufacturing = manufacturing(analysis);
    lowered.extend(manufacturing.owned.iter().map(|o| o.source.as_str()));
    lowered.extend(
        report
            .records
            .iter()
            .filter(|r| r.category == Category::Dimension && dimension(r).is_ok())
            .map(|r| r.source_id.as_str()),
    );
    // A record whose identity another shares has no drawing item to lower to.
    lowered.retain(|s| ids.ids.contains_key(*s));
    for r in &report.records {
        let id = r.source_id.as_str();
        if crate::pmi_lowering::no_part_requirement(r)
            || on_sheet.contains(id)
            || (r.category == Category::Datum && omitted.contains(id))
            || lowered.contains(id)
        {
            continue;
        }
        let refused = why_not
            .iter()
            .find(|p| p.refs.first().is_some_and(|s| s == id));
        let reason = match dimension(r) {
            Err(why) if r.category == Category::Dimension && r.kind != "common_label" => why,
            _ => refused.map_or_else(
                || {
                    if r.blockers.is_empty() {
                        "it is not lowered to a drawing item".to_string()
                    } else {
                        r.blockers.join("; ")
                    }
                },
                |p| p.message.clone(),
            ),
        };
        // A common label the plan refuses as a warning (a value-less radius, #214) stays one.
        let severity = if (r.category == Category::ManufacturingRequirement
            && !MANUFACTURING.contains(&r.kind.as_str()))
            || (r.kind == "common_label" && refused.is_some_and(|p| p.severity == "warning"))
        {
            Severity::Warning
        } else {
            Severity::Error
        };
        let explanation = crate::pmi::public_reason(&reason)
            .filter(|why| !why.is_empty())
            .map_or_else(|| ".".to_string(), |why| format!(": {why}."));
        let mut finding = sourced(
            "pmi_not_lowered",
            id,
            severity,
            format!(
                "The {} {} annotation could not be drawn{explanation} Review its requirement and attachment.",
                if id.starts_with("spec:") {
                    "Spec"
                } else if crate::pmi::specify_written(report, id) {
                    "Specify"
                } else {
                    "STEP"
                },
                r.kind.replace('_', " ")
            ),
        );
        finding.evidence = Some(reason);
        out.push(finding);
    }
    for r in &report.records {
        let id = r.source_id.as_str();
        if !crate::pmi_lowering::no_part_requirement(r)
            && lowered.contains(id)
            && !on_sheet.contains(id)
            && !(r.category == Category::Datum && omitted.contains(id))
            // Specify's general-tolerance dimensions are drawn as the plan dimensions that
            // geometry, not on top of it (#265).
            && !crate::pmi::specify_general(report, r)
        {
            out.push(sourced(
                "pmi_not_rendered",
                id,
                Severity::Error,
                format!(
                    "AP242 source {id} reached a drawing item ({}) but produced no annotation",
                    ids.ids[id]
                ),
            ));
        }
    }
    for m in marks {
        let Some(id) = m.name.strip_prefix("pmi:") else {
            continue;
        };
        let id = ids.source(&m.name).unwrap_or(id);
        if let Some(r) = report.records.iter().find(|r| {
            r.source_id == id
                && r.category == Category::GeometricTolerance
                && r.value.is_some_and(|v| v > 0.0)
        }) && let Some(found) = mismatch(r, &m.text)
        {
            let mut i = sourced("pmi_value_mismatch", id, Severity::Error, found);
            i.id = format!("l:pmi_value_mismatch:{}", m.name);
            i.annotation = Some(m.name.clone());
            out.push(i);
        }
    }
    for r in report
        .records
        .iter()
        .filter(|r| r.category == Category::Dimension && on_sheet.contains(r.source_id.as_str()))
    {
        let Some(g) = &r.geometry else {
            continue;
        };
        if g.targets.is_empty() {
            continue;
        }
        let measured = match (r.kind.as_str(), g.targets.as_slice()) {
            ("diameter" | "radius", [Some(t)]) => t
                .radius
                .map(|v| if r.kind == "diameter" { 2.0 * v } else { v }),
            ("linear" | "thickness", [Some(a), Some(b)]) => {
                crate::pmi_lowering::derived_stations(a, b, [0.0; 3])
                    .ok()
                    .map(|s| {
                        (0..3)
                            .map(|k| (s[1][k] - s[0][k]).powi(2))
                            .sum::<f64>()
                            .sqrt()
                    })
            }
            _ => None,
        };
        if measured
            .zip(r.value)
            .is_none_or(|(m, v)| (m - v).abs() > 0.01_f64.max(v.abs() * 5e-4))
        {
            out.push(sourced(
                "pmi_value_mismatch",
                &r.source_id,
                Severity::Error,
                format!(
                    "AP242 source {} nominal {:?} disagrees with its derived geometry {:?} mm",
                    r.source_id, r.value, measured
                ),
            ));
        }
        if let Some(q) = ["MAX", "MIN", "AVG"]
            .into_iter()
            .find(|q| r.label.ends_with(&format!(" {q}")))
        {
            for mark in marks.iter().filter(|m| {
                m.claims
                    .iter()
                    .any(|c| ids.source(&c.feature) == Some(r.source_id.as_str()))
            }) {
                if !mark.text.split_whitespace().any(|t| t == q) {
                    let mut i = sourced(
                        "pmi_value_mismatch",
                        &r.source_id,
                        Severity::Error,
                        format!("AP242 source {} is missing its {q} qualifier", r.source_id),
                    );
                    i.annotation = Some(mark.name.clone());
                    out.push(i);
                }
            }
        }
    }
    out.extend(manufacturing_text(report, marks, &ids));
    out.extend(materials(report));
    let known: BTreeSet<&str> = report
        .records
        .iter()
        .map(|r| r.source_id.as_str())
        .chain(report.sources.iter().map(|s| s.id.as_str()))
        .collect();
    for id in on_sheet.iter().filter(|s| !known.contains(*s)) {
        out.push(sourced(
            "pmi_source_unknown",
            id,
            Severity::Error,
            format!(
                "a declaration claims AP242 source {id}, which the reconciled document does not \
                 contain — the provenance is fabricated, not merely unmatched"
            ),
        ));
    }
    crate::pmi::graphical::attach_to_lint(report, &on_sheet, &mut out);
    out
}

/// Source-scoped manufacturing text, read from the source itself, independently of the
/// lowering/row formatter. Whitespace may change when notes wrap; words and parameters may not.
/// This checks the text runs. The independent audit of the filled glyph paths remains pending.
fn manufacturing_text(report: &Report, marks: &[Mark], ids: &Identities) -> Vec<LintIssue> {
    let mut out = Vec::new();
    for m in marks {
        let Some(source) = m.name.strip_prefix("pmi:") else {
            continue;
        };
        let source = ids.source(&m.name).unwrap_or(source);
        let Some(r) = report
            .records
            .iter()
            .find(|r| r.source_id == source && r.category == Category::ManufacturingRequirement)
        else {
            continue;
        };
        let words: Vec<&str> = m.text.split_whitespace().collect();
        let states = match r.kind.as_str() {
            "edge_condition" | "general_note" | "surface_treatment" | "heat_treatment" => {
                // The general notes block numbers each note's first row. Its annotation owns
                // only that note's runs, including every wrapped continuation row.
                let note = match words.first().and_then(|s| s.parse::<usize>().ok()) {
                    Some(n) if n > 0 => &words[1..],
                    _ => &words[..],
                };
                // An assembly's note only some parts state ends naming them (`(PART PIN)`).
                let note = match note.iter().rposition(|w| *w == "(PART" || *w == "(PARTS") {
                    Some(k) if note.last().is_some_and(|w| w.ends_with(')')) => &note[..k],
                    _ => note,
                };
                let source = r.label.split_whitespace().collect::<Vec<_>>();
                let qualified = match r.kind.as_str() {
                    "surface_treatment" => note.strip_prefix(&["Coating:"]),
                    "heat_treatment" => note.strip_prefix(&["Heat", "treatment:"]),
                    _ => None,
                };
                !crate::pmi_lowering::no_part_requirement(r)
                    && !note.is_empty()
                    && (note == source || qualified.is_some_and(|text| text == source))
            }
            "surface_finish" => {
                let parameters: Vec<_> = r
                    .fields
                    .iter()
                    .filter_map(|(name, field)| match field {
                        crate::pmi::Field::Length(mm) => Some((name.as_str(), mm * 1000.0)),
                        _ => None,
                    })
                    .collect();
                // ISO 1302's parameter labels carry values in micrometres. An arbitrary
                // number somewhere in a label cannot stand for the named parameter. Accept
                // the exact source value or its declared three-significant-digit display,
                // independently of the lowering formatter, never an interval of nearby values.
                let mut printed = words.iter().copied();
                let states = !parameters.is_empty()
                    && parameters.iter().all(|(name, expected)| {
                        name.split_whitespace()
                            .all(|word| printed.next() == Some(word))
                            && expected.is_finite()
                            && printed
                                .next()
                                .and_then(|word| word.parse::<f64>().ok())
                                .is_some_and(|value| {
                                    value.is_finite()
                                        && (value == *expected
                                            || format!("{expected:.2e}")
                                                .parse::<f64>()
                                                .is_ok_and(|display| value == display))
                                })
                    });
                states && printed.next().is_none()
            }
            _ => continue,
        };
        if !states {
            let mut i = sourced(
                "pmi_source_text_mismatch",
                source,
                Severity::Error,
                format!(
                    "{} does not state AP242 {} {} as {:?}",
                    m.name,
                    r.kind.replace('_', " "),
                    source,
                    r.label
                ),
            );
            i.id = format!("l:pmi_source_text_mismatch:{}", m.name);
            i.annotation = Some(m.name.clone());
            i.view = m.view.clone();
            out.push(i);
        }
    }
    out
}

/// Whether dimension record *r* lowers to a drawing item (draftwright's typed dimension), or why
/// not: a function of the record alone. The kinds stage 2b (`dims::pmi_dimensions`) draws, a length
/// along x, y or z between two proven stations, a diameter or radius seen end-on or an angle a view
/// shows true, lower; a record its reading or its faces block, a common label (which [`lower`]
/// lowers as a label), an angle no view shows true and an oblique or curved quantity do not
/// (draftwright checks the faces when it extracts, so it says `pmi_not_extracted` where the port's
/// reading holds the record and lowering refuses it). Where a lowered dimension then goes, a model
/// dimension that states it or its own, is the drawing's; one the drawing does not state is
/// `pmi_not_rendered`.
fn dimension(r: &Record) -> Result<(), String> {
    if !r.blockers.is_empty() {
        return Err(r.blockers.join("; "));
    }
    if r.kind == "common_label" {
        return Err("it is a common label, text on its faces, which lowers as a label".into());
    }
    let Some(g) = &r.geometry else {
        return Err("its attachment was not derived".into());
    };
    if !g.reasons.is_empty() {
        return Err(g.reasons.join("; "));
    }
    if r.value.is_none() {
        return Err("the file states no nominal value".into());
    }
    match (r.kind.as_str(), &g.axis) {
        ("linear" | "thickness" | "diameter" | "radius", Some(_)) => Ok(()),
        ("linear" | "thickness", None) => {
            match g.stations.as_slice() {
                [a,b] if crate::pmi_lowering::projection_view([0,1,2].map(|k| b[k]-a[k])).is_some() => Ok(()),
                _ => Err("its stations have no proven span in a principal projection plane".into()),
            }
        },
        ("diameter" | "radius", None) => Err(
            "its axis is not along x, y or z; the port draws a diameter or radius seen end-on"
                .into(),
        ),
        // As the plan's `pmi_dimensions` decides: drawn where a view shows the angle true.
        ("angular", _) => match g.angle.as_ref().map(|a| a.principal()) {
            Some(Some(_)) => Ok(()),
            Some(None) => Err("supported angular ink requires explicit coplanar rays in a principal view: the plane of its sides is oblique to every view, so none shows the angle true".into()),
            None => Err("the sides of its angle were not derived (analyse the part again)".into()),
        },
        (kind, _) => Err(format!(
            "the port draws no {} dimension",
            kind.replace('_', " ")
        )),
    }
}

/// Python's `lint_step_title_defaults`, as far as the reading decides it: whether the file
/// states one material a title block may take. Several are ambiguous; one stated by one part of
/// several is not the drawing's.
fn materials(report: &Report) -> Vec<LintIssue> {
    match report.materials.as_slice() {
        [] => Vec::new(),
        [m] if !report.owns(m) => vec![sourced(
            "step_material_unavailable",
            &m.source_id,
            Severity::Warning,
            format!(
                "STEP material {} is unusable: material is not owned by the single shaped source \
                 product definition (it is part {}'s, of {} parts)",
                m.source_id,
                m.part,
                report.parts.len()
            ),
        )],
        [m] if m.designation.trim().is_empty() => vec![sourced(
            "step_material_unavailable",
            &m.source_id,
            Severity::Warning,
            format!(
                "STEP material {} is unusable: material designation is empty",
                m.source_id
            ),
        )],
        [_] => Vec::new(),
        several => vec![LintIssue {
            sources: several.iter().map(|m| m.source_id.clone()).collect(),
            ..issue(
                "step_material_ambiguous",
                "part",
                Severity::Warning,
                "STEP contains multiple material properties; no material was selected".into(),
            )
        }],
    }
}

/// Annotations claiming PMI sources on a part with no PMI reading to check them against.
fn unreconciled(on_sheet: &BTreeSet<&str>) -> Vec<LintIssue> {
    if on_sheet.is_empty() {
        return Vec::new();
    }
    vec![LintIssue {
        sources: on_sheet.iter().map(|s| s.to_string()).collect(),
        ..issue(
            "pmi_unreconciled",
            "part",
            Severity::Warning,
            format!(
                "this drawing declares {} AP242 source reference(s), and no AP242 census was \
                 available to check them — the claims are unverified, not satisfied",
                on_sheet.len()
            ),
        )
    }]
}

/// What a frame's compartments *text* (its runs, joined by spaces: `⌖ ⌀0.05Ⓜ A B`) state
/// that differs from record *r*, or none when they state its magnitude, zone and datums.
fn mismatch(r: &Record, text: &str) -> Option<String> {
    let expected = r.value?;
    let zone_of = |r: &Record| {
        ["spherical_diameter_zone", "diameter_zone"]
            .into_iter()
            .find(|z| r.gtol_modifiers.iter().any(|m| m == z))
    };
    let expected_zone = zone_of(r);
    let mut datums: Vec<&str> = Vec::new();
    for d in &r.datum_refs {
        if !datums.contains(&d.as_str()) {
            datums.push(d);
        }
    }
    // The compartments after the characteristic's symbol: one states the zone, the magnitude
    // and the material condition, the rest a datum letter each. A count of the features the
    // frame applies to (`4×`, over a frame on a leader to one of them) comes first.
    let mut cells = text.split_whitespace().peekable();
    cells.next_if(|c| {
        c.strip_suffix('×')
            .is_some_and(|n| !n.is_empty() && n.chars().all(|d| d.is_ascii_digit()))
    });
    let mut values = Vec::new();
    let mut zones = Vec::new();
    let mut letters = Vec::new();
    for cell in cells.skip(1) {
        let (zone, rest) = if let Some(rest) = cell.strip_prefix("S⌀") {
            (Some("spherical_diameter_zone"), rest)
        } else if let Some(rest) = cell.strip_prefix('⌀') {
            (Some("diameter_zone"), rest)
        } else {
            (None, cell)
        };
        let number = rest.trim_end_matches(['Ⓜ', 'Ⓛ']);
        match number.parse::<f64>() {
            Ok(v) if v.is_finite() => {
                values.push(v);
                zones.push(zone);
            }
            _ => letters.push(cell),
        }
    }
    // Python's display bound: half a unit of the 13th significant digit of the source value.
    let bound = 0.5 * 10f64.powi(expected.abs().log10().floor() as i32 - 12);
    let zone = zones.first().copied().flatten();
    if values.len() == 1
        && (values[0] - expected).abs() <= bound
        && zones.len() == 1
        && zone == expected_zone
        && letters == datums
    {
        return None;
    }
    let stated = if values.is_empty() {
        "no numeric value".to_string()
    } else {
        values
            .iter()
            .map(|v| format!("{v}"))
            .collect::<Vec<_>>()
            .join(", ")
    };
    let list = |v: &[&str]| {
        if v.is_empty() {
            "none".to_string()
        } else {
            v.join(" ")
        }
    };
    Some(format!(
        "pmi:{} states {stated} with zone {} and datums {} for AP242 source {}; source tolerance \
         is {expected} with zone {} and datums {}",
        r.source_id,
        zone.unwrap_or("none"),
        list(&letters),
        r.source_id,
        expected_zone.unwrap_or("none"),
        list(&datums)
    ))
}

/// Source sites are checked against the finished leader and the source faces, never the Plan.
/// A planar finish may slide along its face's edge line; a cylinder uses either silhouette, or,
/// where its face proves none, any point inside its outline seen head-on.
pub(super) fn source_sites(
    analysis: &Analysis,
    drawing: &crate::output::Drawing,
) -> Vec<LintIssue> {
    use crate::output::Kind;
    let Some(Reading::Read(report)) = &analysis.pmi else {
        return vec![];
    };
    let ids = report.identities();
    let mut out = Vec::new();
    for a in &drawing.annotations {
        let Some(source) = ids.source(&a.name).or_else(|| a.name.strip_prefix("pmi:")) else {
            continue;
        };
        let ink = drawing.sheet.items.iter().find(|i| i.name == a.name);
        let text = a
            .runs
            .iter()
            .filter_map(|&i| drawing.text.get(i))
            .map(|r| r.text.as_str())
            .collect::<Vec<_>>()
            .join(" ");
        let finish_carrier = a.view.is_some()
            && ink.is_some_and(|i| i.kind == Kind::Gdt)
            && ["Ra ", "Rz ", "Rq "]
                .iter()
                .any(|prefix| text.starts_with(prefix));
        let general_note = a.view.is_none()
            && drawing.sheet.items.iter().any(|i| {
                i.kind == Kind::Notes
                    && i.name == "general_notes"
                    && a.runs
                        .iter()
                        .filter_map(|&j| drawing.text.get(j))
                        .any(|run| {
                            (0..2).all(|k| {
                                i.ink[k] - 0.05 <= run.at[k] && run.at[k] <= i.ink[k + 2] + 0.05
                            })
                        })
            });
        let Some(r) = report.records.iter().find(|r| r.source_id == source) else {
            if (finish_carrier || general_note) && report.sources.iter().any(|s| s.id == source) {
                let mut finding = sourced(
                    "pmi_source_site_mismatch",
                    source,
                    Severity::Error,
                    format!(
                        "{} claims source {source}, whose census entry has no resolved face-finish or document-note record",
                        a.name
                    ),
                );
                finding.id = format!("l:pmi_source_site_mismatch:{}", a.name);
                finding.annotation = Some(a.name.clone());
                finding.view = a.view.clone();
                out.push(finding);
            }
            continue;
        };
        let valid = if r.kind == "surface_finish" || finish_carrier {
            r.kind == "surface_finish"
                && a.view.is_some()
                && ink.is_some_and(|i| i.kind == Kind::Gdt)
                && (|| {
                    let tip = ink?.lines.first()?.first()?;
                    let frame = drawing
                        .sheet
                        .frames
                        .get(a.view.as_deref()?.trim_start_matches("v:"))?;
                    let view = haecceity::hlr::View::new(frame.direction, frame.up)?;
                    let [lo, hi] = analysis.envelope;
                    let centre = view.map([0, 1, 2].map(|k| 0.5 * (lo[k] + hi[k])));
                    let page = |p| {
                        let q = view.map(p);
                        let q = frame.broken.as_ref().map_or(q, |b| b.map(q));
                        [0, 1].map(|k| frame.at[k] + frame.scale * (q[k] - centre[k]))
                    };
                    Some(
                        r.faces
                            .iter()
                            .filter_map(|&f| analysis.face_attachments.get(f))
                            .any(|f| {
                                let (Some(b), Some(axis)) = (f.bounds, f.axis) else {
                                    return false;
                                };
                                // The source surface must be seen edge-on, as its carrier promises.
                                let end_on = view.toward[axis].abs() > 1.0 - 1e-6;
                                let mut bounds = [
                                    f64::INFINITY,
                                    f64::INFINITY,
                                    f64::NEG_INFINITY,
                                    f64::NEG_INFINITY,
                                ];
                                for mask in 0..8 {
                                    let q =
                                        page([0, 1, 2].map(|k| {
                                            b[k + if mask & (1 << k) == 0 { 0 } else { 3 }]
                                        }));
                                    for k in 0..2 {
                                        bounds[k] = bounds[k].min(q[k]);
                                        bounds[k + 2] = bounds[k + 2].max(q[k]);
                                    }
                                }
                                let inside = (0..2).all(|k| {
                                    bounds[k] - 0.05 <= tip[k] && tip[k] <= bounds[k + 2] + 0.05
                                });
                                match f.surface.as_deref() {
                                    Some("plane") => {
                                        !end_on
                                            && inside
                                            && (0..2).any(|k| {
                                                bounds[k + 2] - bounds[k] <= 0.05
                                                    && (tip[k] - bounds[k]).abs() <= 0.05
                                            })
                                    }
                                    Some("cylinder") => {
                                        let direction =
                                            view.map([0, 1, 2].map(|k| f64::from(k == axis)));
                                        let across = if direction[0].abs() > direction[1].abs() {
                                            1
                                        } else {
                                            0
                                        };
                                        // A cylinder with no proven silhouette is
                                        // reached head-on, its leader's dot inside it.
                                        !end_on
                                            && inside
                                            && (!f.face_on.is_empty()
                                                || [bounds[across], bounds[across + 2]]
                                                    .iter()
                                                    .any(|v| (tip[across] - v).abs() <= 0.05))
                                    }
                                    _ => false,
                                }
                            }),
                    )
                })()
                .unwrap_or(false)
        } else if general_note {
            matches!(
                r.kind.as_str(),
                "edge_condition"
                    | "general_note"
                    | "surface_treatment"
                    | "heat_treatment"
                    | "datum_scheme"
                    | "model_representation"
            )
        } else {
            true
        };
        if !valid {
            let mut finding = sourced(
                "pmi_source_site_mismatch",
                source,
                Severity::Error,
                format!(
                    "{} is not anchored to its referenced AP242 {} source {source}",
                    a.name, r.kind
                ),
            );
            finding.id = format!("l:pmi_source_site_mismatch:{}", a.name);
            finding.annotation = Some(a.name.clone());
            finding.view = a.view.clone();
            out.push(finding);
        }
    }
    out
}

// Read designation and role-bound values from all source-bound leader/note runs.
// Numeric fields are compared with the parsed source, never with generated label text.
pub(super) fn thread_text_states(t: &crate::pmi_lowering::Thread, text: &str) -> bool {
    let words: Vec<_> = text
        .split_whitespace()
        .map(|s| s.trim_end_matches([';', ',']))
        .collect();
    let equal = |s: &str, value: f64| {
        s.trim_end_matches([';', ','])
            .parse::<f64>()
            .is_ok_and(|v| (v - value).abs() < 1e-6)
    };
    let designation = words.iter().enumerate().any(|(i, word)| {
        if !word.starts_with('M') {
            return false;
        }
        (1..=5).filter(|n| i + n <= words.len()).any(|n| {
            let s = words[i..i + n].join(" ");
            let s = s.trim_end_matches([';', ',']);
            t.designation_matches(s)
        })
    });
    if !designation {
        return false;
    }
    if let Some(depth) = t.minimum_full_thread {
        let full = words
            .windows(4)
            .any(|w| equal(w[0], depth) && w[1..] == ["MIN", "FULL", "THREAD"])
            || words[..words
                .iter()
                .position(|w| w.starts_with(['⌀', 'ø', 'Ø']))
                .unwrap_or(words.len())]
                .windows(2)
                .any(|w| w[0] == "↧" && equal(w[1], depth));
        if !full {
            return false;
        }
    }
    if t.through
        && !words
            .iter()
            .any(|w| w.trim_end_matches([';', ',']) == "THRU")
    {
        return false;
    }
    if !t.internal {
        return true;
    }
    let conventional_through = t.through && t.conventional_drill();
    if conventional_through {
        return true;
    }
    let Some(drill) = t.drill_diameter else {
        return true;
    };
    words.iter().enumerate().any(|(i, word)| {
        let Some(size) = word.strip_prefix(['⌀', 'ø', 'Ø']) else {
            return false;
        };
        if !equal(size, drill) {
            return false;
        }
        match t.drill_depth {
            Some(depth) => words[i + 1..]
                .split(|w| w.starts_with(['⌀', 'ø', 'Ø']))
                .next()
                .unwrap_or(&[])
                .windows(2)
                .any(|w| w[0] == "↧" && equal(w[1], depth)),
            None => true,
        }
    })
}

/// A short manufacturing leader needs exactly one complete, source-matched rendered row.
/// Read only the drawing and the source reading; searchable text in an unrelated table
/// cannot supply the requirement or borrow another source's reference.
pub fn manufacturing_references(analysis: &Analysis, drawing: &Drawing) -> Vec<LintIssue> {
    let manufacturing = manufacturing(analysis);
    let marks = super::marks(drawing);
    let table_present = drawing
        .sheet
        .items
        .iter()
        .any(|i| i.name == "manufacturing_requirements" && i.kind == Kind::Table);
    let normal = |s: &str| s.split_whitespace().collect::<Vec<_>>().join(" ");
    let headers: Vec<_> = marks
        .iter()
        .filter(|m| m.name == "manufacturing_requirements_header")
        .collect();
    let header_complete =
        headers.len() == 1 && normal(&headers[0].text) == "REF MANUFACTURING REQUIREMENT";
    let mut issues = Vec::new();
    for o in &manufacturing.owned {
        let related: Vec<_> = marks
            .iter()
            .filter(|m| {
                m.claims
                    .iter()
                    .any(|c| c.feature == o.id && c.parameter == o.aspect)
            })
            .collect();
        if related.is_empty() {
            continue;
        }
        let mut text = related
            .iter()
            .map(|m| m.text.as_str())
            .collect::<Vec<_>>()
            .join(" ");
        for a in drawing.annotations.iter().filter(|a| {
            a.name.starts_with("manufacturing_requirements/")
                && a.approved_claims.len() == 1
                && a.approved_claims[0].feature == o.id
                && a.approved_claims[0].parameter == o.aspect
        }) {
            text.push(' ');
            text += &super::schedule_evidence::text(drawing, a);
        }
        let states = if let Some(t) = o.thread() {
            thread_text_states(t, &text)
        } else if let Some((pattern, pitch, maximum)) = o.knurl_values() {
            let words: Vec<_> = text
                .split_whitespace()
                .map(|w| w.trim_end_matches([';', ',']))
                .collect();
            let same = |s: &str, expected: f64| {
                s.parse::<f64>().is_ok_and(|v| (v - expected).abs() < 1e-6)
            };
            words.windows(3).any(|w| {
                w[0] == pattern.to_uppercase()
                    && w[1] == "KNURL"
                    && w[2].strip_prefix('P').is_some_and(|s| same(s, pitch))
            }) && maximum.is_none_or(|d| {
                words.windows(4).any(|w| {
                    w[0].strip_prefix(['ø', '⌀', 'Ø'])
                        .is_some_and(|s| same(s, d))
                        && w[1..] == ["MAX", "AFTER", "KNURL"]
                })
            })
        } else {
            true
        };
        if !states {
            let mut finding = issue(
                "pmi_source_text_mismatch",
                &o.id,
                Severity::Error,
                format!(
                    "The rendered manufacturing callout and its note do not state the source designation, pitch, depth or diameter values for {}",
                    o.source
                ),
            );
            finding.sources = vec![o.source.clone()];
            finding.annotation = related.first().map(|m| m.name.clone());
            issues.push(finding);
        }
    }
    for leader in marks
        .iter()
        .filter(|m| !m.name.starts_with("manufacturing_requirements/"))
    {
        let owned: Vec<_> = manufacturing
            .owned
            .iter()
            .filter(|o| {
                leader
                    .claims
                    .iter()
                    .any(|c| c.feature == o.id && c.parameter == o.aspect)
            })
            .collect();
        if owned.is_empty() {
            continue;
        }
        let words: Vec<_> = leader.text.split_whitespace().collect();
        for words in words.windows(3).filter(|w| w[0] == "SEE" && w[1] == "MFG") {
            // Python's reference regex ends at a word boundary, so punctuation after the
            // tag (including a full stop) must not hide an unresolved reference.
            let end = words[2]
                .find(|c: char| !c.is_ascii_digit())
                .unwrap_or(words[2].len());
            let number = &words[2][..end];
            if words[2][end..].starts_with(|c: char| c.is_alphanumeric() || c == '_')
                || number.is_empty()
                || number.starts_with('0')
                || !number.bytes().all(|c| c.is_ascii_digit())
            {
                continue;
            }
            let tag = format!("MFG {number}");
            let name = format!("manufacturing_requirements/{tag}");
            let rows: Vec<_> = drawing
                .annotations
                .iter()
                .filter(|m| m.name == name)
                .collect();
            let complete = table_present
                && header_complete
                && rows.len() == 1
                && owned.iter().any(|o| {
                    rows[0].approved_claims.len() == 1
                        && rows[0].approved_claims[0].feature == o.id
                        && rows[0].approved_claims[0].parameter == o.aspect
                        && normal(&super::schedule_evidence::text(drawing, rows[0]))
                            == normal(&format!("{tag} {}", o.text))
                });
            if complete {
                continue;
            }
            let mut finding = issue(
                "manufacturing_reference_unresolved",
                &format!("{}/{tag}", leader.name),
                Severity::Error,
                format!(
                    "{} refers to {tag}, but its complete source-matched manufacturing row is absent",
                    leader.name
                ),
            );
            finding.sources = owned.iter().map(|o| o.source.clone()).collect();
            finding.annotation = Some(leader.name.clone());
            finding.view = leader.view.clone();
            finding.related = rows.iter().map(|m| m.name.clone()).collect();
            finding.fix = Some("Restore the complete manufacturing requirements row and its source binding, or print the requirement on its leader in full.".into());
            issues.push(finding);
        }
    }
    issues
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame(value: f64, modifiers: &[&str], datums: &[&str]) -> Record {
        let mut r: Record = serde_json::from_value(serde_json::json!({
            "source_id": "geometric_tolerance:#1", "category": "geometric_tolerance",
            "kind": "position", "part": 0, "part21_id": 1, "label": "", "dominant_axis": "Z",
            "datum_refs": [], "gtol_modifiers": [], "basic": false, "faces": [], "face_ids": [],
            "face_groups": [], "edges": [], "instances": [], "blockers": [],
        }))
        .unwrap();
        r.value = Some(value);
        r.gtol_modifiers = modifiers.iter().map(|s| s.to_string()).collect();
        r.datum_refs = datums.iter().map(|s| s.to_string()).collect();
        r
    }

    #[test]
    fn a_dimension_lowers_when_its_kind_and_faces_are_drawn() {
        let mut r = frame(30.0, &[], &[]);
        r.category = Category::Dimension;
        r.kind = "linear".into();
        r.geometry = Some(crate::pmi::Geometry {
            axis: Some("X".into()),
            ..Default::default()
        });
        assert_eq!(dimension(&r), Ok(()));
        let mut oblique = r.clone();
        oblique.geometry.as_mut().unwrap().axis = None;
        assert!(
            dimension(&oblique)
                .unwrap_err()
                .contains("principal projection plane")
        );
        let mut unproven = r.clone();
        unproven.geometry.as_mut().unwrap().reasons =
            vec!["linear reference groups occupy the same station".into()];
        assert_eq!(
            dimension(&unproven),
            Err("linear reference groups occupy the same station".into())
        );
        let mut blocked = r.clone();
        blocked.blockers = vec!["the dimension modifier Reference is not carried".into()];
        assert!(dimension(&blocked).unwrap_err().contains("Reference"));
        let mut label = r.clone();
        label.kind = "common_label".into();
        assert!(dimension(&label).unwrap_err().contains("common label"));
        // An angle lowers where a view shows it true, as the plan draws it.
        let mut angle = r.clone();
        angle.kind = "angular".into();
        assert!(dimension(&angle).unwrap_err().contains("not derived"));
        // The sides as the Analysis carries them (the lint names no planner type, ADR1-9).
        let sides = |second: [f64; 3]| {
            serde_json::from_value(serde_json::json!({
                "vertex": [0.0, 0.0, 0.0], "first": [1.0, 0.0, 0.0], "second": second,
            }))
            .unwrap()
        };
        angle.geometry.as_mut().unwrap().angle = Some(sides([0.5, 0.866, 0.0]));
        assert_eq!(dimension(&angle), Ok(()));
        angle.geometry.as_mut().unwrap().angle = Some(sides([0.5, 0.6, 0.6]));
        assert!(
            dimension(&angle)
                .unwrap_err()
                .contains("oblique to every view")
        );
    }

    #[test]
    fn one_material_of_the_drawing_is_its_title_blocks() {
        let mut report: Report = serde_json::from_value(serde_json::json!({
            "schema": "draftwright.pmi/1", "parts": ["plate"], "sources": [], "records": [],
            "accounting": {"semantic": 0, "consumed": 0, "reported": 0, "presentation": 0,
                "validation": 0},
            "materials": [{"source_id": "material:#9", "part": 0, "designation": "AlMg3"}],
        }))
        .unwrap();
        assert!(materials(&report).is_empty());
        // A material stating no designation is reported, not taken as no material.
        let mut unnamed = report.clone();
        unnamed.materials[0].designation.clear();
        let found = materials(&unnamed);
        assert_eq!(found.len(), 1, "{found:#?}");
        assert_eq!(found[0].code, "step_material_unavailable");
        assert!(found[0].message.contains("designation is empty"));
        // One part's material, of two parts drawn: not the drawing's.
        report.parts.push("pin".into());
        let found = materials(&report);
        assert_eq!(found.len(), 1, "{found:#?}");
        assert_eq!(
            (found[0].code.as_str(), found[0].severity),
            ("step_material_unavailable", Severity::Warning)
        );
        assert_eq!(found[0].sources, ["material:#9"]);
        // Each part's own: none can be the title block's.
        let mut steel = report.materials[0].clone();
        steel.source_id = "material:#12".into();
        steel.part = 1;
        report.materials.push(steel);
        let found = materials(&report);
        assert_eq!(found.len(), 1, "{found:#?}");
        assert_eq!(found[0].code, "step_material_ambiguous");
        assert_eq!(found[0].sources, ["material:#9", "material:#12"]);
    }

    #[test]
    fn a_frame_states_its_magnitude_zone_and_datums() {
        let r = frame(0.05, &["diameter_zone"], &["A", "B", "A"]);
        assert_eq!(mismatch(&r, "⌖ ⌀0.05Ⓜ A B"), None);
        assert!(mismatch(&r, "⌖ ⌀0.06Ⓜ A B").is_some());
        assert!(mismatch(&r, "⌖ 0.05Ⓜ A B").is_some());
        assert!(mismatch(&r, "⌖ S⌀0.05 A B").is_some());
        assert!(mismatch(&r, "⌖ ⌀0.05 A").is_some());
        assert!(mismatch(&r, "⌖ ⌀0.05 A B 7").is_some());
        assert!(mismatch(&r, "⌖ A B").unwrap().contains("no numeric value"));
        // A count of the features over the frame is read past, not as a value.
        assert_eq!(mismatch(&r, "4× ⌖ ⌀0.05Ⓜ A B"), None);
        assert!(mismatch(&r, "4× ⌖ ⌀0.06Ⓜ A B").is_some());
        // Within the display's rounding of the 13th significant digit.
        let r = frame(0.1 + 0.2, &[], &[]);
        assert_eq!(mismatch(&r, "⏥ 0.3"), None);
    }

    /// LINT-code-pmi_source_text_mismatch: source-scoped notes compare their own words, never
    /// the shared note formatter; a finish compares its parameter and value, not just a number.
    #[test]
    fn manufacturing_source_text_is_checked_independently_of_its_identity() {
        use crate::pmi::Field;
        use serde_json::json;
        let mut edge = frame(0., &[], &[]);
        edge.source_id = "manufacturing_requirement:#2".into();
        edge.category = Category::ManufacturingRequirement;
        edge.kind = "edge_condition".into();
        edge.label = "Break sharp edges and remove burrs 0.2 max".into();
        let mut finish = edge.clone();
        finish.source_id = "surface_texture:#3".into();
        finish.kind = "surface_finish".into();
        finish.label = "Ra 0.8 µm".into();
        finish.fields = vec![("Ra".into(), Field::Length(0.0008))];
        let report: Report = serde_json::from_value(json!({
            "schema": "draftwright.pmi/1", "parts": ["plate"], "sources": [],
            "records": [edge, finish], "accounting": {"semantic": 0, "consumed": 0, "reported": 0, "presentation": 0, "validation": 0}, "materials": []
        }))
        .unwrap();
        let mut a: Analysis = serde_json::from_value(json!({
            "schema": "draftwright.analysis/5",
            "binding": {"source_sha256": "", "part": "", "face_count": 0, "loader": ""},
            "envelope": [[0., 0., 0.], [1., 1., 1.]], "iso_bounds": [0., 0., 0., 0.]
        }))
        .unwrap();
        let ids = report.identities();
        let marks = |edge: &str, finish: &str| {
            vec![
                Mark {
                    name: ids.id("manufacturing_requirement:#2").unwrap().into(),
                    view: None,
                    text: edge.into(),
                    claims: vec![],
                },
                Mark {
                    name: ids.id("surface_texture:#3").unwrap().into(),
                    view: None,
                    text: finish.into(),
                    claims: vec![],
                },
            ]
        };
        a.pmi = Some(Reading::Read(report.clone()));
        let mismatches = |marks: &[Mark]| {
            coverage(&a, marks)
                .into_iter()
                .filter(|i| i.code == "pmi_source_text_mismatch")
                .collect::<Vec<_>>()
        };
        let good = marks("1  Break sharp edges and remove\nburrs  0.2 max", "Ra 0.80");
        assert!(mismatches(&good).is_empty());
        for text in [
            "1 Break sharp edges and remove burrs",
            "1 Deburr all edges",
            "1 Break sharp edges and remove burrs 0.2 max EXCEPT HOLES",
        ] {
            let found = mismatches(&marks(text, "Ra 0.8"));
            assert_eq!(found.len(), 1, "{text}: {found:?}");
            assert_eq!(found[0].sources, ["manufacturing_requirement:#2"]);
            assert_eq!(found[0].severity, Severity::Error);
            assert_eq!(found[0].annotation.as_deref(), Some(good[0].name.as_str()));
        }
        for text in [
            "Ra 9.9",
            "Ra 0.8001",
            "Ra 0.8000000000001",
            "Rz 0.8",
            "Ra 0.8 Ra 0.8",
            "Ra NaN",
            "",
        ] {
            let found = mismatches(&marks(&good[0].text, text));
            assert_eq!(found.len(), 1, "{text}: {found:?}");
            assert_eq!(found[0].sources, ["surface_texture:#3"]);
        }
        // Text on an unrelated annotation cannot rescue either source's own corrupted text.
        let mut wrong = marks("1 Deburr", "Ra 9.9");
        wrong.push(Mark {
            name: "n:1".into(),
            view: None,
            text: format!("{} Ra 0.8", report.records[0].label),
            claims: vec![],
        });
        assert_eq!(mismatches(&wrong).len(), 2);
        // Structured AP242 textures may also name a multi-word length parameter.
        let mut multi = report.clone();
        multi.records[1]
            .fields
            .push(("sampling length".into(), Field::Length(0.25)));
        let mark = Mark {
            name: "pmi:surface_texture:#3".into(),
            view: None,
            text: "Ra 0.80 sampling length 250".into(),
            claims: vec![],
        };
        assert!(manufacturing_text(&multi, &[mark], &multi.identities()).is_empty());
        // The existing surface-finish display contract is three significant digits. Neither
        // unit conversion roundoff nor a correctly rounded value is a source mismatch.
        for (mm, text) in [
            (0.00012, "Ra 0.12"),
            (0.001016, "Ra 1.02"),
            (0.009999, "Ra 10"),
            (0.00000000001234, "Ra 1.23e-8"),
        ] {
            let mut source = report.clone();
            source.records[1].fields = vec![("Ra".into(), Field::Length(mm))];
            let mark = Mark {
                name: "pmi:surface_texture:#3".into(),
                view: None,
                text: text.into(),
                claims: vec![],
            };
            assert!(
                manufacturing_text(&source, &[mark], &source.identities()).is_empty(),
                "{mm} mm must print as {text}"
            );
        }
    }
}
