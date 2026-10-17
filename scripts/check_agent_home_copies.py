"""Bounded source tripwire for copies into selected agent homes, never a safety proof."""

from __future__ import annotations

import argparse
import ast
import json
from dataclasses import asdict, dataclass
from pathlib import Path

SEARCHED_DIRECTORIES = ("src", "tests", "scripts", "ops")
HOME_VARIABLES = {"PLAN_HERMES_HOME", "HERMES_HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME"}
COPY_FUNCTIONS = {"shutil.copy", "shutil.copy2", "shutil.copyfile", "shutil.copytree", "os.link"}
CONFIG_FILES = {"config.yaml", ".env"}
PathParts = tuple[str, ...]
Branches = tuple[tuple[int, bool], ...]


class _Literal(str):
    """A proven source string literal, preserved through simple aliases and loops."""


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    classification: str
    files: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class _Operation:
    kind: str
    line: int
    source: PathParts
    destination: PathParts
    branches: Branches


def _compatible(left: Branches, right: Branches) -> bool:
    choices = dict(left)
    return all(key not in choices or choices[key] == value for key, value in right)


class _Function:
    def __init__(self, imports: dict[str, str]) -> None:
        self.imports = dict(imports)
        self.operations: list[_Operation] = []
        self.homes: list[tuple[PathParts, Branches]] = []

    def _path(self, node: ast.expr, values: dict[str, PathParts]) -> PathParts:
        if isinstance(node, ast.Name):
            return values.get(node.id, (node.id,))
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return (_Literal(node.value),)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return self._path(node.left, values) + self._path(node.right, values)
        if (
            isinstance(node, ast.Call)
            and self._name(node.func) in {"str", "Path", "pathlib.Path"}
            and len(node.args) == 1
        ):
            return self._path(node.args[0], values)
        return (ast.unparse(node),)

    def _name(self, node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return self.imports.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return self._name(node.value) + "." + node.attr
        return ""

    def walk(
        self, body: list[ast.stmt], values: dict[str, PathParts], branches: Branches = ()
    ) -> None:
        for statement in body:
            if isinstance(statement, ast.Import):
                for alias in statement.names:
                    self.imports[alias.asname or alias.name.split(".")[0]] = (
                        alias.name if alias.asname else alias.name.split(".")[0]
                    )
            if isinstance(statement, ast.ImportFrom) and statement.module:
                for alias in statement.names:
                    self.imports[alias.asname or alias.name] = statement.module + "." + alias.name
            if isinstance(statement, (ast.Return, ast.Raise)):
                return
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(statement, ast.If):
                test = statement.test
                if (
                    isinstance(test, ast.Compare)
                    and len(test.ops) == 1
                    and isinstance(test.ops[0], (ast.Eq, ast.NotEq))
                    and isinstance(test.left, ast.Name)
                    and test.left.id in values
                    and len(values[test.left.id]) == 1
                    and isinstance(values[test.left.id][0], _Literal)
                    and len(test.comparators) == 1
                    and isinstance(test.comparators[0], ast.Constant)
                    and isinstance(test.comparators[0].value, str)
                ):
                    matches = values[test.left.id] == (test.comparators[0].value,)
                    take_body = matches if isinstance(test.ops[0], ast.Eq) else not matches
                    self.walk(
                        statement.body if take_body else statement.orelse, dict(values), branches
                    )
                    continue
                self.walk(statement.body, dict(values), branches + ((statement.lineno, True),))
                self.walk(statement.orelse, dict(values), branches + ((statement.lineno, False),))
                if statement.body and isinstance(statement.body[-1], (ast.Return, ast.Raise)):
                    branches += ((statement.lineno, False),)
                if statement.orelse and isinstance(statement.orelse[-1], (ast.Return, ast.Raise)):
                    branches += ((statement.lineno, True),)
                continue
            if isinstance(statement, (ast.For, ast.AsyncFor)):
                if isinstance(statement.target, ast.Name) and isinstance(
                    statement.iter, (ast.Tuple, ast.List)
                ):
                    for item in statement.iter.elts:
                        nested = {**values, statement.target.id: self._path(item, values)}
                        self.walk(statement.body, nested, branches)
                else:
                    self.walk(statement.body, dict(values), branches)
                self.walk(statement.orelse, dict(values), branches)
                continue
            if isinstance(statement, (ast.With, ast.AsyncWith)):
                self.walk(statement.body, dict(values), branches)
                continue
            if isinstance(statement, ast.Try):
                self.walk(statement.body, dict(values), branches)
                for handler in statement.handlers:
                    self.walk(handler.body, dict(values), branches)
                self.walk(statement.orelse, dict(values), branches)
                self.walk(statement.finalbody, dict(values), branches)
                continue
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                value = statement.value
                targets = (
                    statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                )
                if value is not None:
                    resolved = self._path(value, values)
                    for target in targets:
                        if isinstance(target, ast.Name):
                            values[target.id] = resolved
                        if (
                            isinstance(target, ast.Subscript)
                            and isinstance(target.slice, ast.Constant)
                            and target.slice.value in HOME_VARIABLES
                        ):
                            self.homes.append((resolved, branches))
            for node in ast.walk(statement):
                if not isinstance(node, ast.Call):
                    continue
                name = self._name(node.func)
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "setenv"
                    and len(node.args) >= 2
                ):
                    if (
                        isinstance(node.args[0], ast.Constant)
                        and node.args[0].value in HOME_VARIABLES
                    ):
                        self.homes.append((self._path(node.args[1], values), branches))
                arguments = {kw.arg: kw.value for kw in node.keywords}
                source = node.args[0] if node.args else arguments.get("src")
                destination = node.args[1] if len(node.args) > 1 else arguments.get("dst")
                if name in COPY_FUNCTIONS and source is not None and destination is not None:
                    self.operations.append(
                        _Operation(
                            "copy",
                            node.lineno,
                            self._path(source, values),
                            self._path(destination, values),
                            branches,
                        )
                    )
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"symlink_to", "hardlink_to"}
                    and node.args
                ):
                    self.operations.append(
                        _Operation(
                            "link" if node.func.attr == "symlink_to" else "copy",
                            node.lineno,
                            self._path(node.args[0], values),
                            self._path(node.func.value, values),
                            branches,
                        )
                    )

    def findings(self, path: str) -> list[Finding]:
        grouped: dict[int, list[tuple[_Operation, bool]]] = {}
        for operation in self.operations:
            if operation.kind != "copy":
                continue
            for home, branches in self.homes:
                if not _compatible(operation.branches, branches):
                    continue
                if operation.destination[: len(home)] != home:
                    continue
                relative = operation.destination[len(home) :]
                # One explicit configuration filename, not a wildcard exemption.
                # This says nothing about arbitrary bytes hidden inside that file.
                if relative == ("settings.json",) and operation.source[-1] == "settings.json":
                    continue
                safe_config = (
                    len(relative) == 1
                    and relative[0] in CONFIG_FILES
                    and operation.source[-1] == relative[0]
                    and any(
                        link.kind == "link"
                        and link.destination == home + ("auth.json",)
                        and link.source == operation.source[:-1] + ("auth.json",)
                        and _compatible(link.branches, operation.branches)
                        and _compatible(link.branches, branches)
                        for link in self.operations
                    )
                )
                grouped.setdefault(operation.line, []).append((operation, safe_config))
                break
        findings = []
        for line, copies in sorted(grouped.items()):
            dismissed = all(safe for _, safe in copies)
            findings.append(
                Finding(
                    path,
                    line,
                    "dismissal" if dismissed else "accusation",
                    tuple(sorted({op.source[-1] for op, _ in copies})),
                    (
                        "Configuration isolation with a canonical auth symlink; "
                        "external .env contents remain unverified."
                    )
                    if dismissed
                    else (
                        "Copy reaches a selected agent home; "
                        "inspect source credentials and ownership."
                    ),
                )
            )
        return findings


