//! Drawing length units. Geometry and page coordinates always remain millimetres.
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub enum Units {
    #[default]
    #[serde(rename = "mm")]
    Mm,
    #[serde(rename = "in")]
    In,
}
impl Units {
    pub fn is_mm(&self) -> bool {
        *self == Self::Mm
    }
    pub fn symbol(self) -> &'static str {
        match self {
            Self::Mm => "mm",
            Self::In => "in",
        }
    }
    pub fn millimetres(self) -> f64 {
        match self {
            Self::Mm => 1.,
            Self::In => 25.4,
        }
    }
    pub fn from_mm(self, value: f64) -> f64 {
        value / self.millimetres()
    }
    pub fn format(self, mm: f64) -> String {
        self.format_places(mm, None)
    }
    pub fn format_places(self, mm: f64, places: Option<u8>) -> String {
        if self == Self::Mm {
            return places.map_or_else(
                || crate::nominal::format(mm),
                |p| crate::dims::precision::format(mm, p),
            );
        }
        let text = crate::dims::precision::format(self.from_mm(mm), places.unwrap_or(3));
        Self::inch_digits(text)
    }
    /// Tolerances default to three inch places; a nonzero sub-thousandth stays nonzero.
    pub fn magnitude(self, mm: f64) -> String {
        if self.is_mm() {
            return crate::pmi::fmt_magnitude(mm, None);
        }
        let v = self.from_mm(mm);
        let places = (3..=12)
            .find(|&p| v == 0. || (v * 10f64.powi(p)).round() != 0.)
            .unwrap_or(12) as u8;
        self.format_places(mm, Some(places))
    }
    pub(crate) fn inch_digits(text: String) -> String {
        if let Some(t) = text.strip_prefix("-0.") {
            format!("-.{t}")
        } else if let Some(t) = text.strip_prefix("0.") {
            format!(".{t}")
        } else {
            text
        }
    }
}

/// Paper family, independent of drawing units. All pages are landscape.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Paper {
    #[default]
    Iso,
    Ansi,
    Arch,
}
impl Paper {
    pub fn pages(self) -> &'static [(&'static str, f64, f64); 5] {
        match self {
            Self::Iso => &crate::layout::PAGES,
            Self::Ansi => &crate::layout::ANSI_PAGES,
            Self::Arch => &crate::layout::ARCH_PAGES,
        }
    }
    pub fn for_size(size: &str) -> Self {
        if size.starts_with("ANSI ") {
            Self::Ansi
        } else if size.starts_with("ARCH ") {
            Self::Arch
        } else {
            Self::Iso
        }
    }
}
impl crate::model::Spec {
    pub fn paper(&self) -> Paper {
        self.sheet.paper.unwrap_or_else(|| {
            self.sheet
                .size
                .as_deref()
                .or(self.sheet.preferred_max_size.as_deref())
                .map(Paper::for_size)
                .unwrap_or(if self.units == Units::In {
                    Paper::Ansi
                } else {
                    Paper::Iso
                })
        })
    }
    pub fn pages(&self) -> &'static [(&'static str, f64, f64); 5] {
        self.paper().pages()
    }
    pub fn scales(&self) -> &'static [f64] {
        scales(self.units)
    }
    pub fn smallest_scale(&self) -> f64 {
        *self.scales().last().expect("nonempty scale ladder")
    }
}
pub fn scales(units: Units) -> &'static [f64] {
    if units == Units::In {
        &crate::layout::INCH_SCALES
    } else {
        &crate::layout::SCALES
    }
}

