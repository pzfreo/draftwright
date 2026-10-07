"""Caller-owned exact-face cache for repeated fixed-ink validation (#2207).

The cached faces are rendered annotation ink, not merely projected part faces.
Part/scale/view identity therefore cannot prove reuse after a layout edit: the key
must contain the complete located face geometry as well as tessellation tolerance.
"""

from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from io import BytesIO
from threading import Lock

from OCP.BRepTools import BRepTools
from OCP.TopTools import TopTools_FormatVersion

FaceMesh = tuple[
    tuple[tuple[float, float], ...],
    tuple[tuple[int, int, int], ...],
    tuple[str, ...],
]


class FixedInkMeshCache:
    """Bounded, reusable cache for exact rendered face meshes.

    Pass one instance to successive ``Sheet.build(mesh_cache=...)`` calls. The
    cache is optional, never global, and safe to reuse across part/scale/view
    changes because its key includes each *located* face's exact B-rep. A
    failed key or malformed mesh is never cached; normal validation still runs.
    """

    def __init__(self, *, max_entries: int = 2048) -> None:
        if isinstance(max_entries, bool) or not isinstance(max_entries, int) or max_entries < 1:
            raise ValueError("max_entries must be a positive integer")
        self.max_entries = max_entries
        self._entries: OrderedDict[tuple[float, bytes], FaceMesh] = OrderedDict()
        self._lock = Lock()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _key(face, tolerance: float) -> tuple[float, bytes]:
        stream = BytesIO()
        BRepTools.Write_s(
            face.wrapped,
            stream,
            False,
            False,
            TopTools_FormatVersion.TopTools_FormatVersion_VERSION_3,
        )
        return float(tolerance), stream.getvalue()

    def get(self, key: tuple[float, bytes]) -> FaceMesh | None:
        with self._lock:
            mesh = self._entries.get(key)
            if mesh is None:
                self.misses += 1
            else:
                self.hits += 1
                self._entries.move_to_end(key)
            return mesh

    def put(self, key: tuple[float, bytes], mesh: FaceMesh) -> None:
        with self._lock:
            self._entries[key] = mesh
            self._entries.move_to_end(key)
            if len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        """Release retained face geometry and reset counters."""

        with self._lock:
            self._entries.clear()
            self.hits = self.misses = 0


_ACTIVE_MESH_CACHE: ContextVar[FixedInkMeshCache | None] = ContextVar(
    "draftwright_fixed_ink_mesh_cache", default=None
)


@contextmanager
def _using_fixed_ink_mesh_cache(cache: FixedInkMeshCache | None):
    token = _ACTIVE_MESH_CACHE.set(cache)
    try:
        yield
    finally:
        _ACTIVE_MESH_CACHE.reset(token)


def _active_fixed_ink_mesh_cache() -> FixedInkMeshCache | None:
    return _ACTIVE_MESH_CACHE.get()
