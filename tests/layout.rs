//! The layout arithmetic against draftwright's own: every sheet-layout call recorded from Python
//! builds of the sample (`tools/capture_layout.py`, `tests/fixtures/layout/`) is replayed through
//! `layout::choose` and `layout::geometry`, and must give the same scale, sheet and arrangement,
//! the same view centres and isometric rectangle, and the same fit verdicts, except where the
//! port departs on purpose: each such result is listed, with the port's value, a verdict and the
//! evidence, in `tests/fixtures/known_layout.json`; a listed result must give exactly the value
//! recorded there, and a listed one that agrees again must come off the list.

use std::collections::BTreeMap;
use std::io::Read as _;
use std::path::Path;

use draftwright::layout::{
    self, Arrangement, Block, Corner, Margins, Measured, Principals, Request, Scaled, Strips,
};
use serde::Deserialize;
use serde_json::Value;

/// A recorded call whose result the port departs from on purpose (`tests/fixtures/
/// known_layout.json`): the fixture, the call's index, the port's value of each result key that
/// differs (`pick` for a `choose_scale` call's scale, sheet, title block width and arrangement),
/// a verdict and the evidence.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Known {
    fixture: String,
    call: usize,
    rust: BTreeMap<String, Value>,
    verdict: String,
    reason: String,
}

fn fixtures() -> Vec<(String, Value)> {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/layout");
    let mut names: Vec<_> = std::fs::read_dir(&dir)
        .unwrap()
        .map(|e| e.unwrap().path())
        .filter(|p| p.to_string_lossy().ends_with(".json.gz"))
        .collect();
    names.sort();
    names
        .into_iter()
        .map(|p| {
            let mut text = String::new();
            flate2::read::GzDecoder::new(std::fs::File::open(&p).unwrap())
                .read_to_string(&mut text)
                .unwrap();
            let v: Value = serde_json::from_str(&text).unwrap();
            assert_eq!(v["schema"], "draftwright.layout-calls/1");
            let name = p
                .file_name()
                .unwrap()
                .to_string_lossy()
                .replace(".json.gz", "");
            (name, v)
        })
        .collect()
}

fn f(v: &Value) -> f64 {
    v.as_f64().unwrap_or_else(|| panic!("not a number: {v}"))
}

fn margins(v: &Value) -> Option<Margins> {
    v.as_array().map(|m| Margins {
        left: f(&m[0]),
        right: f(&m[1]),
        top: f(&m[2]),
        bottom: f(&m[3]),
    })
}

fn arrangement(name: &str) -> Arrangement {
    match name {
        "columns" => Arrangement::Columns,
        "stacked-iso" => Arrangement::StackedIso,
        "staggered-side" => Arrangement::StaggeredSide,
        other => panic!("arrangement {other}"),
    }
}

fn corner(name: &str) -> Corner {
    match name {
        "bl" => Corner::BottomLeft,
        "br" => Corner::BottomRight,
        "tl" => Corner::TopLeft,
        "tr" => Corner::TopRight,
        other => panic!("corner {other}"),
    }
}

fn request(c: &Value) -> Request {
    let s = &c["strips"];
    assert!(s.is_object(), "every recorded call carries strip depths");
    let views = c["views"].as_array().map_or(Principals::ALL, |v| {
        let has = |n: &str| v.iter().any(|x| x == n);
        assert!(!has("rear"), "the rear view is not ported");
        Principals {
            front: has("front"),
            plan: has("plan"),
            side: has("side"),
        }
    });
    assert_eq!(c["convention"], "third", "first angle is not ported");
    Request {
        max_page: None,
        automatic_pair: false,
        candidate_arrangement: None,
        aligned: Vec::new(),
        breaks: Vec::new(),
        size: [f(&c["size"][0]), f(&c["size"][1]), f(&c["size"][2])],
        strips: Strips {
            right: f(&s["right"]),
            left: f(&s["left"]),
            top: f(&s["top"]),
            pv_halo: f(&s["pv_halo"]),
            fv_top: f(&s["fv_top"]),
            fv_bottom: f(&s["fv_bottom"]),
            front_hole_below: f(&s["front_hole_below"]),
            pv_authored_top: f(&s["pv_authored_top"]),
            pv_bottom: f(&s["pv_bottom"]),
            sv_top: f(&s["sv_top"]),
            sv_bottom: f(&s["sv_bottom"]),
            sv_right: f(&s["sv_right"]),
            // draftwright has no band left of the side view (only the port's GD&T sets it).
            sv_left: 0.0,
            side_ladder: s["side_ladder"].as_u64().unwrap() as usize,
            plan_demand: s["plan_demand"].as_bool().unwrap(),
            side_step_y: s["side_step_y"].as_bool().unwrap(),
        },
        sections: c["section"].as_u64().unwrap() as usize,
        section_row_fixed: false,
        separate_section: false,
        table_sizes: c["table_sizes"]
            .as_array()
            .unwrap()
            .iter()
            .map(|t| [f(&t[0]), f(&t[1])])
            .collect(),
        required_tables: c["required_tables"]
            .as_array()
            .unwrap()
            .iter()
            .map(|t| ([f(&t[0][0]), f(&t[0][1])], corner(t[1].as_str().unwrap())))
            .collect(),
        margins: margins(&c["margin"]).unwrap(),
        // draftwright's default title block sits 1 mm inside the frame; the port's is flush
        // with it (ISO 7200), so the replay pins draftwright's.
        title_block_margins: margins(&c["title_block_margins"]).or(Some(Margins {
            right: layout::FRAME + 1.0,
            bottom: layout::FRAME + 1.0,
            ..Margins::uniform(layout::FRAME)
        })),
        views,
        include_iso: c["include_iso"].as_bool().unwrap(),
        iso_scale_factor: c["iso_scale_factor"].as_f64(),
        view_gutters: c["view_gutters"].as_bool().unwrap(),
        // The recorded calls are all third angle (asserted above).
        first_angle: false,
    }
}

/// What depends on the scale, as recorded at each standard scale (keyed by Python's repr).
fn scaled(c: &Value, derived: &Value, scale: f64) -> Scaled {
    let at = |m: &Value| -> Option<Value> {
        m.as_object()?
            .iter()
            .find(|(k, _)| k.parse::<f64>().unwrap() == scale)
            .map(|(_, v)| v.clone())
    };
    let angular = at(&c["strips"]["angular"])
        .unwrap_or_else(|| panic!("no angular reservations recorded at scale {scale}"));
    let derived = match derived {
        Value::Array(_) => derived.clone(),
        Value::Null => Value::Array(vec![]),
        m => at(m).unwrap_or_else(|| panic!("no derived views recorded at scale {scale}")),
    };
    Scaled {
        angular: angular
            .as_array()
            .unwrap()
            .iter()
            .map(|a| {
                let b = &a[1];
                (
                    a[0].as_str().unwrap().to_string(),
                    [f(&b[0]), f(&b[1]), f(&b[2]), f(&b[3])],
                )
            })
            .collect(),
        derived: derived
            .as_array()
            .unwrap()
            .iter()
            .map(|d| (d[0].as_str().unwrap().to_string(), f(&d[1]), f(&d[2])))
            .collect(),
    }
}

fn page(v: &Value) -> Option<[f64; 2]> {
    match v {
        Value::Null => None,
        Value::Array(p) => Some([f(&p[0]), f(&p[1])]),
        Value::String(name) => layout::PAGES
            .iter()
            .find(|p| p.0 == name.to_uppercase())
            .map(|p| [p.1, p.2])
            .or_else(|| panic!("page {name}")),
        other => panic!("page {other}"),
    }
}

fn close(a: f64, b: f64) -> bool {
    (a - b).abs() <= 1e-9 * (1.0 + b.abs())
}

