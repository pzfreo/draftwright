//! One owner per support-plane interval; determined dimensions are optional references.

use super::{Omitted, Shape, Want};
use crate::items::State;
use crate::lint::redundancy::{Span, TOL, path, walkable, walked};
use crate::model::{Analysis, Spec};

type Kept = (Want, bool, Option<&'static str>, Vec<&'static str>);

/// The reference dimensions [`consolidate`] made, each by its ID with the IDs of the dimensions
/// that determine it, in the order its basis names them (#26: the plan's `determined_by`).
pub(super) type References = std::collections::BTreeMap<String, Vec<String>>;

/// A ladder's witnesses may have moved from the box minimum to a datum. Its
/// parameter still identifies the original face level, not the rebased interval.
fn ladder_span(a: &Analysis, w: &Want) -> Option<(Span, bool)> {
    let original = w.measurements.iter().find_map(|m| {
        (m.feature == "step_level" && walked(a, &m.feature))
            .then(|| crate::lint::redundancy::span(a, &m.feature, &m.parameter))
            .flatten()
    })?;
    let Shape::Linear { a, b, axis, .. } = w.shape else {
        return None;
    };
    let drawn = Span::new(axis, a[axis], b[axis]);
    Some((drawn, !same_planes(drawn, original)))
}

/// A through step's leg (`through_step_leg.length.<axis>`).
fn leg(w: &Want) -> bool {
    w.measurements
        .iter()
        .any(|m| m.parameter.starts_with("through_step_leg.length."))
}

fn same_planes(a: Span, b: Span) -> bool {
    a.axis == b.axis && (a.lo - b.lo).abs() <= 1e-6 && (a.hi - b.hi).abs() <= 1e-6
}

fn interval(analysis: &Analysis, w: &Want) -> Option<Span> {
    if w.id.starts_with("d:pmi:") {
        return None;
    }
    let Shape::Linear { a, b, axis, .. } = w.shape else {
        return None;
    };
    // Point locations use their own view and physical witness contracts; they
    // are not support planes. Counted sizes and diameters are not one axial interval.
    if w.measurements
        .iter()
        .any(|m| m.parameter.starts_with("location.length.") || m.feature == "step_level")
    {
        return None;
    }
    if w.terms.iter().any(|t| t.contains('×') || t.contains('ø'))
        || w.measurements
            .iter()
            .any(|m| m.parameter.contains("diameter"))
        || ((b[axis] - a[axis]).abs() - w.value).abs() > TOL
    {
        return None;
    }
    let drawn = Span::new(axis, a[axis], b[axis]);
    w.measurements
        .iter()
        .filter(|m| walked(analysis, &m.feature))
        .find_map(|m| {
            crate::lint::redundancy::span(analysis, &m.feature, &m.parameter)
                .filter(|s| s.same(drawn))
        })
}

pub(super) fn consolidate(
    a: &Analysis,
    spec: &Spec,
    kept: &mut Vec<Kept>,
    given: &mut Vec<Vec<String>>,
    omitted: &mut Vec<Omitted>,
) -> References {
    if !spec
        .dimensions
        .values()
        .any(|d| d.style == Some(crate::label_text::Style::Reference))
    {
        return controlling(a, spec, kept, given, omitted);
    }
    // Filter information-only statements out of every controlling pass. Apply the
    // surviving controllers back in input order, without a sorting/merging pass.
    let information = |w: &Want| {
        spec.dimensions
            .get(&w.id)
            .is_some_and(|d| d.style == Some(crate::label_text::Style::Reference))
    };
    let (mut controls, mut fields): (Vec<_>, Vec<_>) = kept
        .iter()
        .zip(given.iter())
        .filter(|((w, ..), _)| !information(w))
        .map(|(w, g)| (w.clone(), g.clone()))
        .unzip();
    let references = controlling(a, spec, &mut controls, &mut fields, omitted);
    let mut surviving = controls.into_iter().zip(fields).peekable();
    (*kept, *given) = std::mem::take(kept)
        .into_iter()
        .zip(std::mem::take(given))
        .filter_map(|(w, g)| {
            if information(&w.0) {
                Some((w, g))
            } else if surviving
                .peek()
                .is_some_and(|((next, ..), _)| next.id == w.0.id)
            {
                surviving.next()
            } else {
                None
            }
        })
        .unzip();
    references
}

