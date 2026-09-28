"""Small value operations shared by record contracts and correspondence checks."""

from math import isfinite


def rounded(value) -> float:
    return round(float(value), 3)


def finite_real(value, *, name: str, positive: bool = False) -> float:
    """Validate an exact built-in real at a public record boundary."""
    if type(value) not in (int, float):
        raise ValueError(f"{name} must be a finite real number")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite real number") from exc
    if not isfinite(result) or (positive and result <= 0.0):
        raise ValueError(f"{name} must be a finite real number")
    return result
