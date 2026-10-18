"""Bounded, durable deployment evidence; a report never initiates deployment."""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import tempfile
from dataclasses import asdict, replace
from pathlib import Path
from typing import cast

from planner.environments.resident.contracts import (
    HostDeploymentResult,
    HostDisposition,
    HostName,
    ResidentDeploymentRecord,
    ResidentError,
    TwoHostDeploymentResult,
)
from planner.environments.resident.logic.deployment import validated_host_result

MAX_REPORT_BYTES = 16 * 1024


def _sha(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise ResidentError("deployment revision must be a full lowercase Git SHA")
    return value


def _object(value: object, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ResidentError("deployment report fields do not match the supported schema")
    return value


def _host(value: object, host: HostName, revision: str) -> HostDeploymentResult:
    data = _object(
        value,
        {
            "host",
            "requested_revision",
            "disposition",
            "observed_revision",
            "detail",
        },
    )
    observed = data["observed_revision"]
    if observed is not None:
        observed = _sha(observed)
    detail = data["detail"]
    if not isinstance(detail, str) or not detail or len(detail) > 500 or not detail.isprintable():
        raise ResidentError("host detail must be bounded printable text")
    if data["host"] != host or data["requested_revision"] != revision:
        raise ResidentError("host or requested revision does not match the report")
    disposition = data["disposition"]
    if not isinstance(disposition, str) or disposition not in {
        "verified",
        "unreachable",
        "deferred",
        "failed",
    }:
        raise ResidentError("unsupported host disposition")
    result = HostDeploymentResult(
        host, revision, cast(HostDisposition, disposition), observed, detail
    )
    if validated_host_result(host, revision, result) != result:
        raise ResidentError("verified host lacks exact revision evidence")
    return result


def parse_deployment_record(value: object) -> ResidentDeploymentRecord:
    data = _object(value, {"version", "deployment_id", "result"})
    if type(data["version"]) is not int or data["version"] != 1:
        raise ResidentError("unsupported resident report version")
    deployment_id = data["deployment_id"]
    if (
        not isinstance(deployment_id, str)
        or len(deployment_id) > 128
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", deployment_id) is None
    ):
        raise ResidentError("invalid deployment identity")
    result = _object(data["result"], {"requested_revision", "vps", "mac", "verified"})
    revision = _sha(result["requested_revision"])
    vps = _host(result["vps"], "vps", revision)
    mac = _host(result["mac"], "mac", revision)
    verified = vps.disposition == "verified" and mac.disposition == "verified"
    if type(result["verified"]) is not bool or result["verified"] != verified:
        raise ResidentError("aggregate verification conflicts with host evidence")
    return ResidentDeploymentRecord(
        1, deployment_id, TwoHostDeploymentResult(revision, vps, mac, verified)
    )


def _safe_path(path: Path) -> None:
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ResidentError("deployment report paths must not contain symlinks")


def read_deployment_record(path: Path) -> ResidentDeploymentRecord:
    _safe_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ResidentError("deployment report must be a regular file")
        content = stream.read(MAX_REPORT_BYTES + 1)
    if len(content) > MAX_REPORT_BYTES:
        raise ResidentError("deployment report is too large")
    try:
        return parse_deployment_record(json.loads(content))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResidentError("deployment report is malformed") from exc


def write_deployment_record(path: Path, record: ResidentDeploymentRecord) -> None:
    """Publish evidence atomically with bounded summaries of raw adapter details.

    Only human detail changes. Identity and outcome evidence still pass the
    strict parser. A multiline exception must not prevent failure publication.
    """

    def summarize(host: HostDeploymentResult) -> HostDeploymentResult:
        if not isinstance(host.detail, str):
            raise ResidentError("host detail must be text")
        printable = "".join(char if char.isprintable() else " " for char in host.detail)
        summary = " ".join(printable.split())[:500] or "No detail supplied"
        return replace(host, detail=summary)

    record = replace(
        record,
        result=replace(
            record.result, vps=summarize(record.result.vps), mac=summarize(record.result.mac)
        ),
    )
    validated = parse_deployment_record(asdict(record))
    encoded = (json.dumps(asdict(validated), sort_keys=True) + "\n").encode()
    if len(encoded) > MAX_REPORT_BYTES:
        raise ResidentError("deployment report is too large")
    _safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f"{path.name}.lock")
    _safe_path(lock_path)
    lock_descriptor = os.open(
        lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    with os.fdopen(lock_descriptor, "r+") as lock:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
            raise ResidentError("deployment report lock must be a regular file")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        _safe_path(path)
        if path.exists():
            existing = read_deployment_record(path)
            if (
                existing.deployment_id != record.deployment_id
                or existing.result.requested_revision != record.result.requested_revision
            ):
                raise ResidentError("report path belongs to another deployment")
        temporary: str | None = None
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            temporary = None
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary is not None:
                os.unlink(temporary)