#[test]
fn layout_calls_match_draftwright() {
    let all = fixtures();
    assert!(all.len() >= 10, "the layout fixtures are captured");
    let (mut choices, mut layouts) = (0, 0);
    // The port's value of every result that differs from draftwright's.
    let mut differ: BTreeMap<(String, usize, String), Value> = BTreeMap::new();
    for (name, fixture) in &all {
        for (i, c) in fixture["calls"].as_array().unwrap().iter().enumerate() {
            let at = format!("{name} call {i} ({})", c["call"]);
            let req = request(c);
            match c["call"].as_str().unwrap() {
                "choose_scale" => {
                    let arrangements: Vec<Arrangement> = c["arrangements"]
                        .as_array()
                        .map(|a| a.iter().map(|x| arrangement(x.as_str().unwrap())).collect())
                        .unwrap_or(layout::AUTOMATIC_ARRANGEMENTS.to_vec());
                    let pick = layout::choose(
                        &req,
                        &|s| scaled(c, &c["derived"], s),
                        c["scale"].as_f64(),
                        page(&c["page"]),
                        &arrangements,
                        c["title_block_width"].as_f64(),
                    );
                    let r = c["result"].as_array().unwrap();
                    if !(close(pick.scale, f(&r[0]))
                        && pick.page == [f(&r[1]), f(&r[2])]
                        && pick.tb_w == f(&r[3])
                        && pick.arrangement.name() == r[4])
                    {
                        let ours = serde_json::json!([
                            pick.scale,
                            pick.page[0],
                            pick.page[1],
                            pick.tb_w,
                            pick.arrangement.name()
                        ]);
                        differ.insert((name.clone(), i, "pick".into()), ours);
                    }
                    choices += 1;
                }
                "layout_geometry" => {
                    let scale = f(&c["scale"]);
                    let measured = c["blocks"].as_object().map(|b| {
                        let block = |n: &str| {
                            b.get(n).map(|v| Block {
                                hw: f(&v[0]),
                                hh: f(&v[1]),
                                top: f(&v[2]),
                                right: f(&v[3]),
                                bottom: f(&v[4]),
                                left: f(&v[5]),
                            })
                        };
                        assert!(!b.contains_key("rear"), "the rear view is not ported");
                        Measured {
                            front: block("front"),
                            plan: block("plan"),
                            side: block("side"),
                        }
                    });
                    let g = layout::geometry(
                        &req,
                        &scaled(c, &c["derived_view_footprints"], scale),
                        scale,
                        page(&c["page"]).unwrap(),
                        f(&c["tb_w"]),
                        arrangement(c["arrangement"].as_str().unwrap()),
                        measured.as_ref(),
                    );
                    let r = &c["result"];
                    let numbers = [
                        ("FV_X", g.front[0]),
                        ("FV_Y", g.front[1]),
                        ("PV_X", g.plan[0]),
                        ("PV_Y", g.plan[1]),
                        ("SV_X", g.side[0]),
                        ("SV_Y", g.side[1]),
                        ("SECTION_X", g.section[0]),
                        ("SECTION_Y", g.section[1]),
                        ("iso_left", g.iso[0]),
                        ("iso_bottom", g.iso[1]),
                        ("iso_right", g.iso[2]),
                        ("iso_top", g.iso[3]),
                        ("ISO_X", g.iso_centre[0]),
                        ("ISO_Y", g.iso_centre[1]),
                        ("vertical_gutter", g.vertical_gutter),
                        ("side_gutter", g.side_gutter),
                        ("x_offset", g.x_offset),
                    ];
                    for (key, ours) in numbers {
                        if !close(ours, f(&r[key])) {
                            differ.insert((name.clone(), i, key.to_string()), ours.into());
                        }
                    }
                    let flags = [
                        ("iso_valid", g.iso_valid),
                        ("iso_fits", g.iso_fits),
                        ("fits", g.fits),
                        ("auto_fits", g.auto_fits),
                    ];
                    for (key, ours) in flags {
                        if Some(ours) != r[key].as_bool() {
                            differ.insert((name.clone(), i, key.to_string()), ours.into());
                        }
                    }
                    assert_eq!(g.arrangement.name(), r["arrangement"], "{at}");
                    layouts += 1;
                }
                other => panic!("{at}: unknown call {other}"),
            }
        }
    }
    assert!(
        choices > 20 && layouts > 20,
        "{choices} choices, {layouts} layouts"
    );
    let known: Vec<Known> = serde_json::from_str(
        &std::fs::read_to_string(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/known_layout.json"),
        )
        .unwrap(),
    )
    .unwrap();
    let mut listed = BTreeMap::new();
    for k in &known {
        assert!(
            ["rust-correct", "equivalent", "undetermined"].contains(&k.verdict.as_str())
                && !k.reason.is_empty()
                && !k.rust.is_empty(),
            "{k:?}"
        );
        for (key, value) in &k.rust {
            assert!(
                listed
                    .insert((k.fixture.clone(), k.call, key.clone()), value.clone())
                    .is_none(),
                "{} call {} {key} is listed twice",
                k.fixture,
                k.call
            );
        }
    }
    let unlisted: Vec<_> = differ.keys().filter(|k| !listed.contains_key(*k)).collect();
    let stale: Vec<_> = listed.keys().filter(|k| !differ.contains_key(*k)).collect();
    assert!(
        unlisted.is_empty() && stale.is_empty(),
        "differences not in known_layout.json: {unlisted:#?}\nlisted but no longer differing: {stale:#?}"
    );
    // A listed result gives the port's recorded value, not merely some other value than Python's.
    let same = |a: &Value, b: &Value| match (a, b) {
        (Value::Number(x), Value::Number(y)) => close(x.as_f64().unwrap(), y.as_f64().unwrap()),
        (Value::Array(x), Value::Array(y)) => {
            x.len() == y.len()
                && x.iter().zip(y).all(|(p, q)| match (p, q) {
                    (Value::Number(m), Value::Number(n)) => {
                        close(m.as_f64().unwrap(), n.as_f64().unwrap())
                    }
                    _ => p == q,
                })
        }
        _ => a == b,
    };
    let moved: Vec<_> = differ
        .iter()
        .filter(|(k, ours)| !same(ours, &listed[*k]))
        .map(|(k, ours)| format!("{k:?}: {ours} (known_layout.json: {})", listed[k]))
        .collect();
    assert!(moved.is_empty(), "listed results that moved: {moved:#?}");
}

