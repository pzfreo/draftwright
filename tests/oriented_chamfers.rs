#[path = "common/corpus.rs"]
mod corpus;
use draftwright::{
    api,
    items::State,
    lint,
    model::{self, Analysis, Plan, Spec},
};
use serde_json::json;
use std::sync::OnceLock;
fn ctc() -> &'static (draftwright::haecceity::Part, Analysis) {
    static PART: OnceLock<(draftwright::haecceity::Part, Analysis)> = OnceLock::new();
    PART.get_or_init(|| {
        model::analyse_native(
            &corpus::dir()
                .expect("CTC corpus")
                .join("nist/nist_ctc_01_asme1_rd.stp"),
        )
        .unwrap()
    })
}
fn parse(v: serde_json::Value) -> Spec {
    serde_json::from_value(v).unwrap()
}
fn plan(s: &Spec) -> Plan {
    model::plan_recovered(&ctc().1, s).unwrap()
}
#[test]
fn ctc01_consumes_bevel_faces_and_sizes_both_notches_without_duplicate_c50() {
    let a = &ctc().1;
    assert_eq!(
        a.features
            .iter()
            .filter(|f| f.kind == "oriented_chamfer")
            .count(),
        4
    );
    assert!(
        !a.unrecognised
            .iter()
            .any(|r| r.family == "oriented_chamfers")
    );
    let p = plan(&Spec::default());
    assert!(p.completeness.claimed_faces > 77, "{:?}", p.completeness);
    assert_eq!(p.sheet.size, "A3");
    assert_eq!(p.sheet.scale, "1:5");
    // No radius-centre table: the 4× R25 corners' leader is not obstructed, so they are called
    // out (alpha.12 tabulated them, when the 350 row pitch's extension lines crossed it).
    assert!(p.radius_table.is_none());
    assert!(
        p.dimensions
            .iter()
            .any(|d| d.label == "4× R25" && d.placed.is_some())
    );
    assert_eq!(
        a.features
            .iter()
            .filter(|f| f.kind == "section_recess")
            .count(),
        4
    );

    let angles: Vec<_> = p
        .dimensions
        .iter()
        .filter(|d| d.measurements.iter().any(|m| m.parameter == "notch.angle"))
        .collect();
    assert_eq!(angles.len(), 2);
    for d in angles {
        assert_eq!(d.view, "v:plan");
        assert!((d.value - 60.).abs() < 1e-6);
        assert!(d.placed.is_some());
        assert_eq!(d.region, Some(draftwright::dims::CandidateRegion::Interior));
    }
    let depths: Vec<_> = p
        .dimensions
        .iter()
        .filter(|d| {
            d.measurements
                .iter()
                .any(|m| m.feature.starts_with("notch:") && m.parameter == "notch_depth.length")
        })
        .collect();
    // Coincident equal depth spans may share a dimension, retaining both measurements.
    assert_eq!(
        depths
            .iter()
            .flat_map(|d| &d.measurements)
            .filter(|m| m.parameter == "notch_depth.length")
            .count(),
        2
    );
    assert!(
        depths
            .iter()
            .all(|d| (d.value - 50.).abs() < 1e-6 && d.placed.is_some())
    );
    assert_eq!(p.dimensions.iter().filter(|d| d.label == "C50").count(), 1);
    assert_eq!(
        p.dimensions.iter().filter(|d| d.label == "6× C5").count(),
        1
    );
    for id in [
        "d:f:slot:1/slot_width.length",
        "d:f:slot:2/slot_width.length",
    ] {
        let d = p.dimensions.iter().find(|d| d.id == id).unwrap();
        let ink = d.placed.as_ref().unwrap();
        let lo = ink.arrows[0][0][1].min(ink.arrows[1][0][1]);
        let hi = ink.arrows[0][0][1].max(ink.arrows[1][0][1]);
        assert!(
            ink.label_box[1] >= lo && ink.label_box[3] <= hi,
            "the new notch bands must leave the existing slot widths on their own spans: {id}"
        );
    }
    let mut seen = std::collections::BTreeSet::new();
    for d in &p.dimensions {
        for m in &d.measurements {
            assert!(seen.insert((&m.feature, &m.parameter)), "duplicate {m:?}");
        }
    }
    // Without the oriented chamfers, in the features and in quiddity's face accounting.
    let mut without = a.clone();
    let bevels: Vec<usize> = without
        .features
        .iter()
        .filter(|f| f.kind == "oriented_chamfer")
        .flat_map(|f| f.faces.clone())
        .collect();
    without.features.retain(|f| f.kind != "oriented_chamfer");
    if let Some(groups) = &mut without.face_accounting {
        for f in bevels {
            groups[f] = model::FaceGroup::Unexplained;
        }
    }
    assert!(
        p.completeness.claimed_faces
            >= model::plan(&without, &Spec::default())
                .unwrap()
                .completeness
                .claimed_faces
                + 4
    );
}
#[test]
fn notch_dimensions_accept_omit_tolerance_place_and_label_text() {
    let p = plan(&Spec::default());
    for parameter in [
        "notch.angle",
        "notch_depth.length",
        "location.length.x",
        "oriented_chamfer.length",
    ] {
        let original = p
            .dimensions
            .iter()
            .find(|d| {
                d.measurements.iter().any(|m| {
                    m.parameter == parameter
                        && (parameter != "location.length.x" || m.feature.starts_with("notch:"))
                })
            })
            .unwrap();
        let id = &original.id;
        let options = draftwright::dims::placement_options(&ctc().1, &p.views);
        assert!(
            options[id]["views"]
                .as_array()
                .unwrap()
                .iter()
                .any(|v| v["view"] == original.view.trim_start_matches("v:")),
            "{id}: {:?}",
            options[id]
        );
        let omitted = plan(&parse(json!({"dimensions":{id:{"omit":true}}})));
        assert!(!omitted.dimensions.iter().any(|d| &d.id == id));
        assert!(omitted.omitted.iter().any(|d| &d.id == id));
        let tolerance = plan(&parse(json!({"dimensions":{id:{"tolerance":"±0.5"}}})));
        assert!(
            tolerance
                .dimensions
                .iter()
                .find(|d| &d.id == id)
                .unwrap()
                .label
                .contains("±0.5")
        );
        let placed = plan(&parse(
            json!({"dimensions":{id:{"place":{"view":original.view.trim_start_matches("v:"),"lane":3,"pinned":true}}}}),
        ));
        let changed = placed.dimensions.iter().find(|d| &d.id == id).unwrap();
        assert!(changed.placed.is_some());
        let other_lane = plan(&parse(
            json!({"dimensions":{id:{"place":{"view":original.view.trim_start_matches("v:"),"lane":6,"pinned":true}}}}),
        ));
        assert_ne!(
            changed.placed,
            other_lane
                .dimensions
                .iter()
                .find(|d| &d.id == id)
                .unwrap()
                .placed,
            "two distinct requested lanes must produce distinct ink for {id}"
        );
        let labelled = plan(&parse(
            json!({"dimensions":{id:{"text":{"suffix":"INCLUDED"}}}}),
        ));
        assert_eq!(
            labelled
                .dimensions
                .iter()
                .find(|d| &d.id == id)
                .unwrap()
                .label,
            format!("{} INCLUDED", original.label)
        );
    }
}
#[test]
fn overall_tolerances_do_not_change_profile_requirement_labels() {
    let p = plan(&Spec::default());
    let q = plan(&parse(json!({"envelope_tolerance":"±1"})));
    for d in p.dimensions.iter().filter(|d| {
        d.measurements
            .iter()
            .any(|m| m.feature.starts_with("notch:"))
    }) {
        assert_eq!(
            q.dimensions.iter().find(|e| e.id == d.id).unwrap().label,
            d.label
        );
    }
}

