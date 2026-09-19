import tempfile
import unittest
from pathlib import Path

from radar.core import classify, quality_gate
from radar.reporting import action_queue, ranked, reports


class PainRoutingTests(unittest.TestCase):
    def test_cross_sentence_pain_without_purchase_intent(self):
        texts = [
            'I have spent hours trying to automate receipts. Basic things keep breaking.',
            'Love this assistant for work. It keeps losing context after most long chats. Opening a new chat is frustrating.',
            'My workflow: dictate, copy the text, paste it into another app. This makes me leave the app daily.',
        ]
        for text in texts:
            with self.subTest(text=text):
                row = dict(classify({'text': text}), status='NEW')
                self.assertIn('pain', row['category'])
                self.assertTrue(quality_gate(row, 'pain'))
                self.assertNotIn('money', row['category'])
                self.assertFalse(quality_gate(row, 'money'))
                self.assertEqual(action_queue([row], []), [])

    def test_pain_does_not_require_scope_route_or_actionability(self):
        row = dict(classify({'text': 'We manually process invoices every week.'}),
                   title='Current invoice friction', total_score=1, solo_fit='TOO_LARGE',
                   status='NEW', contact_page_or_public_contact='')
        self.assertTrue(quality_gate(row, 'pain'))
        self.assertFalse(quality_gate(row))
        self.assertEqual(ranked([row], 'pain'), [row])
        with tempfile.TemporaryDirectory() as tmp:
            # enrich_history derives scope again from text, so use explicit large scope.
            row['text'] += ' This is an enterprise migration.'
            reports(Path(tmp), [row], [], [], '2026-09-19', preview=True)
            self.assertIn(row['title'], (Path(tmp)/'pain_radar_top5.md').read_text(encoding='utf8'))
            row['feedback'] = 'CONTACTED'
            reports(Path(tmp), [row], [], [], '2026-09-19', preview=True)
            self.assertNotIn(row['title'], (Path(tmp)/'pain_radar_top5.md').read_text(encoding='utf8'))

    def test_promotion_jobs_hypothetical_and_opinion_stay_out(self):
        for text in [
            'I manually process invoices every week. Hire me for your automation.',
            'I spent 2 hours every week manually categorizing expenses. Then I built a bot. Here is the problem I solved.',
            'If I manually process invoices every week, it takes hours.',
            'I think software is interesting. Manual workflows are a topic of academic discussion.',
            'I manually process invoices every week. Apply now for this full-time position.',
            'I will build my own invoice tool. The amount I would save could cover a subscription.',
        ]:
            with self.subTest(text=text):
                row = classify({'text': text})
                self.assertNotIn('pain', row['category'])
                self.assertFalse(quality_gate(dict(row, category='pain', total_score=5), 'pain'))

    def test_contact_still_requires_qualified_pain(self):
        row = dict(classify({'text': 'I need someone to automate invoice extraction. My budget is $500.'}),
                   status='NEW', url='https://x.com/buyer/status/1', total_score=5)
        self.assertFalse(quality_gate(row, 'pain'))
        self.assertEqual(action_queue([row], []), [])
