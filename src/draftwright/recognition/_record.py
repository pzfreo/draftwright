"""The recognition-record base mixin (ADR 0013).

Every feature-recognition record is a frozen, geometry-only dataclass — no
build123d / OCP object leaks out (records are the future ``b123d-recognisers``
surface, ADR 0013 Phase 2). This mixin gives them the one uniform serialization
accessor the contract promises: :meth:`to_dict`, a plain nested dict of
primitives (``dataclasses.asdict`` recurses into nested records and hole tuples).

Leaf of the recognition DAG — imports only the stdlib.
"""

from __future__ import annotations

import dataclasses


class Record:
    """Mixin: a uniform ``.to_dict()`` for every recognition record (ADR 0013).

    The record must be a dataclass; ``to_dict`` returns a nested dict of pure
    primitives, so a leaked build123d type surfaces as a non-serializable value
    the contract test catches.
    """

    def to_dict(self) -> dict:
        # `self` is always a dataclass instance in practice (the mixin is only used
        # on records); mypy can't see that through the plain-class base.
        return dataclasses.asdict(self)  # type: ignore[call-overload,no-any-return]
