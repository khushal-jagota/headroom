"""One bounded runtime admission before any deployment service action."""

from __future__ import annotations

import ctypes
import json
import os
import platform
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from planner.environments.app import AppManifest, AppValidationError
from planner.environments.runtime_contracts import RuntimeContract

PROBE_TIMEOUT_SECONDS = 45
ADMISSION_TIMEOUT_SECONDS = 120

# Isolated Python excludes cwd and environment paths. Use its installed packages
# and require every imported native package to stay in its root.
NATIVE_PROBE = r"""
import sys, pathlib, json, os
root = pathlib.Path(sys.argv[1]).resolve()
assert sys.version.split()[0] == sys.argv[2], sys.version
for path in (sys.executable, sys.prefix, sys.base_prefix, os.__file__):
    pathlib.Path(path).resolve().relative_to(root)
import planner, ssl, sqlite3, hashlib, zlib, bz2, lzma, ctypes, decimal
import icu, yaml, sqlalchemy, greenlet, pydantic, cryptography
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
for module in (planner, ssl, sqlite3, icu, yaml, sqlalchemy, greenlet, pydantic, cryptography):
    pathlib.Path(module.__file__).resolve().relative_to(root)
with sqlite3.connect(":memory:") as db:
    assert db.execute("select 6 * 7").fetchone()[0] == 42
ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
assert hashlib.sha256(b"proof").digest()
for module in (zlib, bz2, lzma):
    assert module.decompress(module.compress(b"proof")) == b"proof"
assert decimal.Decimal("1.25") * 2 == decimal.Decimal("2.50")
assert ctypes.CDLL(None).getpid() == os.getpid()
formatter = icu.SimpleDateFormat("yyyy-MM-dd", icu.Locale("en_US"))
formatter.setTimeZone(icu.TimeZone.createTimeZone("UTC"))
assert formatter.format(0.0) == "1970-01-01"
assert yaml.load("answer: 42", Loader=yaml.CSafeLoader)["answer"] == 42
engine = sqlalchemy.create_engine("sqlite://")
with engine.connect() as connection:
    assert connection.execute(sqlalchemy.text("select 42")).scalar() == 42
assert greenlet.greenlet(lambda: 42).switch() == 42
assert pydantic.TypeAdapter(int).validate_python("42") == 42
cipher = AESGCM(bytes(32))
assert cipher.decrypt(bytes(12), cipher.encrypt(bytes(12), b"proof", b""), b"") == b"proof"
# Public operations for every remaining native runtime package. Developer-only
# mypy/black and their compiled support packages receive loader/import proof.
import aiohttp, frozenlist, multidict, yarl, propcache, rpds, cffi
import markupsafe, charset_normalizer, websockets, asyncio
from aiohttp.http import HttpResponseParser
from propcache.api import cached_property
from websockets.frames import Frame, Opcode
for module in (aiohttp, frozenlist, multidict, yarl, propcache, rpds, cffi,
               markupsafe, charset_normalizer, websockets):
    pathlib.Path(module.__file__).resolve().relative_to(root)
values = frozenlist.FrozenList([42])
values.freeze()
assert values.frozen and values[0] == 42
headers = multidict.CIMultiDict({"Content-Type": "text/plain"})
assert headers["content-type"] == "text/plain"
assert str(yarl.URL("https://example.invalid").with_query(answer="42")) == (
    "https://example.invalid/?answer=42")
class Cached:
    @cached_property
    def answer(self):
        return object()
cached = Cached()
assert cached.answer is cached.answer
assert rpds.HashTrieMap({"answer": 42})["answer"] == 42
ffi = cffi.FFI()
assert ffi.new("int *", 42)[0] == 42
assert str(markupsafe.escape("<proof>")) == "&lt;proof&gt;"
sample = "The café serves crème brûlée and tea. " * 20
assert str(charset_normalizer.from_bytes(sample.encode("utf-8")).best()) == sample
frame = Frame(Opcode.TEXT, b"proof").serialize(mask=True)
assert len(frame) == 11 and frame[1] & 0x80
loop = asyncio.new_event_loop()
try:
    parser = HttpResponseParser(None, loop, 8192)
    messages, upgraded, tail = parser.feed_data(
        b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
    assert messages[0][0].code == 200 and not upgraded and not tail
finally:
    loop.close()
loaded = set()
external = set(json.loads(sys.argv[3]))
for line in pathlib.Path("/proc/self/maps").read_text().splitlines():
    path = line.split()[-1]
    if not path.startswith("/") or ".so" not in pathlib.Path(path).name:
        continue
    resolved = pathlib.Path(path).resolve()
    loaded.add(str(resolved))
    assert resolved.is_relative_to(root) or resolved.name in external, str(resolved)
assert any("libicu" in path and pathlib.Path(path).is_relative_to(root) for path in loaded)
print(json.dumps({"libraries": sorted(loaded), "python": sys.version.split()[0],
                  "prefix": sys.prefix, "native": "passed"}))
"""


