import csv
import logging
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from radar.core import classify, quality_gate
from radar.intake import HN_QUERIES, collect_hn, hn_demand_text
from radar.reporting import reports, rejection_reasons, near_misses, action_queue


class SourceQualityTests(unittest.TestCase):
    def test_body_demand_not_parent_title_or_manual_keyword(self):
        for text in ['The Unix manual says convert and copy a file.', 'Nobody is willing to pay for that.',
                     'You do not need someone to reverse engineer hardware.', 'If I need someone to help, I will ask.',
                     'I think you need someone to teach you Emacs software.', 'I am willing to pay extra for cotton shirts.',
                     '"I need help with my homework software". Put that in your prompt.',
                     'I built this app, give it a try!', 'SEEKING WORK: I need help with finding clients.']:
            self.assertFalse(hn_demand_text({'title':'I am willing to pay', 'text':text}), text)
        for text in ['We need someone to fix our script that does not export invoices.',
                     'I am looking for a freelancer to clean our data.', 'Is there a tool to export this data?',
                     'I am manually doing invoice entry every week.', 'We need help with a paid project.']:
            self.assertTrue(hn_demand_text({'text':text}), text)

    def test_bounded_queries_recent_dates_noise_and_timeout_isolation(self):
        now = datetime.now(timezone.utc)
        good = {'text':'We need someone to fix our spreadsheet export.', 'created_at':now.isoformat()}
        hits = [good, dict(good, text='Read the Unix manual.'), dict(good, created_at='invalid'),
                dict(good, created_at=(now-timedelta(days=31)).isoformat()),
                dict(good, created_at=(now+timedelta(days=1)).isoformat())]
        with patch('radar.intake.HackerNewsCollector') as cls, patch('radar.intake.time.sleep'):
            cls.return_value.fetch_and_normalize.side_effect = [TimeoutError()] + [hits]*(len(HN_QUERIES)-1)
            rows, stats = collect_hn(logging.getLogger('test'))
        self.assertEqual(len(rows), len(HN_QUERIES)-1)
        self.assertEqual(len(cls.call_args_list), 8)
        self.assertIn('FAILED', stats[0])
        self.assertIn('1 demand posts / 2 recent hits; source noise removed: 1', stats[1])
        self.assertNotIn('manual', HN_QUERIES)
        for call in cls.call_args_list:
            self.assertEqual(call.args[0]['hits_per_page'], 30)
            self.assertIn(call.args[0]['query'], HN_QUERIES)

    def test_primary_totals_exclusive_and_near_miss_never_actionable(self):
        now = datetime.now(timezone.utc).isoformat()
        rows = []
        texts = ["I've noticed the same failure mode across coding agents. They leave unused code after changing direction. How are others handling this?",
                 'I need to be able to enforce structured output in my workflow. I could use a script but have not tried it.',
                 'Manual memory management is an interesting technical topic for programming languages.',
                 'We need someone for enterprise migration and Salesforce integration. Our budget is £500.']
        for i, text in enumerate(texts):
            row = dict(classify({'title':'Topic', 'text':text,'url':f'https://example.com/{i}'}), status='SEEN', first_seen_at=now, important_update=False)
            if i < 2:
                row['total_score'] = 2.2  # Existing stored ranking can be higher than evidence extraction.
            rows.append(row)
        self.assertEqual(rejection_reasons(rows[2])[0], 'NO_CONCRETE_BUYER_OR_USER_NEED')
        self.assertEqual(rejection_reasons(rows[3])[0], 'SCOPE_TOO_LARGE')
        self.assertEqual(len(near_misses(rows)), 2)
        self.assertTrue(all(not quality_gate(r) for r in rows))
        self.assertEqual(action_queue(rows, []), [])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports(root, rows, [], [], now)
            text = (root/'filter_summary.md').read_text(encoding='utf-8')
            self.assertIn('TOTAL: 4', text)
            with (root/'opportunities.csv').open(encoding='utf-8-sig') as stream:
                exported = list(csv.DictReader(stream))
            self.assertEqual(len(exported), 4)
            self.assertTrue(all(r['primary_rejection_reason'] for r in exported))
            self.assertIn('LOW CONFIDENCE / NOT ACTIONABLE', (root/'near_misses.md').read_text(encoding='utf-8'))
            self.assertIn('NO ACTION REQUIRED TODAY', (root/'action_queue.md').read_text(encoding='utf-8'))
        many = [dict(rows[0], url=f'https://example.com/{i}') for i in range(10)]
        self.assertEqual(len(near_misses(many)), 5)


if __name__ == '__main__':
    unittest.main()
