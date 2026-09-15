import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from radar.core import classify, canonical_url, History, SCORES
from radar.intake import manual_records, get_public
from radar.reporting import fresh, write_csv
from run_daily_radar import run

FIXTURE = Path(__file__).parent/'fixtures/radar_samples.json'


class RadarTests(unittest.TestCase):
    def setUp(self):
        self.samples = json.loads(FIXTURE.read_text(encoding='utf-8'))

    def test_qualification_and_false_positives(self):
        lead = classify(self.samples[0])
        self.assertEqual(set(lead['category'].strip(';').split(';')), {'money','pain','uk','ledgerdrop'})
        for key in SCORES + ['total_score']:
            self.assertGreaterEqual(lead[key], 0)
            self.assertLessEqual(lead[key], 5)
        for sample in self.samples[2:6]:
            self.assertNotIn('money', classify(sample)['category'])
            self.assertNotIn('ledgerdrop', classify(sample)['category'])
        for change in ({'company':''}, {'website':''}, {'country':'US', 'text':'We are a small agency in the US. We manually process invoices every week. We need help with automation.'}):
            self.assertNotIn('uk', classify(dict(self.samples[0], **change))['category'].split(';'))

    def test_persistence_duplicate_update_and_owner_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'history.sqlite3'
            first = classify(self.samples[0])
            h = History(p)
            self.assertEqual(h.track(first, '2026-09-01')['status'], 'NEW')
            self.assertEqual(h.track(first, '2026-09-01')['status'], 'NEW')
            self.assertEqual(len(h.current), 1)
            h.close()
            h = History(p)
            self.assertEqual(h.track(first, '2026-09-02')['status'], 'SEEN')
            updated = classify(dict(self.samples[0], text=self.samples[0]['text']+' We are no longer looking; already hired.'))
            changed = h.track(updated, '2026-09-02')
            self.assertEqual(changed['status'], 'UPDATED')
            self.assertTrue(changed['important_update'])
            self.assertEqual(changed['first_seen_at'], '2026-09-01')
            h.close()

    def test_cross_url_text_duplicate_and_distinct_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = History(Path(tmp)/'h.db')
            h.track(classify(self.samples[0]), 'today')
            h.track(classify(dict(self.samples[0], url='https://example.com/other')), 'today')
            self.assertEqual(len(h.current), 1)
            h.track(classify(dict(self.samples[0], url='https://example.com/design', title='Need a designer to fix website', text='We need a designer to fix our website layout. Our budget is £200.')), 'today')
            self.assertEqual(len(h.current), 2)
            h.close()

    def test_full_flow_windows_unicode_csv_markdown_twice(self):
        with tempfile.TemporaryDirectory(prefix='radar space ') as tmp:
            root = Path(tmp)/'中文 目錄'
            rows = run(root, True, [FIXTURE])
            self.assertEqual(len(rows), 6)
            expected = ['uk_leads.csv','uk_leads_top.md','money_radar.csv','money_radar_top5.md','pain_radar.csv','pain_radar_top5.md','ledgerdrop_validation.csv','daily_report.md']
            for name in expected:
                self.assertTrue((root/'output'/name).exists(), name)
            csvpath = root/'output/money_radar.csv'
            self.assertTrue(csvpath.read_bytes().startswith(b'\xef\xbb\xbf'))
            with csvpath.open(encoding='utf-8-sig', newline='') as f:
                money = list(csv.DictReader(f))
            self.assertIn('£500', money[0]['text'])
            report = (root/'output/daily_report.md').read_text(encoding='utf-8')
            self.assertIn('## 🎯 Recommended Action', report)
            self.assertIn('## 🇬🇧 UK Lead Radar Top 5', report)
            again = run(root, True, [FIXTURE])
            self.assertTrue(all(r['status']=='SEEN' for r in again))
            self.assertIn('今日無新增', (root/'output/daily_report.md').read_text(encoding='utf-8'))

    def test_empty_bad_import_and_network_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = root/'bad.json'
            bad.write_text('{broken', encoding='utf-8')
            with patch('radar.intake.HackerNewsCollector.fetch_and_normalize', side_effect=TimeoutError), patch('radar.intake.time.sleep'), patch('run_daily_radar.collect_public_sources', return_value=([], ['Additional sources: empty'])):
                self.assertEqual(run(root, False, [bad]), [])
            report = (root/'output/daily_report.md').read_text(encoding='utf-8')
            self.assertIn('FAILED', report)
            self.assertTrue((root/'logs/radar.log').exists())

    def test_manual_text_and_pending_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'input').mkdir()
            text = 'url: https://x.com/test/status/1\nauthor: Test\n'+self.samples[0]['text']
            (root/'input/manual_text.txt').write_text(text, encoding='utf-8')
            (root/'input/manual_urls.txt').write_text('https://twitter.com/test/status/1?s=20\nhttps://reddit.com/r/test/2\nnot-a-url', encoding='utf-8')
            rows = run(root, True)
            self.assertEqual(len(rows), 1)
            with (root/'output/pending_intake.csv').open(encoding='utf-8-sig') as f:
                self.assertEqual(len(list(csv.DictReader(f))), 2)

    def test_url_normalization_and_public_guard(self):
        self.assertEqual(canonical_url('https://twitter.com/a/status/1?utm_source=x&s=4'), 'https://x.com/a/status/1')
        self.assertEqual(canonical_url('https://name:password@example.com'), '')
        with self.assertRaises(ValueError):
            get_public('https://127.0.0.1/test')

    def test_unknown_workflow_not_validation(self):
        r = classify({'text':'I am frustrated with software and love PDF tools, but have nothing else to share.'})
        self.assertNotIn('ledgerdrop', r['category'])

    def test_parent_news_money_is_not_buyer_budget(self):
        r = classify({'source':'hackernews', 'title':'Temporal raises $550M', 'text':'I think scale is less important. There is no alternative to hiring people to automate workflows.'})
        self.assertNotIn('money', r['category'])
        self.assertEqual(r['payment_signal'], 'UNKNOWN')
        r = classify({'text':'If I need help with website automation I generally write it myself. I am happy with my current software.'})
        self.assertNotIn('money', r['category'])

    def test_old_signals_are_not_daily_actions(self):
        r = dict(classify(self.samples[0]), status='NEW', published_at='2001-01-01T00:00:00Z')
        self.assertFalse(fresh(r))

    def test_budget_change_is_important_and_formula_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            h = History(root/'history.db')
            h.track(classify(self.samples[0]), 'today')
            h.close()
            h = History(root/'history.db')
            row = h.track(classify(dict(self.samples[0], text=self.samples[0]['text'].replace('£500','£900'))), 'tomorrow')
            self.assertTrue(row['important_update'])
            h.close()
            write_csv(root/'safe.csv', [{'text':'=HYPERLINK("https://example.com")'}], ['text'])
            self.assertIn("'=HYPERLINK", (root/'safe.csv').read_text(encoding='utf-8-sig'))


if __name__ == '__main__':
    unittest.main()
