"""Validated, build-scoped inputs shared by the drawing front doors (#1927).

The public call signatures stay explicit. This rank-1 value object owns their
defaults and the policy checks that can run without a part or a drawing.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from draftwright._core import _dimension_draft
from draftwright.annotation_layout_profile import annotation_layout_policy
from draftwright.leader_policy import leader_region_policy
from draftwright.view_plan import validate_projection

if TYPE_CHECKING:
    from draftwright.model import Feature, PartModel


@dataclass(frozen=True)
class BuildOptions:
    """Options carried unchanged across analysis, placement and retry builds."""

    out: str | None = None
    title: str | None = field(default=None, metadata={"script_order": 0, "script_always": True})
    number: str = field(default="DWG-001", metadata={"script_order": 1, "script_always": True})
    tolerance: str | None = field(default=None, metadata={"script_order": 11})
    drawn_by: str = field(default="", metadata={"script_order": 10, "script_truthy": True})
    scale: float | None = field(default=None, metadata={"script_order": 12})
    page: str | tuple | None = field(default=None, metadata={"script_order": 14})
    auto_dims: bool = True
    detail_view: bool = True
    pmi: Literal["off", "report", "annotate"] | None = field(
        default=None, metadata={"script_order": 17}
    )
    repair: bool = True
    assembly: bool | None = None
    model: Sequence[Feature] | PartModel | None = None
    decorations: dict | None = None
    requested: tuple | None = None
    authored: tuple | None = None
    trace: str | Path | bool | None = None
    material: str = field(default="", metadata={"script_order": 18, "script_truthy": True})
    date: str = field(default="", metadata={"script_order": 19, "script_truthy": True})
    revision: str = field(default="A", metadata={"script_order": 20})
    company: str = field(default="", metadata={"script_order": 21, "script_truthy": True})
    frame: bool = field(default=False, metadata={"script_order": 26, "script_truthy": True})
    projection: str | None = field(
        default=None, metadata={"script_order": 28, "script_truthy": True}
    )
    zones: bool = field(default=False, metadata={"script_order": 27, "script_truthy": True})
    scale_policy: Literal["strict", "fallback", "permissive"] = field(
        default="fallback", metadata={"script_order": 13}
    )
    reproducible: bool = True
    framed_recognition: bool = False
    text_position: str = field(default="inline", metadata={"script_order": 30})
    text_orientation: str = field(default="aligned", metadata={"script_order": 31})
    _required_tables: tuple = ()
    _views: tuple[str, ...] | None = None
    _include_iso: bool = True
    _view_constraints: object = None
    _document_input: object = None
    projection_symbol: bool = field(default=True, metadata={"script_order": 29})
    source: str | Path | None = field(default=None, metadata={"script_order": 16})
    approved_by: str = field(default="", metadata={"script_order": 22, "script_truthy": True})
    document_type: str = field(default="", metadata={"script_order": 23, "script_truthy": True})
    sheet: str = field(default="", metadata={"script_order": 24, "script_truthy": True})
    margin_left: float | None = field(default=None, metadata={"script_order": 2})
    margin_right: float | None = field(default=None, metadata={"script_order": 3})
    margin_top: float | None = field(default=None, metadata={"script_order": 4})
    margin_bottom: float | None = field(default=None, metadata={"script_order": 5})
    title_block_width: float | None = field(default=None, metadata={"script_order": 6})
    leader_region: Literal["auto", "interior", "exterior"] = field(
        default="auto", metadata={"script_order": 32}
    )
    annotation_layout: Literal[
        "estimated-strips", "demand-guided", "compare", "baseline", "candidate-preview", "best"
    ] = field(default="demand-guided", metadata={"script_order": 33, "script_always": True})
    _replayed_scale: float | None = None

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> BuildOptions:
        """Capture matching public arguments without a second forwarding roster."""
        selected = {item.name: values[item.name] for item in fields(cls) if item.name in values}
        return cls(**cast("dict[str, Any]", selected))

    def script_constructor_args(self, special: Mapping[str, Sequence[str]]) -> list[str]:
        """Serialize Sheet constructor options in the established source order.

        The emitter supplies only the values that depend on its replay context. Every
        ordinary value and default is read from this dataclass.
        """
        defaults = type(self)()
        result: list[str] = []
        script_fields = sorted(
            (item for item in fields(self) if "script_order" in item.metadata),
            key=lambda item: item.metadata["script_order"],
        )
        unknown = set(special) - {item.name for item in script_fields}
        if unknown:
            raise ValueError(f"unclassified script option overrides: {sorted(unknown)}")
        for item in script_fields:
            if item.name in special:
                result.extend(special[item.name])
                continue
            value = getattr(self, item.name)
            if item.metadata.get("script_always") or (
                bool(value)
                if item.metadata.get("script_truthy")
                else value != getattr(defaults, item.name)
            ):
                result.append(f"{item.name}={value!r}")
        return result

    def script_front_door_kwargs(self) -> dict[str, Any]:
        """The shared options accepted by both script generation and emission.

        ``source`` is supplied as ``pmi_source`` only after the generator seals the
        STEP snapshot; it is deliberately absent from its public signature.
        """
        return {
            item.name: getattr(self, item.name)
            for item in fields(self)
            if "script_order" in item.metadata and item.name != "source"
        }

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "annotation_layout", annotation_layout_policy(self.annotation_layout)
        )
        validate_projection(self.projection, projection_symbol=self.projection_symbol)
        _dimension_draft(self.text_position, self.text_orientation)
        object.__setattr__(self, "leader_region", leader_region_policy(self.leader_region).value)
        if self._replayed_scale is not None:
            replayed = float(self._replayed_scale)
            if not math.isfinite(replayed) or replayed <= 0:
                raise ValueError(f"_replayed_scale must be finite and positive, got {replayed!r}")
            if self.scale is not None:
                raise ValueError("_replayed_scale cannot be combined with authored scale=")
            if self.page is None:
                raise ValueError("_replayed_scale requires the settled page")
            object.__setattr__(self, "_replayed_scale", replayed)
        if self.scale_policy not in {"strict", "fallback", "permissive"}:
            raise ValueError(
                "scale_policy must be 'strict', 'fallback', or 'permissive', "
                f"got {self.scale_policy!r}"
            )
