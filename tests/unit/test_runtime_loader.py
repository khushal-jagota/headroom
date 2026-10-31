"""Compare packaging decisions with glibc through a real shared-interpreter caller.

These small ELF graphs catch loader semantics that file-only provider tests cannot.
The executable owns one RPATH, its libpython-like dependency calls dlopen, and an
extension needs a uniquely named leaf library. No host cache can supply that leaf.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from planner.environments.runtime_contracts import RuntimeContractError
from planner.environments.runtime_elf import ElfDynamic, ElfObject, read_dynamic, resolve_provider


@pytest.fixture
def compiler() -> str:
    compiler = shutil.which("cc")
    assert compiler is not None, "real ELF loader checks require a C compiler"
    assert shutil.which("readelf") is not None, "real ELF loader checks require readelf"
    return compiler


def _compile(compiler: str, source: str, destination: Path, *flags: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_path = destination.with_suffix(".c")
    source_path.write_text(source)
    subprocess.run(
        [compiler, str(source_path), "-o", str(destination), *flags],
        check=True,
        capture_output=True,
        text=True,
    )


def _loader_paths(value: str, *, runpath: bool = False) -> tuple[str, ...]:
    tag = "-Wl,--enable-new-dtags" if runpath else "-Wl,--disable-new-dtags"
    return tag, f"-Wl,-rpath,{value}"


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("inherited_rpath", 11),
        ("requester_runpath_suppresses_ancestry", None),
        ("ancestor_runpath_is_not_transitive", None),
        ("requester_rpath_precedes_ancestor", 22),
        ("requester_runpath_provider", 22),
    ],
)
def test_resolver_matches_shared_interpreter_dlopen(
    tmp_path: Path, compiler: str, scenario: str, expected: int | None
) -> None:
    # The leaf copies share a SONAME but differ in ABI-visible behavior.
    leaf_name = "libpanels_fixture_leaf.so"
    ancestor_leaf = tmp_path / "ancestor" / leaf_name
    extension_leaf = tmp_path / "extension-private" / leaf_name
    for destination, value in ((ancestor_leaf, 11), (extension_leaf, 22)):
        _compile(
            compiler,
            f"int leaf(void) {{ return {value}; }}",
            destination,
            "-shared",
            "-fPIC",
            f"-Wl,-soname,{leaf_name}",
        )
    extension = tmp_path / "extensions/plugin.so"
    extension_flags: tuple[str, ...] = ()
    if scenario == "requester_runpath_suppresses_ancestry":
        extension_flags = _loader_paths("$ORIGIN/absent", runpath=True)
    elif scenario in ("requester_rpath_precedes_ancestor", "requester_runpath_provider"):
        extension_flags = _loader_paths(
            "$ORIGIN/../extension-private", runpath=scenario == "requester_runpath_provider"
        )
    _compile(
        compiler,
        "extern int leaf(void); int answer(void) { return leaf(); }",
        extension,
        "-shared",
        "-fPIC",
        f"-L{ancestor_leaf.parent}",
        "-lpanels_fixture_leaf",
        *extension_flags,
    )
    caller = tmp_path / "bin/libfixture_interpreter.so"
    _compile(
        compiler,
        '#include <dlfcn.h>\n#include <stdio.h>\n'
        'int load_extension(char *path) { void *h = dlopen(path, RTLD_NOW); '
        'if (!h) { puts(dlerror()); return 3; } '
        'int (*answer)(void) = (int (*)(void))dlsym(h, "answer"); '
        'if (!answer) return 4; printf("%d\\n", answer()); return 0; }',
        caller,
        "-shared",
        "-fPIC",
        "-ldl",
        "-Wl,-soname,libfixture_interpreter.so",
    )
    executable = tmp_path / "bin/python"
    _compile(
        compiler,
        "extern int load_extension(char *); "
        "int main(int argc, char **argv) { return argc == 2 ? load_extension(argv[1]) : 5; }",
        executable,
        f"-L{caller.parent}",
        "-lfixture_interpreter",
        *_loader_paths(
            "$ORIGIN:$ORIGIN/../ancestor", runpath=scenario == "ancestor_runpath_is_not_transitive"
        ),
    )
    # Eliminate ambient loader overrides. The unique SONAME avoids host fallback.
    environment = {key: value for key, value in os.environ.items() if not key.startswith("LD_")}
    result = subprocess.run(
        [str(executable), str(extension)],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    requester = ElfObject(extension, read_dynamic(extension))
    ancestors = tuple(ElfObject(path, read_dynamic(path)) for path in (caller, executable))
    if expected is None:
        assert result.returncode == 3
        assert leaf_name in result.stdout
        with pytest.raises(RuntimeContractError, match="unresolved native dependency"):
            resolve_provider(leaf_name, requester, ancestors, {})
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout == f"{expected}\n"
        expected_path = ancestor_leaf if expected == 11 else extension_leaf
        assert resolve_provider(leaf_name, requester, ancestors, {}) == expected_path.resolve()


def test_required_missing_dependency_refuses_even_with_interpreter_ancestry(tmp_path: Path) -> None:
    # Omission of the specifically unused Tk extension must never exempt other edges.
    executable = ElfObject(tmp_path / "python", ElfDynamic((), ("$ORIGIN/lib",), None, None))
    extension = ElfObject(tmp_path / "required.so", ElfDynamic((), (), None, None))
    with pytest.raises(RuntimeContractError, match="unresolved native dependency"):
        resolve_provider("libpanels_required_missing.so", extension, (executable,), {})
