//! Authored metric external spur gears: Python model/ir.py:953 and annotations/gears.py.
//! Only the author supplies normative data. Full-wire recognition remains lint evidence.
use std::borrow::Cow;

use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::bridge::{Recognition, Record};
use crate::model::{Analysis, Invalid, Spec};
use crate::tables::TableSpec;

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Frame {
    pub origin: [f64; 3],
    pub axis: String,
}

/// Python's complete requirement set, anchored to a feature or face of this part.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct GearSpec {
    pub on: String,
    pub frame: Frame,
    pub tooth_count: i64,
    pub module: f64,
    pub pressure_angle: f64,
    pub profile_shift: f64,
    pub face_width: f64,
    pub tooth_thickness: f64,
    /// Lower and upper deviations, mm (not absolute limits).
    pub tooth_thickness_tolerance: [f64; 2],
    pub flank_tolerance_class: i64,
}

impl<'de> Deserialize<'de> for GearSpec {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        use serde::de::Error;
        let mut fields = serde_json::Map::<String, serde_json::Value>::deserialize(deserializer)?;
        fn take<T: serde::de::DeserializeOwned>(
            fields: &mut serde_json::Map<String, serde_json::Value>,
            key: &str,
        ) -> Result<T, String> {
            let value = fields
                .remove(key)
                .ok_or_else(|| format!("gear requires {key}"))?;
            serde_json::from_value(value).map_err(|e| format!("gear {key}: {e}"))
        }
        macro_rules! field {
            ($key:literal) => {
                take(&mut fields, $key).map_err(D::Error::custom)?
            };
        }
        let mut gear = Self {
            on: field!("on"),
            frame: field!("frame"),
            tooth_count: field!("tooth_count"),
            module: field!("module"),
            pressure_angle: field!("pressure_angle"),
            profile_shift: field!("profile_shift"),
            face_width: field!("face_width"),
            tooth_thickness: field!("tooth_thickness"),
            tooth_thickness_tolerance: field!("tooth_thickness_tolerance"),
            flank_tolerance_class: field!("flank_tolerance_class"),
        };
        if let Some(key) = fields.keys().next() {
            return Err(D::Error::custom(format!("unknown gear field {key:?}")));
        }
        gear.frame.axis = gear.frame.axis.to_lowercase();
        gear.check().map_err(|e| D::Error::custom(e.message))?;
        Ok(gear)
    }
}

fn invalid(message: impl Into<String>) -> Invalid {
    Invalid {
        path: None,
        also: Vec::new(),
        message: message.into(),
        choices: Vec::new(),
    }
}

impl GearSpec {
    pub fn check(&self) -> Result<(), Invalid> {
        if !self.frame.origin.iter().all(|v| v.is_finite()) {
            return Err(invalid(
                "gear frame origin must contain three finite numbers",
            ));
        }
        if !["x", "y", "z"].contains(&self.frame.axis.as_str()) {
            return Err(invalid("gear frame axis must be x, y or z"));
        }
        if !(5..=1000).contains(&self.tooth_count) {
            return Err(invalid("tooth_count must be from 5 to 1000 for ISO 1328-1"));
        }
        for (name, value) in [
            ("module", self.module),
            ("face_width", self.face_width),
            ("tooth_thickness", self.tooth_thickness),
        ] {
            if !value.is_finite() || value <= 0.0 {
                return Err(invalid(format!("{name} must be finite and positive")));
            }
        }
        if !(0.5..=70.0).contains(&self.module) {
            return Err(invalid(
                "module must be from 0.5 mm to 70 mm for ISO 1328-1",
            ));
        }
        if !(4.0..=1200.0).contains(&self.face_width) {
            return Err(invalid(
                "face_width must be from 4 mm to 1200 mm for ISO 1328-1",
            ));
        }
        if !(5.0..=15000.0).contains(&(self.module * self.tooth_count as f64)) {
            return Err(invalid(
                "module × tooth_count must give a 5 mm to 15000 mm reference diameter for ISO 1328-1",
            ));
        }
        if !self.pressure_angle.is_finite()
            || self.pressure_angle <= 0.0
            || self.pressure_angle >= 90.0
        {
            return Err(invalid(
                "pressure_angle must be finite and between 0 and 90 degrees",
            ));
        }
        if !self.profile_shift.is_finite() {
            return Err(invalid("profile_shift must be finite"));
        }
        let [lower, upper] = self.tooth_thickness_tolerance;
        if !lower.is_finite() || !upper.is_finite() {
            return Err(invalid(
                "tooth_thickness_tolerance must contain two finite deviations",
            ));
        }
        if lower > upper {
            return Err(invalid(
                "tooth_thickness_tolerance lower deviation must not exceed upper",
            ));
        }
        if self.tooth_thickness + lower <= 0.0 {
            return Err(invalid(
                "tooth_thickness tolerance must retain a positive lower limit",
            ));
        }
        if !(1..=11).contains(&self.flank_tolerance_class) {
            return Err(invalid(
                "flank_tolerance_class must be an integer from 1 to 11",
            ));
        }
        Ok(())
    }
}

