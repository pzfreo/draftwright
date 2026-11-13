import unittest

from record_known_pmi import reviewed


class ReviewedChanges(unittest.TestCase):
    def setUp(self):
        self.entry = dict(file='part.stp', record='datum:1', port='datum:#1',
                          field='drawing', python=None, rust=None,
                          verdict='rust-correct', evidence='Source datum is drawn.')

    def test_stale_before_refuses_the_entire_change(self):
        stale = dict(self.entry, rust={'outcome': 'refused'})
        with self.assertRaisesRegex(ValueError, 'expected entry differs'):
            reviewed([self.entry], [dict(before=stale, after=None, reason='Reviewed')])

    def test_replacement_preserves_exact_evidence_and_reason(self):
        after = dict(self.entry, rust={'drawn': ['J']})
        result = reviewed([self.entry], [dict(before=self.entry, after=after,
                                            reason='CAD frame permits the datum.')])
        self.assertEqual(result[0]['rust'], {'drawn': ['J']})
        self.assertEqual(result[0]['evidence'],
                         'Source datum is drawn. CAD frame permits the datum.')
        self.assertEqual(self.entry['rust'], None)

    def test_unreviewed_or_duplicate_changes_are_refused(self):
        change = dict(before=self.entry, after=None, reason='')
        with self.assertRaisesRegex(ValueError, 'nonempty review reason'):
            reviewed([self.entry], [change])
        change['reason'] = 'Obsolete: both drawings now agree.'
        with self.assertRaisesRegex(ValueError, 'duplicate change'):
            reviewed([self.entry], [change, change])


if __name__ == '__main__':
    unittest.main()
