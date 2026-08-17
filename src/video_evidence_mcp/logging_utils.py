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


def redact_text(value: str) -> str:
    redacted = SECRET_RE.sub(lambda match: f"{match.group(1)}[REDACTED]", value)
    words = redacted.split()
    output: list[str] = []
    for word in words:
        if word.startswith(("http://", "https://")):
            try:
                parts = urlsplit(word)
                safe_query = urlencode(
                    [
                        (key, "[REDACTED]" if key.lower() in SENSITIVE_QUERY_KEYS else val)
                        for key, val in parse_qsl(parts.query, keep_blank_values=True)
                    ]
                )
                word = urlunsplit((parts.scheme, parts.netloc, parts.path, safe_query, ""))
            except ValueError:
                pass
        output.append(word)
    return " ".join(output)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(str(record.msg))
        if record.args:
            record.args = tuple(redact_text(str(arg)) for arg in record.args)
        return True


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
