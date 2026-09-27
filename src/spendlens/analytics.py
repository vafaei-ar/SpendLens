import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from spendlens.normalize import normalize_merchant
from spendlens.query_spec import QueryGroupBy, QueryMetric, QuerySpec


class AnalyticsError(RuntimeError):
    pass


@dataclass(frozen=True)
class AnalyticsResult:
    text: str


def _money(minor: int, exponent: int, currency: str) -> str:
    value = Decimal(minor) / (Decimal(10) ** exponent)
    return f"{value:.{exponent}f} {currency}"


def _date_scope(spec: QuerySpec) -> str:
    if spec.start_date is not None and spec.end_date is not None:
        return f" from {spec.start_date} through {spec.end_date}"
    if spec.start_date is not None:
        return f" since {spec.start_date}"
    if spec.end_date is not None:
        return f" through {spec.end_date}"
    return ""


def _merchant_conditions(
    spec: QuerySpec,
    *,
    alias: str = "r",
) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if spec.start_date is not None:
        clauses.append(f"{alias}.transaction_date >= ?")
        params.append(spec.start_date.isoformat())
    if spec.end_date is not None:
        clauses.append(f"{alias}.transaction_date <= ?")
        params.append(spec.end_date.isoformat())

    if spec.merchants:
        merchant_parts: list[str] = []
        for merchant in spec.merchants:
            normalized = normalize_merchant(merchant)
            merchant_parts.append(
                f"{alias}.merchant_normalized LIKE ?"
            )
            params.append(f"%{normalized}%")
        clauses.append("(" + " OR ".join(merchant_parts) + ")")

    return clauses, params


def _item_conditions(
    spec: QuerySpec,
    *,
    receipt_alias: str = "r",
    item_alias: str = "li",
) -> tuple[list[str], list[Any]]:
    clauses, params = _merchant_conditions(
        spec,
        alias=receipt_alias,
    )

    if spec.categories:
        placeholders = ",".join("?" for _ in spec.categories)
        clauses.append(f"{item_alias}.category IN ({placeholders})")
        params.extend(category.value for category in spec.categories)

    if spec.subcategories:
        placeholders = ",".join("?" for _ in spec.subcategories)
        clauses.append(
            f"{item_alias}.subcategory IN ({placeholders})"
        )
        params.extend(
            subcategory.value for subcategory in spec.subcategories
        )

    if spec.item_terms:
        item_parts: list[str] = []
        for term in spec.item_terms:
            item_parts.append(
                "lower(COALESCE("
                f"{item_alias}.description_normalized, "
                f"{item_alias}.description_raw)) LIKE ?"
            )
            params.append(f"%{term.casefold()}%")
        clauses.append("(" + " OR ".join(item_parts) + ")")

    return clauses, params


def _where(clauses: list[str]) -> str:
    if not clauses:
        return ""
    return " WHERE " + " AND ".join(clauses)


def _receipt_group_expression(
    group_by: QueryGroupBy | None,
) -> tuple[str | None, str | None]:
    if group_by is None:
        return None, None
    mapping = {
        QueryGroupBy.DAY: ("r.transaction_date", "day"),
        QueryGroupBy.WEEK: (
            "strftime('%Y-W%W', r.transaction_date)",
            "week",
        ),
        QueryGroupBy.MONTH: (
            "substr(r.transaction_date, 1, 7)",
            "month",
        ),
        QueryGroupBy.MERCHANT: ("r.merchant_raw", "merchant"),
    }
    if group_by not in mapping:
        raise AnalyticsError(
            f"{group_by.value} grouping is not valid for receipt totals"
        )
    return mapping[group_by]


def _item_group_expression(
    group_by: QueryGroupBy | None,
) -> tuple[str | None, str | None]:
    if group_by is None:
        return None, None
    mapping = {
        QueryGroupBy.DAY: ("r.transaction_date", "day"),
        QueryGroupBy.WEEK: (
            "strftime('%Y-W%W', r.transaction_date)",
            "week",
        ),
        QueryGroupBy.MONTH: (
            "substr(r.transaction_date, 1, 7)",
            "month",
        ),
        QueryGroupBy.CATEGORY: (
            "COALESCE(li.category, 'uncategorized')",
            "category",
        ),
        QueryGroupBy.SUBCATEGORY: (
            "COALESCE(li.subcategory, 'uncategorized')",
            "subcategory",
        ),
        QueryGroupBy.MERCHANT: ("r.merchant_raw", "merchant"),
        QueryGroupBy.ITEM: (
            "COALESCE(NULLIF(li.description_normalized, ''), "
            "li.description_raw)",
            "item",
        ),
    }
    return mapping[group_by]


