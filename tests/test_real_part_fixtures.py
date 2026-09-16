"""Source identity and importability of retained real-part fixtures."""

import hashlib
from pathlib import Path

import pytest
from build123d import import_step

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("name", "sha256"),
    [
        (
            "issue_1595_whistle_key_frame.step",
            "8f060dfd4eeb4ff9589243f85eea8ca027784ac51ae98dc2d29a2371a2d115d0",
        ),
        (
            "issue_1595_whistle_key_lever.step",
            "1cbb191930b641b74f7706a472ea8b8c2da1a2b1f571015092d31bff797ee6da",
        ),
    ],
)
def test_the_whistle_key_fixture_pair_retains_its_source_identity_issue_1655(name, sha256):
    """Keep the mating frame and lever from pzfreo/whistle-key@7fb4d14 together."""
    source = FIXTURES / name

    assert hashlib.sha256(source.read_bytes()).hexdigest() == sha256
    part = import_step(str(source))
    assert part.is_valid
    assert len(part.solids()) == 1