fn controlling(
    a: &Analysis,
    spec: &Spec,
    kept: &mut Vec<Kept>,
    given: &mut Vec<Vec<String>>,
    omitted: &mut Vec<Omitted>,
) -> References {
    super::gussets::share(kept, given, omitted);
    let mut references = References::new();
    if !walkable(a) {
        return references;
    }
    let mut gone = std::collections::BTreeSet::new();
    // A datum-based plane location owns a coincident size, carrying both claims.
    // Compare physical support planes, not equal values or point/axis locations:
    // the datum-to-face chain must survive, and unrelated equal sizes stay distinct.
    for j in 0..kept.len() {
        let Some((s, true)) = ladder_span(a, &kept[j].0) else {
            continue;
        };
        if kept[j].2.is_none() {
            continue;
        }
        for i in 0..kept.len() {
            if i == j
                || gone.contains(&i)
                || !kept[i].1
                || !given[i].is_empty()
                || kept[i].2 != kept[j].2
                || kept[i].0.terms != kept[j].0.terms
                || !interval(a, &kept[i].0).is_some_and(|p| same_planes(p, s))
            {
                continue;
            }
            let claims = kept[i].0.measurements.clone();
            kept[j].0.measurements.extend(claims);
            cover(
                i,
                j,
                kept,
                omitted,
                "the datum-based ladder states the same two support planes and carries this size too",
            );
            gone.insert(i);
        }
    }
    // #33: on a turned profile dimensioned as a baseline from its datum, a band's own length is
    // conveyed by the rung that locates its far shoulder (its `step_position.length`, or the
    // overall length for the last band); the rungs before it locate its near one. Unless the spec
    // tolerances that length, or omits the rung, it is not drawn again.
    for i in 0..kept.len() {
        let Some(band) = kept[i]
            .0
            .id
            .strip_prefix("d:")
            .and_then(|s| s.strip_suffix("/step.length"))
        else {
            continue;
        };
        if gone.contains(&i)
            || !given[i].is_empty()
            || kept[i].2.is_some_and(|v| crate::detail::NAMES.contains(&v))
        {
            continue;
        }
        let rung = (0..kept.len()).find(|&j| {
            j != i
                && kept[j]
                    .0
                    .measurements
                    .iter()
                    .any(|m| m.feature == band && m.parameter == "step_position.length")
        });
        if let Some(j) = rung {
            cover(
                i,
                j,
                kept,
                omitted,
                "a baseline from the profile's datum locates both shoulders of its band",
            );
            gone.insert(i);
        }
    }
    // A functional outside diameter or A/F statement owns an equal overall span only
    // when its actual support planes reach both ends of that axis.
    for i in 0..kept.len() {
        if gone.contains(&i)
            || !given[i].is_empty()
            || !crate::dims::is_envelope_extent(&kept[i].0.id)
        {
            continue;
        }
        let Some(s) = interval(a, &kept[i].0) else {
            continue;
        };
        let owner = (0..kept.len()).find(|&j| {
            j != i
                && !gone.contains(&j)
                && kept[j].0.measurements.iter().any(|m| {
                    crate::lint::redundancy::external_span(a, &m.feature, &m.parameter, s.axis)
                        .is_some_and(|p| p.same(s))
                })
        });
        if let Some(j) = owner {
            let claims = kept[i].0.measurements.clone();
            kept[j].0.measurements.extend(claims);
            cover(
                i,
                j,
                kept,
                omitted,
                "the functional outside size spans the same support planes",
            );
            gone.insert(i);
        }
    }
    let mut order: Vec<_> = (0..kept.len())
        .filter(|i| !gone.contains(i) && interval(a, &kept[*i].0).is_some())
        .collect();
    order.sort_by(|&i, &j| {
        let rank = |i: usize| {
            let w = &kept[i].0;
            if crate::dims::is_envelope_extent(&w.id) {
                0
            } else if leg(w) {
                4
            } else if w.id.contains("step_level") || w.id.contains("/location") {
                3
            } else {
                2
            }
        };
        rank(i)
            .cmp(&rank(j))
            .then(
                ((kept[j].0.value * 1e6).round() as i64)
                    .cmp(&((kept[i].0.value * 1e6).round() as i64)),
            )
            .then_with(|| {
                super::presentation_id(a, &kept[i].0).cmp(&super::presentation_id(a, &kept[j].0))
            })
    });
    // Stream K6 (ADR4-U1): a boss standing on a face level closes a chain with that level's
    // ladder rung and the overall height, all measured from the part's base (boss_circle: the 9
    // overall, the 6 rung to the plate's top and the 3 boss height above it). For a boss height
    // only, the rungs join the walk as controlling edges, a baseline from the datum, so the boss
    // height it closes becomes the reference. Every other interval keeps its walk without them
    // (slot floors and walls, and a rung a feature states exactly, have their own passes below).
    let rungs: Vec<(Span, usize)> = (0..kept.len())
        .filter(|i| !gone.contains(i) && kept[*i].0.id.starts_with("d:step_level/"))
        .filter_map(|i| {
            ladder_span(a, &kept[i].0)
                .filter(|_| {
                    kept[i].0.measurements.iter().any(|m| {
                        m.feature == "step_level" && m.parameter.starts_with("step_height.length.")
                    })
                })
                .map(|(s, _)| s)
                .map(|s| (s, i))
        })
        .collect();
    // ADR1-8: a through step's leg is walked last, with the ladder's rungs (heights and shoulder
    // positions from the box) among its edges: lint::steps accepts a leg stated by a rung, a
    // plate's thickness or either with the overall extent, so the leg is the member that yields,
    // never the plate, rung or overall that states it too. A rung rebased onto a datum is no
    // edge: lint reads a rung's interval from the box, so it could not see what the leg yielded to.
    let ladder: Vec<(Span, usize)> = (0..kept.len())
        .filter(|i| !gone.contains(i) && kept[*i].0.id.starts_with("d:step_level/"))
        .filter_map(|i| match ladder_span(a, &kept[i].0) {
            Some((s, false)) => Some((s, i)),
            _ => None,
        })
        .collect();
    let mut edges = Vec::new();
    // ADR1-8: a counted or compound label states its members' intervals; they carry chains as
    // controlling statements but never yield (a count or callout is not one interval).
    let compound: Vec<usize> = (0..kept.len())
        .filter(|i| !gone.contains(i) && interval(a, &kept[*i].0).is_none())
        .collect();
    for &i in &compound {
        edges.extend(members(a, &kept[i].0).into_iter().map(|s| (s, i)));
    }
    // Only a label that printed a member counts: a rung, diameter or point location has no
    // interval either, and must not satisfy `rungs_on_compound_labels`' label guard.
    let compound: Vec<usize> = compound
        .into_iter()
        .filter(|i| edges.iter().any(|e| e.1 == *i))
        .collect();
    for i in order {
        // Invariant: kept contains only dimensions for which interval returned Some.
        let s = interval(a, &kept[i].0).unwrap();
        let measurements = &kept[i].0.measurements;
        let boss = !measurements.is_empty()
            && measurements
                .iter()
                .all(|m| m.parameter == "boss_height.length");
        let found = if leg(&kept[i].0) {
            let with: Vec<_> = edges.iter().chain(&ladder).copied().collect();
            path(&with, s)
        } else {
            path(&edges, s).or_else(|| {
                let with: Vec<_> = edges.iter().chain(&rungs).copied().collect();
                path(&with, s).filter(|route| boss && route.len() > 1)
            })
        };
        if let Some(route) = found {
            // A dimension the spec tolerances, or one that states the part's PMI, is controlling,
            // never reference (#33: reference is for redundant, untoleranced dimensions only).
            if !given[i].is_empty()
                || kept[i]
                    .0
                    .measurements
                    .iter()
                    .any(|m| m.feature.starts_with("pmi:"))
            {
                edges.push((s, i));
                continue;
            }
            if route.len() == 1 && given[i].is_empty() && given[route[0]].is_empty() {
                let j = route[0];
                if kept[i]
                    .0
                    .measurements
                    .iter()
                    .any(|m| m.parameter == "thickness.length")
                    && kept[j]
                        .0
                        .measurements
                        .iter()
                        .any(|m| m.parameter == "thickness.length")
                {
                    let body = |w: &Want| {
                        w.measurements
                            .first()
                            .and_then(|m| a.features.iter().find(|f| f.id == m.feature))
                            .and_then(|f| f.parameters.get("body_key"))
                            .filter(|key| !key.is_null())
                    };
                    if body(&kept[i].0).is_none() || body(&kept[i].0) != body(&kept[j].0) {
                        edges.push((s, i));
                        continue;
                    }
                }
                if kept[i].2 != kept[j].2
                    && kept[i].0.measurements.iter().all(|m| {
                        m.feature.starts_with("f:hole:") || m.feature.starts_with("f:hole_pattern:")
                    })
                    && kept[j].0.measurements.iter().all(|m| {
                        m.feature.starts_with("f:hole:") || m.feature.starts_with("f:hole_pattern:")
                    })
                {
                    edges.push((s, i));
                    continue;
                }
                let claims = kept[i].0.measurements.clone();
                kept[j].0.measurements.extend(claims);
                cover(
                    i,
                    j,
                    kept,
                    omitted,
                    "the same physical support-plane interval is already stated",
                );
                gone.insert(i);
            } else {
                // ISSUE-1505-b: a short shoulder is never the member a chain leaves to its total.
                // Of the walk's members (the total aside), the longest one free to yield is.
                let (i, route) =
                    longest_member(a, kept, given, &references, &mut edges, i, s, route);
                let by: Vec<String> = route.iter().map(|j| kept[*j].0.id.clone()).collect();
                let carriers = by.join(", ");
                references.insert(kept[i].0.id.clone(), by);
                let w = &mut kept[i].0;
                w.terms = vec![format!("({})", w.terms.join(" "))];
                w.basis += &format!(
                    "; reference: the stated total and other chain members determine this interval ({carriers})"
                );
            }
        } else {
            edges.push((s, i));
        }
    }
    // A ladder's datum stays controlling unless a supported feature interval
    // states those exact planes. In particular, a slot's reference must own its
    // coincident floor rung too, rather than leaving the closed chain on another view.
    for i in 0..kept.len() {
        if gone.contains(&i)
            || !kept[i].0.id.starts_with("d:step_level/")
            || !given[i].is_empty()
            || kept[i]
                .0
                .measurements
                .iter()
                .any(|m| m.feature.starts_with("pmi:"))
        {
            continue;
        }
        let Some((s, rebased)) = ladder_span(a, &kept[i].0).filter(|_| {
            kept[i].0.measurements.iter().any(|m| {
                m.feature == "step_level" && m.parameter.starts_with("step_height.length.")
            })
        }) else {
            continue;
        };
        if rebased {
            continue;
        }
        let owner = (0..kept.len()).find(|&j| {
            j != i
                && !gone.contains(&j)
                && kept[j].2.is_some()
                && interval(a, &kept[j].0).is_some_and(|p| p.same(s))
        });
        if let Some(j) = owner {
            let claims = kept[i].0.measurements.clone();
            kept[j].0.measurements.extend(claims);
            cover(
                i,
                j,
                kept,
                omitted,
                "the feature interval states the same support planes as this ladder datum",
            );
            gone.insert(i);
            retarget(kept, &mut references, i, j);
        } else if a.features.iter().filter(|f| f.kind == "slot").any(|f| {
            crate::lint::redundancy::span(a, &f.id, "location_slot.length")
                .is_some_and(|p| p.same(s))
        }) && let Some(route) = path(&edges, s)
            && route.len() > 1
            && route.iter().all(|j| kept[*j].2.is_some())
        {
            let by = route.iter().map(|j| kept[*j].0.id.clone()).collect();
            references.insert(kept[i].0.id.clone(), by);
            let w = &mut kept[i].0;
            w.terms = vec![format!("({})", w.terms.join(" "))];
            w.basis +=
                "; reference: the stated overall and slot length determine this floor interval";
        }
    }
    shoulders_on_slot_walls(a, kept, given, &gone, &mut references);
    rungs_on_compound_labels(
        a,
        kept,
        given,
        omitted,
        &compound,
        &edges,
        &mut gone,
        &mut references,
    );
    if spec.sheet.references != Some(true) && !references.is_empty() {
        let mut suppressed = std::collections::BTreeMap::new();
        for (i, (w, ..)) in kept.iter().enumerate() {
            let Some(by) = references.get(&w.id) else {
                continue;
            };
            let explicit = spec
                .dimensions
                .get(&w.id)
                .is_some_and(|d| d.style.is_some() || d.place.as_ref().is_some_and(|p| p.pinned));
            if gone.contains(&i) || explicit {
                continue;
            }
            // The station walk also treats coincident supports (within recognition's
            // tolerance) as a zero-edge reference. Credit the dimension stating that
            // datum, rather than leaving an empty determining basis or a bracketed zero.
            let by = if by.is_empty() {
                interval(a, w)
                    .and_then(|target| {
                        edges
                            .iter()
                            .find(|(s, j)| {
                                !gone.contains(j)
                                    && s.axis == target.axis
                                    && ((s.lo - target.lo).abs() <= TOL
                                        || (s.hi - target.lo).abs() <= TOL)
                            })
                            .map(|(_, j)| vec![kept[*j].0.id.clone()])
                    })
                    .unwrap_or_default()
            } else {
                by.clone()
            };
            let Some(owner) = by.first() else { continue };
            suppressed.insert(w.id.clone(), owner.clone());
            let mut basis = format!(
                "conveyed by {}: other stated dimensions determine this interval; dependencies: {}",
                owner,
                by.join(", ")
            );
            if w.basis.contains("Specify's general-tolerance dimension") {
                basis += &format!("; {}", w.basis);
            }
            omitted.push(Omitted {
                id: w.id.clone(),
                label: w.terms[0]
                    .trim_start_matches('(')
                    .trim_end_matches(')')
                    .into(),
                basis,
                state: State::Covered { by: by[0].clone() },
            });
            gone.insert(i);
        }
        // A coincident datum may already have yielded to a now-omitted reference.
        // Keep the covered ledger pointing to a drawn determining dimension.
        for o in omitted.iter_mut() {
            if let State::Covered { by } = &mut o.state {
                while let Some(owner) = suppressed.get(by) {
                    o.basis += &format!("; conveyed by {owner}: determines {by}");
                    by.clone_from(owner);
                }
            }
        }
    }
    let mut i = 0;
    given.retain(|_| {
        let keep = !gone.contains(&i);
        i += 1;
        keep
    });
    let mut i = 0;
    kept.retain(|_| {
        let keep = !gone.contains(&i);
        i += 1;
        keep
    });
    references
}
/// A reference the yielding *i* helped determine is determined by its new owner *j* instead.
fn retarget(kept: &mut [Kept], references: &mut References, i: usize, j: usize) {
    let (from, to) = (kept[i].0.id.clone(), kept[j].0.id.clone());
    for (id, by) in references.iter_mut() {
        if by.contains(&from) {
            by.iter_mut()
                .filter(|x| **x == from)
                .for_each(|x| x.clone_from(&to));
            if let Some(w) = kept.iter_mut().find(|k| &k.0.id == id) {
                w.0.basis = w.0.basis.replace(&from, &to);
            }
        }
    }
}

