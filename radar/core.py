"""Conservative, explainable opportunity rules and persistent identity."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import os
from pathlib import Path
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from collectors.hackernews import clean_text

FIELDS = '''source source_type url author_or_company title text published_at country category
need_type pain_summary current_workaround payment_signal pain_signal saas_signal fit_signal
score_authenticity score_payment_intent score_pain_strength score_user_fit score_saas_potential
total_score reason status first_seen_at last_seen_at company website contact_page_or_public_contact
possible_offer competitor workflow feature_request potential_tester important_update
evidence_url evidence_text buyer_type deadline'''.split()
SCORES = [f for f in FIELDS if f.startswith('score_')]
NEEDS = {
    'document/data processing': r'invoices?|receipts?|expenses?|bookkeeping|pdf|ocr|data entry|manual entry|financial documents?|document extraction',
    'spreadsheet cleanup': r'spreadsheet|excel|csv|copy.paste',
    'automation/internal tools': r'automation|automate|workflow|internal tool|repetitive',
    'website/development': r'developer|website|software|coding|programmer',
    'design': r'designer|design help',
    'marketing/content': r'marketer|marketing|content help|content organization',
}
SEEK = r'looking for|need (?:someone|help|a |an |to )|hiring (?:someone|a |an )|willing to pay|can anyone|is there a tool|seeking|alternative|wish .{0,30}(?:tool|feature)'
PAIN = r'manual(?:ly)?|repetitive|copy.paste|too expensive|too complicated|missing feature|frustrat\w*|spreadsheet hell|takes .{0,20}hours|data entry|by hand|wast\w* time|struggl\w*'
ACTION = r'process\w*|extract\w*|enter\w*|reconcil\w*|track\w*|copy\w*|clean\w*|upload\w*|convert\w*|organiz\w*|automat\w*|fix\w*|import\w*|export\w*|workflow|manually|data entry|manual entry'
OWNER = r'\b(?:i|we|our|my)\b'
BUYER = r'(?:i|we)(?:.m| are| am)? (?:looking for|need (?:someone|help|a developer|a designer|a freelancer|a marketer)|want to hire|would pay|am willing to pay)|(?:my|our) (?:company|team|business) needs help|need someone to|looking for (?:a |an )?(?:freelancer|developer|designer|marketer)|hiring someone'
PAYMENT = r'(?:i|we)(?:.m| are| am)? (?:willing to pay|can pay|will pay|would pay)|(?:my|our|have a|have) budget|budget (?:is|of|:)|paid (?:project|contract)|pay someone'
BUSINESS_OBJECT = r'invoice|receipt|expense|bookkeeping|pdf|ocr|spreadsheet|excel|data entry|manual entry|website|customer|client|business|report|content|marketing|software|tool|workflow'


def match(pattern, text):
    return bool(re.search(pattern, text, re.I))


def excerpt(text, pattern):
    for sentence in re.split(r'(?<=[.!?])\s+|\n', text):
        if match(pattern, sentence):
            return sentence[:600]
    return 'UNKNOWN'


def canonical_url(value):
    try:
        p = urlsplit(str(value).strip())
        if p.scheme not in ('https', 'http') or not p.hostname or p.username or p.password:
            return ''
        host = p.hostname.lower().removeprefix('www.')
        if host in ('twitter.com', 'mobile.twitter.com'):
            host = 'x.com'
        query = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith('utm_') and k not in ('ref', 's', 't', 'fbclid')]
        return urlunsplit(('https', host + (f':{p.port}' if p.port else ''), p.path.rstrip('/'), urlencode(sorted(query)), ''))
    except ValueError:
        return ''


def domain(url):
    return urlsplit(canonical_url(url)).hostname or ''


def normalize(raw):
    if not isinstance(raw, dict):
        raise ValueError('record must be an object')
    row = {k: clean_text(raw.get(k)) if isinstance(raw.get(k), (str, int, float)) else '' for k in FIELDS}
    row['text'] = row['text'] or clean_text(raw.get('story_text') or raw.get('comment_text') or raw.get('source_text'))
    row['title'] = row['title'] or clean_text(raw.get('source_title')) or row['text'][:100]
    row['author_or_company'] = row['author_or_company'] or clean_text(raw.get('author') or raw.get('company')) or 'UNKNOWN'
    row['published_at'] = row['published_at'] or clean_text(raw.get('created_at')) or 'UNKNOWN'
    row['source'] = row['source'] or 'manual'
    row['source_type'] = row['source_type'] or 'public_post'
    row['url'] = canonical_url(raw.get('hn_url') or row['url'] or raw.get('source_url', ''))
    row['website'] = canonical_url(row['website'])
    row['evidence_url'] = canonical_url(row['evidence_url']) or row['url']
    row['evidence_text'] = row['evidence_text'] or row['text']
    row['country'] = row['country'] or 'UNKNOWN'
    if len(row['text']) < 25:
        raise ValueError('insufficient post text; paste the actual content')
    return row


def classify(raw):
    r = normalize(raw)
    # HN comment titles describe the parent story, not the comment author's need.
    text = r['text'] if r['source'] == 'hackernews' else r['title'] + '. ' + r['text']
    need = [name for name, pattern in NEEDS.items() if match(pattern, text)]
    seek, pain, owner = match(SEEK, text), match(PAIN, text), match(OWNER, text)
    ad = match(r'hire me|available for hire|my services|we offer|our services|buy now|sign up now|sponsored|use my referral|i built|we built|i launched|show hn:', text)
    recruiter = match(r'recruitment agency|recruiter|recruiting agency|job board|apply now|full.time (?:role|position)', text)
    negative = match(r'not willing to pay|won.t pay|no budget|zero budget|unpaid|for free|no longer (?:need|looking)|position filled|already (?:solved|hired)', text)
    payment = excerpt(text, PAYMENT)
    quantified = match(r'\d+\s*(?:hours?|days?|invoices?|receipts?)|every (?:day|week|month)|daily|weekly|monthly', text)
    concrete = bool(need) and match(ACTION, text)
    auth = min(5, int(owner)*2 + int(concrete)*2 + int(quantified or bool(r['url'])))
    pay = 0 if negative else (5 if payment != 'UNKNOWN' and seek else 2 if seek and need else 0)
    strength = min(5, int(pain)*2 + int(pain and quantified)*2 + int(pain and seek))
    fit = 4 if concrete else 2 if need else 0
    saas = min(5, int(concrete and pain)*2 + int(quantified and pain)*2 + int(match(r'alternative|missing feature|too expensive|too complicated', text)))
    sentences = re.split(r'(?<=[.!?])\s+|\n', text)
    personal_pain = any(match(OWNER, s) and match(PAIN, s) and match(BUSINESS_OBJECT, s) for s in sentences)
    buyer = any(match(BUYER, s) and not match(r'\bif (?:i|we)|suppose|hypothetical|used to|years ago', s) and any(match(p, s) for p in NEEDS.values()) for s in sentences)
    qualified = auth >= 3 and not ad and not recruiter and not match(r'no longer (?:need|looking)|position filled|already (?:solved|hired)', text)
    categories = []
    if qualified and buyer and pay >= 2:
        categories.append('money')
    if qualified and personal_pain and concrete:
        categories.append('pain')
    r.update(zip(SCORES, (auth, pay, strength, fit, saas)))
    r.update(category=';'.join(categories), need_type='; '.join(need) or 'UNKNOWN',
             total_score=round((auth+pay+strength+fit+saas)/5, 2),
             pain_summary=excerpt(r['text'], PAIN), pain_signal=excerpt(r['text'], PAIN),
             payment_signal=payment if not negative else 'Explicit negative/free/closed signal; payment not established',
             current_workaround=excerpt(r['text'], r'currently|using|manual|by hand|copy.paste|spreadsheet|excel'),
             workflow=excerpt(r['text'], ACTION) if concrete else 'UNKNOWN',
             competitor=excerpt(text, r'quickbooks|xero|dext|expensify|freshbooks|wave|sage'),
             feature_request=excerpt(text, r'wish|missing|need .{0,40}feature|looking for an alternative'),
             potential_tester='POSSIBLE — confirm interest' if qualified and concrete and seek else 'UNKNOWN',
             saas_signal='Recurring workflow hypothesis; demand unvalidated' if saas >= 3 else 'Insufficient recurring evidence',
             fit_signal='Small scoped service hypothesis' if fit >= 4 else 'Needs scope review',
             possible_offer='Propose a small paid pilot for: ' + '; '.join(need) if need else 'UNKNOWN',
             reason=f'Rules v2; owner={owner}; buyer request={buyer}; personal pain={personal_pain}; concrete workflow={concrete}; recurring/quantity={quantified}; ad={ad}; recruiter={recruiter}; negative={negative}. Scores are estimates, not verified purchase intent.')
    # Region and small-company evidence must both be explicit; a .uk suffix is not proof.
    uk = r['country'].casefold() in ('uk', 'gb', 'united kingdom', 'great britain') or match(r'\b(?:uk|united kingdom|britain|england|scotland|wales|northern ireland)\b', text)
    small = match(r'small (?:business|agency|company|team)|sole trader|independent consultant|local business|\b[1-9][0-9]?[- ]person\b', text)
    large = match(r'fortune 500|multinational|global enterprise|\b\d{3,}[,\d]* employees', text) or domain(r['website']) in {'google.com', 'amazon.com', 'microsoft.com', 'apple.com', 'meta.com'}
    if uk:
        r['country'] = 'UK'
    if qualified and uk and small and not large and r['company'] and r['website'] and r['evidence_url'] and concrete and (pain or seek):
        r['category'] += ';uk'
    if qualified and concrete and personal_pain and match(r'invoice|receipt|expense|bookkeeping|pdf|spreadsheet|manual entry|ocr|financial document|document extraction', text):
        r['category'] += ';ledgerdrop'
    return r


def fingerprint(r):
    return hashlib.sha256(re.sub(r'\W+', ' ', r['text'].casefold()).strip().encode()).hexdigest()


class History:
    """One local SQLite transaction per completed run; no database server."""
    def __init__(self, path, *, preview=False):
        self.preview = preview
        if os.environ.get('RADAR_TEST_MODE') == '1' and str(path) != ':memory:':
            production = Path(__file__).resolve().parents[1]/'data'
            if Path(path).resolve().is_relative_to(production):
                raise ValueError('Tests cannot access production history')
        self.db = sqlite3.connect(':memory:' if preview else path, timeout=2)
        if preview and Path(path).is_file():
            original = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True)
            try:
                original.backup(self.db)
            finally:
                original.close()
        self.db.execute('CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
        self.db.execute('BEGIN IMMEDIATE')
        self.rows = [(key, json.loads(payload)) for key, payload in self.db.execute('SELECT id,payload FROM history')]
        self.current = {}

    def track(self, row, now):
        def same(old):
            if row['url'] and row['url'] == old['url']:
                return True
            if fingerprint(row) == fingerprint(old):
                return True
            same_owner = row['author_or_company'] != 'UNKNOWN' and row['author_or_company'].casefold() == old['author_or_company'].casefold()
            same_company = domain(row['website']) and domain(row['website']) == domain(old['website'])
            return bool((same_owner or same_company) and len(row['title']) >= 20 and SequenceMatcher(None, row['title'].casefold(), old['title'].casefold()).ratio() >= .92 and SequenceMatcher(None, row['text'].casefold(), old['text'].casefold()).ratio() >= .72)
        found = next(((key, old) for key, old in self.rows if same(old)), None)
        row = dict(row)
        row.update(status='NEW', first_seen_at=now, last_seen_at=now, important_update=False)
        if found:
            key, old = found
            changed = any(row.get(k) != old.get(k) for k in ('text', 'title', 'company', 'website', 'country', 'deadline'))
            row['status'] = 'UPDATED' if changed else 'SEEN'
            row['first_seen_at'] = old['first_seen_at']
            row['important_update'] = changed and (row['category'] != old['category'] or row['payment_signal'] != old['payment_signal'] or row.get('deadline', '') != old.get('deadline', '') or any(abs(float(row[k])-float(old[k])) >= 1 for k in SCORES))
            if key in self.current:
                prior = self.current[key]
                row['status'] = prior['status'] if prior['status'] == 'NEW' or not changed else row['status']
                row['important_update'] |= prior['important_update']
            self.db.execute('UPDATE history SET payload=? WHERE id=?', (json.dumps(row, ensure_ascii=False), key))
            self.rows = [(k, row if k == key else v) for k, v in self.rows]
        else:
            key = self.db.execute('INSERT INTO history(payload) VALUES (?)', (json.dumps(row, ensure_ascii=False),)).lastrowid
            self.rows.append((key, row))
        self.current[key] = row
        return row

    def close(self, commit=True):
        self.db.commit() if commit else self.db.rollback()
        self.db.close()


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class JsonHistory(History):
    """Same identity engine, sorted JSONL on disk; atomic replacement, no server."""
    def __init__(self, path, *, preview=False):
        self.path = Path(path)
        self.lock = None
        if os.environ.get('RADAR_TEST_MODE') == '1' and self.path.resolve().is_relative_to(Path(__file__).resolve().parents[1]/'data'):
            raise ValueError('Tests cannot access production history')
        if not preview:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lock = self.path.with_suffix('.lock')
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            self.lock = lock
        try:
            super().__init__(':memory:', preview=preview)
            if self.path.exists():
                for line in self.path.read_text(encoding='utf-8').splitlines():
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    if not isinstance(item, dict) or not {'id', 'record'} <= item.keys():
                        raise ValueError('Invalid JSONL history; do not reset production state')
                    row = item['record']
                    required = {'text', 'title', 'url', 'author_or_company', 'website', 'first_seen_at', 'category', 'payment_signal', *SCORES}
                    if not isinstance(row, dict) or not required <= row.keys():
                        raise ValueError('Incomplete JSONL history record')
                    self.db.execute('INSERT INTO history(id,payload) VALUES (?,?)', (item['id'], json.dumps(row, ensure_ascii=False)))
                self.rows = [(key, json.loads(payload)) for key, payload in self.db.execute('SELECT id,payload FROM history ORDER BY id')]
        except Exception:
            if hasattr(self, 'db'):
                self.db.close()
            if self.lock:
                self.lock.unlink(missing_ok=True)
            raise

    def close(self, commit=True):
        try:
            if commit and not self.preview:
                temporary = self.path.with_suffix('.jsonl.tmp')
                try:
                    with temporary.open('w', encoding='utf-8', newline='\n') as f:
                        for key, row in sorted(self.rows):
                            f.write(json.dumps({'id': key, 'record': row}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))+'\n')
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(temporary, self.path)
                finally:
                    temporary.unlink(missing_ok=True)
        finally:
            self.db.close()
            if self.lock:
                self.lock.unlink(missing_ok=True)
