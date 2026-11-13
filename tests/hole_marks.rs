//! #224: frames and datum symbols on holes attach to the holes' size callout or table tag where
//! the holes are seen end-on, and a drawn frame citing an undrawn datum is said.
use std::path::Path;

use draftwright::model::{self, Analysis, Plan, Spec};

fn nist(name: &str) -> Analysis {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/pmi/step")
        .join(name);
    model::analyse_native(&path).unwrap().1
}

const FTC10: &str = "nist_ftc_10_asme1_ap242-e2.stp.gz";
const CYLINDRICITY: &str = "pmi:geometric_tolerance.cylindricity:f101,102,104,105,107,108,110,111";
const POSITION_7: &str = "pmi:geometric_tolerance.position:f101,102,104,105,107,108,110,111:ref=A,B,C:mod=diameter_zone,maximum_material_requirement";
const DATUM_F: &str = "pmi:datum.datum:f244,245:t=F";

/// The size callout or tag box a moved glyph sits against, within *gap* paper mm across and
/// overlapping it along.
fn mark_box(plan: &Plan, b: [f64; 4], gap: f64) -> Option<[f64; 4]> {
    let beside = |m: [f64; 4]| {
        let along = b[0] < m[2] && b[2] > m[0];
        let vertical = (b[3] - m[1]).abs() <= gap || (m[3] - b[1]).abs() <= gap;
        along && vertical
    };
    plan.dimensions
        .iter()
        .filter(|d| d.kind == "callout")
        .filter_map(|d| d.placed.as_ref().map(|p| p.label_box))
        .chain(plan.balloons.iter().filter_map(|b| b.label_box))
        .find(|m| beside(*m))
}

/// FTC-10's frames on its four ⌀7 holes say which holes they control: with the hole table's rows
/// in place of the front holes' PMI callouts the plan view has no clear paper beneath the `4× ⌀7`
/// callout, so each takes a leader to one of those holes, the one the callout points at, and
/// carries their count (2026-10-29: a frame no longer holds the callouts back).
#[test]
fn ftc10_hole_frames_and_datums_attach_to_their_marks() {
    let a = nist(FTC10);
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    let callout = plan
        .dimensions
        .iter()
        .find(|d| d.kind == "callout" && d.view == "v:plan" && d.label.starts_with("4× ⌀7"))
        .and_then(|d| d.placed.as_ref())
        .expect("the ⌀7 holes' callout");
    let view = plan.views.iter().find(|v| v.id == "v:plan").unwrap();
    for id in [CYLINDRICITY, POSITION_7] {
        let frame = plan.gdt.iter().find(|g| g.id == id).unwrap();
        let p = frame.placed.as_ref().unwrap();
        // Seen end-on (the plan looks down z), not with a leader to blank material.
        assert_eq!(frame.view, "v:plan");
        assert!(
            frame.basis.contains("sees the holes end-on"),
            "{}",
            frame.basis
        );
        if p.lines.is_empty() {
            let m = mark_box(&plan, p.label_box, 1.0).expect("a callout beside the frame");
            assert!((m[0] - p.label_box[0]).abs() < 1e-6);
            continue;
        }
        assert_eq!(frame.side, "leader", "{}", frame.basis);
        assert_eq!(p.arrows.len(), 1);
        assert_eq!(
            p.runs[0].text, "4×",
            "the frame states how many holes it controls"
        );
        // Its arrowhead on the rim of one of the ⌀7 holes, 22 apart along x from the one the
        // callout's leader starts on (that leader starts on its rim, so within a diameter).
        let from = callout.lines[0][0];
        let on_a_hole = (-3..=3).any(|k| {
            let c = [from[0] + f64::from(k) * 22.0 * view.scale, from[1]];
            (p.tip[0] - c[0]).hypot(p.tip[1] - c[1]) <= 7.0 * view.scale + 1e-6
        });
        assert!(on_a_hole, "{:?} vs {from:?}", p.tip);
    }
    // No front hole's PMI callout is drawn beside its hole table row.
    assert!(
        !plan.dimensions.iter().any(|d| d.kind == "callout"
            && d.view == "v:front"
            && d.placed.is_some()
            && ["8× ⌀2.5", "3× ⌀3.5", "4× ⌀6"]
                .iter()
                .any(|l| d.label.starts_with(l))),
        "a duplicate callout"
    );

    // The datum's triangle stands on its tag in the view that sees its hole end-on.
    let datum = plan.gdt.iter().find(|g| g.id == DATUM_F).unwrap();
    assert_eq!(datum.view, "v:front");
    let d = datum.placed.as_ref().unwrap();
    let triangle = d.fills[0].iter().fold(
        [
            f64::INFINITY,
            f64::INFINITY,
            f64::NEG_INFINITY,
            f64::NEG_INFINITY,
        ],
        |t, q| {
            [
                t[0].min(q[0]),
                t[1].min(q[1]),
                t[2].max(q[0]),
                t[3].max(q[1]),
            ]
        },
    );
    assert!(
        mark_box(&plan, triangle, 0.5 + 1e-6).is_some(),
        "the triangle's base stands on its mark: {triangle:?}"
    );
    // Repacking the recovered source spans leaves room for B on its own bore's
    // frame. The end-on attachment identifies the same cylinder as the former
    // edge-on surface symbol, without borrowing another hole's control frame.
    let b = plan
        .gdt
        .iter()
        .find(|g| g.source == "datum:#18109")
        .unwrap();
    let owner = plan
        .gdt
        .iter()
        .find(|g| g.source == "geometric_tolerance:#18546")
        .unwrap();
    assert_eq!(b.faces, [163, 164]);
    assert_eq!(b.faces, owner.faces);
    assert_eq!(b.view, "v:front");
    assert_eq!(b.view, owner.view);
    let support = owner.placed.as_ref().unwrap().label_box;
    let triangle = &b.placed.as_ref().unwrap().fills[0];
    for base in [triangle[0], triangle[2]] {
        assert!((base[1] - support[3] - 0.5).abs() < 1e-6);
        assert!(base[0] >= support[0] && base[0] <= support[2]);
    }

    // Nothing moved has a finding of its own.
    for id in [CYLINDRICITY, POSITION_7, DATUM_F] {
        let own: Vec<_> = plan
            .problems
            .iter()
            .filter(|p| p.code.starts_with("annotation_") && p.subjects.iter().any(|s| s == id))
            .collect();
        assert!(own.is_empty(), "{id}: {own:#?}");
    }
}

