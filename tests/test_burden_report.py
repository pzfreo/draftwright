"""Machine-readable test-burden report contract."""

import json

from _burden_report import SCHEMA_VERSION, _collect_report, _document, _State
from jsonschema.validators import validator_for

_SCHEMA = "docs/reference/test-burden-report-v1.schema.json"


class _Report:
    nodeid = "tests/test_example.py::test_case"
    when = "teardown"
    worker_id = "gw0"
    user_properties = [
        (
            "draftwright_burden",
            json.dumps(
                {
                    "duration": 1.25,
                    "outcome": "passed",
                    "counts": {"drawing_builds": 2, "lint_runs": 1},
                    "recipes": [
                        {
                            "recipe": "box_60x40x20",
                            "options": {"page": "'A3'"},
                            "cache_hit": True,
                        }
                    ],
                }
            ),
        )
    ]


def test_document_aggregates_test_counts_and_recipe_requests(tmp_path):
    state = _State(tmp_path / "burden.json")
    _collect_report(state, _Report())

    document = _document(state)

    assert document["schema_version"] == SCHEMA_VERSION
    assert document["workers"] == ["gw0"]
    assert document["totals"] == {
        "duration": 1.25,
        "counts": {"drawing_builds": 2, "lint_runs": 1},
        "recipe_requests": [
            {
                "recipe": "box_60x40x20",
                "options": {"page": "'A3'"},
                "cache_hit": True,
                "count": 1,
            }
        ],
    }
    assert document["tests"][0]["counts"] == {"drawing_builds": 2, "lint_runs": 1}
    assert document["tests"][0]["phases"]["teardown"]["outcome"] == "passed"
    assert document["tests"][0]["recipes"][0]["recipe"] == "box_60x40x20"


def test_document_orders_tests_and_combines_duplicate_recipe_requests(tmp_path):
    state = _State(tmp_path / "burden.json")
    first = _Report()
    _collect_report(state, first)
    _collect_report(state, first)
    second = _Report()
    second.nodeid = "tests/test_a.py::test_first"
    _collect_report(state, second)

    document = _document(state)

    assert [row["nodeid"] for row in document["tests"]] == [
        "tests/test_a.py::test_first",
        "tests/test_example.py::test_case",
    ]
    assert document["totals"]["recipe_requests"][0]["count"] == 2


def test_document_matches_the_published_closed_schema(tmp_path):
    state = _State(tmp_path / "burden.json")
    _collect_report(state, _Report())
    schema = json.loads(open(_SCHEMA, encoding="utf-8").read())
    validator = validator_for(schema)(schema)

    validator.validate(_document(state))
