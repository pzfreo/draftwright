"""Apply individually reviewed PMI audit differences, with exact before values and reasons.

Usage: python3 -I tools/record_known_pmi.py <known.json> <reviewed-changes.json>

Each change is {"before": <whole old entry or null>, "after": <whole new entry or
null>, "reason": "..."}. Read the audit's JSON diagnostics and review the drawing
before preparing changes. A removal records its reason on stdout; an addition or
replacement also appends it to the entry's evidence. This does not infer verdicts
or regenerate a known list from failing tests.
"""
import argparse
import json
from pathlib import Path


def identity(row):
    if 'record' in row:
        return row['file'], row['record'], row['field']
    return row['file'], row['code'], row['source']


def reviewed(rows, changes):
    rows = [dict(row) for row in rows]
    seen = set()
    for change in changes:
        if set(change) != {'before', 'after', 'reason'}:
            raise ValueError('each change needs before, after and reason')
        before, after, reason = (change[k] for k in ('before', 'after', 'reason'))
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('each change needs a nonempty review reason')
        if before is None and after is None:
            raise ValueError('empty change')
        key = identity(before if before is not None else after)
        if key in seen:
            raise ValueError(f'duplicate change: {key}')
        seen.add(key)
        matches = [i for i, row in enumerate(rows) if identity(row) == key]
        if before is None:
            if matches:
                raise ValueError(f'addition already exists: {key}')
        elif len(matches) != 1 or rows[matches[0]] != before:
            raise ValueError(f'expected entry differs: {key}')
        if after is not None:
            fields = ({'file', 'record', 'port', 'field', 'python', 'rust'}
                      if 'record' in after else {'file', 'code', 'source', 'python', 'rust'})
            if set(after) != fields | {'verdict', 'evidence'} or identity(after) != key:
                raise ValueError(f'incomplete replacement or changed identity: {key}')
            if after['verdict'] not in {'rust-correct', 'rust-wrong', 'equivalent',
                                        'undetermined', 'not-applicable'}:
                raise ValueError(f'invalid verdict: {key}')
            if not isinstance(after['evidence'], str) or not after['evidence'].strip():
                raise ValueError(f'missing evidence: {key}')
            after = dict(after, evidence=after['evidence'] + ' ' + reason.strip())
        if before is not None:
            index = matches[0]
            if after is None:
                rows.pop(index)
            else:
                rows[index] = after
        else:
            rows.append(after)
    keys = [identity(row) for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError('duplicate entries in resulting list')
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('known', type=Path)
    parser.add_argument('changes', type=Path)
    args = parser.parse_args()
    if args.known.name not in {'known_pmi_drawings.json', 'known_pmi_coverage.json'}:
        parser.error('only the two native PMI audit lists are supported')
    changes = json.loads(args.changes.read_text())
    try:
        result = reviewed(json.loads(args.known.read_text()), changes)
    except (KeyError, TypeError, ValueError) as error:
        parser.error(str(error))
    args.known.write_text(json.dumps(result, indent=1, ensure_ascii=False) + '\n')
    for change in changes:
        row = change['before'] if change['before'] is not None else change['after']
        print(identity(row), change['reason'])


if __name__ == '__main__':
    main()
