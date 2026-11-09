"""H3 acceptance: unchanged GD&T/content, less ink and less detached dimension text.

Usage: python3 -I tools/check_label_lanes.py before-dir after-dir
Or: --current quality-dir --after-binary target/release/draftwright (CI locality gate).
Binary options regenerate stc09.plan.json from the committed compressed STEP.
"""
import itertools
import argparse
import gzip
import json
import os
from pathlib import Path
import subprocess
import sys

def shared_dimension_lines(plan):
    linear = [d for d in plan['dimensions'] if d['kind'] == 'linear'
              and d.get('placed') and len(d['placed']['arrows']) == 2]
    pairs = set()
    for a, b in itertools.combinations(linear, 2):
        p, q = a['placed'], b['placed']
        k = int(p['rotation'] != 0)
        if a['view'] != b['view'] or p['rotation'] != q['rotation']:
            continue
        if abs(p['arrows'][0][0][1-k] - q['arrows'][0][0][1-k]) >= 1e-6:
            continue
        lo, hi = sorted(x[0][k] for x in p['arrows'])
        low, high = sorted(x[0][k] for x in q['arrows'])
        if min(hi, high) - max(lo, low) > 1e-6:
            pairs.add(tuple(sorted([a['id'], b['id']])))
    return pairs


def crosses(segment, box):
    """Clip a stroke to the open label box; endpoint touches are not crossings."""
    p, q = segment
    low, high = 0., 1.
    for k in (0, 1):
        slope = q[k] - p[k]
        if abs(slope) < 1e-12:
            if not box[k] < p[k] < box[k+2]:
                return False
        else:
            ends = sorted(((box[k] - p[k]) / slope, (box[k+2] - p[k]) / slope))
            low, high = max(low, ends[0]), min(high, ends[1])
    return high - low > 1e-9


def cleared_labels(before, after):
    old = {d['id']: d for d in before['dimensions'] + before['gdt'] if d.get('placed')}
    new = {d['id']: d for d in after['dimensions'] + after['gdt'] if d.get('placed')}
    count = 0
    for id_, d in new.items():
        if id_ not in old or d.get('kind') != 'linear':
            continue
        box, prior = d['placed']['label_box'], old[id_]['placed']['label_box']
        padded = [box[0]-1, box[1]-1, box[2]+1, box[3]+1]
        for peer, q in new.items():
            if peer == id_ or peer not in old:
                continue
            for a, b in zip(old[peer]['placed']['lines'], q['placed']['lines']):
                if crosses(a, prior) and not crosses(b, box):
                    assert not crosses(b, padded), (id_, peer, 'cleared label has less than 1 mm')
                    count += 1
    return count


def newly_near(before, after):
    """Moved labels that come within 1 mm of another item's stroke they were clear of."""
    def near(plan, id_):
        items = {d['id']: d for d in plan['dimensions'] + plan.get('gdt', []) if d.get('placed')}
        b = items[id_]['placed']['label_box']
        pad = [b[0]-1, b[1]-1, b[2]+1, b[3]+1]
        return {j for j, d in items.items() if j != id_
                and any(crosses(l, pad) and not crosses(l, b)
                        for l in d['placed']['lines'] + d['placed']['arrows'])}
    old = {d['id']: d for d in before['dimensions'] if d.get('placed')}
    return [(d['id'], sorted(near(after, d['id']) - near(before, d['id'])))
            for d in after['dimensions'] if d.get('placed') and d['id'] in old
            and d['placed']['label_box'] != old[d['id']]['placed']['label_box']
            and near(after, d['id']) - near(before, d['id'])]


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('before', type=Path, nargs='?')
parser.add_argument('after', type=Path, nargs='?')
parser.add_argument('--current', type=Path)
parser.add_argument('--before-binary', type=Path)
parser.add_argument('--after-binary', type=Path)
parser.add_argument('--corpus', type=Path, default=os.environ.get('DRAFTWRIGHT_CORPUS'))
args = parser.parse_args()
before, after = args.before, args.after
if args.current:
    after = args.current
elif before is None or after is None:
    parser.error('supply before/after directories or --current')
