"""Named substrates for the most-copied test geometry (#1637 step 4).

The suite spells the `Box` constructor 2,091 times across `tests/*.py` with 534 distinct
argument strings, but the head of that distribution is tiny: the fifteen strings below carry
642 of those calls. Every one is a plain rectangular block whose only job is to be *something
to draw* — the subject of such a test is the pass under critique, not the prism, and the
prism's numbers were chosen once and copied ever after.

Naming them does two things. It gives `conftest.shared_drawing`'s cache a hashable key, so
one build of a substrate serves every read-only test in a worker that wants it; and it gives
a substrate one place to change when a recogniser's behaviour on it moves, instead of the
sixty-odd literals a rename would have to chase today.

A recipe takes no arguments and returns a fresh solid. Variation belongs in the *build
options* (`page=`, `auto_dims=`, `scale=`, …), which are part of the cache key — never in the
recipe, so two tests naming one recipe are known to be asking for one substrate.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import partial
from types import MappingProxyType

from build123d import Box

# (length, width, height, constructor calls in tests/*.py at this branch's base commit). The
# cut is 23 calls; the next substrate below it is 100 x 60 x 20 at 21. The counts are the
# snapshot that picked the members, not an invariant — nothing asserts them, and they drop as
# the migration proceeds.
_BOX_SUBSTRATES: tuple[tuple[int, int, int, int], ...] = (
    (60, 40, 20, 90),
    (80, 60, 20, 68),
    (30, 20, 10, 55),
    (80, 50, 8, 51),
    (40, 30, 20, 49),
    (10, 10, 10, 47),
    (20, 20, 20, 41),
    (90, 60, 20, 40),
    (80, 60, 30, 38),
    (40, 40, 10, 32),
    (40, 20, 10, 31),
    (80, 20, 15, 29),
    (30, 8, 20, 25),
    (60, 40, 30, 23),
    (40, 30, 8, 23),
)

PART_RECIPES: Mapping[str, Callable[[], object]] = MappingProxyType(
    {
        f"box_{length}x{width}x{height}": partial(Box, length, width, height)
        for length, width, height, _count in _BOX_SUBSTRATES
    }
)


def part(recipe: str):
    """Build a fresh solid for *recipe*, failing with the known names if it is not one."""
    try:
        builder = PART_RECIPES[recipe]
    except KeyError:
        raise KeyError(
            f"unknown part recipe {recipe!r}; tests/_parts.py holds "
            f"{', '.join(sorted(PART_RECIPES))}. Add one only when its substrate is copied "
            "widely enough to be worth a name — a one-off block belongs in its own test."
        ) from None
    return builder()
