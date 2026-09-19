"""Keep geometry-wide Quiddity scans at reviewed consumer boundaries."""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "src" / "draftwright"

# These functions inspect a part's geometry. Sequence-only projections such as
# recognise_hole_patterns are intentionally absent: reusing an existing record tuple is cheap
# and does not start another geometry-recognition lifecycle.
PART_SCANNERS = frozenset(
    {
        "analyse_cylinders",
        "build_recognition_evidence",
        "build_raw_recognition_result",
        "recognise_bosses",
        "recognise_chamfers",
        "recognise_countersinks",
        "recognise_double_d_bores",
        "recognise_fillets",
        "recognise_flats",
        "recognise_grooves",
        "recognise_holes",
        "recognise_paired_ramp_steps",
        "recognise_plates",
        "recognise_polygonal_bosses",
        "recognise_polygonal_stock",
        "recognise_rectangular_pads",
        "recognise_risers",
        "recognise_slots",
        "recognise_through_steps",
        "recognise_turned_steps",
    }
)

# Main-part builds should receive the run's RecognitionResult. These exceptions are the
# deliberately supported standalone entry points and compatibility fallbacks. Keeping the
# roster exact makes any new scan require a reasoned review instead of silently adding work.
ALLOWED_STANDALONE_CALLS = {
    (
        "analysis.py",
        "_raw_recognition",
        "build_recognition_evidence",
    ): "automatic analysis owns the main-part aggregate acquisition",
    (
        "analysis.py",
        "_classify_geometry",
        "analyse_cylinders",
    ): "analysis acquires the shared cylinder substrate before aggregate recognition",
    (
        "drawing.py",
        "Drawing._lint",
        "analyse_cylinders",
    ): "manual Drawing fallback has no Analysis substrate to reuse",
    (
        "evaluation/step_analysis.py",
        "_default_observers.observe_holes",
        "build_raw_recognition_result",
    ): "isolated STEP evaluation observer has no Drawing lifecycle",
    (
        "linting/coverage.py",
        "lint_feature_coverage",
        "analyse_cylinders",
    ): "public standalone lint fallback",
    (
        "linting/coverage.py",
        "lint_feature_coverage",
        "recognise_holes",
    ): "public standalone lint fallback",
    (
        "linting/coverage.py",
        "lint_location_coverage",
        "recognise_holes",
    ): "public standalone lint fallback (two cylinder-cache branches)",
    (
        "linting/coverage.py",
        "lint_location_coverage",
        "recognise_double_d_bores",
    ): "public standalone lint fallback",
    (
        "linting/coverage.py",
        "lint_prismatic_coverage",
        "recognise_rectangular_pads",
    ): "public standalone lint fallback",
    (
        "linting/coverage.py",
        "lint_prismatic_coverage",
        "build_raw_recognition_result",
    ): "public standalone lint fallback (two completeness ledgers)",
    (
        "linting/coverage.py",
        "lint_axial_coverage",
        "recognise_turned_steps",
    ): "public standalone lint fallback",
    (
        "model/declare.py",
        "_read_step_levels",
        "recognise_risers",
    ): "declared object may differ from the drawing's main part",
    (
        "model/detect.py",
        "build_part_model",
        "analyse_cylinders",
    ): "standalone build_part_model fallback",
    (
        "model/detect.py",
        "build_part_model",
        "build_recognition_evidence",
    ): "standalone build_part_model aggregate acquisition",
    (
        "model/detect.py",
        "build_part_model",
        "recognise_chamfers",
    ): "standalone build_part_model fallback",
    (
        "recognition_cache.py",
        "RecognitionCache.ensure",
        "build_recognition_evidence",
    ): "declared critique owns one lazy aggregate acquisition",
    (
        "model/detect.py",
        "build_part_model",
        "recognise_fillets",
    ): "standalone build_part_model fallback",
    (
        "model/detect.py",
        "build_part_model",
        "recognise_bosses",
    ): "standalone build_part_model fallback",
    (
        "model/detect.py",
        "build_part_model",
        "recognise_polygonal_stock",
    ): "standalone build_part_model fallback",
    (
        "model/detect.py",
        "build_part_model",
        "recognise_through_steps",
    ): "standalone build_part_model fallback",
    (
        "model/detect.py",
        "build_part_model",
        "recognise_plates",
    ): "standalone build_part_model fallback (two applicability branches)",
    (
        "model/detect.py",
        "build_part_model",
        "recognise_risers",
    ): "standalone build_part_model fallback",
    (
        "model/detect.py",
        "build_part_model",
        "recognise_holes",
    ): "standalone build_part_model fallback",
    (
        "model/detect.py",
        "build_part_model",
        "recognise_countersinks",
    ): "standalone build_part_model fallback",
    (
        "model/detect.py",
        "build_part_model",
        "recognise_double_d_bores",
    ): "standalone build_part_model fallback",
    (
        "model/detect.py",
        "build_part_model",
        "recognise_slots",
    ): "standalone build_part_model fallback",
    (
        "model/detect.py",
        "build_part_model",
        "recognise_polygonal_bosses",
    ): "standalone build_part_model fallback",
    (
        "model/detect.py",
        "build_part_model",
        "recognise_grooves",
    ): "standalone build_part_model fallback",
    (
        "model/detect.py",
        "build_part_model",
        "recognise_paired_ramp_steps",
    ): "standalone build_part_model fallback",
    (
        "model/detect.py",
        "build_part_model",
        "recognise_flats",
    ): "standalone build_part_model fallback",
}

