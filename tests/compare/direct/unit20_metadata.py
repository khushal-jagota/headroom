"""Export the current Click declarations as data, without invoking any command."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def declarations() -> list[dict[str, Any]]:
    import click
    from click._utils import UNSET

    from planner.cli.main import main

    result = []

    def visit(
        command: click.Command, path: list[str], parent: click.Context | None = None
    ) -> None:
        ctx = click.Context(
            command, info_name=path[-1] if path else "panels", parent=parent
        )
        parameters = []
        for parameter in command.params:
            kind = parameter.type
            typ = "string"
            if isinstance(kind, click.Choice):
                typ = "choice"
            elif isinstance(kind, click.types.IntParamType):
                typ = "integer"
            elif isinstance(kind, click.types.FloatParamType):
                typ = "float"
            elif isinstance(kind, click.types.BoolParamType):
                typ = "boolean"
            elif isinstance(kind, click.Path):
                typ = "path"
            item = {
                "name": parameter.name,
                "type": typ,
                "required": parameter.required,
                "default": None if parameter.default is UNSET else parameter.default,
                "metavar": parameter.make_metavar(ctx),
            }
            if isinstance(parameter, click.Argument):
                item["argument"] = True
                item["nargs"] = parameter.nargs
            else:
                assert isinstance(parameter, click.Option)
                item.update(
                    opts=parameter.opts,
                    secondary_opts=parameter.secondary_opts,
                    multiple=parameter.multiple,
                    flag=parameter.is_flag,
                    help=parameter.help or "",
                    hidden=parameter.hidden,
                    show_default=parameter.show_default or False,
                )
            if isinstance(kind, click.Choice):
                item["choices"] = list(kind.choices)
            if isinstance(kind, (click.IntRange, click.FloatRange)):
                if kind.min is not None:
                    item["min"] = kind.min
                if kind.max is not None:
                    item["max"] = kind.max
            if isinstance(kind, click.Path):
                item.update(
                    exists=kind.exists, file_okay=kind.file_okay, dir_okay=kind.dir_okay
                )
            parameters.append(item)
        result.append(
            {
                "path": path,
                "help": command.help or "",
                "hidden": command.hidden,
                "group": isinstance(command, click.Group),
                "parameters": parameters,
            }
        )
        if isinstance(command, click.Group):
            for name in command.list_commands(ctx):
                visit(command.commands[name], [*path, name], ctx)

    visit(main, [])
    return result


if __name__ == "__main__":
    # This is an import-only exporter, but still refuses ambient worker identity.
    assert not any(
        name.startswith("PLAN_") for name in os.environ
    ), "clean environment required"
    target = Path(__file__).resolve().parents[3] / "server/cli/commands.ts"
    target.write_text(
        "/** Current Python command declarations, exported as data. */\n"
        "import type { CommandDefinition } from './contracts.ts';\n"
        "export const commands: CommandDefinition[] = "
        + json.dumps(declarations(), ensure_ascii=False, indent=2)
        + ";\n"
    )
