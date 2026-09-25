import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from spendlens.eval_corpus import CorpusCase, SourceVariant
from spendlens.evaluation import (
    DeliveryComparison,
    EvaluationCase,
    EvaluationReport,
    blocker_counts,
    critical_fields_correct,
    evaluate_cases,
)
from spendlens.extraction import (
    ExtractionParseError,
    ExtractionProviderError,
    ExtractionResult,
)
from spendlens.models import ReceiptExtraction
from spendlens.validation import ValidationReport, validate_receipt


class Extractor(Protocol):
    def extract(
        self,
        image_bytes: bytes,
        *,
        mime_type: str,
    ) -> ExtractionResult: ...


class ExperimentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    receipt_key: str
    source_variant: SourceVariant
    source_sha256: str
    mime_type: str
    truth: ReceiptExtraction
    prediction: ReceiptExtraction | None
    validation: ValidationReport | None
    auto_accepted: bool
    status: Literal[
        "ok",
        "parse_error",
        "provider_error",
        "unsupported_source",
    ]
    provider: str | None = None
    model_id: str | None = None
    prompt_version: str | None = None
    duration_seconds: float
    error: str | None = None


def run_case(
    case: CorpusCase,
    extractor: Extractor,
) -> ExperimentRecord:
    if not case.metadata.mime_type.startswith("image/"):
        return ExperimentRecord(
            case_id=case.metadata.case_id,
            receipt_key=case.metadata.receipt_key,
            source_variant=case.metadata.source_variant,
            source_sha256=case.metadata.source_sha256,
            mime_type=case.metadata.mime_type,
            truth=case.truth,
            prediction=None,
            validation=None,
            auto_accepted=False,
            status="unsupported_source",
            duration_seconds=0.0,
            error="Current evaluation runner supports image sources only.",
        )

    started = time.perf_counter()
    try:
        result = extractor.extract(
            case.source_path.read_bytes(),
            mime_type=case.metadata.mime_type,
        )
    except ExtractionParseError as exc:
        return ExperimentRecord(
            case_id=case.metadata.case_id,
            receipt_key=case.metadata.receipt_key,
            source_variant=case.metadata.source_variant,
            source_sha256=case.metadata.source_sha256,
            mime_type=case.metadata.mime_type,
            truth=case.truth,
            prediction=None,
            validation=None,
            auto_accepted=False,
            status="parse_error",
            duration_seconds=time.perf_counter() - started,
            error=str(exc),
        )
    except ExtractionProviderError as exc:
        return ExperimentRecord(
            case_id=case.metadata.case_id,
            receipt_key=case.metadata.receipt_key,
            source_variant=case.metadata.source_variant,
            source_sha256=case.metadata.source_sha256,
            mime_type=case.metadata.mime_type,
            truth=case.truth,
            prediction=None,
            validation=None,
            auto_accepted=False,
            status="provider_error",
            duration_seconds=time.perf_counter() - started,
            error=str(exc),
        )

    report = validate_receipt(result.extraction)
    return ExperimentRecord(
        case_id=case.metadata.case_id,
        receipt_key=case.metadata.receipt_key,
        source_variant=case.metadata.source_variant,
        source_sha256=case.metadata.source_sha256,
        mime_type=case.metadata.mime_type,
        truth=case.truth,
        prediction=result.extraction,
        validation=report,
        auto_accepted=report.auto_accept,
        status="ok",
        provider=result.provider,
        model_id=result.model_id,
        prompt_version=result.prompt_version,
        duration_seconds=time.perf_counter() - started,
    )


def run_corpus(
    cases: list[CorpusCase],
    extractor: Extractor,
) -> list[ExperimentRecord]:
    return [run_case(case, extractor) for case in cases]


def _evaluation_case(record: ExperimentRecord) -> EvaluationCase:
    return EvaluationCase(
        case_id=record.case_id,
        truth=record.truth,
        prediction=record.prediction,
        auto_accepted=record.auto_accepted,
    )


