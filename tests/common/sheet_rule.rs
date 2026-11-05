//! An independent oracle for the maintainer's sheet rule, read from the recorded candidates.
//! Live automatic choices exhaust descending preferred scales on each paper before growing it.
//! The legacy recovery trace below retains its historical fill rule (2026-10-12).
//! This oracle does not call the planner's selection code.
//!
//! 1. The smallest fit is the smallest sheet with a valid complete candidate (no hard finding or
//!    loss, no more annotations over another view than the first estimate), at its largest such
//!    scale. 2. Only that pair and the next larger sheet at a larger scale are allowed. 3. Against
//!    a valid first estimate, an allowed pair adding material crossings is set aside; if every one
//!    does, the first estimate is kept. 4. In the fill band the larger scale wins, then the
//!    smaller sheet; otherwise the fullest valid pair wins.

#![allow(dead_code)]

use draftwright::model::Plan;

#[derive(Clone, Debug, PartialEq)]
pub struct Tried {
    pub size: String,
    pub scale: String,
    pub fill: f64,
    pub valid: bool,
    pub over: usize,
    pub crossings: usize,
}

pub fn sheet_index(size: &str) -> usize {
    draftwright::layout::PAGES
        .iter()
        .position(|p| p.0 == size)
        .unwrap_or_else(|| panic!("{size} is not a sheet"))
}

pub fn factor(scale: &str) -> f64 {
    let (a, b) = scale.split_once(':').unwrap();
    a.parse::<f64>().unwrap() / b.parse::<f64>().unwrap()
}

/// Every finished candidate the basis lists (pairs refused before planning have no fill).
pub fn tried(basis: &str) -> Vec<Tried> {
    let mut rest = basis
        .split_once("candidates: ")
        .unwrap_or_else(|| panic!("no candidates: {basis}"))
        .1;
    let mut out = Vec::new();
    while let Some(i) = rest.find(" fill ") {
        let head: Vec<&str> = rest[..i].rsplitn(3, ' ').collect();
        let (scale, size) = (head[0], head[1]);
        let tail = &rest[i + 6..];
        let pct = tail.find('%').unwrap();
        let fill = tail[..pct].parse::<f64>().unwrap() / 100.0;
        let open = tail.find('(').unwrap();
        let close = open + tail[open..].find(')').unwrap();
        let inside = &tail[open + 1..close];
        let count = |what: &str| {
            inside
                .split(", ")
                .find_map(|p| p.strip_suffix(what))
                .map(|n| n.trim().parse::<usize>().unwrap())
                .unwrap_or_else(|| panic!("{what} missing: {inside}"))
        };
        out.push(Tried {
            size: size.to_string(),
            scale: scale.to_string(),
            fill,
            valid: inside.starts_with("valid and complete"),
            over: count(" additional view conflicts"),
            crossings: count(" additional material crossings"),
        });
        rest = &tail[close..];
    }
    out
}

fn in_band(f: f64) -> bool {
    (draftwright::sheet_fit::FILL_BAND[0]..=draftwright::sheet_fit::FILL_BAND[1]).contains(&f)
}

