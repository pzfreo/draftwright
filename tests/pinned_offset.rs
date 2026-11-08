//! F5 (#12): a pinned callout placed by its bend's offset from the automatic leader tip, which
//! can sit inside the view. The editor's sample plate (draftwright-editor public/samples).
use draftwright::dims::Placed;
use draftwright::items::{Op, key};
use draftwright::model::{Problem, Spec};
use draftwright::{Session, api};
use serde_json::json;
use std::path::Path;

#[path = "common/corpus.rs"]
mod corpus;
#[path = "common/support.rs"]
#[macro_use]
mod support;

const ID: &str = "d:f:hole_pattern:1/bore.diameter";

fn session() -> Session {
    let path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/samples/editor-plate.step");
    Session::open(&path, api::Features::Native, Spec::default()).unwrap()
}

fn ink(s: &Session) -> Placed {
    s.plan()
        .dimensions
        .iter()
        .find(|d| d.id == ID)
        .unwrap()
        .placed
        .clone()
        .unwrap()
}

fn pin(s: &mut Session, offset: [f64; 2]) {
    s.apply(
        &[Op::set(
            format!("dimensions/{}/place", key(ID)),
            json!({"offset": offset, "pinned": true}),
        )],
        true,
    )
    .unwrap();
}

fn against(s: &Session) -> Vec<&Problem> {
    s.plan()
        .problems
        .iter()
        .filter(|p| p.subjects.first().map(String::as_str) == Some(ID))
        .collect()
}

/// The plate's projected outline in the plan view: 100 × 60 about the view's origin.
fn outline(s: &Session) -> [f64; 4] {
    let v = s.plan().views.iter().find(|v| v.id == "v:plan").unwrap();
    [
        v.at[0] - 50.0 * v.scale,
        v.at[1] - 30.0 * v.scale,
        v.at[0] + 50.0 * v.scale,
        v.at[1] + 30.0 * v.scale,
    ]
}

fn inside(p: [f64; 2], o: [f64; 4]) -> bool {
    o[0] < p[0] && p[0] < o[2] && o[1] < p[1] && p[1] < o[3]
}

fn nearest_member(
    members: &[draftwright::session::CalloutMember],
    elbow: [f64; 2],
) -> &draftwright::session::CalloutMember {
    members
        .iter()
        .min_by(|a, b| {
            let distance = |m: &draftwright::session::CalloutMember| {
                (m.centre[0] - elbow[0]).hypot(m.centre[1] - elbow[1]) - m.radius
            };
            distance(a).total_cmp(&distance(b))
        })
        .unwrap()
}

fn assert_radial(tip: [f64; 2], elbow: [f64; 2], site: &draftwright::session::CalloutMember) {
    let r = [tip[0] - site.centre[0], tip[1] - site.centre[1]];
    let d = [elbow[0] - tip[0], elbow[1] - tip[1]];
    assert!(
        (r[0].hypot(r[1]) - site.radius).abs() < 1e-8,
        "{tip:?} {site:?}"
    );
    assert!((r[0] * d[1] - r[1] * d[0]).abs() < 1e-8);
    assert!(r[0] * d[0] + r[1] * d[1] > 0.0);
}

#[test]
fn issue_149_table_selects_the_member_nearest_the_elbow() {
    let mut s = session();
    let at = s.callout_offset(ID).unwrap();
    assert_eq!(at.members.len(), 6);
    assert!(
        at.members
            .iter()
            .all(|m| m.span.is_none() && m.radius > 0.0)
    );
    let original = nearest_member(&at.members, at.tip);
    let stats = s.stats();
    assert_eq!(s.callout_offset(ID).unwrap(), at);
    assert_eq!(s.stats(), stats, "query does not plan or project");
    for offset in [[-20., -10.], [30., 20.], [60., 20.], [90., 0.], [1., 0.]] {
        pin(&mut s, offset);
        let p = ink(&s);
        let elbow = [at.tip[0] + offset[0], at.tip[1] + offset[1]];
        assert_eq!(p.lines[0][1], elbow);
        let member = nearest_member(&at.members, elbow);
        assert_radial(p.lines[0][0], elbow, member);
        assert_eq!(p.arrows[0][0], p.lines[0][0]);
        let current = s.callout_offset(ID).unwrap();
        assert_eq!(
            current.tip, at.tip,
            "offset origin must not follow the arrow"
        );
        assert_eq!(current.members, at.members);
        assert!((current.offset[0] - offset[0]).abs() < 1e-8);
        assert!((current.offset[1] - offset[1]).abs() < 1e-8);
        if offset == [90., 0.] {
            assert!(member.centre[0] > original.centre[0]);
            assert_eq!(
                member.centre[0],
                at.members
                    .iter()
                    .map(|m| m.centre[0])
                    .max_by(f64::total_cmp)
                    .unwrap()
            );
        } else if offset == [1., 0.] {
            assert_eq!(member, original);
        }
    }
}

#[test]
fn issue_149_single_site_keeps_its_radial_attachment() {
    let mut s = session();
    let (id, at) = s
        .plan()
        .dimensions
        .iter()
        .filter(|d| d.kind == "callout")
        .filter_map(|d| s.callout_offset(&d.id).ok().map(|at| (d.id.clone(), at)))
        .find(|(_, at)| {
            at.members.len() == 1 && at.members[0].radius > 0.0 && at.members[0].span.is_none()
        })
        .expect("plate has a single bore");
    for offset in [[-20., -10.], [30., 20.], [60., 20.], [90., 0.]] {
        s.apply(
            &[Op::set(
                format!("dimensions/{}/place", key(&id)),
                json!({"offset":offset,"pinned":true}),
            )],
            true,
        )
        .unwrap();
        let p = s
            .plan()
            .dimensions
            .iter()
            .find(|d| d.id == id)
            .unwrap()
            .placed
            .as_ref()
            .unwrap();
        assert_eq!(
            p.lines[0][1],
            [at.tip[0] + offset[0], at.tip[1] + offset[1]]
        );
        assert_radial(p.lines[0][0], p.lines[0][1], &at.members[0]);
    }
}

#[test]
fn pinning_the_automatic_offset_keeps_bend_and_label() {
    let mut s = session();
    let before = ink(&s);
    let at = s.callout_offset(ID).unwrap();
    assert_eq!(at.view, "v:plan");
    assert_eq!([at.tip, at.bend], before.lines[0]);
    // The automatic bend lies inside the view (#12's report: a bare side/lane pin cannot).
    assert!(inside(at.bend, outline(&s)), "{at:?}");
    // Through JSON, as the editor sends it.
    let offset: [f64; 2] =
        serde_json::from_str(&serde_json::to_string(&at.offset).unwrap()).unwrap();
    pin(&mut s, offset);
    assert_eq!(ink(&s), before);
    assert_eq!(s.callout_offset(ID).unwrap(), at);
    assert!(
        against(&s).is_empty(),
        "{:?}",
        against(&s).iter().map(|p| &p.code).collect::<Vec<_>>()
    );
    // The embedded Spec keeps the intent, and re-planning it reproduces the ink.
    let spec = s.plan().spec.clone();
    assert_eq!(
        spec.dimensions[ID].place.as_ref().unwrap().offset,
        Some(offset)
    );
    let path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/samples/editor-plate.step");
    let again = Session::open(&path, api::Features::Native, spec).unwrap();
    assert_eq!(ink(&again), before);
}

