"""Plain-language Markdown Market Opportunity Report renderer."""

from __future__ import annotations

from typing import Any

from pipeline.validator import top_opportunities


CRITICAL_UNKNOWN_LABELS = {
    "willingness_to_pay": "使用者是否願意付費",
    "economic_cost": "問題造成的實際經濟成本",
    "problem_frequency": "問題發生頻率",
    "current_solution": "使用者目前怎麼解決",
}


def build_markdown_report(
    *,
    raw_data_count: int,
    pain_signals: list[dict[str, Any]],
    opportunity_clusters: list[dict[str, Any]],
) -> str:
    top = top_opportunities(opportunity_clusters)
    score_70_count = sum(
        1
        for cluster in opportunity_clusters
        if cluster.get("publish_eligible")
        and int(cluster.get("opportunity_score", 0)) >= 70
    )
    do_now_count = sum(
        cluster.get("decision") == "DO NOW"
        for cluster in opportunity_clusters
        if cluster.get("publish_eligible")
    )
    best = top[0]["opportunity_name"] if top else "本輪沒有通過證據門檻的機會"

    lines = [
        "# MARKET OPPORTUNITY REPORT",
        "",
        "## 先看結論",
        "",
        f"- 本輪原始資料：{raw_data_count} 筆",
        f"- 通過 Pain Signal 證據門檻：{len(pain_signals)} 筆",
        f"- Opportunity Clusters：{len(opportunity_clusters)} 個",
        f"- 分析分數 ≥70：{score_70_count} 個",
        f"- 真正判定 DO NOW：{do_now_count} 個",
        f"- 最值得先看：{best}",
        "",
        "> 100 分是分析工具，不等於「我們應該做」。最終決定另外受到商業證據、關鍵未知與小團隊適配度限制。",
        "",
        "---",
        "",
        "## 值得我們看的機會",
        "",
    ]

    if not top:
        lines.extend(["本輪沒有通過最低證據門檻的候選；不為湊數輸出。", ""])

    for index, cluster in enumerate(top, 1):
        score = cluster["score_breakdown"]
        risks = cluster["risks"]
        current_solutions = _known_list(cluster.get("current_alternatives"))
        current_solution_text = (
            "；".join(current_solutions) if current_solutions else "目前資料不知道。"
        )
        solution_gap_text = (
            _known_text(cluster.get("alternative_weakness"), "目前還不能確定。")
            if current_solutions
            else "目前還不能確定。"
        )
        payment_answer, payment_reason = _payment_plain(cluster)
        team_answer, team_reason = _team_plain(cluster)
        unknowns = _top_unknowns(cluster)
        source_text = {
            "CROSS_SOURCE_VALIDATED": "HN 與 Reddit 都有",
            "HN_ONLY": "只有 Hacker News",
            "REDDIT_ONLY": "只有 Reddit",
        }.get(str(cluster.get("source_diversity")), "來源仍不明確")

        lines.extend(
            [
                f"### #{index} {cluster['opportunity_name']}",
                "",
                "#### 一句話結論",
                "",
                _plain_conclusion(cluster),
                "",
                "#### 誰有這個問題？",
                "",
                _known_text(cluster.get("target_customer"), "目前資料還不知道是誰。"),
                "",
                "#### 他到底在煩什麼？",
                "",
                _known_text(cluster.get("core_problem"), "目前資料還說不清楚。"),
                "",
                "#### 有什麼證據？",
                "",
                f"共有 {cluster['evidence_count']} 個訊號；來源狀態是「{source_text}」。",
                "",
                _known_text(cluster.get("strongest_evidence"), "目前沒有足夠的具體摘要。"),
                "",
                "#### 有沒有證明他願意花錢？",
                "",
                f"{payment_answer}。{payment_reason}",
                "",
                "#### 現在他怎麼解決？",
                "",
                current_solution_text,
                "",
                "#### 為什麼現在的解法不夠好？",
                "",
                solution_gap_text,
                "",
                "#### 適不適合我們？",
                "",
                f"{team_answer}。{team_reason}",
                "",
                "#### 如果想驗證，最便宜的方法是什麼？",
                "",
                _cheap_validation(cluster),
                "",
                "#### 最大未知問題",
                "",
                *[f"- {item}" for item in unknowns],
                "",
                "#### 最終決定",
                "",
                f"**{cluster['decision']}**",
                "",
                "#### 詳細分數（供檢查）",
                "",
                f"- Pain Severity：{score['pain_severity']}/20",
                f"- Payment Evidence：{score['payment_evidence']}/20",
                f"- Durability：{score['durability']}/15",
                f"- Solution Gap：{score['solution_gap']}/15",
                f"- Channel Accessibility：{score['channel_accessibility']}/10",
                f"- Low-Cost Entry：{score['low_cost_entry']}/10",
                f"- Validation Speed：{score['validation_speed']}/5",
                f"- Small-Team Fit：{score['small_team_fit']}/5",
                "",
                f"**Total：{cluster['opportunity_score']}/100**",
                "",
                f"Confidence：{cluster['confidence']}",
                "",
                "風險：",
                "",
                f"- hype_dependency：{risks['hype_dependency']}",
                f"- platform_dependency：{risks['platform_dependency']}",
                f"- regulation_risk：{risks['regulation_risk']}",
                f"- capital_risk：{risks['capital_risk']}",
                f"- technical_complexity：{risks['technical_complexity']}",
                "- customer_acquisition_difficulty："
                f"{risks['customer_acquisition_difficulty']}",
                "",
                "Source URLs：",
                "",
                *[f"- {url}" for url in cluster["source_urls"]],
                "",
                "---",
                "",
            ]
        )

    return "\n".join(lines)


