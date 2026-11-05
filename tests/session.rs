//! S4 part B: deltas are checked against a fresh plan and render after every slice edit.
#[path = "common/corpus.rs"]
mod corpus;

use draftwright::api;
use draftwright::items::{Op, key};
use draftwright::model::{self, Spec};
use draftwright::session::{Delta, ItemFilter, ProblemFilter, Session};
use draftwright::text::Font;
use serde_json::json;
use std::path::{Path, PathBuf};
use std::time::Instant;
#[path = "common/session_edits.rs"]
mod session_edits;
use session_edits::edits;

fn path(file: &str) -> Option<PathBuf> {
    if file == "plate" {
        return Some(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/mounting_plate.step"),
        );
    }
    corpus::dir().map(|c| c.join(file))
}
fn open(file: &str) -> Option<Session> {
    let (part, analysis) = model::analyse_native(&path(file)?).unwrap();
    Some(Session::new(part, analysis, Spec::default()).unwrap())
}
fn benchmark(file: &str) {
    let Some(mut s) = open(file) else { return };
    let font = Font::new(api::MONO.to_vec()).unwrap();
    let initial_plan = serde_json::to_vec(s.plan()).unwrap();
    let initial_svg = s.drawing().svg();
    let mut replay: draftwright::session::State =
        serde_json::from_slice(&serde_json::to_vec(s.state()).unwrap()).unwrap();
    let mut times = Vec::new();
    let sequence = edits(&s);
    let moves_dimension = !file.contains("GRM-03");
    assert_eq!(sequence.len(), if moves_dimension { 9 } else { 8 });
    assert_eq!(
        sequence.last().unwrap(),
        &[Op::set("views/side/omit", json!(true))]
    );
    if moves_dimension {
        assert_eq!(
            sequence[7],
            [Op::set(
                "dimensions/d:envelope~1width.length/place/view",
                json!("plan")
            )]
        );
    }
    for ops in &sequence {
        let previous = serde_json::to_vec(s.state()).unwrap();
        let before = s.stats();
        let preview = s.apply(ops, false).unwrap();
        assert_eq!(preview.plans, 1);
        assert_eq!(serde_json::to_vec(s.state()).unwrap(), previous);
        assert_eq!(s.stats(), before);
        let start = Instant::now();
        let delta = s.apply(ops, true).unwrap();
        assert_eq!(delta.plans, 1);
        let elapsed = start.elapsed().as_secs_f64() * 1000.0;
        times.push(elapsed);
        eprintln!("SESSION_EDIT_LATENCY {file}: {elapsed:.3} ms {ops:?}");
        assert_eq!(
            serde_json::to_vec(&delta).unwrap(),
            serde_json::to_vec(&preview).unwrap()
        );
        assert_eq!(s.stats().plans, before.plans + 1, "{file}: plans per edit");
        let wire: Delta = serde_json::from_slice(&serde_json::to_vec(&delta).unwrap()).unwrap();
        wire.apply_to(&mut replay).unwrap();
        let full = model::plan_recovered(s.analysis(), s.spec()).unwrap();
        assert_eq!(
            serde_json::to_vec(&replay.plan).unwrap(),
            serde_json::to_vec(&full).unwrap(),
            "{file}: plan delta"
        );
        let fresh = model::render(s.part(), s.analysis(), &full, &font).unwrap();
        let applied = replay
            .render(
                s.part(),
                s.analysis(),
                &font,
                &mut model::Projections::default(),
            )
            .unwrap();
        assert_eq!(applied.svg(), fresh.svg(), "{file}: render delta");
        assert_eq!(s.drawing().svg(), fresh.svg(), "{file}: session render");
        assert_eq!(applied.dxf(), fresh.dxf());
        assert_eq!(applied.annotations, fresh.annotations);
        assert_eq!(applied.sheet, fresh.sheet);
        let saved = serde_json::to_vec(s.plan()).unwrap();
        let svg = s.drawing().svg();
        s.undo().unwrap().apply_to(&mut replay).unwrap();
        assert_eq!(serde_json::to_vec(s.state()).unwrap(), previous);
        assert_eq!(serde_json::to_vec(&replay).unwrap(), previous);
        s.redo().unwrap().apply_to(&mut replay).unwrap();
        assert_eq!(serde_json::to_vec(s.plan()).unwrap(), saved);
        assert_eq!(s.drawing().svg(), svg);
    }
    for _ in &sequence {
        s.undo().unwrap().apply_to(&mut replay).unwrap();
    }
    assert_eq!(serde_json::to_vec(s.plan()).unwrap(), initial_plan);
    assert_eq!(serde_json::to_vec(&replay.plan).unwrap(), initial_plan);
    assert_eq!(s.drawing().svg(), initial_svg);
    times.sort_by(f64::total_cmp);
    let p90 = times[(times.len() * 9).div_ceil(10) - 1];
    eprintln!(
        "SESSION_LATENCY {file}: samples={} p90={p90:.3} ms max={:.3} ms",
        times.len(),
        times.last().unwrap()
    );
    // The 150 ms p90 bound is a go/no-go latency gate measured on the development machine.
    // DRAFTWRIGHT_LATENCY_GATE=0 (set only by CI's hosted test job, whose runners are about
    // five times slower) reports the latency above without asserting the bound.
    if std::env::var("DRAFTWRIGHT_LATENCY_GATE").as_deref() != Ok("0") {
        assert!(
            p90 <= 150.0,
            "{file}: native p90 exceeded 150 ms: {times:?}"
        );
    }
}
#[test]
fn plate_benchmark_deltas() {
    benchmark("plate");
}
#[test]
fn dense_benchmark_deltas() {
    benchmark("mfcadpp/10127.step");
}
#[test]
fn turned_benchmark_deltas() {
    benchmark("gramel/GRM-03_thumbwheel_drive_screw.step");
}
#[test]
fn nist_benchmark_deltas() {
    benchmark("nist/nist_ctc_01_asme1_rd.stp");
}

