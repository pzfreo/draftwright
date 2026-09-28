"""Small value operations shared by record contracts and correspondence checks."""

from math import fsum, isfinite


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


def dot_fsum(first, second):
    """Dot product with the existing strict-length, compensated summation rule."""
    return fsum(a * b for a, b in zip(first, second, strict=True))


def cross3(first, second):
    """Three-component cross product used by profile and frame checks."""
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )
