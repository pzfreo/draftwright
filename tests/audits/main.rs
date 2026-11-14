// Each original module keeps its helper instances, including private cache statics.
#![allow(clippy::duplicate_mod)]

#[path = "../completeness.rs"]
mod completeness;
#[path = "../completeness_rules.rs"]
mod completeness_rules;
#[path = "../issue_regressions.rs"]
mod issue_regressions;
#[path = "../items.rs"]
mod items;
#[path = "../lint.rs"]
mod lint;
#[path = "../lint_coverage.rs"]
mod lint_coverage;
#[path = "../lint_family_rest.rs"]
mod lint_family_rest;
#[path = "../lint_fidelity.rs"]
mod lint_fidelity;
#[path = "../lint_structural.rs"]
mod lint_structural;
#[path = "../ownership_rules.rs"]
mod ownership_rules;
#[path = "../pmi_coverage.rs"]
mod pmi_coverage;
#[path = "../problem_wording.rs"]
mod problem_wording;
#[path = "../review_leftovers.rs"]
mod review_leftovers;
#[path = "../rule_pins.rs"]
mod rule_pins;
#[path = "../rules_catalogue.rs"]
mod rules_catalogue;
#[path = "../rules_text.rs"]
mod rules_text;
#[path = "../trust_rules.rs"]
mod trust_rules;

#[path = "../finding_impact.rs"]
mod finding_impact;

#[path = "../issue_tiers.rs"]
mod issue_tiers;
