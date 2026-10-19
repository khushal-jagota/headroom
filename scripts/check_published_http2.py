"""Require HTTP/2 from the explicitly configured published Panels origin."""

from __future__ import annotations

import os
import subprocess
import sys
from urllib.parse import urlsplit


def published_meta_url(origin: str) -> str:
    """Accept only an HTTPS origin, never a path or implicit target."""
    try:
        parsed = urlsplit(origin)
        valid = (
            bool(origin)
            and not any(character.isspace() for character in origin)
            and parsed.scheme == "https"
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and parsed.path in ("", "/")
            and not parsed.query
            and not parsed.fragment
            and "?" not in origin
            and "#" not in origin
        )
        # Access validates a supplied port even though it is not rewritten.
        _ = parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("PANELS_PUBLISHED_ORIGIN must contain an explicit HTTPS origin")
    return origin.rstrip("/") + "/api/meta"


def main() -> int:
    try:
        target = published_meta_url(os.environ.get("PANELS_PUBLISHED_ORIGIN", ""))
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f"Published HTTP/2 check: {target}", flush=True)
    try:
        result = subprocess.run(
            [
                "curl", "--disable", "--http2", "--silent", "--show-error",
                "--connect-timeout", "5", "--max-time", "10",
                "--output", "/dev/null", "--write-out", "%{http_code} %{http_version}\\n",
                "--url", target,
            ],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"Published HTTP/2 check failed: {error}", file=sys.stderr)
        return 1
    fields = result.stdout.split()
    status, version = fields if len(fields) == 2 else ("unknown", "unknown")
    print(f"Published response: status={status} http_version={version}", flush=True)
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)
    if result.returncode:
        print(f"curl failed with exit code {result.returncode}", file=sys.stderr)
        return 1
    if len(status) != 3 or not status.isascii() or not status.isdigit() or version != "2":
        print("Published route requires an HTTP/2 response", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
