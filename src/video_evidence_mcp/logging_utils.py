from __future__ import annotations

import logging
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SECRET_RE = re.compile(
    r"(?i)(authorization\s*[:=]\s*bearer\s+|cookie\s*[:=]\s*|"
    r"(?:api[_-]?key|token|secret|password)\s*[:=]\s*)([^\s,;&]+)"
)
SENSITIVE_QUERY_KEYS = frozenset(
    {"access_token", "api_key", "apikey", "auth", "code", "key", "password", "signature", "token"}
)
URL_RE = re.compile(r"https?://[^\s]+")


def redact_text(value: str) -> str:
    redacted = SECRET_RE.sub(lambda match: f"{match.group(1)}[REDACTED]", value)

    def redact_url(match: re.Match[str]) -> str:
        url = match.group(0)
        try:
            parts = urlsplit(url)
            safe_query = urlencode(
                [
                    (key, "[REDACTED]" if key.lower() in SENSITIVE_QUERY_KEYS else val)
                    for key, val in parse_qsl(parts.query, keep_blank_values=True)
                ]
            )
            return urlunsplit((parts.scheme, parts.netloc, parts.path, safe_query, ""))
        except ValueError:
            return url

    return URL_RE.sub(redact_url, redacted)


class RedactingFilter(logging.Filter):
    """Compatibility filter; final rendered output is redacted by the formatter."""

    def filter(self, record: logging.LogRecord) -> bool:
        return True


class RedactingFormatter(logging.Formatter):
    """Redact the complete rendered record, including exception tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        record.exc_text = None
        return redact_text(rendered)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