#[test]
fn lint_remeasures_new_dimensions_and_rejects_wrong_nominals_and_tolerance_digits() {
    let (part, a) = ctc();
    let p = plan(&Spec::default());
    let drawing = model::render(
        part,
        a,
        &p,
        &draftwright::text::Font::new(api::MONO.to_vec()).unwrap(),
    )
    .unwrap();
    let mut marks = lint::marks(&drawing);
    assert!(
        !lint::check(a, &marks)
            .iter()
            .any(|i| i.code.starts_with("notch_requirement_")),
        "{:?}",
        lint::check(a, &marks)
    );
    for parameter in [
        "notch.angle",
        "notch_depth.length",
        "oriented_chamfer.length",
    ] {
        let i = marks
            .iter()
            .position(|m| m.claims.iter().any(|c| c.parameter == parameter))
            .unwrap();
        let old = marks[i].text.clone();
        marks[i].text = if parameter == "notch.angle" {
            "99° ±60"
        } else {
            "999 ±50"
        }
        .into();
        let issues = lint::check(a, &marks);
        assert!(
            issues
                .iter()
                .any(|issue| issue.code.ends_with("requirement_unverifiable")
                    && issue.annotation.as_deref() == Some(&marks[i].name)),
            "{parameter}: {issues:?}"
        );
        marks[i].text = old;
    }
}
#[test]
fn datum_scheme_takes_the_notch_station_once() {
    let p = plan(&Spec::default());
    let id = p
        .dimensions
        .iter()
        .flat_map(|d| &d.measurements)
        .find(|m| m.parameter == "notch.angle")
        .unwrap()
        .feature
        .clone();
    let station = p
        .dimensions
        .iter()
        .find(|d| {
            d.measurements
                .iter()
                .any(|m| m.feature == id && m.parameter == "location.length.x")
        })
        .unwrap()
        .id
        .clone();
    let s = parse(
        json!({"dimension_schemes":{"notch":{"scheme":"baseline","axis":"x","view":"plan","targets":[id],"from":"max"}}}),
    );
    let q = plan(&s);
    assert!(
        q.dimensions
            .iter()
            .any(|d| d.id.starts_with("d:scheme:notch/"))
    );
    assert!(
        q.omitted
            .iter()
            .any(|d| d.id == station && matches!(d.state, State::Covered { .. }))
    );
    let drawing = model::render(
        &ctc().0,
        &ctc().1,
        &q,
        &draftwright::text::Font::new(api::MONO.to_vec()).unwrap(),
    )
    .unwrap();
    let mut marks = lint::marks(&drawing);
    assert!(
        !lint::check(&ctc().1, &marks)
            .iter()
            .any(|i| i.code.starts_with("notch_requirement_"))
    );
    let link = marks
        .iter_mut()
        .find(|m| m.name.starts_with("d:scheme:notch/"))
        .unwrap();
    link.text = "999 ±290".into();
    let issues = lint::check(&ctc().1, &marks);
    assert!(issues.iter().any(|i| i.code == "dimension_scheme_invalid"));
    assert!(issues.iter().any(|i| i.code == "notch_requirement_missing"));
}

