"""Token-preserving mutable feature list for the declarative Sheet facade."""

from __future__ import annotations

from collections.abc import Callable, MutableSequence, Sequence


class _FeatureView(MutableSequence):
    """The public ``Sheet.features`` list — a view over ``(token, feature)`` entries.

    Handles address their feature by **token**, not position (#908), and this view is
    what keeps the two in step. Every mutation moves entries, so the token travels with
    the feature it names:

    - ``append`` mints a new token — a declaration;
    - ``features[i] = f`` mints a **new** token: assignment cannot express "move this
      feature here", so inheriting the slot's identity would silently hand every
      reference to whatever was assigned. Stale references fail loudly instead. The
      identity-preserving rebuild a size verb needs is :meth:`_rebind`;
    - ``reverse`` / ``sort`` / ``insert`` reorder entries, so a handle simply finds its
      feature at the new position rather than silently naming a neighbour;
    - ``del`` drops the token, so a handle for a removed feature raises when used.

    Before this, everything addressed by index into a plain list, and seven review rounds
    on #872 found seven ways for a long-lived reference — a tolerance, a GD&T origin, a
    section, a dimension intent — to end up pointing at the wrong feature. Each fix
    detected one route and opened another. Carrying identity makes the class impossible
    instead of detectable.
    """

    __slots__ = ("_entries", "_next_token", "validate_change")

    def __init__(self, entries: list) -> None:
        self._entries = entries
        # A plain int, not `itertools.count`: counters lose pickle/copy support in
        # Python 3.14 and this package supports >=3.11, so a Sheet carrying one would
        # stop being copyable. Per-sheet rather than global because tokens appear in
        # internal keys and this project holds output to be deterministic — a
        # process-wide counter would make those keys depend on how many other sheets
        # happened to be built first.
        self._next_token = 0
        self.validate_change: Callable[[Sequence], None] | None = None

    def _validate(self, values):
        if self.validate_change is not None:
            self.validate_change(values)

    def _mint(self) -> int:
        token = self._next_token
        self._next_token += 1
        return token

    def __getitem__(self, i):
        if isinstance(i, slice):
            return [e[1] for e in self._entries[i]]
        return self._entries[i][1]

    def __setitem__(self, i, value) -> None:
        # Public assignment always mints a NEW token, scalar or slice.
        #
        # This is the distinction an earlier draft got wrong. Keeping the destination
        # slot's token is right for the INTERNAL rebuild a size verb does — `.depth()`
        # replaces the frozen dataclass with an updated copy of the same feature — but on
        # the public view, assignment cannot tell "move this feature here" from "put a
        # different feature here". Preserving identity across it silently transferred
        # every reference (handles, tolerances, GD&T origins, sections, intents) onto
        # whatever was assigned, which is the exact bug this class exists to remove:
        #
        #     features[0], features[1] = features[1], features[0]   # a tuple swap
        #     features[:] = features[::-1]                          # a slice reversal
        #
        # both of which move VALUES between slots. Minting fresh tokens makes those
        # references fail loudly instead. To reorder while keeping identity, use
        # `reverse()` / `sort()`, which move whole entries. Internal rebuilding goes
        # through `_rebind`, which is the only path that preserves a token.
        current = list(self)
        if isinstance(i, slice):
            value = list(value)
        current[i] = value
        self._validate(current)
        if isinstance(i, slice):
            self._entries[i] = [(self._mint(), f) for f in value]
            return
        self._entries[i] = (self._mint(), value)

    def _rebind(self, index: int, feature) -> None:
        """Replace the feature at *index* KEEPING its token — the same feature, rebuilt.

        The only identity-preserving write. Used by the size verbs, whose frozen
        dataclasses are replaced wholesale on every `.depth()` / `.cbore()` / `.thread()`.
        """
        current = list(self)
        current[index] = feature
        self._validate(current)
        self._entries[index] = (self._entries[index][0], feature)

    def __delitem__(self, i) -> None:
        current = list(self)
        del current[i]
        self._validate(current)
        del self._entries[i]

    def clear(self) -> None:
        self._validate([])
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)

    def insert(self, i, value) -> None:
        current = list(self)
        current.insert(i, value)
        self._validate(current)
        self._entries.insert(i, (self._mint(), value))

    def extend(self, values) -> None:
        values = list(values)
        self._validate([*self, *values])
        self._entries.extend((self._mint(), value) for value in values)

    # Reordering must move ENTRIES, not values. `MutableSequence` implements `reverse`
    # in terms of `__setitem__`, which here keeps each slot's token — right for a size
    # verb replacing a feature in place, wrong for a reorder, where it would swap the
    # features and leave every token pointing at its old position. Overriding both is
    # what makes a reorder transparent to handles rather than silently retargeting them.
    def reverse(self) -> None:
        self._entries.reverse()

    def sort(self, *, key=None, reverse: bool = False) -> None:
        self._entries.sort(
            key=(lambda e: e[1]) if key is None else (lambda e: key(e[1])), reverse=reverse
        )

    def __repr__(self) -> str:
        return repr([e[1] for e in self._entries])

    def __eq__(self, other) -> bool:
        return [e[1] for e in self._entries] == list(other)