#[test]
fn preview_refusal_and_history_are_transactional() {
    let mut s = open("plate").unwrap();
    let initial = serde_json::to_vec(s.state()).unwrap();
    let stats = s.stats();
    let ops = edits(&s)[5].clone();
    for preview_ops in edits(&s) {
        s.apply(&preview_ops, false).unwrap();
        assert_eq!(serde_json::to_vec(s.state()).unwrap(), initial);
        assert_eq!(s.stats(), stats);
    }
    let preview = s.apply(&ops, false).unwrap();
    assert_eq!(preview.plans, 1);
    assert_eq!(serde_json::to_vec(s.state()).unwrap(), initial);
    assert_eq!(s.stats(), stats);
    assert!(s.undo().is_err());
    assert!(
        s.apply(&[Op::set("dimensions/unknown/omit", json!(true))], true)
            .is_err()
    );
    assert_eq!(serde_json::to_vec(s.state()).unwrap(), initial);
    let committed = s.apply(&ops, true).unwrap();
    assert_eq!(
        serde_json::to_vec(&preview).unwrap(),
        serde_json::to_vec(&committed).unwrap()
    );
    let held = s.stats();
    assert_eq!(held.projections, stats.projections);
    s.undo().unwrap();
    s.apply(&edits(&s)[1], false).unwrap();
    s.redo().unwrap();
    s.undo().unwrap();
    s.apply(&edits(&s)[1], true).unwrap();
    assert!(s.redo().is_err());
}

#[test]
fn queries_are_typed_filtered_and_do_not_plan() {
    let s = open("plate").unwrap();
    let stats = s.stats();
    let d = &s.plan().dimensions[0];
    let answer = s.item(&d.id).unwrap();
    assert_eq!(answer.value, Some(d.value));
    assert_eq!(answer.item.label, Some(d.label.clone()));
    assert!(answer.placement.is_some());
    let filtered = s.items(&ItemFilter {
        view: Some(d.view.clone()),
        kind: Some("dimension".into()),
        ..Default::default()
    });
    assert!(filtered.iter().any(|i| i.item.id == d.id));
    assert!(
        filtered
            .iter()
            .all(|i| i.item.view.as_ref() == Some(&d.view))
    );
    assert!(
        s.items(&ItemFilter {
            region: Some([0.0, 0.0, 0.1, 0.1]),
            ..Default::default()
        })
        .is_empty()
    );
    assert!(
        s.items(&ItemFilter {
            near: Some(d.id.clone()),
            radius: Some(5.0),
            ..Default::default()
        })
        .iter()
        .any(|i| i.item.id == d.id)
    );
    assert!(
        s.item(&format!("{}x", d.id))
            .unwrap_err()
            .choices
            .contains(&d.id)
    );
    let options = s.options(&d.id).unwrap();
    assert!(!options.placements.is_empty());
    assert!(options.operations.contains(&"tolerance".into()));
    let problems = s.problems(&ProblemFilter {
        subject: Some(d.id.clone()),
        ..Default::default()
    });
    assert!(problems.iter().all(|p| p.subjects.contains(&d.id)));
    assert_eq!(s.stats(), stats);
    let filtered = s.items(&ItemFilter {
        kind: Some(d.kind.clone()),
        feature: Some(d.measurements[0].feature.clone()),
        state: Some(d.state.clone()),
        ..Default::default()
    });
    assert!(filtered.iter().any(|i| i.item.id == d.id));
}