def _delivery_comparison(
    records: list[ExperimentRecord],
) -> DeliveryComparison:
    grouped: dict[str, dict[SourceVariant, ExperimentRecord]] = defaultdict(dict)
    for record in records:
        grouped[record.receipt_key][record.source_variant] = record

    result = DeliveryComparison()
    for variants in grouped.values():
        photo = variants.get(SourceVariant.TELEGRAM_PHOTO)
        file = variants.get(SourceVariant.TELEGRAM_FILE)
        if photo is None or file is None:
            continue

        photo_ok = critical_fields_correct(_evaluation_case(photo))
        file_ok = critical_fields_correct(_evaluation_case(file))
        result.paired_receipts += 1

        if photo_ok and file_ok:
            result.both_correct += 1
        elif not photo_ok and file_ok:
            result.photo_only_wrong += 1
        elif photo_ok and not file_ok:
            result.file_only_wrong += 1
        else:
            result.both_wrong += 1

    return result


def build_report(
    records: list[ExperimentRecord],
) -> EvaluationReport:
    if not records:
        raise ValueError("At least one experiment record is required")

    cases = [_evaluation_case(record) for record in records]
    silent_auto_accepts = [
        record.case_id
        for record, case in zip(records, cases, strict=True)
        if record.auto_accepted and not critical_fields_correct(case)
    ]
    critical_errors = [
        record.case_id
        for record, case in zip(records, cases, strict=True)
        if not critical_fields_correct(case)
    ]
    no_predictions = [
        record.case_id
        for record in records
        if record.prediction is None
    ]
    issues = [
        [
            issue.code
            for issue in record.validation.issues
            if issue.severity == "blocker"
        ]
        if record.validation is not None
        else []
        for record in records
    ]

    by_variant: dict[str, object] = {}
    variants = sorted({record.source_variant.value for record in records})
    for variant in variants:
        subset = [
            _evaluation_case(record)
            for record in records
            if record.source_variant.value == variant
        ]
        by_variant[variant] = evaluate_cases(subset)

    return EvaluationReport(
        metrics=evaluate_cases(cases),
        silent_auto_accept_case_ids=sorted(silent_auto_accepts),
        critical_error_case_ids=sorted(critical_errors),
        no_prediction_case_ids=sorted(no_predictions),
        blocker_counts=blocker_counts(issues),
        by_source_variant=by_variant,
        delivery_comparison=_delivery_comparison(records),
    )


def write_jsonl(
    records: list[ExperimentRecord],
    path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(record.model_dump_json() + "\n")


def write_report_json(
    report: EvaluationReport,
    path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        report.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )


def render_report_markdown(report: EvaluationReport) -> str:
    metrics = report.metrics
    false_rate = (
        "n/a"
        if metrics.false_auto_accept_rate is None
        else f"{metrics.false_auto_accept_rate:.2%}"
    )
    lines = [
        "# SpendLens extraction evaluation",
        "",
        f"- Cases: {metrics.n}",
        f"- Prediction rate: {metrics.prediction_rate:.2%}",
        f"- Merchant accuracy: {metrics.merchant_accuracy:.2%}",
        f"- Date accuracy: {metrics.date_accuracy:.2%}",
        f"- Total accuracy: {metrics.total_accuracy:.2%}",
        (
            "- Critical receipt accuracy "
            f"(merchant + date + total): {metrics.critical_receipt_accuracy:.2%}"
        ),
        f"- Auto-accept rate: {metrics.auto_accept_rate:.2%}",
        f"- Manual-review rate: {metrics.manual_review_rate:.2%}",
        f"- False auto-accept count: {metrics.false_auto_accept_count}",
        f"- False auto-accept rate: {false_rate}",
        "",
        "## Silent auto-accept errors",
        "",
    ]
    if report.silent_auto_accept_case_ids:
        lines.extend(
            f"- {case_id}"
            for case_id in report.silent_auto_accept_case_ids
        )
    else:
        lines.append("- None")

    comparison = report.delivery_comparison
    lines.extend(
        [
            "",
            "## Telegram photo vs file pairs",
            "",
            f"- Paired receipts: {comparison.paired_receipts}",
            f"- Both correct: {comparison.both_correct}",
            f"- Photo only wrong: {comparison.photo_only_wrong}",
            f"- File only wrong: {comparison.file_only_wrong}",
            f"- Both wrong: {comparison.both_wrong}",
            "",
            "## Blocking validation reasons",
            "",
        ]
    )
    if report.blocker_counts:
        lines.extend(
            f"- {code}: {count}"
            for code, count in report.blocker_counts.items()
        )
    else:
        lines.append("- None")

    return "\n".join(lines) + "\n"


def write_report_markdown(
    report: EvaluationReport,
    path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_report_markdown(report),
        encoding="utf-8",
    )
