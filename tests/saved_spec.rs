//! A saved Spec opened across kernel upgrades (#191): refusals name the entry they refuse,
//! `Session::open_spec` sets aside entries the part no longer has, and `api::spec_ops` turns one
//! Spec into another.
use draftwright::{
    api,
    items::Op,
    model,
    session::{Session, SetAside},
};
use serde_json::{Value, json};

fn plate() -> Session {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts/mounting_plate.step");
    let (part, analysis) = model::analyse_native(&path).unwrap();
    Session::new(part, analysis, model::Spec::default()).unwrap()
}

fn spec(value: Value) -> model::Spec {
    serde_json::from_value(value).unwrap()
}

fn set_aside(path: &str, entry: Value, reason_code: &str) -> SetAside {
    SetAside {
        path: path.into(),
        entry,
        reason_code: reason_code.into(),
    }
}

/// Each "there is no …" refusal names the escaped Spec entry as data; its message and choices are
/// unchanged, and other refusals carry no path.
#[test]
fn refusals_name_the_entry_the_part_does_not_have() {
    let mut s = plate();
    // f:hole:1 is on the plate (in f:hole_pattern:1), but its bore diameter is not drawn.
    assert!(s.analysis().features.iter().any(|f| f.id == "f:hole:1"));
    let cases = [
        (
            json!({"features":{"f:hole:99":{"suppress":true}}}),
            "features/f:hole:99",
        ),
        (
            json!({"features":{"f:hole:99":{"fit":"H7"}}}),
            "features/f:hole:99",
        ),
        (json!({"views":{"gone":{"omit":true}}}), "views/gone"),
    ];
    for (value, path) in cases {
        let e = s.set_spec(&spec(value.clone())).unwrap_err();
        assert_eq!(e.code, "invalid", "{value}");
        assert_eq!(e.path.as_deref(), Some(path), "{value}");
        assert_eq!(serde_json::to_value(&e).unwrap()["path"], path);
    }
    for value in [
        json!({"dimensions":{"d:f:hole:1/bore.diameter":{"omit":true}}}),
        json!({"dimensions":{"d:f:hole:99/bore.diameter":{"omit":true}}}),
        json!({"annotations":{"pmi:gone":{"omit":true}}}),
        json!({"precision":{"d:f:hole:99/bore.diameter":2}}),
    ] {
        let requested = spec(value);
        s.set_spec(&requested).unwrap();
        assert!(
            s.plan()
                .problems
                .iter()
                .any(|p| p.code.ends_with("edit_unapplied"))
        );
        assert!(s.plan().dimensions.iter().any(|d| d.placed.is_some()));
    }
    let e = s
        .set_spec(&spec(
            json!({"dimensions":{"d:f:hole:4/bore.diameter":{"place":{"view":"iso"}}}}),
        ))
        .unwrap_err();
    assert_eq!(e.path, None);
    assert!(serde_json::to_value(&e).unwrap().get("path").is_none());
}

