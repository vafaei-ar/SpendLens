import json

import httpx
import pytest

from spendlens.extraction import (
    ExtractionParseError,
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
