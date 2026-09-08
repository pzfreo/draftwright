"""Angular ink and radius-dependent footprints for the shared corridor solve."""

from __future__ import annotations

from math import ceil, cos, degrees, radians, sin

from build123d import Align, Arrow, Compound, Edge, Location, Mode, Text, trace

from draftwright._core import _text_size
from draftwright.angular_geometry import AngularGeometry, _box, _rotate
from draftwright.fonts import PLEX_MONO


class AngularInk(AngularGeometry):
    """A projected angular intent with measured text and bounded rendered candidates."""

    def __init__(self, vertex, first, second, label, draft, *, sector="minor"):
        self.label = label
        self.font_path = getattr(draft, "font_path", PLEX_MONO)
        text_size = _text_size(
            label, draft.font_size, self.font_path, draft.font, draft.font_style
        )
        super().__init__(vertex, first, second, text_size, draft, sector=sector)

    def build(self, radius):
        return AngularDimension(self, radius)

    def compact_candidates(self, step):
        """Three corner-local alternatives at the existing dimension tier spacing."""
        for index in range(3):
            yield self.build(self.minimum_radius + index * step)


class AngularDimension(Compound):
    """Arc, arrow heads, complete label and extension lines, with movable metadata."""

    def __init__(self, ink: AngularInk, radius: float):
        if radius < ink.minimum_radius:
            raise ValueError("angular radius is below the legibility bound")
        draft = ink.draft
        intervals = ink.arc_intervals(radius)
        shafts = [
            Edge.make_circle(radius, start_angle=degrees(start), end_angle=degrees(end)).moved(
                Location((*ink.vertex, 0))
            )
            for start, end in intervals
        ]
        arrows = [
            Arrow(
                draft.arrow_length,
                shaft,
                draft.line_width,
                head_at_start=index == 0,
                head_type=draft.head_type,
                mode=Mode.PRIVATE,
            )
            for index, shaft in enumerate(shafts)
        ]
        segments = ink.extension_segments(radius)
        # Opposite-sector extensions cross at the virtual vertex. Keep both
        # strokes intact rather than fusing away their intersecting boundaries.
        extensions = Compound(
            children=[
                trace(
                    Edge.make_line((*start, 0), (*end, 0)),
                    line_width=draft.line_width,
                    mode=Mode.PRIVATE,
                )
                for start, end in segments
            ]
        )
        text = Text(
            ink.label,
            font_size=draft.font_size,
            font_path=ink.font_path,
            font=draft.font,
            font_style=draft.font_style,
            align=Align.CENTER,
            mode=Mode.PRIVATE,
        )
        box = text.bounding_box()
        text = text.moved(
            Location((-(box.min.X + box.max.X) / 2, -(box.min.Y + box.max.Y) / 2, 0))
        )
        centre = ink.point(radius, ink.middle)
        text = text.moved(Location((*centre, 0), (0, 0, degrees(ink.text_angle))))
        super().__init__(children=[*arrows, extensions, text], label=ink.label)
        self._angular_points = (ink.witnesses[0], ink.vertex, ink.witnesses[1])
        self.angular_sector = ink.sector
        self._label_polygon = ink.label_polygon(radius)
        self._extension_segments = segments
        self._radius = radius
        self._sweep = ink.sweep
        self._arc_intervals = intervals
        self._vertex = ink.vertex

    @property
    def arc_radius(self):
        return self._radius

    def _point(self, point):
        rotated = _rotate(point, radians(self.location.orientation.Z))
        return rotated[0] + self.location.position.X, rotated[1] + self.location.position.Y

    @property
    def angular_points(self):
        return tuple(self._point(point) for point in self._angular_points)

    @property
    def measured_angle(self):
        return degrees(self._sweep)

    @property
    def label_polygon(self):
        return tuple(self._point(point) for point in self._label_polygon)

    @property
    def label_bbox(self):
        return _box(self.label_polygon)

    @property
    def segments(self):
        # The existing ink critic accepts line segments. Bound arc chord error
        # by 0.005mm so curved shafts also participate in collision checks.
        result = list(self._extension_segments)
        for start, end in self._arc_intervals:
            count = max(1, ceil((end - start) * (self._radius / 0.04) ** 0.5))
            points = [
                (
                    self._vertex[0] + self._radius * cos(start + (end - start) * index / count),
                    self._vertex[1] + self._radius * sin(start + (end - start) * index / count),
                )
                for index in range(count + 1)
            ]
            result.extend(zip(points, points[1:]))
        return tuple((self._point(start), self._point(end)) for start, end in result)
