"""UTF-8 CSV and Markdown reports, with fresh evidence first."""
import csv
import re
from collections import Counter
from datetime import datetime, timezone, timedelta

from radar.core import FIELDS, domain, quality_gate, signal_priority, quality_evidence
from radar.intelligence import enrich_history, eligible, cash_candidate, pain_clusters, date as parse_date


def write_csv(path, rows, fields):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            # Keep spreadsheet applications from evaluating pasted content as formulas.
            writer.writerow({k: "'" + v if isinstance(v, str) and v.lstrip().startswith(('=', '+', '-', '@')) else v for k, v in row.items() if k in fields})


def md(value):
    return re.sub(r'([\\`*_{}\[\]<>#|])', r'\\\1', str(value)).replace('\n', ' ')


def fresh(r):
    deadline = parse_date(r.get('deadline'))
    if deadline and deadline <= datetime.now(timezone.utc):
        return False
    try:
        date = datetime.fromisoformat(r['published_at'].replace('Z', '+00:00'))
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        if not datetime.now(timezone.utc) - timedelta(days=30) <= date <= datetime.now(timezone.utc):
            return False
    except (ValueError, TypeError):
        pass  # Unknown dates remain explicitly marked for human verification.
    return r['status'] == 'NEW' or (r['status'] == 'UPDATED' and r['important_update'])


def ranked(rows, category, only_fresh=False, preview=False):
    return sorted((r for r in rows if category in r['category'].split(';') and (category not in ('money', 'pain') or quality_gate(r, category)) and (category != 'money' or cash_candidate(r)) and (not only_fresh or fresh(r))), key=lambda r: (0 if preview else -int(fresh(r)), -r.get('cash_score' if category == 'money' else 'market_score', 0), -r.get('validation_value', 0), *(-v for v in signal_priority(r)), r['url'], r['title']))


def card(r, category):
    source = f"[原文]({r['url'].replace(')', '%29').replace('(', '%28')})" if r['url'] else '未附 URL，先補原文'
    base = f"### {md(r['title'])}\n\n{source} · {r['status']} · {r['total_score']}/5 · 發布：{md(r['published_at'])}\n\n"
    base += f"Cash: {r.get('cash_score', 0)}/5 · Market: {r.get('market_score', 0)}/5 · {md(r.get('solo_fit', 'UNKNOWN'))}\n\n"
    if not quality_gate(r):
        base += '**LOW CONFIDENCE / NOT ACTIONABLE**\n\n'
    if category == 'money':
        detail = [('對方要什麼', r['workflow']), ('為什麼可能願意付錢', r['payment_signal']), ('我們可以賣什麼（假設）', r['possible_offer']), ('是否值得聯絡', '先人工核對時效、身份、預算，再決定' if r['score_payment_intent'] >= 4 else '先確認是否有預算，不直接當成付費客戶')]
    elif category == 'uk':
        detail = [('Company / buyer', r['company']), ('Buyer type', r.get('buyer_type') or 'Small company (verify)'), ('Website', r['website']), ('需求證據', r.get('evidence_text') or r['workflow']), ('Offer（假設）', r['possible_offer']), ('Deadline', r.get('deadline') or 'UNKNOWN'), ('Public contact', r['contact_page_or_public_contact'] or 'UNKNOWN')]
    else:
        detail = [('Pain', r['pain_summary']), ('現在如何解決', r['current_workaround']), ('痛點強度', str(r['score_pain_strength'])+'/5'), ('是否有人付費', r['payment_signal']), ('現有競爭（僅原文提及）', r['competitor']), ('SaaS 機會（假設）', r['saas_signal']), ('我們是否值得追', '先訪談確認重複頻率與付費；尚未驗證市場')]
    return base + '\n'.join(f'- {label}：{md(value)}' for label, value in detail) + '\n\n'


