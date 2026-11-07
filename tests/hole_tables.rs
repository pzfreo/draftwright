//! Drop and escalate (the maintainer's decision of 2026-10-09; `src/hole_table.rs`): a hole
//! callout or location placed off the sheet is dropped and reported, and a callout or plan-view
//! location drop escalates to draftwright's plan-view hole table with balloons, under its gates.
//!
//! The reference is draftwright on the five parts the port dropped hole items on before the layout
//! reserved every view's bands (drawing quality 3; CTC-02 and cgb245 now drop none)
//! (`tests/fixtures/hole_tables.json`, written by `tools/capture_hole_tables.py`): the table rows
//! its escalation attempted, whether it kept the table, its balloons and its drop findings. On all
//! five draftwright keeps no table and draws no balloon: on NIST CTC-02 and CTC-04 it attempts a
//! table and withdraws it for want of balloon room; on the others none is warranted. Each part is
//! analysed with the port's own recognition, as the command line does. Every difference in the
//! rows is listed with its verdict in `tests/fixtures/known_hole_tables.json`.
//!
//! No corpus part keeps a table in draftwright or the port (CTC-02, the only corpus part whose plan
//! view has 16 holes outside a pattern, has no balloon room in either, even on A0), so the
//! committed table is checked on a plate of 18 scattered holes with one location reported off the
//! sheet as a stand-in: whole, on the sheet clear of the views and the title block, a balloon per
//! row, credited by lint, and the same bytes run to run.

#[path = "common/corpus.rs"]
mod corpus;

use std::collections::BTreeMap;
use std::path::Path;
use std::sync::OnceLock;

use draftwright::haecceity::Part;
use draftwright::hole_table::{self, HoleRow};
use draftwright::lint::structural;
use draftwright::model::{self, Analysis, Plan, Problem, Spec};
use draftwright::text::Font;
use serde_json::Value;

const PARTS: [(&str, &str); 5] = [
    ("ftc09", "nist/nist_ftc_09_asme1_rd.stp"),
    ("ftc08", "nist/nist_ftc_08_asme1_rc.stp"),
    ("ctc02", "nist/nist_ctc_02_asme1_rc.stp"),
    ("ctc04", "nist/nist_ctc_04_asme1_rd.stp"),
    ("cgb245", "cadgenbench_inputs/cgb245.step"),
];

fn fixture(name: &str) -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(name);
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

/// Part *name* read with the port's own recognition, once per test binary.
fn part(name: &str) -> Option<&'static (Part, Analysis)> {
    static PARTS_READ: OnceLock<std::sync::Mutex<BTreeMap<String, &'static (Part, Analysis)>>> =
        OnceLock::new();
    let dir = corpus::dir()?;
    let file = PARTS.iter().find(|(n, _)| *n == name).unwrap().1;
    let cache = PARTS_READ.get_or_init(Default::default);
    if let Some(p) = cache.lock().unwrap().get(name) {
        return Some(*p);
    }
    let read: &'static (Part, Analysis) =
        Box::leak(Box::new(model::analyse_native(&dir.join(file)).unwrap()));
    cache.lock().unwrap().insert(name.into(), read);
    Some(read)
}

fn font() -> Font {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf");
    Font::new(std::fs::read(path).unwrap()).unwrap()
}

fn codes(plan: &Plan) -> Vec<&str> {
    plan.problems.iter().map(|p| p.code.as_str()).collect()
}

/// Whether *d* is a hole callout or a hole location (what the escalation may drop).
fn hole_item(d: &draftwright::dims::PlannedDimension) -> bool {
    let hole = |f: &str| f.starts_with("f:hole:") || f.starts_with("f:hole_pattern:");
    !d.measurements.is_empty()
        && d.measurements.iter().all(|m| hole(&m.feature))
        && (d.kind == "callout" || d.measurements[0].parameter.starts_with("location.length."))
}

