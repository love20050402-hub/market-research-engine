"""Run daily radar independently from the original market research pipeline."""
import argparse
import logging
import os
from pathlib import Path

from radar.core import History, JsonHistory, classify, utcnow
from radar.intake import collect_hn, load_records, manual_records
from radar.github_intake import collect_issues
from radar.public_sources import collect_public_sources
from radar.reporting import reports

BASE = Path(__file__).resolve().parent


def run(root=BASE, offline=False, imports=(), *, fresh_preview=False, state_file=None, github_issues=None):
    root = Path(root).resolve()
    if os.environ.get('RADAR_TEST_MODE') == '1' and root == BASE:
        raise ValueError('Tests must supply an isolated root')
    if not fresh_preview and root == BASE and any(Path(p).resolve().is_relative_to(BASE/'tests') for p in imports):
        raise ValueError('Fixture imports require --fresh-preview or an isolated --root')
    output = root/'output'/('preview' if fresh_preview else '')
    for name in ('input', 'output', 'data', 'logs'):
        (root/name).mkdir(parents=True, exist_ok=True)
    for name in ('manual_text.txt', 'manual_urls.txt'):
        path = root/'input'/name
        if not path.exists() and not fresh_preview:
            path.write_text('# Paste public post text with url: metadata; separate posts with ---\n' if name == 'manual_text.txt' else '# One public URL per line; X/Reddit also need pasted text.\n', encoding='utf-8')
    logger = logging.getLogger('opportunity_radar')
    handler = logging.FileHandler(root/'logs/radar.log', encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    history = None
    try:
        try:
            records, pending = manual_records(root, offline, logger)
        except (OSError, UnicodeError) as exc:
            logger.warning('Manual intake read failed: %s; save input as UTF-8', type(exc).__name__)
            records, pending = [], [{'url': '', 'reason': 'Manual intake read failed; check encoding/permissions and logs'}]
        stats = [f'Manual text: {len(records)} records']
        for path in imports:
            try:
                added = list(load_records(Path(path)))
                records.extend(added)
                stats.append(f'Import {Path(path).name}: {len(added)} records')
            except Exception as exc:
                logger.warning('Import failed %s: %s', Path(path).name, type(exc).__name__)
                stats.append(f'Import {Path(path).name}: FAILED ({type(exc).__name__})')
        prepared, feedback = [], []
        if not offline:
            auto, health = collect_hn(logger)
            records.extend(auto)
            stats.extend(health)
            additional, health = collect_public_sources(logger)
            for row in additional:
                if row.get('source') == 'contracts_finder':
                    prepared.append(row)  # Validated structured official procurement, not user-supplied scores.
                else:
                    records.append(row)
            stats.extend(health)
            if github_issues:
                issue_rows, issue_pending, health = collect_issues(github_issues, logger, feedback=feedback)
                records.extend(issue_rows)
                pending.extend(issue_pending)
                stats.extend(health)
        else:
            stats.append('Automatic sources: disabled (--offline)')
        now = utcnow()
        history = JsonHistory(state_file, preview=fresh_preview) if state_file else History(root/'data/opportunity_history.sqlite3', preview=fresh_preview)
        rejected = 0
        for index, raw in enumerate(records):
            try:
                row = classify(raw)
            except (ValueError, TypeError, AttributeError) as exc:
                rejected += 1
                logger.warning('Skipping malformed record %s: %s', index, type(exc).__name__)
                continue
            history.track(row, now)
        for row in prepared:
            history.track(row, now)
        history.update_intelligence(feedback)
        rows = list(history.current.values())
        stats.append(f'Processed unique: {len(rows)}; malformed skipped: {rejected}')
        reports(output, rows, stats, pending, now, preview=fresh_preview, mobile=bool(github_issues), history_rows=[r for _, r in history.rows])
        if not fresh_preview:
            # Reuse this run's in-memory results: no second collection/history mutation.
            reports(output/'preview', rows, stats, pending, now, preview=True, mobile=bool(github_issues), history_rows=[r for _, r in history.rows])
        history.close()
        history = None
        logger.info('Completed: %s unique records, %s pending URLs', len(rows), len(pending))
        return rows
    except Exception as exc:
        logger.error('Run failed: %s; history transaction will roll back', type(exc).__name__)
        # Never leave yesterday's actionable recommendations under a failed run.
        try:
            output.mkdir(parents=True, exist_ok=True)
            (output/'daily_report.md').write_text('# Opportunity Radar\n\n**FAILED**\n\nUTC: '+utcnow()+'\n\nCore pipeline/history/report failed ('+type(exc).__name__+'). Check Actions logs.\n', encoding='utf-8')
            (output/'action_queue.md').write_text('# Today\'s Action Queue\n\nFAILED — recommendations unavailable.\n', encoding='utf-8')
        except OSError:
            logger.error('Cannot write FAILED report; use workflow status/logs')
        raise
    finally:
        if history is not None:
            history.close(commit=False)
        logger.removeHandler(handler)
        handler.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--root', type=Path, default=BASE, help='Separate input/output/history directory (e.g. demo)')
    parser.add_argument('--import-file', type=Path, action='append', default=[], help='Existing normalized JSON array or CSV; repeatable')
    parser.add_argument('--fresh-preview', action='store_true', help='Read history without modifying it; include SEEN in output/preview')
    parser.add_argument('--state-file', type=Path, help='Tracked JSONL history (GitHub); default local SQLite remains unchanged')
    parser.add_argument('--github-issues', metavar='OWNER/REPO', help='Read trusted open Radar Intake Issues; optional GITHUB_TOKEN from environment')
    args = parser.parse_args()
    try:
        run(args.root, args.offline, args.import_file, fresh_preview=args.fresh_preview, state_file=args.state_file, github_issues=args.github_issues)
    except Exception as exc:
        print(f'Radar could not finish ({type(exc).__name__}). Check filesystem permissions, history lock/integrity and logs/radar.log. Existing history is not reset.')
        return 1
    report = args.root.resolve()/'output'/('preview' if args.fresh_preview else '')/'daily_report.md'
    print(f'Report: {report}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