#[test]
fn authored_notch_angle_transfers_ownership_and_auto_mode_names_the_existing_owner() {
    use draftwright::model::dimension_geometry::Reference;
    let a = &ctc().1;
    let planes:Vec<_>=a.dimension_references().iter().filter(|(_,rs)| matches!(rs.as_slice(),[Reference::Plane{normal,bounds,..}] if normal[2].abs()<1e-8 && bounds[0][0]< -80. && bounds[1][0]>-140. && bounds[1][1]< -70.)).map(|(k,_)|k.clone()).collect();
    let refs = planes
        .iter()
        .find_map(|x| {
            planes.iter().find_map(|y| {
                let refs = vec![x.clone(), y.clone()];
                let m = a.measure_references(&refs, "angle", None).ok()?;
                let angle = m.angle?;
                ((m.value - 60.).abs() < 1e-5
                    && (angle.vertex[0] + 110.).abs() < 1e-5
                    && (angle.vertex[1] + 75.).abs() < 1e-5)
                    .then_some(refs)
            })
        })
        .expect("the two notch support planes");
    let s = parse(json!({"dimensions":{"d:authored:notch":{"kind":"angle","between":refs}}}));
    let q = model::plan(a, &s).unwrap();
    assert!(q.problems.iter().any(|p| p.code == "duplicate_measurement"
        && p.subjects.iter().any(|id| id.contains("/notch.angle."))));
    assert!(q.dimensions.iter().any(|d| d.id == "d:authored:notch"));
    assert!(q.dimensions.iter().any(|d| d.placed.is_some()));
    let mut s = s;
    s.dimension_source = model::DimensionSource::Authored;
    let q = plan(&s);
    let owner = q
        .dimensions
        .iter()
        .find(|d| d.id == "d:authored:notch")
        .unwrap();
    assert!(
        owner
            .measurements
            .iter()
            .any(|m| m.parameter == "notch.angle")
    );
    assert!(q.omitted.iter().any(|d| d.id.contains("/notch.angle.")
        && matches!(&d.state,State::Covered{by} if by=="d:authored:notch")));
}

