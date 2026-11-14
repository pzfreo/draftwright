//! Python owns an actual haecceity PartPmi, never a second requirement model.
use super::raised;
use draftwright::haecceity::{self, pmi::*};
use pyo3::prelude::*;
use serde_json::json;
use std::collections::BTreeSet;

fn length(value: &str) -> PyResult<Length> {
    let value = Decimal::parse(value).map_err(raised)?;
    if !value.to_f64().is_finite() || value.is_negative() {
        return Err(raised("a length must be finite and nonnegative"));
    }
    Ok(Length {
        value,
        unit: LengthUnit::Millimetre,
    })
}
fn value(value: &str) -> PyResult<Value> {
    Ok(Value {
        quantity: Quantity::Length(length(value)?),
        decimal_places: None,
    })
}

#[pyclass]
#[derive(Clone, Default)]
pub struct Pmi {
    model: PartPmi,
    faces: usize,
}

impl Pmi {
    fn feature(&mut self, mut faces: Vec<usize>) -> PyResult<FeatureId> {
        faces.sort_unstable();
        faces.dedup();
        if faces.is_empty() || faces.iter().any(|&f| f >= self.faces) {
            return Err(raised("PMI must name faces of this Part"));
        }
        let feature = Feature::Items(
            faces
                .into_iter()
                .map(|f| Anchor::Face(FaceIndex(f)))
                .collect(),
        );
        if let Some(i) = self.model.features.iter().position(|f| f == &feature) {
            return Ok(FeatureId(i));
        }
        let id = FeatureId(self.model.features.len());
        self.model.features.push(feature);
        Ok(id)
    }
}

#[pymethods]
impl Pmi {
    #[new]
    fn new(faces: usize) -> Self {
        Self {
            faces,
            ..Self::default()
        }
    }