# No hand-made AP242 Plan is needed. A supplied binary regenerates the protected fixture.
for directory, binary in [(before, args.before_binary), (after, args.after_binary)]:
    if binary:
        directory.mkdir(parents=True, exist_ok=True)
        root = Path(__file__).resolve().parents[1]
        if not (directory / 'baseline.json').is_file():
            assert args.corpus and args.corpus.is_dir(), '--corpus or DRAFTWRIGHT_CORPUS required to generate quality'
            files = [str(args.corpus / name) for name in (root / 'tools/quality_parts.txt').read_text().splitlines()]
            with (directory / 'quality.log').open('w') as log:
                subprocess.run([sys.executable, '-I', str(root / 'tools/quality.py'), str(binary.resolve()),
                                str(directory.resolve()), *files], stdout=log, stderr=subprocess.STDOUT, check=True)
        fixture = root / 'tests/fixtures/pmi/step/nist_stc_09_asme1_ap242-e4.stp.gz'
        step = directory / 'stc09.stp'
        step.write_bytes(gzip.decompress(fixture.read_bytes()))
        result = subprocess.run([str(binary.resolve()), 'plan', str(step.resolve())],
                                capture_output=True, text=True, check=True)
        (directory / 'stc09.plan.json').write_text(result.stdout)
if args.current:
    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, '-I', str(root / 'tools/check_quality_table.py'),
                    str(root / 'docs/status-2026-10-10.md'), str(after / 'baseline.json')], check=True)
    plan = json.loads((after / 'stc09.plan.json').read_text())
    dimensions = {d['id']: d for d in plan['dimensions']}
    slot = dimensions['d:f:slot:f102/slot_length.length']['placed']
    # Slots 1 and 2 are congruent and now share one `2× SLOT` callout, so slot 3's length is
    # held against the innermost lane on its side of the front view instead of slot 1's length.
    view = dimensions['d:f:slot:f102/slot_length.length']['view']
    lanes = [d['placed']['arrows'][0][0][1] for d in plan['dimensions']
             if d.get('placed') and d['kind'] == 'linear' and d.get('view') == view
             and d['placed']['rotation'] == slot['rotation'] and d['placed']['arrows'][0][0][1] < slot['lines'][0][0][1]]
    assert abs(slot['arrows'][0][0][1] - max(lanes)) <= 14 + 1e-6, 'slot skipped its next lane'
    assert all(abs(a[1] - b[1]) < 60 for a, b in slot['lines'][:2]), 'slot witnesses cross the tower'
    # string_post's inspection-end baseline replaces the old 1.1 mm chain link.
    # Its retained 1 mm terminal-band length (turned_step:3/step.length) is now
    # the short local span: its outside-arrow label must stay beside its own tips,
    # rather than migrating along the neighbouring baseline's longer stations.
    for part, labels in [('1000', ['6.925', '8.854', '10.854']), ('10020', ['2.845', '3.886']),
                         ('10007', ['14.031']), ('string_post', ['1'])]:
        plan = json.loads((after / f'{part}.plan.json').read_text())
        for label in labels:
            items = [d for d in plan['dimensions'] if d['kind'] == 'linear' and d['label'] == label and d.get('placed')]
            assert items, (part, label, 'missing dimension')
            for d in items:
                assert any(abs(float(label) - m['value']) <= 0.0005 + 1e-6
                           for m in d['measurements']), (part, d['id'], label, 'not the modelled value')
                p = d['placed']; k = int(p['rotation'] != 0)
                lo, hi = sorted(a[0][k] for a in p['arrows'])
                assert max(lo - p['label_box'][k+2], p['label_box'][k] - hi) < 5.7 - 1e-6, (part, label, 'detached')
    print('PASS: current own-span examples and STC-09 locality')
    sys.exit(0)
a, b = (json.loads((p / 'baseline.json').read_text()) for p in (before, after))
assert len(a) == len(b) == 30 and a.keys() == b.keys(), 'incomplete quality set'
keys = ['off_sheet', 'repeated_label', 'repeated_measure', 'views_overlap',
        'view_on_title_block', 'ink', 'repeated_span', 'line_through_feature',
        'hidden_on_visible', 'section_hidden', 'unplanned_features', 'text_off_span']