/// CTC-05's ⌀254 bore opens away from the plan view, the only view that sees it end-on, so its
/// circle there is hidden (dashed). Its total runout frame takes no leader to that circle (ASME
/// Y14.2, ISO 128-22: a leader ends on a visible outline); it stays beneath the bore's PMI size
/// callout, which is kept beside its hole table row to carry it, with datum B on that callout
/// (2026-10-29).
#[test]
fn ctc05_hidden_bore_frame_stays_on_its_callout() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/rule_pins/nist_ctc_05_asme1_ap242.stp.gz");
    let a = model::analyse_native(&path).unwrap().1;
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    let callout = plan
        .dimensions
        .iter()
        .find(|d| d.kind == "callout" && d.placed.is_some() && d.label.starts_with("⌀254"))
        .expect("the ⌀254 callout, carrying the bore's frame");
    let frame = plan
        .gdt
        .iter()
        .find(|g| g.id == "pmi:geometric_tolerance.total_runout:f37:ref=A")
        .unwrap();
    let f = frame.placed.as_ref().unwrap();
    assert_ne!(frame.side, "leader", "{}", frame.basis);
    assert!(f.lines.is_empty() && f.arrows.is_empty(), "{f:?}");
    assert_eq!(frame.view, callout.view);
    assert!(frame.basis.contains("size callout"), "{}", frame.basis);
    let m = mark_box(&plan, f.label_box, 1.0).expect("a callout beside the frame");
    assert!((m[0] - f.label_box[0]).abs() < 1e-6);
    let datum = plan
        .gdt
        .iter()
        .find(|g| g.id == "pmi:datum.datum:f37:t=B")
        .unwrap();
    assert!(datum.basis.contains("size callout"), "{}", datum.basis);
    assert_eq!(datum.view, frame.view);
    for id in [frame.id.as_str(), datum.id.as_str()] {
        let own: Vec<_> = plan
            .problems
            .iter()
            .filter(|p| p.code.starts_with("annotation_") && p.subjects.iter().any(|s| s == id))
            .collect();
        assert!(own.is_empty(), "{id}: {own:#?}");
    }
}

/// FTC-07's four ⌀15.082 holes: two in the enclosure's near wall, drawn visible in the plan
/// view, and two in its far wall, drawn hidden there (no view shows them visible). No callout
/// names just one pair, and a leader to a pair's faces ends at their midpoint, on the centre of
/// a hole of the other pair (2026-10-29: never such a leader). Each pair's two position frames
/// lead to that pair's own holes, the far pair's to a dashed circle with its
/// `leader_to_hidden_edge` warning, each carrying the count `2×` over its stack.
#[test]
fn ftc07_frames_lead_to_their_own_holes() {
    use draftwright::output::Curve;
    let a = nist("nist_ftc_07_asme1_ap242-e2.stp.gz");
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    let view = plan.views.iter().find(|v| v.id == "v:plan").unwrap();
    let r = 0.5 * 15.082 * view.scale;
    // Each hole's centre mark in the plan view, by the faces of its hole.
    let centres: Vec<([f64; 2], &[usize])> = view
        .line_work
        .iter()
        .filter(|w| w.kind == "cross")
        .filter_map(|w| {
            let f = a
                .features
                .iter()
                .find(|f| w.id == format!("ink:v:plan:{}", f.id))?;
            let Some(Curve::Line(p, q)) = w.curves.first() else {
                return None;
            };
            Some((
                [0.5 * (p[0] + q[0]), 0.5 * (p[1] + q[1])],
                f.faces.as_slice(),
            ))
        })
        .collect();
    for (faces, far) in [("f261,262,263,264", true), ("f237,238,239,240", false)] {
        let frames: Vec<_> = plan
            .gdt
            .iter()
            .filter(|g| {
                g.id.starts_with(&format!("pmi:geometric_tolerance.position:{faces}:"))
            })
            .collect();
        assert_eq!(frames.len(), 2, "{faces}");
        let led: Vec<_> = frames.iter().filter(|g| g.side == "leader").collect();
        assert!(!led.is_empty(), "{faces}: no frame leads to a hole");
        for g in &frames {
            assert!(g.basis.contains("sees the holes end-on"), "{}", g.basis);
            assert_eq!(far, g.basis.contains("drawn hidden"), "{}", g.basis);
        }
        for g in led {
            let p = g.placed.as_ref().unwrap();
            assert_eq!(p.runs[0].text, "2×", "{}", g.id);
            // On the rim of one of its own holes, and on no hole's centre.
            let own = centres.iter().any(|(c, f)| {
                f.iter().any(|x| g.faces.contains(x))
                    && ((c[0] - p.tip[0]).hypot(c[1] - p.tip[1]) - r).abs() < 0.05
            });
            assert!(own, "{}: {:?}", g.id, p.tip);
            assert!(
                centres
                    .iter()
                    .all(|(c, _)| (c[0] - p.tip[0]).hypot(c[1] - p.tip[1]) > 1.0),
                "{}",
                g.id
            );
            let hidden = plan
                .problems
                .iter()
                .any(|q| q.code == "leader_to_hidden_edge" && q.subjects.contains(&g.id));
            assert_eq!(far, hidden, "{}", g.id);
        }
    }
}

