"""The Drawing operations used by annotation passes, without an upward import.

This is a structural type only. Runtime stand-ins can still supply the subset a
particular pass needs; the builder's real Drawing must satisfy the whole port.
The existing ``getattr``/``hasattr`` fallbacks preserve partial test and repair
stand-ins; the real Drawing always has those members. ``solve_trace`` has an
optional value, not optional presence on a real Drawing.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from build123d_drafting.helpers import Draft

    from draftwright.linting.coverage import CoverageState
    from draftwright.model import PartModel
    from draftwright.registry import AnnotationRegistry


class ViewCoordinateMap(Protocol):
    """The typed projection operation used by drawing and annotation passes."""

    def pp(self, x: float, y: float, z: float) -> tuple[float, float]: ...


class DrawingPort(Protocol):
    """The current rank-4 pass vocabulary over a rank-5 Drawing."""

    # Sections commit and roll back views; the corridor transaction restores items.
    views: dict[str, tuple[Any, Any]]
    items: list[Any]
    detail_decisions: list[dict[str, object]]

    scale: float
    page_w: float
    page_h: float
    look_at: tuple[float, float, float]
    dist: float
    draft: Draft

    @property
    def registry(self) -> AnnotationRegistry: ...

    @property
    def coverage(self) -> CoverageState: ...

    @property
    def model_declared(self) -> bool: ...

    @property
    def pmi_mode(self) -> str: ...

    @property
    def general_tolerance_source(self) -> object | None: ...

    @property
    def default_surface_finish_source(self) -> object | None: ...

    def model(self) -> PartModel | None: ...

    def annotations(self) -> dict[str, str]: ...

    def annotations_in_view(self, view: str) -> Iterable[tuple[str, Any]]: ...

    def annotations_of(self, feature: object) -> dict[str, Any]: ...

    def iter_annotations(self) -> Iterator[tuple[str, Any]]: ...

    def get_annotation(self, name: str) -> Any | None: ...

    def view_of(self, name: str) -> str | None: ...

    def at(self, view: str, x: float, y: float, z: float) -> tuple[float, float, float]: ...

    def coords(self, view: str) -> ViewCoordinateMap: ...

    def view_bounds(self, view: str) -> tuple[float, float, float, float] | None: ...

    def _add_view(
        self,
        name: str,
        shape: Any,
        camera: Any,
        up: Any,
        position: Any,
        *,
        look_at: Any = None,
        scaled: bool = False,
        bounds_cache: Any = None,
    ) -> Any: ...

    def _set_view_coordinates(self, view: str, coords: ViewCoordinateMap) -> None: ...

    def add_table(
        self,
        rows: Any,
        *,
        prefer: str = "tr",
        name: str = "table",
        block_cols: Any = None,
        _source_id: str | None = None,
        _source_ids: tuple[str, ...] = (),
        _features: tuple[object, ...] = (),
        _drop_code: str = "table_dropped",
        _drop_severity: Literal["error", "warning", "info"] = "warning",
        _cells: Any = (),
        _left_align_cols: Any = (),
    ) -> Any: ...

    def material_fields(self) -> dict[Any, Any]: ...

    def title_block_for(
        self, key: object, factory: Callable[[], tuple[Any, Any]]
    ) -> tuple[Any, Any]: ...

    def pin(self, name: str) -> Any: ...

    def record_section_decision(
        self, status: str, *, reason: str | None = None, detail: str = ""
    ) -> None: ...

    def remove(self, name: str) -> Any: ...
