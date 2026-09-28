"""Small value operations shared by record contracts and correspondence checks."""


def rounded(value) -> float:
    return round(float(value), 3)
