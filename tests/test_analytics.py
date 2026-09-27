from datetime import date

from spendlens.analytics import execute_analytics
from spendlens.db import (
    connect,
    initialize_database,
    persist_receipt,
    record_source_document,
)
from spendlens.models import ReceiptExtraction
from spendlens.query_spec import QuerySpec
from spendlens.storage import store_source_bytes


def _persist(
    connection,
    *,
    tmp_path,
    source_key: str,
    merchant: str,
    transaction_date: str,
    total: str,
    items: list[dict[str, object]],
) -> None:
    source = store_source_bytes(
        source_key.encode(),
        data_dir=tmp_path,
        mime_type="image/jpeg",
    )
    source_id = record_source_document(connection, source)
    extraction = ReceiptExtraction.model_validate(
        {
            "merchant": merchant,
            "transaction_date": transaction_date,
            "subtotal": total,
            "tax": "0.00",
            "total": total,
            "currency": "USD",
            "item_count": len(items),
            "line_items": items,
        }
    )
    persist_receipt(
        connection,
        extraction=extraction,
        source_document_id=source_id,
        status="accepted",
        actor="test",
    )


def test_item_spend_uses_line_items_not_receipt_totals(tmp_path) -> None:
    database_path = initialize_database(tmp_path)
    with connect(database_path) as connection:
        _persist(
            connection,
            tmp_path=tmp_path,
            source_key="sams",
            merchant="Sam's Club",
            transaction_date="2026-09-21",
            total="49.20",
            items=[
                {
                    "description_raw": "GOLDKIWI",
                    "description_normalized": "Gold kiwi",
                    "amount": "6.87",
                    "category": "groceries",
                    "subcategory": "fruit",
                },
                {
                    "description_raw": "PLUMS",
                    "description_normalized": "Plums",
                    "amount": "6.96",
                    "category": "groceries",
                    "subcategory": "fruit",
                },
                {
                    "description_raw": "BANANAS",
                    "description_normalized": "Bananas",
                    "amount": "1.47",
                    "category": "groceries",
                    "subcategory": "fruit",
                },
                {
                    "description_raw": "GROUND BEEF",
                    "description_normalized": "Ground beef",
                    "amount": "18.51",
                    "category": "groceries",
                    "subcategory": "meat_seafood",
                },
                {
                    "description_raw": "DAWN",
                    "description_normalized": "Dawn dish soap",
                    "amount": "9.98",
                    "category": "household",
                    "subcategory": "cleaning",
                },
            ],
        )

        result = execute_analytics(
            connection,
            QuerySpec(
                metric="item_spend",
                subcategories=["fruit"],
            ),
        )

    assert "15.30 USD" in result.text
    assert "3 item lines" in result.text
    assert "49.20" not in result.text


def test_merchant_spend_uses_receipt_totals(tmp_path) -> None:
    database_path = initialize_database(tmp_path)
    with connect(database_path) as connection:
        _persist(
            connection,
            tmp_path=tmp_path,
            source_key="sams",
            merchant="Sam's Club",
            transaction_date="2026-09-21",
            total="49.20",
            items=[
                {
                    "description_raw": "BANANAS",
                    "amount": "1.47",
                    "category": "groceries",
                    "subcategory": "fruit",
                }
            ],
        )
        _persist(
            connection,
            tmp_path=tmp_path,
            source_key="other",
            merchant="Other Market",
            transaction_date="2026-09-22",
            total="20.00",
            items=[
                {
                    "description_raw": "APPLE",
                    "amount": "20.00",
                    "category": "groceries",
                    "subcategory": "fruit",
                }
            ],
        )

        result = execute_analytics(
            connection,
            QuerySpec(
                metric="total_spend",
                merchants=["Sam's Club"],
            ),
        )

    assert "49.20 USD" in result.text
    assert "1 receipts" in result.text
    assert "20.00" not in result.text


def test_item_frequency_can_rank_products(tmp_path) -> None:
    database_path = initialize_database(tmp_path)
    with connect(database_path) as connection:
        for index, item in enumerate(["Bananas", "Bananas", "Plums"]):
            _persist(
                connection,
                tmp_path=tmp_path,
                source_key=f"receipt-{index}",
                merchant="Example Market",
                transaction_date=f"2026-09-{20 + index:02d}",
                total="5.00",
                items=[
                    {
                        "description_raw": item.upper(),
                        "description_normalized": item,
                        "amount": "5.00",
                        "category": "groceries",
                        "subcategory": "fruit",
                    }
                ],
            )

        result = execute_analytics(
            connection,
            QuerySpec(
                metric="item_frequency",
                subcategories=["fruit"],
                group_by="item",
            ),
        )

    lines = result.text.splitlines()
    assert "Bananas: 2 purchases" in lines[1]
    assert any("Plums: 1 purchases" in line for line in lines)


def test_unusual_items_use_prior_recorded_history(tmp_path) -> None:
    database_path = initialize_database(tmp_path)
    with connect(database_path) as connection:
        for index in range(5):
            _persist(
                connection,
                tmp_path=tmp_path,
                source_key=f"history-{index}",
                merchant="Example Market",
                transaction_date=f"2026-08-{10 + index:02d}",
                total="3.00",
                items=[
                    {
                        "description_raw": "APPLE",
                        "description_normalized": "Apple",
                        "amount": "3.00",
                        "category": "groceries",
                        "subcategory": "fruit",
                    }
                ],
            )

        _persist(
            connection,
            tmp_path=tmp_path,
            source_key="target",
            merchant="Example Market",
            transaction_date="2026-09-25",
            total="8.00",
            items=[
                {
                    "description_raw": "APPLE",
                    "description_normalized": "Apple",
                    "amount": "3.00",
                    "category": "groceries",
                    "subcategory": "fruit",
                },
                {
                    "description_raw": "DRAGONFRUIT",
                    "description_normalized": "Dragonfruit",
                    "amount": "5.00",
                    "category": "groceries",
                    "subcategory": "fruit",
                },
            ],
        )

        result = execute_analytics(
            connection,
            QuerySpec(
                metric="unusual_items",
                start_date=date(2026, 9, 1),
                end_date=date(2026, 9, 30),
                unusual_max_prior_purchases=1,
            ),
            today=date(2026, 9, 27),
        )

    assert "Dragonfruit" in result.text
    assert "never seen before" in result.text
    assert "Apple" not in result.text


def test_unusual_items_abstain_with_too_little_history(tmp_path) -> None:
    database_path = initialize_database(tmp_path)
    with connect(database_path) as connection:
        _persist(
            connection,
            tmp_path=tmp_path,
            source_key="only-history",
            merchant="Example Market",
            transaction_date="2026-08-10",
            total="3.00",
            items=[
                {
                    "description_raw": "APPLE",
                    "amount": "3.00",
                    "category": "groceries",
                    "subcategory": "fruit",
                }
            ],
        )

        result = execute_analytics(
            connection,
            QuerySpec(
                metric="unusual_items",
                start_date=date(2026, 9, 1),
                end_date=date(2026, 9, 30),
            ),
            today=date(2026, 9, 27),
        )

    assert "not enough prior receipt history" in result.text
    assert "at least 5 are required" in result.text
