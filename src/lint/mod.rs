//! Lint: an independent check of a finished drawing against the part.
//!
//! Lint judges what the drawing *says* (its annotations' text and the measurements they claim)
//! against what the part *is* (the [`Analysis`]). It never reads the Plan or the Spec the drawing
//! was made from (ADR1-9, ADR5-2; maintainer decision 1 in ADR R4): the requirements it counts
//! come from the Analysis, and the evidence from the rendered [`Drawing`]. A guard test
//! (`guard::tests`) fails if any file in this module names the Plan or the Spec. So the same
//! checks can judge Python draftwright's captured sheets and the port's own.
//!
//! - [`lint`] runs every ported rule on a drawing;
//! - [`marks`] reads a drawing's annotations as lint sees them, and [`check`] runs the rules on
//!   marks from any source (the comparison with Python's sheets builds them from the captured
//!   facts);
//! - [`LintIssue`] is a finding, with the catalogue's fields (LINT-issues-lintissue_schema);
//! - [`structural`] checks the drawn sheet itself (scale stated, views and annotations on the
//!   page and apart, derived-view identifiers): draftwright's `lint_drawing`, which runs before
//!   coverage. It reads the drawing alone; [`lint`] stays coverage only, and the review reports
//!   the structural findings beside it.
//!
//! Ported rules, in the order Python's lint runs them: the diameter backstop
//! ([`coverage::feature_coverage`]); boss heights ([`bosses`]); the location backstop
//! ([`coverage::located`]); pads not defined and pockets not located ([`prismatic`]); raised pads
//! ([`pads`]) and polygonal bosses ([`polygonal`]); section recesses no drafting grammar reads,
//! and refused recess recognitions ([`section_recesses`], which also reads every recess's grammar
//! for the families below); hexagonal pockets and circular seats ([`pockets::hex_coverage`],
//! [`channels::circular_coverage`]); angled steps ([`angled_steps`]); openings and outlines of
//! the principal faces no feature states ([`profiles`], from the Analysis's record of the part's
//! principal faces); double-D bores ([`profiled_bores`]); flat, groove, chamfer, fillet and
//! blend coverage ([`flats`], [`grooves`], [`chamfers`], [`fillets`], [`blends`]); paired-ramp steps
//! ([`paired_ramp_steps`]), gusset ribs ([`gussets`]), circular blind steps
//! ([`circular_blind_steps`]), blind slots ([`blind_slots`]) and oriented slots
//! ([`oriented_slots`]); through-step and slot coverage
//! ([`steps`], [`slots`]); hole coverage ([`holes`]); open channels ([`channels`]); polygonal stock
//! ([`polygonal`]); lone pockets ([`pockets`]); and, after them as Python's `_lint_pmi` runs, the
//! part's AP242 PMI on the drawing ([`pmi_coverage`]). The backstop's count check
//! ([`coverage::counts`]) is not run: draftwright's drawing lint leaves counts to hole coverage.
//! Centre marks are checked from the drawing's centres and transforms. The families whose
//! requirements are a feature's own sizes share [`parameters`]. The envelope's and turned steps'
//! completeness ([`envelope`], [`turned`]) also report uncovered ledger requirements. This is a
//! deliberate difference from Python, recorded in the completeness findings verdicts.

mod characteristics;

use serde::Serialize;

use crate::model::Analysis;
use crate::output::{Claim, Drawing};

pub mod angled_steps;
mod authored;
pub mod blends;
pub mod blind_slots;
pub mod bosses;
pub mod chamfers;
pub mod channels;
pub mod circular_blind_steps;
pub mod coverage;
pub mod envelope;
pub mod fidelity;
pub mod fillets;
pub mod flats;
pub mod gears;
pub mod grooves;
pub mod gussets;
pub mod hole_patterns;
pub mod holes;
mod interior_voids;
pub mod leaders;
pub mod location;
pub(crate) mod notch_evidence;
mod notches;
pub(crate) mod oriented_chamfers;
pub mod oriented_slots;
pub mod pads;
pub mod paired_ramp_steps;
pub mod parameters;
pub mod plates;
pub mod pmi_coverage;
pub mod pocket_patterns;
pub mod pockets;
pub mod polygonal;
pub mod prismatic;
pub mod profile_angles;
pub mod profiled_bores;
pub mod profiles;
pub mod rectangular_notches;
pub mod redundancy;
pub mod schedule_evidence;
pub(crate) mod schemes;
pub mod section_recesses;
pub mod slots;
pub mod steps;
pub mod structural;
pub mod turned;

