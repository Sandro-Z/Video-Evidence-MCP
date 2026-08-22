import io
import logging

from video_evidence_mcp.logging_utils import RedactingFilter, RedactingFormatter, redact_text


def test_redacts_headers_and_query_secrets() -> None:
    result = redact_text(
        "Authorization: Bearer abc123 https://example.com/x?token=secret&safe=yes Cookie=session"
    )
    assert "abc123" not in result
    assert "secret" not in result
    assert "session" not in result
    assert "safe=yes" in result


def test_redacting_formatter_covers_tracebacks_without_collapsing_lines() -> None:
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.addFilter(RedactingFilter())
    handler.setFormatter(RedactingFormatter("%(levelname)s %(message)s"))
    logger = logging.getLogger("test-redacting-formatter")
    logger.handlers[:] = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    try:
        raise ValueError("https://example.com/x?token=trace-secret&safe=yes")
    except ValueError:
        logger.exception("request %d failed Authorization: Bearer %s", 2, "message-secret")

    rendered = output.getvalue()
    assert "trace-secret" not in rendered
    assert "message-secret" not in rendered
    assert "safe=yes" in rendered
    assert "request 2 failed" in rendered
    assert "Traceback" in rendered
    assert "\n" in rendered
