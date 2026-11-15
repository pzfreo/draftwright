#!/usr/bin/env python3
"""Compare two release CLIs on PMI fixtures and all recognised turned corpus parts.

Writes raw analyses, plans, reviews (including SVGs and findings) and a JSON comparison
outside the checkout. SVG comparison is exact byte equality, with no hash baselines.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import subprocess


def steps(root):
    return sorted(p for p in root.rglob('*') if p.is_file()
                  and p.name.lower().endswith(('.step', '.stp', '.step.gz', '.stp.gz')))


def run(binary, operation, path, destination, analysis=None, resume=False):
    if resume and destination.exists():
        try:
            saved = json.loads(destination.read_bytes())
            key = {"analyse": "binding", "plan": "sheet", "review": "svg"}[operation]
            if key in saved:
                return saved
        except (ValueError, OSError):
            pass
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [str(binary), operation, str(path)]
    if analysis is not None:
        command += ['--analysis', str(analysis)]
    with destination.open('wb') as output:
        result = subprocess.run(command, stdout=output, stderr=subprocess.PIPE)
    destination.with_suffix('.stderr').write_bytes(result.stderr)
    if result.returncode:
        return {'exit': result.returncode, 'error': result.stderr.decode(errors='replace'),
                'stdout': destination.read_text(errors='replace')}
    return json.loads(destination.read_bytes())


def codes(findings):
    return dict(sorted(Counter(p['code'] for p in findings).items()))


def plan_signature(plan):
    if 'exit' in plan:
        return plan
    return {
        'sheet_scale': [{k: s.get(k) for k in ('number', 'size', 'page', 'scale', 'scale_factor')}
                        for s in [plan['sheet'], *plan.get('sheets', [])]],
        'view_scales': [(v['id'], v.get('sheet'), v['scale']) for v in plan['views']],
        'dimensions': [{k: d.get(k) for k in ('id', 'label', 'state', 'sheet')}
                       for d in plan['dimensions']],
        'problems': codes(plan['problems']),
    }


def review_signature(review):
    if 'exit' in review:
        return review
    return {k: codes(review[k]) for k in ('problems', 'lint', 'structural')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--head', type=Path, required=True)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--resume', action='store_true',
                        help='reuse successful captures; only use with the same inputs and binaries')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    fixtures = steps(root / 'tests/fixtures/pmi') + steps(root / 'tests/fixtures/turned_knurl')
    corpus = steps(args.corpus)
    inputs = [('fixtures/' + str(p.relative_to(root / 'tests/fixtures')), p, True) for p in fixtures]
    inputs += [('corpus/' + str(p.relative_to(args.corpus)), p, False) for p in corpus]
    args.out.mkdir(parents=True, exist_ok=True)

    def check(item):
        key, path, fixture = item
        analyses, files = {}, {}
        for version in ('base', 'head'):
            files[version] = args.out / version / key / 'analysis.json'
            analyses[version] = run(getattr(args, version), 'analyse', path, files[version], resume=args.resume)
        turned = any(f['kind'] in ('turned_body', 'turned_step')
                     for a in analyses.values() for f in a.get('features', []))
        entry = {'input': key, 'fixture': fixture, 'turned': turned,
                 'analysis_errors': {v: a for v, a in analyses.items() if 'exit' in a}}
        if not fixture and not turned:
            return entry
        results = {}
        for version in ('base', 'head'):
            binary = getattr(args, version)
            directory = files[version].parent
            analysis = files[version] if 'exit' not in analyses[version] else None
            plan = run(binary, 'plan', path, directory / 'plan.json', analysis, args.resume)
            review = run(binary, 'review', path, directory / 'review.json', analysis, args.resume)
            svgs = [review['svg'], *review.get('sheets', [])] if 'svg' in review else []
            for n, svg in enumerate(svgs, 1):
                (directory / f'sheet{n}.svg').write_bytes(svg.encode())
            results[version] = (plan_signature(plan), review_signature(review), svgs)
        entry['rendered'] = True
        entry['errors'] = {v: [r for r in results[v][:2] if 'exit' in r] for v in results
                           if any('exit' in r for r in results[v][:2])}
        entry['differences'] = {}
        for i, name in enumerate(('plan', 'review')):
            a, b = results['base'][i], results['head'][i]
            if a != b:
                entry['differences'][name] = {'base': a, 'head': b}
        a, b = results['base'][2], results['head'][2]
        entry['svg_bytes_identical'] = a == b and not entry['errors']
        if a != b:
            entry['differences']['svg_sheets'] = [i + 1 for i in range(max(len(a), len(b)))
                                                  if i >= len(a) or i >= len(b) or a[i] != b[i]]
        return entry

    entries = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(check, item): item[0] for item in inputs}
        for future in as_completed(futures):
            entry = future.result()
            entries.append(entry)
            print(json.dumps({k: entry[k] for k in ('input', 'turned', 'rendered', 'errors',
                                                    'svg_bytes_identical') if k in entry}), flush=True)
            (args.out / 'inventory.json').write_text(json.dumps(sorted(entries, key=lambda e: e['input']), indent=2))
    changed = [e for e in entries if e.get('differences')]
    counts = {'fixture_inputs': len(fixtures), 'corpus_scanned': len(corpus),
              'turned_corpus': sum(e['turned'] and not e['fixture'] for e in entries),
              'compared': sum(e.get('rendered', False) for e in entries),
              'changed': len(changed),
              'identical_svg': sum(e.get('svg_bytes_identical', False) for e in entries),
              'render_errors': sum(bool(e.get('errors')) for e in entries),
              'analysis_errors': sum(bool(e['analysis_errors']) for e in entries)}
    (args.out / 'comparison.json').write_text(json.dumps({'counts': counts, 'changed': changed}, indent=2))
    print(json.dumps(counts), flush=True)


if __name__ == '__main__':
    main()
