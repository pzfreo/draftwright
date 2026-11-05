use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use draftwright::pack::{self, Pack, PackSpec, PartSpec, Supply};
use serde_json::json;

fn fixture(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/assembly")
        .join(name)
}

fn small() -> &'static Pack {
    static PACK: OnceLock<Pack> = OnceLock::new();
    PACK.get_or_init(|| pack::build(&fixture("pack-small.step.gz"), &PackSpec::default()).unwrap())
}

#[test]
fn groups_quantities_items_csv_and_furniture() {
    let pack = small();
    let rows = &pack.report.rows;
    assert_eq!(rows.len(), 3);
    assert_eq!(rows.iter().map(|r| r.item).collect::<Vec<_>>(), [1, 2, 3]);
    let pin = rows.iter().find(|r| r.name == "pin A").unwrap();
    assert_eq!(pin.qty, 2);
    assert_eq!(pin.products.len(), 2);
    assert_eq!(pin.supply, Supply::Make);
    assert_eq!(rows[2].supply, Supply::Buy);
    assert!(rows[2].buy_reasons[0].contains("screw"));
    assert!(rows[2].sheets.is_empty());
    assert!(
        rows.iter().all(|r| r.error.is_none()),
        "{}",
        serde_json::to_string(rows).unwrap()
    );
    assert_eq!(pack.report.pages, pack.drawings.len());
    assert!(
        pack.report
            .findings
            .iter()
            .any(|f| f.code == "pack_geometry_merge" && f.products.len() == 2)
    );
    assert_eq!(pack.report.csv().lines().count(), 4);
    assert!(pack.report.csv().contains("\"2\",\"make\""));
    for row in rows.iter().filter(|r| r.supply == Supply::Make) {
        for &page in &row.sheets {
            let svg = pack.drawings[page - 1].svg();
            assert!(svg.contains(&format!(">{}/{}<", page, pack.report.pages)));
            for value in [
                &row.name,
                "ITEM",
                "QTY",
                &row.item.to_string(),
                &row.qty.to_string(),
            ] {
                assert!(
                    svg.contains(&format!(">{value}<")),
                    "missing {value} on page {page}"
                );
            }
        }
    }
}

#[test]
fn overrides_reserve_items_keep_guesses_and_change_quantity_and_supply() {
    let key = small().report.rows[2].key.clone();
    let spec = PackSpec {
        parts: [(
            key,
            PartSpec {
                supply: Some(Supply::Make),
                item: Some(1),
                quantity: Some(8),
                ..Default::default()
            },
        )]
        .into(),
        ..Default::default()
    };
    let pack = pack::build(&fixture("pack-small.step.gz"), &spec).unwrap();
    assert_eq!(
        pack.report.rows.iter().map(|r| r.item).collect::<Vec<_>>(),
        [2, 3, 1]
    );
    let row = &pack.report.rows[2];
    assert_eq!((row.supply, row.qty), (Supply::Make, 8));
    assert!(!row.sheets.is_empty());
    assert!(
        pack.report
            .findings
            .iter()
            .any(|f| f.code == "pack_buy_guess" && f.products.contains(&row.key))
    );
    let again = pack::build(&fixture("pack-small.step.gz"), &spec).unwrap();
    assert_eq!(
        serde_json::to_value(&pack.report).unwrap(),
        serde_json::to_value(&again.report).unwrap()
    );
}

#[test]
fn strict_spec_duplicate_items_conflicting_group_intent_and_separation() {
    for value in [
        json!({"typo":1}),
        json!({"parts":{"p":{"suply":"buy"}}}),
        json!({"parts":{"p":{"drawing":{"typo":1}}}}),
    ] {
        assert!(serde_json::from_value::<PackSpec>(value).is_err());
    }
    let mut spec = PackSpec::default();
    spec.parts
        .insert("does-not-exist".into(), PartSpec::default());
    assert!(
        pack::build(&fixture("pack-small.step.gz"), &spec)
            .err()
            .unwrap()
            .message
            .contains("unknown pack part key")
    );
    spec.parts.clear();
    for row in &small().report.rows[..2] {
        spec.parts.insert(
            row.key.clone(),
            PartSpec {
                item: Some(7),
                ..Default::default()
            },
        );
    }
    assert!(
        pack::build(&fixture("pack-small.step.gz"), &spec)
            .err()
            .unwrap()
            .message
            .contains("duplicate")
    );
    let pin = &small().report.rows[1];
    spec.parts.clear();
    spec.parts.insert(
        pin.products[0].clone(),
        PartSpec {
            supply: Some(Supply::Make),
            ..Default::default()
        },
    );
    spec.parts.insert(
        pin.products[1].clone(),
        PartSpec {
            supply: Some(Supply::Buy),
            ..Default::default()
        },
    );
    assert!(
        pack::build(&fixture("pack-small.step.gz"), &spec)
            .err()
            .unwrap()
            .message
            .contains("conflicting supply")
    );
    spec.parts.get_mut(&pin.products[1]).unwrap().separate = true;
    let pack = pack::build(&fixture("pack-small.step.gz"), &spec).unwrap();
    assert_eq!(pack.report.rows.len(), 4);
    assert!(pack.report.rows.iter().all(|r| r.qty == 1));
}

