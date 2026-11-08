//! Exact, whole-Spec identity migration. Saved keys without current evidence remain intact.
use crate::model::{Analysis, Invalid, Plan, SPEC_SCHEMA, SPEC_SCHEMAS, Spec};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;

/// spec/1 and spec/2 are accepted through this release, then refused. Removing support
/// requires changing this single cutoff; the approved conversion route remains explicit.
pub const LAST_LEGACY_SPEC_READER: &str = "0.3.9";

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Migrated {
    /// RFC 6901 path in the saved document (without a leading slash).
    pub path: String,
    pub from: String,
    pub to: String,
}

pub(crate) struct Migration {
    pub spec: Spec,
    pub migrated: Vec<Migrated>,
    pub ids: BTreeMap<String, String>,
    unmapped: Vec<(String, String)>,
}
impl Migration {
    pub(crate) fn report(&self, plan: &mut Plan) {
        plan.migrated = self.migrated.clone();
        plan.ids = self.ids.clone();
        complete_ids(plan);
        for (path, id) in &self.unmapped {
            let problem = format!("p:id_not_migrated:{path}:{id}");
            if !plan.problems.iter().any(|p| p.id == problem) {
                plan.problems.push(crate::model::Problem {
                    id: problem, code: "id_not_migrated".into(), severity: "info".into(),
                    message: "A saved identity has no exact mapping in the current geometry; its key is retained.".into(),
                    subjects: vec![path.clone()], refs: vec![id.clone()], ..Default::default()
                });
            }
        }
    }
}

/// Assign final names after all recognition merges; rank assignment remains unchanged above
/// this boundary and is retained only for exact saved-Spec migration.
fn complete_ids(plan: &mut Plan) {
    let reverse = plan
        .ids
        .iter()
        .map(|(o, n)| (n.clone(), o.clone()))
        .collect();
    for id in plan
        .dimensions
        .iter()
        .map(|d| &d.id)
        .chain(plan.omitted.iter().map(|d| &d.id))
        .chain(plan.problems.iter().map(|p| &p.id))
    {
        let old = crate::revision_rename::rename_ids(id, &reverse);
        if old != *id {
            plan.ids.insert(old, id.clone());
        }
    }
}

pub(crate) fn feature_names(
    features: &[crate::bridge::Feature],
    patterns: &[crate::bridge::HolePattern],
) -> Result<Vec<String>, String> {
    let records: Vec<_> = features
        .iter()
        .map(|f| (f.kind.as_str(), f.faces.as_slice()))
        .chain(
            patterns
                .iter()
                .map(|p| ("hole_pattern", p.faces.as_slice())),
        )
        .collect();
    type Anchors<'a> = BTreeMap<(&'a str, usize), Vec<(usize, &'a [usize])>>;
    let mut groups: Anchors<'_> = BTreeMap::new();
    for (i, (kind, faces)) in records.iter().enumerate() {
        let face = faces
            .iter()
            .min()
            .ok_or_else(|| format!("{kind} has no faces to anchor its ID"))?;
        groups.entry((kind, *face)).or_default().push((i, faces));
    }
    let mut names = vec![String::new(); records.len()];
    for ((kind, face), mut group) in groups {
        group.sort_by(|a, b| a.1.cmp(b.1));
        let shared = group.len() > 1;
        for (n, (i, _)) in group.into_iter().enumerate() {
            names[i] = format!(
                "f:{kind}:f{face}{}",
                if shared {
                    format!("_{n}")
                } else {
                    String::new()
                }
            );
        }
    }
    Ok(names)
}

pub(crate) fn anchor(
    features: &mut [crate::bridge::Feature],
    patterns: &mut [crate::bridge::HolePattern],
) -> Result<(), String> {
    let names = feature_names(features, patterns)?;
    let map: BTreeMap<_, _> = features
        .iter()
        .map(|f| &f.id)
        .chain(patterns.iter().map(|p| &p.id))
        .cloned()
        .zip(names.iter().cloned())
        .collect();
    for (i, f) in features.iter_mut().enumerate() {
        f.legacy_id = Some(std::mem::replace(&mut f.id, names[i].clone()));
        f.parameters = serde_json::from_value(crate::revision_rename::rename_spec(
            &Value::Object(f.parameters.clone()),
            &map,
        )?)
        .expect("parameter object");
    }
    for (i, p) in patterns.iter_mut().enumerate() {
        p.legacy_id = Some(std::mem::replace(
            &mut p.id,
            names[features.len() + i].clone(),
        ));
        for member in &mut p.members {
            *member = crate::revision_rename::rename_ids(member, &map);
        }
        p.parameters = serde_json::from_value(crate::revision_rename::rename_spec(
            &Value::Object(p.parameters.clone()),
            &map,
        )?)
        .expect("parameter object");
    }
    Ok(())
}

