"""Apply or revert the three probes, so each effect can be measured on its own.

    python measure/probes/apply.py S1 C1 M1     # apply these
    python measure/probes/apply.py --revert     # put the tree back

S1  conditional responses on both managed-file routes
C1  one shared in-flight read per managed file
M1  media previews stop downloading before anybody asks
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
API = ROOT / "src/planner/files/api.py"
PREVIEW = ROOT / "web/src/components/FilePreview.svelte"
SHARED = ROOT / "web/src/lib/managedFileRead.ts"

SHARED_SOURCE = '''/** PROBE. One in-flight read per managed file, shared by the previews that name it.
 *
 * Concurrent reads only. The entry is dropped the moment the read settles, either way,
 * so nothing resolved is retained and a later read goes back to the server, where the
 * conditional response decides whether any bytes travel.
 */
type Entry = {
  promise: Promise<string>;
  controller: AbortController;
  consumers: number;
};

const inFlight = new Map<string, Entry>();

export function readManagedFile(href: string, leaving: AbortSignal): Promise<string> {
  let entry = inFlight.get(href);
  if (entry === undefined) {
    const controller = new AbortController();
    const promise = fetch(href, { signal: controller.signal }).then((response) => {
      if (!response.ok) throw new Error(`file fetch failed: ${response.status}`);
      return response.text();
    });
    entry = { promise, controller, consumers: 0 };
    inFlight.set(href, entry);
    const release = (): void => {
      if (inFlight.get(href) === entry) inFlight.delete(href);
    };
    promise.then(release, release);
  }
  const held = entry;
  held.consumers += 1;
  // One consumer leaving must not take the read away from the others. The fetch is
  // abandoned only when the last of them has gone.
  leaving.addEventListener("abort", () => {
    held.consumers -= 1;
    if (held.consumers === 0) held.controller.abort();
  });
  return held.promise;
}

/** Probe only: how many reads are in flight, for a test to assert on. */
export function inFlightManagedFileReads(): number {
  return inFlight.size;
}
'''

S1_IMPORTS = """from fastapi.responses import FileResponse
from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.staticfiles import NotModifiedResponse"""

S1_HELPER = '''

def _content_validator(path: Path) -> str:
    """A validator the file's own bytes decide.

    The stat-based one Starlette writes cannot tell two same-size writes apart inside
    the filesystem's 4 ms timestamp tick, and a managed artifact is machine-written.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return f'"{digest.hexdigest()}"'


def _reusable(request: Request, response: FileResponse, path: Path) -> Response:
    """PROBE. Let a browser reuse a copy it holds, and never a stale one."""
    statistics = path.stat()
    response.headers["ETag"] = _content_validator(path)
    response.headers["Last-Modified"] = formatdate(statistics.st_mtime, usegmt=True)
    response.headers.setdefault("Content-Length", str(statistics.st_size))
    # private: these files are answered per reader, and a shared cache must never hand
    # one reader's artifact to another. no-cache: revalidate on every use.
    response.headers["Cache-Control"] = "private, no-cache"
    if _matches(request.headers, response.headers["ETag"], response.headers["Last-Modified"]):
        return NotModifiedResponse(Headers(raw=response.raw_headers))
    return response


def _matches(request_headers: Headers, etag: str, last_modified: str) -> bool:
    """If-None-Match decides alone when it is present. It never falls through."""
    if_none_match = request_headers.get("if-none-match")
    if if_none_match is not None:
        if if_none_match.strip() == "*":
            return True
        return etag in [tag.strip().removeprefix("W/") for tag in if_none_match.split(",")]
    if_modified_since = parsedate(request_headers.get("if-modified-since", ""))
    served = parsedate(last_modified)
    return (
        if_modified_since is not None
        and served is not None
        and if_modified_since >= served
    )

'''


def apply(names: set[str]) -> None:
    if "S1" in names:
        text = API.read_text()
        text = text.replace("from fastapi.responses import FileResponse", S1_IMPORTS)
        text = text.replace(
            "import mimetypes\nimport re",
            "import hashlib\nimport mimetypes\nimport re\nfrom email.utils import formatdate, parsedate\nfrom pathlib import Path",
        )
        text = text.replace(
            "async def get_ticket_file(request: Request, ticket_id: str, file_path: str) -> FileResponse:",
            "async def get_ticket_file(request: Request, ticket_id: str, file_path: str) -> Response:",
        )
        text = text.replace(") -> FileResponse:\n", ") -> Response:\n")
        text = text.replace(
            '''    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@router.get("/files/sprint-items''',
            '''    response.headers["X-Content-Type-Options"] = "nosniff"
    return _reusable(request, response, ticket_file.absolute_path)


@router.get("/files/sprint-items''',
        )
        text = text.replace(
            '''    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


''',
            '''    response.headers["X-Content-Type-Options"] = "nosniff"
    return _reusable(request, response, managed_file.absolute_path)

'''
            + S1_HELPER
            + "\n",
            1,
        )
        API.write_text(text)
        print("applied S1")

    if "C1" in names:
        SHARED.write_text(SHARED_SOURCE)
        text = PREVIEW.read_text()
        text = text.replace(
            '  import MarkdownBlock from "./MarkdownBlock.svelte";',
            '  import MarkdownBlock from "./MarkdownBlock.svelte";\n'
            '  import { readManagedFile } from "../lib/managedFileRead";',
        )
        text = text.replace(
            """    const controller = new AbortController();
    fetch(current.href, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`file fetch failed: ${response.status}`);
        return response.text();
      })
      .then((body) => {""",
            """    const controller = new AbortController();
    readManagedFile(current.href, controller.signal)
      .then((body) => {
        if (controller.signal.aborted) return;""",
        )
        PREVIEW.write_text(text)
        print("applied C1")

    if "M1" in names:
        text = PREVIEW.read_text()
        text = text.replace('      preload="metadata"', '      preload="none"')
        PREVIEW.write_text(text)
        print("applied M1")


def revert() -> None:
    SHARED.unlink(missing_ok=True)
    subprocess.run(["git", "checkout", "--", str(API), str(PREVIEW)], cwd=ROOT, check=True)
    print("reverted")


if __name__ == "__main__":
    if "--revert" in sys.argv:
        revert()
    else:
        apply(set(sys.argv[1:]))
