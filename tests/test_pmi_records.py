"""Public PMI record identity and source paths, independent of OCP GDT support."""

import inspect
import pickle

from draftwright import pmi


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