/// Asserts *plan*'s automatic sheet follows the rule over its recorded candidates; *seed* is the
/// first estimate's (sheet, scale). Returns the smallest fit.
pub fn assert_follows(name: &str, plan: &Plan, seed: (&str, &str)) -> (String, String) {
    if !plan.sheet_candidates.is_empty() {
        let maximum = plan
            .spec
            .sheet
            .preferred_max_size
            .as_deref()
            .unwrap_or("A3");
        let preferred = draftwright::layout::PAGES
            .iter()
            .position(|p| p.0 == maximum)
            .unwrap();
        let hard = plan.spec.sheets.as_ref().and_then(|s| s.max).unwrap_or(9) as usize;
        let count = (plan.spec.sheet.preferred_max_sheets.unwrap_or(2) as usize).min(hard);
        let mut expected: Vec<_> = draftwright::layout::PAGES[..=preferred]
            .iter()
            .map(|p| (p.0, 1))
            .collect();
        expected.extend((2..=count).map(|n| (maximum, n)));
        for p in &draftwright::layout::PAGES[preferred + 1..] {
            expected.extend((1..=count).map(|n| (p.0, n)));
        }
        expected.extend((count + 1..=hard).map(|n| ("A0", n)));
        assert!(plan.sheet_candidates.len() <= expected.len());
        for (candidate, request) in plan.sheet_candidates.iter().zip(&expected) {
            assert_eq!(
                (candidate.size.as_str(), candidate.max),
                *request,
                "{name}: trial order"
            );
            assert_eq!(candidate.preferred_max_size, maximum);
            assert_eq!(
                candidate.preferred_max_sheets,
                plan.spec.sheet.preferred_max_sheets.unwrap_or(2) as usize
            );
            let scales: Vec<_> = candidate.scales.iter().map(|s| factor(s)).collect();
            assert!(
                scales
                    .iter()
                    .all(|s| draftwright::layout::SCALES.contains(s)),
                "{name}: non-preferred scale"
            );
            assert!(
                scales.windows(2).all(|w| w[0] > w[1]),
                "{name}: preferred scales must descend without repeats"
            );
            // A request ruled out before planning has no trial scale and states why.
            if candidate.kind.as_deref() == Some(draftwright::sheet_policy::RULED_OUT) {
                assert!(candidate.scales.is_empty(), "{name}: ruled out yet planned");
                assert!(
                    candidate.outcome.starts_with("ruled out: ")
                        && candidate
                            .outcome
                            .contains(&format!("{} drawable area is", candidate.size))
                        || candidate.outcome.contains("readable floor"),
                    "{name}: {}",
                    candidate.outcome
                );
            }
        }
        let winners: Vec<_> = plan
            .sheet_candidates
            .iter()
            .enumerate()
            .filter(|(_, c)| c.outcome.starts_with(&format!("{}: usable at ", c.size)))
            .collect();
        if let Some((i, winner)) = winners.first() {
            assert_eq!(winners.len(), 1);
            assert_eq!(
                *i,
                plan.sheet_candidates.len() - 1,
                "{name}: first usable stops search"
            );
            assert_eq!(winner.size, plan.sheet.size);
            assert!(winner.scales.contains(&plan.sheet.scale));
            assert_eq!(
                winner.scales.last(),
                Some(&plan.sheet.scale),
                "{name}: largest clean scale must be the last trial"
            );
            assert!(plan.sheets.len() < winner.max);
            assert!(
                plan.sheet
                    .basis
                    .contains(&format!("sheet choice: {}", winner.outcome))
            );
        } else {
            assert_eq!(
                plan.sheet_candidates.len(),
                expected.len(),
                "{name}: fallback needs full search"
            );
            assert!(plan.sheet.basis.contains("least-bad unclipped candidate"));
            assert!(
                plan.problems
                    .iter()
                    .any(|p| p.code == "layout_recovery_exhausted")
            );
        }
        assert!(plan.sheet.default);
        return (plan.sheet.size.clone(), plan.sheet.scale.clone());
    }
    let basis = &plan.sheet.basis;
    let all = tried(basis);
    let estimate = format!("first estimate {} at {} (", seed.0, seed.1);
    let said = basis
        .split_once(&estimate)
        .unwrap_or_else(|| panic!("{name}: no {estimate}: {basis}"))
        .1;
    let seed_clean = said.starts_with("valid and complete)");
    let valid: Vec<&Tried> = all.iter().filter(|t| t.valid && t.over == 0).collect();
    assert!(!valid.is_empty(), "{name}: no valid candidate: {basis}");
    let smallest = valid
        .iter()
        .min_by(|a, b| {
            sheet_index(&a.size)
                .cmp(&sheet_index(&b.size))
                .then(factor(&b.scale).total_cmp(&factor(&a.scale)))
        })
        .unwrap();
    assert!(
        basis.starts_with(&format!(
            "smallest fit {} at {};",
            smallest.size, smallest.scale
        )),
        "{name}: {basis}"
    );
    let pool: Vec<&Tried> = valid
        .iter()
        .copied()
        .filter(|t| {
            (t.size == smallest.size && t.scale == smallest.scale)
                || (sheet_index(&t.size) == sheet_index(&smallest.size) + 1
                    && factor(&t.scale) > factor(&smallest.scale))
        })
        .filter(|t| !seed_clean || t.crossings == 0)
        .collect();
    if pool.is_empty() {
        assert!(seed_clean, "{name}: no allowed pair: {basis}");
        assert_eq!(
            (plan.sheet.size.as_str(), plan.sheet.scale.as_str()),
            seed,
            "{name}: the valid first estimate is kept: {basis}"
        );
        assert!(basis.contains("which is kept"), "{name}: {basis}");
        return (smallest.size.clone(), smallest.scale.clone());
    }
    let chosen = pool
        .iter()
        .find(|t| t.size == plan.sheet.size && t.scale == plan.sheet.scale)
        .unwrap_or_else(|| {
            panic!(
                "{name}: {} {} is not an allowed pair: {basis}",
                plan.sheet.size, plan.sheet.scale
            )
        });
    // Fill is recorded to 0.1%: compare with that resolution.
    let band: Vec<&&Tried> = pool.iter().filter(|t| in_band(t.fill)).collect();
    if band.is_empty() {
        let fullest = pool.iter().map(|t| t.fill).fold(0.0, f64::max);
        assert!(
            chosen.fill >= fullest - 1e-9,
            "{name}: below the band {} {} ({:.1}%) is not the fullest ({:.1}%): {basis}",
            chosen.size,
            chosen.scale,
            chosen.fill * 100.0,
            fullest * 100.0
        );
    } else {
        assert!(in_band(chosen.fill), "{name}: a band pair exists: {basis}");
        let best = band
            .iter()
            .map(|t| (factor(&t.scale), -(sheet_index(&t.size) as f64)))
            .fold((0.0, f64::NEG_INFINITY), |a, b| {
                if b.0 > a.0 || (b.0 == a.0 && b.1 > a.1) {
                    b
                } else {
                    a
                }
            });
        assert_eq!(
            (factor(&chosen.scale), -(sheet_index(&chosen.size) as f64)),
            best,
            "{name}: {basis}"
        );
    }
    (smallest.size.clone(), smallest.scale.clone())
}