/// On each part: every hole item `model::plan` places off the sheet is unplaced after the
/// escalation and reported once with draftwright's code for it (a callout, a plan-view location,
/// or an off-axis location at info), its placement findings gone with its ink; nothing else is
/// unplaced; what stays off the sheet is no hole item. As in draftwright, no part keeps a table
/// or draws a balloon; CTC-02 attempts a table and withdraws it, said (`table_dropped`), and a
/// part whose plan view has fewer than 16 holes outside a pattern says why it has none. *drops*
/// says whether the part's plan places any hole item off the sheet: since the layout reserves
/// every view's bands (drawing quality 3), CTC-02's and cgb245's place none, so nothing of theirs
/// is dropped (draftwright drops on both for collisions or crowding, which the port places and
/// reports: `known_hole_tables.json`).
fn check_part(name: &str, drops: bool) {
    let Some((_, analysis)) = part(name) else {
        eprintln!("SKIPPED: the corpus is not found");
        return;
    };
    let python = &fixture("hole_tables.json")[name];
    let first = model::plan(analysis, &Spec::default()).unwrap();
    let mut escalated = first.clone();
    hole_table::escalate(analysis, &mut escalated, None);
    let off: Vec<&str> = first
        .problems
        .iter()
        .filter(|p| p.code == "annotation_off_sheet")
        .map(|p| p.subjects[0].as_str())
        .collect();
    let mut dropped = 0;
    for (before, after) in first.dimensions.iter().zip(&escalated.dimensions) {
        assert_eq!(before.id, after.id);
        let is_dropped = off.contains(&before.id.as_str()) && hole_item(before);
        assert_eq!(
            after.placed.is_none(),
            before.placed.is_none() || is_dropped,
            "{name}: {}",
            before.id
        );
        let said: Vec<&Problem> = escalated
            .problems
            .iter()
            .filter(|p| hole_table::DROPS.contains(&p.code.as_str()) && p.subjects[0] == before.id)
            .collect();
        if !is_dropped {
            assert!(said.is_empty(), "{name}: {}", before.id);
            continue;
        }
        dropped += 1;
        let (code, severity) = match (before.kind.as_str(), before.view.as_str()) {
            ("callout", _) => ("callout_dropped", "warning"),
            (_, "v:plan") => ("location_ref_dropped", "warning"),
            _ => ("off_axis_location_dropped", "info"),
        };
        assert_eq!(said.len(), 1, "{name}: {}", before.id);
        assert_eq!(
            (said[0].code.as_str(), said[0].severity.as_str()),
            (code, severity)
        );
        assert!(
            !escalated
                .problems
                .iter()
                .any(|p| p.code.starts_with("annotation_") && p.subjects.contains(&before.id)),
            "{name}: {} keeps a placement finding",
            before.id
        );
    }
    assert_eq!(dropped > 0, drops, "{name}: {dropped} dropped");
    for p in &escalated.problems {
        if p.code == "annotation_off_sheet" {
            let d = escalated.dimensions.iter().find(|d| d.id == p.subjects[0]);
            assert!(
                !d.is_some_and(hole_item),
                "{name}: {} stays off the sheet",
                p.subjects[0]
            );
        }
    }
    // draftwright keeps no table and draws no balloon on any of the five.
    assert!(!python["table_kept"].as_bool().unwrap() && python["balloons"] == Value::Array(vec![]));
    assert!(
        escalated.hole_table.is_none() && escalated.balloons.is_empty(),
        "{name}"
    );
    let attempted = codes(&escalated).contains(&"table_dropped");
    let rows = hole_table::table_rows(analysis, &first).len();
    let escalates = escalated
        .problems
        .iter()
        .any(|p| matches!(p.code.as_str(), "callout_dropped" | "location_ref_dropped"));
    assert_eq!(
        attempted,
        escalates && rows >= hole_table::MIN_HOLES,
        "{name}: {rows} rows"
    );
    if escalates && !attempted {
        assert!(
            escalated
                .problems
                .iter()
                .filter(|p| matches!(p.code.as_str(), "callout_dropped" | "location_ref_dropped"))
                .all(|p| p.message.contains("No hole table")),
            "{name}"
        );
    }
    // The verdict on whether the escalation is attempted, where draftwright's differs.
    let python_attempted = !python["attempted_tables"].as_array().unwrap().is_empty();
    if python_attempted != attempted {
        let known = &fixture("known_hole_tables.json")["attempted"][name];
        assert!(
            known["verdict"].is_string(),
            "{name}: draftwright attempts a table: {python_attempted}, the port: {attempted}; list it with a verdict"
        );
    }
}

