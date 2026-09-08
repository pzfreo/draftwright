"""Analytic angular footprints shared by composition and rendered dimensions."""

from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, hypot, pi, sin


@dataclass(frozen=True)
class AngularStyle:
    """Fixed paper-space dimensions needed for analytic angular ink."""

    arrow_length: float
    extension_gap: float
    pad_around_text: float
    line_width: float


def _box(points, padding=0.0):
    xs, ys = zip(*points, strict=True)
    return min(xs) - padding, min(ys) - padding, max(xs) + padding, max(ys) + padding


def _rotate(point, angle):
    c, s = cos(angle), sin(angle)
    x, y = point
    return c * x - s * y, s * x + c * y


class AngularGeometry:
    """One projected angular intent; only its radius varies during placement.

    Coordinates belong to the already selected true-angle projection. The two
    directed rays fix the sector: moving an annotation cannot turn an acute
    dimension into its supplementary angle or move it to the opposite sector.
    """

    def __init__(self, vertex, first, second, text_size, draft, *, sector="minor"):
        if sector not in ("minor", "opposite"):
            raise ValueError("angular ink needs a minor or opposite sector")
        self.sector = sector
        self.vertex = tuple(vertex[:2])
        points = (tuple(first[:2]), tuple(second[:2]))
        rays = [tuple(p - v for p, v in zip(point, self.vertex, strict=True)) for point in points]
        lengths = tuple(hypot(*ray) for ray in rays)
        # Page-fit bisection can project valid model rays far below a nanometre.
        # Their directions still define the same angle and fixed-size label;
        # rejecting them here turns an infeasible sheet into a render exception.
        if min(lengths) == 0:
            raise ValueError("angular ink needs two nonzero projected rays")
        rays = [tuple(c / length for c in ray) for ray, length in zip(rays, lengths, strict=True)]
        if sector == "opposite":
            rays = [tuple(-component for component in ray) for ray in rays]
            lengths = tuple(-length for length in lengths)
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
        self.draft = draft
        self.text_size = text_size
        tangent = (self.middle + pi / 2) % (2 * pi)
        self.text_angle = tangent - pi if pi / 2 < tangent <= 3 * pi / 2 else tangent
        width, height = self.text_size
        # This bound leaves room for both arrow shafts and the full text. It also
        # keeps extension lines beyond their physical witness points. Opposite
        # witnesses have negative stations: their extensions cross the vertex.
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
