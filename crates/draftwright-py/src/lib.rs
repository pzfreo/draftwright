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

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add("DraftwrightError", m.py().get_type::<DraftwrightError>())?;
    m.add_function(wrap_pyfunction!(cli, m)?)?;
    m.add_function(wrap_pyfunction!(mcp, m)?)?;
    m.add_function(wrap_pyfunction!(render, m)?)?;
    Ok(())
}
