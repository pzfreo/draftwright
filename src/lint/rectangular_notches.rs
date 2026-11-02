//! Rectangular through-notch geometry read independently of planning.
use crate::bridge::{Feature, holes};
use crate::model::Analysis;

pub(crate) struct Notch {
    pub axis: usize,
    pub wide: usize,
    pub deep: usize,
    pub at: [f64; 3],
    pub mouth: [f64; 3],
    pub width: f64,
    pub depth: f64,
}

pub(crate) fn read(a: &Analysis, f: &Feature) -> Result<Notch, String> {
    let frame = holes::frame(a);
    let direction = |name: &str| {
        super::parameters::direction(&frame, f.parameters.get(name))
            .ok_or_else(|| format!("its record gives no {name}"))
    };
    let run = direction("axis")?;
    let opening = direction("opening")?;
    let axis = holes::principal(run).ok_or("its thickness is not along a principal axis")?;
    let deep = holes::principal(opening).ok_or("its opening is not along a principal axis")?;
    if axis == deep {
        return Err("its opening is not perpendicular to its thickness".into());
    }
    let wide = 3 - axis - deep;
    let at = super::parameters::place(&frame, f.parameters.get("at"))
        .ok_or("its record gives no bottom-wall centre")?;
    let size = |name| {
        super::parameters::size(f, name).and_then(|v| {
            if v.is_finite() && v > 1e-6 {
                Ok(v)
            } else {
                Err(format!("its {name} is not positive"))
            }
        })
    };
    let width = size("width")?;
    let depth = size("depth")?;
    let corner_radius = f
        .parameters
        .get("corner_radius")
        .and_then(serde_json::Value::as_f64)
        .unwrap_or(0.);
    if corner_radius < 0. || corner_radius >= depth || 2. * corner_radius >= width {
        return Err("its corner radius leaves no straight bottom or side-wall witness".into());
    }
    let mouth = [0, 1, 2].map(|k| at[k] + depth * opening[k]);
    Ok(Notch {
        axis,
        wide,
        deep,
        at,
        mouth,
        width,
        depth,
    })
}

impl Notch {
    /// Locate a physical mouth corner. Width then fixes the opposite wall;
    /// no implied centreline or prose locator is needed.
    pub(crate) fn locator(&self) -> [f64; 3] {
        let mut p = self.mouth;
        p[self.wide] -= 0.5 * self.width;
        p
    }
}
