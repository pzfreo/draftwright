"""Exported files carry the drawing, not the run that produced it.

Two exports of one drawing used to differ in bytes. That makes a drawing useless as
something to check in and diff, which is how a caller notices its output changed.
Three separate sources, one per format, each guarded here:

1. **Element order.** ``ExportSVG``/``ExportDXF`` walk a shape with ``.faces()`` /
   ``.edges()`` and write one element per part, in the order they are handed over.
   ``export._elements`` sorts that walk by geometry for the DXF (whose entities *and
   their handles* follow it), and :func:`export.canonicalize_svg` settles the SVG
   afterwards, where the elements are already in the file and cost nothing to reorder.
2. **The clock.** ezdxf stamps every save with the time, a fresh GUID pair and a
   version marker; reportlab stamps a ``/CreationDate`` and a derived ``/ID``.
3. **A set iteration.** ezdxf builds the CLASSES section by iterating a set of type
   names, whose order over strings follows the interpreter's hash seed.

:func:`test_two_runs_under_different_hash_seeds_agree` is the gate that covers all
three at once, and it is deliberately a **subprocess** pair: comparing two exports
inside one interpreter proves nothing, because string hashing is stable for a given
``PYTHONHASHSEED`` and the unsorted code passes such a test too.
"""

from __future__ import annotations

import inspect
import itertools
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import ezdxf
import pytest
from build123d import Box, Edge, Face, Rectangle, Wire

import draftwright.drawing as drawing_mod
from draftwright import build_drawing
from draftwright._core import draft_preset
from draftwright.builder import make_drawing
from draftwright.drawing import Drawing
from draftwright.export import (
    _DraftwrightDXF,
    _elements,
    _geometry_key,
    _render_pdf,
    canonicalize_svg,
    write_dxf,
)

# ---------------------------------------------------------------- SVG element order

_SVG = '<svg xmlns="http://www.w3.org/2000/svg">\n  <g id="part">\n{body}  </g>\n</svg>\n'
# Three leaves whose sorted order (circle, then the two lines) is none of the orders
# they are fed in below, so every permutation has to be moved to reach it.
_LEAVES = (
    '    <line x1="1" y1="0" />\n',
    '    <line x1="2" y1="0" />\n',
    '    <circle r="3" />\n',
)


def _write_permutations(tmp_path):
    """One file per ordering of *_LEAVES*, plus the set of distinct inputs written."""
    paths, distinct = [], set()
    for i, perm in enumerate(itertools.permutations(_LEAVES)):
        p = tmp_path / f"perm{i}.svg"
        p.write_text(_SVG.format(body="".join(perm)), encoding="utf-8")
        distinct.add(p.read_text(encoding="utf-8"))
        paths.append(p)
    return paths, distinct


def test_layer_order_does_not_depend_on_the_order_the_elements_arrived(tmp_path):
    paths, distinct = _write_permutations(tmp_path)
    # Precondition: the fixture really does present the same layer six different ways.
    assert len(distinct) == 6

    for p in paths:
        canonicalize_svg(str(p))
    canonical = {p.read_text(encoding="utf-8") for p in paths}
    assert len(canonical) == 1, f"{len(canonical)} distinct outputs from one element set"

    # ...and the one output is in the sorted order, not merely a shared arbitrary one.
    (out,) = canonical
    assert out.index("<circle") < out.index('x1="1"') < out.index('x1="2"')


def test_the_last_slots_indent_does_not_travel_with_its_element(tmp_path):
    """``tail`` belongs to the slot, not to the element that happens to sit in it.

    ``tail`` is the whitespace ElementTree stores *after* an element, and a group's
    last child carries a shorter one than its siblings. Reorder the children with
    their tails attached and that short indent lands on whichever element sorted
    last, so two runs agree on the element order and still disagree on the bytes.
    Holding the tails in place while the elements move through them is the fix; this
    asserts the defect is reachable first, by reproducing the ordering that has it.

    The gate below does **not** cover this: on a real drawing the elements that
    change places sit mid-layer, so the last slot keeps its element and the bytes
    agree anyway. It is latent there, which is why it is pinned here directly.
    """
    paths, _ = _write_permutations(tmp_path)

    def sorted_by(path, key):
        tree = ET.parse(path)
        for group in tree.getroot().iter("{http://www.w3.org/2000/svg}g"):
            group[:] = sorted(list(group), key=key)
        buf = ET.tostring(tree.getroot(), encoding="unicode")
        return buf

    # The old key: the serialized element, tail included.
    old = {sorted_by(str(p), lambda e: ET.tostring(e, encoding="unicode")) for p in paths}
    assert len(old) > 1, "fixture no longer exposes the tail-in-the-sort-key defect"

    for p in paths:
        canonicalize_svg(str(p))
    assert len({p.read_text(encoding="utf-8") for p in paths}) == 1


