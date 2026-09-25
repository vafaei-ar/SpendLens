import hashlib
import json
import mimetypes
import re
import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from spendlens.models import ReceiptExtraction


class SourceVariant(StrEnum):
    CAMERA_ORIGINAL = "camera_original"
    TELEGRAM_PHOTO = "telegram_photo"
    TELEGRAM_FILE = "telegram_file"
    EMAIL_FILE = "email_file"
    OTHER = "other"


class CorpusMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    receipt_key: str = Field(min_length=1)
    source_variant: SourceVariant
    source_file: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mime_type: str = Field(min_length=1)
    notes: str | None = None


@dataclass(frozen=True)
class CorpusCase:
    metadata: CorpusMetadata
    truth: ReceiptExtraction
    source_path: Path


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _guess_mime_type(path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type is not None:
        return mime_type

    fallback = {
        ".heic": "image/heic",
        ".heif": "image/heif",
    }
    return fallback.get(path.suffix.casefold(), "application/octet-stream")


def _validate_case_id(case_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", case_id):
        raise ValueError(
            "case_id may contain only letters, numbers, dot, underscore, and hyphen"
        )


def _validate_truth(truth: ReceiptExtraction) -> None:
    missing = []
    if truth.merchant is None:
        missing.append("merchant")
    if truth.transaction_date is None:
        missing.append("transaction_date")
    if truth.total is None:
        missing.append("total")
    if truth.currency is None:
        missing.append("currency")
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"Gold-standard truth is missing critical fields: {joined}")


def create_case(
    *,
    corpus_root: Path,
    case_id: str,
    receipt_key: str,
    source_path: Path,
    source_variant: SourceVariant,
    truth: ReceiptExtraction,
    notes: str | None = None,
) -> CorpusCase:
    _validate_case_id(case_id)
    _validate_truth(truth)

    source_bytes = source_path.read_bytes()
    if not source_bytes:
        raise ValueError("Evaluation source file is empty")

    case_dir = corpus_root / case_id
    if case_dir.exists():
        raise FileExistsError(f"Evaluation case already exists: {case_dir}")

    suffix = source_path.suffix.casefold() or ".bin"
    stored_name = f"source{suffix}"
    stored_path = case_dir / stored_name

    case_dir.mkdir(parents=True)
    shutil.copyfile(source_path, stored_path)

    metadata = CorpusMetadata(
        case_id=case_id,
        receipt_key=receipt_key,
        source_variant=source_variant,
        source_file=stored_name,
        source_sha256=_sha256(source_bytes),
        mime_type=_guess_mime_type(source_path),
        notes=notes,
    )

    (case_dir / "metadata.json").write_text(
        json.dumps(metadata.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (case_dir / "truth.json").write_text(
        truth.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    return CorpusCase(
        metadata=metadata,
        truth=truth,
        source_path=stored_path,
    )


def load_case(case_dir: Path) -> CorpusCase:
    metadata_path = case_dir / "metadata.json"
    truth_path = case_dir / "truth.json"

    metadata = CorpusMetadata.model_validate_json(
        metadata_path.read_text(encoding="utf-8")
    )
    truth = ReceiptExtraction.model_validate_json(
        truth_path.read_text(encoding="utf-8")
    )
    _validate_truth(truth)

    source_path = case_dir / metadata.source_file
    source_bytes = source_path.read_bytes()
    actual_hash = _sha256(source_bytes)
    if actual_hash != metadata.source_sha256:
        raise ValueError(
            f"Source hash mismatch for {metadata.case_id}: "
            f"expected {metadata.source_sha256}, got {actual_hash}"
        )

    return CorpusCase(
        metadata=metadata,
        truth=truth,
        source_path=source_path,
    )


def load_corpus(corpus_root: Path) -> list[CorpusCase]:
    if not corpus_root.exists():
        raise FileNotFoundError(f"Evaluation corpus does not exist: {corpus_root}")

    cases: list[CorpusCase] = []
    seen_ids: set[str] = set()

    for case_dir in sorted(path for path in corpus_root.iterdir() if path.is_dir()):
        if not (case_dir / "metadata.json").exists():
            continue
        case = load_case(case_dir)
        if case.metadata.case_id in seen_ids:
            raise ValueError(f"Duplicate case_id: {case.metadata.case_id}")
        seen_ids.add(case.metadata.case_id)
        cases.append(case)

    if not cases:
        raise ValueError(f"No labeled evaluation cases found in {corpus_root}")
    return cases
