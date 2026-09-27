import asyncio
import logging

from telegram import BotCommand, BotCommandScopeChat, MenuButtonCommands, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from spendlens.analytics_handlers import (
    answer_analytics_question,
    build_query_planner,
    register_analytics_handlers,
)
from spendlens.config import Settings
from spendlens.db import (
    close_review_session,
    connect,
    create_review_session,
    find_possible_duplicates,
    get_open_review_by_id,
    get_open_review_by_message,
    get_open_review_for_source,
    initialize_database,
    link_extraction_to_receipt,
    persist_receipt,
    record_extraction_attempt,
    record_source_document,
    record_telegram_ingestion,
    set_review_message_id,
    source_receipt_id,
    update_review_session,
)
from spendlens.inspection_handlers import register_inspection_handlers
from spendlens.models import ReceiptExtraction
from spendlens.pipeline import ReceiptExtractionPipeline
from spendlens.pipeline_factory import build_receipt_pipeline
from spendlens.review import apply_correction, format_review
from spendlens.storage import store_source_bytes
from spendlens.validation import VALIDATOR_VERSION, validate_receipt

LOGGER = logging.getLogger("spendlens.bot")
SCHEMA_VERSION = "1"
MAX_CONCURRENT_UPDATES = 8

BOT_COMMANDS = [
    BotCommand("start", "Start SpendLens and show a quick introduction"),
    BotCommand("help", "Show available commands"),
    BotCommand("ask", "Ask a question about recorded purchases"),
    BotCommand("status", "Show database and extraction pipeline status"),
    BotCommand("last", "Inspect the latest receipt upload end to end"),
    BotCommand("recent", "Show recent saved receipts"),
    BotCommand("extractions", "Show recent AI/OCR extraction attempts"),
    BotCommand("reviews", "Show receipts waiting for review"),
    BotCommand("receipt", "Show a saved receipt, optionally by ID"),
    BotCommand("source", "Send the exact stored source, optionally by ID"),
    BotCommand("accept", "Accept a reviewed receipt by review ID"),
    BotCommand("discard", "Discard reviewed data but keep source evidence"),
]


async def configure_bot_commands(application: Application) -> None:
    settings: Settings = application.bot_data["settings"]

    for user_id in settings.telegram_allowed_user_ids:
        scope = BotCommandScopeChat(chat_id=user_id)
        await application.bot.set_my_commands(
            BOT_COMMANDS,
            scope=scope,
        )
        await application.bot.set_chat_menu_button(
            chat_id=user_id,
            menu_button=MenuButtonCommands(),
        )



def _is_authorized(update: Update, settings: Settings) -> bool:
    user = update.effective_user
    return (
        user is not None
        and user.id in settings.telegram_allowed_user_ids
    )


def _saved_summary(
    extraction: ReceiptExtraction,
    *,
    receipt_id: int,
    corrected: bool = False,
) -> str:
    prefix = "✅ Saved after review" if corrected else "✅ Saved automatically"
    item_text = f"Line items: {len(extraction.line_items)}"
    if extraction.item_count is not None:
        item_text += f" / printed count {extraction.item_count}"

    return (
        f"{prefix} as receipt #{receipt_id}\n"
        f"Merchant: {extraction.merchant}\n"
        f"Date: {extraction.transaction_date}\n"
        f"Total: {extraction.total} {extraction.currency}\n"
        f"{item_text}"
    )


async def start_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _is_authorized(update, settings):
        return

    message = update.effective_message
    if message is not None:
        await message.reply_text(
            "SpendLens is running. Send a receipt photo or image file. "
            "PDFs are preserved now, but PDF extraction is not enabled yet. "
            "Use /last to inspect the latest upload, or ask a spending "
            "question directly."
        )


async def receipt_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _is_authorized(update, settings):
        return

    message = update.effective_message
    if message is None:
        return

    supported = bool(message.photo)
    declared_size: int | None = None
    if message.photo:
        declared_size = message.photo[-1].file_size
    elif message.document:
        mime_type = message.document.mime_type or "application/octet-stream"
        supported = (
            mime_type.startswith("image/")
            or mime_type == "application/pdf"
        )
        declared_size = message.document.file_size

    lock: asyncio.Lock = context.application.bot_data[
        "receipt_processing_lock"
    ]

    if (
        supported
        and (
            declared_size is None
            or declared_size <= settings.max_source_bytes
        )
    ):
        if lock.locked():
            await message.reply_text(
                "📥 Receipt received and queued. "
                "Another receipt is being processed."
            )
        else:
            await message.reply_text(
                "📥 Receipt received. Reading receipt…"
            )

    async with lock:
        await _receipt_handler_serial(update, context)