/// A frame on holes whose bores are split in halves at an angle (so neither half spans the
/// diameter across a principal axis) still finds its holes' size callout.
#[test]
fn a_frame_on_split_bores_attaches_to_their_callout() {
    let a = nist("nist_stc_09_asme1_ap242-e4.stp.gz");
    // #127 adds real radius callouts to the dense front strip. Give this
    // topology/attachment scenario a free size-label row; the default may
    // legitimately use its own rim when a frame cannot fit beneath its mark.
    let spec: Spec = serde_json::from_value(serde_json::json!({
        "sheet":{"size":"A0"},
        "dimensions":{"d:f:hole:f42/bore.diameter":{"place":{"side":"above","lane":4}}}
    }))
    .unwrap();
    let plan = model::plan_recovered(&a, &spec).unwrap();
    let frame = plan
        .gdt
        .iter()
        .find(|g| {
            g.id == "pmi:geometric_tolerance.position:f42,43,44,45,46,47,54,55:ref=A,B,C:mod=diameter_zone"
        })
        .unwrap();
    assert!(frame.basis.contains("size callout"), "{}", frame.basis);
    let p = frame.placed.as_ref().unwrap();
    assert!(p.lines.is_empty(), "{p:?}");
    assert!(mark_box(&plan, p.label_box, 1.0).is_some());
}

/// The Spec's placement intent is the override: a frame placed on a side keeps its leader, a
/// datum placed in a view its surface.
#[test]
fn a_placed_hole_frame_or_datum_keeps_its_lowered_attachment() {
    let a = nist(FTC10);
    let spec: Spec = serde_json::from_value(serde_json::json!({"annotations": {
        CYLINDRICITY: {"place": {"side": "below"}},
        DATUM_F: {"place": {"view": "side"}},
    }}))
    .unwrap();
    let plan = model::plan_recovered(&a, &spec).unwrap();
    let frame = plan.gdt.iter().find(|g| g.id == CYLINDRICITY).unwrap();
    assert!(!frame.placed.as_ref().unwrap().lines.is_empty());
    assert!(!frame.basis.contains("size callout"));
    let datum = plan.gdt.iter().find(|g| g.id == DATUM_F).unwrap();
    assert_eq!(datum.view, "v:side");
    assert!(!datum.basis.contains("table tag"));
}

/// A bare pin on an attached datum holds it on its hole's size callout, not back on the edge-on
/// silhouette it was lowered onto.
#[test]
fn a_bare_pin_holds_an_attached_datum() {
    let path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/pmi/step/spool_fits.step.gz");
    let a = model::analyse_native(&path).unwrap().1;
    const DATUM_B: &str = "pmi:datum.datum:f55:t=B";
    let free = model::plan_recovered(&a, &Spec::default()).unwrap();
    let spec: Spec = serde_json::from_value(serde_json::json!({"annotations": {
        DATUM_B: {"place": {"pinned": true}},
    }}))
    .unwrap();
    let pinned = model::plan_recovered(&a, &spec).unwrap();
    let datum = |p: &Plan| p.gdt.iter().find(|g| g.id == DATUM_B).unwrap().clone();
    let (free, pinned) = (datum(&free), datum(&pinned));
    assert_eq!(free.view, "v:plan");
    assert!(free.basis.contains("size callout"), "{}", free.basis);
    assert_eq!(pinned.view, free.view);
    let (f, p) = (free.placed.unwrap(), pinned.placed.unwrap());
    assert!(
        (0..2).all(|k| (f.tip[k] - p.tip[k]).abs() < 1e-6),
        "{:?} vs {:?}",
        f.tip,
        p.tip
    );
}

