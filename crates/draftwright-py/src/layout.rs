//! Layout-only Spec patches for Part drawings. Engineering intent belongs in STEP PMI.
use serde_json::Value;

fn refuse(path: &str) -> String {
    format!(
        "{path} is a requirement: use Part.material(), general_tolerance(), process(), datum(), hole()/holes(), face(), ; Drawing.update/spec only sets layout"
    )
}

fn requirements(v: &Value) -> Result<(), String> {
    for key in ["process", "envelope_tolerance", "faces", "notes", "gears"] {
        if v.get(key).is_some() {
            return Err(refuse(key));
        }
    }
    if let Some(sheet) = v.get("sheet") {
        if sheet.get("default_finish").is_some() {
            return Err(refuse("sheet/default_finish"));
        }
        if let Some(title) = sheet.get("title") {
            for key in ["material", "tolerance"] {
                if title.get(key).is_some() {
                    return Err(refuse(&format!("sheet/title/{key}")));
                }
            }
        }
    }
    for (collection, keys) in [
        (
            "features",
            &[
                "fit",
                "datum",
                "controls",
                "finish",
                "thread",
                "knurl",
                "note",
                "note_satisfies",
            ][..],
        ),
        ("dimensions", &["tolerance", "fit"][..]),
    ] {
        for (id, settings) in v[collection].as_object().into_iter().flatten() {
            for key in keys {
                if settings.get(key).is_some() {
                    return Err(refuse(&format!("{collection}/{id}/{key}")));
                }
            }
        }
    }
    Ok(())
}

fn merge(base: &mut Value, patch: Value) {
    match (base, patch) {
        (Value::Object(base), Value::Object(patch)) => {
            for (key, value) in patch {
                merge(base.entry(key).or_insert(Value::Null), value);
            }
        }
        (base, patch) => *base = patch,
    }
}

pub fn update(base: &str, patch: &str) -> Result<String, String> {
    let mut base: Value = serde_json::from_str(base).map_err(|e| e.to_string())?;
    let patch: Value = serde_json::from_str(patch).map_err(|e| e.to_string())?;
    if !patch.is_object() {
        return Err("Drawing.update/spec needs a Spec dictionary".into());
    }
    merge(&mut base, patch);
    requirements(&base)?;
    let schema: Value = serde_json::from_str(include_str!("../../../schemas/spec.json"))
        .expect("published Spec schema");
    draftwright::mcp::validate(&schema, &base)?;
    // Typed parsing also enforces custom vocabulary and semantic field constraints.
    serde_json::from_value::<draftwright::model::Spec>(base.clone()).map_err(|e| e.to_string())?;
    Ok(base.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn patches_are_layout_only_and_validate_before_commit() {
        let base = r#"{"sheet":{"title":{"title":"Plate"}},"views":{"iso":{"style":"shaded"}}}"#;
        let result: Value = serde_json::from_str(
            &update(
                base,
                r#"{"sheet":{"title":{"drawn_by":"Author","revision":"B"}}}"#,
            )
            .unwrap(),
        )
        .unwrap();
        assert_eq!(
            result["sheet"]["title"],
            json!({"title":"Plate","drawn_by":"Author","revision":"B"})
        );
        assert_eq!(result["views"]["iso"]["style"], "shaded");
        for patch in [
            r#"{"sheet":{"title":{"material":"steel"}}}"#,
            r#"{"features":{"f:hole:f1":{"fit":"H7"}}}"#,
            r#"{"dimensions":{"d:a":{"tolerance":"±0.1"}}}"#,
            r#"{"process":"machined"}"#,
            r#"{"notes":[]}"#,
        ] {
            assert!(update(base, patch).unwrap_err().contains("Part."));
        }
        for patch in [
            r#"{"sheet":{"bogus":true}}"#,
            r#"{"sheet":{"title":{"date":5}}}"#,
            r#"{"views":{"iso":{"style":"bad"}}}"#,
        ] {
            assert!(update(base, patch).is_err());
        }
    }
}