def action_queue(rows, clusters, preview=False):
    actions, used = [], set()
    current = {r.get('evidence_url') or r['url'] for r in rows if fresh(r)}
    for row in sorted(rows, key=lambda r: (r.get('cash_score', 0), r.get('validation_value', 0), signal_priority(r)), reverse=True):
        if not cash_candidate(row) or not quality_gate(row, 'pain') or not fresh(row) or preview:
            continue
        url = row.get('evidence_url') or row.get('url')
        route = row.get('contact_page_or_public_contact')
        if not route and domain(url) in {'news.ycombinator.com', 'x.com', 'reddit.com'}:
            route = 'Public source thread (check replies are open): '+url
        if not url or not route or route == 'UNKNOWN':
            continue
        actions.append(('CONTACT', row, 'Explicit commercial request; new or meaningfully updated evidence.', route))
        used.add(url)
        if len(actions) == 3:
            return actions
    for cluster in clusters:
        candidates = [r for r in cluster['members'] if eligible(r) and fresh(r) and (r.get('evidence_url') or r['url']) in current - used]
        if preview or not cluster['worthy'] or not candidates:
            continue
        row = max(candidates, key=lambda r: (r.get('market_score', 0), r.get('validation_value', 0)))
        action = 'VALIDATE' if row.get('market_score', 0) >= 3.5 else 'WATCH'
        actions.append((action, row, f"{cluster['key']}: {cluster['independent_users']} independent users / {cluster['source_count']} sources in 30 days; verify the shared need.", row.get('contact_page_or_public_contact') or 'Evidence thread; verify an available public reply route before contact.'))
        used.add(row.get('evidence_url') or row['url'])
        if len(actions) == 3:
            break
    return actions


def queue_markdown(actions):
    body = "## Today's Action Queue\n\n"
    if not actions:
        return body + 'NO ACTION REQUIRED TODAY\n\n'
    for action, row, why, route in actions:
        body += f"### {action}: {md(row.get('author_or_company') or 'UNKNOWN')}\n\n"
        for label, value in [('Who', row.get('company') or row.get('author_or_company')), ('Need', row.get('pain_summary') if row.get('pain_summary') != 'UNKNOWN' else row.get('workflow')), ('Evidence', row.get('evidence_url') or row['url']), ('Why now', why), ('What we can offer', row.get('possible_offer')), ('Contact route', route), ('Confidence', 'MEDIUM — rule-based evidence; verify scope, date and identity')]:
            rendered = '[source]('+str(value).replace('(', '%28').replace(')', '%29')+')' if label == 'Evidence' else md(value)
            body += f'- {label}: {rendered}\n'
        body += '\n'
    return body


def diagnostic_need(row):
    """Human-review recall only. Never used by classification or Action Queue."""
    text = row.get('text', '')
    if re.search(r'\b(?:i|we) (?:built|launched)|seeking work|available for hire|willing to relocate', text, re.I):
        return ''
    for sentence in re.split(r'(?<=[.!?])\s+|\n', text):
        if (re.search(r'\b(?:i|we) need (?:to|an? (?:tool|script|developer|service|freelancer))|i.ve noticed.{0,100}(?:failure|problem)|how are others handling this', sentence, re.I)
                and re.search(r'workflow|code|coding|agent|output|export|import|invoice|spreadsheet|script|software', sentence, re.I)):
            return sentence
    return ''