/// Every shown frame citing a datum with no shown symbol is said once, naming what it lacks,
/// and no other frame is.
#[test]
fn frames_citing_undrawn_datums_are_said() {
    let a = nist("nist_stc_10_asme1_ap242-e2.stp.gz");
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    // accepted regression, pzfreo/draftwright#452 (Paul 2026-11-13); restore when fixed
    assert_eq!(
        plan.gdt.iter().filter(|g| g.source == "datum:#117").count(),
        0
    );
    let refused: Vec<_> = plan
        .problems
        .iter()
        .filter(|p| p.code == "pmi_not_lowered" && p.refs.iter().any(|r| r == "datum:#117"))
        .collect();
    assert_eq!(refused.len(), 1);
    assert_eq!(refused[0].refs[1], "datum reference faces are not coplanar");
    // Opposite faces still define the source width; accepting the missing symbol
    // must not accept a missing or incorrectly measured size dimension.
    let planes = [&a.face_attachments[39], &a.face_attachments[43]];
    for f in planes {
        assert_eq!(f.surface.as_deref(), Some("plane"));
        assert_eq!(f.axis, Some(0));
        let bounds = f.bounds.unwrap();
        assert!((bounds[0] - bounds[3]).abs() < 1e-6);
    }
    assert!((planes[0].bounds.unwrap()[0] - planes[1].bounds.unwrap()[0] - 15.0).abs() < 1e-6);
    let normals = planes.map(|f| f.normal.unwrap());
    assert!(
        (normals[0]
            .iter()
            .zip(normals[1])
            .map(|(a, b)| a * b)
            .sum::<f64>()
            + 1.0)
            .abs()
            < 1e-6
    );
    let size = plan
        .dimensions
        .iter()
        .find(|d| d.id == "d:pmi:dimension.linear:f39,43/linear")
        .unwrap();
    assert_eq!(size.value, 15.0);
    assert_eq!(size.terms, ["15", "±0.2"]);
    assert_eq!(size.view, "v:front");
    let v = plan.views.iter().find(|v| v.id == size.view).unwrap();
    let width = size.placed.as_ref().unwrap();
    assert!(((width.lines[1][0][0] - width.lines[0][0][0]).abs() / v.scale - 15.0).abs() < 1e-6);
    // accepted regression, pzfreo/draftwright#452 (Paul 2026-11-13); restore when fixed
    // C now stands on its own front control frame; the datum is still drawn.
    let c = plan.gdt.iter().find(|g| g.source == "datum:#114").unwrap();
    assert_eq!(c.faces, [25]);
    assert_eq!(c.view, "v:front");
    let face = &a.face_attachments[25];
    assert_eq!(face.surface.as_deref(), Some("cylinder"));
    assert_eq!(face.axis, Some(1));
    let base = &c.placed.as_ref().unwrap().fills[0];
    assert!(plan.gdt.iter().any(|g| {
        if g.kind != "control_frame" || g.faces != c.faces || g.view != c.view {
            return false;
        }
        let Some(p) = &g.placed else { return false };
        [base[0], base[2]].iter().all(|q| {
            q[0] >= p.label_box[0]
                && q[0] <= p.label_box[2]
                && ((q[1] - p.label_box[1]).abs() - 0.5)
                    .abs()
                    .min(((q[1] - p.label_box[3]).abs() - 0.5).abs())
                    < 1e-6
        })
    }));
    // E can now stand on a frame belonging to its own bore, seen end-on.
    let e = plan.gdt.iter().find(|g| g.source == "datum:#119").unwrap();
    assert_eq!(e.faces, [87]);
    assert_eq!(e.view, "v:front");
    let base = &e.placed.as_ref().unwrap().fills[0];
    assert!(plan.gdt.iter().any(|g| {
        if g.kind != "control_frame" || g.faces != e.faces || g.view != e.view {
            return false;
        }
        let Some(p) = &g.placed else {
            return false;
        };
        [base[0], base[2]].iter().all(|q| {
            q[0] >= p.label_box[0]
                && q[0] <= p.label_box[2]
                && ((q[1] - p.label_box[1]).abs() - 0.5)
                    .abs()
                    .min(((q[1] - p.label_box[3]).abs() - 0.5).abs())
                    < 1e-6
        })
    }));

    // The missing-reference check is non-vacuous: the accepted default already lacks J.
    let drawn: Vec<&str> = plan
        .gdt
        .iter()
        .filter(|g| g.kind == "datum_feature" && g.state == draftwright::items::State::Shown)
        .map(|g| g.cells[0].as_str())
        .collect();
    let mut said = 0;
    for g in plan
        .gdt
        .iter()
        .filter(|g| g.kind == "control_frame" && g.state == draftwright::items::State::Shown)
    {
        let missing: Vec<&str> = g.cells[2..]
            .iter()
            .map(String::as_str)
            .filter(|d| !drawn.contains(d))
            .collect();
        let found: Vec<_> = plan
            .problems
            .iter()
            .filter(|p| p.code == "frame_datum_not_drawn" && p.subjects == [g.id.clone()])
            .collect();
        if missing.is_empty() {
            assert!(found.is_empty(), "{}: {found:#?}", g.id);
        } else {
            assert_eq!(found.len(), 1, "{}", g.id);
            assert_eq!(found[0].severity, "warning");
            for d in missing {
                assert!(found[0].refs.contains(&format!("datum {d}")));
            }
            said += 1;
        }
    }
    // The symmetry frame alone cites the missing datum J.
    assert_eq!(said, 1);
    let lacking: Vec<&String> = plan
        .problems
        .iter()
        .filter(|p| p.code == "frame_datum_not_drawn")
        .flat_map(|p| &p.refs)
        .collect();
    assert_eq!(lacking, ["datum J"]);
}

/// Where a datum's place on its hole's mark runs past the view's reserved area into vacant
/// paper, that area grows to hold it: both of CTC-01's hole datums attach in the plan view, each
/// with no finding of its own, and the grown area stays clear of every other view's. (CTC-02's
/// datum F did too until quiddity-rust v0.2.2's R25 fillets took the paper beneath the plan; see
/// `gdt_band`.)
#[test]
fn hole_datums_attach_where_their_view_area_can_grow() {
    for (part, datums) in [(
        "nist_ctc_01_asme1_ap242-e1.stp.gz",
        &["pmi:datum.datum:f75,76:t=C", "pmi:datum.datum:f89,90:t=B"][..],
    )] {
        let a = nist(part);
        let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
        let view = plan.views.iter().find(|v| v.id == "v:plan").unwrap();
        for id in datums {
            let datum = plan.gdt.iter().find(|g| g.id == *id).unwrap();
            assert_eq!(datum.view, "v:plan", "{part} {id}");
            assert!(
                datum.basis.contains("end-on"),
                "{part} {id}: {}",
                datum.basis
            );
            let p = datum.placed.as_ref().unwrap();
            let a = view.area;
            for q in p.lines.iter().flatten().chain(p.fills.iter().flatten()) {
                assert!(
                    a[0] <= q[0] && q[0] <= a[2] && a[1] <= q[1] && q[1] <= a[3],
                    "{part} {id}: {q:?} outside {a:?}"
                );
            }
            let own: Vec<_> = plan
                .problems
                .iter()
                .filter(|p| p.subjects.iter().any(|s| s == id))
                .filter(|p| p.code.starts_with("annotation_") || p.code.contains("through"))
                .collect();
            assert!(own.is_empty(), "{part} {id}: {own:#?}");
        }
        for other in plan
            .views
            .iter()
            .filter(|v| v.id != view.id && v.sheet == view.sheet)
        {
            let (a, b) = (view.area, other.area);
            assert!(
                !(a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3]),
                "{part}: the plan's area {a:?} overlaps {}'s {b:?}",
                other.id
            );
        }
    }
}