for part in a:
    assert 'error' not in a[part] and 'error' not in b[part], part
    for key in keys:
        assert b[part][key] <= a[part][key], (part, key, a[part][key], b[part][key])
    assert b[part]['faces'] == a[part]['faces'], part
    assert b[part]['claimed_faces'] >= a[part]['claimed_faces'], part
    plans = [json.loads((p / f'{part}.plan.json').read_text()) for p in (before, after)]
    # Measurements survive lane changes, including a different recovered sheet choice.
    measurements = [sorted((m['feature'], m['parameter'], m['value'])
                           for d in plan['dimensions'] for m in d['measurements'])
                    for plan in plans]
    assert measurements[0] == measurements[1], (part, 'measurements changed')
    assert shared_dimension_lines(plans[1]) <= shared_dimension_lines(plans[0]), (part, 'new shared dimension line')
for key in ['ink', 'text_off_span']:
    old, new = (sum(m[key] for m in x.values()) for x in (a, b))
    assert new < old, (key, old, new, 'no improvement')
    print(f'{key}: {old} -> {new}')
plans = [json.loads((p / 'stc09.plan.json').read_text()) for p in (before, after)]
assert plans[0]['gdt'] and plans[0]['gdt'] == plans[1]['gdt'], 'stc09 GD&T changed'
assert plans[0]['sheet'] == plans[1]['sheet'], 'stc09 sheet changed'
assert plans[0]['views'] == plans[1]['views'], 'stc09 views changed'
print('stc09 stroke/label contacts cleared with >=1 mm:', cleared_labels(*plans))
assert not newly_near(*plans), ('stc09 label moved within 1 mm of a new stroke', newly_near(*plans))
old = {d['id']: d for d in plans[0]['dimensions']}
promoted = []
for d in plans[1]['dimensions']:
    p, q = old[d['id']].get('placed'), d.get('placed')
    if d['kind'] != 'linear' or not p or not q or len(p['arrows']) != 2:
        continue
    k = int(p['rotation'] == 0)
    jump = abs(p['arrows'][0][0][k] - q['arrows'][0][0][k])
    assert jump <= 14 + 1e-6, (d['id'], 'promotion skipped lanes', jump)
    assert [l[0] for l in p['lines'][:2]] == [l[0] for l in q['lines'][:2]], d['id']
    if jump > 1e-6:
        promoted.append(d['id'])
print('stc09 local promotions:', promoted)
if args.corpus and args.before_binary and args.after_binary:
    # Hold the sheet constant to distinguish real promotion from recovered view packing.
    fixed = []
    for directory, binary in [(before, args.before_binary), (after, args.after_binary)]:
        spec = directory / 'ctc3-a3.spec.json'
        spec.write_text(json.dumps({'sheet': {'size': 'A3', 'scale': '1:5'}}))
        result = subprocess.run([str(binary.resolve()), 'plan',
                                str(args.corpus / 'nist/nist_ctc_03_asme1_rc.stp'),
                                '--spec', str(spec.resolve())], capture_output=True, text=True, check=True)
        plan = json.loads(result.stdout)
        (directory / 'ctc3-a3.plan.json').write_text(result.stdout)
        fixed.append(next(d for d in plan['dimensions'] if d['id'] == 'd:f:slot:2/slot_length.length'))
    p, q = (d['placed'] for d in fixed)
    jump = abs(q['arrows'][0][0][0] - p['arrows'][0][0][0])
    assert 1e-6 < jump <= 14 + 1e-6, ('CTC-03 fixed A3 promotion', jump)
    assert fixed[0]['measurements'] == fixed[1]['measurements']
    assert [l[0] for l in p['lines'][:2]] == [l[0] for l in q['lines'][:2]]
    print('CTC-03 fixed A3 promotion:', jump, 'mm')
print('PASS: 30 parts no worse, measurements preserved, stc09 GD&T exactly unchanged')