/// ADR1-8, ADR4-U1: the member intervals a counted (`4× 255`) or compound
/// (`2 × 3.7 × 4.761 DEEP`) label prints, read as lint reads its text.
fn members(analysis: &Analysis, w: &Want) -> Vec<Span> {
    // A hole callout (`⌀6 ↧ 10`) states its blind depth's interval too.
    if w.id.starts_with("d:pmi:")
        || !(w.terms.iter().any(|t| t.contains('×'))
            || w.measurements.iter().any(|m| m.parameter == "bore.depth"))
        || w.terms.first().is_some_and(|t| t.starts_with('('))
    {
        return Vec::new();
    }
    let claims = || {
        w.measurements
            .iter()
            .map(|m| (m.feature.as_str(), m.parameter.as_str()))
    };
    let mut own = crate::lint::redundancy::stated(analysis, &w.terms.join(" "), claims());
    let halves: Vec<Span> = claims()
        .filter(|(_, p)| *p != "slot_width.length")
        .flat_map(|(f, p)| crate::lint::redundancy::halves(analysis, f, p, &own))
        .collect();
    own.extend(halves);
    own
}

/// ADR1-8: a face-level rung (`step_height` or `step_position`, from the box) whose two planes a
/// counted or compound label's member closes with the other rungs and stated intervals yields:
/// stating exactly the member's planes it is conveyed by that label (mfcadpp 10063's 24.518 rung
/// and its pocket's 24.518 DEEP), otherwise it is a reference the chain determines (10020's
/// 10.898 and 12.898 rungs on the walls of a 2 wide pocket located to its centre). The rung
/// farther from the box is tried first, so where yielding one rung opens the chain, the nearer
/// stays. A toleranced
/// rung, one stating PMI and a ladder rebased onto a datum stay controlling, as does every rung
/// a chain closes without such a label (their own passes decide those).
#[allow(clippy::too_many_arguments)]
fn rungs_on_compound_labels(
    a: &Analysis,
    kept: &mut [Kept],
    given: &[Vec<String>],
    omitted: &mut Vec<Omitted>,
    compound: &[usize],
    edges: &[(Span, usize)],
    gone: &mut std::collections::BTreeSet<usize>,
    references: &mut References,
) {
    if !edges.iter().any(|e| compound.contains(&e.1)) {
        return;
    }
    let mut ladder: Vec<(usize, Span)> = (0..kept.len())
        .filter(|&i| {
            let w = &kept[i].0;
            !gone.contains(&i)
                && w.id.starts_with("d:step_level/")
                && given[i].is_empty()
                && !w.terms.iter().any(|t| t.starts_with('('))
                && !w.measurements.iter().any(|m| m.feature.starts_with("pmi:"))
                // A rung that determines a reference already (a leg's, a boss height's) stays.
                && !references.values().any(|by| by.contains(&w.id))
                && w.measurements.iter().any(|m| {
                    m.feature == "step_level"
                        && (m.parameter.starts_with("step_height.length.")
                            || m.parameter.starts_with("step_position.length."))
                })
        })
        .filter_map(|i| match ladder_span(a, &kept[i].0) {
            Some((s, false)) => Some((i, s)),
            _ => None,
        })
        .collect();
    ladder.sort_by(|x, y| {
        (y.1.hi - y.1.lo)
            .total_cmp(&(x.1.hi - x.1.lo))
            .then_with(|| {
                super::presentation_id(a, &kept[x.0].0)
                    .cmp(&super::presentation_id(a, &kept[y.0].0))
            })
    });
    let mut live: Vec<(Span, usize)> = ladder.iter().map(|&(i, s)| (s, i)).collect();
    for (i, s) in ladder {
        let with: Vec<_> = edges
            .iter()
            .chain(live.iter().filter(|e| e.1 != i))
            .copied()
            .collect();
        let Some(route) = path(&with, s) else {
            continue;
        };
        if !route.iter().any(|j| compound.contains(j)) {
            continue;
        }
        live.retain(|e| e.1 != i);
        if let [j] = route[..] {
            let claims = kept[i].0.measurements.clone();
            kept[j].0.measurements.extend(claims);
            cover(
                i,
                j,
                kept,
                omitted,
                "the counted or compound label states the same support planes as this ladder rung",
            );
            gone.insert(i);
            retarget(kept, references, i, j);
            continue;
        }
        let by: Vec<String> = route.iter().map(|j| kept[*j].0.id.clone()).collect();
        let carriers = by.join(", ");
        references.insert(kept[i].0.id.clone(), by);
        let w = &mut kept[i].0;
        w.terms = vec![format!("({})", w.terms.join(" "))];
        w.basis += &format!(
            "; reference: a counted or compound label and the other stated intervals determine this rung ({carriers})"
        );
    }
}