def rejection_reasons(row, action_urls=()):
    """Ordered bottlenecks: content/scope before score, recurrence and dedupe."""
    ev = quality_evidence(row)
    reasons = []
    if row.get('feedback') in {'BAD', 'IGNORED', 'CONTACTED', 'REPLIED', 'TESTER', 'PAID'}:
        reasons.append('FEEDBACK_SUPPRESSED')
    if row.get('solo_fit') == 'TOO_LARGE':
        reasons.append('SCOPE_TOO_LARGE')
    if re.search(r'\b(?:i|we) (?:built|launched)|seeking work|available for hire|willing to relocate', row['text'], re.I):
        reasons.append('SELLER_OR_SELF_PROMOTION')
    if row.get('source') == 'weworkremotely' and not cash_candidate(row):
        reasons.append('ROLE_NOT_SCOPED_SERVICE')
    need = bool(quality_gate(row, 'pain') or ev['request'] or ev['replacement'])
    if not need:
        reasons.append('UNRECOGNIZED_NEED_REVIEW' if diagnostic_need(row) else 'NO_CONCRETE_BUYER_OR_USER_NEED')
    if not quality_gate(row):
        reasons.append('BELOW_ACTIONABILITY_GATE')
    if not ev['payment']:
        reasons.append('NO_COMMERCIAL_SIGNAL')
    if row.get('solo_fit') != 'SOLO_FIT':
        reasons.append('SOLO_FIT_UNPROVEN')
    if row.get('feedback_penalty'):
        reasons.append('SIMILAR_NEGATIVE_FEEDBACK')
    if not fresh(row):
        reasons.append('SEEN_OR_EXPIRED')
    url = row.get('evidence_url') or row.get('url')
    if url in action_urls:
        return 'NOT_REJECTED', []
    reasons.append('NO_QUALIFIED_ACTION_ROUTE_OR_CLUSTER')
    return reasons[0], reasons[1:]


def near_misses(rows, action_urls=()):
    candidates = []
    for row in rows:
        primary, secondary = rejection_reasons(row, action_urls)
        ev = quality_evidence(row)
        if (float(row.get('total_score', 0)) >= 1.5 and primary not in {'NOT_REJECTED', 'FEEDBACK_SUPPRESSED', 'SCOPE_TOO_LARGE', 'SELLER_OR_SELF_PROMOTION', 'ROLE_NOT_SCOPED_SERVICE'}
                and (diagnostic_need(row) or ev['pain'] or ev['request'] or ev['replacement'])):
            candidates.append((row, primary, secondary))
    return sorted(candidates, key=lambda item: (float(item[0].get('total_score', 0)), item[0]['url']), reverse=True)[:5]