#[test]
fn furniture_and_view_deltas_report_changed_added_and_removed_items() {
    let mut s = open("plate").unwrap();
    let mut replay: draftwright::session::State =
        serde_json::from_slice(&serde_json::to_vec(s.state()).unwrap()).unwrap();
    let stats = s.stats();
    let delta = s
        .apply(&[Op::set("sheet/title/material", json!("Steel"))], true)
        .unwrap();
    assert!(delta.changed.iter().any(|i| i.item.id == "sheet"));
    assert!(delta.annotations.is_empty());
    assert!(delta.geometry.views.is_empty());
    assert_eq!(s.stats().annotations, stats.annotations);
    delta.apply_to(&mut replay).unwrap();
    let delta = s
        .apply(&[Op::set("views/iso/omit", json!(true))], true)
        .unwrap();
    assert!(delta.removed.contains(&"v:iso".into()));
    delta.apply_to(&mut replay).unwrap();
    let delta = s.undo().unwrap();
    assert!(delta.added.iter().any(|i| i.item.id == "v:iso"));
    delta.apply_to(&mut replay).unwrap();
    assert_eq!(
        serde_json::to_vec(&replay).unwrap(),
        serde_json::to_vec(s.state()).unwrap()
    );
}

#[test]
fn pmi_fragments_pins_and_problem_changes_replay() {
    use std::io::Read;
    let fixture = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/pmi/step/nist_ctc_01_asme1_ap242-e1.stp.gz");
    let mut bytes = Vec::new();
    flate2::read::GzDecoder::new(std::fs::File::open(fixture).unwrap())
        .read_to_end(&mut bytes)
        .unwrap();
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR")).join("session-pmi");
    std::fs::create_dir_all(&dir).unwrap();
    let step = dir.join("ctc01.step");
    std::fs::write(&step, bytes).unwrap();
    let (part, analysis) = model::analyse_native(&step).unwrap();
    let mut s = Session::new(part, analysis, Spec::default()).unwrap();
    let g = &s.plan().gdt[0];
    let base = format!("annotations/{}", key(&g.id));
    let id = g.id.clone();
    let initial = serde_json::to_vec(s.state()).unwrap();
    let mut replay: draftwright::session::State =
        serde_json::from_slice(&serde_json::to_vec(s.state()).unwrap()).unwrap();
    let font = Font::new(api::MONO.to_vec()).unwrap();
    let mut saw_problem = false;
    let sequence = [
        vec![Op::set(format!("{base}/place"), json!({"pinned":true}))],
        vec![Op::set(format!("{base}/place/lane"), json!(100))],
        vec![Op::set(format!("{base}/omit"), json!(true))],
        vec![Op::set(format!("{base}/omit"), json!(false))],
        vec![Op::remove(format!("{base}/place"))],
    ];
    for ops in &sequence {
        let delta = s.apply(ops, true).unwrap();
        assert_eq!(delta.plans, 1);
        saw_problem |= !delta.problems_added.is_empty() || !delta.problems_resolved.is_empty();
        let problem_ids: std::collections::BTreeSet<_> =
            s.plan().problems.iter().map(|p| &p.id).collect();
        assert_eq!(problem_ids.len(), s.plan().problems.len());
        let wire: Delta = serde_json::from_slice(&serde_json::to_vec(&delta).unwrap()).unwrap();
        wire.apply_to(&mut replay).unwrap();
        assert_eq!(
            serde_json::to_vec(&replay).unwrap(),
            serde_json::to_vec(s.state()).unwrap()
        );
        let full = model::plan_recovered(s.analysis(), s.spec()).unwrap();
        assert_eq!(
            serde_json::to_vec(&replay.plan).unwrap(),
            serde_json::to_vec(&full).unwrap()
        );
        let fresh = model::render(s.part(), s.analysis(), &full, &font).unwrap();
        assert_eq!(
            replay
                .render(
                    s.part(),
                    s.analysis(),
                    &font,
                    &mut model::Projections::default()
                )
                .unwrap()
                .svg(),
            fresh.svg()
        );
        assert!(s.item(&id).is_ok());
    }
    assert!(saw_problem);
    for _ in sequence {
        s.undo().unwrap().apply_to(&mut replay).unwrap();
    }
    assert_eq!(serde_json::to_vec(s.state()).unwrap(), initial);
    assert_eq!(serde_json::to_vec(&replay).unwrap(), initial);
}

