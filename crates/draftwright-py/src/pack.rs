//! One native entry into the ordinary pack builder, with separate semantic PMI.
use super::{
    part::{Geometry, Pmi},
    raised,
};
use draftwright::pack::{PackSpec, ProductPmi};
use pyo3::prelude::*;
use serde_json::json;
use std::collections::BTreeMap;

#[pyfunction]
pub fn render_pack(
    py: Python<'_>,
    bytes: Vec<u8>,
    name: String,
    spec: String,
    products: Vec<(String, Py<Pmi>, Py<Geometry>)>,
    out: String,
    formats: Vec<String>,
) -> PyResult<String> {
    let spec: PackSpec = serde_json::from_str(&spec).map_err(raised)?;
    spec.check_title().map_err(raised)?;
    let mut pmi = BTreeMap::new();
    for (key, model, geometry) in products {
        let geometry = geometry.borrow(py);
        let model = geometry.bound_product(&model.borrow(py))?;
        let reference = geometry.product.clone();
        if pmi
            .insert(key.clone(), ProductPmi { model, reference })
            .is_some()
        {
            return Err(raised(format!("duplicate PMI product {key:?}")));
        }
    }
    py.detach(|| {
        let formats: Vec<_> = formats.iter().map(String::as_str).collect();
        let pack =
            draftwright::pack::build_bytes_with_pmi(&name, &bytes, &spec, &pmi).map_err(raised)?;
        let files = pack.encode(&formats).map_err(raised)?;
        let written =
            draftwright::api::write(std::path::Path::new(&out), &files).map_err(raised)?;
        Ok(json!({"written": written, "report": pack.report}).to_string())
    })
}

#[pyfunction]
pub fn pack_title(base: &str, patch: &str) -> PyResult<String> {
    let mut title: BTreeMap<String, String> = serde_json::from_str(base).map_err(raised)?;
    title.extend(serde_json::from_str::<BTreeMap<String, String>>(patch).map_err(raised)?);
    let spec = PackSpec {
        title,
        ..Default::default()
    };
    spec.check_title().map_err(raised)?;
    serde_json::to_string(&spec.title).map_err(raised)
}
