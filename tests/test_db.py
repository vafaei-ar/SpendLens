import sqlite3

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



def test_rich_line_items_are_persisted_for_later_analytics(tmp_path) -> None:
    source = store_source_bytes(
        b"rich-line-items",
        data_dir=tmp_path,
        mime_type="image/jpeg",
    )
    database_path = initialize_database(tmp_path)
    extraction = ReceiptExtraction.model_validate(
        {
            "merchant": "Example Market",
            "transaction_date": "2026-09-24",
            "subtotal": "6.87",
            "tax": "0.00",
            "total": "6.87",
            "currency": "USD",
            "item_count": 1,
            "line_items": [
                {
                    "description_raw": "326086 GOLDKIWI 2LF",
                    "sku": "326086",
                    "description_normalized": "Gold kiwi 2 lb",
                    "brand": None,
                    "quantity": "1",
                    "unit_price": "6.87",
                    "discount": "0.00",
                    "amount": "6.87",
                    "category": "groceries",
                    "subcategory": "fruit",
                }
            ],
        }
    )

    with connect(database_path) as connection:
        source_id = record_source_document(connection, source)
        receipt_id = persist_receipt(
            connection,
            extraction=extraction,
            source_document_id=source_id,
            status="accepted",
            actor="test",
        )
        receipt_row = connection.execute(
            "SELECT item_count FROM receipts WHERE id = ?",
            (receipt_id,),
        ).fetchone()
        row = connection.execute(
            """
            SELECT
                sku,
                description_raw,
                description_normalized,
                brand,
                quantity,
                unit_price_minor,
                discount_minor,
                amount_minor,
                category,
                subcategory
            FROM line_items
            WHERE receipt_id = ?
            """,
            (receipt_id,),
        ).fetchone()

    assert receipt_row is not None
    assert receipt_row["item_count"] == 1
    assert row is not None
    assert row["sku"] == "326086"
    assert row["description_raw"] == "326086 GOLDKIWI 2LF"
    assert row["description_normalized"] == "Gold kiwi 2 lb"
    assert row["quantity"] == "1"
    assert row["unit_price_minor"] == 687
    assert row["discount_minor"] == 0
    assert row["amount_minor"] == 687
    assert row["category"] == "groceries"
    assert row["subcategory"] == "fruit"


def test_initialize_database_migrates_existing_line_items_table(tmp_path) -> None:
    database_path = tmp_path / "spendlens.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE line_items (
                id INTEGER PRIMARY KEY,
                receipt_id INTEGER NOT NULL,
                description_raw TEXT NOT NULL,
                description_normalized TEXT,
                quantity TEXT,
                unit_price_minor INTEGER,
                amount_minor INTEGER NOT NULL,
                category TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    initialize_database(tmp_path)

    with connect(database_path) as connection:
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(line_items)"
            ).fetchall()
        }

    assert {"sku", "brand", "discount_minor", "subcategory"} <= columns

    with connect(database_path) as connection:
        receipt_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(receipts)"
            ).fetchall()
        }

    assert "item_count" in receipt_columns
