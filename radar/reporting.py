"""UTF-8 CSV and Markdown reports, with fresh evidence first."""
import csv
import re
from datetime import datetime, timezone, timedelta

from radar.core import FIELDS, domain


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
    try:
        date = datetime.fromisoformat(r['published_at'].replace('Z', '+00:00'))
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        if date < datetime.now(timezone.utc) - timedelta(days=30):
            return False
    except (ValueError, TypeError):
        pass  # Unknown dates remain explicitly marked for human verification.
    return r['status'] == 'NEW' or (r['status'] == 'UPDATED' and r['important_update'])


def ranked(rows, category, only_fresh=False, preview=False):
    return sorted((r for r in rows if category in r['category'].split(';') and (not only_fresh or fresh(r))), key=lambda r: (0 if preview else -int(fresh(r)), -float(r['total_score']), r['url'], r['title']))


def card(r, category):
    source = f"[原文]({r['url'].replace(')', '%29').replace('(', '%28')})" if r['url'] else '未附 URL，先補原文'
    base = f"### {md(r['title'])}\n\n{source} · {r['status']} · {r['total_score']}/5 · 發布：{md(r['published_at'])}\n\n"
    if category == 'money':
        detail = [('對方要什麼', r['workflow']), ('為什麼可能願意付錢', r['payment_signal']), ('我們可以賣什麼（假設）', r['possible_offer']), ('是否值得聯絡', '先人工核對時效、身份、預算，再決定' if r['score_payment_intent'] >= 4 else '先確認是否有預算，不直接當成付費客戶')]
    elif category == 'uk':
        detail = [('Company / buyer', r['company']), ('Buyer type', r.get('buyer_type') or 'Small company (verify)'), ('Website', r['website']), ('需求證據', r.get('evidence_text') or r['workflow']), ('Offer（假設）', r['possible_offer']), ('Deadline', r.get('deadline') or 'UNKNOWN'), ('Public contact', r['contact_page_or_public_contact'] or 'UNKNOWN')]
    else:
        detail = [('Pain', r['pain_summary']), ('現在如何解決', r['current_workaround']), ('痛點強度', str(r['score_pain_strength'])+'/5'), ('是否有人付費', r['payment_signal']), ('現有競爭（僅原文提及）', r['competitor']), ('SaaS 機會（假設）', r['saas_signal']), ('我們是否值得追', '先訪談確認重複頻率與付費；尚未驗證市場')]
    return base + '\n'.join(f'- {label}：{md(value)}' for label, value in detail) + '\n\n'


def reports(output, rows, stats, pending, now, *, preview=False, mobile=False):
    output.mkdir(parents=True, exist_ok=True)
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
        top = [r for r in selected if preview or fresh(r)][:5]
        section = f'## {heading}\n\n' + (''.join(card(r, cat) for r in top) or '今日沒有新的合格訊號；不補入舊資料或示範資料。\n\n')
        section += f'本次合格 {len(selected)} 筆；其餘 SEEN／非重要更新請看 CSV。\n\n'
        sections[cat] = section
        if cat != 'ledgerdrop':
            name = filename + ('_top.md' if cat == 'uk' else '_top5.md')
            (output/name).write_text(section, encoding='utf-8')
    candidates = sorted([r for r in rows if r['category'].strip(';') and (preview or fresh(r))], key=lambda r: -r['total_score'])
    strongest = card(candidates[0], 'uk' if 'uk' in candidates[0]['category'].split(';') else 'money' if 'money' in candidates[0]['category'] else 'pain') if candidates else '今日無新增或重要更新的合格訊號。\n\n'
    actions = []
    for r in candidates[:2]:
        actions.append(f"人工查看「{md(r['title'])}」原文與日期，確認需求仍存在；{'確認預算並準備小額試做提案' if r['score_payment_intent'] >= 4 else '整理 3 個訪談問題：頻率、現行解法、成本'}。")
    if pending:
        actions.append(f'補上 pending_intake.csv 中 {len(pending)} 筆 URL 的實際貼文文字。')
    if not actions:
        actions = ['貼入 1–3 篇具體需求／痛點原文到 input/manual_text.txt，然後重跑。']
    if mobile:
        actions = [a.replace('input/manual_text.txt，然後重跑', 'GitHub 的 Radar Intake Issue，等待下次排程') for a in actions]
        if pending:
            actions = [a.replace('URL 的實際貼文文字', 'URL 的實際貼文文字（編輯對應 Radar Intake Issue）') for a in actions]
    report = '# Opportunity Radar\n\n' + f'執行時間 UTC：{now}。評分為保守規則估計，所有分數 0–5；total 為五項平均。\n\n'
    if preview:
        report += '> FRESH PREVIEW：包含 SEEN，僅供人工 review；未修改正式 history 或 daily report。\n\n'
    report += ''.join(sections.values()) + '## 🚨 Strongest Signal Today\n\n' + strongest
    report += '## 🎯 Recommended Action\n\n' + '\n'.join(f'{i}. {a}' for i, a in enumerate(actions[:3], 1))
    report += '\n\n## Source health\n\n' + '\n'.join('- '+md(s) for s in stats) + f'\n- Pending intake: {len(pending)}\n'
    (output/'daily_report.md').write_text(report, encoding='utf-8')