def market_top10(output, rows, now):
    """Five-axis review export; never changes existing scores or Action Queue gates."""
    selected = []
    moment = parse_date(now) or datetime.now(timezone.utc)
    excluded = r'\b(?:crypto(?:currency)?|bitcoin|ethereum|airdrop|nft|politics|political|election|senator|president|full.time|part.time|salary|resume|résumé|seeking work|promotion|promo|discount)\b|apply now|apply here|join our team|we offer|our services|buy now|sign up|sponsored|referral|\b(?:i|we) (?:built|launched)\b'
    for row in rows:
        if (not row.get('url') or not eligible(row) or row.get('source') == 'weworkremotely'
                or row.get('source_type') in {'job', 'public_job', 'public_tender'}
                or re.search(excluded, row['title']+' '+row['text'], re.I)
                or not {'money', 'pain'} & set(row.get('category', '').split(';'))):
            continue
        published, deadline = parse_date(row.get('published_at')), parse_date(row.get('deadline'))
        if (published and not moment-timedelta(days=30) <= published <= moment) or (deadline and deadline <= moment):
            continue
        evidence = quality_evidence(row)
        urgent = next((s for s in re.split(r'(?<=[.!?])\s+', row['text'])
                       if re.search(r'\b(?:i|we|our|my)\b', s, re.I)
                       and re.search(r'\b(?:urgent|asap|by tomorrow|this week|blocked|deadline)\b', s, re.I)
                       and not re.search(r'not urgent|no deadline|not blocked', s, re.I)), '')
        scores = dict(Pain=float(row['score_pain_strength']) if evidence['pain'] else 0,
                      WTP=float(row['score_payment_intent']) if evidence['payment'] else 0,
                      Workaround=5 if evidence['workaround'] and evidence['repeat'] else 3 if evidence['workaround'] else 0,
                      Urgency=5 if deadline and deadline <= moment+timedelta(days=7) else 3 if urgent else 0,
                      Fit=float(row['score_user_fit']))
        action = ('WATCH — already seen; verify new evidence before contacting' if not fresh(row) else
                  'CONTACT — verify identity, budget and scope before proposing a small pilot' if cash_candidate(row) else
                  'VALIDATE — ask about frequency, current process and willingness to pay; not yet an Action Queue recommendation')
        selected.append(dict(title=row['title'], source_url=row['url'], evidence=row['text'], status=row['status'],
                             **scores, v1_score=round(sum(scores.values())/5, 2),
                             pain_evidence=evidence['pain'] or 'UNKNOWN',
                             wtp_evidence=(row['payment_signal'] if row.get('payment_signal') not in ('', 'UNKNOWN', None) else evidence['payment']) if scores['WTP'] else 'UNKNOWN',
                             workaround_evidence=evidence['workaround'] or 'UNKNOWN',
                             urgency_evidence=('Deadline: '+row['deadline']) if scores['Urgency'] == 5 else urgent or 'UNKNOWN',
                             recommended_next_action=action))
    selected.sort(key=lambda r: (-r['v1_score'], -r['WTP'], -r['Pain'], r['source_url']))
    top = [dict(rank=i, **r) for i, r in enumerate(selected[:10], 1)]
    fields = ['rank', 'title', 'source_url', 'evidence', 'status', 'Pain', 'WTP', 'Workaround', 'Urgency', 'Fit', 'v1_score',
              'pain_evidence', 'wtp_evidence', 'workaround_evidence', 'urgency_evidence', 'recommended_next_action']
    write_csv(output/'market_top10.csv', top, fields)
    report = '# Market Radar v1 — Top 10\n\n'+f'UTC: {now}\n\n'
    report += 'Research shortlist, not validated demand. Each axis is 0–5; v1_score is their equal-weight mean. Missing evidence scores 0. SEEN remains visible for review; existing Action Queue gates are unchanged.\n\n'
    for row in top:
        report += f"## {row['rank']}. {md(row['title'])}\n\n[Source]({row['source_url'].replace('(', '%28').replace(')', '%29')}) · {row['status']}\n\n"
        report += ' · '.join(f'{k}: {row[k]}/5' for k in ['Pain', 'WTP', 'Workaround', 'Urgency', 'Fit', 'v1_score'])+'\n\n'
        for label in ['evidence', 'pain_evidence', 'wtp_evidence', 'workaround_evidence', 'urgency_evidence', 'recommended_next_action']:
            report += f'- {label}: {md(row[label])}\n'
        report += '\n'
    if not top:
        report += 'No qualifying market signals.\n'
    (output/'market_top10.md').write_text(report.rstrip()+'\n', encoding='utf-8')
    return top