/// The first estimate's (sheet, scale): from the sheet basis or the `layout_replanned`
/// finding (an automatic choice records the estimate only in its basis), or the plan's own.
pub fn seed_of(plan: &Plan) -> (String, String) {
    plan.sheet
        .basis
        .split_once("first estimate ")
        .map(|(_, from)| from)
        .or_else(|| {
            plan.problems
                .iter()
                .find(|p| p.code == "layout_replanned")
                .and_then(|p| p.message.split_once("from ").map(|(_, from)| from))
        })
        .map(|from| {
            let words: Vec<&str> = from.split_whitespace().collect();
            (words[0].to_string(), words[2].to_string())
        })
        .unwrap_or_else(|| (plan.sheet.size.clone(), plan.sheet.scale.clone()))
}

/// [`assert_follows`] with the seed read from the plan.
pub fn assert_rule(name: &str, plan: &Plan) -> (String, String) {
    let seed = seed_of(plan);
    assert_follows(name, plan, (&seed.0, &seed.1))
}

/// Soft preferences never replace the hard count limit or suppress their advisory.
pub fn assert_preferences(plan: &Plan) {
    let pages = ["A4", "A3", "A2", "A1", "A0"];
    let preferred = plan
        .spec
        .sheet
        .preferred_max_size
        .as_deref()
        .unwrap_or("A3");
    let used = pages.iter().position(|p| *p == plan.sheet.size).unwrap();
    let preference = pages.iter().position(|p| *p == preferred).unwrap();
    let count = 1 + plan.sheets.len();
    assert!(count <= plan.spec.sheets.as_ref().and_then(|s| s.max).unwrap_or(9) as usize);
    let exceeds =
        used > preference || count > plan.spec.sheet.preferred_max_sheets.unwrap_or(2) as usize;
    let notices: Vec<_> = plan
        .problems
        .iter()
        .filter(|p| p.code == "sheet_preference_exceeded")
        .collect();
    assert_eq!(notices.len(), usize::from(exceeds), "{}", plan.sheet.basis);
    for n in notices {
        assert_eq!(n.severity, "info");
        assert_eq!(n.labels().impact, draftwright::finding::Impact::Advisory);
        assert!(n.message.contains(&plan.sheet.size));
        assert!(n.message.contains(&format!("{count} sheet(s)")));
    }
}
