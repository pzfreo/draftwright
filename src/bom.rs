//! File-stated BOM facts. This reader uses the same P21 document access as PMI UDAs;
//! it does not recognise shapes or allocate numbers. Only the saved `part.process`
//! answer is read, to resolve manufacturing process before pack grouping.
use std::collections::{BTreeMap, BTreeSet};

use base64::{Engine, engine::general_purpose::STANDARD};
use haecceity::p21::{self, Attribute as A, Document, RawEntity};
use haecceity::step::PartDefinition;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default)]
pub struct Metadata {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub process: Option<crate::process::Process>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub part_number: Option<String>,
    /// This row has its own explicitly answered Specify part_number UDA.
    #[serde(skip_serializing_if = "is_false")]
    pub part_number_is_explicit: bool,
    /// Native PRODUCT ids, retained separately from overridden/evidenced part numbers.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub product_ids: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub part_number_suggestion: Option<String>,
    /// Fields filled from stored default.<field> suggestions, not file statements.
    #[serde(skip_serializing_if = "BTreeSet::is_empty")]
    pub suggested_fields: BTreeSet<String>,
    /// Native source retained even when NOT_KNOWN falls through to guessing.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub file_make_buy: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub revision: Option<String>,
    /// `made`, `bought`, or explicitly `not known`; the last is a fact, not a guess.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub make_buy: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub material: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub item_number: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub optional: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stock: Option<String>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub categories: Vec<String>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub classifications: Vec<String>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub occurrence_references: Vec<String>,
}

fn is_false(value: &bool) -> bool {
    !value
}

fn attrs<'a>(doc: &'a Document, id: u64, ty: &str) -> Option<&'a [A]> {
    match doc.get(id)? {
        RawEntity::Simple {
            name, attributes, ..
        } if name == ty
            || haecceity::express::is_a(&name.to_ascii_lowercase(), &ty.to_ascii_lowercase()) =>
        {
            Some(attributes)
        }
        RawEntity::Complex { parts, .. } => parts
            .iter()
            .find(|p| p.name == ty)
            .map(|p| p.attributes.as_slice()),
        _ => None,
    }
}
fn reference(a: &A) -> Option<u64> {
    if let A::EntityRef(id) = a {
        Some(*id)
    } else {
        None
    }
}
fn text(a: &A) -> Option<String> {
    let s = match a {
        A::String(s) => p21::decode(s).ok()?,
        A::Enum(s) => s.to_ascii_lowercase().replace('_', " "),
        _ => return None,
    };
    (!s.trim().is_empty()).then(|| s.trim().to_string())
}
fn at(doc: &Document, id: u64, ty: &str, index: usize) -> Option<String> {
    attrs(doc, id, ty)?.get(index).and_then(text)
}
fn link(doc: &Document, id: u64, ty: &str, index: usize) -> Option<u64> {
    attrs(doc, id, ty)?.get(index).and_then(reference)
}
fn list(a: Option<&A>) -> Vec<u64> {
    match a {
        Some(A::List(v)) => v.iter().filter_map(reference).collect(),
        _ => Vec::new(),
    }
}
fn key(s: &str) -> String {
    s.trim().to_ascii_lowercase().replace([' ', '-'], "_")
}
fn term(s: &str) -> String {
    s.trim().to_ascii_lowercase().replace(['_', '-'], " ")
}
fn source(s: &str) -> Option<String> {
    Some(
        match term(s).as_str() {
            "made" | "make" => "made",
            "bought" | "buy" | "purchased" => "bought",
            "not known" | "unknown" => "not known",
            _ => return None,
        }
        .into(),
    )
}
pub(crate) fn stock(s: &str) -> Option<String> {
    Some(
        match term(s).as_str() {
            "from stock" | "stock" | "in stock" => "from stock",
            "to be made" | "make" => "to be made",
            _ => return None,
        }
        .into(),
    )
}
fn boolean(a: &A) -> Option<bool> {
    if let A::Enum(value) = a {
        return match value.as_str() {
            "T" => Some(true),
            "F" => Some(false),
            _ => None,
        };
    }
    match term(&text(a)?).as_str() {
        "yes" | "true" => Some(true),
        "no" | "false" => Some(false),
        _ => None,
    }
}

