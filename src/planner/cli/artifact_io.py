"""Exact UTF-8 input shared by artifact commands."""

import sys
from pathlib import Path

from planner.cli import http


def read_artifact_source(spec: str, as_json: bool) -> str:
    """Keep the note's exact text, including CRLF and its final newline, if any."""
    try:
        raw = sys.stdin.buffer.read() if spec == "-" else Path(spec).read_bytes()
        return raw.decode("utf-8")
    except (OSError, UnicodeError):
        http.fail_validation(f"cannot read UTF-8 body file: {spec}", as_json)
