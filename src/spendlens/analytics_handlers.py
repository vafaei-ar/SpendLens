import asyncio

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from spendlens.analytics import AnalyticsError, execute_analytics
from spendlens.config import Settings
from spendlens.db import connect
from spendlens.query_planner import GeminiQueryPlanner, QueryPlanningError
from spendlens.query_spec import QueryStatus


def build_query_planner(settings: Settings) -> GeminiQueryPlanner:
    if settings.gemini_api_key is None:
        raise ValueError("Analytics requires GEMINI_API_KEY")
    api_key = settings.gemini_api_key.get_secret_value().strip()
    if not api_key:
        raise ValueError("Analytics requires a non-empty GEMINI_API_KEY")
    return GeminiQueryPlanner(
        api_key=api_key,
        model=settings.query_model,
    )


def _authorized(update: Update, settings: Settings) -> bool:
    user = update.effective_user
    return (
        user is not None
        and user.id in settings.telegram_allowed_user_ids
    )


async def answer_analytics_question(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    question: str | None = None,
) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not _authorized(update, settings):
        return

    message = update.effective_message
    if message is None:
        return

    if not settings.analytics_enabled:
        await message.reply_text("SpendLens analytics is disabled.")
        return

    text = question
    if text is None:
        text = " ".join(context.args) if context.args else message.text

    if text is None or not text.strip():
        await message.reply_text(
            "Ask a spending question, for example: "
            "/ask how much did I spend on fruit?"
        )
        return

    planner: GeminiQueryPlanner = context.application.bot_data[
        "query_planner"
    ]
    try:
        decision = await asyncio.to_thread(planner.plan, text)
    except QueryPlanningError as exc:
        await message.reply_text(
            f"I could not translate that question safely: {exc}"
        )
        return

    if decision.status == QueryStatus.NEEDS_CLARIFICATION:
        await message.reply_text(
            decision.reason or "I need a little more detail."
        )
        return

    if decision.status == QueryStatus.UNSUPPORTED:
        await message.reply_text(
            decision.reason
            or "That question is outside the recorded receipt data."
        )
        return

    if decision.query is None:
        await message.reply_text(
            "I could not create a safe analytics query for that question."
        )
        return

    database_path = context.application.bot_data["database_path"]
    try:
        with connect(database_path) as connection:
            result = execute_analytics(connection, decision.query)
    except AnalyticsError as exc:
        await message.reply_text(
            f"I cannot answer that query safely yet: {exc}"
        )
        return

    await message.reply_text(result.text)


async def ask_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await answer_analytics_question(update, context)


def register_analytics_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("ask", ask_handler))