#[cfg(test)]
mod guard;

/// How serious a finding is.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Severity {
    Error,
    Warning,
    Info,
}

/// Where in the drawing's making a finding arose: a valid annotation that did not fit
/// (`placement`), or malformed input (`validation`). Coverage findings have none.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Stage {
    Placement,
    Validation,
}

/// A physical requirement of a feature: the feature's Analysis ID and the parameter, in the role
/// vocabulary (`bore.diameter`, `bore.through`, `grouping.count`, …).
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize)]
pub struct Requirement {
    pub feature: String,
    pub parameter: String,
}

/// One finding (LINT-issues-lintissue_schema: Python's `LintIssue`, with the same stable fields).
#[derive(Clone, Debug, PartialEq)]
pub struct LintIssue {
    /// Stable within one drawing: `l:<code>:<subject>`.
    pub id: String,
    /// Page of a structural finding; absent means sheet 1. Coverage is document-wide.
    pub sheet: Option<u32>,
    /// The stable machine-readable check, e.g. `hole_requirement_missing`.
    pub code: String,
    pub severity: Severity,
    pub message: String,
    /// Where on the page, mm, when the finding has a place.
    pub at: Option<[f64; 2]>,
    /// The view it is in, `v:<name>`.
    pub view: Option<String>,
    /// The features it is about, by Analysis ID.
    pub subjects: Vec<String>,
    /// The physical requirements it is about (Python's `hole_requirement_ids`).
    pub requirements: Vec<Requirement>,
    /// The measurements it implicates, as `<feature>/<parameter>` (Python's `measurement_ids`).
    pub measurements: Vec<String>,
    /// External source records it implicates (AP242 PMI IDs).
    pub sources: Vec<String>,
    pub stage: Option<Stage>,
    /// The annotation it names, and any others taking part.
    pub annotation: Option<String>,
    pub related: Vec<String>,
    /// Why the evidence was not accepted, where some was found.
    pub evidence: Option<String>,
    /// Prose advice has no Spec operations: its scope is conservatively sheet-wide.
    pub fix: Option<String>,
}

impl LintIssue {
    pub fn labels(&self) -> crate::finding::Labels {
        crate::finding::labels(
            &self.code,
            self.severity == Severity::Info,
            &self.subjects,
            self.requirements.iter().map(|r| r.parameter.as_str()),
            &self.sources,
            &[],
            None,
        )
    }
}

impl Serialize for LintIssue {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        use serde::ser::SerializeMap;
        let mut map = serializer.serialize_map(None)?;
        macro_rules! field {
            ($name:ident) => {
                map.serialize_entry(stringify!($name), &self.$name)?
            };
        }
        macro_rules! optional {
            ($name:ident) => {
                if let Some(value) = &self.$name {
                    map.serialize_entry(stringify!($name), value)?;
                }
            };
        }
        macro_rules! list {
            ($name:ident) => {
                if !self.$name.is_empty() {
                    field!($name);
                }
            };
        }
        field!(id);
        optional!(sheet);
        field!(code);
        field!(severity);
        let labels = self.labels();
        map.serialize_entry("impact", &labels.impact)?;
        map.serialize_entry("group", &labels.group)?;
        field!(message);
        optional!(at);
        optional!(view);
        field!(subjects);
        list!(requirements);
        list!(measurements);
        list!(sources);
        optional!(stage);
        optional!(annotation);
        list!(related);
        optional!(evidence);
        if let Some(fix) = &self.fix {
            map.serialize_entry("fix", fix)?;
            map.serialize_entry("scope", &crate::remedy_scope::UNBOUND_SCOPE)?;
            map.serialize_entry("fix_description", &advice_description(fix))?;
        }
        map.end()
    }
}

