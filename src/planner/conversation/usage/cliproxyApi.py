"""T3 cliproxyApi.ts, translated mechanically with an injected HTTP transport.

No transport or provider action is constructed by this module. The production
usage path configures no external source. Upstream protocol tests use fake HTTP.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlparse
from uuid import UUID

from .claudeUsageLimits import claudeUsageResponseToLimits, isoFromString
from .codexUsageLimits import codexRateLimitsToLimits, isNumber
from .contracts.providerUsageLimits import UsageLimitSourceError
from .providerUsageLimits import makeUnavailableUsageLimits

CODEX_BASE = "https://chatgpt.com/backend-api/wham"
CREDIT_URL = f"{CODEX_BASE}/rate-limit-reset-credits"
HttpTransport = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def creditRedeemRequestId(accountId: str, creditId: str) -> str:
    digest = hashlib.sha1(
        bytes.fromhex("6f1c2a9e2d4b4c1e9a7f3b8d5e0c1a42") + f"{accountId}:{creditId}".encode()
    ).digest()[:16]
    result = bytearray(digest)
    result[6] = (result[6] & 0x0F) | 0x50
    result[8] = (result[8] & 0x3F) | 0x80
    return str(UUID(bytes=bytes(result)))


def codexPlanLabel(planType: str | None) -> str | None:
    # Exact dependency from CodexProvider.ts:102–133, without its provider runtime.
    labels = {
        "free": "Free",
        "go": "Go",
        "plus": "Plus",
        "pro": "Pro 20x",
        "prolite": "Pro 5x",
        "team": "Team",
        "self_serve_business_prolite": "Business",
        "self_serve_business_usage_based": "Business",
        "business": "Business",
        "ent26": "Enterprise",
        "enterprise_cbp_automation": "Enterprise",
        "enterprise_cbp_usage_based": "Enterprise",
        "enterprise": "Enterprise",
        "edu": "Edu",
        "edu_plus": "Edu",
        "edu_pro": "Edu",
    }
    if planType == "unknown":
        return "ChatGPT Subscription"
    label = labels.get(planType or "")
    return f"ChatGPT {label} Subscription" if label else None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Expected an object")
    return value


def _string(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected a string")
    return value


def _number(value: Any) -> float:
    if not isNumber(value):
        raise ValueError("Expected a number")
    return float(value)


def _optional(
    value: dict[str, Any], key: str, validator: Callable[[Any], Any], *, nullable: bool = False
) -> None:
    if key in value and not (nullable and value[key] is None):
        validator(value[key])


def _window(value: Any, provider: str) -> None:
    value = _object(value)
    if provider == "codex":
        _number(value["used_percent"])
        _optional(value, "reset_at", _number, nullable=True)
        _optional(value, "limit_window_seconds", _number)
    else:
        _number(value["utilization"])
        if value["resets_at"] is not None:
            _string(value["resets_at"])


def _usage(body: str, provider: str) -> dict[str, Any]:
    value = _object(json.loads(body))
    if provider == "codex":
        _optional(value, "plan_type", _string)
        rate_limit = value["rate_limit"]
        if rate_limit is not None:
            rate_limit = _object(rate_limit)
            for key in ("primary_window", "secondary_window"):
                _optional(rate_limit, key, lambda v: _window(v, "codex"), nullable=True)
    else:
        for key in ("five_hour", "seven_day"):
            _optional(value, key, lambda v: _window(v, "claude"), nullable=True)
        if "limits" in value:
            if not isinstance(value["limits"], list):
                raise ValueError("Expected an array")
            for limit in value["limits"]:
                limit = _object(limit)
                _string(limit["kind"])
                _optional(limit, "percent", _number, nullable=True)
                _optional(limit, "resets_at", _string, nullable=True)
                if limit.get("scope") is not None:
                    scope = _object(limit["scope"])
                    if scope.get("model") is not None:
                        _string(_object(scope["model"])["display_name"])
    return value


class CliproxyApi:
    def __init__(
        self, client: HttpTransport, now: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self.client = client
        self.now = now

    async def management(self, config: dict[str, Any], path: str, body: Any = None) -> Any:
        try:
            base = urlparse(config["url"])
            if not base.scheme or not base.netloc:
                raise ValueError("Invalid URL")
            url = urljoin(config["url"], f"/v0/management/{path}")
        except (ValueError, KeyError) as error:
            raise UsageLimitSourceError("The hub URL is not valid.") from error
        request = {
            "method": "GET" if body is None else "POST",
            "url": url,
            "headers": {"Authorization": f"Bearer {config['managementKey']}"},
        }
        if body is not None:
            request["body"] = body
        try:
            response = await asyncio.wait_for(self.client(request), timeout=15)
            if response["status"] < 200 or response["status"] >= 300:
                raise ValueError("HTTP status")
            return response["body"]
        except Exception as error:
            raise UsageLimitSourceError("The hub management request failed.") from error

    async def authFiles(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        response = _object(await self.management(config, "auth-files"))
        files = response["files"]
        if not isinstance(files, list):
            raise ValueError("Expected an array")
        for account in files:
            account = _object(account)
            for field in ("id", "auth_index", "provider"):
                _string(account[field])
            _optional(account, "email", _string)
            if "disabled" in account and not isinstance(account["disabled"], bool):
                raise ValueError("Expected a boolean")
            if "id_token" in account:
                token = _object(account["id_token"])
                for field in ("chatgpt_account_id", "chatgpt_plan_type"):
                    _optional(token, field, _string)
        return files

    async def apiCall(
        self, config: dict[str, Any], account: dict[str, Any], url: str, data: Any = None
    ) -> str:
        if account["provider"] == "codex":
            header = {
                "Authorization": "Bearer $TOKEN$",
                "Content-Type": "application/json",
                "OpenAI-Beta": "codex-1",
                "Originator": "Codex Desktop",
            }
            if account.get("id_token", {}).get("chatgpt_account_id"):
                header["Chatgpt-Account-Id"] = account["id_token"]["chatgpt_account_id"]
        else:
            header = {"Authorization": "Bearer $TOKEN$", "anthropic-beta": "oauth-2025-04-20"}
        request = {
            "auth_index": account["auth_index"],
            "method": "GET" if data is None else "POST",
            "url": url,
            "header": header,
        }
        if data is not None:
            request["data"] = _json(data)
        response = _object(await self.management(config, "api-call", request))
        status = _number(response["status_code"])
        body = _string(response["body"])
        if status < 200 or status >= 300:
            raise UsageLimitSourceError(f"The provider refused the hub request (HTTP {status:g}).")
        return body

    async def credits(
        self, config: dict[str, Any], account: dict[str, Any]
    ) -> list[dict[str, Any]]:
        body = await self.apiCall(config, account, CREDIT_URL)
        response = _object(json.loads(body))
        credits = response["credits"]
        if not isinstance(credits, list):
            raise ValueError("Expected an array")
        for credit in credits:
            credit = _object(credit)
            for field in ("id", "status", "reset_type", "expires_at"):
                _string(credit[field])
        now = self.now().timestamp()

        def timestamp(credit: dict[str, Any]) -> float:
            try:
                return datetime.fromisoformat(
                    credit["expires_at"].replace("Z", "+00:00")
                ).timestamp()
            except (ValueError, OverflowError):
                return float("nan")

        return sorted(
            [
                credit
                for credit in credits
                if credit["reset_type"] == "codex_rate_limits"
                and credit["status"] == "available"
                and timestamp(credit) > now
            ],
            key=timestamp,
        )

    async def readAccount(self, config: dict[str, Any], account: dict[str, Any]) -> dict[str, Any]:
        checkedAt = (
            self.now().astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        )
        base = {
            "id": account["id"],
            "driver": "codex" if account["provider"] == "codex" else "claudeAgent",
        }
        if account.get("email"):
            base["email"] = account["email"]
        try:
            if account["provider"] == "claude":
                body = await self.apiCall(
                    config, account, "https://api.anthropic.com/api/oauth/usage"
                )
                usage = _usage(body, "claude")
                model_scoped = [
                    {
                        "display_name": limit["scope"]["model"]["display_name"],
                        "utilization": limit["percent"],
                        "resets_at": limit.get("resets_at"),
                    }
                    for limit in usage.get("limits", [])
                    if limit["kind"] == "weekly_scoped"
                    and (limit.get("scope") or {}).get("model")
                    and isNumber(limit.get("percent"))
                ]
                return {
                    **base,
                    "plan": "Claude Subscription",
                    "usageLimits": claudeUsageResponseToLimits(
                        {
                            "checkedAt": checkedAt,
                            "response": {
                                "rate_limits_available": True,
                                "rate_limits": {
                                    "five_hour": usage.get("five_hour"),
                                    "seven_day": usage.get("seven_day"),
                                    "model_scoped": model_scoped,
                                },
                            },
                        }
                    )["limits"],
                }
            body = await self.apiCall(config, account, f"{CODEX_BASE}/usage")
            usage = _usage(body, "codex")

            def toWindow(window: dict[str, Any] | None) -> dict[str, Any] | None:
                if window is None:
                    return None
                result = {"usedPercent": window["used_percent"], "resetsAt": window.get("reset_at")}
                if "limit_window_seconds" in window:
                    result["windowDurationMins"] = window["limit_window_seconds"] / 60
                return result

            try:
                available = await self.credits(config, account)
            except Exception:
                available = None
            next = available[0] if available else None
            limits = codexRateLimitsToLimits(
                {
                    "checkedAt": checkedAt,
                    "snapshot": {
                        "planType": usage.get("plan_type"),
                        "primary": toWindow((usage.get("rate_limit") or {}).get("primary_window")),
                        "secondary": toWindow(
                            (usage.get("rate_limit") or {}).get("secondary_window")
                        ),
                    },
                }
            )
            if available is not None:
                limits["resetCredits"] = {"availableCount": len(available)}
                if next:
                    limits["resetCredits"].update(
                        {
                            "nextCreditId": next["id"],
                            "nextExpiresAt": isoFromString(next["expires_at"]),
                        }
                    )
            planType = usage.get("plan_type")
            plan = codexPlanLabel(
                account.get("id_token", {}).get("chatgpt_plan_type")
                if planType is None
                else planType
            )
            return {**base, **({"plan": plan} if plan is not None else {}), "usageLimits": limits}
        except Exception:
            return {
                **base,
                "usageLimits": makeUnavailableUsageLimits(
                    {
                        "checkedAt": checkedAt,
                        "reason": "probeFailed",
                        "message": "The hub could not read this account's usage.",
                    }
                ),
            }

    async def readAccounts(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            accounts = await self.authFiles(config)
        except Exception as error:
            raise UsageLimitSourceError("The hub could not list accounts.") from error
        semaphore = asyncio.Semaphore(4)

        async def read(account: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                return await self.readAccount(config, account)

        return list(
            await asyncio.gather(
                *(
                    read(account)
                    for account in accounts
                    if not account.get("disabled") and account["provider"] in ("codex", "claude")
                )
            )
        )

    async def consume(
        self, config: dict[str, Any], accountId: str, creditId: str
    ) -> dict[str, Any]:
        try:
            account = next(
                (account for account in await self.authFiles(config) if account["id"] == accountId),
                None,
            )
            if not account or account.get("disabled") or account["provider"] != "codex":
                raise UsageLimitSourceError("The Codex hub account is missing or disabled.")
            body = await self.apiCall(
                config,
                account,
                f"{CREDIT_URL}/consume",
                {
                    "redeem_request_id": creditRedeemRequestId(
                        (
                            account.get("id_token", {}).get("chatgpt_account_id")
                            if account.get("id_token", {}).get("chatgpt_account_id") is not None
                            else account["id"]
                        ),
                        creditId,
                    ),
                    "credit_id": creditId,
                },
            )
            response = _object(json.loads(body))
            outcome = {
                "reset": "reset",
                "nothing_to_reset": "nothingToReset",
                "no_credit": "noCredit",
                "already_redeemed": "alreadyRedeemed",
            }[response["code"]]
            if outcome not in ("reset", "alreadyRedeemed"):
                return {"outcome": outcome}
            result = {"outcome": outcome}
            try:
                await self.management(config, "reset-quota", {"auth_index": account["auth_index"]})
            except Exception:
                result["warning"] = (
                    "Credit redeemed, but the hub cooldown could not be cleared. "
                    "Routing may resume after its cooldown expires."
                )
            return result
        except UsageLimitSourceError:
            raise
        except Exception as error:
            raise UsageLimitSourceError(
                "The hub returned an unexpected reset-credit response."
            ) from error


def makeCliproxyApi(
    client: HttpTransport, now: Callable[[], datetime] = lambda: datetime.now(UTC)
) -> CliproxyApi:
    return CliproxyApi(client, now)
