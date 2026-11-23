"""Recognise a STEP file's features with Python quiddity, for draftwright's bridge.

    ~/repos/quiddity-python/.venv/bin/python tools/recognise.py part.step -o recognition.json
    draftwright analyse part.step --features recognition.json

Writes quiddity's recognition document unchanged, wrapped with what the bridge (``src/bridge.rs``)
needs to prove that it belongs to the part it is read with: the file's sha256 and, for each face
in the file's own order (OpenCascade's traversal order, which haecceity reads in too), its
surface type and area. Indices inside the document are quiddity's working-shape indices; each of
its faces carries the file-order index as ``caller_index``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from quiddity import __version__, import_step_geometry
from quiddity.document import build_recognition_document

FORMAT = "draftwright.recognition/1"


def recognise(step: Path) -> dict[str, object]:
    part = import_step_geometry(step)
    return {
        "format": FORMAT,
        "source_sha256": hashlib.sha256(step.read_bytes()).hexdigest(),
        "recogniser": f"quiddity {__version__}",
        "faces": [
            {"surface": face.geom_type.name.lower(), "area": face.area} for face in part.faces()
        ],
        "quiddity": build_recognition_document(part),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    encoded = json.dumps(recognise(args.step), indent=1, sort_keys=True, allow_nan=False)
    args.output.write_text(encoded + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