fn number(a: &A) -> Option<u32> {
    let n = match a {
        A::Integer(n) => u32::try_from(*n).ok()?,
        _ => text(a)?.parse().ok()?,
    };
    (n > 0).then_some(n)
}

/// Representation items on a property, including complex typed UDA items.
fn items(doc: &Document, property: u64) -> BTreeMap<String, A> {
    let mut result = BTreeMap::new();
    for &id in doc.referrers(property) {
        if link(doc, id, "PROPERTY_DEFINITION_REPRESENTATION", 0) != Some(property) {
            continue;
        }
        let Some(rep) = link(doc, id, "PROPERTY_DEFINITION_REPRESENTATION", 1) else {
            continue;
        };
        for item in list(attrs(doc, rep, "REPRESENTATION").and_then(|v| v.get(1))) {
            let name = at(doc, item, "REPRESENTATION_ITEM", 0).or_else(|| {
                at(doc, item, "DESCRIPTIVE_REPRESENTATION_ITEM", 0)
                    .or_else(|| at(doc, item, "INTEGER_REPRESENTATION_ITEM", 0))
                    .or_else(|| at(doc, item, "BOOLEAN_REPRESENTATION_ITEM", 0))
            });
            let Some(name) = name else {
                continue;
            };
            for ty in [
                "DESCRIPTIVE_REPRESENTATION_ITEM",
                "INTEGER_REPRESENTATION_ITEM",
                "BOOLEAN_REPRESENTATION_ITEM",
            ] {
                if let Some(a) = attrs(doc, item, ty) {
                    // Complex subtype components carry only their own value.
                    if let Some(value) = a.get(if a.len() == 1 { 0 } else { 1 }) {
                        result.insert(key(&name), value.clone());
                    }
                }
            }
        }
    }
    result
}

fn properties(doc: &Document, owner: u64, row: &mut Metadata) {
    for &p in doc.referrers(owner) {
        if link(doc, p, "PROPERTY_DEFINITION", 2) != Some(owner) {
            continue;
        }
        let set = at(doc, p, "PROPERTY_DEFINITION", 0).unwrap_or_default();
        let values = items(doc, p);
        if term(&set) == "material property"
            && at(doc, p, "PROPERTY_DEFINITION", 1).is_some_and(|s| term(&s) == "material name")
        {
            // Preserve the material designation used by the existing title
            // block. Descriptive text is a fallback when no designation exists.
            let names: Vec<_> = doc
                .referrers(p)
                .iter()
                .filter_map(|&id| {
                    (link(doc, id, "PROPERTY_DEFINITION_REPRESENTATION", 0) == Some(p))
                        .then(|| link(doc, id, "PROPERTY_DEFINITION_REPRESENTATION", 1))
                        .flatten()
                })
                .flat_map(|rep| list(attrs(doc, rep, "REPRESENTATION").and_then(|v| v.get(1))))
                .filter_map(|id| {
                    at(doc, id, "DESCRIPTIVE_REPRESENTATION_ITEM", 0)
                        .or_else(|| at(doc, id, "DESCRIPTIVE_REPRESENTATION_ITEM", 1))
                })
                .collect();
            if let [name] = names.as_slice() {
                row.material = Some(name.clone());
            }
        }
        apply_items(&values, term(&set) == "specify bom", row);
    }
}

fn apply_process(values: &BTreeMap<String, A>, row: &mut Metadata) {
    let Some(process) = values
        .get("manufacturing_process")
        .and_then(text)
        .and_then(|v| process_value(&v))
    else {
        return;
    };
    let suggested = process_default(values);
    if row.process.is_none() || !suggested {
        row.process = Some(process);
        if suggested {
            row.suggested_fields.insert("process".into());
        } else {
            row.suggested_fields.remove("process");
        }
    }
}

