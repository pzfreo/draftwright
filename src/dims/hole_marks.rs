//! Datum feature symbols and feature control frames on cylinders, attached to a size mark or
//! their own rim. For holes, attached to the hole's mark where
//! the hole is seen end-on (#224). Lowering draws a cylinder's datum symbol on its silhouette in
//! the view that sees the cylinder edge-on, and hangs a frame's leader from the centre of its
//! faces ([`crate::pmi_lowering`]); for a hole that silhouette is hidden and that centre is often
//! blank material, so symbols stack on one rim and frames converge on points that do not say
//! which hole they control. ASME Y14.5 and ISO 5459 let a feature of size carry both on its size
//! callout instead, so once the drawing is finished:
//!
//! - **A frame** whose faces are all bores of holes one shown size callout names, and no others,
//!   moves beneath that callout's text in the view that sees the holes end-on, left-aligned with
//!   it and without a leader, as the size callout's own frame (over the text when beneath has no
//!   room); a second frame on the same callout continues the first's stack. A frame on one hole
//!   a table states takes its tag the same way.
//! - **A datum symbol** whose faces are one hole's bore moves onto that hole's mark in the view
//!   that sees it end-on: its own size callout when that callout names this hole alone (a
//!   pattern's `4×` callout would make the whole pattern the datum), else its hole table tag. The
//!   triangle's base stands just off the mark's text, its line running away from the hole, the
//!   frame in the nearest of a few tiers.
//! - **Without a clear place on a mark** (a hole table row replaced the callout, or the callout
//!   has no clear paper beside it), a frame takes a leader to the hole itself in that view
//!   (ASME Y14.5: a frame attached to its feature by a leader): its arrowhead on the bore's
//!   circle, the leader radial to it, then a shelf and the frame; one on several holes leads to
//!   the hole their callout points at and carries their count over it (`4×`, ISO 1101). A datum
//!   symbol without a clear place on its mark stands on a frame so attached to its hole.
//! - **Visible holes first** (ASME Y14.2, ISO 128-22: a leader ends on a visible outline): the
//!   leader goes to a hole the view draws visible. Where none is, the holes' callout kept beside
//!   its table row carries the frame; failing that, the leader goes to one of its holes drawn
//!   hidden, with a `leader_to_hidden_edge` warning. A frame whose leader to its faces ends on
//!   another hole's centre (so it reads as that hole's) does not keep it: it re-aims that leader
//!   at the rim of one of its holes, or takes a new one, either crossing other leaders no more
//!   often than the old one. A second frame on the same holes may stack on the first's leader.
//!
//! The same attachment order applies to a frame on any one principal cylinder (a shaft,
//! boss, turned step or unrecognised bore), including a cylinder split across faces. Its own
//! diameter dimension or callout in any view comes first; otherwise the end-on view takes a
//! radial leader to that cylinder's rim. Visible rim sites precede hidden ones. A leader never
//! runs beside a centre line or through another off-axis cylinder. Frames on different
//! cylinders cannot borrow each other's attachment, even when they share an axis. Datums on
//! these exterior cylinders already stand on their edge-on surface, rather than its axis.
//!
//! Each takes a place only when it is clear of every other item, other views and sheet
//! furniture, on blank paper of its own view, with no finding of its own (a leader to the hole
//! may pass through material no more often than the leader it replaces); otherwise it stays
//! where lowering put it. A place past its view's reserved area alone grows that area, where the
//! grown area stays clear of the margins, the title block, every other view's area and every
//! table, notes block and caption it did not already meet. A frame with an all-around or
//! all-over mark keeps its leader (the mark sits at its bend). One the Spec places in a view or
//! side (`annotations/<id>/place`) is the Spec's, so placing it keeps it on its surface or
//! leader; a bare pin holds the attached ink.

use std::collections::{BTreeMap, BTreeSet};

use super::{P2, PlannedDimension, callout_routes, gdt, placement};
use crate::bridge::holes::Hole;
use crate::model::{Analysis, Plan, Spec};

/// The few tiers a datum symbol tries out from its mark before it stays on its surface.
const TIERS: usize = 3;
/// The gap between a mark's text and the frame or triangle attached to it, paper mm.
const GAP: f64 = 0.5;

/// A hole's mark in its end-on view: its text box, where its leader starts (on the hole), its
/// item ID and what the basis calls it.
#[derive(Clone)]
struct Mark {
    id: String,
    /// The view it stands in.
    view: String,
    label_box: [f64; 4],
    from: P2,
    tag: bool,
    name: String,
}

