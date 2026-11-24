//! draftwright's output against the Rust port's, from one drawing's content
//! (`tools/capture_output.py`): the content read back into a [`Drawing`], and SVG and DXF files
//! read back into each layer's lines (sampled) and style, to be compared both ways.

#![allow(dead_code)]

use std::collections::BTreeMap;
use std::io::Read as _;
use std::path::Path;

use draftwright::output::{Curve, Drawing, HAlign, Item, Layer, TextRun, VAlign, layer_styles};
use serde_json::Value;

pub type P = [f64; 2];
pub type Seg = (P, P);

pub fn fixtures() -> std::path::PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/output")
}

/// A gzipped fixture file's bytes.
pub fn gunzip(file: &str) -> Vec<u8> {
    let mut bytes = Vec::new();
    flate2::read::GzDecoder::new(std::fs::File::open(fixtures().join(file)).unwrap())
        .read_to_end(&mut bytes)
        .unwrap();
    bytes
}

pub fn load(name: &str) -> Value {
    serde_json::from_slice(&gunzip(&format!("{name}.json.gz"))).unwrap()
}

fn p(v: &Value) -> P {
    [v[0].as_f64().unwrap(), v[1].as_f64().unwrap()]
}

/// A recorded edge as the curve it draws, in its drawn direction.
fn curve(e: &Value) -> Curve {
    let reversed = e["reversed"].as_bool().unwrap();
    let (u0, u1) = (
        e["range"][0].as_f64().unwrap(),
        e["range"][1].as_f64().unwrap(),
    );
    match e["kind"].as_str().unwrap() {
        "line" => {
            let (a, b) = (p(&e["start"]), p(&e["end"]));
            if reversed {
                Curve::Line(b, a)
            } else {
                Curve::Line(a, b)
            }
        }
        "circle" | "ellipse" => {
            let (t0, t1) = if reversed { (u1, u0) } else { (u0, u1) };
            Curve::Conic {
                centre: p(&e["centre"]),
                radii: [
                    e["radii"][0].as_f64().unwrap(),
                    e["radii"][1].as_f64().unwrap(),
                ],
                x_axis: p(&e["x_axis"]),
                y_axis: p(&e["y_axis"]),
                t0,
                t1,
            }
        }
        _ => {
            let mut pieces: Vec<Vec<P>> = e["beziers"]
                .as_array()
                .unwrap()
                .iter()
                .map(|b| b.as_array().unwrap().iter().map(p).collect())
                .collect();
            if reversed {
                pieces.reverse();
                pieces.iter_mut().for_each(|b| b.reverse());
            }
            Curve::Beziers(pieces)
        }
    }
}

/// The recorded content as a drawing in draftwright's layer styles.
pub fn drawing(content: &Value) -> Drawing {
    let layers = layer_styles()
        .into_iter()
        .map(|(name, style)| Layer {
            name: name.to_string(),
            style,
            items: content["layers"][name]
                .as_array()
                .unwrap()
                .iter()
                .map(|it| match it.get("edge") {
                    Some(e) => Item::Edge(curve(e)),
                    None => Item::Face(
                        it["face"]
                            .as_array()
                            .unwrap()
                            .iter()
                            .map(|w| w.as_array().unwrap().iter().map(curve).collect())
                            .collect(),
                    ),
                })
                .collect(),
        })
        .collect();
    let text = content["text_runs"]
        .as_array()
        .unwrap()
        .iter()
        .map(|r| TextRun {
            text: r["text"].as_str().unwrap().to_string(),
            at: [r["x"].as_f64().unwrap(), r["y"].as_f64().unwrap()],
            size: r["font_size"].as_f64().unwrap(),
            rotation: r["rotation"].as_f64().unwrap(),
            font: r["font_path"]
                .as_str()
                .unwrap_or("IBMPlexMono-Regular.ttf")
                .to_string(),
            h_align: match r["h_align"].as_str().unwrap() {
                "center" => HAlign::Center,
                "right" => HAlign::Right,
                _ => HAlign::Left,
            },
            v_align: if r["v_align"] == "middle" {
                VAlign::Middle
            } else {
                VAlign::Baseline
            },
        })
        .collect();
    Drawing {
        page: p(&content["page"]),
        layers,
        link: content["link"]
            .as_array()
            .map(|l| [0, 1, 2, 3].map(|i| l[i].as_f64().unwrap())),
        text,
        annotations: Vec::new(),
        sheet: Default::default(),
        problems: Vec::new(),
        spec: None,
    }
}