def test_a_group_holding_another_group_is_left_alone(tmp_path):
    """Only all-leaf groups are sorted, so nesting is never flattened or reordered."""
    p = tmp_path / "nested.svg"
    p.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg">\n'
        '  <g id="outer">\n'
        '    <g id="zzz"><line x1="1" /></g>\n'
        '    <g id="aaa"><line x1="2" /></g>\n'
        "  </g>\n"
        "</svg>\n",
        encoding="utf-8",
    )
    canonicalize_svg(str(p))
    out = p.read_text(encoding="utf-8")
    # A leaf sort would put "aaa" first; the paint order of the layers is not ours to change.
    assert out.index('id="zzz"') < out.index('id="aaa"')


def test_the_canonical_svg_is_still_an_svg(tmp_path):
    """The round-trip keeps the default namespace — svglib renders the PDF from this."""
    p = tmp_path / "ns.svg"
    p.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="20mm" height="10mm" '
        'viewBox="0 0 20 10">\n'
        '  <g id="part">\n'
        '    <line x1="2" y1="2" x2="8" y2="8" stroke="black" />\n'
        '    <circle cx="4" cy="4" r="1" stroke="black" fill="none" />\n'
        "  </g>\n"
        "</svg>\n",
        encoding="utf-8",
    )
    canonicalize_svg(str(p))
    assert 'xmlns="http://www.w3.org/2000/svg"' in p.read_text(encoding="utf-8")

    from svglib.svglib import svg2rlg

    assert svg2rlg(str(p)) is not None


def test_svg_canonicalization_does_not_change_elementtree_global_namespaces(tmp_path):
    p = tmp_path / "global.svg"
    p.write_text(_SVG.format(body="".join(reversed(_LEAVES))), encoding="utf-8")
    before = dict(ET._namespace_map)
    canonicalize_svg(str(p))
    assert ET._namespace_map == before


# ------------------------------------------------------------- shape decomposition


def test_elements_come_out_in_geometric_order():
    """``ordered=True`` orders the emitted edge stream and loses no boundary ink."""
    shape = Box(20, 10, 5)
    parts = _elements(shape, ordered=True)

    expected = [edge for face in shape.faces() for edge in face.edges()]
    owned = set(expected)
    expected += [edge for edge in shape.edges() if edge not in owned]
    assert len(parts) == len(expected)
    assert all(part in expected for part in parts)


def test_a_segment_and_its_reverse_do_not_tie():
    """Hidden-line removal emits overlapping silhouettes as reversed duplicate pairs.

    Two such edges share a bounding box, a geometry type, an edge count, a length
    and a vertex set — everything the key used to look at — yet an exporter writes
    each one's start and end, so they are not interchangeable. Left tied, ``sorted``
    keeps whatever order the kernel handed them over in, which is the run-dependent
    order this whole module exists to remove.
    """
    a = Edge.make_line((0, 0, 0), (10, 0, 0))
    b = Edge.make_line((10, 0, 0), (0, 0, 0))

    # Precondition: everything except the direction agrees.
    for attr in ("min", "max"):
        box_a, box_b = getattr(a.bounding_box(), attr), getattr(b.bounding_box(), attr)
        assert (box_a.X, box_a.Y, box_a.Z) == (box_b.X, box_b.Y, box_b.Z)
    assert str(a.geom_type) == str(b.geom_type)
    assert len(a.edges()) == len(b.edges())
    assert a.length == pytest.approx(b.length)

    assert _geometry_key(a) != _geometry_key(b)


