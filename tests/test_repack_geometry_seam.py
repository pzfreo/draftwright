"""The #1137 seam: which solid an assembly projects is an explicit input.

ADR 0004 wants real geometry built once per build. The measure-and-repack loop assembles up
to three times, so it is built up to three times — the root cause of #1135, where one part's
plan view alone took over sixteen minutes of hidden-line removal.

Slice 1 changes no behaviour: it makes the projected solid an argument that defaults to the
analysed part, so a later slice can hand the loop's intermediate assemblies a cheap stand-in
and keep the real solid for the final one. The guard below is therefore not "the parameter
exists" — an ignored parameter would pass that — but "what is passed is what gets projected".
"""

from __future__ import annotations

from build123d import Align, Box, Cylinder, Pos

from draftwright.analysis import _analyse
from draftwright.builder import _assemble


def _stepped_shaft():
    """A turned shaft: several distinct silhouette rungs, so a box stand-in is unmistakably
    different from the real thing under projection."""
    rungs = [(24, 6), (16, 8), (10, 6)]
    z = 0.0
    part = None
    for diameter, height in rungs:
        rung = Pos(0, 0, z) * Cylinder(
            diameter / 2, height, align=(Align.CENTER, Align.CENTER, Align.MIN)
        )
        part = rung if part is None else part + rung
        z += height
    return part


def _assembled_front_edges(part, shape=None):
    a = _analyse(
        part, title="", number="", tolerance="ISO 2768-m", drawn_by="", out="seam", pmi="off"
    )
    dwg = _assemble(a, "seam", None, None, auto_dims=False, shape=shape)
    visible, _hidden = dwg.views["front"]
    return len(visible.edges())


def test_an_assembly_projects_the_shape_it_is_given():
    part = _stepped_shaft()
    bb = part.bounding_box()
    stand_in = Pos(*bb.center()) * Box(bb.size.X, bb.size.Y, bb.size.Z)

    real = _assembled_front_edges(part)
    proxy = _assembled_front_edges(part, shape=stand_in)

    assert real != proxy, (
        "the shape argument reached no projector: a box stand-in and a stepped shaft "
        f"cannot both project to {real} front-view edges"
    )


def test_the_default_is_still_the_analysed_part():
    """The no-behaviour-change half: omitting the argument must project the real solid."""
    part = _stepped_shaft()

    assert _assembled_front_edges(part) == _assembled_front_edges(part, shape=part)