/// The fonts draftwright vendors, by file name.
pub fn fonts(name: &str) -> Option<Vec<u8>> {
    std::fs::read(
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("fonts")
            .join(name),
    )
    .ok()
}

/// A layer read back: its style attributes and its lines, sampled.
#[derive(Default, Debug)]
pub struct ReadLayer {
    pub style: BTreeMap<String, String>,
    pub lines: Vec<Seg>,
}

fn bezier(p: &[P], t: f64) -> P {
    let mut q = p.to_vec();
    while q.len() > 1 {
        q = q
            .windows(2)
            .map(|w| {
                [
                    w[0][0] + (w[1][0] - w[0][0]) * t,
                    w[0][1] + (w[1][1] - w[0][1]) * t,
                ]
            })
            .collect();
    }
    q[0]
}

fn sampled(points: impl Iterator<Item = P>, out: &mut Vec<Seg>) {
    let pts: Vec<P> = points.collect();
    out.extend(pts.windows(2).map(|w| (w[0], w[1])));
}

/// An SVG elliptical arc, by the endpoint-to-centre conversion of SVG's appendix.
fn svg_arc(
    from: P,
    r: [f64; 2],
    rotation: f64,
    large: bool,
    sweep: bool,
    to: P,
    out: &mut Vec<Seg>,
) {
    let (mut rx, mut ry) = (r[0].abs(), r[1].abs());
    if rx == 0.0 || ry == 0.0 {
        out.push((from, to));
        return;
    }
    let (c, s) = (rotation.to_radians().cos(), rotation.to_radians().sin());
    let (dx, dy) = ((from[0] - to[0]) / 2.0, (from[1] - to[1]) / 2.0);
    let (x1, y1) = (c * dx + s * dy, -s * dx + c * dy);
    let lambda = x1 * x1 / (rx * rx) + y1 * y1 / (ry * ry);
    if lambda > 1.0 {
        (rx, ry) = (rx * lambda.sqrt(), ry * lambda.sqrt());
    }
    let num = (rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1).max(0.0);
    let mut k = (num / (rx * rx * y1 * y1 + ry * ry * x1 * x1)).sqrt();
    if large == sweep {
        k = -k;
    }
    let (cx1, cy1) = (k * rx * y1 / ry, -k * ry * x1 / rx);
    let centre = [
        c * cx1 - s * cy1 + (from[0] + to[0]) / 2.0,
        s * cx1 + c * cy1 + (from[1] + to[1]) / 2.0,
    ];
    let angle = |ux: f64, uy: f64| uy.atan2(ux);
    let t0 = angle((x1 - cx1) / rx, (y1 - cy1) / ry);
    let mut dt = angle((-x1 - cx1) / rx, (-y1 - cy1) / ry) - t0;
    if sweep && dt < 0.0 {
        dt += 2.0 * std::f64::consts::PI;
    } else if !sweep && dt > 0.0 {
        dt -= 2.0 * std::f64::consts::PI;
    }
    let n = 128;
    sampled(
        (0..=n).map(|i| {
            let t = t0 + dt * i as f64 / n as f64;
            let (x, y) = (rx * t.cos(), ry * t.sin());
            [centre[0] + c * x - s * y, centre[1] + s * x + c * y]
        }),
        out,
    );
}

fn attr(tag: &str, name: &str) -> Option<String> {
    let key = format!(" {name}=\"");
    let i = tag.find(&key)? + key.len();
    Some(tag[i..i + tag[i..].find('"')?].to_string())
}

