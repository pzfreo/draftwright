"""Ownership decisions over one acquired recognition inventory.

This stage compares one provider aggregate with caller-supplied radius inventories.
The recognition lifecycle remains in ``detect.build_part_model``.
"""

from __future__ import annotations

from collections import Counter
from math import isfinite

from quiddity import CircularBlindStep, Fillet

from draftwright.model.declare import circular_blind_step


def _fillet_ownership_key(record) -> tuple:
    """Strict primitive-only key for aggregate Fillet/Blend partition comparisons."""
    if type(record) is not Fillet:
        raise TypeError("fillet inventory members must be exact Fillet records")
    if type(record.axis) is not str or record.axis not in ("x", "y", "z"):
        raise ValueError("fillet axis must be exactly 'x', 'y', or 'z'")
    if type(record.turned) is not bool:
        raise ValueError("fillet turned must be an exact bool")
    if type(record.radius) not in (int, float):
        raise ValueError("fillet radius must be an exact non-boolean int or float")
    if type(record.at) is not tuple or len(record.at) != 3:
        raise ValueError("fillet at must be an immutable 3-vector")
    if any(type(component) not in (int, float) for component in record.at):
        raise ValueError("fillet at components must be exact non-boolean ints or floats")
    try:
        radius = float(record.radius)
        at = tuple(float(component) for component in record.at)
    except (OverflowError, ValueError) as exc:
        raise ValueError("fillet radius and at must be finite") from exc
    if radius <= 0.0 or not isfinite(radius) or not all(isfinite(component) for component in at):
        raise ValueError("fillet radius and at must be finite, with a positive radius")
    return record.axis, radius, at, record.turned


def _fillet_blend_ownership_keys(fillets, blends) -> tuple[tuple, tuple]:
    """Validate both public inventories before comparing only built-in primitive values."""
    from draftwright.blend_contract import blend_provider_key

    return (
        tuple(_fillet_ownership_key(record) for record in fillets),
        tuple(blend_provider_key(record) for record in blends),
    )


def _preserves_ownership_with_unique_additions(
    supplied_keys: tuple, aggregate_keys: tuple
) -> bool:
    """Keep every aggregate occurrence without cloning an existing or added owner."""
    supplied_counts = Counter(supplied_keys)
    aggregate_counts = Counter(aggregate_keys)
    if not (aggregate_counts <= supplied_counts):
        return False
    additions = supplied_counts - aggregate_counts
    return all(count == 1 and key not in aggregate_counts for key, count in additions.items())


def _same_ownership_occurrences(supplied_keys: tuple, aggregate_keys: tuple) -> bool:
    """Compare order-independent occurrence inventories while retaining multiplicity."""
    return Counter(supplied_keys) == Counter(aggregate_keys)


def _same_fillet_blend_partition(
    supplied: tuple[tuple, tuple], aggregate: tuple[tuple, tuple]
) -> bool:
    """Compare both aggregate sibling inventories as occurrence multisets."""
    return all(
        _same_ownership_occurrences(supplied_family, aggregate_family)
        for supplied_family, aggregate_family in zip(supplied, aggregate, strict=True)
    )


def _circular_blind_step_ownership_key(record) -> tuple:
    """Strict canonical key for circular-step ownership and multiplicity comparisons."""
    if type(record) is not CircularBlindStep:
        raise TypeError(
            "circular_blind_steps inventory members must be exact CircularBlindStep records"
        )
    feature = circular_blind_step(
        axis=record.axis,
        radius=record.radius,
        length=record.length,
        centreline=record.centreline,
        section=record.section,
    )
    return (
        feature.axis,
        feature.radius,
        feature.length,
        feature.centreline,
        feature.section,
    )


