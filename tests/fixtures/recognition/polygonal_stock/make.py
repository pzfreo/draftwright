"""Build the polygonal-stock fixture parts (no corpus part is whole-part hexagonal stock): a
regular hexagon of circumradius 20 extruded 30 mm along z (``plain``), and the same prism turned
rigidly so its axis lies along +x (``x``) and -y (``y``), as draftwright's
``tests/fixtures/evaluation/corpus-polygonal-stock-v1.json`` describes its ``polygonal-stock-x``
and ``-y`` cases. ``roll17.step`` (the plain prism turned 17 degrees about its own axis) and
``translated.step`` (circumradius 23, 37 long, moved by (13, -7, 11)) are copied unchanged from
draftwright's ``tests/fixtures/evaluation/polygonal-stock-*.step`` (CC0-1.0). Run from this
directory with draftwright's venv, then record quiddity's recognition and draftwright's sheet
facts for each part:

    ~/repos/draftwright/.venv/bin/python make.py .
    ~/repos/quiddity-python/.venv/bin/python ../../../../tools/recognise.py plain.step -o plain.json
    gzip -9 -n plain.json                 # and the same for x, y, roll17 and translated
    PYTHONPATH=<draftwright>/src ~/repos/draftwright/.venv/bin/python ../slot_patterns/capture.py \\
        ../../../../tools/capture_sheet_facts.py <draftwright>/src . . plain x y roll17 translated
"""

import sys
from pathlib import Path

from build123d import RegularPolygon, Rot, export_step, extrude

out = Path(sys.argv[1])
prism = extrude(RegularPolygon(20, 6), 30)
export_step(prism, str(out / "plain.step"))
export_step(Rot(0, 90, 0) * prism, str(out / "x.step"))
export_step(Rot(90, 0, 0) * prism, str(out / "y.step"))
