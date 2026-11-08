//! S3: seeded edits preserve pinned geometry, changes re-resolve intent, exports replay it.
#[path = "common/corpus.rs"]
mod corpus;

#[path = "common/local_fix.rs"]
mod local_fix;

use draftwright::model::{self, Analysis, Plan, Spec};
use serde_json::{Value, json};
use std::collections::{BTreeMap, BTreeSet};
use std::io::Read as _;
use std::path::{Path, PathBuf};

fn fixture(p: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(p)
}
fn plate() -> (draftwright::haecceity::Part, Analysis) {
    model::analyse_native(&fixture("parts/mounting_plate.step")).unwrap()
}
fn slice() -> Vec<(String, Analysis)> {
    let mut parts = vec![("plate".into(), plate().1)];
    if let Some(c) = corpus::dir() {
        let m = c.join("mfcadpp/10127.step");
        parts.push(("10127".into(), model::analyse_native(&m).unwrap().1));
        for (name, file) in [
            ("GRM-03", "gramel/GRM-03_thumbwheel_drive_screw.step"),
            ("CTC-01", "nist/nist_ctc_01_asme1_rd.stp"),
        ] {
            parts.push((name.into(), model::analyse_native(&c.join(file)).unwrap().1));
        }
    }
    parts
}
fn pmi_part() -> (draftwright::haecceity::Part, Analysis) {
    let file = fixture("pmi/step/nist_ctc_01_asme1_ap242-e1.stp.gz");
    let mut bytes = Vec::new();
    flate2::read::GzDecoder::new(std::fs::File::open(file).unwrap())
        .read_to_end(&mut bytes)
        .unwrap();
    static NEXT: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
    let n = NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let directory = Path::new(env!("CARGO_TARGET_TMPDIR"))
        .join("placement_intent")
        .join(format!("{}-{n}", std::process::id()));
    std::fs::create_dir_all(&directory).unwrap();
    let path = directory.join("nist_ctc_01_asme1_ap242-e1.stp");
    std::fs::write(&path, bytes).unwrap();
    model::analyse_native(&path).unwrap()
}
fn pmi() -> Analysis {
    pmi_part().1
}
fn spec(id: &str, place: Value) -> Spec {
    serde_json::from_value(json!({"dimensions":{id:{"place":place}}})).unwrap()
}
fn geometry(p: &Plan, id: &str) -> Value {
    if let Some(d) = p.dimensions.iter().find(|d| d.id == id) {
        return serde_json::to_value(&d.placed).unwrap();
    }
    if let Some(g) = p.gdt.iter().find(|g| g.id == id) {
        return serde_json::to_value(&g.placed).unwrap();
    }
    if let Some(v) = p.views.iter().find(|v| v.id == id) {
        return json!({"at":v.at,"area":v.area,"scale":v.scale,"direction":v.direction,"up":v.up});
    }
    Value::Null
}
fn path(id: &str) -> String {
    if id.starts_with("v:") {
        format!("views/{}", id.trim_start_matches("v:"))
    } else {
        format!(
            "{}/{}",
            if id.starts_with("pmi:") {
                "annotations"
            } else {
                "dimensions"
            },
            draftwright::items::key(id)
        )
    }
}
fn set(s: &Spec, id: &str, field: &str, value: Value) -> Spec {
    model::edit(
        s,
        &json!([{"op":"set","path":format!("{}/{field}",path(id)),"value":value}]),
    )
    .unwrap()
}
fn side(p: &Plan, id: &str) -> &'static str {
    match p
        .dimensions
        .iter()
        .find(|d| d.id == id)
        .and_then(|d| d.placed.as_ref())
        .map(|p| p.rotation)
    {
        Some(90.0) => "right",
        _ => "above",
    }
}
fn random(seed: &mut u64, n: usize) -> usize {
    *seed = seed
        .wrapping_mul(6364136223846793005)
        .wrapping_add(1442695040888963407);
    (*seed >> 32) as usize % n
}
/// A view's place beside another view (never the page): its projection partner `front`, or for
/// the front view `plan` or `side`, on the side it already lies.
fn beside(p: &Plan, id: &str, gap: usize) -> Value {
    let me = p.views.iter().find(|v| v.id == id).unwrap();
    let anchor = ["v:front", "v:plan", "v:side"]
        .into_iter()
        .filter(|a| *a != id)
        .find_map(|a| p.views.iter().find(|v| v.id == a))
        .unwrap();
    let c = |b: [f64; 4]| [0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3])];
    let (m, o) = (c(me.area), c(anchor.area));
    let side = if (m[0] - o[0]).abs() > (m[1] - o[1]).abs() {
        if m[0] > o[0] { "right" } else { "left" }
    } else if m[1] > o[1] {
        "above"
    } else {
        "below"
    };
    json!({"relative_to":anchor.id.trim_start_matches("v:"),"side":side,"gap":gap,"align":"middle"})
}
fn pin(s: &Spec, p: &Plan, id: &str, lane: usize) -> Spec {
    let place = if p.views.iter().any(|v| v.id == id) {
        let mut place = beside(p, id, 10 + lane * 5);
        place["pinned"] = json!(true);
        place
    } else {
        let view = draftwright::items::items(p)
            .into_iter()
            .find(|i| i.id == id)
            .unwrap()
            .view
            .unwrap();
        json!({"view":view.trim_start_matches("v:"),"side":side(p,id),"lane":lane,"along":"middle","pinned":true})
    };
    set(s, id, "place", place)
}

