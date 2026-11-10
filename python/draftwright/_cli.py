"""Console adapters for the Rust implementations."""
import signal
import sys

from . import _native


def _run(entry):
    # Python normally defers SIGINT until native code returns. Console commands need
    # the OS default instead: terminate even inside a render or a blocking stdin read.
    previous = signal.signal(signal.SIGINT, signal.SIG_DFL)
    try:
        return entry(sys.argv)
    finally:
        signal.signal(signal.SIGINT, previous)


def main():
    return _run(_native.cli)


def mcp():
    return _run(_native.mcp)