pub fn check(gears: &[GearSpec]) -> Result<(), Invalid> {
    for (i, gear) in gears.iter().enumerate() {
        gear.check()
            .map_err(|e| invalid(format!("gears/{i}: {}", e.message)))?;
    }
    Ok(())
}

/// Like Python float repr, except integral floats print as integers, including zero.
pub fn authored(value: f64) -> String {
    if value.fract() == 0.0 {
        return if value == 0.0 {
            "0".into()
        } else {
            format!("{value:.0}")
        };
    }
    if value.abs() < 1e-4 {
        let s = format!("{value:e}");
        // Invariant: The small nonzero finite magnitude branch uses Rust scientific notation with an exponent.
        let (mantissa, exp) = s.split_once('e').unwrap();
        // Invariant: Rust scientific notation writes a signed integer exponent fitting i32.
        let exp: i32 = exp.parse().unwrap();
        return format!("{mantissa}e{exp:+03}");
    }
    value.to_string()
}

pub fn rows(g: &GearSpec) -> Vec<Vec<String>> { rows_in(g, crate::units::Units::Mm) }

fn rows_in(g: &GearSpec, units: crate::units::Units) -> Vec<Vec<String>> {
    let geometry = "ISO 21771-1:2024";
    let length = |v| if units.is_mm() {authored(v)} else {units.format(v)};
    let unit = units.symbol();
    let signed = |v: f64| {
        if v > 0.0 {
            format!("+{}", if units.is_mm() {authored(v)} else {units.magnitude(v)})
        } else {
            if units.is_mm() {authored(v)} else {units.magnitude(v)}
        }
    };
    let [lower, upper] = g.tooth_thickness_tolerance;
    [
        ["GEAR DATA".into(), "VALUE".into(), "BASIS".into()],
        [
            "TYPE".into(),
            "METRIC EXTERNAL SPUR".into(),
            geometry.into(),
        ],
        ["BASIC RACK".into(), "STANDARD".into(), "ISO 53:1998".into()],
        ["TEETH z".into(), g.tooth_count.to_string(), geometry.into()],
        [
            "MODULE m".into(),
            format!("{} {unit}", length(g.module)),
            "ISO 54:1996".into(),
        ],
        [
            "PRESSURE ANGLE".into(),
            format!("{} deg", authored(g.pressure_angle)),
            geometry.into(),
        ],
        [
            "PROFILE SHIFT x".into(),
            authored(g.profile_shift),
            geometry.into(),
        ],
        [
            "FACE WIDTH b".into(),
            format!("{} {unit}", length(g.face_width)),
            geometry.into(),
        ],
        [
            "TOOTH THICKNESS s".into(),
            format!(
                "{} {}/{} {unit}",
                length(g.tooth_thickness),
                signed(upper),
                signed(lower)
            ),
            "ISO 21771-2:2025".into(),
        ],
        [
            "FLANK CLASS".into(),
            g.flank_tolerance_class.to_string(),
            "ISO 1328-1:2013".into(),
        ],
    ]
    .into_iter()
    .map(Vec::from)
    .collect()
}

