"""Semantic text overlay for PDFs rendered from vector glyph paths.

This rank-2 export peer consumes the drawing's draft and named annotations
explicitly. Drawing passes both values at its delegation seam.
"""

from __future__ import annotations

import math
from itertools import permutations

from build123d import Align, Mode, Text
from build123d_drafting.helpers import DEFAULT_FONT_PATH

from draftwright._core import _font_safe_text, _table_metrics, _text_line_spacing_em, _text_size
from draftwright.export import _PDFTextRun
from draftwright.fonts import PLEX_MONO
from draftwright.registry import DimensionPlacementSpec, PlacedDimension, RegisteredDimensionSpec


def _exact_vertex_rotation(source_vertices, target_vertices) -> float | None:
    """Rotation when two ordered outlines differ only by translation/scale/rotation."""
    if len(source_vertices) != len(target_vertices) or len(source_vertices) < 2:
        return None
    source = [complex(point.X, point.Y) for point in source_vertices]
    target = [complex(point.X, point.Y) for point in target_vertices]
    source_mean, target_mean = sum(source) / len(source), sum(target) / len(target)
    source = [point - source_mean for point in source]
    target = [point - target_mean for point in target]
    source_norm = sum(abs(point) ** 2 for point in source)
    target_norm = sum(abs(point) ** 2 for point in target)
    if min(source_norm, target_norm) < 1e-12:
        return None
    transform = sum(t * s.conjugate() for s, t in zip(source, target, strict=True)) / source_norm
    error = sum(abs(transform * s - t) ** 2 for s, t in zip(source, target, strict=True))
    if error / target_norm >= 1e-12:
        return None
    return math.degrees(math.atan2(transform.imag, transform.real))


def _exact_face_rotation(source_faces, target_faces) -> float | None:
    """Exact outline rotation independent of disconnected-face enumeration order."""
    if len(source_faces) != len(target_faces) or not source_faces or len(source_faces) > 6:
        return None
    source_vertices = [vertex for face in source_faces for vertex in face.vertices()]
    source_counts = [len(face.vertices()) for face in source_faces]
    for ordered_targets in permutations(target_faces):
        if source_counts != [len(face.vertices()) for face in ordered_targets]:
            continue
        target_vertices = [vertex for face in ordered_targets for vertex in face.vertices()]
        rotation = _exact_vertex_rotation(source_vertices, target_vertices)
        if rotation is not None:
            return rotation
    return None


_PDF_VECTOR_ONLY_TEXT = str.maketrans("⌴⌵↧", "   ")


def _semantic_text(value) -> str:
    # The bundled faces do not carry the geometric counterbore,
    # countersink or depth glyphs.  They remain visibly rendered by the
    # existing vector callout; spaces keep the neighbouring supported
    # terms separate in copied/searchable text.
    return _font_safe_text(value).translate(_PDF_VECTOR_ONLY_TEXT)


def _raw_dimension_candidates(annotation, value, draft):
    """Possible visible labels when an external helper discarded constructor args."""
    bare_zero = draft._number_with_units(0.0, display_units=False)
    unit_zero = draft._number_with_units(0.0, display_units=True)
    unit_suffix = unit_zero.removeprefix(bare_zero) if draft.display_units else ""
    candidates = [value, draft._number_with_units(annotation.measured_length)]
    if unit_suffix and not value.endswith(unit_suffix):
        candidates.append(value + unit_suffix)
    bare_value = draft._number_with_units(annotation.measured_length, display_units=False)
    prefix, separator, limits = value.partition(" +")
    upper, minus, lower = limits.partition(" -")
    if separator and minus and prefix == bare_value:
        # Helper compatibility metadata reverses asymmetric tuple limits relative
        # to the visible auto-generated string. Keep the authored candidate too;
        # exact live ink bounds distinguish an explicit lookalike label.
        candidates.insert(0, f"{prefix} +{lower} -{upper}{unit_suffix}")
    return list(dict.fromkeys(candidates))


def _raw_dimension_label_is_freeform(annotation, value, draft):
    """Whether retained helper metadata can only have come from ``label=``."""
    numeric_prefix = value.split(" ±", 1)[0].split(" +", 1)[0]
    try:
        measured_value = float(annotation.measured_length)
        if not math.isclose(
            float(numeric_prefix),
            measured_value,
            abs_tol=1e-9,
        ):
            return True
        # A numerically equivalent label can still be authored text (for
        # example ``label="1"`` when the active draft would render ``1.0``).
        # Raw helpers discard that constructor provenance, so preserve the
        # spelling whenever it differs from this drawing's automatic form.
        return value == numeric_prefix and value != draft._number_with_units(
            measured_value,
            display_units=False,
        )
    except ValueError:
        return True


