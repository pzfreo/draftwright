"""Explicit multi-sheet authoring over one source and recognition authority."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType

from draftwright.builder import _detect_part_model_analysis
from draftwright.document_evidence import bind_document_claims
from draftwright.document_input import DocumentInput
from draftwright.progress import BuildCancelled, stage
from draftwright.reporting import (
    ReportUnavailableError,
    build_requirement_catalog,
    document_report,
    evaluate_document_requirements,
    match_requirement_catalog,
    write_json_document,
)
from draftwright.sheet import Sheet


class DocumentBuildError(RuntimeError):
    """A named member failed; no complete document result was produced."""

    def __init__(self, sheet_name, cause):
        self.sheet_name = sheet_name
        super().__init__(f"document sheet {sheet_name!r} failed: {cause}")


class DocumentResult:
    """Live member drawings and the authority under which they were built."""

    def __init__(self, source: DocumentInput, sheets: Mapping, catalog, member_recipes=None):
        self._source = source
        self._sheets = MappingProxyType(dict(sheets))
        self._catalog = catalog
        self._member_recipes = dict(member_recipes or {})

    @property
    def sheets(self):
        return self._sheets

    def _project_members(self, *, include_lint=False):
        members = []
        for name, drawing in self._sheets.items():
            try:
                snapshot = drawing.requirement_snapshot(include_lint=include_lint)
                self._source.validate_model(snapshot.part, snapshot.model)
                catalog = build_requirement_catalog(
                    evidence=snapshot.evidence,
                    ownership=snapshot.ownership,
                    model=snapshot.model,
                    part=snapshot.part,
                    requirement_outcomes=snapshot.outcomes,
                )
                aligned = match_requirement_catalog(self._catalog, catalog)
            except (ValueError, ReportUnavailableError) as exc:
                raise ReportUnavailableError(f"document sheet {name!r}: {exc}") from exc
            members.append((name, snapshot, aligned))
        return tuple(members)

    def _evaluate(self, *, include_lint=False):
        members = self._project_members(include_lint=include_lint)
        claims = tuple(
            bind_document_claims(name, drawing.measurement_snapshot(), drawing.registry)
            for name, drawing in self._sheets.items()
        )
        return evaluate_document_requirements(
            self._catalog,
            self._source.model(self._source.initial_features()),
            self._source.analysis.part,
            members,
            claims,
        )

    def report(self) -> dict[str, object]:
        """Read a schema-v4 document report over live members and one source catalog.

        IDs belong only to this report. A returned JSON value does not change when
        members are edited; call again for new evidence. Bounded clearance is not
        manufacturing readiness. Missing common authority raises ReportUnavailableError.
        """
        evaluation = self._evaluate(include_lint=True)
        resolved = {
            name: {
                "scale": drawing.scale,
                "page_mm": (drawing.page_w, drawing.page_h),
                "scale_decision": drawing.scale_decision,
                "view_decision": drawing.view_decision,
            }
            for name, drawing in self._sheets.items()
        }
        return document_report(
            catalog=self._catalog,
            evaluation=evaluation,
            model=self._source.model(self._source.initial_features()),
            source={
                "kind": "step",
                "name": self._source.source_name,
                "sha256": sha256(self._source.source_bytes).hexdigest(),
            },
            run_options={"pmi": self._source.analysis.pmi_mode, "frame": "raw"},
            member_recipes=self._member_recipes,
            resolved=resolved,
        )

    def write_report(self, path) -> str:
        """Atomically write the strict document report without exporting drawing ink."""
        return write_json_document(self.report(), path)


class Document:
    """Author explicit sheets against an immutable common physical-feature membership."""

    def __init__(self, source: DocumentInput):
        self._source = source
        self._catalog = build_requirement_catalog(
            evidence=source.analysis.recognition_evidence,
            ownership=source.analysis.recognition_ownership,
            model=source.model(source.initial_features()),
            part=source.analysis.part,
        )
        self._sheets: dict[str, Sheet] = {}

    @classmethod
    def from_part(cls, path, *, pmi="off"):
        """Read one STEP byte snapshot and retain its one raw detection acquisition."""
        resolved = Path(path).resolve()
        source_bytes = resolved.read_bytes()
        with TemporaryDirectory(prefix="draftwright-document-") as directory:
            snapshot = Path(directory) / resolved.name
            snapshot.write_bytes(source_bytes)
            _model, analysis = _detect_part_model_analysis(snapshot, pmi=pmi)
        try:
            source = DocumentInput(analysis, Path(path).name, source_bytes)
        except ValueError as exc:
            raise ReportUnavailableError(str(exc)) from exc
        return cls(source)

    @property
    def features(self):
        return self._source.features

    def sheet(self, name: str, **options) -> Sheet:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("a document sheet needs a nonempty name")
        if name in self._sheets:
            raise ValueError(f"duplicate document sheet {name!r}")
        sheet = Sheet(self._source.analysis.part, **options)
        sheet._bind_document(self._source)
        self._sheets[name] = sheet
        return sheet

    def build(self) -> DocumentResult:
        if not self._sheets:
            raise ValueError("a document needs at least one sheet")
        members = []
        for name, sheet in tuple(self._sheets.items()):
            try:
                snapshot = sheet._snapshot_for_document()
                members.append((name, snapshot, snapshot._document_recipe()))
            except Exception as exc:
                raise DocumentBuildError(name, exc) from exc
        drawings = {}
        for name, sheet, _recipe in members:
            try:
                with stage(f"document sheet {name}"):
                    drawings[name] = sheet.build()
            except BuildCancelled as exc:
                exc.diagnostic = {**exc.diagnostic, "sheet": name}
                exc.completed_result = None
                raise
            except Exception as exc:
                raise DocumentBuildError(name, exc) from exc
        return DocumentResult(
            self._source,
            drawings,
            self._catalog,
            {name: recipe for name, _sheet, recipe in members},
        )
