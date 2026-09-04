from __future__ import annotations

import copy

import pytest

from draftwright._section_recess_prototype import consume_section_recess_document


def _document():
    return {
        "schema_version": 1,
        "reference_scope": "result",
        "bodies": [{"index": 0}],
        "faces": [{"index": index} for index in range(5)],
        "occurrences": [
            {
                "index": 0,
                "body": 0,
                "geometry": {
                    "type": "section_recess",
                    "frame": {
                        "origin": [0.0, 0.0, 0.0],
                        "run": [0.0, 0.0, 1.0],
                        "u": [1.0, 0.0, 0.0],
                        "v": [0.0, 1.0, 0.0],
                    },
                    "run_interval": [0.0, 6.0],
                    "profile": {
                        "closure": "closed",
                        "boundary": [
                            {"point": [-6.0, -3.0], "bulge": 0.0},
                            {"point": [6.0, -3.0], "bulge": 1.0},
                            {"point": [6.0, 3.0], "bulge": 0.0},
                            {"point": [-6.0, 3.0], "bulge": 1.0},
                        ],
                    },
                    "ends": {
                        "low": {"condition": "capped", "gradient": [0.0, 0.0]},
                        "high": {"condition": "open", "gradient": [0.0, 0.0]},
                    },
                },
                "classification": {"feature_kind": "pocket", "section_shape": "obround"},
                "evidence": {
                    "defining_faces": [0, 1, 2, 3],
                    "constituent_faces": [0, 1, 2, 3, 4],
                },
            }
        ],
    }


def test_consumer_derives_dimensions_and_keeps_result_local_references() -> None:
    (pocket,) = consume_section_recess_document(_document())

    assert pocket.length == pytest.approx(18.0)
    assert pocket.width == pytest.approx(6.0)
    assert pocket.depth == pytest.approx(6.0)
    assert pocket.body == 0
    assert pocket.defining_faces == (0, 1, 2, 3)
    assert pocket.constituent_faces == (0, 1, 2, 3, 4)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update(schema_version=2), "unsupported"),
        (lambda value: value["faces"].pop(), "in range"),
        (
            lambda value: value["occurrences"][0]["classification"].update(
                section_shape="rectangle"
            ),
            "obround pockets only",
        ),
    ],
)
def test_consumer_fails_closed_on_contract_drift(mutation, message: str) -> None:
    document = copy.deepcopy(_document())
    mutation(document)

    with pytest.raises(ValueError, match=message):
        consume_section_recess_document(document)
