"""One statement of a property every gate depends on: the tools that produce
evidence must belong to the tree under test.

A virtualenv copied from another worktree keeps absolute paths to the tree it was
built in. Those paths are silent — the working directory still looks right, and a
gate can report a clean result for somebody else's code. Three separate paths
survive a copy, and any one of them is enough:

* ``pyvenv.cfg`` records the directory the virtualenv was created at.
* Each console script under ``.venv/bin`` carries an absolute interpreter shebang.
* ``site-packages`` holds an editable path file naming the original ``src``.

``faults`` states the property once. ``scripts/verify.py``, ``tests/conftest.py``,
and mypy (through the ``plugin`` entry point at the end of this file) each collect
the answers their own process can see and ask the same question.

This module imports nothing outside the standard library, and never imports
``planner`` at module level: it must stay usable when the environment is wrong.
"""

from __future__ import annotations

import os
import shlex
import subprocess
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
    if answers.planner_absent:
        found.append(f"planner is not installed in {venv}")
    elif answers.planner_file is not None and not _inside(answers.planner_file, tree / "src"):
        found.append(f"planner resolves to {answers.planner_file}, outside {tree}/src")
    for name, interpreter in sorted(answers.launcher_interpreters.items()):
        if interpreter is not None and not _inside(interpreter, venv):
            found.append(f"{name} runs on {interpreter}, outside {venv}")
    return found


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def interpreter_from_shebang(first_line: str) -> Path | None:
    """Resolve the interpreter a console script launches on.

    Returns ``None`` for a native binary or any line that is not a shebang: those
    carry no interpreter to redirect.
    """
    if not first_line.startswith("#!"):
        return None
    try:
        words = shlex.split(first_line[2:].strip())
    except ValueError:
        return None
    if not words:
        return None
    # ``#!/usr/bin/env python`` names the finder, not the interpreter.
    candidate = words[1] if Path(words[0]).name == "env" and len(words) > 1 else words[0]
    if not candidate.startswith("/"):
        return None
    # Normalized, never resolved: ``.venv/bin/python`` is a symlink chain ending at
    # the system interpreter, and resolving it would throw away the only thing this
    # line records — which virtualenv the launcher runs out of.
    return Path(os.path.normpath(candidate))


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
            if words and words[-1].startswith("/"):
                return Path(words[-1]).resolve()
    return None


def read_launcher_interpreter(launcher: Path) -> Path | None:
    """Read a console script's shebang, tolerating a native binary or a missing file."""
    try:
        with launcher.open("rb") as handle:
            head = handle.read(4096).split(b"\n", 1)[0]
    except OSError:
        return None
    try:
        return interpreter_from_shebang(head.decode("utf-8"))
    except UnicodeDecodeError:
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
        except ImportError:
            planner_absent = True
        else:
            planner_file = Path(planner.__file__).resolve() if planner.__file__ else None
    return EnvironmentAnswers(
        venv_prefix=Path(sys.prefix).resolve(),
        venv_origin=read_venv_origin(venv),
        planner_file=planner_file,
        planner_absent=planner_absent,
    )


def probed_answers(tree: Path, launchers: tuple[str, ...]) -> EnvironmentAnswers:
    """Collect answers about a tree's virtualenv from outside it.

    ``scripts/verify.py`` runs before its gates, so it asks the virtualenv's own
    python where ``planner`` is, and reads the shebang of every launcher it is
    about to invoke.
    """
    venv = tree.resolve() / ".venv"
    planner_file: Path | None = None
    planner_absent = False
    completed = subprocess.run(
        [str(venv / "bin" / "python"), "-c", PLANNER_PROBE],
        capture_output=True,
        text=True,
        check=False,
    )
    output = completed.stdout.strip()
    if completed.returncode != 0 or not output:
        planner_absent = True
    else:
        planner_file = Path(output).resolve()
    return EnvironmentAnswers(
        venv_prefix=None,
        venv_origin=read_venv_origin(venv),
        planner_file=planner_file,
        planner_absent=planner_absent,
        launcher_interpreters={
            name: read_launcher_interpreter(venv / "bin" / name) for name in launchers
        },
    )


def plugin(version: str) -> type:
    """mypy plugin entry point, so that a bare ``mypy`` refuses too.

    ``pyproject.toml`` names this file, and mypy resolves that path against the
    config file, so the tree's own config always loads the tree's own copy of this
    module. A fault here raises, and mypy stops with the reason.
    """
    from mypy.plugin import Plugin

    tree = Path(__file__).resolve().parent.parent
    found = faults(tree, running_answers(tree, ask_planner=False))
    if found:
        # mypy turns an exception from here into a traceback that buries the reason,
        # so print the reason plainly and stop. Exit code 2 is mypy's own code for
        # "could not check", which is what happened.
        print(f"mypy: this environment does not belong to {tree}:", file=sys.stderr)
        for reason in found:
            print(f"  {reason}", file=sys.stderr)
        print(
            "  rebuild the virtualenv in place: rm -rf .venv && python3 -m venv .venv"
            " && .venv/bin/python -m pip install -r requirements.txt"
            " && .venv/bin/python -m pip install --editable .",
            file=sys.stderr,
        )
        raise SystemExit(2)

    class TreeEnvironmentPlugin(Plugin):
        """No analysis. The check above is the whole purpose."""

    return TreeEnvironmentPlugin
