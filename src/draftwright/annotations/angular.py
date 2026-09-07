"""Angular ink and radius-dependent footprints for the shared corridor solve."""

from __future__ import annotations

from math import atan2, ceil, cos, degrees, hypot, pi, radians, sin

from build123d import Align, Arrow, Compound, Edge, Location, Mode, Text, trace

from draftwright._core import _text_size
from draftwright.fonts import PLEX_MONO


def _box(points, padding=0.0):
    xs, ys = zip(*points, strict=True)
    return min(xs) - padding, min(ys) - padding, max(xs) + padding, max(ys) + padding


def _rotate(point, angle):
    c, s = cos(angle), sin(angle)
    x, y = point
    return c * x - s * y, s * x + c * y


class AngularInk:
    """One projected angular intent; only its radius varies during placement.

    Coordinates belong to the already selected true-angle projection. The two
    directed rays fix the sector: moving an annotation cannot turn an acute
    dimension into its supplementary angle or move it to the opposite sector.
    """

    def __init__(self, vertex, first, second, label, draft):
        self.vertex = tuple(vertex[:2])
        points = (tuple(first[:2]), tuple(second[:2]))
        rays = [tuple(p - v for p, v in zip(point, self.vertex, strict=True)) for point in points]
        lengths = tuple(hypot(*ray) for ray in rays)
        if min(lengths) <= 1e-9:
            raise ValueError("angular ink needs two nonzero projected rays")
        rays = [tuple(c / length for c in ray) for ray, length in zip(rays, lengths, strict=True)]
        first_ray, second_ray = rays
        signed = atan2(
            first_ray[0] * second_ray[1] - first_ray[1] * second_ray[0],
            sum(a * b for a, b in zip(first_ray, second_ray, strict=True)),
        )
        self.sweep = abs(signed)
        if not 1e-9 < self.sweep < pi - 1e-9:
            raise ValueError("angular ink needs a nondegenerate minor sector")
        if signed < 0:
            first_ray, second_ray = second_ray, first_ray
            points = points[::-1]
            lengths = lengths[::-1]
        self.start = atan2(first_ray[1], first_ray[0])
        self.middle = self.start + self.sweep / 2
        self.rays = (first_ray, second_ray)
        self.witnesses = points
        self.lengths = lengths
        self.label = label
        self.draft = draft
        self.font_path = getattr(draft, "font_path", PLEX_MONO)
        self.text_size = _text_size(
            label, draft.font_size, self.font_path, draft.font, draft.font_style
        )
        tangent = (self.middle + pi / 2) % (2 * pi)
        self.text_angle = tangent - pi if pi / 2 < tangent <= 3 * pi / 2 else tangent
        width, height = self.text_size
        # This bound leaves room for both arrow shafts and the full text. It also
        # keeps extension lines beyond their physical witness points.
        self.minimum_radius = max(
            max(lengths) + draft.extension_gap + draft.arrow_length,
            (width + height + 2 * draft.pad_around_text + 4 * draft.arrow_length) / self.sweep
            + height / 2
            + draft.pad_around_text,
        )

    @property
    def bisector(self):
        return cos(self.middle), sin(self.middle)

    def point(self, radius, angle):
        x, y = self.vertex
        return x + radius * cos(angle), y + radius * sin(angle)

    def label_polygon(self, radius):
        width, height = self.text_size
        centre = self.point(radius, self.middle)
        return tuple(
            tuple(a + b for a, b in zip(centre, _rotate(point, self.text_angle), strict=True))
            for point in (
                (-width / 2, -height / 2),
                (width / 2, -height / 2),
                (width / 2, height / 2),
                (-width / 2, height / 2),
            )
        )

    def extension_segments(self, radius):
        end_radius = radius + self.draft.extension_gap
        return tuple(
            (
                self.point(length + self.draft.extension_gap, angle),
                self.point(end_radius, angle),
            )
            for length, angle in zip(
                self.lengths, (self.start, self.start + self.sweep), strict=True
            )
        )

    def arc_intervals(self, radius):
        width, height = self.text_size
        gap = atan2(
            width / 2 + self.draft.pad_around_text,
            radius - height / 2 - self.draft.pad_around_text,
        )
        intervals = ((self.start, self.middle - gap), (self.middle + gap, self.start + self.sweep))
        if any((end - start) * radius <= self.draft.arrow_length for start, end in intervals):
            raise ValueError("angular radius cannot carry the complete label and arrows")
        return intervals

    def footprint(self, radius):
        """Conservative analytic ink box, including arc extrema and arrow heads.

        The perpendicular extent changes with radius. Supplying this function to
        the corridor prevents its straight-dimension translation model from
        hiding obstacles from an angular candidate.
        """
        angles = [self.start, self.start + self.sweep]
        for index in range(-4, 9):
            angle = index * pi / 2
            if self.start < angle < self.start + self.sweep:
                angles.append(angle)
        points = [self.point(radius, angle) for angle in angles]
        points.extend(point for segment in self.extension_segments(radius) for point in segment)
        points.extend(self.label_polygon(radius))
        return _box(points, self.draft.arrow_length + self.draft.line_width)

    def build(self, radius):
        return AngularDimension(self, radius)


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
        extensions = trace(
            [Edge.make_line((*start, 0), (*end, 0)) for start, end in segments],
            line_width=draft.line_width,
            mode=Mode.PRIVATE,
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
        self._label_polygon = ink.label_polygon(radius)
        self._extension_segments = segments
        self._radius = radius
        self._sweep = ink.sweep
        self._arc_intervals = intervals
        self._vertex = ink.vertex

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
