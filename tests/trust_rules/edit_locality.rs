#[path = "../common/conveyed_owners.rs"]
mod ownership;
#[path = "../common/reference_geometry.rs"]
mod reference_geometry;

use std::collections::BTreeMap;

use draftwright::dims::{self, PlannedDimension};
use draftwright::model::{self, Analysis, DimensionSource, DimensionSpec, Plan, Spec};

use super::{ids, omitting, without_note};
use ownership::conveyed_owners;

/// The correlated set (ARCH0015-2) a dimension belongs to: the step-height ladder, or the
/// ladder's shoulders along one axis, as `dims::correlated_set` reads its ID.
fn ladder_set(analysis: &Analysis, id: &str) -> Option<String> {
    if id.starts_with("d:step_level/step_height.length") {
        return Some("heights".into());
    }
    let parameter = id.strip_prefix("d:step_level/")?;
    analysis
        .ladder_axis(parameter)
        .map(|axis| format!("shoulders:{axis}"))
}

/// The placed items by ID.
fn placed(plan: &Plan) -> BTreeMap<&str, &PlannedDimension> {
    plan.dimensions
        .iter()
        .filter(|d| d.placed.is_some())
        .map(|d| (d.id.as_str(), d))
        .collect()
}

/// ARCH0003-2 (edit locality, as R4 translates it: a small spec edit changes only the affected
/// plan items). Omitting a default or authored dimension leaves unrelated items, views, sheet
/// and ink as they were. A blanket radius note depends on its explicit exceptions; losing one
/// withdraws that note and restores the fillet leaders it conveyed. On plate.step this used to fail:
/// lanes were packed in order, so omitting the column pitch moved the x location and the bore
/// callout.
pub(super) fn check(
    name: &str,
    analysis: &Analysis,
    full: &Plan,
    hidden_notes: &mut BTreeMap<String, Plan>,
    mut check_addressability: impl FnMut(&str, &Plan, &mut BTreeMap<String, Plan>),
) -> (usize, usize) {
    let mut edits = 0;
    let mut shared = 0;
    let all = placed(full);
    let authored = |listed: &[&str]| Spec {
        dimension_source: DimensionSource::Authored,
        dimensions: listed
            .iter()
            .map(|id| (id.to_string(), DimensionSpec::default()))
            .collect(),
        ..Spec::default()
    };
    let every = ids(full);
    let conveyed_by = |id: &str| -> Vec<&str> {
        full.omitted
            .iter()
            .filter(|o| conveyed_owners(&o.basis).contains(&id))
            .map(|o| o.id.as_str())
            .collect()
    };
    // The complete set: every item drawn and every item one of them conveys (one span, one
    // dimension: ARCH0009-6), as an author lists them; a shared size counts what is listed.
    let complete: Vec<&str> = every
        .iter()
        .copied()
        .chain(every.iter().flat_map(|i| conveyed_by(i)))
        .collect();
    assert_eq!(
        placed(&model::plan(analysis, &authored(&complete)).unwrap())
            .values()
            .map(|d| (&d.id, &d.placed))
            .collect::<Vec<_>>(),
        all.values().map(|d| (&d.id, &d.placed)).collect::<Vec<_>>(),
        "{name}: the authored complete set is placed as the defaults are"
    );
    for gone in &every {
        let without = model::plan(analysis, &omitting(&[gone])).unwrap();
        check_addressability(gone, &without, hidden_notes);
        let rest: Vec<&str> = every.iter().copied().filter(|i| i != gone).collect();
        // Omitting it brings back a coincident location it conveyed (ARCH0009-6), drawn in
        // its lane, which the item merged away shares (a size it shared among three or more
        // is shared again by the rest, the others conveyed by the one drawn); one left out of
        // an authored set, with what it conveyed, conveys nothing back.
        let conveyed = conveyed_by(gone);
        let listed: Vec<&str> = complete
            .iter()
            .copied()
            .filter(|i| i != gone && !conveyed.contains(i))
            .collect();
        for (how, plan, mut promoted) in [
            ("omitted", without, conveyed),
            (
                "left out of the authored set",
                model::plan(analysis, &authored(&listed)).unwrap(),
                vec![],
            ),
        ] {
            // A correlated set (ARCH0015-2) is approved or omitted whole: in this test it is
            // one item. Omitting a rung omits the rest of its ladder (each named by a
            // correlated_set_whole problem); leaving one out of an authored set that lists
            // the others draws it as before. Every item outside the set stays fixed.
            let siblings: Vec<&str> = every
                .iter()
                .copied()
                .filter(|i| {
                    i != gone
                        && ladder_set(analysis, gone)
                            .is_some_and(|s| ladder_set(analysis, i).as_ref() == Some(&s))
                })
                .collect();
            let whole = |id: &str| {
                plan.problems
                    .iter()
                    .any(|p| p.code == "correlated_set_whole" && p.subjects.iter().any(|s| s == id))
            };
            let rest: Vec<&str> =
                if siblings.is_empty() {
                    rest.clone()
                } else if how == "omitted" {
                    for s in &siblings {
                        assert!(
                            plan.omitted.iter().any(|o| o.id == *s
                                && o.state == draftwright::items::State::OmittedByUser)
                                && whole(s),
                            "{name}: {gone} {how}: {s} is neither drawn nor omitted with its set"
                        );
                    }
                    // What an omitted sibling conveyed comes back, as for the item omitted.
                    for s in &siblings {
                        promoted.extend(conveyed_by(s));
                    }
                    rest.iter()
                        .copied()
                        .filter(|i| !siblings.contains(i))
                        .collect()
                } else {
                    assert!(
                        whole(gone),
                        "{name}: {gone} {how}: its set does not approve it"
                    );
                    every.clone()
                };
            // A blanket radius default depends on its exceptions. Withdrawing it
            // restores the fillet leaders it conveyed; all unrelated ink stays fixed.
            for o in &full.omitted {
                let draftwright::items::State::Covered { by } = &o.state else {
                    continue;
                };
                if !by.starts_with("n:")
                    || !plan.notes.iter().any(|n| {
                        n.id == *by
                            && matches!(n.state, draftwright::items::State::NotDrawable { .. })
                    })
                {
                    continue;
                }
                let Some(d) = plan
                    .dimensions
                    .iter()
                    .find(|d| d.id == o.id && d.placed.is_some())
                else {
                    continue;
                };
                assert!(
                    full.dimensions
                        .iter()
                        .find(|d| d.id == *gone)
                        .unwrap()
                        .measurements
                        .iter()
                        .any(|m| matches!(m.parameter.as_str(), "blend.radius" | "fillet.radius")),
                    "only a radius exception can withdraw a radius default"
                );
                assert!(
                    plan.problems.iter().any(|p| {
                        p.code == "notes_not_placed" && p.subjects.first() == Some(by)
                    })
                );
                assert_eq!(d.label, o.label);
                let hidden = without_note(analysis, by, hidden_notes);
                assert_eq!(
                    d.placed,
                    hidden
                        .dimensions
                        .iter()
                        .find(|d| d.id == o.id)
                        .unwrap()
                        .placed,
                    "restored radius uses its established geometry"
                );
                promoted.push(o.id.as_str());
            }
            edits += 1;
            // Fill describes the remaining drawn items, not placement stability.
            let sheet_geometry = |sheet: &draftwright::model::PlannedSheet| {
                let mut value = serde_json::to_value(sheet).unwrap();
                value.as_object_mut().unwrap().remove("fill");
                value["basis"] = serde_json::Value::String(
                    sheet
                        .basis
                        .split("; drawn-item fill ")
                        .next()
                        .unwrap()
                        .into(),
                );
                value
            };
            assert_eq!(
                sheet_geometry(&plan.sheet),
                sheet_geometry(&full.sheet),
                "{name}: {gone} {how}"
            );
            for old in full
                .views
                .iter()
                .filter(|v| !plan.views.iter().any(|n| n.id == v.id))
            {
                let d = old
                    .detail
                    .as_ref()
                    .expect("only optional recovery furniture may disappear");
                assert!(
                    !d.authored
                        && d.dimensions
                            .iter()
                            .all(|id| plan.dimensions.iter().all(|dim| dim.id != *id))
                );
            }
            assert_eq!(
                plan.views
                    .iter()
                    .map(|v| (&v.id, v.at, v.area))
                    .collect::<Vec<_>>(),
                full.views
                    .iter()
                    .filter(|v| plan.views.iter().any(|n| n.id == v.id))
                    .map(|v| (&v.id, v.at, v.area))
                    .collect::<Vec<_>>(),
                "{name}: {gone} {how}"
            );
            let back: Vec<&str> = ids(&plan)
                .into_iter()
                .filter(|i| promoted.contains(i))
                .collect();
            assert_eq!(back.is_empty(), promoted.is_empty(), "{name}: {gone} {how}");
            for p in promoted.iter().filter(|p| !back.contains(p)) {
                assert!(
                    plan.omitted.iter().any(|o| o.id == *p
                        && back
                            .iter()
                            .any(|b| o.basis.starts_with(&format!("conveyed by {b}: ")))),
                    "{name}: {gone} {how}: {p} is neither drawn nor conveyed by what is"
                );
            }
            assert_eq!(
                ids(&plan)
                    .into_iter()
                    .filter(|i| !promoted.contains(i))
                    .collect::<Vec<_>>(),
                rest,
                "{name}: {gone} {how}"
            );
            for d in &plan.dimensions {
                if promoted.contains(&d.id.as_str()) {
                    let Some(p) = d.placed.as_ref() else {
                        panic!("{name}: {} is not drawn", d.id);
                    };
                    // A previously auxiliary interval becomes controlling when any
                    // determining dimension is withdrawn. It states its own span; the
                    // unrelated items below still retain their exact established ink.
                    if full.omitted.iter().any(|o| {
                        o.id == d.id
                            && o.basis
                                .contains("other stated dimensions determine this interval")
                    }) {
                        assert!(!d.reference, "{name}: {} stayed reference", d.id);
                        let view = plan.views.iter().find(|v| v.id == d.view).unwrap();
                        let (a, b) = (p.arrows[0][0], p.arrows[1][0]);
                        assert!(
                            ((a[0] - b[0]).hypot(a[1] - b[1]) - d.value * view.scale).abs() < 1e-6,
                            "{name}: {} does not state its promoted interval",
                            d.id
                        );
                        continue;
                    }
                    // In the lane of the item it was merged into: its dimension line on that
                    // item's (the merged items hold no lane of their own).
                    let lane = |p: &dims::Placed| {
                        let [a, b] = p.lines[2];
                        if a[0] == b[0] { a[0] } else { a[1] }
                    };
                    // Its survivor: the item it was conveyed by (the omitted one, or with a
                    // correlated set, the sibling omitted with it).
                    let by = full
                        .omitted
                        .iter()
                        .find(|o| o.id == d.id)
                        .and_then(|o| {
                            conveyed_owners(&o.basis)
                                .into_iter()
                                .find(|b| b == gone || siblings.contains(b))
                        })
                        .unwrap_or(gone);
                    if let (true, Some(survivor)) = (d.kind == "linear", all.get(by)) {
                        // An underside derived from a slab and the overall is not a
                        // coincident span. Restore its own front datum-to-level span;
                        // the existing checks still hold every unrelated item fixed.
                        let slab = full
                            .omitted
                            .iter()
                            .find(|o| o.id == d.id)
                            .and_then(|o| match &o.state {
                                draftwright::items::State::Covered { by } => all.get(by.as_str()),
                                _ => None,
                            })
                            .and_then(|owner| {
                                owner
                                    .measurements
                                    .iter()
                                    .find(|m| m.parameter == "thickness.length")
                            })
                            .and_then(|m| analysis.features.iter().find(|f| f.id == m.feature));
                        if let Some(slab) = slab
                            && d.measurements.iter().any(|m| {
                                m.feature == "step_level"
                                    && m.parameter.starts_with("step_height.length.f")
                            })
                        {
                            let slab = draftwright::lint::plates::read(analysis, slab).unwrap();
                            assert_eq!(slab.axis, 2);
                            assert!((slab.span[1][2] - analysis.envelope[1][2]).abs() <= 1e-3);
                            assert!(
                                (d.value - (slab.span[0][2] - analysis.envelope[0][2])).abs()
                                    <= 1e-3
                            );
                            assert_eq!(d.view, "v:front");
                            let view = plan.views.iter().find(|v| v.id == d.view).unwrap();
                            let (a, b) = (p.arrows[0][0], p.arrows[1][0]);
                            assert!(
                                ((a[0] - b[0]).hypot(a[1] - b[1]) - d.value * view.scale).abs()
                                    < 1e-6
                            );
                            continue;
                        }
                        // Counted walls may lie on different axes. On promotion the
                        // remaining wall must state its own interval in an edge-on view.
                        if d.measurements
                            .iter()
                            .any(|m| m.parameter == "thickness.length")
                        {
                            let m = d
                                .measurements
                                .iter()
                                .find(|m| m.parameter == "thickness.length")
                                .unwrap();
                            let feature = analysis
                                .features
                                .iter()
                                .find(|f| f.id == m.feature)
                                .unwrap();
                            let wall = draftwright::lint::plates::read(analysis, feature).unwrap();
                            let view = plan.views.iter().find(|v| v.id == d.view).unwrap();
                            let (a, b) = (p.arrows[0][0], p.arrows[1][0]);
                            assert!(
                                ((a[0] - b[0]).hypot(a[1] - b[1]) - wall.thickness() * view.scale)
                                    .abs()
                                    < 1e-6,
                                "{name}: {} does not state its promoted wall",
                                d.id
                            );
                            if survivor
                                .measurements
                                .iter()
                                .any(|old| old.parameter == "thickness.length")
                            {
                                assert!(
                                    survivor
                                        .measurements
                                        .iter()
                                        .any(|old| old.feature == m.feature
                                            && old.parameter == m.parameter
                                            && old.value == m.value)
                                );
                            }
                            continue;
                        }
                        // An item retried inside its view (its own exterior witness
                        // crossed another feature) holds no exterior lane to hand on: the
                        // promoted item is placed on its own merits, outside.
                        if survivor.region == Some(dims::CandidateRegion::Interior) {
                            continue;
                        }
                        // Nor does a callout in another view that conveyed it (ADR1-8: a
                        // boss height a hole callout's depth states): the promoted item is
                        // placed in its own view, outside.
                        if survivor.kind != "linear"
                            && survivor.view != d.view
                            && conveyed_by(by).contains(&d.id.as_str())
                        {
                            continue;
                        }
                        let survivor = survivor.placed.as_ref().unwrap();
                        assert!(
                            (lane(p) - lane(survivor)).abs() < 1e-9,
                            "{name}: {} is not drawn in {gone}'s lane",
                            d.id
                        );
                        shared += 1;
                    }
                    continue;
                }
                let was = full.dimensions.iter().find(|e| e.id == d.id).unwrap();
                if full.omitted.iter().any(|o| {
                    back.contains(&o.id.as_str())
                        && o.basis
                            .contains("other stated dimensions determine this interval")
                        && conveyed_owners(&o.basis).contains(&d.id.as_str())
                }) {
                    // Restoring an auxiliary interval may reflow the participating
                    // chain's text. Its measured supports and arrows remain fixed.
                    assert_eq!(
                        (d.view.as_str(), d.value, d.label.as_str()),
                        (was.view.as_str(), was.value, was.label.as_str())
                    );
                    let (now, old) = (d.placed.as_ref().unwrap(), was.placed.as_ref().unwrap());
                    assert_eq!(&now.lines[..2], &old.lines[..2], "{} witnesses", d.id);
                    assert_eq!(now.arrows, old.arrows, "{} arrows", d.id);
                    continue;
                }
                // ADR1-8: an item conveyed across views (m10127's boss height, by the hole
                // callout down its axis) comes back in its own view, where the omitted owner
                // held no lane for it; a leader callout there may re-route around its ink.
                // Its view and text stay, and every linear item stays fixed.
                let owner_view = full
                    .dimensions
                    .iter()
                    .find(|e| e.id == *gone)
                    .map(|e| &e.view);
                if d.kind != "linear"
                    && owner_view.is_some_and(|v| *v != d.view)
                    && plan
                        .dimensions
                        .iter()
                        .any(|e| promoted.contains(&e.id.as_str()) && e.view == d.view)
                {
                    assert_eq!(
                        (d.view.as_str(), d.label.as_str()),
                        (was.view.as_str(), was.label.as_str())
                    );
                    continue;
                }
                reference_geometry::assert_local(was, d);
            }
        }
    }
    (edits, shared)
}