def test_distinct_faces_with_the_same_cheap_key_have_one_exact_order():
    """Exact B-rep breaks ties that bounds/type/edge-count cannot distinguish."""
    a = Face(Wire.make_polygon([(0, 0), (10, 0), (8, 10), (0, 10)], close=True))
    b = Face(Wire.make_polygon([(0, 0), (10, 0), (10, 10), (2, 10)], close=True))
    assert _geometry_key(a) == _geometry_key(b)  # the adversarial collision is real

    class Pair:
        def __init__(self, faces):
            self._faces = faces

        def faces(self):
            return self._faces

        def edges(self):
            return [edge for face in self._faces for edge in face.edges()]

    forward = _elements(Pair([a, b]), ordered=True)
    reverse = _elements(Pair([b, a]), ordered=True)
    assert [_geometry_key(edge) for edge in forward] == [_geometry_key(edge) for edge in reverse]


def test_one_face_cannot_leak_its_cyclic_wire_start_into_entity_order():
    points = [(0, 0), (10, 0), (10, 10), (0, 10)]
    a = Face(Wire.make_polygon(points, close=True))
    b = Face(Wire.make_polygon(points[1:] + points[:1], close=True))

    class OneFace:
        def __init__(self, face):
            self.face = face

        def faces(self):
            return [self.face]

        def edges(self):
            return self.face.edges()

    assert [_geometry_key(edge) for edge in _elements(OneFace(a), ordered=True)] == [
        _geometry_key(edge) for edge in _elements(OneFace(b), ordered=True)
    ]


def test_subnanometre_edges_tied_by_the_fast_prefix_have_one_exact_order():
    a = Edge.make_line((0, 0, 0), (1, 0, 0))
    b = Edge.make_line((0, 1e-10, 0), (1, 1e-10, 0))
    assert _geometry_key(a) == _geometry_key(b)

    class Pair:
        def __init__(self, edges):
            self._edges = edges

        def faces(self):
            return []

        def edges(self):
            return self._edges

    forward = _elements(Pair([a, b]), ordered=True)
    reverse = _elements(Pair([b, a]), ordered=True)
    assert [edge.bounding_box().min.Y for edge in forward] == [
        edge.bounding_box().min.Y for edge in reverse
    ]


def test_elements_of_an_edge_only_shape_are_ordered_too():
    shape = Rectangle(10, 5).edges()
    parts = _elements(shape, ordered=True)
    assert len(parts) == 4
    assert parts == sorted(parts, key=_geometry_key)


def test_elements_does_not_order_by_default():
    """Off, this costs exactly what it did before the option existed.

    The sort is the expensive half of a reproducible export — a bounding box and an
    edge walk per part — so the default path must not do it at all, rather than do
    it and discard the result. Same parts, in the order the kernel gave them.
    """
    for shape in (Box(20, 10, 5), Rectangle(10, 5).edges()):
        default = _elements(shape)
        faces = list(shape.faces())
        owned = {e for f in faces for e in f.edges()}
        kernel_order = faces + [e for e in shape.edges() if e not in owned]
        assert default == kernel_order


# ------------------------------------------------------------------ write_dxf(reproducible=)

_FIXED_JULIAN = "2451545.0"  # ezdxf's juliandate(2000-01-01)
_ZERO_GUID = "{00000000-0000-0000-0000-000000000000}"
_CONST_MARKER = "0.0 @ 2000-01-01T00:00:00.000000+00:00"
_LIVE_MARKER = re.compile(r"\d+\.\d+\.\d+ @ 20[2-9]\d-")


def _header_value(text: str, name: str) -> str | None:
    """The value ezdxf wrote for header variable *name*, read from the file itself.

    Not via ``ezdxf.readfile`` — loading a document re-stamps its header, so a
    round-trip reports the reader's clock rather than what was written.
    """
    m = re.search(r"^\$" + name + r"\n\s*\d+\n(.*)$", text, re.M)
    return m.group(1) if m else None


def _write(tmp_path, name, **kwargs) -> str:
    exp = _DraftwrightDXF()
    exp.add_layer("part")
    exp.add_shape(Rectangle(10, 5).edges(), layer="part")
    path = str(tmp_path / name)
    write_dxf(exp, path, 210.0, 297.0, **kwargs)
    return Path(path).read_text(encoding="utf-8", errors="replace")