#[test]
fn ftc09_keeps_every_hole_item_with_the_reserved_recess_lanes() {
    check_part("ftc09", false);
}

#[test]
fn ftc08_keeps_every_hole_item_with_the_reserved_recess_lanes() {
    check_part("ftc08", false);
}

#[test]
fn ctc02_has_no_hole_item_off_the_sheet_to_drop() {
    check_part("ctc02", false);
}

/// Its one off-sheet hole location (`d:f:hole_pattern:2/location.length.x`, 480) has had
/// a place since the items merged away stopped holding lanes of their own: two x locations that
/// fell through below the plan now fit above it, and it takes the first lane below. Nothing of
/// ctc04's is dropped either.
#[test]
fn ctc04_has_no_hole_item_off_the_sheet_to_drop() {
    check_part("ctc04", false);
}

#[test]
fn cgb245_has_no_hole_item_off_the_sheet_to_drop() {
    check_part("cgb245", false);
}

/// The rows: each the cells draftwright's escalation printed for the same hole (matched by its
/// X and Y), `TAG | ⌀ | DEPTH | X | Y`, the tags A, B, … in order; a row on one side only is a
/// hole the two recognitions read differently, listed with its verdict.
fn rows_match(name: &str) {
    let Some((_, analysis)) = part(name) else {
        eprintln!("SKIPPED: the corpus is not found");
        return;
    };
    let plan = model::plan(analysis, &Spec::default()).unwrap();
    let ours: Vec<HoleRow> = hole_table::table_rows(analysis, &plan);
    // All nominal cells have exact new-policy expectations. Then replay the independent
    // archived Python comparison at its historical display precision, retaining its row set,
    // mismatch ledger and minimum matched-row assertion unchanged.
    let expected = fixture("nominal_hole_table_rows.json");
    assert_eq!(
        serde_json::to_value(ours.iter().map(|r| &r.cells).collect::<Vec<_>>()).unwrap(),
        expected[name]
    );
    let ours: Vec<_> = ours
        .into_iter()
        .map(|mut row| {
            for cell in row.cells.iter_mut().skip(1) {
                let (prefix, value) = cell
                    .strip_prefix('ø')
                    .map_or(("", cell.as_str()), |v| ("ø", v));
                if let Ok(value) = value.parse::<f64>() {
                    let rounded = value.round();
                    *cell = if (value - rounded).abs() < 1e-6 {
                        format!("{prefix}{rounded}")
                    } else {
                        format!("{prefix}{value:.1}")
                    };
                }
            }
            row
        })
        .collect();
    let tags = hole_table::tags(ours.len());
    assert!(
        ours.iter()
            .zip(&tags)
            .all(|(r, t)| r.tag == *t && r.cells[0] == *t)
    );
    let python = &fixture("hole_tables.json")[name]["attempted_tables"][0];
    let theirs: Vec<Vec<String>> = python
        .as_array()
        .unwrap()
        .iter()
        .map(|r| {
            r.as_array()
                .unwrap()
                .iter()
                .map(|c| c.as_str().unwrap().to_string())
                .collect()
        })
        .collect();
    assert_eq!(theirs[0], hole_table::HEADER);
    let at = |cells: &[String]| (cells[3].clone(), cells[4].clone());
    let known = &fixture("known_hole_tables.json")["rows"][name];
    let listed = |side: &str, xy: &(String, String)| {
        known[side].as_array().is_some_and(|l| {
            l.iter()
                .any(|e| e["x"] == xy.0.as_str() && e["y"] == xy.1.as_str())
        }) && known["verdict"].is_string()
    };
    let mut matched = 0;
    for t in &theirs[1..] {
        match ours.iter().find(|r| at(&r.cells) == at(t)) {
            Some(r) => {
                assert_eq!(r.cells[1..], t[1..], "{name}: {}", r.feature);
                matched += 1;
            }
            None => assert!(
                listed("python_only", &at(t)),
                "{name}: draftwright's row {t:?} has no row here; list it with a verdict"
            ),
        }
    }
    for r in &ours {
        if !theirs[1..].iter().any(|t| at(t) == at(&r.cells)) {
            assert!(
                listed("rust_only", &at(&r.cells)),
                "{name}: row {:?} ({}) is not draftwright's; list it with a verdict",
                r.cells,
                r.feature
            );
        }
    }
    assert!(matched >= 10, "{name}: {matched} rows matched");
}