/// Each automatic frame and datum symbol on holes moved onto the holes' size callout or tag in
/// the view that sees them end-on, where it has a clear place; the problems of what moved are
/// checked again.
pub(crate) fn attach(analysis: &Analysis, spec: &Spec, plan: &mut Plan) {
    if !plan
        .gdt
        .iter()
        .any(|g| matches!(g.kind.as_str(), "datum_feature" | "control_frame") && g.placed.is_some())
    {
        return;
    }
    let read = crate::bridge::holes::read(analysis);
    let holes: Vec<&Hole> = read.iter().filter_map(|(_, h)| h.as_ref().ok()).collect();
    let exterior = super::interior::exterior_proofs(analysis, &plan.views);
    let interior = super::interior::callout_proofs(analysis, &plan.views);
    let cache = super::footprints::Cache::default();
    let mut changed = BTreeSet::new();
    let mut rechecked_codes = BTreeSet::new();
    // The frames already on each callout, so the next one continues their stack.
    let mut stacks: BTreeMap<String, Vec<String>> = BTreeMap::new();
    // The frames attached to each set of holes, which a datum symbol on one of them may take.
    let mut framed: BTreeMap<BTreeSet<&str>, Vec<String>> = BTreeMap::new();
    // Reserve clear size marks before placing any rim leaders: a newly repaired axis-point
    // leader must not occupy the paper a later frame needs beneath its own size callout.
    // Hole marks precede other cylinders' marks; datums then keep clear of every frame.
    #[derive(Clone, Copy)]
    enum Pass {
        Marks,
        Foreign,
        Groups,
        Any,
    }
    for (kind, cylinders, pass) in [
        ("control_frame", false, Pass::Marks),
        ("control_frame", true, Pass::Marks),
        // Never let an axis-point repair take the place a leader pointing at another
        // feature needs. Groups then precede single-cylinder repairs: they have fewer
        // usable circles and their count needs additional paper.
        ("control_frame", false, Pass::Foreign),
        ("control_frame", false, Pass::Groups),
        ("control_frame", false, Pass::Any),
        ("control_frame", true, Pass::Any),
        ("datum_feature", false, Pass::Any),
    ] {
        let marks_only = matches!(pass, Pass::Marks);
        for gi in 0..plan.gdt.len() {
            let g = &plan.gdt[gi];
            if g.kind != kind
                || changed.contains(&g.id)
                || g.placed.is_none()
                || g.state != crate::items::State::Shown
                || !g.modifiers.is_empty()
                // An attached pin replays the published glyph, including its
                // triangle. Reattaching to a newly settled size label moves it.
                || (attached(g)
                    && spec.annotations.get(&g.id).is_some_and(|a| {
                        a.place.as_ref().is_some_and(|p| p.pinned)
                    }))
            {
                continue;
            }
            let frame = kind == "control_frame";
            // Its holes, else (a frame only) the one cylinder of a shaft, boss or bore it is on.
            let (vi, named, rims, cylinder) = match target(analysis, spec, plan, &holes, g) {
                Some((vi, named)) if !cylinders => {
                    let rims = hole_rims(analysis, &plan.views[vi], &holes, &named);
                    (vi, named, rims, false)
                }
                None if cylinders => match cylinder(analysis, spec, plan, g) {
                    Some((vi, named, rim)) => (vi, named, vec![rim], true),
                    None => continue,
                },
                _ => continue,
            };
            let foreign = points_elsewhere(analysis, plan, &holes, &named, g);
            if (matches!(pass, Pass::Foreign) && !foreign)
                || (matches!(pass, Pass::Groups) && named.len() < 2)
            {
                continue;
            }
            let view = &plan.views[vi];
            let others = others(analysis, view, &holes);
            let own = if cylinder {
                cylinder_mark(plan, &named, rims[0].r / view.scale)
            } else {
                mark(plan, &named, &view.id)
            };
            // A datum symbol stands on its hole's mark or on a frame of its hole; a frame may
            // also take a leader to the hole itself.
            if own.is_none() && !frame && !framed.contains_key(&named) {
                continue;
            }
            // The marks to try, in order: the holes' own mark, then (for a datum symbol) a frame
            // already attached to the same hole, which the triangle may stand on (ASME Y14.5,
            // ISO 5459: a datum feature symbol attached to the feature's control frame). A frame
            // may as a last resort stack on another frame's leader to the same holes.
            let mut marks: Vec<Mark> = own.into_iter().collect();
            let mut on_frames: Vec<Mark> = Vec::new();
            for id in framed.get(&named).into_iter().flatten() {
                let Some(q) = plan.gdt.iter().find(|q| &q.id == id) else {
                    continue;
                };
                let Some(p) = q.placed.as_ref() else {
                    continue;
                };
                if frame && (q.side != LEADER || (cylinder && q.faces != g.faces)) {
                    continue;
                }
                on_frames.push(Mark {
                    id: id.clone(),
                    view: q.view.clone(),
                    label_box: p.label_box,
                    from: p.lines.last().map_or(p.tip, |l| l[1]),
                    tag: false,
                    // On a frame that leads to a hidden hole, it is as far from its mark.
                    name: if cylinder {
                        format!(
                            "{CYLINDER_FRAME}{}",
                            if led_to_hidden(q) {
                                CYLINDER_HIDDEN
                            } else {
                                ""
                            }
                        )
                    } else if q.basis.contains(HIDDEN) {
                        format!("{ON_FRAME}{HIDDEN}")
                    } else {
                        ON_FRAME.into()
                    },
                });
            }
            if !frame {
                marks.append(&mut on_frames);
            }
            let mut settled = None;
            // Where its leader to its faces ends on another hole's centre, so that it reads as
            // that hole's frame, or on its own cylinder's axis, which says nothing of which of
            // the circles there it controls, a leader to its own rim may cross as many leaders
            // as that one does.
            let misleading = frame && (foreign || on_axis(view, &rims, g) || rims.len() > 1);
            // A frame without a place on its mark leads to one of its rims the view draws
            // visible (crossing as many leaders as its old one, where that one misleads), else
            // stacks on another frame's leader to them, else (rather than keep a leader to faces
            // that may read as another feature's) leads to one it draws hidden. Every leader
            // stays clear of centre lines and other holes.
            let leaders = |hidden: bool| {
                [false, true]
                    .into_iter()
                    .filter(move |&relaxed| frame && (!relaxed || misleading))
                    .flat_map(move |relaxed| {
                        [false, true].map(move |fine| (None, hidden, relaxed, fine, false))
                    })
            };
            // Keep #251's rim attachments when strict routes cannot fit: the radial shaft
            // may share only its own centre mark's short overrun, as the finished-drawing
            // contact rule permits. It never shares another feature's axis or mark.
            let arm = |hidden| {
                [false, true]
                    .into_iter()
                    .filter(move |_| frame && !cylinder)
                    .map(move |fine| (None, hidden, true, fine, true))
            };
            let steps = marks
                .iter()
                .map(|m| (Some(m), false, false, false, false))
                .chain(leaders(false))
                .chain(arm(false))
                .chain(
                    on_frames
                        .iter()
                        .map(|m| (Some(m), false, false, false, false)),
                )
                .chain(leaders(true))
                .chain(arm(true))
                .filter(|(mark, _, _, _, _)| !marks_only || mark.is_some());
            // A mark in the end-on view orders the holes a leader tries.
            let near = marks.first().filter(|m| m.view == view.id);
            let count = named.len();
            let prefix = format!("ink:{}:", view.id);
            let centres: Vec<_> = view
                .line_work
                .iter()
                .flat_map(|w| {
                    let own = w.kind == "cross"
                        && w.id
                            .strip_prefix(&prefix)
                            .is_some_and(|id| named.contains(id));
                    crate::centrelines::segments(w)
                        .into_iter()
                        .map(move |s| (s, own))
                })
                .collect();
            let leader = Leader {
                view,
                rims: &rims,
                others: &others,
                count,
                centres: &centres,
                own_arm: false,
            };
            for (mark, hidden, relaxed, fine, own_arm) in steps {
                let leader = Leader { own_arm, ..leader };
                // A mark stands in its own view (a cylinder's size callout, in the view that
                // sees it edge-on); a leader to the rim, in the view that sees it end-on.
                let view = mark
                    .and_then(|m| plan.views.iter().find(|v| v.id == m.view))
                    .unwrap_or(view);
                let taken = tips(plan, &view.id, &g.id);
                let candidates: Box<dyn Iterator<Item = (&'static str, gdt::GdtPlaced)> + '_> =
                    match (frame, mark) {
                        (true, Some(mark)) => {
                            Box::new(frame_candidates(plan, &stacks, g, mark).into_iter())
                        }
                        // Its own leader first, ending on one of its rims instead.
                        (true, None) => Box::new(
                            (if misleading && !fine {
                                retargeted(analysis, &leader, g, hidden, &taken)
                            } else {
                                Vec::new()
                            })
                            .into_iter()
                            .chain(leader_candidates(
                                analysis, &leader, g, near, hidden, fine, &taken,
                            )),
                        ),
                        (false, Some(mark)) => Box::new(datum_candidates(g, mark).into_iter()),
                        (false, None) => Box::new(std::iter::empty()),
                    };
                // The mark, and the frames stacked on it, may touch it.
                let mut attached: BTreeSet<String> = BTreeSet::new();
                if let Some(mark) = mark {
                    attached.extend(stacks.get(&mark.id).cloned().unwrap_or_default());
                    attached.insert(mark.id.clone());
                }
                // Its own leader re-aimed, it may still touch what already stood beside it.
                if relaxed && let Some(b) = g.placed.as_ref().map(|p| p.label_box) {
                    attached.extend(
                        plan.gdt
                            .iter()
                            .filter(|q| q.id != g.id && q.view == g.view)
                            .filter(|q| {
                                q.placed.as_ref().is_some_and(|p| {
                                    let pad = [b[0] - 1.0, b[1] - 1.0, b[2] + 1.0, b[3] + 1.0];
                                    placement::boxes_overlap(pad, p.label_box)
                                })
                            })
                            .map(|q| q.id.clone()),
                    );
                }
                if let Some(found) = settle(
                    analysis,
                    plan,
                    gi,
                    view,
                    candidates,
                    &attached,
                    (&exterior, &interior),
                    &cache,
                    relaxed,
                )
                // An explicit zero-gutter request retains its selected reservations for
                // new rim repairs. Automatic layouts keep #251's vacant-paper allowance;
                // size carriers and foreign-feature repairs retain their existing policy.
                .filter(|(_, _, area, _)| {
                    spec.sheet.view_gutter != Some(0.0)
                        || mark.is_some()
                        || foreign
                        || area.is_none()
                }) {
                    settled = Some((mark, hidden, own_arm, view, found));
                    break;
                }
            }
            // A taller size label may have been routed away from the frame's corridor.
            // Before giving each its own leader, search nearby paper for the complete unit.
            let moved_mark = if settled.is_none() && marks_only && !cylinder {
                marks
                    .first()
                    .filter(|m| !m.tag && !stacks.contains_key(&m.id))
                    .and_then(|mark| {
                        joint_mark(
                            analysis,
                            spec,
                            plan,
                            gi,
                            &leader,
                            mark,
                            (&exterior, &interior),
                            &cache,
                        )
                    })
            } else {
                None
            };
            if let Some((_, side, next, area, before)) = &moved_mark {
                settled = Some((
                    marks.first(),
                    false,
                    false,
                    view,
                    (*side, next.clone(), *area, before.clone()),
                ));
            }
            let Some((mark, hidden, own_arm, view, (side, next, area, before))) = settled else {
                continue;
            };
            let view = view.id.clone();
            let id = g.id.clone();
            rechecked_codes.extend(before.into_iter().map(|p| p.code));
            if let Some((next, _, _, _, _)) = moved_mark {
                let mark = mark.expect("a joint placement has a size carrier");
                let d = plan
                    .dimensions
                    .iter_mut()
                    .find(|d| d.id == mark.id)
                    .expect("size carrier");
                d.placed = Some(next.clone());
                d.region = None;
                d.basis = d.basis.replace(super::interior::BASIS, "");
                changed.insert(d.id.clone());
                let v = plan
                    .views
                    .iter()
                    .find(|v| v.id == view)
                    .expect("carrier view");
                plan.callout_tips.insert(
                    d.id.clone(),
                    (
                        view.clone(),
                        [next.lines[0][0][0] - v.at[0], next.lines[0][0][1] - v.at[1]],
                    ),
                );
            }
            let g = &mut plan.gdt[gi];
            g.view.clone_from(&view);
            g.side = side.into();
            g.placed = Some(next);
            let at = view.trim_start_matches("v:");
            g.basis += &match (frame, mark) {
                (true, Some(mark)) if cylinder => {
                    stacks
                        .entry(mark.id.clone())
                        .or_default()
                        .push(g.id.clone());
                    format!(
                        "; {} {} in the {at} view, in place of a leader to the centre of its faces (a view or side in annotations/{}/place keeps the leader)",
                        if side == "below" { "beneath" } else { "over" },
                        mark.name,
                        g.id
                    )
                }
                (true, None) if cylinder => format!(
                    "; on a leader to {CYLINDER_RIM}{} in the {at} view, which sees it end-on, in place of a leader to the centre of its faces (a view or side in annotations/{}/place keeps that leader)",
                    if hidden { CYLINDER_HIDDEN } else { "" },
                    g.id
                ),
                (true, Some(mark)) => {
                    stacks
                        .entry(mark.id.clone())
                        .or_default()
                        .push(g.id.clone());
                    format!(
                        "; {} {} in the {at} view{ATTACHED}s end-on, in place of a leader to its faces (a view or side in annotations/{}/place keeps the leader)",
                        if side == "below" { "beneath" } else { "over" },
                        mark.name,
                        g.id
                    )
                }
                (true, None) => format!(
                    "; on a leader to {}{} in the {at} view{ATTACHED}s end-on, in place of a leader to its faces (a view or side in annotations/{}/place keeps that leader)",
                    if named.len() > 1 {
                        "one of its holes"
                    } else {
                        "its hole"
                    },
                    if hidden { HIDDEN } else { "" },
                    g.id
                ),
                (false, mark) => format!(
                    "; attached to {} in the {at} view{ATTACHED} end-on where its surface is hidden (a view or side in annotations/{}/place keeps it on its surface)",
                    mark.map_or("its mark", |m| m.name.as_str()),
                    g.id
                ),
            };
            if own_arm {
                g.basis += "; only its own centre mark's short rim overrun is shared, rather than retaining an ambiguous leader to its faces";
            }
            // A cylinder of no feature has no name a second glyph could share.
            if frame && !named.is_empty() {
                framed.entry(named.clone()).or_default().push(g.id.clone());
            }
            changed.insert(id);
            if let Some((vi, area)) = area {
                plan.views[vi].area = area;
            }
        }
    }
    if changed.is_empty() {
        return;
    }
    let mut all = plan.dimensions.clone();
    all.extend(plan.gdt.iter().filter_map(gdt::proxy));
    callout_routes::refresh(analysis, plan, &all, &cache, &changed, &rechecked_codes);
}

/// Keep an owned size/depth callout and its frame together when the settled carrier
/// has no room for the frame. The radial route belongs to the size label; the frame
/// has no second leader. Nearby radial routes are bounded and tried lazily.
#[allow(clippy::too_many_arguments, clippy::type_complexity)]
fn joint_mark(
    analysis: &Analysis,
    spec: &Spec,
    plan: &Plan,
    gi: usize,
    leader: &Leader<'_>,
    mark: &Mark,
    proofs: (
        &BTreeMap<String, std::rc::Rc<super::interior::Proof>>,
        &BTreeMap<String, std::rc::Rc<super::interior::Proof>>,
    ),
    cache: &super::footprints::Cache,
) -> Option<(
    super::Placed,
    &'static str,
    gdt::GdtPlaced,
    Option<(usize, [f64; 4])>,
    Vec<crate::model::Problem>,
)> {
    if spec
        .dimensions
        .get(&mark.id)
        .is_some_and(|d| d.place.is_some())
    {
        return None;
    }
    let di = plan.dimensions.iter().position(|d| d.id == mark.id)?;
    let d = &plan.dimensions[di];
    let old = d.placed.as_ref()?;
    let g = &plan.gdt[gi];
    let view = plan.views.iter().find(|v| v.id == mark.view)?;
    let sheet = std::iter::once(&plan.sheet)
        .chain(&plan.sheets)
        .find(|s| s.number == view.sheet)?;
    let mut carrier = g.clone();
    carrier.placed.as_mut()?.label_box = old.label_box;
    let mut taken = tips(plan, &view.id, &g.id);
    // The size carrier is moving with this frame, so its old arrow is not a peer.
    if let Some(shaft) = old.lines.first() {
        taken.retain(|tip| (tip[0] - shaft[0][0]).hypot(tip[1] - shaft[0][1]) > 1e-6);
    }
    let mut all = plan.dimensions.clone();
    all.extend(plan.gdt.iter().filter_map(gdt::proxy));
    let fi = all.iter().position(|q| q.id == g.id)?;
    let geometry = placement::CollisionGeometry::new(analysis, &all, sheet, &plan.views, cache);
    let mut before = geometry.for_item(analysis, &all, sheet, &plan.views, &g.id);
    before.extend(geometry.for_item(analysis, &all, sheet, &plan.views, &d.id));
    all[fi].view.clone_from(&view.id);
    all[di].region = None;
    let peers: Vec<_> = all
        .iter()
        .filter(|q| q.id != g.id && q.id != d.id)
        .filter(|q| {
            plan.views
                .iter()
                .find(|v| v.id == q.view)
                .is_none_or(|v| v.sheet == sheet.number)
        })
        .chain(callout_routes::furniture(plan).iter())
        .cloned()
        .collect();
    let outlines: Vec<_> = plan
        .views
        .iter()
        .filter(|v| v.sheet == sheet.number)
        .map(|v| {
            (
                v.id == view.id,
                placement::Frame::new(analysis, v).outline(),
            )
        })
        .collect();
    let paper = |b| {
        proofs
            .0
            .get(&view.id)
            .is_some_and(|p| p.exterior_clear(view, b, &[], &[]))
            || proofs.1.get(&view.id).is_some_and(|p| p.blank(view, b))
    };
    let attached = BTreeSet::new();
    // A carrier already states the count; a frame attached to it must not repeat it.
    let leader = Leader {
        count: 1,
        ..*leader
    };
    for (_, route) in
        leader_candidates(analysis, &leader, &carrier, Some(mark), false, true, &taken).take(512)
    {
        let delta = [
            route.label_box[0] - old.label_box[0],
            route.label_box[1] - old.label_box[1],
        ];
        let mut size = old.clone();
        size.label_box = route.label_box;
        size.label_at = [old.label_at[0] + delta[0], old.label_at[1] + delta[1]];
        size.lines = route.lines.clone();
        size.arrows = route.arrows.clone();
        // Callout shelves stop a millimetre short of their text.
        let last = size.lines.last_mut()?;
        last[1][0] += if last[1][0] >= last[0][0] { -1.0 } else { 1.0 };
        let next_mark = Mark {
            label_box: size.label_box,
            ..mark.clone()
        };
        all[di].placed = Some(size.clone());
        if !clear(&route, &attached, &peers, &outlines, &paper) {
            continue;
        }
        for (side, next) in frame_candidates(plan, &BTreeMap::new(), g, &next_mark) {
            if !clear(&next, &attached, &peers, &outlines, &paper) {
                continue;
            }
            let proxy = all[fi].placed.as_mut()?;
            proxy.label_box = next.label_box;
            proxy.label_at = [next.label_box[0], next.label_box[1]];
            proxy.lines.clear();
            proxy.arrows.clear();
            let ink = super::ink_box(&all[di])?;
            let frame_ink = super::ink_box(&all[fi])?;
            let union = [
                ink[0].min(frame_ink[0]),
                ink[1].min(frame_ink[1]),
                ink[2].max(frame_ink[2]),
                ink[3].max(frame_ink[3]),
            ];
            let Some(area) = grown(view, union, sheet, &plan.views, &peers) else {
                continue;
            };
            let mut views = plan.views.clone();
            let vi = views.iter().position(|v| v.id == view.id)?;
            views[vi].area = area;
            let problems: Vec<_> = [&d.id, &g.id]
                .into_iter()
                .flat_map(|id| geometry.for_item(analysis, &all, sheet, &views, id))
                .collect();
            if problems.is_empty() {
                return Some((size, side, next, Some((vi, area)), before));
            }
        }
    }
    None
}

/// What a datum symbol standing on its hole's frame calls that frame.
const ON_FRAME: &str = "the control frame of its hole";
/// What the basis of a frame on a leader to a hole its view draws hidden says: the last resort,
/// after its holes' mark ([`attachments`] counts it as not attached).
const HIDDEN: &str = " (drawn hidden: no view shows its holes visible)";
/// What the basis of a frame beneath its cylinder's size callout says.
const CYLINDER_CALLOUT: &str = "its cylinder's size callout";
/// What a frame stacked on the leader of another frame on the same cylinder calls it.
const CYLINDER_FRAME: &str = "the control frame of its cylinder";
/// What the basis of a frame on a leader to its cylinder's rim says.
const CYLINDER_RIM: &str = "its cylinder's rim";
/// What the basis of a frame on a leader to its cylinder's rim drawn hidden says: the last
/// resort, as [`HIDDEN`] for holes.
pub(super) const CYLINDER_HIDDEN: &str = " (drawn hidden: no view shows its rim visible)";
/// The `side` a frame on a leader to its hole settles with.
const LEADER: &str = "leader";
/// How far beyond the hole's rim a leader's bend may stand, paper mm, nearest first.
const REACH: [f64; 11] = [
    4.0, 6.0, 8.0, 11.0, 15.0, 20.0, 27.0, 36.0, 48.0, 64.0, 85.0,
];

/// Where frame *g* may sit on *mark*: beneath its text, or over it when beneath has no room,
/// left-aligned with it, else shifted left no further than right-aligned with it; a second
/// frame continues the first's stack (*stacks*).
fn frame_candidates(
    plan: &Plan,
    stacks: &BTreeMap<String, Vec<String>>,
    g: &gdt::PlannedGdt,
    mark: &Mark,
) -> Vec<(&'static str, gdt::GdtPlaced)> {
    let stacked: Vec<[f64; 4]> = stacks
        .get(&mark.id)
        .into_iter()
        .flatten()
        .filter_map(|id| plan.gdt.iter().find(|q| &q.id == id)?.placed.as_ref())
        .map(|p| p.label_box)
        .collect();
    // A size callout needs clearance from the first frame. A frame on another frame
    // shares its edge instead: CAD draws each native TOLERANCE two DIMTXT tall (5 mm).
    // In particular, keeping GAP here separated FTC-07's independent position rows.
    // Do not turn matching symbols into a composite: AutoCAD and QCAD's ^J layout
    // joins their symbol compartments, changing the meaning of independent controls.
    let frame = plan
        .gdt
        .iter()
        .find(|q| q.id == mark.id)
        .and_then(gdt::frame);
    let b = frame.as_ref().map_or(mark.label_box, |f| {
        let half = haecceity::presentation::gdt::TEXT_HEIGHT;
        [f.at[0], f.at[1] - half, mark.label_box[2], f.at[1] + half]
    });
    let gap = if frame.is_some() { 0.0 } else { GAP };
    let below = stacked.iter().map(|q| q[1]).fold(b[1] - gap, f64::min);
    let above = stacked.iter().map(|q| q[3]).fold(b[3] + gap, f64::max);
    let over = stacked.iter().any(|q| q[1] > b[1]);
    // Invariant: only placed glyphs attach.
    let old = g.placed.as_ref().expect("placed");
    let h = old.label_box[3] - old.label_box[1];
    let w = old.label_box[2] - old.label_box[0];
    // A stack keeps its first frame's left edge.
    let x0 = stacked.first().map_or(b[0], |q| q[0]);
    let under = ("below", beneath(old, [x0, below]));
    let atop = ("above", beneath(old, [x0, above + h]));
    match (stacked.is_empty(), over) {
        (true, _) => {
            // Then shifted left a millimetre at a time, until right-aligned with the text.
            let shifts: Vec<f64> = (1..)
                .map(|k| b[0] - f64::from(k))
                .take_while(|&x| x > b[2] - w)
                .chain((w > b[2] - b[0]).then_some(b[2] - w))
                .collect();
            let mut out = vec![under, atop];
            for (side, y) in [("below", below), ("above", above + h)] {
                out.extend(shifts.iter().map(|&x| (side, beneath(old, [x, y]))));
            }
            out
        }
        (false, false) => vec![under],
        (false, true) => vec![atop],
    }
}

/// Where datum symbol *g* may stand on *mark*: the nearest tier first, wherever along the text.
fn datum_candidates(g: &gdt::PlannedGdt, mark: &Mark) -> Vec<(&'static str, gdt::GdtPlaced)> {
    let letter = g.cells.first().cloned().unwrap_or_default();
    let b = mark.label_box;
    let mut out = Vec::new();
    for side in sides(mark) {
        for tier in 0..TIERS {
            for at in along(mark, side) {
                // The base stands GAP off the text, as a frame beneath it does.
                let tip = match side {
                    "below" => [at, b[1] - GAP],
                    "above" => [at, b[3] + GAP],
                    "left" => [b[0] - GAP, at],
                    _ => [b[2] + GAP, at],
                };
                out.push((side, gdt::datum_at(&letter, tip, side, tier)));
            }
        }
    }
    out
}

/// One of a glyph's own circles where its view sees it end-on: centre and radius on the page,
/// and which of its rim the view draws visible.
struct Rim {
    c: P2,
    r: f64,
    seen: Seen,
    /// Proven circular edge spans for a cylinder trimmed by flats or other surfaces; a
    /// recognised bore is already proved circular. Chord tolerance is in page millimetres.
    trace: Option<(Vec<[P2; 2]>, f64)>,
}

/// Which of a [`Rim`] its view draws visible.
enum Seen {
    /// A hole's bore, seen or not as a whole ([`seen`]).
    Whole(bool),
    /// Another cylinder (a shaft, boss or turned step): seen wherever no planar face lies nearer
    /// the viewer over its rim at *end*, its axis point at the end facing the viewer, radius *r*
    /// (model mm).
    Rim { end: [f64; 3], r: f64 },
}

/// What a frame's leader to its rims in *view*, which sees them end-on, works from: its own
/// circles (*rims*), every hole the view sees end-on at its outermost step (*others*, which a
/// leader through would seem to point at), and how many features it controls (*count*).
#[derive(Clone, Copy)]
struct Leader<'a> {
    view: &'a crate::model::PlannedView,
    rims: &'a [Rim],
    others: &'a [Other],
    count: usize,
    /// Centreline segments and whether each is one of the controlled holes' own arms.
    centres: &'a [([P2; 2], bool)],
    /// Last resort for a hole frame: only the controlled circle's own short arm.
    own_arm: bool,
}

