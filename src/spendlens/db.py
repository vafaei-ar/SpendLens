import sqlite3
from pathlib import Path

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
    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY,
    merchant_raw TEXT NOT NULL,
    merchant_normalized TEXT NOT NULL,
    transaction_date TEXT NOT NULL,
    transaction_time TEXT,
    currency TEXT NOT NULL CHECK (length(currency) = 3),
    currency_exponent INTEGER NOT NULL DEFAULT 2,
    subtotal_minor INTEGER,
    tax_minor INTEGER,
    tip_minor INTEGER,
    fees_minor INTEGER,
    discount_minor INTEGER,
    total_minor INTEGER NOT NULL,
    transaction_type TEXT NOT NULL CHECK (transaction_type IN ('purchase', 'refund', 'return')),
    status TEXT NOT NULL CHECK (status IN ('accepted', 'corrected', 'manual_review')),
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

CREATE TABLE IF NOT EXISTS receipt_extractions (
    id INTEGER PRIMARY KEY,
    source_document_id INTEGER NOT NULL REFERENCES source_documents(id),
    receipt_id INTEGER REFERENCES receipts(id),
    model_id TEXT NOT NULL,
    model_version TEXT,
    prompt_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    validator_version TEXT NOT NULL,
    extraction_json TEXT NOT NULL,
    validation_json TEXT NOT NULL,
    auto_accepted INTEGER NOT NULL CHECK (auto_accepted IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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

CREATE INDEX IF NOT EXISTS idx_receipts_date ON receipts(transaction_date);
CREATE INDEX IF NOT EXISTS idx_receipts_merchant_total_date ON receipts(merchant_normalized, total_minor, transaction_date);
CREATE INDEX IF NOT EXISTS idx_extractions_source ON receipt_extractions(source_document_id);
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


def record_source_document(connection: sqlite3.Connection, source: StoredSource) -> int:
    connection.execute(
        """INSERT OR IGNORE INTO source_documents
        (sha256, relative_path, mime_type, byte_size) VALUES (?, ?, ?, ?)""",
        (source.sha256, source.relative_path, source.mime_type, source.byte_size),
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
) -> int:
    cursor = connection.execute(
        """INSERT INTO source_ingestions (
            source_document_id, source_type, telegram_file_id, telegram_file_unique_id,
            telegram_message_id, telegram_chat_id
        ) VALUES (?, ?, ?, ?, ?, ?)""",
        (
            source_document_id,
            source_type,
            telegram_file_id,
            telegram_file_unique_id,
            telegram_message_id,
            telegram_chat_id,
        ),
    )
    return int(cursor.lastrowid)
