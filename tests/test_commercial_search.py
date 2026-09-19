import tempfile
import unittest
from pathlib import Path

from radar.core import classify, quality_gate, solution_search_evidence
from radar.reporting import action_queue, ranked, rejection_reasons, reports


class CommercialSearchTests(unittest.TestCase):
    def test_concrete_active_search_enters_money_without_contact(self):
        for text in [
            'Anyone using a good online distribution software for inventory and billing/invoices? Looking for something reliable. Please recommend one or connect me with a good source.',
            "I'm looking for recommendations on accounting software.",
            "What tools are you using to build your business?\nI'm currently using:\nShopify\nOutlook\nTrello\nI'm looking for recommendations on accounting software.",
            'Looking for inventory software for our shop.',
            'Please recommend a tool for billing invoices.',
            'Connect me with a good provider of payroll software.',
            'I need a tool for tracking inventory.',
            'I am looking for an alternative to QuickBooks.',
        ]:
            with self.subTest(text=text):
                row = dict(classify({'text': text, 'url': 'https://x.com/buyer/status/1'}), status='NEW')
                self.assertTrue(solution_search_evidence(row))
                self.assertIn('money', row['category'])
                self.assertTrue(quality_gate(row, 'money'))
                self.assertEqual(ranked([row], 'money'), [row])
                self.assertNotIn('pain', row['category'])
                self.assertEqual(action_queue([row], []), [])
                self.assertIn('budget unconfirmed', row['payment_signal'])
                primary, secondary = rejection_reasons(row)
                self.assertNotIn('NO_COMMERCIAL_SIGNAL', [primary, *secondary])
                self.assertEqual(row['cash_score'], 0)

    def test_discussion_sellers_ads_jobs_and_hypotheticals_stay_out(self):
        for text in [
            'What tools do you use to build your business?',
            "What's your setup for feeding context to the model? CLAUDE.md, a RAG layer, just pasting docs? Looking for what actually works for people.",
            'People are looking for accounting software according to a recent study.',
            'I recommend a tool for accounting software users.',
            'If I were looking for accounting software, I would compare reviews.',
            'Looking for accounting software? We offer the best service. Sign up now.',
            'Looking for accounting software. Apply now for this full-time position.',
            'I am looking for software for free, no budget.',
            'I am looking for an alternative to my software to process invoices.',
            'Looking for software, just curious about the industry.',
            'I read a quote: "Looking for accounting software."',
        ]:
            with self.subTest(text=text):
                row = classify({'text': text})
                self.assertFalse(solution_search_evidence(row))
                self.assertNotIn('money', row['category'])

    def test_reports_keep_search_visible_but_no_actions(self):
        row = dict(classify({'text': "I'm looking for recommendations on accounting software.",
                            'title': 'Accounting buyer', 'url': 'https://x.com/buyer/status/1'}), status='NEW')
        with tempfile.TemporaryDirectory() as tmp:
            reports(Path(tmp), [row], [], [], '2026-09-19')
            self.assertIn('Accounting buyer', (Path(tmp)/'money_radar_top5.md').read_text(encoding='utf8'))
            self.assertIn('NO ACTION REQUIRED TODAY', (Path(tmp)/'action_queue.md').read_text(encoding='utf8'))
