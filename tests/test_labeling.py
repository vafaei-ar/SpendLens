from spendlens.labeling import interactive_truth


class FakeInput:
    def __init__(self, responses: list[str]) -> None:
        self._responses = iter(responses)

    def __call__(self, prompt: str) -> str:
        return next(self._responses)


def test_interactive_truth_reprompts_required_fields() -> None:
    messages: list[str] = []
    fake_input = FakeInput(
        [
            "",
            "Example Market",
            "",
            "09/26/2026",
            "2026-09-26",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "abc",
            "57.82",
            "",
            "",
        ]
    )

    truth = interactive_truth(
        input_fn=fake_input,
        output_fn=messages.append,
    )

    assert truth.merchant == "Example Market"
    assert truth.transaction_date is not None
    assert truth.transaction_date.isoformat() == "2026-09-26"
    assert truth.total is not None
    assert str(truth.total) == "57.82"
    assert truth.currency == "USD"
    assert truth.transaction_type.value == "purchase"
    assert any("Required field" in message for message in messages)
    assert any("Invalid date" in message for message in messages)
    assert any("Invalid amount" in message for message in messages)


def test_interactive_truth_validates_currency_and_type() -> None:
    messages: list[str] = []
    fake_input = FakeInput(
        [
            "Example Market",
            "2026-09-26",
            "",
            "",
            "",
            "",
            "",
            "",
            "10.60",
            "US",
            "usd",
            "sale",
            "refund",
        ]
    )

    truth = interactive_truth(
        input_fn=fake_input,
        output_fn=messages.append,
    )

    assert truth.currency == "USD"
    assert truth.transaction_type.value == "refund"
    assert any("3-letter code" in message for message in messages)
    assert any("purchase, refund, or return" in message for message in messages)