#[test]
fn table_rows_match_draftwrights_attempt_on_ctc02() {
    rows_match("ctc02");
}

#[test]
fn table_rows_match_draftwrights_attempt_on_ctc04() {
    rows_match("ctc04");
}

/// draftwright's transaction: on CTC-02's own sheet not every row's balloon finds room beside the
/// plan view, so the table is withdrawn whole: no table, no row balloon, every item it would
/// replace restored (only the dropped items unplaced), the drops still reported and the failure
/// said (`table_dropped`), as draftwright's `table_dropped`/`balloon_dropped` on the same part.
/// Since the layout reserves every view's bands (drawing quality 3) CTC-02's plan places no hole
/// item off the sheet, so its first plan-view callout of a hole outside a pattern is reported off
/// the sheet as a stand-in for draftwright's dropped callouts (a pattern callout's drop would
/// bring its own balloon).
#[test]
fn a_table_without_every_balloon_is_withdrawn_whole() {
    let Some((_, analysis)) = part("ctc02") else {
        eprintln!("SKIPPED: the corpus is not found");
        return;
    };
    let mut first = model::plan(analysis, &Spec::default()).unwrap();
    let id = first
        .dimensions
        .iter()
        .find(|d| {
            d.view == "v:plan"
                && d.kind == "callout"
                && d.placed.is_some()
                && d.id.starts_with("d:f:hole:")
        })
        .unwrap()
        .id
        .clone();
    stand_in_off_sheet(&mut first, id);
    let mut p = first.clone();
    hole_table::escalate(analysis, &mut p, None);
    assert!(p.hole_table.is_none() && p.balloons.is_empty());
    let unplaced: Vec<&str> = p
        .dimensions
        .iter()
        .zip(&first.dimensions)
        .filter(|(a, b)| a.placed.is_none() && b.placed.is_some())
        .map(|(a, _)| a.id.as_str())
        .collect();
    let drops: Vec<&str> = p
        .problems
        .iter()
        .filter(|q| hole_table::DROPS.contains(&q.code.as_str()))
        .map(|q| q.subjects[0].as_str())
        .collect();
    assert_eq!(unplaced, drops);
    assert!(!drops.is_empty());
    let said = p
        .problems
        .iter()
        .find(|q| q.code == "table_dropped")
        .unwrap();
    assert!(said.message.contains("balloon"), "{}", said.message);
    let python = &fixture("hole_tables.json")["ctc02"]["findings"];
    assert!(
        python
            .as_array()
            .unwrap()
            .iter()
            .any(|f| f[0] == "table_dropped")
    );
}

/// The scattered-holes plate (`tests/fixtures/parts/scattered_holes.step`, written by
/// `tools/make_scattered_holes.py`: 18 through holes of three sizes in no pattern), its plan-view
/// location `d:f:hole:1/location.length.x` reported off the sheet as a stand-in: the plate
/// has room, so the table is kept whole.
fn forced() -> (&'static (Part, Analysis), Plan) {
    static READ: OnceLock<(Part, Analysis)> = OnceLock::new();
    let read = READ.get_or_init(|| {
        let path =
            Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/scattered_holes.step");
        model::analyse_native(&path).unwrap()
    });
    let mut p = model::plan(&read.1, &Spec::default()).unwrap();
    let id = p
        .dimensions
        .iter()
        .find(|d| d.view == "v:plan" && d.id.ends_with("/location.length.x"))
        .unwrap()
        .id
        .clone();
    stand_in_off_sheet(&mut p, id);
    hole_table::escalate(&read.1, &mut p, None);
    (read, p)
}

