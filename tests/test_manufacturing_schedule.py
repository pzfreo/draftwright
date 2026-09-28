"""The PMI table is an alternate carrier, not a lossy label abbreviation."""

from dataclasses import replace
from types import SimpleNamespace

from draftwright.linting.pmi_coverage import lint_manufacturing_references
from draftwright.model.ir import CylindricalReference, KnurlRequirement, ThreadRequirement
from draftwright.model.manufacturing_schedule import (
    _wrap_requirement,
    manufacturing_schedule,
)


def _reference(diameter):
    return CylindricalReference(
        axis_origin=(0.0, 0.0, 0.0),
        axis_direction=(1.0, 0.0, 0.0),
        radius=diameter / 2,
        axial_interval=(0.0, 10.0),
        sense="external",
    )


def _thread():
    return ThreadRequirement(
        application="external",
        designation="M3 x 0.5-6g RH",
        nominal_diameter=3.0,
        pitch=0.5,
        tolerance_class="6g",
        hand="RH",
        text="M3 x 0.5-6g RH, full available length",
        source_ids=("manufacturing_requirement:#1",),
        part21_id="#1",
        shape_aspect_ids=("#1:aspect",),
        reference_item_ids=("#1:face",),
        cylindrical_refs=(_reference(3.0),),
        full_available_length=True,
    )


def _knurl():
    return KnurlRequirement(
        pattern="straight",
        pitch=1.0,
        full_width=True,
        text="Straight knurl, full width between C0.3 chamfers",
        source_ids=("manufacturing_requirement:#2",),
        part21_id="#2",
        shape_aspect_ids=("#2:aspect",),
        reference_item_ids=("#2:face",),
        cylindrical_refs=(_reference(10.0),),
        edge_chamfer=0.3,
        maximum_diameter=10.0,
        processes=("cut", "formed"),
    )


def test_long_imported_requirements_get_stable_complete_keyed_rows():
    thread_owner = SimpleNamespace(thread=_thread(), knurl=None)
    knurl_owner = SimpleNamespace(thread=None, knurl=_knurl())
    model = SimpleNamespace(features=[knurl_owner, thread_owner])

    schedule = manufacturing_schedule(model, include_source_pmi=True)

    assert schedule is not None
    assert schedule.tags_by_source == {
        "manufacturing_requirement:#1": "MFG 1",
        "manufacturing_requirement:#2": "MFG 2",
    }
    assert schedule.owners == (thread_owner, knurl_owner)
    assert schedule.rows[0] == ("REF", "MANUFACTURING REQUIREMENT")
    by_tag = {}
    current = None
    for tag, text in schedule.rows[1:]:
        if tag:
            current = tag
            by_tag[current] = []
        by_tag[current].append(text)
    for entry in schedule.entries:
        assert " ".join(by_tag[entry.tag]) == entry.requirement.callout_suffix


def test_one_requirement_or_pmi_off_keeps_direct_callouts():
    thread_owner = SimpleNamespace(thread=_thread(), knurl=None)
    knurl_owner = SimpleNamespace(thread=None, knurl=_knurl())
    assert (
        manufacturing_schedule(SimpleNamespace(features=[thread_owner]), include_source_pmi=True)
        is None
    )
    assert (
        manufacturing_schedule(
            SimpleNamespace(features=[thread_owner, knurl_owner]), include_source_pmi=False
        )
        is None
    )


def test_long_requirement_wrap_keeps_the_final_phrase_together():
    assert _wrap_requirement(
        "M2 x 0.4-6H RH; 6 MIN FULL THREAD; 118° CONVENTIONAL DRILL POINT"
    ) == (
        "M2 x 0.4-6H RH; 6 MIN FULL THREAD; 118°",
        "CONVENTIONAL DRILL POINT",
    )


def test_ambiguous_source_identity_fails_closed_to_direct_callouts():
    thread = _thread()
    knurl = replace(_knurl(), source_ids=thread.source_ids)
    assert (
        manufacturing_schedule(
            SimpleNamespace(
                features=[
                    SimpleNamespace(thread=thread, knurl=None),
                    SimpleNamespace(thread=None, knurl=knurl),
                ]
            ),
            include_source_pmi=True,
        )
        is None
    )


def test_free_text_mention_of_manufacturing_reference_is_not_engine_claim():
    class Registry:
        def named(self, _name):
            return None

        def iter_named(self):
            return iter((("note0", SimpleNamespace(label="SEE MFG 1")),))

        def features_of(self, _name):
            return ()

    assert lint_manufacturing_references(Registry()) == []