// Advice has no executable edit and cannot promise a sheet re-layout.
fn advice_description(fix: &str) -> String {
    let mut chars = fix.trim().trim_end_matches('.').chars();
    let text = chars
        .next()
        .map(|c| c.to_uppercase().chain(chars).collect::<String>())
        .unwrap_or_default();
    format!("{text}.")
}

/// An annotation as lint sees it: the text it prints and the measurements it claims.
#[derive(Clone, Debug, PartialEq)]
pub struct Mark {
    pub name: String,
    pub view: Option<String>,
    /// Its label's text runs, joined by spaces.
    pub text: String,
    pub claims: Vec<Claim>,
}

/// The drawing's annotations as marks: each label's text read back from the drawing's own text
/// runs, never from the item it was drawn from.
pub fn marks(drawing: &Drawing) -> Vec<Mark> {
    drawing_marks(drawing, true)
}

fn drawing_marks(drawing: &Drawing, witnesses: bool) -> Vec<Mark> {
    drawing
        .annotations
        .iter()
        .map(|a| {
            let drawn_text = schedule_evidence::text(drawing, a);
            let body = drawing
                .sheet
                .label_text
                .get(&a.name)
                .map(|wording| wording.body(&drawn_text));
            let mut claims =
                if !matches!(body, Some(None)) && schedule_evidence::verified(a, &drawn_text) {
                    a.claims.clone()
                } else {
                    Vec::new()
                };
            // Preserve the mark's own witness geometry for callers of the mark-only
            // audit too. Coordinates are reconstructed from Analysis by location, never
            // inferred by a matching high-end number. This metadata is not drawn.
            if witnesses
                && let Some(item) = drawing.sheet.items.iter().find(|d| d.name == a.name)
                && item.kind == crate::output::Kind::Dimension
                && item.elbow.is_none()
                && let Some(frame) = a
                    .view
                    .as_deref()
                    .and_then(|v| drawing.sheet.frames.get(v.trim_start_matches("v:")))
            {
                let witness = serde_json::to_string(&(frame, &item.lines)).unwrap();
                claims.push(crate::output::Claim {
                    feature: format!("station_witness:{witness}"),
                    parameter: "witnesses".into(),
                });
            }
            let text = body.flatten().unwrap_or(drawn_text);
            Mark {
                name: a.name.clone(),
                view: a.view.clone(),
                text,
                claims,
            }
        })
        .collect()
}

