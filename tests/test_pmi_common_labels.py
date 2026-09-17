"""Regression coverage for AP242 common-label extraction and lowering (#1674)."""

import math
from pathlib import Path

import pytest
from build123d import Align, Axis, Box, Cylinder

from draftwright import extract_pmi_report
from draftwright.model import Note, PmiFeature
from draftwright.model.detect import build_pmi_features
from draftwright.pmi import _face_topology_witness

CTC04 = Path(__file__).parent / "fixtures" / "nist_ctc_04_asme1_ap242.stp"


def test_ctc04_common_labels_keep_all_source_occurrences_and_associations():
    report = extract_pmi_report(CTC04)
    source_ids = tuple(f"dimension:0:1:4:{index}" for index in range(31, 35))
    sources = {source.source_id: source for source in report.sources}
    records = {
        record.source_id: record for record in report.records if record.kind == "common_label"
    }

    assert set(records) == set(source_ids)
    assert all(sources[source_id].outcome == "extracted" for source_id in source_ids)
    assert [records[source_id].label for source_id in source_ids] == ["A", "⌴", "C", "B"]
    assert [records[source_id].part21_id for source_id in source_ids] == [
        "#20358",
        "#20386",
        "#20414",
        "#20442",
    ]
    assert [records[source_id].shape_aspect_ids for source_id in source_ids] == [
        ("#18302",),
        ("#19419",),
        ("#18382",),
        ("#18341",),
    ]
    assert [len(records[source_id].reference_item_ids) for source_id in source_ids] == [1, 8, 2, 2]
    assert all(records[source_id].ref_pts for source_id in source_ids)
    assert all(records[source_id].lowering_blockers == () for source_id in source_ids)

    # B and C each reference two cylindrical faces. Their merged-box centre lies on the
    # cylinder axis, so it is not a truthful attachment site; every witness must instead lie
    # on the imported radius-6.05 face selected by its exact Part21 item.
    for source_id in source_ids[2:]:
        record = records[source_id]
        assert len(record.ref_pts) == 2
        assert record.ref_bbox is not None
        x0, y0, _z0, x1, y1, _z1 = record.ref_bbox
        axis_x, axis_y = (x0 + x1) / 2, (y0 + y1) / 2
        assert [
            math.hypot(x - axis_x, y - axis_y) for x, y, _z in record.ref_pts
        ] == pytest.approx([6.05, 6.05])


def test_each_common_label_lowers_to_one_provenance_carrying_note():
    records = [
        record for record in extract_pmi_report(CTC04).records if record.kind == "common_label"
    ]
    bbox = Box(1, 1, 1).bounding_box()

    notes = build_pmi_features(records, bbox)

    assert len(notes) == 4
    assert all(isinstance(note, Note) for note in notes)
    assert [note.source_ids for note in notes] == [(record.source_id,) for record in records]
    assert [note.part21_id for note in notes] == [record.part21_id for record in records]
    assert all(isinstance(note.origin, PmiFeature) for note in notes)
    assert [note.origin.shape_aspect_ids for note in notes] == [
        record.shape_aspect_ids for record in records
    ]
    assert [note.origin.reference_item_ids for note in notes] == [
        record.reference_item_ids for record in records
    ]


def test_common_label_witness_belongs_to_a_trimmed_face_boundary():
    alignment = (Align.CENTER, Align.CENTER, Align.MIN)
    part = Box(20, 20, 2, align=alignment) - Cylinder(5, 2, align=alignment)
    annular_face = part.faces().filter_by(Axis.Z).sort_by(Axis.Z)[-1]

    x, y, z = _face_topology_witness(annular_face.wrapped)

    assert z == pytest.approx(2.0)
    on_inner_wire = math.hypot(x, y) == pytest.approx(5.0)
    on_outer_wire = abs(x) == pytest.approx(10.0) or abs(y) == pytest.approx(10.0)
    assert on_inner_wire or on_outer_wire