const STC06: &str = "nist_stc_06_asme1_ap242-e3.stp.gz";

/// The identity of *letter*'s datum on *a*.
fn datum_id(a: &Analysis, letter: &str) -> String {
    let Some(draftwright::pmi::Reading::Read(report)) = &a.pmi else {
        panic!("PMI read");
    };
    let ids = report.identities();
    let r = report
        .records
        .iter()
        .find(|r| r.category == draftwright::pmi::Category::Datum && r.label == letter)
        .unwrap();
    ids.id(&r.source_id).unwrap().to_string()
}

/// A datum on a width's two opposite planes is the width's median plane: STC-06's datum B, which
/// twelve frames cite, stands on an extension line of its 304.8 size dimension, in line with the
/// dimension line (ISO 5459), not on either face; no frame then lacks it, and the refusal that
/// left it undrawn is gone. `annotations/<id>/omit` leaves it out.
#[test]
fn a_width_datum_stands_on_its_size_dimension() {
    let a = nist(STC06);
    let b = datum_id(&a, "B");
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    let datum = plan.gdt.iter().find(|g| g.id == b).expect("datum B drawn");
    assert_eq!(datum.state, draftwright::items::State::Shown);
    assert!(datum.basis.contains("median plane"), "{}", datum.basis);
    let p = datum.placed.as_ref().unwrap();
    let size = plan
        .dimensions
        .iter()
        .find(|d| d.view == datum.view && d.label.starts_with("304.8"))
        .expect("its size dimension in the datum's view");
    let s = size.placed.as_ref().unwrap();
    // The triangle's base is on one of the dimension's arrow tips (where its dimension line meets
    // its extension line), and the symbol's line runs along the dimension line, outward.
    let tips: Vec<[f64; 2]> = s.arrows.iter().map(|a| a[0]).collect();
    let tip = tips
        .iter()
        .find(|t| (t[0] - p.tip[0]).abs() < 1e-6 && (t[1] - p.tip[1]).abs() < 1e-6)
        .expect("the triangle on an arrow tip");
    let other = tips.iter().find(|t| *t != tip).unwrap();
    for line in &p.lines {
        for q in line {
            assert!(
                (q[1] - tip[1]).abs() < 1e-6,
                "in line with the dimension line"
            );
            assert!(
                (q[0] - tip[0]) * (tip[0] - other[0]) >= -1e-6,
                "outward: {line:?}"
            );
        }
    }
    let own: Vec<_> = plan
        .problems
        .iter()
        .filter(|q| q.code.starts_with("annotation_") && q.subjects.contains(&b))
        .collect();
    assert!(own.is_empty(), "{own:#?}");
    assert!(
        !plan
            .problems
            .iter()
            .any(|q| q.code == "frame_datum_not_drawn" && q.refs.contains(&"datum B".into()))
    );
    assert!(
        !plan
            .problems
            .iter()
            .any(|q| q.code == "pmi_not_lowered" && q.refs.first() == Some(&datum.source))
    );

    let mut spec = Spec::default();
    spec.annotations.insert(
        b.clone(),
        serde_json::from_value(serde_json::json!({"omit": true})).unwrap(),
    );
    let omitted = model::plan_recovered(&a, &spec).unwrap();
    assert!(
        !omitted
            .gdt
            .iter()
            .any(|g| g.id == b && g.state == draftwright::items::State::Shown)
    );
}

/// A datum feature that names no face of its own, which the reader reads as its datum's usage's
/// faces (a nonconformance it reads through), is drawn on those faces, the reading in its basis:
/// CTC-02's datum D.
#[test]
fn a_datum_read_through_a_nonconformance_is_drawn() {
    let a = nist("nist_ctc_02_asme1_ap242-e2.stp.gz");
    let d = datum_id(&a, "D");
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    let datum = plan
        .gdt
        .iter()
        .find(|g| g.id == d)
        .expect("datum D lowered");
    assert_eq!(datum.state, draftwright::items::State::Shown);
    assert!(datum.basis.contains("read as"), "{}", datum.basis);
    assert!(
        !plan
            .problems
            .iter()
            .any(|q| q.code == "frame_datum_not_drawn" && q.refs.contains(&"datum D".into()))
    );
}

const CTC05_CIRCULARITY: &str = "pmi:geometric_tolerance.circularity:f72";
const CTC05_CONCENTRICITY: &str =
    "pmi:geometric_tolerance.concentricity:f10:ref=A:mod=diameter_zone";

fn ctc05() -> Analysis {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/rule_pins/nist_ctc_05_asme1_ap242.stp.gz");
    model::analyse_native(&path).unwrap().1
}

/// Project independently of the attachment planner, using the public camera and envelope.
fn page(a: &Analysis, v: &model::PlannedView, p: [f64; 3]) -> [f64; 2] {
    let camera = haecceity::hlr::View::new(v.direction, v.up).unwrap();
    let middle = [0, 1, 2].map(|k| 0.5 * (a.envelope[0][k] + a.envelope[1][k]));
    let (p, c) = (camera.map(p), camera.map(middle));
    [0, 1].map(|k| v.at[k] + v.scale * (p[k] - c[k]))
}

