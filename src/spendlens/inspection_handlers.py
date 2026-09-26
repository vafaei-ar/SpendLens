from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from spendlens.config import Settings
from spendlens.db import connect
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


_HELP_TEXT = """SpendLens commands

/status - database and model status
/last - inspect the latest uploaded receipt end to end
/recent - recent saved receipts
/extractions - recent AI extraction attempts
/reviews - open receipts needing review
/receipt [id] - show a saved receipt; omit id for latest
/source [id] - send the exact stored source; omit id for latest
/accept <review_id> - accept a reviewed receipt
/discard <review_id> - discard structured data, keep source
/help - show this list
"""


def _authorized(
    update: Update,
    settings: Settings,
) -> bool:
    user = update.effective_user
    return (
        user is not None
        and user.id in settings.telegram_allowed_user_ids
    )


def _optional_positive_int(
    context: ContextTypes.DEFAULT_TYPE,
) -> int | None:
    if not context.args:
        return None
    if len(context.args) != 1:
        raise ValueError
    value = int(context.args[0])
    if value <= 0:
        raise ValueError
    return value


def _list_limit(
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    value = _optional_positive_int(context)
    if value is None:
        return 10
    return min(value, 20)


async def help_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _authorized(update, settings):
        return
    if update.effective_message is not None:
        await update.effective_message.reply_text(_HELP_TEXT)


async def status_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _authorized(update, settings):
        return
    message = update.effective_message
    if message is None:
        return

    database_path = context.application.bot_data["database_path"]
    with connect(database_path) as connection:
        counts = get_counts(connection)

    await message.reply_text(
        format_status(
            counts,
            model_id=settings.ollama_model,
            extraction_enabled=settings.extraction_enabled,
        )
    )


async def last_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _authorized(update, settings):
        return
    message = update.effective_message
    if message is None:
        return

    database_path = context.application.bot_data["database_path"]
    with connect(database_path) as connection:
        bundle = get_latest_bundle(connection)

    if bundle is None:
        await message.reply_text("No receipt uploads have been recorded yet.")
        return
    await message.reply_text(format_latest(bundle))


async def recent_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _authorized(update, settings):
        return
    message = update.effective_message
    if message is None:
        return

    try:
        limit = _list_limit(context)
    except (ValueError, TypeError):
        await message.reply_text("Use: /recent [1-20]")
        return

    database_path = context.application.bot_data["database_path"]
    with connect(database_path) as connection:
        rows = recent_receipts(connection, limit=limit)
    await message.reply_text(format_recent_receipts(rows))


async def extractions_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _authorized(update, settings):
        return
    message = update.effective_message
    if message is None:
        return

    try:
        limit = _list_limit(context)
    except (ValueError, TypeError):
        await message.reply_text("Use: /extractions [1-20]")
        return

    database_path = context.application.bot_data["database_path"]
    with connect(database_path) as connection:
        rows = recent_extractions(connection, limit=limit)
    await message.reply_text(format_extractions(rows))


async def reviews_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _authorized(update, settings):
        return
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    database_path = context.application.bot_data["database_path"]
    with connect(database_path) as connection:
        rows = open_reviews(
            connection,
            telegram_user_id=user.id,
        )
    await message.reply_text(format_reviews(rows))


async def receipt_detail_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _authorized(update, settings):
        return
    message = update.effective_message
    if message is None:
        return

    try:
        receipt_id = _optional_positive_int(context)
    except (ValueError, TypeError):
        await message.reply_text("Use: /receipt [receipt_id]")
        return

    database_path = context.application.bot_data["database_path"]
    with connect(database_path) as connection:
        receipt = get_receipt(connection, receipt_id)

    if receipt is None:
        target = "latest" if receipt_id is None else f"#{receipt_id}"
        await message.reply_text(f"Receipt {target} was not found.")
        return
    await message.reply_text(format_receipt(receipt))


def _safe_source_path(
    data_dir: Path,
    relative_path: str,
) -> Path:
    root = data_dir.resolve()
    target = (root / relative_path).resolve()
    if not target.is_relative_to(root):
        raise ValueError("Stored source path escapes the SpendLens data directory")
    return target


async def source_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _authorized(update, settings):
        return
    message = update.effective_message
    if message is None:
        return

    try:
        source_id = _optional_positive_int(context)
    except (ValueError, TypeError):
        await message.reply_text("Use: /source [source_id]")
        return

    database_path = context.application.bot_data["database_path"]
    with connect(database_path) as connection:
        source = get_source(connection, source_id)

    if source is None:
        target = "latest" if source_id is None else f"#{source_id}"
        await message.reply_text(f"Source {target} was not found.")
        return

    try:
        source_path = _safe_source_path(
            settings.data_dir,
            str(source["relative_path"]),
        )
    except ValueError as exc:
        await message.reply_text(f"Cannot read source: {exc}")
        return

    if not source_path.exists():
        await message.reply_text(
            f"Source #{source['id']} exists in SQLite, "
            "but the stored file is missing."
        )
        return

    caption = (
        f"Exact stored source #{source['id']}\n"
        f"{source['mime_type']} · {source['byte_size']} bytes\n"
        f"SHA-256: {str(source['sha256'])[:20]}…"
    )
    with source_path.open("rb") as handle:
        await message.reply_document(
            document=handle,
            filename=source_path.name,
            caption=caption,
        )


def register_inspection_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("help", help_handler))
    application.add_handler(CommandHandler("status", status_handler))
    application.add_handler(CommandHandler("last", last_handler))
    application.add_handler(CommandHandler("recent", recent_handler))
    application.add_handler(CommandHandler("receipts", recent_handler))
    application.add_handler(
        CommandHandler("extractions", extractions_handler)
    )
    application.add_handler(CommandHandler("reviews", reviews_handler))
    application.add_handler(
        CommandHandler("receipt", receipt_detail_handler)
    )
    application.add_handler(CommandHandler("source", source_handler))