/// The rims of holes *named* in *view*, which sees them end-on: each bore's circle.
fn hole_rims(
    analysis: &Analysis,
    view: &crate::model::PlannedView,
    holes: &[&Hole],
    named: &BTreeSet<&str>,
) -> Vec<Rim> {
    let frame = placement::Frame::new(analysis, view);
    holes
        .iter()
        .filter(|h| named.contains(h.feature.id.as_str()))
        .map(|h| Rim {
            c: frame.page(h.at),
            r: 0.5 * h.diameter * frame.scale(),
            seen: Seen::Whole(seen(analysis, view, h)),
            trace: None,
        })
        .collect()
}

/// A competing circular feature. An enclosing exterior outline is the target's supporting
/// stock or boss, rather than another hole its leader could appear to name.
struct Other {
    c: P2,
    r: f64,
    enclosing: bool,
}

/// Every cylinder *view* sees end-on, including each hole's outermost step.
fn others(analysis: &Analysis, view: &crate::model::PlannedView, holes: &[&Hole]) -> Vec<Other> {
    let frame = placement::Frame::new(analysis, view);
    let axis = (0..3).max_by(|&i, &j| view.direction[i].abs().total_cmp(&view.direction[j].abs()));
    let mut circles: Vec<_> = holes
        .iter()
        .filter(|h| h.principal.map(|(k, _)| k) == axis)
        .map(|h| {
            let outer = h
                .counterbore
                .iter()
                .chain(&h.outer_steps)
                .chain(&h.spotface)
                .map(|r| r.diameter)
                .chain(h.countersink.map(|c| c.diameter))
                .fold(h.diameter, f64::max);
            Other {
                c: frame.page(h.at),
                r: 0.5 * outer * frame.scale(),
                enclosing: false,
            }
        })
        .collect();
    // Other complete cylindrical outlines are competing targets too. A cylindrical patch
    // (a groove or fillet) does not own the imaginary full disc of its supporting cylinder.
    for (fi, a) in analysis.face_attachments.iter().enumerate() {
        let (Some("cylinder"), Some(k), Some(b), Some(p), &[u, v]) = (
            a.surface.as_deref(),
            a.axis,
            a.bounds,
            a.sites.first(),
            a.support.as_slice(),
        ) else {
            continue;
        };
        if Some(k) != axis {
            continue;
        }
        let across: Vec<_> = (0..3).filter(|&j| j != k).collect();
        let r = (p[across[0]] - u).hypot(p[across[1]] - v);
        if across
            .iter()
            .zip([u, v])
            .all(|(&j, c)| b[j] <= c - r + 1e-3 && b[j + 3] >= c + r - 1e-3)
        {
            let mut c = *p;
            c[across[0]] = u;
            c[across[1]] = v;
            circles.push(Other {
                c: frame.page(c),
                r: r * frame.scale(),
                enclosing: analysis.stock_faces.contains(&fi)
                    || analysis.features.iter().any(|f| {
                        matches!(f.kind.as_str(), "boss" | "turned_step")
                            && f.faces.binary_search(&fi).is_ok()
                    }),
            });
        }
    }
    circles
}

