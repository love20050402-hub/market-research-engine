"""UTF-8 CSV and Markdown reports, with fresh evidence first."""
import csv
import re
from collections import Counter
from datetime import datetime, timezone, timedelta

from radar.core import FIELDS, domain, quality_gate, signal_priority, NON_MARKET
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
        if not cash_candidate(row) or not fresh(row) or preview:
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
    reasons = Counter()
    for row in rows:
        if not quality_gate(row): reasons['Insufficient actionable evidence / score < 3.5'] += 1
        if re.search(NON_MARKET, row['title'], re.I) and not quality_gate(row, 'pain') and not quality_gate(row, 'money'): reasons['Non-market discussion without concrete user need'] += 1
        if not quality_gate(row, 'money'): reasons['No explicit commercial request'] += 1
        if not quality_gate(row, 'pain'): reasons['No concrete user pain'] += 1
        if row.get('solo_fit') != 'SOLO_FIT': reasons['Solo fit not established / scope too large'] += 1
        if not eligible(row): reasons['Feedback suppression / too large'] += 1
        if row.get('feedback_penalty'): reasons['Similar negative feedback penalty'] += 1
        if not fresh(row): reasons['SEEN / not important / expired age'] += 1
    summary = '# Filter Summary\n\n'+f'Processed unique: {len(rows)}\nReasons overlap; full evidence remains in CSV.\n\n'
    summary += '\n'.join(f'- {reason}: {count}' for reason, count in reasons.items())+'\n\n'+'\n'.join('- '+md(s) for s in stats)+'\n'
    (output/'filter_summary.md').write_text(summary, encoding='utf-8')
    write_csv(output/'opportunities.csv', rows, FIELDS)
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
        top = [r for r in selected if (preview or fresh(r)) and (cat == 'ledgerdrop' or eligible(r)) and r.get('feedback') not in {'BAD', 'IGNORED'}][:5]
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
    report += queue + '## Cash Now\n\n' + sections['money']
    worthy = [c for c in clusters if c['worthy']]
    report += '## Repeated Market Pain\n\n' + ('\n'.join(f"- {md(c['key'])}: {c['independent_users']} independent users; {c['signal_count']} signals; {md(c['confidence'])}" for c in worthy[:5]) if worthy else 'No cluster meets the independent-evidence gate.') + '\n\n[30-day evidence](pain_clusters.md)\n\n'
    report += '## LedgerDrop\n\n' + sections['ledgerdrop']
    report += '## System Health\n\n'+health+'\n\n'+'\n'.join('- '+md(s) for s in stats)+'\n\n[Filter summary](filter_summary.md) · [Full CSV](opportunities.csv)\n\n'
    report += '## 🚨 Strongest Signal Today\n\n' + strongest
    report += '## 🎯 Recommended Action\n\n' + ('See Today\'s Action Queue above.' if action_queue(rows, clusters, preview) else 'NO ACTION REQUIRED TODAY')
    report += '\n\n' + sections['uk'] + sections['pain']
    if preview:
        for name in ('pain_clusters.md', 'filter_summary.md', 'opportunities.csv'):
            report = report.replace(']('+name+')', '](../'+name+')')
    (output/'daily_report.md').write_text(report, encoding='utf-8')
