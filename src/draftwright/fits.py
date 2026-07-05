"""fits — ISO 286 fit-class → limit-deviation lookup (ADR 0011 P2a.2, #29).

A fit code like ``H7`` / ``h6`` / ``g6`` names a *tolerance class*, not a number: the
actual ± deviation depends on the nominal diameter's ISO 286 size band. This module is
the one genuine (c)-gap of Phase 2 — helpers renders tolerances but has no fit-code
semantics — so draftwright owns the small standard table that turns ``(code, ⌀) →
(lower, upper)`` signed deviations (mm), feeding P2a's tolerance path.

Coverage is deliberately the **common** machining classes over nominal ⌀ ≤ 250 mm:
holes ``H``/``G``/``F`` (the EI = −es mirror rule) and shafts
``h``/``g``/``f``/``js``/``k``/``n``/``p``. Anything outside the table fails loudly with a
``ValueError`` pointing at an explicit ``.tolerance(lo, hi)`` — never a silent wrong
number (matching the declaration-constructor discipline, #452). The transition/interference
*hole* classes (``K``/``N``/``P`` — the ISO delta rule) are intentionally not modelled.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ISO 286 size bands: upper bound (mm) of each step "over prev, up to and including this".
_BANDS = (3, 6, 10, 18, 30, 50, 80, 120, 180, 250)

# Standard tolerance grade IT values (µm), index-aligned to _BANDS (ISO 286-1 Table 1).
_IT: dict[int, tuple[int, ...]] = {
    5: (4, 5, 6, 8, 9, 11, 13, 15, 18, 20),
    6: (6, 8, 9, 11, 13, 16, 19, 22, 25, 29),
    7: (10, 12, 15, 18, 21, 25, 30, 35, 40, 46),
    8: (14, 18, 22, 27, 33, 39, 46, 54, 63, 72),
    9: (25, 30, 36, 43, 52, 62, 74, 87, 100, 115),
    10: (40, 48, 58, 70, 84, 100, 120, 140, 160, 185),
    11: (60, 75, 90, 110, 130, 160, 190, 220, 250, 290),
}

# Shaft fundamental UPPER deviation es (µm) for the clearance letters (≤ 0), index-aligned.
_ES: dict[str, tuple[int, ...]] = {
    "h": (0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    "g": (-2, -4, -5, -6, -7, -9, -10, -12, -14, -15),
    "f": (-6, -10, -13, -16, -20, -25, -30, -36, -43, -50),
}

# Shaft fundamental LOWER deviation ei (µm) for the interference letters (≥ 0), index-aligned.
_EI: dict[str, tuple[int, ...]] = {
    "k": (0, 1, 1, 1, 2, 2, 2, 3, 3, 4),
    "n": (4, 8, 10, 12, 15, 17, 20, 23, 27, 31),
    "p": (6, 12, 15, 18, 22, 26, 32, 37, 43, 50),
}

_CODE_RE = re.compile(r"^([A-Za-z]+)(\d+)$")


def _fmt_dev(v: float) -> str:
    """A single limit deviation for a callout label: signed, trailing-zero trimmed to the
    µm digit (3 dp), with the half-µm (``js``) case kept at 4 dp; an exact ``0`` renders ``0``
    (ISO convention). Not the sheet's ``decimal_precision`` — a fit deviation must show its
    real precision (``±0.05`` must not round to ``±0.1``)."""
    if abs(v) < 5e-5:
        return "0"
    s = f"{v:+.4f}"
    return s[:-1] if s.endswith("0") else s  # 4 dp -> 3 dp when the µm digit is whole


@dataclass(frozen=True)
class FitClass:
    """A resolved fit sitting in ``DimParameter.tolerance`` as an aspect marker (P2a.2). It
    renders through the owned ``_core._tol_suffix`` like any other diameter tolerance, so it
    reuses all of P2a's threading — either as the class code (``H7``, default) or the signed
    limit deviations (``+0.021/0``)."""

    code: str
    lower: float
    upper: float
    show: str = "class"  # "class" -> " H7"; "deviation" -> " +0.021/0"

    def suffix(self) -> str:
        if self.show == "deviation":
            return f" {_fmt_dev(self.upper)}/{_fmt_dev(self.lower)}"
        return f" {self.code}"


def fit_class(code: str, nominal: float, show: str = "class") -> FitClass:
    """Resolve *code* at nominal ⌀ into a :class:`FitClass` (raising for a class/size outside
    the table, so a fit fails at declaration). ``show`` selects the label form."""
    if show not in ("class", "deviation"):
        raise ValueError(f"fit show= must be 'class' or 'deviation' (got {show!r})")
    lower, upper = fit_deviation(code, nominal)
    return FitClass(code=code, lower=lower, upper=upper, show=show)


def parse_fit(code: str) -> tuple[str, int]:
    """Split a fit code into (letters, IT grade): ``"H7" → ("H", 7)``, ``"js6" → ("js", 6)``.
    Case is significant — an UPPER-case letter is a hole class, lower-case a shaft class."""
    m = _CODE_RE.match(str(code).strip())
    if m is None:
        raise ValueError(f"fit code must be letters + a grade number (got {code!r})")
    return m.group(1), int(m.group(2))


def _band_index(nominal: float) -> int:
    if not (isinstance(nominal, (int, float)) and not isinstance(nominal, bool) and nominal > 0):
        raise ValueError(f"nominal diameter must be a positive number (got {nominal!r})")
    for i, upper in enumerate(_BANDS):
        if nominal <= upper:
            return i
    raise ValueError(
        f"nominal ⌀{nominal} mm is outside the built-in ISO 286 table (≤ {_BANDS[-1]} mm) — "
        "supply an explicit .tolerance(lo, hi)"
    )


def fit_deviation(code: str, nominal: float) -> tuple[float, float]:
    """The signed ``(lower, upper)`` limit deviations (mm) for fit *code* at nominal ⌀.

    ``fit_deviation("H7", 20) == (0.0, 0.021)``; ``fit_deviation("g6", 20) ==
    (-0.020, -0.007)``. Raises ``ValueError`` for a class or size outside the built-in
    table so a fit is never silently wrong — the caller should fall back to an explicit
    ``.tolerance(lo, hi)``."""
    letters, grade = parse_fit(code)
    if grade not in _IT:
        raise ValueError(f"fit {code!r}: IT grade {grade} outside the built-in table (5–11)")
    i = _band_index(nominal)
    it = _IT[grade][i]
    letter = letters.lower()
    is_hole = letters[0].isupper()

    lo: float
    hi: float
    if is_hole:
        # Holes via the EI = −es(shaft) mirror rule (valid for A–H, i.e. H/G/F here).
        if letter not in _ES:
            raise ValueError(
                f"hole fit class {letters!r} is not in the built-in table "
                "(supported: H, G, F) — use an explicit .tolerance(lo, hi)"
            )
        ei = -_ES[letter][i]
        lo, hi = ei, ei + it
    elif letter == "js":
        lo, hi = -it / 2, it / 2
    elif letter in _ES:  # clearance shafts h/g/f: es is the upper deviation, ei = es − IT
        es = _ES[letter][i]
        lo, hi = es - it, es
    elif letter in _EI:  # interference shafts k/n/p: ei is the lower deviation, es = ei + IT
        ei = _EI[letter][i]
        lo, hi = ei, ei + it
    else:
        raise ValueError(
            f"shaft fit class {letters!r} is not in the built-in table "
            "(supported: h, g, f, js, k, n, p) — use an explicit .tolerance(lo, hi)"
        )
    return (lo / 1000.0, hi / 1000.0)
