from hashlib import sha256

from spendlens.storage import store_source_bytes


def test_source_storage_is_content_addressed_and_idempotent(tmp_path) -> None:
    payload = b"synthetic-receipt-bytes"
    expected_hash = sha256(payload).hexdigest()

    first = store_source_bytes(payload, data_dir=tmp_path, mime_type="image/jpeg")
    second = store_source_bytes(payload, data_dir=tmp_path, mime_type="image/jpeg")

    assert first.sha256 == expected_hash
    assert first.created is True
    assert second.created is False
    assert first.relative_path == second.relative_path
    assert (tmp_path / first.relative_path).read_bytes() == payload
