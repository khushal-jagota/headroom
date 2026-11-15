from planner.conversation.backends.failure_summary import (
    normalized_failure_summary,
)


def test_multiline_provider_detail_is_one_bounded_redacted_line() -> None:
    detail = "provider failed\nAuthorization: Bearer secret-value\n" + ("x" * 400)
    summary = normalized_failure_summary(detail)
    assert "secret-value" not in summary
    assert "\n" not in summary
    assert len(summary) <= 240




def test_quoted_json_credentials_with_spaces_do_not_leak_value_tails() -> None:
    detail = (
        'request failed: {"api_key": "secret value with tail", '
        '"authorization": "Bearer another secret tail", "cause": "denied"}'
    )
    summary = normalized_failure_summary(detail)
    assert "secret value" not in summary
    assert "another secret" not in summary
    assert "with tail" not in summary
    assert '"cause": "denied"' in summary


def test_compound_credential_names_are_redacted() -> None:
    detail = (
        'access_token="one value", refresh_token="two value", '
        'client_secret="three value", OPENAI_API_KEY="four value", '
        'db_password="five value", client_password="six value", '
        'proxy_authorization: "Bearer seven value"'
    )
    summary = normalized_failure_summary(detail)
    for secret in (
        "one value",
        "two value",
        "three value",
        "four value",
        "five value",
        "six value",
        "seven value",
    ):
        assert secret not in summary
    assert summary.count("[redacted]") == 7