fn assert_cylinder_attachment(a: &Analysis, plan: &Plan, id: &str) {
    let g = plan.gdt.iter().find(|g| g.id == id).unwrap();
    let p = g.placed.as_ref().expect("the frame stays drawn");
    let v = plan.views.iter().find(|v| v.id == g.view).unwrap();
    let f = &a.face_attachments[g.faces[0]];
    let k = f.axis.unwrap();
    let across: Vec<_> = (0..3).filter(|&j| j != k).collect();
    let radius = (f.sites[0][across[0]] - f.support[0]).hypot(f.sites[0][across[1]] - f.support[1]);
    if p.lines.is_empty() {
        // The frame touches the correct size dimension, in the same view. Numeric equality
        // alone cannot borrow a diameter from another feature.
        let d = plan.dimensions.iter().find(|d| {
            d.view == g.view
                && d.placed.as_ref().is_some_and(|m| {
                    let b = m.label_box;
                    p.label_box[0] < b[2]
                        && p.label_box[2] > b[0]
                        && ((p.label_box[3] - b[1]).abs() <= 0.5 + 1e-6
                            || (p.label_box[1] - b[3]).abs() <= 0.5 + 1e-6)
                })
                && d.measurements.iter().any(|m| {
                    m.parameter.ends_with("diameter")
                        && (m.value - 2.0 * radius).abs() < 1e-3
                        && a.features.iter().any(|f| {
                            f.id == m.feature && g.faces.iter().all(|i| f.faces.contains(i))
                        })
                })
        });
        assert!(
            d.is_some(),
            "{id}: a frame must touch its own size dimension"
        );
        assert!(p.arrows.is_empty());
        return;
    }
    assert!(v.direction[k].abs() > 0.999, "{id}: end-on view");
    let mut axis = [0.0; 3];
    for (&j, &s) in across.iter().zip(&f.support) {
        axis[j] = s;
    }
    let c = page(a, v, axis);
    let r = radius * v.scale;
    let distance = (p.tip[0] - c[0]).hypot(p.tip[1] - c[1]);
    assert!(distance > 1.0, "{id}: axis-point leader {p:?}");
    assert!((distance - r).abs() < 1e-5, "{id}: on its own rim");
    assert_eq!(p.arrows.len(), 1);
    let [tip, elbow] = p.lines[0];
    let u = [tip[0] - c[0], tip[1] - c[1]];
    let d = [elbow[0] - tip[0], elbow[1] - tip[1]];
    assert!((u[0] * d[1] - u[1] * d[0]).abs() < 1e-5, "radial");
    assert!(u[0] * d[0] + u[1] * d[1] > 0.0, "outward");
    // No leg travels along either the cylinder's axis strokes or another feature's centre
    // mark. Proper crossings are allowed; nearly parallel travel is ambiguous.
    for s in &p.lines {
        for t in v
            .line_work
            .iter()
            .flat_map(draftwright::centrelines::segments)
        {
            let d = [s[1][0] - s[0][0], s[1][1] - s[0][1]];
            let n = d[0].hypot(d[1]);
            if n < 0.5 {
                continue;
            }
            let u = d.map(|x| x / n);
            let q = t.map(|p| [p[0] - s[0][0], p[1] - s[0][1]]);
            let parallel = q.iter().all(|p| (p[0] * u[1] - p[1] * u[0]).abs() < 1.0);
            let ends = q.map(|p| p[0] * u[0] + p[1] * u[1]);
            let overlap = n.min(ends[0].max(ends[1])) - 0.0_f64.max(ends[0].min(ends[1]));
            assert!(
                !parallel || overlap < 0.5,
                "{id}: centre-line travel {s:?} {t:?}"
            );
        }
    }
    // In particular, the first leg may not run through CTC-05's off-axis holes (A2 in the
    // reported drawing). Use all cylindrical attachments, independent of recognised owners.
    for (fi, f) in a.face_attachments.iter().enumerate() {
        if f.surface.as_deref() != Some("cylinder") || f.axis != Some(k) || f.sites.is_empty() {
            continue;
        }
        let mut at = [0.0; 3];
        for (&j, &s) in across.iter().zip(&f.support) {
            at[j] = s;
        }
        let other = page(a, v, at);
        if (other[0] - c[0]).hypot(other[1] - c[1]) < 1e-6 {
            continue;
        }
        let r = (f.sites[0][across[0]] - f.support[0]).hypot(f.sites[0][across[1]] - f.support[1])
            * v.scale;
        // The enclosing stock circle supports this hole; it is not a second hole. A
        // leader to an off-axis hole in a round plate necessarily crosses that outline.
        let hole = a
            .features
            .iter()
            .any(|h| h.kind == "hole" && h.faces.contains(&fi));
        if !hole && (other[0] - c[0]).hypot(other[1] - c[1]) + radius * v.scale < r {
            continue;
        }
        let n2 = d[0] * d[0] + d[1] * d[1];
        let t = (((other[0] - tip[0]) * d[0] + (other[1] - tip[1]) * d[1]) / n2).clamp(0.0, 1.0);
        let closest = [tip[0] + t * d[0], tip[1] + t * d[1]];
        assert!(
            (closest[0] - other[0]).hypot(closest[1] - other[1]) > r,
            "{id}: passes through another cylinder"
        );
    }
}