async def _receipt_handler_serial(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _is_authorized(update, settings):
        return

    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    original_filename: str | None = None
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
        if not (
            mime_type.startswith("image/")
            or mime_type == "application/pdf"
        ):
            await message.reply_text("Please send a receipt image or PDF.")
            return
        file_id = document.file_id
        file_unique_id = document.file_unique_id
        source_type = "telegram_document"
        width = None
        height = None
        declared_size = document.file_size
        original_filename = document.file_name
    else:
        return

    if (
        declared_size is not None
        and declared_size > settings.max_source_bytes
    ):
        await message.reply_text(
            "This source is larger than the configured upload limit."
        )
        return

    telegram_file = await context.bot.get_file(file_id)
    payload = bytes(await telegram_file.download_as_bytearray())

    if len(payload) > settings.max_source_bytes:
        await message.reply_text(
            "This source is larger than the configured upload limit."
        )
        return

    stored = store_source_bytes(
        payload,
        data_dir=settings.data_dir,
        mime_type=mime_type,
    )

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
            original_filename=original_filename,
            width=width,
            height=height,
        )
        existing_receipt_id = source_receipt_id(
            connection,
            source_document_id,
        )
        open_review = get_open_review_for_source(
            connection,
            source_document_id,
        )

    if existing_receipt_id is not None:
        await message.reply_text(
            "This exact source is already linked to "
            f"receipt #{existing_receipt_id}."
        )
        return

    if open_review is not None:
        await message.reply_text(
            "This exact source is already awaiting review as "
            f"#{open_review['id']}."
        )
        return

    if mime_type == "application/pdf":
        await message.reply_text(
            "Source preserved locally. PDF extraction is not enabled yet."
        )
        return

    if not settings.extraction_enabled:
        await message.reply_text(
            "Source preserved locally. Extraction is disabled."
        )
        return

    pipeline: ReceiptExtractionPipeline = context.application.bot_data[
        "pipeline"
    ]
    cascade = await asyncio.to_thread(
        pipeline.run,
        payload,
        mime_type=mime_type,
    )

    if cascade.chosen_attempt is None:
        with connect(database_path) as connection:
            for attempt in cascade.attempts:
                record_extraction_attempt(
                    connection,
                    source_document_id=source_document_id,
                    provider=attempt.provider,
                    model_id=attempt.model_id,
                    prompt_version=attempt.prompt_version,
                    schema_version=(
                        attempt.extraction.schema_version
                        if attempt.extraction is not None
                        else SCHEMA_VERSION
                    ),
                    validator_version=VALIDATOR_VERSION,
                    status=attempt.status,
                    raw_response=attempt.raw_response,
                    parsed_json=(
                        attempt.extraction.model_dump_json()
                        if attempt.extraction is not None
                        else None
                    ),
                    validation_json=(
                        attempt.validation.model_dump_json()
                        if attempt.validation is not None
                        else None
                    ),
                    error_text=attempt.error_text,
                    auto_accepted=False,
                )
        await message.reply_text(
            "Source preserved, but the configured extraction cascade "
            "could not produce usable structured data. "
            "Use /extractions to inspect each attempt."
        )
        return

    chosen = cascade.chosen_attempt
    extraction = chosen.extraction
    if extraction is None:
        raise RuntimeError("Chosen extraction attempt has no receipt data")

    with connect(database_path) as connection:
        duplicates = find_possible_duplicates(connection, extraction)
        report = validate_receipt(
            extraction,
            possible_duplicate=bool(duplicates),
        )

        attempt_ids: list[int] = []
        for index, attempt in enumerate(cascade.attempts):
            attempt_report = (
                report
                if index == cascade.chosen_index
                else attempt.validation
            )
            attempt_ids.append(
                record_extraction_attempt(
                    connection,
                    source_document_id=source_document_id,
                    provider=attempt.provider,
                    model_id=attempt.model_id,
                    prompt_version=attempt.prompt_version,
                    schema_version=(
                        attempt.extraction.schema_version
                        if attempt.extraction is not None
                        else SCHEMA_VERSION
                    ),
                    validator_version=VALIDATOR_VERSION,
                    status=attempt.status,
                    raw_response=attempt.raw_response,
                    parsed_json=(
                        attempt.extraction.model_dump_json()
                        if attempt.extraction is not None
                        else None
                    ),
                    validation_json=(
                        attempt_report.model_dump_json()
                        if attempt_report is not None
                        else None
                    ),
                    error_text=attempt.error_text,
                    auto_accepted=(
                        attempt_report.auto_accept
                        if attempt_report is not None
                        else False
                    ),
                )
            )

        if cascade.chosen_index is None:
            raise RuntimeError("Chosen extraction index is missing")
        chosen_attempt_id = attempt_ids[cascade.chosen_index]

        if report.auto_accept:
            receipt_id = persist_receipt(
                connection,
                extraction=extraction,
                source_document_id=source_document_id,
                status="accepted",
                actor="system:auto_accept",
            )
            link_extraction_to_receipt(
                connection,
                extraction_attempt_id=chosen_attempt_id,
                receipt_id=receipt_id,
            )
        else:
            receipt_id = None
            review_id = create_review_session(
                connection,
                extraction_attempt_id=chosen_attempt_id,
                source_document_id=source_document_id,
                telegram_chat_id=message.chat_id,
                telegram_user_id=user.id,
                proposed_json=extraction.model_dump_json(),
                validation_json=report.model_dump_json(),
            )

    if receipt_id is not None:
        await message.reply_text(
            _saved_summary(extraction, receipt_id=receipt_id)
        )
        return

    review_message = await message.reply_text(
        format_review(
            review_id=review_id,
            extraction=extraction,
            report=report,
        )
    )
    with connect(database_path) as connection:
        set_review_message_id(
            connection,
            review_id=review_id,
            message_id=review_message.message_id,
        )


