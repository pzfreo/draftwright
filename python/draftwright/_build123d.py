"""Check the optional CAD dependency only when a shape is used."""
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
import re

from . import _native


@lru_cache(maxsize=1)
def _version_check():
    try:
        found = version("build123d")
    except PackageNotFoundError:
        return "build123d>=0.10 is required; installed build123d version metadata was not found"
    release = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", found)
    numbers = tuple(int(n or 0) for n in release.groups()) if release else ()
    if numbers < (0, 10, 0):
        return f"build123d>=0.10 is required; found build123d {found}"
    return numbers


def check_version():
    # Cache failures as well as successful checks, without retaining exceptions.
    result = _version_check()
    if isinstance(result, str):
        raise _native.DraftwrightError(result)
    return result
