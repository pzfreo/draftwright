"""Release guard regression checks: no external calls or publication."""
import copy
from pathlib import Path
import unittest

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
        if '/attempts/' in endpoint:
            # Paginated results, including jobs beyond the first page.
            return [{'jobs': self.jobs[:5]}, {'jobs': self.jobs[5:]}]
        return [{'workflow_runs': []}, {'workflow_runs': [self.run]}]

    def test_exact_commit_all_jobs_and_paginated_attempt(self):
        guard.check('owner/repo', 'v1.0', self.fetch)
        self.assertTrue(self.calls[0].endswith('/commits/refs%2Ftags%2Fv1.0'))
        self.assertIn('head_sha=abc123&branch=main&event=push', self.calls[1])
        self.assertIn('/attempts/2/jobs?', self.calls[2])

    def test_green_pr_dispatch_wrong_commit_branch_or_incomplete_run_refused(self):
        original = copy.copy(self.run)
        for key, value in [('event', 'pull_request'), ('event', 'workflow_dispatch'),
                           ('head_sha', 'other'), ('head_branch', 'feature'),
                           ('status', 'in_progress'), ('conclusion', 'failure')]:
            with self.subTest(key=key, value=value):
                self.run = {**original, key: value}
                with self.assertRaisesRegex(ValueError, 'no successful full'):
                    guard.check('owner/repo', 'v1.0', self.fetch)

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
            self.assertLess(guarded.index('check_release_ci.py --tag "$TAG"'), guarded.index(write))


if __name__ == '__main__':
    unittest.main()