ALLOWED_CALL_COUNTS = Counter({call: 1 for call in ALLOWED_STANDALONE_CALLS})
ALLOWED_CALL_COUNTS.update(
    {
        ("linting/coverage.py", "lint_location_coverage", "recognise_holes"): 1,
        ("linting/coverage.py", "lint_prismatic_coverage", "build_raw_recognition_result"): 1,
        ("model/detect.py", "build_part_model", "recognise_plates"): 1,
    }
)


class _Scanner(ast.NodeVisitor):
    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path
        self.imports: dict[str, str] = {}
        self.module_aliases: set[str] = set()
        self.evidence_aliases: set[str] = set()
        self.evidence_roots: set[str] = set()
        self.scopes: list[str] = []
        self.calls: Counter[tuple[str, str, str]] = Counter()

    def visit_Import(self, node: ast.Import) -> None:
        for name in node.names:
            if name.name == "quiddity":
                self.module_aliases.add(name.asname or name.name)
            elif name.name == "quiddity.evidence":
                if name.asname is not None:
                    self.evidence_aliases.add(name.asname)
                else:
                    self.evidence_roots.add("quiddity")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module in {"quiddity", "quiddity.evidence"}:
            for name in node.names:
                if name.name in PART_SCANNERS:
                    self.imports[name.asname or name.name] = name.name

    def _visit_scope(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        self.scopes.append(node.name)
        self.generic_visit(node)
        self.scopes.pop()

    visit_FunctionDef = _visit_scope
    visit_AsyncFunctionDef = _visit_scope
    visit_ClassDef = _visit_scope

    def visit_Call(self, node: ast.Call) -> None:
        scanner = None
        if isinstance(node.func, ast.Name):
            scanner = self.imports.get(node.func.id)
        elif (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in self.module_aliases | self.evidence_aliases
            and node.func.attr in PART_SCANNERS
        ):
            scanner = node.func.attr
        elif (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Attribute)
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id in self.evidence_roots
            and node.func.value.attr == "evidence"
            and node.func.attr in PART_SCANNERS
        ):
            scanner = node.func.attr
        if scanner is not None:
            scope = ".".join(self.scopes) or "<module>"
            self.calls[(self.relative_path, scope, scanner)] += 1
        self.generic_visit(node)


def _standalone_part_scans() -> Counter[tuple[str, str, str]]:
    calls: Counter[tuple[str, str, str]] = Counter()
    for path in SOURCE.rglob("*.py"):
        relative_path = path.relative_to(SOURCE).as_posix()
        scanner = _Scanner(relative_path)
        scanner.visit(ast.parse(path.read_text(encoding="utf-8")))
        calls.update(scanner.calls)
    return calls


def test_part_scans_are_confined_to_reviewed_standalone_boundaries() -> None:
    assert _standalone_part_scans() == ALLOWED_CALL_COUNTS
    assert all(reason.strip() for reason in ALLOWED_STANDALONE_CALLS.values())


def test_boundary_detects_direct_and_module_alias_calls() -> None:
    scanner = _Scanner("new_consumer.py")
    scanner.visit(
        ast.parse(
            "from quiddity import recognise_holes as holes\n"
            "from quiddity import analyse_cylinders\n"
            "from quiddity.evidence import build_recognition_evidence\n"
            "import quiddity as provider\n"
            "import quiddity.evidence as evidence\n"
            "import quiddity.evidence\n"
            "holes(part)\n"
            "analyse_cylinders(part)\n"
            "build_recognition_evidence(part)\n"
            "provider.recognise_slots(part)\n"
            "evidence.build_recognition_evidence(part)\n"
            "quiddity.evidence.build_recognition_evidence(part)\n"
        )
    )

    assert scanner.calls == Counter(
        {
            ("new_consumer.py", "<module>", "recognise_holes"): 1,
            ("new_consumer.py", "<module>", "analyse_cylinders"): 1,
            ("new_consumer.py", "<module>", "build_recognition_evidence"): 3,
            ("new_consumer.py", "<module>", "recognise_slots"): 1,
        }
    )
