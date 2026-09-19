import csv
import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from radar.core import classify, utcnow
from radar.reporting import market_top10
from run_daily_radar import run

FIXTURE = Path(__file__).parent/'fixtures/radar_samples.json'


class MarketV1Tests(unittest.TestCase):
    def test_existing_samples_deduplicate_and_export_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = run(root, True, [FIXTURE, FIXTURE])
            self.assertEqual(len(rows), 6)
            with (root/'output/market_top10.csv').open(encoding='utf-8-sig') as stream:
                top = list(csv.DictReader(stream))
            self.assertEqual(len(top), 2)
            self.assertEqual(top[0]['title'], 'Need invoice automation help')
            self.assertIn('£500', top[0]['wtp_evidence'])
            self.assertEqual(float(top[0]['Urgency']), 0)
            self.assertEqual(top[0]['urgency_evidence'], 'UNKNOWN')
            for row in top:
                self.assertTrue(row['source_url'])
                self.assertTrue(row['evidence'])
                self.assertTrue(row['recommended_next_action'])
                for key in ['Pain', 'WTP', 'Workaround', 'Urgency', 'Fit', 'v1_score']:
                    self.assertTrue(0 <= float(row[key]) <= 5)
            again = run(root, True, [FIXTURE])
            self.assertTrue(all(r['status'] == 'SEEN' for r in again))
            text = (root/'output/market_top10.md').read_text(encoding='utf-8')
            self.assertIn('WATCH — already seen', text)
            self.assertNotIn('CONTACT —', text)
            self.assertTrue((root/'output/daily_report.md').exists())

    def test_exclusions_top10_and_evidence_based_urgency(self):
        sample = json.loads(FIXTURE.read_text(encoding='utf-8'))[0]
        valid = dict(classify(sample), status='NEW', important_update=False)
        excluded = [dict(valid, title=term+' '+valid['title']) for term in
                    ['Crypto', 'Bitcoin', 'political election', 'Full-time', 'Salary', 'Promotion', 'Buy now']]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self.assertEqual(market_top10(folder, excluded, utcnow()), [])
            rows = [dict(valid, url=f'https://example.com/{i}') for i in range(12)]
            rows[0]['deadline'] = (datetime.now(timezone.utc)+timedelta(days=2)).isoformat()
            top = market_top10(folder, rows, utcnow())
            self.assertEqual(len(top), 10)
            self.assertEqual(top[0]['Urgency'], 5)
            self.assertTrue(top[0]['urgency_evidence'].startswith('Deadline:'))
            self.assertEqual(valid['total_score'], classify(sample)['total_score'])


if __name__ == '__main__':
    unittest.main()