def reports(output, rows, stats, pending, now, *, preview=False, mobile=False, history_rows=None):
    rows = enrich_history(rows)
    historical = enrich_history(history_rows if history_rows is not None else rows)
    # Similarity penalties require the full history, including negatives absent today.
    enriched = {r['url']: r for r in historical if r['url']}
    rows = [dict(r, **{k: enriched[r['url']][k] for k in ('cash_score', 'market_score', 'feedback_penalty')}) if r['url'] in enriched else r for r in rows]
    clusters = pain_clusters(historical, now)
    queue = queue_markdown(action_queue(rows, clusters, preview))
    health = 'DEGRADED' if pending or any(re.search(r'failed|error|timeout|capped|malformed skipped: [1-9]', s, re.I) for s in stats) else 'HEALTHY'
    output.mkdir(parents=True, exist_ok=True)
    market_top10(output, rows, now)
    (output/'action_queue.md').write_text(queue, encoding='utf-8')
    cluster_text = '## Repeated Market Pain\n\n30-day window; unknown publication dates use first seen. Unknown authors do not count as independent users.\n\n'
    for cluster in clusters:
        cluster_text += f"### {md(cluster['key'])}\n\n"
        for key in ('independent_users', 'source_count', 'signal_count', 'workaround_count', 'payment_count', 'validation_value', 'confidence'):
            cluster_text += f"- {key}: {md(cluster[key])}\n"
        cluster_text += '- Worth validating: '+('YES' if cluster['worthy'] else 'NO')+'\n'
        cluster_text += '- Example URLs: '+', '.join('[source]('+ (r.get('evidence_url') or r['url']).replace('(', '%28').replace(')', '%29') +')' for r in cluster['members'][:3])+'\n\n'
    if not clusters:
        cluster_text += 'No repeated concrete pain found in the last 30 days.\n\n'
    (output/'pain_clusters.md').write_text(cluster_text, encoding='utf-8')
    actual_actions = action_queue(rows, clusters)
    action_urls = {r.get('evidence_url') or r['url'] for _, r, _, _ in actual_actions}
    reasons, secondary_counts = Counter(), Counter()
    details = []
    for row in rows:
        primary, secondary = rejection_reasons(row, action_urls)
        row['primary_rejection_reason'] = primary
        row['secondary_rejection_reasons'] = ';'.join(secondary)
        reasons[primary] += 1
        secondary_counts.update(secondary)
        details.append(f"| {md(row['url'])} | {primary} | {', '.join(secondary)} |")
    summary = '# Filter Summary\n\n'+f'Processed unique: {len(rows)}\nExactly one primary outcome per record; NOT_REJECTED means queued. Content/scope precede score, recurrence and SEEN.\n\n## Primary reasons (exclusive)\n\n'
    summary += '\n'.join(f'- {reason}: {count}' for reason, count in reasons.most_common())+f'\n- TOTAL: {sum(reasons.values())}\n\n## Secondary reasons (overlapping)\n\n'
    summary += '\n'.join(f'- {reason}: {count}' for reason, count in secondary_counts.most_common())+'\n\n## Source health\n\n'+'\n'.join('- '+md(s) for s in stats)+'\n'
    summary += '\n## Per-record diagnosis\n\n| URL | Primary | Secondary |\n| --- | --- | --- |\n'+'\n'.join(details)+'\n'
    (output/'filter_summary.md').write_text(summary, encoding='utf-8')
    write_csv(output/'opportunities.csv', rows, FIELDS+['primary_rejection_reason', 'secondary_rejection_reasons'])
    diagnostics = '# Near Misses\n\nLOW CONFIDENCE / NOT ACTIONABLE — diagnostic only, never an Action Queue input. Ranked by existing total score; no quota filling.\n\n'
    moment = parse_date(now) or datetime.now(timezone.utc)
    recent_history = [r for r in historical if (parse_date(r.get('published_at')) or parse_date(r.get('first_seen_at')) or datetime.min.replace(tzinfo=timezone.utc)) >= moment-timedelta(days=30)]
    misses = near_misses(recent_history, action_urls)
    for row, primary, secondary in misses:
        diagnostics += f"### {md(row['title'])}\n\n[Evidence]({row['url'].replace(')', '%29')}) · {row['total_score']}/5\n\n- Last observed: {md(row.get('last_seen_at') or 'UNKNOWN')} (30-day history; may not be in today's feed)\n- Rejected reason: {primary}\n- Secondary: {', '.join(secondary)}\n- Evidence to review: {md(diagnostic_need(row) or row.get('pain_summary') or row['text'][:400])}\n\n"
    if not misses:
        diagnostics += 'No near-threshold candidates with concrete demand evidence.\n'
    (output/'near_misses.md').write_text(diagnostics.rstrip()+'\n', encoding='utf-8')
    write_csv(output/'pending_intake.csv', pending, ['url', 'reason'])
    sections = {}
    for cat, filename, heading in [('money', 'money_radar', '💰 Money Radar Top 5'), ('uk', 'uk_leads', '🇬🇧 UK Lead Radar Top 5'), ('pain', 'pain_radar', '🔥 Pain Radar Top 5'), ('ledgerdrop', 'ledgerdrop_validation', '🧪 LedgerDrop Validation')]:
        selected = ranked(rows, cat, preview=preview)
        if cat == 'uk':
            unique = {}
            for r in selected:
                if not r.get('evidence_url'):
                    continue
                unique.setdefault(domain(r['website']), r)
            selected = list(unique.values())
            export = [dict(r, detected_need=r['need_type'], evidence=r['pain_signal'] if r['pain_signal'] != 'UNKNOWN' else r['workflow'], estimated_fit=r['score_user_fit'], score=r['total_score']) for r in selected]
            fields = ['company', 'website', 'country', 'evidence_url', 'detected_need', 'evidence_text', 'evidence', 'possible_offer', 'estimated_fit', 'contact_page_or_public_contact', 'score', 'url', 'status', 'buyer_type', 'deadline']
        elif cat == 'ledgerdrop':
            export = [dict(r, pain=r['pain_summary'], workaround=r['current_workaround'], willingness_to_pay=r['payment_signal']) for r in selected]
            fields = FIELDS + ['pain', 'workaround', 'willingness_to_pay']
        else:
            export, fields = selected, FIELDS
        write_csv(output/(filename+'.csv'), export, fields)
        top = [r for r in selected if (preview or fresh(r)) and (cat == 'ledgerdrop' or (cat == 'pain' and r.get('feedback') not in {'CONTACTED', 'REPLIED', 'TESTER', 'PAID'}) or eligible(r)) and r.get('feedback') not in {'BAD', 'IGNORED'}][:5]
        section = f'## {heading}\n\n' + (''.join(card(r, cat) for r in top) or '今日沒有新的合格訊號；不補入舊資料或示範資料。\n\n')
        section += f'本次合格 {len(selected)} 筆；其餘 SEEN／非重要更新請看 CSV。\n\n'
        sections[cat] = section
        if cat != 'ledgerdrop':
            name = filename + ('_top.md' if cat == 'uk' else '_top5.md')
            (output/name).write_text(section, encoding='utf-8')
    candidates = sorted([r for r in rows if r['category'].strip(';') and eligible(r) and not r.get('feedback_penalty') and quality_gate(r) and (preview or fresh(r))], key=signal_priority, reverse=True)
    strongest = card(candidates[0], 'uk' if 'uk' in candidates[0]['category'].split(';') else 'money' if 'money' in candidates[0]['category'] else 'pain') if candidates else 'No actionable signal today.\n\n今日無新增或重要更新的可行動訊號。\n\n'
    report = '# Opportunity Radar\n\n'+f'**{health}**\n\n' + f'執行時間 UTC：{now}。評分為保守規則估計，所有分數 0–5；total 為五項平均。\n\n'
    if preview:
        report += '> FRESH PREVIEW：包含 SEEN，僅供人工 review；未修改正式 history 或 daily report。\n\n'
    else:
        report += '[查看目前最佳候選（含 SEEN，只讀預覽）](preview/daily_report.md)\n\n'
    report += '[Market Radar v1 — Top 10](market_top10.md)\n\n' + queue + '## Cash Now\n\n' + sections['money']
    worthy = [c for c in clusters if c['worthy']]
    report += '## Repeated Market Pain\n\n' + ('\n'.join(f"- {md(c['key'])}: {c['independent_users']} independent users; {c['signal_count']} signals; {md(c['confidence'])}" for c in worthy[:5]) if worthy else 'No cluster meets the independent-evidence gate.') + '\n\n[30-day evidence](pain_clusters.md)\n\n'
    report += '## LedgerDrop\n\n' + sections['ledgerdrop']
    report += '## System Health\n\n'+health+'\n\n'+'\n'.join('- '+md(s) for s in stats)+'\n\n[Filter summary](filter_summary.md) · [Near misses — diagnostic only](near_misses.md) · [Full CSV](opportunities.csv)\n\n'
    report += '## 🚨 Strongest Signal Today\n\n' + strongest
    report += '## 🎯 Recommended Action\n\n' + ('See Today\'s Action Queue above.' if action_queue(rows, clusters, preview) else 'NO ACTION REQUIRED TODAY')
    report += '\n\n' + sections['uk'] + sections['pain']
    if preview:
        for name in ('pain_clusters.md', 'filter_summary.md', 'opportunities.csv', 'near_misses.md', 'market_top10.md'):
            report = report.replace(']('+name+')', '](../'+name+')')
    (output/'daily_report.md').write_text(report, encoding='utf-8')
