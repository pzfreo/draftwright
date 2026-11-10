"""Explicit migration errors for the archived Python package's public surface."""
import importlib.abc
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
# Generated once from every .py module and directory in draftwright-python
# src/draftwright at 6f6a27fea57fb19a9adcdc20257dc0e60a1be309; exclude __init__.py / py.typed.
OLD_MODULES = frozenset([
    '_build_profile', '_core', '_geometry', '_pmi_datum_geometry',
    '_pmi_linear_geometry', '_pmi_part21', '_pmi_schema', '_pmi_support_blockers',
    '_pmi_topology', '_warnings', 'analysis', 'angular_geometry',
    'annotate', 'annotation_layout_profile', 'annotations', 'audit',
    'auxiliary_layout', 'blend_contract', 'build_once', 'build_options',
    'build_policy', 'builder', 'cli', 'compose',
    'contract_values', 'document', 'document_evidence', 'document_input',
    'drawing', 'drawing_diagnostics', 'drawing_evidence', 'drawing_export',
    'drawing_state', 'drawing_tables', 'evaluation', 'explicit_scale',
    'export', 'family_table', 'feature_identity', 'fits',
    'fixed_ink_cache', 'fonts', 'inspection', 'inspection_contract',
    'intent_drain', 'intent_routing', 'intents', 'layout',
    'layout_safety', 'layout_scheme', 'layout_selection', 'leader_policy',
    'linting', 'location_contract', 'make_drawing', 'measurement_support',
    'model', 'obligations', 'oriented_slot_contract', 'pdf_text',
    'plate_correspondence', 'pmi', 'profile_angles', 'progress',
    'projection', 'recogniser_contract', 'recogniser_policy', 'recogniser_schema',
    'recognition', 'recognition_cache', 'recognition_frame', 'recognition_ownership',
    'registry', 'repair', 'replay_assessment', 'reporting',
    'score', 'section_recess_contract', 'section_scale_recovery', 'sheet',
    'sheet_emit', 'sheet_feature_lines', 'sheet_features', 'sheet_layout_controls',
    'sheet_metadata', 'sheet_object_source', 'sheet_views', 'view_plan',
])


def removed(name):
    return name in OLD_ALL and name != "make_drawing"


def message(name):
    return (
        f"{name} was part of the Python draftwright 0.4 API, which 0.6 does not include; "
        "pin draftwright<0.6 to keep using it. The Rust engine is driven by a Spec: "
        "https://github.com/pzfreo/draftwright/blob/main/docs/model.md. "
        "For the supported one-liner use from draftwright import make_drawing."
    )


# AttributeError gained extra C instance fields in CPython 3.10; its layout
# conflicts with ImportError's on newer CPython. Use the dual type where legal,
# otherwise preserve both behaviours with errors selected at the API boundary.
try:
    class MigrationError(ImportError, AttributeError):
        """A removed Python 0.4 API on interpreters supporting both bases."""
except TypeError:
    class MigrationError(Exception):
        """Common base for migration errors across attribute and import access."""

    class AttributeMigrationError(MigrationError, AttributeError):
        pass

    class ImportMigrationError(MigrationError, ImportError):
        pass
else:
    AttributeMigrationError = ImportMigrationError = MigrationError


class _LegacyFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith("draftwright."):
            relative = fullname[len("draftwright."):]
            if relative.split(".")[0] in OLD_MODULES or removed(relative):
                # Match the message from getattr(package, name) exactly for old
                # public names imported with `from draftwright import name`.
                name = relative if removed(relative) else fullname
                raise ImportMigrationError(message(name))
        return None


def install():
    if not any(isinstance(finder, _LegacyFinder) for finder in sys.meta_path):
        sys.meta_path.append(_LegacyFinder())