/// An SVG's layers (the groups with an id), as draftwright's and the port's are written.
pub fn read_svg(text: &str) -> BTreeMap<String, ReadLayer> {
    let mut layers: BTreeMap<String, ReadLayer> = BTreeMap::new();
    let mut current: Option<String> = None;
    for tag in text.split('<').skip(1) {
        let tag = tag.split('>').next().unwrap_or("");
        let f = |n: &str| {
            attr(tag, n)
                .and_then(|v| v.parse::<f64>().ok())
                .unwrap_or(0.0)
        };
        if tag.starts_with("g ") {
            current = attr(tag, "id");
            if let Some(id) = &current {
                let layer = layers.entry(id.clone()).or_default();
                for a in ["fill", "stroke", "stroke-width", "stroke-dasharray"] {
                    if let Some(v) = attr(tag, a) {
                        layer.style.insert(a.into(), v);
                    }
                }
            }
            continue;
        }
        let Some(layer) = current.as_ref().map(|id| layers.get_mut(id).unwrap()) else {
            continue;
        };
        let out = &mut layer.lines;
        if tag.starts_with("line ") {
            out.push(([f("x1"), f("y1")], [f("x2"), f("y2")]));
        } else if tag.starts_with("circle ") {
            let (cx, cy, r) = (f("cx"), f("cy"), f("r"));
            let n = 256;
            sampled(
                (0..=n).map(|i| {
                    let t = 2.0 * std::f64::consts::PI * i as f64 / n as f64;
                    [cx + r * t.cos(), cy + r * t.sin()]
                }),
                out,
            );
        } else if tag.starts_with("path ") {
            let d = attr(tag, "d").unwrap_or_default();
            let tokens: Vec<&str> = d.split([' ', ',']).filter(|t| !t.is_empty()).collect();
            let (mut i, mut at) = (0, [0.0, 0.0]);
            let num = |i: &mut usize| -> f64 {
                *i += 1;
                tokens[*i - 1].parse().unwrap()
            };
            while i < tokens.len() {
                let cmd = tokens[i];
                i += 1;
                match cmd {
                    "M" => at = [num(&mut i), num(&mut i)],
                    "L" => {
                        let to = [num(&mut i), num(&mut i)];
                        out.push((at, to));
                        at = to;
                    }
                    "Q" | "C" => {
                        let n = if cmd == "Q" { 2 } else { 3 };
                        let mut poles = vec![at];
                        for _ in 0..n {
                            poles.push([num(&mut i), num(&mut i)]);
                        }
                        sampled((0..=32).map(|k| bezier(&poles, k as f64 / 32.0)), out);
                        at = poles[n];
                    }
                    "A" => {
                        let (rx, ry, rot) = (num(&mut i), num(&mut i), num(&mut i));
                        let (large, sweep) = (num(&mut i) != 0.0, num(&mut i) != 0.0);
                        let to = [num(&mut i), num(&mut i)];
                        svg_arc(at, [rx, ry], rot, large, sweep, to, out);
                        at = to;
                    }
                    other => panic!("unexpected path command {other:?}"),
                }
            }
        }
    }
    layers
}

/// A DXF's layers: each layer's line weight and its entities' lines, sampled.
pub fn read_dxf(bytes: &[u8]) -> BTreeMap<String, ReadLayer> {
    use dxf::entities::EntityType;
    let d = dxf::Drawing::load(&mut std::io::Cursor::new(bytes)).unwrap();
    let mut layers: BTreeMap<String, ReadLayer> = BTreeMap::new();
    for l in d.layers() {
        layers
            .entry(l.name.clone())
            .or_default()
            .style
            .insert("lineweight".into(), l.line_weight.raw_value().to_string());
    }
    for e in d.entities() {
        let out = &mut layers.entry(e.common.layer.clone()).or_default().lines;
        let n = 128;
        match &e.specific {
            EntityType::Line(l) => out.push(([l.p1.x, l.p1.y], [l.p2.x, l.p2.y])),
            EntityType::Circle(c) => sampled(
                (0..=n).map(|i| {
                    let t = 2.0 * std::f64::consts::PI * i as f64 / n as f64;
                    [
                        c.center.x + c.radius * t.cos(),
                        c.center.y + c.radius * t.sin(),
                    ]
                }),
                out,
            ),
            EntityType::Arc(a) => {
                let (s, mut e) = (a.start_angle.to_radians(), a.end_angle.to_radians());
                while e <= s {
                    e += 2.0 * std::f64::consts::PI;
                }
                sampled(
                    (0..=n).map(|i| {
                        let t = s + (e - s) * i as f64 / n as f64;
                        [
                            a.center.x + a.radius * t.cos(),
                            a.center.y + a.radius * t.sin(),
                        ]
                    }),
                    out,
                );
            }
            EntityType::Ellipse(el) => {
                let (mx, my) = (el.major_axis.x, el.major_axis.y);
                let (nx, ny) = (-my * el.minor_axis_ratio, mx * el.minor_axis_ratio);
                let (s, mut e) = (el.start_parameter, el.end_parameter);
                while e <= s {
                    e += 2.0 * std::f64::consts::PI;
                }
                sampled(
                    (0..=n).map(|i| {
                        let t = s + (e - s) * i as f64 / n as f64;
                        [
                            el.center.x + mx * t.cos() + nx * t.sin(),
                            el.center.y + my * t.cos() + ny * t.sin(),
                        ]
                    }),
                    out,
                );
            }
            EntityType::Spline(sp) => {
                let k = &sp.knot_values;
                let deg = sp.degree_of_curve as usize;
                let pts: Vec<P> = sp.control_points.iter().map(|q| [q.x, q.y]).collect();
                let w: Vec<f64> = if sp.weight_values.len() == pts.len() {
                    sp.weight_values.clone()
                } else {
                    vec![1.0; pts.len()]
                };
                let (a, b) = (k[deg], k[k.len() - deg - 1]);
                let m = 64 * (pts.len() / (deg.max(1))).max(1);
                sampled(
                    (0..=m).map(|i| de_boor(deg, k, &pts, &w, a + (b - a) * i as f64 / m as f64)),
                    out,
                );
            }
            other => panic!("unexpected DXF entity {other:?}"),
        }
    }
    layers
}