impl Units {
    /// Reprint an engine-owned length label whose numeric source is in mm.
    /// Counts, angles, fit classes and metric thread designations are symbolic terms.
    pub(crate) fn length_text(self, text: &str) -> String {
        self.format_text(text, false)
    }
    pub(crate) fn magnitude_text(self, text: &str) -> String {
        self.format_text(text, true)
    }
    fn format_text(self, text: &str, magnitude: bool) -> String {
        if self.is_mm() {
            return text.into();
        }
        let mut tokens = Self::text_numbers(text);
        if magnitude {
            for n in &mut tokens {
                if !n.angular && !n.symbolic {
                    n.length = true;
                }
            }
        }
        let places = tokens
            .iter()
            .filter(|n| n.length)
            .filter_map(|n| text[n.range.clone()].parse::<f64>().ok())
            .map(|mm| self.magnitude_places(mm))
            .max()
            .unwrap_or(3);
        let mut out = String::new();
        let mut at = 0;
        for n in tokens {
            out.push_str(&text[at..n.range.start]);
            let word = &text[n.range.clone()];
            if n.length
                && let Ok(mm) = word.parse::<f64>()
            {
                let tolerance = magnitude
                    || text[..n.range.start].contains('±')
                    || text[..n.range.start].contains('+');
                if magnitude && mm == 0. && text[..n.range.start].trim_end().ends_with('/') {
                    out.push('-');
                }
                out.push_str(&self.format_places(mm, tolerance.then_some(places)));
            } else {
                out.push_str(word);
            }
            at = n.range.end;
        }
        out.push_str(&text[at..]);
        out
    }
    fn magnitude_places(self, mm: f64) -> u8 {
        let v = self.from_mm(mm);
        (3..=12)
            .find(|&p| v == 0. || (v * 10f64.powi(p)).round() != 0.)
            .unwrap_or(12) as u8
    }
    pub(crate) fn deviations(self, upper: f64, lower: f64) -> String {
        let places = self
            .magnitude_places(upper)
            .max(self.magnitude_places(lower));
        let signed = |v: f64, zero_sign: char| {
            let sign = if v == 0. {
                zero_sign
            } else if v > 0. {
                '+'
            } else {
                '-'
            };
            format!("{sign}{}", self.format_places(v.abs(), Some(places)))
        };
        format!("{}/{}", signed(upper, '+'), signed(lower, '-'))
    }
    /// Engine-owned label grammar. A number is a length only with a length marker,
    /// decimal point, or an unambiguous numeric size expression. Ratios, counts,
    /// angles, fit classes and metric thread tokens retain their own meanings.
    pub(crate) fn text_numbers(text: &str) -> Vec<TextNumber> {
        let mut out = Vec::new();
        let mut at = 0;
        let mut thread_end = 0;
        let numeric_expression = text
            .chars()
            .all(|c| c.is_ascii_digit() || c.is_whitespace() || ".+-±/×[]()°".contains(c));
        while at < text.len() {
            let c = text[at..].chars().next().unwrap();
            if c == 'M' && text[at + 1..].starts_with(|c: char| c.is_ascii_digit()) {
                thread_end = text[at..]
                    .find(|c: char| c.is_whitespace() || c == ';')
                    .map_or(text.len(), |i| at + i);
            }
            if c.is_ascii_digit()
                || (c == '.' && text[at + 1..].starts_with(|c: char| c.is_ascii_digit()))
            {
                let end = text[at..]
                    .find(|c: char| !c.is_ascii_digit() && c != '.')
                    .map_or(text.len(), |i| at + i);
                let word = &text[at..end];
                let head = &text[..at];
                let tail = text[end..].trim_start();
                let previous = head.chars().next_back();
                let marker = head
                    .trim_end()
                    .chars()
                    .next_back()
                    .is_some_and(|p| "⌀ØøRCP↧".contains(p));
                let angular = tail.starts_with('°') || tail.starts_with("deg");
                let times = tail.strip_prefix(['×', 'X']).map(str::trim_start);
                let next_angle = times.is_some_and(|rest| {
                    let e = rest
                        .find(|c: char| !c.is_ascii_digit() && c != '.')
                        .unwrap_or(rest.len());
                    e > 0 && rest[e..].trim_start().starts_with('°')
                });
                let count = !marker
                    && !word.contains('.')
                    && !next_angle
                    && times.is_some_and(|rest| {
                        text[end..].starts_with(['×', 'X'])
                            || rest.starts_with(['⌀', 'Ø', 'ø', 'R', 'M'])
                            || tail.starts_with('X')
                    });
                let ratio = tail.starts_with(':') || head.trim_end().ends_with(':');
                let symbolic = at < thread_end
                    || ratio
                    || count
                    || tail.starts_with("STARTS")
                    || previous.is_some_and(|p| p.is_ascii_alphabetic() && !"RCP".contains(p));
                let length = !symbolic
                    && !angular
                    && (marker
                        || word.contains('.')
                        || numeric_expression
                        || next_angle
                        || ["MIN", "MAX", "THRU", "TYP", "TO C"]
                            .iter()
                            .any(|q| tail.starts_with(q)));
                out.push(TextNumber {
                    range: at..end,
                    length,
                    angular: angular && !symbolic,
                    symbolic,
                });
                at = end;
            } else {
                at += c.len_utf8();
            }
        }
        out
    }
}