def _scope_description(spec: QuerySpec) -> str:
    if spec.item_terms:
        return ", ".join(spec.item_terms)
    if spec.subcategories:
        return ", ".join(value.value for value in spec.subcategories)
    if spec.categories:
        return ", ".join(value.value for value in spec.categories)
    if spec.merchants:
        return ", ".join(spec.merchants)
    return "recorded purchases"


def _execute_receipt_metric(
    connection: sqlite3.Connection,
    spec: QuerySpec,
) -> AnalyticsResult:
    if spec.categories or spec.subcategories or spec.item_terms:
        raise AnalyticsError(
            "Receipt-total metrics cannot use item/category filters. "
            "Use item_spend or item_frequency instead."
        )

    group_expr, group_label = _receipt_group_expression(spec.group_by)
    clauses, params = _merchant_conditions(spec)
    where_sql = _where(clauses)

    if spec.metric == QueryMetric.TRANSACTION_COUNT:
        select_group = (
            f"{group_expr} AS group_value, "
            if group_expr is not None
            else ""
        )
        group_sql = (
            f" GROUP BY {group_expr}" if group_expr is not None else ""
        )
        order_sql = (
            " ORDER BY transaction_count DESC"
            if group_expr is not None
            else ""
        )
        rows = connection.execute(
            f"""
            SELECT
                {select_group}
                COUNT(*) AS transaction_count
            FROM receipts AS r
            {where_sql}
            {group_sql}
            {order_sql}
            LIMIT ?
            """,
            (*params, spec.limit),
        ).fetchall()

        if not rows:
            return AnalyticsResult(
                "No recorded receipts match that question."
            )

        if group_expr is None:
            return AnalyticsResult(
                "Among purchases recorded in SpendLens"
                f"{_date_scope(spec)}, there are "
                f"{int(rows[0]['transaction_count'])} transactions."
            )

        lines = [
            "Recorded transaction count"
            f"{_date_scope(spec)} by {group_label}:"
        ]
        for row in rows:
            lines.append(
                f"- {row['group_value']}: "
                f"{int(row['transaction_count'])}"
            )
        return AnalyticsResult("\n".join(lines))

    select_group = (
        f"{group_expr} AS group_value, "
        if group_expr is not None
        else ""
    )
    group_fields = (
        f"{group_expr}, " if group_expr is not None else ""
    )
    rows = connection.execute(
        f"""
        SELECT
            {select_group}
            r.currency,
            r.currency_exponent,
            COUNT(*) AS receipt_count,
            SUM(r.total_minor) AS total_minor
        FROM receipts AS r
        {where_sql}
        GROUP BY {group_fields}r.currency, r.currency_exponent
        ORDER BY total_minor DESC
        LIMIT ?
        """,
        (*params, spec.limit),
    ).fetchall()

    if not rows:
        return AnalyticsResult(
            "No recorded receipts match that question."
        )

    scope = _scope_description(spec)
    if spec.metric == QueryMetric.TOTAL_SPEND:
        if group_expr is None:
            lines = [
                "Among purchases recorded in SpendLens"
                f"{_date_scope(spec)}, total spend for {scope}:"
            ]
            for row in rows:
                lines.append(
                    f"- {_money(int(row['total_minor']), int(row['currency_exponent']), str(row['currency']))} "
                    f"across {int(row['receipt_count'])} receipts"
                )
            return AnalyticsResult("\n".join(lines))

        lines = [
            "Recorded spend"
            f"{_date_scope(spec)} by {group_label}:"
        ]
        for row in rows:
            lines.append(
                f"- {row['group_value']}: "
                f"{_money(int(row['total_minor']), int(row['currency_exponent']), str(row['currency']))}"
            )
        return AnalyticsResult("\n".join(lines))

    if spec.metric == QueryMetric.AVERAGE_TRANSACTION:
        lines = [
            "Average recorded transaction"
            f"{_date_scope(spec)}"
            + (f" by {group_label}:" if group_label else ":")
        ]
        for row in rows:
            count = int(row["receipt_count"])
            total = Decimal(int(row["total_minor"]))
            average_minor = int(
                (total / count).to_integral_value()
            )
            prefix = (
                f"{row['group_value']}: "
                if group_expr is not None
                else ""
            )
            lines.append(
                f"- {prefix}"
                f"{_money(average_minor, int(row['currency_exponent']), str(row['currency']))} "
                f"across {count} receipts"
            )
        return AnalyticsResult("\n".join(lines))

    raise AnalyticsError(
        f"Unsupported receipt metric: {spec.metric.value}"
    )


