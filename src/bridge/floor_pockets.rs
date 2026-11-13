//! Closed rectangular pockets quiddity 0.5.0 reports in place of slots and fillets.
//!
//! quiddity 0.5.0 (#228) recognises a closed rectangular recess with analytic floor rounds as a
//! `Pocket` (before, a `Slot` on its four walls and a `Fillet` on each straight floor round).
//! draftwright dimensions slots and fillets and reads no `pockets` records, so that recess left
//! the drawing (FTC-08's long slot lost its width, length and two locations, and `4× R0.762`).
//!
//! Such a pocket is read as it was: a `Slot` on the walls its floor rounds name (its width,
//! length, location and floor depth are the pocket's own), and a concave `Fillet` on each floor
//! round springing from those walls, placed at the middle of its face as quiddity places one. A
//! pocket without floor rounds, or open at the part's edge, is left alone and stays unread.
use haecceity::Part;
use haecceity::geom::Surface;
use quiddity::recognition::{FaceAccounting, RecognitionDocument, Record};
use serde_json::{Map, Value, json};

/// The slot keys a `Slot` record carries; a pocket's own (depth, floor rounds, …) are dropped.
const SLOT_KEYS: [&str; 12] = [
    "body_key",
    "corner_radius",
    "d_hi",
    "d_lo",
    "end_radius",
    "hi",
    "length",
    "long_axis",
    "lo",
    "w_center",
    "width",
    "width_axis",
];

/// Replaces each such pocket in *doc* by its slot and fillet records.
pub(crate) fn restore(part: &Part, doc: &mut RecognitionDocument) {
    let mut slots = doc.records.iter().filter(|r| r.family == "slots").count();
    let mut fillets = doc.records.iter().filter(|r| r.family == "fillets").count();
    let mut added = Vec::new();
    let mut renamed: Vec<(String, String)> = Vec::new();
    let mut handled: Vec<Vec<usize>> = Vec::new();
    for r in &mut doc.records {
        if r.family != "pockets" || r.record_type != "Pocket" {
            continue;
        }
        let p = &r.parameters;
        let Some(rounds) = p.get("floor_fillets").and_then(Value::as_array).cloned() else {
            continue;
        };
        if p.get("edge_anchored").and_then(Value::as_bool) == Some(true)
            || p.get("open_sign").is_some_and(|v| v.as_i64() == Some(0))
            || SLOT_KEYS
                .iter()
                .any(|k| *k != "end_radius" && *k != "corner_radius" && !p.contains_key(*k))
        {
            continue;
        }
        let face = |v: &Value| v.as_u64().map(|f| f as usize);
        let mut walls: Vec<usize> = rounds
            .iter()
            .filter_map(|x| face(&x["wall_face"]))
            .filter(|w| r.faces.contains(w))
            .collect();
        walls.sort_unstable();
        walls.dedup();
        // A slot is its four walls: floor rounds on its end walls alone name too few of them,
        // and the pocket stays unread rather than become a slot on faces that are not its walls.
        if walls.len() < 4 {
            continue;
        }
        let mut slot: Map<String, Value> = SLOT_KEYS
            .iter()
            .filter_map(|k| Some((k.to_string(), p.get(*k)?.clone())))
            .collect();
        slot.entry("end_radius").or_insert(Value::Null);
        slot.entry("corner_radius").or_insert(Value::Null);
        slot.insert("source".into(), Value::Null);
        let old = std::mem::replace(&mut r.id, format!("slots/{slots}"));
        slots += 1;
        renamed.push((old, r.id.clone()));
        r.family = "slots".into();
        r.record_type = "Slot".into();
        let all = std::mem::replace(&mut r.faces, walls.clone());
        handled.push(all.clone());
        r.parameters = slot;
        for x in &rounds {
            let (Some(f), Some(w), Some(radius)) = (
                face(&x["face"]),
                face(&x["wall_face"]),
                x["radius"].as_f64(),
            ) else {
                continue;
            };
            if !walls.contains(&w) || !all.contains(&f) {
                continue;
            }
            let Some(parameters) = fillet(part, f, radius) else {
                continue;
            };
            let id = format!("fillets/{fillets}");
            fillets += 1;
            added.push(Record::new(
                id,
                "fillets".into(),
                "Fillet".into(),
                vec![f],
                parameters,
            ));
        }
    }
    if let Some(groups) = &mut doc.face_accounting {
        for new in &added {
            for &f in &new.faces {
                if let Some(g) = groups.get_mut(f) {
                    *g = FaceAccounting::Feature {
                        record_ids: vec![new.id.clone()],
                    };
                }
            }
        }
        for g in groups.iter_mut() {
            if let FaceAccounting::Feature { record_ids } = g {
                for id in record_ids {
                    if let Some((_, new)) = renamed.iter().find(|(old, _)| old == id) {
                        *id = new.clone();
                    }
                }
            }
        }
    }
    // quiddity's refusal to read the same pocket as a section recess says nothing the slot's own
    // dimensions do not: a `SectionRecessRefusal` whose defining faces are all the pocket's goes.
    doc.records.retain(|r| {
        if r.record_type != "SectionRecessRefusal" {
            return true;
        }
        let defining: Vec<usize> = r
            .parameters
            .get("evidence")
            .and_then(|e| e.get("defining_faces"))
            .and_then(Value::as_array)
            .map(|a| {
                a.iter()
                    .filter_map(|f| f.as_u64().map(|f| f as usize))
                    .collect()
            })
            .unwrap_or_default();
        defining.is_empty()
            || !handled
                .iter()
                .any(|h| defining.iter().all(|f| h.contains(f)))
    });
    doc.records.extend(added);
}

/// A straight round's `Fillet` parameters: its axis letter, radius and the surface point at the
/// middle of its trimmed face, which is where quiddity anchors a fillet's leader.
fn fillet(part: &Part, face: usize, radius: f64) -> Option<Map<String, Value>> {
    let f = part.faces.get(face)?;
    let Surface::Cylinder { frame, .. } = &f.surface else {
        return None;
    };
    let k = (0..3).find(|&k| frame.z[k].abs() > 1.0 - 1e-9)?;
    let (u0, u1, v0, v1) = part.uv_bounds(face)?;
    let at = f.surface.value(0.5 * (u0 + u1), 0.5 * (v0 + v1));
    let axis = ["x", "y", "z"][k];
    let round = |v: f64| (v * 1e3).round() / 1e3 + 0.0;
    let Value::Object(m) = json!({
        "at": at.map(round),
        "axis": axis,
        "radius": radius,
        "side": "concave",
        "turned": false,
    }) else {
        return None;
    };
    Some(m)
}
