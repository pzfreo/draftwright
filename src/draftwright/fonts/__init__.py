"""Vendored, pinned fonts (#149).

Referenced by **file path** so text rendering never resolves a font *name*
through the OS font stack — the same glyph outlines are used on every platform,
which is what makes generated layout deterministic across OS (an "Arial" name
substitutes to a different font on Linux, shifting the whole sheet ~1 mm).

Both are IBM Plex, OFL-1.1 (see the ``LICENSE-*-OFL.txt`` files alongside).
"""

from pathlib import Path

_DIR = Path(__file__).parent

# Dimensions / callouts / notes — a monospace face: digits and tolerance text
# line up cleanly.
PLEX_MONO = str(_DIR / "IBMPlexMono-Regular.ttf")

# Title block — condensed sans fits the tight ISO 7200 cells.
PLEX_SANS_CONDENSED = str(_DIR / "IBMPlexSansCondensed-Regular.ttf")
