"""Pure logic for the verify instrument (SPEC 18.2): item registry, skip-scan,
junit parsing, and scoring.

No side effects and no printing — everything here is importable and unit-testable
(item 21 exercises the scan and scorer against synthetic inputs). verify.py owns
argv, subprocess, and output.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple
from xml.etree import ElementTree


@dataclass(frozen=True)
class Item:
    """One acceptance item from SPEC 18.3."""

    number: int
    suite: str  # "unit" | "e2e"
    token: str  # anchor prefix, e.g. "test_a01" / "test_e22"
    label: str  # short phrase for the scoreboard


@dataclass(frozen=True)
class ItemResult:
    item: Item
    passed: bool


class Violation(NamedTuple):
    """A forbidden pattern found in a scanned test file."""

    file: str
    pattern: str


# The 36 acceptance items, in item order (units 1-21 and 36, e2e 22-35). The
# scoreboard is printed in this order; scoring picks each item's junit map by
# its suite, not its position.
ITEMS: list[Item] = [
    Item(1, "unit", "test_a01", "planning-date math"),
    Item(2, "unit", "test_a02", "gating chain"),
    Item(3, "unit", "test_a03", "ceiling auto-accept"),
    Item(4, "unit", "test_a04", "at-cap stop vs propose"),
    Item(5, "unit", "test_a05", "one pending proposal per field"),
    Item(6, "unit", "test_a06", "edit-accept"),
    Item(7, "unit", "test_a07", "result routing"),
    Item(8, "unit", "test_a08", "recap"),
    Item(9, "unit", "test_a09", "blocking"),
    Item(10, "unit", "test_a10", "sprint-item permissions"),
    Item(11, "unit", "test_a11", "dispatch ordering"),
    Item(12, "unit", "test_a12", "day-ticket removal"),
    Item(13, "unit", "test_a13", "sprint assignment rules"),
    Item(14, "unit", "test_a14", "claim CAS"),
    Item(15, "unit", "test_a15", "TTL/reclaim"),
    Item(16, "unit", "test_a16", "circuit breaker"),
    Item(17, "unit", "test_a17", "day-plan tree"),
    Item(18, "unit", "test_a18", "boundary job"),
    Item(19, "unit", "test_a19", "seed fixtures"),
    Item(20, "unit", "test_a20", "freeze rules"),
    Item(21, "unit", "test_a21", "instrument integrity"),
    Item(22, "e2e", "test_e22", "CLI create to live board"),
    Item(23, "e2e", "test_e23", "env-pinned propose"),
    Item(24, "e2e", "test_e24", "accept in Review"),
    Item(25, "e2e", "test_e25", "edit-accept in Review"),
    Item(26, "e2e", "test_e26", "chat panel"),
    Item(27, "e2e", "test_e27", "auto-accept chain e2e"),
    Item(28, "e2e", "test_e28", "day view"),
    Item(29, "e2e", "test_e29", "invalidation"),
    Item(30, "e2e", "test_e30", "dispatcher e2e"),
    Item(31, "e2e", "test_e31", "refresh restores state"),
    Item(32, "e2e", "test_e32", "sprint view live"),
    Item(33, "e2e", "test_e33", "seed e2e"),
    Item(34, "e2e", "test_e34", "snapshot migration e2e"),
    Item(35, "e2e", "test_e35", "dogfood Level A"),
    Item(36, "unit", "test_a36", "onward grant"),
]


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


# --- junit parsing + scoring ----------------------------------------------


def _outcome(testcase: ElementTree.Element) -> str:
    """Map a junit <testcase> to passed / failed / error / skipped."""
    tags = {child.tag.lower() for child in testcase}
    if "error" in tags:
        return "error"
    if "failure" in tags:
        return "failed"
    if "skipped" in tags:
        return "skipped"
    return "passed"


def parse_junit(path: Path | str) -> dict[str, str]:
    """Parse a junit XML file into {classname::name: outcome}.

    A missing or unparseable file yields an empty map, so every item scored
    against it FAILs. Keys are fully qualified so same-named tests in different
    files stay distinct (and thus count as multiple matches).
    """
    p = Path(path)
    if not p.exists():
        return {}
    try:
        tree = ElementTree.parse(p)
    except (ElementTree.ParseError, OSError):
        return {}
    results: dict[str, str] = {}
    for testcase in tree.iter("testcase"):
        name = testcase.get("name")
        if not name:
            continue
        classname = testcase.get("classname", "")
        results[f"{classname}::{name}"] = _outcome(testcase)
    return results


def score(
    unit_results: dict[str, str], e2e_results: dict[str, str]
) -> list[ItemResult]:
    """Score every item. An item PASSes iff exactly one collected test name is
    anchored to its token (bare name starts with ``<token>_``) and that test
    passed; zero matches, multiple matches, failure, error, or skip mean FAIL.
    """
    out: list[ItemResult] = []
    for item in ITEMS:
        table = unit_results if item.suite == "unit" else e2e_results
        prefix = item.token + "_"
        matches = [
            outcome
            for key, outcome in table.items()
            if key.rsplit("::", 1)[-1].startswith(prefix)
        ]
        passed = len(matches) == 1 and matches[0] == "passed"
        out.append(ItemResult(item, passed))
    return out
