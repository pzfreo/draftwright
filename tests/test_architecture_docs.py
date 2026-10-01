"""Drift guard for documents that present current architecture.

Frozen ADRs and historical roadmaps are deliberately outside this scope.

Current-architecture documents must not cite source line numbers, which rot on
every edit. The dependency guide must also match package metadata. Former
phrase-absence assertions (stale ADR 1 (was 0008) / ADR 2 (was 0009) references)
were retired by the #1222 guard audit.
"""

import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

_ROOT = Path(__file__).resolve().parent.parent


def test_current_architecture_docs_have_no_source_line_anchors():
    for relative in (
        "docs/architecture.md",
        "docs/adr/0001-compiler-pipeline.md",
        "docs/adr/0002-sheet-layout-and-view-planning.md",
        "docs/adr/0003-recognition-boundary.md",
        "docs/adr/0004-declared-intent.md",
        "docs/adr/0005-trust-and-honest-failure.md",
    ):
        text = (_ROOT / relative).read_text(encoding="utf-8")
        assert "orchestrator.py:" not in text, relative


def test_agent_dependency_requirements_match_package_metadata():
    """The agent's CAD dependency specs mirror the published project metadata."""
    project = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    guide = (
        (_ROOT / "AGENTS.md")
        .read_text(encoding="utf-8")
        .split("## Dependencies", 1)[1]
        .split("## Testing", 1)[0]
    )
    names = ("build123d", "build123d-drafting-helpers", "quiddity")

    def is_cad_requirement(value: str) -> bool:
        return any(re.match(rf"^{re.escape(name)}(?=[<=>!~])", value) for name in names)

    expected = tuple(req for req in project["dependencies"] if is_cad_requirement(req))
    documented = tuple(
        item for item in re.findall(r"`([^`]+)`", guide) if is_cad_requirement(item)
    )
    assert documented == expected, f"AGENTS.md CAD dependencies {documented} != {expected}"