def _execute_item_metric(
    connection: sqlite3.Connection,
    spec: QuerySpec,
) -> AnalyticsResult:
    group_expr, group_label = _item_group_expression(spec.group_by)
    clauses, params = _item_conditions(spec)
    where_sql = _where(clauses)
    select_group = (
        f"{group_expr} AS group_value, "
        if group_expr is not None
        else ""
    )
    group_fields = (
        f"{group_expr}, " if group_expr is not None else ""
    )

    rows = connection.execute(
        f"""
        SELECT
            {select_group}
            r.currency,
            r.currency_exponent,
            COUNT(*) AS line_count,
            COUNT(DISTINCT r.id) AS receipt_count,
            SUM(li.amount_minor) AS amount_minor
        FROM line_items AS li
        JOIN receipts AS r ON r.id = li.receipt_id
        {where_sql}
        GROUP BY {group_fields}r.currency, r.currency_exponent
        ORDER BY
            {"line_count" if spec.metric == QueryMetric.ITEM_FREQUENCY else "amount_minor"} DESC
        LIMIT ?
        """,
        (*params, spec.limit),
    ).fetchall()

    if not rows:
        return AnalyticsResult(
            "No recorded line items match that question."
        )

    scope = _scope_description(spec)
    if spec.metric == QueryMetric.ITEM_SPEND:
        if group_expr is None:
            lines = [
                "Among purchases recorded in SpendLens"
                f"{_date_scope(spec)}, item-level spend for {scope}:"
            ]
            for row in rows:
                lines.append(
                    f"- {_money(int(row['amount_minor']), int(row['currency_exponent']), str(row['currency']))} "
                    f"across {int(row['line_count'])} item lines in "
                    f"{int(row['receipt_count'])} receipts"
                )
            return AnalyticsResult("\n".join(lines))

        lines = [
            "Recorded item-level spend"
            f"{_date_scope(spec)} by {group_label}:"
        ]
        for row in rows:
            lines.append(
                f"- {row['group_value']}: "
                f"{_money(int(row['amount_minor']), int(row['currency_exponent']), str(row['currency']))} "
                f"({int(row['line_count'])} purchases)"
            )
        return AnalyticsResult("\n".join(lines))

    if spec.metric == QueryMetric.ITEM_FREQUENCY:
        if group_expr is None:
            total_occurrences = sum(int(row["line_count"]) for row in rows)
            return AnalyticsResult(
                "Among purchases recorded in SpendLens"
                f"{_date_scope(spec)}, {scope} appears "
                f"{total_occurrences} times in extracted line items."
            )

        lines = [
            "Recorded purchase frequency"
            f"{_date_scope(spec)} by {group_label}:"
        ]
        for row in rows:
            lines.append(
                f"- {row['group_value']}: "
                f"{int(row['line_count'])} purchases"
            )
        return AnalyticsResult("\n".join(lines))

    raise AnalyticsError(
        f"Unsupported item metric: {spec.metric.value}"
    )


