//! Placement intent relative to views and feature anchors (ADR R1 invariant 1).
use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::items::{Op, key};
use crate::model::{Analysis, Invalid, Plan, PlannedSheet, PlannedView, Problem, Spec};

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Place {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub view: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub side: Option<Side>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub lane: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub along: Option<Along>,
    /// A pinned callout's bend from its automatic tip, or a linear label from its
    /// canonical label position, `[dx, dy]` in paper mm (y up). Excludes side/lane/along.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub offset: Option<[f64; 2]>,
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub pinned: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Side {
    Above,
    Below,
    Left,
    Right,
}
impl Side {
    pub fn name(self) -> &'static str {
        match self {
            Self::Above => "above",
            Self::Below => "below",
            Self::Left => "left",
            Self::Right => "right",
        }
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum Along {
    Fraction(f64),
    Slot(Slot),
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Slot {
    Start,
    Middle,
    End,
    Outside,
}
impl Along {
    pub fn fraction(self) -> f64 {
        match self {
            Self::Fraction(f) => f,
            Self::Slot(Slot::Start) => 0.0,
            Self::Slot(Slot::Middle) => 0.5,
            Self::Slot(Slot::End) => 1.0,
            Self::Slot(Slot::Outside) => 1.25,
        }
    }
}
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct AnnotationSpec {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub text: Option<crate::label_text::Text>,
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    pub omit: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub place: Option<Place>,
}
/// View alignment is separate from annotation slots: only views have projection origins.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ViewAlign {
    Start,
    Middle,
    End,
    Outside,
    Projection,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ViewPlace {
    /// Another view's name: a view is placed only relative to another view, never to the page
    /// (ADR R1 invariant 1).
    pub relative_to: String,
    pub side: Side,
    /// Clear paper distance between the views’ annotation bands, not their part outlines, in mm.
    #[serde(default = "default_gap")]
    pub gap: f64,
    #[serde(default = "middle")]
    pub align: ViewAlign,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub pinned: bool,
}
fn default_gap() -> f64 {
    10.0
}
fn middle() -> ViewAlign {
    ViewAlign::Middle
}

pub(crate) fn carrier(plan: &Plan, id: &str) -> Option<String> {
    let owner = plan
        .omitted
        .iter()
        .find(|o| o.id == id)
        .and_then(|o| match &o.state {
            crate::items::State::Covered { by } => Some(by.as_str()),
            _ => None,
        })
        .unwrap_or(id);
    plan.dimensions
        .iter()
        .find(|d| d.id == owner)
        .map(|d| d.view.clone())
        .or_else(|| plan.gdt.iter().find(|g| g.id == id).map(|g| g.view.clone()))
}

pub(crate) fn has_pins(s: &Spec) -> bool {
    s.dimensions
        .values()
        .any(|d| d.place.as_ref().is_some_and(|p| p.pinned))
        || s.annotations
            .values()
            .any(|d| d.place.as_ref().is_some_and(|p| p.pinned))
        || s.views
            .values()
            .any(|v| v.place.as_ref().is_some_and(|p| p.pinned))
}
pub(crate) fn check(s: &Spec) -> Result<(), Invalid> {
    let error = |message: String, choices: Vec<String>| Invalid {
        message,
        choices,
        path: None,
        also: Vec::new(),
    };
    for (id, p) in s
        .dimensions
        .iter()
        .filter_map(|(id, d)| Some((id, d.place.as_ref()?)))
        .chain(
            s.annotations
                .iter()
                .filter_map(|(id, d)| Some((id, d.place.as_ref()?))),
        )
    {
        if let Some(v) = &p.view
            && !["front", "plan", "side"].contains(&v.as_str())
            && !crate::detail::NAMES.contains(&v.as_str())
        {
            return Err(error(
                format!("The placement view of {id} must be a principal or detail view."),
                ["front", "plan", "side"].map(String::from).to_vec(),
            ));
        }
        if let Some(o) = p.offset {
            if s.annotations.contains_key(id) {
                return Err(error(
                    format!(
                        "The placement offset of {id} applies only to callouts and linear dimensions."
                    ),
                    ["side", "lane", "along"].map(String::from).to_vec(),
                ));
            }
            if p.side.is_some() || p.lane.is_some() || p.along.is_some() || !p.pinned {
                return Err(error(
                    format!(
                        "The placement offset of {id} is a pinned form of its own: set pinned, and no side, lane or along."
                    ),
                    vec!["offset, pinned".into(), "side, lane, along".into()],
                ));
            }
            if o.iter().any(|v| !v.is_finite() || v.abs() > 1000.0) {
                return Err(error(
                    format!("The placement offset of {id} must be within ±1000 paper mm."),
                    vec!["-1000..1000".into()],
                ));
            }
        }
        if p.lane.is_some_and(|n| n > 1000) {
            return Err(error(
                format!("The placement lane of {id} exceeds 1000."),
                vec!["0..1000".into()],
            ));
        }
        if let Some(Along::Fraction(f)) = p.along
            && (!f.is_finite() || !(0.0..=1.0).contains(&f))
        {
            return Err(error(
                format!("The along fraction of {id} must be between 0 and 1."),
                vec![
                    "0..1".into(),
                    "start".into(),
                    "middle".into(),
                    "end".into(),
                    "outside".into(),
                ],
            ));
        }
    }
    for (name, v) in &s.views {
        if let Some(p) = &v.place {
            if !p.gap.is_finite() || !(0.0..=1000.0).contains(&p.gap) {
                return Err(error(
                    format!("The gap of {name} must be between 0 and 1000 paper mm."),
                    vec!["0..1000".into()],
                ));
            }
            if p.relative_to == *name
                || (!["front", "plan", "side", "iso", "section_aa"]
                    .contains(&p.relative_to.as_str())
                    && !crate::detail::NAMES.contains(&p.relative_to.as_str()))
            {
                return Err(error(
                    format!("The placement anchor of {name} is not another view."),
                    ["front", "plan", "side", "iso", "section_aa"]
                        .into_iter()
                        .filter(|v| *v != name)
                        .map(String::from)
                        .collect(),
                ));
            }
            if p.align == ViewAlign::Projection && !projection_related(name, &p.relative_to) {
                return Err(error(
                    format!(
                        "The alignment of {name} relative to {} cannot be projection; valid choices are start, middle, end, outside.",
                        p.relative_to
                    ),
                    align_choices(name, &p.relative_to)
                        .iter()
                        .map(|s| (*s).into())
                        .collect(),
                ));
            }
            let mut seen = BTreeSet::new();
            let mut at = name.as_str();
            while let Some(p) = s.views.get(at).and_then(|v| v.place.as_ref()) {
                if !seen.insert(at) {
                    return Err(error(
                        format!(
                            "View placement anchors form a cycle at {name}; anchor one of them to a view outside the cycle."
                        ),
                        vec![],
                    ));
                }
                at = &p.relative_to;
            }
        }
    }
    Ok(())
}

fn projection_side(name: &str, anchor: &str, first: bool) -> Option<Side> {
    match (name, anchor, first) {
        ("plan", "front", false) | ("front", "plan", true) => Some(Side::Above),
        ("plan", "front", true) | ("front", "plan", false) => Some(Side::Below),
        ("side", "front", false) | ("front", "side", true) => Some(Side::Right),
        ("side", "front", true) | ("front", "side", false) => Some(Side::Left),
        _ => None,
    }
}

pub(crate) fn projection_related(name: &str, anchor: &str) -> bool {
    projection_side(name, anchor, false).is_some()
}

pub(crate) fn align_choices(name: &str, anchor: &str) -> &'static [&'static str] {
    if projection_related(name, anchor) {
        &["projection", "start", "middle", "end", "outside"]
    } else {
        &["start", "middle", "end", "outside"]
    }
}

fn unconventional(s: &Spec, name: &str, p: &ViewPlace) -> bool {
    let expected = projection_side(
        name,
        &p.relative_to,
        s.sheet.projection.as_deref() == Some("first"),
    );
    matches!(
        (name, p.relative_to.as_str()),
        ("side", "plan") | ("plan", "side")
    ) || expected.is_some_and(|side| {
        p.side != side || !matches!(p.align, ViewAlign::Middle | ViewAlign::Projection)
    })
}

fn overlap(a: [f64; 4], b: [f64; 4]) -> bool {
    a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3]
}
fn shift(v: &mut PlannedView, d: [f64; 2]) {
    // A relation already satisfied within the paper-boundary tolerance keeps its exact frame.
    if d.iter().all(|x| x.abs() <= 1e-9) {
        return;
    }
    v.at = [v.at[0] + d[0], v.at[1] + d[1]];
    v.area = [
        v.area[0] + d[0],
        v.area[1] + d[1],
        v.area[2] + d[0],
        v.area[3] + d[1],
    ];
}
fn problem(id: &str, code: &str, message: String, path: &str) -> Problem {
    Problem {
        id: format!("p:{code}:{id}"),
        code: code.into(),
        severity: "warning".into(),
        message,
        subjects: vec![id.into()],
        fix: vec![if path.starts_with("views/") {
            Op::remove(path)
        } else {
            Op::set(path, json!({}))
        }],
        ..Default::default()
    }
}

/// Explicit positions resolve in dependency order. Pinned carriers retain the canonical frame;
/// unrelated omissions, text edits and recovery cannot change their geometry.
pub(crate) fn views(
    s: &Spec,
    views: &mut [PlannedView],
    canonical: Option<&Plan>,
    sheet: &PlannedSheet,
    obstacles: &[[f64; 4]],
    problems: &mut Vec<Problem>,
) {
    let page = sheet.page;
    for (name, requested) in &s.views {
        if let Some(p) = &requested.place
            && views.iter().any(|v| v.id == format!("v:{name}"))
            && unconventional(s, name, p)
        {
            let mut finding = problem(
                &format!("v:{name}"),
                "projection_relation_infeasible",
                format!(
                    "The authored {name} relation is infeasible in {}-angle projection; principal views must keep their conventional side and aligned projection origins.",
                    if s.sheet.projection.as_deref() == Some("first") {
                        "first"
                    } else {
                        "third"
                    }
                ),
                &format!("views/{name}/place"),
            );
            finding.severity = "error".into();
            finding.refs = vec!["sheet/projection".into()];
            if !problems.iter().any(|q| q.id == finding.id) {
                problems.push(finding);
            }
        }
    }
    if let Some(c) = canonical {
        for v in views.iter_mut() {
            if let Some(base) = c.views.iter().find(|b| b.id == v.id) {
                v.at = base.at;
                v.area = base.area;
                v.scale = base.scale;
            }
        }
    }
    let mut resolved = BTreeSet::new();
    for _ in 0..views.len() {
        for i in 0..views.len() {
            let name = views[i].id.trim_start_matches("v:").to_string();
            let Some(p) = s.views.get(&name).and_then(|v| v.place.as_ref()) else {
                resolved.insert(name);
                continue;
            };
            if resolved.contains(&name) {
                continue;
            }
            if s.views
                .get(&p.relative_to)
                .is_some_and(|v| v.place.is_some())
                && !resolved.contains(&p.relative_to)
            {
                continue;
            }
            let Some(anchor) = views
                .iter()
                .find(|v| v.id == format!("v:{}", p.relative_to))
            else {
                continue;
            };
            let b = anchor.area;
            let a = views[i].area;
            let horizontal = matches!(p.side, Side::Left | Side::Right);
            let k = usize::from(horizontal);
            let principal_pair = projection_related(&name, &p.relative_to);
            // Retain middle's historical principal-view behavior for saved Specs.
            let offset = if principal_pair && p.align == ViewAlign::Middle {
                anchor.at[k] - views[i].at[k]
            } else {
                match p.align {
                    ViewAlign::Start => b[k] - a[k],
                    ViewAlign::Middle => 0.5 * (b[k] + b[k + 2] - a[k] - a[k + 2]),
                    ViewAlign::End | ViewAlign::Outside => b[k + 2] - a[k + 2],
                    ViewAlign::Projection => anchor.at[k] - views[i].at[k],
                }
            };
            let out = match p.side {
                Side::Above => b[3] + p.gap - a[1],
                Side::Below => b[1] - p.gap - a[3],
                Side::Right => b[2] + p.gap - a[0],
                Side::Left => b[0] - p.gap - a[2],
            };
            let exact = |d: f64| if d.abs() < 1e-9 { 0.0 } else { d };
            let previous = views[i].at;
            shift(
                &mut views[i],
                if horizontal {
                    [exact(out), exact(offset)]
                } else {
                    [exact(offset), exact(out)]
                },
            );
            if name == "front" {
                let delta = [0, 1].map(|k| views[i].at[k] - previous[k]);
                for partner in views
                    .iter_mut()
                    .filter(|v| matches!(v.id.as_str(), "v:plan" | "v:side"))
                {
                    let is_plan = partner.id == "v:plan";
                    let key = partner.id.trim_start_matches("v:");
                    let pinned = s.views.get(key).is_some_and(|v| v.place.is_some())
                        || canonical.is_some_and(|p| {
                            s.dimensions
                                .iter()
                                .filter_map(|(id, d)| {
                                    d.place.as_ref().filter(|place| place.pinned).map(|_| id)
                                })
                                .chain(s.annotations.iter().filter_map(|(id, d)| {
                                    d.place.as_ref().filter(|place| place.pinned).map(|_| id)
                                }))
                                .any(|id| carrier(p, id).as_deref() == Some(partner.id.as_str()))
                        });
                    if !pinned {
                        shift(
                            partner,
                            if is_plan {
                                [delta[0], 0.0]
                            } else {
                                [0.0, delta[1]]
                            },
                        );
                    }
                }
            }
            // The isometric's area is a reserved region, not a dimensioned projection frame.
            // Resolve its ink inside that region before fixing it, as the automatic fitter does.
            if name == "iso"
                && p.pinned
                && let Some(c) = canonical
            {
                let a = views[i].area;
                let margin = crate::layout::FRAME;
                // A settled automatic iso carries its ink band, not the old empty
                // fitting rectangle. Offer the remaining paper on the authored side
                // before resolving obstacles; the pinned gap still locates that region.
                let region = match p.side {
                    Side::Right | Side::Above => [
                        a[0].max(margin),
                        a[1].max(margin),
                        page[0] - margin,
                        page[1] - margin,
                    ],
                    Side::Left => [
                        margin,
                        a[1].max(margin),
                        a[2].min(page[0] - margin),
                        page[1] - margin,
                    ],
                    Side::Below => [
                        a[0].max(margin),
                        margin,
                        page[0] - margin,
                        a[3].min(page[1] - margin),
                    ],
                };
                let occupied: Vec<_> = c
                    .views
                    .iter()
                    .filter(|v| {
                        v.id != "v:iso"
                            && !(v.id == "v:iso_below"
                                && s.views.get("iso_below").is_none_or(|v| v.place.is_none()))
                    })
                    .map(|v| {
                        if s.views
                            .get(v.id.trim_start_matches("v:"))
                            .is_some_and(|v| v.place.as_ref().is_some_and(|p| p.pinned))
                        {
                            views
                                .iter()
                                .find(|p| p.id == v.id)
                                .map_or(v.area, |p| p.area)
                        } else {
                            v.area
                        }
                    })
                    .chain([sheet.title_block])
                    .chain(
                        c.hole_tables
                            .iter()
                            .flat_map(|t| &t.blocks)
                            .filter_map(|b| b.placed.as_ref().map(|p| p.cells)),
                    )
                    .collect();
                if region[0] < region[2]
                    && region[1] < region[3]
                    && (region != a || occupied.iter().any(|b| overlap(a, *b)))
                {
                    let size = [a[2] - a[0], a[3] - a[1]];
                    let fit = crate::layout::largest_empty_rect(region, &occupied, size);
                    if !occupied.iter().any(|b| overlap(fit, *b)) {
                        let factor = ((fit[2] - fit[0]) / size[0])
                            .min((fit[3] - fit[1]) / size[1])
                            .min(1.0);
                        let v = &mut views[i];
                        v.at = [0, 1].map(|k| {
                            0.5 * (fit[k] + fit[k + 2])
                                + factor * (v.at[k] - 0.5 * (a[k] + a[k + 2]))
                        });
                        v.scale *= factor;
                        // At the established scale the settled ink/caption band already fits.
                        // The larger search rectangle is free room, not occupied ink: keeping
                        // it as the reservation consumes the adjacent schedule's clearance.
                        v.area = if factor >= 1.0 - 1e-9 {
                            let centre = [0, 1].map(|k| 0.5 * (fit[k] + fit[k + 2]));
                            [
                                centre[0] - 0.5 * factor * size[0],
                                centre[1] - 0.5 * factor * size[1],
                                centre[0] + 0.5 * factor * size[0],
                                centre[1] + 0.5 * factor * size[1],
                            ]
                        } else {
                            fit
                        };
                        v.basis += "; isometric ink fitted inside its relative placement region";
                    }
                }
            }
            views[i].default = false;
            views[i].basis += "; placed relative to its anchor by the spec";
            resolved.insert(name);
        }
    }
    // Dragging either pictorial leaves its unpinned partner automatic. Find room using
    // settled peer reservations rather than rejecting a valid editor placement.
    for (name, partner) in [("iso_below", "iso"), ("iso", "iso_below")] {
        if s.views.get(name).is_some_and(|v| v.place.is_some())
            || !s.views.get(partner).is_some_and(|v| v.place.is_some())
        {
            continue;
        }
        let Some(i) = views.iter().position(|v| v.id == format!("v:{name}")) else {
            continue;
        };
        let occupied: Vec<_> = views
            .iter()
            .enumerate()
            .filter(|(j, _)| *j != i)
            .map(|(_, v)| v.area)
            .chain([sheet.title_block])
            .chain(obstacles.iter().copied())
            .collect();
        let region = [
            crate::layout::FRAME,
            crate::layout::FRAME,
            page[0] - crate::layout::FRAME,
            page[1] - crate::layout::FRAME,
        ];
        let a = views[i].area;
        let size = [a[2] - a[0], a[3] - a[1]];
        let fit = crate::layout::largest_empty_rect(region, &occupied, size);
        if fit[2] > fit[0] && fit[3] > fit[1] && !occupied.iter().any(|b| overlap(fit, *b)) {
            let factor = ((fit[2] - fit[0]) / size[0])
                .min((fit[3] - fit[1]) / size[1])
                .min(1.);
            let v = &mut views[i];
            let centre = [0, 1].map(|k| (fit[k] + fit[k + 2]) / 2.);
            v.at = [0, 1].map(|k| centre[k] + factor * (v.at[k] - (a[k] + a[k + 2]) / 2.));
            v.area = [
                centre[0] - factor * size[0] / 2.,
                centre[1] - factor * size[1] / 2.,
                centre[0] + factor * size[0] / 2.,
                centre[1] + factor * size[1] / 2.,
            ];
            v.scale *= factor;
        } else {
            problems.push(problem(&views[i].id, "pictorial_placement_unresolved", format!("No space remains for {name}; move the pinned {partner}, enlarge the paper or omit {name}."), &format!("views/{partner}/place")));
        }
    }
    for (name, v) in &s.views {
        if v.place.is_some() && !resolved.contains(name) {
            let id = format!("v:{name}");
            let mut p = problem(
                &id,
                "placement_anchor_not_shown",
                format!(
                    "The placement anchor of {} is not shown; its intent is retained.",
                    crate::wording::view(&id)
                ),
                &format!("views/{name}/place"),
            );
            if !views.iter().any(|v| v.id == id) {
                p.subjects.clear();
                p.refs.push(id);
            }
            problems.push(p);
        }
    }
    // Relative view pins have a free root when no annotation carrier is held.
    // Translate the connected orthographic group to fit a roomy named sheet,
    // preserving every gap and alignment, rather than centring its root so high
    // that a feasible relation is incorrectly reported off-sheet.
    if s.views.values().any(|v| v.place.is_some())
        && !s
            .dimensions
            .values()
            .any(|d| d.place.as_ref().is_some_and(|p| p.pinned))
        && !s
            .annotations
            .values()
            .any(|a| a.place.as_ref().is_some_and(|p| p.pinned))
    {
        let mut group: BTreeSet<String> = ["front", "plan", "side", "section_aa"]
            .map(String::from)
            .into_iter()
            .collect();
        for _ in 0..s.views.len() {
            for (name, v) in &s.views {
                if v.place
                    .as_ref()
                    .is_some_and(|p| group.contains(&p.relative_to))
                {
                    group.insert(name.clone());
                }
            }
        }
        let members: Vec<_> = views
            .iter()
            .filter(|v| group.contains(v.id.trim_start_matches("v:")))
            .collect();
        let margin = crate::layout::FRAME;
        let mut delta = [0.0; 2];
        for k in 0..2 {
            let lo = members
                .iter()
                .map(|v| v.area[k])
                .fold(f64::INFINITY, f64::min);
            let hi = members
                .iter()
                .map(|v| v.area[k + 2])
                .fold(f64::NEG_INFINITY, f64::max);
            if hi - lo <= page[k] - 2.0 * margin {
                delta[k] = if hi > page[k] - margin {
                    page[k] - margin - hi
                } else if lo < margin {
                    margin - lo
                } else {
                    0.0
                };
            }
        }
        for v in views
            .iter_mut()
            .filter(|v| group.contains(v.id.trim_start_matches("v:")))
        {
            shift(v, delta);
        }
    }
    let mut fixed: BTreeSet<String> = s
        .views
        .iter()
        // An explicit projection relation must also survive automatic obstacle packing.
        .filter(|(_, v)| {
            v.place
                .as_ref()
                .is_some_and(|p| p.pinned || p.align == ViewAlign::Projection)
        })
        .map(|(n, _)| format!("v:{n}"))
        .collect();
    if s.views.values().any(|v| {
        v.place
            .as_ref()
            .is_some_and(|p| p.align == ViewAlign::Projection)
    }) {
        // Front's automatic partners share its projection origins too. Packing one of
        // them independently after moving front would break an otherwise valid relation.
        fixed.extend(
            views
                .iter()
                .filter(|v| matches!(v.id.as_str(), "v:front" | "v:plan" | "v:side"))
                .map(|v| v.id.clone()),
        );
    }
    // Annotation anchors are fixed too, so filling around one cannot move its carrying view.
    if canonical.is_some() {
        for (id, p) in s
            .dimensions
            .iter()
            .filter_map(|(id, d)| Some((id, d.place.as_ref()?)))
            .chain(
                s.annotations
                    .iter()
                    .filter_map(|(id, d)| Some((id, d.place.as_ref()?))),
            )
        {
            if !p.pinned {
                continue;
            }
            let name = p
                .view
                .as_deref()
                .map(|n| format!("v:{n}"))
                .or_else(|| canonical.and_then(|c| carrier(c, id)));
            if let Some(n) = name {
                fixed.insert(n);
            }
        }
    }
    for _ in 0..views.len() {
        for v in views
            .iter()
            .filter(|v| fixed.contains(&v.id))
            .collect::<Vec<_>>()
        {
            if let Some(p) = s
                .views
                .get(v.id.trim_start_matches("v:"))
                .and_then(|v| v.place.as_ref())
            {
                fixed.insert(format!("v:{}", p.relative_to));
            }
        }
    }
    let margin = crate::layout::FRAME;
    // A constrained principal arrangement may consume the orientation budget.
    // Prefer the sheet scale whenever it fits; keep the existing five-percent
    // snapping rule and honour an authored isometric scale exactly.
    if s.iso_scale_factor.is_none()
        && !s.views.contains_key("iso")
        && s.views.iter().any(|(name, v)| {
            ["front", "plan", "side"].contains(&name.as_str())
                && v.place.as_ref().is_some_and(|p| p.pinned)
        })
        && let Some(iso) = views.iter_mut().find(|v| v.id == "v:iso")
        && iso.scale > sheet.scale_factor
    {
        iso.scale = sheet.scale_factor;
    }
    let mut occupied: Vec<[f64; 4]> = views
        .iter()
        .filter(|v| fixed.contains(&v.id))
        .map(|v| v.area)
        .collect();
    occupied.push(sheet.title_block);
    occupied.extend_from_slice(obstacles);
    for v in views.iter_mut().filter(|v| !fixed.contains(&v.id)) {
        if v.id == "v:iso" && occupied.iter().any(|b| overlap(v.area, *b)) {
            let a = v.area;
            let size = [a[2] - a[0], a[3] - a[1]];
            let fit = crate::layout::largest_empty_rect(
                [margin, margin, page[0] - margin, page[1] - margin],
                &occupied,
                size,
            );
            if !occupied.iter().any(|b| overlap(fit, *b)) {
                let factor = ((fit[2] - fit[0]) / size[0])
                    .min((fit[3] - fit[1]) / size[1])
                    .min(1.0);
                let scale = v.scale * factor;
                let scale = if s.iso_scale_factor.is_none() {
                    crate::layout::keep_sheet_scale(scale, sheet.scale_factor)
                } else {
                    scale
                };
                let factor = scale / v.scale;
                v.at = [0, 1].map(|k| {
                    0.5 * (fit[k] + fit[k + 2]) + factor * (v.at[k] - 0.5 * (a[k] + a[k + 2]))
                });
                v.scale = scale;
                v.area = fit;
                v.basis += "; fitted around fixed placement obstacles";
            }
        }
        if occupied.iter().any(|b| overlap(v.area, *b)) {
            let base = v.clone();
            'search: for shrink in 0..if v.id == "v:iso" { 10 } else { 1 } {
                let factor = 0.9_f64.powi(shrink);
                let mut trial = base.clone();
                trial.scale *= factor;
                trial.area = [
                    base.at[0] + factor * (base.area[0] - base.at[0]),
                    base.at[1] + factor * (base.area[1] - base.at[1]),
                    base.at[0] + factor * (base.area[2] - base.at[0]),
                    base.at[1] + factor * (base.area[3] - base.at[1]),
                ];
                let (w, h) = (trial.area[2] - trial.area[0], trial.area[3] - trial.area[1]);
                let mut xs = vec![trial.area[0], margin, page[0] - margin - w];
                let mut ys = vec![trial.area[1], margin, page[1] - margin - h];
                for b in &occupied {
                    xs.extend([b[2] + 1.0, b[0] - w - 1.0]);
                    ys.extend([b[3] + 1.0, b[1] - h - 1.0]);
                }
                let mut candidates: Vec<[f64; 2]> = xs
                    .iter()
                    .flat_map(|x| ys.iter().map(move |y| [*x, *y]))
                    .collect();
                candidates.sort_by(|a, b| {
                    ((a[0] - base.area[0]).abs() + (a[1] - base.area[1]).abs())
                        .total_cmp(&((b[0] - base.area[0]).abs() + (b[1] - base.area[1]).abs()))
                        .then(a[0].total_cmp(&b[0]))
                        .then(a[1].total_cmp(&b[1]))
                });
                for at in candidates {
                    let a = [at[0], at[1], at[0] + w, at[1] + h];
                    if a[0] >= margin
                        && a[1] >= margin
                        && a[2] <= page[0] - margin
                        && a[3] <= page[1] - margin
                        && !occupied.iter().any(|b| overlap(a, *b))
                    {
                        let d = [at[0] - trial.area[0], at[1] - trial.area[1]];
                        shift(&mut trial, d);
                        trial.basis += "; fitted around fixed placement obstacles";
                        *v = trial;
                        break 'search;
                    }
                }
            }
        }
        occupied.push(v.area);
    }
    if let Some(front) = views.iter().find(|v| v.id == "v:front") {
        let first = s.sheet.projection.as_deref() == Some("first");
        for v in views
            .iter()
            .filter(|v| matches!(v.id.as_str(), "v:plan" | "v:side"))
        {
            let align_axis = usize::from(v.id == "v:side");
            let outward_axis = 1 - align_axis;
            let outward =
                (v.at[outward_axis] - front.at[outward_axis]) * if first { -1.0 } else { 1.0 };
            if (v.at[align_axis] - front.at[align_axis]).abs() > 1e-6 || outward <= 0.0 {
                let mut finding = problem(
                    &v.id,
                    "projection_relation_infeasible",
                    format!(
                        "The authored layout is infeasible in {}-angle projection: {} must keep its conventional side and projection-origin alignment with front.",
                        if first { "first" } else { "third" },
                        crate::wording::view(&v.id)
                    ),
                    &format!("views/{}/place", v.id.trim_start_matches("v:")),
                );
                if s.views
                    .get(v.id.trim_start_matches("v:"))
                    .is_none_or(|v| v.place.is_none())
                {
                    finding.fix.clear();
                }
                finding.severity = "error".into();
                finding.refs = vec!["sheet/projection".into()];
                if !problems.iter().any(|q| q.id == finding.id) {
                    problems.push(finding);
                }
            }
        }
    }
    // Explicit relations can move a view off the sheet even without `pinned: true`.
    // Audit every authored view placement, not only the obstacle packer's fixed set.
    for v in views.iter().filter(|v| {
        s.views
            .get(v.id.trim_start_matches("v:"))
            .is_some_and(|v| v.place.is_some())
    }) {
        let a = v.area;
        let path = format!("views/{}/place", v.id.trim_start_matches("v:"));
        // Anchor arithmetic can put a boundary a few ulps beyond the paper margin.
        let tolerance = 1e-9;
        if a[0] < margin - tolerance
            || a[1] < margin - tolerance
            || a[2] > page[0] - margin + tolerance
            || a[3] > page[1] - margin + tolerance
        {
            let mut finding = problem(
                &v.id,
                "pinned_view_off_sheet",
                format!(
                    "{} is outside the drawing area. Remove or adjust its placement to bring it onto the sheet.",
                    crate::wording::capital(&crate::wording::view(&v.id))
                ),
                &path,
            );
            finding.severity = "error".into();
            problems.push(finding);
        }
        if fixed.contains(&v.id)
            && (views
                .iter()
                .any(|other| other.id != v.id && overlap(a, other.area))
                || overlap(a, sheet.title_block))
            && s.views
                .get(v.id.trim_start_matches("v:"))
                .is_some_and(|v| v.place.is_some())
        {
            problems.push(problem(
                &v.id,
                "pinned_view_overlap",
                format!(
                    "{} overlaps another view or the title block and is kept at its pinned position.",
                    crate::wording::capital(&crate::wording::view(&v.id))
                ),
                &path,
            ));
        }
    }
}