/// Every ported rule's findings on *drawing*, a drawing of the part *analysis* describes.
/// Lint reads no spec (ADR1-9): when the drawing's spec authors datums or controls, the caller
/// passes the Analysis with them merged into its PMI reading ([`crate::spec_gdt::effective`]), as
/// [`crate::api::review`] does.
pub fn lint(analysis: &Analysis, drawing: &Drawing) -> Vec<LintIssue> {
    let mut marks = drawing_marks(drawing, false);
    location::conveyed_boss_locations(analysis, &mut marks);
    location::drawn_constraints(analysis, drawing, &mut marks);
    slots::conveyed_locations(analysis, &mut marks);
    // Stream K4: each callout's tip against the edge it names, and its shaft against the
    // material (`leaders`). A callout whose leader or witness misses the physical feature it
    // claims earns no credit for it (ISSUE-1374-a/d).
    let (leader_issues, withdrawn) = leaders::check(analysis, drawing);
    for w in &withdrawn {
        for mark in marks.iter_mut().filter(|m| m.name == w.annotation) {
            mark.claims.retain(|c| {
                !(w.features.contains(&c.feature) && w.parameters.contains(&c.parameter.as_str()))
            });
        }
    }
    let sites: std::collections::BTreeMap<_, _> = crate::bridge::holes::read(analysis)
        .into_iter()
        .filter_map(|(f, h)| Some((f.id.as_str(), h.ok()?.at)))
        .collect();
    for (a, mark) in drawing.annotations.iter().zip(&mut marks) {
        let Some(origin) = a.coordinate_origin else {
            continue;
        };
        if drawing
            .sheet
            .items
            .iter()
            .any(|item| item.name == a.name && item.kind == crate::output::Kind::Dimension)
        {
            continue;
        }
        // Verify the printed cell in its own datum frame before translating its evidence into
        // the envelope frame used by the existing hole/pattern coverage checks.
        let proof = mark.claims.first().and_then(|c| {
            let axis = ["x", "y", "z"]
                .iter()
                .position(|axis| c.parameter == format!("location.length.{axis}"))?;
            let at = sites.get(c.feature.as_str())?;
            holes::states(&mark.text, at[axis] - origin[axis])
                .then(|| coverage::fmt(at[axis] - analysis.envelope[0][axis]))
        });
        if let Some(text) = proof {
            mark.text = text;
        } else {
            mark.claims.clear();
        }
    }
    // Withdraw a damaged scheduled size cell as a unit, including its THRU/count riders.
    // The requirement inventory remains in the Analysis and still reports the missing ink.
    for mark in &mut marks {
        if !holes::verified_schedule_size(analysis, mark) {
            mark.claims.clear();
        }
    }
    let marks: Vec<_> = marks
        .into_iter()
        .filter(|m| blends::verified_row(analysis, m))
        .collect();
    let mut relief_issues = Vec::new();
    if !blends::relief_stated(analysis, drawing) {
        relief_issues.push(LintIssue {
            sheet: None,
            code: "relief_profile_requirement_missing".into(),
            severity: if analysis.solid_count.is_some_and(|n| n > 1) {
                Severity::Info
            } else {
                Severity::Warning
            },
            message: "Grouped shallow relief profiles have no placed model-governing note.".into(),
            subjects: vec![blends::RELIEF_NOTE.into()],
            id: "l:relief_profile_requirement_missing".into(),
            fix: Some("Restore the relief-profile note or state the profiles explicitly.".into()),
            requirements: Vec::new(),
            at: None,
            view: None,
            measurements: Vec::new(),
            sources: Vec::new(),
            stage: None,
            annotation: None,
            related: Vec::new(),
            evidence: None,
        });
    }
    let satisfied: std::collections::BTreeSet<_> = drawing
        .annotations
        .iter()
        .filter(|a| !a.satisfies.is_empty())
        .filter(|a| {
            let Some(text) = crate::output::drawn_body(drawing, a) else {
                return false;
            };
            let Some((_, radius)) = text.split_once("ALL UNSPECIFIED RADII R") else {
                return false;
            };
            let radius = radius.trim();
            // A blanket default must identify its value, and every different radius must
            // have a real callout. Scoped or representative-point notes grant no authority.
            blends::relief_stated(analysis, drawing)
                && radius
                    .parse::<f64>()
                    .is_ok_and(|r| r.is_finite() && r > 0.0)
                && analysis
                    .features
                    .iter()
                    .filter(|f| {
                        matches!(f.kind.as_str(), "blend" | "fillet")
                            && !blends::is_relief(analysis, f)
                    })
                    .all(|f| {
                        f.parameters
                            .get("radius")
                            .and_then(|v| v.as_f64())
                            .is_some_and(|r| {
                                holes::states(radius, r)
                                    || marks.iter().any(|m| {
                                        m.view.is_some()
                                            && holes::states(&m.text, r)
                                            && m.claims.iter().any(|c| {
                                                c.feature == f.id
                                                    && c.parameter == format!("{}.radius", f.kind)
                                            })
                                    })
                            })
                    })
        })
        .flat_map(|a| {
            // Invariant: The preceding filter validated this same drawn note body.
            let text = crate::output::drawn_body(drawing, a).expect("verified note wrapper");
            let radius = text
                .split_once("ALL UNSPECIFIED RADII R")
                // Invariant: The preceding filter required the ALL UNSPECIFIED RADII R prefix.
                .unwrap()
                .1
                .trim()
                .to_string();
            a.satisfies.iter().filter(move |c| {
                analysis.features.iter().any(|f| {
                    f.id == c.feature
                        && c.parameter == format!("{}.radius", f.kind)
                        && f.parameters
                            .get("radius")
                            .and_then(|v| v.as_f64())
                            .is_some_and(|r| holes::states(&radius, r))
                })
            })
        })
        .map(|c| (&c.feature, &c.parameter))
        .collect();
    let mut satisfied = satisfied;
    // Authored feature notes grant only their exact, explicitly named physical identities.
    // No prose, synthetic through/count authority, or sibling propagation is involved.
    if let Some(crate::pmi::Reading::Read(report)) = &analysis.pmi {
        let ids = report.identities();
        for a in &drawing.annotations {
            let record = ids
                .source(&a.name)
                .and_then(|id| report.records.iter().find(|r| r.source_id == id));
            let Some(r) = record.filter(|r| r.kind == "note") else {
                continue;
            };
            let Some(owner) = r
                .source_id
                .strip_prefix("spec:features/")
                .and_then(|s| s.strip_suffix("/note"))
            else {
                continue;
            };
            if a.view.as_deref().is_none_or(|v| {
                !drawing
                    .sheet
                    .frames
                    .contains_key(v.trim_start_matches("v:"))
            }) || !drawing.sheet.items.iter().any(|i| {
                i.name == a.name && i.kind == crate::output::Kind::Gdt && !i.lines.is_empty()
            }) || !a.runs.iter().any(|&i| {
                drawing
                    .text
                    .get(i)
                    .is_some_and(|r| !r.text.trim().is_empty())
            }) {
                continue;
            }
            for c in &a.satisfies {
                if c.feature == owner
                    && (analysis.features.iter().any(|f| f.id == owner)
                        || analysis
                            .recognition
                            .as_ref()
                            .is_some_and(|r| r.patterns.iter().any(|p| p.id == owner)))
                {
                    satisfied.insert((&c.feature, &c.parameter));
                }
            }
        }
    }
    let mut issues: Vec<_> = check(analysis, &marks)
        .into_iter()
        .filter_map(|mut i| {
            if !i
                .requirements
                .iter()
                .any(|r| satisfied.contains(&(&r.feature, &r.parameter)))
            {
                return Some(i);
            }
            i.requirements
                .retain(|r| !satisfied.contains(&(&r.feature, &r.parameter)));
            if i.requirements.is_empty() {
                return None;
            }
            i.subjects
                .retain(|id| i.requirements.iter().any(|r| &r.feature == id));
            Some(i)
        })
        .chain(relief_issues)
        .collect();
    let concentric = hole_patterns::concentric_on_drawing(analysis, drawing);
    issues.retain(|i| {
        i.code != "hole_pattern_requirement_missing"
            || i.requirements.is_empty()
            || !i.requirements.iter().all(|r| {
                concentric
                    .get(&r.feature)
                    .is_some_and(|parameters| parameters.contains(r.parameter.as_str()))
            })
    });
    // A corner its drawn linear dimensions fix needs no angle (ISO 129-1).
    if issues
        .iter()
        .any(|i| i.code == "angular_requirement_missing")
    {
        let defined = profile_angles::defined_by_drawing(analysis, drawing);
        issues.retain(|i| {
            i.code != "angular_requirement_missing"
                || i.requirements.is_empty()
                || !i.requirements.iter().all(|r| defined.contains(&r.feature))
        });
    }
    // A corner given its angle but no position is still undefined (#217).
    let reported: std::collections::BTreeSet<String> = issues
        .iter()
        .filter(|i| i.code.starts_with("angular_requirement_"))
        .flat_map(|i| i.requirements.iter().map(|r| r.feature.clone()))
        .collect();
    issues.extend(
        profile_angles::unlocated_by_drawing(analysis, drawing)
            .iter()
            .filter(|c| !reported.contains(&c.feature))
            .map(|c| profile_angles::unlocated(analysis, c)),
    );
    issues.extend(gears::check(analysis, drawing));
    issues.extend(schedule_evidence::check(drawing));
    issues.extend(pmi_coverage::manufacturing_references(analysis, drawing));
    issues.extend(coverage::drawn_centre_marks(analysis, drawing));
    issues.extend(leader_issues);
    issues.extend(pmi_coverage::source_sites(analysis, drawing));
    issues.extend(characteristics::check(drawing));
    issues
}