#[test]
fn one_failed_drawing_keeps_its_row_and_other_drawings() {
    let row = &small().report.rows[0];
    let drawing = serde_json::from_value(json!({"sheet":{"size":"INVALID"}})).unwrap();
    let spec = PackSpec {
        parts: [(
            row.key.clone(),
            PartSpec {
                drawing: Some(drawing),
                ..Default::default()
            },
        )]
        .into(),
        ..Default::default()
    };
    let pack = pack::build(&fixture("pack-small.step.gz"), &spec).unwrap();
    assert_eq!(pack.report.rows.len(), 3);
    assert!(pack.report.rows[0].error.is_some());
    assert!(pack.report.rows[0].sheets.is_empty());
    assert!(pack.report.rows[1].error.is_none());
    assert!(!pack.report.rows[1].sheets.is_empty());
    assert_eq!(pack.report.pages, 2);
    assert!(pack.drawings[0].svg().contains(">1/2<"));
    assert!(pack.drawings[1].svg().contains(">2/2<"));
}

#[test]
fn handed_parts_do_not_merge() {
    let pack = pack::build(&fixture("pack-handed.step.gz"), &Default::default()).unwrap();
    assert_eq!(pack.report.rows.len(), 2);
    assert!(pack.report.rows.iter().all(|r| r.qty == 1));
}

#[test]
fn gramil_products_quantities_and_custom_screw_are_made() {
    let pack = pack::build(&fixture("gramil.step.gz"), &Default::default()).unwrap();
    assert_eq!(pack.report.rows.len(), 12);
    assert_eq!(
        pack.report
            .rows
            .iter()
            .filter(|r| r.supply == Supply::Make)
            .count(),
        8
    );
    assert_eq!(
        pack.report
            .rows
            .iter()
            .filter(|r| r.supply == Supply::Buy)
            .count(),
        4
    );
    for row in &pack.report.rows {
        assert_eq!(
            row.qty,
            if row.name.contains("GRM-06") {
                4
            } else if row.name.contains("GRM-07") {
                2
            } else {
                1
            }
        );
        assert_eq!(
            row.supply,
            if row.name.starts_with("GRM-") || row.name == "push rod" {
                Supply::Make
            } else {
                Supply::Buy
            },
            "{}",
            row.name
        );
        assert!(row.error.is_none(), "{}: {:?}", row.name, row.error);
    }
    assert_eq!(
        pack.report
            .findings
            .iter()
            .filter(|f| f.code == "pack_buy_guess")
            .count(),
        4
    );
}

#[test]
fn single_part_keeps_ordinary_views_measurements_and_all_sheets() {
    let step = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/plate.step");
    let pack = pack::build(&step, &Default::default()).unwrap();
    let row = &pack.report.rows[0];
    assert_eq!((row.qty, row.supply), (1, Supply::Make));
    let (part, analysis) = draftwright::model::analyse_native(&step).unwrap();
    let plan = draftwright::api::plan_analysed(&analysis, &Default::default()).unwrap();
    let font = draftwright::text::Font::new(draftwright::api::MONO.to_vec()).unwrap();
    let ordinary = draftwright::model::render_sheets(&part, &analysis, &plan, &font).unwrap();
    assert_eq!(ordinary.len(), pack.drawings.len());
    // Geometry layers are identical; pack-only title block furniture lives in dimensions.
    for (before, after) in ordinary.iter().zip(&pack.drawings) {
        assert_eq!(before.page, after.page);
        // A one-part pack keeps even the ordinary generic-name/file-stem title default.
        // Only the pack identifier runs are added to the searchable text layer.
        let after_runs: Vec<_> = after
            .text
            .iter()
            .map(|r| serde_json::to_value(r).unwrap())
            .collect();
        for run in &before.text {
            assert!(
                after_runs.contains(&serde_json::to_value(run).unwrap()),
                "lost or changed text run: {}",
                run.text
            );
        }

        for index in 0..2 {
            assert_eq!(before.layers[index].items, after.layers[index].items);
        }
        assert_eq!(before.annotations.len(), after.annotations.len());
        for (a, b) in before.annotations.iter().zip(&after.annotations) {
            assert_eq!((&a.name, &a.view, &a.claims), (&b.name, &b.view, &b.claims));
            let labels = |d: &draftwright::output::Drawing,
                          annotation: &draftwright::output::Annotation| {
                annotation
                    .runs
                    .iter()
                    .map(|&i| d.text[i].text.clone())
                    .collect::<Vec<_>>()
            };
            assert_eq!(labels(before, a), labels(after, b));
        }
    }
}

#[test]
fn cli_writes_requested_pack_files_and_refuses_unknown_format() {
    let dir = std::env::temp_dir().join(format!("draftwright-pack-cli-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_draftwright"))
        .arg("pack")
        .arg(fixture("pack-small.step.gz"))
        .arg("-o")
        .arg(&dir)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stdout)
    );
    let report: serde_json::Value =
        serde_json::from_slice(&std::fs::read(dir.join("pack.json")).unwrap()).unwrap();
    assert_eq!(report["rows"].as_array().unwrap().len(), 3);
    assert!(
        std::fs::read(dir.join("pack.pdf"))
            .unwrap()
            .starts_with(b"%PDF-")
    );
    assert_eq!(
        std::fs::read_to_string(dir.join("pack.csv"))
            .unwrap()
            .lines()
            .count(),
        4
    );
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_draftwright"))
        .arg("pack")
        .arg(fixture("pack-small.step.gz"))
        .arg("-o")
        .arg(&dir)
        .args(["--formats", "svg"])
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(2));
    std::fs::remove_dir_all(dir).unwrap();
}

