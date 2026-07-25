"""Regenerate the codex app-server bindings from the binary Panels actually spawns.

Codex's app-server protocol has no version negotiation and no capability probe, so the
only thing standing between this adapter and a silent protocol change is a pin. The pin
here is stronger than a repository commit: the JSON Schema is asked of the **installed
codex binary** — the same one the adapter launches — with
``codex app-server generate-json-schema``. The upstream tag that binary was cut from is
recorded alongside it so a reader can go and diff the Rust.

Three things come out of a run:

- ``schema/codex_app_server_protocol.subset.schema.json`` — the vendored schema, pruned to
  the definitions this adapter can reach from the messages it actually sends and reads.
  Vendoring the pruned document rather than the whole dump keeps the diff readable: when a
  regeneration changes it, the change is in something we use.
- ``bindings_gen.py`` — pydantic models for those definitions.
- The pin, written into the generated file's header: binary version, upstream commit, how
  the schema was obtained, and a canonical digest of the full dump the subset came from.

Run it as ``python -m planner.conversation2.backends.codex_app_server.generate_bindings``.
It refuses to run against a codex whose version is not the pinned one, because a
regeneration that silently moves the protocol is the failure this file exists to prevent.
Moving the pin is a deliberate edit of the three constants below.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------------------
# The pin. Change these three together, never one of them.
# ---------------------------------------------------------------------------------------

# What ``codex --version`` must print for this generated file to be the one that matches.
PINNED_CODEX_CLI_VERSION = "codex-cli 0.145.0"

# The upstream tag that binary was cut from, and its commit, so the Rust can be read.
PINNED_UPSTREAM_TAG = "rust-v0.145.0"
PINNED_UPSTREAM_COMMIT = "25af12f7e61572b0bc18ddb1008be543b91519b0"

# ---------------------------------------------------------------------------------------

# The one file in the dump that holds everything: the version-agnostic surface at the top
# level, and the whole v2 surface nested under a ``v2`` key.
FULL_SCHEMA_DUMP_FILE_NAME = "codex_app_server_protocol.schemas.json"

_HERE = Path(__file__).parent
VENDORED_SUBSET_SCHEMA_PATH = _HERE / "schema" / "codex_app_server_protocol.subset.schema.json"
GENERATED_BINDINGS_PATH = _HERE / "bindings_gen.py"

# The nested namespace the v2 surface lives in inside the dump, and how its definitions are
# referred to from anywhere in the document.
_V2_NAMESPACE = "v2"
_REFERENCE_PREFIX = "#/definitions/"

# Every message this adapter sends or reads, named in the namespace the dump keeps it in.
# Nothing else is generated: a binding for a message we never speak would be a claim about
# a shape nobody here has checked. ``client.py`` and ``adapter.py`` import from exactly
# this list, so adding a message to the adapter starts here.
SCHEMA_ROOTS: tuple[tuple[str, str], ...] = (
    # The handshake, which is not versioned and lives at the top level.
    ("", "InitializeParams"),
    ("", "InitializeResponse"),
    # The two approval requests the server makes of us, likewise top level.
    ("", "CommandExecutionRequestApprovalParams"),
    ("", "CommandExecutionRequestApprovalResponse"),
    ("", "FileChangeRequestApprovalParams"),
    ("", "FileChangeRequestApprovalResponse"),
    # Everything else is v2: the thread, the turn, and the news about them.
    (_V2_NAMESPACE, "ThreadStartParams"),
    (_V2_NAMESPACE, "ThreadStartResponse"),
    (_V2_NAMESPACE, "ThreadResumeParams"),
    (_V2_NAMESPACE, "ThreadResumeResponse"),
    (_V2_NAMESPACE, "TurnStartParams"),
    (_V2_NAMESPACE, "TurnStartResponse"),
    (_V2_NAMESPACE, "TurnInterruptParams"),
    (_V2_NAMESPACE, "TurnInterruptResponse"),
    (_V2_NAMESPACE, "ThreadStartedNotification"),
    (_V2_NAMESPACE, "TurnStartedNotification"),
    (_V2_NAMESPACE, "TurnCompletedNotification"),
    (_V2_NAMESPACE, "AgentMessageDeltaNotification"),
    (_V2_NAMESPACE, "ItemStartedNotification"),
    (_V2_NAMESPACE, "ItemCompletedNotification"),
    (_V2_NAMESPACE, "ErrorNotification"),
    # The two snapshot probes, which are requests like any other.
    (_V2_NAMESPACE, "GetAccountParams"),
    (_V2_NAMESPACE, "GetAccountResponse"),
    (_V2_NAMESPACE, "ModelListParams"),
    (_V2_NAMESPACE, "ModelListResponse"),
)


class BindingGenerationFailed(Exception):
    """The bindings could not be regenerated, and why."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--codex-executable",
        default="codex",
        help="the codex binary to ask for the schema (default: the one on PATH)",
    )
    arguments = parser.parse_args(argv)
    try:
        generate_bindings(codex_executable=arguments.codex_executable)
    except BindingGenerationFailed as failed:
        print(f"bindings not regenerated: {failed}", file=sys.stderr)
        return 1
    return 0