/// The two CTC-05 frames used to share an axis-point leader down the vertical centre line,
/// through A2, despite controlling two different cylinders. A clear size dimension wins;
/// otherwise each arrow must reach its own circle with a radial, unambiguous leader.
#[test]
fn ctc05_cylinder_frames_identify_their_own_surfaces() {
    let a = ctc05();
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    for id in [CTC05_CIRCULARITY, CTC05_CONCENTRICITY] {
        assert_cylinder_attachment(&a, &p, id);
    }
    let frames: Vec<_> = p
        .gdt
        .iter()
        .filter(|g| [CTC05_CIRCULARITY, CTC05_CONCENTRICITY].contains(&g.id.as_str()))
        .filter_map(|g| g.placed.as_ref().filter(|p| !p.lines.is_empty()))
        .collect();
    if let [a, b] = frames.as_slice() {
        assert!(
            (a.tip[0] - b.tip[0]).hypot(a.tip[1] - b.tip[1]) > 1.0,
            "different cylinders need different leaders"
        );
    }
}

#[test]
fn cylinder_frame_placement_is_overridable() {
    let a = ctc05();
    let spec: Spec = serde_json::from_value(serde_json::json!({"annotations": {
        CTC05_CIRCULARITY: {"place": {"view": "plan", "side": "above"}},
        CTC05_CONCENTRICITY: {"place": {"side": "above"}},
    }}))
    .unwrap();
    let p = model::plan_recovered(&a, &spec).unwrap();
    for id in [CTC05_CIRCULARITY, CTC05_CONCENTRICITY] {
        let g = p.gdt.iter().find(|g| g.id == id).unwrap();
        assert!(
            !g.basis.contains("its cylinder's"),
            "explicit placement wins"
        );
        assert!(!g.placed.as_ref().unwrap().lines.is_empty());
    }
}

#[test]
fn ctc05_without_size_dimensions_uses_two_radial_rim_leaders() {
    let a = ctc05();
    let spec: Spec = serde_json::from_value(serde_json::json!({"dimensions": {
        "d:f:turned_step:f10/step.diameter": {"omit": true},
        "d:f:turned_step:3/step.diameter": {"omit": true},
    }}))
    .unwrap();
    let p = model::plan_recovered(&a, &spec).unwrap();
    let mut tips = Vec::new();
    for id in [CTC05_CIRCULARITY, CTC05_CONCENTRICITY] {
        assert_cylinder_attachment(&a, &p, id);
        let g = p.gdt.iter().find(|g| g.id == id).unwrap();
        let placed = g.placed.as_ref().unwrap();
        assert!(!placed.lines.is_empty(), "no size annotation to carry {id}");
        assert!(
            !p.problems
                .iter()
                .any(|p| p.code == "leader_to_hidden_edge" && p.subjects.contains(&id.to_owned())),
            "both rims are visible"
        );
        tips.push(placed.tip);
    }
    assert!((tips[0][0] - tips[1][0]).hypot(tips[0][1] - tips[1][1]) > 1.0);
}

/// A hidden hole that already had a rim leader must not regress to its axis merely because
/// its old cardinal shaft used a centre-mark arm. Re-aim the shaft while retaining a clear
/// frame, or use another clear radial route, with the hidden-edge warning still reported.
#[test]
fn stricter_centreline_clearance_keeps_thumbwheel_frame_on_its_rim() {
    let a = nist("thumbwheel_thread_knurl.step.gz");
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    const ID: &str = "pmi:geometric_tolerance.position:f12:ref=A,B:mod=diameter_zone";
    assert_cylinder_attachment(&a, &p, ID);
    let g = p.gdt.iter().find(|g| g.id == ID).unwrap();
    if !g.placed.as_ref().unwrap().lines.is_empty() {
        assert!(
            p.problems
                .iter()
                .any(|p| p.code == "leader_to_hidden_edge" && p.subjects.contains(&ID.to_owned()))
        );
    }
}

/// The shallow diagonal to this hole has room beside the round plate where the cardinal
/// shaft follows its centre mark and 45-degree routes enter the part or another view.
#[test]
fn intermediate_radial_directions_do_not_regress_an_existing_rim_attachment() {
    let a = nist("hw_every_kind.step.gz");
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    assert_cylinder_attachment(&a, &p, "pmi:geometric_tolerance.flatness:f0");
}

/// Repairing other axis-point frames must not displace #251's two-hole frame back onto
/// the different hole at their midpoint. Its circle is small, so measure the actual rim
/// rather than treating every tip within 1 mm of an axis as an axis-point attachment.
#[test]
fn ctc_axis_repairs_preserve_stc09s_own_hole_attachment() {
    let a = nist("nist_stc_09_asme1_ap242-e4.stp.gz");
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    const ID: &str = "pmi:geometric_tolerance.position:f80,81,82,83:ref=A,D,B:mod=diameter_zone";
    let g = p.gdt.iter().find(|g| g.id == ID).unwrap();
    let placed = g.placed.as_ref().unwrap();
    let v = p.views.iter().find(|v| v.id == g.view).unwrap();
    assert!(
        !p.problems
            .iter()
            .any(|p| p.code == "annotation_ink_overlap" && p.subjects.contains(&ID.to_owned()))
    );
    // Over its two holes' own size callout (`2× ⌀5.613`), left-aligned with it and without a
    // leader, once that callout has room (since haecceity v0.2.7's one-line deviations); else
    // on a leader to one of its own holes' rims.
    if placed.lines.is_empty() {
        let callout = p
            .dimensions
            .iter()
            .find(|d| d.id == "d:f:hole:f80/bore.diameter")
            .unwrap();
        let holes: Vec<&str> = callout
            .measurements
            .iter()
            .filter(|m| m.parameter == "bore.diameter")
            .map(|m| m.feature.as_str())
            .collect();
        assert_eq!(holes, ["f:hole:f80", "f:hole:f82"]);
        let b = callout.placed.as_ref().unwrap().label_box;
        assert_eq!(placed.label_box[0], b[0]);
        assert!(placed.label_box[1] >= b[3] && placed.label_box[1] - b[3] < 1.0);
        assert!(
            g.basis.contains("over the holes' size callout"),
            "{}",
            g.basis
        );
        return;
    }
    let on_own_rim = g.faces.iter().any(|&fi| {
        let f = &a.face_attachments[fi];
        let k = f.axis.unwrap();
        let across: Vec<_> = (0..3).filter(|&j| j != k).collect();
        let mut c = [0.0; 3];
        for (&j, &s) in across.iter().zip(&f.support) {
            c[j] = s;
        }
        let r = (f.sites[0][across[0]] - c[across[0]]).hypot(f.sites[0][across[1]] - c[across[1]])
            * v.scale;
        let c = page(&a, v, c);
        ((placed.tip[0] - c[0]).hypot(placed.tip[1] - c[1]) - r).abs() < 1e-5
    });
    assert!(
        on_own_rim,
        "the frame must identify one of its own two holes"
    );
}

