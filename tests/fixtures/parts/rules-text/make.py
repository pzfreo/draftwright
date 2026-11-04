"""Python #958 cross-solid and ARCH0009-3 central-bore regression shapes.

Regenerate with draftwright's venv. Fixtures are below parts/ so the opt-in byte guard's
parts/*.step roster does not change. No drawing or recognition expectations are recorded.
"""
from pathlib import Path
from build123d import (Align, Box, BuildPart, Compound, Cylinder, Hole, Pos, Rotation,
                      export_step)

out = Path(__file__).parent
# Python tests/test_issue_958_cross_solid_recognition.py.
base = Box(80, 60, 10)
detached = Pos(0, 0, 10) * Box(30, 20, 4)
wall = Pos(0, -10.5, 6.5) * Box(30, 1, 3)
slotted = Box(60, 30, 10) - Box(30, 8, 20)
padded = base + Pos(0, 0, 7) * Box(30, 20, 4)
pocketed = Box(80, 60, 20) - Pos(0, 0, 6) * Box(30, 20, 8)
channel = (Box(50, 50, 12) + Pos(0, -18.75, 15) * Box(50, 12.5, 18)
           + Pos(0, 18.75, 15) * Box(50, 12.5, 18))
cases = {
    "detached": Compound(children=[base, detached]),
    "three-body-pocket": Compound(children=[base, detached, wall]),
    "three-body-channel": Compound(children=[Box(30, 60, 10), detached, wall]),
    "two-slots": Compound(children=[slotted, Pos(0, 60, 0) * slotted]),
    "two-pads": Compound(children=[padded, Pos(120, 0, 0) * padded]),
    "two-pockets": Compound(children=[pocketed, Pos(120, 0, 0) * pocketed]),
    "two-channels-reversed": Compound(children=[Pos(0, 0, 60) * channel, channel]),
}
# ARCH0009-3: Python tests/test_layout_generalisation.py plus two block proportions.
with BuildPart() as drilled:
    Cylinder(6, 20)
    Hole(0.8, depth=8)
b = Align.MIN
cases.update({
    "central-prismatic": Box(80, 60, 20) - Cylinder(4, 20),
    "central-prismatic-wide": Box(100, 70, 15) - Cylinder(6, 15),
    "central-prismatic-square": Box(60, 60, 10) - Cylinder(3, 10),
    # This Hole starts inside the centred cylinder: retained as a closed-cavity
    # negative control. tools/make_review_fixtures.py writes the true opening used
    # by the axis-ink placement pin (synthetic-coaxial-cylinder-x.step).
    "coaxial-cylinder-x": Rotation(0, 90, 0) * drilled.part,
    "coaxial-step-x": Rotation(0, 90, 0) * (
        Cylinder(6, 12, align=(Align.CENTER, Align.CENTER, b))
        + Pos(0, 0, 12) * Cylinder(4, 12, align=(Align.CENTER, Align.CENTER, b))
        - Cylinder(0.8, 8, align=(Align.CENTER, Align.CENTER, b))
    ),
})
# two-channels.step is not exported here: build123d wrote this Compound([channel,
# Pos(0, 0, 60) * channel]) with one occurrence of the block, so the file held one solid. The
# committed file is quiddity-rust's tests/fixtures/two_channels_ordered.step: the reversed file
# below with its two occurrences' placements swapped (quiddity-rust #73).
for name, part in cases.items():
    export_step(part, out / f"{name}.step")
