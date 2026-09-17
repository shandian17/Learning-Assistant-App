SUPPORTED_LANGUAGES = {"zh-CN", "en"}


def normalize_language(value, default: str = "zh-CN") -> str:
    if value is None or value == "":
        return default
    if not isinstance(value, str):
        raise ValueError("language must be a string")
    normalized = value.strip().lower()
    if normalized in {"zh", "zh-cn", "zh-hans"}:
        return "zh-CN"
    if normalized in {"en", "en-us", "en-ca", "en-gb"}:
        return "en"
    raise ValueError("language must be zh-CN or en")


def language_name(value: str) -> str:
    return "English" if value == "en" else "Simplified Chinese"
