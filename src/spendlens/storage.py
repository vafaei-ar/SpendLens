import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

_MIME_EXTENSIONS = {
    "application/pdf": ".pdf",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


@dataclass(frozen=True)
class StoredSource:
    sha256: str
    relative_path: str
    byte_size: int
    mime_type: str
    created: bool


def store_source_bytes(data: bytes, *, data_dir: Path, mime_type: str) -> StoredSource:
    if not data:
        raise ValueError("Source data must not be empty")

    digest = hashlib.sha256(data).hexdigest()
    extension = _MIME_EXTENSIONS.get(mime_type.lower(), ".bin")
    relative = Path("receipts") / digest[:2] / f"{digest}{extension}"
    target = data_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        existing_digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if existing_digest != digest:
            raise RuntimeError(f"Hash collision or corrupted source file at {target}")
        return StoredSource(digest, relative.as_posix(), len(data), mime_type, False)

    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, target)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()

    return StoredSource(digest, relative.as_posix(), len(data), mime_type, True)
