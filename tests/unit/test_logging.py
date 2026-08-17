from video_evidence_mcp.logging_utils import redact_text


def test_redacts_headers_and_query_secrets() -> None:
    result = redact_text(
        "Authorization: Bearer abc123 https://example.com/x?token=secret&safe=yes Cookie=session"
    )
    assert "abc123" not in result
    assert "secret" not in result
    assert "session" not in result
    assert "safe=yes" in result
