"""One statement of a property every gate depends on: the tools that produce
evidence must belong to the tree under test and match its dependency pins.

A virtualenv copied from another worktree keeps absolute paths to the tree it was
built in. Those paths are silent — the working directory still looks right, and a
gate can report a clean result for somebody else's code. Three separate paths
survive a copy, and any one of them is enough:

* ``pyvenv.cfg`` records the directory the virtualenv was created at.
* Each console script under ``.venv/bin`` carries an absolute interpreter shebang.
* ``site-packages`` holds an editable path file naming the original ``src``.

``faults`` states the property once. ``tests/conftest.py`` and mypy (through the
``plugin`` entry point at the end of this file) each collect the answers their own
process can see and ask the same question.

Module startup imports only the standard library. The dependency check lazily loads
``packaging`` and diagnoses its absence before it imports ``planner``. It compares
installed distribution metadata with applicable pins, not frozen-environment equality.
Extra distributions and legitimate editable installs remain valid. The hooks cannot
protect failures before pytest or mypy loads them. This module never imports
``planner`` at module level: it must stay usable when the environment is wrong.
"""

from __future__ import annotations

import importlib.metadata
import os
import re
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path

PLANNER_PROBE = "import planner; print(planner.__file__)"


@dataclass(frozen=True)
class EnvironmentAnswers:
    """What one process can see about where its tools came from.

    Every field is optional because each caller sees a different subset. ``None``
    means "not asked", except ``planner_file``, where ``planner_absent`` separates
    "not asked" from "asked, and it is not installed".
    """

    venv_prefix: Path | None = None
    venv_origin: Path | None = None
    editable_root: Path | None = None
    planner_file: Path | None = None
    planner_absent: bool = False
    launcher_interpreters: dict[str, Path | None] = field(default_factory=dict)


def faults(tree: Path, answers: EnvironmentAnswers) -> list[str]:
    """Return one human-readable line per way the environment does not match the tree.

    An empty list means every answer the caller collected points back into ``tree``.
    """
    tree = tree.resolve()
    venv = tree / ".venv"
    found: list[str] = []

    if answers.venv_prefix is not None and answers.venv_prefix != venv:
        found.append(
            f"the running interpreter belongs to {answers.venv_prefix}, not {venv}"
        )
    if answers.venv_origin is not None and answers.venv_origin != venv:
        found.append(
            f"{venv}/pyvenv.cfg records creation at {answers.venv_origin}, "
            "so this virtualenv was copied or moved and must be rebuilt"
        )
    if answers.editable_root is not None and answers.editable_root != tree / "src":
        found.append(
            f"the editable install in {venv} points at {answers.editable_root}, "
            f"not {tree}/src"
        )
    if answers.planner_absent:
        found.append(f"planner is not installed in {venv}")
    elif answers.planner_file is not None and not _inside(answers.planner_file, tree / "src"):
        if _inside(answers.planner_file, venv):
            # It belongs to this tree's virtualenv, but as a copy of the source taken at
            # install time. Tests would pass on that copy, not on the working tree.
            found.append(
                f"planner is installed into {venv} rather than linked to {tree}/src: "
                "reinstall it with pip install --editable ."
            )
        else:
            found.append(f"planner resolves to {answers.planner_file}, outside {tree}/src")
    for name, interpreter in sorted(answers.launcher_interpreters.items()):
        if interpreter is not None and not _inside(interpreter, venv):
            found.append(f"{name} runs on {interpreter}, outside {venv}")
    return found


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def interpreter_from_launcher(text: str) -> Path | None:
    """Resolve the interpreter a console script launches on.

    Three forms appear in ``.venv/bin``:

    * ``#!/path/to/python`` — the ordinary shebang.
    * ``#!/usr/bin/env python`` — names the finder, not the interpreter, so there is
      no answer here.
    * A two-line ``/bin/sh`` wrapper. pip writes this instead of a shebang whenever
      the interpreter path is over 127 bytes or holds a space, which a long worktree
      name reaches on its own. The real path is the second line's ``exec`` argument.

    Returns ``None`` for a native binary, a relative interpreter, or anything else
    that records no path: those carry nothing to redirect.
    """
    lines = text.splitlines()
    if not lines or not lines[0].startswith("#!"):
        return None
    words = _split(lines[0][2:])
    if words and Path(words[0]).name in {"sh", "bash"} and len(lines) > 1:
        # The line reads: <quoting noise>exec' /path/to/python "$0" "$@"
        words = _split(lines[1])
        words = words[1:] if words and words[0].endswith("exec") else words
    if not words:
        return None
    candidate = words[1] if Path(words[0]).name == "env" and len(words) > 1 else words[0]
    if not candidate.startswith("/"):
        return None
    # The directory is resolved but the interpreter name is not. ``.venv/bin/python``
    # is a symlink chain ending at the system interpreter: follow it and every healthy
    # launcher looks foreign. Leave the directory unresolved and a tree reached through
    # a symlink looks foreign instead. Resolving only the parent answers both.
    named = Path(os.path.normpath(candidate))
    try:
        return named.parent.resolve() / named.name
    except OSError:
        return named