def _execute_unusual_items(
    connection: sqlite3.Connection,
    spec: QuerySpec,
    *,
    today: date,
) -> AnalyticsResult:
    end_date = spec.end_date or today
    start_date = spec.start_date or (end_date - timedelta(days=29))

    history_clauses: list[str] = ["r.transaction_date < ?"]
    history_params: list[Any] = [start_date.isoformat()]
    if spec.merchants:
        merchant_parts: list[str] = []
        for merchant in spec.merchants:
            merchant_parts.append("r.merchant_normalized LIKE ?")
            history_params.append(
                f"%{normalize_merchant(merchant)}%"
            )
        history_clauses.append(
            "(" + " OR ".join(merchant_parts) + ")"
        )

    history_receipts = int(
        connection.execute(
            f"""
            SELECT COUNT(*)
            FROM receipts AS r
            WHERE {" AND ".join(history_clauses)}
            """,
            history_params,
        ).fetchone()[0]
    )
    if history_receipts < 5:
        return AnalyticsResult(
            "There is not enough prior receipt history to identify unusual "
            f"items reliably. SpendLens has {history_receipts} receipts "
            f"before {start_date}; at least 5 are required."
        )

    target_spec = spec.model_copy(
        update={
            "start_date": start_date,
            "end_date": end_date,
        }
    )
    target_clauses, target_params = _item_conditions(target_spec)
    target_where = _where(target_clauses)

    history_item_clauses = ["r.transaction_date < ?"]
    history_item_params: list[Any] = [start_date.isoformat()]
    if spec.merchants:
        merchant_parts = []
        for merchant in spec.merchants:
            merchant_parts.append("r.merchant_normalized LIKE ?")
            history_item_params.append(
                f"%{normalize_merchant(merchant)}%"
            )
        history_item_clauses.append(
            "(" + " OR ".join(merchant_parts) + ")"
        )

    key_expr = (
        "lower(trim(COALESCE(NULLIF(li.description_normalized, ''), "
        "li.description_raw)))"
    )
    display_expr = (
        "COALESCE(NULLIF(li.description_normalized, ''), "
        "li.description_raw)"
    )

    rows = connection.execute(
        f"""
        WITH target AS (
            SELECT
                {key_expr} AS item_key,
                {display_expr} AS item_name,
                COALESCE(li.category, 'uncategorized') AS category,
                COALESCE(li.subcategory, 'uncategorized') AS subcategory,
                r.currency AS currency,
                r.currency_exponent AS currency_exponent,
                COUNT(*) AS target_count,
                SUM(li.amount_minor) AS target_amount_minor
            FROM line_items AS li
            JOIN receipts AS r ON r.id = li.receipt_id
            {target_where}
            GROUP BY
                item_key,
                item_name,
                category,
                subcategory,
                r.currency,
                r.currency_exponent
        ),
        history AS (
            SELECT
                {key_expr} AS item_key,
                COUNT(*) AS prior_count,
                MAX(r.transaction_date) AS last_prior_date
            FROM line_items AS li
            JOIN receipts AS r ON r.id = li.receipt_id
            WHERE {" AND ".join(history_item_clauses)}
            GROUP BY item_key
        )
        SELECT
            target.*,
            COALESCE(history.prior_count, 0) AS prior_count,
            history.last_prior_date
        FROM target
        LEFT JOIN history USING (item_key)
        WHERE COALESCE(history.prior_count, 0) <= ?
        ORDER BY
            prior_count ASC,
            target_count DESC,
            target_amount_minor DESC
        LIMIT ?
        """,
        (
            *target_params,
            *history_item_params,
            spec.unusual_max_prior_purchases,
            spec.limit,
        ),
    ).fetchall()

    if not rows:
        return AnalyticsResult(
            "No unusual items matched that definition in the requested "
            f"period ({start_date} through {end_date})."
        )

    lines = [
        "Unusual recorded items "
        f"from {start_date} through {end_date}:",
        (
            "Definition: purchased in this period and bought no more than "
            f"{spec.unusual_max_prior_purchases} times in the prior "
            f"{history_receipts} recorded receipts."
        ),
    ]
    for row in rows:
        prior = int(row["prior_count"])
        previous = (
            f", last prior {row['last_prior_date']}"
            if row["last_prior_date"]
            else ", never seen before"
        )
        lines.append(
            f"- {row['item_name']}: {int(row['target_count'])} this period, "
            f"{prior} prior{previous}; "
            f"{_money(int(row['target_amount_minor']), int(row['currency_exponent']), str(row['currency']))}"
        )
    return AnalyticsResult("\n".join(lines))


def execute_analytics(
    connection: sqlite3.Connection,
    spec: QuerySpec,
    *,
    today: date | None = None,
) -> AnalyticsResult:
    current_date = today or date.today()

    if spec.metric in {
        QueryMetric.TOTAL_SPEND,
        QueryMetric.TRANSACTION_COUNT,
        QueryMetric.AVERAGE_TRANSACTION,
    }:
        return _execute_receipt_metric(connection, spec)

    if spec.metric in {
        QueryMetric.ITEM_SPEND,
        QueryMetric.ITEM_FREQUENCY,
    }:
        return _execute_item_metric(connection, spec)

    if spec.metric == QueryMetric.UNUSUAL_ITEMS:
        return _execute_unusual_items(
            connection,
            spec,
            today=current_date,
        )

    raise AnalyticsError(
        f"Unsupported analytics metric: {spec.metric.value}"
    )
