"""Release guard regression checks: no external calls or publication."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

import check_release_ci as guard


class ReleaseCI(unittest.TestCase):
    def setUp(self):
        self.run = dict(id=42, run_attempt=2, head_sha='abc123', head_branch='main',
                        event='push', status='completed', conclusion='success')
        self.jobs = [dict(name=name, status='completed', conclusion='success')
                     for name in sorted(guard.REQUIRED_JOBS)]
        self.calls = []

    def fetch(self, endpoint):
        self.calls.append(endpoint)
        if '/commits/' in endpoint:
            return [{'sha': 'abc123'}]
        if '/jobs?' in endpoint:
            # Paginated results, including jobs beyond the first page.
            return [{'jobs': self.jobs[:5]}, {'jobs': self.jobs[5:]}]
        return [{'workflow_runs': []}, {'workflow_runs': [self.run]}]

    def test_api_calls_have_a_timeout_so_network_stalls_cannot_wait_forever(self):
        with patch.object(guard.subprocess, 'check_output', return_value='[]') as request:
            self.assertEqual(guard.api('repos/owner/repo/actions/runs'), [])
        self.assertEqual(request.call_args.kwargs['timeout'], 60)

    def test_exact_commit_all_latest_jobs_and_pagination(self):
        guard.check('owner/repo', 'v1.0', self.fetch)
        self.assertTrue(self.calls[0].endswith('/commits/refs%2Ftags%2Fv1.0'))
        self.assertIn('head_sha=abc123&branch=main&event=push', self.calls[1])
        self.assertIn('/runs/42/jobs?filter=latest&', self.calls[2])

    def test_green_pr_dispatch_wrong_commit_branch_or_incomplete_run_refused(self):
        original = copy.copy(self.run)
        for key, value in [('event', 'pull_request'), ('event', 'workflow_dispatch'),
                           ('head_sha', 'other'), ('head_branch', 'feature'),
                           ('status', 'cancelled'), ('conclusion', 'failure')]:
            with self.subTest(key=key, value=value):
                self.run = {**original, key: value}
                with self.assertRaisesRegex(ValueError, 'no successful full'):
                    guard.check('owner/repo', 'v1.0', self.fetch)

    def test_rerun_failed_jobs_includes_successes_carried_from_first_attempt(self):
        # Attempt 1 passed 21 jobs; attempt 2 reran only the failed shard. The latest-jobs
        # endpoint returns both, while attempts/2/jobs would return only that shard.
        self.jobs = [{**job, 'run_attempt': 2 if job['name'] == 'test (1/10)' else 1}
                     for job in self.jobs]
        def history(endpoint):
            if '/attempts/' in endpoint:
                return [{'jobs': [j for j in self.jobs if j['run_attempt'] == 2]}]
            return self.fetch(endpoint)
        guard.check('owner/repo', 'v1.0', history)
        self.assertIn('/jobs?filter=latest&', self.calls[-1])

    def test_waits_through_queued_and_running_main_then_accepts_success(self):
        states = iter(['queued', 'in_progress', 'completed'])
        elapsed, sleeps = [0], []
        def fetch(endpoint):
            if '/ci.yml/runs?' in endpoint:
                self.assertNotIn('status=success', endpoint)
                return [{'workflow_runs': [{**self.run, 'status': next(states)}]}]
            return self.fetch(endpoint)
        def sleep(seconds):
            sleeps.append(seconds)
            elapsed[0] += seconds
        guard.check('owner/repo', 'v1.0', fetch, wait_seconds=120, poll_seconds=30,
                    sleep=sleep, monotonic=lambda: elapsed[0])
        self.assertEqual(sleeps, [30, 30])

    def test_wait_is_bounded_and_failure_does_not_wait(self):
        elapsed, sleeps = [0], []
        self.run.update(status='in_progress', conclusion=None)
        def sleep(seconds):
            sleeps.append(seconds)
            elapsed[0] += seconds
        with self.assertRaisesRegex(ValueError, 'timed out.*publication refused'):
            guard.check('owner/repo', 'v1.0', self.fetch, wait_seconds=45 * 60,
                        sleep=sleep, monotonic=lambda: elapsed[0])
        self.assertEqual(sum(sleeps), 45 * 60)
        self.run.update(status='completed', conclusion='failure')
        sleeps.clear()
        with self.assertRaisesRegex(ValueError, 'no successful full'):
            guard.check('owner/repo', 'v1.0', self.fetch, sleep=sleep)
        self.assertEqual(sleeps, [])

    def test_each_missing_skipped_failed_or_pending_job_refuses_publication(self):
        original = copy.deepcopy(self.jobs)
        for name in guard.REQUIRED_JOBS:
            for status, conclusion in [('completed', 'skipped'), ('completed', 'failure'),
                                       ('in_progress', None)]:
                self.jobs = [{**job, 'status': status, 'conclusion': conclusion}
                             if job['name'] == name else job for job in original]
                with self.subTest(name=name, conclusion=conclusion):
                    with self.assertRaises(ValueError):
                        guard.check('owner/repo', 'v1.0', self.fetch)
            self.jobs = [job for job in original if job['name'] != name]
            with self.assertRaises(ValueError):
                guard.check('owner/repo', 'v1.0', self.fetch)

    def test_every_publication_workflow_checks_before_writing(self):
        root = Path(__file__).resolve().parents[1] / '.github/workflows'
        for file, write in [('release.yml', 'gh release upload'), ('cli.yml', 'gh release upload'),
                            ('publish.yml', 'uses: pypa/gh-action-pypi-publish@'),
                            ('crates.yml', '- name: Mint a crates.io token')]:
            text = (root / file).read_text()
            guarded = text[text.index('          path: .release-ci-guard'):]
            self.assertIn('ref: ${{ github.workflow_sha }}', text)
            self.assertIn('actions: read', text)
            self.assertNotIn('uses: ./.github/workflows/release-checks.yml', text)
            command = 'check_release_ci.py --tag "$TAG" --require-release-checks'
            self.assertLess(guarded.index(command), guarded.index(write))


class SharedReleaseGate(unittest.TestCase):
    def setUp(self):
        self.run = dict(id=42, run_attempt=1, path=guard.RELEASE_WORKFLOW,
                        head_sha='abc123', head_branch='v1.0', event='release',
                        display_title='Release checks for v1.0 (release 17)',
                        status='completed', conclusion='success')
        self.job = dict(name='release-only', status='completed', conclusion='success')
        self.calls = []

    def fetch(self, endpoint):
        self.calls.append(endpoint)
        if '/releases/tags/' in endpoint:
            return [{'id': 17}]
        if '/workflows/' in endpoint:
            # The latest attempt can change while the four publishers are waiting.
            other = {**self.run, 'id': 41, 'display_title': 'Release checks for v1.0 (release 16)'}
            return [{'workflow_runs': [other]}, {'workflow_runs': [{**self.run, 'run_attempt': 2}]}]
        if '/jobs?' in endpoint:
            return [{'jobs': [self.job]}]
        if '/commits/' in endpoint:
            return [{'sha': 'abc123'}]
        return [self.run]

    def check(self, **kwargs):
        guard.check_release_gate('owner/repo', 'v1.0', 'abc123', fetch=self.fetch, **kwargs)

    def test_publishing_uses_the_same_first_attempt_even_after_a_rerun(self):
        self.check(release_id=17)
        self.assertIn('head_sha=abc123&event=release', self.calls[0])
        self.assertIn('/runs/42/attempts/1', self.calls[1])
        self.run.update(conclusion='failure')
        with self.assertRaisesRegex(ValueError, 'gate 42/1 failed'):
            self.check(release_id=17)

    def test_dispatch_can_name_a_shared_attempt_for_the_exact_tag_and_commit(self):
        self.run.update(event='workflow_dispatch', head_branch='main', run_attempt=2,
                        head_sha='dispatch-commit', display_title='Release checks for v1.0 at abc123')
        self.check(selected_run='42/2')
        self.assertIn('/runs/42/attempts/2', self.calls[1])
        for key, value in [('display_title', 'Release checks for v1.0 at wrong-commit'),
                           ('path', '.github/workflows/another.yml'), ('head_branch', 'feature')]:
            with self.subTest(key=key), patch.dict(self.run, {key: value}):
                with self.assertRaisesRegex(ValueError, 'is not for this release'):
                    self.check(selected_run='42/2')

    def test_queued_attempt_waits_and_a_skipped_gate_refuses_publication(self):
        self.run.update(status='queued', conclusion=None)
        waits = []
        def sleep(seconds):
            waits.append(seconds)
            self.run.update(status='completed', conclusion='success')
        self.check(release_id=17, sleep=sleep)
        self.assertEqual(waits, [30])
        self.job['conclusion'] = 'skipped'
        with self.assertRaisesRegex(ValueError, 'gate 42/1 failed'):
            self.check(release_id=17)

    def test_waits_for_the_release_run_to_be_registered(self):
        elapsed = [0]
        def fetch(endpoint):
            if '/workflows/' in endpoint and not elapsed[0]:
                return [{'workflow_runs': []}]
            return self.fetch(endpoint)
        def sleep(seconds):
            elapsed[0] += seconds
        guard.check_release_gate('owner/repo', 'v1.0', 'abc123', 17, fetch=fetch,
                                 sleep=sleep, monotonic=lambda: elapsed[0])
        self.assertEqual(elapsed[0], 30)

        elapsed[0] = 0
        with self.assertRaisesRegex(ValueError, 'timed out.*publication refused'):
            guard.check_release_gate('owner/repo', 'v1.0', 'abc123', 17, fetch=fetch,
                                     wait_seconds=0, monotonic=lambda: 0)

    def test_gate_refuses_a_tag_that_moves_after_testing(self):
        def fetch(endpoint):
            return [{'sha': 'moved'}] if '/commits/' in endpoint else self.fetch(endpoint)
        with self.assertRaisesRegex(ValueError, 'tag moved'):
            guard.check_release_gate('owner/repo', 'v1.0', 'abc123', 17, fetch=fetch)


if __name__ == '__main__':
    unittest.main()