#[derive(Clone, Debug)]
pub(crate) struct TextNumber {
    pub range: std::ops::Range<usize>,
    pub length: bool,
    pub angular: bool,
    pub symbolic: bool,
}

/// A conservative advisory: sixteenth-inch or thousandth-inch decimal grids explain
/// every principal extent and at least 85% of four distinct sizes, including three
/// on-grid non-integral mm sizes.
/// This examines model measurements, never drawing scale or printed rounding.
pub(crate) fn inch_design(lengths: &[f64]) -> bool {
    let on_grid = |v: f64| {
        [25.4 / 16., 25.4 / 1000.].iter().any(|step| {
            let nearest = (v / step).round() * step;
            (v - nearest).abs() <= 0.001 + 1e-9
        })
    };
    if lengths.len() < 3
        || !lengths[..3]
            .iter()
            .all(|v| v.is_finite() && *v > 0. && on_grid(*v))
    {
        return false;
    }
    let mut distinct: Vec<f64> = lengths
        .iter()
        .copied()
        .filter(|v| v.is_finite() && *v > 0.)
        .collect();
    distinct.sort_by(f64::total_cmp);
    distinct.dedup_by(|a, b| (*a - *b).abs() <= 0.001);
    distinct.len() >= 4
        && distinct.iter().filter(|v| on_grid(**v)).count() * 100 >= distinct.len() * 85
        && distinct
            .iter()
            .filter(|v| on_grid(**v) && (**v - v.round()).abs() > 0.001)
            .count()
            >= 3
}

