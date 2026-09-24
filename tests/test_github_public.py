import io
import json
import logging
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from radar.github_public import collect_public_issues, normalize_issue, QUERIES
from radar.core import classify, quality_gate
from radar.reporting import action_queue
from run_daily_radar import run


class PublicIssueTests(unittest.TestCase):
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)

    def issue(self, **changes):
        return dict(dict(state='open', user={'login': 'real-user', 'type': 'User'},
                         created_at='2026-09-20T00:00:00Z', title='Invoice export workflow',
                         body='We manually process 200 invoices every week in Excel; it takes 5 hours.',
                         html_url='https://github.com/org/project/issues/1'), **changes)

    def test_real_pain_not_automatic_buyer(self):
        row = dict(classify(normalize_issue(self.issue(), self.now)), status='NEW')
        self.assertEqual(row['source'], 'github_issues')
        self.assertEqual(row['source_type'], 'public_issue')
        self.assertTrue(quality_gate(row, 'pain'))
        self.assertNotIn('money', row['category'])
        self.assertEqual(action_queue([row], []), [])
        row = classify(normalize_issue(self.issue(body=self.issue()['body']+" I'm looking for recommendations on accounting software."), self.now))
        self.assertNotIn('money', row['category'])
        self.assertFalse(quality_gate(row, 'money'))
        paid = classify(normalize_issue(self.issue(body=self.issue()['body']+' We need someone to automate invoice extraction. Our budget is $500.'), self.now))
        self.assertIn('money', paid['category'])

    def test_noise_old_closed_bots_generated(self):
        for changes in [dict(body='Please add an invoice export feature.'),
                        dict(user={'login': 'robot[bot]', 'type': 'Bot'}),
                        dict(body='Automatically generated issue. '+self.issue()['body']),
                        dict(body='```'+self.issue()['body']+'```'),
                        dict(body='> '+self.issue()['body']),
                        dict(body='An example: We manually process invoices every week. Our research asks how workflow tools work.'),
                        dict(body=self.issue()['body']+' Contact us for enterprise services.'),
                        dict(created_at='2026-08-01T00:00:00Z'), dict(state='closed'),
                        dict(pull_request={}), dict(body='Hire me. '+self.issue()['body'])]:
            with self.subTest(changes=changes):
                self.assertIsNone(normalize_issue(self.issue(**changes), self.now))

    @patch.dict('os.environ', {'GITHUB_TOKEN': 'test-token'})
    def test_bounds_dedupe_queries_and_health(self):
        calls = []
        def opener(request, timeout):
            calls.append(request)
            response = io.BytesIO(json.dumps({'items': [self.issue()]*8}).encode())
            response.headers = {}
            return response
        rows, stats = collect_public_issues(logging.getLogger(), opener=opener, now=self.now)
        self.assertEqual(len(calls), len(QUERIES))
        self.assertEqual(len(rows), 1)
        self.assertIn('raw=40; eligible=1; failures=0', stats[-1])
        for request in calls:
            self.assertIn('is%3Aopen', request.full_url)
            self.assertIn('is%3Apublic', request.full_url)
            self.assertIn('created%3A%3E%3D2026-08-25', request.full_url)
            self.assertEqual(request.get_header('Authorization'), 'Bearer test-token')

    @patch.dict('os.environ', {'GITHUB_TOKEN': 'test-token'})
    def test_failure_degrades_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch('radar.github_public.build_opener') as opener, \
                patch('run_daily_radar.collect_hn', return_value=([], [])), \
                patch('run_daily_radar.collect_public_sources', return_value=([], [])):
            opener.return_value.open.side_effect = TimeoutError()
            self.assertEqual(run(Path(tmp)), [])
            self.assertIn('DEGRADED', (Path(tmp)/'output/daily_report.md').read_text(encoding='utf8'))
            self.assertIn('failures=5', (Path(tmp)/'output/filter_summary.md').read_text(encoding='utf8'))
