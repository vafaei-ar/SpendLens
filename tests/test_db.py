from spendlens.db import (
    connect,
    find_possible_duplicates,
    initialize_database,
    persist_receipt,
    record_source_document,
    record_telegram_ingestion,
)
from spendlens.models import ReceiptExtraction
from spendlens.storage import store_source_bytes


def receipt(
    *,
    merchant: str = "Example Market",
    date: str = "2026-09-24",
    total: str = "10.60",
) -> ReceiptExtraction:
    return ReceiptExtraction.model_validate(
        {
            "merchant": merchant,
            "transaction_date": date,
            "subtotal": "10.00",
            "tax": "0.60",
            "total": total,
            "currency": "USD",
        }
    )


def test_database_records_source_and_repeated_ingestion(
    tmp_path,
) -> None:
    source = store_source_bytes(
        b"synthetic-source",
        data_dir=tmp_path,
        mime_type="application/pdf",
    )
    database_path = initialize_database(tmp_path)

    with connect(database_path) as connection:
        source_id = record_source_document(connection, source)
        duplicate_source_id = record_source_document(
            connection,
            source,
        )
        record_telegram_ingestion(
            connection,
            source_document_id=source_id,
            source_type="telegram_document",
            telegram_file_id="file-a",
            telegram_file_unique_id="unique-a",
            telegram_message_id=1,
            telegram_chat_id=2,
        )
        record_telegram_ingestion(
            connection,
            source_document_id=source_id,
            source_type="telegram_document",
            telegram_file_id="file-b",
            telegram_file_unique_id="unique-b",
            telegram_message_id=3,
            telegram_chat_id=2,
        )
        ingestion_count = connection.execute(
            "SELECT COUNT(*) FROM source_ingestions"
        ).fetchone()[0]

    assert source_id == duplicate_source_id
    assert ingestion_count == 2


def test_transaction_level_duplicate_detection(tmp_path) -> None:
    source = store_source_bytes(
        b"source-one",
        data_dir=tmp_path,
        mime_type="image/jpeg",
    )
    database_path = initialize_database(tmp_path)

    with connect(database_path) as connection:
        source_id = record_source_document(connection, source)
        persist_receipt(
            connection,
            extraction=receipt(),
            source_document_id=source_id,
            status="accepted",
            actor="test",
        )
        candidates = find_possible_duplicates(
            connection,
            receipt(date="2026-09-25"),
        )

    assert len(candidates) == 1
    assert candidates[0]["merchant_raw"] == "Example Market"
