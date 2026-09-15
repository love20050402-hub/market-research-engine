import csv
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime

from radar.core import History, JsonHistory, classify
from radar.github_intake import collect_issues, parse_issue
from radar.public_sources import parse_wwr, parse_contracts, collect_public_sources
from run_daily_radar import run, BASE

FIXTURE = Path(__file__).parent/'fixtures/radar_samples.json'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CloudRadarTests(unittest.TestCase):
    def setUp(self):
        self.sample = json.loads(FIXTURE.read_text(encoding='utf-8'))[0]

    def test_jsonl_matches_sqlite_new_seen_updated(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = [(History, Path(tmp)/'h.sqlite3'), (JsonHistory, Path(tmp)/'h.jsonl')]
            results = []
            for cls, path in paths:
                states = []
                for text in [self.sample['text'], self.sample['text'], self.sample['text'].replace('£500','£900')]:
                    history = cls(path)
                    row = history.track(classify(dict(self.sample, text=text)), 'today')
                    states.append((row['status'], row['important_update'], row['first_seen_at']))
                    history.close()
                results.append(states)
            self.assertEqual(results[0], results[1])
            self.assertEqual([x[0] for x in results[0]], ['NEW', 'SEEN', 'UPDATED'])

    def test_preview_preserves_both_histories_and_daily_report(self):
        for jsonl in [False, True]:
            with self.subTest(jsonl=jsonl), tempfile.TemporaryDirectory(prefix='cloud 空白 ') as tmp:
                root = Path(tmp)
                state = root/'data/github_history.jsonl' if jsonl else None
                run(root, True, [FIXTURE], state_file=state)
                history = state or root/'data/opportunity_history.sqlite3'
                daily = root/'output/daily_report.md'
                before = (digest(history), digest(daily))
                rows = run(root, True, [FIXTURE], fresh_preview=True, state_file=state)
                self.assertEqual(before, (digest(history), digest(daily)))
                self.assertTrue(all(r['status'] == 'SEEN' for r in rows))
                preview = (root/'output/preview/daily_report.md').read_text(encoding='utf-8')
                self.assertIn('FRESH PREVIEW', preview)
                self.assertIn('Need invoice automation help', preview)
                self.assertIn('SEEN', preview)

    def test_preview_new_root_creates_no_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run(root, True, [FIXTURE], fresh_preview=True)
            self.assertFalse((root/'data/opportunity_history.sqlite3').exists())
            run(root, True, [FIXTURE], fresh_preview=True, state_file=root/'data/h.jsonl')
            self.assertFalse((root/'data/h.jsonl').exists())

    def test_guard_production_and_fixture_import(self):
        with patch.dict(os.environ, {'RADAR_TEST_MODE':'1'}):
            with self.assertRaises(ValueError):
                run(BASE, True)
            with self.assertRaises(ValueError):
                History(BASE/'data/opportunity_history.sqlite3')
            with self.assertRaises(ValueError):
                JsonHistory(BASE/'data/github_history.jsonl')
        with patch.dict(os.environ, {'RADAR_TEST_MODE':'0'}):
            with self.assertRaises(ValueError):
                run(BASE, True, [FIXTURE])

    def test_malformed_history_never_reset_and_lock_released(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'h.jsonl'
            p.write_text('{bad', encoding='utf-8')
            before = digest(p)
            with self.assertRaises(ValueError):
                JsonHistory(p)
            self.assertEqual(digest(p), before)
            self.assertFalse(p.with_suffix('.lock').exists())

    def test_jsonl_failed_report_rolls_back_and_concurrent_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root/'h.jsonl'
            run(root, True, [FIXTURE], state_file=p)
            before = digest(p)
            with patch('run_daily_radar.reports', side_effect=OSError):
                with self.assertRaises(OSError):
                    run(root, True, [FIXTURE], state_file=p)
            self.assertEqual(before, digest(p))
            h = JsonHistory(p)
            with self.assertRaises(FileExistsError):
                JsonHistory(p)
            h.close(False)

    def issue(self, **changes):
        return dict({'title':'Radar Intake', 'body':'URL: https://x.com/user/status/1\nCOUNTRY: UK\nTEXT:\n'+self.sample['text'], 'user':{'login':'owner'}, 'author_association':'OWNER', 'html_url':'https://github.com/owner/repo/issues/1'}, **changes)

    def test_issue_trust_text_and_untrusted_payload(self):
        row = parse_issue(self.issue(), 'owner')
        self.assertEqual(row['country'], 'UK')
        self.assertEqual(row['text'], self.sample['text'])
        self.assertEqual(row['published_at'], 'UNKNOWN')
        self.assertIsNone(parse_issue(self.issue(user={'login':'outsider'}, author_association='NONE'), 'owner'))
        self.assertIsNone(parse_issue(self.issue(pull_request={}), 'owner'))
        with self.assertRaises(ValueError):
            parse_issue(self.issue(body='URL: https://x.com/user/status/1'), 'owner')
        text = self.issue(body='URL: https://x.com/user/status/1\nTEXT:\nI need help with website automation. $(touch /tmp/pwned)')
        self.assertIn('$(touch', parse_issue(text, 'owner')['text'])

    def test_issue_failure_and_malformed_isolation(self):
        def opener(request, timeout):
            self.assertTrue(request.full_url.startswith('https://api.github.com/repos/owner/repo/issues?'))
            return io.BytesIO(json.dumps([self.issue(), self.issue(body='bad')]).encode())
        rows, pending, stats = collect_issues('owner/repo', logging.getLogger('test'), opener=opener)
        self.assertEqual((len(rows), len(pending)), (1, 1))
        def failed(*a, **kw):
            raise TimeoutError()
        self.assertIn('FAILED', collect_issues('owner/repo', logging.getLogger('test'), opener=failed)[2][0])

    def test_rss_contract_only_and_company_evidence(self):
        date = format_datetime(datetime.now(timezone.utc))
        xml = f'''<rss><channel><item><title>Example: Developer</title><type>Contract</type><country>United Kingdom</country><pubDate>{date}</pubDate><link>https://weworkremotely.com/remote-jobs/example</link><description><![CDATA[<strong>URL:</strong> <a href="https://example.com">Example</a><p>We are a small agency in the UK. We need a developer to automate our spreadsheet workflow.</p>]]></description></item></channel></rss>'''
        rows = parse_wwr(xml)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['website'], 'https://example.com')
        self.assertIn('uk', classify(rows[0])['category'])
        self.assertEqual(parse_wwr(xml.replace('<type>Contract','<type>Full-Time')), [])
        with self.assertRaises(ValueError):
            parse_wwr('<!DOCTYPE rss><rss/>')

    def tender(self):
        return {'releases':[{'date':datetime.now(timezone.utc).isoformat(), 'tag':['tender'], 'buyer':{'id':'1'}, 'parties':[{'id':'1','name':'Example UK public buyer','address':{'countryName':'United Kingdom'},'details':{'url':'https://example.gov.uk'}}], 'tender':{'title':'Website data processing', 'description':'Required website data processing and spreadsheet cleanup for weekly reports.', 'status':'active','suitability':{'sme':True},'value':{'amount':15000,'currency':'GBP'},'tenderPeriod':{'endDate':(datetime.now(timezone.utc)+timedelta(days=10)).isoformat()},'documents':[{'documentType':'tenderNotice','url':'https://www.contractsfinder.service.gov.uk/Notice/example'}]}}]}

    def test_uk_tender_evidence_scope_and_deadline_gates(self):
        data = self.tender()
        rows = parse_contracts(json.dumps(data))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['category'], 'uk')
        self.assertIn('not claimed', rows[0]['buyer_type'])
        for field, value in [('status','complete'), ('value',{'amount':500000,'currency':'GBP'}), ('documents',[])]:
            changed = self.tender()
            changed['releases'][0]['tender'][field] = value
            self.assertEqual(parse_contracts(json.dumps(changed)), [])
        changed = self.tender()
        changed['releases'][0]['tender']['tenderPeriod']['endDate']='2001-01-01T00:00:00Z'
        self.assertEqual(parse_contracts(json.dumps(changed)), [])

    def test_public_source_timeout_malformed_empty(self):
        for result in [TimeoutError(), ('garbage','text/plain'), ('{"releases":[]}','application/json')]:
            with patch('radar.public_sources.get_public', side_effect=result if isinstance(result, Exception) else None, return_value=result):
                rows, stats = collect_public_sources(logging.getLogger('test'))
            self.assertEqual(rows, [])
            self.assertEqual(len(stats), 2)

    def test_uk_top_requires_evidence_url(self):
        self.assertNotIn('uk', classify(dict(self.sample, url='', evidence_url=''))['category'])


if __name__ == '__main__':
    unittest.main()
