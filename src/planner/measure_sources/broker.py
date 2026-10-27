"""One clean, host-only broker process per source run. No database access."""

from __future__ import annotations

import sys
from dataclasses import fields
from typing import cast

from planner.credentials.contracts import (
    BasicSecret,
    BearerSecret,
    CredentialAuthKind,
    CredentialMetadata,
    CredentialReference,
    CredentialScheme,
    CredentialScope,
    HeaderSecret,
    ResolvedCredential,
)
from planner.measure_sources.configuration import BROKER_CONFIGURATION_BYTES
from planner.measure_sources.contracts import (
    BrokerConfiguration,
    DestinationGrant,
    FailureCode,
    FailureReply,
    HostRequest,
    HostResponse,
    HttpMethod,
    HttpTransport,
    JsonValue,
    PathMatch,
    RequestReply,
    RunFailure,
    RuntimeBudget,
    RuntimeLimits,
)
from planner.measure_sources.logic.protocol import (
    Refusal,
    decode,
    encode,
    integer,
    object_value,
    refuse,
    string,
)
from planner.measure_sources.logic.transport import authenticate, redact, secret_patterns
from planner.measure_sources.transport import PublicHttpsTransport


def configuration_from_wire(value: JsonValue) -> BrokerConfiguration:
    data = object_value(value)
    scope_data = object_value(data["scope"])
    scope = CredentialScope(string(scope_data["project_id"]), string(scope_data["account"]))
    limits_data = object_value(data["limits"])
    limit_values = {f.name: limits_data[f.name] for f in fields(RuntimeLimits)}
    limits = RuntimeLimits(**cast(dict[str, int], limit_values))
    destinations: list[DestinationGrant] = []
    for raw in cast(list[JsonValue], data["destinations"]):
        grant = object_value(raw)
        reference = None
        if grant["credential"] is not None:
            ref = object_value(grant["credential"])
            reference = CredentialReference(
                CredentialScheme(string(ref["scheme"])), string(ref["name"])
            )
        destinations.append(
            DestinationGrant(
                string(grant["id"]),
                string(grant["origin"]),
                HttpMethod(string(grant["method"])),
                string(grant["path"]),
                PathMatch(string(grant["path_match"])),
                reference,
            )
        )
    credentials: list[ResolvedCredential] = []
    for raw in cast(list[JsonValue], data["credentials"]):
        item = object_value(raw)
        metadata = object_value(item["metadata"])
        secret_data = object_value(item["secret"])
        scoped = object_value(metadata["scope"])
        reference_data = object_value(metadata["reference"])
        kind = CredentialAuthKind(string(metadata["auth_kind"]))
        secret: BearerSecret | BasicSecret | HeaderSecret
        if kind == CredentialAuthKind.bearer:
            secret = BearerSecret(string(secret_data["token"]))
        elif kind == CredentialAuthKind.basic:
            secret = BasicSecret(string(secret_data["username"]), string(secret_data["password"]))
        else:
            secret = HeaderSecret(string(secret_data["header_name"]), string(secret_data["value"]))
        credentials.append(
            ResolvedCredential(
                CredentialMetadata(
                    string(metadata["id"]),
                    CredentialScope(string(scoped["project_id"]), string(scoped["account"])),
                    CredentialReference(
                        CredentialScheme(string(reference_data["scheme"])),
                        string(reference_data["name"]),
                    ),
                    kind,
                    tuple(string(x) for x in cast(list[JsonValue], metadata["allowed_origins"])),
                    integer(metadata["revision"]),
                    integer(metadata["created_at"]),
                    integer(metadata["updated_at"]),
                ),
                secret,
            )
        )
    return BrokerConfiguration(
        scope,
        tuple(destinations),
        tuple(credentials),
        limits,
        float(cast(float, data["deadline_monotonic"])),
    )


