"""Pure logic for the verify instrument: the test skip-scan and the CSS syntax
check.

No side effects and no printing — everything here is importable and unit-tested
(test_instrument exercises the scan and the css check against synthetic inputs).
verify.py owns argv, subprocess, and output.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import NamedTuple


class Violation(NamedTuple):
    """A forbidden pattern found in a scanned test file."""

    file: str
    pattern: str


def parse_verify_mode(argv: list[str]) -> str:
    """Resolve the explicit verification tier without weakening the full default."""
    if not argv:
        return "full"
    if len(argv) == 1 and argv[0] in {"full", "fast", "integration", "e2e"}:
        return argv[0]
    raise ValueError("usage: ./verify [full|fast|integration|e2e]")


# --- skip-scan -------------------------------------------------------------

# Substring patterns forbidden anywhere in a test line (SPEC 18.2). Note that
# "@pytest.mark.skip" also catches "@pytest.mark.skipif" and "xfail" catches
# both the marker and pytest.xfail() — stricter, never weaker.
_TEXT_PATTERNS: tuple[str, ...] = (
    "@pytest.mark.skip",
    "pytest.skip(",
    "xfail",
    ".only",
)


def _iter_py_files(paths: Iterable[Path | str]) -> Iterator[Path]:
    """Yield every *.py file for the given paths; a path may be a file or a dir."""
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            yield from sorted(p.rglob("*.py"))
        elif p.suffix == ".py" and p.is_file():
            yield p


def _is_empty_test_body(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when a test function's body is only pass / ... / string literals."""
    body = list(fn.body)
    for stmt in body:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            # a bare docstring, "..." (Ellipsis), or string literal is a no-op
            if stmt.value.value is Ellipsis or isinstance(stmt.value.value, str):
                continue
        return False
    return True


def scan_test_files(paths: Iterable[Path | str]) -> list[Violation]:
    """Scan test files for the six forbidden patterns of SPEC 18.2.

    Returns a deduplicated (file, pattern) list. Files that cannot be read are
    skipped; a file that will not parse still gets its text patterns scanned.
    """
    violations: list[Violation] = []
    seen: set[tuple[str, str]] = set()

    def add(file_str: str, pattern: str) -> None:
        key = (file_str, pattern)
        if key not in seen:
            seen.add(key)
            violations.append(Violation(file_str, pattern))

    for path in _iter_py_files(paths):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        file_str = str(path)

        for line in text.splitlines():
            for pattern in _TEXT_PATTERNS:
                if pattern in line:
                    add(file_str, pattern)
            stripped = line.strip()
            if stripped.startswith("#") and "def test_" in stripped:
                add(file_str, "commented-out test")

        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name.startswith("test_")
                and _is_empty_test_body(node)
            ):
                add(file_str, "empty test body")

    return violations


# --- css syntax check (build gate, SPEC 18.2) -------------------------------


def check_css_syntax(text: str) -> list[str]:
    """Validate CSS syntax the way ``node --check`` validates JS: structurally,
    not semantically. Checks brace balance and unterminated block comments and
    strings (CSS strings may not contain a raw newline; ``\\``-escapes and
    escaped line continuations are honored). Returns human-readable errors,
    empty when the text is clean."""
    errors: list[str] = []
    depth = 0
    line = 1
    in_comment = False
    comment_line = 0
    quote: str | None = None
    quote_line = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\n":
            if quote is not None:
                errors.append(f"line {quote_line}: unterminated string")
                quote = None
            line += 1
            i += 1
            continue
        if in_comment:
            if ch == "*" and text[i : i + 2] == "*/":
                in_comment = False
                i += 2
                continue
            i += 1
            continue
        if quote is not None:
            if ch == "\\":
                if text[i + 1 : i + 3] == "\r\n":  # escaped CRLF continuation
                    line += 1
                    i += 3
                    continue
                if text[i + 1 : i + 2] == "\n":
                    line += 1  # escaped newline: a legal string continuation
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch == "/" and text[i : i + 2] == "/*":
            in_comment = True
            comment_line = line
            i += 2
            continue
        if ch in ("'", '"'):
            quote = ch
            quote_line = line
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            if depth == 0:
                errors.append(f"line {line}: unexpected '}}'")
            else:
                depth -= 1
        i += 1
    if quote is not None:
        errors.append(f"line {quote_line}: unterminated string")
    if in_comment:
        errors.append(f"line {comment_line}: unterminated block comment")
    if depth > 0:
        errors.append(f"{depth} unclosed '{{'")
    return errors