def _plain_conclusion(cluster: dict[str, Any]) -> str:
    decision = cluster.get("decision")
    conclusions = {
        "DO NOW": "證據與小團隊適配度都夠強，值得現在用最低成本開始驗證。",
        "VALIDATE": "看起來值得研究，但還有重要問題沒確認，先驗證，不要直接做產品。",
        "WATCH": "痛點可能是真的，但付費或商業證據還不夠，現在先觀察與補證據。",
        "MARKET INTERESTING / TEAM MISMATCH": "這個問題可能值得觀察，但目前證據不足，且不適合我們現在做。",
        "MARKET GOOD / TEAM MISMATCH": "市場問題可能值得解決，但成本或技術負擔不適合目前的 1–4 人小團隊。",
        "REJECT": "目前證據太弱或適配度太低，不值得投入時間。",
    }
    return conclusions.get(str(decision), conclusions["REJECT"])


def _payment_plain(cluster: dict[str, Any]) -> tuple[str, str]:
    payment_score = int(cluster["score_breakdown"]["payment_evidence"])
    if payment_score >= 10:
        return "有", _known_text(
            cluster.get("payment_evidence"), "來源中有直接付款或金錢損失證據。"
        )
    if payment_score > 0:
        return "部分證據", _known_text(
            cluster.get("payment_evidence"), "目前只有間接商業線索。"
        )
    return "沒有", "目前來源沒有證明已付費、明確預算、購買行為、具體金錢損失或願意付費。"


def _team_plain(cluster: dict[str, Any]) -> tuple[str, str]:
    score = cluster["score_breakdown"]
    low_cost = int(score["low_cost_entry"])
    team_fit = int(score["small_team_fit"])
    if low_cost <= 3 and team_fit <= 2:
        return "不適合", "開始成本與小團隊適配度都太低，現在不應由我們直接做。"
    if low_cost >= 6 and team_fit >= 4:
        return "適合", "可以由小團隊用低成本方式先驗證。"
    if team_fit >= 3:
        return "可以驗證", "可以先做訪談或人工服務測試，但不應先做完整產品。"
    return "不太適合", "目前技術、資本或執行負擔偏高。"


def _cheap_validation(cluster: dict[str, Any]) -> str:
    target = _known_text(cluster.get("target_customer"), "可能受影響的人")
    problem = _known_text(cluster.get("core_problem"), "這個問題")
    return (
        f"先不寫程式：找 5 位「{target}」訪談，請他們展示最近一次遇到「{problem}」的真實流程；"
        "再用人工方式協助 1–2 次，確認發生頻率、現在的替代做法，以及是否真的願意付費。"
    )


def _top_unknowns(cluster: dict[str, Any]) -> list[str]:
    output: list[str] = []
    critical = cluster.get("critical_unknowns")
    if isinstance(critical, dict):
        for key, value in critical.items():
            if value == "UNKNOWN" and key in CRITICAL_UNKNOWN_LABELS:
                output.append(CRITICAL_UNKNOWN_LABELS[key])
    for item in _known_list(cluster.get("key_unknowns")):
        if item not in output:
            output.append(item)
    return output[:3] or ["目前沒有額外列出的未知問題"]


def _known_list(value: object) -> list[str]:
    values = value if isinstance(value, list) else [value]
    output: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if text and text.casefold() not in {"unknown", "未知", "none", "null"}:
            output.append(text)
    return output


def _known_text(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    return (
        fallback
        if not text or text.casefold() in {"unknown", "未知", "none", "null"}
        else text
    )