/// The member of a closed chain to show as reference, and the stated members that determine it:
/// the longest of the walk's controlling members other than its total (the longest interval of
/// the cycle), when that is longer than *i*, the interval that closed the walk; otherwise *i*.
/// A member can yield only when it is untoleranced, states no PMI, is not an overall extent and
/// determines no reference already made, and only one stating the same parameter as *i*. When
/// one does, *i* takes its place among the edges.
#[allow(clippy::too_many_arguments)]
fn longest_member(
    a: &Analysis,
    kept: &[Kept],
    given: &[Vec<String>],
    references: &References,
    edges: &mut Vec<(Span, usize)>,
    i: usize,
    s: Span,
    route: Vec<usize>,
) -> (usize, Vec<usize>) {
    let length = |j: usize| interval(a, &kept[j].0).map_or(0., |p| p.hi - p.lo);
    let on_edges = |j: usize| edges.iter().any(|e| e.1 == j);
    if !route.iter().all(|&j| on_edges(j)) {
        return (i, route);
    }
    let Some(total) = route
        .iter()
        .copied()
        .chain([i])
        .max_by(|&x, &y| length(x).total_cmp(&length(y)))
    else {
        return (i, route);
    };
    // Only a member of the same kind as *i* (a band's length for a band's, say): a slot's length
    // never yields to its location.
    let kind = |j: usize| kept[j].0.measurements.first().map(|m| m.parameter.as_str());
    let free = |j: usize| {
        kind(j) == kind(i)
            && given[j].is_empty()
            && !crate::dims::is_envelope_extent(&kept[j].0.id)
            && !kept[j]
                .0
                .measurements
                .iter()
                .any(|m| m.feature.starts_with("pmi:"))
            && !references.values().any(|by| by.contains(&kept[j].0.id))
    };
    let pick = route
        .iter()
        .copied()
        .filter(|&j| j != total && free(j) && length(j) > length(i) + TOL)
        .max_by(|&x, &y| {
            length(x).total_cmp(&length(y)).then_with(|| {
                super::presentation_id(a, &kept[y].0).cmp(&super::presentation_id(a, &kept[x].0))
            })
        });
    let Some(pick) = pick else {
        return (i, route);
    };
    let Some(target) = interval(a, &kept[pick].0) else {
        return (i, route);
    };
    let before = edges.clone();
    edges.retain(|e| e.1 != pick);
    edges.push((s, i));
    match path(edges, target) {
        Some(mut found) if found.len() > 1 => {
            // The total first: it is the dimension the omitted member is conveyed by.
            found.sort_by_key(|&j| j != total);
            (pick, found)
        }
        _ => {
            *edges = before;
            (i, route)
        }
    }
}

