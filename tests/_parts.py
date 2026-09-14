"""Named substrates for the most-copied test geometry (#1637 step 4).

At this branch's base commit (b9ac7e0) the suite spelled the `Box` constructor 2,091 times
across `tests/*.py` with 534 distinct argument strings, but the head of that distribution was
tiny: the fifteen strings below carried 642 of those calls, and the next one below the cut
(`100, 60, 20`) carried 21. Every one is a plain rectangular block whose only job is to be
*something to draw* — the subject of such a test is the pass under critique, not the prism,
and the prism's numbers were chosen once and copied ever after.

That census, and the descending order of the members below, came from::

    import collections, re, subprocess
    rev = "b9ac7e0"
    pat = re.compile(r"\\bBox\\(([^()]*)\\)")
    counts = collections.Counter()
    for f in subprocess.run(["git", "ls-tree", "--name-only", rev + ":tests"],
                            capture_output=True, text=True).stdout.split():
        if f.endswith(".py"):
            src = subprocess.run(["git", "show", f"{rev}:tests/{f}"],
                                 capture_output=True, text=True).stdout
            counts.update(m.group(1) for m in pat.finditer(src))
    top = [a for a, n in counts.most_common() if n >= 23]
    print(len(counts), sum(counts.values()), len(top), sum(counts[a] for a in top))
    print(counts.most_common()[len(top)])

It is the snapshot that picked the members, not an invariant, and re-running it on a later
commit will not reproduce those numbers: the migration this module exists to enable removes
the very literals it counted. Re-derive it against whatever commit you are arguing about
rather than trusting the figures above.

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

# (length, width, height), in descending order of the census above. The cut was 23 calls.
_BOX_SUBSTRATES: tuple[tuple[int, int, int], ...] = (
    (60, 40, 20),
    (80, 60, 20),
    (30, 20, 10),
    (80, 50, 8),
    (40, 30, 20),
    (10, 10, 10),
    (20, 20, 20),
    (90, 60, 20),
    (80, 60, 30),
    (40, 40, 10),
    (40, 20, 10),
    (80, 20, 15),
    (30, 8, 20),
    (60, 40, 30),
    (40, 30, 8),
)

PART_RECIPES: Mapping[str, Callable[[], object]] = MappingProxyType(
    {
        f"box_{length}x{width}x{height}": partial(Box, length, width, height)
        for length, width, height in _BOX_SUBSTRATES
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