def test_write_dxf_without_reproducible_lets_the_clock_through(tmp_path):
    """The precondition the flag exists to remove: an unpinned save carries the run."""
    text = _write(tmp_path, "live.dxf", reproducible=False)
    assert _header_value(text, "TDCREATE") != _FIXED_JULIAN
    assert _header_value(text, "VERSIONGUID") != _ZERO_GUID
    assert _LIVE_MARKER.search(text), "expected a live ezdxf version/timestamp marker"
    assert _CONST_MARKER not in text


def test_write_dxf_reproducible_pins_the_clock_and_the_guids(tmp_path):
    text = _write(tmp_path, "pinned.dxf", reproducible=True)
    assert _header_value(text, "TDCREATE") == _FIXED_JULIAN
    assert _header_value(text, "TDUPDATE") == _FIXED_JULIAN
    assert _header_value(text, "VERSIONGUID") == _ZERO_GUID
    assert _header_value(text, "FINGERPRINTGUID") == _ZERO_GUID


def test_write_dxf_reproducible_pins_the_creation_marker_too(tmp_path):
    """ezdxf's own option misses this one: it is stamped when the document is built.

    ``_DraftwrightDXF()`` constructs the ezdxf document long before an export asks
    for anything, so CREATED_BY_EZDXF already holds a live timestamp by the time the
    option is flipped. Both markers have to end up constant or the file still differs.
    """
    text = _write(tmp_path, "marked.dxf", reproducible=True)
    assert not _LIVE_MARKER.search(text), "a live ezdxf timestamp survived the pin"
    assert text.count(_CONST_MARKER) == 2  # CREATED_BY_EZDXF and WRITTEN_BY_EZDXF


def test_write_dxf_is_not_reproducible_by_default(tmp_path):
    """Opt-in: an unasked-for export pays nothing and carries the clock, as before."""
    assert _write(tmp_path, "default.dxf") != _write(tmp_path, "pinned.dxf", reproducible=True)
    default = _write(tmp_path, "default2.dxf")
    assert _header_value(default, "TDCREATE") != _FIXED_JULIAN
    assert _LIVE_MARKER.search(default)


def test_the_classes_entries_we_seed_are_registered_in_sorted_order(tmp_path):
    """ezdxf ends ``add_required_classes`` with ``for dxftype in dxf_types_in_use``.

    That is a ``set[str]``, so its iteration order follows the interpreter's hash
    seed. Seeding those names first, sorted, fixes them — ``add_class()`` keeps the
    first entry for a key, and ezdxf's pass then adds only what is left, in the fixed
    order of its own ``REQUIRED_CLASSES`` table. So only the entries that came from
    the document's types-in-use are ours to order; the rest keep ezdxf's order and
    are left alone here.

    This asserts the mechanism. The property it exists for — that the order is the
    same on the next run — can only be measured across processes, which is
    :func:`test_two_runs_under_different_hash_seeds_agree`.
    """
    exp = _DraftwrightDXF()
    exp.add_layer("part")
    exp.add_shape(Rectangle(10, 5).edges(), layer="part")
    in_use = set(exp._document.entitydb.dxf_types_in_use())
    path = str(tmp_path / "classes.dxf")
    write_dxf(exp, path, 210.0, 297.0, reproducible=True)

    section = Path(path).read_text(encoding="utf-8", errors="replace")
    section = section.split("CLASSES", 1)[1].split("ENDSEC", 1)[0]
    names = re.findall(r"^\s*1\n(.+)$", section, re.M)
    seeded = [n for n in names if n in in_use]
    assert len(seeded) > 1, "fixture must register more than one class worth ordering"
    assert seeded == sorted(seeded)
    # Ours come first, before ezdxf's own required-class pass.
    assert names[: len(seeded)] == seeded


