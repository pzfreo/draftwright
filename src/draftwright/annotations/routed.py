"""Routed leaders for bounded sheet-global recovery (#1797)."""

from build123d import Align, Edge, Location, Mode, Sketch, Text, Vector, Wire, sweep, trace
from build123d_drafting.helpers import (
    Arrow,
    Leader,
    _Annotation,
    _circle_arcs,
    _font_path,
    _segments,
)


class RoutedLeader(Leader):
    """A normal drafting leader whose shaft has solver-owned routing bends."""

    def __init__(
        self,
        tip,
        bends,
        elbow,
        label,
        draft,
        *,
        all_around=False,
        all_over=False,
        callout=None,
    ):
        if callout is not None and label:
            raise ValueError("Pass either label or callout, not both")
        tip_v = Vector(tip[0], tip[1], 0.0)
        if len(bends) == 2 and isinstance(bends[0], (int, float)):
            bends = (bends,)
        bend_vs = tuple(Vector(bend[0], bend[1], 0.0) for bend in bends)
        elbow_v = Vector(elbow[0], elbow[1], 0.0)
        route = (tip_v, *bend_vs, elbow_v)
        if not bend_vs or any(left == right for left, right in zip(route, route[1:])):
            raise ValueError("a routed leader needs bends and non-zero shaft segments")

        shaft_edges = tuple(Edge.make_line(left, right) for left, right in zip(route, route[1:]))
        arrow = Arrow(
            arrow_size=draft.arrow_length,
            shaft_path=Wire(list(shaft_edges)),
            shaft_width=draft.line_width,
            head_at_start=True,
            mode=Mode.PRIVATE,
        )
        shelf_direction = 1.0 if elbow_v.X >= route[-2].X else -1.0
        gap = draft.pad_around_text
        shelf_end = Vector(elbow_v.X + shelf_direction * gap, elbow_v.Y, 0.0)
        shelf_edge = Edge.make_line(elbow_v, shelf_end)
        shelf_pen = shelf_edge.perpendicular_line(draft.line_width, 0)
        shelf = sweep(shelf_pen, shelf_edge, mode=Mode.PRIVATE)
        faces = [*arrow.faces(), *shelf.faces()]

        rings = []
        if all_around or all_over:
            radius = 0.7 * draft.arrow_length
            for ring_radius in [radius, 1.7 * radius] if all_over else [radius]:
                rings.extend(_circle_arcs(ring_radius, elbow_v))
        if rings:
            faces.extend(trace(rings, line_width=draft.line_width).faces())

        text_x = elbow_v.X + shelf_direction * gap
        if callout is not None:
            callout_box = callout.bounding_box()
            anchor_x = callout_box.min.X if shelf_direction > 0 else callout_box.max.X
            middle_y = (callout_box.min.Y + callout_box.max.Y) / 2.0
            text_shape = callout.moved(
                Location(Vector(text_x - anchor_x, elbow_v.Y - middle_y, 0.0))
            )
        else:
            text_shape = Text(
                txt=label,
                font_size=draft.font_size,
                font=draft.font,
                font_path=_font_path(draft),
                align=(Align.MIN if shelf_direction > 0 else Align.MAX, Align.CENTER),
                mode=Mode.PRIVATE,
            ).moved(Location(Vector(text_x, elbow_v.Y, 0.0)))
        text_box = text_shape.bounding_box()
        faces.append(text_shape)

        _Annotation.__init__(
            self,
            Sketch(children=faces),
            label=label,
            label_bbox=(text_box.min.X, text_box.min.Y, text_box.max.X, text_box.max.Y),
            segments=_segments([*shaft_edges, shelf_edge]),
            mode=Mode.ADD,
        )
        self._tip_local = self._bake_point((tip_v.X, tip_v.Y))
        self._elbow_local = self._bake_point((elbow_v.X, elbow_v.Y))
        self._bends_local = tuple(self._bake_point((bend.X, bend.Y)) for bend in bend_vs)

        covers = getattr(callout, "covers_diameters", ())
        if covers:
            self.covers_diameters = tuple(covers)
            self.covers_count = getattr(callout, "covers_count", 1)

    @property
    def bend(self):
        return self.bends[-1]

    @property
    def bends(self):
        return tuple(self._live_point(bend) for bend in self._bends_local)