#[test]
fn placement_vocabulary_is_validated_without_page_coordinates() {
    let s = spec(
        "d:envelope/width.length",
        json!({"view":"front","side":"above","lane":2,"along":0.25,"pinned":true}),
    );
    for bad in [
        json!({"at":[10,20]}),
        json!({"lane":-1}),
        json!({"lane":1001}),
        json!({"along":1.1}),
        json!({"side":"north"}),
        json!({"view":"iso"}),
    ] {
        assert!(model::edit(&s,&json!([{"op":"set","path":"dimensions/d:envelope~1width.length/place","value":bad}])).is_err(),"{bad}");
    }
    for along in [
        json!(0),
        json!(1),
        json!("start"),
        json!("middle"),
        json!("end"),
        json!("outside"),
    ] {
        set(&s, "d:envelope/width.length", "place/along", along);
    }
    let cycle = json!({"views":{"front":{"place":{"relative_to":"plan","side":"below"}},"plan":{"place":{"relative_to":"front","side":"above"}}}});
    assert!(
        model::edit(
            &Spec::default(),
            &json!([{"op":"set","path":"views","value":cycle["views"]}])
        )
        .is_err()
    );
    for (_, a) in slice() {
        let base = model::plan_recovered(&a, &Spec::default()).unwrap();
        let id = &base
            .dimensions
            .iter()
            .find(|d| d.id.starts_with("d:envelope/"))
            .unwrap()
            .id;
        let explicit = spec(id, json!({"pinned":false}));
        let p = model::plan_recovered(&a, &explicit).unwrap();
        for d in &base.dimensions {
            assert_eq!(geometry(&base, &d.id), geometry(&p, &d.id));
        }
        for v in &base.views {
            assert_eq!(geometry(&base, &v.id), geometry(&p, &v.id));
        }
        for problem in base.problems.iter().filter(|p| {
            p.code.starts_with("annotation_")
                && p.subjects
                    .iter()
                    .any(|id| base.dimensions.iter().any(|d| d.id == *id))
        }) {
            assert!(!problem.fix.is_empty(), "{problem:?}");
            model::edit(
                &Spec::default(),
                &serde_json::to_value(&problem.fix).unwrap(),
            )
            .unwrap();
        }
    }
    let (_, a) = plate();
    let p = model::plan(&a, &s).unwrap();
    let d = p
        .dimensions
        .iter()
        .find(|d| d.id == "d:envelope/width.length")
        .unwrap();
    assert!(d.from_spec.contains(&"place".into()));
    let a = pmi();
    let base = model::plan_recovered(&a, &Spec::default()).unwrap();
    let angle = base
        .dimensions
        .iter()
        .find(|d| d.kind == "angular")
        .unwrap();
    for along in [
        json!("start"),
        json!("middle"),
        json!("end"),
        json!("outside"),
    ] {
        let s = spec(
            &angle.id,
            json!({"view":"plan","lane":2,"along":along,"pinned":true}),
        );
        let p = model::plan_recovered(&a, &s).unwrap();
        let held = geometry(&p, &angle.id);
        assert!(!held.is_null());
        let changed = set(&s, "d:envelope/width.length", "omit", json!(true));
        assert_eq!(
            geometry(&model::plan_recovered(&a, &changed).unwrap(), &angle.id),
            held
        );
    }
    let (_, a) = plate();
    let legacy = set(&Spec::default(), &d.id, "view", json!("plan"));
    let legacy = set(&legacy, &d.id, "place", json!({"pinned":true}));
    assert_eq!(
        model::plan_recovered(&a, &legacy)
            .unwrap()
            .dimensions
            .iter()
            .find(|i| i.id == d.id)
            .unwrap()
            .view,
        "v:plan"
    );
    let bad = spec(&d.id, json!({"side":"left"}));
    let plan = model::plan(&a, &bad).unwrap();
    assert!(
        plan.problems
            .iter()
            .any(|p| p.code == "placement_unapplied")
    );
    assert!(
        plan.dimensions
            .iter()
            .any(|shown| shown.id == d.id && shown.placed.is_some())
    );
}

#[test]
fn unchanged_view_pins_at_the_margin_are_not_off_sheet() {
    let Some(c) = corpus::dir() else { return };
    for name in ["1000", "10000", "1004"] {
        let (_, a) = model::analyse_native(&c.join(format!("mfcadpp/{name}.step"))).unwrap();
        let base = model::plan_recovered(&a, &Spec::default()).unwrap();
        // Pin the reported default gap: automatic bands now spend spare paper.
        let front = base.views.iter().find(|v| v.id == "v:front").unwrap();
        let top = base.views.iter().find(|v| v.id == "v:plan").unwrap();
        let gap = top.area[1] - front.area[3];
        let s = set(
            &Spec::default(),
            "v:plan",
            "place",
            json!({"relative_to":"front","side":"above","gap":gap,"align":"middle","pinned":true}),
        );
        let pinned = model::plan_recovered(&a, &s).unwrap();
        // The pin holds the view's position, scale and orientation. Its reserved area is what
        // the unpinned neighbours leave (the side view re-spaces freely once the plan is
        // fixed), as in seeded_edits_on_slice_parts_preserve_pinned_geometry.
        let held = |p: &model::Plan| {
            let mut g = geometry(p, "v:plan");
            g.as_object_mut().unwrap().remove("area");
            g
        };
        assert_eq!(held(&base), held(&pinned), "{name}");
        assert!(
            !pinned
                .problems
                .iter()
                .any(|p| p.code == "pinned_view_off_sheet"),
            "{name}: {:?}",
            pinned.problems
        );
    }
}

#[test]
fn pinning_without_offsets_keeps_automatic_geometry() {
    for (name, a) in slice()
        .into_iter()
        .chain(std::iter::once(("AP242".into(), pmi())))
    {
        let base = model::plan_recovered(&a, &Spec::default()).unwrap();
        for id in base
            .dimensions
            .iter()
            .filter(|d| d.placed.is_some())
            .map(|d| &d.id)
            .chain(
                base.gdt
                    .iter()
                    .filter(|g| g.placed.is_some())
                    .map(|g| &g.id),
            )
        {
            let s = set(&Spec::default(), id, "place", json!({"pinned":true}));
            let p = model::plan_recovered(&a, &s).unwrap();
            assert_eq!(
                serde_json::to_vec(&geometry(&base, id)).unwrap(),
                serde_json::to_vec(&geometry(&p, id)).unwrap(),
                "{name} {id}"
            );
            let other = base.dimensions.iter().find(|d| d.id != *id).unwrap();
            let changed = set(&s, &other.id, "omit", json!(true));
            assert_eq!(
                geometry(&p, id),
                geometry(&model::plan_recovered(&a, &changed).unwrap(), id),
                "{name} {id}"
            );
        }
    }
}