@pytest.mark.parametrize("reproducible", [True, False])
@pytest.mark.parametrize("preset", [True, False])
def test_write_dxf_restores_the_global_ezdxf_option(tmp_path, reproducible, preset):
    """The pin is process-wide state belonging to whoever set it.

    ``write_fixed_meta_data_for_testing`` is an ``ezdxf.options`` global: leaving it
    flipped decides what every other ezdxf user in the interpreter writes, including
    the next test in the same session.
    """
    previous = ezdxf.options.write_fixed_meta_data_for_testing
    try:
        ezdxf.options.write_fixed_meta_data_for_testing = preset
        _write(tmp_path, f"opt-{reproducible}-{preset}.dxf", reproducible=reproducible)
        assert ezdxf.options.write_fixed_meta_data_for_testing is preset
    finally:
        ezdxf.options.write_fixed_meta_data_for_testing = previous


def test_write_dxf_restores_the_global_option_even_when_the_save_fails(tmp_path):
    previous = ezdxf.options.write_fixed_meta_data_for_testing
    try:
        ezdxf.options.write_fixed_meta_data_for_testing = False
        exp = _DraftwrightDXF()
        exp.add_layer("part")
        exp.add_shape(Rectangle(10, 5).edges(), layer="part")

        def boom(*args, **kwargs):
            raise OSError("disk full")

        exp._document.saveas = boom
        with pytest.raises(OSError, match="disk full"):
            write_dxf(exp, str(tmp_path / "boom.dxf"), 210.0, 297.0, reproducible=True)
        assert ezdxf.options.write_fixed_meta_data_for_testing is False
    finally:
        ezdxf.options.write_fixed_meta_data_for_testing = previous


def test_concurrent_reproducible_writes_do_not_touch_ezdxf_global_state(tmp_path):
    previous = ezdxf.options.write_fixed_meta_data_for_testing
    try:
        ezdxf.options.write_fixed_meta_data_for_testing = False
        with ThreadPoolExecutor(max_workers=2) as executor:
            texts = list(
                executor.map(
                    lambda index: _write(
                        tmp_path,
                        f"thread-{index}.dxf",
                        reproducible=True,
                    ),
                    range(2),
                )
            )
        assert ezdxf.options.write_fixed_meta_data_for_testing is False
        assert all(_header_value(text, "TDCREATE") == _FIXED_JULIAN for text in texts)
    finally:
        ezdxf.options.write_fixed_meta_data_for_testing = previous


def test_zoom_fallback_retains_reproducible_metadata(tmp_path, monkeypatch):
    from ezdxf import zoom

    monkeypatch.setattr(zoom, "window", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError()))
    text = _write(tmp_path, "zoom-fallback.dxf", reproducible=True)
    assert _header_value(text, "TDCREATE") == _FIXED_JULIAN
    assert _header_value(text, "VERSIONGUID") == _ZERO_GUID


def test_reproducible_dxf_fails_if_the_document_is_inaccessible(tmp_path):
    class OpaqueExporter:
        def write(self, path):
            Path(path).write_text("live", encoding="utf-8")

    with pytest.raises(RuntimeError, match="requires access to the ezdxf document"):
        write_dxf(
            OpaqueExporter(),
            str(tmp_path / "opaque.dxf"),
            210.0,
            297.0,
            reproducible=True,
        )


# ------------------------------------------------------------------ _render_pdf(reproducible=)

_FIXED_PDF_DATE = b"/CreationDate (D:20000101000000+00'00')"


@pytest.fixture(scope="module")
def small_svg(tmp_path_factory):
    p = tmp_path_factory.mktemp("pdf") / "in.svg"
    p.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="40mm" height="20mm" '
        'viewBox="0 0 40 20">\n'
        '  <line x1="2" y1="2" x2="38" y2="18" stroke="black" />\n'
        "</svg>\n",
        encoding="utf-8",
    )
    return str(p)


def test_render_pdf_without_reproducible_carries_the_clock(small_svg, tmp_path):
    out = tmp_path / "live.pdf"
    _render_pdf(small_svg, str(out), reproducible=False)
    assert _FIXED_PDF_DATE not in out.read_bytes()


def test_render_pdf_reproducible_writes_the_same_bytes_twice(small_svg, tmp_path):
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    _render_pdf(small_svg, str(a), reproducible=True)
    _render_pdf(small_svg, str(b), reproducible=True)
    assert _FIXED_PDF_DATE in a.read_bytes()
    assert a.read_bytes() == b.read_bytes()


