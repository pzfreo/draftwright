# Declared external spur gear requirements

Draftwright's first gear surface supports one deliberately narrow class: a single metric,
external, spur, cylindrical involute gear. It does not infer that class from a toothed-looking
boundary. The author declares the manufacturing requirements; independent B-rep evidence may
only confirm correspondence facts such as axis and repeat count.

## Standards basis

| Standard | Role in the declaration |
| --- | --- |
| [ISO 21771-1:2024](https://www.iso.org/standard/84949.html) | Cylindrical involute gear concepts and geometry |
| [ISO 53:1998](https://www.iso.org/standard/22643.html) | Standard basic rack tooth profile |
| [ISO 54:1996](https://www.iso.org/standard/22644.html) | Metric normal-module vocabulary |
| [ISO 21771-2:2025](https://www.iso.org/standard/78378.html) | Tooth-thickness calculation and measurement vocabulary |
| [ISO 1328-1:2013](https://www.iso.org/standard/45309.html) | Individual tooth-flank tolerance classification |
| [ISO 2203:1973](https://www.iso.org/standard/7006.html) | Reviewed representation standard; conventional simplification is not yet claimed |

ISO 21771-2 explicitly does not select the desired tooth thickness or its tolerance. Draftwright
therefore requires both as authored values. It does not derive them from module, pressure angle,
profile shift, or a mating-gear assumption.

## Minimum complete set

`Sheet.external_spur_gear(...)` requires every item below; there are no optional normative
fields:

- target centre and axis;
- integer tooth count `z` from 5 to 1,000;
- metric module `m` from 0.5 mm to 70 mm;
- pressure angle;
- profile-shift coefficient `x`, including an explicit zero;
- face width `b` from 4 mm to 1,200 mm;
- tooth thickness at the reference cylinder and its signed lower/upper deviations;
- ISO 1328-1 flank tolerance class 1–11.

The class and five requirement-standard editions are fixed by the typed feature, rather than repeated as
free-form strings. Missing values, non-finite/non-positive sizes, invalid angles, inverted tooth-
thickness limits, and out-of-range tooth/flank classes raise at the IR boundary. For this spur
class, `m × z` is the reference diameter; it must also remain within ISO 1328-1's published
5 mm to 15,000 mm applicability range.

```python
sheet.external_spur_gear(
    at=(0, 0, 5),
    axis="z",
    tooth_count=13,
    module=1.25,
    pressure_angle=20,
    profile_shift=0,
    face_width=10,
    tooth_thickness=1.9634954084936207,
    tooth_thickness_tolerance=(-0.03, 0.01),
    flank_tolerance_class=7,
)
```

The resulting gear-data table is late furniture placed through `Drawing.add_table()`'s shared
free-space solve. The declaration exposes no raw page coordinate. Generated Sheet scripts use a
lossless numeric spelling for these authored values and reproduce the same frozen IR record.
Draftwright continues to project the source B-rep exactly; it does not yet replace that geometry
with ISO 2203's conventional simplified representation, and the table makes no such claim.

## Fail-closed correspondence

The data table proves what the author required; it does not prove which boundary is the gear.
Completeness lint therefore distinguishes:

- repeating-profile evidence with no declaration (`gear_semantics_missing`);
- no unique centre/span correspondence (`gear_correspondence_unverifiable`);
- a declared/evidenced axis disagreement (`gear_axis_mismatch`);
- a tooth/repeat-count disagreement (`gear_repeat_count_mismatch`);
- missing, ambiguous, or stale table output (`gear_requirement_*`).

Issue #1087 supplies the production full-wire repeating-profile evidence. Until that producer is
present, a declared gear stays explicitly unverifiable under physical lint, and cyclic-looking
geometry retains `unrecognised_defining_geometry`.

## Out of scope

Internal gears, helical gears, racks, gear pairs, mesh/backlash design, materials, heat treatment,
load capacity, process selection, and automatic semantic inference are unsupported. Supporting one
of those requires its own complete typed class and standards evidence; it must not be represented by
adding optional fields to this record.