/// The expanded opening inventory can settle adjacent hole marks again. An
/// attached datum pin must hold its published carrier through those passes,
/// while an empty placement edit leaves its pinned drawing's ink unchanged.
#[test]
fn notch_lane_edits_keep_the_attached_datum_in_its_published_carrier() {
    let a = pmi();
    let base = model::plan_recovered(&a, &Spec::default()).unwrap();
    let id = "pmi:datum.datum:f75,76:t=C";
    for place in [
        json!({"pinned":true}),
        json!({"pinned":true,"view":"plan","lane":2,"side":"above"}),
    ] {
        let s = set(&Spec::default(), id, "place", place);
        let pinned = model::plan_recovered(&a, &s).unwrap();
        let held = geometry(&pinned, id);
        assert_eq!(held, geometry(&base, id));
        // A pin may repack free views, as on main. Its own carrier and
        // attached size frame must retain the published geometry.
        let carrier = &base.gdt.iter().find(|g| g.id == id).unwrap().view;
        let (mut actual, mut expected) = (geometry(&pinned, carrier), geometry(&base, carrier));
        actual.as_object_mut().unwrap().remove("area");
        expected.as_object_mut().unwrap().remove("area");
        assert_eq!(actual, expected);
        let free = "d:envelope/width.length";
        for (field, value) in [("tolerance", json!("±0.1")), ("place", json!({}))] {
            let edited = set(&s, free, field, value);
            let changed = model::plan_recovered(&a, &edited).unwrap();
            assert_eq!(geometry(&changed, id), held, "{field}");
            if field == "place" {
                for v in &pinned.views {
                    let (mut actual, mut expected) =
                        (geometry(&changed, &v.id), geometry(&pinned, &v.id));
                    actual.as_object_mut().unwrap().remove("area");
                    expected.as_object_mut().unwrap().remove("area");
                    assert_eq!(actual, expected);
                }
                for d in &pinned.dimensions {
                    let other = changed
                        .dimensions
                        .iter()
                        .find(|other| other.id == d.id)
                        .unwrap();
                    assert_eq!(
                        (&other.label, &other.terms, &other.measurements),
                        (&d.label, &d.terms, &d.measurements)
                    );
                    assert_eq!(geometry(&changed, &d.id), geometry(&pinned, &d.id));
                }
            }
        }
    }
}

