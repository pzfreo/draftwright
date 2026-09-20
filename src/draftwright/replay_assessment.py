"""Post-build evidence for an exact generated-script replay.

The generation-time STEP inspection document answers what recognition saw.  This module's
separate document answers what one exact, possibly edited, Python script built and exported.
It deliberately consumes the finalized ``Drawing`` and export return value; it does not build,
recognize, or infer declarations a second time.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING

from draftwright.reporting import write_json_document

if TYPE_CHECKING:
    from draftwright.drawing import Drawing

ASSESSMENT_SCHEMA = "draftwright-replay-assessment"
ASSESSMENT_SCHEMA_VERSION = 1
_ASSESSMENT_SUFFIX = ".draftwright-assessment.json"


@dataclass(frozen=True)
class _FileIdentity:
    path: Path
    name: str
    sha256: str
    size: int


def assessment_sidecar_path(py_path: str | PathLike[str]) -> str:
    """Return the post-build assessment path paired with generated script *py_path*."""

    path = str(py_path)
    return f"{path.removesuffix('.py')}{_ASSESSMENT_SUFFIX}"


def _is_owned_assessment(path: str | PathLike[str]) -> bool:
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
        return bool(isinstance(document, dict) and document.get("schema") == ASSESSMENT_SCHEMA)
    except (OSError, ValueError, TypeError):
        return False


def invalidate_replay_assessment(path: str | PathLike[str]) -> bool:
    """Remove a stale tool-owned assessment, preserving every foreign file.

    Returns whether an owned file was removed.  A missing, unreadable, malformed, or differently
    schema'd destination is not ours and remains untouched.
    """

    if not _is_owned_assessment(path):
        return False
    Path(path).unlink(missing_ok=True)
    return True


def _identity(path: str | PathLike[str], *, name: str | None = None) -> _FileIdentity:
    source = Path(path)
    payload = source.read_bytes()
    return _FileIdentity(
        source,
        source.name if name is None else name,
        hashlib.sha256(payload).hexdigest(),
        len(payload),
    )


def _same_file(identity: _FileIdentity, role: str) -> None:
    try:
        current = _identity(identity.path, name=identity.name)
    except OSError as error:
        raise RuntimeError(f"{role} became unavailable during replay") from error
    if current.sha256 != identity.sha256:
        raise RuntimeError(f"{role} changed during replay")


def _validated_formats(
    pmi_mode: str, formats: Sequence[str], reproducible: bool
) -> tuple[str, ...]:
    if pmi_mode not in {"off", "report", "annotate"}:
        raise ValueError(f"unknown replay PMI mode {pmi_mode!r}")
    if isinstance(formats, str):
        raise TypeError("replay formats must be a sequence, not one string")
    normalized = tuple(formats)
    if any(item not in {"svg", "dxf", "pdf", "png"} for item in normalized):
        raise ValueError("replay formats must be svg, dxf, pdf, or png")
    if len(set(normalized)) != len(normalized):
        raise ValueError("replay formats must be unique")
    if type(reproducible) is not bool:
        raise TypeError("reproducible must be a bool")
    return normalized


@dataclass(frozen=True)
class _PreparedReplayAssessment:
    """Captured pre-build identities completed from one finalized drawing and export."""

    destination: Path
    source: _FileIdentity | None
    script: _FileIdentity
    pmi_mode: str
    formats: tuple[str, ...]
    reproducible: bool

    def write(self, drawing: Drawing, outputs: Mapping[str, str]) -> str:
        """Atomically persist this replay's strict assessment after export succeeds."""

        _same_file(self.script, "generated script")
        if self.source is not None:
            _same_file(self.source, "STEP source")

        # This is intentionally the strict public authority.  Any inability to produce it must
        # propagate before a new assessment appears; an empty or reduced substitute would make
        # the replay look safer than it is.
        report = drawing.report()
        if not (
            report.get("schema") == "draftwright-report"
            and report.get("schema_version") == 8
            and report.get("scope") == "declared-sheet"
        ):
            raise RuntimeError("generated replay did not provide a declared-sheet report v8")

        if set(outputs) != set(self.formats):
            raise RuntimeError("export result does not match the replay's requested formats")
        output_rows = []
        for format_name in self.formats:
            output = _identity(outputs[format_name])
            output_rows.append(
                {
                    "format": format_name,
                    "path": str(output.path),
                    "sha256": output.sha256,
                    "bytes": output.size,
                }
            )

        if self.destination.exists() and not _is_owned_assessment(self.destination):
            raise FileExistsError(
                f"refusing to replace foreign assessment destination {self.destination}"
            )
        source = (
            {
                "kind": "build123d",
                "name": None,
                "sha256": None,
                "reason": "a live object replay has no immutable STEP byte source",
            }
            if self.source is None
            else {
                "kind": "step",
                "name": self.source.name,
                "sha256": self.source.sha256,
                "reason": None,
            }
        )
        document = {
            "schema": ASSESSMENT_SCHEMA,
            "schema_version": ASSESSMENT_SCHEMA_VERSION,
            "scope": "generated-script-replay",
            "status": report["status"],
            "producer": report["producer"],
            "source": source,
            "script": {"name": self.script.name, "sha256": self.script.sha256},
            "run": {
                "pmi_mode": self.pmi_mode,
                "formats": list(self.formats),
                "reproducible": self.reproducible,
            },
            "outputs": output_rows,
            "semantic_links": {
                "authority": "drawing.declarations",
                "declarations": "drawing.declarations.entries[].id",
                "representations": "drawing.declarations.entries[].representations[].name",
                "identity_scope": "build-local",
            },
            "drawing": report,
        }
        return write_json_document(document, self.destination)


def prepare_replay_assessment(
    path: str | PathLike[str],
    *,
    script_path: str | PathLike[str],
    source_path: str | PathLike[str] | None,
    source_name: str | None,
    pmi_mode: str,
    formats: Sequence[str],
    reproducible: bool,
) -> _PreparedReplayAssessment:
    """Invalidate stale evidence and capture identities before a generated replay begins."""

    destination = Path(path).resolve()
    if destination.exists():
        if not invalidate_replay_assessment(destination):
            raise FileExistsError(
                f"refusing to replace foreign assessment destination {destination}"
            )
    script = _identity(Path(script_path).resolve())
    if source_path is None:
        if source_name is not None:
            raise ValueError("a source name requires a STEP source path")
        source = None
    else:
        if type(source_name) is not str or not source_name:
            raise ValueError("a STEP replay requires its generation-time source name")
        source = _identity(Path(source_path).resolve(), name=source_name)
    normalized_formats = _validated_formats(pmi_mode, formats, reproducible)
    return _PreparedReplayAssessment(
        destination,
        source,
        script,
        pmi_mode,
        normalized_formats,
        reproducible,
    )


__all__ = [
    "ASSESSMENT_SCHEMA",
    "ASSESSMENT_SCHEMA_VERSION",
    "assessment_sidecar_path",
    "invalidate_replay_assessment",
    "prepare_replay_assessment",
]