/// What the part still has is applied; each entry it no longer has is set aside with the entry
/// and why; the older `preferred_printer` is read as `preferred_max_size`.
#[test]
fn open_spec_sets_aside_what_the_part_no_longer_has() {
    let mut s = plate();
    let saved = json!({
        "schema": "draftwright.spec/2",
        "sheet": {"preferred_printer": "A4"},
        "dimensions": {
            "d:f:hole:4/bore.diameter": {"omit": true},
            "d:f:hole:1/bore.diameter": {"omit": true},
            "d:f:hole:99/bore.diameter": {"place": {"side": "above"}},
        },
        "features": {"f:hole:99": {"suppress": true}},
        "annotations": {"pmi:gone": {"omit": true}},
        "views": {"gone": {"omit": true}, "iso": {"omit": true},
                  "detail_a": {"detail": {"around": "f:hole:99", "scale_factor": 4}}},
    });
    let opened = s.open_spec(&saved.to_string()).unwrap();
    let mut got = opened.set_aside.clone();
    got.sort_by(|a, b| a.path.cmp(&b.path));
    assert_eq!(
        got,
        [
            set_aside(
                "features/f:hole:99",
                json!({"suppress": true}),
                "no_feature"
            ),
            set_aside(
                "views/detail_a",
                // Read in its current form: the scale factor is a number.
                json!({"detail": {"around": "f:hole:99", "scale_factor": 4.0}}),
                "no_feature"
            ),
            set_aside("views/gone", json!({"omit": true}), "no_view"),
        ]
    );
    assert_eq!(opened.spec, serde_json::to_value(s.spec()).unwrap());
    assert_eq!(opened.spec["sheet"]["preferred_max_size"], "A4");
    assert!(opened.spec["sheet"].get("preferred_printer").is_none());
    assert_eq!(opened.spec["dimensions"], saved["dimensions"]);
    assert_eq!(opened.spec["views"], json!({"iso": {"omit": true}}));
    assert_eq!(opened.spec["annotations"]["pmi:gone"], json!({"omit":true}));
    for code in ["dimension_edit_unapplied", "annotation_edit_unapplied"] {
        assert!(s.plan().problems.iter().any(|p| p.code == code));
    }
    assert!(opened.spec.get("features").is_none_or(|f| f == &json!({})));
    // Applied: the bore is omitted and the iso view is not drawn.
    assert!(
        s.plan()
            .dimensions
            .iter()
            .all(|d| d.id != "d:f:hole:4/bore.diameter")
    );
    assert!(s.plan().views.iter().all(|v| v.id != "v:iso"));
    // A Spec the part has whole is opened as set_spec opens it, with nothing set aside.
    let mut t = plate();
    let whole = t.open_spec(&opened.spec.to_string()).unwrap();
    assert!(whole.set_aside.is_empty());
    assert_eq!(t.spec(), s.spec());
}

/// A refusal that is not about an entry the part lacks still refuses the whole Spec, and the
/// session is unchanged.
#[test]
fn open_spec_still_refuses_other_refusals() {
    let mut s = plate();
    let before = serde_json::to_value(s.spec()).unwrap();
    for saved in [
        json!({"features": {"f:hole:99": {"suppress": true}},
               "dimensions": {"d:f:hole:4/bore.diameter": {"place": {"view": "iso"}}}}),
        json!({"dimensions": {"d:f:hole:99/bore.diameter": {"omit": true}},
               "sheet": {"preferred_max_sheets": 0}}),
    ] {
        let e = s.open_spec(&saved.to_string()).unwrap_err();
        assert_eq!(e.code, "invalid", "{saved}");
        assert_eq!(e.path, None, "{saved}");
        assert_eq!(serde_json::to_value(s.spec()).unwrap(), before);
    }
    assert_eq!(s.open_spec("{").unwrap_err().code, "invalid");
}

/// The ops: sorted keys, `schema` skipped at the root, `~` and `/` escaped, arrays and scalars
/// replaced whole, a new empty object set, a key missing or null in *after* removed.
#[test]
fn spec_ops_turn_one_spec_into_another() {
    let before = json!({"schema": "a", "x": {"b": 1, "c": [1, 2], "k": {"schema": 1}}, "d": 1,
                        "n": null});
    let after = json!({"schema": "b", "x": {"c": [1], "e/f~": {"g": 2}, "k": {"schema": 2}},
                       "h": null, "z": {}, "n": {"m": true}, "d2": 3});
    assert_eq!(
        api::spec_ops(&before, &after),
        [
            Op::remove("d"),
            Op::set("d2", json!(3)),
            Op::set("n/m", json!(true)),
            Op::remove("x/b"),
            Op::set("x/c", json!([1])),
            Op::set("x/e~1f~0/g", json!(2)),
            Op::set("x/k/schema", json!(2)),
            // A new empty object is set whole: it has no keys to set one by one.
            Op::set("z", json!({})),
        ]
    );
    // An object replacing a scalar is set whole; an emptied object loses its keys.
    assert_eq!(
        api::spec_ops(
            &json!({"a": 1, "b": {"c": 1}}),
            &json!({"a": {"x": 1}, "b": {}})
        ),
        [Op::set("a", json!({"x": 1})), Op::remove("b/c")]
    );
    assert!(api::spec_ops(&after, &after).is_empty());
    assert_eq!(api::spec_ops(&json!(1), &json!(2)), [Op::set("", json!(2))]);

    // Round trip through the Spec editor: the ops turn before into after, and back.
    let s = plate();
    let before = serde_json::to_value(s.spec()).unwrap();
    let mut after = before.clone();
    after["dimensions"] = json!({
        "d:f:hole:4/bore.diameter": {"omit": true},
        "d:envelope/width.length": {"place": {"view": "plan"}},
    });
    after["sheet"]["size"] = json!("A3");
    after["views"] = json!({"iso": {"omit": true}});
    let ops = api::spec_ops(&before, &after);
    assert!(
        ops.iter()
            .any(|op| op.path == "dimensions/d:f:hole:4~1bore.diameter/omit")
    );
    let edited = model::edit(s.spec(), &serde_json::to_value(&ops).unwrap()).unwrap();
    assert_eq!(serde_json::to_value(&edited).unwrap(), after);
    let back = api::spec_ops(&after, &before);
    let restored = model::edit(&edited, &serde_json::to_value(&back).unwrap()).unwrap();
    assert_eq!(serde_json::to_value(&restored).unwrap(), before);
}