#[test]
fn seeded_edits_on_slice_parts_preserve_pinned_geometry() {
    let mut parts = slice();
    parts.push(("CTC-01 AP242".into(), pmi()));
    for (name, a) in parts {
        let base = model::plan_recovered(&a, &Spec::default()).unwrap();
        let eligible: Vec<String> = base
            .dimensions
            .iter()
            .filter(|d| d.placed.is_some() && d.kind != "angular")
            .map(|d| d.id.clone())
            .chain(
                base.gdt
                    .iter()
                    .filter(|g| g.placed.is_some())
                    .map(|g| g.id.clone()),
            )
            .chain(
                base.views
                    .iter()
                    .filter(|v| v.id == "v:iso")
                    .map(|v| v.id.clone()),
            )
            .collect();
        for seed in [0x563, 0x314159] {
            let mut rng = seed;
            let n = 1 + random(&mut rng, eligible.len().min(4));
            let mut pool = eligible.clone();
            let mut pins = BTreeSet::new();
            let mut s = Spec::default();
            for _ in 0..n {
                let id = pool.remove(random(&mut rng, pool.len()));
                s = pin(&s, &base, &id, random(&mut rng, 3));
                pins.insert(id);
            }
            let pinned = model::plan_recovered(&a, &s).unwrap();
            let snapshot: BTreeMap<_, _> = pins
                .iter()
                .map(|id| (id.clone(), geometry(&pinned, id)))
                .collect();
            assert!(
                snapshot.values().all(|v| !v.is_null()),
                "{name}: {snapshot:?}"
            );
            let mut distribution = BTreeMap::new();
            for problem in &pinned.problems {
                *distribution.entry(problem.code.clone()).or_insert(0) += 1;
            }
            let mut baseline = BTreeMap::new();
            for problem in &base.problems {
                *baseline.entry(problem.code.clone()).or_insert(0) += 1;
            }
            let ink_codes = [
                "annotation_ink_overlap",
                "annotation_over_view",
                "annotation_line_over_view",
                "annotation_leader_crossing",
                "annotation_outside_area",
            ];
            let ink = |codes: &BTreeMap<String, usize>| {
                ink_codes
                    .iter()
                    .map(|c| codes.get(*c).copied().unwrap_or(0))
                    .sum::<usize>()
            };
            eprintln!(
                "pins {name} seed={seed} N={n}: off-sheet {} -> {}, ink {} -> {}; {distribution:?}",
                baseline.get("annotation_off_sheet").copied().unwrap_or(0),
                distribution
                    .get("annotation_off_sheet")
                    .copied()
                    .unwrap_or(0),
                ink(&baseline),
                ink(&distribution)
            );
            let mut anchors: BTreeSet<_> = draftwright::items::items(&pinned)
                .into_iter()
                .filter(|i| pins.contains(&i.id))
                .filter_map(|i| i.view)
                .collect();
            // A pinned view sits relative to its anchor view: editing the anchor is editing the
            // pin's own intent, so the anchor chain is not one of the "other" items.
            for _ in 0..8 {
                for (name, v) in &s.views {
                    if let Some(place) = &v.place
                        && (pins.contains(&format!("v:{name}"))
                            || anchors.contains(&format!("v:{name}")))
                    {
                        anchors.insert(format!("v:{}", place.relative_to));
                    }
                }
            }
            let mut free: Vec<String> = eligible
                .iter()
                .filter(|id| !pins.contains(*id) && !id.starts_with("v:"))
                .cloned()
                .collect();
            free.extend(
                base.views
                    .iter()
                    .filter(|v| !pins.contains(&v.id) && !anchors.contains(&v.id))
                    .map(|v| v.id.clone()),
            );
            for _ in 0..16 {
                let id = &free[random(&mut rng, free.len())];
                s = if id.starts_with("v:") {
                    let mut place = beside(&base, id, random(&mut rng, 40));
                    place["align"] = json!("end");
                    // An edit that would close an anchor cycle is refused; the Spec stays.
                    model::edit(
                        &s,
                        &json!([{"op":"set","path":format!("{}/place",path(id)),"value":place}]),
                    )
                    .unwrap_or(s)
                } else {
                    match random(&mut rng, 3) {
                        0 => set(&s, id, "omit", json!(random(&mut rng, 2) == 0)),
                        1 if id.starts_with("d:") => set(
                            &s,
                            id,
                            "tolerance",
                            json!(if random(&mut rng, 2) == 0 {
                                "±0.1"
                            } else {
                                "±0.2"
                            }),
                        ),
                        _ => set(
                            &s,
                            id,
                            "place",
                            json!({"side":side(&base,id),"lane":random(&mut rng,4),"along":random(&mut rng,100) as f64/100.0}),
                        ),
                    }
                };
                let p = model::plan_recovered(&a, &s).unwrap();
                for (id, expected) in &snapshot {
                    // A pinned view holds its position, scale and orientation. Its reserved
                    // area is whatever its free neighbours leave unused: a neighbour whose
                    // ink grows reclaims that paper (main does the same on 10127 when the
                    // bore takes a tolerance), so the area need only still hold its ink.
                    let (mut actual, mut expected) = (geometry(&p, id), expected.clone());
                    if id.starts_with("v:") {
                        let v = p.views.iter().find(|v| v.id == *id).unwrap();
                        let b = a.iso_bounds;
                        let ink = [0, 1, 2, 3].map(|k| v.at[k % 2] + v.scale * b[k]);
                        assert!(
                            v.area[0] <= ink[0] + 1e-6
                                && v.area[1] <= ink[1] + 1e-6
                                && v.area[2] >= ink[2] - 1e-6
                                && v.area[3] >= ink[3] - 1e-6,
                            "{name} seed={seed} pin={id}: area {:?} does not hold its ink {ink:?}",
                            v.area
                        );
                        actual.as_object_mut().unwrap().remove("area");
                        expected.as_object_mut().unwrap().remove("area");
                    }
                    assert_eq!(
                        serde_json::to_vec(&actual).unwrap(),
                        serde_json::to_vec(&expected).unwrap(),
                        "{name} seed={seed} pin={id}"
                    );
                }
            }
        }
    }
}

#[test]
fn sheet_and_scale_changes_keep_or_report_every_pin() {
    for (name, a) in slice()
        .into_iter()
        .chain(std::iter::once(("AP242".into(), pmi())))
    {
        let base = model::plan_recovered(&a, &Spec::default()).unwrap();
        let mut s = Spec::default();
        let mut pins = Vec::new();
        for id in base
            .dimensions
            .iter()
            .filter(|d| d.placed.is_some())
            .take(3)
            .map(|d| d.id.clone())
            .chain(
                base.gdt
                    .iter()
                    .filter(|g| g.placed.is_some())
                    .take(2)
                    .map(|g| g.id.clone()),
            )
            .chain(std::iter::once("v:front".into()))
        {
            s = pin(&s, &base, &id, 1);
            pins.push(id);
        }
        let before = serde_json::to_value(&s).unwrap();
        for (size, scale) in [("A4", "1:5"), ("A3", "2:1"), ("A0", "1:1")] {
            let changed=model::edit(&s,&json!([{"op":"set","path":"sheet/size","value":size},{"op":"set","path":"sheet/scale","value":scale}])).unwrap();
            let p = model::plan_recovered(&a, &changed).unwrap();
            let after = serde_json::to_value(&p.spec).unwrap();
            for id in &pins {
                assert_eq!(
                    before.pointer(&format!("/{}/place", path(id))),
                    after.pointer(&format!("/{}/place", path(id))),
                    "{name} {id}"
                );
                assert!(
                    !geometry(&p, id).is_null()
                        || p.problems.iter().any(|p| {
                            [
                                "pinned_item_not_drawn",
                                "placement_anchor_not_shown",
                                "pmi_not_drawn",
                            ]
                            .contains(&p.code.as_str())
                                && (p.subjects.contains(id) || p.refs.contains(id))
                        }),
                    "{name} {id}"
                );
                let ink = geometry(&p, id);
                if let Some(b) = ink.get("label_box").and_then(Value::as_array) {
                    let mut points = vec![
                        [b[0].as_f64().unwrap(), b[1].as_f64().unwrap()],
                        [b[2].as_f64().unwrap(), b[3].as_f64().unwrap()],
                    ];
                    for field in ["lines", "arrows"] {
                        for line in ink[field].as_array().unwrap() {
                            for q in line.as_array().unwrap() {
                                points.push([q[0].as_f64().unwrap(), q[1].as_f64().unwrap()]);
                            }
                        }
                    }
                    if points.iter().any(|q| {
                        q[0] < 10.0 - 1e-9
                            || q[1] < 10.0 - 1e-9
                            || q[0] > p.sheet.page[0] - 10.0 + 1e-9
                            || q[1] > p.sheet.page[1] - 10.0 + 1e-9
                    }) {
                        assert!(
                            p.problems.iter().any(
                                |p| p.code == "annotation_off_sheet" && p.subjects.contains(id)
                            ),
                            "{name} {size} {scale} {id}: off-sheet ink needs its own problem"
                        );
                    }
                }
            }
        }
        // A view pinned beside another is re-resolved against its anchor on every sheet: the
        // same relation holds, and a sheet too small for it reports the overrun.
        let moved = ["v:plan", "v:side", "v:iso"]
            .into_iter()
            .find(|v| base.views.iter().any(|b| b.id == *v))
            .unwrap();
        let name_of = moved.trim_start_matches("v:");
        let large: Spec = serde_json::from_value(json!({"sheet":{"size":"A0","scale":"1:5"},"views":{name_of:{"place":{"relative_to":"front","side":"above","gap":300,"align":"start","pinned":true}}}})).unwrap();
        let before = model::plan_recovered(&a, &large).unwrap();
        assert!(
            !before
                .problems
                .iter()
                .any(|p| p.code == "pinned_view_off_sheet" && p.subjects.contains(&moved.into())),
            "{name}"
        );
        let small = model::edit(
            &large,
            &json!([{"op":"set","path":"sheet/size","value":"A4"}]),
        )
        .unwrap();
        let after = model::plan_recovered(&a, &small).unwrap();
        for p in [&before, &after] {
            let front = p.views.iter().find(|v| v.id == "v:front").unwrap();
            let view = p.views.iter().find(|v| v.id == moved).unwrap();
            assert!(
                (view.area[1] - front.area[3] - 300.0).abs() < 1e-9,
                "{name}"
            );
            assert!((view.area[0] - front.area[0]).abs() < 1e-9, "{name}");
        }
        assert_eq!(after.spec.views, small.views);
        assert!(
            after
                .problems
                .iter()
                .any(|p| p.code == "pinned_view_off_sheet"
                    && p.subjects.contains(&moved.into())
                    && !p.fix.is_empty()),
            "{name}: a smaller sheet must report the overrun"
        );
    }
}

