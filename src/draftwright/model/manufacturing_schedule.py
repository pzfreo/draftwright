"""A source-owned alternate presentation for crowded manufacturing PMI.

The choice and its printed text live below both sheet sizing and rendering.  A
short reference on the feature is only valid when the complete schedule has
actually been placed; callers may fall back to full leaders if it does not fit.
"""

from __future__ import annotations

from dataclasses import dataclass

from draftwright.model.ir import KnurlRequirement, PatternFeature, ThreadRequirement


@dataclass(frozen=True)
class ManufacturingScheduleEntry:
    tag: str
    requirement: ThreadRequirement | KnurlRequirement
    owners: tuple[object, ...]

    @property
    def source_ids(self) -> tuple[str, ...]:
        return self.requirement.source_ids


@dataclass(frozen=True)
class ManufacturingSchedule:
    entries: tuple[ManufacturingScheduleEntry, ...]

    @property
    def tags_by_source(self) -> dict[str, str]:
        return {source_id: entry.tag for entry in self.entries for source_id in entry.source_ids}

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(source_id for entry in self.entries for source_id in entry.source_ids)
        )

    @property
    def source_ids_by_tag(self) -> dict[str, tuple[str, ...]]:
        return {entry.tag: entry.source_ids for entry in self.entries}

    @property
    def owners(self) -> tuple[object, ...]:
        owners: list[object] = []
        seen: set[int] = set()
        for entry in self.entries:
            for owner in entry.owners:
                if id(owner) not in seen:
                    owners.append(owner)
                    seen.add(id(owner))
        return tuple(owners)

    @property
    def rows(self) -> tuple[tuple[str, str], ...]:
        rows = [("REF", "MANUFACTURING REQUIREMENT")]
        for entry in self.entries:
            for index, line in enumerate(_wrap_requirement(entry.requirement.callout_text)):
                rows.append((entry.tag if index == 0 else "", line))
        return tuple(rows)


def _wrap_requirement(text: str, *, width: int = 44) -> tuple[str, ...]:
    """Break at words without changing any source term or punctuation."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        if current and len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}" if current else word
    if current:
        lines.append(current)
    return tuple(lines)


def manufacturing_callout_suffix(
    requirement: ThreadRequirement | KnurlRequirement,
    tags_by_source: dict[str, str] | None,
) -> str:
    """One reference rule shared by hole and diameter callout renderers."""
    if tags_by_source:
        for source_id in requirement.source_ids:
            if tag := tags_by_source.get(source_id):
                return f"SEE {tag}"
    return requirement.callout_suffix


def manufacturing_schedule(model, *, include_source_pmi: bool) -> ManufacturingSchedule | None:
    """Choose a schedule only for multiple long imported typed requirements.

    A single short thread keeps its conventional direct callout.  The threshold
    is a typography trigger, not a source-specific rule; full requirements stay
    on the feature if the table cannot be placed.
    """
    if not include_source_pmi:
        return None
    by_source: dict[
        tuple[str, ...], tuple[ThreadRequirement | KnurlRequirement, list[object]]
    ] = {}
    claimed_ids: set[str] = set()
    for feature in model.features:
        owner = feature.member if isinstance(feature, PatternFeature) else feature
        for requirement in (getattr(owner, "thread", None), getattr(owner, "knurl", None)):
            if not isinstance(requirement, ThreadRequirement | KnurlRequirement):
                continue
            if requirement.source != "ap242_pmi":
                continue
            key = requirement.source_ids
            if key not in by_source:
                # A source claimed by two different typed facts is ambiguous.
                # Keep their complete direct labels rather than let a dict
                # silently retarget one reference to the other table row.
                if claimed_ids.intersection(key):
                    return None
                by_source[key] = (requirement, [])
                claimed_ids.update(key)
            elif by_source[key][0] != requirement:
                return None
            if not any(owner is feature for owner in by_source[key][1]):
                by_source[key][1].append(feature)
    requirements = sorted(by_source.items(), key=lambda item: item[0])
    if len(requirements) < 2 or not any(
        len(requirement.callout_suffix) > 52 for _source, (requirement, _owners) in requirements
    ):
        return None
    return ManufacturingSchedule(
        tuple(
            ManufacturingScheduleEntry(f"MFG {index}", requirement, tuple(owners))
            for index, (_source, (requirement, owners)) in enumerate(requirements, 1)
        )
    )