#[test]
fn nested_drawing_spec_keeps_multiple_sheets_and_pack_fields_on_each_page() {
    let drawing = serde_json::from_value(json!({
        "sheet": {"size": "A4", "scale": "1:1", "title": {"company": "SHOP"}},
        "sheets": {"max": 2}, "views": {"iso": {"sheet": 2}},
        "tables": [{"id": "t:1", "sheet": 2, "rows": [["SECOND SHEET"]]}]
    }))
    .unwrap();
    let spec = PackSpec {
        parts: [(
            small().report.rows[0].key.clone(),
            PartSpec {
                drawing: Some(drawing),
                ..Default::default()
            },
        )]
        .into(),
        ..Default::default()
    };
    let pack = pack::build(&fixture("pack-small.step.gz"), &spec).unwrap();
    assert_eq!(pack.report.rows[0].sheets, [2, 3]);
    assert_eq!(pack.report.rows[1].sheets, [4]);
    assert_eq!(pack.report.general_assembly.as_ref().unwrap().sheet, 1);
    assert_eq!(
        pack.report
            .rows
            .iter()
            .flat_map(|r| r.sheets.iter().copied())
            .collect::<Vec<_>>(),
        (2..=4).collect::<Vec<_>>()
    );
    for page in 1..=pack.report.pages {
        let svg = pack.page_svg(page).unwrap();
        assert_eq!(svg, pack.drawings[page - 1].svg());
        assert!(svg.contains(&format!(">{page}/4<")));
        if page > 1 {
            let row = pack
                .report
                .rows
                .iter()
                .find(|r| r.sheets.contains(&page))
                .unwrap();
            assert!(
                pack.drawings[page - 1]
                    .text
                    .iter()
                    .any(|run| run.text == row.name)
            );
        }
    }
    for page in [0, pack.report.pages + 1, usize::MAX] {
        assert_eq!(pack.page_svg(page).unwrap_err().code, "invalid");
    }
    for (i, drawing) in pack.drawings[1..3].iter().enumerate() {
        let texts: Vec<_> = drawing.text.iter().map(|t| t.text.as_str()).collect();
        for value in ["plate", "SHOP", "ITEM", "QTY", "1", &format!("{}/4", i + 2)] {
            assert!(texts.contains(&value), "page {}: {}", i + 1, value);
        }
        assert_eq!(texts.contains(&"SECOND SHEET"), i == 1);
    }
    let (_, files) = pack::render(&fixture("pack-small.step.gz"), &spec, &["pdf"]).unwrap();
    assert!(String::from_utf8_lossy(&files[0].1).contains("/Count 4"));
}

#[test]
fn all_buy_pack_can_omit_ga_and_keep_every_row_with_no_drawing_pages() {
    let spec = PackSpec {
        ga: pack::GaSpec {
            include: Some(false),
            ..Default::default()
        },
        parts: small()
            .report
            .rows
            .iter()
            .map(|r| {
                (
                    r.key.clone(),
                    PartSpec {
                        supply: Some(Supply::Buy),
                        ..Default::default()
                    },
                )
            })
            .collect(),
        ..Default::default()
    };
    let (report, files) = pack::render(
        &fixture("pack-small.step.gz"),
        &spec,
        &["pdf", "csv", "json"],
    )
    .unwrap();
    assert_eq!(report.rows.len(), 3);
    assert_eq!(report.pages, 0);
    assert!(
        report
            .rows
            .iter()
            .all(|r| r.sheets.is_empty() && r.supply == Supply::Buy)
    );
    assert!(String::from_utf8_lossy(&files[0].1).contains("/Count 0"));
}

#[test]
fn keys_use_names_meaningful_ids_and_ordered_occurrences_without_entities() {
    let parts = [
        ("unique name", "SOLID"),
        ("ASSEMBLY", "catalogue-123"),
        ("ASSEMBLY", "COMPOUND"),
        ("ASSEMBLY", "ASSEMBLY"),
        ("", ""),
        ("", "SOLID"),
        ("ASSEMBLY#2", "SOLID"),
        ("repeat", "unique name"),
        ("repeat", "SOLID"),
    ]
    .into_iter()
    .enumerate()
    .map(|(part, (name, product_id))| draftwright::model::FilePart {
        part,
        name: name.into(),
        product_id: product_id.into(),
        faces: 0,
        instances: 1,
    })
    .collect::<Vec<_>>();
    assert_eq!(
        pack::part_keys(&parts),
        [
            "unique name",
            "catalogue-123",
            "ASSEMBLY#2#",
            "ASSEMBLY#3",
            "unnamed#1",
            "unnamed#2",
            "ASSEMBLY#2",
            "repeat#1",
            "repeat#2"
        ]
    );
}

fn edited_fixture(name: &str, source: &str, edit: impl FnOnce(String) -> String) -> PathBuf {
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR"))
        .join("pack")
        .join(name);
    std::fs::create_dir_all(&dir).unwrap();
    let bytes = draftwright::model::read_step_file(&fixture(source)).unwrap();
    let path = dir.join("assembly.step");
    std::fs::write(&path, edit(String::from_utf8(bytes).unwrap())).unwrap();
    path
}