def _centred_runs(
    value,
    label_box,
    font_size,
    rotation,
    font_path,
    font_name="Arial",
    font_style="REGULAR",
    line_spacing=None,
):
    lines = _semantic_text(value).splitlines()
    if not lines:
        return []
    x0, y0, x1, y1 = label_box
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    angle = math.radians(rotation)
    sin_angle, cos_angle = math.sin(angle), math.cos(angle)
    if line_spacing is None:
        line_spacing = _text_line_spacing_em(font_size, font_path, font_name)
    runs = []
    for index, line in enumerate(lines):
        if not line:
            continue
        local_y = ((len(lines) - 1) / 2.0 - index) * font_size * line_spacing
        runs.append(
            _PDFTextRun(
                line,
                cx - sin_angle * local_y,
                cy + cos_angle * local_y,
                font_size,
                rotation=rotation,
                font_path=font_path,
                font_name=font_name,
                font_style=font_style,
                h_align="center",
                v_align="middle",
            )
        )
    return runs


class _TextRotation:
    """Recover the final label angle from authored specs or live helper ink."""

    def __init__(self, draft, fs, drawing_font_path, drawing_font_name):
        self.draft = draft
        self.fs = fs
        self.drawing_font_path = drawing_font_path
        self.drawing_font_name = drawing_font_name
        self.raw_basic_matches: dict[int, tuple[str, float]] = {}
        self.raw_basic_exact_matches: set[int] = set()
        self.raw_basic_unresolved: set[int] = set()
        self.dimension_specs: dict[int, DimensionPlacementSpec | RegisteredDimensionSpec] = {}

    def angle(self, annotation) -> float:
        def upright(angle: float) -> float:
            angle = (angle + 90.0) % 180.0 - 90.0
            if math.isclose(angle, -90.0, abs_tol=1e-6):
                return 90.0
            return angle

        live_rotation = float(getattr(annotation.location.orientation, "Z", 0.0))
        explicit = getattr(annotation, "pdf_text_rotation", None)
        if explicit is not None:
            return float(explicit) + live_rotation
        spec = self.dimension_specs.get(id(annotation))
        if spec is not None and hasattr(annotation, "measured_length"):
            dx, dy = spec.p2[0] - spec.p1[0], spec.p2[1] - spec.p1[1]
            if math.hypot(dx, dy) > 1e-9:
                # Dimension first makes its path label upright, then BaseSketchObject
                # applies constructor/live transforms to the whole annotation. Do not
                # upright-normalise again after those transforms: 120° must remain 120°.
                return (
                    upright(math.degrees(math.atan2(dy, dx)))
                    + (
                        spec.rotation
                        if isinstance(spec, RegisteredDimensionSpec)
                        else float(spec.kwargs.get("rotation", 0.0))
                    )
                    + live_rotation
                )
        if getattr(annotation, "is_basic", False):
            if match := self.raw_basic_matches.get(id(annotation)):
                return match[1]
            # Raw helper geometry has already absorbed its constructor rotation; live
            # location transforms are reflected by ``segments`` too. Match the helper's
            # operation order: upright the pre-transform axis, then reapply both.
            transform_rotation = float(getattr(annotation, "_init_rot", 0.0)) + live_rotation

            def raw_basic_text_angle(final_axis: float) -> float:
                return upright(final_axis - transform_rotation) + transform_rotation

            # A basic dimension's keep-clear polygon is its axis-aligned frame,
            # not its rotated text. For short labels, distinguish collinear shaft spans,
            # parallel witness
            # lines, and the mixed one-shaft/one-witness case. Helper span order puts
            # a surviving shaft before witnesses.
            segments = list(getattr(annotation, "segments", ()))
            ink = segments[:-4] if len(segments) >= 4 else segments
            if len(ink) == 2:
                (a0, a1), (b0, b1) = ink
                dx, dy = a1[0] - a0[0], a1[1] - a0[1]
                angle = math.degrees(math.atan2(dy, dx))
                bx, by = b1[0] - b0[0], b1[1] - b0[1]
                if abs(dx * by - dy * bx) > 1e-7:
                    return raw_basic_text_angle(angle)
                separation = dx * (b0[1] - a0[1]) - dy * (b0[0] - a0[0])
                axis = angle if abs(separation) < 1e-7 else angle + 90.0
                return raw_basic_text_angle(axis)
            for start, end in ink:
                dx, dy = end[0] - start[0], end[1] - start[1]
                if math.hypot(dx, dy) > 1e-9:
                    return raw_basic_text_angle(math.degrees(math.atan2(dy, dx)))
            # An extremely wide label can consume every shaft and witness span. Recover
            # angle magnitude from the rotated text AABB (the frame adds a known pad),
            # using glyph-face covariance only for the sign.
            if label_box := getattr(annotation, "label_bbox", None):
                x0, y0, x1, y1 = label_box
                inset = 0.05 * self.fs
                centres = []
                glyph_faces = []
                for face in annotation.faces():
                    face_box = face.bounding_box()
                    if (
                        face_box.min.X >= x0 + inset
                        and face_box.max.X <= x1 - inset
                        and face_box.min.Y >= y0 + inset
                        and face_box.max.Y <= y1 - inset
                    ):
                        centre = face.center()
                        centres.append((centre.X, centre.Y))
                        glyph_faces.append(face)
                if glyph_faces:
                    min_area = max(face.area for face in glyph_faces) * 0.01
                    glyph_faces = [face for face in glyph_faces if face.area >= min_area]
                    centres = [(face.center().X, face.center().Y) for face in glyph_faces]
                shape_matches: list[tuple[float, str, float]] = []
                raw_label = str(getattr(annotation, "label", ""))
                for candidate in _raw_dimension_candidates(annotation, raw_label, self.draft):
                    source_face_options = []
                    for font_path in dict.fromkeys((self.drawing_font_path, DEFAULT_FONT_PATH)):
                        faces = Text(
                            txt=candidate,
                            font_size=self.fs,
                            font=self.drawing_font_name,
                            font_style=self.draft.font_style,
                            font_path=font_path,
                            align=(Align.CENTER, Align.CENTER),
                            mode=Mode.PRIVATE,
                        ).faces()
                        if len(faces) != len(glyph_faces) or not faces:
                            continue

                        def face_ratio(face):
                            box = face.oriented_bounding_box()
                            sizes = sorted((box.size.X, box.size.Y, box.size.Z), reverse=True)
                            return sizes[0] / max(sizes[1], 1e-12)

                        source_area = sum(face.area for face in faces)
                        target_area = sum(face.area for face in glyph_faces)
                        font_error = sum(
                            abs(source.area / source_area - target.area / target_area)
                            + abs(math.log(face_ratio(source) / face_ratio(target)))
                            for source, target in zip(faces, glyph_faces, strict=True)
                        )
                        source_face_options.append((font_error, faces))
                    if not source_face_options:
                        continue
                    if len(candidate) == 1:
                        for _font_error, exact_faces in source_face_options:
                            exact_rotation = _exact_face_rotation(exact_faces, glyph_faces)
                            if exact_rotation is not None:
                                self.raw_basic_exact_matches.add(id(annotation))
                                shape_matches.append((0.0, candidate, exact_rotation))
                                break
                        else:
                            exact_rotation = None
                        if exact_rotation is not None:
                            continue
                    _font_error, source_faces = min(source_face_options, key=lambda item: item[0])
                    if len(source_faces) == 1:

                        def line_directions(face):
                            directions = []
                            for edge in face.edges():
                                if getattr(edge.geom_type, "name", "") != "LINE":
                                    continue
                                tangent = edge.tangent_at(0.5)
                                directions.append(
                                    (
                                        math.degrees(math.atan2(tangent.Y, tangent.X)) % 180.0,
                                        edge.length,
                                    )
                                )
                            return directions

                        def direction_stats(directions, angle):
                            matching = [
                                weight
                                for direction, weight in directions
                                if abs((direction - angle + 90.0) % 180.0 - 90.0) < 1e-5
                            ]
                            return sum(matching), len(matching)

                        source_directions = line_directions(source_faces[0])
                        target_directions = line_directions(glyph_faces[0])
                        source_horizontal, source_horizontal_count = direction_stats(
                            source_directions, 0.0
                        )
                        source_vertical, source_vertical_count = direction_stats(
                            source_directions, 90.0
                        )
                        source_axis_total = source_horizontal + source_vertical
                        if source_axis_total > 1e-9 and target_directions:
                            axis_matches = []
                            source_share = source_horizontal / source_axis_total
                            for direction, _weight in target_directions:
                                for angle in (direction, direction - 90.0):
                                    target_horizontal, target_horizontal_count = direction_stats(
                                        target_directions, angle
                                    )
                                    target_vertical, target_vertical_count = direction_stats(
                                        target_directions, angle + 90.0
                                    )
                                    target_axis_total = target_horizontal + target_vertical
                                    if target_axis_total > 1e-9:
                                        axis_matches.append(
                                            (
                                                abs(
                                                    source_share
                                                    - target_horizontal / target_axis_total
                                                )
                                                + (
                                                    abs(
                                                        source_horizontal_count
                                                        - target_horizontal_count
                                                    )
                                                    + abs(
                                                        source_vertical_count
                                                        - target_vertical_count
                                                    )
                                                )
                                                / max(
                                                    1,
                                                    source_horizontal_count
                                                    + source_vertical_count,
                                                ),
                                                angle,
                                            )
                                        )
                            if axis_matches:
                                error, angle = min(axis_matches)
                                shape_matches.append((error, candidate, angle))
                                continue
                        source_obb = source_faces[0].oriented_bounding_box()
                        target_obb = glyph_faces[0].oriented_bounding_box()
                        source_axes = sorted(
                            (
                                (source_obb.size.X, source_obb.plane.x_dir),
                                (source_obb.size.Y, source_obb.plane.y_dir),
                                (source_obb.size.Z, source_obb.plane.z_dir),
                            ),
                            key=lambda item: item[0],
                            reverse=True,
                        )
                        target_axes = sorted(
                            (
                                (target_obb.size.X, target_obb.plane.x_dir),
                                (target_obb.size.Y, target_obb.plane.y_dir),
                                (target_obb.size.Z, target_obb.plane.z_dir),
                            ),
                            key=lambda item: item[0],
                            reverse=True,
                        )
                        if min(source_axes[1][0], target_axes[1][0]) < 1e-9:
                            continue
                        source_ratio = source_axes[0][0] / source_axes[1][0]
                        target_ratio = target_axes[0][0] / target_axes[1][0]
                        aspect_error = abs(math.log(source_ratio / target_ratio))
                        source_dir = source_axes[0][1]
                        target_dir = target_axes[0][1]
                        dot = source_dir.X * target_dir.X + source_dir.Y * target_dir.Y
                        cross = source_dir.X * target_dir.Y - source_dir.Y * target_dir.X
                        shape_matches.append(
                            (
                                aspect_error,
                                candidate,
                                math.degrees(math.atan2(cross, dot)),
                            )
                        )
                        continue
                    else:
                        source_points = [
                            (face.center().X, face.center().Y) for face in source_faces
                        ]
                        target_points = centres
                    source_mean = (
                        sum(point[0] for point in source_points) / len(source_points),
                        sum(point[1] for point in source_points) / len(source_points),
                    )
                    target_mean = (
                        sum(point[0] for point in target_points) / len(target_points),
                        sum(point[1] for point in target_points) / len(target_points),
                    )
                    dot = cross = 0.0
                    for source, target in zip(source_points, target_points, strict=True):
                        sx, sy = source[0] - source_mean[0], source[1] - source_mean[1]
                        tx, ty = target[0] - target_mean[0], target[1] - target_mean[1]
                        dot += sx * tx + sy * ty
                        cross += sx * ty - sy * tx
                    if math.hypot(dot, cross) < 1e-9:
                        continue
                    angle = math.atan2(cross, dot)
                    cos_angle, sin_angle = math.cos(angle), math.sin(angle)
                    error = 0.0
                    for source, target in zip(source_points, target_points, strict=True):
                        sx, sy = source[0] - source_mean[0], source[1] - source_mean[1]
                        predicted = (
                            target_mean[0] + cos_angle * sx - sin_angle * sy,
                            target_mean[1] + sin_angle * sx + cos_angle * sy,
                        )
                        error += math.dist(predicted, target) ** 2
                    for source_face, target_face in zip(source_faces, glyph_faces, strict=True):
                        error += (source_face.area - target_face.area) ** 2
                    shape_matches.append((error, candidate, math.degrees(angle)))
                if len(raw_label) == 1 and id(annotation) not in self.raw_basic_exact_matches:
                    # External helpers discard their construction Draft.  A guessed
                    # face can put selectable text far from custom-font ink, so retain
                    # the helper's visible vector glyph unless its outline proves the
                    # semantic face and rotation exactly.
                    self.raw_basic_unresolved.add(id(annotation))
                if shape_matches:
                    _error, matched_text, matched_angle = min(shape_matches)
                    matched_angle = raw_basic_text_angle(matched_angle)
                    self.raw_basic_matches[id(annotation)] = (matched_text, matched_angle)
                    return matched_angle
        polygon = getattr(annotation, "label_polygon", None)
        if polygon and len(polygon) >= 2:
            (x0, y0), (x1, y1) = polygon[:2]
            return math.degrees(math.atan2(y1 - y0, x1 - x0))
        return live_rotation