    fn material(&mut self, text: String) {
        self.model.material = Some(Material {
            id: text,
            name: None,
            density: None,
        });
    }
    fn general_tolerance(&mut self, text: String) {
        self.model.general = vec![GeneralTolerance::Class {
            text,
            standard: None,
        }];
    }
    fn process(&mut self, text: String) {
        self.model
            .attributes
            .retain(|a| a.name != "manufacturing process");
        self.model.attributes.push(AttributeSet {
            name: "manufacturing process".into(),
            on: None,
            items: vec![("process".into(), AttributeValue::Text(text))],
            description: None,
            general_property: None,
        });
    }
    fn datum(&mut self, letter: &str, faces: Vec<usize>) -> PyResult<()> {
        let label = DatumLabel::new(letter).map_err(raised)?;
        if self.model.datums.iter().any(|d| d.label() == &label) {
            return Err(raised(format!("datum {letter} already declared")));
        }
        let f = self.feature(faces)?;
        self.model
            .datums
            .push(Datum::new(label, Some(f), vec![]).map_err(raised)?);
        Ok(())
    }
    fn fit(&mut self, faces: Vec<usize>, diameter: &str, class: &str) -> PyResult<()> {
        let letter = class.trim_end_matches(|c: char| c.is_ascii_digit());
        let grade = &class[letter.len()..];
        let deviation = FundamentalDeviation::parse(letter).map_err(raised)?;
        let grade = ToleranceGrade::parse(grade).map_err(raised)?;
        let tolerance = DimTolerance::Fit {
            class: Iso286Class { deviation, grade },
            limits: None,
        };
        let nominal = value(diameter)?;
        let f = self.feature(faces)?;
        let dim = Dimension {
            kind: DimensionKind::Size {
                feature: f,
                kind: SizeKind::Diameter,
                path: None,
                angle: None,
            },
            nominal: Some(nominal),
            tolerance,
            qualifier: None,
            modifiers: vec![],
            principle: None,
        };
        if let Some(existing) = self
            .model
            .dimensions
            .iter_mut()
            .find(|d| d.kind == dim.kind)
        {
            *existing = dim;
        } else {
            self.model.dimensions.push(dim);
        }
        Ok(())
    }
    fn finish(&mut self, faces: Vec<usize>, ra: &str) -> PyResult<()> {
        let raw = ra
            .trim()
            .strip_prefix("Ra")
            .or_else(|| ra.trim().strip_prefix("ra"))
            .unwrap_or(ra)
            .trim()
            .trim_end_matches("µm")
            .trim();
        let mut v = length(raw)?;
        v.unit = LengthUnit::Micrometre;
        let f = self.feature(faces)?;
        self.model.surface_textures.push(SurfaceTexture {
            on: Some(f),
            material_removal: MaterialRemoval::AnyProcessAllowed,
            parameters: vec![SurfaceTextureParameter {
                characteristic: "Ra".into(),
                value: v,
            }],
        });
        Ok(())
    }
    fn flatness(&mut self, faces: Vec<usize>, tolerance: &str) -> PyResult<()> {
        let magnitude = value(tolerance)?;
        let target = ToleranceTarget::Feature(self.feature(faces)?);
        self.model.tolerances.push(GeometricTolerance {
            kind: ToleranceKind::Flatness,
            target,
            magnitude: Some(magnitude),
            zone: None,
            modifiers: vec![],
            unit_basis: None,
            maximum: None,
            unequal: None,
            datums: None,
            auxiliary: vec![],
            description: None,
        });
        Ok(())
    }
    fn position(
        &mut self,
        faces: Vec<usize>,
        tolerance: &str,
        datums: Vec<String>,
    ) -> PyResult<()> {
        let magnitude = value(tolerance)?;
        let compartments = datums
            .iter()
            .map(|letter| {
                let i = self
                    .model
                    .datums
                    .iter()
                    .position(|d| d.label().as_str() == letter)
                    .ok_or_else(|| raised(format!("datum {letter} has not been declared")))?;
                Ok(Compartment {
                    references: vec![DatumReference {
                        datum: DatumId(i),
                        modifiers: vec![],
                    }],
                    modifiers: vec![],
                })
            })
            .collect::<PyResult<Vec<_>>>()?;
        let datums = DatumSystem::new(compartments).map_err(raised)?;
        let target = ToleranceTarget::Feature(self.feature(faces)?);
        self.model.tolerances.push(GeometricTolerance {
            kind: ToleranceKind::Position,
            target,
            magnitude: Some(magnitude),
            zone: Some(Zone {
                form: ZoneForm::WithinACylinder,
                projected: None,
                non_uniform: None,
                runout_angle: None,
                affected_plane: None,
                affected_plane_kind: None,
            }),
            modifiers: vec![],
            unit_basis: None,
            maximum: None,
            unequal: None,
            datums: Some(datums),
            auxiliary: vec![],
            description: None,
        });
        Ok(())
    }
    fn thread(&mut self, faces: Vec<usize>, designation: String) -> PyResult<()> {
        let (major, pitch) = draftwright::spec_notes::metric(&designation).map_err(raised)?;
        let major_diameter = length(&major.to_string())?;
        let pitch = value(&pitch.to_string())?;
        let fit = designation
            .split_once('-')
            .map_or("6H", |(_, c)| c.split_whitespace().next().unwrap_or("6H"))
            .to_string();
        if !fit.chars().last().is_some_and(|c| c.is_ascii_uppercase()) {
            return Err(raised(
                "an internal thread needs a capital fit class such as 6H",
            ));
        }
        let f = self.feature(faces)?;
        self.model.threads.push(Thread {
            feature: f,
            partial_area: None,
            side: ThreadSide::Internal,
            major_diameter,
            minor_diameter: None,
            pitch_diameter: None,
            number_of_threads: Ratio(Decimal::parse("1").map_err(raised)?),
            form: "M".into(),
            fit_class: fit,
            fit_class_2: None,
            hand: if designation.trim().ends_with(" LH") {
                Hand::Left
            } else {
                Hand::Right
            },
            crest: None,
            qualifier: Some(designation),
            nominal_size: None,
            runout: None,
        });
        self.model.attributes.push(AttributeSet {
            name: "thread manufacturing".into(),
            on: Some(NoteOwner::Feature(f)),
            items: vec![("pitch".into(), AttributeValue::Measure(pitch))],
            description: None,
            general_property: None,
        });
        Ok(())
    }
}

#[pyclass]
pub struct Geometry {
    part: haecceity::Part,
    analysis: draftwright::model::Analysis,
    mapping: Vec<usize>,
}