fn linear_ink(s: &Session, id: &str) -> draftwright::dims::Placed {
    s.plan()
        .dimensions
        .iter()
        .find(|d| d.id == id)
        .unwrap()
        .placed
        .clone()
        .unwrap()
}

fn pin_linear(s: &mut Session, id: &str, offset: [f64; 2]) {
    s.apply(
        &[Op::set(
            format!("dimensions/{}/place", key(id)),
            json!({"offset":offset,"pinned":true}),
        )],
        true,
    )
    .unwrap_or_else(|error| panic!("{id} offset {offset:?}: {error}"));
}

fn near_point(a: [f64; 2], b: [f64; 2]) {
    assert!(
        (a[0] - b[0]).abs() < 1e-8 && (a[1] - b[1]).abs() < 1e-8,
        "{a:?} != {b:?}"
    );
}

#[test]
fn linear_offsets_pin_inside_outside_and_keep_measured_stations() {
    let mut s = open("plate").unwrap();
    // Cover both horizontal and vertical dimensions.
    let ids: Vec<_> = s
        .plan()
        .dimensions
        .iter()
        .filter(|d| d.kind == "linear" && d.placed.is_some())
        .map(|d| d.id.clone())
        .collect();
    assert!(ids.len() >= 2);
    let mut rotations = std::collections::BTreeSet::new();
    for id in ids {
        let before = linear_ink(&s, &id);
        rotations.insert(before.rotation as i32);
        let at = s.label_offset(&id).unwrap();
        near_point(at.offset, [0., 0.]);
        let view = s
            .plan()
            .views
            .iter()
            .find(|v| v.id == at.view)
            .unwrap()
            .clone();
        let value = s
            .plan()
            .dimensions
            .iter()
            .find(|d| d.id == id)
            .unwrap()
            .value;
        // JSON round trip of an automatic position is a bare pin, with identical ink.
        let offset = serde_json::from_str(&serde_json::to_string(&at.offset).unwrap()).unwrap();
        pin_linear(&mut s, &id, offset);
        assert_eq!(linear_ink(&s, &id), before);
        s.undo().unwrap();
        let k = usize::from(before.rotation != 0.);
        for displacement in [-3., 3.] {
            let mut target = before.label_at;
            target[1 - k] = view.at[1 - k] + displacement;
            let offset = [target[0] - at.anchor[0], target[1] - at.anchor[1]];
            pin_linear(&mut s, &id, offset);
            let p = linear_ink(&s, &id);
            near_point(p.label_at, target);
            near_point(s.label_offset(&id).unwrap().offset, offset);
            assert_eq!(
                s.plan()
                    .dimensions
                    .iter()
                    .find(|d| d.id == id)
                    .unwrap()
                    .value,
                value
            );
            let mut old_stations: Vec<_> = before.arrows.iter().map(|a| a[0][k]).collect();
            let mut new_stations: Vec<_> = p.arrows.iter().map(|a| a[0][k]).collect();
            old_stations.sort_by(f64::total_cmp);
            new_stations.sort_by(f64::total_cmp);
            assert_eq!(new_stations, old_stations);
            for line in &p.lines[..2] {
                // Every witness aims towards its own dimension line, from either side.
                let height = p.arrows[0][0][1 - k];
                assert!((line[1][1 - k] - line[0][1 - k]) * (height - line[0][1 - k]) >= 0.);
            }
            // Reading and reapplying a dragged offset must not move it either.
            let again = s.label_offset(&id).unwrap();
            pin_linear(&mut s, &id, again.offset);
            assert_eq!(linear_ink(&s, &id), p);
        }
        // Text beyond the witnesses gets outside arrows and a line run-on.
        let mut offset = [0., 0.];
        offset[k] = 100.;
        offset[1 - k] = 12.;
        pin_linear(&mut s, &id, offset);
        let p = linear_ink(&s, &id);
        let lo = p
            .arrows
            .iter()
            .map(|a| a[0][k])
            .fold(f64::INFINITY, f64::min);
        let hi = p
            .arrows
            .iter()
            .map(|a| a[0][k])
            .fold(f64::NEG_INFINITY, f64::max);
        assert!(p.arrows.iter().all(|a| if a[0][k] == lo {
            a[1][k] < lo
        } else {
            a[1][k] > hi
        }));
        near_point(
            p.label_at,
            [at.anchor[0] + offset[0], at.anchor[1] + offset[1]],
        );
        s.apply(
            &[Op::remove(format!("dimensions/{}/place", key(&id)))],
            true,
        )
        .unwrap();
    }
    assert!(rotations.contains(&0) && rotations.contains(&90));
}

