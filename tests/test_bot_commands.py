import asyncio
from types import SimpleNamespace

from spendlens.bot import BOT_COMMANDS, configure_bot_commands


class FakeBot:
    def __init__(self) -> None:
        self.command_calls: list[tuple[int, list[str]]] = []
        self.menu_calls: list[int] = []

    async def set_my_commands(self, commands, *, scope) -> None:
        self.command_calls.append(
            (
                scope.chat_id,
                [command.command for command in commands],
            )
        )

    async def set_chat_menu_button(self, *, chat_id, menu_button) -> None:
        self.menu_calls.append(chat_id)


def test_configure_bot_commands_for_allowed_users() -> None:
    bot = FakeBot()
    application = SimpleNamespace(
        bot=bot,
        bot_data={
            "settings": SimpleNamespace(
                telegram_allowed_user_ids={123, 456},
            )
        },
    )

    asyncio.run(configure_bot_commands(application))

    assert {chat_id for chat_id, _ in bot.command_calls} == {123, 456}
    assert set(bot.menu_calls) == {123, 456}

    expected = {command.command for command in BOT_COMMANDS}
    assert "last" in expected
    assert "status" in expected
    assert "extractions" in expected
    assert "reviews" in expected
    assert "source" in expected
