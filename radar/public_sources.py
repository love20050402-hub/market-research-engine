"""Small public feeds; no website crawling, login, paid or unofficial API."""
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode

from collectors.hackernews import clean_text
from radar.core import canonical_url, match, NEEDS, SCORES, classify
from radar.intake import get_public

WWR_FEED = 'https://weworkremotely.com/remote-jobs.rss'
CF_API = 'https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search'


def parse_wwr(content, now=None):
    now = now or datetime.now(timezone.utc)
    if '<!DOCTYPE' in content.upper() or '<!ENTITY' in content.upper():
        raise ValueError('DTD/entity declarations are not accepted')
    tree = ET.fromstring(content)
    if tree.tag != 'rss':
        raise ValueError('Expected RSS feed')
    rows = []
    for item in tree.findall('./channel/item')[:100]:
        try:
            if (item.findtext('type') or '').casefold() != 'contract':
                continue
            date = parsedate_to_datetime(item.findtext('pubDate') or '')
            if date.tzinfo is None or date < now-timedelta(days=30):
                continue
            expiry = item.findtext('expires_at')
            if expiry and parsedate_to_datetime(expiry) <= now:
                continue
            title = clean_text(item.findtext('title'))
            description = item.findtext('description') or ''
            text = clean_text(description)
            if not any(match(p, title+' '+text) for p in NEEDS.values()):
                continue
            if match(r'commission.only|recruitment agency|recruiter|full.time', text):
                continue
            # Company URL is explicitly supplied by this feed, never inferred from employer name.
            website = re.search(r'URL:</strong>\s*<a\s+href=[\"\x27]([^\"\x27]+)', description, re.I)
            rows.append({'source': 'weworkremotely', 'source_type': 'contract_listing',
                         'title': title, 'text': text, 'url': item.findtext('link'),
                         'company': title.split(':', 1)[0] if ':' in title else '',
                         'website': website.group(1) if website else '',
                         'country': 'UK' if match(r'United Kingdom|\bUK\b', item.findtext('country') or '') else 'UNKNOWN',
                         'published_at': date.isoformat(), 'buyer_type': 'Company contract listing; verify size and engagement terms'})
        except (ValueError, TypeError, AttributeError):
            continue
    return rows


def parse_contracts(content, now=None):
    """Only open, small-value SME-suitable digital procurement with named buyer/website."""
    now = now or datetime.now(timezone.utc)
    payload = json.loads(content)
    if not isinstance(payload, dict) or not isinstance(payload.get('releases'), list):
        raise ValueError('Missing OCDS releases')
    rows = []
    for release in payload['releases'][:50]:
        try:
            tender = release['tender']
            if tender.get('status') != 'active' or 'tender' not in release.get('tag', []):
                continue
            deadline = datetime.fromisoformat(tender['tenderPeriod']['endDate'].replace('Z', '+00:00'))
            if deadline <= now or tender.get('suitability', {}).get('sme') is not True:
                continue
            value = tender.get('value', {})
            amount = float(value.get('amount', 0))
            if value.get('currency') != 'GBP' or not 0 < amount <= 25000:
                continue
            title, text = clean_text(tender.get('title')), clean_text(tender.get('description'))
            if not match(r'website|spreadsheet|data (?:processing|cleansing|entry)|document (?:extraction|processing)|workflow automation|content (?:migration|organisation|organization)', title+' '+text):
                continue
            buyer = next(p for p in release['parties'] if p['id'] == release['buyer']['id'])
            if buyer.get('address', {}).get('countryName') not in {'United Kingdom', 'England', 'Scotland', 'Wales', 'Northern Ireland'}:
                continue
            website = canonical_url(buyer.get('details', {}).get('url', ''))
            evidence = next(canonical_url(d['url']) for d in tender.get('documents', []) if d.get('documentType') == 'tenderNotice')
            if not website or not evidence or len(text) < 25:
                continue
            row = {'source': 'contracts_finder', 'source_type': 'public_tender', 'url': evidence,
                   'evidence_url': evidence, 'evidence_text': text, 'title': title, 'text': text,
                   'company': buyer['name'], 'website': website, 'country': 'UK',
                   'author_or_company': buyer['name'], 'published_at': release['date'],
                   'deadline': deadline.isoformat(), 'buyer_type': 'Public procurement buyer (not claimed to be a small private company)',
                   'contact_page_or_public_contact': evidence}
            # Keep explicit structured procurement evidence separate from first-person post rules.
            scored = classify(row)
            scored.update(category='uk', payment_signal=f'Published procurement value: GBP {amount:g}; award/payment not guaranteed',
                          possible_offer='Assess eligibility first; propose a tightly scoped digital delivery only if tender requirements fit',
                          reason='Official active tender, SME-suitable, GBP <=25,000, digital scope, future deadline, buyer website and evidence URL. Procurement eligibility must be checked.')
            scored.update(zip(SCORES, (5, 4, 1, 3, 1)))
            scored['total_score'] = 2.8
            rows.append(scored)
        except (KeyError, ValueError, TypeError, AttributeError, StopIteration):
            continue
    return rows


def collect_public_sources(logger):
    since = (datetime.now(timezone.utc)-timedelta(days=2)).strftime('%Y-%m-%dT%H:%M:%S')
    sources = [('WWR RSS', WWR_FEED, parse_wwr),
               ('UK Contracts Finder', CF_API+'?'+urlencode({'publishedFrom': since, 'stages': 'tender', 'limit': 50}), parse_contracts)]
    records, stats = [], []
    for name, url, parser in sources:
        try:
            content, _ = get_public(url)
            rows = parser(content)
            records.extend(rows)
            stats.append(f'{name}: OK, {len(rows)} eligible source records (bounded, no pagination)')
        except Exception as exc:
            logger.warning('%s failed: %s', name, type(exc).__name__)
            stats.append(f'{name}: FAILED ({type(exc).__name__})')
    return records, stats