#[test]
fn linear_offset_refusals_are_transactional() {
    let mut s = open("plate").unwrap();
    let id = "d:envelope/width.length";
    let before = serde_json::to_vec(s.state()).unwrap();
    for place in [
        json!({"offset":[1,2]}),
        json!({"offset":[1,2],"pinned":true,"side":"above"}),
        json!({"offset":[1,2],"pinned":true,"lane":0}),
        json!({"offset":[1,2],"pinned":true,"along":"middle"}),
        json!({"offset":[1001,2],"pinned":true}),
        json!({"offset":[1],"pinned":true}),
    ] {
        assert!(
            s.apply(
                &[Op::set(format!("dimensions/{}/place", key(id)), place)],
                true
            )
            .is_err()
        );
        assert_eq!(serde_json::to_vec(s.state()).unwrap(), before);
    }
    assert!(s.label_offset("v:plan").is_err());
    assert!(s.label_offset("unknown").is_err());
    let callout = s
        .plan()
        .dimensions
        .iter()
        .find(|d| d.kind == "callout")
        .unwrap()
        .id
        .clone();
    assert!(s.label_offset(&callout).is_err());
    s.apply(
        &[Op::set(format!("dimensions/{}/omit", key(id)), json!(true))],
        true,
    )
    .unwrap();
    assert!(s.label_offset(id).is_err());
}

#[test]
fn linear_offsets_cover_baselines_chains_and_automatic_ladder_rungs() {
    let fixture =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/scattered_holes.step");
    for style in ["automatic", "baseline", "chain", "ordinate"] {
        let (part, analysis) = if style == "automatic" {
            let path = Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/recognition/steps/plates_pads_levels_and_slanted_steps.step");
            let (part, mut analysis) = model::analyse(&path).unwrap();
            let mut recognition = String::new();
            use std::io::Read;
            flate2::read::GzDecoder::new(
                std::fs::File::open(path.with_extension("json.gz")).unwrap(),
            )
            .read_to_string(&mut recognition)
            .unwrap();
            model::recognise(&part, &mut analysis, &recognition).unwrap();
            (part, analysis)
        } else {
            model::analyse_native(&fixture).unwrap()
        };
        let spec: Spec = if style == "automatic" {
            Spec::default()
        } else {
            serde_json::from_value(json!({"dimension_schemes":{"locations":{"scheme":style,"axis":"x","view":"plan"}}})).unwrap()
        };
        let mut s = Session::new(part, analysis, spec).unwrap();
        let ids: Vec<_> = s
            .plan()
            .dimensions
            .iter()
            .filter(|d| {
                d.kind == "linear"
                    && d.placed.is_some()
                    && if style == "automatic" {
                        d.id.contains("step_height")
                    } else {
                        d.id.starts_with("d:scheme:")
                    }
            })
            .map(|d| d.id.clone())
            .collect();
        assert!(!ids.is_empty(), "{style}");
        for id in ids {
            let before = linear_ink(&s, &id);
            let at = s.label_offset(&id).unwrap();
            pin_linear(&mut s, &id, at.offset);
            assert_eq!(linear_ink(&s, &id), before, "{style} {id}");
            s.undo().unwrap();
            pin_linear(&mut s, &id, [2., -15.]);
            let after = s.label_offset(&id).unwrap();
            near_point(after.offset, [2., -15.]);
            near_point(after.label, [after.anchor[0] + 2., after.anchor[1] - 15.]);
            assert_eq!(linear_ink(&s, &id).arrows.len(), before.arrows.len());
            s.undo().unwrap();
        }
    }
}