pub(crate) fn obstacles(
    s: &Spec,
    dimensions: &[crate::dims::PlannedDimension],
    fixed: &[crate::dims::PlannedDimension],
) -> Vec<[f64; 4]> {
    dimensions
        .iter()
        .filter(|d| {
            s.dimensions
                .get(&d.id)
                .and_then(|e| e.place.as_ref())
                .is_some_and(|p| p.pinned)
        })
        .chain(fixed)
        .filter_map(crate::dims::ink_box)
        .collect()
}

pub(crate) fn finish(plan: &mut Plan, analysis: &Analysis) {
    // Finishing runs again after geometry/table audits. Refresh this owned
    // finding instead of appending a second record with the same ID: collection
    // deltas address findings by ID and must replay the complete plan losslessly.
    plan.problems.retain(|p| p.code != "pinned_item_not_drawn");
    let pins: BTreeMap<String, String> = plan
        .spec
        .dimensions
        .iter()
        .filter(|(_, d)| d.place.as_ref().is_some_and(|p| p.pinned))
        .map(|(id, _)| (id.clone(), format!("dimensions/{}/place", key(id))))
        .chain(
            plan.spec
                .annotations
                .iter()
                .filter(|(_, d)| d.place.as_ref().is_some_and(|p| p.pinned))
                .map(|(id, _)| (id.clone(), format!("annotations/{}/place", key(id)))),
        )
        .collect();
    // A pinned callout is not routed, so the router's report of a leader cutting back into its
    // view's material (`dims::leaders`, ADR2-8) is made here, by the same measure lint's
    // `leader_crosses_silhouette` uses (ISSUE-798-d). Before the loop below, which gives it the
    // unpinning fix; once only, as this runs again after a hole table is chosen.
    for d in plan
        .dimensions
        .iter()
        .filter(|d| d.kind == "callout" && pins.contains_key(&d.id))
    {
        let cut = crate::dims::leader_reentry(analysis, &plan.views, d);
        let subjects = vec![d.id.clone(), d.view.clone()];
        let id = format!("p:annotation_line_over_view:{}", subjects.join("|"));
        if cut > 0.0 && !plan.problems.iter().any(|p| p.id == id) {
            plan.problems.push(Problem {
                id,
                code: "annotation_line_over_view".into(),
                severity: "warning".into(),
                message: format!(
                    "The pinned leader of {} runs back across its own view, {}, cutting {cut:.1} mm of it. Its bend and label are kept at the prescribed position.",
                    crate::wording::item(&d.id, &d.kind, &d.label),
                    crate::wording::view(&d.view)
                ),
                subjects,
                ..Default::default()
            });
        }
    }
    for p in &mut plan.problems {
        if p.code == "leader_through_feature"
            && let Some(id) = p.subjects.first().filter(|id| {
                pins.contains_key(*id)
                    && plan
                        .dimensions
                        .iter()
                        .any(|d| d.id == **id && d.kind == "callout")
            })
        {
            let label = plan
                .dimensions
                .iter()
                .find(|d| d.id == **id)
                .map_or_else(String::new, |d| d.label.clone());
            p.message = format!(
                "The pinned label's leader crosses part features: the leader of {} crosses {}. Its bend and label are kept at the prescribed position.",
                crate::wording::item(id, "callout", &label),
                crate::wording::feature_id(analysis, &p.subjects[1])
            );
            if !p.refs.contains(id) {
                p.refs.push(id.clone());
            }
        }
        if p.code.starts_with("annotation_") {
            if let Some((_, path)) = p.subjects.iter().find_map(|id| pins.get_key_value(id)) {
                p.fix = vec![Op::set(path, json!({}))];
            } else if p.fix.is_empty() {
                if let Some(d) = p
                    .subjects
                    .iter()
                    .find_map(|id| plan.dimensions.iter().find(|d| d.id == *id))
                {
                    if let Some(placed) = &d.placed {
                        let side = if placed.rotation == 90.0 {
                            "left"
                        } else {
                            "above"
                        };
                        // A linear strip change must compose with other fixes:
                        // let layout coordinate lanes and text stations rather
                        // than promise lane zero/outside for every moved item.
                        let place = if d.kind == "linear" {
                            json!({"view":d.view.trim_start_matches("v:"),"side":side})
                        } else {
                            json!({"view":d.view.trim_start_matches("v:"),"side":side,"lane":0,"along":"outside"})
                        };
                        p.fix = vec![Op::set(format!("dimensions/{}/place", key(&d.id)), place)];
                    }
                } else if let Some(g) = p
                    .subjects
                    .iter()
                    .find_map(|id| plan.gdt.iter().find(|g| g.id == *id))
                {
                    let side = match g.side.as_str() {
                        "above" => "below",
                        "below" => "above",
                        "left" => "right",
                        _ => "left",
                    };
                    p.fix = vec![Op::set(
                        format!("annotations/{}/place", key(&g.id)),
                        json!({"view":g.view.trim_start_matches("v:"),"side":side,"lane":0}),
                    )];
                }
            }
        }
    }
    for (id, path) in pins {
        let item = crate::items::items(plan).into_iter().find(|i| i.id == id);
        let named = item.as_ref().map_or_else(
            || "a pinned item no longer in the plan".to_string(),
            |i| crate::wording::item(&id, &i.kind, i.label.as_deref().unwrap_or("")),
        );
        let state = item.map(|i| i.state);
        if !matches!(state, Some(crate::items::State::Shown)) {
            let mut p = problem(
                &id,
                "pinned_item_not_drawn",
                format!(
                    "{} cannot be drawn at its pinned anchor; its placement is retained.",
                    crate::wording::capital(&named)
                ),
                &path,
            );
            if state.is_none() {
                p.subjects.clear();
                p.refs.push(id);
            }
            plan.problems.push(p);
        }
    }
}