fn process_default(values: &BTreeMap<String, A>) -> bool {
    values
        .get("default.manufacturing_process")
        .is_some_and(|v| {
            matches!(v, A::Enum(s) if s == "T")
                || text(v).is_some_and(|s| s.eq_ignore_ascii_case("true"))
        })
}

fn process_value(value: &str) -> Option<crate::process::Process> {
    use crate::process::Process;
    Some(match value.trim().to_ascii_lowercase().as_str() {
        "machined" => Process::Machined,
        "3d printed: fdm" => Process::Fdm,
        "3d printed: resin" => Process::Resin,
        "3d printed: powder" => Process::Powder,
        "3d printed: metal" => Process::Metal,
        "sheet metal" => Process::SheetMetal,
        "moulded" => Process::Moulded,
        "cast" => Process::Cast,
        _ => return None,
    })
}

/// Specify's process::read precedence: its last unmarked answer, the first
/// foreign statement, then its last marked suggestion. Product-level only.
fn file_process(doc: &Document, pd: u64, shape: u64) -> Option<(crate::process::Process, bool)> {
    let (mut answered, mut file, mut suggested) = (None, None, None);
    for owner in [pd, shape] {
        // Property order is record order, rather than STEP entity-number order.
        let mut properties: Vec<_> = doc.referrers(owner).iter().copied().collect();
        properties.sort_by_key(|&id| doc.span(id).map(|s| s.start));
        for id in properties {
            if link(doc, id, "PROPERTY_DEFINITION", 2) != Some(owner) {
                continue;
            }
            let set = at(doc, id, "PROPERTY_DEFINITION", 0).unwrap_or_default();
            let values = items(doc, id);
            let process = |key: &str| {
                values
                    .get(key)
                    .and_then(text)
                    .and_then(|s| process_value(&s))
            };
            if let Some(value) = process("manufacturing_process") {
                if set != "specify process" {
                    file.get_or_insert(value);
                } else if process_default(&values) {
                    suggested = Some(value);
                } else {
                    answered = Some(value);
                }
            } else if set == "specify process"
                && let Some(value) = process("specify_process")
            {
                // OCCT rewrites a set to one item named after it, losing its
                // default marker. Specify treats that as a foreign statement.
                file.get_or_insert(value);
            }
        }
    }
    answered
        .or(file)
        .map(|p| (p, false))
        .or_else(|| suggested.map(|p| (p, true)))
}