/// Reports item *id* of *plan* off the sheet, as placement would (a stand-in drop).
fn stand_in_off_sheet(plan: &mut Plan, id: String) {
    plan.problems.push(Problem {
        id: format!("p:annotation_off_sheet:{id}"),
        code: "annotation_off_sheet".into(),
        severity: "error".into(),
        message: "stand-in".into(),
        subjects: vec![id],
        ..Default::default()
    });
}

fn inside(b: [f64; 4], r: [f64; 4]) -> bool {
    b[0] >= r[0] && b[1] >= r[1] && b[2] <= r[2] && b[3] <= r[3]
}

fn overlap(a: [f64; 4], b: [f64; 4]) -> bool {
    a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3]
}

/// The committed table (draftwright's `_maybe_tabulate_holes_impl`): one row per hole outside a
/// pattern, as `table_rows` gives them; one certifying balloon per row, owning its hole, each on
/// the sheet and clear of the others; every block on the sheet, clear of every view's area (the
/// isometric's drawn box) and the title block; the replaced callouts and locations unplaced with their basis saying so, the
/// stand-in drop cleared by its row (`hole_table_escalated`). Drawn, structural lint finds the
/// table and the balloons nowhere off the sheet or over another label, and hole coverage credits
/// the rows: no tabulated hole lacks its diameter, THRU or depth, or its count.
#[test]
fn an_escalated_table_is_whole_on_the_sheet_with_a_balloon_per_row() {
    let ((part, analysis), p) = forced();
    let t = p.hole_table.as_ref().unwrap_or_else(|| {
        panic!(
            "{:?}",
            p.problems
                .iter()
                .filter(|q| q.code.contains("table") || q.code.contains("balloon"))
                .map(|q| q.message.clone())
                .collect::<Vec<_>>()
        )
    });
    let first = model::plan(analysis, &p.spec).unwrap();
    assert_eq!(t.rows, hole_table::table_rows(analysis, &first));
    assert_eq!(p.balloons.len(), t.rows.len());
    for (b, r) in p.balloons.iter().zip(&t.rows) {
        assert!(b.certifying && b.tag == r.tag && b.features == [r.feature.clone()]);
        assert_eq!(b.id, format!("balloon_plan_{}_0", r.tag));
    }
    let [w, h] = p.sheet.page;
    let drawable = [10.0, 10.0, w - 10.0, h - 10.0];
    let rings: Vec<[f64; 4]> = p
        .balloons
        .iter()
        .map(|b| {
            [
                b.at[0] - b.radius,
                b.at[1] - b.radius,
                b.at[0] + b.radius,
                b.at[1] + b.radius,
            ]
        })
        .collect();
    for (i, r) in rings.iter().enumerate() {
        assert!(inside(*r, drawable));
        assert!(rings[i + 1..].iter().all(|q| !overlap(*r, *q)));
    }
    for block in &t.blocks {
        let cells = block.placed.as_ref().unwrap().cells;
        assert!(inside(cells, drawable), "{cells:?}");
        assert!(!overlap(cells, p.sheet.title_block));
        // Each view's area; the isometric's drawn box, as the spec's tables keep clear of it.
        for v in &p.views {
            let ib = analysis.iso_bounds;
            let area = if v.id == "v:iso" {
                [
                    v.at[0] + v.scale * ib[0],
                    v.at[1] + v.scale * ib[1],
                    v.at[0] + v.scale * ib[2],
                    v.at[1] + v.scale * ib[3],
                ]
            } else {
                v.area
            };
            assert!(!overlap(cells, area), "{} over {}", block.id, v.id);
        }
    }
    let replaced: Vec<&str> = p
        .dimensions
        .iter()
        .filter(|d| d.basis.contains(hole_table::ID))
        .map(|d| d.id.as_str())
        .collect();
    assert!(replaced.iter().any(|d| d.ends_with("/location.length.x")));
    assert!(
        p.dimensions
            .iter()
            .filter(|d| d.basis.contains(hole_table::ID))
            .all(|d| d.placed.is_none())
    );
    assert!(codes(&p).contains(&"hole_table_escalated"));
    assert!(!codes(&p).contains(&"location_ref_dropped"));

    let drawing = model::render(part, analysis, &p, &font()).unwrap();
    for i in structural::check(&drawing) {
        let ours = |n: &str| n.starts_with(hole_table::ID) || n.starts_with("balloon_");
        let named = i.annotation.as_deref().is_some_and(ours) || i.related.iter().any(|n| ours(n));
        assert!(
            !(named
                && matches!(
                    i.code.as_str(),
                    "annotation_out_of_bounds" | "annotation_overlap" | "label_centerline_overlap"
                )),
            "{}: {}",
            i.code,
            i.message
        );
    }
    let tabulated: Vec<&str> = t.rows.iter().map(|r| r.feature.as_str()).collect();
    for i in draftwright::lint::lint(analysis, &drawing) {
        if i.code == "hole_requirement_missing" || i.code == "hole_requirement_unverifiable" {
            assert!(
                !i.subjects.iter().any(|s| tabulated.contains(&s.as_str())),
                "{}: {}",
                i.code,
                i.message
            );
        }
    }
}

