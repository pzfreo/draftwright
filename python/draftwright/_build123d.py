"""Check the optional CAD dependency only when a shape is used."""
from importlib.metadata import version
import re

from . import _native


def check_version():
    found = version("build123d")
    release = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?", found)
    numbers = tuple(int(n or 0) for n in release.groups()) if release else ()
    if numbers < (0, 10, 0):
        raise _native.DraftwrightError(f"build123d>=0.10 is required; found build123d {found}")
    return numbers