/// Read only Specify's part.process answer, not feature/face answers. Match its
/// resume::stored_in framing: schemas 1..3; first legacy frame wins, a later
/// provenance-scoped frame replaces it (even if it has no process answer).
fn answered_processes(doc: &Document) -> BTreeMap<u64, crate::process::Process> {
    fn stored_text(doc: &Document, id: u64) -> Option<String> {
        if let Some(a) = attrs(doc, id, "DESCRIPTIVE_REPRESENTATION_ITEM") {
            return a.get(1).and_then(text);
        }
        let a = attrs(doc, id, "COMPOUND_REPRESENTATION_ITEM")?;
        let A::Typed { type_name, value } = a.get(1)? else {
            return None;
        };
        if type_name != "LIST_REPRESENTATION_ITEM" {
            return None;
        }
        let A::List(children) = value.as_ref() else {
            return None;
        };
        if children.is_empty() {
            return None;
        }
        let mut joined = String::new();
        for child in children {
            let a = attrs(doc, reference(child)?, "DESCRIPTIVE_REPRESENTATION_ITEM")?;
            if a.first().and_then(text).as_deref() != Some("haecceity text chunk/1") {
                return None;
            }
            joined.push_str(&a.get(1).and_then(text)?);
        }
        Some(joined)
    }
    let mut records: Vec<_> = doc
        .ids()
        .filter(|&id| {
            matches!(doc.get(id), Some(RawEntity::Simple { name, attributes, .. })
                if matches!(name.as_str(), "DESCRIPTIVE_REPRESENTATION_ITEM" | "COMPOUND_REPRESENTATION_ITEM")
                    && matches!(attributes.first(), Some(A::String(s)) if s == "pmi-assist answers"))
        })
        .collect();
    records.sort_by_key(|&id| doc.span(id).map(|s| s.start));
    let mut answers = BTreeMap::new();
    for id in records {
        let Some(payload) = stored_text(doc, id)
            .and_then(|s| STANDARD.decode(s).ok())
            .and_then(|bytes| serde_json::from_slice::<serde_json::Value>(&bytes).ok())
        else {
            continue;
        };
        let schema = match &payload["schema"] {
            serde_json::Value::Bool(b) => Some(f64::from(u8::from(*b))),
            value => value.as_f64(),
        };
        if !schema.is_some_and(|s| [1., 2., 3.].contains(&s)) || !payload["answers"].is_object() {
            continue;
        }
        let part = match payload.get("part") {
            None => Some(0),
            Some(serde_json::Value::Bool(b)) => Some(u64::from(*b)),
            Some(value) => value.as_u64(),
        };
        let Some(part) = part else { continue };
        // Answers accept the exact offered enum, unlike foreign file text.
        let process = serde_json::from_value::<crate::process::Process>(
            payload["answers"]["part.process"].clone(),
        )
        .ok();
        if payload["pmi_scope"] == "specify-core:written:v1" {
            answers.insert(part, process);
        } else {
            answers.entry(part).or_insert(process);
        }
    }
    if !answers.values().any(Option::is_some) {
        return BTreeMap::new();
    }
    // Callers may request just one definition from an assembly. Stored indexes
    // always address the original file's definitions, never that subset.
    haecceity::step::read_part_definitions(doc.bytes())
        .unwrap_or_default()
        .into_iter()
        .enumerate()
        .filter_map(|(i, d)| {
            Some((
                d.product_definition,
                answers.get(&(i as u64)).copied().flatten()?,
            ))
        })
        .collect()
}

fn apply_items(values: &BTreeMap<String, A>, overrides: bool, row: &mut Metadata) {
    for (k, value) in values {
        // Explicit answers override; stored suggestions fill gaps and keep their provenance.
        let explicit = overrides
            && !values
                .get(&format!("default.{k}"))
                .is_some_and(|v| matches!(v, A::Enum(s) if s == "T"));
        macro_rules! put {
            ($field:ident, $value:expr) => {
                if let Some(value) = $value
                    && (explicit || row.$field.is_none())
                {
                    row.$field = Some(value);
                    let field = stringify!($field).to_string();
                    if overrides && !explicit {
                        row.suggested_fields.insert(field);
                    } else {
                        row.suggested_fields.remove(&field);
                    }
                }
            };
        }

        match k.as_str() {
            "part_number" if overrides && explicit => {
                put!(part_number, text(value));
                row.part_number_is_explicit = row.part_number.is_some();
                row.part_number_suggestion = None;
            }
            "part_number" if overrides => {
                put!(part_number_suggestion, text(value));
            }
            "name" if overrides => put!(name, text(value)),
            "description" => put!(description, text(value)),
            "revision" => put!(revision, text(value)),
            "make_buy" | "make_or_buy" => put!(make_buy, text(value).and_then(|s| source(&s))),
            "optional" => put!(optional, boolean(value)),
            "stock" => put!(stock, text(value).and_then(|s| stock(&s))),
            "item_number" | "find_number" => put!(item_number, number(value)),
            _ => {}
        }
    }
}