/// Ladder shoulders standing on a slot's walls close against the slot's stated width: with its
/// centre-line location also stated, every such shoulder is determined; otherwise, of two
/// shoulders on both walls, the farther is. A determined shoulder is shown as reference.
fn shoulders_on_slot_walls(
    a: &Analysis,
    kept: &mut [Kept],
    given: &[Vec<String>],
    gone: &std::collections::BTreeSet<usize>,
    references: &mut References,
) {
    use crate::lint::redundancy::slot_walls;
    let reference = |w: &Want| w.terms.len() == 1 && w.terms[0].starts_with('(');
    let stated = |kept: &[Kept], feature: &str, parameter: &str| {
        (0..kept.len()).find(|&j| {
            !gone.contains(&j)
                && !reference(&kept[j].0)
                && kept[j]
                    .0
                    .measurements
                    .iter()
                    .any(|m| m.feature == feature && m.parameter == parameter)
        })
    };
    for f in a.features.iter().filter(|f| f.kind == "slot") {
        let Some((walls, _)) = slot_walls(a, &f.id) else {
            continue;
        };
        let Some(width) = stated(kept, &f.id, "slot_width.length") else {
            continue;
        };
        let across = stated(kept, &f.id, "location_slot_across.length");
        // Every retained statement of a wall's station from the box: (index, station, a ladder item).
        let mut rungs: Vec<(usize, f64, bool)> = (0..kept.len())
            .filter(|&i| !gone.contains(&i) && !reference(&kept[i].0))
            .filter_map(|i| {
                let s = kept[i]
                    .0
                    .measurements
                    .iter()
                    .filter(|m| m.feature == "step_level" && walked(a, &m.feature))
                    .find_map(|m| crate::lint::redundancy::span(a, &m.feature, &m.parameter))?;
                let ladder = kept[i].0.id.starts_with("d:step_level/") && given[i].is_empty();
                (s.axis == walls.axis
                    && (s.lo - a.envelope[0][s.axis]).abs() <= TOL
                    && [walls.lo, walls.hi].iter().any(|w| (s.hi - w).abs() <= TOL))
                .then_some((i, s.hi, ladder))
            })
            .collect();
        if across.is_none() {
            let on = |w: f64| rungs.iter().any(|r| (r.1 - w).abs() <= TOL);
            if !(on(walls.lo) && on(walls.hi)) {
                continue;
            }
            // The farther wall's ladder item, else the nearer's.
            rungs.sort_by(|x, y| y.1.total_cmp(&x.1));
            rungs.retain(|r| r.2);
            rungs.truncate(1);
        } else {
            rungs.retain(|r| r.2);
        }
        let by: Vec<String> = std::iter::once(width)
            .chain(across)
            .map(|j| kept[j].0.id.clone())
            .collect();
        let carriers = by.join(", ");
        for (i, ..) in rungs {
            references.insert(kept[i].0.id.clone(), by.clone());
            let w = &mut kept[i].0;
            w.terms = vec![format!("({})", w.terms.join(" "))];
            w.basis += &format!(
                "; reference: the slot's stated width and location determine this shoulder on its wall ({carriers})"
            );
        }
    }
}

