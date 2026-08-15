"""A flat's opposite face must be on the same piece of stock (#1015).

``recognise_flats`` sizes each flat by looking for an *opponent* — an antiparallel face across
the same axis. Finding one makes the size flat-to-flat (a double-D's A/F); finding none makes
it flat-to-opposite-OD (a lone D's height). The search required the same infinite axis LINE and
nothing more, so two lone D-flats on separate coaxial regions were each taken for the other's
opposite face and both were sized as the sum of two unrelated chord offsets.

``across`` is a flat's ONLY size parameter, so this is a wrong number on the drawing rather
than a missing one — the part gets made to it.
"""

from b123d_recognisers import recognise_flats
from build123d import Align, Box, Cylinder, Pos

_C = (Align.CENTER, Align.CENTER, Align.CENTER)


def _lone_d(radius: float, offset: float, z: float):
    """Round stock of *radius* with a SINGLE flat at signed *offset* from the axis, centred
    at *z*. The cutter takes the material beyond the flat, on whichever side *offset* names.

    One flat, not two: the bug needs a face whose true size is flat-to-opposite-OD, because a
    double-D already finds its real opponent on its own stock.
    """
    beyond = 40 if offset >= 0 else -40
    cutter = Pos(offset + beyond, 0, 0) * Box(80, 80, 60, align=_C)
    return Pos(0, 0, z) * (Cylinder(radius, 40) - cutter)


def _two_coaxial_lone_ds():
    """Two lone-D regions on ONE axis, joined by a narrow waist, their flats on OPPOSITE sides
    so each looks like the other's opponent.

    ⌀40 with its flat 10 from the axis is 10 + 20 = 30 A/F.
    ⌀24 with its flat 8 from the axis is 8 + 12 = 20 A/F.
    Paired with each other they both read 10 + 8 = 18.
    """
    return _lone_d(20, 10, -30) + Cylinder(5, 60) + _lone_d(12, -8, 60)


def test_lone_flats_on_separate_coaxial_stock_are_not_opposed():
    flats = recognise_flats(_two_coaxial_lone_ds())
    assert len(flats) == 2, "the fixture must produce exactly the two lone flats"
    assert sorted(f.across for f in flats) == [20.0, 30.0]


def test_the_two_faces_of_one_double_d_are_still_opposed():
    """The other direction, and the property the fix most easily breaks: a genuine opposed
    pair shares its stock, so it must still size flat-to-flat rather than falling back to
    flat-to-OD (which would read 12.5 + 20 = 32.5 instead of 25)."""
    part = Cylinder(20, 40) & Box(25, 60, 60, align=_C)
    flats = recognise_flats(part)
    assert len(flats) == 2
    assert {f.across for f in flats} == {25.0}


def test_a_lone_flat_on_a_single_stock_is_unchanged():
    """No opponent anywhere: 10 from the axis of ⌀40 stock is 10 + 20 = 30 A/F. Guards against
    a fix that suppresses opposition altogether."""
    flats = recognise_flats(_lone_d(20, 10, 0))
    assert [f.across for f in flats] == [30.0]