/// The escalation is a function of the plan: the same plan, the same table and balloons, and the
/// same drawing bytes, run to run.
#[test]
fn the_escalation_and_its_drawing_are_deterministic() {
    let ((part, analysis), p) = forced();
    let (_, q) = forced();
    assert_eq!(
        serde_json::to_string(&p).unwrap(),
        serde_json::to_string(&q).unwrap()
    );
    let font = font();
    let a = model::render(part, analysis, &p, &font).unwrap();
    let b = model::render(part, analysis, &q, &font).unwrap();
    assert_eq!(a.svg(), b.svg());
    assert!(a.text.iter().any(|r| r.text == "TAG") && a.text.iter().any(|r| r.text == "A"));
}

#[test]
fn an_off_axis_pin_does_not_suppress_a_plan_view_table_attempt() {
    let Some((_, analysis)) = part("ctc02") else {
        return;
    };
    let mut p = model::plan(analysis, &Spec::default()).unwrap();
    let held = p
        .dimensions
        .iter()
        .find(|d| d.view != "v:plan" && hole_item(d) && d.placed.is_some())
        .unwrap()
        .clone();
    p.spec = model::edit(&p.spec, &serde_json::json!([{"op":"set", "path":format!("dimensions/{}/place", draftwright::items::key(&held.id)), "value":{"pinned":true}}])).unwrap();
    let dropped = p
        .dimensions
        .iter()
        .find(|d| d.view == "v:plan" && d.kind == "callout" && d.id.starts_with("d:f:hole:"))
        .unwrap()
        .id
        .clone();
    stand_in_off_sheet(&mut p, dropped);
    hole_table::escalate(analysis, &mut p, None);
    assert!(
        p.problems.iter().any(|p| p.code == "table_dropped"),
        "the table must be attempted before its balloon failure is reported"
    );
    assert_eq!(
        p.dimensions
            .iter()
            .find(|d| d.id == held.id)
            .unwrap()
            .placed,
        held.placed
    );
}