/// BOM property associations are not manufacturing annotations. Mark only
/// properties with usable public values and a resolved BOM owner as accounted
/// for; unknown sets/invalid values still follow ordinary PMI accounting.
pub(crate) fn accounts_for(doc: &Document, id: u64, definitions: &[PartDefinition]) -> bool {
    if attrs(doc, id, "GENERAL_PROPERTY").is_some() {
        let associations: Vec<_> = doc
            .referrers(id)
            .iter()
            .copied()
            .filter(|&a| link(doc, a, "GENERAL_PROPERTY_ASSOCIATION", 2) == Some(id))
            .collect();
        return !associations.is_empty()
            && associations
                .iter()
                .all(|&a| accounts_for(doc, a, definitions));
    }
    let property = link(doc, id, "GENERAL_PROPERTY_ASSOCIATION", 3)
        .or_else(|| link(doc, id, "PROPERTY_DEFINITION_REPRESENTATION", 0))
        .unwrap_or(id);
    let Some(owner) = link(doc, property, "PROPERTY_DEFINITION", 2) else {
        if let Some(a) = attrs(doc, id, "MATERIAL_DESIGNATION") {
            return a.first().and_then(text).is_some()
                && definitions.iter().any(|d| {
                    list(a.get(1))
                        .iter()
                        .any(|o| *o == d.product_definition || *o == d.shape)
                });
        }
        return false;
    };
    let Some(set) = at(doc, property, "PROPERTY_DEFINITION", 0) else {
        return false;
    };
    let values = items(doc, property);
    if term(&set) == "specify bom item numbers" {
        return link(doc, owner, "PRODUCT_DEFINITION_SHAPE", 2)
            .is_some_and(|pd| attrs(doc, pd, "PRODUCT_DEFINITION").is_some())
            && !values.is_empty()
            && values.iter().all(|(k, v)| {
                k.strip_prefix("history.").is_some_and(|suffix| {
                    suffix.len() == 6 && suffix.bytes().all(|c| c.is_ascii_digit())
                }) && text(v).is_some()
            });
    }
    let is_leaf_owner = definitions.iter().any(|d| {
        let pd = d.product_definition;
        let product = link(doc, pd, "PRODUCT_DEFINITION", 2)
            .and_then(|f| link(doc, f, "PRODUCT_DEFINITION_FORMATION", 2));
        owner == pd
            || owner == d.shape
            || Some(owner) == product
            || link(doc, owner, "NEXT_ASSEMBLY_USAGE_OCCURRENCE", 4) == Some(pd)
            || link(doc, owner, "PRODUCT_DEFINITION_RELATIONSHIP", 4) == Some(pd)
    });
    if !is_leaf_owner {
        return false;
    }
    if term(&set) == "specify process" {
        let mut facts = Metadata::default();
        apply_process(&values, &mut facts);
        return attrs(doc, owner, "PRODUCT_DEFINITION").is_some()
            && facts.process.is_some()
            && values.keys().all(|k| {
                matches!(
                    k.as_str(),
                    "manufacturing_process" | "default.manufacturing_process"
                )
            });
    }
    // A recognised BOM value does not account for an unrelated attribute in
    // the same set. Keep mixed manufacturing/property sets reportable.
    let fields = [
        "part_number",
        "name",
        "description",
        "revision",
        "make_buy",
        "make_or_buy",
        "optional",
        "stock",
        "item_number",
        "find_number",
    ];
    if values.keys().any(|k| {
        !fields.contains(&k.as_str())
            && k != "item_number_source"
            && !k
                .strip_prefix("default.")
                .is_some_and(|field| fields.contains(&field))
    }) {
        return false;
    }
    let mut facts = Metadata::default();
    apply_items(&values, term(&set) == "specify bom", &mut facts);
    facts != Metadata::default()
}

/// Read only metadata for the supplied leaf definitions. Reverse references keep
/// the work proportional to their properties, rather than scanning per leaf.
pub fn read(doc: &Document, definitions: &[PartDefinition]) -> Vec<Metadata> {
    let answers = answered_processes(doc);
    definitions
        .iter()
        .map(|d| {
            let mut row = read_definition(doc, d.product_definition, d.shape, &d.name);
            if let Some(&process) = answers.get(&d.product_definition) {
                row.process = Some(process);
                row.suggested_fields.remove("process");
            }
            // Specify's process::of has no process for an explicitly bought part.
            if row.make_buy.as_deref() == Some("bought")
                && !row.suggested_fields.contains("make_buy")
            {
                row.process = None;
                row.suggested_fields.remove("process");
            }
            row
        })
        .collect()
}

