"""Bounded byte accounting for the archived #1529 frame exports (not a geometry validator)."""

import argparse
import gzip
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path

import ezdxf

parser = argparse.ArgumentParser(description="Measure the six pinned frame exports; no CAD build.")
parser.add_argument("directory", type=Path)
root = parser.parse_args().directory
results = []
for path in sorted(
    root / f"frame-{sheet}.{extension}"
    for sheet in ("features", "general")
    for extension in ("dxf", "svg", "pdf")
):
    b = path.read_bytes()
    compressed = gzip.compress(b, mtime=0)
    assert gzip.decompress(compressed) == b
    r = {
        "file": path.name,
        "bytes": len(b),
        "sha256": sha256(b).hexdigest(),
        "gzip_bytes": len(compressed),
    }
    if path.suffix == ".dxf":
        lines = b.splitlines(keepends=True)
        assert len(lines) % 2 == 0
        pairs = [
            (int(lines[i].strip()), lines[i + 1].strip(), len(lines[i]) + len(lines[i + 1]))
            for i in range(0, len(lines), 2)
        ]
        starts = [i for i, p in enumerate(pairs) if p[0] == 0] + [len(pairs)]
        section = None
        rows = defaultdict(lambda: {"count": 0, "bytes": 0})
        sections = Counter()
        layer_bytes = Counter()
        spline_degrees = Counter()
        for left, right in zip(starts, starts[1:]):
            chunk = pairs[left:right]
            kind = chunk[0][1].decode("ascii")
            n = sum(p[2] for p in chunk)
            if kind == "SECTION":
                section = next(p[1].decode("ascii") for p in chunk if p[0] == 2)
            sections[section or "OUTSIDE"] += n
            if section == "ENTITIES" and kind not in ("SECTION", "ENDSEC"):
                rows[kind]["count"] += 1
                rows[kind]["bytes"] += n
                layer = next((p[1].decode("utf-8") for p in chunk if p[0] == 8), "0")
                layer_bytes[layer] += n
                if kind == "SPLINE":
                    spline_degrees[
                        str(next(p[1].decode("ascii") for p in chunk if p[0] == 71))
                    ] += 1
            if kind == "ENDSEC":
                section = None
        assert sum(sections.values()) == len(b), (path, sections, len(b))
        doc = ezdxf.readfile(path)
        verified = Counter(e.dxftype() for e in doc.modelspace())
        assert verified == Counter({k: v["count"] for k, v in rows.items()}), (verified, rows)
        r.update(
            sections=dict(sections),
            entities=dict(sorted(rows.items(), key=lambda kv: -kv[1]["bytes"])),
            layers=dict(Counter(e.dxf.layer for e in doc.modelspace())),
            layer_bytes=dict(layer_bytes),
            spline_degrees=dict(spline_degrees),
        )
    elif path.suffix == ".svg":
        tree = ET.fromstring(b)
        counts = Counter(e.tag.rsplit("}", 1)[-1] for e in tree.iter())
        data = [e.get("d", "") for e in tree.iter() if e.tag.rsplit("}", 1)[-1] == "path"]
        r.update(
            elements=dict(counts),
            path_data_bytes=sum(len(d.encode()) for d in data),
            path_commands=dict(
                Counter(c for d in data for c in re.findall("[MmLlHhVvCcSsQqTtAaZz]", d))
            ),
        )
    results.append(r)
print(json.dumps(results, indent=2, allow_nan=False))