def probe_environment(state: Path) -> dict[str, str]:
    """Allowlist instead of inheriting credentials, homes, or loader overrides."""
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(state),
        "TMPDIR": str(state),
        "XDG_CONFIG_HOME": str(state / "config"),
        "XDG_CACHE_HOME": str(state / "cache"),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PLAN_DISPATCH_ENABLED": "false",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
        "CODEX_HOME": str(state / "codex"),
        "CLAUDE_CONFIG_DIR": str(state / "claude"),
    }


def _run_probe(
    command: list[str], *, state: Path, timeout: float = PROBE_TIMEOUT_SECONDS
) -> str:
    if timeout <= 0:
        raise AppValidationError("runtime admission exceeded its time budget")
    try:
        with tempfile.TemporaryFile(mode="w+") as output:
            with subprocess.Popen(
                command, cwd=state, env=probe_environment(state),
                stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                text=True, start_new_session=True,
            ) as process:
                try:
                    code = process.wait(timeout=min(PROBE_TIMEOUT_SECONDS, timeout))
                except BaseException:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise
            output.seek(0, os.SEEK_END)
            output.seek(max(0, output.tell() - 2000))
            detail = output.read()
            if code:
                raise AppValidationError(f"runtime probe failed: {detail}")
            return detail.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise AppValidationError(f"runtime probe failed: {exc}") from exc


def verify_runtime_host(contract: RuntimeContract, *, state: Path) -> None:
    if platform.system().lower() != contract.system or platform.machine() != contract.architecture:
        raise AppValidationError("runtime requires Linux x86_64")
    try:
        release = platform.freedesktop_os_release()
        supported = next(
            item
            for item in contract.platforms
            if (item.distribution, item.version) == (release.get("ID"), release.get("VERSION_ID"))
        )
        libc, version = platform.libc_ver()
        if libc != "glibc" or tuple(map(int, version.split("."))) < tuple(
            map(int, supported.minimum_glibc.split("."))
        ):
            raise ValueError("unsupported glibc version")
        feature_sets = [
            set(line.split(":", 1)[1].split())
            for line in Path("/proc/cpuinfo").read_text().splitlines()
            if line.startswith("flags")
        ]
        if not feature_sets or any(
            not set(contract.cpu_features) <= features for features in feature_sets
        ):
            raise ValueError("required CPU features are unavailable")
    except (OSError, ValueError, StopIteration) as exc:
        raise AppValidationError(f"unsupported runtime host: {exc}") from exc
    for library in contract.external_libraries:
        try:
            ctypes.CDLL(library)
        except OSError as exc:
            raise AppValidationError(f"runtime host library is missing: {library}") from exc
    for executable in contract.external_executables:
        if shutil.which(executable, path="/usr/local/bin:/usr/bin:/bin") is None:
            raise AppValidationError(f"runtime host prerequisite is missing: {executable}")
    node = _run_probe(["/usr/bin/env", "node", "--version"], state=state)
    if node.split(".")[0] != f"v{contract.node_major}":
        raise AppValidationError(f"runtime requires Node {contract.node_major}")


def native_tool_commands(root: Path, contract: RuntimeContract) -> list[list[str]]:
    """Offline startup probes for selected Linux packages, never agent sessions."""
    modules = root / "agent_backends/node_modules"
    site = root / ".venv/lib/python3.12/site-packages"
    vendor = modules / "@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl"
    commands = [
        ["node", str(modules / "@anthropic-ai/claude-code/cli-wrapper.cjs"), "--version"],
        ["node", str(modules / "@openai/codex/bin/codex.js"), "--version"],
    ]
    for path in (
        modules / "@anthropic-ai/claude-code-linux-x64/claude",
        modules / "@anthropic-ai/claude-agent-sdk-linux-x64/claude",
        modules / "@anthropic-ai/claude-code/bin/claude.exe",
        site / "claude_agent_sdk/_bundled/claude",
        site / "playwright/driver/node",
        vendor / "bin/codex",
        vendor / "codex-resources/bwrap",
        vendor / "codex-resources/zsh/bin/zsh",
        vendor / "codex-path/rg",
    ):
        commands.append([str(path), "--version"])
    commands.append([str(vendor / "bin/codex-code-mode-host"), "--help"])
    return commands


def admit_runtime(root: Path, manifest: AppManifest) -> None:
    """Reject legacy candidates, verify the host, then execute the private runtime."""
    contract = manifest.runtime_contract
    if contract is None:
        raise AppValidationError("candidate lacks a runtime contract; republish the app")
    deadline = time.monotonic() + ADMISSION_TIMEOUT_SECONDS
    with tempfile.TemporaryDirectory(prefix="panels-runtime-probe-") as temporary:
        state = Path(temporary)
        verify_runtime_host(contract, state=state)
        _run_probe(
            [
                str(root.resolve() / ".venv/bin/python"),
                "-I",
                "-B",
                "-c",
                NATIVE_PROBE,
                str(root.resolve()),
                contract.python_version,
                json.dumps(contract.external_libraries),
            ],
            state=state, timeout=deadline - time.monotonic(),
        )
        for command in native_tool_commands(root.resolve(), contract):
            _run_probe(command, state=state, timeout=deadline - time.monotonic())
