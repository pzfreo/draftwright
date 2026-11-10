"""Explicit migration errors for the archived Python package's public surface."""
import importlib.abc
import importlib.util
import sys

# draftwright-python main, src/draftwright/__init__.py::__all__.
OLD_ALL = frozenset([
    "BuildEvent", "BuildCancelled", "observe_build", "INSPECTION_SCHEMA",
    "INSPECTION_SCHEMA_VERSION", "Drawing", "Document", "DocumentResult",
    "DocumentBuildError", "SoftDeprecationWarning", "FeatureInfo", "FixedInkMeshCache",
    "InspectionUnavailableError", "LeaderRegionPolicy", "PmiExtractionReport", "PmiRecord",
    "PmiSourceEntity", "ReportUnavailableError", "Sheet", "ScaleIncompatibilityError",
    "ScaleCompletenessWarning", "ProjectionGeometryWarning", "ViewPlanIncomplete",
    "ViewConstraints", "ViewSpec", "build_drawing", "choose_scale", "extract_pmi",
    "extract_pmi_report", "inspect_step", "lint_feature_coverage", "make_drawing",
])
# Public providing modules, plus imports documented in the archived README/docs.
OLD_MODULES = frozenset([
    "builder", "drawing", "progress", "fixed_ink_cache", "reporting", "inspection",
    "sheet", "leader_policy", "document", "linting", "pmi", "compose", "view_plan",
    "_warnings", "make_drawing", "analysis", "annotations", "export", "layout",
    "projection", "model", "cli", "audit", "sheet_emit", "evaluation",
])


def removed(name):
    return name in OLD_ALL and name != "make_drawing"


def message(name):
    return (
        f"{name} was part of the Python draftwright 0.4 API, which 0.6 does not include; "
        "pin draftwright<0.6 to keep using it. The Rust engine is driven by a Spec: "
        "https://github.com/pzfreo/draftwright/blob/main/docs/model.md"
    )


class _LegacyFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith("draftwright.") and (fullname.split(".")[1] in OLD_MODULES or removed(fullname.split(".")[1])):
            return importlib.util.spec_from_loader(fullname, self, is_package=True)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        # Raising the same AttributeError during import covers both direct submodule
        # imports and `from draftwright.sheet import Sheet`, without silent stubs.
        raise AttributeError(message(module.__name__))


def install():
    if not any(isinstance(finder, _LegacyFinder) for finder in sys.meta_path):
        sys.meta_path.append(_LegacyFinder())
