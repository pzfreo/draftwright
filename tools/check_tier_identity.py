"""Compare main and candidate drawing bytes and problem data, excluding only the new tier.

Includes every STEP/STP (plain or gzip) under DRAFTWRIGHT_CORPUS and tests/fixtures/pmi.
Evidence stays in --out; no absolute output hashes or recorded baselines are used.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
TIERS = {'fix_before_printing', 'tidy', 'information'}


def without_tier(value, candidate=False):
    if isinstance(value, dict):
        if 'code' in value and 'severity' in value:
            if candidate and value.get('tier') not in TIERS:
                raise ValueError(f'missing/invalid tier: {value}')
            return {k: without_tier(v, candidate) for k, v in value.items() if k != 'tier'}
        return {k: without_tier(v, candidate) for k, v in value.items()}
    if isinstance(value, list):
        return [without_tier(v, candidate) for v in value]
    return value


def inputs(corpus):
    return sorted(p for root in (corpus, ROOT / 'tests/fixtures/pmi') for p in root.rglob('*')
                  if p.is_file() and p.name.lower().endswith(('.step', '.stp', '.step.gz', '.stp.gz')))


def capture(binary, part, directory):
    directory.mkdir(parents=True)
    def run(*args):
        result = subprocess.run([str(binary), *map(str, args)], capture_output=True,
                                check=True, timeout=3600)
        return json.loads(result.stdout)
    plan = run('plan', part)
    (directory / 'plan.json').write_text(json.dumps(plan))
    render = run('render', part, '-o', directory / 'drawing', '--formats', 'svg,dxf')
    (directory / 'render.json').write_text(json.dumps(render))
    review = run('review', part)
    (directory / 'review.json').write_text(json.dumps(review))
    files = {p.name: p.read_bytes() for p in directory.iterdir() if p.suffix in ('.svg', '.dxf')}
    if not files or not any(p.endswith('.svg') for p in files) or not any(p.endswith('.dxf') for p in files):
        raise ValueError(f'{part}: missing drawing outputs')
    # Output destinations necessarily differ, but every returned problem must agree.
    del render['written']
    return {'plan': plan, 'render': render, 'review': review}, files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    parser.add_argument('--corpus', type=Path, default=os.environ.get('DRAFTWRIGHT_CORPUS'))
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    if args.corpus is None or not args.corpus.is_dir() or args.out.exists():
        parser.error('a corpus and fresh output directory are required')
    parts = inputs(args.corpus)
    if not parts:
        parser.error('no STEP inputs')
    def check(task):
        i, part = task
        directory = args.out / f'{i:03}-{part.name}'
        before, old_files = capture(args.before.resolve(), part, directory / 'before')
        after, new_files = capture(args.after.resolve(), part, directory / 'after')
        if without_tier(before) != without_tier(after, candidate=True):
            raise ValueError(f'{part}: plan/render/review changed apart from tier')
        if old_files != new_files:
            changed = sorted(k for k in old_files.keys() | new_files.keys()
                             if old_files.get(k) != new_files.get(k))
            raise ValueError(f'{part}: SVG/DXF bytes or sheet inventory changed: {changed}')
        print(f'{part}: {len(new_files)} SVG/DXF files, plan/render/review identical apart from tier', flush=True)
        return len(new_files)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        count = sum(pool.map(check, enumerate(parts)))
    print(f'PASS: {len(parts)} inputs, {count} byte-identical SVG/DXF files; all problem lists unchanged apart from tier')


if __name__ == '__main__':
    main()