pub fn ids(a: &Analysis) -> BTreeMap<String, String> {
    let mut ids: BTreeMap<_, _> = a
        .features
        .iter()
        .filter_map(|f| Some((f.legacy_id.clone()?, f.id.clone())))
        .collect();
    if let Some(r) = &a.recognition {
        ids.extend(
            r.patterns
                .iter()
                .filter_map(|p| Some((p.legacy_id.clone()?, p.id.clone()))),
        );
    }
    ids.extend(
        crate::dims::levels::legacy_ids(a)
            .iter()
            .map(|(o, n)| (o.clone(), n.clone())),
    );
    ids.retain(|old, new| old != new);
    ids
}

fn version(s: &str) -> Vec<u64> {
    s.split('.').map(|n| n.parse().unwrap_or(0)).collect()
}
fn refusal(schema: &str, current: &str) -> Option<String> {
    (matches!(schema, "draftwright.spec/1" | "draftwright.spec/2")
        && version(current) > version(LAST_LEGACY_SPEC_READER)).then(|| format!(
        "This Spec is {schema}, which draftwright {current} no longer reads: it reads only draftwright.spec/3, whose feature and step IDs are named by faces rather than by rank or measured value. Convert it once with draftwright {LAST_LEGACY_SPEC_READER}, the last release that reads spec/2: run `draftwright plan PART.step --spec FILE` and keep the `spec` it prints. Then open the converted Spec here."
    ))
}
fn invalid(message: String) -> Invalid {
    Invalid {
        message,
        path: None,
        also: Vec::new(),
        choices: Vec::new(),
    }
}
fn escape(s: &str) -> String {
    s.replace('~', "~0").replace('/', "~1")
}

/// Report changes to keys and string values, including references nested in arrays.
fn changes(v: &Value, path: &str, map: &BTreeMap<String, String>, out: &mut Vec<Migrated>) {
    let mut changed = |s: &str, path: String| {
        let to = crate::revision_rename::rename_ids(s, map);
        if s != to {
            out.push(Migrated {
                path,
                from: s.into(),
                to,
            });
        }
    };
    match v {
        Value::String(s) => changed(s, path.into()),
        Value::Object(o) => {
            for (k, x) in o {
                let p = if path.is_empty() {
                    escape(k)
                } else {
                    format!("{path}/{}", escape(k))
                };
                let to = crate::revision_rename::rename_ids(k, map);
                if *k != to {
                    out.push(Migrated {
                        path: p.clone(),
                        from: k.clone(),
                        to,
                    });
                }
                changes(x, &p, map, out);
            }
        }
        Value::Array(a) => {
            for (i, x) in a.iter().enumerate() {
                changes(x, &format!("{path}/{i}"), map, out);
            }
        }
        _ => {}
    }
}

fn unmapped(v: &Value, a: &Analysis) -> Vec<(String, String)> {
    fn visit(v: &Value, path: &str, a: &Analysis, out: &mut Vec<(String, String)>) {
        let scan = |s: &str, path: &str, out: &mut Vec<(String, String)>| {
            let mut rest = s;
            while let Some((start, end)) = crate::revision_rename::next_id(rest) {
                let id = &rest[start..end];
                if id.starts_with("f:")
                    && id
                        .rsplit(':')
                        .next()
                        .is_some_and(|n| n.bytes().all(|b| b.is_ascii_digit()))
                    && !a.features.iter().any(|f| f.id == id)
                    && !a
                        .recognition
                        .iter()
                        .flat_map(|r| &r.patterns)
                        .any(|p| p.id == id)
                {
                    out.push((path.into(), id.into()));
                }
                rest = &rest[end..];
            }
            for prefix in [
                "step_height.length.h",
                "step_position.length.x",
                "step_position.length.y",
            ] {
                if let Some(i) = s.find(prefix) {
                    let end = s[i..]
                        .find(|c: char| !(c.is_ascii_alphanumeric() || c == '_' || c == '.'))
                        .map_or(s.len(), |n| i + n);
                    out.push((path.into(), s[i..end].into()));
                }
            }
        };
        match v {
            Value::String(s) => scan(s, path, out),
            Value::Object(o) => {
                for (k, x) in o {
                    let p = if path.is_empty() {
                        escape(k)
                    } else {
                        format!("{path}/{}", escape(k))
                    };
                    scan(k, &p, out);
                    visit(x, &p, a, out);
                }
            }
            Value::Array(list) => {
                for (i, x) in list.iter().enumerate() {
                    visit(x, &format!("{path}/{i}"), a, out);
                }
            }
            _ => {}
        }
    }
    let mut out = Vec::new();
    visit(v, "", a, &mut out);
    out.sort();
    out.dedup();
    out
}