fn renumbered(old: &str, new: &str) -> Session {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts/mounting_plate.step");
    let (part, mut analysis) = model::analyse_native(&path).unwrap();
    for f in &mut analysis.features {
        if f.id == old {
            f.id = new.into();
        }
    }
    for p in analysis
        .recognition
        .iter_mut()
        .flat_map(|r| &mut r.patterns)
    {
        for m in &mut p.members {
            if m == old {
                *m = new.into();
            }
        }
    }
    Session::new(part, analysis, model::Spec::default()).unwrap()
}

fn sorted(mut v: Vec<SetAside>) -> Vec<SetAside> {
    v.sort_by(|a, b| a.path.cmp(&b.path));
    v
}

/// A saved anchor whose faces the part no longer has is a stale entry, set aside with the rest
/// applied; a malformed anchor is the Spec's error and still refuses the whole Spec.
#[test]
fn a_stale_anchor_is_set_aside_and_a_malformed_one_refused() {
    let mut s = plate();
    let gone = format!("face:sha256:{}", "0".repeat(64));
    let saved = json!({
        "features": {"f:hole:4": {"suppress": true, "anchor": {"faces": [gone]}}},
        "views": {"iso": {"omit": true}},
    });
    let e = s.set_spec(&spec(saved.clone())).unwrap_err();
    assert_eq!(e.path.as_deref(), Some("features/f:hole:4"));
    let opened = s.open_spec(&saved.to_string()).unwrap();
    assert_eq!(
        opened.set_aside,
        [set_aside(
            "features/f:hole:4",
            saved["features"]["f:hole:4"].clone(),
            "no_feature"
        )]
    );
    assert_eq!(opened.spec["views"], json!({"iso": {"omit": true}}));
    // The hole is not suppressed: its bore is still drawn.
    assert!(
        s.plan()
            .dimensions
            .iter()
            .any(|d| d.id == "d:f:hole:4/bore.diameter")
    );

    let mut t = plate();
    let malformed =
        json!({"features": {"f:hole:4": {"suppress": true, "anchor": {"faces": ["face:1"]}}}});
    let e = t.open_spec(&malformed.to_string()).unwrap_err();
    assert_eq!(e.path, None);
}