def generate_bindings(*, codex_executable: str = "codex") -> None:
    """Ask the pinned binary for its schema, prune it, vendor it, and generate models."""
    installed_version = _installed_codex_version(codex_executable)
    if installed_version != PINNED_CODEX_CLI_VERSION:
        raise BindingGenerationFailed(
            f"this file is pinned to {PINNED_CODEX_CLI_VERSION!r} and the binary at "
            f"{codex_executable!r} is {installed_version!r}. Regenerating against a "
            "different binary changes what the adapter believes the protocol is, so move "
            "the pin constants in this file deliberately and then run it again."
        )
    with tempfile.TemporaryDirectory(prefix="codex-app-server-schema-") as scratch:
        dump = _dump_schema(codex_executable, Path(scratch))
        full_dump_digest = _canonical_digest(dump)
        subset = _pruned_subset(dump)
        subset_text = json.dumps(subset, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
        VENDORED_SUBSET_SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
        VENDORED_SUBSET_SCHEMA_PATH.write_text(subset_text, encoding="utf-8")
        # What the generator reads is the vendored subset with codex's prose taken out.
        # Upstream writes paragraphs into ``description``, and a code generator turns each
        # one into a single very long line. The prose is worth keeping where a person
        # reads it — the vendored schema — and worth dropping where a machine writes it.
        codegen_input = Path(scratch) / "subset-without-prose.json"
        codegen_input.write_text(
            json.dumps(_without_prose(subset), indent=1, ensure_ascii=False), encoding="utf-8"
        )
        _run_datamodel_code_generator(
            subset_schema_path=codegen_input,
            output_path=GENERATED_BINDINGS_PATH,
            header=_generated_file_header(
                installed_version=installed_version,
                full_dump_digest=full_dump_digest,
                subset_digest=hashlib.sha256(subset_text.encode("utf-8")).hexdigest(),
                definition_count=len(subset["definitions"]),
            ),
        )
    print(f"wrote {VENDORED_SUBSET_SCHEMA_PATH}")
    print(f"wrote {GENERATED_BINDINGS_PATH}")


def _installed_codex_version(codex_executable: str) -> str:
    if shutil.which(codex_executable) is None and not Path(codex_executable).exists():
        raise BindingGenerationFailed(f"no codex binary at {codex_executable!r}")
    completed = subprocess.run(  # noqa: S603
        [codex_executable, "--version"], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise BindingGenerationFailed(f"{codex_executable} --version failed: {completed.stderr}")
    return completed.stdout.strip()


def _dump_schema(codex_executable: str, into: Path) -> dict[str, Any]:
    completed = subprocess.run(  # noqa: S603
        [codex_executable, "app-server", "generate-json-schema", "--out", str(into)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise BindingGenerationFailed(
            f"codex app-server generate-json-schema failed: {completed.stderr}"
        )
    dumped = into / FULL_SCHEMA_DUMP_FILE_NAME
    if not dumped.exists():
        raise BindingGenerationFailed(f"the schema dump has no {FULL_SCHEMA_DUMP_FILE_NAME}")
    parsed: dict[str, Any] = json.loads(dumped.read_text(encoding="utf-8"))
    return parsed


def _canonical_digest(document: Any) -> str:
    """A digest of what the document says, not of how it happened to be printed."""
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- pruning ------------------------------------------------------------------------------


def _pruned_subset(dump: dict[str, Any]) -> dict[str, Any]:
    """The definitions reachable from the messages we speak, flattened into one namespace.

    The dump keeps the v2 surface nested, so a v2 definition is referred to as
    ``#/definitions/v2/Name`` and a top-level one as ``#/definitions/Name``. Flattening
    both into one map is what makes the subset a document a code generator can read on its
    own. Two definitions of the same name from the two namespaces are only allowed to meet
    if they say the same thing — a shared name that has drifted apart is a genuine
    ambiguity and is refused rather than resolved by a coin toss.
    """
    definitions = dump.get("definitions")
    if not isinstance(definitions, dict):
        raise BindingGenerationFailed("the schema dump has no definitions")
    namespaced = _namespaced_definitions(definitions)
    reachable = _reachable_from_roots(namespaced)

    flattened: dict[str, Any] = {}
    claimed_by: dict[str, str] = {}
    for namespace, name in sorted(reachable):
        rewritten = _rewrite_references(namespaced[(namespace, name)])
        if name in flattened and flattened[name] != rewritten:
            raise BindingGenerationFailed(
                f"{name!r} exists in both the top-level and the {_V2_NAMESPACE!r} namespace "
                f"and the two do not agree, so flattening them would pick one at random "
                f"(first seen in namespace {claimed_by[name]!r})"
            )
        flattened[name] = rewritten
        claimed_by.setdefault(name, namespace or "<top level>")
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "definitions": flattened,
    }


def _without_prose(document: Any) -> Any:
    if isinstance(document, dict):
        return {
            key: _without_prose(value)
            for key, value in document.items()
            if key != "description"
        }
    if isinstance(document, list):
        return [_without_prose(item) for item in document]
    return document


def _namespaced_definitions(definitions: dict[str, Any]) -> dict[tuple[str, str], Any]:
    namespaced: dict[tuple[str, str], Any] = {}
    for name, definition in definitions.items():
        if name == _V2_NAMESPACE:
            for v2_name, v2_definition in definition.items():
                namespaced[(_V2_NAMESPACE, v2_name)] = v2_definition
            continue
        namespaced[("", name)] = definition
    return namespaced


def _reachable_from_roots(namespaced: dict[tuple[str, str], Any]) -> set[tuple[str, str]]:
    reachable: set[tuple[str, str]] = set()
    pending = list(SCHEMA_ROOTS)
    for root in SCHEMA_ROOTS:
        if root not in namespaced:
            raise BindingGenerationFailed(
                f"{root[1]!r} is not in the schema dump's "
                f"{root[0] or 'top-level'} namespace any more"
            )
    while pending:
        key = pending.pop()
        if key in reachable:
            continue
        reachable.add(key)
        for referenced in _references_in(namespaced[key]):
            if referenced not in namespaced:
                raise BindingGenerationFailed(f"{referenced} is referenced but not defined")
            pending.append(referenced)
    return reachable


def _references_in(definition: Any) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    pending = [definition]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            reference = node.get("$ref")
            if isinstance(reference, str) and reference.startswith(_REFERENCE_PREFIX):
                found.append(_reference_target(reference))
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    return found


def _reference_target(reference: str) -> tuple[str, str]:
    path = reference[len(_REFERENCE_PREFIX) :]
    namespace, _, name = path.rpartition("/")
    return (namespace, name)


def _rewrite_references(definition: Any) -> Any:
    """The same definition with every reference pointing into the flattened namespace."""
    if isinstance(definition, dict):
        rewritten: dict[str, Any] = {}
        for key, value in definition.items():
            if key == "$ref" and isinstance(value, str) and value.startswith(_REFERENCE_PREFIX):
                rewritten[key] = _REFERENCE_PREFIX + _reference_target(value)[1]
                continue
            rewritten[key] = _rewrite_references(value)
        return rewritten
    if isinstance(definition, list):
        return [_rewrite_references(item) for item in definition]
    return definition


# --- generating ---------------------------------------------------------------------------


def _generated_file_header(
    *,
    installed_version: str,
    full_dump_digest: str,
    subset_digest: str,
    definition_count: int,
) -> str:
    return "\n".join(
        (
            '"""Codex app-server protocol bindings. Generated — do not edit.',
            "",
            "Regenerate with:",
            "    python -m planner.conversation2.backends.codex_app_server.generate_bindings",
            "",
            "The pin these models were generated under:",
            "",
            f"    codex binary version   {installed_version}",
            f"    upstream openai/codex  {PINNED_UPSTREAM_TAG} = {PINNED_UPSTREAM_COMMIT}",
            "    schema obtained by     codex app-server generate-json-schema --out <dir>",
            f"    from the dump's        {FULL_SCHEMA_DUMP_FILE_NAME}",
            f"    dump digest (sha256)   {full_dump_digest}",
            "    pruned and vendored    schema/codex_app_server_protocol.subset.schema.json",
            f"    subset digest (sha256) {subset_digest}",
            f"    definitions generated  {definition_count}",
            "",
            "The digests are taken over the JSON's meaning — keys sorted — so they change",
            "when the protocol changes and not when the dump is printed differently.",
            '"""',
        )
    )


def _run_datamodel_code_generator(
    *, subset_schema_path: Path, output_path: Path, header: str
) -> None:
    completed = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "datamodel_code_generator",
            "--input",
            str(subset_schema_path),
            "--input-file-type",
            "jsonschema",
            "--output",
            str(output_path),
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--target-python-version",
            "3.12",
            "--use-standard-collections",
            "--use-union-operator",
            "--use-annotated",
            "--field-constraints",
            "--collapse-root-models",
            "--disable-timestamp",
            "--strict-refs",
            # Upstream titles every variant of a union, so a class is called
            # ``CommandExecutionThreadItem`` rather than ``ThreadItem6``.
            "--use-title-as-name",
            # A closed set of strings on the wire reads as a closed set of strings here,
            # so a status is compared to ``"completed"`` rather than to a generated enum
            # member whose name is one more thing to look up.
            "--enum-field-as-literal",
            "all",
            "--custom-file-header",
            header,
            "--formatters",
            "ruff-check",
            "ruff-format",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise BindingGenerationFailed(f"datamodel-code-generator failed: {completed.stderr}")


if __name__ == "__main__":  # pragma: no cover - a developer tool's entry point
    raise SystemExit(main())
