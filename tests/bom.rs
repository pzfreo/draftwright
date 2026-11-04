//! BOM facts are read without geometry recognition or Specify as a dependency.
use draftwright::{bom, haecceity, model, pack};
use haecceity::p21::Document;
use std::path::{Path, PathBuf};

fn fixture(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/assembly")
        .join(name)
}
fn read(text: &str) -> Vec<bom::Metadata> {
    let file = haecceity::step::StepFile::read(text.as_bytes()).unwrap();
    bom::read(
        &Document::parse(text.as_bytes().to_vec()).unwrap(),
        &file.parts,
    )
}
fn small() -> String {
    String::from_utf8(model::read_step_file(&fixture("pack-small.step.gz")).unwrap()).unwrap()
}
fn append(text: &str, entities: &str) -> String {
    text.replacen(
        "ENDSEC;\nEND-ISO-10303-21;",
        &format!("{entities}\nENDSEC;\nEND-ISO-10303-21;"),
        1,
    )
}
fn uda(owner: u64, name: &str, items: &str) -> String {
    // Public UDA 1.8 path used by Specify, independent of its private history.
    format!(
        "#9000=PROPERTY_DEFINITION('{name}','',#{owner});
#9001=PROPERTY_DEFINITION_REPRESENTATION(#9000,#9002);
#9002=REPRESENTATION('',({items}),#9003);
#9003=REPRESENTATION_CONTEXT('','');"
    )
}

#[test]
fn native_product_formation_description_source_and_categories() {
    let s = small()
        .replace(
            "PRODUCT('plate','plate',''",
            "PRODUCT('PN-42','plate','Plate description'",
        )
        .replace(
            "PRODUCT_DEFINITION_FORMATION('','',#41)",
            "PRODUCT_DEFINITION_FORMATION_WITH_SPECIFIED_SOURCE('C','',#41,.BOUGHT.)",
        );
    let rows = read(&s);
    assert_eq!(rows[0].part_number.as_deref(), Some("PN-42"));
    assert_eq!(rows[0].description.as_deref(), Some("Plate description"));
    assert_eq!(rows[0].revision.as_deref(), Some("C"));
    assert_eq!(rows[0].make_buy.as_deref(), Some("bought"));
    assert_eq!(rows[0].categories, ["part"]);
    assert_eq!(rows[0].item_number, None); // OCCT placement id is not a find number.
    assert_eq!(rows[0].occurrence_references, ["1"]);
}

#[test]
fn case_normalisation_invalid_values_and_last_specify_override() {
    let s = small().replace(
        "PRODUCT('plate','plate',''",
        "PRODUCT('PN-42','plate','native'",
    );
    let items = "#9010,#9011,#9012,#9013,#9014,#9015,#9016,#9017,#9018";
    let entities = uda(384, "SpEcIfY BoM", items)
        + "
#9010=DESCRIPTIVE_REPRESENTATION_ITEM('MAKE-OR-BUY','PURCHASED');
#9011=DESCRIPTIVE_REPRESENTATION_ITEM('PART NUMBER','');
#9012=DESCRIPTIVE_REPRESENTATION_ITEM('Description','Override, with a comma');
#9013=DESCRIPTIVE_REPRESENTATION_ITEM('STOCK','FROM_STOCK');
#9014=BOOLEAN_REPRESENTATION_ITEM('OpTiOnAl',.T.);
#9015=INTEGER_REPRESENTATION_ITEM('FIND-NUMBER',17);
#9016=DESCRIPTIVE_REPRESENTATION_ITEM('Revision','  ');
#9017=DESCRIPTIVE_REPRESENTATION_ITEM('make_buy','nonsense');
#9018=DESCRIPTIVE_REPRESENTATION_ITEM('Name','A stated name');";
    let rows = read(&append(&s, &entities));
    let row = &rows[0];
    assert_eq!(row.part_number.as_deref(), Some("PN-42"));
    assert_eq!(row.name.as_deref(), Some("A stated name"));
    assert_eq!(row.description.as_deref(), Some("Override, with a comma"));
    assert_eq!(row.revision, None);
    assert_eq!(row.make_buy.as_deref(), Some("bought"));
    assert_eq!(row.stock.as_deref(), Some("from stock"));
    assert_eq!((row.optional, row.item_number), (Some(true), Some(17)));
}

#[test]
fn foreign_attributes_fill_gaps_and_material_native_homes() {
    let entities = uda(39, "Exporter attributes", "#9010,#9011,#9012")
        + "
#9010=DESCRIPTIVE_REPRESENTATION_ITEM('make_buy','MAKE');
#9011=DESCRIPTIVE_REPRESENTATION_ITEM('optional','perhaps');
#9012=INTEGER_REPRESENTATION_ITEM('item_number',-1);
#9020=MATERIAL_DESIGNATION('Aluminium 6082',(#39));
#9021=CLASS('ISO hardware','Standard part','ISO-42',.F.);
#9022=CLASSIFICATION_ROLE('classification');
#9023=APPLIED_CLASSIFICATION_ASSIGNMENT(#9021,#9022,(#41));";
    let row = read(&append(&small(), &entities)).remove(0);
    assert_eq!(row.make_buy.as_deref(), Some("made"));
    assert_eq!(row.material.as_deref(), Some("Aluminium 6082"));
    assert_eq!(row.classifications, ["ISO hardware"]);
    assert_eq!((row.optional, row.item_number), (None, None));
}