#[test]
fn reexport_entity_numbers_do_not_change_keys_or_saved_group_intent() {
    let path = edited_fixture("numbered", "pack-small.step.gz", |s| {
        s.replace("pin A", "pin_1").replace("pin B", "pin_2")
    });
    let spec: PackSpec =
        serde_json::from_value(json!({"parts":{"pin":{"item":7,"quantity":9,"supply":"buy"}}}))
            .unwrap();
    let before = pack::build(&path, &spec).unwrap();
    let merged = before.report.rows.iter().find(|r| r.key == "pin").unwrap();
    assert_eq!(
        (&*merged.name, merged.qty, merged.item, merged.supply),
        ("pin", 9, 7, Supply::Buy)
    );
    assert_eq!(merged.products, ["pin_1", "pin_2"]);
    let path = edited_fixture("renumbered", "pack-small.step.gz", |s| {
        let s = s.replace("pin A", "pin_1").replace("pin B", "pin_2");
        let mut out = String::new();
        let mut input = s.chars().peekable();
        let mut quoted = false;
        while let Some(c) = input.next() {
            if c == '\'' {
                quoted = !quoted;
            }
            out.push(c);
            if c == '#' && !quoted {
                let mut n = String::new();
                while input.peek().is_some_and(|c| c.is_ascii_digit()) {
                    n.push(input.next().unwrap());
                }
                if !n.is_empty() {
                    out.push_str(&(n.parse::<usize>().unwrap() + 100_000).to_string());
                }
            }
        }
        out
    });
    let after = pack::build(&path, &spec).unwrap();
    let identity = |p: &Pack| {
        p.report
            .rows
            .iter()
            .map(|r| (&r.key, &r.name, &r.products, r.item, r.qty, r.supply))
            .map(|r| serde_json::to_value(r).unwrap())
            .collect::<Vec<_>>()
    };
    assert_eq!(identity(&before), identity(&after));
    let invalid: PackSpec =
        serde_json::from_value(json!({"parts":{"SOLID@definition:110":{}}})).unwrap();
    let error = pack::build(&path, &invalid).err().unwrap();
    for key in ["pin", "pin_1", "pin_2", "plate", "M2 screw"] {
        assert!(error.message.contains(key), "{error}");
    }
}

#[test]
fn merged_buy_reasons_are_distinct_in_csv_and_json() {
    let path = edited_fixture("bought-twins", "pack-small.step.gz", |s| {
        s.replace("pin A", "peg_screw_1")
            .replace("pin B", "peg_screw_2")
    });
    let pack = pack::build(&path, &Default::default()).unwrap();
    let row = pack
        .report
        .rows
        .iter()
        .find(|r| r.key == "peg_screw")
        .unwrap();
    assert_eq!(row.name, "peg_screw");
    assert_eq!(row.qty, 2);
    assert_eq!(row.buy_reasons, ["name: screw"]);
    let csv = pack.report.csv();
    let line = csv
        .lines()
        .find(|line| line.contains("peg_screw\""))
        .unwrap();
    assert_eq!(line.matches("name: screw").count(), 1);
    assert_eq!(
        serde_json::to_value(row).unwrap()["buy_reasons"],
        json!(["name: screw"])
    );
}

#[test]
fn unnamed_catalogue_shapes_use_recognised_features_and_custom_shapes_stay_made() {
    // No names or product IDs with supply hints survive: exercise only shape evidence.
    let path = edited_fixture("anonymous-shapes", "pack-shapes.step.gz", |mut s| {
        for i in 1..=9 {
            s = s.replace(&format!("body_{i}"), "ASSEMBLY");
        }
        s
    });
    let pack = pack::build(&path, &Default::default()).unwrap();
    assert_eq!(pack.report.rows.len(), 9);
    for (index, rule) in [
        (0, "thin-annulus washer"),
        (1, "two treated ends"),
        (2, "two treated ends"),
        (5, "externally threaded headed fastener"),
    ] {
        let row = pack
            .report
            .rows
            .iter()
            .find(|r| r.part_indices == [index])
            .unwrap();
        assert_eq!(row.supply, Supply::Buy, "{index}: {:?}", row.buy_reasons);
        assert!(
            row.buy_reasons
                .iter()
                .any(|r| r.starts_with("shape:") && r.contains(rule)),
            "{index}: {:?}",
            row.buy_reasons
        );
        assert!(
            pack.report
                .findings
                .iter()
                .any(|f| f.code == "pack_buy_guess"
                    && f.products == row.products
                    && f.message.contains(rule))
        );
    }
    for index in [3, 4, 6, 7, 8] {
        let row = pack
            .report
            .rows
            .iter()
            .find(|r| r.part_indices == [index])
            .unwrap();
        assert_eq!(row.supply, Supply::Make);
        assert!(row.buy_reasons.is_empty());
    }
    let washer = pack
        .report
        .rows
        .iter()
        .find(|r| r.part_indices == [0])
        .unwrap();
    let spec = PackSpec {
        parts: [(
            washer.key.clone(),
            PartSpec {
                supply: Some(Supply::Make),
                ..Default::default()
            },
        )]
        .into(),
        ..Default::default()
    };
    let overridden = pack::build(&path, &spec).unwrap();
    let washer = overridden
        .report
        .rows
        .iter()
        .find(|r| r.part_indices == [0])
        .unwrap();
    assert_eq!(washer.supply, Supply::Make);
    assert!(!washer.sheets.is_empty());
    assert!(!washer.buy_reasons.is_empty());
}

