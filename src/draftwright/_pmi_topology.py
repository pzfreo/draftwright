"""Exact Part21 support resolvers for imported AP242 PMI topology.

Native STEP transfer binders prove source items match the originally imported
B-rep; geometric likeness alone cannot establish source ownership.
"""

from __future__ import annotations

from draftwright._pmi_support_blockers import _failure_reason

try:
    from OCP.TopAbs import TopAbs_COMPOUND, TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape
except ImportError:
    # The extractor reports unavailable OCP capability before using these resolvers.
    pass


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