/// A face entry naming a face the part does not have is set aside as `no_face`; a face key that
/// is no face name at all is the Spec's error.
#[test]
fn a_stale_face_entry_is_set_aside() {
    let mut s = plate();
    let saved = json!({
        "faces": {"face:99999": {"datum": "B"}, "bottom": {"datum": "A"}},
        "features": {"f:hole:4": {"controls": [
            {"characteristic": "position", "tolerance": 0.1, "datums": ["A"]}]}},
    });
    let e = s.set_spec(&spec(saved.clone())).unwrap_err();
    assert_eq!(e.path.as_deref(), Some("faces/face:99999"));
    let opened = s.open_spec(&saved.to_string()).unwrap();
    assert_eq!(
        opened.set_aside,
        [set_aside(
            "faces/face:99999",
            json!({"datum": "B"}),
            "no_face"
        )]
    );
    assert_eq!(opened.spec["faces"], json!({"bottom": {"datum": "A"}}));
    assert!(
        s.plan()
            .gdt
            .iter()
            .any(|g| g.source == "spec:faces/bottom/datum")
    );

    let mut t = plate();
    let e = t
        .open_spec(&json!({"faces": {"topp": {"datum": "A"}}}).to_string())
        .unwrap_err();
    assert_eq!(e.path, None);
}

/// A datum set aside with its feature leaves a control citing it applied, with the
/// `datum_undeclared` warning a control citing an undeclared datum always has: the control is
/// not refused, so it is kept rather than lost.
#[test]
fn a_control_citing_a_set_aside_datum_is_kept_with_its_warning() {
    let mut s = plate();
    let control = json!({"controls": [
        {"characteristic": "position", "tolerance": 0.1, "datums": ["A"]}]});
    let saved = json!({
        "features": {"f:hole:99": {"datum": "A"}, "f:hole:4": control},
        "views": {"iso": {"omit": true}},
    });
    let opened = s.open_spec(&saved.to_string()).unwrap();
    assert_eq!(
        opened.set_aside,
        [set_aside(
            "features/f:hole:99",
            json!({"datum": "A"}),
            "no_feature"
        )]
    );
    assert_eq!(opened.spec["features"]["f:hole:4"], control);
    assert_eq!(opened.spec["views"], json!({"iso": {"omit": true}}));
    assert!(
        s.plan()
            .gdt
            .iter()
            .any(|g| g.source == "spec:features/f:hole:4/controls/0")
    );
    assert!(
        s.plan()
            .problems
            .iter()
            .any(|p| p.code == "datum_undeclared")
    );
}

/// A member of an authored group that the part no longer has is taken out of the group alone
/// while the rest is still a group; when the rest is no longer one, the whole group is set
/// aside, as it was saved.
#[test]
fn a_gone_group_member_is_set_aside_alone_while_the_group_holds() {
    // f:hole:3 (x = 30, y = -15) is renumbered f:hole:103.
    let mut s = renumbered("f:hole:3", "f:hole:103");
    let row = json!({"pattern": {"arrangement": "linear",
                                 "members": ["f:hole:1", "f:hole:3", "f:hole:2"]}});
    let saved = json!({"features": {"f:hole_pattern:row": row}});
    let e = s.set_spec(&spec(saved.clone())).unwrap_err();
    assert_eq!(
        e.path.as_deref(),
        Some("features/f:hole_pattern:row/pattern/members/1")
    );
    let opened = s.open_spec(&saved.to_string()).unwrap();
    assert_eq!(
        opened.set_aside,
        [set_aside(
            "features/f:hole_pattern:row/pattern/members/1",
            json!("f:hole:3"),
            "no_feature"
        )]
    );
    assert_eq!(
        opened.spec["features"]["f:hole_pattern:row"]["pattern"]["members"],
        json!(["f:hole:1", "f:hole:2"])
    );
    assert!(
        s.plan()
            .dimensions
            .iter()
            .any(|d| d.id.starts_with("d:f:hole_pattern:row/"))
    );

    // Four members on a 2 × 2 grid: three are no grid, so the whole group is set aside.
    let mut t = renumbered("f:hole:6", "f:hole:106");
    let grid = json!({"pattern": {"arrangement": "grid",
        "members": ["f:hole:1", "f:hole:2", "f:hole:5", "f:hole:6"]}});
    let saved = json!({"features": {"f:hole_pattern:corner": grid.clone()},
                       "views": {"iso": {"omit": true}}});
    let opened = t.open_spec(&saved.to_string()).unwrap();
    assert_eq!(
        opened.set_aside,
        [set_aside(
            "features/f:hole_pattern:corner",
            grid,
            "no_feature"
        )]
    );
    assert_eq!(opened.spec["views"], json!({"iso": {"omit": true}}));
    assert!(
        opened.spec["features"]
            .get("f:hole_pattern:corner")
            .is_none()
    );
}

