"""No code in this repository copies a real agent login.

Panels runs one codex account. Its login is one file holding one rotating refresh token,
and codex writes a new token into whichever home it was pointed at. So a second home
holding a copy of that file is a credential with two owners: codex rotates it in the copy,
and the file everything else reads keeps a token the server has already spent. A dead
login then looks healthy, because nothing involved knows it was replaced.

That is not hypothetical. A real-provider test once copied the live login into a scratch
home and pointed ``CODEX_HOME`` at it. That test is gone, and nothing stops the next one
being written — which is what this check is. It is deliberately simple: a module that
mentions a login file and also copies files is the shape of the mistake, and a test that
needs a login must write a synthetic one instead.
"""

from __future__ import annotations

import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SEARCHED_DIRECTORIES = ("src", "tests", "scripts", "ops")

LOGIN_FILE_MENTIONED = re.compile(r"auth\.json|LOGIN_FILE_NAME")
A_FILE_COPY = re.compile(r"\b(?:shutil\.)?copy(?:2|file|tree)?\s*\(")

# The module that reads logins for a living, and this check, both name the file on purpose.
ALLOWED = {
    Path("src/planner/conversation/backends/codex_app_server/login.py"),
    Path("tests/unit/test_no_codex_credential_copies.py"),
}


def _searched_files() -> list[Path]:
    found: list[Path] = []
    for directory in SEARCHED_DIRECTORIES:
        root = REPOSITORY_ROOT / directory
        if root.exists():
            found.extend(sorted(root.rglob("*.py")))
    return found


def test_no_module_both_names_a_login_file_and_copies_files() -> None:
    searched = _searched_files()
    assert searched, "the repository scan found no Python at all, so it proves nothing"

    offending: list[str] = []
    for path in searched:
        relative = path.relative_to(REPOSITORY_ROOT)
        if relative in ALLOWED:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if LOGIN_FILE_MENTIONED.search(text) and A_FILE_COPY.search(text):
            offending.append(str(relative))

    assert not offending, (
        "these modules name an agent login file and copy files, which is how one rotating "
        "credential ends up with two homes. Write a synthetic login instead: "
        f"{offending}"
    )
