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
            if words and words[-1].startswith("/"):
                return Path(words[-1]).resolve()
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
        for path_file in sorted(site_packages.glob("__editable__*.pth")):
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
            if exc.name is not None and exc.name.split(".")[0] != "planner":
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


def probed_answers(tree: Path, launchers: tuple[str, ...]) -> EnvironmentAnswers:
    """Collect answers about a tree's virtualenv from outside it.

    ``scripts/verify.py`` runs before its gates, so it asks the virtualenv's own
    python where ``planner`` is, and reads the shebang of every launcher it is
    about to invoke.
    """
    venv = tree.resolve() / ".venv"
    planner_file: Path | None = None
    planner_absent = False
    try:
        completed = subprocess.run(
            [str(venv / "bin" / "python"), "-c", PLANNER_PROBE],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        # A dangling ``.venv/bin/python`` after a system Python upgrade. Every other
        # reader here tolerates a missing file, and so does this one.
        completed = None
    # The last line, not the whole of stdout: a chatty path file or sitecustomize can
    # print before the probe does, and a two-line ``Path`` is nonsense.
    lines = completed.stdout.strip().splitlines() if completed is not None else []
    if completed is None or completed.returncode != 0 or not lines:
        planner_absent = True
    else:
        planner_file = Path(lines[-1]).resolve()
    return EnvironmentAnswers(
        venv_prefix=None,
        venv_origin=read_venv_origin(venv),
        editable_root=read_editable_root(venv),
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
    module. A fault here stops mypy with the reason.

    Three answers, not two. ``venv_prefix`` and ``venv_origin`` both go quiet for a
    relocatable virtualenv built by ``uv``: its launchers resolve the interpreter
    beside themselves, and it writes no creation path. The editable path file still
    names the tree it was installed from, and reading it costs no import.
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
