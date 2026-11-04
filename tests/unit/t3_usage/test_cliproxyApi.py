"""Every cliproxyApi.test.ts case, with fake HTTP and unchanged assertions."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import pytest

from planner.conversation.usage.cliproxyApi import (
    CliproxyApi,
    creditRedeemRequestId,
    makeCliproxyApi,
)
from planner.conversation.usage.contracts.providerUsageLimits import UsageLimitSourceError

config = {
    "kind": "cliproxy",
    "url": "http://hub.test:8317",
    "managementKey": "management-secret",
    "enabled": True,
}
accounts = [
    {
        "id": "first.json",
        "auth_index": "a",
        "provider": "codex",
        "email": "first@example.com",
        "id_token": {"chatgpt_account_id": "account-a"},
    },
    {
        "id": "second.json",
        "auth_index": "b",
        "provider": "codex",
        "email": "second@example.com",
        "id_token": {"chatgpt_account_id": "account-b"},
    },
]


def credit(id: str, expires_at: str = "2099-01-01T00:00:00Z") -> dict[str, Any]:
    return {
        "id": id,
        "expires_at": expires_at,
        "status": "available",
        "reset_type": "codex_rate_limits",
    }


def encodeJson(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


@dataclass
class Fixture:
    requests: list[dict[str, Any]]
    api: CliproxyApi


def fixture(options: dict[str, Any] | None = None) -> Fixture:
    options = options or {}
    requests: list[dict[str, Any]] = []

    async def http(request: dict[str, Any]) -> dict[str, Any]:
        assert request["headers"]["Authorization"] == "Bearer management-secret"
        path = urlparse(request["url"]).path
        body = request.get("body")
        requests.append({"path": path, **({"body": body} if body is not None else {})})
        if path.endswith("/auth-files"):
            return {"status": 200, "body": {"files": options.get("accounts", accounts)}}
        if path.endswith("/reset-quota"):
            return {"status": options.get("cooldownStatus", 200), "body": {}}
        assert path == "/v0/management/api-call"
        assert isinstance(body, dict)
        assert body["header"]["Authorization"] == "Bearer $TOKEN$"
        upstream: Callable[[dict[str, Any]], dict[str, Any]] | None = options.get("upstream")
        if upstream:
            response = upstream(body)
        elif body["url"].endswith("/consume"):
            response = {"status": 200, "body": {"code": "reset"}}
        elif body["url"].endswith("/rate-limit-reset-credits"):
            response = {
                "status": 200,
                "body": {
                    "credits": [
                        credit("later", "2099-02-01T00:00:00Z"),
                        credit("first"),
                        credit("expired", "2000-01-01T00:00:00Z"),
                        {**credit("used"), "status": "redeemed"},
                    ]
                },
            }
        else:
            response = {
                "status": 200,
                "body": {
                    "plan_type": "pro",
                    "rate_limit": {
                        "secondary_window": {
                            "used_percent": 78,
                            "reset_at": 4070908800,
                            "limit_window_seconds": 604800,
                        }
                    },
                },
            }
        return {
            "status": 200,
            "body": {"status_code": response["status"], "body": encodeJson(response["body"])},
        }

    def now() -> datetime:
        return datetime.fromtimestamp(1788710400000 / 1000, UTC)

    return Fixture(requests, makeCliproxyApi(http, now))


def test_reads_both_accounts_and_their_earliest_unexpired_credits_without_plugin_endpoints() -> (
    None
):
    async def run() -> None:
        test = fixture()
        result = await test.api.readAccounts(config)
        assert [account["usageLimits"].get("resetCredits") for account in result] == [
            {
                "availableCount": 2,
                "nextCreditId": "first",
                "nextExpiresAt": "2099-01-01T00:00:00.000Z",
            },
            {
                "availableCount": 2,
                "nextCreditId": "first",
                "nextExpiresAt": "2099-01-01T00:00:00.000Z",
            },
        ]
        assert [
            {key: window[key] for key in ("id", "usedPercent", "kind")}
            for window in result[0]["usageLimits"]["windows"]
        ] == [{"id": "secondary", "usedPercent": 78, "kind": "weekly"}]
        calls = [request for request in test.requests if request.get("body", {}).get("url")]
        assert sorted(request["body"]["auth_index"] for request in calls) == ["a", "a", "b", "b"]
        assert (
            next(request for request in calls if request["body"]["auth_index"] == "b")["body"][
                "header"
            ]["Chatgpt-Account-Id"]
            == "account-b"
        )

    asyncio.run(run())


def test_keeps_usage_when_the_credits_endpoint_fails() -> None:
    async def run() -> None:
        test = fixture(
            {
                "upstream": lambda request: (
                    {"status": 503, "body": {"token": "do-not-publish"}}
                    if request["url"].endswith("rate-limit-reset-credits")
                    else {
                        "status": 200,
                        "body": {"rate_limit": {"primary_window": {"used_percent": 12}}},
                    }
                )
            }
        )
        result = await test.api.readAccounts(config)
        assert result[0]["usageLimits"]["windows"][0]["usedPercent"] == 12
        assert "resetCredits" not in result[0]["usageLimits"]

    asyncio.run(run())


def test_isolates_a_failed_account_and_never_publishes_upstream_error_bodies() -> None:
    async def run() -> None:
        def upstream(request: dict[str, Any]) -> dict[str, Any]:
            if request["auth_index"] == "a":
                return {"status": 401, "body": {"token": "do-not-publish"}}
            body: dict[str, Any] = (
                {"credits": []}
                if request["url"].endswith("rate-limit-reset-credits")
                else {"rate_limit": {"primary_window": {"used_percent": 12}}}
            )
            return {"status": 200, "body": body}

        test = fixture({"upstream": upstream})
        result = await test.api.readAccounts(config)
        assert result[0]["usageLimits"]["unavailable"]["reason"] == "probeFailed"
        assert result[1]["usageLimits"]["windows"][0]["usedPercent"] == 12
        assert "do-not-publish" not in encodeJson(result)

    asyncio.run(run())


def test_maps_claude_scoped_windows_without_a_scheduler_plugin() -> None:
    async def run() -> None:
        test = fixture(
            {
                "accounts": [{**accounts[0], "provider": "claude"}],
                "upstream": lambda _: {
                    "status": 200,
                    "body": {
                        "five_hour": {"utilization": 10, "resets_at": None},
                        "seven_day": {"utilization": 50, "resets_at": "2099-01-01T00:00:00Z"},
                        "limits": [
                            {
                                "kind": "weekly_scoped",
                                "percent": 80,
                                "resets_at": None,
                                "scope": {"model": {"display_name": "Fable"}},
                            }
                        ],
                    },
                },
            }
        )
        result = await test.api.readAccounts(config)
        assert [
            [window["id"], window["usedPercent"]] for window in result[0]["usageLimits"]["windows"]
        ] == [["five_hour", 10], ["seven_day", 50], ["seven_day_fable", 80]]

    asyncio.run(run())


def test_pins_redemption_to_the_displayed_credit_and_clears_only_that_accounts_cooldown() -> None:
    async def run() -> None:
        test = fixture()
        assert await test.api.consume(config, "second.json", "credit-b") == {"outcome": "reset"}
        assert await test.api.consume(config, "second.json", "credit-b") == {"outcome": "reset"}
        redemptions = [
            request
            for request in test.requests
            if request.get("body", {}).get("url", "").endswith("/consume")
        ]
        assert len(redemptions) == 2
        assert redemptions[0]["body"]["data"] == redemptions[1]["body"]["data"]
        assert redemptions[0]["body"]["data"] == encodeJson(
            {
                "redeem_request_id": creditRedeemRequestId("account-b", "credit-b"),
                "credit_id": "credit-b",
            }
        )
        assert [
            request["body"]["auth_index"]
            for request in test.requests
            if request["path"].endswith("/reset-quota")
        ] == ["b", "b"]

    asyncio.run(run())


@pytest.mark.parametrize(
    ("code", "outcome"),
    [
        ("nothing_to_reset", "nothingToReset"),
        ("no_credit", "noCredit"),
        ("already_redeemed", "alreadyRedeemed"),
    ],
)
def test_reports_code_accurately(code: str, outcome: str) -> None:
    async def run() -> None:
        test = fixture({"upstream": lambda _: {"status": 200, "body": {"code": code}}})
        assert await test.api.consume(config, "first.json", "credit") == {"outcome": outcome}
        assert any(request["path"].endswith("/reset-quota") for request in test.requests) == (
            code == "already_redeemed"
        )

    asyncio.run(run())


def test_reports_redemption_success_even_if_cooldown_clearing_fails() -> None:
    async def run() -> None:
        result = await fixture({"cooldownStatus": 404}).api.consume(config, "first.json", "credit")
        assert result["outcome"] == "reset"
        assert "cooldown" in result["warning"]

    asyncio.run(run())


def test_skips_disabled_accounts_and_rejects_redemption_on_them() -> None:
    async def run() -> None:
        test = fixture({"accounts": [{**accounts[0], "disabled": True}]})
        assert await test.api.readAccounts(config) == []
        with pytest.raises(UsageLimitSourceError):
            await test.api.consume(config, "first.json", "credit")
        assert all(request["path"].endswith("/auth-files") for request in test.requests)

    asyncio.run(run())


def test_keeps_the_same_redemption_id_after_an_uncertain_upstream_failure() -> None:
    async def run() -> None:
        attempts = 0

        def upstream(_: Any) -> dict[str, Any]:
            nonlocal attempts
            attempts += 1
            return (
                {"status": 503, "body": {}}
                if attempts == 1
                else {"status": 200, "body": {"code": "already_redeemed"}}
            )

        test = fixture({"upstream": upstream})
        with pytest.raises(UsageLimitSourceError):
            await test.api.consume(config, "first.json", "credit")
        assert await test.api.consume(config, "first.json", "credit") == {
            "outcome": "alreadyRedeemed"
        }
        data = [
            request["body"]["data"]
            for request in test.requests
            if request.get("body", {}).get("url", "").endswith("/consume")
        ]
        assert data[0] == data[1]
        assert (
            len([request for request in test.requests if request["path"].endswith("/reset-quota")])
            == 1
        )

    asyncio.run(run())


def test_rejects_unknown_accounts_without_forwarding_a_redemption() -> None:
    async def run() -> None:
        test = fixture()
        with pytest.raises(UsageLimitSourceError):
            await test.api.consume(config, "missing.json", "credit")
        assert len(test.requests) == 1

    asyncio.run(run())