/// Assembly headers use the same public PDM/property path as component rows.
pub(crate) fn read_assembly(doc: &Document, pd: u64, name: &str) -> Metadata {
    let shape = doc
        .referrers(pd)
        .iter()
        .copied()
        .find(|id| link(doc, *id, "PRODUCT_DEFINITION_SHAPE", 2) == Some(pd))
        .unwrap_or(0);
    read_definition(doc, pd, shape, name)
}

fn read_definition(doc: &Document, pd: u64, shape: u64, name: &str) -> Metadata {
    let formation = link(doc, pd, "PRODUCT_DEFINITION", 2).unwrap_or(0);
    let product = link(doc, formation, "PRODUCT_DEFINITION_FORMATION", 2).unwrap_or(0);
    let mut row = Metadata {
        name: (!name.trim().is_empty()).then(|| name.to_owned()),
        product_ids: at(doc, product, "PRODUCT", 0).into_iter().collect(),
        description: at(doc, product, "PRODUCT", 2)
            .or_else(|| at(doc, pd, "PRODUCT_DEFINITION", 1)),
        revision: at(doc, formation, "PRODUCT_DEFINITION_FORMATION", 0),
        file_make_buy: attrs(
            doc,
            formation,
            "PRODUCT_DEFINITION_FORMATION_WITH_SPECIFIED_SOURCE",
        )
        .and_then(|a| a.last())
        .and_then(text)
        .and_then(|s| source(&s)),
        ..Metadata::default()
    };
    row.make_buy = row.file_make_buy.clone().filter(|s| s != "not known");
    let mut occurrences = Vec::new();
    for &id in doc.referrers(pd) {
        if link(doc, id, "PRODUCT_DEFINITION_RELATIONSHIP", 4)
            .or_else(|| link(doc, id, "NEXT_ASSEMBLY_USAGE_OCCURRENCE", 4))
            == Some(pd)
            && attrs(doc, id, "NEXT_ASSEMBLY_USAGE_OCCURRENCE").is_some()
        {
            occurrences.push(id);
        }
    }
    let mut numbers = Vec::new();
    for &id in &occurrences {
        let rel = attrs(doc, id, "PRODUCT_DEFINITION_RELATIONSHIP")
            .or_else(|| attrs(doc, id, "NEXT_ASSEMBLY_USAGE_OCCURRENCE"));
        let reference = attrs(doc, id, "ASSEMBLY_COMPONENT_USAGE")
            .and_then(|a| a.get(if a.len() == 1 { 0 } else { 5 }))
            .and_then(text)
            .or_else(|| at(doc, id, "NEXT_ASSEMBLY_USAGE_OCCURRENCE", 5));
        let occurrence_id = rel.and_then(|a| a.first()).and_then(text);
        row.occurrence_references
            .extend(reference.iter().chain(occurrence_id.iter()).cloned());
        // Specify's native find-number convention is NAUO.name, not its id
        // or reference_designator (which identifies occurrence locations).
        let n = rel.and_then(|a| a.get(1)).and_then(number);
        numbers.push(n);
    }
    if let Some(Some(n)) = numbers.first()
        && numbers.iter().all(|v| *v == Some(*n))
    {
        row.item_number = Some(*n);
    }
    for owner in [product, formation, pd, shape] {
        for &id in doc.referrers(owner) {
            if let Some(a) = attrs(doc, id, "MATERIAL_DESIGNATION")
                && list(a.get(1)).contains(&owner)
                && row.material.is_none()
            {
                row.material = a.first().and_then(text);
            }
            if let Some(a) = attrs(doc, id, "PRODUCT_RELATED_PRODUCT_CATEGORY")
                && list(a.get(if a.len() == 1 { 0 } else { 2 })).contains(&product)
                && let Some(name) =
                    at(doc, id, "PRODUCT_CATEGORY", 0).or_else(|| a.first().and_then(text))
            {
                row.categories.push(name);
            }
            if let Some(a) = attrs(doc, id, "APPLIED_CLASSIFICATION_ASSIGNMENT")
                && list(a.get(if a.len() == 1 { 0 } else { 2 })).contains(&owner)
                && let Some(class) = link(doc, id, "CLASSIFICATION_ASSIGNMENT", 0)
                    .or_else(|| a.first().and_then(reference))
            {
                let value = at(doc, class, "GROUP", 0).or_else(|| at(doc, class, "CLASS", 0));
                if let Some(value) = value {
                    row.classifications.push(value);
                }
            }
        }
        properties(doc, owner, &mut row);
    }
    for owner in occurrences {
        properties(doc, owner, &mut row);
    }
    if let Some((process, suggested)) = file_process(doc, pd, shape) {
        row.process = Some(process);
        if suggested {
            row.suggested_fields.insert("process".into());
        }
    }
    row.categories = row
        .categories
        .into_iter()
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    row.classifications = row
        .classifications
        .into_iter()
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    row
}