/// Paths the sample's builds never take, against values computed by draftwright's own
/// `choose_scale` and `_layout_geometry` for the same inputs.
#[test]
fn unsampled_paths_match_draftwright() {
    // draftwright's own arithmetic places views against its 10 mm frame, without the frame
    // gutter the port keeps (2026-10-25).
    let req = |size| {
        let mut r = Request::new(size, Principals::ALL, true);
        r.margins = draftwright::layout::Margins::uniform(layout::FRAME);
        r
    };
    let none = |_: f64| Scaled::default();
    let auto = layout::AUTOMATIC_ARRANGEMENTS;
    // Too large for every standard scale: the largest scale that fits A0, by bisection.
    let p = layout::choose(&req([5e7, 2e7, 1e7]), &none, None, None, &auto, None);
    assert_eq!(p.scale, 1.0376190476190478e-05);
    assert_eq!((p.page, p.tb_w), ([1189.0, 841.0], 150.0));
    assert_eq!(p.fallback, Some(layout::Fallback::Computed));
    // A pinned sheet and a pinned scale.
    let p = layout::choose(
        &req([80.0, 50.0, 30.0]),
        &none,
        None,
        Some([420.0, 297.0]),
        &auto,
        None,
    );
    assert_eq!(
        (p.scale, p.page, p.arrangement),
        (1.0, [420.0, 297.0], Arrangement::Columns)
    );
    let p = layout::choose(
        &req([80.0, 50.0, 30.0]),
        &none,
        Some(5.0),
        None,
        &auto,
        None,
    );
    assert_eq!((p.scale, p.page), (5.0, [841.0, 594.0]));
    // A required table, and an optional one.
    let mut r = req([80.0, 50.0, 30.0]);
    r.required_tables = vec![([60.0, 30.0], Corner::TopRight)];
    r.table_sizes = vec![[40.0, 20.0]];
    let g = layout::geometry(
        &r,
        &none(1.0),
        1.0,
        [297.0, 210.0],
        120.0,
        Arrangement::Columns,
        None,
    );
    assert_eq!((g.front, g.side[0]), ([70.0, 70.0], 155.0));
    assert_eq!(g.iso, [130.0, 105.0, 227.0, 200.0]);
    assert_eq!((g.fits, g.auto_fits), (false, true));
    r.required_tables = vec![([260.0, 30.0], Corner::TopRight)];
    r.table_sizes.clear();
    let g = layout::geometry(
        &r,
        &none(1.0),
        1.0,
        [297.0, 210.0],
        120.0,
        Arrangement::Columns,
        None,
    );
    assert_eq!((g.fits, g.auto_fits), (false, false));
}

