"""Which managed skill rows name something this build does not have.

A skill row is the owner's text, so nothing here rewrites it. This reports, and the
report is what makes a stale row visible instead of silent. Before this existed, a
build could remove a command and leave every skill still teaching it, with nothing
anywhere saying so.

The four kinds of reference are checked against the build itself rather than against
a list of yesterday's names: the CLI command tree, the Stage ids the Worker types
declare, the Stage ladders those Worker types spell out, and the skills that are
retired. A list would need editing every time something moved, and the thing that goes
stale is exactly the list nobody edits.

A Stage is named two ways. Its id, ``needs_something``, is unmistakable anywhere it
appears. Its label is an ordinary phrase, and searching prose for a label reports text
that is correct: "approach" is an English word, and "kickoff" names the live
``--kickoff-note`` option. So labels are read in one place only, the ladder — names
joined by arrows inside a bold span, ending at a terminal Stage. Nothing but a taught
sequence takes that shape.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Final

import click

from planner.skill_sources import RETIRED_PANELS_SKILL_NAMES

# A command a skill teaches is written as a literal a worker types, inside backticks.
# Bare prose that happens to contain the word "panels" is not a command reference.
# A span may wrap across a line, so newlines are allowed and collapse into the word
# split. It is bounded so that one stray backtick cannot swallow half a document.
COMMAND_SPAN: Final = re.compile(r"`panels ([^`]{0,200}?)`", re.S)
# Markdown escapes the underscores in bold, so both spellings occur.
STAGE_REFERENCE: Final = re.compile(r"needs(?:\\_|_)([a-z](?:[a-z]|\\_|_)*)")
# A ladder is the sequence a skill teaches: Stage names joined by arrows, inside a bold
# span, ending at a terminal Stage. The bold and the terminal Stage are what separate a
# taught sequence from any other arrow in the text. A span may wrap across a line.
LADDER_SPAN: Final = re.compile(r"\*\*([^*]*?\u2192[^*]*?)\*\*", re.S)
LADDER_ARROW: Final = "\u2192"


# The probe fixture's specialist skill ships so that the Worker type machinery can be
# proved generic. Its Worker type is registered only in tests, so its Stage ids are
# absent from every real database by design. Checking them reports the same two findings
# on a clean build forever, and a check that always shows noise gets read like silence.
UNCHECKED_STAGE_SKILL_NAMES: Final = frozenset({"probe-worker"})


@dataclass(frozen=True)
class StaleReference:
    """One thing a skill row names that this build does not have."""

    skill_name: str
    kind: str
    reference: str

    def as_dict(self) -> dict[str, str]:
        return {"skill_name": self.skill_name, "kind": self.kind, "reference": self.reference}


def command_words(span: str) -> tuple[str, ...]:
    """The command path in a backticked span, without its options and placeholders."""
    words: list[str] = []
    for word in span.split():
        if word.startswith(("-", "<", "[", "(", ".", "…")) or word == "...":
            break
        words.append(word)
    return tuple(words)


def command_resolves(words: tuple[str, ...], root: click.Command) -> bool:
    """Whether this word sequence names a real command.

    Descend while the node is a Group. At a leaf Command the remaining words are its
    arguments, so stop there. A word a Group does not know is the dead one — resolving
    it to the parent group instead is how a check reports a clean run it did not have.
    """
    node: click.Command = root
    for word in words:
        if not isinstance(node, click.Group):
            return True
        next_node = node.commands.get(word)
        if next_node is None:
            return False
        node = next_node
    return True


def declared_stage_ids(conn: sqlite3.Connection) -> frozenset[str]:
    ids: set[str] = set()
    for row in conn.execute("SELECT definition_json FROM worker_types"):
        definition = json.loads(row[0])
        ids.update(str(stage["id"]) for stage in definition["stages"])
    return frozenset(ids)


def declared_stage_names(conn: sqlite3.Connection) -> frozenset[str]:
    """Every id and every label a Worker type gives one of its Stages."""
    names: set[str] = set()
    for row in conn.execute("SELECT definition_json FROM worker_types"):
        definition = json.loads(row[0])
        for stage in definition["stages"]:
            names.add(str(stage["id"]))
            label = stage.get("label")
            if label:
                names.add(str(label))
    return frozenset(names)


def terminal_stage_names(conn: sqlite3.Connection) -> frozenset[str]:
    """The id and label of the last Stage of every Worker type.

    A ladder ends where the work ends. Reading that from the Worker types keeps the
    check free of a literal to update the next time the last Stage is renamed.
    """
    names: set[str] = set()
    for row in conn.execute("SELECT definition_json FROM worker_types"):
        definition = json.loads(row[0])
        last = definition["stages"][-1]
        names.add(str(last["id"]))
        label = last.get("label")
        if label:
            names.add(str(label))
    return frozenset(names)


def ladder_names(span: str) -> tuple[str, ...]:
    """The Stage names in a bold arrow span, in order.

    A skill sometimes introduces the sequence inside the bold, as "Stages: Brief
    \u2192 ...". That prefix labels the list rather than naming a Stage, so it is
    dropped. Line breaks inside the span collapse, and a closing period is not part of
    the last name.
    """
    names: list[str] = []
    for part in span.split(LADDER_ARROW):
        name = " ".join(part.split()).strip(".")
        if not names and ":" in name:
            name = name.rsplit(":", 1)[1].strip()
        names.append(name)
    return tuple(names)


def stale_references_in(
    skill_name: str,
    source_text: str,
    *,
    command_root: click.Command,
    stage_ids: frozenset[str],
    stage_names: frozenset[str] = frozenset(),
    terminal_names: frozenset[str] = frozenset(),
) -> tuple[StaleReference, ...]:
    found: set[StaleReference] = set()
    for match in COMMAND_SPAN.finditer(source_text):
        words = command_words(match.group(1))
        if words and not command_resolves(words, command_root):
            found.add(
                StaleReference(skill_name, "command", "panels " + " ".join(words)),
            )
    if skill_name not in UNCHECKED_STAGE_SKILL_NAMES:
        for match in STAGE_REFERENCE.finditer(source_text):
            stage_id = "needs_" + match.group(1).replace("\\", "").rstrip("_")
            if stage_id not in stage_ids:
                found.add(StaleReference(skill_name, "stage", stage_id))
        for match in LADDER_SPAN.finditer(source_text):
            names = ladder_names(match.group(1))
            if len(names) < 2 or names[-1] not in terminal_names:
                continue
            for name in names:
                if name not in stage_names:
                    found.add(StaleReference(skill_name, "ladder", name))
    for retired_name in RETIRED_PANELS_SKILL_NAMES:
        if retired_name in source_text:
            found.add(StaleReference(skill_name, "retired_skill", retired_name))
    return tuple(sorted(found, key=lambda item: (item.kind, item.reference)))


def stale_references(
    conn: sqlite3.Connection,
    *,
    command_root: click.Command | None = None,
) -> tuple[StaleReference, ...]:
    """Every reference in every skill row that this build cannot resolve."""
    if command_root is None:
        from planner.cli.main import main as cli_root

        command_root = cli_root
    stage_ids = declared_stage_ids(conn)
    stage_names = declared_stage_names(conn)
    terminal_names = terminal_stage_names(conn)
    found: list[StaleReference] = []
    for row in conn.execute(
        "SELECT skill_name, source_text FROM managed_skills ORDER BY skill_name"
    ):
        found.extend(
            stale_references_in(
                str(row[0]),
                str(row[1]),
                command_root=command_root,
                stage_ids=stage_ids,
                stage_names=stage_names,
                terminal_names=terminal_names,
            )
        )
    return tuple(found)