async def correction_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _is_authorized(update, settings):
        return

    message = update.effective_message
    user = update.effective_user
    if message is None or user is None or message.text is None:
        return

    if message.reply_to_message is None:
        await answer_analytics_question(
            update,
            context,
            question=message.text,
        )
        return

    database_path = context.application.bot_data["database_path"]
    with connect(database_path) as connection:
        review = get_open_review_by_message(
            connection,
            telegram_chat_id=message.chat_id,
            message_id=message.reply_to_message.message_id,
        )

    if review is None:
        await answer_analytics_question(
            update,
            context,
            question=message.text,
        )
        return

    with connect(database_path) as connection:
        current = ReceiptExtraction.model_validate_json(
            review["proposed_json"]
        )
        try:
            corrected = apply_correction(current, message.text)
        except ValueError as exc:
            await message.reply_text(str(exc))
            return

        duplicates = find_possible_duplicates(connection, corrected)
        report = validate_receipt(
            corrected,
            possible_duplicate=bool(duplicates),
        )
        update_review_session(
            connection,
            review_id=int(review["id"]),
            proposed_json=corrected.model_dump_json(),
            validation_json=report.model_dump_json(),
        )

        if report.auto_accept:
            receipt_id = persist_receipt(
                connection,
                extraction=corrected,
                source_document_id=int(review["source_document_id"]),
                status="corrected",
                actor=f"telegram_user:{user.id}",
            )
            link_extraction_to_receipt(
                connection,
                extraction_attempt_id=int(
                    review["extraction_attempt_id"]
                ),
                receipt_id=receipt_id,
            )
            close_review_session(
                connection,
                review_id=int(review["id"]),
                status="accepted",
            )
        else:
            receipt_id = None

    if receipt_id is not None:
        await message.reply_text(
            _saved_summary(
                corrected,
                receipt_id=receipt_id,
                corrected=True,
            )
        )
        return

    updated_message = await message.reply_text(
        format_review(
            review_id=int(review["id"]),
            extraction=corrected,
            report=report,
        )
    )
    with connect(database_path) as connection:
        set_review_message_id(
            connection,
            review_id=int(review["id"]),
            message_id=updated_message.message_id,
        )


