"""Public PMI record identity and source paths, independent of OCP GDT support."""

import inspect
import pickle

import pytest

from draftwright import pmi
from draftwright._pmi_linear_geometry import _linear_reference_stations


def test_pmi_records_keep_source_inspection_and_pickle_paths():
    for name in ("PmiRecord", "PmiSourceEntity", "PmiExtractionReport"):
        record_type = getattr(pmi, name)
        assert record_type.__module__ == "draftwright.pmi"
        assert inspect.getsourcefile(record_type) == pmi.__file__
        assert f"class {name}:" in inspect.getsource(record_type)

    record = pmi.PmiRecord(kind="linear", type_code=2, value=12.0)
    source = pmi.PmiSourceEntity("dimension:1", "dimension", 2, "extracted")
    report = pmi.PmiExtractionReport(sources=(source,), records=(record,))
    for value in (record, source, report):
        assert pickle.loads(pickle.dumps(value)) == value


@pytest.mark.parametrize(
    ("stations", "nominal", "axis", "reason"),
    [
        (((0, 0, 0), None), 10, "?", "two measurable authored reference groups"),
        (((0, 0, 0), (0, 0, 0)), 0, "?", "same station"),
        (((0, 0, 0), (3, 4, 5)), 7, "?", "principal projection plane"),
        (((0, 0, 0), (3, 4, 0)), 5, "?", None),
        (((0, 0, 0), (10, 0, 0)), 9, "X", "differs from nominal"),
        (((0, 0, 0), (10, 0, 0)), 10, "X", None),
    ],
)
def test_linear_reference_stations_only_accept_truthful_authored_spans(
    stations, nominal, axis, reason
):
    measured, actual_axis, findings = _linear_reference_stations(stations, nominal)
    assert measured == tuple(station for station in stations if station is not None)
    assert actual_axis == axis
    if reason is None:
        assert findings == ()
    else:
        assert len(findings) == 1
        assert reason in findings[0]