def _split(line: str) -> list[str]:
    try:
        return shlex.split(line.strip())
    except ValueError:
        return []


def venv_origin_from_pyvenv_cfg(text: str) -> Path | None:
    """Resolve the directory a virtualenv was created at, when the file records it.

    ``python -m venv`` writes a ``command`` line holding the target path. ``uv``
    writes no such line, so the answer is ``None`` and this check stays silent
    rather than guessing.
    """
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == "command":
            words = shlex.split(value.strip())
            # CPython records its target without shell quoting, even with spaces.
            # The absolute target follows the interpreter and venv options.
            targets = [index for index, word in enumerate(words[1:], 1) if word.startswith("/")]
            if targets:
                return Path(" ".join(words[targets[0]:])).resolve()
    return None


def read_launcher_interpreter(launcher: Path) -> Path | None:
    """Read a console script's opening lines, tolerating a native binary or a missing file."""
    try:
        with launcher.open("rb") as handle:
            head = b"\n".join(handle.read(4096).split(b"\n")[:2])
    except OSError:
        return None
    try:
        return interpreter_from_launcher(head.decode("utf-8"))
    except UnicodeDecodeError:
        return None


def read_editable_root(venv: Path) -> Path | None:
    """Resolve the source directory a virtualenv's editable install points at.

    This reads a file. It never imports ``planner``, which is what makes it usable
    inside mypy, where the import would cost startup time for a question mypy does
    not otherwise ask.
    """
    for site_packages in sorted(venv.glob("lib/python*/site-packages")):
        for path_file in sorted(site_packages.glob("__editable__.planner-*.pth")):
            try:
                first = path_file.read_text(encoding="utf-8").splitlines()[:1]
            except (OSError, UnicodeDecodeError):
                continue
            if first and first[0].startswith("/"):
                return Path(os.path.normpath(first[0]))
    return None


def read_venv_origin(venv: Path) -> Path | None:
    try:
        text = (venv / "pyvenv.cfg").read_text(encoding="utf-8")
    except OSError:
        return None
    return venv_origin_from_pyvenv_cfg(text)


def running_answers(tree: Path, *, ask_planner: bool) -> EnvironmentAnswers:
    """Collect what the current process can see, without starting a subprocess.

    ``tests/conftest.py`` runs inside the pytest that is under suspicion and asks
    for ``planner``, because that import is what its tests are about to make. The
    mypy plugin does not: mypy reads source by path, so importing the package
    would cost startup time and answer a question mypy does not ask.
    """
    venv = tree.resolve() / ".venv"
    planner_file: Path | None = None
    planner_absent = False
    if ask_planner:
        try:
            import planner
        except ImportError as exc:
            # Only planner's own absence is this module's business. A missing transitive
            # dependency, or a SyntaxError in the tree being edited, is a different
            # problem and keeps its own error rather than being reported as the wrong one.
            if exc.name != "planner":
                raise
            planner_absent = True
        else:
            planner_file = Path(planner.__file__).resolve() if planner.__file__ else None
    return EnvironmentAnswers(
        venv_prefix=Path(sys.prefix).resolve(),
        venv_origin=read_venv_origin(venv),
        editable_root=read_editable_root(venv),
        planner_file=planner_file,
        planner_absent=planner_absent,
    )


