/** Trusted v1 compatibility probe. It runs only the candidate's own CPython. */
import { GLIBC_LIBRARIES } from '../updates/logic/runtime_contract.ts';
import { SYSTEM_LIBRARY_DIRECTORIES } from './native/configuration.ts';
export const NATIVE_PYTHON_PROBE = String.raw`
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
external = set(${JSON.stringify(GLIBC_LIBRARIES)})
system_directories = {pathlib.Path(path).resolve() for path in ${JSON.stringify(SYSTEM_LIBRARY_DIRECTORIES)}
                      if pathlib.Path(path).is_dir()}
for line in pathlib.Path("/proc/self/maps").read_text().splitlines():
    parts = line.split(maxsplit=5)
    if len(parts) < 6 or "x" not in parts[1] or not parts[5].startswith("/"):
        continue
    resolved = pathlib.Path(parts[5]).resolve()
    loaded.add(str(resolved))
    assert resolved.is_relative_to(root) or (
        resolved.parent in system_directories and resolved.name in external), str(resolved)
assert any("libicu" in path and pathlib.Path(path).is_relative_to(root) for path in loaded)
print(json.dumps({"libraries": sorted(loaded), "python": sys.version.split()[0],
                  "prefix": sys.prefix, "native": "passed"}))
`;
