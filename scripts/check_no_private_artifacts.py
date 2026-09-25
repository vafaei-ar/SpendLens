from pathlib import Path
import sys


DATABASE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
RECEIPT_SUFFIXES = {
    ".heic",
    ".heif",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".webp",
}
SAFE_RECEIPT_PREFIX = "tests/fixtures/synthetic/"


def violation(path_text: str) -> str | None:
    path = Path(path_text)
    normalized = path.as_posix()
    name = path.name.lower()
    suffix = path.suffix.lower()

    if normalized == ".env.example":
        return None
    if name == ".env" or name.startswith(".env."):
        return "environment or secret file"
    if suffix in DATABASE_SUFFIXES:
        return "database file"
    if normalized.startswith("data/receipts/"):
        return "local receipt source"
    if normalized.startswith("evaluation/private/"):
        return "private evaluation corpus"
    if (
        suffix in RECEIPT_SUFFIXES
        and not normalized.startswith(SAFE_RECEIPT_PREFIX)
    ):
        return (
            "image/PDF blocked by default; synthetic fixtures belong under "
            "tests/fixtures/synthetic/"
        )
    return None


def main(paths: list[str]) -> int:
    failures: list[tuple[str, str]] = []
    for path in paths:
        reason = violation(path)
        if reason:
            failures.append((path, reason))

    if not failures:
        return 0

    print("SpendLens private-artifact check failed:", file=sys.stderr)
    for path, reason in failures:
        print(f"  {path}: {reason}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
