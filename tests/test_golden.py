"""Golden-output regression gate for the #138 pipeline refactor (ADR 0005, Step 0).

The refactor's acceptance contract is "no generated drawing behaviour changes
unless a PR explicitly says so." The existing geometry-level tests assert *local*
properties (an edge count here, a bbox there); they do not, together, pin the
*whole* drawing, so a refactor that shifts a dimension or reorders a pass can pass
them all. This module pins the whole drawing.

For each reference part it builds the drawing and snapshots a canonical digest:

- ``drawing`` — per-view edge counts + geometry bboxes, every annotation's
  type + label, geometry-annotation bboxes, and the lint summary. Built from the
  **public** surface (`views`, `items`, `page_*`, `scale`, `lint_summary()`)
  only — so the oracle survives the registry move (Step 2) without edits.
- ``svg`` — page size + per-(tag, class) element counts, to guard the SVG export
  path (Step 3).
- ``dxf`` — entity counts by type and layer, to guard the DXF export path.

**Platform portability.** The digest pins *counts and geometry*, never text. A
dimension's ``label_bbox`` and the path coordinates of its rendered glyphs come
from font metrics, which differ across OS by up to ~0.3 mm — so committed
snapshots that included them failed on Linux while passing on macOS. The gate
therefore records dimension *values* (label strings), geometry-annotation bboxes,
and per-layer element counts — all platform-stable — and deliberately omits text
extents and glyph coordinates. The trade-off: it does not pin the exact pixel
position of dimension text (unlikely to regress from pure code movement, and the
value + owning view geometry + path counts still move if a dimension is dropped,
revalued, or re-attributed).

Geometry coordinates are rounded to ``ROUND`` decimal places (1e-4 mm): tight
enough to catch a real placement change, loose enough to absorb FP noise.

A refactor PR must leave every ``tests/golden/*.json`` byte-identical. When a PR
*intends* to change output (a layout correction), regenerate and review the diff:

    UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py

The heavy NIST CTC build is marked ``slow`` (deselected by default, run in CI).
"""

import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from build123d import Box, Cylinder

from draftwright import build_drawing

GOLDEN_DIR = Path(__file__).parent / "golden"
FIXTURES = Path(__file__).parent / "fixtures"
ROUND = 4  # page-mm decimal places
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"


# ---------------------------------------------------------------------------
# Digest helpers (public-surface only)
# ---------------------------------------------------------------------------


def _r(x):
    return round(float(x), ROUND)


def _geom_bbox(o):
    """A glyph-free, platform-stable geometric bbox, or ``None``."""
    try:
        b = o.bounding_box()
        return [_r(b.min.X), _r(b.min.Y), _r(b.max.X), _r(b.max.Y)]
    except Exception:  # noqa: BLE001 — not every annotation bbox-es cleanly
        return None


def _anno_entry(o):
    """A platform-portable digest entry for one annotation.

    Text-bearing annotations (dimensions, balloons) carry a ``label_bbox`` derived
    from font metrics, which differ across OS by up to ~0.3 mm (the dimension text
    glyph boxes are the *only* part of the drawing that is not platform-stable).
    So we pin their *value* (the label text), not their box. Pure-geometry
    annotations (centrelines, leaders, section lines) have a glyph-free,
    platform-stable bbox, so we keep it — that retains position sensitivity for
    everything except dimension text.
    """
    entry = {"type": type(o).__name__, "label": _label(o)}
    if getattr(o, "label_bbox", None) is None:
        entry["bbox"] = _geom_bbox(o)
    return entry


def _shape_digest(shape):
    if shape is None:
        return {"edges": 0, "bbox": None}
    try:
        edges = len(shape.edges())
    except Exception:  # noqa: BLE001
        edges = None
    try:
        b = shape.bounding_box()
        bbox = [_r(b.min.X), _r(b.min.Y), _r(b.max.X), _r(b.max.Y)]
    except Exception:  # noqa: BLE001
        bbox = None
    return {"edges": edges, "bbox": bbox}


def _label(o):
    lab = getattr(o, "label", None)
    if lab is None or isinstance(lab, str):
        return lab
    return str(lab)


def digest_drawing(dwg) -> dict:
    """A canonical, JSON-friendly digest of a built :class:`Drawing`.

    Uses only the public surface so it remains a stable oracle across the
    refactor (it must keep witnessing the *same values*, whatever module owns
    the state underneath).
    """
    views = {}
    for name in sorted(dwg.views):
        vis, hid = dwg.views[name]
        views[name] = {"visible": _shape_digest(vis), "hidden": _shape_digest(hid)}

    anns = [_anno_entry(o) for o in dwg.items]
    # Sort canonically: annotation list-order is draw order, not a behaviour we
    # want to pin. Type/label/(geometry) position are.
    anns.sort(key=lambda a: (a["type"], a["label"] or "", json.dumps(a.get("bbox"))))

    s = dwg.lint_summary()
    lint = {
        "passed": s["passed"],
        "errors": s["errors"],
        "warnings": s["warnings"],
        "infos": s["infos"],
        "by_code": dict(sorted(s["by_code"].items())),
    }

    return {
        "page": {"w": _r(dwg.page_w), "h": _r(dwg.page_h), "scale": _r(dwg.scale)},
        "views": views,
        "annotations": anns,
        "lint": lint,
    }


