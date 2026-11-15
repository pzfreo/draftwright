//! Coordinate constraints reconstructed through the ordinary geometry constructors.
use super::*;
use crate::placement_intent::{Place, Side};

pub(crate) struct Recipes {
    wants: Vec<Want>,
}
impl Recipes {
    pub(crate) fn new(a: &Analysis, spec: &Spec, views: &[PlannedView]) -> Self {
        let (mut wants, ..) = pmi_policy::wants(a, spec);
        callouts::apply(a, spec, &mut wants);
        let _ = schemes::apply(a, spec, &mut wants);
        let _ = authored::append(a, spec, &mut wants);
        let _ = profile_angles::apply(a, spec, &mut wants);
        detail_routes(a, &mut wants, views);
        Self { wants }
    }
    /// A recipe is emitted only after every regenerated ink field matches exactly.
    pub(crate) fn encode(
        &self,
        a: &Analysis,
        spec: &Spec,
        view: &PlannedView,
        d: &PlannedDimension,
    ) -> Option<(Place, bool, usize)> {
        let target = d.placed.as_ref()?;
        for initial in [false, true] {
            let mut place = Place {
                view: Some(d.view.clone()),
                pinned: true,
                ..Default::default()
            };
            if d.kind == "linear" {
                for side in [Side::Above, Side::Below, Side::Left, Side::Right] {
                    place.side = Some(side);
                    for lane in 0..32 {
                        place.lane = Some(lane);
                        place.offset = None;
                        let first = self
                            .candidates(a, spec, view, d, &place, initial)
                            .into_iter()
                            .next();
                        let Some(base) = first else {
                            continue;
                        };
                        if base.rotation != target.rotation
                            || base.arrows.first().zip(target.arrows.first()).is_none_or(
                                |(x, y)| {
                                    (x[0][usize::from(base.rotation == 0.)]
                                        - y[0][usize::from(base.rotation == 0.)])
                                    .abs()
                                        > 1e-7
                                },
                            )
                        {
                            continue;
                        }
                        let delta = [0, 1].map(|k| rounded(target.label_at[k] - base.label_at[k]));
                        place.offset = (delta != [0., 0.]).then_some(delta);
                        if let Some(variant) = self
                            .candidates(a, spec, view, d, &place, initial)
                            .iter()
                            .position(|p| p == target)
                        {
                            return Some((place, initial, variant));
                        }
                    }
                }
            } else if d.kind == "callout" {
                let mut elbow = target.lines.first()?[1];
                let at = if initial {
                    let (at, shifts) = view.line_pose.as_ref()?;
                    for delta in shifts.iter().rev() {
                        for k in 0..2 {
                            elbow[k] -= delta[k];
                        }
                    }
                    *at
                } else {
                    view.at
                };
                place.offset = Some([0, 1].map(|k| rounded(elbow[k] - at[k])));
                if let Some(variant) = self
                    .candidates(a, spec, view, d, &place, initial)
                    .iter()
                    .position(|p| p == target)
                {
                    return Some((place, initial, variant));
                }
            }
        }
        None
    }
    pub(crate) fn candidates(
        &self,
        a: &Analysis,
        spec: &Spec,
        view: &PlannedView,
        d: &PlannedDimension,
        place: &Place,
        initial: bool,
    ) -> Vec<Placed> {
        let Some(w) = self.wants.iter().find(|w| w.id == d.id) else {
            return Vec::new();
        };
        let mut w = w.clone();
        w.terms.clone_from(&d.terms);
        if let Some(name) = view.id.strip_prefix("v:") {
            route_sites(a, &mut w, name);
        }
        let mut view = view.clone();
        let shifts = if initial {
            if let Some((at, shifts)) = &view.line_pose {
                view.at = *at;
                shifts.clone()
            } else {
                return Vec::new();
            }
        } else {
            Vec::new()
        };
        let f = Frame::new(a, &view);
        let mut candidates = if d.kind == "linear" {
            let Some(side) = place.side else {
                return Vec::new();
            };
            let side = match side {
                Side::Above => placement::Side::Above,
                Side::Below => placement::Side::Below,
                Side::Left => placement::Side::Left,
                Side::Right => placement::Side::Right,
            };
            let Some(mut p) = placement::place(&f, &w, side, place.lane.unwrap_or(0)) else {
                return Vec::new();
            };
            if let Some(delta) = place.offset {
                p.label_at = [p.label_at[0] + delta[0], p.label_at[1] + delta[1]];
                crate::placement_intent::move_box(&mut p.label_box, delta);
            }
            let mut adjusted = d.clone();
            adjusted.placed = Some(p.clone());
            placement::nearest_shared_witnesses(
                a,
                spec,
                std::slice::from_ref(&view),
                std::slice::from_mut(&mut adjusted),
            );
            let mut result = vec![p];
            if let Some(p) = adjusted.placed
                && !result.contains(&p)
            {
                result.push(p);
            }
            result
        } else if d.kind == "callout" {
            let Some(hint) = place.offset else {
                return Vec::new();
            };
            // Only the bounded producer coordinates are persisted; all ink is fresh.
            let drawable = [
                crate::layout::margin(),
                crate::layout::margin(),
                100000.,
                100000.,
            ];
            leaders::pin_candidates(&f, &w, drawable, hint, view.at)
        } else {
            return Vec::new();
        };
        for p in &mut candidates {
            for delta in &shifts {
                for q in p.lines.iter_mut().chain(&mut p.arrows).flatten() {
                    crate::placement_intent::move_point(q, *delta);
                }
                crate::placement_intent::move_point(&mut p.label_at, *delta);
                crate::placement_intent::move_box(&mut p.label_box, *delta);
            }
        }
        candidates
    }
}

fn rounded(n: f64) -> f64 {
    (n * 10000.).round() / 10000.
}
