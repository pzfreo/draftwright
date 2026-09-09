"""Explicit multi-sheet authoring over one source and recognition authority."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType

from draftwright.builder import _detect_part_model_analysis
from draftwright.document_input import DocumentInput
from draftwright.progress import BuildCancelled, stage
from draftwright.reporting import (
    ReportUnavailableError,
    build_requirement_catalog,
    match_requirement_catalog,
)
from draftwright.sheet import Sheet


class DocumentBuildError(RuntimeError):
    """A named member failed; no complete document result was produced."""

    def __init__(self, sheet_name, cause):
        self.sheet_name = sheet_name
        super().__init__(f"document sheet {sheet_name!r} failed: {cause}")


class DocumentResult:
    """Live member drawings and the authority under which they were built."""

    def __init__(self, source: DocumentInput, sheets: Mapping, catalog):
        self._source = source
        self._sheets = MappingProxyType(dict(sheets))
        self._catalog = catalog

    @property
    def sheets(self):
        return self._sheets

    def _project_members(self):
        members = []
        for name, drawing in self._sheets.items():
            try:
                snapshot = drawing.requirement_snapshot()
                self._source.validate(snapshot.part, snapshot.model.features)
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
                members.append((name, sheet._snapshot_for_document()))
            except Exception as exc:
                raise DocumentBuildError(name, exc) from exc
        drawings = {}
        for name, sheet in members:
            try:
                with stage(f"document sheet {name}"):
                    drawings[name] = sheet.build()
            except BuildCancelled as exc:
                exc.diagnostic = {**exc.diagnostic, "sheet": name}
                exc.completed_result = None
                raise
            except Exception as exc:
                raise DocumentBuildError(name, exc) from exc
        return DocumentResult(self._source, drawings, self._catalog)