/// Lower declarations into the independent requirement inventory, like authored PMI.
/// Coordinates are the part's; the anchor provides ownership, never tooth data.
pub(crate) fn effective<'a>(
    mut analysis: Cow<'a, Analysis>,
    spec: &Spec,
) -> Result<Cow<'a, Analysis>, Invalid> {
    check(&spec.gears)?;
    if spec.gears.is_empty()
        || analysis
            .recognition
            .as_ref()
            .is_some_and(|r| r.evidence.iter().any(|e| e.family == "authored_gears"))
    {
        return Ok(analysis);
    }
    if spec
        .binding
        .as_ref()
        .is_some_and(|b| b.source_sha256 != analysis.binding.source_sha256)
    {
        return Err(invalid(
            "Gear anchors belong to another revision; reattach gears to this part.",
        ));
    }
    let mut records = Vec::new();
    for (i, g) in spec.gears.iter().enumerate() {
        let faces = if g.on.starts_with("f:") {
            let feature = analysis
                .features
                .iter()
                .find(|f| f.id == g.on)
                .ok_or_else(|| invalid(format!("gears/{i}/on: unknown feature {:?}", g.on)))?;
            if !["turned_step", "turned_body", "cylindrical_boss", "boss"]
                .contains(&feature.kind.as_str())
            {
                return Err(invalid(format!(
                    "gears/{i}/on: use a turned/cylindrical feature or face:<n>"
                )));
            }
            // The refusal names gears/<i>, not a features entry.
            let faces = crate::spec_gdt::feature_faces(&analysis, &g.on).map_err(|mut e| {
                e.path = None;
                e
            })?;
            if !faces.iter().any(|&f| {
                analysis
                    .face_attachments
                    .get(f)
                    .is_some_and(|a| a.surface.as_deref() == Some("cylinder"))
            }) {
                return Err(invalid(format!(
                    "gears/{i}/on must anchor a cylindrical feature; use a face anchor for a toothed profile"
                )));
            }
            faces
        } else {
            // The refusal names gears/<i>, not a faces entry.
            crate::spec_gdt::named_faces(&analysis, &g.on).map_err(|mut e| {
                e.path = None;
                e
            })?
        };
        if faces.is_empty() {
            return Err(invalid(format!("gears/{i}/on has no faces")));
        }
        let mut parameters = serde_json::to_value(g)
            // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
            .unwrap()
            .as_object()
            // Invariant: GearSpec derives serialization as an object.
            .unwrap()
            .clone();
        parameters.insert("id".into(), json!(format!("gear:{}", i + 1)));
        records.push(Record {
            family: "authored_gears".into(),
            subtype: "external_spur_gear".into(),
            faces,
            parameters,
        });
    }
    analysis
        .to_mut()
        .recognition
        .get_or_insert_with(|| Recognition {
            recogniser: "authored requirements".into(),
            frame: crate::bridge::native::IDENTITY,
            evidence: Vec::new(),
            refusals: Vec::new(),
            patterns: Vec::new(),
        })
        .evidence
        .extend(records);
    Ok(analysis)
}

pub fn tables(analysis: &Analysis) -> Vec<TableSpec> {tables_in(analysis, crate::units::Units::Mm)}

pub(crate) fn tables_in(analysis: &Analysis, units: crate::units::Units) -> Vec<TableSpec> {
    analysis
        .recognition
        .iter()
        .flat_map(|r| &r.evidence)
        .filter(|r| r.family == "authored_gears")
        .filter_map(|r| {
            let mut p = r.parameters.clone();
            let id = p.remove("id")?.as_str()?.strip_prefix("gear:")?.to_string();
            let g: GearSpec = serde_json::from_value(p.into()).ok()?;
            Some(TableSpec {
                id: format!("gear_data_{id}"),
                rows: rows_in(&g, units).into_iter().map(|r| json!(r)).collect(),
                prefer: Some("tr".into()),
                sheet: None,
            })
        })
        .collect()
}
