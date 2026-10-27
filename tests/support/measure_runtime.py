"""Admitted synthetic sources and a TLS adapter reachable only from test code."""

from __future__ import annotations

import datetime
import hashlib
import http.client
import ssl
import time
from pathlib import Path
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from planner.credentials.contracts import CredentialScope
from planner.measure_sources.configuration import DEFAULT_LIMITS
from planner.measure_sources.contracts import (
    AuthenticatedHttpRequest,
    DestinationGrant,
    RunAdmission,
    RunMode,
    RunStatus,
    RuntimeLimits,
    RunTrigger,
    SourceRun,
    TransportResponse,
)
from planner.measures.contracts import MeasureKind, OutputMeasure


def admission(
    script: str,
    *,
    outputs: tuple[str, ...] = ("m",),
    grants: tuple[DestinationGrant, ...] = (),
    limits: RuntimeLimits = DEFAULT_LIMITS,
    run_id: str = "synthetic-run",
) -> RunAdmission:
    run = SourceRun(
        id=run_id,
        source_id="synthetic-source",
        mode=RunMode.test,
        trigger=RunTrigger.test,
        status=RunStatus.running,
        source_revision=1,
        script_revision=1,
        script_sha256=hashlib.sha256(script.encode()).hexdigest(),
        frozen_at=1793059200,
        required_outputs=tuple(
            OutputMeasure(value, 1, MeasureKind.reading, "Europe/Berlin", None, "2026-10-27", None)
            for value in outputs
        ),
        history=None,
        schedule_date=None,
        schedule_time=None,
        created_at=1793059200,
        started_at=1793059200,
        finished_at=None,
        failure=None,
        metrics=None,
        observation_count=0,
    )
    return RunAdmission(
        run,
        script,
        CredentialScope("synthetic-project", "synthetic-account"),
        grants,
        (),
        limits,
        time.monotonic() + limits.wall_seconds,
    )


class FixtureTransport:
    """Injected by a test-owned broker entry script; absent from production settings."""

    def __init__(self, certificate: str) -> None:
        self.context = ssl.create_default_context(cafile=certificate)

    def send(
        self,
        request: AuthenticatedHttpRequest,
        *,
        deadline_monotonic: float,
        max_response_bytes: int,
    ) -> TransportResponse:
        url = urlsplit(request.url)
        assert url.hostname == "localhost"
        connection = http.client.HTTPSConnection(
            "localhost",
            url.port,
            timeout=max(0.001, deadline_monotonic - time.monotonic()),
            context=self.context,
        )
        try:
            target = url.path + ("?" + url.query if url.query else "")
            connection.request(request.method.value, target, request.body, dict(request.headers))
            response = connection.getresponse()
            return TransportResponse(
                response.status,
                tuple(response.getheaders()),
                response.read(max_response_bytes + 1),
            )
        finally:
            connection.close()


def fixture_broker_command(original: list[str], certificate: Path) -> list[str]:
    root = Path(__file__).resolve().parents[2]
    code = original[-1].split("from planner.measure_sources.broker import main;main()")[0]
    code += (
        f"import sys;sys.path.insert(0,{str(root)!r});"
        "from tests.support.measure_runtime import FixtureTransport;"
        "from planner.measure_sources.broker import main;"
        f"main(FixtureTransport({str(certificate)!r}))"
    )
    return [*original[:-1], code]


def fixture_certificate(tmp_path: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "tls.crt"
    key_path = tmp_path / "tls.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path