#[test]
fn colliding_and_off_sheet_pins_are_reported_and_never_dropped() {
    let (part, a) = plate();
    let base = model::plan_recovered(&a, &Spec::default()).unwrap();
    let hole = base
        .dimensions
        .iter()
        .find(|d| d.kind == "callout" && d.id.starts_with("d:f:hole"))
        .unwrap();
    let s = spec(
        &hole.id,
        json!({"view":hole.view.trim_start_matches("v:"),"lane":100,"pinned":true}),
    );
    let p = model::plan_recovered(&a, &s).unwrap();
    assert!(!geometry(&p, &hole.id).is_null());
    assert!(
        !p.problems
            .iter()
            .any(|p| p.code.ends_with("_dropped") && p.subjects.contains(&hole.id))
    );
    let problem = p
        .problems
        .iter()
        .find(|p| p.code == "annotation_off_sheet" && p.subjects.contains(&hole.id))
        .unwrap();
    assert!(
        local_fix::edits(problem, &p)
            .iter()
            .all(|op| op.path.ends_with("/place"))
    );
    let mut session = draftwright::session::Session::new(part, a.clone(), p.spec.clone()).unwrap();
    let delta = session.apply(&problem.fix, true).unwrap();
    let repaired = session.plan();
    assert_eq!(
        (
            &repaired.sheet.size,
            &repaired.sheet.scale,
            repaired.sheets.len()
        ),
        (&p.sheet.size, &p.sheet.scale, p.sheets.len())
    );
    assert_eq!(repaired.views.len(), p.views.len());
    let mut moved = 0;
    for before in &p.views {
        let after = repaired.views.iter().find(|v| v.id == before.id).unwrap();
        assert_eq!(after.scale, before.scale);
        if (after.at, after.area) != (before.at, before.area) {
            moved += 1;
            let notice = delta
                .notices
                .iter()
                .find(|p| p.code == "layout_replanned")
                .unwrap();
            assert!(notice.subjects.contains(&before.id));
            assert_eq!(draftwright::wording::carries_id(&notice.message), None);
        }
    }
    assert!(
        moved > 0,
        "the fixed-sheet solve derives frames from intent rather than stored coordinates"
    );
    assert!(!geometry(repaired, &hole.id).is_null());
    let rebuilt = model::plan_recovered(&a, session.spec()).unwrap();
    assert_eq!(
        serde_json::to_value(&rebuilt).unwrap(),
        serde_json::to_value(repaired).unwrap()
    );
    assert!(
        !repaired
            .problems
            .iter()
            .any(|p| p.code == "annotation_off_sheet" && p.subjects.contains(&hole.id))
    );

    if let Some(c) = corpus::dir() {
        let a = model::analyse_native(&c.join("cadgenbench_inputs/cgb245.step"))
            .unwrap()
            .1;
        let base = model::plan_recovered(&a, &Spec::default()).unwrap();
        let covered = base
            .omitted
            .iter()
            .find(|o| matches!(o.state, draftwright::items::State::Covered { .. }))
            .expect("cgb245 consolidates its coincident slot widths");
        let s = spec(&covered.id, json!({"pinned":true}));
        let p = model::plan_recovered(&a, &s).unwrap();
        let mut claims = BTreeSet::new();
        for d in &p.dimensions {
            for m in &d.measurements {
                assert!(
                    claims.insert((&m.feature, &m.parameter)),
                    "a pin duplicated {m:?}"
                );
            }
        }
        if geometry(&p, &covered.id).is_null() {
            assert!(p.problems.iter().any(|p| p.subjects.contains(&covered.id)));
        }
    }
    // Two views pinned to the same place beside the front view collide with each other.
    let other = ["v:side", "v:iso"]
        .into_iter()
        .find(|v| base.views.iter().any(|b| b.id == *v))
        .unwrap();
    let mut s = Spec::default();
    for id in ["v:plan", other] {
        s = set(
            &s,
            id,
            "place",
            json!({"relative_to":"front","side":"above","gap":10,"align":"start","pinned":true}),
        );
    }
    let p = model::plan_recovered(&a, &s).unwrap();
    for id in ["v:plan", other] {
        assert!(p.problems.iter().any(|p| p.code == "pinned_view_overlap"
            && p.subjects.contains(&id.to_string())
            && !p.fix.is_empty()));
    }
    for problem in p
        .problems
        .iter()
        .filter(|p| p.code == "pinned_view_overlap")
    {
        model::edit(&p.spec, &serde_json::to_value(&problem.fix).unwrap()).unwrap();
    }
    let s = set(&s, "v:plan", "place/gap", json!(1000));
    let p = model::plan_recovered(&a, &s).unwrap();
    assert!(
        p.problems
            .iter()
            .any(|p| p.code == "pinned_view_off_sheet" && p.subjects.contains(&"v:plan".into()))
    );
    assert!(p.views.iter().find(|v| v.id == "v:plan").unwrap().at[1] > 1000.0);
    // The page is not an anchor: a view is placed only beside another view.
    assert!(
        model::edit(
            &Spec::default(),
            &json!([{"op":"set","path":"views/front/place","value":{"relative_to":"sheet","side":"above"}}]),
        )
        .is_err()
    );
}

