"""A real symlink and closed output pipe pin process behavior, not just run().

Reuse one existing synthetic host. Real pipe descriptors prove Click's quiet EPIPE
exit and the installed-style TS entry; an in-memory formatter cannot prove either.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from tests.compare.direct import unit20_cli as driver
from tests.support.server_fixtures import _start_server, _stop_server


def test_unit20_cli_native_process_boundaries(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    handle = _start_server(tmp_path / "host", server_module="tests.compare.host")
    evidence: dict[str, Any] = {"entry": {}, "closed_pipe": {}}
    try:
        host = driver.CliHost(handle, tmp_path)
        input_root = tmp_path / "input"
        driver._admit(host, input_root)
        env = driver.environment(host, {"actor": "owner"}, input_root)
        preload = [
            "node",
            "--import",
            str(driver.ROOT / "server/cli/testing/operator_probe.ts"),
            str(driver.ROOT / "server/cli/main.ts"),
        ]
        link = tmp_path / "panels"
        link.symlink_to(driver.ROOT / "server/cli/main.ts")
        # Existing host state is populated with default projects. Both executions
        # exercise the physical process and the same command from a symlink path.
        for argv in (["--help"], ["project", "list", "--limit", "1"]):
            direct = driver.raw_process([*driver.TYPESCRIPT, *argv], env)
            linked = driver.raw_process([str(link), *argv], env)
            evidence["entry"][" ".join(argv)] = [
                driver.process_answer(result) for result in (direct, linked)
            ]
            for result in evidence["entry"][" ".join(argv)]:
                driver._check_output(result, {"exit_code": 0}, argv, "text", candidate=True)
            assert direct.stdout == linked.stdout and direct.stderr == linked.stderr == b""
        # Close the read end before launch. Unlike head timing, this reliably
        # forces a real EPIPE without depending on pipe capacity or scheduling.
        for name, command, stream in (
            ("python", [*driver.PYTHON, "--help"], "stdout"),
            ("typescript", [*driver.TYPESCRIPT, "--help"], "stdout"),
            ("sigterm", [*preload, "serve"], "stderr"),
        ):
            read_end, write_end = os.pipe()
            os.close(read_end)
            try:
                child = subprocess.run(
                    command,
                    cwd=driver.ROOT,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=write_end if stream == "stdout" else subprocess.PIPE,
                    stderr=write_end if stream == "stderr" else subprocess.PIPE,
                    timeout=40,
                    check=False,
                )
            finally:
                os.close(write_end)
            evidence["closed_pipe"][name] = {
                "exit_code": child.returncode,
                "stderr": (child.stderr or b"").decode(),
            }
        for name, code in (
            ("python", 1),
            ("typescript", 1),
            ("sigterm", -signal.SIGTERM),
        ):
            assert evidence["closed_pipe"][name] == {"exit_code": code, "stderr": ""}
        # A foreign socket at an arbitrary free port cannot borrow this host's
        # valid database/PID handle and reach the metadata or subprocess doors.
        with socket.socket() as foreign:
            foreign.bind(("127.0.0.1", 0))
            foreign.listen()
            port = foreign.getsockname()[1]
            forged = replace(handle, base=f"http://127.0.0.1:{port}", port=port)

            def forbidden(*args: Any, **kwargs: Any) -> None:
                raise AssertionError("foreign listener admission reached network or execution")

            with (
                patch.object(httpx.Client, "get", forbidden),
                patch.object(subprocess, "run", forbidden),
            ):
                with pytest.raises(ValueError, match="fixture PID"):
                    driver.run_cli(
                        driver.CliHost(forged, tmp_path),
                        {
                            "kind": "cli",
                            "argv": ["--help"],
                            "identity": {"actor": "owner"},
                            "proof": "A foreign listener cannot impersonate a fixture-owned URL.",
                            "exit_code": 0,
                        },
                        True,
                        tmp_path / "refused-input",
                    )
        evidence["foreign_listener"] = "refused before metadata, files or subprocesses"
        # The existing owned host supplies the same private paths and process guard.
        import click
        from tests.support.update_apps import build_app

        from planner.cli.main import main

        app = build_app(input_root / "operator-app", "a" * 40, "marker")
        (app / "marker").write_text("changed after manifest")
        (input_root / "blocked'app").write_text("not a directory")
        failure = [
            "environment",
            "backup-current",
            "--source-db",
            str(handle.db_path),
            "--backup-dir",
            str(input_root / "blocked'app/backup"),
            "--current-app",
            str(app),
        ]
        answers = [
            driver.process_answer(driver.raw_process([*entry, *failure], env))
            for entry in (driver.PYTHON, driver.TYPESCRIPT)
        ]
        assert driver.comparable(answers[0]) == driver.comparable(answers[1])
        for answer in answers:
            assert answer["exit_code"] == 1 and answer["stdout"] == ""
            assert isinstance(answer["stderr"], str) and answer["stderr"]
            assert str(input_root / "blocked'app/backup") in answer["stderr"]
        success = list(failure)
        success[success.index("--backup-dir") + 1] = str(input_root / "warning-backups")
        completed = [
            driver.raw_process([*entry, *success], env, merge_stderr=True)
            for entry in (driver.PYTHON, driver.TYPESCRIPT)
        ]
        combined = [result.stdout.decode() for result in completed]
        assert all(result.returncode == 0 for result in completed)
        assert all(text for text in combined)
        assert all(Path(text.splitlines()[-1]).is_dir() for text in combined)
        interrupted = driver.raw_process([*preload, "restart"], env)
        stopped = driver.raw_process([*preload, "serve"], env)

        def abort() -> None:
            click.echo("retained prefix", err=True)
            raise click.Abort()

        with patch.object(main.commands["restart"], "callback", abort):
            abort_reference = driver._python_library(["restart"], b"", env)
        actual_abort = driver.process_answer(interrupted)
        assert driver.comparable(actual_abort) == driver.comparable(abort_reference)
        assert actual_abort["stderr"].startswith("retained prefix\n")
        assert stopped.returncode == -signal.SIGTERM and stopped.stdout == b""
        assert stopped.stderr == b"T" * (2 * 1024 * 1024)
        evidence["operators"] = {
            "failure": {"python": answers[0], "typescript": answers[1]},
            "warning_success": {
                "python": {
                    "exit_code": completed[0].returncode,
                    "combined": combined[0],
                },
                "typescript": {
                    "exit_code": completed[1].returncode,
                    "combined": combined[1],
                },
            },
            "sigint": {
                "python": abort_reference,
                "typescript": driver.process_answer(interrupted),
            },
            "sigterm": driver.process_answer(stopped),
        }
        evidence["transport"] = {}
        _transport_proof(host, tmp_path, env, evidence["transport"])
    finally:
        _stop_server(handle)
        evidence["seconds"] = time.perf_counter() - started
        driver.write_report(request, "unit20-cli-process", evidence)


def _transport_proof(
    host: driver.CliHost, root: Path, env: dict[str, str], evidence: dict[str, Any]
) -> None:
    """Real loopback sockets prove proxies, custom CAs and per-read timing cheaply.

    TLS/socket behavior cannot be established by a formatter test. Only the explicit
    transport test parameter changes to300ms; production remains30s in every phase.
    """
    import hashlib
    import ipaddress
    import ssl
    from datetime import UTC, datetime, timedelta

    import certifi
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    from planner.cli import http as python_http

    started = time.perf_counter()
    host.check()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "unit20.invalid")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = root / "transport-cert.pem", root / "transport-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    ca_dir = root / "transport-ca"
    ca_dir.mkdir()
    digest = (
        subprocess.check_output(
            ["openssl", "x509", "-hash", "-noout", "-in", str(cert_path)], env=env
        )
        .decode()
        .strip()
    )
    (ca_dir / (digest + ".0")).symlink_to(cert_path)
    wrong_ca_dir = root / "wrong-hash-ca"
    wrong_ca_dir.mkdir()
    wrong_hash = "deadbeef" if digest != "deadbeef" else "feedface"
    (wrong_ca_dir / (wrong_hash + ".0")).symlink_to(cert_path)
    assert digest.upper() != digest, "fixture subject hash must exercise uppercase rejection"
    (wrong_ca_dir / (digest.upper() + ".0")).symlink_to(cert_path)
    host.check()
    server = subprocess.Popen(
        [
            "node",
            str(driver.ROOT / "server/cli/testing/transport_server.ts"),
            str(cert_path),
            str(key_path),
        ],
        cwd=driver.ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert server.stdout is not None
        description = json.loads(server.stdout.readline())
        fault = replace(
            host.handle,
            proc=server,
            base=f"http://127.0.0.1:{description['httpPort']}",
            port=description["httpPort"],
        )
        owned = driver.CliHost(fault, root)
        driver._admit(owned, root / "transport-input")
        for name in ("tlsPort", "proxyPort"):
            driver.CliHost(
                replace(
                    fault,
                    base=f"http://127.0.0.1:{description[name]}",
                    port=description[name],
                ),
                root,
            ).check()
        assert (
            description["defaults"] == {"connect_ms": 30000, "write_ms": 30000, "read_ms": 30000}
            and python_http._TIMEOUT == 30
        )
        assert description["messages"] and all(
            isinstance(message, str) and message for message in description["messages"].values()
        )
        evidence["native_errors"] = description["messages"]
        evidence["production_limits"] = description["defaults"]
        python_roots = sorted(
            {
                hashlib.sha256(cert).hexdigest()
                for cert in ssl.create_default_context(cafile=certifi.where()).get_ca_certs(
                    binary_form=True
                )
            }
        )
        node_roots = sorted(set(description["root_fingerprints"]))
        evidence["default_trust"] = {
            "python_policy": "HTTPX certifi roots",
            "python_count": len(python_roots),
            "typescript_policy": "Node bundled roots",
            "typescript_count": len(node_roots),
            "python_fingerprints": python_roots,
            "typescript_fingerprints": node_roots,
            "python_only": sorted(set(python_roots) - set(node_roots)),
            "typescript_only": sorted(set(node_roots) - set(python_roots)),
        }
        origin = f"http://127.0.0.1:{description['httpPort']}"
        tls = f"https://127.0.0.1:{description['tlsPort']}"
        proxy = f"http://127.0.0.1:{description['proxyPort']}"
        cases: list[tuple[str, str, dict[str, str], bool]] = [
            ("http_proxy", origin, {"HTTP_PROXY": proxy}, True),
            ("all_proxy", origin, {"ALL_PROXY": proxy}, True),
            ("no_proxy", origin, {"ALL_PROXY": proxy, "NO_PROXY": "127.0.0.1"}, False),
            ("ssl_cert_file", tls, {"SSL_CERT_FILE": str(cert_path)}, False),
            (
                "missing_cert_file",
                tls,
                {"SSL_CERT_FILE": str(root / "absent-ca.pem")},
                False,
            ),
            ("ssl_cert_dir", tls, {"SSL_CERT_DIR": str(ca_dir)}, False),
            (
                "https_proxy_ca",
                tls,
                {"HTTPS_PROXY": proxy, "SSL_CERT_FILE": str(cert_path)},
                True,
            ),
            ("missing_scheme", origin.removeprefix("http://"), {}, False),
            ("malformed_url", "http://[", {}, False),
            ("tls_untrusted", tls, {"SSL_CERT_DIR": str(wrong_ca_dir)}, False),
        ]
        evidence["cases"] = {}
        evidence["case_environments"] = {}
        for name, url, settings, proxied in cases:
            evidence["case_environments"][name] = {"PLAN_SERVER_URL": url, **settings}
            outcomes: list[dict[str, Any]] = []
            for implementation, command in (
                ("python", driver.PYTHON),
                ("typescript", driver.TYPESCRIPT),
            ):
                owned.check()
                before = httpx.get(origin + "/proxy-count", trust_env=False).json()["proxied"]
                child_env = {**env, "PLAN_SERVER_URL": url, **settings}
                completed = driver.raw_process([*command, "project", "list", "--json"], child_env)
                after = httpx.get(origin + "/proxy-count", trust_env=False).json()["proxied"]
                assert (after > before) == proxied, (
                    name,
                    implementation,
                    before,
                    after,
                )
                outcomes.append(driver.process_answer(completed))
            evidence["cases"][name] = outcomes
            if name == "malformed_url":
                # Manager accepts clean validation1 for all malformed URL syntax;
                # fresh Python treats this bracket form as a DNS connection failure2.
                assert outcomes[0]["exit_code"] == 2 and outcomes[1]["exit_code"] == 1
                assert json.loads(outcomes[0]["stderr"])["error"]["code"] == "connection"
                assert outcomes[0]["stdout"] == outcomes[1]["stdout"] == ""
                assert json.loads(outcomes[1]["stderr"])["error"] == {
                    "code": "validation",
                    "message": "Invalid Panels server URL: http://[",
                    "detail": {},
                }
            elif name == "missing_cert_file":
                assert outcomes[0]["exit_code"] == outcomes[1]["exit_code"] == 1
                assert outcomes[0]["stdout"] == outcomes[1]["stdout"] == ""
                assert json.loads(outcomes[1]["stderr"])["error"] == {
                    "code": "validation",
                    "message": f"Cannot read SSL_CERT_FILE: {root / 'absent-ca.pem'}",
                    "detail": {},
                }
            elif name == "tls_untrusted":
                for answer in outcomes:
                    assert answer["exit_code"] == 2 and answer["stdout"] == ""
                    assert json.loads(answer["stderr"])["error"]["code"] == "connection"
                # The accepted contract retains this runtime's native TLS wording.
                # Reuse the same owned listener and its empty effective CA set.
                driver.CliHost(
                    replace(
                        fault,
                        base=tls.replace("https:", "http:"),
                        port=description["tlsPort"],
                    ),
                    root,
                ).check()
                oracle_started = time.perf_counter()
                native = driver.raw_process(
                    [
                        "node",
                        "--input-type=module",
                        "-e",
                        "import {get} from 'node:https';"
                        "get(process.argv[1],{ca:[],agent:false,servername:''},response=>{"
                        "response.resume();process.stderr.write('Expected native TLS rejection');"
                        "process.exitCode=1;}).on('error',error=>{"
                        "process.stdout.write(JSON.stringify({node:process.versions.node,"
                        "code:error.code,message:error.message}));});",
                        tls,
                    ],
                    child_env,
                )
                assert native.returncode == 0, native.stderr.decode()
                oracle = json.loads(native.stdout)
                evidence["tls_native_oracle"] = {
                    **oracle,
                    "seconds": time.perf_counter() - oracle_started,
                }
                assert oracle["code"] == "DEPTH_ZERO_SELF_SIGNED_CERT"
                assert json.loads(outcomes[1]["stderr"])["error"]["message"] == oracle["message"]
                assert json.loads(outcomes[0]["stderr"])["error"]["message"].startswith(
                    "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
                    "self-signed certificate ("
                )
            else:
                assert driver.comparable(outcomes[0]) == driver.comparable(outcomes[1]), (
                    name,
                    outcomes,
                )
                assert outcomes[0]["exit_code"] == (2 if name == "missing_scheme" else 0)
        # Node reads extra roots at process startup; reuse this listener and CA.
        owned.check()
        extra_env = {
            **env,
            "PLAN_SERVER_URL": tls,
            "NODE_EXTRA_CA_CERTS": str(cert_path),
        }
        extra_answer = driver.process_answer(
            driver.raw_process(
                [*driver.TYPESCRIPT, "project", "list", "--json"],
                extra_env,
            )
        )
        evidence["node_extra_ca_certs"] = {
            "environment": {
                "PLAN_SERVER_URL": tls,
                "NODE_EXTRA_CA_CERTS": str(cert_path),
            },
            "result": extra_answer,
        }
        assert extra_answer == evidence["cases"]["ssl_cert_file"][1]
        slow_env = {**env, "PLAN_SERVER_URL": origin}
        evidence["phase_waits"] = {
            "injected_ms": 300,
            "body_gap_ms": 200,
            "total_body_ms": 400,
            "stalled_gap_ms": 600,
            "header_gap_ms": 200,
            "total_headers_ms": 400,
            "results": {},
        }
        for path, expected_exit in (
            ("/headers", 0),
            ("/slow", 0),
            ("/stall", 2),
            ("/incomplete", 2),
        ):
            python_code = (
                "import json;from planner.cli import http;http._TIMEOUT=.3;"
                f"print(json.dumps({{'text':http.send_text('GET',{path!r},as_json=True)}}))"
            )
            node_code = """
