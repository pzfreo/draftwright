"""Compatibility import for the supported make_drawing one-liner."""
from ._drawing import make_drawing
from ._legacy import AttributeMigrationError, message, removed

__all__ = ["make_drawing"]
# Let importlib resolve removed from-import names through our migration finder.
__path__ = []


def __getattr__(name):
    if removed(name):
        raise AttributeMigrationError(message(f"{__name__}.{name}"))
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