#[test]
fn views_and_pmi_have_relative_placement() {
    let a = pmi();
    let base = model::plan_recovered(&a, &Spec::default()).unwrap();
    let g = base.gdt.iter().find(|g| g.placed.is_some()).unwrap();
    let mut s:Spec=serde_json::from_value(json!({"views":{"plan":{"place":{"relative_to":"front","side":"above","gap":20,"align":"middle","pinned":true}}},"annotations":{g.id.clone():{"place":{"view":"front","side":"left","lane":1,"along":"middle","pinned":true}}}})).unwrap();
    let p = model::plan_recovered(&a, &s).unwrap();
    let front = p.views.iter().find(|v| v.id == "v:front").unwrap();
    let plan = p.views.iter().find(|v| v.id == "v:plan").unwrap();
    assert!((plan.area[1] - front.area[3] - 20.0).abs() < 1e-9);
    let placed = p.gdt.iter().find(|i| i.id == g.id).unwrap();
    assert_eq!(placed.view, "v:front");
    assert_eq!(placed.side, "left");
    let held = geometry(&p, &g.id);
    for d in base.dimensions.iter().take(4) {
        s = set(&s, &d.id, "omit", json!(true));
    }
    assert_eq!(
        geometry(&model::plan_recovered(&a, &s).unwrap(), &g.id),
        held
    );
    s = set(&s, "v:front", "omit", json!(true));
    let p = model::plan_recovered(&a, &s).unwrap();
    assert!(
        p.problems
            .iter()
            .any(|p| p.subjects.contains(&g.id) && !p.fix.is_empty())
    );
    assert_eq!(p.spec.annotations[&g.id].place, s.annotations[&g.id].place);

    // Free views fill around the held footprint rather than moving it.
    let (_, a) = plate();
    let base = model::plan_recovered(&a, &Spec::default()).unwrap();
    let s = set(
        &Spec::default(),
        "v:plan",
        "place",
        json!({"relative_to":"front","side":"right","gap":20,"align":"start","pinned":true}),
    );
    let p = model::plan_recovered(&a, &s).unwrap();
    assert_ne!(geometry(&base, "v:plan"), geometry(&p, "v:plan"));
    let held = p.views.iter().find(|v| v.id == "v:plan").unwrap();
    assert!(
        p.views
            .iter()
            .filter(|v| v.id != "v:plan")
            .all(|v| !overlap(v.area, held.area)),
        "views: {:?}",
        p.views.iter().map(|v| (&v.id, v.area)).collect::<Vec<_>>()
    );
}
fn overlap(a: [f64; 4], b: [f64; 4]) -> bool {
    a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1]
}

#[test]
fn exported_spec_with_place_renders_identically() {
    for (part, a) in [plate(), pmi_part()] {
        let s = spec(
            "d:envelope/width.length",
            json!({"side":"above","along":"end","pinned":true}),
        );
        let s = set(
            &s,
            "v:plan",
            "place",
            json!({"relative_to":"front","side":"above","gap":10,"align":"middle","pinned":true}),
        );
        let base = model::plan_recovered(&a, &s).unwrap();
        let s = if let Some(g) = base.gdt.iter().find(|g| g.placed.is_some()) {
            pin(&s, &base, &g.id, 1)
        } else {
            s
        };
        let p = model::plan_recovered(&a, &s).unwrap();
        let font = draftwright::text::Font::new(
            std::fs::read(
                PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"),
            )
            .unwrap(),
        )
        .unwrap();
        let first = model::render(&part, &a, &p, &font).unwrap();
        let fonts = |name: &str| {
            std::fs::read(
                PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                    .join("fonts")
                    .join(name),
            )
            .ok()
        };
        let files = vec![
            first.svg().into_bytes(),
            first.dxf(),
            first.pdf(&fonts),
            first.png(25.0).unwrap(),
        ];
        for file in files {
            let embedded = model::embedded_spec(&file).unwrap().unwrap();
            assert_eq!(
                embedded.dimensions["d:envelope/width.length"].place,
                s.dimensions["d:envelope/width.length"].place
            );
            assert_eq!(embedded.annotations, p.spec.annotations);
            assert_eq!(embedded.views, p.spec.views);
            let again = model::plan_recovered(&a, &embedded).unwrap();
            let drawn = model::render(&part, &a, &again, &font).unwrap();
            assert_eq!(first.svg(), drawn.svg());
            assert_eq!(first.dxf(), drawn.dxf());
            assert_eq!(first.pdf(&fonts), drawn.pdf(&fonts));
            assert_eq!(first.png(25.0).unwrap(), drawn.png(25.0).unwrap());
        }
    }
}