#[test]
fn an_offset_into_open_space_inside_the_view_places_the_label_there() {
    let mut s = session();
    let o = outline(&s);
    let tip = s.callout_offset(ID).unwrap().tip;
    // Open plate below the ⌀8 bore, between the pattern's lower middle and right holes.
    let centre = [0.5 * (o[0] + o[2]), 0.5 * (o[1] + o[3])];
    let bend = [centre[0] + 10.6, centre[1] - 24.2];
    pin(&mut s, [bend[0] - tip[0], bend[1] - tip[1]]);
    let p = ink(&s);
    let [leader_tip, at] = p.lines[0];
    assert!((at[0] - bend[0]).abs() < 1e-9 && (at[1] - bend[1]).abs() < 1e-9);
    assert!(inside(at, o), "{p:?}");
    assert!(inside([p.label_box[0], p.label_box[1]], o), "{p:?}");
    assert!(inside([p.label_box[2], p.label_box[3]], o), "{p:?}");
    // The leader runs right, so the text reads to the right of the bend.
    assert!(p.label_box[0] > at[0], "{p:?}");
    // The leader still ends on the hole's rim, radially: the arrow points at its tip.
    assert_eq!(p.arrows[0][0], leader_tip);
    let members = s.callout_offset(ID).unwrap().members;
    let site = nearest_member(&members, at);
    assert_radial(leader_tip, at, site);
    assert_eq!(
        s.callout_offset(ID).unwrap().bend,
        at,
        "the editor reads the bend it placed"
    );
    assert!(
        against(&s).is_empty(),
        "{:?}",
        against(&s)
            .iter()
            .map(|p| (&p.code, &p.message))
            .collect::<Vec<_>>()
    );
}

#[test]
fn an_offset_over_the_view_geometry_is_kept_and_reported() {
    let mut s = session();
    let o = outline(&s);
    let tip = s.callout_offset(ID).unwrap().tip;
    // On the ⌀8 bore at the plate's centre.
    let bend = [0.5 * (o[0] + o[2]), 0.5 * (o[1] + o[3]) - 0.3];
    pin(&mut s, [bend[0] - tip[0], bend[1] - tip[1]]);
    let p = ink(&s);
    assert!((p.lines[0][1][0] - bend[0]).abs() < 1e-9);
    assert!((p.lines[0][1][1] - bend[1]).abs() < 1e-9);
    let codes: Vec<&str> = against(&s).iter().map(|p| p.code.as_str()).collect();
    assert!(codes.contains(&"leader_through_feature"), "{codes:?}");
    let crossing = against(&s)
        .into_iter()
        .find(|p| p.code == "leader_through_feature")
        .unwrap();
    assert!(crossing.message.contains("kept"), "{}", crossing.message);
    assert_eq!(crossing.severity, "warning");
}

#[test]
fn offset_is_a_pinned_callout_form_of_its_own() {
    let mut s = session();
    let path = format!("dimensions/{}/place", key(ID));
    for place in [
        json!({"offset":[5,5]}),
        json!({"offset":[5,5],"side":"above","pinned":true}),
        json!({"offset":[5,5],"lane":1,"pinned":true}),
        json!({"offset":[5,1e6],"pinned":true}),
        json!({"offset":[5],"pinned":true}),
    ] {
        assert!(
            s.apply(&[Op::set(&path, place.clone())], false).is_err(),
            "{place}"
        );
    }
    assert!(s.callout_offset("d:envelope/width.length").is_err());
    // Options advertise both supported free placement forms.
    let fields = s.options(ID).unwrap().settable;
    assert!(fields.contains_key(&format!("{path}/offset")));
    let fields = s.options("d:envelope/width.length").unwrap().settable;
    assert!(fields.keys().any(|k| k.ends_with("/place/offset")));
}

