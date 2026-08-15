"""Contributor surfaces keep the package-owned delivery protocol discoverable."""

from pathlib import Path

ROOT = Path(__file__).parents[1]
PROTOCOL = "https://github.com/pzfreo/b123d-recognisers/blob/main/docs/delivery-protocol.md"


def test_contributor_guide_links_protocol_and_preserves_ownership_boundary() -> None:
    guide = " ".join((ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8").split())
    assert PROTOCOL in guide
    for owned_stage in (
        "IR adapter",
        "Sheet` declaration",
        "generated-code round trip",
        "drawing regression",
        "completeness decision",
    ):
        assert owned_stage in guide
    assert "must not duplicate geometry recognition" in guide
    assert "never commit a path or Git override" in guide


def test_pull_request_template_requires_the_two_checkout_protocol() -> None:
    template = (ROOT / ".github/pull_request_template.md").read_text(encoding="utf-8")
    assert PROTOCOL in template
    assert "two-checkout check" in template