#[test]
fn missing_pmi_placement_on_a_revision_is_inactive() {
    let mut a = pmi();
    let base = model::plan_recovered(&a, &Spec::default()).unwrap();
    let g = base.gdt.iter().find(|g| g.placed.is_some()).unwrap();
    let mut s = set(&Spec::default(), &g.id, "place", json!({"pinned":true}));
    s.binding = Some(a.binding.clone());
    a.binding.source_sha256 = "revised-part".into();
    a.pmi = None;
    let p = model::plan_recovered(&a, &s).unwrap();
    assert_eq!(p.spec.annotations, s.annotations);
    assert_eq!(p.spec.binding, s.binding);
    let problem = p
        .problems
        .iter()
        .find(|p| p.code == "spec_entries_inactive" && p.refs.contains(&g.id))
        .unwrap();
    assert!(
        problem
            .fix
            .iter()
            .any(|op| op.path == format!("annotations/{}", draftwright::items::key(&g.id)))
    );
    let clean = model::edit(&s, &serde_json::to_value(&problem.fix).unwrap()).unwrap();
    assert!(clean.annotations.is_empty());
    assert!(model::plan_recovered(&a, &clean).is_ok());
}

#[test]
fn a_bare_pin_relays_its_own_label_when_its_text_changes() {
    let mut checked = BTreeMap::<String, usize>::new();
    for (name, a) in slice() {
        let base = model::plan_recovered(&a, &Spec::default()).unwrap();
        // Hold the frame while changing text; automatic paper may otherwise change with it.
        let fixed = Spec {
            sheet: model::SheetSpec {
                size: Some(base.sheet.size.clone()),
                scale: Some(base.sheet.scale.clone()),
                ..Default::default()
            },
            ..Default::default()
        };
        for d in base
            .dimensions
            .iter()
            .filter(|d| d.placed.is_some() && d.kind != "angular")
        {
            let pinned = set(&fixed, &d.id, "place", json!({"pinned":true}));
            let held = model::plan_recovered(&a, &pinned).unwrap();
            let Ok(toleranced) = model::edit(
                &pinned,
                &json!([{"op":"set","path":format!("{}/tolerance",path(&d.id)),"value":"±0.25"}]),
            ) else {
                continue;
            };
            let Ok(p) = model::plan_recovered(&a, &toleranced) else {
                continue;
            };
            let free = model::plan_recovered(
                &a,
                &model::edit(
                    &fixed,
                    &json!([{"op":"set","path":format!("{}/tolerance",path(&d.id)),"value":"±0.25"}]),
                )
                .unwrap(),
            )
            .unwrap();
            let get = |p: &Plan| p.dimensions.iter().find(|i| i.id == d.id).cloned();
            let (Some(was), Some(now), Some(free)) = (get(&held), get(&p), get(&free)) else {
                continue;
            };
            if now.label == was.label {
                continue;
            }
            let (was, now, free) = (
                was.placed.unwrap(),
                now.placed.unwrap(),
                free.placed.unwrap(),
            );
            let size = |b: [f64; 4]| [b[2] - b[0], b[3] - b[1]];
            // The box is the drawn label's, not the default label's.
            for k in 0..2 {
                assert!(
                    (size(now.label_box)[k] - size(free.label_box)[k]).abs() < 1e-9,
                    "{name} {}: {:?} vs {:?}",
                    d.id,
                    now.label_box,
                    free.label_box
                );
            }
            assert_ne!(size(now.label_box), size(was.label_box), "{name} {}", d.id);
            // The pinned anchor holds: arrows and the dimension's lines do not move (a callout's
            // shoulder may lengthen under a longer label).
            assert_eq!(now.arrows, was.arrows, "{name} {}", d.id);
            assert_eq!(now.rotation, was.rotation, "{name} {}", d.id);
            let n = if d.kind == "callout" {
                was.lines.len() - 1
            } else {
                was.lines.len()
            };
            assert_eq!(now.lines[..n], was.lines[..n], "{name} {}", d.id);
            *checked.entry(d.kind.clone()).or_default() += 1;
        }
    }
    eprintln!("re-laid pinned labels: {checked:?}");
    assert!(checked.get("linear").is_some_and(|n| *n > 0), "{checked:?}");
}

#[test]
fn bare_pins_do_not_restack_settled_linear_labels() {
    let Some(c) = corpus::dir() else { return };
    for (file, ids) in [
        (
            "gramel/GRM-03_thumbwheel_drive_screw.step",
            vec!["d:f:turned_step:f6/step.length"],
        ),
        (
            "cadgenbench_inputs/cgb245.step",
            vec![
                "d:f:hole:f20/location.length.x",
                "d:step_level/step_height.length.f53",
                "d:envelope/depth.length",
            ],
        ),
    ] {
        let (_, a) = model::analyse_native(&c.join(file)).unwrap();
        let base = model::plan_recovered(&a, &Spec::default()).unwrap();
        let mut s = Spec::default();
        for id in ids {
            s = set(&s, id, "place", json!({"pinned":true}));
        }
        let pinned = model::plan_recovered(&a, &s).unwrap();
        for d in base.dimensions.iter().filter(|d| d.kind == "linear") {
            assert_eq!(
                geometry(&base, &d.id),
                geometry(&pinned, &d.id),
                "{file}: {}",
                d.id
            );
        }
    }
}

