"""Engineering drawings from STEP, using the Rust engine.

``make_drawing`` returns **(svg_path, dxf_path)**, as in 0.4. The other 0.4 Python
classes are unavailable.
"""
from ._native import DraftwrightError, __version__
from ._drawing import make_drawing
from ._part import Part
from ._legacy import install as _install, removed as _removed

__all__ = ["make_drawing", "Part", "DraftwrightError", "__version__"]
_install()


def __getattr__(name):
    if _removed(name):
        from ._legacy import AttributeMigrationError, message
        raise AttributeMigrationError(message(name))
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