/// Coverage over all sheets, counted once. Text-run indices remain local to each annotation
/// until they are rebased into this drawing-wide evidence inventory; no positions are audited.
pub fn lint_sheets(analysis: &Analysis, drawings: &[Drawing]) -> Vec<LintIssue> {
    let Some(first) = drawings.first() else {
        return check(analysis, &[]);
    };
    let mut combined = first.clone();
    for drawing in &drawings[1..] {
        let offset = combined.text.len();
        combined.text.extend(drawing.text.iter().cloned());
        combined
            .annotations
            .extend(drawing.annotations.iter().cloned().map(|mut a| {
                for run in &mut a.runs {
                    *run += offset;
                }
                a
            }));
        combined
            .sheet
            .views
            .extend(drawing.sheet.views.iter().cloned());
        combined
            .sheet
            .items
            .extend(drawing.sheet.items.iter().cloned());
        combined.sheet.frames.extend(drawing.sheet.frames.clone());
        combined
            .sheet
            .precision
            .extend(drawing.sheet.precision.clone());
        for (view, centres) in &drawing.sheet.centre_marks {
            combined
                .sheet
                .centre_marks
                .entry(view.clone())
                .or_default()
                .extend(centres);
        }
    }
    lint(analysis, &combined)
}

/// Every ported rule's findings on a drawing whose annotations are *marks*.
pub fn check(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let verified: Vec<_> = marks
        .iter()
        .filter(|m| blends::verified_row(analysis, m))
        .cloned()
        .collect();
    let printed = &verified;
    let (mut extra, scheme_issues) = schemes::evidence(analysis, printed);
    extra.extend(location::evidence(analysis, printed));
    let expanded;
    let marks = if extra.is_empty() {
        printed
    } else {
        expanded = printed.iter().cloned().chain(extra).collect::<Vec<_>>();
        &expanded
    };
    let mut issues = coverage::feature_coverage(analysis, marks);
    issues.extend(scheme_issues);
    issues.extend(interior_voids::findings(analysis));
    issues.extend(authored::check(analysis, marks));
    issues.extend(bosses::coverage(analysis, marks));
    if !location::active(analysis, printed) {
        issues.extend(coverage::located(analysis, marks));
    }
    issues.extend(coverage::schedule_entries(analysis, marks));
    issues.extend(prismatic::coverage(analysis, marks));
    issues.extend(pads::coverage(analysis, marks));
    issues.extend(polygonal::boss_coverage(analysis, marks));
    issues.extend(section_recesses::coverage(analysis, marks));
    issues.extend(pockets::hex_coverage(analysis, marks));
    issues.extend(channels::circular_coverage(analysis, marks));
    issues.extend(angled_steps::coverage(analysis, marks));
    issues.extend(profiles::coverage(analysis));
    issues.extend(profile_angles::coverage(analysis, marks));
    issues.extend(profiled_bores::coverage(analysis, marks));
    issues.extend(flats::coverage(analysis, marks));
    issues.extend(grooves::coverage(analysis, marks));
    issues.extend(grooves::annular_coverage(analysis, marks));
    issues.extend(chamfers::coverage(analysis, marks));
    issues.extend(notches::coverage(analysis, marks));
    issues.extend(notches::channel_coverage(analysis, marks));
    issues.extend(notches::rectangular_coverage(analysis, marks));
    issues.extend(fillets::coverage(analysis, marks));
    issues.extend(blends::coverage(analysis, marks));
    issues.extend(paired_ramp_steps::coverage(analysis, marks));
    issues.extend(gussets::coverage(analysis, marks));
    issues.extend(circular_blind_steps::coverage(analysis, marks));
    issues.extend(blind_slots::coverage(analysis, marks));
    issues.extend(blind_slots::semicircular_coverage(analysis, marks));
    issues.extend(oriented_slots::coverage(analysis, marks));
    issues.extend(steps::coverage(analysis, marks));
    issues.extend(slots::coverage(analysis, marks));
    issues.extend(holes::coverage(analysis, marks));
    issues.extend(channels::coverage(analysis, marks));
    issues.extend(plates::coverage(analysis, marks));
    issues.extend(polygonal::stock_coverage(analysis, marks));
    issues.extend(pockets::coverage(analysis, marks));
    issues.extend(pockets::corner_coverage(analysis, marks));
    issues.extend(pocket_patterns::coverage(analysis, marks));
    issues.extend(envelope::coverage(analysis, marks));
    issues.extend(turned::coverage(analysis, marks));
    issues.extend(hole_patterns::coverage(analysis, marks));
    assembly_coverage(analysis, &mut issues);
    issues.extend(pmi_coverage::coverage(analysis, marks));
    redundancy::determined(analysis, printed, &mut issues);
    issues.extend(redundancy::coverage(analysis, printed));
    issues.extend(redundancy::references(analysis, printed));
    if location::active(analysis, printed) {
        issues.extend(location::coverage(analysis, printed));
    } else {
        // The legacy backstop omits centred holes and transverse slot locations.
        // The printed graph must also fail closed for those geometric requirements.
        let missing = location::coverage(analysis, printed)
            .into_iter()
            .filter(|i| i.code == "feature_not_located");
        for issue in missing {
            if !issue.requirements.iter().all(|r| {
                issues.iter().any(|i| {
                    (i.code.contains("missing") || i.code == "feature_not_located")
                        && i.requirements.contains(r)
                })
            }) {
                issues.push(issue);
            }
        }
    }
    if location::has_station_requirements(analysis) {
        issues.extend(location::sizes(analysis, printed));
    }
    issues
}

