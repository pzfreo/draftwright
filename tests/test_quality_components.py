"""Fail-closed guards over the quality components' hand-maintained vocabularies (#1127).

The completeness and legibility components each classify a vocabulary the rest of the engine
owns: the lint drop codes, and the recognition inventories. Both are literal collections, so
a code or an inventory added elsewhere would join them by omission — legibility would stop
counting a lost annotation, and completeness would stop reporting a blind spot it advertises
as explicit. These tests turn "somebody remembered" into "CI refuses".
"""

from __future__ import annotations

import re
from pathlib import Path

from draftwright.linting.quality import (
    _NON_REQUIREMENT_INVENTORIES,
    _PLACEMENT_DROP_CODES,
    _RECOGNISED_REQUIREMENT_FAMILIES,
    _STAGE_CLASSIFIED_DROP_CODES,
    quality_components,
)
from draftwright.recognition import RecognitionResult

_SRC = Path(__file__).resolve().parents[1] / "src" / "draftwright"

# The one site that builds a drop code at runtime rather than writing it out
# (``annotations/from_model.py``'s slot/pad/pocket dim drop). Its expansion is listed here
# because a literal scrape cannot see it; the scrape's blind spot is itself guarded below.
_DYNAMIC_DROP_CODES = frozenset({"pad_dim_dropped", "pocket_dim_dropped", "slot_dim_dropped"})


def _producer_files():
    """Every engine module except the classifier itself.

    Scanning ``quality.py`` would make both directions of the ratchet vacuous: its own
    frozensets would supply every code the scrape then finds.
    """

    return sorted(path for path in _SRC.rglob("*.py") if path.name != "quality.py")


def _recorded_drop_codes() -> set[str]:
    pattern = re.compile(r"""["']([a-z_]+_dropped)["']""")
    return {
        code for path in _producer_files() for code in pattern.findall(path.read_text("utf-8"))
    } | set(_DYNAMIC_DROP_CODES)


def test_every_drop_code_the_engine_can_record_is_classified():
    classified = _PLACEMENT_DROP_CODES | _STAGE_CLASSIFIED_DROP_CODES
    unclassified = sorted(_recorded_drop_codes() - classified)

    assert unclassified == [], (
        "these lint drop codes are neither placement drops nor stage-carrying, so an "
        f"annotation they report as lost would still score as perfectly legible: {unclassified}"
    )


def test_no_classified_drop_code_has_left_the_engine():
    """The other direction: a stale entry means the classification is no longer exercised."""
    stale = sorted((_PLACEMENT_DROP_CODES | _STAGE_CLASSIFIED_DROP_CODES) - _recorded_drop_codes())

    assert stale == [], f"classified drop codes that no producer records any more: {stale}"


def test_a_runtime_built_drop_code_cannot_evade_the_literal_scrape():
    f_string_drop = re.compile(r"""f["'][^"']*\{[^}]+\}[a-z_]*_dropped["']""")
    sites = sorted(
        (str(path.relative_to(_SRC)), match)
        for path in _producer_files()
        for match in f_string_drop.findall(path.read_text("utf-8"))
    )

    assert sites == [("annotations/from_model.py", 'f"{noun}_dim_dropped"')], (
        "a drop code assembled at runtime is invisible to the literal scrape; add its "
        f"expansion to _DYNAMIC_DROP_CODES and record the new site here: {sites}"
    )


def test_every_recognition_inventory_is_either_a_requirement_family_or_excluded():
    inventories = set(RecognitionResult.__dataclass_fields__)
    classified = set(_RECOGNISED_REQUIREMENT_FAMILIES) | _NON_REQUIREMENT_INVENTORIES
    unclassified = sorted(inventories - classified)

    assert unclassified == [], (
        "completeness reports recognised-but-unscored families from a literal map, so these "
        f"inventories would be a blind spot it never admits to: {unclassified}"
    )
    assert not _NON_REQUIREMENT_INVENTORIES - inventories, (
        "excluded inventories that no longer exist make the exclusion rationale unreadable"
    )


def test_an_unavailable_completeness_component_is_data_not_a_zero_score():
    components = quality_components(
        recognition=None,
        features=(),
        registry=None,
        omissions=(),
        issues=(),
        error_penalty=0.15,
        warning_penalty=0.05,
    )
    completeness = components["completeness"]

    assert completeness["available"] is False
    assert completeness["score"] is None, "a missing inventory must not read as zero coverage"
    assert completeness["coverage"] == "unavailable"
    assert completeness["reason"] == "physical recognition inventory unavailable"
    assert completeness["requirements"] == 0