def inspect_source(source: str, path: str = "<source>") -> list[Finding]:
    tree = ast.parse(source, filename=path)
    imports: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imports[alias.asname or alias.name] = node.module + "." + alias.name
    results = []
    # Separate lexical scopes; no module-wide copy/home co-occurrence inference.
    for scope in [
        tree,
        *(
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ),
    ]:
        check = _Function(imports)
        check.walk(scope.body, {})
        results.extend(check.findings(path))
    return sorted(results, key=lambda finding: (finding.path, finding.line))


def scan(root: Path) -> list[Finding]:
    root = root.resolve()
    missing = [name for name in SEARCHED_DIRECTORIES if not (root / name).is_dir()]
    if missing:
        raise FileNotFoundError(
            f"Cannot scan root {root}: missing searched directories: {', '.join(missing)}"
        )
    findings = []
    for directory in SEARCHED_DIRECTORIES:
        for path in sorted((root / directory).rglob("*.py")):
            findings.extend(
                inspect_source(path.read_text(encoding="utf-8"), path.relative_to(root).as_posix())
            )
    return sorted(findings, key=lambda finding: (finding.path, finding.line))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args()
    try:
        findings = scan(arguments.root)
    except FileNotFoundError as error:
        parser.error(str(error))
    counts = {
        kind: sum(item.classification == kind for item in findings)
        for kind in ("accusation", "dismissal")
    }
    if arguments.json:
        print(
            json.dumps(
                {"counts": counts, "findings": [asdict(item) for item in findings]},
                sort_keys=True,
                indent=2,
            )
        )
    else:
        for item in findings:
            print(
                f"{item.path}:{item.line}: {item.classification}: "
                f"{', '.join(item.files)} — {item.reason}"
            )
        print(
            f"{counts['accusation']} accusations, {counts['dismissal']} dismissals. "
            "A clean result does not certify safety."
        )
    return 1 if counts["accusation"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
