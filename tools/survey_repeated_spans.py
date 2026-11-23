"""Survey quality parts, corpus parts used by tests, and every AP242 fixture.

Run from the repository root: python3 -I tools/survey_repeated_spans.py <binary> <absolute out>
Set DRAFTWRIGHT_CORPUS. The manifest includes literal test inputs and sheet-facts bindings;
the thirty quality parts are a subset. The survey fails on a repeated span or a failed part.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import importlib.util
import json
import re
import os
from pathlib import Path

EXCLUDED = {'cadgenbench/threaded_connector_109.step'}

module = importlib.util.spec_from_file_location('quality', Path(__file__).with_name('quality.py'))
quality = importlib.util.module_from_spec(module)
module.loader.exec_module(quality)


def shard_arg(text):
    k, n = map(int, text.split('/'))
    if not 1 <= k <= n:
        raise argparse.ArgumentTypeError('shard must be K/N with 1 <= K <= N')
    return k, n


def in_shard(names, k, n):
    """The K-th of N interleaved slices of the sorted names; the N slices partition them."""
    return sorted(names)[k - 1::n]


def inputs(corpus, directory):
    """The 70 survey inputs: the corpus manifest, which must hold every corpus file the tests
    read, and each PMI fixture decompressed into *directory*."""
    corpus, root = Path(corpus), Path(__file__).resolve().parents[1]
    assert corpus.is_dir(), corpus
    directory.mkdir(parents=True, exist_ok=True)
    files = [corpus / f for f in (root / 'tools/repeated_span_parts.txt').read_text().splitlines()]
    assert set((root / 'tools/quality_parts.txt').read_text().splitlines()) <= {
        str(p.relative_to(corpus)) for p in files}
    sources = '\n'.join(p.read_text() for p in (root / 'tests').rglob('*.rs'))
    used = {p for p in corpus.rglob('*') if p.suffix.lower() in {'.step', '.stp'}
            # A bare file name counts only as a whole name: `cgb203.step` does not use `203.step`.
            and (re.search(r'(?<![\w-])' + re.escape(p.name), sources)
                 or str(p.relative_to(corpus)) in sources)}
    used.update(corpus / json.loads(gzip.decompress(p.read_bytes()))['binding']['file']
                for p in (root / 'tests/fixtures/sheets').glob('*.gz'))
    # Left out by the maintainer: real drawings rarely model threads, so the part is a poor
    # survey case. Tests and sheet facts that mirror the Python corpus still read it.
    used -= {corpus / p for p in EXCLUDED}
    assert used <= set(files), f'add test inputs to the manifest: {sorted(used - set(files))}'
    for fixture in sorted((root / 'tests/fixtures/pmi/step').glob('*.gz')):
        path = directory / fixture.name[:-3]
        path.write_bytes(gzip.decompress(fixture.read_bytes()))
        files.append(path)
    assert all(p.is_file() for p in files), 'a survey input is missing'
    assert len({p.stem for p in files}) == len(files), 'ambiguous output basenames'
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary')
    parser.add_argument('out', type=Path)
    parser.add_argument('--timeout', type=float, default=600, help='Per render/plan timeout in seconds')
    parser.add_argument('--shard', type=shard_arg, default=(1, 1), metavar='K/N',
                        help='Survey only the K-th of N interleaved slices of the sorted part names')
    parser.add_argument('--before', type=Path, help='Verify the recorded pre-E3 patterns against this baseline')
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    verdicts = json.loads(Path('tests/fixtures/known_repeated_spans.json').read_text())
    assert all(v['verdict'] == 'rust-correct' and v['evidence'].strip() for v in verdicts)
    if args.before:
        before = json.loads(args.before.read_text())
        found = [{'part': part, **g} for part, p in before.items() for g in p['repeated_span_groups']]
        recorded = [{k: v[k] for k in ('part', 'kind', 'items')} for v in verdicts]
        assert sorted(found, key=str) == sorted(recorded, key=str), 'pre-E3 patterns differ from verdicts'
    corpus = Path(os.environ['DRAFTWRIGHT_CORPUS'])
    assert args.out.is_absolute(), 'out must be absolute'
    files = inputs(corpus, args.out / 'inputs')
    keep = set(in_shard({p.stem for p in files}, *args.shard))
    files = [p for p in files if p.stem in keep]
    (args.out / 'inputs.txt').write_text(''.join(f'{p}\n' for p in files))
    # The verdict is repeated_span, read from the plan; review feeds only quality cells, and it
    # took most of CI's slowest shard (modelled threads in hidden-line removal).
    with ThreadPoolExecutor(4) as executor:
        results = dict(executor.map(lambda p: quality.one(str(p), args.binary, str(args.out),
                                                          timeout=args.timeout, review=False), files))
    (args.out / 'baseline.json').write_text(json.dumps(results, indent=1, sort_keys=True) + '\n')
    quality.table(results)
    return int(any('error' in p or p['repeated_span'] for p in results.values()))


if __name__ == '__main__':
    raise SystemExit(main())
