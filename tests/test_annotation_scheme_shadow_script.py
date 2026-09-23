import importlib.machinery
import importlib.util
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "annotation-scheme-shadow"


def _load_script():
    loader = importlib.machinery.SourceFileLoader("annotation_scheme_shadow_script", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_isolated_shadow_runner_returns_json_friendly_analysis():
    fixture = Path(__file__).parent / "fixtures" / "ap242_single_cylinder_diameter.step"

    result = _load_script().analyse_isolated(fixture, pmi="annotate", timeout=30)

    assert result["status"] == "ok"
    assert result["path"] == str(fixture)
    assert result["feature_count"] > 0
    assert result["scale"] > 0
    assert result["shadow"]["unplanned_count"] >= 0
    assert isinstance(result["shadow"]["corridors"], list)


def test_isolated_shadow_runner_reports_timeout_as_data():
    result = _load_script().analyse_isolated(Path("unused.step"), pmi="off", timeout=0.0)

    assert result == {
        "path": "unused.step",
        "status": "timeout",
        "error": "analysis exceeded 0 seconds",
    }


def test_checkout_executable_bootstraps_src_package():
    fixture = Path(__file__).parent / "fixtures" / "ap242_single_cylinder_diameter.step"

    completed = subprocess.run(
        [str(SCRIPT), "--timeout", "30", str(fixture)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert '"status": "ok"' in completed.stdout
