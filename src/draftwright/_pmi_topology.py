"""Exact Part21 support resolvers for imported AP242 PMI topology.

Native STEP transfer binders prove source items match the originally imported
B-rep; geometric likeness alone cannot establish source ownership.
"""

from __future__ import annotations

from draftwright._pmi_datum_geometry import _frame_vector
from draftwright._pmi_support_blockers import _failure_reason

try:
    from OCP.TopAbs import TopAbs_COMPOUND, TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape
except ImportError:
    # The extractor reports unavailable OCP capability before using these resolvers.
    pass


def _parallel_planar_reference_support(groups, frame, shape_bbox) -> tuple[str | None, str | None]:
    """Find the common principal normal and any required witness view."""
    axis, view, _witness = _parallel_planar_reference_witness(groups, frame, shape_bbox)
    return axis, view


def _parallel_planar_reference_witness(groups, frame, shape_bbox):
    """Prove a view and, when its default midpoint misses, a shared face witness."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.TopoDS import TopoDS

    if len(groups) != 2 or not all(groups):
        return None, None, ()
    axes: list[int] = []
    group_boxes = []
    group_faces = []
    for shapes in groups:
        group_axis = None
        group_station = None
        boxes = []
        for shape in shapes:
            try:
                if shape.ShapeType() != TopAbs_FACE:
                    return None, None, ()
                surface = BRepAdaptor_Surface(TopoDS.Face_s(shape))
                if surface.GetType() != GeomAbs_Plane:
                    return None, None, ()
                direction = surface.Plane().Axis().Direction()
                local = _frame_vector((direction.X(), direction.Y(), direction.Z()), frame)
                axis_index = max(range(3), key=lambda index: abs(local[index]))
                if abs(abs(local[axis_index]) - 1.0) > 1e-6:
                    return None, None, ()
                if any(abs(local[index]) > 1e-6 for index in range(3) if index != axis_index):
                    return None, None, ()
                box = shape_bbox(shape) if frame is None else shape_bbox(shape, frame)
                boxes.append(box)
                station = (box[axis_index] + box[axis_index + 3]) / 2
                if box[axis_index + 3] - box[axis_index] > 1e-5:
                    return None, None, ()
            except Exception:
                return None, None, ()
            if group_axis is not None and group_axis != axis_index:
                return None, None, ()
            if group_station is not None and abs(group_station - station) > 1e-5:
                return None, None, ()
            group_axis, group_station = axis_index, station
        assert group_axis is not None  # Each validated source group contains a face.
        axes.append(group_axis)
        group_boxes.append(boxes)
        group_faces.append(shapes)
    if axes[0] != axes[1]:
        return None, None, ()
    axis = "XYZ"[axes[0]]
    if axis == "X":
        return _principal_x_planar_witness(group_boxes, group_faces, frame)
    view_axis, view = _supported_planar_view(axis, group_boxes)
    return view_axis, view, ()


def _principal_x_planar_witness(group_boxes, group_faces, frame):
    # A front projection only needs both planes represented at the same Z;
    # parallel face patches may be offset in Y (#2184). The plan fallback,
    # however, claims one shared Y/Z point and must prove it on both trims.
    if _midpoint_witness_supported(group_boxes, 2):
        return "X", None, ()
    common = _common_x_face_witness(group_boxes, group_faces, frame)
    if common is not None:
        y, z = common
        return "X", "plan", ((1, y), (2, z))
    return None, None, None


def _common_x_face_witness(group_boxes, group_faces, frame):
    """Find a point in the interiors of both exact, trimmed X faces."""
    candidates = [
        (
            (min(a[4], b[4]) - max(a[1], b[1])) * (min(a[5], b[5]) - max(a[2], b[2])),
            max(a[1], b[1]),
            max(a[2], b[2]),
            min(a[4], b[4]),
            min(a[5], b[5]),
            a,
            b,
            group_faces[0][i],
            group_faces[1][j],
        )
        for i, a in enumerate(group_boxes[0])
        for j, b in enumerate(group_boxes[1])
        if min(a[4], b[4]) >= max(a[1], b[1]) and min(a[5], b[5]) >= max(a[2], b[2])
    ]
    for _area, y0, z0, y1, z1, a, b, first, second in sorted(
        candidates, key=lambda item: (-item[0], item[1], item[2])
    ):
        # Start at the overlap centre, then sample its interior deterministically.
        # A bounding box may contain a face hole or a concavity; only OCC's
        # trimmed-face classifier can establish a real common witness.
        for y_fraction, z_fraction in ((0.5, 0.5),) + tuple(
            (i / 10, j / 10) for i in range(1, 10) for j in range(1, 10)
        ):
            y = y0 + (y1 - y0) * y_fraction
            z = z0 + (z1 - z0) * z_fraction
            if all(
                _point_inside_x_face(face, (box[0] + box[3]) / 2, y, z, frame)
                for face, box in ((first, a), (second, b))
            ):
                return y, z
    return None


def _point_inside_x_face(face, x, y, z, frame):
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_IN
    from OCP.TopoDS import TopoDS

    local = (x, y, z)
    world = local if frame is None else frame.to_world(local)
    classifier = BRepClass_FaceClassifier()
    try:
        classifier.Perform(TopoDS.Face_s(face), gp_Pnt(*world), 1e-7)
        return classifier.State() == TopAbs_IN
    except Exception:
        return False


def _supported_planar_view(axis: str, group_boxes) -> tuple[str | None, str | None]:
    """Select only projection views whose shared witness touches both groups."""
    if axis in {"X", "Z"}:
        return (
            (axis, None)
            if _midpoint_witness_supported(group_boxes, 2 if axis == "X" else 0)
            else (None, None)
        )
    side = _midpoint_witness_supported(group_boxes, 2)
    plan = _midpoint_witness_supported(group_boxes, 0)
    if side and plan:
        return axis, None
    if side or plan:
        return axis, "side" if side else "plan"
    return None, None


def _midpoint_witness_supported(group_boxes, index: int) -> bool:
    """Require a projected face in each group at the renderer's bbox midpoint."""
    all_boxes = [box for boxes in group_boxes for box in boxes]
    midpoint = (
        min(box[index] for box in all_boxes) + max(box[index + 3] for box in all_boxes)
    ) / 2
    return all(
        any(box[index] - 1e-6 <= midpoint <= box[index + 3] + 1e-6 for box in boxes)
        for boxes in group_boxes
    )


