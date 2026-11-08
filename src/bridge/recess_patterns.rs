//! Unified recess pattern links shared by native and Python readers.
use super::{FINGERPRINT_VERSION, Feature, Fingerprint};
use haecceity::Part;
use serde_json::{Map, Value};

pub fn constituent_faces(
    record: &Map<String, Value>,
    face_count: usize,
) -> Result<Vec<usize>, String> {
    let faces = record
        .get("evidence")
        .and_then(|e| e.get("constituent_faces"))
        .and_then(Value::as_array)
        .ok_or("section recess has no constituent faces")?;
    let faces = faces
        .iter()
        .map(|f| {
            f.as_u64()
                .filter(|&f| f < face_count as u64)
                .map(|f| f as usize)
                .ok_or_else(|| "section recess names an invalid face".into())
        })
        .collect::<Result<Vec<_>, String>>()?;
    if faces.is_empty() || !faces.windows(2).all(|w| w[0] < w[1]) {
        return Err("section recess has no ascending constituent faces".into());
    }
    Ok(faces)
}

/// Resolve run-local occurrence indices to stable drawing feature IDs. Missing, ambiguous,
/// repeated and multiply-owned members fail closed on both paths.
pub fn append(
    part: &Part,
    frame: &[[f64; 3]; 4],
    features: &mut Vec<Feature>,
    patterns: &[Map<String, Value>],
) -> Result<(), String> {
    let mut added = Vec::new();
    let mut used = Vec::new();
    for p in patterns {
        let members = p
            .get("members")
            .and_then(Value::as_array)
            .filter(|m| m.len() >= 2)
            .ok_or("section recess pattern has no distinct members")?;
        let mut ids = Vec::new();
        let mut faces: Vec<usize> = Vec::new();
        for m in members {
            let index = m
                .as_u64()
                .ok_or("section recess pattern member is not an index")?;
            let matches: Vec<_> = features
                .iter()
                .filter(|f| {
                    f.kind == "section_recess"
                        && f.parameters.get("index").and_then(Value::as_u64) == Some(index)
                })
                .collect();
            if matches.len() != 1 || used.contains(&index) {
                return Err(format!(
                    "section recess pattern member {index} is missing, ambiguous or repeated"
                ));
            }
            let f = matches[0];
            if !matches!(
                crate::lint::section_recesses::grammar(f),
                Ok(crate::lint::section_recesses::Grammar::Pocket(_))
            ) {
                return Err(format!(
                    "section recess pattern member {index} is not a pocket"
                ));
            }
            used.push(index);
            ids.push(Value::String(f.id.clone()));
            faces.extend(&f.faces);
        }
        faces.sort_unstable();
        faces.dedup();
        let number = |k: &str| p.get(k).and_then(Value::as_f64).filter(|v| *v > 0.0);
        let vector = |k: &str| super::holes::point(p.get(k));
        let subtype = if p.contains_key("rows") {
            let rows = p
                .get("rows")
                .and_then(Value::as_u64)
                .filter(|v| *v >= 2)
                .ok_or("bad recess grid rows")?;
            let cols = p
                .get("cols")
                .and_then(Value::as_u64)
                .filter(|v| *v >= 2)
                .ok_or("bad recess grid columns")?;
            if rows.checked_mul(cols) != Some(members.len() as u64)
                || number("row_pitch").is_none()
                || number("col_pitch").is_none()
                || vector("center").is_none()
            {
                return Err("bad recess grid sizes".into());
            }
            let row = vector("row_direction").ok_or("bad recess grid row direction")?;
            let col = vector("col_direction").ok_or("bad recess grid column direction")?;
            if !unit(row)
                || !unit(col)
                || row.iter().zip(col).map(|(a, b)| a * b).sum::<f64>().abs() > 1e-6
            {
                return Err("bad recess grid directions".into());
            }
            "SectionRecessGrid"
        } else {
            if number("pitch").is_none() || !vector("direction").is_some_and(unit) {
                return Err("bad recess array pitch or direction".into());
            }
            "SectionRecessArray"
        };
        let mut parameters = p.clone();
        parameters.insert("members".into(), ids.into());
        added.push(Feature {
            legacy_id: None,
            id: String::new(),
            kind: "pocket_pattern".into(),
            fingerprint: Fingerprint {
                version: FINGERPRINT_VERSION.into(),
                family: "section_recess_patterns".into(),
                subtype: subtype.into(),
                sizes: super::sizes(p),
                axis: super::axis(p, frame)?,
                position: super::centre(part, &faces).map(super::round),
                pattern: None,
                neighbourhood: super::neighbourhood(part, &faces),
            },
            faces,
            parameters,
        });
    }
    added.sort_by(|a, b| a.faces.cmp(&b.faces));
    for (i, f) in added.iter_mut().enumerate() {
        f.id = format!("f:pocket_pattern:{}", i + 1);
    }
    features.extend(added);
    features.sort_by(|a, b| (&a.kind, &a.faces).cmp(&(&b.kind, &b.faces)));
    Ok(())
}

fn unit(v: [f64; 3]) -> bool {
    (v.iter().map(|x| x * x).sum::<f64>() - 1.0).abs() <= 1e-6
}
