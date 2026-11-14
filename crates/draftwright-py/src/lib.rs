mod layout;
mod part;

use pyo3::exceptions::PyException;
use pyo3::prelude::*;

pyo3::create_exception!(draftwright, DraftwrightError, PyException);

#[pyfunction]
fn cli(py: Python<'_>, args: Vec<std::ffi::OsString>) -> u8 {
    py.detach(|| draftwright::cli::main(args))
}

#[pyfunction]
fn mcp(py: Python<'_>, args: Vec<String>) -> u8 {
    py.detach(|| draftwright::cli::mcp::main(args))
}

#[pyfunction]
fn render(
    py: Python<'_>,
    step: String,
    out: Option<String>,
    spec: String,
    scale: Option<f64>,
    source: Option<String>,
) -> PyResult<(String, String)> {
    py.detach(|| draftwright::cli::make_drawing(step, out, &spec, scale, source.as_deref()))
        .map_err(|e| DraftwrightError::new_err(e.message))
}

fn raised(error: impl std::fmt::Display) -> PyErr {
    DraftwrightError::new_err(error.to_string())
}

#[pyfunction]
fn layout_spec(base: &str, patch: &str) -> PyResult<String> {
    layout::update(base, patch).map_err(raised)
}

#[pyfunction]
fn inspect_step(py: Python<'_>, step: String) -> PyResult<String> {
    py.detach(|| {
        draftwright::api::analyse_from(
            std::path::Path::new(&step),
            draftwright::api::Features::Native,
        )
    })
    .map(|v| serde_json::to_string(&v).expect("analysis serialises"))
    .map_err(raised)
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add("DraftwrightError", m.py().get_type::<DraftwrightError>())?;
    m.add_function(wrap_pyfunction!(cli, m)?)?;
    m.add_function(wrap_pyfunction!(mcp, m)?)?;
    m.add_function(wrap_pyfunction!(render, m)?)?;
    m.add_function(wrap_pyfunction!(layout_spec, m)?)?;
    m.add_function(wrap_pyfunction!(inspect_step, m)?)?;
    m.add_class::<part::Pmi>()?;
    m.add_class::<part::Geometry>()?;
    Ok(())
}
