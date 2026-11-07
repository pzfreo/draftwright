//! Issue regressions (`docs/rules/issues.yaml`) ported on the parts their Python tests build: the
//! boxes, pockets and channels of draftwright's `tests/test_issue_<n>_*.py`, made by
//! `tests/fixtures/recognition/issues/make.py` and recognised by Python quiddity 0.3.9. Each test
//! names the rule it checks and what draftwright's own run of the same part gives where the port
//! is compared with it. None needs the corpus but ISSUE-1187-a's, on nist_ctc_04 as draftwright's.

use std::collections::BTreeSet;
use std::io::Read as _;
use std::path::{Path, PathBuf};

use draftwright::lint::{
    self,
    section_recesses::{Grammar, grammar, recesses},
};
use draftwright::model::{self, Analysis, DimensionSpec, Plan, PlannedView, Spec};
use draftwright::output::Drawing;
use draftwright::text::Font;
use serde_json::{Value, json};

fn fixture(path: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/recognition/issues")
        .join(path)
}

fn font() -> Font {
    Font::open(&Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
        .unwrap()
}

/// The recognition document of the issue part *name*.
fn recognition(name: &str) -> Value {
    let mut json = String::new();
    flate2::read::GzDecoder::new(std::fs::File::open(fixture(&format!("{name}.json.gz"))).unwrap())
        .read_to_string(&mut json)
        .unwrap();
    serde_json::from_str(&json).unwrap()
}

/// The STEP file of the issue part *name*; a gzipped one is unpacked once into the test's
/// scratch directory.
fn step(name: &str) -> PathBuf {
    let plain = fixture(&format!("{name}.step"));
    if plain.exists() {
        return plain;
    }
    let out = Path::new(env!("CARGO_TARGET_TMPDIR")).join(format!("{name}.step"));
    static UNPACK: std::sync::Mutex<()> = std::sync::Mutex::new(());
    let _one = UNPACK.lock().unwrap();
    if !out.exists() {
        let mut bytes = Vec::new();
        flate2::read::GzDecoder::new(
            std::fs::File::open(fixture(&format!("{name}.step.gz"))).unwrap(),
        )
        .read_to_end(&mut bytes)
        .unwrap();
        // Written whole then renamed, so another test binary never reads half a file.
        let partial = out.with_extension(format!("step.{}", std::process::id()));
        std::fs::write(&partial, bytes).unwrap();
        std::fs::rename(&partial, &out).unwrap();
    }
    out
}

/// The issue part *name*, recognised with *document* (its own unless changed).
fn read_with(name: &str, document: &Value) -> (draftwright::haecceity::Part, Analysis) {
    let (part, mut a) = model::analyse(&step(name)).unwrap();
    model::recognise(&part, &mut a, &document.to_string()).unwrap();
    (part, a)
}

fn analysed(name: &str) -> (draftwright::haecceity::Part, Analysis) {
    read_with(name, &recognition(name))
}

fn planned(name: &str, spec: &Spec) -> Plan {
    model::plan(&analysed(name).1, spec).unwrap()
}

/// A record broken as one of draftwright's tests breaks it: the reason the reader gives, and the
/// change to the record's geometry.
type Breakage = (&'static str, fn(&mut Value));

fn spec(value: Value) -> Spec {
    serde_json::from_value(value).unwrap()
}

/// Where world point *p* lands on the page in view *v* (the centre of the part's box lands on
/// `at`; the page's x is `up × direction`).
fn page_of(v: &PlannedView, analysis: &Analysis, p: [f64; 3]) -> [f64; 2] {
    let [lo, hi] = analysis.envelope;
    let (t, u) = (v.direction, v.up);
    let right = [
        u[1] * t[2] - u[2] * t[1],
        u[2] * t[0] - u[0] * t[2],
        u[0] * t[1] - u[1] * t[0],
    ];
    let q = [0, 1, 2].map(|k| p[k] - 0.5 * (lo[k] + hi[k]));
    let dot = |a: [f64; 3]| a[0] * q[0] + a[1] * q[1] + a[2] * q[2];
    [v.at[0] + v.scale * dot(right), v.at[1] + v.scale * dot(u)]
}

fn overlap(a: [f64; 4], b: [f64; 4]) -> bool {
    a[0] < b[2] - 1e-9 && a[2] > b[0] + 1e-9 && a[1] < b[3] - 1e-9 && a[3] > b[1] + 1e-9
}

/// The recognition document rounds a record's coordinates to 0.001 mm in quiddity's working
/// frame, whose origin is off that grid on these parts (pocket_square's lies at -0.5634 on every
/// axis), so a pocket read back in the part's frame is up to 0.0005 mm from draftwright's, which
/// reads the shape itself.
const DOCUMENT: f64 = 1e-3;

/// A pocket as draftwright's own run of the part has it (its `PocketFeature`): the centre of its
/// frame, the axis it is cut along, its width and length axes and sizes, and its callout.
struct Pocket {
    part: &'static str,
    centre: [f64; 3],
    depth_axis: usize,
    width: (usize, f64),
    long: (usize, f64),
    label: &'static str,
}

const POCKETS: [Pocket; 6] = [
    Pocket {
        part: "pocket_square",
        centre: [10.0, 10.0, 10.0],
        depth_axis: 2,
        width: (0, 20.0),
        long: (1, 20.0),
        label: "20 × 20 × 10 DEEP",
    },
    Pocket {
        part: "pocket_z",
        centre: [10.0, 10.0, 10.0],
        depth_axis: 2,
        width: (1, 14.0),
        long: (0, 22.0),
        label: "14 × 22 × 10 DEEP",
    },
    Pocket {
        part: "pocket_x",
        centre: [10.0, 10.0, -10.0],
        depth_axis: 0,
        width: (1, 14.0),
        long: (2, 22.0),
        label: "14 × 22 × 10 DEEP",
    },
    Pocket {
        part: "pocket_y",
        centre: [10.0, -10.0, 10.0],
        depth_axis: 1,
        width: (2, 14.0),
        long: (0, 22.0),
        label: "14 × 22 × 10 DEEP",
    },
    Pocket {
        part: "curved_centered",
        centre: [0.0, 0.0, 14.0],
        depth_axis: 2,
        width: (0, 6.0),
        long: (1, 24.0),
        label: "6 × 24 ×",
    },
    Pocket {
        part: "curved_rotated",
        centre: [25.0, 22.0, 33.0],
        depth_axis: 0,
        width: (2, 6.0),
        long: (1, 24.0),
        label: "6 × 24 ×",
    },
];

/// ISSUE-916-a: a pocket's callout leader starts on the pocket's rim, at the end of the axis
/// matching a size it states, and leaves the part cleanly, whichever way the pocket is cut. On
/// draftwright's blind pocket (square, and 22 × 14 cut along z, x and y) and two pockets with a
/// cylindrical mouth, the callout is in the view that looks into the pocket and its arrow tip
/// is the midpoint of one of the rim's sides: on the width's or the length's centre line, at half
/// the other size from the centre (draftwright's `_tip_is_on_rim`, and its axis-matching case
/// where the tip lies on the centre line). Leaving cleanly: no plan problem but
/// `annotation_outside_area` (info) names the callout, and the sheet lint
/// (`lint::structural`) finds nothing on the drawing; a leader taken back across its own view
/// would be reported (`annotation_line_over_view`), so the leader leaves the part's outline once.
/// On pocket_square draftwright's tip is at (75, 140) and the port's at (74.9996, 139.9996).
/// Differences: draftwright accepts a tip anywhere along a side, the port takes a side's midpoint
/// (equivalent: both on the rim and on the matching axis); its third case forces the exit across
/// the width by planting notes in the other corridors, which the port's spec cannot place (each
/// exit is seen here on the parts as they come); and the callout of a pocket with a cylindrical
/// mouth prints `12.0`, draftwright's `12` (`a_cylindrical_mouth_states_its_greatest_depth`).
#[test]
fn a_pocket_leader_starts_on_its_rim_on_the_matching_axis() {
    let view_of = ["v:side", "v:front", "v:plan"];
    let mut axes_used = [false; 2];
    for p in &POCKETS {
        let (part, analysis) = analysed(p.part);
        let plan = model::plan(&analysis, &Spec::default()).unwrap();
        let callout = plan
            .dimensions
            .iter()
            .find(|d| d.id.ends_with("/pocket_width.length") && d.view == view_of[p.depth_axis])
            .unwrap_or_else(|| panic!("{}: no pocket callout", p.part));
        assert_eq!(callout.view, view_of[p.depth_axis], "{}", p.part);
        // A curved pocket's depth is checked in a_cylindrical_mouth_states_its_greatest_depth.
        if p.label.ends_with('×') {
            assert!(callout.label.starts_with(p.label), "{}", p.part);
        } else {
            assert_eq!(callout.label, p.label, "{}", p.part);
        }
        let v = plan.views.iter().find(|v| v.id == callout.view).unwrap();
        let tip = callout.placed.as_ref().unwrap().arrows[0][0];
        let mut on_side = None;
        for (i, (k, size)) in [p.width, p.long].into_iter().enumerate() {
            for s in [-1.0, 1.0] {
                let mut end = p.centre;
                end[k] += s * 0.5 * size;
                let at = page_of(v, &analysis, end);
                if (tip[0] - at[0]).hypot(tip[1] - at[1]) < DOCUMENT * v.scale {
                    on_side = Some(i);
                }
            }
        }
        let Some(i) = on_side else {
            panic!("{}: the tip {tip:?} is not on the rim", p.part);
        };
        axes_used[i] = true;
        for q in &plan.problems {
            assert!(
                !q.subjects.contains(&callout.id) || q.code == "annotation_outside_area"
                    // Curved depths need more than three places: the required info problem
                    // is compatible with the unchanged rim and clear-placement assertions.
                    || (q.code == "nominal_rounded" && q.severity == "info"
                        && q.message.contains("three-place limit")),
                "{}: {} {:?}",
                p.part,
                q.code,
                q.subjects
            );
        }
        let drawing = model::render(&part, &analysis, &plan, &font()).unwrap();
        let sheet = draftwright::lint::structural::check(&drawing);
        assert!(sheet.is_empty(), "{}: {sheet:#?}", p.part);
    }
    // Both kinds of exit occur: from a side across the width and from an end of the length.
    assert_eq!(axes_used, [true, true]);
}

/// ISSUE-1485-a: a pocket whose mouth is a cylinder owns its greatest depth, not a uniform one,
/// and a record whose mouth and floor disagree is refused. On draftwright's pocket cut into an
/// x cylinder of radius 20 (centred, offset off the crest, turned over, and moved and turned to
/// stand along z), the one callout claims `pocket_max_depth.length` and never
/// `pocket_depth.length`, prints `DEEP`, and states draftwright's depth: 12, or √364 − 8 on
/// the offset pocket, whose floor is lower than the mouth's crest. Five of draftwright's broken
/// records (a floor through the mouth, a mouth off its published centroid intersection, an end
/// of no known surface, two cylindrical ends, a mouth opening towards its floor) are refused by
/// the reader with draftwright's reasons, and the plan states nothing for them and reports the
/// recess (an info `dimensions_not_ported`, whose message, that no section recess is dimensioned
/// yet, is out of date since dims::recesses). The planar-substitution clause is
/// `a_planar_reading_cannot_certify_a_curved_pocket`. Not ported: repeated curved pockets as one
/// `N×` group (quiddity's `section_recess_patterns` are not read). Difference: the centred depth
/// prints `12.0` where draftwright prints `12`. The document rounds the floor's run interval to
/// 0.001 mm but gives the mouth's axis to the micrometre, so the depth reads 12.000483
/// (rust-wrong in what is printed, from the input: draftwright's own run reads the shape
/// exactly).
#[test]
fn a_cylindrical_mouth_states_its_greatest_depth() {
    let offset = 364f64.sqrt() - 8.0;
    for (part, depth, length) in [
        ("curved_centered", 12.0, 24.0),
        ("curved_offset", offset, 6.0),
        ("curved_negative", 12.0, 24.0),
        ("curved_rotated", 12.0, 24.0),
    ] {
        let plan = planned(part, &Spec::default());
        let mut callouts = plan
            .dimensions
            .iter()
            .filter(|d| d.id.contains("section_recess") && d.kind == "callout");
        let callout = callouts.next().unwrap();
        assert!(callouts.next().is_none(), "{part}");
        let claims: Vec<(&str, f64)> = callout
            .measurements
            .iter()
            .map(|m| (m.parameter.as_str(), m.value))
            .collect();
        assert_eq!(claims[0], ("pocket_width.length", 6.0), "{part}");
        assert_eq!(claims[1].0, "pocket_length.length", "{part}");
        assert!((claims[1].1 - length).abs() < 1e-9, "{part}");
        assert_eq!(claims[2].0, "pocket_max_depth.length", "{part}");
        assert!(
            (claims[2].1 - depth).abs() < DOCUMENT,
            "{part}: {}",
            claims[2].1
        );
        assert!(
            plan.dimensions
                .iter()
                .flat_map(|d| &d.measurements)
                .all(|m| m.parameter != "pocket_depth.length"),
            "{part}"
        );
        let printed = draftwright::dims::fmt(claims[2].1);
        assert_eq!(
            callout.label,
            format!("6 × {} × {printed} DEEP", draftwright::dims::fmt(length)),
            "{part}"
        );
    }
    assert_eq!(
        planned("curved_offset", &Spec::default())
            .dimensions
            .iter()
            .find(|d| d.id.contains("section_recess") && d.kind == "callout")
            .unwrap()
            .label,
        "6 × 6 × 11.079 DEEP"
    );

    let base = recognition("curved_centered");
    let broken: [Breakage; 5] = [
        ("positive depth over its complete profile", |g| {
            // The floor halfway up the mouth's height range over the section (draftwright's
            // test): the mouth's crest is at 20 above its axis, its lowest point (at the
            // section's ends, 12 off the crest) at 16.
            let axis = g["ends"]["high"]["surface"]["axis_point"][2]
                .as_f64()
                .unwrap();
            g["run_interval"][0] = json!(axis + 18.0);
        }),
        ("published centroid intersection", |g| {
            let high = g["run_interval"][1].as_f64().unwrap();
            g["run_interval"][1] = json!(high + 0.5);
        }),
        ("known surface type", |g| {
            g["ends"]["low"]["surface"] = json!({ "type": "sphere" });
        }),
        ("one cylindrical end", |g| {
            g["ends"]["low"]["surface"] = g["ends"]["high"]["surface"].clone();
        }),
        ("open away from its planar floor", |g| {
            g["ends"]["high"]["surface"]["branch"] = json!("negative");
        }),
    ];
    for (reason, change) in broken {
        let mut doc = base.clone();
        let features = doc["quiddity"]["features"].as_array_mut().unwrap();
        let f = features
            .iter_mut()
            .find(|f| f["family"] == "section_recesses")
            .unwrap();
        change(&mut f["record"]["geometry"]);
        let (_, analysis) = read_with("curved_centered", &doc);
        let recess = recesses(&analysis)[0];
        match grammar(recess) {
            Err(why) => assert!(why.reason().contains(reason), "{reason}: {}", why.reason()),
            Ok(_) => panic!("{reason}: the broken record is read"),
        }
        let plan = model::plan(&analysis, &Spec::default()).unwrap();
        assert!(
            plan.dimensions
                .iter()
                .flat_map(|d| &d.measurements)
                .all(|m| m.feature != recess.id),
            "{reason}"
        );
        assert!(
            plan.problems
                .iter()
                .any(|q| q.subjects == [recess.id.clone()]),
            "{reason}: the refused recess is not reported"
        );
    }
}

/// ISSUE-917-a, in part: an open U-channel is one feature with one width, and neither a bounded
/// pocket nor a centred rebate is a channel. On draftwright's base-and-walls channel (walls 12.5
/// and 12.5, and 10 and 15), pierced by a counterbored hole and a cross hole, the plan states one
/// channel width, 25, in the view along the channel, and the overall width and depth, 50 and 50,
/// as draftwright does; the holes' callouts are draftwright's (`2× ⌀8 THRU`, `⌀4 THRU ⌴ ⌀12 ↧ 4`).
/// A pocket bounded on all sides is dimensioned as a pocket (`20 × 30 × 8 DEEP`, no channel
/// width), and a rebate centred across a block is left to the height ladder (shoulders 20 and 40,
/// as draftwright's sheet), its recess reported, not dimensioned. The independent wall
/// thickness (`12.5`, or `10` with the opposite wall derived) and the base's `12` are
/// plate items. Their printed claims cover both the channel and plate requirements;
/// severing those claims loses that evidence in both consumers.
#[test]
fn an_open_channel_is_one_feature_with_one_width() {
    let labels = |plan: &Plan, part: &str| -> Vec<String> {
        plan.dimensions
            .iter()
            .filter(|d| d.id.contains(part))
            .map(|d| d.label.clone())
            .collect()
    };
    for part in ["u_channel", "u_channel_asymmetric"] {
        let plan = planned(part, &Spec::default());
        let widths: Vec<_> = plan
            .dimensions
            .iter()
            .filter(|d| d.id.ends_with("/channel_width.length"))
            .collect();
        assert_eq!(widths.len(), 1, "{part}");
        assert_eq!(widths[0].label, "25", "{part}");
        assert!(widths[0].placed.is_some(), "{part}");
        assert_eq!(labels(&plan, "d:envelope/width"), ["50"], "{part}");
        assert_eq!(labels(&plan, "d:envelope/depth"), ["50"], "{part}");
        let callouts: Vec<&str> = plan
            .dimensions
            .iter()
            .filter(|d| {
                d.id.ends_with("/bore.diameter") && (d.view == "v:front" || d.view == "v:plan")
            })
            .map(|d| d.label.as_str())
            .collect();
        assert_eq!(callouts, ["2× ⌀8 THRU", "⌀4 THRU ⌴ ⌀12 ↧ 4"], "{part}");
        assert!(
            plan.dimensions.iter().all(|d| !d.id.contains("/pocket_")),
            "{part}"
        );
        assert!(!plan.problems.iter().any(|q| q.code == "dimensions_not_ported" && q.subjects[0].starts_with("f:plate:")), "{part}");
        let (shape, analysis) = analysed(part);
        let plates: Vec<_> = analysis
            .features
            .iter()
            .filter(|f| f.kind == "plate")
            .collect();
        assert_eq!(plates.len(), 3, "{part}");
        for plate in &plates {
            let count = plan
                .dimensions
                .iter()
                .flat_map(|d| &d.measurements)
                .filter(|m| m.feature == plate.id && m.parameter == "thickness.length")
                .count();
            let covered = plan.omitted.iter().any(|o| {
                o.id == format!("d:{}/thickness.length", plate.id)
                    && matches!(o.state, draftwright::items::State::Covered { .. })
            });
            assert!(
                count == 1 || count == 0 && covered,
                "{part}: {} is stated once or derived",
                plate.id
            );
        }
        let mut drawing = model::render(&shape, &analysis, &plan, &font()).unwrap();
        assert!(
            findings(&analysis, &drawing).is_empty(),
            "{part}: {:?}",
            findings(&analysis, &drawing)
        );
        // Severing a wall's claim loses both the plate and channel evidence.
        for annotation in &mut drawing.annotations {
            annotation
                .claims
                .retain(|c| c.parameter != "thickness.length");
        }
        let issues = lint::lint(&analysis, &drawing);
        assert!(
            issues
                .iter()
                .any(|i| i.code == "channel_requirement_missing"
                    && i.requirements
                        .iter()
                        .any(|r| r.parameter == "thickness.length")),
            "{part}"
        );
        assert!(
            issues.iter().any(|i| i.code == "plate_requirement_missing"),
            "{part}"
        );
    }

    let pocket = planned("bounded_pocket", &Spec::default());
    assert!(pocket.dimensions.iter().all(|d| !d.id.contains("channel")));
    assert_eq!(
        labels(&pocket, "/pocket_width.length"),
        ["20 × 30 × 8 DEEP"]
    );

    let rebate = planned("centred_rebate", &Spec::default());
    assert!(
        rebate
            .dimensions
            .iter()
            .all(|d| !d.id.contains("section_recess"))
    );
    assert_eq!(labels(&rebate, "/step_position.length."), ["20", "40"]);
    let (_, analysis) = analysed("centred_rebate");
    let recess = &recesses(&analysis)[0].id;
    assert!(
        rebate
            .problems
            .iter()
            .any(|q| q.code == "dimensions_not_ported" && &q.subjects[0] == recess)
    );
}

/// ISSUE-1593-a, in part: no dimension is drawn into the title block, and one the block displaces
/// is still drawn, never discarded. On draftwright's four cases (a 40 × 30 × 12 box, an
/// 80 × 60 × 30 box, the same stepped, a bored plate), on the automatic sheet and on the sheet
/// draftwright's defect was seen on (A4 at 2:1 for the small box, 1:1 for the others), no placed
/// dimension's label, lines or arrowheads reach into the block, the overall depth is drawn with
/// draftwright's label, and no problem is an error. Where there is no room at all (the tall box
/// pinned to A4 at 2:1, whose side view runs off the sheet), the depth keeps its place and is
/// reported (`annotation_over_title_block`, `annotation_off_sheet`), not dropped. Not ported:
/// draftwright withholds such a dimension as an error naming the block and re-plans onto a
/// sheet that fits (its recovery ladder, ISSUE-1299-a); the port reports it where it stands.
/// On the stepped box the port's first fit is A4 at 1:1, as draftwright's sheet, since its plan
/// view, which no dimension needs, is left out (#222; it was A3 with the plan).
#[test]
fn the_title_block_keeps_dimensions_out_without_dropping_them() {
    for (part, defect, depth) in [
        ("tb_plain_box", "2:1", "30"),
        ("tb_tall_box", "1:1", "60"),
        ("tb_stepped", "1:1", "60"),
        ("tb_bored_plate", "1:1", "60"),
    ] {
        let (_, analysis) = analysed(part);
        for asked in [
            Spec::default(),
            spec(json!({ "sheet": { "size": "A4", "scale": defect } })),
        ] {
            let plan = model::plan(&analysis, &asked).unwrap();
            let tb = plan.sheet.title_block;
            let within = |p: [f64; 2]| tb[0] < p[0] && p[0] < tb[2] && tb[1] < p[1] && p[1] < tb[3];
            for d in &plan.dimensions {
                let p = d.placed.as_ref().unwrap();
                let ink = overlap(p.label_box, tb)
                    || p.lines
                        .iter()
                        .chain(&p.arrows)
                        .flatten()
                        .any(|q| within(*q));
                assert!(
                    !ink,
                    "{part} {}: {} is in the title block",
                    plan.sheet.scale, d.id
                );
            }
            let d = plan
                .dimensions
                .iter()
                .find(|d| d.id == "d:envelope/depth.length")
                .unwrap();
            assert_eq!(d.label, depth, "{part}");
            assert!(
                plan.problems.iter().all(|q| q.severity != "error"),
                "{part} {}: {:?}",
                plan.sheet.scale,
                plan.problems
            );
        }
    }
    assert_eq!(planned("tb_stepped", &Spec::default()).sheet.size, "A4");
    let crowded = planned(
        "tb_tall_box",
        &spec(json!({ "sheet": { "size": "A4", "scale": "2:1" } })),
    );
    let depth = "d:envelope/depth.length";
    assert!(
        crowded
            .dimensions
            .iter()
            .any(|d| d.id == depth && d.placed.is_some())
    );
    // H6 always lifts the row clear of the title block, even if the explicit scale then
    // overflows the border. The depth stays drawn and its overflow stays reported.
    assert!(
        !crowded
            .problems
            .iter()
            .any(|p| p.code == "annotation_over_title_block")
    );
    assert!(
        crowded
            .problems
            .iter()
            .any(|q| q.code == "annotation_off_sheet" && q.subjects[0] == depth)
    );
    let tb = crowded.sheet.title_block;
    for v in &crowded.views {
        assert!(!overlap(v.area, tb), "{} is over the title block", v.id);
    }
}

/// The section recesses of *analysis* that the reader takes as hexagonal pockets: each one's
/// axis and mouth centre in the part's frame, depth and across-flats size.
fn hexes(analysis: &Analysis) -> Vec<(String, usize, f64, f64, [f64; 3])> {
    draftwright::lint::pockets::hexes(analysis)
        .into_iter()
        .map(|h| {
            let (axis, at) = h.placed.unwrap();
            (h.feature.id.clone(), axis, h.depth, h.across_flats, at)
        })
        .collect()
}

/// ISSUE-1598-a, in part: a hex nut pocket is taken only from a regular-hex section, and counted
/// hexes keep distinct claims. On draftwright's whistle frame, as in its test, six section
/// recesses read as hexagonal pockets along z, 4.3 across flats and 1.9 deep, their mouths on the
/// part's bottom face (z = 0), within the document's 0.001 mm; the plan states them in one
/// callout, `6× HEX 4.3 A/F × 1.9 DEEP` (draftwright's label on the same part), claiming each
/// pocket's own across-flats size and depth (twelve claims, two per pocket). A pocket record whose
/// six vertices are not a regular hexagon (one moved 0.05 mm, one pulled to the centre, one
/// repeated, two swapped), that has five vertices or no depth is not read as a hex pocket, with
/// draftwright's reasons, and the plan states nothing for it. Not ported: declared hex pockets
/// (no declarations, R4 decision 3) and the label of a pocket of which only one size is approved
/// (the port's spec cannot approve one term of a callout); the rank of hex callouts over
/// incidental flats in leader placement.
#[test]
fn a_hex_pocket_needs_a_regular_hexagon_and_keeps_each_claim() {
    let name = "whistle_frame_reference";
    let (_, analysis) = analysed(name);
    let found = hexes(&analysis);
    assert_eq!(found.len(), 6);
    for (id, axis, depth, across, at) in &found {
        assert_eq!(*axis, 2, "{id}");
        assert!((depth - 1.9).abs() < DOCUMENT, "{id}: {depth}");
        assert!((across - 4.3).abs() < DOCUMENT, "{id}: {across}");
        assert!(at[2].abs() < DOCUMENT, "{id}: {at:?}");
    }
    let plan = model::plan(&analysis, &Spec::default()).unwrap();
    let callouts: Vec<_> = plan
        .dimensions
        .iter()
        .filter(|d| d.id.ends_with("/polygon_across_flats.length"))
        .collect();
    assert_eq!(callouts.len(), 1);
    assert_eq!(callouts[0].label, "6× HEX 4.3 A/F × 1.9 DEEP");
    let claims: std::collections::BTreeSet<(&str, &str)> = callouts[0]
        .measurements
        .iter()
        .map(|m| (m.feature.as_str(), m.parameter.as_str()))
        .collect();
    // ADR1-8: the pockets' floors are the 1.9 face level, so that level's rung is conveyed by
    // the callout too, which carries its claim beside the pockets' twelve.
    let own = claims
        .iter()
        .filter(|(f, _)| found.iter().any(|(id, ..)| id == f))
        .count();
    let conveyed: Vec<(&str, &str)> = claims
        .iter()
        .copied()
        .filter(|(f, _)| !found.iter().any(|(id, ..)| id == f))
        .collect();
    assert_eq!(own, 12);
    assert_eq!(conveyed, [("step_level", "step_height.length.h1_9")]);
    for (id, ..) in &found {
        for parameter in ["polygon_across_flats.length", "pocket_depth.length"] {
            assert!(
                claims.contains(&(id.as_str(), parameter)),
                "{id} {parameter}"
            );
        }
    }

    let base = recognition(name);
    let hex_index = base["quiddity"]["features"]
        .as_array()
        .unwrap()
        .iter()
        .position(|f| f["record"]["classification"]["section_shape"] == "hexagonal")
        .unwrap();
    let broken: [Breakage; 6] = [
        ("not regular", |g| {
            let x = g["profile"]["boundary"][2]["point"][0].as_f64().unwrap();
            g["profile"]["boundary"][2]["point"][0] = json!(x + 0.05);
        }),
        ("convex ordered section", |g| {
            g["profile"]["boundary"][2]["point"] = json!([0.0, 0.0]);
        }),
        ("convex ordered section", |g| {
            g["profile"]["boundary"][2] = g["profile"]["boundary"][1].clone();
        }),
        ("convex ordered section", |g| {
            g["profile"]["boundary"].as_array_mut().unwrap().swap(2, 3);
        }),
        ("six straight physical walls", |g| {
            g["profile"]["boundary"].as_array_mut().unwrap().pop();
        }),
        ("depth must be positive", |g| {
            let low = g["run_interval"][0].clone();
            g["run_interval"][1] = low;
        }),
    ];
    for (reason, change) in broken {
        let mut doc = base.clone();
        change(&mut doc["quiddity"]["features"][hex_index]["record"]["geometry"]);
        let (_, analysis) = read_with(name, &doc);
        assert_eq!(hexes(&analysis).len(), 5, "{reason}");
        let refused: Vec<_> = recesses(&analysis)
            .into_iter()
            .filter_map(|f| grammar(f).err().map(|why| (f, why)))
            .filter(|(_, why)| why.reason().contains(reason))
            .collect();
        assert_eq!(refused.len(), 1, "{reason}");
        let plan = model::plan(&analysis, &Spec::default()).unwrap();
        let id = &refused[0].0.id;
        assert!(
            plan.dimensions
                .iter()
                .flat_map(|d| &d.measurements)
                .all(|m| &m.feature != id),
            "{reason}"
        );
        assert!(
            plan.dimensions
                .iter()
                .any(|d| d.label == "5× HEX 4.3 A/F × 1.9 DEEP"),
            "{reason}"
        );
    }
}

/// ISSUE-1613-a, in part: a circular seat keeps its arc and its full set of size and location
/// requirements. On draftwright's whistle frame, as in its tests, three section recesses read as
/// circular seats along y: ⌀14.3 (to 0.0003 mm), 6 long, a 62.89° arc (to 0.0001°, a minor arc,
/// not a rectangular channel), their axis 11.1 above the bottom (to 0.0003 mm). The plan states
/// six requirements for each seat and nothing twice: one callout `3× ø14.3 × 6 LONG × 62.889° ARC`
/// (draftwright's label) claiming each seat's diameter, run and sweep (nine claims), and five
/// location dimensions carrying nine claims, as draftwright's: one x and one z shared by the
/// coaxial seats (`11.1`) and a y for each. Not ported: declared seats and the refusal of a
/// declared wrong radius or arc midpoint (no declarations, R4 decision 3); the seats' axis marks.
/// Difference: the shared x location prints `15.6`, draftwright's `15.7`. The seats' axis lies
/// on the part's midplane, 15.65 from the box, which draftwright reads exactly (and prints
/// 15.65 to one decimal as 15.7); the document's 0.001 mm rounding in quiddity's working frame
/// gives the port 15.6495 (rust-wrong in what is printed, from the input).
#[test]
fn a_circular_seat_keeps_its_arc_and_six_requirements() {
    let (_, analysis) = analysed("whistle_frame_reference");
    let seats: Vec<_> = recesses(&analysis)
        .into_iter()
        .filter(|f| matches!(grammar(f), Ok(Grammar::CircularChannel { .. })))
        .map(|f| f.id.clone())
        .collect();
    assert_eq!(seats.len(), 3);
    let plan = model::plan(&analysis, &Spec::default()).unwrap();
    assert!(plan.views.iter().any(|v| {
        serde_json::to_value(v).unwrap()["line_work"]
            .as_array()
            .is_some_and(|w| {
                w.iter().any(|w| {
                    w["kind"] == "axis"
                        && seats
                            .iter()
                            .any(|id| w["id"].as_str().unwrap().ends_with(id))
                })
            })
    }));
    let callout = plan
        .dimensions
        .iter()
        .find(|d| d.id.ends_with("/seat_diameter.diameter"))
        .unwrap();
    assert_eq!(callout.label, "3× ø14.300 × 6 LONG ×\n62.889° ARC");
    assert_eq!(callout.measurements.len(), 9);
    for m in &callout.measurements {
        let (want, within) = match m.parameter.as_str() {
            "seat_diameter.diameter" => (14.3, 3e-4),
            "seat_run.length" => (6.0, 1e-9),
            "seat_sweep.angle" => (62.8888, 1e-4),
            other => panic!("{other}"),
        };
        assert!(
            (m.value - want).abs() < within,
            "{}: {}",
            m.parameter,
            m.value
        );
    }
    let locations: Vec<_> = plan
        .dimensions
        .iter()
        .filter(|d| {
            d.measurements
                .iter()
                .any(|m| m.parameter.starts_with("seat_location.location."))
        })
        .collect();
    assert_eq!(locations.len(), 5);
    assert_eq!(
        locations
            .iter()
            .map(|d| d
                .measurements
                .iter()
                .filter(|m| m.parameter.starts_with("seat_location.location."))
                .count())
            .sum::<usize>(),
        9
    );
    for seat in &seats {
        let mut owned: Vec<&str> = plan
            .dimensions
            .iter()
            .flat_map(|d| &d.measurements)
            .filter(|m| &m.feature == seat)
            .map(|m| m.parameter.as_str())
            .collect();
        owned.sort();
        assert_eq!(
            owned,
            [
                "seat_diameter.diameter",
                "seat_location.location.x",
                "seat_location.location.y",
                "seat_location.location.z",
                "seat_run.length",
                "seat_sweep.angle",
            ],
            "{seat}"
        );
    }
    let z = locations
        .iter()
        .find(|d| {
            d.measurements
                .iter()
                .any(|m| m.parameter == "seat_location.location.z")
                && d.view == "v:front"
        })
        .unwrap();
    assert_eq!(
        (
            z.label.as_str(),
            z.measurements
                .iter()
                .filter(|m| m.parameter == "seat_location.location.z")
                .count()
        ),
        ("11.100", 3)
    );
    assert!((z.measurements[0].value - 11.1).abs() < 3e-4);
    let x = locations
        .iter()
        .find(|d| {
            d.measurements
                .iter()
                .any(|m| m.parameter == "seat_location.location.x")
        })
        .unwrap();
    assert_eq!(
        (
            x.label.as_str(),
            x.measurements
                .iter()
                .filter(|m| m.parameter == "seat_location.location.x")
                .count()
        ),
        ("15.650", 3)
    );
}

/// Lint's findings on *drawing* as `code feature parameter`, one per requirement they name.
fn findings(analysis: &Analysis, drawing: &Drawing) -> BTreeSet<String> {
    lint::lint(analysis, drawing)
        .iter()
        .flat_map(|i| {
            i.requirements
                .iter()
                .map(move |r| format!("{} {} {}", i.code, r.feature, r.parameter))
        })
        .collect()
}

fn omitting(omit: &[&str]) -> Spec {
    let mut spec = Spec::default();
    for id in omit {
        spec.dimensions.insert(
            id.to_string(),
            DimensionSpec {
                omit: true,
                ..Default::default()
            },
        );
    }
    spec
}

/// ISSUE-1143-a, the location clause, on draftwright's single hole (`_single_hole`: an 80 × 50
/// × 10 plate with a ⌀8 hole 12 and 7 off its centre): removing a required location reduces
/// completeness, and so does removing the callout. The default plan states `⌀8 THRU` and the
/// hole's two locations, `52` and `32`, as draftwright's own run of the part does, and lint finds
/// nothing. Leaving out the x location gives `feature_not_located` (the coverage backstop,
/// `lint::coverage::located`) naming exactly that requirement, `location.length.x`, with
/// draftwright's message; leaving out both names both axes; leaving out the callout gives
/// `feature_not_dimensioned` for ø8 (draftwright's message) and the callout's two requirements
/// missing, and no location finding. draftwright's lint on the same removals gives the same two
/// backstop findings and, from its hole ledger, `hole_requirement_missing` for each location
/// axis too: the port's hole coverage does not count locations yet (src/lint/holes.rs), so a
/// location is earned through the backstop alone. Lint reads the drawing, never the spec: an
/// omitted location is missing, where draftwright's ledger calls an authored omission
/// suppressed.
#[test]
fn removing_a_hole_location_or_its_callout_is_reported() {
    let (part, analysis) = analysed("single_hole");
    let callout = "d:f:hole:1/bore.diameter";
    let [x, y] = ["x", "y"].map(|k| format!("d:f:hole:1/location.length.{k}"));
    let full = model::plan(&analysis, &Spec::default()).unwrap();
    let labels: BTreeSet<(&str, &str)> = full
        .dimensions
        .iter()
        .filter(|d| d.id.starts_with("d:f:hole:1/"))
        .map(|d| (d.id.as_str(), d.label.as_str()))
        .collect();
    assert_eq!(
        labels,
        BTreeSet::from([(callout, "⌀8 THRU"), (x.as_str(), "52"), (y.as_str(), "32")])
    );
    let lint_of = |spec: &Spec| {
        let plan = model::plan(&analysis, spec).unwrap();
        let drawing = model::render(&part, &analysis, &plan, &font()).unwrap();
        (
            findings(&analysis, &drawing),
            lint::lint(&analysis, &drawing),
        )
    };
    let (none, _) = lint_of(&Spec::default());
    assert!(none.is_empty(), "{none:?}");

    let (found, issues) = lint_of(&omitting(&[&x]));
    assert_eq!(
        found,
        BTreeSet::from(["feature_not_located f:hole:1 location.length.x".to_string()])
    );
    let located = issues
        .iter()
        .find(|i| i.code == "feature_not_located")
        .unwrap();
    assert_eq!(located.message, "1 hole(s) have no locating dimension");
    assert_eq!(located.subjects, ["f:hole:1"]);

    let (found, _) = lint_of(&omitting(&[&x, &y]));
    assert_eq!(
        found,
        BTreeSet::from([
            "feature_not_located f:hole:1 location.length.x".to_string(),
            "feature_not_located f:hole:1 location.length.y".to_string(),
        ])
    );

    let (found, issues) = lint_of(&omitting(&[callout]));
    assert_eq!(
        found,
        BTreeSet::from([
            "feature_not_dimensioned f:hole:1 bore.diameter".to_string(),
            "hole_requirement_missing f:hole:1 bore.diameter".to_string(),
            "hole_requirement_missing f:hole:1 bore.through".to_string(),
        ])
    );
    assert!(issues.iter().any(|i| i.code == "feature_not_dimensioned"
        && i.message == "cylindrical feature ø8 has no diameter callout on the sheet"));
}

/// ISSUE-1351-a's prose clause and ISSUE-1543-c's descriptive-table clause, on draftwright's
/// counterbored block (`_part` of its #1351 test: a 60 × 40 × 20 block, ⌀8 through, counterbore
/// ⌀14 × 8 deep): prose cannot silence coverage, and a table that is not a verified schedule
/// cannot credit what it prints. The default callout is draftwright's, `⌀8 THRU ⌴ ⌀14 ↧ 8`.
/// With it left out, lint reports the counterbore's diameter and depth missing (with the bore's
/// diameter and through) and `feature_not_dimensioned` for ø14, as draftwright's
/// `test_prose_without_structured_satisfaction_cannot_silence_coverage` reports them; adding
/// draftwright's prose note (`PROFILED BORE: diameter 14 x 8 DEEP`) as a general note, a note
/// in the callout's own words, or a table printing the callout changes no finding, though each
/// is drawn on the sheet with its text searchable. Lint reads only the drawing's annotations
/// (the dimensions and callouts, each with its claims); general notes and spec tables are not
/// annotations, so nothing they print is credited, even a `⌀` diameter the backstop would
/// otherwise read as free text. Not ported: a structured note's explicit `satisfies` claim on a
/// placed feature (the spec's notes carry no claims), and verified schedule cells (spec tables
/// are descriptive only). draftwright calls the omitted requirements `suppressed` (its ledger
/// reads the author's omission); the port's lint never reads the spec, so they are missing.
#[test]
fn prose_and_descriptive_tables_cannot_silence_coverage() {
    let (part, analysis) = analysed("counterbored_block");
    let callout = "d:f:hole:1/bore.diameter";
    let full = model::plan(&analysis, &Spec::default()).unwrap();
    assert_eq!(
        full.dimensions
            .iter()
            .find(|d| d.id == callout)
            .unwrap()
            .label,
        "⌀8 THRU ⌴ ⌀14 ↧ 8"
    );
    let drawn = |spec: &Spec| {
        let plan = model::plan(&analysis, spec).unwrap();
        let drawing = model::render(&part, &analysis, &plan, &font()).unwrap();
        (plan, drawing)
    };
    let mut bare = omitting(&[callout]);
    let (_, drawing) = drawn(&bare);
    let missing = findings(&analysis, &drawing);
    for wanted in [
        "hole_requirement_missing f:hole:1 counterbore.diameter",
        "hole_requirement_missing f:hole:1 counterbore.depth",
        "feature_not_dimensioned f:hole:1 counterbore.diameter",
    ] {
        assert!(missing.contains(wanted), "{wanted}: {missing:?}");
    }
    assert!(
        lint::lint(&analysis, &drawing)
            .iter()
            .any(|i| i.message == "cylindrical feature ø14 has no diameter callout on the sheet")
    );

    let prose = "PROFILED BORE: diameter 14 x 8 DEEP";
    let words = "⌀8 THRU ⌴ ⌀14 ↧ 8";
    for (what, extra) in [
        (
            "draftwright's prose note",
            json!({ "notes": [ { "id": "n:1", "text": prose } ] }),
        ),
        (
            "the callout's words as a note",
            json!({ "notes": [ { "id": "n:1", "text": words } ] }),
        ),
        (
            "a table printing the callout",
            json!({ "tables": [ { "id": "t:1", "rows": [["HOLE", "SIZE"], ["A", words]] } ] }),
        ),
    ] {
        let with: Spec = spec(extra);
        bare.notes = with.notes;
        bare.tables = with.tables;
        let (plan, drawing) = drawn(&bare);
        assert!(
            plan.notes.iter().all(|n| n.placed.is_some())
                && plan.tables.iter().all(|t| t.placed.is_some()),
            "{what}: {:?}",
            plan.problems
        );
        let printed = if what.contains("prose") { prose } else { words };
        let text: Vec<&str> = drawing.text.iter().map(|r| r.text.as_str()).collect();
        assert!(text.iter().any(|t| t.contains(printed)), "{what}: {text:?}");
        assert_eq!(findings(&analysis, &drawing), missing, "{what}");
    }
}

/// ISSUE-1485-a's planar-substitution clause, on draftwright's pocket cut into a cylinder: a
/// drawing that claims a uniform planar `pocket_depth.length` cannot certify it. On the port's own drawing lint finds nothing for the pocket;
/// with the callout's greatest-depth claim changed to the planar reading, lint reports the
/// pocket's `pocket_max_depth.length` (lint::pockets requires it of a pocket whose mouth is a
/// cylinder) and credits nothing for the uniform depth. draftwright's test substitutes a planar
/// declared feature for the recognised one and finds the pocket's five requirements unverifiable;
/// the port has no declared features (maintainer decision), so the substitution is made where the
/// port's lint reads its evidence, the drawn callout (equivalent in what it guards: a uniform depth
/// never stands for a cylindrical mouth's).
#[test]
fn a_planar_reading_cannot_certify_a_curved_pocket() {
    let (part, analysis) = analysed("curved_centered");
    let plan = model::plan(&analysis, &Spec::default()).unwrap();
    let drawing = model::render(&part, &analysis, &plan, &font()).unwrap();
    let pocket = |f: &BTreeSet<String>| -> BTreeSet<String> {
        f.iter()
            .filter(|s| s.contains("f:section_recess:1"))
            .cloned()
            .collect()
    };
    assert!(pocket(&findings(&analysis, &drawing)).is_empty());

    let mut planar = drawing.clone();
    let a = planar
        .annotations
        .iter_mut()
        .find(|a| {
            a.name
                .starts_with("d:f:section_recess:1/pocket_width.length")
        })
        .unwrap();
    let max = a
        .claims
        .iter_mut()
        .find(|c| c.parameter == "pocket_max_depth.length")
        .unwrap();
    max.parameter = "pocket_depth.length".into();
    let run = a.runs[0];
    // #305 removes limit wording; the geometric claim, not MAX, distinguishes the readings.
    assert!(planar.text[run].text.ends_with(" DEEP"));
    assert!(!planar.text[run].text.contains("MAX"));
    let found = pocket(&findings(&analysis, &planar));
    assert!(
        found
            .iter()
            .any(|f| f.ends_with(" f:section_recess:1 pocket_max_depth.length")),
        "{found:?}"
    );
    assert!(
        found.iter().all(|f| !f.ends_with(" pocket_depth.length")),
        "{found:?}"
    );
}

#[path = "common/corpus.rs"]
mod corpus;

/// ISSUE-1187-a (draftwright's `test_the_retained_crossings_have_no_clear_alternative`, on
/// nist_ctc_04, and on cgb245, whose two crossings the alpha 1 quality gate accepts on this
/// ground: `docs/quality-2026-10-09.md`, "Accepted regressions"): a callout keeps a leader that crosses other ink or is
/// blocked (Policy B) only when no route is clear of the views, the sheet's furniture and every
/// committed annotation. Measured, not asserted: for each callout the plan reports crossing,
/// laid over, or blocked, the clear-route sweep (`dims::clear_routes`, after draftwright's test
/// helper `_clear_routes`: elbows around the leader's sites in 64 directions at ten reaches, far
/// more freedom than the original candidate producers gave) finds no clear route. Issue #147
/// also tries these rays in final repair and includes interior face and centre-line evidence;
/// a placement change that keeps a crossing while a clear route exists
/// fails here. Its features are the port's own (`model::analyse_native`), as the command line
/// reads the part; needs the corpus.
#[test]
fn issue_1187_a_retained_leader_crossings_have_no_clear_route() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    for part in [
        "nist/nist_ctc_04_asme1_rd.stp",
        "cadgenbench_inputs/cgb245.step",
    ] {
        retained_leader_crossings_have_no_clear_route(&dir.join(part));
    }
}

fn retained_leader_crossings_have_no_clear_route(path: &Path) {
    let (_, mut analysis) = model::analyse_native(path).unwrap();
    if path.ends_with("cgb245.step") {
        // Its two congruent obround slots, called out together, take the slot radius leaders
        // that kept crossings here: slot 2 is split from their callout to keep the historical
        // routing fixture, whose crossings make this check not vacuous.
        corpus::split_slot(&mut analysis, "f:slot:2");
    }
    let plan = model::plan_recovered(
        &analysis,
        &Spec {
            // Preserve the historical routing fixture's paper and its available corridors.
            sheet: serde_json::from_value(serde_json::json!({"size": if path.file_name().unwrap().to_string_lossy().contains("ctc_04") {"A1"} else {"A2"}})).unwrap(),
            hole_table: draftwright::hole_table::Setting::Never,
            ..Spec::default()
        },
    )
    .unwrap();
    let callouts: BTreeSet<&str> = plan
        .dimensions
        .iter()
        .filter(|d| d.kind == "callout")
        .map(|d| d.id.as_str())
        .collect();
    let retained: BTreeSet<&str> = plan
        .problems
        .iter()
        .filter(|p| {
            [
                "annotation_leader_crossing",
                "annotation_ink_overlap",
                "annotation_overlap",
                "annotation_over_view",
                "annotation_line_over_view",
                "annotation_leader_long",
            ]
            .contains(&p.code.as_str())
        })
        .flat_map(|p| p.subjects.iter().map(String::as_str))
        .filter(|s| callouts.contains(s))
        .collect();
    // The local slot-group layout now clears cgb245's formerly retained
    // crossings. Preserve that positive result and the actual radius callout.
    if path.ends_with("cgb245.step") {
        assert!(retained.is_empty(), "{retained:?}");
        assert!(
            plan.dimensions
                .iter()
                .any(|d| d.id == "d:f:slot:1/slot_end_radius.radius" && d.placed.is_some())
        );
        return;
    }
    // CTC-04 still keeps crossings (its strips are full), so the exhaustive
    // clear-route audit below remains nonvacuous.
    assert!(
        !retained.is_empty(),
        "{} no longer keeps a crossing leader",
        path.display()
    );
    let free: Vec<(&str, usize)> = retained
        .iter()
        .map(|id| {
            let n = draftwright::dims::clear_routes(
                &analysis,
                &plan.sheet,
                &plan.views,
                &plan.dimensions,
                id,
            )
            .unwrap_or_else(|| panic!("{id} is a placed callout"));
            (*id, n)
        })
        .filter(|(_, n)| *n > 0)
        .collect();
    assert!(
        free.is_empty(),
        "{}: callouts keep a crossing though clear routes exist: {free:?}",
        path.display()
    );
}

#[test]
fn offset_pocket_tangent_presentations_measure_the_actual_cylinder() {
    let id = "d:f:section_recess:1/pocket_width.length";
    let (part, a) = analysed("curved_offset");
    for (choice, value) in [("tangent_depth", 12.), ("far_extreme", 28.)] {
        let spec: Spec = serde_json::from_value(
            serde_json::json!({"dimensions": {id: {"presentation": choice}}}),
        )
        .unwrap();
        let p = model::plan(&a, &spec).unwrap();
        let d = p.dimensions.iter().find(|d| d.id == id).unwrap();
        assert!(
            (d.measurements[2].value - value).abs() < DOCUMENT,
            "{:?}",
            d.measurements
        );
        assert!(!d.basis.contains("greatest"), "{}", d.basis);
        let drawing = model::render(&part, &a, &p, &font()).unwrap();
        if choice == "tangent_depth" {
            assert!(!lint::lint(&a, &drawing).iter().any(|i| {
                i.code.starts_with("pocket_requirement_")
                    && i.requirements
                        .iter()
                        .any(|r| r.parameter == "pocket_max_depth.length")
            }));
        }
    }
}
