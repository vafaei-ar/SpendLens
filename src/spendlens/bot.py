import logging
from typing import Any

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from spendlens.config import Settings
from spendlens.db import connect, initialize_database, record_source_document, record_telegram_ingestion
from spendlens.storage import store_source_bytes


LOGGER = logging.getLogger("spendlens.bot")


def _is_authorized(update: Update, settings: Settings) -> bool:
    user = update.effective_user
    return user is not None and user.id in settings.telegram_allowed_user_ids


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _is_authorized(update, settings):
        return

    if update.effective_message is not None:
        await update.effective_message.reply_text(
            "SpendLens is running. Send a receipt photo or PDF. "
            "At this bootstrap milestone, SpendLens preserves the source locally; "
            "receipt extraction is the next step."
        )


async def receipt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _is_authorized(update, settings):
        return

    message = update.effective_message
    if message is None:
        return

    if message.photo:
        photo = message.photo[-1]
        file_id = photo.file_id
        file_unique_id = photo.file_unique_id
        mime_type = "image/jpeg"
        source_type = "telegram_photo"
        width = photo.width
        height = photo.height
        declared_size = photo.file_size
    elif message.document:
        document = message.document
        mime_type = document.mime_type or "application/octet-stream"
        if not (mime_type.startswith("image/") or mime_type == "application/pdf"):
            await message.reply_text("Please send a receipt image or PDF.")
            return
        file_id = document.file_id
        file_unique_id = document.file_unique_id
        source_type = "telegram_document"
        width = None
        height = None
        declared_size = document.file_size
    else:
        return

    if declared_size is not None and declared_size > settings.max_source_bytes:
        await message.reply_text("This source is larger than the configured upload limit.")
        return

    telegram_file = await context.bot.get_file(file_id)
    payload = bytes(await telegram_file.download_as_bytearray())

    if len(payload) > settings.max_source_bytes:
        await message.reply_text("This source is larger than the configured upload limit.")
        return

    stored = store_source_bytes(payload, data_dir=settings.data_dir, mime_type=mime_type)

    database_path = context.application.bot_data["database_path"]
    with connect(database_path) as connection:
        source_document_id = record_source_document(connection, stored)
        record_telegram_ingestion(
            connection,
            source_document_id=source_document_id,
            source_type=source_type,
            telegram_file_id=file_id,
            telegram_file_unique_id=file_unique_id,
            telegram_message_id=message.message_id,
            telegram_chat_id=message.chat_id,
        )

    LOGGER.info(
        "Stored receipt source sha256=%s created=%s dimensions=%sx%s",
        stored.sha256,
        stored.created,
        width,
        height,
    )
    duplicate_note = " Exact source already existed." if not stored.created else ""
    await message.reply_text(
        f"Source preserved locally: {stored.sha256[:12]}...{duplicate_note} "
        "Extraction is not enabled yet."
    )


def build_application(settings: Settings) -> Application[Any, Any, Any, Any, Any, Any]:
    database_path = initialize_database(settings.data_dir)
    application = Application.builder().token(settings.telegram_bot_token.get_secret_value()).build()
    application.bot_data["settings"] = settings
    application.bot_data["database_path"] = database_path
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, receipt_handler))
    return application


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings()
    application = build_application(settings)
    application.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