#[test]
fn relative_isometric_pins_fit_the_remaining_region() {
    let Some(c) = corpus::dir() else { return };
    for (file, gap, pins) in [
        (
            "gramel/GRM-03_thumbwheel_drive_screw.step",
            40,
            vec![
                "d:f:turned_step:f2/step.diameter",
                "d:f:turned_step:f2/step.length",
            ],
        ),
        (
            "ttt_inputs/sm-hanger.step",
            0,
            vec![
                "d:f:hole:1/bore.diameter",
                "d:f:hole:1/location.length.z",
                "d:f:hole:1/location.length.y",
                "d:envelope/depth.length",
                "d:step_level/step_height.length.f64",
                "d:envelope/width.length",
            ],
        ),
    ] {
        let (_, a) = model::analyse_native(&c.join(file)).unwrap();
        let mut s = Spec::default();
        for id in pins {
            s = set(&s, id, "place", json!({"pinned":true}));
        }
        s = set(
            &s,
            "v:iso",
            "place",
            json!({"relative_to":"front","side":"right","gap":gap,"align":"start","pinned":true}),
        );
        if gap == 0 {
            s = set(
                &s,
                "v:side",
                "place",
                json!({"relative_to":"front","side":"right","gap":0,"align":"start","pinned":true}),
            );
        }
        let p = model::plan_recovered(&a, &s).unwrap();
        assert!(
            !p.problems.iter().any(|p| p.code == "pinned_view_off_sheet"
                || (matches!(
                    p.code.as_str(),
                    "annotation_over_view" | "annotation_line_over_view"
                ) && p.subjects.iter().any(|id| id == "v:iso"))),
            "{file}: {:?}",
            p.problems
        );
        assert!(p.views.iter().any(|v| v.id == "v:iso"));
    }
}

#[test]
fn right_pinned_callouts_have_clear_lanes_and_free_dimensions() {
    let (part, mut a) = model::analyse(&fixture("parts/mounting_plate.step")).unwrap();
    let features = std::fs::read_to_string(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tools/mcp-eval/features/plate.features.json"),
    )
    .unwrap();
    model::recognise(&part, &mut a, &features).unwrap();
    let ids = [
        "d:f:hole_pattern:f6/bore.diameter",
        "d:f:hole:f9/bore.diameter",
        "d:f:hole:f12/bore.diameter",
    ];
    for alongs in [["start", "middle", "end"], ["middle"; 3]] {
        let mut s = Spec::default();
        for (lane, id) in ids.iter().enumerate() {
            s = set(
                &s,
                id,
                "place",
                json!({"view":"plan","side":"right","lane":lane,"along":alongs[lane],"pinned":true}),
            );
        }
        let p = model::plan_recovered(&a, &s).unwrap();
        let problems: Vec<_> = p
            .problems
            .iter()
            .filter(|p| p.subjects.iter().any(|id| ids.contains(&id.as_str())))
            .collect();
        if alongs == ["start", "middle", "end"] {
            // The central bore's prescribed middle station crosses the small right hole.
            // Report that crossing instead of quietly moving its pin to a clear station.
            assert_eq!(problems.len(), 1, "{alongs:?}: {problems:?}");
            assert_eq!(problems[0].code, "leader_through_feature");
            assert_eq!(problems[0].subjects, [ids[1], "f:hole:f12"]);
            assert!(problems[0].refs.contains(&ids[1].into()));
        }
        let view = p.views.iter().find(|v| v.id == "v:plan").unwrap();
        let height = view.scale * (a.envelope[1][1] - a.envelope[0][1]);
        for (id, along) in ids.iter().zip(alongs) {
            let fraction = match along {
                "start" => 0.0,
                "middle" => 0.5,
                _ => 1.0,
            };
            let bend = p
                .dimensions
                .iter()
                .find(|d| d.id == *id)
                .unwrap()
                .placed
                .as_ref()
                .unwrap()
                .lines[0][1];
            assert!(
                (bend[1] - (view.at[1] + (fraction - 0.5) * height)).abs() < 1e-6,
                "{id} slid from its pinned station: {bend:?}"
            );
        }
        let boxes: Vec<_> = ids
            .iter()
            .map(|id| {
                p.dimensions
                    .iter()
                    .find(|d| d.id == *id)
                    .unwrap()
                    .placed
                    .as_ref()
                    .unwrap()
                    .label_box
            })
            .collect();
        for (i, a) in boxes.iter().enumerate() {
            for b in &boxes[i + 1..] {
                assert!(
                    a[0] >= b[2] || a[2] <= b[0] || a[1] >= b[3] || a[3] <= b[1],
                    "{alongs:?}: overlapping pinned lanes {a:?} {b:?}"
                );
            }
        }
    }
}

/// #95: the editor/MCP plan must expose the same overrun structural lint sees.
#[test]
fn grm02_off_sheet_view_relation_is_an_error_with_an_unpin_fix() {
    let (_, a) = model::analyse_native(&fixture("samples/GRM-02_shaft.step")).unwrap();
    for (pinned, gap) in [(false, 10), (true, 500)] {
        let s: Spec = serde_json::from_value(json!({
            "sheet": {"size": "A4", "scale": "2:1"},
            "views": {"iso": {"place": {"relative_to": "front", "side": "below",
                "align": "start", "gap": gap, "pinned": pinned}}}
        }))
        .unwrap();
        let p = model::plan_recovered(&a, &s).unwrap();
        let iso = p.views.iter().find(|v| v.id == "v:iso").unwrap();
        assert!(iso.area[1] < 0.0, "{:?}", iso.area);
        let findings: Vec<_> = p
            .problems
            .iter()
            .filter(|p| p.code == "pinned_view_off_sheet")
            .collect();
        assert_eq!(findings.len(), 1, "{:?}", p.problems);
        let finding = findings[0];
        assert_eq!(finding.severity, "error");
        assert_eq!(finding.subjects, ["v:iso"]);
        assert_eq!(draftwright::wording::carries_id(&finding.message), None);
        assert_eq!(
            finding.fix,
            [draftwright::items::Op::remove("views/iso/place")]
        );
        let mut fixed = s.clone();
        fixed.views.get_mut("iso").unwrap().place = None;
        let fixed = model::plan_recovered(&a, &fixed).unwrap();
        assert!(
            !fixed
                .problems
                .iter()
                .any(|p| p.code == "pinned_view_off_sheet")
        );
    }
}
