from pathlib import Path

import pytest

from spendlens.eval_corpus import (
    SourceVariant,
    create_case,
    load_case,
    load_corpus,
)
from spendlens.models import ReceiptExtraction


def truth() -> ReceiptExtraction:
    return ReceiptExtraction.model_validate(
        {
            "merchant": "Example Market",
            "transaction_date": "2026-09-24",
            "subtotal": "10.00",
            "tax": "0.60",
            "total": "10.60",
            "currency": "USD",
        }
    )


def test_create_and_load_private_case(tmp_path: Path) -> None:
    source = tmp_path / "input.jpg"
    source.write_bytes(b"synthetic-evaluation-image")
    corpus_root = tmp_path / "private"

    created = create_case(
        corpus_root=corpus_root,
        case_id="r001-photo",
        receipt_key="r001",
        source_path=source,
        source_variant=SourceVariant.TELEGRAM_PHOTO,
        truth=truth(),
    )
    loaded = load_case(corpus_root / "r001-photo")

    assert loaded.metadata.case_id == "r001-photo"
    assert loaded.metadata.receipt_key == "r001"
    assert loaded.metadata.source_variant == SourceVariant.TELEGRAM_PHOTO
    assert loaded.source_path.name == "source.jpg"
    assert loaded.source_path.read_bytes() == source.read_bytes()
    assert loaded.metadata.source_sha256 == created.metadata.source_sha256
    assert len(load_corpus(corpus_root)) == 1


def test_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "input.jpg"
    source.write_bytes(b"original")
    corpus_root = tmp_path / "private"

    case = create_case(
        corpus_root=corpus_root,
        case_id="r001",
        receipt_key="r001",
        source_path=source,
        source_variant=SourceVariant.OTHER,
        truth=truth(),
    )
    case.source_path.write_bytes(b"tampered")

    with pytest.raises(ValueError, match="Source hash mismatch"):
        load_case(corpus_root / "r001")


def test_truth_requires_critical_fields(tmp_path: Path) -> None:
    source = tmp_path / "input.jpg"
    source.write_bytes(b"synthetic")
    incomplete = ReceiptExtraction.model_validate(
        {
            "merchant": "Example Market",
            "transaction_date": "2026-09-24",
            "currency": "USD",
        }
    )

    with pytest.raises(ValueError, match="total"):
        create_case(
            corpus_root=tmp_path / "private",
            case_id="r001",
            receipt_key="r001",
            source_path=source,
            source_variant=SourceVariant.OTHER,
            truth=incomplete,
        )
