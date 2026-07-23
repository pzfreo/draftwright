"""#602: DXF export sets the viewport from the known page window, not an entity walk.

``ExportDXF.write`` ends with ``ezdxf.zoom.extents`` — a pure-Python bbox pass over
every modelspace entity that re-flattens each spline (~2 s on the scattered-plate
benchmark, half the DXF export cost). draftwright places everything in page-mm, so
``export.write_dxf`` sets the same single-window viewport directly from
``(0, 0)–(page_w, page_h)`` and saves. These tests guard:

1. the perf property — no ``zoom.extents`` call on the export path (a regression
   silently reintroduces the O(entities) walk);
2. output integrity — the file still loads, keeps its layers and metadata, and the
   active viewport is centred on the page;
3. the best-effort fallback when the exporter hides its ezdxf internals.
"""

from __future__ import annotations

import ezdxf
import ezdxf.zoom
import pytest
from build123d import Box, Cylinder, Pos

from draftwright import build_drawing
from draftwright.export import write_dxf


@pytest.fixture(scope="module")
def dwg():
    return build_drawing(Box(60, 40, 20) - Pos(10, 5, 0) * Cylinder(4, 20))


def test_dxf_export_skips_entity_walk_zoom(dwg, tmp_path, monkeypatch):
    def _no_walk(*args, **kwargs):
        raise AssertionError("zoom.extents (O(entities) walk) called on the DXF export path")

    monkeypatch.setattr(ezdxf.zoom, "extents", _no_walk)
    path = dwg.export(str(tmp_path / "plate"), formats="dxf")["dxf"]
    doc = ezdxf.readfile(path)
    assert len(list(doc.modelspace())) > 0


def test_dxf_viewport_centred_on_page(dwg, tmp_path):
    path = dwg.export(str(tmp_path / "plate"), formats="dxf")["dxf"]
    doc = ezdxf.readfile(path)
    # write_dxf zooms to the page window → the active vport centre is the page centre.
    (vport,) = doc.viewports.get("*Active")
    cx, cy = vport.dxf.center.x, vport.dxf.center.y
    assert cx == pytest.approx(dwg.page_w / 2)
    assert cy == pytest.approx(dwg.page_h / 2)
    # Layers and the set_dxf_metadata stamp survive the direct saveas.
    assert {"part", "dims"} <= {layer.dxf.name for layer in doc.layers}
    assert doc.header.custom_vars.get("GeneratedBy") == "draftwright"


def test_write_dxf_falls_back_without_ezdxf_internals(tmp_path):
    class Opaque:
        def __init__(self):
            self.wrote = None

        def write(self, path):
            self.wrote = path

    exp = Opaque()
    write_dxf(exp, str(tmp_path / "o.dxf"), 210.0, 297.0)
    assert exp.wrote == str(tmp_path / "o.dxf")
