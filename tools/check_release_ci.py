#!/usr/bin/env python3
"""Refuse publication unless the exact tag commit passed main CI and, in CI, its release gate.

Uses the read-only GitHub API through gh. Overall success alone is insufficient: historical
release-metadata runs could skip tests. Check the latest result of every full-tier job,
including jobs carried forward when only failed jobs are rerun.
Run this guard from the workflow revision, not the tag, so old tags cannot omit the guard.
"""
import argparse
import json
import os
import re
import subprocess
import time
from urllib.parse import quote

REQUIRED_JOBS = {
    'changes', 'lint', 'build', 'binary', 'plan-cache (1)', 'plan-cache (2)', 'test (harness)',
    'acceptance (quality-gate)', 'acceptance (budgets)', 'Browser WASM and native parity',
    *(f'test ({k}/10)' for k in range(1, 11)),
    *(f'acceptance (quality-pins-{k}-4)' for k in range(1, 5)),
}
RELEASE_WORKFLOW = '.github/workflows/release-checks.yml'


def api(endpoint):
    return json.loads(subprocess.check_output(
        ['gh', 'api', '--paginate', '--slurp', endpoint], text=True, timeout=60))


def tagged_commit(repo, tag, fetch):
    ref = quote(f'refs/tags/{tag}', safe='')
    return fetch(f'repos/{repo}/commits/{ref}')[0]['sha']


def check(repo, tag, fetch=api, *, wait_seconds=45 * 60, poll_seconds=30,
          sleep=time.sleep, monotonic=time.monotonic):
    commit = tagged_commit(repo, tag, fetch)
    deadline = monotonic() + wait_seconds
    while True:
        # Include active runs: publication may start before main's full CI finishes.
        pages = fetch(f'repos/{repo}/actions/workflows/ci.yml/runs'
                      f'?head_sha={commit}&branch=main&event=push&per_page=100')
        active = False
        for page in pages:
            for run in page['workflow_runs']:
                if (run['head_sha'] != commit or run['head_branch'] != 'main'
                        or run['event'] != 'push'):
                    continue
                if run['status'] in ('queued', 'in_progress', 'waiting', 'pending', 'requested'):
                    active = True
                    continue
                if run['status'] != 'completed' or run['conclusion'] != 'success':
                    continue
                # filter=latest includes successful jobs carried over from earlier attempts.
                jobs = fetch(f'repos/{repo}/actions/runs/{run["id"]}'
                             '/jobs?filter=latest&per_page=100')
                passed = {job['name'] for page in jobs for job in page['jobs']
                          if job['status'] == 'completed' and job['conclusion'] == 'success'}
                if REQUIRED_JOBS <= passed:
                    print(f'{tag} ({commit}): full main CI passed, run {run["id"]}')
                    return commit
        if not active:
            raise ValueError(f'{tag} ({commit}): no successful full push-to-main CI run; '
                             'all ten test shards, harness and acceptance jobs must pass before publishing')
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise ValueError(f'{tag} ({commit}): timed out after {wait_seconds / 60:g} minutes '
                             'waiting for full main CI; publication refused')
        print(f'{tag} ({commit}): main CI is queued or in progress; waiting', flush=True)
        sleep(min(poll_seconds, remaining))


def check_release_gate(repo, tag, commit, release_id=None, selected_run='', fetch=api, *,
                       wait_seconds=90 * 60, poll_seconds=30, sleep=time.sleep,
                       monotonic=time.monotonic):
    """Use the release event's first attempt or one explicitly selected shared attempt."""
    if release_id is None:
        release_id = fetch(f'repos/{repo}/releases/tags/{quote(tag, safe="")}')[0]['id']
    release_title = f'Release checks for {tag} (release {release_id})'
    backfill_title = f'Release checks for {tag} at {commit}'

    run_id, attempt = None, 1
    if selected_run:
        match = re.fullmatch(r'([1-9][0-9]*)(?:/([1-9][0-9]*))?', selected_run)
        if not match:
            raise ValueError('release_checks_run must be a run ID or run ID/attempt')
        run_id = int(match[1])
        attempt = int(match[2]) if match[2] else 1

    def is_release(run):
        return (run['event'] == 'release' and run['head_sha'] == commit
                and run['display_title'] == release_title)

    runs_path = f'repos/{repo}/actions/runs'
    deadline = monotonic() + wait_seconds
    while True:
        if run_id is None:
            pages = fetch(f'repos/{repo}/actions/workflows/release-checks.yml/runs'
                          f'?head_sha={commit}&event=release&per_page=100')
            candidates = {run['id'] for page in pages for run in page['workflow_runs']
                          if is_release(run)}
            if len(candidates) > 1:
                raise ValueError(f'{tag}: multiple release gate runs found; publication refused')
            run_id = next(iter(candidates), None)
        if run_id is not None:
            endpoint = f'{runs_path}/{run_id}/attempts/{attempt}'
            run = fetch(endpoint)[0]
            is_backfill = (selected_run and run['event'] == 'workflow_dispatch'
                           and run['head_branch'] == 'main'
                           and run['display_title'] == backfill_title)
            if (run['id'] != run_id or run['run_attempt'] != attempt
                    or run['path'].partition('@')[0] != RELEASE_WORKFLOW
                    or not (is_release(run) or is_backfill)):
                raise ValueError(f'{tag} ({commit}): gate run {run_id}/{attempt} is not for this release')
            if run['status'] == 'completed':
                jobs = fetch(f'{endpoint}/jobs?per_page=100')
                passed = any(job['name'] == 'release-only' and job['status'] == 'completed'
                             and job['conclusion'] == 'success' for page in jobs for job in page['jobs'])
                if run['conclusion'] != 'success' or not passed:
                    raise ValueError(f'{tag}: release gate {run_id}/{attempt} failed; publication refused')
                if tagged_commit(repo, tag, fetch) != commit:
                    raise ValueError(f'{tag}: tag moved while waiting for the release gate; publication refused')
                print(f'{tag} ({commit}): shared release checks passed, run {run_id}/{attempt}')
                return
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise ValueError(f'{tag}: timed out waiting for the shared release gate; publication refused')
        print(f'{tag}: waiting for the shared release gate', flush=True)
        sleep(min(poll_seconds, remaining))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default=os.environ.get('GITHUB_REPOSITORY'), required=False)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--require-release-checks', action='store_true')
    args = parser.parse_args()
    if not args.repo:
        parser.error('give --repo or set GITHUB_REPOSITORY')
    try:
        commit = check(args.repo, args.tag)
        if args.require_release_checks:
            with open(os.environ['GITHUB_EVENT_PATH'], encoding='utf-8') as event_file:
                event = json.load(event_file)
            if os.environ['GITHUB_EVENT_NAME'] == 'release':
                if commit != os.environ['GITHUB_SHA']:
                    raise ValueError(f'{args.tag}: tag differs from the release event commit')
                check_release_gate(args.repo, args.tag, commit, release_id=event['release']['id'])
            else:
                check_release_gate(args.repo, args.tag, commit,
                                   selected_run=event['inputs'].get('release_checks_run', ''))
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit(f'::error::{error}') from error


if __name__ == '__main__':
    main()