#[test]
fn renamed_gramil_custom_screw_and_bolt_still_do_not_match_catalogue_shapes() {
    let path = edited_fixture("anonymous-gramil", "gramil.step.gz", |s| {
        s.replace("GRM-03 thumbwheel drive screw", "neutral body a")
            .replace("GRM-05 depth-lock bolt", "neutral body b")
    });
    let pack = pack::build(&path, &Default::default()).unwrap();
    assert!(pack.report.rows.iter().any(|r| r.name == "neutral body a"));
    assert!(pack.report.rows.iter().any(|r| r.name == "neutral body b"));
    for index in [9, 10, 11] {
        let row = pack
            .report
            .rows
            .iter()
            .find(|r| r.part_indices == [index])
            .unwrap();
        assert_eq!(row.supply, Supply::Make, "{}", row.name);
        assert!(row.buy_reasons.is_empty());
        assert!(row.error.is_none());
    }
}

fn check_ga(pack: &Pack, step: &Path) {
    let structural = draftwright::lint::structural::check(&pack.drawings[0]);
    assert!(
        structural.is_empty(),
        "{}",
        serde_json::to_string_pretty(&structural).unwrap()
    );
    assert!(pack.drawings[0].layers[1].items.is_empty());
    let ga = pack
        .report
        .general_assembly
        .as_ref()
        .expect("GA front page");
    assert_eq!(ga.sheet, 1);
    assert!(
        pack.report
            .rows
            .iter()
            .flat_map(|r| &r.sheets)
            .all(|&s| s > 1)
    );
    assert!(
        pack.drawings[0]
            .svg()
            .contains(&format!(">1/{}<", pack.report.pages))
    );
    let table = ga.parts_list.as_ref().unwrap();
    assert_eq!(table.rows.len(), pack.report.rows.len() + 1);
    let mut items: Vec<_> = pack.report.rows.iter().map(|r| r.item).collect();
    items.sort_unstable();
    let printed: Vec<_> = table.rows[1..]
        .iter()
        .rev()
        .map(|r| r[0].parse::<u32>().unwrap())
        .collect();
    assert_eq!(printed, items);
    let placed = table.placed.as_ref().unwrap();
    for pair in placed.cells_at[1..].windows(2) {
        assert!(pair[0][0].unwrap()[1] > pair[1][0].unwrap()[1]);
    }
    for row in &pack.report.rows {
        let cells = table
            .rows
            .iter()
            .skip(1)
            .find(|c| c[0] == row.item.to_string())
            .unwrap();
        assert_eq!(cells[1], row.qty.to_string());
        assert_eq!(cells[2], row.name);
        assert_eq!(cells[3], row.reference);
        assert_eq!(
            cells[4],
            if row.supply == Supply::Make {
                "make"
            } else {
                "buy"
            }
        );
        assert_eq!(cells[5], "");
        let count = ga.balloons.iter().filter(|b| b.item == row.item).count();
        assert!(count <= 1);
        assert_eq!(
            count
                + usize::from(ga.unballooned.contains(&row.item))
                + usize::from(ga.suppressed.contains(&row.item)),
            1
        );
        if ga.unballooned.contains(&row.item) {
            assert!(
                pack.report
                    .findings
                    .iter()
                    .any(|f| f.code.starts_with("pack_ga_") && f.products == row.products)
            );
        }
    }
    // Independent reader ownership check, including bought groups and placements.
    let bytes = draftwright::model::read_step_file(step).unwrap();
    let file = draftwright::haecceity::step::StepFile::read(&bytes).unwrap();
    let part = draftwright::haecceity::read_step(&bytes).unwrap();
    for b in &ga.balloons {
        let row = pack.report.rows.iter().find(|r| r.item == b.item).unwrap();
        assert!(row.part_indices.iter().any(|&i| {
            file.parts[i]
                .placements
                .iter()
                .any(|p| p.instances.contains(&b.instance))
        }));
        assert_eq!(part.face_source(b.face).unwrap().instance, b.instance);
        let source_point = [0, 1, 2].map(|k| {
            (0..3)
                .map(|j| ga.frame[j][k] * (b.point[j] - ga.frame[j][3]))
                .sum()
        });
        if b.section {
            let solid = part
                .solids
                .iter()
                .find(|s| s.faces.contains(&b.face))
                .unwrap();
            let classifier =
                draftwright::haecceity::classify::Classifier::for_faces(&part, solid.faces.clone());
            let state = classifier.classify(source_point);
            assert!(
                state == draftwright::haecceity::classify::State::In
                    || (!b.interior && state == draftwright::haecceity::classify::State::On),
                "item {} cut ownership",
                b.item
            );
        } else {
            assert!(
                draftwright::haecceity::cloud::FaceProbe::new(&part, b.face).distance(source_point)
                    < 1e-3,
                "item {} face attachment",
                b.item
            );
        }

        assert!(b.radius > draftwright::hole_table::RADIUS);
        assert!(
            pack.drawings[0]
                .text
                .iter()
                .any(|r| r.text == b.item.to_string() && r.at == b.at)
        );
        assert!(
            pack.drawings[0]
                .sheet
                .items
                .iter()
                .any(|d| d.name == format!("ga_item_{}", b.item))
        );
    }
    let segment = |b: &draftwright::pack::ItemReference| {
        let d = [b.at[0] - b.tip[0], b.at[1] - b.tip[1]];
        let length = d[0].hypot(d[1]);
        [
            b.tip,
            [
                b.at[0] - b.radius * d[0] / length,
                b.at[1] - b.radius * d[1] / length,
            ],
        ]
    };
    let cross = |p: [f64; 2], q: [f64; 2]| p[0] * q[1] - p[1] * q[0];
    for (i, b) in ga.balloons.iter().enumerate() {
        let [p, q] = segment(b);
        let d = [q[0] - p[0], q[1] - p[1]];
        for c in &ga.balloons[i + 1..] {
            let [r, s] = segment(c);
            let e = [s[0] - r[0], s[1] - r[1]];
            let denominator = cross(d, e);
            if denominator.abs() > 1e-8 {
                let offset = [r[0] - p[0], r[1] - p[1]];
                let t = cross(offset, e) / denominator;
                let u = cross(offset, d) / denominator;
                assert!(
                    !(0. ..=1.).contains(&t) || !(0. ..=1.).contains(&u),
                    "leaders {} and {} cross",
                    b.item,
                    c.item
                );
            }
        }
        let dot=pack.drawings[0].layers.iter().flat_map(|l|&l.items).any(|item|matches!(item,
            draftwright::output::Item::Face(loops) if loops.len()==1 && matches!(loops[0].as_slice(),
            [draftwright::output::Curve::Conic {centre,radii,..}] if *centre==b.tip && *radii==[0.3,0.3])));
        assert_eq!(
            dot, b.interior,
            "item {} dot/outline-arrow convention",
            b.item
        );
    }
    let dimensions: Vec<_> = pack.drawings[0]
        .annotations
        .iter()
        .filter(|a| a.name.starts_with("d:"))
        .collect();
    assert_eq!(dimensions.len(), 3);
    assert!(dimensions.iter().all(|d| d.name.starts_with("d:envelope/")));
    assert!(
        pack.drawings[0]
            .sheet
            .fields
            .iter()
            .all(|f| f.field != "pack_qty")
    );
}

