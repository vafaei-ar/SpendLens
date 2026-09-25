import re
import unicodedata


def normalize_merchant(value: str | None) -> str:
    if value is None:
        return ""
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())