/// Whether *tip* lies on a proved span of its cylinder's circle. A clipped cylinder does
/// not own the missing part of that circle (STC-09's open grooves, for example).
fn on_rim(rim: &Rim, tip: P2) -> bool {
    rim.trace
        .as_ref()
        .is_none_or(|(spans, tolerance)| spans.iter().any(|&s| near(s, tip, tolerance - 0.5)))
}

/// Whether *view* draws *rim* visible where a leader leaving its centre along page direction
/// *u* meets it.
fn rim_seen(analysis: &Analysis, view: &crate::model::PlannedView, rim: &Rim, u: P2) -> bool {
    let (end, r) = match rim.seen {
        Seen::Whole(seen) => return seen,
        Seen::Rim { end, r } => (end, r),
    };
    let (Some(ink), Some(camera)) = (
        analysis.edge_ink.as_ref(),
        haecceity::hlr::View::new(view.direction, view.up),
    ) else {
        return true;
    };
    let d = [0, 1, 2].map(|k| u[0] * camera.x[k] + u[1] * camera.y[k]);
    // Just inside and just outside the rim: the edge is hidden only where both are covered.
    [0.98, 1.02].into_iter().any(|f| {
        let p = [0, 1, 2].map(|k| end[k] + f * r * d[k]);
        !covered(ink, &camera, p)
    })
}

/// Whether *s* runs beside one of *view*'s centre lines, even only along its overrun past a
/// rim, so that the two read as one line ([`crate::centrelines::runs_beside`], as the leader
/// router avoids).
fn along_centre(leader: &Leader, centre: P2, radius: f64, s: [P2; 2]) -> bool {
    leader.centres.iter().any(|&(c, own)| {
        let own_arm = leader.own_arm
            && own
            && (0..2).all(|k| (0.5 * (c[0][k] + c[1][k]) - centre[k]).abs() < 1e-6)
            && c.iter().all(|p| {
                (p[0] - centre[0]).hypot(p[1] - centre[1])
                    <= radius + crate::centrelines::overrun(radius) + 1e-6
            });
        (!own_arm && crate::centrelines::runs_beside(s, c))
            || super::contacts::along_centreline(s, c)
    })
}

/// Whether segment *s* from a rim at *c* passes through one of *others* (a hole other than one
/// centred at *c*), so that it seems to point at it.
fn through_others(others: &[Other], c: P2, r: f64, s: [P2; 2]) -> bool {
    others.iter().any(|o| {
        let d = (o.c[0] - c[0]).hypot(o.c[1] - c[1]);
        d > 1e-6 && !(o.enclosing && d + r < o.r) && near(s, o.c, o.r)
    })
}

