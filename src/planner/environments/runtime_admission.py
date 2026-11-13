"""One bounded runtime admission before any deployment service action."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from re import fullmatch
from typing import IO

from planner.environments.app import AppManifest, AppValidationError
from planner.environments.runtime_contracts import (
    NodeRuntimeContract,
    RuntimeContract,
)
from planner.platform_runtime.contracts import RuntimeRequirements
from planner.platform_runtime.selection import select_home_runtime
from planner.platform_runtime.targets import LINUX_HOME, RuntimeTarget

PROBE_TIMEOUT_SECONDS = 45
ADMISSION_TIMEOUT_SECONDS = 120
PROBE_REPORT_LIMIT = 1024 * 1024

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


# Version 2. This server owns the probe source; the candidate supplies no probe and no
# entrypoint runs. Host-only operations finish first. Then the probe records the
# libraries host Node already loaded. Only libraries loaded after that baseline must
# come from the candidate or be a declared glibc library.
NODE_PROBE = r"""
import { createRequire } from 'node:module';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import tls from 'node:tls';
import zlib from 'node:zlib';

const EXTERNAL = new Set(__EXTERNAL_LIBRARIES__);
const root = fs.realpathSync(process.argv[1]);
const fail = (message) => { throw new Error(message); };
const inside = (file) => {
  const relative = path.relative(root, file);
  return relative !== '' && !relative.startsWith('..') && !path.isAbsolute(relative);
};
const libraries = () => {
  const loaded = new Set();
  for (const line of fs.readFileSync('/proc/self/maps', 'utf8').split('\n')) {
    const file = line.trim().split(/\s+/).slice(5).join(' ');
    const name = path.basename(file);
    // Shared objects, and Node native addons, which end in .node.
    if (file.startsWith('/') && (name.includes('.so') || name.endsWith('.node'))) {
      loaded.add(fs.realpathSync(file));
    }
  }
  return loaded;
};

// Host Node only: TLS, SHA256, AES-GCM, zlib, Brotli and ICU.
tls.createSecureContext();
crypto.createHash('sha256').update('proof').digest();
const key = crypto.randomBytes(32), iv = crypto.randomBytes(12);
const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
const sealed = Buffer.concat([cipher.update('proof'), cipher.final()]);
const decipher = crypto.createDecipheriv('aes-256-gcm', key, iv);
decipher.setAuthTag(cipher.getAuthTag());
const opened = Buffer.concat([decipher.update(sealed), decipher.final()]);
if (opened.toString() !== 'proof') fail('AES-GCM');
if (zlib.inflateSync(zlib.deflateSync('proof')).toString() !== 'proof') fail('zlib');
const brotli = zlib.brotliDecompressSync(zlib.brotliCompressSync('proof'));
if (brotli.toString() !== 'proof') fail('Brotli');
const day = new Intl.DateTimeFormat('en-US', {
  timeZone: 'UTC', year: 'numeric', month: '2-digit', day: '2-digit',
}).format(new Date(0));
if (day !== '01/01/1970') fail(`ICU: ${day}`);

// The trusted host baseline, recorded before any candidate code.
const baseline = libraries();

const manifest = path.join(root, 'server', 'package.json');
const require = createRequire(manifest);
const dependencies = Object.keys(JSON.parse(fs.readFileSync(manifest, 'utf8')).dependencies ?? {});
if (dependencies.length === 0) fail('server/package.json declares no production dependencies');
const package_paths = {};
for (const name of dependencies.sort()) {
  const resolved = fs.realpathSync(require.resolve(name));
  if (!inside(resolved)) fail(`${name} resolves outside the candidate: ${resolved}`);
  package_paths[name] = resolved;
}
for (const name of ['better-sqlite3', 'koffi', '@anthropic-ai/claude-agent-sdk']) {
  if (!(name in package_paths)) fail(`server/package.json lacks ${name}`);
}

