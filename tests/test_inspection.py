from pathlib import Path

from spendlens.db import (
    connect,
    create_review_session,
    initialize_database,
    link_extraction_to_receipt,
    persist_receipt,
    record_extraction_attempt,
    record_source_document,
    record_telegram_ingestion,
)
from spendlens.inspection import (
    format_extractions,
    format_latest,
    format_receipt,
    format_recent_receipts,
    format_reviews,
    format_status,
    get_counts,
    get_latest_bundle,
    get_receipt,
    get_source,
    open_reviews,
    recent_extractions,
    recent_receipts,
)
from spendlens.models import ReceiptExtraction
from spendlens.storage import store_source_bytes
from spendlens.validation import VALIDATOR_VERSION, validate_receipt


def _receipt() -> ReceiptExtraction:
    return ReceiptExtraction.model_validate(
        {
            "merchant": "Example Market",
            "transaction_date": "2026-09-26",
            "subtotal": "10.00",
            "tax": "0.60",
            "total": "10.60",
            "currency": "USD",
            "line_items": [
                {
                    "description_raw": "APPLE",
                    "description_normalized": "Apple",
                    "amount": "10.00",
                    "category": "groceries",
                    "subcategory": "fruit",
                }
            ],
        }
    )


def _seed_saved_receipt(tmp_path: Path) -> tuple[Path, int, int]:
    database_path = initialize_database(tmp_path)
    stored = store_source_bytes(
        b"synthetic-image",
        data_dir=tmp_path,
        mime_type="image/jpeg",
    )
    extraction = _receipt()
    report = validate_receipt(extraction)

    with connect(database_path) as connection:
        source_id = record_source_document(connection, stored)
        record_telegram_ingestion(
            connection,
            source_document_id=source_id,
            source_type="telegram_photo",
            telegram_file_id="file-1",
            telegram_file_unique_id="unique-1",
            telegram_message_id=11,
            telegram_chat_id=22,
            width=1000,
            height=2000,
        )
        attempt_id = record_extraction_attempt(
            connection,
            source_document_id=source_id,
            provider="ollama",
            model_id="qwen3-vl:4b",
            prompt_version="receipt-v1",
            schema_version="1",
            validator_version=VALIDATOR_VERSION,
            status="parsed",
            raw_response=extraction.model_dump_json(),
            parsed_json=extraction.model_dump_json(),
            validation_json=report.model_dump_json(),
            error_text=None,
            auto_accepted=report.auto_accept,
        )
        receipt_id = persist_receipt(
            connection,
            extraction=extraction,
            source_document_id=source_id,
            status="accepted",
            actor="test",
        )
        link_extraction_to_receipt(
            connection,
            extraction_attempt_id=attempt_id,
            receipt_id=receipt_id,
        )

    return database_path, source_id, receipt_id


def test_status_and_latest_bundle(tmp_path: Path) -> None:
    database_path, source_id, receipt_id = _seed_saved_receipt(tmp_path)

    with connect(database_path) as connection:
        counts = get_counts(connection)
        bundle = get_latest_bundle(connection)

    assert counts.sources == 1
    assert counts.ingestions == 1
    assert counts.extractions == 1
    assert counts.receipts == 1
    assert counts.open_reviews == 0

    status = format_status(
        counts,
        model_id="qwen3-vl:4b",
        extraction_enabled=True,
    )
    assert "Sources stored: 1" in status
    assert "Saved receipts: 1" in status

    assert bundle is not None
    latest = format_latest(bundle)
    assert f"Source #{source_id}" in latest
    assert f"Saved receipt #{receipt_id}" in latest
    assert "Example Market" in latest
    assert "10.60 USD" in latest


def test_recent_receipts_and_detail(tmp_path: Path) -> None:
    database_path, source_id, receipt_id = _seed_saved_receipt(tmp_path)

    with connect(database_path) as connection:
        rows = recent_receipts(connection)
        receipt = get_receipt(connection, receipt_id)
        source = get_source(connection, source_id)

    assert len(rows) == 1
    recent_text = format_recent_receipts(rows)
    assert "Example Market" in recent_text
    assert "10.60 USD" in recent_text

    assert receipt is not None
    receipt_text = format_receipt(receipt)
    assert f"Receipt #{receipt_id}" in receipt_text
    assert f"Source #{source_id}" in receipt_text
    assert "Apple" in receipt_text
    assert "groceries/fruit" in receipt_text

    assert source is not None
    assert source["relative_path"].endswith(".jpg")


def test_extractions_and_reviews_are_visible(tmp_path: Path) -> None:
    database_path = initialize_database(tmp_path)
    stored = store_source_bytes(
        b"needs-review",
        data_dir=tmp_path,
        mime_type="image/jpeg",
    )
    extraction = ReceiptExtraction.model_validate(
        {
            "merchant": "Example Market",
            "transaction_date": "2026-09-26",
            "subtotal": "10.00",
            "tax": "0.60",
            "total": "11.60",
            "currency": "USD",
        }
    )
    report = validate_receipt(extraction)
    assert not report.auto_accept

    with connect(database_path) as connection:
        source_id = record_source_document(connection, stored)
        record_telegram_ingestion(
            connection,
            source_document_id=source_id,
            source_type="telegram_photo",
            telegram_file_id="file-2",
            telegram_file_unique_id="unique-2",
            telegram_message_id=33,
            telegram_chat_id=44,
        )
        attempt_id = record_extraction_attempt(
            connection,
            source_document_id=source_id,
            provider="ollama",
            model_id="qwen3-vl:4b",
            prompt_version="receipt-v1",
            schema_version="1",
            validator_version=VALIDATOR_VERSION,
            status="parsed",
            raw_response=extraction.model_dump_json(),
            parsed_json=extraction.model_dump_json(),
            validation_json=report.model_dump_json(),
            error_text=None,
            auto_accepted=False,
        )
        review_id = create_review_session(
            connection,
            extraction_attempt_id=attempt_id,
            source_document_id=source_id,
            telegram_chat_id=44,
            telegram_user_id=55,
            proposed_json=extraction.model_dump_json(),
            validation_json=report.model_dump_json(),
        )
        extraction_rows = recent_extractions(connection)
        review_rows = open_reviews(
            connection,
            telegram_user_id=55,
        )

    extraction_text = format_extractions(extraction_rows)
    assert "qwen3-vl:4b" in extraction_text
    assert "review" in extraction_text

    review_text = format_reviews(review_rows)
    assert f"#{review_id}" in review_text
    assert "receipt_arithmetic_mismatch" in review_text