pub(crate) fn migrate(spec: &Spec, a: &Analysis) -> Result<Migration, Invalid> {
    if let Some(message) = refusal(&spec.schema, env!("CARGO_PKG_VERSION")) {
        return Err(invalid(message));
    }
    if !SPEC_SCHEMAS.contains(&spec.schema.as_str()) {
        let mut error = invalid(format!(
            "The spec is {:?}, which this draftwright does not read.",
            spec.schema
        ));
        error.choices = SPEC_SCHEMAS.iter().map(|s| (*s).into()).collect();
        return Err(error);
    }
    let ids = ids(a);
    let mut out = crate::model::migrate_v1(spec, a)?;
    let mut migrated = Vec::new();
    if spec.schema != SPEC_SCHEMA {
        let mut map = ids.clone();
        if spec.schema == "draftwright.spec/1" {
            // This is the existing view/PMI migration, composed with the identity map.
            for old in spec.dimensions.keys() {
                let mut one = spec.clone();
                one.dimensions.retain(|k, _| k == old);
                let v1 = crate::model::migrate_v1(&one, a)?;
                if let Some(new) = v1.dimensions.keys().next() {
                    map.insert(old.clone(), crate::revision_rename::rename_ids(new, &ids));
                }
            }
        }
        let original = serde_json::to_value(spec).expect("Spec serializes");
        changes(&original, "", &map, &mut migrated);
        let value = crate::revision_rename::rename_spec(
            &serde_json::to_value(&out).expect("Spec serializes"),
            &ids,
        )
        .map_err(invalid)?;
        out = serde_json::from_value(value).expect("renaming strings preserves Spec structure");
    }
    out.schema = SPEC_SCHEMA.into();
    let unmapped = unmapped(&serde_json::to_value(&out).expect("Spec serializes"), a);
    Ok(Migration {
        unmapped,
        spec: out,
        migrated,
        ids,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn anchors_and_collision_suffixes_are_local_and_order_independent() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/parts/mounting_plate.step");
        let (_, a) = crate::model::analyse_native(&path).unwrap();
        let template = &a.features[0];
        let feature = |id: &str, faces: Vec<usize>| {
            let mut f = template.clone();
            f.kind = "section_recess".into();
            f.id = id.into();
            f.faces = faces;
            f
        };
        let original = vec![
            feature("f:section_recess:1", vec![9]),
            feature("f:section_recess:2", vec![146, 152, 166]),
            feature("f:section_recess:3", vec![146, 154, 164]),
        ];
        let mut fs = original.clone();
        anchor(&mut fs, &mut []).unwrap();
        assert_eq!(
            fs.iter().map(|f| f.id.as_str()).collect::<Vec<_>>(),
            [
                "f:section_recess:f9",
                "f:section_recess:f146_0",
                "f:section_recess:f146_1"
            ]
        );
        let mut reduced = original[1..].to_vec();
        reduced.reverse();
        anchor(&mut reduced, &mut []).unwrap();
        assert_eq!(reduced[0].id, fs[2].id);
        assert_eq!(reduced[1].id, fs[1].id);
        assert_eq!(fs[1].legacy_id.as_deref(), Some("f:section_recess:2"));
    }

    #[test]
    fn authored_patterns_reserve_automatic_face_names() {
        for name in ["12", "f12", "f12_0"] {
            let spec: Spec = serde_json::from_value(serde_json::json!({"features": {
                format!("f:hole_pattern:{name}"): {"pattern":{"arrangement":"linear","members":["f:hole:f6","f:hole:f7"]}}
            }})).unwrap();
            assert!(
                crate::recognition_corrections::check(&spec).is_err(),
                "{name}"
            );
        }
        let spec: Spec = serde_json::from_value(serde_json::json!({"features": {
            "f:hole_pattern:mounting": {"pattern":{"arrangement":"linear","members":["f:hole:f6","f:hole:f7"]}}
        }})).unwrap();
        crate::recognition_corrections::check(&spec).unwrap();
    }

    #[test]
    fn approved_refusal_and_window() {
        assert!(refusal("draftwright.spec/2", "0.3.9").is_none());
        assert_eq!(
            refusal("draftwright.spec/2", "0.3.10").unwrap(),
            "This Spec is draftwright.spec/2, which draftwright 0.3.10 no longer reads: it reads only draftwright.spec/3, whose feature and step IDs are named by faces rather than by rank or measured value. Convert it once with draftwright 0.3.9, the last release that reads spec/2: run `draftwright plan PART.step --spec FILE` and keep the `spec` it prints. Then open the converted Spec here."
        );
        assert_eq!(
            refusal("draftwright.spec/1", "0.3.10"),
            refusal("draftwright.spec/2", "0.3.10").map(|s| s.replacen(
                "draftwright.spec/2",
                "draftwright.spec/1",
                1
            ))
        );
    }
    #[test]
    fn exact_whole_document_rename_and_collision() {
        let map = BTreeMap::from([("f:hole:1".into(), "f:hole:f21".into())]);
        let v = serde_json::json!({"dimensions":{"d:f:hole:1/bore.diameter":{}}, "sheet":{"origin":"f:hole:1"}, "notes":[{"text":"p:problem:d:f:hole:1/bore.diameter|f:hole:10"}]});
        let n = crate::revision_rename::rename_spec(&v, &map).unwrap();
        assert!(n["dimensions"].get("d:f:hole:f21/bore.diameter").is_some());
        assert_eq!(n["sheet"]["origin"], "f:hole:f21");
        assert_eq!(
            n["notes"][0]["text"],
            "p:problem:d:f:hole:f21/bore.diameter|f:hole:10"
        );
        assert!(
            crate::revision_rename::rename_spec(
                &serde_json::json!({"f:hole:1":1,"f:hole:f21":2}),
                &map
            )
            .is_err()
        );
    }
}