def dependency_faults(tree: Path) -> list[str]:
    """Compare this interpreter's distribution metadata with the tree's pinned requirements.

    The requirements file supports blank lines, comments, and PEP 508 exact pins
    (including extras and environment markers). Other pip syntax is an explicit
    error: silently skipping a line would turn missing evidence into a clean gate.
    """
    requirements = tree / "requirements.txt"
    try:
        lines = requirements.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return [f"cannot read {requirements}: {exc}"]
    try:
        from packaging.requirements import InvalidRequirement, Requirement
        from packaging.utils import canonicalize_name
    except Exception as exc:
        # This parser is itself pinned. Report its state without needing that parser.
        required = next(
            (line.strip() for line in lines if re.match(r"(?i)^packaging\s*[=<>!~]", line.strip())),
            "packaging (required to parse requirements.txt)",
        )
        installed = installed_version("packaging")
        return [
            f"packaging bootstrap failure: required {required}; installed "
            f"{installed or 'absent'}; cannot load requirement parser: {exc}"
        ]

    found: list[str] = []
    for number, raw in enumerate(lines, 1):
        line = re.split(r"\s+#", raw, maxsplit=1)[0].strip()
        if not line or line.startswith("#"):
            continue
        location = f"{requirements}:{number}"
        try:
            requirement = Requirement(line)
        except InvalidRequirement as exc:
            found.append(f"{location}: invalid requirement {line!r}: {exc}")
            continue
        pins = list(requirement.specifier)
        if (
            requirement.url is not None
            or len(pins) != 1
            or pins[0].operator != "=="
            or "*" in pins[0].version
        ):
            found.append(f"{location}: unsupported requirement {line!r}; expected an exact == pin")
            continue
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        name = canonicalize_name(requirement.name)
        installed = installed_version(name)
        try:
            matches = installed is not None and requirement.specifier.contains(
                installed, prereleases=True
            )
        except ValueError:
            matches = False
        if not matches:
            found.append(
                f"{name}: required {requirement.specifier}; installed {installed or 'absent'}"
            )
    return found


def installed_version(name: str) -> str | None:
    """Read the active interpreter's metadata, never import the dependency itself."""
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def preflight(tree: Path, *, ask_planner: bool) -> str | None:
    """Refuse foreign tools first, stale dependencies second, then inspect planner.

    Import failures in planner after a clean preflight keep their original traceback.
    Both hooks return the same diagnosis and commands, even from another directory.
    """
    tree = tree.resolve()
    found = faults(tree, running_answers(tree, ask_planner=False))
    if found:
        return _diagnosis(tree, found, dependencies=False)
    found = dependency_faults(tree)
    if found:
        return _diagnosis(tree, found, dependencies=True)
    if ask_planner:
        found = faults(tree, running_answers(tree, ask_planner=True))
        if found:
            return _diagnosis(tree, found, dependencies=False)
    return None


def _diagnosis(tree: Path, found: list[str], *, dependencies: bool) -> str:
    interpreter = shlex.quote(sys.executable)
    command = f"cd {shlex.quote(str(tree))} && "
    if dependencies:
        heading = f"dependency drift from {tree / 'requirements.txt'}"
        command += f"{interpreter} -m pip install -r requirements.txt"
        repair = "Repair dependencies"
    else:
        heading = f"this environment does not belong to {tree}"
        command += (
            "rm -rf .venv && python3 -m venv .venv"
            " && .venv/bin/python -m pip install -r requirements.txt"
            " && .venv/bin/python -m pip install --editable ."
        )
        repair = "Rebuild the virtualenv in place"
    return (
        f"{heading}:\n  " + "\n  ".join(found)
        + (
            "\nNo bypass is supported. After a pin change, refusal is expected "
            "until installed dependencies match the pins."
            if dependencies else ""
        )
        + f"\nActive interpreter: {sys.executable}\n{repair}: {command}"
    )


def plugin(version: str) -> type:
    """mypy plugin entry point, so that a bare ``mypy`` refuses too.

    ``pyproject.toml`` names this file, and mypy resolves that path against the
    config file, so the tree's own config always loads the tree's own copy of this
    module. A fault here stops mypy with the reason.

    Three answers, not two. ``venv_prefix`` and ``venv_origin`` both go quiet for a
    relocatable virtualenv built by ``uv``: its launchers resolve the interpreter
    beside themselves, and it writes no creation path. The editable path file still
    names the tree it was installed from, and reading it costs no import.
    """
    from mypy.plugin import Plugin

    tree = Path(__file__).resolve().parent.parent
    diagnosis = preflight(tree, ask_planner=False)
    if diagnosis:
        # mypy buries ordinary plugin exceptions in a traceback. This refusal means
        # "could not check", for which mypy uses exit code 2.
        print(f"mypy: {diagnosis}", file=sys.stderr)
        raise SystemExit(2)

    class TreeEnvironmentPlugin(Plugin):
        """No analysis. The check above is the whole purpose."""

    return TreeEnvironmentPlugin
