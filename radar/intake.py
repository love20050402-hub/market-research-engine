"""Bounded public collection and manual/legacy intake. No login or paid API."""
import csv
import ipaddress
import json
import re
import socket
import time
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from urllib.parse import urlsplit, parse_qs
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.robotparser import RobotFileParser

from collectors.hackernews import HackerNewsCollector, clean_text
from radar.core import canonical_url, domain

AGENT = 'OpportunityRadar/1.0'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def get_public(url):
    p = urlsplit(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None, 443):
        raise ValueError('only public HTTPS URLs without credentials are supported')
    addresses = socket.getaddrinfo(p.hostname, 443)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('non-public address rejected')
    with build_opener(NoRedirect).open(Request(url, headers={'User-Agent': AGENT}), timeout=12) as response:
        data = response.read(1_000_001)
        if len(data) > 1_000_000:
            raise ValueError('response exceeds 1 MB limit')
        return data.decode('utf-8', errors='replace'), response.headers.get_content_type()


class PageText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript'):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript'):
            self.hidden = max(0, self.hidden-1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def load_records(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        if path.suffix.lower() == '.csv':
            yield from csv.DictReader(f)
        else:
            data = json.load(f)
            if not isinstance(data, list):
                raise ValueError('JSON import must be an array of records')
            yield from data


def manual_records(root, offline, logger):
    records, pending = [], []
    folder = root / 'input'
    path = folder / 'manual_text.txt'
    if path.exists():
        for block in re.split(r'(?m)^\s*---\s*$', path.read_text(encoding='utf-8-sig')):
            lines = [line for line in block.strip().splitlines() if not line.startswith('#')]
            row, body = {'source': 'manual'}, []
            for line in lines:
                key, sep, value = line.partition(':')
                if sep and key.strip().lower() in {'url', 'title', 'author', 'company', 'website', 'country', 'published_at', 'contact_page_or_public_contact'}:
                    row[key.strip().lower()] = value.strip()
                else:
                    body.append(line)
            if any(s.strip() for s in body):
                row['text'] = '\n'.join(body)
                records.append(row)
    supplied = {canonical_url(r.get('url', '')) for r in records}
    urls = folder / 'manual_urls.txt'
    if urls.exists():
        seen = set()
        for line in urls.read_text(encoding='utf-8-sig').splitlines():
            raw = line.strip()
            if not raw or raw.startswith('#') or raw in seen:
                continue
            seen.add(raw)
            url = canonical_url(raw)
            host = domain(url)
            reason = ''
            if url and url in supplied:
                continue
            if not url:
                reason = 'Invalid URL'
            elif offline:
                reason = 'Offline: paste post text'
            elif host in {'x.com', 'reddit.com', 'old.reddit.com', 'm.reddit.com', 'redd.it'} or host.endswith('.reddit.com'):
                reason = 'Restricted source: paste actual post text with URL in manual_text.txt'
            elif len(seen) > 10:
                reason = 'Maximum 10 manual URL entries per run; paste text or reduce intake'
            else:
                try:
                    if host == 'news.ycombinator.com':
                        item_id = parse_qs(urlsplit(url).query).get('id', [''])[0]
                        if not item_id.isdigit():
                            raise ValueError('HN item URL must include numeric id')
                        payload, _ = get_public('https://hn.algolia.com/api/v1/items/' + item_id)
                        item = json.loads(payload)
                        records.append({'source': 'hackernews', 'url': url, 'title': item.get('title'), 'text': clean_text(item.get('text')), 'author': item.get('author'), 'created_at': item.get('created_at')})
                    else:
                        origin = 'https://' + urlsplit(url).netloc
                        robots, _ = get_public(origin + '/robots.txt')
                        rules = RobotFileParser()
                        rules.parse(robots.splitlines())
                        if not rules.can_fetch(AGENT, url):
                            raise ValueError('robots.txt disallows fetch')
                        delay = max(1, rules.crawl_delay(AGENT) or 1)
                        if delay > 10:
                            raise ValueError('robots crawl-delay exceeds bounded fetch budget; paste text')
                        time.sleep(delay)
                        body, content_type = get_public(url)
                        if content_type not in ('text/html', 'text/plain'):
                            raise ValueError('unsupported content type; paste text')
                        parser = PageText()
                        parser.feed(body)
                        records.append({'source': host, 'source_type': 'public_page', 'url': url, 'text': clean_text(' '.join(parser.parts))})
                except Exception as exc:
                    reason = f'Fetch failed ({type(exc).__name__}); paste text'
                    logger.warning('manual URL %s: %s', host, reason)
            if reason:
                pending.append({'url': url or '[invalid URL]', 'reason': reason})
    return records, pending


def collect_hn(logger):
    records, stats = [], []
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    for query in ('willing to pay', 'need help', 'manual', 'looking for alternative'):
        try:
            collector = HackerNewsCollector({'endpoint': 'https://hn.algolia.com/api/v1/search_by_date', 'query': query, 'tags': '(story,comment)', 'hits_per_page': 30, 'timeout_seconds': 12, 'user_agent': AGENT})
            hits = collector.fetch_and_normalize()
            recent = []
            for hit in hits:
                try:
                    date = datetime.fromisoformat(hit['created_at'].replace('Z', '+00:00'))
                    if date >= cutoff:
                        recent.append(hit)
                except (ValueError, TypeError):
                    continue
            records.extend(recent)
            stats.append(f'HN / {query}: OK, {len(recent)} records within 30 days')
        except Exception as exc:
            logger.warning('HN query %s failed: %s', query, type(exc).__name__)
            stats.append(f'HN / {query}: FAILED ({type(exc).__name__})')
        time.sleep(1)
    return records, stats
