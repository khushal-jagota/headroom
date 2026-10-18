"""Tripwire for a second copy of a real agent login, never a proof of safety.

A real-provider test once copied the live login into a scratch home and pointed
CODEX_HOME at it. A rotating refresh token then had two owners: rotation in the
scratch home left the canonical login with a spent token. This check targets that
mistake by inspecting each supported call's source and destination for auth.json
or LOGIN_FILE_NAME, including earlier simple assignments in the same lexical scope.
It claims no parity with the old module-wide text check.

The line is between a name that resolves the current canonical file and a second
thing that holds its bytes. A symlink is on the first side. Copies and hardlinks
are on the second: a hardlink shares an inode now, but diverges the moment the
canonical path is replaced by rename. shutil.move and os.replace are deliberately
absent: they move the credential to one place rather than create a second owner.

Blind spots: whole-home copies (documented limit)
------------------------------------------------
copytree(real_home, scratch_home) names no login, so it passes. copytree is
supported only when an argument explicitly names a login path. Matching its
destination to a later agent home would still not prove whether the template
contains real credentials: external environment inputs decide its contents.
General coverage would require tracing helpers, branches, filesystem operations
and launch paths, not merely finding a copy beside a home assignment.

The invariant is one canonical home and credential per real agent identity.
Synthetic homes are allowed. Configuration-only copies with a symlink to the
canonical credential keep one owner; whole real-home or credential copies do not.
The Codex runtime guard separately rejects matching credentials in a different
home for children Panels launches, allowing unreadable credentials with a warning.
It does not protect Hermes or Claude. The separate bounded source detector in
scripts/check_agent_home_copies.py relates supported copies to selected agent homes,
but cannot prove external credential provenance or safety on its omitted routes.

The steering harness now copies Hermes configuration while symlinking canonical
auth.json, and copies Claude settings while symlinking canonical .credentials.json.
Codex uses its real home. The former whole-home and credential copies are removed;
docs/conversation-system.md describes the current checks and their remaining limits.
This tripwire has no new exemption.

Other blind spots
-----------------
Resolution does not follow helper returns, object attributes, container lookups,
cross-module assignments, or control-flow-dependent reassignments. Argument
unpacking such as copy2(*paths), read_bytes/write_bytes transfers, other non-shutil
routes, and shell copies through subprocess are outside coverage. The old check
also missed byte-transfer and shell-copy routes. Reviewers retain responsibility
for these paths.

Readability costs more than two regexes: this is not a thirty-second resolver.
The top-level rule stays short through named helpers for call identification,
argument selection, and bounded resolution. It is not a general data-flow engine.
For example, copying config.yaml beside a login symlink passes, while copying
p after p = home / "auth.json" fails. A safe symlink never excuses another copy.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SEARCHED_DIRECTORIES = ("src", "tests", "scripts", "ops")
COPY_FUNCTIONS = {"shutil.copy", "shutil.copy2", "shutil.copyfile", "shutil.copytree", "os.link"}

# The module that reads logins for a living, and this check, both name the file on purpose.
ALLOWED = {
    Path("src/planner/conversation/backends/codex_app_server/login.py"),
    Path("tests/unit/test_no_codex_credential_copies.py"),
}


def _qualified_name(node: ast.expr, imports: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return imports.get(node.id, "")
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value, imports)
        return f"{parent}.{node.attr}" if parent else ""
    return ""


def _names_login(node: ast.AST, assigned_logins: dict[str, bool]) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "LOGIN_FILE_NAME" or assigned_logins.get(node.id, False)
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str) and "auth.json" in node.value
    # Attribute/container/helper results are not resolved as assignments elsewhere.
    # A literal login name in a path expression still counts, e.g. Path("auth.json").
    return any(_names_login(child, assigned_logins) for child in ast.iter_child_nodes(node))


def _copy_paths(call: ast.Call, imports: dict[str, str]) -> list[ast.expr]:
    function = _qualified_name(call.func, imports)
    if function in COPY_FUNCTIONS:
        paths = [arg for arg in call.args[:2] if not isinstance(arg, ast.Starred)]
        return paths + [kw.value for kw in call.keywords if kw.arg in {"src", "dst"}]
    if isinstance(call.func, ast.Attribute):
        if call.func.attr == "symlink_to":
            return []  # A symlink resolves the current canonical login, not a second copy.
        if call.func.attr == "hardlink_to":
            return [
                call.func.value,
                *call.args[:1],
                *(kw.value for kw in call.keywords if kw.arg == "target"),
            ]
    return []


class _LoginCopies(ast.NodeVisitor):
    """Walk statements in order; snapshot simple assignments, never merge flows."""

    def __init__(self, imports: dict[str, str] | None = None) -> None:
        self.imports = dict(imports or {})
        self.assigned_logins: dict[str, bool] = {}
        self.offending_lines: list[int] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports[alias.asname or alias.name.split(".")[0]] = (
                alias.name if alias.asname else alias.name.split(".")[0]
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level == 0 and node.module:
            for alias in node.names:
                self.imports[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    def _assign(self, target: ast.expr, value: ast.expr | None) -> None:
        if isinstance(target, ast.Name):
            self.assigned_logins[target.id] = value is not None and _names_login(
                value, self.assigned_logins
            )
            self.imports.pop(target.id, None)
        elif isinstance(target, (ast.Tuple, ast.List)):
            # Destructuring is outside simple assignment resolution. Invalidate aliases.
            for element in target.elts:
                self._assign(element, None)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self._assign(target, node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
            self._assign(node.target, node.value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.value)
        self._assign(node.target, None)

    def visit_Call(self, node: ast.Call) -> None:
        if any(
            _names_login(path, self.assigned_logins) for path in _copy_paths(node, self.imports)
        ):
            self.offending_lines.append(node.lineno)
        self.generic_visit(node)

    def _scope(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        # Defaults/decorators/bases execute in the enclosing scope, unlike the body.
        for field, value in ast.iter_fields(node):
            if field == "body":
                continue
            if isinstance(value, ast.AST):
                self.visit(value)
            elif isinstance(value, list):
                for expression in value:
                    if isinstance(expression, ast.AST):
                        self.visit(expression)
        # Import aliases are visible in nested scopes; path assignments are deliberately not.
        child = _LoginCopies(self.imports)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            arguments += [arg for arg in (node.args.vararg, node.args.kwarg) if arg is not None]
            for arg in arguments:
                child.imports.pop(arg.arg, None)
        for statement in node.body:
            child.visit(statement)
        self.offending_lines.extend(child.offending_lines)
        self.imports.pop(node.name, None)
        self.assigned_logins.pop(node.name, None)

    visit_FunctionDef = _scope
    visit_AsyncFunctionDef = _scope
    visit_ClassDef = _scope

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.visit(node.args)  # Defaults execute before the lambda body has its own scope.
        child = _LoginCopies(self.imports)
        arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        arguments += [arg for arg in (node.args.vararg, node.args.kwarg) if arg is not None]
        for arg in arguments:
            child.imports.pop(arg.arg, None)
        child.visit(node.body)
        self.offending_lines.extend(child.offending_lines)

    def _control_flow(self, node: ast.AST) -> None:
        # Inspect branch calls, but do not pretend an assignment reaches another branch
        # or the statements after it. This is a tripwire, not a control-flow analysis.
        imports, assigned = self.imports.copy(), self.assigned_logins.copy()
        changed_names = {
            child.id
            for child in ast.walk(node)
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
        }
        for _field, value in ast.iter_fields(node):
            if isinstance(value, list):
                child_check = _LoginCopies(imports)
                child_check.assigned_logins = assigned.copy()
                for child in value:
                    if isinstance(child, ast.AST):
                        child_check.visit(child)
                self.offending_lines.extend(child_check.offending_lines)
            elif isinstance(value, ast.AST):
                self.visit(value)
        self.imports, self.assigned_logins = imports, assigned
        for name in changed_names:
            self.imports.pop(name, None)
            self.assigned_logins.pop(name, None)

    visit_If = _control_flow
    visit_For = _control_flow
    visit_AsyncFor = _control_flow
    visit_While = _control_flow
    visit_Try = _control_flow
    visit_TryStar = _control_flow
    visit_Match = _control_flow


def _credential_copy_failures(source: str, filename: str = "<example>") -> list[str]:
    try:
        module = ast.parse(source, filename=filename)
    except (SyntaxError, ValueError) as error:
        # The old text scan covered unparseable modules for free. AST inspection must
        # explicitly pay for fail-closed behavior; skipping this file would lose it.
        return [f"{filename}: cannot parse: {error}"]
    check = _LoginCopies()
    check.visit(module)
    return [f"{filename}:{line}: login copy or hardlink" for line in check.offending_lines]


def _searched_files() -> list[Path]:
    found: list[Path] = []
    for directory in SEARCHED_DIRECTORIES:
        root = REPOSITORY_ROOT / directory
        if root.exists():
            found.extend(sorted(root.rglob("*.py")))
    return found


def test_no_login_path_is_copied_or_hardlinked() -> None:
    searched = _searched_files()
    assert searched, "the repository scan found no Python at all, so it proves nothing"
    offending: list[str] = []
    for path in searched:
        relative = path.relative_to(REPOSITORY_ROOT)
        if relative not in ALLOWED:
            offending.extend(
                _credential_copy_failures(
                    path.read_text(encoding="utf-8", errors="replace"), str(relative)
                )
            )
    assert not offending, (
        "login copies and hardlinks create a second credential owner; use the canonical "
        "login through a symlink, or write a synthetic login. Parse errors also fail "
        "this tripwire. Review its documented blind spots:\n" + "\n".join(offending)
    )


@pytest.mark.parametrize("function", ["copy", "copy2", "copyfile", "copytree"])
@pytest.mark.parametrize("login", ['"auth.json"', "LOGIN_FILE_NAME"])
@pytest.mark.parametrize("arguments", ["home / {login}, scratch", "src=config, dst=home / {login}"])
def test_rejects_each_copy_function_and_login_spelling(
    function: str,
    login: str,
    arguments: str,
) -> None:
    source = f"import shutil\nshutil.{function}({arguments.format(login=login)})"
    assert _credential_copy_failures(source)


@pytest.mark.parametrize(
    "source",
    [
        'import shutil as files\nfiles.copy2(home / "auth.json", scratch)',
        "from shutil import copyfile as duplicate\n"
        "duplicate(src=config, dst=home / LOGIN_FILE_NAME)",
        'import shutil\np = home / "auth.json"\nq = p\np = config\nshutil.copy2(q, scratch)',
        "import shutil\ndef example():\n p: Path = home / LOGIN_FILE_NAME\n q = p\n"
        " shutil.copy(q, scratch)",
        'import os as system\nsystem.link(src=home / "auth.json", dst=scratch)',
        'import shutil\nf = lambda: shutil.copy2(home / "auth.json", scratch)',
        'import shutil\np = home / "auth.json"\ndef example(value=shutil.copy2(p, scratch)): pass',
        "from os import link as duplicate\nduplicate(config, home / LOGIN_FILE_NAME)",
        '(scratch / "auth.json").hardlink_to(canonical)',
        "scratch.hardlink_to(target=canonical / LOGIN_FILE_NAME)",
        'import shutil\n(home / "auth.json").symlink_to(canonical)\n'
        'shutil.copy2(canonical / "auth.json", scratch)',
    ],
)
def test_rejects_resolved_login_copies_and_hardlinks(source: str) -> None:
    assert _credential_copy_failures(source)


@pytest.mark.parametrize(
    "source",
    [
        'import shutil\nshutil.copy2(home / "config.yaml", scratch)\n'
        '(scratch / "auth.json").symlink_to(canonical)',
        "import shutil\n# auth.json is a comment\nshutil.copy(config, scratch)",
        'import shutil\ndef first():\n p = home / "auth.json"\ndef second():\n'
        " shutil.copy2(p, scratch)",
        'import shutil\np = home / "auth.json"\np = config\nshutil.copy2(p, scratch)',
        'import shutil\nshutil.copy2(p, scratch)\np = home / "auth.json"',
        'import shutil\np = home / "auth.json"\nf = lambda p: shutil.copy2(p, scratch)',
        'import shutil\nf = lambda shutil: shutil.copy2("auth.json", scratch)',
        'import shutil\nshutil.move(home / "auth.json", scratch)',
        'import os\nos.replace(home / "auth.json", scratch)',
    ],
)
def test_allows_safe_patterns_and_keeps_scope_and_statement_boundaries(source: str) -> None:
    assert _credential_copy_failures(source) == []


@pytest.mark.parametrize("source", ['import shutil\nshutil.copy2("auth.json",', 'x = "\x00"'])
def test_unparseable_module_fails_closed(source: str) -> None:
    failures = _credential_copy_failures(source, "broken.py")
    assert len(failures) == 1
    assert failures[0].startswith("broken.py: cannot parse:")