#[test]
fn ga_small_sources_list_and_numbering() {
    check_ga(small(), &fixture("pack-small.step.gz"));
    assert!(
        !small()
            .report
            .general_assembly
            .as_ref()
            .unwrap()
            .balloons
            .is_empty()
    );
}

#[test]
fn ga_gramil_sources_list_and_numbering() {
    let step = fixture("gramil.step.gz");
    let pack = pack::build(&step, &Default::default()).unwrap();
    check_ga(&pack, &step);
}

#[test]
fn ga_overrides_and_omission_preserve_part_geometry() {
    let step = fixture("pack-small.step.gz");
    let off: PackSpec = serde_json::from_value(json!({"ga":{"include":false}})).unwrap();
    let without = pack::build(&step, &off).unwrap();
    assert!(without.report.general_assembly.is_none());
    assert_eq!(small().drawings.len(), without.drawings.len() + 1);
    for (before, after) in without.drawings.iter().zip(&small().drawings[1..]) {
        assert_eq!(before.layers[0].items, after.layers[0].items);
        assert_eq!(before.layers[1].items, after.layers[1].items);
    }
    let item = small().report.general_assembly.as_ref().unwrap().balloons[0].item;
    let spec: PackSpec = serde_json::from_value(json!({"ga":{
        "drawing":{"sheet":{"size":"A3","scale":"1:1","hidden":true}},
        "balloons":{item.to_string():{"suppress":true}}
    }}))
    .unwrap();
    let pack = pack::build(&step, &spec).unwrap();
    let ga = pack.report.general_assembly.as_ref().unwrap();
    assert_eq!(ga.size, "A3");
    assert_eq!(ga.scale, "1:1");
    assert_eq!(ga.suppressed, [item]);
    assert!(!ga.balloons.iter().any(|b| b.item == item));
    assert!(!pack.drawings[0].layers[1].items.is_empty());
    let b = &small().report.general_assembly.as_ref().unwrap().balloons[0];
    let frame = &small().drawings[0].sheet.frames[b.view.trim_start_matches("v:")];
    let position = [b.at[0] - frame.at[0], b.at[1] - frame.at[1] + 4.];
    let pinned: PackSpec = serde_json::from_value(json!({"ga":{
        "balloon_radius":7, "parts_list":false,
        "balloons":{b.item.to_string():{"view":b.view.trim_start_matches("v:"),"position":position}}
    }, "parts":{"plate":{"drawing":{"sheet":{"title":{"number":"PL-01"}}}}}}))
    .unwrap();
    let pinned = pack::build(&fixture("pack-small.step.gz"), &pinned).unwrap();
    let ga = pinned.report.general_assembly.as_ref().unwrap();
    assert!(ga.parts_list.is_none());
    let actual = ga.balloons.iter().find(|r| r.item == b.item).unwrap();
    let frame = &pinned.drawings[0].sheet.frames[actual.view.trim_start_matches("v:")];
    assert_eq!(
        actual.at,
        [frame.at[0] + position[0], frame.at[1] + position[1]]
    );
    assert_eq!(actual.radius, 7.);
    assert_eq!(pinned.report.rows[0].reference, "PL-01");
    for invalid in [
        json!({"ga":{"balloon_radius":1}}),
        json!({"ga":{"horizontal_axis":"q"}}),
        json!({"ga":{"section_item":999}}),
        json!({"ga":{"detail_scale":"3:1"}}),
        json!({"ga":{"hatching":{"1":{"angle":200}}}}),
        json!({"ga":{"balloons":{"999":{}}}}),
        json!({"ga":{"balloons":{"1":{"view":"rear"}}}}),
    ] {
        let spec = serde_json::from_value(invalid).unwrap();
        assert_eq!(
            pack::build(&fixture("pack-small.step.gz"), &spec)
                .err()
                .unwrap()
                .code,
            "invalid"
        );
    }
    let step = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/plate.step");
    assert!(
        pack::build(&step, &Default::default())
            .unwrap()
            .report
            .general_assembly
            .is_none()
    );
    let force: PackSpec = serde_json::from_value(json!({"ga":{"include":true}})).unwrap();
    let pack = pack::build(&step, &force).unwrap();
    assert!(pack.report.general_assembly.is_some());
    assert!(pack.report.rows[0].sheets.iter().all(|&s| s > 1));
}