#[test]
fn a_spool_frame_clears_the_reporters_centreline_contact_band() {
    let a = nist("hw_spool_fits_intent.step.gz");
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    const ID: &str = "pmi:geometric_tolerance.perpendicularity:f55:ref=A:mod=diameter_zone";
    assert_cylinder_attachment(&a, &p, ID);
    assert!(
        !p.problems
            .iter()
            .any(|p| p.code == "leader_along_centreline" && p.subjects.contains(&ID.to_owned()))
    );
}

#[test]
fn axis_repairs_keep_stc10s_eight_hole_frame_on_its_own_rim() {
    let a = nist("nist_stc_10_asme1_ap242-e2.stp.gz");
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    const ID: &str =
        "pmi:geometric_tolerance.position:f93,94,95,96,97,98,99,100:ref=A,B,C:mod=diameter_zone";
    let g = p.gdt.iter().find(|g| g.id == ID).unwrap();
    let placed = g.placed.as_ref().unwrap();
    if placed.lines.is_empty() {
        assert!(g.basis.contains("size callout") || g.basis.contains("control frame"));
        return;
    }
    let v = p.views.iter().find(|v| v.id == g.view).unwrap();
    assert!(
        g.faces.iter().any(|&fi| {
            let f = &a.face_attachments[fi];
            let k = f.axis.unwrap();
            let across: Vec<_> = (0..3).filter(|&j| j != k).collect();
            let mut c = [0.0; 3];
            for (&j, &s) in across.iter().zip(&f.support) {
                c[j] = s;
            }
            let r = (f.sites[0][across[0]] - c[across[0]])
                .hypot(f.sites[0][across[1]] - c[across[1]])
                * v.scale;
            let c = page(&a, v, c);
            ((placed.tip[0] - c[0]).hypot(placed.tip[1] - c[1]) - r).abs() < 1e-5
        }),
        "the eight-hole frame must keep identifying one of its own surfaces"
    );
}

/// A size/depth label and its hole's position frame are one callout even when
/// tolerance notation makes the size label taller than its original lane.
#[test]
fn a_hole_position_frame_stays_with_its_taller_size_label() {
    let a = nist("string_post_tapped.step.gz");
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    let size = plan
        .dimensions
        .iter()
        .find(|d| d.view == "v:front" && d.kind == "callout" && d.label.starts_with("⌀1.7"))
        .expect("the cross-hole size and depth");
    let frame = plan
        .gdt
        .iter()
        .find(|g| g.view == size.view && g.kind == "control_frame" && g.faces == [5])
        .expect("the cross-hole position frame");
    let label = size.placed.as_ref().unwrap();
    let position = frame.placed.as_ref().unwrap();
    assert!(
        position.lines.is_empty() && position.arrows.is_empty(),
        "one shared leader"
    );
    assert!(
        position.label_box[0] < label.label_box[2] && position.label_box[2] > label.label_box[0]
    );
    let gap = (position.label_box[1] - label.label_box[3])
        .abs()
        .min((label.label_box[1] - position.label_box[3]).abs());
    assert!(
        gap <= 0.5 + 1e-6,
        "the frame is stacked against its label: {gap}"
    );
    assert_eq!(label.arrows.len(), 1);
    let view = plan.views.iter().find(|v| v.id == size.view).unwrap();
    // frame laid out as CAD's TOLERANCE (Paul 2026-11-12): the 5 mm frame
    // now fits beside the upper half of the hole, left of the outline. Prove
    // both boxes and the shelf clear that outline, retaining the short outward route.
    let height = (a.envelope[1][2] - a.envelope[0][2]) * view.scale;
    let left = view.at[0] - 0.5 * (a.envelope[1][0] - a.envelope[0][0]) * view.scale;
    assert!(label.label_box[2].max(position.label_box[2]) < left);
    assert!(label.lines.last().unwrap()[1][0] < left);
    assert!((position.label_box[3] - position.label_box[1] - 5.0).abs() < 1e-6);
    let tip = label.lines[0][0];
    assert!(label.lines.iter().flatten().all(|p| p[1] >= tip[1] - 1e-6));
    let length: f64 = label
        .lines
        .iter()
        .map(|[a, b]| (b[0] - a[0]).hypot(b[1] - a[1]))
        .sum();
    assert!(
        length < 0.5 * height,
        "a local leader, not a route across the view: {length}"
    );
}