def pdf_text_runs(draft, annotations, *, dimension_spec_of=None):
    """Return semantic text overlaid on path-rendered PDF glyphs.

    The visible glyphs remain paths.  Dimensions, leaders and notes expose
    their authoritative label plus final label geometry; tables retain their
    source rows; the title block retains public-cell-centred value specs.
    Unsupported feature symbols stay path-only while adjacent text remains
    selectable, rather than forcing the complete callout back to outlines.
    """
    groups = []
    fs = draft.font_size
    pad = draft.pad_around_text
    drawing_font_path = getattr(draft, "font_path", DEFAULT_FONT_PATH)
    drawing_font_name = getattr(draft, "font", "Arial")
    drawing_font_style = getattr(getattr(draft, "font_style", None), "name", "REGULAR")
    _angles = _TextRotation(draft, fs, drawing_font_path, drawing_font_name)

    for ordinal, (name, annotation) in enumerate(annotations):
        spec = (
            dimension_spec_of(name)
            if dimension_spec_of is not None
            else annotation.placement_spec
            if isinstance(annotation, PlacedDimension)
            else None
        )
        if spec is not None:
            _angles.dimension_specs[id(annotation)] = spec
        box = annotation.bounding_box()
        rows = getattr(annotation, "table_rows", None)
        runs = []
        if rows:
            lefts, _rights, _width, total_h, row_h, _bc = _table_metrics(
                rows, fs, pad, getattr(annotation, "table_block_cols", None)
            )
            for ri, row in enumerate(rows):
                baseline = box.min.Y + total_h - (ri + 0.5) * row_h - fs * 0.35
                for ci, cell in enumerate(row):
                    if cell:
                        runs.append(
                            _PDFTextRun(
                                _semantic_text(cell),
                                box.min.X + lefts[ci] + pad,
                                baseline,
                                fs,
                                font_path=PLEX_MONO,
                            )
                        )
        elif specs := getattr(annotation, "pdf_text_specs", None):
            for value, x, y, font_size, font_path in specs:
                runs.extend(_centred_runs(value, (x, y, x, y), font_size, 0.0, font_path))
        elif specs := getattr(annotation, "pdf_text_relative_specs", None):
            label_box = getattr(annotation, "label_bbox", None)
            if label_box:
                cx = (label_box[0] + label_box[2]) / 2.0
                cy = (label_box[1] + label_box[3]) / 2.0
                rotation = _angles.angle(annotation)
                angle = math.radians(rotation)
                cos_angle, sin_angle = math.cos(angle), math.sin(angle)
                for spec in specs:
                    value, rx, ry, size, path, name, style, *align = spec
                    h_align, v_align = align or ("center", "middle")
                    runs.append(
                        _PDFTextRun(
                            _semantic_text(value),
                            cx + cos_angle * rx - sin_angle * ry,
                            cy + sin_angle * rx + cos_angle * ry,
                            size,
                            rotation=rotation,
                            font_path=path,
                            font_name=name,
                            font_style=style,
                            h_align=h_align,
                            v_align=v_align,
                        )
                    )
        else:
            value = getattr(annotation, "pdf_text", None)
            if value is None:
                value = getattr(annotation, "label", None)
            label_box = getattr(annotation, "label_bbox", None)
            if value and label_box:
                raw_freeform = False
                run_font_size = fs
                run_font_path = drawing_font_path
                run_font_name = drawing_font_name
                run_font_style = drawing_font_style
                run_font_style_enum = draft.font_style
                if hasattr(annotation, "measured_length"):
                    dimension_draft = (
                        spec.live_draft
                        if isinstance(spec, RegisteredDimensionSpec)
                        else spec.draft
                        if spec is not None
                        else draft
                    )
                    run_font_size = dimension_draft.font_size
                    run_font_path = getattr(dimension_draft, "font_path", DEFAULT_FONT_PATH)
                    run_font_name = getattr(dimension_draft, "font", "Arial")
                    run_font_style_enum = dimension_draft.font_style
                    run_font_style = getattr(dimension_draft.font_style, "name", "REGULAR")
                    if spec is not None and spec.kwargs.get("label") is None:
                        # The engine owns this construction spec, including tolerance;
                        # reproduce the exact helper-rendered label rather than its
                        # intentionally unitless compatibility metadata.
                        value = dimension_draft._number_with_units(
                            annotation.measured_length,
                            spec.kwargs.get("tolerance"),
                        )
                    elif spec is None:
                        # External raw helpers retain no label/tolerance constructor args.
                        # Compare the few possible unit renderings to the live label geometry;
                        # this also preserves explicit custom labels (their own width wins).
                        raw_freeform = _raw_dimension_label_is_freeform(
                            annotation,
                            value,
                            dimension_draft,
                        )
                        candidates = (
                            [value]
                            if raw_freeform
                            else _raw_dimension_candidates(annotation, value, dimension_draft)
                        )
                        rotation = _angles.angle(annotation)
                        if id(annotation) in _angles.raw_basic_unresolved:
                            continue
                        if getattr(annotation, "is_basic", False):
                            x0, y0, x1, y1 = label_box
                            actual = (x1 - x0, y1 - y0)
                            transform = math.radians(
                                float(getattr(annotation, "_init_rot", 0.0))
                                + float(getattr(annotation.location.orientation, "Z", 0.0))
                            )
                            base_angle = math.radians(rotation) - transform

                            def geometry_error(candidate):
                                width, height = _text_size(
                                    candidate,
                                    run_font_size,
                                    run_font_path,
                                    run_font_name,
                                    run_font_style_enum,
                                )
                                frame_width = (
                                    abs(width * math.cos(base_angle))
                                    + abs(height * math.sin(base_angle))
                                    + 0.8 * run_font_size
                                )
                                frame_height = (
                                    abs(width * math.sin(base_angle))
                                    + abs(height * math.cos(base_angle))
                                    + 0.8 * run_font_size
                                )
                                predicted = (
                                    abs(frame_width * math.cos(transform))
                                    + abs(frame_height * math.sin(transform)),
                                    abs(frame_width * math.sin(transform))
                                    + abs(frame_height * math.cos(transform)),
                                )
                                return math.dist(actual, predicted)

                        else:
                            polygon = getattr(annotation, "label_polygon", None)
                            visible_width = (
                                math.dist(polygon[0], polygon[1])
                                if polygon
                                else label_box[2] - label_box[0]
                            )

                            def geometry_error(candidate):
                                width = _text_size(
                                    candidate,
                                    run_font_size,
                                    run_font_path,
                                    run_font_name,
                                    run_font_style_enum,
                                )[0]
                                return abs(width - visible_width)

                        match = _angles.raw_basic_matches.get(id(annotation))
                        value = (
                            match[0]
                            if match is not None and match[0] in candidates
                            else min(candidates, key=geometry_error)
                        )
                        if raw_freeform:
                            polygon = getattr(annotation, "label_polygon", None)
                            if polygon:
                                visible_width = math.dist(polygon[0], polygon[1])
                                unit_width, unit_height = _text_size(
                                    value,
                                    1.0,
                                    run_font_path,
                                    run_font_name,
                                    run_font_style_enum,
                                )
                                if getattr(annotation, "is_basic", False):
                                    unit_width = (
                                        abs(unit_width * math.cos(base_angle))
                                        + abs(unit_height * math.sin(base_angle))
                                        + 0.8
                                    )
                                if unit_width > 1e-9:
                                    run_font_size = visible_width / unit_width
                if not hasattr(annotation, "measured_length"):
                    run_font_style = getattr(annotation, "pdf_text_font_style", "REGULAR")
                runs.extend(
                    _centred_runs(
                        value,
                        label_box,
                        run_font_size,
                        _angles.angle(annotation),
                        run_font_path,
                        run_font_name,
                        run_font_style,
                        getattr(annotation, "pdf_text_line_spacing", None),
                    )
                )
        if runs:
            groups.append((-box.max.Y, box.min.X, ordinal, runs))
    return tuple(run for _top, _left, _ordinal, runs in sorted(groups) for run in runs)