#[test]
fn ga_reference_sizes_projection_and_generic_title() {
    let pack = small();
    let ga = pack.report.general_assembly.as_ref().unwrap();
    let drawing = &pack.drawings[0];
    let labels: Vec<_> = drawing
        .annotations
        .iter()
        .filter(|a| a.name.starts_with("d:envelope/"))
        .collect();
    assert_eq!(labels.len(), 3);
    let mut printed: Vec<_> = labels
        .iter()
        .flat_map(|label| label.runs.iter().map(|&i| drawing.text[i].text.as_str()))
        .collect();
    printed.sort();
    assert_eq!(printed, ["(22)", "(30)", "(42)"]);
    let front = ga.views.iter().find(|v| v.id == "v:front").unwrap();
    let plan = ga.views.iter().find(|v| v.id == "v:plan").unwrap();
    let camera = |v: &draftwright::model::PlannedView| {
        draftwright::haecceity::hlr::View::new(v.direction, v.up).unwrap()
    };
    assert_eq!(camera(front).x, camera(plan).x);
    assert!((front.at[0] - plan.at[0]).abs() < 1e-8);
    assert!(ga.envelope[0] >= ga.envelope[1] && ga.envelope[0] >= ga.envelope[2]);
    assert!(
        pack.report
            .rows
            .iter()
            .filter(|r| r.name == r.key)
            .all(|r| r.reference.is_empty())
    );
}

#[test]
fn ga_nested_items_have_a_section_and_readable_detail() {
    let step = fixture("pack-nested.step.gz");
    let pack = pack::build(&step, &PackSpec::default()).unwrap();
    check_ga(&pack, &step);
    let ga = pack.report.general_assembly.as_ref().unwrap();
    let section = ga
        .views
        .iter()
        .find(|v| v.id == "v:section_aa")
        .expect("nested assembly section");
    assert_eq!(section.cut.as_ref().unwrap().marked_in, "v:plan");
    let detail = ga
        .views
        .iter()
        .find(|v| v.id == "v:detail_b")
        .expect("0.25 mm liner detail");
    assert!(detail.scale > section.scale);
    let liner = pack.report.rows.iter().find(|r| r.name == "liner").unwrap();
    assert_eq!(
        ga.balloons
            .iter()
            .find(|b| b.item == liner.item)
            .unwrap()
            .view,
        "v:detail_b"
    );
    assert!(ga.unballooned.is_empty(), "{:?}", ga.unballooned);
    let drawing = &pack.drawings[0];
    assert!(drawing.text.iter().any(|r| r.text == "pack-nested"));
    assert!(drawing.text.iter().any(|r| r.text.starts_with("SECTION A")));
    assert!(drawing.text.iter().any(|r| r.text.starts_with("DETAIL B")));
    assert!(drawing.sheet.items.iter().any(|i| i.name == "section_line"));
    for (i, b) in ga.balloons.iter().enumerate() {
        for c in &ga.balloons[i + 1..] {
            assert!(b.view != c.view || (b.tip[0] - c.tip[0]).hypot(b.tip[1] - c.tip[1]) >= 3.);
        }
    }
    let hatch = drawing
        .layers
        .iter()
        .find(|l| l.name == "ga_hatch_v:detail_b")
        .unwrap();
    let centre = (detail.detail.as_ref().unwrap().bounds[0][0]
        + detail.detail.as_ref().unwrap().bounds[1][0])
        / 2.;
    let mut bush = 0;
    let mut liner_hatch = 0;
    for item in &hatch.items {
        if let draftwright::output::Item::Edge(draftwright::output::Curve::Line(p, q)) = item {
            let x = (centre + ((p[0] + q[0]) / 2. - detail.at[0]) / detail.scale).abs();
            let diagonal = (q[0] - p[0]) * (q[1] - p[1]);
            if x > 4. + 1e-5 && x < 5. - 1e-5 {
                assert!(diagonal < 0.);
                bush += 1;
            }
            if x > 3.75 + 1e-5 && x < 4. - 1e-5 {
                assert!(diagonal > 0.);
                liner_hatch += 1;
            }
        }
    }
    assert!(
        bush > 0 && liner_hatch > 0,
        "both adjacent cut items need distinct hatch directions"
    );
    let bush_item = pack
        .report
        .rows
        .iter()
        .find(|r| r.name == "bushing")
        .unwrap()
        .item;
    let overrides:PackSpec=serde_json::from_value(json!({"ga":{"section_item":liner.item,"detail_scale":"10:1","hatching":{
        bush_item.to_string():{"angle":30,"spacing":1,"omit":false},liner.item.to_string():{"omit":true}
    }}})).unwrap();
    let changed = pack::build(&step, &overrides).unwrap();
    let changed_ga = changed.report.general_assembly.as_ref().unwrap();
    let changed_detail = changed_ga
        .views
        .iter()
        .find(|v| v.id == "v:detail_b")
        .unwrap();
    assert_eq!(changed_detail.scale, 10.);
    assert!(
        changed_ga
            .balloons
            .iter()
            .any(|b| b.item == liner.item && b.view == "v:detail_b")
    );
    let centre = (changed_detail.detail.as_ref().unwrap().bounds[0][0]
        + changed_detail.detail.as_ref().unwrap().bounds[1][0])
        / 2.;
    let mut altered = 0;
    for item in &changed.drawings[0]
        .layers
        .iter()
        .find(|l| l.name == "ga_hatch_v:detail_b")
        .unwrap()
        .items
    {
        if let draftwright::output::Item::Edge(draftwright::output::Curve::Line(p, q)) = item {
            let x =
                (centre + ((p[0] + q[0]) / 2. - changed_detail.at[0]) / changed_detail.scale).abs();
            assert!(
                !(3.75 + 1e-5..4. - 1e-5).contains(&x),
                "suppressed liner hatch"
            );
            if x > 4. + 1e-5 && x < 5. - 1e-5 {
                assert!(((q[1] - p[1]) / (q[0] - p[0]) - 1. / 3f64.sqrt()).abs() < 1e-8);
                altered += 1;
            }
        }
    }
    assert!(altered > 0);
    let off: PackSpec =
        serde_json::from_value(json!({"ga":{"sections":false,"horizontal_axis":"y"}})).unwrap();
    let off = pack::build(&step, &off).unwrap();
    let ga = off.report.general_assembly.as_ref().unwrap();
    assert!(
        ga.views
            .iter()
            .all(|v| v.cut.is_none() && v.detail.is_none())
    );
    assert_eq!(ga.envelope[0], 40.);
}

