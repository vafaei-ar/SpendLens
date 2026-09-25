import argparse
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from spendlens.eval_corpus import load_corpus
from spendlens.eval_runner import (
    build_report,
    run_corpus,
    write_jsonl,
    write_report_json,
    write_report_markdown,
)
from spendlens.extraction import OllamaVisionExtractor


def _run_name(model: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    model_slug = re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("-")
    return f"{timestamp}-{model_slug}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the local receipt extractor against the private corpus."
    )
    parser.add_argument(
        "--corpus-root",
        type=Path,
        default=Path("evaluation/private"),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("evaluation/results"),
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OLLAMA_MODEL", "qwen3-vl:4b"),
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get(
            "OLLAMA_BASE_URL",
            "http://127.0.0.1:11434",
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(
            os.environ.get("OLLAMA_TIMEOUT_SECONDS", "180")
        ),
    )
    args = parser.parse_args()

    cases = load_corpus(args.corpus_root)
    extractor = OllamaVisionExtractor(
        base_url=args.base_url,
        model=args.model,
        timeout_seconds=args.timeout,
    )
    records = run_corpus(cases, extractor)
    report = build_report(records)

    run_dir = args.output_root / _run_name(args.model)
    write_jsonl(records, run_dir / "records.jsonl")
    write_report_json(report, run_dir / "report.json")
    write_report_markdown(report, run_dir / "report.md")

    print(f"Evaluation complete: {run_dir}")
    print(
        "False auto-accept rate: "
        f"{report.metrics.false_auto_accept_rate}"
    )
    print(
        "Critical receipt accuracy: "
        f"{report.metrics.critical_receipt_accuracy:.2%}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
