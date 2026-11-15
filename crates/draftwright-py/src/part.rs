//! Python owns an actual haecceity PartPmi, never a second requirement model.
use super::raised;
use draftwright::haecceity::{self, pmi::*};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use serde_json::json;
use std::collections::{BTreeMap, BTreeSet};

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
    fn shares_faces(&self, a: FeatureId, b: FeatureId) -> bool {
        match (&self.model.features[a.0], &self.model.features[b.0]) {
            (Feature::Items(a), Feature::Items(b)) => a.iter().any(|anchor| b.contains(anchor)),
            _ => false,
        }
    }

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
    fn process(&mut self, text: String) -> PyResult<()> {
        let canonical = match text.as_str() {
            "cnc-milled" | "cnc-turned" => "machined",
            other => other,
        };
        let process: draftwright::process::Process = serde_json::from_value(json!(canonical))
            .map_err(|e| PyValueError::new_err(format!("invalid process: {e}")))?;
        let text = process.label();
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
        Ok(())
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
        if self
            .model
            .threads
            .iter()
            .any(|t| self.shares_faces(t.feature, f))
        {
            return Err(PyValueError::new_err(
                "a cylinder is either fitted or threaded, not both",
            ));
        }
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
        self.model
            .surface_textures
            .retain(|texture| texture.on != Some(f));
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
    fn control(
        &mut self,
        faces: Vec<usize>,
        kind: &str,
        tolerance: &str,
        datums: Vec<String>,
    ) -> PyResult<()> {
        let kind = match kind {
            "flatness" => ToleranceKind::Flatness,
            "position" => ToleranceKind::Position,
            "perpendicularity" => ToleranceKind::Perpendicularity,
            "parallelism" => ToleranceKind::Parallelism,
            "cylindricity" => ToleranceKind::Cylindricity,
            "circular_runout" => ToleranceKind::CircularRunout,
            "total_runout" => ToleranceKind::TotalRunout,
            "profile_surface" => ToleranceKind::SurfaceProfile,
            "concentricity" => ToleranceKind::Concentricity,
            _ => return Err(raised(format!("unsupported geometric control {kind:?}"))),
        };
        match kind.datums() {
            DatumRequirement::Required if datums.is_empty() => {
                return Err(raised(format!("{kind:?} requires at least one datum")));
            }
            DatumRequirement::Forbidden if !datums.is_empty() => {
                return Err(raised(format!("{kind:?} takes no datum references")));
            }
            _ => {}
        }
        let magnitude = value(tolerance)?;
        if magnitude.si() <= 0.0 {
            return Err(raised("a geometric tolerance must be positive"));
        }
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
        let datums = if compartments.is_empty() {
            None
        } else {
            Some(DatumSystem::new(compartments).map_err(raised)?)
        };
        let target = ToleranceTarget::Feature(self.feature(faces)?);
        self.model
            .tolerances
            .retain(|t| t.kind != kind || t.target != target);
        self.model.tolerances.push(GeometricTolerance {
            kind,
            target,
            magnitude: Some(magnitude),
            zone: matches!(kind, ToleranceKind::Position | ToleranceKind::Concentricity).then_some(
                Zone {
                    form: ZoneForm::WithinACylinder,
                    projected: None,
                    non_uniform: None,
                    runout_angle: None,
                    affected_plane: None,
                    affected_plane_kind: None,
                },
            ),
            modifiers: vec![],
            unit_basis: None,
            maximum: None,
            unequal: None,
            datums,
            auxiliary: vec![],
            description: None,
        });
        Ok(())
    }

    fn dimension(
        &mut self,
        faces: Vec<usize>,
        to: Option<Vec<usize>>,
        nominal: &str,
        lower: &str,
        upper: &str,
        limits: bool,
    ) -> PyResult<()> {
        let nominal = value(nominal)?;
        let signed_value = |s: &str| -> PyResult<Value> {
            let n = Decimal::parse(s).map_err(raised)?;
            if !n.to_f64().is_finite() {
                return Err(raised("tolerance bounds must be finite"));
            }
            Ok(Value {
                quantity: Quantity::Length(Length {
                    value: n,
                    unit: LengthUnit::Millimetre,
                }),
                decimal_places: None,
            })
        };
        let bounds = Bounds::new(signed_value(upper)?, signed_value(lower)?).map_err(raised)?;
        let tolerance = if limits {
            DimTolerance::Limits(bounds)
        } else {
            DimTolerance::Deviations(bounds)
        };
        let f = self.feature(faces)?;
        let kind = if let Some(to) = to {
            DimensionKind::Location {
                from: f,
                to: self.feature(to)?,
                kind: LocationKind::LinearDistance,
                path: None,
                directed: false,
                angle: None,
            }
        } else {
            if self
                .model
                .threads
                .iter()
                .any(|t| self.shares_faces(t.feature, f))
            {
                return Err(PyValueError::new_err(
                    "a threaded cylinder cannot also carry a diameter tolerance or fit",
                ));
            }
            DimensionKind::Size {
                feature: f,
                kind: SizeKind::Diameter,
                path: None,
                angle: None,
            }
        };
        let dim = Dimension {
            kind,
            nominal: Some(nominal),
            tolerance,
            qualifier: None,
            modifiers: vec![],
            principle: None,
        };
        self.model.dimensions.retain(|d| d.kind != dim.kind);
        self.model.dimensions.push(dim);
        Ok(())
    }

    fn knurl(
        &mut self,
        faces: Vec<usize>,
        pattern: &str,
        pitch: &str,
        diameter: &str,
        teeth: u32,
        helix_angle: Option<&str>,
    ) -> PyResult<()> {
        let pattern = match pattern {
            "straight" => KnurlPattern::Straight,
            "diamond" => KnurlPattern::Diamond,
            _ => return Err(raised("knurl pattern must be straight or diamond")),
        };
        let pitch = length(pitch)?;
        let diameter = length(diameter)?;
        let expected = std::f64::consts::PI * diameter.mm() / pitch.mm();
        if teeth == 0 || !expected.is_finite() || (expected - f64::from(teeth)).abs() > 1.0 {
            return Err(raised(
                "knurl teeth must agree with pi * diameter / pitch (within one tooth)",
            ));
        }
        let helix_angle = helix_angle
            .map(|s| -> PyResult<Angle> {
                let value = Decimal::parse(s).map_err(raised)?;
                if !(value.to_f64() > 0.0 && value.to_f64() < 90.0) {
                    return Err(raised("knurl helix_angle must be between 0 and 90 degrees"));
                }
                Ok(Angle {
                    value,
                    unit: AngleUnit::Degree,
                })
            })
            .transpose()?;
        if pattern == KnurlPattern::Diamond && helix_angle.is_none() {
            return Err(raised("a diamond knurl requires helix_angle in degrees"));
        }
        let f = self.feature(faces)?;
        if self
            .model
            .threads
            .iter()
            .any(|t| self.shares_faces(t.feature, f))
        {
            return Err(PyValueError::new_err(
                "a cylinder cannot be both threaded and knurled",
            ));
        }
        self.model.knurls.retain(|k| k.feature != f);
        self.model.knurls.push(Knurl {
            feature: f,
            pattern,
            major_diameter: diameter.clone(),
            nominal_diameter: diameter,
            diametral_pitch: pitch,
            number_of_teeth: Some(Count(teeth)),
            tooth_depth: None,
            root_fillet: None,
            helix_angle,
            helix_hand: None,
        });
        Ok(())
    }
    fn thread(
        &mut self,
        faces: Vec<usize>,
        designation: String,
        external: bool,
        extent: Option<&str>,
    ) -> PyResult<()> {
        let (major, pitch) = draftwright::spec_notes::metric(&designation).map_err(raised)?;
        let major_diameter = length(&major.to_string())?;
        let pitch = value(&pitch.to_string())?;
        let fit = designation
            .split_once('-')
            .map_or(if external { "6g" } else { "6H" }, |(_, c)| {
                c.split_whitespace().next().unwrap_or("")
            })
            .to_string();
        let valid_class = |class: &str| {
            let bytes = class.as_bytes();
            !bytes.is_empty()
                && bytes.len() <= 4
                && bytes.len().is_multiple_of(2)
                && bytes.as_chunks::<2>().0.iter().all(|c| {
                    if external {
                        (b'3'..=b'9').contains(&c[0]) && b"efgh".contains(&c[1])
                    } else {
                        (b'4'..=b'8').contains(&c[0]) && b"GH".contains(&c[1])
                    }
                })
        };
        if !valid_class(&fit) {
            return Err(raised(if external {
                "unknown external thread class; use a lower-case ISO metric class such as 6g"
            } else {
                "unknown internal thread class; use a capital ISO metric class such as 6H"
            }));
        }
        let f = self.feature(faces)?;
        if self.model.dimensions.iter().any(|d| {
            matches!(d.kind,
            DimensionKind::Size { feature, kind: SizeKind::Diameter, .. } if self.shares_faces(feature, f))
        }) {
            return Err(PyValueError::new_err(
                "a cylinder is either fitted or threaded, not both (nor diameter toleranced)",
            ));
        }
        if self
            .model
            .knurls
            .iter()
            .any(|k| self.shares_faces(k.feature, f))
        {
            return Err(PyValueError::new_err(
                "a cylinder cannot be both threaded and knurled",
            ));
        }
        let extent = if external {
            Some(value(extent.ok_or_else(|| {
                raised("external thread needs its cylinder length")
            })?)?)
        } else {
            None
        };
        let canonical = format!(
            "M{major} x {}",
            pitch.as_length().expect("pitch is a length").mm()
        );
        self.model.threads.retain(|thread| thread.feature != f);
        self.model.attributes.retain(|attribute| {
            attribute.name != "thread manufacturing" || attribute.on != Some(NoteOwner::Feature(f))
        });
        self.model.threads.push(Thread {
            feature: f,
            partial_area: None,
            side: if external {
                ThreadSide::External
            } else {
                ThreadSide::Internal
            },
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
            qualifier: Some(if external { canonical } else { designation }),
            nominal_size: None,
            runout: None,
        });
        let mut items = vec![("pitch".into(), AttributeValue::Measure(pitch))];
        if let Some(extent) = extent {
            items.push(("thread length".into(), AttributeValue::Measure(extent)));
        }
        self.model.attributes.push(AttributeSet {
            name: "thread manufacturing".into(),
            on: Some(NoteOwner::Feature(f)),
            items,
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
    fn new(py: Python<'_>, bytes: Vec<u8>, name: String, names: Vec<String>) -> PyResult<Self> {
        py.detach(|| {
            use haecceity::p21::{self, Attribute, Document, RawEntity};
            let file = haecceity::step::StepFile::read(&bytes).map_err(raised)?;
            if file.parts.len() != 1 {
                return Err(raised("Part requires one product"));
            }
            let (part, analysis) =
                draftwright::model::analyse_native_bytes(&name, &bytes).map_err(raised)?;
            if names.len() != part.faces.len()
                || names.iter().collect::<BTreeSet<_>>().len() != names.len()
            {
                return Err(raised("face mapping needs one unique name per native face"));
            }
            // STEP text order and shell traversal order need not agree. Resolve each
            // native face's source name, then bind the caller's faces by those names.
            let doc = Document::parse(bytes).map_err(raised)?;
            let mut by_name: BTreeMap<String, Vec<usize>> = BTreeMap::new();
            for f in 0..part.faces.len() {
                let source = part
                    .face_source(f)
                    .ok_or_else(|| raised("native face has no source"))?;
                let Some(RawEntity::Simple { attributes, .. }) = doc.get(source.entity) else {
                    return Err(raised("native face source is not a named STEP face"));
                };
                let Some(Attribute::String(raw)) = attributes.first() else {
                    return Err(raised("native face source has no name"));
                };
                let name = p21::decode(raw).map_err(raised)?;
                by_name.entry(name).or_default().push(f);
            }
            let mapping = names
                .iter()
                .map(|name| match by_name.get(name).map(Vec::as_slice) {
                    Some([f]) => Ok(*f),
                    found => Err(raised(format!(
                        "face name {name:?} maps to {} native faces, expected one",
                        found.map_or(0, <[usize]>::len)
                    ))),
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
