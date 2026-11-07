import re
import unicodedata


def location_code_prefix(name: str, used_prefixes: set[str]) -> str:
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    words = re.findall(r"[A-Za-z]+", normalized)
    if len(words) >= 2 and words[0].upper() == "DEMO" and words[1].upper() == "ONLY":
        words = words[2:]
    if not words:
        words = ["LK"]

    first = words[0].upper()
    candidates = [first[:length] for length in range(2, len(first) + 1)]
    base = first[:2] or "L"
    candidates.extend(base + word[0].upper() for word in words[1:])
    candidates.extend(f"{base}{suffix}" for suffix in range(2, 1000))

    for candidate in candidates:
        if candidate and candidate not in used_prefixes:
            return candidate
    raise ValueError(f"Could not derive a unique locker-code prefix from location name {name!r}.")


def locker_code(prefix: str, sequence: int) -> str:
    if not prefix.isalnum() or not 1 <= sequence <= 999_999:
        raise ValueError("Invalid locker-code prefix or sequence.")
    return f"{prefix}{sequence:03d}"
