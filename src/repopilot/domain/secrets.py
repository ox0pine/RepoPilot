"""Secret redaction shared by the execution event stream and run persistence."""

from collections.abc import Iterable


def secret_values(values: Iterable[str]) -> tuple[str, ...]:
    """Longest first, so a secret containing another is replaced before its prefix."""
    return tuple(sorted({value for value in values if value}, key=len, reverse=True))


def redact_secrets(value, secrets: Iterable[str]):
    """Recursively replace secrets in strings, dict keys and list items."""
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, '[REDACTED]')
        return value
    if isinstance(value, dict):
        return {redact_secrets(key, secrets): redact_secrets(item, secrets)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_secrets(item, secrets) for item in value]
    return value
