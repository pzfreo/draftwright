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

    // Declarations validate before editing their vectors. Roll back feature allocation
    // as well if any later check rejects the call (including a second distance endpoint).
    fn edit(&mut self, edit: impl FnOnce(&mut Self) -> PyResult<()>) -> PyResult<()> {
        let features = self.model.features.len();
        let result = edit(self);
        if result.is_err() {
            self.model.features.truncate(features);
        }
        result
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

    fn snapshot(&self) -> Self {
        self.clone()
    }

    fn same(&self, other: &Self) -> bool {
        self.faces == other.faces && self.model == other.model
    }

    fn angular_tolerance(
        &mut self,
        first: Vec<usize>,
        second: Vec<usize>,
        lower: &str,
        upper: &str,
        limits: bool,
    ) -> PyResult<()> {
        self.edit(|this| {
            let angle_value = |s: &str| -> PyResult<Value> {
                let value = Decimal::parse(s).map_err(raised)?;
                if !value.to_f64().is_finite() {
                    return Err(raised("angular tolerance bounds must be finite"));
                }
                Ok(Value {
                    quantity: Quantity::Angle(Angle {
                        value,
                        unit: AngleUnit::Degree,
                    }),
                    decimal_places: None,
                })
            };
            let bounds = Bounds::new(angle_value(upper)?, angle_value(lower)?).map_err(raised)?;
            let kind = DimensionKind::Location {
                from: this.feature(first)?,
                to: this.feature(second)?,
                kind: LocationKind::Other("angle".into()),
                path: None,
                directed: false,
                angle: Some(AngleSelection::Equal),
            };
            let dimension = this
                .model
                .dimensions
                .iter_mut()
                .find(|d| d.kind == kind)
                .ok_or_else(|| raised("state the angle before tolerancing it"))?;
            dimension.tolerance = if limits {
                DimTolerance::Limits(bounds)
            } else {
                DimTolerance::Deviations(bounds)
            };
            Ok(())
        })
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
    fn note(&mut self, text: String) -> PyResult<()> {
        if text.trim().is_empty() {
            return Err(raised("note text must not be blank"));
        }
        let index = self
            .model
            .attributes
            .iter()
            .filter(|a| a.name == "semantic text" && a.on.is_none())
            .count()
            + 1;
        self.model.attributes.push(AttributeSet {
            name: "semantic text".into(),
            on: None,
            items: vec![
                ("general note".into(), AttributeValue::Text(text)),
                ("note index".into(), AttributeValue::Integer(index as i64)),
            ],
            description: Some("user defined attribute".into()),
            general_property: Some(GeneralProperty {
                id: String::new(),
                name: "semantic text".into(),
                description: None,
            }),
        });
        Ok(())
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
        self.edit(|this| {
            let label = DatumLabel::new(letter).map_err(raised)?;
            if this.model.datums.iter().any(|d| d.label() == &label) {
                return Err(raised(format!("datum {letter} already declared")));
            }
            let f = this.feature(faces)?;
            this.model
                .datums
                .push(Datum::new(label, Some(f), vec![]).map_err(raised)?);
            Ok(())
        })
    }
    fn fit(&mut self, faces: Vec<usize>, diameter: &str, class: &str) -> PyResult<()> {
        self.edit(|this| {
            let letter = class.trim_end_matches(|c: char| c.is_ascii_digit());
            let grade = &class[letter.len()..];
            let deviation = FundamentalDeviation::parse(letter).map_err(raised)?;
            let grade = ToleranceGrade::parse(grade).map_err(raised)?;
            let tolerance = DimTolerance::Fit {
                class: Iso286Class { deviation, grade },
                limits: None,
            };
            let nominal = value(diameter)?;
            let f = this.feature(faces)?;
            if this
                .model
                .threads
                .iter()
                .any(|t| this.shares_faces(t.feature, f))
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
            if let Some(existing) = this
                .model
                .dimensions
                .iter_mut()
                .find(|d| d.kind == dim.kind)
            {
                *existing = dim;
            } else {
                this.model.dimensions.push(dim);
            }
            Ok(())
        })
    }
    fn finish(&mut self, faces: Vec<usize>, ra: &str) -> PyResult<()> {
        self.edit(|this| {
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
            let f = if faces.is_empty() {
                None
            } else {
                Some(this.feature(faces)?)
            };
            this.model
                .surface_textures
                .retain(|texture| texture.on != f);
            this.model.surface_textures.push(SurfaceTexture {
                on: f,
                material_removal: MaterialRemoval::AnyProcessAllowed,
                parameters: vec![SurfaceTextureParameter {
                    characteristic: "Ra".into(),
                    value: v,
                }],
            });
            Ok(())
        })
    }
    fn control(
        &mut self,
        faces: Vec<usize>,
        kind: &str,
        tolerance: &str,
        datums: Vec<String>,
        cylindrical: bool,
    ) -> PyResult<()> {
        self.edit(|this| {
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
            let f = this.feature(faces)?;
            let compartments = datums
                .iter()
                .map(|letter| {
                    let i = this
                        .model
                        .datums
                        .iter()
                        .position(|d| d.label().as_str() == letter)
                        .ok_or_else(|| raised(format!("datum {letter} has not been declared")))?;
                    if this.model.datums[i]
                        .feature()
                        .is_some_and(|datum| this.shares_faces(datum, f))
                    {
                        return Err(PyValueError::new_err(format!(
                            "datum {letter} shares faces with the controlled feature"
                        )));
                    }
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
            let target = ToleranceTarget::Feature(f);
            this.model
                .tolerances
                .retain(|t| t.kind != kind || t.target != target);
            this.model.tolerances.push(GeometricTolerance {
                kind,
                target,
                magnitude: Some(magnitude),
                zone: (matches!(kind, ToleranceKind::Position | ToleranceKind::Concentricity)
                    || (cylindrical
                        && matches!(
                            kind,
                            ToleranceKind::Perpendicularity | ToleranceKind::Parallelism
                        )))
                .then_some(Zone {
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
                datums,
                auxiliary: vec![],
                description: None,
            });
            Ok(())
        })
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
        self.edit(|this| {
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
            let f = this.feature(faces)?;
            let kind = if let Some(to) = to {
                DimensionKind::Location {
                    from: f,
                    to: this.feature(to)?,
                    kind: LocationKind::LinearDistance,
                    path: None,
                    directed: false,
                    angle: None,
                }
            } else {
                if this
                    .model
                    .threads
                    .iter()
                    .any(|t| this.shares_faces(t.feature, f))
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
            this.model.dimensions.retain(|d| d.kind != dim.kind);
            this.model.dimensions.push(dim);
            Ok(())
        })
    }

    fn knurl(
        &mut self,
        faces: Vec<usize>,
        pattern: &str,
        pitch: &str,
        diameter: &str,
        helix_angle: Option<&str>,
    ) -> PyResult<()> {
        self.edit(|this| {
            let pattern = match pattern {
                "straight" => KnurlPattern::Straight,
                "diamond" => KnurlPattern::Diamond,
                _ => return Err(raised("knurl pattern must be straight or diamond")),
            };
            if pattern == KnurlPattern::Straight && helix_angle.is_some() {
                return Err(PyValueError::new_err(
                    "a straight knurl takes no helix_angle",
                ));
            }
            let pitch = length(pitch)?;
            let diameter = length(diameter)?;
            let expected = std::f64::consts::PI * diameter.mm() / pitch.mm();
            let teeth = expected.round_ties_even();
            if !expected.is_finite()
                || pitch.mm() <= 0.0
                || !(1.0..=f64::from(u32::MAX)).contains(&teeth)
            {
                return Err(raised(
                    "knurl pitch must produce a positive tooth count in the AP242 range",
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
            let f = this.feature(faces)?;
            if this
                .model
                .threads
                .iter()
                .any(|t| this.shares_faces(t.feature, f))
            {
                return Err(PyValueError::new_err(
                    "a cylinder cannot be both threaded and knurled",
                ));
            }
            this.model.knurls.retain(|k| k.feature != f);
            this.model.knurls.push(Knurl {
                feature: f,
                pattern,
                major_diameter: diameter.clone(),
                nominal_diameter: diameter,
                diametral_pitch: pitch,
                number_of_teeth: Some(Count(teeth as u32)),
                tooth_depth: None,
                root_fillet: None,
                helix_angle,
                helix_hand: None,
            });
            Ok(())
        })
    }
    fn thread(
        &mut self,
        faces: Vec<usize>,
        designation: String,
        external: bool,
        extent: Option<&str>,
        diameters: Vec<String>,
    ) -> PyResult<()> {
        self.edit(|this| {
            let (major, pitch_mm) = draftwright::thread_designation::metric(&designation).map_err(raised)?;
            let major_diameter = length(&major.to_string())?;
            let pitch = value(&pitch_mm.to_string())?;
            let number_of_threads = Ratio(Decimal::parse("1").map_err(raised)?);
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
            let selected = faces.len();
            let f = this.feature(faces)?;
            if this.model.dimensions.iter().any(|d| match d.kind {
                DimensionKind::Size {
                    feature,
                    kind: SizeKind::Diameter,
                    ..
                } => this.shares_faces(feature, f),
                _ => false,
            }) {
                return Err(PyValueError::new_err(
                    "a cylinder is either fitted or threaded, not both (nor diameter toleranced)",
                ));
            }
            if this
                .model
                .knurls
                .iter()
                .any(|k| this.shares_faces(k.feature, f))
            {
                return Err(PyValueError::new_err(
                    "a cylinder cannot be both threaded and knurled",
                ));
            }
            if diameters.len() != selected || diameters.is_empty() {
                return Err(PyValueError::new_err("thread needs a measured diameter for every cylinder"));
            }
            // Match pmi_lowering::authored: 0.01 mm externally, or the tapping-drill
            // interval nominal - 1.5*pitch .. nominal, with 0.01 mm slack internally.
            for measured in &diameters {
                let diameter = length(measured)?.mm();
                let matches = if external {
                    (diameter - major).abs() <= 0.01
                } else {
                    diameter <= major + 0.01 && diameter >= major - 1.5 * pitch_mm - 0.01
                };
                if !matches {
                    return Err(PyValueError::new_err(format!(
                        "thread {designation} does not match the measured {} cylinder diameter {diameter} mm",
                        if external { "external" } else { "internal" }
                    )));
                }
            }
            let partial = !external && extent.is_some();
            let extent = if external {
                Some(value(extent.ok_or_else(|| {
                    raised("external thread needs its cylinder length")
                })?)?)
            } else {
                extent.map(value).transpose()?
            };
            let canonical = format!(
                "M{major} x {}",
                pitch.as_length().expect("pitch is a length").mm()
            );
            this.model.threads.retain(|thread| thread.feature != f);
            this.model.attributes.retain(|attribute| {
                attribute.name != "thread manufacturing" || attribute.on != Some(NoteOwner::Feature(f))
            });
            this.model.threads.push(Thread {
                feature: f,
                partial_area: partial.then_some(f),
                side: if external {
                    ThreadSide::External
                } else {
                    ThreadSide::Internal
                },
                major_diameter: major_diameter.clone(),
                minor_diameter: None,
                pitch_diameter: None,
                number_of_threads,
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
                nominal_size: partial.then_some(major_diameter),
                runout: None,
            });
            let mut items = vec![("pitch".into(), AttributeValue::Measure(pitch))];
            if let Some(extent) = extent {
                let name = if external {
                    "thread length"
                } else {
                    "minimum full thread"
                };
                items.push((name.into(), AttributeValue::Measure(extent)));
            }
            this.model.attributes.push(AttributeSet {
                name: "thread manufacturing".into(),
                on: Some(NoteOwner::Feature(f)),
                items,
                description: None,
                general_property: None,
            });
            Ok(())
        })
    }
}

#[pyclass]
pub struct Geometry {
    part: haecceity::Part,
    pub(crate) product: std::sync::Arc<haecceity::Part>,
    product_mapping: Vec<usize>,
    analysis: draftwright::model::Analysis,
    mapping: Vec<usize>,
}

#[pymethods]
impl Geometry {
    #[new]
    fn new(py: Python<'_>, bytes: Vec<u8>, name: String, names: Vec<String>) -> PyResult<Self> {
        py.detach(|| {
            let file = haecceity::step::StepFile::read(&bytes).map_err(raised)?;
            if file.parts.len() != 1 {
                return Err(raised("Part requires one product"));
            }
            let (part, analysis) =
                draftwright::model::analyse_native_bytes(&name, &bytes).map_err(raised)?;
            let product = file.part(0).map_err(raised)?;
            let doc = haecceity::p21::Document::parse(bytes).map_err(raised)?;
            let mapping = face_mapping(&part, &doc, &names)?;
            let product_mapping = face_mapping(&product, &doc, &names)?;
            Ok(Self {
                part,
                product: std::sync::Arc::new(product),
                product_mapping,
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
    fn angle(&self, pmi: &mut Pmi, first: Vec<usize>, second: Vec<usize>) -> PyResult<f64> {
        // Measure with the renderer's own plane/ray selection, in a tentative model.
        // Rejected references leave both features and dimensions untouched.
        let mut candidate = pmi.clone();
        let from = candidate.feature(first)?;
        let to = candidate.feature(second)?;
        let kind = DimensionKind::Location {
            from,
            to,
            kind: LocationKind::Other("angle".into()),
            path: None,
            directed: false,
            angle: Some(AngleSelection::Equal),
        };
        // Re-measure an existing angle without discarding its tolerance or
        // moving its identity in the dimension inventory when obtaining a handle.
        let index = if let Some(i) = candidate
            .model
            .dimensions
            .iter()
            .position(|d| d.kind == kind)
        {
            candidate.model.dimensions[i].nominal = None;
            i
        } else {
            candidate.model.dimensions.push(Dimension {
                kind,
                nominal: None,
                tolerance: DimTolerance::None,
                qualifier: None,
                modifiers: vec![],
                principle: None,
            });
            candidate.model.dimensions.len() - 1
        };
        let report =
            draftwright::pmi::from_model(&self.part, &self.bound(&candidate)?).map_err(raised)?;
        let geometry = report
            .records
            .get(index)
            .and_then(|r| r.geometry.as_ref())
            .ok_or_else(|| raised("angle requires two distinct planar faces"))?;
        let angle = geometry
            .angle
            .as_ref()
            .ok_or_else(|| raised(geometry.reasons.join("; ")))?;
        if angle.principal().is_none() {
            return Err(raised("angle requires coplanar rays in a principal view"));
        }
        let degrees = angle
            .degrees()
            .ok_or_else(|| raised("angle reference rays must not be parallel"))?;
        // Measurement round-off is not authored display precision. Use the
        // engine's nominal cleanup before storing the measured degree value.
        let cleaned = (degrees * 1e6).round() / 1e6;
        let degrees = if cleaned > 0.0 && cleaned < 180.0 {
            cleaned
        } else {
            degrees
        };
        candidate
            .model
            .dimensions
            .get_mut(index)
            .expect("angle dimension")
            .nominal = Some(Value {
            quantity: Quantity::Angle(Angle {
                value: Decimal::parse(&degrees.to_string()).map_err(raised)?,
                unit: AngleUnit::Degree,
            }),
            decimal_places: None,
        });
        *pmi = candidate;
        Ok(degrees)
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
        bind(pmi, &self.mapping)
    }

    pub(crate) fn bound_product(&self, pmi: &Pmi) -> PyResult<PartPmi> {
        bind(pmi, &self.product_mapping)
    }
}

fn bind(pmi: &Pmi, mapping: &[usize]) -> PyResult<PartPmi> {
    if pmi.faces != mapping.len() {
        return Err(raised("PMI and shape face counts differ"));
    }
    let mut model = pmi.model.clone();
    for feature in &mut model.features {
        if let Feature::Items(anchors) = feature {
            for anchor in anchors {
                if let Anchor::Face(i) = anchor {
                    i.0 = *mapping
                        .get(i.0)
                        .ok_or_else(|| raised("PMI face not in shape"))?;
                }
            }
        }
    }
    Ok(model)
}

fn face_mapping(
    part: &haecceity::Part,
    doc: &haecceity::p21::Document,
    names: &[String],
) -> PyResult<Vec<usize>> {
    use haecceity::p21::{self, Attribute, RawEntity};
    if names.len() != part.faces.len() || names.iter().collect::<BTreeSet<_>>().len() != names.len()
    {
        return Err(raised("face mapping needs one unique name per native face"));
    }
    // Text order, placed-body traversal and product-local traversal may differ.
    // Resolve both maps by the same exact, caller-authored face names.
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
    names
        .iter()
        .map(|name| match by_name.get(name).map(Vec::as_slice) {
            Some([f]) => Ok(*f),
            found => Err(raised(format!(
                "face name {name:?} maps to {} native faces, expected one",
                found.map_or(0, <[usize]>::len)
            ))),
        })
        .collect::<PyResult<Vec<_>>>()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn python_angular_tolerances_round_trip_as_ap242_degree_bounds() {
        let bytes = draftwright::model::read_step_file(
            &std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../../tests/fixtures/pmi/step/nist_ctc_01_asme1_ap242-e1.stp.gz"),
        )
        .unwrap();
        let (part, analysis) = draftwright::model::analyse_native_bytes("angle", &bytes).unwrap();
        let defs = haecceity::step::read_part_definitions(&bytes).unwrap();
        let doc = haecceity::p21::Document::parse(bytes.clone()).unwrap();
        let imported = read(&doc, &defs).unwrap();
        let report = draftwright::pmi::report(&doc, &defs, &part, &imported).unwrap();
        let angle = report
            .records
            .iter()
            .find(|r| r.kind == "angular" && r.blockers.is_empty())
            .unwrap();
        let (first, second) = (angle.face_groups[0].clone(), angle.face_groups[1].clone());
        let mut p = Pmi::new(part.faces.len());
        let geometry = Geometry {
            mapping: (0..part.faces.len()).collect(),
            product_mapping: (0..part.faces.len()).collect(),
            product: std::sync::Arc::new(haecceity::read_step(&bytes).unwrap()),
            part,
            analysis,
        };
        let nominal = geometry
            .angle(&mut p, first.clone(), second.clone())
            .unwrap();
        for limits in [false, true] {
            let (lower, upper) = if limits {
                (nominal - 0.5, nominal + 0.5)
            } else {
                (-0.5, 0.5)
            };
            p.angular_tolerance(
                first.clone(),
                second.clone(),
                &lower.to_string(),
                &upper.to_string(),
                limits,
            )
            .unwrap();
            let (edit, _) = write(
                &doc,
                &defs,
                &[(PartId(0), p.model.clone())],
                Mode::Replace,
                write::PresentationPolicy::RemovePresentation,
            )
            .unwrap();
            let bytes = doc.apply(&edit).unwrap().bytes;
            let defs = haecceity::step::read_part_definitions(&bytes).unwrap();
            let written = haecceity::p21::Document::parse(bytes).unwrap();
            let read = read(&written, &defs).unwrap();
            let d = &read.parts[0].dimensions[0];
            let bounds = match (&d.tolerance, limits) {
                (DimTolerance::Limits(bounds), true)
                | (DimTolerance::Deviations(bounds), false) => bounds,
                _ => panic!("angular tolerance changed kind"),
            };
            for (value, expected) in [(bounds.lower(), lower), (bounds.upper(), upper)] {
                let Quantity::Angle(angle) = &value.quantity else {
                    panic!("angular bound is not an angle")
                };
                assert_eq!(angle.unit, AngleUnit::Degree);
                assert_eq!(angle.value.to_f64(), expected);
            }
            let report = draftwright::pmi::report(&written, &defs, &geometry.part, &read).unwrap();
            let angular = report.records.iter().find(|r| r.kind == "angular").unwrap();
            assert!(angular.blockers.is_empty(), "{:?}", angular.blockers);
        }
    }

    #[test]
    fn caller_pmi_replays_engine_pins_across_byte_revisions_and_content_edits() {
        use draftwright::{model, pmi, session::Session};
        let bytes = include_bytes!("../../../tests/fixtures/parts/plate.step");
        let (part, mut analysis) = model::analyse_native_bytes("plate", bytes).unwrap();
        let mut p = Pmi::new(part.faces.len());
        p.note("FIRST PYTHON NOTE".into()).unwrap();
        p.finish(vec![], "Ra 3.2").unwrap();
        analysis.pmi = Some(pmi::Reading::Read(
            pmi::from_model(&part, &p.model).unwrap(),
        ));
        let mut session = Session::new(part, analysis.clone(), model::Spec::default()).unwrap();
        session.apply_incremental(&[], true).unwrap();
        let revision = [bytes.as_slice(), b"\n"].concat();
        let (revised_part, mut revised_analysis) =
            model::analyse_native_bytes("plate", &revision).unwrap();
        revised_analysis.pmi = Some(pmi::Reading::Read(
            pmi::from_model(&revised_part, &p.model).unwrap(),
        ));
        let retained = model::plan_incremental_source(
            session.part(),
            session.plan(),
            &revised_part,
            &revised_analysis,
            session.spec(),
        )
        .unwrap();
        assert!(
            retained
                .spec
                .engine_pins
                .as_ref()
                .unwrap()
                .geometry_sha256
                .is_some()
        );
        assert_ne!(
            retained.binding.source_sha256,
            analysis.binding.source_sha256
        );
        for edited in [false, true, true] {
            if edited && p.model.attributes.len() == 1 {
                p.note("ADDED PYTHON NOTE".into()).unwrap();
                p.finish(vec![], "Ra 1.6").unwrap();
            }
            let (plan, files) = draftwright::api::render_model(
                session.part(),
                &analysis,
                &p.model,
                &retained.spec,
                &["svg"],
                draftwright::api::DPI,
            )
            .unwrap();
            assert!(plan.spec.engine_pins.is_some());
            assert!(
                !plan.problems.iter().any(|p| matches!(
                    p.code.as_str(),
                    "engine_pins_replanned" | "pmi_not_lowered" | "pmi_not_rendered"
                )),
                "{:?}",
                plan.problems
            );
            assert_eq!(
                serde_json::to_value(&plan.views).unwrap(),
                serde_json::to_value(&retained.views).unwrap()
            );
            let svg = String::from_utf8(files[0].1.clone()).unwrap();
            assert!(svg.contains("FIRST PYTHON NOTE"));
            assert_eq!(svg.contains("ADDED PYTHON NOTE"), edited);
            assert!(svg.contains(if edited { "1.6" } else { "3.2" }));
        }
    }

    #[test]
    fn new_calls_use_existing_ap242_semantic_fields() {
        let mut p = Pmi::new(8);
        p.note("DEBURR".into()).unwrap();
        p.note("HARDEN".into()).unwrap();
        assert_eq!(p.model.attributes[0].name, "semantic text");
        assert_eq!(p.model.attributes[0].on, None);
        assert_eq!(
            p.model.attributes[0].items[0],
            ("general note".into(), AttributeValue::Text("DEBURR".into()))
        );
        assert_eq!(
            p.model.attributes[1].items[1],
            ("note index".into(), AttributeValue::Integer(2))
        );
        p.finish(vec![], "Ra 3.2").unwrap();
        p.finish(vec![0], "Ra 0.8").unwrap();
        p.finish(vec![], "Ra 1.6").unwrap();
        assert_eq!(p.model.surface_textures.len(), 2);
        let default = p
            .model
            .surface_textures
            .iter()
            .find(|t| t.on.is_none())
            .unwrap();
        assert_eq!(default.parameters[0].value.unit, LengthUnit::Micrometre);
        assert_eq!(default.parameters[0].value.value.to_f64(), 1.6);
        p.thread(vec![1], "M6".into(), false, Some("12"), vec!["5".into()])
            .unwrap();
        let thread = &p.model.threads[0];
        let extent = p
            .model
            .attributes
            .iter()
            .find(|a| a.name == "thread manufacturing")
            .unwrap();
        assert_eq!(extent.on, Some(NoteOwner::Feature(thread.feature)));
        assert_eq!(
            extent.items[1],
            (
                "minimum full thread".into(),
                AttributeValue::Measure(value("12").unwrap())
            )
        );
        p.knurl(vec![2], "straight", "0.8", "10", None).unwrap();
        assert_eq!(p.model.knurls[0].number_of_teeth, Some(Count(39)));
        p.model.validate().unwrap();
    }

    #[test]
    fn python_knurls_derive_the_count_and_round_trip_without_pitch_disagreement() {
        for (pattern, fixture, angle) in [
            ("straight", "spindle_turned_knurl", None),
            ("diamond", "spindle_diamond_knurl", Some("30")),
        ] {
            let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join(format!(
                "../../tests/fixtures/turned_knurl/{fixture}.step.gz"
            ));
            let bytes = draftwright::model::read_step_file(&path).unwrap();
            let part = haecceity::read_step(&bytes).unwrap();
            let defs = haecceity::step::read_part_definitions(&bytes).unwrap();
            let doc = haecceity::p21::Document::parse(bytes).unwrap();
            let original = read(&doc, &defs).unwrap();
            let stated = &original.parts[0].knurls[0];
            let wall = part
                .faces
                .iter()
                .position(|face| {
                    matches!(
                        face.surface, haecceity::geom::Surface::Cylinder { radius, .. }
                            if (2. * radius - stated.major_diameter.mm()).abs() < 1e-6
                    )
                })
                .unwrap();
            // Exercise both rounding directions, not just the fixture's own pitch.
            for pitch in ["0.8", "1", "1.2"] {
                let mut p = Pmi::new(part.faces.len());
                p.knurl(vec![wall], pattern, pitch, "24", angle).unwrap();
                let knurl = &p.model.knurls[0];
                let expected = std::f64::consts::PI * 24. / length(pitch).unwrap().mm();
                let count = knurl.number_of_teeth.unwrap();
                assert_eq!(f64::from(count.0), expected.round_ties_even());
                assert!((f64::from(count.0) - expected).abs() <= 1.);
                assert!(knurl.tooth_depth.is_none());
                assert!(knurl.root_fillet.is_none());
                // WR2 requires six parameters. Supply only the fixture's explicitly
                // stated tooth geometry at the export boundary; the Python declaration
                // remains pattern/pitch/diameter/derived count (plus diamond angle).
                let mut export = p.model;
                export.knurls[0].tooth_depth = stated.tooth_depth.clone();
                export.knurls[0].root_fillet = stated.root_fillet.clone();
                let (edit, _) = write(
                    &doc,
                    &defs,
                    &[(PartId(0), export)],
                    Mode::Replace,
                    write::PresentationPolicy::RemovePresentation,
                )
                .unwrap();
                let bytes = doc.apply(&edit).unwrap().bytes;
                let defs = haecceity::step::read_part_definitions(&bytes).unwrap();
                let written = haecceity::p21::Document::parse(bytes).unwrap();
                let imported = read(&written, &defs).unwrap();
                assert_eq!(imported.parts[0].knurls.len(), 1);
                let knurl = &imported.parts[0].knurls[0];
                assert_eq!(knurl.number_of_teeth, Some(count));
                assert_eq!(knurl.diametral_pitch.mm(), length(pitch).unwrap().mm());
                assert_eq!(knurl.major_diameter.mm(), 24.);
                let report = draftwright::pmi::report(&written, &defs, &part, &imported).unwrap();
                let record = report.records.iter().find(|r| r.kind == "knurl").unwrap();
                assert!(
                    record.blockers.is_empty(),
                    "{pattern}/{pitch}: {:?}",
                    record.blockers
                );
            }
        }
    }

    #[test]
    fn notes_default_and_thread_extent_round_trip_through_the_ap242_reader() {
        let bytes = std::fs::read(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../tests/fixtures/turned_knurl/plain_shaft_thread.step"
        ))
        .unwrap();
        let part = haecceity::read_step(&bytes).unwrap();
        let wall = part
            .faces
            .iter()
            .position(|face| matches!(face.surface, haecceity::geom::Surface::Cylinder { .. }))
            .unwrap();
        let mut p = Pmi::new(part.faces.len());
        p.note("DEBURR".into()).unwrap();
        p.note("NO LACQUER".into()).unwrap();
        p.finish(vec![], "Ra 3.2").unwrap();
        p.thread(
            vec![wall],
            "M10".into(),
            true,
            Some("12"),
            vec!["10".into()],
        )
        .unwrap();
        // Full-depth API declarations retain main's fields. The eventual
        // exporter supplies these structural AP242 WR12/parameter-count fields.
        assert!(p.model.threads[0].partial_area.is_none());
        assert!(p.model.threads[0].nominal_size.is_none());
        let mut export = p.model.clone();
        export.threads[0].partial_area = Some(export.threads[0].feature);
        export.threads[0].nominal_size = Some(export.threads[0].major_diameter.clone());
        let defs = haecceity::step::read_part_definitions(&bytes).unwrap();
        let doc = haecceity::p21::Document::parse(bytes).unwrap();
        let (edit, _) = write(
            &doc,
            &defs,
            &[(PartId(0), export)],
            Mode::Replace,
            write::PresentationPolicy::RemovePresentation,
        )
        .unwrap();
        let written = doc.apply(&edit).unwrap().bytes;
        let defs = haecceity::step::read_part_definitions(&written).unwrap();
        let doc = haecceity::p21::Document::parse(written).unwrap();
        let read = read(&doc, &defs).unwrap();
        assert_eq!(read.parts[0].surface_textures, p.model.surface_textures);
        let notes = |model: &PartPmi| {
            model
                .attributes
                .iter()
                .filter(|a| a.name == "semantic text")
                .cloned()
                .collect::<Vec<_>>()
        };
        assert_eq!(notes(&read.parts[0]), notes(&p.model));
        let attributes = &read.parts[0].attributes;
        let thread = &read.parts[0].threads[0];
        let extent = attributes
            .iter()
            .find(|a| a.name == "thread manufacturing")
            .unwrap();
        assert_eq!(extent.on, Some(NoteOwner::Feature(thread.feature)));
        let Some(AttributeValue::Measure(length)) = extent
            .items
            .iter()
            .find(|(name, _)| name == "thread length")
            .map(|(_, value)| value)
        else {
            panic!("missing typed thread length: {:?}", extent.items);
        };
        let length = length.as_length().unwrap();
        assert_eq!(length.unit, LengthUnit::Millimetre);
        // STEP spells an integer REAL as `12.`; Decimal retains that spelling.
        assert_eq!(length.mm(), 12.0);
        assert_eq!(thread.major_diameter.mm(), 10.0);
        assert_eq!(thread.nominal_size.as_ref().unwrap().mm(), 10.0);
        assert!(thread.partial_area.is_some());
    }

    #[test]
    fn full_depth_threads_keep_main_fields_and_internal_depth_round_trips() {
        let bytes = std::fs::read(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../tests/fixtures/parts/plate.step"
        ))
        .unwrap();
        let part = haecceity::read_step(&bytes).unwrap();
        let wall = part.faces.iter().position(|face| matches!(
            face.surface, haecceity::geom::Surface::Cylinder { radius, .. } if (radius - 3.).abs() < 1e-6
        )).unwrap();
        let mut p = Pmi::new(part.faces.len());
        p.thread(vec![wall], "M7".into(), false, None, vec!["6".into()])
            .unwrap();
        assert!(p.model.threads[0].partial_area.is_none());
        assert!(p.model.threads[0].nominal_size.is_none());
        p.thread(vec![wall], "M7".into(), false, Some("2"), vec!["6".into()])
            .unwrap();
        let thread = &p.model.threads[0];
        assert_eq!(thread.partial_area, Some(thread.feature));
        assert_eq!(thread.nominal_size.as_ref().unwrap().mm(), 7.);
        let defs = haecceity::step::read_part_definitions(&bytes).unwrap();
        let doc = haecceity::p21::Document::parse(bytes).unwrap();
        let (edit, _) = write(
            &doc,
            &defs,
            &[(PartId(0), p.model)],
            Mode::Replace,
            write::PresentationPolicy::RemovePresentation,
        )
        .unwrap();
        let bytes = doc.apply(&edit).unwrap().bytes;
        let defs = haecceity::step::read_part_definitions(&bytes).unwrap();
        let doc = haecceity::p21::Document::parse(bytes).unwrap();
        let read = read(&doc, &defs).unwrap();
        let model = &read.parts[0];
        let thread = &model.threads[0];
        assert_eq!(thread.side, ThreadSide::Internal);
        assert!(thread.partial_area.is_some());
        let attribute = model
            .attributes
            .iter()
            .find(|a| a.name == "thread manufacturing")
            .unwrap();
        assert_eq!(attribute.on, Some(NoteOwner::Feature(thread.feature)));
        let AttributeValue::Measure(depth) = &attribute
            .items
            .iter()
            .find(|(name, _)| name == "minimum full thread")
            .unwrap()
            .1
        else {
            panic!("minimum full thread must be a typed length")
        };
        assert_eq!(depth.as_length().unwrap().unit, LengthUnit::Millimetre);
        assert_eq!(depth.as_length().unwrap().mm(), 2.);
    }

    fn rejected_without_edits(pmi: &mut Pmi, edit: impl FnOnce(&mut Pmi) -> PyResult<()>) {
        let before = pmi.model.clone();
        assert!(edit(pmi).is_err());
        // This includes the feature inventory, not just the projected requirements.
        assert_eq!(pmi.model, before);
    }

    #[test]
    fn rejected_declarations_do_not_leave_orphan_features() {
        Python::initialize();
        let mut p = Pmi::new(8);
        p.datum("A", vec![0]).unwrap();
        p.datum("B", vec![1, 2]).unwrap();
        rejected_without_edits(&mut p, |p| {
            p.control(vec![0], "parallelism", "0.1", vec!["A".into()], true)
        });
        rejected_without_edits(&mut p, |p| {
            p.control(
                vec![2, 1, 2],
                "profile_surface",
                "0.1",
                vec!["B".into()],
                false,
            )
        });
        // A partly overlapping datum is invalid in either direction. The new
        // target group must also be rolled back when rejection follows allocation.
        for faces in [vec![2, 3], vec![1], vec![1, 2, 3]] {
            rejected_without_edits(&mut p, |p| {
                let error = p
                    .control(faces, "profile_surface", "0.1", vec!["B".into()], false)
                    .unwrap_err();
                assert!(error.to_string().contains("datum B shares faces"));
                Err(error)
            });
        }
        rejected_without_edits(&mut p, |p| {
            p.control(vec![3], "parallelism", "0.1", vec!["C".into()], true)
        });
        rejected_without_edits(&mut p, |p| {
            p.dimension(vec![3], Some(vec![99]), "30", "-0.1", "0.1", false)
        });
        rejected_without_edits(&mut p, |p| {
            p.thread(vec![3], "M12".into(), true, Some("30"), vec!["10".into()])
        });
        rejected_without_edits(&mut p, |p| {
            p.thread(vec![3], "M10".into(), true, None, vec!["10".into()])
        });
        rejected_without_edits(&mut p, |p| {
            p.thread(
                vec![3],
                "M10".into(),
                true,
                Some("bad length"),
                vec!["10".into()],
            )
        });
        rejected_without_edits(&mut p, |p| {
            p.knurl(vec![3], "straight", "0.8", "10", Some("30"))
        });
        p.fit(vec![3], "10", "h6").unwrap();
        // A new, overlapping face group is rejected after tentative feature creation.
        rejected_without_edits(&mut p, |p| {
            p.thread(
                vec![3, 4],
                "M10".into(),
                true,
                Some("30"),
                vec!["10".into(), "10".into()],
            )
        });
        p.thread(vec![5], "M10".into(), true, Some("30"), vec!["10".into()])
            .unwrap();
        rejected_without_edits(&mut p, |p| p.fit(vec![5, 6], "10", "h6"));
        rejected_without_edits(&mut p, |p| {
            p.dimension(vec![5, 6], None, "10", "-0.1", "0.1", false)
        });
        rejected_without_edits(&mut p, |p| {
            p.knurl(vec![5, 6], "straight", "0.8", "10", None)
        });
    }

    #[test]
    fn orientation_zone_distinguishes_axes_from_planes() {
        let mut p = Pmi::new(6);
        p.datum("A", vec![0]).unwrap();
        for (kind, offset) in [("perpendicularity", 1), ("parallelism", 3)] {
            for (cylindrical, face) in [(true, offset), (false, offset + 1)] {
                p.control(vec![face], kind, "0.05", vec!["A".into()], cylindrical)
                    .unwrap();
                let zone = &p.model.tolerances.last().unwrap().zone;
                if cylindrical {
                    assert_eq!(zone.as_ref().unwrap().form, ZoneForm::WithinACylinder);
                } else {
                    assert!(zone.is_none());
                }
            }
        }
    }
}