// Every callout drawn with a leader on several parts, pinned at the offset callout_offset
// reports: its ink is the automatic ink, the whole plan is the bare pin's, and the reported
// offset is unchanged. One callout per part is then dragged, and its bend lands where it was
// put. GRM-03's views move after packing, the frame the review found the reported tip in.
part_cases!(pinning_every_callout_at_its_reported_offset_moves_nothing;
    editor_plate => "editor-plate",
    grm03 => "gramel/GRM-03_thumbwheel_drive_screw.step",
    cgb203 => "cadgenbench_inputs/cgb203.step",
    ftc06 => "nist/nist_ftc_06_asme1_rd.stp",
);
fn pinning_every_callout_at_its_reported_offset_moves_nothing(file: &str) {
    let Some(dir) = corpus::dir() else {
        return;
    };
    let part = if file == "editor-plate" {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/samples/editor-plate.step")
    } else {
        dir.join(file)
    };
    let open = |part: &Path| Session::open(part, api::Features::Native, Spec::default()).unwrap();
    let placed = |s: &Session, id: &str| -> Placed {
        s.plan()
            .dimensions
            .iter()
            .find(|d| d.id == id)
            .and_then(|d| d.placed.clone())
            .unwrap()
    };
    let set = |s: &mut Session, id: &str, place: serde_json::Value| {
        s.apply(
            &[Op::set(format!("dimensions/{}/place", key(id)), place)],
            true,
        )
        .unwrap();
    };
    {
        let part = &part;
        let base = open(part);
        let ids: Vec<String> = base
            .plan()
            .dimensions
            .iter()
            .filter(|d| d.kind == "callout" && d.placed.is_some())
            .map(|d| d.id.clone())
            .collect();
        assert!(!ids.is_empty(), "{part:?}");
        let jobs: Vec<_> = ids.iter().enumerate().collect();
        support::parallel(&jobs, |&(n, id)| {
            let at = base.callout_offset(id).unwrap();
            assert_eq!(at.bend, placed(&base, id).lines[0][1], "{part:?} {id}");
            let mut s = open(part);
            set(&mut s, id, json!({"offset": at.offset, "pinned": true}));
            assert_eq!(placed(&s, id), placed(&base, id), "{part:?} {id} jumped");
            assert_eq!(s.callout_offset(id).unwrap(), at, "{part:?} {id}");
            let mut bare = open(part);
            set(&mut bare, id, json!({"pinned": true}));
            assert_eq!(
                s.plan().dimensions,
                bare.plan().dimensions,
                "{part:?} {id}: an offset at the automatic bend is a bare pin"
            );
            if n == 0 {
                let drag = [at.offset[0] + 2.0, at.offset[1] + 1.5];
                set(&mut s, id, json!({"offset": drag, "pinned": true}));
                let bend = placed(&s, id).lines[0][1];
                let want = [at.bend[0] + 2.0, at.bend[1] + 1.5];
                assert!(
                    (bend[0] - want[0]).abs() < 1e-6 && (bend[1] - want[1]).abs() < 1e-6,
                    "{part:?} {id}: bend {bend:?}, put at {want:?}"
                );
                assert_eq!(s.callout_offset(id).unwrap().offset, drag, "{part:?} {id}");
            }
        });
    }
}

#[test]
fn offset_members_expose_the_projected_arc_span() {
    let Some(corpus) = corpus::dir() else { return };
    // Its two obround slots are congruent: one is split from their slot callout, so slot 1
    // keeps the radius leader whose arc span this test reads.
    let spec: Spec =
        serde_json::from_value(json!({"features": {"f:slot:f21": {"split": true}}})).unwrap();
    let s = Session::open(
        &corpus.join("cadgenbench_inputs/cgb245.step"),
        api::Features::Native,
        spec,
    )
    .unwrap();
    let id = "d:f:slot:f13/slot_end_radius.radius";
    let at = s.callout_offset(id).unwrap();
    let p = s
        .plan()
        .dimensions
        .iter()
        .find(|d| d.id == id)
        .unwrap()
        .placed
        .as_ref()
        .unwrap();
    assert!(!at.members.is_empty());
    let arc = at
        .members
        .iter()
        .filter(|m| m.span.is_some())
        .find(|m| {
            // Opposite trimmed arcs can have the same centre and radius.
            // Membership requires the angular interval as well as the circle.
            let span = m.span.unwrap();
            let angle = (p.lines[0][0][1] - m.centre[1]).atan2(p.lines[0][0][0] - m.centre[0]);
            ((p.lines[0][0][0] - m.centre[0]).hypot(p.lines[0][0][1] - m.centre[1]) - m.radius)
                .abs()
                < 1e-6
                && (angle - span.start + 1e-6).rem_euclid(std::f64::consts::TAU)
                    <= span.sweep + 2e-6
        })
        .expect("drawn radius tip lies on an exposed arc");
    let span = arc.span.unwrap();
    assert!(span.start.is_finite() && span.sweep > 0. && span.sweep <= std::f64::consts::TAU);
    let angle = (p.lines[0][0][1] - arc.centre[1]).atan2(p.lines[0][0][0] - arc.centre[0]);
    assert!((angle - span.start + 1e-6).rem_euclid(std::f64::consts::TAU) <= span.sweep + 2e-6);
    let wire = serde_json::to_value(at).unwrap();
    assert!(
        wire["members"]
            .as_array()
            .unwrap()
            .iter()
            .any(|m| m["span"]["sweep"].is_number())
    );
}