pub(crate) fn advise(analysis: &crate::model::Analysis, plan: &mut crate::model::Plan) {
    plan.problems.retain(|p| p.code != "inch_design_detected");
    if !plan.spec.units.is_mm() {
        return;
    }
    let mut lengths: Vec<_> = (0..3)
        .map(|k| analysis.envelope[1][k] - analysis.envelope[0][k])
        .collect();
    for f in &analysis.features {
        for key in [
            "diameter", "width", "length", "height", "radius", "leg1", "leg2",
        ] {
            if let Some(v) = f.parameters.get(key).and_then(serde_json::Value::as_f64) {
                lengths.push(v);
            }
        }
    }
    if inch_design(&lengths) {
        plan.problems.push(crate::model::Problem {
            id: "p:inch_design_detected:sheet".into(), code: "inch_design_detected".into(), severity: "info".into(),
            message: "The principal dimensions and feature sizes closely follow sixteenth-inch or thousandth-inch decimal grids. Consider decimal-inch units and ANSI paper; the drawing remains in its requested units.".into(),
            subjects: vec!["sheet".into()],
            fix: {
                let mut ops = vec![crate::items::Op::set("units", serde_json::json!("in")), crate::items::Op::set("sheet/paper", serde_json::json!("ansi"))];
                for (path, size) in [("sheet/size", plan.spec.sheet.size.as_deref()), ("sheet/preferred_max_size", plan.spec.sheet.preferred_max_size.as_deref())] {
                    if size.is_some_and(|v| Paper::for_size(v) != Paper::Ansi) { ops.push(crate::items::Op::set(path, serde_json::Value::Null)); }
                }
                ops
            },
            ..Default::default()
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        lint,
        model::{self, Spec},
    };
    use serde_json::json;

    #[test]
    fn reference_ink_cache_is_partitioned_by_units_and_paper() {
        let mm = Spec::default();
        let inch: Spec = serde_json::from_value(json!({"units":"in"})).unwrap();
        let arch: Spec =
            serde_json::from_value(json!({"units":"in","sheet":{"paper":"arch"}})).unwrap();
        let iso: Spec = serde_json::from_value(json!({"sheet":{"paper":"iso"}})).unwrap();
        assert_ne!(model::established_key(&mm), model::established_key(&inch));
        assert_ne!(model::established_key(&inch), model::established_key(&arch));
        assert_eq!(model::established_key(&mm), model::established_key(&iso));
    }

    #[test]
    fn decimal_inches_and_explicit_places() {
        for (mm, expected) in [
            (12.7, ".500"),
            (-12.7, "-.500"),
            (0., ".000"),
            (-0.00001, ".000"),
            (101.6, "4.000"),
            (52.3875, "2.063"),
            (6159.5, "242.500"),
        ] {
            assert_eq!(Units::In.format(mm), expected);
        }
        assert_eq!(Units::In.format_places(52.3875, Some(4)), "2.0625");
        assert_eq!(Units::In.format_places(12.7, Some(0)), "0");
        assert_eq!(Units::Mm.format(12.7), "12.7");
        assert_eq!(Units::In.magnitude(0.0254), ".001");
        assert_eq!(Units::In.magnitude(0.2), ".008");
        assert_eq!(Units::In.magnitude(0.00254), ".0001");
        assert_eq!(
            Units::In.length_text("M8×1.25; 25.4 MIN FULL THREAD"),
            "M8×1.25; 1.000 MIN FULL THREAD"
        );
        assert_eq!(Units::In.length_text("⌀12.7 × 90°"), "⌀.500 × 90°");
        assert_eq!(Units::In.length_text("⌀10 × 90°"), "⌀.394 × 90°");
        assert_eq!(Units::In.length_text("5 × 45°"), ".197 × 45°");
        assert_eq!(Units::In.length_text("4× ⌀10 × 90°"), "4× ⌀.394 × 90°");
    }

    #[test]
    fn old_specs_and_new_fields_roundtrip_without_a_schema_bump() {
        let old: Spec = serde_json::from_value(json!({"schema":"draftwright.spec/3"})).unwrap();
        let saved = serde_json::to_value(&old).unwrap();
        assert!(saved.get("units").is_none());
        assert!(saved["sheet"].get("paper").is_none());
        assert_eq!(old.paper(), Paper::Iso);
        for paper in ["iso", "ansi", "arch"] {
            let spec: Spec =
                serde_json::from_value(json!({"units":"in","sheet":{"paper":paper}})).unwrap();
            assert_eq!(
                serde_json::from_value::<Spec>(serde_json::to_value(&spec).unwrap()).unwrap(),
                spec
            );
        }
        assert_eq!(
            serde_json::from_value::<Spec>(json!({"units":"in"}))
                .unwrap()
                .paper(),
            Paper::Ansi
        );
        assert_eq!(
            serde_json::from_value::<Spec>(json!({"sheet":{"size":"ARCH D"}}))
                .unwrap()
                .paper(),
            Paper::Arch
        );
        assert!(serde_json::from_value::<Spec>(json!({"units":"feet"})).is_err());
        assert!(serde_json::from_value::<Spec>(json!({"sheet":{"paper":"us"}})).is_err());
    }

    #[test]
    fn imperial_paper_and_scale_ladders() {
        for (family, names) in [
            (Paper::Iso, ["A4", "A3", "A2", "A1", "A0"]),
            (
                Paper::Ansi,
                ["ANSI A", "ANSI B", "ANSI C", "ANSI D", "ANSI E"],
            ),
            (
                Paper::Arch,
                ["ARCH A", "ARCH B", "ARCH C", "ARCH D", "ARCH E"],
            ),
        ] {
            let pages = family.pages();
            assert_eq!(pages.map(|p| p.0), names);
            assert!(pages.iter().all(|p| p.1 > p.2));
            assert!(pages.windows(2).all(|p| p[1].1 > p[0].1 && p[1].2 > p[0].2));
            let mut request =
                crate::layout::Request::new([100., 60., 10.], crate::layout::Principals::ALL, true);
            request.paper = family;
            request.units = Units::In;
            let pick = crate::layout::choose(
                &request,
                &|_| crate::layout::Scaled::default(),
                None,
                None,
                &crate::layout::AUTOMATIC_ARRANGEMENTS,
                None,
            );
            assert!(pages.iter().any(|p| [p.1, p.2] == pick.page));
            assert!(scales(Units::In).contains(&pick.scale));
        }
        assert_eq!(
            scales(Units::In)
                .iter()
                .map(|s| model::scale_name(*s))
                .collect::<Vec<_>>(),
            [
                "4:1", "2:1", "1:1", "1:2", "1:4", "1:8", "1:12", "1:16", "1:24", "1:48", "1:96"
            ]
        );
    }

    #[test]
    fn inch_grid_detection_is_conservative_and_independent_of_sheet_scale() {
        assert!(inch_design(&[6159.5, 2628.9, 101.6, 52.3875]));
        assert!(!inch_design(&[100., 60., 10., 8., 6.]));
        assert!(!inch_design(&[25.4, 50.8, 12.7]));
        assert!(!inch_design(&[127., 254., 381., 508.]));
        assert!(!inch_design(&[6159.5, 2628.9, 101.6, 52.3875, 13.]));
    }

    #[test]
    fn length_lint_roundtrips_both_units_and_rejects_wrong_ink() {
        for (units, text) in [
            (Units::Mm, "⌀12.7 ↧ 25.4 ⌵ ⌀25.4 × 90°"),
            (Units::In, "⌀.500 ↧ 1.000 ⌵ ⌀1.000 × 90°"),
        ] {
            assert!(lint::holes::states_in(text, 12.7, units));
            assert!(lint::holes::states_in(text, 25.4, units));
            assert!(lint::holes::states_in(text, 90., units));
            assert!(!lint::holes::states_in(text, 14., units));
        }
        assert!(lint::holes::states_in(".333", 8.46, Units::In));
        assert!(!lint::holes::states_in(".333", 8.5, Units::In));
    }

    #[test]
    fn inch_precision_tolerances_and_fits_reach_the_print_boundary() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/parts/plate.step");
        let (_, a) = model::analyse_native(&path).unwrap();
        let mut spec:Spec=serde_json::from_value(json!({"units":"in","sheet":{"size":"ANSI B","scale":"1:1"},"envelope_tolerance":"+.002 -.001","precision":{"d:envelope/width.length":4}})).unwrap();
        let p = model::plan(&a, &spec).unwrap();
        let width = p
            .dimensions
            .iter()
            .find(|d| d.id == "d:envelope/width.length")
            .unwrap();
        assert!(
            width
                .label
                .contains(&Units::In.format_places(width.value, Some(4)))
        );
        assert!(width.label.contains("+.002 -.001"), "{}", width.label);
        assert_eq!(width.value, a.envelope[1][0] - a.envelope[0][0]);
        spec.envelope_tolerance = None;
        spec.sheet.fit_deviations = Some(true);
        for f in a.features.iter().filter(|f| f.kind == "hole") {
            spec.features.entry(f.id.clone()).or_default().fit = Some("H7".into());
        }
        let p = model::plan(&a, &spec).unwrap();
        assert!(p.dimensions.iter().any(|d| d.label.contains("H7")));
        assert!(
            p.dimensions
                .iter()
                .filter(|d| d.label.contains("H7"))
                .all(|d| d.label.contains(&Units::In.format(d.value)))
        );
    }

    #[test]
    fn inch_hole_table_roundtrip_still_catches_a_wrong_cell() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/parts/scattered_holes.step");
        let (part, a) = model::analyse_native(&path).unwrap();
        let spec: Spec = serde_json::from_value(json!({"units":"in"})).unwrap();
        let p = model::plan_recovered(&a, &spec).unwrap();
        let mut coordinates = 0;
        for table in p.hole_table.iter().chain(&p.hole_tables) {
            for row in &table.rows {
                for cell in row.cells.iter().skip(2).take(2) {
                    if let Ok(value) = cell.parse::<f64>() {
                        assert_eq!(*cell, Units::In.format(value * 25.4));
                        coordinates += 1;
                    }
                }
            }
        }
        assert!(coordinates > 0);
        let font = crate::text::Font::new(crate::api::MONO.to_vec()).unwrap();
        let mut drawing = model::render(&part, &a, &p, &font).unwrap();
        assert!(lint::lint(&a, &drawing).is_empty());
        let annotation = drawing
            .annotations
            .iter()
            .find(|a| {
                a.coordinate_origin.is_some()
                    && a.claims.iter().any(|c| c.parameter == "location.length.x")
            })
            .expect("coordinate cell");
        let index = annotation.runs[0];
        drawing.text[index].text = "99.999".into();
        assert!(!lint::lint(&a, &drawing).is_empty());
    }

    #[test]
    fn source_pmi_lengths_convert_once_from_their_mm_evidence() {
        let font = crate::text::Font::new(crate::api::MONO.to_vec()).unwrap();
        for file in [
            "pmi/step/nist_stc_06_asme1_ap242-e3.stp.gz",
            "pmi/step/nist_ctc_01_asme1_ap242-e1.stp.gz",
        ] {
            let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures")
                .join(file);
            let (part, a) = model::analyse_native(&path).unwrap();
            let spec: Spec = serde_json::from_value(
                json!({"units":"in","sheet":{"size":"ANSI E","scale":"1:2"}}),
            )
            .unwrap();
            let p = model::plan(&a, &spec).unwrap();
            let source: Vec<_> = p
                .dimensions
                .iter()
                .filter(|d| {
                    d.kind != "angular"
                        && d.measurements.iter().any(|m| {
                            m.feature.starts_with("pmi:")
                                && [
                                    "linear",
                                    "linear.basic",
                                    "diameter",
                                    "diameter.basic",
                                    "radius",
                                    "radius.basic",
                                    "thickness",
                                    "thickness.basic",
                                ]
                                .contains(&m.parameter.as_str())
                        })
                })
                .collect();
            assert!(!source.is_empty(), "{file}");
            for d in source {
                assert!(
                    lint::holes::states_in(&d.label, d.value, Units::In),
                    "{file}: {} {}",
                    d.label,
                    d.value
                );
            }
            let drawing = model::render(&part, &a, &p, &font).unwrap();
            assert!(lint::marks(&drawing).iter().all(|m| m.units == Units::In));
        }
    }

    #[test]
    fn pmi_location_unit_roundtrip() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/pmi/step/nist_ctc_01_asme1_ap242-e1.stp.gz");
        let (part, a) = model::analyse_native(&path).unwrap();
        let font = crate::text::Font::new(crate::api::MONO.to_vec()).unwrap();
        let mut codes = Vec::new();
        for units in [Units::Mm, Units::In] {
            let spec: Spec = serde_json::from_value(json!({"units":units})).unwrap();
            let plan = model::plan_recovered(&a, &spec).unwrap();
            let drawing = model::render(&part, &a, &plan, &font).unwrap();
            let mut current: Vec<_> = lint::lint(&a, &drawing)
                .iter()
                .map(|i| i.code.clone())
                .collect();
            current.sort();
            codes.push(current);
        }
        assert_eq!(codes[0], codes[1]);
    }

    #[test]
    fn rendered_inch_plate_has_no_new_geometric_lint() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/parts/plate.step");
        let (part, a) = model::analyse_native(&path).unwrap();
        let font = crate::text::Font::new(crate::api::MONO.to_vec()).unwrap();
        let mut codes = Vec::new();
        for units in [Units::Mm, Units::In] {
            let spec: Spec = serde_json::from_value(
                json!({"units":units,"sheet":{"paper":"iso","size":"A3","scale":"1:1"}}),
            )
            .unwrap();
            let plan = model::plan(&a, &spec).unwrap();
            let drawing = model::render(&part, &a, &plan, &font).unwrap();
            let issues = lint::lint(&a, &drawing);
            let mut current: Vec<_> = issues
                .iter()
                .filter(|i| i.code != "nominal_rounded")
                .map(|i| i.code.clone())
                .collect();
            current.sort();
            codes.push(current);
            if units == Units::In {
                assert!(
                    plan.sheet
                        .title_cells
                        .iter()
                        .any(|f| f.field == "units" && f.value.as_deref() == Some("in"))
                );
                let marks = lint::marks(&drawing);
                assert!(
                    marks
                        .iter()
                        .any(|m| m.units == Units::In && m.text.contains('.'))
                );
            }
        }
        assert_eq!(codes[0], codes[1]);
    }
    #[test]
    fn review_tokens_are_lengths_only_with_drawing_evidence() {
        for (text, wrong, right) in [
            ("4× ⌀.394 THRU", 100., 10.),
            ("4× ⌀.394 THRU", 110., 10.),
            ("M8×1.25 ↧ .500", 203., 12.7),
            ("2× .250", 50., 6.35),
        ] {
            assert!(!lint::holes::states_in(text, wrong, Units::In), "{text}");
            assert!(lint::holes::states_in(text, right, Units::In), "{text}");
        }
        assert!(!lint::holes::states_in("⌀4", 101.6, Units::In));
        let mark = lint::Mark {
            precision: Some(0),
            units: Units::In,
            name: "printed".into(),
            view: None,
            text: "⌀4".into(),
            claims: vec![],
        };
        assert!(mark.states_text(&mark.text, 101.6));
        assert!(!mark.states_text("4× ⌀.394", 101.6));
        assert!(!mark.states_text("⌀4 ↧ 2", 50.8));
        assert!(!lint::holes::states_in(".5", 0.5, Units::Mm));
        assert!(lint::holes::states_in(".5", 12.7, Units::In));
        assert_eq!(Units::In.length_text("10 × 20"), ".394 × .787");
        assert_eq!(Units::In.length_text("2X ⌀10"), "2X ⌀.394");
        assert_eq!(Units::In.length_text("1:2"), "1:2");
        assert_eq!(Units::In.deviations(0.0762, 0.), "+.003/-.000");
        assert_eq!(Units::In.deviations(0.1016, -0.01016), "+.0040/-.0004");
        assert_eq!(
            Units::In.magnitude_text("H7 (+0.0762/0)"),
            "H7 (+.003/-.000)"
        );
    }

    fn review_fixture(name: &str) -> (haecceity::Part, model::Analysis) {
        model::analyse_native(
            &std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/parts")
                .join(name),
        )
        .unwrap()
    }

    #[test]
    fn review_active_scale_floor_and_detection_fix_render() {
        let (part, a) = review_fixture("inch_plate.step");
        let spec: Spec = serde_json::from_value(json!({"units":"in","sheet":{"preferred_max_size":"ANSI A"},"sheets":{"max":2,"smallest_scale":"1:8"}})).unwrap();
        let mut plan = model::plan_recovered(&a, &spec).unwrap();
        assert_eq!(spec.smallest_scale(), 1. / 96.);
        for v in &mut plan.views {
            v.scale = 1. / 192.;
        }
        let findings = crate::sheets::check(&plan);
        let warning = findings
            .iter()
            .find(|p| p.code == "sheet_below_smallest_scale")
            .unwrap();
        assert!(warning.fix.iter().any(|op| op.value == Some(json!("1:96"))));
        let font = crate::text::Font::new(crate::api::MONO.to_vec()).unwrap();
        for sheet in [json!({}), json!({"size":"A3","preferred_max_size":"A1"})] {
            let spec: Spec = serde_json::from_value(json!({"sheet":sheet})).unwrap();
            let plan = model::plan_recovered(&a, &spec).unwrap();
            let fix = &plan
                .problems
                .iter()
                .find(|p| p.code == "inch_design_detected")
                .unwrap()
                .fix;
            let fixed = model::edit(&spec, &serde_json::to_value(fix).unwrap()).unwrap();
            assert_eq!(fixed.units, Units::In);
            assert_eq!(fixed.paper(), Paper::Ansi);
            assert!(fixed.sheet.size.is_none());
            assert!(fixed.sheet.preferred_max_size.is_none());
            let p = model::plan_recovered(&a, &fixed).unwrap();
            assert!(model::render(&part, &a, &p, &font).is_ok());
        }
    }

    #[test]
    fn review_rounding_summary_and_verbatim_notes() {
        let (_, a) = review_fixture("plate.step");
        let text = "KEEP 20 mm; 2X 10; SCALE 1:2";
        let spec: Spec =
            serde_json::from_value(json!({"units":"in", "notes":[{"id":"n:1","text":text}]}))
                .unwrap();
        let p = model::plan_recovered(&a, &spec).unwrap();
        let advice: Vec<_> = p
            .problems
            .iter()
            .filter(|p| p.code == "nominal_rounded")
            .collect();
        assert_eq!(advice.len(), 1);
        let advice = advice[0];
        assert_eq!(advice.subjects[0], "sheet");
        assert!(advice.refs.len() > 1);
        assert!(
            advice
                .refs
                .iter()
                .all(|r| r.contains(" mm prints ") && r.contains(" in "))
        );
        assert!(
            advice
                .fix
                .iter()
                .any(|op| op.path == "units" && op.value == Some(json!("mm")))
        );
        let remedy = advice
            .fixes
            .iter()
            .find(|f| f.ops.len() == 1 && f.ops[0].path == "precision")
            .expect("four-place remedy");
        assert!(
            remedy.ops[0]
                .value
                .as_ref()
                .unwrap()
                .as_object()
                .unwrap()
                .values()
                .all(|v| *v == json!(4))
        );
        let four = model::edit(&spec, &serde_json::to_value(&remedy.ops).unwrap()).unwrap();
        assert_eq!(four.units, Units::In);
        assert!(
            !model::plan_recovered(&a, &four)
                .unwrap()
                .problems
                .iter()
                .any(|p| p.code == "nominal_rounded")
        );
        assert!(p.notes.iter().any(|n| n.text == text));
    }

    #[test]
    fn review_dense_rounding_fix_fits_transport_and_preserves_authored_precision() {
        let (_, a) = review_fixture("scattered_holes.step");
        let spec: Spec =
            serde_json::from_value(json!({"units":"in","precision":{"d:envelope/width.length":5}}))
                .unwrap();
        let plan = model::plan_recovered(&a, &spec).unwrap();
        let problem = plan
            .problems
            .iter()
            .find(|p| p.code == "nominal_rounded")
            .unwrap();
        assert!(problem.refs.len() > crate::stateless::MAX_OPS);
        let ops = &problem
            .fixes
            .iter()
            .find(|f| f.ops.len() == 1 && f.ops[0].path == "precision")
            .expect("four-place remedy")
            .ops;
        assert_eq!(ops.len(), 1);
        assert!(crate::stateless::check_operation_count(ops.len()).is_ok());
        let fixed = model::edit(&spec, &serde_json::to_value(ops).unwrap()).unwrap();
        assert_eq!(fixed.units, Units::In);
        assert_eq!(fixed.precision["d:envelope/width.length"], 5);
        assert!(fixed.precision.len() > crate::stateless::MAX_OPS);
        assert!(fixed.precision.iter().all(|(id, p)| *p
            == if id == "d:envelope/width.length" {
                5
            } else {
                4
            }));
        let p = model::plan_recovered(&a, &fixed).unwrap();
        assert!(!p.problems.iter().any(|p| p.code == "nominal_rounded"));
    }

    #[test]
    fn review_zero_place_approval_reaches_the_independent_drawing() {
        let (part, a) = review_fixture("plate.step");
        let spec: Spec =
            serde_json::from_value(json!({"units":"in","precision":{"d:envelope/width.length":0}}))
                .unwrap();
        let p = model::plan_recovered(&a, &spec).unwrap();
        let font = crate::text::Font::new(crate::api::MONO.to_vec()).unwrap();
        let d = model::render(&part, &a, &p, &font).unwrap();
        let marks = lint::marks(&d);
        let mark = marks
            .iter()
            .find(|m| m.name == "d:envelope/width.length")
            .unwrap();
        assert_eq!(mark.precision, Some(0));
        let dimension = p.dimensions.iter().find(|d| d.id == mark.name).unwrap();
        assert!(mark.states_text(&mark.text, dimension.value));
        assert!(!lint::holes::states_in(
            &mark.text,
            dimension.value,
            Units::In
        ));
        assert!(lint::lint(&a, &d).is_empty());
    }

    #[test]
    fn review_paper_families_zones_and_smallest_sheet_first() {
        let (_, a) = review_fixture("plate.step");
        for sheet in [
            json!({"size":"ANSI B","preferred_max_size":"A1"}),
            json!({"paper":"arch","size":"ANSI B"}),
        ] {
            let spec: Spec = serde_json::from_value(json!({"sheet":sheet})).unwrap();
            assert!(
                model::plan(&a, &spec)
                    .unwrap_err()
                    .message
                    .contains("different paper family")
            );
        }
        for family in [Paper::Ansi, Paper::Arch] {
            for (i, (_, w, h)) in family.pages().iter().enumerate() {
                let z = crate::sheets::zones([*w, *h]);
                assert_eq!(
                    (z.columns, z.rows),
                    [(2, 2), (4, 2), (4, 4), (8, 4), (8, 8)][i]
                );
            }
            let mut r =
                crate::layout::Request::new([100., 60., 10.], crate::layout::Principals::ALL, true);
            r.paper = family;
            r.units = Units::In;
            let p = crate::layout::choose(
                &r,
                &|_| crate::layout::Scaled::default(),
                None,
                None,
                &crate::layout::AUTOMATIC_ARRANGEMENTS,
                None,
            );
            assert_eq!(p.page, [family.pages()[0].1, family.pages()[0].2]);
            assert!(p.scale <= 1.);
        }
    }

    #[test]
    fn review_decimal_inch_detection_allows_a_small_metric_minor_size() {
        let decimal: Vec<_> = [0.1, 0.2, 0.05, 0.35, 0.45, 0.55, 0.65]
            .map(|v| v * 25.4)
            .into();
        assert!(inch_design(&decimal));
        let mut mixed = decimal.clone();
        mixed.push(6.);
        assert!(inch_design(&mixed));
        assert!(!inch_design(&[100., 60., 10., 8., 6., 4., 3.]));
        assert!(!inch_design(&[127., 254., 381., 508., 635., 762.]));
    }
}