import {send_text} from './server/cli/http.ts';
import {create_commands} from './server/cli/commands.ts';
import {find_command} from './server/cli/declarations.ts';
const inv={command:find_command(create_commands(process.env),['project','list']),
  values:{as_json:true},
  environment:process.env,stdin:async()=>Buffer.alloc(0)};
try {
  const text=await send_text(inv,'GET',process.argv[1],{
    transport_limits:{connect_ms:300,write_ms:300,read_ms:300}});
  process.stdout.write(JSON.stringify({text})+'\\n');
} catch(error) {
  if(!error.result)throw error;
  process.stderr.write(error.result.stderr);process.exitCode=error.result.exit_code;
}
"""
            phase: list[dict[str, Any]] = []
            commands = (
                [driver.PYTHON[0], "-c", python_code],
                ["node", "--input-type=module", "-e", node_code, path],
            )
            for command in commands:
                owned.check()
                elapsed = time.perf_counter()
                result = driver.raw_process(command, slow_env)
                phase.append(
                    {
                        **driver.process_answer(result),
                        "seconds": time.perf_counter() - elapsed,
                    }
                )
                assert result.returncode == expected_exit, result.stderr.decode()
                if expected_exit == 0:
                    assert json.loads(result.stdout) == {"text": '{"phase":"ok"}'}
                else:
                    assert result.stdout == b""
                    assert json.loads(result.stderr)["error"] == {
                        "code": "connection",
                        "message": (
                            "peer closed connection without sending complete message body "
                            "(received 5 bytes, expected 20)"
                            if path == "/incomplete"
                            else "timed out"
                        ),
                        "detail": {},
                    }
            evidence["phase_waits"]["results"][path] = phase
        evidence["accepted_policy"] = {
            "default_roots": "Node built-in roots; fresh root-set differences retained above",
            "tls_errors": "native diagnostics, same connection envelope and exit2",
            "configuration_errors": "clean validation exit1 for missing CA file and all malformed "
            "URL syntax; bracket comparison retains Python DNS connection exit2",
            "missing_scheme": "exact Python connection envelope and exit2",
        }
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
        evidence["seconds"] = time.perf_counter() - started
