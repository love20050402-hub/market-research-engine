"""Read trusted repository Issues as data only; never execute or fetch pasted URLs."""
import json
import os
import re
from urllib.request import Request, build_opener

from radar.intake import NoRedirect
from radar.core import canonical_url
from radar.intelligence import FEEDBACK, date


def parse_feedback(issue, owner):
    if not isinstance(issue, dict) or 'pull_request' in issue:
        return None
    if not re.match(r'^Radar Feedback(?:\s|:|$)', str(issue.get('title', '')), re.I):
        return None
    if issue.get('user', {}).get('login') != owner and issue.get('author_association') not in {'OWNER', 'MEMBER', 'COLLABORATOR'}:
        return None
    body = issue.get('body') or ''
    if not isinstance(body, str) or len(body) > 20000:
        raise ValueError('Invalid feedback body')
    fields = dict((k.strip().upper(), v.strip()) for k, v in re.findall(r'^([^:\n]+):([^\n]*)$', body, re.M))
    url, status = canonical_url(fields.get('URL', '')), fields.get('STATUS', '').upper()
    updated = date(issue.get('updated_at'))
    if not url or status not in FEEDBACK or not updated:
        raise ValueError('Feedback requires URL, STATUS and Issue update time')
    return dict(url=url, feedback=status, feedback_at=updated.isoformat(), feedback_issue=canonical_url(issue.get('html_url', '')))


def parse_issue(issue, owner):
    if not isinstance(issue, dict) or 'pull_request' in issue:
        return None
    if not re.match(r'^Radar Intake(?:\s|:|$)', str(issue.get('title', '')), re.I):
        return None
    if issue.get('user', {}).get('login') != owner and issue.get('author_association') not in {'OWNER', 'MEMBER', 'COLLABORATOR'}:
        return None
    body = issue.get('body') or ''
    if not isinstance(body, str) or len(body) > 20000:
        raise ValueError('Issue body must be text, at most 20,000 characters')
    # Only parse metadata before TEXT:, preserving URLs/colons within the actual post.
    head, sep, text = body.partition('TEXT:')
    if not sep:
        parts = re.split(r'(?im)^text:\s*', body, maxsplit=1)
        if len(parts) != 2:
            raise ValueError('Missing TEXT: and original post content')
        head, text = parts
    row = {'source': 'github_issue', 'source_type': 'manual_post', 'title': '', 'text': text.strip()}
    for line in head.splitlines():
        key, sep, value = line.partition(':')
        key = key.strip().lower()
        if sep and key in {'url', 'author', 'company', 'website', 'country', 'published_at', 'title', 'contact_page_or_public_contact'}:
            row[key] = value.strip()
    if len(row['text']) < 25:
        raise ValueError('Paste actual post text, not just the URL')
    row['url'] = canonical_url(row.get('url', ''))
    if not row['url']:
        raise ValueError('A valid public evidence URL is required')
    # Do not use Issue creation time as the publication time of copied source evidence.
    row.setdefault('published_at', 'UNKNOWN')
    return row


def collect_issues(repository, logger, *, opener=None, feedback=None):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Repository must be owner/name')
    opener = opener or build_opener(NoRedirect).open
    records, pending, stats = [], [], []
    headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'OpportunityRadar/2.0', 'X-GitHub-Api-Version': '2022-11-28'}
    token = os.environ.get('GITHUB_TOKEN')
    if token:
        headers['Authorization'] = 'Bearer '+token
    try:
        for page in (1, 2):
            url = f'https://api.github.com/repos/{repository}/issues?state=all&sort=updated&direction=desc&per_page=100&page={page}'
            with opener(Request(url, headers=headers), timeout=12) as response:
                content = response.read(5_000_001)
            if len(content) > 5_000_000:
                raise ValueError('Issue response too large')
            payload = json.loads(content)
            if not isinstance(payload, list):
                raise ValueError('Issue response must be a list')
            for issue in payload:
                try:
                    response = parse_feedback(issue, repository.split('/')[0])
                    if response and feedback is not None:
                        feedback.append(response)
                    row = parse_issue(issue, repository.split('/')[0]) if isinstance(issue, dict) and issue.get('state', 'open') == 'open' else None
                    if row:
                        records.append(row)
                except (ValueError, TypeError, AttributeError):
                    pending.append({'url': canonical_url(issue.get('html_url', '')) if isinstance(issue, dict) else '', 'reason': 'Issue malformed; Intake needs URL:/TEXT:, Feedback needs URL:/STATUS: (max 20,000 chars)'})
            if len(payload) < 100:
                break
            if page == 2:
                stats.append('Issues: capped at 200 most recently updated entries; older saved feedback retained, edit an older Issue to resurface it')
        stats.append(f'GitHub Issues: OK, {len(records)} trusted intake records; {len(pending)} pending')
    except Exception as exc:
        logger.warning('GitHub Issues failed: %s', type(exc).__name__)
        stats.append(f'GitHub Issues: FAILED ({type(exc).__name__}); manual files still work')
    return records, pending, stats