/// A rational B-spline's point at *t*.
fn de_boor(p: usize, k: &[f64], c: &[P], w: &[f64], t: f64) -> P {
    let n = c.len();
    let mut s = p;
    while s + 1 < n && t >= k[s + 1] {
        s += 1;
    }
    let mut d: Vec<[f64; 3]> = (0..=p)
        .map(|j| {
            let i = j + s - p;
            [c[i][0] * w[i], c[i][1] * w[i], w[i]]
        })
        .collect();
    for r in 1..=p {
        for j in (r..=p).rev() {
            let i = j + s - p;
            let den = k[i + p + 1 - r] - k[i];
            let a = if den == 0.0 { 0.0 } else { (t - k[i]) / den };
            let prev = d[j - 1];
            for (x, q) in d[j].iter_mut().enumerate() {
                *q = (1.0 - a) * prev[x] + a * *q;
            }
        }
    }
    [d[p][0] / d[p][2], d[p][1] / d[p][2]]
}

pub const TOL: f64 = 1e-3;
const STEP: f64 = 0.05;

/// The share of *from*'s length within TOL of *to*.
pub fn covered(from: &[Seg], to: &[Seg]) -> f64 {
    let cell = 0.5;
    let key = |p: P| ((p[0] / cell).floor() as i64, (p[1] / cell).floor() as i64);
    let mut grid: std::collections::HashMap<(i64, i64), Vec<usize>> = Default::default();
    for (i, (a, b)) in to.iter().enumerate() {
        let (ka, kb) = (key(*a), key(*b));
        for x in ka.0.min(kb.0) - 1..=ka.0.max(kb.0) + 1 {
            for y in ka.1.min(kb.1) - 1..=ka.1.max(kb.1) + 1 {
                grid.entry((x, y)).or_default().push(i);
            }
        }
    }
    let near = |p: P| {
        grid.get(&key(p))
            .is_some_and(|ids| ids.iter().any(|&i| distance(p, to[i]) <= TOL))
    };
    let (mut total, mut hit) = (0.0, 0.0);
    for (a, b) in from {
        let len = (b[0] - a[0]).hypot(b[1] - a[1]);
        let n = (len / STEP).ceil().max(1.0) as usize;
        for k in 0..n {
            let t = (k as f64 + 0.5) / n as f64;
            total += len / n as f64;
            if near([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]) {
                hit += len / n as f64;
            }
        }
    }
    if total == 0.0 { 1.0 } else { hit / total }
}

fn distance(p: P, (a, b): Seg) -> f64 {
    let (dx, dy) = (b[0] - a[0], b[1] - a[1]);
    let len2 = dx * dx + dy * dy;
    let t = if len2 == 0.0 {
        0.0
    } else {
        (((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2).clamp(0.0, 1.0)
    };
    (p[0] - a[0] - t * dx).hypot(p[1] - a[1] - t * dy)
}
