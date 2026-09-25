import sqlite3
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from spendlens.models import ReceiptExtraction
from spendlens.money import currency_exponent, decimal_to_minor
from spendlens.normalize import normalize_merchant
from spendlens.storage import StoredSource

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS source_documents (
    id INTEGER PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    relative_path TEXT NOT NULL UNIQUE,
    mime_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK (byte_size > 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_ingestions (
    id INTEGER PRIMARY KEY,
    source_document_id INTEGER NOT NULL REFERENCES source_documents(id),
    source_type TEXT NOT NULL,
    telegram_file_id TEXT,
    telegram_file_unique_id TEXT,
    telegram_message_id INTEGER,
    telegram_chat_id INTEGER,
    original_filename TEXT,
    width INTEGER,
    height INTEGER,
    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY,
    merchant_raw TEXT NOT NULL,
    merchant_normalized TEXT NOT NULL,
    transaction_date TEXT NOT NULL,
    transaction_time TEXT,
    currency TEXT NOT NULL CHECK (length(currency) = 3),
    currency_exponent INTEGER NOT NULL,
    subtotal_minor INTEGER,
    tax_minor INTEGER,
    tip_minor INTEGER,
    fees_minor INTEGER,
    discount_minor INTEGER,
    total_minor INTEGER NOT NULL,
    transaction_type TEXT NOT NULL CHECK (
        transaction_type IN ('purchase', 'refund', 'return')
    ),
    status TEXT NOT NULL CHECK (
        status IN ('accepted', 'corrected', 'manual_review')
    ),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS receipt_sources (
    receipt_id INTEGER NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
    source_document_id INTEGER NOT NULL REFERENCES source_documents(id),
    relationship TEXT NOT NULL DEFAULT 'primary',
    PRIMARY KEY (receipt_id, source_document_id)
);

CREATE TABLE IF NOT EXISTS line_items (
    id INTEGER PRIMARY KEY,
    receipt_id INTEGER NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
    description_raw TEXT NOT NULL,
    description_normalized TEXT,
    quantity TEXT,
    unit_price_minor INTEGER,
    amount_minor INTEGER NOT NULL,
    category TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS extraction_attempts (
    id INTEGER PRIMARY KEY,
    source_document_id INTEGER NOT NULL REFERENCES source_documents(id),
    receipt_id INTEGER REFERENCES receipts(id),
    provider TEXT NOT NULL,
    model_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    validator_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('parsed', 'invalid_schema', 'provider_error')
    ),
    raw_response TEXT,
    parsed_json TEXT,
    validation_json TEXT,
    error_text TEXT,
    auto_accepted INTEGER NOT NULL CHECK (auto_accepted IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS review_sessions (
    id INTEGER PRIMARY KEY,
    extraction_attempt_id INTEGER NOT NULL REFERENCES extraction_attempts(id),
    source_document_id INTEGER NOT NULL REFERENCES source_documents(id),
    telegram_chat_id INTEGER NOT NULL,
    telegram_user_id INTEGER NOT NULL,
    review_message_id INTEGER,
    proposed_json TEXT NOT NULL,
    validation_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('open', 'accepted', 'discarded')
    ),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY,
    receipt_id INTEGER REFERENCES receipts(id),
    source_document_id INTEGER REFERENCES source_documents(id),
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_receipts_date
    ON receipts(transaction_date);

CREATE INDEX IF NOT EXISTS idx_receipts_merchant_total_date
    ON receipts(merchant_normalized, total_minor, transaction_date);

CREATE INDEX IF NOT EXISTS idx_extraction_attempts_source
    ON extraction_attempts(source_document_id);

CREATE INDEX IF NOT EXISTS idx_review_message
    ON review_sessions(telegram_chat_id, review_message_id, status);
"""


def initialize_database(data_dir: Path) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    database_path = data_dir / "spendlens.sqlite"
    with connect(database_path) as connection:
        connection.executescript(SCHEMA)
    return database_path


def connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def record_source_document(
    connection: sqlite3.Connection,
    source: StoredSource,
) -> int:
    connection.execute(
        """
        INSERT OR IGNORE INTO source_documents
            (sha256, relative_path, mime_type, byte_size)
        VALUES (?, ?, ?, ?)
        """,
        (
            source.sha256,
            source.relative_path,
            source.mime_type,
            source.byte_size,
        ),
    )
    row = connection.execute(
        "SELECT id FROM source_documents WHERE sha256 = ?",
        (source.sha256,),
    ).fetchone()
    if row is None:
        raise RuntimeError("Failed to record source document")
    return int(row["id"])


def record_telegram_ingestion(
    connection: sqlite3.Connection,
    *,
    source_document_id: int,
    source_type: str,
    telegram_file_id: str | None,
    telegram_file_unique_id: str | None,
    telegram_message_id: int | None,
    telegram_chat_id: int | None,
    original_filename: str | None = None,
    width: int | None = None,
    height: int | None = None,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO source_ingestions (
            source_document_id,
            source_type,
            telegram_file_id,
            telegram_file_unique_id,
            telegram_message_id,
            telegram_chat_id,
            original_filename,
            width,
            height
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_document_id,
            source_type,
            telegram_file_id,
            telegram_file_unique_id,
            telegram_message_id,
            telegram_chat_id,
            original_filename,
            width,
            height,
        ),
    )
    return int(cursor.lastrowid)


def source_receipt_id(
    connection: sqlite3.Connection,
    source_document_id: int,
) -> int | None:
    row = connection.execute(
        """
        SELECT receipt_id
        FROM receipt_sources
        WHERE source_document_id = ?
        ORDER BY receipt_id
        LIMIT 1
        """,
        (source_document_id,),
    ).fetchone()
    return int(row["receipt_id"]) if row is not None else None


def find_possible_duplicates(
    connection: sqlite3.Connection,
    receipt: ReceiptExtraction,
    *,
    day_tolerance: int = 1,
) -> list[sqlite3.Row]:
    if (
        receipt.merchant is None
        or receipt.transaction_date is None
        or receipt.total is None
        or receipt.currency is None
    ):
        return []

    exponent = currency_exponent(receipt.currency)
    total_minor = decimal_to_minor(receipt.total, exponent)
    start_date = receipt.transaction_date - timedelta(days=day_tolerance)
    end_date = receipt.transaction_date + timedelta(days=day_tolerance)

    return connection.execute(
        """
        SELECT id, merchant_raw, transaction_date, total_minor, currency
        FROM receipts
        WHERE merchant_normalized = ?
          AND total_minor = ?
          AND currency = ?
          AND transaction_date BETWEEN ? AND ?
        ORDER BY transaction_date, id
        """,
        (
            normalize_merchant(receipt.merchant),
            total_minor,
            receipt.currency,
            start_date.isoformat(),
            end_date.isoformat(),
        ),
    ).fetchall()


def record_extraction_attempt(
    connection: sqlite3.Connection,
    *,
    source_document_id: int,
    provider: str,
    model_id: str,
    prompt_version: str,
    schema_version: str,
    validator_version: str,
    status: str,
    raw_response: str | None,
    parsed_json: str | None,
    validation_json: str | None,
    error_text: str | None,
    auto_accepted: bool,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO extraction_attempts (
            source_document_id,
            provider,
            model_id,
            prompt_version,
            schema_version,
            validator_version,
            status,
            raw_response,
            parsed_json,
            validation_json,
            error_text,
            auto_accepted
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_document_id,
            provider,
            model_id,
            prompt_version,
            schema_version,
            validator_version,
            status,
            raw_response,
            parsed_json,
            validation_json,
            error_text,
            int(auto_accepted),
        ),
    )
    return int(cursor.lastrowid)


def _minor_or_none(
    value: Decimal | None,
    exponent: int,
) -> int | None:
    if value is None:
        return None
    return decimal_to_minor(value, exponent)


def persist_receipt(
    connection: sqlite3.Connection,
    *,
    extraction: ReceiptExtraction,
    source_document_id: int,
    status: str,
    actor: str,
) -> int:
    if extraction.merchant is None:
        raise ValueError("Merchant is required to persist a receipt")
    if extraction.transaction_date is None:
        raise ValueError("Transaction date is required to persist a receipt")
    if extraction.total is None:
        raise ValueError("Total is required to persist a receipt")
    if extraction.currency is None:
        raise ValueError("Currency is required to persist a receipt")

    exponent = currency_exponent(extraction.currency)
    cursor = connection.execute(
        """
        INSERT INTO receipts (
            merchant_raw,
            merchant_normalized,
            transaction_date,
            transaction_time,
            currency,
            currency_exponent,
            subtotal_minor,
            tax_minor,
            tip_minor,
            fees_minor,
            discount_minor,
            total_minor,
            transaction_type,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            extraction.merchant,
            normalize_merchant(extraction.merchant),
            extraction.transaction_date.isoformat(),
            (
                extraction.transaction_time.isoformat()
                if extraction.transaction_time is not None
                else None
            ),
            extraction.currency,
            exponent,
            _minor_or_none(extraction.subtotal, exponent),
            _minor_or_none(extraction.tax, exponent),
            _minor_or_none(extraction.tip, exponent),
            _minor_or_none(extraction.fees, exponent),
            _minor_or_none(extraction.discount, exponent),
            decimal_to_minor(extraction.total, exponent),
            extraction.transaction_type.value,
            status,
        ),
    )
    receipt_id = int(cursor.lastrowid)

    connection.execute(
        """
        INSERT INTO receipt_sources (
            receipt_id,
            source_document_id,
            relationship
        )
        VALUES (?, ?, 'primary')
        """,
        (receipt_id, source_document_id),
    )

    for item in extraction.line_items:
        connection.execute(
            """
            INSERT INTO line_items (
                receipt_id,
                description_raw,
                quantity,
                unit_price_minor,
                amount_minor,
                category
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                receipt_id,
                item.description_raw,
                str(item.quantity) if item.quantity is not None else None,
                _minor_or_none(item.unit_price, exponent),
                decimal_to_minor(item.amount, exponent),
                item.category,
            ),
        )

    connection.execute(
        """
        INSERT INTO audit_events (
            receipt_id,
            source_document_id,
            event_type,
            actor,
            after_json
        )
        VALUES (?, ?, 'receipt_created', ?, ?)
        """,
        (
            receipt_id,
            source_document_id,
            actor,
            extraction.model_dump_json(),
        ),
    )
    return receipt_id


def link_extraction_to_receipt(
    connection: sqlite3.Connection,
    *,
    extraction_attempt_id: int,
    receipt_id: int,
) -> None:
    connection.execute(
        """
        UPDATE extraction_attempts
        SET receipt_id = ?
        WHERE id = ?
        """,
        (receipt_id, extraction_attempt_id),
    )


def create_review_session(
    connection: sqlite3.Connection,
    *,
    extraction_attempt_id: int,
    source_document_id: int,
    telegram_chat_id: int,
    telegram_user_id: int,
    proposed_json: str,
    validation_json: str,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO review_sessions (
            extraction_attempt_id,
            source_document_id,
            telegram_chat_id,
            telegram_user_id,
            proposed_json,
            validation_json,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, 'open')
        """,
        (
            extraction_attempt_id,
            source_document_id,
            telegram_chat_id,
            telegram_user_id,
            proposed_json,
            validation_json,
        ),
    )
    return int(cursor.lastrowid)


def set_review_message_id(
    connection: sqlite3.Connection,
    *,
    review_id: int,
    message_id: int,
) -> None:
    connection.execute(
        """
        UPDATE review_sessions
        SET review_message_id = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status = 'open'
        """,
        (message_id, review_id),
    )


def get_open_review_by_message(
    connection: sqlite3.Connection,
    *,
    telegram_chat_id: int,
    message_id: int,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT *
        FROM review_sessions
        WHERE telegram_chat_id = ?
          AND review_message_id = ?
          AND status = 'open'
        ORDER BY id DESC
        LIMIT 1
        """,
        (telegram_chat_id, message_id),
    ).fetchone()


def get_open_review_by_id(
    connection: sqlite3.Connection,
    *,
    review_id: int,
    telegram_chat_id: int,
    telegram_user_id: int,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT *
        FROM review_sessions
        WHERE id = ?
          AND telegram_chat_id = ?
          AND telegram_user_id = ?
          AND status = 'open'
        """,
        (review_id, telegram_chat_id, telegram_user_id),
    ).fetchone()


def get_open_review_for_source(
    connection: sqlite3.Connection,
    source_document_id: int,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT *
        FROM review_sessions
        WHERE source_document_id = ?
          AND status = 'open'
        ORDER BY id DESC
        LIMIT 1
        """,
        (source_document_id,),
    ).fetchone()


def update_review_session(
    connection: sqlite3.Connection,
    *,
    review_id: int,
    proposed_json: str,
    validation_json: str,
) -> None:
    connection.execute(
        """
        UPDATE review_sessions
        SET proposed_json = ?,
            validation_json = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status = 'open'
        """,
        (proposed_json, validation_json, review_id),
    )


def close_review_session(
    connection: sqlite3.Connection,
    *,
    review_id: int,
    status: str,
) -> None:
    if status not in {"accepted", "discarded"}:
        raise ValueError("Review status must be accepted or discarded")
    connection.execute(
        """
        UPDATE review_sessions
        SET status = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status = 'open'
        """,
        (status, review_id),
    )