def _review_id_from_args(
    context: ContextTypes.DEFAULT_TYPE,
) -> int | None:
    if len(context.args) != 1:
        return None
    try:
        return int(context.args[0])
    except ValueError:
        return None


async def accept_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _is_authorized(update, settings):
        return

    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    review_id = _review_id_from_args(context)
    if review_id is None:
        await message.reply_text("Use: /accept <review_id>")
        return

    database_path = context.application.bot_data["database_path"]
    with connect(database_path) as connection:
        review = get_open_review_by_id(
            connection,
            review_id=review_id,
            telegram_chat_id=message.chat_id,
            telegram_user_id=user.id,
        )
        if review is None:
            await message.reply_text("Open review not found.")
            return

        extraction = ReceiptExtraction.model_validate_json(
            review["proposed_json"]
        )
        report = validate_receipt(extraction)
        item_blockers = {
            issue.code
            for issue in report.blockers
            if issue.code in {
                "line_items_missing",
                "line_items_incomplete",
            }
        }
        if item_blockers:
            await message.reply_text(
                "Cannot accept yet: purchased-item extraction is incomplete. "
                "Discard this review and resend/reprocess the source."
            )
            return

        try:
            receipt_id = persist_receipt(
                connection,
                extraction=extraction,
                source_document_id=int(review["source_document_id"]),
                status="corrected",
                actor=f"telegram_user:{user.id}",
            )
        except ValueError as exc:
            await message.reply_text(
                f"Cannot accept yet: {exc}"
            )
            return

        link_extraction_to_receipt(
            connection,
            extraction_attempt_id=int(
                review["extraction_attempt_id"]
            ),
            receipt_id=receipt_id,
        )
        close_review_session(
            connection,
            review_id=review_id,
            status="accepted",
        )

    await message.reply_text(
        _saved_summary(
            extraction,
            receipt_id=receipt_id,
            corrected=True,
        )
    )


async def discard_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _is_authorized(update, settings):
        return

    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    review_id = _review_id_from_args(context)
    if review_id is None:
        await message.reply_text("Use: /discard <review_id>")
        return

    database_path = context.application.bot_data["database_path"]
    with connect(database_path) as connection:
        review = get_open_review_by_id(
            connection,
            review_id=review_id,
            telegram_chat_id=message.chat_id,
            telegram_user_id=user.id,
        )
        if review is None:
            await message.reply_text("Open review not found.")
            return
        close_review_session(
            connection,
            review_id=review_id,
            status="discarded",
        )

    await message.reply_text(
        f"Review #{review_id} discarded. Source evidence was retained."
    )


def build_application(settings: Settings) -> Application:
    database_path = initialize_database(settings.data_dir)
    pipeline = build_receipt_pipeline(settings)
    application = (
        Application.builder()
        .token(settings.telegram_bot_token.get_secret_value())
        .concurrent_updates(MAX_CONCURRENT_UPDATES)
        .post_init(configure_bot_commands)
        .build()
    )
    application.bot_data["settings"] = settings
    application.bot_data["database_path"] = database_path
    application.bot_data["pipeline"] = pipeline
    application.bot_data["receipt_processing_lock"] = asyncio.Lock()
    if settings.analytics_enabled:
        application.bot_data["query_planner"] = build_query_planner(
            settings
        )

    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CommandHandler("accept", accept_handler))
    application.add_handler(CommandHandler("discard", discard_handler))
    register_inspection_handlers(application)
    register_analytics_handlers(application)
    application.add_handler(
        MessageHandler(
            filters.PHOTO | filters.Document.ALL,
            receipt_handler,
        )
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            correction_handler,
        )
    )
    return application


def _configure_logging(secret: str) -> None:
    class SecretFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            message = record.getMessage()
            if secret and secret in message:
                record.msg = message.replace(secret, "<redacted>")
                record.args = ()
            return True

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    root = logging.getLogger()
    for handler in root.handlers:
        handler.addFilter(SecretFilter())


def main() -> None:
    settings = Settings()
    _configure_logging(settings.telegram_bot_token.get_secret_value())
    application = build_application(settings)
    application.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