/// Where frame *g* may sit on a leader to one of its rims (ASME Y14.5: a frame attached to its
/// feature by a leader): the arrowhead on the circle, the leader radial to it out to its bend,
/// then a shelf and the frame. The hole *mark* points at first (so a callout and its frame name
/// the same hole), then the others by distance from it; the nearest bend first, pointing away
/// from the view's middle first. Only rims the view draws visible ([`rim_seen`]) take the
/// leader (ASME Y14.2, ISO 128-22: a leader ends on a visible outline, not on dashed
/// line-work), or with *hidden* only those it draws hidden. No leader runs along a centre line
/// or through another hole.
fn leader_candidates<'a>(
    analysis: &'a Analysis,
    leader: &'a Leader<'_>,
    g: &'a gdt::PlannedGdt,
    mark: Option<&Mark>,
    hidden: bool,
    fine: bool,
    taken: &'a [P2],
) -> Box<dyn Iterator<Item = (&'static str, gdt::GdtPlaced)> + 'a> {
    let Some(old) = g.placed.as_ref() else {
        return Box::new(std::iter::empty());
    };
    let view = leader.view;
    let frame = placement::Frame::new(analysis, view);
    let o = frame.outline();
    let middle = [0.5 * (o[0] + o[2]), 0.5 * (o[1] + o[3])];
    let mut circles: Vec<&Rim> = leader
        .rims
        .iter()
        .filter(|r| !matches!(r.seen, Seen::Whole(seen) if seen == hidden))
        .collect();
    if let Some(m) = mark {
        let d = |c: P2| (c[0] - m.from[0]).hypot(c[1] - m.from[1]);
        circles.sort_by(|a, b| d(a.c).total_cmp(&d(b.c)));
    }
    circles.truncate(3);
    let size = [
        old.label_box[2] - old.label_box[0],
        old.label_box[3] - old.label_box[1],
    ];
    let mut reaches: Vec<_> = REACH.into_iter().map(|r| (r, false)).collect();
    if fine {
        // Try narrow gaps between coarse samples only after ordinary routes fail.
        reaches.extend(REACH.windows(2).map(|w| (0.5 * (w[0] + w[1]), true)));
        reaches.sort_by(|a, b| a.0.total_cmp(&b.0));
    }
    // Build only the next reach's routes. A successful ordinary route never
    // allocates the remaining reaches or the more elaborate rim rails.
    let direct_circles = circles.clone();
    let direct = reaches.into_iter().flat_map(move |(reach, intermediate)| {
        let mut out = Vec::new();
        for rim in &direct_circles {
            let (c, r) = (rim.c, rim.r);
            // Only after the ordinary eight directions fail, try the intermediate rays.
            // A shallow diagonal can clear a centre mark while keeping a frame beside its
            // view; 45 degrees may put it over the part or into the neighbouring view.
            let divisions = if fine { 32 } else { 8 };
            let step = std::f64::consts::FRAC_PI_4 / if fine { 4.0 } else { 1.0 };
            let mut directions: Vec<P2> = (0..divisions)
                .filter(|i| !fine || intermediate || i % 4 != 0)
                .map(|i| {
                    let a = f64::from(i) * step;
                    [a.cos(), a.sin()]
                })
                .collect();
            let away = [c[0] - middle[0], c[1] - middle[1]];
            directions.sort_by(|a, b| {
                (b[0] * away[0] + b[1] * away[1]).total_cmp(&(a[0] * away[0] + a[1] * away[1]))
            });
            for u in directions {
                let tip = [c[0] + r * u[0], c[1] + r * u[1]];
                if !on_rim(rim, tip)
                    || crowded(tip, taken)
                    || rim_seen(analysis, view, rim, u) == hidden
                {
                    continue;
                }
                let elbow = [c[0] + (r + reach) * u[0], c[1] + (r + reach) * u[1]];
                if through_others(leader.others, c, r, [tip, elbow]) {
                    continue;
                }
                if along_centre(leader, c, r, [tip, elbow]) {
                    continue;
                }
                // The shelf runs on away from the hole; a vertical leader's to either side.
                let shelves: &[f64] = if u[0].abs() < 1e-9 {
                    &[1.0, -1.0]
                } else if u[0] > 0.0 {
                    &[1.0]
                } else {
                    &[-1.0]
                };
                for &dir in shelves {
                    let end = [elbow[0] + dir * gdt::SHELF, elbow[1]];
                    let x0 = if dir > 0.0 { end[0] } else { end[0] - size[0] };
                    let mut next = beneath(old, [x0, end[1] + 0.5 * size[1]]);
                    next.tip = tip;
                    next.lines = vec![[tip, elbow], [elbow, end]];
                    next.arrows = vec![placement::arrow(tip, elbow)];
                    // On one hole of several, the frame says how many it controls.
                    if leader.count > 1 {
                        gdt::with_count(&mut next, leader.count);
                    }
                    if !through_others(leader.others, c, r, [elbow, end])
                        && !along_centre(leader, c, r, [elbow, end])
                    {
                        out.push((LEADER, next));
                    }
                }
            }
        }
        out
    });
    // Bound the additional corridor search independently of machine speed.
    // Ordinary radial routes keep their complete, established search order.
    const RAILS_PER_REACH: usize = 1024;
    // Radial exit, then a rail around the view's witness/frame corridor.
    // Existing direct routes remain first and every leg keeps their checks.
    let rails = [4.0, 8.0, 15.0, 27.0]
        .into_iter()
        .filter(move |_| fine)
        .flat_map(move |reach| {
            let mut out = Vec::new();
            for rim in &circles {
                let (c, r) = (rim.c, rim.r);
                for i in 0..32 {
                    let angle = f64::from(i) * std::f64::consts::TAU / 32.0;
                    let u = [angle.cos(), angle.sin()];
                    let tip = [c[0] + r * u[0], c[1] + r * u[1]];
                    let elbow = [c[0] + (r + reach) * u[0], c[1] + (r + reach) * u[1]];
                    if !on_rim(rim, tip)
                        || crowded(tip, taken)
                        || rim_seen(analysis, view, rim, u) == hidden
                        || through_others(leader.others, c, r, [tip, elbow])
                        || along_centre(leader, c, r, [tip, elbow])
                    {
                        continue;
                    }
                    for x in [
                        elbow[0],
                        o[0] - super::GAP - gdt::SHELF,
                        o[2] + super::GAP + gdt::SHELF,
                    ] {
                        let rail = [x, elbow[1]];
                        let mut path = vec![[tip, elbow]];
                        if (x - elbow[0]).abs() > 1e-6 {
                            if through_others(leader.others, c, r, [elbow, rail])
                                || along_centre(leader, c, r, [elbow, rail])
                            {
                                continue;
                            }
                            path.push([elbow, rail]);
                        }
                        for gap in [3.0, 11.0, 27.0] {
                            for y in [o[3] + gap + 0.5 * size[1], o[1] - gap - 0.5 * size[1]] {
                                let bend = [x, y];
                                if (rail[1] - y).abs() < 2.0
                                    || through_others(leader.others, c, r, [rail, bend])
                                    || along_centre(leader, c, r, [rail, bend])
                                {
                                    continue;
                                }
                                for dir in [1.0, -1.0] {
                                    let end = [x + dir * gdt::SHELF, y];
                                    if through_others(leader.others, c, r, [bend, end])
                                        || along_centre(leader, c, r, [bend, end])
                                    {
                                        continue;
                                    }
                                    let x0 = if dir > 0.0 { end[0] } else { end[0] - size[0] };
                                    let mut next = beneath(old, [x0, y + 0.5 * size[1]]);
                                    next.tip = tip;
                                    next.lines = path.clone();
                                    next.lines.extend([[rail, bend], [bend, end]]);
                                    next.arrows = vec![placement::arrow(tip, elbow)];
                                    if leader.count > 1 {
                                        gdt::with_count(&mut next, leader.count);
                                    }
                                    out.push((LEADER, next));
                                    if out.len() == RAILS_PER_REACH {
                                        return out;
                                    }
                                }
                            }
                        }
                    }
                }
            }
            out
        });
    Box::new(direct.chain(rails))
}

/// Whether some planar face of the part lies nearer the viewer of *camera* than point *end*,
/// over it.
fn covered(
    ink: &crate::model::projected_ink::EdgeInk,
    camera: &haecceity::hlr::View,
    end: [f64; 3],
) -> bool {
    let t = camera.toward;
    let dot = |a: [f64; 3], b: [f64; 3]| a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    let q = camera.map(end);
    ink.planes.iter().any(|f| {
        let along = dot(f.normal, t);
        let Some(v0) = f.edges.first().and_then(|&e| ink.edges.get(e)?.first()) else {
            return false;
        };
        if along.abs() < 1e-6 {
            return false;
        }
        // How far toward the viewer the face's plane lies over the end.
        if (dot(f.normal, *v0) - dot(f.normal, end)) / along <= 0.01 {
            return false;
        }
        // Over the end, by the even-odd rule across all its loops' edges.
        let mut inside = false;
        for e in f.edges.iter().filter_map(|&e| ink.edges.get(e)) {
            for w in e.windows(2) {
                let (a, b) = (camera.map(w[0]), camera.map(w[1]));
                if (a[1] > q[1]) != (b[1] > q[1])
                    && q[0] < a[0] + (q[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
                {
                    inside = !inside;
                }
            }
        }
        inside
    })
}

/// Whether *view* sees hole *h* end-on through an open end: no planar face of the part lies
/// nearer the viewer over the bore's axis at its opening (or, for a through hole of known depth,
/// at its exit). The planner has no hidden-line removal (ADR R1), so this tests the stored faces
/// directly; curved faces are not tested, and a part without stored faces counts as seen.
fn seen(analysis: &Analysis, view: &crate::model::PlannedView, h: &Hole) -> bool {
    let Some(ink) = analysis.edge_ink.as_ref() else {
        return true;
    };
    let Some(camera) = haecceity::hlr::View::new(view.direction, view.up) else {
        return true;
    };
    let mut ends = vec![h.at];
    if let (true, Some(depth)) = (h.through, h.depth) {
        ends.push([0, 1, 2].map(|k| h.at[k] + depth * h.direction[k]));
    }
    ends.into_iter().any(|end| !covered(ink, &camera, end))
}

/// Frame *g* where it stands in its leader's view, its leader's first leg ending on one of its
/// rims instead (those the view draws visible there, or with *hidden* those it draws hidden),
/// nearest its old end first, where that leg neither runs along a centre line nor passes
/// through another hole; none when *g* stands in another view.
fn retargeted(
    analysis: &Analysis,
    leader: &Leader,
    g: &gdt::PlannedGdt,
    hidden: bool,
    taken: &[P2],
) -> Vec<(&'static str, gdt::GdtPlaced)> {
    let view = leader.view;
    let Some(old) = g.placed.as_ref().filter(|_| g.view == view.id) else {
        return Vec::new();
    };
    let Some(&[_, next]) = old.lines.first().map(|l| l.as_slice()) else {
        return Vec::new();
    };
    let mut circles: Vec<&Rim> = leader.rims.iter().collect();
    let d = |c: P2| (c[0] - old.tip[0]).hypot(c[1] - old.tip[1]);
    circles.sort_by(|a, b| d(a.c).total_cmp(&d(b.c)));
    // Preserve the frame's existing paper first. Besides shortening its old shaft, try
    // either side of that same frame with a longer horizontal shelf. This gives a radial
    // shaft room to leave a centre line without stealing another size mark's paper.
    let mut routes = vec![(next, old.lines.clone())];
    if old.lines.len() == 2 {
        for reach in REACH {
            for (x, dir) in [(old.label_box[0], -1.0), (old.label_box[2], 1.0)] {
                let end = [x, old.lines[1][1][1]];
                let elbow = [x + dir * reach, end[1]];
                routes.push((elbow, vec![[old.tip, elbow], [elbow, end]]));
            }
        }
    }
    let mut out = Vec::new();
    for rim in circles {
        let (c, r) = (rim.c, rim.r);
        for (next, lines) in &routes {
            let (dx, dy) = (next[0] - c[0], next[1] - c[1]);
            let n = dx.hypot(dy);
            if n <= r {
                continue;
            }
            let u = [dx / n, dy / n];
            let tip = [c[0] + r * u[0], c[1] + r * u[1]];
            if !on_rim(rim, tip)
                || crowded(tip, taken)
                || rim_seen(analysis, view, rim, u) == hidden
            {
                continue;
            }
            if std::iter::once([tip, *next])
                .chain(lines.iter().skip(1).copied())
                .any(|s| through_others(leader.others, c, r, s) || along_centre(leader, c, r, s))
            {
                continue;
            }
            let mut p = old.clone();
            p.tip = tip;
            p.lines.clone_from(lines);
            p.lines[0][0] = tip;
            p.arrows = vec![placement::arrow(tip, *next)];
            if leader.count > 1 {
                gdt::with_count(&mut p, leader.count);
            }
            out.push((LEADER, p));
        }
    }
    out
}

/// Where the leaders of *view*'s items other than *id* end: a second leader ending at one of
/// them would read as the same leader. A frame on the same faces as *id* may share it.
fn tips(plan: &Plan, view: &str, id: &str) -> Vec<P2> {
    let dims = plan
        .dimensions
        .iter()
        .filter(|d| d.view == view && d.id != id)
        .filter_map(|d| Some(d.placed.as_ref()?.lines.first()?[0]));
    let faces = plan.gdt.iter().find(|q| q.id == id).map(|q| &q.faces);
    let frames = plan
        .gdt
        .iter()
        .filter(|q| q.view == view && q.id != id && Some(&q.faces) != faces)
        .filter_map(|q| q.placed.as_ref())
        .filter(|p| !p.lines.is_empty())
        .map(|p| p.tip);
    dims.chain(frames).collect()
}

/// Whether only the leader changed: the frame and its text keep exactly their existing ink.
fn same_glyph(old: &gdt::GdtPlaced, next: &gdt::GdtPlaced) -> bool {
    old.label_box == next.label_box
        && old.strokes == next.strokes
        && old.arcs == next.arcs
        && old.fills == next.fills
        && old.letters == next.letters
        && old.runs == next.runs
}

/// Whether *tip* lies within a millimetre of one of *taken*, the ends of other leaders.
fn crowded(tip: P2, taken: &[P2]) -> bool {
    taken
        .iter()
        .any(|t| (t[0] - tip[0]).hypot(t[1] - tip[1]) < 1.0)
}

/// Whether frame *g*'s leader ends on the centre of a hole other than its own (*named*) that its
/// view sees end-on, so that the frame reads as that hole's (FTC-07's frames on two holes, led
/// to the middle of their faces, on the hole between them).
fn points_elsewhere(
    analysis: &Analysis,
    plan: &Plan,
    holes: &[&Hole],
    named: &BTreeSet<&str>,
    g: &gdt::PlannedGdt,
) -> bool {
    let (Some(p), Some(view)) = (
        g.placed.as_ref(),
        plan.views.iter().find(|v| v.id == g.view),
    ) else {
        return false;
    };
    if p.lines.is_empty() {
        return false;
    }
    let frame = placement::Frame::new(analysis, view);
    let axis = (0..3).max_by(|&i, &j| view.direction[i].abs().total_cmp(&view.direction[j].abs()));
    holes
        .iter()
        .filter(|h| !named.contains(h.feature.id.as_str()))
        .filter(|h| h.principal.map(|(k, _)| k) == axis)
        .any(|h| {
            let c = frame.page(h.at);
            (c[0] - p.tip[0]).hypot(c[1] - p.tip[1]) < 0.3
        })
}

/// Whether frame *g*'s leader in *view* ends within a millimetre of the centre of one of its
/// own *rims*: on the axis, which among concentric circles says none of them.
fn on_axis(view: &crate::model::PlannedView, rims: &[Rim], g: &gdt::PlannedGdt) -> bool {
    g.view == view.id
        && g.placed.as_ref().is_some_and(|p| {
            !p.lines.is_empty()
                && rims
                    .iter()
                    .any(|r| (r.c[0] - p.tip[0]).hypot(r.c[1] - p.tip[1]) < 1.0)
        })
}

/// Whether segment *s* comes within *r* (and a half-millimetre gap) of point *c*.
fn near(s: [P2; 2], c: P2, r: f64) -> bool {
    let d = [s[1][0] - s[0][0], s[1][1] - s[0][1]];
    let l = d[0] * d[0] + d[1] * d[1];
    let t = if l > 0.0 {
        (((c[0] - s[0][0]) * d[0] + (c[1] - s[0][1]) * d[1]) / l).clamp(0.0, 1.0)
    } else {
        0.0
    };
    let p = [s[0][0] + t * d[0], s[0][1] + t * d[1]];
    (p[0] - c[0]).hypot(p[1] - c[1]) < r + 0.5
}

/// Where glyph *gi* of *plan* goes in *view*: the first of *candidates* clear of every item but
/// those it is *attached* to, of other views and of sheet furniture, on blank paper of its own
/// view (*proofs*: its exterior and interior proofs), with no finding of its own, or past its
/// view's reserved area alone where that area can grow (the view and grown area returned); with
/// the findings its old place had, to check again. None when no candidate is clear. A leader to
/// a hole may pass through material as often as the old place did, and *relaxed* (in place of a
/// leader that reads as another feature's) cross other leaders as often too.
#[allow(clippy::type_complexity, clippy::too_many_arguments)]
pub(super) fn settle(
    analysis: &Analysis,
    plan: &Plan,
    gi: usize,
    view: &crate::model::PlannedView,
    candidates: impl IntoIterator<Item = (&'static str, gdt::GdtPlaced)>,
    attached: &BTreeSet<String>,
    proofs: (
        &BTreeMap<String, std::rc::Rc<super::interior::Proof>>,
        &BTreeMap<String, std::rc::Rc<super::interior::Proof>>,
    ),
    cache: &super::footprints::Cache,
    relaxed: bool,
) -> Option<(
    &'static str,
    gdt::GdtPlaced,
    Option<(usize, [f64; 4])>,
    Vec<crate::model::Problem>,
)> {
    let sheet = std::iter::once(&plan.sheet)
        .chain(&plan.sheets)
        .find(|s| s.number == view.sheet)?;
    let (exterior, interior) = proofs;
    // Other views' outlines are closed to it; its own view's only on proven blank paper.
    let outlines: Vec<(bool, [f64; 4])> = plan
        .views
        .iter()
        .filter(|v| v.sheet == sheet.number)
        .filter(|v| {
            let name = v.id.trim_start_matches("v:");
            super::NAMES.contains(&name) || crate::detail::NAMES.contains(&name)
        })
        .map(|v| {
            (
                v.id == view.id,
                placement::Frame::new(analysis, v).outline(),
            )
        })
        .collect();
    let paper = |b: [f64; 4]| {
        exterior
            .get(&view.id)
            .is_some_and(|p| p.exterior_clear(view, b, &[], &[]))
            || interior.get(&view.id).is_some_and(|p| p.blank(view, b))
    };
    let on_sheet = |id: &str| {
        plan.views
            .iter()
            .find(|v| v.id == id)
            .is_none_or(|v| v.sheet == sheet.number)
    };
    let id = &plan.gdt[gi].id;
    let own_triangle = format!("{id}:triangle:");
    let mut all: Vec<PlannedDimension> = plan.dimensions.clone();
    all.extend(plan.gdt.iter().filter_map(gdt::proxy));
    let i = all
        .iter()
        .position(|d| d.id == *id)
        // Invariant: a placed glyph has a proxy, appended above.
        .expect("the glyph's proxy");
    let mut peers: Vec<PlannedDimension> = all
        .iter()
        .enumerate()
        .filter(|(j, q)| *j != i && on_sheet(&q.view))
        .map(|(_, q)| q.clone())
        .collect();
    let annotation_peers = peers.len();
    peers.extend(
        callout_routes::furniture(plan)
            .into_iter()
            .filter(|q| !q.id.starts_with(&own_triangle) && on_sheet(&q.view)),
    );
    let geometry = placement::CollisionGeometry::new(analysis, &all, sheet, &plan.views, cache);
    let before = geometry.for_item(analysis, &all, sheet, &plan.views, id);
    let old = plan.gdt[gi]
        .placed
        .as_ref()
        .filter(|_| plan.gdt[gi].view == view.id);
    let mut chosen = None;
    let mut best_crossings = [usize::MAX; 2];
    let crossing_peers: Vec<_> = peers[..annotation_peers]
        .iter()
        .filter(|q| {
            q.id != *id
                && plan
                    .views
                    .iter()
                    .find(|v| v.id == q.view)
                    .map_or(sheet.number == 1, |v| v.sheet == sheet.number)
        })
        .collect();
    let old_crossings = before
        .iter()
        .filter(|p| p.code == "annotation_leader_crossing")
        .count();
    // Only the view and placed ink vary between trials. Keep the proxy's text,
    // metadata and allocated line buffers instead of cloning the entire glyph.
    all[i].view.clone_from(&view.id);
    for (side, next) in candidates {
        // An unchanged frame retains its old paper; only its new leader is checked against
        // the existing findings below. A repair must not increase any of their counts.
        let held = side == LEADER && old.is_some_and(|o| same_glyph(o, &next));
        if !held && !clear(&next, attached, &peers, &outlines, &paper) {
            continue;
        }
        let ink = all[i].placed.as_mut().expect("the glyph's placed proxy");
        ink.lines.clone_from(&next.lines);
        ink.arrows.clone_from(&next.arrows);
        ink.label_box = next.label_box;
        ink.label_at = [next.label_box[0], next.label_box[1]];
        // A strict route permits no crossing; repairs may retain the old count. Reject
        // proven failures before preparing a complete diagnostic report.
        let allowed = if relaxed || held { old_crossings } else { 0 };
        let crossings = placement::route_crossings(
            &all[i],
            &crossing_peers,
            [best_crossings[0], best_crossings[1], allowed],
        );
        if crossings[2] > allowed
            || (chosen.is_some()
                && (crossings[..2] == best_crossings
                    || (0..2).any(|k| crossings[k] > best_crossings[k])))
        {
            continue;
        }
        let problems = geometry.for_item(analysis, &all, sheet, &plan.views, id);
        let crossings = ["leader", "arrow"].map(|kind| {
            problems
                .iter()
                .filter(|p| {
                    p.code == "annotation_leader_crossing"
                        && p.crossing_kinds.iter().any(|k| k == kind)
                })
                .count()
        });
        // Keep the first valid route as a fallback. Prefer a later route only
        // to avoid a crossed arrowhead without increasing leader crossings.
        // Once the arrowheads are clear, retain the first valid route.
        let improves =
            crossings != best_crossings && (0..2).all(|k| crossings[k] <= best_crossings[k]);

        // A leader to the hole may pass through material as often as the leader it replaces
        // did: the frame then says which hole it controls at no new cost. *relaxed*, it may
        // cross other leaders as often too.
        let tolerated = |ps: &[crate::model::Problem]| -> Vec<crate::model::Problem> {
            let count = |ps: &[crate::model::Problem], code: &str| {
                ps.iter().filter(|p| p.code == code).count()
            };
            let mut codes = vec!["leader_through_feature"];
            if relaxed {
                codes.push("annotation_leader_crossing");
            }
            if held {
                codes.extend(ps.iter().map(|p| p.code.as_str()));
            }
            codes.retain(|c| side == LEADER && count(ps, c) <= count(&before, c));
            ps.iter()
                .filter(|p| !codes.contains(&p.code.as_str()))
                .cloned()
                .collect()
        };
        let problems = tolerated(&problems);
        if problems.is_empty() {
            if improves {
                chosen = Some((side, next, None));
                best_crossings = crossings;
            }
            if best_crossings[1] == 0 {
                break;
            }
            continue;
        }
        // Past its view's reserved area alone: that area grows to hold it where the
        // sheet has vacant paper, and nothing else may change.
        if problems.iter().all(|p| p.code == "annotation_outside_area")
            && let Some(area) =
                super::ink_box(&all[i]).and_then(|ink| grown(view, ink, sheet, &plan.views, &peers))
        {
            let mut views = plan.views.clone();
            // Invariant: `view` is one of the plan's views.
            let vi = views
                .iter()
                .position(|v| v.id == view.id)
                .expect("its view");
            views[vi].area = area;
            if tolerated(&geometry.for_item(analysis, &all, sheet, &views, id)).is_empty() {
                if improves {
                    chosen = Some((side, next, Some((vi, area))));
                    best_crossings = crossings;
                }
                if best_crossings[1] == 0 {
                    break;
                }
            }
        }
    }
    let (side, next, area) = chosen?;
    Some((side, next, area, before))
}

/// Whether placement intent *p* keeps a glyph on its lowered attachment: it names a view other
/// than *end_on* (where the glyph attaches), or a side, lane or position along a lowered strip.
/// A bare pin, an offset, or a pin in *end_on* holds the attached ink instead.
fn lowered(p: &crate::placement_intent::Place, end_on: &str) -> bool {
    if p.view.as_deref() == Some(end_on) {
        return !p.pinned;
    }
    p.view.is_some() || p.side.is_some() || p.lane.is_some() || p.along.is_some()
}

/// Whether *place* pins lowered glyph *item* in the view it attaches in on its hole's mark (an
/// editor's pin of the attached glyph, where it is drawn). That view is not one the glyph is
/// lowered in, so lowering takes it as a bare pin and [`attach`] holds it on its mark.
pub(crate) fn pinned_on_mark(
    analysis: &Analysis,
    item: &crate::pmi_lowering::Lowered,
    place: &crate::placement_intent::Place,
) -> bool {
    use crate::pmi_lowering::Glyph;
    let frame = match &item.glyph {
        Glyph::ControlFrame {
            all_around,
            all_over,
            ..
        } => !*all_around && !*all_over,
        Glyph::Datum { .. } => false,
        _ => return false,
    };
    if !place.pinned || place.view.is_none() {
        return false;
    }
    let read = crate::bridge::holes::read(analysis);
    let holes: Vec<&Hole> = read.iter().filter_map(|(_, h)| h.as_ref().ok()).collect();
    let Some(owners) = bore_owners(analysis, &holes, &item.faces) else {
        return false;
    };
    if !frame && owners.len() != 1 {
        return false;
    }
    // The principal view down the holes' common axis: side, front or plan down x, y or z.
    let Some(k) = owners[0].principal.map(|(k, _)| k) else {
        return false;
    };
    owners
        .iter()
        .all(|h| h.principal.map(|(a, _)| a) == Some(k))
        && place.view.as_deref() == Some(["side", "front", "plan"][k])
}

/// Where glyph *g* would attach: the index of the principal view down its holes' common axis and the holes
/// whose bores its faces are (one, for a datum symbol); none when it stays where lowering put it,
/// or when the Spec places it in that view or side.
fn target<'h>(
    analysis: &Analysis,
    spec: &Spec,
    plan: &Plan,
    holes: &[&'h Hole<'h>],
    g: &gdt::PlannedGdt,
) -> Option<(usize, BTreeSet<&'h str>)> {
    let owners = bore_owners(analysis, holes, &g.faces)?;
    if g.kind != "control_frame" && owners.len() != 1 {
        return None;
    }
    // The principal view down the holes' common axis.
    let k = owners[0].principal.map(|(k, _)| k)?;
    if owners
        .iter()
        .any(|h| h.principal.map(|(a, _)| a) != Some(k))
    {
        return None;
    }
    let vi = plan.views.iter().position(|v| {
        super::NAMES.contains(&v.id.trim_start_matches("v:")) && v.direction[k].abs() > 0.999
    })?;
    if spec
        .annotations
        .get(&g.id)
        .and_then(|a| a.place.as_ref())
        .is_some_and(|p| lowered(p, plan.views[vi].id.trim_start_matches("v:")))
    {
        return None;
    }
    Some((vi, owners.iter().map(|h| h.feature.id.as_str()).collect()))
}

/// The frames and datum symbols of *plan* that attach to their holes ([`attach`]) when the
/// drawing is finished as it stands, each with the size callout it attaches to: none for a table
/// tag, a frame on the same hole or a leader to the hole itself. One that cannot attach, or only
/// by a leader to a hole drawn hidden (directly or through the frame it stands on), is left out:
/// its holes' callout, where one exists, is the better place.
pub(crate) fn attachments(analysis: &Analysis, plan: &Plan) -> BTreeMap<String, Option<String>> {
    let mut finished = plan.clone();
    attach(analysis, &plan.spec, &mut finished);
    let read = crate::bridge::holes::read(analysis);
    let holes: Vec<&Hole> = read.iter().filter_map(|(_, h)| h.as_ref().ok()).collect();
    finished
        .gdt
        .iter()
        .filter(|g| attached(g) && !led_to_hidden(g))
        .map(|g| {
            let on_callout = g.side != LEADER
                && !g.basis.contains(ON_FRAME)
                && !g.basis.contains(CYLINDER_FRAME);
            let callout = target(analysis, &plan.spec, &finished, &holes, g)
                .filter(|_| on_callout)
                .and_then(|(vi, named)| mark(&finished, &named, &finished.views[vi].id))
                .filter(|m| !m.tag)
                .map(|m| m.id);
            (g.id.clone(), callout)
        })
        .collect()
}

/// The hole size callouts of *plan* whose hole a frame or datum symbol leads to where the view
/// draws it hidden ([`led_to_hidden`]): the symbol has no place on the hole itself, so its
/// callout, where one exists, is the better place, and a hole table row that replaced the callout
/// would leave the symbol none (ctc05's ⌀254 blind bore).
pub(crate) fn hidden_carriers(analysis: &Analysis, plan: &Plan) -> BTreeSet<String> {
    let mut finished = plan.clone();
    attach(analysis, &plan.spec, &mut finished);
    let hidden: Vec<&gdt::PlannedGdt> = finished.gdt.iter().filter(|g| led_to_hidden(g)).collect();
    if hidden.is_empty() {
        return BTreeSet::new();
    }
    let patterns = analysis
        .recognition
        .as_ref()
        .map_or(&[][..], |r| r.patterns.as_slice());
    let led = |hole: &str| {
        analysis
            .features
            .iter()
            .find(|f| f.id == hole && f.kind == "hole")
            .is_some_and(|f| {
                hidden
                    .iter()
                    .any(|g| g.faces.iter().any(|face| f.faces.contains(face)))
            })
    };
    plan.dimensions
        .iter()
        .filter(|d| d.kind == "callout" && d.id.starts_with("d:f:hole"))
        // One hole's callout. A counted group's rows state each of its holes, and the frames
        // led to them state their own count (`3×`, FTC-10's front holes): a group's callout kept
        // beside its rows is a duplicate, which the drawings of FTC-10 and STC-10 are pinned
        // against, where a lone hole's callout is the only place its frame has.
        .filter(|d| {
            d.measurements
                .iter()
                .filter(|m| m.parameter == "bore.diameter")
                .count()
                == 1
        })
        .filter(|d| {
            d.measurements.iter().any(|m| {
                led(&m.feature)
                    || patterns
                        .iter()
                        .find(|p| p.id == m.feature)
                        .is_some_and(|p| p.members.iter().any(|h| led(h)))
            })
        })
        .map(|d| d.id.clone())
        .collect()
}

/// Whether glyph *g* was attached to a hole's mark here, so a pin on it holds that ink rather
/// than naming the view it was lowered into.
pub(crate) fn attached(g: &gdt::PlannedGdt) -> bool {
    g.basis.contains(ATTACHED)
        || g.basis.contains(CYLINDER_CALLOUT)
        || g.basis.contains(CYLINDER_RIM)
        || g.basis.contains(CYLINDER_FRAME)
}

/// Whether frame *g* leads to one of its holes where its view draws it hidden, as no view
/// shows its holes visible and it has no place on their mark (a frame stacked on such a frame's
/// shelf shares that leader).
pub(crate) fn led_to_hidden(g: &gdt::PlannedGdt) -> bool {
    attached(g) && (g.basis.contains(HIDDEN) || g.basis.contains(CYLINDER_HIDDEN))
}

/// What the basis of an attached glyph says.
const ATTACHED: &str = ", which sees the hole";

/// Where frame *g* would attach when its faces are one cylinder that is no hole's bore (a shaft,
/// boss or turned step): the index of the principal view down its axis, the feature whose faces
/// they are (none when no one feature claims them all), and its rim there; none when the Spec
/// places it in that view or side.
fn cylinder<'a>(
    analysis: &'a Analysis,
    spec: &Spec,
    plan: &Plan,
    g: &gdt::PlannedGdt,
) -> Option<(usize, BTreeSet<&'a str>, Rim)> {
    let first = analysis.face_attachments.get(*g.faces.first()?)?;
    let k = first.axis?;
    let &[u, v] = first.support.as_slice() else {
        return None;
    };
    let across: Vec<usize> = (0..3).filter(|&j| j != k).collect();
    let mut radius = None;
    let (mut lo, mut hi) = (f64::INFINITY, f64::NEG_INFINITY);
    for &face in &g.faces {
        let a = analysis.face_attachments.get(face)?;
        let (Some("cylinder"), Some(axis), Some(b), Some(p)) =
            (a.surface.as_deref(), a.axis, a.bounds, a.sites.first())
        else {
            return None;
        };
        // One cylinder: the same axis and radius across every face (a cylinder split in halves).
        let r = (p[across[0]] - u).hypot(p[across[1]] - v);
        if axis != k
            || a.support.len() != 2
            || (a.support[0] - u).abs() > 1e-6
            || (a.support[1] - v).abs() > 1e-6
            || radius.is_some_and(|q: f64| (q - r).abs() > 1e-3 * r.max(1.0))
        {
            return None;
        }
        radius = Some(r);
        lo = lo.min(b[k]);
        hi = hi.max(b[k + 3]);
    }
    let r = radius?;
    let vi = plan.views.iter().position(|w| {
        super::NAMES.contains(&w.id.trim_start_matches("v:")) && w.direction[k].abs() > 0.999
    })?;
    let view = &plan.views[vi];
    if spec
        .annotations
        .get(&g.id)
        .and_then(|a| a.place.as_ref())
        .is_some_and(|p| lowered(p, view.id.trim_start_matches("v:")))
    {
        return None;
    }
    // Its end facing the viewer, on its axis.
    let mut end = [0.0; 3];
    end[k] = if view.direction[k] > 0.0 { hi } else { lo };
    end[across[0]] = u;
    end[across[1]] = v;
    let frame = placement::Frame::new(analysis, view);
    // Only actual circular boundary spans belong to this cylinder. In particular, neither
    // the untrimmed half of an open groove nor the gap cut by flats may receive an arrow.
    let tolerance = crate::model::projected_ink::TOLERANCE + crate::model::projected_ink::GRID;
    let trace = analysis.edge_ink.as_ref().map(|ink| {
        let radial = |p: [f64; 3]| (p[across[0]] - u).hypot(p[across[1]] - v);
        let spans = ink
            .edges
            .iter()
            .flat_map(|e| e.windows(2))
            .filter(|s| {
                (s[0][k] - s[1][k]).abs() <= tolerance
                    && s.iter().all(|p| {
                        p[k] >= lo - tolerance
                            && p[k] <= hi + tolerance
                            && (radial(*p) - r).abs() <= tolerance
                    })
            })
            .map(|s| [frame.page(s[0]), frame.page(s[1])])
            .collect();
        (spans, tolerance * frame.scale())
    });
    let rim = Rim {
        c: frame.page(end),
        r: r * frame.scale(),
        seen: Seen::Rim { end, r },
        trace,
    };
    let mut owners = analysis
        .features
        .iter()
        .filter(|f| g.faces.iter().all(|x| f.faces.binary_search(x).is_ok()));
    let named = match (owners.next(), owners.next()) {
        (Some(f), None) => BTreeSet::from([f.id.as_str()]),
        _ => BTreeSet::new(),
    };
    Some((vi, named, rim))
}

/// The size annotation of the cylinder of feature *named* in any view: a shown dimension stating its
/// diameter, and nothing of another feature's but the PMI dimensions that tolerance it and the
/// part's envelope it may also be.
fn cylinder_mark(plan: &Plan, named: &BTreeSet<&str>, radius: f64) -> Option<Mark> {
    let &[id] = named.iter().copied().collect::<Vec<_>>().as_slice() else {
        return None;
    };
    let d = plan.dimensions.iter().find(|d| {
        matches!(d.kind.as_str(), "callout" | "linear")
            && d.state == crate::items::State::Shown
            && d.placed.is_some()
            && d.measurements.iter().any(|m| {
                m.feature == id
                    && m.parameter.ends_with("diameter")
                    && (m.value - 2.0 * radius).abs() < 1e-3
            })
            && d.measurements.iter().all(|m| {
                m.feature == id || m.feature.starts_with("pmi:") || m.feature == "envelope"
            })
            && !counted(&d.label)
    })?;
    let p = d.placed.as_ref()?;
    Some(Mark {
        id: d.id.clone(),
        view: d.view.clone(),
        label_box: p.label_box,
        from: p.lines.first().map_or(p.label_at, |l| l[0]),
        tag: false,
        name: CYLINDER_CALLOUT.into(),
    })
}

/// The holes whose bores *faces* are, each face a cylinder of one hole at its bore's diameter
/// (not a counterbore, spotface or countersink), or none.
fn bore_owners<'a>(
    analysis: &Analysis,
    holes: &[&'a Hole<'a>],
    faces: &[usize],
) -> Option<Vec<&'a Hole<'a>>> {
    if faces.is_empty() {
        return None;
    }
    let mut owners: Vec<&Hole> = Vec::new();
    for &face in faces {
        let a = analysis.face_attachments.get(face)?;
        let (Some("cylinder"), Some(axis), Some(b)) = (a.surface.as_deref(), a.axis, a.bounds)
        else {
            return None;
        };
        // Its diameter from a proven site's distance to its axis, else its box: a bore split in
        // halves at an angle spans less than its diameter across either principal axis.
        let across: Vec<usize> = (0..3).filter(|&j| j != axis).collect();
        let across = match (a.sites.first(), a.support.as_slice()) {
            (Some(p), &[u, v]) => 2.0 * (p[across[0]] - u).hypot(p[across[1]] - v),
            _ => across.iter().map(|&j| b[j + 3] - b[j]).fold(0.0, f64::max),
        };
        let mut of = holes
            .iter()
            .filter(|h| h.feature.faces.binary_search(&face).is_ok());
        let (Some(h), None) = (of.next(), of.next()) else {
            return None;
        };
        if (across - h.diameter).abs() > 1e-3 * h.diameter.max(1.0) {
            return None;
        }
        if !owners.iter().any(|o| o.feature.id == h.feature.id) {
            owners.push(h);
        }
    }
    Some(owners)
}

/// The mark of holes *named* in *view*: a shown size callout naming exactly them, else one hole's
/// table tag.
fn mark(plan: &Plan, named: &BTreeSet<&str>, view: &str) -> Option<Mark> {
    let callout = plan.dimensions.iter().find(|d| {
        let holes: BTreeSet<&str> = d
            .measurements
            .iter()
            .filter(|m| m.parameter == "bore.diameter")
            .map(|m| m.feature.as_str())
            .collect();
        d.view == view
            && d.kind == "callout"
            && d.state == crate::items::State::Shown
            && d.placed.is_some()
            && holes == *named
            // Its holes' own measurements, and the PMI dimensions that tolerance them.
            && d.measurements
                .iter()
                .all(|m| named.contains(m.feature.as_str()) || m.feature.starts_with("pmi:"))
            // A count (`4×`) makes the whole pattern the mark's subject; `⌵ ⌀12 × 90°` and
            // `M6×1` are one hole's.
            && (named.len() > 1 || !counted(&d.label))
    });
    if let Some(d) = callout {
        let p = d.placed.as_ref()?;
        return Some(Mark {
            id: d.id.clone(),
            view: d.view.clone(),
            label_box: p.label_box,
            from: p.lines.first().map_or(p.label_at, |l| l[0]),
            tag: false,
            name: format!(
                "{} size callout",
                if named.len() > 1 { "the holes'" } else { "its" }
            ),
        });
    }
    let b = plan.balloons.iter().find(|b| {
        b.view == view
            && b.certifying
            && b.features.len() == 1
            && named.len() == 1
            && named.contains(b.features[0].as_str())
            && b.state == crate::items::State::Shown
    })?;
    Some(Mark {
        id: b.id.clone(),
        view: b.view.clone(),
        label_box: b.label_box?,
        from: b.leader.map_or(b.at, |l| l[0]),
        tag: true,
        name: format!("its hole table tag {}", b.tag),
    })
}

/// Whether *label* opens with a count, `4×`.
fn counted(label: &str) -> bool {
    label
        .split_once('×')
        .map(|(n, _)| n.trim())
        .is_some_and(|n| !n.is_empty() && n.chars().all(|c| c.is_ascii_digit()))
}

/// Frame *old* moved rigidly so its box's top left corner is *corner*, without its leader.
fn beneath(old: &gdt::GdtPlaced, corner: P2) -> gdt::GdtPlaced {
    let b = old.label_box;
    let d = [corner[0] - b[0], corner[1] - b[3]];
    let shift = |p: P2| [p[0] + d[0], p[1] + d[1]];
    let mut next = old.clone();
    next.tip = corner;
    next.lines.clear();
    next.arrows.clear();
    next.label_box = [b[0] + d[0], b[1] + d[1], b[2] + d[0], b[3] + d[1]];
    for s in &mut next.strokes {
        *s = [shift(s[0]), shift(s[1])];
    }
    for a in &mut next.arcs {
        [a[0], a[1]] = shift([a[0], a[1]]);
    }
    for t in next.letters.iter_mut().chain(&mut next.runs) {
        t.at = shift(t.at);
    }
    next
}

/// The sides of *mark* a datum symbol tries, away from the hole first and never back toward it:
/// a callout's text above or below only, so its line never runs along the text.
fn sides(mark: &Mark) -> Vec<&'static str> {
    let b = mark.label_box;
    let away = [
        0.5 * (b[0] + b[2]) - mark.from[0],
        0.5 * (b[1] + b[3]) - mark.from[1],
    ];
    let vertical = if away[1] < 0.0 { "below" } else { "above" };
    let opposite = if vertical == "below" {
        "above"
    } else {
        "below"
    };
    let horizontal = if away[0] < 0.0 { "left" } else { "right" };
    if !mark.tag {
        return vec![vertical, opposite];
    }
    if away[0].abs() > away[1].abs() {
        vec![horizontal, "above", "below"]
    } else {
        vec![vertical, "left", "right"]
    }
}

