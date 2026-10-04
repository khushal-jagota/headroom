from planner.conversation.backends.codex_app_server import bindings_gen as bindings
from planner.conversation.backends.codex_app_server.adapter import _error_summary
from planner.conversation.backends.failure_summary import (
    UNKNOWN_TURN_FAILURE,
    normalized_failure_summary,
)


def test_empty_failure_evidence_uses_an_honest_fallback() -> None:
    assert normalized_failure_summary(" \n ") == UNKNOWN_TURN_FAILURE


def test_multiline_provider_detail_is_one_bounded_redacted_line() -> None:
    detail = "provider failed\nAuthorization: Bearer secret-value\n" + ("x" * 400)
    summary = normalized_failure_summary(detail)
    assert "secret-value" not in summary
    assert "\n" not in summary
    assert len(summary) <= 240


def test_codex_structured_unauthorized_error_names_rejection_not_expiry() -> None:
    summary = _error_summary(
        bindings.TurnError(
            message="Your token may be expired",
            codexErrorInfo="unauthorized",
        )
    )
    assert summary == "authentication was rejected (HTTP 401)"
    assert "expired" not in summary


def test_codex_http_variant_preserves_the_status_without_exposing_stderr() -> None:
    summary = _error_summary(
        bindings.TurnError(
            message="provider unavailable",
            codexErrorInfo=bindings.HttpConnectionFailedCodexErrorInfo(
                httpConnectionFailed=bindings.HttpConnectionFailed(httpStatusCode=503)
            ),
        )
    )
    assert summary == "provider unavailable (HTTP 503)"