def test_render_pdf_is_not_reproducible_by_default(small_svg, tmp_path):
    out = tmp_path / "default.pdf"
    _render_pdf(small_svg, str(out))
    assert _FIXED_PDF_DATE not in out.read_bytes()


def test_reproducible_pdf_does_not_fall_back_to_a_live_render(small_svg, tmp_path, monkeypatch):
    from reportlab.pdfgen.canvas import Canvas

    def fail_link(*_args, **_kwargs):
        raise RuntimeError("link failed")

    monkeypatch.setattr(Canvas, "linkURL", fail_link)
    with pytest.raises(RuntimeError, match="link failed"):
        _render_pdf(
            small_svg,
            str(tmp_path / "no-live-fallback.pdf"),
            link_rect=(0, 0, 1, 1),
            reproducible=True,
        )


# ------------------------------------- the public surface PartCAD reaches it through


@pytest.fixture(scope="module")
def plate():
    """One built sheet, shared: these tests are about wiring, not about geometry."""
    return build_drawing(Box(30, 20, 10))


def _spy(monkeypatch):
    """Record what the export path was actually asked to do."""
    seen = {"canonicalized": 0, "ordered": set(), "pinned": set()}
    real_canon, real_shape, real_dxf = (
        drawing_mod.canonicalize_svg,
        drawing_mod._export_shape,
        drawing_mod.write_dxf,
    )

    def canon(path):
        seen["canonicalized"] += 1
        return real_canon(path)

    def export_shape(exporter, shape, layer, ctx, *, ordered=False):
        seen["ordered"].add((type(exporter).__name__, ordered))
        return real_shape(exporter, shape, layer, ctx, ordered=ordered)

    def write(dxf, path, page_w, page_h, reproducible=False):
        seen["pinned"].add(reproducible)
        return real_dxf(dxf, path, page_w, page_h, reproducible=reproducible)

    monkeypatch.setattr(drawing_mod, "canonicalize_svg", canon)
    monkeypatch.setattr(drawing_mod, "_export_shape", export_shape)
    monkeypatch.setattr(drawing_mod, "write_dxf", write)
    return seen


def test_export_does_no_reproducibility_work_by_default(plate, tmp_path, monkeypatch):
    """The default export must not pay for what it was not asked for.

    Not "produces a file that happens to differ" — that is unobservable in one run.
    This asserts the work is *not requested*: no canonicalisation pass over the SVG,
    nothing ordered, nothing pinned.
    """
    seen = _spy(monkeypatch)
    plate.export(str(tmp_path / "d"), formats=("svg", "dxf"))
    assert seen["canonicalized"] == 0
    assert seen["ordered"] == {("ExportSVG", False), ("_DraftwrightDXF", False)}
    assert seen["pinned"] == {False}


def test_export_reproducible_true_orders_the_dxf_and_canonicalises_the_svg(
    plate, tmp_path, monkeypatch
):
    """The two formats reach the same guarantee by different, deliberate routes.

    The DXF has to be ordered *going in*: ``ExportDXF`` writes an entity per element
    as it converts and the handles follow, so nothing after the fact can fix it. The
    SVG is settled *coming out*, by sorting the written file — which is why it is
    handed its shapes unordered even here. Ordering it too would pay the expensive
    half twice for a result :func:`canonicalize_svg` already reaches on its own.
    """
    seen = _spy(monkeypatch)
    plate.export(str(tmp_path / "r"), formats=("svg", "dxf"), reproducible=True)
    assert seen["canonicalized"] == 1
    assert seen["ordered"] == {("ExportSVG", False), ("_DraftwrightDXF", True)}
    assert seen["pinned"] == {True}


def test_build_drawing_sets_the_drawings_own_default(tmp_path, monkeypatch):
    """``build_drawing(reproducible=True)`` is the seam PartCAD reaches.

    PartCAD forwards its configured options to ``build_drawing`` and then calls
    ``drawing.export(stem, formats=(fmt,))`` with no keywords of its own, so the
    setting has to survive the build and be found by that later bare export.
    """
    drawing = build_drawing(Box(30, 20, 10), reproducible=True)
    assert drawing.reproducible is True

    seen = _spy(monkeypatch)
    drawing.export(str(tmp_path / "b"), formats=("dxf",))  # exactly PartCAD's call shape
    assert seen["ordered"] == {("_DraftwrightDXF", True)}
    assert seen["pinned"] == {True}


