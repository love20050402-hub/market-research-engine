import unittest

from radar.core import classify, marketplace_buyer_evidence, quality_evidence, quality_gate
from radar.reporting import action_queue, ranked, rejection_reasons


class MarketplaceBuyerTests(unittest.TestCase):
    url = 'https://www.upwork.com/freelance-jobs/apply/Invoice-Automation_~02123456789/'
    request = 'We need a freelancer to build automation to extract invoice data from PDFs into a spreadsheet.'

    def row(self, text, **changes):
        return dict(classify(dict(text=text, url=self.url, **changes)), status='NEW')

    def test_budget_formats_and_deliverable_without_pain(self):
        for budget in ['$25 fixed price', '$110 fixed-price', '$20-$40/hour', '$20–$40/hr',
                       'budget: $25', 'hourly: $20-$40', '$1,100.50 fixed-price', 'Paid one-time project']:
            with self.subTest(budget=budget):
                row = self.row(self.request + ' ' + budget + '.')
                ev = marketplace_buyer_evidence(row)
                self.assertTrue(ev['payment'])
                self.assertEqual(ev['request'], self.request)
                self.assertEqual(row['contact_page_or_public_contact'], ev['route'])
                self.assertTrue(quality_evidence(row)['payment'])
                self.assertEqual(row['solo_fit'], 'SOLO_FIT')
                self.assertIn(row, ranked([row], 'money'))
                self.assertFalse(quality_gate(row, 'pain'))
                self.assertIn('Route B', action_queue([row], [])[0][2])
                primary, secondary = rejection_reasons(row)
                self.assertNotIn('NO_COMMERCIAL_SIGNAL', [primary, *secondary])

    def test_not_source_alone_seller_employee_or_unpaid(self):
        for text in [
            'We need a freelancer for something. Budget: $25.',
            self.request + ' Budget: $0 fixed price.',
            self.request + ' Unpaid project. Budget: $25.',
            self.request + ' Hire me. My services start at $25 fixed price.',
            'We need someone to extract invoice data from PDFs. Full-time employee position. Budget: $25.',
            'If we need a freelancer to extract invoice data from PDFs, budget: $25.',
        ]:
            with self.subTest(text=text):
                row = self.row(text)
                self.assertFalse(marketplace_buyer_evidence(row)['payment'])
                self.assertFalse(marketplace_buyer_evidence(row)['route'])
                self.assertEqual(action_queue([row], []), [])
        for url in ['https://upwork.com/freelancers/person',
                    'https://upwork.com.evil.example/freelance-jobs/apply/Invoice_~123',
                    'https://example.com/freelance-jobs/apply/Invoice_~123']:
            row = self.row(self.request + ' Budget: $25.', source='upwork')
            row['url'] = url
            self.assertFalse(marketplace_buyer_evidence(row)['payment'])

    def test_pain_route_keeps_score_pain_scope_and_route_gates(self):
        row = self.row('We manually process 200 invoices every week in Excel; it takes 5 hours. '
                       + self.request + ' Budget: $110 fixed-price.')
        self.assertTrue(quality_gate(row, 'pain'))
        row['url'] = 'https://example.com/buyer'
        row['text'] += ' Our budget is $110.'
        self.assertEqual(action_queue([row], [])[0][0], 'CONTACT')
        self.assertIn('Route A', action_queue([row], [])[0][2])
        for change in [dict(total_score=3.49), dict(cash_score=3.49), dict(solo_fit='TOO_LARGE'),
                       dict(solo_fit='UNKNOWN'), dict(contact_page_or_public_contact=''), dict(status='SEEN')]:
            with self.subTest(change=change):
                self.assertEqual(action_queue([dict(row, **change)], []), [])

    def test_marketplace_route_requires_evidence_scope_and_freshness(self):
        row = self.row(self.request + ' Budget: $25 fixed price.')
        self.assertIn('Route B', action_queue([dict(row, total_score=1, cash_score=1)], [])[0][2])
        for change in [dict(solo_fit='UNKNOWN'), dict(solo_fit='SMALL_TEAM_FIT'), dict(solo_fit='TOO_LARGE'),
                       dict(status='SEEN'), dict(feedback='CONTACTED'), dict(feedback='BAD'), dict(feedback_penalty=1),
                       dict(url=''), dict(url='https://upwork.com/freelancers/person'),
                       dict(text=self.request), dict(text=self.request+' Budget: $25. Full-time employee.'),
                       dict(text=self.request+' Budget: $25. Pay us a registration fee first.'),
                       dict(text=self.request+' Budget: $25. Sponsored. Sign up now.')]:
            with self.subTest(change=change):
                self.assertEqual(action_queue([dict(row, **change)], []), [])
        self.assertEqual(action_queue([row], [], preview=True), [])
