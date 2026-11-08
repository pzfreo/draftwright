//! Native regressions for text association and collision-driven lane promotion.
use draftwright::model::{self, Spec};
#[path = "common/corpus.rs"]
mod corpus;

fn plan(path: &str) -> Option<model::Plan> {
    plan_with(path, &Spec::default())
}

fn plan_with(path: &str, spec: &Spec) -> Option<model::Plan> {
    let a = model::analyse_native(&corpus::dir()?.join(path)).unwrap().1;
    Some(model::plan_recovered(&a, spec).unwrap())
}

fn adjacent(d: &draftwright::dims::PlannedDimension) -> bool {
    let p = d.placed.as_ref().unwrap();
    let k = usize::from(p.rotation != 0.0);
    let lo = p.arrows[0][0][k].min(p.arrows[1][0][k]);
    let hi = p.arrows[0][0][k].max(p.arrows[1][0][k]);
    let b = p.label_box;
    (b[k] >= lo - 1e-6 && b[k + 2] <= hi + 1e-6)
        || (b[k + 2] <= lo && lo - b[k + 2] < 5.7 - 1e-6)
        || (b[k] >= hi && b[k] - hi < 5.7 - 1e-6)
}

type Seg = [[f64; 2]; 2];

/// Whether segment *s* passes through the open box *b* (endpoint touches are not crossings).
fn crosses([p, q]: Seg, b: [f64; 4]) -> bool {
    let (mut lo, mut hi) = (0.0f64, 1.0f64);
    for k in 0..2 {
        let s = q[k] - p[k];
        if s.abs() < 1e-12 {
            if !(b[k] < p[k] && p[k] < b[k + 2]) {
                return false;
            }
        } else {
            let (a, z) = ((b[k] - p[k]) / s, (b[k + 2] - p[k]) / s);
            lo = lo.max(a.min(z));
            hi = hi.min(a.max(z));
        }
    }
    hi - lo > 1e-9
}

/// Ids of other items (dimensions and GD&T leaders) with a stroke within 1 paper mm of
/// *id*'s label.
fn strokes_near(p: &model::Plan, id: &str) -> Vec<String> {
    let mut items: Vec<(&str, Vec<Seg>)> = p
        .dimensions
        .iter()
        .filter(|d| d.id != id)
        .filter_map(|d| {
            let q = d.placed.as_ref()?;
            Some((
                d.id.as_str(),
                q.lines.iter().chain(&q.arrows).copied().collect(),
            ))
        })
        .collect();
    items.extend(p.gdt.iter().filter_map(|g| {
        let q = g.placed.as_ref()?;
        Some((
            g.id.as_str(),
            q.lines.iter().chain(&q.arrows).copied().collect(),
        ))
    }));
    let d = p.dimensions.iter().find(|d| d.id == id).unwrap();
    let b = d.placed.as_ref().unwrap().label_box;
    let pad = [b[0] - 1.0, b[1] - 1.0, b[2] + 1.0, b[3] + 1.0];
    items
        .into_iter()
        .filter(|(_, strokes)| strokes.iter().any(|l| crosses(*l, pad)))
        .map(|(i, _)| i.to_string())
        .collect()
}

#[test]
fn a_moved_label_keeps_clear_of_strokes_it_never_touched() {
    // string_post's 5.5 stood clear above its span; moving it inside put it on the hole
    // location's dimension line (gap 0.02 mm). Newly approached strokes need 1 mm too.
    // On the sheet the defect was captured on: H6 now draws string_post on A3 at 5:1, where
    // settlement does not move this label at all.
    let mut captured = Spec::default();
    captured.sheet.size = Some("A4".into());
    captured.sheet.scale = Some("2:1".into());
    let Some(p) = plan_with("gramel/string_post.step", &captured) else {
        eprintln!("SKIPPED: the corpus is not found");
        return;
    };
    assert_eq!(
        strokes_near(&p, "d:f:turned_step:f0/step_position.length"),
        Vec::<String>::new()
    );
}

#[test]
fn short_native_dimensions_keep_their_text_on_their_span() {
    for (name, labels) in [
        ("mfcadpp/1000.step", vec!["6.925", "8.854", "10.854"]),
        ("mfcadpp/10020.step", vec!["2.845", "3.886"]),
        ("mfcadpp/10007.step", vec!["14.031"]),
        ("gramel/string_post.step", vec!["3.75", "6.5", "7.6"]),
    ] {
        let Some(p) = plan(name) else {
            eprintln!("SKIPPED: the corpus is not found");
            return;
        };
        for label in labels {
            let ds: Vec<_> = p
                .dimensions
                .iter()
                .filter(|d| d.kind == "linear" && d.label == label && d.placed.is_some())
                .collect();
            assert!(!ds.is_empty(), "{name}: missing {label}");
            assert!(
                ds.iter().all(|d| adjacent(d)),
                "{name}: {label} away from its own span"
            );
        }
    }
}