def test_build_drawing_defaults_to_off(tmp_path):
    assert build_drawing(Box(30, 20, 10)).reproducible is False


def test_reproducible_is_appended_after_the_existing_positional_scale_policy():
    for function in (build_drawing, make_drawing):
        parameters = tuple(inspect.signature(function).parameters)
        assert parameters.index("scale_policy") < parameters.index("reproducible")


def test_a_directly_constructed_drawing_defaults_to_off():
    """``build_drawing`` is not the only door: ``Drawing`` is public and constructible.

    Separate from the test above because that one cannot see this: ``build_drawing``
    passes its own ``reproducible=`` down explicitly, so it would keep reporting
    ``False`` even if the constructor's default were flipped.
    """
    drawing = Drawing(
        scale=1.0,
        page_w=210.0,
        page_h=297.0,
        tb_w=50.0,
        draft=draft_preset(),
        look_at=(0.0, 0.0, 0.0),
        dist=100.0,
        centroid=(0.0, 0.0, 0.0),
        out="x",
    )
    assert drawing.reproducible is False


@pytest.mark.parametrize("drawing_default", [True, False])
def test_the_export_keyword_overrides_the_drawings_default(tmp_path, monkeypatch, drawing_default):
    drawing = build_drawing(Box(30, 20, 10), reproducible=drawing_default)
    seen = _spy(monkeypatch)
    drawing.export(str(tmp_path / "o"), formats=("dxf",), reproducible=not drawing_default)
    assert seen["ordered"] == {("_DraftwrightDXF", not drawing_default)}
    assert seen["pinned"] == {not drawing_default}


def test_the_pdf_render_follows_the_same_flag(plate, tmp_path):
    """PDF is one of the three formats PartCAD renders, so it obeys the flag too."""
    a = plate.export(str(tmp_path / "p1"), formats=("pdf",), reproducible=True)["pdf"]
    b = plate.export(str(tmp_path / "p2"), formats=("pdf",), reproducible=True)["pdf"]
    assert Path(a).read_bytes() == Path(b).read_bytes()
    plain = plate.export(str(tmp_path / "p3"), formats=("pdf",))["pdf"]
    assert _FIXED_PDF_DATE not in Path(plain).read_bytes()


# ------------------------------------------------------- the gate: two real runs


_EXPORT_SCRIPT = """
from build123d import Box
from draftwright.builder import build_drawing
import sys
build_drawing(Box(30, 20, 10), reproducible=True).export(
    sys.argv[1], formats=("svg", "dxf", "pdf", "png")
)
"""


def test_two_runs_under_different_hash_seeds_agree(tmp_path):
    """The whole contract, end to end, the only way it can honestly be measured.

    Separate interpreters: within one process a same-process comparison passes on
    unsorted code and proves nothing, so it would not be a measurement at all.

    Read this as a **sampling** detector, not a proof. The kernel's ordering does
    not reduce to ``PYTHONHASHSEED`` — varying the seed is only a way to get two
    genuinely different runs — and a given fixture does not reorder on every one:
    with the sort removed on purpose this caught it in 3 of 4 trials. It never
    fails with the sort in place (6 of 6). The per-mechanism tests above are the
    deterministic guards; this is the one that would notice a source of drift
    nobody thought of.
    """
    outs = []
    for seed in ("1", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        stem = str(tmp_path / f"seed{seed}")
        subprocess.run(
            [sys.executable, "-c", _EXPORT_SCRIPT, stem],
            check=True,
            env=env,
            capture_output=True,
        )
        outs.append(stem)

    for ext in ("svg", "dxf", "pdf", "png"):
        first, second = (Path(f"{o}.{ext}").read_bytes() for o in outs)
        assert first, f"{ext} export produced nothing"
        assert first == second, f"{ext} differs between runs under different hash seeds"
