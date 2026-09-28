"""Public PMI record identity and source paths, independent of OCP GDT support."""

import inspect
import pickle

import pytest

from draftwright import pmi
from draftwright._pmi_linear_geometry import _linear_reference_stations


@pytest.mark.skipif(not pmi._PMI_AVAILABLE, reason="OCP GDT support not available")
def test_report_returns_structured_reader_failures(monkeypatch):
    monkeypatch.setattr(pmi, "_PMI_AVAILABLE", False)
    assert "SetGDTMode" in pmi.extract_pmi_report("missing.step").error

    class FakeReader:
        def __init__(self, *, status=1, transfer=True):
            self.status = status
            self.transfer = transfer

        def SetGDTMode(self, _enabled):
            pass

        def SetNameMode(self, _enabled):
            pass

        def ReadFile(self, _path):
            if isinstance(self.status, Exception):
                raise self.status
            return self.status

        def Transfer(self, _doc):
            if isinstance(self.transfer, Exception):
                raise self.transfer
            return self.transfer

    monkeypatch.setattr(pmi, "_PMI_AVAILABLE", True)
    monkeypatch.setattr(pmi, "IFSelect_RetDone", 1)
    monkeypatch.setattr(pmi, "TCollection_ExtendedString", lambda value: value)
    monkeypatch.setattr(pmi, "TDocStd_Document", lambda _name: object())

    for reader, expected in (
        (FakeReader(status=RuntimeError("read exploded")), "read exploded"),
        (FakeReader(status=0), "ReadFile failed"),
        (FakeReader(transfer=RuntimeError("transfer exploded")), "transfer exploded"),
        (FakeReader(transfer=False), "Transfer failed"),
    ):
        monkeypatch.setattr(pmi, "STEPCAFControl_Reader", lambda: reader)
        assert expected in pmi.extract_pmi_report("broken.step").error


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