/// A rectangle with no room gives no scale to the transform: the isometric stays at sheet scale
/// (draftwright #1395), and one within 5 % of it is left alone.
#[test]
fn a_degenerate_iso_rectangle_leaves_the_sheet_scale() {
    let bounds = [-10.0, -8.0, 10.0, 8.0];
    let sliver = [100.0, 50.0, 236.0, 50.0 + 1.4e-14];
    assert_eq!(layout::iso_factor(sliver, [168.0, 50.0], bounds), 1.0);
    let inverted = [100.0, 40.0, 90.0, 60.0];
    assert_eq!(layout::iso_factor(inverted, [95.0, 50.0], bounds), 1.0);
    assert_eq!(
        layout::iso_factor([0.0, 0.0, 21.0, 17.0], [10.5, 8.5], bounds),
        1.0
    );
    // Room to spare: grow to 90 % of the rectangle, at most 1.3.
    assert_eq!(
        layout::iso_factor([0.0, 0.0, 100.0, 100.0], [50.0, 50.0], bounds),
        1.3
    );
    assert_eq!(
        layout::iso_factor([0.0, 0.0, 24.0, 40.0], [12.0, 20.0], bounds),
        1.08
    );
    // Too little: shrink to fit with 2 % to spare.
    assert_eq!(
        layout::iso_factor([0.0, 0.0, 10.0, 40.0], [5.0, 20.0], bounds),
        0.49
    );
}

#[test]
fn internally_raised_readability_scale_still_obeys_automatic_paper_cap() {
    let mut req = Request::new([1000.0; 3], draftwright::layout::Principals::ALL, false);
    req.max_page = Some(1);
    let pick = draftwright::layout::choose(
        &req,
        &|_| draftwright::layout::Scaled::default(),
        Some(1.0),
        None,
        &draftwright::layout::AUTOMATIC_ARRANGEMENTS,
        None,
    );
    assert_eq!(pick.page, [420.0, 297.0]);
    assert!(pick.fallback.is_some());
}

#[test]
fn capped_automatic_ladder_keeps_its_standard_reduction_tail() {
    let mut req = Request::new([2000.0; 3], draftwright::layout::Principals::ALL, false);
    req.max_page = Some(1);
    let pick = draftwright::layout::choose(
        &req,
        &|_| draftwright::layout::Scaled::default(),
        None,
        None,
        &draftwright::layout::AUTOMATIC_ARRANGEMENTS,
        None,
    );
    assert_eq!(pick.page, [420.0, 297.0]);
    assert!(pick.scale >= 0.02, "{}", pick.scale);
    assert!(pick.fallback.is_none());
}
