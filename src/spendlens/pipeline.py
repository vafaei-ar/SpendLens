from dataclasses import dataclass
from typing import Literal

from spendlens.evidence import combine_ocr_evidence, recover_receipt_fields
from spendlens.extraction import (
    ExtractionParseError,
    ExtractionProviderError,
    ExtractionResult,
    GeminiReceiptExtractor,
    MLXOCRExtractor,
    OCRResult,
    OllamaTextStructurer,
    evidence_envelope,
)
from spendlens.models import ReceiptExtraction
from spendlens.validation import ValidationReport, validate_receipt

AttemptStatus = Literal["parsed", "invalid_schema", "provider_error"]


@dataclass(frozen=True)
class PipelineAttempt:
    provider: str
    model_id: str
    prompt_version: str
    status: AttemptStatus
    raw_response: str | None
    extraction: ReceiptExtraction | None
    validation: ValidationReport | None
    error_text: str | None
    cloud: bool = False


@dataclass(frozen=True)
class PipelineResult:
    attempts: list[PipelineAttempt]
    chosen_index: int | None

    @property
    def chosen_attempt(self) -> PipelineAttempt | None:
        if self.chosen_index is None:
            return None
        return self.attempts[self.chosen_index]

    @property
    def used_cloud(self) -> bool:
        attempt = self.chosen_attempt
        return bool(attempt is not None and attempt.cloud)

    def as_extraction_result(self) -> ExtractionResult:
        attempt = self.chosen_attempt
        if attempt is None or attempt.extraction is None:
            raise ExtractionProviderError(
                "No extractor produced a usable structured receipt"
            )
        return ExtractionResult(
            extraction=attempt.extraction,
            raw_response=attempt.raw_response or "",
            provider=attempt.provider,
            model_id=attempt.model_id,
            prompt_version=attempt.prompt_version,
        )


