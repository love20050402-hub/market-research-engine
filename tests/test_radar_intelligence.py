import hashlib
import io
import json
import logging
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from radar.core import classify, History, JsonHistory, quality_gate
from radar.github_intake import collect_issues, parse_feedback
from radar.intelligence import enrich_history, pain_clusters, cash_candidate, FEEDBACK
from radar.reporting import action_queue, reports
from run_daily_radar import run

NOW = datetime.now(timezone.utc).isoformat()


def signal(author='alice', source='hackernews', **changes):
    raw = dict(source=source, author=author, url='https://news.ycombinator.com/item?id='+author,
               title='Invoice workflow help', published_at=NOW,
               text='We manually process 200 invoices every week in Excel; it takes 5 hours. We need someone to automate invoice extraction. Our budget is £500.')
    raw.update(changes)
    return dict(classify(raw), status='NEW', first_seen_at=NOW, important_update=False)


def feedback(status, url=None, stamp=NOW):
    return dict(url=url or signal()['url'], feedback=status, feedback_at=stamp,
                feedback_issue='https://github.com/owner/repo/issues/1')


class IntelligenceTests(unittest.TestCase):
    def test_trusted_feedback_all_states_and_closed_issue(self):
        issue = dict(title='Radar Feedback', body='URL: '+signal()['url']+'\nSTATUS: PAID', user={'login':'owner'},
                     updated_at=NOW, state='closed', html_url='https://github.com/owner/repo/issues/1')
        for status in FEEDBACK:
            self.assertEqual(parse_feedback(dict(issue, body=issue['body'].replace('PAID', status)), 'owner')['feedback'], status)
        self.assertIsNone(parse_feedback(dict(issue, user={'login':'stranger'}, author_association='NONE'), 'owner'))
        self.assertEqual(parse_feedback(dict(issue, user={'login':'member'}, author_association='MEMBER'), 'owner')['feedback'], 'PAID')
        self.assertIsNone(parse_feedback(dict(issue, pull_request={}), 'owner'))
        with self.assertRaises(ValueError):
            parse_feedback(dict(issue, body='URL: nope\nSTATUS: EXECUTE'), 'owner')
        events = []
        rows, pending, stats = collect_issues('owner/repo', logging.getLogger('test'), feedback=events, opener=lambda *a, **kw: io.BytesIO(json.dumps([issue]).encode()))
        self.assertEqual((rows, pending, len(events)), ([], [], 1))

    def test_feedback_persists_across_seen_preview_and_missing_api(self):
        for cls, suffix in [(History, '.sqlite3'), (JsonHistory, '.jsonl')]:
            with self.subTest(cls=cls), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/('state'+suffix)
                h = cls(path)
                h.track(signal(), NOW)
                h.update_intelligence([feedback('CONTACTED')])
                h.close()
                before = hashlib.sha256(path.read_bytes()).digest()
                h = cls(path, preview=True)
                h.track(signal(), NOW)
                h.update_intelligence([feedback('GOOD', stamp='2099-01-01T00:00:00+00:00')])
                h.close()
                self.assertEqual(before, hashlib.sha256(path.read_bytes()).digest())
                h = cls(path)
                h.track(signal(), NOW)
                h.update_intelligence([])  # API failure/closed Issue does not erase saved outcome.
                row = list(h.current.values())[0]
                self.assertEqual(row['feedback'], 'CONTACTED')
                self.assertFalse(cash_candidate(row))
                self.assertEqual(action_queue([dict(row, status='NEW')], []), [])
                h.close()

    def test_feedback_similarity_and_validation_not_gate_bypass(self):
        a, b = signal(), signal('bob', text=signal()['text'].replace('200', '300'))
        base = enrich_history([a, b])
        result = enrich_history([a, b], [feedback('BAD')])
        self.assertLess(result[1]['market_score'], base[1]['market_score'])
        self.assertLess(result[1]['cash_score'], base[1]['cash_score'])
        self.assertFalse(cash_candidate(result[0]))
        web = signal(text='We need someone to fix our website layout. Our budget is £500.')
        similar = dict(web, url='https://example.com/web2', evidence_url='https://example.com/web2')
        web_result = enrich_history([web, similar], [feedback('IGNORED', web['url'])])
        self.assertLess(web_result[1]['cash_score'], web['cash_score'])
        for status, value in [('REPLIED', 2), ('TESTER', 3), ('PAID', 5)]:
            row = enrich_history([a], [feedback(status)])[0]
            self.assertEqual(row['validation_value'], value)
            self.assertFalse(cash_candidate(row))
        newest = enrich_history([a], [feedback('PAID', stamp='2026-09-02T00:00:00+00:00'), feedback('BAD', stamp='2026-09-01T00:00:00+00:00')])[0]
        self.assertEqual(newest['feedback'], 'PAID')

    def test_clusters_independent_users_sources_dates_and_duplicates(self):
        rows = [signal(a, text=signal()['text'].replace('200', str(201+i))) for i, a in enumerate(['alice', 'bob', 'carol'])]
        clusters = pain_clusters(enrich_history(rows), NOW)
        self.assertEqual(clusters[0]['independent_users'], 3)
        self.assertTrue(clusters[0]['worthy'])
        self.assertEqual(clusters[0]['workaround_count'], 3)
        self.assertEqual(clusters[0]['payment_count'], 3)
        repeated_author = [dict(r, author_or_company='alice') for r in rows]
        self.assertFalse(pain_clusters(enrich_history(repeated_author), NOW)[0]['worthy'])
        self.assertFalse(pain_clusters(enrich_history([dict(r, author_or_company='UNKNOWN') for r in rows]), NOW)[0]['worthy'])
        cross = [rows[0], dict(rows[1], source='public_feed', url='https://example.com/pain', evidence_url='https://example.com/pain')]
        self.assertTrue(pain_clusters(enrich_history(cross), NOW)[0]['worthy'])
        old = (datetime.now(timezone.utc)-timedelta(days=31)).isoformat()
        self.assertEqual(pain_clusters(enrich_history([dict(r, published_at=old) for r in rows]), NOW), [])
        duplicate = [dict(rows[0], url='https://example.com/'+str(i), evidence_url='https://example.com/'+str(i), author_or_company=str(i)) for i in range(3)]
        self.assertEqual(pain_clusters(enrich_history(duplicate), NOW)[0]['signal_count'], 1)
        self.assertEqual(action_queue([], clusters), [])  # Yesterday's NEW flag is not today's evidence.
        market = [signal(a, text=f'We manually process {220+i} invoices every week; it takes 5 hours.') for i, a in enumerate(['alice', 'bob', 'carol'])]
        self.assertEqual(action_queue(market, pain_clusters(market, NOW))[0][0], 'VALIDATE')
        watch = [signal(a, text=f'Our invoice tool is too expensive to export {220+i} records.') for i, a in enumerate(['alice', 'bob', 'carol'])]
        self.assertEqual(action_queue(watch, pain_clusters(watch, NOW))[0][0], 'WATCH')

    def test_solo_cash_market_court_dn_and_document_evidence(self):
        self.assertTrue(cash_candidate(signal()))
        for text in ['DN Colleges Group requires WordPress multisite development, migration and parallel Salesforce CRM programmes.', 'We need someone for enterprise migration and heavy compliance. Our budget is £500.']:
            row = signal(title='DN Colleges Group', text=text)
            self.assertEqual(row['solo_fit'], 'TOO_LARGE')
            self.assertFalse(cash_candidate(row))
            self.assertEqual(action_queue([row], []), [])
        court = signal(title='Amazon vs. Perplexity - U.S. Court of Appeals', text='When you say I am manually controlling the site, that actually means I run a program on my computer. An agentic workflow does the same thing.')
        self.assertFalse(quality_gate(court))
        self.assertEqual(action_queue([court], []), [])
        pain = signal(text='We manually process 200 invoices every week in Excel; it takes 5 hours.')
        self.assertEqual(pain['cash_score'], 0)
        self.assertGreaterEqual(pain['market_score'], 3.5)
        isolated = signal(text='Our software is too complicated to export reports every week. I like invoice tools and receipt scanners.')
        self.assertNotIn('ledgerdrop', isolated['category'])
        self.assertIn('ledgerdrop', signal()['category'])

    def test_queue_max_three_no_repeat_and_mobile_health_reports(self):
        rows = [signal(str(i), text=signal()['text'].replace('200', str(210+i))) for i in range(5)]
        self.assertEqual(len(action_queue(rows, [])), 3)
        self.assertEqual(action_queue([dict(r, status='SEEN') for r in rows], []), [])
        self.assertEqual(action_queue(rows, [], preview=True), [])
        expired = dict(rows[0], deadline='2001-01-01T00:00:00+00:00')
        self.assertEqual(action_queue([expired], []), [])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports(root, rows, ['HN: OK', 'RSS: FAILED (TimeoutError)'], [], NOW, history_rows=rows)
            text = (root/'daily_report.md').read_text(encoding='utf-8')
            self.assertIn('**DEGRADED**', text)
            positions = [text.index('## '+heading) for heading in ["Today's Action Queue", 'Cash Now', 'Repeated Market Pain', 'LedgerDrop', 'System Health']]
            self.assertEqual(positions, sorted(positions))
            queue = (root/'action_queue.md').read_text(encoding='utf-8')
            for field in ['Who', 'Need', 'Evidence', 'Why now', 'What we can offer', 'Contact route', 'Confidence']:
                self.assertIn('- '+field+':', queue)
            for name in ['pain_clusters.md', 'filter_summary.md', 'opportunities.csv']:
                self.assertTrue((root/name).exists())
            reports(root, [], ['HN: OK, empty'], [], NOW)
            self.assertIn('**HEALTHY**', (root/'daily_report.md').read_text(encoding='utf-8'))
            self.assertIn('NO ACTION REQUIRED TODAY', (root/'action_queue.md').read_text(encoding='utf-8'))
            engaged = enrich_history(rows, [feedback('CONTACTED', r['url']) for r in rows])
            reports(root, engaged, [], [], NOW)
            self.assertIn('NO ACTION REQUIRED TODAY', (root/'action_queue.md').read_text(encoding='utf-8'))
            self.assertNotIn('### Invoice workflow help', (root/'pain_radar_top5.md').read_text(encoding='utf-8'))

    def test_pipeline_failure_reports_failed_and_keeps_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root/'state.jsonl'
            state.write_text('', encoding='utf-8')
            before = state.read_bytes()
            with patch('run_daily_radar.reports', side_effect=OSError):
                with self.assertRaises(OSError):
                    run(root, True, state_file=state)
            self.assertEqual(before, state.read_bytes())
            self.assertIn('**FAILED**', (root/'output/daily_report.md').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