/// Stream K6 (ADR1-8, ISSUE-1154-a, draftwright's authored-set parity, epic #964): an authored
/// set decides which dimensions are drawn, not where the geometry states a fact. An item left out
/// of the set that the defaults would have handed to an owner the set keeps (*defaults* and
/// *unused*: the default consolidation of every item, *all*) is recorded as conveyed by that
/// owner, as in the automatic plan, and the owner carries the claims the default owner took from
/// it. So the set an automatic plan draws, authored back, plans the same omissions and claims.
/// An owner the set leaves out, or one printing other than its default text, takes nothing: the
/// item stays `not_authored`.
pub(super) fn authored_parity(
    all: &[Want],
    defaults: &[Kept],
    unused: &[Omitted],
    kept: &mut [Kept],
    omitted: &mut [Omitted],
) {
    for o in omitted.iter_mut().filter(|o| o.state == State::NotAuthored) {
        let Some(u) = unused.iter().find(|u| u.id == o.id) else {
            continue;
        };
        let State::Covered { by } = &u.state else {
            continue;
        };
        let Some(owner) = kept.iter_mut().find(|k| &k.0.id == by) else {
            continue;
        };
        let Some(own) = all.iter().find(|w| w.id == o.id) else {
            continue;
        };
        // Only where the owner prints what it would by default: a count or shared size the
        // defaults gave it (`2× ⌀6`) cannot be claimed by a label that does not print it.
        let Some(default) = defaults.iter().find(|d| &d.0.id == by) else {
            continue;
        };
        if default.0.terms != owner.0.terms {
            continue;
        }
        let taken = default.0.measurements.iter().filter(|m| {
            own.measurements
                .iter()
                .any(|c| c.feature == m.feature && c.parameter == m.parameter)
        });
        for m in taken {
            if !owner
                .0
                .measurements
                .iter()
                .any(|c| c.feature == m.feature && c.parameter == m.parameter)
            {
                owner.0.measurements.push(m.clone());
            }
        }
        o.basis.clone_from(&u.basis);
        o.state = u.state.clone();
    }
}

fn cover(i: usize, j: usize, kept: &[Kept], omitted: &mut Vec<Omitted>, why: &str) {
    let (w, owner) = (&kept[i].0, &kept[j].0);
    omitted.push(Omitted {
        id: w.id.clone(),
        label: w.terms.join(" "),
        basis: format!("conveyed by {}: {why}", owner.id),
        state: State::Covered {
            by: owner.id.clone(),
        },
    });
}