#[test]
fn linear_offset_collisions_are_reported_and_never_slide_the_pin() {
    let mut s = open("plate").unwrap();
    let id = "d:envelope/width.length";
    let at = s.label_offset(id).unwrap();
    let other = s
        .plan()
        .views
        .iter()
        .find(|v| v.id != at.view && v.id != "v:iso")
        .unwrap();
    let target = other.at;
    pin_linear(
        &mut s,
        id,
        [target[0] - at.anchor[0], target[1] - at.anchor[1]],
    );
    near_point(linear_ink(&s, id).label_at, target);
    assert!(
        s.plan()
            .problems
            .iter()
            .any(|p| p.code == "annotation_over_view" && p.subjects.iter().any(|s| s == id)),
        "{:?}",
        s.plan().problems
    );
    // An unrelated text edit leaves the pinned offset in its carrier frame.
    s.apply(&[Op::set("sheet/title/material", json!("Steel"))], true)
        .unwrap();
    near_point(linear_ink(&s, id).label_at, target);
}

#[test]
fn linear_offset_query_keeps_the_canonical_anchor_after_a_lane_edit() {
    let mut s = open("plate").unwrap();
    let id = "d:envelope/width.length";
    let before = s.label_offset(id).unwrap();
    s.apply(
        &[Op::set(
            format!("dimensions/{}/place", key(id)),
            json!({"lane":2}),
        )],
        true,
    )
    .unwrap();
    let at = s.label_offset(id).unwrap();
    near_point(at.anchor, before.anchor);
    assert_ne!(at.label, before.label);
    let ink = linear_ink(&s, id);
    pin_linear(&mut s, id, at.offset);
    near_point(linear_ink(&s, id).label_at, ink.label_at);
}

#[test]
fn aligned_linear_offset_preserves_the_measured_span() {
    use draftwright::model::dimension_geometry::Reference;
    let fixture = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/plate.step");
    let (part, analysis) = model::analyse_native(&fixture).unwrap();
    let vertices: Vec<_> = analysis
        .dimension_references()
        .iter()
        .filter_map(|(id, refs)| match refs.as_slice() {
            [Reference::Vertex { point, .. }] => Some((id, point)),
            _ => None,
        })
        .collect();
    let (a, b) = vertices
        .iter()
        .flat_map(|a| vertices.iter().map(move |b| (a, b)))
        .find(|(a, b)| {
            (a.1[1] - b.1[1]).abs() < 1e-7
                && (a.1[0] - b.1[0]).abs() > 1e-3
                && (a.1[2] - b.1[2]).abs() > 1e-3
        })
        .unwrap();
    let spec = serde_json::from_value(json!({"dimensions":{"d:authored:diagonal":{"between":[a.0,b.0],"along":"true","view":"front"}}})).unwrap();
    let mut s = Session::new(part, analysis, spec).unwrap();
    let id = "d:authored:diagonal";
    let before = linear_ink(&s, id);
    assert!(before.rotation != 0. && before.rotation != 90.);
    let stats = s.stats();
    let at = s.label_offset(id).unwrap();
    assert_eq!(s.stats(), stats);
    pin_linear(&mut s, id, at.offset);
    assert_eq!(linear_ink(&s, id), before);
    pin_linear(&mut s, id, [5., -8.]);
    let p = linear_ink(&s, id);
    near_point(p.label_at, [at.anchor[0] + 5., at.anchor[1] - 8.]);
    let span = |p: &draftwright::dims::Placed| {
        (p.arrows[0][0][0] - p.arrows[1][0][0]).hypot(p.arrows[0][0][1] - p.arrows[1][0][1])
    };
    assert!((span(&p) - span(&before)).abs() < 1e-8);
}
