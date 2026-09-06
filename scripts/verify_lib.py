"""Pure logic for verification mode parsing and the CSS syntax check.

No side effects and no printing — everything here is importable and unit-tested
(test_instrument exercises the mode parser and css check against synthetic inputs).
verify.py owns argv, subprocess, and output.
"""

from __future__ import annotations


def parse_verify_mode(argv: list[str]) -> str:
    """Resolve the explicit verification tier without weakening the full default."""
    if not argv:
        return "full"
    if len(argv) == 1 and argv[0] in {"full", "fast", "integration", "e2e"}:
        return argv[0]
    raise ValueError("usage: ./verify [full|fast|integration|e2e]")


# --- css syntax check -------------------------------------------------------


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
