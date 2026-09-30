"""Required strip-ink resolution and survivor commit phases."""

from __future__ import annotations


def _resolve_required_strip_ink(run, *, annotation_ink_clear, _geom_box, _drawing_bounds) -> None:
    """Repair required exact ink or return it to normal drop handling."""
    dwg, cands = run.dwg, run.cands
    require_clear_ink, anchored = run.require_clear_ink, run.anchored
    ink_repair_candidates, ink_displaced = run.ink_repair_candidates, run.ink_displaced
    tp, solved, todo = run.tp, run.solved, run.todo
    _survival_rank = run.survival_rank
    _real_box_conflict = run.real_box_conflict
    # A force-kept GD&T frame must not bypass the exact-ink decision merely because
    # its strip tier fitted. First let the dimension batch repair its own labels;
    # then test the *whole* frame and leader against committed ink and its siblings.
    # Try bounded alternatives through the same candidate build/validation seam.
    # A remaining conflict returns to on_drop, which can relocate the declaration
    # or report its named unmet obligation. No raw position escapes to the API.
    builds_by_name = dict(cands) if require_clear_ink else {}
    for name in sorted(
        require_clear_ink,
        key=lambda item: tuple(-value for value in _survival_rank(item)) + (item,),
    ):
        index = next((i for i, (key, _item) in enumerate(solved) if key == name), None)
        if index is None:
            continue
        original = solved[index][1]
        others = [item for key, item in solved if key != name]
        if annotation_ink_clear(dwg, original, additional=others):
            continue
        replacement = None
        if not (anchored or {}).get(name, False):
            alternatives = (ink_repair_candidates or {}).get(name)
            if alternatives is not None:
                for candidate in alternatives(original):
                    box = _geom_box(candidate)
                    page = _drawing_bounds(dwg)
                    if (
                        box is None
                        or box[0] < page[0]
                        or box[1] < page[1]
                        or box[2] > page[2]
                        or box[3] > page[3]
                        or _real_box_conflict(name, box)
                        or not annotation_ink_clear(dwg, candidate, additional=others)
                    ):
                        continue
                    replacement = candidate
                    break
        if replacement is None:
            # An authored frame outranks ordinary automatic ink, but not a pin or
            # mandatory dimension. If its original position clears settled ink,
            # yield only lower-priority same-batch conflicts before yielding the
            # frame itself. The displaced candidates retain their normal on_drop
            # handlers and may recover elsewhere; a force pass must not undo this
            # exact-ink decision by putting them back on top of the frame.
            conflicts = (
                [
                    key
                    for key, item in solved
                    if key != name and not annotation_ink_clear(dwg, original, additional=(item,))
                ]
                if annotation_ink_clear(dwg, original)
                else []
            )
            if conflicts and all(
                _survival_rank(key) < _survival_rank(name) and not (anchored or {}).get(key, False)
                for key in conflicts
            ):
                displaced = set(conflicts)
                remaining = [item for key, item in solved if key != name and key not in displaced]
                if annotation_ink_clear(dwg, original, additional=remaining):
                    solved = [(key, item) for key, item in solved if key not in displaced]
                    todo.extend(
                        (key, builds_by_name[key]) for key, _build in cands if key in displaced
                    )
                    if ink_displaced is not None:
                        ink_displaced.update(displaced)
                    if tp is not None:
                        tp["placed"] = [
                            item for item in tp["placed"] if item["name"] not in displaced
                        ]
                        tp.setdefault("ink_displaced", []).extend(sorted(displaced))
                    continue
            solved.pop(index)
            todo.append((name, builds_by_name[name]))
            if tp is not None:
                tp["placed"] = [item for item in tp["placed"] if item["name"] != name]
                tp.setdefault("ink_rejected", []).append(name)
        else:
            solved[index] = (name, replacement)
            if tp is not None:
                tp.setdefault("ink_repaired", []).append(name)
    run.solved, run.todo = solved, todo


def _commit_strip_candidate_run(run):
    """Place survivors with provenance, then close the optional solve trace."""
    ctx, view = run.ctx, run.view
    features, measurements = run.features, run.measurements
    spans = run.measurement_spans
    satisfactions, declarations = run.satisfactions, run.declarations
    solved, todo, tp, trace = run.solved, run.todo, run.tp, run.trace
    for name, dim in solved:
        # Record feature provenance (ADR 5 (was 0010)): the drain-time seam for corridor-placed
        # dims — `features` maps this batch's names to their source IR feature.
        feature = (features or {}).get(name)
        measurement = (measurements or {}).get(name)
        measurement_span = (spans or {}).get(name)
        span_kwargs = (
            {"measurement_span": measurement_span} if measurement_span is not None else {}
        )
        # Preserve the established duck-typed ``ctx.place`` contract for callers that do
        # not participate in structured-note authority. Only a candidate carrying the new
        # provenance axis receives the keyword (#1351).
        satisfaction = (satisfactions or {}).get(name)
        declaration = (declarations or {}).get(name)
        if satisfaction is not None and declaration is not None:
            ctx.place(
                dim,
                name,
                view=view,
                feature=feature,
                measurement=measurement,
                satisfaction=satisfaction,
                declaration=declaration,
                **span_kwargs,
            )
        elif satisfaction is not None:
            ctx.place(
                dim,
                name,
                view=view,
                feature=feature,
                measurement=measurement,
                satisfaction=satisfaction,
                **span_kwargs,
            )
        elif declaration is not None:
            ctx.place(
                dim,
                name,
                view=view,
                feature=feature,
                measurement=measurement,
                declaration=declaration,
                **span_kwargs,
            )
        else:
            ctx.place(
                dim,
                name,
                view=view,
                feature=feature,
                measurement=measurement,
                **span_kwargs,
            )
    if tp is not None:
        tp["unplaced"] = [n for n, _ in todo]
        trace.end_pass(tp)  # folds a standalone pass's items; no-op when corridor-nested
    return todo
