#!/usr/bin/env python3
"""Refuse release publication unless the exact tag commit passed full push-to-main CI.

Uses the read-only GitHub API through gh. Overall success alone is insufficient: historical
release-metadata runs could skip tests. Check every full-tier job in one successful attempt.
Run this guard from the workflow revision, not the tag, so old tags cannot omit the guard.
"""
import argparse
import json
import os
import subprocess
from urllib.parse import quote

REQUIRED_JOBS = {
    'changes', 'lint', 'build', 'binary', 'test (harness)',
    'acceptance (quality-gate)', 'acceptance (budgets)', 'Browser WASM and native parity',
    *(f'test ({k}/10)' for k in range(1, 11)),
    *(f'acceptance (quality-pins-{k}-4)' for k in range(1, 5)),
}


def api(endpoint):
    return json.loads(subprocess.check_output(
        ['gh', 'api', '--paginate', '--slurp', endpoint], text=True))


def check(repo, tag, fetch=api):
    ref = quote(f'refs/tags/{tag}', safe='')
    commit = fetch(f'repos/{repo}/commits/{ref}')[0]['sha']
    pages = fetch(f'repos/{repo}/actions/workflows/ci.yml/runs'
                  f'?head_sha={commit}&branch=main&event=push&status=success&per_page=100')
    for page in pages:
        for run in page['workflow_runs']:
            if (run['head_sha'] != commit or run['head_branch'] != 'main'
                    or run['event'] != 'push' or run['status'] != 'completed'
                    or run['conclusion'] != 'success'):
                continue
            jobs = fetch(f'repos/{repo}/actions/runs/{run["id"]}'
                         f'/attempts/{run["run_attempt"]}/jobs?per_page=100')
            passed = {job['name'] for page in jobs for job in page['jobs']
                      if job['status'] == 'completed' and job['conclusion'] == 'success'}
            if REQUIRED_JOBS <= passed:
                print(f'{tag} ({commit}): full main CI passed, run {run["id"]}')
                return
    raise ValueError(f'{tag} ({commit}): no successful full push-to-main CI attempt; '
                     'all ten test shards, harness and acceptance jobs must pass before publishing')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default=os.environ.get('GITHUB_REPOSITORY'), required=False)
    parser.add_argument('--tag', required=True)
    args = parser.parse_args()
    if not args.repo:
        parser.error('give --repo or set GITHUB_REPOSITORY')
    try:
        check(args.repo, args.tag)
    except (ValueError, KeyError, subprocess.CalledProcessError) as error:
        raise SystemExit(f'::error::{error}') from error


if __name__ == '__main__':
    main()
