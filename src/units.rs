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
    /// Tolerance magnitudes retain significant source digits beyond the nominal precision.
    pub fn magnitude(self, mm: f64) -> String {
        if self.is_mm() {
            return crate::pmi::fmt_magnitude(mm, None);
        }
        let v = self.from_mm(mm);
        let places = (3..=12)
            .find(|&p| {
                let k = 10f64.powi(p);
                ((v * k).round() / k - v).abs() <= 1e-12
            })
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
        let mut out = String::new();
        let mut at = 0;
        let mut thread_end = 0;
        while at < text.len() {
            let c = text[at..].chars().next().unwrap();
            if c == 'M' && text[at + 1..].starts_with(|c: char| c.is_ascii_digit()) {
                thread_end = text[at..]
                    .find(char::is_whitespace)
                    .map_or(text.len(), |i| at + i);
            }
            if at >= thread_end && (c.is_ascii_digit() || c == '.') {
                let end = text[at..]
                    .find(|c: char| !c.is_ascii_digit() && c != '.')
                    .map_or(text.len(), |i| at + i);
                let word = &text[at..end];
                let previous = text[..at].chars().next_back();
                let tail = text[end..].trim_start();
                let symbolic = previous
                    .is_some_and(|p| p.is_ascii_alphabetic() && !matches!(p, 'R' | 'C' | 'P'))
                    || tail.starts_with('°')
                    || tail.starts_with("deg")
                    || (tail.starts_with('×') && !word.contains('.'));
                if let Ok(mm) = word.parse::<f64>() {
                    if symbolic {
                        out.push_str(word);
                    } else {
                        let tolerance =
                            magnitude || text[..at].contains('±') || text[..at].contains('+');
                        out.push_str(&if tolerance {
                            self.magnitude(mm)
                        } else {
                            self.format(mm)
                        });
                    }
                } else {
                    out.push_str(word);
                }
                at = end;
            } else {
                out.push(c);
                at += c.len_utf8();
            }
        }
        out
    }
}

/// A conservative advisory: a sixteenth-inch grid must explain every principal extent
/// and at least 95% of four distinct sizes, including three non-integral mm sizes.
/// This examines model measurements, never drawing scale or printed rounding.
pub(crate) fn inch_design(lengths: &[f64]) -> bool {
    let on_grid = |v: f64| {
        let steps = v * 16. / 25.4;
        (steps - steps.round()).abs() <= 0.001 / (25.4 / 16.) + 1e-9
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
        && distinct.iter().filter(|v| on_grid(**v)).count() * 100 >= distinct.len() * 95
        && distinct
            .iter()
            .filter(|v| (**v - v.round()).abs() > 0.001)
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
            message: "The principal dimensions and feature sizes closely follow a 1/16-inch grid. Consider decimal-inch units and ANSI paper; the drawing remains in its requested units.".into(),
            subjects: vec!["sheet".into()],
            fix: vec![crate::items::Op::set("units", serde_json::json!("in")), crate::items::Op::set("sheet/paper", serde_json::json!("ansi"))],
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
        assert_eq!(
            Units::In.length_text("M8×1.25; 25.4 MIN FULL THREAD"),
            "M8×1.25; 1.000 MIN FULL THREAD"
        );
        assert_eq!(Units::In.length_text("⌀12.7 × 90°"), "⌀.500 × 90°");
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
}