#[test]
fn native_oriented_records_are_checked_and_non45_callouts_state_both_values() {
    let (part, a) = ctc();
    let r = quiddity::correspondence::recognise(part);
    let mut doc = quiddity::recognition::document(&r, part.faces.len());
    let i = doc
        .records
        .iter()
        .position(|r| r.family == "chamfers" && r.parameters["leg1_direction"].is_array())
        .unwrap();
    // quiddity 0.2.0 merges oriented chamfers into chamfers (#92), their run as the axis.
    for field in [
        "corner",
        "axis",
        "leg1_direction",
        "leg2_direction",
        "support_spans",
        "length",
        "angle",
    ] {
        let mut bad = doc.clone();
        bad.records[i].parameters.remove(field);
        let mut unchanged = a.clone();
        let before = serde_json::to_value(&unchanged).unwrap();
        assert!(
            model::recognise_document(part, &mut unchanged, &bad)
                .unwrap_err()
                .contains(field)
        );
        assert_eq!(serde_json::to_value(unchanged).unwrap(), before);
    }
    doc.records[i].parameters.insert("leg1".into(), json!(10.));
    doc.records[i]
        .parameters
        .insert("angle".into(), json!(26.57));
    let mut a = a.clone();
    model::recognise_document(part, &mut a, &doc).unwrap();
    let q = model::plan_recovered(&a, &Spec::default()).unwrap();
    let d = q
        .dimensions
        .iter()
        .find(|d| {
            d.measurements
                .iter()
                .any(|m| m.parameter == "oriented_chamfer.angle")
        })
        .unwrap();
    assert_eq!(d.label, "10 × 26.57°");
    assert!(d.placed.is_some());
    let drawing = model::render(
        part,
        &a,
        &q,
        &draftwright::text::Font::new(api::MONO.to_vec()).unwrap(),
    )
    .unwrap();
    let mut marks = lint::marks(&drawing);
    let m = marks.iter_mut().find(|m| m.name == d.id).unwrap();
    m.text = "999 × 99° ±10 ±26.57".into();
    assert!(
        lint::check(&a, &marks)
            .iter()
            .any(|i| i.code == "chamfer_requirement_unverifiable"
                && i.annotation.as_deref() == Some(&d.id))
    );
}

#[test]
fn bevel_run_midpoints_do_not_invent_locations_or_force_sheet_enlargement() {
    for (file, count, label) in [
        ("mfcadpp/1007.step", 1, "C0.1"),
        ("nist/nist_ctc_04_asme1_rd.stp", 4, "12× C5"),
    ] {
        let (_, a) = model::analyse_native(&corpus::dir().unwrap().join(file)).unwrap();
        assert_eq!(
            a.features
                .iter()
                .filter(|f| f.kind == "oriented_chamfer")
                .count(),
            count
        );
        let p = model::plan_recovered(&a, &Spec::default()).unwrap();
        assert_eq!(p.dimensions.iter().filter(|d| d.label == label).count(), 1);
        assert!(
            !p.dimensions
                .iter()
                .flat_map(|d| &d.measurements)
                .any(|m| m.feature.starts_with("f:oriented_chamfer:")
                    && m.parameter.starts_with("location."))
        );
        if count == 1 {
            assert_eq!(p.sheet.size, "A4");
            assert_eq!(p.sheet.scale, "1:1");
        }
    }
}