/// Stale dimension edits warn independently of entries that cannot be read: a detail
/// view around a gone face is `no_face`, and one from a source view the part does not have is
/// `no_view`, with the detail's path.
#[test]
fn reason_codes_do_not_depend_on_the_order_of_refusals() {
    let dimension = json!({"d:f:hole:99/bore.diameter": {"omit": true}});
    for saved in [
        json!({"dimensions": dimension}),
        json!({"dimensions": dimension, "features": {"f:hole:99": {"fit": "H7"}}}),
        json!({"dimensions": dimension, "features": {"f:hole:99": {"suppress": true}}}),
    ] {
        let mut s = plate();
        let opened = s.open_spec(&saved.to_string()).unwrap();
        let reasons: Vec<_> = sorted(opened.set_aside)
            .into_iter()
            .map(|a| (a.path, a.reason_code))
            .collect();
        let expected = if saved.get("features").is_some() {
            vec![("features/f:hole:99".to_string(), "no_feature".to_string())]
        } else {
            vec![]
        };
        assert_eq!(reasons, expected, "{saved}");
        assert_eq!(opened.spec["dimensions"], dimension);
        assert!(
            s.plan()
                .problems
                .iter()
                .any(|p| p.code == "dimension_edit_unapplied")
        );
        assert!(s.plan().dimensions.iter().any(|d| d.placed.is_some()));
    }

    let mut s = plate();
    let face = json!({"detail": {"around": "face:99999", "scale_factor": 3.0}});
    let e = s
        .set_spec(&spec(json!({"views": {"detail_a": face}})))
        .unwrap_err();
    assert_eq!(e.path.as_deref(), Some("views/detail_a"));
    let opened = s
        .open_spec(&json!({"views": {"detail_a": face}}).to_string())
        .unwrap();
    assert_eq!(
        opened.set_aside,
        [set_aside("views/detail_a", face, "no_face")]
    );

    // The plate has no section, so no detail can be taken from one.
    let mut s = plate();
    assert!(s.plan().views.iter().all(|v| v.id != "v:section_aa"));
    let from = json!({"detail": {"around": "f:hole:4", "from": "section_aa", "scale_factor": 3.0}});
    let e = s
        .set_spec(&spec(json!({"views": {"detail_b": from}})))
        .unwrap_err();
    assert_eq!(e.path.as_deref(), Some("views/detail_b"), "{}", e.message);
    let opened = s
        .open_spec(&json!({"views": {"detail_b": from}}).to_string())
        .unwrap();
    assert_eq!(
        opened.set_aside,
        [set_aside("views/detail_b", from, "no_view")]
    );
}

/// Every stale dimension edit is retained and individually reported without applying it.
#[test]
fn many_stale_dimension_edits_are_retained_with_individual_warnings() {
    let mut s = plate();
    let mut dimensions = serde_json::Map::new();
    for i in 0..12 {
        dimensions.insert(
            format!("d:f:hole:{}/bore.diameter", 900 + i),
            json!({"omit": true}),
        );
        dimensions.insert(format!("d:envelope/bogus{i}.length"), json!({"omit": true}));
    }
    dimensions.insert("d:f:hole:4/bore.diameter".into(), json!({"omit": true}));
    let saved = json!({"dimensions": dimensions});
    let plans = s.stats().plans;
    let opened = s.open_spec(&saved.to_string()).unwrap();
    assert!(opened.set_aside.is_empty());
    assert_eq!(
        s.plan()
            .problems
            .iter()
            .filter(|p| p.code == "dimension_edit_unapplied")
            .count(),
        24
    );
    assert_eq!(opened.spec["dimensions"], saved["dimensions"]);
    assert!(
        s.plan()
            .dimensions
            .iter()
            .all(|d| d.id != "d:f:hole:4/bore.diameter")
    );
    assert!(s.plan().dimensions.iter().any(|d| d.placed.is_some()));
    // One committed edit opened it.
    assert_eq!(s.stats().plans, plans + 1);
}
