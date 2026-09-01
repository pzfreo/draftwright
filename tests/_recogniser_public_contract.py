"""Test helpers for the released public b123d-recognisers record contract."""

from __future__ import annotations

import dataclasses
import inspect
import types
import typing

import b123d_recognisers as recognition


def _is_record_like_class(value: object) -> bool:
    """Whether *value* has the released record shape, independent of publication."""

    if not isinstance(value, type):
        return False
    params = getattr(value, "__dataclass_params__", None)
    return bool(
        dataclasses.is_dataclass(value)
        and params is not None
        and params.frozen
        and callable(getattr(value, "to_dict", None))
    )


def is_public_record_class(value: object) -> bool:
    """Whether *value* is a record class published at the package root."""

    if not _is_record_like_class(value):
        return False
    name = getattr(value, "__name__", "")
    return name in recognition.__all__ and getattr(recognition, name, None) is value


def _nested_record_like_classes(annotation: object) -> set[type]:
    """Find structurally record-like classes anywhere in an annotation."""

    if _is_record_like_class(annotation):
        return {annotation}
    found: set[type] = set()
    for member in typing.get_args(annotation):
        found |= _nested_record_like_classes(member)
    return found


def public_record_return_types(annotation: object, *, source: str) -> set[type]:
    """Validate and return the records in one public emitter's return annotation.

    Non-record returns are outside this contract and contribute nothing. Once a public record
    occurs anywhere in the annotation, however, ADR 0013's emitter grammar is strict: the
    top-level shape is ``list[...]`` and every list member is a public record class.
    """

    nested = _nested_record_like_classes(annotation)
    if not nested:
        return set()

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    assert origin is list and len(args) == 1, (
        f"{source} returns public record(s) in {annotation!r}; expected list[PublicRecord]"
    )
    element = args[0]
    element_origin = typing.get_origin(element)
    members = (
        typing.get_args(element)
        if element_origin in (typing.Union, types.UnionType)
        else (element,)
    )
    invalid = [member for member in members if not is_public_record_class(member)]
    assert not invalid, (
        f"{source} has non-public-record list member(s) {invalid!r} in {annotation!r}"
    )
    return set(members)


def public_record_universe() -> set[type]:
    """Every record type returned by every released public package function."""

    universe: set[type] = set()
    for name in recognition.__all__:
        fn = getattr(recognition, name)
        if inspect.isclass(fn) or not callable(fn):
            continue
        try:
            hints = typing.get_type_hints(fn)
        except Exception as exc:
            raise AssertionError(
                f"could not resolve return hints for recognition.{name}: {exc!r}"
            ) from exc
        found = public_record_return_types(hints.get("return"), source=f"recognition.{name}")
        if name.startswith(("recognise_", "project_")) or name == "step_level_records":
            assert found, (
                f"recognition.{name} has no public-record return annotation "
                f"(got {hints.get('return')!r})"
            )
        universe |= found
    return universe
