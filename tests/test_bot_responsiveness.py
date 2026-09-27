import asyncio
from types import SimpleNamespace

from spendlens.bot import receipt_handler


class FakeMessage:
    def __init__(self) -> None:
        self.photo = [
            SimpleNamespace(file_size=1024),
        ]
        self.document = None
        self.replies: list[str] = []

    async def reply_text(self, text: str):
        self.replies.append(text)
        return SimpleNamespace(message_id=999)


def _context(lock: asyncio.Lock):
    return SimpleNamespace(
        application=SimpleNamespace(
            bot_data={
                "settings": SimpleNamespace(
                    telegram_allowed_user_ids={123},
                    max_source_bytes=1024 * 1024,
                ),
                "receipt_processing_lock": lock,
            }
        )
    )


def _update(message: FakeMessage):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=123),
        effective_message=message,
    )


def test_receipt_acknowledges_before_processing(monkeypatch) -> None:
    async def scenario() -> None:
        message = FakeMessage()
        processed: list[bool] = []

        async def fake_serial(update, context) -> None:
            processed.append(True)

        monkeypatch.setattr(
            "spendlens.bot._receipt_handler_serial",
            fake_serial,
        )

        await receipt_handler(
            _update(message),
            _context(asyncio.Lock()),
        )

        assert message.replies == [
            "📥 Receipt received. Reading receipt…"
        ]
        assert processed == [True]

    asyncio.run(scenario())


def test_second_receipt_is_queued_while_ocr_is_busy(monkeypatch) -> None:
    async def scenario() -> None:
        message = FakeMessage()
        lock = asyncio.Lock()
        await lock.acquire()
        processed: list[bool] = []

        async def fake_serial(update, context) -> None:
            processed.append(True)

        monkeypatch.setattr(
            "spendlens.bot._receipt_handler_serial",
            fake_serial,
        )

        task = asyncio.create_task(
            receipt_handler(
                _update(message),
                _context(lock),
            )
        )
        await asyncio.sleep(0)

        assert message.replies == [
            "📥 Receipt received and queued. "
            "Another receipt is being processed."
        ]
        assert processed == []

        lock.release()
        await task
        assert processed == [True]

    asyncio.run(scenario())
