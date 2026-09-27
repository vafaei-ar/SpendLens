import json

import httpx
import pytest

from spendlens.extraction import (
    ExtractionParseError,
    GeminiReceiptExtractor,
    OllamaVisionExtractor,
)


def valid_model_payload() -> dict[str, object]:
    return {
        "schema_version": "1",
        "merchant": "Example Market",
        "transaction_date": "2026-09-24",
        "transaction_time": None,
        "subtotal": "10.00",
        "tax": "0.60",
        "tip": None,
        "fees": None,
        "discount": None,
        "total": "10.60",
        "currency": "USD",
        "transaction_type": "purchase",
        "line_items": [],
    }


def test_ollama_extractor_requests_json_schema() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured.update(body)
        content = json.dumps(valid_model_payload())
        return httpx.Response(
            200,
            json={"message": {"content": content}},
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        extractor = OllamaVisionExtractor(
            base_url="http://ollama.test",
            model="qwen3-vl:4b",
            timeout_seconds=5,
            client=client,
        )
        result = extractor.extract(
            b"synthetic-image",
            mime_type="image/jpeg",
        )

    assert captured["model"] == "qwen3-vl:4b"
    assert isinstance(captured["format"], dict)
    messages = captured["messages"]
    assert isinstance(messages, list)
    assert messages[0]["images"]
    assert result.extraction.total is not None
    assert str(result.extraction.total) == "10.60"


def test_ollama_invalid_schema_is_not_silently_accepted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"message": {"content": '{"total": "abc"}'}},
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        extractor = OllamaVisionExtractor(
            base_url="http://ollama.test",
            model="qwen3-vl:4b",
            timeout_seconds=5,
            client=client,
        )
        with pytest.raises(ExtractionParseError):
            extractor.extract(
                b"synthetic-image",
                mime_type="image/jpeg",
            )



class FakeInteraction:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text


class FakeInteractions:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if "response_format" in kwargs:
            return FakeInteraction(json.dumps(valid_model_payload()))
        return FakeInteraction(
            "EXAMPLE MARKET\nAPPLE 10.00\n"
            "SUBTOTAL 10.00\nTAX 0.60\nTOTAL 10.60"
        )


class FakeGeminiClient:
    def __init__(self) -> None:
        self.interactions = FakeInteractions()


def test_gemini_transcription_uses_ultra_high_image_resolution(
    monkeypatch,
) -> None:
    extractor = GeminiReceiptExtractor(
        api_key="test-key",
        model="gemini-test",
    )
    client = FakeGeminiClient()
    monkeypatch.setattr(extractor, "_client", lambda: client)

    result = extractor.transcribe_image(
        b"synthetic-image",
        mime_type="image/jpeg",
    )

    assert "TOTAL 10.60" in result.text
    call = client.interactions.calls[0]
    inputs = call["input"]
    assert isinstance(inputs, list)
    assert inputs[0]["type"] == "text"
    assert inputs[1]["type"] == "image"
    assert inputs[1]["mime_type"] == "image/jpeg"
    assert inputs[1]["resolution"] == "ultra_high"


def test_gemini_structuring_uses_json_schema_object(
    monkeypatch,
) -> None:
    extractor = GeminiReceiptExtractor(
        api_key="test-key",
        model="gemini-test",
    )
    client = FakeGeminiClient()
    monkeypatch.setattr(extractor, "_client", lambda: client)

    result = extractor.extract_from_text(
        "SUBTOTAL 10.00\nTAX 0.60\nTOTAL 10.60"
    )

    call = client.interactions.calls[0]
    response_format = call["response_format"]
    assert isinstance(response_format, dict)
    assert response_format["type"] == "text"
    assert response_format["mime_type"] == "application/json"
    assert isinstance(response_format["schema"], dict)
    assert str(result.extraction.total) == "10.60"