impl Metadata {
    pub(crate) fn stated_part_number(&self) -> Option<&String> {
        self.part_number
            .as_ref()
            .filter(|_| self.part_number_is_explicit)
    }

    /// The stated BOM facts used by Specify 0.1.14's `same_as` rule, plus
    /// manufacturing process: Paul's decision, "Process counts". Congruent
    /// parts made by different stated processes require separate drawings.
    /// Names, defaults, exporter ids, find numbers and other metadata do not
    /// assert that congruent products are different items.
    pub(crate) fn compatible(&self, other: &Self) -> bool {
        fn stated<'a, T>(row: &Metadata, field: &str, value: &'a Option<T>) -> Option<&'a T> {
            value
                .as_ref()
                .filter(|_| !row.suggested_fields.contains(field))
        }
        fn supply(row: &Metadata) -> Option<&String> {
            // Native NOT_KNOWN is retained separately to allow supply guesses
            // in drawings, but is still a stated fact when comparing products.
            stated(row, "make_buy", &row.make_buy).or(row.file_make_buy.as_ref())
        }
        stated(self, "part_number", &self.part_number).filter(|_| self.part_number_is_explicit)
            == stated(other, "part_number", &other.part_number)
                .filter(|_| other.part_number_is_explicit)
            && stated(self, "description", &self.description)
                == stated(other, "description", &other.description)
            && stated(self, "revision", &self.revision)
                == stated(other, "revision", &other.revision)
            && supply(self) == supply(other)
            && stated(self, "material", &self.material)
                == stated(other, "material", &other.material)
            && stated(self, "optional", &self.optional)
                == stated(other, "optional", &other.optional)
            && stated(self, "stock", &self.stock) == stated(other, "stock", &other.stock)
            && stated(self, "process", &self.process) == stated(other, "process", &other.process)
    }
}

#[cfg(test)]
mod grouping_tests {
    use super::Metadata;

    #[test]
    fn only_stated_bom_facts_decide_compatibility() {
        let empty = Metadata::default();
        let mut exported = Metadata {
            name: Some("Different name #2".into()),
            part_number: Some("RAW-PRODUCT-ID".into()),
            product_ids: vec!["RAW-PRODUCT-ID".into()],
            part_number_suggestion: Some("Proposed number".into()),
            item_number: Some(42),
            categories: vec!["part".into()],
            classifications: vec!["catalogue".into()],
            occurrence_references: vec!["location 2".into()],
            ..Metadata::default()
        };
        assert!(empty.compatible(&exported));
        assert!(exported.compatible(&empty));
        exported.description = Some("Default description".into());
        exported.revision = Some("Default revision".into());
        exported.make_buy = Some("bought".into());
        exported.material = Some("Default material".into());
        exported.optional = Some(false);
        exported.stock = Some("from stock".into());
        exported.process = Some(crate::process::Process::Fdm);
        exported.suggested_fields = [
            "description",
            "revision",
            "make_buy",
            "material",
            "optional",
            "stock",
            "process",
        ]
        .map(String::from)
        .into();
        assert!(empty.compatible(&exported));
        assert!(exported.compatible(&empty));
        // Paul's "Process counts": only defaulted/guessed process is ignored.
        exported.suggested_fields.remove("process");
        assert!(!empty.compatible(&exported));
        exported.suggested_fields.insert("process".into());
        // An explicitly stated false is different from an unstated optional flag.
        exported.suggested_fields.remove("optional");
        assert!(!empty.compatible(&exported));
    }