#[pymethods]
impl Geometry {
    #[new]
    fn new(py: Python<'_>, bytes: Vec<u8>, name: String, order: Vec<usize>) -> PyResult<Self> {
        py.detach(|| {
            let file = haecceity::step::StepFile::read(&bytes).map_err(raised)?;
            if file.parts.len() != 1 {
                return Err(raised("Part requires one product"));
            }
            let (part, analysis) =
                draftwright::model::analyse_native_bytes(&name, &bytes).map_err(raised)?;
            let mapping = order
                .iter()
                .map(|&i| {
                    let entity = file.parts[0]
                        .faces
                        .get(i)
                        .ok_or_else(|| raised("face mapping exceeds geometry"))?;
                    let found: Vec<_> = (0..part.faces.len())
                        .filter(|&f| part.face_source(f).is_some_and(|s| s.entity == *entity))
                        .collect();
                    match found.as_slice() {
                        [f] => Ok(*f),
                        _ => Err(raised(format!(
                            "face {i} maps to {} native faces",
                            found.len()
                        ))),
                    }
                })
                .collect::<PyResult<Vec<_>>>()?;
            Ok(Self {
                part,
                analysis,
                mapping,
            })
        })
    }
    fn keys(&self, faces: Vec<usize>) -> PyResult<String> {
        let faces: BTreeSet<_> = faces
            .iter()
            .map(|&i| {
                self.mapping
                    .get(i)
                    .copied()
                    .ok_or_else(|| raised("face not in Part"))
            })
            .collect::<PyResult<_>>()?;
        let features: Vec<_> = self
            .analysis
            .features
            .iter()
            .filter(|f| faces.iter().all(|i| f.faces.contains(i)))
            .collect();
        let exact: Vec<_> = features
            .iter()
            .filter(|f| f.faces.iter().copied().collect::<BTreeSet<_>>() == faces)
            .collect();
        let feature = if exact.len() == 1 {
            Some(&exact[0].id)
        } else if features.len() == 1 {
            Some(&features[0].id)
        } else {
            None
        };
        let references: Vec<_> = self
            .analysis
            .dimension_references()
            .iter()
            .filter(|(k, refs)| {
                k.starts_with("face:v1:")
                    && refs.iter().any(|r| {
                        r.membership()
                            .faces
                            .iter()
                            .copied()
                            .collect::<BTreeSet<_>>()
                            == faces
                    })
            })
            .map(|(k, _)| k)
            .collect();
        Ok(json!({"feature_id":feature,"reference_keys":references,"faces":faces,"features_found":features.iter().map(|f| &f.id).collect::<Vec<_>>()} ).to_string())
    }
    fn inspect(&self, pmi: &Pmi) -> PyResult<String> {
        let model = self.bound(pmi)?;
        serde_json::to_string(&draftwright::pmi::from_model(&self.part, &model).map_err(raised)?)
            .map_err(raised)
    }
    fn render(
        &self,
        py: Python<'_>,
        pmi: &Pmi,
        spec: &str,
        out: String,
        formats: Vec<String>,
    ) -> PyResult<String> {
        let model = self.bound(pmi)?;
        let spec: draftwright::model::Spec = serde_json::from_str(spec).map_err(raised)?;
        py.detach(|| {
            let formats: Vec<_> = formats.iter().map(String::as_str).collect();
            let (plan, files) = draftwright::api::render_model(
                &self.part,
                &self.analysis,
                &model,
                &spec,
                &formats,
                draftwright::api::DPI,
            )
            .map_err(raised)?;
            let written =
                draftwright::api::write(std::path::Path::new(&out), &files).map_err(raised)?;
            Ok(json!({"written": written, "problems": plan.problems}).to_string())
        })
    }
}
impl Geometry {
    fn bound(&self, pmi: &Pmi) -> PyResult<PartPmi> {
        if pmi.faces != self.mapping.len() {
            return Err(raised("PMI and shape face counts differ"));
        }
        let mut model = pmi.model.clone();
        for feature in &mut model.features {
            if let Feature::Items(anchors) = feature {
                for anchor in anchors {
                    if let Anchor::Face(i) = anchor {
                        i.0 = *self
                            .mapping
                            .get(i.0)
                            .ok_or_else(|| raised("PMI face not in shape"))?;
                    }
                }
            }
        }
        Ok(model)
    }
}