#[test]
fn stc09_slot_length_stays_beside_its_peer_and_gdt_corridors() {
    use std::io::Read as _;
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    let bytes =
        std::fs::read(root.join("tests/fixtures/pmi/step/nist_stc_09_asme1_ap242-e4.stp.gz"))
            .unwrap();
    let mut step = Vec::new();
    flate2::read::GzDecoder::new(bytes.as_slice())
        .read_to_end(&mut step)
        .unwrap();
    let dir = std::path::Path::new(env!("CARGO_TARGET_TMPDIR")).join("label_lanes");
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("stc09.stp");
    std::fs::write(&path, step).unwrap();
    let mut a = model::analyse_native(&path).unwrap().1;
    // Its slots 1 and 2 are congruent, so one slot callout would state them: slot 2 is split
    // from the callout, so slot 1 keeps the length that is slot 3's peer here.
    corpus::split_slot(&mut a, "f:slot:f98");
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    let placed = |id: &str| {
        p.dimensions
            .iter()
            .find(|d| d.id == id)
            .unwrap()
            .placed
            .as_ref()
            .unwrap()
    };
    let slot = placed("d:f:slot:f102/slot_length.length");
    let peer = placed("d:f:slot:f94/slot_length.length");
    assert!(
        (slot.arrows[0][0][1] - peer.arrows[0][0][1]).abs() <= 14.0 + 1e-6,
        "a slot must remain within the next lane of its neighbouring size"
    );
    assert!(
        slot.lines[..2]
            .iter()
            .all(|[a, b]| (a[1] - b[1]).abs() < 60.0),
        "the size must not gain witnesses running across the location tower"
    );
    // The value-less `2X R` and `4X R` common labels are refused (#214).
    assert_eq!(p.gdt.len(), 26);
    // The 63.5 location sat clear outside its arrow; a move may not butt its text against
    // the perpendicularity frame's leader (x = 154.40 against a box from 154.44).
    assert_eq!(
        strokes_near(&p, "d:f:hole:f84/location.length.x"),
        Vec::<String>::new(),
        "a moved label must keep 1 mm from a GD&T leader it never touched"
    );
}

#[test]
fn precise_labels_keep_their_span_after_the_final_inventory_settles() {
    for (file, expected) in [
        (
            "mfcadpp/10047.step",
            vec![
                ("d:f:slot:1/slot_width.length", "4.53"),
                ("d:f:section_recess:6/location_pocket.z", "3.526"),
            ],
        ),
        (
            "cadgenbench_inputs/cgb207.step",
            vec![("d:step_level/step_height.length.f156", "12.001")],
        ),
    ] {
        let Some(p) = plan(file) else { return };
        for (id, label) in expected {
            let d = p.dimensions.iter().find(|d| d.id == id).unwrap();
            assert_eq!(d.label, label);
            assert!(adjacent(d), "{file}: {id} is detached from its span");
            let placed = d.placed.as_ref().unwrap();
            let k = usize::from(placed.rotation != 0.0);
            let lo = placed.arrows[0][0][k].min(placed.arrows[1][0][k]);
            let hi = placed.arrows[0][0][k].max(placed.arrows[1][0][k]);
            let b = placed.label_box;
            let line = placed.arrows[0][0][1 - k];
            let normal = [line.min(b[1 - k]), line.max(b[3 - k])];
            for peer in p
                .dimensions
                .iter()
                .filter(|q| q.id != d.id && q.kind == "linear" && q.view == d.view)
            {
                let Some(q) = &peer.placed else { continue };
                for [a, z] in q.lines.iter().take(2) {
                    let between = (b[k] + 1e-6 < a[k] && a[k] < lo - 1e-6)
                        || (hi + 1e-6 < a[k] && a[k] < b[k + 2] - 1e-6);
                    assert!(
                        !((a[k] - z[k]).abs() <= 1e-6
                            && between
                            && a[1 - k].min(z[1 - k]) <= normal[1]
                            && a[1 - k].max(z[1 - k]) >= normal[0]),
                        "{file}: {} separates {id}'s text from its span",
                        peer.id
                    );
                }
            }
        }
    }
    let mut spec = Spec::default();
    spec.sheet.size = Some("A1".into()); // Keep the complete inventory for precision checks.
    let Some(p) = plan_with("nist/nist_ftc_08_asme1_rc.stp", &spec) else {
        return;
    };
    let slot = p
        .dimensions
        .iter()
        .find(|d| d.id == "d:f:slot:1/slot_width.length")
        .unwrap();
    assert_eq!(slot.label, "192.21");
    let fillet = p
        .dimensions
        .iter()
        .find(|d| {
            d.measurements
                .iter()
                .any(|m| m.parameter == "fillet.radius" && (m.value - 1.524).abs() < 1e-6)
        })
        .unwrap();
    assert!(
        fillet
            .measurements
            .iter()
            .all(|m| (m.value - 1.524).abs() < 1e-6)
    );
    // Exact since the octagon's stations (#217) changed the inventory around it; mfcadpp/1004
    // below keeps the established one-decimal text on its unchanged ink run.
    // v0.2.3 (#127) adds eight proved inside rounds to the same radius.
    assert_eq!(fillet.label, "16× R1.524");
    if let Some(p) = plan_with("mfcadpp/1004.step", &Spec::default()) {
        let hole = p
            .dimensions
            .iter()
            .find(|d| d.id == "d:f:hole:1/bore.diameter")
            .unwrap();
        assert!((hole.measurements[0].value - 7.44654).abs() < 1e-4);
        assert_eq!(hole.label, "⌀7.4 ↧ 5.9");
        assert!(hole.basis.contains("one-decimal text retained"));
    }
    assert!(
        !p.problems.iter().any(|q| {
            matches!(
                q.code.as_str(),
                "extension_through_feature" | "leader_through_feature"
            ) && q
                .subjects
                .iter()
                .any(|id| id == &slot.id || id == "d:f:section_recess:1/seat_diameter.diameter")
        }),
        "a table must not freeze newly obstructed old witnesses or seat leaders"
    );
}