/// Where along *side* of *mark* the triangle may stand: a tag's middle; along a callout's text,
/// its middle first, then out toward either end, the triangle wholly on the text.
fn along(mark: &Mark, side: &str) -> Vec<f64> {
    let b = mark.label_box;
    let (lo, hi) = if matches!(side, "above" | "below") {
        (b[0], b[2])
    } else {
        (b[1], b[3])
    };
    let middle = 0.5 * (lo + hi);
    if mark.tag {
        return vec![middle];
    }
    let reach = (0.5 * (hi - lo) - gdt::TRIANGLE).max(0.0);
    [0.0, -0.5, 0.5, -1.0, 1.0]
        .iter()
        .map(|f| middle + f * reach)
        .collect()
}

/// The reserved area of *view* grown to hold *ink* with the half-millimetre band
/// [`crate::sheet_fit`] reserves around ink, when the grown area stays inside the drawing margins,
/// off the title block, clear of every other view's area on its sheet and of every table, notes
/// block and caption among *peers* the area did not already meet; else none.
pub(super) fn grown(
    view: &crate::model::PlannedView,
    ink: [f64; 4],
    sheet: &crate::model::PlannedSheet,
    views: &[crate::model::PlannedView],
    peers: &[PlannedDimension],
) -> Option<[f64; 4]> {
    let a = view.area;
    let next = [
        a[0].min(ink[0] - 0.5),
        a[1].min(ink[1] - 0.5),
        a[2].max(ink[2] + 0.5),
        a[3].max(ink[3] + 0.5),
    ];
    let m = crate::layout::margin();
    let inside = next[0] >= m - 1e-6
        && next[1] >= m - 1e-6
        && next[2] <= sheet.page[0] - m + 1e-6
        && next[3] <= sheet.page[1] - m + 1e-6;
    let vacant = !placement::boxes_overlap(next, sheet.title_block)
        && views
            .iter()
            .filter(|v| v.id != view.id && v.sheet == view.sheet)
            .all(|v| !placement::boxes_overlap(next, v.area))
        && peers
            .iter()
            .filter(|q| matches!(q.kind.as_str(), "table" | "notes" | "caption"))
            .filter_map(super::ink_box)
            .all(|b| !placement::boxes_overlap(next, b) || placement::boxes_overlap(a, b));
    (inside && vacant).then_some(next)
}