def _validate_aggregate_radius_ownership(
    recognition,
    *,
    fillets,
    blends,
    circular_blind_steps,
    fillets_supplied: bool,
    blends_supplied: bool,
    circular_blind_steps_supplied: bool,
) -> None:
    """Refuse partial overrides that would change the aggregate radius partition."""
    # Fillet and Blend are two projections of the same rounded-chain geometry. The
    # aggregate owns their exact defining-face precedence, so a divergent one-sided
    # override can double-own or erase a radius requirement. A one-sided value must
    # preserve every aggregate-owned occurrence; additions remain available for legacy
    # explicit injection only when the sibling aggregate is empty. A fully supplied pair
    # is accepted only through the detected path's source-part-bound aggregate handoff.
    if fillets_supplied and not blends_supplied:
        supplied_keys = _fillet_blend_ownership_keys(fillets, ())[0]
        aggregate_keys = _fillet_blend_ownership_keys(recognition.fillets, ())[0]
        ownership_changed = (
            not _same_ownership_occurrences(supplied_keys, aggregate_keys)
            if recognition.blends
            else not _preserves_ownership_with_unique_additions(supplied_keys, aggregate_keys)
        )
        if ownership_changed:
            raise ValueError("fillets and blends must preserve aggregate ownership exactly")
    elif blends_supplied and not fillets_supplied:
        supplied_keys = _fillet_blend_ownership_keys((), blends)[1]
        aggregate_keys = _fillet_blend_ownership_keys((), recognition.blends)[1]
        ownership_changed = (
            not _same_ownership_occurrences(supplied_keys, aggregate_keys)
            if recognition.fillets
            else not _preserves_ownership_with_unique_additions(supplied_keys, aggregate_keys)
        )
        if ownership_changed:
            raise ValueError("fillets and blends must preserve aggregate ownership exactly")
    elif fillets_supplied and blends_supplied:
        if not _same_fillet_blend_partition(
            _fillet_blend_ownership_keys(fillets, blends),
            _fillet_blend_ownership_keys(recognition.fillets, recognition.blends),
        ):
            raise ValueError("fillets and blends must preserve aggregate ownership exactly")
    # A circular blind step and its legacy fillet projection compete for the same
    # curved wall.  The aggregate resolves that ownership atomically.  A partial caller
    # may still supply either inventory when it agrees with the aggregate (or when no
    # competing aggregate owner exists), but a divergent one-sided override is ambiguous:
    # accepting it could emit two radius requirements or silently emit neither. The two
    # public record families are independently quantised and carry no shared provider
    # owner identity, so even a paired divergent override cannot be reconciled safely.
    # Preserve the aggregate partition exactly whenever either family owns geometry.
    aggregate_fillet_keys = _fillet_blend_ownership_keys(recognition.fillets, ())[0]
    aggregate_circular_keys = tuple(
        _circular_blind_step_ownership_key(record) for record in recognition.circular_blind_steps
    )
    if fillets_supplied and not circular_blind_steps_supplied:
        supplied_fillet_keys = _fillet_blend_ownership_keys(fillets, ())[0]
        if recognition.circular_blind_steps and not _same_ownership_occurrences(
            supplied_fillet_keys, aggregate_fillet_keys
        ):
            raise ValueError(
                "fillets and circular_blind_steps must be supplied together when "
                "overriding aggregate ownership"
            )
    elif circular_blind_steps_supplied and not fillets_supplied:
        supplied_circular_keys = tuple(
            _circular_blind_step_ownership_key(record) for record in circular_blind_steps
        )
        if (
            recognition.circular_blind_steps or recognition.fillets
        ) and not _same_ownership_occurrences(supplied_circular_keys, aggregate_circular_keys):
            raise ValueError(
                "fillets and circular_blind_steps must be supplied together when "
                "overriding aggregate ownership"
            )
        if not recognition.circular_blind_steps and not _preserves_ownership_with_unique_additions(
            supplied_circular_keys, aggregate_circular_keys
        ):
            raise ValueError("circular_blind_steps must not duplicate an ownership occurrence")
    elif fillets_supplied and circular_blind_steps_supplied:
        supplied_fillet_keys = _fillet_blend_ownership_keys(fillets, ())[0]
        supplied_circular_keys = tuple(
            _circular_blind_step_ownership_key(record) for record in circular_blind_steps
        )
        if recognition.circular_blind_steps or recognition.fillets:
            if not _same_ownership_occurrences(
                supplied_fillet_keys, aggregate_fillet_keys
            ) or not _same_ownership_occurrences(supplied_circular_keys, aggregate_circular_keys):
                raise ValueError(
                    "fillets and circular_blind_steps must preserve aggregate ownership "
                    "exactly; divergent paired overrides require provider owner identity"
                )
        elif fillets and circular_blind_steps:
            raise ValueError(
                "nonempty fillets and circular_blind_steps cannot be supplied together "
                "without provider owner identity"
            )
        elif not _preserves_ownership_with_unique_additions(
            supplied_circular_keys, aggregate_circular_keys
        ):
            raise ValueError("circular_blind_steps must not duplicate an ownership occurrence")