class _DatumTopologyResolver:
    """Resolve Part21 face labels through OCCT's native transfer binders.

    ``TransferOne(rank)`` stays inside C++, avoiding the OCP wrapper's loss of pointer
    identity when a STEP entity is returned to Python.  It reuses the existing transfer
    binder.  ``FindIndex`` then proves that result is ``IsSame`` to a face in the original
    imported topology; no geometric comparison participates in correspondence.
    """

    _dynamic_type = "StepShape_AdvancedFace"
    _part21_noun = "advanced face"
    _shape_noun = "face"
    _exclusive_claims = True

    def _expected_shape_type(self):
        return TopAbs_FACE

    def __init__(self, step_reader, imported_faces):
        self._reader = step_reader
        self._model = step_reader.StepModel()
        self._imported_faces = imported_faces
        self._items: dict[str, tuple[object, int]] = {}
        self._definitions: dict[str, tuple[object, ...]] = {}
        self._claims: dict[int, str] = {}

    def _transfer_face(self, item_id: str):
        cached = self._items.get(item_id)
        if cached is not None:
            return cached, ""
        rank = self._model.NextNumberForLabel(item_id, 0, True)
        if rank <= 0:
            return None, f"Part21 representation item {item_id} is unavailable"
        entity = self._model.Value(rank)
        if entity.DynamicType().Name() != self._dynamic_type:
            return None, f"Part21 representation item {item_id} is not an {self._part21_noun}"
        before = self._reader.NbShapes()
        try:
            transferred = self._reader.TransferOne(rank)
        except Exception as exc:
            return None, (
                f"Part21 representation item {item_id} could not be transferred "
                f"({_failure_reason(exc)})"
            )
        after = self._reader.NbShapes()
        if not transferred or after != before + 1:
            return None, f"Part21 representation item {item_id} could not be transferred"
        shape = self._reader.Shape(after)
        if shape is None or shape.IsNull() or shape.ShapeType() != self._expected_shape_type():
            return None, (
                f"Part21 representation item {item_id} did not transfer to one {self._shape_noun}"
            )
        face_index = self._imported_faces.FindIndex(shape)
        if face_index <= 0:
            return None, (
                f"Part21 representation item {item_id} is not a {self._shape_noun} in the "
                "imported topology"
            )
        result = (shape, face_index)
        self._items[item_id] = result
        return result, ""

    def resolve(
        self,
        definition_id: str,
        item_ids: tuple[str, ...],
        *,
        noun: str = "datum feature",
    ):
        """Return exact imported faces, or reasons that make the definition unsafe."""
        cached = self._definitions.get(definition_id)
        if cached is not None:
            return cached, ()
        if not item_ids:
            return (), (f"{noun} has no Part21 representation items",)

        resolved = []
        reasons: list[str] = []
        for item_id in item_ids:
            result, reason = self._transfer_face(item_id)
            if reason:
                reasons.append(reason)
            elif result is not None:
                resolved.append(result)
        if reasons:
            return (), tuple(dict.fromkeys(reasons))

        indices = [face_index for _shape, face_index in resolved]
        if len(set(indices)) != len(indices):
            return (), (
                f"{noun} representation items resolve to the same imported {self._shape_noun}",
            )
        if self._exclusive_claims:
            for face_index in indices:
                owner = self._claims.get(face_index)
                if owner is not None and owner != definition_id:
                    reasons.append(
                        f"one imported {self._shape_noun} is already claimed by {noun} {owner}"
                    )
            if reasons:
                return (), tuple(dict.fromkeys(reasons))

        shapes = tuple(shape for shape, _face_index in resolved)
        if self._exclusive_claims:
            for face_index in indices:
                self._claims[face_index] = definition_id
        self._definitions[definition_id] = shapes
        return shapes, ()