/// Required leaders surrounding the hole field make every perimeter balloon unsafe.
#[test]
fn retained_leader_crossings_roll_back_the_whole_table_and_restore_fallbacks() {
    let ((_, analysis), successful) = forced();
    assert!(successful.hole_table.is_some());
    let mut p = model::plan(analysis, &Spec::default()).unwrap();
    let v = p.views.iter().find(|v| v.id == "v:plan").unwrap();
    let width = analysis.envelope[1][0] - analysis.envelope[0][0];
    let height = analysis.envelope[1][1] - analysis.envelope[0][1];
    let [x, y] = v.at;
    let [w, h] = [width * v.scale * 0.5 + 1.0, height * v.scale * 0.5 + 1.0];
    let corners = [
        [x - w, y - h],
        [x + w, y - h],
        [x + w, y + h],
        [x - w, y + h],
    ];
    let seed = p
        .dimensions
        .iter()
        .find(|d| d.placed.is_some())
        .unwrap()
        .clone();
    for i in 0..4 {
        let mut retained = seed.clone();
        retained.id = format!("d:retained-leader:{i}");
        retained.view = "v:plan".into();
        retained.kind = "callout".into();
        retained.measurements.clear();
        let ink = retained.placed.as_mut().unwrap();
        ink.lines = vec![[corners[i], corners[(i + 1) % 4]]];
        ink.arrows.clear();
        p.dimensions.push(retained);
    }
    let dropped = p
        .dimensions
        .iter()
        .find(|d| d.id.ends_with("/location.length.x") && d.view == "v:plan")
        .unwrap()
        .id
        .clone();
    stand_in_off_sheet(&mut p, dropped.clone());
    let before = p.dimensions.clone();
    let original_drops: Vec<_> = p
        .problems
        .iter()
        .filter(|problem| problem.code == "annotation_off_sheet" && problem.severity != "info")
        .filter_map(|problem| problem.subjects.first().cloned())
        .collect();
    hole_table::escalate(analysis, &mut p, None);
    assert!(p.hole_table.is_none() && p.balloons.is_empty());
    assert!(codes(&p).contains(&"table_dropped"));
    assert!(codes(&p).contains(&"balloon_dropped"));
    for original in &before {
        let restored = p.dimensions.iter().find(|d| d.id == original.id).unwrap();
        if original_drops.contains(&original.id) {
            assert!(restored.placed.is_none());
            assert!(
                p.problems
                    .iter()
                    .any(|problem| hole_table::DROPS.contains(&problem.code.as_str())
                        && problem.subjects.contains(&original.id))
            );
        } else {
            assert_eq!(restored, original, "{}", original.id);
        }
    }
}

/// Larger annotation budgets must not weaken the audit when the author really
/// asks for a sheet that cannot carry the hole items.
#[test]
fn a_starved_dense_sheet_reports_every_dropped_hole_item() {
    for name in ["ftc08", "ftc09"] {
        let Some((_, analysis)) = part(name) else {
            return;
        };
        let spec: Spec = serde_json::from_value(serde_json::json!({
            "sheet": {"size": "A4", "scale": "1:1"}, "hole_table": "never"
        }))
        .unwrap();
        let first = model::plan(analysis, &spec).unwrap();
        let off: Vec<_> = first
            .dimensions
            .iter()
            .filter(|d| {
                hole_item(d)
                    && first.problems.iter().any(|p| {
                        p.code == "annotation_off_sheet" && p.subjects.first() == Some(&d.id)
                    })
            })
            .map(|d| d.id.clone())
            .collect();
        assert!(
            !off.is_empty(),
            "{name}: the explicit sheet must starve hole items"
        );
        let mut escalated = first.clone();
        hole_table::escalate(analysis, &mut escalated, None);
        for (before, after) in first.dimensions.iter().zip(&escalated.dimensions) {
            assert_eq!(before.id, after.id);
            assert_eq!(
                after.placed.is_none(),
                before.placed.is_none() || off.contains(&before.id)
            );
            let reports = escalated
                .problems
                .iter()
                .filter(|p| {
                    hole_table::DROPS.contains(&p.code.as_str())
                        && p.subjects.first() == Some(&before.id)
                })
                .count();
            assert_eq!(
                reports,
                usize::from(off.contains(&before.id)),
                "{name}: {}",
                before.id
            );
        }
    }
}

#[test]
fn balloon_leaders_do_not_cross_the_drawings_dimension_or_extension_lines() {
    let (_, p) = forced();
    assert!(
        p.hole_table.is_some(),
        "the scenario must retain its whole table"
    );
    let cross = |a: [[f64; 2]; 2], b: [[f64; 2]; 2]| {
        let side = |p: [f64; 2], q: [f64; 2], r: [f64; 2]| {
            (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        };
        side(a[0], a[1], b[0]) * side(a[0], a[1], b[1]) < 0.
            && side(b[0], b[1], a[0]) * side(b[0], b[1], a[1]) < 0.
    };
    for balloon in &p.balloons {
        if let Some(leader) = balloon.leader {
            for d in &p.dimensions {
                if let Some(placed) = &d.placed {
                    assert!(
                        !placed.lines.iter().any(|l| cross(leader, *l)),
                        "{} crosses {}",
                        balloon.id,
                        d.id
                    );
                }
            }
        }
    }
}