def _redact_json(value: JsonValue, patterns: tuple[str, ...]) -> JsonValue:
    if isinstance(value, str):
        return redact(value, patterns)
    if isinstance(value, list):
        return [_redact_json(item, patterns) for item in value]
    if isinstance(value, dict):
        return {redact(k, patterns): _redact_json(v, patterns) for k, v in value.items()}
    return value


def handle(
    config: BrokerConfiguration, request: HostRequest, transport: HttpTransport
) -> HostResponse:
    authorized = authenticate(config, request)
    response = transport.send(
        authorized,
        deadline_monotonic=config.deadline_monotonic,
        max_response_bytes=config.limits.response_bytes,
    )
    if len(response.body) > config.limits.response_bytes:
        raise refuse(FailureCode.budget_exhausted, RuntimeBudget.response_bytes)
    if response.status >= 400:
        raise Refusal(RunFailure(FailureCode.http_error, http_status=response.status))
    patterns = secret_patterns(config)
    headers = tuple((redact(k, patterns), redact(v, patterns)) for k, v in response.headers)
    body: JsonValue = None
    if not 300 <= response.status < 400:
        content_type = dict((k.lower(), v) for k, v in response.headers).get("content-type", "")
        mime = content_type.split(";", 1)[0].strip().lower()
        if mime != "application/json" and not (
            mime.startswith("application/") and mime.endswith("+json")
        ):
            raise refuse(FailureCode.invalid_json)
        try:
            text = redact(response.body.decode("utf-8"), patterns)
            body = _redact_json(decode(text.encode(), depth=config.limits.json_depth), patterns)
        except (Refusal, UnicodeError):
            raise refuse(FailureCode.invalid_json) from None
    return HostResponse(response.status, headers, body)


def main(transport: HttpTransport | None = None) -> None:
    """Adapter injection is a Python test seam, never a process argument or setting."""
    adapter = transport if transport is not None else PublicHttpsTransport()
    identifier: int | None = None
    try:
        line = sys.stdin.buffer.readline(BROKER_CONFIGURATION_BYTES + 1)
        if len(line) > BROKER_CONFIGURATION_BYTES or not line.endswith(b"\n"):
            raise refuse(FailureCode.malformed_protocol)
        config = configuration_from_wire(decode(line))
        total = 0
        count = 0
        while line := sys.stdin.buffer.readline(config.limits.request_frame_bytes + 1):
            if len(line) > config.limits.request_frame_bytes or not line.endswith(b"\n"):
                raise refuse(FailureCode.budget_exhausted, RuntimeBudget.request_frame_bytes)
            data = object_value(decode(line, depth=config.limits.json_depth))
            identifier = integer(data["id"])
            count += 1
            if count > config.limits.request_count:
                raise refuse(FailureCode.budget_exhausted, RuntimeBudget.request_count)
            request = HostRequest(
                string(data["grant_id"]),
                string(data["url"]),
                HttpMethod(string(data["method"])),
                data.get("json_body"),
            )
            response = handle(config, request, adapter)
            reply = encode(RequestReply(identifier, response))
            if len(reply) > config.limits.response_bytes:
                raise refuse(FailureCode.budget_exhausted, RuntimeBudget.response_bytes)
            total += len(reply)
            if total > config.limits.total_response_bytes:
                raise refuse(FailureCode.budget_exhausted, RuntimeBudget.total_response_bytes)
            sys.stdout.buffer.write(reply)
            sys.stdout.buffer.flush()
    except Refusal as failure:
        sys.stdout.buffer.write(encode(FailureReply(identifier, failure.failure)))
        sys.stdout.buffer.flush()
    except MemoryError:
        sys.stdout.buffer.write(
            encode(
                FailureReply(
                    identifier,
                    RunFailure(
                        FailureCode.budget_exhausted,
                        RuntimeBudget.broker_memory_bytes,
                    ),
                )
            )
        )
        sys.stdout.buffer.flush()
    except Exception:
        sys.stdout.buffer.write(
            encode(FailureReply(identifier, RunFailure(FailureCode.transport_failure)))
        )
        sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
