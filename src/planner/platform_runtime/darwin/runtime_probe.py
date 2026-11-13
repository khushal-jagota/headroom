"""Native Mac home proof for declared runtime dependencies and loaded libraries."""

NATIVE_PROBE = r"""
import sys, pathlib, json, os, platform
assert sys.platform == "darwin" and platform.machine() == "arm64"
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
# Public operations for the remaining native runtime dependencies.
import aiohttp, frozenlist, multidict, yarl, propcache, rpds, cffi
import markupsafe, charset_normalizer, asyncio
from aiohttp.http import HttpResponseParser
from propcache.api import cached_property
for module in (aiohttp, frozenlist, multidict, yarl, propcache, rpds, cffi,
               markupsafe, charset_normalizer):
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
loop = asyncio.new_event_loop()
try:
    parser = HttpResponseParser(None, loop, 8192)
    messages, upgraded, tail = parser.feed_data(
        b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
    assert messages[0][0].code == 200 and not upgraded and not tail
finally:
    loop.close()

loaded = set()
loader = ctypes.CDLL(None)
loader._dyld_image_count.restype = ctypes.c_uint32
loader._dyld_get_image_name.argtypes = [ctypes.c_uint32]
loader._dyld_get_image_name.restype = ctypes.c_char_p
for index in range(loader._dyld_image_count()):
    raw = loader._dyld_get_image_name(index)
    assert raw is not None
    resolved = pathlib.Path(os.fsdecode(raw)).resolve()
    loaded.add(str(resolved))
    system_library = str(resolved).startswith(tuple(json.loads(sys.argv[3])))
    assert resolved.is_relative_to(root) or system_library, str(resolved)
assert any("libicu" in path and pathlib.Path(path).is_relative_to(root) for path in loaded)
print(json.dumps({"libraries": sorted(loaded), "python": sys.version.split()[0],
                  "prefix": sys.prefix, "native": "passed"}))
"""