/// A side alone is an author constraint on the shared solve, rather than a fixed station.
pub(crate) fn side_hint(p: &Place) -> Option<Side> {
    (!p.pinned && p.lane.is_none() && p.along.is_none() && p.offset.is_none())
        .then_some(p.side)
        .flatten()
}

pub(crate) fn automatic_pin(p: &Place) -> bool {
    p.pinned && p.side.is_none() && p.lane.is_none() && p.along.is_none() && p.offset.is_none()
}

/// Bare dimension pins keep settled peers in their carrier frames. Explicit view
/// relations transport those frames too; they do not change annotation intent.
pub(crate) fn automatic_pins_only(spec: &Spec) -> bool {
    spec.dimensions
        .values()
        .any(|d| d.place.as_ref().is_some_and(automatic_pin))
        && spec
            .dimensions
            .values()
            .all(|d| d.place.as_ref().is_none_or(automatic_pin))
        && spec.annotations.is_empty()
        && spec
            .dimensions
            .values()
            .all(|d| d.tolerance.is_none() && d.text.is_none() && d.style.is_none())
        && spec.features.values().all(|f| f.fit.is_none())
}

/// Whether an offset preserves canonical ink: zero for linear labels, or the
/// automatic bend minus the leader tip for callouts.
pub(crate) fn automatic_offset(base: &Plan, id: &str, offset: [f64; 2]) -> bool {
    if base
        .dimensions
        .iter()
        .any(|d| d.id == id && d.kind == "linear" && d.placed.is_some())
    {
        return offset.iter().all(|v| v.abs() < 1e-6);
    }
    base.dimensions
        .iter()
        .find(|d| d.id == id && d.kind == "callout")
        .and_then(|d| d.placed.as_ref()?.lines.first().copied())
        .is_some_and(|[tip, bend]| {
            (bend[0] - tip[0] - offset[0]).abs() < 1e-6
                && (bend[1] - tip[1] - offset[1]).abs() < 1e-6
        })
}