class ReceiptExtractionPipeline:
    """Gemini two-pass receipt reading with optional local fallback."""

    def __init__(
        self,
        *,
        gemini: GeminiReceiptExtractor,
        default_currency: str | None = None,
        local_fallback_enabled: bool = False,
        ocr_extractors: list[MLXOCRExtractor] | None = None,
        structurer: OllamaTextStructurer | None = None,
    ) -> None:
        self.gemini = gemini
        self.default_currency = (
            default_currency.strip().upper()
            if default_currency and default_currency.strip()
            else None
        )
        self.local_fallback_enabled = local_fallback_enabled
        self.ocr_extractors = ocr_extractors or []
        self.structurer = structurer

    @property
    def description(self) -> str:
        base = (
            f"{self.gemini.model} "
            "(Gemini ultra-high transcript -> structured extraction)"
        )
        if not self.local_fallback_enabled:
            return f"{base} -> direct Gemini retry -> local fallback off"

        local_models = " -> ".join(
            extractor.model for extractor in self.ocr_extractors
        )
        if local_models and self.structurer is not None:
            return (
                f"{base} -> direct Gemini retry -> {local_models} -> "
                f"{self.structurer.model} (local fallback)"
            )
        return f"{base} -> local fallback enabled but not configured"

    def _recover_currency(
        self,
        extraction: ReceiptExtraction,
    ) -> tuple[ReceiptExtraction, list[str]]:
        return recover_receipt_fields(
            extraction,
            ocr_text="",
            default_currency=self.default_currency,
        )

    def _gemini_two_pass_attempt(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
    ) -> PipelineAttempt:
        transcript: OCRResult | None = None
        try:
            transcript = self.gemini.transcribe_image(
                image_bytes,
                mime_type=mime_type,
            )
            result = self.gemini.extract_from_text(transcript.text)
        except ExtractionParseError as exc:
            return PipelineAttempt(
                provider="gemini-vision-ocr+gemini-structure",
                model_id=f"{self.gemini.model} -> {self.gemini.model}",
                prompt_version="receipt-gemini-two-pass-v1",
                status="invalid_schema",
                raw_response=evidence_envelope(
                    ocr=transcript,
                    structured_response=exc.raw_response,
                ),
                extraction=None,
                validation=None,
                error_text=str(exc),
                cloud=True,
            )
        except ExtractionProviderError as exc:
            return PipelineAttempt(
                provider="gemini-vision-ocr+gemini-structure",
                model_id=f"{self.gemini.model} -> {self.gemini.model}",
                prompt_version="receipt-gemini-two-pass-v1",
                status="provider_error",
                raw_response=evidence_envelope(
                    ocr=transcript,
                    structured_response=None,
                ),
                extraction=None,
                validation=None,
                error_text=str(exc),
                cloud=True,
            )

        extraction, recovered_fields = recover_receipt_fields(
            result.extraction,
            ocr_text=transcript.text,
            default_currency=self.default_currency,
        )
        report = validate_receipt(extraction)
        return PipelineAttempt(
            provider="gemini-vision-ocr+gemini-structure",
            model_id=f"{transcript.model_id} -> {result.model_id}",
            prompt_version="receipt-gemini-two-pass-v1",
            status="parsed",
            raw_response=evidence_envelope(
                ocr=transcript,
                structured_response=result.raw_response,
                recovered_fields=recovered_fields,
            ),
            extraction=extraction,
            validation=report,
            error_text=None,
            cloud=True,
        )

    def _gemini_direct_attempt(
        self,
        *,
        image_bytes: bytes,
        mime_type: str,
    ) -> PipelineAttempt:
        try:
            result = self.gemini.extract_from_image(
                image_bytes,
                mime_type=mime_type,
            )
        except ExtractionParseError as exc:
            return PipelineAttempt(
                provider="gemini-direct",
                model_id=self.gemini.model,
                prompt_version="receipt-gemini-direct-v1",
                status="invalid_schema",
                raw_response=exc.raw_response,
                extraction=None,
                validation=None,
                error_text=str(exc),
                cloud=True,
            )
        except ExtractionProviderError as exc:
            return PipelineAttempt(
                provider="gemini-direct",
                model_id=self.gemini.model,
                prompt_version="receipt-gemini-direct-v1",
                status="provider_error",
                raw_response=None,
                extraction=None,
                validation=None,
                error_text=str(exc),
                cloud=True,
            )

        extraction, recovered_fields = self._recover_currency(
            result.extraction
        )
        report = validate_receipt(extraction)
        return PipelineAttempt(
            provider="gemini-direct",
            model_id=result.model_id,
            prompt_version="receipt-gemini-direct-v1",
            status="parsed",
            raw_response=evidence_envelope(
                ocr=None,
                structured_response=result.raw_response,
                recovered_fields=recovered_fields,
            ),
            extraction=extraction,
            validation=report,
            error_text=None,
            cloud=True,
        )

    def _structured_local_attempt(
        self,
        *,
        ocr: OCRResult,
    ) -> PipelineAttempt:
        if self.structurer is None:
            return PipelineAttempt(
                provider=ocr.provider,
                model_id=ocr.model_id,
                prompt_version="ocr-v1",
                status="provider_error",
                raw_response=evidence_envelope(
                    ocr=ocr,
                    structured_response=None,
                ),
                extraction=None,
                validation=None,
                error_text="Local structurer is not configured.",
            )

        try:
            result = self.structurer.extract_from_text(ocr.text)
        except ExtractionParseError as exc:
            return PipelineAttempt(
                provider=f"{ocr.provider}+ollama-text",
                model_id=f"{ocr.model_id} -> {self.structurer.model}",
                prompt_version="receipt-text-v1",
                status="invalid_schema",
                raw_response=evidence_envelope(
                    ocr=ocr,
                    structured_response=exc.raw_response,
                ),
                extraction=None,
                validation=None,
                error_text=str(exc),
            )
        except ExtractionProviderError as exc:
            return PipelineAttempt(
                provider=f"{ocr.provider}+ollama-text",
                model_id=f"{ocr.model_id} -> {self.structurer.model}",
                prompt_version="receipt-text-v1",
                status="provider_error",
                raw_response=evidence_envelope(
                    ocr=ocr,
                    structured_response=None,
                ),
                extraction=None,
                validation=None,
                error_text=str(exc),
            )

        extraction, recovered_fields = recover_receipt_fields(
            result.extraction,
            ocr_text=ocr.text,
            default_currency=self.default_currency,
        )
        report = validate_receipt(extraction)
        return PipelineAttempt(
            provider=f"{ocr.provider}+ollama-text",
            model_id=f"{ocr.model_id} -> {result.model_id}",
            prompt_version=result.prompt_version,
            status="parsed",
            raw_response=evidence_envelope(
                ocr=ocr,
                structured_response=result.raw_response,
                recovered_fields=recovered_fields,
            ),
            extraction=extraction,
            validation=report,
            error_text=None,
        )

    @staticmethod
    def _blocker_count(attempt: PipelineAttempt) -> int:
        if attempt.validation is None:
            return 999
        return len(attempt.validation.blockers)

    @staticmethod
    def _line_item_count(attempt: PipelineAttempt) -> int:
        if attempt.extraction is None:
            return 0
        return len(attempt.extraction.line_items)

    @classmethod
    def _ready_to_stop(cls, attempt: PipelineAttempt) -> bool:
        return bool(
            attempt.validation is not None
            and attempt.validation.auto_accept
            and cls._line_item_count(attempt) > 0
        )

    def _run_local_fallback(
        self,
        image_bytes: bytes,
        *,
        mime_type: str,
        attempts: list[PipelineAttempt],
        parsed_indexes: list[int],
    ) -> PipelineResult | None:
        evidence: list[OCRResult] = []

        for extractor in self.ocr_extractors:
            try:
                ocr = extractor.extract_text(
                    image_bytes,
                    mime_type=mime_type,
                )
            except ExtractionProviderError as exc:
                attempts.append(
                    PipelineAttempt(
                        provider="mlx-ocr",
                        model_id=extractor.model,
                        prompt_version="ocr-v1",
                        status="provider_error",
                        raw_response=None,
                        extraction=None,
                        validation=None,
                        error_text=str(exc),
                    )
                )
                continue

            evidence.append(ocr)
            attempt = self._structured_local_attempt(ocr=ocr)
            attempts.append(attempt)
            index = len(attempts) - 1
            if attempt.status == "parsed":
                parsed_indexes.append(index)
                if self._ready_to_stop(attempt):
                    return PipelineResult(
                        attempts=attempts,
                        chosen_index=index,
                    )

        if len(evidence) > 1:
            combined = OCRResult(
                text=combine_ocr_evidence(evidence),
                provider="combined-ocr",
                model_id=" + ".join(
                    item.model_id for item in evidence
                ),
            )
            attempt = self._structured_local_attempt(ocr=combined)
            attempts.append(attempt)
            index = len(attempts) - 1
            if attempt.status == "parsed":
                parsed_indexes.append(index)
                if self._ready_to_stop(attempt):
                    return PipelineResult(
                        attempts=attempts,
                        chosen_index=index,
                    )

        return None

    def run(
        self,
        image_bytes: bytes,
        *,
        mime_type: str,
    ) -> PipelineResult:
        attempts: list[PipelineAttempt] = []
        parsed_indexes: list[int] = []

        two_pass = self._gemini_two_pass_attempt(
            image_bytes=image_bytes,
            mime_type=mime_type,
        )
        attempts.append(two_pass)
        if two_pass.status == "parsed":
            parsed_indexes.append(0)
            if self._ready_to_stop(two_pass):
                return PipelineResult(
                    attempts=attempts,
                    chosen_index=0,
                )

        direct = self._gemini_direct_attempt(
            image_bytes=image_bytes,
            mime_type=mime_type,
        )
        attempts.append(direct)
        if direct.status == "parsed":
            parsed_indexes.append(1)
            if self._ready_to_stop(direct):
                return PipelineResult(
                    attempts=attempts,
                    chosen_index=1,
                )

        if self.local_fallback_enabled:
            local_result = self._run_local_fallback(
                image_bytes,
                mime_type=mime_type,
                attempts=attempts,
                parsed_indexes=parsed_indexes,
            )
            if local_result is not None:
                return local_result

        if parsed_indexes:
            chosen_index = min(
                parsed_indexes,
                key=lambda index: (
                    self._blocker_count(attempts[index]),
                    -self._line_item_count(attempts[index]),
                    index,
                ),
            )
            return PipelineResult(
                attempts=attempts,
                chosen_index=chosen_index,
            )

        return PipelineResult(attempts=attempts, chosen_index=None)

    def extract(
        self,
        image_bytes: bytes,
        *,
        mime_type: str,
    ) -> ExtractionResult:
        return self.run(
            image_bytes,
            mime_type=mime_type,
        ).as_extraction_result()