#[test]
fn bounded_notches_reserve_acute_arcs_and_leave_dense_general_profiles_alone() {
    for file in [
        "mfcadpp/10020.step",
        "mfcadpp/10046.step",
        "nist/nist_ftc_08_asme1_rc.stp",
    ] {
        let (_, a) = model::analyse_native(&corpus::dir().unwrap().join(file)).unwrap();
        let p = model::plan_recovered(&a, &Spec::default()).unwrap();
        if file.contains("10020") {
            assert_eq!(
                p.dimensions
                    .iter()
                    .filter(|d| d.id.contains("/notch.angle."))
                    .count(),
                1
            );
            assert!(
                !p.problems
                    .iter()
                    .any(|p| p.code == "annotation_outside_area")
            );
            // Native recesses add two complete pocket callouts, their four datum
            // locations and the hex-pocket depth. A4 retains every requirement, at 2:1 since
            // #222 left out the plan view, which no dimension needs.
            assert_eq!(p.sheet.size, "A4");
            assert_eq!(p.sheet.scale, "2:1");
            for (feature, parameters) in [
                (
                    "f:section_recess:1",
                    &[
                        "pocket_width.length",
                        "pocket_length.length",
                        "pocket_depth.length",
                        "location_pocket.z",
                        "location_pocket.y",
                    ][..],
                ),
                (
                    "f:section_recess:5",
                    &[
                        "pocket_width.length",
                        "pocket_length.length",
                        "pocket_depth.length",
                        "location_pocket.z",
                        "location_pocket.y",
                    ][..],
                ),
                (
                    "f:section_recess:2",
                    &["polygon_across_flats.length", "pocket_depth.length"][..],
                ),
            ] {
                for parameter in parameters {
                    assert_eq!(
                        p.dimensions
                            .iter()
                            .filter(|d| d.placed.is_some()
                                && d.measurements
                                    .iter()
                                    .any(|m| m.feature == feature && m.parameter == *parameter))
                            .count(),
                        1,
                        "one native recess owner for {feature}/{parameter}"
                    );
                }
            }
            let angle = p
                .dimensions
                .iter()
                .find(|d| d.id.contains("/notch.angle."))
                .unwrap();
            assert_eq!(
                angle.region, None,
                "the exterior acute sector is not an interior candidate"
            );
        } else {
            assert!(!p.dimensions.iter().any(|d| {
                d.measurements
                    .iter()
                    .any(|m| m.feature.starts_with("notch:"))
            }));
        }
    }
}