pub(crate) fn anchor_delta(base: &Plan, views: &[PlannedView], view: &str) -> Option<[f64; 2]> {
    let before = base.views.iter().find(|v| v.id == view)?;
    let after = views.iter().find(|v| v.id == view)?;
    Some([after.at[0] - before.at[0], after.at[1] - before.at[1]])
}

pub(crate) fn move_point(p: &mut [f64; 2], delta: [f64; 2]) {
    if delta != [0.0, 0.0] {
        p[0] += delta[0];
        p[1] += delta[1];
    }
}
pub(crate) fn move_box(b: &mut [f64; 4], delta: [f64; 2]) {
    if delta != [0.0, 0.0] {
        for k in 0..4 {
            b[k] += delta[k % 2];
        }
    }
}

pub(crate) fn held_dimensions(
    base: Option<&Plan>,
    views: &[PlannedView],
) -> Vec<crate::dims::PlannedDimension> {
    let Some(base) = base else {
        return Vec::new();
    };
    base.dimensions
        .iter()
        .filter_map(|d| {
            let delta = anchor_delta(base, views, &d.view)?;
            let mut d = d.clone();
            if let Some(p) = &mut d.placed {
                for line in p.lines.iter_mut().chain(&mut p.arrows) {
                    for q in line {
                        move_point(q, delta);
                    }
                }
                move_point(&mut p.label_at, delta);
                move_box(&mut p.label_box, delta);
            }
            Some(d)
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn issue109_projection_origin_survives_lopsided_bands_and_obstacle_packing() {
        let sheet: PlannedSheet = serde_json::from_value(json!({
            "size":"A3", "page":[420,297], "scale":"1:1", "scale_factor":1,
            "arrangement":"columns", "title_block":[350,10,410,40],
            "default":true, "basis":"test", "state":{"kind":"shown"}, "title":{}
        }))
        .unwrap();
        for pinned in [false, true] {
            for gap in [0., 5., 15., 30., 1000.] {
                let mut pair: Vec<PlannedView> = serde_json::from_str(include_str!(
                    "../tests/fixtures/projection_alignment/issue109.views.json"
                ))
                .unwrap();
                let spec: Spec = serde_json::from_value(json!({"views":{"plan":{"place":{
                    "relative_to":"front", "side":"above", "align":"projection", "gap":gap, "pinned":pinned
                }}}})).unwrap();
                // This obstacle cannot be cleared by sliding the projection off its line.
                views(
                    &spec,
                    &mut pair,
                    None,
                    &sheet,
                    &[[15., 125., 230., 285.]],
                    &mut Vec::new(),
                );
                assert!((pair[1].at[0] - pair[0].at[0]).abs() < 1e-6);
                assert!((pair[1].area[1] - pair[0].area[3] - gap).abs() < 1e-6);
            }
        }
    }

    #[test]
    fn pinned_view_bounds_distinguish_roundoff_from_overruns_on_every_edge() {
        let sheet: PlannedSheet = serde_json::from_value(json!({
            "size":"A4", "page":[210,297], "scale":"1:1", "scale_factor":1,
            "arrangement":"columns", "title_block":[150,10,200,40],
            "default":true, "basis":"test", "state":{"kind":"shown"}, "title":{}
        }))
        .unwrap();
        let view: PlannedView = serde_json::from_value(json!({
            "id":"v:plan", "direction":[0,0,1], "up":[0,1,0], "at":[50,50],
            "scale":1, "area":[10,10,200,287], "default":true,
            "basis":"test", "state":{"kind":"shown"}
        }))
        .unwrap();
        let spec: Spec = serde_json::from_value(json!({"views":{"plan":{"place":{
            "relative_to":"front", "side":"above", "pinned":true
        }}}}))
        .unwrap();
        for edge in 0..4 {
            for (error, off_sheet) in [(0.0, false), (1e-12, false), (1e-4, true)] {
                let mut held = view.clone();
                held.area[edge] += if edge < 2 { -error } else { error };
                let area = held.area;
                let mut problems = Vec::new();
                views(
                    &spec,
                    std::slice::from_mut(&mut held),
                    None,
                    &sheet,
                    &[],
                    &mut problems,
                );
                assert_eq!(held.area, area);
                assert_eq!(
                    problems
                        .iter()
                        .any(|p| p.code == "pinned_view_off_sheet" && p.subjects == ["v:plan"]),
                    off_sheet,
                    "edge {edge}, error {error}"
                );
            }
        }
    }
}
