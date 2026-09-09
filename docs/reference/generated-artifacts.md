# Managing generated drawing artifacts

Keep the editable generator, source model, dependency record and review evidence together.
Treat SVG, DXF, PDF and previews as generated outputs. A smaller archive is easier to transfer;
its size says nothing about drawing completeness.

## Keep source review readable

If drawings are committed, scope these root `.gitattributes` rules to the generated directory:

```gitattributes
drawings/generated/**/*.svg binary linguist-generated=true
drawings/generated/**/*.dxf binary linguist-generated=true
drawings/generated/**/*.pdf binary linguist-generated=true
drawings/generated/**/*.png binary linguist-generated=true
```

Git's `binary` attribute disables textual diffs, text merges and newline conversion for those
paths. Conflicting generated files require regeneration or an explicit choice; Git does not
silently merge their content. `linguist-generated=true` additionally hides generated content in
GitHub diffs by default. These rules leave Python generators, JSON evidence and dependency files
reviewable. Adjust the directory to your project; do not classify every SVG or every file in the
repository as generated. See the [Git attributes manual](https://git-scm.com/docs/gitattributes)
and [GitHub's generated-file guidance](https://docs.github.com/en/repositories/working-with-files/managing-files/customizing-how-changed-files-appear-on-github).

Check the effective rules, including any local overrides:

```sh
git check-attr diff merge text linguist-generated -- \
  drawings/generated/frame-features.svg drawings/generated/frame-features.dxf \
  drawings/generated/frame-features.pdf drawings/generated/frame-preview.png \
  drawings/generated/frame.draftwright.json scripts/draw_frame.py
```

Alternatively, ignore a dedicated build-output directory and distribute a reviewed archive.
Keep source, generator and environment records under version control. Attributes change diff
presentation; they do not reduce repository history size or replace visual drawing review.

## Inventory a review bundle

The following packaging example uses an explicit list of artifacts. Adapt the names to the
files returned by `drawing.export()`. It neither builds a drawing nor runs recognition. Save
the function in your packaging script; `root` is the directory containing the listed files.

<!-- issue-1537-bundle-example -->
```python
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def bundle_review(root):
    root = Path(root)
    roles = {
        "source": ["frame-reference.step"],
        "generator": ["draw_titanium_frame.py"],
        "generation_environment": ["requirements.txt"],
        "drawings": [
            f"frame-{sheet}.{extension}"
            for sheet in ("general", "features")
            for extension in ("svg", "dxf", "pdf")
        ],
    }
    # Retain available evidence as separate documents, without joining their IDs.
    evidence_names = (
        "frame-general.draftwright.json", "frame-features.draftwright.json",
        "frame.draftwright-inspection.json", "drawing-checks.json",
    )
    roles["review_evidence"] = [name for name in evidence_names if (root / name).is_file()]
    payload = {
        name: (root / name).read_bytes()
        for names in roles.values() for name in names
    }  # Missing required files fail before creating the archive.
    manifest = {
        "scope": "bundled-file-integrity",
        "review_status": "QUOTATION / DFM REVIEW - NOT RELEASED",
        "unresolved_decisions": ["material grade", "hinge fit and pin retention"],
        "generation_environment_record": "requirements.txt",
        "evidence": {
            "present": roles["review_evidence"],
            "not_provided": [name for name in evidence_names if name not in payload],
            "assessment": "Read each document under its own schema and scope.",
        },
        "files": [
            {"role": role, "path": name, "bytes": len(payload[name]),
             "sha256": hashlib.sha256(payload[name]).hexdigest()}
            for role, names in roles.items() for name in names
        ],
    }
    manifest_bytes = (json.dumps(manifest, indent=2, allow_nan=False) + "\n").encode()
    archive = root / "frame-review.zip"
    with ZipFile(archive, "x", compression=ZIP_DEFLATED) as bundle:
        for name, data in payload.items():
            bundle.writestr(name, data)
        bundle.writestr("bundle-manifest.json", manifest_bytes)
    # Verify the exact bytes written, including every listed artifact.
    with ZipFile(archive) as bundle:
        assert set(bundle.namelist()) == {*payload, "bundle-manifest.json"}
        assert bundle.read("bundle-manifest.json") == manifest_bytes
        for name, data in payload.items():
            assert bundle.read(name) == data
    return archive
```
<!-- /issue-1537-bundle-example -->

The output is a ZIP with the original artifact bytes and a bundle manifest. An existing ZIP is
refused, so a rerun does not overwrite a previously reviewed package. This small example holds
the selected files in memory and assumes generation has finished; it does not promise an atomic
snapshot of a concurrently edited directory. Archive timestamps are not a reproducibility API.

The environment record must come from **generation**, including the Draftwright, Quiddity,
build123d and drafting-helpers versions or exact source revisions used. Do not substitute the
versions installed on a machine that merely packages old drawings. Preserve the existing
[`Drawing.report()`](reports.md) and [inspection sidecar](inspection.md) when available: their
producer, source and run information retain their original meaning. A report and an inspection
document answer different questions. Legacy `drawing-checks.json` is included as review evidence,
not relabelled as a current Draftwright report.

The example explicitly lists absent evidence. In particular, a declared drawing may refuse a
strict report; record that limitation in the review record rather than manufacturing an empty
report. Do not trigger a second recognition run merely to fill a packaging field. File hashes
prove which bytes were bundled, not that the outputs were generated from the adjacent source
or that manufacturing decisions have been resolved. Inspect available generation evidence and
the actual drawings before changing the authored review status.

## Size and fidelity

For the pinned #1529 field-report exports, the feature/general DXFs were 8,548,578 and
8,743,378 bytes. Their annotation layers accounted for 7,860,774 and 7,952,882 bytes. The
entity census contains curves and lines, with no native `TEXT` or `MTEXT` entities; those layers
also contain annotation geometry other than text. This makes their size consistent with the
stored vector representation, rather than evidence of unnecessary hidden part geometry.

Lossless gzip reduced those files to 667,005 and 682,691 bytes in the measured environment,
and decompressing restored every byte. ZIP similarly transports the original files without
replacing physical curves, annotation outlines or labels. Do not reduce size by flattening
curves or deleting drawing content without a separate fidelity case. The detailed pinned
[measurement record](https://github.com/pzfreo/draftwright/blob/main/docs/research/1537-export-size.md)
includes all formats, hashes and a repeatable analyzer.

`reproducible=True` is a different control: repeated exports of one drawing on one version
agree byte-for-byte. It does not promise stability across versions, feature correspondence,
coverage, portable generated source paths, or a manufacturing release. Portable generated
paths and other CLI artifact changes remain tracked in #1255.