/// Whether glyph *next* keeps the 1 mm text gap from every item but those it is *attached* to
/// (which it may touch, never overlap), and stays off every view but on blank paper of its own:
/// the checks the route repair makes beyond the collision report.
fn clear(
    next: &gdt::GdtPlaced,
    attached: &BTreeSet<String>,
    peers: &[PlannedDimension],
    outlines: &[(bool, [f64; 4])],
    paper: &dyn Fn([f64; 4]) -> bool,
) -> bool {
    let b = next.label_box;
    let pad = [b[0] - 1.0, b[1] - 1.0, b[2] + 1.0, b[3] + 1.0];
    let triangle = next.fills.iter().flatten().fold(b, |t, p| {
        [
            t[0].min(p[0]),
            t[1].min(p[1]),
            t[2].max(p[0]),
            t[3].max(p[1]),
        ]
    });
    if outlines
        .iter()
        .any(|(own, o)| placement::boxes_overlap(triangle, *o) && !(*own && paper(triangle)))
    {
        return false;
    }
    // A datum's triangle stands on its mark: it may touch the mark, nothing else.
    let fills: Vec<[f64; 4]> = next
        .fills
        .iter()
        .map(|f| {
            let t = f.iter().fold(
                [
                    f64::INFINITY,
                    f64::INFINITY,
                    f64::NEG_INFINITY,
                    f64::NEG_INFINITY,
                ],
                |t, p| {
                    [
                        t[0].min(p[0]),
                        t[1].min(p[1]),
                        t[2].max(p[0]),
                        t[3].max(p[1]),
                    ]
                },
            );
            [t[0] + 0.05, t[1] + 0.05, t[2] - 0.05, t[3] - 0.05]
        })
        .collect();
    peers
        .iter()
        .filter_map(|q| Some((attached.contains(&q.id), q.placed.as_ref()?)))
        .all(|(own, q)| {
            let room = if own { b } else { pad };
            let frame_clear = !placement::boxes_overlap(room, q.label_box)
                && !q
                    .lines
                    .iter()
                    .chain(&q.arrows)
                    .any(|s| placement::crosses(*s, room))
                && !next.lines.iter().any(|s| {
                    // Its line keeps the text gap too, from all but its own mark.
                    let l = q.label_box;
                    let g = if own { 0.0 } else { 1.0 };
                    placement::crosses(*s, [l[0] - g, l[1] - g, l[2] + g, l[3] + g])
                });
            let triangle_clear = own
                || fills.iter().all(|t| {
                    !placement::boxes_overlap(*t, q.label_box)
                        && !q
                            .lines
                            .iter()
                            .chain(&q.arrows)
                            .any(|s| placement::crosses(*s, *t))
                });
            frame_clear && triangle_clear
        })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_clear_route_does_not_request_later_candidates() {
        let analysis: Analysis = serde_json::from_value(serde_json::json!({
            "schema": crate::model::ANALYSIS_SCHEMA,
            "binding":{"source_sha256":"", "part":"plate", "face_count":0, "loader":"test"},
            "envelope":[[0,0,0],[100,60,10]], "iso_bounds":[0,0,100,60]
        }))
        .unwrap();
        let mut plan = crate::model::plan(&analysis, &Spec::default()).unwrap();
        plan.dimensions.clear();
        plan.views.truncate(1);
        let h = plan.sheet.page[1];
        plan.views[0].area = [5., 5., plan.sheet.page[0] - 5., h - 5.];
        let next: gdt::GdtPlaced = serde_json::from_value(serde_json::json!({
            "tip":[20,h-25.], "label_box":[20,h-25.,30,h-19.],
            "lines":[], "arrows":[], "strokes":[], "arcs":[],
            "fills":[], "letters":[], "runs":[]
        }))
        .unwrap();
        plan.gdt.push(gdt::PlannedGdt {
            from_spec: vec![],
            id: "pmi:frame".into(),
            kind: "control_frame".into(),
            view: plan.views[0].id.clone(),
            side: LEADER.into(),
            source: "frame".into(),
            faces: vec![],
            cells: vec!["0.1".into()],
            modifiers: vec![],
            label: "0.1".into(),
            default: true,
            basis: String::new(),
            state: crate::items::State::Shown,
            placed: Some(next.clone()),
        });
        let candidates = std::iter::once((LEADER, next.clone())).chain(std::iter::from_fn(|| {
            panic!("a clear route must stop generation of later candidates")
        }));
        let found = settle(
            &analysis,
            &plan,
            0,
            &plan.views[0],
            candidates,
            &BTreeSet::new(),
            (&BTreeMap::new(), &BTreeMap::new()),
            &super::super::footprints::Cache::default(),
            false,
        )
        .unwrap();
        assert_eq!(found.1, next);
    }

    #[test]
    fn only_a_leading_count_counts() {
        assert!(super::counted("4× ⌀7 THRU"));
        assert!(super::counted("12 × ⌀3"));
        assert!(!super::counted("⌀12 THRU ⌵ ⌀20 × 90°"));
        assert!(!super::counted("M6×1 THRU"));
    }
}
