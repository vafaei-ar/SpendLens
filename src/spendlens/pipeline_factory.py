from spendlens.config import Settings
from spendlens.extraction import (
    GeminiReceiptExtractor,
    MLXOCRExtractor,
    OllamaTextStructurer,
)
from spendlens.pipeline import ReceiptExtractionPipeline


def build_receipt_pipeline(
    settings: Settings,
) -> ReceiptExtractionPipeline:
    ocr_extractors = [
        MLXOCRExtractor(
            model=settings.local_ocr_primary_model,
            prompt="OCR:",
            max_tokens=settings.local_ocr_max_tokens,
        )
    ]

    if settings.local_ocr_secondary_enabled:
        ocr_extractors.append(
            MLXOCRExtractor(
                model=settings.local_ocr_secondary_model,
                prompt=(
                    "Extract all visible text from this receipt image. "
                    "Preserve original text and reading order. "
                    "Do not summarize or translate."
                ),
                max_tokens=settings.local_ocr_max_tokens,
            )
        )

    structurer = OllamaTextStructurer(
        base_url=settings.ollama_base_url,
        model=settings.ollama_structurer_model,
        timeout_seconds=settings.ollama_timeout_seconds,
    )

    gemini = None
    if settings.cloud_fallback_enabled:
        if settings.gemini_api_key is None:
            raise ValueError(
                "CLOUD_FALLBACK_ENABLED=true requires GEMINI_API_KEY"
            )
        api_key = settings.gemini_api_key.get_secret_value().strip()
        if not api_key:
            raise ValueError(
                "CLOUD_FALLBACK_ENABLED=true requires a non-empty GEMINI_API_KEY"
            )
        gemini = GeminiReceiptExtractor(
            api_key=api_key,
            model=settings.gemini_model,
        )

    return ReceiptExtractionPipeline(
        ocr_extractors=ocr_extractors,
        structurer=structurer,
        gemini=gemini,
        gemini_mode=settings.gemini_fallback_mode,
    )
