"""Small evidence-only rules shared by daily reports and read-only history review."""
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta

from radar.core import canonical_url, quality_evidence, quality_gate, fingerprint, domain

FEEDBACK = {'GOOD', 'BAD', 'MAYBE', 'CONTACTED', 'REPLIED', 'TESTER', 'PAID', 'IGNORED'}
ENGAGED = {'CONTACTED', 'REPLIED', 'TESTER', 'PAID'}


def date(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    except (ValueError, TypeError):
        return None


def cluster_key(row, require_pain=True):
    evidence = quality_evidence(row)
    if not evidence['pain'] and (require_pain or not evidence['request']):
        return ''
    # Narrow object + operation buckets; never merge all "software problems" together.
    text = ' '.join(evidence[k] for k in ('pain', 'workaround', 'replacement', 'request')).lower()
    for obj, pattern in [('invoices', r'invoice|receipt|bookkeeping'), ('documents', r'\bpdf\b|document|\bocr\b'), ('spreadsheets', r'spreadsheet|excel|\bcsv\b'), ('websites', r'website')]:
        if re.search(pattern, text):
            for task, operation in [('manual processing', r'manual|copy.paste|data entry|by hand'), ('export/import', r'export|import|convert'), ('extraction', r'extract|\bocr\b'), ('tool replacement', r'alternative|replace|switch|too expensive|missing feature'), ('layout repair', r'layout|design')]:
                if re.search(operation, text):
                    return obj + ': ' + task
    return ''


def document_workflow(row):
    for sentence in re.split(r'(?<=[.!?])\s+|\n', row.get('text', '')):
        if (re.search(r'invoice|receipt|expense|bookkeeping|\bpdf\b|\bocr\b|financial document|document extraction', sentence, re.I)
                and re.search(r'process|extract|enter|reconcil|copy|convert|import|export|upload|data entry', sentence, re.I)
                and quality_evidence({'text': sentence})['pain']):
            return True
    return False


def enrich_history(rows, feedback=()):
    latest = {}
    for item in feedback:
        url = canonical_url(item['url'])
        if url not in latest or item['feedback_at'] > latest[url]['feedback_at']:
            latest[url] = item
    result = []
    for original in rows:
        row = dict(original)
        item = latest.get(canonical_url(row.get('url', ''))) or latest.get(canonical_url(row.get('evidence_url', '')))
        if item and item['feedback_at'] > row.get('feedback_at', ''):
            row.update({key: item[key] for key in ('feedback', 'feedback_at', 'feedback_issue')})
        ev = quality_evidence(row)
        text = row.get('title', '') + ' ' + row.get('text', '')
        if re.search(r'multisite|multi.site|salesforce|enterprise (?:migration|integration)|heavy compliance|regulatory compliance|HIPAA|PCI.DSS|large tender|national framework|fortune 500|global enterprise|large.scale|website estate', text, re.I):
            fit = 'TOO_LARGE'
        elif re.search(r'tender|procurement|team of|multiple developers', text, re.I) or row.get('source_type') == 'public_tender':
            fit = 'SMALL_TEAM_FIT'
        elif (ev['request'] or ev['pain']) and row.get('need_type', 'UNKNOWN') != 'UNKNOWN' and re.search(r'invoice|receipt|spreadsheet|\bcsv\b|\bpdf\b|small (?:website|script|tool)|website layout|automation', text, re.I):
            fit = 'SOLO_FIT'
        else:
            fit = 'UNKNOWN'
        row['solo_fit'] = fit
        row['pain_cluster'] = cluster_key(row)
        row['validation_value'] = {'GOOD': 1, 'REPLIED': 2, 'TESTER': 3, 'PAID': 5}.get(row.get('feedback'), 0)
        row['cash_score'] = min(5, 2*bool(ev['payment']) + bool(ev['request']) + bool(ev['pain']) + bool(ev['workaround'])) if ev['payment'] else 0
        row['market_score'] = min(5, 2*bool(ev['pain']) + bool(ev['repeat']) + bool(ev['workaround']) + bool(ev['replacement']))
        if not document_workflow(row):
            row['category'] = ';'.join(c for c in row.get('category', '').split(';') if c != 'ledgerdrop')
        result.append(row)
    negative = Counter(cluster_key(r, False) for r in result if cluster_key(r, False) and r.get('feedback') in {'BAD', 'IGNORED'})
    for row in result:
        key = cluster_key(row, False)
        penalty = min(2, negative[key]) if key else 0
        row['feedback_penalty'] = penalty
        row['cash_score'] = max(0, row['cash_score'] - penalty)
        row['market_score'] = max(0, row['market_score'] - penalty)
    return result


def eligible(row):
    return row.get('feedback') not in ENGAGED | {'BAD', 'IGNORED'} and row.get('solo_fit') != 'TOO_LARGE'


def cash_candidate(row):
    return (eligible(row) and row.get('solo_fit') == 'SOLO_FIT' and row.get('cash_score', 0) >= 3.5
            and 'money' in row.get('category', '').split(';') and quality_gate(row, 'money') and quality_gate(row))


def pain_clusters(rows, now):
    moment = date(now) or datetime.now(timezone.utc)
    buckets = defaultdict(list)
    seen_urls, seen_text = set(), set()
    for row in rows:
        published = date(row.get('published_at')) or date(row.get('first_seen_at'))
        key, url, digest = row.get('pain_cluster'), canonical_url(row.get('evidence_url') or row.get('url', '')), fingerprint(row)
        if (not key or not url or not published or not moment-timedelta(days=30) <= published <= moment
                or row.get('feedback') in {'BAD', 'IGNORED'} or not quality_gate(row, 'pain')
                or 'pain' not in row.get('category', '').split(';') or url in seen_urls or digest in seen_text):
            continue
        seen_urls.add(url)
        seen_text.add(digest)
        buckets[key].append(row)
    clusters = []
    for key, members in buckets.items():
        # Unknown identities never count as independent users. A handle across sources
        # is counted once, conservatively, rather than assuming two different people.
        users = {r.get('author_or_company', '').strip().casefold() for r in members} - {'', 'unknown'}
        sources = {domain(r.get('evidence_url') or r['url']) for r in members} - {''}
        evs = [quality_evidence(r) for r in members]
        workaround = sum(bool(e['workaround']) for e in evs)
        payment = sum(bool(e['payment']) for e in evs)
        worthy = len(users) >= 3 or (len(sources) >= 2 and bool(workaround or payment))
        clusters.append(dict(key=key, members=members, independent_users=len(users), signal_count=len(members),
                             workaround_count=workaround, payment_count=payment, source_count=len(sources),
                             worthy=worthy, confidence='MEDIUM — validate, not proven demand' if worthy else 'LOW — insufficient independent evidence',
                             validation_value=sum(r.get('validation_value', 0) for r in members)))
    return sorted(clusters, key=lambda c: (c['worthy'], c['validation_value'], c['independent_users'], c['signal_count']), reverse=True)
