"""Compare rendered RGBA pixels and problem-code counts between two native CLIs.

Requires resvg on PATH and Pillow for this Python interpreter. Inputs are read-only;
all analyses, migrated Specs, SVGs and the JSON report go under --out. Default cases
are every corpus STEP plus every drawings.toml entry (without its saved Spec).
Saved cases cover every explicit spec/1 and spec/2 under examples/specs, matched
through drawings.toml to its STEP. Missing matches and command failures are errors.

Reproduce a branch comparison (build each binary from its corresponding checkout):
  RUSTC_WRAPPER= cargo build --locked --release --bin draftwright
  python3 tools/compare_drawings.py --base-binary /tmp/dw-main/target/release/draftwright \
    --head-binary target/release/draftwright --base-ref origin/main \
    --corpus /Users/paul/repos/quiddity/tests/corpus \
    --examples /Users/paul/repos/dw-examples --out /tmp/dw-drawing-comparison

The base checkout can be created with git worktree add --detach /tmp/dw-main origin/main.
Build it there with the same cargo command. Neither checkout's inputs are rewritten.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import gzip
import io
import json
import os
from pathlib import Path
import subprocess
import tomllib


LEGACY = {'draftwright.spec/1', 'draftwright.spec/2'}


def cases(corpus, examples, inputs):
    result = []
    for number, path in enumerate(sorted(corpus.rglob('*'))):
        if not path.name.lower().endswith(('.step', '.stp', '.step.gz', '.stp.gz')):
            continue
        source = path
        if path.suffix == '.gz':
            source = inputs / str(number) / path.name[:-3]
            source.parent.mkdir()
            source.write_bytes(gzip.decompress(path.read_bytes()))
        result.append(('default', f'corpus-{number}-{path.stem}', source, None))
    drawings = tomllib.loads((examples / 'drawings.toml').read_text())['drawing']
    for d in drawings:
        result.append(('default', 'example-' + d['name'], examples / d['step'], None))
    for path in sorted((examples / 'specs').rglob('*.json')):
        if json.loads(path.read_text()).get('schema') not in LEGACY:
            continue
        matches = {examples / d['step'] for d in drawings
                   if 'spec' in d and (examples / d['spec']).resolve() == path.resolve()}
        if len(matches) != 1:
            raise ValueError(f'{path}: expected exactly one matching STEP, found {matches}')
        result.append(('saved', path.stem, matches.pop(), path))
    for _, _, step, _ in result:
        if not step.is_file():
            raise ValueError(f'Missing STEP: {step}')
    return result


def pixel_difference(before, after):
    """Count differing RGBA pixels, including colour changes under transparent alpha."""
    if before.size != after.size:
        return {'before_size': before.size, 'after_size': after.size}
    left, right = before.convert('RGBA').tobytes(), after.convert('RGBA').tobytes()
    if left == right:
        return None
    return {'different_pixels': sum(left[n:n + 4] != right[n:n + 4]
                                    for n in range(0, len(left), 4))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-binary', type=Path, required=True)
    parser.add_argument('--head-binary', type=Path, required=True)
    parser.add_argument('--base-ref', default='origin/main')
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--examples', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--only', choices=['default', 'saved', 'all'], default='all')
    args = parser.parse_args()
    from PIL import Image
    args.out.mkdir(parents=True, exist_ok=False)
    inputs = args.out / 'inputs'
    inputs.mkdir()
    tasks = [c for c in cases(args.corpus, args.examples, inputs)
             if args.only == 'all' or c[0] == args.only]
    env = dict(os.environ, HAECCEITY_THREADS='1', RUSTC_WRAPPER='')
    env.pop('DRAFTWRIGHT_PLAN_CACHE', None)
    binaries = {'base': args.base_binary.resolve(), 'head': args.head_binary.resolve()}

    def command(binary, argv, destination):
        reply = subprocess.run([str(binary), *map(str, argv)], env=env,
                               capture_output=True, timeout=3600)
        destination.write_bytes(reply.stdout)
        if reply.returncode:
            raise RuntimeError(f'{argv}: {reply.returncode}: {reply.stderr.decode()}')
        return json.loads(reply.stdout)

    def raster(path):
        png = subprocess.check_output(['resvg', str(path), '-c'], stderr=subprocess.DEVNULL,
                                      timeout=120)
        return Image.open(io.BytesIO(png)).convert('RGBA')

    def compare(case):
        group, name, step, saved = case
        replies = {}
        for side, binary in binaries.items():
            directory = args.out / group / name / side
            directory.mkdir(parents=True)
            spec = saved
            if side == 'head' and saved:
                plan = command(binary, ['plan', step, '--spec', saved], directory / 'open.json')
                spec = directory / 'migrated.spec.json'
                if plan['spec']['schema'] != 'draftwright.spec/3':
                    raise ValueError(f'{saved}: migration did not yield spec/3')
                spec.write_text(json.dumps(plan['spec']))
            extra = ['--spec', spec] if spec else []
            replies[side] = command(binary, ['render', step, *extra, '-o', directory / 'drawing',
                                            '--formats', 'svg'], directory / 'render.json')
        pages = {side: [Path(p) for p in reply['written'] if p.endswith('.svg')]
                 for side, reply in replies.items()}
        codes = {side: dict(sorted(Counter(p['code'] for p in reply.get('problems', [])).items()))
                 for side, reply in replies.items()}
        differences = []
        if len(pages['base']) != len(pages['head']):
            differences.append({'page_counts': {s: len(p) for s, p in pages.items()}})
        for n, (before, after) in enumerate(zip(pages['base'], pages['head']), 1):
            delta = pixel_difference(raster(before), raster(after))
            if delta:
                differences.append({'page': n, **delta})
        row = {'group': group, 'case': name, 'step': str(step),
               'saved_spec': str(saved) if saved else None,
               'pages': len(pages['base']), 'ink_changes': differences,
               'before_codes': codes['base'], 'after_codes': codes['head'],
               'codes_equal': codes['base'] == codes['head']}
        print(json.dumps(row if differences or not row['codes_equal'] else
                         {'case': name, 'pages': row['pages'], 'identical': True}), flush=True)
        return row

    with ThreadPoolExecutor(args.workers) as pool:
        rows = list(pool.map(compare, tasks))
    changed = [r for r in rows if r['ink_changes'] or not r['codes_equal']]
    report = {'base_ref': args.base_ref,
              'base_sha': subprocess.check_output(['git', 'rev-parse', args.base_ref], text=True).strip(),
              'head_sha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
              'binaries': {s: str(p) for s, p in binaries.items()},
              'groups': {g: {'cases': sum(r['group'] == g for r in rows),
                             'pages': sum(r['pages'] for r in rows if r['group'] == g)}
                         for g in ['default', 'saved']},
              'changed': changed, 'rows': rows}
    (args.out / 'comparison.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'groups': report['groups'], 'changed': len(changed)}), flush=True)
    return int(bool(changed))


if __name__ == '__main__':
    raise SystemExit(main())