/// #170: on a multi-sheet plan (cgb243 plans its plan view onto a second A2 sheet by
/// whole-view allocation) a callout pinned at its reported offset is a bare pin and
/// keeps its ink. The offset is measured on the finished drawing, after its route
/// repair. One callout only: every callout of cgb243 takes about 13 minutes.
#[test]
fn pinning_a_callout_at_its_reported_offset_on_a_multi_sheet_plan_moves_nothing() {
    let Some(dir) = corpus::dir() else { return };
    let part = dir.join("cadgenbench_inputs/cgb243.step.gz");
    let id = "d:f:boss:f86/boss.diameter";
    let open = || Session::open(&part, api::Features::Native, Spec::default()).unwrap();
    let placed = |s: &Session| -> Placed {
        s.plan()
            .dimensions
            .iter()
            .find(|d| d.id == id)
            .and_then(|d| d.placed.clone())
            .unwrap()
    };
    let base = open();
    assert!(base.plan().views.iter().any(|v| v.sheet > 1));
    let at = base.callout_offset(id).unwrap();
    let mut s = open();
    let place = format!("dimensions/{}/place", key(id));
    s.apply(
        &[Op::set(
            &place,
            json!({"offset": at.offset, "pinned": true}),
        )],
        true,
    )
    .unwrap();
    assert_eq!(placed(&s), placed(&base), "{id} jumped");
    assert_eq!(s.callout_offset(id).unwrap(), at);
    let mut bare = open();
    bare.apply(&[Op::set(&place, json!({"pinned": true}))], true)
        .unwrap();
    assert_eq!(s.plan().dimensions, bare.plan().dimensions);
}

/// #170: a view pinned where a step-allocated multi-sheet plan has it (CTC-04 on two A2
/// sheets, section and isometric moved to sheet 2) replays that allocation: the same
/// frames on every sheet, the same pages and the same hole schedule. Sheet 2 spends the
/// canonical drawing's gutters: a gutter solve tied to the pinned sheet 1 left the
/// isometric 25 mm short of its canonical place.
#[test]
fn pinning_a_view_on_a_step_allocated_multi_sheet_plan_keeps_its_frames() {
    let Some(dir) = corpus::dir() else { return };
    let part = dir.join("nist/nist_ctc_04_asme1_rd.stp");
    let forced = json!({"sheet": {"size": "A2", "scale": "1:5"}, "sheets": {"max": 2}});
    let spec: Spec = serde_json::from_value(forced.clone()).unwrap();
    let base = Session::open(&part, api::Features::Native, spec).unwrap();
    let canonical = base.plan();
    let front = canonical.views.iter().find(|v| v.id == "v:front").unwrap();
    let plan = canonical.views.iter().find(|v| v.id == "v:plan").unwrap();
    assert!(canonical.views.iter().any(|v| v.sheet > 1));
    let mut pinned = forced;
    pinned["views"] = json!({"plan": {"place": {
        "relative_to": "front", "side": "above", "align": "projection", "pinned": true,
        "gap": plan.area[1] - front.area[3],
    }}});
    let spec: Spec = serde_json::from_value(pinned).unwrap();
    let s = Session::open(&part, api::Features::Native, spec).unwrap();
    let frames = |p: &draftwright::model::Plan| {
        p.views
            .iter()
            .map(|v| (v.id.clone(), v.at))
            .collect::<Vec<_>>()
    };
    let sheets = |p: &draftwright::model::Plan| {
        p.views
            .iter()
            .map(|v| (v.id.clone(), v.sheet))
            .collect::<Vec<_>>()
    };
    assert_eq!(sheets(s.plan()), sheets(canonical));
    assert_eq!(frames(s.plan()), frames(canonical));
    assert_eq!(s.plan().balloons.len(), canonical.balloons.len());
    assert!(!canonical.balloons.is_empty(), "the hole schedule is kept");
    assert_eq!(s.plan().dimensions.len(), canonical.dimensions.len());
}

