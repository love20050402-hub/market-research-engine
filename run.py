#!/usr/bin/env python3
"""One-command local HN + Reddit Market Opportunity Research CLI."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from collectors.base import Collector, CollectorError
from collectors.hackernews import HackerNewsCollector
from collectors.reddit import RedditCollector
from pipeline.clustering import cluster_pain_signals
from pipeline.pain_extractor import extract_pain_signals
from pipeline.prefilter import candidate_prefilter
from pipeline.reporting import build_markdown_report
from pipeline.validator import prepare_pain_signals, validate_opportunities
from providers.llm_provider import (
    LLMProvider,
    MissingCredentialError,
    ProviderConfigurationError,
    ProviderError,
    create_provider,
)


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PROJECT_DIR / "config.yaml"


def load_config(path: Path) -> dict[str, Any]:
    """Load JSON-compatible YAML using only the Python standard library."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            config = json.load(handle)
    except FileNotFoundError as exc:
        raise ValueError(f"Config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Config is invalid JSON-compatible YAML at line {exc.lineno}: {exc.msg}"
        ) from exc
    if not isinstance(config, dict):
        raise ValueError("Config root must be an object.")
    for section in ("hackernews", "prefilter", "llm", "output"):
        if not isinstance(config.get(section), dict):
            raise ValueError(f"Config is missing object section: {section}")
    return config


def load_env_file(path: Path) -> None:
    """Load a small .env file without logging or overwriting existing environment values."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def run_pipeline(
    *,
    config: dict[str, Any],
    provider: LLMProvider,
    collector: Collector | None = None,
    collectors: list[Collector] | None = None,
    output_dir: Path | None = None,
    progress: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Execute configured collectors through one shared evidence-first pipeline."""
    if collector is not None and collectors is not None:
        raise ValueError("Pass either collector or collectors, not both.")
    active_collectors = collectors or ([collector] if collector is not None else [])
    if not active_collectors:
        active_collectors = [HackerNewsCollector(config["hackernews"])]
        reddit_config = config.get("reddit")
        if isinstance(reddit_config, dict) and reddit_config.get("enabled") is True:
            active_collectors.append(RedditCollector(reddit_config))
    output_config = config["output"]
    resolved_output = output_dir or PROJECT_DIR / str(output_config["directory"])
    resolved_output.mkdir(parents=True, exist_ok=True)

    raw_signals: list[dict[str, Any]] = []
    records_by_source: dict[str, list[dict[str, Any]]] = {}
    for active_collector in active_collectors:
        progress(
            f"Fetching {active_collector.source_name}: "
            f"{active_collector.request_description}"
        )
        source_records = active_collector.fetch_and_normalize()
        records_by_source.setdefault(active_collector.source_name, []).extend(source_records)
        raw_signals.extend(source_records)
        progress(
            f"Normalized {active_collector.source_name} records: {len(source_records)}"
        )
    _write_json(resolved_output / str(output_config["raw_signals"]), raw_signals)

    candidates: list[dict[str, Any]] = []
    source_limits = config["prefilter"].get("source_limits", {})
    for source_name, source_records in records_by_source.items():
        max_candidates = (
            int(source_limits.get(source_name, config["prefilter"].get("max_candidates", 20)))
            if isinstance(source_limits, dict)
            else int(config["prefilter"].get("max_candidates", 20))
        )
        source_candidates = candidate_prefilter(
            source_records,
            max_candidates=max_candidates,
        )
        candidates.extend(source_candidates)
        source_real_count = sum(
            1 for item in source_candidates if item.get("is_sentinel") is not True
        )
        progress(f"Candidate Prefilter {source_name}: {source_real_count}")
    real_candidate_count = sum(1 for item in candidates if item.get("is_sentinel") is not True)
    progress(f"Candidate Prefilter total: {real_candidate_count}")

    extracted = extract_pain_signals(
        candidates,
        provider,
        request_delay_seconds=float(config["llm"].get("request_delay_seconds", 0)),
        progress=progress,
    )
    pain_signals = prepare_pain_signals(extracted)
    _write_json(resolved_output / str(output_config["pain_signals"]), pain_signals)
    progress(f"Qualified Pain Signals: {len(pain_signals)}")

    progress("Clustering qualified Pain Signals...")
    raw_clusters = cluster_pain_signals(pain_signals, provider)
    opportunities = validate_opportunities(raw_clusters, pain_signals)
    _write_json(resolved_output / str(output_config["opportunities"]), opportunities)
    progress(f"Validated Opportunity Clusters: {len(opportunities)}")

    report = build_markdown_report(
        raw_data_count=len(raw_signals),
        pain_signals=pain_signals,
        opportunity_clusters=opportunities,
    )
    report_path = resolved_output / str(output_config["report"])
    report_path.write_text(report, encoding="utf-8")
    progress(f"Market Opportunity Report: {report_path}")

    return {
        "raw_signals": raw_signals,
        "pain_signals": pain_signals,
        "opportunities": opportunities,
        "report": report,
        "report_path": report_path,
    }


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch HN data and produce an evidence-first Market Opportunity Report."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to JSON-compatible config.yaml.",
    )
    parser.add_argument(
        "--provider",
        choices=("ollama", "gemini", "mock"),
        help="Override llm.provider. Ollama is the free local default; mock validates plumbing only.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Override the output directory.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    load_env_file(PROJECT_DIR / ".env")
    try:
        config = load_config(args.config.resolve())
        provider = create_provider(config["llm"], provider_name=args.provider)
        run_pipeline(
            config=config,
            provider=provider,
            output_dir=args.output_dir.resolve() if args.output_dir else None,
        )
        return 0
    except MissingCredentialError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except ProviderConfigurationError as exc:
        print(f"Provider configuration error: {exc}", file=sys.stderr)
        return 2
    except CollectorError as exc:
        print(f"Collection failed: {exc}", file=sys.stderr)
        return 3
    except ProviderError as exc:
        print(f"AI analysis failed: {exc}", file=sys.stderr)
        return 4
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Cancelled by user.", file=sys.stderr)
        return 130


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