#[test]
fn source_pmi_owns_a_notch_angle_at_another_normal_section_without_duplicate_ink() {
    use std::io::Read as _;
    let mut bytes = Vec::new();
    flate2::read::GzDecoder::new(
        &include_bytes!("fixtures/pmi/step/nist_ctc_01_asme1_ap242-e1.stp.gz")[..],
    )
    .read_to_end(&mut bytes)
    .unwrap();
    let (part, a) =
        model::analyse_native_step(std::path::Path::new("notch-pmi.stp"), &bytes).unwrap();
    let p = model::plan(&a, &Spec::default()).unwrap();
    let owner = p
        .dimensions
        .iter()
        .find(|d| d.id == "d:pmi:dimension.angular:f15,17/angular")
        .unwrap();
    assert_eq!(owner.label, "60° ±0.5°");
    assert_eq!(
        p.dimensions.iter().filter(|d| d.kind == "angular").count(),
        2,
        "one automatic angle and the existing PMI angle specify the two physical notches"
    );
    assert_eq!(
        p.dimensions
            .iter()
            .filter(|d| d.measurements.iter().any(|m| m.parameter == "notch.angle"))
            .count(),
        2
    );
    assert!(
        owner
            .measurements
            .iter()
            .any(|m| m.parameter == "notch.angle")
    );
    assert!(p.omitted.iter().any(|d| d.id.contains("/notch.angle.")
        && matches!(&d.state,State::Covered{by} if by==&owner.id)));
    let drawing = model::render(
        &part,
        &a,
        &p,
        &draftwright::text::Font::new(api::MONO.to_vec()).unwrap(),
    )
    .unwrap();
    let marks = lint::marks(&drawing);
    assert!(
        !lint::check(&a, &marks)
            .iter()
            .any(|i| i.code.starts_with("notch_requirement_"))
    );
    let alias = p
        .omitted
        .iter()
        .find(|d| {
            d.id.contains("/notch.angle.")
                && matches!(&d.state, State::Covered { by } if by == &owner.id)
        })
        .unwrap();
    let hidden_alias = model::plan(
        &a,
        &parse(json!({"dimensions":{(alias.id.clone()):{"omit":true}}})),
    )
    .unwrap();
    let hidden_drawing = model::render(
        &part,
        &a,
        &hidden_alias,
        &draftwright::text::Font::new(api::MONO.to_vec()).unwrap(),
    )
    .unwrap();
    assert!(
        !lint::check(&a, &lint::marks(&hidden_drawing))
            .iter()
            .any(|i| i.code.starts_with("notch_requirement_")),
        "the still-drawn source angle retains its independently proved notch claim"
    );
    let mut bad = marks.clone();
    bad.iter_mut().find(|m| m.name == owner.id).unwrap().text = "999 ±60".into();
    assert!(
        lint::check(&a, &bad)
            .iter()
            .any(|i| i.code == "notch_requirement_unverifiable"
                && i.annotation.as_deref() == Some(owner.id.as_str()))
    );
    // A bare number (no `°`) proves an angle only through its typed source claim.
    let mut untyped = marks;
    let bare = untyped.iter_mut().find(|m| m.name == owner.id).unwrap();
    bare.text = "60 ±0.5".into();
    bare.claims.retain(|c| !c.feature.starts_with("pmi:"));
    assert!(
        lint::check(&a, &untyped)
            .iter()
            .any(|i| i.code == "notch_requirement_unverifiable"
                && i.annotation.as_deref() == Some(owner.id.as_str()))
    );
    let q = model::plan(
        &a,
        &parse(json!({"dimensions":{(owner.id.clone()):{"omit":true}}})),
    )
    .unwrap();
    assert_eq!(
        q.dimensions.iter().filter(|d| d.kind == "angular").count(),
        2
    );
    assert!(
        q.dimensions
            .iter()
            .filter(|d| d.kind == "angular")
            .all(|d| d.id.contains("/notch.angle."))
    );
}

#[test]
fn an_unproved_bevel_corner_cannot_replace_the_proven_notch_depth() {
    let (part, a) =
        model::analyse_native(&corpus::dir().unwrap().join("mfcadpp/10020.step")).unwrap();
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    let depth = p
        .dimensions
        .iter()
        .find(|d| {
            d.measurements
                .iter()
                .any(|m| m.parameter == "notch_depth.length")
        })
        .unwrap();
    // v0.4.2 withholds this virtual corner: the existing bevel claim does not prove
    // the depth's witness stations. Keep the independently measured depth explicit.
    assert!(a.features.iter().any(|f| {
        f.id == "f:chamfer:1"
            && f.parameters
                .get("corner")
                .is_some_and(serde_json::Value::is_null)
    }));
    assert!(depth.id.contains("/notch_depth.length."));
    assert_eq!(depth.label, "10.620");
    assert!(depth.placed.is_some());
    assert!(
        p.dimensions
            .iter()
            .filter(|d| d.id == "d:f:chamfer:1/chamfer.length")
            .all(|d| !d
                .measurements
                .iter()
                .any(|m| m.parameter == "notch_depth.length")),
        "an unproved virtual corner cannot claim the physical notch span"
    );
    assert!(
        !p.omitted
            .iter()
            .any(|d| d.id.contains("/notch_depth.length.")
                && matches!(&d.state, State::Covered { by } if by == "d:f:chamfer:1/chamfer.length"))
    );
    let drawing = model::render(
        &part,
        &a,
        &p,
        &draftwright::text::Font::new(api::MONO.to_vec()).unwrap(),
    )
    .unwrap();
    let mut marks = lint::marks(&drawing);
    assert!(
        !lint::check(&a, &marks)
            .iter()
            .any(|i| i.code.starts_with("notch_requirement_"))
    );
    marks.iter_mut().find(|m| m.name == depth.id).unwrap().text = "999 × 10.6° ±10.6".into();
    assert!(
        lint::check(&a, &marks)
            .iter()
            .any(|i| i.code == "notch_requirement_unverifiable"
                && i.annotation.as_deref() == Some(depth.id.as_str()))
    );
    let q = model::plan_recovered(
        &a,
        &parse(json!({"dimensions":{"d:f:chamfer:1/chamfer.length":{"omit":true}}})),
    )
    .unwrap();
    assert!(
        q.dimensions
            .iter()
            .any(|d| d.id.contains("/notch_depth.length.")),
        "hiding the bevel keeps the independently proved notch depth"
    );
}