#[test]
fn product_definition_description_and_complex_source_formation() {
    let s = small().replace("PRODUCT_DEFINITION('design','',#40", "PRODUCT_DEFINITION('design','Definition text',#40")
        .replace("PRODUCT_DEFINITION_FORMATION('','',#41)", "(PRODUCT_DEFINITION_FORMATION('D','',#41) PRODUCT_DEFINITION_FORMATION_WITH_SPECIFIED_SOURCE(.NOT_KNOWN.))");
    let row = read(&s).remove(0);
    assert_eq!(row.description.as_deref(), Some("Definition text"));
    assert_eq!(row.revision.as_deref(), Some("D"));
    assert_eq!(row.make_buy.as_deref(), Some("not known"));
}

#[test]
fn numeric_reference_designator_and_numeric_nauo_name() {
    for occurrence in [
        "'placement','plate','',#5,#39,'23'",
        "'placement','23','',#5,#39,$",
    ] {
        let s = small().replace("'1','plate','',#5,#39,$", occurrence);
        assert_eq!(read(&s)[0].item_number, Some(23));
    }
}

#[test]
fn written_specify_fixture_public_bom_and_pack_provenance() {
    let path = fixture("bom-small.step.gz");
    let pack = pack::build(&path, &pack::PackSpec::default()).unwrap();
    assert_eq!(pack.report.rows.len(), 13);
    let mut rows: Vec<_> = pack.report.rows.iter().collect();
    rows.sort_by_key(|r| r.item);
    assert_eq!(
        rows.iter().map(|r| (r.item, r.qty)).collect::<Vec<_>>(),
        [
            (1, 1),
            (2, 1),
            (3, 4),
            (4, 2),
            (5, 1),
            (6, 1),
            (7, 1),
            (8, 1),
            (9, 1),
            (10, 1),
            (11, 1),
            (12, 1),
            (13, 1)
        ]
    );
    assert_eq!(
        rows.iter()
            .filter(|r| r.bom.stock.as_deref() == Some("from stock"))
            .map(|r| r.item)
            .collect::<Vec<_>>(),
        [12, 13]
    );
    assert_eq!(rows[12].quantity_range.as_deref(), Some("0–1"));
    assert_eq!(rows[0].bom.part_number.as_deref(), Some("GRM-01"));
    assert_eq!(rows[0].bom.revision.as_deref(), Some("B"));
    for row in &rows {
        for field in [
            "supply",
            "item",
            "material",
            "description",
            "optional",
            "stock",
        ] {
            assert_eq!(
                row.sources[field],
                pack::ValueSource::File,
                "{} {field}",
                row.name
            );
        }
        assert!(row.buy_reasons.is_empty());
        assert!(row.error.is_none(), "{} {:?}", row.name, row.error);
        if row.supply == pack::Supply::Make {
            let drawing = &pack.drawings[row.sheets[0] - 1];
            assert!(drawing.svg().contains(row.bom.material.as_deref().unwrap()));
        }
    }
    assert!(
        !pack
            .report
            .findings
            .iter()
            .any(|f| f.code.ends_with("_guess"))
    );
    let json = serde_json::to_value(&pack.report).unwrap();
    assert!(
        json["rows"]
            .as_array()
            .unwrap()
            .iter()
            .all(|r| r["material"].is_string() && r["sources"]["material"] == "file")
    );
    assert!(pack.report.csv().contains("\"0–1\""));
}

#[test]
fn spec_over_file_over_guess_including_title_fields() {
    let path = fixture("bom-small.step.gz");
    let keys =
        pack::part_keys(&model::parts(&path, &model::read_step_file(&path).unwrap()).unwrap());
    let spec: pack::PackSpec = serde_json::from_value(serde_json::json!({
        "ga":{"include":false},"parts":{keys[0].clone():{
            "supply":"buy","item":90,"quantity":2,"description":"Spec description",
            "material":"Titanium","part_number":"SPEC-1","revision":"Z","optional":true,"stock":"from stock"
        }}
    })).unwrap();
    let pack = pack::build(&path, &spec).unwrap();
    let row = pack.report.rows.iter().find(|r| r.item == 90).unwrap();
    assert_eq!((row.supply, row.qty), (pack::Supply::Buy, 2));
    assert_eq!(row.quantity_range.as_deref(), Some("0–2"));
    assert_eq!(row.bom.material.as_deref(), Some("Titanium"));
    assert_eq!(row.reference, "SPEC-1");
    for field in [
        "supply",
        "item",
        "material",
        "part_number",
        "revision",
        "optional",
        "stock",
        "description",
        "qty",
        "quantity_range",
    ] {
        assert_eq!(row.sources[field], pack::ValueSource::Spec);
    }
    assert!(
        !pack
            .report
            .findings
            .iter()
            .any(|f| f.code.ends_with("_guess"))
    );
}

#[test]
fn no_bom_data_keeps_guesses_and_absent_values_unsourced() {
    let pack = pack::build(&fixture("pack-small.step.gz"), &pack::PackSpec::default()).unwrap();
    assert_eq!(pack.report.rows.len(), 3);
    for row in &pack.report.rows {
        assert_eq!(row.sources["supply"], pack::ValueSource::Guess);
        assert_eq!(row.sources["item"], pack::ValueSource::Guess);
        for field in [
            "material",
            "description",
            "revision",
            "optional",
            "stock",
            "quantity_range",
        ] {
            assert!(!row.sources.contains_key(field));
        }
    }
}
