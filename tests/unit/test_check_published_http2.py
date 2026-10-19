"""Exercise the command boundary without contacting the published origin."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts/check_published_http2.py"


@pytest.fixture
def curl_environment(tmp_path: Path) -> dict[str, str]:
    curl = tmp_path / "curl"
    curl.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "pathlib.Path(os.environ['CURL_ARGUMENTS']).write_text(json.dumps(sys.argv[1:]))\n"
        "print(os.environ.get('CURL_RESPONSE', '200 2'))\n"
        "sys.stderr.write(os.environ.get('CURL_ERROR', ''))\n"
        "sys.exit(int(os.environ.get('CURL_EXIT', '0')))\n"
    )
    curl.chmod(0o755)
    return os.environ | {
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "PANELS_PUBLISHED_ORIGIN": "https://panels.example:443/",
        "CURL_ARGUMENTS": str(tmp_path / "arguments.json"),
    }


@pytest.mark.parametrize("status", ["200", "503"])
def test_http2_success_uses_bounded_tls_curl_without_user_configuration(
    curl_environment: dict[str, str], status: str,
) -> None:
    result = subprocess.run(
        [sys.executable, str(HELPER)], env=curl_environment | {"CURL_RESPONSE": f"{status} 2"},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert f"status={status} http_version=2" in result.stdout
    arguments = json.loads(Path(curl_environment["CURL_ARGUMENTS"]).read_text())
    assert arguments == [
        "--disable", "--http2", "--silent", "--show-error",
        "--connect-timeout", "5", "--max-time", "10", "--output", "/dev/null",
        "--write-out", "%{http_code} %{http_version}\\n",
        "--url", "https://panels.example:443/api/meta",
    ]


@pytest.mark.parametrize(("response", "exit_code", "error"), [
    ("200 1.1", "0", ""),  # --http2 alone accepts ALPN downgrade.
    ("200 2", "60", "certificate verification failed"),
    ("000 0", "28", "operation timed out"),
    ("", "2", "curl: HTTP2 support unavailable"),
    ("unexpected output", "0", ""),
])
def test_protocol_and_transport_failures_reject_success(
    curl_environment: dict[str, str], response: str, exit_code: str, error: str,
) -> None:
    result = subprocess.run(
        [sys.executable, str(HELPER)], capture_output=True, text=True, check=False,
        env=curl_environment | {
            "CURL_RESPONSE": response, "CURL_EXIT": exit_code, "CURL_ERROR": error,
        },
    )
    assert result.returncode != 0
    assert "Published response: status=" in result.stdout
    assert error in result.stderr


@pytest.mark.parametrize("origin", [
    "", "http://panels.example", "https://panels.example/api/meta",
    "https://user@panels.example", "https://panels.example?target=other",
    "https://panels.example/#fragment", "https://panels.example:invalid",
])
def test_missing_or_non_origin_configuration_fails_before_curl(
    curl_environment: dict[str, str], origin: str,
) -> None:
    result = subprocess.run(
        [sys.executable, str(HELPER)], capture_output=True, text=True, check=False,
        env=curl_environment | {"PANELS_PUBLISHED_ORIGIN": origin},
    )
    assert result.returncode != 0
    assert "PANELS_PUBLISHED_ORIGIN" in result.stderr
    assert not Path(curl_environment["CURL_ARGUMENTS"]).exists()