#[test]
fn notch_dimensions_leave_the_boss_leader_clear_of_hidden_ink_and_witnesses() {
    use draftwright::output::{Curve, Item};
    type Point = [f64; 2];
    fn crosses([a, b]: [Point; 2], [c, d]: [Point; 2]) -> bool {
        let side = |p: Point, q: Point, r: Point| {
            (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        };
        side(a, b, c) * side(a, b, d) < -1e-12 && side(c, d, a) * side(c, d, b) < -1e-12
    }
    fn touches([a, b]: [Point; 2], bounds: [f64; 4]) -> bool {
        let (mut lo, mut hi) = (0.0_f64, 1.0_f64);
        for k in 0..2 {
            let delta = b[k] - a[k];
            if delta.abs() < 1e-12 {
                if a[k] < bounds[k] || a[k] > bounds[k + 2] {
                    return false;
                }
            } else {
                let x = (bounds[k] - a[k]) / delta;
                let y = (bounds[k + 2] - a[k]) / delta;
                lo = lo.max(x.min(y));
                hi = hi.min(x.max(y));
            }
        }
        lo <= hi
    }
    let (part, a) =
        model::analyse_native(&corpus::dir().unwrap().join("mfcadpp/10020.step")).unwrap();
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    let boss = p
        .dimensions
        .iter()
        .find(|d| d.id == "d:f:boss:1/boss.diameter")
        .unwrap();
    let route = &boss.placed.as_ref().unwrap().lines;
    let geometry = model::render_geometry(&part, &a, &p, &mut Default::default()).unwrap();
    for (_, view) in geometry.views {
        for item in view.hidden {
            let Item::Edge(curve) = item else {
                panic!("hidden edge")
            };
            let n = if matches!(curve, Curve::Line(..)) {
                1
            } else {
                2048
            };
            for i in 0..n {
                let edge = [
                    curve.at(i as f64 / n as f64),
                    curve.at((i + 1) as f64 / n as f64),
                ];
                assert!(
                    route.iter().all(|s| !crosses(*s, edge)),
                    "boss crosses hidden ink: {edge:?}"
                );
            }
        }
    }
    let ink = model::render_item(model::AnnotationItem::Dimension(boss));
    for d in &p.dimensions {
        let Some(placed) = &d.placed else { continue };
        if placed.arrows.len() != 2 || placed.lines.len() < 3 {
            continue;
        }
        for [a, b] in &placed.lines[..2] {
            let bounds = [
                a[0].min(b[0]) - 0.2,
                a[1].min(b[1]) - 0.2,
                a[0].max(b[0]) + 0.2,
                a[1].max(b[1]) + 0.2,
            ];
            assert!(
                ink.drawn[0].heads.iter().all(|s| !touches(*s, bounds)),
                "boss arrow touches {}",
                d.id
            );
        }
    }
}
