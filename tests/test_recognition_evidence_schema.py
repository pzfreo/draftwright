"""The provider rename changes a closed field and therefore requires schema v2."""

import json
from pathlib import Path

import pytest
from jsonschema import ValidationError
from jsonschema.validators import validator_for


@pytest.mark.parametrize("name", ["draftwright-report", "draftwright-step-inspection"])
@pytest.mark.parametrize("version,provider", [(1, "b123d-recognisers"), (2, "quiddity")])
def test_each_evidence_schema_accepts_only_its_own_provider_and_version(name, version, provider):
    path = Path(__file__).parents[1] / f"docs/reference/{name}-v{version}.schema.json"
    schema = json.loads(path.read_text())
    validator_for(schema).check_schema(schema)
    assert schema["$id"].endswith(path.name)
    assert schema["properties"]["schema_version"] == {"const": version}
    producer_schema = schema["properties"]["producer"]
    assert producer_schema["additionalProperties"] is False
    assert set(producer_schema["required"]) == {"draftwright", provider}
    assert set(producer_schema["properties"]) == {"draftwright", provider}
    validator = validator_for(schema)(producer_schema)
    current = {"draftwright": "0.4.20", provider: "0.2.2" if version == 2 else "0.4.14"}
    validator.validate(current)
    other = "quiddity" if version == 1 else "b123d-recognisers"
    for malformed in (
        {"draftwright": "0.4.20"},
        {"draftwright": "0.4.20", other: "0.2.2"},
        {**current, other: "0.2.2"},
        {**current, "future-provider": "1.0"},
        {**current, provider: ""},
        {**current, provider: None},
        {provider: current[provider]},
    ):
        with pytest.raises(ValidationError):
            validator.validate(malformed)