/// Missing-feature coverage on a general arrangement is informational. Keep this policy at
/// the collection boundary so every family uses the same solid-count decision.
fn assembly_coverage(analysis: &Analysis, issues: &mut [LintIssue]) {
    if analysis.solid_count.is_some_and(|n| n > 1) {
        for issue in issues {
            if issue.severity == Severity::Warning {
                issue.severity = Severity::Info;
            }
        }
    }
}

/// A bounded coverage finding; several solids retain Python's informational severity.
fn uncovered(
    analysis: &Analysis,
    code: &str,
    feature: &str,
    parameter: &str,
    message: String,
) -> LintIssue {
    LintIssue {
        sheet: None,
        id: format!("l:{code}:{feature}/{parameter}"),
        code: code.into(),
        severity: if analysis.solid_count.is_some_and(|n| n > 1) {
            Severity::Info
        } else {
            Severity::Warning
        },
        message,
        at: None,
        view: None,
        subjects: vec![feature.into()],
        requirements: vec![Requirement {
            feature: feature.into(),
            parameter: parameter.into(),
        }],
        measurements: vec![format!("{feature}/{parameter}")],
        sources: Vec::new(),
        stage: None,
        annotation: None,
        related: Vec::new(),
        evidence: None,
        fix: Some(format!(
            "State the {} of {} in a view that shows its true size or location.",
            parameter.replace(['_', '.'], " "),
            analysis
                .features
                .iter()
                .find(|f| f.id == feature)
                .map(parameters::feature_name)
                .unwrap_or_else(|| "the feature".into())
        )),
    }
}
