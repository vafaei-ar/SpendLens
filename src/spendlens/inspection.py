import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from spendlens.models import ReceiptExtraction
from spendlens.validation import ValidationReport


@dataclass(frozen=True)
class InspectionCounts:
    sources: int
    ingestions: int
    extractions: int
    receipts: int
    open_reviews: int


@dataclass(frozen=True)
class LatestBundle:
    source: dict[str, Any]
    extraction: dict[str, Any] | None
    receipt: dict[str, Any] | None
    review: dict[str, Any] | None


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def get_counts(connection: sqlite3.Connection) -> InspectionCounts:
    row = connection.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM source_documents) AS sources,
            (SELECT COUNT(*) FROM source_ingestions) AS ingestions,
            (SELECT COUNT(*) FROM extraction_attempts) AS extractions,
            (SELECT COUNT(*) FROM receipts) AS receipts,
            (
                SELECT COUNT(*)
                FROM review_sessions
                WHERE status = 'open'
            ) AS open_reviews
        """
    ).fetchone()
    if row is None:
        raise RuntimeError("Failed to read SpendLens counts")
    return InspectionCounts(
        sources=int(row["sources"]),
        ingestions=int(row["ingestions"]),
        extractions=int(row["extractions"]),
        receipts=int(row["receipts"]),
        open_reviews=int(row["open_reviews"]),
    )


def get_latest_bundle(connection: sqlite3.Connection) -> LatestBundle | None:
    source_row = connection.execute(
        """
        SELECT
            sd.id AS source_id,
            sd.sha256,
            sd.relative_path,
            sd.mime_type,
            sd.byte_size,
            sd.created_at AS stored_at,
            si.id AS ingestion_id,
            si.source_type,
            si.original_filename,
            si.width,
            si.height,
            si.received_at
        FROM source_ingestions AS si
        JOIN source_documents AS sd
          ON sd.id = si.source_document_id
        ORDER BY si.id DESC
        LIMIT 1
        """
    ).fetchone()
    if source_row is None:
        return None

    source_id = int(source_row["source_id"])
    extraction = connection.execute(
        """
        SELECT *
        FROM extraction_attempts
        WHERE source_document_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (source_id,),
    ).fetchone()
    receipt = connection.execute(
        """
        SELECT r.*
        FROM receipt_sources AS rs
        JOIN receipts AS r
          ON r.id = rs.receipt_id
        WHERE rs.source_document_id = ?
        ORDER BY r.id DESC
        LIMIT 1
        """,
        (source_id,),
    ).fetchone()
    review = connection.execute(
        """
        SELECT *
        FROM review_sessions
        WHERE source_document_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (source_id,),
    ).fetchone()

    return LatestBundle(
        source=dict(source_row),
        extraction=_row_dict(extraction),
        receipt=_row_dict(receipt),
        review=_row_dict(review),
    )


def recent_receipts(
    connection: sqlite3.Connection,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            r.id,
            r.merchant_raw,
            r.transaction_date,
            r.total_minor,
            r.currency,
            r.currency_exponent,
            r.status,
            r.created_at,
            rs.source_document_id
        FROM receipts AS r
        LEFT JOIN receipt_sources AS rs
          ON rs.receipt_id = r.id
        ORDER BY r.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def recent_extractions(
    connection: sqlite3.Connection,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            id,
            source_document_id,
            receipt_id,
            provider,
            model_id,
            status,
            parsed_json,
            validation_json,
            error_text,
            auto_accepted,
            created_at
        FROM extraction_attempts
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def open_reviews(
    connection: sqlite3.Connection,
    *,
    telegram_user_id: int | None = None,
) -> list[dict[str, Any]]:
    if telegram_user_id is None:
        rows = connection.execute(
            """
            SELECT *
            FROM review_sessions
            WHERE status = 'open'
            ORDER BY id DESC
            """
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT *
            FROM review_sessions
            WHERE status = 'open'
              AND telegram_user_id = ?
            ORDER BY id DESC
            """,
            (telegram_user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_receipt(
    connection: sqlite3.Connection,
    receipt_id: int | None = None,
) -> dict[str, Any] | None:
    if receipt_id is None:
        receipt = connection.execute(
            "SELECT * FROM receipts ORDER BY id DESC LIMIT 1"
        ).fetchone()
    else:
        receipt = connection.execute(
            "SELECT * FROM receipts WHERE id = ?",
            (receipt_id,),
        ).fetchone()
    if receipt is None:
        return None

    result = dict(receipt)
    source = connection.execute(
        """
        SELECT
            sd.id AS source_id,
            sd.sha256,
            sd.mime_type,
            sd.byte_size,
            sd.relative_path
        FROM receipt_sources AS rs
        JOIN source_documents AS sd
          ON sd.id = rs.source_document_id
        WHERE rs.receipt_id = ?
        ORDER BY sd.id
        LIMIT 1
        """,
        (int(receipt["id"]),),
    ).fetchone()
    items = connection.execute(
        """
        SELECT
            id,
            description_raw,
            quantity,
            unit_price_minor,
            amount_minor,
            category
        FROM line_items
        WHERE receipt_id = ?
        ORDER BY id
        """,
        (int(receipt["id"]),),
    ).fetchall()

    result["source"] = _row_dict(source)
    result["line_items"] = [dict(item) for item in items]
    return result


def get_source(
    connection: sqlite3.Connection,
    source_id: int | None = None,
) -> dict[str, Any] | None:
    if source_id is None:
        row = connection.execute(
            """
            SELECT *
            FROM source_documents
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    else:
        row = connection.execute(
            "SELECT * FROM source_documents WHERE id = ?",
            (source_id,),
        ).fetchone()
    return _row_dict(row)


def _format_minor(
    amount_minor: int | None,
    exponent: int,
    currency: str,
) -> str:
    if amount_minor is None:
        return "unknown"
    amount = Decimal(amount_minor) / (Decimal(10) ** exponent)
    return f"{amount:.{exponent}f} {currency}"


def _parse_extraction(
    payload: str | None,
) -> ReceiptExtraction | None:
    if payload is None:
        return None
    try:
        return ReceiptExtraction.model_validate_json(payload)
    except Exception:
        return None


def _parse_validation(
    payload: str | None,
) -> ValidationReport | None:
    if payload is None:
        return None
    try:
        return ValidationReport.model_validate_json(payload)
    except Exception:
        return None


def format_status(
    counts: InspectionCounts,
    *,
    model_id: str,
    extraction_enabled: bool,
) -> str:
    state = "enabled" if extraction_enabled else "disabled"
    return (
        "SpendLens status\n"
        f"Sources stored: {counts.sources}\n"
        f"Telegram ingestions: {counts.ingestions}\n"
        f"Extraction attempts: {counts.extractions}\n"
        f"Saved receipts: {counts.receipts}\n"
        f"Open reviews: {counts.open_reviews}\n"
        f"Extraction: {state}\n"
        f"Model: {model_id}"
    )


def format_latest(bundle: LatestBundle) -> str:
    source = bundle.source
    size_kib = int(source["byte_size"]) / 1024
    dimensions = ""
    if source.get("width") and source.get("height"):
        dimensions = f" · {source['width']}x{source['height']}"

    lines = [
        "Latest upload",
        (
            f"Source #{source['source_id']} · {source['mime_type']} "
            f"· {size_kib:.1f} KiB{dimensions}"
        ),
        f"SHA-256: {str(source['sha256'])[:16]}…",
        f"Received: {source['received_at']}",
    ]

    if bundle.extraction is None:
        lines.append("Extraction: none")
    else:
        extraction = bundle.extraction
        parsed = _parse_extraction(extraction.get("parsed_json"))
        auto_text = (
            "yes" if int(extraction["auto_accepted"]) else "no"
        )
        lines.append(
            f"Extraction #{extraction['id']}: {extraction['status']} "
            f"· {extraction['model_id']} · auto-accept {auto_text}"
        )
        if parsed is not None:
            lines.append(
                "AI read: "
                f"{parsed.merchant or 'unknown'} · "
                f"{parsed.transaction_date or 'unknown'} · "
                f"{parsed.total if parsed.total is not None else 'unknown'} "
                f"{parsed.currency or ''}".rstrip()
            )
        if extraction.get("error_text"):
            lines.append(f"Error: {extraction['error_text']}")
        validation = _parse_validation(extraction.get("validation_json"))
        if validation is not None and validation.blockers:
            codes = ", ".join(issue.code for issue in validation.blockers)
            lines.append(f"Validation blockers: {codes}")

    if bundle.receipt is not None:
        receipt = bundle.receipt
        total = _format_minor(
            receipt["total_minor"],
            receipt["currency_exponent"],
            receipt["currency"],
        )
        lines.append(
            f"Saved receipt #{receipt['id']}: "
            f"{receipt['merchant_raw']} · "
            f"{receipt['transaction_date']} · {total}"
        )
    elif bundle.review is not None and bundle.review["status"] == "open":
        lines.append(f"Review: open #{bundle.review['id']}")
    else:
        lines.append("Saved receipt: none")

    lines.append(f"Original: /source {source['source_id']}")
    return "\n".join(lines)


def format_recent_receipts(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No saved receipts yet."

    lines = ["Recent receipts"]
    for row in rows:
        total = _format_minor(
            row["total_minor"],
            row["currency_exponent"],
            row["currency"],
        )
        lines.append(
            f"#{row['id']} · {row['transaction_date']} · "
            f"{row['merchant_raw']} · {total} · {row['status']}"
        )
    lines.append("Use /receipt <id> for details.")
    return "\n".join(lines)


def format_extractions(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No extraction attempts yet."

    lines = ["Recent extraction attempts"]
    for row in rows:
        parsed = _parse_extraction(row.get("parsed_json"))
        summary = ""
        if parsed is not None:
            summary = (
                f" · {parsed.merchant or 'unknown'}"
                f" · {parsed.total if parsed.total is not None else 'unknown'}"
                f" {parsed.currency or ''}"
            )
        auto_text = "auto" if int(row["auto_accepted"]) else "review"
        lines.append(
            f"#{row['id']} · source #{row['source_document_id']} · "
            f"{row['status']} · {auto_text} · {row['model_id']}{summary}"
        )
    return "\n".join(lines)


def format_reviews(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No open reviews."

    lines = ["Open reviews"]
    for row in rows:
        proposed = _parse_extraction(row.get("proposed_json"))
        validation = _parse_validation(row.get("validation_json"))
        if proposed is None:
            summary = "unreadable proposed JSON"
        else:
            summary = (
                f"{proposed.merchant or 'unknown'} · "
                f"{proposed.transaction_date or 'unknown'} · "
                f"{proposed.total if proposed.total is not None else 'unknown'} "
                f"{proposed.currency or ''}".rstrip()
            )
        blocker_text = ""
        if validation is not None and validation.blockers:
            blocker_text = " · " + ", ".join(
                issue.code for issue in validation.blockers
            )
        lines.append(
            f"#{row['id']} · source #{row['source_document_id']} · "
            f"{summary}{blocker_text}"
        )
    lines.append("Use /accept <id>, /discard <id>, or reply with a correction.")
    return "\n".join(lines)


def format_receipt(receipt: dict[str, Any]) -> str:
    exponent = int(receipt["currency_exponent"])
    currency = str(receipt["currency"])
    lines = [
        f"Receipt #{receipt['id']}",
        f"Merchant: {receipt['merchant_raw']}",
        f"Date: {receipt['transaction_date']}",
        f"Time: {receipt['transaction_time'] or 'unknown'}",
        f"Subtotal: {_format_minor(receipt['subtotal_minor'], exponent, currency)}",
        f"Tax: {_format_minor(receipt['tax_minor'], exponent, currency)}",
        f"Tip: {_format_minor(receipt['tip_minor'], exponent, currency)}",
        f"Fees: {_format_minor(receipt['fees_minor'], exponent, currency)}",
        f"Discount: {_format_minor(receipt['discount_minor'], exponent, currency)}",
        f"Total: {_format_minor(receipt['total_minor'], exponent, currency)}",
        f"Type: {receipt['transaction_type']}",
        f"Status: {receipt['status']}",
    ]

    source = receipt.get("source")
    if source is not None:
        lines.append(
            f"Source #{source['source_id']} · "
            f"{str(source['sha256'])[:16]}…"
        )
        lines.append(f"Original: /source {source['source_id']}")

    items = receipt.get("line_items", [])
    lines.append(f"Line items: {len(items)}")
    for item in items[:20]:
        lines.append(
            f"- {item['description_raw']} · "
            f"{_format_minor(item['amount_minor'], exponent, currency)}"
        )
    if len(items) > 20:
        lines.append(f"…and {len(items) - 20} more")

    return "\n".join(lines)


def compact_json(payload: str | None) -> str | None:
    if payload is None:
        return None
    try:
        return json.dumps(json.loads(payload), separators=(",", ":"))
    except (json.JSONDecodeError, TypeError):
        return payload