const Database = require('better-sqlite3');
const db = new Database(':memory:');
db.exec('CREATE TABLE proof (answer INTEGER)');
db.prepare('INSERT INTO proof VALUES (?)').run(42);
if (db.prepare('SELECT answer FROM proof').get().answer !== 42) fail('SQLite');
db.close();

const koffi = require('koffi');
const libc = koffi.load('libc.so.6');
if (libc.func('int getpid()')() !== process.pid) fail('Koffi getpid');
const flock = libc.func('int flock(int, int)');
const lock = fs.openSync('admission.lock', 'w');
if (flock(lock, 2 | 4) !== 0 || flock(lock, 8) !== 0) fail('Koffi flock');
fs.closeSync(lock);

const loaded = libraries();
for (const file of loaded) {
  // An addon must be the candidate's own: no glibc name or baseline entry qualifies it.
  if (file.endsWith('.node') && !inside(file)) fail(`addon outside the candidate: ${file}`);
  if (!baseline.has(file) && !inside(file) && !EXTERNAL.has(path.basename(file))) {
    fail(`candidate loaded a host library: ${file}`);
  }
}
process.stdout.write(JSON.stringify({
  node_version: process.versions.node,
  native: 'passed',
  loaded_libraries: [...loaded].sort(),
  package_paths,
}));
"""


def node_probe_source(contract: NodeRuntimeContract) -> str:
    return NODE_PROBE.replace(
        "__EXTERNAL_LIBRARIES__", json.dumps(list(contract.external_libraries))
    )


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


def _run_owned(
    command: list[str], *, state: Path, timeout: float, stdout: IO[str], stderr: IO[str]
) -> int:
    """Run in its own process group; on timeout or error kill and reap that group."""
    if timeout <= 0:
        raise AppValidationError("runtime admission exceeded its time budget")
    with subprocess.Popen(
        command, cwd=state, env=probe_environment(state),
        stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
        text=True, start_new_session=True,
    ) as process:
        try:
            return process.wait(timeout=min(PROBE_TIMEOUT_SECONDS, timeout))
        except BaseException:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise


def _tail(stream: IO[str]) -> str:
    stream.seek(0, os.SEEK_END)
    stream.seek(max(0, stream.tell() - 2000))
    return stream.read()


def _run_probe(
    command: list[str], *, state: Path, timeout: float = PROBE_TIMEOUT_SECONDS
) -> str:
    try:
        with tempfile.TemporaryFile(mode="w+") as output:
            code = _run_owned(command, state=state, timeout=timeout, stdout=output, stderr=output)
            detail = _tail(output)
            if code:
                raise AppValidationError(f"runtime probe failed: {detail}")
            return detail.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise AppValidationError(f"runtime probe failed: {exc}") from exc


def _run_json_probe(command: list[str], *, state: Path, timeout: float) -> object:
    """Stdout must be one JSON document; warnings on stderr only explain a failure."""
    try:
        with (
            tempfile.TemporaryFile(mode="w+") as output,
            tempfile.TemporaryFile(mode="w+") as errors,
        ):
            code = _run_owned(command, state=state, timeout=timeout, stdout=output, stderr=errors)
            if code:
                raise AppValidationError(f"runtime probe failed: {_tail(errors)}")
            output.seek(0)
            text = output.read(PROBE_REPORT_LIMIT + 1)
            if len(text) > PROBE_REPORT_LIMIT:
                raise AppValidationError("runtime probe report is too large")
    except (OSError, subprocess.SubprocessError) as exc:
        raise AppValidationError(f"runtime probe failed: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AppValidationError("runtime probe returned no valid JSON") from exc


def verify_runtime_host(contract: RuntimeRequirements, *, state: Path) -> None:
    runtime = select_home_runtime(RuntimeTarget(contract.system, contract.architecture, "home"))
    runtime.verify_host(contract, state=state)


def _require_minimum_node(version: str, contract: NodeRuntimeContract) -> None:
    match = fullmatch(r"(\d+)\.(\d+)\.(\d+)", version.strip())
    if match is None:
        raise AppValidationError(f"unreadable Node version: {version[:40]}")
    found = (int(match[1]), int(match[2]), int(match[3]))
    minimum = tuple(int(part) for part in contract.minimum_node_version.split("."))
    if found[0] != contract.node_major or found < minimum:
        raise AppValidationError(
            f"runtime requires Node {contract.minimum_node_version} or a later Node "
            f"{contract.node_major}"
        )


def native_tool_commands(
    root: Path, contract: RuntimeRequirements, *, node: str = "node"
) -> list[list[str]]:
    return select_home_runtime(LINUX_HOME).native_tool_commands(root, contract, node=node)


def admit_runtime(root: Path, manifest: AppManifest) -> None:
    """Reject legacy candidates, verify the host, then execute the private runtime.

    Version 1 runs the private CPython probe. Version 2 has no private Python.
    """
    contract = manifest.runtime_contract
    if contract is None:
        raise AppValidationError("candidate lacks a runtime contract; republish the app")
    deadline = time.monotonic() + ADMISSION_TIMEOUT_SECONDS
    with tempfile.TemporaryDirectory(prefix="panels-runtime-probe-") as temporary:
        state = Path(temporary)
        verify_runtime_host(contract, state=state)
        if isinstance(contract, RuntimeContract):
            _run_python_probe(root, contract, state=state, deadline=deadline)
            commands = native_tool_commands(root.resolve(), contract)
        elif isinstance(contract, NodeRuntimeContract):
            node = _host_node()
            _run_node_probe(root, contract, node=node, state=state, deadline=deadline)
            commands = native_tool_commands(root.resolve(), contract, node=node)
        else:
            raise AppValidationError("unsupported runtime contract")
        for command in commands:
            _run_probe(command, state=state, timeout=deadline - time.monotonic())


def _host_node() -> str:
    found = shutil.which("node", path="/usr/local/bin:/usr/bin:/bin")
    if found is None:
        raise AppValidationError("runtime host prerequisite is missing: node")
    return str(Path(found).resolve())


def _run_node_probe(
    root: Path, contract: NodeRuntimeContract, *, node: str, state: Path, deadline: float
) -> None:
    """Run this server's own probe source with host Node and check its exact report."""
    app_root = root.resolve()
    report = _run_json_probe(
        [node, "--input-type=module", "-e", node_probe_source(contract), str(app_root)],
        state=state, timeout=deadline - time.monotonic(),
    )
    if not isinstance(report, dict) or set(report) != {
        "node_version", "native", "loaded_libraries", "package_paths",
    }:
        raise AppValidationError("runtime probe report has missing or unknown fields")
    version, libraries, packages = (
        report["node_version"], report["loaded_libraries"], report["package_paths"],
    )
    if not isinstance(version, str) or report["native"] != "passed":
        raise AppValidationError("runtime probe did not pass")
    _require_minimum_node(version, contract)
    if (
        not isinstance(libraries, list)
        or not all(isinstance(item, str) and item.startswith("/") for item in libraries)
        or libraries != sorted(libraries)
    ):
        raise AppValidationError("runtime probe reported malformed loaded libraries")
    if any(
        item.endswith(".node") and not Path(item).resolve().is_relative_to(app_root)
        for item in libraries
    ):
        raise AppValidationError("runtime probe loaded a native addon outside the candidate")
    declared = json.loads((app_root / "server/package.json").read_text()).get("dependencies")
    if (
        not isinstance(packages, dict)
        or not isinstance(declared, dict)
        or set(packages) != set(declared)
        or not all(
            isinstance(item, str) and Path(item).resolve().is_relative_to(app_root)
            for item in packages.values()
        )
    ):
        raise AppValidationError("runtime probe packages differ from the candidate's own")


def _run_python_probe(
    root: Path, contract: RuntimeContract, *, state: Path, deadline: float
) -> None:
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