/// A session's retained planning scope must not make a plan depend on earlier calls. On
/// FTC-06 a fresh thread planned an offset pin and then its bare pin differently: the second
/// call reused the first call's finished plan, which skipped the established route policies
/// that planning activates, so three unrelated callouts took other routes. That was the CI
/// flake in `pinning_every_callout_at_its_reported_offset_moves_nothing::ftc06`.
#[test]
fn a_retained_session_scope_plans_an_offset_pin_and_its_bare_pin_alike() {
    let Some(dir) = corpus::dir() else { return };
    let part = dir.join("nist/nist_ftc_06_asme1_rd.stp");
    let open = || Session::open(&part, api::Features::Native, Spec::default()).unwrap();
    let base = open();
    let id = "d:f:boss:f43/boss.diameter";
    let at = base.callout_offset(id).unwrap();
    let place = format!("dimensions/{}/place", key(id));
    // Another worker has already planned the same requests: the process-wide caches hold
    // them, while a fresh thread starts with an empty retained scope.
    for pin in [
        json!({"offset": at.offset, "pinned": true}),
        json!({"pinned": true}),
    ] {
        open().apply(&[Op::set(&place, pin)], true).unwrap();
    }
    let (pinned, bare) = std::thread::scope(|scope| {
        scope
            .spawn(|| {
                let mut s = open();
                s.apply(
                    &[Op::set(
                        &place,
                        json!({"offset": at.offset, "pinned": true}),
                    )],
                    true,
                )
                .unwrap();
                let mut bare = open();
                bare.apply(&[Op::set(&place, json!({"pinned": true}))], true)
                    .unwrap();
                (s.plan().dimensions.clone(), bare.plan().dimensions.clone())
            })
            .join()
            .unwrap()
    });
    assert_eq!(pinned, bare);
}

/// A view pinned at its own place on a single sheet replays the canonical drawing. CTC-03's
/// side view, pinned right of the front at its gap (the pins-1-1 quality cell), re-routed
/// two slot radius callouts; one then took the hole schedule's origin-mark position, so
/// the schedule was withdrawn for its 35-dimension ladder. Unchanged callouts keep their
/// canonical routes as incumbents in a pin replay too. Its two slots are congruent, so a slot
/// callout would now state their ends: one is split from it (in both drawings), so the two slot
/// radius callouts of this case remain.
#[test]
fn pinning_a_view_at_its_own_place_keeps_the_ctc03_schedule() {
    let Some(dir) = corpus::dir() else { return };
    let part = dir.join("nist/nist_ctc_03_asme1_rc.stp");
    let open = |spec: Spec| {
        let (p, mut a) = draftwright::model::analyse_native(&part).unwrap();
        corpus::split_slot(&mut a, "f:slot:f66");
        Session::new(p, a, spec).unwrap()
    };
    let base = open(Spec::default());
    let canonical = base.plan();
    let front = canonical.views.iter().find(|v| v.id == "v:front").unwrap();
    let side = canonical.views.iter().find(|v| v.id == "v:side").unwrap();
    let spec: Spec = serde_json::from_value(json!({"views": {"side": {"place": {
        "relative_to": "front", "side": "right", "align": "projection", "pinned": true,
        "gap": side.area[0] - front.area[2],
    }}}}))
    .unwrap();
    let s = open(spec);
    assert!(
        !canonical.balloons.is_empty(),
        "the canonical drawing has a schedule"
    );
    let at = |p: &draftwright::model::Plan| {
        p.views
            .iter()
            .map(|v| (v.id.clone(), v.at))
            .collect::<Vec<_>>()
    };
    assert_eq!(at(s.plan()), at(canonical));
    assert_eq!(s.plan().balloons.len(), canonical.balloons.len());
    let ids = |p: &draftwright::model::Plan| {
        p.dimensions
            .iter()
            .map(|d| d.id.clone())
            .collect::<Vec<_>>()
    };
    assert_eq!(ids(s.plan()), ids(canonical));
}