class _SurfaceLabelTopologyResolver(_DatumTopologyResolver):
    """Resolve surface-label edge items through the same identity-proof boundary."""

    _dynamic_type = "StepShape_EdgeCurve"
    _part21_noun = "edge curve"
    _shape_noun = "edge"
    _exclusive_claims = False

    def _expected_shape_type(self):
        return TopAbs_EDGE


class _CommonLabelTopologyResolver(_DatumTopologyResolver):
    """Resolve common-label face items without claiming them away from other PMI."""

    _exclusive_claims = False


class _DimensionSupportResolver:
    """Transfer exact dimension supports and prove B-rep items belong to the imported part."""

    def __init__(self, step_reader):
        self._reader = step_reader
        self._model = step_reader.StepModel()
        self._faces = TopTools_IndexedMapOfShape()
        self._edges = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(step_reader.OneShape(), TopAbs_FACE, self._faces)
        TopExp.MapShapes_s(step_reader.OneShape(), TopAbs_EDGE, self._edges)
        self._items: dict[str, object] = {}
        self._groups: dict[str, tuple[tuple[str, ...], tuple[object, ...]]] = {}

    def _transfer(self, item_id: str):
        cached = self._items.get(item_id)
        if cached is not None:
            return cached, ""
        rank = self._model.NextNumberForLabel(item_id, 0, True)
        if rank <= 0:
            return None, f"Part21 dimension support item {item_id} is unavailable"
        entity = self._model.Value(rank)
        dynamic_type = entity.DynamicType().Name()
        expected = {
            "StepShape_AdvancedFace": (TopAbs_FACE, self._faces, "face"),
            "StepShape_EdgeCurve": (TopAbs_EDGE, self._edges, "edge"),
            "StepShape_GeometricCurveSet": (TopAbs_COMPOUND, None, "geometric curve set"),
        }.get(dynamic_type)
        if expected is None:
            return None, (
                f"Part21 dimension support item {item_id} has unsupported type {dynamic_type}"
            )
        before = self._reader.NbShapes()
        try:
            transferred = self._reader.TransferOne(rank)
        except Exception as exc:
            return None, (
                f"Part21 dimension support item {item_id} could not be transferred "
                f"({_failure_reason(exc)})"
            )
        after = self._reader.NbShapes()
        if not transferred or after != before + 1:
            return None, f"Part21 dimension support item {item_id} could not be transferred"
        shape = self._reader.Shape(after)
        expected_type, imported, noun = expected
        if shape is None or shape.IsNull() or shape.ShapeType() != expected_type:
            return None, f"Part21 dimension support item {item_id} did not transfer to one {noun}"
        # Faces and edge curves are topology claims, so identity with the imported solid is
        # mandatory. A geometric-curve-set is authored construction geometry rather than a
        # B-rep subshape; its exact Part21 identity and successful transfer are the evidence.
        if imported is not None and imported.FindIndex(shape) <= 0:
            return None, f"Part21 dimension support item {item_id} is not in the imported topology"
        self._items[item_id] = shape
        return shape, ""

    def resolve_group(self, aspect_id: str, item_ids: tuple[str, ...]):
        cached = self._groups.get(aspect_id)
        if cached is not None:
            cached_item_ids, cached_shapes = cached
            if cached_item_ids != item_ids:
                return (), (f"dimension support group {aspect_id} has conflicting Part21 items",)
            return cached_shapes, ()
        if not item_ids:
            return (), (f"dimension support group {aspect_id} has no Part21 items",)
        shapes = []
        reasons = []
        for item_id in item_ids:
            shape, reason = self._transfer(item_id)
            if reason:
                reasons.append(reason)
            elif shape is not None:
                shapes.append(shape)
        if reasons:
            return (), tuple(dict.fromkeys(reasons))
        result = tuple(shapes)
        self._groups[aspect_id] = (item_ids, result)
        return result, ()
