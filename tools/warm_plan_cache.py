#!/usr/bin/env python3
"""Fill a plan cache with the native analysis and default plan of each of the 68 survey inputs.

CI's test shards and quality-pins measurements analyse and plan these parts under the default
Spec again and again; the plan-cache jobs do each once with this commit's executable, and the
later jobs read the entries (src/plan_cache.rs keys them by engine fingerprint and every input,
so they only ever match the same engine and inputs). A part that fails here is only not cached: the jobs that
read the cache plan it themselves, and fail there if it is broken.

  python3 -I tools/warm_plan_cache.py --binary target/release/draftwright \
      --corpus "$DRAFTWRIGHT_CORPUS" --cache plan-cache --inputs target/warm-inputs [--shard 1/2]
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def shard(files, weights, spec):
    """Share heaviest-first files out to the lightest shard, as ci_acceptance.shard_tasks does."""
    k, n = (int(x) for x in spec.split('/'))
    if not 1 <= k <= n:
        raise SystemExit(f'--shard {spec}: expected k/n with 1 <= k <= n')
    load_, shares = [0.0] * n, [[] for _ in range(n)]
    for path in files:
        i = min(range(n), key=lambda j: (load_[j], j))
        load_[i] += weights.get(path.stem, 10)
        shares[i].append(path)
    return shares[k - 1]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--inputs', type=Path, required=True, help='where PMI fixtures are decompressed')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--shard', default='1/1', help='k/n: warm only this share of the parts')
    args = parser.parse_args()
    files = load('survey_repeated_spans').inputs(args.corpus, args.inputs)
    weights = load('ci_acceptance').WEIGHTS
    # Heaviest first, so the slowest part does not start last.
    files.sort(key=lambda p: (-weights.get(p.stem, 10), p.stem))
    files = shard(files, weights, args.shard)
    args.cache.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'DRAFTWRIGHT_PLAN_CACHE': str(args.cache.resolve())}

    def plan(path):
        start = time.monotonic()
        p = subprocess.run([str(args.binary.resolve()), 'plan', str(path)], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        return path.stem, p.returncode, time.monotonic() - start, p.stderr[-300:]

    start = time.monotonic()
    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(plan, files))
    for stem, code, seconds, error in sorted(results, key=lambda r: -r[2]):
        print(f'{seconds:7.1f}s {stem}' + (f'  not cached (exit {code}): {error}' if code else ''))
    entries = len(list(args.cache.glob('plan-*.json')))
    refused = sorted(p.name for p in args.cache.glob('*.refused'))
    print(f'{len(files)} parts, {entries} plans, {time.monotonic() - start:.1f}s wall')
    for name in refused:
        print(f'::warning::plan cache refused an entry that did not read back identically: {name}')


if __name__ == '__main__':
    main()