def digest_svg(svg_path: str) -> dict:
    """A structural digest of the exported SVG: page size + per-(tag, class)
    element counts.

    Counts, not coordinates: glyph path *coordinates* differ across OS (the same
    font-metric variance that keeps text bboxes out of the drawing digest), but
    the *number* of paths per layer is platform-stable and witnesses dropped /
    added / mis-layered geometry and labels. Keying by ``class`` adds per-layer
    granularity (``part`` / ``hidden`` / ``dims``) over a bare tag histogram.
    """
    root = ET.fromstring(Path(svg_path).read_text(encoding="utf-8"))
    hist: dict[str, int] = {}
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        cls = el.get("class") or el.get("id") or ""
        key = f"{tag}.{cls}" if cls else tag
        hist[key] = hist.get(key, 0) + 1
    return {
        "elements": dict(sorted(hist.items())),
        "width": root.get("width"),
        "height": root.get("height"),
    }


# DXF layers carrying tessellated dimension-text glyphs. Their LINE/SPLINE entity
# counts depend on the platform's curve-flattening of the font outlines (Linux and
# macOS disagree by hundreds of entities), so they are excluded from the digest —
# the geometry layers (part/hidden) carry the portable, meaningful DXF witness.
_DXF_TEXT_LAYERS = {"dims"}


def digest_dxf(dxf_path: str) -> dict:
    """A structural digest of the exported DXF: geometry entity counts by type and
    layer, excluding text-bearing layers.

    Counts (not coordinates) for the same portability reason as the SVG digest;
    this is the witness for the DXF export path, which the drawing/SVG digests do
    not otherwise cover. Text layers are skipped (see ``_DXF_TEXT_LAYERS``) because
    glyph tessellation is platform-variant; dimension *presence/value* is already
    pinned by the drawing digest, so this guards the geometry reaching the DXF.
    """
    import ezdxf

    msp = ezdxf.readfile(dxf_path).modelspace()
    by_type: dict[str, int] = {}
    by_layer: dict[str, int] = {}
    for e in msp:
        layer = e.dxf.layer
        if layer in _DXF_TEXT_LAYERS:
            continue
        by_type[e.dxftype()] = by_type.get(e.dxftype(), 0) + 1
        by_layer[layer] = by_layer.get(layer, 0) + 1
    return {
        "by_type": dict(sorted(by_type.items())),
        "by_layer": dict(sorted(by_layer.items())),
    }


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

_PARTS = {
    "cylinder": lambda: Cylinder(radius=15, height=40),
    "plate": lambda: Box(80, 50, 8),
    "stepped": lambda: Box(40, 40, 10) + Box(20, 20, 10).translate((0, 0, 10)),
}

# (id, kind, ident, slow). Three primitives cover the turned / prismatic /
# stepped geometry classes cheaply; CTC-01 is one real-world fixture with holes
# (exercises the hole-callout pass), marked slow. The pathological dense-ballooning
# case (CTC-02) is deliberately excluded — too heavy for a routine gate, and its
# overlap acceptance is already pinned by test_e2e_standards.py.
_CASES = [
    ("cylinder", "obj", "cylinder", False),
    ("plate", "obj", "plate", False),
    ("stepped", "obj", "stepped", False),
    ("ctc01", "step", "nist_ctc_01_asme1_ap203.stp", True),
]


def _params():
    out = []
    for cid, kind, ident, slow in _CASES:
        # Slow cases are STEP-fixture builds; give them the 600s timeout the
        # other CTC tests use (the O(n²) lint in the digest is the cost).
        marks = [pytest.mark.slow, pytest.mark.timeout(600)] if slow else []
        out.append(pytest.param((cid, kind, ident), id=cid, marks=marks))
    return out


def _build(case, stem):
    _cid, kind, ident = case
    if kind == "obj":
        return build_drawing(_PARTS[ident](), out=stem, title=_cid.upper(), number="DWG-1")
    return build_drawing(str(FIXTURES / ident), out=stem, number="DWG-1")


def _diffs(exp, act, path=""):
    """Human-readable list of where two digests differ."""
    if isinstance(exp, dict) and isinstance(act, dict):
        out = []
        for k in sorted(set(exp) | set(act)):
            out += _diffs(exp.get(k, "<missing>"), act.get(k, "<missing>"), f"{path}.{k}")
        return out
    if isinstance(exp, list) and isinstance(act, list):
        out = []
        if len(exp) != len(act):
            out.append(f"{path}: list len {len(exp)} -> {len(act)}")
        for i, (e, a) in enumerate(zip(exp, act)):
            out += _diffs(e, a, f"{path}[{i}]")
        return out
    return [] if exp == act else [f"{path}: {exp!r} -> {act!r}"]


@pytest.mark.parametrize("case", _params())
def test_golden(case, tmp_path):
    cid = case[0]
    stem = str(tmp_path / cid)
    dwg = _build(case, stem)
    svg, dxf = dwg.export(stem)
    actual = {
        "drawing": digest_drawing(dwg),
        "svg": digest_svg(svg),
        "dxf": digest_dxf(dxf),
    }

    path = GOLDEN_DIR / f"{cid}.json"
    if UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return

    assert path.exists(), (
        f"no golden for {cid!r}: {path} is missing. "
        f"Generate it with:  UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py"
    )
    expected = json.loads(path.read_text(encoding="utf-8"))
    if actual != expected:
        diffs = _diffs(expected, actual)
        head = "\n".join(diffs[:40])
        more = f"\n… and {len(diffs) - 40} more" if len(diffs) > 40 else ""
        pytest.fail(
            f"golden mismatch for {cid!r} ({len(diffs)} difference(s)). "
            f"If intended, regenerate with UPDATE_GOLDEN=1 and review the diff.\n"
            f"{head}{more}"
        )


def test_digest_is_deterministic(tmp_path):
    """The digest must be reproducible build-to-build, or the gate is noise."""
    a = build_drawing(_PARTS["plate"](), out=str(tmp_path / "a"), number="DWG-1")
    b = build_drawing(_PARTS["plate"](), out=str(tmp_path / "b"), number="DWG-1")
    assert digest_drawing(a) == digest_drawing(b)
