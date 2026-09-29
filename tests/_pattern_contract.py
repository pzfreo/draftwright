"""Inputs shared by the pocket and slot pattern behavior tests."""

from dataclasses import dataclass

from draftwright.model import pocket, slot


def pocket_member():
    # 7.88 × 13.6 × 19 deep, opening +Z, centred at y=-58.
    return pocket(
        width=7.88,
        length=13.6,
        depth=19.0,
        long_axis="x",
        width_axis="y",
        depth_axis="z",
        lo=-6.8,
        hi=6.8,
        w_center=-58.0,
        at=(0.0, -58.0, 1.0),
    )


def slot_member():
    # Through-Z slot, 8 wide × 20 long, centred at the origin.
    return slot(
        width=8.0,
        length=20.0,
        long_axis="x",
        width_axis="y",
        depth_axis="z",
        lo=-10.0,
        hi=10.0,
        w_center=0.0,
        at=(0.0, 0.0, 0.0),
    )


@dataclass(frozen=True)
class PatternCase:
    kind: str
    part_size: tuple[int, int, int]
    member: object
    count: int
    pitch: float
    callout_prefix: str
    pitch_prefix: str
    label: str


PATTERNS = (
    PatternCase(
        "pocket_pattern",
        (26, 161, 21),
        pocket_member,
        5,
        27.2,
        "m_pocketpat",
        "dim_pocketpat_pitch_",
        "5× 7.9 × 13.6 × 19 DEEP",
    ),
    PatternCase(
        "slot_pattern",
        (60, 161, 21),
        slot_member,
        4,
        30.0,
        "m_slotpat",
        "dim_slotpat_pitch_",
        "4× SLOT 8 × 20",
    ),
)