#[test]
fn ga_item_hatch_override_survives_conventional_thread_rendering() {
    let spec: PackSpec = serde_json::from_value(json!({
        "parts": {"body_6": {"item":25,"supply":"make"}},
        "ga": {"section_item":25,"hatching":{"25":{"omit":false,"angle":17,"spacing":1.1}}}
    }))
    .unwrap();
    let pack = pack::build(&fixture("pack-shapes.step.gz"), &spec).unwrap();
    let ga = pack.report.general_assembly.as_ref().unwrap();
    assert!(ga.views.iter().any(|v| v.id == "v:section_aa"));
    let hatch = pack.drawings[0]
        .layers
        .iter()
        .flat_map(|l| &l.items)
        .filter_map(|item| match item {
            draftwright::output::Item::Edge(draftwright::output::Curve::Line(a, b)) => {
                let d = [b[0] - a[0], b[1] - a[1]];
                (d[0].hypot(d[1]) > 0.5).then_some(d[1].atan2(d[0]).to_degrees().rem_euclid(180.))
            }
            _ => None,
        })
        .filter(|angle| (*angle - 17.).abs() < 1e-5)
        .count();
    assert!(
        hatch >= 3,
        "the item's authored hatch angle must survive the thread carrier's section cache key"
    );
}

#[test]
fn bytes_and_path_builds_match_on_every_assembly_fixture() {
    let mut paths: Vec<_> = std::fs::read_dir(fixture(""))
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .filter(|path| path.to_string_lossy().ends_with(".step.gz"))
        .collect();
    paths.sort();
    assert!(!paths.is_empty());
    for path in paths {
        let name = path.display().to_string();
        let bytes = std::fs::read(&path).unwrap();
        let a = pack::build(&path, &PackSpec::default()).unwrap();
        let b = pack::build_bytes(&name, &bytes, &PackSpec::default()).unwrap();
        assert_eq!(
            serde_json::to_value(&a.report).unwrap(),
            serde_json::to_value(&b.report).unwrap(),
            "{name}"
        );
        assert_eq!(a.drawings.len(), b.drawings.len(), "{name}");
        assert_eq!(a.pdf(), b.pdf(), "{name}");
        assert_eq!(a.report.csv(), b.report.csv(), "{name}");
        for page in 1..=a.report.pages {
            assert_eq!(
                a.page_svg(page).unwrap(),
                b.page_svg(page).unwrap(),
                "{name}: {page}"
            );
        }
    }
}

#[test]
fn byte_render_and_cli_outputs_match_and_failed_rows_keep_keys() {
    let path = fixture("pack-small.step.gz");
    let bytes = std::fs::read(&path).unwrap();
    let (_, files) = pack::render_bytes(
        &path.display().to_string(),
        &bytes,
        &PackSpec::default(),
        &pack::FORMATS,
    )
    .unwrap();
    let dir =
        std::env::temp_dir().join(format!("draftwright-pack-bytes-cli-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_draftwright"))
        .arg("pack")
        .arg(&path)
        .arg("-o")
        .arg(&dir)
        .output()
        .unwrap();
    assert!(output.status.success(), "{:?}", output);
    for (format, bytes) in files {
        assert_eq!(
            bytes,
            std::fs::read(dir.join(format!("pack.{format}"))).unwrap(),
            "{format}"
        );
    }
    std::fs::remove_dir_all(dir).unwrap();
    let spec =
        serde_json::from_value(json!({"parts":{"plate":{"drawing":{"sheet":{"size":"INVALID"}}}}}))
            .unwrap();
    let pack = pack::build_bytes("pack-small.step.gz", &bytes, &spec).unwrap();
    let failed = pack.report.rows.iter().find(|r| r.error.is_some()).unwrap();
    assert_eq!(failed.key, "plate");
    assert!(failed.products.contains(&"plate".into()));
    assert!(
        pack.report
            .findings
            .iter()
            .filter(|f| !f.code.starts_with("pack_ga_failed"))
            .all(|f| !f.products.is_empty())
    );
}
