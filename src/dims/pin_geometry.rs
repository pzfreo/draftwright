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
    ) -> Option<(Place, Option<usize>, usize)> {
        let target = d.placed.as_ref()?;
        let frames = std::iter::once(None).chain(
            view.line_pose
                .iter()
                .flat_map(|(_, shifts)| (0..=shifts.len()).map(Some)),
        );
        for initial in frames {
            let mut place = Place {
                view: Some(d.view.clone()),
                pinned: true,
                ..Default::default()
            };
            if d.kind == "linear" {
                for side in [Side::Above, Side::Below, Side::Left, Side::Right] {
                    place.side = Some(side);
                    for lane in 0..96 {
                        place.lane = Some(lane);
                        place.offset = None;
                        let first = self
                            .construct(a, spec, view, d, &place, (initial, None))
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
                        if let Some(variant) = self
                            .candidates(a, spec, view, d, &place, initial)
                            .iter()
                            .position(|p| p == target)
                        {
                            return Some((place, initial, variant));
                        }
                        let delta = [0, 1].map(|k| rounded(target.label_at[k] - base.label_at[k]));
                        place.offset = (delta != [0., 0.]).then_some(delta);
                        let candidates = self.candidates(a, spec, view, d, &place, initial);
                        if let Some(variant) = candidates.iter().position(|p| p == target) {
                            return Some((place, initial, variant));
                        }
                    }
                }
            } else if d.kind == "angular" {
                for lane in 0..128 {
                    place.lane = Some(lane);
                    if let Some(variant) = self
                        .candidates(a, spec, view, d, &place, initial)
                        .iter()
                        .position(|p| p == target)
                    {
                        return Some((place, initial, variant));
                    }
                }
            } else if d.kind == "callout" {
                let mut elbow = target.lines.first()?[1];
                let (recipe_view, shifts) = recipe_frame(view, initial)?;
                for delta in shifts.iter().rev() {
                    for k in 0..2 {
                        elbow[k] -= delta[k];
                    }
                }
                let at = recipe_view.at;
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
        initial: Option<usize>,
    ) -> Vec<Placed> {
        let mut out = self.construct(a, spec, view, d, place, (initial, None));
        if d.kind == "linear" {
            let count = recipe_frame(view, initial).map_or(0, |(_, shifts)| shifts.len());
            for stage in 0..=count {
                for p in self.construct(a, spec, view, d, place, (initial, Some(stage))) {
                    if !out.contains(&p) {
                        out.push(p);
                    }
                }
            }
        }
        out
    }
    fn construct(
        &self,
        a: &Analysis,
        spec: &Spec,
        view: &PlannedView,
        d: &PlannedDimension,
        place: &Place,
        selection: (Option<usize>, Option<usize>),
    ) -> Vec<Placed> {
        let (initial, label_stage) = selection;
        let Some(w) = self.wants.iter().find(|w| w.id == d.id) else {
            return Vec::new();
        };
        let mut w = w.clone();
        w.terms.clone_from(&d.terms);
        if let Some(name) = view.id.strip_prefix("v:") {
            route_sites(a, &mut w, name);
        }
        let Some((view, shifts)) = recipe_frame(view, initial) else {
            return Vec::new();
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
            let lane = place.lane.unwrap_or(0);
            let candidate = match lane {
                0..32 => placement::place(&f, &w, side, lane),
                32..64 => placement::interior_lanes(&f, &w, side, false)
                    .into_iter()
                    .nth(lane - 32),
                _ => placement::opening_lanes(&f, &w, side)
                    .into_iter()
                    .nth(lane - 64),
            };
            let Some(mut p) = candidate else {
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
        } else if d.kind == "angular" {
            let Shape::Angular { angle, alternates } = &w.shape else {
                return Vec::new();
            };
            angular::pin_candidates(&f, angle, alternates, &w.terms, place.lane.unwrap_or(0))
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
        for stage in 0..=shifts.len() {
            if label_stage == Some(stage) && d.kind == "linear" {
                native_labels(&mut candidates);
            }
            if let Some(delta) = shifts.get(stage) {
                for p in &mut candidates {
                    for q in p.lines.iter_mut().chain(&mut p.arrows).flatten() {
                        crate::placement_intent::move_point(q, *delta);
                    }
                    crate::placement_intent::move_point(&mut p.label_at, *delta);
                    crate::placement_intent::move_box(&mut p.label_box, *delta);
                }
            }
        }
        candidates
    }
}

fn rounded(n: f64) -> f64 {
    (n * 10000.).round() / 10000.
}

fn recipe_frame(view: &PlannedView, initial: Option<usize>) -> Option<(PlannedView, Vec<P2>)> {
    let mut v = view.clone();
    let Some(stage) = initial else {
        return Some((v, Vec::new()));
    };
    let (at, shifts) = view.line_pose.as_ref()?;
    if stage > shifts.len() {
        return None;
    }
    v.at = *at;
    for delta in &shifts[..stage] {
        crate::placement_intent::move_point(&mut v.at, *delta);
    }
    Some((v, shifts[stage..].to_vec()))
}

fn native_labels(out: &mut Vec<Placed>) {
    for base in out.clone() {
        let variants = std::iter::once(label_lanes::outward_heads(&base))
            .chain(label_lanes::centered_short(&base))
            .chain(label_lanes::alternatives_placed(
                &base,
                [-100000., -100000., 100000., 100000.],
                &[],
            ))
            .chain(placement::slides(&base, [-100000., -100000., 100000., 100000.]).take(128));
        for p in variants {
            if !out.contains(&p) {
                out.push(p);
            }
        }
    }
}