    #[test]
    fn native_unknown_supply_is_a_fact_even_when_a_default_fills_the_display() {
        let native = Metadata {
            file_make_buy: Some("not known".into()),
            ..Metadata::default()
        };
        let suggested = Metadata {
            make_buy: Some("made".into()),
            suggested_fields: ["make_buy".into()].into(),
            ..native.clone()
        };
        assert!(native.compatible(&suggested));
        assert!(!native.compatible(&Metadata::default()));
        assert!(!native.compatible(&Metadata {
            make_buy: Some("made".into()),
            ..Metadata::default()
        }));
    }
}

thread_local! {
    static PLANNING_TITLES: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
}

/// File defaults fill existing cells after the drawing's layout is settled.
/// A long exported id must not select different paper, views or dimensions.
/// All real title-cell overflow remains reported on the returned plan.
pub(crate) fn with_titles(
    analysis: &crate::model::Analysis,
    spec: &crate::model::Spec,
    run: impl FnOnce() -> Result<crate::model::Plan, crate::model::Invalid>,
) -> Result<crate::model::Plan, crate::model::Invalid> {
    struct Restore(bool);
    impl Drop for Restore {
        fn drop(&mut self) {
            PLANNING_TITLES.set(self.0);
        }
    }
    let nested = PLANNING_TITLES.replace(true);
    let _restore = Restore(nested);
    let mut plan = run()?;
    if nested {
        return Ok(plan);
    }
    let Some(metadata) = &analysis.bom else {
        return Ok(plan);
    };
    fill_titles(metadata, spec, &mut plan);
    Ok(plan)
}

pub(crate) fn fill_titles(
    metadata: &Metadata,
    spec: &crate::model::Spec,
    plan: &mut crate::model::Plan,
) {
    let defaults = [
        ("number", "drawing_number", metadata.stated_part_number()),
        ("material", "material", metadata.material.as_ref()),
    ];
    let mut problems = Vec::new();
    let mut changed = BTreeSet::new();
    for sheet in std::iter::once(&mut plan.sheet).chain(&mut plan.sheets) {
        for (key, field, value) in defaults {
            if spec.sheet.title.contains_key(key) {
                continue;
            }
            let Some(value) = value else {
                continue;
            };
            if sheet.title.get(key) == Some(value) {
                continue;
            }
            sheet.title.insert(key.into(), value.clone());
            for cell in sheet.title_cells.iter_mut().filter(|c| c.field == field) {
                cell.value = Some(value.clone());
                changed.insert(format!("title_block/{field}"));
                for mut problem in
                    crate::title_block::problems(std::slice::from_ref(cell), sheet.page)
                {
                    if sheet.number > 1 {
                        problem.id += &format!(":sheet:{}", sheet.number);
                        problem.subjects = vec![format!("sheet:{}", sheet.number)];
                    }
                    problems.push(problem);
                }
            }
        }
    }
    plan.problems.retain(|p| {
        !(["title_field_overflow", "annotation_off_sheet"].contains(&p.code.as_str())
            && p.refs.iter().any(|r| changed.contains(r)))
    });
    plan.problems.extend(problems);
    plan.status = crate::completeness::status(&plan.problems);
}

impl Metadata {
    /// Independent product fact or material-class guess; reads no authoring or layout state.
    pub fn manufacturing_process(&self) -> crate::process::Process {
        use crate::process::Process;
        self.process.unwrap_or_else(|| {
            self.classifications
                .iter()
                .chain(&self.categories)
                .find_map(|s| match s.to_ascii_lowercase().as_str() {
                    "3d printing: filament" => Some(Process::Fdm),
                    "3d printing: resin" => Some(Process::Resin),
                    "3d printing: powder" => Some(Process::Powder),
                    "3d printing: metal" => Some(Process::Metal),
                    _ => None,
                })
                .unwrap_or_default()
        })
    }
}
